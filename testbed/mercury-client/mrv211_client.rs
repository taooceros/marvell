//! Multi-QP 100GbE non-root hardware-bypass (IBV_QPT_UD RoCEv2) traffic generator
//! & end-to-end verifier running on `mercury` (`mlx5_2`, NUMA 0) for MRV-211 (MRV-203 E3).
//!
//! Zero external crates; builds directly on `mercury` in < 1 s:
//!   gcc -O3 -c mrv211_verbs_shim.c -o mrv211_verbs_shim.o
//!   rustc -O mrv211_client.rs -C link-arg=mrv211_verbs_shim.o -C link-arg=-libverbs -o mrv211_client
//!
//! Supports offline compilation & unit testing on `saturn` (without libibverbs) via:
//!   rustc --test --cfg mock_verbs -O mrv211_client.rs -o /tmp/mrv211_client_test
//!
//! Three MRV-211 (E3) Workloads:
//!   1. `--workload burst-onset` : Microsecond-scale sudden burst onset (idle/low-rate -> 100GbE step),
//!                                 measuring onset RTT, post-burst recovery, per-500us ACK timeseries,
//!                                 and CEIO transient LLC leakage / phase-pause telemetry.
//!   2. `--workload flow-churn`  : 64 -> 16,384 transient flows with 100 us / 500 us / 1 ms active
//!                                 time slots (reproducing CEIO Fig. 12: 16 concurrent active flows,
//!                                 512 B echo), measuring aggregate throughput & slow-path cliff.
//!   3. `--workload mixed-rpc`   : Concurrent latency-sensitive RPC flows (open-loop Poisson, 256 B,
//!                                 CLASS_RPC_HI | FLAG_KV_LOOKUP) + large bulk burst flows (1400 B,
//!                                 CLASS_BULK | FLAG_LAZY_RELEASE), reproducing CEIO Fig. 10a / Table 4.

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
const MAX_FLOWS: usize = 16_384;
const GRH_BYTES: usize = 40;

const OP_DATA: u8 = 1;
const OP_PROBE: u8 = 2;
const OP_ACK: u8 = 3;

const CLASS_BULK: u8 = 0;
const CLASS_RPC_HI: u8 = 1;
const CLASS_CHURN: u8 = 2;
const CLASS_BURST: u8 = 3;

const PATH_UNIFIED: u8 = 0;
const PATH_CEIO_FAST: u8 = 1;
const PATH_CEIO_SLOW: u8 = 2;

const FLAG_CEIO_LLC_LEAK: u8 = 1 << 0;
const FLAG_PHASE_PAUSED: u8 = 1 << 1;
const FLAG_KV_LOOKUP: u8 = 1 << 2;
const FLAG_LAZY_RELEASE: u8 = 1 << 3;

const RX_SLOTS: u32 = 2048;
const RX_STRIDE: u32 = 2048;
const TX_SLOTS: u32 = 2048;
const TX_STRIDE: u32 = 2048;
const INFLIGHT_CAP: usize = 2048;
const INFLIGHT_MASK: usize = INFLIGHT_CAP - 1;
const TS_MAX_BUCKETS: usize = 1024;
const KV_NUM_KEYS: u32 = 65_536;

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
    while ramp.elapsed() < Duration::from_millis(600) {
        for _ in 0..4096 {
            idx = (idx + 8) & (buf.len() - 1);
            let v = unsafe { *buf.get_unchecked(idx) };
            acc = acc.wrapping_mul(6364136223846793005).wrapping_add(v ^ 1);
            unsafe { *buf.get_unchecked_mut(idx) = acc };
        }
        std::hint::black_box(acc);
        if ramp.elapsed() >= Duration::from_millis(250) && read_cpu_khz(cpu) >= 3_200_000 {
            break;
        }
    }
    hz
}

// -----------------------------------------------------------------------------
// Exact wire format shared bit-for-bit with `testbed::mrv211`
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
const fn pack_key_epoch(kv_key: u32, burst_epoch: u32) -> u64 {
    (kv_key as u64) | ((burst_epoch as u64) << 32)
}

