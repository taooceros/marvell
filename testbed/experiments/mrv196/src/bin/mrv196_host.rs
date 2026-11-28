//! Host-side controlled memory-interference benchmark for MRV-196:
//! Measures whether high-bandwidth BAR4 MMIO writes (>= 150 Mpps, 1..4 cores)
//! degrade co-located Socket 1 memory latency (LLC-fit 16 MiB pointer chase,
//! DRAM 256 MiB pointer chase) or memory bandwidth (STREAM Copy / Triad),
//! compared against throughput-matched Socket 1 host-RAM cacheable ring and
//! non-temporal streaming writes, with 100% non-multiplexed SPR CHA TOR, IMC,
//! M2PCIe1, IRP1, IIO1, core, and CN106 TAD/DDR5 PMU accounting.
//!
//! Safety invariants:
//! - BAR4 slot 15 is ONLY accessed under an active MRV-26 lease (`Lease::acquire`
//!   + `LeasedSlot::check()` every batch), never raw-mapped.
//! - All victim and host-RAM aggressor buffers use 2 MiB hugepages strictly
//!   `mbind(MPOL_BIND)`-pinned to NUMA node 1 and verified via `get_mempolicy`.
//! - All host & card PMU counters are strictly 100% non-multiplexed.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv196_host runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::arch::asm;
    use std::arch::x86_64::{
        __m256i, _mm256_fmadd_pd, _mm256_load_pd, _mm256_load_si256, _mm256_set1_pd,
        _mm256_store_pd, _mm256_store_si256, _mm256_stream_si256, _mm_sfence,
    };
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::size_of;
    use std::net::TcpStream;
    use std::os::fd::RawFd;
    use std::ptr;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, AtomicUsize, Ordering};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Eal, Memzone};
    use testbed::lease::{self, Lease};
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};
    const BAR4_UC_PATH: &str = "/sys/bus/pci/devices/0000:99:00.0/resource4";
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const COORD_CPU: i32 = 36;
    const NUM_CHAS: usize = 32;
    const NUM_IMCS: usize = 8;
    const REF_HZ: f64 = 2.2e9;

    const HUGE_2M: usize = 2 << 20;
    const BAR4_LINES_PER_THREAD: usize = 4096; // 256 KiB per thread (<= 2 MiB for 8 threads)
    const MAX_BAR4_SINK_BYTES: usize = 3 << 20; // 3 MiB max (< READY_OFFSET in 4 MiB BAR4 slot 15)
    const LLC_CHASE_BYTES: usize = 16 << 20; // 16 MiB (fits inside 60 MiB L3, >> 2 MiB L2)
    const DRAM_CHASE_BYTES: usize = 256 << 20; // 256 MiB (>> 60 MiB L3)
    const STREAM_ELEMS: usize = 16 * 1024 * 1024; // 128 MiB per f64 array (384 MiB Triad)

    const CELL_MAGIC: u64 = 0xA55A_1960_0000_0000;
    const NODE_SIG_MAGIC: u64 = 0x5A5A_1960_0000_0000;

    const PHASE_WARMUP: u8 = 0;
    const PHASE_CALIB: u8 = 1;
    const PHASE_MEASURE: u8 = 2;
    const PHASE_STOP: u8 = 3;
    #[inline(always)]
    fn cell_sig(seq: u64, global_line: usize, run_tag: u64) -> u64 {
        seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ (global_line as u64) ^ run_tag ^ CELL_MAGIC
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum VictimKind {
        None,
        LlcChase,
        DramChase,
        StreamCopy,
        StreamTriad,
    }

    impl VictimKind {
        fn as_str(self) -> &'static str {
            match self {
                Self::None => "none",
                Self::LlcChase => "llc-chase",
                Self::DramChase => "dram-chase",
                Self::StreamCopy => "stream-copy",
                Self::StreamTriad => "stream-triad",
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum AggrMode {
        Idle,
        L1Spin,
        Bar4,
        RamWb,
        RamNt,
        DpiPull,
    }

    impl AggrMode {
        fn as_str(self) -> &'static str {
            match self {
                Self::Idle => "idle",
                Self::L1Spin => "l1-spin",
                Self::Bar4 => "bar4",
                Self::RamWb => "ram-wb",
                Self::RamNt => "ram-nt",
                Self::DpiPull => "dpi-pull",
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum ChaOccMode {
        Wcilf,
        All,
        MissDrd,
    }

    impl ChaOccMode {
        fn as_str(self) -> &'static str {
            match self {
                Self::Wcilf => "wcilf",
                Self::All => "all",
                Self::MissDrd => "miss-drd",
            }
        }
        fn config(self) -> u64 {
            match self {
                Self::Wcilf => CHA_TOR_OCC_IA_WCILF,
                Self::All => CHA_TOR_OCC_ALL,
                Self::MissDrd => CHA_TOR_OCC_IA_MISS_DRD,
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum)]
        victim: VictimKind,
        #[arg(long, value_enum)]
        aggr: AggrMode,
        /// Victim CPU list (comma-separated, e.g. "40" or "40,41,42,43,44,45").
        #[arg(long, default_value = "40")]
        victim_cpus: String,
        /// Aggressor CPU list (comma-separated, e.g. "52" or "52,53,54,55" or "104").
        #[arg(long, default_value = "52")]
        aggr_cpus: String,
        /// Aggregate target write rate in Mpps (64 B writes/s). 0 = unthrottled full speed.
        #[arg(long, default_value_t = 0.0)]
        target_mpps: f64,
        /// Measurement duration in ms.
        #[arg(long, default_value_t = 1000)]
        duration_ms: u64,
        /// Minimum warmup duration in ms (adaptive freq wait extends up to max-warmup-ms).
        #[arg(long, default_value_t = 400)]
        warmup_ms: u64,
        /// Maximum warmup duration waiting for schedutil cores to ramp.
        #[arg(long, default_value_t = 3500)]
        max_warmup_ms: u64,
        /// Target minimum GHz during adaptive warmup (0 disables freq check).
        #[arg(long, default_value_t = 3.80)]
        min_ghz: f64,
        /// Pointer-chase hops per rdtscp sample (0 = auto: 32 for LLC, 8 for DRAM).
        #[arg(long, default_value_t = 0)]
        chase_k: usize,
        /// CHA TOR occupancy event on counter 0.
        #[arg(long, value_enum, default_value_t = ChaOccMode::Wcilf)]
        cha_occ: ChaOccMode,
        /// Unique run tag embedded in every written 64 B cell for end-of-window verification.
        #[arg(long, default_value_t = 1)]
        run_tag: u64,
        /// BAR4 sysfs resource path.
        #[arg(long, default_value = BAR4_UC_PATH)]
        bar: String,
        /// MRV-26 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card: String,
        /// Card sync/PMU/verify TCP address.
        #[arg(long, default_value = "10.10.0.2:7527")]
        sync: String,
        /// Collect Card TAD/DDR5 PMU via sync server when aggr == bar4 or dpi-pull.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        with_card_pmu: bool,
        /// Per-thread host RAM ring size in MiB for ram-wb / ram-nt (power of 2, default 256).
        #[arg(long, default_value_t = 256)]
        ram_ring_mib: usize,
        /// Total host DPDK memzone ring size in MiB for dpi-pull (power of 2, e.g. 16 or 256).
        #[arg(long, default_value_t = 16)]
        dpi_ring_mib: usize,
        /// CN106 DPI FromHost chunk bytes (default 65536) and queue depth (default 16).
        #[arg(long, default_value_t = 65536)]
        dpi_chunk_bytes: u64,
        #[arg(long, default_value_t = 16)]
        dpi_depth: u64,
        /// BAR4 MOVDIR64B writes per SFENCE batch (unified across paced and unpaced BAR4 arms).
        #[arg(long, default_value_t = 512)]
        bar4_sfence_batch: usize,
        /// Host-RAM / DPI-ring writes per batch (unified across paced and unpaced RAM arms).
        #[arg(long, default_value_t = 128)]
        ram_batch: usize,
    }

    fn parse_cpus(s: &str) -> Result<Vec<i32>, Box<dyn Error>> {
        let mut out = Vec::new();
        for tok in s.split(',') {
            let t = tok.trim();
            if !t.is_empty() {
                out.push(t.parse::<i32>()?);
            }
        }
        Ok(out)
    }

    fn pin_to_cpu(cpu: i32) -> Result<(), Box<dyn Error>> {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu as usize, &mut set);
            if libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set) != 0 {
                return Err(io::Error::last_os_error().into());
            }
        }
        Ok(())
    }

    #[inline(always)]
    fn rdtsc_raw() -> u64 {
        let lo: u32;
        let hi: u32;
        unsafe {
            asm!(
                "rdtsc",
                out("eax") lo,
                out("edx") hi,
                options(nostack, preserves_flags, nomem)
            );
        }
        ((hi as u64) << 32) | (lo as u64)
    }

    #[inline(always)]
    fn rdtscp_lfence() -> u64 {
        let lo: u32;
        let hi: u32;
        unsafe {
            asm!(
                "rdtscp",
                "lfence",
                out("eax") lo,
                out("edx") hi,
                out("ecx") _,
                options(nostack, preserves_flags, nomem)
            );
        }
        ((hi as u64) << 32) | (lo as u64)
    }

    fn calibrate_tsc_hz() -> f64 {
        let t0 = Instant::now();
        let c0 = rdtscp_lfence();
        thread::sleep(Duration::from_millis(50));
        let c1 = rdtscp_lfence();
        let dt = t0.elapsed().as_secs_f64();
        (c1.saturating_sub(c0) as f64) / dt
    }

    #[inline(never)]
    fn calibrate_timer_floor_ticks() -> u64 {
        let mut samples = [0u64; 4096];
        for _ in 0..512 {
            let _ = rdtscp_lfence();
        }
        let mut prev = rdtscp_lfence();
        for s in &mut samples {
            let cur = rdtscp_lfence();
            *s = cur.saturating_sub(prev);
            prev = cur;
        }
        samples.sort_unstable();
        samples[samples.len() / 2]
    }

    fn process_page_faults() -> u64 {
        unsafe {
            let mut ru: libc::rusage = std::mem::zeroed();
            if libc::getrusage(libc::RUSAGE_SELF, &mut ru) == 0 {
                (ru.ru_minflt + ru.ru_majflt) as u64
            } else {
                0
            }
        }
    }

    fn thread_page_faults() -> u64 {
        unsafe {
            let mut ru: libc::rusage = std::mem::zeroed();
            if libc::getrusage(libc::RUSAGE_THREAD, &mut ru) == 0 {
                (ru.ru_minflt + ru.ru_majflt) as u64
            } else {
                0
            }
        }
    }

    // ---- NUMA Node 1 2 MiB hugepage allocator ----
    const MPOL_BIND: i32 = 2;
    const MPOL_MF_STRICT: u32 = 1 << 0;
    const MPOL_MF_MOVE: u32 = 1 << 1;
    const MPOL_F_NODE: u64 = 1 << 0;
    const MPOL_F_ADDR: u64 = 1 << 1;

    struct NumaHugeBuf {
        ptr: *mut u8,
        len: usize,
    }

    unsafe impl Send for NumaHugeBuf {}
    unsafe impl Sync for NumaHugeBuf {}

    impl NumaHugeBuf {
        fn alloc_node1(bytes: usize) -> Result<Self, Box<dyn Error>> {
            let len = bytes.div_ceil(HUGE_2M) * HUGE_2M;
            let ptr = unsafe {
                libc::mmap(
                    ptr::null_mut(),
                    len,
                    libc::PROT_READ | libc::PROT_WRITE,
                    libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_HUGETLB,
                    -1,
                    0,
                )
            };
            if ptr == libc::MAP_FAILED {
                return Err(format!(
                    "mmap(MAP_HUGETLB, {} MiB) failed: {}",
                    len >> 20,
                    io::Error::last_os_error()
                )
                .into());
            }
            let nodemask: [u64; 1] = [1u64 << 1]; // NUMA node 1
            let ret = unsafe {
                libc::syscall(
                    libc::SYS_mbind,
                    ptr,
                    len,
                    MPOL_BIND,
                    nodemask.as_ptr(),
                    64usize,
                    (MPOL_MF_STRICT | MPOL_MF_MOVE) as usize,
                )
            };
            if ret != 0 {
                let err = io::Error::last_os_error();
                unsafe { libc::munmap(ptr, len) };
                return Err(format!("mbind(node=1, {} MiB) failed: {err}", len >> 20).into());
            }
            let p = ptr.cast::<u8>();
            // Pre-fault every 2 MiB page cleanly on Node 1 before measurement
            let mut off = 0usize;
            while off < len {
                unsafe { p.add(off).write_volatile(0) };
                off += 4096;
            }
            // Verify physical NUMA residence at start, mid, end
            for &check_off in &[0usize, len / 2, len - 4096] {
                let mut node: i32 = -1;
                let r = unsafe {
                    libc::syscall(
                        libc::SYS_get_mempolicy,
                        &mut node as *mut i32,
                        ptr::null_mut::<u64>(),
                        0usize,
                        p.add(check_off),
                        (MPOL_F_NODE | MPOL_F_ADDR) as usize,
                    )
                };
                if r != 0 || node != 1 {
                    unsafe { libc::munmap(ptr, len) };
                    return Err(format!(
                        "NUMA verification failed at off={check_off}: ret={r} node={node}"
                    )
                    .into());
                }
            }
            Ok(Self { ptr: p, len })
        }

        fn as_ptr(&self) -> *mut u8 {
            self.ptr
        }
    }

    impl Drop for NumaHugeBuf {
        fn drop(&mut self) {
            unsafe {
                libc::munmap(self.ptr.cast::<libc::c_void>(), self.len);
            }
        }
    }

    // ---- Low-level 100% non-multiplexed SPR PMU helpers ----
    const PERF_TYPE_HARDWARE: u32 = 0;
    const PERF_TYPE_RAW: u32 = 4;
    const PERF_COUNT_HW_CPU_CYCLES: u64 = 0;
    const PERF_COUNT_HW_INSTRUCTIONS: u64 = 1;
    const PERF_COUNT_HW_REF_CPU_CYCLES: u64 = 9;
    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    const UNCORE_CLOCKTICKS: u64 = 0x01;

    const fn cha_cfg(event: u8, umask: u64) -> u64 {
        (event as u64) | ((umask & 0xff) << 8) | ((umask >> 8) << 32)
    }

    const fn iio_cfg(event: u8, umask: u8, ch_mask: u64, fc_mask: u64) -> u64 {
        (event as u64) | ((umask as u64) << 8) | (ch_mask << 36) | (fc_mask << 48)
    }

    // SPR CHA TOR encodings (verified via perf list --details)
    const CHA_TOR_OCC_IA_WCILF: u64 = cha_cfg(0x36, 0xc867ff01);
    const CHA_TOR_INS_IA_WCILF: u64 = cha_cfg(0x35, 0xc867ff01);
    const CHA_TOR_OCC_ALL: u64 = cha_cfg(0x36, 0xc001ffff);
    const CHA_TOR_INS_ALL: u64 = cha_cfg(0x35, 0xc001ffff);
    const CHA_TOR_OCC_IA_MISS_DRD: u64 = cha_cfg(0x36, 0xc817fe01);

    // SPR IMC encodings (all 8 channels on Socket 1)
    const IMC_CAS_COUNT_RD: u64 = 0xcf05; // event=0x05, umask=0xcf (64 B per CAS)
    const IMC_CAS_COUNT_WR: u64 = 0xf005; // event=0x05, umask=0xf0 (64 B per CAS)
    const IMC_RPQ_OCC_PCH0: u64 = 0x0080; // event=0x80

    // SPR M2PCIe1 encodings (4 counters)
    const M2P_RXC_INS_CHA_NCB: u64 = 0x0211;
    const M2P_RXC_NE_CHA_NCB: u64 = 0x0210;
    const M2P_CRD_REJECT_NCB: u64 = 0x1034; // unc_m2p_iio_credits_reject.ncb

    // SPR IRP1 encodings (strictly 2 counters)
    const IRP_TXS_REQ_OCC: u64 = 0x000c;
    const IRP_TXS_INS_NCS: u64 = 0x000e;

    // SPR IIO1 encodings (4 counters)
    const IIO_CPU_MEM_WR_TXN: u64 = iio_cfg(0xc1, 0x01, 0xff, 7);
    const IIO_CPU_MEM_WR_DW: u64 = iio_cfg(0xc0, 0x01, 0xff, 7);
    const IIO_DMA_RD_TXN: u64 = iio_cfg(0x84, 0x04, 0xff, 7);
    const IIO_DMA_RD_DW: u64 = iio_cfg(0x83, 0x04, 0xff, 7);
    const IIO_OUTBOUND_CL_TO_IO: u64 = iio_cfg(0xd0, 0x08, 0xff, 7);
    // SPR Core raw encodings (cpu/type=4)
    const CORE_LLC_REF: u64 = 0x4f2e; // longest_lat_cache.reference
    const CORE_LLC_MISS: u64 = 0x412e; // longest_lat_cache.miss
    const CORE_L3_HIT: u64 = 0x04d1; // mem_load_retired.l3_hit
    const CORE_L3_MISS: u64 = 0x20d1; // mem_load_retired.l3_miss
    const CORE_SB_STALLS: u64 = 0x08a2; // resource_stalls.sb

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
        fn delta(self, earlier: Self) -> Self {
            Self {
                val: self.val.saturating_sub(earlier.val),
                en: self.en.saturating_sub(earlier.en),
                run: self.run.saturating_sub(earlier.run),
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
            assert_eq!(n, size_of::<[u64; 3]>() as isize, "short read on perf fd");
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

    fn read_pmu_type(name: &str) -> Result<u32, Box<dyn Error>> {
        let p = format!("/sys/bus/event_source/devices/{name}/type");
        Ok(fs::read_to_string(&p)?.trim().parse()?)
    }

    struct CoreClockPmu {
        cpu: i32,
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        sb_stalls: Option<PerfCounter>,
    }

    #[derive(Clone, Copy, Default)]
    struct CoreClockSnap {
        cycles: Sample,
        ref_cycles: Sample,
        sb_stalls: Option<Sample>,
    }

    impl CoreClockPmu {
        fn open(cpu: i32, with_sb: bool) -> io::Result<Self> {
            Ok(Self {
                cpu,
                cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu)?,
                ref_cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, cpu)?,
                sb_stalls: if with_sb {
                    Some(PerfCounter::open(PERF_TYPE_RAW, CORE_SB_STALLS, cpu)?)
                } else {
                    None
                },
            })
        }
        fn snap(&self) -> CoreClockSnap {
            CoreClockSnap {
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                sb_stalls: self.sb_stalls.as_ref().map(|c| c.read()),
            }
        }
    }

    struct VictimPrimaryPmu {
        inst: PerfCounter,
        llc_ref: PerfCounter,
        llc_miss: PerfCounter,
        l3_hit: PerfCounter,
        l3_miss: PerfCounter,
    }

    struct VictimPrimarySnap {
        inst: Sample,
        llc_ref: Sample,
        llc_miss: Sample,
        l3_hit: Sample,
        l3_miss: Sample,
    }

    impl VictimPrimaryPmu {
        fn open(cpu: i32) -> io::Result<Self> {
            Ok(Self {
                inst: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu)?,
                llc_ref: PerfCounter::open(PERF_TYPE_RAW, CORE_LLC_REF, cpu)?,
                llc_miss: PerfCounter::open(PERF_TYPE_RAW, CORE_LLC_MISS, cpu)?,
                l3_hit: PerfCounter::open(PERF_TYPE_RAW, CORE_L3_HIT, cpu)?,
                l3_miss: PerfCounter::open(PERF_TYPE_RAW, CORE_L3_MISS, cpu)?,
            })
        }
        fn snap(&self) -> VictimPrimarySnap {
            VictimPrimarySnap {
                inst: self.inst.read(),
                llc_ref: self.llc_ref.read(),
                llc_miss: self.llc_miss.read(),
                l3_hit: self.l3_hit.read(),
                l3_miss: self.l3_miss.read(),
            }
        }
    }

    struct HostPmu {
        cha_occ: Vec<PerfCounter>,
        cha_ins_wcilf: Vec<PerfCounter>,
        cha_ins_all: Vec<PerfCounter>,
        cha_clk: PerfCounter,
        imc_rd: Vec<PerfCounter>,
        imc_wr: Vec<PerfCounter>,
        imc_rpq: Vec<PerfCounter>,
        imc_clk: PerfCounter,
        m2p_ins_ncb: PerfCounter,
        m2p_ne_ncb: PerfCounter,
        m2p_crd_rej: PerfCounter,
        m2p_clk: PerfCounter,
        irp_occ: PerfCounter,
        irp_ins_ncs: PerfCounter,
        iio_wr_txn: PerfCounter,
        iio_wr_dw: PerfCounter,
        iio_out_cl: PerfCounter,
        iio_clk: PerfCounter,
        victim_clks: Vec<CoreClockPmu>,
        victim_prim: Option<VictimPrimaryPmu>,
        aggr_clks: Vec<CoreClockPmu>,
    }

    const MAX_TRACKED_CORES: usize = 16;

    struct HostPmuSnap {
        wall: Instant,
        cha_occ: [Sample; NUM_CHAS],
        cha_ins_wcilf: [Sample; NUM_CHAS],
        cha_ins_all: [Sample; NUM_CHAS],
        cha_clk: Sample,
        imc_rd: [Sample; NUM_IMCS],
        imc_wr: [Sample; NUM_IMCS],
        imc_rpq: [Sample; NUM_IMCS],
        imc_clk: Sample,
        m2p_ins_ncb: Sample,
        m2p_ne_ncb: Sample,
        m2p_crd_rej: Sample,
        m2p_clk: Sample,
        irp_occ: Sample,
        irp_ins_ncs: Sample,
        iio_wr_txn: Sample,
        iio_wr_dw: Sample,
        iio_out_cl: Sample,
        iio_clk: Sample,
        n_victim_clks: usize,
        victim_clks: [CoreClockSnap; MAX_TRACKED_CORES],
        victim_prim: Option<VictimPrimarySnap>,
        n_aggr_clks: usize,
        aggr_clks: [CoreClockSnap; MAX_TRACKED_CORES],
    }

    impl HostPmu {
        fn open(
            cha_occ_cfg: u64,
            iio_inbound_rd: bool,
            victim_cpus: &[i32],
            aggr_cpus: &[i32],
        ) -> Result<Self, Box<dyn Error>> {
            let mut cha_occ = Vec::with_capacity(NUM_CHAS);
            let mut cha_ins_wcilf = Vec::with_capacity(NUM_CHAS);
            let mut cha_ins_all = Vec::with_capacity(NUM_CHAS);
            for i in 0..NUM_CHAS {
                let t = read_pmu_type(&format!("uncore_cha_{i}"))?;
                cha_occ.push(PerfCounter::open(t, cha_occ_cfg, SOCKET1_UNCORE_CPU)?);
                cha_ins_wcilf.push(PerfCounter::open(t, CHA_TOR_INS_IA_WCILF, SOCKET1_UNCORE_CPU)?);
                cha_ins_all.push(PerfCounter::open(t, CHA_TOR_INS_ALL, SOCKET1_UNCORE_CPU)?);
            }
            let cha0_t = read_pmu_type("uncore_cha_0")?;
            let cha_clk = PerfCounter::open(cha0_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;

            let mut imc_rd = Vec::with_capacity(NUM_IMCS);
            let mut imc_wr = Vec::with_capacity(NUM_IMCS);
            let mut imc_rpq = Vec::with_capacity(NUM_IMCS);
            for i in 0..NUM_IMCS {
                let t = read_pmu_type(&format!("uncore_imc_{i}"))?;
                imc_rd.push(PerfCounter::open(t, IMC_CAS_COUNT_RD, SOCKET1_UNCORE_CPU)?);
                imc_wr.push(PerfCounter::open(t, IMC_CAS_COUNT_WR, SOCKET1_UNCORE_CPU)?);
                imc_rpq.push(PerfCounter::open(t, IMC_RPQ_OCC_PCH0, SOCKET1_UNCORE_CPU)?);
            }
            let imc0_t = read_pmu_type("uncore_imc_0")?;
            let imc_clk = PerfCounter::open(imc0_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;

            let m2p_t = read_pmu_type("uncore_m2pcie_1")?;
            let m2p_ins_ncb = PerfCounter::open(m2p_t, M2P_RXC_INS_CHA_NCB, SOCKET1_UNCORE_CPU)?;
            let m2p_ne_ncb = PerfCounter::open(m2p_t, M2P_RXC_NE_CHA_NCB, SOCKET1_UNCORE_CPU)?;
            let m2p_crd_rej = PerfCounter::open(m2p_t, M2P_CRD_REJECT_NCB, SOCKET1_UNCORE_CPU)?;
            let m2p_clk = PerfCounter::open(m2p_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;

            let irp_t = read_pmu_type("uncore_irp_1")?;
            let irp_occ = PerfCounter::open(irp_t, IRP_TXS_REQ_OCC, SOCKET1_UNCORE_CPU)?;
            let irp_ins_ncs = PerfCounter::open(irp_t, IRP_TXS_INS_NCS, SOCKET1_UNCORE_CPU)?;

            let iio_t = read_pmu_type("uncore_iio_1")?;
            let (iio_txn_cfg, iio_dw_cfg) = if iio_inbound_rd {
                (IIO_DMA_RD_TXN, IIO_DMA_RD_DW)
            } else {
                (IIO_CPU_MEM_WR_TXN, IIO_CPU_MEM_WR_DW)
            };
            let iio_wr_txn = PerfCounter::open(iio_t, iio_txn_cfg, SOCKET1_UNCORE_CPU)?;
            let iio_wr_dw = PerfCounter::open(iio_t, iio_dw_cfg, SOCKET1_UNCORE_CPU)?;
            let iio_out_cl = PerfCounter::open(iio_t, IIO_OUTBOUND_CL_TO_IO, SOCKET1_UNCORE_CPU)?;
            let iio_clk = PerfCounter::open(iio_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;

            let mut victim_clks = Vec::with_capacity(victim_cpus.len());
            for &c in victim_cpus {
                victim_clks.push(CoreClockPmu::open(c, false)?);
            }
            let victim_prim = if let Some(&c0) = victim_cpus.first() {
                Some(VictimPrimaryPmu::open(c0)?)
            } else {
                None
            };
            let mut aggr_clks = Vec::with_capacity(aggr_cpus.len());
            for &c in aggr_cpus {
                aggr_clks.push(CoreClockPmu::open(c, true)?);
            }

            Ok(Self {
                cha_occ,
                cha_ins_wcilf,
                cha_ins_all,
                cha_clk,
                imc_rd,
                imc_wr,
                imc_rpq,
                imc_clk,
                m2p_ins_ncb,
                m2p_ne_ncb,
                m2p_crd_rej,
                m2p_clk,
                irp_occ,
                irp_ins_ncs,
                iio_wr_txn,
                iio_wr_dw,
                iio_out_cl,
                iio_clk,
                victim_clks,
                victim_prim,
                aggr_clks,
            })
        }

        fn snap(&self) -> HostPmuSnap {
            let mut cha_occ = [Sample::default(); NUM_CHAS];
            let mut cha_ins_wcilf = [Sample::default(); NUM_CHAS];
            let mut cha_ins_all = [Sample::default(); NUM_CHAS];
            for i in 0..NUM_CHAS {
                cha_occ[i] = self.cha_occ[i].read();
                cha_ins_wcilf[i] = self.cha_ins_wcilf[i].read();
                cha_ins_all[i] = self.cha_ins_all[i].read();
            }
            let mut imc_rd = [Sample::default(); NUM_IMCS];
            let mut imc_wr = [Sample::default(); NUM_IMCS];
            let mut imc_rpq = [Sample::default(); NUM_IMCS];
            for i in 0..NUM_IMCS {
                imc_rd[i] = self.imc_rd[i].read();
                imc_wr[i] = self.imc_wr[i].read();
                imc_rpq[i] = self.imc_rpq[i].read();
            }
            let mut victim_clks = [CoreClockSnap::default(); MAX_TRACKED_CORES];
            let n_v = self.victim_clks.len().min(MAX_TRACKED_CORES);
            for i in 0..n_v {
                victim_clks[i] = self.victim_clks[i].snap();
            }
            let mut aggr_clks = [CoreClockSnap::default(); MAX_TRACKED_CORES];
            let n_a = self.aggr_clks.len().min(MAX_TRACKED_CORES);
            for i in 0..n_a {
                aggr_clks[i] = self.aggr_clks[i].snap();
            }
            HostPmuSnap {
                wall: Instant::now(),
                cha_occ,
                cha_ins_wcilf,
                cha_ins_all,
                cha_clk: self.cha_clk.read(),
                imc_rd,
                imc_wr,
                imc_rpq,
                imc_clk: self.imc_clk.read(),
                m2p_ins_ncb: self.m2p_ins_ncb.read(),
                m2p_ne_ncb: self.m2p_ne_ncb.read(),
                m2p_crd_rej: self.m2p_crd_rej.read(),
                m2p_clk: self.m2p_clk.read(),
                irp_occ: self.irp_occ.read(),
                irp_ins_ncs: self.irp_ins_ncs.read(),
                iio_wr_txn: self.iio_wr_txn.read(),
                iio_wr_dw: self.iio_wr_dw.read(),
                iio_out_cl: self.iio_out_cl.read(),
                iio_clk: self.iio_clk.read(),
                n_victim_clks: n_v,
                victim_clks,
                victim_prim: self.victim_prim.as_ref().map(|c| c.snap()),
                n_aggr_clks: n_a,
                aggr_clks,
            }
        }

        fn measure_active_min_ghz(&self, window_ms: u64) -> (f64, Vec<(i32, f64)>) {
            let v0: Vec<CoreClockSnap> = self.victim_clks.iter().map(|c| c.snap()).collect();
            let a0: Vec<CoreClockSnap> = self.aggr_clks.iter().map(|c| c.snap()).collect();
            thread::sleep(Duration::from_millis(window_ms));
            let mut min_ghz = f64::INFINITY;
            let mut detail = Vec::new();
            for (pmu, s0) in self.victim_clks.iter().zip(v0.iter()) {
                let s1 = pmu.snap();
                let dc = s1.cycles.delta(s0.cycles).val;
                let dr = s1.ref_cycles.delta(s0.ref_cycles).val;
                let ghz = if dr > 0 { (dc as f64 / dr as f64) * (REF_HZ / 1e9) } else { 0.0 };
                min_ghz = min_ghz.min(ghz);
                detail.push((pmu.cpu, ghz));
            }
            for (pmu, s0) in self.aggr_clks.iter().zip(a0.iter()) {
                let s1 = pmu.snap();
                let dc = s1.cycles.delta(s0.cycles).val;
                let dr = s1.ref_cycles.delta(s0.ref_cycles).val;
                let ghz = if dr > 0 { (dc as f64 / dr as f64) * (REF_HZ / 1e9) } else { 0.0 };
                min_ghz = min_ghz.min(ghz);
                detail.push((pmu.cpu, ghz));
            }
            if min_ghz.is_infinite() {
                min_ghz = 0.0;
            }
            (min_ghz, detail)
        }
    }

    // ---- Card sync TCP client ----
    struct SyncClient {
        stream: TcpStream,
        reader: BufReader<TcpStream>,
        buf: String,
    }

    impl SyncClient {
        fn connect(addr: &str) -> Result<Self, Box<dyn Error>> {
            let stream = TcpStream::connect(addr)?;
            stream.set_nodelay(true)?;
            stream.set_read_timeout(Some(Duration::from_secs(10)))?;
            let reader = BufReader::new(stream.try_clone()?);
            let mut buf = String::with_capacity(4096);
            buf.push_str("init");
            buf.clear();
            Ok(Self { stream, reader, buf })
        }

        fn cmd(&mut self, line: &str) -> Result<String, Box<dyn Error>> {
            writeln!(self.stream, "{line}")?;
            self.stream.flush()?;
            self.buf.clear();
            let n = self.reader.read_line(&mut self.buf)?;
            if n == 0 {
                return Err("card sync closed".into());
            }
            Ok(self.buf.trim().to_string())
        }
    }

    /// Unbounded IOMMU-safe card DPI drain gate: keeps the process (and therefore the
    /// VFIO container / DPDK Memzone IOVA mapping) alive until card `DPI_STOP` confirms
    /// `"ok":true` (`running == false && drain_inflight == 0`), reconnecting on TCP drops.
    fn drain_dpi_until_ok(
        sync_client: &mut Option<SyncClient>,
        sync_addr: &str,
        allow_not_enabled: bool,
    ) -> String {
        let drain_wait_start = Instant::now();
        let mut last_log: Option<Instant> = None;
        let mut attempt: u64 = 0;
        loop {
            attempt += 1;
            let should_log = last_log.map_or(true, |t| t.elapsed() >= Duration::from_secs(2));
            if sync_client.is_none() {
                match SyncClient::connect(sync_addr) {
                    Ok(sc) => *sync_client = Some(sc),
                    Err(e) => {
                        if should_log {
                            eprintln!(
                                "mrv196_host: waiting to reconnect {sync_addr} for DPI_STOP (attempt {attempt}, elapsed {:.1}s, holding VFIO/Memzone mapped): {e}",
                                drain_wait_start.elapsed().as_secs_f64()
                            );
                            last_log = Some(Instant::now());
                        }
                        thread::sleep(Duration::from_millis(100));
                        continue;
                    }
                }
            }
            match sync_client.as_mut().unwrap().cmd("DPI_STOP") {
                Ok(resp) => {
                    if resp.contains("\"ok\":true")
                        || (allow_not_enabled && resp.contains("\"dpi_not_enabled\""))
                    {
                        return resp;
                    }
                    if should_log {
                        eprintln!(
                            "mrv196_host: DPI_STOP not yet drained (attempt {attempt}, elapsed {:.1}s, holding VFIO/Memzone mapped): {resp}",
                            drain_wait_start.elapsed().as_secs_f64()
                        );
                        last_log = Some(Instant::now());
                    }
                }
                Err(e) => {
                    *sync_client = None;
                    if should_log {
                        eprintln!(
                            "mrv196_host: DPI_STOP transport error (attempt {attempt}, elapsed {:.1}s, holding VFIO/Memzone mapped): {e}",
                            drain_wait_start.elapsed().as_secs_f64()
                        );
                        last_log = Some(Instant::now());
                    }
                }
            }
            thread::sleep(Duration::from_millis(100));
        }
    }

    // ---- Cache-line-padded atomic counters ----
    #[repr(C, align(64))]
    struct PaddedAtomicU64 {
        v: AtomicU64,
        _pad: [u8; 56],
    }

    impl PaddedAtomicU64 {
        fn new(val: u64) -> Self {
            Self { v: AtomicU64::new(val), _pad: [0; 56] }
        }
    }

    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct AlignedCell {
        w: [u64; 8],
    }

    // ---- Pointer-chase victim (16 MiB LLC or 256 MiB DRAM) ----
    #[derive(Default)]
    struct ChaseResult {
        total_hops: u64,
        total_chunks: u64,
        total_ns: f64,
        raw_mean_ns: f64,
        net_mean_ns: f64,
        p50_ns: f64,
        p90_ns: f64,
        p99_ns: f64,
        p999_ns: f64,
        p99_overflow: bool,
        p999_overflow: bool,
        max_ns: f64,
        bin_step_ns: f64,
        fine_cap_ns: f64,
        coarse_cap_ns: f64,
        overflow_chunks: u64,
        bad_nodes: u64,
        xor_acc: u64,
        k_hops: usize,
        buf_mib: usize,
        timer_floor_ns: f64,
        victim_page_faults: u64,
    }

    fn build_hamiltonian_cycle(buf: &NumaHugeBuf, bytes: usize, seed: u64) -> usize {
        let n = bytes / 64;
        // Sattolo's algorithm: uniform random single cycle of length `n`
        let mut perm: Vec<usize> = (0..n).collect();
        let mut rng = seed | 1;
        for i in (1..n).rev() {
            rng ^= rng << 13;
            rng ^= rng >> 7;
            rng ^= rng << 17;
            let j = (rng as usize) % i; // strictly < i -> single cycle!
            perm.swap(i, j);
        }
        let base = buf.as_ptr();
        for idx in 0..n {
            let next_idx = perm[idx];
            let node_ptr = unsafe { base.add(idx * 64).cast::<u64>() };
            let next_ptr = unsafe { base.add(next_idx * 64) as u64 };
            let sig = (idx as u64) ^ NODE_SIG_MAGIC;
            unsafe {
                node_ptr.write(next_ptr);
                node_ptr.add(1).write(sig);
            }
        }
        n
    }

    fn verify_hamiltonian_cycle(buf: &NumaHugeBuf, n: usize) -> u64 {
        let base = buf.as_ptr() as u64;
        let mut cur = base;
        let mut bad = 0u64;
        for _ in 0..n {
            let off = cur.wrapping_sub(base);
            if off >= (n as u64) * 64 || (off & 63) != 0 {
                return n as u64;
            }
            let idx = (off >> 6) as u64;
            let p = cur as *const u64;
            let next = unsafe { p.read() };
            let sig = unsafe { p.add(1).read() };
            if sig != (idx ^ NODE_SIG_MAGIC) {
                bad += 1;
            }
            cur = next;
        }
        if cur != base {
            bad += 1;
        }
        bad
    }

    #[inline(always)]
    unsafe fn chase_8(mut p: *const u64) -> *const u64 {
        unsafe {
            p = *(p as *const *const u64);
            p = *(p as *const *const u64);
            p = *(p as *const *const u64);
            p = *(p as *const *const u64);
            p = *(p as *const *const u64);
            p = *(p as *const *const u64);
            p = *(p as *const *const u64);
            p = *(p as *const *const u64);
        }
        p
    }

    fn run_chase_victim(
        cpu: i32,
        buf: &NumaHugeBuf,
        bytes: usize,
        k_hops: usize,
        phase: &AtomicU8,
        ready_count: &AtomicUsize,
        calib_done: &AtomicBool,
        tsc_hz: f64,
    ) -> Result<ChaseResult, Box<dyn Error>> {
        pin_to_cpu(cpu)?;
        let n = bytes / 64;
        let mut p = buf.as_ptr() as *const u64;
        // Pre-allocate and fault fine+coarse histograms + timer-calibration stack BEFORE warmup
        const FINE_BINS: usize = 4096;
        const COARSE_BINS: usize = 1024;
        let mut hist = [0u32; FINE_BINS];
        let mut tail_hist = [0u32; COARSE_BINS];
        hist.fill(1);
        tail_hist.fill(1);
        std::hint::black_box(&mut hist);
        std::hint::black_box(&mut tail_hist);
        hist.fill(0);
        tail_hist.fill(0);
        let _ = std::hint::black_box(calibrate_timer_floor_ticks());
        let _ = thread_page_faults();
        ready_count.fetch_add(1, Ordering::Release);

        // Warmup traversal (ensure 16 MiB fully resident in LLC, TLB warm, core hot at 3.9 GHz)
        while phase.load(Ordering::Acquire) == PHASE_WARMUP {
            for _ in 0..64 {
                p = unsafe { chase_8(p) };
            }
        }
        // Calibrate timer floor at the end of WARMUP (PHASE_CALIB, strictly BEFORE PHASE_MEASURE)
        let floor_ticks: u64;
        if phase.load(Ordering::Acquire) == PHASE_CALIB {
            floor_ticks = calibrate_timer_floor_ticks();
            calib_done.store(true, Ordering::Release);
            while phase.load(Ordering::Acquire) == PHASE_CALIB {
                for _ in 0..16 {
                    p = unsafe { chase_8(p) };
                }
            }
        } else {
            floor_ticks = calibrate_timer_floor_ticks();
            calib_done.store(true, Ordering::Release);
        }
        let timer_floor_ns = (floor_ticks as f64) * 1e9 / tsc_hz;
        let mut overflow = 0u64;
        let mut total_chunks = 0u64;
        let mut total_ticks = 0u64;
        let mut max_ticks = 0u64;
        let mut xor_acc = 0u64;

        // Fine bins: 0.25 ns for LLC (0..1024 ns), 1.0 ns for DRAM (0..4096 ns)
        // Coarse tail bins: 16.0 ns step above fine_cap (1024 bins -> +16384 ns)
        let bin_step_ns: f64 = if bytes > (64 << 20) { 1.0 } else { 0.25 };
        let coarse_step_ns: f64 = 16.0;
        let fine_cap_ns = (FINE_BINS as f64) * bin_step_ns;
        let coarse_cap_ns = fine_cap_ns + (COARSE_BINS as f64) * coarse_step_ns;
        let fine_bins_per_tick = (1e9 / bin_step_ns) / (tsc_hz * k_hops as f64);
        let qns_mul = (fine_bins_per_tick * ((1u64 << 32) as f64)) as u64;
        let coarse_ratio = (coarse_step_ns / bin_step_ns).round() as usize; // 64 for LLC, 16 for DRAM
        let sub_8 = k_hops / 8;
        let rem = k_hops % 8;

        let v_pf0 = thread_page_faults();
        let mut prev = rdtscp_lfence();
        let mut batch_ctr = 0u32;
        while phase.load(Ordering::Acquire) == PHASE_MEASURE {
            for _ in 0..sub_8 {
                p = unsafe { chase_8(p) };
            }
            for _ in 0..rem {
                p = unsafe { *(p as *const *const u64) };
            }
            let cur = rdtscp_lfence();
            let dt = cur.saturating_sub(prev);
            prev = cur;

            xor_acc ^= p as usize as u64;
            total_chunks += 1;
            total_ticks += dt;
            if dt > max_ticks {
                max_ticks = dt;
            }
            let net_dt = dt.saturating_sub(floor_ticks);
            let bin = (((net_dt as u128) * (qns_mul as u128)) >> 32) as usize;
            if bin < FINE_BINS {
                hist[bin] = hist[bin].saturating_add(1);
            } else {
                let cbin = (bin - FINE_BINS) / coarse_ratio;
                if cbin < COARSE_BINS {
                    tail_hist[cbin] = tail_hist[cbin].saturating_add(1);
                } else {
                    overflow += 1;
                }
            }

            batch_ctr = batch_ctr.wrapping_add(1);
            if (batch_ctr & 0xff) != 0 {
                continue;
            }
        }
        let v_pf1 = thread_page_faults();

        let total_hops = total_chunks * (k_hops as u64);
        let total_ns = (total_ticks as f64) * 1e9 / tsc_hz;
        let floor_ns_total = (total_chunks as f64) * (floor_ticks as f64) * 1e9 / tsc_hz;
        let raw_mean_ns = if total_hops > 0 { total_ns / total_hops as f64 } else { 0.0 };
        let net_mean_ns = if total_hops > 0 {
            (total_ns - floor_ns_total).max(0.0) / total_hops as f64
        } else {
            0.0
        };
        let pct_from_hist = |q: f64| -> (f64, bool) {
            if total_chunks == 0 {
                return (0.0, false);
            }
            let target = ((total_chunks as f64) * q).ceil() as u64;
            let mut acc = 0u64;
            for (i, &c) in hist.iter().enumerate() {
                acc += c as u64;
                if acc >= target {
                    return ((i as f64) * bin_step_ns, false);
                }
            }
            for (i, &c) in tail_hist.iter().enumerate() {
                acc += c as u64;
                if acc >= target {
                    return (fine_cap_ns + (i as f64) * coarse_step_ns, false);
                }
            }
            (coarse_cap_ns, true)
        };

        let (p50_ns, _) = pct_from_hist(0.50);
        let (p90_ns, _) = pct_from_hist(0.90);
        let (p99_ns, p99_overflow) = pct_from_hist(0.99);
        let (p999_ns, p999_overflow) = pct_from_hist(0.999);
        let max_ns =
            (max_ticks.saturating_sub(floor_ticks) as f64) * 1e9 / (tsc_hz * k_hops as f64);
        let bad_nodes = verify_hamiltonian_cycle(buf, n);

        Ok(ChaseResult {
            total_hops,
            total_chunks,
            total_ns,
            raw_mean_ns,
            net_mean_ns,
            p50_ns,
            p90_ns,
            p99_ns,
            p999_ns,
            p99_overflow,
            p999_overflow,
            max_ns,
            bin_step_ns,
            fine_cap_ns,
            coarse_cap_ns,
            overflow_chunks: overflow,
            bad_nodes,
            xor_acc,
            k_hops,
            buf_mib: bytes >> 20,
            timer_floor_ns,
            victim_page_faults: v_pf1.saturating_sub(v_pf0),
        })
    }

    // ---- STREAM Copy / Triad victim (AVX2 cacheable, multi-thread spin barrier) ----
    struct SpinBarrier {
        n_threads: usize,
        arrived: AtomicUsize,
        epoch: AtomicU64,
        phase_snap: AtomicU8,
    }

    impl SpinBarrier {
        fn new(n_threads: usize) -> Self {
            Self {
                n_threads,
                arrived: AtomicUsize::new(0),
                epoch: AtomicU64::new(0),
                phase_snap: AtomicU8::new(PHASE_WARMUP),
            }
        }
        #[inline(always)]
        fn wait(&self) {
            if self.n_threads <= 1 {
                return;
            }
            let g = self.epoch.load(Ordering::Acquire);
            if self.arrived.fetch_add(1, Ordering::AcqRel) + 1 == self.n_threads {
                self.arrived.store(0, Ordering::Relaxed);
                self.epoch.store(g + 1, Ordering::Release);
            } else {
                while self.epoch.load(Ordering::Acquire) == g {
                    std::hint::spin_loop();
                }
            }
        }
        /// Synchronized barrier that samples `phase` once on the last arriving thread
        /// and broadcasts the exact same value to all threads (eliminates phase exit race).
        #[inline(always)]
        fn wait_phase(&self, phase: &AtomicU8) -> u8 {
            if self.n_threads <= 1 {
                return phase.load(Ordering::Acquire);
            }
            let g = self.epoch.load(Ordering::Acquire);
            if self.arrived.fetch_add(1, Ordering::AcqRel) + 1 == self.n_threads {
                let p = phase.load(Ordering::Acquire);
                self.phase_snap.store(p, Ordering::Relaxed);
                self.arrived.store(0, Ordering::Relaxed);
                self.epoch.store(g + 1, Ordering::Release);
                p
            } else {
                while self.epoch.load(Ordering::Acquire) == g {
                    std::hint::spin_loop();
                }
                self.phase_snap.load(Ordering::Acquire)
            }
        }
    }

    #[target_feature(enable = "avx2")]
    unsafe fn stream_copy_avx2(dst: *mut f64, src: *const f64, n: usize) {
        let mut i = 0usize;
        while i + 16 <= n {
            unsafe {
                let v0 = _mm256_load_pd(src.add(i));
                let v1 = _mm256_load_pd(src.add(i + 4));
                let v2 = _mm256_load_pd(src.add(i + 8));
                let v3 = _mm256_load_pd(src.add(i + 12));
                _mm256_store_pd(dst.add(i), v0);
                _mm256_store_pd(dst.add(i + 4), v1);
                _mm256_store_pd(dst.add(i + 8), v2);
                _mm256_store_pd(dst.add(i + 12), v3);
            }
            i += 16;
        }
    }

    #[target_feature(enable = "avx2,fma")]
    unsafe fn stream_triad_avx2(a: *mut f64, b: *const f64, c: *const f64, scalar: f64, n: usize) {
        let vs = _mm256_set1_pd(scalar);
        let mut i = 0usize;
        while i + 16 <= n {
            unsafe {
                let vb0 = _mm256_load_pd(b.add(i));
                let vc0 = _mm256_load_pd(c.add(i));
                let vb1 = _mm256_load_pd(b.add(i + 4));
                let vc1 = _mm256_load_pd(c.add(i + 4));
                let vb2 = _mm256_load_pd(b.add(i + 8));
                let vc2 = _mm256_load_pd(c.add(i + 8));
                let vb3 = _mm256_load_pd(b.add(i + 12));
                let vc3 = _mm256_load_pd(c.add(i + 12));
                _mm256_store_pd(a.add(i), _mm256_fmadd_pd(vs, vc0, vb0));
                _mm256_store_pd(a.add(i + 4), _mm256_fmadd_pd(vs, vc1, vb1));
                _mm256_store_pd(a.add(i + 8), _mm256_fmadd_pd(vs, vc2, vb2));
                _mm256_store_pd(a.add(i + 12), _mm256_fmadd_pd(vs, vc3, vb3));
            }
            i += 16;
        }
    }

    #[derive(Default)]
    struct StreamResult {
        passes: u64,
        total_ns: f64,
        mean_gb_s: f64,
        median_pass_gb_s: f64,
        p10_pass_gb_s: f64,
        min_pass_gb_s: f64,
        max_pass_gb_s: f64,
        bytes_per_pass: u64,
        verify_errors: u64,
        victim_page_faults: u64,
    }
    // ---- Aggressor loops ----
    #[target_feature(enable = "avx2")]
    unsafe fn write_64b_wb(dst: *mut u8, src: &AlignedCell) {
        unsafe {
            let p = src as *const AlignedCell as *const __m256i;
            let v0 = _mm256_load_si256(p);
            let v1 = _mm256_load_si256(p.add(1));
            let d = dst as *mut __m256i;
            _mm256_store_si256(d, v0);
            _mm256_store_si256(d.add(1), v1);
        }
    }

    #[target_feature(enable = "avx2")]
    unsafe fn write_64b_nt(dst: *mut u8, src: &AlignedCell) {
        unsafe {
            let p = src as *const AlignedCell as *const __m256i;
            let v0 = _mm256_load_si256(p);
            let v1 = _mm256_load_si256(p.add(1));
            let d = dst as *mut __m256i;
            _mm256_stream_si256(d, v0);
            _mm256_stream_si256(d.add(1), v1);
        }
    }

    fn run_aggr_bar4(
        tid: usize,
        n_aggr: usize,
        cpu: i32,
        lease: &Lease,
        bar_path: &str,
        run_tag: u64,
        per_thread_mpps: f64,
        sfence_batch: usize,
        tsc_hz: f64,
        phase: &AtomicU8,
        ready_count: &AtomicUsize,
        writes_out: &PaddedAtomicU64,
        pf_out: &PaddedAtomicU64,
    ) -> Result<(), String> {
        pin_to_cpu(cpu).map_err(|e| format!("pin cpu{cpu}: {e}"))?;
        let slot = lease.map_slot(bar_path).map_err(|e| format!("map_slot: {e}"))?;
        let base_line = tid * BAR4_LINES_PER_THREAD;
        let base_ptr = unsafe { slot.as_ptr().add(base_line * 64) };
        let mask = BAR4_LINES_PER_THREAD - 1;
        let batch: usize = sfence_batch.max(1);
        let pace_ticks: u64 = if per_thread_mpps > 0.0 {
            ((batch as f64) * tsc_hz / (per_thread_mpps * 1e6)).round().max(1.0) as u64
        } else {
            0
        };

        let mut cell = AlignedCell { w: [0, 0, run_tag, 0x1960_3333, 0x1960_4444, 0x1960_5555, 0x1960_6666, 0] };
        let tag_magic = run_tag ^ CELL_MAGIC;
        let mut seq = 1u64;
        let mut idx = 0usize;
        let stagger = if pace_ticks > 0 && n_aggr > 1 {
            (pace_ticks.saturating_mul(tid as u64)) / (n_aggr as u64)
        } else {
            0
        };
        let mut next_tsc = rdtsc_raw().wrapping_add(stagger);
        let _ = thread_page_faults(); // touch rusage stack frame before warmup
        ready_count.fetch_add(1, Ordering::Release);

        let mut in_measure = false;
        let mut pf0 = 0u64;
        loop {
            let ph = phase.load(Ordering::Acquire);
            if ph == PHASE_STOP {
                break;
            }
            if !in_measure && ph == PHASE_MEASURE {
                pf0 = thread_page_faults();
                in_measure = true;
            }
            if pace_ticks > 0 {
                let now = rdtsc_raw();
                if now < next_tsc {
                    std::hint::spin_loop();
                    continue;
                }
                next_tsc = next_tsc.wrapping_add(pace_ticks);
                if now.saturating_sub(next_tsc) > pace_ticks.saturating_mul(64) {
                    next_tsc = now.wrapping_add(pace_ticks);
                }
            }
            slot.check()?;
            for _ in 0..batch {
                let l = idx & mask;
                let g_line = (base_line + l) as u64;
                let sig = seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ g_line ^ tag_magic;
                cell.w[0] = seq;
                cell.w[1] = sig;
                cell.w[7] = seq;
                unsafe { movdir64b(base_ptr.add(l << 6), &cell) };
                seq += 1;
                idx = idx.wrapping_add(1);
            }
            sfence();
            writes_out.v.store(seq - 1, Ordering::Relaxed);
        }
        let pf1 = thread_page_faults();
        if in_measure {
            pf_out.v.store(pf1.saturating_sub(pf0), Ordering::Relaxed);
        }
        sfence();
        Ok(())
    }

    fn run_aggr_ram_ptr(
        tid: usize,
        n_aggr: usize,
        cpu: i32,
        base_ptr: *mut u8,
        ring_lines: usize,
        non_temporal: bool,
        run_tag: u64,
        per_thread_mpps: f64,
        ram_batch: usize,
        tsc_hz: f64,
        phase: &AtomicU8,
        ready_count: &AtomicUsize,
        writes_out: &PaddedAtomicU64,
        pf_out: &PaddedAtomicU64,
    ) -> Result<u64, String> {
        pin_to_cpu(cpu).map_err(|e| format!("pin cpu{cpu}: {e}"))?;
        if !ring_lines.is_power_of_two() {
            return Err(format!("ring_lines={ring_lines} must be a power of two"));
        }
        let base_line = tid * ring_lines;
        let mask = ring_lines - 1;
        let batch: usize = ram_batch.max(1);
        let pace_ticks: u64 = if per_thread_mpps > 0.0 {
            ((batch as f64) * tsc_hz / (per_thread_mpps * 1e6)).round().max(1.0) as u64
        } else {
            0
        };

        let mut cell = AlignedCell { w: [0, 0, run_tag, 0x1960_3333, 0x1960_4444, 0x1960_5555, 0x1960_6666, 0] };
        let tag_magic = run_tag ^ CELL_MAGIC;
        let mut seq = 1u64;
        let mut idx = 0usize;
        let stagger = if pace_ticks > 0 && n_aggr > 1 {
            (pace_ticks.saturating_mul(tid as u64)) / (n_aggr as u64)
        } else {
            0
        };
        let mut next_tsc = rdtsc_raw().wrapping_add(stagger);
        let _ = thread_page_faults();
        ready_count.fetch_add(1, Ordering::Release);

        let mut in_measure = false;
        let mut pf0 = 0u64;
        loop {
            let ph = phase.load(Ordering::Acquire);
            if ph == PHASE_STOP {
                break;
            }
            if !in_measure && ph == PHASE_MEASURE {
                pf0 = thread_page_faults();
                in_measure = true;
            }
            if pace_ticks > 0 {
                let now = rdtsc_raw();
                if now < next_tsc {
                    std::hint::spin_loop();
                    continue;
                }
                next_tsc = next_tsc.wrapping_add(pace_ticks);
                if now.saturating_sub(next_tsc) > pace_ticks.saturating_mul(64) {
                    next_tsc = now.wrapping_add(pace_ticks);
                }
            }
            if non_temporal {
                for _ in 0..batch {
                    let l = idx & mask;
                    let g_line = (base_line + l) as u64;
                    let sig = seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ g_line ^ tag_magic;
                    cell.w[0] = seq;
                    cell.w[1] = sig;
                    cell.w[7] = seq;
                    unsafe { write_64b_nt(base_ptr.add(l << 6), &cell) };
                    seq += 1;
                    idx = idx.wrapping_add(1);
                }
                unsafe { _mm_sfence() };
            } else {
                for _ in 0..batch {
                    let l = idx & mask;
                    let g_line = (base_line + l) as u64;
                    let sig = seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ g_line ^ tag_magic;
                    cell.w[0] = seq;
                    cell.w[1] = sig;
                    cell.w[7] = seq;
                    unsafe { write_64b_wb(base_ptr.add(l << 6), &cell) };
                    seq += 1;
                    idx = idx.wrapping_add(1);
                }
            }
            writes_out.v.store(seq - 1, Ordering::Relaxed);
        }
        let pf1 = thread_page_faults();
        if in_measure {
            pf_out.v.store(pf1.saturating_sub(pf0), Ordering::Relaxed);
        }
        if non_temporal {
            unsafe { _mm_sfence() };
        }

        // Verify sampled 4096 written lines across the ring
        let mut bad = 0u64;
        let written_lines = (seq - 1).min(ring_lines as u64) as usize;
        if written_lines > 0 {
            let step = (written_lines / 4096).max(1);
            let mut l = 0usize;
            while l < written_lines {
                let p = unsafe { base_ptr.add(l << 6).cast::<u64>() };
                let w0 = unsafe { p.read() };
                let w1 = unsafe { p.add(1).read() };
                let w2 = unsafe { p.add(2).read() };
                let w7 = unsafe { p.add(7).read() };
                let exp_sig = cell_sig(w0, base_line + l, run_tag);
                if w0 == 0 || w0 != w7 || w2 != run_tag || w1 != exp_sig {
                    bad += 1;
                }
                l += step;
            }
        }
        Ok(bad)
    }

    fn run_aggr_l1spin(
        cpu: i32,
        phase: &AtomicU8,
        ready_count: &AtomicUsize,
        writes_out: &PaddedAtomicU64,
        pf_out: &PaddedAtomicU64,
    ) -> Result<(), String> {
        pin_to_cpu(cpu).map_err(|e| format!("pin cpu{cpu}: {e}"))?;
        let mut x = 0x1234_5678_9abc_def1u64;
        let mut iters = 0u64;
        let _ = thread_page_faults();
        ready_count.fetch_add(1, Ordering::Release);
        let mut in_measure = false;
        let mut pf0 = 0u64;
        loop {
            let ph = phase.load(Ordering::Acquire);
            if ph == PHASE_STOP {
                break;
            }
            if !in_measure && ph == PHASE_MEASURE {
                pf0 = thread_page_faults();
                in_measure = true;
            }
            for _ in 0..4096 {
                x = x.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            }
            iters += 1;
            if (iters & 0xf) == 0 {
                std::hint::black_box(x);
            }
        }
        let pf1 = thread_page_faults();
        if in_measure {
            pf_out.v.store(pf1.saturating_sub(pf0), Ordering::Relaxed);
        }
        writes_out.v.store(std::hint::black_box(x) & 1, Ordering::Relaxed);
        Ok(())
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let args = Args::parse();
        if args.aggr == AggrMode::Bar4 && !has_movdir64b() {
            return Err("CPU does not support MOVDIR64B".into());
        }

        pin_to_cpu(COORD_CPU)?;
        // Pin default task mempolicy to NUMA node 1 (MPOL_BIND) so Linux AutoNUMA
        // does not inject periodic PROT_NONE hint minor page faults into worker stacks/heap.
        unsafe {
            let nodemask: [u64; 1] = [1u64 << 1];
            let _ = libc::syscall(libc::SYS_set_mempolicy, MPOL_BIND, nodemask.as_ptr(), 64usize);
        }
        let tsc_hz = calibrate_tsc_hz();
        let mut floor_ns = 0.0f64;

        let victim_cpus = if args.victim == VictimKind::None {
            Vec::new()
        } else {
            parse_cpus(&args.victim_cpus)?
        };
        let aggr_cpus = if args.aggr == AggrMode::Idle {
            Vec::new()
        } else {
            parse_cpus(&args.aggr_cpus)?
        };
        if args.victim != VictimKind::None && victim_cpus.is_empty() {
            return Err("victim_cpus must be non-empty".into());
        }
        if args.aggr != AggrMode::Idle && aggr_cpus.is_empty() {
            return Err("aggr_cpus must be non-empty".into());
        }
        if args.bar4_sfence_batch == 0 || args.ram_batch == 0 {
            return Err("bar4_sfence_batch and ram_batch must be >= 1".into());
        }
        if args.aggr == AggrMode::Bar4
            && aggr_cpus.len() * BAR4_LINES_PER_THREAD * 64 > MAX_BAR4_SINK_BYTES
        {
            return Err(format!(
                "BAR4 sink overflow: {} threads * {} lines * 64 B = {} B > {} B max",
                aggr_cpus.len(),
                BAR4_LINES_PER_THREAD,
                aggr_cpus.len() * BAR4_LINES_PER_THREAD * 64,
                MAX_BAR4_SINK_BYTES
            )
            .into());
        }
        // Pre-allocate NUMA node 1 hugepage buffers BEFORE opening measurement window
        let chase_buf = match args.victim {
            VictimKind::LlcChase => {
                let b = NumaHugeBuf::alloc_node1(LLC_CHASE_BYTES)?;
                build_hamiltonian_cycle(&b, LLC_CHASE_BYTES, 0x196_11c_0001);
                Some((b, LLC_CHASE_BYTES))
            }
            VictimKind::DramChase => {
                let b = NumaHugeBuf::alloc_node1(DRAM_CHASE_BYTES)?;
                build_hamiltonian_cycle(&b, DRAM_CHASE_BYTES, 0x196_d2a_0002);
                Some((b, DRAM_CHASE_BYTES))
            }
            _ => None,
        };

        let stream_bufs = match args.victim {
            VictimKind::StreamCopy | VictimKind::StreamTriad => {
                let bytes = STREAM_ELEMS * size_of::<f64>();
                let a = NumaHugeBuf::alloc_node1(bytes)?;
                let b = NumaHugeBuf::alloc_node1(bytes)?;
                let c = NumaHugeBuf::alloc_node1(bytes)?;
                unsafe {
                    let pa = a.as_ptr().cast::<f64>();
                    let pb = b.as_ptr().cast::<f64>();
                    let pc = c.as_ptr().cast::<f64>();
                    for i in 0..STREAM_ELEMS {
                        pa.add(i).write(1.0);
                        pb.add(i).write(1.0);
                        pc.add(i).write(2.0);
                    }
                }
                Some((a, b, c))
            }
            _ => None,
        };

        let ram_ring_bytes = (args.ram_ring_mib.max(2) << 20).next_power_of_two();
        let ram_ring_lines = ram_ring_bytes / 64;
        let mut ram_aggr_bufs: Vec<NumaHugeBuf> = Vec::new();
        if matches!(args.aggr, AggrMode::RamWb | AggrMode::RamNt) {
            for _ in 0..aggr_cpus.len() {
                ram_aggr_bufs.push(NumaHugeBuf::alloc_node1(ram_ring_bytes)?);
            }
        }

        // Optional DPDK EAL + Socket-1 Memzone ONLY for real CN106 DPI FromHost pull arm
        let dpi_ring_bytes = (args.dpi_ring_mib.max(2) << 20).next_power_of_two();
        let dpi_eal_opt: Option<Eal> = if args.aggr == AggrMode::DpiPull {
            let eal_args = vec![
                "mrv196_host".to_string(),
                "-l".to_string(),
                COORD_CPU.to_string(),
                "-a".to_string(),
                "0000:99:02.0".to_string(),
                "--file-prefix=mrv196h".to_string(),
                "--in-memory".to_string(),
                "--log-level=lib.eal:warning".to_string(),
            ];
            let (eal, _) = Eal::init(eal_args)?;
            Some(eal)
        } else {
            None
        };
        let dpi_mz_opt: Option<Memzone<'_>> = if let Some(eal_ref) = &dpi_eal_opt {
            let mz = Memzone::reserve_contiguous(eal_ref, "mrv196_dpi_ring", dpi_ring_bytes, 1, 4096)?;
            unsafe { mz.as_ptr().write_bytes(0, dpi_ring_bytes) };
            Some(mz)
        } else {
            None
        };

        // Open all host PMUs BEFORE starting BAR4 lease or card DPI FromHost pulls
        // so a local PMU setup failure can never leave card DPI running.
        let pmu = HostPmu::open(
            args.cha_occ.config(),
            args.aggr == AggrMode::DpiPull,
            &victim_cpus,
            &aggr_cpus,
        )?;

        // Acquire MRV-26 BAR4 lease & connect card sync for BAR4 or DpiPull modes
        let mut sync_client: Option<SyncClient> = None;
        let lease_guard: Option<Lease> = if args.aggr == AggrMode::Bar4 {
            let l = Lease::acquire(&args.card)?;
            let mut sc = SyncClient::connect(&args.sync)?;
            let total_lines = aggr_cpus.len() * BAR4_LINES_PER_THREAD;
            let clear_resp = sc.cmd(&format!("CLEAR {total_lines}"))?;
            if !clear_resp.contains("\"ok\":true") {
                return Err(format!("card CLEAR failed: {clear_resp}").into());
            }
            sync_client = Some(sc);
            Some(l)
        } else if args.aggr == AggrMode::DpiPull {
            let sc = SyncClient::connect(&args.sync)?;
            sync_client = Some(sc);
            let mz = dpi_mz_opt.as_ref().unwrap();
            let start_cmd = format!(
                "DPI_START {} {} {} {}",
                mz.iova(),
                dpi_ring_bytes,
                args.dpi_chunk_bytes,
                args.dpi_depth
            );
            match sync_client.as_mut().unwrap().cmd(&start_cmd) {
                Ok(start_resp) if start_resp.contains("\"ok\":true") => {}
                Ok(start_resp) => {
                    let pre_start_reject = start_resp.contains("\"dpi_not_enabled\"")
                        || start_resp.contains("\"dpi_start_failed\"")
                        || start_resp.contains("\"dpi_init_timeout\"")
                        || start_resp.contains("\"dpi_already_running\"");
                    if !pre_start_reject {
                        let _ = drain_dpi_until_ok(&mut sync_client, &args.sync, false);
                    }
                    return Err(format!("card DPI_START failed: {start_resp}").into());
                }
                Err(e) => {
                    sync_client = None;
                    let _ = drain_dpi_until_ok(&mut sync_client, &args.sync, true);
                    return Err(format!("card DPI_START transport error (drained safely): {e}").into());
                }
            }
            None
        } else {
            None
        };
        let phase = Arc::new(AtomicU8::new(PHASE_WARMUP));
        let ready_count = Arc::new(AtomicUsize::new(0));
        let calib_done = Arc::new(AtomicBool::new(false));
        let writes_counters: Vec<PaddedAtomicU64> =
            (0..aggr_cpus.len()).map(|_| PaddedAtomicU64::new(0)).collect();
        let aggr_pf_counters: Vec<PaddedAtomicU64> =
            (0..aggr_cpus.len()).map(|_| PaddedAtomicU64::new(0)).collect();

        let per_thread_mpps = if args.target_mpps > 0.0 && !aggr_cpus.is_empty() {
            args.target_mpps / (aggr_cpus.len() as f64)
        } else {
            0.0
        };
        let n_aggr = aggr_cpus.len();
        let expected_ready = aggr_cpus.len()
            + match args.victim {
                VictimKind::None => 0,
                VictimKind::LlcChase | VictimKind::DramChase => 1,
                VictimKind::StreamCopy | VictimKind::StreamTriad => victim_cpus.len(),
            };

        let mut chase_res: Option<ChaseResult> = None;
        let mut stream_res: Option<StreamResult> = None;
        let mut ram_verify_errors = 0u64;
        let mut aggr_writes_delta = 0u64;
        let mut card_pmu_json = String::from("null");
        let mut warmup_elapsed_ms = 0u64;
        let mut warmup_min_ghz = 0.0f64;
        let mut pmu_s0: Option<HostPmuSnap> = None;
        let mut pmu_s1: Option<HostPmuSnap> = None;
        let mut pf_delta = 0u64;

        let scope_res: Result<(), Box<dyn Error>> = thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            // Spawn aggressor threads
            let mut aggr_handles = Vec::new();
            for (tid, &cpu) in aggr_cpus.iter().enumerate() {
                let ph = Arc::clone(&phase);
                let rc = Arc::clone(&ready_count);
                let ctr = &writes_counters[tid];
                let pfc = &aggr_pf_counters[tid];
                match args.aggr {
                    AggrMode::Idle => {}
                    AggrMode::L1Spin => {
                        aggr_handles.push(scope.spawn(move || -> Result<u64, String> {
                            run_aggr_l1spin(cpu, &ph, &rc, ctr, pfc)?;
                            Ok(0)
                        }));
                    }
                    AggrMode::Bar4 => {
                        let l_ref = lease_guard.as_ref().unwrap();
                        let bar_path = args.bar.as_str();
                        let tag = args.run_tag;
                        let batch = args.bar4_sfence_batch;
                        aggr_handles.push(scope.spawn(move || -> Result<u64, String> {
                            run_aggr_bar4(
                                tid, n_aggr, cpu, l_ref, bar_path, tag, per_thread_mpps, batch,
                                tsc_hz, &ph, &rc, ctr, pfc,
                            )?;
                            Ok(0)
                        }));
                    }
                    AggrMode::RamWb | AggrMode::RamNt => {
                        let p_addr = ram_aggr_bufs[tid].as_ptr() as usize;
                        let nt = args.aggr == AggrMode::RamNt;
                        let tag = args.run_tag;
                        let batch = args.ram_batch;
                        aggr_handles.push(scope.spawn(move || -> Result<u64, String> {
                            run_aggr_ram_ptr(
                                tid, n_aggr, cpu, p_addr as *mut u8, ram_ring_lines, nt, tag,
                                per_thread_mpps, batch, tsc_hz, &ph, &rc, ctr, pfc,
                            )
                        }));
                    }
                    AggrMode::DpiPull => {
                        let mz = dpi_mz_opt.as_ref().unwrap();
                        let slice_raw = (dpi_ring_bytes / aggr_cpus.len()).max(4096);
                        let per_thr_bytes = 1usize << slice_raw.ilog2();
                        let p_addr = unsafe { mz.as_ptr().add(tid * per_thr_bytes) } as usize;
                        let per_thr_lines = (per_thr_bytes / 64).max(64);
                        let tag = args.run_tag;
                        let batch = args.ram_batch;
                        aggr_handles.push(scope.spawn(move || -> Result<u64, String> {
                            run_aggr_ram_ptr(
                                tid, n_aggr, cpu, p_addr as *mut u8, per_thr_lines, false, tag,
                                per_thread_mpps, batch, tsc_hz, &ph, &rc, ctr, pfc,
                            )
                        }));
                    }
                }
            }

            // Spawn victim thread(s)
            let mut chase_handle = None;
            let mut stream_handles = Vec::new();
            let stream_barrier = Arc::new(SpinBarrier::new(victim_cpus.len().max(1)));
            let stream_pass_ticks = Arc::new(std::sync::Mutex::new((vec![1u64; 4096], 0usize)));

            match args.victim {
                VictimKind::None => {}
                VictimKind::LlcChase | VictimKind::DramChase => {
                    let (buf, bytes) = chase_buf.as_ref().unwrap();
                    let vcpu = victim_cpus[0];
                    let k = if args.chase_k > 0 {
                        args.chase_k
                    } else if args.victim == VictimKind::LlcChase {
                        32
                    } else {
                        8
                    };
                    let ph = Arc::clone(&phase);
                    let rc = Arc::clone(&ready_count);
                    let cd = Arc::clone(&calib_done);
                    let b_ref = buf;
                    let sz = *bytes;
                    chase_handle = Some(scope.spawn(move || -> Result<ChaseResult, String> {
                        run_chase_victim(vcpu, b_ref, sz, k, &ph, &rc, &cd, tsc_hz)
                            .map_err(|e| e.to_string())
                    }));
                }
                VictimKind::StreamCopy | VictimKind::StreamTriad => {
                    let (ba, bb, bc) = stream_bufs.as_ref().unwrap();
                    let n_thr = victim_cpus.len();
                    let is_triad = args.victim == VictimKind::StreamTriad;
                    let chunk = (STREAM_ELEMS / n_thr) & !15usize;
                    let total_used = chunk * n_thr;
                    for (tidx, &vcpu) in victim_cpus.iter().enumerate() {
                        let ph = Arc::clone(&phase);
                        let rc = Arc::clone(&ready_count);
                        let bar = Arc::clone(&stream_barrier);
                        let pticks = Arc::clone(&stream_pass_ticks);
                        let pa = unsafe { ba.as_ptr().cast::<f64>().add(tidx * chunk) } as usize;
                        let pb = unsafe { bb.as_ptr().cast::<f64>().add(tidx * chunk) } as usize;
                        let pc = unsafe { bc.as_ptr().cast::<f64>().add(tidx * chunk) } as usize;
                        stream_handles.push(scope.spawn(move || -> Result<(usize, u64), String> {
                            pin_to_cpu(vcpu).map_err(|e| format!("pin victim cpu{vcpu}: {e}"))?;
                            let a = pa as *mut f64;
                            let b = pb as *const f64;
                            let c = pc as *const f64;
                            let c_mut = pc as *mut f64;
                            let a_const = pa as *const f64;
                            let _ = thread_page_faults();
                            rc.fetch_add(1, Ordering::Release);

                            let mut in_measure = false;
                            let mut pf0 = 0u64;
                            loop {
                                let cmd = bar.wait_phase(&ph);
                                if cmd == PHASE_STOP {
                                    break;
                                }
                                if !in_measure && cmd == PHASE_MEASURE {
                                    pf0 = thread_page_faults();
                                    in_measure = true;
                                }
                                let t0 = if cmd == PHASE_MEASURE && tidx == 0 {
                                    rdtscp_lfence()
                                } else {
                                    0
                                };
                                if is_triad {
                                    unsafe { stream_triad_avx2(a, b, c, 3.0, chunk) };
                                } else {
                                    unsafe { stream_copy_avx2(c_mut, a_const, chunk) };
                                }
                                bar.wait();
                                if cmd == PHASE_MEASURE && tidx == 0 {
                                    let t1 = rdtscp_lfence();
                                    let mut guard = pticks.lock().unwrap();
                                    let idx = guard.1;
                                    if idx < guard.0.len() {
                                        guard.0[idx] = t1.saturating_sub(t0);
                                        guard.1 = idx + 1;
                                    }
                                }
                            }
                            let pf1 = thread_page_faults();
                            let vpf = if in_measure { pf1.saturating_sub(pf0) } else { 0 };
                            Ok((total_used, vpf))
                        }));
                    }
                }
            }

            // Wait until EVERY worker thread has pinned, mapped BAR4/buffers, and pre-faulted stack
            let t_ready = Instant::now();
            while ready_count.load(Ordering::Acquire) < expected_ready {
                if t_ready.elapsed() > Duration::from_secs(5) {
                    phase.store(PHASE_STOP, Ordering::Release);
                    return Err("worker threads timed out before ready".into());
                }
                thread::sleep(Duration::from_millis(1));
            }

            // Adaptive warmup: ensure min warmup_ms AND all active cores ramped >= min_ghz
            let tw = Instant::now();
            loop {
                let el = tw.elapsed().as_millis() as u64;
                if el >= args.warmup_ms {
                    let (mg, _) = pmu.measure_active_min_ghz(60);
                    warmup_min_ghz = mg;
                    if args.min_ghz <= 0.0 || mg >= args.min_ghz || el >= args.max_warmup_ms {
                        warmup_elapsed_ms = tw.elapsed().as_millis() as u64;
                        break;
                    }
                } else {
                    thread::sleep(Duration::from_millis(50));
                }
            }

            // Calibrate chase timer floor at the end of WARMUP, strictly BEFORE PMU_START / PHASE_MEASURE
            if chase_handle.is_some() {
                phase.store(PHASE_CALIB, Ordering::Release);
                let t_cal = Instant::now();
                while !calib_done.load(Ordering::Acquire) {
                    if t_cal.elapsed() > Duration::from_secs(2) {
                        phase.store(PHASE_STOP, Ordering::Release);
                        return Err("chase timer floor calibration timed out".into());
                    }
                    std::hint::spin_loop();
                }
            }

            // Open measurement window (PMU_START / PMU_STOP strictly outside pf0..pf1)
            if let Some(sc) = sync_client.as_mut() {
                if args.with_card_pmu {
                    let _ = sc.cmd("PMU_START")?;
                }
            }
            let w0: u64 = writes_counters.iter().map(|c| c.v.load(Ordering::Relaxed)).sum();
            let s0 = pmu.snap();
            let pf0 = process_page_faults();
            phase.store(PHASE_MEASURE, Ordering::Release);

            thread::sleep(Duration::from_millis(args.duration_ms));

            let pf1 = process_page_faults();
            let s1 = pmu.snap();
            let w1: u64 = writes_counters.iter().map(|c| c.v.load(Ordering::Relaxed)).sum();
            phase.store(PHASE_STOP, Ordering::Release);
            if let Some(sc) = sync_client.as_mut() {
                if args.with_card_pmu {
                    card_pmu_json = sc.cmd("PMU_STOP")?;
                }
            }
            pmu_s0 = Some(s0);
            pmu_s1 = Some(s1);
            aggr_writes_delta = w1.saturating_sub(w0);
            pf_delta = pf1.saturating_sub(pf0);

            // Join victims
            if let Some(h) = chase_handle {
                chase_res = Some(h.join().map_err(|_| "chase panic")??);
            }
            if !stream_handles.is_empty() {
                let mut total_used = 0usize;
                let mut stream_vpf = 0u64;
                for h in stream_handles {
                    let (tu, vpf) = h.join().map_err(|_| "stream panic")??;
                    total_used = tu;
                    stream_vpf += vpf;
                }
                let guard = stream_pass_ticks.lock().unwrap();
                let ticks = &guard.0[..guard.1];
                let bytes_per_pass = if args.victim == VictimKind::StreamTriad {
                    (total_used as u64) * 24 // 3 arrays * 8 B logical STREAM Triad
                } else {
                    (total_used as u64) * 16 // 2 arrays * 8 B logical STREAM Copy
                };
                let mut gbs: Vec<f64> = ticks
                    .iter()
                    .map(|&t| (bytes_per_pass as f64) / ((t as f64) / tsc_hz) / 1e9)
                    .collect();
                let tot_ticks: u64 = ticks.iter().sum();
                let total_ns = (tot_ticks as f64) * 1e9 / tsc_hz;
                let mean_gb_s = if tot_ticks > 0 {
                    ((ticks.len() as u64 * bytes_per_pass) as f64) / (total_ns / 1e9) / 1e9
                } else {
                    0.0
                };
                gbs.sort_by(|a, b| a.total_cmp(b));
                let median_pass = if gbs.is_empty() { 0.0 } else { gbs[gbs.len() / 2] };
                let p10_pass = if gbs.is_empty() { 0.0 } else { gbs[gbs.len() / 10] };
                let min_pass = gbs.first().copied().unwrap_or(0.0);
                let max_pass = gbs.last().copied().unwrap_or(0.0);

                // Verify STREAM output numerically
                let (ba, _, bc) = stream_bufs.as_ref().unwrap();
                let mut verify_errors = 0u64;
                if args.victim == VictimKind::StreamTriad {
                    let pa = ba.as_ptr().cast::<f64>();
                    for i in (0..total_used).step_by(64) {
                        let v = unsafe { pa.add(i).read() };
                        if (v - 7.0).abs() > 1e-9 {
                            verify_errors += 1;
                        }
                    }
                } else {
                    let pc = bc.as_ptr().cast::<f64>();
                    for i in (0..total_used).step_by(64) {
                        let v = unsafe { pc.add(i).read() };
                        if (v - 1.0).abs() > 1e-9 {
                            verify_errors += 1;
                        }
                    }
                }
                stream_res = Some(StreamResult {
                    passes: ticks.len() as u64,
                    total_ns,
                    mean_gb_s,
                    median_pass_gb_s: median_pass,
                    p10_pass_gb_s: p10_pass,
                    min_pass_gb_s: min_pass,
                    max_pass_gb_s: max_pass,
                    bytes_per_pass,
                    verify_errors,
                    victim_page_faults: stream_vpf,
                });
            }

            // Join aggressors
            for h in aggr_handles {
                let bad = h.join().map_err(|_| "aggr panic")??;
                ram_verify_errors += bad;
            }
            Ok(())
        });

        // Safety gate: NEVER drop Memzone/Eal or exit the process (which closes the VFIO
        // container and unmaps IOMMU IOVAs) until card DPI_STOP confirms ok:true (all
        // in-flight FromHost DMA reads drained).
        let mut card_verify_json = String::from("null");
        let mut dpi_stats_json = String::from("null");
        if args.aggr == AggrMode::DpiPull {
            dpi_stats_json = drain_dpi_until_ok(&mut sync_client, &args.sync, false);
        }
        scope_res?;
        if args.aggr == AggrMode::Bar4 {
            sfence();
            if let Some(l) = &lease_guard {
                let slot = l.map_slot(&args.bar)?;
                slot.check()?;
                let _ = std::hint::black_box(slot.read_u64(0));
            }
            if let Some(sc) = sync_client.as_mut() {
                let total_lines = aggr_cpus.len() * BAR4_LINES_PER_THREAD;
                card_verify_json = sc.cmd(&format!("VERIFY {total_lines} {}", args.run_tag))?;
            }
        }
        let s0 = pmu_s0.unwrap();
        let s1 = pmu_s1.unwrap();
        let wall_s = s1.wall.saturating_duration_since(s0.wall).as_secs_f64();
        let mut min_pct = 100.0f64;
        let mut d = |a: Sample, b: Sample| -> u64 {
            let x = b.delta(a);
            min_pct = min_pct.min(x.pct());
            x.val
        };

        let mut cha_occ_sum = 0u64;
        let mut cha_ins_wcilf_sum = 0u64;
        let mut cha_ins_all_sum = 0u64;
        let mut cha_box_occ = [0u64; NUM_CHAS];
        for i in 0..NUM_CHAS {
            let bo = d(s0.cha_occ[i], s1.cha_occ[i]);
            cha_box_occ[i] = bo;
            cha_occ_sum += bo;
            cha_ins_wcilf_sum += d(s0.cha_ins_wcilf[i], s1.cha_ins_wcilf[i]);
            cha_ins_all_sum += d(s0.cha_ins_all[i], s1.cha_ins_all[i]);
        }
        let cha_clk = d(s0.cha_clk, s1.cha_clk);
        let mesh_ghz = (cha_clk as f64) / wall_s / 1e9;
        let cha_occ_entries = if cha_clk > 0 { cha_occ_sum as f64 / cha_clk as f64 } else { 0.0 };
        let mut box_entries = [0.0f64; NUM_CHAS];
        if cha_clk > 0 {
            for i in 0..NUM_CHAS {
                box_entries[i] = cha_box_occ[i] as f64 / cha_clk as f64;
            }
        }
        box_entries.sort_by(|a, b| a.total_cmp(b));
        let cha_occ_min_box = box_entries[0];
        let cha_occ_p50_box = box_entries[NUM_CHAS / 2];
        let cha_occ_max_box = box_entries[NUM_CHAS - 1];
        let cha_wcilf_res_ns = if args.cha_occ == ChaOccMode::Wcilf && cha_ins_wcilf_sum > 0 && mesh_ghz > 0.0 {
            (cha_occ_sum as f64 / cha_ins_wcilf_sum as f64) / mesh_ghz
        } else {
            0.0
        };
        let cha_all_res_ns = if args.cha_occ == ChaOccMode::All && cha_ins_all_sum > 0 && mesh_ghz > 0.0 {
            (cha_occ_sum as f64 / cha_ins_all_sum as f64) / mesh_ghz
        } else {
            0.0
        };
        let mut imc_rd_cas = 0u64;
        let mut imc_wr_cas = 0u64;
        let mut imc_rpq_occ = 0u64;
        let mut imc_active_ch = 0usize;
        for i in 0..NUM_IMCS {
            let r = d(s0.imc_rd[i], s1.imc_rd[i]);
            let w = d(s0.imc_wr[i], s1.imc_wr[i]);
            let q = d(s0.imc_rpq[i], s1.imc_rpq[i]);
            if r + w > 10_000 {
                imc_active_ch += 1;
            }
            imc_rd_cas += r;
            imc_wr_cas += w;
            imc_rpq_occ += q;
        }
        let imc_clk = d(s0.imc_clk, s1.imc_clk);
        let imc_dclk_ghz = (imc_clk as f64) / wall_s / 1e9;
        let imc_rd_gb_s = (imc_rd_cas as f64 * 64.0) / wall_s / 1e9;
        let imc_wr_gb_s = (imc_wr_cas as f64 * 64.0) / wall_s / 1e9;
        let imc_tot_gb_s = imc_rd_gb_s + imc_wr_gb_s;
        let imc_rpq_entries = if imc_clk > 0 { imc_rpq_occ as f64 / imc_clk as f64 } else { 0.0 };

        let m2p_ins_ncb = d(s0.m2p_ins_ncb, s1.m2p_ins_ncb);
        let m2p_ne_ncb = d(s0.m2p_ne_ncb, s1.m2p_ne_ncb);
        let m2p_crd_rej = d(s0.m2p_crd_rej, s1.m2p_crd_rej);
        let m2p_clk = d(s0.m2p_clk, s1.m2p_clk);
        // MRV-185: M2PCIe1 RxC_NE.CHA_NCB (0x0210) carries a 2x hardware multiplier (divide by 2*clk)
        let m2p_ne_ncb_duty_pct = if m2p_clk > 0 {
            (m2p_ne_ncb as f64 / (2.0 * m2p_clk as f64)) * 100.0
        } else {
            0.0
        };

        let irp_occ = d(s0.irp_occ, s1.irp_occ);
        let irp_ins_ncs = d(s0.irp_ins_ncs, s1.irp_ins_ncs);

        let iio_wr_txn = d(s0.iio_wr_txn, s1.iio_wr_txn);
        let iio_wr_dw = d(s0.iio_wr_dw, s1.iio_wr_dw);
        let iio_out_cl = d(s0.iio_out_cl, s1.iio_out_cl);
        let iio_clk = d(s0.iio_clk, s1.iio_clk);
        let iio_ghz = (iio_clk as f64) / wall_s / 1e9;
        let irp_occ_entries = if iio_clk > 0 { irp_occ as f64 / iio_clk as f64 } else { 0.0 };
        let iio_wr_mpps = (iio_wr_txn as f64) / wall_s / 1e6;
        let iio_wr_gb_s = (iio_wr_dw as f64 * 4.0) / wall_s / 1e9;

        let mut victim_ghz_list = Vec::new();
        let mut victim_min_ghz = f64::INFINITY;
        for (a, b) in s0.victim_clks[..s0.n_victim_clks].iter().zip(s1.victim_clks[..s1.n_victim_clks].iter()) {
            let dc = d(a.cycles, b.cycles);
            let dr = d(a.ref_cycles, b.ref_cycles);
            let g = if dr > 0 { (dc as f64 / dr as f64) * (REF_HZ / 1e9) } else { 0.0 };
            victim_min_ghz = victim_min_ghz.min(g);
            victim_ghz_list.push(g);
        }
        if victim_min_ghz.is_infinite() {
            victim_min_ghz = 0.0;
        }
        let victim_prim_json = if let (Some(a), Some(b)) = (&s0.victim_prim, &s1.victim_prim) {
            let inst = d(a.inst, b.inst);
            let llc_ref = d(a.llc_ref, b.llc_ref);
            let llc_miss = d(a.llc_miss, b.llc_miss);
            let l3_hit = d(a.l3_hit, b.l3_hit);
            let l3_miss = d(a.l3_miss, b.l3_miss);
            let llc_miss_pct = if llc_ref > 0 { llc_miss as f64 * 100.0 / llc_ref as f64 } else { 0.0 };
            let l3_load_miss_pct = if l3_hit + l3_miss > 0 {
                l3_miss as f64 * 100.0 / (l3_hit + l3_miss) as f64
            } else {
                0.0
            };
            format!(
                "{{\"instructions\":{inst},\"llc_ref\":{llc_ref},\"llc_miss\":{llc_miss},\"llc_miss_pct\":{llc_miss_pct:.4},\"l3_hit\":{l3_hit},\"l3_miss\":{l3_miss},\"l3_load_miss_pct\":{l3_load_miss_pct:.4}}}"
            )
        } else {
            String::from("null")
        };

        let mut aggr_ghz_list = Vec::new();
        let mut aggr_min_ghz = f64::INFINITY;
        let mut aggr_sb_stalls_sum = 0u64;
        let mut aggr_cycles_sum = 0u64;
        for (a, b) in s0.aggr_clks[..s0.n_aggr_clks].iter().zip(s1.aggr_clks[..s1.n_aggr_clks].iter()) {
            let dc = d(a.cycles, b.cycles);
            let dr = d(a.ref_cycles, b.ref_cycles);
            aggr_cycles_sum += dc;
            if let (Some(sa), Some(sb)) = (a.sb_stalls, b.sb_stalls) {
                aggr_sb_stalls_sum += d(sa, sb);
            }
            let g = if dr > 0 { (dc as f64 / dr as f64) * (REF_HZ / 1e9) } else { 0.0 };
            aggr_min_ghz = aggr_min_ghz.min(g);
            aggr_ghz_list.push(g);
        }
        if aggr_min_ghz.is_infinite() {
            aggr_min_ghz = 0.0;
        }
        let aggr_sb_stall_pct = if aggr_cycles_sum > 0 {
            aggr_sb_stalls_sum as f64 * 100.0 / aggr_cycles_sum as f64
        } else {
            0.0
        };

        let aggr_mpps = (aggr_writes_delta as f64) / wall_s / 1e6;
        let aggr_gb_s = (aggr_writes_delta as f64 * 64.0) / wall_s / 1e9;
        let aggr_page_faults: u64 = aggr_pf_counters.iter().map(|c| c.v.load(Ordering::Relaxed)).sum();
        let victim_page_faults: u64 = chase_res
            .as_ref()
            .map(|c| c.victim_page_faults)
            .or_else(|| stream_res.as_ref().map(|s| s.victim_page_faults))
            .unwrap_or(0);
        let iio_dir = if args.aggr == AggrMode::DpiPull {
            "inbound_dma_rd"
        } else {
            "outbound_cpu_wr"
        };

        let chase_json = if let Some(cr) = chase_res {
            floor_ns = cr.timer_floor_ns;
            let overflow_pct = if cr.total_chunks > 0 {
                cr.overflow_chunks as f64 * 100.0 / cr.total_chunks as f64
            } else {
                0.0
            };
            format!(
                "{{\"buf_mib\":{},\"k_hops\":{},\"total_hops\":{},\"total_chunks\":{},\"total_ns\":{:.1},\"raw_mean_ns\":{:.4},\"net_mean_ns\":{:.4},\"p50_ns\":{:.2},\"p90_ns\":{:.2},\"p99_ns\":{:.2},\"p999_ns\":{:.2},\"p99_overflow\":{},\"p999_overflow\":{},\"max_ns\":{:.2},\"bin_step_ns\":{:.2},\"fine_cap_ns\":{:.1},\"coarse_cap_ns\":{:.1},\"overflow_chunks\":{},\"overflow_pct\":{:.6},\"bad_nodes\":{},\"xor_acc\":{}}}",
                cr.buf_mib, cr.k_hops, cr.total_hops, cr.total_chunks, cr.total_ns,
                cr.raw_mean_ns, cr.net_mean_ns, cr.p50_ns, cr.p90_ns, cr.p99_ns,
                cr.p999_ns, cr.p99_overflow, cr.p999_overflow, cr.max_ns,
                cr.bin_step_ns, cr.fine_cap_ns, cr.coarse_cap_ns,
                cr.overflow_chunks, overflow_pct, cr.bad_nodes, cr.xor_acc
            )
        } else {
            String::from("null")
        };

        let stream_json = if let Some(sr) = stream_res {
            format!(
                "{{\"passes\":{},\"total_ns\":{:.1},\"bytes_per_pass\":{},\"mean_gb_s\":{:.4},\"median_pass_gb_s\":{:.4},\"p10_pass_gb_s\":{:.4},\"min_pass_gb_s\":{:.4},\"max_pass_gb_s\":{:.4},\"verify_errors\":{}}}",
                sr.passes, sr.total_ns, sr.bytes_per_pass, sr.mean_gb_s,
                sr.median_pass_gb_s, sr.p10_pass_gb_s, sr.min_pass_gb_s,
                sr.max_pass_gb_s, sr.verify_errors
            )
        } else {
            String::from("null")
        };

        let fmt_f64_vec = |v: &[f64]| -> String {
            let s: Vec<String> = v.iter().map(|x| format!("{x:.3}")).collect();
            format!("[{}]", s.join(","))
        };
        let fmt_i32_vec = |v: &[i32]| -> String {
            let s: Vec<String> = v.iter().map(|x| x.to_string()).collect();
            format!("[{}]", s.join(","))
        };

        println!(
            "{{\"victim\":\"{}\",\"aggr\":\"{}\",\"victim_cpus\":{},\"aggr_cpus\":{},\"target_mpps\":{:.2},\"bar4_sfence_batch\":{},\"ram_batch\":{},\"ram_ring_mib\":{},\"dpi_ring_mib\":{},\"cha_occ_mode\":\"{}\",\"run_tag\":{},\"wall_s\":{:.6},\"tsc_hz\":{:.1},\"timer_floor_ns\":{:.3},\"warmup_ms\":{},\"warmup_min_ghz\":{:.3},\"page_faults\":{},\"victim_page_faults\":{},\"aggr_page_faults\":{},\"host_pmu_min_pct\":{:.2},\"aggr_writes\":{},\"aggr_mpps\":{:.4},\"aggr_gb_s\":{:.4},\"ram_verify_errors\":{},\"victim_min_ghz\":{:.3},\"victim_ghz\":{},\"aggr_min_ghz\":{:.3},\"aggr_ghz\":{},\"aggr_sb_stall_pct\":{:.3},\"victim_core_pmu\":{},\"chase\":{},\"stream\":{},\"cha\":{{\"occ\":{},\"occ_entries\":{:.4},\"occ_min_box\":{:.4},\"occ_p50_box\":{:.4},\"occ_max_box\":{:.4},\"ins_wcilf\":{},\"ins_all\":{},\"clk\":{},\"mesh_ghz\":{:.4},\"wcilf_res_ns\":{:.3},\"all_res_ns\":{:.3}}},\"imc\":{{\"rd_cas\":{},\"wr_cas\":{},\"rd_gb_s\":{:.4},\"wr_gb_s\":{:.4},\"tot_gb_s\":{:.4},\"rpq_occ\":{},\"rpq_entries\":{:.4},\"dclk_ghz\":{:.4},\"active_ch\":{}}},\"m2p1\":{{\"ins_cha_ncb\":{},\"ne_cha_ncb\":{},\"ne_cha_ncb_duty_pct\":{:.3},\"crd_reject_ncb\":{},\"clk\":{}}},\"irp1\":{{\"occ\":{},\"occ_entries\":{:.4},\"ins_ncs\":{}}},\"iio1\":{{\"dir\":\"{}\",\"wr_txn\":{},\"wr_dw\":{},\"out_cl\":{},\"clk\":{},\"ghz\":{:.4},\"wr_mpps\":{:.4},\"wr_gb_s\":{:.4}}},\"card_pmu\":{},\"card_verify\":{},\"dpi_stats\":{}}}",
            args.victim.as_str(),
            args.aggr.as_str(),
            fmt_i32_vec(&victim_cpus),
            fmt_i32_vec(&aggr_cpus),
            args.target_mpps,
            args.bar4_sfence_batch,
            args.ram_batch,
            args.ram_ring_mib,
            args.dpi_ring_mib,
            args.cha_occ.as_str(),
            args.run_tag,
            wall_s,
            tsc_hz,
            floor_ns,
            warmup_elapsed_ms,
            warmup_min_ghz,
            pf_delta,
            victim_page_faults,
            aggr_page_faults,
            min_pct,
            aggr_writes_delta,
            aggr_mpps,
            aggr_gb_s,
            ram_verify_errors,
            victim_min_ghz,
            fmt_f64_vec(&victim_ghz_list),
            aggr_min_ghz,
            fmt_f64_vec(&aggr_ghz_list),
            aggr_sb_stall_pct,
            victim_prim_json,
            chase_json,
            stream_json,
            cha_occ_sum,
            cha_occ_entries,
            cha_occ_min_box,
            cha_occ_p50_box,
            cha_occ_max_box,
            cha_ins_wcilf_sum,
            cha_ins_all_sum,
            cha_clk,
            mesh_ghz,
            cha_wcilf_res_ns,
            cha_all_res_ns,
            imc_rd_cas,
            imc_wr_cas,
            imc_rd_gb_s,
            imc_wr_gb_s,
            imc_tot_gb_s,
            imc_rpq_occ,
            imc_rpq_entries,
            imc_dclk_ghz,
            imc_active_ch,
            m2p_ins_ncb,
            m2p_ne_ncb,
            m2p_ne_ncb_duty_pct,
            m2p_crd_rej,
            m2p_clk,
            irp_occ,
            irp_occ_entries,
            irp_ins_ncs,
            iio_dir,
            iio_wr_txn,
            iio_wr_dw,
            iio_out_cl,
            iio_clk,
            iio_ghz,
            iio_wr_mpps,
            iio_wr_gb_s,
            card_pmu_json,
            card_verify_json,
            dpi_stats_json,
        );
        Ok(())
    }
}
