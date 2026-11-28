//! Multi-QP 100GbE non-root hardware-bypass (IBV_QPT_UD RoCEv2) traffic generator for
//! MRV-209 (MRV-203 E5) running on `mercury` (`mlx5_2`).
//!
//! Zero external crates; compiles in < 1 s directly on `mercury` with:
//!   gcc -O3 -c mrv209_verbs_shim.c -o mrv209_verbs_shim.o
//!   rustc --edition 2021 -O mrv209_client.rs -C link-arg=mrv209_verbs_shim.o -C link-arg=-libverbs -o mrv209_client
//! Workloads:
//!   - Real Upstream Memcached 1.6.45 Binary UDP:
//!       `get8`, `set8`, `rw8`, `get128`, `set128`, `rw128`, `get1024`, `get3840`,
//!       `small_mix`, `etc_mix` (Facebook ETC), `websearch_mc` (WebSearch heavy-tailed)
//!   - Lightweight Stateful NFV (`ShRing/rxBisect` cross-comparison):
//!       `nfv_nat_64`, `nfv_nat_256`, `nfv_nat_1024`, `nfv_lb_256`, `nfv_websearch`
//!
//! Arrival distributions (zero coordinated omission: latency measured from `t_sched`):
//!   - `closed`        : closed-loop saturation (`--window`)
//!   - `poisson`       : pure open-loop Poisson (`--rate-krps`)
//!   - `poisson_burst` : open-loop Poisson background + periodic 100GbE microbursts
//!                       (`--rate-krps`, `--burst-period-ms`, `--burst-dur-us`, `--burst-rate-krps`)
//!   - `incast`        : open-loop Poisson background + synchronized many-to-one Incast spikes
//!                       (`--rate-krps`, `--burst-period-ms`, `--incast-pkts`, `--num-flows`)

use std::ffi::CString;
use std::io::Write;
use std::sync::{Arc, Barrier};
use std::time::{Duration, Instant};

const ROCE_QKEY: u32 = 0x1111_1111;
const ROCE_CARD_QPN: u32 = 0x0001_2345;
const OP_FRAG: u8 = 0xfa;
const OP_NFV_WIRE: u8 = 0xfc;
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
const MC_ITEM_FLAGS: u32 = 0x2090_0001;

const NFV_HDR_LEN: usize = 48;
const NFV_MIN_LEN: usize = 64;
const NFV_SUBOP_NAT: u8 = 0;
const NFV_SUBOP_LB: u8 = 1;
const NFV_NAT_PUBLIC_IP: u32 = 0xCB00_7101;
const NFV_LB_VIP: u32 = 0x0A0A_FE01;
const NFV_NUM_BACKENDS: usize = 64;

const WIRE_SEQ_MASK: u64 = (1u64 << 38) - 1;
const LOCAL_SEQ_MASK: u64 = (1u64 << 29) - 1;
const MAX_FRAG_PAYLOAD: usize = 1008;
const MAX_SEG_BYTES: usize = 1024;
const MAX_RPC_BYTES: usize = 4096;
const GRH_BYTES: usize = 40;
const RX_SLOTS: u32 = 4096;
const RX_STRIDE: u32 = 2048;
const TX_SLOTS: u32 = 4096;
const TX_STRIDE: u32 = 4096;
const INFLIGHT_CAP: usize = 4096;
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

extern "C" {
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
}

#[inline(always)]
fn rdtsc_after_loads() -> u64 {
    let mut aux = 0u32;
    unsafe { std::arch::x86_64::__rdtscp(&mut aux) }
}

fn read_cpu_khz(cpu: usize) -> u64 {
    let p = format!("/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq");
    std::fs::read_to_string(p)
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok())
        .unwrap_or(0)
}

fn calibrate_tsc_hz(cpu: usize) -> f64 {
    let _ = cpu;
    let t0 = Instant::now();
    let c0 = rdtsc_after_loads();
    std::thread::sleep(Duration::from_millis(80));
    let c1 = rdtsc_after_loads();
    let dt = t0.elapsed().as_secs_f64();
    (c1.saturating_sub(c0) as f64) / dt
}

fn pin_cpu(cpu: usize) {
    unsafe {
        let mut mask = [0u64; 16];
        let word = cpu / 64;
        let bit = cpu % 64;
        if word < mask.len() {
            mask[word] |= 1u64 << bit;
        }
        extern "C" {
            fn sched_setaffinity(pid: i32, cpusetsize: usize, mask: *const u64) -> i32;
        }
        let _ = sched_setaffinity(0, std::mem::size_of_val(&mask), mask.as_ptr());
    }
}

#[inline(always)]
const fn pack_wire_meta(rpc_seq: u64, payload_len: u16, frag_idx: u8, frag_cnt: u8, op: u8) -> u64 {
    (rpc_seq & WIRE_SEQ_MASK)
        | (((payload_len as u64) & 0xfff) << 38)
        | (((frag_idx as u64) & 0x7) << 50)
        | (((frag_cnt as u64) & 0x7) << 53)
        | ((op as u64) << 56)
}

#[inline(always)]
const fn unpack_wire_meta(meta: u64) -> (u64, u16, u8, u8, u8) {
    let rpc_seq = meta & WIRE_SEQ_MASK;
    let payload_len = ((meta >> 38) & 0xfff) as u16;
    let frag_idx = ((meta >> 50) & 0x7) as u8;
    let frag_cnt = ((meta >> 53) & 0x7) as u8;
    let op = (meta >> 56) as u8;
    (rpc_seq, payload_len, frag_idx, frag_cnt, op)
}

#[inline(always)]
const fn opaque_from_rpc_seq(rpc_seq: u64) -> u32 {
    let tid = ((rpc_seq >> 34) & 7) as u32;
    let lseq = (rpc_seq & 0x1fff_ffff) as u32;
    (tid << 29) | lseq
}

#[inline(always)]
const fn rpc_seq_from_opaque(opaque: u32) -> u64 {
    let tid = ((opaque >> 29) & 7) as u64;
    let lseq = (opaque & 0x1fff_ffff) as u64;
    (tid << 34) | lseq
}

#[inline(always)]
const fn roce_frag_cnt(payload_len: usize) -> u8 {
    if payload_len <= MAX_SEG_BYTES {
        1
    } else {
        payload_len.div_ceil(MAX_FRAG_PAYLOAD) as u8
    }
}

