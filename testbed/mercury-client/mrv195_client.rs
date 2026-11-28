//! Multi-QP 100GbE non-root hardware-bypass (IBV_QPT_UD RoCEv2) RPC/KV client
//! running on `mercury` (`mlx5_2`, NUMA 0 `cpus 0..T-1`) for MRV-195.
//!
//! Zero external crates; compiles in < 1 s directly on `mercury` with:
//!   gcc -O3 -c mrv195_verbs_shim.c -o mrv195_verbs_shim.o
//!   rustc -O mrv195_client.rs -C link-arg=mrv195_verbs_shim.o -C link-arg=-libverbs -o mrv195_client
//!
//! Features:
//!   - True open-loop Poisson arrival schedule (records `t_now - t_sched`, zero coordinated omission)
//!     and closed-loop saturation / W=1 latency modes.
//!   - Real asymmetric KV GET / PUT protocol (`64 B / 256 B / 1024 B / 4096 B` + `mix`:
//!     80% small 64/256 B GET/PUT + 20% 4 KiB GET).
//!   - Zipf (`s = 0.99`) and Uniform key distributions across 8,192 keys (32.5 MiB server KV store).
//!   - Full end-to-end verification: seq, client_tsc, key, status, version, value words, and 32-bit
//!     checksum over EVERY received value word across multi-fragment 4 KiB responses.

use std::ffi::CString;
use std::io::Write;
use std::sync::{Arc, Barrier};
use std::time::{Duration, Instant};

const ROCE_QKEY: u32 = 0x1111_1111;
const ROCE_CARD_QPN: u32 = 0x0001_2345;
const OP_NET: u8 = 3;
const OP_PROBE: u8 = 4;
const KV_GET: u8 = 1;
const KV_PUT: u8 = 2;
const KV_STATUS_REQ: u8 = 0;
const KV_STATUS_OK: u8 = 1;
const SEQ_MASK: u64 = (1u64 << 38) - 1;
const MAX_SEG_BYTES: usize = 1024;
const MAX_RPC_BYTES: usize = 4096;
const GRH_BYTES: usize = 40;
const RX_SLOTS: u32 = 2048;
const RX_STRIDE: u32 = 2048;
const TX_SLOTS: u32 = 2048;
const TX_STRIDE: u32 = 4096;
const INFLIGHT_CAP: usize = 2048;
const INFLIGHT_MASK: usize = INFLIGHT_CAP - 1;
const KV_NUM_KEYS: usize = 8192;
const ROCE_HDR_LEN: usize = 62;
const ROCE_ICRC_LEN: usize = 4;
const ETH_WIRE_OVERHEAD: usize = ROCE_HDR_LEN + ROCE_ICRC_LEN + 4 + 20; // 90 B/frame

#[repr(C)]
struct UdClient {
    _private: [u8; 0],
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct RxComp {
    slot: u32,
    byte_len: u32,
    status: i32,
}

unsafe extern "C" {
    fn ud_open(
        dev_name: *const i8,
        sgid_index: i32,
        dst_ip: *const u8,
        remote_qpn: u32,
        remote_qkey: u32,
        flow_label: u32,
        rx_slots: u32,
        rx_stride: u32,
        tx_slots: u32,
        tx_stride: u32,
    ) -> *mut UdClient;
    fn ud_set_flow(u: *mut UdClient, flow_label: u32, remote_qpn: u32) -> i32;
    fn ud_rx_buf(u: *mut UdClient) -> *mut u8;
    fn ud_tx_buf(u: *mut UdClient) -> *mut u8;
    fn ud_local_qpn(u: *mut UdClient) -> u32;
    fn ud_max_inline(u: *mut UdClient) -> u32;
    fn ud_post_send_batch(
        u: *mut UdClient,
        tx_slots: *const u32,
        seg_lens: *const u32,
        frag_cnts: *const u32,
        n_rpcs: i32,
        force_sig: i32,
    ) -> i32;
    fn ud_drain_send_cq(u: *mut UdClient) -> i32;
    fn ud_poll_rx(u: *mut UdClient, out: *mut RxComp, max_n: i32) -> i32;
    fn ud_repost_rx_batch(u: *mut UdClient, slots: *const u32, count: i32) -> i32;
    fn ud_close(u: *mut UdClient);
    fn sched_setaffinity(pid: i32, cpusetsize: usize, mask: *const u8) -> i32;
}

#[inline(always)]
fn rdtsc_after_loads() -> u64 {
    let mut aux: u32 = 0;
    unsafe { core::arch::x86_64::__rdtscp(&mut aux) }
}

fn read_cpu_khz(cpu: usize) -> u64 {
    let path = format!("/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq");
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok())
        .unwrap_or(0)
}

fn calibrate_tsc_hz(cpu: usize) -> f64 {
    let t0 = Instant::now();
    let c0 = rdtsc_after_loads();
    std::thread::sleep(Duration::from_millis(60));
    let c1 = rdtsc_after_loads();
    let hz = (c1 - c0) as f64 / t0.elapsed().as_secs_f64();
    // Ramp core + uncore before warmup
    let mut buf = vec![0u64; 4 * 1024 * 1024];
    let ramp = Instant::now();
    let mut idx = 0usize;
    let mut acc = 1u64;
    while ramp.elapsed() < Duration::from_millis(800) {
        for _ in 0..4096 {
            idx = (idx + 8) & (buf.len() - 1);
            let v = unsafe { *buf.get_unchecked(idx) };
            acc = acc.wrapping_mul(6364136223846793005).wrapping_add(v ^ 1);
            unsafe { *buf.get_unchecked_mut(idx) = acc };
        }
        std::hint::black_box(acc);
        if ramp.elapsed() >= Duration::from_millis(350) && read_cpu_khz(cpu) >= 3_500_000 {
            break;
        }
    }
    hz
}

fn pin_cpu(cpu: usize) {
    let mut mask = [0u8; 128];
    mask[cpu / 8] |= 1u8 << (cpu % 8);
    unsafe {
        sched_setaffinity(0, mask.len(), mask.as_ptr());
    }
}

#[inline(always)]
const fn pack_meta(rpc_seq: u64, bytes_div64: u16, frag_idx: u8, frag_cnt: u8, op: u8) -> u64 {
    (rpc_seq & SEQ_MASK)
        | (((bytes_div64 as u64) & 0x3ff) << 38)
        | (((frag_idx as u64) & 0xf) << 48)
        | (((frag_cnt as u64) & 0xf) << 52)
        | ((op as u64) << 56)
}

