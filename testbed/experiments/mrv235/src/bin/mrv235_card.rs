//! MRV-235 card-side benchmark (aarch64 CN106, root):
//!
//! Strictly follows MRV-26 BAR4 slot 15 lease (`inline::SLOT = 15`) and maps
//! ONLY the leased 4 MiB slot 15 buffer via `/dev/bar4_mair` under controlled
//! ARMv8 page-table memory attributes:
//!   - `wb_huge`      : Original 512 MiB MAP_HUGETLB Normal-WB (0xFF) baseline
//!   - `wb_64k`       : `/dev/bar4_mair` mode 0 Normal-WB (0xFF) 64 KiB pages
//!   - `normal_nc`    : `/dev/bar4_mair` mode 1 standard Linux MT_NORMAL_NC (0x44)
//!   - `inner_nc_ish` : `/dev/bar4_mair` mode 2 Inner-NC, Outer-WB (0xF4, SH=ISH)
//!   - `inner_nc_osh` : `/dev/bar4_mair` mode 3 Inner-NC, Outer-WB (0xF4, SH=OSH)
//!   - `inner_nc_nsh` : `/dev/bar4_mair` mode 4 Inner-NC, Outer-WB (0xF4, SH=NSH)
//!
//! Hardware-verifies every VA's resolved `MAIR` attribute + `SH` + `PA` via
//! `AT S1E0R` (`BAR4_MAIR_IOC_TRANSLATE_VA` -> `PAR_EL1`).
//!
//! Also tests single-variable card-side payload read & prefetch variants to
//! answer Hongtao's prefetch question empirically:
//!   - `full64_ldar`        : `ldar` head poll + full 64 B `ldr` payload read
//!   - `full64_ldr_dmb`     : `ldr` head poll + `dmb ishld` + full 64 B `ldr` read
//!   - `full64_ldp`         : `ldr` head poll + `dmb ishld` + full 64 B `4x ldp` read
//!   - `hdr16_ldp`          : single 16 B `ldp (seq, tsc)` header-only poll/read
//!   - `dual_va_wb`         : `ldr` head poll on active VA + full 64 B read on WB VA
//!   - `dual_va_wb_civac`   : `dual_va_wb` + post-read `dc civac` on WB VA
//!   - `post_prfm_l3`       : `ldr` head poll + post-ready `prfm pldl3keep` + full 64 B
//!   - `post_prfm_l1_nc`    : `ldr` head poll + post-ready `prfm pldl1keep` on active VA + full 64 B
//!   - `post_prfm_l1_wb`    : `ldr` head poll + post-ready `prfm pldl1keep` on WB VA + full 64 B on WB VA
//!   - `frontier_prfm_l1_wb`: `ldr` head poll + unready-frontier `prfm pldl1keep` on WB VA
//!   - `frontier_prfm_l1_nc`: `ldr` head poll + unready-frontier `prfm pldl1keep` on active VA
//!   - `none`               : card silent (host direct-push ceiling)
//!
//! Collects 100.0% non-multiplexed CN106 PMUs:
//!   - 8 `tad` counters (incl. `tad_snp_msh_out_any`, `tad_req_occ`, `tad_req_msh_out_dss_rd`)
//!   - 7 `armv8_pmuv3` counters per worker core (incl. `l1d_cache_refill`, `0x58 l2d_cache_inval`, `ll_cache_rd`, `ll_cache_miss_rd`)
//!   - 12 `mrvl_ddr_pmu` fixed counters (6 DDR5 channels x `ddr_ddr_reads` & `ddr_ddr_writes`)

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv235_card runs on the aarch64 card");
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
    use testbed::inline::{Cell, NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{
        Bar4Window, OUTBOUND_REGION_BYTES, OUTBOUND_REGIONS, OutboundWindow, restore_defaults, store64_gathered,
    };

    const HUGE_512M: usize = 512 * 1024 * 1024;
    const MAX_WORKERS: usize = 4; // K in {1, 2, 4} card cores on cpus 4..7 -> regions 255..252
    const REPLY_CELLS_PER_WORKER: usize = 1024;
    const CELL_STRIDE_BYTES: usize = 128;
    const WORKER_CPUS: [usize; MAX_WORKERS] = [4, 5, 6, 7];

    // -------------------------------------------------------------------------
    // `/dev/bar4_mair` UAPI (must match card-kmod/bar4_mair/bar4_mair_uapi.h)
    // -------------------------------------------------------------------------
    const BAR4_MAIR_MODE_NORMAL_WB: u64 = 0;
    const BAR4_MAIR_MODE_NORMAL_NC: u64 = 1;
    const BAR4_MAIR_MODE_INNER_NC_ISH: u64 = 2;
    const BAR4_MAIR_MODE_INNER_NC_OSH: u64 = 3;
    const BAR4_MAIR_MODE_INNER_NC_NSH: u64 = 4;

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
        InnerNcIsh = 3,
        InnerNcOsh = 4,
        InnerNcNsh = 5,
    }

    impl MapMode {
        fn parse(s: &str) -> Result<Self, String> {
            match s {
                "wb_huge" => Ok(Self::WbHuge),
                "wb_64k" => Ok(Self::Wb64k),
                "normal_nc" => Ok(Self::NormalNc),
                "inner_nc_ish" => Ok(Self::InnerNcIsh),
                "inner_nc_osh" => Ok(Self::InnerNcOsh),
                "inner_nc_nsh" => Ok(Self::InnerNcNsh),
                _ => Err(format!("unknown map_mode: {s}")),
            }
        }
        fn as_str(self) -> &'static str {
            match self {
                Self::WbHuge => "wb_huge",
                Self::Wb64k => "wb_64k",
                Self::NormalNc => "normal_nc",
                Self::InnerNcIsh => "inner_nc_ish",
                Self::InnerNcOsh => "inner_nc_osh",
                Self::InnerNcNsh => "inner_nc_nsh",
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum AccessMode {
        None = 0,
        Full64Ldar = 1,
        Full64LdrDmb = 2,
        Full64Ldp = 3,
        Hdr16Ldp = 4,
        DualVaWb = 5,
        DualVaWbCivac = 6,
        PostPrfmL3 = 7,
        PostPrfmL1Nc = 8,
        PostPrfmL1Wb = 9,
        FrontierPrfmL1Wb = 10,
        FrontierPrfmL1Nc = 11,
        Full64NodmbLdp = 12,
        Full64OnePassLdp = 13,
        Full64OnePassLdpq = 14,
    }

    impl AccessMode {
        fn parse(s: &str) -> Result<Self, String> {
            match s {
                "none" => Ok(Self::None),
                "full64_ldar" => Ok(Self::Full64Ldar),
                "full64_ldr_dmb" => Ok(Self::Full64LdrDmb),
                "full64_ldp" => Ok(Self::Full64Ldp),
                "hdr16_ldp" => Ok(Self::Hdr16Ldp),
                "dual_va_wb" => Ok(Self::DualVaWb),
                "dual_va_wb_civac" => Ok(Self::DualVaWbCivac),
                "post_prfm_l3" => Ok(Self::PostPrfmL3),
                "post_prfm_l1_nc" => Ok(Self::PostPrfmL1Nc),
                "post_prfm_l1_wb" => Ok(Self::PostPrfmL1Wb),
                "frontier_prfm_l1_wb" => Ok(Self::FrontierPrfmL1Wb),
                "frontier_prfm_l1_nc" => Ok(Self::FrontierPrfmL1Nc),
                "full64_nodmb_ldp" => Ok(Self::Full64NodmbLdp),
                "full64_1pass_ldp" => Ok(Self::Full64OnePassLdp),
                "full64_1pass_ldpq" => Ok(Self::Full64OnePassLdpq),
                _ => Err(format!("unknown access_mode: {s}")),
            }
        }
        fn as_str(self) -> &'static str {
            match self {
                Self::None => "none",
                Self::Full64Ldar => "full64_ldar",
                Self::Full64LdrDmb => "full64_ldr_dmb",
                Self::Full64Ldp => "full64_ldp",
                Self::Hdr16Ldp => "hdr16_ldp",
                Self::DualVaWb => "dual_va_wb",
                Self::DualVaWbCivac => "dual_va_wb_civac",
                Self::PostPrfmL3 => "post_prfm_l3",
                Self::PostPrfmL1Nc => "post_prfm_l1_nc",
                Self::PostPrfmL1Wb => "post_prfm_l1_wb",
                Self::FrontierPrfmL1Wb => "frontier_prfm_l1_wb",
                Self::FrontierPrfmL1Nc => "frontier_prfm_l1_nc",
                Self::Full64NodmbLdp => "full64_nodmb_ldp",
                Self::Full64OnePassLdp => "full64_1pass_ldp",
                Self::Full64OnePassLdpq => "full64_1pass_ldpq",
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
        info: Bar4MairInfo,
        wb_huge: *mut u8,
        wb_64k: *mut u8,
        normal_nc: *mut u8,
        inner_nc_ish: Option<*mut u8>,
        inner_nc_osh: Option<*mut u8>,
        inner_nc_nsh: Option<*mut u8>,
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
            let (inner_nc_ish, inner_nc_osh, inner_nc_nsh) = if info.custom_active != 0 {
                (
                    Some(do_mmap(BAR4_MAIR_MODE_INNER_NC_ISH)?),
                    Some(do_mmap(BAR4_MAIR_MODE_INNER_NC_OSH)?),
                    Some(do_mmap(BAR4_MAIR_MODE_INNER_NC_NSH)?),
                )
            } else {
                (None, None, None)
            };

            let translate = |name: &str, va: *mut u8, exp_attr: u8| -> Result<String, Box<dyn Error>> {
                // Touch via volatile read on wb_huge only? No: PTEs are already mapped by remap_pfn_range.
                // For wb_huge it was already written/zeroed during alloc.
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

            let mut pars = Vec::new();
            pars.push(translate("wb_huge", buf.ptr, 0xff)?);
            pars.push(translate("wb_64k", wb_64k, 0xff)?);
            pars.push(translate("normal_nc", normal_nc, 0x44)?);
            if let Some(p) = inner_nc_ish {
                pars.push(translate("inner_nc_ish", p, info.custom_attr as u8)?);
            }
            if let Some(p) = inner_nc_osh {
                pars.push(translate("inner_nc_osh", p, info.custom_attr as u8)?);
            }
            if let Some(p) = inner_nc_nsh {
                pars.push(translate("inner_nc_nsh", p, info.custom_attr as u8)?);
            }

            let par_json = format!(
                "{{\"mair_orig\":\"0x{:016x}\",\"mair_curr\":\"0x{:016x}\",\"online_cpus\":{},\"all_cpus_equal\":{},\"custom_active\":{},\"custom_slot\":{},\"custom_attr\":\"0x{:02x}\",\"page_size\":{},\"ttbr0_cnp\":\"0x{:x}\",\"ttbr1_cnp\":\"0x{:x}\",\"pa\":\"0x{:x}\",\"translations\":{{{}}}}}",
                info.mair_orig,
                info.mair_curr,
                info.online_cpus,
                info.all_cpus_equal,
                info.custom_active,
                info.custom_slot,
                info.custom_attr,
                info.page_size,
                info.ttbr0_cnp,
                info.ttbr1_cnp,
                buf.pa,
                pars.join(",")
            );

            Ok(Self {
                dev,
                info,
                wb_huge: buf.ptr,
                wb_64k,
                normal_nc,
                inner_nc_ish,
                inner_nc_osh,
                inner_nc_nsh,
                par_json,
            })
        }

        fn ptr_for(&self, mode: MapMode) -> Option<*mut u8> {
            match mode {
                MapMode::WbHuge => Some(self.wb_huge),
                MapMode::Wb64k => Some(self.wb_64k),
                MapMode::NormalNc => Some(self.normal_nc),
                MapMode::InnerNcIsh => self.inner_nc_ish,
                MapMode::InnerNcOsh => self.inner_nc_osh,
                MapMode::InnerNcNsh => self.inner_nc_nsh,
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
                if let Some(p) = self.inner_nc_ish { libc::munmap(p.cast(), SLOT_BYTES); }
                if let Some(p) = self.inner_nc_osh { libc::munmap(p.cast(), SLOT_BYTES); }
                if let Some(p) = self.inner_nc_nsh { libc::munmap(p.cast(), SLOT_BYTES); }
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

    // Exactly 8 CN106 TAD PMU v1 hardware counters (all <= 0x24) = 100.0% non-multiplexed!
    const TAD_EVENTS: &[(&str, u64)] = &[
        ("tad_req_msh_in_any", 0x01),
        ("tad_dat_msh_in_dss", 0x0a),
        ("tad_req_msh_out_dss_rd", 0x0c),
        ("tad_req_msh_out_dss_wr", 0x0d),
        ("tad_snp_msh_out_any", 0x14),
        ("tad_alloc_any", 0x1c),
        ("tad_hit_any", 0x1f),
        ("tad_req_occ", 0x24),
    ];

    // Exactly 7 Neoverse N2 PMUv3 counters (1 dedicated cycle + 6 programmable) = 100.0% non-multiplexed!
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
    // Explicit ARM64 poll / payload-read / prefetch kernels
    // -------------------------------------------------------------------------
    enum PollRes {
        Empty,
        Partial,
        Complete(Cell),
    }

    #[inline(always)]
    unsafe fn load_head_ldar(ptr: *const u64) -> u64 {
        let head: u64;
        unsafe {
            asm!(
                "ldar {head}, [{ptr}]",
                ptr = in(reg) ptr,
                head = out(reg) head,
                options(nostack, preserves_flags),
            );
        }
        head
    }

    #[inline(always)]
    unsafe fn load_head_ldr(ptr: *const u64) -> u64 {
        let head: u64;
        unsafe {
            asm!(
                "ldr {head}, [{ptr}]",
                ptr = in(reg) ptr,
                head = out(reg) head,
                options(nostack, preserves_flags),
            );
        }
        head
    }

    #[inline(always)]
    unsafe fn read_tail_ldr(at: *const Cell, head: u64, seq: u64) -> PollRes {
        let p = at.cast::<u64>();
        let mut words = [0u64; 8];
        words[0] = head;
        for i in 1..8 {
            words[i] = unsafe { p.add(i).read_volatile() };
        }
        if words[7] != seq {
            return PollRes::Partial;
        }
        PollRes::Complete(unsafe { std::mem::transmute::<[u64; 8], Cell>(words) })
    }

    #[inline(always)]
    unsafe fn read_full64_ldp(at: *const Cell, seq: u64) -> PollRes {
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
        if w0 != seq || w7 != seq {
            return PollRes::Partial;
        }
        PollRes::Complete(unsafe { std::mem::transmute::<[u64; 8], Cell>([w0, w1, w2, w3, w4, w5, w6, w7]) })
    }

    #[inline(never)]
    unsafe fn poll_full64_ldar(at: *const Cell, seq: u64) -> PollRes {
        let head = unsafe { load_head_ldar(at.cast()) };
        if head != seq {
            return PollRes::Empty;
        }
        unsafe { read_tail_ldr(at, head, seq) }
    }

    #[inline(never)]
    unsafe fn poll_full64_ldr_dmb(at: *const Cell, seq: u64) -> PollRes {
        let head = unsafe { load_head_ldr(at.cast()) };
        if head != seq {
            return PollRes::Empty;
        }
        unsafe {
            asm!("dmb ishld", options(nostack, preserves_flags));
            read_tail_ldr(at, head, seq)
        }
    }

    #[inline(never)]
    unsafe fn poll_full64_ldp(at: *const Cell, seq: u64) -> PollRes {
        let head = unsafe { load_head_ldr(at.cast()) };
        if head != seq {
            return PollRes::Empty;
        }
        unsafe {
            asm!("dmb ishld", options(nostack, preserves_flags));
            read_full64_ldp(at, seq)
        }
    }

    #[inline(never)]
    unsafe fn poll_hdr16_ldp(at: *const Cell, seq: u64) -> PollRes {
        let (head, tsc): (u64, u64);
        unsafe {
            asm!(
                "ldp {head}, {tsc}, [{p}]",
                p = in(reg) at,
                head = out(reg) head,
                tsc = out(reg) tsc,
                options(nostack, preserves_flags),
            );
        }
        if head != seq {
            return PollRes::Empty;
        }
        PollRes::Complete(Cell::new(seq, seq, tsc))
    }

    #[inline(never)]
    unsafe fn poll_dual_va_wb(poll_at: *const Cell, wb_at: *const Cell, seq: u64, do_civac: bool) -> PollRes {
        let head = unsafe { load_head_ldr(poll_at.cast()) };
        if head != seq {
            return PollRes::Empty;
        }
        unsafe {
            asm!("dmb ishld", options(nostack, preserves_flags));
        }
        let res = unsafe { read_full64_ldp(wb_at, seq) };
        if do_civac && matches!(res, PollRes::Complete(_)) {
            unsafe {
                asm!("dc civac, {p}", p = in(reg) wb_at, options(nostack, preserves_flags));
            }
        }
        res
    }

    #[inline(never)]
    unsafe fn poll_post_prfm_l3(poll_at: *const Cell, next_poll_at: *const Cell, seq: u64) -> PollRes {
        let head = unsafe { load_head_ldr(poll_at.cast()) };
        if head != seq {
            return PollRes::Empty;
        }
        unsafe {
            asm!(
                "prfm pldl3keep, [{np}]",
                "dmb ishld",
                np = in(reg) next_poll_at,
                options(nostack, preserves_flags),
            );
            read_full64_ldp(poll_at, seq)
        }
    }

    #[inline(never)]
    unsafe fn poll_post_prfm_l1_nc(poll_at: *const Cell, seq: u64) -> PollRes {
        let head = unsafe { load_head_ldr(poll_at.cast()) };
        if head != seq {
            return PollRes::Empty;
        }
        unsafe {
            asm!(
                "prfm pldl1keep, [{p}]",
                "dmb ishld",
                p = in(reg) poll_at,
                options(nostack, preserves_flags),
            );
            read_full64_ldp(poll_at, seq)
        }
    }

    #[inline(never)]
    unsafe fn poll_post_prfm_l1_wb(poll_at: *const Cell, wb_at: *const Cell, seq: u64) -> PollRes {
        let head = unsafe { load_head_ldr(poll_at.cast()) };
        if head != seq {
            return PollRes::Empty;
        }
        unsafe {
            asm!(
                "prfm pldl1keep, [{wb}]",
                "dmb ishld",
                wb = in(reg) wb_at,
                options(nostack, preserves_flags),
            );
            read_full64_ldp(wb_at, seq)
        }
    }

    #[inline(never)]
    unsafe fn poll_frontier_prfm_l1(poll_at: *const Cell, prfm_target: *const Cell, seq: u64) -> PollRes {
        unsafe {
            asm!("prfm pldl1keep, [{p}]", p = in(reg) prfm_target, options(nostack, preserves_flags));
        }
        let head = unsafe { load_head_ldr(poll_at.cast()) };
        if head != seq {
            return PollRes::Empty;
        }
        unsafe {
            asm!("dmb ishld", options(nostack, preserves_flags));
            read_full64_ldp(poll_at, seq)
        }
    }

    #[inline(never)]
    unsafe fn poll_full64_nodmb_ldp(at: *const Cell, seq: u64) -> PollRes {
        let head = unsafe { load_head_ldr(at.cast()) };
        if head != seq {
            return PollRes::Empty;
        }
        let (w1, w2, w3, w4, w5, w6, w7): (u64, u64, u64, u64, u64, u64, u64);
        unsafe {
            asm!(
                "ldr {w1}, [{p}, #8]",
                "ldp {w2}, {w3}, [{p}, #16]",
                "ldp {w4}, {w5}, [{p}, #32]",
                "ldp {w6}, {w7}, [{p}, #48]",
                p = in(reg) at,
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
        if w7 != seq {
            return PollRes::Partial;
        }
        let cell = unsafe { std::mem::transmute::<[u64; 8], Cell>([head, w1, w2, w3, w4, w5, w6, w7]) };
        if !cell.payload_matches(seq) {
            return PollRes::Partial;
        }
        PollRes::Complete(cell)
    }

    #[inline(never)]
    unsafe fn poll_full64_1pass_ldp(at: *const Cell, seq: u64) -> PollRes {
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
        if w0 != seq {
            return PollRes::Empty;
        }
        if w7 != seq {
            return PollRes::Partial;
        }
        let cell = unsafe { std::mem::transmute::<[u64; 8], Cell>([w0, w1, w2, w3, w4, w5, w6, w7]) };
        if !cell.payload_matches(seq) {
            return PollRes::Partial;
        }
        PollRes::Complete(cell)
    }

    #[inline(never)]
    unsafe fn poll_full64_1pass_ldpq(at: *const Cell, seq: u64) -> PollRes {
        let mut words = [0u64; 8];
        unsafe {
            asm!(
                "ldp q0, q1, [{p}, #0]",
                "ldp q2, q3, [{p}, #32]",
                "stp q0, q1, [{dst}, #0]",
                "stp q2, q3, [{dst}, #32]",
                p = in(reg) at,
                dst = in(reg) words.as_mut_ptr(),
                out("q0") _,
                out("q1") _,
                out("q2") _,
                out("q3") _,
                options(nostack, preserves_flags),
            );
        }
        if words[0] != seq {
            return PollRes::Empty;
        }
        if words[7] != seq {
            return PollRes::Partial;
        }
        let cell = unsafe { std::mem::transmute::<[u64; 8], Cell>(words) };
        if !cell.payload_matches(seq) {
            return PollRes::Partial;
        }
        PollRes::Complete(cell)
    }

    // -------------------------------------------------------------------------
    // Server Runtime
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

    pub fn main() -> Result<(), Box<dyn Error>> {
        let args = Args::parse();
        if args.restore {
            let changed = restore_defaults(SLOT)?;
            for c in &changed {
                println!("mrv235_card: restored {c}");
            }
            if changed.is_empty() {
                println!("mrv235_card: BAR4 slot {SLOT} and outbound defaults already clean");
            }
            return Ok(());
        }

        pin_cpu(1);
        let changed = restore_defaults(SLOT)?;
        for c in &changed {
            eprintln!("mrv235_card: restored {c}");
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
        println!("mrv235_card: mair_info={}", mair.par_json);

        let mut outbound: Vec<Mutex<OutboundWindow>> = Vec::new();
        if args.wc {
            for w in 0..MAX_WORKERS {
                let region_idx = OUTBOUND_REGIONS - 1 - w; // 255, 254, 253, 252
                let ow = OutboundWindow::claim(region_idx, 0, 1, true)?;
                outbound.push(Mutex::new(ow));
            }
        }
        let outbound = Arc::new(outbound);

        // Claim ONLY BAR4 slot 15 (`inline::SLOT = 15`)! Dropped before `mair` and `buf`.
        let window = Bar4Window::claim(SLOT, buf.pa)?;
        let ctrl_ready = unsafe { &*(buf.ptr.add(READY_OFFSET) as *const AtomicU64) };
        let ctrl_nonce = unsafe { &*(buf.ptr.add(NONCE_OFFSET) as *const AtomicU64) };
        ctrl_ready.store(READY, Ordering::Release);
        println!(
            "mrv235_card: BAR4 slot {SLOT} -> pa {:#x} (was {:#x}); wc={}; hosts lease the slot at {}; rpc at {}",
            buf.pa, window.previous(), args.wc, args.control, args.rpc
        );

        let active_cpl = Arc::new(AtomicUsize::new(1));
        let active_ring_cells = Arc::new(AtomicUsize::new(4096));
        let active_map_mode = Arc::new(AtomicUsize::new(0));
        let active_access_mode = Arc::new(AtomicUsize::new(0));
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
                let active_cpl = active_cpl.clone();
                let active_ring_cells = active_ring_cells.clone();
                let active_map_mode = active_map_mode.clone();
                let active_access_mode = active_access_mode.clone();
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
                        let cpl = active_cpl.load(Ordering::Acquire);
                        let ring_cells = active_ring_cells.load(Ordering::Acquire);
                        let mm_u = active_map_mode.load(Ordering::Acquire);
                        let am_u = active_access_mode.load(Ordering::Acquire);
                        if w >= cpl || am_u == AccessMode::None as usize {
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
                            3 => MapMode::InnerNcIsh,
                            4 => MapMode::InnerNcOsh,
                            5 => MapMode::InnerNcNsh,
                            _ => MapMode::WbHuge,
                        };
                        let poll_base = mair.ptr_for(map_mode).expect("map_mode validated in PREPARE");
                        let wb_base = mair.wb_64k;
                        let rtarget = *my_reply.lock().unwrap();
                        my_ready.store(cur_gen, Ordering::Release);

                        let mut next: u64 = w as u64 + 1;
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
                            let off = idx * CELL_STRIDE_BYTES;
                            let poll_at = unsafe { poll_base.add(off).cast::<Cell>() };
                            let wb_at = unsafe { wb_base.add(off).cast::<Cell>() };

                            let got = unsafe {
                                match am_u {
                                    1 => poll_full64_ldar(poll_at, next),
                                    2 => poll_full64_ldr_dmb(poll_at, next),
                                    3 => poll_full64_ldp(poll_at, next),
                                    4 => poll_hdr16_ldp(poll_at, next),
                                    5 => poll_dual_va_wb(poll_at, wb_at, next, false),
                                    6 => poll_dual_va_wb(poll_at, wb_at, next, true),
                                    7 => {
                                        let next_idx = ((next + cpl as u64 - 1) as usize) % ring_cells;
                                        let next_poll = poll_base.add(next_idx * CELL_STRIDE_BYTES).cast::<Cell>();
                                        poll_post_prfm_l3(poll_at, next_poll, next)
                                    }
                                    8 => poll_post_prfm_l1_nc(poll_at, next),
                                    9 => poll_post_prfm_l1_wb(poll_at, wb_at, next),
                                    10 => poll_frontier_prfm_l1(poll_at, wb_at, next),
                                    11 => poll_frontier_prfm_l1(poll_at, poll_at, next),
                                    12 => poll_full64_nodmb_ldp(poll_at, next),
                                    13 => poll_full64_1pass_ldp(poll_at, next),
                                    14 => poll_full64_1pass_ldpq(poll_at, next),
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
                                        rt.taken.store(local_taken, Ordering::Relaxed);
                                        rt.next_seq.store(next, Ordering::Relaxed);
                                    }
                                }
                                PollRes::Partial => {
                                    local_partial += 1;
                                }
                                PollRes::Empty => {
                                    if in_busy_burst {
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
                        "INFO" => format!("{}\n", mair.par_json),
                        // PREPARE <cards> <ring_cells> <map_mode> <access_mode> <reply_iova_hex>
                        "PREPARE" if parts.len() == 6 => {
                            let cpl: usize = parts[1].parse().unwrap_or(0);
                            let r: usize = parts[2].parse().unwrap_or(0);
                            let mm = MapMode::parse(parts[3]);
                            let am = AccessMode::parse(parts[4]);
                            let riova = u64::from_str_radix(parts[5].trim_start_matches("0x"), 16).unwrap_or(u64::MAX);
                            if !matches!(cpl, 1 | 2 | 4)
                                || r == 0
                                || r * CELL_STRIDE_BYTES > (SLOT_BYTES - 65536)
                                || mm.is_err()
                                || am.is_err()
                                || riova == u64::MAX
                                || (riova != 0 && !wc_enabled)
                            {
                                format!("ERR bad PREPARE args: {}\n", line.trim())
                            } else {
                                let mm = mm.unwrap();
                                let am = am.unwrap();
                                if mair.ptr_for(mm).is_none() {
                                    format!("ERR map_mode {} unavailable (custom_active={})\n", mm.as_str(), mair.info.custom_active)
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
                                        for w in 0..cpl {
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
                                        for w in cpl..MAX_WORKERS {
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
                                        // Zero ring cells (NEVER touching control page at SLOT_BYTES - 65536!)
                                        unsafe { ptr::write_bytes(buf.ptr, 0, r * CELL_STRIDE_BYTES); }
                                        // Flush & invalidate ALL 4 MiB out of ALL 24 cores' L1D/L2 via DC CIVAC!
                                        if let Err(e) = mair.clean_inval_slot() {
                                            format!("ERR clean_inval: {e}\n")
                                        } else {
                                            for w in 0..MAX_WORKERS {
                                                worker_rt[w].reset(w as u64 + 1);
                                            }
                                            active_cpl.store(cpl, Ordering::Release);
                                            active_ring_cells.store(r, Ordering::Release);
                                            active_map_mode.store(mm as usize, Ordering::Release);
                                            active_access_mode.store(am as usize, Ordering::Release);
                                            *cur_gen += 1;
                                            run_gen.store(*cur_gen, Ordering::Release);
                                            for w in 0..MAX_WORKERS {
                                                while worker_ready_gen[w].load(Ordering::Acquire) != *cur_gen {
                                                    thread::sleep(Duration::from_micros(50));
                                                }
                                            }
                                            format!("OK cpl={cpl} ring_cells={r} map={} access={} riova={riova:#x}\n", mm.as_str(), am.as_str())
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
                                let (pmu_json, _) = pmu.format_delta_json(&s0, &s1);
                                let cpl = active_cpl.load(Ordering::Acquire);
                                let mut w_json = Vec::with_capacity(cpl);
                                for w in 0..cpl {
                                    let td = worker_rt[w].taken.load(Ordering::Relaxed).saturating_sub(pmu_taken0[w]);
                                    let ed = worker_rt[w].empty_polls.load(Ordering::Relaxed).saturating_sub(pmu_empty0[w]);
                                    let bd = worker_rt[w].busy_polls.load(Ordering::Relaxed).saturating_sub(pmu_busy0[w]);
                                    w_json.push(format!(
                                        "{{\"worker\":{w},\"cpu\":{},\"taken_win\":{td},\"empty_win\":{ed},\"busy_win\":{bd},\"corrupt\":{},\"partial\":{}}}",
                                        WORKER_CPUS[w],
                                        worker_rt[w].corrupt.load(Ordering::Relaxed),
                                        worker_rt[w].partial.load(Ordering::Relaxed),
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
                                    "{{\"worker\":{w},\"cpu\":{},\"taken\":{},\"next\":{},\"corrupt\":{},\"partial\":{},\"empty\":{},\"busy\":{}}}",
                                    WORKER_CPUS[w],
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
        drop(mair);
        res
    }
}
