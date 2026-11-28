//! MRV-231 card-side microbenchmark (aarch64 CN106, root):
//!
//! 1. `--mode arm-snoop`: pure on-chip ARM <-> ARM TAD snoop bandwidth ceiling
//!    (zero PCIe, zero BAR4). Spawns K in {0, 1, 2, 4, 8} independent pairs of
//!    ARM cores (cpus 4..19) exchanging verified 64 B Cells through per-pair
//!    128 B-strided L1D-resident rings (or single hot lines), recording 100%
//!    non-multiplexed CN106 TAD PMU (8 counters) and ARMv8 PMUv3 counters.
//!
//! 2. `--mode bar4-server`: claims ONLY BAR4 slot 15 (`inline::SLOT = 15`,
//!    MRV-26 compliant) backed by a 4 MiB slice of a 512 MiB MAP_HUGETLB page,
//!    serves the standard MRV-26 TCP lease on `10.10.0.2:7526`, and exposes a
//!    lightweight control/PMU RPC on `10.10.0.2:7527`.
//!    Inside leased BAR4 slot 15, up to 4 independent sub-rings (960 KiB apart,
//!    128 B cell stride) are polled by `lanes * cards_per_lane` (up to 8)
//!    independent ARM cores (cpus 4..11) with configurable read ordering:
//!    `none` | `ldar` | `ldr` | `ldr_dmb` | `ldnp`.
//!    With `--wc`, each worker claims its approved PEM0 outbound region
//!    (`255 - w`, regions 248..255 via `/dev/pem_wc`) and writes verified 64 B
//!    WC replies (`store64_gathered`) to the host's closed-loop reply ring
//!    (reproducing MRV-75's exact WC closed-loop regime with zero BAR4 credit reads).

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv231_card runs on the aarch64 card");
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
    use std::fs::{self, File};
    use std::io::{BufRead, BufReader, Write};
    use std::net::{TcpListener, TcpStream};
    use std::os::unix::fs::FileExt;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
    use std::sync::{Arc, Barrier, Mutex};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use testbed::inline::{Cell, NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{
        Bar4Window, OUTBOUND_REGION_BYTES, OUTBOUND_REGIONS, OutboundWindow, restore_defaults, store64_gathered,
    };

    const HUGE_512M: usize = 512 * 1024 * 1024;
    const MAX_LANES: usize = 4;
    const MAX_WORKERS: usize = 8; // up to 4 lanes x 2 card cores/lane -> regions 248..255
    const REPLY_CELLS_PER_WORKER: usize = 1024;
    /// 960 KiB spacing inside 4 MiB BAR4 slot 15 (4 * 960 KiB = 3840 KiB < 4 MiB).
    const LANE_STRIDE_BYTES: usize = 960 * 1024;
    const CTRL_LANE_BASE: usize = SLOT_BYTES - 4096;
    const CELL_STRIDE_BYTES: usize = 128;

    const WORKER_CPUS: [usize; MAX_WORKERS] = [4, 5, 6, 7, 8, 9, 10, 11];

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Mode {
        ArmSnoop,
        Bar4Server,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum ArmPattern {
        Ring,
        Hotline,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum PollMode {
        None,
        Ldar,
        Ldr,
        LdrDmb,
        Ldnp,
    }

    impl PollMode {
        fn parse_str(s: &str) -> Result<Self, String> {
            match s {
                "none" => Ok(Self::None),
                "ldar" => Ok(Self::Ldar),
                "ldr" => Ok(Self::Ldr),
                "ldr_dmb" => Ok(Self::LdrDmb),
                "ldnp" => Ok(Self::Ldnp),
                _ => Err(format!("unknown poll_mode: {s}")),
            }
        }

        fn as_str(self) -> &'static str {
            match self {
                Self::None => "none",
                Self::Ldar => "ldar",
                Self::Ldr => "ldr",
                Self::LdrDmb => "ldr_dmb",
                Self::Ldnp => "ldnp",
            }
        }
    }

    #[derive(Parser, Debug)]
    struct Args {
        #[arg(long, value_enum)]
        mode: Mode,
        #[arg(long, default_value_t = 1)]
        pairs: usize,
        #[arg(long, value_enum, default_value_t = ArmPattern::Ring)]
        arm_pattern: ArmPattern,
        #[arg(long, default_value_t = 64)]
        ring_cells: usize,
        #[arg(long, default_value_t = 300)]
        warmup_ms: u64,
        #[arg(long, default_value_t = 1000)]
        duration_ms: u64,
        #[arg(long, default_value_t = 0)]
        rep: usize,
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        #[arg(long, default_value = "10.10.0.2:7527")]
        rpc: String,
        /// Enable PEM0 outbound WC reply regions 248..255 (`/dev/pem_wc` must be loaded).
        #[arg(long)]
        wc: bool,
        #[arg(long)]
        restore: bool,
    }

    fn pin_cpu(cpu: usize) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu, &mut set);
            let rc = libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set);
            assert_eq!(rc, 0, "sched_setaffinity(cpu={cpu}) failed");
        }
    }

    struct HugeBuf {
        ptr: *mut u8,
        len: usize,
        pa: u64,
    }
    unsafe impl Send for HugeBuf {}
    unsafe impl Sync for HugeBuf {}

    impl HugeBuf {
        fn alloc_512m() -> Result<Self, Box<dyn Error>> {
            let p = unsafe {
                libc::mmap(
                    ptr::null_mut(),
                    HUGE_512M,
                    libc::PROT_READ | libc::PROT_WRITE,
                    libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_HUGETLB,
                    -1,
                    0,
                )
            };
            if p == libc::MAP_FAILED {
                return Err(format!("mmap 512M MAP_HUGETLB failed: {}", std::io::Error::last_os_error()).into());
            }
            let base = p as *mut u8;
            unsafe { ptr::write_bytes(base, 0, 16 * 1024 * 1024); }
            let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) } as u64;
            let pagemap = File::open("/proc/self/pagemap")?;
            let read_pa = |off: usize| -> Result<u64, Box<dyn Error>> {
                let vpn = (base as u64 + off as u64) / page_size;
                let mut buf = [0u8; 8];
                pagemap.read_exact_at(&mut buf, vpn * 8)?;
                let entry = u64::from_ne_bytes(buf);
                if (entry >> 63) & 1 == 0 {
                    return Err(format!("pagemap entry at offset {off:#x} not present").into());
                }
                let pfn = entry & ((1u64 << 55) - 1);
                Ok(pfn * page_size)
            };
            let pa0 = read_pa(0)?;
            if pa0 % (SLOT_BYTES as u64) != 0 {
                return Err(format!("hugepage pa {pa0:#x} not 4 MiB aligned").into());
            }
            for off in (0..16 * 1024 * 1024).step_by(65536) {
                let pa = read_pa(off)?;
                if pa != pa0 + off as u64 {
                    return Err(format!("hugepage not contiguous at {off:#x}: {pa:#x} != {:#x}", pa0 + off as u64).into());
                }
            }
            Ok(HugeBuf { ptr: base, len: HUGE_512M, pa: pa0 })
        }
    }

    impl Drop for HugeBuf {
        fn drop(&mut self) {
            unsafe { libc::munmap(self.ptr.cast(), self.len); }
        }
    }

    // -------------------------------------------------------------------------
    // perf_event_open wrapper (100% non-multiplexed TAD + ARMv8 PMUv3)
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

    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    struct PerfCounter {
        name: &'static str,
        fd: libc::c_int,
    }

    #[derive(Clone, Copy, Default)]
    struct CounterSample {
        val: u64,
        ena: u64,
        run: u64,
    }

    impl CounterSample {
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
        fn open(name: &'static str, type_: u32, config: u64, cpu: i32) -> Result<Self, String> {
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
                return Err(format!("perf_event_open({name}, type={type_}, cfg={config:#x}, cpu={cpu}): {}", std::io::Error::last_os_error()));
            }
            Ok(Self { name, fd })
        }
        fn enable(&self) { unsafe { libc::ioctl(self.fd, PERF_EVENT_IOC_ENABLE, 0); } }
        fn disable(&self) { unsafe { libc::ioctl(self.fd, PERF_EVENT_IOC_DISABLE, 0); } }
        fn read(&self) -> CounterSample {
            let mut buf = [0u64; 3];
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast(), std::mem::size_of_val(&buf)) };
            assert_eq!(n as usize, std::mem::size_of_val(&buf));
            CounterSample { val: buf[0], ena: buf[1], run: buf[2] }
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

    // Exactly 8 CN106 TAD PMU v1 hardware counters (all <= 0x24) = 100.0% non-multiplexed!
    const TAD_EVENTS: &[(&str, u64)] = &[
        ("tad_req_msh_in_any", 0x01),
        ("tad_dat_msh_in_any", 0x09),
        ("tad_snp_msh_out_any", 0x14),
        ("tad_req_occ", 0x24),
        ("tad_ifb_occ", 0x23),
        ("tad_rsp_retry_exlmn", 0x10),
        ("tad_hit_any", 0x1f),
        ("tad_alloc_any", 0x1c),
    ];

    const CORE_EVENTS: &[(&str, u64)] = &[
        ("cpu_cycles", 0x0011),
        ("inst_retired", 0x0008),
        ("l1d_cache_refill", 0x0003),
        ("l2d_cache_refill", 0x0017),
    ];

    struct CardPmu {
        tad: Vec<PerfCounter>,
        cores: Vec<(usize, Vec<PerfCounter>)>,
    }

    struct CardPmuSnap {
        tad: Vec<CounterSample>,
        cores: Vec<(usize, Vec<CounterSample>)>,
    }

    impl CardPmu {
        fn open(cpus: &[usize]) -> Result<Self, String> {
            let tad_type = read_sysfs_u32("/sys/bus/event_source/devices/tad/type")?;
            let tad_cpu = read_sysfs_u32("/sys/bus/event_source/devices/tad/cpumask")? as i32;
            let mut tad = Vec::with_capacity(TAD_EVENTS.len());
            for &(name, cfg) in TAD_EVENTS {
                let c = PerfCounter::open(name, tad_type, cfg, tad_cpu)?;
                c.enable();
                tad.push(c);
            }
            let core_type = read_sysfs_u32("/sys/bus/event_source/devices/armv8_pmuv3/type")?;
            let mut cores = Vec::with_capacity(cpus.len());
            for &cpu in cpus {
                let mut v = Vec::with_capacity(CORE_EVENTS.len());
                for &(name, cfg) in CORE_EVENTS {
                    let c = PerfCounter::open(name, core_type, cfg, cpu as i32)?;
                    c.enable();
                    v.push(c);
                }
                cores.push((cpu, v));
            }
            Ok(Self { tad, cores })
        }

        fn snap(&self) -> CardPmuSnap {
            CardPmuSnap {
                tad: self.tad.iter().map(PerfCounter::read).collect(),
                cores: self.cores.iter().map(|(cpu, v)| (*cpu, v.iter().map(PerfCounter::read).collect())).collect(),
            }
        }

        fn format_delta_json(&self, s0: &CardPmuSnap, s1: &CardPmuSnap) -> (String, f64) {
            let mut min_pct = 100.0_f64;
            let mut tad_parts = Vec::with_capacity(self.tad.len());
            for (i, c) in self.tad.iter().enumerate() {
                let d = s1.tad[i].delta(s0.tad[i]);
                let p = d.pct();
                if p < min_pct { min_pct = p; }
                tad_parts.push(format!("\"{}\":{{\"val\":{},\"pct\":{:.2}}}", c.name, d.val, p));
            }
            let mut core_parts = Vec::with_capacity(self.cores.len());
            for (ci, (cpu, counters)) in self.cores.iter().enumerate() {
                let mut evs = Vec::with_capacity(counters.len() + 1);
                evs.push(format!("\"cpu\":{cpu}"));
                for (ei, c) in counters.iter().enumerate() {
                    let d = s1.cores[ci].1[ei].delta(s0.cores[ci].1[ei]);
                    let p = d.pct();
                    if p < min_pct { min_pct = p; }
                    evs.push(format!("\"{}\":{}", c.name, d.val));
                }
                core_parts.push(format!("{{{}}}", evs.join(",")));
            }
            let json = format!(
                "\"min_pct\":{min_pct:.2},\"tad\":{{{}}},\"core_pmu\":[{}]",
                tad_parts.join(","),
                core_parts.join(",")
            );
            (json, min_pct)
        }
    }

    impl Drop for CardPmu {
        fn drop(&mut self) {
            for c in &self.tad { c.disable(); }
            for (_, v) in &self.cores {
                for c in v { c.disable(); }
            }
        }
    }

    // -------------------------------------------------------------------------
    // Explicit ARM64 head load instructions (verified via aarch64-objdump)
    // -------------------------------------------------------------------------
    enum PollRes {
        Empty,
        Partial,
        Complete(Cell),
    }

    #[inline(never)]
    unsafe fn poll_cell_ldar(at: *const Cell, seq: u64) -> PollRes {
        let head: u64;
        unsafe {
            asm!(
                "ldar {head}, [{ptr}]",
                ptr = in(reg) at,
                head = out(reg) head,
                options(nostack, preserves_flags),
            );
        }
        if head != seq {
            return PollRes::Empty;
        }
        unsafe { read_verify_tail(at, head, seq) }
    }

    #[inline(never)]
    unsafe fn poll_cell_ldr(at: *const Cell, seq: u64) -> PollRes {
        let head: u64;
        unsafe {
            asm!(
                "ldr {head}, [{ptr}]",
                ptr = in(reg) at,
                head = out(reg) head,
                options(nostack, preserves_flags),
            );
        }
        if head != seq {
            return PollRes::Empty;
        }
        unsafe { read_verify_tail(at, head, seq) }
    }

    #[inline(never)]
    unsafe fn poll_cell_ldr_dmb(at: *const Cell, seq: u64) -> PollRes {
        let head: u64;
        unsafe {
            asm!(
                "ldr {head}, [{ptr}]",
                ptr = in(reg) at,
                head = out(reg) head,
                options(nostack, preserves_flags),
            );
        }
        if head != seq {
            return PollRes::Empty;
        }
        unsafe {
            asm!("dmb ishld", options(nostack, preserves_flags));
            read_verify_tail(at, head, seq)
        }
    }

    #[inline(never)]
    unsafe fn poll_cell_ldnp(at: *const Cell, seq: u64) -> PollRes {
        let head: u64;
        let _w1: u64;
        unsafe {
            asm!(
                "ldnp {head}, {w1}, [{ptr}]",
                ptr = in(reg) at,
                head = out(reg) head,
                w1 = out(reg) _w1,
                options(nostack, preserves_flags),
            );
        }
        if head != seq {
            return PollRes::Empty;
        }
        unsafe {
            asm!("dmb ishld", options(nostack, preserves_flags));
            read_verify_tail(at, head, seq)
        }
    }

    #[inline(always)]
    unsafe fn read_verify_tail(at: *const Cell, head: u64, seq: u64) -> PollRes {
        let p = at.cast::<u64>();
        let mut words = [0u64; 8];
        words[0] = head;
        for i in 1..8 {
            words[i] = unsafe { p.add(i).read_volatile() };
        }
        if words[7] != seq {
            return PollRes::Partial;
        }
        let cell: Cell = unsafe { std::mem::transmute::<[u64; 8], Cell>(words) };
        PollRes::Complete(cell)
    }

    // -------------------------------------------------------------------------
    // Control 1: Pure on-chip ARM <-> ARM TAD snoop bandwidth ceiling
    // -------------------------------------------------------------------------
    fn run_arm_snoop(args: &Args) -> Result<(), Box<dyn Error>> {
        if !matches!(args.pairs, 0 | 1 | 2 | 4 | 8) {
            return Err(format!("--pairs must be 0, 1, 2, 4, or 8, got {}", args.pairs).into());
        }
        pin_cpu(1);
        let buf = Arc::new(HugeBuf::alloc_512m()?);

        if args.pairs == 0 {
            let pmu = CardPmu::open(&[])?;
            let t0 = Instant::now();
            let s0 = pmu.snap();
            thread::sleep(Duration::from_millis(args.duration_ms));
            let s1 = pmu.snap();
            let elapsed_ns = t0.elapsed().as_nanos() as u64;
            let (pmu_json, _) = pmu.format_delta_json(&s0, &s1);
            println!(
                "MRV231_ARM {{\"exp\":\"arm_snoop\",\"pattern\":\"idle\",\"pairs\":0,\"ring_cells\":{},\"rep\":{},\"elapsed_ns\":{},\"total_msgs\":0,\"mops\":0.0,\"corrupt\":0,\"lost\":0,{}}}",
                args.ring_cells, args.rep, elapsed_ns, pmu_json
            );
            return Ok(());
        }

        let k_pairs = args.pairs;
        let ring_cells = args.ring_cells;
        let credit_w = (ring_cells / 2).max(1) as u64;
        assert!(ring_cells * CELL_STRIDE_BYTES < 512 * 1024);

        let mut worker_cpus = Vec::with_capacity(k_pairs * 2);
        for p in 0..k_pairs {
            worker_cpus.push(4 + 2 * p);
            worker_cpus.push(4 + 2 * p + 1);
        }
        let pmu = CardPmu::open(&worker_cpus)?;

        let phase = Arc::new(AtomicUsize::new(0));
        let start_barrier = Arc::new(Barrier::new(k_pairs * 2 + 1));

        let sent_start: Vec<Arc<AtomicU64>> = (0..k_pairs).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let sent_end: Vec<Arc<AtomicU64>> = (0..k_pairs).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let sent_total: Vec<Arc<AtomicU64>> = (0..k_pairs).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let taken_start: Vec<Arc<AtomicU64>> = (0..k_pairs).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let taken_end: Vec<Arc<AtomicU64>> = (0..k_pairs).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let taken_total: Vec<Arc<AtomicU64>> = (0..k_pairs).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let corrupt_cnt: Vec<Arc<AtomicU64>> = (0..k_pairs).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let reader_polls: Vec<Arc<AtomicU64>> = (0..k_pairs).map(|_| Arc::new(AtomicU64::new(0))).collect();

        let pattern = args.arm_pattern;
        let mut handles = Vec::with_capacity(k_pairs * 2);

        for pair in 0..k_pairs {
            let pair_base = pair * 1024 * 1024;
            let ack_off = pair_base + 512 * 1024;
            let hot_off = pair * 4224;

            {
                let buf = buf.clone();
                let phase = phase.clone();
                let bar = start_barrier.clone();
                let ss = sent_start[pair].clone();
                let se = sent_end[pair].clone();
                let st = sent_total[pair].clone();
                let w_cpu = 4 + 2 * pair;
                handles.push(thread::spawn(move || {
                    pin_cpu(w_cpu);
                    bar.wait();
                    match pattern {
                        ArmPattern::Ring => {
                            let ack = unsafe { &*(buf.ptr.add(ack_off) as *const AtomicU64) };
                            let mut seq: u64 = 1;
                            let mut allowed: u64 = credit_w;
                            let mut latched_start = false;
                            loop {
                                if (seq & 15) == 1 {
                                    let ph = phase.load(Ordering::Acquire);
                                    if ph == 1 && !latched_start {
                                        ss.store(seq - 1, Ordering::Relaxed);
                                        latched_start = true;
                                    } else if ph >= 2 {
                                        se.store(seq - 1, Ordering::Relaxed);
                                        st.store(seq - 1, Ordering::Release);
                                        break;
                                    }
                                }
                                while seq > allowed {
                                    let ph = phase.load(Ordering::Acquire);
                                    if ph >= 2 {
                                        se.store(seq - 1, Ordering::Relaxed);
                                        st.store(seq - 1, Ordering::Release);
                                        return;
                                    }
                                    allowed = ack.load(Ordering::Acquire) + credit_w;
                                }
                                let idx = ((seq - 1) as usize) % ring_cells;
                                let dst = unsafe { buf.ptr.add(pair_base + idx * CELL_STRIDE_BYTES).cast::<Cell>() };
                                let cell = Cell::new(seq, seq, 1);
                                unsafe { cell.publish(dst) };
                                seq += 1;
                            }
                        }
                        ArmPattern::Hotline => {
                            let word = unsafe { &*(buf.ptr.add(hot_off) as *const AtomicU64) };
                            let mut seq: u64 = 1;
                            let mut latched_start = false;
                            loop {
                                if (seq & 63) == 1 {
                                    let ph = phase.load(Ordering::Acquire);
                                    if ph == 1 && !latched_start {
                                        ss.store(seq - 1, Ordering::Relaxed);
                                        latched_start = true;
                                    } else if ph >= 2 {
                                        se.store(seq - 1, Ordering::Relaxed);
                                        st.store(seq - 1, Ordering::Release);
                                        break;
                                    }
                                }
                                word.store(seq, Ordering::Release);
                                seq += 1;
                            }
                        }
                    }
                }));
            }

            {
                let buf = buf.clone();
                let phase = phase.clone();
                let bar = start_barrier.clone();
                let ts = taken_start[pair].clone();
                let te = taken_end[pair].clone();
                let tt = taken_total[pair].clone();
                let st = sent_total[pair].clone();
                let corr = corrupt_cnt[pair].clone();
                let polls = reader_polls[pair].clone();
                let r_cpu = 4 + 2 * pair + 1;
                handles.push(thread::spawn(move || {
                    pin_cpu(r_cpu);
                    bar.wait();
                    match pattern {
                        ArmPattern::Ring => {
                            let ack = unsafe { &*(buf.ptr.add(ack_off) as *const AtomicU64) };
                            let mut next: u64 = 1;
                            let mut latched_start = false;
                            let mut latched_end = false;
                            let mut local_corr: u64 = 0;
                            let mut local_polls: u64 = 0;
                            loop {
                                let idx = ((next - 1) as usize) % ring_cells;
                                let at = unsafe { buf.ptr.add(pair_base + idx * CELL_STRIDE_BYTES).cast::<Cell>() };
                                if let PollRes::Complete(cell) = unsafe { poll_cell_ldar(at, next) } {
                                    if !cell.payload_matches(next) {
                                        local_corr += 1;
                                    }
                                    next += 1;
                                    if ((next - 1) & 7) == 0 {
                                        ack.store(next - 1, Ordering::Release);
                                    }
                                    if ((next - 1) & 15) == 0 {
                                        let ph = phase.load(Ordering::Acquire);
                                        if ph == 1 && !latched_start {
                                            ts.store(next - 1, Ordering::Relaxed);
                                            latched_start = true;
                                        } else if ph >= 2 && !latched_end {
                                            te.store(next - 1, Ordering::Relaxed);
                                            latched_end = true;
                                        }
                                    }
                                } else {
                                    local_polls += 1;
                                    if (local_polls & 63) == 0 {
                                        ack.store(next - 1, Ordering::Release);
                                        let ph = phase.load(Ordering::Acquire);
                                        if ph == 1 && !latched_start {
                                            ts.store(next - 1, Ordering::Relaxed);
                                            latched_start = true;
                                        } else if ph >= 2 {
                                            if !latched_end {
                                                te.store(next - 1, Ordering::Relaxed);
                                                latched_end = true;
                                            }
                                            let target = st.load(Ordering::Acquire);
                                            if target > 0 && next - 1 >= target {
                                                break;
                                            }
                                        }
                                    }
                                }
                            }
                            tt.store(next - 1, Ordering::Release);
                            corr.store(local_corr, Ordering::Relaxed);
                            polls.store(local_polls, Ordering::Relaxed);
                        }
                        ArmPattern::Hotline => {
                            let word = unsafe { &*(buf.ptr.add(hot_off) as *const AtomicU64) };
                            let mut prev: u64 = 0;
                            let mut obs: u64 = 0;
                            let mut local_corr: u64 = 0;
                            let mut local_polls: u64 = 0;
                            let mut latched_start = false;
                            loop {
                                let v = word.load(Ordering::Acquire);
                                local_polls += 1;
                                if v < prev {
                                    local_corr += 1;
                                } else if v > prev {
                                    prev = v;
                                    obs += 1;
                                }
                                if (local_polls & 63) == 0 {
                                    let ph = phase.load(Ordering::Acquire);
                                    if ph == 1 && !latched_start {
                                        ts.store(obs, Ordering::Relaxed);
                                        latched_start = true;
                                    } else if ph >= 2 {
                                        te.store(obs, Ordering::Relaxed);
                                        tt.store(obs, Ordering::Release);
                                        break;
                                    }
                                }
                            }
                            corr.store(local_corr, Ordering::Relaxed);
                            polls.store(local_polls, Ordering::Relaxed);
                        }
                    }
                }));
            }
        }

        start_barrier.wait();
        thread::sleep(Duration::from_millis(args.warmup_ms));

        let t0 = Instant::now();
        let s0 = pmu.snap();
        phase.store(1, Ordering::Release);
        thread::sleep(Duration::from_millis(args.duration_ms));
        phase.store(2, Ordering::Release);
        let s1 = pmu.snap();
        let elapsed_ns = t0.elapsed().as_nanos() as u64;

        for h in handles {
            h.join().expect("arm_snoop worker panicked");
        }

        let mut total_sent_win: u64 = 0;
        let mut total_taken_win: u64 = 0;
        let mut total_corrupt: u64 = 0;
        let mut total_lost: u64 = 0;
        let mut per_pair = Vec::with_capacity(k_pairs);
        for p in 0..k_pairs {
            let sw = sent_end[p].load(Ordering::Relaxed).saturating_sub(sent_start[p].load(Ordering::Relaxed));
            let tw = taken_end[p].load(Ordering::Relaxed).saturating_sub(taken_start[p].load(Ordering::Relaxed));
            let st = sent_total[p].load(Ordering::Relaxed);
            let tt = taken_total[p].load(Ordering::Relaxed);
            let c = corrupt_cnt[p].load(Ordering::Relaxed);
            let lost = if pattern == ArmPattern::Ring { st.abs_diff(tt) } else { 0 };
            total_sent_win += sw;
            total_taken_win += tw;
            total_corrupt += c;
            total_lost += lost;
            per_pair.push(format!(
                "{{\"pair\":{},\"sent_win\":{},\"taken_win\":{},\"sent_tot\":{},\"taken_tot\":{},\"corrupt\":{},\"polls\":{}}}",
                p, sw, tw, st, tt, c, reader_polls[p].load(Ordering::Relaxed)
            ));
        }
        let secs = elapsed_ns as f64 / 1e9;
        let mops = (total_taken_win as f64 / secs) / 1e6;
        let sent_mops = (total_sent_win as f64 / secs) / 1e6;
        let (pmu_json, _) = pmu.format_delta_json(&s0, &s1);
        let pat_str = match pattern { ArmPattern::Ring => "ring", ArmPattern::Hotline => "hotline" };

        println!(
            "MRV231_ARM {{\"exp\":\"arm_snoop\",\"pattern\":\"{pat_str}\",\"pairs\":{k_pairs},\"ring_cells\":{ring_cells},\"rep\":{},\"elapsed_ns\":{elapsed_ns},\"total_msgs\":{total_taken_win},\"sent_msgs\":{total_sent_win},\"mops\":{mops:.4},\"sent_mops\":{sent_mops:.4},\"corrupt\":{total_corrupt},\"lost\":{total_lost},\"per_pair\":[{}],{pmu_json}}}",
            args.rep,
            per_pair.join(",")
        );
        Ok(())
    }

    // -------------------------------------------------------------------------
    // Control 2 & 3: BAR4 slot 15 server (strictly MRV-26 leased, optional WC reply)
    // -------------------------------------------------------------------------
    struct WorkerRuntime {
        taken: AtomicU64,
        empty_polls: AtomicU64,
        busy_polls: AtomicU64,
        corrupt: AtomicU64,
        partial: AtomicU64,
        next_seq: AtomicU64,
    }

    impl WorkerRuntime {
        fn new() -> Self {
            Self {
                taken: AtomicU64::new(0),
                empty_polls: AtomicU64::new(0),
                busy_polls: AtomicU64::new(0),
                corrupt: AtomicU64::new(0),
                partial: AtomicU64::new(0),
                next_seq: AtomicU64::new(1),
            }
        }
        fn reset(&self, start_seq: u64) {
            self.taken.store(0, Ordering::Relaxed);
            self.empty_polls.store(0, Ordering::Relaxed);
            self.busy_polls.store(0, Ordering::Relaxed);
            self.corrupt.store(0, Ordering::Relaxed);
            self.partial.store(0, Ordering::Relaxed);
            self.next_seq.store(start_seq, Ordering::Relaxed);
        }
    }

    #[derive(Clone, Copy)]
    struct ReplyTarget {
        base_ptr: *mut u8,
        ring_off: usize,
    }
    unsafe impl Send for ReplyTarget {}
    unsafe impl Sync for ReplyTarget {}

    fn run_bar4_server(args: &Args) -> Result<(), Box<dyn Error>> {
        pin_cpu(1);
        let changed = restore_defaults(SLOT)?;
        for c in &changed {
            eprintln!("mrv231_card: restored {c}");
        }

        let listener = TcpListener::bind(&args.control)
            .map_err(|e| format!("bind lease control {}: {e}", args.control))?;
        let rpc_listener = TcpListener::bind(&args.rpc)
            .map_err(|e| format!("bind rpc {}: {e}", args.rpc))?;
        rpc_listener.set_nonblocking(true)?;

        let quit = Arc::new(AtomicBool::new(false));
        let q = quit.clone();
        ctrlc::set_handler(move || q.store(true, Ordering::Relaxed))?;

        let buf = Arc::new(HugeBuf::alloc_512m()?);
        unsafe { ptr::write_bytes(buf.ptr, 0, SLOT_BYTES); }

        // Optional approved PEM0 outbound WC windows (regions 255..=248, host VF0 -> pf=0, vf=1)
        let mut outbound: Vec<Mutex<OutboundWindow>> = Vec::new();
        if args.wc {
            for w in 0..MAX_WORKERS {
                let region_idx = OUTBOUND_REGIONS - 1 - w; // 255, 254, ..., 248
                let ow = OutboundWindow::claim(region_idx, 0, 1, true)?;
                outbound.push(Mutex::new(ow));
            }
        }
        let outbound = Arc::new(outbound);

        // Claim ONLY BAR4 slot 15 (`inline::SLOT = 15`)! Dropped before `buf`.
        let window = Bar4Window::claim(SLOT, buf.pa)?;
        let ctrl_ready = unsafe { &*(buf.ptr.add(READY_OFFSET) as *const AtomicU64) };
        let ctrl_nonce = unsafe { &*(buf.ptr.add(NONCE_OFFSET) as *const AtomicU64) };
        for lane in 0..MAX_LANES {
            let next_word = unsafe { &*(buf.ptr.add(CTRL_LANE_BASE + lane * 128) as *const AtomicU64) };
            next_word.store(1, Ordering::Relaxed);
        }
        ctrl_ready.store(READY, Ordering::Release);
        println!(
            "mrv231_card: BAR4 slot {SLOT} -> pa {:#x} (was {:#x}); wc={}; hosts lease the slot at {}; rpc at {}",
            buf.pa, window.previous(), args.wc, args.control, args.rpc
        );

        let active_lanes = Arc::new(AtomicUsize::new(0));
        let active_cpl = Arc::new(AtomicUsize::new(1)); // cards_per_lane (1 or 2)
        let active_ring_cells = Arc::new(AtomicUsize::new(4096));
        let active_poll_mode = Arc::new(AtomicUsize::new(0)); // 0=none, 1=ldar, 2=ldr, 3=ldr_dmb, 4=ldnp
        let active_reply_iova = Arc::new(AtomicU64::new(0));
        let reply_targets: Vec<Arc<Mutex<Option<ReplyTarget>>>> = (0..MAX_WORKERS).map(|_| Arc::new(Mutex::new(None))).collect();
        let run_gen = Arc::new(AtomicU64::new(0));
        let worker_ready_gen: Vec<Arc<AtomicU64>> = (0..MAX_WORKERS).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let worker_rt: Vec<Arc<WorkerRuntime>> = (0..MAX_WORKERS).map(|_| Arc::new(WorkerRuntime::new())).collect();

        let pmu = CardPmu::open(&WORKER_CPUS)?;
        let wc_enabled = args.wc;

        let res = thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            let lease_srv = lease::serve(scope, listener, ctrl_nonce)?;

            for w in 0..MAX_WORKERS {
                let buf = buf.clone();
                let quit = quit.clone();
                let active_lanes = active_lanes.clone();
                let active_cpl = active_cpl.clone();
                let active_ring_cells = active_ring_cells.clone();
                let active_poll_mode = active_poll_mode.clone();
                let my_reply = reply_targets[w].clone();
                let run_gen = run_gen.clone();
                let my_ready = worker_ready_gen[w].clone();
                let rt = worker_rt[w].clone();
                scope.spawn(move || {
                    pin_cpu(WORKER_CPUS[w]);
                    let mut cur_gen = 0u64;
                    while !quit.load(Ordering::Relaxed) {
                        let g = run_gen.load(Ordering::Acquire);
                        if g == cur_gen || (g & 1) == 0 {
                            if g != cur_gen {
                                cur_gen = g;
                                my_ready.store(cur_gen, Ordering::Release);
                            }
                            thread::sleep(Duration::from_micros(200));
                            continue;
                        }
                        cur_gen = g;
                        let k_lanes = active_lanes.load(Ordering::Acquire);
                        let cpl = active_cpl.load(Ordering::Acquire);
                        let ring_cells = active_ring_cells.load(Ordering::Acquire);
                        let pm = active_poll_mode.load(Ordering::Acquire);
                        let total_active = k_lanes * cpl;
                        if w >= total_active || pm == 0 {
                            my_ready.store(cur_gen, Ordering::Release);
                            while run_gen.load(Ordering::Acquire) == cur_gen && !quit.load(Ordering::Relaxed) {
                                thread::sleep(Duration::from_micros(200));
                            }
                            continue;
                        }

                        let lane = w / cpl;
                        let sub = w % cpl;
                        let lane_base = lane * LANE_STRIDE_BYTES;
                        let card_next_word = unsafe { &*(buf.ptr.add(CTRL_LANE_BASE + lane * 128) as *const AtomicU64) };
                        let rtarget = *my_reply.lock().unwrap();

                        // Pre-warm this worker's stride of the sub-ring into L1D/L2 before signaling ready.
                        let mut sink: u64 = 0;
                        let prewarm_n = ring_cells.min(512);
                        for idx in (sub..prewarm_n).step_by(cpl) {
                            let p = unsafe { buf.ptr.add(lane_base + idx * CELL_STRIDE_BYTES).cast::<u64>() };
                            sink = sink.wrapping_add(unsafe { p.read_volatile() });
                        }
                        std::hint::black_box(sink);
                        my_ready.store(cur_gen, Ordering::Release);

                        let mut next: u64 = sub as u64 + 1;
                        let mut nth: usize = 0;
                        let mut local_taken: u64 = 0;
                        let mut local_empty: u64 = 0;
                        let mut local_busy: u64 = 0;
                        let mut local_corrupt: u64 = 0;
                        let mut local_partial: u64 = 0;
                        let mut in_busy_burst = false;
                        let mut iter_chk: u64 = 0;

                        while !quit.load(Ordering::Relaxed) {
                            let idx = ((next - 1) as usize) % ring_cells;
                            let at = unsafe { buf.ptr.add(lane_base + idx * CELL_STRIDE_BYTES).cast::<Cell>() };
                            let got = unsafe {
                                match pm {
                                    1 => poll_cell_ldar(at, next),
                                    2 => poll_cell_ldr(at, next),
                                    3 => poll_cell_ldr_dmb(at, next),
                                    4 => poll_cell_ldnp(at, next),
                                    _ => PollRes::Empty,
                                }
                            };
                            match got {
                                PollRes::Complete(mut cell) => {
                                    if !cell.payload_matches(next) {
                                        local_corrupt += 1;
                                        cell.tsc = 0;
                                    }
                                    if let Some(rtg) = rtarget {
                                        let slot_off = (w * REPLY_CELLS_PER_WORKER + (nth % REPLY_CELLS_PER_WORKER)) * 64;
                                        let dst = unsafe { rtg.base_ptr.add(rtg.ring_off + slot_off) };
                                        let words = cell.words();
                                        unsafe { store64_gathered(dst, &words) };
                                    }
                                    next += cpl as u64;
                                    nth += 1;
                                    local_taken += 1;
                                    if !in_busy_burst {
                                        local_busy += 1;
                                        in_busy_burst = true;
                                    }
                                    if (local_taken & 15) == 0 {
                                        if cpl == 1 && rtarget.is_none() {
                                            card_next_word.store(next, Ordering::Release);
                                        }
                                        rt.taken.store(local_taken, Ordering::Relaxed);
                                        rt.next_seq.store(next, Ordering::Relaxed);
                                    }
                                }
                                PollRes::Partial => {
                                    local_partial += 1;
                                }
                                PollRes::Empty => {
                                    if in_busy_burst {
                                        if cpl == 1 && rtarget.is_none() {
                                            card_next_word.store(next, Ordering::Release);
                                        }
                                        rt.taken.store(local_taken, Ordering::Relaxed);
                                        rt.next_seq.store(next, Ordering::Relaxed);
                                        in_busy_burst = false;
                                    }
                                    local_empty += 1;
                                    iter_chk += 1;
                                    if (iter_chk & 127) == 0 {
                                        if run_gen.load(Ordering::Acquire) != cur_gen {
                                            break;
                                        }
                                        rt.empty_polls.store(local_empty, Ordering::Relaxed);
                                        rt.busy_polls.store(local_busy, Ordering::Relaxed);
                                        rt.corrupt.store(local_corrupt, Ordering::Relaxed);
                                        rt.partial.store(local_partial, Ordering::Relaxed);
                                    }
                                }
                            }
                        }
                        if cpl == 1 && rtarget.is_none() {
                            card_next_word.store(next, Ordering::Release);
                        }
                        rt.taken.store(local_taken, Ordering::Release);
                        rt.empty_polls.store(local_empty, Ordering::Release);
                        rt.busy_polls.store(local_busy, Ordering::Release);
                        rt.corrupt.store(local_corrupt, Ordering::Release);
                        rt.partial.store(local_partial, Ordering::Release);
                        rt.next_seq.store(next, Ordering::Release);
                    }
                });
            }

            let mut pmu_t0: Option<Instant> = None;
            let mut pmu_s0: Option<CardPmuSnap> = None;
            let mut pmu_taken0 = [0u64; MAX_WORKERS];
            let mut pmu_empty0 = [0u64; MAX_WORKERS];
            let mut pmu_busy0 = [0u64; MAX_WORKERS];
            let mut cur_gen: u64 = 0;

            let handle_conn = |stream: TcpStream,
                               cur_gen: &mut u64,
                               pmu_t0: &mut Option<Instant>,
                               pmu_s0: &mut Option<CardPmuSnap>,
                               pmu_taken0: &mut [u64; MAX_WORKERS],
                               pmu_empty0: &mut [u64; MAX_WORKERS],
                               pmu_busy0: &mut [u64; MAX_WORKERS]| {
                let _ = stream.set_nodelay(true);
                let _ = stream.set_nonblocking(false);
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut writer = stream;
                let mut line = String::new();
                while reader.read_line(&mut line).unwrap_or(0) > 0 {
                    let parts: Vec<&str> = line.trim().split_whitespace().collect();
                    if parts.is_empty() {
                        line.clear();
                        continue;
                    }
                    let resp = match parts[0] {
                        // PREPARE <lanes> <cards_per_lane> <ring_cells> <poll_mode> <reply_iova_hex>
                        "PREPARE" if parts.len() == 6 => {
                            let k: usize = parts[1].parse().unwrap_or(0);
                            let cpl: usize = parts[2].parse().unwrap_or(0);
                            let r: usize = parts[3].parse().unwrap_or(0);
                            let pm = PollMode::parse_str(parts[4]);
                            let riova = u64::from_str_radix(parts[5].trim_start_matches("0x"), 16).unwrap_or(u64::MAX);
                            if k == 0 || k > MAX_LANES || (cpl != 1 && cpl != 2) || k * cpl > MAX_WORKERS
                                || r == 0 || r * CELL_STRIDE_BYTES > LANE_STRIDE_BYTES || pm.is_err() || riova == u64::MAX
                                || (riova != 0 && !wc_enabled)
                            {
                                format!("ERR bad PREPARE args: {}\n", line.trim())
                            } else {
                                let pm = pm.unwrap();
                                if (*cur_gen & 1) == 1 {
                                    *cur_gen += 1;
                                    run_gen.store(*cur_gen, Ordering::Release);
                                    for w in 0..MAX_WORKERS {
                                        while worker_ready_gen[w].load(Ordering::Acquire) != *cur_gen {
                                            thread::sleep(Duration::from_micros(50));
                                        }
                                    }
                                }
                                // Retarget outbound WC windows if reply_iova != 0
                                let mut retarget_err: Option<String> = None;
                                if riova != 0 {
                                    let region_base = riova & !(OUTBOUND_REGION_BYTES - 1);
                                    let ring_off = (riova - region_base) as usize;
                                    for w in 0..(k * cpl) {
                                        let mut ow = outbound[w].lock().unwrap();
                                        if let Err(e) = ow.retarget(region_base) {
                                            retarget_err = Some(format!("ERR retarget w={w}: {e}\n"));
                                            break;
                                        }
                                        *reply_targets[w].lock().unwrap() = Some(ReplyTarget {
                                            base_ptr: ow.as_ptr(),
                                            ring_off,
                                        });
                                    }
                                    for w in (k * cpl)..MAX_WORKERS {
                                        *reply_targets[w].lock().unwrap() = None;
                                    }
                                } else {
                                    for w in 0..MAX_WORKERS {
                                        *reply_targets[w].lock().unwrap() = None;
                                    }
                                }
                                if let Some(err) = retarget_err {
                                    err
                                } else {
                                    for l in 0..k {
                                        unsafe {
                                            ptr::write_bytes(buf.ptr.add(l * LANE_STRIDE_BYTES), 0, r * CELL_STRIDE_BYTES);
                                        }
                                        let nw = unsafe { &*(buf.ptr.add(CTRL_LANE_BASE + l * 128) as *const AtomicU64) };
                                        nw.store(1, Ordering::Release);
                                    }
                                    for w in 0..MAX_WORKERS {
                                        worker_rt[w].reset((w % cpl) as u64 + 1);
                                    }
                                    active_lanes.store(k, Ordering::Release);
                                    active_cpl.store(cpl, Ordering::Release);
                                    active_ring_cells.store(r, Ordering::Release);
                                    active_reply_iova.store(riova, Ordering::Release);
                                    active_poll_mode.store(
                                        match pm {
                                            PollMode::None => 0,
                                            PollMode::Ldar => 1,
                                            PollMode::Ldr => 2,
                                            PollMode::LdrDmb => 3,
                                            PollMode::Ldnp => 4,
                                        },
                                        Ordering::Release,
                                    );
                                    *cur_gen += 1;
                                    run_gen.store(*cur_gen, Ordering::Release);
                                    for w in 0..MAX_WORKERS {
                                        while worker_ready_gen[w].load(Ordering::Acquire) != *cur_gen {
                                            thread::sleep(Duration::from_micros(50));
                                        }
                                    }
                                    format!("OK lanes={k} cpl={cpl} ring_cells={r} poll_mode={} riova={riova:#x}\n", pm.as_str())
                                }
                            }
                        }
                        "PMU_START" => {
                            for w in 0..MAX_WORKERS {
                                pmu_taken0[w] = worker_rt[w].taken.load(Ordering::Relaxed);
                                pmu_empty0[w] = worker_rt[w].empty_polls.load(Ordering::Relaxed);
                                pmu_busy0[w] = worker_rt[w].busy_polls.load(Ordering::Relaxed);
                            }
                            *pmu_t0 = Some(Instant::now());
                            *pmu_s0 = Some(pmu.snap());
                            "OK\n".to_string()
                        }
                        "PMU_STOP" => {
                            let s1 = pmu.snap();
                            let elapsed_ns = pmu_t0.take().map(|t| t.elapsed().as_nanos() as u64).unwrap_or(0);
                            if let Some(s0) = pmu_s0.take() {
                                let (pmu_json, _) = pmu.format_delta_json(&s0, &s1);
                                let k = active_lanes.load(Ordering::Acquire);
                                let cpl = active_cpl.load(Ordering::Acquire);
                                let nw = k * cpl;
                                let mut w_json = Vec::with_capacity(nw);
                                for w in 0..nw {
                                    let td = worker_rt[w].taken.load(Ordering::Relaxed).saturating_sub(pmu_taken0[w]);
                                    let ed = worker_rt[w].empty_polls.load(Ordering::Relaxed).saturating_sub(pmu_empty0[w]);
                                    let bd = worker_rt[w].busy_polls.load(Ordering::Relaxed).saturating_sub(pmu_busy0[w]);
                                    w_json.push(format!(
                                        "{{\"worker\":{w},\"lane\":{},\"sub\":{},\"taken_win\":{td},\"empty_win\":{ed},\"busy_win\":{bd},\"corrupt\":{},\"partial\":{}}}",
                                        w / cpl, w % cpl,
                                        worker_rt[w].corrupt.load(Ordering::Relaxed),
                                        worker_rt[w].partial.load(Ordering::Relaxed),
                                    ));
                                }
                                format!(
                                    "{{\"elapsed_ns\":{elapsed_ns},\"workers\":[{}],{pmu_json}}}\n",
                                    w_json.join(",")
                                )
                            } else {
                                "ERR PMU_START not called\n".to_string()
                            }
                        }
                        "STATS" => {
                            let k = active_lanes.load(Ordering::Acquire);
                            let cpl = active_cpl.load(Ordering::Acquire);
                            let nw = k * cpl;
                            let mut parts_j = Vec::with_capacity(nw);
                            for w in 0..nw {
                                parts_j.push(format!(
                                    "{{\"worker\":{w},\"lane\":{},\"sub\":{},\"taken\":{},\"next\":{},\"corrupt\":{},\"partial\":{},\"empty\":{},\"busy\":{}}}",
                                    w / cpl, w % cpl,
                                    worker_rt[w].taken.load(Ordering::Acquire),
                                    worker_rt[w].next_seq.load(Ordering::Acquire),
                                    worker_rt[w].corrupt.load(Ordering::Acquire),
                                    worker_rt[w].partial.load(Ordering::Acquire),
                                    worker_rt[w].empty_polls.load(Ordering::Acquire),
                                    worker_rt[w].busy_polls.load(Ordering::Acquire),
                                ));
                            }
                            format!("{{\"workers\":[{}]}}\n", parts_j.join(","))
                        }
                        "PAUSE" => {
                            if (*cur_gen & 1) == 1 {
                                *cur_gen += 1;
                                run_gen.store(*cur_gen, Ordering::Release);
                                for w in 0..MAX_WORKERS {
                                    while worker_ready_gen[w].load(Ordering::Acquire) != *cur_gen {
                                        thread::sleep(Duration::from_micros(50));
                                    }
                                }
                            }
                            for w in 0..MAX_WORKERS {
                                *reply_targets[w].lock().unwrap() = None;
                            }
                            "OK\n".to_string()
                        }
                        _ => format!("ERR unknown cmd: {}\n", line.trim()),
                    };
                    let _ = writer.write_all(resp.as_bytes());
                    line.clear();
                }
            };

            while !quit.load(Ordering::Relaxed) {
                match rpc_listener.accept() {
                    Ok((stream, _)) => handle_conn(
                        stream,
                        &mut cur_gen,
                        &mut pmu_t0,
                        &mut pmu_s0,
                        &mut pmu_taken0,
                        &mut pmu_empty0,
                        &mut pmu_busy0,
                    ),
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                    }
                    Err(e) => return Err(e.into()),
                }
            }

            if (cur_gen & 1) == 1 {
                cur_gen += 1;
                run_gen.store(cur_gen, Ordering::Release);
                for w in 0..MAX_WORKERS {
                    while worker_ready_gen[w].load(Ordering::Acquire) != cur_gen {
                        thread::sleep(Duration::from_micros(50));
                    }
                }
            }
            ctrl_ready.store(0, Ordering::Release);
            lease_srv.close();
            Ok(())
        });

        drop(outbound);
        drop(window);
        res
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let args = Args::parse();
        if args.restore {
            let changed = restore_defaults(SLOT)?;
            for c in &changed {
                println!("mrv231_card: restored {c}");
            }
            if changed.is_empty() {
                println!("mrv231_card: BAR4 slot {SLOT} and outbound defaults already clean");
            }
            return Ok(());
        }
        match args.mode {
            Mode::ArmSnoop => run_arm_snoop(&args),
            Mode::Bar4Server => run_bar4_server(&args),
        }
    }
}
