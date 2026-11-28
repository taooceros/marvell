//! MRV-245 host-side benchmark (x86_64 saturn, Socket 1 ONLY):
//!
//! Strictly acquires the MRV-26 BAR4 slot 15 lease (`testbed::lease`), verifies
//! READY + nonce before every MOVDIR64B, pins EAL main lcore to Socket 1 cpu39
//! and host worker threads to Socket 1 cpu40 (H=1) or cpu40 + cpu41 (H=2).
//!
//! Supports all three MRV-245 experiment groups on the exact same BAR4 slot 15 harness:
//!   - Group 1 (`tx_oneway`): full-64 B verified one-way TX (no per-packet reply,
//!     coarse WC progress credit to prevent ring wrap overwrite), H=1/2, K=1..12,
//!     unroll=1 vs unroll=4, plus card-silent (`none`) MOVDIR64B ceiling.
//!   - Group 2 (`closed`): high-concurrency closed-loop ping-pong (`W = 1, 64, 256, 512`),
//!     H=1/2, K=1/4/8, full 64 B reply verification and exact RDTSCP RTT percentiles.
//!   - Group 3 (head-to-head vs Software Pacing & Decoupled Coalesced Doorbell):
//!       * `--pace-mode hyst`  : MRV-115/123 host credit + hysteresis batch release (honest tot RTT)
//!       * `--pace-mode burst` : host burst + inter-burst gap pacing (works in closed & tx_oneway)
//!       * `--doorbell adaptive|batch` : MRV-177 Arm B2 SFENCE + coalesced `prod_idx` doorbell.
//!
//! Collects 100.0% non-multiplexed Host Socket 1 PMU (`cpu40`/`cpu41` cycles/inst/GHz,
//! 32x CHA WCILF, `uncore_iio_1` cpu_wr & inb_wr) synchronized with card TAD/PMUv3/DDR5.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv245_host runs on the x86_64 host");
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
    use std::sync::{Arc, Barrier, Mutex};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::Parser;
    use dpdk::{Eal, Memzone, Port, rdtsc, rdtsc_after_loads};
    use testbed::inline::{Cell, SLOT_BYTES, Slot};
    use testbed::lease::{self, Lease};
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};

    const MAX_CARD_WORKERS: usize = 12;
    const REPLY_CELLS_PER_WORKER: usize = 1024;
    const CELL_STRIDE_BYTES: usize = 128;
    const DOORBELL_OFFSET_BASE: usize = SLOT_BYTES - 4096;
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const MAX_RTT_SAMPLES_PER_LANE: usize = 4_000_000;
    const HOST_CPUS: [usize; 2] = [40, 41];

    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct DoorbellCell {
        prod_idx: u64,
        pad: [u64; 6],
        prod_tail: u64,
    }
    const _: () = assert!(std::mem::size_of::<DoorbellCell>() == 64);
    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct FastCell([u64; 8]);
    const _: () = assert!(std::mem::size_of::<FastCell>() == 64);

    #[inline(always)]
    fn make_fast_cell(seq: u64, tsc: u64) -> FastCell {
        const K: u64 = 0xa5a5_a5a5_a5a5_a5a5;
        const M: u64 = 0x9e37_79b9_7f4a_7c15;
        FastCell([
            seq,
            tsc,
            seq,
            seq.wrapping_mul(M),
            (seq ^ K).wrapping_mul(M),
            (seq ^ K.wrapping_mul(2)).wrapping_mul(M),
            (seq ^ K.wrapping_mul(3)).wrapping_mul(M),
            seq,
        ])
    }

    #[derive(Parser, Debug)]
    struct Args {
        #[arg(long, default_value_t = 1)]
        host_cores: usize,
        /// Total number of card worker cores (1..=12, divisible by host_cores).
        #[arg(long, default_value_t = 4)]
        card_cores: usize,
        /// Workload mode: tx_oneway | closed.
        #[arg(long, default_value = "tx_oneway")]
        work_mode: String,
        /// Total in-flight window W (closed: 1..=512; tx_oneway: per-lane credit window, default 6144).
        #[arg(long, default_value_t = 6144)]
        window: u64,
        /// Ring cells per host lane (power of 2; 128 B stride).
        #[arg(long, default_value_t = 8192)]
        ring_cells: usize,
        /// Card page-table attribute mode: wb_huge | wb_64k | normal_nc.
        #[arg(long, default_value = "normal_nc")]
        map_mode: String,
        /// Card polling kernel: none | u1_ldp | u1_ldpq | u4_ldpq | doorbell.
        #[arg(long, default_value = "u1_ldp")]
        poll_mode: String,
        /// Card credit update step per worker in tx_oneway mode.
        #[arg(long, default_value_t = 128)]
        credit_step: u64,
        /// Software pacing mode: none | hyst | burst.
        #[arg(long, default_value = "none")]
        pace_mode: String,
        /// For `hyst`: per-lane on-wire credit cap C.
        #[arg(long, default_value_t = 32)]
        pace_credit: u64,
        /// For `hyst`: hysteresis release batch B; for `burst`: packets per burst B.
        #[arg(long, default_value_t = 16)]
        pace_batch: u64,
        /// For `burst`: inter-burst pause in ns.
        #[arg(long, default_value_t = 0)]
        pace_gap_ns: u64,
        /// Doorbell mode: none | adaptive | batch.
        #[arg(long, default_value = "none")]
        doorbell: String,
        /// Issue SFENCE before doorbell MOVDIR64B (default true).
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        db_sfence: bool,
        /// Coalesced doorbell batch size (packets per doorbell under load).
        #[arg(long, default_value_t = 32)]
        db_batch: u64,
        /// Adaptive doorbell light-load immediate threshold (inflight <= db_light rings immediately).
        #[arg(long, default_value_t = 4)]
        db_light: u64,
        /// Adaptive doorbell max holdback flush timeout in ns.
        #[arg(long, default_value_t = 500)]
        db_flush_ns: u64,
        /// Open-loop Poisson target rate in Mmsg/s (0.0 = unpaced).
        #[arg(long, default_value_t = 0.0)]
        target_mpps: f64,
        /// Co-located Socket-1 neighbor workload: none | stream6_l3.
        #[arg(long, default_value = "none")]
        neighbor: String,
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
    // Host Socket 1 PMU (100% non-multiplexed, supports H=1 or H=2)
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
        cpus: Vec<usize>,
        cyc: Vec<PerfCounter>,
        ins: Vec<PerfCounter>,
        cha_ins: Vec<PerfCounter>,
        cha_occ: Vec<PerfCounter>,
        cha0_clk: PerfCounter,
        iio1_cpu_wr: PerfCounter,
        iio1_inb_wr: PerfCounter,
    }

    struct HostPmuSnap {
        cyc: Vec<Sample>,
        ins: Vec<Sample>,
        cha_ins: Vec<Sample>,
        cha_occ: Vec<Sample>,
        cha0_clk: Sample,
        iio1_cpu_wr: Sample,
        iio1_inb_wr: Sample,
    }

    impl HostPmu {
        fn open(cpus: &[usize]) -> Result<Self, String> {
            const UMASK_WCILF: u64 = (0x01u64 << 8) | (0xc867ffu64 << 32);
            const CFG_CHA_INS: u64 = 0x35u64 | UMASK_WCILF;
            const CFG_CHA_OCC: u64 = 0x36u64 | UMASK_WCILF;
            const CFG_CHA_CLK: u64 = 0x01u64;
            const CFG_IIO_CPU_WR: u64 = 0xc1u64 | (1u64 << 8) | (0xffu64 << 36) | (7u64 << 48);
            const CFG_IIO_INB_WR: u64 = 0x84u64 | (1u64 << 8) | (0xffu64 << 36) | (7u64 << 48);

            let mut cyc = Vec::with_capacity(cpus.len());
            let mut ins = Vec::with_capacity(cpus.len());
            for &cpu in cpus {
                let c = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu as i32)?;
                let i = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu as i32)?;
                c.enable();
                i.enable();
                cyc.push(c);
                ins.push(i);
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

            Ok(Self {
                cpus: cpus.to_vec(),
                cyc,
                ins,
                cha_ins,
                cha_occ,
                cha0_clk,
                iio1_cpu_wr,
                iio1_inb_wr,
            })
        }

        fn snap(&self) -> HostPmuSnap {
            HostPmuSnap {
                cyc: self.cyc.iter().map(PerfCounter::read).collect(),
                ins: self.ins.iter().map(PerfCounter::read).collect(),
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
            let mut cpu_parts = Vec::with_capacity(self.cpus.len());
            let mut ghz0 = 0.0;
            let mut cyc0 = 0u64;
            let mut ins0 = 0u64;
            for idx in 0..self.cpus.len() {
                let dc = s1.cyc[idx].delta(s0.cyc[idx]);
                let di = s1.ins[idx].delta(s0.ins[idx]);
                upd(dc);
                upd(di);
                let g = (dc.val as f64 / secs) / 1e9;
                if idx == 0 {
                    ghz0 = g;
                    cyc0 = dc.val;
                    ins0 = di.val;
                }
                cpu_parts.push(format!("{{\"cpu\":{},\"cycles\":{},\"instructions\":{},\"ghz\":{g:.3}}}", self.cpus[idx], dc.val, di.val));
            }

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
                "{{\"min_pct\":{min_pct:.2},\"cpu\":{},\"cycles\":{cyc0},\"instructions\":{ins0},\"ghz\":{ghz0:.3},\"cpus\":[{}],\"mesh_ghz\":{mesh_ghz:.4},\"cha_ins_wcilf\":{sum_ins},\"cha_occ_wcilf\":{sum_occ},\"cha0_clk\":{},\"cha_inflight\":{cha_inflight:.3},\"cha_res_ns\":{cha_res_ns:.2},\"iio1_cpu_wr\":{},\"iio1_inb_wr\":{}}}",
                self.cpus[0], cpu_parts.join(","), dclk.val, diio_cpu.val, diio_inb.val
            );
            (json, min_pct)
        }
    }

    impl Drop for HostPmu {
        fn drop(&mut self) {
            for c in &self.cyc { c.disable(); }
            for c in &self.ins { c.disable(); }
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
        Ok(u64),
    }

    #[inline(always)]
    unsafe fn poll_host_reply(reply_base: *mut u8, lane_first_worker: usize, k_per_lane: usize, seq: u64) -> ReplyPoll {
        let lw = ((seq - 1) as usize) % k_per_lane;
        let w = lane_first_worker + lw;
        let nth = ((seq - 1) as usize) / k_per_lane;
        let off = (w * REPLY_CELLS_PER_WORKER + (nth & (REPLY_CELLS_PER_WORKER - 1))) * 64;
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

    #[inline(always)]
    unsafe fn read_card_min_next(
        credit_base: *mut u8,
        lane_first_worker: usize,
        k_per_lane: usize,
        cached: &mut [u64],
    ) -> u64 {
        let mut min_n = u64::MAX;
        for lw in 0..k_per_lane {
            let w = lane_first_worker + lw;
            let p = unsafe { credit_base.add(w * 64).cast::<u64>() };
            let head = unsafe { AtomicU64::from_ptr(p) }.load(Ordering::Acquire);
            let tail = unsafe { p.add(7).read_volatile() };
            if head == tail && head > cached[lw] {
                cached[lw] = head;
            }
            if cached[lw] < min_n {
                min_n = cached[lw];
            }
        }
        min_n
    }

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum DbMode {
        None,
        Adaptive,
        Batch,
    }

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum PaceMode {
        None,
        Hyst,
        Burst,
    }

    struct DbState {
        mode: DbMode,
        sfence_before: bool,
        batch: u64,
        light: u64,
        flush_ticks: u64,
        db_off: usize,
        last_db_sent: u64,
        last_db_tsc: u64,
        rings_total: u64,
        rings_win: u64,
    }

    impl DbState {
        #[inline(always)]
        fn ring_now(&mut self, slot: SharedSlot, sent: u64, now: u64, measuring: bool) -> Result<(), String> {
            slot.check()?;
            if self.sfence_before {
                sfence();
            }
            let cell = DoorbellCell { prod_idx: sent, pad: [0; 6], prod_tail: sent };
            unsafe { movdir64b(slot.as_ptr().add(self.db_off), &cell) };
            self.last_db_sent = sent;
            self.last_db_tsc = now;
            self.rings_total += 1;
            if measuring {
                self.rings_win += 1;
            }
            Ok(())
        }
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let args = Args::parse();
        if !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }
        if !matches!(args.host_cores, 1 | 2) {
            return Err("--host-cores must be 1 or 2".into());
        }
        if !(1..=MAX_CARD_WORKERS).contains(&args.card_cores) || args.card_cores % args.host_cores != 0 {
            return Err(format!("--card-cores ({}) must be in 1..={MAX_CARD_WORKERS} and divisible by --host-cores ({})", args.card_cores, args.host_cores).into());
        }
        if args.work_mode != "tx_oneway" && args.work_mode != "closed" && args.work_mode != "idle_neighbor" {
            return Err("--work-mode must be tx_oneway, closed, or idle_neighbor".into());
        }
        if args.neighbor != "none" && args.neighbor != "stream6_l3" {
            return Err("--neighbor must be none or stream6_l3".into());
        }
        if args.work_mode == "closed" && (args.window == 0 || args.window > 512 || args.window % args.host_cores as u64 != 0) {
            return Err(format!("closed --window ({}) must be in 1..=512 and divisible by host_cores", args.window).into());
        }
        if args.work_mode == "tx_oneway" && args.window >= args.ring_cells as u64 {
            return Err(format!("tx_oneway --window ({}) must be < --ring-cells ({})", args.window, args.ring_cells).into());
        }

        pin_socket1_cpu(39);

        let eal_args = [
            "mrv252_host".to_string(),
            "-l".to_string(),
            "39".to_string(),
            "--main-lcore".to_string(),
            "39".to_string(),
            "-a".to_string(),
            "0000:99:02.0".to_string(),
            "--file-prefix=mrv252".to_string(),
            "--log-level=lib.eal:error".to_string(),
        ];
        let (eal, _) = Eal::init(eal_args)?;
        let tsc_hz = eal.tsc_hz();
        let socket = Port::socket_of(&eal, 0);
        // 1 MiB contiguous memzone (fits 12 * 1024 * 64 B = 768 KiB reply rings OR 12 * 64 B credit slots)
        let bytes: usize = 1024 * 1024;
        let mz = Memzone::reserve_contiguous(&eal, "mrv252_replies", bytes, socket, bytes as u32)?;
        unsafe { mz.as_ptr().write_bytes(0, bytes); }
        pin_socket1_cpu(39);

        let res = run_bench(&args, tsc_hz, mz.iova(), RawPtr(mz.as_ptr()));
        drop(mz);
        drop(eal);
        res
    }

    struct LaneOut {
        done_win: u64,
        sent_win: u64,
        sent_tot: u64,
        done_tot: u64,
        torn_retries: u64,
        bad_or_torn: u64,
        db_rings_win: u64,
        db_rings_tot: u64,
        rtts: Vec<u32>,
    }
    struct NeighborOut {
        stream_gbps: f64,
        l3_p50_ns: f64,
        l3_p99_ns: f64,
        l3_samples: usize,
    }

    fn run_idle_neighbor(args: &Args, tsc_hz: u64) -> Result<(), Box<dyn Error>> {
        let active_host_cpus = &HOST_CPUS[..args.host_cores];
        let pmu = HostPmu::open(active_host_cpus)?;
        let phase = Arc::new(AtomicUsize::new(0));
        let start_barrier = Arc::new(Barrier::new(1 + if args.neighbor == "stream6_l3" { 7 } else { 0 }));
        let stream_bytes = Arc::new(std::sync::atomic::AtomicU64::new(0));
        let l3_out: Arc<Mutex<Vec<f64>>> = Arc::new(Mutex::new(Vec::new()));
        let mut host_elapsed_ns: u64 = 0;
        let mut host_pmu_json = String::new();
        let mut host_min_pct = 100.0_f64;

        thread::scope(|scope| {
            if args.neighbor == "stream6_l3" {
                spawn_neighbor_threads(scope, &phase, &start_barrier, &stream_bytes, &l3_out, tsc_hz);
            }
            start_barrier.wait();
            thread::sleep(Duration::from_millis(args.warmup_ms));
            let h_s0 = pmu.snap();
            let t0 = Instant::now();
            phase.store(1, Ordering::Release);
            thread::sleep(Duration::from_millis(args.duration_ms));
            phase.store(2, Ordering::Release);
            host_elapsed_ns = t0.elapsed().as_nanos() as u64;
            let h_s1 = pmu.snap();
            let (hj, hmin) = pmu.format_delta_json(&h_s0, &h_s1, host_elapsed_ns);
            host_pmu_json = hj;
            host_min_pct = hmin;
        });

        let nb = collect_neighbor(&stream_bytes, &l3_out, host_elapsed_ns);
        println!(
            "MRV252_RUN {{\"exp\":\"{}\",\"cond\":\"{}\",\"rep\":{},\"host_cores\":0,\"card_cores\":0,\"work_mode\":\"idle_neighbor\",\"window\":0,\"window_per_lane\":0,\"batch\":1,\"ring_cells\":0,\"cell_stride\":{CELL_STRIDE_BYTES},\"map_mode\":\"none\",\"poll_mode\":\"none\",\"target_mpps\":0.0,\"neighbor\":\"{}\",\"stream_gbps\":{:.3},\"l3_p50_ns\":{:.2},\"l3_p99_ns\":{:.2},\"l3_samples\":{},\"elapsed_ns\":{host_elapsed_ns},\"done_win\":0,\"sent_win\":0,\"sent_tot\":0,\"done_tot\":0,\"mops\":0.0,\"lane_mops\":[],\"gb_s\":0.0,\"inv_rate_ns\":0.0,\"rtt_samples\":0,\"rtt_mean_ns\":0.0,\"rtt_p50_ns\":0.0,\"rtt_p90_ns\":0.0,\"rtt_p99_ns\":0.0,\"rtt_p999_ns\":0.0,\"lost\":0,\"bad_or_torn\":0,\"torn_retries\":0,\"host_min_pct\":{host_min_pct:.2},\"host_pmu\":{host_pmu_json},\"card_pmu\":{{}},\"card_stats\":{{}}}}",
            args.exp, args.cond, args.rep, args.neighbor,
            nb.stream_gbps, nb.l3_p50_ns, nb.l3_p99_ns, nb.l3_samples,
        );
        Ok(())
    }

    fn spawn_neighbor_threads<'scope>(
        scope: &'scope thread::Scope<'scope, '_>,
        phase: &Arc<AtomicUsize>,
        barrier: &Arc<Barrier>,
        stream_bytes: &Arc<std::sync::atomic::AtomicU64>,
        l3_out: &Arc<Mutex<Vec<f64>>>,
        tsc_hz: u64,
    ) {
        // 6 STREAM Triad threads on disjoint Socket-1 physical cores cpu50..55
        for i in 0..6usize {
            let ph = phase.clone();
            let bar = barrier.clone();
            let sb = stream_bytes.clone();
            let cpu = 50 + i;
            scope.spawn(move || {
                pin_socket1_cpu(cpu);
                const N: usize = 4 * 1024 * 1024; // 32 MiB per array -> 96 MiB per thread (576 MiB total > L3)
                let mut a = vec![1.0_f64; N];
                let b = vec![2.0_f64; N];
                let c = vec![3.0_f64; N];
                let scalar = 3.0_f64;
                bar.wait();
                let mut bytes_win: u64 = 0;
                loop {
                    let p = ph.load(Ordering::Acquire);
                    if p >= 2 {
                        break;
                    }
                    let mut j = 0;
                    while j < N {
                        let end = (j + 4096).min(N);
                        for k in j..end {
                            unsafe {
                                *a.get_unchecked_mut(k) = *b.get_unchecked(k) + scalar * *c.get_unchecked(k);
                            }
                        }
                        if ph.load(Ordering::Relaxed) == 1 {
                            bytes_win += ((end - j) * 24) as u64; // STREAM Triad: 3 * 8 B
                        }
                        if ph.load(Ordering::Relaxed) >= 2 {
                            break;
                        }
                        j = end;
                    }
                }
                std::hint::black_box(&a);
                sb.fetch_add(bytes_win, Ordering::Relaxed);
            });
        }
        // 16 MiB L3 pointer-chasing latency victim on disjoint Socket-1 physical core cpu56
        {
            let ph = phase.clone();
            let bar = barrier.clone();
            let out = l3_out.clone();
            scope.spawn(move || {
                pin_socket1_cpu(56);
                const LINES: usize = (16 * 1024 * 1024) / 64; // 262,144 cache lines = 16 MiB
                let mut perm: Vec<usize> = (0..LINES).collect();
                let mut rng: u64 = 0x9e37_79b9_7f4a_7c15;
                for i in (1..LINES).rev() {
                    rng ^= rng >> 12;
                    rng ^= rng << 25;
                    rng ^= rng >> 27;
                    let r = rng.wrapping_mul(0x2545_f491_4f6c_dd1d);
                    let j = (r as usize) % i;
                    perm.swap(i, j);
                }
                let mut buf = vec![0usize; LINES * 8]; // 64 B stride (8 * usize)
                for i in 0..LINES {
                    buf[perm[i] * 8] = perm[(i + 1) % LINES] * 8;
                }
                let tick_to_ns = 1e9_f64 / (tsc_hz as f64);
                let mut samples: Vec<f64> = Vec::with_capacity(200_000);
                let mut idx: usize = 0;
                bar.wait();
                loop {
                    let p = ph.load(Ordering::Acquire);
                    if p >= 2 {
                        break;
                    }
                    let t0 = dpdk::rdtsc_after_loads();
                    let mut cur = std::hint::black_box(idx);
                    for _ in 0..64 {
                        cur = unsafe { *buf.get_unchecked(cur) };
                    }
                    idx = std::hint::black_box(cur);
                    let t1 = dpdk::rdtsc_after_loads();
                    if p == 1 && samples.len() < 200_000 {
                        samples.push((t1.wrapping_sub(t0) as f64) * tick_to_ns / 64.0);
                    }
                }
                std::hint::black_box(idx);
                *out.lock().unwrap() = samples;
            });
        }
    }

    fn collect_neighbor(
        stream_bytes: &Arc<std::sync::atomic::AtomicU64>,
        l3_out: &Arc<Mutex<Vec<f64>>>,
        elapsed_ns: u64,
    ) -> NeighborOut {
        let sb = stream_bytes.load(Ordering::Relaxed);
        let secs = (elapsed_ns as f64) / 1e9;
        let stream_gbps = if secs > 0.0 { (sb as f64 / secs) / 1e9 } else { 0.0 };
        let mut s = l3_out.lock().unwrap().clone();
        let n = s.len();
        let (p50, p99) = if n > 0 {
            s.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            (s[(n - 1) * 50 / 100], s[(n - 1) * 99 / 100])
        } else {
            (0.0, 0.0)
        };
        NeighborOut {
            stream_gbps,
            l3_p50_ns: p50,
            l3_p99_ns: p99,
            l3_samples: n,
        }
    }

    fn run_bench(args: &Args, tsc_hz: u64, reply_iova: u64, reply_ptr: RawPtr) -> Result<(), Box<dyn Error>> {
        if args.work_mode == "idle_neighbor" {
            return run_idle_neighbor(args, tsc_hz);
        }
        let db_mode = match args.doorbell.as_str() {
            "none" => DbMode::None,
            "adaptive" => DbMode::Adaptive,
            "batch" => DbMode::Batch,
            other => return Err(format!("unknown --doorbell {other}").into()),
        };
        let pace_mode = match args.pace_mode.as_str() {
            "none" => PaceMode::None,
            "hyst" => PaceMode::Hyst,
            "burst" => PaceMode::Burst,
            other => return Err(format!("unknown --pace-mode {other}").into()),
        };

        let lease = Lease::acquire(&args.card)?;
        let slot = lease.map_slot(&args.bar)?;
        let shared_slot = SharedSlot(&slot);

        let mut rpc = RpcClient::connect(&args.rpc)?;
        let no_poll = args.poll_mode == "none";
        let riova_arg = if no_poll { 0 } else { reply_iova };
        let prep = rpc.call(&format!(
            "PREPARE {} {} {} {} {} {} {} {:#x}",
            args.host_cores,
            args.card_cores,
            args.ring_cells,
            args.map_mode,
            args.poll_mode,
            args.work_mode,
            args.credit_step,
            riova_arg
        ))?;
        if !prep.starts_with("OK") {
            return Err(format!("PREPARE failed: {prep}").into());
        }

        let active_host_cpus = &HOST_CPUS[..args.host_cores];
        let pmu = HostPmu::open(active_host_cpus)?;
        let phase = Arc::new(AtomicUsize::new(0));
        let lease_err = Arc::new(AtomicBool::new(false));
        let has_neighbor = args.neighbor == "stream6_l3";
        let start_barrier = Arc::new(Barrier::new(args.host_cores + 1 + if has_neighbor { 7 } else { 0 }));
        let stream_bytes = Arc::new(std::sync::atomic::AtomicU64::new(0));
        let l3_out: Arc<Mutex<Vec<f64>>> = Arc::new(Mutex::new(Vec::new()));

        let lane_outs: Vec<Arc<Mutex<Option<LaneOut>>>> =
            (0..args.host_cores).map(|_| Arc::new(Mutex::new(None))).collect();

        let h_lanes = args.host_cores;
        let k_per_lane = args.card_cores / h_lanes;
        let ring_cells = args.ring_cells;
        let ring_mask = ring_cells - 1;
        let is_closed = args.work_mode == "closed";
        let lane_w = if is_closed { args.window / (h_lanes as u64) } else { args.window };
        let pace_credit = args.pace_credit;
        let pace_batch = args.pace_batch.max(1);
        let gap_ticks = ((args.pace_gap_ns as u128 * tsc_hz as u128 + 500_000_000) / 1_000_000_000) as u64;
        let db_sfence = args.db_sfence;
        let db_batch = args.db_batch.max(1);
        let db_light = args.db_light;
        let db_flush_ticks = ((args.db_flush_ns as u128 * tsc_hz as u128 + 500_000_000) / 1_000_000_000) as u64;

        let mut host_elapsed_ns: u64 = 0;
        let mut card_pmu_json = String::new();
        let mut host_pmu_json = String::new();
        let mut host_min_pct = 100.0_f64;

        thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            if has_neighbor {
                spawn_neighbor_threads(scope, &phase, &start_barrier, &stream_bytes, &l3_out, tsc_hz);
            }
            for lane in 0..h_lanes {
                let phase_w = phase.clone();
                let lease_err_w = lease_err.clone();
                let bar_w = start_barrier.clone();
                let out_slot = lane_outs[lane].clone();
                let host_cpu = active_host_cpus[lane];
                let lane_first_worker = lane * k_per_lane;
                let lane_bar_off = lane * ring_cells * CELL_STRIDE_BYTES;

                scope.spawn(move || {
                    pin_socket1_cpu(host_cpu);
                    let mut db = DbState {
                        mode: db_mode,
                        sfence_before: db_sfence,
                        batch: db_batch,
                        light: db_light,
                        flush_ticks: db_flush_ticks,
                        db_off: DOORBELL_OFFSET_BASE + lane * 128,
                        last_db_sent: 0,
                        last_db_tsc: 0,
                        rings_total: 0,
                        rings_win: 0,
                    };

                    let mut local_rtts: Vec<u32> = Vec::with_capacity(if is_closed { MAX_RTT_SAMPLES_PER_LANE } else { 0 });
                    let sample_mask: u64 = if lane_w <= 4 { 0 } else { 31 };

                    bar_w.wait();

                    if !is_closed {
                        // -------------------- ONE-WAY TX --------------------
                        let mut cached_next = vec![1u64; k_per_lane];
                        for lw in 0..k_per_lane {
                            cached_next[lw] = lw as u64 + 1;
                        }
                        let mut credit_limit: u64 = if no_poll { u64::MAX } else { lane_w };
                        let mut sent: u64 = 0;
                        let mut sent_start: u64 = 0;
                        let sent_end: u64;
                        let mut latched_start = false;
                        let mut measuring = false;
                        let mut burst_cnt: u64 = 0;
                        let mut next_burst_tsc: u64 = 0;
                        let mut spin_chk: u64 = 0;
                        let bar_lane_ptr = unsafe { shared_slot.as_ptr().add(lane_bar_off) };

                        let lane_target_mpps = args.target_mpps / (h_lanes as f64);
                        if lane_target_mpps > 0.0 {
                            // Open-loop Poisson inter-arrival paced sender
                            let mean_ticks = (tsc_hz as f64) / (lane_target_mpps * 1e6);
                            let max_lag_ticks = tsc_hz / 1000; // 1 ms cap
                            let mut rng: u64 = 0x243f_6a88_85a3_08d3 ^ ((lane as u64 + 1) * 0x9e37_79b9_7f4a_7c15);
                            let mut next_tsc = rdtsc();
                            loop {
                                if (spin_chk & 63) == 0 {
                                    let ph = phase_w.load(Ordering::Acquire);
                                    if ph == 1 && !latched_start {
                                        sent_start = sent;
                                        latched_start = true;
                                    } else if ph >= 2 {
                                        sent_end = sent;
                                        break;
                                    }
                                }
                                spin_chk += 1;

                                if !no_poll && sent >= credit_limit {
                                    let min_n = unsafe {
                                        read_card_min_next(reply_ptr.as_ptr(), lane_first_worker, k_per_lane, &mut cached_next)
                                    };
                                    let new_lim = min_n.saturating_sub(1) + lane_w;
                                    if new_lim > credit_limit {
                                        credit_limit = new_lim;
                                    } else {
                                        std::hint::spin_loop();
                                        continue;
                                    }
                                }

                                let now = rdtsc();
                                if now < next_tsc {
                                    std::hint::spin_loop();
                                    continue;
                                }
                                if now.saturating_sub(next_tsc) > max_lag_ticks {
                                    next_tsc = now;
                                }

                                if shared_slot.check().is_err() {
                                    lease_err_w.store(true, Ordering::Relaxed);
                                    return;
                                }
                                let s = sent + 1;
                                let off = (((s - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                let cell = make_fast_cell(s, 1);
                                unsafe { movdir64b(bar_lane_ptr.add(off), &cell) };
                                sent = s;

                                // Exponential inter-arrival via xorshift64*
                                rng ^= rng >> 12;
                                rng ^= rng << 25;
                                rng ^= rng >> 27;
                                let r = rng.wrapping_mul(0x2545_f491_4f6c_dd1d);
                                let u = ((r >> 11) as f64 + 0.5) * (1.0 / ((1u64 << 53) as f64));
                                let dt = (-u.ln() * mean_ticks) as u64;
                                next_tsc = next_tsc.wrapping_add(dt.max(1));
                            }
                        } else if pace_mode == PaceMode::None && db.mode == DbMode::None {
                            // Unpaced, zero-doorbell, zero-SFENCE fast path: hits full single-core MOVDIR64B ceiling
                            loop {
                                let ph = phase_w.load(Ordering::Acquire);
                                if ph == 1 && !latched_start {
                                    sent_start = sent;
                                    latched_start = true;
                                } else if ph >= 2 {
                                    sent_end = sent;
                                    break;
                                }

                                if !no_poll && sent + 64 > credit_limit {
                                    let min_n = unsafe {
                                        read_card_min_next(reply_ptr.as_ptr(), lane_first_worker, k_per_lane, &mut cached_next)
                                    };
                                    let new_lim = min_n.saturating_sub(1) + lane_w;
                                    if new_lim > credit_limit {
                                        credit_limit = new_lim;
                                    }
                                    if sent >= credit_limit {
                                        std::hint::spin_loop();
                                        continue;
                                    }
                                }

                                let chunk = if no_poll { 64 } else { (credit_limit - sent).min(64) };
                                for _ in 0..chunk {
                                    if shared_slot.check().is_err() {
                                        lease_err_w.store(true, Ordering::Relaxed);
                                        return;
                                    }
                                    let s = sent + 1;
                                    let off = (((s - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                    let cell = make_fast_cell(s, 1);
                                    unsafe { movdir64b(bar_lane_ptr.add(off), &cell) };
                                    sent = s;
                                }
                            }
                        } else {
                            loop {
                                if (spin_chk & 63) == 0 {
                                    let ph = phase_w.load(Ordering::Acquire);
                                    if ph == 1 && !latched_start {
                                        sent_start = sent;
                                        latched_start = true;
                                        measuring = true;
                                    } else if ph >= 2 {
                                        sent_end = sent;
                                        break;
                                    }
                                }
                                spin_chk += 1;

                                if !no_poll && sent >= credit_limit {
                                    if db.mode != DbMode::None && sent > db.last_db_sent {
                                        if db.ring_now(shared_slot, sent, 0, measuring).is_err() {
                                            lease_err_w.store(true, Ordering::Relaxed);
                                            return;
                                        }
                                    }
                                    let min_n = unsafe {
                                        read_card_min_next(reply_ptr.as_ptr(), lane_first_worker, k_per_lane, &mut cached_next)
                                    };
                                    let new_lim = min_n.saturating_sub(1) + lane_w;
                                    if new_lim > credit_limit {
                                        credit_limit = new_lim;
                                    } else {
                                        std::hint::spin_loop();
                                        continue;
                                    }
                                }

                                if pace_mode == PaceMode::Burst && gap_ticks > 0 && next_burst_tsc != 0 {
                                    if rdtsc() < next_burst_tsc {
                                        std::hint::spin_loop();
                                        continue;
                                    }
                                    next_burst_tsc = 0;
                                }

                                if shared_slot.check().is_err() {
                                    lease_err_w.store(true, Ordering::Relaxed);
                                    return;
                                }
                                let s = sent + 1;
                                let off = (((s - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                let cell = make_fast_cell(s, 1);
                                unsafe { movdir64b(bar_lane_ptr.add(off), &cell) };
                                sent = s;

                                if db.mode != DbMode::None && (sent - db.last_db_sent) >= db.batch {
                                    if db.ring_now(shared_slot, sent, 0, measuring).is_err() {
                                        lease_err_w.store(true, Ordering::Relaxed);
                                        return;
                                    }
                                }

                                if pace_mode == PaceMode::Burst && gap_ticks > 0 {
                                    burst_cnt += 1;
                                    if burst_cnt >= pace_batch {
                                        if db.mode != DbMode::None && sent > db.last_db_sent {
                                            if db.ring_now(shared_slot, sent, 0, measuring).is_err() {
                                                lease_err_w.store(true, Ordering::Relaxed);
                                                return;
                                            }
                                        }
                                        burst_cnt = 0;
                                        next_burst_tsc = rdtsc() + gap_ticks;
                                    }
                                }
                            }
                        }
                        // Final doorbell flush + wait for card to consume all `sent` cells cleanly
                        let mut final_done = sent;
                        let mut drain_err: u64 = 0;
                        if !no_poll {
                            if db.mode != DbMode::None && sent > db.last_db_sent {
                                let _ = db.ring_now(shared_slot, sent, 0, false);
                            }
                            let deadline = Instant::now() + Duration::from_secs(3);
                            let mut last_min = 0u64;
                            while Instant::now() < deadline {
                                let min_n = unsafe {
                                    read_card_min_next(reply_ptr.as_ptr(), lane_first_worker, k_per_lane, &mut cached_next)
                                };
                                last_min = min_n;
                                if min_n > sent {
                                    break;
                                }
                                std::hint::spin_loop();
                            }
                            if last_min <= sent {
                                final_done = last_min.saturating_sub(1);
                                drain_err = sent.saturating_sub(final_done);
                            }
                        }

                        let sent_win = sent_end.saturating_sub(sent_start);
                        *out_slot.lock().unwrap() = Some(LaneOut {
                            done_win: sent_win,
                            sent_win,
                            sent_tot: sent,
                            done_tot: final_done,
                            torn_retries: 0,
                            bad_or_torn: drain_err,
                            db_rings_win: db.rings_win,
                            db_rings_tot: db.rings_total,
                            rtts: Vec::new(),
                        });
                        return;
                    }

                    // -------------------- CLOSED-LOOP PING-PONG --------------------
                    let mut submit_tsc = [1u64; 1024];
                    let mut submitted: u64 = 0;
                    let mut sent: u64 = 0;
                    let mut done: u64 = 0;
                    let mut done_start: u64 = 0;
                    let done_end: u64;
                    let mut sent_start: u64 = 0;
                    let sent_end: u64;
                    let mut latched_start = false;
                    let mut measuring = false;
                    let mut iter: u64 = 0;
                    let mut local_torn_retries: u64 = 0;
                    let mut local_bad_or_torn: u64 = 0;
                    let mut hyst_gated = false;
                    let mut burst_cnt: u64 = 0;
                    let mut next_burst_tsc: u64 = 0;

                    loop {
                        if (iter & 31) == 0 || lane_w == 1 {
                            let ph = phase_w.load(Ordering::Acquire);
                            if ph == 1 && !latched_start {
                                done_start = done;
                                sent_start = sent;
                                latched_start = true;
                                measuring = true;
                            } else if ph >= 2 {
                                done_end = done;
                                sent_end = sent;
                                break;
                            }
                        }

                        // 1. Caller logical submissions up to window `lane_w`
                        while submitted - done < lane_w {
                            let s = submitted + 1;
                            let t_sub = if measuring && ((s & sample_mask) == 0) {
                                let t = rdtsc();
                                if t == 0 { 1 } else { t }
                            } else {
                                1
                            };
                            submit_tsc[(s as usize) & 1023] = t_sub;
                            submitted = s;
                        }

                        // 2. Wire transmission (unshaped OR pace_hyst OR pace_burst)
                        match pace_mode {
                            PaceMode::None => {
                                while sent < submitted {
                                    if shared_slot.check().is_err() {
                                        lease_err_w.store(true, Ordering::Relaxed);
                                        return;
                                    }
                                    let s = sent + 1;
                                    let off = lane_bar_off + (((s - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                    let cell = Cell::new(s, s, submit_tsc[(s as usize) & 1023]);
                                    unsafe { movdir64b(shared_slot.as_ptr().add(off), &cell) };
                                    sent = s;
                                    if db.mode != DbMode::None && (sent - db.last_db_sent) >= db.batch {
                                        let now = rdtsc();
                                        if db.ring_now(shared_slot, sent, now, measuring).is_err() {
                                            lease_err_w.store(true, Ordering::Relaxed);
                                            return;
                                        }
                                    }
                                }
                            }
                            PaceMode::Hyst => {
                                let on_wire = sent - done;
                                if on_wire >= pace_credit {
                                    hyst_gated = true;
                                } else if hyst_gated && on_wire + pace_batch <= pace_credit {
                                    hyst_gated = false;
                                }
                                while sent < submitted && !hyst_gated && (sent - done) < pace_credit {
                                    if shared_slot.check().is_err() {
                                        lease_err_w.store(true, Ordering::Relaxed);
                                        return;
                                    }
                                    let s = sent + 1;
                                    let off = lane_bar_off + (((s - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                    let cell = Cell::new(s, s, submit_tsc[(s as usize) & 1023]);
                                    unsafe { movdir64b(shared_slot.as_ptr().add(off), &cell) };
                                    sent = s;
                                    if (sent - done) >= pace_credit {
                                        hyst_gated = true;
                                    }
                                }
                            }
                            PaceMode::Burst => {
                                if lane_w == 1 && sent == done {
                                    burst_cnt = 0;
                                    next_burst_tsc = 0;
                                }
                                let can_send = if gap_ticks == 0 || next_burst_tsc == 0 {
                                    true
                                } else if rdtsc() >= next_burst_tsc {
                                    next_burst_tsc = 0;
                                    true
                                } else {
                                    false
                                };
                                if can_send {
                                    while sent < submitted {
                                        if shared_slot.check().is_err() {
                                            lease_err_w.store(true, Ordering::Relaxed);
                                            return;
                                        }
                                        let s = sent + 1;
                                        let off = lane_bar_off + (((s - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                        let cell = Cell::new(s, s, submit_tsc[(s as usize) & 1023]);
                                        unsafe { movdir64b(shared_slot.as_ptr().add(off), &cell) };
                                        sent = s;
                                        if gap_ticks > 0 {
                                            burst_cnt += 1;
                                            if burst_cnt >= pace_batch {
                                                burst_cnt = 0;
                                                next_burst_tsc = rdtsc() + gap_ticks;
                                                break;
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // 3. Adaptive doorbell flush (MRV-177 Arm B2)
                        if db.mode == DbMode::Adaptive && sent > db.last_db_sent {
                            let inflight = sent - done;
                            let unrung = sent - db.last_db_sent;
                            let now = rdtsc();
                            let should_ring = unrung >= db.batch
                                || inflight <= db.light
                                || db.last_db_sent <= done
                                || (db.flush_ticks > 0 && now.saturating_sub(db.last_db_tsc) >= db.flush_ticks);
                            if should_ring {
                                if db.ring_now(shared_slot, sent, now, measuring).is_err() {
                                    lease_err_w.store(true, Ordering::Relaxed);
                                    return;
                                }
                            }
                        } else if db.mode == DbMode::Batch && sent > db.last_db_sent && db.last_db_sent <= done {
                            let now = rdtsc();
                            if db.ring_now(shared_slot, sent, now, measuring).is_err() {
                                lease_err_w.store(true, Ordering::Relaxed);
                                return;
                            }
                        }

                        // 4. Poll replies (up to 32 per pass, matching MRV-235 & MRV-123 MAX_DRAIN=32)
                        let mut reaped = 0;
                        while done < sent && reaped < 32 {
                            let expect = done + 1;
                            match unsafe { poll_host_reply(reply_ptr.as_ptr(), lane_first_worker, k_per_lane, expect) } {
                                ReplyPoll::Ok(echo_tsc) => {
                                    if measuring
                                        && ((expect & sample_mask) == 0)
                                        && echo_tsc > 1
                                        && local_rtts.len() < MAX_RTT_SAMPLES_PER_LANE
                                    {
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

                    // Drain remaining submitted & in-flight requests cleanly
                    let deadline = Instant::now() + Duration::from_secs(3);
                    while done < submitted && Instant::now() < deadline {
                        while sent < submitted && (sent - done) < lane_w.min(64) {
                            if shared_slot.check().is_err() {
                                break;
                            }
                            let s = sent + 1;
                            let off = lane_bar_off + (((s - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                            let cell = Cell::new(s, s, 1);
                            unsafe { movdir64b(shared_slot.as_ptr().add(off), &cell) };
                            sent = s;
                        }
                        if db.mode != DbMode::None && sent > db.last_db_sent {
                            let _ = db.ring_now(shared_slot, sent, 0, false);
                        }
                        let mut reaped = 0;
                        while done < sent && reaped < 32 {
                            let expect = done + 1;
                            match unsafe { poll_host_reply(reply_ptr.as_ptr(), lane_first_worker, k_per_lane, expect) } {
                                ReplyPoll::Ok(_) => {
                                    done = expect;
                                    reaped += 1;
                                }
                                ReplyPoll::TornOrBad => {
                                    local_torn_retries += 1;
                                    if Instant::now() + Duration::from_millis(10) >= deadline {
                                        local_bad_or_torn += 1;
                                        break;
                                    }
                                    break;
                                }
                                ReplyPoll::Empty => break,
                            }
                        }
                    }
                    if done < submitted {
                        local_bad_or_torn += submitted - done;
                    }

                    *out_slot.lock().unwrap() = Some(LaneOut {
                        done_win: done_end.saturating_sub(done_start),
                        sent_win: sent_end.saturating_sub(sent_start),
                        sent_tot: sent,
                        done_tot: done,
                        torn_retries: local_torn_retries,
                        bad_or_torn: local_bad_or_torn,
                        db_rings_win: db.rings_win,
                        db_rings_tot: db.rings_total,
                        rtts: local_rtts,
                    });
                });
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
            return Err("BAR4 slot 15 lease revoked mid-run".into());
        }

        let card_stats_json = rpc.call("STATS")?;
        let _ = rpc.call("PAUSE");
        thread::sleep(Duration::from_millis(10));

        let mut done_win_sum: u64 = 0;
        let mut sent_win_sum: u64 = 0;
        let mut sent_tot_sum: u64 = 0;
        let mut done_tot_sum: u64 = 0;
        let mut torn_retries_sum: u64 = 0;
        let mut bad_or_torn_sum: u64 = 0;
        let mut db_rings_win_sum: u64 = 0;
        let mut db_rings_tot_sum: u64 = 0;
        let mut all_rtts: Vec<u32> = Vec::new();
        let mut lane_mops_parts: Vec<String> = Vec::new();

        let secs = host_elapsed_ns as f64 / 1e9;
        for lane in 0..h_lanes {
            let lo = lane_outs[lane].lock().unwrap().take().unwrap();
            done_win_sum += lo.done_win;
            sent_win_sum += lo.sent_win;
            sent_tot_sum += lo.sent_tot;
            done_tot_sum += lo.done_tot;
            torn_retries_sum += lo.torn_retries;
            bad_or_torn_sum += lo.bad_or_torn;
            db_rings_win_sum += lo.db_rings_win;
            db_rings_tot_sum += lo.db_rings_tot;
            let lm = (lo.done_win as f64 / secs) / 1e6;
            lane_mops_parts.push(format!("{lm:.4}"));
            all_rtts.extend_from_slice(&lo.rtts);
        }

        let lost = sent_tot_sum.saturating_sub(done_tot_sum);
        let mops = (done_win_sum as f64 / secs) / 1e6;
        let gb_s = (done_win_sum as f64 * 64.0 / secs) / 1e9;
        let inv_rate_ns = if mops > 0.0 { 1000.0 / mops } else { 0.0 };
        let pkts_per_db = if db_rings_win_sum > 0 {
            sent_win_sum as f64 / db_rings_win_sum as f64
        } else {
            0.0
        };

        let n_rtt = all_rtts.len();
        let (rtt_mean_ns, rtt_p50_ns, rtt_p90_ns, rtt_p99_ns, rtt_p999_ns) = if n_rtt > 0 {
            let sum: u64 = all_rtts.iter().map(|&x| x as u64).sum();
            all_rtts.sort_unstable();
            let tick_to_ns = 1e9_f64 / (tsc_hz as f64);
            let pct = |num: usize, den: usize| -> f64 {
                let idx = ((n_rtt - 1) * num) / den;
                all_rtts[idx] as f64 * tick_to_ns
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

        let nb = collect_neighbor(&stream_bytes, &l3_out, host_elapsed_ns);
        println!(
            "MRV252_RUN {{\"exp\":\"{}\",\"cond\":\"{}\",\"rep\":{},\"host_cores\":{h_lanes},\"card_cores\":{},\"work_mode\":\"{}\",\"window\":{},\"window_per_lane\":{lane_w},\"batch\":1,\"ring_cells\":{ring_cells},\"cell_stride\":{CELL_STRIDE_BYTES},\"map_mode\":\"{}\",\"poll_mode\":\"{}\",\"target_mpps\":{:.2},\"neighbor\":\"{}\",\"stream_gbps\":{:.3},\"l3_p50_ns\":{:.2},\"l3_p99_ns\":{:.2},\"l3_samples\":{},\"credit_step\":{},\"pace_mode\":\"{}\",\"pace_credit\":{},\"pace_batch\":{},\"pace_gap_ns\":{},\"doorbell\":\"{}\",\"db_sfence\":{},\"db_batch\":{},\"db_light\":{},\"db_flush_ns\":{},\"db_rings_win\":{db_rings_win_sum},\"db_rings_tot\":{db_rings_tot_sum},\"pkts_per_db\":{pkts_per_db:.2},\"elapsed_ns\":{host_elapsed_ns},\"done_win\":{done_win_sum},\"sent_win\":{sent_win_sum},\"sent_tot\":{sent_tot_sum},\"done_tot\":{done_tot_sum},\"mops\":{mops:.4},\"lane_mops\":[{}],\"gb_s\":{gb_s:.4},\"inv_rate_ns\":{inv_rate_ns:.2},\"rtt_samples\":{n_rtt},\"rtt_mean_ns\":{rtt_mean_ns:.2},\"rtt_p50_ns\":{rtt_p50_ns:.2},\"rtt_p90_ns\":{rtt_p90_ns:.2},\"rtt_p99_ns\":{rtt_p99_ns:.2},\"rtt_p999_ns\":{rtt_p999_ns:.2},\"lost\":{lost},\"bad_or_torn\":{bad_or_torn_sum},\"torn_retries\":{torn_retries_sum},\"host_min_pct\":{host_min_pct:.2},\"host_pmu\":{host_pmu_json},\"card_pmu\":{card_pmu_json},\"card_stats\":{card_stats_json}}}",
            args.exp,
            args.cond,
            args.rep,
            args.card_cores,
            args.work_mode,
            args.window,
            args.map_mode,
            args.poll_mode,
            args.target_mpps,
            args.neighbor,
            nb.stream_gbps,
            nb.l3_p50_ns,
            nb.l3_p99_ns,
            nb.l3_samples,
            args.credit_step,
            args.pace_mode,
            args.pace_credit,
            args.pace_batch,
            args.pace_gap_ns,
            args.doorbell,
            args.db_sfence,
            args.db_batch,
            args.db_light,
            args.db_flush_ns,
            lane_mops_parts.join(","),
        );
        Ok(())
    }
}
