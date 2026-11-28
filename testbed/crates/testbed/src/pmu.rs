//! Shared Linux `perf_event_open` helpers for non-multiplexed hardware PMU
//! sampling on both x86_64 host (core + SPR CHA/IIO/IRP/M2PCIe/IMC/DSA uncore)
//! and aarch64 card (PMUv3 + CN10K TAD/DDR uncore).

use std::fs;
use std::io;
use std::mem::size_of;
use std::os::fd::RawFd;
use std::path::Path;
pub const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
pub const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
pub const PERF_FLAG_FD_CLOEXEC: u64 = 1 << 3;

/// Linux `struct perf_event_attr` prefix (ABI v8, 128 bytes).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct PerfEventAttr {
    pub type_: u32,
    pub size: u32,
    pub config: u64,
    pub sample_period: u64,
    pub sample_type: u64,
    pub read_format: u64,
    pub flags: u64,
    pub wakeup_events: u32,
    pub bp_type: u32,
    pub config1: u64,
    pub config2: u64,
    pub branch_sample_type: u64,
    pub sample_regs_user: u64,
    pub sample_stack_user: u32,
    pub clockid: i32,
    pub sample_regs_intr: u64,
    pub aux_watermark: u32,
    pub sample_max_stack: u16,
    pub reserved_2: u16,
    pub aux_sample_size: u32,
    pub reserved_3: u32,
    pub sig_data: u64,
    pub config3: u64,
}

impl Default for PerfEventAttr {
    fn default() -> Self {
        Self {
            type_: 0,
            size: size_of::<Self>() as u32,
            config: 0,
            sample_period: 0,
            sample_type: 0,
            read_format: PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING,
            flags: 0,
            wakeup_events: 0,
            bp_type: 0,
            config1: 0,
            config2: 0,
            branch_sample_type: 0,
            sample_regs_user: 0,
            sample_stack_user: 0,
            clockid: 0,
            sample_regs_intr: 0,
            aux_watermark: 0,
            sample_max_stack: 0,
            reserved_2: 0,
            aux_sample_size: 0,
            reserved_3: 0,
            sig_data: 0,
            config3: 0,
        }
    }
}

/// Raw reading returned by `read(fd)` when `TOTAL_TIME_ENABLED | TOTAL_TIME_RUNNING` is set.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PerfSample {
    pub value: u64,
    pub time_enabled: u64,
    pub time_running: u64,
}

impl PerfSample {
    /// Compute the delta between `self` (end) and `start`.
    pub fn delta_since(self, start: PerfSample) -> PerfDelta {
        PerfDelta {
            value: self.value.saturating_sub(start.value),
            time_enabled: self.time_enabled.saturating_sub(start.time_enabled),
            time_running: self.time_running.saturating_sub(start.time_running),
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PerfDelta {
    pub value: u64,
    pub time_enabled: u64,
    pub time_running: u64,
}

impl PerfDelta {
    /// True iff the hardware counter ran continuously without multiplexing.
    pub fn is_non_multiplexed(self) -> bool {
        self.time_enabled > 0 && self.time_enabled == self.time_running
    }

    /// Running fraction in `[0.0, 1.0]`.
    pub fn running_ratio(self) -> f64 {
        if self.time_enabled == 0 {
            0.0
        } else {
            self.time_running as f64 / self.time_enabled as f64
        }
    }
}

/// Owned file descriptor for a single Linux `perf_event_open` counter.
pub struct PerfCounter {
    fd: RawFd,
}

impl PerfCounter {
    /// Open a hardware/uncore counter via `SYS_perf_event_open`.
    /// `pid = -1` targets a specific `cpu`; `cpu = -1` targets a specific `pid` (`0` = current thread).
    pub fn open(attr: &PerfEventAttr, pid: i32, cpu: i32, group_fd: RawFd) -> io::Result<Self> {
        let fd = unsafe {
            libc::syscall(
                libc::SYS_perf_event_open,
                attr as *const PerfEventAttr,
                pid as libc::c_int,
                cpu as libc::c_int,
                group_fd as libc::c_int,
                PERF_FLAG_FD_CLOEXEC as libc::c_ulong,
            )
        };
        if fd < 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(Self { fd: fd as RawFd })
        }
    }

    /// Convenience helper for an uncore or raw PMU event on a fixed CPU (`pid = -1`).
    pub fn open_cpu_raw(pmu_type: u32, config: u64, config1: u64, cpu: i32) -> io::Result<Self> {
        let attr = PerfEventAttr { type_: pmu_type, config, config1, ..PerfEventAttr::default() };
        Self::open(&attr, -1, cpu, -1)
    }

    pub fn raw_fd(&self) -> RawFd {
        self.fd
    }

    pub fn read(&self) -> io::Result<PerfSample> {
        let mut s = PerfSample::default();
        let n = unsafe {
            libc::read(self.fd, (&mut s as *mut PerfSample).cast(), size_of::<PerfSample>())
        };
        if n == size_of::<PerfSample>() as isize {
            Ok(s)
        } else if n < 0 {
            Err(io::Error::last_os_error())
        } else {
            Err(io::Error::new(io::ErrorKind::UnexpectedEof, "short perf_event read"))
        }
    }
}

impl Drop for PerfCounter {
    fn drop(&mut self) {
        unsafe {
            libc::close(self.fd);
        }
    }
}

/// Read the dynamic PMU type ID from `/sys/bus/event_source/devices/<name>/type`.
pub fn read_pmu_type(pmu_name: &str) -> io::Result<u32> {
    let p = Path::new("/sys/bus/event_source/devices").join(pmu_name).join("type");
    let s = fs::read_to_string(p)?;
    s.trim()
        .parse::<u32>()
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn perf_delta_checks_multiplexing() {
        let a = PerfSample { value: 100, time_enabled: 1_000, time_running: 1_000 };
        let b = PerfSample { value: 350, time_enabled: 3_000, time_running: 3_000 };
        let d = b.delta_since(a);
        assert_eq!(d.value, 250);
        assert!(d.is_non_multiplexed());
        assert!((d.running_ratio() - 1.0).abs() < 1e-12);

        let mux = PerfSample { value: 500, time_enabled: 5_000, time_running: 4_000 }.delta_since(b);
        assert!(!mux.is_non_multiplexed());
        assert!((mux.running_ratio() - 0.5).abs() < 1e-12);
    }
}
