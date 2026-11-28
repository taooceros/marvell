//! MRV-210 mercury multi-core 100GbE traffic generator (zero external crates).
//!
//! Runs on `mercury` NUMA-0 cores (matching ConnectX-5 `mlx5_2` / `ens6f1np1`, zero root).
//! Compiles directly on `mercury` in < 1 s:
//!   gcc -O3 -c mrv210_verbs_shim.c -o mrv210_verbs_shim.o
//!   rustc --edition 2024 -O mrv210_client.rs -C link-arg=mrv210_verbs_shim.o -C link-arg=-libverbs -o mrv210_client
//!
//! Supports:
//!   - `mode=ud`:  Hardware-bypass `IBV_QPT_UD` (RoCEv2, `dport=4791`, wire L2 = `70 + payload` B;
//!                 e.g. `payload=184` -> `254 B` L2 at full 100GbE `45+ Mpps`, `payload=1024` -> `1094 B`,
//!                 and `payload=1428` -> `1498 B` when port active_mtu >= 2048).
//!   - `mode=udp`: Standard non-root Linux IPv4 UDP (`dport=21000`, wire L2 = `46 + payload` B;
//!                 supports EXACT `1500 B` L2 via `payload=1454` with `UDP_SEGMENT` GSO / `sendmmsg`
//!                 at full 100GbE line rate even on standard MTU 1500, and `payload=210` -> `256 B` L2).
//!   - Uniform scaling (`1 -> 2 -> 4 -> 8` CN106/saturn queues) AND exact weighted RSS skew
//!     (`dist=uniform`, `dist=ratio4to1`, `dist=zipf` with configurable `zipf_alpha`) via
//!     per-worker multi-queue channel rotation over probed RSS `flow_label`s / UDP `src_port`s.
//!   - Untouched end-to-end MRV-210 payload verification fields (`seq`, `cookie`, `meta`, `mid_sig`, `tail`)
//!     matching `testbed::mrv210::verify_payload` bit-for-bit.

use std::ffi::{CString, c_char, c_int, c_void};
use std::io::{BufRead, BufReader, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, UdpSocket};
use std::os::fd::AsRawFd;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Instant;

const MAX_QUEUES: usize = 8;
const MAX_THREADS: usize = 24;
const SLOT_COUNT: u32 = 2048;
const SLOT_MASK: u32 = SLOT_COUNT - 1;
const UD_CARD_QPN: u32 = 0x0001_2345;
const UDP_DATA_PORT: u16 = 21000;
const UDP_PROBE_BASE_SPORT: u16 = 30000;
const SCHED_LEN: usize = 128;

const COOKIE_MAGIC: u64 = 0x2100_55AA_0F0F_F0F0;
const TAIL_MAGIC: u64 = 0x2100_A5A5_F0F0_0F0F;
const MID_XOR: u64 = 0x5555_AAAA_5555_AAAA;

// Linux UDP_SEGMENT (GSO) sockopt/cmsg constants
const SOL_UDP: c_int = 17;
const UDP_SEGMENT: c_int = 103;
const SOL_SOCKET: c_int = 1;
const SO_SNDBUF: c_int = 7;
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
    fn mrv210_open(dev_name: *const c_char, sgid_idx: c_int, dst_ip_str: *const c_char) -> *mut c_void;
    fn mrv210_active_mtu(c: *mut c_void) -> u32;
    fn mrv210_close(c: *mut c_void);
    fn mrv210_chan_create(
        c: *mut c_void,
        flow_label: u32,
        remote_qpn: u32,
        slot_count: u32,
        slot_stride: u32,
        want_inline: u32,
    ) -> *mut c_void;
    fn mrv210_chan_buf(ch: *mut c_void) -> *mut u8;
    fn mrv210_chan_max_inline(ch: *mut c_void) -> u32;
    fn mrv210_chan_destroy(ch: *mut c_void);
    fn mrv210_chan_post_batch(
        ch: *mut c_void,
        start_slot: u32,
        slot_mask: u32,
        count: u32,
        payload_len: u32,
        wr_id_tag: u64,
    ) -> c_int;
    fn mrv210_chan_poll_cq(ch: *mut c_void, max_wc: c_int, err_out: *mut u64) -> c_int;
    fn mrv210_ethtool_open(ifname: *const c_char) -> *mut c_void;
    fn mrv210_ethtool_read(et: *mut c_void, out: *mut PhyStats) -> c_int;
    fn mrv210_ethtool_close(et: *mut c_void);

    fn sched_setaffinity(pid: c_int, cpusetsize: usize, mask: *const u8) -> c_int;
    fn setsockopt(fd: c_int, level: c_int, optname: c_int, optval: *const c_void, optlen: u32) -> c_int;
    fn send(fd: c_int, buf: *const c_void, len: usize, flags: c_int) -> isize;
}

