//! MRV-55: Measure the card's actual cache coherency / false-sharing
//! granularity on hugepage memory across distances 0 B, 64 B, 128 B, 256 B,
//! and 4 KiB using two card cores (lcore 3, 4), while recording card TAD PMU
//! (`tad_req_msh_in_any`, `tad_snp_msh_out_any`, ...) and ARMv8 core PMU
//! counters via `perf_event_open`.
//!
//! Workloads:
//! - `idle`: both cores pause for 1 s to record the SoC background TAD rate.
//! - `write-only`: each core repeatedly writes its own 64 B line (`Cell`
//!   format: 7 volatile word stores + 1 release head store) for 1 s.
//! - `rmw`: each core repeatedly loads all 8 words of its 64 B line (acquire
//!   head + 7 volatile loads), adds 1 to each word, and stores all 8 words
//!   back (7 volatile stores + release head) for 1 s.
//! - `atomic-rmw`: each core repeatedly performs an atomic `fetch_add`
//!   (`AcqRel`) on word 0 plus read-modify-write of words 1..7 of its 64 B
//!   line for 1 s.
//! - `poll-bystander`: core 0 continuously polls its 64 B line at offset 0
//!   while core 1 continuously writes the 64 B line at offset `dist` for 1 s.
//! - `poll-pingpong`: closed-loop poll-write latency distribution where core 1
//!   publishes a timestamped 64 B `Cell` at offset 0, core 0 polls offset 0,
//!   verifies integrity, records one-way arrival latency, and publishes an ack
//!   `Cell` at offset `dist` that core 1 polls.
//! - `poll-bystander-lat`: poll-write latency distribution where core 0
//!   hot-polls offset 0 while core 1 writes `bystander_writes` 64 B cells to
//!   offset `dist` and then immediately publishes a timestamped 64 B `Cell` to
//!   offset 0 (recording both post-bystander notification latency `lat_ns` and
//!   total burst+notification latency `burst_lat_ns`).
//!
//! Only allocates and accesses its own hugepage `Memzone`; never touches CSRs.

use std::fs;
use std::io;
use std::mem::size_of;
use std::os::fd::RawFd;
use std::sync::atomic::{AtomicU64, Ordering, fence};

use clap::{Parser, ValueEnum};
use dpdk::{Eal, Memzone, rdtsc, rdtsc_after_loads};
use testbed::inline::{Cell, Slot};

const DISTANCES: &[usize] = &[0, 64, 128, 256, 4096];
const CTRL_OFFSET: usize = 32768; // 32 KiB away from test lines (isolated 4 KiB page)
const ACK_PAGE_OFFSET: usize = 16384; // 16 KiB away from test lines for isolated handshake

#[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
enum Mode {
    All,
    Idle,
    WriteOnly,
    Rmw,
    AtomicRmw,
    PollBystander,
    PollPingpong,
    PollBystanderLat,
}

impl Mode {
    fn as_str(self) -> &'static str {
        match self {
            Mode::All => "all",
            Mode::Idle => "idle",
            Mode::WriteOnly => "write_only",
            Mode::Rmw => "rmw",
            Mode::AtomicRmw => "atomic_rmw",
            Mode::PollBystander => "poll_bystander",
            Mode::PollPingpong => "poll_pingpong",
            Mode::PollBystanderLat => "poll_bystander_lat",
        }
    }
}

#[derive(Parser, Debug)]
struct Args {
    /// Workload mode to execute.
    #[arg(long, value_enum, default_value_t = Mode::All)]
    mode: Mode,
    /// Repetitions per (mode, distance) pair.
    #[arg(long, default_value_t = 5)]
    reps: usize,
    /// Duration in milliseconds for throughput modes (write-only, rmw, poll-bystander).
    #[arg(long, default_value_t = 1000)]
    duration_ms: u64,
    /// Number of timestamped cells per run for latency modes.
    #[arg(long, default_value_t = 500_000)]
    rounds: u64,
    /// Number of bystander writes to `base + dist` before each notification in poll-bystander-lat.
    #[arg(long, default_value_t = 8)]
    bystander_writes: u64,
    /// Optional path to append JSONL records to.
    #[arg(long)]
    jsonl: Option<String>,
}

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
const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0; // bit 0 on little-endian
const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400; // _IO('$', 0)
const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401; // _IO('$', 1)

struct PerfCounter {
    name: &'static str,
    fd: RawFd,
}

#[derive(Clone, Copy, Debug, Default)]
struct CounterSample {
    name: &'static str,
    value: u64,
    time_enabled_ns: u64,
    time_running_ns: u64,
}

impl CounterSample {
    fn delta_from(self, earlier: Self) -> Self {
        CounterSample {
            name: self.name,
            value: self.value.saturating_sub(earlier.value),
            time_enabled_ns: self.time_enabled_ns.saturating_sub(earlier.time_enabled_ns),
            time_running_ns: self.time_running_ns.saturating_sub(earlier.time_running_ns),
        }
    }
}

