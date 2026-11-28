//! Single-host-core (`saturn` Socket 1 `cpu40 @ 3.90 GHz`) standard-SDP receiver,
//! echo/KV responder, CEIO §4.2 2P1C `SW ring` + `async_recv()` driver, optional
//! co-located 32 MiB KV latency tenant (`cpu41`), and 100% non-multiplexed Host PMU
//! for MRV-211 (MRV-203 E3).
//!
//! All modes run on the EXACT SAME standard DPDK `octeon_ep` SDP data plane:
//!   - `--mode unified`  : Compact DDIO-pinned SDP hot rings (`nb_desc = 512`, `R_eff = 256`,
//!                         compact hot mempool) + fence-free `MOVDIR64B -> BAR4` credit returns.
//!                         Single flat polling loop: zero SW ring, zero `async_recv()`.
//!   - `--mode ceio`     : CEIO §4.2 host driver over standard SDP:
//!                           * Fast Path Ring (`SDP` Q[0..C-1], `nb_desc = 2048`)
//!                           * Slow Path Ring (`SDP` Q[C..2C-1], `nb_desc = 512`)
//!                           * 2P1C `SW ring` (`SwMergeRing2P1C`) + `async_recv()` pull
//!                           * Lazy batched fast-path head credit release + slow-path head acks
//!                             via fence-free `MOVDIR64B -> BAR4`.
//!   - `--mode large-sdp`: Passive uncontrolled large SDP (`nb_desc = 4096`, large pool).

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv211_host runs on the x86_64 host");
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
    use dpdk::{Burst, Eal, EthStats, Mbuf, Mempool, Memzone, Port, rdtsc, rdtsc_after_loads};
    use testbed::lease::{Lease, LeasedSlot};
    use testbed::mmio::{has_movdir64b, movdir64b};
    use mrv211::{
        BAR4_CEIO_PULL_OFF, BAR4_HOST_CREDIT_OFF, CLASS_RPC_HI, CeioSlowPullLine,
        DEFAULT_CEIO_FAST_RX_DESC, DEFAULT_CEIO_LAZY_BATCH, DEFAULT_CEIO_PULL_WINDOW,
        DEFAULT_CEIO_SLOW_RX_DESC, DEFAULT_CREDIT_BATCH, DEFAULT_UNIFIED_R_EFF_PER_Q,
        DEFAULT_UNIFIED_RX_DESC, FLAG_CEIO_LLC_LEAK, FLAG_KV_LOOKUP, FLAG_PHASE_PAUSED,
        FastHist, FlowOrderTracker, HOST_CTRL_ADDR, HostCreditLine, MAX_CARD_CORES, MAX_DRAIN,
        MIN_WIRE_PAYLOAD, OP_DATA, PATH_CEIO_FAST, PATH_CEIO_SLOW, SDP_HDR_LEN, SDP_MBUF_BUF_SIZE,
        SDP_MTU, SwMergeRing2P1C, SwProducer, encode_wire_ack64, splitmix64,
        verify_and_touch_wire_pkt, verify_sdp_credit_safety,
    };
    use testbed::swap_macs;

    const CTRL_CPU: usize = 44;
    const KV_TABLE_WORDS: usize = (32 * 1024 * 1024) / 8; // Exact 32 MiB table
    const KV_TABLE_MASK: usize = KV_TABLE_WORDS - 1;
    const TS_MAX_BUCKETS: usize = 1024;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Mode {
        Unified,
        Ceio,
        LargeSdp,
    }

    impl Mode {
        fn as_str(self) -> &'static str {
            match self {
                Mode::Unified => "unified",
                Mode::Ceio => "ceio",
                Mode::LargeSdp => "large-sdp",
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = Mode::Unified)]
        mode: Mode,
        #[arg(long, default_value = testbed::lease::DEFAULT_ADDR)]
        card: String,
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar4: String,
        /// Number of card data lanes (1..=4). `ceio` opens `2 * lanes` SDP queues (fast + slow).
        #[arg(long, default_value_t = 4)]
        lanes: usize,
        /// Unified mode SDP RX descriptors per queue (default 512 with allow_small_oq=1 -> WMARK=128).
        #[arg(long, default_value_t = DEFAULT_UNIFIED_RX_DESC)]
        unified_rx_desc: u16,
        /// Unified mode logical credit window per queue (default 256 -> Pitfall 1 safe).
        #[arg(long, default_value_t = DEFAULT_UNIFIED_R_EFF_PER_Q)]
        r_eff_per_q: u64,
        /// Unified mode host credit return batch size (fence-free MOVDIR64B every B pkts or idle-flush).
        #[arg(long, default_value_t = DEFAULT_CREDIT_BATCH)]
        credit_batch: u64,
        /// CEIO mode fast-path SDP RX descriptors per lane (default 2048).
        #[arg(long, default_value_t = DEFAULT_CEIO_FAST_RX_DESC)]
        ceio_fast_rx_desc: u16,
        /// CEIO mode slow-path SDP RX descriptors per lane (default 512).
        #[arg(long, default_value_t = DEFAULT_CEIO_SLOW_RX_DESC)]
        ceio_slow_rx_desc: u16,
        /// CEIO mode lazy credit release batch threshold (default 64).
        #[arg(long, default_value_t = DEFAULT_CEIO_LAZY_BATCH)]
        ceio_lazy_batch: u64,
        /// CEIO mode async_recv() slow-path pull window per lane (default 128).
        #[arg(long, default_value_t = DEFAULT_CEIO_PULL_WINDOW)]
        ceio_pull_window: u32,
        /// Large-SDP control mode RX descriptors per lane (default 4096).
        #[arg(long, default_value_t = 4096)]
        large_rx_desc: u16,
        /// SDP TX descriptors per queue (default 2048).
        #[arg(long, default_value_t = 2048)]
        sdp_tx_desc: u16,
        /// Touch every 64 B cache line of every received packet to verify real DDIO hits vs LLC misses.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        touch_payload: bool,
        /// Optional synthetic per-packet CPU work in nanoseconds (models application processing cost).
        #[arg(long, default_value_t = 0)]
        work_ns: u64,
        /// Optional periodic host consumer pause interval in milliseconds (0 = disabled).
        #[arg(long, default_value_t = 0)]
        pause_every_ms: u64,
        /// Optional host consumer pause duration in microseconds when `--pause-every-ms > 0`.
        #[arg(long, default_value_t = 0)]
        pause_us: u64,
        /// Optional dedicated Socket-1 sibling CPU for co-located 32 MiB random-lookup KV tenant (0 = off).
        #[arg(long, default_value_t = 0)]
        coloc_kv_cpu: usize,
        /// Timeseries bucket width in microseconds (default 500 us x 1024 buckets = 512 ms).
        #[arg(long, default_value_t = 500)]
        ts_bucket_us: u64,
        /// Enable 100% non-multiplexed Host Core + Socket-1 CHA/IIO/IMC PMU counters.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        pmu: bool,
        #[arg(long, default_value = HOST_CTRL_ADDR)]
        ctrl: String,
    }

    // -------------------------------------------------------------------------
    // 100% Non-Multiplexed Sapphire Rapids Host PMU (matching MRV-166 / MRV-195)
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

    #[derive(Clone, Copy)]
    struct HostSnap {
        ts_ns: u64,
        cycles: Sample,
        ref_cycles: Sample,
        instr: Sample,
        page_faults: Sample,
        llc_loads: Sample,
        llc_load_misses: Sample,
        dtlb_load_misses: Sample,
        cha_len: usize,
        cha_wcilf: [Sample; 32],
        cha_io_hit: [Sample; 32],
        cha_io_miss: [Sample; 32],
        mesh_clk: Sample,
        iio1_cpu_wr: Sample,
        iio1_inb_wr: Sample,
        iio1_inb_rd: Sample,
        imc_len: usize,
        imc_rd: [Sample; 8],
        imc_wr: [Sample; 8],
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

            let mut imc_rd = Vec::with_capacity(8);
            let mut imc_wr = Vec::with_capacity(8);
            for i in 0..8 {
                if let Ok(t) = read_pmu_type(&format!("uncore_imc_{i}")) {
                    if let (Ok(r), Ok(w)) = (
                        PerfCounter::open(t, IMC_CAS_COUNT_READ, SOCKET1_UNCORE_CPU),
                        PerfCounter::open(t, IMC_CAS_COUNT_WRITE, SOCKET1_UNCORE_CPU),
                    ) {
                        imc_rd.push(r);
                        imc_wr.push(w);
                    }
                }
            }
            Ok(HostPmu {
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

        fn snap(&self, t0: Instant) -> HostSnap {
            let cha_len = self.cha_wcilf.len().min(32);
            let mut cha_wcilf = [Sample::default(); 32];
            let mut cha_io_hit = [Sample::default(); 32];
            let mut cha_io_miss = [Sample::default(); 32];
            for i in 0..cha_len {
                cha_wcilf[i] = self.cha_wcilf[i].read();
                cha_io_hit[i] = self.cha_io_hit[i].read();
                cha_io_miss[i] = self.cha_io_miss[i].read();
            }
            let imc_len = self.imc_rd.len().min(8);
            let mut imc_rd = [Sample::default(); 8];
            let mut imc_wr = [Sample::default(); 8];
            for i in 0..imc_len {
                imc_rd[i] = self.imc_rd[i].read();
                imc_wr[i] = self.imc_wr[i].read();
            }
            HostSnap {
                ts_ns: t0.elapsed().as_nanos() as u64,
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                instr: self.instr.read(),
                page_faults: self.page_faults.read(),
                llc_loads: self.llc_loads.read(),
                llc_load_misses: self.llc_load_misses.read(),
                dtlb_load_misses: self.dtlb_load_misses.read(),
                cha_len,
                cha_wcilf,
                cha_io_hit,
                cha_io_miss,
                mesh_clk: self.mesh_clk.read(),
                iio1_cpu_wr: self.iio1_cpu_wr.read(),
                iio1_inb_wr: self.iio1_inb_wr.read(),
                iio1_inb_rd: self.iio1_inb_rd.read(),
                imc_len,
                imc_rd,
                imc_wr,
            }
        }
    }

    fn format_pmu_delta(a: &HostSnap, b: &HostSnap) -> String {
        let dt_s = ((b.ts_ns.saturating_sub(a.ts_ns)) as f64 * 1e-9).max(1e-9);
        let d_cyc = b.cycles.delta(a.cycles);
        let d_ref = b.ref_cycles.delta(a.ref_cycles);
        let d_ins = b.instr.delta(a.instr);
        let d_pf = b.page_faults.delta(a.page_faults);
        let d_ll = b.llc_loads.delta(a.llc_loads);
        let d_lm = b.llc_load_misses.delta(a.llc_load_misses);
        let d_tlb = b.dtlb_load_misses.delta(a.dtlb_load_misses);
        let d_mesh = b.mesh_clk.delta(a.mesh_clk);
        let d_cpu_wr = b.iio1_cpu_wr.delta(a.iio1_cpu_wr);
        let d_inb_wr = b.iio1_inb_wr.delta(a.iio1_inb_wr);
        let d_inb_rd = b.iio1_inb_rd.delta(a.iio1_inb_rd);

        let mut min_pct = 100.0f64;
        for s in [d_cyc, d_ref, d_ins, d_ll, d_lm, d_tlb, d_mesh, d_cpu_wr, d_inb_wr, d_inb_rd] {
            min_pct = min_pct.min(s.pct());
        }
        let sum_vec = |v_b: &[Sample], v_a: &[Sample], mp: &mut f64| -> u64 {
            let mut sum = 0u64;
            for (sb, sa) in v_b.iter().zip(v_a.iter()) {
                let d = sb.delta(*sa);
                *mp = mp.min(d.pct());
                sum += d.val;
            }
            sum
        };
        let wcilf = sum_vec(&b.cha_wcilf[..b.cha_len], &a.cha_wcilf[..a.cha_len], &mut min_pct);
        let io_hit = sum_vec(&b.cha_io_hit[..b.cha_len], &a.cha_io_hit[..a.cha_len], &mut min_pct);
        let io_miss = sum_vec(&b.cha_io_miss[..b.cha_len], &a.cha_io_miss[..a.cha_len], &mut min_pct);
        let imc_rd = sum_vec(&b.imc_rd[..b.imc_len], &a.imc_rd[..a.imc_len], &mut min_pct);
        let imc_wr = sum_vec(&b.imc_wr[..b.imc_len], &a.imc_wr[..a.imc_len], &mut min_pct);

        let cpu_ghz = if d_ref.val > 0 { (d_cyc.val as f64 / d_ref.val as f64) * (REF_HZ / 1e9) } else { 0.0 };
        let mesh_ghz = (d_mesh.val as f64 / dt_s) / 1e9;
        let ddio_hit_pct = if io_hit + io_miss > 0 {
            100.0 * (io_hit as f64) / ((io_hit + io_miss) as f64)
        } else {
            100.0
        };
        let llc_miss_pct = if d_ll.val > 0 { 100.0 * (d_lm.val as f64) / (d_ll.val as f64) } else { 0.0 };

        format!(
            "{{\"dt_s\":{dt_s:.6},\"cycles\":{},\"ref_cycles\":{},\"instr\":{},\"page_faults\":{},\"llc_loads\":{},\"llc_load_misses\":{},\"llc_miss_pct\":{llc_miss_pct:.4},\"dtlb_load_misses\":{},\"cha_wcilf\":{wcilf},\"cha_io_hit\":{io_hit},\"cha_io_miss\":{io_miss},\"ddio_hit_pct\":{ddio_hit_pct:.4},\"iio1_cpu_wr\":{},\"iio1_inb_wr\":{},\"iio1_inb_rd\":{},\"imc_rd\":{imc_rd},\"imc_wr\":{imc_wr},\"cpu_ghz\":{cpu_ghz:.4},\"mesh_ghz\":{mesh_ghz:.4},\"min_running_pct\":{min_pct:.3}}}",
            d_cyc.val,
            d_ref.val,
            d_ins.val,
            d_pf.val,
            d_ll.val,
            d_lm.val,
            d_tlb.val,
            d_cpu_wr.val,
            d_inb_wr.val,
            d_inb_rd.val,
        )
    }

    // -------------------------------------------------------------------------
    // Timeseries bucket & Host Statistics Snapshot
    // -------------------------------------------------------------------------
    #[derive(Clone, Copy, Default)]
    struct TsBucket {
        rx_pkts: u64,
        fast_pkts: u64,
        slow_pkts: u64,
        leak_pkts: u64,
        phase_paused_pkts: u64,
        rpc_pkts: u64,
    }

    #[derive(Clone, Default)]
    struct HostStatsSnap {
        rx_pkts: u64,
        rx_bytes: u64,
        fast_rx_pkts: u64,
        slow_rx_pkts: u64,
        rpc_rx_pkts: u64,
        bulk_rx_pkts: u64,
        leak_flagged_pkts: u64,
        phase_paused_pkts: u64,
        acks_sent: u64,
        tx_drops: u64,
        bad: u64,
        torn: u64,
        ooo: u64,
        dup: u64,
        gap: u64,
        credit_writes: u64,
        pull_writes: u64,
        sw_fast_merged: u64,
        sw_slow_merged: u64,
        sw_phase_switches: u64,
        pauses_injected: u64,
        kv_lookups: u64,
        ts_buckets: Vec<TsBucket>,
    }
    impl HostStatsSnap {
        fn with_prealloc_buckets() -> Self {
            let mut s = HostStatsSnap::default();
            s.ts_buckets = vec![TsBucket::default(); TS_MAX_BUCKETS];
            // Write non-zero bytes to every backing page to fault out Linux calloc zero-pages up front.
            for b in &mut s.ts_buckets {
                b.rx_pkts = 1;
            }
            std::hint::black_box(&s.ts_buckets);
            for b in &mut s.ts_buckets {
                *b = TsBucket::default();
            }
            s.ts_buckets.clear();
            s
        }

        fn reset_in_place(&mut self) {
            self.rx_pkts = 0;
            self.rx_bytes = 0;
            self.fast_rx_pkts = 0;
            self.slow_rx_pkts = 0;
            self.rpc_rx_pkts = 0;
            self.bulk_rx_pkts = 0;
            self.leak_flagged_pkts = 0;
            self.phase_paused_pkts = 0;
            self.acks_sent = 0;
            self.tx_drops = 0;
            self.bad = 0;
            self.torn = 0;
            self.ooo = 0;
            self.dup = 0;
            self.gap = 0;
            self.credit_writes = 0;
            self.pull_writes = 0;
            self.sw_fast_merged = 0;
            self.sw_slow_merged = 0;
            self.sw_phase_switches = 0;
            self.pauses_injected = 0;
            self.kv_lookups = 0;
            self.ts_buckets.clear();
        }

        fn copy_from(&mut self, src: &HostStatsSnap) {
            self.rx_pkts = src.rx_pkts;
            self.rx_bytes = src.rx_bytes;
            self.fast_rx_pkts = src.fast_rx_pkts;
            self.slow_rx_pkts = src.slow_rx_pkts;
            self.rpc_rx_pkts = src.rpc_rx_pkts;
            self.bulk_rx_pkts = src.bulk_rx_pkts;
            self.leak_flagged_pkts = src.leak_flagged_pkts;
            self.phase_paused_pkts = src.phase_paused_pkts;
            self.acks_sent = src.acks_sent;
            self.tx_drops = src.tx_drops;
            self.bad = src.bad;
            self.torn = src.torn;
            self.ooo = src.ooo;
            self.dup = src.dup;
            self.gap = src.gap;
            self.credit_writes = src.credit_writes;
            self.pull_writes = src.pull_writes;
            self.sw_fast_merged = src.sw_fast_merged;
            self.sw_slow_merged = src.sw_slow_merged;
            self.sw_phase_switches = src.sw_phase_switches;
            self.pauses_injected = src.pauses_injected;
            self.kv_lookups = src.kv_lookups;
            self.ts_buckets.clear();
            self.ts_buckets.extend_from_slice(&src.ts_buckets);
        }
    }


    #[derive(Clone, Default)]
    struct ColocKvSnap {
        enabled: bool,
        queries: u64,
        p50_ns: f64,
        p99_ns: f64,
        p999_ns: f64,
        max_ns: f64,
    }

    fn pin_thread(cpu: usize) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu, &mut set);
            libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set);
        }
    }

    fn current_cpu() -> i32 {
        unsafe { libc::sched_getcpu() }
    }

    struct PktCtx {
        touch_payload: bool,
        kv_ptr_usize: usize,
        work_ticks: u64,
        ts_start_tsc: u64,
        ts_bucket_ticks: u64,
    }

    #[inline(always)]
    fn process_pkt<'e>(
        mut pkt: Mbuf<'e>,
        lane: usize,
        tx_b: &mut Burst<'e, MAX_DRAIN>,
        port: &Port<'e>,
        order: &mut FlowOrderTracker,
        st: &mut HostStatsSnap,
        ctx: &PktCtx,
    ) {
        let flen = pkt.data_len();
        if flen < SDP_HDR_LEN + MIN_WIRE_PAYLOAD {
            st.bad += 1;
            return;
        }
        let pay_len = flen - SDP_HDR_LEN;
        let parsed = verify_and_touch_wire_pkt(&pkt.data()[SDP_HDR_LEN..flen], ctx.touch_payload);
        let (meta, client_tsc, flow_id, flow_seq, kv_key, burst_epoch, _card_tsc, mut aux) = match parsed {
            Ok(v) => v,
            Err(true) => {
                st.torn += 1;
                return;
            }
            Err(false) => {
                st.bad += 1;
                return;
            }
        };
        if meta.op != OP_DATA {
            st.bad += 1;
            return;
        }

        let prev_ooo = order.ooo_count;
        order.observe(flow_id, flow_seq);
        if order.ooo_count > prev_ooo && order.ooo_count <= 5 {
            eprintln!(
                "HOST_OOO #{}: lane={lane} flow={flow_id} seq={flow_seq} path={} flags={:#x}",
                order.ooo_count, meta.path_tag, meta.flags
            );
        }
        st.rx_pkts += 1;
        st.rx_bytes += pay_len as u64;
        if meta.path_tag == PATH_CEIO_SLOW {
            st.slow_rx_pkts += 1;
        } else {
            st.fast_rx_pkts += 1;
        }
        if meta.pkt_class == CLASS_RPC_HI {
            st.rpc_rx_pkts += 1;
        } else {
            st.bulk_rx_pkts += 1;
        }
        if (meta.flags & FLAG_CEIO_LLC_LEAK) != 0 {
            st.leak_flagged_pkts += 1;
        }
        if (meta.flags & FLAG_PHASE_PAUSED) != 0 {
            st.phase_paused_pkts += 1;
        }

        let now_tsc = rdtsc();
        let bidx = (now_tsc.saturating_sub(ctx.ts_start_tsc) / ctx.ts_bucket_ticks) as usize;
        if bidx < TS_MAX_BUCKETS {
            if st.ts_buckets.len() <= bidx {
                st.ts_buckets.resize(bidx + 1, TsBucket::default());
            }
            let b = &mut st.ts_buckets[bidx];
            b.rx_pkts += 1;
            if meta.path_tag == PATH_CEIO_SLOW {
                b.slow_pkts += 1;
            } else if meta.path_tag == PATH_CEIO_FAST {
                b.fast_pkts += 1;
            }
            if (meta.flags & FLAG_CEIO_LLC_LEAK) != 0 {
                b.leak_pkts += 1;
            }
            if (meta.flags & FLAG_PHASE_PAUSED) != 0 {
                b.phase_paused_pkts += 1;
            }
            if meta.pkt_class == CLASS_RPC_HI {
                b.rpc_pkts += 1;
            }
        }

        if (meta.flags & FLAG_KV_LOOKUP) != 0 {
            let kidx = (splitmix64(kv_key as u64) as usize) & KV_TABLE_MASK;
            let v = unsafe { *((ctx.kv_ptr_usize as *const u64).add(kidx)) };
            aux ^= v;
            st.kv_lookups += 1;
        }

        if ctx.work_ticks > 0 {
            let deadline = rdtsc().saturating_add(ctx.work_ticks);
            while rdtsc() < deadline {
                std::hint::spin_loop();
            }
        }

        let target_flen = SDP_HDR_LEN + MIN_WIRE_PAYLOAD;
        if flen > target_flen {
            let _ = pkt.trim((flen - target_flen) as u16);
        }
        {
            let frame = pkt.data_mut();
            swap_macs(frame);
            encode_wire_ack64(
                &mut frame[SDP_HDR_LEN..target_flen],
                meta.global_seq,
                client_tsc,
                flow_id,
                flow_seq,
                kv_key,
                burst_epoch,
                meta.pkt_class,
                meta.path_tag,
                meta.flags,
                aux,
            );
        }
        if let Err(m_back) = tx_b.push(pkt) {
            let mut retries = 0u32;
            while !tx_b.is_empty() {
                if port.tx(lane as u16, tx_b) == 0 {
                    retries += 1;
                    if retries > 200_000 {
                        st.tx_drops += tx_b.len() as u64;
                        tx_b.clear();
                        break;
                    }
                }
            }
            let _ = tx_b.push(m_back);
        }
        st.acks_sent += 1;
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

        if args.lanes == 0 || args.lanes > MAX_CARD_CORES {
            return Err(format!("lanes must be 1..={MAX_CARD_CORES}, got {}", args.lanes).into());
        }
        if args.mode == Mode::Unified
            && !verify_sdp_credit_safety(args.unified_rx_desc, args.r_eff_per_q, args.credit_batch)
        {
            return Err(format!(
                "Pitfall 1 violation: unified_rx_desc={} r_eff_per_q={} credit_batch={} would risk SDP WMARK or deadlock",
                args.unified_rx_desc, args.r_eff_per_q, args.credit_batch
            )
            .into());
        }
        let use_bar4 = matches!(args.mode, Mode::Unified | Mode::Ceio);
        if use_bar4 && !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B support".into());
        }

        let (eal, _) = Eal::init(eal_args)?;
        let tsc_hz = eal.tsc_hz() as f64;
        let ticks_to_ns = 1e9 / tsc_hz;
        let work_ticks = ((args.work_ns as f64) * 1e-9 * tsc_hz).round() as u64;
        let pause_every_ticks = ((args.pause_every_ms as f64) * 1e-3 * tsc_hz).round() as u64;
        let pause_dur_ticks = ((args.pause_us as f64) * 1e-6 * tsc_hz).round() as u64;
        let ts_bucket_ticks = ((args.ts_bucket_us as f64) * 1e-6 * tsc_hz).round().max(1.0) as u64;

        let worker_cpu = current_cpu();
        let port_id: u16 = 0;
        let sock = Port::socket_of(&eal, port_id);

        // Configure standard SDP queues & mempools cleanly per mode
        let enable_promisc_best_effort = |p: &Port<'_>| -> Result<(), Box<dyn Error>> {
            if let Err(e) = p.promiscuous() {
                if e.errno() != libc::EOPNOTSUPP {
                    return Err(e.into());
                }
            }
            Ok(())
        };

        let (total_sdp_queues, fast_pool, slow_pool_opt, port) = match args.mode {
            Mode::Unified => {
                // Pitfall 2: compact DDIO-hot RX pool (accounts for octeon_ep TX flush threshold OTX_EP_MAX_INSTR=256/q)
                let pool_sz = (args.lanes as u32) * (args.unified_rx_desc as u32 + 512) + 511;
                let fp = Mempool::new_with_cache(&eal, "m211h_uni_pool", pool_sz, 128, SDP_MBUF_BUF_SIZE, sock)?;
                let p = Port::start_with_opts_rss_fc(
                    &eal,
                    port_id,
                    &fp,
                    args.lanes as u16,
                    args.unified_rx_desc,
                    args.sdp_tx_desc,
                    Some(SDP_MTU),
                    false,
                    false,
                )?;
                enable_promisc_best_effort(&p)?;
                (args.lanes, fp, None, p)
            }
            Mode::Ceio => {
                // Dual-ring CEIO host setup on standard SDP:
                //   Q[0..lanes-1]       = Fast Path Ring (legacy passive size, e.g. 2048 desc)
                //   Q[lanes..2*lanes-1] = Slow Path Ring (async pull ring, e.g. 512 desc)
                let nq = args.lanes * 2;
                let fast_sz = (args.lanes as u32) * (args.ceio_fast_rx_desc as u32 + 512) + 511;
                let slow_sz = (args.lanes as u32) * (args.ceio_slow_rx_desc as u32 + 512) + 511;
                let fp = Mempool::new_with_cache(&eal, "m211h_ceio_fp", fast_sz, 256, SDP_MBUF_BUF_SIZE, sock)?;
                let sp = Mempool::new_with_cache(&eal, "m211h_ceio_sp", slow_sz, 128, SDP_MBUF_BUF_SIZE, sock)?;
                let mut pools: Vec<&Mempool<'_>> = Vec::with_capacity(nq);
                let mut rx_descs: Vec<u16> = Vec::with_capacity(nq);
                for _ in 0..args.lanes {
                    pools.push(&fp);
                    rx_descs.push(args.ceio_fast_rx_desc);
                }
                for _ in 0..args.lanes {
                    pools.push(&sp);
                    rx_descs.push(args.ceio_slow_rx_desc);
                }
                let p = Port::start_with_queue_cfg(&eal, port_id, &pools, &rx_descs, args.sdp_tx_desc, Some(SDP_MTU), false, false)?;
                enable_promisc_best_effort(&p)?;
                (nq, fp, Some(sp), p)
            }
            Mode::LargeSdp => {
                // Uncontrolled passive large SDP baseline (128+ MiB pool footprint)
                let pool_sz = 65535u32;
                let fp = Mempool::new_with_cache(&eal, "m211h_large_pool", pool_sz, 256, SDP_MBUF_BUF_SIZE, sock)?;
                let p = Port::start_with_opts_rss_fc(
                    &eal,
                    port_id,
                    &fp,
                    args.lanes as u16,
                    args.large_rx_desc,
                    args.sdp_tx_desc,
                    Some(SDP_MTU),
                    false,
                    false,
                )?;
                enable_promisc_best_effort(&p)?;
                (args.lanes, fp, None, p)
            }
        };
        let _keep_pools = (&fast_pool, &slow_pool_opt);

        // 32 MiB NUMA-1 hugepage table for RPC KV lookup (Workload 3)
        let kv_mz = Memzone::reserve_contiguous(&eal, "m211h_kv32m", KV_TABLE_WORDS * 8, sock, 2 << 20)?;
        let kv_ptr = kv_mz.as_ptr().cast::<u64>();
        for i in 0..KV_TABLE_WORDS {
            unsafe { *kv_ptr.add(i) = splitmix64((i as u64) ^ 0x211_feed_beef) };
        }
        let kv_ptr_usize = kv_ptr as usize;

        // Lock all current & future pages in RAM before opening PMU to guarantee zero cpu40 page faults
        unsafe {
            let _ = libc::mlockall(libc::MCL_CURRENT | libc::MCL_FUTURE);
        }

        // Acquire BAR4 slot 15 lease (used by BOTH unified & ceio for fence-free MOVDIR64B!)
        let lease_opt = if use_bar4 { Some(Lease::acquire(&args.card)?) } else { None };
        let slot_opt = if let Some(l) = lease_opt.as_ref() {
            Some(l.map_slot(&args.bar4)?)
        } else {
            None
        };

        let host_pmu = if args.pmu { Some(HostPmu::open(worker_cpu)?) } else { None };
        let t_origin = Instant::now();
        let pmu_start: Arc<Mutex<Option<HostSnap>>> = Arc::new(Mutex::new(host_pmu.as_ref().map(|p| p.snap(t_origin))));
        let pmu_latest: Arc<Mutex<Option<HostSnap>>> = Arc::new(Mutex::new(host_pmu.as_ref().map(|p| p.snap(t_origin))));

        let quit = Arc::new(AtomicBool::new(false));
        let reset_gen = Arc::new(AtomicU64::new(0));
        let active_epoch = Arc::new(AtomicU64::new(0));
        let reset_ack = Arc::new(AtomicU64::new(0));
        let stats_snap = Arc::new(Mutex::new(HostStatsSnap::with_prealloc_buckets()));
        let coloc_reset_gen = Arc::new(AtomicU64::new(0));
        let coloc_snap = Arc::new(Mutex::new(ColocKvSnap { enabled: args.coloc_kv_cpu > 0, ..Default::default() }));
        let port_stats_snap = Arc::new(Mutex::new(EthStats::default()));

        {
            let q = Arc::clone(&quit);
            ctrlc::set_handler(move || q.store(true, Ordering::Relaxed))?;
        }

        // Optional co-located 32 MiB cache-sensitive KV tenant on dedicated Socket-1 sibling core
        let coloc_handle = if args.coloc_kv_cpu > 0 {
            let cpu = args.coloc_kv_cpu;
            let q = Arc::clone(&quit);
            let crg = Arc::clone(&coloc_reset_gen);
            let csnap = Arc::clone(&coloc_snap);
            Some(std::thread::spawn(move || {
                pin_thread(cpu);
                let mut tbl = vec![0u64; KV_TABLE_WORDS];
                for (i, w) in tbl.iter_mut().enumerate() {
                    *w = splitmix64((i as u64) ^ 0x7777_2110);
                }
                let mut rng = 0x9e37_79b9_7f4a_7c15u64;
                let mut hist = FastHist::new();
                let mut local_rg = 0u64;
                let mut acc = 0u64;
                let mut iter = 0u64;
                while !q.load(Ordering::Relaxed) {
                    let rg = crg.load(Ordering::Acquire);
                    if rg != local_rg {
                        hist.reset();
                        local_rg = rg;
                        *csnap.lock().unwrap() = ColocKvSnap { enabled: true, ..Default::default() };
                    }
                    let t0 = rdtsc_after_loads();
                    rng = splitmix64(rng ^ acc);
                    let idx = (rng as usize) & KV_TABLE_MASK;
                    let v = unsafe { *tbl.get_unchecked(idx) };
                    acc = acc.wrapping_add(v);
                    let t1 = rdtsc_after_loads();
                    let ns = ((t1.saturating_sub(t0)) as f64 * ticks_to_ns) as u64;
                    hist.record_ns(ns);
                    iter = iter.wrapping_add(1);
                    if (iter & 0x3fff) == 0 {
                        std::hint::black_box(acc);
                        if let Ok(mut g) = csnap.try_lock() {
                            g.queries = hist.count();
                            g.p50_ns = hist.pct_us(0.50) * 1000.0;
                            g.p99_ns = hist.pct_us(0.99) * 1000.0;
                            g.p999_ns = hist.pct_us(0.999) * 1000.0;
                            g.max_ns = hist.max_us() * 1000.0;
                        }
                    }
                }
            }))
        } else {
            None
        };

        // Non-hot TCP control thread on cpu44 (`PING`, `RESET`, `STATS`, `QUIT`)
        let ctrl_listener = TcpListener::bind(&args.ctrl)?;
        ctrl_listener.set_nonblocking(true)?;
        let ctrl_quit = Arc::clone(&quit);
        let ctrl_rg = Arc::clone(&reset_gen);
        let ctrl_epoch = Arc::clone(&active_epoch);
        let ctrl_ra = Arc::clone(&reset_ack);
        let ctrl_crg = Arc::clone(&coloc_reset_gen);
        let ctrl_stats = Arc::clone(&stats_snap);
        let ctrl_coloc = Arc::clone(&coloc_snap);
        let ctrl_pstats = Arc::clone(&port_stats_snap);
        let ctrl_pmu_a = Arc::clone(&pmu_start);
        let ctrl_pmu_b = Arc::clone(&pmu_latest);
        let mode_str = args.mode.as_str();
        let lanes = args.lanes;
        let ts_bucket_us = args.ts_bucket_us;

        let ctrl_handle = std::thread::spawn(move || {
            pin_thread(CTRL_CPU);
            while !ctrl_quit.load(Ordering::Relaxed) {
                match ctrl_listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_nodelay(true);
                        let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
                        let mut reader = BufReader::new(stream.try_clone().unwrap());
                        let mut line = String::new();
                        if reader.read_line(&mut line).is_ok() {
                            let cmd = line.trim();
                            let resp = if cmd == "PING" {
                                format!("{{\"ok\":true,\"mode\":\"{mode_str}\",\"lanes\":{lanes}}}\n")
                            } else if cmd == "RESET" || cmd.starts_with("RESET ") {
                                let ep = cmd
                                    .strip_prefix("RESET ")
                                    .and_then(|s| s.trim().parse::<u64>().ok())
                                    .unwrap_or_else(|| ctrl_epoch.load(Ordering::Acquire).wrapping_add(1));
                                ctrl_epoch.store(ep, Ordering::Release);
                                ctrl_crg.fetch_add(1, Ordering::AcqRel);
                                let next = ctrl_rg.fetch_add(1, Ordering::AcqRel) + 1;
                                let dl = Instant::now() + Duration::from_secs(3);
                                while ctrl_ra.load(Ordering::Acquire) < next && Instant::now() < dl {
                                    std::thread::sleep(Duration::from_millis(1));
                                }
                                format!("{{\"ok\":true,\"epoch\":{ep}}}\n")
                            } else if cmd == "STATS" {
                                let s = ctrl_stats.lock().unwrap().clone();
                                let c = ctrl_coloc.lock().unwrap().clone();
                                let ps = *ctrl_pstats.lock().unwrap();
                                let pmu_str = match (
                                    ctrl_pmu_a.lock().unwrap().as_ref(),
                                    ctrl_pmu_b.lock().unwrap().as_ref(),
                                ) {
                                    (Some(a), Some(b)) => format_pmu_delta(a, b),
                                    _ => "null".to_string(),
                                };
                                let ts_parts: Vec<String> = s
                                    .ts_buckets
                                    .iter()
                                    .map(|b| {
                                        format!(
                                            "[{},{},{},{},{},{}]",
                                            b.rx_pkts,
                                            b.fast_pkts,
                                            b.slow_pkts,
                                            b.leak_pkts,
                                            b.phase_paused_pkts,
                                            b.rpc_pkts
                                        )
                                    })
                                    .collect();
                                format!(
                                    "{{\"mode\":\"{mode_str}\",\"lanes\":{lanes},\"rx_pkts\":{},\"rx_bytes\":{},\"fast_rx_pkts\":{},\"slow_rx_pkts\":{},\"rpc_rx_pkts\":{},\"bulk_rx_pkts\":{},\"leak_flagged_pkts\":{},\"phase_paused_pkts\":{},\"acks_sent\":{},\"tx_drops\":{},\"bad\":{},\"torn\":{},\"ooo\":{},\"dup\":{},\"gap\":{},\"credit_writes\":{},\"pull_writes\":{},\"sw_fast_merged\":{},\"sw_slow_merged\":{},\"sw_phase_switches\":{},\"pauses_injected\":{},\"kv_lookups\":{},\"coloc_kv\":{{\"enabled\":{},\"queries\":{},\"p50_ns\":{:.2},\"p99_ns\":{:.2},\"p999_ns\":{:.2},\"max_ns\":{:.2}}},\"sdp_port_stats\":{{\"ipackets\":{},\"opackets\":{},\"ibytes\":{},\"obytes\":{},\"imissed\":{},\"ierrors\":{},\"oerrors\":{},\"rx_nombuf\":{}}},\"pmu\":{},\"ts_bucket_us\":{ts_bucket_us},\"timeseries\":[{}]}}\n",
                                    s.rx_pkts,
                                    s.rx_bytes,
                                    s.fast_rx_pkts,
                                    s.slow_rx_pkts,
                                    s.rpc_rx_pkts,
                                    s.bulk_rx_pkts,
                                    s.leak_flagged_pkts,
                                    s.phase_paused_pkts,
                                    s.acks_sent,
                                    s.tx_drops,
                                    s.bad,
                                    s.torn,
                                    s.ooo,
                                    s.dup,
                                    s.gap,
                                    s.credit_writes,
                                    s.pull_writes,
                                    s.sw_fast_merged,
                                    s.sw_slow_merged,
                                    s.sw_phase_switches,
                                    s.pauses_injected,
                                    s.kv_lookups,
                                    c.enabled,
                                    c.queries,
                                    c.p50_ns,
                                    c.p99_ns,
                                    c.p999_ns,
                                    c.max_ns,
                                    ps.ipackets,
                                    ps.opackets,
                                    ps.ibytes,
                                    ps.obytes,
                                    ps.imissed,
                                    ps.ierrors,
                                    ps.oerrors,
                                    ps.rx_nombuf,
                                    pmu_str,
                                    ts_parts.join(",")
                                )
                            } else if cmd == "QUIT" {
                                ctrl_quit.store(true, Ordering::Relaxed);
                                "{\"ok\":true}\n".to_string()
                            } else {
                                format!("{{\"ok\":false,\"err\":\"unknown cmd {cmd}\"}}\n")
                            };
                            let _ = stream.write_all(resp.as_bytes());
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

        // Main hot-path worker loop on cpu40
        let mut taken_q = [0u64; 4];
        let mut reported_taken_total = 0u64;
        let mut credit_gen = 0u64;
        let mut slow_head_q = [0u64; 4];
        let mut pull_target_q = [args.ceio_pull_window; 4];
        let mut pull_gen = 0u64;
        let mut sw_ring: SwMergeRing2P1C<Mbuf<'_>> = SwMergeRing2P1C::new(4096);
        let mut order = FlowOrderTracker::new();
        for fid in 0..mrv211::MAX_FLOWS {
            order.observe(fid as u16, 1);
        }
        order.reset();

        let mut rx_burst: Burst<'_, MAX_DRAIN> = Burst::new();
        let mut tx_bursts: Vec<Burst<'_, MAX_DRAIN>> = (0..args.lanes).map(|_| Burst::new()).collect();
        let mut st = HostStatsSnap::with_prealloc_buckets();
        let mut ts_start_tsc = rdtsc_after_loads();
        let mut last_pause_tsc = ts_start_tsc;
        let mut last_credit_post_tsc = ts_start_tsc;
        let ceio_credit_flush_ticks = (20.0e-6 * tsc_hz).round().max(1.0) as u64;
        let mut local_rg = 0u64;
        let mut local_epoch = 0u64;
        let mut iter = 0u64;

        // Helper: post fence-free MOVDIR64B HostCreditLine strictly guarded by LeasedSlot::check() (AGENTS.md MRV-26)
        let post_credit = |slot_o: &Option<LeasedSlot<'_>>,
                           gen_ctr: &mut u64,
                           taken: [u64; 4],
                           epoch: u64,
                           st_wr: &mut u64|
         -> Result<(), String> {
            if let Some(slot) = slot_o.as_ref() {
                slot.check()?;
                *gen_ctr = gen_ctr.wrapping_add(1);
                let line = HostCreditLine::new(*gen_ctr, taken, epoch, 1);
                unsafe { movdir64b(slot.as_ptr().add(BAR4_HOST_CREDIT_OFF), &line) };
                *st_wr += 1;
            }
            Ok(())
        };
        let post_ceio_pull = |slot_o: &Option<LeasedSlot<'_>>,
                              gen_ctr: &mut u64,
                              epoch: u64,
                              shead: [u64; 4],
                              ptgt: [u32; 4],
                              st_wr: &mut u64|
         -> Result<(), String> {
            if let Some(slot) = slot_o.as_ref() {
                slot.check()?;
                *gen_ctr = gen_ctr.wrapping_add(1);
                let line = CeioSlowPullLine::new(*gen_ctr, epoch, shead, ptgt);
                unsafe { movdir64b(slot.as_ptr().add(BAR4_CEIO_PULL_OFF), &line) };
                *st_wr += 1;
            }
            Ok(())
        };

        if args.mode == Mode::Ceio {
            post_ceio_pull(&slot_opt, &mut pull_gen, local_epoch, slow_head_q, pull_target_q, &mut st.pull_writes)?;
        }

        let _ = port.stats_reset();
        println!(
            "MRV211_HOST READY mode={} cpu={} lanes={} total_sdp_q={} coloc_kv_cpu={}",
            args.mode.as_str(),
            worker_cpu,
            args.lanes,
            total_sdp_queues,
            args.coloc_kv_cpu
        );
        let _ = io::stdout().flush();

        while !quit.load(Ordering::Relaxed) {
            if let Some(l) = lease_opt.as_ref() {
                if l.revoked() {
                    eprintln!("mrv211_host: BAR4 lease revoked, stopping");
                    break;
                }
            }

            let rg = reset_gen.load(Ordering::Acquire);
            if rg != local_rg {
                // Drain any straggling pre-reset SDP packets so warmup packets cannot leak into the new epoch.
                for q in 0..total_sdp_queues {
                    while port.rx(q as u16, &mut rx_burst) > 0 {
                        rx_burst.clear();
                    }
                }
                sw_ring.reset();
                order.reset();
                for tb in &mut tx_bursts {
                    tb.clear();
                }
                local_epoch = active_epoch.load(Ordering::Acquire);
                taken_q = [0u64; 4];
                reported_taken_total = 0;
                slow_head_q = [0u64; 4];
                pull_target_q = [args.ceio_pull_window; 4];
                st.reset_in_place();
                let _ = port.stats_reset();
                if use_bar4 {
                    if let Err(e) = post_credit(&slot_opt, &mut credit_gen, taken_q, local_epoch, &mut st.credit_writes) {
                        eprintln!("mrv211_host: {e}");
                        break;
                    }
                }
                if args.mode == Mode::Ceio {
                    if let Err(e) = post_ceio_pull(
                        &slot_opt,
                        &mut pull_gen,
                        local_epoch,
                        slow_head_q,
                        pull_target_q,
                        &mut st.pull_writes,
                    ) {
                        eprintln!("mrv211_host: {e}");
                        break;
                    }
                }
                if let Some(p) = host_pmu.as_ref() {
                    let sn = p.snap(t_origin);
                    *pmu_start.lock().unwrap() = Some(sn.clone());
                    *pmu_latest.lock().unwrap() = Some(sn);
                }
                ts_start_tsc = rdtsc_after_loads();
                last_pause_tsc = ts_start_tsc;
                local_rg = rg;
                reset_ack.store(rg, Ordering::Release);
                continue;
            }

            // Optional deterministic host consumer pause injection (Workload 1 burst buildup)
            if pause_every_ticks > 0 && pause_dur_ticks > 0 {
                let now = rdtsc();
                if now.saturating_sub(last_pause_tsc) >= pause_every_ticks {
                    let end = now.saturating_add(pause_dur_ticks);
                    while rdtsc() < end {
                        std::hint::spin_loop();
                    }
                    last_pause_tsc = rdtsc();
                    st.pauses_injected += 1;
                }
            }

            let mut handled_this_iter = 0u64;
            let mut slow_handled_this_iter = 0u64;

            let pkt_ctx = PktCtx {
                touch_payload: args.touch_payload,
                kv_ptr_usize,
                work_ticks,
                ts_start_tsc,
                ts_bucket_ticks,
            };

            match args.mode {
                Mode::Unified | Mode::LargeSdp => {
                    // Single flat polling loop over SDP Q[0..lanes-1] — zero SW ring, zero async_recv()!
                    for q in 0..args.lanes {
                        if port.rx(q as u16, &mut rx_burst) > 0 {
                            let n = rx_burst.len() as u64;
                            rx_burst.drain_into(|pkt| {
                                process_pkt(pkt, q, &mut tx_bursts[q], &port, &mut order, &mut st, &pkt_ctx);
                            });
                            taken_q[q] += n;
                            handled_this_iter += n;
                        }
                    }
                    // Fence-free MOVDIR64B credit return (batched + idle-flush)
                    if args.mode == Mode::Unified {
                        let taken_total: u64 = taken_q.iter().sum();
                        let delta = taken_total.saturating_sub(reported_taken_total);
                        if delta >= args.credit_batch || (handled_this_iter == 0 && delta > 0) {
                            if let Err(e) =
                                post_credit(&slot_opt, &mut credit_gen, taken_q, local_epoch, &mut st.credit_writes)
                            {
                                eprintln!("mrv211_host: {e}");
                                break;
                            }
                            reported_taken_total = taken_total;
                        }
                    }
                }

                Mode::Ceio => {
                    // CEIO §4.2 async_recv():
                    // 1) Poll Fast-Path HW rings Q[0..lanes-1] -> 2P1C SW ring first (phase exclusivity!)
                    for q in 0..args.lanes {
                        if port.rx(q as u16, &mut rx_burst) > 0 {
                            rx_burst.drain_into(|pkt| {
                                if let Err(pkt_back) = sw_ring.push_from(SwProducer::FastPath, q as u8, pkt) {
                                    if let Some(ev) = sw_ring.pop() {
                                        let el = ev.lane as usize;
                                        process_pkt(
                                            ev.item,
                                            el,
                                            &mut tx_bursts[el],
                                            &port,
                                            &mut order,
                                            &mut st,
                                            &pkt_ctx,
                                        );
                                        if ev.producer == SwProducer::FastPath {
                                            taken_q[el] += 1;
                                            handled_this_iter += 1;
                                        } else {
                                            slow_head_q[el] += 1;
                                            slow_handled_this_iter += 1;
                                        }
                                    }
                                    let _ = sw_ring.push_from(SwProducer::FastPath, q as u8, pkt_back);
                                }
                            });
                        }
                    }
                    // 2) Poll Slow-Path HW rings Q[lanes..2*lanes-1] -> 2P1C SW ring
                    for q in 0..args.lanes {
                        if port.rx((q + args.lanes) as u16, &mut rx_burst) > 0 {
                            rx_burst.drain_into(|pkt| {
                                if let Err(pkt_back) = sw_ring.push_from(SwProducer::SlowPath, q as u8, pkt) {
                                    if let Some(ev) = sw_ring.pop() {
                                        let el = ev.lane as usize;
                                        process_pkt(
                                            ev.item,
                                            el,
                                            &mut tx_bursts[el],
                                            &port,
                                            &mut order,
                                            &mut st,
                                            &pkt_ctx,
                                        );
                                        if ev.producer == SwProducer::FastPath {
                                            taken_q[el] += 1;
                                            handled_this_iter += 1;
                                        } else {
                                            slow_head_q[el] += 1;
                                            slow_handled_this_iter += 1;
                                        }
                                    }
                                    let _ = sw_ring.push_from(SwProducer::SlowPath, q as u8, pkt_back);
                                }
                            });
                        }
                    }
                    // 3) Drain unified 2P1C SW ring into application
                    while let Some(ev) = sw_ring.pop() {
                        let el = ev.lane as usize;
                        process_pkt(ev.item, el, &mut tx_bursts[el], &port, &mut order, &mut st, &pkt_ctx);
                        if ev.producer == SwProducer::FastPath {
                            taken_q[el] += 1;
                            handled_this_iter += 1;
                        } else {
                            slow_head_q[el] += 1;
                            slow_handled_this_iter += 1;
                        }
                    }
                    // 4) Lazy fast-path credit release (with per-pull and 20 us bounded flush) + slow-path head & pull-window advance
                    let taken_total: u64 = taken_q.iter().sum();
                    let delta = taken_total.saturating_sub(reported_taken_total);
                    let now_c = rdtsc();
                    if delta >= args.ceio_lazy_batch
                        || (delta > 0
                            && (handled_this_iter == 0
                                || slow_handled_this_iter > 0
                                || now_c.saturating_sub(last_credit_post_tsc) >= ceio_credit_flush_ticks))
                    {
                        if let Err(e) =
                            post_credit(&slot_opt, &mut credit_gen, taken_q, local_epoch, &mut st.credit_writes)
                        {
                            eprintln!("mrv211_host: {e}");
                            break;
                        }
                        reported_taken_total = taken_total;
                        last_credit_post_tsc = now_c;
                    }
                    if slow_handled_this_iter > 0 {
                        for q in 0..args.lanes {
                            pull_target_q[q] = (slow_head_q[q] as u32).saturating_add(args.ceio_pull_window);
                        }
                        if let Err(e) = post_ceio_pull(
                            &slot_opt,
                            &mut pull_gen,
                            local_epoch,
                            slow_head_q,
                            pull_target_q,
                            &mut st.pull_writes,
                        ) {
                            eprintln!("mrv211_host: {e}");
                            break;
                        }
                    }
                }
            }

            // Flush any queued SDP TX ACK bursts
            for q in 0..args.lanes {
                if !tx_bursts[q].is_empty() {
                    let mut retries = 0u32;
                    while !tx_bursts[q].is_empty() {
                        if port.tx(q as u16, &mut tx_bursts[q]) == 0 {
                            retries += 1;
                            if retries > 200_000 {
                                st.tx_drops += tx_bursts[q].len() as u64;
                                tx_bursts[q].clear();
                                break;
                            }
                        }
                    }
                }
            }

            iter = iter.wrapping_add(1);
            if (iter & 0x3fff) == 0 {
                st.ooo = order.ooo_count;
                st.dup = order.dup_count;
                st.gap = order.gap_count;
                st.sw_fast_merged = sw_ring.stats.fast_merged;
                st.sw_slow_merged = sw_ring.stats.slow_merged;
                st.sw_phase_switches = sw_ring.stats.phase_switches;
                if let Ok(mut g) = stats_snap.try_lock() {
                    g.copy_from(&st);
                }
                if let Ok(ps) = port.stats() {
                    if let Ok(mut pg) = port_stats_snap.try_lock() {
                        *pg = ps;
                    }
                }
                if let Some(p) = host_pmu.as_ref() {
                    if let Ok(mut pl) = pmu_latest.try_lock() {
                        *pl = Some(p.snap(t_origin));
                    }
                }
            }
        }

        // Final snapshot before exiting
        st.ooo = order.ooo_count;
        st.dup = order.dup_count;
        st.gap = order.gap_count;
        st.sw_fast_merged = sw_ring.stats.fast_merged;
        st.sw_slow_merged = sw_ring.stats.slow_merged;
        st.sw_phase_switches = sw_ring.stats.phase_switches;
        stats_snap.lock().unwrap().copy_from(&st);
        if let Ok(ps) = port.stats() {
            *port_stats_snap.lock().unwrap() = ps;
        }
        if let Some(p) = host_pmu.as_ref() {
            *pmu_latest.lock().unwrap() = Some(p.snap(t_origin));
        }

        quit.store(true, Ordering::Relaxed);
        let _ = ctrl_handle.join();
        if let Some(h) = coloc_handle {
            let _ = h.join();
        }
        Ok(())
    }
}
