//! Host-side multi-core (1-8 cores on Socket 1) standard-SDP RX benchmark for
//! MRV-210 (MRV-203 E4: uniform scaling 1->2->4->8, RSS skew Zipf/4:1, and
//! single slow-core 1-10 ms stall isolation).
//!
//! Compares four receive-ring architectures over the identical standard DPDK
//! `octeon_ep` VF0 (`0000:99:02.0`, up to 8 hardware queues) data plane:
//!   1. `priv-ring`:  Per-core private large SDP ring (`rx_desc = 2048`, 4 MiB/core -> 32 MiB @ 8 cores).
//!   2. `sh-ring`:    ShRing-style (OSDI'23) compact shared host `RxArr` ring (`R_sh = 1024`)
//!                    with cross-core spinlock & contiguous FIFO head reclamation (HoL under slow core).
//!   3. `rx-bisect`:  rxBisect-style (OSDI'25) lock-free small hot `Ax` LIFO pool + large per-core
//!                    host delivery ring `Bx[q]` (no HoL lock, but slow-core backlog accumulates in
//!                    host RAM via real NIC DMA, overflowing 2-way DDIO / polluting KV neighbor or
//!                    dropping once `Bx` fills).
//!   4. `credit-sdp`: Our design — per-core compact `SDP` hot ring (`rx_desc = 256..512`) +
//!                    dedicated compact per-queue hot mempool + exclusive 64 B NO-SFENCE `MOVDIR64B`
//!                    `BAR4` credit line (`credit_line_offset(q)`) gating CN106 on-path elastic FIFOs.
//!
//! Includes:
//!   - Concurrent Socket-1 `32 MiB` cache-sensitive KV random-lookup neighbor on a dedicated
//!     physical core (`cpu48`, Node-1 2 MiB hugepages, zero SMT sharing, zero page faults).
//!   - 100% non-multiplexed Host Core (every RX core + KV core) + Socket-1 Uncore PMU
//!     (mesh_clk, IIO1 inb_wr/dw/rd/cpu_wr, 32x CHA DDIO hit/miss, 8x IMC DRAM rd/wr).

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv210_host runs on x86_64 saturn");
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
    use std::mem::size_of;
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, EthStats, Mbuf, Mempool, Port, RxQueue, rdtsc};
    use testbed::lease::{self, Lease, LeasedSlot};
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};
    use mrv210::{
        CreditLine, HOST_CTRL_PORT, MAX_QUEUES, PayloadCheck, RefillTracker, StreamTracker,
        credit_line_offset, extract_payload, verify_payload,
    };

    const PORT: u16 = 0;
    const BURST_N: usize = 32;
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const NUM_CHAS: usize = 32;
    const NUM_IMCS: usize = 8;
    const REF_HZ: f64 = 2.2e9;
    const HUGE_2M: usize = 2 << 20;

    // NUMA policy syscalls
    const MPOL_BIND: i32 = 2;
    const MPOL_MF_STRICT: u32 = 1 << 0;
    const MPOL_MF_MOVE: u32 = 1 << 1;
    const MPOL_F_NODE: u64 = 1 << 0;
    const MPOL_F_ADDR: u64 = 1 << 1;

    // KV worker phases
    const KV_PHASE_WARMUP: u32 = 0;
    const KV_PHASE_MEASURE: u32 = 1;
    const KV_PHASE_STOP: u32 = 2;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Arm {
        PrivRing,
        ShRing,
        RxBisect,
        CreditSdp,
        KvSolo,
    }

    impl Arm {
        fn as_str(self) -> &'static str {
            match self {
                Self::PrivRing => "priv-ring",
                Self::ShRing => "sh-ring",
                Self::RxBisect => "rx-bisect",
                Self::CreditSdp => "credit-sdp",
                Self::KvSolo => "kv-solo",
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        /// Receive-ring architecture arm (or `kv-solo` unloaded baseline).
        #[arg(long, value_enum, default_value_t = Arm::CreditSdp)]
        arm: Arm,
        /// Number of host RX worker cores / VF0 SDP queues (`1..=8`).
        #[arg(long, default_value_t = 8)]
        queues: usize,
        /// Comma-separated physical CPU IDs of the RX worker cores (must match EAL worker lcores).
        #[arg(long, default_value = "40,41,42,43,44,45,46,47")]
        rx_cpus: String,
        /// Host SDP RX descriptors per queue (`2048` for privRing, `256`/`512` with allow_small_oq=1).
        #[arg(long, default_value_t = 512)]
        rx_desc: u16,
        /// Host SDP TX descriptors per queue (`octeon_ep` IQ minimum = 2048).
        #[arg(long, default_value_t = 2048)]
        tx_desc: u16,
        /// Mempool size (per-queue for priv-ring/credit-sdp; total shared pool for sh-ring/rx-bisect).
        #[arg(long, default_value_t = 640)]
        pool_size: u32,
        /// Mempool per-lcore LIFO cache size.
        #[arg(long, default_value_t = 128)]
        pool_cache: u32,
        /// Credit return batch B (packets per NO-SFENCE MOVDIR64B in `credit-sdp`).
        #[arg(long, default_value_t = 64)]
        credit_batch: u64,
        /// Only publish BAR4 credit for descriptors already refilled via `SLIST_DBELL` (Pitfall 1/4).
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        align_refill: bool,
        /// Shared `RxArr` capacity (packets) in `sh-ring` mode (power of 2, default 1024).
        #[arg(long, default_value_t = 1024)]
        shring_cap: usize,
        /// Per-core host delivery ring `Bx` capacity (packets) in `rx-bisect` mode.
        #[arg(long, default_value_t = 8192)]
        bx_cap: usize,
        /// Optional per-packet application compute time in ns (0 = pure RX+verify).
        #[arg(long, default_value_t = 0)]
        app_work_ns: u64,
        /// Slow-core index for stall injection (`-1` = disabled; `0..queues`).
        #[arg(long, default_value_t = -1, allow_hyphen_values = true)]
        stall_core: i32,
        /// Slow-core stall duration in microseconds (`1000..=10000` for 1-10 ms).
        #[arg(long, default_value_t = 0)]
        stall_us: u64,
        /// Periodic stall interval in milliseconds (0 = disabled).
        #[arg(long, default_value_t = 100)]
        stall_period_ms: u64,
        /// Expected wire payload length in bytes (0 = accept any >= 40 B).
        #[arg(long, default_value_t = 0)]
        expected_payload: usize,
        /// Monotonic run generation ID (stamped by mercury and written into BAR4 credit lines).
        #[arg(long, default_value_t = 1)]
        run_gen: u64,
        /// Concurrent Socket-1 KV table size in bytes (0 disables KV; default 32 MiB).
        #[arg(long, default_value_t = 33_554_432)]
        kv_bytes: usize,
        /// Dedicated Socket-1 physical CPU for concurrent KV neighbor (zero SMT sharing with cpu40..47).
        #[arg(long, default_value_t = 48)]
        kv_cpu: i32,
        /// Solo measurement duration in ms for `--arm kv-solo`.
        #[arg(long, default_value_t = 1000)]
        solo_ms: u64,
        /// Warmup duration in ms for `--arm kv-solo`.
        #[arg(long, default_value_t = 400)]
        solo_warmup_ms: u64,
        /// PF BAR4 sysfs resource file.
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar: String,
        /// Card MRV-26 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card_lease: String,
        /// Host control TCP listen port on `127.0.0.1`.
        #[arg(long, default_value_t = HOST_CTRL_PORT)]
        ctrl_port: u16,
    }

    // ---- Low-level OS Helpers (NUMA Node 1 Hugepages, Affinity, Page Faults) ----

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

    fn pin_thread(cpu: i32) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu as usize, &mut set);
            libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &raw const set);
        }
    }

    // ---- 100% Non-Multiplexed Sapphire Rapids Host Core + Socket-1 Uncore PMU ----

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
    const PERF_TYPE_RAW: u32 = 4;
    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    // Sapphire Rapids uncore PMU raw configs (verified across MRV-165 / MRV-172 / MRV-195 / MRV-212)
    const M2P_CLOCKTICKS: u64 = 0x01;
    const IIO_INB_WR: u64 = 0x0007_0FF0_0000_0184;
    const IIO_INB_WR_DW: u64 = 0x0007_0FF0_0000_0183;
    const IIO_INB_RD: u64 = 0x0007_0FF0_0000_0484;
    const IIO_CPU_WR: u64 = 0x0007_0FF0_0000_01C1;
    const CHA_IO_HIT_ITOM: u64 = 0x00CC_43FD_0000_0435;
    const CHA_IO_MISS_ITOM: u64 = 0x00CC_43FE_0000_0435;
    const IMC_CAS_RD: u64 = 0xCF05;
    const IMC_CAS_WR: u64 = 0xF005;
    const CORE_SB_STALLS: u64 = 0x08A2;
    const CORE_LLC_LOADS: u64 = 0x00002;
    const CORE_LLC_LOAD_MISSES: u64 = 0x10002;

    #[derive(Clone, Copy, Default)]
    struct Sample {
        val: u64,
        ena: u64,
        run: u64,
    }

    impl Sample {
        fn delta(self, prev: Self) -> Self {
            Self {
                val: self.val.wrapping_sub(prev.val),
                ena: self.ena.wrapping_sub(prev.ena),
                run: self.run.wrapping_sub(prev.run),
            }
        }

        fn pct(self) -> f64 {
            if self.ena == 0 {
                100.0
            } else {
                (self.run as f64 * 100.0 / self.ena as f64).min(100.0)
            }
        }
    }

    struct PerfCounter {
        fd: RawFd,
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
            let n = unsafe {
                libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>())
            };
            assert_eq!(n, size_of::<[u64; 3]>() as isize);
            Sample { val: buf[0], ena: buf[1], run: buf[2] }
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

    fn read_pmu_type(name: &str) -> io::Result<u32> {
        let s = fs::read_to_string(format!("/sys/bus/event_source/devices/{name}/type"))?;
        s.trim()
            .parse::<u32>()
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
    }

    struct CorePmu {
        cpu: i32,
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        instr: PerfCounter,
        llc_loads: PerfCounter,
        llc_misses: PerfCounter,
        sb_stalls: PerfCounter,
        page_faults: PerfCounter,
    }

    #[derive(Clone, Copy, Default)]
    struct CoreSnap {
        cpu: i32,
        cycles: Sample,
        ref_cycles: Sample,
        instr: Sample,
        llc_loads: Sample,
        llc_misses: Sample,
        sb_stalls: Sample,
        page_faults: Sample,
    }

    impl CorePmu {
        fn open(cpu: i32) -> io::Result<Self> {
            Ok(Self {
                cpu,
                cycles: PerfCounter::open(PERF_TYPE_HARDWARE, 0, cpu)?,
                instr: PerfCounter::open(PERF_TYPE_HARDWARE, 1, cpu)?,
                ref_cycles: PerfCounter::open(PERF_TYPE_HARDWARE, 9, cpu)?,
                llc_loads: PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOADS, cpu)?,
                llc_misses: PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOAD_MISSES, cpu)?,
                sb_stalls: PerfCounter::open(PERF_TYPE_RAW, CORE_SB_STALLS, cpu)?,
                page_faults: PerfCounter::open(PERF_TYPE_SOFTWARE, 2, cpu)?,
            })
        }

        fn snap(&self) -> CoreSnap {
            CoreSnap {
                cpu: self.cpu,
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                instr: self.instr.read(),
                llc_loads: self.llc_loads.read(),
                llc_misses: self.llc_misses.read(),
                sb_stalls: self.sb_stalls.read(),
                page_faults: self.page_faults.read(),
            }
        }
    }

    struct HostPmu {
        rx_cores: Vec<CorePmu>,
        kv_core: Option<CorePmu>,
        mesh_clk: PerfCounter,
        iio_inb_wr: PerfCounter,
        iio_inb_wr_dw: PerfCounter,
        iio_inb_rd: PerfCounter,
        iio_cpu_wr: PerfCounter,
        cha_hit: Vec<PerfCounter>,
        cha_miss: Vec<PerfCounter>,
        imc_rd: Vec<PerfCounter>,
        imc_wr: Vec<PerfCounter>,
    }

    struct HostSnap {
        wall: Instant,
        rx_cores: Vec<CoreSnap>,
        kv_core: Option<CoreSnap>,
        mesh_clk: Sample,
        iio_inb_wr: Sample,
        iio_inb_wr_dw: Sample,
        iio_inb_rd: Sample,
        iio_cpu_wr: Sample,
        cha_hit: [Sample; NUM_CHAS],
        cha_miss: [Sample; NUM_CHAS],
        imc_rd: [Sample; NUM_IMCS],
        imc_wr: [Sample; NUM_IMCS],
    }

    impl HostPmu {
        fn open(rx_cpus: &[i32], kv_cpu: Option<i32>) -> Result<Self, Box<dyn Error>> {
            let mut rx_cores = Vec::with_capacity(rx_cpus.len());
            for &c in rx_cpus {
                rx_cores.push(CorePmu::open(c)?);
            }
            let kv_core = match kv_cpu {
                Some(c) => Some(CorePmu::open(c)?),
                None => None,
            };
            let m2p_t = read_pmu_type("uncore_m2pcie_1")?;
            let iio_t = read_pmu_type("uncore_iio_1")?;
            let mut cha_hit = Vec::with_capacity(NUM_CHAS);
            let mut cha_miss = Vec::with_capacity(NUM_CHAS);
            for i in 0..NUM_CHAS {
                let t = read_pmu_type(&format!("uncore_cha_{i}"))?;
                cha_hit.push(PerfCounter::open(t, CHA_IO_HIT_ITOM, SOCKET1_UNCORE_CPU)?);
                cha_miss.push(PerfCounter::open(t, CHA_IO_MISS_ITOM, SOCKET1_UNCORE_CPU)?);
            }
            let mut imc_rd = Vec::with_capacity(NUM_IMCS);
            let mut imc_wr = Vec::with_capacity(NUM_IMCS);
            for i in 0..NUM_IMCS {
                let t = read_pmu_type(&format!("uncore_imc_{i}"))?;
                imc_rd.push(PerfCounter::open(t, IMC_CAS_RD, SOCKET1_UNCORE_CPU)?);
                imc_wr.push(PerfCounter::open(t, IMC_CAS_WR, SOCKET1_UNCORE_CPU)?);
            }
            Ok(Self {
                rx_cores,
                kv_core,
                mesh_clk: PerfCounter::open(m2p_t, M2P_CLOCKTICKS, SOCKET1_UNCORE_CPU)?,
                iio_inb_wr: PerfCounter::open(iio_t, IIO_INB_WR, SOCKET1_UNCORE_CPU)?,
                iio_inb_wr_dw: PerfCounter::open(iio_t, IIO_INB_WR_DW, SOCKET1_UNCORE_CPU)?,
                iio_inb_rd: PerfCounter::open(iio_t, IIO_INB_RD, SOCKET1_UNCORE_CPU)?,
                iio_cpu_wr: PerfCounter::open(iio_t, IIO_CPU_WR, SOCKET1_UNCORE_CPU)?,
                cha_hit,
                cha_miss,
                imc_rd,
                imc_wr,
            })
        }

        fn snap(&self) -> HostSnap {
            let rx_cores = self.rx_cores.iter().map(|c| c.snap()).collect();
            let kv_core = self.kv_core.as_ref().map(|c| c.snap());
            let mut cha_hit = [Sample::default(); NUM_CHAS];
            let mut cha_miss = [Sample::default(); NUM_CHAS];
            for i in 0..NUM_CHAS {
                cha_hit[i] = self.cha_hit[i].read();
                cha_miss[i] = self.cha_miss[i].read();
            }
            let mut imc_rd = [Sample::default(); NUM_IMCS];
            let mut imc_wr = [Sample::default(); NUM_IMCS];
            for i in 0..NUM_IMCS {
                imc_rd[i] = self.imc_rd[i].read();
                imc_wr[i] = self.imc_wr[i].read();
            }
            HostSnap {
                wall: Instant::now(),
                rx_cores,
                kv_core,
                mesh_clk: self.mesh_clk.read(),
                iio_inb_wr: self.iio_inb_wr.read(),
                iio_inb_wr_dw: self.iio_inb_wr_dw.read(),
                iio_inb_rd: self.iio_inb_rd.read(),
                iio_cpu_wr: self.iio_cpu_wr.read(),
                cha_hit,
                cha_miss,
                imc_rd,
                imc_wr,
            }
        }

        fn delta_json(s0: &HostSnap, s1: &HostSnap) -> String {
            let mut min_pct = 100.0f64;
            let mut d = |a: Sample, b: Sample| -> u64 {
                let x = b.delta(a);
                min_pct = min_pct.min(x.pct());
                x.val
            };
            let dt = s1.wall.saturating_duration_since(s0.wall).as_secs_f64().max(1e-9);

            let mut core_delta = |a: CoreSnap, b: CoreSnap| -> (String, u64, u64, u64, f64) {
                let cyc = d(a.cycles, b.cycles);
                let ref_c = d(a.ref_cycles, b.ref_cycles);
                let ins = d(a.instr, b.instr);
                let llc_l = d(a.llc_loads, b.llc_loads);
                let llc_m = d(a.llc_misses, b.llc_misses);
                let sb = d(a.sb_stalls, b.sb_stalls);
                let pf = d(a.page_faults, b.page_faults);
                let ghz = if ref_c > 0 { (cyc as f64 / ref_c as f64) * (REF_HZ / 1e9) } else { 0.0 };
                let ipc = ins as f64 / cyc.max(1) as f64;
                let llc_miss_pct = if llc_l > 0 { llc_m as f64 * 100.0 / llc_l as f64 } else { 0.0 };
                let sb_pct = sb as f64 * 100.0 / cyc.max(1) as f64;
                let js = format!(
                    "{{\"cpu\":{},\"ghz\":{ghz:.4},\"cycles\":{cyc},\"instr\":{ins},\"ipc\":{ipc:.3},\"llc_loads\":{llc_l},\"llc_misses\":{llc_m},\"llc_miss_pct\":{llc_miss_pct:.3},\"sb_stall_pct\":{sb_pct:.3},\"page_faults\":{pf}}}",
                    a.cpu
                );
                (js, cyc, llc_l, llc_m, ghz)
            };

            let mut rx_jsons = Vec::with_capacity(s0.rx_cores.len());
            let mut sum_rx_cycles = 0u64;
            let mut sum_rx_llc_loads = 0u64;
            let mut sum_rx_llc_misses = 0u64;
            let mut min_rx_ghz = f64::INFINITY;
            for (&a, &b) in s0.rx_cores.iter().zip(s1.rx_cores.iter()) {
                let (js, cyc, ll, lm, ghz) = core_delta(a, b);
                sum_rx_cycles += cyc;
                sum_rx_llc_loads += ll;
                sum_rx_llc_misses += lm;
                min_rx_ghz = min_rx_ghz.min(ghz);
                rx_jsons.push(js);
            }
            if s0.rx_cores.is_empty() {
                min_rx_ghz = 0.0;
            }
            let kv_json = match (s0.kv_core, s1.kv_core) {
                (Some(a), Some(b)) => core_delta(a, b).0,
                _ => "null".to_string(),
            };

            let mesh_clk = d(s0.mesh_clk, s1.mesh_clk);
            let mesh_ghz = mesh_clk as f64 / dt / 1e9;
            let inb_wr = d(s0.iio_inb_wr, s1.iio_inb_wr);
            let inb_wr_dw = d(s0.iio_inb_wr_dw, s1.iio_inb_wr_dw);
            let inb_rd = d(s0.iio_inb_rd, s1.iio_inb_rd);
            let cpu_wr = d(s0.iio_cpu_wr, s1.iio_cpu_wr);

            let mut cha_hit = 0u64;
            let mut cha_miss = 0u64;
            for i in 0..NUM_CHAS {
                cha_hit = cha_hit.saturating_add(d(s0.cha_hit[i], s1.cha_hit[i]));
                cha_miss = cha_miss.saturating_add(d(s0.cha_miss[i], s1.cha_miss[i]));
            }
            let ddio_tot = cha_hit + cha_miss;
            let ddio_hit_pct = if ddio_tot > 0 { cha_hit as f64 * 100.0 / ddio_tot as f64 } else { 100.0 };

            let mut imc_rd = 0u64;
            let mut imc_wr = 0u64;
            for i in 0..NUM_IMCS {
                imc_rd = imc_rd.saturating_add(d(s0.imc_rd[i], s1.imc_rd[i]));
                imc_wr = imc_wr.saturating_add(d(s0.imc_wr[i], s1.imc_wr[i]));
            }
            let imc_rd_gbps = (imc_rd as f64 * 64.0 * 8.0) / dt / 1e9;
            let imc_wr_gbps = (imc_wr as f64 * 64.0 * 8.0) / dt / 1e9;
            let inb_wr_mops = inb_wr as f64 / dt / 1e6;
            let inb_wr_gbps = (inb_wr_dw as f64 * 4.0 * 8.0) / dt / 1e9;
            let inb_wr_avg_bytes = if inb_wr > 0 { 4.0 * inb_wr_dw as f64 / inb_wr as f64 } else { 0.0 };
            let inb_rd_mops = inb_rd as f64 / dt / 1e6;
            let cpu_wr_mops = cpu_wr as f64 / dt / 1e6;
            let rx_llc_miss_pct = if sum_rx_llc_loads > 0 {
                sum_rx_llc_misses as f64 * 100.0 / sum_rx_llc_loads as f64
            } else {
                0.0
            };

            format!(
                "{{\"dt_s\":{dt:.6},\"min_running_pct\":{min_pct:.3},\"min_rx_ghz\":{min_rx_ghz:.4},\"mesh_ghz\":{mesh_ghz:.4},\"sum_rx_cycles\":{sum_rx_cycles},\"sum_rx_llc_loads\":{sum_rx_llc_loads},\"sum_rx_llc_misses\":{sum_rx_llc_misses},\"rx_llc_miss_pct\":{rx_llc_miss_pct:.3},\"iio1_inb_wr_mops\":{inb_wr_mops:.4},\"iio1_inb_wr_gbps\":{inb_wr_gbps:.4},\"iio1_inb_wr_avg_bytes\":{inb_wr_avg_bytes:.2},\"iio1_inb_rd_mops\":{inb_rd_mops:.4},\"iio1_cpu_wr_mops\":{cpu_wr_mops:.4},\"cha_io_hit_itom\":{cha_hit},\"cha_io_miss_itom\":{cha_miss},\"ddio_hit_pct\":{ddio_hit_pct:.3},\"imc_rd_lines\":{imc_rd},\"imc_wr_lines\":{imc_wr},\"imc_rd_gbps\":{imc_rd_gbps:.4},\"imc_wr_gbps\":{imc_wr_gbps:.4},\"rx_cores\":[{}],\"kv_core\":{kv_json}}}",
                rx_jsons.join(",")
            )
        }

        /// Measure minimum active core GHz (across all RX cores and optional KV core) and Socket-1 mesh GHz
        /// over `window_ms`. Used after warmup to ensure `schedutil` cores have ramped to steady-state turbo.
        fn measure_min_ghz(&self, window_ms: u64) -> (f64, f64) {
            let rx0: Vec<(Sample, Sample)> = self.rx_cores.iter().map(|c| (c.cycles.read(), c.ref_cycles.read())).collect();
            let kv0 = self.kv_core.as_ref().map(|c| (c.cycles.read(), c.ref_cycles.read()));
            let m0 = self.mesh_clk.read();
            let t0 = Instant::now();
            thread::sleep(Duration::from_millis(window_ms));
            let dt = t0.elapsed().as_secs_f64().max(1e-9);
            let m1 = self.mesh_clk.read();
            let mesh_ghz = m1.delta(m0).val as f64 / dt / 1e9;
            let mut min_ghz = f64::INFINITY;
            for (c, (c0, r0)) in self.rx_cores.iter().zip(rx0.iter()) {
                let dc = c.cycles.read().delta(*c0).val;
                let dr = c.ref_cycles.read().delta(*r0).val;
                let g = if dr > 0 { (dc as f64 / dr as f64) * (REF_HZ / 1e9) } else { 0.0 };
                min_ghz = min_ghz.min(g);
            }
            if let (Some(c), Some((c0, r0))) = (self.kv_core.as_ref(), kv0) {
                let dc = c.cycles.read().delta(c0).val;
                let dr = c.ref_cycles.read().delta(r0).val;
                let g = if dr > 0 { (dc as f64 / dr as f64) * (REF_HZ / 1e9) } else { 0.0 };
                min_ghz = min_ghz.min(g);
            }
            if !min_ghz.is_finite() {
                min_ghz = 0.0;
            }
            (min_ghz, mesh_ghz)
        }
    }
    // ---- Concurrent Socket-1 32 MiB Cache-Sensitive KV Neighbor (MRV-165 verified design) ----

    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct KvEntry {
        key: u64,
        val: [u64; 6],
        next_salt: u64,
    }

    const _: () = assert!(size_of::<KvEntry>() == 64);

    #[inline(always)]
    fn splitmix64(mut x: u64) -> u64 {
        x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
        x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        x ^ (x >> 31)
    }

    fn init_kv_table(buf: &NumaHugeBuf, kv_bytes: usize) {
        let n = kv_bytes / size_of::<KvEntry>();
        let entries = buf.as_ptr().cast::<KvEntry>();
        for i in 0..n {
            let k = (i as u64) ^ 0x55aa_2100_9911_2233;
            let salt = splitmix64((i as u64).wrapping_mul(0x210_cafe_0001) ^ 0xfeed_face);
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
                options(nostack, preserves_flags),
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
                options(nostack, preserves_flags),
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

    /// L1d-resident dual-resolution latency histogram (24 KiB):
    /// - 2048 fine bins of `fine_step` (ns for KV, us for inter-batch gap)
    /// - 1024 coarse bins of `coarse_step`
    const HIST_FINE_BINS: usize = 2048;
    const HIST_COARSE_BINS: usize = 1024;

    struct DualHist {
        fine: Box<[u64; HIST_FINE_BINS]>,
        coarse: Box<[u64; HIST_COARSE_BINS]>,
        fine_step: f64,
        coarse_step: f64,
        count: u64,
        sum: f64,
        min_v: f64,
        max_v: f64,
        overflow: u64,
    }

    impl DualHist {
        fn new(fine_step: f64, coarse_step: f64) -> Self {
            let fine = Box::new([0u64; HIST_FINE_BINS]);
            let coarse = Box::new([0u64; HIST_COARSE_BINS]);
            unsafe {
                let _ = libc::mlock(fine.as_ptr().cast(), size_of::<[u64; HIST_FINE_BINS]>());
                let _ = libc::mlock(coarse.as_ptr().cast(), size_of::<[u64; HIST_COARSE_BINS]>());
            }
            Self {
                fine,
                coarse,
                fine_step,
                coarse_step,
                count: 0,
                sum: 0.0,
                min_v: f64::INFINITY,
                max_v: 0.0,
                overflow: 0,
            }
        }
        fn reset(&mut self) {
            self.fine.fill(0);
            self.coarse.fill(0);
            self.count = 0;
            self.sum = 0.0;
            self.min_v = f64::INFINITY;
            self.max_v = 0.0;
            self.overflow = 0;
        }

        #[inline(always)]
        fn record(&mut self, v: f64) {
            let val = v.max(0.0);
            self.count += 1;
            self.sum += val;
            if val < self.min_v {
                self.min_v = val;
            }
            if val > self.max_v {
                self.max_v = val;
            }
            let fine_cap = HIST_FINE_BINS as f64 * self.fine_step;
            if val < fine_cap {
                let idx = (val / self.fine_step) as usize;
                self.fine[idx.min(HIST_FINE_BINS - 1)] += 1;
            } else {
                let cidx = ((val - fine_cap) / self.coarse_step) as usize;
                if cidx < HIST_COARSE_BINS {
                    self.coarse[cidx] += 1;
                } else {
                    self.overflow += 1;
                }
            }
        }

        fn pct(&self, q: f64) -> f64 {
            if self.count == 0 {
                return 0.0;
            }
            let target = (((self.count - 1) as f64) * q).round() as u64;
            let mut acc = 0u64;
            for (i, &c) in self.fine.iter().enumerate() {
                if acc + c > target {
                    return (i as f64 + 0.5) * self.fine_step;
                }
                acc += c;
            }
            let fine_cap = HIST_FINE_BINS as f64 * self.fine_step;
            for (i, &c) in self.coarse.iter().enumerate() {
                if acc + c > target {
                    return fine_cap + (i as f64 + 0.5) * self.coarse_step;
                }
                acc += c;
            }
            self.max_v
        }

        fn summary(&self) -> (f64, f64, f64, f64, f64, f64, f64) {
            if self.count == 0 {
                return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
            }
            (
                self.sum / self.count as f64,
                self.min_v,
                self.pct(0.50),
                self.pct(0.90),
                self.pct(0.99),
                self.pct(0.999),
                self.max_v,
            )
        }
    }

    #[derive(Clone, Default)]
    struct KvReport {
        lookups: u64,
        bad: u64,
        page_faults: u64,
        wall_s: f64,
        timer_floor_ns: f64,
        mean_ns: f64,
        min_ns: f64,
        p50_ns: f64,
        p90_ns: f64,
        p99_ns: f64,
        p999_ns: f64,
        max_ns: f64,
        overflow: u64,
    }

    impl KvReport {
        fn to_json(&self) -> String {
            let mlookups_s = if self.wall_s > 0.0 {
                self.lookups as f64 / self.wall_s / 1e6
            } else {
                0.0
            };
            format!(
                "{{\"lookups\":{},\"bad\":{},\"page_faults\":{},\"wall_s\":{:.6},\"mlookups_s\":{:.4},\"timer_floor_ns\":{:.2},\"mean_ns\":{:.2},\"min_ns\":{:.2},\"p50_ns\":{:.2},\"p90_ns\":{:.2},\"p99_ns\":{:.2},\"p999_ns\":{:.2},\"max_ns\":{:.2},\"overflow\":{}}}",
                self.lookups,
                self.bad,
                self.page_faults,
                self.wall_s,
                mlookups_s,
                self.timer_floor_ns,
                self.mean_ns,
                self.min_ns,
                self.p50_ns,
                self.p90_ns,
                self.p99_ns,
                self.p999_ns,
                self.max_ns,
                self.overflow,
            )
        }
    }

    struct KvShared {
        phase: AtomicU32,
        cmd_gen: AtomicU64,
        ack_gen: AtomicU64,
        report: Mutex<KvReport>,
    }

    impl KvShared {
        fn new() -> Self {
            Self {
                phase: AtomicU32::new(KV_PHASE_WARMUP),
                cmd_gen: AtomicU64::new(0),
                ack_gen: AtomicU64::new(0),
                report: Mutex::new(KvReport::default()),
            }
        }

        fn reset_window(&self) {
            let next = self.cmd_gen.load(Ordering::Relaxed) + 1;
            self.phase.store(KV_PHASE_MEASURE, Ordering::Release);
            self.cmd_gen.store(next, Ordering::Release);
            let t0 = Instant::now();
            while self.ack_gen.load(Ordering::Acquire) < next && t0.elapsed().as_secs() < 3 {
                std::hint::spin_loop();
            }
        }

        fn snapshot(&self) -> KvReport {
            // Trigger snapshot via phase transition back to WARMUP and wait for ack
            let next = self.cmd_gen.load(Ordering::Relaxed) + 1;
            self.phase.store(KV_PHASE_WARMUP, Ordering::Release);
            self.cmd_gen.store(next, Ordering::Release);
            let t0 = Instant::now();
            while self.ack_gen.load(Ordering::Acquire) < next && t0.elapsed().as_secs() < 3 {
                std::hint::spin_loop();
            }
            self.report.lock().unwrap().clone()
        }
    }

    fn run_kv_worker(buf: Arc<NumaHugeBuf>, kv_bytes: usize, kv_cpu: i32, tsc_hz: f64, sh: Arc<KvShared>) {
        pin_thread(kv_cpu);
        let n_entries = kv_bytes / size_of::<KvEntry>();
        let mask = n_entries - 1;
        debug_assert!(n_entries.is_power_of_two());
        let entries = buf.as_ptr().cast::<KvEntry>();
        let ns_per_tick = 1e9 / tsc_hz;

        let mut hist = DualHist::new(2.0, 64.0);
        hist.reset();

        // Warm all 2 MiB TLB entries & cache lines once before warmup phase
        let mut rng = 0x1234_5678_9abc_def1u64;
        let mut warm_acc = 0u64;
        for i in 0..n_entries {
            let e = unsafe { &*entries.add(i) };
            warm_acc ^= e.key ^ e.next_salt;
        }
        rng ^= warm_acc;

        let mut floor_ticks = calibrate_kv_timer_floor();
        let mut floor_ns = floor_ticks as f64 * ns_per_tick;
        let mut lookups = 0u64;
        let mut bad = 0u64;
        let mut pf0 = thread_page_faults();
        let mut t0 = Instant::now();
        let mut seen_gen = 0u64;
        let mut measuring = false;

        loop {
            let phase = sh.phase.load(Ordering::Acquire);
            if phase == KV_PHASE_STOP {
                break;
            }
            let cg = sh.cmd_gen.load(Ordering::Acquire);
            if cg > seen_gen {
                if measuring {
                    let pf1 = thread_page_faults();
                    let wall_s = t0.elapsed().as_secs_f64();
                    let (mean, min_v, p50, p90, p99, p999, max_v) = hist.summary();
                    *sh.report.lock().unwrap() = KvReport {
                        lookups,
                        bad,
                        page_faults: pf1.saturating_sub(pf0),
                        wall_s,
                        timer_floor_ns: floor_ns,
                        mean_ns: mean,
                        min_ns: min_v,
                        p50_ns: p50,
                        p90_ns: p90,
                        p99_ns: p99,
                        p999_ns: p999,
                        max_ns: max_v,
                        overflow: hist.overflow,
                    };
                }
                measuring = phase == KV_PHASE_MEASURE;
                if measuring {
                    // Recalibrate timer floor AFTER warmup while kv_cpu is at steady-state turbo
                    floor_ticks = calibrate_kv_timer_floor();
                    floor_ns = floor_ticks as f64 * ns_per_tick;
                    hist.reset();
                    lookups = 0;
                    bad = 0;
                    pf0 = thread_page_faults();
                    t0 = Instant::now();
                }
                seen_gen = cg;
                sh.ack_gen.store(cg, Ordering::Release);
            }

            // Execute 64 dependent random cache-line lookups per control-check iteration
            for _ in 0..64 {
                let idx = (rng as usize) & mask;
                let want_key = (idx as u64) ^ 0x55aa_2100_9911_2233;
                if measuring {
                    let ts0 = unsafe { rdtsc_begin() };
                    let e = unsafe { &*entries.add(idx) };
                    let dep = e.key ^ e.val[0] ^ e.val[5] ^ e.next_salt;
                    let ts1 = unsafe { rdtsc_end(dep) };
                    let net_ticks = ts1.saturating_sub(ts0).saturating_sub(floor_ticks);
                    hist.record(net_ticks as f64 * ns_per_tick);
                    if e.key != want_key {
                        bad += 1;
                    }
                    lookups += 1;
                    rng = splitmix64(rng ^ e.next_salt);
                } else {
                    let e = unsafe { &*entries.add(idx) };
                    if e.key != want_key {
                        bad += 1;
                    }
                    rng = splitmix64(rng ^ e.next_salt);
                }
            }
        }
        std::hint::black_box(rng);
    }

    // ---- ShRing (OSDI'23) Shared Compact Host Receive Ring with Cross-Core Spinlock ----

    struct ShRingInner<'e> {
        head: u64,
        tail: u64,
        cap: usize,
        mask: usize,
        pkts: Vec<Option<Mbuf<'e>>>,
        done: Vec<bool>,
    }

    struct ShRingState<'e> {
        lock: AtomicBool,
        inner: std::cell::UnsafeCell<ShRingInner<'e>>,
    }

    // SAFETY: All accesses to `inner` are serialized by the TTAS spinlock `lock`.
    unsafe impl Sync for ShRingState<'_> {}

    impl<'e> ShRingState<'e> {
        fn new(cap: usize) -> Self {
            let cap = cap.next_power_of_two().max(128);
            let mut pkts = Vec::with_capacity(cap);
            pkts.resize_with(cap, || None);
            Self {
                lock: AtomicBool::new(false),
                inner: std::cell::UnsafeCell::new(ShRingInner {
                    head: 0,
                    tail: 0,
                    cap,
                    mask: cap - 1,
                    pkts,
                    done: vec![false; cap],
                }),
            }
        }

        #[inline(always)]
        fn acquire(&self, wait_ticks_acc: &mut u64) {
            if self.lock.compare_exchange_weak(false, true, Ordering::Acquire, Ordering::Relaxed).is_ok() {
                return;
            }
            let t0 = rdtsc();
            loop {
                while self.lock.load(Ordering::Relaxed) {
                    std::hint::spin_loop();
                }
                if self.lock.compare_exchange_weak(false, true, Ordering::Acquire, Ordering::Relaxed).is_ok() {
                    *wait_ticks_acc += rdtsc().saturating_sub(t0);
                    return;
                }
            }
        }

        #[inline(always)]
        fn release(&self) {
            self.lock.store(false, Ordering::Release);
        }

        /// Reserve `count` contiguous slots in shared `RxArr` and store raw packet pointers.
        /// Returns `Some(start_seq)` if room exists, or `None` if `RxArr` is HoL-blocked/full.
        fn try_push_burst(&self, burst: &mut Burst<'e, BURST_N>, wait_ticks_acc: &mut u64) -> Option<(u64, usize)> {
            let n = burst.len();
            if n == 0 {
                return Some((0, 0));
            }
            self.acquire(wait_ticks_acc);
            let inner = unsafe { &mut *self.inner.get() };
            let used = inner.tail.saturating_sub(inner.head) as usize;
            let res = if used + n <= inner.cap {
                let start = inner.tail;
                let mut cur = start;
                burst.drain_into(|m| {
                    let idx = (cur as usize) & inner.mask;
                    inner.pkts[idx] = Some(m);
                    inner.done[idx] = false;
                    cur += 1;
                });
                inner.tail = cur;
                Some((start, n))
            } else {
                None
            };
            self.release();
            res
        }

        /// Mark `[start .. start + n)` done and contiguously reclaim all completed slots from `head`.
        /// Freed `Mbuf`s are dropped outside the critical section? No: returning them to the shared
        /// mempool after advancing `head` models `ShRing`'s synchronized buffer-return step.
        fn complete_and_reclaim(&self, start: u64, n: usize, wait_ticks_acc: &mut u64) -> usize {
            let mut to_free: Burst<'e, 64> = Burst::new();
            let mut reclaimed = 0usize;
            self.acquire(wait_ticks_acc);
            let inner = unsafe { &mut *self.inner.get() };
            for i in 0..n {
                let idx = ((start + i as u64) as usize) & inner.mask;
                inner.done[idx] = true;
            }
            while inner.head < inner.tail {
                let idx = (inner.head as usize) & inner.mask;
                if !inner.done[idx] {
                    break;
                }
                inner.done[idx] = false;
                if let Some(m) = inner.pkts[idx].take() {
                    if to_free.push(m).is_err() {
                        to_free.clear();
                    }
                }
                inner.head += 1;
                reclaimed += 1;
            }
            self.release();
            to_free.clear();
            reclaimed
        }

        /// Borrow packet slices for `[start .. start + n)` while owned exclusively by this worker
        /// (slot is reserved and cannot be reclaimed until this worker calls `complete_and_reclaim`).
        unsafe fn verify_slot_range(
            &self,
            start: u64,
            n: usize,
            expected_payload: usize,
            run_gen: u64,
            tracker: &mut StreamTracker,
            cnt: &mut CoreCnt,
        ) {
            let inner = unsafe { &*self.inner.get() };
            for i in 0..n {
                let idx = ((start + i as u64) as usize) & inner.mask;
                if let Some(m) = inner.pkts[idx].as_ref() {
                    verify_mbuf(m, expected_payload, run_gen, tracker, cnt);
                }
            }
        }

        fn force_clear(&self) {
            let mut dummy = 0u64;
            self.acquire(&mut dummy);
            let inner = unsafe { &mut *self.inner.get() };
            for p in &mut inner.pkts {
                let _ = p.take();
            }
            inner.done.fill(false);
            inner.head = 0;
            inner.tail = 0;
            self.release();
        }
    }

    // ---- Shared Per-Core Counters & Verification ----

    #[derive(Clone, Default)]
    struct CoreCnt {
        rx_pkts: u64,
        rx_bytes: u64,
        bad: u64,
        torn: u64,
        stream_lost: u64,
        stream_ooo: u64,
        empty_polls: u64,
        nonempty_polls: u64,
        credit_writes: u64,
        hol_stalls: u64,
        lock_wait_us: f64,
        bx_peak: u64,
        bx_overflow_drops: u64,
        stalls_injected: u64,
        stall_drain_max_us: f64,
        page_faults: u64,
        gap_mean_us: f64,
        gap_p50_us: f64,
        gap_p99_us: f64,
        gap_p999_us: f64,
        gap_max_us: f64,
    }

    impl CoreCnt {
        fn to_json(&self, q: usize, cpu: i32) -> String {
            format!(
                "{{\"q\":{q},\"cpu\":{cpu},\"rx_pkts\":{},\"rx_bytes\":{},\"bad\":{},\"torn\":{},\"stream_lost\":{},\"stream_ooo\":{},\"empty_polls\":{},\"nonempty_polls\":{},\"credit_writes\":{},\"hol_stalls\":{},\"lock_wait_us\":{:.2},\"bx_peak\":{},\"bx_overflow_drops\":{},\"stalls_injected\":{},\"stall_drain_max_us\":{:.2},\"page_faults\":{},\"gap_mean_us\":{:.3},\"gap_p50_us\":{:.3},\"gap_p99_us\":{:.3},\"gap_p999_us\":{:.3},\"gap_max_us\":{:.3}}}",
                self.rx_pkts,
                self.rx_bytes,
                self.bad,
                self.torn,
                self.stream_lost,
                self.stream_ooo,
                self.empty_polls,
                self.nonempty_polls,
                self.credit_writes,
                self.hol_stalls,
                self.lock_wait_us,
                self.bx_peak,
                self.bx_overflow_drops,
                self.stalls_injected,
                self.stall_drain_max_us,
                self.page_faults,
                self.gap_mean_us,
                self.gap_p50_us,
                self.gap_p99_us,
                self.gap_p999_us,
                self.gap_max_us,
            )
        }
    }

    #[inline(always)]
    fn verify_mbuf(
        m: &Mbuf<'_>,
        expected_payload: usize,
        run_gen: u64,
        tracker: &mut StreamTracker,
        cnt: &mut CoreCnt,
    ) {
        let d = m.data();
        cnt.rx_pkts += 1;
        cnt.rx_bytes += (d.len() + 4) as u64;
        match extract_payload(d) {
            Some(pay) => match verify_payload(pay, expected_payload, run_gen) {
                PayloadCheck::Ok { stream_id, seq, .. } => {
                    cnt.stream_lost += tracker.observe(stream_id, seq);
                }
                PayloadCheck::Bad => cnt.bad += 1,
                PayloadCheck::Torn => cnt.torn += 1,
            },
            None => cnt.bad += 1,
        }
    }

    #[inline(always)]
    fn do_app_work(ticks: u64) {
        if ticks == 0 {
            return;
        }
        let end = rdtsc().wrapping_add(ticks);
        let mut x = 0x1357_9bdf_u64;
        while rdtsc() < end {
            x = x.wrapping_mul(6364136223846793005).wrapping_add(1);
            std::hint::black_box(x);
        }
    }

    #[inline(always)]
    fn publish_bar4_credit(
        slot: &LeasedSlot<'_>,
        q: usize,
        taken: u64,
        run_gen: u64,
        bytes_taken: u64,
    ) -> Result<(), String> {
        slot.check().map_err(|e| e.to_string())?;
        let line = CreditLine::new(q, taken, run_gen, bytes_taken, rdtsc());
        // SAFETY: `credit_line_offset(q)` is 64 B aligned inside leased BAR4 slot 15.
        // Single atomic 64 B posted write; NO SFENCE required (MRV-203 §I & §IV Pitfall 4).
        unsafe { movdir64b(slot.as_ptr().add(credit_line_offset(q)), &line) };
        Ok(())
    }

    struct SyncSlot {
        ack_gen: AtomicU64,
        snap: Mutex<CoreCnt>,
    }

    impl SyncSlot {
        fn new() -> Self {
            Self { ack_gen: AtomicU64::new(0), snap: Mutex::new(CoreCnt::default()) }
        }
    }

    struct Shared {
        arm: Arm,
        rx_cpus: Vec<i32>,
        expected_payload: usize,
        run_gen: AtomicU64,
        credit_batch: u64,
        align_refill: bool,
        bx_cap: usize,
        app_work_ticks: u64,
        stall_core: i32,
        stall_ticks: u64,
        stall_period_ticks: u64,
        tsc_hz: f64,
        active: AtomicBool,
        quit: AtomicBool,
        /// `(gen << 4) | cmd`: 1 = RESET, 2 = SNAP
        cmd_word: AtomicU64,
        slots: Vec<SyncSlot>,
        eth_base: Mutex<EthStats>,
        t_reset: Mutex<Instant>,
    }

    impl Shared {
        fn broadcast_cmd(&self, cmd: u64) -> Vec<CoreCnt> {
            let cur = self.cmd_word.load(Ordering::Relaxed);
            let next_gen = (cur >> 4) + 1;
            self.cmd_word.store((next_gen << 4) | cmd, Ordering::Release);
            let deadline = Instant::now() + Duration::from_secs(5);
            for s in &self.slots {
                while s.ack_gen.load(Ordering::Acquire) < next_gen {
                    if Instant::now() >= deadline || self.quit.load(Ordering::Relaxed) {
                        break;
                    }
                    std::hint::spin_loop();
                }
            }
            self.slots.iter().map(|s| s.snap.lock().unwrap().clone()).collect()
        }
    }

    struct WorkerInput<'p, 'e, 'l> {
        q: usize,
        rx_q: RxQueue<'p, 'e>,
        bar4_slot: Option<LeasedSlot<'l>>,
        shring: Option<&'p ShRingState<'e>>,
    }

    fn worker_main<'p, 'e, 'l>(mut inp: WorkerInput<'p, 'e, 'l>, sh: &Shared) {
        let q = inp.q;
        let is_stall_core = sh.stall_core == q as i32 && sh.stall_ticks > 0 && sh.stall_period_ticks > 0;
        let us_per_tick = 1e6 / sh.tsc_hz;

        let mut cnt = CoreCnt::default();
        let mut tracker = StreamTracker::new();
        let mut refill = RefillTracker::new();
        // Inter-batch gap histogram: 0.5 us fine bins (0..1024 us), 32 us coarse bins (1..33 ms)
        let mut gap_hist = DualHist::new(0.5, 32.0);
        let mut rx_b: Burst<'e, BURST_N> = Burst::new();
        let mut bx_ring: VecDeque<Mbuf<'e>> = if sh.arm == Arm::RxBisect {
            VecDeque::with_capacity(sh.bx_cap.min(65536))
        } else {
            VecDeque::new()
        };

        let mut handled_cmd_gen = 0u64;
        let mut pf0 = thread_page_faults();
        let mut lock_wait_ticks = 0u64;
        let mut last_rx_tsc = rdtsc();
        let mut next_stall_tsc = rdtsc().wrapping_add(sh.stall_period_ticks / 2);
        let mut cur_run_gen = sh.run_gen.load(Ordering::Acquire);
        let mut total_bytes_for_credit = 0u64;

        while !sh.quit.load(Ordering::Relaxed) {
            let cw = sh.cmd_word.load(Ordering::Acquire);
            let cg = cw >> 4;
            if cg > handled_cmd_gen {
                let cmd = cw & 0xf;
                if cmd == 1 {
                    cur_run_gen = sh.run_gen.load(Ordering::Acquire);
                    bx_ring.clear();
                    rx_b.clear();
                    tracker.reset();
                    gap_hist.reset();
                    cnt = CoreCnt::default();
                    lock_wait_ticks = 0;
                    pf0 = thread_page_faults();
                    last_rx_tsc = rdtsc();
                    next_stall_tsc = last_rx_tsc.wrapping_add(sh.stall_period_ticks / 2);
                    // Reset per-run credit baseline cleanly
                    refill = RefillTracker::new();
                    total_bytes_for_credit = 0;
                    if let Some(slot) = inp.bar4_slot.as_ref() {
                        let _ = publish_bar4_credit(slot, q, 0, cur_run_gen, 0);
                    }
                    *sh.slots[q].snap.lock().unwrap() = cnt.clone();
                } else if cmd == 2 {
                    cnt.stream_ooo = tracker.ooo;
                    cnt.page_faults = thread_page_faults().saturating_sub(pf0);
                    cnt.lock_wait_us = lock_wait_ticks as f64 * us_per_tick;
                    let (gm, _, gp50, _, gp99, gp999, gmax) = gap_hist.summary();
                    cnt.gap_mean_us = gm;
                    cnt.gap_p50_us = gp50;
                    cnt.gap_p99_us = gp99;
                    cnt.gap_p999_us = gp999;
                    cnt.gap_max_us = gmax;
                    *sh.slots[q].snap.lock().unwrap() = cnt.clone();
                }
                handled_cmd_gen = cg;
                sh.slots[q].ack_gen.store(cg, Ordering::Release);
            }

            let active = sh.active.load(Ordering::Relaxed);

            // ---- Slow-Core Stall Injection (1-10 ms) ----
            if active && is_stall_core {
                let now = rdtsc();
                if now >= next_stall_tsc {
                    let stall_end = now.wrapping_add(sh.stall_ticks);
                    cnt.stalls_injected += 1;

                    match sh.arm {
                        Arm::PrivRing | Arm::CreditSdp => {
                            // Genuine application/GC pause: core stops polling VF0 RX and (in CreditSDP)
                            // stops returning BAR4 credits. In CreditSDP, CN106 buffers 100% of arrivals
                            // in card L3/DDR5 FIFO without touching host LLC/DDIO!
                            while rdtsc() < stall_end && !sh.quit.load(Ordering::Relaxed) && sh.active.load(Ordering::Relaxed) {
                                std::hint::spin_loop();
                            }
                            let drain_t0 = rdtsc();
                            // Immediately resume and publish credit upon wakeup
                            let mut idle_spins = 0u32;
                            while idle_spins < 64 && !sh.quit.load(Ordering::Relaxed) {
                                let n = inp.rx_q.rx(&mut rx_b);
                                refill.on_rx_burst(n as u64);
                                if n == 0 {
                                    idle_spins += 1;
                                    if let Some(slot) = inp.bar4_slot.as_ref() {
                                        if let Some(cval) = refill.flush_credit(sh.align_refill) {
                                            let _ = publish_bar4_credit(slot, q, cval, cur_run_gen, total_bytes_for_credit);
                                            cnt.credit_writes += 1;
                                        }
                                    }
                                    continue;
                                }
                                idle_spins = 0;
                                for m in rx_b.as_slice() {
                                    verify_mbuf(m, sh.expected_payload, cur_run_gen, &mut tracker, &mut cnt);
                                    total_bytes_for_credit += m.data_len() as u64;
                                }
                                rx_b.clear();
                                if let Some(slot) = inp.bar4_slot.as_ref() {
                                    if let Some(cval) = refill.poll_credit_due(sh.credit_batch, sh.align_refill) {
                                        let _ = publish_bar4_credit(slot, q, cval, cur_run_gen, total_bytes_for_credit);
                                        cnt.credit_writes += 1;
                                    }
                                }
                            }
                            let drain_us = rdtsc().saturating_sub(drain_t0) as f64 * us_per_tick;
                            if drain_us > cnt.stall_drain_max_us {
                                cnt.stall_drain_max_us = drain_us;
                            }
                        }
                        Arm::ShRing => {
                            // Slow core holds a live batch in the shared `RxArr` during its 1-10 ms pause,
                            // pinning `RxArr.head` so healthy cores HoL-block once the shared 1024-slot ring fills!
                            let shring = inp.shring.unwrap();
                            let held: Option<(u64, usize)> = loop {
                                let n = inp.rx_q.rx(&mut rx_b);
                                if n > 0 {
                                    if let Some(span) = shring.try_push_burst(&mut rx_b, &mut lock_wait_ticks) {
                                        break Some(span);
                                    } else {
                                        cnt.hol_stalls += 1;
                                        rx_b.clear();
                                        break None;
                                    }
                                }
                                if rdtsc() >= stall_end || sh.quit.load(Ordering::Relaxed) {
                                    break None;
                                }
                                std::hint::spin_loop();
                            };
                            while rdtsc() < stall_end && !sh.quit.load(Ordering::Relaxed) && sh.active.load(Ordering::Relaxed) {
                                std::hint::spin_loop();
                            }
                            if let Some((start, count)) = held {
                                unsafe {
                                    shring.verify_slot_range(
                                        start,
                                        count,
                                        sh.expected_payload,
                                        cur_run_gen,
                                        &mut tracker,
                                        &mut cnt,
                                    );
                                }
                                shring.complete_and_reclaim(start, count, &mut lock_wait_ticks);
                            }
                        }
                        Arm::RxBisect => {
                            // rxBisect (OSDI'25): NIC keeps DMA-writing slow core's packets into fresh
                            // host RAM buffers from `Ax` and appending to host delivery ring `Bx[slow]`
                            // while the application is stalled. Host buffers stay pinned in `Bx[slow]`
                            // (overflowing 2-way DDIO & polluting KV neighbor) or drop if `Bx` fills!
                            while rdtsc() < stall_end && !sh.quit.load(Ordering::Relaxed) && sh.active.load(Ordering::Relaxed) {
                                let n = inp.rx_q.rx(&mut rx_b);
                                if n == 0 {
                                    std::hint::spin_loop();
                                    continue;
                                }
                                rx_b.drain_into(|m| {
                                    if bx_ring.len() < sh.bx_cap {
                                        bx_ring.push_back(m);
                                        let d = bx_ring.len() as u64;
                                        if d > cnt.bx_peak {
                                            cnt.bx_peak = d;
                                        }
                                    } else {
                                        cnt.bx_overflow_drops += 1;
                                    }
                                });
                            }
                            // Application wakes up and drains the accumulated host-RAM `Bx[slow]` backlog
                            let drain_t0 = rdtsc();
                            while let Some(m) = bx_ring.pop_front() {
                                verify_mbuf(&m, sh.expected_payload, cur_run_gen, &mut tracker, &mut cnt);
                                do_app_work(sh.app_work_ticks);
                            }
                            let drain_us = rdtsc().saturating_sub(drain_t0) as f64 * us_per_tick;
                            if drain_us > cnt.stall_drain_max_us {
                                cnt.stall_drain_max_us = drain_us;
                            }
                        }
                        Arm::KvSolo => {}
                    }
                    last_rx_tsc = rdtsc();
                    next_stall_tsc = last_rx_tsc.wrapping_add(sh.stall_period_ticks);
                    continue;
                }
            }

            // ---- Normal RX Poll Loop ----
            let n = inp.rx_q.rx(&mut rx_b);
            refill.on_rx_burst(n as u64);

            if n == 0 {
                if active {
                    cnt.empty_polls += 1;
                }
                if sh.arm == Arm::CreditSdp {
                    if let Some(slot) = inp.bar4_slot.as_ref() {
                        if let Some(cval) = refill.poll_credit_due(sh.credit_batch, sh.align_refill) {
                            let _ = publish_bar4_credit(slot, q, cval, cur_run_gen, total_bytes_for_credit);
                            cnt.credit_writes += 1;
                        }
                    }
                }
                continue;
            }

            if !active {
                rx_b.clear();
                if sh.arm == Arm::CreditSdp {
                    if let Some(slot) = inp.bar4_slot.as_ref() {
                        if let Some(cval) = refill.poll_credit_due(sh.credit_batch, sh.align_refill) {
                            let _ = publish_bar4_credit(slot, q, cval, cur_run_gen, total_bytes_for_credit);
                            cnt.credit_writes += 1;
                        }
                    }
                }
                continue;
            }

            cnt.nonempty_polls += 1;
            let now_tsc = rdtsc();
            gap_hist.record(now_tsc.saturating_sub(last_rx_tsc) as f64 * us_per_tick);
            last_rx_tsc = now_tsc;

            match sh.arm {
                Arm::PrivRing => {
                    for m in rx_b.as_slice() {
                        verify_mbuf(m, sh.expected_payload, cur_run_gen, &mut tracker, &mut cnt);
                        do_app_work(sh.app_work_ticks);
                    }
                    rx_b.clear();
                }
                Arm::ShRing => {
                    let shring = inp.shring.unwrap();
                    // Spin waiting for free space in shared `RxArr` (HoL-blocks when slow core holds `head`!)
                    let mut span = None;
                    let mut spins = 0u32;
                    while !sh.quit.load(Ordering::Relaxed) && sh.active.load(Ordering::Relaxed) {
                        if let Some(sp) = shring.try_push_burst(&mut rx_b, &mut lock_wait_ticks) {
                            span = Some(sp);
                            break;
                        }
                        cnt.hol_stalls += 1;
                        spins += 1;
                        // Periodically check control commands so STOP/QUIT never deadlocks
                        if (spins & 1023) == 0 && (sh.cmd_word.load(Ordering::Acquire) >> 4) > handled_cmd_gen {
                            break;
                        }
                        std::hint::spin_loop();
                    }
                    if let Some((start, count)) = span {
                        unsafe {
                            shring.verify_slot_range(
                                start,
                                count,
                                sh.expected_payload,
                                cur_run_gen,
                                &mut tracker,
                                &mut cnt,
                            );
                        }
                        for _ in 0..count {
                            do_app_work(sh.app_work_ticks);
                        }
                        shring.complete_and_reclaim(start, count, &mut lock_wait_ticks);
                    } else {
                        rx_b.clear();
                    }
                }
                Arm::RxBisect => {
                    // Balanced fast path: lock-free direct consume & return to per-lcore LIFO `Ax` cache!
                    for m in rx_b.as_slice() {
                        verify_mbuf(m, sh.expected_payload, cur_run_gen, &mut tracker, &mut cnt);
                        do_app_work(sh.app_work_ticks);
                    }
                    rx_b.clear();
                }
                Arm::CreditSdp => {
                    for m in rx_b.as_slice() {
                        verify_mbuf(m, sh.expected_payload, cur_run_gen, &mut tracker, &mut cnt);
                        total_bytes_for_credit += m.data_len() as u64;
                        do_app_work(sh.app_work_ticks);
                    }
                    rx_b.clear();
                    if let Some(slot) = inp.bar4_slot.as_ref() {
                        if let Some(cval) = refill.poll_credit_due(sh.credit_batch, sh.align_refill) {
                            let _ = publish_bar4_credit(slot, q, cval, cur_run_gen, total_bytes_for_credit);
                            cnt.credit_writes += 1;
                        }
                    }
                }
                Arm::KvSolo => {
                    rx_b.clear();
                }
            }
        }
        bx_ring.clear();
        rx_b.clear();
    }

    fn eth_stats_delta(cur: EthStats, base: EthStats) -> EthStats {
        EthStats {
            ipackets: cur.ipackets.saturating_sub(base.ipackets),
            opackets: cur.opackets.saturating_sub(base.opackets),
            ibytes: cur.ibytes.saturating_sub(base.ibytes),
            obytes: cur.obytes.saturating_sub(base.obytes),
            imissed: cur.imissed.saturating_sub(base.imissed),
            ierrors: cur.ierrors.saturating_sub(base.ierrors),
            oerrors: cur.oerrors.saturating_sub(base.oerrors),
            rx_nombuf: cur.rx_nombuf.saturating_sub(base.rx_nombuf),
        }
    }

    fn eth_stats_json(s: EthStats) -> String {
        format!(
            "{{\"ipackets\":{},\"opackets\":{},\"ibytes\":{},\"obytes\":{},\"imissed\":{},\"ierrors\":{},\"oerrors\":{},\"rx_nombuf\":{}}}",
            s.ipackets, s.opackets, s.ibytes, s.obytes, s.imissed, s.ierrors, s.oerrors, s.rx_nombuf
        )
    }

    fn run_kv_solo(args: &Args, tsc_hz: f64) -> Result<(), Box<dyn Error>> {
        if args.kv_bytes == 0 || !args.kv_bytes.is_power_of_two() {
            return Err("kv-solo requires power-of-two --kv-bytes > 0".into());
        }
        let kv_buf = Arc::new(NumaHugeBuf::alloc_node1(args.kv_bytes)?);
        init_kv_table(&kv_buf, args.kv_bytes);
        unsafe { let _ = libc::mlockall(libc::MCL_CURRENT | libc::MCL_FUTURE); }
        let kv_sh = Arc::new(KvShared::new());
        let pmu = HostPmu::open(&[], Some(args.kv_cpu))?;
        let b_clone = Arc::clone(&kv_buf);
        let s_clone = Arc::clone(&kv_sh);
        let kv_bytes = args.kv_bytes;
        let kv_cpu = args.kv_cpu;
        let handle = thread::spawn(move || run_kv_worker(b_clone, kv_bytes, kv_cpu, tsc_hz, s_clone));
        thread::sleep(Duration::from_millis(args.solo_warmup_ms));
        for _ in 0..50 {
            let (mg, mesh) = pmu.measure_min_ghz(100);
            if mg >= 3.80 && mesh >= 2.18 {
                break;
            }
        }
        kv_sh.reset_window();
        let s0 = pmu.snap();
        thread::sleep(Duration::from_millis(args.solo_ms));
        let s1 = pmu.snap();
        let rep = kv_sh.snapshot();
        kv_sh.phase.store(KV_PHASE_STOP, Ordering::Release);
        let _ = handle.join();
        println!(
            "MRV210_KV_SOLO {{\"arm\":\"kv-solo\",\"kv_bytes\":{},\"kv_cpu\":{},\"kv\":{},\"host_pmu\":{}}}",
            args.kv_bytes,
            args.kv_cpu,
            rep.to_json(),
            HostPmu::delta_json(&s0, &s1)
        );
        Ok(())
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        // Bind all process allocations (stacks, heap, histograms, hugepages) to NUMA Node 1
        // so Linux `kernel.numa_balancing=1` never triggers spurious PROT_NONE hint page faults.
        let nodemask: [u64; 1] = [1u64 << 1];
        unsafe {
            let _ = libc::syscall(libc::SYS_set_mempolicy, MPOL_BIND, nodemask.as_ptr(), 64usize);
        }
        let (eal, rest) = Eal::init(std::env::args())?;
        let args = Args::parse_from(rest);
        let tsc_hz = eal.tsc_hz() as f64;
        if args.arm == Arm::KvSolo {
            return run_kv_solo(&args, tsc_hz);
        }

        let n_queues = args.queues.clamp(1, MAX_QUEUES);
        let total_lcores = eal.lcores();
        if total_lcores != n_queues + 1 {
            return Err(format!(
                "need 1 main lcore + {n_queues} worker lcores (got {total_lcores})"
            )
            .into());
        }
        let rx_cpus: Vec<i32> = args
            .rx_cpus
            .split(',')
            .filter_map(|s| s.trim().parse::<i32>().ok())
            .take(n_queues)
            .collect();
        if rx_cpus.len() != n_queues {
            return Err(format!("--rx-cpus must list at least {n_queues} CPUs").into());
        }

        if args.arm == Arm::CreditSdp && !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }

        let socket = Port::socket_of(&eal, PORT);
        let data_room = 2048 + 128;

        // Allocate mempools per architectural arm:
        // - `priv-ring` / `credit-sdp`: per-queue dedicated mempools (Pitfall 2: tight hot pool for credit-sdp!)
        // - `sh-ring` / `rx-bisect`: single shared host mempool (small for sh-ring, large+LIFO for rx-bisect)
        let mut owned_pools: Vec<Mempool<'_>> = Vec::new();
        match args.arm {
            Arm::PrivRing | Arm::CreditSdp => {
                for q in 0..n_queues {
                    owned_pools.push(Mempool::new_with_cache(
                        &eal,
                        &format!("mrv210_hq{q}"),
                        args.pool_size,
                        args.pool_cache,
                        data_room,
                        socket,
                    )?);
                }
            }
            Arm::ShRing => {
                owned_pools.push(Mempool::new_with_cache(
                    &eal,
                    "mrv210_hshared",
                    args.pool_size,
                    args.pool_cache,
                    data_room,
                    socket,
                )?);
            }
            Arm::RxBisect => {
                // Steel-manned OSDI'25 rxBisect: lock-free LIFO hot allocation pool `Ax` (`lf_stack`)
                owned_pools.push(Mempool::new_with_opts(
                    &eal,
                    "mrv210_hshared",
                    args.pool_size,
                    args.pool_cache,
                    data_room,
                    socket,
                    Some("lf_stack"),
                )?);
            }
            Arm::KvSolo => unreachable!(),
        }
        let pool_refs: Vec<&Mempool<'_>> = match args.arm {
            Arm::PrivRing | Arm::CreditSdp => owned_pools.iter().collect(),
            Arm::ShRing | Arm::RxBisect => vec![&owned_pools[0]; n_queues],
            Arm::KvSolo => unreachable!(),
        };

        let (mut port, _) = Port::start_fast(
            &eal,
            PORT,
            &pool_refs,
            n_queues as u16,
            n_queues as u16,
            args.rx_desc,
            args.tx_desc,
            None,
            false,
            false,
            false,
        )?;
        let _ = port.stats_reset();
        let (eth_stats_h, rx_qs, _tx_qs) = port.split_rx_tx_queues();

        // Acquire MRV-26 BAR4 lease ONLY in `credit-sdp` mode
        let lease_opt = if args.arm == Arm::CreditSdp {
            Some(Lease::acquire(&args.card_lease)?)
        } else {
            None
        };

        let shring_opt = if args.arm == Arm::ShRing {
            Some(ShRingState::new(args.shring_cap))
        } else {
            None
        };

        // Optional concurrent Socket-1 32 MiB KV neighbor on dedicated physical core (default cpu48)
        let kv_enabled = args.kv_bytes > 0;
        let kv_sh = Arc::new(KvShared::new());
        let kv_handle = if kv_enabled {
            let kv_buf = Arc::new(NumaHugeBuf::alloc_node1(args.kv_bytes)?);
            init_kv_table(&kv_buf, args.kv_bytes);
            let s_c = Arc::clone(&kv_sh);
            let kv_bytes = args.kv_bytes;
            let kv_cpu = args.kv_cpu;
            Some(thread::spawn(move || run_kv_worker(kv_buf, kv_bytes, kv_cpu, tsc_hz, s_c)))
        } else {
            None
        };
        unsafe { let _ = libc::mlockall(libc::MCL_CURRENT | libc::MCL_FUTURE); }

        let pmu = HostPmu::open(&rx_cpus, kv_enabled.then_some(args.kv_cpu))?;

        let shared = Arc::new(Shared {
            arm: args.arm,
            rx_cpus: rx_cpus.clone(),
            expected_payload: args.expected_payload,
            run_gen: AtomicU64::new(args.run_gen),
            credit_batch: args.credit_batch.max(1),
            align_refill: args.align_refill,
            bx_cap: args.bx_cap.max(64),
            app_work_ticks: ((args.app_work_ns as f64 * 1e-9) * tsc_hz) as u64,
            stall_core: args.stall_core,
            stall_ticks: ((args.stall_us as f64 * 1e-6) * tsc_hz) as u64,
            stall_period_ticks: ((args.stall_period_ms as f64 * 1e-3) * tsc_hz) as u64,
            tsc_hz,
            active: AtomicBool::new(false),
            quit: AtomicBool::new(false),
            cmd_word: AtomicU64::new(0),
            slots: (0..n_queues).map(|_| SyncSlot::new()).collect(),
            eth_base: Mutex::new(eth_stats_h.stats().unwrap_or_default()),
            t_reset: Mutex::new(Instant::now()),
        });

        let sh_sig = Arc::clone(&shared);
        let _ = ctrlc::set_handler(move || {
            sh_sig.quit.store(true, Ordering::Release);
        });

        let ctrl_listener = TcpListener::bind(("127.0.0.1", args.ctrl_port))?;
        let _ = ctrl_listener.set_nonblocking(true);

        let mut inputs: Vec<Option<WorkerInput<'_, '_, '_>>> = Vec::with_capacity(total_lcores);
        inputs.push(None);
        for (q, rx_q) in rx_qs.into_iter().enumerate() {
            let bar4_slot = match &lease_opt {
                Some(l) => Some(l.map_slot(&args.bar)?),
                None => None,
            };
            inputs.push(Some(WorkerInput {
                q,
                rx_q,
                bar4_slot,
                shring: shring_opt.as_ref(),
            }));
        }

        let ctrl_port = args.ctrl_port;
        let arm_str = args.arm.as_str();
        let rx_desc = args.rx_desc;
        let pool_sz = args.pool_size;
        let kv_bytes_dbg = args.kv_bytes;

        eal.on_each_lcore(inputs, |lcore_idx, opt_inp| {
            if let Some(inp) = opt_inp {
                worker_main(inp, &shared);
                return;
            }
            debug_assert_eq!(lcore_idx, 0);
            let mut pmu_snap0 = pmu.snap();
            println!(
                "MRV210_HOST READY arm={arm_str} queues={n_queues} rx_desc={rx_desc} pool_size={pool_sz} kv_bytes={kv_bytes_dbg} ctrl={ctrl_port}"
            );

            while !shared.quit.load(Ordering::Relaxed) {
                match ctrl_listener.accept() {
                    Ok((mut s, _)) => {
                        let _ = s.set_nonblocking(false);
                        let mut r = BufReader::new(s.try_clone().unwrap());
                        let mut line = String::new();
                        if r.read_line(&mut line).unwrap_or(0) == 0 {
                            continue;
                        }
                        let cmd = line.trim();
                        let reply = if cmd == "PING" {
                            "{\"ok\":true}".to_string()
                        } else if cmd == "START" {
                            shared.active.store(true, Ordering::Release);
                            "{\"ok\":true}".to_string()
                        } else if cmd == "STOP" {
                            shared.active.store(false, Ordering::Release);
                            "{\"ok\":true}".to_string()
                        } else if cmd == "RESET" || cmd.starts_with("RESET ") {
                            if let Some(rest) = cmd.strip_prefix("RESET ") {
                                for tok in rest.split_whitespace() {
                                    if let Some((k, v)) = tok.split_once('=') {
                                        if k == "run_gen" {
                                            if let Ok(rg) = v.parse::<u64>() {
                                                shared.run_gen.store(rg, Ordering::Release);
                                            }
                                        }
                                    }
                                }
                            }
                            if let Some(sr) = &shring_opt {
                                sr.force_clear();
                            }
                            let _ = shared.broadcast_cmd(1);
                            sfence();
                            *shared.eth_base.lock().unwrap() = eth_stats_h.stats().unwrap_or_default();
                            *shared.t_reset.lock().unwrap() = Instant::now();
                            if kv_enabled {
                                kv_sh.reset_window();
                            }
                            pmu_snap0 = pmu.snap();
                            "{\"ok\":true}".to_string()
                        } else if cmd == "WAIT_TURBO" || cmd.starts_with("WAIT_TURBO ") {
                            let target = cmd
                                .strip_prefix("WAIT_TURBO ")
                                .and_then(|s| s.trim().parse::<f64>().ok())
                                .unwrap_or(3.45);
                            let wt0 = Instant::now();
                            let mut last_mg = 0.0f64;
                            let mut last_mesh = 0.0f64;
                            for _ in 0..60 {
                                let (mg, mesh) = pmu.measure_min_ghz(100);
                                last_mg = mg;
                                last_mesh = mesh;
                                if mg >= target && mesh >= 2.18 {
                                    break;
                                }
                                if shared.quit.load(Ordering::Relaxed) {
                                    break;
                                }
                            }
                            format!(
                                "{{\"ok\":true,\"min_ghz\":{last_mg:.4},\"mesh_ghz\":{last_mesh:.4},\"waited_ms\":{}}}",
                                wt0.elapsed().as_millis()
                            )
                        } else if cmd == "PMU_START" {
                            if kv_enabled {
                                kv_sh.reset_window();
                            }
                            pmu_snap0 = pmu.snap();
                            "{\"ok\":true}".to_string()
                        } else if cmd == "STATS" {
                            let pmu_snap1 = pmu.snap();
                            let kv_json = if kv_enabled {
                                kv_sh.snapshot().to_json()
                            } else {
                                "null".to_string()
                            };
                            let cores = shared.broadcast_cmd(2);
                            let dt_s = shared.t_reset.lock().unwrap().elapsed().as_secs_f64();
                            let mut tot = CoreCnt::default();
                            let mut per_q_json = Vec::with_capacity(cores.len());
                            for (q, c) in cores.iter().enumerate() {
                                tot.rx_pkts += c.rx_pkts;
                                tot.rx_bytes += c.rx_bytes;
                                tot.bad += c.bad;
                                tot.torn += c.torn;
                                tot.stream_lost += c.stream_lost;
                                tot.stream_ooo += c.stream_ooo;
                                tot.empty_polls += c.empty_polls;
                                tot.nonempty_polls += c.nonempty_polls;
                                tot.credit_writes += c.credit_writes;
                                tot.hol_stalls += c.hol_stalls;
                                tot.lock_wait_us += c.lock_wait_us;
                                tot.bx_peak = tot.bx_peak.max(c.bx_peak);
                                tot.bx_overflow_drops += c.bx_overflow_drops;
                                tot.stalls_injected += c.stalls_injected;
                                tot.stall_drain_max_us = tot.stall_drain_max_us.max(c.stall_drain_max_us);
                                tot.page_faults += c.page_faults;
                                per_q_json.push(c.to_json(q, shared.rx_cpus[q]));
                            }
                            let eth_d = eth_stats_delta(
                                eth_stats_h.stats().unwrap_or_default(),
                                *shared.eth_base.lock().unwrap(),
                            );
                            let pmu_json = HostPmu::delta_json(&pmu_snap0, &pmu_snap1);
                            format!(
                                "{{\"ok\":true,\"arm\":\"{arm_str}\",\"queues\":{n_queues},\"rx_desc\":{rx_desc},\"pool_size\":{pool_sz},\"dt_s\":{dt_s:.6},\"rx_pkts\":{},\"rx_bytes\":{},\"bad\":{},\"torn\":{},\"stream_lost\":{},\"stream_ooo\":{},\"credit_writes\":{},\"hol_stalls\":{},\"lock_wait_us\":{:.2},\"bx_peak_max\":{},\"bx_overflow_drops\":{},\"stalls_injected\":{},\"stall_drain_max_us\":{:.2},\"page_faults\":{},\"eth\":{},\"per_q\":[{}],\"kv\":{kv_json},\"host_pmu\":{pmu_json}}}",
                                tot.rx_pkts,
                                tot.rx_bytes,
                                tot.bad,
                                tot.torn,
                                tot.stream_lost,
                                tot.stream_ooo,
                                tot.credit_writes,
                                tot.hol_stalls,
                                tot.lock_wait_us,
                                tot.bx_peak,
                                tot.bx_overflow_drops,
                                tot.stalls_injected,
                                tot.stall_drain_max_us,
                                tot.page_faults,
                                eth_stats_json(eth_d),
                                per_q_json.join(",")
                            )
                        } else if cmd == "QUIT" {
                            shared.active.store(false, Ordering::Release);
                            shared.quit.store(true, Ordering::Release);
                            "{\"ok\":true}".to_string()
                        } else {
                            "{\"ok\":false,\"err\":\"unknown_cmd\"}".to_string()
                        };
                        let _ = writeln!(s, "{reply}");
                        let _ = s.flush();
                    }
                    Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_micros(200));
                    }
                    Err(_) => {
                        thread::sleep(Duration::from_millis(1));
                    }
                }
            }
        });

        if let Some(h) = kv_handle {
            kv_sh.phase.store(KV_PHASE_STOP, Ordering::Release);
            let _ = h.join();
        }
        if let Some(sr) = &shring_opt {
            sr.force_clear();
        }
        Ok(())
    }
}
