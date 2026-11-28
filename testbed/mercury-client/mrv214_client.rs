//! MRV-214 mercury multi-core 100GbE traffic generator (zero external crates, zero root).
//!
//! Compiles directly on `mercury` (`mlx5_2` / `ens6f1np1`, NUMA 0) with:
//!   gcc -O3 -Wall -Wextra -c mrv214_verbs_shim.c -o mrv214_verbs_shim.o
//!   rustc -O --edition 2024 mrv214_client.rs -C link-arg=mrv214_verbs_shim.o -C link-arg=-libverbs -o mrv214_client
//!
//! Supports all three MRV-214 (E6) frame tiers:
//!   - `uc64`: `IBV_QPT_UC` (0 B payload -> exact 64.0 B L2 frames, HW 24-bit BTH.PSN + ICRC + FCS)
//!   - `ud`  : `IBV_QPT_UD` (P B payload -> exact 70 + P B L2 frames; e.g. 184 B -> 254 B L2, 1024 B -> 1094 B L2)
//!   - `udp` : Linux `SOCK_DGRAM` + `UDP_SEGMENT` GSO (with automatic `sendmmsg` fallback;
//!             1454 B payload -> exact 1500.0 B L2 frames with deterministic `src_port` per flow for CN106 RSS)
//!
//! Payload layout matches `testbed::mrv214` bit-for-bit:
//!   `[seq: u64, cookie(stream, seq, run_gen_lo16), meta(stream, plen, run_gen_lo16), ..., mid_sig, tail]`

use std::ffi::{CString, c_char, c_int, c_void};
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

const SLOT_COUNT: u32 = 2048;
const SLOT_MASK: u32 = SLOT_COUNT - 1;
const UC64_BASE_QPN: u32 = 0x0001_0000;
const UD_CARD_QPN: u32 = 0x0001_2345;
const UDP_DST_PORT: u16 = 14000;
const UDP_SRC_PORT_BASE: u16 = 20000;
const MAX_THREADS: usize = 32;
const MID_XOR: u64 = 0x5555_AAAA_5555_AAAA;
const MAX_UDP_GSO_SEGS: usize = 44; // 44 * 1454 = 63,976 B < 64 KiB IP datagram limit

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct PhyStats {
    tx_packets_phy: u64,
    tx_bytes_phy: u64,
    rx_packets_phy: u64,
    rx_bytes_phy: u64,
    tx_discards_phy: u64,
    rx_crc_errors_phy: u64,
    tx_global_pause: u64,
    rx_global_pause: u64,
}

#[cfg(not(test))]
unsafe extern "C" {
    fn mrv214_open(dev_name: *const c_char, sgid_idx: c_int, dst_ip_str: *const c_char) -> *mut c_void;
    fn mrv214_close(c: *mut c_void);
    fn mrv214_chan_create(
        c: *mut c_void,
        is_uc: c_int,
        flow_label: u32,
        remote_qpn: u32,
        slot_count: u32,
        slot_stride: u32,
        want_inline: u32,
    ) -> *mut c_void;
    fn mrv214_chan_buf(ch: *mut c_void) -> *mut u8;
    fn mrv214_chan_max_inline(ch: *mut c_void) -> u32;
    fn mrv214_chan_destroy(ch: *mut c_void);
    fn mrv214_chan_post_batch(
        ch: *mut c_void,
        start_slot: u32,
        slot_mask: u32,
        count: u32,
        payload_len: u32,
        wr_id_tag: u64,
    ) -> c_int;
    fn mrv214_chan_poll_cq(ch: *mut c_void, max_wc: c_int, err_out: *mut u64) -> c_int;
    fn mrv214_udp_open(
        src_ip_str: *const c_char,
        src_port: u16,
        dst_ip_str: *const c_char,
        dst_port: u16,
        want_gso: c_int,
    ) -> *mut c_void;
    fn mrv214_udp_send_gso(u: *mut c_void, buf: *const u8, total_len: u32, gso_size: u16) -> c_int;
    fn mrv214_udp_send_mmsg(u: *mut c_void, buf: *const u8, seg_len: u32, count: u32) -> c_int;
    fn mrv214_udp_close(u: *mut c_void);
    fn mrv214_ethtool_open(ifname: *const c_char) -> *mut c_void;
    fn mrv214_ethtool_read(et: *mut c_void, out: *mut PhyStats) -> c_int;
    fn mrv214_ethtool_close(et: *mut c_void);
    fn sched_setaffinity(pid: c_int, cpusetsize: usize, mask: *const u8) -> c_int;
}

