//! MRV-245 card-side benchmark (aarch64 CN106, root):
//!
//! Strictly enforces `enable_custom_mair=0` (ZERO MAIR_EL1 modification!),
//! MRV-26 BAR4 slot 15 lease (`inline::SLOT = 15`), and PEM0 outbound regions
//! strictly within `248..=255` (`/dev/pem_wc`).
//!
//! Supports:
//!   - `H in {1, 2}` disjoint host lanes inside BAR4 slot 15 (`128 B` cell stride)
//!   - `K in 1..=12` card worker cores (`WORKER_CPUS = [4..15]`)
//!   - `map_mode`: `wb_huge` (0xFF), `wb_64k` (0xFF), `normal_nc` (standard Linux 0x44)
//!   - `poll_mode`:
//!       * `none`     : card silent (host MOVDIR64B ceiling)
//!       * `u1_ldp`   : unroll=1 single-pass 64 B `4x ldp x` (MRV-235 kernel, full 64 B verify)
//!       * `u1_ldpq`  : unroll=1 single-pass 64 B `2x ldp q` (NEON sanity check)
//!       * `u4_ldpq`  : unroll=4 concurrent 4-slot `8x ldp q` (`q0..q15`, MLP=4, full 64 B verify)
//!       * `doorbell` : MRV-177 Arm B2 decoupled coalesced doorbell (`prod_idx` line + drain)
//!   - `work_mode`:
//!       * `tx_oneway`: no per-packet reply; coarse 64 B WC progress credit every `credit_step`
//!                      cells (and on busy->empty) to guarantee zero ring wrap overwrite
//!       * `closed`   : per-packet 64 B WC reply (`K <= 8`, dedicated outbound region `255 - w`)
//!   - 100.0% non-multiplexed CN106 PMUs: 8 TAD + 7 PMUv3/active-core + 6x2 DDR5 channels.

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv245_card runs on the aarch64 card");
    std::process::exit(2);
}

