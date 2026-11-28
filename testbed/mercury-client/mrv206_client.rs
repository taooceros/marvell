//! Multi-QP 100GbE non-root hardware-bypass (IBV_QPT_UD RoCEv2) real Memcached Binary
//! UDP client running on `mercury` (`mlx5_2`, NUMA 0 `cpus 0..T-1`) for MRV-206.
//!
//! Zero external crates; compiles in < 1 s directly on `mercury` with:
//!   gcc -O3 -c mrv206_verbs_shim.c -o mrv206_verbs_shim.o
//!   rustc -O mrv206_client.rs -C link-arg=mrv206_verbs_shim.o -C link-arg=-libverbs -o mrv206_client
//!
//! Features:
//!   - Emits 100% genuine Memcached Binary UDP datagrams (8 B Memcached UDP header +
//!     24 B `protocol_binary_request_header` + extras + 16 B ASCII keys + self-verifying values).
//!   - True open-loop Poisson arrival schedule (`t_now - t_sched`, zero coordinated omission)
//!     and closed-loop saturation / W=1 latency modes.
//!   - Workloads:
//!       * `get8`, `set8`, `rw8` (95/5), `get128`, `set128`, `rw128` (95/5)
//!       * `get1024`, `set1024`, `get3840`, `set3840`
//!       * `small_mix` (Facebook USR/ETC small: 50% 8 B + 50% 128 B, 95% GET / 5% SET)
//!       * `etc_mix`   (Facebook ETC mixed-size: 40% 8 B + 40% 128 B + 20% 3840 B,
//!                      95% GET / 5% SET on small + GET on 3840 B, Zipf s=0.99)
//!   - Strict end-to-end verification on EVERY response:
//!       Memcached UDP header, binary magic 0x81, opcode, status 0x0000, opaque, non-zero CAS,
//!       flags, embedded key, version >= 1, every value word, and 64-bit checksum.

use std::ffi::CString;
use std::io::Write;
use std::sync::{Arc, Barrier};
use std::time::{Duration, Instant};

const ROCE_QKEY: u32 = 0x1111_1111;
const ROCE_CARD_QPN: u32 = 0x0001_2345;
const OP_FRAG: u8 = 0xfa;
const OP_PROBE_WIRE: u8 = 0xfe;

