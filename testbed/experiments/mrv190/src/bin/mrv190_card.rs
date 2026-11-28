//! Card-side real-time 64 B cache-line reordering detector, convergence verifier,
//! BAR4 slot 15 MRV-26 lease server, and 100% non-multiplexed CN106 TAD/DDR5 PMU
//! server for MRV-190.
//!
//! Layout in BAR4 slot 15 (4 MiB physically contiguous hugepage):
//! - `0x0000 .. 0x1000`: payload area (up to 4 KiB = 64 cache lines of 64 B)
//! - `0x1000 .. 0x1040`: independent 64 B doorbell line (`DB_OFFSET`)
//! - `0x2000 .. 0x2040`: card->host 64 B ack line (`ACK_OFFSET`)
//!
//! For each generation `g`, the card core (pinned to EAL main lcore, e.g. cpu3):
//! 1. Spins with ARM64 `ldar` strictly on the watched 8 B tail word
//!    (`payload[N-1].w7` in `tail` mode, or `doorbell.w7` in `doorbell` mode) until `== g`.
//! 2. Immediately executes `dmb ishld`.
//! 3. Reads `w0, w1` of the watched line (detecting intra-64 B tearing) and takes a tight,
//!    branch-free snapshot (`w0, w1, w7`) of all preceding `check_lines` (`0..N-1` in `tail`
//!    mode, or `0..N` in `doorbell` mode) in `forward` or `reverse` order.
//! 4. Classifies transient reordering: missing line count, per-line miss histogram,
//!    farthest reverse span, intra-64 B tear count, and unexpected-value count.
//! 5. Spins until all missing lines converge to `g` (measuring convergence duration via
//!    `cntvct_el0` and verifying zero persistent errors across the full block).
//! 6. Publishes `g` to `ACK_OFFSET` with `stlr`.

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv190_card runs on the aarch64 card");
    std::process::exit(2);
}

#[cfg(target_arch = "aarch64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "aarch64")]
mod imp {
    use std::arch::asm;
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::size_of;
    use std::net::{TcpListener, TcpStream};
    use std::os::fd::RawFd;
    use std::ptr;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::Parser;
    use dpdk::{Eal, Memzone};
    use testbed::inline::{NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{Bar4Window, restore_defaults};

    const MAX_LINES: usize = 64; // 4096 B / 64 B
    const DB_OFFSET: usize = 4096;
    const ACK_OFFSET: usize = 8192;
    const CLEAR_BYTES: usize = 12288; // 0..0x3000
    const LINE_MAGIC: u64 = 0xA55A_1900_0000_0000;
    const DB_MAGIC: u64 = 0xDB19_0000_0000_0000;

    #[inline(always)]
    fn line_sig(gen_val: u64, line_idx: usize) -> u64 {
        gen_val ^ LINE_MAGIC ^ ((line_idx as u64) << 40)
    }

    #[inline(always)]
    fn db_sig(gen_val: u64) -> u64 {
        gen_val ^ DB_MAGIC
    }

    #[inline(always)]
    fn dsb_sy() {
        unsafe { asm!("dsb sy", options(nostack, preserves_flags)) };
    }

    #[inline(always)]
    fn dmb_ishld() {
        unsafe { asm!("dmb ishld", options(nostack, preserves_flags)) };
    }

    #[inline(always)]
    unsafe fn ldar_u64(ptr: *const u64) -> u64 {
        let val: u64;
        unsafe {
            asm!(
                "ldar {v}, [{p}]",
                v = out(reg) val,
                p = in(reg) ptr,
                options(nostack, preserves_flags, readonly)
            );
        }
        val
    }

    #[inline(always)]
    unsafe fn stlr_u64(ptr: *mut u64, val: u64) {
        unsafe {
            asm!(
                "stlr {v}, [{p}]",
                v = in(reg) val,
                p = in(reg) ptr,
                options(nostack, preserves_flags)
            );
        }
    }

    #[inline(always)]
    fn read_cntvct() -> u64 {
        let v: u64;
        unsafe {
            asm!("isb", "mrs {v}, cntvct_el0", v = out(reg) v, options(nostack, preserves_flags));
        }
        v
    }

    #[inline(always)]
    fn read_cntfrq() -> u64 {
        let v: u64;
        unsafe {
            asm!("mrs {v}, cntfrq_el0", v = out(reg) v, options(nostack, preserves_flags, nomem));
        }
        v
    }

    // ---- 100% non-multiplexed CN106 TAD (8 counters) + 6-ch DDR5 PMU ----
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

    struct PerfCounter {
        fd: RawFd,
    }

    #[derive(Clone, Copy, Default)]
    struct Sample {
        val: u64,
        en: u64,
        run: u64,
    }

    impl Sample {
        fn delta(self, prev: Self) -> Self {
            Self {
                val: self.val.saturating_sub(prev.val),
                en: self.en.saturating_sub(prev.en),
                run: self.run.saturating_sub(prev.run),
            }
        }
        fn pct(self) -> f64 {
            if self.en == 0 { 100.0 } else { self.run as f64 * 100.0 / self.en as f64 }
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
            let c = Self { fd: fd as RawFd };
            if unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) } != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> Sample {
            let mut buf = [0u64; 3];
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast(), size_of::<[u64; 3]>()) };
            assert_eq!(n, size_of::<[u64; 3]>() as isize);
            Sample { val: buf[0], en: buf[1], run: buf[2] }
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