#[cfg(test)]
mod mock_ffi {
    use super::{PhyStats, c_char, c_int, c_void};
    pub unsafe fn mrv214_open(_: *const c_char, _: c_int, _: *const c_char) -> *mut c_void { std::ptr::null_mut() }
    pub unsafe fn mrv214_close(_: *mut c_void) {}
    pub unsafe fn mrv214_chan_create(_: *mut c_void, _: c_int, _: u32, _: u32, _: u32, _: u32, _: u32) -> *mut c_void { std::ptr::null_mut() }
    pub unsafe fn mrv214_chan_buf(_: *mut c_void) -> *mut u8 { std::ptr::null_mut() }
    pub unsafe fn mrv214_chan_max_inline(_: *mut c_void) -> u32 { 0 }
    pub unsafe fn mrv214_chan_destroy(_: *mut c_void) {}
    pub unsafe fn mrv214_chan_post_batch(_: *mut c_void, _: u32, _: u32, _: u32, _: u32, _: u64) -> c_int { 0 }
    pub unsafe fn mrv214_chan_poll_cq(_: *mut c_void, _: c_int, _: *mut u64) -> c_int { 0 }
    pub unsafe fn mrv214_udp_open(_: *const c_char, _: u16, _: *const c_char, _: u16, _: c_int) -> *mut c_void { std::ptr::null_mut() }
    pub unsafe fn mrv214_udp_send_gso(_: *mut c_void, _: *const u8, _: u32, _: u16) -> c_int { 0 }
    pub unsafe fn mrv214_udp_send_mmsg(_: *mut c_void, _: *const u8, _: u32, _: u32) -> c_int { 0 }
    pub unsafe fn mrv214_udp_close(_: *mut c_void) {}
    pub unsafe fn mrv214_ethtool_open(_: *const c_char) -> *mut c_void { std::ptr::null_mut() }
    pub unsafe fn mrv214_ethtool_read(_: *mut c_void, _: *mut PhyStats) -> c_int { 0 }
    pub unsafe fn mrv214_ethtool_close(_: *mut c_void) {}
    pub unsafe fn sched_setaffinity(_: c_int, _: usize, _: *const u8) -> c_int { 0 }
}
#[cfg(test)]
use mock_ffi::*;

fn pin_cpu(cpu: usize) {
    let mut mask = [0u8; 128];
    if cpu < 1024 {
        mask[cpu / 8] |= 1u8 << (cpu % 8);
        unsafe {
            sched_setaffinity(0, mask.len(), mask.as_ptr());
        }
    }
}

#[inline(always)]
fn rdtsc() -> u64 {
    #[cfg(target_arch = "x86_64")]
    unsafe {
        std::arch::x86_64::_rdtsc()
    }
    #[cfg(not(target_arch = "x86_64"))]
    {
        0
    }
}

fn measure_tsc_hz() -> f64 {
    let t0 = Instant::now();
    let c0 = rdtsc();
    thread::sleep(Duration::from_millis(50));
    let c1 = rdtsc();
    (c1 - c0) as f64 / t0.elapsed().as_secs_f64()
}

/// Bit-for-bit identical to `testbed::mrv214::pack_meta`.
#[inline(always)]
const fn pack_meta(stream_id: u32, payload_bytes: u16, run_gen_lo16: u16) -> u64 {
    ((stream_id as u64) << 32) | ((payload_bytes as u64) << 16) | (run_gen_lo16 as u64)
}