impl PerfCounter {
    fn open(name: &'static str, type_: u32, config: u64, cpu: i32) -> io::Result<Self> {
        let attr = PerfEventAttr {
            type_,
            size: size_of::<PerfEventAttr>() as u32,
            config,
            read_format: PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING,
            flags: PERF_ATTR_FLAG_DISABLED,
            ..Default::default()
        };
        // SAFETY: valid pointer to PerfEventAttr; pid = -1 (all threads on `cpu`).
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
        Ok(PerfCounter { name, fd: fd as RawFd })
    }

    fn enable(&self) {
        // SAFETY: valid perf_event fd.
        let rc = unsafe { libc::ioctl(self.fd, PERF_EVENT_IOC_ENABLE, 0) };
        assert_eq!(rc, 0, "PERF_EVENT_IOC_ENABLE failed for {}", self.name);
    }

    fn disable(&self) {
        // SAFETY: valid perf_event fd.
        let rc = unsafe { libc::ioctl(self.fd, PERF_EVENT_IOC_DISABLE, 0) };
        assert_eq!(rc, 0, "PERF_EVENT_IOC_DISABLE failed for {}", self.name);
    }

    fn read_sample(&self) -> CounterSample {
        let mut buf = [0u64; 3];
        // SAFETY: reading 3 u64 words matching read_format.
        let n = unsafe {
            libc::read(
                self.fd,
                buf.as_mut_ptr().cast::<libc::c_void>(),
                size_of::<[u64; 3]>(),
            )
        };
        assert_eq!(
            n,
            size_of::<[u64; 3]>() as isize,
            "short read from perf counter {}",
            self.name
        );
        CounterSample {
            name: self.name,
            value: buf[0],
            time_enabled_ns: buf[1],
            time_running_ns: buf[2],
        }
    }
}

impl Drop for PerfCounter {
    fn drop(&mut self) {
        // SAFETY: owned fd.
        unsafe { libc::close(self.fd) };
    }
}

struct PmuSession {
    tad: Vec<PerfCounter>,
    core0: Vec<PerfCounter>,
    core1: Vec<PerfCounter>,
}

struct PmuReadout {
    tad: Vec<CounterSample>,
    core0: Vec<CounterSample>,
    core1: Vec<CounterSample>,
}

fn read_sysfs_u32(path: &str) -> Option<u32> {
    let s = fs::read_to_string(path).ok()?;
    let first = s.trim().split(&[',', '-'][..]).next()?;
    first.parse::<u32>().ok()
}

const TAD_EVENTS: &[(&str, u64)] = &[
    ("tad_req_msh_in_any", 0x01),
    ("tad_dat_msh_in_any", 0x09),
    ("tad_snp_msh_out_any", 0x14),
    ("tad_alloc_dtg", 0x1a),
    ("tad_alloc_ltg", 0x1b),
    ("tad_alloc_any", 0x1c),
    ("tad_hit_dtg", 0x1d),
    ("tad_hit_ltg", 0x1e),
];

const CORE_EVENTS: &[(&str, u64)] = &[
    ("cpu_cycles", 0x0011),
    ("inst_retired", 0x0008),
    ("l1d_cache_refill", 0x0003),
    ("l2d_cache_refill", 0x0017),
    ("bus_access", 0x0019),
];

impl PmuSession {
    fn open_and_enable(cpu0: i32, cpu1: i32) -> Self {
        let mut tad = Vec::new();
        if let (Some(tad_type), Some(tad_cpu)) = (
            read_sysfs_u32("/sys/bus/event_source/devices/tad/type"),
            read_sysfs_u32("/sys/bus/event_source/devices/tad/cpumask"),
        ) {
            for &(name, cfg) in TAD_EVENTS {
                let c = PerfCounter::open(name, tad_type, cfg, tad_cpu as i32)
                    .unwrap_or_else(|e| panic!("open TAD counter {name} failed: {e}"));
                c.enable();
                tad.push(c);
            }
        }
        let mut core0 = Vec::new();
        let mut core1 = Vec::new();
        if let Some(core_type) = read_sysfs_u32("/sys/bus/event_source/devices/armv8_pmuv3/type") {
            for &(name, cfg) in CORE_EVENTS {
                let c0 = PerfCounter::open(name, core_type, cfg, cpu0)
                    .unwrap_or_else(|e| panic!("open core0 counter {name} failed: {e}"));
                let c1 = PerfCounter::open(name, core_type, cfg, cpu1)
                    .unwrap_or_else(|e| panic!("open core1 counter {name} failed: {e}"));
                c0.enable();
                c1.enable();
                core0.push(c0);
                core1.push(c1);
            }
        }
        PmuSession { tad, core0, core1 }
    }

    fn read_tad(&self) -> Vec<CounterSample> {
        self.tad.iter().map(PerfCounter::read_sample).collect()
    }

    fn read_core(&self, idx: usize) -> Vec<CounterSample> {
        let list = if idx == 0 { &self.core0 } else { &self.core1 };
        list.iter().map(PerfCounter::read_sample).collect()
    }

    fn disable_all(&self) {
        for c in &self.tad {
            c.disable();
        }
        for c in &self.core0 {
            c.disable();
        }
        for c in &self.core1 {
            c.disable();
        }
    }
}

fn delta_vec(later: &[CounterSample], earlier: &[CounterSample]) -> Vec<CounterSample> {
    later
        .iter()
        .zip(earlier.iter())
        .map(|(&b, &a)| b.delta_from(a))
        .collect()
}

