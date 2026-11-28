//! Minimal host-side VFIO FLR trigger for VF0 (`0000:99:02.0` on `/dev/vfio/286`).
//! Used by MRV-217 together with kernel-source-verified `SDPX_GBL_CONTROL[2]` (`BPFLR_D=0`)
//! to cleanly restore VF0's `SDPX_OUT_BP_ENX_W1S(0)` bits back to `0x0` without touching
//! `ens1` (`0000:99:00.0`) or rebooting CN106.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv217_vfio_flr runs on x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::ffi::CString;
    use std::fs::OpenOptions;
    use std::io;
    use std::os::fd::AsRawFd;

    const VFIO_SET_IOMMU: libc::c_ulong = 0x3b66;
    const VFIO_TYPE1_IOMMU: libc::c_ulong = 1;
    const VFIO_GROUP_SET_CONTAINER: libc::c_ulong = 0x3b68;
    const VFIO_GROUP_GET_DEVICE_FD: libc::c_ulong = 0x3b6a;
    const VFIO_DEVICE_RESET: libc::c_ulong = 0x3b6f;

    let group_path = std::env::args().nth(1).unwrap_or_else(|| "/dev/vfio/286".to_string());
    let bdf = std::env::args().nth(2).unwrap_or_else(|| "0000:99:02.0".to_string());
    let bdf_c = CString::new(bdf)?;

    let container = OpenOptions::new().read(true).write(true).open("/dev/vfio/vfio")?;
    let group = OpenOptions::new().read(true).write(true).open(&group_path)?;

    let mut c_fd: libc::c_int = container.as_raw_fd();
    if unsafe { libc::ioctl(group.as_raw_fd(), VFIO_GROUP_SET_CONTAINER, &mut c_fd) } < 0 {
        return Err(io::Error::last_os_error().into());
    }
    if unsafe { libc::ioctl(container.as_raw_fd(), VFIO_SET_IOMMU, VFIO_TYPE1_IOMMU) } < 0 {
        return Err(io::Error::last_os_error().into());
    }
    let dev_fd = unsafe { libc::ioctl(group.as_raw_fd(), VFIO_GROUP_GET_DEVICE_FD, bdf_c.as_ptr()) };
    if dev_fd < 0 {
        return Err(io::Error::last_os_error().into());
    }
    let rc = unsafe { libc::ioctl(dev_fd, VFIO_DEVICE_RESET) };
    let err = io::Error::last_os_error();
    unsafe { libc::close(dev_fd) };
    if rc < 0 {
        return Err(err.into());
    }
    println!("{{\"ok\":true,\"vfio_device_reset_rc\":{rc}}}");
    Ok(())
}