#[cfg(test)]
#[allow(dead_code)]
mod test_stubs {
    use super::*;
    pub unsafe fn mrv210_open(_: *const c_char, _: c_int, _: *const c_char) -> *mut c_void { std::ptr::null_mut() }
    pub unsafe fn mrv210_active_mtu(_: *mut c_void) -> u32 { 1024 }
    pub unsafe fn mrv210_close(_: *mut c_void) {}
    pub unsafe fn mrv210_chan_create(_: *mut c_void, _: u32, _: u32, _: u32, _: u32, _: u32) -> *mut c_void { std::ptr::null_mut() }
    pub unsafe fn mrv210_chan_buf(_: *mut c_void) -> *mut u8 { std::ptr::null_mut() }
    pub unsafe fn mrv210_chan_max_inline(_: *mut c_void) -> u32 { 0 }
    pub unsafe fn mrv210_chan_destroy(_: *mut c_void) {}
    pub unsafe fn mrv210_chan_post_batch(_: *mut c_void, _: u32, _: u32, _: u32, _: u32, _: u64) -> c_int { -1 }
    pub unsafe fn mrv210_chan_poll_cq(_: *mut c_void, _: c_int, _: *mut u64) -> c_int { 0 }
    pub unsafe fn mrv210_ethtool_open(_: *const c_char) -> *mut c_void { std::ptr::null_mut() }
    pub unsafe fn mrv210_ethtool_read(_: *mut c_void, _: *mut PhyStats) -> c_int { 0 }
    pub unsafe fn mrv210_ethtool_close(_: *mut c_void) {}
    pub unsafe fn sched_setaffinity(_: c_int, _: usize, _: *const u8) -> c_int { 0 }
    pub unsafe fn setsockopt(_: c_int, _: c_int, _: c_int, _: *const c_void, _: u32) -> c_int { 0 }
    pub unsafe fn send(_: c_int, _: *const c_void, len: usize, _: c_int) -> isize { len as isize }
}
#[cfg(test)]
use test_stubs::*;

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
    thread::sleep(std::time::Duration::from_millis(50));
    let c1 = rdtsc();
    let dt = t0.elapsed().as_secs_f64();
    (c1 - c0) as f64 / dt
}

// ---- Exact Bit-Compatible MRV-210 Payload Encoding (matches `testbed::mrv210`) ----

#[inline(always)]
const fn pack_meta(stream_id: u32, target_q: u16, run_gen: u64) -> u64 {
    ((stream_id as u64) << 32) | ((target_q as u64) << 16) | (run_gen & 0xffff)
}

#[inline(always)]
const fn pkt_cookie(stream_id: u32, seq: u64, run_gen: u64) -> u64 {
    let x = seq.wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ (stream_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9)
        ^ run_gen.wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ COOKIE_MAGIC
}

#[inline(always)]
const fn pkt_tail(stream_id: u32, seq: u64, run_gen: u64) -> u64 {
    let x = seq.wrapping_mul(0x6c62_272e_07bb_0142)
        ^ (stream_id as u64).wrapping_mul(0x517c_c1b7_2722_0a95)
        ^ run_gen.wrapping_mul(0x2545_f491_4f6c_dd1d);
    let v = x ^ TAIL_MAGIC;
    if v == 0 { 1 } else { v }
}

#[inline(always)]
fn stamp_payload(buf: &mut [u8], stream_id: u32, target_q: u16, seq: u64, run_gen: u64, tx_tsc: u64) {
    let len = buf.len();
    debug_assert!(len >= 40);
    let ck = pkt_cookie(stream_id, seq, run_gen);
    let meta = pack_meta(stream_id, target_q, run_gen);
    let tl = pkt_tail(stream_id, seq, run_gen);
    unsafe {
        let p = buf.as_mut_ptr();
        p.cast::<u64>().write_unaligned(seq);
        p.add(8).cast::<u64>().write_unaligned(ck);
        p.add(16).cast::<u64>().write_unaligned(meta);
        p.add(24).cast::<u64>().write_unaligned(tx_tsc);
        if len >= 128 {
            let mid = (len / 2) & !7;
            p.add(mid).cast::<u64>().write_unaligned(ck ^ MID_XOR);
        }
        p.add(len - 8).cast::<u64>().write_unaligned(tl);
    }
}