#[inline(always)]
fn tsc_before_store() -> u64 {
    #[cfg(target_arch = "aarch64")]
    // SAFETY: ISB prevents MRS CNTVCT_EL0 from executing early.
    unsafe {
        std::arch::asm!("isb", options(nostack, preserves_flags));
    }
    rdtsc()
}

/// Write all 64 B of a cell (words 1..7 volatile, word 0 release).
#[inline(always)]
unsafe fn write_line_64b(at: *mut Cell, seq: u64, tag: u64, tsc: u64) {
    let words = Cell::new(seq, tag, tsc).words();
    let ptr = at.cast::<u64>();
    // SAFETY: `at` is a live 64 B-aligned cell inside our memzone.
    unsafe {
        for (i, &w) in words.iter().enumerate().skip(1) {
            ptr.add(i).write_volatile(w);
        }
        AtomicU64::from_ptr(ptr).store(words[0], Ordering::Release);
    }
}

/// Read all 64 B of a cell (word 0 acquire, words 1..7 volatile), add 1 to each
/// word, and write all 64 B back (words 1..7 volatile, word 0 release).
#[inline(always)]
unsafe fn rmw_line_64b(at: *mut Cell) {
    let ptr = at.cast::<u64>();
    // SAFETY: `at` is a live 64 B-aligned cell inside our memzone.
    unsafe {
        let w0 = AtomicU64::from_ptr(ptr).load(Ordering::Acquire).wrapping_add(1);
        let w1 = ptr.add(1).read_volatile().wrapping_add(1);
        let w2 = ptr.add(2).read_volatile().wrapping_add(1);
        let w3 = ptr.add(3).read_volatile().wrapping_add(1);
        let w4 = ptr.add(4).read_volatile().wrapping_add(1);
        let w5 = ptr.add(5).read_volatile().wrapping_add(1);
        let w6 = ptr.add(6).read_volatile().wrapping_add(1);
        let w7 = ptr.add(7).read_volatile().wrapping_add(1);
        ptr.add(1).write_volatile(w1);
        ptr.add(2).write_volatile(w2);
        ptr.add(3).write_volatile(w3);
        ptr.add(4).write_volatile(w4);
        ptr.add(5).write_volatile(w5);
        ptr.add(6).write_volatile(w6);
        ptr.add(7).write_volatile(w7);
        AtomicU64::from_ptr(ptr).store(w0, Ordering::Release);
    }
}

/// Atomic RMW (`fetch_add(1, AcqRel)`) on word 0 plus volatile RMW on words 1..7.
#[inline(always)]
unsafe fn atomic_rmw_line_64b(at: *mut Cell) {
    let ptr = at.cast::<u64>();
    // SAFETY: `at` is a live 64 B-aligned cell inside our memzone.
    unsafe {
        for i in 1..8 {
            let v = ptr.add(i).read_volatile().wrapping_add(1);
            ptr.add(i).write_volatile(v);
        }
        AtomicU64::from_ptr(ptr).fetch_add(1, Ordering::AcqRel);
    }
}

#[repr(C, align(4096))]
struct ControlBlock {
    ready_count: AtomicU64,
    start_tsc: AtomicU64,
    end_tsc: AtomicU64,
    done_count: AtomicU64,
}

fn current_cpu() -> i32 {
    // SAFETY: sched_getcpu has no preconditions.
    unsafe { libc::sched_getcpu() }
}

fn format_counters_json(samples: &[CounterSample]) -> String {
    let mut parts = Vec::with_capacity(samples.len());
    for s in samples {
        let mux_pct = if s.time_enabled_ns > 0 {
            100.0 * (s.time_running_ns as f64) / (s.time_enabled_ns as f64)
        } else {
            100.0
        };
        let per_s = if s.time_enabled_ns > 0 {
            (s.value as f64) / (s.time_enabled_ns as f64 * 1e-9)
        } else {
            0.0
        };
        parts.push(format!(
            "\"{}\":{{\"val\":{},\"per_s\":{:.1},\"enabled_ns\":{},\"running_ns\":{},\"pct\":{:.2}}}",
            s.name, s.value, per_s, s.time_enabled_ns, s.time_running_ns, mux_pct
        ));
    }
    format!("{{{}}}", parts.join(","))
}

