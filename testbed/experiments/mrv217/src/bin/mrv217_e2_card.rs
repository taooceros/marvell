//! On-Path SmartNIC CN106 data-plane engine for MRV-213 (MRV-203 E2: real
//! 100GbE + standard SDP burst protection, concurrent 32 MiB KV tail latency,
//! and full line-rate drain).
//!
//! Pipeline per CN106 worker core `c in 0..n_cores`:
//!   `eth0 (NIX0) RX[c] -> On-Path Elastic FIFO[c] (L3/DDR5) -> sdp1-1 TX[c] -> saturn VF0 RX[c]`
//!
//! Forwarding policies (single-variable control matching E2 Arms 1-5):
//!   - `passthrough` (Arms 1 & 3: large-mono / large-lifo): zero-copy direct TX into host
//!   - `drop`        (Arm 2: small-drop): small SDP ring passive overflow -> drop on `sdp1-1` TX full
//!   - `native-bp`   (Arm 4: zero-BAR4 ablation): absorb into card elastic FIFO when native SDP
//!                   `SLIST_DBELL / WMARK` backpressures `sdp1-1` TX
//!   - `credit-ring` (Arm 5: full design): gate `sdp1-1` TX by host's NO-SFENCE `BAR4` `MOVDIR64B`
//!                   `CreditLine[c]` (`submitted - taken_cached < R_eff`), keeping hardware strictly
//!                   below `WMARK` while absorbing bursts in CN106 48 MiB L3 + 6-ch DDR5
//!
//! Pitfall 3 locality discipline:
//!   - `CompactSlab` elastic FIFO resets `head = tail = 0` whenever it drains to empty so
//!     bursts `<= 32 MiB` reuse the exact same hot region in CN106's 48 MiB L3 (zero card DDR5),
//!     while bursts `> 48 MiB` spill cleanly to CN106's 6-channel DDR5.
//!   - Optional `MbufRing` (`--fifo-mode mbuf`) stores zero-copy NPA mbufs directly for `1500 B`
//!     frames using CN106's hardware LIFO NPA aura.
//!
//! Safety & Telemetry:
//!   - MRV-26 BAR4 slot 15 lease server (`10.10.0.2:7526`), `--restore` cleanup support.
//!   - 100% non-multiplexed CN106 PMU: ARMv8 core PMU + TAD L3 + all 6 DDR5 channels.

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv213_card runs on the aarch64 CN106 card");
    std::process::exit(2);
}