const MC_UDP_HDR_LEN: usize = 8;
const MC_UDP_DATA_SIZE: usize = 1392;
const MC_UDP_MAX_PAYLOAD: usize = 1400;
const MC_BIN_HDR_LEN: usize = 24;
const MC_KEY_LEN: usize = 16;
const MC_MAGIC_REQ: u8 = 0x80;
const MC_MAGIC_RES: u8 = 0x81;
const MC_CMD_GET: u8 = 0x00;
const MC_CMD_SET: u8 = 0x01;
const MC_STATUS_OK: u16 = 0x0000;
const MC_ITEM_FLAGS: u32 = 0x2060_0001;
const OP_PUSH_MAX_MC_BYTES: usize = 192;
const WIRE_SEQ_MASK: u64 = (1u64 << 38) - 1;
const LOCAL_SEQ_MASK: u64 = (1u64 << 29) - 1;
const MAX_FRAG_PAYLOAD: usize = 1008;
const MAX_SEG_BYTES: usize = 1024;
const MAX_RPC_BYTES: usize = 4096;
const GRH_BYTES: usize = 40;
const RX_SLOTS: u32 = 2048;
const RX_STRIDE: u32 = 2048;
const TX_SLOTS: u32 = 2048;
const TX_STRIDE: u32 = 4096;
const INFLIGHT_CAP: usize = 2048;
const INFLIGHT_MASK: usize = INFLIGHT_CAP - 1;
const ROCE_HDR_LEN: usize = 62;
const ROCE_ICRC_LEN: usize = 4;
const ETH_WIRE_OVERHEAD: usize = ROCE_HDR_LEN + ROCE_ICRC_LEN + 4 + 20;

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
    let mut buf = vec![0u64; 4 * 1024 * 1024];
    let ramp = Instant::now();
    let mut idx = 0usize;
    let mut acc = 1u64;
    while ramp.elapsed() < Duration::from_millis(600) {
        for _ in 0..4096 {
            idx = (idx + 8) & (buf.len() - 1);
            let v = unsafe { *buf.get_unchecked(idx) };
            acc = acc.wrapping_mul(6364136223846793005).wrapping_add(v ^ 1);
            unsafe { *buf.get_unchecked_mut(idx) = acc };
        }
        std::hint::black_box(acc);
        if ramp.elapsed() >= Duration::from_millis(250) && read_cpu_khz(cpu) >= 3_500_000 {
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
const fn pack_wire_meta(rpc_seq: u64, mc_len: u16, frag_idx: u8, frag_cnt: u8, op: u8) -> u64 {
    (rpc_seq & WIRE_SEQ_MASK)
        | (((mc_len as u64) & 0xfff) << 38)
        | (((frag_idx as u64) & 0x7) << 50)
        | (((frag_cnt as u64) & 0x7) << 53)
        | ((op as u64) << 56)
}

#[inline(always)]
const fn unpack_wire_meta(meta: u64) -> (u64, u16, u8, u8, u8) {
    let rpc_seq = meta & WIRE_SEQ_MASK;
    let mc_len = ((meta >> 38) & 0xfff) as u16;
    let frag_idx = ((meta >> 50) & 0x7) as u8;
    let frag_cnt = ((meta >> 53) & 0x7) as u8;
    let op = (meta >> 56) as u8;
    (rpc_seq, mc_len, frag_idx, frag_cnt, op)
}

#[inline(always)]
const fn opaque_from_rpc_seq(rpc_seq: u64) -> u32 {
    let tid = ((rpc_seq >> 34) & 7) as u32;
    let lseq = (rpc_seq & LOCAL_SEQ_MASK) as u32;
    (tid << 29) | lseq
}

#[inline(always)]
const fn rpc_seq_from_opaque(opaque: u32) -> u64 {
    let tid = ((opaque >> 29) & 7) as u64;
    let lseq = (opaque & LOCAL_SEQ_MASK as u32) as u64;
    (tid << 34) | lseq
}

#[inline(always)]
const fn roce_frag_cnt(mc_len: usize) -> u8 {
    if mc_len <= MAX_SEG_BYTES {
        1
    } else {
        ((mc_len + MAX_FRAG_PAYLOAD - 1) / MAX_FRAG_PAYLOAD) as u8
    }
}

#[inline(always)]
const fn mc_udp_dgram_cnt(bin_len: usize) -> u16 {
    if bin_len == 0 {
        1
    } else {
        ((bin_len + MC_UDP_DATA_SIZE - 1) / MC_UDP_DATA_SIZE) as u16
    }
}
#[inline(always)]
const fn probe_cookie(rpc_seq: u64) -> u64 {
    rpc_seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0x55aa_55aa_0f0f_f0f0
}

#[inline(always)]
const fn splitmix64(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ (x >> 31)
}

#[inline(always)]
const fn mc_val_word(key: u64, ver: u64, word_idx: usize) -> u64 {
    splitmix64(
        key.wrapping_mul(0x9e37_79b9_7f4a_7c15)
            ^ ver.wrapping_mul(0xbf58_476d_1ce4_e5b9)
            ^ (word_idx as u64).wrapping_mul(0x94d0_49bb_1331_11eb),
    )
}

#[inline(always)]
const fn mc_val8_tag(key: u32, ver: u16) -> u16 {
    let h = splitmix64(((key as u64) << 16) | (ver as u64));
    ((h ^ (h >> 32)) & 0xffff) as u16
}

#[inline(always)]
fn format_mc_key(key: u64, dst: &mut [u8; MC_KEY_LEN]) {
    dst[0] = b'k';
    let mut v = key;
    for i in (1..MC_KEY_LEN).rev() {
        dst[i] = b'0' + ((v % 10) as u8);
        v /= 10;
    }
}

fn fill_mc_value(key: u64, ver: u64, dst: &mut [u8]) {
    let n = dst.len();
    if n == 8 {
        let k32 = key as u32;
        let v16 = (ver as u16).max(1);
        let tag = mc_val8_tag(k32, v16);
        dst[0..4].copy_from_slice(&k32.to_le_bytes());
        dst[4..6].copy_from_slice(&v16.to_le_bytes());
        dst[6..8].copy_from_slice(&tag.to_le_bytes());
        return;
    }
    let n_words = n / 8;
    dst[0..8].copy_from_slice(&key.to_le_bytes());
    dst[8..16].copy_from_slice(&ver.to_le_bytes());
    let mut acc = splitmix64(key ^ ver.rotate_left(17) ^ (n as u64).rotate_left(33));
    for wi in 2..(n_words - 1) {
        let w = mc_val_word(key, ver, wi);
        dst[wi * 8..(wi + 1) * 8].copy_from_slice(&w.to_le_bytes());
        acc = acc.rotate_left(7).wrapping_add(w ^ 0x517c_c1b7_2722_0a95);
    }
    if n_words >= 3 {
        let last = (n_words - 1) * 8;
        dst[last..last + 8].copy_from_slice(&acc.to_le_bytes());
    }
}

#[inline(always)]
fn verify_mc_value(expected_key: u64, val: &[u8]) -> bool {
    let n = val.len();
    if n < 8 || (n & 7) != 0 {
        return false;
    }
    if n == 8 {
        let k32 = u32::from_le_bytes([val[0], val[1], val[2], val[3]]);
        let v16 = u16::from_le_bytes([val[4], val[5]]);
        let tag = u16::from_le_bytes([val[6], val[7]]);
        return k32 == (expected_key as u32) && v16 >= 1 && tag == mc_val8_tag(k32, v16);
    }
    let n_words = n / 8;
    let k = u64::from_le_bytes(val[0..8].try_into().unwrap());
    let ver = u64::from_le_bytes(val[8..16].try_into().unwrap());
    if k != expected_key || ver == 0 {
        return false;
    }
    let mut acc = splitmix64(k ^ ver.rotate_left(17) ^ (n as u64).rotate_left(33));
    for wi in 2..(n_words - 1) {
        let w = u64::from_le_bytes(val[wi * 8..(wi + 1) * 8].try_into().unwrap());
        if w != mc_val_word(k, ver, wi) {
            return false;
        }
        acc = acc.rotate_left(7).wrapping_add(w ^ 0x517c_c1b7_2722_0a95);
    }
    if n_words >= 3 {
        let last = (n_words - 1) * 8;
        let got_acc = u64::from_le_bytes(val[last..last + 8].try_into().unwrap());
        if got_acc != acc {
            return false;
        }
    }
    true
}

fn build_mc_get_udp(req_id: u16, opaque: u32, key: u64, out: &mut [u8]) -> usize {
    let total = MC_UDP_HDR_LEN + MC_BIN_HDR_LEN + MC_KEY_LEN; // 48 B
    out[0..2].copy_from_slice(&req_id.to_be_bytes());
    out[2..4].copy_from_slice(&0u16.to_be_bytes());
    out[4..6].copy_from_slice(&1u16.to_be_bytes());
    out[6..8].copy_from_slice(&0u16.to_be_bytes());

    let h = &mut out[8..32];
    h.fill(0);
    h[0] = MC_MAGIC_REQ;
    h[1] = MC_CMD_GET;
    h[2..4].copy_from_slice(&(MC_KEY_LEN as u16).to_be_bytes());
    h[8..12].copy_from_slice(&(MC_KEY_LEN as u32).to_be_bytes());
    h[12..16].copy_from_slice(&opaque.to_be_bytes());

    let mut kbuf = [0u8; MC_KEY_LEN];
    format_mc_key(key, &mut kbuf);
    out[32..total].copy_from_slice(&kbuf);
    total
}

fn build_mc_set_udp(req_id: u16, opaque: u32, key: u64, ver: u64, val_len: usize, out: &mut [u8]) -> usize {
    let total = MC_UDP_HDR_LEN + MC_BIN_HDR_LEN + 8 + MC_KEY_LEN + val_len;
    out[0..2].copy_from_slice(&req_id.to_be_bytes());
    out[2..4].copy_from_slice(&0u16.to_be_bytes());
    out[4..6].copy_from_slice(&1u16.to_be_bytes());
    out[6..8].copy_from_slice(&0u16.to_be_bytes());

    let h = &mut out[8..32];
    h.fill(0);
    h[0] = MC_MAGIC_REQ;
    h[1] = MC_CMD_SET;
    h[2..4].copy_from_slice(&(MC_KEY_LEN as u16).to_be_bytes());
    h[4] = 8;
    let body_len = (8 + MC_KEY_LEN + val_len) as u32;
    h[8..12].copy_from_slice(&body_len.to_be_bytes());
    h[12..16].copy_from_slice(&opaque.to_be_bytes());

    out[32..36].copy_from_slice(&MC_ITEM_FLAGS.to_be_bytes());
    out[36..40].copy_from_slice(&0u32.to_be_bytes());

    let mut kbuf = [0u8; MC_KEY_LEN];
    format_mc_key(key, &mut kbuf);
    out[40..40 + MC_KEY_LEN].copy_from_slice(&kbuf);
    fill_mc_value(key, ver, &mut out[40 + MC_KEY_LEN..total]);
    total
}

/// Fast xoshiro256** PRNG
struct Rng64 {
    s: [u64; 4],
}

impl Rng64 {
    fn new(seed: u64) -> Self {
        let mut x = seed;
        let mut next = || {
            x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
            let mut z = x;
            z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
            z ^ (z >> 31)
        };
        Rng64 { s: [next(), next(), next(), next()] }
    }

    #[inline(always)]
    fn next_u64(&mut self) -> u64 {
        let res = self.s[1].wrapping_mul(5).rotate_left(7).wrapping_mul(9);
        let t = self.s[1] << 17;
        self.s[2] ^= self.s[0];
        self.s[3] ^= self.s[1];
        self.s[1] ^= self.s[2];
        self.s[0] ^= self.s[3];
        self.s[2] ^= t;
        self.s[3] = self.s[3].rotate_left(45);
        res
    }

    #[inline(always)]
    fn next_f64(&mut self) -> f64 {
        let u = (self.next_u64() >> 11) as f64 * (1.0 / ((1u64 << 53) as f64));
        if u <= 1e-15 { 1e-15 } else { u }
    }
}

struct ZipfSampler {
    cdf: Vec<f64>,
}

impl ZipfSampler {
    fn new(n: usize, s: f64) -> Self {
        let mut cdf = Vec::with_capacity(n);
        let mut sum = 0.0f64;
        for k in 1..=n {
            sum += 1.0 / (k as f64).powf(s);
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
        let mut lo = 0usize;
        let mut hi = self.cdf.len() - 1;
        while lo < hi {
            let mid = (lo + hi) >> 1;
            if self.cdf[mid] < u {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        lo
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
    fn bin_for_ns(ns: u64) -> usize {
        if ns < 20_000 {
            (ns / 10) as usize
        } else if ns < 200_000 {
            B0 + ((ns - 20_000) / 100) as usize
        } else if ns < 20_000_000 {
            B0 + B1 + ((ns - 200_000) / 10_000) as usize
        } else if ns < 1_000_000_000 {
            B0 + B1 + B2 + ((ns - 20_000_000) / 1_000_000) as usize
        } else {
            TOTAL_BINS - 1
        }
    }

    fn ns_for_bin(b: usize) -> f64 {
        if b < B0 {
            (b as f64) * 10.0 + 5.0
        } else if b < B0 + B1 {
            20_000.0 + ((b - B0) as f64) * 100.0 + 50.0
        } else if b < B0 + B1 + B2 {
            200_000.0 + ((b - B0 - B1) as f64) * 10_000.0 + 5_000.0
        } else if b < B0 + B1 + B2 + B3 {
            20_000_000.0 + ((b - B0 - B1 - B2) as f64) * 1_000_000.0 + 500_000.0
        } else {
            1_000_000_000.0
        }
    }

    #[inline(always)]
    fn record_ns(&mut self, ns: u64) {
        let b = Self::bin_for_ns(ns);
        unsafe { *self.bins.get_unchecked_mut(b) += 1 };
        self.count += 1;
        self.sum_ns += ns as u128;
        if ns > self.max_ns {
            self.max_ns = ns;
        }
    }

    fn merge_from(&mut self, o: &FastHist) {
        if o.count == 0 {
            return;
        }
        for i in 0..TOTAL_BINS {
            self.bins[i] += o.bins[i];
        }
        self.count += o.count;
        self.sum_ns += o.sum_ns;
        if o.max_ns > self.max_ns {
            self.max_ns = o.max_ns;
        }
    }

    fn pct_us(&self, q: f64) -> f64 {
        if self.count == 0 {
            return 0.0;
        }
        let target = ((self.count as f64) * q).ceil().max(1.0) as u64;
        let mut cum = 0u64;
        for b in 0..TOTAL_BINS {
            cum += self.bins[b];
            if cum >= target {
                return Self::ns_for_bin(b) / 1000.0;
            }
        }
        (self.max_ns as f64) / 1000.0
    }

    fn mean_us(&self) -> f64 {
        if self.count == 0 {
            0.0
        } else {
            (self.sum_ns as f64 / self.count as f64) / 1000.0
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum DriveMode {
    Closed,
    Poisson,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Workload {
    Get8,
    Set8,
    Rw8,
    Get128,
    Set128,
    Rw128,
    Get1024,
    Set1024,
    Get3840,
    Set3840,
    SmallMix,
    EtcMix,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum KeyDist {
    Zipf,
    Uniform,
}

#[derive(Clone)]
struct Config {
    dev: String,
    sgid: i32,
    cpu: usize,
    threads: usize,
    card_queues: usize,
    num_keys: usize,
    mode: DriveMode,
    workload: Workload,
    key_dist: KeyDist,
    zipf_s: f64,
    set_pct: u64,
    window: usize,
    rate_krps: f64,
    warmup_ms: u64,
    duration_ms: u64,
    max_tx_batch: usize,
    sync_reset: bool,
    seed: u64,
}

#[derive(Clone, Copy, Default)]
struct Inflight {
    active: bool,
    measured: bool,
    rpc_seq: u64,
    req_id: u16,
    opaque: u32,
    mc_op: u8,
    key: u64,
    val_len: usize,
    exp_udp_tot: u16,
    exp_resp_mc_len: usize,
    exp_resp_frags: u8,
    rx_mask: u16,
    t_sched: u64,
    t_send: u64,
}

fn parse_args() -> Config {
    let args: Vec<String> = std::env::args().collect();
    let mut cfg = Config {
        dev: "mlx5_2".to_string(),
        sgid: 3,
        cpu: 0,
        threads: 8,
        card_queues: 4,
        num_keys: 8192,
        mode: DriveMode::Closed,
        workload: Workload::EtcMix,
        key_dist: KeyDist::Zipf,
        zipf_s: 0.99,
        set_pct: 5,
        window: 256,
        rate_krps: 0.0,
        warmup_ms: 400,
        duration_ms: 3000,
        max_tx_batch: 16,
        sync_reset: false,
        seed: 20601,
    };
    let next = |i: usize| -> &str {
        args.get(i + 1).map(String::as_str).expect("missing arg value")
    };
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--dev" => { cfg.dev = next(i).to_string(); i += 2; }
            "--sgid" => { cfg.sgid = next(i).parse().unwrap(); i += 2; }
            "--cpu" => { cfg.cpu = next(i).parse().unwrap(); i += 2; }
            "--threads" => { cfg.threads = next(i).parse().unwrap(); i += 2; }
            "--card-queues" => { cfg.card_queues = next(i).parse().unwrap(); i += 2; }
            "--num-keys" => { cfg.num_keys = next(i).parse().unwrap(); i += 2; }
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
                    "get8" | "8" => Workload::Get8,
                    "set8" => Workload::Set8,
                    "rw8" => Workload::Rw8,
                    "get128" | "128" => Workload::Get128,
                    "set128" => Workload::Set128,
                    "rw128" => Workload::Rw128,
                    "get1024" | "1024" => Workload::Get1024,
                    "set1024" => Workload::Set1024,
                    "get3840" | "3840" => Workload::Get3840,
                    "set3840" => Workload::Set3840,
                    "small_mix" | "etc_small" => Workload::SmallMix,
                    "etc_mix" | "mix" | "etc" => Workload::EtcMix,
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
            "--set-pct" => { cfg.set_pct = next(i).parse().unwrap(); i += 2; }
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
        let meta = pack_wire_meta(pseq, 48, 0, 1, OP_PROBE_WIRE);
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
                        let (rseq, _, _, _, op) = unpack_wire_meta(w0);
                        if op == OP_PROBE_WIRE && rseq == pseq {
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
    set_measured: u64,
    req_bytes_measured: u64,
    resp_bytes_measured: u64,
    wire_tx_bytes_measured: u64,
    wire_rx_bytes_measured: u64,
    bad: u64,
    proto_err: u64,
    val_err: u64,
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
    hist_set: FastHist,
}

fn main() {
    let cfg = parse_args();
    pin_cpu(cfg.cpu);
    let tsc_hz = calibrate_tsc_hz(cfg.cpu);
    let ticks_to_ns = 1e9 / tsc_hz;
    let cpu_khz_start = read_cpu_khz(cfg.cpu);

    let nk = cfg.num_keys;
    let p40 = (nk * 4) / 10;
    let p80 = (nk * 8) / 10;
    let p50 = nk / 2;

    let zipf_all = Arc::new(ZipfSampler::new(nk, cfg.zipf_s));
    let zipf_p40 = Arc::new(ZipfSampler::new(p40.max(1), cfg.zipf_s));
    let zipf_p40_b = Arc::new(ZipfSampler::new((p80 - p40).max(1), cfg.zipf_s));
    let zipf_p20 = Arc::new(ZipfSampler::new((nk - p80).max(1), cfg.zipf_s));
    let zipf_p50_a = Arc::new(ZipfSampler::new(p50.max(1), cfg.zipf_s));
    let zipf_p50_b = Arc::new(ZipfSampler::new((nk - p50).max(1), cfg.zipf_s));

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
        let zipf_p40 = Arc::clone(&zipf_p40);
        let zipf_p40_b = Arc::clone(&zipf_p40_b);
        let zipf_p20 = Arc::clone(&zipf_p20);
        let zipf_p50_a = Arc::clone(&zipf_p50_a);
        let zipf_p50_b = Arc::clone(&zipf_p50_b);
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
            let mut tx_mc_tmp = [0u8; MAX_RPC_BYTES];
            let mut inflight_cnt: usize = 0;

            let mut hist_tot = FastHist::new();
            let mut hist_wire = FastHist::new();
            let mut hist_small = FastHist::new();
            let mut hist_large = FastHist::new();
            let mut hist_get = FastHist::new();
            let mut hist_set = FastHist::new();

            let mut local_seq: u64 = 0;
            let mut set_ver_ctr: u64 = 2 + (tid as u64) * 1_000_000;
            let mut tx_rpcs_total: u64 = 0;
            let mut rx_rpcs_total: u64 = 0;
            let mut tx_pkts_total: u64 = 0;
            let mut rx_pkts_total: u64 = 0;
            let mut sent_measured: u64 = 0;
            let mut done_measured: u64 = 0;
            let mut get_measured: u64 = 0;
            let mut set_measured: u64 = 0;
            let mut req_bytes_measured: u64 = 0;
            let mut resp_bytes_measured: u64 = 0;
            let mut wire_tx_bytes_measured: u64 = 0;
            let mut wire_rx_bytes_measured: u64 = 0;
            let mut bad: u64 = 0;
            let mut proto_err: u64 = 0;
            let mut val_err: u64 = 0;
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

            // Sample next genuine Memcached op `(mc_op, key, val_len)`
            let sample_op = |rng: &mut Rng64| -> (u8, u64, usize) {
                let pick = |rng: &mut Rng64, start: usize, len: usize, zipf: &ZipfSampler| -> u64 {
                    let off = match cfg.key_dist {
                        KeyDist::Zipf => zipf.sample(rng.next_f64()),
                        KeyDist::Uniform => (rng.next_u64() as usize) % len,
                    };
                    (start + off) as u64
                };
                match cfg.workload {
                    Workload::Get8 => (MC_CMD_GET, pick(rng, 0, nk, &zipf_all), 8),
                    Workload::Set8 => (MC_CMD_SET, pick(rng, 0, nk, &zipf_all), 8),
                    Workload::Rw8 => {
                        let k = pick(rng, 0, nk, &zipf_all);
                        let op = if (rng.next_u64() % 100) < cfg.set_pct { MC_CMD_SET } else { MC_CMD_GET };
                        (op, k, 8)
                    }
                    Workload::Get128 => (MC_CMD_GET, pick(rng, 0, nk, &zipf_all), 128),
                    Workload::Set128 => (MC_CMD_SET, pick(rng, 0, nk, &zipf_all), 128),
                    Workload::Rw128 => {
                        let k = pick(rng, 0, nk, &zipf_all);
                        let op = if (rng.next_u64() % 100) < cfg.set_pct { MC_CMD_SET } else { MC_CMD_GET };
                        (op, k, 128)
                    }
                    Workload::Get1024 => (MC_CMD_GET, pick(rng, 0, nk, &zipf_all), 1024),
                    Workload::Set1024 => (MC_CMD_SET, pick(rng, 0, nk, &zipf_all), 1024),
                    Workload::Get3840 => (MC_CMD_GET, pick(rng, 0, nk, &zipf_all), 3840),
                    Workload::Set3840 => (MC_CMD_SET, pick(rng, 0, nk, &zipf_all), 3840),
                    Workload::SmallMix => {
                        let r = rng.next_u64() % 100;
                        let op = if (rng.next_u64() % 100) < cfg.set_pct { MC_CMD_SET } else { MC_CMD_GET };
                        if r < 50 {
                            (op, pick(rng, 0, p50, &zipf_p50_a), 8)
                        } else {
                            (op, pick(rng, p50, nk - p50, &zipf_p50_b), 128)
                        }
                    }
                    Workload::EtcMix => {
                        // 40% 8 B + 40% 128 B (with cfg.set_pct SET, default 5% -> Facebook ETC 95/5) + 20% 3840 B GET
                        let r = rng.next_u64() % 100;
                        if r < 40 {
                            let op = if (rng.next_u64() % 100) < cfg.set_pct { MC_CMD_SET } else { MC_CMD_GET };
                            (op, pick(rng, 0, p40, &zipf_p40), 8)
                        } else if r < 80 {
                            let op = if (rng.next_u64() % 100) < cfg.set_pct { MC_CMD_SET } else { MC_CMD_GET };
                            (op, pick(rng, p40, p80 - p40, &zipf_p40_b), 128)
                        } else {
                            (MC_CMD_GET, pick(rng, p80, nk - p80, &zipf_p20), 3840)
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
                                 set_measured: &mut u64,
                                 resp_bytes_measured: &mut u64,
                                 wire_rx_bytes_measured: &mut u64,
                                 bad: &mut u64,
                                 proto_err: &mut u64,
                                 val_err: &mut u64,
                                 torn: &mut u64,
                                 hist_tot: &mut FastHist,
                                 hist_wire: &mut FastHist,
                                 hist_small: &mut FastHist,
                                 hist_large: &mut FastHist,
                                 hist_get: &mut FastHist,
                                 hist_set: &mut FastHist|
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
                    if c.status != 0 || (c.byte_len as usize) < GRH_BYTES + 32 {
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

                    let (sidx, mc_len, fcnt, mc_slice): (usize, usize, u8, &[u8]) = if p[7] == 0 {
                        // Raw zero-wrapper standard Memcached Binary UDP datagram (<= 1024 B)
                        if plen < 32 || p[8] != MC_MAGIC_RES {
                            *proto_err += 1;
                            *bad += 1;
                            continue;
                        }
                        let opaque = u32::from_be_bytes([p[20], p[21], p[22], p[23]]);
                        let rseq = rpc_seq_from_opaque(opaque);
                        let sidx = (rseq as usize) & INFLIGHT_MASK;
                        let slot = &inflight[sidx];
                        if !slot.active
                            || slot.rpc_seq != rseq
                            || slot.exp_resp_mc_len != plen
                            || slot.exp_resp_frags != 1
                        {
                            *bad += 1;
                            continue;
                        }
                        (sidx, plen, 1u8, p)
                    } else if p[7] == OP_FRAG {
                        // 1024 B RoCEv2 UD link-MTU segment carrying multi-MTU Memcached UDP payload (> 1024 B)
                        if plen != MAX_SEG_BYTES {
                            *bad += 1;
                            continue;
                        }
                        let w0 = u64::from_le_bytes(p[0..8].try_into().unwrap());
                        let w_end = u64::from_le_bytes(p[plen - 8..plen].try_into().unwrap());
                        if w0 != w_end {
                            *torn += 1;
                            continue;
                        }
                        let (rseq, mc_len_u16, fidx, fcnt, _op) = unpack_wire_meta(w0);
                        let mc_len = mc_len_u16 as usize;
                        let sidx = (rseq as usize) & INFLIGHT_MASK;
                        let slot = &mut inflight[sidx];
                        if !slot.active
                            || slot.rpc_seq != rseq
                            || slot.exp_resp_mc_len != mc_len
                            || slot.exp_resp_frags != fcnt
                            || fidx >= fcnt
                        {
                            *bad += 1;
                            continue;
                        }
                        let start = (fidx as usize) * MAX_FRAG_PAYLOAD;
                        let take = MAX_FRAG_PAYLOAD.min(mc_len.saturating_sub(start));
                        let dst_off = sidx * MAX_RPC_BYTES + start;
                        reasm_buf[dst_off..dst_off + take].copy_from_slice(&p[8..8 + take]);
                        slot.rx_mask |= 1u16 << fidx;
                        let full = (1u16 << fcnt) - 1;
                        if slot.rx_mask != full {
                            continue;
                        }
                        (sidx, mc_len, fcnt, &reasm_buf[sidx * MAX_RPC_BYTES..sidx * MAX_RPC_BYTES + mc_len])
                    } else {
                        *bad += 1;
                        continue;
                    };

                    let slot = &mut inflight[sidx];

                    // Strict standard Memcached Binary UDP verification across ALL `exp_udp_tot` datagrams
                    // (1 datagram for <= 1392 B; 3 datagrams seq=0..2 for 3840 B GET per upstream transmit_udp()).
                    let mut bin_reasm = [0u8; MAX_RPC_BYTES];
                    let (udp_ok, bin_slice): (bool, &[u8]) = if slot.exp_udp_tot == 1 {
                        let udp_req_id = u16::from_be_bytes([mc_slice[0], mc_slice[1]]);
                        let udp_seq = u16::from_be_bytes([mc_slice[2], mc_slice[3]]);
                        let udp_tot = u16::from_be_bytes([mc_slice[4], mc_slice[5]]);
                        let udp_resv = u16::from_be_bytes([mc_slice[6], mc_slice[7]]);
                        (
                            udp_req_id == slot.req_id && udp_seq == 0 && udp_tot == 1 && udp_resv == 0,
                            &mc_slice[MC_UDP_HDR_LEN..],
                        )
                    } else {
                        let mut ok = true;
                        let mut off = 0usize;
                        let mut b_off = 0usize;
                        for d in 0..(slot.exp_udp_tot as usize) {
                            if off + MC_UDP_HDR_LEN > mc_slice.len() {
                                ok = false;
                                break;
                            }
                            let dlen = MC_UDP_MAX_PAYLOAD.min(mc_slice.len() - off);
                            let dg = &mc_slice[off..off + dlen];
                            let udp_req_id = u16::from_be_bytes([dg[0], dg[1]]);
                            let udp_seq = u16::from_be_bytes([dg[2], dg[3]]);
                            let udp_tot = u16::from_be_bytes([dg[4], dg[5]]);
                            let udp_resv = u16::from_be_bytes([dg[6], dg[7]]);
                            if udp_req_id != slot.req_id
                                || udp_seq != (d as u16)
                                || udp_tot != slot.exp_udp_tot
                                || udp_resv != 0
                            {
                                ok = false;
                                break;
                            }
                            let pay = &dg[MC_UDP_HDR_LEN..];
                            bin_reasm[b_off..b_off + pay.len()].copy_from_slice(pay);
                            b_off += pay.len();
                            off += dlen;
                        }
                        if off != mc_slice.len() {
                            ok = false;
                        }
                        (ok, &bin_reasm[..b_off])
                    };

                    let exp_ext = if slot.mc_op == MC_CMD_GET { 4u8 } else { 0u8 };
                    let exp_body = if slot.mc_op == MC_CMD_GET { 4 + slot.val_len } else { 0 };

                    let proto_ok = udp_ok
                        && bin_slice.len() == MC_BIN_HDR_LEN + exp_body
                        && bin_slice[0] == MC_MAGIC_RES
                        && bin_slice[1] == slot.mc_op
                        && u16::from_be_bytes([bin_slice[2], bin_slice[3]]) == 0
                        && bin_slice[4] == exp_ext
                        && u16::from_be_bytes([bin_slice[6], bin_slice[7]]) == MC_STATUS_OK
                        && (u32::from_be_bytes([bin_slice[8], bin_slice[9], bin_slice[10], bin_slice[11]]) as usize) == exp_body
                        && u32::from_be_bytes([bin_slice[12], bin_slice[13], bin_slice[14], bin_slice[15]]) == slot.opaque
                        && u64::from_be_bytes(bin_slice[16..24].try_into().unwrap()) != 0;

                    if !proto_ok {
                        *proto_err += 1;
                        *bad += 1;
                    } else if slot.mc_op == MC_CMD_GET {
                        let flags = u32::from_be_bytes([bin_slice[24], bin_slice[25], bin_slice[26], bin_slice[27]]);
                        if flags != MC_ITEM_FLAGS || !verify_mc_value(slot.key, &bin_slice[28..28 + slot.val_len]) {
                            *val_err += 1;
                            *bad += 1;
                        }
                    }

                    slot.active = false;
                    *inflight_cnt -= 1;
                    *rx_rpcs_total += 1;

                    if slot.measured {
                        let tot_ns = ((t_now.saturating_sub(slot.t_sched)) as f64 * ticks_to_ns) as u64;
                        let wire_ns = ((t_now.saturating_sub(slot.t_send)) as f64 * ticks_to_ns) as u64;
                        *done_measured += 1;
                        *resp_bytes_measured += mc_len as u64;
                        let wire_b = if fcnt == 1 {
                            (mc_len + ETH_WIRE_OVERHEAD).max(84) as u64
                        } else {
                            (fcnt as u64) * ((MAX_SEG_BYTES + ETH_WIRE_OVERHEAD) as u64)
                        };
                        *wire_rx_bytes_measured += wire_b;
                        hist_tot.record_ns(tot_ns);
                        hist_wire.record_ns(wire_ns);
                        if mc_len <= OP_PUSH_MAX_MC_BYTES {
                            hist_small.record_ns(tot_ns);
                        } else {
                            hist_large.record_ns(tot_ns);
                        }
                        if slot.mc_op == MC_CMD_GET {
                            *get_measured += 1;
                            hist_get.record_ns(tot_ns);
                        } else {
                            *set_measured += 1;
                            hist_set.record_ns(tot_ns);
                        }
                    }
                }
                unsafe { ud_repost_rx_batch(u, repost_slots.as_ptr(), n_repost as i32) };
                n as usize
            };

            let mut t_meas_start_tsc = 0u64;
            let mut t_meas_end_tsc = 0u64;

            for phase in 0..2 {
                let measured = phase == 1;
                let phase_ms = if measured { cfg.duration_ms } else { cfg.warmup_ms };
                if phase == 1 {
                    if inflight_cnt > 0 {
                        eprintln!("mrv206_client tid={tid} warmup ended with inflight={inflight_cnt} tx={tx_rpcs_total} rx={rx_rpcs_total} bad={bad} proto_err={proto_err} val_err={val_err}");
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
                        &mut set_measured,
                        &mut resp_bytes_measured,
                        &mut wire_rx_bytes_measured,
                        &mut bad,
                        &mut proto_err,
                        &mut val_err,
                        &mut torn,
                        &mut hist_tot,
                        &mut hist_wire,
                        &mut hist_small,
                        &mut hist_large,
                        &mut hist_get,
                        &mut hist_set,
                    );
                    if got > 0 {
                        last_rx = Instant::now();
                    } else if inflight_cnt > 0 && (iter & 0x3fff) == 0 && last_rx.elapsed() > Duration::from_secs(3) {
                        eprintln!("mrv206_client tid={tid} stalled >3s phase={phase} inflight={inflight_cnt}");
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

                        local_seq = (local_seq + 1) & LOCAL_SEQ_MASK;
                        if local_seq == 0 {
                            local_seq = 1;
                        }
                        let rpc_seq = ((tid as u64) << 34) | local_seq;
                        let tx_slot = (rpc_seq as usize) & INFLIGHT_MASK;
                        if inflight[tx_slot].active {
                            break;
                        }

                        let opaque = opaque_from_rpc_seq(rpc_seq);
                        let req_id = (local_seq & 0xffff) as u16;
                        let (mc_op, key, val_len) = sample_op(&mut rng);
                        let (req_mc_len, exp_bin_len) = if mc_op == MC_CMD_GET {
                            let n = build_mc_get_udp(req_id, opaque, key, &mut tx_mc_tmp);
                            (n, MC_BIN_HDR_LEN + 4 + val_len)
                        } else {
                            set_ver_ctr += 1;
                            let ver = set_ver_ctr;
                            let n = build_mc_set_udp(req_id, opaque, key, ver, val_len, &mut tx_mc_tmp);
                            (n, MC_BIN_HDR_LEN)
                        };
                        let exp_udp_tot = mc_udp_dgram_cnt(exp_bin_len);
                        let exp_resp_mc_len = (exp_udp_tot as usize) * MC_UDP_HDR_LEN + exp_bin_len;

                        let req_frags = roce_frag_cnt(req_mc_len);
                        let seg_len = if req_frags > 1 { MAX_SEG_BYTES } else { req_mc_len };
                        let exp_resp_frags = roce_frag_cnt(exp_resp_mc_len);

                        let t_send = if n_batch == 0 { now_tsc } else { rdtsc_after_loads() };
                        let tx_ptr = unsafe { tx_base.add(tx_slot * (TX_STRIDE as usize)) };
                        if req_frags == 1 {
                            // Raw zero-wrapper standard Memcached Binary UDP datagram (<= 1024 B)
                            let buf = unsafe { std::slice::from_raw_parts_mut(tx_ptr, req_mc_len) };
                            buf.copy_from_slice(&tx_mc_tmp[..req_mc_len]);
                        } else {
                            for f in 0..(req_frags as usize) {
                                let seg = unsafe {
                                    std::slice::from_raw_parts_mut(tx_ptr.add(f * MAX_SEG_BYTES), MAX_SEG_BYTES)
                                };
                                let wf = pack_wire_meta(rpc_seq, req_mc_len as u16, f as u8, req_frags, OP_FRAG);
                                let start = f * MAX_FRAG_PAYLOAD;
                                let take = MAX_FRAG_PAYLOAD.min(req_mc_len.saturating_sub(start));
                                seg[0..8].copy_from_slice(&wf.to_le_bytes());
                                seg[8..8 + take].copy_from_slice(&tx_mc_tmp[start..start + take]);
                                if take < MAX_FRAG_PAYLOAD {
                                    seg[8 + take..MAX_SEG_BYTES - 8].fill(0);
                                }
                                seg[MAX_SEG_BYTES - 8..MAX_SEG_BYTES].copy_from_slice(&wf.to_le_bytes());
                            }
                        }

                        batch_slots[n_batch] = tx_slot as u32;
                        batch_lens[n_batch] = seg_len as u32;
                        batch_frags[n_batch] = req_frags as u32;
                        n_batch += 1;
                        wr_batch += req_frags as usize;

                        inflight[tx_slot] = Inflight {
                            active: true,
                            measured,
                            rpc_seq,
                            req_id,
                            opaque,
                            mc_op,
                            key,
                            val_len,
                            exp_udp_tot,
                            exp_resp_mc_len,
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
                            req_bytes_measured += req_mc_len as u64;
                            wire_tx_bytes_measured += if req_frags == 1 {
                                (req_mc_len + ETH_WIRE_OVERHEAD).max(84) as u64
                            } else {
                                (req_frags as u64) * ((MAX_SEG_BYTES + ETH_WIRE_OVERHEAD) as u64)
                            };
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
                set_measured,
                req_bytes_measured,
                resp_bytes_measured,
                wire_tx_bytes_measured,
                wire_rx_bytes_measured,
                bad,
                proto_err,
                val_err,
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
                hist_set,
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
    let mut tot_set_m = 0u64;
    let mut tot_req_b = 0u64;
    let mut tot_resp_b = 0u64;
    let mut tot_wire_tx_b = 0u64;
    let mut tot_wire_rx_b = 0u64;
    let mut tot_bad = 0u64;
    let mut tot_proto_err = 0u64;
    let mut tot_val_err = 0u64;
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
    let mut hist_set = FastHist::new();

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
        tot_set_m += r.set_measured;
        tot_req_b += r.req_bytes_measured;
        tot_resp_b += r.resp_bytes_measured;
        tot_wire_tx_b += r.wire_tx_bytes_measured;
        tot_wire_rx_b += r.wire_rx_bytes_measured;
        tot_bad += r.bad;
        tot_proto_err += r.proto_err;
        tot_val_err += r.val_err;
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
        hist_set.merge_from(&r.hist_set);
    }

    let dt_s = dt_sum / (cfg.threads as f64);
    let achieved_mrps = if dt_s > 0.0 { tot_done_m as f64 / dt_s / 1e6 } else { 0.0 };
    let payload_tx_gbps = if dt_s > 0.0 { tot_req_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let payload_rx_gbps = if dt_s > 0.0 { tot_resp_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let wire_tx_gbps = if dt_s > 0.0 { tot_wire_tx_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let wire_rx_gbps = if dt_s > 0.0 { tot_wire_rx_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let verified = tot_lost == 0
        && tot_bad == 0
        && tot_proto_err == 0
        && tot_val_err == 0
        && tot_torn == 0
        && tot_send_err == 0
        && tot_tx == tot_rx;
    let cpu_khz_end = read_cpu_khz(cfg.cpu);

    println!(
        "MRV206_CLIENT_JSON {{\"verified\":{},\"threads\":{},\"rxqs\":[{}],\"tsc_ghz\":{:.6},\"cpu_khz_start\":{},\"cpu_khz_end\":{},\"dt_s\":{:.6},\"tx_rpcs_total\":{},\"rx_rpcs_total\":{},\"tx_pkts_total\":{},\"rx_pkts_total\":{},\"sent_measured\":{},\"done_measured\":{},\"get_measured\":{},\"set_measured\":{},\"achieved_mrps\":{:.6},\"payload_tx_gbps\":{:.4},\"payload_rx_gbps\":{:.4},\"wire_tx_gbps\":{:.4},\"wire_rx_gbps\":{:.4},\"tot_p50_us\":{:.3},\"tot_p95_us\":{:.3},\"tot_p99_us\":{:.3},\"tot_p999_us\":{:.3},\"tot_mean_us\":{:.3},\"wire_p50_us\":{:.3},\"wire_p99_us\":{:.3},\"small_cnt\":{},\"small_p50_us\":{:.3},\"small_p99_us\":{:.3},\"large_cnt\":{},\"large_p50_us\":{:.3},\"large_p99_us\":{:.3},\"get_p50_us\":{:.3},\"get_p99_us\":{:.3},\"set_p50_us\":{:.3},\"set_p99_us\":{:.3},\"max_inflight\":{},\"window_cap_hits\":{},\"lost\":{},\"bad\":{},\"proto_err\":{},\"val_err\":{},\"torn\":{},\"send_err\":{}}}",
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
        tot_set_m,
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
        hist_set.pct_us(0.50),
        hist_set.pct_us(0.99),
        max_inf,
        tot_wcap,
        tot_lost,
        tot_bad,
        tot_proto_err,
        tot_val_err,
        tot_torn,
        tot_send_err
    );

    if !verified {
        std::process::exit(2);
    }
}
