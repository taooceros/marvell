//! Host-side DPDK standard-SDP benchmark driver for MRV-212 (E1):
//! - Control A: 5 credit-signaling modes (`off`, `bar4-nofence`, `bar4-sfence`,
//!   `bar4-uc64`, `hostmem-dpi`) CPU cost (`cycles/batch`, `cycles/pkt` with
//!   empty-poll-free `app_work` accounting + pure signaling cross-check),
//!   zero-load `W=1` latency increment, direct control-loop RTT `tau_rtt`, and
//!   100GbE BDP lower bound.
//! - Control C: Static invariant-IOVA ring vs dynamic `mbuf` rebind (aligned
//!   post-refill and unaligned B=32 racing ahead of SLIST_DBELL) zero-error
//!   data-safety verification with poison check.
//! - Control D: Core ablation of native SDP `SLIST_DBELL / WMARK` backpressure
//!   + card elastic FIFO vs explicit NO-SFENCE `BAR4` credit gating (D1 single
//!   queue pause/window/stall + D2 dual-queue slow-core NIX/SDP HoL blocking).
//!
//! Safety invariants:
//! - BAR4 slot 15 is ONLY accessed under an active MRV-26 lease (`Lease::acquire`
//!   + `LeasedSlot::check()`).
//! - For `hostmem-dpi`, host NEVER drops its `Memzone` or exits EAL until card
//!   `RUN_STOP` confirms all in-flight DPI reads have drained.
//! - 100% non-multiplexed Host Core + Socket-1 CHA/IMC/M2PCIe1/IRP1/IIO1 PMU
//!   and CN106 TAD/DDR5 PMU.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv212_host runs on the x86_64 host");
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
    use std::net::TcpStream;
    use std::os::fd::RawFd;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, Ordering};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, Mempool, Memzone, Port, RxQueue, rdtsc, rdtsc_after_loads};
    use testbed::lease::{self, Lease, LeasedSlot};
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};
    use mrv212::{
        CreditLine, FrameCheck, SYNC_ADDR, credit_line_offset, poison_frame, verify_frame,
        write_frame,
    };
    use testbed::Mac;

    const PORT: u16 = 0;
    const BAR4_UC_PATH: &str = "/sys/bus/pci/devices/0000:99:00.0/resource4";
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const COORD_CPU: i32 = 36;
    const NUM_CHAS: usize = 32;
    const NUM_IMCS: usize = 8;
    const REF_HZ: f64 = 2.2e9;

    const PHASE_WARMUP: u8 = 0;
    const PHASE_MEASURE: u8 = 1;
    const PHASE_DRAIN: u8 = 2;
    const PHASE_STOP_DRAIN: u8 = 3;

    struct StopOnDrop<'a>(&'a AtomicBool);
    impl Drop for StopOnDrop<'_> {
        fn drop(&mut self) {
            self.0.store(true, Ordering::Release);
        }
    }

    fn pin_thread_to_cpu(cpu: i32) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu as usize, &mut set);
            let _ = libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set);
        }
    }

    fn sleep_abortable(dur: Duration, abort: &AtomicBool) -> bool {
        let t0 = Instant::now();
        while t0.elapsed() < dur {
            if abort.load(Ordering::Acquire) {
                return false;
            }
            let rem = dur.saturating_sub(t0.elapsed());
            thread::sleep(rem.min(Duration::from_millis(5)));
        }
        !abort.load(Ordering::Acquire)
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Mode {
        CalibrateWork,
        CalibrateEmptyRx,
        MicroSignal,
        RxStream,
        CreditRtt,
        PingPong,
        DualQueue,
    }

    impl Mode {
        #[allow(dead_code)]
        fn as_str(self) -> &'static str {
            match self {
                Self::CalibrateWork => "calibrate-work",
                Self::CalibrateEmptyRx => "calibrate-empty-rx",
                Self::MicroSignal => "micro-signal",
                Self::RxStream => "rx-stream",
                Self::CreditRtt => "credit-rtt",
                Self::PingPong => "pingpong",
                Self::DualQueue => "dual-queue",
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum CreditMode {
        Off,
        Bar4Nofence,
        Bar4Sfence,
        Bar4Uc64,
        HostmemDpi,
        NativeBp,
    }

    impl CreditMode {
        fn as_str(self) -> &'static str {
            match self {
                Self::Off => "off",
                Self::Bar4Nofence => "bar4-nofence",
                Self::Bar4Sfence => "bar4-sfence",
                Self::Bar4Uc64 => "bar4-uc64",
                Self::HostmemDpi => "hostmem-dpi",
                Self::NativeBp => "native-bp",
            }
        }
        fn uses_bar4(self) -> bool {
            matches!(self, Self::Bar4Nofence | Self::Bar4Sfence | Self::Bar4Uc64)
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum RecycleMode {
        Dynamic,
        StaticInplace,
    }

    impl RecycleMode {
        fn as_str(self) -> &'static str {
            match self {
                Self::Dynamic => "dynamic",
                Self::StaticInplace => "static-inplace",
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum)]
        mode: Mode,
        #[arg(long, value_enum, default_value_t = CreditMode::Bar4Nofence)]
        credit: CreditMode,
        /// Ethernet frame size (64..=1514).
        #[arg(long, default_value_t = 64)]
        frame_size: u16,
        /// Credit return batch size B (packets per credit update).
        #[arg(long, default_value_t = 32)]
        credit_batch: u64,
        /// Logical credit window R_eff in packets (per queue).
        #[arg(long, default_value_t = 256)]
        r_eff: u64,
        /// Optional byte-granular credit window R_eff_bytes (0 = use packet R_eff).
        #[arg(long, default_value_t = 0)]
        r_eff_bytes: u64,
        /// Host SDP RX descriptor count (256 or 512 with allow_small_oq=1, or 2048).
        #[arg(long, default_value_t = 512)]
        rx_desc: u16,
        /// Host SDP TX descriptor count (2048 minimum on octeon_ep IQ).
        #[arg(long, default_value_t = 2048)]
        tx_desc: u16,
        /// Tight hot RX mempool size per queue (e.g. 512 for static-inplace, 1023 for dynamic).
        #[arg(long, default_value_t = 1023)]
        pool_size: u32,
        /// Mempool local cache size.
        #[arg(long, default_value_t = 128)]
        pool_cache: u32,
        /// Offered card send rate in Mpps (0 = card max speed).
        #[arg(long, default_value_t = 0.0)]
        target_mpps: f64,
        /// Relative Q1 rate fraction in dual-queue mode (default 1.0 = equal).
        #[arg(long, default_value_t = 1.0)]
        q1_rate_frac: f64,
        /// Enable card elastic software FIFO (Control D).
        #[arg(long)]
        use_fifo: bool,
        /// Card elastic FIFO capacity per queue (in packets).
        #[arg(long, default_value_t = 131072)]
        fifo_cap: usize,
        /// Enable empty-poll register-only app_work accounting (Control A1).
        #[arg(long)]
        app_work: bool,
        /// Pre-calibrated cycles per empty rx+app_work poll (from --mode calibrate-empty-rx).
        #[arg(long, default_value_t = 0.0)]
        work_cycles_per_step: f64,
        /// Inner loop store-buffer iterations in micro-signal mode (default 512 -> ~300 ns realistic B=32 spacing).
        #[arg(long, default_value_t = 512)]
        body_iters: usize,
        /// Recycle mode for Control C: dynamic vs static-inplace.
        #[arg(long, value_enum, default_value_t = RecycleMode::Dynamic)]
        recycle: RecycleMode,
        #[arg(long)]
        poison: bool,
        /// Only publish BAR4 credit for packets already refilled (multiple of 64) after rx_burst refill.
        #[arg(long)]
        align_refill: bool,
        /// Periodic host consumer pause period in ms (0 = disabled; Control D1).
        #[arg(long, default_value_t = 0)]
        pause_every_ms: u64,
        /// Periodic host consumer pause duration in us (Control D1).
        #[arg(long, default_value_t = 1000)]
        pause_dur_us: u64,
        /// Q1 pause period in ms for dual-queue mode (Control D2).
        #[arg(long, default_value_t = 10)]
        q1_period_ms: u64,
        /// Q1 pause duration in us for dual-queue mode (Control D2).
        #[arg(long, default_value_t = 8000)]
        q1_pause_us: u64,
        /// Measurement duration in ms (stream / dual-queue / calibrate-work / micro-signal).
        #[arg(long, default_value_t = 1000)]
        duration_ms: u64,
        /// Warmup duration in ms (stream / dual-queue).
        #[arg(long, default_value_t = 300)]
        warmup_ms: u64,
        /// Measured rounds (credit-rtt / pingpong / micro-signal).
        #[arg(long, default_value_t = 50_000)]
        rounds: u64,
        /// Warmup rounds (credit-rtt / pingpong).
        #[arg(long, default_value_t = 5_000)]
        warmup_rounds: u64,
        /// Unique run generation id.
        #[arg(long, default_value_t = 1)]
        run_gen: u64,
        /// Primary host CPU id for PMU (default 40).
        #[arg(long, default_value_t = 40)]
        host_cpu: i32,
        /// Secondary host CPU id for dual-queue PMU (default 41).
        #[arg(long, default_value_t = 41)]
        q1_cpu: i32,
        /// BAR4 sysfs resource path.
        #[arg(long, default_value = BAR4_UC_PATH)]
        bar: String,
        /// MRV-26 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card: String,
        /// Card sync/PMU TCP address.
        #[arg(long, default_value = SYNC_ADDR)]
        sync: String,
        /// Collect Card TAD/DDR5 PMU via sync server.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        with_card_pmu: bool,
    }

    // ---- Register-only app_work_step (zero memory/IO, ~300 ns on 3.9 GHz SPR) ----
    #[inline(never)]
    fn app_work_step(mut state: u64) -> u64 {
        // 128 dependent integer ALU ops (fixed IPC, zero memory traffic)
        let mut s2 = state ^ 0x9e37_79b9_7f4a_7c15;
        for _ in 0..64 {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            s2 = s2.rotate_left(13) ^ state;
        }
        std::hint::black_box(state ^ s2)
    }

    // ---- Thread-level minor+major page fault reader ----
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

    // ---- 100% non-multiplexed SPR Core + Uncore PMU ----
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
    const CHA_TOR_INS_IO_ITOM: u64 = cha_cfg(0x35, 0xc867ff04);
    const IMC_CAS_COUNT_RD: u64 = 0xcf05;
    const IMC_CAS_COUNT_WR: u64 = 0xf005;
    const M2P_RXC_INS_CHA_NCB: u64 = 0x0211;
    const M2P_RXC_NE_CHA_NCB: u64 = 0x0210;
    const IRP_TXS_REQ_OCC: u64 = 0x000c;
    const IRP_TXS_INS_NCS: u64 = 0x000e;

    // Strictly 4 counters on uncore_iio_1 -> 100.00% enabled!
    const IIO_CPU_MEM_WR_TXN: u64 = iio_cfg(0xc1, 0x01, 0xff, 7);
    const IIO_DMA_WR_TXN: u64 = iio_cfg(0x84, 0x01, 0xff, 7);
    const IIO_DMA_RD_TXN: u64 = iio_cfg(0x84, 0x04, 0xff, 7);

    // Core raw counters (strictly <= 5 programmable counters on SPR!)
    const CORE_LLC_REF: u64 = 0x4f2e;
    const CORE_LLC_MISS: u64 = 0x412e;
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

    fn read_pmu_type(name: &str) -> Result<u32, Box<dyn Error>> {
        let p = format!("/sys/bus/event_source/devices/{name}/type");
        Ok(fs::read_to_string(&p)?.trim().parse()?)
    }

    struct CorePmu {
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        inst: PerfCounter,
        llc_ref: PerfCounter,
        llc_miss: PerfCounter,
        l3_hit: PerfCounter,
        l3_miss: PerfCounter,
        sb_stalls: PerfCounter,
    }

    #[derive(Clone, Copy, Default)]
    struct CoreSnap {
        cycles: Sample,
        ref_cycles: Sample,
        inst: Sample,
        llc_ref: Sample,
        llc_miss: Sample,
        l3_hit: Sample,
        l3_miss: Sample,
        sb_stalls: Sample,
    }

    impl CorePmu {
        fn open(cpu: i32) -> io::Result<Self> {
            Ok(Self {
                cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu)?,
                ref_cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, cpu)?,
                inst: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu)?,
                llc_ref: PerfCounter::open(PERF_TYPE_RAW, CORE_LLC_REF, cpu)?,
                llc_miss: PerfCounter::open(PERF_TYPE_RAW, CORE_LLC_MISS, cpu)?,
                l3_hit: PerfCounter::open(PERF_TYPE_RAW, CORE_L3_HIT, cpu)?,
                l3_miss: PerfCounter::open(PERF_TYPE_RAW, CORE_L3_MISS, cpu)?,
                sb_stalls: PerfCounter::open(PERF_TYPE_RAW, CORE_SB_STALLS, cpu)?,
            })
        }
        fn snap(&self) -> CoreSnap {
            CoreSnap {
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                inst: self.inst.read(),
                llc_ref: self.llc_ref.read(),
                llc_miss: self.llc_miss.read(),
                l3_hit: self.l3_hit.read(),
                l3_miss: self.l3_miss.read(),
                sb_stalls: self.sb_stalls.read(),
            }
        }
    }

    struct HostPmu {
        core: CorePmu,
        cha_occ: Vec<PerfCounter>,
        cha_wcilf: Vec<PerfCounter>,
        cha_io_itom: Vec<PerfCounter>,
        cha_clk: PerfCounter,
        imc_rd: Vec<PerfCounter>,
        imc_wr: Vec<PerfCounter>,
        m2p_ins: PerfCounter,
        m2p_ne: PerfCounter,
        m2p_clk: PerfCounter,
        irp_occ: PerfCounter,
        irp_ins: PerfCounter,
        iio_cpu_wr: PerfCounter,
        iio_dma_wr: PerfCounter,
        iio_dma_rd: PerfCounter,
        iio_clk: PerfCounter,
    }

    struct HostSnap {
        wall: Instant,
        core: CoreSnap,
        cha_occ: [Sample; NUM_CHAS],
        cha_wcilf: [Sample; NUM_CHAS],
        cha_io_itom: [Sample; NUM_CHAS],
        cha_clk: Sample,
        imc_rd: [Sample; NUM_IMCS],
        imc_wr: [Sample; NUM_IMCS],
        m2p_ins: Sample,
        m2p_ne: Sample,
        m2p_clk: Sample,
        irp_occ: Sample,
        irp_ins: Sample,
        iio_cpu_wr: Sample,
        iio_dma_wr: Sample,
        iio_dma_rd: Sample,
        iio_clk: Sample,
    }

    struct HostPmuDelta {
        wall_s: f64,
        core_ghz: f64,
        cycles: u64,
        inst: u64,
        llc_ref: u64,
        llc_miss: u64,
        l3_hit: u64,
        l3_miss: u64,
        sb_stalls: u64,
        cha_occ_mean: f64,
        cha_wcilf_ins: u64,
        cha_io_itom_ins: u64,
        cha_ghz: f64,
        imc_rd_lines: u64,
        imc_wr_lines: u64,
        imc_rd_gbs: f64,
        imc_wr_gbs: f64,
        m2p_ins: u64,
        m2p_ne_pct: f64,
        irp_occ_wall: f64,
        irp_ins: u64,
        iio_cpu_wr_txn: u64,
        iio_dma_wr_txn: u64,
        iio_dma_rd_txn: u64,
        min_pct: f64,
    }

    impl HostPmu {
        fn open(cpu: i32) -> Result<Self, Box<dyn Error>> {
            let core = CorePmu::open(cpu)?;
            let mut cha_occ = Vec::with_capacity(NUM_CHAS);
            let mut cha_wcilf = Vec::with_capacity(NUM_CHAS);
            let mut cha_io_itom = Vec::with_capacity(NUM_CHAS);
            for i in 0..NUM_CHAS {
                let t = read_pmu_type(&format!("uncore_cha_{i}"))?;
                cha_occ.push(PerfCounter::open(t, CHA_TOR_OCC_ALL, SOCKET1_UNCORE_CPU)?);
                cha_wcilf.push(PerfCounter::open(t, CHA_TOR_INS_IA_WCILF, SOCKET1_UNCORE_CPU)?);
                cha_io_itom.push(PerfCounter::open(t, CHA_TOR_INS_IO_ITOM, SOCKET1_UNCORE_CPU)?);
            }
            let cha0_t = read_pmu_type("uncore_cha_0")?;
            let cha_clk = PerfCounter::open(cha0_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let mut imc_rd = Vec::with_capacity(NUM_IMCS);
            let mut imc_wr = Vec::with_capacity(NUM_IMCS);
            for i in 0..NUM_IMCS {
                let t = read_pmu_type(&format!("uncore_imc_{i}"))?;
                imc_rd.push(PerfCounter::open(t, IMC_CAS_COUNT_RD, SOCKET1_UNCORE_CPU)?);
                imc_wr.push(PerfCounter::open(t, IMC_CAS_COUNT_WR, SOCKET1_UNCORE_CPU)?);
            }
            let m2p_t = read_pmu_type("uncore_m2pcie_1")?;
            let m2p_ins = PerfCounter::open(m2p_t, M2P_RXC_INS_CHA_NCB, SOCKET1_UNCORE_CPU)?;
            let m2p_ne = PerfCounter::open(m2p_t, M2P_RXC_NE_CHA_NCB, SOCKET1_UNCORE_CPU)?;
            let m2p_clk = PerfCounter::open(m2p_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let irp_t = read_pmu_type("uncore_irp_1")?;
            let irp_occ = PerfCounter::open(irp_t, IRP_TXS_REQ_OCC, SOCKET1_UNCORE_CPU)?;
            let irp_ins = PerfCounter::open(irp_t, IRP_TXS_INS_NCS, SOCKET1_UNCORE_CPU)?;
            let iio_t = read_pmu_type("uncore_iio_1")?;
            let iio_cpu_wr = PerfCounter::open(iio_t, IIO_CPU_MEM_WR_TXN, SOCKET1_UNCORE_CPU)?;
            let iio_dma_wr = PerfCounter::open(iio_t, IIO_DMA_WR_TXN, SOCKET1_UNCORE_CPU)?;
            let iio_dma_rd = PerfCounter::open(iio_t, IIO_DMA_RD_TXN, SOCKET1_UNCORE_CPU)?;
            let iio_clk = PerfCounter::open(iio_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            Ok(Self {
                core,
                cha_occ,
                cha_wcilf,
                cha_io_itom,
                cha_clk,
                imc_rd,
                imc_wr,
                m2p_ins,
                m2p_ne,
                m2p_clk,
                irp_occ,
                irp_ins,
                iio_cpu_wr,
                iio_dma_wr,
                iio_dma_rd,
                iio_clk,
            })
        }

        fn snap(&self) -> HostSnap {
            let mut cha_occ = [Sample::default(); NUM_CHAS];
            let mut cha_wcilf = [Sample::default(); NUM_CHAS];
            let mut cha_io_itom = [Sample::default(); NUM_CHAS];
            for i in 0..NUM_CHAS {
                cha_occ[i] = self.cha_occ[i].read();
                cha_wcilf[i] = self.cha_wcilf[i].read();
                cha_io_itom[i] = self.cha_io_itom[i].read();
            }
            let mut imc_rd = [Sample::default(); NUM_IMCS];
            let mut imc_wr = [Sample::default(); NUM_IMCS];
            for i in 0..NUM_IMCS {
                imc_rd[i] = self.imc_rd[i].read();
                imc_wr[i] = self.imc_wr[i].read();
            }
            HostSnap {
                wall: Instant::now(),
                core: self.core.snap(),
                cha_occ,
                cha_wcilf,
                cha_io_itom,
                cha_clk: self.cha_clk.read(),
                imc_rd,
                imc_wr,
                m2p_ins: self.m2p_ins.read(),
                m2p_ne: self.m2p_ne.read(),
                m2p_clk: self.m2p_clk.read(),
                irp_occ: self.irp_occ.read(),
                irp_ins: self.irp_ins.read(),
                iio_cpu_wr: self.iio_cpu_wr.read(),
                iio_dma_wr: self.iio_dma_wr.read(),
                iio_dma_rd: self.iio_dma_rd.read(),
                iio_clk: self.iio_clk.read(),
            }
        }

        fn delta(s0: &HostSnap, s1: &HostSnap) -> HostPmuDelta {
            let mut min_pct = 100.0f64;
            let mut d = |a: Sample, b: Sample| -> u64 {
                let x = b.delta(a);
                min_pct = min_pct.min(x.pct());
                x.val
            };
            let wall_s = s1.wall.saturating_duration_since(s0.wall).as_secs_f64().max(1e-9);
            let cycles = d(s0.core.cycles, s1.core.cycles);
            let ref_cycles = d(s0.core.ref_cycles, s1.core.ref_cycles);
            let inst = d(s0.core.inst, s1.core.inst);
            let llc_ref = d(s0.core.llc_ref, s1.core.llc_ref);
            let llc_miss = d(s0.core.llc_miss, s1.core.llc_miss);
            let l3_hit = d(s0.core.l3_hit, s1.core.l3_hit);
            let l3_miss = d(s0.core.l3_miss, s1.core.l3_miss);
            let sb_stalls = d(s0.core.sb_stalls, s1.core.sb_stalls);
            let core_ghz = if ref_cycles > 0 {
                (cycles as f64 / ref_cycles as f64) * (REF_HZ / 1e9)
            } else {
                0.0
            };

            let mut cha_occ_sum = 0u64;
            let mut cha_wcilf_ins = 0u64;
            let mut cha_io_itom_ins = 0u64;
            for i in 0..NUM_CHAS {
                cha_occ_sum = cha_occ_sum.saturating_add(d(s0.cha_occ[i], s1.cha_occ[i]));
                cha_wcilf_ins = cha_wcilf_ins.saturating_add(d(s0.cha_wcilf[i], s1.cha_wcilf[i]));
                cha_io_itom_ins = cha_io_itom_ins.saturating_add(d(s0.cha_io_itom[i], s1.cha_io_itom[i]));
            }
            let cha_clk = d(s0.cha_clk, s1.cha_clk).max(1);
            let cha_occ_mean = cha_occ_sum as f64 / cha_clk as f64;
            let cha_ghz = cha_clk as f64 / (wall_s * 1e9);

            let mut imc_rd_lines = 0u64;
            let mut imc_wr_lines = 0u64;
            for i in 0..NUM_IMCS {
                imc_rd_lines = imc_rd_lines.saturating_add(d(s0.imc_rd[i], s1.imc_rd[i]));
                imc_wr_lines = imc_wr_lines.saturating_add(d(s0.imc_wr[i], s1.imc_wr[i]));
            }
            let imc_rd_gbs = (imc_rd_lines as f64 * 64.0) / (wall_s * 1e9);
            let imc_wr_gbs = (imc_wr_lines as f64 * 64.0) / (wall_s * 1e9);

            let m2p_ins = d(s0.m2p_ins, s1.m2p_ins);
            let m2p_ne = d(s0.m2p_ne, s1.m2p_ne);
            let m2p_clk = d(s0.m2p_clk, s1.m2p_clk).max(1);
            // MRV-185 calibrated 2x hardware factor on M2P RxC_NE.CHA_NCB
            let m2p_ne_pct = (m2p_ne as f64 / (2.0 * m2p_clk as f64)) * 100.0;

            let irp_occ = d(s0.irp_occ, s1.irp_occ);
            let irp_ins = d(s0.irp_ins, s1.irp_ins);
            let _iio_clk = d(s0.iio_clk, s1.iio_clk);
            // Wall-clock normalized IRP1 occupancy (1 GHz nominal IIO clock avoids clock-gating bias)
            let irp_occ_wall = irp_occ as f64 / (wall_s * 1e9);

            let iio_cpu_wr_txn = d(s0.iio_cpu_wr, s1.iio_cpu_wr);
            let iio_dma_wr_txn = d(s0.iio_dma_wr, s1.iio_dma_wr);
            let iio_dma_rd_txn = d(s0.iio_dma_rd, s1.iio_dma_rd);

            HostPmuDelta {
                wall_s,
                core_ghz,
                cycles,
                inst,
                llc_ref,
                llc_miss,
                l3_hit,
                l3_miss,
                sb_stalls,
                cha_occ_mean,
                cha_wcilf_ins,
                cha_io_itom_ins,
                cha_ghz,
                imc_rd_lines,
                imc_wr_lines,
                imc_rd_gbs,
                imc_wr_gbs,
                m2p_ins,
                m2p_ne_pct,
                irp_occ_wall,
                irp_ins,
                iio_cpu_wr_txn,
                iio_dma_wr_txn,
                iio_dma_rd_txn,
                min_pct,
            }
        }
    }

    impl HostPmuDelta {
        fn to_json(&self) -> String {
            let l3_tot = self.l3_hit + self.l3_miss;
            let l3_miss_pct = if l3_tot > 0 {
                self.l3_miss as f64 * 100.0 / l3_tot as f64
            } else {
                0.0
            };
            format!(
                "{{\"wall_s\":{:.6},\"core_ghz\":{:.4},\"cycles\":{},\"inst\":{},\"llc_ref\":{},\"llc_miss\":{},\"l3_hit\":{},\"l3_miss\":{},\"l3_miss_pct\":{:.3},\"sb_stalls\":{},\"cha_occ_mean\":{:.3},\"cha_wcilf_ins\":{},\"cha_io_itom_ins\":{},\"cha_ghz\":{:.4},\"imc_rd_lines\":{},\"imc_wr_lines\":{},\"imc_rd_gbs\":{:.4},\"imc_wr_gbs\":{:.4},\"m2p_ins\":{},\"m2p_ne_pct\":{:.3},\"irp_occ_wall\":{:.3},\"irp_ins\":{},\"iio_cpu_wr_txn\":{},\"iio_dma_wr_txn\":{},\"iio_dma_rd_txn\":{},\"min_pct\":{:.2}}}",
                self.wall_s,
                self.core_ghz,
                self.cycles,
                self.inst,
                self.llc_ref,
                self.llc_miss,
                self.l3_hit,
                self.l3_miss,
                l3_miss_pct,
                self.sb_stalls,
                self.cha_occ_mean,
                self.cha_wcilf_ins,
                self.cha_io_itom_ins,
                self.cha_ghz,
                self.imc_rd_lines,
                self.imc_wr_lines,
                self.imc_rd_gbs,
                self.imc_wr_gbs,
                self.m2p_ins,
                self.m2p_ne_pct,
                self.irp_occ_wall,
                self.irp_ins,
                self.iio_cpu_wr_txn,
                self.iio_dma_wr_txn,
                self.iio_dma_rd_txn,
                self.min_pct,
            )
        }
    }

    // ---- Sync TCP Client ----
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

    fn extract_json_str(json: &str, key: &str) -> Option<String> {
        let pat = format!("\"{key}\":\"");
        let start = json.find(&pat)? + pat.len();
        let rest = &json[start..];
        let end = rest.find('"')?;
        Some(rest[..end].to_string())
    }

    fn extract_json_obj(json: &str, key: &str) -> String {
        let pat = format!("\"{key}\":");
        if let Some(i) = json.find(&pat) {
            let rest = &json[i + pat.len()..];
            if rest.starts_with('{') {
                let mut depth = 0i32;
                for (j, ch) in rest.char_indices() {
                    if ch == '{' {
                        depth += 1;
                    } else if ch == '}' {
                        depth -= 1;
                        if depth == 0 {
                            return rest[..=j].to_string();
                        }
                    }
                }
            }
        }
        "{}".to_string()
    }

    #[inline(always)]
    fn issue_credit(
        credit: CreditMode,
        slot_opt: Option<&LeasedSlot<'_>>,
        q: usize,
        host_taken_ptr: Option<*mut CreditLine>,
        taken: u64,
        run_gen: u64,
        bytes_taken: u64,
    ) -> Result<(), Box<dyn Error>> {
        match credit {
            CreditMode::Off | CreditMode::NativeBp => Ok(()),
            CreditMode::Bar4Nofence => {
                let slot = slot_opt.unwrap();
                slot.check()?;
                let line = CreditLine::new(taken, run_gen, bytes_taken, 0);
                unsafe { movdir64b(slot.as_ptr().add(credit_line_offset(q)), &line) };
                Ok(())
            }
            CreditMode::Bar4Sfence => {
                let slot = slot_opt.unwrap();
                slot.check()?;
                let line = CreditLine::new(taken, run_gen, bytes_taken, 0);
                unsafe { movdir64b(slot.as_ptr().add(credit_line_offset(q)), &line) };
                sfence();
                Ok(())
            }
            CreditMode::Bar4Uc64 => {
                let slot = slot_opt.unwrap();
                slot.check()?;
                slot.write_u64(credit_line_offset(q), taken);
                Ok(())
            }
            CreditMode::HostmemDpi => {
                if let Some(p) = host_taken_ptr {
                    let line = CreditLine::new(taken, run_gen, bytes_taken, 0);
                    unsafe {
                        let dst = p as *mut u64;
                        dst.add(1).write_volatile(line.w[1]);
                        dst.add(2).write_volatile(line.w[2]);
                        dst.add(5).write_volatile(line.w[5]);
                        dst.add(7).write_volatile(line.w[7]);
                        std::sync::atomic::compiler_fence(Ordering::Release);
                        dst.add(0).write_volatile(line.w[0]);
                    }
                }
                Ok(())
            }
        }
    }

    fn summarize_us(samples: &mut [f64]) -> (f64, f64, f64, f64, f64, f64) {
        if samples.is_empty() {
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
        }
        let sum: f64 = samples.iter().sum();
        let mean = sum / samples.len() as f64;
        samples.sort_by(|a, b| a.total_cmp(b));
        let n = samples.len();
        let pct = |q: f64| samples[((n - 1) as f64 * q).round() as usize];
        (mean, pct(0.50), pct(0.90), pct(0.99), pct(0.999), samples[n - 1])
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        // Lock ELF .text/.rodata/.data and libc pages before EAL reserves virtual hugepage regions
        // so cold branches (e.g. D1 pause drain) incur zero minor page faults during measurement.
        let _ = unsafe { libc::mlockall(libc::MCL_CURRENT) };
        let (eal, app_args) = Eal::init(std::env::args())?;
        let args = Args::parse_from(app_args);
        let tsc_hz = eal.tsc_hz() as f64;

        if args.credit.uses_bar4() && !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }

        // ---- Mode 1: Calibrate app_work_step() on host_cpu ----
        if args.mode == Mode::CalibrateWork {
            let pmu = HostPmu::open(args.host_cpu)?;
            // Warmup 300 ms
            let mut acc = 0x1234_5678u64;
            let w0 = Instant::now();
            while w0.elapsed() < Duration::from_millis(300) {
                acc = app_work_step(acc);
            }
            let pf0 = thread_page_faults();
            let s0 = pmu.snap();
            let t0 = Instant::now();
            let mut steps = 0u64;
            while t0.elapsed() < Duration::from_millis(args.duration_ms) {
                for _ in 0..64 {
                    acc = app_work_step(acc);
                }
                steps += 64;
            }
            let s1 = pmu.snap();
            let pf1 = thread_page_faults();
            std::hint::black_box(acc);
            let d = HostPmu::delta(&s0, &s1);
            let cyc_per_step = d.cycles as f64 / steps.max(1) as f64;
            let ns_per_step = d.wall_s * 1e9 / steps.max(1) as f64;
            println!(
                "{{\"mode\":\"calibrate-work\",\"steps\":{steps},\"cycles_per_step\":{cyc_per_step:.4},\"ns_per_step\":{ns_per_step:.3},\"core_ghz\":{:.4},\"page_faults\":{},\"host_pmu\":{}}}",
                d.core_ghz,
                pf1.saturating_sub(pf0),
                d.to_json()
            );
            return Ok(());
        }

        // ---- Mode 2: Micro-signal pure cost cross-check (Control A0) ----
        if args.mode == Mode::MicroSignal {
            let mut sync = SyncClient::connect(&args.sync)?;
            let _ = sync.cmd("CLEAR_BAR4 0")?;
            let lease = Lease::acquire(&args.card)?;
            let slot = lease.map_slot(&args.bar)?;
            let host_mz = Memzone::reserve_contiguous(&eal, "mrv212_micro_taken", 4096, Port::socket_of(&eal, PORT), 64)?;
            let host_taken_ptr = host_mz.as_ptr().cast::<CreditLine>();
            let pmu = HostPmu::open(args.host_cpu)?;

            // Small L1 scratchpad simulating realistic B=32 RX descriptor/mbuf store-buffer state
            let mut scratch = [0u64; 256];
            let mut acc = 1u64;
            let mut taken = 0u64;
            let body = args.body_iters.max(1);

            // Warmup 50k batches
            for i in 0..50_000u64 {
                for j in 0..body {
                    let k = ((i as usize).wrapping_add(j) * 7) & 255;
                    scratch[k] = scratch[k].wrapping_add(i ^ j as u64);
                    acc ^= scratch[k];
                }
                taken += args.credit_batch;
                issue_credit(args.credit, Some(&slot), 0, Some(host_taken_ptr), taken, args.run_gen, taken * args.frame_size as u64)?;
            }
            sfence();

            if args.with_card_pmu {
                let _ = sync.cmd("PMU_START")?;
            }
            let pf0 = thread_page_faults();
            let s0 = pmu.snap();
            for i in 0..args.rounds {
                for j in 0..body {
                    let k = ((i as usize).wrapping_add(j) * 7) & 255;
                    scratch[k] = scratch[k].wrapping_add(i ^ j as u64);
                    acc ^= scratch[k];
                }
                taken += args.credit_batch;
                issue_credit(args.credit, Some(&slot), 0, Some(host_taken_ptr), taken, args.run_gen, taken * args.frame_size as u64)?;
            }
            let s1 = pmu.snap();
            let pf1 = thread_page_faults();
            let card_pmu_json = if args.with_card_pmu {
                extract_json_obj(&sync.cmd("PMU_STOP")?, "pmu")
            } else {
                "{}".to_string()
            };
            std::hint::black_box(acc);
            let d = HostPmu::delta(&s0, &s1);
            let cyc_per_batch = d.cycles as f64 / args.rounds.max(1) as f64;
            let sb_per_batch = d.sb_stalls as f64 / args.rounds.max(1) as f64;
            let ns_per_batch = d.wall_s * 1e9 / args.rounds.max(1) as f64;
            println!(
                "{{\"mode\":\"micro-signal\",\"credit\":\"{}\",\"body_iters\":{body},\"rounds\":{},\"credit_batch\":{},\"cycles_per_batch\":{cyc_per_batch:.3},\"ns_per_batch\":{ns_per_batch:.3},\"sb_stalls_per_batch\":{sb_per_batch:.3},\"page_faults\":{},\"host_pmu\":{},\"card_pmu\":{card_pmu_json}}}",
                args.credit.as_str(),
                args.rounds,
                args.credit_batch,
                pf1.saturating_sub(pf0),
                d.to_json()
            );
            return Ok(());
        }

        // ---- Standard SDP Modes (RxStream, CreditRtt, PingPong, DualQueue) ----
        let n_queues: usize = if args.mode == Mode::DualQueue { 2 } else { 1 };
        let sock = Port::socket_of(&eal, PORT);

        let mut pools: Vec<Mempool<'_>> = Vec::with_capacity(n_queues);
        for q in 0..n_queues {
            pools.push(Mempool::new_with_cache(
                &eal,
                &format!("mrv212_rx_q{q}"),
                args.pool_size,
                args.pool_cache,
                Mempool::default_data_room(),
                sock,
            )?);
        }
        let tx_pool = Mempool::new_with_cache(&eal, "mrv212_tx", 1023, 128, Mempool::default_data_room(), sock)?;
        let pool_refs: Vec<&Mempool<'_>> = pools.iter().collect();
        let mut port = Port::start_per_queue(&eal, PORT, &pool_refs, args.rx_desc, args.tx_desc)?;
        let _ = port.promiscuous();
        let host_mac = Mac(port.mac());

        let host_taken_mz = Memzone::reserve_contiguous(&eal, "mrv212_host_taken", 4096, sock, 64)?;
        unsafe { ptr::write_bytes(host_taken_mz.as_ptr(), 0, 4096) };
        let host_taken_ptr = host_taken_mz.as_ptr().cast::<CreditLine>();
        let host_taken_iova = host_taken_mz.iova();

        let mut sync = SyncClient::connect(&args.sync)?;
        let ping_resp = sync.cmd("PING")?;
        let card_mac_str = extract_json_str(&ping_resp, "mac").ok_or("missing card mac in PING")?;
        let card_mac: Mac = card_mac_str.parse()?;
        let _ = sync.cmd("CLEAR_BAR4 0")?;

        // Acquire BAR4 MRV-26 lease whenever BAR4 is used (or always for uniform setup)
        let lease = Lease::acquire(&args.card)?;
        let slot = lease.map_slot(&args.bar)?;
        let pmu = HostPmu::open(args.host_cpu)?;

        // ---- Mode 2b: Calibrate empty SDP RX poll (+ optional app_work_step) ----
        if args.mode == Mode::CalibrateEmptyRx {
            let mut rx_b: Burst<'_, 32> = Burst::new();
            let mut acc = 0x1234_5678u64;
            let w0 = Instant::now();
            while w0.elapsed() < Duration::from_millis(300) {
                let _ = port.rx(0, &mut rx_b);
                rx_b.clear();
                if args.app_work {
                    acc = app_work_step(acc);
                }
            }
            let pf0 = thread_page_faults();
            let s0 = pmu.snap();
            let t0 = Instant::now();
            let mut polls = 0u64;
            while t0.elapsed() < Duration::from_millis(args.duration_ms) {
                let _ = port.rx(0, &mut rx_b);
                if args.app_work {
                    acc = app_work_step(acc);
                }
                polls += 1;
            }
            let s1 = pmu.snap();
            let pf1 = thread_page_faults();
            std::hint::black_box(acc);
            let d = HostPmu::delta(&s0, &s1);
            let cyc_per_poll = d.cycles as f64 / polls.max(1) as f64;
            let ns_per_poll = d.wall_s * 1e9 / polls.max(1) as f64;
            println!(
                "{{\"mode\":\"calibrate-empty-rx\",\"app_work\":{},\"polls\":{polls},\"cycles_per_poll\":{cyc_per_poll:.4},\"ns_per_poll\":{ns_per_poll:.3},\"core_ghz\":{:.4},\"page_faults\":{},\"host_pmu\":{}}}",
                args.app_work,
                d.core_ghz,
                pf1.saturating_sub(pf0),
                d.to_json()
            );
            return Ok(());
        }

        // ---- Mode 3: Control A2 Direct Credit Control-Loop RTT (`credit-rtt`) ----
        if args.mode == Mode::CreditRtt {
            let run_cmd = format!(
                "RUN_START mode=credit-rtt credit={} host_mac={host_mac} frame_size={} run_gen={} queues=1 r_eff=1 host_taken_iova=0x{host_taken_iova:x}",
                args.credit.as_str(),
                args.frame_size,
                args.run_gen
            );
            sync.cmd(&run_cmd)?;

            let mut rx_b: Burst<'_, 32> = Burst::new();
            let mut bad = 0u64;
            let mut torn = 0u64;
            let mut ooo = 0u64;
            let mut rtts_us = vec![1.0f64; args.rounds as usize];
            std::hint::black_box(&rtts_us);
            rtts_us.clear();
            let total = args.warmup_rounds + args.rounds;
            let mut s0_opt = None;
            let mut pf0 = 0u64;
            let mut m_t0 = Instant::now();

            for i in 1..=total {
                if i == args.warmup_rounds + 1 {
                    if args.with_card_pmu {
                        let _ = sync.cmd("PMU_START")?;
                    }
                    pf0 = thread_page_faults();
                    s0_opt = Some(pmu.snap());
                    m_t0 = Instant::now();
                }
                let t0 = rdtsc_after_loads();
                let taken = i;
                issue_credit(
                    args.credit,
                    Some(&slot),
                    0,
                    Some(host_taken_ptr),
                    taken,
                    args.run_gen,
                    taken * args.frame_size as u64,
                )?;
                let deadline = Instant::now() + Duration::from_secs(2);
                loop {
                    if port.rx(0, &mut rx_b) > 0 {
                        let t1 = rdtsc_after_loads();
                        for pkt in rx_b.as_slice() {
                            match verify_frame(pkt.data(), args.frame_size as usize, args.run_gen) {
                                FrameCheck::Ok { seq, .. } => {
                                    if seq != taken {
                                        ooo += 1;
                                    }
                                }
                                FrameCheck::Bad => bad += 1,
                                FrameCheck::Torn => torn += 1,
                            }
                        }
                        rx_b.clear();
                        if i > args.warmup_rounds {
                            let us = t1.saturating_sub(t0) as f64 * 1e6 / tsc_hz;
                            rtts_us.push(us);
                        }
                        break;
                    }
                    if Instant::now() > deadline {
                        let _ = sync.cmd("RUN_STOP");
                        return Err(format!("credit-rtt timeout at seq={taken}").into());
                    }
                }
            }
            let meas_s = m_t0.elapsed().as_secs_f64();
            let s1 = pmu.snap();
            let pf1 = thread_page_faults();
            let card_pmu_json = if args.with_card_pmu {
                extract_json_obj(&sync.cmd("PMU_STOP")?, "pmu")
            } else {
                "{}".to_string()
            };
            let stop_resp = sync.cmd("RUN_STOP")?;
            let card_stats = extract_json_obj(&stop_resp, "stats");
            let d = HostPmu::delta(&s0_opt.unwrap(), &s1);
            let (mean, p50, p90, p99, p999, max_v) = summarize_us(&mut rtts_us);
            let rate_mps = args.rounds as f64 / (meas_s * 1e6);
            let inv_rate_us = 1.0 / rate_mps;
            // 100GbE = 12.5 GB/s = 12.5 KiB/us (decimal GB/s -> KiB = bytes/1024)
            let bdp_mean_kib = (12.5e9 * (mean * 1e-6)) / 1024.0;
            let bdp_p50_kib = (12.5e9 * (p50 * 1e-6)) / 1024.0;
            let bdp_p99_kib = (12.5e9 * (p99 * 1e-6)) / 1024.0;

            println!(
                "{{\"mode\":\"credit-rtt\",\"credit\":\"{}\",\"frame_size\":{},\"rx_desc\":{},\"rounds\":{},\"bad\":{bad},\"torn\":{torn},\"ooo\":{ooo},\"lost\":0,\"mean_us\":{mean:.4},\"p50_us\":{p50:.4},\"p90_us\":{p90:.4},\"p99_us\":{p99:.4},\"p999_us\":{p999:.4},\"max_us\":{max_v:.4},\"inv_rate_us\":{inv_rate_us:.4},\"bdp_100gbe_mean_kib\":{bdp_mean_kib:.3},\"bdp_100gbe_p50_kib\":{bdp_p50_kib:.3},\"bdp_100gbe_p99_kib\":{bdp_p99_kib:.3},\"page_faults\":{},\"host_pmu\":{},\"card_pmu\":{card_pmu_json},\"card_stats\":{card_stats}}}",
                args.credit.as_str(),
                args.frame_size,
                args.rx_desc,
                args.rounds,
                pf1.saturating_sub(pf0),
                d.to_json()
            );
            return Ok(());
        }

        // ---- Mode 4: Control A2 Zero-Load W=1 Ping-Pong Latency Increment (`pingpong`) ----
        if args.mode == Mode::PingPong {
            let run_cmd = format!(
                "RUN_START mode=pingpong credit={} host_mac={host_mac} frame_size={} run_gen={} queues=1 r_eff={} host_taken_iova=0x{host_taken_iova:x}",
                args.credit.as_str(),
                args.frame_size,
                args.run_gen,
                args.r_eff
            );
            sync.cmd(&run_cmd)?;

            let mut rx_b: Burst<'_, 32> = Burst::new();
            let mut taken = 0u64;
            let mut bad = 0u64;
            let mut torn = 0u64;
            let mut ooo = 0u64;
            let mut rtts_us = vec![1.0f64; args.rounds as usize];
            std::hint::black_box(&rtts_us);
            rtts_us.clear();
            let total = args.warmup_rounds + args.rounds;
            let mut s0_opt = None;
            let mut pf0 = 0u64;
            let mut m_t0 = Instant::now();

            for i in 1..=total {
                if i == args.warmup_rounds + 1 {
                    if args.with_card_pmu {
                        let _ = sync.cmd("PMU_START")?;
                    }
                    pf0 = thread_page_faults();
                    s0_opt = Some(pmu.snap());
                    m_t0 = Instant::now();
                }
                let mut req = tx_pool.alloc().ok_or("tx pool exhausted")?;
                let frame = req.append(args.frame_size).ok_or("append failed")?;
                let t0 = rdtsc_after_loads();
                write_frame(frame, card_mac, host_mac, 0, i, args.run_gen, t0);
                let mut req_opt = Some(req);
                while let Some(r) = req_opt.take() {
                    req_opt = port.send(0, r).err();
                }
                let deadline = Instant::now() + Duration::from_secs(2);
                loop {
                    if port.rx(0, &mut rx_b) > 0 {
                        let t1 = rdtsc_after_loads();
                        for pkt in rx_b.as_slice() {
                            match verify_frame(pkt.data(), args.frame_size as usize, args.run_gen) {
                                FrameCheck::Ok { seq, .. } => {
                                    if seq != i {
                                        ooo += 1;
                                    }
                                }
                                FrameCheck::Bad => bad += 1,
                                FrameCheck::Torn => torn += 1,
                            }
                        }
                        rx_b.clear();
                        taken += 1;
                        // Return credit on RX path (every B packets or every 1 packet for worst-case W=1)
                        if taken.is_multiple_of(args.credit_batch.max(1)) {
                            issue_credit(
                                args.credit,
                                Some(&slot),
                                0,
                                Some(host_taken_ptr),
                                taken,
                                args.run_gen,
                                taken * args.frame_size as u64,
                            )?;
                        }
                        if i > args.warmup_rounds {
                            rtts_us.push(t1.saturating_sub(t0) as f64 * 1e6 / tsc_hz);
                        }
                        break;
                    }
                    if Instant::now() > deadline {
                        let _ = sync.cmd("RUN_STOP");
                        return Err(format!("pingpong timeout at seq={i}").into());
                    }
                }
            }
            let meas_s = m_t0.elapsed().as_secs_f64();
            let s1 = pmu.snap();
            let pf1 = thread_page_faults();
            let card_pmu_json = if args.with_card_pmu {
                extract_json_obj(&sync.cmd("PMU_STOP")?, "pmu")
            } else {
                "{}".to_string()
            };
            let stop_resp = sync.cmd("RUN_STOP")?;
            let card_stats = extract_json_obj(&stop_resp, "stats");
            let d = HostPmu::delta(&s0_opt.unwrap(), &s1);
            let (mean, p50, p90, p99, p999, max_v) = summarize_us(&mut rtts_us);
            let inv_rate_us = (meas_s * 1e6) / args.rounds as f64;
            println!(
                "{{\"mode\":\"pingpong\",\"credit\":\"{}\",\"frame_size\":{},\"rx_desc\":{},\"credit_batch\":{},\"r_eff\":{},\"rounds\":{},\"bad\":{bad},\"torn\":{torn},\"ooo\":{ooo},\"lost\":0,\"mean_us\":{mean:.4},\"p50_us\":{p50:.4},\"p90_us\":{p90:.4},\"p99_us\":{p99:.4},\"p999_us\":{p999:.4},\"max_us\":{max_v:.4},\"inv_rate_us\":{inv_rate_us:.4},\"page_faults\":{},\"host_pmu\":{},\"card_pmu\":{card_pmu_json},\"card_stats\":{card_stats}}}",
                args.credit.as_str(),
                args.frame_size,
                args.rx_desc,
                args.credit_batch,
                args.r_eff,
                args.rounds,
                pf1.saturating_sub(pf0),
                d.to_json()
            );
            return Ok(());
        }

        // ---- Mode 5: RxStream (Controls A1, C1, C2, D1) ----
        if args.mode == Mode::RxStream {
            let _ = port.reset_stats();
            let eth0 = port.stats().unwrap_or_default();

            let run_cmd = format!(
                "RUN_START mode=stream credit={} host_mac={host_mac} frame_size={} run_gen={} queues=1 r_eff={} r_eff_bytes={} target_mpps={:.4} use_fifo={} fifo_cap={} host_taken_iova=0x{host_taken_iova:x}",
                args.credit.as_str(),
                args.frame_size,
                args.run_gen,
                args.r_eff,
                args.r_eff_bytes,
                args.target_mpps,
                if args.use_fifo { 1 } else { 0 },
                args.fifo_cap
            );
            sync.cmd(&run_cmd)?;

            let mut rx_b: Burst<'_, 32> = Burst::new();
            let mut static_hold: Vec<dpdk::Mbuf<'_>> = Vec::with_capacity(128);
            let mut init_iovas = vec![1u64; args.rx_desc as usize];
            init_iovas.fill(0);
            let mut iova_recorded = vec![true; args.rx_desc as usize];
            iova_recorded.fill(false);
            let mut iova_same = 0u64;
            let mut iova_changed = 0u64;
            let mut last_slot_iova = vec![1u64; args.rx_desc as usize];
            last_slot_iova.fill(0);
            let mut expected_seq = 1u64;
            let mut taken_total = 0u64;
            let mut last_credit_sent = 0u64;
            let mut credit_updates = 0u64;

            // Measurement window stats
            let mut meas_pkts = 0u64;
            let mut meas_bad = 0u64;
            let mut meas_torn = 0u64;
            let mut meas_ooo = 0u64;
            let mut meas_seq_gap_lost = 0u64;
            let mut total_bad = 0u64;
            let mut total_torn = 0u64;
            let mut total_ooo = 0u64;
            let mut total_seq_gap_lost = 0u64;
            let mut meas_empty_polls = 0u64;
            let mut meas_nonempty_polls = 0u64;
            let mut meas_app_steps = 0u64;
            let mut app_acc = 0x9876_5432u64;

            // Periodic pause stats (Control D1) — pre-faulted for zero measurement page faults
            let mut pause_count = 0u64;
            let mut pause_drained_samples: Vec<u64> = vec![1u64; 4096];
            std::hint::black_box(&pause_drained_samples);
            pause_drained_samples.clear();
            let mut resume_first_us: Vec<f64> = vec![1.0f64; 4096];
            std::hint::black_box(&resume_first_us);
            resume_first_us.clear();
            let mut inter_batch_gaps_us: Vec<f64> = vec![1.0f64; 65536];
            std::hint::black_box(&inter_batch_gaps_us);
            inter_batch_gaps_us.clear();
            let _ = port.rx(0, &mut rx_b);
            rx_b.clear();
            let phase = AtomicU8::new(PHASE_WARMUP);
            let host_go_sent = AtomicBool::new(false);
            let coord_abort = AtomicBool::new(false);

            let pause_period_ticks = (args.pause_every_ms as f64 * 1e-3 * tsc_hz) as u64;
            let pause_dur_ticks = (args.pause_dur_us as f64 * 1e-6 * tsc_hz) as u64;
            let snap_idle_ticks = (12.0e-6 * tsc_hz) as u64;
            let mut meas_credit_updates = 0u64;
            let mut pf0 = 0u64;
            let mut pf1 = 0u64;

            let with_card_pmu = args.with_card_pmu;
            let warmup_ms = args.warmup_ms;
            let duration_ms = args.duration_ms;
            let coord_res: Result<(HostSnap, HostSnap, String, String), String> = thread::scope(|scope| {
                let coord_handle = scope.spawn(|| -> Result<(HostSnap, HostSnap, String, String), String> {
                    pin_thread_to_cpu(COORD_CPU);
                    while !host_go_sent.load(Ordering::Acquire) {
                        if coord_abort.load(Ordering::Acquire) {
                            let _ = sync.cmd("RUN_STOP");
                            return Err("host aborted before GO".into());
                        }
                        thread::sleep(Duration::from_millis(1));
                    }
                    if !sleep_abortable(Duration::from_millis(warmup_ms), &coord_abort) {
                        let _ = sync.cmd("RUN_STOP");
                        phase.store(PHASE_STOP_DRAIN, Ordering::Release);
                        return Err("host aborted during warmup".into());
                    }
                    if with_card_pmu {
                        sync.cmd("PMU_START").map_err(|e| e.to_string())?;
                    }
                    let s0 = pmu.snap();
                    phase.store(PHASE_MEASURE, Ordering::Release);

                    if !sleep_abortable(Duration::from_millis(duration_ms), &coord_abort) {
                        phase.store(PHASE_DRAIN, Ordering::Release);
                        if with_card_pmu {
                            let _ = sync.cmd("PMU_STOP");
                        }
                        let _ = sync.cmd("RUN_STOP");
                        phase.store(PHASE_STOP_DRAIN, Ordering::Release);
                        return Err("host aborted during measure".into());
                    }

                    phase.store(PHASE_DRAIN, Ordering::Release);
                    let s1 = pmu.snap();
                    let card_pmu_json = if with_card_pmu {
                        extract_json_obj(&sync.cmd("PMU_STOP").map_err(|e| e.to_string())?, "pmu")
                    } else {
                        "{}".to_string()
                    };
                    let stop_resp = sync.cmd("RUN_STOP").map_err(|e| e.to_string())?;
                    let card_stats = extract_json_obj(&stop_resp, "stats");
                    phase.store(PHASE_STOP_DRAIN, Ordering::Release);
                    Ok((s0, s1, card_pmu_json, card_stats))
                });

                // Ensure coordinator exits even if RX loop returns early with error
                let _abort_guard = StopOnDrop(&coord_abort);

                let rx_res: Result<(), String> = (|| {
                    slot.check().map_err(|e| e.to_string())?;
                    slot.write_u64(credit_line_offset(15), args.run_gen);
                    sfence();
                    host_go_sent.store(true, Ordering::Release);

                    let mut was_measure = false;
                    let mut ended_measure = false;
                    let mut stop_drain_start: Option<Instant> = None;
                    let mut stop_empty_since: Option<Instant> = None;
                    let mut next_pause_tsc = 0u64;
                    let mut last_rx_tsc = rdtsc();

                    loop {
                        let p = phase.load(Ordering::Acquire);
                        let in_measure = p == PHASE_MEASURE;
                        if in_measure && !was_measure {
                            pf0 = thread_page_faults();
                            was_measure = true;
                            last_rx_tsc = rdtsc();
                            if args.pause_every_ms > 0 {
                                next_pause_tsc = last_rx_tsc.wrapping_add(pause_period_ticks);
                            }
                        } else if !in_measure && was_measure && !ended_measure {
                            pf1 = thread_page_faults();
                            ended_measure = true;
                        }
                        if p == PHASE_STOP_DRAIN {
                            if stop_drain_start.is_none() {
                                while let Some(m) = static_hold.pop() {
                                    drop(m);
                                }
                                stop_drain_start = Some(Instant::now());
                            }
                            if stop_drain_start.unwrap().elapsed() >= Duration::from_millis(200) {
                                break;
                            }
                        }

                        // Control D1: periodic host consumer pause (strictly in PHASE_MEASURE)
                        if in_measure && args.pause_every_ms > 0 {
                            let now_tsc = rdtsc();
                            if now_tsc >= next_pause_tsc {
                                let pause_end = now_tsc.wrapping_add(pause_dur_ticks);
                                while rdtsc() < pause_end && phase.load(Ordering::Acquire) == PHASE_MEASURE {
                                    std::hint::spin_loop();
                                }
                                pause_count += 1;
                                let resume_t0 = rdtsc_after_loads();
                                let mut burst_in_host = 0u64;
                                let mut got_first = false;
                                let mut last_got_tsc = rdtsc();
                                loop {
                                    let n = port.rx(0, &mut rx_b);
                                    if n == 0 {
                                        if rdtsc().saturating_sub(last_got_tsc) >= snap_idle_ticks {
                                            break;
                                        }
                                        continue;
                                    }
                                    last_got_tsc = rdtsc();
                                    if !got_first {
                                        let t_first = rdtsc_after_loads();
                                        resume_first_us.push(t_first.saturating_sub(resume_t0) as f64 * 1e6 / tsc_hz);
                                        got_first = true;
                                    }
                                    for pkt in rx_b.as_mut_slice() {
                                        match verify_frame(pkt.data(), args.frame_size as usize, args.run_gen) {
                                            FrameCheck::Ok { seq, .. } => {
                                                if seq != expected_seq {
                                                    let gap = seq.saturating_sub(expected_seq);
                                                    total_ooo += 1;
                                                    total_seq_gap_lost += gap;
                                                    meas_ooo += 1;
                                                    meas_seq_gap_lost += gap;
                                                    expected_seq = seq + 1;
                                                } else {
                                                    expected_seq += 1;
                                                }
                                            }
                                            FrameCheck::Bad => {
                                                total_bad += 1;
                                                meas_bad += 1;
                                            }
                                            FrameCheck::Torn => {
                                                total_torn += 1;
                                                meas_torn += 1;
                                            }
                                        }
                                        if args.poison {
                                            poison_frame(pkt.data_mut(), expected_seq);
                                        }
                                    }
                                    burst_in_host += n as u64;
                                    meas_pkts += n as u64;
                                    meas_nonempty_polls += 1;
                                    taken_total += n as u64;
                                    rx_b.clear();
                                    if burst_in_host >= args.rx_desc as u64 {
                                        break;
                                    }
                                }
                                pause_drained_samples.push(burst_in_host);
                                if taken_total > last_credit_sent {
                                    let pub_val = if args.align_refill {
                                        (taken_total / 64) * 64
                                    } else {
                                        taken_total
                                    };
                                    if pub_val > last_credit_sent {
                                        issue_credit(
                                            args.credit,
                                            Some(&slot),
                                            0,
                                            Some(host_taken_ptr),
                                            pub_val,
                                            args.run_gen,
                                            pub_val * args.frame_size as u64,
                                        )
                                        .map_err(|e| e.to_string())?;
                                        last_credit_sent = pub_val;
                                        credit_updates += 1;
                                        meas_credit_updates += 1;
                                    }
                                }
                                last_rx_tsc = rdtsc();
                                next_pause_tsc = last_rx_tsc.wrapping_add(pause_period_ticks);
                                continue;
                            }
                        }

                        let n = port.rx(0, &mut rx_b);
                        if n == 0 {
                            if in_measure {
                                meas_empty_polls += 1;
                                if args.app_work {
                                    app_acc = app_work_step(app_acc);
                                    meas_app_steps += 1;
                                }
                            } else if p == PHASE_STOP_DRAIN {
                                let since = *stop_empty_since.get_or_insert_with(Instant::now);
                                if since.elapsed() >= Duration::from_millis(30) {
                                    break;
                                }
                            }
                            continue;
                        }
                        stop_empty_since = None;

                        if in_measure {
                            meas_nonempty_polls += 1;
                            if args.pause_every_ms > 0 && inter_batch_gaps_us.len() < 65536 {
                                let now_tsc = rdtsc();
                                inter_batch_gaps_us.push(now_tsc.saturating_sub(last_rx_tsc) as f64 * 1e6 / tsc_hz);
                                last_rx_tsc = now_tsc;
                            }
                        }

                        // Verify every packet + track IOVA invariance/churn
                        for pkt in rx_b.as_mut_slice() {
                            let slot_idx = ((expected_seq - 1) % (args.rx_desc as u64)) as usize;
                            let iova = pkt.iova();
                            if !iova_recorded[slot_idx] {
                                init_iovas[slot_idx] = iova;
                                last_slot_iova[slot_idx] = iova;
                                iova_recorded[slot_idx] = true;
                            } else if in_measure {
                                if iova == last_slot_iova[slot_idx] {
                                    iova_same += 1;
                                } else {
                                    iova_changed += 1;
                                    last_slot_iova[slot_idx] = iova;
                                }
                            }

                            match verify_frame(pkt.data(), args.frame_size as usize, args.run_gen) {
                                FrameCheck::Ok { seq, .. } => {
                                    if seq != expected_seq {
                                        let gap = seq.saturating_sub(expected_seq);
                                        total_ooo += 1;
                                        total_seq_gap_lost += gap;
                                        if in_measure {
                                            meas_ooo += 1;
                                            meas_seq_gap_lost += gap;
                                        }
                                        expected_seq = seq + 1;
                                    } else {
                                        expected_seq += 1;
                                    }
                                }
                                FrameCheck::Bad => {
                                    total_bad += 1;
                                    if in_measure {
                                        meas_bad += 1;
                                    }
                                }
                                FrameCheck::Torn => {
                                    total_torn += 1;
                                    if in_measure {
                                        meas_torn += 1;
                                    }
                                }
                            }
                            if args.poison {
                                poison_frame(pkt.data_mut(), expected_seq);
                            }
                        }

                        taken_total += n as u64;
                        if in_measure {
                            meas_pkts += n as u64;
                        }

                        // Recycle mbufs
                        if args.recycle == RecycleMode::StaticInplace && p != PHASE_STOP_DRAIN {
                            let mut tmp: Vec<dpdk::Mbuf<'_>> = Vec::with_capacity(n);
                            while let Some(m) = rx_b.pop() {
                                tmp.push(m);
                            }
                            for m in tmp.into_iter().rev() {
                                static_hold.push(m);
                            }
                            if static_hold.len() >= 64 {
                                while let Some(m) = static_hold.pop() {
                                    drop(m);
                                }
                            }
                        } else {
                            rx_b.clear();
                        }

                        // Credit signaling
                        if taken_total.saturating_sub(last_credit_sent) >= args.credit_batch {
                            let pub_val = if args.align_refill {
                                taken_total.saturating_sub(64) & !63
                            } else {
                                taken_total
                            };
                            if pub_val > last_credit_sent {
                                issue_credit(
                                    args.credit,
                                    Some(&slot),
                                    0,
                                    Some(host_taken_ptr),
                                    pub_val,
                                    args.run_gen,
                                    pub_val * args.frame_size as u64,
                                )
                                .map_err(|e| e.to_string())?;
                                last_credit_sent = pub_val;
                                credit_updates += 1;
                                if in_measure {
                                    meas_credit_updates += 1;
                                }
                            }
                        }
                    }
                    if !ended_measure && was_measure {
                        pf1 = thread_page_faults();
                    }
                    Ok(())
                })();

                if rx_res.is_err() {
                    coord_abort.store(true, Ordering::Release);
                }
                let c_out = coord_handle.join().map_err(|_| "coordinator panicked".to_string())?;
                rx_res?;
                c_out
            });
            let (s0, s1, card_pmu_json, card_stats) = coord_res.map_err(io::Error::other)?;

            let eth1 = port.stats().unwrap_or_default();
            let eth_ipkts = eth1.ipackets.saturating_sub(eth0.ipackets);
            let eth_imissed = eth1.imissed.saturating_sub(eth0.imissed);
            let eth_ierrors = eth1.ierrors.saturating_sub(eth0.ierrors);
            let eth_nombuf = eth1.rx_nombuf.saturating_sub(eth0.rx_nombuf);

            let card_sent: u64 = {
                let pat = "\"q0_sent\":";
                card_stats
                    .find(pat)
                    .and_then(|i| {
                        let rest = &card_stats[i + pat.len()..];
                        let end = rest.find(|c: char| !c.is_ascii_digit()).unwrap_or(rest.len());
                        rest[..end].parse().ok()
                    })
                    .unwrap_or(taken_total)
            };
            let lost = card_sent.saturating_sub(taken_total);

            std::hint::black_box(app_acc);
            let d = HostPmu::delta(&s0, &s1);
            let rx_mpps = meas_pkts as f64 / (d.wall_s * 1e6);
            let rx_gbps = (meas_pkts as f64 * (args.frame_size as f64 + 20.0) * 8.0) / (d.wall_s * 1e9);
            let avg_batch = if meas_nonempty_polls > 0 {
                meas_pkts as f64 / meas_nonempty_polls as f64
            } else {
                0.0
            };
            let gross_cyc_per_pkt = if meas_pkts > 0 {
                d.cycles as f64 / meas_pkts as f64
            } else {
                0.0
            };
            let net_io_cycles = if args.work_cycles_per_step > 0.0 {
                (d.cycles as f64 - meas_empty_polls as f64 * args.work_cycles_per_step).max(0.0)
            } else {
                d.cycles as f64
            };
            let net_cyc_per_pkt = if meas_pkts > 0 {
                net_io_cycles / meas_pkts as f64
            } else {
                0.0
            };
            let net_cyc_per_batch = if meas_credit_updates > 0 {
                net_io_cycles / meas_credit_updates as f64
            } else {
                0.0
            };
            let iova_tot = iova_same + iova_changed;
            let iova_unchanged_pct = if iova_tot > 0 {
                iova_same as f64 * 100.0 / iova_tot as f64
            } else {
                0.0
            };
            let pause_drained_mean = if !pause_drained_samples.is_empty() {
                pause_drained_samples.iter().sum::<u64>() as f64 / pause_drained_samples.len() as f64
            } else {
                0.0
            };
            let pause_drained_max = pause_drained_samples.iter().copied().max().unwrap_or(0);
            let (rf_mean, rf_p50, _, rf_p99, _, rf_max) = summarize_us(&mut resume_first_us);
            let (_, gap_p50, _, gap_p99, gap_p999, gap_max) = summarize_us(&mut inter_batch_gaps_us);

            println!(
                "{{\"mode\":\"rx-stream\",\"credit\":\"{}\",\"recycle\":\"{}\",\"poison\":{},\"align_refill\":{},\"use_fifo\":{},\"fifo_cap\":{},\"frame_size\":{},\"rx_desc\":{},\"pool_size\":{},\"credit_batch\":{},\"r_eff\":{},\"r_eff_bytes\":{},\"target_mpps\":{:.3},\"meas_pkts\":{meas_pkts},\"total_rx\":{taken_total},\"card_sent\":{card_sent},\"lost\":{lost},\"meas_seq_gap_lost\":{meas_seq_gap_lost},\"total_seq_gap_lost\":{total_seq_gap_lost},\"bad\":{meas_bad},\"torn\":{meas_torn},\"ooo\":{meas_ooo},\"total_bad\":{total_bad},\"total_torn\":{total_torn},\"total_ooo\":{total_ooo},\"rx_mpps\":{rx_mpps:.4},\"rx_gbps\":{rx_gbps:.4},\"avg_batch\":{avg_batch:.2},\"empty_polls\":{meas_empty_polls},\"nonempty_polls\":{meas_nonempty_polls},\"app_steps\":{meas_app_steps},\"gross_cyc_per_pkt\":{gross_cyc_per_pkt:.3},\"net_cyc_per_pkt\":{net_cyc_per_pkt:.3},\"net_cyc_per_batch\":{net_cyc_per_batch:.3},\"credit_updates\":{credit_updates},\"meas_credit_updates\":{meas_credit_updates},\"iova_unchanged_pct\":{iova_unchanged_pct:.2},\"iova_same\":{iova_same},\"iova_changed\":{iova_changed},\"pause_count\":{pause_count},\"pause_drained_mean\":{pause_drained_mean:.2},\"pause_drained_max\":{pause_drained_max},\"resume_first_mean_us\":{rf_mean:.3},\"resume_first_p50_us\":{rf_p50:.3},\"resume_first_p99_us\":{rf_p99:.3},\"resume_first_max_us\":{rf_max:.3},\"gap_p50_us\":{gap_p50:.3},\"gap_p99_us\":{gap_p99:.3},\"gap_p999_us\":{gap_p999:.3},\"gap_max_us\":{gap_max:.3},\"eth_ipackets\":{eth_ipkts},\"eth_imissed\":{eth_imissed},\"eth_ierrors\":{eth_ierrors},\"eth_nombuf\":{eth_nombuf},\"page_faults\":{},\"host_pmu\":{},\"card_pmu\":{card_pmu_json},\"card_stats\":{card_stats}}}",
                args.credit.as_str(),
                args.recycle.as_str(),
                args.poison,
                args.align_refill,
                args.use_fifo,
                args.fifo_cap,
                args.frame_size,
                args.rx_desc,
                args.pool_size,
                args.credit_batch,
                args.r_eff,
                args.r_eff_bytes,
                args.target_mpps,
                pf1.saturating_sub(pf0),
                d.to_json()
            );
            return Ok(());
        }

        // ---- Mode 6: DualQueue (Control D2 — Q0 healthy cpu40 vs Q1 stalled cpu41) ----
        if args.mode == Mode::DualQueue {
            if eal.lcores() != 2 {
                return Err("dual-queue needs exactly 2 EAL lcores (e.g. -l 40,41)".into());
            }
            let _ = port.reset_stats();
            let eth0 = port.stats().unwrap_or_default();

            let run_cmd = format!(
                "RUN_START mode=stream credit={} host_mac={host_mac} frame_size={} run_gen={} queues=2 r_eff={} r_eff_bytes={} target_mpps={:.4} q1_rate_frac={:.4} use_fifo={} fifo_cap={}",
                args.credit.as_str(),
                args.frame_size,
                args.run_gen,
                args.r_eff,
                args.r_eff_bytes,
                args.target_mpps,
                args.q1_rate_frac,
                if args.use_fifo { 1 } else { 0 },
                args.fifo_cap
            );
            sync.cmd(&run_cmd)?;

            let (_st, mut rx_qs, _tx_qs) = port.split_rx_tx_queues();
            let rx_q1: RxQueue<'_, '_> = rx_qs.pop().unwrap();
            let rx_q0: RxQueue<'_, '_> = rx_qs.pop().unwrap();

            let phase = AtomicU8::new(PHASE_WARMUP);
            let q1_ready = AtomicBool::new(false);
            let q1_done = AtomicBool::new(false);
            let host_go_sent = AtomicBool::new(false);
            let stop_q1_flag = AtomicBool::new(false);
            let coord_abort = AtomicBool::new(false);
            let q1_meas_pkts = AtomicU64::new(0);
            let q1_total_pkts = AtomicU64::new(0);
            let q1_bad = AtomicU64::new(0);
            let q1_torn = AtomicU64::new(0);
            let q1_ooo = AtomicU64::new(0);
            let q1_meas_seq_gap = AtomicU64::new(0);
            let q1_total_seq_gap = AtomicU64::new(0);
            let q1_pauses = AtomicU64::new(0);
            let q1_drained_sum = AtomicU64::new(0);
            let q1_drained_max = AtomicU64::new(0);
            let q1_pf = AtomicU64::new(0);
            let q1_resume_first_us = std::sync::Mutex::new(Vec::<f64>::new());

            let q1_slot = lease.map_slot(&args.bar)?;
            let sync_mtx = std::sync::Mutex::new(sync);
            let q0_out = std::sync::Mutex::new(None::<(u64, u64, u64, u64, u64, u64, u64, Vec<f64>, u64, HostSnap, HostSnap, String, String)>);

            let lcore_res = eal.on_each_lcore(
                vec![(rx_q0, slot), (rx_q1, q1_slot)],
                |idx, (rx_q, slot_q)| -> Result<(), String> {
                    if idx == 1 {
                        let _ready_guard = StopOnDrop(&q1_ready);
                        let _done_guard = StopOnDrop(&q1_done);
                        let mut q1 = rx_q;
                        let slot1 = slot_q;
                        let mut rx_b: Burst<'_, 32> = Burst::new();
                        let mut exp_seq = 1u64;
                        let mut taken = 0u64;
                        let mut last_cred = 0u64;
                        let period_ticks = (args.q1_period_ms as f64 * 1e-3 * tsc_hz) as u64;
                        let pause_ticks = (args.q1_pause_us as f64 * 1e-6 * tsc_hz) as u64;
                        let mut next_pause = 0u64;
                        let mut was_meas = false;
                        let mut ended_meas = false;
                        let mut pf0 = 0u64;
                        let mut pf1 = 0u64;
                        let mut stop_drain_start: Option<Instant> = None;
                        let mut stop_empty_since: Option<Instant> = None;

                        q1_ready.store(true, Ordering::Release);

                        while !stop_q1_flag.load(Ordering::Acquire) {
                            let p = phase.load(Ordering::Acquire);
                            let meas = p == PHASE_MEASURE;
                            if meas && !was_meas {
                                pf0 = thread_page_faults();
                                was_meas = true;
                                if args.q1_period_ms > 0 && args.q1_pause_us > 0 {
                                    next_pause = rdtsc().wrapping_add(period_ticks / 2);
                                }
                            } else if !meas && was_meas && !ended_meas {
                                pf1 = thread_page_faults();
                                ended_meas = true;
                            }
                            if p == PHASE_STOP_DRAIN {
                                let start = *stop_drain_start.get_or_insert_with(Instant::now);
                                if start.elapsed() >= Duration::from_millis(200) {
                                    break;
                                }
                            }
                            if meas && args.q1_period_ms > 0 && args.q1_pause_us > 0 {
                                let now = rdtsc();
                                if now >= next_pause {
                                    let end = now.wrapping_add(pause_ticks);
                                    while rdtsc() < end
                                        && phase.load(Ordering::Acquire) == PHASE_MEASURE
                                        && !stop_q1_flag.load(Ordering::Acquire)
                                    {
                                        std::hint::spin_loop();
                                    }
                                    q1_pauses.fetch_add(1, Ordering::Relaxed);
                                    let q1_res_t0 = rdtsc_after_loads();
                                    let mut q1_got_first = false;
                                    let mut drained = 0u64;
                                    let snap_idle = (12.0e-6 * tsc_hz) as u64;
                                    let mut last_got = rdtsc();
                                    loop {
                                        let n = q1.rx(&mut rx_b);
                                        if n == 0 {
                                            if rdtsc().saturating_sub(last_got) >= snap_idle {
                                                break;
                                            }
                                            continue;
                                        }
                                        last_got = rdtsc();
                                        if !q1_got_first {
                                            let t_f = rdtsc_after_loads();
                                            q1_resume_first_us.lock().unwrap().push(t_f.saturating_sub(q1_res_t0) as f64 * 1e6 / tsc_hz);
                                            q1_got_first = true;
                                        }
                                        for pkt in rx_b.as_slice() {
                                            match verify_frame(pkt.data(), args.frame_size as usize, args.run_gen) {
                                                FrameCheck::Ok { seq, .. } => {
                                                    if seq != exp_seq {
                                                        let gap = seq.saturating_sub(exp_seq);
                                                        q1_ooo.fetch_add(1, Ordering::Relaxed);
                                                        q1_meas_seq_gap.fetch_add(gap, Ordering::Relaxed);
                                                        q1_total_seq_gap.fetch_add(gap, Ordering::Relaxed);
                                                        exp_seq = seq + 1;
                                                    } else {
                                                        exp_seq += 1;
                                                    }
                                                }
                                                FrameCheck::Bad => { q1_bad.fetch_add(1, Ordering::Relaxed); }
                                                FrameCheck::Torn => { q1_torn.fetch_add(1, Ordering::Relaxed); }
                                            }
                                        }
                                        drained += n as u64;
                                        taken += n as u64;
                                        q1_meas_pkts.fetch_add(n as u64, Ordering::Relaxed);
                                        rx_b.clear();
                                        if drained >= args.rx_desc as u64 {
                                            break;
                                        }
                                    }
                                    q1_drained_sum.fetch_add(drained, Ordering::Relaxed);
                                    let _ = q1_drained_max.fetch_max(drained, Ordering::Relaxed);
                                    if taken > last_cred {
                                        let _ = issue_credit(args.credit, Some(&slot1), 1, None, taken, args.run_gen, taken * args.frame_size as u64);
                                        last_cred = taken;
                                    }
                                    next_pause = rdtsc().wrapping_add(period_ticks);
                                    continue;
                                }
                            }

                            let n = q1.rx(&mut rx_b);
                            if n == 0 {
                                if p == PHASE_STOP_DRAIN {
                                    let since = *stop_empty_since.get_or_insert_with(Instant::now);
                                    if since.elapsed() >= Duration::from_millis(40) {
                                        break;
                                    }
                                }
                                continue;
                            }
                            stop_empty_since = None;
                            for pkt in rx_b.as_slice() {
                                match verify_frame(pkt.data(), args.frame_size as usize, args.run_gen) {
                                    FrameCheck::Ok { seq, .. } => {
                                        if seq != exp_seq {
                                            let gap = seq.saturating_sub(exp_seq);
                                            q1_total_seq_gap.fetch_add(gap, Ordering::Relaxed);
                                            if meas {
                                                q1_ooo.fetch_add(1, Ordering::Relaxed);
                                                q1_meas_seq_gap.fetch_add(gap, Ordering::Relaxed);
                                            }
                                            exp_seq = seq + 1;
                                        } else {
                                            exp_seq += 1;
                                        }
                                    }
                                    FrameCheck::Bad => if meas { q1_bad.fetch_add(1, Ordering::Relaxed); },
                                    FrameCheck::Torn => if meas { q1_torn.fetch_add(1, Ordering::Relaxed); },
                                }
                            }
                            taken += n as u64;
                            if meas {
                                q1_meas_pkts.fetch_add(n as u64, Ordering::Relaxed);
                            }
                            rx_b.clear();
                            if taken.saturating_sub(last_cred) >= args.credit_batch {
                                let _ = issue_credit(args.credit, Some(&slot1), 1, None, taken, args.run_gen, taken * args.frame_size as u64);
                                last_cred = taken;
                            }
                        }
                        if was_meas && !ended_meas {
                            pf1 = thread_page_faults();
                        }
                        if was_meas {
                            q1_pf.store(pf1.saturating_sub(pf0), Ordering::Relaxed);
                        }
                        q1_total_pkts.store(taken, Ordering::Relaxed);
                        return Ok(());
                    }

                    // Main lcore (idx == 0): RAII guards guarantee worker lcore and coordinator NEVER deadlock
                    let _stop_q1_guard = StopOnDrop(&stop_q1_flag);
                    let _abort_coord_guard = StopOnDrop(&coord_abort);

                    let mut rx_q0 = rx_q;
                    let slot = slot_q;
                    let mut q0_meas_pkts = 0u64;
                    let mut q0_bad = 0u64;
                    let mut q0_torn = 0u64;
                    let mut q0_ooo = 0u64;
                    let mut q0_meas_seq_gap = 0u64;
                    let mut q0_total_seq_gap = 0u64;
                    let mut q0_gaps_us: Vec<f64> = vec![1.0f64; 200_000];
                    std::hint::black_box(&q0_gaps_us);
                    q0_gaps_us.clear();
                    let mut rx_b: Burst<'_, 32> = Burst::new();
                    let mut exp_seq = 1u64;
                    let mut taken = 0u64;
                    let mut last_cred = 0u64;
                    let mut pf0 = 0u64;
                    let mut pf1 = 0u64;

                    let with_card_pmu = args.with_card_pmu;
                    let warmup_ms = args.warmup_ms;
                    let duration_ms = args.duration_ms;
                    let coord_res: Result<(HostSnap, HostSnap, String, String), String> = thread::scope(|scope| {
                        let coord_handle = scope.spawn(|| -> Result<(HostSnap, HostSnap, String, String), String> {
                            pin_thread_to_cpu(COORD_CPU);
                            while !host_go_sent.load(Ordering::Acquire) {
                                if coord_abort.load(Ordering::Acquire) {
                                    let _ = sync_mtx.lock().unwrap().cmd("RUN_STOP");
                                    return Err("dual-queue host aborted before GO".into());
                                }
                                thread::sleep(Duration::from_millis(1));
                            }
                            if !sleep_abortable(Duration::from_millis(warmup_ms), &coord_abort) {
                                let _ = sync_mtx.lock().unwrap().cmd("RUN_STOP");
                                phase.store(PHASE_STOP_DRAIN, Ordering::Release);
                                return Err("dual-queue host aborted during warmup".into());
                            }
                            if with_card_pmu {
                                sync_mtx.lock().unwrap().cmd("PMU_START").map_err(|e| e.to_string())?;
                            }
                            let s0 = pmu.snap();
                            phase.store(PHASE_MEASURE, Ordering::Release);

                            if !sleep_abortable(Duration::from_millis(duration_ms), &coord_abort) {
                                phase.store(PHASE_DRAIN, Ordering::Release);
                                if with_card_pmu {
                                    let _ = sync_mtx.lock().unwrap().cmd("PMU_STOP");
                                }
                                let _ = sync_mtx.lock().unwrap().cmd("RUN_STOP");
                                phase.store(PHASE_STOP_DRAIN, Ordering::Release);
                                return Err("dual-queue host aborted during measure".into());
                            }

                            phase.store(PHASE_DRAIN, Ordering::Release);
                            let s1 = pmu.snap();
                            let card_pmu_json = if with_card_pmu {
                                let r = sync_mtx.lock().unwrap().cmd("PMU_STOP").map_err(|e| e.to_string())?;
                                extract_json_obj(&r, "pmu")
                            } else {
                                "{}".to_string()
                            };
                            let stop_resp = sync_mtx.lock().unwrap().cmd("RUN_STOP").map_err(|e| e.to_string())?;
                            let card_stats = extract_json_obj(&stop_resp, "stats");
                            phase.store(PHASE_STOP_DRAIN, Ordering::Release);
                            Ok((s0, s1, card_pmu_json, card_stats))
                        });

                        while !q1_ready.load(Ordering::Acquire) {
                            std::hint::spin_loop();
                        }
                        let q0_res: Result<(), String> = (|| {
                            slot.check().map_err(|e| e.to_string())?;
                            slot.write_u64(credit_line_offset(15), args.run_gen);
                            sfence();
                            host_go_sent.store(true, Ordering::Release);

                            let mut was_meas = false;
                            let mut ended_meas = false;
                            let mut last_rx_tsc = rdtsc();
                            let mut stop_drain_start: Option<Instant> = None;
                            let mut stop_empty_since: Option<Instant> = None;

                            loop {
                                let p = phase.load(Ordering::Acquire);
                                let in_meas = p == PHASE_MEASURE;
                                if in_meas && !was_meas {
                                    pf0 = thread_page_faults();
                                    was_meas = true;
                                    last_rx_tsc = rdtsc();
                                } else if !in_meas && was_meas && !ended_meas {
                                    pf1 = thread_page_faults();
                                    ended_meas = true;
                                }
                                if p == PHASE_STOP_DRAIN {
                                    let start = *stop_drain_start.get_or_insert_with(Instant::now);
                                    if start.elapsed() >= Duration::from_millis(200) {
                                        break;
                                    }
                                }

                                let n = rx_q0.rx(&mut rx_b);
                                if n == 0 {
                                    if p == PHASE_STOP_DRAIN {
                                        let since = *stop_empty_since.get_or_insert_with(Instant::now);
                                        if since.elapsed() >= Duration::from_millis(40) {
                                            break;
                                        }
                                    }
                                    continue;
                                }
                                stop_empty_since = None;

                                if in_meas {
                                    let now_tsc = rdtsc();
                                    if q0_gaps_us.len() < 200_000 {
                                        q0_gaps_us.push(now_tsc.saturating_sub(last_rx_tsc) as f64 * 1e6 / tsc_hz);
                                    }
                                    last_rx_tsc = now_tsc;
                                }
                                for pkt in rx_b.as_slice() {
                                    match verify_frame(pkt.data(), args.frame_size as usize, args.run_gen) {
                                        FrameCheck::Ok { seq, .. } => {
                                            if seq != exp_seq {
                                                let gap = seq.saturating_sub(exp_seq);
                                                q0_total_seq_gap += gap;
                                                if in_meas {
                                                    q0_ooo += 1;
                                                    q0_meas_seq_gap += gap;
                                                }
                                                exp_seq = seq + 1;
                                            } else {
                                                exp_seq += 1;
                                            }
                                        }
                                        FrameCheck::Bad => if in_meas { q0_bad += 1; },
                                        FrameCheck::Torn => if in_meas { q0_torn += 1; },
                                    }
                                }
                                taken += n as u64;
                                if in_meas {
                                    q0_meas_pkts += n as u64;
                                }
                                rx_b.clear();
                                if taken.saturating_sub(last_cred) >= args.credit_batch {
                                    issue_credit(args.credit, Some(&slot), 0, None, taken, args.run_gen, taken * args.frame_size as u64).map_err(|e| e.to_string())?;
                                    last_cred = taken;
                                }
                            }
                            if was_meas && !ended_meas {
                                pf1 = thread_page_faults();
                            }
                            Ok(())
                        })();

                        if q0_res.is_err() {
                            coord_abort.store(true, Ordering::Release);
                        }
                        let c_out = coord_handle.join().map_err(|_| "dual-queue coordinator panicked".to_string())?;
                        q0_res?;
                        c_out
                    });
                    let (s0_snap, s1_snap, card_pmu_json, card_stats) = coord_res?;
                    let wait_q1 = Instant::now();
                    while !q1_done.load(Ordering::Acquire) && wait_q1.elapsed() < Duration::from_millis(250) {
                        thread::sleep(Duration::from_millis(1));
                    }
                    let q0_pf = pf1.saturating_sub(pf0);

                    *q0_out.lock().unwrap() = Some((
                        q0_meas_pkts,
                        taken,
                        q0_bad,
                        q0_torn,
                        q0_ooo,
                        q0_meas_seq_gap,
                        q0_total_seq_gap,
                        q0_gaps_us,
                        q0_pf,
                        s0_snap,
                        s1_snap,
                        card_pmu_json,
                        card_stats,
                    ));
                    Ok(())
                },
            );
            for r in lcore_res {
                r.map_err(io::Error::other)?;
            }
            let (q0_meas_pkts, q0_total_pkts, q0_bad, q0_torn, q0_ooo, q0_meas_seq_gap, q0_total_seq_gap, mut q0_gaps_us, q0_pf, s0_val, s1_val, card_pmu_json, card_stats) =
                q0_out.into_inner().unwrap().ok_or("missing q0 result")?;

            let eth1 = port.stats().unwrap_or_default();
            let eth_imissed = eth1.imissed.saturating_sub(eth0.imissed);
            let eth_ierrors = eth1.ierrors.saturating_sub(eth0.ierrors);

            let extract_u64 = |k: &str| -> u64 {
                let pat = format!("\"{k}\":");
                card_stats
                    .find(&pat)
                    .and_then(|i| {
                        let rest = &card_stats[i + pat.len()..];
                        let end = rest.find(|c: char| !c.is_ascii_digit()).unwrap_or(rest.len());
                        rest[..end].parse().ok()
                    })
                    .unwrap_or(0)
            };
            let c_q0_sent = extract_u64("q0_sent");
            let c_q1_sent = extract_u64("q1_sent");
            let q1_tot = q1_total_pkts.load(Ordering::Relaxed);
            let q0_lost = c_q0_sent.saturating_sub(q0_total_pkts);
            let q1_lost = c_q1_sent.saturating_sub(q1_tot);

            let d = HostPmu::delta(&s0_val, &s1_val);
            let q0_mpps = q0_meas_pkts as f64 / (d.wall_s * 1e6);
            let q0_gbps = (q0_meas_pkts as f64 * (args.frame_size as f64 + 20.0) * 8.0) / (d.wall_s * 1e9);
            let q1_m = q1_meas_pkts.load(Ordering::Relaxed);
            let q1_mpps = q1_m as f64 / (d.wall_s * 1e6);
            let q1_p = q1_pauses.load(Ordering::Relaxed);
            let q1_d_mean = if q1_p > 0 {
                q1_drained_sum.load(Ordering::Relaxed) as f64 / q1_p as f64
            } else {
                0.0
            };
            let q1_d_max = q1_drained_max.load(Ordering::Relaxed);
            let (g_mean, g_p50, g_p90, g_p99, g_p999, g_max) = summarize_us(&mut q0_gaps_us);
            let q0_gaps_gt_50us = q0_gaps_us.iter().filter(|&&x| x > 50.0).count();
            let q0_gaps_gt_500us = q0_gaps_us.iter().filter(|&&x| x > 500.0).count();
            let mut q1_rf_vec = q1_resume_first_us.into_inner().unwrap();
            let (_, q1_rf_p50, _, q1_rf_p99, _, q1_rf_max) = summarize_us(&mut q1_rf_vec);

            println!(
                "{{\"mode\":\"dual-queue\",\"credit\":\"{}\",\"use_fifo\":{},\"fifo_cap\":{},\"credit_batch\":{},\"frame_size\":{},\"rx_desc\":{},\"r_eff\":{},\"target_mpps\":{:.3},\"q1_rate_frac\":{:.4},\"q1_period_ms\":{},\"q1_pause_us\":{},\"q0_meas_pkts\":{q0_meas_pkts},\"q0_mpps\":{q0_mpps:.4},\"q0_gbps\":{q0_gbps:.4},\"q0_lost\":{q0_lost},\"q0_meas_seq_gap_lost\":{q0_meas_seq_gap},\"q0_total_seq_gap_lost\":{q0_total_seq_gap},\"q0_bad\":{q0_bad},\"q0_torn\":{q0_torn},\"q0_ooo\":{q0_ooo},\"q0_gap_mean_us\":{g_mean:.3},\"q0_gap_p50_us\":{g_p50:.3},\"q0_gap_p90_us\":{g_p90:.3},\"q0_gap_p99_us\":{g_p99:.3},\"q0_gap_p999_us\":{g_p999:.3},\"q0_gap_max_us\":{g_max:.3},\"q0_gaps_gt_50us\":{q0_gaps_gt_50us},\"q0_gaps_gt_500us\":{q0_gaps_gt_500us},\"q1_meas_pkts\":{q1_m},\"q1_mpps\":{q1_mpps:.4},\"q1_lost\":{q1_lost},\"q1_meas_seq_gap_lost\":{},\"q1_total_seq_gap_lost\":{},\"q1_bad\":{},\"q1_torn\":{},\"q1_ooo\":{},\"q1_pauses\":{q1_p},\"q1_drained_mean\":{q1_d_mean:.2},\"q1_drained_max\":{q1_d_max},\"q1_resume_first_p50_us\":{q1_rf_p50:.3},\"q1_resume_first_p99_us\":{q1_rf_p99:.3},\"q1_resume_first_max_us\":{q1_rf_max:.3},\"eth_imissed\":{eth_imissed},\"eth_ierrors\":{eth_ierrors},\"q0_page_faults\":{q0_pf},\"q1_page_faults\":{},\"proc_page_faults\":{},\"host_pmu\":{},\"card_pmu\":{card_pmu_json},\"card_stats\":{card_stats}}}",
                args.credit.as_str(),
                args.use_fifo,
                args.fifo_cap,
                args.credit_batch,
                args.frame_size,
                args.rx_desc,
                args.r_eff,
                args.target_mpps,
                args.q1_rate_frac,
                args.q1_period_ms,
                args.q1_pause_us,
                q1_meas_seq_gap.load(Ordering::Relaxed),
                q1_total_seq_gap.load(Ordering::Relaxed),
                q1_bad.load(Ordering::Relaxed),
                q1_torn.load(Ordering::Relaxed),
                q1_ooo.load(Ordering::Relaxed),
                q1_pf.load(Ordering::Relaxed),
                process_page_faults(),
                d.to_json()
            );
            return Ok(());
        }

        Ok(())
    }
}