    struct CardPmu {
        tad_req_in: PerfCounter,
        tad_dat_in: PerfCounter,
        tad_alloc: PerfCounter,
        tad_hit: PerfCounter,
        tad_dss_wr: PerfCounter,
        tad_req_occ: PerfCounter,
        tad_ifb_occ: PerfCounter,
        tad_retry: PerfCounter,
        ddr_writes: Vec<PerfCounter>,
        ddr_reads: Vec<PerfCounter>,
    }

    struct CardSnap {
        tad_req_in: Sample,
        tad_dat_in: Sample,
        tad_alloc: Sample,
        tad_hit: Sample,
        tad_dss_wr: Sample,
        tad_req_occ: Sample,
        tad_ifb_occ: Sample,
        tad_retry: Sample,
        ddr_writes: Vec<Sample>,
        ddr_reads: Vec<Sample>,
        wall: Instant,
    }

    fn read_sysfs_u32(path: &str) -> io::Result<u32> {
        fs::read_to_string(path)?.trim().parse().map_err(io::Error::other)
    }

    impl CardPmu {
        fn open() -> io::Result<Self> {
            let tad_type = read_sysfs_u32("/sys/bus/event_source/devices/tad/type")?;
            let mut ddr_writes = Vec::new();
            let mut ddr_reads = Vec::new();
            let mut entries: Vec<String> = fs::read_dir("/sys/bus/event_source/devices")?
                .filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|n| n.starts_with("mrvl_ddr_pmu_"))
                .collect();
            entries.sort();
            for name in entries {
                let t = read_sysfs_u32(&format!("/sys/bus/event_source/devices/{name}/type"))?;
                ddr_writes.push(PerfCounter::open(t, 0x64, 0)?);
                ddr_reads.push(PerfCounter::open(t, 0x65, 0)?);
            }
            Ok(Self {
                tad_req_in: PerfCounter::open(tad_type, 0x01, 0)?,
                tad_dat_in: PerfCounter::open(tad_type, 0x09, 0)?,
                tad_alloc: PerfCounter::open(tad_type, 0x1c, 0)?,
                tad_hit: PerfCounter::open(tad_type, 0x1f, 0)?,
                tad_dss_wr: PerfCounter::open(tad_type, 0x0d, 0)?,
                tad_req_occ: PerfCounter::open(tad_type, 0x24, 0)?,
                tad_ifb_occ: PerfCounter::open(tad_type, 0x23, 0)?,
                tad_retry: PerfCounter::open(tad_type, 0x10, 0)?,
                ddr_writes,
                ddr_reads,
            })
        }

