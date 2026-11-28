//! Shared host/card runtime setup helpers: zero-page-fault memory locking
//! (`mlockall`), NUMA `MPOL_BIND` pinning, thread CPU affinity, and `getrusage`
//! page-fault audits.

use std::io;
use std::mem::MaybeUninit;

/// Snapshot of process minor/major page faults from `getrusage(RUSAGE_SELF)`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PageFaults {
    pub minor: u64,
    pub major: u64,
}

impl PageFaults {
    pub fn snapshot() -> io::Result<Self> {
        let mut ru = MaybeUninit::<libc::rusage>::zeroed();
        let rc = unsafe { libc::getrusage(libc::RUSAGE_SELF, ru.as_mut_ptr()) };
        if rc != 0 {
            return Err(io::Error::last_os_error());
        }
        let ru = unsafe { ru.assume_init() };
        Ok(Self { minor: ru.ru_minflt.max(0) as u64, major: ru.ru_majflt.max(0) as u64 })
    }

    pub fn delta_since(self, earlier: PageFaults) -> PageFaults {
        PageFaults {
            minor: self.minor.saturating_sub(earlier.minor),
            major: self.major.saturating_sub(earlier.major),
        }
    }
}

/// Lock current and future process pages in RAM (`mlockall(MCL_CURRENT | MCL_FUTURE)`).
pub fn lock_all_memory() -> io::Result<()> {
    let rc = unsafe { libc::mlockall(libc::MCL_CURRENT | libc::MCL_FUTURE) };
    if rc == 0 { Ok(()) } else { Err(io::Error::last_os_error()) }
}

/// Bind future allocations of this thread to NUMA `node` via `set_mempolicy(MPOL_BIND)`.
pub fn bind_numa_node(node: u32) -> io::Result<()> {
    const MPOL_BIND: libc::c_int = 2;
    if node >= 64 {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "NUMA node >= 64"));
    }
    let mask: libc::c_ulong = 1u64.wrapping_shl(node) as libc::c_ulong;
    let rc = unsafe {
        libc::syscall(
            libc::SYS_set_mempolicy,
            MPOL_BIND,
            &mask as *const libc::c_ulong,
            64usize,
        )
    };
    if rc == 0 { Ok(()) } else { Err(io::Error::last_os_error()) }
}

/// Pin the calling thread to a single logical CPU core.
pub fn pin_current_thread_cpu(cpu: usize) -> io::Result<()> {
    let mut set: libc::cpu_set_t = unsafe { std::mem::zeroed() };
    unsafe {
        libc::CPU_ZERO(&mut set);
        libc::CPU_SET(cpu, &mut set);
    }
    let rc = unsafe { libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set) };
    if rc == 0 { Ok(()) } else { Err(io::Error::last_os_error()) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_fault_snapshot_is_monotonic() {
        let a = PageFaults::snapshot().expect("getrusage");
        let b = PageFaults::snapshot().expect("getrusage");
        let d = b.delta_since(a);
        assert!(b.minor >= a.minor);
        assert!(b.major >= a.major);
        assert_eq!(d.minor, b.minor - a.minor);
    }
}
