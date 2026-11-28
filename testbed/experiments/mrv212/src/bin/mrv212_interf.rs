//! Host-side Socket-1 neighbor interference benchmark for MRV-212 Control B:
//! 1. Realistic 100GbE credit-signaling rates (`0.5–7.0 Mpps`, 1–8 host cores,
//!    strictly NO `SFENCE`, per-core dedicated 64 B BAR4 line or multi-line ring)
//! 2. Closing the MRV-196 Opus 5.5 audit item: 1-core unthrottled full-speed
//!    `MOVDIR64B -> BAR4` `SFENCE` decoupling ladder (`sfence_batch = 512 / 2048 / 8192 / 0 [NO SFENCE]`).
//!
//! Co-located Socket-1 independent-physical-core victims:
//! - `llc-chase`: 16 MiB random Hamiltonian pointer chase (fits in 60 MiB L3)
//! - `dram-chase`: 256 MiB random Hamiltonian pointer chase (`K=1` true single-hop & `K=8`)
//! - `stream-triad`: 6-core AVX2 STREAM Triad (saturates Socket 1 2-ch DDR5)
//!
//! Safety invariants:
//! - BAR4 slot 15 ONLY accessed under MRV-26 `Lease::acquire` + `LeasedSlot::check()`.
//! - All victim hugepages strictly `mbind(MPOL_BIND, node=1)` + verified via `get_mempolicy`.
//! - 100% non-multiplexed SPR CHA/IMC/M2PCIe1/IRP1/IIO1/Core PMU + CN106 TAD/DDR5 PMU.
//! - Zero-backlog pacer (`next = max(next + pace, now)`), avoiding catchup bursts.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv212_interf runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::arch::asm;
    use std::arch::x86_64::{_mm256_fmadd_pd, _mm256_load_pd, _mm256_set1_pd, _mm256_store_pd};
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::size_of;
    use std::net::TcpStream;
    use std::os::fd::RawFd;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, AtomicUsize, Ordering};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use testbed::lease::{self, Lease};
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};
    use mrv212::{
        CELL_MAGIC, MAX_SINK_BYTES, SINK_BASE, SYNC_ADDR,
    };

    const BAR4_UC_PATH: &str = "/sys/bus/pci/devices/0000:99:00.0/resource4";
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const COORD_CPU: i32 = 36;
    const NUM_CHAS: usize = 32;
    const NUM_IMCS: usize = 8;
    const REF_HZ: f64 = 2.2e9;
    const HUGE_2M: usize = 2 << 20;

    const LLC_CHASE_BYTES: usize = 16 << 20;
    const DRAM_CHASE_BYTES: usize = 256 << 20;
    const STREAM_ELEMS: usize = 16 * 1024 * 1024; // 128 MiB per array (384 MiB Triad)
    const NODE_SIG_MAGIC: u64 = 0x5A5A_2120_0000_0000;

    const PHASE_WARMUP: u8 = 0;
    const PHASE_CALIB: u8 = 1;
    const PHASE_MEASURE: u8 = 2;
    const PHASE_STOP: u8 = 3;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum VictimKind {
        None,
        LlcChase,
        DramChase,
        StreamTriad,
    }

    impl VictimKind {
        fn as_str(self) -> &'static str {
            match self {
                Self::None => "none",
                Self::LlcChase => "llc-chase",
                Self::DramChase => "dram-chase",
                Self::StreamTriad => "stream-triad",
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum AggrMode {
        Idle,
        L1Spin,
        Bar4,
    }

    impl AggrMode {
        fn as_str(self) -> &'static str {
            match self {
                Self::Idle => "idle",
                Self::L1Spin => "l1-spin",
                Self::Bar4 => "bar4",
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum)]
        victim: VictimKind,
        #[arg(long, value_enum)]
        aggr: AggrMode,
        #[arg(long, default_value = "40")]
        victim_cpus: String,
        #[arg(long, default_value = "52")]
        aggr_cpus: String,
        /// Aggregate target BAR4 write rate in Mpps (0 = unthrottled full speed).
        #[arg(long, default_value_t = 0.0)]
        target_mpps: f64,
        /// Writes per SFENCE batch. `0` = ZERO SFENCE in hot loop (pure no-SFENCE!).
        #[arg(long, default_value_t = 0)]
        sfence_batch: usize,
        /// Distinct 64 B BAR4 lines per aggressor thread (1 = dedicated single credit line; 4096 = MRV-196 Opus audit ring).
        #[arg(long, default_value_t = 1)]
        lines_per_thread: usize,
        /// Measurement duration in ms.
        #[arg(long, default_value_t = 1000)]
        duration_ms: u64,
        /// Minimum warmup duration in ms.
        #[arg(long, default_value_t = 400)]
        warmup_ms: u64,
        /// Max warmup waiting for schedutil cores to ramp.
        #[arg(long, default_value_t = 3500)]
        max_warmup_ms: u64,
        #[arg(long, default_value_t = 3.80)]
        min_ghz: f64,
        /// Pointer-chase hops per sample (0 = auto: 32 for LLC, 1 for DRAM).
        #[arg(long, default_value_t = 0)]
        chase_k: usize,
        #[arg(long, default_value_t = 1)]
        run_tag: u64,
        #[arg(long, default_value = BAR4_UC_PATH)]
        bar: String,
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card: String,
        #[arg(long, default_value = SYNC_ADDR)]
        sync: String,
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        with_card_pmu: bool,
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
        unsafe { asm!("rdtsc", out("eax") lo, out("edx") hi, options(nostack, preserves_flags, nomem)) };
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
        thread::sleep(Duration::from_millis(80));
        let c1 = rdtscp_lfence();
        let dt = t0.elapsed().as_secs_f64();
        (c1 - c0) as f64 / dt
    }

    fn calibrate_timer_floor_ticks() -> u64 {
        let mut samples = [0u64; 1024];
        for s in &mut samples {
            let a = rdtscp_lfence();
            let b = rdtscp_lfence();
            *s = b.saturating_sub(a);
        }
        samples.sort_unstable();
        samples[512]
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

    // ---- NUMA Node 1 2 MiB Hugepage Buffer ----
    struct NumaHugeBuf {
        ptr: *mut u8,
        len: usize,
    }
    unsafe impl Send for NumaHugeBuf {}
    unsafe impl Sync for NumaHugeBuf {}

    impl NumaHugeBuf {
        fn alloc_node1(bytes: usize) -> Result<Self, Box<dyn Error>> {
            const MPOL_BIND: usize = 2;
            const MPOL_MF_STRICT: usize = 1;
            const MPOL_MF_MOVE: usize = 2;
            const MPOL_F_NODE: usize = 1;
            const MPOL_F_ADDR: usize = 2;
            let len = bytes.div_ceil(HUGE_2M) * HUGE_2M;
            let mut ptr = unsafe {
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
                ptr = unsafe {
                    libc::mmap(
                        ptr::null_mut(),
                        len,
                        libc::PROT_READ | libc::PROT_WRITE,
                        libc::MAP_PRIVATE | libc::MAP_ANONYMOUS,
                        -1,
                        0,
                    )
                };
                if ptr == libc::MAP_FAILED {
                    return Err(format!("mmap({len}) failed").into());
                }
                unsafe { libc::madvise(ptr, len, libc::MADV_HUGEPAGE) };
            }
            let nodemask: [u64; 1] = [1u64 << 1];
            let ret = unsafe {
                libc::syscall(
                    libc::SYS_mbind,
                    ptr,
                    len,
                    MPOL_BIND,
                    nodemask.as_ptr(),
                    64usize,
                    MPOL_MF_STRICT | MPOL_MF_MOVE,
                )
            };
            if ret != 0 {
                let err = io::Error::last_os_error();
                unsafe { libc::munmap(ptr, len) };
                return Err(format!("mbind(node=1) failed: {err}").into());
            }
            let p = ptr.cast::<u8>();
            let mut off = 0usize;
            while off < len {
                unsafe { p.add(off).write_volatile(0) };
                off += 4096;
            }
            // Pin hugepages in RAM so Linux AutoNUMA never marks VMAs PROT_NONE during measurement.
            let _ = unsafe { libc::mlock(ptr, len) };
            for &check_off in &[0usize, len / 2, len - 4096] {
                let mut node: i32 = -1;
                let r = unsafe {
                    libc::syscall(
                        libc::SYS_get_mempolicy,
                        &mut node as *mut i32,
                        ptr::null_mut::<u64>(),
                        0usize,
                        p.add(check_off),
                        MPOL_F_NODE | MPOL_F_ADDR,
                    )
                };
                if r != 0 || node != 1 {
                    unsafe { libc::munmap(ptr, len) };
                    return Err(format!("NUMA verify failed at {check_off}: node={node}").into());
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
            unsafe { libc::munmap(self.ptr.cast::<libc::c_void>(), self.len) };
        }
    }

    // ---- 100% Non-Multiplexed SPR PMU ----
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

    const CHA_TOR_OCC_ALL: u64 = cha_cfg(0x36, 0xc001ffff);
    const CHA_TOR_INS_IA_WCILF: u64 = cha_cfg(0x35, 0xc867ff01);
    const CHA_TOR_INS_ALL: u64 = cha_cfg(0x35, 0xc001ffff);
    const IMC_CAS_COUNT_RD: u64 = 0xcf05;
    const IMC_CAS_COUNT_WR: u64 = 0xf005;
    const IMC_RPQ_OCC_PCH0: u64 = 0x0080;
    const M2P_RXC_INS_CHA_NCB: u64 = 0x0211;
    const M2P_RXC_NE_CHA_NCB: u64 = 0x0210;
    const M2P_CRD_REJECT_NCB: u64 = 0x1034;
    const IRP_TXS_REQ_OCC: u64 = 0x000c;
    const IRP_TXS_INS_NCS: u64 = 0x000e;
    const IIO_CPU_MEM_WR_TXN: u64 = iio_cfg(0xc1, 0x01, 0xff, 7);
    const IIO_CPU_MEM_WR_DW: u64 = iio_cfg(0xc0, 0x01, 0xff, 7);
    const IIO_OUTBOUND_CL_TO_IO: u64 = iio_cfg(0xd0, 0x08, 0xff, 7);
    const CORE_L3_HIT: u64 = 0x04d1;
    const CORE_L3_MISS: u64 = 0x20d1;
    const CORE_SB_STALLS: u64 = 0x08a2;

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

    fn read_pmu_type(name: &str) -> Result<u32, Box<dyn Error>> {
        Ok(fs::read_to_string(format!("/sys/bus/event_source/devices/{name}/type"))?.trim().parse()?)
    }

    struct CoreClockPmu {
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
                cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu)?,
                ref_cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, cpu)?,
                sb_stalls: if with_sb { Some(PerfCounter::open(PERF_TYPE_RAW, CORE_SB_STALLS, cpu)?) } else { None },
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

    struct VictimPrimPmu {
        inst: PerfCounter,
        l3_hit: PerfCounter,
        l3_miss: PerfCounter,
    }
    struct VictimPrimSnap {
        inst: Sample,
        l3_hit: Sample,
        l3_miss: Sample,
    }
    impl VictimPrimPmu {
        fn open(cpu: i32) -> io::Result<Self> {
            Ok(Self {
                inst: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu)?,
                l3_hit: PerfCounter::open(PERF_TYPE_RAW, CORE_L3_HIT, cpu)?,
                l3_miss: PerfCounter::open(PERF_TYPE_RAW, CORE_L3_MISS, cpu)?,
            })
        }
        fn snap(&self) -> VictimPrimSnap {
            VictimPrimSnap { inst: self.inst.read(), l3_hit: self.l3_hit.read(), l3_miss: self.l3_miss.read() }
        }
    }

    const MAX_CORES: usize = 16;
    struct HostPmu {
        cha_occ: Vec<PerfCounter>,
        cha_wcilf: Vec<PerfCounter>,
        cha_ins_all: Vec<PerfCounter>,
        cha_clk: PerfCounter,
        imc_rd: Vec<PerfCounter>,
        imc_wr: Vec<PerfCounter>,
        imc_rpq: Vec<PerfCounter>,
        imc_clk: PerfCounter,
        m2p_ins: PerfCounter,
        m2p_ne: PerfCounter,
        m2p_rej: PerfCounter,
        m2p_clk: PerfCounter,
        irp_occ: PerfCounter,
        irp_ins: PerfCounter,
        iio_wr_txn: PerfCounter,
        iio_wr_dw: PerfCounter,
        iio_out_cl: PerfCounter,
        iio_clk: PerfCounter,
        v_clks: Vec<CoreClockPmu>,
        v_prim: Option<VictimPrimPmu>,
        a_clks: Vec<CoreClockPmu>,
    }

    struct HostSnap {
        wall: Instant,
        cha_occ: [Sample; NUM_CHAS],
        cha_wcilf: [Sample; NUM_CHAS],
        cha_ins_all: [Sample; NUM_CHAS],
        cha_clk: Sample,
        imc_rd: [Sample; NUM_IMCS],
        imc_wr: [Sample; NUM_IMCS],
        imc_rpq: [Sample; NUM_IMCS],
        imc_clk: Sample,
        m2p_ins: Sample,
        m2p_ne: Sample,
        m2p_rej: Sample,
        m2p_clk: Sample,
        irp_occ: Sample,
        irp_ins: Sample,
        iio_wr_txn: Sample,
        iio_wr_dw: Sample,
        iio_out_cl: Sample,
        iio_clk: Sample,
        n_v: usize,
        v_clks: [CoreClockSnap; MAX_CORES],
        v_prim: Option<VictimPrimSnap>,
        n_a: usize,
        a_clks: [CoreClockSnap; MAX_CORES],
    }

    impl HostPmu {
        fn open(v_cpus: &[i32], a_cpus: &[i32]) -> Result<Self, Box<dyn Error>> {
            let mut cha_occ = Vec::with_capacity(NUM_CHAS);
            let mut cha_wcilf = Vec::with_capacity(NUM_CHAS);
            let mut cha_ins_all = Vec::with_capacity(NUM_CHAS);
            for i in 0..NUM_CHAS {
                let t = read_pmu_type(&format!("uncore_cha_{i}"))?;
                cha_occ.push(PerfCounter::open(t, CHA_TOR_OCC_ALL, SOCKET1_UNCORE_CPU)?);
                cha_wcilf.push(PerfCounter::open(t, CHA_TOR_INS_IA_WCILF, SOCKET1_UNCORE_CPU)?);
                cha_ins_all.push(PerfCounter::open(t, CHA_TOR_INS_ALL, SOCKET1_UNCORE_CPU)?);
            }
            let cha_clk = PerfCounter::open(read_pmu_type("uncore_cha_0")?, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let mut imc_rd = Vec::with_capacity(NUM_IMCS);
            let mut imc_wr = Vec::with_capacity(NUM_IMCS);
            let mut imc_rpq = Vec::with_capacity(NUM_IMCS);
            for i in 0..NUM_IMCS {
                let t = read_pmu_type(&format!("uncore_imc_{i}"))?;
                imc_rd.push(PerfCounter::open(t, IMC_CAS_COUNT_RD, SOCKET1_UNCORE_CPU)?);
                imc_wr.push(PerfCounter::open(t, IMC_CAS_COUNT_WR, SOCKET1_UNCORE_CPU)?);
                imc_rpq.push(PerfCounter::open(t, IMC_RPQ_OCC_PCH0, SOCKET1_UNCORE_CPU)?);
            }
            let imc_clk = PerfCounter::open(read_pmu_type("uncore_imc_0")?, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let m2p_t = read_pmu_type("uncore_m2pcie_1")?;
            let m2p_ins = PerfCounter::open(m2p_t, M2P_RXC_INS_CHA_NCB, SOCKET1_UNCORE_CPU)?;
            let m2p_ne = PerfCounter::open(m2p_t, M2P_RXC_NE_CHA_NCB, SOCKET1_UNCORE_CPU)?;
            let m2p_rej = PerfCounter::open(m2p_t, M2P_CRD_REJECT_NCB, SOCKET1_UNCORE_CPU)?;
            let m2p_clk = PerfCounter::open(m2p_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let irp_t = read_pmu_type("uncore_irp_1")?;
            let irp_occ = PerfCounter::open(irp_t, IRP_TXS_REQ_OCC, SOCKET1_UNCORE_CPU)?;
            let irp_ins = PerfCounter::open(irp_t, IRP_TXS_INS_NCS, SOCKET1_UNCORE_CPU)?;
            let iio_t = read_pmu_type("uncore_iio_1")?;
            let iio_wr_txn = PerfCounter::open(iio_t, IIO_CPU_MEM_WR_TXN, SOCKET1_UNCORE_CPU)?;
            let iio_wr_dw = PerfCounter::open(iio_t, IIO_CPU_MEM_WR_DW, SOCKET1_UNCORE_CPU)?;
            let iio_out_cl = PerfCounter::open(iio_t, IIO_OUTBOUND_CL_TO_IO, SOCKET1_UNCORE_CPU)?;
            let iio_clk = PerfCounter::open(iio_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let mut v_clks = Vec::new();
            for &c in v_cpus {
                v_clks.push(CoreClockPmu::open(c, false)?);
            }
            let v_prim = v_cpus.first().map(|&c| VictimPrimPmu::open(c)).transpose()?;
            let mut a_clks = Vec::new();
            for &c in a_cpus {
                a_clks.push(CoreClockPmu::open(c, true)?);
            }
            Ok(Self {
                cha_occ,
                cha_wcilf,
                cha_ins_all,
                cha_clk,
                imc_rd,
                imc_wr,
                imc_rpq,
                imc_clk,
                m2p_ins,
                m2p_ne,
                m2p_rej,
                m2p_clk,
                irp_occ,
                irp_ins,
                iio_wr_txn,
                iio_wr_dw,
                iio_out_cl,
                iio_clk,
                v_clks,
                v_prim,
                a_clks,
            })
        }

        fn snap(&self) -> HostSnap {
            let mut cha_occ = [Sample::default(); NUM_CHAS];
            let mut cha_wcilf = [Sample::default(); NUM_CHAS];
            let mut cha_ins_all = [Sample::default(); NUM_CHAS];
            for i in 0..NUM_CHAS {
                cha_occ[i] = self.cha_occ[i].read();
                cha_wcilf[i] = self.cha_wcilf[i].read();
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
            let n_v = self.v_clks.len().min(MAX_CORES);
            let mut v_clks = [CoreClockSnap::default(); MAX_CORES];
            for i in 0..n_v {
                v_clks[i] = self.v_clks[i].snap();
            }
            let n_a = self.a_clks.len().min(MAX_CORES);
            let mut a_clks = [CoreClockSnap::default(); MAX_CORES];
            for i in 0..n_a {
                a_clks[i] = self.a_clks[i].snap();
            }
            HostSnap {
                wall: Instant::now(),
                cha_occ,
                cha_wcilf,
                cha_ins_all,
                cha_clk: self.cha_clk.read(),
                imc_rd,
                imc_wr,
                imc_rpq,
                imc_clk: self.imc_clk.read(),
                m2p_ins: self.m2p_ins.read(),
                m2p_ne: self.m2p_ne.read(),
                m2p_rej: self.m2p_rej.read(),
                m2p_clk: self.m2p_clk.read(),
                irp_occ: self.irp_occ.read(),
                irp_ins: self.irp_ins.read(),
                iio_wr_txn: self.iio_wr_txn.read(),
                iio_wr_dw: self.iio_wr_dw.read(),
                iio_out_cl: self.iio_out_cl.read(),
                iio_clk: self.iio_clk.read(),
                n_v,
                v_clks,
                v_prim: self.v_prim.as_ref().map(|p| p.snap()),
                n_a,
                a_clks,
            }
        }

        fn min_core_ghz(&self, s0: &HostSnap, s1: &HostSnap) -> f64 {
            let mut min_g = 99.0f64;
            for i in 0..s0.n_v {
                let c = s1.v_clks[i].cycles.delta(s0.v_clks[i].cycles).val;
                let r = s1.v_clks[i].ref_cycles.delta(s0.v_clks[i].ref_cycles).val;
                if r > 0 {
                    min_g = min_g.min((c as f64 / r as f64) * (REF_HZ / 1e9));
                }
            }
            for i in 0..s0.n_a {
                let c = s1.a_clks[i].cycles.delta(s0.a_clks[i].cycles).val;
                let r = s1.a_clks[i].ref_cycles.delta(s0.a_clks[i].ref_cycles).val;
                if r > 0 {
                    min_g = min_g.min((c as f64 / r as f64) * (REF_HZ / 1e9));
                }
            }
            if min_g > 90.0 { 0.0 } else { min_g }
        }
    }

    // ---- Sync Client ----
    struct SyncClient {
        reader: BufReader<TcpStream>,
        writer: TcpStream,
    }
    impl SyncClient {
        fn connect(addr: &str) -> Result<Self, Box<dyn Error>> {
            let s = TcpStream::connect(addr)?;
            s.set_nodelay(true)?;
            s.set_read_timeout(Some(Duration::from_secs(10)))?;
            let r = s.try_clone()?;
            Ok(Self { reader: BufReader::new(r), writer: s })
        }
        fn cmd(&mut self, line: &str) -> Result<String, Box<dyn Error>> {
            writeln!(self.writer, "{line}")?;
            self.writer.flush()?;
            let mut resp = String::new();
            self.reader.read_line(&mut resp)?;
            let trimmed = resp.trim().to_string();
            if !trimmed.contains("\"ok\":true") {
                return Err(format!("card cmd '{line}' failed: {trimmed}").into());
            }
            Ok(trimmed)
        }
    }

    fn extract_json_obj(json: &str, key: &str) -> String {
        let pat = format!("\"{key}\":");
        if let Some(i) = json.find(&pat) {
            let rest = &json[i + pat.len()..];
            if rest.starts_with('{') {
                let mut depth = 0i32;
                for (j, ch) in rest.char_indices() {
                    if ch == '{' { depth += 1; } else if ch == '}' {
                        depth -= 1;
                        if depth == 0 { return rest[..=j].to_string(); }
                    }
                }
            }
        }
        "{}".to_string()
    }

    #[repr(C, align(64))]
    struct PaddedAtomicU64 {
        v: AtomicU64,
        _pad: [u8; 56],
    }
    impl PaddedAtomicU64 {
        fn new(val: u64) -> Self { Self { v: AtomicU64::new(val), _pad: [0; 56] } }
    }

    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct AlignedCell {
        w: [u64; 8],
    }

    // ---- Pointer Chase Victim ----
    #[derive(Default)]
    struct ChaseResult {
        total_hops: u64,
        total_chunks: u64,
        net_mean_ns: f64,
        p50_ns: f64,
        p90_ns: f64,
        p99_ns: f64,
        p999_ns: f64,
        p99_overflow: bool,
        p999_overflow: bool,
        max_ns: f64,
        overflow_chunks: u64,
        bad_nodes: u64,
        k_hops: usize,
        buf_mib: usize,
        victim_page_faults: u64,
    }

    fn build_hamiltonian_cycle(buf: &NumaHugeBuf, bytes: usize, seed: u64) -> usize {
        let n = bytes / 64;
        let mut perm: Vec<usize> = (0..n).collect();
        let mut rng = seed | 1;
        for i in (1..n).rev() {
            rng ^= rng << 13;
            rng ^= rng >> 7;
            rng ^= rng << 17;
            let j = (rng as usize) % i;
            perm.swap(i, j);
        }
        let base = buf.as_ptr();
        for idx in 0..n {
            let next_idx = perm[idx];
            let node_ptr = unsafe { base.add(idx * 64).cast::<u64>() };
            let next_ptr = unsafe { base.add(next_idx * 64) as u64 };
            unsafe {
                node_ptr.write(next_ptr);
                node_ptr.add(1).write((idx as u64) ^ NODE_SIG_MAGIC);
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
        if cur != base { bad += 1; }
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
        const FINE_BINS: usize = 4096;
        const COARSE_BINS: usize = 1024;
        let mut hist = vec![0u32; FINE_BINS];
        let mut tail_hist = vec![0u32; COARSE_BINS];
        for x in &mut hist {
            unsafe { ptr::write_volatile(x, 0) };
        }
        for x in &mut tail_hist {
            unsafe { ptr::write_volatile(x, 0) };
        }
        let _ = std::hint::black_box(calibrate_timer_floor_ticks());
        let _ = thread_page_faults();
        ready_count.fetch_add(1, Ordering::Release);

        while phase.load(Ordering::Acquire) == PHASE_WARMUP {
            for _ in 0..64 {
                p = unsafe { chase_8(p) };
            }
        }
        let floor_ticks = calibrate_timer_floor_ticks();
        calib_done.store(true, Ordering::Release);
        while phase.load(Ordering::Acquire) == PHASE_CALIB {
            for _ in 0..16 {
                p = unsafe { chase_8(p) };
            }
        }

        let bin_step_ns: f64 = if bytes > (64 << 20) { 1.0 } else { 0.25 };
        let coarse_step_ns: f64 = 16.0;
        let fine_cap_ns = (FINE_BINS as f64) * bin_step_ns;
        let coarse_cap_ns = fine_cap_ns + (COARSE_BINS as f64) * coarse_step_ns;
        let fine_bins_per_tick = (1e9 / bin_step_ns) / (tsc_hz * k_hops as f64);
        let qns_mul = (fine_bins_per_tick * ((1u64 << 32) as f64)) as u64;
        let coarse_ratio = (coarse_step_ns / bin_step_ns).round() as usize;
        let sub_8 = k_hops / 8;
        let rem = k_hops % 8;

        let mut overflow = 0u64;
        let mut total_chunks = 0u64;
        let mut total_ticks = 0u64;
        let mut max_ticks = 0u64;
        let mut xor_acc = 0u64;
        let pf0 = thread_page_faults();
        let mut prev = rdtscp_lfence();
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
            if dt > max_ticks { max_ticks = dt; }
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
        }
        let pf1 = thread_page_faults();
        std::hint::black_box(xor_acc);
        let total_hops = total_chunks * (k_hops as u64);
        let total_ns = (total_ticks as f64) * 1e9 / tsc_hz;
        let floor_ns_total = (total_chunks as f64) * (floor_ticks as f64) * 1e9 / tsc_hz;
        let net_mean_ns = if total_hops > 0 { (total_ns - floor_ns_total).max(0.0) / total_hops as f64 } else { 0.0 };
        let pct_from_hist = |q: f64| -> (f64, bool) {
            if total_chunks == 0 { return (0.0, false); }
            let target = ((total_chunks as f64) * q).ceil() as u64;
            let mut acc = 0u64;
            for (i, &c) in hist.iter().enumerate() {
                acc += c as u64;
                if acc >= target { return ((i as f64) * bin_step_ns, false); }
            }
            for (i, &c) in tail_hist.iter().enumerate() {
                acc += c as u64;
                if acc >= target { return (fine_cap_ns + (i as f64) * coarse_step_ns, false); }
            }
            (coarse_cap_ns, true)
        };
        let (p50_ns, _) = pct_from_hist(0.50);
        let (p90_ns, _) = pct_from_hist(0.90);
        let (p99_ns, p99_overflow) = pct_from_hist(0.99);
        let (p999_ns, p999_overflow) = pct_from_hist(0.999);
        let max_ns = (max_ticks.saturating_sub(floor_ticks) as f64) * 1e9 / (tsc_hz * k_hops as f64);
        let bad_nodes = verify_hamiltonian_cycle(buf, n);
        Ok(ChaseResult {
            total_hops,
            total_chunks,
            net_mean_ns,
            p50_ns,
            p90_ns,
            p99_ns,
            p999_ns,
            p99_overflow,
            p999_overflow,
            max_ns,
            overflow_chunks: overflow,
            bad_nodes,
            k_hops,
            buf_mib: bytes >> 20,
            victim_page_faults: pf1.saturating_sub(pf0),
        })
    }

    // ---- STREAM Triad Victim ----
    struct SpinBarrier {
        n_threads: usize,
        arrived: AtomicUsize,
        epoch: AtomicU64,
        phase_snap: AtomicU8,
    }
    impl SpinBarrier {
        fn new(n_threads: usize) -> Self {
            Self { n_threads, arrived: AtomicUsize::new(0), epoch: AtomicU64::new(0), phase_snap: AtomicU8::new(PHASE_WARMUP) }
        }
        #[inline(always)]
        fn wait_phase(&self, phase: &AtomicU8) -> u8 {
            if self.n_threads <= 1 { return phase.load(Ordering::Acquire); }
            let g = self.epoch.load(Ordering::Acquire);
            if self.arrived.fetch_add(1, Ordering::AcqRel) + 1 == self.n_threads {
                let p = phase.load(Ordering::Acquire);
                self.phase_snap.store(p, Ordering::Relaxed);
                self.arrived.store(0, Ordering::Relaxed);
                self.epoch.store(g + 1, Ordering::Release);
                p
            } else {
                while self.epoch.load(Ordering::Acquire) == g { std::hint::spin_loop(); }
                self.phase_snap.load(Ordering::Acquire)
            }
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
        mean_gb_s: f64,
        median_pass_gb_s: f64,
        verify_errors: u64,
        victim_page_faults: u64,
    }

    // ---- BAR4 Aggressor (supports sfence_batch = 0 for TRUE ZERO-SFENCE, and lines_per_thread = 1 or 4096) ----
    fn run_aggr_bar4(
        tid: usize,
        n_aggr: usize,
        cpu: i32,
        lease: &Lease,
        bar_path: &str,
        run_tag: u64,
        per_thread_mpps: f64,
        sfence_batch: usize,
        lines_per_thread: usize,
        tsc_hz: f64,
        phase: &AtomicU8,
        ready_count: &AtomicUsize,
        writes_out: &PaddedAtomicU64,
        pf_out: &PaddedAtomicU64,
    ) -> Result<(), String> {
        pin_to_cpu(cpu).map_err(|e| format!("pin cpu{cpu}: {e}"))?;
        let slot = lease.map_slot(bar_path).map_err(|e| format!("map_slot: {e}"))?;
        let base_line = tid * lines_per_thread;
        let base_ptr = unsafe { slot.as_ptr().add(SINK_BASE + base_line * 64) };
        let lpt = lines_per_thread.max(1);
        let mask = if lpt.is_power_of_two() { lpt - 1 } else { 0 };
        let is_pow2 = lpt.is_power_of_two();

        // Loop chunk size: if paced at low credit rate, emit 1 write per pacing tick (realistic credit return!);
        // if unthrottled & sfence_batch > 0, emit `sfence_batch` writes then sfence();
        // if unthrottled & sfence_batch == 0 (NO SFENCE!), emit 512 writes per phase-check with ZERO sfence()!
        let do_sfence = sfence_batch > 0;
        let chunk: usize = if per_thread_mpps > 0.0 {
            if do_sfence { sfence_batch.min(64) } else { 1 }
        } else if do_sfence {
            sfence_batch
        } else {
            512
        };

        let pace_ticks: u64 = if per_thread_mpps > 0.0 {
            ((chunk as f64) * tsc_hz / (per_thread_mpps * 1e6)).round().max(1.0) as u64
        } else {
            0
        };

        let mut cell = AlignedCell { w: [0, 0, run_tag, 0x2120_3333, 0x2120_4444, 0x2120_5555, 0x2120_6666, 0] };
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

        let mut in_meas = false;
        let mut pf0 = 0u64;
        loop {
            let ph = phase.load(Ordering::Acquire);
            if ph == PHASE_STOP {
                break;
            }
            if !in_meas && ph == PHASE_MEASURE {
                pf0 = thread_page_faults();
                in_meas = true;
            }
            if pace_ticks > 0 {
                let now = rdtsc_raw();
                if now < next_tsc {
                    std::hint::spin_loop();
                    continue;
                }
                // Zero-backlog pacer (fixes MRV-196 Opus finding #2: never burst catchup!)
                let adv = next_tsc.wrapping_add(pace_ticks);
                next_tsc = if now > adv { now.wrapping_add(pace_ticks) } else { adv };
            }
            slot.check()?;
            for _ in 0..chunk {
                let l = if is_pow2 { idx & mask } else { idx % lpt };
                let g_line = (base_line + l) as u64;
                let sig = seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ g_line ^ tag_magic;
                cell.w[0] = seq;
                cell.w[1] = sig;
                cell.w[7] = seq;
                unsafe { movdir64b(base_ptr.add(l << 6), &cell) };
                seq += 1;
                idx = idx.wrapping_add(1);
            }
            if do_sfence {
                sfence();
            }
            writes_out.v.store(seq - 1, Ordering::Relaxed);
        }
        let pf1 = thread_page_faults();
        if in_meas {
            pf_out.v.store(pf1.saturating_sub(pf0), Ordering::Relaxed);
        }
        // Final drain ONLY after PHASE_STOP so end-of-run VERIFY_BAR4 sees final writes
        sfence();
        Ok(())
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        // Bind default NUMA policy to Node 1 before any allocation or thread spawn so
        // Linux AutoNUMA (task_numa_work) skips our heap/stack/histogram VMAs.
        unsafe {
            const MPOL_BIND: usize = 2;
            let nodemask: [u64; 1] = [1u64 << 1];
            let _ = libc::syscall(libc::SYS_set_mempolicy, MPOL_BIND, nodemask.as_ptr(), 64usize);
        }
        let args = Args::parse();
        if args.aggr == AggrMode::Bar4 && !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }
        pin_to_cpu(COORD_CPU)?;
        let v_cpus = if args.victim == VictimKind::None { Vec::new() } else { parse_cpus(&args.victim_cpus)? };
        let a_cpus = if args.aggr == AggrMode::Idle { Vec::new() } else { parse_cpus(&args.aggr_cpus)? };
        let n_aggr = a_cpus.len();
        let total_sink_lines = n_aggr.saturating_mul(args.lines_per_thread);
        if args.aggr == AggrMode::Bar4 && total_sink_lines * 64 > MAX_SINK_BYTES {
            return Err(format!("total BAR4 sink lines ({total_sink_lines}) exceeds 3 MiB").into());
        }

        let tsc_hz = calibrate_tsc_hz();

        // Allocate victim buffers on Socket 1
        let chase_buf = match args.victim {
            VictimKind::LlcChase => {
                let b = NumaHugeBuf::alloc_node1(LLC_CHASE_BYTES)?;
                build_hamiltonian_cycle(&b, LLC_CHASE_BYTES, 0x2120_1111 ^ args.run_tag);
                Some(b)
            }
            VictimKind::DramChase => {
                let b = NumaHugeBuf::alloc_node1(DRAM_CHASE_BYTES)?;
                build_hamiltonian_cycle(&b, DRAM_CHASE_BYTES, 0x2120_2222 ^ args.run_tag);
                Some(b)
            }
            _ => None,
        };
        let stream_bufs = if args.victim == VictimKind::StreamTriad {
            let bytes = STREAM_ELEMS * 8;
            let a = NumaHugeBuf::alloc_node1(bytes)?;
            let b = NumaHugeBuf::alloc_node1(bytes)?;
            let c = NumaHugeBuf::alloc_node1(bytes)?;
            unsafe {
                let pa = a.as_ptr().cast::<f64>();
                let pb = b.as_ptr().cast::<f64>();
                let pc = c.as_ptr().cast::<f64>();
                for i in 0..STREAM_ELEMS {
                    pa.add(i).write(0.0);
                    pb.add(i).write(1.5);
                    pc.add(i).write(2.25);
                }
            }
            Some((a, b, c))
        } else {
            None
        };

        let mut sync = if args.aggr == AggrMode::Bar4 || args.with_card_pmu {
            Some(SyncClient::connect(&args.sync)?)
        } else {
            None
        };
        if args.aggr == AggrMode::Bar4 {
            sync.as_mut().unwrap().cmd(&format!("CLEAR_BAR4 {total_sink_lines}"))?;
        }
        let lease = if args.aggr == AggrMode::Bar4 {
            Some(Lease::acquire(&args.card)?)
        } else {
            None
        };

        let pmu = HostPmu::open(&v_cpus, &a_cpus)?;
        let phase = AtomicU8::new(PHASE_WARMUP);
        let ready_count = AtomicUsize::new(0);
        let calib_done = AtomicBool::new(false);
        let expected_ready = v_cpus.len() + a_cpus.len();
        let writes_arr: Vec<PaddedAtomicU64> = (0..n_aggr).map(|_| PaddedAtomicU64::new(0)).collect();
        let aggr_pf_arr: Vec<PaddedAtomicU64> = (0..n_aggr).map(|_| PaddedAtomicU64::new(0)).collect();

        let chase_k = if args.chase_k > 0 {
            args.chase_k
        } else if args.victim == VictimKind::DramChase {
            1
        } else {
            32
        };

        let mut chase_res: Option<ChaseResult> = None;
        let mut stream_res: Option<StreamResult> = None;
        let mut s0_opt: Option<HostSnap> = None;
        let mut s1_opt: Option<HostSnap> = None;
        let mut w0_sum = 0u64;
        let mut w1_sum = 0u64;
        let mut proc_pf = 0u64;
        let mut card_pmu_json = "{}".to_string();

        let stream_bar = SpinBarrier::new(v_cpus.len().max(1));
        let stream_passes = AtomicU64::new(0);
        let stream_v_pf = AtomicU64::new(0);
        let mut init_gbs = vec![1.0f64; 2048];
        std::hint::black_box(&init_gbs);
        init_gbs.clear();
        let stream_pass_gbs = std::sync::Mutex::new(init_gbs);
        let _ = unsafe { libc::mlockall(libc::MCL_CURRENT) };
        thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            let mut aggr_handles = Vec::new();
            let per_t_mpps = if args.target_mpps > 0.0 && n_aggr > 0 {
                args.target_mpps / n_aggr as f64
            } else {
                0.0
            };
            for (tid, &cpu) in a_cpus.iter().enumerate() {
                let phase_r = &phase;
                let ready_r = &ready_count;
                let w_out = &writes_arr[tid];
                let pf_out = &aggr_pf_arr[tid];
                let lease_r = lease.as_ref();
                let bar_r = args.bar.as_str();
                let aggr_mode = args.aggr;
                let sf_b = args.sfence_batch;
                let lpt = args.lines_per_thread;
                let rtag = args.run_tag;
                aggr_handles.push(scope.spawn(move || -> Result<(), String> {
                    match aggr_mode {
                        AggrMode::Idle => Ok(()),
                        AggrMode::L1Spin => {
                            pin_to_cpu(cpu).map_err(|e| format!("{e}"))?;
                            let _ = thread_page_faults();
                            ready_r.fetch_add(1, Ordering::Release);
                            let mut x = 0x1234_5678u64 ^ (tid as u64);
                            let mut in_m = false;
                            let mut pf0 = 0u64;
                            while phase_r.load(Ordering::Acquire) != PHASE_STOP {
                                if !in_m && phase_r.load(Ordering::Acquire) == PHASE_MEASURE {
                                    pf0 = thread_page_faults();
                                    in_m = true;
                                }
                                for _ in 0..256 {
                                    x = x.wrapping_mul(6364136223846793005).wrapping_add(1);
                                }
                            }
                            let pf1 = thread_page_faults();
                            if in_m { pf_out.v.store(pf1.saturating_sub(pf0), Ordering::Relaxed); }
                            std::hint::black_box(x);
                            Ok(())
                        }
                        AggrMode::Bar4 => run_aggr_bar4(
                            tid,
                            n_aggr,
                            cpu,
                            lease_r.unwrap(),
                            bar_r,
                            rtag,
                            per_t_mpps,
                            sf_b,
                            lpt,
                            tsc_hz,
                            phase_r,
                            ready_r,
                            w_out,
                            pf_out,
                        ),
                    }
                }));
            }

            // Spawn victim thread(s)
            let mut chase_handle = None;
            let mut stream_handles = Vec::new();

            if let Some(buf) = &chase_buf {
                let cpu = v_cpus[0];
                let bytes = if args.victim == VictimKind::LlcChase { LLC_CHASE_BYTES } else { DRAM_CHASE_BYTES };
                let phase_r = &phase;
                let ready_r = &ready_count;
                let calib_r = &calib_done;
                chase_handle = Some(scope.spawn(move || {
                    run_chase_victim(cpu, buf, bytes, chase_k, phase_r, ready_r, calib_r, tsc_hz)
                        .map_err(|e| e.to_string())
                }));
            } else if let Some((a, b, c)) = &stream_bufs {
                let n_t = v_cpus.len();
                let chunk = (STREAM_ELEMS / n_t) & !15;
                let bytes_per_pass = (chunk * n_t * 3 * 8) as f64;
                for (tidx, &cpu) in v_cpus.iter().enumerate() {
                    let phase_r = &phase;
                    let ready_r = &ready_count;
                    let bar_r = &stream_bar;
                    let passes_r = &stream_passes;
                    let vpf_r = &stream_v_pf;
                    let gbs_r = &stream_pass_gbs;
                    let pa = unsafe { a.as_ptr().cast::<f64>().add(tidx * chunk) } as usize;
                    let pb = unsafe { b.as_ptr().cast::<f64>().add(tidx * chunk) } as usize;
                    let pc = unsafe { c.as_ptr().cast::<f64>().add(tidx * chunk) } as usize;
                    stream_handles.push(scope.spawn(move || -> Result<(), String> {
                        pin_to_cpu(cpu).map_err(|e| format!("{e}"))?;
                        let _ = thread_page_faults();
                        ready_r.fetch_add(1, Ordering::Release);
                        let mut in_m = false;
                        let mut pf0 = 0u64;
                        let mut pass_t0 = Instant::now();
                        loop {
                            let ph = bar_r.wait_phase(phase_r);
                            if ph == PHASE_STOP {
                                break;
                            }
                            if !in_m && ph == PHASE_MEASURE {
                                pf0 = thread_page_faults();
                                in_m = true;
                            }
                            if tidx == 0 && ph == PHASE_MEASURE {
                                pass_t0 = Instant::now();
                            }
                            unsafe { stream_triad_avx2(pa as *mut f64, pb as *const f64, pc as *const f64, 3.0, chunk) };
                            let ph_after = bar_r.wait_phase(phase_r);
                            if tidx == 0 && ph == PHASE_MEASURE {
                                let dt = pass_t0.elapsed().as_secs_f64().max(1e-9);
                                passes_r.fetch_add(1, Ordering::Relaxed);
                                gbs_r.lock().unwrap().push(bytes_per_pass / (dt * 1e9));
                            }
                            if ph_after == PHASE_STOP {
                                break;
                            }
                        }
                        let pf1 = thread_page_faults();
                        if in_m {
                            vpf_r.fetch_add(pf1.saturating_sub(pf0), Ordering::Relaxed);
                        }
                        Ok(())
                    }));
                }
            }

            // Wait for all workers ready
            while ready_count.load(Ordering::Acquire) < expected_ready {
                thread::sleep(Duration::from_millis(1));
            }
            // Adaptive warmup wait for core frequency ramp
            let w0 = Instant::now();
            thread::sleep(Duration::from_millis(args.warmup_ms));
            if args.min_ghz > 0.0 && expected_ready > 0 {
                while w0.elapsed() < Duration::from_millis(args.max_warmup_ms) {
                    let a = pmu.snap();
                    thread::sleep(Duration::from_millis(60));
                    let b = pmu.snap();
                    if pmu.min_core_ghz(&a, &b) >= args.min_ghz {
                        break;
                    }
                }
            }
            // Calibrate timer floor for chase victims BEFORE measurement
            if chase_buf.is_some() {
                phase.store(PHASE_CALIB, Ordering::Release);
                while !calib_done.load(Ordering::Acquire) {
                    thread::sleep(Duration::from_millis(1));
                }
            }

            if args.with_card_pmu {
                if let Some(sc) = &mut sync {
                    let _ = sc.cmd("PMU_START")?;
                }
            }
            let ppf0 = process_page_faults();
            w0_sum = writes_arr.iter().map(|w| w.v.load(Ordering::Relaxed)).sum();
            let s0 = pmu.snap();
            phase.store(PHASE_MEASURE, Ordering::Release);
            thread::sleep(Duration::from_millis(args.duration_ms));
            let s1 = pmu.snap();
            w1_sum = writes_arr.iter().map(|w| w.v.load(Ordering::Relaxed)).sum();
            let ppf1 = process_page_faults();
            phase.store(PHASE_STOP, Ordering::Release);

            if args.with_card_pmu {
                if let Some(sc) = &mut sync {
                    card_pmu_json = extract_json_obj(&sc.cmd("PMU_STOP")?, "pmu");
                }
            }
            s0_opt = Some(s0);
            s1_opt = Some(s1);
            proc_pf = ppf1.saturating_sub(ppf0);

            for h in aggr_handles {
                h.join().map_err(|_| "aggr panic")??;
            }
            if let Some(h) = chase_handle {
                chase_res = Some(h.join().map_err(|_| "chase panic")??);
            }
            for h in stream_handles {
                h.join().map_err(|_| "stream panic")??;
            }
            if let Some((a, b, c)) = &stream_bufs {
                let n_t = v_cpus.len();
                let chunk = (STREAM_ELEMS / n_t) & !15;
                let used = chunk * n_t;
                let pa = a.as_ptr().cast::<f64>();
                let pb = b.as_ptr().cast::<f64>();
                let pc = c.as_ptr().cast::<f64>();
                let mut verify_errors = 0u64;
                for i in (0..used).step_by(257) {
                    let got = unsafe { *pa.add(i) };
                    let want = unsafe { *pb.add(i) + 3.0 * *pc.add(i) };
                    if (got - want).abs() > 1e-6 {
                        verify_errors += 1;
                    }
                }
                let mut gbs = stream_pass_gbs.lock().unwrap().clone();
                let passes = stream_passes.load(Ordering::Relaxed);
                let mean_gb_s = if !gbs.is_empty() { gbs.iter().sum::<f64>() / gbs.len() as f64 } else { 0.0 };
                gbs.sort_by(|x, y| x.total_cmp(y));
                let median_pass_gb_s = if !gbs.is_empty() { gbs[gbs.len() / 2] } else { 0.0 };
                stream_res = Some(StreamResult {
                    passes,
                    mean_gb_s,
                    median_pass_gb_s,
                    verify_errors,
                    victim_page_faults: stream_v_pf.load(Ordering::Relaxed),
                });
            }
            Ok(())
        })?;

        // Verify BAR4 integrity on card
        let verify_json = if args.aggr == AggrMode::Bar4 {
            let mut cmd = format!("VERIFY_BAR4 {} {} {}", args.run_tag, n_aggr, args.lines_per_thread);
            for w in &writes_arr {
                cmd.push_str(&format!(" {}", w.v.load(Ordering::Relaxed)));
            }
            sync.as_mut().unwrap().cmd(&cmd)?
        } else {
            "{\"ok\":true,\"checked\":0,\"zero\":0,\"bad\":0,\"torn\":0}".to_string()
        };

        let s0 = s0_opt.unwrap();
        let s1 = s1_opt.unwrap();
        let wall_s = s1.wall.saturating_duration_since(s0.wall).as_secs_f64().max(1e-9);
        let mut min_pct = 100.0f64;
        let mut d = |a: Sample, b: Sample| -> u64 {
            let x = b.delta(a);
            min_pct = min_pct.min(x.pct());
            x.val
        };
        let mut cha_occ_sum = 0u64;
        let mut cha_wcilf_sum = 0u64;
        let mut cha_all_sum = 0u64;
        for i in 0..NUM_CHAS {
            cha_occ_sum += d(s0.cha_occ[i], s1.cha_occ[i]);
            cha_wcilf_sum += d(s0.cha_wcilf[i], s1.cha_wcilf[i]);
            cha_all_sum += d(s0.cha_ins_all[i], s1.cha_ins_all[i]);
        }
        let cha_clk = d(s0.cha_clk, s1.cha_clk).max(1);
        let cha_occ_total = cha_occ_sum as f64 / cha_clk as f64;
        let cha_ghz = cha_clk as f64 / (wall_s * 1e9);

        let mut imc_rd_sum = 0u64;
        let mut imc_wr_sum = 0u64;
        let mut imc_rpq_sum = 0u64;
        for i in 0..NUM_IMCS {
            imc_rd_sum += d(s0.imc_rd[i], s1.imc_rd[i]);
            imc_wr_sum += d(s0.imc_wr[i], s1.imc_wr[i]);
            imc_rpq_sum += d(s0.imc_rpq[i], s1.imc_rpq[i]);
        }
        let imc_clk = d(s0.imc_clk, s1.imc_clk).max(1);
        let imc_rd_gbs = (imc_rd_sum as f64 * 64.0) / (wall_s * 1e9);
        let imc_wr_gbs = (imc_wr_sum as f64 * 64.0) / (wall_s * 1e9);
        let imc_rpq_occ = imc_rpq_sum as f64 / imc_clk as f64;

        let m2p_ins = d(s0.m2p_ins, s1.m2p_ins);
        let m2p_ne = d(s0.m2p_ne, s1.m2p_ne);
        let m2p_rej = d(s0.m2p_rej, s1.m2p_rej);
        let m2p_clk = d(s0.m2p_clk, s1.m2p_clk).max(1);
        let m2p_ne_pct = (m2p_ne as f64 / (2.0 * m2p_clk as f64)) * 100.0;

        let irp_occ = d(s0.irp_occ, s1.irp_occ);
        let irp_ins = d(s0.irp_ins, s1.irp_ins);
        let irp_occ_wall = irp_occ as f64 / (wall_s * 1e9);

        let iio_wr_txn = d(s0.iio_wr_txn, s1.iio_wr_txn);
        let iio_wr_dw = d(s0.iio_wr_dw, s1.iio_wr_dw);
        let iio_out_cl = d(s0.iio_out_cl, s1.iio_out_cl);
        let iio_clk = d(s0.iio_clk, s1.iio_clk);
        let iio_ghz = iio_clk as f64 / (wall_s * 1e9);

        let mut v_ghz_min = 99.0f64;
        for i in 0..s0.n_v {
            let cyc = d(s0.v_clks[i].cycles, s1.v_clks[i].cycles);
            let rcyc = d(s0.v_clks[i].ref_cycles, s1.v_clks[i].ref_cycles);
            if rcyc > 0 { v_ghz_min = v_ghz_min.min((cyc as f64 / rcyc as f64) * (REF_HZ / 1e9)); }
        }
        if v_ghz_min > 90.0 { v_ghz_min = 0.0; }

        let mut a_ghz_min = 99.0f64;
        let mut a_sb_total = 0u64;
        for i in 0..s0.n_a {
            let cyc = d(s0.a_clks[i].cycles, s1.a_clks[i].cycles);
            let rcyc = d(s0.a_clks[i].ref_cycles, s1.a_clks[i].ref_cycles);
            if rcyc > 0 { a_ghz_min = a_ghz_min.min((cyc as f64 / rcyc as f64) * (REF_HZ / 1e9)); }
            if let (Some(sb0), Some(sb1)) = (s0.a_clks[i].sb_stalls, s1.a_clks[i].sb_stalls) {
                a_sb_total += d(sb0, sb1);
            }
        }
        if a_ghz_min > 90.0 { a_ghz_min = 0.0; }

        let (v_l3_hit, v_l3_miss) = if let (Some(p0), Some(p1)) = (&s0.v_prim, &s1.v_prim) {
            let _ = d(p0.inst, p1.inst);
            (d(p0.l3_hit, p1.l3_hit), d(p0.l3_miss, p1.l3_miss))
        } else {
            (0, 0)
        };
        let v_l3_miss_pct = if v_l3_hit + v_l3_miss > 0 {
            v_l3_miss as f64 * 100.0 / (v_l3_hit + v_l3_miss) as f64
        } else {
            0.0
        };

        let meas_writes = w1_sum.saturating_sub(w0_sum);
        let actual_mpps = meas_writes as f64 / (wall_s * 1e6);
        let aggr_pf: u64 = aggr_pf_arr.iter().map(|x| x.v.load(Ordering::Relaxed)).sum();

        let victim_json = if let Some(c) = chase_res {
            format!(
                "{{\"kind\":\"{}\",\"k_hops\":{},\"buf_mib\":{},\"total_hops\":{},\"total_chunks\":{},\"net_mean_ns\":{:.4},\"p50_ns\":{:.4},\"p90_ns\":{:.4},\"p99_ns\":{:.4},\"p999_ns\":{:.4},\"p99_overflow\":{},\"p999_overflow\":{},\"max_ns\":{:.3},\"overflow_chunks\":{},\"bad_nodes\":{},\"page_faults\":{}}}",
                args.victim.as_str(),
                c.k_hops,
                c.buf_mib,
                c.total_hops,
                c.total_chunks,
                c.net_mean_ns,
                c.p50_ns,
                c.p90_ns,
                c.p99_ns,
                c.p999_ns,
                c.p99_overflow,
                c.p999_overflow,
                c.max_ns,
                c.overflow_chunks,
                c.bad_nodes,
                c.victim_page_faults,
            )
        } else if let Some(s) = stream_res {
            format!(
                "{{\"kind\":\"stream-triad\",\"passes\":{},\"mean_gb_s\":{:.4},\"median_pass_gb_s\":{:.4},\"verify_errors\":{},\"page_faults\":{}}}",
                s.passes, s.mean_gb_s, s.median_pass_gb_s, s.verify_errors, s.victim_page_faults
            )
        } else {
            "{\"kind\":\"none\",\"page_faults\":0}".to_string()
        };

        println!(
            "{{\"victim\":\"{}\",\"aggr\":\"{}\",\"victim_cpus\":\"{}\",\"aggr_cpus\":\"{}\",\"n_aggr\":{n_aggr},\"target_mpps\":{:.3},\"actual_mpps\":{actual_mpps:.4},\"meas_writes\":{meas_writes},\"sfence_batch\":{},\"lines_per_thread\":{},\"wall_s\":{wall_s:.6},\"v_ghz_min\":{v_ghz_min:.4},\"a_ghz_min\":{a_ghz_min:.4},\"a_sb_stalls\":{a_sb_total},\"v_l3_miss_pct\":{v_l3_miss_pct:.3},\"cha_occ_all\":{cha_occ_total:.3},\"cha_wcilf_ins\":{cha_wcilf_sum},\"cha_ins_all\":{cha_all_sum},\"cha_ghz\":{cha_ghz:.4},\"imc_rd_gbs\":{imc_rd_gbs:.4},\"imc_wr_gbs\":{imc_wr_gbs:.4},\"imc_rpq_occ\":{imc_rpq_occ:.3},\"m2p_ins\":{m2p_ins},\"m2p_ne_pct\":{m2p_ne_pct:.3},\"m2p_rej\":{m2p_rej},\"irp_occ_wall\":{irp_occ_wall:.3},\"irp_ins\":{irp_ins},\"iio_wr_txn\":{iio_wr_txn},\"iio_wr_dw\":{iio_wr_dw},\"iio_out_cl\":{iio_out_cl},\"iio_ghz\":{iio_ghz:.4},\"host_pmu_min_pct\":{min_pct:.2},\"aggr_page_faults\":{aggr_pf},\"proc_page_faults\":{proc_pf},\"victim_res\":{victim_json},\"verify\":{verify_json},\"card_pmu\":{card_pmu_json}}}",
            args.victim.as_str(),
            args.aggr.as_str(),
            args.victim_cpus,
            args.aggr_cpus,
            args.target_mpps,
            args.sfence_batch,
            args.lines_per_thread,
        );
        Ok(())
    }
}
