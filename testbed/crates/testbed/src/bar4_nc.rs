//! Minimal `/dev/bar4_mair` helper for standard Linux `MT_NORMAL_NC (0x44)`
//! mapping of the BAR4 slot-15 response ring (`enable_custom_mair=0`, zero `MAIR_EL1` edit).

use std::error::Error;
use std::fs::{File, OpenOptions};
use std::os::fd::AsRawFd;
use std::ptr;

const BAR4_MAIR_SLOT_BYTES: usize = 4 << 20;
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

pub struct Bar4NcMapping {
    dev: File,
    nc_ptr: *mut u8,
    pub par_el1: u64,
}

unsafe impl Send for Bar4NcMapping {}
unsafe impl Sync for Bar4NcMapping {}

impl Bar4NcMapping {
    /// Open `dev_path` (default `/dev/bar4_mair`), enforce `enable_custom_mair=0`
    /// (`custom_active == 0 && mair_orig == mair_curr && all_cpus_equal == 1`),
    /// bind `(wb_va, 4 MiB)` verifying `phys_out == expected_pa`, mmap
    /// `BAR4_MAIR_MODE_NORMAL_NC`, verify `AT S1E0R` gives `attr == 0x44`, and
    /// clean+invalidate the 4 MiB WB cache lines.
    pub fn open_and_map(
        dev_path: &str,
        wb_va: *mut u8,
        expected_pa: u64,
    ) -> Result<Self, Box<dyn Error>> {
        let dev = OpenOptions::new()
            .read(true)
            .write(true)
            .open(dev_path)
            .map_err(|e| format!("open {dev_path} failed (is bar4_mair.ko loaded?): {e}"))?;
        let fd = dev.as_raw_fd();

        let mut info = Bar4MairInfo::default();
        let rc = unsafe { libc::ioctl(fd, BAR4_MAIR_IOC_GET_INFO, &mut info as *mut Bar4MairInfo) };
        if rc != 0 {
            return Err(format!("BAR4_MAIR_IOC_GET_INFO failed: {}", std::io::Error::last_os_error()).into());
        }
        if info.custom_active != 0 || info.mair_orig != info.mair_curr || info.all_cpus_equal != 1 {
            return Err(format!(
                "REFUSING: Bar4NcMapping requires enable_custom_mair=0 (custom_active={} eq={} orig={:#018x} curr={:#018x})",
                info.custom_active, info.all_cpus_equal, info.mair_orig, info.mair_curr
            )
            .into());
        }

        let mut bind = Bar4MairBind {
            uaddr: wb_va as u64,
            len: BAR4_MAIR_SLOT_BYTES as u64,
            phys_out: 0,
        };
        let rc = unsafe { libc::ioctl(fd, BAR4_MAIR_IOC_BIND, &mut bind as *mut Bar4MairBind) };
        if rc != 0 {
            return Err(format!("BAR4_MAIR_IOC_BIND failed: {}", std::io::Error::last_os_error()).into());
        }
        if bind.phys_out != expected_pa {
            return Err(format!(
                "BAR4_MAIR_IOC_BIND phys mismatch: {:#x} != {:#x}",
                bind.phys_out, expected_pa
            )
            .into());
        }

        let off = (BAR4_MAIR_MODE_NORMAL_NC * BAR4_MAIR_SLOT_BYTES as u64) as libc::off_t;
        let p = unsafe {
            libc::mmap(
                ptr::null_mut(),
                BAR4_MAIR_SLOT_BYTES,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                fd,
                off,
            )
        };
        if p == libc::MAP_FAILED {
            return Err(format!(
                "mmap {dev_path} mode NORMAL_NC failed: {}",
                std::io::Error::last_os_error()
            )
            .into());
        }
        let nc_ptr = p as *mut u8;

        let mut tr = Bar4MairTranslate { uaddr: nc_ptr as u64, par_el1: 0 };
        let rc = unsafe { libc::ioctl(fd, BAR4_MAIR_IOC_TRANSLATE_VA, &mut tr as *mut Bar4MairTranslate) };
        if rc != 0 {
            unsafe { libc::munmap(nc_ptr as *mut libc::c_void, BAR4_MAIR_SLOT_BYTES) };
            return Err(format!("TRANSLATE_VA(normal_nc) failed: {}", std::io::Error::last_os_error()).into());
        }
        let f = tr.par_el1 & 1;
        let attr = ((tr.par_el1 >> 56) & 0xff) as u8;
        let pa = tr.par_el1 & 0x0000_ffff_ffff_f000;
        if f != 0 || pa != expected_pa || attr != 0x44 {
            unsafe { libc::munmap(nc_ptr as *mut libc::c_void, BAR4_MAIR_SLOT_BYTES) };
            return Err(format!(
                "AT S1E0R verify FAILED for normal_nc: par={:#018x} F={f} pa={pa:#x}(exp {expected_pa:#x}) attr={attr:#04x}(exp 0x44)",
                tr.par_el1
            )
            .into());
        }

        let mapping = Self { dev, nc_ptr, par_el1: tr.par_el1 };
        mapping.clean_inval()?;
        Ok(mapping)
    }

    pub fn clean_inval(&self) -> Result<(), Box<dyn Error>> {
        let rc = unsafe { libc::ioctl(self.dev.as_raw_fd(), BAR4_MAIR_IOC_CLEAN_INVAL, 0) };
        if rc != 0 {
            return Err(format!("BAR4_MAIR_IOC_CLEAN_INVAL failed: {}", std::io::Error::last_os_error()).into());
        }
        Ok(())
    }

    pub fn as_ptr(&self) -> *mut u8 {
        self.nc_ptr
    }
}

impl Drop for Bar4NcMapping {
    fn drop(&mut self) {
        if !self.nc_ptr.is_null() {
            unsafe {
                libc::munmap(self.nc_ptr as *mut libc::c_void, BAR4_MAIR_SLOT_BYTES);
            }
        }
    }
}
