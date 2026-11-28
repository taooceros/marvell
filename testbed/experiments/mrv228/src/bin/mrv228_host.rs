//! Host-side (`saturn` Socket 1 `cpu40 @ 3.90 GHz` + co-located 32 MiB 2 MiB-hugepage
//! KV tenant on `cpu41`) 5-arm single-variable showdown for MRV-228:
//!   - `--mode kv-solo`     : Unloaded 32 MiB KV tenant baseline (~41 ns p99)
//!   - `--mode bp1-stop`    : Arm 1 — `BP=1` + `allow_small_oq=1` (`D=256`, compact 3.7 MiB
//!                            `stack` LIFO hot pool), pauses `port.rx(q)` when stalled/slow
//!                            flow cannot be consumed -> triggers HW BP=1 (preserves DDIO/KV,
//!                            exposes severe in-queue HoL blocking on healthy RPCs).
//!   - `--mode bp1-drain`   : Arm 2 — `BP=1` + `allow_small_oq=1` (`D=256`, large `stack`
//!                            LIFO pool), continuously drains HW ring into per-flow host
//!                            software queues (`host_flow_q[fid]`) to avoid HoL -> spills
//!                            slow/elephant bursts into host RAM (destroys DDIO & KV p99).
//!   - `--mode large-sdp`   : Arm 3 — Default passive Large SDP (`D=2048`, `ring_sp_sc` pool)
//!                            + per-flow host staging.
//!   - `--mode ceio`        : Arm 4 — Fortified CEIO §4.1-4.2 dual-path (`4` Fast `D=2048` +
//!                            `4` Slow `D=512`, 2P1C `SwMergeRing`, `async_recv()` pull,
//!                            per-flow BAR4 credit visibility).
//!   - `--mode per-flow-cr` : Arm 5 — OUR DESIGN: identical `D=256` compact 3.7 MiB `stack`
//!                            LIFO hot pool as `bp1-stop` + 64 B packed BAR4 per-flow
//!                            fence-free MOVDIR64B credits (16 flows/line, 1,024 flows in 4 KiB)
//!                            -> zero in-queue HoL, 0.00% loss, ~99-100% DDIO, ~41 ns KV p99!

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv228_host runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::collections::VecDeque;
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, EthStats, Mbuf, Mempool, Port, rdtsc, rdtsc_after_loads};
    use testbed::lease::{Lease, LeasedSlot};
    use testbed::mmio::{has_movdir64b, movdir64b};
    use mrv228::{
        ACK_MBUF_BUF_SIZE, BAR4_CEIO_PULL_OFF, BAR4_Q_CREDIT_OFF, CLASS_RPC_HI, CeioSlowPullLine,
        DEFAULT_CEIO_FAST_RX_DESC, DEFAULT_CEIO_LAZY_BATCH, DEFAULT_CEIO_PULL_WINDOW,
        DEFAULT_CEIO_SLOW_RX_DESC, DEFAULT_CREDIT_BATCH, DEFAULT_R_EFF_PER_Q, DEFAULT_R_FLOW,
        DEFAULT_SMALL_RX_DESC, FLAG_CEIO_LLC_LEAK, FLAG_PHASE_PAUSED, FLOW_CREDIT_GROUPS,
        FlowCreditLine, FlowOrderTracker, HOST_CTRL_ADDR, HostQueueCreditLine, MAX_CARD_CORES,
        MAX_DRAIN, MAX_FLOWS, MIN_WIRE_PAYLOAD, OP_DATA, PATH_CEIO_SLOW,
        SDP_HDR_LEN, SDP_MBUF_BUF_SIZE, SDP_MTU, SwMergeRing2P1C, SwProducer, encode_wire_ack64,
        flow_credit_line_off, splitmix64, verify_and_touch_wire_pkt, verify_sdp_credit_safety,
    };
    use testbed::swap_macs;

    const CTRL_CPU: usize = 44;
    const HUGE_2M: usize = 2 * 1024 * 1024;
    const MPOL_BIND: i32 = 2;
    const MPOL_MF_STRICT: u32 = 1;
    const MPOL_MF_MOVE: u32 = 2;
    const MPOL_F_NODE: u32 = 1 << 0;
    const MPOL_F_ADDR: u32 = 1 << 1;

    const TS_MAX_BUCKETS: usize = 2048;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Mode {
        KvSolo,
        Bp1Stop,
        Bp1Drain,
        LargeSdp,
        Ceio,
        PerFlowCr,
    }

    impl Mode {
        fn as_str(self) -> &'static str {
            match self {
                Mode::KvSolo => "kv-solo",
                Mode::Bp1Stop => "bp1-stop",
                Mode::Bp1Drain => "bp1-drain",
                Mode::LargeSdp => "large-sdp",
                Mode::Ceio => "ceio",
                Mode::PerFlowCr => "per-flow-cr",
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = Mode::PerFlowCr)]
        mode: Mode,
        #[arg(long, default_value = testbed::lease::DEFAULT_ADDR)]
        card: String,
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar4: String,
        #[arg(long, default_value_t = 4)]
        lanes: usize,
        #[arg(long, default_value_t = DEFAULT_SMALL_RX_DESC)]
        small_rx_desc: u16,
        #[arg(long, default_value_t = DEFAULT_R_EFF_PER_Q)]
        r_eff_per_q: u64,
        #[arg(long, default_value_t = DEFAULT_CREDIT_BATCH)]
        credit_batch: u64,
        #[arg(long, default_value_t = 2048)]
        large_rx_desc: u16,
        #[arg(long, default_value_t = DEFAULT_CEIO_FAST_RX_DESC)]
        ceio_fast_rx_desc: u16,
        #[arg(long, default_value_t = DEFAULT_CEIO_SLOW_RX_DESC)]
        ceio_slow_rx_desc: u16,
        #[arg(long, default_value_t = DEFAULT_CEIO_LAZY_BATCH)]
        ceio_lazy_batch: u64,
        #[arg(long, default_value_t = DEFAULT_CEIO_PULL_WINDOW)]
        ceio_pull_window: u32,
        #[arg(long, default_value_t = 2048)]
        sdp_tx_desc: u16,
        /// Number of troublemaker flow IDs (`0..bad_flows`, default 4 = 1 per HW queue).
        #[arg(long, default_value_t = 4)]
        bad_flows: usize,
        /// Per-lane host staging cap for `bp1-stop` (default = DEFAULT_R_FLOW = 32,
        /// matching `per-flow-cr`'s exact per-flow host staging budget for strict single-variable fairness!).
        #[arg(long, default_value_t = DEFAULT_R_FLOW as usize)]
        bp_stop_stage_cap: usize,
        /// Scenario A: periodic consumer stall duration on troublemaker flows in ms (0 = off).
        #[arg(long, default_value_t = 0)]
        stall_ms: u64,
        /// Scenario A: stall repetition period in ms (must be > stall_ms when stall_ms > 0).
        #[arg(long, default_value_t = 40)]
        stall_period_ms: u64,
        /// Scenario B: aggregate host consumer drain rate cap for troublemaker elephant flows
        /// in Gbps (0.0 = unlimited; e.g. 16.0 Gbps = 4.0 Gbps/lane -> builds real 16-64 MiB burst backlog).
        #[arg(long, default_value_t = 0.0)]
        bulk_rate_gbps: f64,
        /// Touch every 64 B cache line of every received packet to exercise real DDIO vs LLC.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        touch_payload: bool,
        /// Co-located 32 MiB hugepage KV tenant CPU on Socket 1 (41; 0 = disabled).
        #[arg(long, default_value_t = 41)]
        coloc_kv_cpu: usize,
        /// KV table size in bytes (default 32 MiB).
        #[arg(long, default_value_t = 32 * 1024 * 1024)]
        kv_bytes: usize,
        /// Solo measurement duration in ms for `--mode kv-solo`.
        #[arg(long, default_value_t = 1200)]
        solo_ms: u64,
        /// Timeseries bucket width in microseconds (default 500 us).
        #[arg(long, default_value_t = 500)]
        ts_bucket_us: u64,
        /// Enable 100% non-multiplexed Host Core + Socket-1 CHA/IIO/IMC PMU counters.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        pmu: bool,
        #[arg(long, default_value = HOST_CTRL_ADDR)]
        ctrl: String,
    }

    // -------------------------------------------------------------------------
    // NUMA Node 1 2 MiB HugePage Allocator & 32 MiB KV Worker (exact ~41 ns baseline)
    // -------------------------------------------------------------------------
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
                return Err(format!("mmap(MAP_HUGETLB, {} MiB) failed: {}", len >> 20, io::Error::last_os_error()).into());
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
                    (MPOL_MF_STRICT | MPOL_MF_MOVE) as usize,
                )
            };
            if ret != 0 {
                let err = io::Error::last_os_error();
                unsafe { libc::munmap(ptr, len) };
                return Err(format!("mbind(node=1, {} MiB) failed: {err}", len >> 20).into());
            }
            let p = ptr.cast::<u8>();
            let mut off = 0usize;
            while off < len {
                unsafe { p.add(off).write_volatile(0) };
                off += 4096;
            }
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
                    return Err(format!("NUMA verify failed at off={check_off}: ret={r} node={node}").into());
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

    fn pin_thread(cpu: usize) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu, &mut set);
            libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set);
        }
    }

    fn current_cpu() -> i32 {
        unsafe { libc::sched_getcpu() }
    }

    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct KvEntry {
        key: u64,
        val: [u64; 6],
        next_salt: u64,
    }

    const _: () = assert!(size_of::<KvEntry>() == 64);

    fn init_kv_table(buf: &NumaHugeBuf, kv_bytes: usize) {
        let n = kv_bytes / size_of::<KvEntry>();
        let entries = buf.as_ptr().cast::<KvEntry>();
        for i in 0..n {
            let k = (i as u64) ^ 0x55aa_2280_9911_2233;
            let salt = splitmix64((i as u64).wrapping_mul(0x228_cafe_0001) ^ 0xfeed_face);
            let mut val = [0u64; 6];
            for (j, slot) in val.iter_mut().enumerate() {
                *slot = salt.wrapping_add((j as u64 + 1).wrapping_mul(0x1111_2222_3333_4445));
            }
            unsafe {
                entries.add(i).write(KvEntry { key: k, val, next_salt: salt });
            }
        }
    }

    #[inline(always)]
    unsafe fn rdtsc_begin() -> u64 {
        let lo: u32;
        let hi: u32;
        unsafe {
            std::arch::asm!(
                "lfence",
                "rdtsc",
                "lfence",
                out("eax") lo,
                out("edx") hi,
                options(nostack, nomem, preserves_flags),
            );
        }
        ((hi as u64) << 32) | (lo as u64)
    }

    #[inline(always)]
    unsafe fn rdtsc_end(dep: u64) -> u64 {
        let lo: u32;
        let hi: u32;
        unsafe {
            std::arch::asm!(
                "rdtscp",
                in("rdi") dep,
                out("eax") lo,
                out("edx") hi,
                out("ecx") _,
                options(nostack, nomem, preserves_flags),
            );
        }
        ((hi as u64) << 32) | (lo as u64)
    }

    fn calibrate_kv_timer_floor() -> u64 {
        let mut samples = [0u64; 256];
        let mut acc = 0u64;
        for s in &mut samples {
            let t0 = unsafe { rdtsc_begin() };
            let t1 = unsafe { rdtsc_end(acc) };
            *s = t1.saturating_sub(t0);
            acc ^= t1;
        }
        std::hint::black_box(acc);
        samples.sort_unstable();
        samples[samples.len() / 2]
    }

    const KV_FINE_BINS: usize = 2048;
    const KV_FINE_STEP_NS: f64 = 2.0;
    const KV_COARSE_BINS: usize = 1024;
    const KV_COARSE_STEP_NS: f64 = 64.0;

    fn kv_percentile(fine: &[u64; KV_FINE_BINS], coarse: &[u64; KV_COARSE_BINS], total: u64, max_ns: f64, q: f64) -> f64 {
        if total == 0 {
            return 0.0;
        }
        let target = ((total as f64) * q).ceil().max(1.0) as u64;
        let mut acc = 0u64;
        for (i, &c) in fine.iter().enumerate() {
            acc += c;
            if acc >= target {
                return (i as f64 + 0.5) * KV_FINE_STEP_NS;
            }
        }
        let fine_cap = KV_FINE_BINS as f64 * KV_FINE_STEP_NS;
        for (i, &c) in coarse.iter().enumerate() {
            acc += c;
            if acc >= target {
                return fine_cap + (i as f64 + 0.5) * KV_COARSE_STEP_NS;
            }
        }
        max_ns
    }

    #[derive(Clone, Default)]
    struct ColocKvSnap {
        enabled: bool,
        queries: u64,
        bad: u64,
        page_faults: u64,
        timer_floor_ns: f64,
        mean_ns: f64,
        p50_ns: f64,
        p99_ns: f64,
        p999_ns: f64,
        max_ns: f64,
    }

    // -------------------------------------------------------------------------
    // 100% Non-Multiplexed Sapphire Rapids Host PMU
    // -------------------------------------------------------------------------
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const REF_HZ: f64 = 2.2e9;

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

    const PERF_TYPE_HARDWARE: u32 = 0;
    const PERF_TYPE_SOFTWARE: u32 = 1;
    const PERF_TYPE_HW_CACHE: u32 = 3;
    const PERF_COUNT_HW_CPU_CYCLES: u64 = 0;
    const PERF_COUNT_HW_INSTRUCTIONS: u64 = 1;
    const PERF_COUNT_HW_REF_CPU_CYCLES: u64 = 9;
    const PERF_COUNT_SW_PAGE_FAULTS: u64 = 2;
    const CORE_LLC_LOADS: u64 = 0x00002;
    const CORE_LLC_LOAD_MISSES: u64 = 0x10002;
    const CORE_DTLB_LOAD_MISSES: u64 = 0x10003;

    const CHA_TOR_INSERTS_IA_WCILF: u64 = 0x00c8_67ff_0000_0135;
    const CHA_TOR_INSERTS_IO_HIT_ITOM: u64 = 0x00cc_43fd_0000_0435;
    const CHA_TOR_INSERTS_IO_MISS_ITOM: u64 = 0x00cc_43fe_0000_0435;
    const UNCORE_CLOCKTICKS: u64 = 0x01;
    const IIO_TXN_REQ_BY_CPU_MEM_WRITE: u64 = 0x0007_0ff0_0000_01c1;
    const IIO_DATA_REQ_OF_CPU_MEM_WRITE: u64 = 0x0007_0ff0_0000_0184;
    const IIO_DATA_REQ_OF_CPU_MEM_READ: u64 = 0x0007_0ff0_0000_0484;
    const IMC_CAS_COUNT_READ: u64 = 0xcf05;
    const IMC_CAS_COUNT_WRITE: u64 = 0xf005;

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
            Sample {
                val: self.val.saturating_sub(prev.val),
                en: self.en.saturating_sub(prev.en),
                run: self.run.saturating_sub(prev.run),
            }
        }
        fn pct(self) -> f64 {
            if self.en == 0 {
                100.0
            } else {
                (self.run as f64 / self.en as f64) * 100.0
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
            let c = PerfCounter { fd: fd as RawFd };
            if unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) } != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> Sample {
            let mut buf = [0u64; 3];
            let n = unsafe {
                libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>())
            };
            if n == size_of::<[u64; 3]>() as isize {
                Sample { val: buf[0], en: buf[1], run: buf[2] }
            } else {
                Sample::default()
            }
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

    fn read_pmu_type(name: &str) -> Option<u32> {
        let p = format!("/sys/bus/event_source/devices/{name}/type");
        fs::read_to_string(p).ok()?.trim().parse::<u32>().ok()
    }

    const MAX_CHA_PMU: usize = 64;
    const MAX_IMC_PMU: usize = 16;

    #[derive(Clone)]
    struct HostSnap {
        ts_ns: u64,
        cycles: Sample,
        ref_cycles: Sample,
        instr: Sample,
        page_faults: Sample,
        llc_loads: Sample,
        llc_load_misses: Sample,
        dtlb_load_misses: Sample,
        cha_len: usize,
        cha_wcilf: [Sample; MAX_CHA_PMU],
        cha_io_hit: [Sample; MAX_CHA_PMU],
        cha_io_miss: [Sample; MAX_CHA_PMU],
        mesh_clk: Sample,
        iio1_cpu_wr: Sample,
        iio1_inb_wr: Sample,
        iio1_inb_rd: Sample,
        imc_len: usize,
        imc_rd: [Sample; MAX_IMC_PMU],
        imc_wr: [Sample; MAX_IMC_PMU],
    }

    struct HostPmu {
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        instr: PerfCounter,
        page_faults: PerfCounter,
        llc_loads: PerfCounter,
        llc_load_misses: PerfCounter,
        dtlb_load_misses: PerfCounter,
        cha_wcilf: Vec<PerfCounter>,
        cha_io_hit: Vec<PerfCounter>,
        cha_io_miss: Vec<PerfCounter>,
        mesh_clk: PerfCounter,
        iio1_cpu_wr: PerfCounter,
        iio1_inb_wr: PerfCounter,
        iio1_inb_rd: PerfCounter,
        imc_rd: Vec<PerfCounter>,
        imc_wr: Vec<PerfCounter>,
    }

    impl HostPmu {
        fn open(cpu: i32) -> Result<Self, Box<dyn Error>> {
            let cycles = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu)?;
            let ref_cycles = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, cpu)?;
            let instr = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu)?;
            let page_faults = PerfCounter::open(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_PAGE_FAULTS, cpu)?;
            let llc_loads = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOADS, cpu)?;
            let llc_load_misses = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOAD_MISSES, cpu)?;
            let dtlb_load_misses = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_DTLB_LOAD_MISSES, cpu)?;

            let mut cha_wcilf = Vec::new();
            let mut cha_io_hit = Vec::new();
            let mut cha_io_miss = Vec::new();
            for i in 0..MAX_CHA_PMU {
                let Some(t) = read_pmu_type(&format!("uncore_cha_{i}")) else { break };
                cha_wcilf.push(PerfCounter::open(t, CHA_TOR_INSERTS_IA_WCILF, SOCKET1_UNCORE_CPU)?);
                cha_io_hit.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_HIT_ITOM, SOCKET1_UNCORE_CPU)?);
                cha_io_miss.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_MISS_ITOM, SOCKET1_UNCORE_CPU)?);
            }
            let cha0 = read_pmu_type("uncore_cha_0").ok_or("uncore_cha_0 missing")?;
            let mesh_clk = PerfCounter::open(cha0, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;

            let iio1 = read_pmu_type("uncore_iio_1").ok_or("uncore_iio_1 missing")?;
            let iio1_cpu_wr = PerfCounter::open(iio1, IIO_TXN_REQ_BY_CPU_MEM_WRITE, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_wr = PerfCounter::open(iio1, IIO_DATA_REQ_OF_CPU_MEM_WRITE, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_rd = PerfCounter::open(iio1, IIO_DATA_REQ_OF_CPU_MEM_READ, SOCKET1_UNCORE_CPU)?;

            let mut imc_rd = Vec::new();
            let mut imc_wr = Vec::new();
            for i in 0..MAX_IMC_PMU {
                let Some(t) = read_pmu_type(&format!("uncore_imc_{i}")) else { break };
                imc_rd.push(PerfCounter::open(t, IMC_CAS_COUNT_READ, SOCKET1_UNCORE_CPU)?);
                imc_wr.push(PerfCounter::open(t, IMC_CAS_COUNT_WRITE, SOCKET1_UNCORE_CPU)?);
            }

            Ok(HostPmu {
                cycles,
                ref_cycles,
                instr,
                page_faults,
                llc_loads,
                llc_load_misses,
                dtlb_load_misses,
                cha_wcilf,
                cha_io_hit,
                cha_io_miss,
                mesh_clk,
                iio1_cpu_wr,
                iio1_inb_wr,
                iio1_inb_rd,
                imc_rd,
                imc_wr,
            })
        }

        fn snap(&self, t0: Instant) -> HostSnap {
            let mut cha_wcilf = [Sample::default(); MAX_CHA_PMU];
            let mut cha_io_hit = [Sample::default(); MAX_CHA_PMU];
            let mut cha_io_miss = [Sample::default(); MAX_CHA_PMU];
            let cha_len = self.cha_wcilf.len().min(MAX_CHA_PMU);
            for i in 0..cha_len {
                cha_wcilf[i] = self.cha_wcilf[i].read();
                cha_io_hit[i] = self.cha_io_hit[i].read();
                cha_io_miss[i] = self.cha_io_miss[i].read();
            }
            let mut imc_rd = [Sample::default(); MAX_IMC_PMU];
            let mut imc_wr = [Sample::default(); MAX_IMC_PMU];
            let imc_len = self.imc_rd.len().min(MAX_IMC_PMU);
            for i in 0..imc_len {
                imc_rd[i] = self.imc_rd[i].read();
                imc_wr[i] = self.imc_wr[i].read();
            }
            HostSnap {
                ts_ns: t0.elapsed().as_nanos() as u64,
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                instr: self.instr.read(),
                page_faults: self.page_faults.read(),
                llc_loads: self.llc_loads.read(),
                llc_load_misses: self.llc_load_misses.read(),
                dtlb_load_misses: self.dtlb_load_misses.read(),
                cha_len,
                cha_wcilf,
                cha_io_hit,
                cha_io_miss,
                mesh_clk: self.mesh_clk.read(),
                iio1_cpu_wr: self.iio1_cpu_wr.read(),
                iio1_inb_wr: self.iio1_inb_wr.read(),
                iio1_inb_rd: self.iio1_inb_rd.read(),
                imc_len,
                imc_rd,
                imc_wr,
            }
        }
    }

    fn format_pmu_delta(a: &HostSnap, b: &HostSnap) -> String {
        let dt_s = ((b.ts_ns.saturating_sub(a.ts_ns)) as f64 * 1e-9).max(1e-9);
        let d_cyc = b.cycles.delta(a.cycles);
        let d_ref = b.ref_cycles.delta(a.ref_cycles);
        let d_ins = b.instr.delta(a.instr);
        let d_pf = b.page_faults.delta(a.page_faults);
        let d_ll = b.llc_loads.delta(a.llc_loads);
        let d_lm = b.llc_load_misses.delta(a.llc_load_misses);
        let d_tlb = b.dtlb_load_misses.delta(a.dtlb_load_misses);
        let d_mesh = b.mesh_clk.delta(a.mesh_clk);
        let d_cpu_wr = b.iio1_cpu_wr.delta(a.iio1_cpu_wr);
        let d_inb_wr = b.iio1_inb_wr.delta(a.iio1_inb_wr);
        let d_inb_rd = b.iio1_inb_rd.delta(a.iio1_inb_rd);

        let mut min_pct = 100.0f64;
        for s in [d_cyc, d_ref, d_ins, d_ll, d_lm, d_tlb, d_mesh, d_cpu_wr, d_inb_wr, d_inb_rd] {
            min_pct = min_pct.min(s.pct());
        }
        let sum_vec = |v_b: &[Sample], v_a: &[Sample], mp: &mut f64| -> u64 {
            let mut sum = 0u64;
            for (sb, sa) in v_b.iter().zip(v_a.iter()) {
                let d = sb.delta(*sa);
                *mp = mp.min(d.pct());
                sum += d.val;
            }
            sum
        };
        let wcilf = sum_vec(&b.cha_wcilf[..b.cha_len], &a.cha_wcilf[..a.cha_len], &mut min_pct);
        let io_hit = sum_vec(&b.cha_io_hit[..b.cha_len], &a.cha_io_hit[..a.cha_len], &mut min_pct);
        let io_miss = sum_vec(&b.cha_io_miss[..b.cha_len], &a.cha_io_miss[..a.cha_len], &mut min_pct);
        let imc_rd = sum_vec(&b.imc_rd[..b.imc_len], &a.imc_rd[..a.imc_len], &mut min_pct);
        let imc_wr = sum_vec(&b.imc_wr[..b.imc_len], &a.imc_wr[..a.imc_len], &mut min_pct);

        let cpu_ghz = if d_ref.val > 0 { (d_cyc.val as f64 / d_ref.val as f64) * (REF_HZ / 1e9) } else { 0.0 };
        let mesh_ghz = (d_mesh.val as f64 / dt_s) / 1e9;
        let ddio_hit_pct = if io_hit + io_miss > 0 {
            100.0 * (io_hit as f64) / ((io_hit + io_miss) as f64)
        } else {
            100.0
        };
        let llc_miss_pct = if d_ll.val > 0 { 100.0 * (d_lm.val as f64) / (d_ll.val as f64) } else { 0.0 };
        let imc_rd_mib = (imc_rd as f64) * 64.0 / (1024.0 * 1024.0);
        let imc_wr_mib = (imc_wr as f64) * 64.0 / (1024.0 * 1024.0);
        let imc_total_gbps = ((imc_rd + imc_wr) as f64) * 64.0 / (1e9 * dt_s);

        format!(
            "{{\"dt_s\":{dt_s:.6},\"cycles\":{},\"ref_cycles\":{},\"instr\":{},\"page_faults\":{},\"llc_loads\":{},\"llc_load_misses\":{},\"llc_miss_pct\":{llc_miss_pct:.4},\"dtlb_load_misses\":{},\"cha_wcilf\":{wcilf},\"cha_io_hit\":{io_hit},\"cha_io_miss\":{io_miss},\"ddio_hit_pct\":{ddio_hit_pct:.4},\"iio1_cpu_wr\":{},\"iio1_inb_wr\":{},\"iio1_inb_rd\":{},\"imc_rd\":{imc_rd},\"imc_wr\":{imc_wr},\"imc_rd_mib\":{imc_rd_mib:.3},\"imc_wr_mib\":{imc_wr_mib:.3},\"imc_total_gbps\":{imc_total_gbps:.4},\"cpu_ghz\":{cpu_ghz:.4},\"mesh_ghz\":{mesh_ghz:.4},\"min_running_pct\":{min_pct:.3}}}",
            d_cyc.val,
            d_ref.val,
            d_ins.val,
            d_pf.val,
            d_ll.val,
            d_lm.val,
            d_tlb.val,
            d_cpu_wr.val,
            d_inb_wr.val,
            d_inb_rd.val,
        )
    }

    // -------------------------------------------------------------------------
    // Per-Flow Consumer Model (identical across all 5 arms!)
    // -------------------------------------------------------------------------
    struct ConsumerModel {
        bad_flows: usize,
        stall_ticks: u64,
        stall_period_ticks: u64,
        bulk_pkt_interval_ticks: u64,
        epoch_start_tsc: u64,
        next_bulk_ready_tsc: [u64; MAX_CARD_CORES],
    }

    impl ConsumerModel {
        fn new(bad_flows: usize, stall_ms: u64, stall_period_ms: u64, bulk_rate_gbps: f64, tsc_hz: f64) -> Self {
            let stall_ticks = ((stall_ms as f64) * 1e-3 * tsc_hz).round() as u64;
            let stall_period_ticks = ((stall_period_ms as f64) * 1e-3 * tsc_hz).round() as u64;
            // Per-lane bulk packet interval for 1024 B RoCEv2 UD MTU payload (1114 B wire) at bulk_rate_gbps / bad_flows
            let bulk_pkt_interval_ticks = if bulk_rate_gbps > 0.0 && bad_flows > 0 {
                let per_flow_bps = (bulk_rate_gbps * 1e9) / (bad_flows as f64);
                let pkt_s = (1114.0 * 8.0) / per_flow_bps;
                (pkt_s * tsc_hz).round().max(1.0) as u64
            } else {
                0
            };
            ConsumerModel {
                bad_flows: bad_flows.min(MAX_CARD_CORES),
                stall_ticks,
                stall_period_ticks,
                bulk_pkt_interval_ticks,
                epoch_start_tsc: 0,
                next_bulk_ready_tsc: [0; MAX_CARD_CORES],
            }
        }

        fn reset(&mut self, now_tsc: u64) {
            self.epoch_start_tsc = now_tsc;
            self.next_bulk_ready_tsc.fill(now_tsc);
        }

        #[inline(always)]
        fn is_bad_flow(&self, fid: u16) -> bool {
            (fid as usize) < self.bad_flows
        }

        #[inline(always)]
        fn is_in_stall_window(&self, now_tsc: u64) -> bool {
            if self.stall_ticks == 0 || self.stall_period_ticks == 0 {
                return false;
            }
            let rel = now_tsc.saturating_sub(self.epoch_start_tsc);
            let pos = rel % self.stall_period_ticks;
            // Start first stall 2 ms after epoch start so warmup->measure transition is clean
            let start = self.stall_period_ticks.saturating_sub(self.stall_ticks);
            pos >= start
        }

        #[inline(always)]
        fn can_consume_now(&mut self, fid: u16, now_tsc: u64) -> bool {
            if !self.is_bad_flow(fid) {
                return true;
            }
            if self.is_in_stall_window(now_tsc) {
                return false;
            }
            if self.bulk_pkt_interval_ticks > 0 {
                let idx = fid as usize;
                return now_tsc >= self.next_bulk_ready_tsc[idx];
            }
            true
        }

        #[inline(always)]
        fn note_consumed(&mut self, fid: u16, now_tsc: u64) {
            if self.is_bad_flow(fid) && self.bulk_pkt_interval_ticks > 0 {
                let idx = fid as usize;
                let base = self.next_bulk_ready_tsc[idx].max(now_tsc.saturating_sub(self.bulk_pkt_interval_ticks * 8));
                self.next_bulk_ready_tsc[idx] = base.saturating_add(self.bulk_pkt_interval_ticks);
            }
        }
    }

    #[derive(Clone, Copy, Default)]
    struct TsBucket {
        rpc_pkts: u64,
        bulk_pkts: u64,
        stall_active: u64,
    }

    #[derive(Clone, Default)]
    struct HostStatsSnap {
        rx_pkts: u64,
        rx_bytes: u64,
        fast_rx_pkts: u64,
        slow_rx_pkts: u64,
        rpc_rx_pkts: u64,
        bulk_rx_pkts: u64,
        leak_flagged_pkts: u64,
        phase_paused_pkts: u64,
        acks_sent: u64,
        tx_drops: u64,
        ack_alloc_fail: u64,
        bad: u64,
        torn: u64,
        ooo: u64,
        dup: u64,
        gap: u64,
        q_credit_writes: u64,
        flow_credit_writes: u64,
        pull_writes: u64,
        sw_fast_merged: u64,
        sw_slow_merged: u64,
        sw_phase_switches: u64,
        host_stage_enq: u64,
        host_stage_max_depth: u64,
        bp_stop_polls_skipped: u64,
        ts_buckets: Vec<TsBucket>,
    }

    impl HostStatsSnap {
        fn with_prealloc_buckets() -> Self {
            let mut s = HostStatsSnap::default();
            s.ts_buckets = vec![TsBucket { rpc_pkts: 1, bulk_pkts: 1, stall_active: 1 }; TS_MAX_BUCKETS];
            std::hint::black_box(&s.ts_buckets);
            for b in &mut s.ts_buckets {
                *b = TsBucket::default();
            }
            s.ts_buckets.clear();
            s
        }

        fn reset_in_place(&mut self) {
            self.rx_pkts = 0;
            self.rx_bytes = 0;
            self.fast_rx_pkts = 0;
            self.slow_rx_pkts = 0;
            self.rpc_rx_pkts = 0;
            self.bulk_rx_pkts = 0;
            self.leak_flagged_pkts = 0;
            self.phase_paused_pkts = 0;
            self.acks_sent = 0;
            self.tx_drops = 0;
            self.ack_alloc_fail = 0;
            self.bad = 0;
            self.torn = 0;
            self.ooo = 0;
            self.dup = 0;
            self.gap = 0;
            self.q_credit_writes = 0;
            self.flow_credit_writes = 0;
            self.pull_writes = 0;
            self.sw_fast_merged = 0;
            self.sw_slow_merged = 0;
            self.sw_phase_switches = 0;
            self.host_stage_enq = 0;
            self.host_stage_max_depth = 0;
            self.bp_stop_polls_skipped = 0;
            self.ts_buckets.clear();
        }

        fn copy_from(&mut self, src: &HostStatsSnap) {
            self.rx_pkts = src.rx_pkts;
            self.rx_bytes = src.rx_bytes;
            self.fast_rx_pkts = src.fast_rx_pkts;
            self.slow_rx_pkts = src.slow_rx_pkts;
            self.rpc_rx_pkts = src.rpc_rx_pkts;
            self.bulk_rx_pkts = src.bulk_rx_pkts;
            self.leak_flagged_pkts = src.leak_flagged_pkts;
            self.phase_paused_pkts = src.phase_paused_pkts;
            self.acks_sent = src.acks_sent;
            self.tx_drops = src.tx_drops;
            self.ack_alloc_fail = src.ack_alloc_fail;
            self.bad = src.bad;
            self.torn = src.torn;
            self.ooo = src.ooo;
            self.dup = src.dup;
            self.gap = src.gap;
            self.q_credit_writes = src.q_credit_writes;
            self.flow_credit_writes = src.flow_credit_writes;
            self.pull_writes = src.pull_writes;
            self.sw_fast_merged = src.sw_fast_merged;
            self.sw_slow_merged = src.sw_slow_merged;
            self.sw_phase_switches = src.sw_phase_switches;
            self.host_stage_enq = src.host_stage_enq;
            self.host_stage_max_depth = src.host_stage_max_depth;
            self.bp_stop_polls_skipped = src.bp_stop_polls_skipped;
            self.ts_buckets.clear();
            self.ts_buckets.extend_from_slice(&src.ts_buckets);
        }
    }

    struct StagedPkt<'e> {
        lane: usize,
        from_slow: bool,
        mbuf: Mbuf<'e>,
    }

    struct PktCtx {
        touch_payload: bool,
        ts_start_tsc: u64,
        ts_bucket_ticks: u64,
    }

    #[inline(always)]
    fn peek_flow_id(pkt: &Mbuf<'_>) -> Option<u16> {
        let f = pkt.data();
        if f.len() < SDP_HDR_LEN + MIN_WIRE_PAYLOAD {
            return None;
        }
        let pay = &f[SDP_HDR_LEN..];
        let w2 = u64::from_le_bytes(pay[16..24].try_into().unwrap());
        Some((w2 & 0xffff) as u16)
    }

    /// Consume one packet completely: verify integrity, touch all 64 B lines, free RX mbuf
    /// immediately back to its LIFO hot pool, and emit a 64 B ACK from the dedicated TX ACK pool.
    /// Returns `Some(flow_id)` if valid `OP_DATA`.
    #[inline(always)]
    fn consume_pkt<'e>(
        pkt: Mbuf<'e>,
        lane: usize,
        ack_pool: &Mempool<'e>,
        tx_b: &mut Burst<'e, MAX_DRAIN>,
        port: &Port<'e>,
        order: &mut FlowOrderTracker,
        st: &mut HostStatsSnap,
        ctx: &PktCtx,
        in_stall_now: bool,
    ) -> Option<u16> {
        let flen = pkt.data_len();
        if flen < SDP_HDR_LEN + MIN_WIRE_PAYLOAD {
            st.bad += 1;
            return None;
        }
        let frame_hdr: [u8; SDP_HDR_LEN] = pkt.data()[..SDP_HDR_LEN].try_into().unwrap();
        let pay_len = flen - SDP_HDR_LEN;
        let parsed = verify_and_touch_wire_pkt(&pkt.data()[SDP_HDR_LEN..flen], ctx.touch_payload);
        // Drop RX mbuf IMMEDIATELY here so it returns to the hot per-queue `stack` LIFO pool!
        drop(pkt);

        let (meta, client_tsc, flow_id, flow_seq, aux_key, burst_epoch, _card_tsc, aux) = match parsed {
            Ok(v) => v,
            Err(true) => {
                st.torn += 1;
                return None;
            }
            Err(false) => {
                st.bad += 1;
                return None;
            }
        };
        if meta.op != OP_DATA {
            st.bad += 1;
            return None;
        }

        order.observe(flow_id, flow_seq);
        st.rx_pkts += 1;
        st.rx_bytes += pay_len as u64;
        if meta.path_tag == PATH_CEIO_SLOW {
            st.slow_rx_pkts += 1;
        } else {
            st.fast_rx_pkts += 1;
        }
        if meta.pkt_class == CLASS_RPC_HI {
            st.rpc_rx_pkts += 1;
        } else {
            st.bulk_rx_pkts += 1;
        }
        if (meta.flags & FLAG_CEIO_LLC_LEAK) != 0 {
            st.leak_flagged_pkts += 1;
        }
        if (meta.flags & FLAG_PHASE_PAUSED) != 0 {
            st.phase_paused_pkts += 1;
        }

        let now_tsc = rdtsc();
        let bidx = (now_tsc.saturating_sub(ctx.ts_start_tsc) / ctx.ts_bucket_ticks) as usize;
        if bidx < TS_MAX_BUCKETS {
            if st.ts_buckets.len() <= bidx {
                st.ts_buckets.resize(bidx + 1, TsBucket::default());
            }
            let b = &mut st.ts_buckets[bidx];
            if meta.pkt_class == CLASS_RPC_HI {
                b.rpc_pkts += 1;
            } else {
                b.bulk_pkts += 1;
            }
            if in_stall_now {
                b.stall_active = 1;
            }
        }

        // Allocate compact 64 B ACK from dedicated TX pool so RX hot pool stays tiny & DDIO-resident
        let Some(mut ack_m) = ack_pool.alloc() else {
            st.ack_alloc_fail += 1;
            return Some(flow_id);
        };
        let target_flen = SDP_HDR_LEN + MIN_WIRE_PAYLOAD;
        if let Some(dst) = ack_m.append(target_flen as u16) {
            dst[..SDP_HDR_LEN].copy_from_slice(&frame_hdr);
            swap_macs(dst);
            encode_wire_ack64(
                &mut dst[SDP_HDR_LEN..target_flen],
                meta.global_seq,
                client_tsc,
                flow_id,
                flow_seq,
                aux_key,
                burst_epoch,
                meta.pkt_class,
                meta.path_tag,
                meta.flags,
                aux,
            );
        }
        if let Err(m_back) = tx_b.push(ack_m) {
            let mut retries = 0u32;
            while !tx_b.is_empty() {
                if port.tx(lane as u16, tx_b) == 0 {
                    retries += 1;
                    if retries > 200_000 {
                        st.tx_drops += tx_b.len() as u64;
                        tx_b.clear();
                        break;
                    }
                }
            }
            let _ = tx_b.push(m_back);
        }
        st.acks_sent += 1;
        Some(flow_id)
    }

    fn spawn_coloc_kv_thread(
        cpu: usize,
        kv_bytes: usize,
        hz: u64,
        quit: Arc<AtomicBool>,
        reset_gen: Arc<AtomicU64>,
        snap_out: Arc<Mutex<ColocKvSnap>>,
        ready_flag: Arc<AtomicBool>,
    ) -> Result<(Arc<NumaHugeBuf>, std::thread::JoinHandle<()>), Box<dyn Error>> {
        let kv_buf = Arc::new(NumaHugeBuf::alloc_node1(kv_bytes)?);
        init_kv_table(&kv_buf, kv_bytes);
        let table_ptr = kv_buf.as_ptr() as usize;
        let handle = std::thread::spawn(move || {
            pin_thread(cpu);
            let entries = table_ptr as *const KvEntry;
            let n_entries = kv_bytes / size_of::<KvEntry>();
            let mask = (n_entries - 1) as u64;

            let mut fine = Box::new([1u64; KV_FINE_BINS]);
            let mut coarse = Box::new([1u64; KV_COARSE_BINS]);
            std::hint::black_box(&fine);
            std::hint::black_box(&coarse);
            fine.fill(0);
            coarse.fill(0);

            // Warm all 2 MiB TLB entries and lines
            let mut state: u64 = 0x1234_5678_9abc_def1;
            let mut acc: u64 = 0;
            for i in 0..n_entries {
                let e = unsafe { &*entries.add(i) };
                acc ^= e.key ^ e.next_salt;
            }
            state ^= acc;

            // Ramp core clock on cpu41 (in case schedutil starts low) before calibrating floor
            let ramp_t0 = Instant::now();
            while ramp_t0.elapsed() < Duration::from_millis(250) {
                for _ in 0..256 {
                    state = splitmix64(state);
                    let idx = (state & mask) as usize;
                    let e = unsafe { &*entries.add(idx) };
                    state ^= e.next_salt;
                }
            }
            let floor_ticks = calibrate_kv_timer_floor();
            let ns_per_tick = 1e9 / (hz as f64);
            let timer_floor_ns = floor_ticks as f64 * ns_per_tick;
            let fine_bins_per_tick = ns_per_tick / KV_FINE_STEP_NS;
            let fine_mul = (fine_bins_per_tick * ((1u64 << 32) as f64)) as u64;
            let coarse_ratio = (KV_COARSE_STEP_NS / KV_FINE_STEP_NS).round() as usize;

            ready_flag.store(true, Ordering::Release);

            let mut local_rg = reset_gen.load(Ordering::Acquire);
            let mut samples = 0u64;
            let mut bad = 0u64;
            let mut sum_net_ticks = 0u64;
            let mut max_net_ticks = 0u64;
            let mut pf0 = thread_page_faults();
            let mut iter = 0u64;

            while !quit.load(Ordering::Relaxed) {
                let rg = reset_gen.load(Ordering::Acquire);
                if rg != local_rg {
                    fine.fill(0);
                    coarse.fill(0);
                    samples = 0;
                    bad = 0;
                    sum_net_ticks = 0;
                    max_net_ticks = 0;
                    pf0 = thread_page_faults();
                    local_rg = rg;
                    *snap_out.lock().unwrap() = ColocKvSnap { enabled: true, timer_floor_ns, ..Default::default() };
                }

                for _ in 0..16 {
                    let mut idx_state = state;
                    let t0 = unsafe { rdtsc_begin() };
                    idx_state = splitmix64(idx_state);
                    let idx = (idx_state & mask) as usize;
                    let e = unsafe { &*entries.add(idx) };
                    let expected_key = (idx as u64) ^ 0x55aa_2280_9911_2233;
                    bad += u64::from((e.key ^ expected_key) != 0);
                    let dep = e.next_salt ^ e.val[0];
                    idx_state ^= e.next_salt;
                    let t1 = unsafe { rdtsc_end(dep) };
                    state = idx_state;
                    acc ^= dep;

                    let net_dt = t1.saturating_sub(t0).saturating_sub(floor_ticks);
                    samples += 1;
                    sum_net_ticks += net_dt;
                    if net_dt > max_net_ticks {
                        max_net_ticks = net_dt;
                    }
                    let bin = (((net_dt as u128) * (fine_mul as u128)) >> 32) as usize;
                    if bin < KV_FINE_BINS {
                        fine[bin] += 1;
                    } else {
                        let cb = (bin - KV_FINE_BINS) / coarse_ratio;
                        if cb < KV_COARSE_BINS {
                            coarse[cb] += 1;
                        }
                    }
                }
                iter = iter.wrapping_add(1);
                if (iter & 0x3ff) == 0 {
                    std::hint::black_box(acc);
                    if let Ok(mut g) = snap_out.try_lock() {
                        let max_ns = max_net_ticks as f64 * ns_per_tick;
                        g.enabled = true;
                        g.queries = samples;
                        g.bad = bad;
                        g.page_faults = thread_page_faults().saturating_sub(pf0);
                        g.timer_floor_ns = timer_floor_ns;
                        g.mean_ns = if samples > 0 { (sum_net_ticks as f64 / samples as f64) * ns_per_tick } else { 0.0 };
                        g.p50_ns = kv_percentile(&fine, &coarse, samples, max_ns, 0.50);
                        g.p99_ns = kv_percentile(&fine, &coarse, samples, max_ns, 0.99);
                        g.p999_ns = kv_percentile(&fine, &coarse, samples, max_ns, 0.999);
                        g.max_ns = max_ns;
                    }
                }
            }
            let max_ns = max_net_ticks as f64 * ns_per_tick;
            let mut g = snap_out.lock().unwrap();
            g.enabled = true;
            g.queries = samples;
            g.bad = bad;
            g.page_faults = thread_page_faults().saturating_sub(pf0);
            g.timer_floor_ns = timer_floor_ns;
            g.mean_ns = if samples > 0 { (sum_net_ticks as f64 / samples as f64) * ns_per_tick } else { 0.0 };
            g.p50_ns = kv_percentile(&fine, &coarse, samples, max_ns, 0.50);
            g.p99_ns = kv_percentile(&fine, &coarse, samples, max_ns, 0.99);
            g.p999_ns = kv_percentile(&fine, &coarse, samples, max_ns, 0.999);
            g.max_ns = max_ns;
        });
        Ok((kv_buf, handle))
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let mut raw_args: Vec<String> = std::env::args().collect();
        let dash = raw_args.iter().position(|a| a == "--");
        let (eal_args, app_args) = match dash {
            Some(i) => (
                raw_args[..i].to_vec(),
                std::iter::once(raw_args[0].clone()).chain(raw_args.drain(i + 1..)).collect(),
            ),
            None => (vec![raw_args[0].clone()], raw_args),
        };
        let args = Args::parse_from(app_args);

        let (eal, _) = Eal::init(eal_args)?;
        let tsc_hz_u64 = eal.tsc_hz();
        let tsc_hz = tsc_hz_u64 as f64;
        let worker_cpu = current_cpu();

        // Standalone unloaded 32 MiB KV baseline (`--mode kv-solo`)
        if args.mode == Mode::KvSolo {
            let host_pmu = if args.pmu { Some(HostPmu::open(worker_cpu)?) } else { None };
            let t_origin = Instant::now();
            let quit = Arc::new(AtomicBool::new(false));
            let rg = Arc::new(AtomicU64::new(0));
            let snap = Arc::new(Mutex::new(ColocKvSnap::default()));
            let ready = Arc::new(AtomicBool::new(false));
            let (_kv_buf, h) = spawn_coloc_kv_thread(
                args.coloc_kv_cpu,
                args.kv_bytes,
                tsc_hz_u64,
                Arc::clone(&quit),
                Arc::clone(&rg),
                Arc::clone(&snap),
                Arc::clone(&ready),
            )?;
            while !ready.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(5));
            }
            rg.fetch_add(1, Ordering::AcqRel);
            std::thread::sleep(Duration::from_millis(50));
            let p0 = host_pmu.as_ref().map(|p| p.snap(t_origin));
            std::thread::sleep(Duration::from_millis(args.solo_ms));
            let p1 = host_pmu.as_ref().map(|p| p.snap(t_origin));
            quit.store(true, Ordering::Relaxed);
            let _ = h.join();
            let c = snap.lock().unwrap().clone();
            let pmu_str = match (p0.as_ref(), p1.as_ref()) {
                (Some(a), Some(b)) => format_pmu_delta(a, b),
                _ => "null".to_string(),
            };
            println!(
                "MRV228_KV_SOLO {{\"mode\":\"kv-solo\",\"coloc_kv\":{{\"enabled\":true,\"queries\":{},\"bad\":{},\"page_faults\":{},\"timer_floor_ns\":{:.2},\"mean_ns\":{:.2},\"p50_ns\":{:.2},\"p99_ns\":{:.2},\"p999_ns\":{:.2},\"max_ns\":{:.2}}},\"pmu\":{}}}",
                c.queries, c.bad, c.page_faults, c.timer_floor_ns, c.mean_ns, c.p50_ns, c.p99_ns, c.p999_ns, c.max_ns, pmu_str
            );
            return Ok(());
        }

        if args.lanes < 2 || args.lanes > MAX_CARD_CORES {
            return Err(format!("lanes must be 2..={MAX_CARD_CORES}, got {}", args.lanes).into());
        }
        if args.mode == Mode::PerFlowCr
            && !verify_sdp_credit_safety(args.small_rx_desc, args.r_eff_per_q, args.credit_batch)
        {
            return Err(format!(
                "Pitfall 1 violation: small_rx_desc={} r_eff_per_q={} credit_batch={}",
                args.small_rx_desc, args.r_eff_per_q, args.credit_batch
            )
            .into());
        }
        let use_bar4 = matches!(args.mode, Mode::PerFlowCr | Mode::Ceio);
        if use_bar4 && !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B support".into());
        }

        let ts_bucket_ticks = ((args.ts_bucket_us as f64) * 1e-6 * tsc_hz).round().max(1.0) as u64;
        let port_id: u16 = 0;
        let sock = Port::socket_of(&eal, port_id);

        // Dedicated tiny LIFO `stack` pool for 64 B TX ACKs so RX hot pools stay tiny & DDIO-pinned
        let ack_pool_sz = (args.lanes as u32) * 512 + 256;
        let ack_pool = Mempool::new_with_opts(&eal, "m228h_ack_pool", ack_pool_sz, 0, ACK_MBUF_BUF_SIZE, sock, Some("stack"))?;

        let enable_promisc_best_effort = |p: &Port<'_>| -> Result<(), Box<dyn Error>> {
            if let Err(e) = p.promiscuous() {
                if e.errno() != libc::EOPNOTSUPP {
                    return Err(e.into());
                }
            }
            Ok(())
        };

        let (total_sdp_queues, rx_pools_owner, port): (usize, Vec<Mempool<'_>>, Port<'_>) = match args.mode {
            Mode::Bp1Stop | Mode::PerFlowCr => {
                // IDENTICAL ultra-compact per-queue LIFO `stack` hot pool (512 mbufs/q = 2,048 total = 4.2 MiB << 8 MiB DDIO!)
                let per_q = (args.small_rx_desc as u32) + 256;
                let mut pools = Vec::with_capacity(args.lanes);
                for q in 0..args.lanes {
                    pools.push(Mempool::new_with_opts(
                        &eal,
                        &format!("m228h_hot_q{q}"),
                        per_q,
                        0,
                        SDP_MBUF_BUF_SIZE,
                        sock,
                        Some("stack"),
                    )?);
                }
                let refs: Vec<&Mempool<'_>> = pools.iter().collect();
                let descs = vec![args.small_rx_desc; args.lanes];
                let p = Port::start_with_queue_cfg(&eal, port_id, &refs, &descs, args.sdp_tx_desc, Some(SDP_MTU), false, false)?;
                enable_promisc_best_effort(&p)?;
                (args.lanes, pools, p)
            }
            Mode::Bp1Drain => {
                // Fair strong BP=1 host-drain arm: D=256 small OQ + ideal LIFO `stack` pool sized for 64 MiB bursts
                let mp = Mempool::new_with_opts(&eal, "m228h_bp1_drain", 131071, 0, SDP_MBUF_BUF_SIZE, sock, Some("stack"))?;
                let p = Port::start_with_opts_rss_fc(
                    &eal,
                    port_id,
                    &mp,
                    args.lanes as u16,
                    args.small_rx_desc,
                    args.sdp_tx_desc,
                    Some(SDP_MTU),
                    false,
                    false,
                )?;
                enable_promisc_best_effort(&p)?;
                (args.lanes, vec![mp], p)
            }
            Mode::LargeSdp => {
                // Default passive Large SDP (D=2048, standard ring_sp_sc FIFO pool)
                let mp = Mempool::new_with_opts(&eal, "m228h_large_sdp", 131071, 0, SDP_MBUF_BUF_SIZE, sock, Some("ring_sp_sc"))?;
                let p = Port::start_with_opts_rss_fc(
                    &eal,
                    port_id,
                    &mp,
                    args.lanes as u16,
                    args.large_rx_desc,
                    args.sdp_tx_desc,
                    Some(SDP_MTU),
                    false,
                    false,
                )?;
                enable_promisc_best_effort(&p)?;
                (args.lanes, vec![mp], p)
            }
            Mode::Ceio => {
                // Fortified CEIO dual-path: Q0..3 Fast (D=2048) + Q4..7 Slow (D=512)
                let nq = args.lanes * 2;
                let fast_sz = (args.lanes as u32) * (args.ceio_fast_rx_desc as u32 + 512) + 4095;
                let slow_sz = 16383u32;
                let fp = Mempool::new_with_opts(&eal, "m228h_ceio_fp", fast_sz, 256, SDP_MBUF_BUF_SIZE, sock, None)?;
                let sp = Mempool::new_with_opts(&eal, "m228h_ceio_sp", slow_sz, 0, SDP_MBUF_BUF_SIZE, sock, Some("stack"))?;
                let pools = vec![fp, sp];
                let mut refs: Vec<&Mempool<'_>> = Vec::with_capacity(nq);
                let mut descs: Vec<u16> = Vec::with_capacity(nq);
                for _ in 0..args.lanes {
                    refs.push(&pools[0]);
                    descs.push(args.ceio_fast_rx_desc);
                }
                for _ in 0..args.lanes {
                    refs.push(&pools[1]);
                    descs.push(args.ceio_slow_rx_desc);
                }
                let p = Port::start_with_queue_cfg(&eal, port_id, &refs, &descs, args.sdp_tx_desc, Some(SDP_MTU), false, false)?;
                enable_promisc_best_effort(&p)?;
                (nq, pools, p)
            }
            Mode::KvSolo => unreachable!(),
        };
        let _keep_pools = (&rx_pools_owner, &ack_pool);

        // Lock all current & future pages before opening PMU to guarantee 0 page faults on cpu40
        unsafe {
            let _ = libc::mlockall(libc::MCL_CURRENT | libc::MCL_FUTURE);
        }

        let lease_opt = if use_bar4 { Some(Lease::acquire(&args.card)?) } else { None };
        let slot_opt = if let Some(l) = lease_opt.as_ref() { Some(l.map_slot(&args.bar4)?) } else { None };

        let host_pmu = if args.pmu { Some(HostPmu::open(worker_cpu)?) } else { None };
        let t_origin = Instant::now();
        let pmu_start: Arc<Mutex<Option<HostSnap>>> = Arc::new(Mutex::new(host_pmu.as_ref().map(|p| p.snap(t_origin))));
        let pmu_latest: Arc<Mutex<Option<HostSnap>>> = Arc::new(Mutex::new(host_pmu.as_ref().map(|p| p.snap(t_origin))));

        let quit = Arc::new(AtomicBool::new(false));
        let reset_gen = Arc::new(AtomicU64::new(0));
        let active_epoch = Arc::new(AtomicU64::new(0));
        let reset_ack = Arc::new(AtomicU64::new(0));
        let stats_snap = Arc::new(Mutex::new(HostStatsSnap::with_prealloc_buckets()));
        let coloc_reset_gen = Arc::new(AtomicU64::new(0));
        let coloc_snap = Arc::new(Mutex::new(ColocKvSnap { enabled: args.coloc_kv_cpu > 0, ..Default::default() }));
        let coloc_ready = Arc::new(AtomicBool::new(false));
        let port_stats_snap = Arc::new(Mutex::new(EthStats::default()));

        {
            let q = Arc::clone(&quit);
            ctrlc::set_handler(move || q.store(true, Ordering::Relaxed))?;
        }

        // Co-located 32 MiB 2 MiB-hugepage KV tenant on cpu41 (exact ~41 ns baseline!)
        let (_kv_buf_keep, coloc_handle) = if args.coloc_kv_cpu > 0 {
            let (b, h) = spawn_coloc_kv_thread(
                args.coloc_kv_cpu,
                args.kv_bytes,
                tsc_hz_u64,
                Arc::clone(&quit),
                Arc::clone(&coloc_reset_gen),
                Arc::clone(&coloc_snap),
                Arc::clone(&coloc_ready),
            )?;
            while !coloc_ready.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(5));
            }
            (Some(b), Some(h))
        } else {
            (None, None)
        };

        // Control thread on cpu44 (`PING`, `RESET <epoch>`, `STATS`, `QUIT`)
        let ctrl_listener = TcpListener::bind(&args.ctrl)?;
        ctrl_listener.set_nonblocking(true)?;
        let ctrl_quit = Arc::clone(&quit);
        let ctrl_rg = Arc::clone(&reset_gen);
        let ctrl_epoch = Arc::clone(&active_epoch);
        let ctrl_ra = Arc::clone(&reset_ack);
        let ctrl_crg = Arc::clone(&coloc_reset_gen);
        let ctrl_stats = Arc::clone(&stats_snap);
        let ctrl_coloc = Arc::clone(&coloc_snap);
        let ctrl_pstats = Arc::clone(&port_stats_snap);
        let ctrl_pmu_a = Arc::clone(&pmu_start);
        let ctrl_pmu_b = Arc::clone(&pmu_latest);
        let mode_str = args.mode.as_str();
        let lanes = args.lanes;
        let ts_bucket_us = args.ts_bucket_us;
        let bp_stop_stage_cap = args.bp_stop_stage_cap;

        let ctrl_handle = std::thread::spawn(move || {
            pin_thread(CTRL_CPU);
            while !ctrl_quit.load(Ordering::Relaxed) {
                match ctrl_listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_nodelay(true);
                        let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
                        let mut reader = BufReader::new(stream.try_clone().unwrap());
                        let mut line = String::new();
                        if reader.read_line(&mut line).is_ok() {
                            let cmd = line.trim();
                            let resp = if cmd == "PING" {
                                format!("{{\"ok\":true,\"mode\":\"{mode_str}\",\"lanes\":{lanes}}}\n")
                            } else if cmd == "RESET" || cmd.starts_with("RESET ") {
                                let ep = cmd
                                    .strip_prefix("RESET ")
                                    .and_then(|s| s.trim().parse::<u64>().ok())
                                    .unwrap_or_else(|| ctrl_epoch.load(Ordering::Acquire).wrapping_add(1));
                                ctrl_epoch.store(ep, Ordering::Release);
                                ctrl_crg.fetch_add(1, Ordering::AcqRel);
                                let next = ctrl_rg.fetch_add(1, Ordering::AcqRel) + 1;
                                let dl = Instant::now() + Duration::from_secs(3);
                                while ctrl_ra.load(Ordering::Acquire) < next && Instant::now() < dl {
                                    std::thread::sleep(Duration::from_millis(1));
                                }
                                format!("{{\"ok\":true,\"epoch\":{ep}}}\n")
                            } else if cmd == "STATS" {
                                let s = ctrl_stats.lock().unwrap().clone();
                                let c = ctrl_coloc.lock().unwrap().clone();
                                let ps = *ctrl_pstats.lock().unwrap();
                                let pmu_str = match (
                                    ctrl_pmu_a.lock().unwrap().as_ref(),
                                    ctrl_pmu_b.lock().unwrap().as_ref(),
                                ) {
                                    (Some(a), Some(b)) => format_pmu_delta(a, b),
                                    _ => "null".to_string(),
                                };
                                let ts_parts: Vec<String> = s
                                    .ts_buckets
                                    .iter()
                                    .map(|b| format!("[{},{},{}]", b.rpc_pkts, b.bulk_pkts, b.stall_active))
                                    .collect();
                                format!(
                                    "{{\"mode\":\"{mode_str}\",\"lanes\":{lanes},\"bp_stop_stage_cap\":{bp_stop_stage_cap},\"rx_pkts\":{},\"rx_bytes\":{},\"fast_rx_pkts\":{},\"slow_rx_pkts\":{},\"rpc_rx_pkts\":{},\"bulk_rx_pkts\":{},\"leak_flagged_pkts\":{},\"phase_paused_pkts\":{},\"acks_sent\":{},\"tx_drops\":{},\"ack_alloc_fail\":{},\"bad\":{},\"torn\":{},\"ooo\":{},\"dup\":{},\"gap\":{},\"q_credit_writes\":{},\"flow_credit_writes\":{},\"pull_writes\":{},\"sw_fast_merged\":{},\"sw_slow_merged\":{},\"sw_phase_switches\":{},\"host_stage_enq\":{},\"host_stage_max_depth\":{},\"bp_stop_polls_skipped\":{},\"coloc_kv\":{{\"enabled\":{},\"queries\":{},\"bad\":{},\"page_faults\":{},\"timer_floor_ns\":{:.2},\"mean_ns\":{:.2},\"p50_ns\":{:.2},\"p99_ns\":{:.2},\"p999_ns\":{:.2},\"max_ns\":{:.2}}},\"sdp_port_stats\":{{\"ipackets\":{},\"opackets\":{},\"ibytes\":{},\"obytes\":{},\"imissed\":{},\"ierrors\":{},\"oerrors\":{},\"rx_nombuf\":{}}},\"pmu\":{},\"ts_bucket_us\":{ts_bucket_us},\"ts_buckets\":[{}]}}\n",
                                    s.rx_pkts,
                                    s.rx_bytes,
                                    s.fast_rx_pkts,
                                    s.slow_rx_pkts,
                                    s.rpc_rx_pkts,
                                    s.bulk_rx_pkts,
                                    s.leak_flagged_pkts,
                                    s.phase_paused_pkts,
                                    s.acks_sent,
                                    s.tx_drops,
                                    s.ack_alloc_fail,
                                    s.bad,
                                    s.torn,
                                    s.ooo,
                                    s.dup,
                                    s.gap,
                                    s.q_credit_writes,
                                    s.flow_credit_writes,
                                    s.pull_writes,
                                    s.sw_fast_merged,
                                    s.sw_slow_merged,
                                    s.sw_phase_switches,
                                    s.host_stage_enq,
                                    s.host_stage_max_depth,
                                    s.bp_stop_polls_skipped,
                                    c.enabled,
                                    c.queries,
                                    c.bad,
                                    c.page_faults,
                                    c.timer_floor_ns,
                                    c.mean_ns,
                                    c.p50_ns,
                                    c.p99_ns,
                                    c.p999_ns,
                                    c.max_ns,
                                    ps.ipackets,
                                    ps.opackets,
                                    ps.ibytes,
                                    ps.obytes,
                                    ps.imissed,
                                    ps.ierrors,
                                    ps.oerrors,
                                    ps.rx_nombuf,
                                    pmu_str,
                                    ts_parts.join(",")
                                )
                            } else if cmd == "QUIT" {
                                ctrl_quit.store(true, Ordering::Relaxed);
                                "{\"ok\":true}\n".to_string()
                            } else {
                                format!("{{\"ok\":false,\"err\":\"unknown cmd {cmd}\"}}\n")
                            };
                            let _ = stream.write_all(resp.as_bytes());
                        }
                    }
                    Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(2));
                    }
                    Err(_) => {
                        std::thread::sleep(Duration::from_millis(5));
                    }
                }
            }
        });

        // Pre-allocate all host hot-path state (zero allocations during run -> 0 page faults!)
        let mut consumer = ConsumerModel::new(args.bad_flows, args.stall_ms, args.stall_period_ms, args.bulk_rate_gbps, tsc_hz);
        let mut flow_lines: [FlowCreditLine; FLOW_CREDIT_GROUPS] = [FlowCreditLine::default(); FLOW_CREDIT_GROUPS];
        let mut dirty_groups: u64 = 0; // 1 bit per 64 B group (64 bits cover all 1,024 flows!)
        let mut taken_q = [0u64; 4];
        let mut reported_taken_total = 0u64;
        let mut credit_gen = 0u64;
        let mut slow_head_q = [0u64; 4];
        let mut slow_staged_q = [0usize; 4];
        let mut pull_target_q = [args.ceio_pull_window; 4];
        let mut pull_gen = 0u64;

        let mut sw_ring: SwMergeRing2P1C<Mbuf<'_>> = SwMergeRing2P1C::new(512);
        let mut order = FlowOrderTracker::new();
        for fid in 0..MAX_FLOWS {
            order.observe(fid as u16, 1);
        }
        order.reset();

        // Pre-allocated per-bad-flow host staging queues (0..4):
        //   - bp1-stop:    holds <= 32 packets (tiny peek hold; pauses port.rx(q) while non-empty!)
        //   - per-flow-cr: holds <= r_flow (32) packets per stalled flow (card gates all excess!)
        //   - bp1-drain / large-sdp: holds entire 16-64 MiB burst in host memory!
        let stage_cap = match args.mode {
            Mode::Bp1Drain | Mode::LargeSdp => 40_000,
            Mode::Ceio => 2_048,
            _ => 256,
        };
        let mut host_flow_q: Vec<VecDeque<StagedPkt<'_>>> = (0..MAX_CARD_CORES)
            .map(|_| VecDeque::with_capacity(stage_cap))
            .collect();
        let mut host_staged_total: usize = 0;
        let mut lane_staged = [0usize; MAX_CARD_CORES];

        let mut rx_burst: Burst<'_, MAX_DRAIN> = Burst::new();
        let mut tx_bursts: Vec<Burst<'_, MAX_DRAIN>> = (0..args.lanes).map(|_| Burst::new()).collect();
        let mut st = HostStatsSnap::with_prealloc_buckets();
        let mut ts_start_tsc = rdtsc_after_loads();
        consumer.reset(ts_start_tsc);
        let mut last_credit_post_tsc = ts_start_tsc;
        let ceio_credit_flush_ticks = (20.0e-6 * tsc_hz).round().max(1.0) as u64;
        let mut local_rg = 0u64;
        let mut local_epoch = 0u64;
        let mut iter = 0u64;
        let mut unflushed_bad_consumed = 0u64;

        let flush_dirty_flow_lines = |slot_o: &Option<LeasedSlot<'_>>,
                                      lines: &[FlowCreditLine; FLOW_CREDIT_GROUPS],
                                      mask: &mut u64,
                                      wr_cnt: &mut u64|
         -> Result<(), String> {
            if let Some(slot) = slot_o.as_ref() {
                if *mask != 0 {
                    slot.check()?;
                    let mut m = *mask;
                    while m != 0 {
                        let g = m.trailing_zeros() as usize;
                        m &= m - 1;
                        unsafe {
                            movdir64b(slot.as_ptr().add(flow_credit_line_off(g)), &lines[g]);
                        }
                        *wr_cnt += 1;
                    }
                    *mask = 0;
                }
            }
            Ok(())
        };

        let flush_all_flow_lines_zero = |slot_o: &Option<LeasedSlot<'_>>,
                                         lines: &mut [FlowCreditLine; FLOW_CREDIT_GROUPS]|
         -> Result<(), String> {
            for line in lines.iter_mut() {
                line.taken = [0u32; 16];
            }
            if let Some(slot) = slot_o.as_ref() {
                slot.check()?;
                for g in 0..FLOW_CREDIT_GROUPS {
                    unsafe {
                        movdir64b(slot.as_ptr().add(flow_credit_line_off(g)), &lines[g]);
                    }
                }
            }
            Ok(())
        };

        let post_q_credit = |slot_o: &Option<LeasedSlot<'_>>,
                             gen_ctr: &mut u64,
                             taken: [u64; 4],
                             epoch: u64,
                             wr_cnt: &mut u64|
         -> Result<(), String> {
            if let Some(slot) = slot_o.as_ref() {
                slot.check()?;
                *gen_ctr = gen_ctr.wrapping_add(1);
                let line = HostQueueCreditLine::new(*gen_ctr, taken, epoch, 1);
                unsafe { movdir64b(slot.as_ptr().add(BAR4_Q_CREDIT_OFF), &line) };
                *wr_cnt += 1;
            }
            Ok(())
        };

        let post_ceio_pull = |slot_o: &Option<LeasedSlot<'_>>,
                              gen_ctr: &mut u64,
                              epoch: u64,
                              shead: [u64; 4],
                              ptgt: [u32; 4],
                              wr_cnt: &mut u64|
         -> Result<(), String> {
            if let Some(slot) = slot_o.as_ref() {
                slot.check()?;
                *gen_ctr = gen_ctr.wrapping_add(1);
                let line = CeioSlowPullLine::new(*gen_ctr, epoch, shead, ptgt);
                unsafe { movdir64b(slot.as_ptr().add(BAR4_CEIO_PULL_OFF), &line) };
                *wr_cnt += 1;
            }
            Ok(())
        };

        if use_bar4 {
            flush_all_flow_lines_zero(&slot_opt, &mut flow_lines)?;
            post_q_credit(&slot_opt, &mut credit_gen, taken_q, local_epoch, &mut st.q_credit_writes)?;
        }
        if args.mode == Mode::Ceio {
            post_ceio_pull(&slot_opt, &mut pull_gen, local_epoch, slow_head_q, pull_target_q, &mut st.pull_writes)?;
        }

        let _ = port.stats_reset();
        println!(
            "MRV228_HOST READY mode={} cpu={} lanes={} total_sdp_q={} coloc_kv_cpu={} stall_ms={}/{} bulk_rate_gbps={:.2}",
            args.mode.as_str(),
            worker_cpu,
            args.lanes,
            total_sdp_queues,
            args.coloc_kv_cpu,
            args.stall_ms,
            args.stall_period_ms,
            args.bulk_rate_gbps
        );
        let _ = io::stdout().flush();

        while !quit.load(Ordering::Relaxed) {
            if let Some(l) = lease_opt.as_ref() {
                if l.revoked() {
                    eprintln!("mrv228_host: BAR4 lease revoked, stopping");
                    break;
                }
            }

            let rg = reset_gen.load(Ordering::Acquire);
            if rg != local_rg {
                for q in 0..total_sdp_queues {
                    while port.rx(q as u16, &mut rx_burst) > 0 {
                        rx_burst.clear();
                    }
                }
                for hq in &mut host_flow_q {
                    hq.clear();
                }
                host_staged_total = 0;
                slow_staged_q = [0; 4];
                lane_staged = [0usize; MAX_CARD_CORES];
                sw_ring.reset();
                order.reset();
                for tb in &mut tx_bursts {
                    tb.clear();
                }
                local_epoch = active_epoch.load(Ordering::Acquire);
                taken_q = [0u64; 4];
                reported_taken_total = 0;
                slow_head_q = [0u64; 4];
                pull_target_q = [args.ceio_pull_window; 4];
                dirty_groups = 0;
                unflushed_bad_consumed = 0;
                st.reset_in_place();
                let _ = port.stats_reset();
                if use_bar4 {
                    // Zero all 64 FlowCreditLines FIRST, then write new epoch in QueueCreditLine!
                    if let Err(e) = flush_all_flow_lines_zero(&slot_opt, &mut flow_lines) {
                        eprintln!("mrv228_host: {e}");
                        break;
                    }
                    if let Err(e) = post_q_credit(&slot_opt, &mut credit_gen, taken_q, local_epoch, &mut st.q_credit_writes) {
                        eprintln!("mrv228_host: {e}");
                        break;
                    }
                }
                if args.mode == Mode::Ceio {
                    if let Err(e) = post_ceio_pull(
                        &slot_opt,
                        &mut pull_gen,
                        local_epoch,
                        slow_head_q,
                        pull_target_q,
                        &mut st.pull_writes,
                    ) {
                        eprintln!("mrv228_host: {e}");
                        break;
                    }
                }
                if let Some(p) = host_pmu.as_ref() {
                    let sn = p.snap(t_origin);
                    *pmu_start.lock().unwrap() = Some(sn.clone());
                    *pmu_latest.lock().unwrap() = Some(sn);
                }
                ts_start_tsc = rdtsc_after_loads();
                consumer.reset(ts_start_tsc);
                last_credit_post_tsc = ts_start_tsc;
                local_rg = rg;
                reset_ack.store(rg, Ordering::Release);
                continue;
            }

            let now_tsc = rdtsc();
            let in_stall_now = consumer.is_in_stall_window(now_tsc);
            let pkt_ctx = PktCtx {
                touch_payload: args.touch_payload,
                ts_start_tsc,
                ts_bucket_ticks,
            };

            let mut handled_this_iter = 0u64;
            let mut slow_consumed_this_iter = 0u64;

            // Step A: Drain any previously staged troublemaker packets whose consumer is now ready
            for bf in 0..consumer.bad_flows {
                let fid = bf as u16;
                let mut drained_here = 0usize;
                while drained_here < MAX_DRAIN && !host_flow_q[bf].is_empty() && consumer.can_consume_now(fid, now_tsc) {
                    let sp = host_flow_q[bf].pop_front().unwrap();
                    host_staged_total -= 1;
                    lane_staged[sp.lane] = lane_staged[sp.lane].saturating_sub(1);
                    if sp.from_slow {
                        slow_staged_q[sp.lane] = slow_staged_q[sp.lane].saturating_sub(1);
                    }
                    if let Some(cfid) = consume_pkt(
                        sp.mbuf,
                        sp.lane,
                        &ack_pool,
                        &mut tx_bursts[sp.lane],
                        &port,
                        &mut order,
                        &mut st,
                        &pkt_ctx,
                        in_stall_now,
                    ) {
                        consumer.note_consumed(cfid, now_tsc);
                        let g = ((cfid as usize) >> 4) & (FLOW_CREDIT_GROUPS - 1);
                        let slot_i = (cfid as usize) & 15;
                        flow_lines[g].taken[slot_i] = flow_lines[g].taken[slot_i].wrapping_add(1);
                        dirty_groups |= 1u64 << g;
                        unflushed_bad_consumed += 1;
                        if sp.from_slow {
                            slow_head_q[sp.lane] += 1;
                            slow_consumed_this_iter += 1;
                        }
                    }
                    handled_this_iter += 1;
                    drained_here += 1;
                }
            }

            // Step B: Poll hardware SDP queues according to active arm
            match args.mode {
                Mode::Bp1Stop => {
                    // Arm 1: Given the EXACT SAME per-lane host staging budget (`bp_stop_stage_cap = r_flow = 32`)
                    // as `per-flow-cr` for strict single-variable fairness! Once `lane_staged[q] >= cap`,
                    // stops calling `port.rx(q)` so CN106 HW `BP=1` fires instead of spilling into host RAM.
                    for q in 0..args.lanes {
                        if lane_staged[q] >= args.bp_stop_stage_cap {
                            st.bp_stop_polls_skipped += 1;
                            continue;
                        }
                        if port.rx(q as u16, &mut rx_burst) > 0 {
                            rx_burst.drain_into(|pkt| {
                                let fid_opt = peek_flow_id(&pkt);
                                if let Some(fid) = fid_opt {
                                    if consumer.is_bad_flow(fid) {
                                        let bf = fid as usize;
                                        debug_assert!(bf < args.lanes, "bad flow {bf} must be < lanes");
                                        if !host_flow_q[bf].is_empty() || !consumer.can_consume_now(fid, now_tsc) {
                                            host_flow_q[bf].push_back(StagedPkt { lane: q, from_slow: false, mbuf: pkt });
                                            host_staged_total += 1;
                                            lane_staged[q] += 1;
                                            st.host_stage_enq += 1;
                                            if (host_staged_total as u64) > st.host_stage_max_depth {
                                                st.host_stage_max_depth = host_staged_total as u64;
                                            }
                                            return;
                                        }
                                    }
                                }
                                if let Some(cfid) = consume_pkt(
                                    pkt,
                                    q,
                                    &ack_pool,
                                    &mut tx_bursts[q],
                                    &port,
                                    &mut order,
                                    &mut st,
                                    &pkt_ctx,
                                    in_stall_now,
                                ) {
                                    consumer.note_consumed(cfid, now_tsc);
                                }
                                handled_this_iter += 1;
                            });
                        }
                    }
                }

                Mode::Bp1Drain | Mode::LargeSdp => {
                    // Arms 2 & 3: Continuously drain HW SDP ring into host per-flow software queues
                    // so healthy RPCs never suffer HoL — at the cost of flooding host DDIO/LLC/IMC!
                    for q in 0..args.lanes {
                        if port.rx(q as u16, &mut rx_burst) > 0 {
                            rx_burst.drain_into(|pkt| {
                                let fid_opt = peek_flow_id(&pkt);
                                if let Some(fid) = fid_opt {
                                    if consumer.is_bad_flow(fid) {
                                        let bf = fid as usize;
                                        if !host_flow_q[bf].is_empty() || !consumer.can_consume_now(fid, now_tsc) {
                                            host_flow_q[bf].push_back(StagedPkt { lane: q, from_slow: false, mbuf: pkt });
                                            host_staged_total += 1;
                                            st.host_stage_enq += 1;
                                            if (host_staged_total as u64) > st.host_stage_max_depth {
                                                st.host_stage_max_depth = host_staged_total as u64;
                                            }
                                            return;
                                        }
                                    }
                                }
                                if let Some(cfid) = consume_pkt(
                                    pkt,
                                    q,
                                    &ack_pool,
                                    &mut tx_bursts[q],
                                    &port,
                                    &mut order,
                                    &mut st,
                                    &pkt_ctx,
                                    in_stall_now,
                                ) {
                                    consumer.note_consumed(cfid, now_tsc);
                                }
                                handled_this_iter += 1;
                            });
                        }
                    }
                }

                Mode::PerFlowCr => {
                    // Arm 5 (OUR DESIGN): Drain HW SDP ring continuously (`taken_q` advances immediately)
                    // while holding `taken_flow[fid]` back on any stalled/slow flow (at most r_flow=32 pkts
                    // ever enter host RAM!). Flush dirty 64 B 16-flow lines via fence-free MOVDIR64B.
                    for q in 0..args.lanes {
                        if port.rx(q as u16, &mut rx_burst) > 0 {
                            let n = rx_burst.len() as u64;
                            rx_burst.drain_into(|pkt| {
                                let fid_opt = peek_flow_id(&pkt);
                                if let Some(fid) = fid_opt {
                                    if consumer.is_bad_flow(fid) {
                                        let bf = fid as usize;
                                        if !host_flow_q[bf].is_empty() || !consumer.can_consume_now(fid, now_tsc) {
                                            host_flow_q[bf].push_back(StagedPkt { lane: q, from_slow: false, mbuf: pkt });
                                            host_staged_total += 1;
                                            st.host_stage_enq += 1;
                                            if (host_staged_total as u64) > st.host_stage_max_depth {
                                                st.host_stage_max_depth = host_staged_total as u64;
                                            }
                                            return;
                                        }
                                    }
                                }
                                if let Some(cfid) = consume_pkt(
                                    pkt,
                                    q,
                                    &ack_pool,
                                    &mut tx_bursts[q],
                                    &port,
                                    &mut order,
                                    &mut st,
                                    &pkt_ctx,
                                    in_stall_now,
                                ) {
                                    consumer.note_consumed(cfid, now_tsc);
                                    let g = ((cfid as usize) >> 4) & (FLOW_CREDIT_GROUPS - 1);
                                    let slot_i = (cfid as usize) & 15;
                                    flow_lines[g].taken[slot_i] = flow_lines[g].taken[slot_i].wrapping_add(1);
                                    dirty_groups |= 1u64 << g;
                                }
                            });
                            taken_q[q] += n;
                            handled_this_iter += n;
                        }
                    }

                    let taken_total: u64 = taken_q.iter().sum();
                    let q_delta = taken_total.saturating_sub(reported_taken_total);
                    if q_delta >= args.credit_batch
                        || unflushed_bad_consumed >= 8
                        || (handled_this_iter == 0 && (q_delta > 0 || dirty_groups != 0))
                    {
                        if let Err(e) = flush_dirty_flow_lines(
                            &slot_opt,
                            &flow_lines,
                            &mut dirty_groups,
                            &mut st.flow_credit_writes,
                        ) {
                            eprintln!("mrv228_host: {e}");
                            break;
                        }
                        unflushed_bad_consumed = 0;
                        if q_delta > 0 {
                            if let Err(e) =
                                post_q_credit(&slot_opt, &mut credit_gen, taken_q, local_epoch, &mut st.q_credit_writes)
                            {
                                eprintln!("mrv228_host: {e}");
                                break;
                            }
                            reported_taken_total = taken_total;
                        }
                    }
                }

                Mode::Ceio => {
                    // Arm 4: Faithful fortified CEIO §4.2 2P1C SW merge + async_recv() pull
                    for q in 0..args.lanes {
                        if port.rx(q as u16, &mut rx_burst) > 0 {
                            rx_burst.drain_into(|pkt| {
                                let _ = sw_ring.push_from(SwProducer::FastPath, q as u8, pkt);
                            });
                        }
                    }
                    for q in 0..args.lanes {
                        if port.rx((q + args.lanes) as u16, &mut rx_burst) > 0 {
                            rx_burst.drain_into(|pkt| {
                                let _ = sw_ring.push_from(SwProducer::SlowPath, q as u8, pkt);
                            });
                        }
                    }
                    while let Some(ev) = sw_ring.pop() {
                        let el = ev.lane as usize;
                        let is_slow = ev.producer == SwProducer::SlowPath;
                        if !is_slow {
                            taken_q[el] += 1;
                            handled_this_iter += 1;
                        }
                        let fid_opt = peek_flow_id(&ev.item);
                        if let Some(fid) = fid_opt {
                            if consumer.is_bad_flow(fid) {
                                let bf = fid as usize;
                                if !host_flow_q[bf].is_empty() || !consumer.can_consume_now(fid, now_tsc) {
                                    host_flow_q[bf].push_back(StagedPkt { lane: el, from_slow: is_slow, mbuf: ev.item });
                                    host_staged_total += 1;
                                    if is_slow {
                                        slow_staged_q[el] += 1;
                                    }
                                    st.host_stage_enq += 1;
                                    if (host_staged_total as u64) > st.host_stage_max_depth {
                                        st.host_stage_max_depth = host_staged_total as u64;
                                    }
                                    continue;
                                }
                            }
                        }
                        if let Some(cfid) = consume_pkt(
                            ev.item,
                            el,
                            &ack_pool,
                            &mut tx_bursts[el],
                            &port,
                            &mut order,
                            &mut st,
                            &pkt_ctx,
                            in_stall_now,
                        ) {
                            consumer.note_consumed(cfid, now_tsc);
                            let g = ((cfid as usize) >> 4) & (FLOW_CREDIT_GROUPS - 1);
                            let slot_i = (cfid as usize) & 15;
                            flow_lines[g].taken[slot_i] = flow_lines[g].taken[slot_i].wrapping_add(1);
                            dirty_groups |= 1u64 << g;
                            if is_slow {
                                slow_head_q[el] += 1;
                                slow_consumed_this_iter += 1;
                            }
                        }
                    }

                    let taken_total: u64 = taken_q.iter().sum();
                    let delta = taken_total.saturating_sub(reported_taken_total);
                    let now_c = rdtsc();
                    if delta >= args.ceio_lazy_batch
                        || unflushed_bad_consumed >= 8
                        || ((delta > 0 || dirty_groups != 0)
                            && (handled_this_iter == 0
                                || slow_consumed_this_iter > 0
                                || now_c.saturating_sub(last_credit_post_tsc) >= ceio_credit_flush_ticks))
                    {
                        if let Err(e) = flush_dirty_flow_lines(
                            &slot_opt,
                            &flow_lines,
                            &mut dirty_groups,
                            &mut st.flow_credit_writes,
                        ) {
                            eprintln!("mrv228_host: {e}");
                            break;
                        }
                        unflushed_bad_consumed = 0;
                        if let Err(e) =
                            post_q_credit(&slot_opt, &mut credit_gen, taken_q, local_epoch, &mut st.q_credit_writes)
                        {
                            eprintln!("mrv228_host: {e}");
                            break;
                        }
                        reported_taken_total = taken_total;
                        last_credit_post_tsc = now_c;
                    }
                    if slow_consumed_this_iter > 0 {
                        for q in 0..args.lanes {
                            // Only advance slow-path pull window when stalled slow-path staging is bounded
                            if slow_staged_q[q] < 64 {
                                pull_target_q[q] = (slow_head_q[q] as u32).saturating_add(args.ceio_pull_window);
                            }
                        }
                        if let Err(e) = post_ceio_pull(
                            &slot_opt,
                            &mut pull_gen,
                            local_epoch,
                            slow_head_q,
                            pull_target_q,
                            &mut st.pull_writes,
                        ) {
                            eprintln!("mrv228_host: {e}");
                            break;
                        }
                    }
                }
                Mode::KvSolo => unreachable!(),
            }

            // Flush any queued SDP TX ACK bursts
            for q in 0..args.lanes {
                if !tx_bursts[q].is_empty() {
                    let mut retries = 0u32;
                    while !tx_bursts[q].is_empty() {
                        if port.tx(q as u16, &mut tx_bursts[q]) == 0 {
                            retries += 1;
                            if retries > 200_000 {
                                st.tx_drops += tx_bursts[q].len() as u64;
                                tx_bursts[q].clear();
                                break;
                            }
                        }
                    }
                }
            }

            iter = iter.wrapping_add(1);
            if (iter & 0x3fff) == 0 {
                st.ooo = order.ooo_count;
                st.dup = order.dup_count;
                st.gap = order.gap_count;
                st.sw_fast_merged = sw_ring.stats.fast_merged;
                st.sw_slow_merged = sw_ring.stats.slow_merged;
                st.sw_phase_switches = sw_ring.stats.phase_switches;
                if let Ok(mut g) = stats_snap.try_lock() {
                    g.copy_from(&st);
                }
                if let Ok(ps) = port.stats() {
                    if let Ok(mut pg) = port_stats_snap.try_lock() {
                        *pg = ps;
                    }
                }
                if let Some(p) = host_pmu.as_ref() {
                    if let Ok(mut pl) = pmu_latest.try_lock() {
                        *pl = Some(p.snap(t_origin));
                    }
                }
            }
        }

        st.ooo = order.ooo_count;
        st.dup = order.dup_count;
        st.gap = order.gap_count;
        st.sw_fast_merged = sw_ring.stats.fast_merged;
        st.sw_slow_merged = sw_ring.stats.slow_merged;
        st.sw_phase_switches = sw_ring.stats.phase_switches;
        stats_snap.lock().unwrap().copy_from(&st);
        if let Ok(ps) = port.stats() {
            *port_stats_snap.lock().unwrap() = ps;
        }
        if let Some(p) = host_pmu.as_ref() {
            *pmu_latest.lock().unwrap() = Some(p.snap(t_origin));
        }

        quit.store(true, Ordering::Relaxed);
        let _ = ctrl_handle.join();
        if let Some(h) = coloc_handle {
            let _ = h.join();
        }
        Ok(())
    }
}