        fn snap(&self) -> CardSnap {
            CardSnap {
                tad_req_in: self.tad_req_in.read(),
                tad_dat_in: self.tad_dat_in.read(),
                tad_alloc: self.tad_alloc.read(),
                tad_hit: self.tad_hit.read(),
                tad_dss_wr: self.tad_dss_wr.read(),
                tad_req_occ: self.tad_req_occ.read(),
                tad_ifb_occ: self.tad_ifb_occ.read(),
                tad_retry: self.tad_retry.read(),
                ddr_writes: self.ddr_writes.iter().map(|c| c.read()).collect(),
                ddr_reads: self.ddr_reads.iter().map(|c| c.read()).collect(),
                wall: Instant::now(),
            }
        }

        fn delta_json(s0: &CardSnap, s1: &CardSnap) -> String {
            let mut min_pct = 100.0f64;
            let mut d = |a: Sample, b: Sample| -> u64 {
                let x = b.delta(a);
                min_pct = min_pct.min(x.pct());
                x.val
            };
            let tad_req_in = d(s0.tad_req_in, s1.tad_req_in);
            let tad_dat_in = d(s0.tad_dat_in, s1.tad_dat_in);
            let tad_alloc = d(s0.tad_alloc, s1.tad_alloc);
            let tad_hit = d(s0.tad_hit, s1.tad_hit);
            let tad_dss_wr = d(s0.tad_dss_wr, s1.tad_dss_wr);
            let tad_req_occ = d(s0.tad_req_occ, s1.tad_req_occ);
            let tad_ifb_occ = d(s0.tad_ifb_occ, s1.tad_ifb_occ);
            let tad_retry = d(s0.tad_retry, s1.tad_retry);
            let mut ddr_wr = 0u64;
            let mut ddr_rd = 0u64;
            for (a, b) in s0.ddr_writes.iter().zip(s1.ddr_writes.iter()) {
                ddr_wr = ddr_wr.saturating_add(d(*a, *b));
            }
            for (a, b) in s0.ddr_reads.iter().zip(s1.ddr_reads.iter()) {
                ddr_rd = ddr_rd.saturating_add(d(*a, *b));
            }
            let wall_ns = s1.wall.saturating_duration_since(s0.wall).as_nanos() as u64;
            format!(
                "{{\"tad_req_in\":{tad_req_in},\"tad_dat_in\":{tad_dat_in},\"tad_alloc\":{tad_alloc},\"tad_hit\":{tad_hit},\"tad_dss_wr\":{tad_dss_wr},\"tad_req_occ\":{tad_req_occ},\"tad_ifb_occ\":{tad_ifb_occ},\"tad_retry\":{tad_retry},\"ddr_writes\":{ddr_wr},\"ddr_reads\":{ddr_rd},\"ddr_channels\":{},\"wall_ns\":{wall_ns},\"min_pct\":{min_pct:.2}}}",
                s0.ddr_writes.len()
            )
        }
    }

    #[derive(Parser)]
    struct Args {
        /// MRV-26 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        /// Sync / watch / PMU TCP address.
        #[arg(long, default_value = "10.10.0.2:7527")]
        sync: String,
        /// Restore firmware BAR4/PEM0 defaults and exit.
        #[arg(long)]
        restore: bool,
    }

    fn percentile_f64(sorted: &[f64], q: f64) -> f64 {
        if sorted.is_empty() {
            return 0.0;
        }
        let idx = ((sorted.len() - 1) as f64 * q).round() as usize;
        sorted[idx.min(sorted.len() - 1)]
    }

    fn hist_percentile(hist: &[u64], total: u64, q: f64) -> usize {
        if total == 0 {
            return 0;
        }
        let target = ((total as f64) * q).ceil().max(1.0) as u64;
        let mut cum = 0u64;
        for (i, &c) in hist.iter().enumerate() {
            cum += c;
            if cum >= target {
                return i;
            }
        }
        hist.len().saturating_sub(1)
    }

    fn run_watch(
        base: *mut u8,
        size: usize,
        doorbell_mode: bool,
        reverse_scan: bool,
        rounds: u64,
        base_gen: u64,
        pmu: &CardPmu,
        stream: &mut TcpStream,
        stop: &AtomicBool,
    ) -> Result<(), Box<dyn Error>> {
        let n_lines = size / 64;
        if size == 0 || !size.is_multiple_of(64) || n_lines > MAX_LINES || (!doorbell_mode && n_lines < 2) {
            writeln!(stream, "ERR bad_size")?;
            stream.flush()?;
            return Ok(());
        }

        let check_lines = if doorbell_mode { n_lines } else { n_lines - 1 };
        let watch_line_off = if doorbell_mode { DB_OFFSET } else { (n_lines - 1) * 64 };
        let watch_line_base = unsafe { base.add(watch_line_off).cast::<u64>() };
        let watch_w7_ptr = unsafe { watch_line_base.add(7) };
        let ack_ptr = unsafe { base.add(ACK_OFFSET).cast::<u64>() };

        // Zero payload + doorbell + ack area before telling host we are ready.
        unsafe { ptr::write_bytes(base, 0, CLEAR_BYTES) };
        dsb_sy();

        let cntfrq = read_cntfrq();
        let card_cpu = unsafe { libc::sched_getcpu() };

        // Notify host and IMMEDIATELY enter tight watch loop.
        writeln!(stream, "READY")?;
        stream.flush()?;

        let p0 = pmu.snap();
        let t_start = Instant::now();

        let mut rounds_observed = 0u64;
        let mut reordered_ops = 0u64;
        let mut persistent_errors = 0u64;
        let mut timeout_ops = 0u64;
        let mut unexpected_vals = 0u64;
        let mut watch_endpoint_torn = 0u64;
        let mut watch_sig_only_torn = 0u64;
        let mut body_endpoint_torn = 0u64;
        let mut body_sig_only_torn = 0u64;
        let mut per_line_miss = [0u64; MAX_LINES];
        let mut missing_count_hist = [0u64; MAX_LINES + 1];
        let mut span_hist = [0u64; MAX_LINES + 1];
        let mut first_missing_hist = [0u64; MAX_LINES];

        let mut missing_sum = 0u64;
        let mut missing_min = usize::MAX;
        let mut missing_max = 0usize;
        let mut span_sum = 0u64;
        let mut span_max = 0usize;

        let mut conv_ns_samples: Vec<f64> = Vec::with_capacity(rounds.min(65536) as usize);

        let mut snap_w0 = [0u64; MAX_LINES];
        let mut snap_w1 = [0u64; MAX_LINES];
        let mut snap_w7 = [0u64; MAX_LINES];

        for r in 0..rounds {
            if stop.load(Ordering::Relaxed) {
                break;
            }
            let g = base_gen + r;
            let prev_g = if r == 0 { 0 } else { g - 1 };
            let exp_watch_sig = if doorbell_mode { db_sig(g) } else { line_sig(g, n_lines - 1) };

            // 1. Tight LDAR spin on the tail/doorbell word 7 (+56 B).
            let mut spins = 0u32;
            let mut op_deadline: Option<Instant> = None;
            let mut timed_out = false;
            loop {
                let v = unsafe { ldar_u64(watch_w7_ptr) };
                if v == g {
                    break;
                }
                spins = spins.wrapping_add(1);
                if (spins & 0xFFFF) == 0 {
                    if stop.load(Ordering::Relaxed) {
                        timed_out = true;
                        break;
                    }
                    let dl = *op_deadline.get_or_insert_with(|| Instant::now() + Duration::from_secs(2));
                    if Instant::now() >= dl {
                        timed_out = true;
                        break;
                    }
                }
            }
            if timed_out {
                timeout_ops += 1;
                break;
            }

            // 2. Explicit ARM64 dmb ishld immediately after observing watch word == g.
            dmb_ishld();

            // 3. Check watch line head/sig (detecting intra-64 B tearing) + tight branch-free snapshot of preceding lines.
            let ww0 = unsafe { ptr::read_volatile(watch_line_base) };
            let ww1 = unsafe { ptr::read_volatile(watch_line_base.add(1)) };

            if !reverse_scan {
                for i in 0..check_lines {
                    let lp = unsafe { base.add(i * 64).cast::<u64>() };
                    unsafe {
                        snap_w0[i] = ptr::read_volatile(lp);
                        snap_w1[i] = ptr::read_volatile(lp.add(1));
                        snap_w7[i] = ptr::read_volatile(lp.add(7));
                    }
                }
            } else {
                for i in (0..check_lines).rev() {
                    let lp = unsafe { base.add(i * 64).cast::<u64>() };
                    unsafe {
                        snap_w0[i] = ptr::read_volatile(lp);
                        snap_w1[i] = ptr::read_volatile(lp.add(1));
                        snap_w7[i] = ptr::read_volatile(lp.add(7));
                    }
                }
            }

            // 4. Analyze snapshot strictly offline from the load burst.
            //    Strictly separate endpoint misses (`w0 != g || w7 != g`) from signature-only tears
            //    (`w0 == g && w7 == g && w1 != sig`) so `reordered_ops` matches the exact head/tail criterion.
            let watch_ep_bad = ww0 != g;
            let watch_sig_only = !watch_ep_bad && ww1 != exp_watch_sig;
            if watch_ep_bad {
                watch_endpoint_torn += 1;
            }
            if watch_sig_only {
                watch_sig_only_torn += 1;
            }
            let mut watch_bad = watch_ep_bad || watch_sig_only;

            let mut miss_mask = 0u64;
            let mut sig_mask = 0u64;
            for i in 0..check_lines {
                let w0 = snap_w0[i];
                let w1 = snap_w1[i];
                let w7 = snap_w7[i];
                let sig = line_sig(g, i);
                let ep_miss = w0 != g || w7 != g;
                let sig_only = !ep_miss && w1 != sig;
                if ep_miss {
                    miss_mask |= 1u64 << i;
                    per_line_miss[i] += 1;
                    if (w0 == g) != (w7 == g) {
                        body_endpoint_torn += 1;
                    }
                    if w0 != g && w0 != prev_g {
                        unexpected_vals += 1;
                    }
                    if w7 != g && w7 != prev_g {
                        unexpected_vals += 1;
                    }
                } else if sig_only {
                    sig_mask |= 1u64 << i;
                    body_sig_only_torn += 1;
                }
            }

            // 5. If any line was missing or torn, record reordering metrics and spin until full convergence.
            let conv_mask = miss_mask | sig_mask;
            if conv_mask != 0 || watch_bad {
                let t_conv0 = read_cntvct();
                if miss_mask != 0 {
                    reordered_ops += 1;
                    let cnt = miss_mask.count_ones() as usize;
                    let first_idx = miss_mask.trailing_zeros() as usize;
                    let span_lines = if doorbell_mode {
                        n_lines - first_idx
                    } else {
                        (n_lines - 1) - first_idx
                    };
                    missing_count_hist[cnt] += 1;
                    span_hist[span_lines] += 1;
                    first_missing_hist[first_idx] += 1;
                    missing_sum += cnt as u64;
                    if cnt < missing_min {
                        missing_min = cnt;
                    }
                    if cnt > missing_max {
                        missing_max = cnt;
                    }
                    span_sum += span_lines as u64;
                    if span_lines > span_max {
                        span_max = span_lines;
                    }
                }

                let conv_dl = Instant::now() + Duration::from_millis(500);
                let mut rem = conv_mask;
                let mut conv_failed = false;
                let mut cspins = 0u32;
                while rem != 0 || watch_bad {
                    if watch_bad {
                        let cw0 = unsafe { ptr::read_volatile(watch_line_base) };
                        let cw1 = unsafe { ptr::read_volatile(watch_line_base.add(1)) };
                        let cw7 = unsafe { ptr::read_volatile(watch_line_base.add(7)) };
                        if cw0 == g && cw1 == exp_watch_sig && cw7 == g {
                            watch_bad = false;
                        }
                    }
                    let mut m = rem;
                    while m != 0 {
                        let i = m.trailing_zeros() as usize;
                        m &= m - 1;
                        let lp = unsafe { base.add(i * 64).cast::<u64>() };
                        let cw0 = unsafe { ptr::read_volatile(lp) };
                        let cw1 = unsafe { ptr::read_volatile(lp.add(1)) };
                        let cw7 = unsafe { ptr::read_volatile(lp.add(7)) };
                        if cw0 == g && cw1 == line_sig(g, i) && cw7 == g {
                            rem &= !(1u64 << i);
                        }
                    }
                    if rem == 0 && !watch_bad {
                        break;
                    }
                    cspins = cspins.wrapping_add(1);
                    if (cspins & 0x3FFF) == 0 && Instant::now() >= conv_dl {
                        conv_failed = true;
                        break;
                    }
                }

                let t_conv1 = read_cntvct();
                if conv_failed {
                    persistent_errors += 1;
                } else {
                    if cntfrq > 0 {
                        let dt_ns = (t_conv1.wrapping_sub(t_conv0) as f64) * 1e9 / (cntfrq as f64);
                        conv_ns_samples.push(dt_ns);
                    }
                    // Final full-block check after convergence.
                    dmb_ishld();
                    let mut final_bad = false;
                    for i in 0..check_lines {
                        let lp = unsafe { base.add(i * 64).cast::<u64>() };
                        let fw0 = unsafe { ptr::read_volatile(lp) };
                        let fw1 = unsafe { ptr::read_volatile(lp.add(1)) };
                        let fw7 = unsafe { ptr::read_volatile(lp.add(7)) };
                        if fw0 != g || fw1 != line_sig(g, i) || fw7 != g {
                            final_bad = true;
                        }
                    }
                    if final_bad {
                        persistent_errors += 1;
                    }
                }
            }

            rounds_observed += 1;

            // 6. Publish ACK to host with release semantics.
            unsafe { stlr_u64(ack_ptr, g) };

            if persistent_errors > 0 {
                break;
            }
        }

        let wall_s = t_start.elapsed().as_secs_f64();
        let p1 = pmu.snap();
        let pmu_json = CardPmu::delta_json(&p0, &p1);

        let reorder_pct = if rounds_observed > 0 {
            (reordered_ops as f64) * 100.0 / (rounds_observed as f64)
        } else {
            0.0
        };
        let missing_mean = if reordered_ops > 0 {
            (missing_sum as f64) / (reordered_ops as f64)
        } else {
            0.0
        };
        let missing_min_out = if reordered_ops > 0 { missing_min } else { 0 };
        let missing_p50 = hist_percentile(&missing_count_hist, reordered_ops, 0.50);
        let missing_p90 = hist_percentile(&missing_count_hist, reordered_ops, 0.90);
        let missing_p99 = hist_percentile(&missing_count_hist, reordered_ops, 0.99);

        let span_mean = if reordered_ops > 0 {
            (span_sum as f64) / (reordered_ops as f64)
        } else {
            0.0
        };
        let span_p50 = hist_percentile(&span_hist, reordered_ops, 0.50);
        let span_p99 = hist_percentile(&span_hist, reordered_ops, 0.99);

        conv_ns_samples.sort_by(|a, b| a.total_cmp(b));
        let conv_p50 = percentile_f64(&conv_ns_samples, 0.50);
        let conv_p90 = percentile_f64(&conv_ns_samples, 0.90);
        let conv_p99 = percentile_f64(&conv_ns_samples, 0.99);
        let conv_max = conv_ns_samples.last().copied().unwrap_or(0.0);
        let conv_mean = if conv_ns_samples.is_empty() {
            0.0
        } else {
            conv_ns_samples.iter().sum::<f64>() / (conv_ns_samples.len() as f64)
        };

        let per_line_str: String = per_line_miss[..check_lines]
            .iter()
            .map(|v| v.to_string())
            .collect::<Vec<_>>()
            .join(",");
        let first_miss_str: String = first_missing_hist[..check_lines]
            .iter()
            .map(|v| v.to_string())
            .collect::<Vec<_>>()
            .join(",");
        let miss_hist_pairs: String = missing_count_hist
            .iter()
            .enumerate()
            .filter(|(k, c)| *k > 0 && **c > 0)
            .map(|(k, c)| format!("[{k},{c}]"))
            .collect::<Vec<_>>()
            .join(",");

        writeln!(
            stream,
            "RESULT {{\"card_cpu\":{card_cpu},\"cntfrq_hz\":{cntfrq},\"size\":{size},\"n_lines\":{n_lines},\"check_lines\":{check_lines},\"doorbell_mode\":{doorbell_mode},\"reverse_scan\":{reverse_scan},\"rounds_req\":{rounds},\"rounds_observed\":{rounds_observed},\"wall_s\":{wall_s:.6},\"reordered_ops\":{reordered_ops},\"reorder_pct\":{reorder_pct:.6},\"persistent_errors\":{persistent_errors},\"timeout_ops\":{timeout_ops},\"unexpected_vals\":{unexpected_vals},\"watch_endpoint_torn\":{watch_endpoint_torn},\"watch_sig_only_torn\":{watch_sig_only_torn},\"body_endpoint_torn\":{body_endpoint_torn},\"body_sig_only_torn\":{body_sig_only_torn},\"missing_lines\":{{\"min\":{missing_min_out},\"mean\":{missing_mean:.3},\"p50\":{missing_p50},\"p90\":{missing_p90},\"p99\":{missing_p99},\"max\":{missing_max}}},\"span_lines\":{{\"mean\":{span_mean:.3},\"p50\":{span_p50},\"p99\":{span_p99},\"max\":{span_max},\"max_bytes\":{}}},\"conv_ns\":{{\"n\":{},\"mean\":{conv_mean:.1},\"p50\":{conv_p50:.1},\"p90\":{conv_p90:.1},\"p99\":{conv_p99:.1},\"max\":{conv_max:.1}}},\"per_line_miss\":[{per_line_str}],\"first_missing_hist\":[{first_miss_str}],\"missing_count_hist\":[{miss_hist_pairs}],\"card_pmu\":{pmu_json}}}",
            span_max * 64,
            conv_ns_samples.len(),
        )?;
        stream.flush()?;
        Ok(())
    }

    fn handle_conn(
        mut stream: TcpStream,
        base: *mut u8,
        pmu: &CardPmu,
        stop: &AtomicBool,
    ) -> Result<bool, Box<dyn Error>> {
        stream.set_nodelay(true)?;
        let reader_stream = stream.try_clone()?;
        let mut reader = BufReader::new(reader_stream);
        let mut line = String::new();
        while !stop.load(Ordering::Relaxed) {
            line.clear();
            let n = reader.read_line(&mut line)?;
            if n == 0 {
                return Ok(false);
            }
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.is_empty() {
                continue;
            }
            match parts[0] {
                "PING" => {
                    writeln!(stream, "PONG")?;
                    stream.flush()?;
                }
                "WATCH" if parts.len() >= 6 => {
                    let size: usize = parts[1].parse()?;
                    let doorbell_mode = match parts[2] {
                        "doorbell" => true,
                        "tail" => false,
                        _ => {
                            writeln!(stream, "ERR bad_mode")?;
                            stream.flush()?;
                            continue;
                        }
                    };
                    let reverse_scan = match parts[3] {
                        "reverse" => true,
                        "forward" => false,
                        _ => {
                            writeln!(stream, "ERR bad_scan")?;
                            stream.flush()?;
                            continue;
                        }
                    };
                    let rounds: u64 = parts[4].parse()?;
                    let base_gen: u64 = parts[5].parse()?;
                    run_watch(base, size, doorbell_mode, reverse_scan, rounds, base_gen, pmu, &mut stream, stop)?;
                }
                "QUIT" => {
                    writeln!(stream, "BYE")?;
                    stream.flush()?;
                    return Ok(true);
                }
                _ => {
                    writeln!(stream, "ERR unknown_cmd")?;
                    stream.flush()?;
                }
            }
        }
        Ok(false)
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let raw_args: Vec<String> = std::env::args().collect();
        if raw_args.iter().any(|a| a == "--restore") {
            let changed = restore_defaults(SLOT)?;
            if changed.is_empty() {
                eprintln!("mrv190_card: BAR4 slot {SLOT} and outbound regions already at firmware defaults");
            } else {
                for line in changed {
                    eprintln!("mrv190_card: restored {line}");
                }
            }
            return Ok(());
        }

        let (eal, app_args) = Eal::init(std::env::args())?;
        let args = Args::parse_from(app_args);

        let stop = Arc::new(AtomicBool::new(false));
        {
            let s = stop.clone();
            ctrlc::set_handler(move || s.store(true, Ordering::Relaxed))?;
        }

        let pmu = CardPmu::open()?;
        let mz = Memzone::reserve_contiguous(&eal, "mrv190_bar4", SLOT_BYTES, 0, SLOT_BYTES as u32)?;
        let base = mz.as_ptr();
        let pa = mz.phys_addr().ok_or("rte_mem_virt2phy failed (run as root)")?;
        unsafe { ptr::write_bytes(base, 0, SLOT_BYTES) };
        unsafe { ptr::write_volatile(base.add(READY_OFFSET).cast::<u64>(), READY) };
        let nonce_word = unsafe { &*base.add(NONCE_OFFSET).cast::<AtomicU64>() };
        nonce_word.store(0, Ordering::Release);
        dsb_sy();

        let _bar4 = Bar4Window::claim(SLOT, pa)?;
        let lease_listener = TcpListener::bind(&args.control)?;
        let sync_listener = TcpListener::bind(&args.sync)?;
        sync_listener.set_nonblocking(true)?;

        let card_cpu = unsafe { libc::sched_getcpu() };
        let cntfrq = read_cntfrq();
        eprintln!(
            "mrv190_card: BAR4 slot {SLOT} ready pa={pa:#x} cpu={card_cpu} cntfrq={cntfrq} ddr_ch={} control={} sync={}",
            pmu.ddr_writes.len(),
            args.control,
            args.sync
        );

        let res: Result<(), Box<dyn Error>> = thread::scope(|scope| {
            let server = lease::serve(scope, lease_listener, nonce_word)?;
            while !stop.load(Ordering::Relaxed) {
                match sync_listener.accept() {
                    Ok((stream, _peer)) => {
                        if handle_conn(stream, base, &pmu, &stop)? {
                            break;
                        }
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(e) => return Err(e.into()),
                }
            }
            server.close();
            Ok(())
        });

        unsafe { ptr::write_volatile(base.add(READY_OFFSET).cast::<u64>(), 0) };
        dsb_sy();
        drop(_bar4);
        eprintln!("mrv190_card: restored BAR4 slot {SLOT} and exiting cleanly");
        res
    }
}
