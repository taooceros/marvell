//! Multi-QP 100GbE non-root RoCEv2 UD (`IBV_QPT_UD`) traffic generator & end-to-end
//! integrity verifier on `mercury` (`mlx5_2`, NUMA 0) for MRV-228.
//!
//! 8 threads pinned to NUMA 0 (`cpu 4..11`), mapped 2 per CN106 hardware SDP queue `0..3`:
//!   - `tid 0..3`: Healthy latency-sensitive short RPC flows (`flow_id = lane + 4*k`,
//!                 `k = 1..(F/4)`, `256 B`, `CLASS_RPC_HI`, open-loop Poisson with true
//!                 `t_sched` -> zero coordinated omission!).
//!   - `tid 4..7`: Troublemaker bulk/elephant flows (`flow_id = lane`, `1400 B`, `CLASS_BULK`):
//!       * `--scenario stall`: continuous paced stream (`--bulk-send-gbps`, default `48 Gbps`)
//!         while host stalls troublemaker consumers for `1 / 4 / 10 ms`.
//!       * `--scenario burst`: periodic wire-speed `16 MiB` or `64 MiB` elephant bursts
//!         while host bulk consumers drain at `--bulk-rate-gbps`.
//!
//! Builds directly on `mercury`:
//!   gcc -O3 -Wall -Wextra -Werror -c mrv228_verbs_shim.c -o mrv228_verbs_shim.o
//!   rustc -O -D warnings mrv228_client.rs -C link-arg=mrv228_verbs_shim.o -C link-arg=-libverbs -o mrv228_client
//! Offline unit test on `saturn`:
//!   rustc --test --cfg mock_verbs -O -D warnings mrv228_client.rs -o /tmp/mrv228_client_test

use std::ffi::CString;
use std::io::Write;
use std::sync::{Arc, Barrier};
use std::time::{Duration, Instant};

const ROCE_QKEY: u32 = 0x1111_1111;
const ROCE_CARD_QPN: u32 = 0x0001_2345;
const ROCE_HDR_LEN: usize = 62;
const ROCE_ICRC_LEN: usize = 4;
const ETH_WIRE_OVERHEAD: usize = ROCE_HDR_LEN + ROCE_ICRC_LEN + 4 + 20; // 90 B/frame on wire

const MIN_WIRE_PAYLOAD: usize = 64;
const MAX_WIRE_PAYLOAD: usize = 1408;
const SEQ_MASK: u64 = (1u64 << 38) - 1;
const MAX_FLOWS: usize = 1024;
const GRH_BYTES: usize = 40;

const OP_DATA: u8 = 1;
const OP_PROBE: u8 = 2;
const OP_ACK: u8 = 3;

const CLASS_BULK: u8 = 0;
const CLASS_RPC_HI: u8 = 1;

const PATH_PER_FLOW_CR: u8 = 0;
const PATH_CEIO_SLOW: u8 = 2;

const FLAG_CEIO_LLC_LEAK: u8 = 1 << 0;
const FLAG_PHASE_PAUSED: u8 = 1 << 1;

const RX_SLOTS: u32 = 2048;
const RX_STRIDE: u32 = 2048;
const TX_SLOTS: u32 = 2048;
const TX_STRIDE: u32 = 2048;
const INFLIGHT_CAP: usize = 32_768;
const INFLIGHT_MASK: usize = INFLIGHT_CAP - 1;
const TS_MAX_BUCKETS: usize = 4096;

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

#[cfg(not(mock_verbs))]
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
        lens: *const u32,
        count: i32,
        force_sig: i32,
    ) -> i32;
    fn ud_drain_send_cq(u: *mut UdClient) -> i32;
    fn ud_poll_rx(u: *mut UdClient, out: *mut RxComp, max_n: i32) -> i32;
    fn ud_repost_rx_batch(u: *mut UdClient, slots: *const u32, count: i32) -> i32;
    fn ud_close(u: *mut UdClient);
    fn sched_setaffinity(pid: i32, cpusetsize: usize, mask: *const u8) -> i32;
}

#[cfg(mock_verbs)]
mod mock_ffi {
    use super::{RxComp, UdClient};
    pub unsafe fn ud_open(
        _dev: *const i8,
        _sgid: i32,
        _ip: *const u8,
        _qpn: u32,
        _qkey: u32,
        _fl: u32,
        _rxs: u32,
        _rxst: u32,
        _txs: u32,
        _txst: u32,
    ) -> *mut UdClient {
        std::ptr::null_mut()
    }
    pub unsafe fn ud_set_flow(_u: *mut UdClient, _fl: u32, _qpn: u32) -> i32 {
        0
    }
    pub unsafe fn ud_rx_buf(_u: *mut UdClient) -> *mut u8 {
        std::ptr::null_mut()
    }
    pub unsafe fn ud_tx_buf(_u: *mut UdClient) -> *mut u8 {
        std::ptr::null_mut()
    }
    pub unsafe fn ud_post_send_batch(
        _u: *mut UdClient,
        _tx_slots: *const u32,
        _lens: *const u32,
        _count: i32,
        _force_sig: i32,
    ) -> i32 {
        0
    }
    pub unsafe fn ud_drain_send_cq(_u: *mut UdClient) -> i32 {
        0
    }
    pub unsafe fn ud_poll_rx(_u: *mut UdClient, _out: *mut RxComp, _max_n: i32) -> i32 {
        0
    }
    pub unsafe fn ud_repost_rx_batch(_u: *mut UdClient, _slots: *const u32, _count: i32) -> i32 {
        0
    }
    pub unsafe fn ud_close(_u: *mut UdClient) {}
    pub unsafe fn sched_setaffinity(_pid: i32, _sz: usize, _mask: *const u8) -> i32 {
        0
    }
}
#[cfg(mock_verbs)]
use mock_ffi::*;

#[inline(always)]
fn rdtsc_after_loads() -> u64 {
    #[cfg(target_arch = "x86_64")]
    {
        let mut aux: u32 = 0;
        unsafe { core::arch::x86_64::__rdtscp(&mut aux) }
    }
    #[cfg(not(target_arch = "x86_64"))]
    {
        0
    }
}