#[cfg(target_arch = "aarch64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "aarch64")]
mod imp {
    use std::collections::VecDeque;
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::size_of;
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, EthStats, Mbuf, Mempool, Memzone, Port, PortStats, QueuePair};
    use testbed::inline::{NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{Bar4Window, restore_defaults};
    use mrv213::{
        CARD_IP, CARD_SYNC_ADDR, CreditLine, CreditPoll, MAX_QUEUES, PktProfile, StreamTracker,
        WireCheck, credit_line_offset, verify_wire_frame,
    };

    const BURST_N: usize = 32;
    const ETH_DESC: u16 = 2048;
    const ETH_MBUF_BUF_SIZE: u16 = 2176;
    const MAX_PROBE_FLOWS: usize = 128;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Policy {
        Idle,
        Passthrough,
        Drop,
        NativeBp,
        CreditRing,
    }

    impl Policy {
        fn as_str(self) -> &'static str {
            match self {
                Policy::Idle => "idle",
                Policy::Passthrough => "passthrough",
                Policy::Drop => "drop",
                Policy::NativeBp => "native-bp",
                Policy::CreditRing => "credit-ring",
            }
        }

        fn from_name(s: &str) -> Option<Self> {
            match s.to_ascii_lowercase().as_str() {
                "idle" => Some(Policy::Idle),
                "passthrough" => Some(Policy::Passthrough),
                "drop" => Some(Policy::Drop),
                "native-bp" | "native_bp" => Some(Policy::NativeBp),
                "credit-ring" | "credit_ring" => Some(Policy::CreditRing),
                _ => None,
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum FifoMode {
        Compact,
        Mbuf,
    }

    impl FifoMode {
        fn as_str(self) -> &'static str {
            match self {
                FifoMode::Compact => "compact",
                FifoMode::Mbuf => "mbuf",
            }
        }

        fn from_name(s: &str) -> Option<Self> {
            match s.to_ascii_lowercase().as_str() {
                "compact" => Some(FifoMode::Compact),
                "mbuf" => Some(FifoMode::Mbuf),
                _ => None,
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, default_value = "0002:02:00.0")]
        eth_dev: String,
        #[arg(long, default_value = "0002:01:00.2")]
        sdp_dev: String,
        /// `sdp1-1` TX descriptor ring depth (e.g. 256 for small-drop, 2048 default).
        #[arg(long, default_value_t = 2048)]
        sdp_tx_desc: u16,
        /// `sdp1-1` RX descriptor ring depth.
        #[arg(long, default_value_t = 512)]
        sdp_rx_desc: u16,
        /// NPA mbuf pool size for `eth0` RX + `sdp1-1` TX.
        /// Increase to e.g. 131072 when running `--fifo-mode mbuf` on 1500 B 128 MiB bursts.
        #[arg(long, default_value_t = 16384)]
        eth_pool_size: u32,
        /// Initial forwarding policy (can be reconfigured live via TCP `CONFIG`).
        #[arg(long, value_enum, default_value_t = Policy::CreditRing)]
        policy: Policy,
        /// Initial packet profile (`64b`, `256b`, `1500b`, `1094b`).
        #[arg(long, default_value = "1500b")]
        profile: String,
        /// Elastic FIFO storage mode (`compact` MADV_HUGEPAGE L3-hot slab or `mbuf` zero-copy NPA).
        #[arg(long, value_enum, default_value_t = FifoMode::Compact)]
        fifo_mode: FifoMode,
        /// Total card elastic FIFO capacity across all cores in bytes (default 192 MiB >= 128 MiB burst).
        #[arg(long, default_value_t = 192 << 20)]
        fifo_cap_bytes: usize,
        /// Initial logical credit window per queue in packets (Pitfall 1: `< rx_desc - WMARK - 64`).
        #[arg(long, default_value_t = 256)]
        r_eff: u64,
        /// Optional byte-granular credit window per queue (0 = packet-only).
        #[arg(long, default_value_t = 0)]
        r_eff_bytes: u64,
        /// MRV-26 BAR4 lease TCP listen address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        /// Control/sync/PMU TCP listen address.
        #[arg(long, default_value = CARD_SYNC_ADDR)]
        sync: String,
        /// Only restore BAR4 slot 15 and PEM0 outbound regions 248-255, then exit.
        #[arg(long)]
        restore: bool,
    }

    // -------------------------------------------------------------------------
    // 100% Non-Multiplexed CN106 PMU: ARMv8 Core + TAD L3 + 6x DDR5 Channels
    // -------------------------------------------------------------------------
    #[repr(C)]
    #[derive(Default)]
    struct PerfEventAttr {
        type_: u32,
        size: u32,
        config: u64,
        sample_period: u64,
        sample_type: u64,
        read_format: u64,
        flags: u64,
        wakeup_events: u32,
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
        reserved2: u16,
        aux_sample_size: u32,
        reserved3: u32,
        sig_data: u64,
        config3: u64,
    }

    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    // 5 ARMv8 PMU events per core (<= 6 GP counters -> 100% non-multiplexed)
    const CORE_EVENTS: &[(&str, u64)] = &[
        ("cpu_cycles", 0x0011),
        ("inst_retired", 0x0008),
        ("l2d_cache_refill", 0x0017),
        ("ll_cache_rd", 0x0036),
        ("ll_cache_miss_rd", 0x0037),
    ];

    const TAD_EVENTS: &[(&str, u64)] = &[
        ("tad_req_msh_in_any", 0x01),
        ("tad_alloc_any", 0x1c),
        ("tad_hit_any", 0x1f),
        ("tad_req_msh_out_dss_wr", 0x0d),
    ];

    const DDR_PMU_NAMES: &[&str] = &[
        "mrvl_ddr_pmu_87e1c0000000",
        "mrvl_ddr_pmu_87e1c1000000",
        "mrvl_ddr_pmu_87e1c2000000",
        "mrvl_ddr_pmu_87e1c3000000",
        "mrvl_ddr_pmu_87e1c4000000",
        "mrvl_ddr_pmu_87e1c5000000",
    ];
    const DDR_READS_CFG: u64 = 0x65;
    const DDR_WRITES_CFG: u64 = 0x64;

    struct PerfCounter {
        name: String,
        fd: RawFd,
    }

    #[derive(Clone, Default)]
    struct CounterSample {
        name: String,
        val: u64,
        en: u64,
        run: u64,
    }

    impl CounterSample {
        fn delta(&self, earlier: &Self) -> Self {
            Self {
                name: self.name.clone(),
                val: self.val.saturating_sub(earlier.val),
                en: self.en.saturating_sub(earlier.en),
                run: self.run.saturating_sub(earlier.run),
            }
        }
        fn pct(&self) -> f64 {
            if self.en == 0 {
                100.0
            } else {
                (self.run as f64 / self.en as f64) * 100.0
            }
        }
    }

    impl PerfCounter {
        fn open(name: String, type_: u32, config: u64, cpu: i32) -> io::Result<Self> {
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
            let c = PerfCounter { name, fd: fd as RawFd };
            let rc = unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) };
            if rc != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> CounterSample {
            let mut buf = [0u64; 3];
            let n = unsafe {
                libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>())
            };
            assert_eq!(n, size_of::<[u64; 3]>() as isize, "short read on {}", self.name);
            CounterSample { name: self.name.clone(), val: buf[0], en: buf[1], run: buf[2] }
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

    fn read_sysfs_u32(path: &str) -> Option<u32> {
        let s = fs::read_to_string(path).ok()?;
        s.trim().split(&[',', '-'][..]).next()?.parse::<u32>().ok()
    }

    struct CardPmu {
        cores: Vec<(i32, Vec<PerfCounter>)>,
        tad: Vec<PerfCounter>,
        ddr_rd: Vec<PerfCounter>,
        ddr_wr: Vec<PerfCounter>,
    }

    #[derive(Clone)]
    struct CardPmuSnap {
        at: Instant,
        cores: Vec<(i32, Vec<CounterSample>)>,
        tad: Vec<CounterSample>,
        ddr_rd: Vec<CounterSample>,
        ddr_wr: Vec<CounterSample>,
    }

    impl CardPmu {
        fn open(cpus: &[i32]) -> Result<Self, String> {
            let mut cores = Vec::with_capacity(cpus.len());
            if let Some(arm_type) = read_sysfs_u32("/sys/bus/event_source/devices/armv8_pmuv3/type") {
                for &cpu in cpus {
                    let mut evs = Vec::with_capacity(CORE_EVENTS.len());
                    for &(name, cfg) in CORE_EVENTS {
                        evs.push(
                            PerfCounter::open(name.to_string(), arm_type, cfg, cpu)
                                .map_err(|e| format!("cpu{cpu} {name}: {e}"))?,
                        );
                    }
                    cores.push((cpu, evs));
                }
            }
            let mut tad = Vec::new();
            if let Some(tad_type) = read_sysfs_u32("/sys/bus/event_source/devices/tad/type") {
                let tad_cpu = read_sysfs_u32("/sys/bus/event_source/devices/tad/cpumask").unwrap_or(0) as i32;
                for &(name, cfg) in TAD_EVENTS {
                    tad.push(
                        PerfCounter::open(name.to_string(), tad_type, cfg, tad_cpu)
                            .map_err(|e| format!("tad {name}: {e}"))?,
                    );
                }
            }
            let mut ddr_rd = Vec::new();
            let mut ddr_wr = Vec::new();
            for &dev in DDR_PMU_NAMES {
                let p_type = format!("/sys/bus/event_source/devices/{dev}/type");
                let p_cpu = format!("/sys/bus/event_source/devices/{dev}/cpumask");
                if let Some(t) = read_sysfs_u32(&p_type) {
                    let c = read_sysfs_u32(&p_cpu).unwrap_or(0) as i32;
                    ddr_rd.push(
                        PerfCounter::open(format!("{dev}:rd"), t, DDR_READS_CFG, c)
                            .map_err(|e| format!("{dev} rd: {e}"))?,
                    );
                    ddr_wr.push(
                        PerfCounter::open(format!("{dev}:wr"), t, DDR_WRITES_CFG, c)
                            .map_err(|e| format!("{dev} wr: {e}"))?,
                    );
                }
            }
            Ok(CardPmu { cores, tad, ddr_rd, ddr_wr })
        }

        fn snap(&self) -> CardPmuSnap {
            CardPmuSnap {
                at: Instant::now(),
                cores: self
                    .cores
                    .iter()
                    .map(|(cpu, evs)| (*cpu, evs.iter().map(PerfCounter::read).collect()))
                    .collect(),
                tad: self.tad.iter().map(PerfCounter::read).collect(),
                ddr_rd: self.ddr_rd.iter().map(PerfCounter::read).collect(),
                ddr_wr: self.ddr_wr.iter().map(PerfCounter::read).collect(),
            }
        }

        fn delta_json(s0: &CardPmuSnap, s1: &CardPmuSnap) -> String {
            let wall_s = s1.at.duration_since(s0.at).as_secs_f64().max(1e-9);
            let mut min_pct = 100.0f64;

            let mut core_strs = Vec::with_capacity(s0.cores.len());
            for ((cpu, ev0), (_, ev1)) in s0.cores.iter().zip(&s1.cores) {
                let mut fields = vec![format!("\"cpu\":{cpu}")];
                for (a, b) in ev0.iter().zip(ev1) {
                    let d = b.delta(a);
                    min_pct = min_pct.min(d.pct());
                    fields.push(format!("\"{}\":{}", d.name, d.val));
                }
                core_strs.push(format!("{{{}}}", fields.join(",")));
            }

            let mut tad_fields = Vec::with_capacity(s0.tad.len());
            for (a, b) in s0.tad.iter().zip(&s1.tad) {
                let d = b.delta(a);
                min_pct = min_pct.min(d.pct());
                tad_fields.push(format!("\"{}\":{{\"val\":{},\"pct\":{:.2}}}", d.name, d.val, d.pct()));
            }

            let mut ddr_rd_total = 0u64;
            let mut ddr_wr_total = 0u64;
            let mut ch_rd = Vec::with_capacity(s0.ddr_rd.len());
            let mut ch_wr = Vec::with_capacity(s0.ddr_wr.len());
            for (a, b) in s0.ddr_rd.iter().zip(&s1.ddr_rd) {
                let d = b.delta(a);
                min_pct = min_pct.min(d.pct());
                ddr_rd_total += d.val;
                ch_rd.push(d.val.to_string());
            }
            for (a, b) in s0.ddr_wr.iter().zip(&s1.ddr_wr) {
                let d = b.delta(a);
                min_pct = min_pct.min(d.pct());
                ddr_wr_total += d.val;
                ch_wr.push(d.val.to_string());
            }
            // CN106 DDR5 PMU counts 64-byte cache-line read/write transactions per channel.
            let ddr_rd_mib = (ddr_rd_total as f64) * 64.0 / (1024.0 * 1024.0);
            let ddr_wr_mib = (ddr_wr_total as f64) * 64.0 / (1024.0 * 1024.0);

            format!(
                "{{\"wall_s\":{wall_s:.6},\"min_pct\":{min_pct:.2},\
                 \"ddr_rd_lines\":{ddr_rd_total},\"ddr_wr_lines\":{ddr_wr_total},\
                 \"ddr_rd_mib\":{ddr_rd_mib:.4},\"ddr_wr_mib\":{ddr_wr_mib:.4},\
                 \"ddr_ch_rd\":[{}],\"ddr_ch_wr\":[{}],\
                 \"tad\":{{{}}},\"cores\":[{}]}}",
                ch_rd.join(","),
                ch_wr.join(","),
                tad_fields.join(","),
                core_strs.join(","),
            )
        }
    }

    // -------------------------------------------------------------------------
    // On-Path Elastic FIFO (CompactSlab with L3 hot-zone reset + MbufRing)
    // -------------------------------------------------------------------------
    struct CompactSlab {
        ptr: *mut u8,
        mmap_len: usize,
        stride: usize,
        cap_pkts: usize,
        lens: Vec<u16>,
        head: usize,
        len: usize,
    }

    impl CompactSlab {
        fn new(cap_bytes: usize, stride: usize) -> Result<Self, Box<dyn Error>> {
            let stride = stride.max(64);
            let cap_pkts = (cap_bytes / stride).max(1024);
            let raw_bytes = cap_pkts * stride;
            let mmap_len = raw_bytes.div_ceil(2 << 20) * (2 << 20);
            let p = unsafe {
                libc::mmap(
                    ptr::null_mut(),
                    mmap_len,
                    libc::PROT_READ | libc::PROT_WRITE,
                    libc::MAP_PRIVATE | libc::MAP_ANONYMOUS,
                    -1,
                    0,
                )
            };
            if p == libc::MAP_FAILED {
                return Err(format!("mmap({} MiB) failed: {}", mmap_len >> 20, io::Error::last_os_error()).into());
            }
            unsafe {
                libc::madvise(p, mmap_len, libc::MADV_HUGEPAGE);
                // Pre-fault all pages so zero page faults happen during burst arrival
                let b = p.cast::<u8>();
                let mut off = 0usize;
                while off < mmap_len {
                    b.add(off).write_volatile(0);
                    off += 4096;
                }
            }
            Ok(CompactSlab {
                ptr: p.cast::<u8>(),
                mmap_len,
                stride,
                cap_pkts,
                lens: vec![0u16; cap_pkts],
                head: 0,
                len: 0,
            })
        }

        fn reconfigure(&mut self, stride: usize) {
            let stride = stride.max(64);
            self.stride = stride;
            self.cap_pkts = (self.mmap_len / stride).max(1024);
            self.lens.resize(self.cap_pkts, 0);
            self.head = 0;
            self.len = 0;
        }

        #[inline(always)]
        fn clear(&mut self) {
            self.head = 0;
            self.len = 0;
        }

        #[inline(always)]
        fn len(&self) -> usize {
            self.len
        }

        #[inline(always)]
        fn push_slice(&mut self, data: &[u8]) -> bool {
            if self.len >= self.cap_pkts || data.len() > self.stride {
                return false;
            }
            let tail = self.head + self.len;
            let idx = if tail < self.cap_pkts { tail } else { tail - self.cap_pkts };
            unsafe {
                ptr::copy_nonoverlapping(data.as_ptr(), self.ptr.add(idx * self.stride), data.len());
            }
            self.lens[idx] = data.len() as u16;
            self.len += 1;
            true
        }

        #[inline(always)]
        fn peek_at(&self, offset: usize) -> &[u8] {
            debug_assert!(offset < self.len);
            let pos = self.head + offset;
            let idx = if pos < self.cap_pkts { pos } else { pos - self.cap_pkts };
            let n = self.lens[idx] as usize;
            unsafe { std::slice::from_raw_parts(self.ptr.add(idx * self.stride), n) }
        }

        #[inline(always)]
        fn advance_head(&mut self, count: usize) {
            debug_assert!(count <= self.len);
            self.len -= count;
            if self.len == 0 {
                // Pitfall 3: reset to index 0 on drain-to-empty so consecutive bursts
                // <= 32 MiB always hit the same hot region of CN106's 48 MiB L3!
                self.head = 0;
            } else {
                let nh = self.head + count;
                self.head = if nh < self.cap_pkts { nh } else { nh - self.cap_pkts };
            }
        }
    }

    impl Drop for CompactSlab {
        fn drop(&mut self) {
            unsafe {
                libc::munmap(self.ptr.cast::<libc::c_void>(), self.mmap_len);
            }
        }
    }

    enum ElasticFifo<'e> {
        Compact(CompactSlab),
        Mbuf { ring: VecDeque<Mbuf<'e>>, cap_pkts: usize },
    }

    impl<'e> ElasticFifo<'e> {
        fn len(&self) -> usize {
            match self {
                ElasticFifo::Compact(s) => s.len(),
                ElasticFifo::Mbuf { ring, .. } => ring.len(),
            }
        }

        fn is_empty(&self) -> bool {
            self.len() == 0
        }

        fn clear(&mut self) {
            match self {
                ElasticFifo::Compact(s) => s.clear(),
                ElasticFifo::Mbuf { ring, .. } => ring.clear(),
            }
        }

        fn push_mbuf(&mut self, m: Mbuf<'e>) -> bool {
            match self {
                ElasticFifo::Compact(s) => s.push_slice(m.data()),
                ElasticFifo::Mbuf { ring, cap_pkts } => {
                    if ring.len() >= *cap_pkts {
                        false
                    } else {
                        ring.push_back(m);
                        true
                    }
                }
            }
        }
    }

    // -------------------------------------------------------------------------
    // Shared Configuration, Per-Core Stats & Control Coordination
    // -------------------------------------------------------------------------
    #[derive(Clone, Copy, Debug)]
    struct RuntimeCfg {
        policy: Policy,
        profile: PktProfile,
        fifo_mode: FifoMode,
        run_gen: u64,
        r_eff: u64,
        r_eff_bytes: u64,
        fifo_cap_bytes_per_core: usize,
    }

    #[derive(Clone, Default)]
    struct CoreStats {
        eth_rx_pkts: u64,
        eth_rx_bytes: u64,
        sdp_tx_pkts: u64,
        sdp_tx_bytes: u64,
        sdp_tx_drops: u64,
        sdp_tx_stalls: u64,
        fast_path_pkts: u64,
        fifo_enq: u64,
        fifo_deq: u64,
        fifo_drops: u64,
        fifo_max_depth: u64,
        fifo_cur_depth: u64,
        alloc_fails: u64,
        credit_updates: u64,
        credit_torn: u64,
        credit_blocks: u64,
        taken_cached_pkts: u64,
        taken_cached_bytes: u64,
        wire_bad: u64,
        wire_torn: u64,
        wire_gaps: u64,
        wire_ooo: u64,
        arp_replies: u64,
    }

    impl CoreStats {
        fn add_assign(&mut self, o: &Self) {
            self.eth_rx_pkts += o.eth_rx_pkts;
            self.eth_rx_bytes += o.eth_rx_bytes;
            self.sdp_tx_pkts += o.sdp_tx_pkts;
            self.sdp_tx_bytes += o.sdp_tx_bytes;
            self.sdp_tx_drops += o.sdp_tx_drops;
            self.sdp_tx_stalls += o.sdp_tx_stalls;
            self.fast_path_pkts += o.fast_path_pkts;
            self.fifo_enq += o.fifo_enq;
            self.fifo_deq += o.fifo_deq;
            self.fifo_drops += o.fifo_drops;
            self.fifo_max_depth += o.fifo_max_depth;
            self.fifo_cur_depth += o.fifo_cur_depth;
            self.alloc_fails += o.alloc_fails;
            self.credit_updates += o.credit_updates;
            self.credit_torn += o.credit_torn;
            self.credit_blocks += o.credit_blocks;
            self.taken_cached_pkts += o.taken_cached_pkts;
            self.taken_cached_bytes += o.taken_cached_bytes;
            self.wire_bad += o.wire_bad;
            self.wire_torn += o.wire_torn;
            self.wire_gaps += o.wire_gaps;
            self.wire_ooo += o.wire_ooo;
            self.arp_replies += o.arp_replies;
        }
    }

    struct CoreSlot {
        ack_gen: AtomicU64,
        cur_fifo_len: AtomicU64,
        sdp_tx_pkts: AtomicU64,
        snap: Mutex<CoreStats>,
    }

    impl CoreSlot {
        fn new() -> Self {
            Self {
                ack_gen: AtomicU64::new(0),
                cur_fifo_len: AtomicU64::new(0),
                sdp_tx_pkts: AtomicU64::new(0),
                snap: Mutex::new(CoreStats::default()),
            }
        }
    }

    struct Shared {
        quit: AtomicBool,
        n_cores: usize,
        cmd_lock: Mutex<()>,
        cmd_word: AtomicU64, // (gen << 4) | cmd (1=RESET, 2=STATS, 3=RECONFIG)
        cfg: Mutex<RuntimeCfg>,
        slots: Vec<CoreSlot>,
        flow_q: [AtomicU64; MAX_PROBE_FLOWS],
        pmu_start: Mutex<Option<CardPmuSnap>>,
        bar4_ptr: usize,
    }

    impl Shared {
        fn new(n_cores: usize, cfg: RuntimeCfg, bar4_ptr: usize) -> Self {
            Self {
                quit: AtomicBool::new(false),
                n_cores,
                cmd_lock: Mutex::new(()),
                cmd_word: AtomicU64::new(0),
                cfg: Mutex::new(cfg),
                slots: (0..n_cores).map(|_| CoreSlot::new()).collect(),
                flow_q: [const { AtomicU64::new(0) }; MAX_PROBE_FLOWS],
                pmu_start: Mutex::new(None),
                bar4_ptr,
            }
        }

        fn run_cmd(&self, cmd: u64) -> Option<(CoreStats, Vec<CoreStats>)> {
            let _g = self.cmd_lock.lock().unwrap();
            let prev = self.cmd_word.load(Ordering::Relaxed) >> 4;
            let next_gen = prev + 1;
            self.cmd_word.store((next_gen << 4) | (cmd & 0xf), Ordering::Release);
            let t0 = Instant::now();
            loop {
                if self.slots.iter().all(|s| s.ack_gen.load(Ordering::Acquire) >= next_gen) {
                    break;
                }
                if self.quit.load(Ordering::Relaxed) || t0.elapsed() > Duration::from_secs(5) {
                    return None;
                }
                std::thread::sleep(Duration::from_micros(50));
            }
            let mut tot = CoreStats::default();
            let mut per = Vec::with_capacity(self.n_cores);
            for s in &self.slots {
                let c = s.snap.lock().unwrap().clone();
                tot.add_assign(&c);
                per.push(c);
            }
            Some((tot, per))
        }

        fn zero_credit_lines(&self) {
            if self.bar4_ptr == 0 {
                return;
            }
            let base = self.bar4_ptr as *mut u8;
            for q in 0..MAX_QUEUES {
                let line = unsafe { base.add(credit_line_offset(q)).cast::<u64>() };
                for w in 0..8 {
                    unsafe { line.add(w).write_volatile(0) };
                }
            }
            std::sync::atomic::fence(Ordering::SeqCst);
        }

        fn total_fifo_len(&self) -> u64 {
            self.slots.iter().map(|s| s.cur_fifo_len.load(Ordering::Relaxed)).sum()
        }

        fn total_sdp_tx(&self) -> u64 {
            self.slots.iter().map(|s| s.sdp_tx_pkts.load(Ordering::Relaxed)).sum()
        }
    }

    #[inline(always)]
    fn poll_credit_line(
        bar4_base: *const u8,
        queue_id: usize,
        expected_run_gen: u64,
        taken_cached: u64,
    ) -> CreditPoll {
        let p = unsafe { bar4_base.add(credit_line_offset(queue_id)).cast::<u64>() };
        // Fast path: check head word first; if unchanged, skip reading the rest of the 64 B line.
        let w0_peek = unsafe { p.read_volatile() };
        if w0_peek <= taken_cached {
            return CreditPoll::Empty;
        }
        // Host delivers 64 B atomically via MOVDIR64B, but ARMv8 reads 8x64-bit words across
        // multiple instructions; retry transient mid-read updates (standard seqlock reader).
        for _ in 0..8 {
            let mut line = CreditLine::default();
            line.w[0] = unsafe { p.read_volatile() };
            std::sync::atomic::fence(Ordering::Acquire);
            for i in 1..7 {
                line.w[i] = unsafe { p.add(i).read_volatile() };
            }
            std::sync::atomic::fence(Ordering::Acquire);
            line.w[7] = unsafe { p.add(7).read_volatile() };
            match line.verify(expected_run_gen, queue_id as u16) {
                CreditPoll::Torn => continue,
                other => return other,
            }
        }
        CreditPoll::Torn
    }
    #[inline(always)]
    fn try_handle_arp<'e>(
        m: Mbuf<'e>,
        eth_mac: [u8; 6],
        eth_qp: &mut QueuePair<'_, 'e>,
        cnt: &mut CoreStats,
    ) -> Option<Mbuf<'e>> {
        let d = m.data();
        if d.len() >= 42 && d[12] == 0x08 && d[13] == 0x06 {
            if d[20] == 0x00 && d[21] == 0x01 && d[38..42] == CARD_IP {
                let mut reply = m;
                let b = reply.data_mut();
                let mut req_mac = [0u8; 6];
                let mut req_ip = [0u8; 4];
                req_mac.copy_from_slice(&b[22..28]);
                req_ip.copy_from_slice(&b[28..32]);
                b[0..6].copy_from_slice(&req_mac);
                b[6..12].copy_from_slice(&eth_mac);
                b[20] = 0x00;
                b[21] = 0x02;
                b[22..28].copy_from_slice(&eth_mac);
                b[28..32].copy_from_slice(&CARD_IP);
                b[32..38].copy_from_slice(&req_mac);
                b[38..42].copy_from_slice(&req_ip);
                let _ = eth_qp.send(reply);
                cnt.arp_replies += 1;
            }
            return None;
        }
        Some(m)
    }

    fn read_xstats_json(ps: &PortStats<'_, '_>) -> String {
        let mut parts = Vec::new();
        for (name, val) in ps.xstats().unwrap_or_default() {
            if name.contains("rpm_rx_") || name.contains("rx_drop") || name.contains("rx_err") || name.contains("imissed") {
                parts.push(format!("\"{}\":{}", name.replace('"', ""), val));
            }
        }
        format!("{{{}}}", parts.join(","))
    }

    fn format_eth_stats(s: &EthStats) -> String {
        format!(
            "{{\"ipackets\":{},\"opackets\":{},\"ibytes\":{},\"obytes\":{},\"imissed\":{},\"ierrors\":{},\"oerrors\":{},\"rx_nombuf\":{}}}",
            s.ipackets, s.opackets, s.ibytes, s.obytes, s.imissed, s.ierrors, s.oerrors, s.rx_nombuf
        )
    }

    fn format_stats_json(
        cfg: &RuntimeCfg,
        n_cores: usize,
        tot: &CoreStats,
        per: &[CoreStats],
        eth_st: &EthStats,
        eth_xs: &str,
        sdp_st: &EthStats,
        flow_q: &[AtomicU64; MAX_PROBE_FLOWS],
    ) -> String {
        let per_s: Vec<String> = per
            .iter()
            .enumerate()
            .map(|(i, c)| {
                format!(
                    "{{\"q\":{i},\"eth_rx\":{},\"sdp_tx\":{},\"sdp_drops\":{},\"sdp_stalls\":{},\
                     \"fast_path\":{},\"fifo_enq\":{},\"fifo_deq\":{},\"fifo_drops\":{},\"fifo_max\":{},\"fifo_cur\":{},\
                     \"credit_upd\":{},\"credit_torn\":{},\"credit_blk\":{},\"taken_pkts\":{}}}",
                    c.eth_rx_pkts,
                    c.sdp_tx_pkts,
                    c.sdp_tx_drops,
                    c.sdp_tx_stalls,
                    c.fast_path_pkts,
                    c.fifo_enq,
                    c.fifo_deq,
                    c.fifo_drops,
                    c.fifo_max_depth,
                    c.fifo_cur_depth,
                    c.credit_updates,
                    c.credit_torn,
                    c.credit_blocks,
                    c.taken_cached_pkts,
                )
            })
            .collect();
        let fq_s: Vec<String> = flow_q.iter().map(|a| a.load(Ordering::Relaxed).to_string()).collect();

        format!(
            "{{\"policy\":\"{}\",\"profile\":\"{}\",\"fifo_mode\":\"{}\",\"run_gen\":{},\"n_cores\":{},\
             \"r_eff\":{},\"r_eff_bytes\":{},\
             \"eth_rx_pkts\":{},\"eth_rx_bytes\":{},\"sdp_tx_pkts\":{},\"sdp_tx_bytes\":{},\
             \"sdp_tx_drops\":{},\"sdp_tx_stalls\":{},\"fast_path_pkts\":{},\
             \"fifo_enq\":{},\"fifo_deq\":{},\"fifo_drops\":{},\"fifo_max_depth\":{},\"fifo_cur_depth\":{},\
             \"alloc_fails\":{},\"credit_updates\":{},\"credit_torn\":{},\"credit_blocks\":{},\
             \"taken_cached_pkts\":{},\"taken_cached_bytes\":{},\
             \"wire_bad\":{},\"wire_torn\":{},\"wire_gaps\":{},\"wire_ooo\":{},\"arp_replies\":{},\
             \"eth\":{},\"eth_xstats\":{},\"sdp\":{},\"flow_q\":[{}],\"per_core\":[{}]}}",
            cfg.policy.as_str(),
            cfg.profile.as_str(),
            cfg.fifo_mode.as_str(),
            cfg.run_gen,
            n_cores,
            cfg.r_eff,
            cfg.r_eff_bytes,
            tot.eth_rx_pkts,
            tot.eth_rx_bytes,
            tot.sdp_tx_pkts,
            tot.sdp_tx_bytes,
            tot.sdp_tx_drops,
            tot.sdp_tx_stalls,
            tot.fast_path_pkts,
            tot.fifo_enq,
            tot.fifo_deq,
            tot.fifo_drops,
            tot.fifo_max_depth,
            tot.fifo_cur_depth,
            tot.alloc_fails,
            tot.credit_updates,
            tot.credit_torn,
            tot.credit_blocks,
            tot.taken_cached_pkts,
            tot.taken_cached_bytes,
            tot.wire_bad,
            tot.wire_torn,
            tot.wire_gaps,
            tot.wire_ooo,
            tot.arp_replies,
            format_eth_stats(eth_st),
            eth_xs,
            format_eth_stats(sdp_st),
            fq_s.join(","),
            per_s.join(","),
        )
    }

    struct WorkerInput<'p, 'e> {
        eth_qp: QueuePair<'p, 'e>,
        sdp_qp: QueuePair<'p, 'e>,
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let mut raw_args: Vec<String> = std::env::args().collect();
        if raw_args.iter().any(|a| a == "--restore") {
            let changed = restore_defaults(SLOT)?;
            if changed.is_empty() {
                println!("BAR4 slot {SLOT} and PEM0 outbound regions already at defaults");
            } else {
                for line in changed {
                    println!("restored {line}");
                }
            }
            return Ok(());
        }

        let dash = raw_args.iter().position(|a| a == "--");
        let (eal_args, app_args) = match dash {
            Some(i) => (
                raw_args[..i].to_vec(),
                std::iter::once(raw_args[0].clone()).chain(raw_args.drain(i + 1..)).collect(),
            ),
            None => (vec![raw_args[0].clone()], raw_args),
        };
        let args = Args::parse_from(app_args);
        let init_profile = PktProfile::from_name(&args.profile)
            .ok_or_else(|| format!("unknown --profile '{}'", args.profile))?;

        let (eal, _) = Eal::init(eal_args)?;
        let n_cores = eal.lcores();
        if n_cores == 0 || n_cores > MAX_QUEUES {
            return Err(format!("n_cores={n_cores} must be in 1..={MAX_QUEUES}").into());
        }

        // Open eth0 (`0002:02:00.0`) and sdp1-1 (`0002:01:00.2`)
        let eth_id = Port::by_name(&eal, &args.eth_dev)?;
        let sdp_id = Port::by_name(&eal, &args.sdp_dev)?;
        let eth_sock = Port::socket_of(&eal, eth_id);
        let sdp_sock = Port::socket_of(&eal, sdp_id);

        // Native CN106 NPA mempool (hardware LIFO aura, Pitfall 3)
        let eth_pool = Mempool::new_with_opts(
            &eal,
            "m213_eth_pool",
            args.eth_pool_size,
            256,
            ETH_MBUF_BUF_SIZE,
            eth_sock,
            None,
        )?;
        let sdp_rx_pool = Mempool::new_with_opts(
            &eal,
            "m213_sdp_rx_pool",
            (n_cores as u32 * args.sdp_rx_desc as u32 + 1023).max(2047),
            128,
            ETH_MBUF_BUF_SIZE,
            sdp_sock,
            None,
        )?;

        let eth_pools = vec![&eth_pool; n_cores];
        let mut eth_port = Port::start_per_queue_ex(
            &eal,
            eth_id,
            &eth_pools,
            ETH_DESC,
            ETH_DESC,
            Some(1500),
            n_cores > 1,
        )?;
        eth_port.promiscuous()?;
        if let Err(e) = eth_port.disable_flow_ctrl() {
            eprintln!("WARN: eth0 disable_flow_ctrl failed: {e}");
        }
        let eth_mac = eth_port.mac();

        let sdp_pools = vec![&sdp_rx_pool; n_cores];
        let mut sdp_port = Port::start_per_queue_ex(
            &eal,
            sdp_id,
            &sdp_pools,
            args.sdp_rx_desc,
            args.sdp_tx_desc,
            Some(1500),
            false,
        )?;
        sdp_port.promiscuous()?;

        // Claim BAR4 slot 15 and publish MRV-26 lease area (`READY` + `NONCE`)
        let bar4_mz = Memzone::reserve_contiguous(&eal, "m213_bar4", SLOT_BYTES, eth_sock, SLOT_BYTES as u32)?;
        unsafe {
            ptr::write_bytes(bar4_mz.as_ptr(), 0, SLOT_BYTES);
            bar4_mz.as_ptr().add(READY_OFFSET).cast::<u64>().write_volatile(READY);
            bar4_mz.as_ptr().add(NONCE_OFFSET).cast::<u64>().write_volatile(0);
        }
        let bar4_pa = bar4_mz.phys_addr().ok_or("rte_mem_virt2phy failed on BAR4 memzone")?;
        let _bar4_win = Bar4Window::claim(SLOT, bar4_pa)?;
        let bar4_ptr_usize = bar4_mz.as_ptr() as usize;

        let init_cfg = RuntimeCfg {
            policy: args.policy,
            profile: init_profile,
            fifo_mode: args.fifo_mode,
            run_gen: 1,
            r_eff: args.r_eff,
            r_eff_bytes: args.r_eff_bytes,
            fifo_cap_bytes_per_core: args.fifo_cap_bytes.div_ceil(n_cores),
        };
        let shared = Arc::new(Shared::new(n_cores, init_cfg, bar4_ptr_usize));
        {
            let sh = Arc::clone(&shared);
            ctrlc::set_handler(move || sh.quit.store(true, Ordering::Relaxed))?;
        }

        // Open CN106 PMU across worker CPUs (collect actual CPU ids from EAL lcores)
        let worker_cpus: Vec<i32> = eal.on_each_lcore(vec![(); n_cores], |_, _| unsafe { libc::sched_getcpu() });
        let card_pmu = Arc::new(CardPmu::open(&worker_cpus)?);

        let lease_listener = TcpListener::bind(&args.control)?;
        let sync_listener = TcpListener::bind(&args.sync)?;
        sync_listener.set_nonblocking(true)?;

        let (eth_stats, eth_qps) = eth_port.split_queues();
        let (sdp_stats, sdp_qps) = sdp_port.split_queues();
        let _ = eth_stats.stats_reset();
        let _ = eth_stats.xstats_reset();
        let _ = sdp_stats.stats_reset();

        let inputs: Vec<WorkerInput<'_, '_>> = eth_qps
            .into_iter()
            .zip(sdp_qps)
            .map(|(eth_qp, sdp_qp)| WorkerInput { eth_qp, sdp_qp })
            .collect();

        let tsc_hz = eal.tsc_hz();

        std::thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            let nonce_ref: &AtomicU64 =
                unsafe { &*bar4_mz.as_ptr().add(NONCE_OFFSET).cast::<AtomicU64>() };
            let _lease_srv = lease::serve(scope, lease_listener, nonce_ref)?;

            let sh_srv = Arc::clone(&shared);
            let pmu_srv = Arc::clone(&card_pmu);
            scope.spawn(move || {
                // Pin control thread to cpu0 (off data-plane lcores)
                unsafe {
                    let mut set: libc::cpu_set_t = std::mem::zeroed();
                    libc::CPU_ZERO(&mut set);
                    libc::CPU_SET(0, &mut set);
                    libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &raw const set);
                }
                while !sh_srv.quit.load(Ordering::Relaxed) {
                    match sync_listener.accept() {
                        Ok((mut stream, _)) => {
                            let _ = stream.set_nodelay(true);
                            let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
                            let mut reader = BufReader::new(stream.try_clone().unwrap());
                            let mut line = String::new();
                            if reader.read_line(&mut line).is_ok() {
                                let cmd = line.trim();
                                let reply = if cmd == "PING" {
                                    "{\"ok\":true}".to_string()
                                } else if cmd == "PMU_START" {
                                    *sh_srv.pmu_start.lock().unwrap() = Some(pmu_srv.snap());
                                    "{\"ok\":true}".to_string()
                                } else if cmd == "PMU_STOP" {
                                    let s1 = pmu_srv.snap();
                                    let guard = sh_srv.pmu_start.lock().unwrap();
                                    match guard.as_ref() {
                                        Some(s0) => CardPmu::delta_json(s0, &s1),
                                        None => "{\"err\":\"no PMU_START\"}".to_string(),
                                    }
                                } else if cmd == "RESET" {
                                    sh_srv.zero_credit_lines();
                                    for f in &sh_srv.flow_q {
                                        f.store(0, Ordering::Relaxed);
                                    }
                                    let _ = sh_srv.run_cmd(1);
                                    let _ = eth_stats.stats_reset();
                                    let _ = eth_stats.xstats_reset();
                                    let _ = sdp_stats.stats_reset();
                                    "{\"ok\":true}".to_string()
                                } else if let Some(rest) = cmd.strip_prefix("CONFIG ") {
                                    let mut next_cfg = *sh_srv.cfg.lock().unwrap();
                                    let mut parse_err: Option<String> = None;
                                    for tok in rest.split_whitespace() {
                                        if let Some((k, v)) = tok.split_once('=') {
                                            match k {
                                                "policy" => match Policy::from_name(v) {
                                                    Some(p) => next_cfg.policy = p,
                                                    None => parse_err = Some(format!("bad policy {v}")),
                                                },
                                                "profile" => match PktProfile::from_name(v) {
                                                    Some(p) => next_cfg.profile = p,
                                                    None => parse_err = Some(format!("bad profile {v}")),
                                                },
                                                "fifo_mode" => match FifoMode::from_name(v) {
                                                    Some(m) => next_cfg.fifo_mode = m,
                                                    None => parse_err = Some(format!("bad fifo_mode {v}")),
                                                },
                                                "run_gen" => {
                                                    if let Ok(x) = v.parse() {
                                                        next_cfg.run_gen = x;
                                                    }
                                                }
                                                "r_eff" => {
                                                    if let Ok(x) = v.parse() {
                                                        next_cfg.r_eff = x;
                                                    }
                                                }
                                                "r_eff_bytes" => {
                                                    if let Ok(x) = v.parse() {
                                                        next_cfg.r_eff_bytes = x;
                                                    }
                                                }
                                                "fifo_cap_bytes" => {
                                                    if let Ok(x) = v.parse::<usize>() {
                                                        next_cfg.fifo_cap_bytes_per_core = x.div_ceil(sh_srv.n_cores);
                                                    }
                                                }
                                                _ => {}
                                            }
                                        }
                                    }
                                    if let Some(e) = parse_err {
                                        format!("{{\"ok\":false,\"err\":\"{e}\"}}")
                                    } else {
                                        *sh_srv.cfg.lock().unwrap() = next_cfg;
                                        sh_srv.zero_credit_lines();
                                        for f in &sh_srv.flow_q {
                                            f.store(0, Ordering::Relaxed);
                                        }
                                        let ok = sh_srv.run_cmd(3).is_some();
                                        let _ = eth_stats.stats_reset();
                                        let _ = eth_stats.xstats_reset();
                                        let _ = sdp_stats.stats_reset();
                                        format!(
                                            "{{\"ok\":{ok},\"policy\":\"{}\",\"profile\":\"{}\",\"run_gen\":{},\"r_eff\":{}}}",
                                            next_cfg.policy.as_str(),
                                            next_cfg.profile.as_str(),
                                            next_cfg.run_gen,
                                            next_cfg.r_eff,
                                        )
                                    }
                                } else if let Some(rest) = cmd.strip_prefix("DRAIN_WAIT") {
                                    let timeout_ms: u64 = rest
                                        .split_whitespace()
                                        .find_map(|t| t.strip_prefix("timeout_ms=").and_then(|v| v.parse().ok()))
                                        .unwrap_or(5000);
                                    let t0 = Instant::now();
                                    while sh_srv.total_fifo_len() > 0
                                        && t0.elapsed() < Duration::from_millis(timeout_ms)
                                        && !sh_srv.quit.load(Ordering::Relaxed)
                                    {
                                        std::thread::sleep(Duration::from_micros(100));
                                    }
                                    format!(
                                        "{{\"ok\":true,\"fifo_len\":{},\"sdp_tx_pkts\":{},\"wait_ms\":{:.3}}}",
                                        sh_srv.total_fifo_len(),
                                        sh_srv.total_sdp_tx(),
                                        t0.elapsed().as_secs_f64() * 1e3,
                                    )
                                } else if cmd == "STATS" {
                                    let cfg_now = *sh_srv.cfg.lock().unwrap();
                                    match sh_srv.run_cmd(2) {
                                        Some((tot, per)) => {
                                            let est = eth_stats.stats().unwrap_or_default();
                                            let exs = read_xstats_json(&eth_stats);
                                            let sst = sdp_stats.stats().unwrap_or_default();
                                            format_stats_json(
                                                &cfg_now,
                                                sh_srv.n_cores,
                                                &tot,
                                                &per,
                                                &est,
                                                &exs,
                                                &sst,
                                                &sh_srv.flow_q,
                                            )
                                        }
                                        None => "{\"err\":\"timeout\"}".to_string(),
                                    }
                                } else if cmd == "QUIT" {
                                    sh_srv.quit.store(true, Ordering::Relaxed);
                                    "{\"ok\":true}".to_string()
                                } else {
                                    "{\"err\":\"unknown\"}".to_string()
                                };
                                let _ = writeln!(stream, "{reply}");
                                let _ = stream.flush();
                            }
                        }
                        Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(1));
                        }
                        Err(_) => {
                            std::thread::sleep(Duration::from_millis(5));
                        }
                    }
                }
            });

            println!(
                "MRV213_CARD READY n_cores={} cpus={:?} policy={} profile={} fifo_mode={} sdp_tx_desc={} eth_pool={} bar4_pa={:#x} mac={:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x} tsc_hz={}",
                n_cores,
                worker_cpus,
                init_cfg.policy.as_str(),
                init_cfg.profile.as_str(),
                init_cfg.fifo_mode.as_str(),
                args.sdp_tx_desc,
                args.eth_pool_size,
                bar4_pa,
                eth_mac[0],
                eth_mac[1],
                eth_mac[2],
                eth_mac[3],
                eth_mac[4],
                eth_mac[5],
                tsc_hz,
            );
            let _ = io::stdout().flush();

            let sh_ref = &shared;
            let eth_pool_ref = &eth_pool;

            eal.on_each_lcore(inputs, move |core_idx, mut inp| {
                let mut cfg = *sh_ref.cfg.lock().unwrap();
                let mut fifo: ElasticFifo<'_> = match cfg.fifo_mode {
                    FifoMode::Compact => ElasticFifo::Compact(
                        CompactSlab::new(
                            cfg.fifo_cap_bytes_per_core.max(4 << 20),
                            cfg.profile.card_slab_stride(),
                        )
                        .expect("allocate CN106 elastic FIFO slab"),
                    ),
                    FifoMode::Mbuf => {
                        let cap_pkts = (cfg.fifo_cap_bytes_per_core / cfg.profile.l2_bytes()).max(1024);
                        ElasticFifo::Mbuf { ring: VecDeque::with_capacity(cap_pkts.min(65536)), cap_pkts }
                    }
                };

                let mut rx_burst: Burst<'_, BURST_N> = Burst::new();
                let mut tx_burst: Burst<'_, BURST_N> = Burst::new();
                let mut overflow_burst: Burst<'_, BURST_N> = Burst::new();
                let mut cnt = CoreStats::default();
                let mut tracker = StreamTracker::new();
                let mut handled_cmd_gen = 0u64;
                let mut iter = 0u64;
                let bar4_base = sh_ref.bar4_ptr as *const u8;

                while !sh_ref.quit.load(Ordering::Relaxed) {
                    iter = iter.wrapping_add(1);

                    // Periodic command handling & atomic telemetry publication
                    if (iter & 0x1ff) == 0 {
                        sh_ref.slots[core_idx]
                            .cur_fifo_len
                            .store(fifo.len() as u64, Ordering::Relaxed);
                        sh_ref.slots[core_idx]
                            .sdp_tx_pkts
                            .store(cnt.sdp_tx_pkts, Ordering::Relaxed);

                        let cw = sh_ref.cmd_word.load(Ordering::Acquire);
                        let cg = cw >> 4;
                        if cg > handled_cmd_gen {
                            let cmd = cw & 0xf;
                            if cmd == 1 || cmd == 3 {
                                if cmd == 3 {
                                    let new_cfg = *sh_ref.cfg.lock().unwrap();
                                    let want_mode = new_cfg.fifo_mode;
                                    match (&mut fifo, want_mode) {
                                        (ElasticFifo::Compact(s), FifoMode::Compact) => {
                                            s.reconfigure(new_cfg.profile.card_slab_stride());
                                        }
                                        (ElasticFifo::Mbuf { ring, cap_pkts }, FifoMode::Mbuf) => {
                                            ring.clear();
                                            *cap_pkts = (new_cfg.fifo_cap_bytes_per_core
                                                / new_cfg.profile.l2_bytes())
                                            .max(1024);
                                        }
                                        (_, FifoMode::Compact) => {
                                            let s = CompactSlab::new(
                                                new_cfg.fifo_cap_bytes_per_core.max(4 << 20),
                                                new_cfg.profile.card_slab_stride(),
                                            )
                                            .expect("reallocate compact slab");
                                            fifo = ElasticFifo::Compact(s);
                                        }
                                        (_, FifoMode::Mbuf) => {
                                            let cap_pkts = (new_cfg.fifo_cap_bytes_per_core
                                                / new_cfg.profile.l2_bytes())
                                            .max(1024);
                                            fifo = ElasticFifo::Mbuf {
                                                ring: VecDeque::with_capacity(cap_pkts.min(65536)),
                                                cap_pkts,
                                            };
                                        }
                                    }
                                    cfg = new_cfg;
                                } else {
                                    fifo.clear();
                                }
                                rx_burst.clear();
                                tx_burst.clear();
                                cnt = CoreStats::default();
                                tracker.reset();
                                sh_ref.slots[core_idx].cur_fifo_len.store(0, Ordering::Relaxed);
                                sh_ref.slots[core_idx].sdp_tx_pkts.store(0, Ordering::Relaxed);
                                *sh_ref.slots[core_idx].snap.lock().unwrap() = cnt.clone();
                            } else if cmd == 2 {
                                cnt.fifo_cur_depth = fifo.len() as u64;
                                cnt.wire_gaps = tracker.gaps;
                                cnt.wire_ooo = tracker.out_of_order;
                                *sh_ref.slots[core_idx].snap.lock().unwrap() = cnt.clone();
                            }
                            handled_cmd_gen = cg;
                            sh_ref.slots[core_idx].ack_gen.store(cg, Ordering::Release);
                        }
                    }

                    // 1. Refresh NO-SFENCE BAR4 credit line locally from CN106 L3 (~10-20 ns)
                    if cfg.policy == Policy::CreditRing && !bar4_base.is_null() {
                        match poll_credit_line(bar4_base, core_idx, cfg.run_gen, cnt.taken_cached_pkts) {
                            CreditPoll::Valid { taken_pkts, taken_bytes } => {
                                if taken_pkts > cnt.taken_cached_pkts {
                                    cnt.taken_cached_pkts = taken_pkts;
                                    cnt.taken_cached_bytes = cnt.taken_cached_bytes.max(taken_bytes);
                                    cnt.credit_updates += 1;
                                }
                            }
                            CreditPoll::Torn => {
                                cnt.credit_torn += 1;
                            }
                            CreditPoll::Empty | CreditPoll::Stale => {}
                        }
                    }

                    // 2. Receive incoming 100GbE packets from mercury on eth0 RX[core_idx]
                    let n_rx = inp.eth_qp.rx(&mut rx_burst);
                    if n_rx > 0 {
                        let l2_pkt_bytes = cfg.profile.l2_bytes() as u64;
                        let mut credit_room_now = if cfg.policy == Policy::CreditRing {
                            compute_credit_room(&cfg, &cnt, l2_pkt_bytes)
                        } else {
                            u64::MAX
                        };

                        rx_burst.drain_into(|pkt| {
                            let Some(pkt) = try_handle_arp(pkt, eth_mac, &mut inp.eth_qp, &mut cnt) else {
                                return;
                            };
                            let d = pkt.data();
                            match verify_wire_frame(d, cfg.profile, cfg.run_gen) {
                                WireCheck::Ignore => return,
                                WireCheck::Bad => {
                                    cnt.wire_bad += 1;
                                    return;
                                }
                                WireCheck::Torn => {
                                    cnt.wire_torn += 1;
                                    return;
                                }
                                WireCheck::Ok { stream_id, seq, is_psn24, .. } => {
                                    tracker.observe(stream_id, seq, is_psn24);
                                    if (stream_id as usize) < MAX_PROBE_FLOWS {
                                        let slot = &sh_ref.flow_q[stream_id as usize];
                                        if slot.load(Ordering::Relaxed) == 0 {
                                            slot.store((core_idx + 1) as u64, Ordering::Relaxed);
                                        }
                                    }
                                }
                            }
                            cnt.eth_rx_pkts += 1;
                            cnt.eth_rx_bytes += l2_pkt_bytes;

                            match cfg.policy {
                                Policy::Idle => {
                                    // Drop after verify (used for RSS probe / baseline check)
                                }
                                Policy::Passthrough | Policy::Drop => {
                                    let _ = tx_burst.push(pkt);
                                }
                                Policy::NativeBp => {
                                    if fifo.is_empty() {
                                        let _ = tx_burst.push(pkt);
                                    } else if fifo.push_mbuf(pkt) {
                                        cnt.fifo_enq += 1;
                                        let cur = fifo.len() as u64;
                                        if cur > cnt.fifo_max_depth {
                                            cnt.fifo_max_depth = cur;
                                        }
                                    } else {
                                        cnt.fifo_drops += 1;
                                    }
                                }
                                Policy::CreditRing => {
                                    if fifo.is_empty() && overflow_burst.is_empty() && credit_room_now > 0 {
                                        let _ = tx_burst.push(pkt);
                                        credit_room_now -= 1;
                                    } else {
                                        if credit_room_now == 0 && fifo.is_empty() && overflow_burst.is_empty() {
                                            cnt.credit_blocks += 1;
                                        }
                                        if tx_burst.is_empty() {
                                            if fifo.push_mbuf(pkt) {
                                                cnt.fifo_enq += 1;
                                                let cur = fifo.len() as u64;
                                                if cur > cnt.fifo_max_depth {
                                                    cnt.fifo_max_depth = cur;
                                                }
                                            } else {
                                                cnt.fifo_drops += 1;
                                            }
                                        } else {
                                            let _ = overflow_burst.push(pkt);
                                        }
                                    }
                                }
                            }
                        });

                        // Flush direct zero-copy fast-path burst to sdp1-1 TX[core_idx]
                        if !tx_burst.is_empty() {
                            let want = tx_burst.len();
                            let sent = inp.sdp_qp.tx(&mut tx_burst);
                            cnt.sdp_tx_pkts += sent as u64;
                            cnt.sdp_tx_bytes += sent as u64 * l2_pkt_bytes;
                            cnt.fast_path_pkts += sent as u64;

                            if !tx_burst.is_empty() {
                                let rem = want - sent;
                                match cfg.policy {
                                    Policy::Passthrough => {
                                        // Retry until sdp1-1 SQ accepts (large-mono / large-lifo host absorbs)
                                        let mut spins = 0u32;
                                        while !tx_burst.is_empty() && !sh_ref.quit.load(Ordering::Relaxed) {
                                            let s2 = inp.sdp_qp.tx(&mut tx_burst);
                                            if s2 > 0 {
                                                cnt.sdp_tx_pkts += s2 as u64;
                                                cnt.sdp_tx_bytes += s2 as u64 * l2_pkt_bytes;
                                                cnt.fast_path_pkts += s2 as u64;
                                                spins = 0;
                                            } else {
                                                cnt.sdp_tx_stalls += 1;
                                                spins += 1;
                                                if spins > 2_000_000 {
                                                    cnt.sdp_tx_drops += tx_burst.len() as u64;
                                                    tx_burst.clear();
                                                    break;
                                                }
                                                std::hint::spin_loop();
                                            }
                                        }
                                    }
                                    Policy::Drop => {
                                        // Arm 2: small passive host ring full -> card drops immediately
                                        cnt.sdp_tx_drops += rem as u64;
                                        cnt.sdp_tx_stalls += 1;
                                        tx_burst.clear();
                                    }
                                    Policy::NativeBp | Policy::CreditRing => {
                                        // Backpressure hit! Absorb unsent head packets first (strict FIFO order)
                                        cnt.sdp_tx_stalls += 1;
                                        tx_burst.drain_into(|m| {
                                            if fifo.push_mbuf(m) {
                                                cnt.fifo_enq += 1;
                                                let cur = fifo.len() as u64;
                                                if cur > cnt.fifo_max_depth {
                                                    cnt.fifo_max_depth = cur;
                                                }
                                            } else {
                                                cnt.fifo_drops += 1;
                                            }
                                        });
                                    }
                                    Policy::Idle => tx_burst.clear(),
                                }
                            }
                        }

                        // Enqueue any excess packets from this RX burst strictly AFTER unsent tx_burst packets
                        if !overflow_burst.is_empty() {
                            overflow_burst.drain_into(|m| {
                                if fifo.push_mbuf(m) {
                                    cnt.fifo_enq += 1;
                                    let cur = fifo.len() as u64;
                                    if cur > cnt.fifo_max_depth {
                                        cnt.fifo_max_depth = cur;
                                    }
                                } else {
                                    cnt.fifo_drops += 1;
                                }
                            });
                        }
                    }

                    // 3. Drain On-Path Elastic FIFO into sdp1-1 TX[core_idx]
                    if !fifo.is_empty() && matches!(cfg.policy, Policy::NativeBp | Policy::CreditRing) {
                        let l2_pkt_bytes = cfg.profile.l2_bytes() as u64;
                        let budget: usize = if cfg.policy == Policy::CreditRing {
                            let room = compute_credit_room(&cfg, &cnt, l2_pkt_bytes);
                            if room == 0 {
                                cnt.credit_blocks += 1;
                                0
                            } else {
                                (room as usize).min(BURST_N).min(fifo.len())
                            }
                        } else {
                            BURST_N.min(fifo.len())
                        };

                        if budget > 0 {
                            match &mut fifo {
                                ElasticFifo::Compact(slab) => {
                                    let mut staged = 0usize;
                                    while staged < budget {
                                        let Some(mut m) = eth_pool_ref.alloc() else {
                                            cnt.alloc_fails += 1;
                                            break;
                                        };
                                        let src = slab.peek_at(staged);
                                        if let Some(dst) = m.append(src.len() as u16) {
                                            dst.copy_from_slice(src);
                                            let _ = tx_burst.push(m);
                                            staged += 1;
                                        } else {
                                            cnt.alloc_fails += 1;
                                            break;
                                        }
                                    }
                                    if staged > 0 {
                                        let sent = inp.sdp_qp.tx(&mut tx_burst);
                                        if sent > 0 {
                                            slab.advance_head(sent);
                                            cnt.fifo_deq += sent as u64;
                                            cnt.sdp_tx_pkts += sent as u64;
                                            cnt.sdp_tx_bytes += sent as u64 * l2_pkt_bytes;
                                        }
                                        if sent < staged {
                                            cnt.sdp_tx_stalls += 1;
                                            // Unsent mbufs are freed; exact copies remain at slab head
                                            tx_burst.clear();
                                        }
                                    }
                                }
                                ElasticFifo::Mbuf { ring, .. } => {
                                    let mut staged = 0usize;
                                    while staged < budget {
                                        if let Some(m) = ring.pop_front() {
                                            let _ = tx_burst.push(m);
                                            staged += 1;
                                        } else {
                                            break;
                                        }
                                    }
                                    if staged > 0 {
                                        let sent = inp.sdp_qp.tx(&mut tx_burst);
                                        if sent > 0 {
                                            cnt.fifo_deq += sent as u64;
                                            cnt.sdp_tx_pkts += sent as u64;
                                            cnt.sdp_tx_bytes += sent as u64 * l2_pkt_bytes;
                                        }
                                        if sent < staged {
                                            cnt.sdp_tx_stalls += 1;
                                            // Preserve strict FIFO order by returning unsent mbufs to front
                                            let mut leftover: Vec<Mbuf<'_>> = Vec::with_capacity(staged - sent);
                                            tx_burst.drain_into(|m| leftover.push(m));
                                            for m in leftover.into_iter().rev() {
                                                ring.push_front(m);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            });

            Ok(())
        })?;

        Ok(())
    }

    #[inline(always)]
    fn compute_credit_room(cfg: &RuntimeCfg, cnt: &CoreStats, l2_pkt_bytes: u64) -> u64 {
        let inflight_pkts = cnt.sdp_tx_pkts.saturating_sub(cnt.taken_cached_pkts);
        let pkt_room = cfg.r_eff.saturating_sub(inflight_pkts);
        if cfg.r_eff_bytes > 0 {
            let inflight_bytes = cnt.sdp_tx_bytes.saturating_sub(cnt.taken_cached_bytes);
            let byte_room = cfg.r_eff_bytes.saturating_sub(inflight_bytes) / l2_pkt_bytes.max(1);
            pkt_room.min(byte_room)
        } else {
            pkt_room
        }
    }
}
