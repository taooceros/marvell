//! Single-host-core (`cpu40 @ 3.90 GHz`) server embedding 100% unmodified upstream
//! Memcached 1.6.45 (`github.com/memcached/memcached`, 0 lines modified in upstream core)
//! on `saturn` for MRV-206.
//!
//! Runs the EXACT SAME upstream Memcached core engine (`proto_bin.c` binary protocol parser,
//! Murmur3 hash, `assoc.c` hash table, `slabs.c` slab allocator, `items.c` segmented LRU,
//! CAS, item refcounting, and per-thread/slab stats) over two host<->NIC interfaces:
//!   - `--mode sdp`:    Fair async optimal `SDP fair-full` (`rx_poll_mem=1,rx_poll_tail_off=48,
//!                      ism_enable=1,allow_small_oq=1`, 4Q x 256 DDIO-fit RX desc, compact RX
//!                      pool, in-place zero-copy resize, batched `port.tx()`).
//!   - `--mode direct`: `DirectPush` (`<= 192 B` Memcached UDP via 1..4 x 64 B L1-stack
//!                      `MOVDIR64B` to 128 B-stride exclusive BAR4 sub-rings + `> 192 B`
//!                      via native bidirectional CN106 DPI DMA from retained LLC-hot `tx_pool`).
//!
//! Embeds 100% non-multiplexed Core + Socket-1 CHA/IIO/IMC PMU (`HostPmu`) snapshotted
//! cleanly across each `RESET` .. `STATS` measurement window.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv206_host runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::error::Error;
    use std::ffi::CStr;
    use std::fs;
    use std::io::{self, Read, Write};
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, Mempool, Memzone, Port, rdtsc, rdtsc_after_loads};
    use testbed::lease::Lease;
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};
    use mrv206::{
        BAR4_CELL_STRIDE_DEFAULT, CTRL_BAR4_STRIDE, CTRL_CARD_ACK_GEN, CTRL_CARD_CORES,
        CTRL_CREDIT_HYST, CTRL_HOST_READY_GEN, CTRL_RX_ZONE_IOVA, CellPoll, MAX_CARD_CORES,
        MAX_DRAIN, MAX_MC_BYTES, MC_CMD_GET, MC_CMD_SET, MC_NUM_KEYS, MC_STATUS_ENOENT,
        MC_STATUS_OK, MC_UDP_DATA_SIZE, McCell, OP_DMA, OP_NET, OP_PUSH, OP_PUSH_MAX_MC_BYTES,
        RX_ZONE_BYTES, SDP_HDR_LEN, SDP_MBUF_BUF_SIZE, SDP_MTU, SDP_UDP_DST_PORT, SYNC_ADDR,
        TX_POOL_BYTES, TX_POOL_SLOT_BYTES, TX_POOL_SLOTS, bar4_cell_offset, build_mc_set_udp,
        credit_line_offset, large_rx_offset, mix_val_len_for_key, pack_cell_meta, pack_wire_meta,
        push_cells_for_mc_len, rx_ring_offset, small_mix_val_len_for_key, unpack_cell_meta,
        unpack_wire_meta, wire_frag_cnt, wire_total_bytes,
    };
    use testbed::swap_macs;

    const CTRL_CPU: usize = 44;
    const MRV206_MC_MAX_IOVS: usize = 8;
    const MRV206_MC_MAX_UDP_DGRAMS: usize = 4;

    // -------------------------------------------------------------------------
    // FFI to 100% unmodified upstream Memcached 1.6.45 via mrv206_mc_adapter.c
    // -------------------------------------------------------------------------
    #[repr(C)]
    #[derive(Clone, Copy)]
    struct McReply {
        udp_hdrs: [[u8; 8]; MRV206_MC_MAX_UDP_DGRAMS],
        udp_dgram_cnt: u32,
        iov: [libc::iovec; MRV206_MC_MAX_IOVS],
        iovcnt: u32,
        bin_len: u32,
        total_len: u32,
        status: u16,
        opcode: u8,
        _pad: u8,
    }
    impl Default for McReply {
        fn default() -> Self {
            unsafe { std::mem::zeroed() }
        }
    }

    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    struct McStats {
        get_cmds: u64,
        get_hits: u64,
        get_misses: u64,
        set_cmds: u64,
        delete_hits: u64,
        delete_misses: u64,
        evictions: u64,
        reclaimed: u64,
        curr_items: u64,
        total_items: u64,
        bytes_stored: u64,
        bytes_read: u64,
        bytes_written: u64,
        hashpower: u32,
        maxbytes_mib: u32,
    }

    unsafe extern "C" {
        fn mrv206_mc_init(maxbytes: usize, hashpower: u32, maint_cpu: i32) -> i32;
        fn mrv206_mc_exec(udp_req: *const u8, udp_req_len: usize, out: *mut McReply) -> i32;
        fn mrv206_mc_release();
        fn mrv206_mc_init_mt(maxbytes: usize, hashpower: u32, maint_cpu: i32, n_threads: i32) -> i32;
        fn mrv206_mc_exec_tid(tid: i32, udp_req: *const u8, udp_req_len: usize, out: *mut McReply) -> i32;
        fn mrv206_mc_release_tid(tid: i32);
        fn mrv206_mc_tick_clock();
        fn mrv206_mc_get_stats(st: *mut McStats);
        fn mrv206_mc_reset_stats();
        fn mrv206_mc_shutdown();
        fn mrv206_mc_version() -> *const libc::c_char;
    }

    /// Copy standard Memcached Binary UDP datagram(s) (`[udp_hdrs[d], chunk_d]` for `d = 0..udp_dgram_cnt`)
    /// contiguously into `dst[..reply.total_len]`. Matches upstream `memcached.c:transmit_udp()` byte-for-byte
    /// (`UDP_DATA_SIZE = 1392 B` -> 1 datagram for `<= 1392 B`, 3 datagrams `seq=0..2` for `3840 B GET`).
    #[inline(always)]
    fn copy_mc_reply_contiguous(reply: &McReply, dst: &mut [u8]) {
        if reply.udp_dgram_cnt <= 1 {
            dst[0..8].copy_from_slice(&reply.udp_hdrs[0]);
            let mut off = 8usize;
            for i in 0..(reply.iovcnt as usize) {
                let len = reply.iov[i].iov_len;
                if len > 0 {
                    let src = unsafe { std::slice::from_raw_parts(reply.iov[i].iov_base.cast::<u8>(), len) };
                    dst[off..off + len].copy_from_slice(src);
                    off += len;
                }
            }
            return;
        }
        let mut dst_off = 0usize;
        let mut dgram = 0usize;
        let mut chunk_rem = 0usize;
        for i in 0..(reply.iovcnt as usize) {
            let len = reply.iov[i].iov_len;
            if len == 0 {
                continue;
            }
            let src = unsafe { std::slice::from_raw_parts(reply.iov[i].iov_base.cast::<u8>(), len) };
            let mut s_off = 0usize;
            while s_off < len {
                if chunk_rem == 0 {
                    dst[dst_off..dst_off + 8].copy_from_slice(&reply.udp_hdrs[dgram]);
                    dst_off += 8;
                    dgram += 1;
                    chunk_rem = MC_UDP_DATA_SIZE;
                }
                let take = chunk_rem.min(len - s_off);
                dst[dst_off..dst_off + take].copy_from_slice(&src[s_off..s_off + take]);
                dst_off += take;
                s_off += take;
                chunk_rem -= take;
            }
        }
    }

    /// Pack single-datagram reply (`<= 192 B`) directly into `out_cells[0..cells]` (48 B/cell)
    /// without integer division or intermediate staging copy.
    #[inline(always)]
    fn pack_mc_reply_into_cells(
        reply: &McReply,
        base_bar_pos: u64,
        tid: u8,
        out_cells: &mut [McCell],
    ) -> usize {
        let mc_len = reply.total_len as usize;
        let cells = push_cells_for_mc_len(mc_len);
        for c in 0..cells {
            out_cells[c] = McCell::default();
            out_cells[c].w[0] = pack_cell_meta(
                base_bar_pos + c as u64,
                mc_len as u16,
                cells as u8,
                tid,
                OP_PUSH,
            );
        }
        let mut c = 0usize;
        let mut c_off = 0usize;
        let mut append = |src: &[u8]| {
            let mut s_off = 0usize;
            while s_off < src.len() {
                let take = (48 - c_off).min(src.len() - s_off);
                out_cells[c].payload_bytes_mut()[c_off..c_off + take]
                    .copy_from_slice(&src[s_off..s_off + take]);
                c_off += take;
                if c_off == 48 {
                    c += 1;
                    c_off = 0;
                }
                s_off += take;
            }
        };
        append(&reply.udp_hdrs[0]);
        for i in 0..(reply.iovcnt as usize) {
            let len = reply.iov[i].iov_len;
            if len > 0 {
                let s = unsafe { std::slice::from_raw_parts(reply.iov[i].iov_base.cast::<u8>(), len) };
                append(s);
            }
        }
        for k in 0..cells {
            out_cells[k].seal();
        }
        cells
    }

    #[inline(never)]
    fn prefault_stack() {
        let mut buf = [0u8; 64 * 1024];
        for i in (0..buf.len()).step_by(4096) {
            unsafe { std::ptr::write_volatile(buf.as_mut_ptr().add(i), (i & 0xff) as u8) };
        }
        std::hint::black_box(&buf);
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Mode {
        Sdp,
        Direct,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum McProfile {
        #[value(name = "8")]
        V8,
        #[value(name = "128")]
        V128,
        #[value(name = "1024")]
        V1024,
        #[value(name = "3840")]
        V3840,
        #[value(name = "small_mix")]
        SmallMix,
        #[value(name = "etc_mix")]
        EtcMix,
    }

    impl McProfile {
        fn from_str_opt(s: &str) -> Option<Self> {
            match s {
                "8" | "get8" | "set8" | "rw8" => Some(McProfile::V8),
                "128" | "get128" | "set128" | "rw128" => Some(McProfile::V128),
                "1024" | "get1024" | "set1024" => Some(McProfile::V1024),
                "3840" | "get3840" | "set3840" => Some(McProfile::V3840),
                "small_mix" | "etc_small" => Some(McProfile::SmallMix),
                "etc_mix" | "mix" | "etc" => Some(McProfile::EtcMix),
                _ => None,
            }
        }

        fn val_len_for_key(self, key: usize, num_keys: usize) -> usize {
            match self {
                McProfile::V8 => 8,
                McProfile::V128 => 128,
                McProfile::V1024 => 1024,
                McProfile::V3840 => 3840,
                McProfile::SmallMix => small_mix_val_len_for_key(key, num_keys),
                McProfile::EtcMix => mix_val_len_for_key(key, num_keys),
            }
        }
    }

    /// Populate `0..num_keys` through genuine upstream Memcached Binary UDP SET packets.
    fn populate_memcached(profile: McProfile, num_keys: usize) -> Result<(), Box<dyn Error>> {
        let mut req = [0u8; MAX_MC_BYTES + 64];
        let mut rep = McReply::default();
        for k in 0..num_keys {
            let vlen = profile.val_len_for_key(k, num_keys);
            let n = build_mc_set_udp((k & 0xffff) as u16, k as u32, k as u64, 1, vlen, &mut req);
            let rc = unsafe { mrv206_mc_exec(req.as_ptr(), n, &mut rep) };
            let st = rep.status;
            unsafe { mrv206_mc_release() };
            if rc != 0 || st != MC_STATUS_OK {
                return Err(format!("populate_memcached key={k} vlen={vlen} failed rc={rc} status={st:#x}").into());
            }
        }
        unsafe { mrv206_mc_reset_stats() };
        Ok(())
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = Mode::Direct)]
        mode: Mode,
        #[arg(long, value_enum, default_value_t = McProfile::EtcMix)]
        mc_profile: McProfile,
        #[arg(long, default_value_t = MC_NUM_KEYS)]
        num_keys: usize,
        /// Memcached slab pool memory limit in MiB (preallocated on NUMA 1).
        #[arg(long, default_value_t = 256)]
        mc_mem_mb: usize,
        /// Memcached hash table power (default 16 = 65,536 buckets).
        #[arg(long, default_value_t = 16)]
        mc_hashpower: u32,
        #[arg(long, default_value = testbed::lease::DEFAULT_ADDR)]
        card: String,
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar4: String,
        #[arg(long, default_value_t = 4)]
        sdp_queues: u16,
        #[arg(long, default_value_t = 256)]
        sdp_rx_desc: u16,
        #[arg(long, default_value_t = 2048)]
        sdp_tx_desc: u16,
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        sdp_compact_pool: bool,
        /// Explicit host SDP mbuf pool size (0 = auto: `n_q * (rx_desc + 512) + 1023` when compact).
        #[arg(long, default_value_t = 0)]
        sdp_pool_size: u32,
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        sdp_zero_copy: bool,
        #[arg(long, default_value_t = 1024)]
        tx_pool_slots: usize,
        #[arg(long, default_value_t = 0)]
        credit_lines: u64,
        #[arg(long, default_value_t = 8)]
        credit_hyst: u64,
        #[arg(long, default_value_t = BAR4_CELL_STRIDE_DEFAULT)]
        bar4_stride: usize,
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        pmu: bool,
        #[arg(long)]
        app_work: bool,
        /// Enable per-request rdtsc timing of upstream Memcached execution (off by default).
        #[arg(long)]
        app_timing: bool,
        /// Issue MOVDIR64B immediately per request instead of batching at drain boundary (ablation).
        #[arg(long)]
        no_batch_mmio: bool,
        #[arg(long, default_value_t = 0)]
        idle_cal_ms: u64,
        #[arg(long, default_value = "127.0.0.1:7541")]
        ctrl: String,
        /// Number of host worker cores on Socket 1 (1, 2, or 4; pinned to base_cpu..base_cpu+H-1).
        #[arg(long, default_value_t = 1)]
        host_cores: usize,
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

    // -------------------------------------------------------------------------
    // 100% Non-Multiplexed Host Core + Socket-1 CHA/IIO/IMC PMU
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
            s1.cpu, cyc, ref_cyc, ins, ghz, ipc, cyc_per_req, ins_per_req,
            pf, llc_l, llc_m, dtlb_m, wcilf, io_hit, io_miss, ddio_hit_pct,
            mesh_ghz, iio_cwr, iio_iwr, iio_ird, imc_r, imc_w, imc_total_bytes, imc_gbps, imc_b_per_req,
            min_pct
        )
    }

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
        set_rpcs: u64,
        small_out: u64,
        large_out: u64,
        mc_misses: u64,
        mc_errors: u64,
        bad: u64,
        torn: u64,
        wc_split_retries: u64,
        tx_drops: u64,
        sdp_imissed: u64,
        sdp_ierrors: u64,
        sdp_oerrors: u64,
        sdp_rx_nombuf: u64,
        bar4_lines_written: u64,
        credit_waits: u64,
        empty_polls: u64,
        busy_polls: u64,
        busy_tsc: u64,
        mc_exec_tsc: u64,
        app_work_units: u64,
        page_faults: u64,
        mc_stats: McStats,
        pmu_json: String,
    }

    impl HostStats {
        fn reset(&mut self, now_tsc: u64) {
            *self = HostStats::default();
            self.t_reset_tsc = now_tsc;
            self.pmu_json = "null".to_string();
        }

        fn add_from(&mut self, o: &HostStats) {
            self.rx_rpcs += o.rx_rpcs;
            self.tx_rpcs += o.tx_rpcs;
            self.get_rpcs += o.get_rpcs;
            self.set_rpcs += o.set_rpcs;
            self.small_out += o.small_out;
            self.large_out += o.large_out;
            self.mc_misses += o.mc_misses;
            self.mc_errors += o.mc_errors;
            self.bad += o.bad;
            self.torn += o.torn;
            self.wc_split_retries += o.wc_split_retries;
            self.tx_drops += o.tx_drops;
            self.bar4_lines_written += o.bar4_lines_written;
            self.credit_waits += o.credit_waits;
            self.empty_polls += o.empty_polls;
            self.busy_polls += o.busy_polls;
            self.busy_tsc += o.busy_tsc;
            self.mc_exec_tsc += o.mc_exec_tsc;
            self.app_work_units += o.app_work_units;
            self.page_faults += o.page_faults;
        }

        fn to_json(&self, tsc_hz: f64, numa: &str, mc_ver: &str) -> String {
            let rpcs_mps = if self.dt_s > 0.0 { self.tx_rpcs as f64 / self.dt_s / 1e6 } else { 0.0 };
            let app_work_kps = if self.dt_s > 0.0 { self.app_work_units as f64 / self.dt_s / 1e3 } else { 0.0 };
            let busy_ns_per_req = if self.tx_rpcs > 0 && tsc_hz > 0.0 {
                (self.busy_tsc as f64 / tsc_hz) * 1e9 / (self.tx_rpcs as f64)
            } else {
                0.0
            };
            let mc_exec_ns_per_req = if self.tx_rpcs > 0 && tsc_hz > 0.0 {
                (self.mc_exec_tsc as f64 / tsc_hz) * 1e9 / (self.tx_rpcs as f64)
            } else {
                0.0
            };
            let ms = &self.mc_stats;
            format!(
                "{{\"mc_version\":\"{}\",\"dt_s\":{:.6},\"rx_rpcs\":{},\"tx_rpcs\":{},\"rpcs_mps\":{:.6},\"get_rpcs\":{},\"set_rpcs\":{},\"small_out\":{},\"large_out\":{},\"mc_misses\":{},\"mc_errors\":{},\"bad\":{},\"torn\":{},\"wc_split_retries\":{},\"tx_drops\":{},\"sdp_imissed\":{},\"sdp_ierrors\":{},\"sdp_oerrors\":{},\"sdp_rx_nombuf\":{},\"bar4_lines\":{},\"credit_waits\":{},\"empty_polls\":{},\"busy_polls\":{},\"busy_ns_per_req\":{:.1},\"mc_exec_ns_per_req\":{:.1},\"app_work_units\":{},\"app_work_kps\":{:.3},\"page_faults\":{},\"numa\":\"{}\",\"mc_stats\":{{\"get_cmds\":{},\"get_hits\":{},\"get_misses\":{},\"set_cmds\":{},\"evictions\":{},\"reclaimed\":{},\"curr_items\":{},\"total_items\":{},\"bytes_stored\":{},\"bytes_read\":{},\"bytes_written\":{},\"hashpower\":{},\"maxbytes_mib\":{}}},\"pmu\":{}}}",
                mc_ver,
                self.dt_s,
                self.rx_rpcs,
                self.tx_rpcs,
                rpcs_mps,
                self.get_rpcs,
                self.set_rpcs,
                self.small_out,
                self.large_out,
                self.mc_misses,
                self.mc_errors,
                self.bad,
                self.torn,
                self.wc_split_retries,
                self.tx_drops,
                self.sdp_imissed,
                self.sdp_ierrors,
                self.sdp_oerrors,
                self.sdp_rx_nombuf,
                self.bar4_lines_written,
                self.credit_waits,
                self.empty_polls,
                self.busy_polls,
                busy_ns_per_req,
                mc_exec_ns_per_req,
                self.app_work_units,
                app_work_kps,
                self.page_faults,
                numa,
                ms.get_cmds,
                ms.get_hits,
                ms.get_misses,
                ms.set_cmds,
                ms.evictions,
                ms.reclaimed,
                ms.curr_items,
                ms.total_items,
                ms.bytes_stored,
                ms.bytes_read,
                ms.bytes_written,
                ms.hashpower,
                ms.maxbytes_mib,
                self.pmu_json
            )
        }
    }

    struct Coord {
        quit: AtomicBool,
        cmd_word: AtomicU64,
        done_gen: AtomicU64,
        cmd_lock: Mutex<()>,
        reset_profile: Mutex<Option<McProfile>>,
        snap: Mutex<HostStats>,
        worker_done: std::sync::atomic::AtomicUsize,
        worker_snaps: Mutex<Vec<HostStats>>,
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
                worker_done: std::sync::atomic::AtomicUsize::new(0),
                worker_snaps: Mutex::new(Vec::new()),
            }
        }

        fn call(&self, cmd: u64, profile: Option<McProfile>) -> Option<HostStats> {
            let _g = self.cmd_lock.lock().unwrap();
            *self.reset_profile.lock().unwrap() = profile;
            let prev = self.cmd_word.load(Ordering::Relaxed) >> 4;
            let next_gen = prev + 1;
            self.cmd_word.store((next_gen << 4) | (cmd & 0xf), Ordering::Release);
            let t0 = Instant::now();
            while self.done_gen.load(Ordering::Acquire) < next_gen {
                if self.quit.load(Ordering::Relaxed) || t0.elapsed() > Duration::from_secs(10) {
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

        // Initialize 100% unmodified upstream Memcached 1.6.45 on NUMA-1 (background threads pinned to cpu44)
        let mc_rc = unsafe {
            mrv206_mc_init_mt(args.mc_mem_mb * 1024 * 1024, args.mc_hashpower, CTRL_CPU as i32, args.host_cores.max(1) as i32)
        };
        if mc_rc != 0 {
            return Err(format!("mrv206_mc_init failed rc={mc_rc}").into());
        }
        let mc_ver = unsafe { CStr::from_ptr(mrv206_mc_version()).to_string_lossy().into_owned() };
        // Pre-warm all slab classes (`3840, 1024, 128, 8`) + hash table before ready so
        // hot-path SET/GET mutations take strictly zero minor page faults.
        for prof in [McProfile::V3840, McProfile::V1024, McProfile::V128, McProfile::V8, args.mc_profile] {
            populate_memcached(prof, args.num_keys)?;
        }
        prefault_stack();

        // Retained 128 MiB TX pool on NUMA-1 HugePages for DirectPush large (> 192 B) DMA replies
        let tx_pool = Memzone::reserve_contiguous(&eal, "m206_tx_pool", TX_POOL_BYTES, socket, 2 * 1024 * 1024)?;
        let tx_pool_ptr = tx_pool.as_ptr();
        let tx_pool_iova = tx_pool.iova();
        unsafe { std::ptr::write_bytes(tx_pool_ptr, 0, TX_POOL_BYTES) };

        // Configure host VF0 `0000:99:02.0`.
        // octeon_ep TX reclaim triggers at OTX_EP_MAX_INSTR = 256 per queue, so 4Q zero-copy
        // can pin up to 4*256 RX + 4*(256+32) unreclaimed TX + 256 lcore cache > 2047 mbufs.
        // Auto compact pool uses `n_q * (rx_desc + 512) + 1023` (4095 for 4Q x 256) to prevent OQ rx_nombuf starvation.
        let n_sdp_q = if args.mode == Mode::Sdp { args.sdp_queues } else { 1 };
        let rx_pool_sz = if args.sdp_pool_size > 0 {
            args.sdp_pool_size
        } else if args.mode == Mode::Sdp && args.sdp_compact_pool {
            ((n_sdp_q as u32) * (args.sdp_rx_desc as u32 + 512) + 1023).max(4095)
        } else {
            8192
        };
        let rx_desc = if args.mode == Mode::Sdp { args.sdp_rx_desc } else { 2048 };
        let tx_desc = if args.mode == Mode::Sdp { args.sdp_tx_desc } else { 2048 };
        let rx_pool = Mempool::new_with_buf_size(&eal, "m206_rx_pool", rx_pool_sz, SDP_MBUF_BUF_SIZE, socket)?;
        let tx_mbuf_pool_opt = if args.mode == Mode::Sdp && !args.sdp_zero_copy {
            Some(Mempool::new_with_buf_size(&eal, "m206_tx_mbuf_pool", 2047, SDP_MBUF_BUF_SIZE, socket)?)
        } else {
            None
        };
        let mut port = Port::start_with_opts_rss(&eal, 0, &rx_pool, n_sdp_q, rx_desc, tx_desc, Some(SDP_MTU), false)?;
        let _ = port.promiscuous();
        let (eth_stats, mut all_qps) = port.split_queues();

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
            let rz = Memzone::reserve_contiguous(&eal, "m206_rx_zone", RX_ZONE_BYTES, socket, 8 * 1024 * 1024)?;
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
            let mc_ver_srv = mc_ver.clone();
            scope.spawn(move || {
                pin_thread(CTRL_CPU);
                prefault_stack();
                let mut rx_buf = [0u8; 256];
                let mut reply_buf = String::with_capacity(4096);
                let mut last_tick = Instant::now();
                while !coord_srv.quit.load(Ordering::Relaxed) {
                    if last_tick.elapsed() >= Duration::from_millis(500) {
                        unsafe { mrv206_mc_tick_clock() };
                        last_tick = Instant::now();
                    }
                    match listener.accept() {
                        Ok((mut stream, _)) => {
                            let _ = stream.set_nodelay(true);
                            let _ = stream.set_read_timeout(Some(Duration::from_secs(10)));
                            let mut n_read = 0usize;
                            while n_read < rx_buf.len() {
                                match stream.read(&mut rx_buf[n_read..]) {
                                    Ok(0) => break,
                                    Ok(k) => {
                                        n_read += k;
                                        if rx_buf[..n_read].contains(&b'\n') {
                                            break;
                                        }
                                    }
                                    Err(_) => break,
                                }
                            }
                            if n_read > 0 {
                                let line = std::str::from_utf8(&rx_buf[..n_read]).unwrap_or("");
                                let mut it = line.trim().split_whitespace();
                                let cmd = it.next().unwrap_or("");
                                let arg1 = it.next();
                                reply_buf.clear();
                                match cmd {
                                    "PING" => reply_buf.push_str("{\"ok\":true}"),
                                    "RESET" => {
                                        let prof = arg1.and_then(McProfile::from_str_opt);
                                        let _ = coord_srv.call(1, prof);
                                        reply_buf.push_str("{\"ok\":true}");
                                    }
                                    "STATS" => match coord_srv.call(2, None) {
                                        Some(st) => reply_buf.push_str(&st.to_json(tsc_hz, &numa_srv, &mc_ver_srv)),
                                        None => reply_buf.push_str("{\"err\":\"timeout\"}"),
                                    },
                                    "QUIT" => {
                                        match coord_srv.call(3, None) {
                                            Some(st) => reply_buf.push_str(&st.to_json(tsc_hz, &numa_srv, &mc_ver_srv)),
                                            None => reply_buf.push_str("{\"ok\":true}"),
                                        }
                                        coord_srv.quit.store(true, Ordering::Relaxed);
                                    }
                                    _ => reply_buf.push_str("{\"err\":\"unknown\"}"),
                                }
                                reply_buf.push('\n');
                                let _ = stream.write_all(reply_buf.as_bytes());
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

            // Warm stats/PMU/JSON paths before publishing READY so first STATS call incurs 0 page faults.
            let mut stats = HostStats::default();
            let now0 = rdtsc_after_loads();
            stats.reset(now0);
            let mut pmu_s0 = host_pmu.as_ref().map(|p| p.snapshot());
            unsafe { mrv206_mc_get_stats(&mut stats.mc_stats) };
            if let (Some(p), Some(s0)) = (host_pmu.as_ref(), pmu_s0.as_ref()) {
                let s1 = p.snapshot();
                stats.pmu_json = format_host_pmu(s0, &s1, 0.001, 1);
            }
            std::hint::black_box(stats.to_json(tsc_hz, &numa_str, &mc_ver));
            let _ = eth_stats.stats_reset();
            let host_cores = args.host_cores.max(1).min(n_card_cores.max(1));
            let bar4_base_usize = bar4_slot.as_ref().map_or(0usize, |s| s.as_ptr() as usize);
            let rx_zone_usize = rx_zone_ptr as usize;
            let tx_pool_usize = tx_pool_ptr as usize;
            println!(
                "MRV206_HOST READY mode={:?} mc_version={} cpu={} host_cores={} mc_profile={:?} num_keys={} mc_mem_mb={} n_card_cores={} sdp_queues={} sdp_rx_desc={} sdp_pool_sz={} numa={} pmu={}",
                args.mode,
                mc_ver,
                cpu,
                host_cores,
                args.mc_profile,
                args.num_keys,
                args.mc_mem_mb,
                n_card_cores,
                n_sdp_q,
                rx_desc,
                rx_pool_sz,
                numa_str,
                host_pmu.is_some()
            );
            let _ = std::io::stdout().flush();

            let mut qps_per_worker: Vec<Vec<dpdk::QueuePair<'_, '_>>> = (0..host_cores).map(|_| Vec::new()).collect();
            for (qi, qp) in all_qps.drain(..).enumerate() {
                qps_per_worker[qi % host_cores].push(qp);
            }

            let run_worker = |worker_idx: usize,
                              worker_cpu: usize,
                              c_start: usize,
                              c_end: usize,
                              mut my_qps: Vec<_>|
             -> HostStats {
                if worker_idx > 0 {
                    unsafe {
                        let mut set: libc::cpu_set_t = std::mem::zeroed();
                        libc::CPU_ZERO(&mut set);
                        libc::CPU_SET(worker_cpu, &mut set);
                        libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set);
                    }
                }
                let mc_tid = worker_idx as i32;
                let rx_zone_ptr = rx_zone_usize as *mut u8;
                let tx_pool_ptr = tx_pool_usize as *mut u8;
                let mut stats = HostStats::default();
                let now0 = rdtsc_after_loads();
                stats.reset(now0);
                let mut pmu_t0 = Instant::now();
                let mut pmu_s0 = if worker_idx == 0 { host_pmu.as_ref().map(|p| p.snapshot()) } else { None };
                let mut flt_base = read_minor_faults();

                let mut handled_gen: u64 = 0;
                let mut work_state = 0xdead_beef_cafe_babeu64;
                let mut tx_slot_per_core = [0usize; MAX_CARD_CORES];
                let tx_slots_per_core = (args.tx_pool_slots / n_card_cores.max(1)).clamp(256, TX_POOL_SLOTS / MAX_CARD_CORES);
                let mut rx_burst = Burst::<MAX_DRAIN>::new();
                let mut tx_burst = Burst::<MAX_DRAIN>::new();

                let mut rx_seq = [1u64; MAX_CARD_CORES];
                let mut bar_pos = [1u64; MAX_CARD_CORES];
                let mut bar_consumed = [0u64; MAX_CARD_CORES];
                let mut rr_core: usize = c_start;
                let card_credit_cap = if args.credit_lines > 0 {
                    (args.credit_lines / (n_card_cores as u64)).max(4)
                } else {
                    0
                };

                let mut pending_bar4_cells = [McCell::default(); MAX_DRAIN * 4];
                let mut pending_bar4_pos = [0u64; MAX_DRAIN * 4];
                let mut pending_bar4_core = [0usize; MAX_DRAIN * 4];
                let mut extra_cells = [McCell::default(); 4];
                let mut out_cells = [McCell::default(); 4];
                let mut push_req_buf = [0u8; 256];
                let mut mc_reply = McReply::default();
                let mut iter: u64 = 0;
                let app_timing = args.app_timing;
                let batch_mmio = !args.no_batch_mmio;

                while !coord.quit.load(Ordering::Relaxed) {
                    iter = iter.wrapping_add(1);
                    if (iter & 0x3ff) == 0 {
                        if worker_idx == 0 {
                            if let Some(l) = lease_opt.as_ref() {
                                if l.check().is_err() {
                                    coord.quit.store(true, Ordering::Release);
                                    break;
                                }
                            }
                        }
                        let word = coord.cmd_word.load(Ordering::Acquire);
                        let g = word >> 4;
                        if g > handled_gen {
                            let cmd = word & 0xf;
                            let now = rdtsc_after_loads();
                            if cmd == 1 {
                                stats.reset(now);
                                pmu_t0 = Instant::now();
                                flt_base = read_minor_faults();
                            } else {
                                stats.dt_s = now.saturating_sub(stats.t_reset_tsc) as f64 / tsc_hz;
                                stats.page_faults = read_minor_faults().saturating_sub(flt_base);
                            }
                            handled_gen = g;
                            if worker_idx > 0 {
                                if cmd != 1 {
                                    coord.worker_snaps.lock().unwrap().push(stats.clone());
                                }
                                coord.worker_done.fetch_add(1, Ordering::Release);
                                while coord.done_gen.load(Ordering::Acquire) < g {
                                    std::hint::spin_loop();
                                }
                            } else {
                                while coord.worker_done.load(Ordering::Acquire) < host_cores - 1 {
                                    std::hint::spin_loop();
                                }
                                if cmd == 1 {
                                    if let Some(prof) = coord.reset_profile.lock().unwrap().take() {
                                        if let Err(e) = populate_memcached(prof, args.num_keys) {
                                            eprintln!("MRV206_HOST populate error: {e}");
                                        }
                                    } else {
                                        unsafe { mrv206_mc_reset_stats() };
                                    }
                                    let _ = eth_stats.stats_reset();
                                    pmu_s0 = host_pmu.as_ref().map(|p| p.snapshot());
                                } else {
                                    let wall_s = pmu_t0.elapsed().as_secs_f64();
                                    let mut merged = stats.clone();
                                    {
                                        let mut ws = coord.worker_snaps.lock().unwrap();
                                        for w in ws.drain(..) {
                                            merged.add_from(&w);
                                        }
                                    }
                                    if let Ok(pst) = eth_stats.stats() {
                                        merged.sdp_imissed = pst.imissed;
                                        merged.sdp_ierrors = pst.ierrors;
                                        merged.sdp_oerrors = pst.oerrors;
                                        merged.sdp_rx_nombuf = pst.rx_nombuf;
                                    }
                                    unsafe { mrv206_mc_get_stats(&mut merged.mc_stats) };
                                    merged.pmu_json = match (host_pmu.as_ref(), pmu_s0.as_ref()) {
                                        (Some(p), Some(s0)) => {
                                            let s1 = p.snapshot();
                                            format_host_pmu(s0, &s1, wall_s, merged.tx_rpcs)
                                        }
                                        _ => "null".to_string(),
                                    };
                                    *coord.snap.lock().unwrap() = merged;
                                }
                                coord.worker_done.store(0, Ordering::Release);
                                coord.done_gen.store(g, Ordering::Release);
                            }
                            if cmd == 3 {
                                break;
                            }
                        }
                    }

                match args.mode {
                    Mode::Sdp => {
                        let mut got_any = false;
                        for qp in my_qps.iter_mut() {
                            let n = dpdk::QueuePair::rx(qp, &mut rx_burst);
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
                                let (rpc_seq, mc_len_u16, _fi, _fc, _op) = unpack_wire_meta(w0);
                                let mc_len = mc_len_u16 as usize;
                                let req_total = wire_total_bytes(mc_len);
                                if rpc_seq == 0
                                    || mc_len < 32
                                    || mc_len > MAX_MC_BYTES
                                    || SDP_HDR_LEN + req_total > flen
                                {
                                    stats.bad += 1;
                                    return;
                                }
                                let tail_w = u64::from_le_bytes(
                                    pkt.data()[SDP_HDR_LEN + req_total - 8..SDP_HDR_LEN + req_total]
                                        .try_into()
                                        .unwrap(),
                                );
                                if tail_w != w0 {
                                    stats.torn += 1;
                                    return;
                                }

                                stats.rx_rpcs += 1;

                                // Execute 100% unmodified upstream Memcached Binary UDP handler directly on RX mbuf!
                                let mc_req_ptr = unsafe { pkt.data().as_ptr().add(SDP_HDR_LEN + 8) };
                                let t_mc0 = if app_timing { rdtsc() } else { 0 };
                                let rc = unsafe { mrv206_mc_exec_tid(mc_tid, mc_req_ptr, mc_len, &mut mc_reply) };
                                if app_timing {
                                    stats.mc_exec_tsc += rdtsc().wrapping_sub(t_mc0);
                                }
                                if rc != 0 {
                                    stats.mc_errors += 1;
                                    unsafe { mrv206_mc_release_tid(mc_tid) };
                                    return;
                                }

                                if mc_reply.opcode == MC_CMD_GET {
                                    stats.get_rpcs += 1;
                                } else if mc_reply.opcode == MC_CMD_SET {
                                    stats.set_rpcs += 1;
                                }
                                if mc_reply.status == MC_STATUS_ENOENT {
                                    stats.mc_misses += 1;
                                } else if mc_reply.status != MC_STATUS_OK {
                                    stats.mc_errors += 1;
                                }

                                let resp_mc_len = mc_reply.total_len as usize;
                                let resp_total = wire_total_bytes(resp_mc_len);
                                let target_flen = SDP_HDR_LEN + resp_total;
                                let Some(mut m) = prep_sdp_mbuf(
                                    pkt,
                                    flen,
                                    target_flen,
                                    args.sdp_zero_copy,
                                    tx_mbuf_pool_opt.as_ref(),
                                ) else {
                                    unsafe { mrv206_mc_release_tid(mc_tid) };
                                    stats.tx_drops += 1;
                                    return;
                                };

                                let data = m.data_mut();
                                let ip_tot = (20 + 8 + 6 + resp_total) as u16;
                                let udp_len = (8 + 6 + resp_total) as u16;
                                data[16..18].copy_from_slice(&ip_tot.to_be_bytes());
                                data[38..40].copy_from_slice(&udp_len.to_be_bytes());

                                let out_w0 = pack_wire_meta(
                                    rpc_seq,
                                    resp_mc_len as u16,
                                    0,
                                    wire_frag_cnt(resp_mc_len),
                                    OP_NET,
                                );
                                data[SDP_HDR_LEN..SDP_HDR_LEN + 8].copy_from_slice(&out_w0.to_le_bytes());
                                copy_mc_reply_contiguous(
                                    &mc_reply,
                                    &mut data[SDP_HDR_LEN + 8..SDP_HDR_LEN + 8 + resp_mc_len],
                                );
                                data[target_flen - 8..target_flen].copy_from_slice(&out_w0.to_le_bytes());
                                unsafe { mrv206_mc_release_tid(mc_tid) };

                                if let Err(p_back) = tx_burst.push(m) {
                                    while !tx_burst.is_empty() {
                                        let _ = qp.tx(&mut tx_burst);
                                    }
                                    let _ = tx_burst.push(p_back);
                                }
                                stats.tx_rpcs += 1;
                                if resp_mc_len <= OP_PUSH_MAX_MC_BYTES {
                                    stats.small_out += 1;
                                } else {
                                    stats.large_out += 1;
                                }
                            });
                            while !tx_burst.is_empty() {
                                if qp.tx(&mut tx_burst) == 0 {
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
                        let bar_base = bar4_base_usize as *mut u8;
                        let mut got = 0usize;
                        let mut n_bar4_pending = 0usize;
                        let mut need_sfence = false;
                        let mut blocked_credit = false;
                        let t_busy0 = rdtsc();

                        for _ in 0..MAX_DRAIN {
                            let mut found_any = false;
                            let mut off_c = rr_core;
                            for _ in c_start..c_end {
                                let c = off_c;
                                let rseq = rx_seq[c];
                                let rx_ptr = unsafe { rx_zone_ptr.add(rx_ring_offset(c, rseq)) };
                                let cell = match unsafe { McCell::poll_seq(rx_ptr, rseq) } {
                                    CellPoll::Empty => {
                                        off_c += 1;
                                        if off_c == c_end {
                                            off_c = c_start;
                                        }
                                        continue;
                                    }
                                    CellPoll::Torn => {
                                        stats.wc_split_retries += 1;
                                        let t_wait = rdtsc();
                                        let wait_limit = (tsc_hz / 1_000.0) as u64;
                                        let mut resolved = None;
                                        loop {
                                            match unsafe { McCell::poll_seq(rx_ptr, rseq) } {
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
                                                if off_c == c_end {
                                                    off_c = c_start;
                                                }
                                                continue;
                                            }
                                        }
                                    }
                                    CellPoll::Ready(cl) => cl,
                                };

                                let (_rseq_got, mc_len_u16, aux, tid, op) = unpack_cell_meta(cell.w[0]);
                                let mc_len = mc_len_u16 as usize;

                                // Optional credit gate
                                if card_credit_cap > 0 {
                                    let need_lines = if mc_len <= OP_PUSH_MAX_MC_BYTES { 4u64 } else { 1u64 };
                                    let written = bar_pos[c] - 1;
                                    if written.saturating_sub(bar_consumed[c]) + need_lines > card_credit_cap {
                                        let cptr = unsafe { rx_zone_ptr.add(credit_line_offset(c)) };
                                        if let Some(v) = unsafe { McCell::poll_credit(cptr) } {
                                            if v > bar_consumed[c] {
                                                bar_consumed[c] = v;
                                            }
                                        }
                                        if written.saturating_sub(bar_consumed[c]) + need_lines > card_credit_cap {
                                            blocked_credit = true;
                                            off_c += 1;
                                            if off_c == c_end {
                                                off_c = c_start;
                                            }
                                            continue;
                                        }
                                    }
                                }

                                let (mc_req_ptr, rpc_seq_opt): (*const u8, Option<u64>) = if op == OP_PUSH {
                                    let cells = aux as usize;
                                    if cells == 0 || cells > 4 || mc_len < 32 || mc_len > OP_PUSH_MAX_MC_BYTES {
                                        rx_seq[c] += 1;
                                        stats.bad += 1;
                                        found_any = true;
                                        break;
                                    }
                                    extra_cells[0] = cell;
                                    let mut all_ok = true;
                                    for k in 1..cells {
                                        let cp = unsafe { rx_ptr.add(k * 64) };
                                        match unsafe { McCell::poll_seq(cp, rseq) } {
                                            CellPoll::Ready(ec) => extra_cells[k] = ec,
                                            _ => {
                                                all_ok = false;
                                                break;
                                            }
                                        }
                                    }
                                    if !all_ok {
                                        off_c += 1;
                                        if off_c == c_end {
                                            off_c = c_start;
                                        }
                                        continue;
                                    }
                                    rx_seq[c] += 1;
                                    if cells == 1 {
                                        // Zero-copy straight out of stack Cell 0!
                                        (extra_cells[0].payload_bytes().as_ptr(), None)
                                    } else {
                                        for k in 0..cells {
                                            let off = k * 48;
                                            let take = (mc_len.saturating_sub(off)).min(48);
                                            push_req_buf[off..off + take]
                                                .copy_from_slice(&extra_cells[k].payload_bytes()[..take]);
                                        }
                                        (push_req_buf.as_ptr(), None)
                                    }
                                } else if op == OP_DMA {
                                    rx_seq[c] += 1;
                                    let rpc_seq = cell.w[1];
                                    let lslot = cell.w[2] as usize;
                                    let src_ptr = unsafe { rx_zone_ptr.add(large_rx_offset(c, lslot) + 8) };
                                    (src_ptr, Some(rpc_seq))
                                } else {
                                    rx_seq[c] += 1;
                                    stats.bad += 1;
                                    found_any = true;
                                    break;
                                };

                                got += 1;
                                found_any = true;
                                rr_core = if c + 1 == c_end { c_start } else { c + 1 };
                                stats.rx_rpcs += 1;

                                // Execute 100% unmodified upstream Memcached Binary UDP handler!
                                let t_mc0 = if app_timing { rdtsc() } else { 0 };
                                let rc = unsafe { mrv206_mc_exec_tid(mc_tid, mc_req_ptr, mc_len, &mut mc_reply) };
                                if app_timing {
                                    stats.mc_exec_tsc += rdtsc().wrapping_sub(t_mc0);
                                }
                                if rc != 0 {
                                    stats.mc_errors += 1;
                                    unsafe { mrv206_mc_release_tid(mc_tid) };
                                    break;
                                }

                                if mc_reply.opcode == MC_CMD_GET {
                                    stats.get_rpcs += 1;
                                } else if mc_reply.opcode == MC_CMD_SET {
                                    stats.set_rpcs += 1;
                                }
                                if mc_reply.status == MC_STATUS_ENOENT {
                                    stats.mc_misses += 1;
                                } else if mc_reply.status != MC_STATUS_OK {
                                    stats.mc_errors += 1;
                                }

                                let resp_mc_len = mc_reply.total_len as usize;
                                if resp_mc_len <= OP_PUSH_MAX_MC_BYTES {
                                    let cells = if batch_mmio {
                                        let n = pack_mc_reply_into_cells(
                                            &mc_reply,
                                            bar_pos[c],
                                            tid,
                                            &mut pending_bar4_cells[n_bar4_pending..],
                                        );
                                        unsafe { mrv206_mc_release_tid(mc_tid) };
                                        for k in 0..n {
                                            pending_bar4_pos[n_bar4_pending + k] = bar_pos[c] + k as u64;
                                            pending_bar4_core[n_bar4_pending + k] = c;
                                        }
                                        n_bar4_pending += n;
                                        n
                                    } else {
                                        let n = pack_mc_reply_into_cells(&mc_reply, bar_pos[c], tid, &mut out_cells);
                                        unsafe { mrv206_mc_release_tid(mc_tid) };
                                        for k in 0..n {
                                            let my_pos = bar_pos[c] + k as u64;
                                            unsafe {
                                                movdir64b(
                                                    bar_base.add(bar4_cell_offset(c, my_pos, args.bar4_stride)),
                                                    &out_cells[k],
                                                );
                                            }
                                        }
                                        n
                                    };
                                    bar_pos[c] += cells as u64;
                                    stats.bar4_lines_written += cells as u64;
                                    stats.tx_rpcs += 1;
                                    stats.small_out += 1;
                                } else {
                                    // Large reply (> 192 B): write standard Memcached UDP datagram(s) to
                                    // LLC-hot tx_pool slot and queue DPI OP_DMA
                                    let pslot = c * tx_slots_per_core + (tx_slot_per_core[c] % tx_slots_per_core);
                                    tx_slot_per_core[c] = tx_slot_per_core[c].wrapping_add(1);
                                    let resp_total = wire_total_bytes(resp_mc_len);
                                    let dst_ptr = unsafe { tx_pool_ptr.add(pslot * TX_POOL_SLOT_BYTES) };
                                    let dst_slice = unsafe { std::slice::from_raw_parts_mut(dst_ptr, resp_total) };
                                    copy_mc_reply_contiguous(&mc_reply, &mut dst_slice[8..8 + resp_mc_len]);
                                    unsafe { mrv206_mc_release_tid(mc_tid) };

                                    let rpc_seq = match rpc_seq_opt {
                                        Some(s) => s,
                                        None => {
                                            let opq = mrv206::mc_udp_opaque(&dst_slice[8..8 + resp_mc_len]);
                                            let lseq = (opq & 0x1fff_ffff) as u64;
                                            ((tid as u64) << 34) | lseq.max(1)
                                        }
                                    };
                                    let rfrag = wire_frag_cnt(resp_mc_len);
                                    let out_w0 = pack_wire_meta(rpc_seq, resp_mc_len as u16, 0, rfrag, OP_NET);
                                    dst_slice[0..8].copy_from_slice(&out_w0.to_le_bytes());
                                    dst_slice[resp_total - 8..resp_total].copy_from_slice(&out_w0.to_le_bytes());
                                    let my_pos = bar_pos[c];
                                    bar_pos[c] += 1;
                                    let mut desc = McCell::default();
                                    desc.w[0] = pack_cell_meta(my_pos, resp_mc_len as u16, rfrag, tid, OP_DMA);
                                    desc.w[1] = rpc_seq;
                                    desc.w[2] = tx_pool_iova + (pslot * TX_POOL_SLOT_BYTES) as u64;
                                    desc.w[3] = resp_total as u64;
                                    desc.seal();
                                    pending_bar4_cells[n_bar4_pending] = desc;
                                    pending_bar4_pos[n_bar4_pending] = my_pos;
                                    pending_bar4_core[n_bar4_pending] = c;
                                    n_bar4_pending += 1;
                                    need_sfence = true;
                                    stats.bar4_lines_written += 1;
                                    stats.tx_rpcs += 1;
                                    stats.large_out += 1;
                                }
                                break;
                            }
                            if !found_any {
                                break;
                            }
                        }

                        if n_bar4_pending > 0 {
                            if need_sfence {
                                sfence();
                            }
                            for i in 0..n_bar4_pending {
                                let c = pending_bar4_core[i];
                                unsafe {
                                    movdir64b(
                                        bar_base.add(bar4_cell_offset(c, pending_bar4_pos[i], args.bar4_stride)),
                                        &pending_bar4_cells[i],
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
                let _ = std::hint::black_box(work_state);
                stats
            };

            let lanes_per_worker = (n_card_cores / host_cores).max(1);
            let mut qps_iter = qps_per_worker.into_iter();
            let qps0 = qps_iter.next().unwrap();
            let mut stats = std::thread::scope(|wscope| {
                let mut handles = Vec::new();
                for (h, qps_h) in qps_iter.enumerate() {
                    let worker_idx = h + 1;
                    let worker_cpu = (cpu as usize) + worker_idx;
                    let c_start = worker_idx * lanes_per_worker;
                    let c_end = ((worker_idx + 1) * lanes_per_worker).min(n_card_cores);
                    let rw = &run_worker;
                    handles.push(wscope.spawn(move || {
                        rw(worker_idx, worker_cpu, c_start, c_end, qps_h)
                    }));
                }
                let st = run_worker(0, cpu as usize, 0, lanes_per_worker.min(n_card_cores), qps0);
                for h in handles {
                    let _ = h.join();
                }
                st
            });

            if args.mode == Mode::Direct {
                if let Ok(mut s) = std::net::TcpStream::connect_timeout(
                    &SYNC_ADDR.parse().unwrap(),
                    Duration::from_secs(2),
                ) {
                    let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
                    let _ = writeln!(s, "DETACH");
                    let _ = s.flush();
                    let mut resp_b = [0u8; 64];
                    let _ = s.read(&mut resp_b);
                }
            }
            let now_end = rdtsc_after_loads();
            stats.dt_s = now_end.saturating_sub(stats.t_reset_tsc) as f64 / tsc_hz;
            unsafe { mrv206_mc_get_stats(&mut stats.mc_stats) };
            println!("MRV206_HOST_DONE {}", stats.to_json(tsc_hz, &numa_str, &mc_ver));
            unsafe { mrv206_mc_shutdown() };
            Ok(())
        })?;

        Ok(())
    }
}