fn pin_cpu(cpu: usize) {
    let mut mask = [0u8; 128];
    mask[cpu / 8] |= 1u8 << (cpu % 8);
    unsafe {
        sched_setaffinity(0, mask.len(), mask.as_ptr());
    }
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
    let hz = (c1.saturating_sub(c0)) as f64 / t0.elapsed().as_secs_f64();
    let mut buf = vec![0u64; 2 * 1024 * 1024];
    let ramp = Instant::now();
    let mut idx = 0usize;
    let mut acc = 1u64;
    while ramp.elapsed() < Duration::from_millis(450) {
        for _ in 0..4096 {
            idx = (idx + 8) & (buf.len() - 1);
            let v = unsafe { *buf.get_unchecked(idx) };
            acc = acc.wrapping_mul(6364136223846793005).wrapping_add(v ^ 1);
            unsafe { *buf.get_unchecked_mut(idx) = acc };
        }
        std::hint::black_box(acc);
        if ramp.elapsed() >= Duration::from_millis(200) && read_cpu_khz(cpu) >= 3_200_000 {
            break;
        }
    }
    hz
}

// -----------------------------------------------------------------------------
// Wire format matching `testbed::mrv228` bit-for-bit
// -----------------------------------------------------------------------------

#[inline(always)]
const fn pack_meta(
    global_seq: u64,
    len_bytes: usize,
    pkt_class: u8,
    path_tag: u8,
    op: u8,
    flags: u8,
) -> u64 {
    let len_div8 = ((len_bytes >> 3) as u64) & 0x3ff;
    (1u64 << 63)
        | (global_seq & SEQ_MASK)
        | (len_div8 << 38)
        | (((pkt_class as u64) & 0x3) << 48)
        | (((path_tag as u64) & 0x3) << 50)
        | (((op as u64) & 0xf) << 52)
        | (((flags as u64) & 0x7f) << 56)
}

#[inline(always)]
const fn unpack_meta(w0: u64) -> Option<(u64, usize, u8, u8, u8, u8)> {
    if (w0 >> 63) == 0 {
        return None;
    }
    let global_seq = w0 & SEQ_MASK;
    let len_bytes = (((w0 >> 38) & 0x3ff) as usize) << 3;
    let pkt_class = ((w0 >> 48) & 0x3) as u8;
    let path_tag = ((w0 >> 50) & 0x3) as u8;
    let op = ((w0 >> 52) & 0xf) as u8;
    let flags = ((w0 >> 56) & 0x7f) as u8;
    Some((global_seq, len_bytes, pkt_class, path_tag, op, flags))
}

#[inline(always)]
const fn pack_flow_meta(flow_id: u16, flow_seq: u64) -> u64 {
    (flow_id as u64) | ((flow_seq & ((1u64 << 48) - 1)) << 16)
}

#[inline(always)]
const fn unpack_flow_meta(w2: u64) -> (u16, u64) {
    ((w2 & 0xffff) as u16, w2 >> 16)
}

