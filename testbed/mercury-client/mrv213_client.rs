//! MRV-213 (E2) mercury multi-core 100GbE discrete-burst generator (Rust + C shim).
//!
//! Runs on `mercury` NUMA-0 cores (matching ConnectX-5 `mlx5_2` / `ens6f1np1`, zero root).
//! Zero external Rust crates; compiles directly with:
//!   gcc -O3 -Wall -Wextra -Werror -c mrv213_verbs_shim.c -o mrv213_verbs_shim.o
//!   rustc -O -D warnings mrv213_client.rs -C link-arg=mrv213_verbs_shim.o -C link-arg=-libverbs -o mrv213_client
//!
//! Supported packet profiles (exact L2 Ethernet frame sizes):
//!   - `uc64`    : `IBV_QPT_UC` 0 B payload -> 64 B L2 (`60 B` mbuf, HW BTH.PSN + ICRC + FCS)
//!   - `ud256`   : `IBV_QPT_UD` 186 B payload -> 256 B L2 (`252 B` mbuf, inline RoCEv2 UD)
//!   - `udp1500` : Linux `UDP_SEGMENT` GSO / `sendmmsg` 1454 B payload -> 1500 B L2 (`1496 B` mbuf, 0 frag)
//!   - `ud1094`  : `IBV_QPT_UD` 1024 B payload -> 1094 B L2 (`1090 B` mbuf)
//!
//! Wire integrity contract matches `testbed::mrv213` byte-for-byte:
//!   payload[0..8]   = seq (1-based per stream)
//!   payload[8..16]  = pack_payload_meta(run_gen, burst_id, stream_id, l2_bytes)
//!   payload[16..24] = payload_cookie(stream_id, run_gen, seq)
//!   payload[24..32] = tx_tsc
//!   payload[mid..+8] = cookie ^ PAYLOAD_MID_XOR
//!   payload[len-8..] = payload_tail(stream_id, run_gen, seq)

use std::ffi::{CString, c_char, c_int, c_void};
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Barrier, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

const SLOT_COUNT: u32 = 2048;
const SLOT_MASK: u32 = SLOT_COUNT - 1;
const UC64_BASE_QPN: u32 = 0x0001_0000;
const UD_CARD_QPN: u32 = 0x0001_2345;
const DATA_UDP_PORT: u16 = 4799;
const MAX_THREADS: usize = 30;

const PAYLOAD_COOKIE_MAGIC: u64 = 0x2130_CAFE_BABE_55AA;
const PAYLOAD_MID_XOR: u64 = 0x5555_AAAA_3333_CCCC;
const PAYLOAD_TAIL_MAGIC: u64 = 0xA5A5_5A5A_2130_F0F0;

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

unsafe extern "C" {
    fn mrv213_open(dev_name: *const c_char, sgid_idx: c_int, dst_ip_str: *const c_char) -> *mut c_void;
    fn mrv213_close(c: *mut c_void);
    fn mrv213_chan_create(
        c: *mut c_void,
        is_uc: c_int,
        flow_label: u32,
        remote_qpn: u32,
        slot_count: u32,
        slot_stride: u32,
        want_inline: u32,
    ) -> *mut c_void;
    fn mrv213_chan_buf(ch: *mut c_void) -> *mut u8;
    fn mrv213_chan_max_inline(ch: *mut c_void) -> u32;
    fn mrv213_chan_destroy(ch: *mut c_void);
    fn mrv213_chan_post_batch(
        ch: *mut c_void,
        start_slot: u32,
        slot_mask: u32,
        count: u32,
        payload_len: u32,
        wr_id_tag: u64,
    ) -> c_int;
    fn mrv213_chan_poll_cq(ch: *mut c_void, max_wc: c_int, err_out: *mut u64) -> c_int;

    fn mrv213_udp_create(
        src_ip_str: *const c_char,
        src_port: u16,
        dst_ip_str: *const c_char,
        dst_port: u16,
        seg_len: u32,
        want_gso: c_int,
    ) -> *mut c_void;
    fn mrv213_udp_buf(u: *mut c_void) -> *mut u8;
    fn mrv213_udp_gso_active(u: *mut c_void) -> c_int;
    fn mrv213_udp_send_batch(u: *mut c_void, count: u32) -> c_int;
    fn mrv213_udp_destroy(u: *mut c_void);

    fn mrv213_ethtool_open(ifname: *const c_char) -> *mut c_void;
    fn mrv213_ethtool_read(et: *mut c_void, out: *mut PhyStats) -> c_int;
    fn mrv213_ethtool_close(et: *mut c_void);

    fn sched_setaffinity(pid: c_int, cpusetsize: usize, mask: *const u8) -> c_int;
}

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