/// Bit-for-bit identical to `testbed::mrv214::merc_cookie`.
#[inline(always)]
const fn merc_cookie(stream_id: u32, seq: u64, run_gen_lo16: u16) -> u64 {
    let x = seq.wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ ((stream_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ ((run_gen_lo16 as u64).wrapping_mul(0x94d0_49bb_1331_11eb));
    x ^ 0x55aa_55aa_0f0f_f0f0
}

/// Bit-for-bit identical to `testbed::mrv214::merc_tail`.
#[inline(always)]
const fn merc_tail(stream_id: u32, seq: u64, run_gen_lo16: u16) -> u64 {
    let x = seq.wrapping_mul(0x6c62_272e_07bb_0142)
        ^ ((stream_id as u64).wrapping_mul(0x517c_c1b7_2722_0a95))
        ^ ((run_gen_lo16 as u64).wrapping_mul(0x2545_f491_4f6c_dd1d));
    let v = x ^ 0xa5a5_5a5a_f0f0_0f0f;
    if v == 0 { 1 } else { v }
}

/// Stamp self-verifying `[seq, cookie, meta, ..., mid_sig, tail]` matching `testbed::mrv214::stamp_payload`.
#[inline(always)]
fn stamp_payload(buf: &mut [u8], stream_id: u32, seq: u64, run_gen_lo16: u16) {
    let plen = buf.len();
    debug_assert!(plen >= 32);
    let ck = merc_cookie(stream_id, seq, run_gen_lo16);
    let meta = pack_meta(stream_id, plen as u16, run_gen_lo16);
    let tl = merc_tail(stream_id, seq, run_gen_lo16);
    unsafe {
        let p = buf.as_mut_ptr();
        p.cast::<u64>().write_unaligned(seq);
        p.add(8).cast::<u64>().write_unaligned(ck);
        p.add(16).cast::<u64>().write_unaligned(meta);
        if plen >= 128 {
            let mid = (plen / 2) & !7;
            p.add(mid).cast::<u64>().write_unaligned(ck ^ MID_XOR);
        }
        p.add(plen - 8).cast::<u64>().write_unaligned(tl);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TxMode {
    Uc64,
    Ud,
    Udp,
}

impl TxMode {
    fn parse(s: &str) -> Option<Self> {
        match s {
            "uc64" | "uc" => Some(Self::Uc64),
            "ud" => Some(Self::Ud),
            "udp" => Some(Self::Udp),
            _ => None,
        }
    }

    const fn as_str(self) -> &'static str {
        match self {
            Self::Uc64 => "uc64",
            Self::Ud => "ud",
            Self::Udp => "udp",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
struct RunConfig {
    mode: TxMode,
    threads: usize,
    payload_len: u32,
    batch: u32,
    rate_mpps: f64,
    run_gen: u64,
    use_gso: bool,
    flow_base: u32,
    flows: Vec<u32>,
}

impl RunConfig {
    fn parse(rest: &str) -> Result<Self, String> {
        let mut cfg = Self {
            mode: TxMode::Udp,
            threads: 16,
            payload_len: 1454,
            batch: 32,
            rate_mpps: 0.0,
            run_gen: 1,
            use_gso: true,
            flow_base: 1,
            flows: Vec::new(),
        };
        for tok in rest.split_whitespace() {
            if let Some((k, v)) = tok.split_once('=') {
                match k {
                    "mode" => {
                        cfg.mode = TxMode::parse(v).ok_or_else(|| format!("bad mode={v}"))?;
                    }
                    "threads" => {
                        cfg.threads = v.parse::<usize>().unwrap_or(16).clamp(1, MAX_THREADS);
                    }
                    "payload" => {
                        cfg.payload_len = v.parse().unwrap_or(cfg.payload_len);
                    }
                    "batch" => {
                        cfg.batch = v.parse::<u32>().unwrap_or(32).clamp(1, 64);
                    }
                    "rate_mpps" => {
                        cfg.rate_mpps = v.parse().unwrap_or(0.0);
                    }
                    "run_gen" => {
                        cfg.run_gen = v.parse().unwrap_or(1);
                    }
                    "gso" => {
                        cfg.use_gso = v == "1" || v == "true";
                    }
                    "flow_base" => {
                        cfg.flow_base = v.parse().unwrap_or(1);
                    }
                    "flows" => {
                        cfg.flows = v.split(',').filter_map(|x| x.parse::<u32>().ok()).collect();
                    }
                    _ => {}
                }
            }
        }
        if cfg.mode == TxMode::Uc64 {
            cfg.payload_len = 0;
        } else if cfg.payload_len < 32 {
            return Err(format!("payload={} must be >= 32 for {:?}", cfg.payload_len, cfg.mode));
        }
        Ok(cfg)
    }

    fn udp_gso_segs(&self) -> usize {
        let max_by_64k = (65000usize / (self.payload_len.max(1) as usize)).max(1);
        (self.batch as usize).min(MAX_UDP_GSO_SEGS).min(max_by_64k)
    }

    fn flow_for_thread(&self, t: usize) -> u32 {
        if !self.flows.is_empty() {
            self.flows[t % self.flows.len()] & 0xfffff
        } else {
            (self.flow_base.wrapping_add((t as u32).wrapping_mul(0x9e377)) ^ ((t as u32) * 257)) & 0xfffff
        }
    }
}

#[repr(align(64))]
struct ThreadStats {
    tx_pkts: AtomicU64,
    cqe_err: AtomicU64,
    post_err: AtomicU64,
    gso_fallbacks: AtomicU64,
}

impl ThreadStats {
    fn new() -> Self {
        Self {
            tx_pkts: AtomicU64::new(0),
            cqe_err: AtomicU64::new(0),
            post_err: AtomicU64::new(0),
            gso_fallbacks: AtomicU64::new(0),
        }
    }
}

struct ActiveWorkers {
    stop: Arc<AtomicBool>,
    active: Arc<AtomicBool>,
    stats: Arc<Vec<ThreadStats>>,
    handles: Vec<JoinHandle<()>>,
    max_inline: u32,
}

struct SnapState {
    t_app: Instant,
    t_phy: Instant,
    app_pkts: u64,
    cqe_err: u64,
    post_err: u64,
    gso_fallbacks: u64,
    phy: PhyStats,
}

fn spawn_workers(
    ctx_ptr: *mut c_void,
    src_ip: &str,
    dst_ip: &str,
    cfg: RunConfig,
    tsc_hz: f64,
) -> Result<ActiveWorkers, String> {
    let stop = Arc::new(AtomicBool::new(false));
    let active = Arc::new(AtomicBool::new(false));
    let mut stats_vec = Vec::with_capacity(cfg.threads);
    for _ in 0..cfg.threads {
        stats_vec.push(ThreadStats::new());
    }
    let stats = Arc::new(stats_vec);

    match cfg.mode {
        TxMode::Uc64 | TxMode::Ud => {
            let is_uc = cfg.mode == TxMode::Uc64;
            let slot_stride: u32 = ((cfg.payload_len.max(64) + 63) & !63).max(64);
            let want_inline = if cfg.payload_len <= 188 { cfg.payload_len } else { 0 };
            let mut chans = Vec::with_capacity(cfg.threads);
            let mut max_inline = 0u32;
            for t in 0..cfg.threads {
                let flow_label = cfg.flow_for_thread(t);
                let remote_qpn = if is_uc { UC64_BASE_QPN + t as u32 } else { UD_CARD_QPN };
                let ch = unsafe {
                    mrv214_chan_create(
                        ctx_ptr,
                        if is_uc { 1 } else { 0 },
                        flow_label,
                        remote_qpn,
                        SLOT_COUNT,
                        slot_stride,
                        want_inline,
                    )
                };
                if ch.is_null() {
                    for prev in chans {
                        unsafe { mrv214_chan_destroy(prev) };
                    }
                    return Err(format!("mrv214_chan_create failed on thread {t}"));
                }
                max_inline = unsafe { mrv214_chan_max_inline(ch) };
                chans.push(ch);
            }

            let mut handles = Vec::with_capacity(cfg.threads);
            for (t, ch) in chans.into_iter().enumerate() {
                let ch_addr = ch as usize;
                let stop_c = Arc::clone(&stop);
                let active_c = Arc::clone(&active);
                let stats_c = Arc::clone(&stats);
                let cfg_c = cfg.clone();
                let cpu = 2 + (t % 30);
                let h = thread::spawn(move || {
                    pin_cpu(cpu);
                    let ch = ch_addr as *mut c_void;
                    let buf = unsafe { mrv214_chan_buf(ch) };
                    let plen = cfg_c.payload_len as usize;
                    let batch = cfg_c.batch.clamp(1, 64);
                    let max_inflight_batches: u64 = (1024 / batch as u64).max(4);
                    let stream_id = t as u32;
                    let run_gen_lo16 = cfg_c.run_gen as u16;

                    let per_thread_mpps = if cfg_c.rate_mpps > 0.0 {
                        cfg_c.rate_mpps / (cfg_c.threads as f64)
                    } else {
                        0.0
                    };
                    let ticks_per_batch = if per_thread_mpps > 0.0 {
                        (batch as f64 / (per_thread_mpps * 1e6)) * tsc_hz
                    } else {
                        0.0
                    };

                    let mut seq = 1u64;
                    let mut slot = 0u32;
                    let mut inflight_batches = 0u64;
                    let mut cqe_err = 0u64;
                    let mut next_tsc = 0.0f64;
                    let mut was_active = false;

                    while !stop_c.load(Ordering::Relaxed) {
                        if !active_c.load(Ordering::Acquire) {
                            if was_active {
                                while inflight_batches > 0 {
                                    let n = unsafe { mrv214_chan_poll_cq(ch, 32, &mut cqe_err) };
                                    if n > 0 {
                                        inflight_batches = inflight_batches.saturating_sub(n as u64);
                                    } else {
                                        break;
                                    }
                                }
                                if cqe_err > 0 {
                                    stats_c[t].cqe_err.fetch_add(cqe_err, Ordering::Relaxed);
                                    cqe_err = 0;
                                }
                                was_active = false;
                            }
                            thread::sleep(Duration::from_micros(20));
                            continue;
                        }
                        if !was_active {
                            was_active = true;
                            next_tsc = rdtsc() as f64;
                        }

                        if ticks_per_batch > 0.0 {
                            while (rdtsc() as f64) < next_tsc {
                                if !active_c.load(Ordering::Relaxed) || stop_c.load(Ordering::Relaxed) {
                                    break;
                                }
                                std::hint::spin_loop();
                            }
                            next_tsc += ticks_per_batch;
                            let now = rdtsc() as f64;
                            if now - next_tsc > tsc_hz * 0.01 {
                                next_tsc = now;
                            }
                        }

                        while inflight_batches >= max_inflight_batches {
                            let n = unsafe { mrv214_chan_poll_cq(ch, 32, &mut cqe_err) };
                            if n > 0 {
                                inflight_batches = inflight_batches.saturating_sub(n as u64);
                            } else if stop_c.load(Ordering::Relaxed) {
                                break;
                            } else {
                                std::hint::spin_loop();
                            }
                        }
                        if inflight_batches > 0 {
                            let n = unsafe { mrv214_chan_poll_cq(ch, 16, &mut cqe_err) };
                            if n > 0 {
                                inflight_batches = inflight_batches.saturating_sub(n as u64);
                            }
                        }

                        let start_slot = slot;
                        if plen >= 32 {
                            for _ in 0..batch {
                                let sl = (slot & SLOT_MASK) as usize;
                                let p = unsafe { buf.add(sl * slot_stride as usize) };
                                let slice = unsafe { std::slice::from_raw_parts_mut(p, plen) };
                                stamp_payload(slice, stream_id, seq, run_gen_lo16);
                                seq += 1;
                                slot = slot.wrapping_add(1);
                            }
                        } else {
                            slot = slot.wrapping_add(batch);
                            seq += batch as u64;
                        }

                        let rc = unsafe {
                            mrv214_chan_post_batch(ch, start_slot, SLOT_MASK, batch, cfg_c.payload_len, seq)
                        };
                        if rc == 0 {
                            inflight_batches += 1;
                            stats_c[t].tx_pkts.fetch_add(batch as u64, Ordering::Relaxed);
                        } else {
                            stats_c[t].post_err.fetch_add(1, Ordering::Relaxed);
                        }
                        if cqe_err > 0 {
                            stats_c[t].cqe_err.fetch_add(cqe_err, Ordering::Relaxed);
                            cqe_err = 0;
                        }
                    }

                    let deadline = Instant::now() + Duration::from_millis(50);
                    while inflight_batches > 0 && Instant::now() < deadline {
                        let n = unsafe { mrv214_chan_poll_cq(ch, 32, &mut cqe_err) };
                        if n > 0 {
                            inflight_batches = inflight_batches.saturating_sub(n as u64);
                        }
                    }
                    if cqe_err > 0 {
                        stats_c[t].cqe_err.fetch_add(cqe_err, Ordering::Relaxed);
                    }
                    unsafe { mrv214_chan_destroy(ch) };
                });
                handles.push(h);
            }
            Ok(ActiveWorkers { stop, active, stats, handles, max_inline })
        }
        TxMode::Udp => {
            let c_src = CString::new(src_ip).map_err(|_| "NUL in src_ip")?;
            let c_dst = CString::new(dst_ip).map_err(|_| "NUL in dst_ip")?;
            let mut chans = Vec::with_capacity(cfg.threads);
            let mut used_sports = Vec::with_capacity(cfg.threads);
            for t in 0..cfg.threads {
                // Use deterministic src_port derived from flow id so CN106 NIX0
                // IPV4_UDP RSS hashes onto exact probed queues; fall back to
                // ephemeral port if a flow id repeats across threads.
                let flow_id = cfg.flow_for_thread(t);
                let cand_sport = UDP_SRC_PORT_BASE.wrapping_add(flow_id as u16);
                let unique_flow = !cfg.flows.is_empty() && !used_sports.contains(&cand_sport);
                let (sport, dport) = if unique_flow {
                    used_sports.push(cand_sport);
                    (cand_sport, UDP_DST_PORT)
                } else {
                    (0u16, UDP_DST_PORT.wrapping_add((t as u16) % 64))
                };
                let u = unsafe {
                    mrv214_udp_open(
                        c_src.as_ptr(),
                        sport,
                        c_dst.as_ptr(),
                        dport,
                        if cfg.use_gso { 1 } else { 0 },
                    )
                };
                if u.is_null() {
                    for prev in chans {
                        unsafe { mrv214_udp_close(prev) };
                    }
                    return Err(format!("mrv214_udp_open failed on thread {t} (sport={sport})"));
                }
                chans.push(u);
            }

            let mut handles = Vec::with_capacity(cfg.threads);
            for (t, u) in chans.into_iter().enumerate() {
                let u_addr = u as usize;
                let stop_c = Arc::clone(&stop);
                let active_c = Arc::clone(&active);
                let stats_c = Arc::clone(&stats);
                let cfg_c = cfg.clone();
                let cpu = 2 + (t % 30);
                let h = thread::spawn(move || {
                    pin_cpu(cpu);
                    let u = u_addr as *mut c_void;
                    let plen = cfg_c.payload_len as usize;
                    let stream_id = t as u32;
                    let run_gen_lo16 = cfg_c.run_gen as u16;
                    let mut use_gso = cfg_c.use_gso;
                    let gso_segs = cfg_c.udp_gso_segs();
                    let mmsg_segs = (cfg_c.batch as usize).clamp(1, 64);
                    let max_segs = gso_segs.max(mmsg_segs);
                    let mut buf = vec![0x3cu8; max_segs * plen];

                    let per_thread_mpps = if cfg_c.rate_mpps > 0.0 {
                        cfg_c.rate_mpps / (cfg_c.threads as f64)
                    } else {
                        0.0
                    };

                    let mut seq = 1u64;
                    let mut next_tsc = 0.0f64;
                    let mut was_active = false;

                    while !stop_c.load(Ordering::Relaxed) {
                        if !active_c.load(Ordering::Acquire) {
                            was_active = false;
                            thread::sleep(Duration::from_micros(20));
                            continue;
                        }
                        if !was_active {
                            was_active = true;
                            next_tsc = rdtsc() as f64;
                        }

                        let segs = if use_gso { gso_segs } else { mmsg_segs };
                        if per_thread_mpps > 0.0 {
                            let ticks = (segs as f64 / (per_thread_mpps * 1e6)) * tsc_hz;
                            while (rdtsc() as f64) < next_tsc {
                                if !active_c.load(Ordering::Relaxed) || stop_c.load(Ordering::Relaxed) {
                                    break;
                                }
                                std::hint::spin_loop();
                            }
                            next_tsc += ticks;
                            let now = rdtsc() as f64;
                            if now - next_tsc > tsc_hz * 0.01 {
                                next_tsc = now;
                            }
                        }

                        for s in 0..segs {
                            let off = s * plen;
                            stamp_payload(&mut buf[off..off + plen], stream_id, seq, run_gen_lo16);
                            seq += 1;
                        }

                        let sent = if use_gso {
                            let r = unsafe {
                                mrv214_udp_send_gso(u, buf.as_ptr(), (segs * plen) as u32, plen as u16)
                            };
                            if r < 0 {
                                // Fall back cleanly to sendmmsg if UDP_SEGMENT is rejected by NIC/kernel.
                                use_gso = false;
                                stats_c[t].gso_fallbacks.fetch_add(1, Ordering::Relaxed);
                                unsafe { mrv214_udp_send_mmsg(u, buf.as_ptr(), plen as u32, segs as u32) }
                            } else {
                                r
                            }
                        } else {
                            unsafe { mrv214_udp_send_mmsg(u, buf.as_ptr(), plen as u32, segs as u32) }
                        };

                        if sent > 0 {
                            stats_c[t].tx_pkts.fetch_add(sent as u64, Ordering::Relaxed);
                            if (sent as usize) < segs {
                                // Rewind unsent sequence numbers so stream continuity stays intact.
                                seq -= (segs - sent as usize) as u64;
                            }
                        } else {
                            seq -= segs as u64;
                            stats_c[t].post_err.fetch_add(1, Ordering::Relaxed);
                        }
                    }

                    unsafe { mrv214_udp_close(u) };
                });
                handles.push(h);
            }
            Ok(ActiveWorkers { stop, active, stats, handles, max_inline: 0 })
        }
    }
}

fn shutdown_workers(mut w: ActiveWorkers) {
    w.active.store(false, Ordering::Release);
    w.stop.store(true, Ordering::Release);
    for h in w.handles.drain(..) {
        let _ = h.join();
    }
}

fn snap_counters(w: Option<&ActiveWorkers>, et: *mut c_void) -> SnapState {
    let t_app = Instant::now();
    let (mut pkts, mut cqe_err, mut post_err, mut gso_fb) = (0u64, 0u64, 0u64, 0u64);
    if let Some(w) = w {
        for s in w.stats.iter() {
            pkts += s.tx_pkts.load(Ordering::Relaxed);
            cqe_err += s.cqe_err.load(Ordering::Relaxed);
            post_err += s.post_err.load(Ordering::Relaxed);
            gso_fb += s.gso_fallbacks.load(Ordering::Relaxed);
        }
    }
    let mut phy = PhyStats::default();
    let t_phy = Instant::now();
    unsafe {
        mrv214_ethtool_read(et, &mut phy);
    }
    SnapState { t_app, t_phy, app_pkts: pkts, cqe_err, post_err, gso_fallbacks: gso_fb, phy }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut port: u16 = 7557;
    let mut dev = "mlx5_2".to_string();
    let mut ifname = "ens6f1np1".to_string();
    let mut src_ip = "10.10.100.1".to_string();
    let mut dst_ip = "10.10.100.2".to_string();
    let mut sgid_idx: i32 = 3;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--port" => { port = args[i + 1].parse().unwrap(); i += 2; }
            "--dev" => { dev = args[i + 1].clone(); i += 2; }
            "--ifname" => { ifname = args[i + 1].clone(); i += 2; }
            "--src-ip" => { src_ip = args[i + 1].clone(); i += 2; }
            "--dst-ip" => { dst_ip = args[i + 1].clone(); i += 2; }
            "--sgid" => { sgid_idx = args[i + 1].parse().unwrap(); i += 2; }
            _ => { i += 1; }
        }
    }

    pin_cpu(0);
    let tsc_hz = measure_tsc_hz();

    let c_dev = CString::new(dev.as_str()).unwrap();
    let c_ip = CString::new(dst_ip.as_str()).unwrap();
    let ctx = unsafe { mrv214_open(c_dev.as_ptr(), sgid_idx, c_ip.as_ptr()) };
    assert!(!ctx.is_null(), "failed to open ibv device {dev}");

    let c_if = CString::new(ifname.as_str()).unwrap();
    let et = unsafe { mrv214_ethtool_open(c_if.as_ptr()) };
    assert!(!et.is_null(), "failed to open ethtool stats for {ifname}");

    let listener = TcpListener::bind(("0.0.0.0", port)).expect("bind mercury control port");
    println!(
        "MRV214_CLIENT READY port={port} dev={dev} ifname={ifname} src={src_ip} dst={dst_ip} tsc_hz={tsc_hz:.0}"
    );

    let workers_lock: Mutex<Option<ActiveWorkers>> = Mutex::new(None);
    let mut snap0 = snap_counters(None, et);
    let mut active_start: Option<Instant> = None;
    let mut active_dur_s: f64 = 0.0;

    for stream in listener.incoming() {
        let Ok(mut sock) = stream else { continue };
        let mut reader = BufReader::new(sock.try_clone().unwrap());
        let mut line = String::new();
        if reader.read_line(&mut line).unwrap_or(0) == 0 {
            continue;
        }
        let cmd = line.trim();
        let reply = if cmd == "PING" {
            "{\"ok\":true}".to_string()
        } else if let Some(rest) = cmd.strip_prefix("PROBE_RSS ") {
            let mut mode = TxMode::Ud;
            let mut count: u32 = 128;
            for tok in rest.split_whitespace() {
                if let Some((k, v)) = tok.split_once('=') {
                    match k {
                        "mode" => {
                            if let Some(m) = TxMode::parse(v) {
                                mode = m;
                            }
                        }
                        "count" => count = v.parse().unwrap_or(128).clamp(1, 128),
                        _ => {}
                    }
                }
            }
            let mut sent_flows = 0u32;
            match mode {
                TxMode::Ud | TxMode::Uc64 => {
                    let is_uc = mode == TxMode::Uc64;
                    for fl in 1..=count {
                        let qpn = if is_uc { UC64_BASE_QPN + (fl - 1) } else { UD_CARD_QPN };
                        let plen = if is_uc { 0u32 } else { 40u32 };
                        let ch = unsafe {
                            mrv214_chan_create(ctx, if is_uc { 1 } else { 0 }, fl, qpn, 64, 64, plen)
                        };
                        if ch.is_null() {
                            continue;
                        }
                        if !is_uc {
                            let buf = unsafe { mrv214_chan_buf(ch) };
                            for k in 0..4usize {
                                let slice = unsafe { std::slice::from_raw_parts_mut(buf.add(k * 64), 40) };
                                stamp_payload(slice, fl - 1, (k + 1) as u64, 0);
                            }
                        }
                        if unsafe { mrv214_chan_post_batch(ch, 0, 63, 4, plen, fl as u64) } == 0 {
                            let mut err = 0u64;
                            let t0 = Instant::now();
                            while unsafe { mrv214_chan_poll_cq(ch, 4, &mut err) } == 0
                                && t0.elapsed() < Duration::from_millis(20)
                            {
                                std::hint::spin_loop();
                            }
                            sent_flows += 1;
                        }
                        unsafe { mrv214_chan_destroy(ch) };
                    }
                }
                TxMode::Udp => {
                    let c_src = CString::new(src_ip.as_str()).unwrap();
                    let c_dst = CString::new(dst_ip.as_str()).unwrap();
                    for fl in 1..=count {
                        let sport = UDP_SRC_PORT_BASE.wrapping_add(fl as u16);
                        let u = unsafe { mrv214_udp_open(c_src.as_ptr(), sport, c_dst.as_ptr(), UDP_DST_PORT, 0) };
                        if u.is_null() {
                            continue;
                        }
                        let mut buf = [0u8; 4 * 40];
                        for k in 0..4usize {
                            stamp_payload(&mut buf[k * 40..(k + 1) * 40], fl - 1, (k + 1) as u64, 0);
                        }
                        if unsafe { mrv214_udp_send_mmsg(u, buf.as_ptr(), 40, 4) } > 0 {
                            sent_flows += 1;
                        }
                        unsafe { mrv214_udp_close(u) };
                    }
                }
            }
            format!("{{\"ok\":true,\"mode\":\"{}\",\"probed\":{sent_flows}}}", mode.as_str())
        } else if let Some(rest) = cmd.strip_prefix("CONFIG ") {
            match RunConfig::parse(rest) {
                Ok(cfg) => {
                    let mut guard = workers_lock.lock().unwrap();
                    if let Some(old) = guard.take() {
                        shutdown_workers(old);
                    }
                    match spawn_workers(ctx, &src_ip, &dst_ip, cfg.clone(), tsc_hz) {
                        Ok(w) => {
                            let inl = w.max_inline;
                            *guard = Some(w);
                            format!(
                                "{{\"ok\":true,\"mode\":\"{}\",\"threads\":{},\"payload\":{},\"batch\":{},\"gso\":{},\"max_inline\":{}}}",
                                cfg.mode.as_str(),
                                cfg.threads,
                                cfg.payload_len,
                                cfg.batch,
                                cfg.use_gso,
                                inl
                            )
                        }
                        Err(e) => format!("{{\"ok\":false,\"err\":\"{}\"}}", e.replace('"', "'")),
                    }
                }
                Err(e) => format!("{{\"ok\":false,\"err\":\"{}\"}}", e.replace('"', "'")),
            }
        } else if cmd == "START" {
            let guard = workers_lock.lock().unwrap();
            if let Some(w) = guard.as_ref() {
                if active_start.is_none() {
                    active_start = Some(Instant::now());
                }
                w.active.store(true, Ordering::Release);
                "{\"ok\":true}".to_string()
            } else {
                "{\"ok\":false,\"err\":\"not configured\"}".to_string()
            }
        } else if cmd == "STOP" {
            let guard = workers_lock.lock().unwrap();
            if let Some(w) = guard.as_ref() {
                w.active.store(false, Ordering::Release);
            }
            if let Some(t0) = active_start.take() {
                active_dur_s += t0.elapsed().as_secs_f64();
            }
            "{\"ok\":true}".to_string()
        } else if cmd == "RESET" {
            let guard = workers_lock.lock().unwrap();
            snap0 = snap_counters(guard.as_ref(), et);
            active_dur_s = 0.0;
            if active_start.is_some() {
                active_start = Some(Instant::now());
            }
            "{\"ok\":true}".to_string()
        } else if cmd == "STATS" {
            let guard = workers_lock.lock().unwrap();
            let snap1 = snap_counters(guard.as_ref(), et);
            let dt_app = snap1.t_app.duration_since(snap0.t_app).as_secs_f64().max(1e-9);
            let dt_phy = snap1.t_phy.duration_since(snap0.t_phy).as_secs_f64().max(1e-9);
            let dt_active = (active_dur_s
                + active_start.map(|t0| t0.elapsed().as_secs_f64()).unwrap_or(0.0))
            .max(1e-9);
            let dp_app = snap1.app_pkts.saturating_sub(snap0.app_pkts);
            let d_cqe = snap1.cqe_err.saturating_sub(snap0.cqe_err);
            let d_post = snap1.post_err.saturating_sub(snap0.post_err);
            let d_gso_fb = snap1.gso_fallbacks.saturating_sub(snap0.gso_fallbacks);
            let dp_phy = snap1.phy.tx_packets_phy.saturating_sub(snap0.phy.tx_packets_phy);
            let db_phy = snap1.phy.tx_bytes_phy.saturating_sub(snap0.phy.tx_bytes_phy);
            let d_disc = snap1.phy.tx_discards_phy.saturating_sub(snap0.phy.tx_discards_phy);
            let d_crc = snap1.phy.rx_crc_errors_phy.saturating_sub(snap0.phy.rx_crc_errors_phy);
            let d_tp = snap1.phy.tx_global_pause.saturating_sub(snap0.phy.tx_global_pause);
            let d_rp = snap1.phy.rx_global_pause.saturating_sub(snap0.phy.rx_global_pause);
            let avg_l2 = if dp_phy > 0 { db_phy as f64 / dp_phy as f64 } else { 0.0 };
            let phy_mpps = dp_phy as f64 / dt_active / 1e6;
            let phy_l2_gbps = (db_phy as f64 * 8.0) / dt_active / 1e9;
            let phy_wire_gbps = ((db_phy + dp_phy * 20) as f64 * 8.0) / dt_active / 1e9;
            let app_mpps = dp_app as f64 / dt_active / 1e6;
            format!(
                "{{\"dt_active_s\":{dt_active:.6},\"dt_app_s\":{dt_app:.6},\"dt_phy_s\":{dt_phy:.6},\
                 \"app_tx_pkts\":{dp_app},\"app_tx_mpps\":{app_mpps:.6},\
                 \"cqe_err\":{d_cqe},\"post_err\":{d_post},\"gso_fallbacks\":{d_gso_fb},\
                 \"phy\":{{\"tx_pkts\":{dp_phy},\"tx_bytes\":{db_phy},\"avg_l2_bytes\":{avg_l2:.3},\
                 \"tx_mpps\":{phy_mpps:.6},\"l2_gbps\":{phy_l2_gbps:.6},\"wire_gbps\":{phy_wire_gbps:.6},\
                 \"tx_discards\":{d_disc},\"rx_crc_err\":{d_crc},\"tx_pause\":{d_tp},\"rx_pause\":{d_rp}}}}}"
            )
        } else if cmd == "QUIT" {
            let mut guard = workers_lock.lock().unwrap();
            if let Some(old) = guard.take() {
                shutdown_workers(old);
            }
            let _ = writeln!(sock, "{{\"ok\":true}}");
            break;
        } else {
            "{\"ok\":false,\"err\":\"unknown cmd\"}".to_string()
        };
        let _ = writeln!(sock, "{reply}");
    }

    unsafe {
        mrv214_ethtool_close(et);
        mrv214_close(ctx);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn payload_stamp_matches_mrv214_wire_contract() {
        for &plen in &[32usize, 184, 1024, 1454] {
            let mut buf = vec![0u8; plen];
            stamp_payload(&mut buf, 11, 77_777, 42);
            let seq = u64::from_le_bytes(buf[0..8].try_into().unwrap());
            let ck = u64::from_le_bytes(buf[8..16].try_into().unwrap());
            let meta = u64::from_le_bytes(buf[16..24].try_into().unwrap());
            let tl = u64::from_le_bytes(buf[plen - 8..plen].try_into().unwrap());
            assert_eq!(seq, 77_777);
            assert_eq!(ck, merc_cookie(11, 77_777, 42));
            assert_eq!(meta, pack_meta(11, plen as u16, 42));
            assert_eq!(tl, merc_tail(11, 77_777, 42));
            if plen >= 128 {
                let mid = (plen / 2) & !7;
                let mid_v = u64::from_le_bytes(buf[mid..mid + 8].try_into().unwrap());
                assert_eq!(mid_v, ck ^ MID_XOR);
            }
        }
    }

    #[test]
    fn config_parsing_and_gso_sizing() {
        let c1 = RunConfig::parse("mode=udp threads=16 payload=1454 batch=64 run_gen=9 gso=1 flows=1,2,3,4").unwrap();
        assert_eq!(c1.mode, TxMode::Udp);
        assert_eq!(c1.threads, 16);
        assert_eq!(c1.payload_len, 1454);
        assert_eq!(c1.udp_gso_segs(), 44);
        assert!(c1.udp_gso_segs() * (c1.payload_len as usize) < 65535);
        assert_eq!(c1.flow_for_thread(0), 1);
        assert_eq!(c1.flow_for_thread(5), 2);

        let c2 = RunConfig::parse("mode=uc64 threads=24 payload=999 batch=64").unwrap();
        assert_eq!(c2.mode, TxMode::Uc64);
        assert_eq!(c2.payload_len, 0);

        let c3 = RunConfig::parse("mode=ud threads=16 payload=184 batch=32").unwrap();
        assert_eq!(c3.mode, TxMode::Ud);
        assert_eq!(c3.payload_len, 184);

        assert!(RunConfig::parse("mode=ud payload=16").is_err());
        assert!(RunConfig::parse("mode=invalid").is_err());
    }
}
