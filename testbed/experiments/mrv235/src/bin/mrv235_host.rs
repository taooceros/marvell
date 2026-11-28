//! MRV-235 host-side closed-loop benchmark (x86_64 saturn, Socket 1 ONLY):
//!
//! Strictly acquires the MRV-26 BAR4 slot 15 lease (`testbed::lease`), checks
//! READY + nonce before every MOVDIR64B, pins EAL main lcore to Socket 1 cpu39
//! and worker thread to Socket 1 cpu40, and measures:
//!   - Closed-loop throughput (M/s) and bandwidth (GB/s) with `batch = 1`, `W` in `1..=512`
//!   - Exact per-message RTT (p50, p90, p99, p999, mean in ns) using `dpdk::rdtsc()`
//!     echoed in `cell.tsc` and `dpdk::rdtsc_after_loads()` (RDTSCP) on reply
//!   - Cross-check `1 / rate` vs `rtt_mean_ns` at `W = 1`
//!   - Strict end-to-end integrity (`lost = bad = torn = 0`, 1000 us WC torn retry window)
//!   - 100.0% non-multiplexed Host Socket 1 PMU (`cpu40` cycles/inst/GHz, 32x CHA WCILF,
//!     `uncore_iio_1` cpu_wr & inb_wr) synchronized with card TAD + PMUv3 + DDR5 PMUs.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv235_host runs on the x86_64 host");
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
    use dpdk::{Eal, Memzone, Port, rdtsc, rdtsc_after_loads};
    use testbed::inline::{Cell, Slot};
    use testbed::lease::{self, Lease};
    use testbed::mmio::{has_movdir64b, movdir64b};

    const MAX_WORKERS: usize = 4;
    const REPLY_CELLS_PER_WORKER: usize = 1024;
    const CELL_STRIDE_BYTES: usize = 128;
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const MAX_RTT_SAMPLES: usize = 2_000_000;

    #[derive(Parser, Debug)]
    struct Args {
        /// Number of card polling cores (1, 2, or 4).
        #[arg(long, default_value_t = 2)]
        card_cores: usize,
        /// Socket 1 host CPU (must be in 32..63; default 40).
        #[arg(long, default_value_t = 40)]
        host_cpu: usize,
        /// Closed-loop in-flight window W (1..=512).
        #[arg(long, default_value_t = 64)]
        window: u64,
        /// Cells in BAR4 slot 15 ring (128 B stride; default 4096 = 512 KiB).
        #[arg(long, default_value_t = 4096)]
        ring_cells: usize,
        /// Card page-table attribute mode: wb_huge | wb_64k | normal_nc | inner_nc_ish | inner_nc_osh | inner_nc_nsh.
        #[arg(long, default_value = "wb_huge")]
        map_mode: String,
        /// Card access/prefetch mode: full64_ldar | full64_ldr_dmb | full64_ldp | hdr16_ldp |
        /// dual_va_wb | dual_va_wb_civac | post_prfm_l3 | post_prfm_l1_nc | post_prfm_l1_wb |
        /// frontier_prfm_l1_wb | frontier_prfm_l1_nc | none.
        #[arg(long, default_value = "full64_ldar")]
        access_mode: String,
        /// Warmup duration in ms.
        #[arg(long, default_value_t = 500)]
        warmup_ms: u64,
        /// Steady-state measurement window in ms.
        #[arg(long, default_value_t = 1000)]
        duration_ms: u64,
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card: String,
        #[arg(long, default_value = "10.10.0.2:7527")]
        rpc: String,
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar: String,
        #[arg(long, default_value = "exp")]
        exp: String,
        #[arg(long, default_value = "cond")]
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
    // Host Socket 1 PMU (100% non-multiplexed)
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
        cpu: usize,
        cyc: PerfCounter,
        ins: PerfCounter,
        cha_ins: Vec<PerfCounter>,
        cha_occ: Vec<PerfCounter>,
        cha0_clk: PerfCounter,
        iio1_cpu_wr: PerfCounter,
        iio1_inb_wr: PerfCounter,
    }

    struct HostPmuSnap {
        cyc: Sample,
        ins: Sample,
        cha_ins: Vec<Sample>,
        cha_occ: Vec<Sample>,
        cha0_clk: Sample,
        iio1_cpu_wr: Sample,
        iio1_inb_wr: Sample,
    }

    impl HostPmu {
        fn open(cpu: usize) -> Result<Self, String> {
            const UMASK_WCILF: u64 = (0x01u64 << 8) | (0xc867ffu64 << 32);
            const CFG_CHA_INS: u64 = 0x35u64 | UMASK_WCILF;
            const CFG_CHA_OCC: u64 = 0x36u64 | UMASK_WCILF;
            const CFG_CHA_CLK: u64 = 0x01u64;
            const CFG_IIO_CPU_WR: u64 = 0xc1u64 | (1u64 << 8) | (0xffu64 << 36) | (7u64 << 48);
            const CFG_IIO_INB_WR: u64 = 0x84u64 | (1u64 << 8) | (0xffu64 << 36) | (7u64 << 48);

            let cyc = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu as i32)?;
            let ins = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu as i32)?;
            cyc.enable();
            ins.enable();

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

            Ok(Self { cpu, cyc, ins, cha_ins, cha_occ, cha0_clk, iio1_cpu_wr, iio1_inb_wr })
        }

        fn snap(&self) -> HostPmuSnap {
            HostPmuSnap {
                cyc: self.cyc.read(),
                ins: self.ins.read(),
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
            let dc = s1.cyc.delta(s0.cyc);
            let di = s1.ins.delta(s0.ins);
            upd(dc);
            upd(di);
            let ghz = (dc.val as f64 / secs) / 1e9;

            let mut sum_ins: u64 = 0;
            let mut sum_occ: u64 = 0;
            for i in 0..32 {
                let x = s1.cha_ins[i].delta(s0.cha_ins[i]);
                let y = s1.cha_occ[i].delta(s0.cha_occ[i]);
                upd(x);
                upd(y);
                sum_ins = sum_ins.wrapping_add(x.val);
                sum_occ = sum_occ.wrapping_add(y.val);
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
                "{{\"min_pct\":{min_pct:.2},\"cpu\":{},\"cycles\":{},\"instructions\":{},\"ghz\":{ghz:.3},\"mesh_ghz\":{mesh_ghz:.4},\"cha_ins_wcilf\":{sum_ins},\"cha_occ_wcilf\":{sum_occ},\"cha0_clk\":{},\"cha_inflight\":{cha_inflight:.3},\"cha_res_ns\":{cha_res_ns:.2},\"iio1_cpu_wr\":{},\"iio1_inb_wr\":{}}}",
                self.cpu, dc.val, di.val, dclk.val, diio_cpu.val, diio_inb.val
            );
            (json, min_pct)
        }
    }

    impl Drop for HostPmu {
        fn drop(&mut self) {
            self.cyc.disable();
            self.ins.disable();
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
            s.set_read_timeout(Some(Duration::from_secs(15)))?;
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

    enum ReplyPoll {
        Empty,
        TornOrBad,
        Ok(u64), // echoed send_tsc
    }

    #[inline(always)]
    unsafe fn poll_host_reply(reply_base: *mut u8, cpl: usize, seq: u64) -> ReplyPoll {
        let w = ((seq - 1) as usize) % cpl;
        let nth = ((seq - 1) as usize) / cpl;
        let off = (w * REPLY_CELLS_PER_WORKER + (nth % REPLY_CELLS_PER_WORKER)) * 64;
        let at = unsafe { reply_base.add(off).cast::<Cell>() };
        match unsafe { Cell::poll(at, seq) } {
            Slot::Complete(cell) => {
                if cell.tsc != 0 && cell.tag == seq && cell.payload_matches(seq) {
                    ReplyPoll::Ok(cell.tsc)
                } else {
                    ReplyPoll::TornOrBad
                }
            }
            Slot::Partial => ReplyPoll::TornOrBad,
            Slot::Empty => ReplyPoll::Empty,
        }
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let args = Args::parse();
        if !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }
        if !matches!(args.card_cores, 1 | 2 | 4) {
            return Err("--card-cores must be 1, 2, or 4".into());
        }
        if !(32..=63).contains(&args.host_cpu) {
            return Err(format!("--host-cpu must be on Socket 1 (32..63), got {}", args.host_cpu).into());
        }
        if args.window == 0 || args.window > 512 {
            return Err("--window must be 1..=512".into());
        }

        pin_socket1_cpu(39);

        let eal_args = [
            "mrv235_host".to_string(),
            "-l".to_string(),
            "39".to_string(),
            "--main-lcore".to_string(),
            "39".to_string(),
            "-a".to_string(),
            "0000:99:02.0".to_string(),
            "--file-prefix=mrv235".to_string(),
            "--log-level=lib.eal:error".to_string(),
        ];
        let (eal, _) = Eal::init(eal_args)?;
        let tsc_hz = eal.tsc_hz();
        let socket = Port::socket_of(&eal, 0);
        let bytes = MAX_WORKERS * REPLY_CELLS_PER_WORKER * 64;
        let mz = Memzone::reserve_contiguous(&eal, "mrv235_replies", bytes, socket, bytes as u32)?;
        unsafe { mz.as_ptr().write_bytes(0, bytes); }
        pin_socket1_cpu(39);

        let res = run_bench(&args, tsc_hz, mz.iova(), RawPtr(mz.as_ptr()));
        drop(mz);
        drop(eal);
        res
    }

    fn run_bench(args: &Args, tsc_hz: u64, reply_iova: u64, reply_ptr: RawPtr) -> Result<(), Box<dyn Error>> {
        let lease = Lease::acquire(&args.card)?;
        let slot = lease.map_slot(&args.bar)?;
        let shared_slot = SharedSlot(&slot);

        let mut rpc = RpcClient::connect(&args.rpc)?;
        let no_poll = args.access_mode == "none";
        let riova_arg = if no_poll { 0 } else { reply_iova };
        let prep = rpc.call(&format!(
            "PREPARE {} {} {} {} {:#x}",
            args.card_cores, args.ring_cells, args.map_mode, args.access_mode, riova_arg
        ))?;
        if !prep.starts_with("OK") {
            return Err(format!("PREPARE failed: {prep}").into());
        }

        let pmu = HostPmu::open(args.host_cpu)?;
        let phase = Arc::new(AtomicUsize::new(0));
        let lease_err = Arc::new(AtomicBool::new(false));
        let start_barrier = Arc::new(Barrier::new(2));

        let done_start = Arc::new(AtomicU64::new(0));
        let done_end = Arc::new(AtomicU64::new(0));
        let sent_total = Arc::new(AtomicU64::new(0));
        let done_total = Arc::new(AtomicU64::new(0));
        let torn_retries = Arc::new(AtomicU64::new(0));
        let bad_or_torn = Arc::new(AtomicU64::new(0));
        let rtt_samples = Arc::new(std::sync::Mutex::new(Vec::<u32>::new()));

        let cpl = args.card_cores;
        let ring_cells = args.ring_cells;
        let win_w = args.window;
        let host_cpu = args.host_cpu;

        let mut host_elapsed_ns: u64 = 0;
        let mut card_pmu_json = String::new();
        let mut host_pmu_json = String::new();
        let mut host_min_pct = 100.0_f64;

        thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            let phase_w = phase.clone();
            let lease_err_w = lease_err.clone();
            let bar_w = start_barrier.clone();
            let ds_w = done_start.clone();
            let de_w = done_end.clone();
            let st_w = sent_total.clone();
            let dt_w = done_total.clone();
            let tr_w = torn_retries.clone();
            let bt_w = bad_or_torn.clone();
            let rtt_w = rtt_samples.clone();

            scope.spawn(move || {
                pin_socket1_cpu(host_cpu);
                let mut local_rtts: Vec<u32> = Vec::with_capacity(if no_poll { 0 } else { MAX_RTT_SAMPLES });
                // Sample every message for W <= 4, 1 in 16 for W > 4 so 1 s fits comfortably in 2M samples
                let sample_mask: u64 = if win_w <= 4 { 0 } else { 15 };
                bar_w.wait();

                if no_poll {
                    let mut s: u64 = 1;
                    let mut latched_start = false;
                    loop {
                        if (s & 63) == 1 {
                            let ph = phase_w.load(Ordering::Acquire);
                            if ph == 1 && !latched_start {
                                ds_w.store(s - 1, Ordering::Relaxed);
                                latched_start = true;
                            } else if ph >= 2 {
                                de_w.store(s - 1, Ordering::Relaxed);
                                st_w.store(s - 1, Ordering::Release);
                                dt_w.store(s - 1, Ordering::Release);
                                break;
                            }
                        }
                        if shared_slot.check().is_err() {
                            lease_err_w.store(true, Ordering::Relaxed);
                            return;
                        }
                        let idx = ((s - 1) as usize) % ring_cells;
                        let off = idx * CELL_STRIDE_BYTES;
                        let cell = Cell::new(s, s, 1);
                        unsafe { movdir64b(shared_slot.as_ptr().add(off), &cell) };
                        s += 1;
                    }
                    return;
                }

                let mut sent: u64 = 0;
                let mut done: u64 = 0;
                let mut latched_start = false;
                let mut measuring = false;
                let mut iter: u64 = 0;
                let mut local_torn_retries: u64 = 0;
                let mut local_bad_or_torn: u64 = 0;

                loop {
                    if (iter & 31) == 0 || win_w == 1 {
                        let ph = phase_w.load(Ordering::Acquire);
                        if ph == 1 && !latched_start {
                            ds_w.store(done, Ordering::Relaxed);
                            latched_start = true;
                            measuring = true;
                        } else if ph >= 2 {
                            de_w.store(done, Ordering::Relaxed);
                            break;
                        }
                    }

                    // Fill window W
                    while sent - done < win_w {
                        if shared_slot.check().is_err() {
                            lease_err_w.store(true, Ordering::Relaxed);
                            return;
                        }
                        let s = sent + 1;
                        let idx = ((s - 1) as usize) % ring_cells;
                        let off = idx * CELL_STRIDE_BYTES;
                        let t_send = if measuring && ((s & sample_mask) == 0) {
                            let t = rdtsc();
                            if t == 0 { 1 } else { t }
                        } else {
                            1
                        };
                        let cell = Cell::new(s, s, t_send);
                        unsafe { movdir64b(shared_slot.as_ptr().add(off), &cell) };
                        sent = s;
                    }

                    // Poll replies (up to 32 per pass)
                    let mut reaped = 0;
                    while done < sent && reaped < 32 {
                        let expect = done + 1;
                        match unsafe { poll_host_reply(reply_ptr.as_ptr(), cpl, expect) } {
                            ReplyPoll::Ok(echo_tsc) => {
                                if measuring && ((expect & sample_mask) == 0) && echo_tsc > 1 && local_rtts.len() < MAX_RTT_SAMPLES {
                                    let now = rdtsc_after_loads();
                                    let dt = now.wrapping_sub(echo_tsc);
                                    local_rtts.push(dt.min(u32::MAX as u64) as u32);
                                }
                                done = expect;
                                reaped += 1;
                            }
                            ReplyPoll::TornOrBad => {
                                local_torn_retries += 1;
                                break;
                            }
                            ReplyPoll::Empty => break,
                        }
                    }
                    iter += 1;
                }

                // Drain remaining in-flight replies cleanly
                let deadline = Instant::now() + Duration::from_secs(2);
                while done < sent && Instant::now() < deadline {
                    let expect = done + 1;
                    match unsafe { poll_host_reply(reply_ptr.as_ptr(), cpl, expect) } {
                        ReplyPoll::Ok(_) => {
                            done = expect;
                        }
                        ReplyPoll::TornOrBad => {
                            local_torn_retries += 1;
                            if Instant::now() + Duration::from_millis(10) >= deadline {
                                local_bad_or_torn += 1;
                                break;
                            }
                        }
                        ReplyPoll::Empty => {}
                    }
                }
                if done < sent {
                    local_bad_or_torn += sent - done;
                }

                st_w.store(sent, Ordering::Release);
                dt_w.store(done, Ordering::Release);
                tr_w.store(local_torn_retries, Ordering::Relaxed);
                bt_w.store(local_bad_or_torn, Ordering::Relaxed);
                *rtt_w.lock().unwrap() = local_rtts;
            });

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
            return Err("BAR4 slot 15 lease revoked mid-run".into());
        }

        let card_stats_json = rpc.call("STATS")?;
        let _ = rpc.call("PAUSE");
        thread::sleep(Duration::from_millis(10));

        let done_win = done_end.load(Ordering::Relaxed).saturating_sub(done_start.load(Ordering::Relaxed));
        let st = sent_total.load(Ordering::Relaxed);
        let dt = done_total.load(Ordering::Relaxed);
        let lost = st.saturating_sub(dt);
        let secs = host_elapsed_ns as f64 / 1e9;
        let mops = (done_win as f64 / secs) / 1e6;
        let gb_s = (done_win as f64 * 64.0 / secs) / 1e9;
        let inv_rate_ns = if mops > 0.0 { 1000.0 / mops } else { 0.0 };

        let mut samples = rtt_samples.lock().unwrap();
        let n_rtt = samples.len();
        let (rtt_mean_ns, rtt_p50_ns, rtt_p90_ns, rtt_p99_ns, rtt_p999_ns) = if n_rtt > 0 {
            let sum: u64 = samples.iter().map(|&x| x as u64).sum();
            samples.sort_unstable();
            let tick_to_ns = 1e9_f64 / (tsc_hz as f64);
            let pct = |num: usize, den: usize| -> f64 {
                let idx = ((n_rtt - 1) * num) / den;
                samples[idx] as f64 * tick_to_ns
            };
            (
                (sum as f64 / n_rtt as f64) * tick_to_ns,
                pct(50, 100),
                pct(90, 100),
                pct(99, 100),
                pct(999, 1000),
            )
        } else {
            (0.0, 0.0, 0.0, 0.0, 0.0)
        };

        println!(
            "MRV235_RUN {{\"exp\":\"{}\",\"cond\":\"{}\",\"rep\":{},\"card_cores\":{cpl},\"host_cpu\":{host_cpu},\"window\":{win_w},\"batch\":1,\"ring_cells\":{ring_cells},\"cell_stride\":{CELL_STRIDE_BYTES},\"map_mode\":\"{}\",\"access_mode\":\"{}\",\"elapsed_ns\":{host_elapsed_ns},\"done_win\":{done_win},\"sent_tot\":{st},\"done_tot\":{dt},\"mops\":{mops:.4},\"gb_s\":{gb_s:.4},\"inv_rate_ns\":{inv_rate_ns:.2},\"rtt_samples\":{n_rtt},\"rtt_mean_ns\":{rtt_mean_ns:.2},\"rtt_p50_ns\":{rtt_p50_ns:.2},\"rtt_p90_ns\":{rtt_p90_ns:.2},\"rtt_p99_ns\":{rtt_p99_ns:.2},\"rtt_p999_ns\":{rtt_p999_ns:.2},\"lost\":{lost},\"bad_or_torn\":{},\"torn_retries\":{},\"host_min_pct\":{host_min_pct:.2},\"host_pmu\":{host_pmu_json},\"card_pmu\":{card_pmu_json},\"card_stats\":{card_stats_json}}}",
            args.exp,
            args.cond,
            args.rep,
            args.map_mode,
            args.access_mode,
            bad_or_torn.load(Ordering::Relaxed),
            torn_retries.load(Ordering::Relaxed),
        );
        Ok(())
    }
}