#[inline(always)]
const fn wire_cookie(global_seq: u64, flow_id: u16, flow_seq: u64, len_bytes: usize) -> u64 {
    global_seq.wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ ((flow_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ (flow_seq.wrapping_mul(0x94d0_49bb_1331_11eb))
        ^ ((len_bytes as u64).wrapping_mul(0x6c62_272e_07bb_0142))
        ^ 0x2280_cafe_babe_55aa
}

#[inline(always)]
const fn probe_cookie(seq: u64) -> u64 {
    seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0xa5a5_5a5a_2280_5678
}

#[inline(always)]
const fn payload_word(cookie: u64, word_idx: usize) -> u64 {
    cookie.wrapping_add((word_idx as u64).wrapping_mul(0x9e37_79b9_7f4a_7c15)) ^ 0x3c3c_c3c3_5a5a_a5a5
}

#[inline(always)]
fn encode_wire_pkt(
    buf: &mut [u8],
    global_seq: u64,
    client_tsc: u64,
    flow_id: u16,
    flow_seq: u64,
    aux_key: u32,
    burst_epoch: u32,
    pkt_class: u8,
    flags: u8,
) {
    let len = buf.len();
    let w0 = pack_meta(global_seq, len, pkt_class, PATH_PER_FLOW_CR, OP_DATA, flags);
    let w2 = pack_flow_meta(flow_id, flow_seq);
    let w3 = (aux_key as u64) | ((burst_epoch as u64) << 32);
    let ck = wire_cookie(global_seq, flow_id, flow_seq, len);

    buf[0..8].copy_from_slice(&w0.to_le_bytes());
    buf[8..16].copy_from_slice(&client_tsc.to_le_bytes());
    buf[16..24].copy_from_slice(&w2.to_le_bytes());
    buf[24..32].copy_from_slice(&w3.to_le_bytes());
    buf[32..40].copy_from_slice(&0u64.to_le_bytes());
    buf[40..48].copy_from_slice(&ck.to_le_bytes());

    let mut off = 48usize;
    let mut widx = 0usize;
    while off + 8 <= len - 8 {
        let w = payload_word(ck, widx);
        buf[off..off + 8].copy_from_slice(&w.to_le_bytes());
        off += 8;
        widx += 1;
    }
    buf[len - 8..len].copy_from_slice(&w0.to_le_bytes());
}

struct Rng64(u64);
impl Rng64 {
    fn new(seed: u64) -> Self {
        Rng64(if seed == 0 { 0x9e37_79b9_7f4a_7c15 } else { seed })
    }
    #[inline(always)]
    fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
    #[inline(always)]
    fn next_exp(&mut self) -> f64 {
        let u = ((self.next_u64() >> 11) as f64 + 0.5) / ((1u64 << 53) as f64);
        -u.ln()
    }
}

const B0: usize = 2000;
const B1: usize = 1800;
const B2: usize = 1980;
const B3: usize = 980;
const TOTAL_BINS: usize = B0 + B1 + B2 + B3 + 1;

struct FastHist {
    bins: Box<[u64; TOTAL_BINS]>,
    count: u64,
    sum_ns: u128,
    min_ns: u64,
    max_ns: u64,
}

impl FastHist {
    fn new() -> Self {
        FastHist {
            bins: Box::new([0u64; TOTAL_BINS]),
            count: 0,
            sum_ns: 0,
            min_ns: u64::MAX,
            max_ns: 0,
        }
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
        if ns < self.min_ns {
            self.min_ns = ns;
        }
        if ns > self.max_ns {
            self.max_ns = ns;
        }
    }
    fn merge_from(&mut self, o: &FastHist) {
        for i in 0..TOTAL_BINS {
            self.bins[i] += o.bins[i];
        }
        self.count += o.count;
        self.sum_ns += o.sum_ns;
        if o.min_ns < self.min_ns {
            self.min_ns = o.min_ns;
        }
        if o.max_ns > self.max_ns {
            self.max_ns = o.max_ns;
        }
    }
    fn mean_us(&self) -> f64 {
        if self.count == 0 {
            0.0
        } else {
            (self.sum_ns as f64) / (self.count as f64) / 1000.0
        }
    }
    fn max_us(&self) -> f64 {
        self.max_ns as f64 / 1000.0
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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Scenario {
    Stall,
    Burst,
}

impl Scenario {
    fn parse(s: &str) -> Self {
        match s {
            "stall" => Scenario::Stall,
            "burst" => Scenario::Burst,
            other => panic!("unknown scenario {other:?} (expected stall|burst)"),
        }
    }
    fn as_str(self) -> &'static str {
        match self {
            Scenario::Stall => "stall",
            Scenario::Burst => "burst",
        }
    }
}

#[derive(Clone)]
struct Config {
    dev: String,
    sgid: i32,
    cpu: usize,
    card_queues: usize,
    scenario: Scenario,
    total_flows: usize,
    rpc_bytes: usize,
    bulk_bytes: usize,
    rpc_rate_krps: f64,
    rpc_closed_win: usize,
    bulk_send_gbps: f64,
    burst_mib: usize,
    burst_period_ms: u64,
    warmup_ms: u64,
    duration_ms: u64,
    ts_bucket_us: u64,
    seed: u64,
    sync_reset: bool,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            dev: "mlx5_2".to_string(),
            sgid: 3,
            cpu: 4,
            card_queues: 4,
            scenario: Scenario::Stall,
            total_flows: 64,
            rpc_bytes: 256,
            bulk_bytes: 1024,
            rpc_rate_krps: 200.0,
            rpc_closed_win: 0,
            bulk_send_gbps: 48.0,
            burst_mib: 16,
            burst_period_ms: 25,
            warmup_ms: 300,
            duration_ms: 1500,
            ts_bucket_us: 500,
            seed: 228,
            sync_reset: false,
        }
    }
}

fn parse_args() -> Config {
    let mut cfg = Config::default();
    let args: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < args.len() {
        let k = args[i].as_str();
        let next = |idx: &mut usize| -> String {
            *idx += 1;
            args.get(*idx).cloned().unwrap_or_else(|| panic!("missing value for {k}"))
        };
        match k {
            "--dev" => cfg.dev = next(&mut i),
            "--sgid" => cfg.sgid = next(&mut i).parse().unwrap(),
            "--cpu" => cfg.cpu = next(&mut i).parse().unwrap(),
            "--card-queues" => cfg.card_queues = next(&mut i).parse().unwrap(),
            "--scenario" => cfg.scenario = Scenario::parse(&next(&mut i)),
            "--total-flows" => cfg.total_flows = next(&mut i).parse().unwrap(),
            "--rpc-bytes" => cfg.rpc_bytes = next(&mut i).parse().unwrap(),
            "--bulk-bytes" => cfg.bulk_bytes = next(&mut i).parse().unwrap(),
            "--rpc-rate-krps" => cfg.rpc_rate_krps = next(&mut i).parse().unwrap(),
            "--rpc-closed-win" => cfg.rpc_closed_win = next(&mut i).parse().unwrap(),
            "--bulk-send-gbps" => cfg.bulk_send_gbps = next(&mut i).parse().unwrap(),
            "--burst-mib" => cfg.burst_mib = next(&mut i).parse().unwrap(),
            "--burst-period-ms" => cfg.burst_period_ms = next(&mut i).parse().unwrap(),
            "--warmup-ms" => cfg.warmup_ms = next(&mut i).parse().unwrap(),
            "--duration-ms" => cfg.duration_ms = next(&mut i).parse().unwrap(),
            "--ts-bucket-us" => cfg.ts_bucket_us = next(&mut i).parse().unwrap(),
            "--seed" => cfg.seed = next(&mut i).parse().unwrap(),
            "--sync-reset" => cfg.sync_reset = true,
            other => panic!("unknown arg: {other}"),
        }
        i += 1;
    }
    assert!(cfg.card_queues == 4);
    assert!(cfg.total_flows >= 8 && cfg.total_flows <= MAX_FLOWS && cfg.total_flows % 4 == 0);
    assert!(cfg.rpc_bytes >= MIN_WIRE_PAYLOAD && cfg.rpc_bytes <= MAX_WIRE_PAYLOAD && cfg.rpc_bytes % 8 == 0);
    assert!(cfg.bulk_bytes >= MIN_WIRE_PAYLOAD && cfg.bulk_bytes <= MAX_WIRE_PAYLOAD && cfg.bulk_bytes % 8 == 0);
    cfg
}

fn probe_rss_queue(u: *mut UdClient, rx_base: *mut u8, tx_base: *mut u8, tid: usize, target_q: usize) -> (u32, usize) {
    let mut comps = [RxComp::default(); 32];
    let mut repost = [0u32; 32];
    let mut probe_seq = 1u64;
    for attempt in 0..2048u32 {
        let fl = ((tid as u32) * 4096) + attempt + 1;
        unsafe { ud_set_flow(u, fl, ROCE_CARD_QPN) };
        let gseq = ((tid as u64) << 34) | probe_seq;
        probe_seq += 1;
        let p_w0 = pack_meta(gseq, MIN_WIRE_PAYLOAD, 0, PATH_PER_FLOW_CR, OP_PROBE, 0);
        let dst = unsafe { std::slice::from_raw_parts_mut(tx_base, MIN_WIRE_PAYLOAD) };
        dst.fill(0);
        dst[0..8].copy_from_slice(&p_w0.to_le_bytes());
        dst[56..64].copy_from_slice(&p_w0.to_le_bytes());

        let slot = 0u32;
        let len = MIN_WIRE_PAYLOAD as u32;
        let _ = unsafe { ud_post_send_batch(u, &slot, &len, 1, 1) };
        let deadline = Instant::now() + Duration::from_millis(50);
        while Instant::now() < deadline {
            let _ = unsafe { ud_drain_send_cq(u) };
            let n = unsafe { ud_poll_rx(u, comps.as_mut_ptr(), 32) };
            if n > 0 {
                let mut matched_lane = None;
                for i in 0..(n as usize) {
                    repost[i] = comps[i].slot;
                    if comps[i].status == 0 && (comps[i].byte_len as usize) >= GRH_BYTES + MIN_WIRE_PAYLOAD {
                        let ptr = unsafe { rx_base.add((comps[i].slot as usize) * (RX_STRIDE as usize) + GRH_BYTES) };
                        let p = unsafe { std::slice::from_raw_parts(ptr, MIN_WIRE_PAYLOAD) };
                        let w0 = u64::from_le_bytes(p[0..8].try_into().unwrap());
                        let w7 = u64::from_le_bytes(p[56..64].try_into().unwrap());
                        if w0 == w7 {
                            if let Some((rseq, _, _, _, op, _)) = unpack_meta(w0) {
                                if op == OP_PROBE && rseq == gseq {
                                    let lane = u64::from_le_bytes(p[8..16].try_into().unwrap()) as usize;
                                    let ck = u64::from_le_bytes(p[16..24].try_into().unwrap());
                                    if ck == probe_cookie(gseq) {
                                        matched_lane = Some(lane);
                                    }
                                }
                            }
                        }
                    }
                }
                unsafe { ud_repost_rx_batch(u, repost.as_ptr(), n) };
                if let Some(lane) = matched_lane {
                    if lane == target_q {
                        return (fl, lane);
                    }
                    break;
                }
            }
            std::hint::spin_loop();
        }
    }
    panic!("probe_rss_queue failed to match CN106 lane {target_q} for thread {tid}");
}

#[derive(Clone, Copy, Default)]
struct Inflight {
    active: bool,
    measured: bool,
    global_seq: u64,
    flow_id: u16,
    flow_seq: u64,
    pkt_class: u8,
    req_bytes: usize,
    t_sched: u64,
    t_send: u64,
}

struct ThreadResult {
    tid: usize,
    is_rpc: bool,
    sent_measured: u64,
    done_measured: u64,
    rpc_slo_100us_ok: u64,
    rpc_slo_200us_ok: u64,
    max_ack_gap_us: f64,
    req_bytes_measured: u64,
    wire_tx_bytes_measured: u64,
    wire_rx_bytes_measured: u64,
    fast_acks: u64,
    slow_acks: u64,
    leak_flagged_acks: u64,
    phase_paused_acks: u64,
    bad: u64,
    torn: u64,
    ooo: u64,
    send_err: u64,
    dt_s: f64,
    hist_rpc: FastHist,
    hist_bulk: FastHist,
    ts_rpc_acks: Vec<u64>,
    ts_bulk_acks: Vec<u64>,
}

fn main() {
    let cfg = parse_args();
    pin_cpu(cfg.cpu);
    let tsc_hz = calibrate_tsc_hz(cfg.cpu);
    let ticks_to_ns = 1e9 / tsc_hz;

    let total_threads = 8usize; // 4 healthy RPC threads (0..3) + 4 troublemaker bulk threads (4..7)
    let b_probe = Arc::new(Barrier::new(total_threads));
    let b_warmup = Arc::new(Barrier::new(total_threads));
    let b_go = Arc::new(Barrier::new(total_threads));

    let ts_bucket_ticks = ((cfg.ts_bucket_us as f64) * 1e-6 * tsc_hz).round().max(1.0) as u64;
    let burst_period_ticks = ((cfg.burst_period_ms as f64) * 1e-3 * tsc_hz).round().max(1.0) as u64;
    let burst_pkts_per_lane = ((cfg.burst_mib * 1024 * 1024) / (4 * cfg.bulk_bytes)).max(64);

    let mut handles = Vec::with_capacity(total_threads);
    for tid in 0..total_threads {
        let cfg = cfg.clone();
        let b_probe = Arc::clone(&b_probe);
        let b_warmup = Arc::clone(&b_warmup);
        let b_go = Arc::clone(&b_go);

        handles.push(std::thread::spawn(move || {
            pin_cpu(cfg.cpu + tid);
            let is_rpc = tid < 4;
            let lane = tid % 4;
            let flows_per_lane = cfg.total_flows / 4; // 4, 16, 64, 256

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
            let (_fl, _lane) = probe_rss_queue(u, rx_base, tx_base, tid, lane);
            b_probe.wait();

            let mut rng_arr = Rng64::new(cfg.seed ^ ((tid as u64 + 31).wrapping_mul(0xbf58_476d_1ce4_e5b9)));
            let mut inflight = vec![Inflight::default(); INFLIGHT_CAP];
            let mut inflight_cnt: usize = 0;
            let mut send_pending: i32 = 0;
            let mut flow_tx_seq = vec![0u64; MAX_FLOWS];
            let mut flow_rx_exp = vec![0u64; MAX_FLOWS];

            let mut hist_rpc = FastHist::new();
            let mut hist_bulk = FastHist::new();
            let mut ts_rpc_acks: Vec<u64> = Vec::with_capacity(TS_MAX_BUCKETS);
            let mut ts_bulk_acks: Vec<u64> = Vec::with_capacity(TS_MAX_BUCKETS);

            let mut local_seq: u64 = 1;
            let mut sent_measured: u64 = 0;
            let mut done_measured: u64 = 0;
            let mut rpc_slo_100us_ok: u64 = 0;
            let mut rpc_slo_200us_ok: u64 = 0;
            let mut max_ack_gap_ticks: u64 = 0;
            let mut last_ack_tsc: u64 = 0;
            let mut req_bytes_measured: u64 = 0;
            let mut wire_tx_bytes_measured: u64 = 0;
            let mut wire_rx_bytes_measured: u64 = 0;
            let mut fast_acks: u64 = 0;
            let mut slow_acks: u64 = 0;
            let mut leak_flagged_acks: u64 = 0;
            let mut phase_paused_acks: u64 = 0;
            let mut bad: u64 = 0;
            let mut torn: u64 = 0;
            let mut ooo: u64 = 0;
            let mut send_err: u64 = 0;

            let mut comps = [RxComp::default(); 32];
            let mut repost = [0u32; 32];
            let mut tx_slots = [0u32; 16];
            let mut tx_lens = [0u32; 16];
            let mut sched_times = [0u64; 16];

            let mut t_meas_start_tsc = 0u64;
            let mut t_meas_end_tsc = 0u64;

            let rpc_mean_gap_ticks = if is_rpc && cfg.rpc_rate_krps > 0.0 {
                tsc_hz / ((cfg.rpc_rate_krps / 4.0) * 1e3)
            } else {
                0.0
            };
            let bulk_gap_ticks = if !is_rpc && cfg.bulk_send_gbps > 0.0 {
                let per_lane_bps = (cfg.bulk_send_gbps * 1e9) / 4.0;
                let pkt_s = ((cfg.bulk_bytes + ETH_WIRE_OVERHEAD) as f64 * 8.0) / per_lane_bps;
                pkt_s * tsc_hz
            } else {
                0.0
            };

            let max_win = if is_rpc && cfg.rpc_closed_win > 0 {
                cfg.rpc_closed_win.min(INFLIGHT_CAP - 64)
            } else {
                INFLIGHT_CAP - 64
            };

            for phase in 0..2 {
                let measured = phase == 1;
                let phase_ms = if measured { cfg.duration_ms } else { cfg.warmup_ms };
                if measured {
                    b_warmup.wait();
                    if tid == 0 && cfg.sync_reset {
                        println!("WARMUP_DONE");
                        let _ = std::io::stdout().flush();
                        let mut go = String::new();
                        let _ = std::io::stdin().read_line(&mut go);
                    }
                    b_go.wait();
                    if inflight_cnt > 0 {
                        eprintln!("m228c warn: tid={tid} warmup left inflight_cnt={inflight_cnt}");
                        for s in &mut inflight {
                            s.active = false;
                        }
                        inflight_cnt = 0;
                    }
                    flow_rx_exp.fill(0);
                    flow_tx_seq.fill(0);
                    t_meas_start_tsc = rdtsc_after_loads();
                    last_ack_tsc = t_meas_start_tsc;
                }

                let t_phase_start = Instant::now();
                let mut t_next_sched = rdtsc_after_loads() as f64;
                let mut next_burst_tsc = rdtsc_after_loads() + (burst_period_ticks / 4);
                let mut burst_rem_pkts: usize = 0;
                let mut burst_epoch_now: u32 = 0;
                let mut stop_sending = false;
                let mut drain_deadline: Option<Instant> = None;
                let mut rr_healthy_k: usize = 1;

                loop {
                    // Exact SQ CQE draining so TX_SLOTS (2048) never wraps onto an in-flight WQE
                    if send_pending > 0 {
                        let c = unsafe { ud_drain_send_cq(u) };
                        if c > 0 {
                            send_pending = (send_pending - c).max(0);
                        }
                    }

                    // 1. Poll ACKs
                    let n = unsafe { ud_poll_rx(u, comps.as_mut_ptr(), 32) };
                    if n > 0 {
                        let t_now = rdtsc_after_loads();
                        for i in 0..(n as usize) {
                            repost[i] = comps[i].slot;
                            if comps[i].status != 0 || (comps[i].byte_len as usize) < GRH_BYTES + MIN_WIRE_PAYLOAD {
                                if bad < 3 {
                                    eprintln!("m228c bad#1 ph={phase} tid={tid} st={} blen={}", comps[i].status, comps[i].byte_len);
                                }
                                bad += 1;
                                continue;
                            }
                            let ptr = unsafe { rx_base.add((comps[i].slot as usize) * (RX_STRIDE as usize) + GRH_BYTES) };
                            let p = unsafe { std::slice::from_raw_parts(ptr, MIN_WIRE_PAYLOAD) };
                            let w0 = u64::from_le_bytes(p[0..8].try_into().unwrap());
                            let w7 = u64::from_le_bytes(p[56..64].try_into().unwrap());
                            if w0 != w7 {
                                torn += 1;
                                continue;
                            }
                            let Some((rseq, rlen, rcls, rpath, rop, rflags)) = unpack_meta(w0) else {
                                if bad < 3 {
                                    eprintln!("m228c bad#2 ph={phase} tid={tid} w0={w0:#x}");
                                }
                                bad += 1;
                                continue;
                            };
                            if rop != OP_ACK || rlen != MIN_WIRE_PAYLOAD {
                                if bad < 3 {
                                    eprintln!("m228c bad#3 ph={phase} tid={tid} rop={rop} rlen={rlen}");
                                }
                                bad += 1;
                                continue;
                            }
                            let sidx = (rseq as usize) & INFLIGHT_MASK;
                            let slot = &mut inflight[sidx];
                            if !slot.active || slot.global_seq != rseq {
                                if bad < 3 {
                                    eprintln!("m228c bad#4 ph={phase} tid={tid} rseq={rseq:#x} act={} sgseq={:#x}", slot.active, slot.global_seq);
                                }
                                bad += 1;
                                continue;
                            }
                            let echo_tsc = u64::from_le_bytes(p[8..16].try_into().unwrap());
                            let (rfid, rfseq) = unpack_flow_meta(u64::from_le_bytes(p[16..24].try_into().unwrap()));
                            let rck = u64::from_le_bytes(p[40..48].try_into().unwrap());
                            let exp_ck = wire_cookie(rseq, rfid, rfseq, MIN_WIRE_PAYLOAD);
                            if echo_tsc != slot.t_send
                                || rfid != slot.flow_id
                                || rfseq != slot.flow_seq
                                || rcls != slot.pkt_class
                                || rck != exp_ck
                            {
                                if bad < 3 {
                                    eprintln!("m228c bad#5 ph={phase} tid={tid} fid={rfid}/{} fseq={rfseq}/{} cls={rcls}/{}", slot.flow_id, slot.flow_seq, slot.pkt_class);
                                }
                                bad += 1;
                                slot.active = false;
                                inflight_cnt -= 1;
                                continue;
                            }

                            let fidx = (rfid as usize) & (MAX_FLOWS - 1);
                            let exp_f = flow_rx_exp[fidx];
                            if exp_f != 0 && rfseq < exp_f {
                                if ooo < 3 {
                                    eprintln!("m228c ooo ph={phase} tid={tid} fid={rfid} rfseq={rfseq} exp_f={exp_f}");
                                }
                                ooo += 1;
                            }
                            if rfseq + 1 > exp_f {
                                flow_rx_exp[fidx] = rfseq + 1;
                            }

                            let was_meas = slot.measured;
                            let ack_req_bytes = slot.req_bytes;
                            let t_sched = slot.t_sched;
                            slot.active = false;
                            inflight_cnt -= 1;

                            if was_meas {
                                done_measured += 1;
                                req_bytes_measured += ack_req_bytes as u64;
                                wire_rx_bytes_measured += (MIN_WIRE_PAYLOAD + ETH_WIRE_OVERHEAD) as u64;
                                if rpath == PATH_CEIO_SLOW {
                                    slow_acks += 1;
                                } else {
                                    fast_acks += 1;
                                }
                                if (rflags & FLAG_CEIO_LLC_LEAK) != 0 {
                                    leak_flagged_acks += 1;
                                }
                                if (rflags & FLAG_PHASE_PAUSED) != 0 {
                                    phase_paused_acks += 1;
                                }
                                let gap = t_now.saturating_sub(last_ack_tsc);
                                if gap > max_ack_gap_ticks {
                                    max_ack_gap_ticks = gap;
                                }
                                last_ack_tsc = t_now;

                                let rtt_ns = ((t_now.saturating_sub(t_sched)) as f64 * ticks_to_ns) as u64;
                                let bidx = (t_now.saturating_sub(t_meas_start_tsc) / ts_bucket_ticks) as usize;
                                if rcls == CLASS_RPC_HI {
                                    hist_rpc.record_ns(rtt_ns);
                                    if rtt_ns <= 100_000 {
                                        rpc_slo_100us_ok += 1;
                                    }
                                    if rtt_ns <= 200_000 {
                                        rpc_slo_200us_ok += 1;
                                    }
                                    if bidx < TS_MAX_BUCKETS {
                                        if ts_rpc_acks.len() <= bidx {
                                            ts_rpc_acks.resize(bidx + 1, 0);
                                        }
                                        ts_rpc_acks[bidx] += 1;
                                    }
                                } else {
                                    hist_bulk.record_ns(rtt_ns);
                                    if bidx < TS_MAX_BUCKETS {
                                        if ts_bulk_acks.len() <= bidx {
                                            ts_bulk_acks.resize(bidx + 1, 0);
                                        }
                                        ts_bulk_acks[bidx] += 1;
                                    }
                                }
                            }
                        }
                        unsafe { ud_repost_rx_batch(u, repost.as_ptr(), n) };
                    }

                    // 2. Check phase end & clean drain (400 ms > 10x max burst drain; avoids 3 s dead wait on BP=0 drops)
                    if !stop_sending && t_phase_start.elapsed() >= Duration::from_millis(phase_ms) {
                        stop_sending = true;
                        drain_deadline = Some(Instant::now() + Duration::from_millis(400));
                    }
                    if stop_sending {
                        if inflight_cnt == 0 {
                            if measured {
                                t_meas_end_tsc = rdtsc_after_loads();
                            }
                            break;
                        }
                        if let Some(dl) = drain_deadline {
                            if Instant::now() >= dl {
                                if measured {
                                    t_meas_end_tsc = last_ack_tsc.max(t_meas_start_tsc + 1);
                                }
                                break;
                            }
                        }
                        continue;
                    }

                    // 3. Emit packets according to role (Healthy RPC vs Troublemaker Bulk)
                    let mut can_send = max_win.saturating_sub(inflight_cnt).min(16);
                    if can_send == 0 || send_pending >= (TX_SLOTS as i32 - 64) {
                        let c = unsafe { ud_drain_send_cq(u) };
                        if c > 0 {
                            send_pending = (send_pending - c).max(0);
                        }
                        continue;
                    }

                    let now_tsc = rdtsc_after_loads();
                    if is_rpc {
                        if cfg.rpc_closed_win == 0 && rpc_mean_gap_ticks > 0.0 {
                            let mut due = 0usize;
                            while due < can_send && (now_tsc as f64) >= t_next_sched {
                                // Save TRUE scheduled arrival time BEFORE advancing t_next_sched!
                                sched_times[due] = (t_next_sched as u64).min(now_tsc);
                                t_next_sched += rng_arr.next_exp() * rpc_mean_gap_ticks;
                                due += 1;
                            }
                            can_send = due;
                            if can_send == 0 {
                                continue;
                            }
                        } else {
                            for s in sched_times.iter_mut().take(can_send) {
                                *s = now_tsc;
                            }
                        }
                    } else {
                        match cfg.scenario {
                            Scenario::Stall => {
                                if bulk_gap_ticks > 0.0 {
                                    let mut due = 0usize;
                                    while due < can_send && (now_tsc as f64) >= t_next_sched {
                                        sched_times[due] = (t_next_sched as u64).min(now_tsc);
                                        t_next_sched += bulk_gap_ticks;
                                        due += 1;
                                    }
                                    can_send = due;
                                    if can_send == 0 {
                                        continue;
                                    }
                                } else {
                                    for s in sched_times.iter_mut().take(can_send) {
                                        *s = now_tsc;
                                    }
                                }
                            }
                            Scenario::Burst => {
                                if burst_rem_pkts == 0 {
                                    if now_tsc >= next_burst_tsc {
                                        burst_rem_pkts = burst_pkts_per_lane;
                                        burst_epoch_now = burst_epoch_now.wrapping_add(1);
                                        next_burst_tsc = now_tsc.saturating_add(burst_period_ticks);
                                        t_next_sched = now_tsc as f64;
                                    } else {
                                        continue;
                                    }
                                }
                                let lim = can_send.min(burst_rem_pkts);
                                if bulk_gap_ticks > 0.0 {
                                    let mut due = 0usize;
                                    while due < lim && (now_tsc as f64) >= t_next_sched {
                                        sched_times[due] = (t_next_sched as u64).min(now_tsc);
                                        t_next_sched += bulk_gap_ticks;
                                        due += 1;
                                    }
                                    can_send = due;
                                    if can_send == 0 {
                                        continue;
                                    }
                                } else {
                                    can_send = lim;
                                    for s in sched_times.iter_mut().take(can_send) {
                                        *s = now_tsc;
                                    }
                                }
                                burst_rem_pkts -= can_send;
                            }
                        }
                    }

                    let mut n_batch = 0usize;
                    let t_send = rdtsc_after_loads();
                    for b_i in 0..can_send {
                        let gseq = ((tid as u64) << 34) | (local_seq & ((1u64 << 34) - 1));
                        let sidx = (gseq as usize) & INFLIGHT_MASK;
                        if inflight[sidx].active {
                            break;
                        }
                        local_seq += 1;

                        let (fid, plen, pcls) = if is_rpc {
                            let f = (lane + 4 * rr_healthy_k) as u16;
                            rr_healthy_k += 1;
                            if rr_healthy_k >= flows_per_lane {
                                rr_healthy_k = 1;
                            }
                            (f, cfg.rpc_bytes, CLASS_RPC_HI)
                        } else {
                            (lane as u16, cfg.bulk_bytes, CLASS_BULK)
                        };

                        let fidx = (fid as usize) & (MAX_FLOWS - 1);
                        flow_tx_seq[fidx] += 1;
                        let fseq = flow_tx_seq[fidx];

                        let tx_slot = (gseq as u32) & (TX_SLOTS - 1);
                        let buf = unsafe {
                            std::slice::from_raw_parts_mut(tx_base.add((tx_slot as usize) * (TX_STRIDE as usize)), plen)
                        };
                        encode_wire_pkt(buf, gseq, t_send, fid, fseq, 0, burst_epoch_now, pcls, 0);

                        inflight[sidx] = Inflight {
                            active: true,
                            measured,
                            global_seq: gseq,
                            flow_id: fid,
                            flow_seq: fseq,
                            pkt_class: pcls,
                            req_bytes: plen,
                            t_sched: sched_times[b_i],
                            t_send,
                        };
                        inflight_cnt += 1;
                        tx_slots[n_batch] = tx_slot;
                        tx_lens[n_batch] = plen as u32;
                        n_batch += 1;

                        if measured {
                            sent_measured += 1;
                            wire_tx_bytes_measured += (plen + ETH_WIRE_OVERHEAD) as u64;
                        }
                    }

                    if n_batch > 0 {
                        let rc = unsafe {
                            ud_post_send_batch(u, tx_slots.as_ptr(), tx_lens.as_ptr(), n_batch as i32, 0)
                        };
                        if rc != 0 {
                            send_err += n_batch as u64;
                            // Roll back inflight slots so a send error never deadlocks the window
                            for _ in 0..n_batch {
                                local_seq -= 1;
                                let gseq = ((tid as u64) << 34) | (local_seq & ((1u64 << 34) - 1));
                                let sidx = (gseq as usize) & INFLIGHT_MASK;
                                if inflight[sidx].active && inflight[sidx].global_seq == gseq {
                                    inflight[sidx].active = false;
                                    inflight_cnt -= 1;
                                }
                            }
                        } else {
                            send_pending += n_batch as i32;
                        }
                    }
                }
            }

            unsafe { ud_close(u) };
            let dt_s = ((t_meas_end_tsc.saturating_sub(t_meas_start_tsc)) as f64 / tsc_hz).max(1e-9);
            ThreadResult {
                tid,
                is_rpc,
                sent_measured,
                done_measured,
                rpc_slo_100us_ok,
                rpc_slo_200us_ok,
                max_ack_gap_us: (max_ack_gap_ticks as f64) * ticks_to_ns / 1000.0,
                req_bytes_measured,
                wire_tx_bytes_measured,
                wire_rx_bytes_measured,
                fast_acks,
                slow_acks,
                leak_flagged_acks,
                phase_paused_acks,
                bad,
                torn,
                ooo,
                send_err,
                dt_s,
                hist_rpc,
                hist_bulk,
                ts_rpc_acks,
                ts_bulk_acks,
            }
        }));
    }

    let mut results = Vec::with_capacity(total_threads);
    for h in handles {
        results.push(h.join().expect("client worker panicked"));
    }

    let mut sent = 0u64;
    let mut done = 0u64;
    let mut rpc_sent = 0u64;
    let mut rpc_done = 0u64;
    let mut rpc_slo_100us_ok = 0u64;
    let mut rpc_slo_200us_ok = 0u64;
    let mut rpc_max_ack_gap_us = 0.0f64;
    let mut bulk_sent = 0u64;
    let mut bulk_done = 0u64;
    let mut req_bytes = 0u64;
    let mut wire_tx_bytes = 0u64;
    let mut wire_rx_bytes = 0u64;
    let mut fast_acks = 0u64;
    let mut slow_acks = 0u64;
    let mut leak_acks = 0u64;
    let mut pause_acks = 0u64;
    let mut bad = 0u64;
    let mut torn = 0u64;
    let mut ooo = 0u64;
    let mut send_err = 0u64;
    let mut dt_s_max = 0.0f64;
    let mut h_rpc = FastHist::new();
    let mut h_bulk = FastHist::new();
    let mut ts_rpc: Vec<u64> = Vec::new();
    let mut ts_bulk: Vec<u64> = Vec::new();

    for r in &results {
        let _ = r.tid;
        sent += r.sent_measured;
        done += r.done_measured;
        req_bytes += r.req_bytes_measured;
        wire_tx_bytes += r.wire_tx_bytes_measured;
        wire_rx_bytes += r.wire_rx_bytes_measured;
        fast_acks += r.fast_acks;
        slow_acks += r.slow_acks;
        leak_acks += r.leak_flagged_acks;
        pause_acks += r.phase_paused_acks;
        bad += r.bad;
        torn += r.torn;
        ooo += r.ooo;
        send_err += r.send_err;
        dt_s_max = dt_s_max.max(r.dt_s);
        if r.is_rpc {
            rpc_sent += r.sent_measured;
            rpc_done += r.done_measured;
            rpc_slo_100us_ok += r.rpc_slo_100us_ok;
            rpc_slo_200us_ok += r.rpc_slo_200us_ok;
            rpc_max_ack_gap_us = rpc_max_ack_gap_us.max(r.max_ack_gap_us);
            h_rpc.merge_from(&r.hist_rpc);
            if r.ts_rpc_acks.len() > ts_rpc.len() {
                ts_rpc.resize(r.ts_rpc_acks.len(), 0);
            }
            for (i, &c) in r.ts_rpc_acks.iter().enumerate() {
                ts_rpc[i] += c;
            }
        } else {
            bulk_sent += r.sent_measured;
            bulk_done += r.done_measured;
            h_bulk.merge_from(&r.hist_bulk);
            if r.ts_bulk_acks.len() > ts_bulk.len() {
                ts_bulk.resize(r.ts_bulk_acks.len(), 0);
            }
            for (i, &c) in r.ts_bulk_acks.iter().enumerate() {
                ts_bulk[i] += c;
            }
        }
    }

    let lost = sent.saturating_sub(done);
    let rpc_krps = (rpc_done as f64) / dt_s_max / 1e3;
    let rpc_slo100_krps = (rpc_slo_100us_ok as f64) / dt_s_max / 1e3;
    let rpc_slo200_krps = (rpc_slo_200us_ok as f64) / dt_s_max / 1e3;
    let rpc_slo100_pct = if rpc_done > 0 { 100.0 * (rpc_slo_100us_ok as f64) / (rpc_done as f64) } else { 0.0 };
    let bulk_gbps = ((bulk_done as f64) * ((cfg.bulk_bytes + ETH_WIRE_OVERHEAD) as f64) * 8.0) / dt_s_max / 1e9;
    let wire_tx_gbps = ((wire_tx_bytes as f64) * 8.0) / dt_s_max / 1e9;
    let wire_rx_gbps = ((wire_rx_bytes as f64) * 8.0) / dt_s_max / 1e9;
    let goodput_gbps = ((req_bytes as f64) * 8.0) / dt_s_max / 1e9;
    let mpps = (done as f64) / dt_s_max / 1e6;
    let slow_frac = if done > 0 { (slow_acks as f64) / (done as f64) } else { 0.0 };

    // Count how many 500 us buckets had 0 healthy RPC completions (complete HoL blackout windows!)
    let zero_rpc_buckets = ts_rpc.iter().filter(|&&c| c == 0).count();
    let ts_rpc_str: Vec<String> = ts_rpc.iter().take(600).map(|v| v.to_string()).collect();

    println!(
        "MRV228_CLIENT {{\"scenario\":\"{}\",\"total_flows\":{},\"dt_s\":{dt_s_max:.6},\"sent\":{sent},\"done\":{done},\"lost\":{lost},\"rpc_sent\":{rpc_sent},\"rpc_done\":{rpc_done},\"bulk_sent\":{bulk_sent},\"bulk_done\":{bulk_done},\"bad\":{bad},\"torn\":{torn},\"ooo\":{ooo},\"send_err\":{send_err},\"mpps\":{mpps:.4},\"rpc_krps\":{rpc_krps:.3},\"rpc_slo100_krps\":{rpc_slo100_krps:.3},\"rpc_slo200_krps\":{rpc_slo200_krps:.3},\"rpc_slo100_pct\":{rpc_slo100_pct:.2},\"rpc_max_ack_gap_us\":{rpc_max_ack_gap_us:.2},\"zero_rpc_500us_buckets\":{zero_rpc_buckets},\"bulk_gbps\":{bulk_gbps:.4},\"goodput_gbps\":{goodput_gbps:.4},\"wire_tx_gbps\":{wire_tx_gbps:.4},\"wire_rx_gbps\":{wire_rx_gbps:.4},\"fast_acks\":{fast_acks},\"slow_acks\":{slow_acks},\"slow_frac\":{slow_frac:.5},\"leak_flagged_acks\":{leak_acks},\"phase_paused_acks\":{pause_acks},\"rpc_p50_us\":{:.3},\"rpc_p90_us\":{:.3},\"rpc_p95_us\":{:.3},\"rpc_p99_us\":{:.3},\"rpc_p999_us\":{:.3},\"rpc_mean_us\":{:.3},\"rpc_max_us\":{:.3},\"bulk_p50_us\":{:.3},\"bulk_p99_us\":{:.3},\"bulk_max_us\":{:.3},\"ts_bucket_us\":{},\"ts_rpc_acks\":[{}]}}",
        cfg.scenario.as_str(),
        cfg.total_flows,
        h_rpc.pct_us(0.50),
        h_rpc.pct_us(0.90),
        h_rpc.pct_us(0.95),
        h_rpc.pct_us(0.99),
        h_rpc.pct_us(0.999),
        h_rpc.mean_us(),
        h_rpc.max_us(),
        h_bulk.pct_us(0.50),
        h_bulk.pct_us(0.99),
        h_bulk.max_us(),
        cfg.ts_bucket_us,
        ts_rpc_str.join(","),
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wire_codec_matches_mrv228() {
        let mut buf = [0u8; 256];
        encode_wire_pkt(&mut buf, 12345, 777, 1020, 99, 0, 1, CLASS_RPC_HI, 0);
        let w0 = u64::from_le_bytes(buf[0..8].try_into().unwrap());
        let w_end = u64::from_le_bytes(buf[248..256].try_into().unwrap());
        assert_eq!(w0, w_end);
        let (seq, len, cls, path, op, flags) = unpack_meta(w0).unwrap();
        assert_eq!((seq, len, cls, path, op, flags), (12345, 256, CLASS_RPC_HI, PATH_PER_FLOW_CR, OP_DATA, 0));
    }
}