#[inline(always)]
const fn wire_cookie(global_seq: u64, flow_id: u16, flow_seq: u64, len_bytes: usize) -> u64 {
    let x = global_seq
        .wrapping_mul(0x9e37_79b9_7f4a_7c15)
        .wrapping_add((flow_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ flow_seq.wrapping_mul(0x94d0_49bb_1331_11eb)
        ^ ((len_bytes as u64) << 40)
        ^ 0x55aa_55aa_0f0f_f0f0;
    (x ^ (x >> 29)) | 1
}

#[inline(always)]
const fn probe_cookie(seq: u64) -> u64 {
    seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0xa5a5_5a5a_1234_5678
}

#[inline(always)]
const fn payload_word(cookie: u64, word_idx: usize) -> u64 {
    cookie.wrapping_add((word_idx as u64).wrapping_mul(0x9e37_79b9_7f4a_7c15)) ^ 0x3c3c_c3c3_5a5a_a5a5
}

fn encode_wire_pkt(
    buf: &mut [u8],
    global_seq: u64,
    client_tsc: u64,
    flow_id: u16,
    flow_seq: u64,
    kv_key: u32,
    burst_epoch: u32,
    pkt_class: u8,
    flags: u8,
) {
    let len = buf.len();
    let w0 = pack_meta(global_seq, len, pkt_class, PATH_UNIFIED, OP_DATA, flags);
    let w2 = pack_flow_meta(flow_id, flow_seq);
    let w3 = pack_key_epoch(kv_key, burst_epoch);
    let cookie = wire_cookie(global_seq, flow_id, flow_seq, len);

    buf[0..8].copy_from_slice(&w0.to_le_bytes());
    buf[8..16].copy_from_slice(&client_tsc.to_le_bytes());
    buf[16..24].copy_from_slice(&w2.to_le_bytes());
    buf[24..32].copy_from_slice(&w3.to_le_bytes());
    buf[32..40].copy_from_slice(&0u64.to_le_bytes());
    buf[40..48].copy_from_slice(&cookie.to_le_bytes());

    let mut off = 48usize;
    let end = len - 8;
    let mut wi = 0usize;
    while off + 8 <= end {
        let val = payload_word(cookie, wi);
        buf[off..off + 8].copy_from_slice(&val.to_le_bytes());
        off += 8;
        wi += 1;
    }
    buf[end..len].copy_from_slice(&w0.to_le_bytes());
}

// -----------------------------------------------------------------------------
// FastHist & Rng64
// -----------------------------------------------------------------------------

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
    fn next_f64(&mut self) -> f64 {
        ((self.next_u64() >> 11) as f64) * (1.0 / ((1u64 << 53) as f64))
    }
    #[inline(always)]
    fn next_exp(&mut self) -> f64 {
        let u = self.next_f64().max(1e-12);
        -u.ln()
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
    min_ns: u64,
    max_ns: u64,
}

impl FastHist {
    fn new() -> Self {
        FastHist { bins: vec![0u64; TOTAL_BINS], count: 0, sum_ns: 0, min_ns: u64::MAX, max_ns: 0 }
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
        for (a, &b) in self.bins.iter_mut().zip(o.bins.iter()) {
            *a += b;
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
        if self.count == 0 { 0.0 } else { (self.sum_ns as f64) / (self.count as f64) / 1000.0 }
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

// -----------------------------------------------------------------------------
// CLI Configuration
// -----------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Workload {
    BurstOnset,
    FlowChurn,
    MixedRpc,
}

impl Workload {
    fn parse(s: &str) -> Self {
        match s {
            "burst-onset" | "burst_onset" => Workload::BurstOnset,
            "flow-churn" | "flow_churn" => Workload::FlowChurn,
            "mixed-rpc" | "mixed_rpc" => Workload::MixedRpc,
            other => panic!("unknown workload {other:?} (expected burst-onset|flow-churn|mixed-rpc)"),
        }
    }
    fn as_str(self) -> &'static str {
        match self {
            Workload::BurstOnset => "burst-onset",
            Workload::FlowChurn => "flow-churn",
            Workload::MixedRpc => "mixed-rpc",
        }
    }
}

#[derive(Clone)]
struct Config {
    dev: String,
    sgid: i32,
    cpu: usize,
    threads: usize,
    card_queues: usize,
    workload: Workload,
    pkt_bytes: usize,
    rpc_bytes: usize,
    bulk_bytes: usize,
    window: usize,
    total_flows: usize,
    active_flows: usize,
    slot_us: u64,
    burst_ms: u64,
    idle_ms: u64,
    idle_rate_kpps: f64,
    rpc_threads: usize,
    rpc_rate_krps: f64,
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
            threads: 8,
            card_queues: 4,
            workload: Workload::BurstOnset,
            pkt_bytes: 512,
            rpc_bytes: 256,
            bulk_bytes: 1400,
            window: 1024,
            total_flows: 64,
            active_flows: 16,
            slot_us: 500,
            burst_ms: 5,
            idle_ms: 20,
            idle_rate_kpps: 50.0,
            rpc_threads: 2,
            rpc_rate_krps: 400.0,
            warmup_ms: 300,
            duration_ms: 1500,
            ts_bucket_us: 500,
            seed: 211,
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
            "--threads" => cfg.threads = next(&mut i).parse().unwrap(),
            "--card-queues" => cfg.card_queues = next(&mut i).parse().unwrap(),
            "--workload" => cfg.workload = Workload::parse(&next(&mut i)),
            "--pkt-bytes" => cfg.pkt_bytes = next(&mut i).parse().unwrap(),
            "--rpc-bytes" => cfg.rpc_bytes = next(&mut i).parse().unwrap(),
            "--bulk-bytes" => cfg.bulk_bytes = next(&mut i).parse().unwrap(),
            "--window" => cfg.window = next(&mut i).parse().unwrap(),
            "--total-flows" => cfg.total_flows = next(&mut i).parse().unwrap(),
            "--active-flows" => cfg.active_flows = next(&mut i).parse().unwrap(),
            "--slot-us" => cfg.slot_us = next(&mut i).parse().unwrap(),
            "--burst-ms" => cfg.burst_ms = next(&mut i).parse().unwrap(),
            "--idle-ms" => cfg.idle_ms = next(&mut i).parse().unwrap(),
            "--idle-rate-kpps" => cfg.idle_rate_kpps = next(&mut i).parse().unwrap(),
            "--rpc-threads" => cfg.rpc_threads = next(&mut i).parse().unwrap(),
            "--rpc-rate-krps" => cfg.rpc_rate_krps = next(&mut i).parse().unwrap(),
            "--warmup-ms" => cfg.warmup_ms = next(&mut i).parse().unwrap(),
            "--duration-ms" => cfg.duration_ms = next(&mut i).parse().unwrap(),
            "--ts-bucket-us" => cfg.ts_bucket_us = next(&mut i).parse().unwrap(),
            "--seed" => cfg.seed = next(&mut i).parse().unwrap(),
            "--sync-reset" => cfg.sync_reset = true,
            "--help" | "-h" => {
                println!(
                    "mrv211_client --workload burst-onset|flow-churn|mixed-rpc [options]\n\
                     --dev mlx5_2 --sgid 3 --cpu 4 --threads 8 --card-queues 4\n\
                     --pkt-bytes 512 --rpc-bytes 256 --bulk-bytes 1400 --window 1024\n\
                     --total-flows 64..16384 --active-flows 16 --slot-us 100|500|1000\n\
                     --burst-ms 5 --idle-ms 20 --idle-rate-kpps 50\n\
                     --rpc-threads 2 --rpc-rate-krps 400 --warmup-ms 300 --duration-ms 1500 --sync-reset"
                );
                std::process::exit(0);
            }
            other => panic!("unknown arg: {other}"),
        }
        i += 1;
    }
    assert!(cfg.threads >= 1 && cfg.threads <= 16);
    assert!(cfg.pkt_bytes >= MIN_WIRE_PAYLOAD && cfg.pkt_bytes <= MAX_WIRE_PAYLOAD && cfg.pkt_bytes % 8 == 0);
    assert!(cfg.rpc_bytes >= MIN_WIRE_PAYLOAD && cfg.rpc_bytes <= MAX_WIRE_PAYLOAD && cfg.rpc_bytes % 8 == 0);
    assert!(cfg.bulk_bytes >= MIN_WIRE_PAYLOAD && cfg.bulk_bytes <= MAX_WIRE_PAYLOAD && cfg.bulk_bytes % 8 == 0);
    assert!(cfg.total_flows >= 1 && cfg.total_flows <= MAX_FLOWS);
    cfg
}

/// Probe `flow_label` values until CN106 replies with `lane == target_q`.
fn probe_rss_queue(u: *mut UdClient, rx_base: *mut u8, tx_base: *mut u8, tid: usize, target_q: usize) -> (u32, usize) {
    let mut comps = [RxComp::default(); 32];
    let mut repost = [0u32; 32];
    let mut probe_seq = 1u64;
    for attempt in 0..2048u32 {
        let fl = ((tid as u32) * 4096) + attempt + 1;
        unsafe { ud_set_flow(u, fl, ROCE_CARD_QPN) };
        let gseq = ((tid as u64) << 34) | probe_seq;
        probe_seq += 1;
        let p_w0 = pack_meta(gseq, MIN_WIRE_PAYLOAD, 0, PATH_UNIFIED, OP_PROBE, 0);
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
    is_onset: bool,
}

struct ThreadResult {
    tid: usize,
    is_rpc: bool,
    sent_measured: u64,
    done_measured: u64,
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
    hist_all: FastHist,
    hist_rpc: FastHist,
    hist_bulk: FastHist,
    hist_onset: FastHist,
    ts_acks: Vec<u64>,
}

/// Deterministic helper selecting the active flow set for thread `tid` within `total_flows`.
fn pick_thread_flows(
    tid: usize,
    threads: usize,
    total_flows: usize,
    k_per_thread: usize,
    rng: &mut Rng64,
    out: &mut Vec<u16>,
) {
    out.clear();
    let count_for_tid = (total_flows + threads - 1 - tid) / threads;
    if count_for_tid == 0 {
        out.push((tid % total_flows) as u16);
        return;
    }
    let want = k_per_thread.max(1).min(count_for_tid);
    for _ in 0..want {
        let local_idx = (rng.next_u64() as usize) % count_for_tid;
        let fid = (tid + local_idx * threads) as u16;
        out.push(fid);
    }
}

fn main() {
    let cfg = parse_args();
    pin_cpu(cfg.cpu);
    let tsc_hz = calibrate_tsc_hz(cfg.cpu);
    let ticks_to_ns = 1e9 / tsc_hz;

    let b_probe = Arc::new(Barrier::new(cfg.threads));
    let b_warmup = Arc::new(Barrier::new(cfg.threads));
    let b_go = Arc::new(Barrier::new(cfg.threads));

    let per_thread_win = (cfg.window / cfg.threads).max(1).min(INFLIGHT_CAP - 1);
    let k_per_thread = (cfg.active_flows / cfg.threads).max(1);
    let slot_ticks = ((cfg.slot_us as f64) * 1e-6 * tsc_hz).round().max(1.0) as u64;
    let burst_ticks = ((cfg.burst_ms as f64) * 1e-3 * tsc_hz).round().max(1.0) as u64;
    let idle_ticks = ((cfg.idle_ms as f64) * 1e-3 * tsc_hz).round() as u64;
    let onset_win_ticks = (500.0e-6 * tsc_hz).round().max(1.0) as u64;
    let ts_bucket_ticks = ((cfg.ts_bucket_us as f64) * 1e-6 * tsc_hz).round().max(1.0) as u64;

    let mut handles = Vec::with_capacity(cfg.threads);
    for tid in 0..cfg.threads {
        let cfg = cfg.clone();
        let b_probe = Arc::clone(&b_probe);
        let b_warmup = Arc::clone(&b_warmup);
        let b_go = Arc::clone(&b_go);

        handles.push(std::thread::spawn(move || {
            pin_cpu(cfg.cpu + tid);
            let is_rpc = cfg.workload == Workload::MixedRpc && tid < cfg.rpc_threads;

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
            let (_fl, _lane) = probe_rss_queue(u, rx_base, tx_base, tid, target_q);
            b_probe.wait();

            let mut rng = Rng64::new(cfg.seed ^ ((tid as u64 + 1).wrapping_mul(0x9e37_79b9_7f4a_7c15)));
            let mut rng_arr = Rng64::new(cfg.seed ^ ((tid as u64 + 31).wrapping_mul(0xbf58_476d_1ce4_e5b9)));

            let mut inflight = [Inflight::default(); INFLIGHT_CAP];
            let mut inflight_cnt: usize = 0;
            let mut flow_tx_seq = vec![0u64; MAX_FLOWS];
            let mut flow_rx_exp = vec![0u64; MAX_FLOWS];
            let mut active_set: Vec<u16> = Vec::with_capacity(k_per_thread + 4);
            pick_thread_flows(tid, cfg.threads, cfg.total_flows, k_per_thread, &mut rng, &mut active_set);

            let mut hist_all = FastHist::new();
            let mut hist_rpc = FastHist::new();
            let mut hist_bulk = FastHist::new();
            let mut hist_onset = FastHist::new();
            let mut ts_acks: Vec<u64> = Vec::new();

            let mut local_seq: u64 = 1;
            let mut sent_measured: u64 = 0;
            let mut done_measured: u64 = 0;
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

            let mut t_meas_start_tsc = 0u64;
            let mut t_meas_end_tsc = 0u64;

            let rpc_mean_gap_ticks = if is_rpc && cfg.rpc_rate_krps > 0.0 {
                tsc_hz / ((cfg.rpc_rate_krps / (cfg.rpc_threads.max(1) as f64)) * 1e3)
            } else {
                0.0
            };
            let idle_gap_ticks = if cfg.idle_rate_kpps > 0.0 {
                tsc_hz / ((cfg.idle_rate_kpps / (cfg.threads as f64)) * 1e3)
            } else {
                0.0
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
                    flow_rx_exp.fill(0);
                    flow_tx_seq.fill(0);
                    t_meas_start_tsc = rdtsc_after_loads();
                }

                let t_phase_start = Instant::now();
                let mut t_slot_last = rdtsc_after_loads();
                let mut t_next_sched = t_slot_last as f64;
                let cycle_ticks = burst_ticks + idle_ticks;
                let mut stop_sending = false;
                let mut drain_deadline: Option<Instant> = None;
                let mut iter = 0u64;
                let mut rr_flow_idx = 0usize;

                loop {
                    iter = iter.wrapping_add(1);
                    if (iter & 0xf) == 0 {
                        let _ = unsafe { ud_drain_send_cq(u) };
                    }

                    // 1. Poll ACKs
                    let n = unsafe { ud_poll_rx(u, comps.as_mut_ptr(), 32) };
                    if n > 0 {
                        let t_now = rdtsc_after_loads();
                        for i in 0..(n as usize) {
                            repost[i] = comps[i].slot;
                            if comps[i].status != 0 || (comps[i].byte_len as usize) < GRH_BYTES + MIN_WIRE_PAYLOAD {
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
                                bad += 1;
                                continue;
                            };
                            if rop != OP_ACK || rlen != MIN_WIRE_PAYLOAD {
                                bad += 1;
                                continue;
                            }
                            let sidx = (rseq as usize) & INFLIGHT_MASK;
                            let slot = &mut inflight[sidx];
                            if !slot.active || slot.global_seq != rseq {
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
                                bad += 1;
                                slot.active = false;
                                inflight_cnt -= 1;
                                continue;
                            }

                            // Per-flow strict ordering check across all 16,384 flows
                            let fidx = (rfid as usize) & (MAX_FLOWS - 1);
                            let exp_f = flow_rx_exp[fidx];
                            if exp_f != 0 && rfseq < exp_f {
                                ooo += 1;
                            }
                            if rfseq + 1 > exp_f {
                                flow_rx_exp[fidx] = rfseq + 1;
                            }

                            let was_meas = slot.measured;
                            let was_onset = slot.is_onset;
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
                                } else if rpath == PATH_CEIO_FAST || rpath == PATH_UNIFIED {
                                    fast_acks += 1;
                                } else {
                                    fast_acks += 1;
                                }
                                if (rflags & FLAG_CEIO_LLC_LEAK) != 0 {
                                    leak_flagged_acks += 1;
                                }
                                if (rflags & FLAG_PHASE_PAUSED) != 0 {
                                    phase_paused_acks += 1;
                                }
                                let rtt_ns = ((t_now.saturating_sub(t_sched)) as f64 * ticks_to_ns) as u64;
                                hist_all.record_ns(rtt_ns);
                                if rcls == CLASS_RPC_HI {
                                    hist_rpc.record_ns(rtt_ns);
                                } else {
                                    hist_bulk.record_ns(rtt_ns);
                                }
                                if was_onset {
                                    hist_onset.record_ns(rtt_ns);
                                }
                                let bidx = (t_now.saturating_sub(t_meas_start_tsc) / ts_bucket_ticks) as usize;
                                if bidx < TS_MAX_BUCKETS {
                                    if ts_acks.len() <= bidx {
                                        ts_acks.resize(bidx + 1, 0);
                                    }
                                    ts_acks[bidx] += 1;
                                }
                            }
                        }
                        unsafe { ud_repost_rx_batch(u, repost.as_ptr(), n) };
                    }

                    // 2. Check phase end / drain completion
                    if !stop_sending && t_phase_start.elapsed() >= Duration::from_millis(phase_ms) {
                        stop_sending = true;
                        drain_deadline = Some(Instant::now() + Duration::from_secs(2));
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
                                    t_meas_end_tsc = rdtsc_after_loads();
                                } else if inflight_cnt > 0 {
                                    eprintln!("WARN: tid {tid} warmup drain timed out with {inflight_cnt} inflight");
                                }
                                break;
                            }
                        }
                        continue;
                    }

                    // 3. Workload-specific flow rotation & packet emission
                    let now_tsc = rdtsc_after_loads();
                    if cfg.workload == Workload::FlowChurn && now_tsc.saturating_sub(t_slot_last) >= slot_ticks {
                        t_slot_last = now_tsc;
                        pick_thread_flows(tid, cfg.threads, cfg.total_flows, k_per_thread, &mut rng, &mut active_set);
                    }

                    let mut can_send = per_thread_win.saturating_sub(inflight_cnt).min(16);
                    if can_send == 0 {
                        continue;
                    }

                    let mut is_onset_now = false;
                    let mut burst_epoch_now = 0u32;
                    if cfg.workload == Workload::BurstOnset && cycle_ticks > 0 {
                        let rel = now_tsc.saturating_sub(if measured { t_meas_start_tsc } else { t_slot_last });
                        burst_epoch_now = (rel / cycle_ticks) as u32;
                        let pos = rel % cycle_ticks;
                        if pos < idle_ticks {
                            // Quiet/low-rate phase before sudden burst onset
                            if idle_gap_ticks <= 0.0 || (now_tsc as f64) < t_next_sched {
                                continue;
                            }
                            can_send = 1;
                            t_next_sched = (now_tsc as f64) + idle_gap_ticks;
                        } else {
                            // Sudden full-rate burst phase!
                            let in_burst = pos - idle_ticks;
                            is_onset_now = in_burst < onset_win_ticks;
                        }
                    } else if is_rpc && rpc_mean_gap_ticks > 0.0 {
                        // Open-loop Poisson RPC schedule (zero coordinated omission)
                        let mut due = 0usize;
                        while due < can_send && (now_tsc as f64) >= t_next_sched {
                            due += 1;
                            t_next_sched += rng_arr.next_exp() * rpc_mean_gap_ticks;
                        }
                        can_send = due;
                        if can_send == 0 {
                            continue;
                        }
                    }

                    let mut n_batch = 0usize;
                    let t_send = rdtsc_after_loads();
                    for _ in 0..can_send {
                        let gseq = ((tid as u64) << 34) | (local_seq & ((1u64 << 34) - 1));
                        let sidx = (gseq as usize) & INFLIGHT_MASK;
                        if inflight[sidx].active {
                            break;
                        }
                        local_seq += 1;

                        let (fid, plen, pcls, pflags, kv_key) = match cfg.workload {
                            Workload::BurstOnset => {
                                let fid = active_set[rr_flow_idx % active_set.len()];
                                rr_flow_idx = rr_flow_idx.wrapping_add(1);
                                (fid, cfg.pkt_bytes, CLASS_BURST, 0u8, 0u32)
                            }
                            Workload::FlowChurn => {
                                let fid = active_set[rr_flow_idx % active_set.len()];
                                rr_flow_idx = rr_flow_idx.wrapping_add(1);
                                (fid, cfg.pkt_bytes, CLASS_CHURN, 0u8, 0u32)
                            }
                            Workload::MixedRpc => {
                                let fid = active_set[rr_flow_idx % active_set.len()];
                                rr_flow_idx = rr_flow_idx.wrapping_add(1);
                                if is_rpc {
                                    let k = (rng.next_u64() as u32) % KV_NUM_KEYS;
                                    (fid, cfg.rpc_bytes, CLASS_RPC_HI, FLAG_KV_LOOKUP, k)
                                } else {
                                    (fid, cfg.bulk_bytes, CLASS_BULK, FLAG_LAZY_RELEASE, 0u32)
                                }
                            }
                        };

                        let fidx = (fid as usize) & (MAX_FLOWS - 1);
                        flow_tx_seq[fidx] += 1;
                        let fseq = flow_tx_seq[fidx];

                        let tx_slot = (sidx as u32) & (TX_SLOTS - 1);
                        let buf = unsafe {
                            std::slice::from_raw_parts_mut(tx_base.add((tx_slot as usize) * (TX_STRIDE as usize)), plen)
                        };
                        encode_wire_pkt(buf, gseq, t_send, fid, fseq, kv_key, burst_epoch_now, pcls, pflags);

                        inflight[sidx] = Inflight {
                            active: true,
                            measured,
                            global_seq: gseq,
                            flow_id: fid,
                            flow_seq: fseq,
                            pkt_class: pcls,
                            req_bytes: plen,
                            t_sched: if is_rpc && rpc_mean_gap_ticks > 0.0 {
                                (t_next_sched as u64).min(t_send)
                            } else {
                                t_send
                            },
                            t_send,
                            is_onset: is_onset_now,
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
                hist_all,
                hist_rpc,
                hist_bulk,
                hist_onset,
                ts_acks,
            }
        }));
    }

    let mut results = Vec::with_capacity(cfg.threads);
    for h in handles {
        results.push(h.join().expect("client worker panicked"));
    }

    let mut sent = 0u64;
    let mut done = 0u64;
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
    let mut rpc_done = 0u64;
    let mut bulk_done = 0u64;
    let mut bulk_wire_bytes = 0u64;
    let mut dt_s_max = 0.0f64;
    let mut h_all = FastHist::new();
    let mut h_rpc = FastHist::new();
    let mut h_bulk = FastHist::new();
    let mut h_onset = FastHist::new();
    let mut ts_sum: Vec<u64> = Vec::new();

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
        if r.is_rpc {
            rpc_done += r.done_measured;
        } else {
            bulk_done += r.done_measured;
            bulk_wire_bytes += r.wire_tx_bytes_measured;
        }
        dt_s_max = dt_s_max.max(r.dt_s);
        h_all.merge_from(&r.hist_all);
        h_rpc.merge_from(&r.hist_rpc);
        h_bulk.merge_from(&r.hist_bulk);
        h_onset.merge_from(&r.hist_onset);
        if ts_sum.len() < r.ts_acks.len() {
            ts_sum.resize(r.ts_acks.len(), 0);
        }
        for (i, &c) in r.ts_acks.iter().enumerate() {
            ts_sum[i] += c;
        }
    }

    let dt = dt_s_max.max(1e-9);
    let lost = sent.saturating_sub(done);
    let mpps = (done as f64) / dt / 1e6;
    let tx_gbps_wire = (wire_tx_bytes as f64 * 8.0) / dt / 1e9;
    let tx_gbps_pay = (req_bytes as f64 * 8.0) / dt / 1e9;
    let rx_gbps_wire = (wire_rx_bytes as f64 * 8.0) / dt / 1e9;
    let rpc_krps = (rpc_done as f64) / dt / 1e3;
    let bulk_gbps_wire = (bulk_wire_bytes as f64 * 8.0) / dt / 1e9;
    let slow_frac_pct = if fast_acks + slow_acks > 0 {
        100.0 * (slow_acks as f64) / ((fast_acks + slow_acks) as f64)
    } else {
        0.0
    };
    let ts_str: Vec<String> = ts_sum.iter().map(|v| v.to_string()).collect();

    println!(
        "{{\"workload\":\"{}\",\"threads\":{},\"total_flows\":{},\"active_flows\":{},\"slot_us\":{},\"pkt_bytes\":{},\"dt_s\":{dt:.6},\"sent\":{sent},\"done\":{done},\"lost\":{lost},\"bad\":{bad},\"torn\":{torn},\"ooo\":{ooo},\"send_err\":{send_err},\"mpps\":{mpps:.4},\"tx_gbps_wire\":{tx_gbps_wire:.3},\"tx_gbps_pay\":{tx_gbps_pay:.3},\"rx_gbps_wire\":{rx_gbps_wire:.3},\"fast_acks\":{fast_acks},\"slow_acks\":{slow_acks},\"slow_frac_pct\":{slow_frac_pct:.3},\"leak_flagged_acks\":{leak_acks},\"phase_paused_acks\":{pause_acks},\"p50_us\":{:.3},\"p99_us\":{:.3},\"p999_us\":{:.3},\"max_us\":{:.3},\"mean_us\":{:.3},\"onset_cnt\":{},\"onset_p50_us\":{:.3},\"onset_p99_us\":{:.3},\"onset_p999_us\":{:.3},\"rpc_done\":{rpc_done},\"rpc_krps\":{rpc_krps:.3},\"rpc_p50_us\":{:.3},\"rpc_p99_us\":{:.3},\"rpc_p999_us\":{:.3},\"rpc_max_us\":{:.3},\"bulk_done\":{bulk_done},\"bulk_gbps_wire\":{bulk_gbps_wire:.3},\"bulk_p50_us\":{:.3},\"bulk_p99_us\":{:.3},\"ts_bucket_us\":{},\"ts_acks\":[{}]}}",
        cfg.workload.as_str(),
        cfg.threads,
        cfg.total_flows,
        cfg.active_flows,
        cfg.slot_us,
        cfg.pkt_bytes,
        h_all.pct_us(0.50),
        h_all.pct_us(0.99),
        h_all.pct_us(0.999),
        h_all.max_us(),
        h_all.mean_us(),
        h_onset.count,
        h_onset.pct_us(0.50),
        h_onset.pct_us(0.99),
        h_onset.pct_us(0.999),
        h_rpc.pct_us(0.50),
        h_rpc.pct_us(0.99),
        h_rpc.pct_us(0.999),
        h_rpc.max_us(),
        h_bulk.pct_us(0.50),
        h_bulk.pct_us(0.99),
        cfg.ts_bucket_us,
        ts_str.join(",")
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn client_wire_encoding_matches_protocol() {
        let mut buf = vec![0u8; 512];
        encode_wire_pkt(&mut buf, 0x2_0000_0007, 123456, 3001, 99, 42, 3, CLASS_CHURN, FLAG_LAZY_RELEASE);
        let w0 = u64::from_le_bytes(buf[0..8].try_into().unwrap());
        let w_end = u64::from_le_bytes(buf[504..512].try_into().unwrap());
        assert_eq!(w0, w_end);
        let (seq, len, cls, path, op, flags) = unpack_meta(w0).unwrap();
        assert_eq!(seq, 0x2_0000_0007);
        assert_eq!(len, 512);
        assert_eq!(cls, CLASS_CHURN);
        assert_eq!(path, PATH_UNIFIED);
        assert_eq!(op, OP_DATA);
        assert_eq!(flags, FLAG_LAZY_RELEASE);
        let (fid, fseq) = unpack_flow_meta(u64::from_le_bytes(buf[16..24].try_into().unwrap()));
        assert_eq!(fid, 3001);
        assert_eq!(fseq, 99);
        let ck = u64::from_le_bytes(buf[40..48].try_into().unwrap());
        assert_eq!(ck, wire_cookie(seq, fid, fseq, len));
        assert_eq!(
            u64::from_le_bytes(buf[48..56].try_into().unwrap()),
            payload_word(ck, 0)
        );
    }

    #[test]
    fn disjoint_thread_flow_partition_up_to_16384() {
        let threads = 8usize;
        let total = 16_384usize;
        let mut rng = Rng64::new(42);
        let mut out = Vec::new();
        for tid in 0..threads {
            pick_thread_flows(tid, threads, total, 4, &mut rng, &mut out);
            assert_eq!(out.len(), 4);
            for &fid in &out {
                assert!((fid as usize) < total);
                assert_eq!((fid as usize) % threads, tid);
            }
        }
    }
}
