//! Intel DSA Portal (wq2.0) helper module for MRV-230:
//! - Single-open shared 4 KiB DWQ portal mmap + DRAIN gate
//! - 100% non-multiplexed `dsa2` hardware PMU (dwq_success / dwq_full / disp_wq / op_memmove / cpl_ok / cpl_err)
//! - Flood (`noop-stride`, `noop-single`, `memmove-64k-stride`, `memmove-64k-single`) and
//!   bounded-credit (`memmove-64k-credit`) MOVDIR64B aggressor loops.

use std::fs::{self, File, OpenOptions};
use std::io;
use std::mem::size_of;
use std::os::fd::RawFd;
use std::os::unix::io::AsRawFd;
use std::ptr;
use std::sync::atomic::{AtomicU64, AtomicU8, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use crate::mmio::{movdir64b, sfence};

pub const DSA_OPCODE_NOOP: u8 = 0x00;
pub const DSA_OPCODE_DRAIN: u8 = 0x02;
pub const DSA_OPCODE_MEMMOVE: u8 = 0x03;
pub const IDXD_OP_FLAG_CRAV: u32 = 0x0004;
pub const IDXD_OP_FLAG_RCR: u32 = 0x0008;
pub const IDXD_OP_FLAG_CC: u32 = 0x0100;

const PHASE_MEASURE: u8 = 2;
const PHASE_STOP: u8 = 3;

#[repr(C, align(64))]
#[derive(Clone, Copy)]
pub struct DsaDesc {
    pub words: [u64; 8],
}

impl DsaDesc {
    pub fn noop(comp_addr: u64, rcr: bool) -> Self {
        let flags = if rcr { IDXD_OP_FLAG_CRAV | IDXD_OP_FLAG_RCR } else { 0 };
        let w0 = (u64::from(flags) << 32) | (u64::from(DSA_OPCODE_NOOP) << 56);
        Self { words: [w0, if rcr { comp_addr } else { 0 }, 0, 0, 0, 0, 0, 0] }
    }

    pub fn drain(comp_addr: u64) -> Self {
        let flags = IDXD_OP_FLAG_CRAV | IDXD_OP_FLAG_RCR;
        let w0 = (u64::from(flags) << 32) | (u64::from(DSA_OPCODE_DRAIN) << 56);
        Self { words: [w0, comp_addr, 0, 0, 0, 0, 0, 0] }
    }

    pub fn memmove(comp_addr: u64, src: u64, dst: u64, len: u32, cc: bool, rcr: bool) -> Self {
        let mut flags = if rcr { IDXD_OP_FLAG_CRAV | IDXD_OP_FLAG_RCR } else { 0 };
        if cc {
            flags |= IDXD_OP_FLAG_CC;
        }
        let w0 = (u64::from(flags) << 32) | (u64::from(DSA_OPCODE_MEMMOVE) << 56);
        Self { words: [w0, if rcr { comp_addr } else { 0 }, src, dst, u64::from(len), 0, 0, 0] }
    }
}

#[repr(C, align(64))]
pub struct DsaComp {
    pub status: u8,
    pub result: u8,
    pub rsvd: u16,
    pub bytes_completed: u32,
    pub fault_addr: u64,
    pub pad: [u64; 6],
}
pub struct DsaPortal {
    _wq_file: File,
    _res2_file: Option<File>,
    ptr: *mut u8,
    len: usize,
}

unsafe impl Send for DsaPortal {}
unsafe impl Sync for DsaPortal {}

impl DsaPortal {
    pub fn open(wq_path: &str, res2_path: &str) -> io::Result<Self> {
        let wq_file = OpenOptions::new().read(true).write(true).open(wq_path)?;
        let len = 4096usize;
        let mut p = unsafe {
            libc::mmap(ptr::null_mut(), len, libc::PROT_WRITE, libc::MAP_SHARED, wq_file.as_raw_fd(), 0)
        };
        let mut res2_file = None;
        if p == libc::MAP_FAILED {
            let rf = OpenOptions::new().read(true).write(true).open(res2_path)?;
            p = unsafe {
                libc::mmap(
                    ptr::null_mut(),
                    len,
                    libc::PROT_READ | libc::PROT_WRITE,
                    libc::MAP_SHARED,
                    rf.as_raw_fd(),
                    0x1000,
                )
            };
            if p == libc::MAP_FAILED {
                return Err(io::Error::last_os_error());
            }
            res2_file = Some(rf);
        }
        Ok(Self { _wq_file: wq_file, _res2_file: res2_file, ptr: p.cast(), len })
    }

    pub fn as_ptr(&self) -> *mut u8 {
        self.ptr
    }

    /// Submits DSA_OPCODE_DRAIN with retries until completion confirms wq2.0 is empty.
    pub fn drain_wq(&self) -> (bool, u8, u64) {
        let mut comps = [
            DsaComp { status: 0, result: 0, rsvd: 0, bytes_completed: 0, fault_addr: 0, pad: [0; 6] },
            DsaComp { status: 0, result: 0, rsvd: 0, bytes_completed: 0, fault_addr: 0, pad: [0; 6] },
            DsaComp { status: 0, result: 0, rsvd: 0, bytes_completed: 0, fault_addr: 0, pad: [0; 6] },
            DsaComp { status: 0, result: 0, rsvd: 0, bytes_completed: 0, fault_addr: 0, pad: [0; 6] },
            DsaComp { status: 0, result: 0, rsvd: 0, bytes_completed: 0, fault_addr: 0, pad: [0; 6] },
            DsaComp { status: 0, result: 0, rsvd: 0, bytes_completed: 0, fault_addr: 0, pad: [0; 6] },
            DsaComp { status: 0, result: 0, rsvd: 0, bytes_completed: 0, fault_addr: 0, pad: [0; 6] },
            DsaComp { status: 0, result: 0, rsvd: 0, bytes_completed: 0, fault_addr: 0, pad: [0; 6] },
        ];
        let t0 = Instant::now();
        for a in 0..comps.len() {
            let desc = DsaDesc::drain((&raw mut comps[a]) as u64);
            sfence();
            unsafe { movdir64b(self.ptr, &desc) };
            sfence();
            let dl = Instant::now() + Duration::from_millis(150);
            while Instant::now() < dl {
                for c in &comps[..=a] {
                    let st = unsafe { ptr::read_volatile(&c.status) };
                    if st != 0 {
                        return (st == 0x01, st, t0.elapsed().as_micros() as u64);
                    }
                }
                std::hint::spin_loop();
            }
        }
        (false, 0, t0.elapsed().as_micros() as u64)
    }
}

impl Drop for DsaPortal {
    fn drop(&mut self) {
        unsafe { libc::munmap(self.ptr.cast(), self.len) };
    }
}

pub fn read_swerror(path: &str) -> String {
    fs::read_to_string(path).map(|s| s.trim().to_string()).unwrap_or_else(|_| "na".into())
}

pub fn active_dsa_engines(dev_idx: usize) -> usize {
    (0..4)
        .filter(|i| {
            fs::read_to_string(format!("/sys/bus/dsa/devices/engine{dev_idx}.{i}/group_id"))
                .map(|s| s.trim() == "0")
                .unwrap_or(false)
        })
        .count()
}

pub const DSA_PMU_NAMES: [&str; 7] = [
    "dwq_success", "dwq_full", "disp_wq", "op_memmove", "op_drain", "cpl_ok", "cpl_err",
];
const DSA_PMU_SPEC: [(u64, u64); 7] = [
    (0, 0x10), (0, 0x20), (1, 0x20), (3, 0x08), (3, 0x04), (4, 0x10), (4, 0x08),
];

pub struct DsaDevPmu {
    fds: [RawFd; 7],
}

impl DsaDevPmu {
    pub fn open(dev: &str, wq_idx: u32) -> io::Result<Self> {
        let ty: u32 = fs::read_to_string(format!("/sys/bus/event_source/devices/{dev}/type"))?
            .trim().parse().map_err(io::Error::other)?;
        let cpu: i32 = fs::read_to_string(format!("/sys/bus/event_source/devices/{dev}/cpumask"))?
            .trim().split([',', '-']).next().unwrap_or("0").parse().map_err(io::Error::other)?;
        let filter = (1u64 << wq_idx) | (0xffu64 << 32) | (0xfu64 << 40) | (0xffu64 << 44) | (0xffu64 << 52);
        let mut fds = [-1 as RawFd; 7];
        for (i, &(cat, ev)) in DSA_PMU_SPEC.iter().enumerate() {
            let mut attr = [0u64; 16];
            attr[0] = (ty as u64) | (128u64 << 32);
            attr[1] = cat | (ev << 4);
            attr[4] = 1 | 2; // TOTAL_TIME_ENABLED | TOTAL_TIME_RUNNING
            attr[5] = 1; // disabled
            attr[7] = filter; // config1
            let fd = unsafe {
                libc::syscall(libc::SYS_perf_event_open, attr.as_ptr(), -1i32, cpu, -1i32, 0u64)
            } as RawFd;
            if fd < 0 {
                let err = io::Error::last_os_error();
                for &old in &fds[..i] {
                    unsafe { libc::close(old) };
                }
                return Err(err);
            }
            if unsafe { libc::ioctl(fd, 0x2400 as libc::c_ulong, 0) } != 0 {
                let err = io::Error::last_os_error();
                for &old in &fds[..=i] {
                    unsafe { libc::close(old) };
                }
                return Err(err);
            }
            fds[i] = fd;
        }
        Ok(Self { fds })
    }

    pub fn snap(&self) -> [[u64; 3]; 7] {
        let mut out = [[0u64; 3]; 7];
        for (i, &fd) in self.fds.iter().enumerate() {
            let n = unsafe { libc::read(fd, out[i].as_mut_ptr().cast(), size_of::<[u64; 3]>()) };
            assert_eq!(n, size_of::<[u64; 3]>() as isize, "short read on dsa2 pmu fd");
        }
        out
    }

    pub fn format_delta(a: &[[u64; 3]; 7], b: &[[u64; 3]; 7]) -> (String, f64) {
        let mut min_pct = 100.0f64;
        let mut parts = Vec::with_capacity(8);
        for i in 0..7 {
            let v = b[i][0].saturating_sub(a[i][0]);
            let en = b[i][1].saturating_sub(a[i][1]);
            let run = b[i][2].saturating_sub(a[i][2]);
            let pct = if en == 0 { 100.0 } else { run as f64 * 100.0 / en as f64 };
            min_pct = min_pct.min(pct);
            parts.push(format!("\"{}\":{v}", DSA_PMU_NAMES[i]));
        }
        parts.push(format!("\"min_pct\":{min_pct:.2}"));
        (format!("{{{}}}", parts.join(",")), min_pct)
    }
}

impl Drop for DsaDevPmu {
    fn drop(&mut self) {
        for &fd in &self.fds {
            if fd >= 0 {
                unsafe {
                    libc::ioctl(fd, 0x2401 as libc::c_ulong, 0);
                    libc::close(fd);
                }
            }
        }
    }
}

fn thread_pf() -> u64 {
    unsafe {
        let mut ru: libc::rusage = std::mem::zeroed();
        if libc::getrusage(libc::RUSAGE_THREAD, &mut ru) == 0 {
            (ru.ru_minflt + ru.ru_majflt) as u64
        } else {
            0
        }
    }
}

pub fn run_dsa_flood_worker(
    tid: usize,
    n_threads: usize,
    portal_ptr: *mut u8,
    desc: DsaDesc,
    single_line: bool,
    disjoint: bool,
    lines_per_core: usize,
    sfence_batch: usize,
    phase: &AtomicU8,
    ready: &AtomicUsize,
    writes_out: &AtomicU64,
    pf_out: &AtomicU64,
) {
    let chunk = if sfence_batch == 0 { 512 } else { sfence_batch };
    let k = lines_per_core.clamp(1, 64);
    let base = (tid * k) & 63;
    let mut local_idx = 0usize;
    let mut idx = ((tid * 64) / n_threads.max(1)) & 63;
    let mut seq = 0u64;
    let _ = thread_pf();
    ready.fetch_add(1, Ordering::Release);
    let mut in_measure = false;
    let mut pf0 = 0u64;
    loop {
        let ph = phase.load(Ordering::Acquire);
        if ph == PHASE_STOP {
            break;
        }
        if !in_measure && ph == PHASE_MEASURE {
            pf0 = thread_pf();
            in_measure = true;
        }
        if single_line {
            for _ in 0..chunk {
                unsafe { movdir64b(portal_ptr, &desc) };
            }
        } else if disjoint {
            if k == 1 {
                let dst = unsafe { portal_ptr.add(base << 6) };
                for _ in 0..chunk {
                    unsafe { movdir64b(dst, &desc) };
                }
            } else {
                for _ in 0..chunk {
                    let line = (base + local_idx) & 63;
                    unsafe { movdir64b(portal_ptr.add(line << 6), &desc) };
                    local_idx += 1;
                    if local_idx == k {
                        local_idx = 0;
                    }
                }
            }
        } else {
            for _ in 0..chunk {
                unsafe { movdir64b(portal_ptr.add((idx & 63) << 6), &desc) };
                idx = idx.wrapping_add(1);
            }
        }
        if sfence_batch > 0 {
            sfence();
        }
        seq += chunk as u64;
        writes_out.store(seq, Ordering::Relaxed);
    }
    let pf1 = thread_pf();
    if in_measure {
        pf_out.store(pf1.saturating_sub(pf0), Ordering::Relaxed);
    }
    sfence();
}

pub fn run_dsa_credit_worker(
    tid: usize,
    portal_ptr: *mut u8,
    descs: &[DsaDesc],
    comp_base: *mut DsaComp,
    phase: &AtomicU8,
    ready: &AtomicUsize,
    writes_out: &AtomicU64,
    comp_out: &AtomicU64,
    pf_out: &AtomicU64,
) -> u64 {
    let w = descs.len();
    let mut line_idx = (tid * 8) & 63;
    let _ = thread_pf();
    // Prime bounded credit window (strictly <= DWQ size across all threads)
    for i in 0..w {
        unsafe { ptr::write_volatile(&mut (*comp_base.add(i)).status, 0) };
    }
    sfence();
    for i in 0..w {
        unsafe { movdir64b(portal_ptr.add((line_idx & 63) << 6), &descs[i]) };
        line_idx = line_idx.wrapping_add(1);
    }
    sfence();
    let mut submitted = w as u64;
    let mut reaped = 0u64;
    let mut errs = 0u64;
    ready.fetch_add(1, Ordering::Release);
    let mut in_measure = false;
    let mut pf0 = 0u64;
    let mut cur = 0usize;
    loop {
        let ph = phase.load(Ordering::Acquire);
        if ph == PHASE_STOP {
            break;
        }
        if !in_measure && ph == PHASE_MEASURE {
            pf0 = thread_pf();
            in_measure = true;
        }
        let st = unsafe { ptr::read_volatile(&(*comp_base.add(cur)).status) };
        if st != 0 {
            if st != 0x01 {
                errs += 1;
            }
            reaped += 1;
            unsafe { ptr::write_volatile(&mut (*comp_base.add(cur)).status, 0) };
            sfence();
            unsafe { movdir64b(portal_ptr.add((line_idx & 63) << 6), &descs[cur]) };
            line_idx = line_idx.wrapping_add(1);
            submitted += 1;
            cur += 1;
            if cur == w {
                cur = 0;
            }
            writes_out.store(submitted, Ordering::Relaxed);
            comp_out.store(reaped, Ordering::Relaxed);
        } else {
            std::hint::spin_loop();
        }
    }
    let pf1 = thread_pf();
    if in_measure {
        pf_out.store(pf1.saturating_sub(pf0), Ordering::Relaxed);
    }
    // Drain remaining in-flight credits cleanly before exiting
    let dl = Instant::now() + Duration::from_millis(1000);
    while reaped < submitted && Instant::now() < dl {
        let st = unsafe { ptr::read_volatile(&(*comp_base.add(cur)).status) };
        if st != 0 {
            if st != 0x01 {
                errs += 1;
            }
            reaped += 1;
            cur += 1;
            if cur == w {
                cur = 0;
            }
        } else {
            std::hint::spin_loop();
        }
    }
    if reaped < submitted {
        errs += submitted - reaped;
    }
    errs
}