#[cfg(target_arch = "aarch64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "aarch64")]
mod imp {
    use std::arch::aarch64::{uint64x2_t, vgetq_lane_u64};
    use std::arch::asm;
    use std::error::Error;
    use std::fs::{self, File, OpenOptions};
    use std::io::{BufRead, BufReader, Write};
    use std::net::{TcpListener, TcpStream};
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::FileExt;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::Parser;
    use testbed::inline::{NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{
        Bar4Window, OUTBOUND_REGION_BYTES, OUTBOUND_REGIONS, OutboundWindow, restore_defaults, store64_gathered,
    };

    const HUGE_512M: usize = 512 * 1024 * 1024;
    const MAX_WORKERS: usize = 12;
    const MAX_OUTBOUND: usize = 8; // PEM0 outbound regions 255..=248 ONLY
    const REPLY_CELLS_PER_WORKER: usize = 1024;
    const CELL_STRIDE_BYTES: usize = 128;
    const WORKER_CPUS: [usize; MAX_WORKERS] = [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15];
    /// Dedicated 128 B-aligned doorbell lines inside top 64 KiB guard area, well below READY_OFFSET.
    const DOORBELL_OFFSET_BASE: usize = SLOT_BYTES - 4096;

    // -------------------------------------------------------------------------
    // `/dev/bar4_mair` UAPI (strictly `enable_custom_mair=0` in MRV-245)
    // -------------------------------------------------------------------------
    const BAR4_MAIR_MODE_NORMAL_WB: u64 = 0;
    const BAR4_MAIR_MODE_NORMAL_NC: u64 = 1;

    const fn ioc(dir: u64, typ: u8, nr: u8, size: usize) -> libc::c_ulong {
        ((dir << 30) | ((size as u64) << 16) | ((typ as u64) << 8) | (nr as u64)) as libc::c_ulong
    }
    const IOC_NONE: u64 = 0;
    const IOC_WRITE: u64 = 1;
    const IOC_READ: u64 = 2;

    #[repr(C)]
    #[derive(Default, Clone, Copy, Debug)]
    struct Bar4MairInfo {
        mair_orig: u64,
        mair_curr: u64,
        online_cpus: u32,
        all_cpus_equal: u32,
        custom_active: u32,
        custom_slot: u32,
        custom_attr: u32,
        page_size: u32,
        ttbr0_cnp: u64,
        ttbr1_cnp: u64,
    }

    #[repr(C)]
    #[derive(Default, Clone, Copy, Debug)]
    struct Bar4MairBind {
        uaddr: u64,
        len: u64,
        phys_out: u64,
    }

    #[repr(C)]
    #[derive(Default, Clone, Copy, Debug)]
    struct Bar4MairTranslate {
        uaddr: u64,
        par_el1: u64,
    }

    const BAR4_MAIR_IOC_GET_INFO: libc::c_ulong =
        ioc(IOC_READ, b'M', 1, std::mem::size_of::<Bar4MairInfo>());
    const BAR4_MAIR_IOC_BIND: libc::c_ulong =
        ioc(IOC_READ | IOC_WRITE, b'M', 2, std::mem::size_of::<Bar4MairBind>());
    const BAR4_MAIR_IOC_CLEAN_INVAL: libc::c_ulong = ioc(IOC_NONE, b'M', 3, 0);
    const BAR4_MAIR_IOC_TRANSLATE_VA: libc::c_ulong =
        ioc(IOC_READ | IOC_WRITE, b'M', 4, std::mem::size_of::<Bar4MairTranslate>());

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum MapMode {
        WbHuge = 0,
        Wb64k = 1,
        NormalNc = 2,
    }

    impl MapMode {
        fn parse(s: &str) -> Result<Self, String> {
            match s {
                "wb_huge" => Ok(Self::WbHuge),
                "wb_64k" => Ok(Self::Wb64k),
                "normal_nc" => Ok(Self::NormalNc),
                _ => Err(format!("unknown map_mode: {s}")),
            }
        }
        fn as_str(self) -> &'static str {
            match self {
                Self::WbHuge => "wb_huge",
                Self::Wb64k => "wb_64k",
                Self::NormalNc => "normal_nc",
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum PollMode {
        None = 0,
        U1Ldp = 1,
        U1Ldpq = 2,
        U4Ldpq = 3,
        Doorbell = 4,
        Adapt50 = 5,
        Adapt100 = 6,
        AdaptExp = 7,
    }

    impl PollMode {
        fn parse(s: &str) -> Result<Self, String> {
            match s {
                "none" => Ok(Self::None),
                "u1_ldp" => Ok(Self::U1Ldp),
                "u1_ldpq" | "static-u1" | "static-u1q" => Ok(Self::U1Ldpq),
                "u4_ldpq" | "static-u4" => Ok(Self::U4Ldpq),
                "doorbell" => Ok(Self::Doorbell),
                "adapt-50" | "adapt_50" => Ok(Self::Adapt50),
                "adapt-100" | "adapt_100" => Ok(Self::Adapt100),
                "adapt-exp" | "adapt_exp" => Ok(Self::AdaptExp),
                _ => Err(format!("unknown poll_mode: {s}")),
            }
        }
        fn as_str(self) -> &'static str {
            match self {
                Self::None => "none",
                Self::U1Ldp => "u1_ldp",
                Self::U1Ldpq => "u1_ldpq",
                Self::U4Ldpq => "u4_ldpq",
                Self::Doorbell => "doorbell",
                Self::Adapt50 => "adapt-50",
                Self::Adapt100 => "adapt-100",
                Self::AdaptExp => "adapt-exp",
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum WorkMode {
        TxOneway = 0,
        Closed = 1,
    }

    impl WorkMode {
        fn parse(s: &str) -> Result<Self, String> {
            match s {
                "tx_oneway" => Ok(Self::TxOneway),
                "closed" => Ok(Self::Closed),
                _ => Err(format!("unknown work_mode: {s}")),
            }
        }
        fn as_str(self) -> &'static str {
            match self {
                Self::TxOneway => "tx_oneway",
                Self::Closed => "closed",
            }
        }
    }

    #[derive(Parser, Debug)]
    struct Args {
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        #[arg(long, default_value = "10.10.0.2:7527")]
        rpc: String,
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
            for off in (0..SLOT_BYTES).step_by(65536) {
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

    struct MairMappings {
        dev: File,
        wb_huge: *mut u8,
        wb_64k: *mut u8,
        normal_nc: *mut u8,
        par_json: String,
    }
    unsafe impl Send for MairMappings {}
    unsafe impl Sync for MairMappings {}

    impl MairMappings {
        fn open_and_bind(buf: &HugeBuf) -> Result<Self, Box<dyn Error>> {
            let dev = OpenOptions::new().read(true).write(true).open("/dev/bar4_mair")
                .map_err(|e| format!("open /dev/bar4_mair failed (is bar4_mair.ko loaded?): {e}"))?;
            let fd = dev.as_raw_fd();

            let mut info = Bar4MairInfo::default();
            let rc = unsafe { libc::ioctl(fd, BAR4_MAIR_IOC_GET_INFO, &mut info as *mut Bar4MairInfo) };
            if rc != 0 {
                return Err(format!("BAR4_MAIR_IOC_GET_INFO failed: {}", std::io::Error::last_os_error()).into());
            }
            // HARD INVARIANT FOR MRV-245: ZERO MAIR_EL1 modification!
            if info.custom_active != 0 || info.mair_orig != info.mair_curr || info.all_cpus_equal != 1 {
                return Err(format!(
                    "REFUSING: MRV-245 requires enable_custom_mair=0 (custom_active={} eq={} orig={:#018x} curr={:#018x})",
                    info.custom_active, info.all_cpus_equal, info.mair_orig, info.mair_curr
                ).into());
            }

            let mut bind = Bar4MairBind {
                uaddr: buf.ptr as u64,
                len: SLOT_BYTES as u64,
                phys_out: 0,
            };
            let rc = unsafe { libc::ioctl(fd, BAR4_MAIR_IOC_BIND, &mut bind as *mut Bar4MairBind) };
            if rc != 0 {
                return Err(format!("BAR4_MAIR_IOC_BIND failed: {}", std::io::Error::last_os_error()).into());
            }
            if bind.phys_out != buf.pa {
                return Err(format!("BAR4_MAIR_IOC_BIND phys mismatch: {:#x} != {:#x}", bind.phys_out, buf.pa).into());
            }

            let do_mmap = |mode: u64| -> Result<*mut u8, Box<dyn Error>> {
                let off = (mode * SLOT_BYTES as u64) as libc::off_t;
                let p = unsafe {
                    libc::mmap(
                        ptr::null_mut(),
                        SLOT_BYTES,
                        libc::PROT_READ | libc::PROT_WRITE,
                        libc::MAP_SHARED,
                        fd,
                        off,
                    )
                };
                if p == libc::MAP_FAILED {
                    return Err(format!("mmap /dev/bar4_mair mode {mode} failed: {}", std::io::Error::last_os_error()).into());
                }
                Ok(p as *mut u8)
            };

            let wb_64k = do_mmap(BAR4_MAIR_MODE_NORMAL_WB)?;
            let normal_nc = do_mmap(BAR4_MAIR_MODE_NORMAL_NC)?;

            let translate = |name: &str, va: *mut u8, exp_attr: u8| -> Result<String, Box<dyn Error>> {
                let mut tr = Bar4MairTranslate { uaddr: va as u64, par_el1: 0 };
                let rc = unsafe { libc::ioctl(fd, BAR4_MAIR_IOC_TRANSLATE_VA, &mut tr as *mut Bar4MairTranslate) };
                if rc != 0 {
                    return Err(format!("TRANSLATE_VA({name}) failed: {}", std::io::Error::last_os_error()).into());
                }
                let f = tr.par_el1 & 1;
                let attr = ((tr.par_el1 >> 56) & 0xff) as u8;
                let sh = ((tr.par_el1 >> 7) & 0x3) as u8;
                let pa = tr.par_el1 & 0x0000_ffff_ffff_f000;
                if f != 0 || pa != buf.pa || attr != exp_attr {
                    return Err(format!(
                        "AT S1E0R verify FAILED for {name}: par={:#018x} F={f} pa={pa:#x}(exp {:#x}) attr={attr:#04x}(exp {exp_attr:#04x}) sh={sh}",
                        tr.par_el1, buf.pa
                    ).into());
                }
                Ok(format!(
                    "\"{name}\":{{\"par\":\"0x{:016x}\",\"pa\":\"0x{:x}\",\"attr\":\"0x{:02x}\",\"sh\":{sh}}}",
                    tr.par_el1, pa, attr
                ))
            };

            let pars = [
                translate("wb_huge", buf.ptr, 0xff)?,
                translate("wb_64k", wb_64k, 0xff)?,
                translate("normal_nc", normal_nc, 0x44)?,
            ];

            let par_json = format!(
                "{{\"mair_orig\":\"0x{:016x}\",\"mair_curr\":\"0x{:016x}\",\"online_cpus\":{},\"all_cpus_equal\":{},\"custom_active\":{},\"page_size\":{},\"pa\":\"0x{:x}\",\"translations\":{{{}}}}}",
                info.mair_orig,
                info.mair_curr,
                info.online_cpus,
                info.all_cpus_equal,
                info.custom_active,
                info.page_size,
                buf.pa,
                pars.join(",")
            );

            Ok(Self {
                dev,
                wb_huge: buf.ptr,
                wb_64k,
                normal_nc,
                par_json,
            })
        }

        fn ptr_for(&self, mode: MapMode) -> *mut u8 {
            match mode {
                MapMode::WbHuge => self.wb_huge,
                MapMode::Wb64k => self.wb_64k,
                MapMode::NormalNc => self.normal_nc,
            }
        }

        fn clean_inval_slot(&self) -> Result<(), String> {
            let rc = unsafe { libc::ioctl(self.dev.as_raw_fd(), BAR4_MAIR_IOC_CLEAN_INVAL, 0) };
            if rc != 0 {
                return Err(format!("BAR4_MAIR_IOC_CLEAN_INVAL failed: {}", std::io::Error::last_os_error()));
            }
            Ok(())
        }
    }

    impl Drop for MairMappings {
        fn drop(&mut self) {
            unsafe {
                libc::munmap(self.wb_64k.cast(), SLOT_BYTES);
                libc::munmap(self.normal_nc.cast(), SLOT_BYTES);
            }
        }
    }

    // -------------------------------------------------------------------------
    // 100% Non-multiplexed PMU suite: 8 TAD + 7 PMUv3/core + 6x2 DDR5 channels
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
        name: String,
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
        fn open(name: impl Into<String>, type_: u32, config: u64, cpu: i32) -> Result<Self, String> {
            let name = name.into();
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

    const TAD_EVENTS: &[(&str, u64)] = &[
        ("tad_req_msh_in_any", 0x01),
        ("tad_rsp_msh_out_retry_mn", 0x11),
        ("tad_req_msh_out_dss_rd", 0x0c),
        ("tad_req_msh_out_dss_wr", 0x0d),
        ("tad_snp_msh_out_any", 0x14),
        ("tad_snp_msh_out_exlmn", 0x16),
        ("tad_hit_any", 0x1f),
        ("tad_req_occ", 0x24),
    ];

    const CORE_EVENTS: &[(&str, u64)] = &[
        ("cpu_cycles", 0x0011),
        ("inst_retired", 0x0008),
        ("l1d_cache_refill", 0x0003),
        ("l2d_cache_refill", 0x0017),
        ("l2d_cache_inval", 0x0058),
        ("ll_cache_rd", 0x0036),
        ("ll_cache_miss_rd", 0x0037),
    ];

    struct CardPmu {
        tad: Vec<PerfCounter>,
        cores: Vec<(usize, Vec<PerfCounter>)>,
        ddr_rd: Vec<PerfCounter>,
        ddr_wr: Vec<PerfCounter>,
    }

    struct CardPmuSnap {
        tad: Vec<CounterSample>,
        cores: Vec<(usize, Vec<CounterSample>)>,
        ddr_rd: Vec<CounterSample>,
        ddr_wr: Vec<CounterSample>,
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

            let mut ddr_dirs: Vec<String> = fs::read_dir("/sys/bus/event_source/devices")
                .map_err(|e| format!("read_dir event_source/devices: {e}"))?
                .filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().to_string())
                .filter(|n| n.starts_with("mrvl_ddr_pmu_"))
                .collect();
            ddr_dirs.sort();
            let mut ddr_rd = Vec::with_capacity(ddr_dirs.len());
            let mut ddr_wr = Vec::with_capacity(ddr_dirs.len());
            for (idx, d) in ddr_dirs.iter().enumerate() {
                let base = format!("/sys/bus/event_source/devices/{d}");
                let dt = read_sysfs_u32(&format!("{base}/type"))?;
                let dc = read_sysfs_u32(&format!("{base}/cpumask"))? as i32;
                let cr = PerfCounter::open(format!("ddr{idx}_rd"), dt, 0x65, dc)?;
                let cw = PerfCounter::open(format!("ddr{idx}_wr"), dt, 0x64, dc)?;
                cr.enable();
                cw.enable();
                ddr_rd.push(cr);
                ddr_wr.push(cw);
            }

            Ok(Self { tad, cores, ddr_rd, ddr_wr })
        }

        fn snap(&self) -> CardPmuSnap {
            CardPmuSnap {
                tad: self.tad.iter().map(PerfCounter::read).collect(),
                cores: self.cores.iter().map(|(cpu, v)| (*cpu, v.iter().map(PerfCounter::read).collect())).collect(),
                ddr_rd: self.ddr_rd.iter().map(PerfCounter::read).collect(),
                ddr_wr: self.ddr_wr.iter().map(PerfCounter::read).collect(),
            }
        }

        fn format_delta_json(&self, s0: &CardPmuSnap, s1: &CardPmuSnap, active_cores: usize) -> (String, f64) {
            let mut min_pct = 100.0_f64;
            let mut tad_parts = Vec::with_capacity(self.tad.len());
            for (i, c) in self.tad.iter().enumerate() {
                let d = s1.tad[i].delta(s0.tad[i]);
                let p = d.pct();
                if p < min_pct { min_pct = p; }
                tad_parts.push(format!("\"{}\":{{\"val\":{},\"pct\":{:.2}}}", c.name, d.val, p));
            }
            let n_cores = active_cores.min(self.cores.len());
            let mut core_parts = Vec::with_capacity(n_cores);
            for ci in 0..n_cores {
                let (cpu, ref counters) = self.cores[ci];
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
            let mut ddr_rd_tot: u64 = 0;
            let mut ddr_wr_tot: u64 = 0;
            let mut ddr_ch_parts = Vec::with_capacity(self.ddr_rd.len());
            for i in 0..self.ddr_rd.len() {
                let dr = s1.ddr_rd[i].delta(s0.ddr_rd[i]);
                let dw = s1.ddr_wr[i].delta(s0.ddr_wr[i]);
                if dr.pct() < min_pct { min_pct = dr.pct(); }
                if dw.pct() < min_pct { min_pct = dw.pct(); }
                ddr_rd_tot = ddr_rd_tot.wrapping_add(dr.val);
                ddr_wr_tot = ddr_wr_tot.wrapping_add(dw.val);
                ddr_ch_parts.push(format!("{{\"ch\":{i},\"rd\":{},\"wr\":{}}}", dr.val, dw.val));
            }
            let json = format!(
                "\"min_pct\":{min_pct:.2},\"tad\":{{{}}},\"ddr\":{{\"reads_total\":{ddr_rd_tot},\"writes_total\":{ddr_wr_tot},\"channels\":[{}]}},\"core_pmu\":[{}]",
                tad_parts.join(","),
                ddr_ch_parts.join(","),
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
            for c in &self.ddr_rd { c.disable(); }
            for c in &self.ddr_wr { c.disable(); }
        }
    }

    // -------------------------------------------------------------------------
    // Full-64 B verification & explicit ARM64 poll kernels
    // -------------------------------------------------------------------------
    /// Verifies `tag == seq`, `tsc != 0`, and all 32 B payload words (caller checks head & tail `== seq`).
    #[inline(always)]
    fn verify_payload_words(w: &[u64; 8], seq: u64) -> bool {
        if w[2] != seq || w[1] == 0 {
            return false;
        }
        const K: u64 = 0xa5a5_a5a5_a5a5_a5a5;
        const M: u64 = 0x9e37_79b9_7f4a_7c15;
        let p0 = seq.wrapping_mul(M);
        let p1 = (seq ^ K).wrapping_mul(M);
        let p2 = (seq ^ K.wrapping_mul(2)).wrapping_mul(M);
        let p3 = (seq ^ K.wrapping_mul(3)).wrapping_mul(M);
        w[3] == p0 && w[4] == p1 && w[5] == p2 && w[6] == p3
    }
    #[inline(always)]
    unsafe fn load_64b_ldp(at: *const u8) -> [u64; 8] {
        let (w0, w1, w2, w3, w4, w5, w6, w7): (u64, u64, u64, u64, u64, u64, u64, u64);
        unsafe {
            asm!(
                "ldp {w0}, {w1}, [{p}, #0]",
                "ldp {w2}, {w3}, [{p}, #16]",
                "ldp {w4}, {w5}, [{p}, #32]",
                "ldp {w6}, {w7}, [{p}, #48]",
                p = in(reg) at,
                w0 = out(reg) w0,
                w1 = out(reg) w1,
                w2 = out(reg) w2,
                w3 = out(reg) w3,
                w4 = out(reg) w4,
                w5 = out(reg) w5,
                w6 = out(reg) w6,
                w7 = out(reg) w7,
                options(nostack, preserves_flags),
            );
        }
        [w0, w1, w2, w3, w4, w5, w6, w7]
    }
    #[inline(always)]
    unsafe fn load_u64_hdr(at: *const u8) -> u64 {
        let w0: u64;
        unsafe {
            asm!(
                "ldr {w0}, [{p}]",
                p = in(reg) at,
                w0 = out(reg) w0,
                options(nostack, readonly, preserves_flags),
            );
        }
        w0
    }

    #[inline(always)]
    unsafe fn read_cntvct() -> u64 {
        let v: u64;
        unsafe {
            asm!(
                "mrs {v}, cntvct_el0",
                v = out(reg) v,
                options(nostack, nomem, preserves_flags),
            );
        }
        v
    }

    #[inline(always)]
    unsafe fn pause_ns(ns: u64) {
        if ns == 0 {
            return;
        }
        // CN106 CNTVCT_EL0 runs at 1 GHz (1 tick = 1 ns)
        let target = unsafe { read_cntvct() }.wrapping_add(ns);
        while (unsafe { read_cntvct() }.wrapping_sub(target) as i64) < 0 {
            unsafe { asm!("yield", options(nostack, nomem, preserves_flags)) };
        }
    }

    #[inline(always)]
    unsafe fn load_64b_ldpq(at: *const u8) -> [u64; 8] {
        let (q0, q1, q2, q3): (uint64x2_t, uint64x2_t, uint64x2_t, uint64x2_t);
        unsafe {
            asm!(
                "ldp {q0:q}, {q1:q}, [{p}, #0]",
                "ldp {q2:q}, {q3:q}, [{p}, #32]",
                p = in(reg) at,
                q0 = out(vreg) q0,
                q1 = out(vreg) q1,
                q2 = out(vreg) q2,
                q3 = out(vreg) q3,
                options(nostack, preserves_flags),
            );
            [
                vgetq_lane_u64::<0>(q0), vgetq_lane_u64::<1>(q0),
                vgetq_lane_u64::<0>(q1), vgetq_lane_u64::<1>(q1),
                vgetq_lane_u64::<0>(q2), vgetq_lane_u64::<1>(q2),
                vgetq_lane_u64::<0>(q3), vgetq_lane_u64::<1>(q3),
            ]
        }
    }

    /// Issues 4 independent 64 B cell reads (`8x ldp q` into 16 NEON vregs) back-to-back
    /// with ZERO stack stores / store-forwarding stalls, achieving MLP=4 across CN106 L3.
    #[inline(always)]
    unsafe fn load_4x64b_ldpq(
        p0: *const u8,
        p1: *const u8,
        p2: *const u8,
        p3: *const u8,
    ) -> [[uint64x2_t; 4]; 4] {
        let (q0, q1, q2, q3): (uint64x2_t, uint64x2_t, uint64x2_t, uint64x2_t);
        let (q4, q5, q6, q7): (uint64x2_t, uint64x2_t, uint64x2_t, uint64x2_t);
        let (q8, q9, q10, q11): (uint64x2_t, uint64x2_t, uint64x2_t, uint64x2_t);
        let (q12, q13, q14, q15): (uint64x2_t, uint64x2_t, uint64x2_t, uint64x2_t);
        unsafe {
            asm!(
                "ldp {q0:q}, {q1:q}, [{p0}, #0]",
                "ldp {q2:q}, {q3:q}, [{p0}, #32]",
                "ldp {q4:q}, {q5:q}, [{p1}, #0]",
                "ldp {q6:q}, {q7:q}, [{p1}, #32]",
                "ldp {q8:q}, {q9:q}, [{p2}, #0]",
                "ldp {q10:q}, {q11:q}, [{p2}, #32]",
                "ldp {q12:q}, {q13:q}, [{p3}, #0]",
                "ldp {q14:q}, {q15:q}, [{p3}, #32]",
                p0 = in(reg) p0,
                p1 = in(reg) p1,
                p2 = in(reg) p2,
                p3 = in(reg) p3,
                q0 = out(vreg) q0, q1 = out(vreg) q1, q2 = out(vreg) q2, q3 = out(vreg) q3,
                q4 = out(vreg) q4, q5 = out(vreg) q5, q6 = out(vreg) q6, q7 = out(vreg) q7,
                q8 = out(vreg) q8, q9 = out(vreg) q9, q10 = out(vreg) q10, q11 = out(vreg) q11,
                q12 = out(vreg) q12, q13 = out(vreg) q13, q14 = out(vreg) q14, q15 = out(vreg) q15,
                options(nostack, preserves_flags),
            );
        }
        [
            [q0, q1, q2, q3],
            [q4, q5, q6, q7],
            [q8, q9, q10, q11],
            [q12, q13, q14, q15],
        ]
    }

    // -------------------------------------------------------------------------
    // Server Runtime (128 B cache-line isolated per worker to prevent CN106 false sharing!)
    // -------------------------------------------------------------------------
    #[repr(C, align(128))]
    struct WorkerRuntime {
        taken: AtomicU64,
        empty_polls: AtomicU64,
        busy_polls: AtomicU64,
        corrupt: AtomicU64,
        partial: AtomicU64,
        db_ahead_empty: AtomicU64,
        db_ahead_partial: AtomicU64,
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
                db_ahead_empty: AtomicU64::new(0),
                db_ahead_partial: AtomicU64::new(0),
                next_seq: AtomicU64::new(1),
            }
        }
        fn reset(&self, start_seq: u64) {
            self.taken.store(0, Ordering::Relaxed);
            self.empty_polls.store(0, Ordering::Relaxed);
            self.busy_polls.store(0, Ordering::Relaxed);
            self.corrupt.store(0, Ordering::Relaxed);
            self.partial.store(0, Ordering::Relaxed);
            self.db_ahead_empty.store(0, Ordering::Relaxed);
            self.db_ahead_partial.store(0, Ordering::Relaxed);
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

    #[inline(always)]
    unsafe fn write_credit_wc(rtg: ReplyTarget, worker_idx: usize, next_seq: u64, taken: u64) {
        let dst = unsafe { rtg.base_ptr.add(rtg.ring_off + worker_idx * 64) };
        let words = [next_seq, taken, 0, 0, 0, 0, 0, next_seq];
        unsafe { store64_gathered(dst, &words) };
    }

    #[inline(always)]
    unsafe fn write_reply_wc(rtg: ReplyTarget, worker_idx: usize, nth: usize, words: &[u64; 8]) {
        let slot_off = (worker_idx * REPLY_CELLS_PER_WORKER + (nth & (REPLY_CELLS_PER_WORKER - 1))) * 64;
        let dst = unsafe { rtg.base_ptr.add(rtg.ring_off + slot_off) };
        unsafe { store64_gathered(dst, words) };
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let args = Args::parse();
        if args.restore {
            let changed = restore_defaults(SLOT)?;
            for c in &changed {
                println!("mrv245_card: restored {c}");
            }
            if changed.is_empty() {
                println!("mrv245_card: BAR4 slot {SLOT} and outbound defaults already clean");
            }
            return Ok(());
        }

        pin_cpu(1);
        let changed = restore_defaults(SLOT)?;
        for c in &changed {
            eprintln!("mrv245_card: restored {c}");
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

        let mair = Arc::new(MairMappings::open_and_bind(&buf)?);
        println!("mrv245_card: mair_info={}", mair.par_json);

        let mut outbound: Vec<Mutex<OutboundWindow>> = Vec::new();
        if args.wc {
            for i in 0..MAX_OUTBOUND {
                let region_idx = OUTBOUND_REGIONS - 1 - i; // 255..=248 ONLY
                let ow = OutboundWindow::claim(region_idx, 0, 1, true)?;
                outbound.push(Mutex::new(ow));
            }
        }
        let outbound = Arc::new(outbound);

        let window = Bar4Window::claim(SLOT, buf.pa)?;
        let ctrl_ready = unsafe { &*(buf.ptr.add(READY_OFFSET) as *const AtomicU64) };
        let ctrl_nonce = unsafe { &*(buf.ptr.add(NONCE_OFFSET) as *const AtomicU64) };
        ctrl_ready.store(READY, Ordering::Release);
        println!(
            "mrv245_card: BAR4 slot {SLOT} -> pa {:#x} (was {:#x}); wc={}; hosts lease the slot at {}; rpc at {}",
            buf.pa, window.previous(), args.wc, args.control, args.rpc
        );

        let active_lanes = Arc::new(AtomicUsize::new(1));
        let active_cpl = Arc::new(AtomicUsize::new(1));
        let active_ring_cells = Arc::new(AtomicUsize::new(4096));
        let active_map_mode = Arc::new(AtomicUsize::new(0));
        let active_poll_mode = Arc::new(AtomicUsize::new(0));
        let active_work_mode = Arc::new(AtomicUsize::new(0));
        let active_credit_step = Arc::new(AtomicU64::new(128));
        let reply_targets: Vec<Arc<Mutex<Option<ReplyTarget>>>> =
            (0..MAX_WORKERS).map(|_| Arc::new(Mutex::new(None))).collect();
        let run_gen = Arc::new(AtomicU64::new(0));
        let worker_ready_gen: Vec<Arc<AtomicU64>> =
            (0..MAX_WORKERS).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let worker_rt: Vec<Arc<WorkerRuntime>> =
            (0..MAX_WORKERS).map(|_| Arc::new(WorkerRuntime::new())).collect();

        let pmu = CardPmu::open(&WORKER_CPUS)?;
        let wc_enabled = args.wc;

        let res = thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            let lease_srv = lease::serve(scope, listener, ctrl_nonce)?;

            for w in 0..MAX_WORKERS {
                let mair = mair.clone();
                let quit = quit.clone();
                let active_lanes = active_lanes.clone();
                let active_cpl = active_cpl.clone();
                let active_ring_cells = active_ring_cells.clone();
                let active_map_mode = active_map_mode.clone();
                let active_poll_mode = active_poll_mode.clone();
                let active_work_mode = active_work_mode.clone();
                let active_credit_step = active_credit_step.clone();
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
                        let lanes = active_lanes.load(Ordering::Acquire);
                        let cpl = active_cpl.load(Ordering::Acquire);
                        let ring_cells = active_ring_cells.load(Ordering::Acquire);
                        let mm_u = active_map_mode.load(Ordering::Acquire);
                        let pm_u = active_poll_mode.load(Ordering::Acquire);
                        let wm_u = active_work_mode.load(Ordering::Acquire);
                        let credit_step = active_credit_step.load(Ordering::Acquire).max(1);

                        if w >= cpl || pm_u == PollMode::None as usize {
                            my_ready.store(cur_gen, Ordering::Release);
                            while run_gen.load(Ordering::Acquire) == cur_gen && !quit.load(Ordering::Relaxed) {
                                thread::sleep(Duration::from_micros(200));
                            }
                            continue;
                        }

                        let map_mode = match mm_u {
                            0 => MapMode::WbHuge,
                            1 => MapMode::Wb64k,
                            2 => MapMode::NormalNc,
                            _ => MapMode::Wb64k,
                        };
                        let poll_base = mair.ptr_for(map_mode);
                        let k_per_lane = cpl / lanes;
                        let lane = w / k_per_lane;
                        let lane_w = w % k_per_lane;
                        let stride = k_per_lane as u64;
                        let ring_mask = ring_cells - 1;
                        let lane_base = unsafe { poll_base.add(lane * ring_cells * CELL_STRIDE_BYTES) };
                        let db_ptr = unsafe { poll_base.add(DOORBELL_OFFSET_BASE + lane * 128).cast::<u64>() };
                        let rtarget = *my_reply.lock().unwrap();
                        let is_closed = wm_u == WorkMode::Closed as usize;

                        let mut next: u64 = lane_w as u64 + 1;
                        let mut nth: usize = 0;
                        let mut local_taken: u64 = 0;
                        let mut local_empty: u64 = 0;
                        let mut local_busy: u64 = 0;
                        let mut local_corrupt: u64 = 0;
                        let mut local_partial: u64 = 0;
                        let mut local_db_ahead_empty: u64 = 0;
                        let mut local_db_ahead_partial: u64 = 0;
                        let mut last_partial_seq: u64 = 0;
                        let mut partial_retries: u32 = 0;
                        let mut last_credit_taken: u64 = 0;
                        let mut last_credit_next: u64 = next;
                        let mut in_busy_burst = false;
                        let mut empty_streak: u64 = 0;
                        let mut iter_chk: u64 = 0;

                        // Publish initial credit slot on tx_oneway so host starts clean
                        if !is_closed {
                            if let Some(rtg) = rtarget {
                                unsafe { write_credit_wc(rtg, w, next, 0) };
                            }
                        }

                        my_ready.store(cur_gen, Ordering::Release);
                        while !quit.load(Ordering::Relaxed) {
                            let mut consumed: u64 = 0;
                            let mut hit_partial = false;

                            let mut consume_one = |mut words: [u64; 8], s: u64, is_db: bool| -> bool {
                                if words[7] != s {
                                    if last_partial_seq != s {
                                        local_partial += 1;
                                        if is_db {
                                            local_db_ahead_partial += 1;
                                        }
                                        last_partial_seq = s;
                                        partial_retries = 1;
                                    } else {
                                        partial_retries = partial_retries.saturating_add(1);
                                    }
                                    if partial_retries < 4096 {
                                        hit_partial = true;
                                        return false;
                                    }
                                    // Bounded wait exceeded: classify permanently torn cell as corrupt and advance
                                    local_corrupt += 1;
                                    words[1] = 0;
                                    partial_retries = 0;
                                } else {
                                    partial_retries = 0;
                                    if !verify_payload_words(&words, s) {
                                        local_corrupt += 1;
                                        words[1] = 0;
                                    }
                                }
                                if is_closed {
                                    if let Some(rtg) = rtarget {
                                        unsafe { write_reply_wc(rtg, w, nth, &words) };
                                    }
                                }
                                nth += 1;
                                local_taken += 1;
                                consumed += 1;
                                true
                            };

                            match pm_u {
                                1 => {
                                    // u1_ldp
                                    let off = (((next - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                    let words = unsafe { load_64b_ldp(lane_base.add(off)) };
                                    if words[0] == next && consume_one(words, next, false) {
                                        next += stride;
                                    }
                                }
                                2 => {
                                    // u1_ldpq
                                    let off = (((next - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                    let words = unsafe { load_64b_ldpq(lane_base.add(off)) };
                                    if words[0] == next && consume_one(words, next, false) {
                                        next += stride;
                                    }
                                }
                                3 => {
                                    // u4_ldpq (MLP=4 concurrent loads, zero stack spill)
                                    let s0 = next;
                                    let s1 = next + stride;
                                    let s2 = next + stride * 2;
                                    let s3 = next + stride * 3;
                                    let p0 = unsafe { lane_base.add((((s0 - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES) };
                                    let p1 = unsafe { lane_base.add((((s1 - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES) };
                                    let p2 = unsafe { lane_base.add((((s2 - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES) };
                                    let p3 = unsafe { lane_base.add((((s3 - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES) };
                                    let qs = unsafe { load_4x64b_ldpq(p0, p1, p2, p3) };
                                    for j in 0..4 {
                                        let s = next;
                                        let q = qs[j];
                                        let w0 = unsafe { vgetq_lane_u64::<0>(q[0]) };
                                        if w0 != s {
                                            break;
                                        }
                                        let words = unsafe {
                                            [
                                                w0, vgetq_lane_u64::<1>(q[0]),
                                                vgetq_lane_u64::<0>(q[1]), vgetq_lane_u64::<1>(q[1]),
                                                vgetq_lane_u64::<0>(q[2]), vgetq_lane_u64::<1>(q[2]),
                                                vgetq_lane_u64::<0>(q[3]), vgetq_lane_u64::<1>(q[3]),
                                            ]
                                        };
                                        if !consume_one(words, s, false) {
                                            break;
                                        }
                                        next += stride;
                                    }
                                }
                                4 => {
                                    // doorbell (MRV-177 Arm B2)
                                    let prod_idx = unsafe { AtomicU64::from_ptr(db_ptr) }.load(Ordering::Acquire);
                                    if prod_idx >= next {
                                        for _ in 0..64 {
                                            if next > prod_idx {
                                                break;
                                            }
                                            let off = (((next - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                            let words = unsafe { load_64b_ldp(lane_base.add(off)) };
                                            if words[0] != next {
                                                local_db_ahead_empty += 1;
                                                break;
                                            }
                                            if !consume_one(words, next, true) {
                                                break;
                                            }
                                            next += stride;
                                        }
                                    }
                                }
                                5 | 6 | 7 => {
                                    // Adaptive backoff:
                                    // - In busy burst: full u4_ldpq (MLP=4) harvest
                                    // - On empty probe: single-word header ldr on s0 only;
                                    //   on hit -> consume s0 (load_64b_ldpq) and enter busy burst (u4_ldpq next iter);
                                    //   on miss -> pause via CNTVCT_EL0 (zero TAD traffic!)
                                    if in_busy_burst {
                                        let s0 = next;
                                        let s1 = next + stride;
                                        let s2 = next + stride * 2;
                                        let s3 = next + stride * 3;
                                        let p0 = unsafe { lane_base.add((((s0 - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES) };
                                        let p1 = unsafe { lane_base.add((((s1 - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES) };
                                        let p2 = unsafe { lane_base.add((((s2 - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES) };
                                        let p3 = unsafe { lane_base.add((((s3 - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES) };
                                        let qs = unsafe { load_4x64b_ldpq(p0, p1, p2, p3) };
                                        for j in 0..4 {
                                            let s = next;
                                            let q = qs[j];
                                            let w0 = unsafe { vgetq_lane_u64::<0>(q[0]) };
                                            if w0 != s {
                                                break;
                                            }
                                            let words = unsafe {
                                                [
                                                    w0, vgetq_lane_u64::<1>(q[0]),
                                                    vgetq_lane_u64::<0>(q[1]), vgetq_lane_u64::<1>(q[1]),
                                                    vgetq_lane_u64::<0>(q[2]), vgetq_lane_u64::<1>(q[2]),
                                                    vgetq_lane_u64::<0>(q[3]), vgetq_lane_u64::<1>(q[3]),
                                                ]
                                            };
                                            if !consume_one(words, s, false) {
                                                break;
                                            }
                                            next += stride;
                                        }
                                    } else {
                                        let off = (((next - 1) as usize) & ring_mask) * CELL_STRIDE_BYTES;
                                        let p0 = unsafe { lane_base.add(off) };
                                        let w0 = unsafe { load_u64_hdr(p0) };
                                        if w0 == next {
                                            let words = unsafe { load_64b_ldpq(p0) };
                                            if words[0] == next && consume_one(words, next, false) {
                                                next += stride;
                                            }
                                        } else {
                                            let ns = match pm_u {
                                                5 => 50,
                                                6 => 100,
                                                _ => match empty_streak {
                                                    0 => 0,
                                                    1 => 25,
                                                    2 => 50,
                                                    _ => 100,
                                                },
                                            };
                                            unsafe { pause_ns(ns) };
                                        }
                                    }
                                }
                                _ => {}
                            }
                            if consumed > 0 {
                                empty_streak = 0;
                                if !in_busy_burst {
                                    local_busy += 1;
                                    in_busy_burst = true;
                                }
                                if !is_closed && (local_taken - last_credit_taken) >= credit_step {
                                    if let Some(rtg) = rtarget {
                                        unsafe { write_credit_wc(rtg, w, next, local_taken) };
                                        last_credit_taken = local_taken;
                                        last_credit_next = next;
                                    }
                                }
                                if (local_taken & 255) < consumed {
                                    rt.taken.store(local_taken, Ordering::Relaxed);
                                    rt.next_seq.store(next, Ordering::Relaxed);
                                }
                                if (local_taken & 4095) < consumed {
                                    if run_gen.load(Ordering::Acquire) != cur_gen {
                                        break;
                                    }
                                }
                            } else if !hit_partial {
                                if in_busy_burst {
                                    in_busy_burst = false;
                                }
                                empty_streak += 1;
                                if !is_closed && empty_streak == 64 && next != last_credit_next {
                                    if let Some(rtg) = rtarget {
                                        unsafe { write_credit_wc(rtg, w, next, local_taken) };
                                        last_credit_taken = local_taken;
                                        last_credit_next = next;
                                    }
                                }
                                local_empty += 1;
                                iter_chk += 1;
                                if (iter_chk & 255) == 0 {
                                    if run_gen.load(Ordering::Acquire) != cur_gen {
                                        break;
                                    }
                                    rt.taken.store(local_taken, Ordering::Relaxed);
                                    rt.next_seq.store(next, Ordering::Relaxed);
                                    rt.empty_polls.store(local_empty, Ordering::Relaxed);
                                    rt.busy_polls.store(local_busy, Ordering::Relaxed);
                                    rt.corrupt.store(local_corrupt, Ordering::Relaxed);
                                    rt.partial.store(local_partial, Ordering::Relaxed);
                                    rt.db_ahead_empty.store(local_db_ahead_empty, Ordering::Relaxed);
                                    rt.db_ahead_partial.store(local_db_ahead_partial, Ordering::Relaxed);
                                }
                            } else {
                                iter_chk += 1;
                                if (iter_chk & 127) == 0 && run_gen.load(Ordering::Acquire) != cur_gen {
                                    break;
                                }
                            }
                        }

                        if !is_closed && next != last_credit_next {
                            if let Some(rtg) = rtarget {
                                unsafe { write_credit_wc(rtg, w, next, local_taken) };
                            }
                        }
                        rt.taken.store(local_taken, Ordering::Release);
                        rt.empty_polls.store(local_empty, Ordering::Release);
                        rt.busy_polls.store(local_busy, Ordering::Release);
                        rt.corrupt.store(local_corrupt, Ordering::Release);
                        rt.partial.store(local_partial, Ordering::Release);
                        rt.db_ahead_empty.store(local_db_ahead_empty, Ordering::Release);
                        rt.db_ahead_partial.store(local_db_ahead_partial, Ordering::Release);
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
                        "INFO" => format!("{}\n", mair.par_json),
                        // PREPARE <host_lanes> <card_cores> <ring_cells> <map_mode> <poll_mode> <work_mode> <credit_step> <reply_iova_hex>
                        "PREPARE" if parts.len() == 9 => {
                            let lanes: usize = parts[1].parse().unwrap_or(0);
                            let cpl: usize = parts[2].parse().unwrap_or(0);
                            let r: usize = parts[3].parse().unwrap_or(0);
                            let mm = MapMode::parse(parts[4]);
                            let pm = PollMode::parse(parts[5]);
                            let wm = WorkMode::parse(parts[6]);
                            let cstep: u64 = parts[7].parse().unwrap_or(0);
                            let riova = u64::from_str_radix(parts[8].trim_start_matches("0x"), 16).unwrap_or(u64::MAX);

                            let valid_basic = (lanes == 1 || lanes == 2)
                                && (1..=MAX_WORKERS).contains(&cpl)
                                && cpl % lanes == 0
                                && r > 0
                                && r.is_power_of_two()
                                && lanes * r * CELL_STRIDE_BYTES <= (SLOT_BYTES - 65536)
                                && mm.is_ok()
                                && pm.is_ok()
                                && wm.is_ok()
                                && cstep >= 1
                                && riova != u64::MAX
                                && (riova == 0 || wc_enabled);

                            if !valid_basic {
                                format!("ERR bad PREPARE args: {}\n", line.trim())
                            } else {
                                let mm = mm.unwrap();
                                let pm = pm.unwrap();
                                let wm = wm.unwrap();
                                if wm == WorkMode::Closed && cpl > MAX_OUTBOUND {
                                    format!("ERR closed mode requires card_cores <= {MAX_OUTBOUND} (got {cpl})\n")
                                } else {
                                    // Pause active workers first
                                    if (*cur_gen & 1) == 1 {
                                        *cur_gen += 1;
                                        run_gen.store(*cur_gen, Ordering::Release);
                                        for w in 0..MAX_WORKERS {
                                            while worker_ready_gen[w].load(Ordering::Acquire) != *cur_gen {
                                                thread::sleep(Duration::from_micros(50));
                                            }
                                        }
                                    }

                                    let mut retarget_err: Option<String> = None;
                                    if riova != 0 {
                                        let region_base = riova & !(OUTBOUND_REGION_BYTES - 1);
                                        let ring_off = (riova - region_base) as usize;
                                        let n_ob = cpl.min(MAX_OUTBOUND);
                                        let mut ptrs = [ptr::null_mut::<u8>(); MAX_OUTBOUND];
                                        for i in 0..n_ob {
                                            let mut ow = outbound[i].lock().unwrap();
                                            if let Err(e) = ow.retarget(region_base) {
                                                retarget_err = Some(format!("ERR retarget ob={i}: {e}\n"));
                                                break;
                                            }
                                            ptrs[i] = ow.as_ptr();
                                        }
                                        if retarget_err.is_none() {
                                            for w in 0..cpl {
                                                *reply_targets[w].lock().unwrap() = Some(ReplyTarget {
                                                    base_ptr: ptrs[w % MAX_OUTBOUND],
                                                    ring_off,
                                                });
                                            }
                                            for w in cpl..MAX_WORKERS {
                                                *reply_targets[w].lock().unwrap() = None;
                                            }
                                        }
                                    } else {
                                        for w in 0..MAX_WORKERS {
                                            *reply_targets[w].lock().unwrap() = None;
                                        }
                                    }

                                    if let Some(err) = retarget_err {
                                        err
                                    } else {
                                        // Zero ring lanes + doorbell lines (NEVER touching READY_OFFSET / NONCE_OFFSET)
                                        unsafe {
                                            ptr::write_bytes(buf.ptr, 0, lanes * r * CELL_STRIDE_BYTES);
                                            ptr::write_bytes(buf.ptr.add(DOORBELL_OFFSET_BASE), 0, 256);
                                        }
                                        if let Err(e) = mair.clean_inval_slot() {
                                            format!("ERR clean_inval: {e}\n")
                                        } else {
                                            let k_per_lane = cpl / lanes;
                                            for w in 0..MAX_WORKERS {
                                                let start_seq = if w < cpl { (w % k_per_lane) as u64 + 1 } else { 1 };
                                                worker_rt[w].reset(start_seq);
                                            }
                                            active_lanes.store(lanes, Ordering::Release);
                                            active_cpl.store(cpl, Ordering::Release);
                                            active_ring_cells.store(r, Ordering::Release);
                                            active_map_mode.store(mm as usize, Ordering::Release);
                                            active_poll_mode.store(pm as usize, Ordering::Release);
                                            active_work_mode.store(wm as usize, Ordering::Release);
                                            active_credit_step.store(cstep, Ordering::Release);
                                            *cur_gen += 1;
                                            run_gen.store(*cur_gen, Ordering::Release);
                                            for w in 0..MAX_WORKERS {
                                                while worker_ready_gen[w].load(Ordering::Acquire) != *cur_gen {
                                                    thread::sleep(Duration::from_micros(50));
                                                }
                                            }
                                            format!(
                                                "OK lanes={lanes} cpl={cpl} ring_cells={r} map={} poll={} work={} cstep={cstep} riova={riova:#x}\n",
                                                mm.as_str(), pm.as_str(), wm.as_str()
                                            )
                                        }
                                    }
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
                                let cpl = active_cpl.load(Ordering::Acquire);
                                let (pmu_json, _) = pmu.format_delta_json(&s0, &s1, cpl);
                                let mut w_json = Vec::with_capacity(cpl);
                                for w in 0..cpl {
                                    let td = worker_rt[w].taken.load(Ordering::Relaxed).saturating_sub(pmu_taken0[w]);
                                    let ed = worker_rt[w].empty_polls.load(Ordering::Relaxed).saturating_sub(pmu_empty0[w]);
                                    let bd = worker_rt[w].busy_polls.load(Ordering::Relaxed).saturating_sub(pmu_busy0[w]);
                                    w_json.push(format!(
                                        "{{\"worker\":{w},\"cpu\":{},\"taken_win\":{td},\"empty_win\":{ed},\"busy_win\":{bd},\"corrupt\":{},\"partial\":{},\"db_ahead_empty\":{},\"db_ahead_partial\":{}}}",
                                        WORKER_CPUS[w],
                                        worker_rt[w].corrupt.load(Ordering::Relaxed),
                                        worker_rt[w].partial.load(Ordering::Relaxed),
                                        worker_rt[w].db_ahead_empty.load(Ordering::Relaxed),
                                        worker_rt[w].db_ahead_partial.load(Ordering::Relaxed),
                                    ));
                                }
                                format!("{{\"elapsed_ns\":{elapsed_ns},\"workers\":[{}],{pmu_json}}}\n", w_json.join(","))
                            } else {
                                "ERR PMU_START not called\n".to_string()
                            }
                        }
                        "STATS" => {
                            let cpl = active_cpl.load(Ordering::Acquire);
                            let mut parts_j = Vec::with_capacity(cpl);
                            for w in 0..cpl {
                                parts_j.push(format!(
                                    "{{\"worker\":{w},\"cpu\":{},\"taken\":{},\"next\":{},\"corrupt\":{},\"partial\":{},\"db_ahead_empty\":{},\"db_ahead_partial\":{},\"empty\":{},\"busy\":{}}}",
                                    WORKER_CPUS[w],
                                    worker_rt[w].taken.load(Ordering::Acquire),
                                    worker_rt[w].next_seq.load(Ordering::Acquire),
                                    worker_rt[w].corrupt.load(Ordering::Acquire),
                                    worker_rt[w].partial.load(Ordering::Acquire),
                                    worker_rt[w].db_ahead_empty.load(Ordering::Acquire),
                                    worker_rt[w].db_ahead_partial.load(Ordering::Acquire),
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
        drop(mair);
        res
    }
}