fn run_throughput_mode(
    eal: &Eal,
    mz: &Memzone<'_>,
    mode: Mode,
    dist: usize,
    rep: usize,
    duration_ms: u64,
    cpus: [i32; 2],
) -> String {
    let tsc_hz = eal.tsc_hz();
    let dur_ticks = (tsc_hz as u128 * duration_ms as u128 / 1000) as u64;
    let base = mz.as_ptr();
    // SAFETY: memzone is 64 KiB, zeroing first 48 KiB before run.
    unsafe {
        std::ptr::write_bytes(base, 0, 48 * 1024);
    }
    fence(Ordering::SeqCst);

    let pmu = PmuSession::open_and_enable(cpus[0], cpus[1]);
    let base_addr = base as usize;

    let out = eal.on_each_lcore(vec![0usize, 1usize], |idx, _| {
        let base_ptr = base_addr as *mut u8;
        // SAFETY: CTRL_OFFSET is 4 KiB-aligned within the 64 KiB memzone.
        let ctrl = unsafe { &*base_ptr.add(CTRL_OFFSET).cast::<ControlBlock>() };
        let line0 = base_ptr.cast::<Cell>();
        // SAFETY: `dist` <= 4096, within the 64 KiB memzone.
        let line1 = unsafe { base_ptr.add(dist).cast::<Cell>() };
        let my_line = if idx == 0 { line0 } else { line1 };

        let (end_tsc, tad_s0, core_s0) = if idx == 0 {
            if mode == Mode::PollBystander && dist > 0 {
                // SAFETY: valid cell at offset 0, initialized before barrier.
                unsafe { write_line_64b(line0, 1, 1, 1) };
            }
            while ctrl.ready_count.load(Ordering::Acquire) < 1 {
                core::hint::spin_loop();
            }
            let tad_s0 = pmu.read_tad();
            let core_s0 = pmu.read_core(0);
            let t0 = tsc_before_store();
            let t_end = t0 + dur_ticks;
            ctrl.start_tsc.store(t0, Ordering::Relaxed);
            ctrl.end_tsc.store(t_end, Ordering::Release);
            (t_end, tad_s0, core_s0)
        } else {
            ctrl.ready_count.store(1, Ordering::Release);
            let t_end = loop {
                let t_end = ctrl.end_tsc.load(Ordering::Acquire);
                if t_end != 0 {
                    break t_end;
                }
                core::hint::spin_loop();
            };
            let core_s0 = pmu.read_core(1);
            (t_end, Vec::new(), core_s0)
        };

        let mut ops: u64 = 0;
        let mut bad: u64 = 0;
        match mode {
            Mode::Idle => {
                while rdtsc() < end_tsc {
                    for _ in 0..64 {
                        core::hint::spin_loop();
                    }
                }
            }
            Mode::WriteOnly => {
                let tag = idx as u64 + 1;
                while rdtsc() < end_tsc {
                    for _ in 0..32 {
                        ops += 1;
                        // SAFETY: `my_line` is valid 64 B cell.
                        unsafe { write_line_64b(my_line, ops, tag, ops) };
                    }
                }
            }
            Mode::Rmw => {
                while rdtsc() < end_tsc {
                    for _ in 0..32 {
                        ops += 1;
                        // SAFETY: `my_line` is valid 64 B cell.
                        unsafe { rmw_line_64b(my_line) };
                    }
                }
            }
            Mode::AtomicRmw => {
                while rdtsc() < end_tsc {
                    for _ in 0..32 {
                        ops += 1;
                        // SAFETY: `my_line` is valid 64 B cell.
                        unsafe { atomic_rmw_line_64b(my_line) };
                    }
                }
            }
            Mode::PollBystander => {
                if idx == 0 {
                    while rdtsc() < end_tsc {
                        for _ in 0..32 {
                            ops += 1;
                            // SAFETY: valid cell at offset 0.
                            let ptr = line0.cast::<u64>();
                            let head =
                                unsafe { AtomicU64::from_ptr(ptr).load(Ordering::Acquire) };
                            let tail = unsafe { ptr.add(7).read_volatile() };
                            if dist > 0 && (head != 1 || tail != 1) {
                                bad += 1;
                            }
                        }
                    }
                } else {
                    while rdtsc() < end_tsc {
                        for _ in 0..32 {
                            ops += 1;
                            // SAFETY: valid cell at offset `dist`.
                            unsafe { write_line_64b(line1, ops, 2, ops) };
                        }
                    }
                }
            }
            _ => unreachable!(),
        }

        let core_s1 = pmu.read_core(idx);
        let core_delta = delta_vec(&core_s1, &core_s0);

        let (elapsed_ticks, tad_delta) = if idx == 1 {
            ctrl.done_count.store(1, Ordering::Release);
            (0, Vec::new())
        } else {
            while ctrl.done_count.load(Ordering::Acquire) < 1 {
                core::hint::spin_loop();
            }
            let t1 = rdtsc_after_loads();
            let tad_s1 = pmu.read_tad();
            let t0 = ctrl.start_tsc.load(Ordering::Relaxed);
            (t1.saturating_sub(t0), delta_vec(&tad_s1, &tad_s0))
        };
        (ops, elapsed_ticks, bad, tad_delta, core_delta)
    });

    pmu.disable_all();

    let ops0 = out[0].0;
    let ops1 = out[1].0;
    let elapsed_ticks = out[0].1;
    let mut bad = out[0].2 + out[1].2;
    let secs = elapsed_ticks as f64 / tsc_hz as f64;

    // Verify final memory state in hugepage buffer
    // SAFETY: workers have finished.
    unsafe {
        let w0_line0 = base.cast::<u64>().read_volatile();
        let w7_line0 = base.cast::<u64>().add(7).read_volatile();
        let w0_line1 = base.add(dist).cast::<u64>().read_volatile();
        let w7_line1 = base.add(dist).cast::<u64>().add(7).read_volatile();
        match mode {
            Mode::WriteOnly if dist >= 64 => {
                if w0_line0 != ops0 || w7_line0 != ops0 || w0_line1 != ops1 || w7_line1 != ops1 {
                    bad += 1;
                }
            }
            Mode::Rmw if dist >= 64 => {
                if w0_line0 != ops0 || w7_line0 != ops0 || w0_line1 != ops1 || w7_line1 != ops1 {
                    bad += 1;
                }
            }
            Mode::AtomicRmw => {
                if dist == 0 {
                    if w0_line0 != ops0 + ops1 {
                        bad += 1;
                    }
                } else if w0_line0 != ops0 || w0_line1 != ops1 || w7_line0 != ops0 || w7_line1 != ops1
                {
                    bad += 1;
                }
            }
            Mode::PollBystander if dist >= 64 => {
                if w0_line0 != 1 || w7_line0 != 1 || w0_line1 != ops1 || w7_line1 != ops1 {
                    bad += 1;
                }
            }
            _ => {}
        }
    }

    let readout = PmuReadout {
        tad: out[0].3.clone(),
        core0: out[0].4.clone(),
        core1: out[1].4.clone(),
    };
    let mops0 = (ops0 as f64 / secs) / 1e6;
    let mops1 = (ops1 as f64 / secs) / 1e6;
    let tad_req = readout
        .tad
        .iter()
        .find(|s| s.name == "tad_req_msh_in_any")
        .map(|s| s.value)
        .unwrap_or(0);
    let tad_snp = readout
        .tad
        .iter()
        .find(|s| s.name == "tad_snp_msh_out_any")
        .map(|s| s.value)
        .unwrap_or(0);
    let tad_en_ns = readout
        .tad
        .iter()
        .find(|s| s.name == "tad_snp_msh_out_any")
        .map(|s| s.time_enabled_ns)
        .unwrap_or(0);

    println!(
        "mode={} dist={}B rep={} secs={:.4} tad_s={:.4} core0_mops={:.3} core1_mops={:.3} tad_req_msh_in_any={} tad_snp_msh_out_any={} lost=0 bad={} torn=0",
        mode.as_str(),
        dist,
        rep,
        secs,
        tad_en_ns as f64 * 1e-9,
        mops0,
        mops1,
        tad_req,
        tad_snp,
        bad
    );
    assert_eq!(bad, 0, "integrity check failed: bad={bad}");

    format!(
        "{{\"mode\":\"{}\",\"dist_bytes\":{},\"rep\":{},\"cpus\":[{},{}],\"tsc_hz\":{},\"secs\":{:.6},\"core0_ops\":{},\"core1_ops\":{},\"core0_mops\":{:.4},\"core1_mops\":{:.4},\"lost\":0,\"bad\":{},\"torn\":0,\"tad\":{},\"core0_pmu\":{},\"core1_pmu\":{}}}",
        mode.as_str(),
        dist,
        rep,
        cpus[0],
        cpus[1],
        tsc_hz,
        secs,
        ops0,
        ops1,
        mops0,
        mops1,
        bad,
        format_counters_json(&readout.tad),
        format_counters_json(&readout.core0),
        format_counters_json(&readout.core1),
    )
}