#[inline(always)]
const fn unpack_meta(meta: u64) -> (u64, u16, u8, u8, u8) {
    let rpc_seq = meta & SEQ_MASK;
    let bytes_div64 = ((meta >> 38) & 0x3ff) as u16;
    let frag_idx = ((meta >> 48) & 0xf) as u8;
    let frag_cnt = ((meta >> 52) & 0xf) as u8;
    let op = (meta >> 56) as u8;
    (rpc_seq, bytes_div64, frag_idx, frag_cnt, op)
}

#[inline(always)]
const fn pack_kv_meta(kv_op: u8, val_cells: u8, status: u8, cksum32: u32) -> u64 {
    (kv_op as u64)
        | ((val_cells as u64) << 8)
        | ((status as u64) << 16)
        | ((cksum32 as u64) << 32)
}

#[inline(always)]
const fn unpack_kv_meta(kv_meta: u64) -> (u8, u8, u8, u32) {
    let kv_op = (kv_meta & 0xff) as u8;
    let val_cells = ((kv_meta >> 8) & 0xff) as u8;
    let status = ((kv_meta >> 16) & 0xff) as u8;
    let cksum32 = (kv_meta >> 32) as u32;
    (kv_op, val_cells, status, cksum32)
}

#[inline(always)]
const fn val_words_for_cells(cells: usize) -> usize {
    if cells == 0 { 0 } else { 1 + (cells - 1) * 6 }
}

#[inline(always)]
const fn frag_cnt_for_bytes(msg_bytes: usize) -> u8 {
    if msg_bytes > MAX_SEG_BYTES { (msg_bytes / MAX_SEG_BYTES) as u8 } else { 1 }
}

#[inline(always)]
const fn kv_value_word(key: u64, version: u64, word_idx: usize) -> u64 {
    let x = key
        .wrapping_mul(0x9e37_79b9_7f4a_7c15)
        .wrapping_add(version.wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ (word_idx as u64).wrapping_mul(0x94d0_49bb_1331_11eb);
    (x ^ (x >> 29)) | 1
}

#[inline(always)]
const fn cksum_init(key: u64, version: u64, val_cells: u8) -> u64 {
    key.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ version.rotate_left(13) ^ ((val_cells as u64) << 40)
}

#[inline(always)]
const fn cksum_step(acc: u64, word: u64) -> u64 {
    acc.rotate_left(7).wrapping_add(word ^ 0x517c_c1b7_2722_0a95)
}

#[inline(always)]
const fn cksum_finish(acc: u64) -> u32 {
    let z = acc ^ (acc >> 32);
    let out = (z as u32) ^ ((z >> 16) as u32).rotate_left(9);
    if out == 0 { 1 } else { out }
}

#[inline(always)]
const fn probe_cookie(rpc_seq: u64) -> u64 {
    rpc_seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0x55aa_55aa_0f0f_f0f0
}

struct Rng64(u64);
impl Rng64 {
    fn new(seed: u64) -> Self {
        Rng64(if seed == 0 { 0x9e37_79b9_7f4a_7c15 } else { seed })
    }
    #[inline(always)]
    fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_f491_4f6c_dd1d)
    }
    #[inline(always)]
    fn next_f64(&mut self) -> f64 {
        let v = (self.next_u64() >> 11) as f64 * (1.0 / ((1u64 << 53) as f64));
        if v < 1e-12 { 1e-12 } else { v }
    }
}

/// Exact Zipf(s) CDF sampler over `0..n` in O(log n).
struct ZipfSampler {
    cdf: Vec<f64>,
}

impl ZipfSampler {
    fn new(n: usize, s: f64) -> Self {
        let mut cdf = Vec::with_capacity(n);
        let mut sum = 0.0f64;
        for i in 1..=n {
            sum += 1.0 / (i as f64).powf(s);
            cdf.push(sum);
        }
        let inv = 1.0 / sum;
        for v in &mut cdf {
            *v *= inv;
        }
        if let Some(last) = cdf.last_mut() {
            *last = 1.0;
        }
        ZipfSampler { cdf }
    }

    #[inline(always)]
    fn sample(&self, u: f64) -> usize {
        match self.cdf.binary_search_by(|v| v.partial_cmp(&u).unwrap_or(std::cmp::Ordering::Less)) {
            Ok(i) | Err(i) => i.min(self.cdf.len() - 1),
        }
    }
}

const B0: usize = 2000;
const B1: usize = 1800;
const B2: usize = 1980;
const B3: usize = 980;
const TOTAL_BINS: usize = B0 + B1 + B2 + B3 + 1;

struct FastHist {
    bins: Vec<u64>,
    count: u64,
    sum_ns: u128,
    max_ns: u64,
}

