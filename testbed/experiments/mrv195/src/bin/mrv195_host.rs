//! Single-host-core (`cpu40 @ 3.90 GHz`) real lightweight RPC/KV server on `saturn` for MRV-195.
//!
//! Runs the EXACT SAME in-memory open-addressing KV engine (`32.5 MiB` working set:
//! 16,384 hash buckets + 8,192 x 4 KiB value slab on NUMA-1 HugePages, full GET/PUT
//! protocol parsing, real value read/write copies, and 32-bit value checksum verification)
//! over two host<->NIC interfaces:
//!   - `--mode sdp`:    Fair async optimal `SDP fair-full` (`rx_poll_mem=1,rx_poll_tail_off=48,
//!                      ism_enable=1,allow_small_oq=1`, compact DDIO-fit RX ring/pool,
//!                      in-place zero-copy resize, batched `port.tx()`).
//!   - `--mode direct`: `DirectPush` (`<= 256 B` L1-stack `MOVDIR64B` to 128 B-stride
//!                      exclusive BAR4 sub-rings + `>= 1024 B` native DPI DMA from 128 MiB
//!                      retained `tx_pool` with batched `sfence`).
//!
//! Embeds 100% non-multiplexed core + Socket-1 CHA/IIO/IMC PMU (`HostPmu`) snapshotted
//! cleanly across each `RESET` .. `STATS` measurement window.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv195_host runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, Mempool, Memzone, Port, rdtsc, rdtsc_after_loads};
    use testbed::lease::Lease;
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};
    use mrv195::{
        BAR4_CELL_STRIDE_DEFAULT, CTRL_BAR4_STRIDE, CTRL_CARD_ACK_GEN, CTRL_CARD_CORES,
        CTRL_CREDIT_HYST, CTRL_HOST_READY_GEN, CTRL_RX_ZONE_IOVA, CellPoll, KV_GET, KV_NUM_KEYS,
        KV_PUT, KV_SLOT_BYTES, KV_STATUS_ERR, KV_STATUS_MISS, KV_STATUS_OK, KV_TABLE_BUCKETS,
        KV_TABLE_MASK, MAX_CARD_CORES, MAX_DRAIN, MAX_SEG_BYTES, OP_DMA, OP_NET, OP_PUSH,
        RX_ZONE_BYTES, RpcCell, SDP_HDR_LEN, SDP_MBUF_BUF_SIZE, SDP_MTU, SDP_UDP_DST_PORT,
        TX_POOL_BYTES, TX_POOL_SLOT_BYTES, TX_POOL_SLOTS, bar4_cell_offset, cksum_finish,
        cksum_init, cksum_step, credit_line_offset, frag_cnt_for_bytes, kv_value_word,
        large_rx_offset, mix_key_cells, pack_kv_meta, pack_meta, rx_ring_offset, splitmix64,
        unpack_kv_meta, unpack_meta, val_words_for_cells,
    };
    use testbed::swap_macs;

    const CTRL_CPU: usize = 44;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Mode {
        Sdp,
        Direct,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum KvProfile {
        #[value(name = "64")]
        B64,
        #[value(name = "256")]
        B256,
        #[value(name = "1024")]
        B1024,
        #[value(name = "4096")]
        B4096,
        #[value(name = "mix")]
        Mix,
    }

    impl KvProfile {
        fn from_str_opt(s: &str) -> Option<Self> {
            match s {
                "64" | "get64" | "put64" => Some(KvProfile::B64),
                "256" | "get256" | "put256" => Some(KvProfile::B256),
                "1024" | "get1024" | "put1024" => Some(KvProfile::B1024),
                "4096" | "get4096" | "put4096" => Some(KvProfile::B4096),
                "mix" => Some(KvProfile::Mix),
                _ => None,
            }
        }

        fn cells_for_key(self, key: usize) -> u8 {
            match self {
                KvProfile::B64 => 1,
                KvProfile::B256 => 4,
                KvProfile::B1024 => 16,
                KvProfile::B4096 => 64,
                KvProfile::Mix => mix_key_cells(key),
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = Mode::Direct)]
        mode: Mode,
        #[arg(long, value_enum, default_value_t = KvProfile::Mix)]
        kv_profile: KvProfile,
        #[arg(long, default_value = testbed::lease::DEFAULT_ADDR)]
        card: String,
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar4: String,
        #[arg(long, default_value_t = 4)]
        sdp_queues: u16,
        /// SDP RX descriptor ring size per queue (default 256 with `allow_small_oq=1` -> 1024 total DDIO-fit).
        #[arg(long, default_value_t = 256)]
        sdp_rx_desc: u16,
        #[arg(long, default_value_t = 2048)]
        sdp_tx_desc: u16,
        /// Use compact dedicated RX pool sized to `sdp_queues * sdp_rx_desc + 1023` (fair-full DDIO fit).
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        sdp_compact_pool: bool,
        /// In-place zero-copy resize & reply on SDP RX mbuf (fair-full optimal SDP).
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        sdp_zero_copy: bool,
        /// Active slots in DirectPush large-reply TX staging ring (default 1024 = 4 MiB LLC-hot; max 32768 = 128 MiB).
        #[arg(long, default_value_t = 1024)]
        tx_pool_slots: usize,
        /// DirectPush payload threshold >= which DPI DMA is used (default 1024 B).
        #[arg(long, default_value_t = 1024)]
        dma_threshold: usize,
        #[arg(long, default_value_t = 0)]
        credit_lines: u64,
        #[arg(long, default_value_t = 8)]
        credit_hyst: u64,
        #[arg(long, default_value_t = BAR4_CELL_STRIDE_DEFAULT)]
        bar4_stride: usize,
        /// Enable 100% non-multiplexed core + Socket-1 CHA/IIO/IMC PMU counters.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        pmu: bool,
        /// Optional background LCG work on empty polls (off for open-loop latency curves; on for spare-CPU audit).
        #[arg(long)]
        app_work: bool,
        #[arg(long, default_value_t = 0)]
        idle_cal_ms: u64,
        #[arg(long, default_value = "127.0.0.1:7531")]
        ctrl: String,
    }

    // -------------------------------------------------------------------------
    // Real In-Memory Open-Addressing KV Store (32.5 MiB on NUMA-1 HugePages)
    // -------------------------------------------------------------------------
    #[repr(C, align(32))]
    #[derive(Clone, Copy)]
    struct KvBucket {
        key: u64,
        version: u64,
        slot: u32,
        cksum32: u32,
        val_cells: u8,
        occupied: u8,
        _pad: [u8; 6],
    }

    const _: () = assert!(size_of::<KvBucket>() == 32);

    struct KvStore {
        buckets: *mut KvBucket,
        slab: *mut u8,
        next_free_slot: u32,
        probes_total: u64,
    }

    impl KvStore {
        const BUCKETS_BYTES: usize = KV_TABLE_BUCKETS * size_of::<KvBucket>();
        const SLAB_BYTES: usize = KV_NUM_KEYS * KV_SLOT_BYTES;
        const TOTAL_BYTES: usize = Self::BUCKETS_BYTES + Self::SLAB_BYTES;

        unsafe fn from_memzone(ptr: *mut u8) -> Self {
            let buckets = ptr.cast::<KvBucket>();
            let slab = unsafe { ptr.add(Self::BUCKETS_BYTES) };
            KvStore { buckets, slab, next_free_slot: 0, probes_total: 0 }
        }

        fn populate(&mut self, profile: KvProfile) {
            unsafe {
                std::ptr::write_bytes(self.buckets, 0, KV_TABLE_BUCKETS);
            }
            self.next_free_slot = 0;
            self.probes_total = 0;
            for k in 0..KV_NUM_KEYS {
                let key = k as u64;
                let cells = profile.cells_for_key(k);
                let n_words = val_words_for_cells(cells as usize);
                let ver = 1u64;
                let slot = self.next_free_slot;
                self.next_free_slot += 1;

                let val_ptr = unsafe { self.slab.add((slot as usize) * KV_SLOT_BYTES).cast::<u64>() };
                let mut acc = cksum_init(key, ver, cells);
                for wi in 0..n_words {
                    let w = kv_value_word(key, ver, wi);
                    unsafe { *val_ptr.add(wi) = w };
                    acc = cksum_step(acc, w);
                }
                let ck = cksum_finish(acc);

                let mut idx = (splitmix64(key) as usize) & KV_TABLE_MASK;
                loop {
                    let b = unsafe { &mut *self.buckets.add(idx) };
                    if b.occupied == 0 {
                        b.key = key;
                        b.version = ver;
                        b.slot = slot;
                        b.cksum32 = ck;
                        b.val_cells = cells;
                        b.occupied = 1;
                        break;
                    }
                    idx = (idx + 1) & KV_TABLE_MASK;
                }
            }
        }

        #[inline(always)]
        fn lookup(&mut self, key: u64) -> Option<&mut KvBucket> {
            let mut idx = (splitmix64(key) as usize) & KV_TABLE_MASK;
            for step in 1..=KV_TABLE_BUCKETS {
                let b = unsafe { &mut *self.buckets.add(idx) };
                if b.occupied == 0 {
                    self.probes_total += step as u64;
                    return None;
                }
                if b.key == key {
                    self.probes_total += step as u64;
                    return Some(b);
                }
                idx = (idx + 1) & KV_TABLE_MASK;
            }
            None
        }

        #[inline(always)]
        fn val_slice(&self, slot: u32, n_words: usize) -> &[u64] {
            unsafe {
                let p = self.slab.add((slot as usize) * KV_SLOT_BYTES).cast::<u64>();
                std::slice::from_raw_parts(p, n_words)
            }
        }

        #[inline(always)]
        fn val_slice_mut(&mut self, slot: u32, n_words: usize) -> &mut [u64] {
            unsafe {
                let p = self.slab.add((slot as usize) * KV_SLOT_BYTES).cast::<u64>();
                std::slice::from_raw_parts_mut(p, n_words)
            }
        }

        /// Shared `GET(key)` lookup + slab read + 32-bit checksum helper used identically
        /// by both `Mode::Sdp` and `Mode::Direct` to guarantee 100% isomorphic KV work.
        /// Emits `(cell_i, word_k, word)` directly without per-word division.
        #[inline(always)]
        fn execute_get<P, W, C>(&mut self, key: u64, prepare: P, mut write_word: W) -> GetOutcome<C>
        where
            P: FnOnce(u8) -> Option<C>,
            W: FnMut(&mut C, usize, usize, u64),
        {
            let Some(b) = self.lookup(key) else {
                return GetOutcome::Miss;
            };
            let slot = b.slot;
            let vc = b.val_cells;
            let ver = b.version;
            let Some(mut ctx) = prepare(vc) else {
                return GetOutcome::PrepErr;
            };
            let cells = vc as usize;
            let n_words = val_words_for_cells(cells);
            let words = self.val_slice(slot, n_words);
            let mut acc = cksum_init(key, ver, vc);
            let w0 = words[0];
            acc = cksum_step(acc, w0);
            write_word(&mut ctx, 0, 6, w0);
            let mut wi = 1usize;
            for cell_i in 1..cells {
                for word_k in 1..7 {
                    let w = words[wi];
                    wi += 1;
                    acc = cksum_step(acc, w);
                    write_word(&mut ctx, cell_i, word_k, w);
                }
            }
            GetOutcome::Hit { val_cells: vc, version: ver, cksum32: cksum_finish(acc), ctx }
        }

        /// Execute `PUT(key, cells, new_ver, hdr_cksum32)`: probes hash table, copies
        /// incoming value words into 32 MiB slab, verifies checksum, updates metadata.
        #[inline(always)]
        fn execute_put<F>(&mut self, key: u64, cells: u8, new_ver: u64, hdr_cksum32: u32, mut read_word: F) -> (u8, u32)
        where
            F: FnMut(usize, usize) -> u64,
        {
            let n_cells = cells as usize;
            let n_words = val_words_for_cells(n_cells);
            let Some(b) = self.lookup(key) else {
                return (KV_STATUS_MISS, 1);
            };
            let slot = b.slot;
            let dst = self.val_slice_mut(slot, n_words);
            let mut acc = cksum_init(key, new_ver, cells);
            let w0 = read_word(0, 6);
            dst[0] = w0;
            acc = cksum_step(acc, w0);
            let mut wi = 1usize;
            for cell_i in 1..n_cells {
                for word_k in 1..7 {
                    let w = read_word(cell_i, word_k);
                    dst[wi] = w;
                    wi += 1;
                    acc = cksum_step(acc, w);
                }
            }
            let computed = cksum_finish(acc);
            if computed != hdr_cksum32 {
                return (KV_STATUS_ERR, computed);
            }
            let b2 = self.lookup(key).unwrap();
            b2.val_cells = cells;
            b2.version = new_ver;
            b2.cksum32 = computed;
            (KV_STATUS_OK, computed)
        }
    }

    enum GetOutcome<C> {
        Hit { val_cells: u8, version: u64, cksum32: u32, ctx: C },
        Miss,
        PrepErr,
    }

    // -------------------------------------------------------------------------
    // Helper: serialize a large (>= 1024 B) GET reply directly into a contiguous
    // buffer (either `tx_pool` for DirectPush DMA or in-place SDP mbuf)
    // -------------------------------------------------------------------------
    #[inline(always)]
    fn write_wire_word_ck(dst: &mut [u8], cell_i: usize, word_k: usize, w: u64) {
        let off = cell_i * 64 + word_k * 8;
        dst[off..off + 8].copy_from_slice(&w.to_le_bytes());
    }

    #[inline(always)]
    fn finalize_wire_reply_hdr(
        dst: &mut [u8],
        rpc_seq: u64,
        client_tsc: u64,
        key: u64,
        kv_op: u8,
        val_cells: u8,
        status: u8,
        version: u64,
        cksum32: u32,
    ) {
        let cells = val_cells as usize;
        let rpc_bytes = cells * 64;
        let bytes_div64 = cells as u16;
        let frag_cnt = frag_cnt_for_bytes(rpc_bytes);
        let seg_cells = if frag_cnt > 1 { MAX_SEG_BYTES / 64 } else { cells };

        for c in 1..cells {
            let base = c * 64;
            let fidx = (c / seg_cells) as u8;
            let seg_meta = pack_meta(rpc_seq, bytes_div64, fidx, frag_cnt, OP_NET);
            let is_seg_start = (c % seg_cells) == 0;
            let is_seg_end = ((c + 1) % seg_cells) == 0;
            let head_tag = if is_seg_start { seg_meta } else { seg_meta ^ (c as u64) };
            let tail_tag = if is_seg_end { seg_meta } else { head_tag };
            dst[base..base + 8].copy_from_slice(&head_tag.to_le_bytes());
            dst[base + 56..base + 64].copy_from_slice(&tail_tag.to_le_bytes());
        }

        let c0_meta = pack_meta(rpc_seq, bytes_div64, 0, frag_cnt, OP_NET);
        let kv_meta = pack_kv_meta(kv_op, val_cells, status, cksum32);
        dst[0..8].copy_from_slice(&c0_meta.to_le_bytes());
        dst[8..16].copy_from_slice(&client_tsc.to_le_bytes());
        dst[16..24].copy_from_slice(&kv_meta.to_le_bytes());
        dst[24..32].copy_from_slice(&rpc_seq.to_le_bytes());
        dst[32..40].copy_from_slice(&key.to_le_bytes());
        dst[40..48].copy_from_slice(&version.to_le_bytes());
        dst[56..64].copy_from_slice(&c0_meta.to_le_bytes());
    }

    #[inline(always)]
    fn prep_sdp_mbuf<'e>(
        mut pkt: dpdk::Mbuf<'e>,
        flen: usize,
        target_flen: usize,
        zero_copy: bool,
        tx_pool: Option<&Mempool<'e>>,
    ) -> Option<dpdk::Mbuf<'e>> {
        if zero_copy {
            if target_flen > flen {
                pkt.append((target_flen - flen) as u16)?;
            } else if target_flen < flen {
                pkt.trim((flen - target_flen) as u16).ok()?;
            }
            swap_macs(pkt.data_mut());
            Some(pkt)
        } else {
            let tp = tx_pool?;
            let mut m = tp.alloc()?;
            let dst = m.append(target_flen as u16)?;
            let src = pkt.data();
            dst[0..6].copy_from_slice(&src[6..12]);
            dst[6..12].copy_from_slice(&src[0..6]);
            dst[12..SDP_HDR_LEN].copy_from_slice(&src[12..SDP_HDR_LEN]);
            Some(m)
        }
    }
    /// Write a 64 B ACK/status reply directly into a contiguous buffer (for SDP).
    #[inline(always)]
    fn write_wire_ack64(
        dst: &mut [u8],
        rpc_seq: u64,
        client_tsc: u64,
        key: u64,
        kv_op: u8,
        val_cells: u8,
        status: u8,
        version: u64,
        cksum32: u32,
        val_w0: u64,
    ) {
        let meta = pack_meta(rpc_seq, 1, 0, 1, OP_NET);
        let kv_meta = pack_kv_meta(kv_op, val_cells, status, cksum32);
        dst[0..8].copy_from_slice(&meta.to_le_bytes());
        dst[8..16].copy_from_slice(&client_tsc.to_le_bytes());
        dst[16..24].copy_from_slice(&kv_meta.to_le_bytes());
        dst[24..32].copy_from_slice(&rpc_seq.to_le_bytes());
        dst[32..40].copy_from_slice(&key.to_le_bytes());
        dst[40..48].copy_from_slice(&version.to_le_bytes());
        dst[48..56].copy_from_slice(&val_w0.to_le_bytes());
        dst[56..64].copy_from_slice(&meta.to_le_bytes());
    }

    // -------------------------------------------------------------------------
    // 100% Non-Multiplexed Host Core + Socket-1 CHA/IIO/IMC PMU (from MRV-166)
    // -------------------------------------------------------------------------
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const REF_HZ: f64 = 2.2e9;

    #[repr(C)]
    #[derive(Default)]
    struct PerfEventAttr {
        type_: u32,
        size: u32,
        config: u64,
        sample_period_or_freq: u64,
        sample_type: u64,
        read_format: u64,
        flags: u64,
        wakeup_events_or_watermark: u32,
        bp_type: u32,
        config1: u64,
        config2: u64,
        branch_sample_type: u64,
        sample_regs_user: u64,
        sample_stack_user: u32,
        clockid: i32,
        sample_regs_intr: u64,
        aux_watermark: u32,
        sample_max_stack: u16,
        __reserved_2: u16,
        aux_sample_size: u32,
        __reserved_3: u32,
        sig_data: u64,
        config3: u64,
    }

    const PERF_TYPE_HARDWARE: u32 = 0;
    const PERF_TYPE_SOFTWARE: u32 = 1;
    const PERF_TYPE_HW_CACHE: u32 = 3;
    const PERF_COUNT_HW_CPU_CYCLES: u64 = 0;
    const PERF_COUNT_HW_INSTRUCTIONS: u64 = 1;
    const PERF_COUNT_HW_REF_CPU_CYCLES: u64 = 9;
    const PERF_COUNT_SW_PAGE_FAULTS: u64 = 2;
    const CORE_LLC_LOADS: u64 = 0x00002;
    const CORE_LLC_LOAD_MISSES: u64 = 0x10002;
    const CORE_DTLB_LOAD_MISSES: u64 = 0x10003;

    const CHA_TOR_INSERTS_IA_WCILF: u64 = 0x00c8_67ff_0000_0135;
    const CHA_TOR_INSERTS_IO_HIT_ITOM: u64 = 0x00cc_43fd_0000_0435;
    const CHA_TOR_INSERTS_IO_MISS_ITOM: u64 = 0x00cc_43fe_0000_0435;
    const UNCORE_CLOCKTICKS: u64 = 0x01;
    const IIO_TXN_REQ_BY_CPU_MEM_WRITE: u64 = 0x0007_0ff0_0000_01c1;
    const IIO_DATA_REQ_OF_CPU_MEM_WRITE: u64 = 0x0007_0ff0_0000_0184;
    const IIO_DATA_REQ_OF_CPU_MEM_READ: u64 = 0x0007_0ff0_0000_0484;
    const IMC_CAS_COUNT_READ: u64 = 0xcf05;
    const IMC_CAS_COUNT_WRITE: u64 = 0xf005;

    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    struct PerfCounter {
        fd: RawFd,
    }

    #[derive(Clone, Copy, Default)]
    struct Sample {
        val: u64,
        enabled_ns: u64,
        running_ns: u64,
    }

    impl Sample {
        fn delta(self, earlier: Self) -> Self {
            Sample {
                val: self.val.saturating_sub(earlier.val),
                enabled_ns: self.enabled_ns.saturating_sub(earlier.enabled_ns),
                running_ns: self.running_ns.saturating_sub(earlier.running_ns),
            }
        }
        fn pct(self) -> f64 {
            if self.enabled_ns == 0 {
                100.0
            } else {
                (self.running_ns as f64 / self.enabled_ns as f64) * 100.0
            }
        }
    }

    impl PerfCounter {
        fn open(type_: u32, config: u64, cpu: i32) -> io::Result<Self> {
            let attr = PerfEventAttr {
                type_,
                size: size_of::<PerfEventAttr>() as u32,
                config,
                read_format: PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING,
                flags: PERF_ATTR_FLAG_DISABLED,
                ..Default::default()
            };
            let fd = unsafe {
                libc::syscall(
                    libc::SYS_perf_event_open,
                    &raw const attr,
                    -1i32 as libc::pid_t,
                    cpu as libc::c_int,
                    -1i32 as libc::c_int,
                    0 as libc::c_ulong,
                )
            };
            if fd < 0 {
                return Err(io::Error::last_os_error());
            }
            let c = PerfCounter { fd: fd as RawFd };
            let rc = unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) };
            if rc != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> Sample {
            let mut buf = [0u64; 3];
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>()) };
            if n == size_of::<[u64; 3]>() as isize {
                Sample { val: buf[0], enabled_ns: buf[1], running_ns: buf[2] }
            } else {
                Sample::default()
            }
        }
    }

    impl Drop for PerfCounter {
        fn drop(&mut self) {
            unsafe {
                libc::ioctl(self.fd, PERF_EVENT_IOC_DISABLE, 0);
                libc::close(self.fd);
            }
        }
    }

    fn read_pmu_type(name: &str) -> Result<u32, Box<dyn Error>> {
        let p = format!("/sys/bus/event_source/devices/{name}/type");
        let s = fs::read_to_string(&p).map_err(|e| format!("read {p}: {e}"))?;
        Ok(s.trim().parse::<u32>()?)
    }

    struct HostPmu {
        cpu: i32,
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        instr: PerfCounter,
        page_faults: PerfCounter,
        llc_loads: PerfCounter,
        llc_load_misses: PerfCounter,
        dtlb_load_misses: PerfCounter,
        cha_wcilf: Vec<PerfCounter>,
        cha_io_hit: Vec<PerfCounter>,
        cha_io_miss: Vec<PerfCounter>,
        mesh_clk: PerfCounter,
        iio1_cpu_wr: PerfCounter,
        iio1_inb_wr: PerfCounter,
        iio1_inb_rd: PerfCounter,
        imc_rd: Vec<PerfCounter>,
        imc_wr: Vec<PerfCounter>,
    }

    #[derive(Clone)]
    struct HostSnap {
        cpu: i32,
        cycles: Sample,
        ref_cycles: Sample,
        instr: Sample,
        page_faults: Sample,
        llc_loads: Sample,
        llc_load_misses: Sample,
        dtlb_load_misses: Sample,
        cha_wcilf: Vec<Sample>,
        cha_io_hit: Vec<Sample>,
        cha_io_miss: Vec<Sample>,
        mesh_clk: Sample,
        iio1_cpu_wr: Sample,
        iio1_inb_wr: Sample,
        iio1_inb_rd: Sample,
        imc_rd: Vec<Sample>,
        imc_wr: Vec<Sample>,
    }

    impl HostPmu {
        fn open(cpu: i32) -> Result<Self, Box<dyn Error>> {
            let cycles = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu)?;
            let ref_cycles = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, cpu)?;
            let instr = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu)?;
            let page_faults = PerfCounter::open(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_PAGE_FAULTS, cpu)?;
            let llc_loads = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOADS, cpu)?;
            let llc_load_misses = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOAD_MISSES, cpu)?;
            let dtlb_load_misses = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_DTLB_LOAD_MISSES, cpu)?;

            let (mut cha_wcilf, mut cha_io_hit, mut cha_io_miss) =
                (Vec::with_capacity(32), Vec::with_capacity(32), Vec::with_capacity(32));
            for i in 0..32 {
                let t = read_pmu_type(&format!("uncore_cha_{i}"))?;
                cha_wcilf.push(PerfCounter::open(t, CHA_TOR_INSERTS_IA_WCILF, SOCKET1_UNCORE_CPU)?);
                cha_io_hit.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_HIT_ITOM, SOCKET1_UNCORE_CPU)?);
                cha_io_miss.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_MISS_ITOM, SOCKET1_UNCORE_CPU)?);
            }
            let mesh_clk = PerfCounter::open(read_pmu_type("uncore_m2pcie_1")?, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let iio1_t = read_pmu_type("uncore_iio_1")?;
            let iio1_cpu_wr = PerfCounter::open(iio1_t, IIO_TXN_REQ_BY_CPU_MEM_WRITE, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_wr = PerfCounter::open(iio1_t, IIO_DATA_REQ_OF_CPU_MEM_WRITE, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_rd = PerfCounter::open(iio1_t, IIO_DATA_REQ_OF_CPU_MEM_READ, SOCKET1_UNCORE_CPU)?;

            let (mut imc_rd, mut imc_wr) = (Vec::with_capacity(8), Vec::with_capacity(8));
            for i in 0..8 {
                let t = read_pmu_type(&format!("uncore_imc_{i}"))?;
                imc_rd.push(PerfCounter::open(t, IMC_CAS_COUNT_READ, SOCKET1_UNCORE_CPU)?);
                imc_wr.push(PerfCounter::open(t, IMC_CAS_COUNT_WRITE, SOCKET1_UNCORE_CPU)?);
            }
            Ok(HostPmu {
                cpu,
                cycles,
                ref_cycles,
                instr,
                page_faults,
                llc_loads,
                llc_load_misses,
                dtlb_load_misses,
                cha_wcilf,
                cha_io_hit,
                cha_io_miss,
                mesh_clk,
                iio1_cpu_wr,
                iio1_inb_wr,
                iio1_inb_rd,
                imc_rd,
                imc_wr,
            })
        }

        fn snapshot(&self) -> HostSnap {
            HostSnap {
                cpu: self.cpu,
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                instr: self.instr.read(),
                page_faults: self.page_faults.read(),
                llc_loads: self.llc_loads.read(),
                llc_load_misses: self.llc_load_misses.read(),
                dtlb_load_misses: self.dtlb_load_misses.read(),
                cha_wcilf: self.cha_wcilf.iter().map(PerfCounter::read).collect(),
                cha_io_hit: self.cha_io_hit.iter().map(PerfCounter::read).collect(),
                cha_io_miss: self.cha_io_miss.iter().map(PerfCounter::read).collect(),
                mesh_clk: self.mesh_clk.read(),
                iio1_cpu_wr: self.iio1_cpu_wr.read(),
                iio1_inb_wr: self.iio1_inb_wr.read(),
                iio1_inb_rd: self.iio1_inb_rd.read(),
                imc_rd: self.imc_rd.iter().map(PerfCounter::read).collect(),
                imc_wr: self.imc_wr.iter().map(PerfCounter::read).collect(),
            }
        }
    }

    fn format_host_pmu(s0: &HostSnap, s1: &HostSnap, wall_s: f64, tx_rpcs: u64) -> String {
        let mut min_pct = 100.0f64;
        let mut track = |d: Sample| {
            let p = d.pct();
            if p < min_pct {
                min_pct = p;
            }
            d.val
        };
        let cyc = track(s1.cycles.delta(s0.cycles));
        let ref_cyc = track(s1.ref_cycles.delta(s0.ref_cycles));
        let ins = track(s1.instr.delta(s0.instr));
        let pf = track(s1.page_faults.delta(s0.page_faults));
        let llc_l = track(s1.llc_loads.delta(s0.llc_loads));
        let llc_m = track(s1.llc_load_misses.delta(s0.llc_load_misses));
        let dtlb_m = track(s1.dtlb_load_misses.delta(s0.dtlb_load_misses));
        let mesh = track(s1.mesh_clk.delta(s0.mesh_clk));
        let iio_cwr = track(s1.iio1_cpu_wr.delta(s0.iio1_cpu_wr));
        let iio_iwr = track(s1.iio1_inb_wr.delta(s0.iio1_inb_wr));
        let iio_ird = track(s1.iio1_inb_rd.delta(s0.iio1_inb_rd));

        let mut sum_vec = |a: &[Sample], b: &[Sample]| -> u64 {
            a.iter().zip(b.iter()).map(|(x, y)| track(y.delta(*x))).sum()
        };
        let wcilf = sum_vec(&s0.cha_wcilf, &s1.cha_wcilf);
        let io_hit = sum_vec(&s0.cha_io_hit, &s1.cha_io_hit);
        let io_miss = sum_vec(&s0.cha_io_miss, &s1.cha_io_miss);
        let imc_r = sum_vec(&s0.imc_rd, &s1.imc_rd);
        let imc_w = sum_vec(&s0.imc_wr, &s1.imc_wr);

        let ghz = if ref_cyc > 0 { (cyc as f64 / ref_cyc as f64) * (REF_HZ / 1e9) } else { 0.0 };
        let ipc = if cyc > 0 { ins as f64 / cyc as f64 } else { 0.0 };
        let mesh_ghz = if wall_s > 0.0 { mesh as f64 / wall_s / 1e9 } else { 0.0 };
        let itom_tot = io_hit + io_miss;
        let ddio_hit_pct = if itom_tot > 0 { io_hit as f64 * 100.0 / itom_tot as f64 } else { 0.0 };
        let imc_total_bytes = (imc_r + imc_w) * 64;
        let imc_gbps = if wall_s > 0.0 { (imc_total_bytes as f64) / wall_s / 1e9 } else { 0.0 };
        let imc_b_per_req = if tx_rpcs > 0 { (imc_total_bytes as f64) / (tx_rpcs as f64) } else { 0.0 };
        let cyc_per_req = if tx_rpcs > 0 { (cyc as f64) / (tx_rpcs as f64) } else { 0.0 };
        let ins_per_req = if tx_rpcs > 0 { (ins as f64) / (tx_rpcs as f64) } else { 0.0 };

        format!(
            "{{\"cpu\":{},\"cycles\":{},\"ref_cycles\":{},\"instructions\":{},\"ghz\":{:.4},\"ipc\":{:.4},\
             \"cycles_per_req\":{:.1},\"instr_per_req\":{:.1},\
             \"page_faults\":{},\"llc_loads\":{},\"llc_load_misses\":{},\"dtlb_load_misses\":{},\
             \"cha_ia_wcilf\":{},\"cha_io_hit_itom\":{},\"cha_io_miss_itom\":{},\"ddio_hit_pct\":{:.3},\
             \"mesh_ghz\":{:.4},\"iio1_cpu_wr\":{},\"iio1_inb_wr\":{},\"iio1_inb_rd\":{},\
             \"imc_cas_rd\":{},\"imc_cas_wr\":{},\"imc_total_bytes\":{},\"imc_gbps\":{:.4},\"imc_bytes_per_req\":{:.2},\
             \"min_running_pct\":{:.2}}}",
            s1.cpu,
            cyc,
            ref_cyc,
            ins,
            ghz,
            ipc,
            cyc_per_req,
            ins_per_req,
            pf,
            llc_l,
            llc_m,
            dtlb_m,
            wcilf,
            io_hit,
            io_miss,
            ddio_hit_pct,
            mesh_ghz,
            iio_cwr,
            iio_iwr,
            iio_ird,
            imc_r,
            imc_w,
            imc_total_bytes,
            imc_gbps,
            imc_b_per_req,
            min_pct
        )
    }

    // -------------------------------------------------------------------------
    // Host stats & control coordination
    // -------------------------------------------------------------------------
    #[inline(never)]
    fn app_work_step(mut state: u64) -> u64 {
        for _ in 0..160 {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            state ^= state >> 29;
            std::hint::black_box(state);
        }
        state
    }

    fn pin_thread(cpu: usize) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu, &mut set);
            libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set);
        }
    }

    fn read_minor_faults() -> u64 {
        let mut ru: libc::rusage = unsafe { std::mem::zeroed() };
        unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut ru) };
        (ru.ru_minflt + ru.ru_majflt) as u64
    }

    fn read_numa_maps_huge() -> String {
        let mut n0 = 0u64;
        let mut n1 = 0u64;
        let mut kb = 0u64;
        if let Ok(s) = fs::read_to_string("/proc/self/numa_maps") {
            for line in s.lines() {
                if !line.contains("huge") {
                    continue;
                }
                for tok in line.split_whitespace() {
                    if let Some(v) = tok.strip_prefix("N0=") {
                        n0 += v.parse::<u64>().unwrap_or(0);
                    } else if let Some(v) = tok.strip_prefix("N1=") {
                        n1 += v.parse::<u64>().unwrap_or(0);
                    } else if let Some(v) = tok.strip_prefix("kernelpagesize_kB=") {
                        kb = v.parse::<u64>().unwrap_or(0);
                    }
                }
            }
        }
        format!("page_kb={kb},N0={n0},N1={n1}")
    }

    #[derive(Clone, Default)]
    struct HostStats {
        t_reset_tsc: u64,
        dt_s: f64,
        rx_rpcs: u64,
        tx_rpcs: u64,
        get_rpcs: u64,
        put_rpcs: u64,
        small_out: u64,
        large_out: u64,
        kv_misses: u64,
        bad: u64,
        torn: u64,
        wc_split_retries: u64,
        tx_drops: u64,
        bar4_lines_written: u64,
        credit_waits: u64,
        empty_polls: u64,
        busy_polls: u64,
        busy_tsc: u64,
        app_work_units: u64,
        probes_delta: u64,
        page_faults: u64,
        pmu_json: String,
    }

    impl HostStats {
        fn reset(&mut self, now_tsc: u64) {
            *self = HostStats::default();
            self.t_reset_tsc = now_tsc;
            self.pmu_json = "null".to_string();
        }

        fn to_json(&self, tsc_hz: f64, numa: &str) -> String {
            let rpcs_mps = if self.dt_s > 0.0 { self.tx_rpcs as f64 / self.dt_s / 1e6 } else { 0.0 };
            let app_work_kps = if self.dt_s > 0.0 { self.app_work_units as f64 / self.dt_s / 1e3 } else { 0.0 };
            let busy_ns_per_req = if self.tx_rpcs > 0 && tsc_hz > 0.0 {
                (self.busy_tsc as f64 / tsc_hz) * 1e9 / (self.tx_rpcs as f64)
            } else {
                0.0
            };
            let avg_probes = if self.rx_rpcs > 0 {
                self.probes_delta as f64 / self.rx_rpcs as f64
            } else {
                0.0
            };
            format!(
                "{{\"dt_s\":{:.6},\"rx_rpcs\":{},\"tx_rpcs\":{},\"rpcs_mps\":{:.6},\"get_rpcs\":{},\"put_rpcs\":{},\"small_out\":{},\"large_out\":{},\"kv_misses\":{},\"avg_probes\":{:.3},\"bad\":{},\"torn\":{},\"wc_split_retries\":{},\"tx_drops\":{},\"bar4_lines\":{},\"credit_waits\":{},\"empty_polls\":{},\"busy_polls\":{},\"busy_ns_per_req\":{:.1},\"app_work_units\":{},\"app_work_kps\":{:.3},\"page_faults\":{},\"numa\":\"{}\",\"pmu\":{}}}",
                self.dt_s,
                self.rx_rpcs,
                self.tx_rpcs,
                rpcs_mps,
                self.get_rpcs,
                self.put_rpcs,
                self.small_out,
                self.large_out,
                self.kv_misses,
                avg_probes,
                self.bad,
                self.torn,
                self.wc_split_retries,
                self.tx_drops,
                self.bar4_lines_written,
                self.credit_waits,
                self.empty_polls,
                self.busy_polls,
                busy_ns_per_req,
                self.app_work_units,
                app_work_kps,
                self.page_faults,
                numa,
                self.pmu_json
            )
        }
    }

    struct Coord {
        quit: AtomicBool,
        cmd_word: AtomicU64,
        done_gen: AtomicU64,
        cmd_lock: Mutex<()>,
        reset_profile: Mutex<Option<KvProfile>>,
        snap: Mutex<HostStats>,
    }

    impl Coord {
        fn new() -> Self {
            Coord {
                quit: AtomicBool::new(false),
                cmd_word: AtomicU64::new(0),
                done_gen: AtomicU64::new(0),
                cmd_lock: Mutex::new(()),
                reset_profile: Mutex::new(None),
                snap: Mutex::new(HostStats::default()),
            }
        }

        fn call(&self, cmd: u64, profile: Option<KvProfile>) -> Option<HostStats> {
            let _g = self.cmd_lock.lock().unwrap();
            *self.reset_profile.lock().unwrap() = profile;
            let prev = self.cmd_word.load(Ordering::Relaxed) >> 4;
            let next_gen = prev + 1;
            self.cmd_word.store((next_gen << 4) | (cmd & 0xf), Ordering::Release);
            let t0 = Instant::now();
            while self.done_gen.load(Ordering::Acquire) < next_gen {
                if self.quit.load(Ordering::Relaxed) || t0.elapsed() > Duration::from_secs(5) {
                    return None;
                }
                std::thread::sleep(Duration::from_micros(100));
            }
            Some(self.snap.lock().unwrap().clone())
        }
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let mut raw_args: Vec<String> = std::env::args().collect();
        let dash = raw_args.iter().position(|a| a == "--");
        let (eal_args, app_args) = match dash {
            Some(i) => (
                raw_args[..i].to_vec(),
                std::iter::once(raw_args[0].clone()).chain(raw_args.drain(i + 1..)).collect(),
            ),
            None => (vec![raw_args[0].clone()], raw_args),
        };
        let args = Args::parse_from(app_args);

        if args.idle_cal_ms > 0 {
            pin_thread(40);
            let mut st = 0x1234_5678_9abc_def0u64;
            let w0 = Instant::now();
            while w0.elapsed() < Duration::from_millis(200) {
                st = app_work_step(st);
            }
            let t0 = Instant::now();
            let mut units = 0u64;
            let dur = Duration::from_millis(args.idle_cal_ms);
            while t0.elapsed() < dur {
                st = app_work_step(st);
                units += 1;
            }
            let dt = t0.elapsed().as_secs_f64();
            let kps = (units as f64) / dt / 1e3;
            let ns_per = dt * 1e9 / (units as f64);
            println!(
                "{{\"idle_cal\":true,\"dt_s\":{:.6},\"units\":{},\"app_work_kps\":{:.3},\"ns_per_unit\":{:.2},\"sink\":{}}}",
                dt, units, kps, ns_per, st
            );
            return Ok(());
        }

        if args.mode == Mode::Direct && !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }

        let (eal, _) = Eal::init(eal_args)?;
        let tsc_hz = eal.tsc_hz() as f64;
        let cpu = unsafe { libc::sched_getcpu() };
        let socket = Port::socket_of(&eal, 0);

        // Allocate 32.5 MiB KV store on NUMA-1 HugePages (zero page faults!)
        let kv_mz = Memzone::reserve_contiguous(&eal, "m195_kv_store", KvStore::TOTAL_BYTES, socket, 2 * 1024 * 1024)?;
        let mut kv = unsafe { KvStore::from_memzone(kv_mz.as_ptr()) };
        kv.populate(args.kv_profile);

        // Retained 128 MiB TX pool on NUMA-1 HugePages for DirectPush large (>= 1 KiB) DMA replies
        let tx_pool = Memzone::reserve_contiguous(&eal, "m195_tx_pool", TX_POOL_BYTES, socket, 2 * 1024 * 1024)?;
        let tx_pool_ptr = tx_pool.as_ptr();
        let tx_pool_iova = tx_pool.iova();
        unsafe { std::ptr::write_bytes(tx_pool_ptr, 0, TX_POOL_BYTES) };

        // Configure host VF0 `0000:99:02.0` (compact DDIO-fit pool in `--mode sdp`)
        let n_sdp_q = if args.mode == Mode::Sdp { args.sdp_queues } else { 1 };
        let rx_pool_sz = if args.mode == Mode::Sdp && args.sdp_compact_pool {
            ((n_sdp_q as u32) * (args.sdp_rx_desc as u32) + 1023).max(2047)
        } else {
            8192
        };
        let rx_desc = if args.mode == Mode::Sdp { args.sdp_rx_desc } else { 2048 };
        let tx_desc = if args.mode == Mode::Sdp { args.sdp_tx_desc } else { 2048 };
        let rx_pool = Mempool::new_with_buf_size(&eal, "m195_rx_pool", rx_pool_sz, SDP_MBUF_BUF_SIZE, socket)?;
        let tx_mbuf_pool_opt = if args.mode == Mode::Sdp && !args.sdp_zero_copy {
            Some(Mempool::new_with_buf_size(&eal, "m195_tx_mbuf_pool", 2047, SDP_MBUF_BUF_SIZE, socket)?)
        } else {
            None
        };
        let port = Port::start_with_opts_rss(&eal, 0, &rx_pool, n_sdp_q, rx_desc, tx_desc, Some(SDP_MTU), false)?;
        let _ = port.promiscuous();

        // Optional DirectPush BAR4 lease + 8 MiB DDIO-hot host RX zone
        let lease_opt: Option<Lease> = if args.mode == Mode::Direct {
            Some(Lease::acquire(&args.card)?)
        } else {
            None
        };
        let bar4_slot = match lease_opt.as_ref() {
            Some(l) => Some(l.map_slot(&args.bar4)?),
            None => None,
        };
        let mut n_card_cores: usize = args.sdp_queues as usize;
        let rx_zone = if args.mode == Mode::Direct {
            let rz = Memzone::reserve_contiguous(&eal, "m195_rx_zone", RX_ZONE_BYTES, socket, 8 * 1024 * 1024)?;
            unsafe { std::ptr::write_bytes(rz.as_ptr(), 0, RX_ZONE_BYTES) };
            let slot = bar4_slot.as_ref().unwrap();
            let cc = slot.read_u64(CTRL_CARD_CORES) as usize;
            if cc >= 1 && cc <= MAX_CARD_CORES {
                n_card_cores = cc;
            }
            let run_gen = ((std::process::id() as u64) << 16) | (rdtsc_after_loads() & 0xffff) | 1;
            slot.write_u64(CTRL_RX_ZONE_IOVA, rz.iova());
            slot.write_u64(CTRL_CREDIT_HYST, args.credit_hyst.max(1));
            slot.write_u64(CTRL_BAR4_STRIDE, args.bar4_stride as u64);
            sfence();
            slot.write_u64(CTRL_HOST_READY_GEN, run_gen);
            sfence();
            let t0 = Instant::now();
            loop {
                if slot.lease().check().is_err() {
                    return Err("BAR4 lease lost during handshake".into());
                }
                if slot.read_u64(CTRL_CARD_ACK_GEN) == run_gen {
                    break;
                }
                if t0.elapsed() > Duration::from_secs(5) {
                    return Err("timeout waiting for card CTRL_CARD_ACK_GEN".into());
                }
                std::thread::sleep(Duration::from_millis(1));
            }
            Some(rz)
        } else {
            None
        };

        let rx_zone_ptr: *mut u8 = rx_zone.as_ref().map_or(std::ptr::null_mut(), |rz| rz.as_ptr());
        let host_pmu = if args.pmu { Some(HostPmu::open(cpu)?) } else { None };
        let numa_str = read_numa_maps_huge();

        let coord = Arc::new(Coord::new());
        {
            let c = Arc::clone(&coord);
            ctrlc::set_handler(move || c.quit.store(true, Ordering::Relaxed))?;
        }

        let listener = TcpListener::bind(&args.ctrl)?;
        listener.set_nonblocking(true)?;

        std::thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            let coord_srv = Arc::clone(&coord);
            let numa_srv = numa_str.clone();
            scope.spawn(move || {
                pin_thread(CTRL_CPU);
                while !coord_srv.quit.load(Ordering::Relaxed) {
                    match listener.accept() {
                        Ok((mut stream, _)) => {
                            let _ = stream.set_nodelay(true);
                            let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
                            let mut reader = BufReader::new(stream.try_clone().unwrap());
                            let mut line = String::new();
                            if reader.read_line(&mut line).is_ok() {
                                let parts: Vec<&str> = line.trim().split_whitespace().collect();
                                let cmd = parts.first().copied().unwrap_or("");
                                let reply = match cmd {
                                    "PING" => "{\"ok\":true}".to_string(),
                                    "RESET" => {
                                        let prof = parts.get(1).and_then(|s| KvProfile::from_str_opt(s));
                                        let _ = coord_srv.call(1, prof);
                                        "{\"ok\":true}".to_string()
                                    }
                                    "STATS" => match coord_srv.call(2, None) {
                                        Some(st) => st.to_json(tsc_hz, &numa_srv),
                                        None => "{\"err\":\"timeout\"}".to_string(),
                                    },
                                    "QUIT" => {
                                        let r = match coord_srv.call(3, None) {
                                            Some(st) => st.to_json(tsc_hz, &numa_srv),
                                            None => "{\"ok\":true}".to_string(),
                                        };
                                        coord_srv.quit.store(true, Ordering::Relaxed);
                                        r
                                    }
                                    _ => "{\"err\":\"unknown\"}".to_string(),
                                };
                                let _ = writeln!(stream, "{reply}");
                                let _ = stream.flush();
                            }
                        }
                        Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(2));
                        }
                        Err(_) => {
                            std::thread::sleep(Duration::from_millis(5));
                        }
                    }
                }
            });

            println!(
                "MRV195_HOST READY mode={:?} cpu={} kv_profile={:?} kv_bytes={} n_card_cores={} sdp_queues={} sdp_rx_desc={} numa={} pmu={}",
                args.mode,
                cpu,
                args.kv_profile,
                KvStore::TOTAL_BYTES,
                n_card_cores,
                n_sdp_q,
                rx_desc,
                numa_str,
                host_pmu.is_some()
            );
            let _ = std::io::stdout().flush();

            let mut stats = HostStats::default();
            let now0 = rdtsc_after_loads();
            stats.reset(now0);
            let mut pmu_t0 = Instant::now();
            let mut pmu_s0 = host_pmu.as_ref().map(|p| p.snapshot());
            let mut flt_base = read_minor_faults();
            let mut probes_base = kv.probes_total;

            let mut handled_gen: u64 = 0;
            let mut work_state = 0xdead_beef_cafe_babeu64;
            let mut tx_slot_per_core = [0usize; MAX_CARD_CORES];
            let tx_slots_per_core = (args.tx_pool_slots / n_card_cores.max(1)).clamp(256, TX_POOL_SLOTS / MAX_CARD_CORES);
            let mut rx_burst = Burst::<MAX_DRAIN>::new();
            let mut tx_burst = Burst::<MAX_DRAIN>::new();

            let mut rx_seq = [1u64; MAX_CARD_CORES];
            let mut bar_pos = [1u64; MAX_CARD_CORES];
            let mut bar_consumed = [0u64; MAX_CARD_CORES];
            let mut rr_core: usize = 0;
            let card_credit_cap = if args.credit_lines > 0 {
                (args.credit_lines / (n_card_cores as u64)).max(4)
            } else {
                0
            };

            let mut pending_dma_descs = [RpcCell::default(); MAX_DRAIN];
            let mut pending_dma_pos = [0u64; MAX_DRAIN];
            let mut pending_dma_core = [0usize; MAX_DRAIN];
            let mut extra_cells = [RpcCell::default(); 4];
            let mut out_cells = [RpcCell::default(); 4];
            let mut iter: u64 = 0;

            while !coord.quit.load(Ordering::Relaxed) {
                iter = iter.wrapping_add(1);
                if (iter & 0x3ff) == 0 {
                    if let Some(slot) = bar4_slot.as_ref() {
                        if slot.lease().check().is_err() {
                            break;
                        }
                    }
                    let word = coord.cmd_word.load(Ordering::Acquire);
                    let g = word >> 4;
                    if g > handled_gen {
                        let cmd = word & 0xf;
                        let now = rdtsc_after_loads();
                        if cmd == 1 {
                            if let Some(prof) = coord.reset_profile.lock().unwrap().take() {
                                kv.populate(prof);
                            }
                            stats.reset(now);
                            pmu_t0 = Instant::now();
                            pmu_s0 = host_pmu.as_ref().map(|p| p.snapshot());
                            flt_base = read_minor_faults();
                            probes_base = kv.probes_total;
                        } else {
                            let wall_s = pmu_t0.elapsed().as_secs_f64();
                            stats.dt_s = now.saturating_sub(stats.t_reset_tsc) as f64 / tsc_hz;
                            stats.page_faults = read_minor_faults().saturating_sub(flt_base);
                            stats.probes_delta = kv.probes_total.saturating_sub(probes_base);
                            stats.pmu_json = match (host_pmu.as_ref(), pmu_s0.as_ref()) {
                                (Some(p), Some(s0)) => {
                                    let s1 = p.snapshot();
                                    format_host_pmu(s0, &s1, wall_s, stats.tx_rpcs)
                                }
                                _ => "null".to_string(),
                            };
                            *coord.snap.lock().unwrap() = stats.clone();
                        }
                        handled_gen = g;
                        coord.done_gen.store(g, Ordering::Release);
                        if cmd == 3 {
                            break;
                        }
                    }
                }

                match args.mode {
                    Mode::Sdp => {
                        let mut got_any = false;
                        for q in 0..n_sdp_q {
                            let n = port.rx(q, &mut rx_burst);
                            if n == 0 {
                                continue;
                            }
                            let t_busy0 = rdtsc();
                            got_any = true;
                            rx_burst.drain_into(|pkt| {
                                let flen = pkt.data().len();
                                if flen < SDP_HDR_LEN + 64 {
                                    return;
                                }
                                {
                                    let d = pkt.data();
                                    if d[12] != 0x08 || d[13] != 0x00 || d[23] != 17 {
                                        return;
                                    }
                                    if u16::from_be_bytes([d[36], d[37]]) != SDP_UDP_DST_PORT {
                                        return;
                                    }
                                }

                                let w0 = u64::from_le_bytes(pkt.data()[SDP_HDR_LEN..SDP_HDR_LEN + 8].try_into().unwrap());
                                let (rpc_seq, bytes_div64, _fi, frag_cnt, _op) = unpack_meta(w0);
                                let req_bytes = (bytes_div64 as usize) * 64;
                                if rpc_seq == 0 || req_bytes == 0 || SDP_HDR_LEN + req_bytes > flen || frag_cnt == 0 {
                                    stats.bad += 1;
                                    return;
                                }
                                let tail_w = u64::from_le_bytes(
                                    pkt.data()[SDP_HDR_LEN + req_bytes - 8..SDP_HDR_LEN + req_bytes]
                                        .try_into()
                                        .unwrap(),
                                );
                                if tail_w != w0 {
                                    stats.torn += 1;
                                    return;
                                }

                                let pay = &pkt.data()[SDP_HDR_LEN..SDP_HDR_LEN + req_bytes];
                                let client_tsc = u64::from_le_bytes(pay[8..16].try_into().unwrap());
                                let kv_meta = u64::from_le_bytes(pay[16..24].try_into().unwrap());
                                let hdr_rpc_seq = u64::from_le_bytes(pay[24..32].try_into().unwrap());
                                let key = u64::from_le_bytes(pay[32..40].try_into().unwrap());
                                let version = u64::from_le_bytes(pay[40..48].try_into().unwrap());
                                let (kv_op, val_cells_req, _status_in, hdr_ck) = unpack_kv_meta(kv_meta);
                                if hdr_rpc_seq != rpc_seq {
                                    stats.bad += 1;
                                    return;
                                }
                                stats.rx_rpcs += 1;

                                // Execute real KV operation via unified `execute_get` / `execute_put`
                                let out_pkt_opt: Option<dpdk::Mbuf<'_>>;
                                let resp_bytes: usize;


                                if kv_op == KV_GET {
                                    stats.get_rpcs += 1;
                                    let mut pkt_holder = Some(pkt);
                                    match kv.execute_get(
                                        key,
                                        |vc| {
                                            let rbytes = (vc as usize) * 64;
                                            let target_flen = SDP_HDR_LEN + rbytes;
                                            let p = pkt_holder.take().unwrap();
                                            let mut m = prep_sdp_mbuf(p, flen, target_flen, args.sdp_zero_copy, tx_mbuf_pool_opt.as_ref())?;
                                            let data = m.data_mut();
                                            let ip_tot = (20 + 8 + 6 + rbytes) as u16;
                                            let udp_len = (8 + 6 + rbytes) as u16;
                                            data[16..18].copy_from_slice(&ip_tot.to_be_bytes());
                                            data[38..40].copy_from_slice(&udp_len.to_be_bytes());
                                            Some((m, target_flen))
                                        },
                                        |(m, target_flen), cell_i, word_k, w| {
                                            let out_pay = &mut m.data_mut()[SDP_HDR_LEN..*target_flen];
                                            write_wire_word_ck(out_pay, cell_i, word_k, w);
                                        },
                                    ) {
                                        GetOutcome::Hit { val_cells: vc, version: ver, cksum32: ck, ctx: (mut m, target_flen) } => {
                                            resp_bytes = (vc as usize) * 64;
                                            let out_pay = &mut m.data_mut()[SDP_HDR_LEN..target_flen];
                                            finalize_wire_reply_hdr(
                                                out_pay,
                                                rpc_seq,
                                                client_tsc,
                                                key,
                                                KV_GET,
                                                vc,
                                                KV_STATUS_OK,
                                                ver,
                                                ck,
                                            );
                                            out_pkt_opt = Some(m);
                                        }
                                        GetOutcome::Miss => {
                                            stats.kv_misses += 1;
                                            resp_bytes = 64;
                                            let target_flen = SDP_HDR_LEN + 64;
                                            if let Some(mut m) = prep_sdp_mbuf(pkt_holder.take().unwrap(), flen, target_flen, args.sdp_zero_copy, tx_mbuf_pool_opt.as_ref()) {
                                                let data = m.data_mut();
                                                let ip_tot = (20 + 8 + 6 + 64) as u16;
                                                let udp_len = (8 + 6 + 64) as u16;
                                                data[16..18].copy_from_slice(&ip_tot.to_be_bytes());
                                                data[38..40].copy_from_slice(&udp_len.to_be_bytes());
                                                write_wire_ack64(
                                                    &mut data[SDP_HDR_LEN..target_flen],
                                                    rpc_seq,
                                                    client_tsc,
                                                    key,
                                                    KV_GET,
                                                    1,
                                                    KV_STATUS_MISS,
                                                    0,
                                                    1,
                                                    0,
                                                );
                                                out_pkt_opt = Some(m);
                                            } else {
                                                stats.tx_drops += 1;
                                                return;
                                            }
                                        }
                                        GetOutcome::PrepErr => {
                                            stats.tx_drops += 1;
                                            return;
                                        }
                                    }
                                } else if kv_op == KV_PUT {
                                    stats.put_rpcs += 1;
                                    let req_cells = (req_bytes / 64) as u8;
                                    let (st, ck) = kv.execute_put(key, req_cells, version, hdr_ck, |cell_i, word_k| {
                                        let off = cell_i * 64 + word_k * 8;
                                        u64::from_le_bytes(pay[off..off + 8].try_into().unwrap())
                                    });
                                    if st == KV_STATUS_ERR {
                                        stats.bad += 1;
                                    } else if st == KV_STATUS_MISS {
                                        stats.kv_misses += 1;
                                    }
                                    resp_bytes = 64;
                                    let target_flen = SDP_HDR_LEN + 64;
                                    if let Some(mut m) = prep_sdp_mbuf(pkt, flen, target_flen, args.sdp_zero_copy, tx_mbuf_pool_opt.as_ref()) {
                                        let data = m.data_mut();
                                        let ip_tot = (20 + 8 + 6 + 64) as u16;
                                        let udp_len = (8 + 6 + 64) as u16;
                                        data[16..18].copy_from_slice(&ip_tot.to_be_bytes());
                                        data[38..40].copy_from_slice(&udp_len.to_be_bytes());
                                        write_wire_ack64(
                                            &mut data[SDP_HDR_LEN..target_flen],
                                            rpc_seq,
                                            client_tsc,
                                            key,
                                            KV_PUT,
                                            val_cells_req,
                                            st,
                                            version,
                                            ck,
                                            0,
                                        );
                                        out_pkt_opt = Some(m);
                                    } else {
                                        stats.tx_drops += 1;
                                        return;
                                    }
                                } else {
                                    stats.bad += 1;
                                    return;
                                }

                                let Some(out_pkt) = out_pkt_opt else {
                                    return;
                                };
                                if let Err(p_back) = tx_burst.push(out_pkt) {
                                    while !tx_burst.is_empty() {
                                        let _ = port.tx(q, &mut tx_burst);
                                    }
                                    let _ = tx_burst.push(p_back);
                                }
                                stats.tx_rpcs += 1;
                                if resp_bytes <= 256 {
                                    stats.small_out += 1;
                                } else {
                                    stats.large_out += 1;
                                }
                            });
                            while !tx_burst.is_empty() {
                                if port.tx(q, &mut tx_burst) == 0 {
                                    std::hint::spin_loop();
                                }
                            }
                            stats.busy_tsc += rdtsc().wrapping_sub(t_busy0);
                        }
                        if !got_any {
                            stats.empty_polls += 1;
                            if args.app_work {
                                work_state = app_work_step(work_state);
                                stats.app_work_units += 1;
                            }
                        } else {
                            stats.busy_polls += 1;
                        }
                    }

                    Mode::Direct => {
                        let slot = bar4_slot.as_ref().unwrap();
                        let bar_base = slot.as_ptr();
                        let mut got = 0usize;
                        let mut n_dma_descs = 0usize;
                        let mut blocked_credit = false;
                        let t_busy0 = rdtsc();

                        for _ in 0..MAX_DRAIN {
                            let mut found_any = false;
                            let mut off_c = rr_core;
                            for _ in 0..n_card_cores {
                                let c = off_c;
                                let rseq = rx_seq[c];
                                let rx_ptr = unsafe { rx_zone_ptr.add(rx_ring_offset(c, rseq)) };
                                let cell = match unsafe { RpcCell::poll_seq_wc4(rx_ptr, rseq) } {
                                    CellPoll::Empty => {
                                        off_c += 1;
                                        if off_c == n_card_cores {
                                            off_c = 0;
                                        }
                                        continue;
                                    }
                                    CellPoll::Torn => {
                                        stats.wc_split_retries += 1;
                                        let t_wait = rdtsc();
                                        let wait_limit = (tsc_hz / 1_000.0) as u64;
                                        let mut resolved = None;
                                        loop {
                                            match unsafe { RpcCell::poll_seq_wc4(rx_ptr, rseq) } {
                                                CellPoll::Ready(cl) => {
                                                    resolved = Some(cl);
                                                    break;
                                                }
                                                _ => {
                                                    if rdtsc().wrapping_sub(t_wait) > wait_limit {
                                                        break;
                                                    }
                                                    std::hint::spin_loop();
                                                }
                                            }
                                        }
                                        match resolved {
                                            Some(cl) => cl,
                                            None => {
                                                stats.torn += 1;
                                                off_c += 1;
                                                if off_c == n_card_cores {
                                                    off_c = 0;
                                                }
                                                continue;
                                            }
                                        }
                                    }
                                    CellPoll::Ready(cl) => cl,
                                };

                                let (_rseq_got, bytes_div64, _fi, _frag_cnt, op) = unpack_meta(cell.w[0]);
                                let req_bytes = (bytes_div64 as usize) * 64;

                                // Optional credit gate
                                if card_credit_cap > 0 {
                                    let need_lines = if req_bytes < args.dma_threshold {
                                        (req_bytes / 64).max(1) as u64
                                    } else {
                                        1
                                    };
                                    let written = bar_pos[c] - 1;
                                    if written.saturating_sub(bar_consumed[c]) + need_lines > card_credit_cap {
                                        let cptr = unsafe { rx_zone_ptr.add(credit_line_offset(c)) };
                                        if let Some(v) = unsafe { RpcCell::poll_credit(cptr) } {
                                            if v > bar_consumed[c] {
                                                bar_consumed[c] = v;
                                            }
                                        }
                                        if written.saturating_sub(bar_consumed[c]) + need_lines > card_credit_cap {
                                            blocked_credit = true;
                                            off_c += 1;
                                            if off_c == n_card_cores {
                                                off_c = 0;
                                            }
                                            continue;
                                        }
                                    }
                                }

                                // Decode KV request (from OP_PUSH inline cells or OP_DMA large staging)
                                let (rpc_seq, client_tsc, kv_op, val_cells_req, key, version, hdr_ck, req_cells, large_src_opt) =
                                    if op == OP_PUSH {
                                        let cells = req_bytes / 64;
                                        if cells == 0 || cells > 4 {
                                            rx_seq[c] += 1;
                                            stats.bad += 1;
                                            found_any = true;
                                            break;
                                        }
                                        extra_cells[0] = cell;
                                        let mut all_ok = true;
                                        for k in 1..cells {
                                            let cp = unsafe { rx_ptr.add(k * 64) };
                                            let expect_tag = cell.w[0] ^ (k as u64);
                                            let p = cp.cast::<AtomicU64>();
                                            let t0 = unsafe { (*p).load(Ordering::Acquire) };
                                            let t7 = unsafe { (*p.add(7)).load(Ordering::Acquire) };
                                            if t0 != expect_tag || t7 != expect_tag {
                                                all_ok = false;
                                                break;
                                            }
                                            for w in 0..8 {
                                                extra_cells[k].w[w] = unsafe { (*p.add(w)).load(Ordering::Relaxed) };
                                            }
                                        }
                                        if !all_ok {
                                            off_c += 1;
                                            if off_c == n_card_cores {
                                                off_c = 0;
                                            }
                                            continue;
                                        }
                                        rx_seq[c] += 1;
                                        let (kop, vcreq, _st, hck) = unpack_kv_meta(extra_cells[0].w[2]);
                                        (
                                            extra_cells[0].w[3],
                                            extra_cells[0].w[1],
                                            kop,
                                            vcreq,
                                            extra_cells[0].w[4] & 0x0000_ffff_ffff_ffff,
                                            extra_cells[0].w[5],
                                            hck,
                                            cells as u8,
                                            None,
                                        )
                                    } else if op == OP_DMA {
                                        rx_seq[c] += 1;
                                        let client_tsc = cell.w[1];
                                        let rpc_seq = cell.w[2];
                                        let lslot = (cell.w[3] & 0x0000_ffff_ffff_ffff) as usize;
                                        let src_ptr = unsafe { rx_zone_ptr.add(large_rx_offset(c, lslot)) };
                                        let src = unsafe { std::slice::from_raw_parts(src_ptr, req_bytes) };
                                        let kv_meta = u64::from_le_bytes(src[16..24].try_into().unwrap());
                                        let hdr_seq = u64::from_le_bytes(src[24..32].try_into().unwrap());
                                        let key = u64::from_le_bytes(src[32..40].try_into().unwrap());
                                        let ver = u64::from_le_bytes(src[40..48].try_into().unwrap());
                                        if hdr_seq != rpc_seq {
                                            stats.bad += 1;
                                            found_any = true;
                                            break;
                                        }
                                        let (kop, vcreq, _st, hck) = unpack_kv_meta(kv_meta);
                                        (
                                            rpc_seq,
                                            client_tsc,
                                            kop,
                                            vcreq,
                                            key,
                                            ver,
                                            hck,
                                            (req_bytes / 64) as u8,
                                            Some(src_ptr),
                                        )
                                    } else {
                                        rx_seq[c] += 1;
                                        stats.bad += 1;
                                        found_any = true;
                                        break;
                                    };

                                got += 1;
                                found_any = true;
                                rr_core = if c + 1 == n_card_cores { 0 } else { c + 1 };
                                stats.rx_rpcs += 1;

                                // Execute identical KV operation & emit reply over DirectPush / DPI DMA
                                if kv_op == KV_GET {
                                    stats.get_rpcs += 1;
                                    enum DirectGetBuf {
                                        Push { w0_val: u64 },
                                        Dma { pslot: usize, resp_bytes: usize },
                                    }
                                    match kv.execute_get(
                                        key,
                                        |vc| {
                                            let resp_bytes = (vc as usize) * 64;
                                            if resp_bytes < args.dma_threshold {
                                                Some(DirectGetBuf::Push { w0_val: 0 })
                                            } else {
                                                let pslot = c * tx_slots_per_core + (tx_slot_per_core[c] % tx_slots_per_core);
                                                tx_slot_per_core[c] = tx_slot_per_core[c].wrapping_add(1);
                                                Some(DirectGetBuf::Dma { pslot, resp_bytes })
                                            }
                                        },
                                        |buf, cell_i, word_k, w| match buf {
                                            DirectGetBuf::Push { w0_val } => {
                                                if cell_i == 0 {
                                                    *w0_val = w;
                                                } else {
                                                    out_cells[cell_i].w[word_k] = w;
                                                }
                                            }
                                            DirectGetBuf::Dma { pslot, resp_bytes } => {
                                                let dst_ptr = unsafe { tx_pool_ptr.add(*pslot * TX_POOL_SLOT_BYTES) };
                                                let dst_slice = unsafe { std::slice::from_raw_parts_mut(dst_ptr, *resp_bytes) };
                                                write_wire_word_ck(dst_slice, cell_i, word_k, w);
                                            }
                                        },
                                    ) {
                                        GetOutcome::Hit { val_cells: vc, version: ver, cksum32: ck, ctx } => match ctx {
                                            DirectGetBuf::Push { w0_val } => {
                                                let cells = vc as usize;
                                                for k in 1..cells {
                                                    let my_pos = bar_pos[c] + k as u64;
                                                    let m = pack_meta(my_pos, vc as u16, 0, 1, OP_PUSH);
                                                    out_cells[k].w[0] = m;
                                                    out_cells[k].w[7] = m;
                                                }
                                                let m0 = pack_meta(bar_pos[c], vc as u16, 0, 1, OP_PUSH);
                                                out_cells[0] = RpcCell {
                                                    w: [
                                                        m0,
                                                        client_tsc,
                                                        pack_kv_meta(KV_GET, vc, KV_STATUS_OK, ck),
                                                        rpc_seq,
                                                        key,
                                                        ver,
                                                        w0_val,
                                                        m0,
                                                    ],
                                                };
                                                for k in 0..cells {
                                                    let my_pos = bar_pos[c] + k as u64;
                                                    unsafe {
                                                        movdir64b(
                                                            bar_base.add(bar4_cell_offset(c, my_pos, args.bar4_stride)),
                                                            &out_cells[k],
                                                        );
                                                    }
                                                }
                                                bar_pos[c] += cells as u64;
                                                stats.bar4_lines_written += cells as u64;
                                                stats.tx_rpcs += 1;
                                                stats.small_out += 1;
                                            }
                                            DirectGetBuf::Dma { pslot, resp_bytes } => {
                                                let dst_ptr = unsafe { tx_pool_ptr.add(pslot * TX_POOL_SLOT_BYTES) };
                                                let dst_slice = unsafe { std::slice::from_raw_parts_mut(dst_ptr, resp_bytes) };
                                                finalize_wire_reply_hdr(
                                                    dst_slice,
                                                    rpc_seq,
                                                    client_tsc,
                                                    key,
                                                    KV_GET,
                                                    vc,
                                                    KV_STATUS_OK,
                                                    ver,
                                                    ck,
                                                );
                                                let my_pos = bar_pos[c];
                                                bar_pos[c] += 1;
                                                let rfrag = frag_cnt_for_bytes(resp_bytes);
                                                let m = pack_meta(my_pos, vc as u16, 0, rfrag, OP_DMA);
                                                pending_dma_descs[n_dma_descs] = RpcCell {
                                                    w: [
                                                        m,
                                                        client_tsc,
                                                        rpc_seq,
                                                        tx_pool_iova + (pslot * TX_POOL_SLOT_BYTES) as u64,
                                                        resp_bytes as u64,
                                                        0,
                                                        0,
                                                        m,
                                                    ],
                                                };
                                                pending_dma_pos[n_dma_descs] = my_pos;
                                                pending_dma_core[n_dma_descs] = c;
                                                n_dma_descs += 1;
                                                stats.bar4_lines_written += 1;
                                                stats.tx_rpcs += 1;
                                                stats.large_out += 1;
                                            }
                                        },
                                        GetOutcome::Miss => {
                                            stats.kv_misses += 1;
                                            let my_pos = bar_pos[c];
                                            bar_pos[c] += 1;
                                            let m0 = pack_meta(my_pos, 1, 0, 1, OP_PUSH);
                                            let ack = RpcCell {
                                                w: [
                                                    m0,
                                                    client_tsc,
                                                    pack_kv_meta(KV_GET, 1, KV_STATUS_MISS, 1),
                                                    rpc_seq,
                                                    key,
                                                    0,
                                                    0,
                                                    m0,
                                                ],
                                            };
                                            unsafe {
                                                movdir64b(
                                                    bar_base.add(bar4_cell_offset(c, my_pos, args.bar4_stride)),
                                                    &ack,
                                                );
                                            }
                                            stats.bar4_lines_written += 1;
                                            stats.tx_rpcs += 1;
                                            stats.small_out += 1;
                                        }
                                        GetOutcome::PrepErr => {
                                            stats.tx_drops += 1;
                                        }
                                    }
                                } else if kv_op == KV_PUT {
                                    stats.put_rpcs += 1;
                                    let (st, ck) = if let Some(src_ptr) = large_src_opt {
                                        let src = unsafe { std::slice::from_raw_parts(src_ptr, (req_cells as usize) * 64) };
                                        kv.execute_put(key, req_cells, version, hdr_ck, |cell_i, word_k| {
                                            let off = cell_i * 64 + word_k * 8;
                                            u64::from_le_bytes(src[off..off + 8].try_into().unwrap())
                                        })
                                    } else {
                                        kv.execute_put(key, req_cells, version, hdr_ck, |cell_i, word_k| {
                                            extra_cells[cell_i].w[word_k]
                                        })
                                    };
                                    if st == KV_STATUS_ERR {
                                        stats.bad += 1;
                                    } else if st == KV_STATUS_MISS {
                                        stats.kv_misses += 1;
                                    }
                                    // Emit 64 B KV_OK ACK directly via MOVDIR64B OP_PUSH
                                    let my_pos = bar_pos[c];
                                    bar_pos[c] += 1;
                                    let m0 = pack_meta(my_pos, 1, 0, 1, OP_PUSH);
                                    let ack = RpcCell {
                                        w: [
                                            m0,
                                            client_tsc,
                                            pack_kv_meta(KV_PUT, val_cells_req, st, ck),
                                            rpc_seq,
                                            key,
                                            version,
                                            0,
                                            m0,
                                        ],
                                    };
                                    unsafe {
                                        movdir64b(bar_base.add(bar4_cell_offset(c, my_pos, args.bar4_stride)), &ack);
                                    }
                                    stats.bar4_lines_written += 1;
                                    stats.tx_rpcs += 1;
                                    stats.small_out += 1;
                                } else {
                                    stats.bad += 1;
                                }
                                break;
                            }
                            if !found_any {
                                break;
                            }
                        }

                        if n_dma_descs > 0 {
                            sfence();
                            for i in 0..n_dma_descs {
                                let c = pending_dma_core[i];
                                unsafe {
                                    movdir64b(
                                        bar_base.add(bar4_cell_offset(c, pending_dma_pos[i], args.bar4_stride)),
                                        &pending_dma_descs[i],
                                    );
                                }
                            }
                        }

                        if got > 0 {
                            stats.busy_tsc += rdtsc().wrapping_sub(t_busy0);
                            stats.busy_polls += 1;
                        } else {
                            if blocked_credit {
                                stats.credit_waits += 1;
                            }
                            stats.empty_polls += 1;
                            if args.app_work {
                                work_state = app_work_step(work_state);
                                stats.app_work_units += 1;
                            }
                        }
                    }
                }
            }

            // Before unmapping VF0 IOMMU memory on exit, ask card to detach /dev/pem_wc!
            if args.mode == Mode::Direct {
                if let Ok(mut s) = std::net::TcpStream::connect_timeout(
                    &mrv195::SYNC_ADDR.parse().unwrap(),
                    Duration::from_secs(2),
                ) {
                    let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
                    let _ = writeln!(s, "DETACH");
                    let _ = s.flush();
                    let mut resp = String::new();
                    let _ = BufReader::new(s).read_line(&mut resp);
                }
            }
            let now_end = rdtsc_after_loads();
            stats.dt_s = now_end.saturating_sub(stats.t_reset_tsc) as f64 / tsc_hz;
            println!("MRV195_HOST_DONE {}", stats.to_json(tsc_hz, &numa_str));
            let _ = std::hint::black_box(work_state);
            Ok(())
        })?;

        Ok(())
    }
}
