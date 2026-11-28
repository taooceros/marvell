//! Host-side real-time BAR4 64 B write-reordering experiment driver for MRV-190.
//!
//! Arms:
//! - `dsa-single`         (Group 1): single DSA `MEMMOVE` (`128 B..4 KiB`) into BAR4+0;
//!   card watches tail line (`N-1`), snapshots `0..N-2`.
//! - `dsa-dual-batch`     (Group 2a): DSA `Batch(2)` = `[4 KiB payload -> BAR4+0, 64 B doorbell -> BAR4+4096]`,
//!   single-variable toggle `--dsa-fence` (`IDXD_OP_FLAG_FENCE = 0` vs `1`) on sub-descriptor 1.
//! - `dsa-dual-b2b`       (Group 2b): two back-to-back standalone DSA `MEMMOVE` descriptors to `wq2.0`
//!   (same portal line `portal+0`), with optional `--dsa-fence` on descriptor 2 or `--dsa-drain`
//!   (`DSA_OPCODE_DRAIN`) between them.
//! - `dsa-comp-movdir-db` (Group 3): DSA `MEMMOVE` (`4 KiB` -> BAR4+0), host waits for DSA host-RAM
//!   completion record `status == 0x01`, executes `SFENCE` (and optional `--readback-fence`),
//!   then `MOVDIR64B` to independent doorbell (`BAR4+4096`).
//! - `movdir-nofence`     (Group 4): CPU `MOVDIR64B` loop (`0..N-1`) with ZERO `SFENCE`.
//! - `movdir-fenced`      (Group 4 control): CPU `MOVDIR64B` `0..N-2`, `SFENCE`, then `MOVDIR64B` tail `N-1`.
//!
//! Source cache states (`--src-cache`):
//! - `hot`: all source lines in L1D Modified (`sfence` before submit so DSA never reads stale src).
//! - `cold`: rotating 64 MiB hugepage pool + `clflushopt` on every source line (incl. doorbell) + `mfence`.
//! - `front-cold-tail-hot`: rotating 64 MiB hugepage pool + `clflushopt` on front `0..N-2` (or all payload
//!   lines in doorbell arms) + `mfence`, keeping tail/doorbell line hot in L1D + `sfence`.
//!
//! Zero-interference lockstep:
//! After each write finishes on the host (after DSA completion record in host RAM, or immediately
//! after the last `MOVDIR64B` in CPU arms), the host spins silently via plain `rdtsc` (no `lfence`/`sfence`
//! and zero BAR4 reads) for `--quiet-ns` (default 2500 ns) before reading `BAR4 + ACK_OFFSET` once.
//! Logs `ack_first_hit_pct` to prove the card completed observation & convergence before any host BAR4 read.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv190_host runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::arch::asm;
    use std::error::Error;
    use std::fs::{self, OpenOptions};
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::size_of;
    use std::net::TcpStream;
    use std::os::fd::RawFd;
    use std::os::unix::io::AsRawFd;
    use std::ptr;
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use testbed::lease::{self, Lease};
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};

    const DB_OFFSET: usize = 4096;
    const ACK_OFFSET: usize = 8192;
    const SRC_POOL_BYTES: usize = 64 << 20; // 64 MiB (> 60 MiB L3) = 32 x 2 MiB hugepages
    const SRC_STRIDE: usize = 4096;
    const SRC_SLOTS: usize = SRC_POOL_BYTES / SRC_STRIDE; // 16384 slots
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const REF_HZ: f64 = 2.2e9;

    const LINE_MAGIC: u64 = 0xA55A_1900_0000_0000;
    const DB_MAGIC: u64 = 0xDB19_0000_0000_0000;

    const DSA_OPCODE_BATCH: u8 = 0x01;
    const DSA_OPCODE_DRAIN: u8 = 0x02;
    const DSA_OPCODE_MEMMOVE: u8 = 0x03;

    const IDXD_OP_FLAG_FENCE: u32 = 0x0001;
    const IDXD_OP_FLAG_CRAV: u32 = 0x0004;
    const IDXD_OP_FLAG_RCR: u32 = 0x0008;
    const IDXD_OP_FLAG_CC: u32 = 0x0100;

    #[inline(always)]
    fn line_sig(gen_val: u64, line_idx: usize) -> u64 {
        gen_val ^ LINE_MAGIC ^ ((line_idx as u64) << 40)
    }

    #[inline(always)]
    fn db_sig(gen_val: u64) -> u64 {
        gen_val ^ DB_MAGIC
    }

    #[inline(always)]
    unsafe fn clflushopt(p: *const u8) {
        unsafe { asm!("clflushopt [{p}]", p = in(reg) p, options(nostack, preserves_flags)) };
    }

    #[inline(always)]
    unsafe fn clwb(p: *const u8) {
        unsafe { asm!("clwb [{p}]", p = in(reg) p, options(nostack, preserves_flags)) };
    }

    #[inline(always)]
    fn mfence() {
        unsafe { asm!("mfence", options(nostack, preserves_flags)) };
    }

    /// Plain non-serializing RDTSC (must NOT serialize stores/WC buffers during `movdir-nofence` quiet spin).
    #[inline(always)]
    fn rdtsc_raw() -> u64 {
        let lo: u32;
        let hi: u32;
        unsafe {
            asm!("rdtsc", out("eax") lo, out("edx") hi, options(nostack, preserves_flags, nomem));
        }
        ((hi as u64) << 32) | (lo as u64)
    }

    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct DsaDesc {
        words: [u64; 8],
    }

    impl DsaDesc {
        fn memmove(comp_addr: u64, src_addr: u64, dst_addr: u64, xfer_size: u32, flags_extra: u32, rcr: bool) -> Self {
            let flags = (if rcr { IDXD_OP_FLAG_CRAV | IDXD_OP_FLAG_RCR } else { 0 }) | flags_extra;
            let w0 = (u64::from(flags) << 32) | (u64::from(DSA_OPCODE_MEMMOVE) << 56);
            Self {
                words: [w0, if rcr { comp_addr } else { 0 }, src_addr, dst_addr, u64::from(xfer_size), 0, 0, 0],
            }
        }

        fn batch(comp_addr: u64, desc_list_addr: u64, desc_count: u32) -> Self {
            let flags = IDXD_OP_FLAG_CRAV | IDXD_OP_FLAG_RCR;
            let w0 = (u64::from(flags) << 32) | (u64::from(DSA_OPCODE_BATCH) << 56);
            Self { words: [w0, comp_addr, desc_list_addr, 0, u64::from(desc_count), 0, 0, 0] }
        }

        fn drain(comp_addr: u64, rcr: bool) -> Self {
            let flags = if rcr { IDXD_OP_FLAG_CRAV | IDXD_OP_FLAG_RCR } else { 0 };
            let w0 = (u64::from(flags) << 32) | (u64::from(DSA_OPCODE_DRAIN) << 56);
            Self { words: [w0, if rcr { comp_addr } else { 0 }, 0, 0, 0, 0, 0, 0] }
        }
    }

    #[repr(C, align(32))]
    struct DsaComp {
        status: u8,
        result: u8,
        rsvd: u16,
        bytes_completed: u32,
        fault_addr: u64,
        pad: [u64; 2],
    }

    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct Line64 {
        words: [u64; 8],
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Arm {
        DsaSingle,
        DsaDualBatch,
        DsaDualB2b,
        DsaCompMovdirDb,
        MovdirNofence,
        MovdirFenced,
        PositiveCtrlRev,
    }

    impl Arm {
        fn as_str(self) -> &'static str {
            match self {
                Arm::DsaSingle => "dsa_single",
                Arm::DsaDualBatch => "dsa_dual_batch",
                Arm::DsaDualB2b => "dsa_dual_b2b",
                Arm::DsaCompMovdirDb => "dsa_comp_movdir_db",
                Arm::MovdirNofence => "movdir_nofence",
                Arm::MovdirFenced => "movdir_fenced",
                Arm::PositiveCtrlRev => "positive_ctrl_rev",
            }
        }
        fn uses_dsa(self) -> bool {
            !matches!(self, Arm::MovdirNofence | Arm::MovdirFenced | Arm::PositiveCtrlRev)
        }
        fn is_doorbell(self) -> bool {
            matches!(self, Arm::DsaDualBatch | Arm::DsaDualB2b | Arm::DsaCompMovdirDb)
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum SrcCache {
        Hot,
        HotClean,
        Cold,
        FrontColdTailHot,
        CrossNumaFrontColdTailHot,
    }

    impl SrcCache {
        fn as_str(self) -> &'static str {
            match self {
                SrcCache::Hot => "hot",
                SrcCache::HotClean => "hot_clean",
                SrcCache::Cold => "cold",
                SrcCache::FrontColdTailHot => "front_cold_tail_hot",
                SrcCache::CrossNumaFrontColdTailHot => "cross_numa_front_cold_tail_hot",
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum ScanDir {
        Forward,
        Reverse,
    }

    impl ScanDir {
        fn as_str(self) -> &'static str {
            match self {
                ScanDir::Forward => "forward",
                ScanDir::Reverse => "reverse",
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum)]
        arm: Arm,
        #[arg(long, value_enum, default_value_t = SrcCache::Hot)]
        src_cache: SrcCache,
        #[arg(long, value_enum, default_value_t = ScanDir::Forward)]
        scan_dir: ScanDir,
        /// Payload size in bytes (128, 256, 512, 1024, 4096).
        #[arg(long)]
        size: usize,
        /// Number of monitored writes per run.
        #[arg(long, default_value_t = 30000)]
        rounds: u64,
        /// Starting generation ID (unique per run).
        #[arg(long, default_value_t = 1)]
        base_gen: u64,
        /// Set IDXD_OP_FLAG_FENCE on the 2nd (doorbell) descriptor in dual-descriptor arms.
        #[arg(long)]
        dsa_fence: bool,
        /// Insert a DSA_OPCODE_DRAIN descriptor between payload and doorbell in dual-descriptor arms.
        #[arg(long)]
        dsa_drain: bool,
        /// Enable IDXD_OP_FLAG_CC on DSA descriptors (default false).
        #[arg(long)]
        cc: bool,
        /// In `dsa-comp-movdir-db`, perform an 8 B BAR4 readback before ringing the doorbell.
        #[arg(long)]
        readback_fence: bool,
        /// Silent CPU spin (ns, plain rdtsc, zero BAR4 reads) before checking card ACK.
        #[arg(long, default_value_t = 2500)]
        quiet_ns: u64,
        #[arg(long, default_value_t = 40)]
        cpu: usize,
        /// Minimum core GHz warmup target (0 to skip).
        #[arg(long, default_value_t = 3.85)]
        target_ghz: f64,
        /// DSA WQ character device.
        #[arg(long, default_value = "/dev/dsa/wq2.0")]
        wq: String,
        /// DSA BAR2 sysfs resource file (portal fallback).
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:f2:01.0/resource2")]
        resource2: String,
        /// DSA errors sysfs path.
        #[arg(long, default_value = "/sys/bus/dsa/devices/dsa2/errors")]
        errors_path: String,
        /// CN106 BAR4 sysfs file (`resource4` or `resource4_wc`).
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar4: String,
        /// Card MRV-26 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card_lease: String,
        /// Card watch/PMU TCP address.
        #[arg(long, default_value = "10.10.0.2:7527")]
        card_sync: String,
    }

    fn pin_cpu(cpu: usize) -> io::Result<()> {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu, &mut set);
            if libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set) != 0 {
                return Err(io::Error::last_os_error());
            }
        }
        Ok(())
    }

    fn read_trim(path: &str) -> String {
        fs::read_to_string(path).map(|s| s.trim().to_string()).unwrap_or_else(|_| "na".into())
    }

    fn active_dsa_engines() -> usize {
        (0..4)
            .filter(|i| read_trim(&format!("/sys/bus/dsa/devices/engine2.{i}/group_id")) == "0")
            .count()
    }

    // ---- 100% non-multiplexed Host perf_event_open ----
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
    const PERF_COUNT_HW_CPU_CYCLES: u64 = 0;
    const PERF_COUNT_HW_REF_CPU_CYCLES: u64 = 9;
    const PERF_COUNT_SW_PAGE_FAULTS: u64 = 2;
    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    const fn iio_cfg(event: u8, umask: u8, ch_mask: u64, fc_mask: u64) -> u64 {
        (event as u64) | ((umask as u64) << 8) | (ch_mask << 36) | (fc_mask << 48)
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
        fn open(type_: u32, config: u64, pid: i32, cpu: i32) -> io::Result<Self> {
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
                    pid as libc::pid_t,
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

    fn read_pmu_type(name: &str) -> io::Result<u32> {
        fs::read_to_string(format!("/sys/bus/event_source/devices/{name}/type"))?
            .trim()
            .parse()
            .map_err(io::Error::other)
    }

    struct HostPmu {
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        page_faults: PerfCounter,
        iio1_out_tlp: PerfCounter,
        iio1_out_cl: PerfCounter,
        iio1_wr_txn: PerfCounter,
        iio1_wr_dw: PerfCounter,
        iio1_ioclk: PerfCounter,
        iio0_p2p_txn: PerfCounter,
        iio0_p2p_dw: PerfCounter,
        iio0_loc_p2p: PerfCounter,
        iio0_mem_rd_dw: PerfCounter,
        cha0_clk: PerfCounter,
    }

    #[derive(Clone, Copy)]
    struct HostSnap {
        cycles: Sample,
        ref_cycles: Sample,
        page_faults: Sample,
        iio1_out_tlp: Sample,
        iio1_out_cl: Sample,
        iio1_wr_txn: Sample,
        iio1_wr_dw: Sample,
        iio1_ioclk: Sample,
        iio0_p2p_txn: Sample,
        iio0_p2p_dw: Sample,
        iio0_loc_p2p: Sample,
        iio0_mem_rd_dw: Sample,
        cha0_clk: Sample,
    }

    impl HostPmu {
        fn open(cpu_wr_mode: bool) -> io::Result<Self> {
            let iio1_t = read_pmu_type("uncore_iio_1")?;
            let iio1_fr_t = read_pmu_type("uncore_iio_free_running_1")?;
            let iio0_t = read_pmu_type("uncore_iio_0")?;
            let cha0_t = read_pmu_type("uncore_cha_0")?;
            let wr_umask = if cpu_wr_mode { 0x01 } else { 0x02 };
            Ok(Self {
                cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, 0, -1)?,
                ref_cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, 0, -1)?,
                page_faults: PerfCounter::open(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_PAGE_FAULTS, 0, -1)?,
                iio1_out_tlp: PerfCounter::open(iio1_t, iio_cfg(0xd1, 0x08, 0xff, 7), -1, SOCKET1_UNCORE_CPU)?,
                iio1_out_cl: PerfCounter::open(iio1_t, iio_cfg(0xd0, 0x08, 0xff, 7), -1, SOCKET1_UNCORE_CPU)?,
                iio1_wr_txn: PerfCounter::open(iio1_t, iio_cfg(0xc1, wr_umask, 0xff, 7), -1, SOCKET1_UNCORE_CPU)?,
                iio1_wr_dw: PerfCounter::open(iio1_t, iio_cfg(0xc0, wr_umask, 0xff, 7), -1, SOCKET1_UNCORE_CPU)?,
                iio1_ioclk: PerfCounter::open(iio1_fr_t, 0x10ff, -1, SOCKET1_UNCORE_CPU)?,
                iio0_p2p_txn: PerfCounter::open(iio0_t, iio_cfg(0x84, 0x02, 0xff, 7), -1, SOCKET1_UNCORE_CPU)?,
                iio0_p2p_dw: PerfCounter::open(iio0_t, iio_cfg(0x83, 0x02, 0xff, 7), -1, SOCKET1_UNCORE_CPU)?,
                iio0_loc_p2p: PerfCounter::open(iio0_t, iio_cfg(0x8e, 0x20, 0xff, 7), -1, SOCKET1_UNCORE_CPU)?,
                iio0_mem_rd_dw: PerfCounter::open(iio0_t, iio_cfg(0x83, 0x04, 0xff, 7), -1, SOCKET1_UNCORE_CPU)?,
                cha0_clk: PerfCounter::open(cha0_t, 0x01, -1, SOCKET1_UNCORE_CPU)?,
            })
        }

        fn snap(&self) -> HostSnap {
            HostSnap {
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                page_faults: self.page_faults.read(),
                iio1_out_tlp: self.iio1_out_tlp.read(),
                iio1_out_cl: self.iio1_out_cl.read(),
                iio1_wr_txn: self.iio1_wr_txn.read(),
                iio1_wr_dw: self.iio1_wr_dw.read(),
                iio1_ioclk: self.iio1_ioclk.read(),
                iio0_p2p_txn: self.iio0_p2p_txn.read(),
                iio0_p2p_dw: self.iio0_p2p_dw.read(),
                iio0_loc_p2p: self.iio0_loc_p2p.read(),
                iio0_mem_rd_dw: self.iio0_mem_rd_dw.read(),
                cha0_clk: self.cha0_clk.read(),
            }
        }

        fn warmup_ghz(&self, target_ghz: f64) -> (f64, u64) {
            if target_ghz <= 0.0 {
                return (0.0, 0);
            }
            let t0 = Instant::now();
            let mut ghz = 0.0f64;
            for _ in 0..40 {
                let c0 = self.cycles.read();
                let r0 = self.ref_cycles.read();
                let mut acc = 1u64;
                for i in 0..1_500_000u64 {
                    acc = acc.wrapping_mul(6364136223846793005).wrapping_add(i | 1);
                }
                std::hint::black_box(acc);
                let c1 = self.cycles.read();
                let r1 = self.ref_cycles.read();
                let dc = c1.delta(c0).val;
                let dr = r1.delta(r0).val;
                if dr > 0 {
                    ghz = (dc as f64 / dr as f64) * (REF_HZ / 1e9);
                    if ghz >= target_ghz {
                        break;
                    }
                }
            }
            (ghz, t0.elapsed().as_millis() as u64)
        }
    }

    struct HostBuf {
        ptr: *mut u8,
        len: usize,
        hugetlb: bool,
    }

    impl HostBuf {
        fn alloc(len: usize) -> io::Result<Self> {
            let flags_huge = libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_HUGETLB | libc::MAP_POPULATE;
            let p = unsafe { libc::mmap(ptr::null_mut(), len, libc::PROT_READ | libc::PROT_WRITE, flags_huge, -1, 0) };
            if p != libc::MAP_FAILED {
                unsafe { ptr::write_bytes(p.cast::<u8>(), 0, len) };
                return Ok(Self { ptr: p.cast(), len, hugetlb: true });
            }
            let flags = libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_POPULATE;
            let p = unsafe { libc::mmap(ptr::null_mut(), len, libc::PROT_READ | libc::PROT_WRITE, flags, -1, 0) };
            if p == libc::MAP_FAILED {
                return Err(io::Error::last_os_error());
            }
            unsafe {
                libc::madvise(p, len, libc::MADV_HUGEPAGE);
                ptr::write_bytes(p.cast::<u8>(), 0, len);
            }
            Ok(Self { ptr: p.cast(), len, hugetlb: false })
        }
    }

    struct CrossNumaBuf {
        ptr: *mut u8,
        len: usize,
        node0_ok: bool,
    }

    impl CrossNumaBuf {
        /// Allocates a contiguous 4 MiB buffer (two 2 MiB regions):
        /// `[0..2 MiB)` strictly bound to NUMA node 0 (Socket 0 DDR5),
        /// `[2 MiB..4 MiB)` strictly bound to NUMA node 1 (Socket 1 local).
        fn alloc() -> io::Result<Self> {
            const HALF: usize = 2 << 20;
            const TOTAL: usize = 4 << 20;
            const MPOL_BIND: libc::c_int = 2;
            const MPOL_MF_STRICT: libc::c_uint = 1 << 0;
            const MPOL_MF_MOVE: libc::c_uint = 1 << 1;
            let flags = libc::MAP_PRIVATE | libc::MAP_ANONYMOUS;
            let p = unsafe { libc::mmap(ptr::null_mut(), TOTAL, libc::PROT_READ | libc::PROT_WRITE, flags, -1, 0) };
            if p == libc::MAP_FAILED {
                return Err(io::Error::last_os_error());
            }
            let mask0: libc::c_ulong = 1 << 0;
            let mask1: libc::c_ulong = 1 << 1;
            let rc0 = unsafe {
                libc::syscall(
                    libc::SYS_mbind,
                    p,
                    HALF,
                    MPOL_BIND,
                    &mask0 as *const libc::c_ulong,
                    64usize,
                    MPOL_MF_STRICT | MPOL_MF_MOVE,
                )
            };
            let rc1 = unsafe {
                libc::syscall(
                    libc::SYS_mbind,
                    (p as *mut u8).add(HALF),
                    HALF,
                    MPOL_BIND,
                    &mask1 as *const libc::c_ulong,
                    64usize,
                    MPOL_MF_STRICT | MPOL_MF_MOVE,
                )
            };
            unsafe { ptr::write_bytes(p.cast::<u8>(), 0, TOTAL) };
            Ok(Self { ptr: p.cast(), len: TOTAL, node0_ok: rc0 == 0 && rc1 == 0 })
        }
    }

    impl Drop for CrossNumaBuf {
        fn drop(&mut self) {
            unsafe { libc::munmap(self.ptr.cast(), self.len) };
        }
    }

    impl Drop for HostBuf {
        fn drop(&mut self) {
            unsafe { libc::munmap(self.ptr.cast(), self.len) };
        }
    }

    struct CardClient {
        stream: TcpStream,
        reader: BufReader<TcpStream>,
    }

    impl CardClient {
        fn connect(addr: &str) -> Result<Self, Box<dyn Error>> {
            let stream = TcpStream::connect_timeout(&addr.parse()?, Duration::from_secs(2))?;
            stream.set_nodelay(true)?;
            let r = stream.try_clone()?;
            Ok(Self { stream, reader: BufReader::new(r) })
        }

        fn send_line(&mut self, line: &str) -> Result<(), Box<dyn Error>> {
            writeln!(self.stream, "{line}")?;
            self.stream.flush()?;
            Ok(())
        }

        fn recv_line(&mut self) -> Result<String, Box<dyn Error>> {
            let mut resp = String::new();
            let n = self.reader.read_line(&mut resp)?;
            if n == 0 {
                return Err("card closed sync connection".into());
            }
            Ok(resp.trim().to_string())
        }
    }

    fn poll_comp(comp_ptr: *mut u8, timeout: Duration) -> (u8, u8, u32, u64, bool) {
        let cp = comp_ptr.cast::<DsaComp>();
        let mut spins = 0u32;
        let mut dl: Option<Instant> = None;
        loop {
            let st = unsafe { ptr::read_volatile(&(*cp).status) };
            if st != 0 {
                let res = unsafe { ptr::read_volatile(&(*cp).result) };
                let bytes = unsafe { ptr::read_volatile(&(*cp).bytes_completed) };
                let fault = unsafe { ptr::read_volatile(&(*cp).fault_addr) };
                return (st, res, bytes, fault, false);
            }
            spins = spins.wrapping_add(1);
            if (spins & 0x3FFF) == 0 {
                let d = *dl.get_or_insert_with(|| Instant::now() + timeout);
                if Instant::now() >= d {
                    return (0, 0, 0, 0, true);
                }
            }
            std::hint::spin_loop();
        }
    }

    fn percentile(sorted: &[f64], q: f64) -> f64 {
        if sorted.is_empty() {
            return 0.0;
        }
        let idx = ((sorted.len() - 1) as f64 * q).round() as usize;
        sorted[idx.min(sorted.len() - 1)]
    }

    fn format_host_pmu_json(s0: &HostSnap, s1: &HostSnap, wall_s: f64) -> (String, f64, f64, u64) {
        let mut min_pct = 100.0f64;
        let mut d = |a: Sample, b: Sample| -> u64 {
            let x = b.delta(a);
            min_pct = min_pct.min(x.pct());
            x.val
        };
        let cycles = d(s0.cycles, s1.cycles);
        let ref_cycles = d(s0.ref_cycles, s1.ref_cycles);
        let page_faults = d(s0.page_faults, s1.page_faults);
        let iio1_out_tlp = d(s0.iio1_out_tlp, s1.iio1_out_tlp);
        let iio1_out_cl = d(s0.iio1_out_cl, s1.iio1_out_cl);
        let iio1_wr_txn = d(s0.iio1_wr_txn, s1.iio1_wr_txn);
        let iio1_wr_dw = d(s0.iio1_wr_dw, s1.iio1_wr_dw);
        let iio1_ioclk = d(s0.iio1_ioclk, s1.iio1_ioclk);
        let iio0_p2p_txn = d(s0.iio0_p2p_txn, s1.iio0_p2p_txn);
        let iio0_p2p_dw = d(s0.iio0_p2p_dw, s1.iio0_p2p_dw);
        let iio0_loc_p2p = d(s0.iio0_loc_p2p, s1.iio0_loc_p2p);
        let iio0_mem_rd_dw = d(s0.iio0_mem_rd_dw, s1.iio0_mem_rd_dw);
        let cha0_clk = d(s0.cha0_clk, s1.cha0_clk);

        let cpu_ghz = if ref_cycles > 0 {
            (cycles as f64 / ref_cycles as f64) * (REF_HZ / 1e9)
        } else {
            0.0
        };
        let mesh_ghz = if wall_s > 0.0 { (cha0_clk as f64) / wall_s / 1e9 } else { 0.0 };
        let iio1_ghz = if wall_s > 0.0 { (iio1_ioclk as f64) / wall_s / 1e9 } else { 0.0 };
        let bytes_per_tlp = if iio1_out_tlp > 0 { (iio1_wr_dw as f64) * 4.0 / (iio1_out_tlp as f64) } else { 0.0 };

        let json = format!(
            "{{\"cpu_ghz\":{cpu_ghz:.4},\"mesh_ghz\":{mesh_ghz:.4},\"iio1_ghz\":{iio1_ghz:.4},\"cycles\":{cycles},\"ref_cycles\":{ref_cycles},\"page_faults\":{page_faults},\
\"iio1_out_tlp\":{iio1_out_tlp},\"iio1_out_cl\":{iio1_out_cl},\"iio1_wr_txn\":{iio1_wr_txn},\"iio1_wr_dw\":{iio1_wr_dw},\"bytes_per_tlp\":{bytes_per_tlp:.2},\
\"iio0_p2p_txn\":{iio0_p2p_txn},\"iio0_p2p_dw\":{iio0_p2p_dw},\"iio0_loc_p2p\":{iio0_loc_p2p},\"iio0_mem_rd_dw\":{iio0_mem_rd_dw},\"min_pct\":{min_pct:.2}}}"
        );
        (json, min_pct, cpu_ghz, page_faults)
    }

    #[inline(always)]
    unsafe fn fill_src_block(src_ptr: *mut u8, n_lines: usize, gen_val: u64) {
        for i in 0..n_lines {
            let lp = unsafe { src_ptr.add(i * 64).cast::<u64>() };
            let sig = line_sig(gen_val, i);
            unsafe {
                ptr::write(lp, gen_val);
                ptr::write(lp.add(1), sig);
                ptr::write(lp.add(2), gen_val ^ 0x2000_0000_0000_0000 ^ (i as u64));
                ptr::write(lp.add(3), gen_val ^ 0x3000_0000_0000_0000 ^ (i as u64));
                ptr::write(lp.add(4), gen_val ^ 0x4000_0000_0000_0000 ^ (i as u64));
                ptr::write(lp.add(5), gen_val ^ 0x5000_0000_0000_0000 ^ (i as u64));
                ptr::write(lp.add(6), gen_val ^ 0x6000_0000_0000_0000 ^ (i as u64));
                ptr::write(lp.add(7), gen_val);
            }
        }
    }

    #[inline(always)]
    unsafe fn fill_db_line(db_ptr: *mut u8, gen_val: u64) {
        let lp = db_ptr.cast::<u64>();
        let sig = db_sig(gen_val);
        unsafe {
            ptr::write(lp, gen_val);
            ptr::write(lp.add(1), sig);
            ptr::write(lp.add(2), gen_val ^ 0x2222_0000_0000_0000);
            ptr::write(lp.add(3), gen_val ^ 0x3333_0000_0000_0000);
            ptr::write(lp.add(4), gen_val ^ 0x4444_0000_0000_0000);
            ptr::write(lp.add(5), gen_val ^ 0x5555_0000_0000_0000);
            ptr::write(lp.add(6), gen_val ^ 0x6666_0000_0000_0000);
            ptr::write(lp.add(7), gen_val);
        }
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let args = Args::parse();
        if !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }
        if args.size < 128 || args.size > 4096 || !args.size.is_multiple_of(64) {
            return Err(format!("size must be a 64 B multiple in 128..=4096, got {}", args.size).into());
        }
        if args.rounds == 0 {
            return Err("rounds must be >= 1".into());
        }
        pin_cpu(args.cpu)?;

        let n_lines = args.size / 64;
        let pmu = HostPmu::open(!args.arm.uses_dsa())?;
        let (warm_ghz, warm_ms) = pmu.warmup_ghz(args.target_ghz);

        // Open DSA WQ + portal if needed.
        let mut _wq_file: Option<fs::File> = None;
        let mut _res2_file: Option<fs::File> = None;
        let mut portal_ptr: *mut u8 = ptr::null_mut();
        if args.arm.uses_dsa() {
            let wf = OpenOptions::new().read(true).write(true).open(&args.wq)?;
            let mut p = unsafe {
                libc::mmap(ptr::null_mut(), 4096, libc::PROT_WRITE, libc::MAP_SHARED, wf.as_raw_fd(), 0)
            };
            if p == libc::MAP_FAILED {
                let rf = OpenOptions::new().read(true).write(true).open(&args.resource2)?;
                p = unsafe {
                    libc::mmap(
                        ptr::null_mut(),
                        4096,
                        libc::PROT_READ | libc::PROT_WRITE,
                        libc::MAP_SHARED,
                        rf.as_raw_fd(),
                        0x1000,
                    )
                };
                if p == libc::MAP_FAILED {
                    return Err(format!("portal mmap failed: {}", io::Error::last_os_error()).into());
                }
                _res2_file = Some(rf);
            }
            portal_ptr = p.cast();
            _wq_file = Some(wf);
        }

        // Allocate prefaulted hugepage buffers:
        // - src_buf: 64 MiB (16384 x 4 KiB slots) for hot/cold/front-cold-tail-hot payload source
        // - meta_buf: 2 MiB for doorbell source, completion records, and batch sub-descriptor list
        let src_buf = HostBuf::alloc(SRC_POOL_BYTES)?;
        let xnuma_buf = if args.src_cache == SrcCache::CrossNumaFrontColdTailHot {
            Some(CrossNumaBuf::alloc()?)
        } else {
            None
        };
        let meta_buf = HostBuf::alloc(2 << 20)?;
        let db_src_hot_ptr = meta_buf.ptr; // offset 0
        let db_src_cold_base = unsafe { meta_buf.ptr.add(64 << 10) }; // offset 64 KiB, up to 16384 x 64 B = 1 MiB
        let comp0_ptr = unsafe { meta_buf.ptr.add(1280 << 10) }; // offset 1280 KiB
        let comp1_ptr = unsafe { meta_buf.ptr.add((1280 << 10) + 64) };
        let comp2_ptr = unsafe { meta_buf.ptr.add((1280 << 10) + 128) };
        let subdesc_ptr = unsafe { meta_buf.ptr.add(1536 << 10).cast::<DsaDesc>() };

        // Acquire MRV-26 BAR4 slot 15 lease & connect to card watch server.
        let lease = Lease::acquire(&args.card_lease)?;
        let mut card = CardClient::connect(&args.card_sync)?;
        let bar4_slot = lease.map_slot(&args.bar4)?;
        bar4_slot.check()?;
        let bar4_base = bar4_slot.as_ptr();
        let ack_ptr = unsafe { bar4_base.add(ACK_OFFSET).cast::<u64>() };

        let engines_active = active_dsa_engines();
        let swerr_before = read_trim(&args.errors_path);
        let bar4_kind = if args.bar4.ends_with("_wc") { "wc" } else { "uc" };
        let watch_mode = if args.arm.is_doorbell() { "doorbell" } else { "tail" };

        // Tell card to clear BAR4 buffer and start tight LDAR watch loop.
        card.send_line(&format!(
            "WATCH {} {} {} {} {}",
            args.size,
            watch_mode,
            args.scan_dir.as_str(),
            args.rounds,
            args.base_gen
        ))?;
        let ready_resp = card.recv_line()?;
        if ready_resp != "READY" {
            return Err(format!("card WATCH failed: {ready_resp}").into());
        }

        let quiet_ticks = ((args.quiet_ns as f64) * (REF_HZ / 1e9)).round() as u64;
        let cc_flag = if args.cc { IDXD_OP_FLAG_CC } else { 0 };
        let fence_flag = if args.dsa_fence { IDXD_OP_FLAG_FENCE } else { 0 };

        let mut dsa_comp_ns: Vec<f64> = if args.arm.uses_dsa() {
            // Non-zero fill forces real writable pages before h0 (avoiding glibc calloc CoW zero-page faults).
            vec![1.0; args.rounds as usize]
        } else {
            Vec::new()
        };
        let mut dsa_comp_len = 0usize;
        let mut err_ops = 0u64;
        let mut first_err_st = 0u8;
        let mut first_err_fault = 0u64;
        let mut ack_first_hit = 0u64;
        let mut ack_extra_polls = 0u64;
        let mut ack_timeouts = 0u64;
        let mut completed_ops = 0u64;
        let h0 = pmu.snap();
        let t0 = Instant::now();

        for r in 0..args.rounds {
            bar4_slot.check()?;
            let g = args.base_gen + r;
            let src_slot = match args.src_cache {
                SrcCache::Hot | SrcCache::HotClean => 0usize,
                SrcCache::Cold | SrcCache::FrontColdTailHot => (r as usize) % SRC_SLOTS,
                SrcCache::CrossNumaFrontColdTailHot => 0usize,
            };
            let src_ptr = match args.src_cache {
                SrcCache::CrossNumaFrontColdTailHot => {
                    let xb = xnuma_buf.as_ref().unwrap();
                    if args.arm.is_doorbell() {
                        unsafe { xb.ptr.add(((r as usize) % 512) * 4096) }
                    } else {
                        // Straddle the 2 MiB boundary: lines 0..N-2 in Socket 0 DDR5, tail line N-1 in Socket 1!
                        unsafe { xb.ptr.add((2 << 20) - (args.size - 64)) }
                    }
                }
                _ => unsafe { src_buf.ptr.add(src_slot * SRC_STRIDE) },
            };
            let db_ptr = match (args.arm.is_doorbell(), args.src_cache) {
                (true, SrcCache::Cold) => unsafe { db_src_cold_base.add(((r as usize) % 16384) * 64) },
                _ => db_src_hot_ptr,
            };

            // 1. Fill source lines & enforce requested cache state.
            unsafe {
                fill_src_block(src_ptr, n_lines, g);
                if args.arm.is_doorbell() {
                    fill_db_line(db_ptr, g);
                }
                match args.src_cache {
                    SrcCache::Hot => {
                        sfence();
                    }
                    SrcCache::HotClean => {
                        for i in 0..n_lines {
                            clwb(src_ptr.add(i * 64));
                        }
                        if args.arm.is_doorbell() {
                            clwb(db_ptr);
                        }
                        sfence();
                    }
                    SrcCache::Cold => {
                        for i in 0..n_lines {
                            clflushopt(src_ptr.add(i * 64));
                        }
                        if args.arm.is_doorbell() {
                            clflushopt(db_ptr);
                        }
                        mfence();
                    }
                    SrcCache::FrontColdTailHot | SrcCache::CrossNumaFrontColdTailHot => {
                        if args.arm.is_doorbell() {
                            for i in 0..n_lines {
                                clflushopt(src_ptr.add(i * 64));
                            }
                            mfence();
                            clwb(db_ptr);
                            sfence();
                        } else {
                            for i in 0..(n_lines - 1) {
                                clflushopt(src_ptr.add(i * 64));
                            }
                            mfence();
                            clwb(src_ptr.add((n_lines - 1) * 64));
                            sfence();
                        }
                    }
                }
            }

            // 2. Execute the write arm.
            match args.arm {
                Arm::DsaSingle => {
                    let desc = DsaDesc::memmove(
                        comp0_ptr as u64,
                        src_ptr as u64,
                        bar4_base as u64,
                        args.size as u32,
                        cc_flag,
                        true,
                    );
                    let c0 = rdtsc_raw();
                    unsafe {
                        ptr::write_volatile(comp0_ptr, 0);
                        sfence();
                        movdir64b(portal_ptr, &desc);
                    }
                    let (st, _, _, fault, to) = poll_comp(comp0_ptr, Duration::from_millis(500));
                    let c1 = rdtsc_raw();
                    if st != 0x01 || to {
                        err_ops += 1;
                        first_err_st = st;
                        first_err_fault = fault;
                        break;
                    }
                    dsa_comp_ns[dsa_comp_len] = (c1.wrapping_sub(c0) as f64) * 1e9 / REF_HZ;
                    dsa_comp_len += 1;
                }
                Arm::DsaDualBatch => {
                    let count = if args.dsa_drain { 3u32 } else { 2u32 };
                    unsafe {
                        ptr::write(
                            subdesc_ptr.add(0),
                            DsaDesc::memmove(0, src_ptr as u64, bar4_base as u64, args.size as u32, cc_flag, false),
                        );
                        if args.dsa_drain {
                            ptr::write(subdesc_ptr.add(1), DsaDesc::drain(0, false));
                            ptr::write(
                                subdesc_ptr.add(2),
                                DsaDesc::memmove(
                                    0,
                                    db_ptr as u64,
                                    (bar4_base as u64) + DB_OFFSET as u64,
                                    64,
                                    cc_flag | fence_flag,
                                    false,
                                ),
                            );
                        } else {
                            ptr::write(
                                subdesc_ptr.add(1),
                                DsaDesc::memmove(
                                    0,
                                    db_ptr as u64,
                                    (bar4_base as u64) + DB_OFFSET as u64,
                                    64,
                                    cc_flag | fence_flag,
                                    false,
                                ),
                            );
                        }
                        sfence();
                    }
                    let bdesc = DsaDesc::batch(comp0_ptr as u64, subdesc_ptr as u64, count);
                    let c0 = rdtsc_raw();
                    unsafe {
                        ptr::write_volatile(comp0_ptr, 0);
                        sfence();
                        movdir64b(portal_ptr, &bdesc);
                    }
                    let (st, _, _, fault, to) = poll_comp(comp0_ptr, Duration::from_millis(500));
                    let c1 = rdtsc_raw();
                    if st != 0x01 || to {
                        err_ops += 1;
                        first_err_st = st;
                        first_err_fault = fault;
                        break;
                    }
                    dsa_comp_ns[dsa_comp_len] = (c1.wrapping_sub(c0) as f64) * 1e9 / REF_HZ;
                    dsa_comp_len += 1;
                }
                Arm::DsaDualB2b => {
                    let desc0 = DsaDesc::memmove(
                        comp0_ptr as u64,
                        src_ptr as u64,
                        bar4_base as u64,
                        args.size as u32,
                        cc_flag,
                        true,
                    );
                    let desc_drain = DsaDesc::drain(comp2_ptr as u64, true);
                    let desc1 = DsaDesc::memmove(
                        comp1_ptr as u64,
                        db_ptr as u64,
                        (bar4_base as u64) + DB_OFFSET as u64,
                        64,
                        cc_flag | fence_flag,
                        true,
                    );
                    let c0 = rdtsc_raw();
                    unsafe {
                        ptr::write_volatile(comp0_ptr, 0);
                        ptr::write_volatile(comp1_ptr, 0);
                        if args.dsa_drain {
                            ptr::write_volatile(comp2_ptr, 0);
                        }
                        sfence();
                        // Submit back-to-back to the SAME portal line (portal+0) so CPU-to-WQ arrival
                        // order is preserved (0 inversions on 1 portal line), isolating DSA execution order.
                        movdir64b(portal_ptr, &desc0);
                        if args.dsa_drain {
                            movdir64b(portal_ptr, &desc_drain);
                        }
                        movdir64b(portal_ptr, &desc1);
                    }
                    let (st0, _, _, f0, to0) = poll_comp(comp0_ptr, Duration::from_millis(500));
                    let (st1, _, _, f1, to1) = poll_comp(comp1_ptr, Duration::from_millis(500));
                    let (st2, f2, to2) = if args.dsa_drain {
                        let (s, _, _, f, t) = poll_comp(comp2_ptr, Duration::from_millis(500));
                        (s, f, t)
                    } else {
                        (0x01, 0, false)
                    };
                    let c1 = rdtsc_raw();
                    if st0 != 0x01 || to0 {
                        err_ops += 1;
                        first_err_st = st0;
                        first_err_fault = f0;
                        break;
                    }
                    if st1 != 0x01 || to1 {
                        err_ops += 1;
                        first_err_st = st1;
                        first_err_fault = f1;
                        break;
                    }
                    if st2 != 0x01 || to2 {
                        err_ops += 1;
                        first_err_st = st2;
                        first_err_fault = f2;
                        break;
                    }
                    dsa_comp_ns[dsa_comp_len] = (c1.wrapping_sub(c0) as f64) * 1e9 / REF_HZ;
                    dsa_comp_len += 1;
                }
                Arm::DsaCompMovdirDb => {
                    let desc = DsaDesc::memmove(
                        comp0_ptr as u64,
                        src_ptr as u64,
                        bar4_base as u64,
                        args.size as u32,
                        cc_flag,
                        true,
                    );
                    let c0 = rdtsc_raw();
                    unsafe {
                        ptr::write_volatile(comp0_ptr, 0);
                        sfence();
                        movdir64b(portal_ptr, &desc);
                    }
                    let (st, _, _, fault, to) = poll_comp(comp0_ptr, Duration::from_millis(500));
                    let c1 = rdtsc_raw();
                    if st != 0x01 || to {
                        err_ops += 1;
                        first_err_st = st;
                        first_err_fault = fault;
                        break;
                    }
                    dsa_comp_ns[dsa_comp_len] = (c1.wrapping_sub(c0) as f64) * 1e9 / REF_HZ;
                    dsa_comp_len += 1;
                    sfence();
                    if args.readback_fence {
                        let _ = unsafe { ptr::read_volatile(bar4_base.add(args.size - 8).cast::<u64>()) };
                    }
                    unsafe {
                        movdir64b(bar4_base.add(DB_OFFSET), &*db_ptr.cast::<Line64>());
                    }
                }
                Arm::MovdirNofence => {
                    for i in 0..n_lines {
                        let lp = unsafe { &*src_ptr.add(i * 64).cast::<Line64>() };
                        unsafe { movdir64b(bar4_base.add(i * 64), lp) };
                    }
                }
                Arm::MovdirFenced => {
                    for i in 0..(n_lines - 1) {
                        let lp = unsafe { &*src_ptr.add(i * 64).cast::<Line64>() };
                        unsafe { movdir64b(bar4_base.add(i * 64), lp) };
                    }
                    sfence();
                    let lp = unsafe { &*src_ptr.add((n_lines - 1) * 64).cast::<Line64>() };
                    unsafe { movdir64b(bar4_base.add((n_lines - 1) * 64), lp) };
                }
                Arm::PositiveCtrlRev => {
                    // Positive control: intentionally write tail line N-1 FIRST, then 0..N-2.
                    let tail_lp = unsafe { &*src_ptr.add((n_lines - 1) * 64).cast::<Line64>() };
                    unsafe { movdir64b(bar4_base.add((n_lines - 1) * 64), tail_lp) };
                    sfence();
                    for i in 0..(n_lines - 1) {
                        let lp = unsafe { &*src_ptr.add(i * 64).cast::<Line64>() };
                        unsafe { movdir64b(bar4_base.add(i * 64), lp) };
                    }
                }
            }

            // 3. Silent local spin (plain rdtsc, ZERO PAUSE, ZERO BAR4 reads, ZERO fences) so the entire
            //    PCIe flight + card LDAR snapshot + convergence completes with zero interference and full 3.9 GHz.
            if quiet_ticks > 0 {
                let q0 = rdtsc_raw();
                while rdtsc_raw().wrapping_sub(q0) < quiet_ticks {}
            }

            // 4. Confirm card finished observing and converging `g` via ACK_OFFSET.
            let mut got_ack = unsafe { ptr::read_volatile(ack_ptr) };
            if got_ack == g {
                ack_first_hit += 1;
            } else {
                let dl = Instant::now() + Duration::from_secs(2);
                while got_ack != g {
                    ack_extra_polls += 1;
                    if Instant::now() >= dl {
                        ack_timeouts += 1;
                        break;
                    }
                    let w0 = rdtsc_raw();
                    let step = (500.0 * (REF_HZ / 1e9)) as u64;
                    while rdtsc_raw().wrapping_sub(w0) < step {
                        std::hint::spin_loop();
                    }
                    got_ack = unsafe { ptr::read_volatile(ack_ptr) };
                }
                if ack_timeouts > 0 {
                    break;
                }
            }

            completed_ops += 1;
        }

        let wall_s = t0.elapsed().as_secs_f64();
        let h1 = pmu.snap();
        // Receive card JSON result.
        let card_line = card.recv_line()?;
        let card_json = card_line
            .strip_prefix("RESULT ")
            .ok_or_else(|| format!("unexpected card response: {card_line}"))?;

        let swerr_after = read_trim(&args.errors_path);
        let swerr_zero = swerr_after == "00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000";
        let (host_pmu_json, host_pmu_min_pct, cpu_ghz, page_faults) = format_host_pmu_json(&h0, &h1, wall_s);

        dsa_comp_ns.truncate(dsa_comp_len);
        let dsa_comp_json = if !dsa_comp_ns.is_empty() {
            dsa_comp_ns.sort_by(|a, b| a.total_cmp(b));
            let mean = dsa_comp_ns.iter().sum::<f64>() / (dsa_comp_ns.len() as f64);
            format!(
                "{{\"n\":{},\"min\":{:.1},\"p50\":{:.1},\"p90\":{:.1},\"p99\":{:.1},\"max\":{:.1},\"mean\":{:.1}}}",
                dsa_comp_ns.len(),
                dsa_comp_ns[0],
                percentile(&dsa_comp_ns, 0.50),
                percentile(&dsa_comp_ns, 0.90),
                percentile(&dsa_comp_ns, 0.99),
                dsa_comp_ns[dsa_comp_ns.len() - 1],
                mean
            )
        } else {
            "null".to_string()
        };

        let ack_first_hit_pct = if completed_ops > 0 {
            (ack_first_hit as f64) * 100.0 / (completed_ops as f64)
        } else {
            0.0
        };

        println!(
            "{{\"arm\":\"{}\",\"src_cache\":\"{}\",\"scan_dir\":\"{}\",\"bar4_kind\":\"{bar4_kind}\",\"dsa_engines\":{engines_active},\
\"size\":{},\"n_lines\":{n_lines},\"rounds_req\":{},\"completed_ops\":{completed_ops},\"base_gen\":{},\
\"dsa_fence\":{},\"dsa_drain\":{},\"cc\":{},\"readback_fence\":{},\"quiet_ns\":{},\
\"src_hugetlb\":{},\"xnuma_ok\":{},\"cpu\":{},\"warm_ghz\":{warm_ghz:.4},\"warm_ms\":{warm_ms},\"cpu_ghz\":{cpu_ghz:.4},\"page_faults\":{page_faults},\
\"wall_s\":{wall_s:.6},\"err_ops\":{err_ops},\"first_err_st\":\"{first_err_st:#x}\",\"first_err_fault\":\"{first_err_fault:#x}\",\
\"ack_first_hit\":{ack_first_hit},\"ack_first_hit_pct\":{ack_first_hit_pct:.3},\"ack_extra_polls\":{ack_extra_polls},\"ack_timeouts\":{ack_timeouts},\
\"swerror_before\":\"{swerr_before}\",\"swerror_after\":\"{swerr_after}\",\"swerror_zero\":{swerr_zero},\
\"dsa_comp_ns\":{dsa_comp_json},\"host_pmu_min_pct\":{host_pmu_min_pct:.2},\"host_pmu\":{host_pmu_json},\"card\":{card_json}}}",
            args.arm.as_str(),
            args.src_cache.as_str(),
            args.scan_dir.as_str(),
            args.size,
            args.rounds,
            args.base_gen,
            args.dsa_fence,
            args.dsa_drain,
            args.cc,
            args.readback_fence,
            args.quiet_ns,
            src_buf.hugetlb,
            xnuma_buf.as_ref().is_some_and(|x| x.node0_ok),
            args.cpu,
        );

        drop(bar4_slot);
        drop(lease);
        if !portal_ptr.is_null() {
            unsafe { libc::munmap(portal_ptr.cast(), 4096) };
        }
        Ok(())
    }
}