impl FastHist {
    fn new() -> Self {
        FastHist { bins: vec![0u64; TOTAL_BINS], count: 0, sum_ns: 0, max_ns: 0 }
    }
    #[inline(always)]
    fn record_ns(&mut self, ns: u64) {
        let idx = if ns < 20_000 {
            (ns / 10) as usize
        } else if ns < 200_000 {
            B0 + ((ns - 20_000) / 100) as usize
        } else if ns < 20_000_000 {
            B0 + B1 + ((ns - 200_000) / 10_000) as usize
        } else if ns < 1_000_000_000 {
            B0 + B1 + B2 + ((ns - 20_000_000) / 1_000_000) as usize
        } else {
            TOTAL_BINS - 1
        };
        self.bins[idx] += 1;
        self.count += 1;
        self.sum_ns += ns as u128;
        if ns > self.max_ns {
            self.max_ns = ns;
        }
    }
    fn merge_from(&mut self, o: &FastHist) {
        for (a, &b) in self.bins.iter_mut().zip(o.bins.iter()) {
            *a += b;
        }
        self.count += o.count;
        self.sum_ns += o.sum_ns;
        if o.max_ns > self.max_ns {
            self.max_ns = o.max_ns;
        }
    }
    fn mean_us(&self) -> f64 {
        if self.count == 0 { 0.0 } else { (self.sum_ns as f64) / (self.count as f64) / 1000.0 }
    }
    fn pct_us(&self, q: f64) -> f64 {
        if self.count == 0 {
            return 0.0;
        }
        let target = ((self.count as f64) * q).ceil().max(1.0) as u64;
        let mut acc = 0u64;
        for (i, &c) in self.bins.iter().enumerate() {
            acc += c;
            if acc >= target {
                let ns = if i < B0 {
                    (i as f64 + 0.5) * 10.0
                } else if i < B0 + B1 {
                    20_000.0 + ((i - B0) as f64 + 0.5) * 100.0
                } else if i < B0 + B1 + B2 {
                    200_000.0 + ((i - B0 - B1) as f64 + 0.5) * 10_000.0
                } else if i < B0 + B1 + B2 + B3 {
                    20_000_000.0 + ((i - B0 - B1 - B2) as f64 + 0.5) * 1_000_000.0
                } else {
                    self.max_ns as f64
                };
                return ns / 1000.0;
            }
        }
        self.max_ns as f64 / 1000.0
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum DriveMode {
    Closed,
    Poisson,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum KeyDist {
    Zipf,
    Uniform,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Workload {
    Get64,
    Get256,
    Get1024,
    Get4096,
    Put64,
    Put256,
    Put1024,
    Put4096,
    Mix,
    Rw64,
    Rw256,
}

#[derive(Clone, Copy, Default)]
struct Inflight {
    active: bool,
    measured: bool,
    rpc_seq: u64,
    kv_op: u8,
    key: u64,
    sent_version: u64,
    sent_cksum32: u32,
    req_bytes: usize,
    exp_resp_bytes: usize,
    exp_resp_cells: u8,
    exp_resp_frags: u8,
    rx_mask: u16,
    t_sched: u64,
    t_send: u64,
}

#[derive(Clone)]
struct Config {
    dev: String,
    sgid: i32,
    cpu: usize,
    threads: usize,
    card_queues: usize,
    mode: DriveMode,
    workload: Workload,
    key_dist: KeyDist,
    zipf_s: f64,
    window: usize,
    rate_krps: f64,
    warmup_ms: u64,
    duration_ms: u64,
    max_tx_batch: usize,
    sync_reset: bool,
    seed: u64,
}

fn parse_args() -> Config {
    let mut cfg = Config {
        dev: "mlx5_2".to_string(),
        sgid: 3,
        cpu: 0,
        threads: 4,
        card_queues: 4,
        mode: DriveMode::Closed,
        workload: Workload::Mix,
        key_dist: KeyDist::Zipf,
        zipf_s: 0.99,
        window: 256,
        rate_krps: 1000.0,
        warmup_ms: 400,
        duration_ms: 1500,
        max_tx_batch: 16,
        sync_reset: false,
        seed: 0x1950_c0de_cafe_beef,
    };
    let args: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < args.len() {
        let a = args[i].as_str();
        let next = |idx: usize| -> &str { args.get(idx + 1).map(|s| s.as_str()).unwrap_or("") };
        match a {
            "--dev" => { cfg.dev = next(i).to_string(); i += 2; }
            "--sgid" => { cfg.sgid = next(i).parse().unwrap(); i += 2; }
            "--cpu" => { cfg.cpu = next(i).parse().unwrap(); i += 2; }
            "--threads" => { cfg.threads = next(i).parse().unwrap(); i += 2; }
            "--card-queues" => { cfg.card_queues = next(i).parse().unwrap(); i += 2; }
            "--mode" => {
                cfg.mode = match next(i) {
                    "closed" => DriveMode::Closed,
                    "poisson" => DriveMode::Poisson,
                    other => panic!("unknown mode: {other}"),
                };
                i += 2;
            }
            "--workload" => {
                cfg.workload = match next(i) {
                    "get64" | "64" => Workload::Get64,
                    "get256" | "256" => Workload::Get256,
                    "get1024" | "1024" => Workload::Get1024,
                    "get4096" | "4096" => Workload::Get4096,
                    "put64" => Workload::Put64,
                    "put256" => Workload::Put256,
                    "put1024" => Workload::Put1024,
                    "put4096" => Workload::Put4096,
                    "mix" => Workload::Mix,
                    "rw64" => Workload::Rw64,
                    "rw256" => Workload::Rw256,
                    other => panic!("unknown workload: {other}"),
                };
                i += 2;
            }
            "--key-dist" => {
                cfg.key_dist = match next(i) {
                    "zipf" => KeyDist::Zipf,
                    "uniform" => KeyDist::Uniform,
                    other => panic!("unknown key-dist: {other}"),
                };
                i += 2;
            }
            "--zipf-s" => { cfg.zipf_s = next(i).parse().unwrap(); i += 2; }
            "--window" => { cfg.window = next(i).parse().unwrap(); i += 2; }
            "--rate-krps" => { cfg.rate_krps = next(i).parse().unwrap(); i += 2; }
            "--warmup-ms" => { cfg.warmup_ms = next(i).parse().unwrap(); i += 2; }
            "--duration-ms" => { cfg.duration_ms = next(i).parse().unwrap(); i += 2; }
            "--max-tx-batch" => { cfg.max_tx_batch = next(i).parse().unwrap(); i += 2; }
            "--sync-reset" => { cfg.sync_reset = true; i += 1; }
            "--seed" => { cfg.seed = next(i).parse().unwrap(); i += 2; }
            _ => { i += 1; }
        }
    }
    assert!(cfg.threads >= 1 && cfg.threads <= 8);
    assert!(cfg.window >= cfg.threads && cfg.window <= cfg.threads * INFLIGHT_CAP);
    cfg
}

fn probe_rss_queue(u: *mut UdClient, rx_base: *mut u8, tx_base: *mut u8, tid: usize, target_rxq: usize) -> (u32, usize, u64) {
    let mut comps = [RxComp::default(); 8];
    let mut repost = [0u32; 8];
    let mut probes_sent = 0u64;
    for trial in 0u32..512 {
        let fl = ((tid as u32) * 1024 + trial * 17 + 1) & 0xfffff;
        let rqpn = (ROCE_CARD_QPN + ((tid as u32) * 257 + trial)) & 0x00ff_ffff;
        unsafe { ud_set_flow(u, fl, rqpn) };

        let pseq = ((tid as u64 + 1) << 30) | (trial as u64 + 1);
        let meta = pack_meta(pseq, 1, 0, 1, OP_PROBE);
        let buf = unsafe { std::slice::from_raw_parts_mut(tx_base, 64) };
        buf.fill(0);
        buf[0..8].copy_from_slice(&meta.to_le_bytes());
        buf[16..24].copy_from_slice(&probe_cookie(pseq).to_le_bytes());
        buf[56..64].copy_from_slice(&meta.to_le_bytes());

        let slot = 0u32;
        let len = 64u32;
        let frags = 1u32;
        if unsafe { ud_post_send_batch(u, &slot, &len, &frags, 1, 1) } != 0 {
            continue;
        }
        probes_sent += 1;
        let t0 = Instant::now();
        let mut got_q: Option<usize> = None;
        while t0.elapsed() < Duration::from_millis(50) {
            let _ = unsafe { ud_drain_send_cq(u) };
            let n = unsafe { ud_poll_rx(u, comps.as_mut_ptr(), 8) };
            if n > 0 {
                for i in 0..(n as usize) {
                    repost[i] = comps[i].slot;
                    if comps[i].status == 0 && (comps[i].byte_len as usize) >= GRH_BYTES + 64 {
                        let p = unsafe {
                            std::slice::from_raw_parts(
                                rx_base.add((comps[i].slot as usize) * (RX_STRIDE as usize) + GRH_BYTES),
                                64,
                            )
                        };
                        let w0 = u64::from_le_bytes(p[0..8].try_into().unwrap());
                        let (rseq, _, _, _, op) = unpack_meta(w0);
                        if op == OP_PROBE && rseq == pseq {
                            let q = u64::from_le_bytes(p[8..16].try_into().unwrap()) as usize;
                            got_q = Some(q);
                        }
                    }
                }
                unsafe { ud_repost_rx_batch(u, repost.as_ptr(), n) };
                if got_q.is_some() {
                    break;
                }
            }
        }
        if let Some(q) = got_q {
            if q == target_rxq {
                return (fl, q, probes_sent);
            }
        }
    }
    (0, 0, probes_sent)
}

struct ThreadResult {
    rxq_matched: usize,
    tx_rpcs_total: u64,
    rx_rpcs_total: u64,
    tx_pkts_total: u64,
    rx_pkts_total: u64,
    sent_measured: u64,
    done_measured: u64,
    get_measured: u64,
    put_measured: u64,
    req_bytes_measured: u64,
    resp_bytes_measured: u64,
    wire_tx_bytes_measured: u64,
    wire_rx_bytes_measured: u64,
    bad: u64,
    torn: u64,
    send_err: u64,
    lost: u64,
    window_cap_hits: u64,
    max_inflight: usize,
    dt_s: f64,
    hist_tot: FastHist,
    hist_wire: FastHist,
    hist_small: FastHist,
    hist_large: FastHist,
    hist_get: FastHist,
    hist_put: FastHist,
}

fn main() {
    let cfg = parse_args();
    pin_cpu(cfg.cpu);
    let tsc_hz = calibrate_tsc_hz(cfg.cpu);
    let ticks_to_ns = 1e9 / tsc_hz;
    let cpu_khz_start = read_cpu_khz(cfg.cpu);

    // Precompute Zipf samplers shared across all threads (read-only)
    let zipf_all = Arc::new(ZipfSampler::new(KV_NUM_KEYS, cfg.zipf_s));
    let zipf_mix_64 = Arc::new(ZipfSampler::new(3277, cfg.zipf_s));
    let zipf_mix_256 = Arc::new(ZipfSampler::new(3277, cfg.zipf_s));
    let zipf_mix_4k = Arc::new(ZipfSampler::new(1638, cfg.zipf_s));

    let b_probe = Arc::new(Barrier::new(cfg.threads));
    let b_warmup = Arc::new(Barrier::new(cfg.threads));
    let b_go = Arc::new(Barrier::new(cfg.threads));
    let b_done = Arc::new(Barrier::new(cfg.threads));

    let per_thread_win = if cfg.mode == DriveMode::Poisson {
        (cfg.window / cfg.threads).max(512).min(INFLIGHT_CAP - 1)
    } else {
        (cfg.window / cfg.threads).max(1).min(INFLIGHT_CAP - 1)
    };
    let per_thread_rate_krps = cfg.rate_krps / (cfg.threads as f64);

    let mut handles = Vec::with_capacity(cfg.threads);
    for tid in 0..cfg.threads {
        let cfg = cfg.clone();
        let zipf_all = Arc::clone(&zipf_all);
        let zipf_mix_64 = Arc::clone(&zipf_mix_64);
        let zipf_mix_256 = Arc::clone(&zipf_mix_256);
        let zipf_mix_4k = Arc::clone(&zipf_mix_4k);
        let b_probe = Arc::clone(&b_probe);
        let b_warmup = Arc::clone(&b_warmup);
        let b_go = Arc::clone(&b_go);
        let b_done = Arc::clone(&b_done);
        handles.push(std::thread::spawn(move || {
            let my_cpu = cfg.cpu + tid;
            pin_cpu(my_cpu);

            let cdev = CString::new(cfg.dev.as_str()).unwrap();
            let dst_ip = [10u8, 10, 100, 2];
            let u = unsafe {
                ud_open(
                    cdev.as_ptr(),
                    cfg.sgid,
                    dst_ip.as_ptr(),
                    ROCE_CARD_QPN,
                    ROCE_QKEY,
                    (tid as u32) + 1,
                    RX_SLOTS,
                    RX_STRIDE,
                    TX_SLOTS,
                    TX_STRIDE,
                )
            };
            if u.is_null() {
                panic!("ud_open failed on thread {tid}");
            }
            let rx_base = unsafe { ud_rx_buf(u) };
            let tx_base = unsafe { ud_tx_buf(u) };

            let target_q = tid % cfg.card_queues;
            let (_fl, rxq_matched, _probes) = probe_rss_queue(u, rx_base, tx_base, tid, target_q);
            b_probe.wait();

            let mut rng = Rng64::new(cfg.seed ^ ((tid as u64 + 1).wrapping_mul(0x9e37_79b9_7f4a_7c15)));
            let mut rng_arr = Rng64::new(cfg.seed ^ ((tid as u64 + 17).wrapping_mul(0xbf58_476d_1ce4_e5b9)));

            let mut inflight = [Inflight::default(); INFLIGHT_CAP];
            let mut reasm_buf = vec![0u8; INFLIGHT_CAP * MAX_RPC_BYTES];
            let mut inflight_cnt: usize = 0;

            let mut hist_tot = FastHist::new();
            let mut hist_wire = FastHist::new();
            let mut hist_small = FastHist::new();
            let mut hist_large = FastHist::new();
            let mut hist_get = FastHist::new();
            let mut hist_put = FastHist::new();

            let mut local_seq: u64 = 0;
            let mut put_ver_ctr: u64 = 2 + (tid as u64) * 1_000_000_000;
            let mut tx_rpcs_total: u64 = 0;
            let mut rx_rpcs_total: u64 = 0;
            let mut tx_pkts_total: u64 = 0;
            let mut rx_pkts_total: u64 = 0;
            let mut sent_measured: u64 = 0;
            let mut done_measured: u64 = 0;
            let mut get_measured: u64 = 0;
            let mut put_measured: u64 = 0;
            let mut req_bytes_measured: u64 = 0;
            let mut resp_bytes_measured: u64 = 0;
            let mut wire_tx_bytes_measured: u64 = 0;
            let mut wire_rx_bytes_measured: u64 = 0;
            let mut bad: u64 = 0;
            let mut torn: u64 = 0;
            let mut send_err: u64 = 0;
            let mut window_cap_hits: u64 = 0;
            let mut max_inflight: usize = 0;

            let mut comps = [RxComp::default(); 32];
            let mut repost_slots = [0u32; 32];
            let mut batch_slots = [0u32; 16];
            let mut batch_lens = [0u32; 16];
            let mut batch_frags = [0u32; 16];

            let mean_gap_ticks = if per_thread_rate_krps > 0.0 {
                tsc_hz / (per_thread_rate_krps * 1e3)
            } else {
                0.0
            };

            // Helper: sample next KV op `(kv_op, key, req_bytes, exp_resp_bytes)`
            let sample_op = |rng: &mut Rng64| -> (u8, u64, usize, usize) {
                let pick_key_range = |rng: &mut Rng64, start: usize, len: usize, zipf: &ZipfSampler| -> u64 {
                    let off = match cfg.key_dist {
                        KeyDist::Zipf => zipf.sample(rng.next_f64()),
                        KeyDist::Uniform => (rng.next_u64() as usize) % len,
                    };
                    (start + off) as u64
                };
                match cfg.workload {
                    Workload::Get64 => (KV_GET, pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all), 64, 64),
                    Workload::Get256 => (KV_GET, pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all), 64, 256),
                    Workload::Get1024 => (KV_GET, pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all), 64, 1024),
                    Workload::Get4096 => (KV_GET, pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all), 64, 4096),
                    Workload::Put64 => (KV_PUT, pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all), 64, 64),
                    Workload::Put256 => (KV_PUT, pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all), 256, 64),
                    Workload::Put1024 => (KV_PUT, pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all), 1024, 64),
                    Workload::Put4096 => (KV_PUT, pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all), 4096, 64),
                    Workload::Rw64 => {
                        let k = pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all);
                        if (rng.next_u64() % 100) < 80 { (KV_GET, k, 64, 64) } else { (KV_PUT, k, 64, 64) }
                    }
                    Workload::Rw256 => {
                        let k = pick_key_range(rng, 0, KV_NUM_KEYS, &zipf_all);
                        if (rng.next_u64() % 100) < 80 { (KV_GET, k, 64, 256) } else { (KV_PUT, k, 256, 64) }
                    }
                    Workload::Mix => {
                        // 80% small (40% 64B + 40% 256B, each 80% GET / 20% PUT) + 20% 4KiB GET
                        let r = rng.next_u64() % 100;
                        if r < 40 {
                            let k = pick_key_range(rng, 0, 3277, &zipf_mix_64);
                            if r < 32 { (KV_GET, k, 64, 64) } else { (KV_PUT, k, 64, 64) }
                        } else if r < 80 {
                            let k = pick_key_range(rng, 3277, 3277, &zipf_mix_256);
                            if r < 72 { (KV_GET, k, 64, 256) } else { (KV_PUT, k, 256, 64) }
                        } else {
                            let k = pick_key_range(rng, 6554, 1638, &zipf_mix_4k);
                            (KV_GET, k, 64, 4096)
                        }
                    }
                }
            };

            let mut poll_once = |inflight: &mut [Inflight; INFLIGHT_CAP],
                             reasm_buf: &mut [u8],
                             inflight_cnt: &mut usize,
                             rx_rpcs_total: &mut u64,
                             rx_pkts_total: &mut u64,
                             done_measured: &mut u64,
                             get_measured: &mut u64,
                             put_measured: &mut u64,
                             resp_bytes_measured: &mut u64,
                             wire_rx_bytes_measured: &mut u64,
                             bad: &mut u64,
                             torn: &mut u64,
                             hist_tot: &mut FastHist,
                             hist_wire: &mut FastHist,
                             hist_small: &mut FastHist,
                             hist_large: &mut FastHist,
                             hist_get: &mut FastHist,
                             hist_put: &mut FastHist|
             -> usize {
                let n = unsafe { ud_poll_rx(u, comps.as_mut_ptr(), 32) };
                if n <= 0 {
                    return 0;
                }
                let t_now = rdtsc_after_loads();
                let mut n_repost = 0usize;
                for i in 0..(n as usize) {
                    let c = comps[i];
                    repost_slots[n_repost] = c.slot;
                    n_repost += 1;
                    if c.status != 0 || (c.byte_len as usize) < GRH_BYTES + 64 {
                        *bad += 1;
                        continue;
                    }
                    let plen = (c.byte_len as usize) - GRH_BYTES;
                    let p = unsafe {
                        std::slice::from_raw_parts(
                            rx_base.add((c.slot as usize) * (RX_STRIDE as usize) + GRH_BYTES),
                            plen,
                        )
                    };
                    *rx_pkts_total += 1;

                    let w0 = u64::from_le_bytes(p[0..8].try_into().unwrap());
                    let w_end = u64::from_le_bytes(p[plen - 8..plen].try_into().unwrap());
                    if w0 != w_end {
                        *torn += 1;
                        continue;
                    }
                    let (rseq, bytes_div64, fidx, fcnt, _op) = unpack_meta(w0);
                    let rbytes = (bytes_div64 as usize) * 64;
                    let sidx = (rseq as usize) & INFLIGHT_MASK;
                    let slot = &mut inflight[sidx];
                    if !slot.active
                        || slot.rpc_seq != rseq
                        || slot.exp_resp_bytes != rbytes
                        || slot.exp_resp_frags != fcnt
                        || fidx >= fcnt
                    {
                        *bad += 1;
                        continue;
                    }

                    // For multi-fragment replies, copy segment into per-slot reassembly buffer
                    let full_slice: &[u8] = if fcnt == 1 {
                        if plen != rbytes {
                            *bad += 1;
                            continue;
                        }
                        p
                    } else {
                        let seg_len = rbytes / (fcnt as usize);
                        if plen != seg_len {
                            *bad += 1;
                            continue;
                        }
                        let dst_off = sidx * MAX_RPC_BYTES + (fidx as usize) * seg_len;
                        reasm_buf[dst_off..dst_off + seg_len].copy_from_slice(p);
                        slot.rx_mask |= 1u16 << fidx;
                        let full = (1u16 << fcnt) - 1;
                        if slot.rx_mask != full {
                            continue;
                        }
                        &reasm_buf[sidx * MAX_RPC_BYTES..sidx * MAX_RPC_BYTES + rbytes]
                    };

                    // Full end-to-end verification of complete reply
                    let echo_tsc = u64::from_le_bytes(full_slice[8..16].try_into().unwrap());
                    let kv_meta = u64::from_le_bytes(full_slice[16..24].try_into().unwrap());
                    let hdr_seq = u64::from_le_bytes(full_slice[24..32].try_into().unwrap());
                    let echo_key = u64::from_le_bytes(full_slice[32..40].try_into().unwrap());
                    let resp_ver = u64::from_le_bytes(full_slice[40..48].try_into().unwrap());
                    let (resp_op, resp_vc, status, resp_ck) = unpack_kv_meta(kv_meta);

                    let mut ok = echo_tsc == slot.t_send
                        && hdr_seq == rseq
                        && echo_key == slot.key
                        && resp_op == slot.kv_op
                        && status == KV_STATUS_OK;

                    if ok && slot.kv_op == KV_GET {
                        if resp_vc != slot.exp_resp_cells || resp_ver == 0 {
                            ok = false;
                        } else {
                            let cells = resp_vc as usize;
                            let n_words = val_words_for_cells(cells);
                            let mut acc = cksum_init(slot.key, resp_ver, resp_vc);
                            let w0_val = u64::from_le_bytes(full_slice[48..56].try_into().unwrap());
                            if w0_val != kv_value_word(slot.key, resp_ver, 0) {
                                ok = false;
                            }
                            acc = cksum_step(acc, w0_val);
                            let mut wi = 1usize;
                            let mut last_w = w0_val;
                            for c_idx in 1..cells {
                                let base = c_idx * 64;
                                for k in 1..7 {
                                    let w = u64::from_le_bytes(full_slice[base + k * 8..base + (k + 1) * 8].try_into().unwrap());
                                    acc = cksum_step(acc, w);
                                    last_w = w;
                                    wi += 1;
                                }
                            }
                            if last_w != kv_value_word(slot.key, resp_ver, n_words - 1)
                                || cksum_finish(acc) != resp_ck
                                || wi != n_words
                            {
                                ok = false;
                            }
                        }
                    } else if ok && slot.kv_op == KV_PUT {
                        if resp_ver != slot.sent_version || resp_ck != slot.sent_cksum32 {
                            ok = false;
                        }
                    }

                    slot.active = false;
                    *inflight_cnt -= 1;
                    *rx_rpcs_total += 1;
                    if !ok {
                        *bad += 1;
                        continue;
                    }
                    if slot.measured {
                        *done_measured += 1;
                        *resp_bytes_measured += slot.exp_resp_bytes as u64;
                        *wire_rx_bytes_measured +=
                            slot.exp_resp_bytes as u64 + (slot.exp_resp_frags as u64) * (ETH_WIRE_OVERHEAD as u64);
                        let tot_ns = ((t_now.saturating_sub(slot.t_sched)) as f64 * ticks_to_ns) as u64;
                        let wire_ns = ((t_now.saturating_sub(slot.t_send)) as f64 * ticks_to_ns) as u64;
                        hist_tot.record_ns(tot_ns);
                        hist_wire.record_ns(wire_ns);
                        let max_sz = slot.req_bytes.max(slot.exp_resp_bytes);
                        if max_sz <= 256 {
                            hist_small.record_ns(tot_ns);
                        } else {
                            hist_large.record_ns(tot_ns);
                        }
                        if slot.kv_op == KV_GET {
                            *get_measured += 1;
                            hist_get.record_ns(tot_ns);
                        } else {
                            *put_measured += 1;
                            hist_put.record_ns(tot_ns);
                        }
                    }
                }
                unsafe { ud_repost_rx_batch(u, repost_slots.as_ptr(), n_repost as i32) };
                n as usize
            };

            // Two phases: phase 0 = warmup (measured = false), phase 1 = measurement (measured = true)
            let mut t_meas_start_tsc = 0u64;
            let mut t_meas_end_tsc = 0u64;

            for phase in 0..2 {
                let measured = phase == 1;
                let phase_ms = if measured { cfg.duration_ms } else { cfg.warmup_ms };
                if phase == 1 {
                    if inflight_cnt > 0 {
                        let stuck: Vec<u64> = inflight.iter().filter(|x| x.active).map(|x| x.rpc_seq & ((1u64 << 34) - 1)).collect();
                        eprintln!("mrv195_client tid={tid} warmup ended with inflight={inflight_cnt} tx={tx_rpcs_total} rx={rx_rpcs_total} bad={bad} torn={torn} stuck_lseq={stuck:?}");
                    }
                    b_warmup.wait();
                    if tid == 0 && cfg.sync_reset {
                        println!("WARMUP_DONE");
                        let _ = std::io::stdout().flush();
                        let mut go = String::new();
                        let _ = std::io::stdin().read_line(&mut go);
                    }
                    b_go.wait();
                    t_meas_start_tsc = rdtsc_after_loads();
                }

                let t_phase_start = Instant::now();
                let mut t_next_sched = rdtsc_after_loads() as f64;
                let mut stop_sending = false;
                let mut drain_deadline: Option<Instant> = None;
                let mut last_rx = Instant::now();
                let mut iter = 0u64;

                loop {
                    iter = iter.wrapping_add(1);
                    if (iter & 0xf) == 0 {
                        let _ = unsafe { ud_drain_send_cq(u) };
                    }
                    let got = poll_once(
                        &mut inflight,
                        &mut reasm_buf,
                        &mut inflight_cnt,
                        &mut rx_rpcs_total,
                        &mut rx_pkts_total,
                        &mut done_measured,
                        &mut get_measured,
                        &mut put_measured,
                        &mut resp_bytes_measured,
                        &mut wire_rx_bytes_measured,
                        &mut bad,
                        &mut torn,
                        &mut hist_tot,
                        &mut hist_wire,
                        &mut hist_small,
                        &mut hist_large,
                        &mut hist_get,
                        &mut hist_put,
                    );
                    if got > 0 {
                        last_rx = Instant::now();
                    } else if inflight_cnt > 0 && (iter & 0x3fff) == 0 && last_rx.elapsed() > Duration::from_secs(3) {
                        eprintln!("mrv195_client tid={tid} stalled >3s phase={phase} inflight={inflight_cnt}");
                        if measured {
                            t_meas_end_tsc = rdtsc_after_loads();
                        }
                        break;
                    }

                    if !stop_sending && t_phase_start.elapsed() >= Duration::from_millis(phase_ms) {
                        stop_sending = true;
                        if measured {
                            t_meas_end_tsc = rdtsc_after_loads();
                        }
                        drain_deadline = Some(Instant::now() + Duration::from_millis(1000));
                    }

                    if stop_sending {
                        if inflight_cnt == 0 {
                            break;
                        }
                        if let Some(dl) = drain_deadline {
                            if Instant::now() >= dl {
                                break;
                            }
                        }
                        continue;
                    }

                    let mut n_batch = 0usize;
                    let mut wr_batch = 0usize;
                    let now_tsc = rdtsc_after_loads();
                    while n_batch < cfg.max_tx_batch && wr_batch <= 60 {
                        if inflight_cnt >= per_thread_win {
                            if measured && cfg.mode == DriveMode::Poisson && (now_tsc as f64) >= t_next_sched {
                                window_cap_hits += 1;
                            }
                            break;
                        }
                        let t_sched = match cfg.mode {
                            DriveMode::Closed => now_tsc,
                            DriveMode::Poisson => {
                                if (now_tsc as f64) < t_next_sched {
                                    break;
                                }
                                let ts = t_next_sched as u64;
                                t_next_sched += -rng_arr.next_f64().ln() * mean_gap_ticks;
                                ts
                            }
                        };

                        local_seq += 1;
                        let rpc_seq = ((tid as u64) << 34) | (local_seq & ((1u64 << 34) - 1));
                        let tx_slot = (rpc_seq as usize) & INFLIGHT_MASK;
                        if inflight[tx_slot].active {
                            break;
                        }

                        let (kv_op, key, req_bytes, exp_resp_bytes) = sample_op(&mut rng);
                        let req_cells = (req_bytes / 64) as u8;
                        let exp_resp_cells = (exp_resp_bytes / 64) as u8;
                        let req_frags = frag_cnt_for_bytes(req_bytes);
                        let exp_resp_frags = frag_cnt_for_bytes(exp_resp_bytes);
                        let seg_len = if req_frags > 1 { MAX_SEG_BYTES } else { req_bytes };

                        let t_send = if n_batch == 0 { now_tsc } else { rdtsc_after_loads() };
                        let tx_ptr = unsafe { tx_base.add(tx_slot * (TX_STRIDE as usize)) };
                        let buf = unsafe { std::slice::from_raw_parts_mut(tx_ptr, req_bytes) };

                        let (sent_ver, sent_ck) = if kv_op == KV_PUT {
                            put_ver_ctr += 1;
                            let ver = put_ver_ctr;
                            let cells = req_cells as usize;
                            let seg_cells = if req_frags > 1 { MAX_SEG_BYTES / 64 } else { cells };
                            let mut acc = cksum_init(key, ver, req_cells);
                            let w0_val = kv_value_word(key, ver, 0);
                            acc = cksum_step(acc, w0_val);
                            let mut wi = 1usize;
                            for c_idx in 1..cells {
                                let base = c_idx * 64;
                                let fidx = (c_idx / seg_cells) as u8;
                                let seg_meta = pack_meta(rpc_seq, req_cells as u16, fidx, req_frags, OP_NET);
                                let is_start = (c_idx % seg_cells) == 0;
                                let is_end = ((c_idx + 1) % seg_cells) == 0;
                                let head_t = if is_start { seg_meta } else { seg_meta ^ (c_idx as u64) };
                                let tail_t = if is_end { seg_meta } else { head_t };
                                buf[base..base + 8].copy_from_slice(&head_t.to_le_bytes());
                                for k in 1..7 {
                                    let w = kv_value_word(key, ver, wi);
                                    wi += 1;
                                    acc = cksum_step(acc, w);
                                    buf[base + k * 8..base + (k + 1) * 8].copy_from_slice(&w.to_le_bytes());
                                }
                                buf[base + 56..base + 64].copy_from_slice(&tail_t.to_le_bytes());
                            }
                            let ck = cksum_finish(acc);
                            let c0_meta = pack_meta(rpc_seq, req_cells as u16, 0, req_frags, OP_NET);
                            let kvm = pack_kv_meta(KV_PUT, req_cells, KV_STATUS_REQ, ck);
                            buf[0..8].copy_from_slice(&c0_meta.to_le_bytes());
                            buf[8..16].copy_from_slice(&t_send.to_le_bytes());
                            buf[16..24].copy_from_slice(&kvm.to_le_bytes());
                            buf[24..32].copy_from_slice(&rpc_seq.to_le_bytes());
                            buf[32..40].copy_from_slice(&key.to_le_bytes());
                            buf[40..48].copy_from_slice(&ver.to_le_bytes());
                            buf[48..56].copy_from_slice(&w0_val.to_le_bytes());
                            buf[56..64].copy_from_slice(&c0_meta.to_le_bytes());
                            (ver, ck)
                        } else {
                            // 64 B KV_GET request
                            let c0_meta = pack_meta(rpc_seq, 1, 0, 1, OP_NET);
                            let kvm = pack_kv_meta(KV_GET, exp_resp_cells, KV_STATUS_REQ, 1);
                            buf[0..8].copy_from_slice(&c0_meta.to_le_bytes());
                            buf[8..16].copy_from_slice(&t_send.to_le_bytes());
                            buf[16..24].copy_from_slice(&kvm.to_le_bytes());
                            buf[24..32].copy_from_slice(&rpc_seq.to_le_bytes());
                            buf[32..40].copy_from_slice(&key.to_le_bytes());
                            buf[40..48].copy_from_slice(&0u64.to_le_bytes());
                            buf[48..56].copy_from_slice(&0u64.to_le_bytes());
                            buf[56..64].copy_from_slice(&c0_meta.to_le_bytes());
                            (0, 1)
                        };

                        batch_slots[n_batch] = tx_slot as u32;
                        batch_lens[n_batch] = seg_len as u32;
                        batch_frags[n_batch] = req_frags as u32;
                        n_batch += 1;
                        wr_batch += req_frags as usize;

                        inflight[tx_slot] = Inflight {
                            active: true,
                            measured,
                            rpc_seq,
                            kv_op,
                            key,
                            sent_version: sent_ver,
                            sent_cksum32: sent_ck,
                            req_bytes,
                            exp_resp_bytes,
                            exp_resp_cells,
                            exp_resp_frags,
                            rx_mask: 0,
                            t_sched,
                            t_send,
                        };
                        inflight_cnt += 1;
                        if inflight_cnt > max_inflight {
                            max_inflight = inflight_cnt;
                        }
                        tx_rpcs_total += 1;
                        tx_pkts_total += req_frags as u64;
                        if measured {
                            sent_measured += 1;
                            req_bytes_measured += req_bytes as u64;
                            wire_tx_bytes_measured +=
                                req_bytes as u64 + (req_frags as u64) * (ETH_WIRE_OVERHEAD as u64);
                        }
                    }

                    if n_batch > 0 {
                        let force_sig = if per_thread_win <= 2 { 1 } else { 0 };
                        let rc = unsafe {
                            ud_post_send_batch(
                                u,
                                batch_slots.as_ptr(),
                                batch_lens.as_ptr(),
                                batch_frags.as_ptr(),
                                n_batch as i32,
                                force_sig,
                            )
                        };
                        if rc != 0 {
                            send_err += 1;
                        }
                    }
                }
            }
            b_done.wait();
            if tid == 0 && cfg.sync_reset {
                println!("MEAS_DONE");
                let _ = std::io::stdout().flush();
            }

            let lost = inflight_cnt as u64 + tx_rpcs_total.saturating_sub(rx_rpcs_total + inflight_cnt as u64);
            let dt_s = if t_meas_end_tsc > t_meas_start_tsc {
                (t_meas_end_tsc - t_meas_start_tsc) as f64 / tsc_hz
            } else {
                0.0
            };
            unsafe { ud_close(u) };
            ThreadResult {
                rxq_matched,
                tx_rpcs_total,
                rx_rpcs_total,
                tx_pkts_total,
                rx_pkts_total,
                sent_measured,
                done_measured,
                get_measured,
                put_measured,
                req_bytes_measured,
                resp_bytes_measured,
                wire_tx_bytes_measured,
                wire_rx_bytes_measured,
                bad,
                torn,
                send_err,
                lost,
                window_cap_hits,
                max_inflight,
                dt_s,
                hist_tot,
                hist_wire,
                hist_small,
                hist_large,
                hist_get,
                hist_put,
            }
        }));
    }

    let mut tot_tx = 0u64;
    let mut tot_rx = 0u64;
    let mut tot_tx_pkts = 0u64;
    let mut tot_rx_pkts = 0u64;
    let mut tot_sent_m = 0u64;
    let mut tot_done_m = 0u64;
    let mut tot_get_m = 0u64;
    let mut tot_put_m = 0u64;
    let mut tot_req_b = 0u64;
    let mut tot_resp_b = 0u64;
    let mut tot_wire_tx_b = 0u64;
    let mut tot_wire_rx_b = 0u64;
    let mut tot_bad = 0u64;
    let mut tot_torn = 0u64;
    let mut tot_send_err = 0u64;
    let mut tot_lost = 0u64;
    let mut tot_wcap = 0u64;
    let mut max_inf = 0usize;
    let mut dt_sum = 0.0f64;
    let mut rxqs = Vec::new();

    let mut hist_tot = FastHist::new();
    let mut hist_wire = FastHist::new();
    let mut hist_small = FastHist::new();
    let mut hist_large = FastHist::new();
    let mut hist_get = FastHist::new();
    let mut hist_put = FastHist::new();

    for h in handles {
        let r = h.join().unwrap();
        rxqs.push(r.rxq_matched.to_string());
        tot_tx += r.tx_rpcs_total;
        tot_rx += r.rx_rpcs_total;
        tot_tx_pkts += r.tx_pkts_total;
        tot_rx_pkts += r.rx_pkts_total;
        tot_sent_m += r.sent_measured;
        tot_done_m += r.done_measured;
        tot_get_m += r.get_measured;
        tot_put_m += r.put_measured;
        tot_req_b += r.req_bytes_measured;
        tot_resp_b += r.resp_bytes_measured;
        tot_wire_tx_b += r.wire_tx_bytes_measured;
        tot_wire_rx_b += r.wire_rx_bytes_measured;
        tot_bad += r.bad;
        tot_torn += r.torn;
        tot_send_err += r.send_err;
        tot_lost += r.lost;
        tot_wcap += r.window_cap_hits;
        if r.max_inflight > max_inf {
            max_inf = r.max_inflight;
        }
        dt_sum += r.dt_s;
        hist_tot.merge_from(&r.hist_tot);
        hist_wire.merge_from(&r.hist_wire);
        hist_small.merge_from(&r.hist_small);
        hist_large.merge_from(&r.hist_large);
        hist_get.merge_from(&r.hist_get);
        hist_put.merge_from(&r.hist_put);
    }

    let dt_s = dt_sum / (cfg.threads as f64);
    let achieved_mrps = if dt_s > 0.0 { tot_done_m as f64 / dt_s / 1e6 } else { 0.0 };
    let payload_tx_gbps = if dt_s > 0.0 { tot_req_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let payload_rx_gbps = if dt_s > 0.0 { tot_resp_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let wire_tx_gbps = if dt_s > 0.0 { tot_wire_tx_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let wire_rx_gbps = if dt_s > 0.0 { tot_wire_rx_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let verified = tot_lost == 0 && tot_bad == 0 && tot_torn == 0 && tot_send_err == 0 && tot_tx == tot_rx;
    let cpu_khz_end = read_cpu_khz(cfg.cpu);

    println!(
        "MRV195_CLIENT_JSON {{\"verified\":{},\"threads\":{},\"rxqs\":[{}],\"tsc_ghz\":{:.6},\"cpu_khz_start\":{},\"cpu_khz_end\":{},\"dt_s\":{:.6},\"tx_rpcs_total\":{},\"rx_rpcs_total\":{},\"tx_pkts_total\":{},\"rx_pkts_total\":{},\"sent_measured\":{},\"done_measured\":{},\"get_measured\":{},\"put_measured\":{},\"achieved_mrps\":{:.6},\"payload_tx_gbps\":{:.4},\"payload_rx_gbps\":{:.4},\"wire_tx_gbps\":{:.4},\"wire_rx_gbps\":{:.4},\"tot_p50_us\":{:.3},\"tot_p95_us\":{:.3},\"tot_p99_us\":{:.3},\"tot_p999_us\":{:.3},\"tot_mean_us\":{:.3},\"wire_p50_us\":{:.3},\"wire_p99_us\":{:.3},\"small_cnt\":{},\"small_p50_us\":{:.3},\"small_p99_us\":{:.3},\"large_cnt\":{},\"large_p50_us\":{:.3},\"large_p99_us\":{:.3},\"get_p50_us\":{:.3},\"get_p99_us\":{:.3},\"put_p50_us\":{:.3},\"put_p99_us\":{:.3},\"max_inflight\":{},\"window_cap_hits\":{},\"lost\":{},\"bad\":{},\"torn\":{},\"send_err\":{}}}",
        verified,
        cfg.threads,
        rxqs.join(","),
        tsc_hz / 1e9,
        cpu_khz_start,
        cpu_khz_end,
        dt_s,
        tot_tx,
        tot_rx,
        tot_tx_pkts,
        tot_rx_pkts,
        tot_sent_m,
        tot_done_m,
        tot_get_m,
        tot_put_m,
        achieved_mrps,
        payload_tx_gbps,
        payload_rx_gbps,
        wire_tx_gbps,
        wire_rx_gbps,
        hist_tot.pct_us(0.50),
        hist_tot.pct_us(0.95),
        hist_tot.pct_us(0.99),
        hist_tot.pct_us(0.999),
        hist_tot.mean_us(),
        hist_wire.pct_us(0.50),
        hist_wire.pct_us(0.99),
        hist_small.count,
        hist_small.pct_us(0.50),
        hist_small.pct_us(0.99),
        hist_large.count,
        hist_large.pct_us(0.50),
        hist_large.pct_us(0.99),
        hist_get.pct_us(0.50),
        hist_get.pct_us(0.99),
        hist_put.pct_us(0.50),
        hist_put.pct_us(0.99),
        max_inf,
        tot_wcap,
        tot_lost,
        tot_bad,
        tot_torn,
        tot_send_err
    );

    if !verified {
        std::process::exit(2);
    }
}