fn run_latency_mode(
    eal: &Eal,
    mz: &Memzone<'_>,
    mode: Mode,
    dist: usize,
    rep: usize,
    rounds: u64,
    bystander_writes: u64,
    cpus: [i32; 2],
) -> String {
    let tsc_hz = eal.tsc_hz();
    let base = mz.as_ptr();
    // SAFETY: memzone is 64 KiB, zeroing first 48 KiB before run.
    unsafe {
        std::ptr::write_bytes(base, 0, 48 * 1024);
    }
    fence(Ordering::SeqCst);

    let pmu = PmuSession::open_and_enable(cpus[0], cpus[1]);
    let base_addr = base as usize;

    let out = eal.on_each_lcore(vec![0usize, 1usize], |idx, _| {
        let base_ptr = base_addr as *mut u8;
        // SAFETY: CTRL_OFFSET is 4 KiB-aligned within the 64 KiB memzone.
        let ctrl = unsafe { &*base_ptr.add(CTRL_OFFSET).cast::<ControlBlock>() };
        let req_cell = base_ptr.cast::<Cell>();
        // SAFETY: `dist` <= 4096, within the 64 KiB memzone.
        let dist_cell = unsafe { base_ptr.add(dist).cast::<Cell>() };
        // SAFETY: ACK_PAGE_OFFSET is 16 KiB away, within the 64 KiB memzone.
        let ack_word = unsafe { &*base_ptr.add(ACK_PAGE_OFFSET).cast::<AtomicU64>() };

        let (tad_s0, core_s0) = if idx == 0 {
            while ctrl.ready_count.load(Ordering::Acquire) < 1 {
                core::hint::spin_loop();
            }
            let tad_s0 = pmu.read_tad();
            let core_s0 = pmu.read_core(0);
            let t0 = tsc_before_store();
            ctrl.start_tsc.store(t0, Ordering::Relaxed);
            ctrl.end_tsc.store(1, Ordering::Release);
            (tad_s0, core_s0)
        } else {
            ctrl.ready_count.store(1, Ordering::Release);
            while ctrl.end_tsc.load(Ordering::Acquire) == 0 {
                core::hint::spin_loop();
            }
            let core_s0 = pmu.read_core(1);
            (Vec::new(), core_s0)
        };

        let mut samples_ticks = if idx == 0 {
            Vec::with_capacity(rounds as usize)
        } else {
            Vec::new()
        };
        let mut burst_samples_ticks = if idx == 0 {
            Vec::with_capacity(rounds as usize)
        } else {
            Vec::new()
        };
        let mut partial: u64 = 0;
        let mut bad: u64 = 0;
        let mut torn: u64 = 0;

        match mode {
            Mode::PollPingpong => {
                if idx == 0 {
                    for r in 1..=rounds {
                        let req_seq = 2 * r - 1;
                        let ack_seq = 2 * r;
                        let start_wait = rdtsc();
                        let cell = loop {
                            // SAFETY: `req_cell` is a valid 64 B cell.
                            match unsafe { Cell::poll(req_cell, req_seq) } {
                                Slot::Empty => {
                                    if rdtsc().wrapping_sub(start_wait) > tsc_hz * 5 {
                                        torn += 1;
                                        break Cell::new(req_seq, r, start_wait);
                                    }
                                }
                                Slot::Partial => {
                                    partial += 1;
                                    if rdtsc().wrapping_sub(start_wait) > tsc_hz * 5 {
                                        torn += 1;
                                        break Cell::new(req_seq, r, start_wait);
                                    }
                                }
                                Slot::Complete(c) => break c,
                            }
                        };
                        let t_arr = rdtsc_after_loads();
                        if !cell.payload_matches(req_seq) || cell.tag != r {
                            bad += 1;
                        }
                        let dt = t_arr.saturating_sub(cell.tsc);
                        samples_ticks.push(dt);
                        burst_samples_ticks.push(dt);
                        // SAFETY: `dist_cell` is a valid 64 B cell at offset `dist`.
                        unsafe { write_line_64b(dist_cell, ack_seq, r, t_arr) };
                    }
                } else {
                    for r in 1..=rounds {
                        let req_seq = 2 * r - 1;
                        let ack_seq = 2 * r;
                        let t_send = tsc_before_store();
                        // SAFETY: `req_cell` is a valid 64 B cell at offset 0.
                        unsafe { write_line_64b(req_cell, req_seq, r, t_send) };
                        let start_wait = rdtsc();
                        loop {
                            // SAFETY: `dist_cell` is a valid 64 B cell at offset `dist`.
                            match unsafe { Cell::poll(dist_cell, ack_seq) } {
                                Slot::Empty => {
                                    if rdtsc().wrapping_sub(start_wait) > tsc_hz * 5 {
                                        torn += 1;
                                        break;
                                    }
                                }
                                Slot::Partial => {
                                    partial += 1;
                                    if rdtsc().wrapping_sub(start_wait) > tsc_hz * 5 {
                                        torn += 1;
                                        break;
                                    }
                                }
                                Slot::Complete(c) => {
                                    if !c.payload_matches(ack_seq) || c.tag != r {
                                        bad += 1;
                                    }
                                    break;
                                }
                            }
                        }
                    }
                }
            }
            Mode::PollBystanderLat => {
                // Core 0 only polls `req_cell` (offset 0); its ready handshake uses
                // `ack_word` on an isolated 4 KiB page (ACK_PAGE_OFFSET = 16 KiB).
                // Once core 0 is spinning on `req_cell`, core 1 records `t_burst_start`,
                // writes `bystander_writes` 64 B cells to `dist_cell` (offset `dist`),
                // records `t_send`, and publishes `Cell(r, t_burst_start, t_send)` to
                // `req_cell` (offset 0).
                if idx == 0 {
                    for r in 1..=rounds {
                        ack_word.store(r, Ordering::Release);
                        let start_wait = rdtsc();
                        let cell = loop {
                            // SAFETY: `req_cell` is a valid 64 B cell.
                            match unsafe { Cell::poll(req_cell, r) } {
                                Slot::Empty => {
                                    if rdtsc().wrapping_sub(start_wait) > tsc_hz * 5 {
                                        torn += 1;
                                        break Cell::new(r, start_wait, start_wait);
                                    }
                                }
                                Slot::Partial => {
                                    partial += 1;
                                    if rdtsc().wrapping_sub(start_wait) > tsc_hz * 5 {
                                        torn += 1;
                                        break Cell::new(r, start_wait, start_wait);
                                    }
                                }
                                Slot::Complete(c) => break c,
                            }
                        };
                        let t_arr = rdtsc_after_loads();
                        if !cell.payload_matches(r) || cell.tag == 0 || cell.tsc < cell.tag {
                            bad += 1;
                        }
                        samples_ticks.push(t_arr.saturating_sub(cell.tsc));
                        burst_samples_ticks.push(t_arr.saturating_sub(cell.tag));
                    }
                } else {
                    for r in 1..=rounds {
                        while ack_word.load(Ordering::Acquire) != r {
                            core::hint::spin_loop();
                        }
                        // Let core 0 load `req_cell` into its L1D and spin on it.
                        for _ in 0..24 {
                            core::hint::spin_loop();
                        }
                        let t_burst_start = tsc_before_store();
                        for b in 0..bystander_writes {
                            let dummy_seq = u64::MAX - b;
                            // SAFETY: `dist_cell` is a valid 64 B cell at offset `dist`.
                            unsafe { write_line_64b(dist_cell, dummy_seq, b, dummy_seq) };
                        }
                        let t_send = tsc_before_store();
                        // SAFETY: `req_cell` is a valid 64 B cell at offset 0.
                        unsafe { write_line_64b(req_cell, r, t_burst_start, t_send) };
                    }
                }
            }
            _ => unreachable!(),
        }

        let core_s1 = pmu.read_core(idx);
        let core_delta = delta_vec(&core_s1, &core_s0);

        let (elapsed_ticks, tad_delta) = if idx == 1 {
            ctrl.done_count.store(1, Ordering::Release);
            (0, Vec::new())
        } else {
            while ctrl.done_count.load(Ordering::Acquire) < 1 {
                core::hint::spin_loop();
            }
            let t1 = rdtsc_after_loads();
            let tad_s1 = pmu.read_tad();
            let t0 = ctrl.start_tsc.load(Ordering::Relaxed);
            (t1.saturating_sub(t0), delta_vec(&tad_s1, &tad_s0))
        };

        (
            samples_ticks,
            burst_samples_ticks,
            partial,
            bad,
            torn,
            elapsed_ticks,
            tad_delta,
            core_delta,
        )
    });

    pmu.disable_all();

    let mut ticks = out[0].0.clone();
    let mut burst_ticks = out[0].1.clone();
    let partial = out[0].2 + out[1].2;
    let bad = out[0].3 + out[1].3;
    let torn = out[0].4 + out[1].4;
    let elapsed_ticks = out[0].5;
    let secs = elapsed_ticks as f64 / tsc_hz as f64;
    let mmsg = (rounds as f64 / secs) / 1e6;

    ticks.sort_unstable();
    burst_ticks.sort_unstable();
    let ns_per_tick = 1e9 / tsc_hz as f64;
    let q_ns = |v: &[u64], q: f64| -> f64 {
        let idx = ((v.len() - 1) as f64 * q) as usize;
        v[idx] as f64 * ns_per_tick
    };
    let mean_ns =
        (ticks.iter().map(|&t| t as f64).sum::<f64>() / ticks.len() as f64) * ns_per_tick;
    let burst_mean_ns = (burst_ticks.iter().map(|&t| t as f64).sum::<f64>()
        / burst_ticks.len() as f64)
        * ns_per_tick;

    let readout = PmuReadout {
        tad: out[0].6.clone(),
        core0: out[0].7.clone(),
        core1: out[1].7.clone(),
    };
    let tad_req = readout
        .tad
        .iter()
        .find(|s| s.name == "tad_req_msh_in_any")
        .map(|s| s.value)
        .unwrap_or(0);
    let tad_snp = readout
        .tad
        .iter()
        .find(|s| s.name == "tad_snp_msh_out_any")
        .map(|s| s.value)
        .unwrap_or(0);

    println!(
        "mode={} dist={}B rep={} rounds={} secs={:.4} mmsg={:.3} | lat_ns: min {:.1} p50 {:.1} p90 {:.1} p99 {:.1} p99.9 {:.1} max {:.1} mean {:.2} | burst_lat_ns: p50 {:.1} p90 {:.1} p99 {:.1} mean {:.2} | partial={} lost=0 bad={} torn={} | tad_req={} tad_snp={}",
        mode.as_str(),
        dist,
        rep,
        rounds,
        secs,
        mmsg,
        q_ns(&ticks, 0.0),
        q_ns(&ticks, 0.5),
        q_ns(&ticks, 0.9),
        q_ns(&ticks, 0.99),
        q_ns(&ticks, 0.999),
        q_ns(&ticks, 1.0),
        mean_ns,
        q_ns(&burst_ticks, 0.5),
        q_ns(&burst_ticks, 0.9),
        q_ns(&burst_ticks, 0.99),
        burst_mean_ns,
        partial,
        bad,
        torn,
        tad_req,
        tad_snp
    );
    assert_eq!(bad, 0, "integrity check failed: bad={bad}");
    assert_eq!(torn, 0, "integrity check failed: torn={torn}");

    let mut cdf_pts = Vec::with_capacity(105);
    let mut burst_cdf_pts = Vec::with_capacity(105);
    for i in 0..=100 {
        let p = i as f64 / 100.0;
        cdf_pts.push(format!("[{:.4},{:.2}]", p, q_ns(&ticks, p)));
        burst_cdf_pts.push(format!("[{:.4},{:.2}]", p, q_ns(&burst_ticks, p)));
    }
    for &p in &[0.995, 0.999, 0.9995, 0.9999] {
        cdf_pts.push(format!("[{:.4},{:.2}]", p, q_ns(&ticks, p)));
        burst_cdf_pts.push(format!("[{:.4},{:.2}]", p, q_ns(&burst_ticks, p)));
    }

    format!(
        "{{\"mode\":\"{}\",\"dist_bytes\":{},\"rep\":{},\"cpus\":[{},{}],\"tsc_hz\":{},\"rounds\":{},\"bystander_writes\":{},\"secs\":{:.6},\"mmsg\":{:.4},\"partial\":{},\"lost\":0,\"bad\":{},\"torn\":{},\"lat_ns\":{{\"min\":{:.2},\"p10\":{:.2},\"p25\":{:.2},\"p50\":{:.2},\"p75\":{:.2},\"p90\":{:.2},\"p95\":{:.2},\"p99\":{:.2},\"p999\":{:.2},\"p9999\":{:.2},\"max\":{:.2},\"mean\":{:.2}}},\"burst_lat_ns\":{{\"min\":{:.2},\"p10\":{:.2},\"p25\":{:.2},\"p50\":{:.2},\"p75\":{:.2},\"p90\":{:.2},\"p95\":{:.2},\"p99\":{:.2},\"p999\":{:.2},\"p9999\":{:.2},\"max\":{:.2},\"mean\":{:.2}}},\"cdf_ns\":[{}],\"burst_cdf_ns\":[{}],\"tad\":{},\"core0_pmu\":{},\"core1_pmu\":{}}}",
        mode.as_str(),
        dist,
        rep,
        cpus[0],
        cpus[1],
        tsc_hz,
        rounds,
        bystander_writes,
        secs,
        mmsg,
        partial,
        bad,
        torn,
        q_ns(&ticks, 0.0),
        q_ns(&ticks, 0.10),
        q_ns(&ticks, 0.25),
        q_ns(&ticks, 0.50),
        q_ns(&ticks, 0.75),
        q_ns(&ticks, 0.90),
        q_ns(&ticks, 0.95),
        q_ns(&ticks, 0.99),
        q_ns(&ticks, 0.999),
        q_ns(&ticks, 0.9999),
        q_ns(&ticks, 1.0),
        mean_ns,
        q_ns(&burst_ticks, 0.0),
        q_ns(&burst_ticks, 0.10),
        q_ns(&burst_ticks, 0.25),
        q_ns(&burst_ticks, 0.50),
        q_ns(&burst_ticks, 0.75),
        q_ns(&burst_ticks, 0.90),
        q_ns(&burst_ticks, 0.95),
        q_ns(&burst_ticks, 0.99),
        q_ns(&burst_ticks, 0.999),
        q_ns(&burst_ticks, 0.9999),
        q_ns(&burst_ticks, 1.0),
        burst_mean_ns,
        cdf_pts.join(","),
        burst_cdf_pts.join(","),
        format_counters_json(&readout.tad),
        format_counters_json(&readout.core0),
        format_counters_json(&readout.core1),
    )
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let (eal, rest) = Eal::init(std::env::args())?;
    let args = Args::parse_from(rest);
    assert_eq!(
        eal.lcores(),
        2,
        "card_coherency requires exactly 2 lcores (e.g. -l 3,4)"
    );

    let mz = Memzone::reserve_contiguous(&eal, "mrv55_coherency", 64 * 1024, -1, 4096)?;
    let va = mz.as_ptr() as usize;
    let pa = mz.phys_addr().unwrap_or(0);
    let iova = mz.iova();
    assert_eq!(va % 4096, 0, "Memzone VA must be 4 KiB-aligned");
    let cpus_vec = eal.on_each_lcore(vec![0, 1], |_, _| current_cpu());
    let cpus = [cpus_vec[0], cpus_vec[1]];

    println!(
        "card_coherency: cpus={:?} tsc_hz={} memzone va=0x{:x} iova=0x{:x} pa=0x{:x}",
        cpus,
        eal.tsc_hz(),
        va,
        iova,
        pa
    );

    let throughput_modes: &[Mode] = match args.mode {
        Mode::All => &[
            Mode::WriteOnly,
            Mode::Rmw,
            Mode::AtomicRmw,
            Mode::PollBystander,
        ],
        Mode::Idle => &[Mode::Idle],
        Mode::WriteOnly => &[Mode::WriteOnly],
        Mode::Rmw => &[Mode::Rmw],
        Mode::AtomicRmw => &[Mode::AtomicRmw],
        Mode::PollBystander => &[Mode::PollBystander],
        Mode::PollPingpong | Mode::PollBystanderLat => &[],
    };

    let latency_modes: &[Mode] = match args.mode {
        Mode::All => &[Mode::PollPingpong, Mode::PollBystanderLat],
        Mode::PollPingpong => &[Mode::PollPingpong],
        Mode::PollBystanderLat => &[Mode::PollBystanderLat],
        _ => &[],
    };

    let mut records = Vec::new();

    // Interleave across distances for each repetition so any drift affects all distances equally.
    for rep in 0..args.reps {
        if args.mode == Mode::All {
            let rec = run_throughput_mode(&eal, &mz, Mode::Idle, 0, rep, args.duration_ms, cpus);
            records.push(rec);
        }
        for &m in throughput_modes {
            let dists = if m == Mode::Idle { &[0usize][..] } else { DISTANCES };
            for &dist in dists {
                let rec =
                    run_throughput_mode(&eal, &mz, m, dist, rep, args.duration_ms, cpus);
                records.push(rec);
            }
        }
        for &m in latency_modes {
            for &dist in DISTANCES {
                let rec = run_latency_mode(
                    &eal,
                    &mz,
                    m,
                    dist,
                    rep,
                    args.rounds,
                    args.bystander_writes,
                    cpus,
                );
                records.push(rec);
            }
        }
    }

    if let Some(path) = &args.jsonl {
        let mut content = records.join("\n");
        content.push('\n');
        fs::write(path, content)?;
        println!("wrote {} records to {}", records.len(), path);
    }

    Ok(())
}
