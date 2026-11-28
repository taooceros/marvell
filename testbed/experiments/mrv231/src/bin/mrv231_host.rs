//! MRV-231 host-side microbenchmark (x86_64 saturn, Socket 1 ONLY: cpus 32..63):
//!
//! Strictly acquires the MRV-26 BAR4 slot 15 lease (`testbed::lease`), checks
//! READY + nonce before every MOVDIR64B, pins all worker threads exclusively to
//! Socket 1 CPUs (`cpu40..43`), and synchronizes 100% non-multiplexed host
//! Socket 1 PMUs with card TAD + ARMv8 PMUv3 over `10.10.0.2:7527`.
//!
//! Two clean modes (ZERO BAR4 UC credit reads during measurement!):
//! 1. `--closed-loop` (primary Control 2 & Control 3, reproduces MRV-75 WC W=64):
//!    Allocates a contiguous DPDK reply ring on Socket 1 (VF0 `0000:99:02.0`,
//!    EAL main lcore pinned strictly to Socket 1 `cpu39`), passes its IOVA to
//!    the card, and runs closed-loop `W` per lane (`sent - done < W`) polling
//!    host memory for card WC replies (`lost = bad = torn = corrupt = 0`).
//!    Supports K in {1, 2, 4} independent host cores (`cpu40..43`) OR single host
//!    core (`cpu40`) round-robin striping (`--stripe`) across K sub-rings inside
//!    leased BAR4 slot 15 (960 KiB apart, 128 B cell stride).
//! 2. `--poll-mode none` (card-silent direct-push ceiling baseline):
//!    Card does not touch the ring; host blasts pure MOVDIR64B with zero reads.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv231_host runs on the x86_64 host");
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
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpStream;
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
    use std::sync::{Arc, Barrier};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::Parser;
    use dpdk::{Eal, Memzone, Port};
    use testbed::inline::{Cell, Slot};
    use testbed::lease::{self, Lease};
    use testbed::mmio::{has_movdir64b, movdir64b};

    const MAX_LANES: usize = 4;
    const MAX_WORKERS: usize = 8;
    const REPLY_CELLS_PER_WORKER: usize = 1024;
    const LANE_STRIDE_BYTES: usize = 960 * 1024;
    const CELL_STRIDE_BYTES: usize = 128;
    const SOCKET1_UNCORE_CPU: i32 = 32;

    #[derive(Parser, Debug)]
    struct Args {
        /// Number of independent sub-rings inside BAR4 slot 15 (1, 2, or 4).
        #[arg(long, default_value_t = 1)]
        lanes: usize,
        /// Card polling cores per sub-ring (1 or 2; lanes * cards_per_lane <= 8).
        #[arg(long, default_value_t = 1)]
        cards_per_lane: usize,
        /// Socket 1 host CPUs (must all be in 32..63), comma-separated.
        #[arg(long, value_delimiter = ',', default_value = "40")]
        host_cpus: Vec<usize>,
        /// Single host core round-robin striping across all `lanes` sub-rings.
        #[arg(long)]
        stripe: bool,
        /// Closed-loop WC reply mode (exact MRV-75 regime; zero BAR4 credit reads!).
        #[arg(long)]
        closed_loop: bool,
        /// Closed-loop in-flight window W per lane (must be <= 512).
        #[arg(long, default_value_t = 64)]
        window: u64,
        /// Cells per sub-ring (128 B stride; max 7680).
        #[arg(long, default_value_t = 4096)]
        ring_cells: usize,
        /// Card polling mode: none | ldar | ldr | ldr_dmb | ldnp.
        #[arg(long, default_value = "ldar")]
        poll_mode: String,
        /// Warmup duration in milliseconds.
        #[arg(long, default_value_t = 600)]
        warmup_ms: u64,
        /// Steady-state measurement window in milliseconds.
        #[arg(long, default_value_t = 1000)]
        duration_ms: u64,
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card: String,
        #[arg(long, default_value = "10.10.0.2:7527")]
        rpc: String,
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar: String,
        #[arg(long, default_value = "ctrl2")]
        exp: String,
        #[arg(long, default_value = "default")]
        cond: String,
        #[arg(long, default_value_t = 0)]
        rep: usize,
    }

    fn pin_socket1_cpu(cpu: usize) {
        assert!((32..=63).contains(&cpu), "REFUSING to pin outside Socket 1 (cpu32..63): got cpu {cpu}");
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu, &mut set);
            let rc = libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set);
            assert_eq!(rc, 0, "sched_setaffinity(cpu={cpu}) failed");
        }
    }

    // -------------------------------------------------------------------------
    // Host Socket 1 perf_event_open PMU (100% non-multiplexed)
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
        reserved_2: u16,
        aux_sample_size: u32,
        reserved_3: u32,
        sig_data: u64,
        config3: u64,
    }

    const PERF_TYPE_HARDWARE: u32 = 0;
    const PERF_COUNT_HW_CPU_CYCLES: u64 = 0;
    const PERF_COUNT_HW_INSTRUCTIONS: u64 = 1;
    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    struct PerfCounter { fd: libc::c_int }

    #[derive(Clone, Copy, Default)]
    struct Sample { val: u64, ena: u64, run: u64 }

    impl Sample {
        fn delta(self, prev: Self) -> Self {
            Self {
                val: self.val.wrapping_sub(prev.val),
                ena: self.ena.wrapping_sub(prev.ena),
                run: self.run.wrapping_sub(prev.run),
            }
        }
        fn pct(self) -> f64 {
            if self.ena == 0 { 100.0 } else { 100.0 * (self.run as f64) / (self.ena as f64) }
        }
    }

    impl PerfCounter {
        fn open(type_: u32, config: u64, cpu: i32) -> Result<Self, String> {
            let mut attr = PerfEventAttr {
                type_,
                size: std::mem::size_of::<PerfEventAttr>() as u32,
                config,
                read_format: PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING,
                flags: PERF_ATTR_FLAG_DISABLED,
                ..Default::default()
            };
            let fd = unsafe {
                libc::syscall(
                    libc::SYS_perf_event_open,
                    &mut attr as *mut PerfEventAttr,
                    -1 as libc::pid_t,
                    cpu as libc::c_int,
                    -1 as libc::c_int,
                    0 as libc::c_ulong,
                )
            } as libc::c_int;
            if fd < 0 {
                return Err(format!("perf_event_open(type={type_}, cfg={config:#x}, cpu={cpu}): {}", std::io::Error::last_os_error()));
            }
            Ok(Self { fd })
        }
        fn enable(&self) { unsafe { libc::ioctl(self.fd, PERF_EVENT_IOC_ENABLE, 0); } }
        fn disable(&self) { unsafe { libc::ioctl(self.fd, PERF_EVENT_IOC_DISABLE, 0); } }
        fn read(&self) -> Sample {
            let mut buf = [0u64; 3];
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast(), std::mem::size_of_val(&buf)) };
            assert_eq!(n as usize, std::mem::size_of_val(&buf));
            Sample { val: buf[0], ena: buf[1], run: buf[2] }
        }
    }

    impl Drop for PerfCounter {
        fn drop(&mut self) { unsafe { libc::close(self.fd); } }
    }

    fn read_sysfs_u32(path: &str) -> Result<u32, String> {
        let s = fs::read_to_string(path).map_err(|e| format!("read {path}: {e}"))?;
        let tok = s.trim().split(&[',', '-'][..]).next().unwrap_or("");
        tok.parse::<u32>().map_err(|e| format!("parse {path} ({tok:?}): {e}"))
    }

    struct HostPmu {
        cores: Vec<(usize, PerfCounter, PerfCounter)>,
        cha_ins: Vec<PerfCounter>,
        cha_occ: Vec<PerfCounter>,
        cha0_clk: PerfCounter,
        iio1_cpu_wr: PerfCounter,
        iio1_inb_wr: PerfCounter,
    }

    struct HostPmuSnap {
        cores: Vec<(Sample, Sample)>,
        cha_ins: Vec<Sample>,
        cha_occ: Vec<Sample>,
        cha0_clk: Sample,
        iio1_cpu_wr: Sample,
        iio1_inb_wr: Sample,
    }

    impl HostPmu {
        fn open(host_cpus: &[usize]) -> Result<Self, String> {
            const UMASK_WCILF: u64 = (0x01u64 << 8) | (0xc867ffu64 << 32);
            const CFG_CHA_INS: u64 = 0x35u64 | UMASK_WCILF;
            const CFG_CHA_OCC: u64 = 0x36u64 | UMASK_WCILF;
            const CFG_CHA_CLK: u64 = 0x01u64;
            const CFG_IIO_CPU_WR: u64 = 0xc1u64 | (1u64 << 8) | (0xffu64 << 36) | (7u64 << 48);
            const CFG_IIO_INB_WR: u64 = 0x84u64 | (1u64 << 8) | (0xffu64 << 36) | (7u64 << 48);

            let mut cores = Vec::with_capacity(host_cpus.len());
            for &cpu in host_cpus {
                let cyc = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu as i32)?;
                let ins = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu as i32)?;
                cyc.enable();
                ins.enable();
                cores.push((cpu, cyc, ins));
            }
            let mut cha_ins = Vec::with_capacity(32);
            let mut cha_occ = Vec::with_capacity(32);
            for i in 0..32 {
                let t = read_sysfs_u32(&format!("/sys/bus/event_source/devices/uncore_cha_{i}/type"))?;
                let ci = PerfCounter::open(t, CFG_CHA_INS, SOCKET1_UNCORE_CPU)?;
                let co = PerfCounter::open(t, CFG_CHA_OCC, SOCKET1_UNCORE_CPU)?;
                ci.enable();
                co.enable();
                cha_ins.push(ci);
                cha_occ.push(co);
            }
            let cha0_t = read_sysfs_u32("/sys/bus/event_source/devices/uncore_cha_0/type")?;
            let cha0_clk = PerfCounter::open(cha0_t, CFG_CHA_CLK, SOCKET1_UNCORE_CPU)?;
            cha0_clk.enable();

            let iio1_t = read_sysfs_u32("/sys/bus/event_source/devices/uncore_iio_1/type")?;
            let iio1_cpu_wr = PerfCounter::open(iio1_t, CFG_IIO_CPU_WR, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_wr = PerfCounter::open(iio1_t, CFG_IIO_INB_WR, SOCKET1_UNCORE_CPU)?;
            iio1_cpu_wr.enable();
            iio1_inb_wr.enable();

            Ok(Self { cores, cha_ins, cha_occ, cha0_clk, iio1_cpu_wr, iio1_inb_wr })
        }

        fn snap(&self) -> HostPmuSnap {
            HostPmuSnap {
                cores: self.cores.iter().map(|(_, c, i)| (c.read(), i.read())).collect(),
                cha_ins: self.cha_ins.iter().map(PerfCounter::read).collect(),
                cha_occ: self.cha_occ.iter().map(PerfCounter::read).collect(),
                cha0_clk: self.cha0_clk.read(),
                iio1_cpu_wr: self.iio1_cpu_wr.read(),
                iio1_inb_wr: self.iio1_inb_wr.read(),
            }
        }

        fn format_delta_json(&self, s0: &HostPmuSnap, s1: &HostPmuSnap, elapsed_ns: u64) -> (String, f64) {
            let mut min_pct = 100.0_f64;
            let mut upd = |s: Sample| {
                let p = s.pct();
                if p < min_pct { min_pct = p; }
            };
            let secs = elapsed_ns as f64 / 1e9;
            let mut core_parts = Vec::with_capacity(self.cores.len());
            for (idx, (cpu, _, _)) in self.cores.iter().enumerate() {
                let dc = s1.cores[idx].0.delta(s0.cores[idx].0);
                let di = s1.cores[idx].1.delta(s0.cores[idx].1);
                upd(dc);
                upd(di);
                let ghz = (dc.val as f64 / secs) / 1e9;
                core_parts.push(format!(
                    "{{\"cpu\":{cpu},\"cycles\":{},\"instructions\":{},\"ghz\":{ghz:.3}}}",
                    dc.val, di.val
                ));
            }
            let mut sum_ins: u64 = 0;
            let mut sum_occ: u64 = 0;
            for i in 0..32 {
                let di = s1.cha_ins[i].delta(s0.cha_ins[i]);
                let do_ = s1.cha_occ[i].delta(s0.cha_occ[i]);
                upd(di);
                upd(do_);
                sum_ins = sum_ins.wrapping_add(di.val);
                sum_occ = sum_occ.wrapping_add(do_.val);
            }
            let dclk = s1.cha0_clk.delta(s0.cha0_clk);
            let diio_cpu = s1.iio1_cpu_wr.delta(s0.iio1_cpu_wr);
            let diio_inb = s1.iio1_inb_wr.delta(s0.iio1_inb_wr);
            upd(dclk);
            upd(diio_cpu);
            upd(diio_inb);

            let mesh_ghz = (dclk.val as f64 / secs) / 1e9;
            let cha_inflight = if dclk.val > 0 { sum_occ as f64 / dclk.val as f64 } else { 0.0 };
            let cha_res_ns = if sum_ins > 0 && mesh_ghz > 0.0 {
                (sum_occ as f64 / sum_ins as f64) / mesh_ghz
            } else {
                0.0
            };
            let json = format!(
                "{{\"min_pct\":{min_pct:.2},\"mesh_ghz\":{mesh_ghz:.4},\"cha_ins_wcilf\":{sum_ins},\"cha_occ_wcilf\":{sum_occ},\"cha0_clk\":{},\"cha_inflight\":{cha_inflight:.3},\"cha_res_ns\":{cha_res_ns:.2},\"iio1_cpu_wr\":{},\"iio1_inb_wr\":{},\"cores\":[{}]}}",
                dclk.val, diio_cpu.val, diio_inb.val, core_parts.join(",")
            );
            (json, min_pct)
        }
    }

    impl Drop for HostPmu {
        fn drop(&mut self) {
            for (_, c, i) in &self.cores { c.disable(); i.disable(); }
            for c in &self.cha_ins { c.disable(); }
            for c in &self.cha_occ { c.disable(); }
            self.cha0_clk.disable();
            self.iio1_cpu_wr.disable();
            self.iio1_inb_wr.disable();
        }
    }

    struct RpcClient {
        reader: BufReader<TcpStream>,
        writer: TcpStream,
    }

    impl RpcClient {
        fn connect(addr: &str) -> Result<Self, Box<dyn Error>> {
            let s = TcpStream::connect(addr)?;
            s.set_nodelay(true)?;
            s.set_read_timeout(Some(Duration::from_secs(10)))?;
            let r = BufReader::new(s.try_clone()?);
            Ok(Self { reader: r, writer: s })
        }
        fn call(&mut self, cmd: &str) -> Result<String, Box<dyn Error>> {
            self.writer.write_all(cmd.as_bytes())?;
            self.writer.write_all(b"\n")?;
            let mut resp = String::new();
            let n = self.reader.read_line(&mut resp)?;
            if n == 0 {
                return Err(format!("card RPC closed on {cmd:?}").into());
            }
            Ok(resp.trim().to_string())
        }
    }

    #[derive(Clone, Copy)]
    struct SharedSlot<'a>(&'a lease::LeasedSlot<'a>);
    unsafe impl Send for SharedSlot<'_> {}
    unsafe impl Sync for SharedSlot<'_> {}
    impl SharedSlot<'_> {
        #[inline(always)]
        fn check(self) -> Result<(), String> { self.0.check() }
        #[inline(always)]
        fn as_ptr(self) -> *mut u8 { self.0.as_ptr() }
    }

    #[derive(Clone, Copy)]
    struct RawPtr(*mut u8);
    unsafe impl Send for RawPtr {}
    unsafe impl Sync for RawPtr {}
    impl RawPtr {
        #[inline(always)]
        fn as_ptr(self) -> *mut u8 { self.0 }
    }

    #[inline(always)]
    unsafe fn poll_host_reply(reply_base: *mut u8, lane: usize, cpl: usize, seq: u64) -> Option<bool> {
        let sub = ((seq - 1) as usize) % cpl;
        let nth = ((seq - 1) as usize) / cpl;
        let w = lane * cpl + sub;
        let off = (w * REPLY_CELLS_PER_WORKER + (nth % REPLY_CELLS_PER_WORKER)) * 64;
        let at = unsafe { reply_base.add(off).cast::<Cell>() };
        match unsafe { Cell::poll(at, seq) } {
            Slot::Complete(cell) => {
                if cell.tsc != 0 && cell.tag == seq && cell.payload_matches(seq) {
                    Some(true)
                } else {
                    // WC writes may rarely arrive torn; let caller retry briefly before flagging bad
                    None
                }
            }
            Slot::Empty | Slot::Partial => None,
        }
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let args = Args::parse();
        if !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }
        if args.lanes == 0 || args.lanes > MAX_LANES {
            return Err(format!("--lanes must be 1..={MAX_LANES}").into());
        }
        if args.cards_per_lane != 1 && args.cards_per_lane != 2 {
            return Err("--cards-per-lane must be 1 or 2".into());
        }
        if args.lanes * args.cards_per_lane > MAX_WORKERS {
            return Err("lanes * cards_per_lane > 8".into());
        }
        if args.ring_cells == 0 || args.ring_cells * CELL_STRIDE_BYTES > LANE_STRIDE_BYTES {
            return Err("--ring-cells exceeds lane size".into());
        }
        for &c in &args.host_cpus {
            if !(32..=63).contains(&c) {
                return Err(format!("All --host-cpus must be on Socket 1 (32..63); got {c}").into());
            }
        }
        if args.stripe {
            if args.host_cpus.len() != 1 {
                return Err("--stripe requires exactly 1 CPU in --host-cpus".into());
            }
        } else if args.host_cpus.len() != args.lanes {
            return Err(format!("--host-cpus len ({}) must equal --lanes ({})", args.host_cpus.len(), args.lanes).into());
        }
        let no_poll = args.poll_mode == "none";
        if !no_poll && !args.closed_loop {
            return Err("Use --closed-loop for polling modes, or --poll-mode none for no-poll baseline".into());
        }
        if args.closed_loop && (args.window == 0 || args.window > 512) {
            return Err("--window must be 1..=512".into());
        }

        pin_socket1_cpu(39);

        if args.closed_loop {
            let eal_args = [
                "mrv231_host".to_string(),
                "-l".to_string(),
                "39".to_string(),
                "--main-lcore".to_string(),
                "39".to_string(),
                "-a".to_string(),
                "0000:99:02.0".to_string(),
                "--file-prefix=mrv231".to_string(),
                "--log-level=lib.eal:error".to_string(),
            ];
            let (eal, _) = Eal::init(eal_args)?;
            let socket = Port::socket_of(&eal, 0);
            let bytes = MAX_WORKERS * REPLY_CELLS_PER_WORKER * 64; // 512 KiB
            let mz = Memzone::reserve_contiguous(&eal, "mrv231_replies", bytes, socket, bytes as u32)?;
            unsafe { mz.as_ptr().write_bytes(0, bytes); }
            pin_socket1_cpu(39);
            let res = run_with_reply(&args, mz.iova(), RawPtr(mz.as_ptr()));
            drop(mz);
            drop(eal);
            return res;
        }
        run_with_reply(&args, 0, RawPtr(std::ptr::null_mut()))
    }

    fn run_with_reply(args: &Args, reply_iova: u64, reply_ptr: RawPtr) -> Result<(), Box<dyn Error>> {
        let lease = Lease::acquire(&args.card)?;
        let slot = lease.map_slot(&args.bar)?;
        let shared_slot = SharedSlot(&slot);

        let mut rpc = RpcClient::connect(&args.rpc)?;
        let prep = rpc.call(&format!(
            "PREPARE {} {} {} {} {:#x}",
            args.lanes, args.cards_per_lane, args.ring_cells, args.poll_mode, reply_iova
        ))?;
        if !prep.starts_with("OK") {
            return Err(format!("PREPARE failed: {prep}").into());
        }

        let no_poll = args.poll_mode == "none";
        let k_lanes = args.lanes;
        let cpl = args.cards_per_lane;
        let ring_cells = args.ring_cells;
        let win_w = args.window;
        let pmu = HostPmu::open(&args.host_cpus)?;

        // Phase: 0 = warmup, 1 = measure, 2 = stop
        let phase = Arc::new(AtomicUsize::new(0));
        let lease_err = Arc::new(AtomicBool::new(false));

        let done_start: Vec<Arc<AtomicU64>> = (0..k_lanes).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let done_end: Vec<Arc<AtomicU64>> = (0..k_lanes).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let sent_total: Vec<Arc<AtomicU64>> = (0..k_lanes).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let done_total: Vec<Arc<AtomicU64>> = (0..k_lanes).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let bad_cnt: Vec<Arc<AtomicU64>> = (0..k_lanes).map(|_| Arc::new(AtomicU64::new(0))).collect();

        let num_workers = if args.stripe { 1 } else { k_lanes };
        let start_barrier = Arc::new(Barrier::new(num_workers + 1));

        let mut host_elapsed_ns: u64 = 0;
        let mut card_pmu_json = String::new();
        let mut host_pmu_json = String::new();
        let mut host_min_pct = 100.0_f64;

        thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            if no_poll {
                // Card-silent pure MOVDIR64B direct-push baseline (no BAR4 reads, no replies)
                for l in 0..k_lanes {
                    let cpu = args.host_cpus[l];
                    let phase = phase.clone();
                    let lease_err = lease_err.clone();
                    let bar = start_barrier.clone();
                    let ds = done_start[l].clone();
                    let de = done_end[l].clone();
                    let st = sent_total[l].clone();
                    let dt = done_total[l].clone();
                    let slot_ref = shared_slot;
                    scope.spawn(move || {
                        pin_socket1_cpu(cpu);
                        bar.wait();
                        let mut s: u64 = 1;
                        let mut latched_start = false;
                        let lane_base = l * LANE_STRIDE_BYTES;
                        loop {
                            if (s & 63) == 1 {
                                let ph = phase.load(Ordering::Acquire);
                                if ph == 1 && !latched_start {
                                    ds.store(s - 1, Ordering::Relaxed);
                                    latched_start = true;
                                } else if ph >= 2 {
                                    de.store(s - 1, Ordering::Relaxed);
                                    st.store(s - 1, Ordering::Release);
                                    dt.store(s - 1, Ordering::Release);
                                    break;
                                }
                            }
                            if slot_ref.check().is_err() {
                                lease_err.store(true, Ordering::Relaxed);
                                return;
                            }
                            let idx = ((s - 1) as usize) % ring_cells;
                            let off = lane_base + idx * CELL_STRIDE_BYTES;
                            let cell = Cell::new(s, s, 1);
                            unsafe { movdir64b(slot_ref.as_ptr().add(off), &cell) };
                            s += 1;
                        }
                    });
                }
            } else if args.stripe {
                // Single host core round-robin striping closed-loop across K independent sub-rings
                let cpu = args.host_cpus[0];
                let phase = phase.clone();
                let lease_err = lease_err.clone();
                let bar = start_barrier.clone();
                let ds = done_start.clone();
                let de = done_end.clone();
                let st = sent_total.clone();
                let dt = done_total.clone();
                let bc = bad_cnt.clone();
                let slot_ref = shared_slot;
                scope.spawn(move || {
                    pin_socket1_cpu(cpu);
                    bar.wait();
                    let mut sent = [0u64; MAX_LANES];
                    let mut done = [0u64; MAX_LANES];
                    let mut latched_start = false;
                    let mut iter: u64 = 0;
                    loop {
                        if (iter & 31) == 0 {
                            let ph = phase.load(Ordering::Acquire);
                            if ph == 1 && !latched_start {
                                for l in 0..k_lanes { ds[l].store(done[l], Ordering::Relaxed); }
                                latched_start = true;
                            } else if ph >= 2 {
                                for l in 0..k_lanes { de[l].store(done[l], Ordering::Relaxed); }
                                break;
                            }
                        }
                        for l in 0..k_lanes {
                            // Top up window W on lane l (interleaved 1-by-1 across lanes!)
                            if sent[l] - done[l] < win_w {
                                if slot_ref.check().is_err() {
                                    lease_err.store(true, Ordering::Relaxed);
                                    return;
                                }
                                let s = sent[l] + 1;
                                let idx = ((s - 1) as usize) % ring_cells;
                                let off = l * LANE_STRIDE_BYTES + idx * CELL_STRIDE_BYTES;
                                let cell = Cell::new(s, s, 1);
                                unsafe { movdir64b(slot_ref.as_ptr().add(off), &cell) };
                                sent[l] = s;
                            }
                            // Drain ready replies on lane l (up to 32)
                            let mut reaped = 0;
                            while done[l] < sent[l] && reaped < 32 {
                                let expect = done[l] + 1;
                                if unsafe { poll_host_reply(reply_ptr.as_ptr(), l, cpl, expect) }.is_some() {
                                    done[l] = expect;
                                    reaped += 1;
                                } else {
                                    break;
                                }
                            }
                        }
                        iter += 1;
                    }
                    // Drain all in-flight replies cleanly
                    let deadline = Instant::now() + Duration::from_secs(2);
                    loop {
                        let mut rem = 0u64;
                        for l in 0..k_lanes {
                            while done[l] < sent[l] {
                                let expect = done[l] + 1;
                                if unsafe { poll_host_reply(reply_ptr.as_ptr(), l, cpl, expect) }.is_some() {
                                    done[l] = expect;
                                } else {
                                    break;
                                }
                            }
                            rem += sent[l] - done[l];
                        }
                        if rem == 0 || Instant::now() > deadline {
                            break;
                        }
                        thread::yield_now();
                    }
                    for l in 0..k_lanes {
                        st[l].store(sent[l], Ordering::Release);
                        dt[l].store(done[l], Ordering::Release);
                        bc[l].store(sent[l] - done[l], Ordering::Relaxed);
                    }
                });
            } else {
                // K independent host cores, each running its own closed-loop W on sub-ring l
                for l in 0..k_lanes {
                    let cpu = args.host_cpus[l];
                    let phase = phase.clone();
                    let lease_err = lease_err.clone();
                    let bar = start_barrier.clone();
                    let ds = done_start[l].clone();
                    let de = done_end[l].clone();
                    let st = sent_total[l].clone();
                    let dt = done_total[l].clone();
                    let bc = bad_cnt[l].clone();
                    let slot_ref = shared_slot;
                    scope.spawn(move || {
                        pin_socket1_cpu(cpu);
                        bar.wait();
                        let mut sent: u64 = 0;
                        let mut done: u64 = 0;
                        let mut latched_start = false;
                        let lane_base = l * LANE_STRIDE_BYTES;
                        let mut iter: u64 = 0;
                        loop {
                            if (iter & 31) == 0 {
                                let ph = phase.load(Ordering::Acquire);
                                if ph == 1 && !latched_start {
                                    ds.store(done, Ordering::Relaxed);
                                    latched_start = true;
                                } else if ph >= 2 {
                                    de.store(done, Ordering::Relaxed);
                                    break;
                                }
                            }
                            // Fill window W (exact closed_loop.rs burst behavior!)
                            while sent - done < win_w {
                                if slot_ref.check().is_err() {
                                    lease_err.store(true, Ordering::Relaxed);
                                    return;
                                }
                                let s = sent + 1;
                                let idx = ((s - 1) as usize) % ring_cells;
                                let off = lane_base + idx * CELL_STRIDE_BYTES;
                                let cell = Cell::new(s, s, 1);
                                unsafe { movdir64b(slot_ref.as_ptr().add(off), &cell) };
                                sent = s;
                            }
                            // Poll replies (up to 32 per pass, matching closed_loop.rs)
                            let mut reaped = 0;
                            while done < sent && reaped < 32 {
                                let expect = done + 1;
                                if unsafe { poll_host_reply(reply_ptr.as_ptr(), l, cpl, expect) }.is_some() {
                                    done = expect;
                                    reaped += 1;
                                } else {
                                    break;
                                }
                            }
                            iter += 1;
                        }
                        // Drain remaining in-flight replies cleanly
                        let deadline = Instant::now() + Duration::from_secs(2);
                        while done < sent && Instant::now() < deadline {
                            let expect = done + 1;
                            if unsafe { poll_host_reply(reply_ptr.as_ptr(), l, cpl, expect) }.is_some() {
                                done = expect;
                            }
                        }
                        st.store(sent, Ordering::Release);
                        dt.store(done, Ordering::Release);
                        bc.store(sent - done, Ordering::Relaxed);
                    });
                }
            }

            start_barrier.wait();
            thread::sleep(Duration::from_millis(args.warmup_ms));

            let r_start = rpc.call("PMU_START")?;
            if r_start != "OK" {
                return Err(format!("PMU_START failed: {r_start}").into());
            }
            let h_s0 = pmu.snap();
            let t0 = Instant::now();
            phase.store(1, Ordering::Release);

            thread::sleep(Duration::from_millis(args.duration_ms));

            phase.store(2, Ordering::Release);
            host_elapsed_ns = t0.elapsed().as_nanos() as u64;
            let h_s1 = pmu.snap();
            card_pmu_json = rpc.call("PMU_STOP")?;
            let (hj, hmin) = pmu.format_delta_json(&h_s0, &h_s1, host_elapsed_ns);
            host_pmu_json = hj;
            host_min_pct = hmin;
            Ok(())
        })?;

        if lease_err.load(Ordering::Relaxed) {
            let _ = rpc.call("PAUSE");
            return Err("BAR4 slot 15 lease was revoked/closed mid-run".into());
        }

        let card_stats_json = rpc.call("STATS")?;
        let _ = rpc.call("PAUSE");
        // Wait 10 ms after card workers pause before unmapping DPDK reply memzone
        thread::sleep(Duration::from_millis(10));

        let mut total_win: u64 = 0;
        let mut total_lost: u64 = 0;
        let mut per_lane = Vec::with_capacity(k_lanes);
        let secs = host_elapsed_ns as f64 / 1e9;
        for l in 0..k_lanes {
            let dw = done_end[l].load(Ordering::Relaxed).saturating_sub(done_start[l].load(Ordering::Relaxed));
            let st = sent_total[l].load(Ordering::Relaxed);
            let dt = done_total[l].load(Ordering::Relaxed);
            let lost = st.saturating_sub(dt);
            total_win += dw;
            total_lost += lost;
            let lmops = (dw as f64 / secs) / 1e6;
            per_lane.push(format!(
                "{{\"lane\":{l},\"done_win\":{dw},\"sent_tot\":{st},\"done_tot\":{dt},\"lost\":{lost},\"mops\":{lmops:.4}}}"
            ));
        }
        let total_mops = (total_win as f64 / secs) / 1e6;
        let cpus_str = args.host_cpus.iter().map(|c| c.to_string()).collect::<Vec<_>>().join(",");

        println!(
            "MRV231_BAR4 {{\"exp\":\"{}\",\"cond\":\"{}\",\"rep\":{},\"closed_loop\":{},\"lanes\":{},\"cards_per_lane\":{cpl},\"stripe\":{},\"window\":{win_w},\"host_cpus\":[{cpus_str}],\"ring_cells\":{ring_cells},\"poll_mode\":\"{}\",\"elapsed_ns\":{host_elapsed_ns},\"total_done_win\":{total_win},\"mops\":{total_mops:.4},\"lost\":{total_lost},\"host_min_pct\":{host_min_pct:.2},\"per_lane\":[{}],\"host_pmu\":{host_pmu_json},\"card_pmu\":{card_pmu_json},\"card_stats\":{card_stats_json}}}",
            args.exp,
            args.cond,
            args.rep,
            args.closed_loop,
            k_lanes,
            args.stripe,
            args.poll_mode,
            per_lane.join(",")
        );
        Ok(())
    }
}
