//! Card-side BAR4 slot 15 MRV-26 lease server, 100% non-multiplexed CN106
//! TAD L3 + 6-channel DDR5 PMU server, and end-of-window integrity verifier
//! for MRV-196 (Socket 1 host memory interference under >= 150 Mpps MMIO writes).
//!
//! Zero card->host traffic is generated during measurement windows so host
//! IIO1/CHA/IMC measurements reflect purely host-originated traffic.
//!
//! Layout in BAR4 slot 15 (4 MiB physically contiguous hugepage):
//! - `0x000000 .. 0x300000` (3 MiB = 49,152 lines of 64 B): host MOVDIR64B sink slices
//!   (each host aggressor thread `t` writes to its disjoint slice `[t * L .. (t+1) * L)`).
//! - `READY_OFFSET` (`4,194,112`) .. end: MRV-26 lease control area (`READY`, `NONCE`).

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv196_card runs on the aarch64 CN106 card");
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
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::Parser;
    use dpdk::{Eal, HostDir, HostDma, Memzone, PcieFunction};
    use testbed::inline::{NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{Bar4Window, restore_defaults};

    const MAX_SINK_BYTES: usize = 3 << 20; // 3 MiB = 49,152 x 64 B lines (< READY_OFFSET)
    const CELL_MAGIC: u64 = 0xA55A_1960_0000_0000;

    #[inline(always)]
    fn cell_sig(seq: u64, global_line: usize, run_tag: u64) -> u64 {
        seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ (global_line as u64) ^ run_tag ^ CELL_MAGIC
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
            if self.en == 0 {
                100.0
            } else {
                self.run as f64 * 100.0 / self.en as f64
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
        /// Sync / PMU / verify TCP address.
        #[arg(long, default_value = "10.10.0.2:7527")]
        sync: String,
        /// Optional CN106 DPI VF PCI BDF (e.g. 0000:06:00.1) for real FromHost PCIe DMA pull arm.
        #[arg(long)]
        dpi_vf: Option<String>,
        /// Restore firmware BAR4/PEM0 defaults and exit.
        #[arg(long)]
        restore: bool,
    }

    struct DpiCtrl {
        active: AtomicBool,
        running: AtomicBool,
        init_state: std::sync::atomic::AtomicU8, // 0=starting, 1=ready, 2=failed
        init_err: std::sync::Mutex<Option<String>>,
        host_iova: AtomicU64,
        ring_bytes: AtomicU64,
        chunk_bytes: AtomicU64,
        depth: AtomicU64,
        copies: AtomicU64,
        bytes: AtomicU64,
        failed: AtomicU64,
        drain_inflight: AtomicU64,
        wall_ns: AtomicU64,
    }

    impl DpiCtrl {
        fn new() -> Self {
            Self {
                active: AtomicBool::new(false),
                running: AtomicBool::new(false),
                init_state: std::sync::atomic::AtomicU8::new(0),
                init_err: std::sync::Mutex::new(None),
                host_iova: AtomicU64::new(0),
                ring_bytes: AtomicU64::new(0),
                chunk_bytes: AtomicU64::new(65536),
                depth: AtomicU64::new(16),
                copies: AtomicU64::new(0),
                bytes: AtomicU64::new(0),
                failed: AtomicU64::new(0),
                drain_inflight: AtomicU64::new(0),
                wall_ns: AtomicU64::new(0),
            }
        }
    }

    fn json_escape(s: &str) -> String {
        let mut out = String::with_capacity(s.len());
        for c in s.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                c if (c as u32) < 0x20 => {}
                c => out.push(c),
            }
        }
        out
    }

    fn dpi_worker(eal: &Eal, vf_name: &str, ctrl: &DpiCtrl, quit: &AtomicBool) -> Result<(), String> {
        let mirror_bytes: usize = 2 << 20;
        let init_res: Result<(Memzone<'_>, HostDma<'_>), String> = (|| {
            let mirror = Memzone::reserve_contiguous(eal, "mrv196_dpi_mirror", mirror_bytes, 0, 4096)
                .map_err(|e| format!("dpi mirror memzone: {e}"))?;
            let dma = HostDma::start(
                eal,
                vf_name,
                4096,
                PcieFunction { core: 0, pf: 0, vf: 1 },
                HostDir::FromHost,
            )
            .map_err(|e| format!("HostDma::start({vf_name}): {e}"))?;
            Ok((mirror, dma))
        })();
        let (mirror, mut dma) = match init_res {
            Ok(v) => {
                ctrl.init_state.store(1, Ordering::Release);
                v
            }
            Err(msg) => {
                eprintln!("mrv196_card dpi_worker init failed: {msg}");
                if let Ok(mut g) = ctrl.init_err.lock() {
                    *g = Some(msg.clone());
                }
                ctrl.init_state.store(2, Ordering::Release);
                return Err(msg);
            }
        };
        let mirror_iova = mirror.iova();

        while !quit.load(Ordering::Acquire) {
            if !ctrl.active.load(Ordering::Acquire) {
                thread::sleep(Duration::from_millis(1));
                continue;
            }
            let host_iova = ctrl.host_iova.load(Ordering::Acquire);
            let ring_bytes = ctrl.ring_bytes.load(Ordering::Acquire).max(4096);
            let chunk = (ctrl.chunk_bytes.load(Ordering::Acquire) as u32).clamp(4096, 65536);
            let depth = (ctrl.depth.load(Ordering::Acquire) as usize).clamp(1, 32);
            let mirror_slots = (mirror_bytes / (chunk as usize)).max(1);

            ctrl.copies.store(0, Ordering::Relaxed);
            ctrl.bytes.store(0, Ordering::Relaxed);
            ctrl.failed.store(0, Ordering::Relaxed);
            ctrl.drain_inflight.store(0, Ordering::Relaxed);
            ctrl.wall_ns.store(0, Ordering::Relaxed);
            ctrl.running.store(true, Ordering::Release);
            let t0 = Instant::now();
            let mut inflight: usize = 0;
            let mut off: u64 = 0;
            let mut m_idx: usize = 0;
            let mut completed_copies: u64 = 0;
            let mut failed_copies: u64 = 0;

            while ctrl.active.load(Ordering::Acquire) && !quit.load(Ordering::Acquire) {
                let mut submitted = false;
                while inflight < depth {
                    let src = host_iova + off;
                    let dst = mirror_iova + ((m_idx % mirror_slots) * (chunk as usize)) as u64;
                    match dma.copy(src, dst, chunk) {
                        Ok(true) => {
                            inflight += 1;
                            off += chunk as u64;
                            if off + (chunk as u64) > ring_bytes {
                                off = 0;
                            }
                            m_idx = m_idx.wrapping_add(1);
                            submitted = true;
                        }
                        Ok(false) => break,
                        Err(e) => {
                            failed_copies += 1;
                            eprintln!("mrv196_card dpi copy err: {e}");
                            break;
                        }
                    }
                }
                if submitted {
                    let _ = dma.submit();
                }
                let (done, any_fail) = dma.completed(64);
                if done > 0 {
                    inflight = inflight.saturating_sub(done as usize);
                    completed_copies += done as u64;
                }
                if any_fail {
                    failed_copies += 1;
                }
            }

            // Drain EVERY in-flight DPI copy before signalling running=false (IOMMU safety!)
            let drain_start = Instant::now();
            while inflight > 0 && drain_start.elapsed() < Duration::from_secs(3) {
                let (done, any_fail) = dma.completed(64);
                if done > 0 {
                    inflight = inflight.saturating_sub(done as usize);
                    completed_copies += done as u64;
                }
                if any_fail {
                    failed_copies += 1;
                }
                if inflight > 0 {
                    std::hint::spin_loop();
                }
            }
            let wall_ns = t0.elapsed().as_nanos() as u64;
            ctrl.copies.store(completed_copies, Ordering::Relaxed);
            ctrl.bytes.store(completed_copies * (chunk as u64), Ordering::Relaxed);
            ctrl.failed.store(failed_copies + (inflight as u64), Ordering::Relaxed);
            ctrl.drain_inflight.store(inflight as u64, Ordering::Release);
            ctrl.wall_ns.store(wall_ns, Ordering::Relaxed);
            if inflight == 0 {
                ctrl.running.store(false, Ordering::Release);
            } else {
                eprintln!(
                    "mrv196_card dpi_worker: drain timeout with {inflight} inflight; keeping running=true"
                );
                while inflight > 0 && !quit.load(Ordering::Acquire) {
                    let (done, any_fail) = dma.completed(64);
                    if done > 0 {
                        inflight = inflight.saturating_sub(done as usize);
                        completed_copies += done as u64;
                        ctrl.copies.store(completed_copies, Ordering::Relaxed);
                        ctrl.bytes.store(completed_copies * (chunk as u64), Ordering::Relaxed);
                        ctrl.drain_inflight.store(inflight as u64, Ordering::Release);
                    }
                    if any_fail {
                        failed_copies += 1;
                        ctrl.failed.store(failed_copies + (inflight as u64), Ordering::Relaxed);
                    }
                    if inflight > 0 {
                        thread::sleep(Duration::from_millis(1));
                    }
                }
                if inflight == 0 {
                    ctrl.running.store(false, Ordering::Release);
                }
            }
        }
        Ok(())
    }

    fn verify_sink(base: *const u8, total_lines: usize, run_tag: u64) -> String {
        dmb_ishld();
        let max_lines = MAX_SINK_BYTES / 64;
        let n = total_lines.min(max_lines);
        let mut zero_lines = 0u64;
        let mut torn_lines = 0u64;
        let mut bad_lines = 0u64;
        let mut min_seq = u64::MAX;
        let mut max_seq = 0u64;
        for line_idx in 0..n {
            let p = unsafe { base.add(line_idx * 64).cast::<u64>() };
            let w0 = unsafe { p.read_volatile() };
            let w1 = unsafe { p.add(1).read_volatile() };
            let w2 = unsafe { p.add(2).read_volatile() };
            let w7 = unsafe { p.add(7).read_volatile() };
            if w0 == 0 && w7 == 0 {
                zero_lines += 1;
                continue;
            }
            if w0 != w7 {
                torn_lines += 1;
                continue;
            }
            let exp_sig = cell_sig(w0, line_idx, run_tag);
            if w2 != run_tag || w1 != exp_sig {
                bad_lines += 1;
                continue;
            }
            if w0 < min_seq {
                min_seq = w0;
            }
            if w0 > max_seq {
                max_seq = w0;
            }
        }
        if min_seq == u64::MAX {
            min_seq = 0;
        }
        format!(
            "{{\"checked_lines\":{n},\"zero_lines\":{zero_lines},\"torn_lines\":{torn_lines},\"bad_lines\":{bad_lines},\"min_seq\":{min_seq},\"max_seq\":{max_seq}}}"
        )
    }

    fn handle_sync(
        mut stream: TcpStream,
        base: *mut u8,
        pmu: &CardPmu,
        pmu_snap: &mut Option<CardSnap>,
        dpi_ctrl: Option<&DpiCtrl>,
        quit: &AtomicBool,
    ) {
        let _ = stream.set_nodelay(true);
        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
        let mut reader = BufReader::new(match stream.try_clone() {
            Ok(s) => s,
            Err(_) => return,
        });
        let mut line = String::new();
        while reader.read_line(&mut line).unwrap_or(0) > 0 {
            let parts: Vec<&str> = line.split_whitespace().collect();
            let reply = match parts.as_slice() {
                ["PING"] => "{\"ok\":true}".to_string(),
                ["CLEAR", n_str] => {
                    let max_lines = MAX_SINK_BYTES / 64;
                    match n_str.parse::<usize>() {
                        Ok(n_lines) if n_lines <= max_lines => {
                            unsafe {
                                base.write_bytes(0, n_lines * 64);
                            }
                            dsb_sy();
                            format!("{{\"ok\":true,\"cleared_lines\":{n_lines}}}")
                        }
                        Ok(n_lines) => format!(
                            "{{\"ok\":false,\"err\":\"clear_out_of_bounds\",\"requested_lines\":{n_lines},\"max_lines\":{max_lines}}}"
                        ),
                        Err(_) => "{\"ok\":false,\"err\":\"invalid_clear_lines\"}".to_string(),
                    }
                }
                ["PMU_START"] => {
                    *pmu_snap = Some(pmu.snap());
                    "{\"ok\":true}".to_string()
                }
                ["PMU_STOP"] => {
                    let s1 = pmu.snap();
                    match pmu_snap.take() {
                        Some(s0) => CardPmu::delta_json(&s0, &s1),
                        None => "{\"err\":\"no_start\"}".to_string(),
                    }
                }
                ["VERIFY", total_str, tag_str] => {
                    let max_lines = MAX_SINK_BYTES / 64;
                    match (total_str.parse::<usize>(), tag_str.parse::<u64>()) {
                        (Ok(total_lines), Ok(run_tag)) if total_lines <= max_lines => {
                            verify_sink(base, total_lines, run_tag)
                        }
                        (Ok(total_lines), _) => format!(
                            "{{\"ok\":false,\"err\":\"verify_out_of_bounds\",\"requested_lines\":{total_lines},\"max_lines\":{max_lines}}}"
                        ),
                        _ => "{\"ok\":false,\"err\":\"invalid_verify_args\"}".to_string(),
                    }
                }
                ["DPI_START", iova_s, rbytes_s, chunk_s, depth_s] => {
                    if let Some(dc) = dpi_ctrl {
                        let t_init = Instant::now();
                        while dc.init_state.load(Ordering::Acquire) == 0 && t_init.elapsed() < Duration::from_secs(2) {
                            thread::sleep(Duration::from_millis(1));
                        }
                        let st = dc.init_state.load(Ordering::Acquire);
                        if st == 2 {
                            let detail = dc
                                .init_err
                                .lock()
                                .ok()
                                .and_then(|g| g.clone())
                                .unwrap_or_else(|| "unknown dpi init error".to_string());
                            format!(
                                "{{\"ok\":false,\"err\":\"dpi_start_failed\",\"detail\":\"{}\"}}",
                                json_escape(&detail)
                            )
                        } else if st != 1 {
                            "{\"ok\":false,\"err\":\"dpi_init_timeout\"}".to_string()
                        } else if dc.running.load(Ordering::Acquire) {
                            "{\"ok\":false,\"err\":\"dpi_already_running\"}".to_string()
                        } else {
                            let iova: u64 = iova_s.parse().unwrap_or(0);
                            let rbytes: u64 = rbytes_s.parse().unwrap_or(16 << 20);
                            let chunk: u64 = chunk_s.parse().unwrap_or(65536);
                            let depth: u64 = depth_s.parse().unwrap_or(16);
                            dc.host_iova.store(iova, Ordering::Release);
                            dc.ring_bytes.store(rbytes, Ordering::Release);
                            dc.chunk_bytes.store(chunk, Ordering::Release);
                            dc.depth.store(depth, Ordering::Release);
                            dc.active.store(true, Ordering::Release);
                            let t0 = Instant::now();
                            while !dc.running.load(Ordering::Acquire)
                                && dc.init_state.load(Ordering::Acquire) == 1
                                && t0.elapsed() < Duration::from_secs(2)
                            {
                                thread::sleep(Duration::from_millis(1));
                            }
                            if dc.running.load(Ordering::Acquire) {
                                "{\"ok\":true}".to_string()
                            } else {
                                dc.active.store(false, Ordering::Release);
                                "{\"ok\":false,\"err\":\"dpi_start_timeout\"}".to_string()
                            }
                        }
                    } else {
                        "{\"ok\":false,\"err\":\"dpi_not_enabled\"}".to_string()
                    }
                }
                ["DPI_STOP"] => {
                    if let Some(dc) = dpi_ctrl {
                        dc.active.store(false, Ordering::Release);
                        let t0 = Instant::now();
                        while dc.running.load(Ordering::Acquire) && t0.elapsed() < Duration::from_secs(3) {
                            thread::sleep(Duration::from_millis(1));
                        }
                        let still_running = dc.running.load(Ordering::Acquire);
                        let drain_inflight = dc.drain_inflight.load(Ordering::Acquire);
                        let copies = dc.copies.load(Ordering::Acquire);
                        let bytes = dc.bytes.load(Ordering::Acquire);
                        let failed = dc.failed.load(Ordering::Acquire);
                        let wall_ns = dc.wall_ns.load(Ordering::Acquire);
                        let gb_s = if wall_ns > 0 { (bytes as f64) / (wall_ns as f64) } else { 0.0 };
                        if !still_running && drain_inflight == 0 {
                            format!(
                                "{{\"ok\":true,\"copies\":{copies},\"bytes\":{bytes},\"failed\":{failed},\"wall_ns\":{wall_ns},\"gb_s\":{gb_s:.4}}}"
                            )
                        } else {
                            format!(
                                "{{\"ok\":false,\"err\":\"dpi_drain_timeout\",\"running\":{still_running},\"drain_inflight\":{drain_inflight},\"copies\":{copies},\"bytes\":{bytes},\"failed\":{failed},\"wall_ns\":{wall_ns},\"gb_s\":{gb_s:.4}}}"
                            )
                        }
                    } else {
                        "{\"ok\":false,\"err\":\"dpi_not_enabled\"}".to_string()
                    }
                }
                ["QUIT"] => {
                    quit.store(true, Ordering::Release);
                    "{\"ok\":true}".to_string()
                }
                _ => "{\"err\":\"unknown_cmd\"}".to_string(),
            };
            let _ = writeln!(stream, "{reply}");
            let _ = stream.flush();
            line.clear();
            if quit.load(Ordering::Acquire) {
                break;
            }
        }
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let raw_args: Vec<String> = std::env::args().collect();
        if raw_args.iter().any(|a| a == "--restore") {
            let changes = restore_defaults(SLOT)?;
            if changes.is_empty() {
                println!("mrv196_card --restore: already at firmware defaults");
            } else {
                println!("mrv196_card --restore: restored {}", changes.join(", "));
            }
            return Ok(());
        }

        let (eal, rest) = Eal::init(raw_args)?;
        let args = Args::parse_from(rest);
        let _ = restore_defaults(SLOT)?;

        let mz = Memzone::reserve_contiguous(&eal, "mrv196_bar4", SLOT_BYTES, 0, SLOT_BYTES as u32)?;
        unsafe {
            mz.as_ptr().write_bytes(0, SLOT_BYTES);
            mz.as_ptr().add(READY_OFFSET).cast::<AtomicU64>().as_ref().unwrap().store(READY, Ordering::Release);
            mz.as_ptr().add(NONCE_OFFSET).cast::<AtomicU64>().as_ref().unwrap().store(0, Ordering::Release);
        }
        dsb_sy();

        let pa = mz.phys_addr().ok_or("rte_mem_virt2phy failed (run as root)")?;
        let _bar4 = Bar4Window::claim(SLOT, pa)?;
        let pmu = CardPmu::open()?;
        let lease_listener = TcpListener::bind(&args.control)?;
        let sync_listener = TcpListener::bind(&args.sync)?;
        sync_listener.set_nonblocking(true)?;

        let quit = Arc::new(AtomicBool::new(false));
        let q_sig = Arc::clone(&quit);
        ctrlc::set_handler(move || {
            q_sig.store(true, Ordering::Release);
        })?;

        let nonce_word: &AtomicU64 =
            unsafe { mz.as_ptr().add(NONCE_OFFSET).cast::<AtomicU64>().as_ref().unwrap() };

        let dpi_ctrl = args.dpi_vf.as_ref().map(|_| DpiCtrl::new());
        println!(
            "mrv196_card: BAR4 slot {} ready pa=0x{:x} (was 0x{:x}) cntfrq={} ddr_ch={} dpi_vf={} control={} sync={}",
            SLOT,
            pa,
            _bar4.previous(),
            read_cntfrq(),
            pmu.ddr_writes.len(),
            args.dpi_vf.as_deref().unwrap_or("none"),
            args.control,
            args.sync
        );
        let _ = io::stdout().flush();

        thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            let lease_srv = lease::serve(scope, lease_listener, nonce_word)?;
            let dpi_handle = if let (Some(vf), Some(dc)) = (&args.dpi_vf, &dpi_ctrl) {
                let eal_ref = &eal;
                let q_ref = &quit;
                Some(scope.spawn(move || dpi_worker(eal_ref, vf, dc, q_ref)))
            } else {
                None
            };
            let mut pmu_snap: Option<CardSnap> = None;

            while !quit.load(Ordering::Acquire) {
                match sync_listener.accept() {
                    Ok((stream, _)) => {
                        handle_sync(stream, mz.as_ptr(), &pmu, &mut pmu_snap, dpi_ctrl.as_ref(), &quit);
                    }
                    Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                    }
                    Err(_) => {
                        thread::sleep(Duration::from_millis(5));
                    }
                }
            }

            if let Some(dc) = &dpi_ctrl {
                dc.active.store(false, Ordering::Release);
            }
            if let Some(h) = dpi_handle {
                let _ = h.join();
            }
            lease_srv.close();
            unsafe {
                mz.as_ptr().add(READY_OFFSET).cast::<AtomicU64>().as_ref().unwrap().store(0, Ordering::Release);
            }
            dsb_sy();
            Ok(())
        })?;

        // `_bar4` drops here and restores BAR4 slot 15 to firmware default (0x2ff71).
        Ok(())
    }
}
