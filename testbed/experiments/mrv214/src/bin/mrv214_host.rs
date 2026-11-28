//! Host-side (`saturn` x86_64) standard-`SDP` receiver for MRV-214 (E6: design-space
//! sensitivity sweep of `R_eff` / working-set span, NO-SFENCE credit return batch `B`,
//! and CN106 card-core scaling `1..=8`).
//!
//! Capabilities:
//! - Standard DPDK `octeon_ep` VF0 (`0000:99:02.0`) with `1..=8` SDP RX queues
//!   (`OTX_EP_MAX_RINGS_PER_VF = 8`), `rx_desc` `256..=8192` (`allow_small_oq=1`,
//!   up to `OTX_EP_MAX_OQ_DESCRIPTORS = 8192` = 16 MiB/OQ, `8Q x 8192 = 128 MiB`),
//!   and per-queue dedicated `Mempool` (`pool_size` `512..=65536`, `pool_cache`
//!   `0..=512` where `0` = pure FIFO ring for fine-grained working-set sweeps).
//! - Runs `1..=8` EAL worker cores (`cpu40..47`); control TCP server (`127.0.0.1:7556`)
//!   runs on isolated `cpu48` outside EAL so data-plane cores suffer zero control jitter.
//! - NO-SFENCE `MOVDIR64B` credit return into per-queue exclusive 64 B BAR4 slot-15
//!   cache lines under an active MRV-26 lease (`B = 8 / 16 / 32 / 64 / 128`, optional
//!   `SFENCE` cross-check, optional `align_refill`, and `--credit-flush-us` guard).
//! - Register-only `--app-work` empty-poll accounting (`--calibrate-work`) for
//!   unbiased `io_cycles_per_pkt` below 100% host saturation.
//! - 100% non-multiplexed Host PMU across all active worker CPUs (`cycles`, `instr`,
//!   `ref_cycles`, `llc_loads`, `llc_load_misses`, `sb_stalls`, `page_faults`) plus
//!   Socket-1 uncore (`mesh_clk`, `IIO1 inb_wr/dw/rd/cpu_wr`, `32x CHA DDIO hit/miss`,
//!   `8x IMC DRAM rd/wr`) exposed atomically with live counters via `SNAP`.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv214_host runs on the x86_64 saturn host");
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
    use std::mem::size_of;
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::path::Path;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::Parser;
    use dpdk::{Burst, Eal, EthStats, Mempool, Port, PortStats, QueuePair, rdtsc};
    use testbed::closed_loop::TAIL_WAIT_US;
    use testbed::lease::{self, Lease};
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};
    use mrv214::{
        CreditLine, FrameKind, HOST_CTRL_PORT, IovaTracker, MAX_QUEUES, OFF_BTH, OFF_ETH_TYPE,
        PayloadCheck, StreamTracker, UC64_BASE_QPN, credit_line_offset, parse_kv_tokens,
        validate_pool_size, validate_rx_desc, verify_payload,
    };

    const BURST_N: usize = 64;
    const CTRL_CPU: usize = 48;
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const NUM_CHAS: usize = 32;
    const NUM_IMCS: usize = 8;

    #[derive(Parser)]
    struct Args {
        /// Calibrate register-only `app_work` cycles/step on cpu40 and exit (no NIC/BAR4).
        #[arg(long)]
        calibrate_work: bool,
        /// Calibration duration in ms (used with --calibrate-work).
        #[arg(long, default_value_t = 1000)]
        cal_ms: u64,
        /// Number of SDP RX queues (`1..=8`, matches CN106 card cores).
        #[arg(long, default_value_t = 1)]
        sdp_queues: u16,
        /// SDP RX descriptor ring size per queue (power of 2, `256..=8192`).
        #[arg(long, default_value_t = 512)]
        rx_desc: u16,
        /// SDP TX descriptor ring size per queue (`2048` minimum on octeon_ep IQ).
        #[arg(long, default_value_t = 2048)]
        tx_desc: u16,
        /// Allow small OQ (`rx_desc < 2048`; must match `allow_small_oq=1` devarg).
        #[arg(long)]
        allow_small_oq: bool,
        /// Per-queue RX mempool size (`rx_desc + cache + 128` .. `65536`).
        #[arg(long, default_value_t = 1024)]
        pool_size: u32,
        /// Per-queue RX mempool local cache (`0` = pure FIFO ring; `128`/`256` = tight LIFO).
        #[arg(long, default_value_t = 128)]
        pool_cache: u32,
        /// Enable `rx_poll_mem=1` tail settle & clear (only when `rx_poll_mem=1` devarg is set).
        #[arg(long)]
        rx_poll_mem: bool,
        /// Initial frame kind (`uc64`, `ud`, `udp`).
        #[arg(long, default_value = "udp")]
        frame_kind: String,
        /// Initial payload length in bytes (`0` for uc64, `184` for ud254, `1454` for udp1500).
        #[arg(long, default_value_t = 1454)]
        payload_bytes: usize,
        /// Initial credit return batch size B (`8 / 16 / 32 / 64 / 128`).
        #[arg(long, default_value_t = 32)]
        credit_batch: u64,
        /// Disable BAR4 credit return initially (toggled dynamically via CONFIG).
        #[arg(long)]
        no_credit: bool,
        /// Skip acquiring MRV-26 BAR4 lease completely (use only when card runs without BAR4).
        #[arg(long)]
        no_lease: bool,
        /// Insert SFENCE after MOVDIR64B (for SFENCE ablation cross-check; default false = NO-SFENCE).
        #[arg(long)]
        sfence: bool,
        /// Align credit publication to multiples of 64 (DROQ_REFILL_THRESHOLD) after refill.
        #[arg(long)]
        align_refill: bool,
        /// Idle starvation-timeout flush guard in microseconds (0 = disabled; e.g. 50 prevents B > R_eff deadlock).
        #[arg(long, default_value_t = 50)]
        credit_flush_us: u64,
        /// Optional payload cache-line read stride in bytes (`0` = head+mid+tail verify only; `64` = read every line).
        #[arg(long, default_value_t = 0)]
        touch_stride: usize,
        /// Enable register-only empty-poll app_work accounting.
        #[arg(long)]
        app_work: bool,
        /// Track distinct observed mbuf IOVAs (16 KiB L1D bitset, enabled in Sweep 1).
        #[arg(long)]
        track_iova: bool,
        /// Skip startup CPU frequency warm-up spin (useful when all worker CPUs are already pinned).
        #[arg(long)]
        no_freq_warmup: bool,
        /// PF BAR4 sysfs resource file.
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar: String,
        /// Card MRV-26 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card: String,
        /// Host control TCP port.
        #[arg(long, default_value_t = HOST_CTRL_PORT)]
        ctrl_port: u16,
    }

    // ---- 100% Non-Multiplexed Sapphire Rapids Host PMU ----

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

    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    struct PerfFd {
        fd: RawFd,
    }

    impl PerfFd {
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
            let c = Self { fd: fd as RawFd };
            if unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) } != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> [u64; 3] {
            let mut buf = [0u64; 3];
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast(), size_of::<[u64; 3]>()) };
            if n == size_of::<[u64; 3]>() as isize { buf } else { [0, 0, 0] }
        }
    }

    impl Drop for PerfFd {
        fn drop(&mut self) {
            unsafe {
                libc::ioctl(self.fd, PERF_EVENT_IOC_DISABLE, 0);
                libc::close(self.fd);
            }
        }
    }

    fn pmu_type(name: &str) -> io::Result<u32> {
        let p = format!("/sys/bus/event_source/devices/{name}/type");
        fs::read_to_string(&p)?.trim().parse().map_err(io::Error::other)
    }

    struct CorePmu {
        cpu: i32,
        cycles: PerfFd,
        instr: PerfFd,
        ref_cycles: PerfFd,
        llc_loads: PerfFd,
        llc_load_misses: PerfFd,
        sb_stalls: PerfFd,
        page_faults: PerfFd,
    }

    impl CorePmu {
        fn open(cpu: i32) -> io::Result<Self> {
            Ok(Self {
                cpu,
                cycles: PerfFd::open(0, 0, cpu)?,
                instr: PerfFd::open(0, 1, cpu)?,
                ref_cycles: PerfFd::open(0, 9, cpu)?,
                llc_loads: PerfFd::open(3, 0x00002, cpu)?,
                llc_load_misses: PerfFd::open(3, 0x10002, cpu)?,
                sb_stalls: PerfFd::open(4, 0x08A2, cpu)?,
                page_faults: PerfFd::open(1, 2, cpu)?,
            })
        }

        fn snap_json(&self) -> String {
            let fmt = |v: [u64; 3]| format!("[{},{},{}]", v[0], v[1], v[2]);
            format!(
                "{{\"cpu\":{},\"cycles\":{},\"instr\":{},\"ref_cycles\":{},\"llc_loads\":{},\
                 \"llc_load_misses\":{},\"sb_stalls\":{},\"page_faults\":{}}}",
                self.cpu,
                fmt(self.cycles.read()),
                fmt(self.instr.read()),
                fmt(self.ref_cycles.read()),
                fmt(self.llc_loads.read()),
                fmt(self.llc_load_misses.read()),
                fmt(self.sb_stalls.read()),
                fmt(self.page_faults.read()),
            )
        }
    }

    struct HostPmu {
        t0: Instant,
        cores: Vec<CorePmu>,
        mesh_clk: PerfFd,
        iio1_inb_wr: PerfFd,
        iio1_inb_wr_dw: PerfFd,
        iio1_inb_rd: PerfFd,
        iio1_cpu_wr: PerfFd,
        cha_hit: Vec<PerfFd>,
        cha_miss: Vec<PerfFd>,
        imc_rd: Vec<PerfFd>,
        imc_wr: Vec<PerfFd>,
    }

    impl HostPmu {
        fn open(cpus: &[i32]) -> io::Result<Self> {
            let mut cores = Vec::with_capacity(cpus.len());
            for &cpu in cpus {
                cores.push(CorePmu::open(cpu)?);
            }
            let iio1_t = pmu_type("uncore_iio_1")?;
            let m2p_t = pmu_type("uncore_m2pcie_1")?;
            let mesh_clk = PerfFd::open(m2p_t, 0x01, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_wr = PerfFd::open(iio1_t, 0x0007_0FF0_0000_0184, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_wr_dw = PerfFd::open(iio1_t, 0x0007_0FF0_0000_0183, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_rd = PerfFd::open(iio1_t, 0x0007_0FF0_0000_0484, SOCKET1_UNCORE_CPU)?;
            let iio1_cpu_wr = PerfFd::open(iio1_t, 0x0007_0FF0_0000_01C1, SOCKET1_UNCORE_CPU)?;

            let mut cha_hit = Vec::with_capacity(NUM_CHAS);
            let mut cha_miss = Vec::with_capacity(NUM_CHAS);
            for i in 0..NUM_CHAS {
                let t = pmu_type(&format!("uncore_cha_{i}"))?;
                cha_hit.push(PerfFd::open(t, 0x00CC_43FD_0000_0435, SOCKET1_UNCORE_CPU)?);
                cha_miss.push(PerfFd::open(t, 0x00CC_43FE_0000_0435, SOCKET1_UNCORE_CPU)?);
            }
            let mut imc_rd = Vec::with_capacity(NUM_IMCS);
            let mut imc_wr = Vec::with_capacity(NUM_IMCS);
            for i in 0..NUM_IMCS {
                let t = pmu_type(&format!("uncore_imc_{i}"))?;
                imc_rd.push(PerfFd::open(t, 0xCF05, SOCKET1_UNCORE_CPU)?);
                imc_wr.push(PerfFd::open(t, 0xF005, SOCKET1_UNCORE_CPU)?);
            }
            Ok(Self {
                t0: Instant::now(),
                cores,
                mesh_clk,
                iio1_inb_wr,
                iio1_inb_wr_dw,
                iio1_inb_rd,
                iio1_cpu_wr,
                cha_hit,
                cha_miss,
                imc_rd,
                imc_wr,
            })
        }

        fn snap_json(&self) -> String {
            let t_s = self.t0.elapsed().as_secs_f64();
            let fmt = |v: [u64; 3]| format!("[{},{},{}]", v[0], v[1], v[2]);
            let fmt_vec = |vec: &[PerfFd]| -> String {
                let items: Vec<String> = vec.iter().map(|f| fmt(f.read())).collect();
                format!("[{}]", items.join(","))
            };
            let core_items: Vec<String> = self.cores.iter().map(|c| c.snap_json()).collect();
            format!(
                "{{\"t_s\":{t_s:.6},\"cores\":[{}],\
                 \"uncore\":{{\"mesh_clk\":{},\"iio1_inb_wr\":{},\"iio1_inb_wr_dw\":{},\
                 \"iio1_inb_rd\":{},\"iio1_cpu_wr\":{}}},\
                 \"cha_hit\":{},\"cha_miss\":{},\"imc_rd\":{},\"imc_wr\":{}}}",
                core_items.join(","),
                fmt(self.mesh_clk.read()),
                fmt(self.iio1_inb_wr.read()),
                fmt(self.iio1_inb_wr_dw.read()),
                fmt(self.iio1_inb_rd.read()),
                fmt(self.iio1_cpu_wr.read()),
                fmt_vec(&self.cha_hit),
                fmt_vec(&self.cha_miss),
                fmt_vec(&self.imc_rd),
                fmt_vec(&self.imc_wr),
            )
        }
    }

    fn pin_thread(cpu: usize) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu, &mut set);
            libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set);
        }
    }

    fn read_cpu_khz(cpu: i32) -> u64 {
        let p = format!("/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq");
        if Path::new(&p).exists() {
            fs::read_to_string(&p).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(0)
        } else {
            0
        }
    }

    /// Fixed-length register-only ALU step (zero memory accesses) executed on empty
    /// polls when `--app-work` is enabled, for unbiased `io_cycles_per_pkt` accounting.
    #[inline(always)]
    fn app_work_step(mut x: u64) -> u64 {
        for _ in 0..32 {
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
        }
        x
    }

    fn run_calibrate_work(cal_ms: u64) -> Result<(), Box<dyn Error>> {
        pin_thread(40);
        let core_pmu = CorePmu::open(40)?;
        // Warm up 200 ms.
        let mut acc = 0x1234_5678_9abc_def0u64;
        let w0 = Instant::now();
        while w0.elapsed() < Duration::from_millis(200) {
            acc = app_work_step(acc);
        }
        let c0 = core_pmu.cycles.read();
        let r0 = core_pmu.ref_cycles.read();
        let i0 = core_pmu.instr.read();
        let t0 = Instant::now();
        let mut steps = 0u64;
        let dur = Duration::from_millis(cal_ms);
        while t0.elapsed() < dur {
            for _ in 0..64 {
                acc = app_work_step(acc);
            }
            steps += 64;
        }
        let dt_s = t0.elapsed().as_secs_f64();
        let c1 = core_pmu.cycles.read();
        let r1 = core_pmu.ref_cycles.read();
        let i1 = core_pmu.instr.read();
        std::hint::black_box(acc);
        let dcyc = c1[0].saturating_sub(c0[0]);
        let dref = r1[0].saturating_sub(r0[0]).max(1);
        let dinstr = i1[0].saturating_sub(i0[0]);
        let ghz = (dcyc as f64 / dref as f64) * 2.2;
        let cyc_per_step = dcyc as f64 / (steps.max(1) as f64);
        let min_pct = {
            let p1 = if c1[1] > c0[1] { 100.0 * (c1[2] - c0[2]) as f64 / (c1[1] - c0[1]) as f64 } else { 100.0 };
            let p2 = if r1[1] > r0[1] { 100.0 * (r1[2] - r0[2]) as f64 / (r1[1] - r0[1]) as f64 } else { 100.0 };
            p1.min(p2)
        };
        println!(
            "MRV214_CAL_WORK {{\"dt_s\":{dt_s:.6},\"steps\":{steps},\"cycles\":{dcyc},\"instr\":{dinstr},\
             \"cpu40_ghz\":{ghz:.4},\"cycles_per_step\":{cyc_per_step:.4},\"min_running_pct\":{min_pct:.3}}}"
        );
        Ok(())
    }

    #[derive(Clone, Copy, Debug)]
    struct HostCfg {
        run_gen: u64,
        credit_bar4: bool,
        credit_batch: u64,
        use_sfence: bool,
        align_refill: bool,
        credit_flush_us: u64,
        frame_kind: FrameKind,
        payload_bytes: usize,
        touch_stride: usize,
        app_work: bool,
        track_iova: bool,
        rx_poll_mem: bool,
    }

    #[repr(align(64))]
    struct AtomicWorkerLive {
        rx_pkts: AtomicU64,
        rx_bytes: AtomicU64,
        bad: AtomicU64,
        torn: AtomicU64,
        late: AtomicU64,
        merc_stream_lost: AtomicU64,
        credit_writes: AtomicU64,
        credit_timeout_flushes: AtomicU64,
        empty_polls: AtomicU64,
        busy_polls: AtomicU64,
        app_work_steps: AtomicU64,
        lease_err: AtomicU64,
        unique_iova_slots: AtomicU64,
        iova_span_bytes: AtomicU64,
    }

    impl AtomicWorkerLive {
        fn new() -> Self {
            Self {
                rx_pkts: AtomicU64::new(0),
                rx_bytes: AtomicU64::new(0),
                bad: AtomicU64::new(0),
                torn: AtomicU64::new(0),
                late: AtomicU64::new(0),
                merc_stream_lost: AtomicU64::new(0),
                credit_writes: AtomicU64::new(0),
                credit_timeout_flushes: AtomicU64::new(0),
                empty_polls: AtomicU64::new(0),
                busy_polls: AtomicU64::new(0),
                app_work_steps: AtomicU64::new(0),
                lease_err: AtomicU64::new(0),
                unique_iova_slots: AtomicU64::new(0),
                iova_span_bytes: AtomicU64::new(0),
            }
        }
    }

    #[derive(Default)]
    struct WorkerLocal {
        rx_pkts: u64,
        rx_bytes: u64,
        bad: u64,
        torn: u64,
        late: u64,
        merc_stream_lost: u64,
        credit_writes: u64,
        credit_timeout_flushes: u64,
        empty_polls: u64,
        busy_polls: u64,
        app_work_steps: u64,
        lease_err: u64,
        unique_iova_slots: u64,
        iova_span_bytes: u64,
    }

    impl WorkerLocal {
        #[inline(always)]
        fn publish(&self, dst: &AtomicWorkerLive) {
            dst.rx_pkts.store(self.rx_pkts, Ordering::Relaxed);
            dst.rx_bytes.store(self.rx_bytes, Ordering::Relaxed);
            dst.bad.store(self.bad, Ordering::Relaxed);
            dst.torn.store(self.torn, Ordering::Relaxed);
            dst.late.store(self.late, Ordering::Relaxed);
            dst.merc_stream_lost.store(self.merc_stream_lost, Ordering::Relaxed);
            dst.credit_writes.store(self.credit_writes, Ordering::Relaxed);
            dst.credit_timeout_flushes.store(self.credit_timeout_flushes, Ordering::Relaxed);
            dst.empty_polls.store(self.empty_polls, Ordering::Relaxed);
            dst.busy_polls.store(self.busy_polls, Ordering::Relaxed);
            dst.app_work_steps.store(self.app_work_steps, Ordering::Relaxed);
            dst.lease_err.store(self.lease_err, Ordering::Relaxed);
            dst.unique_iova_slots.store(self.unique_iova_slots, Ordering::Relaxed);
            dst.iova_span_bytes.store(self.iova_span_bytes, Ordering::Relaxed);
        }
    }

    struct Shared {
        n_workers: usize,
        sdp_queues: u16,
        rx_desc: u16,
        pool_size: u32,
        pool_cache: u32,
        tsc_hz: u64,
        settle_ticks: u64,
        bar4_ptr: usize,
        active: AtomicBool,
        quit: AtomicBool,
        cfg_gen: AtomicU64,
        reset_gen: AtomicU64,
        cfg: Mutex<HostCfg>,
        cfg_ack: [AtomicU64; MAX_QUEUES],
        reset_ack: [AtomicU64; MAX_QUEUES],
        worker_cpus: [AtomicU64; MAX_QUEUES],
        workers_ready: AtomicU64,
        live: Vec<AtomicWorkerLive>,
        reset_wall: Mutex<Instant>,
    }

    impl Shared {
        fn new(
            n_workers: usize,
            sdp_queues: u16,
            rx_desc: u16,
            pool_size: u32,
            pool_cache: u32,
            tsc_hz: u64,
            bar4_ptr: usize,
            init_cfg: HostCfg,
        ) -> Self {
            let mut live = Vec::with_capacity(n_workers);
            for _ in 0..n_workers {
                live.push(AtomicWorkerLive::new());
            }
            Self {
                n_workers,
                sdp_queues,
                rx_desc,
                pool_size,
                pool_cache,
                tsc_hz,
                settle_ticks: (tsc_hz / 1_000_000) * TAIL_WAIT_US,
                bar4_ptr,
                active: AtomicBool::new(false),
                quit: AtomicBool::new(false),
                cfg_gen: AtomicU64::new(0),
                reset_gen: AtomicU64::new(0),
                cfg: Mutex::new(init_cfg),
                cfg_ack: std::array::from_fn(|_| AtomicU64::new(0)),
                reset_ack: std::array::from_fn(|_| AtomicU64::new(0)),
                worker_cpus: std::array::from_fn(|_| AtomicU64::new(0)),
                workers_ready: AtomicU64::new(0),
                live,
                reset_wall: Mutex::new(Instant::now()),
            }
        }

        fn wait_ack(acks: &[AtomicU64; MAX_QUEUES], n_workers: usize, target: u64) {
            let t0 = Instant::now();
            while t0.elapsed() < Duration::from_secs(3) {
                if (0..n_workers).all(|w| acks[w].load(Ordering::Acquire) >= target) {
                    return;
                }
                std::hint::spin_loop();
            }
        }

        fn stats_json(&self, port_st: &PortStats<'_, '_>, pmu: Option<&HostPmu>) -> String {
            let elapsed_s = self.reset_wall.lock().unwrap().elapsed().as_secs_f64();
            let mut rx_pkts = 0u64;
            let mut rx_bytes = 0u64;
            let mut bad = 0u64;
            let mut torn = 0u64;
            let mut late = 0u64;
            let mut merc_stream_lost = 0u64;
            let mut credit_writes = 0u64;
            let mut credit_timeout_flushes = 0u64;
            let mut empty_polls = 0u64;
            let mut busy_polls = 0u64;
            let mut app_work_steps = 0u64;
            let mut lease_err = 0u64;
            let mut unique_iova_slots = 0u64;
            let mut iova_span_bytes = 0u64;

            for w in 0..self.n_workers {
                let l = &self.live[w];
                rx_pkts += l.rx_pkts.load(Ordering::Relaxed);
                rx_bytes += l.rx_bytes.load(Ordering::Relaxed);
                bad += l.bad.load(Ordering::Relaxed);
                torn += l.torn.load(Ordering::Relaxed);
                late += l.late.load(Ordering::Relaxed);
                merc_stream_lost += l.merc_stream_lost.load(Ordering::Relaxed);
                credit_writes += l.credit_writes.load(Ordering::Relaxed);
                credit_timeout_flushes += l.credit_timeout_flushes.load(Ordering::Relaxed);
                empty_polls += l.empty_polls.load(Ordering::Relaxed);
                busy_polls += l.busy_polls.load(Ordering::Relaxed);
                app_work_steps += l.app_work_steps.load(Ordering::Relaxed);
                lease_err += l.lease_err.load(Ordering::Relaxed);
                unique_iova_slots += l.unique_iova_slots.load(Ordering::Relaxed);
                iova_span_bytes += l.iova_span_bytes.load(Ordering::Relaxed);
            }

            let eth: EthStats = port_st.stats().unwrap_or_default();
            let cfg = *self.cfg.lock().unwrap();
            let pmu_part = match pmu {
                Some(p) => format!(",\"pmu\":{}", p.snap_json()),
                None => String::new(),
            };
            let unique_mib = (unique_iova_slots as f64 * 2048.0) / (1024.0 * 1024.0);
            let span_mib = (iova_span_bytes as f64) / (1024.0 * 1024.0);
            format!(
                "{{\"elapsed_s\":{elapsed_s:.6},\"host_cores\":{},\"sdp_queues\":{},\"rx_desc\":{},\
                 \"pool_size\":{},\"pool_cache\":{},\"run_gen\":{},\"credit_bar4\":{},\"credit_batch\":{},\
                 \"sfence\":{},\"align_refill\":{},\"touch_stride\":{},\"app_work\":{},\"track_iova\":{},\
                 \"rx_pkts\":{rx_pkts},\"rx_bytes\":{rx_bytes},\"bad\":{bad},\"torn\":{torn},\"late\":{late},\
                 \"merc_stream_lost\":{merc_stream_lost},\"credit_writes\":{credit_writes},\
                 \"credit_timeout_flushes\":{credit_timeout_flushes},\"empty_polls\":{empty_polls},\
                 \"busy_polls\":{busy_polls},\"app_work_steps\":{app_work_steps},\"lease_err\":{lease_err},\
                 \"unique_iova_slots\":{unique_iova_slots},\"measured_iova_unique_mib\":{unique_mib:.3},\
                 \"measured_iova_span_mib\":{span_mib:.3},\
                 \"eth\":{{\"ipackets\":{},\"ibytes\":{},\"imissed\":{},\"ierrors\":{},\"rx_nombuf\":{}}}{pmu_part}}}",
                self.n_workers,
                self.sdp_queues,
                self.rx_desc,
                self.pool_size,
                self.pool_cache,
                cfg.run_gen,
                cfg.credit_bar4,
                cfg.credit_batch,
                cfg.use_sfence,
                cfg.align_refill,
                cfg.touch_stride,
                cfg.app_work,
                cfg.track_iova,
                eth.ipackets,
                eth.ibytes,
                eth.imissed,
                eth.ierrors,
                eth.rx_nombuf,
            )
        }
    }

    #[inline(always)]
    fn publish_credit_line(
        lease: Option<&Lease>,
        bar4_ptr: usize,
        q: usize,
        taken: u64,
        bytes_taken: u64,
        cfg: &HostCfg,
        cnt: &mut WorkerLocal,
    ) {
        if bar4_ptr == 0 {
            return;
        }
        if let Some(l) = lease {
            if l.check().is_err() {
                cnt.lease_err += 1;
                return;
            }
        }
        let flags = 1u64
            | if cfg.use_sfence { 1 << 1 } else { 0 }
            | if cfg.align_refill { 1 << 2 } else { 0 };
        let line = CreditLine::new(q as u64, taken, cfg.run_gen, bytes_taken, rdtsc(), flags);
        let dst = (bar4_ptr + credit_line_offset(q)) as *mut u8;
        // SAFETY: `dst` is 64-byte aligned inside a valid MRV-26 leased BAR4 slot 15 mapping,
        // and `has_movdir64b()` was verified at startup.
        unsafe { movdir64b(dst, &line) };
        if cfg.use_sfence {
            sfence();
        }
        cnt.credit_writes += 1;
    }

    struct HostWorkerInput<'p, 'e> {
        worker_idx: usize,
        queues: Vec<QueuePair<'p, 'e>>,
    }

    fn run_host_worker<'p, 'e>(
        sh: &Shared,
        lease: Option<&Lease>,
        no_freq_warmup: bool,
        mut inp: HostWorkerInput<'p, 'e>,
    ) {
        let w = inp.worker_idx;
        let my_cpu = unsafe { libc::sched_getcpu() }.max(0);
        sh.worker_cpus[w].store(my_cpu as u64, Ordering::Release);

        // Warm up schedutil worker CPUs (`cpu41..47`) so they reach 3.90 GHz before READY.
        if !no_freq_warmup && sh.n_workers > 1 {
            let t0 = Instant::now();
            let mut acc = (w as u64).wrapping_add(1);
            while t0.elapsed() < Duration::from_millis(3500) {
                for _ in 0..4096 {
                    acc = app_work_step(acc);
                }
                std::hint::black_box(acc);
                if t0.elapsed() >= Duration::from_millis(250) && read_cpu_khz(my_cpu) >= 3_800_000 {
                    break;
                }
            }
        }
        let mut rx_burst: Burst<'e, BURST_N> = Burst::new();
        let mut tracker = StreamTracker::new();
        let mut iova_tracker = IovaTracker::new();
        let mut cnt = WorkerLocal::default();
        sh.workers_ready.fetch_add(1, Ordering::Release);

        let n_my_q = inp.queues.len();
        let mut cfg = *sh.cfg.lock().unwrap();
        let mut seen_cfg_gen = sh.cfg_gen.load(Ordering::Acquire);
        let mut seen_reset_gen = sh.reset_gen.load(Ordering::Acquire);
        let mut taken = [0u64; MAX_QUEUES];
        let mut bytes_taken = [0u64; MAX_QUEUES];
        let mut published = [0u64; MAX_QUEUES];
        let mut last_rx_tsc = [0u64; MAX_QUEUES];
        let mut next_qi = 0usize;
        let mut work_acc = 0x517c_c1b7_2722_0a95u64 ^ (w as u64);
        let mut touch_sink = 0u64;
        let mut idle_iters = 0u32;

        while !sh.quit.load(Ordering::Acquire) {
            let cg = sh.cfg_gen.load(Ordering::Acquire);
            if cg != seen_cfg_gen {
                // Drain any residual packets from previous run.
                for rq in &mut inp.queues {
                    while rq.rx(&mut rx_burst) > 0 {
                        rx_burst.clear();
                    }
                }
                cfg = *sh.cfg.lock().unwrap();
                taken = [0u64; MAX_QUEUES];
                bytes_taken = [0u64; MAX_QUEUES];
                published = [0u64; MAX_QUEUES];
                let now = rdtsc();
                last_rx_tsc = [now; MAX_QUEUES];
                tracker.reset();
                iova_tracker.reset();
                cnt = WorkerLocal::default();
                cnt.publish(&sh.live[w]);
                seen_cfg_gen = cg;
                sh.cfg_ack[w].store(cg, Ordering::Release);
                continue;
            }

            let rg = sh.reset_gen.load(Ordering::Acquire);
            if rg != seen_reset_gen {
                tracker.reset();
                iova_tracker.reset();
                cnt = WorkerLocal::default();
                cnt.publish(&sh.live[w]);
                seen_reset_gen = rg;
                sh.reset_ack[w].store(rg, Ordering::Release);
            }

            if n_my_q == 0 {
                thread::sleep(Duration::from_millis(1));
                continue;
            }

            let qi = next_qi;
            next_qi += 1;
            if next_qi == n_my_q {
                next_qi = 0;
            }
            let rq = &mut inp.queues[qi];
            let qid = rq.queue() as usize;
            let n = rq.rx(&mut rx_burst);

            let flush_ticks = (sh.tsc_hz / 1_000_000) * cfg.credit_flush_us;

            if n == 0 {
                if sh.active.load(Ordering::Relaxed) {
                    cnt.empty_polls += 1;
                    if cfg.app_work {
                        work_acc = app_work_step(work_acc);
                        cnt.app_work_steps += 1;
                    }
                    // Starvation-timeout flush guard (prevents deadlock when B > R_eff or tail drain).
                    if cfg.credit_bar4 && flush_ticks > 0 && taken[qid] > published[qid] {
                        let now = rdtsc();
                        if now.wrapping_sub(last_rx_tsc[qid]) >= flush_ticks {
                            publish_credit_line(
                                lease,
                                sh.bar4_ptr,
                                qid,
                                taken[qid],
                                bytes_taken[qid],
                                &cfg,
                                &mut cnt,
                            );
                            published[qid] = taken[qid];
                            last_rx_tsc[qid] = now;
                            cnt.credit_timeout_flushes += 1;
                        }
                    }
                }
                idle_iters = idle_iters.wrapping_add(1);
                if idle_iters & 0x3ff == 0 {
                    std::hint::black_box(work_acc ^ touch_sink);
                    cnt.publish(&sh.live[w]);
                }
                continue;
            }

            idle_iters = 0;
            let active = sh.active.load(Ordering::Relaxed);
            let fk = cfg.frame_kind;
            let plen = cfg.payload_bytes;
            let is_uc_zero = fk == FrameKind::Uc64 && plen == 0;
            let pay_off = fk.payload_offset();
            let frame_l2 = fk.l2_bytes(plen) as u64;
            let rpm = cfg.rx_poll_mem;
            let stride = cfg.touch_stride;
            let track_iova = cfg.track_iova;

            if active {
                cnt.busy_polls += 1;
                for m in rx_burst.as_mut_slice() {
                    if track_iova {
                        iova_tracker.observe(m.iova());
                    }
                    let dlen = m.data_len();
                    let p = m.data_ptr();
                    if is_uc_zero {
                        if dlen < 60 {
                            cnt.bad += 1;
                            continue;
                        }
                        if rpm {
                            let tail_p = unsafe { p.add(52).cast::<u64>() };
                            let t0 = rdtsc();
                            while unsafe { tail_p.read_volatile() } == 0 {
                                if rdtsc().wrapping_sub(t0) >= sh.settle_ticks {
                                    cnt.torn += 1;
                                    break;
                                }
                                std::hint::spin_loop();
                            }
                        }
                        let etype = u16::from_be_bytes(unsafe {
                            [*p.add(OFF_ETH_TYPE), *p.add(OFF_ETH_TYPE + 1)]
                        });
                        let bth_op = unsafe { *p.add(OFF_BTH) };
                        let dst_qp = ((unsafe { *p.add(OFF_BTH + 5) } as u32) << 16)
                            | ((unsafe { *p.add(OFF_BTH + 6) } as u32) << 8)
                            | (unsafe { *p.add(OFF_BTH + 7) } as u32);
                        let psn = ((unsafe { *p.add(OFF_BTH + 9) } as u64) << 16)
                            | ((unsafe { *p.add(OFF_BTH + 10) } as u64) << 8)
                            | (unsafe { *p.add(OFF_BTH + 11) } as u64);
                        if etype != 0x0800 || bth_op != 0x24 || dst_qp < UC64_BASE_QPN {
                            cnt.bad += 1;
                        } else {
                            cnt.merc_stream_lost += tracker.observe_uc64(dst_qp - UC64_BASE_QPN, psn);
                        }
                        if rpm {
                            unsafe { m.data_mut_ptr().add(52).cast::<u64>().write_volatile(0) };
                        }
                        cnt.rx_pkts += 1;
                        cnt.rx_bytes += 64;
                    } else {
                        if dlen < pay_off + plen {
                            cnt.bad += 1;
                            continue;
                        }
                        let mut check = verify_payload(
                            unsafe { std::slice::from_raw_parts(p.add(pay_off), plen) },
                            plen,
                        );
                        if rpm && !matches!(check, PayloadCheck::Ok { .. }) {
                            let t0 = rdtsc();
                            while rdtsc().wrapping_sub(t0) < sh.settle_ticks {
                                std::hint::spin_loop();
                                std::sync::atomic::fence(Ordering::Acquire);
                                let c2 = verify_payload(
                                    unsafe { std::slice::from_raw_parts(p.add(pay_off), plen) },
                                    plen,
                                );
                                if matches!(c2, PayloadCheck::Ok { .. }) {
                                    check = c2;
                                    cnt.late += 1;
                                    break;
                                }
                            }
                        }
                        match check {
                            PayloadCheck::Ok { stream_id, seq, .. } => {
                                cnt.merc_stream_lost += tracker.observe_seq(stream_id, seq);
                            }
                            PayloadCheck::Torn => cnt.torn += 1,
                            PayloadCheck::Bad => cnt.bad += 1,
                        }
                        if stride > 0 {
                            let mut off = 0usize;
                            while off + 8 <= dlen {
                                let v = unsafe { p.add(off).cast::<u64>().read_unaligned() };
                                touch_sink = touch_sink.wrapping_add(v);
                                off += stride;
                            }
                        }
                        if rpm {
                            let mp = m.data_mut_ptr();
                            unsafe {
                                mp.add(pay_off).cast::<u64>().write_volatile(0);
                                mp.add(pay_off + plen - 8).cast::<u64>().write_volatile(0);
                            }
                        }
                        cnt.rx_pkts += 1;
                        cnt.rx_bytes += frame_l2;
                    }
                }
                if track_iova {
                    cnt.unique_iova_slots = iova_tracker.unique_slots();
                    cnt.iova_span_bytes = iova_tracker.span_bytes();
                }
            }

            // Free received mbufs back to the queue's dedicated mempool BEFORE publishing credit.
            rx_burst.clear();

            if active {
                taken[qid] += n as u64;
                bytes_taken[qid] += (n as u64) * frame_l2;
                last_rx_tsc[qid] = rdtsc();

                if cfg.credit_bar4 {
                    let eligible = if cfg.align_refill {
                        // Publish only packets already refilled by cnxk_ep_rx_refill (multiple of 64).
                        taken[qid] & !63u64
                    } else {
                        taken[qid]
                    };
                    let step = if cfg.align_refill {
                        cfg.credit_batch.max(64)
                    } else {
                        cfg.credit_batch
                    };
                    while eligible.saturating_sub(published[qid]) >= step {
                        let next_pub = published[qid] + step;
                        publish_credit_line(
                            lease,
                            sh.bar4_ptr,
                            qid,
                            next_pub,
                            next_pub.saturating_mul(frame_l2),
                            &cfg,
                            &mut cnt,
                        );
                        published[qid] = next_pub;
                    }
                }
                cnt.publish(&sh.live[w]);
            }
        }

        std::hint::black_box(work_acc ^ touch_sink);
        cnt.publish(&sh.live[w]);
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        // Handle pure CPU calibration cleanly before EAL init.
        let raw_args: Vec<String> = std::env::args().collect();
        if raw_args.iter().any(|a| a == "--calibrate-work") {
            let mut cal_ms = 1000u64;
            for i in 0..raw_args.len() {
                if raw_args[i] == "--cal-ms" {
                    if let Some(v) = raw_args.get(i + 1) {
                        cal_ms = v.parse().unwrap_or(1000);
                    }
                }
            }
            return run_calibrate_work(cal_ms);
        }
        // Lock binary text/data/glibc/stack in physical RAM before DPDK reserves VA space
        // (DPDK hugepages are already pinned by hugetlbfs + VFIO IOMMU).
        unsafe {
            if libc::mlockall(libc::MCL_CURRENT) != 0 {
                eprintln!(
                    "WARN: mlockall(MCL_CURRENT) failed: {}",
                    io::Error::last_os_error()
                );
            }
        }
        let (eal, rest) = Eal::init(raw_args)?;
        let args = Args::parse_from(rest);

        if args.calibrate_work {
            return run_calibrate_work(args.cal_ms);
        }

        let n_workers = eal.lcores();
        assert!(
            (1..=MAX_QUEUES).contains(&n_workers),
            "host EAL workers must be in 1..={MAX_QUEUES} (got {n_workers})"
        );
        assert!(
            args.sdp_queues >= 1 && usize::from(args.sdp_queues) <= MAX_QUEUES,
            "--sdp-queues must be in 1..={MAX_QUEUES}"
        );
        validate_rx_desc(args.rx_desc, args.allow_small_oq)?;
        validate_pool_size(args.rx_desc, args.pool_size, args.pool_cache)?;

        let init_fk = FrameKind::parse(&args.frame_kind)
            .ok_or_else(|| format!("invalid --frame-kind {}", args.frame_kind))?;

        let lease_hold: Option<Lease> = if !args.no_lease {
            assert!(has_movdir64b(), "CPU lacks MOVDIR64B required for BAR4 credit return");
            Some(Lease::acquire(&args.card)?)
        } else {
            None
        };
        let slot_hold = match lease_hold.as_ref() {
            Some(l) => {
                let s = l.map_slot(&args.bar)?;
                s.check()?;
                Some(s)
            }
            None => None,
        };
        let bar4_ptr = slot_hold.as_ref().map(|s| s.as_ptr() as usize).unwrap_or(0);

        let socket = Port::socket_of(&eal, 0).max(0);
        let data_room = Mempool::default_data_room();
        let mut rx_pools: Vec<Mempool<'_>> = Vec::with_capacity(usize::from(args.sdp_queues));
        for q in 0..args.sdp_queues {
            rx_pools.push(Mempool::new_with_cache(
                &eal,
                &format!("mrv214_h_rxp{q}"),
                args.pool_size,
                args.pool_cache,
                data_room,
                socket,
            )?);
        }
        let pool_refs: Vec<&Mempool<'_>> = rx_pools.iter().collect();

        let (port, _) = Port::start_fast(
            &eal,
            0,
            &pool_refs,
            args.sdp_queues,
            args.sdp_queues,
            args.rx_desc,
            args.tx_desc,
            None,
            false,
            false,
            false,
        )?;
        let _ = port.stats_reset();
        let port_stats = port.stats_handle();
        let rx_queues = port.split_queue_pairs();

        // Partition SDP RX queues disjointly across host workers (`q % n_workers == w`).
        let mut per_worker_q: Vec<Vec<QueuePair<'_, '_>>> = (0..n_workers).map(|_| Vec::new()).collect();
        for rq in rx_queues {
            let w = (rq.queue() as usize) % n_workers;
            per_worker_q[w].push(rq);
        }
        let worker_inputs: Vec<HostWorkerInput<'_, '_>> = per_worker_q
            .into_iter()
            .enumerate()
            .map(|(worker_idx, queues)| HostWorkerInput { worker_idx, queues })
            .collect();

        let init_cfg = HostCfg {
            run_gen: 1,
            credit_bar4: !args.no_credit && !args.no_lease,
            credit_batch: args.credit_batch.max(1),
            use_sfence: args.sfence,
            align_refill: args.align_refill,
            credit_flush_us: args.credit_flush_us,
            frame_kind: init_fk,
            payload_bytes: args.payload_bytes,
            touch_stride: args.touch_stride,
            app_work: args.app_work,
            track_iova: args.track_iova,
            rx_poll_mem: args.rx_poll_mem,
        };
        let sh = Arc::new(Shared::new(
            n_workers,
            args.sdp_queues,
            args.rx_desc,
            args.pool_size,
            args.pool_cache,
            eal.tsc_hz(),
            bar4_ptr,
            init_cfg,
        ));

        let quit_sig = Arc::clone(&sh);
        let _ = ctrlc::set_handler(move || {
            quit_sig.active.store(false, Ordering::Release);
            quit_sig.quit.store(true, Ordering::Release);
        });

        // Spawn isolated TCP control + HostPmu thread on `cpu48` (outside EAL workers).
        let ctrl_sh = Arc::clone(&sh);
        let ctrl_port = args.ctrl_port;
        let no_lease_flag = args.no_lease;
        let no_freq_warmup = args.no_freq_warmup;
        let lease_ref = lease_hold.as_ref();

        thread::scope(|scope| {
            let _ctrl_handle = scope.spawn(move || {
                pin_thread(CTRL_CPU);
                // Wait for all EAL workers to record their CPU ids & complete frequency warmup.
                while ctrl_sh.workers_ready.load(Ordering::Acquire) < (n_workers as u64)
                    && !ctrl_sh.quit.load(Ordering::Acquire)
                {
                    thread::sleep(Duration::from_millis(5));
                }
                let cpus: Vec<i32> = (0..n_workers)
                    .map(|w| ctrl_sh.worker_cpus[w].load(Ordering::Acquire) as i32)
                    .collect();
                let pmu = HostPmu::open(&cpus).expect("open 100% non-multiplexed HostPmu");

                let listener = TcpListener::bind(("127.0.0.1", ctrl_port)).expect("bind host ctrl port");
                listener.set_nonblocking(true).expect("host ctrl nonblocking");
                println!(
                    "MRV214_HOST READY workers={} cpus={:?} sdp_queues={} rx_desc={} pool_size={} pool_cache={} ctrl_port={} bar4={}",
                    n_workers, cpus, ctrl_sh.sdp_queues, ctrl_sh.rx_desc, ctrl_sh.pool_size, ctrl_sh.pool_cache, ctrl_port, !no_lease_flag
                );

                while !ctrl_sh.quit.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((mut sock, _)) => {
                            let _ = sock.set_nonblocking(false);
                            let _ = sock.set_read_timeout(Some(Duration::from_secs(5)));
                            let mut reader = BufReader::new(sock.try_clone().unwrap());
                            let mut line = String::new();
                            if reader.read_line(&mut line).unwrap_or(0) == 0 {
                                continue;
                            }
                            let cmd = line.trim();
                            let reply = if cmd == "PING" {
                                "{\"ok\":true}".to_string()
                            } else if let Some(rest) = cmd.strip_prefix("CONFIG ") {
                                ctrl_sh.active.store(false, Ordering::Release);
                                let mut next = *ctrl_sh.cfg.lock().unwrap();
                                let mut parse_err: Option<String> = None;
                                for (k, v) in parse_kv_tokens(rest) {
                                    match k {
                                        "run_gen" => next.run_gen = v.parse().unwrap_or(next.run_gen),
                                        "credit" => {
                                            next.credit_bar4 = match v {
                                                "bar4" | "bar4-nofence" | "on" | "1" => {
                                                    next.use_sfence = false;
                                                    true
                                                }
                                                "bar4-sfence" => {
                                                    next.use_sfence = true;
                                                    true
                                                }
                                                "off" | "0" => false,
                                                _ => {
                                                    parse_err = Some(format!("bad credit={v}"));
                                                    next.credit_bar4
                                                }
                                            };
                                        }
                                        "credit_batch" | "batch" => {
                                            next.credit_batch = v.parse::<u64>().unwrap_or(next.credit_batch).max(1)
                                        }
                                        "sfence" => next.use_sfence = v == "1" || v == "true",
                                        "align_refill" => next.align_refill = v == "1" || v == "true",
                                        "credit_flush_us" => {
                                            next.credit_flush_us = v.parse().unwrap_or(next.credit_flush_us)
                                        }
                                        "frame_kind" => match FrameKind::parse(v) {
                                            Some(fk) => next.frame_kind = fk,
                                            None => parse_err = Some(format!("bad frame_kind={v}")),
                                        },
                                        "payload" => next.payload_bytes = v.parse().unwrap_or(next.payload_bytes),
                                        "touch_stride" => {
                                            next.touch_stride = v.parse().unwrap_or(next.touch_stride)
                                        }
                                        "app_work" => next.app_work = v == "1" || v == "true",
                                        "track_iova" => next.track_iova = v == "1" || v == "true",
                                        _ => {}
                                    }
                                }
                                if next.credit_bar4 && no_lease_flag {
                                    parse_err = Some("cannot enable BAR4 credit with --no-lease".to_string());
                                }
                                if let Some(err) = parse_err {
                                    format!("{{\"ok\":false,\"err\":\"{err}\"}}")
                                } else {
                                    *ctrl_sh.cfg.lock().unwrap() = next;
                                    let epoch = ctrl_sh.cfg_gen.fetch_add(1, Ordering::AcqRel) + 1;
                                    Shared::wait_ack(&ctrl_sh.cfg_ack, n_workers, epoch);
                                    let _ = port_stats.stats_reset();
                                    *ctrl_sh.reset_wall.lock().unwrap() = Instant::now();
                                    format!(
                                        "{{\"ok\":true,\"run_gen\":{},\"credit_bar4\":{},\"credit_batch\":{},\"sfence\":{},\"align_refill\":{},\"credit_flush_us\":{},\"frame_kind\":\"{}\",\"payload\":{},\"touch_stride\":{},\"app_work\":{},\"track_iova\":{}}}",
                                        next.run_gen,
                                        next.credit_bar4,
                                        next.credit_batch,
                                        next.use_sfence,
                                        next.align_refill,
                                        next.credit_flush_us,
                                        next.frame_kind.as_str(),
                                        next.payload_bytes,
                                        next.touch_stride,
                                        next.app_work,
                                        next.track_iova
                                    )
                                }
                            } else if cmd == "START" {
                                ctrl_sh.active.store(true, Ordering::Release);
                                "{\"ok\":true}".to_string()
                            } else if cmd == "STOP" {
                                ctrl_sh.active.store(false, Ordering::Release);
                                "{\"ok\":true}".to_string()
                            } else if cmd == "RESET" {
                                let epoch = ctrl_sh.reset_gen.fetch_add(1, Ordering::AcqRel) + 1;
                                Shared::wait_ack(&ctrl_sh.reset_ack, n_workers, epoch);
                                let _ = port_stats.stats_reset();
                                *ctrl_sh.reset_wall.lock().unwrap() = Instant::now();
                                "{\"ok\":true}".to_string()
                            } else if cmd == "PMU_SNAP" {
                                pmu.snap_json()
                            } else if cmd == "SNAP" {
                                ctrl_sh.stats_json(&port_stats, Some(&pmu))
                            } else if cmd == "STATS" {
                                ctrl_sh.stats_json(&port_stats, None)
                            } else if cmd == "QUIT" {
                                ctrl_sh.active.store(false, Ordering::Release);
                                ctrl_sh.quit.store(true, Ordering::Release);
                                let _ = writeln!(sock, "{{\"ok\":true}}");
                                break;
                            } else {
                                "{\"ok\":false,\"err\":\"unknown cmd\"}".to_string()
                            };
                            let _ = writeln!(sock, "{reply}");
                        }
                        Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(1));
                        }
                        Err(_) => {
                            thread::sleep(Duration::from_millis(1));
                        }
                    }
                }
            });

            let sh_ref = &sh;
            eal.on_each_lcore(worker_inputs, move |_lcore_idx, inp| {
                run_host_worker(sh_ref, lease_ref, no_freq_warmup, inp);
            });
        });

        drop(slot_hold);
        drop(lease_hold);
        Ok(())
    }
}