#[inline(always)]
const fn pack_payload_meta(run_gen: u64, burst_id: u16, stream_id: u16, l2_bytes: u16) -> u64 {
    ((run_gen & 0xffff) << 48)
        | ((burst_id as u64) << 32)
        | ((stream_id as u64) << 16)
        | (l2_bytes as u64)
}

#[inline(always)]
const fn payload_cookie(stream_id: u16, run_gen: u64, seq: u64) -> u64 {
    seq.wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ ((stream_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ ((run_gen & 0xffff).wrapping_mul(0x94d0_49bb_1331_11eb))
        ^ PAYLOAD_COOKIE_MAGIC
}

#[inline(always)]
const fn payload_tail(stream_id: u16, run_gen: u64, seq: u64) -> u64 {
    let v = seq.wrapping_mul(0x6c62_272e_07bb_0142)
        ^ ((stream_id as u64).wrapping_mul(0x517c_c1b7_2722_0a95))
        ^ ((run_gen & 0xffff).wrapping_mul(0x2545_f491_4f6c_dd1d))
        ^ PAYLOAD_TAIL_MAGIC;
    if v == 0 { 1 } else { v }
}

#[inline(always)]
unsafe fn stamp_payload_ptr(
    p: *mut u8,
    plen: usize,
    stream_id: u16,
    run_gen: u64,
    burst_id: u16,
    seq: u64,
    tx_tsc: u64,
    l2_bytes: u16,
) {
    let meta = pack_payload_meta(run_gen, burst_id, stream_id, l2_bytes);
    let cookie = payload_cookie(stream_id, run_gen, seq);
    let tail = payload_tail(stream_id, run_gen, seq);
    let mid = (plen / 2) & !7;
    unsafe {
        p.cast::<u64>().write_unaligned(seq);
        p.add(8).cast::<u64>().write_unaligned(meta);
        p.add(16).cast::<u64>().write_unaligned(cookie);
        p.add(24).cast::<u64>().write_unaligned(tx_tsc);
        if mid >= 32 && mid + 8 <= plen - 8 {
            p.add(mid).cast::<u64>().write_unaligned(cookie ^ PAYLOAD_MID_XOR);
        }
        p.add(plen - 8).cast::<u64>().write_unaligned(tail);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TxMode {
    Uc64,
    Ud256,
    Udp1500,
    Ud1094,
}

impl TxMode {
    fn from_name(s: &str) -> Option<Self> {
        match s.to_ascii_lowercase().as_str() {
            "uc64" | "64" | "64b" => Some(TxMode::Uc64),
            "ud256" | "256" | "256b" => Some(TxMode::Ud256),
            "udp1500" | "1500" | "1500b" => Some(TxMode::Udp1500),
            "ud1094" | "1094" | "1094b" => Some(TxMode::Ud1094),
            _ => None,
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            TxMode::Uc64 => "uc64",
            TxMode::Ud256 => "ud256",
            TxMode::Udp1500 => "udp1500",
            TxMode::Ud1094 => "ud1094",
        }
    }

    const fn l2_bytes(self) -> u16 {
        match self {
            TxMode::Uc64 => 64,
            TxMode::Ud256 => 254,
            TxMode::Udp1500 => 1500,
            TxMode::Ud1094 => 1094,
        }
    }

    const fn wire_bytes(self) -> u64 {
        self.l2_bytes() as u64 + 20
    }

    const fn payload_len(self) -> u32 {
        match self {
            TxMode::Uc64 => 0,
            TxMode::Ud256 => 184,
            TxMode::Udp1500 => 1454,
            TxMode::Ud1094 => 1024,
        }
    }
}

#[repr(align(64))]
struct ThreadStats {
    tx_pkts: AtomicU64,
    cqe_err: AtomicU64,
    post_err: AtomicU64,
    gso_active: AtomicU64,
}

impl ThreadStats {
    fn new() -> Self {
        Self {
            tx_pkts: AtomicU64::new(0),
            cqe_err: AtomicU64::new(0),
            post_err: AtomicU64::new(0),
            gso_active: AtomicU64::new(0),
        }
    }
}

#[derive(Clone, Debug)]
struct RunConfig {
    mode: TxMode,
    threads: usize,
    batch: u32,
    rate_mpps: f64,
    run_gen: u64,
    udp_gso: bool,
    flow_base: u32,
    flows: Vec<u32>,
}

struct BurstDispatch {
    burst_gen: AtomicU64,
    done_count: AtomicU64,
    burst_id: AtomicU64,
    total_pkts: AtomicU64,
    run_gen: AtomicU64,
    stop: AtomicBool,
    start_barrier: Barrier,
}

struct ActiveWorkers {
    cfg: RunConfig,
    dispatch: Arc<BurstDispatch>,
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
    phy: PhyStats,
}

enum ChanHandle {
    Verbs(*mut c_void),
    Udp(*mut c_void),
}

unsafe impl Send for ChanHandle {}

fn spawn_workers(
    ctx_ptr: *mut c_void,
    src_ip: &str,
    dst_ip: &str,
    cfg: RunConfig,
    tsc_hz: f64,
) -> Result<ActiveWorkers, String> {
    let dispatch = Arc::new(BurstDispatch {
        burst_gen: AtomicU64::new(0),
        done_count: AtomicU64::new(0),
        burst_id: AtomicU64::new(0),
        total_pkts: AtomicU64::new(0),
        run_gen: AtomicU64::new(cfg.run_gen),
        stop: AtomicBool::new(false),
        start_barrier: Barrier::new(cfg.threads),
    });
    let mut stats_vec = Vec::with_capacity(cfg.threads);
    for _ in 0..cfg.threads {
        stats_vec.push(ThreadStats::new());
    }
    let stats = Arc::new(stats_vec);

    let plen = cfg.mode.payload_len();
    let slot_stride: u32 = ((plen.max(64) + 63) & !63).max(64);
    let want_inline = if plen <= 188 { plen } else { 0 };
    let c_src = CString::new(src_ip).unwrap();
    let c_dst = CString::new(dst_ip).unwrap();

    let mut chans: Vec<ChanHandle> = Vec::with_capacity(cfg.threads);
    let mut max_inline = 0u32;

    for t in 0..cfg.threads {
        let fl = if !cfg.flows.is_empty() {
            cfg.flows[t % cfg.flows.len()]
        } else {
            cfg.flow_base.wrapping_add(t as u32)
        };
        if cfg.mode == TxMode::Udp1500 {
            let sport = (20000u32 + (fl & 0x7fff)) as u16;
            let u = unsafe {
                mrv213_udp_create(
                    c_src.as_ptr(),
                    sport,
                    c_dst.as_ptr(),
                    DATA_UDP_PORT,
                    plen,
                    if cfg.udp_gso { 1 } else { 0 },
                )
            };
            if u.is_null() {
                for prev in chans {
                    if let ChanHandle::Udp(p) = prev {
                        unsafe { mrv213_udp_destroy(p) };
                    }
                }
                return Err(format!("mrv213_udp_create failed on thread {t} sport={sport}"));
            }
            chans.push(ChanHandle::Udp(u));
        } else {
            let is_uc = if cfg.mode == TxMode::Uc64 { 1 } else { 0 };
            let remote_qpn = if cfg.mode == TxMode::Uc64 {
                UC64_BASE_QPN + t as u32
            } else {
                UD_CARD_QPN
            };
            let ch = unsafe {
                mrv213_chan_create(
                    ctx_ptr,
                    is_uc,
                    fl & 0xfffff,
                    remote_qpn,
                    SLOT_COUNT,
                    slot_stride,
                    want_inline,
                )
            };
            if ch.is_null() {
                for prev in chans {
                    if let ChanHandle::Verbs(p) = prev {
                        unsafe { mrv213_chan_destroy(p) };
                    }
                }
                return Err(format!("mrv213_chan_create failed on thread {t}"));
            }
            max_inline = unsafe { mrv213_chan_max_inline(ch) };
            chans.push(ChanHandle::Verbs(ch));
        }
    }

    let mut handles = Vec::with_capacity(cfg.threads);
    for (t, ch) in chans.into_iter().enumerate() {
        let disp = Arc::clone(&dispatch);
        let stats_c = Arc::clone(&stats);
        let cfg_c = cfg.clone();
        // Pin to NUMA-0 physical cores 2..31 on mercury (matching mlx5_2)
        let cpu = 2 + (t % MAX_THREADS);
        let h = thread::spawn(move || {
            pin_cpu(cpu);
            let stream_id = t as u16;
            let l2_bytes = cfg_c.mode.l2_bytes();
            let plen_u32 = cfg_c.mode.payload_len();
            let plen_us = plen_u32 as usize;
            let batch_max = cfg_c.batch.clamp(1, 64);
            let max_inflight_batches: u64 = (1024 / batch_max as u64).max(4);
            let per_thread_mpps = if cfg_c.rate_mpps > 0.0 {
                cfg_c.rate_mpps / (cfg_c.threads as f64)
            } else {
                0.0
            };
            let ticks_per_pkt = if per_thread_mpps > 0.0 {
                tsc_hz / (per_thread_mpps * 1e6)
            } else {
                0.0
            };

            let mut handled_gen = 0u64;
            let mut seq = 1u64;
            let mut slot = 0u32;
            let mut cqe_err = 0u64;

            while !disp.stop.load(Ordering::Relaxed) {
                let g = disp.burst_gen.load(Ordering::Acquire);
                if g == handled_gen {
                    thread::sleep(Duration::from_micros(15));
                    continue;
                }
                handled_gen = g;
                let total_pkts = disp.total_pkts.load(Ordering::Relaxed);
                let burst_id = disp.burst_id.load(Ordering::Relaxed) as u16;
                let run_gen = disp.run_gen.load(Ordering::Relaxed);
                let base = total_pkts / cfg_c.threads as u64;
                let rem = total_pkts % cfg_c.threads as u64;
                let my_pkts = base + if (t as u64) < rem { 1 } else { 0 };

                // Synchronize all threads so burst hits the 100GbE wire simultaneously
                disp.start_barrier.wait();

                let mut sent = 0u64;
                let mut next_tsc = rdtsc() as f64;

                match ch {
                    ChanHandle::Verbs(vch) => {
                        let buf = unsafe { mrv213_chan_buf(vch) };
                        let mut inflight_batches = 0u64;
                        while sent < my_pkts && !disp.stop.load(Ordering::Relaxed) {
                            let n = ((my_pkts - sent) as u32).min(batch_max);
                            if ticks_per_pkt > 0.0 {
                                while (rdtsc() as f64) < next_tsc {
                                    std::hint::spin_loop();
                                }
                                next_tsc += ticks_per_pkt * (n as f64);
                            }
                            while inflight_batches >= max_inflight_batches {
                                let c = unsafe { mrv213_chan_poll_cq(vch, 32, &mut cqe_err) };
                                if c > 0 {
                                    inflight_batches = inflight_batches.saturating_sub(c as u64);
                                } else {
                                    std::hint::spin_loop();
                                }
                            }
                            let start_slot = slot;
                            if plen_us >= 40 {
                                let now_tsc = rdtsc();
                                for _ in 0..n {
                                    let sl = (slot & SLOT_MASK) as usize;
                                    let p = unsafe { buf.add(sl * slot_stride as usize) };
                                    unsafe {
                                        stamp_payload_ptr(
                                            p, plen_us, stream_id, run_gen, burst_id, seq, now_tsc, l2_bytes,
                                        );
                                    }
                                    seq += 1;
                                    slot = slot.wrapping_add(1);
                                }
                            } else {
                                seq += n as u64;
                                slot = slot.wrapping_add(n);
                            }

                            let rc = unsafe {
                                mrv213_chan_post_batch(vch, start_slot, SLOT_MASK, n, plen_u32, seq)
                            };
                            if rc == 0 {
                                inflight_batches += 1;
                                sent += n as u64;
                                stats_c[t].tx_pkts.fetch_add(n as u64, Ordering::Relaxed);
                            } else {
                                stats_c[t].post_err.fetch_add(1, Ordering::Relaxed);
                                break;
                            }
                            if inflight_batches > 0 {
                                let c = unsafe { mrv213_chan_poll_cq(vch, 16, &mut cqe_err) };
                                if c > 0 {
                                    inflight_batches = inflight_batches.saturating_sub(c as u64);
                                }
                            }
                        }
                        // Drain all Send CQEs before marking this burst complete!
                        let drain_deadline = Instant::now() + Duration::from_millis(500);
                        while inflight_batches > 0 && Instant::now() < drain_deadline {
                            let c = unsafe { mrv213_chan_poll_cq(vch, 32, &mut cqe_err) };
                            if c > 0 {
                                inflight_batches = inflight_batches.saturating_sub(c as u64);
                            } else {
                                std::hint::spin_loop();
                            }
                        }
                        if cqe_err > 0 {
                            stats_c[t].cqe_err.fetch_add(cqe_err, Ordering::Relaxed);
                            cqe_err = 0;
                        }
                    }
                    ChanHandle::Udp(uch) => {
                        let buf = unsafe { mrv213_udp_buf(uch) };
                        while sent < my_pkts && !disp.stop.load(Ordering::Relaxed) {
                            let n = ((my_pkts - sent) as u32).min(batch_max);
                            if ticks_per_pkt > 0.0 {
                                while (rdtsc() as f64) < next_tsc {
                                    std::hint::spin_loop();
                                }
                                next_tsc += ticks_per_pkt * (n as f64);
                            }
                            let now_tsc = rdtsc();
                            for i in 0..n as usize {
                                let p = unsafe { buf.add(i * plen_us) };
                                unsafe {
                                    stamp_payload_ptr(
                                        p, plen_us, stream_id, run_gen, burst_id, seq, now_tsc, l2_bytes,
                                    );
                                }
                                seq += 1;
                            }
                            let rc = unsafe { mrv213_udp_send_batch(uch, n) };
                            if rc > 0 {
                                sent += rc as u64;
                                stats_c[t].tx_pkts.fetch_add(rc as u64, Ordering::Relaxed);
                            } else {
                                stats_c[t].post_err.fetch_add(1, Ordering::Relaxed);
                                break;
                            }
                        }
                        let gso = unsafe { mrv213_udp_gso_active(uch) } as u64;
                        stats_c[t].gso_active.store(gso, Ordering::Relaxed);
                    }
                }

                disp.done_count.fetch_add(1, Ordering::AcqRel);
            }

            match ch {
                ChanHandle::Verbs(vch) => unsafe { mrv213_chan_destroy(vch) },
                ChanHandle::Udp(uch) => unsafe { mrv213_udp_destroy(uch) },
            }
        });
        handles.push(h);
    }

    Ok(ActiveWorkers { cfg, dispatch, stats, handles, max_inline })
}

fn shutdown_workers(mut w: ActiveWorkers) {
    w.dispatch.stop.store(true, Ordering::Release);
    for h in w.handles.drain(..) {
        let _ = h.join();
    }
}

fn snap_counters(w: Option<&ActiveWorkers>, et: *mut c_void) -> SnapState {
    let t_app = Instant::now();
    let (mut pkts, mut cqe_err, mut post_err) = (0u64, 0u64, 0u64);
    if let Some(w) = w {
        for s in w.stats.iter() {
            pkts += s.tx_pkts.load(Ordering::Relaxed);
            cqe_err += s.cqe_err.load(Ordering::Relaxed);
            post_err += s.post_err.load(Ordering::Relaxed);
        }
    }
    let mut phy = PhyStats::default();
    let t_phy = Instant::now();
    unsafe {
        mrv213_ethtool_read(et, &mut phy);
    }
    SnapState { t_app, t_phy, app_pkts: pkts, cqe_err, post_err, phy }
}

fn read_u64_le(b: &[u8]) -> u64 {
    u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]])
}

fn run_self_test() {
    for mode in [TxMode::Ud256, TxMode::Udp1500, TxMode::Ud1094] {
        let plen = mode.payload_len() as usize;
        let mut buf = vec![0u8; plen];
        unsafe {
            stamp_payload_ptr(buf.as_mut_ptr(), plen, 5, 101, 7, 4242, 0x1234, mode.l2_bytes());
        }
        let seq = read_u64_le(&buf[0..8]);
        let meta = read_u64_le(&buf[8..16]);
        let cookie = read_u64_le(&buf[16..24]);
        let mid = (plen / 2) & !7;
        let mid_val = read_u64_le(&buf[mid..mid + 8]);
        let tail = read_u64_le(&buf[plen - 8..plen]);
        assert_eq!(seq, 4242);
        assert_eq!(meta, pack_payload_meta(101, 7, 5, mode.l2_bytes()));
        assert_eq!(cookie, payload_cookie(5, 101, 4242));
        assert_eq!(mid_val, cookie ^ PAYLOAD_MID_XOR);
        assert_eq!(tail, payload_tail(5, 101, 4242));
    }
    assert_eq!(TxMode::Uc64.l2_bytes(), 64);
    assert_eq!(TxMode::Ud256.l2_bytes(), 254);
    assert_eq!(TxMode::Udp1500.l2_bytes(), 1500);
    println!("mrv213_client self-test OK");
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--self-test") {
        run_self_test();
        return;
    }

    let mut port: u16 = 7547;
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
    let c_dst = CString::new(dst_ip.as_str()).unwrap();
    let c_src = CString::new(src_ip.as_str()).unwrap();
    let ctx = unsafe { mrv213_open(c_dev.as_ptr(), sgid_idx, c_dst.as_ptr()) };
    assert!(!ctx.is_null(), "failed to open ibv device {}", dev);

    let c_if = CString::new(ifname.as_str()).unwrap();
    let et = unsafe { mrv213_ethtool_open(c_if.as_ptr()) };
    assert!(!et.is_null(), "failed to open ethtool stats for {}", ifname);
    let listener = TcpListener::bind(("0.0.0.0", port)).expect("bind control port");
    println!(
        "MRV213_CLIENT READY port={port} dev={dev} ifname={ifname} src={src_ip} dst={dst_ip} tsc_hz={tsc_hz:.0}"
    );

    let workers_lock: Mutex<Option<ActiveWorkers>> = Mutex::new(None);
    let mut snap0 = snap_counters(None, et);
    let mut active_dur_s: f64 = 0.0;
    let mut bursts_sent: u64 = 0;

    for stream in listener.incoming() {
        let Ok(mut sock) = stream else { continue };
        let _ = sock.set_nodelay(true);
        let mut reader = BufReader::new(sock.try_clone().unwrap());
        let mut line = String::new();
        if reader.read_line(&mut line).unwrap_or(0) == 0 {
            continue;
        }
        let cmd = line.trim();
        let reply = if cmd == "PING" {
            "{\"ok\":true}".to_string()
        } else if let Some(rest) = cmd.strip_prefix("PROBE_RSS ") {
            let mut mode = TxMode::Ud256;
            let mut count: u32 = 64;
            let mut run_gen: u64 = 1;
            for tok in rest.split_whitespace() {
                if let Some((k, v)) = tok.split_once('=') {
                    match k {
                        "mode" => {
                            if let Some(m) = TxMode::from_name(v) {
                                mode = m;
                            }
                        }
                        "count" => count = v.parse::<u32>().unwrap_or(64).clamp(1, 128),
                        "run_gen" => run_gen = v.parse().unwrap_or(1),
                        _ => {}
                    }
                }
            }
            let plen = mode.payload_len();
            let mut probed = 0u32;
            if mode == TxMode::Udp1500 {
                for fl in 1..=count {
                    let sport = (20000u32 + fl) as u16;
                    let u = unsafe {
                        mrv213_udp_create(c_src.as_ptr(), sport, c_dst.as_ptr(), DATA_UDP_PORT, plen, 0)
                    };
                    if u.is_null() { continue; }
                    let buf = unsafe { mrv213_udp_buf(u) };
                    let sid = (fl - 1) as u16;
                    for k in 0..4u32 {
                        let p = unsafe { buf.add(k as usize * plen as usize) };
                        unsafe {
                            stamp_payload_ptr(
                                p, plen as usize, sid, run_gen, 0, (k + 1) as u64, rdtsc(), mode.l2_bytes(),
                            );
                        }
                    }
                    if unsafe { mrv213_udp_send_batch(u, 4) } > 0 {
                        probed += 1;
                    }
                    unsafe { mrv213_udp_destroy(u) };
                }
            } else {
                let is_uc = if mode == TxMode::Uc64 { 1 } else { 0 };
                let stride = ((plen.max(64) + 63) & !63).max(64);
                for fl in 1..=count {
                    let sid = (fl - 1) as u16;
                    let rqpn = if mode == TxMode::Uc64 {
                        UC64_BASE_QPN + sid as u32
                    } else {
                        UD_CARD_QPN
                    };
                    let ch = unsafe {
                        mrv213_chan_create(ctx, is_uc, fl, rqpn, 64, stride, plen.min(188))
                    };
                    if ch.is_null() { continue; }
                    if plen >= 40 {
                        let buf = unsafe { mrv213_chan_buf(ch) };
                        for k in 0..4u32 {
                            let p = unsafe { buf.add(k as usize * stride as usize) };
                            unsafe {
                                stamp_payload_ptr(
                                    p, plen as usize, sid, run_gen, 0, (k + 1) as u64, rdtsc(), mode.l2_bytes(),
                                );
                            }
                        }
                    }
                    if unsafe { mrv213_chan_post_batch(ch, 0, 63, 4, plen, fl as u64) } == 0 {
                        let mut err = 0u64;
                        let t0 = Instant::now();
                        while unsafe { mrv213_chan_poll_cq(ch, 4, &mut err) } == 0
                            && t0.elapsed() < Duration::from_millis(20)
                        {
                            std::hint::spin_loop();
                        }
                        probed += 1;
                    }
                    unsafe { mrv213_chan_destroy(ch) };
                }
            }
            format!("{{\"ok\":true,\"mode\":\"{}\",\"probed\":{probed}}}", mode.as_str())
        } else if let Some(rest) = cmd.strip_prefix("CONFIG ") {
            let mut cfg = RunConfig {
                mode: TxMode::Udp1500,
                threads: 8,
                batch: 32,
                rate_mpps: 0.0,
                run_gen: 1,
                udp_gso: true,
                flow_base: 1,
                flows: Vec::new(),
            };
            let mut parse_err: Option<String> = None;
            for tok in rest.split_whitespace() {
                if let Some((k, v)) = tok.split_once('=') {
                    match k {
                        "mode" => match TxMode::from_name(v) {
                            Some(m) => cfg.mode = m,
                            None => parse_err = Some(format!("bad mode {v}")),
                        },
                        "threads" => cfg.threads = v.parse::<usize>().unwrap_or(8).clamp(1, MAX_THREADS),
                        "batch" => cfg.batch = v.parse::<u32>().unwrap_or(32).clamp(1, 64),
                        "rate_mpps" => cfg.rate_mpps = v.parse().unwrap_or(0.0),
                        "run_gen" => cfg.run_gen = v.parse().unwrap_or(1),
                        "udp_gso" => cfg.udp_gso = v != "0" && v != "false",
                        "flow_base" => cfg.flow_base = v.parse().unwrap_or(1),
                        "flows" => {
                            cfg.flows = v.split(',').filter_map(|x| x.parse::<u32>().ok()).collect();
                        }
                        _ => {}
                    }
                }
            }
            if let Some(e) = parse_err {
                format!("{{\"ok\":false,\"err\":\"{e}\"}}")
            } else {
                let mut guard = workers_lock.lock().unwrap();
                if let Some(old) = guard.take() {
                    shutdown_workers(old);
                }
                match spawn_workers(ctx, &src_ip, &dst_ip, cfg.clone(), tsc_hz) {
                    Ok(w) => {
                        let inl = w.max_inline;
                        *guard = Some(w);
                        snap0 = snap_counters(guard.as_ref(), et);
                        active_dur_s = 0.0;
                        bursts_sent = 0;
                        format!(
                            "{{\"ok\":true,\"mode\":\"{}\",\"threads\":{},\"batch\":{},\"payload\":{},\"l2_bytes\":{},\"max_inline\":{},\"udp_gso\":{}}}",
                            cfg.mode.as_str(),
                            cfg.threads,
                            cfg.batch,
                            cfg.mode.payload_len(),
                            cfg.mode.l2_bytes(),
                            inl,
                            cfg.udp_gso,
                        )
                    }
                    Err(e) => format!("{{\"ok\":false,\"err\":\"{}\"}}", e.replace('"', "'")),
                }
            }
        } else if let Some(rest) = cmd.strip_prefix("SET_RUN_GEN ") {
            let rg: u64 = rest.trim().parse().unwrap_or(1);
            let guard = workers_lock.lock().unwrap();
            if let Some(w) = guard.as_ref() {
                w.dispatch.run_gen.store(rg, Ordering::Release);
                format!("{{\"ok\":true,\"run_gen\":{rg}}}")
            } else {
                "{\"ok\":false,\"err\":\"not configured\"}".to_string()
            }
        } else if cmd == "RESET" {
            let guard = workers_lock.lock().unwrap();
            snap0 = snap_counters(guard.as_ref(), et);
            active_dur_s = 0.0;
            bursts_sent = 0;
            "{\"ok\":true}".to_string()
        } else if let Some(rest) = cmd.strip_prefix("BURST ") {
            let mut burst_id: u64 = 1;
            let mut pkts: u64 = 1024;
            for tok in rest.split_whitespace() {
                if let Some((k, v)) = tok.split_once('=') {
                    match k {
                        "burst_id" => burst_id = v.parse().unwrap_or(1),
                        "pkts" => pkts = v.parse().unwrap_or(1024),
                        _ => {}
                    }
                }
            }
            let guard = workers_lock.lock().unwrap();
            if let Some(w) = guard.as_ref() {
                w.dispatch.done_count.store(0, Ordering::Release);
                w.dispatch.burst_id.store(burst_id, Ordering::Relaxed);
                w.dispatch.total_pkts.store(pkts, Ordering::Relaxed);
                let t0 = Instant::now();
                w.dispatch.burst_gen.fetch_add(1, Ordering::AcqRel);
                let n_threads = w.cfg.threads as u64;
                while w.dispatch.done_count.load(Ordering::Acquire) < n_threads {
                    std::hint::spin_loop();
                }
                let dt_s = t0.elapsed().as_secs_f64().max(1e-9);
                active_dur_s += dt_s;
                bursts_sent += 1;
                let wire_gbps = (pkts as f64 * w.cfg.mode.wire_bytes() as f64 * 8.0) / dt_s / 1e9;
                format!(
                    "{{\"ok\":true,\"burst_id\":{burst_id},\"tx_pkts\":{pkts},\"burst_s\":{dt_s:.6},\"wire_gbps\":{wire_gbps:.4}}}"
                )
            } else {
                "{\"ok\":false,\"err\":\"not configured\"}".to_string()
            }
        } else if cmd == "STATS" {
            let guard = workers_lock.lock().unwrap();
            let snap1 = snap_counters(guard.as_ref(), et);
            let gso_threads: u64 = guard
                .as_ref()
                .map(|w| w.stats.iter().map(|s| s.gso_active.load(Ordering::Relaxed)).sum())
                .unwrap_or(0);
            let dt_app = snap1.t_app.duration_since(snap0.t_app).as_secs_f64().max(1e-9);
            let dt_phy = snap1.t_phy.duration_since(snap0.t_phy).as_secs_f64().max(1e-9);
            let dt_active = active_dur_s.max(1e-9);
            let dp_app = snap1.app_pkts.saturating_sub(snap0.app_pkts);
            let d_cqe = snap1.cqe_err.saturating_sub(snap0.cqe_err);
            let d_post = snap1.post_err.saturating_sub(snap0.post_err);
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
            format!(
                "{{\"bursts\":{bursts_sent},\"dt_active_s\":{dt_active:.6},\"dt_app_s\":{dt_app:.6},\"dt_phy_s\":{dt_phy:.6},\
                 \"app_tx_pkts\":{dp_app},\"cqe_err\":{d_cqe},\"post_err\":{d_post},\"gso_threads\":{gso_threads},\
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
        mrv213_ethtool_close(et);
        mrv213_close(ctx);
    }
}