/// Build a deterministic `SCHED_LEN`-entry queue-selection schedule whose empirical
/// queue frequencies match `uniform`, `ratio4to1`, or `zipf(alpha)` to <1% accuracy,
/// interleaved smoothly via Deficit Round-Robin (no artificial bursts on a single queue!).
fn build_weighted_schedule(dist: &str, n_queues: usize, zipf_alpha: f64, custom_weights: &[f64]) -> Vec<usize> {
    let n = n_queues.clamp(1, MAX_QUEUES);
    let raw: Vec<f64> = if custom_weights.len() == n && custom_weights.iter().all(|&w| w > 0.0) {
        custom_weights.to_vec()
    } else {
        match dist {
            "ratio4to1" | "4:1" => {
                let mut v = vec![1.0; n];
                if n > 1 {
                    v[0] = 4.0;
                }
                v
            }
            "zipf" => (0..n).map(|i| 1.0 / ((i + 1) as f64).powf(zipf_alpha.max(0.0))).collect(),
            _ => vec![1.0; n],
        }
    };
    let sum: f64 = raw.iter().sum();
    let norm: Vec<f64> = raw.into_iter().map(|w| w / sum).collect();

    // Smooth weighted round-robin (Nginx/DRR style) guarantees exact target counts AND smooth interleaving
    let mut counts = vec![1usize; n];
    let rem = SCHED_LEN.saturating_sub(n);
    let mut fracs: Vec<(usize, f64)> = Vec::with_capacity(n);
    let mut assigned = n;
    for q in 0..n {
        let exact = norm[q] * (SCHED_LEN as f64);
        let target = exact.floor() as usize;
        let extra = target.saturating_sub(1);
        let add = extra.min(SCHED_LEN - assigned);
        counts[q] += add;
        assigned += add;
        fracs.push((q, exact - (counts[q] as f64)));
    }
    let _ = rem;
    fracs.sort_by(|a, b| b.1.total_cmp(&a.1));
    let mut fi = 0usize;
    while assigned < SCHED_LEN {
        counts[fracs[fi % n].0] += 1;
        assigned += 1;
        fi += 1;
    }

    // Smoothly interleave `counts` across `SCHED_LEN` positions
    let mut cur = vec![0i64; n];
    let mut sched = Vec::with_capacity(SCHED_LEN);
    let total = SCHED_LEN as i64;
    for _ in 0..SCHED_LEN {
        let mut best_q = 0usize;
        let mut best_v = i64::MIN;
        for q in 0..n {
            cur[q] += counts[q] as i64;
            if cur[q] > best_v {
                best_v = cur[q];
                best_q = q;
            }
        }
        cur[best_q] -= total;
        sched.push(best_q);
    }
    sched
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TxMode {
    Ud,
    Udp,
}

#[derive(Clone, Debug)]
struct RunConfig {
    mode: TxMode,
    threads: usize,
    queues: usize,
    payload_len: u32,
    batch: u32,
    rate_mpps: f64,
    run_gen: u64,
    dist: String,
    zipf_alpha: f64,
    use_gso: bool,
    /// Per-queue list of probed flow_labels (UD) or UDP src_ports (UDP).
    q_flows: Vec<Vec<u32>>,
    custom_weights: Vec<f64>,
}

#[repr(align(64))]
struct ThreadStats {
    tx_pkts: AtomicU64,
    per_q_pkts: [AtomicU64; MAX_QUEUES],
    cqe_err: AtomicU64,
    post_err: AtomicU64,
}

impl ThreadStats {
    fn new() -> Self {
        Self {
            tx_pkts: AtomicU64::new(0),
            per_q_pkts: std::array::from_fn(|_| AtomicU64::new(0)),
            cqe_err: AtomicU64::new(0),
            post_err: AtomicU64::new(0),
        }
    }
}

struct ActiveWorkers {
    stop: Arc<AtomicBool>,
    active: Arc<AtomicBool>,
    stats: Arc<Vec<ThreadStats>>,
    handles: Vec<JoinHandle<()>>,
    max_inline: u32,
    sched_counts: [usize; MAX_QUEUES],
}

struct SnapState {
    t_app: Instant,
    t_phy: Instant,
    app_pkts: u64,
    per_q_pkts: [u64; MAX_QUEUES],
    cqe_err: u64,
    post_err: u64,
    phy: PhyStats,
}

fn spawn_ud_workers(ctx_ptr: *mut c_void, cfg: RunConfig, tsc_hz: f64) -> Result<ActiveWorkers, String> {
    let stop = Arc::new(AtomicBool::new(false));
    let active = Arc::new(AtomicBool::new(false));
    let stats: Arc<Vec<ThreadStats>> = Arc::new((0..cfg.threads).map(|_| ThreadStats::new()).collect());
    let sched = build_weighted_schedule(&cfg.dist, cfg.queues, cfg.zipf_alpha, &cfg.custom_weights);
    let mut sched_counts = [0usize; MAX_QUEUES];
    for &q in &sched {
        sched_counts[q] += 1;
    }

    let slot_stride: u32 = ((cfg.payload_len.max(64) + 63) & !63).max(64);
    let want_inline = if cfg.payload_len <= 188 { cfg.payload_len } else { 0 };

    // Pre-create `cfg.queues` UD channels per thread (one per target CN106 RSS queue)
    let mut all_chans: Vec<Vec<usize>> = Vec::with_capacity(cfg.threads);
    let mut max_inline = 0u32;
    for t in 0..cfg.threads {
        let mut t_chans = Vec::with_capacity(cfg.queues);
        for q in 0..cfg.queues {
            let fl = cfg
                .q_flows
                .get(q)
                .and_then(|v| if v.is_empty() { None } else { Some(v[t % v.len()]) })
                .unwrap_or(((t * MAX_QUEUES + q + 1) as u32) & 0xfffff);
            let ch = unsafe {
                mrv210_chan_create(ctx_ptr, fl, UD_CARD_QPN, SLOT_COUNT, slot_stride, want_inline)
            };
            if ch.is_null() {
                for prev_vec in all_chans {
                    for p in prev_vec {
                        unsafe { mrv210_chan_destroy(p as *mut c_void) };
                    }
                }
                for p in t_chans {
                    unsafe { mrv210_chan_destroy(p as *mut c_void) };
                }
                return Err(format!("mrv210_chan_create failed for thread={t} q={q} fl={fl}"));
            }
            max_inline = unsafe { mrv210_chan_max_inline(ch) };
            t_chans.push(ch as usize);
        }
        all_chans.push(t_chans);
    }

    let mut handles = Vec::with_capacity(cfg.threads);
    for (t, t_chans) in all_chans.into_iter().enumerate() {
        let stop_c = Arc::clone(&stop);
        let active_c = Arc::clone(&active);
        let stats_c = Arc::clone(&stats);
        let cfg_c = cfg.clone();
        let sched_c = sched.clone();
        // NUMA-0 physical cores 2..25 on mercury
        let cpu = 2 + (t % 24);

        let h = thread::spawn(move || {
            pin_cpu(cpu);
            let nq = cfg_c.queues;
            let plen = cfg_c.payload_len as usize;
            let batch = cfg_c.batch.clamp(1, 64);
            let max_inflight_batches: u64 = (1024 / batch as u64).max(4);
            let run_gen = cfg_c.run_gen;

            let mut bufs: Vec<*mut u8> = Vec::with_capacity(nq);
            for &ch_addr in &t_chans {
                bufs.push(unsafe { mrv210_chan_buf(ch_addr as *mut c_void) });
            }
            let mut seqs = vec![1u64; nq];
            let mut slots = vec![0u32; nq];
            let mut inflight = vec![0u64; nq];
            let mut cqe_err = 0u64;

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

            let mut sched_pos = (t * 7) % SCHED_LEN;
            let mut next_tsc = 0.0f64;
            let mut was_active = false;

            while !stop_c.load(Ordering::Relaxed) {
                if !active_c.load(Ordering::Acquire) {
                    if was_active {
                        for q in 0..nq {
                            let ch = t_chans[q] as *mut c_void;
                            let dl = Instant::now() + std::time::Duration::from_millis(20);
                            while inflight[q] > 0 && Instant::now() < dl {
                                let n = unsafe { mrv210_chan_poll_cq(ch, 32, &mut cqe_err) };
                                if n > 0 {
                                    inflight[q] = inflight[q].saturating_sub(n as u64);
                                } else {
                                    std::hint::spin_loop();
                                }
                            }
                        }
                        if cqe_err > 0 {
                            stats_c[t].cqe_err.fetch_add(cqe_err, Ordering::Relaxed);
                            cqe_err = 0;
                        }
                        was_active = false;
                    }
                    thread::sleep(std::time::Duration::from_micros(20));
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

                let q = sched_c[sched_pos];
                sched_pos = (sched_pos + 1) & (SCHED_LEN - 1);
                let ch = t_chans[q] as *mut c_void;

                while inflight[q] >= max_inflight_batches {
                    let n = unsafe { mrv210_chan_poll_cq(ch, 32, &mut cqe_err) };
                    if n > 0 {
                        inflight[q] = inflight[q].saturating_sub(n as u64);
                    } else if stop_c.load(Ordering::Relaxed) || !active_c.load(Ordering::Relaxed) {
                        break;
                    } else {
                        std::hint::spin_loop();
                    }
                }
                if inflight[q] > 0 {
                    let n = unsafe { mrv210_chan_poll_cq(ch, 16, &mut cqe_err) };
                    if n > 0 {
                        inflight[q] = inflight[q].saturating_sub(n as u64);
                    }
                }

                let stream_id = (t * MAX_QUEUES + q) as u32;
                let start_slot = slots[q];
                let now_tsc = rdtsc();
                for _ in 0..batch {
                    let sl = (slots[q] & SLOT_MASK) as usize;
                    let p = unsafe { bufs[q].add(sl * slot_stride as usize) };
                    let s = seqs[q];
                    seqs[q] += 1;
                    let slice = unsafe { std::slice::from_raw_parts_mut(p, plen) };
                    stamp_payload(slice, stream_id, q as u16, s, run_gen, now_tsc);
                    slots[q] = slots[q].wrapping_add(1);
                }

                let rc = unsafe {
                    mrv210_chan_post_batch(ch, start_slot, SLOT_MASK, batch, cfg_c.payload_len, seqs[q])
                };
                if rc == 0 {
                    inflight[q] += 1;
                    stats_c[t].tx_pkts.fetch_add(batch as u64, Ordering::Relaxed);
                    stats_c[t].per_q_pkts[q].fetch_add(batch as u64, Ordering::Relaxed);
                } else {
                    stats_c[t].post_err.fetch_add(1, Ordering::Relaxed);
                }
                if cqe_err > 0 {
                    stats_c[t].cqe_err.fetch_add(cqe_err, Ordering::Relaxed);
                    cqe_err = 0;
                }
            }

            let dl = Instant::now() + std::time::Duration::from_millis(50);
            for q in 0..nq {
                let ch = t_chans[q] as *mut c_void;
                while inflight[q] > 0 && Instant::now() < dl {
                    let n = unsafe { mrv210_chan_poll_cq(ch, 32, &mut cqe_err) };
                    if n > 0 {
                        inflight[q] = inflight[q].saturating_sub(n as u64);
                    }
                }
                unsafe { mrv210_chan_destroy(ch) };
            }
            if cqe_err > 0 {
                stats_c[t].cqe_err.fetch_add(cqe_err, Ordering::Relaxed);
            }
        });
        handles.push(h);
    }

    Ok(ActiveWorkers { stop, active, stats, handles, max_inline, sched_counts })
}

fn spawn_udp_workers(dst_ip: Ipv4Addr, cfg: RunConfig, tsc_hz: f64) -> Result<ActiveWorkers, String> {
    let stop = Arc::new(AtomicBool::new(false));
    let active = Arc::new(AtomicBool::new(false));
    let stats: Arc<Vec<ThreadStats>> = Arc::new((0..cfg.threads).map(|_| ThreadStats::new()).collect());
    let sched = build_weighted_schedule(&cfg.dist, cfg.queues, cfg.zipf_alpha, &cfg.custom_weights);
    let mut sched_counts = [0usize; MAX_QUEUES];
    for &q in &sched {
        sched_counts[q] += 1;
    }

    let dst_addr = SocketAddrV4::new(dst_ip, UDP_DATA_PORT);
    let plen = cfg.payload_len as usize;
    // Max 64 KiB GSO super-datagram -> up to 44 x 1454 B exact 1500 B L2 segments per syscall!
    let gso_segs = if cfg.use_gso {
        ((64000 / plen.max(40)) as u32).clamp(1, cfg.batch.clamp(1, 64))
    } else {
        1
    };

    let mut all_socks: Vec<Vec<(UdpSocket, bool)>> = Vec::with_capacity(cfg.threads);
    for t in 0..cfg.threads {
        let mut t_socks = Vec::with_capacity(cfg.queues);
        for q in 0..cfg.queues {
            let want_sport = cfg
                .q_flows
                .get(q)
                .and_then(|v| if v.is_empty() { None } else { Some(v[t % v.len()] as u16) })
                .unwrap_or(0);
            let sock = UdpSocket::bind(SocketAddrV4::new(Ipv4Addr::UNSPECIFIED, want_sport))
                .or_else(|_| UdpSocket::bind(SocketAddrV4::new(Ipv4Addr::UNSPECIFIED, 0)))
                .map_err(|e| format!("bind udp socket t={t} q={q} sport={want_sport}: {e}"))?;
            sock.connect(dst_addr)
                .map_err(|e| format!("connect udp socket to {dst_addr}: {e}"))?;
            let sndbuf: c_int = 16 * 1024 * 1024;
            unsafe {
                let _ = setsockopt(
                    sock.as_raw_fd(),
                    SOL_SOCKET,
                    SO_SNDBUF,
                    (&raw const sndbuf).cast::<c_void>(),
                    size_of::<c_int>() as u32,
                );
            }
            let mut gso_ok = false;
            if cfg.use_gso && gso_segs > 1 {
                let seg_sz: c_int = cfg.payload_len as c_int;
                let rc = unsafe {
                    setsockopt(
                        sock.as_raw_fd(),
                        SOL_UDP,
                        UDP_SEGMENT,
                        (&raw const seg_sz).cast::<c_void>(),
                        size_of::<c_int>() as u32,
                    )
                };
                gso_ok = rc == 0;
            }
            t_socks.push((sock, gso_ok));
        }
        all_socks.push(t_socks);
    }

    let mut handles = Vec::with_capacity(cfg.threads);
    for (t, t_socks) in all_socks.into_iter().enumerate() {
        let stop_c = Arc::clone(&stop);
        let active_c = Arc::clone(&active);
        let stats_c = Arc::clone(&stats);
        let cfg_c = cfg.clone();
        let sched_c = sched.clone();
        let cpu = 2 + (t % 24);

        let h = thread::spawn(move || {
            pin_cpu(cpu);
            let nq = cfg_c.queues;
            let plen = cfg_c.payload_len as usize;
            let run_gen = cfg_c.run_gen;
            let batch_pkts = if gso_segs > 1 { gso_segs } else { cfg_c.batch.clamp(1, 64) };
            let mut buf = vec![0x3cu8; plen * (batch_pkts as usize)];
            let mut seqs = vec![1u64; nq];

            let per_thread_mpps = if cfg_c.rate_mpps > 0.0 {
                cfg_c.rate_mpps / (cfg_c.threads as f64)
            } else {
                0.0
            };
            let ticks_per_batch = if per_thread_mpps > 0.0 {
                (batch_pkts as f64 / (per_thread_mpps * 1e6)) * tsc_hz
            } else {
                0.0
            };

            let mut sched_pos = (t * 7) % SCHED_LEN;
            let mut next_tsc = 0.0f64;
            let mut was_active = false;

            while !stop_c.load(Ordering::Relaxed) {
                if !active_c.load(Ordering::Acquire) {
                    was_active = false;
                    thread::sleep(std::time::Duration::from_micros(20));
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

                let q = sched_c[sched_pos];
                sched_pos = (sched_pos + 1) & (SCHED_LEN - 1);
                let (sock, gso_ok) = &t_socks[q];
                let fd = sock.as_raw_fd();
                let stream_id = (t * MAX_QUEUES + q) as u32;
                let now_tsc = rdtsc();

                if *gso_ok && batch_pkts > 1 {
                    let segs = batch_pkts as usize;
                    for i in 0..segs {
                        let s = seqs[q];
                        seqs[q] += 1;
                        stamp_payload(&mut buf[i * plen..(i + 1) * plen], stream_id, q as u16, s, run_gen, now_tsc);
                    }
                    let total_len = segs * plen;
                    let n = unsafe { send(fd, buf.as_ptr().cast::<c_void>(), total_len, 0) };
                    if n == total_len as isize {
                        stats_c[t].tx_pkts.fetch_add(segs as u64, Ordering::Relaxed);
                        stats_c[t].per_q_pkts[q].fetch_add(segs as u64, Ordering::Relaxed);
                    } else {
                        stats_c[t].post_err.fetch_add(1, Ordering::Relaxed);
                    }
                } else {
                    let mut ok_pkts = 0u64;
                    for _ in 0..batch_pkts {
                        let s = seqs[q];
                        seqs[q] += 1;
                        stamp_payload(&mut buf[..plen], stream_id, q as u16, s, run_gen, now_tsc);
                        let n = unsafe { send(fd, buf.as_ptr().cast::<c_void>(), plen, 0) };
                        if n == plen as isize {
                            ok_pkts += 1;
                        } else {
                            stats_c[t].post_err.fetch_add(1, Ordering::Relaxed);
                            break;
                        }
                    }
                    if ok_pkts > 0 {
                        stats_c[t].tx_pkts.fetch_add(ok_pkts, Ordering::Relaxed);
                        stats_c[t].per_q_pkts[q].fetch_add(ok_pkts, Ordering::Relaxed);
                    }
                }
            }
        });
        handles.push(h);
    }

    Ok(ActiveWorkers { stop, active, stats, handles, max_inline: 0, sched_counts })
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
    let mut app_pkts = 0u64;
    let mut per_q_pkts = [0u64; MAX_QUEUES];
    let mut cqe_err = 0u64;
    let mut post_err = 0u64;
    if let Some(w) = w {
        for s in w.stats.iter() {
            app_pkts += s.tx_pkts.load(Ordering::Relaxed);
            for q in 0..MAX_QUEUES {
                per_q_pkts[q] += s.per_q_pkts[q].load(Ordering::Relaxed);
            }
            cqe_err += s.cqe_err.load(Ordering::Relaxed);
            post_err += s.post_err.load(Ordering::Relaxed);
        }
    }
    let mut phy = PhyStats::default();
    let t_phy = Instant::now();
    if !et.is_null() {
        unsafe {
            mrv210_ethtool_read(et, &mut phy);
        }
    }
    SnapState { t_app, t_phy, app_pkts, per_q_pkts, cqe_err, post_err, phy }
}

fn parse_q_flows(s: &str) -> Vec<Vec<u32>> {
    s.split(';')
        .map(|grp| grp.split(',').filter_map(|x| x.parse::<u32>().ok()).collect())
        .collect()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut port: u16 = 7552;
    let mut dev = "mlx5_2".to_string();
    let mut ifname = "ens6f1np1".to_string();
    let mut dst_ip_str = "10.10.100.2".to_string();
    let mut sgid_idx: i32 = 3;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--port" => { port = args[i + 1].parse().unwrap(); i += 2; }
            "--dev" => { dev = args[i + 1].clone(); i += 2; }
            "--ifname" => { ifname = args[i + 1].clone(); i += 2; }
            "--dst-ip" => { dst_ip_str = args[i + 1].clone(); i += 2; }
            "--sgid" => { sgid_idx = args[i + 1].parse().unwrap(); i += 2; }
            _ => { i += 1; }
        }
    }

    pin_cpu(0);
    let tsc_hz = measure_tsc_hz();
    let dst_ip: Ipv4Addr = dst_ip_str.parse().expect("valid --dst-ip IPv4");

    let c_dev = CString::new(dev.as_str()).unwrap();
    let c_ip = CString::new(dst_ip_str.as_str()).unwrap();
    let ctx = unsafe { mrv210_open(c_dev.as_ptr(), sgid_idx, c_ip.as_ptr()) };
    assert!(!ctx.is_null(), "failed to open ibv device {dev}");
    let active_mtu = unsafe { mrv210_active_mtu(ctx) };

    let c_if = CString::new(ifname.as_str()).unwrap();
    let et = unsafe { mrv210_ethtool_open(c_if.as_ptr()) };
    assert!(!et.is_null(), "failed to open ethtool stats for {ifname}");

    let listener = TcpListener::bind(("0.0.0.0", port)).expect("bind control port");
    println!(
        "MRV210_CLIENT READY port={port} dev={dev} ifname={ifname} dst={dst_ip_str} active_mtu={active_mtu} tsc_hz={tsc_hz:.0}"
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
            format!("{{\"ok\":true,\"active_mtu\":{active_mtu}}}")
        } else if let Some(rest) = cmd.strip_prefix("PROBE_RSS ") {
            let mut mode = TxMode::Ud;
            let mut count = 128u32;
            let mut run_gen = 1u64;
            for tok in rest.split_whitespace() {
                if let Some((k, v)) = tok.split_once('=') {
                    match k {
                        "mode" => mode = if v == "udp" { TxMode::Udp } else { TxMode::Ud },
                        "count" => count = v.parse::<u32>().unwrap_or(128).clamp(1, 256),
                        "run_gen" => run_gen = v.parse::<u64>().unwrap_or(1),
                        _ => {}
                    }
                }
            }
            let mut probed = 0u32;
            match mode {
                TxMode::Ud => {
                    for fl in 1..=count {
                        let ch = unsafe { mrv210_chan_create(ctx, fl, UD_CARD_QPN, 64, 64, 48) };
                        if ch.is_null() {
                            continue;
                        }
                        let buf = unsafe { mrv210_chan_buf(ch) };
                        for k in 0..4u32 {
                            let p = unsafe { buf.add((k as usize) * 64) };
                            let slice = unsafe { std::slice::from_raw_parts_mut(p, 48) };
                            stamp_payload(slice, fl - 1, 0, (k + 1) as u64, run_gen, rdtsc());
                        }
                        if unsafe { mrv210_chan_post_batch(ch, 0, 63, 4, 48, fl as u64) } == 0 {
                            let mut err = 0u64;
                            let t0 = Instant::now();
                            while unsafe { mrv210_chan_poll_cq(ch, 4, &mut err) } == 0
                                && t0.elapsed().as_millis() < 20
                            {
                                std::hint::spin_loop();
                            }
                            probed += 1;
                        }
                        unsafe { mrv210_chan_destroy(ch) };
                    }
                }
                TxMode::Udp => {
                    let dst_addr = SocketAddrV4::new(dst_ip, UDP_DATA_PORT);
                    let mut pbuf = [0x3cu8; 64];
                    for idx in 1..=count {
                        let sport = UDP_PROBE_BASE_SPORT.wrapping_add(idx as u16);
                        if let Ok(s) = UdpSocket::bind(SocketAddrV4::new(Ipv4Addr::UNSPECIFIED, sport)) {
                            if s.connect(dst_addr).is_ok() {
                                for k in 0..4u32 {
                                    stamp_payload(&mut pbuf, idx - 1, 0, (k + 1) as u64, run_gen, rdtsc());
                                    let _ = s.send(&pbuf);
                                }
                                probed += 1;
                            }
                        }
                    }
                }
            }
            format!("{{\"ok\":true,\"probed\":{probed},\"base_sport\":{UDP_PROBE_BASE_SPORT}}}")
        } else if let Some(rest) = cmd.strip_prefix("CONFIG ") {
            let mut cfg = RunConfig {
                mode: TxMode::Ud,
                threads: 16,
                queues: 8,
                payload_len: 184,
                batch: 32,
                rate_mpps: 0.0,
                run_gen: 1,
                dist: "uniform".to_string(),
                zipf_alpha: 1.0,
                use_gso: true,
                q_flows: Vec::new(),
                custom_weights: Vec::new(),
            };
            for tok in rest.split_whitespace() {
                if let Some((k, v)) = tok.split_once('=') {
                    match k {
                        "mode" => cfg.mode = if v == "udp" { TxMode::Udp } else { TxMode::Ud },
                        "threads" => cfg.threads = v.parse::<usize>().unwrap_or(16).clamp(1, MAX_THREADS),
                        "queues" => cfg.queues = v.parse::<usize>().unwrap_or(8).clamp(1, MAX_QUEUES),
                        "payload" => cfg.payload_len = v.parse::<u32>().unwrap_or(184).max(40),
                        "batch" => cfg.batch = v.parse::<u32>().unwrap_or(32).clamp(1, 64),
                        "rate_mpps" => cfg.rate_mpps = v.parse::<f64>().unwrap_or(0.0),
                        "run_gen" => cfg.run_gen = v.parse::<u64>().unwrap_or(1),
                        "dist" => cfg.dist = v.to_string(),
                        "zipf_alpha" => cfg.zipf_alpha = v.parse::<f64>().unwrap_or(1.0),
                        "gso" => cfg.use_gso = v != "0" && v != "false",
                        "q_flows" => cfg.q_flows = parse_q_flows(v),
                        "q_weights" => {
                            cfg.custom_weights = v.split(',').filter_map(|x| x.parse::<f64>().ok()).collect();
                        }
                        _ => {}
                    }
                }
            }
            let mut guard = workers_lock.lock().unwrap();
            if let Some(old) = guard.take() {
                shutdown_workers(old);
            }
            let spawned = match cfg.mode {
                TxMode::Ud => spawn_ud_workers(ctx, cfg.clone(), tsc_hz),
                TxMode::Udp => spawn_udp_workers(dst_ip, cfg.clone(), tsc_hz),
            };
            match spawned {
                Ok(w) => {
                    let inl = w.max_inline;
                    let sc: Vec<String> = w.sched_counts[..cfg.queues].iter().map(|x| x.to_string()).collect();
                    *guard = Some(w);
                    format!(
                        "{{\"ok\":true,\"mode\":\"{}\",\"threads\":{},\"queues\":{},\"payload\":{},\"dist\":\"{}\",\"max_inline\":{},\"sched_counts\":[{}]}}",
                        if cfg.mode == TxMode::Ud { "ud" } else { "udp" },
                        cfg.threads,
                        cfg.queues,
                        cfg.payload_len,
                        cfg.dist,
                        inl,
                        sc.join(",")
                    )
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
            let dt_active = (active_dur_s + active_start.map(|t| t.elapsed().as_secs_f64()).unwrap_or(0.0)).max(1e-9);
            let app_pkts = snap1.app_pkts.saturating_sub(snap0.app_pkts);
            let cqe_err = snap1.cqe_err.saturating_sub(snap0.cqe_err);
            let post_err = snap1.post_err.saturating_sub(snap0.post_err);
            let tx_pkts = snap1.phy.tx_packets_phy.saturating_sub(snap0.phy.tx_packets_phy);
            let tx_bytes = snap1.phy.tx_bytes_phy.saturating_sub(snap0.phy.tx_bytes_phy);
            let tx_disc = snap1.phy.tx_discards_phy.saturating_sub(snap0.phy.tx_discards_phy);
            let rx_crc = snap1.phy.rx_crc_errors_phy.saturating_sub(snap0.phy.rx_crc_errors_phy);
            let tx_pause = snap1.phy.tx_global_pause.saturating_sub(snap0.phy.tx_global_pause);
            let rx_pause = snap1.phy.rx_global_pause.saturating_sub(snap0.phy.rx_global_pause);
            let avg_l2_bytes = if tx_pkts > 0 { tx_bytes as f64 / tx_pkts as f64 } else { 0.0 };
            let wire_gbps = ((tx_bytes + tx_pkts * 20) as f64 * 8.0) / dt_phy / 1e9;
            let phy_mpps = tx_pkts as f64 / dt_phy / 1e6;
            let pq: Vec<String> = (0..MAX_QUEUES)
                .map(|q| snap1.per_q_pkts[q].saturating_sub(snap0.per_q_pkts[q]).to_string())
                .collect();
            format!(
                "{{\"ok\":true,\"dt_app_s\":{dt_app:.6},\"dt_phy_s\":{dt_phy:.6},\"dt_active_s\":{dt_active:.6},\"app_pkts\":{app_pkts},\"cqe_err\":{cqe_err},\"post_err\":{post_err},\"per_q_tx\":[{}],\"phy\":{{\"tx_pkts\":{tx_pkts},\"tx_bytes\":{tx_bytes},\"tx_discards\":{tx_disc},\"rx_crc_errors\":{rx_crc},\"tx_pause\":{tx_pause},\"rx_pause\":{rx_pause},\"avg_l2_bytes\":{avg_l2_bytes:.2},\"mpps\":{phy_mpps:.4},\"wire_gbps\":{wire_gbps:.4}}}}}",
                pq.join(",")
            )
        } else if cmd == "QUIT" {
            let mut guard = workers_lock.lock().unwrap();
            if let Some(old) = guard.take() {
                shutdown_workers(old);
            }
            let _ = writeln!(sock, "{{\"ok\":true}}");
            let _ = sock.flush();
            break;
        } else {
            "{\"ok\":false,\"err\":\"unknown_cmd\"}".to_string()
        };
        let _ = writeln!(sock, "{reply}");
        let _ = sock.flush();
    }

    unsafe {
        mrv210_ethtool_close(et);
        mrv210_close(ctx);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weighted_schedule_uniform_ratio4to1_and_zipf() {
        let su = build_weighted_schedule("uniform", 8, 1.0, &[]);
        assert_eq!(su.len(), SCHED_LEN);
        for q in 0..8 {
            assert_eq!(su.iter().filter(|&&x| x == q).count(), SCHED_LEN / 8);
        }

        let sr = build_weighted_schedule("ratio4to1", 8, 1.0, &[]);
        assert_eq!(sr.len(), SCHED_LEN);
        let c0 = sr.iter().filter(|&&x| x == 0).count();
        let c1 = sr.iter().filter(|&&x| x == 1).count();
        let ratio = c0 as f64 / c1 as f64;
        assert!((ratio - 4.0).abs() < 0.35, "ratio4to1 got c0={c0} c1={c1} ratio={ratio:.2}");

        let sz = build_weighted_schedule("zipf", 8, 1.0, &[]);
        let counts: Vec<usize> = (0..8).map(|q| sz.iter().filter(|&&x| x == q).count()).collect();
        assert!(counts[0] > counts[1] && counts[1] >= counts[7] && counts[7] >= 1);
        assert_eq!(counts.iter().sum::<usize>(), SCHED_LEN);
    }

    #[test]
    fn payload_stamp_matches_protocol_offsets() {
        let mut buf = vec![0u8; 1454];
        stamp_payload(&mut buf, 19, 3, 777, 5, 99999);
        let seq = u64::from_le_bytes(buf[0..8].try_into().unwrap());
        let ck = u64::from_le_bytes(buf[8..16].try_into().unwrap());
        let meta = u64::from_le_bytes(buf[16..24].try_into().unwrap());
        let mid_off = (1454 / 2) & !7;
        let mid = u64::from_le_bytes(buf[mid_off..mid_off + 8].try_into().unwrap());
        let tl = u64::from_le_bytes(buf[1446..1454].try_into().unwrap());
        assert_eq!(seq, 777);
        assert_eq!(ck, pkt_cookie(19, 777, 5));
        assert_eq!(meta, pack_meta(19, 3, 5));
        assert_eq!(mid, ck ^ MID_XOR);
        assert_eq!(tl, pkt_tail(19, 777, 5));
    }

    #[test]
    fn parse_q_flows_multi_queue() {
        let parsed = parse_q_flows("1,9,17;2,10;3,11,19");
        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[0], vec![1, 9, 17]);
        assert_eq!(parsed[1], vec![2, 10]);
        assert_eq!(parsed[2], vec![3, 11, 19]);
    }
}