#[inline(always)]
const fn mc_udp_dgram_cnt(bin_len: usize) -> u16 {
    if bin_len == 0 {
        1
    } else {
        bin_len.div_ceil(MC_UDP_DATA_SIZE) as u16
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
    if n < 8 || n % 8 != 0 {
        return false;
    }
    if n == 8 {
        let k32 = u32::from_le_bytes(val[0..4].try_into().unwrap());
        let v16 = u16::from_le_bytes(val[4..6].try_into().unwrap());
        let tag = u16::from_le_bytes(val[6..8].try_into().unwrap());
        return k32 == (expected_key as u32) && v16 >= 1 && tag == mc_val8_tag(k32, v16);
    }
    let n_words = n / 8;
    let key = u64::from_le_bytes(val[0..8].try_into().unwrap());
    let ver = u64::from_le_bytes(val[8..16].try_into().unwrap());
    if key != expected_key || ver == 0 {
        return false;
    }
    let mut acc = splitmix64(key ^ ver.rotate_left(17) ^ (n as u64).rotate_left(33));
    for wi in 2..(n_words - 1) {
        let w = u64::from_le_bytes(val[wi * 8..(wi + 1) * 8].try_into().unwrap());
        if w != mc_val_word(key, ver, wi) {
            return false;
        }
        acc = acc.rotate_left(7).wrapping_add(w ^ 0x517c_c1b7_2722_0a95);
    }
    if n_words >= 3 {
        let last = (n_words - 1) * 8;
        let got = u64::from_le_bytes(val[last..last + 8].try_into().unwrap());
        if got != acc {
            return false;
        }
    }
    true
}

fn build_mc_get_udp(req_id: u16, opaque: u32, key: u64, out: &mut [u8]) -> usize {
    let total = MC_UDP_HDR_LEN + MC_BIN_HDR_LEN + MC_KEY_LEN;
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

// -----------------------------------------------------------------------------
// Stateful NFV Packet Helpers (byte-for-byte identical to testbed::mrv209)
// -----------------------------------------------------------------------------

#[inline(always)]
const fn nfv_five_tuple(flow_id: u16, is_lb: bool) -> (u32, u32, u16, u16, u8) {
    let f = flow_id as u32;
    let src_ip = 0x0A00_0001u32 + (f & 0xffff);
    let dst_ip = if is_lb {
        NFV_LB_VIP
    } else {
        0xC633_6401u32 + (f & 0xff)
    };
    let sport = 10000u16.wrapping_add(flow_id);
    let dport = 80u16 + (flow_id & 0x3f);
    (src_ip, dst_ip, sport, dport, 17)
}

#[inline(always)]
const fn nfv_flow_hash(src_ip: u32, dst_ip: u32, sport: u16, dport: u16, proto: u8) -> u64 {
    let a = ((src_ip as u64) << 32) | (dst_ip as u64);
    let b = ((sport as u64) << 24) | ((dport as u64) << 8) | (proto as u64);
    splitmix64(a ^ b.rotate_left(19))
}

#[inline(always)]
const fn expected_nfv_ip(is_lb: bool, src_ip: u32, dst_ip: u32, sport: u16, dport: u16, proto: u8) -> u32 {
    if is_lb {
        let h = nfv_flow_hash(src_ip, dst_ip, sport, dport, proto);
        0x0A14_0001u32 + ((h as usize % NFV_NUM_BACKENDS) as u32)
    } else {
        NFV_NAT_PUBLIC_IP
    }
}

#[inline(always)]
const fn expected_nfv_port(is_lb: bool, flow_id: u16, dport: u16) -> u16 {
    if is_lb {
        dport
    } else {
        32768u16.wrapping_add(flow_id)
    }
}

#[inline(always)]
const fn nfv_hdr_checksum(
    src_ip: u32,
    dst_ip: u32,
    mapped_ip: u32,
    sport: u16,
    dport: u16,
    mapped_port: u16,
    proto: u8,
) -> u16 {
    let mut s = (src_ip & 0xffff)
        + (src_ip >> 16)
        + (dst_ip & 0xffff)
        + (dst_ip >> 16)
        + (mapped_ip & 0xffff)
        + (mapped_ip >> 16)
        + (sport as u32)
        + (dport as u32)
        + (mapped_port as u32)
        + (proto as u32);
    while (s >> 16) != 0 {
        s = (s & 0xffff) + (s >> 16);
    }
    !(s as u16)
}

fn build_nfv_req(rpc_seq: u64, flow_id: u16, is_lb: bool, burst_flag: bool, pkt_len: usize, out: &mut [u8]) -> usize {
    let len = pkt_len.clamp(NFV_MIN_LEN, MAX_SEG_BYTES) & !7;
    let subop = if is_lb { NFV_SUBOP_LB } else { NFV_SUBOP_NAT };
    let w0 = pack_wire_meta(rpc_seq, len as u16, subop, 1, OP_NFV_WIRE);
    let (src_ip, dst_ip, sport, dport, proto) = nfv_five_tuple(flow_id, is_lb);
    out[0..8].copy_from_slice(&w0.to_le_bytes());
    out[8..12].copy_from_slice(&src_ip.to_le_bytes());
    out[12..16].copy_from_slice(&dst_ip.to_le_bytes());
    out[16..18].copy_from_slice(&sport.to_le_bytes());
    out[18..20].copy_from_slice(&dport.to_le_bytes());
    out[20] = proto;
    out[21] = if burst_flag { 1 } else { 0 };
    out[22..24].copy_from_slice(&flow_id.to_le_bytes());
    out[24..40].fill(0);
    let req_sig = splitmix64(rpc_seq ^ ((flow_id as u64) << 40) ^ (len as u64));
    out[40..48].copy_from_slice(&req_sig.to_le_bytes());
    if len > NFV_HDR_LEN + 8 {
        out[NFV_HDR_LEN..len - 8].fill((flow_id & 0xff) as u8);
    }
    out[len - 8..len].copy_from_slice(&w0.to_le_bytes());
    len
}

fn verify_nfv_resp(buf: &[u8], expected_rpc_seq: u64, flow_id: u16, is_lb: bool, expected_len: usize) -> bool {
    if buf.len() != expected_len || expected_len < NFV_MIN_LEN {
        return false;
    }
    let w0 = u64::from_le_bytes(buf[0..8].try_into().unwrap());
    let w_tail = u64::from_le_bytes(buf[expected_len - 8..expected_len].try_into().unwrap());
    if w0 != w_tail {
        return false;
    }
    let (rpc_seq, plen, _, _, op) = unpack_wire_meta(w0);
    if op != OP_NFV_WIRE || rpc_seq != expected_rpc_seq || (plen as usize) != expected_len {
        return false;
    }
    if (buf[21] & 0x80) == 0 {
        return false;
    }
    let (src_ip, dst_ip, sport, dport, proto) = nfv_five_tuple(flow_id, is_lb);
    let exp_ip = expected_nfv_ip(is_lb, src_ip, dst_ip, sport, dport, proto);
    let exp_port = expected_nfv_port(is_lb, flow_id, dport);
    let got_ip = u32::from_le_bytes(buf[24..28].try_into().unwrap());
    let got_port = u16::from_le_bytes(buf[28..30].try_into().unwrap());
    let got_csum = u16::from_le_bytes(buf[30..32].try_into().unwrap());
    let exp_csum = nfv_hdr_checksum(src_ip, dst_ip, exp_ip, sport, dport, exp_port, proto);
    let flow_pkts = u64::from_le_bytes(buf[32..40].try_into().unwrap());
    let req_sig = splitmix64(expected_rpc_seq ^ ((flow_id as u64) << 40) ^ (expected_len as u64));
    let exp_resp_sig = req_sig ^ splitmix64(((exp_ip as u64) << 16) | (exp_port as u64));
    let got_resp_sig = u64::from_le_bytes(buf[40..48].try_into().unwrap());
    got_ip == exp_ip && got_port == exp_port && got_csum == exp_csum && flow_pkts >= 1 && got_resp_sig == exp_resp_sig
}

// -----------------------------------------------------------------------------
// Fast PRNG, Zipf Sampler & Constant-Memory Histogram
// -----------------------------------------------------------------------------

struct Rng64 {
    s: [u64; 4],
}

impl Rng64 {
    fn new(seed: u64) -> Self {
        let mut x = seed;
        let mut s = [0u64; 4];
        for slot in &mut s {
            x = splitmix64(x);
            *slot = x;
        }
        Self { s }
    }

    #[inline(always)]
    fn next_u64(&mut self) -> u64 {
        let res = (self.s[1].wrapping_mul(5)).rotate_left(7).wrapping_mul(9);
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
        let v = (self.next_u64() >> 11) as f64 * (1.0 / ((1u64 << 53) as f64));
        if v <= 1e-15 { 1e-15 } else if v >= 1.0 - 1e-15 { 1.0 - 1e-15 } else { v }
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
        for v in &mut cdf {
            *v /= sum;
        }
        if let Some(last) = cdf.last_mut() {
            *last = 1.0;
        }
        Self { cdf }
    }

    #[inline(always)]
    fn sample(&self, u: f64) -> usize {
        match self.cdf.binary_search_by(|x| x.partial_cmp(&u).unwrap()) {
            Ok(i) => i,
            Err(i) => i.min(self.cdf.len() - 1),
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
        Self {
            bins: vec![0u64; TOTAL_BINS],
            count: 0,
            sum_ns: 0,
            max_ns: 0,
        }
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
        unsafe {
            *self.bins.get_unchecked_mut(b) += 1;
        }
        self.count += 1;
        self.sum_ns += ns as u128;
        if ns > self.max_ns {
            self.max_ns = ns;
        }
    }

    fn merge_from(&mut self, other: &FastHist) {
        if other.count == 0 {
            return;
        }
        for i in 0..TOTAL_BINS {
            self.bins[i] += other.bins[i];
        }
        self.count += other.count;
        self.sum_ns += other.sum_ns;
        if other.max_ns > self.max_ns {
            self.max_ns = other.max_ns;
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
        self.max_ns as f64 / 1000.0
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
    PoissonBurst,
    Incast,
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
    Get3840,
    SmallMix,
    EtcMix,
    WebSearchMc,
    NfvNat64,
    NfvNat256,
    NfvNat1024,
    NfvLb256,
    NfvWebSearch,
}

impl Workload {
    fn is_nfv(self) -> bool {
        matches!(
            self,
            Workload::NfvNat64
                | Workload::NfvNat256
                | Workload::NfvNat1024
                | Workload::NfvLb256
                | Workload::NfvWebSearch
        )
    }

    fn is_nfv_lb(self) -> bool {
        matches!(self, Workload::NfvLb256)
    }
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
    num_flows: usize,
    mode: DriveMode,
    workload: Workload,
    key_dist: KeyDist,
    zipf_s: f64,
    set_pct: u64,
    window: usize,
    rate_krps: f64,
    burst_period_ms: u64,
    burst_dur_us: u64,
    burst_rate_krps: f64,
    incast_pkts: usize,
    allow_drops: bool,
    drop_timeout_ms: u64,
    warmup_ms: u64,
    duration_ms: u64,
    max_tx_batch: usize,
    sync_reset: bool,
    self_test: bool,
    seed: u64,
}

#[derive(Clone, Copy, Default)]
struct Inflight {
    active: bool,
    measured: bool,
    is_burst: bool,
    is_nfv: bool,
    is_nfv_lb: bool,
    rpc_seq: u64,
    req_id: u16,
    flow_id: u16,
    opaque: u32,
    mc_op: u8,
    key: u64,
    val_len: usize,
    exp_udp_tot: u16,
    exp_resp_len: usize,
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
        cpu: 4,
        threads: 4,
        card_queues: 4,
        num_keys: 8192,
        num_flows: 256,
        mode: DriveMode::PoissonBurst,
        workload: Workload::EtcMix,
        key_dist: KeyDist::Zipf,
        zipf_s: 0.99,
        set_pct: 5,
        window: 2048,
        rate_krps: 800.0,
        burst_period_ms: 25,
        burst_dur_us: 1500,
        burst_rate_krps: 4500.0,
        incast_pkts: 512,
        allow_drops: false,
        drop_timeout_ms: 100,
        warmup_ms: 400,
        duration_ms: 3000,
        max_tx_batch: 16,
        sync_reset: false,
        self_test: false,
        seed: 20901,
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
            "--num-flows" => { cfg.num_flows = next(i).parse().unwrap(); i += 2; }
            "--mode" => {
                cfg.mode = match next(i) {
                    "closed" => DriveMode::Closed,
                    "poisson" => DriveMode::Poisson,
                    "poisson_burst" | "burst" => DriveMode::PoissonBurst,
                    "incast" => DriveMode::Incast,
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
                    "get3840" | "3840" => Workload::Get3840,
                    "small_mix" | "etc_small" => Workload::SmallMix,
                    "etc_mix" | "mix" | "etc" => Workload::EtcMix,
                    "websearch_mc" | "websearch" => Workload::WebSearchMc,
                    "nfv_nat_64" => Workload::NfvNat64,
                    "nfv_nat_256" => Workload::NfvNat256,
                    "nfv_nat_1024" => Workload::NfvNat1024,
                    "nfv_lb_256" => Workload::NfvLb256,
                    "nfv_websearch" => Workload::NfvWebSearch,
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
            "--burst-period-ms" => { cfg.burst_period_ms = next(i).parse().unwrap(); i += 2; }
            "--burst-dur-us" => { cfg.burst_dur_us = next(i).parse().unwrap(); i += 2; }
            "--burst-rate-krps" => { cfg.burst_rate_krps = next(i).parse().unwrap(); i += 2; }
            "--incast-pkts" => { cfg.incast_pkts = next(i).parse().unwrap(); i += 2; }
            "--allow-drops" => { cfg.allow_drops = true; i += 1; }
            "--drop-timeout-ms" => { cfg.drop_timeout_ms = next(i).parse().unwrap(); i += 2; }
            "--warmup-ms" => { cfg.warmup_ms = next(i).parse().unwrap(); i += 2; }
            "--duration-ms" => { cfg.duration_ms = next(i).parse().unwrap(); i += 2; }
            "--max-tx-batch" => { cfg.max_tx_batch = next(i).parse().unwrap(); i += 2; }
            "--sync-reset" => { cfg.sync_reset = true; i += 1; }
            "--self-test" => { cfg.self_test = true; i += 1; }
            "--seed" => { cfg.seed = next(i).parse().unwrap(); i += 2; }
            _ => { i += 1; }
        }
    }
    assert!(cfg.threads >= 1 && cfg.threads <= 8);
    assert!(cfg.num_flows >= 1 && cfg.num_flows <= 16384);
    cfg
}

fn run_offline_self_test() {
    let mut buf = [0u8; MAX_RPC_BYTES];
    for vlen in [8usize, 64, 128, 256, 1024, 3840] {
        let n = build_mc_set_udp(123, opaque_from_rpc_seq((2u64 << 34) | 77), 42, 3, vlen, &mut buf);
        assert_eq!(n, 8 + 24 + 8 + 16 + vlen);
        assert!(verify_mc_value(42, &buf[56..n]));
    }
    let gn = build_mc_get_udp(9, opaque_from_rpc_seq((1u64 << 34) | 5), 11, &mut buf);
    assert_eq!(gn, 48);
    assert_eq!(buf[7], 0);
    // Simulate NFV server transform and verify client check
    for is_lb in [false, true] {
        for &sz in &[64usize, 256, 1024] {
            let rseq = (1u64 << 34) | 99;
            let fid = 321u16;
            let n = build_nfv_req(rseq, fid, is_lb, true, sz, &mut buf);
            assert_eq!(n, sz);
            let (src_ip, dst_ip, sport, dport, proto) = nfv_five_tuple(fid, is_lb);
            let mapped_ip = expected_nfv_ip(is_lb, src_ip, dst_ip, sport, dport, proto);
            let mapped_port = expected_nfv_port(is_lb, fid, dport);
            let csum = nfv_hdr_checksum(src_ip, dst_ip, mapped_ip, sport, dport, mapped_port, proto);
            buf[21] |= 0x80;
            buf[24..28].copy_from_slice(&mapped_ip.to_le_bytes());
            buf[28..30].copy_from_slice(&mapped_port.to_le_bytes());
            buf[30..32].copy_from_slice(&csum.to_le_bytes());
            buf[32..40].copy_from_slice(&1u64.to_le_bytes());
            let rsig = u64::from_le_bytes(buf[40..48].try_into().unwrap());
            let resp_sig = rsig ^ splitmix64(((mapped_ip as u64) << 16) | (mapped_port as u64));
            buf[40..48].copy_from_slice(&resp_sig.to_le_bytes());
            assert!(verify_nfv_resp(&buf[..n], rseq, fid, is_lb, sz));
        }
    }
    let mut h = FastHist::new();
    for x in 1..=1000u64 {
        h.record_ns(x * 100);
    }
    assert!((h.pct_us(0.50) - 50.0).abs() < 1.0);
    assert!((h.pct_us(0.99) - 99.0).abs() < 1.0);
    println!("MRV209_CLIENT_SELF_TEST OK");
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
    burst_sent_measured: u64,
    burst_done_measured: u64,
    burst_events: u64,
    req_bytes_measured: u64,
    resp_bytes_measured: u64,
    wire_tx_bytes_measured: u64,
    wire_rx_bytes_measured: u64,
    bad: u64,
    proto_err: u64,
    val_err: u64,
    torn: u64,
    send_err: u64,
    dropped_timeouts: u64,
    lost: u64,
    window_cap_hits: u64,
    max_inflight: usize,
    dt_s: f64,
    hist_tot: FastHist,
    hist_bg: FastHist,
    hist_burst: FastHist,
    hist_wire: FastHist,
}

fn main() {
    let cfg = parse_args();
    if cfg.self_test {
        run_offline_self_test();
        return;
    }

    pin_cpu(cfg.cpu);
    let tsc_hz = calibrate_tsc_hz(cfg.cpu);
    let ticks_to_ns = 1e9 / tsc_hz;
    let cpu_khz_start = read_cpu_khz(cfg.cpu);

    let nk = cfg.num_keys;
    let p40 = (nk * 4) / 10;
    let p50 = nk / 2;
    let p75 = (nk * 3) / 4;
    let p80 = (nk * 8) / 10;
    let p90 = (nk * 9) / 10;

    let zipf_all = Arc::new(ZipfSampler::new(nk, cfg.zipf_s));
    let zipf_p40 = Arc::new(ZipfSampler::new(p40.max(1), cfg.zipf_s));
    let zipf_p40_b = Arc::new(ZipfSampler::new((p80 - p40).max(1), cfg.zipf_s));
    let zipf_p20 = Arc::new(ZipfSampler::new((nk - p80).max(1), cfg.zipf_s));
    let zipf_p50_a = Arc::new(ZipfSampler::new(p50.max(1), cfg.zipf_s));
    let zipf_p50_b = Arc::new(ZipfSampler::new((nk - p50).max(1), cfg.zipf_s));
    let zipf_ws1 = Arc::new(ZipfSampler::new((p75 - p50).max(1), cfg.zipf_s));
    let zipf_ws2 = Arc::new(ZipfSampler::new((p90 - p75).max(1), cfg.zipf_s));
    let zipf_ws3 = Arc::new(ZipfSampler::new((nk - p90).max(1), cfg.zipf_s));

    let b_probe = Arc::new(Barrier::new(cfg.threads));
    let b_warmup = Arc::new(Barrier::new(cfg.threads));
    let b_go = Arc::new(Barrier::new(cfg.threads));
    let b_done = Arc::new(Barrier::new(cfg.threads));

    let per_thread_win = if cfg.mode == DriveMode::Closed {
        (cfg.window / cfg.threads).max(1).min(INFLIGHT_CAP - 1)
    } else {
        (cfg.window / cfg.threads).max(1024).min(INFLIGHT_CAP - 1)
    };
    let per_thread_bg_krps = cfg.rate_krps / (cfg.threads as f64);
    let per_thread_burst_krps = cfg.burst_rate_krps / (cfg.threads as f64);
    let drop_timeout_ticks = ((cfg.drop_timeout_ms as f64) * 1e-3 * tsc_hz) as u64;
    let burst_period_ticks = ((cfg.burst_period_ms.max(1) as f64) * 1e-3 * tsc_hz) as u64;
    let burst_dur_ticks = ((cfg.burst_dur_us as f64) * 1e-6 * tsc_hz) as u64;

    let mut handles = Vec::with_capacity(cfg.threads);
    for tid in 0..cfg.threads {
        let cfg = cfg.clone();
        let zipf_all = Arc::clone(&zipf_all);
        let zipf_p40 = Arc::clone(&zipf_p40);
        let zipf_p40_b = Arc::clone(&zipf_p40_b);
        let zipf_p20 = Arc::clone(&zipf_p20);
        let zipf_p50_a = Arc::clone(&zipf_p50_a);
        let zipf_p50_b = Arc::clone(&zipf_p50_b);
        let zipf_ws1 = Arc::clone(&zipf_ws1);
        let zipf_ws2 = Arc::clone(&zipf_ws2);
        let zipf_ws3 = Arc::clone(&zipf_ws3);
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
            let mut tx_tmp = [0u8; MAX_RPC_BYTES];
            let mut inflight_cnt: usize = 0;

            let mut hist_tot = FastHist::new();
            let mut hist_bg = FastHist::new();
            let mut hist_burst = FastHist::new();
            let mut hist_wire = FastHist::new();

            let mut local_seq: u64 = 0;
            let mut set_ver_ctr: u64 = 2 + (tid as u64) * 1_000_000;
            let mut tx_rpcs_total: u64 = 0;
            let mut rx_rpcs_total: u64 = 0;
            let mut tx_pkts_total: u64 = 0;
            let mut rx_pkts_total: u64 = 0;
            let mut sent_measured: u64 = 0;
            let mut done_measured: u64 = 0;
            let mut burst_sent_measured: u64 = 0;
            let mut burst_done_measured: u64 = 0;
            let mut burst_events: u64 = 0;
            let mut req_bytes_measured: u64 = 0;
            let mut resp_bytes_measured: u64 = 0;
            let mut wire_tx_bytes_measured: u64 = 0;
            let mut wire_rx_bytes_measured: u64 = 0;
            let mut bad: u64 = 0;
            let mut proto_err: u64 = 0;
            let mut val_err: u64 = 0;
            let mut torn: u64 = 0;
            let mut send_err: u64 = 0;
            let mut dropped_timeouts: u64 = 0;
            let mut window_cap_hits: u64 = 0;
            let mut max_inflight: usize = 0;

            let mut comps = [RxComp::default(); 32];
            let mut repost_slots = [0u32; 32];
            let mut batch_slots = [0u32; 16];
            let mut batch_lens = [0u32; 16];
            let mut batch_frags = [0u32; 16];

            let bg_gap_ticks = if per_thread_bg_krps > 0.0 {
                tsc_hz / (per_thread_bg_krps * 1e3)
            } else {
                0.0
            };
            let burst_gap_ticks = if per_thread_burst_krps > 0.0 {
                tsc_hz / (per_thread_burst_krps * 1e3)
            } else {
                bg_gap_ticks
            };

            let sample_mc_op = |rng: &mut Rng64| -> (u8, u64, usize) {
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
                    Workload::Get3840 => (MC_CMD_GET, pick(rng, 0, nk, &zipf_all), 3840),
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
                    Workload::WebSearchMc => {
                        let r = rng.next_u64() % 100;
                        if r < 50 {
                            let op = if (rng.next_u64() % 100) < cfg.set_pct { MC_CMD_SET } else { MC_CMD_GET };
                            (op, pick(rng, 0, p50, &zipf_p50_a), 64)
                        } else if r < 75 {
                            (MC_CMD_GET, pick(rng, p50, p75 - p50, &zipf_ws1), 256)
                        } else if r < 90 {
                            (MC_CMD_GET, pick(rng, p75, p90 - p75, &zipf_ws2), 1024)
                        } else {
                            (MC_CMD_GET, pick(rng, p90, nk - p90, &zipf_ws3), 3840)
                        }
                    }
                    _ => unreachable!(),
                }
            };

            let sample_nfv_len = |rng: &mut Rng64| -> usize {
                match cfg.workload {
                    Workload::NfvNat64 => 64,
                    Workload::NfvNat256 | Workload::NfvLb256 => 256,
                    Workload::NfvNat1024 => 1024,
                    Workload::NfvWebSearch => {
                        let r = rng.next_u64() % 100;
                        if r < 50 {
                            64
                        } else if r < 75 {
                            256
                        } else {
                            1024
                        }
                    }
                    _ => 256,
                }
            };

            let mut poll_once = |inflight: &mut [Inflight; INFLIGHT_CAP],
                                 reasm_buf: &mut [u8],
                                 inflight_cnt: &mut usize,
                                 rx_rpcs_total: &mut u64,
                                 rx_pkts_total: &mut u64,
                                 done_measured: &mut u64,
                                 burst_done_measured: &mut u64,
                                 resp_bytes_measured: &mut u64,
                                 wire_rx_bytes_measured: &mut u64,
                                 bad: &mut u64,
                                 proto_err: &mut u64,
                                 val_err: &mut u64,
                                 torn: &mut u64,
                                 hist_tot: &mut FastHist,
                                 hist_bg: &mut FastHist,
                                 hist_burst: &mut FastHist,
                                 hist_wire: &mut FastHist|
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

                    let tag = p[7];
                    if tag == OP_NFV_WIRE {
                        if plen < NFV_MIN_LEN {
                            *bad += 1;
                            continue;
                        }
                        let w0 = u64::from_le_bytes(p[0..8].try_into().unwrap());
                        let (rseq, _, _, _, _) = unpack_wire_meta(w0);
                        let sidx = (rseq as usize) & INFLIGHT_MASK;
                        let slot = &mut inflight[sidx];
                        if !slot.active || slot.rpc_seq != rseq || !slot.is_nfv {
                            if !cfg.allow_drops {
                                *bad += 1;
                            }
                            continue;
                        }
                        if !verify_nfv_resp(p, rseq, slot.flow_id, slot.is_nfv_lb, slot.exp_resp_len) {
                            *val_err += 1;
                            *bad += 1;
                        }
                        slot.active = false;
                        *inflight_cnt -= 1;
                        *rx_rpcs_total += 1;
                        if slot.measured {
                            let tot_ns = ((t_now.saturating_sub(slot.t_sched)) as f64 * ticks_to_ns) as u64;
                            let wire_ns = ((t_now.saturating_sub(slot.t_send)) as f64 * ticks_to_ns) as u64;
                            *done_measured += 1;
                            if slot.is_burst {
                                *burst_done_measured += 1;
                                hist_burst.record_ns(tot_ns);
                            } else {
                                hist_bg.record_ns(tot_ns);
                            }
                            *resp_bytes_measured += plen as u64;
                            *wire_rx_bytes_measured += (plen + ETH_WIRE_OVERHEAD).max(84) as u64;
                            hist_tot.record_ns(tot_ns);
                            hist_wire.record_ns(wire_ns);
                        }
                        continue;
                    }

                    let (sidx, mc_len, fcnt, mc_slice): (usize, usize, u8, &[u8]) = if tag == 0 {
                        if plen < 32 || p[8] != MC_MAGIC_RES {
                            *proto_err += 1;
                            *bad += 1;
                            continue;
                        }
                        let opaque = u32::from_be_bytes([p[20], p[21], p[22], p[23]]);
                        let rseq = rpc_seq_from_opaque(opaque);
                        let sidx = (rseq as usize) & INFLIGHT_MASK;
                        let slot = &inflight[sidx];
                        if !slot.active || slot.rpc_seq != rseq {
                            if !cfg.allow_drops {
                                *bad += 1;
                            }
                            continue;
                        }
                        if slot.exp_resp_len != plen || slot.exp_resp_frags != 1 {
                            *bad += 1;
                            continue;
                        }
                        (sidx, plen, 1u8, p)
                    } else if tag == OP_FRAG {
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
                        let (rseq, mc_len_u16, fidx, fcnt, _) = unpack_wire_meta(w0);
                        let mc_len = mc_len_u16 as usize;
                        let sidx = (rseq as usize) & INFLIGHT_MASK;
                        let slot = &mut inflight[sidx];
                        if !slot.active || slot.rpc_seq != rseq {
                            if !cfg.allow_drops {
                                *bad += 1;
                            }
                            continue;
                        }
                        if slot.exp_resp_len != mc_len || slot.exp_resp_frags != fcnt || fidx >= fcnt {
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
                        if slot.is_burst {
                            *burst_done_measured += 1;
                            hist_burst.record_ns(tot_ns);
                        } else {
                            hist_bg.record_ns(tot_ns);
                        }
                        *resp_bytes_measured += mc_len as u64;
                        let wire_b = if fcnt == 1 {
                            (mc_len + ETH_WIRE_OVERHEAD).max(84) as u64
                        } else {
                            (fcnt as u64) * ((MAX_SEG_BYTES + ETH_WIRE_OVERHEAD) as u64)
                        };
                        *wire_rx_bytes_measured += wire_b;
                        hist_tot.record_ns(tot_ns);
                        hist_wire.record_ns(wire_ns);
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
                    // Clear any leftover warmup drops if allow_drops was set
                    if cfg.allow_drops && inflight_cnt > 0 {
                        for slot in &mut inflight {
                            slot.active = false;
                        }
                        inflight_cnt = 0;
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
                let phase_start_tsc = rdtsc_after_loads();
                let mut t_next_sched = phase_start_tsc as f64;
                let mut next_burst_epoch: u64 = 1;
                let mut incast_rem: usize = 0;
                let mut incast_sched_tsc: u64 = 0;
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
                        &mut burst_done_measured,
                        &mut resp_bytes_measured,
                        &mut wire_rx_bytes_measured,
                        &mut bad,
                        &mut proto_err,
                        &mut val_err,
                        &mut torn,
                        &mut hist_tot,
                        &mut hist_bg,
                        &mut hist_burst,
                        &mut hist_wire,
                    );
                    if got > 0 {
                        last_rx = Instant::now();
                    } else if !cfg.allow_drops
                        && inflight_cnt > 0
                        && (iter & 0x3fff) == 0
                        && last_rx.elapsed() > Duration::from_secs(3)
                    {
                        eprintln!("mrv209_client tid={tid} stalled >3s phase={phase} inflight={inflight_cnt}");
                        if measured {
                            t_meas_end_tsc = rdtsc_after_loads();
                        }
                        break;
                    }

                    // Stale-slot eviction ONLY when `--allow-drops` is set (for `small-sdp-drop` baseline)
                    if cfg.allow_drops && (iter & 0x3ff) == 0 && inflight_cnt > 0 {
                        let now_age = rdtsc_after_loads();
                        for slot in &mut inflight {
                            if slot.active && now_age.saturating_sub(slot.t_send) > drop_timeout_ticks {
                                slot.active = false;
                                inflight_cnt -= 1;
                                if slot.measured {
                                    dropped_timeouts += 1;
                                }
                            }
                        }
                    }

                    if !stop_sending && t_phase_start.elapsed() >= Duration::from_millis(phase_ms) {
                        stop_sending = true;
                        if measured {
                            t_meas_end_tsc = rdtsc_after_loads();
                        }
                        let drain_ms = if cfg.allow_drops { cfg.drop_timeout_ms + 150 } else { 1200 };
                        drain_deadline = Some(Instant::now() + Duration::from_millis(drain_ms));
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
                    let elapsed_tsc = now_tsc.saturating_sub(phase_start_tsc);

                    // Check synchronized Incast trigger across threads (during measurement phase only)
                    if cfg.mode == DriveMode::Incast && measured {
                        let epoch_now = elapsed_tsc / burst_period_ticks;
                        if epoch_now >= next_burst_epoch {
                            next_burst_epoch = epoch_now + 1;
                            incast_rem += cfg.incast_pkts;
                            incast_sched_tsc = now_tsc;
                            burst_events += 1;
                        }
                    }

                    while n_batch < cfg.max_tx_batch && wr_batch <= 60 {
                        if inflight_cnt >= per_thread_win {
                            if measured && cfg.mode != DriveMode::Closed && (now_tsc as f64) >= t_next_sched {
                                window_cap_hits += 1;
                            }
                            break;
                        }

                        let (t_sched, is_burst) = match cfg.mode {
                            DriveMode::Closed => (now_tsc, false),
                            DriveMode::Poisson => {
                                if (now_tsc as f64) < t_next_sched {
                                    break;
                                }
                                let ts = t_next_sched as u64;
                                t_next_sched += -rng_arr.next_f64().ln() * bg_gap_ticks;
                                (ts, false)
                            }
                            DriveMode::PoissonBurst => {
                                let in_burst_win = measured && (elapsed_tsc % burst_period_ticks) < burst_dur_ticks;
                                if in_burst_win {
                                    let epoch_now = elapsed_tsc / burst_period_ticks + 1;
                                    if epoch_now >= next_burst_epoch {
                                        next_burst_epoch = epoch_now + 1;
                                        burst_events += 1;
                                    }
                                }
                                if (now_tsc as f64) < t_next_sched {
                                    break;
                                }
                                let ts = t_next_sched as u64;
                                let gap = if in_burst_win { burst_gap_ticks } else { bg_gap_ticks };
                                t_next_sched += -rng_arr.next_f64().ln() * gap;
                                (ts, in_burst_win)
                            }
                            DriveMode::Incast => {
                                if incast_rem > 0 {
                                    incast_rem -= 1;
                                    (incast_sched_tsc, true)
                                } else {
                                    if (now_tsc as f64) < t_next_sched {
                                        break;
                                    }
                                    let ts = t_next_sched as u64;
                                    t_next_sched += -rng_arr.next_f64().ln() * bg_gap_ticks;
                                    (ts, false)
                                }
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

                        let flow_id = ((rng.next_u64() as usize) % cfg.num_flows) as u16;
                        let opaque = opaque_from_rpc_seq(rpc_seq);
                        let is_nfv = cfg.workload.is_nfv();
                        let is_nfv_lb = cfg.workload.is_nfv_lb();

                        let (req_len, exp_resp_len, exp_udp_tot, mc_op, key, val_len) = if is_nfv {
                            let sz = sample_nfv_len(&mut rng);
                            let n = build_nfv_req(rpc_seq, flow_id, is_nfv_lb, is_burst, sz, &mut tx_tmp);
                            (n, n, 1u16, 0u8, 0u64, sz)
                        } else {
                            let (op, k, vlen) = sample_mc_op(&mut rng);
                            let (n, exp_bin_len) = if op == MC_CMD_GET {
                                (build_mc_get_udp(flow_id, opaque, k, &mut tx_tmp), MC_BIN_HDR_LEN + 4 + vlen)
                            } else {
                                set_ver_ctr += 1;
                                (
                                    build_mc_set_udp(flow_id, opaque, k, set_ver_ctr, vlen, &mut tx_tmp),
                                    MC_BIN_HDR_LEN,
                                )
                            };
                            let udp_tot = mc_udp_dgram_cnt(exp_bin_len);
                            let resp_len = (udp_tot as usize) * MC_UDP_HDR_LEN + exp_bin_len;
                            (n, resp_len, udp_tot, op, k, vlen)
                        };

                        let req_frags = roce_frag_cnt(req_len);
                        let seg_len = if req_frags > 1 { MAX_SEG_BYTES } else { req_len };
                        let exp_resp_frags = roce_frag_cnt(exp_resp_len);

                        let t_send = if n_batch == 0 { now_tsc } else { rdtsc_after_loads() };
                        let tx_ptr = unsafe { tx_base.add(tx_slot * (TX_STRIDE as usize)) };
                        if req_frags == 1 {
                            let buf = unsafe { std::slice::from_raw_parts_mut(tx_ptr, req_len) };
                            buf.copy_from_slice(&tx_tmp[..req_len]);
                        } else {
                            for f in 0..(req_frags as usize) {
                                let seg = unsafe {
                                    std::slice::from_raw_parts_mut(tx_ptr.add(f * MAX_SEG_BYTES), MAX_SEG_BYTES)
                                };
                                let wf = pack_wire_meta(rpc_seq, req_len as u16, f as u8, req_frags, OP_FRAG);
                                let start = f * MAX_FRAG_PAYLOAD;
                                let take = MAX_FRAG_PAYLOAD.min(req_len.saturating_sub(start));
                                seg[0..8].copy_from_slice(&wf.to_le_bytes());
                                seg[8..8 + take].copy_from_slice(&tx_tmp[start..start + take]);
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
                            is_burst,
                            is_nfv,
                            is_nfv_lb,
                            rpc_seq,
                            req_id: flow_id,
                            flow_id,
                            opaque,
                            mc_op,
                            key,
                            val_len,
                            exp_udp_tot,
                            exp_resp_len,
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
                            if is_burst {
                                burst_sent_measured += 1;
                            }
                            req_bytes_measured += req_len as u64;
                            wire_tx_bytes_measured += if req_frags == 1 {
                                (req_len + ETH_WIRE_OVERHEAD).max(84) as u64
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

            let lost = sent_measured.saturating_sub(done_measured);
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
                burst_sent_measured,
                burst_done_measured,
                burst_events,
                req_bytes_measured,
                resp_bytes_measured,
                wire_tx_bytes_measured,
                wire_rx_bytes_measured,
                bad,
                proto_err,
                val_err,
                torn,
                send_err,
                dropped_timeouts,
                lost,
                window_cap_hits,
                max_inflight,
                dt_s,
                hist_tot,
                hist_bg,
                hist_burst,
                hist_wire,
            }
        }));
    }

    let mut tot_tx = 0u64;
    let mut tot_rx = 0u64;
    let mut tot_tx_pkts = 0u64;
    let mut tot_rx_pkts = 0u64;
    let mut tot_sent_m = 0u64;
    let mut tot_done_m = 0u64;
    let mut tot_burst_sent = 0u64;
    let mut tot_burst_done = 0u64;
    let mut tot_burst_events = 0u64;
    let mut tot_req_b = 0u64;
    let mut tot_resp_b = 0u64;
    let mut tot_wire_tx_b = 0u64;
    let mut tot_wire_rx_b = 0u64;
    let mut tot_bad = 0u64;
    let mut tot_proto_err = 0u64;
    let mut tot_val_err = 0u64;
    let mut tot_torn = 0u64;
    let mut tot_send_err = 0u64;
    let mut tot_drop_to = 0u64;
    let mut tot_lost = 0u64;
    let mut tot_wcap = 0u64;
    let mut max_inf = 0usize;
    let mut dt_sum = 0.0f64;
    let mut rxqs = Vec::new();

    let mut hist_tot = FastHist::new();
    let mut hist_bg = FastHist::new();
    let mut hist_burst = FastHist::new();
    let mut hist_wire = FastHist::new();

    for h in handles {
        let r = h.join().unwrap();
        rxqs.push(r.rxq_matched.to_string());
        tot_tx += r.tx_rpcs_total;
        tot_rx += r.rx_rpcs_total;
        tot_tx_pkts += r.tx_pkts_total;
        tot_rx_pkts += r.rx_pkts_total;
        tot_sent_m += r.sent_measured;
        tot_done_m += r.done_measured;
        tot_burst_sent += r.burst_sent_measured;
        tot_burst_done += r.burst_done_measured;
        tot_burst_events += r.burst_events;
        tot_req_b += r.req_bytes_measured;
        tot_resp_b += r.resp_bytes_measured;
        tot_wire_tx_b += r.wire_tx_bytes_measured;
        tot_wire_rx_b += r.wire_rx_bytes_measured;
        tot_bad += r.bad;
        tot_proto_err += r.proto_err;
        tot_val_err += r.val_err;
        tot_torn += r.torn;
        tot_send_err += r.send_err;
        tot_drop_to += r.dropped_timeouts;
        tot_lost += r.lost;
        tot_wcap += r.window_cap_hits;
        if r.max_inflight > max_inf {
            max_inf = r.max_inflight;
        }
        dt_sum += r.dt_s;
        hist_tot.merge_from(&r.hist_tot);
        hist_bg.merge_from(&r.hist_bg);
        hist_burst.merge_from(&r.hist_burst);
        hist_wire.merge_from(&r.hist_wire);
    }

    let dt_s = dt_sum / (cfg.threads as f64);
    let offered_mrps = if dt_s > 0.0 { tot_sent_m as f64 / dt_s / 1e6 } else { 0.0 };
    let achieved_mrps = if dt_s > 0.0 { tot_done_m as f64 / dt_s / 1e6 } else { 0.0 };
    let drop_pct = if tot_sent_m > 0 { (tot_lost as f64) * 100.0 / (tot_sent_m as f64) } else { 0.0 };
    let payload_tx_gbps = if dt_s > 0.0 { tot_req_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let payload_rx_gbps = if dt_s > 0.0 { tot_resp_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let wire_tx_gbps = if dt_s > 0.0 { tot_wire_tx_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let wire_rx_gbps = if dt_s > 0.0 { tot_wire_rx_b as f64 * 8.0 / dt_s / 1e9 } else { 0.0 };
    let integrity_ok = tot_bad == 0 && tot_proto_err == 0 && tot_val_err == 0 && tot_torn == 0 && tot_send_err == 0;
    let verified = integrity_ok && (cfg.allow_drops || tot_lost == 0);
    let cpu_khz_end = read_cpu_khz(cfg.cpu);

    println!(
        "MRV209_CLIENT_JSON {{\"verified\":{},\"integrity_ok\":{},\"allow_drops\":{},\"threads\":{},\"num_flows\":{},\
         \"rxqs\":[{}],\"tsc_ghz\":{:.6},\"cpu_khz_start\":{},\"cpu_khz_end\":{},\"dt_s\":{:.6},\
         \"tx_rpcs_total\":{},\"rx_rpcs_total\":{},\"tx_pkts_total\":{},\"rx_pkts_total\":{},\
         \"sent_measured\":{},\"done_measured\":{},\"burst_sent\":{},\"burst_done\":{},\"burst_events\":{},\
         \"offered_mrps\":{:.6},\"achieved_mrps\":{:.6},\"drop_pct\":{:.4},\
         \"payload_tx_gbps\":{:.4},\"payload_rx_gbps\":{:.4},\"wire_tx_gbps\":{:.4},\"wire_rx_gbps\":{:.4},\
         \"tot_p50_us\":{:.3},\"tot_p95_us\":{:.3},\"tot_p99_us\":{:.3},\"tot_p999_us\":{:.3},\"tot_mean_us\":{:.3},\
         \"bg_p50_us\":{:.3},\"bg_p99_us\":{:.3},\"bg_p999_us\":{:.3},\
         \"burst_p50_us\":{:.3},\"burst_p99_us\":{:.3},\"burst_p999_us\":{:.3},\
         \"wire_p50_us\":{:.3},\"wire_p99_us\":{:.3},\
         \"max_inflight\":{},\"window_cap_hits\":{},\"dropped_timeouts\":{},\"lost\":{},\
         \"bad\":{},\"proto_err\":{},\"val_err\":{},\"torn\":{},\"send_err\":{}}}",
        verified,
        integrity_ok,
        cfg.allow_drops,
        cfg.threads,
        cfg.num_flows,
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
        tot_burst_sent,
        tot_burst_done,
        tot_burst_events,
        offered_mrps,
        achieved_mrps,
        drop_pct,
        payload_tx_gbps,
        payload_rx_gbps,
        wire_tx_gbps,
        wire_rx_gbps,
        hist_tot.pct_us(0.50),
        hist_tot.pct_us(0.95),
        hist_tot.pct_us(0.99),
        hist_tot.pct_us(0.999),
        hist_tot.mean_us(),
        hist_bg.pct_us(0.50),
        hist_bg.pct_us(0.99),
        hist_bg.pct_us(0.999),
        hist_burst.pct_us(0.50),
        hist_burst.pct_us(0.99),
        hist_burst.pct_us(0.999),
        hist_wire.pct_us(0.50),
        hist_wire.pct_us(0.99),
        max_inf,
        tot_wcap,
        tot_drop_to,
        tot_lost,
        tot_bad,
        tot_proto_err,
        tot_val_err,
        tot_torn,
        tot_send_err,
    );

    if !verified {
        std::process::exit(2);
    }
}
