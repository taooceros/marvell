//! Pure `mercury` ConnectX-5 (`mlx5_2`, `ens6f1np1`, `10.10.100.1`, `cpu 0`)
//! hardware self-loopback benchmark over `IBV_QPT_UD` RoCEv2 (`sgid_index = 3`).
//!
//! Uses the exact same libibverbs fast path (`mrv133_verbs_shim.c`), `IBV_SEND_INLINE`
//! (<= 256 B), 1024 B segmentation (4 KiB = 4 x 1024 B), `RDTSCP` timestamps, and
//! per-packet header/cookie/middle/tail verification as MRV-133 (`mrv133_client.rs`),
//! with `remote_qpn = local_qpn` and `dst_ip = 10.10.100.1` so packets loop back
//! inside the ConnectX-5 NIC hardware without touching CN106 or the 3m DAC.

use std::ffi::CString;
use std::time::{Duration, Instant};

const ROCE_QKEY: u32 = 0x1111_1111;
const OP_NET: u8 = 3;
const MAX_SEG_BYTES: usize = 1024;
const GRH_BYTES: usize = 40;
const RX_SLOTS: u32 = 2048;
const RX_STRIDE: u32 = 2048;
const TX_SLOTS: u32 = 512;
const TX_STRIDE: u32 = 4096;

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
        rx_slots: u32,
        rx_stride: u32,
        tx_slots: u32,
        tx_stride: u32,
    ) -> *mut UdClient;
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

fn pin_cpu(cpu: usize) {
    let mut mask = [0u8; 128];
    mask[cpu / 8] |= 1u8 << (cpu % 8);
    unsafe {
        sched_setaffinity(0, mask.len(), mask.as_ptr());
    }
}

fn calibrate_tsc_hz() -> f64 {
    let t0 = Instant::now();
    let c0 = rdtsc_after_loads();
    std::thread::sleep(Duration::from_millis(60));
    let c1 = rdtsc_after_loads();
    let dt = t0.elapsed().as_secs_f64();
    let hz = (c1 - c0) as f64 / dt;
    // Busy-spin AFTER calibration sleep so powersave CPU ramps to full turbo
    // before warmup and measurement (no cold-start sleep offset).
    let ramp = Instant::now();
    let mut acc = 1u64;
    while ramp.elapsed() < Duration::from_millis(250) {
        acc = acc.wrapping_mul(6364136223846793005).wrapping_add(1);
        std::hint::black_box(acc);
    }
    hz
}

#[inline(always)]
const fn pack_meta(rpc_seq: u64, bytes_div64: u16, frag_idx: u8, frag_cnt: u8, op: u8) -> u64 {
    (rpc_seq & 0x00ff_ffff_ffff)
        | (((bytes_div64 as u64) & 0x3ff) << 40)
        | (((frag_idx as u64) & 0x7) << 50)
        | (((frag_cnt as u64) & 0x7) << 53)
        | ((op as u64) << 56)
}

#[inline(always)]
const fn unpack_meta(meta: u64) -> (u64, u16, u8, u8, u8) {
    let rpc_seq = meta & 0x00ff_ffff_ffff;
    let bytes_div64 = ((meta >> 40) & 0x3ff) as u16;
    let frag_idx = ((meta >> 50) & 0x7) as u8;
    let frag_cnt = ((meta >> 53) & 0x7) as u8;
    let op = (meta >> 56) as u8;
    (rpc_seq, bytes_div64, frag_idx, frag_cnt, op)
}

#[inline(always)]
const fn req_cookie(rpc_seq: u64) -> u64 {
    rpc_seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0x55aa_55aa_0f0f_f0f0
}

#[inline(always)]
const fn seg_sentinel(rpc_seq: u64, frag_idx: u8, line_idx: u64) -> u64 {
    let x = rpc_seq
        .wrapping_mul(0xbf58_476d_1ce4_e5b9)
        .wrapping_add(((frag_idx as u64) << 32) ^ line_idx.wrapping_mul(0x94d0_49bb_1331_11eb));
    (x ^ (x >> 30)) | 1
}

fn percentile(sorted: &[u64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() as f64 - 1.0) * p).round() as usize;
    sorted[idx.min(sorted.len() - 1)] as f64 / 1000.0
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut dev = "mlx5_2".to_string();
    let mut sgid_index: i32 = 3;
    let mut ip: [u8; 4] = [10, 10, 100, 1];
    let mut cpu: usize = 0;
    let mut size: usize = 64;
    let mut warmup: usize = 10_000;
    let mut rounds: usize = 100_000;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--dev" => { i += 1; dev = args[i].clone(); }
            "--sgid" => { i += 1; sgid_index = args[i].parse().unwrap(); }
            "--ip" => {
                i += 1;
                let parts: Vec<u8> = args[i].split('.').map(|s| s.parse().unwrap()).collect();
                ip = [parts[0], parts[1], parts[2], parts[3]];
            }
            "--cpu" => { i += 1; cpu = args[i].parse().unwrap(); }
            "--size" => { i += 1; size = args[i].parse().unwrap(); }
            "--warmup" => { i += 1; warmup = args[i].parse().unwrap(); }
            "--rounds" => { i += 1; rounds = args[i].parse().unwrap(); }
            other => {
                eprintln!("unknown arg: {other}");
                std::process::exit(2);
            }
        }
        i += 1;
    }

    assert!(size >= 64 && size % 64 == 0 && size <= 4096);
    let frag_cnt: u8 = if size <= MAX_SEG_BYTES { 1 } else { (size / MAX_SEG_BYTES) as u8 };
    let seg_len: usize = if frag_cnt == 1 { size } else { MAX_SEG_BYTES };
    let bytes_div64: u16 = (size / 64) as u16;
    let full_mask: u8 = (1u8 << frag_cnt) - 1;

    pin_cpu(cpu);
    let tsc_hz = calibrate_tsc_hz();
    let ticks_to_ns = 1e9 / tsc_hz;

    let dev_c = CString::new(dev.as_str()).unwrap();
    // remote_qpn = 0 -> shim sets remote_qpn = local_qpn (self-loopback on mlx5_2)
    let u = unsafe {
        ud_open(
            dev_c.as_ptr(),
            sgid_index,
            ip.as_ptr(),
            0,
            ROCE_QKEY,
            RX_SLOTS,
            RX_STRIDE,
            TX_SLOTS,
            TX_STRIDE,
        )
    };
    if u.is_null() {
        eprintln!("ud_open failed on {dev} sgid={sgid_index}");
        std::process::exit(1);
    }

    let rx_base = unsafe { ud_rx_buf(u) };
    let tx_base = unsafe { ud_tx_buf(u) };
    let local_qpn = unsafe { ud_local_qpn(u) };
    let max_inline = unsafe { ud_max_inline(u) };

    let mut samples_ns: Vec<u64> = Vec::with_capacity(rounds);
    let mut comps = [RxComp::default(); 32];
    let mut repost_slots = [0u32; 32];
    let mut bad: u64 = 0;
    let mut torn: u64 = 0;
    let mut lost: u64 = 0;

    let total = warmup + rounds;
    let mut t_meas_start = 0u64;

    for iter in 0..total {
        if iter == warmup {
            t_meas_start = rdtsc_after_loads();
        }
        let seq = (iter as u64) + 1;
        let tx_slot = (iter as u32) & (TX_SLOTS - 1);
        let t_send = rdtsc_after_loads();
        let cookie = req_cookie(seq);

        // Fill all segments identically to mrv133_client.rs
        let base = unsafe { tx_base.add((tx_slot as usize) * (TX_STRIDE as usize)) };
        for f in 0..(frag_cnt as usize) {
            let meta = pack_meta(seq, bytes_div64, f as u8, frag_cnt, OP_NET);
            let seg = unsafe { std::slice::from_raw_parts_mut(base.add(f * seg_len), seg_len) };
            seg[0..8].copy_from_slice(&meta.to_le_bytes());
            seg[8..16].copy_from_slice(&t_send.to_le_bytes());
            seg[16..24].copy_from_slice(&cookie.to_le_bytes());
            seg[24..32].copy_from_slice(&(seg_sentinel(seq, f as u8, 0) ^ 3).to_le_bytes());
            if seg_len > 64 {
                let mid = seg_len / 2;
                seg[mid..mid + 8].copy_from_slice(&seg_sentinel(seq, f as u8, (mid / 64) as u64).to_le_bytes());
            }
            seg[56..64].copy_from_slice(&meta.to_le_bytes());
            if seg_len > 64 {
                seg[seg_len - 8..seg_len].copy_from_slice(&meta.to_le_bytes());
            }
        }

        let sl = seg_len as u32;
        let fc = frag_cnt as u32;
        let rc = unsafe { ud_post_send_batch(u, &tx_slot, &sl, &fc, 1, 0) };
        if rc != 0 {
            eprintln!("ud_post_send_batch failed: {rc}");
            std::process::exit(1);
        }

        // Wait for all fragments of this W=1 RPC to arrive back
        let mut rx_mask = 0u8;
        let deadline = Instant::now() + Duration::from_secs(2);
        let mut t_recv = 0u64;
        while rx_mask != full_mask {
            let n = unsafe { ud_poll_rx(u, comps.as_mut_ptr(), 32) };
            if n > 0 {
                let now = rdtsc_after_loads();
                let mut n_repost = 0usize;
                for k in 0..(n as usize) {
                    let c = comps[k];
                    repost_slots[n_repost] = c.slot;
                    n_repost += 1;
                    if c.status != 0 || (c.byte_len as usize) < GRH_BYTES + 64 {
                        bad += 1;
                        continue;
                    }
                    let plen = (c.byte_len as usize) - GRH_BYTES;
                    let p = unsafe {
                        std::slice::from_raw_parts(
                            rx_base.add((c.slot as usize) * (RX_STRIDE as usize) + GRH_BYTES),
                            plen,
                        )
                    };
                    let w0 = u64::from_le_bytes(p[0..8].try_into().unwrap());
                    let w7 = u64::from_le_bytes(p[56..64].try_into().unwrap());
                    if w0 != w7 {
                        torn += 1;
                        continue;
                    }
                    let (rseq, bdiv, fidx, fcnt, _op) = unpack_meta(w0);
                    let echo_tsc = u64::from_le_bytes(p[8..16].try_into().unwrap());
                    let rcookie = u64::from_le_bytes(p[16..24].try_into().unwrap());
                    if rseq != seq
                        || bdiv != bytes_div64
                        || fcnt != frag_cnt
                        || fidx >= frag_cnt
                        || plen != seg_len
                        || echo_tsc != t_send
                        || rcookie != cookie
                    {
                        bad += 1;
                        continue;
                    }
                    if plen > 64 {
                        let mid = u64::from_le_bytes(p[plen / 2..plen / 2 + 8].try_into().unwrap());
                        let tail = u64::from_le_bytes(p[plen - 8..plen].try_into().unwrap());
                        if mid == 0 || tail != w0 {
                            bad += 1;
                            continue;
                        }
                    }
                    rx_mask |= 1u8 << fidx;
                    if rx_mask == full_mask {
                        t_recv = now;
                    }
                }
                unsafe { ud_repost_rx_batch(u, repost_slots.as_ptr(), n_repost as i32) };
            } else if Instant::now() > deadline {
                lost += 1;
                break;
            }
        }
        if (iter & 0xf) == 0 {
            let dr = unsafe { ud_drain_send_cq(u) };
            if dr < 0 {
                bad += 1;
            }
        }
        if iter >= warmup && rx_mask == full_mask {
            let ns = ((t_recv.saturating_sub(t_send)) as f64 * ticks_to_ns) as u64;
            samples_ns.push(ns);
        }
    }
    let t_meas_end = rdtsc_after_loads();
    unsafe { ud_close(u) };

    samples_ns.sort_unstable();
    let meas_s = ((t_meas_end.saturating_sub(t_meas_start)) as f64 * ticks_to_ns) * 1e-9;
    let rate_mps = if meas_s > 0.0 { (samples_ns.len() as f64) / meas_s / 1e6 } else { 0.0 };
    let inv_rate_us = if rate_mps > 0.0 { 1.0 / rate_mps } else { 0.0 };
    let mean_us = if !samples_ns.is_empty() {
        samples_ns.iter().map(|&v| v as f64).sum::<f64>() / (samples_ns.len() as f64) / 1000.0
    } else {
        0.0
    };
    let p50_us = percentile(&samples_ns, 0.50);
    let p99_us = percentile(&samples_ns, 0.99);
    let p999_us = percentile(&samples_ns, 0.999);
    let min_us = samples_ns.first().copied().unwrap_or(0) as f64 / 1000.0;
    let max_us = samples_ns.last().copied().unwrap_or(0) as f64 / 1000.0;
    let verified = bad == 0 && torn == 0 && lost == 0 && samples_ns.len() == rounds;

    println!(
        "{{\"dev\":\"{dev}\",\"sgid\":{sgid_index},\"cpu\":{cpu},\"size\":{size},\"frag_cnt\":{frag_cnt},\"window\":1,\"warmup\":{warmup},\"rounds\":{rounds},\"completed\":{},\"local_qpn\":{local_qpn},\"max_inline\":{max_inline},\"tsc_hz\":{tsc_hz:.1},\"p50_us\":{p50_us:.3},\"p99_us\":{p99_us:.3},\"p999_us\":{p999_us:.3},\"mean_us\":{mean_us:.3},\"min_us\":{min_us:.3},\"max_us\":{max_us:.3},\"rate_mps\":{rate_mps:.4},\"inv_rate_us\":{inv_rate_us:.3},\"bad\":{bad},\"torn\":{torn},\"lost\":{lost},\"verified\":{verified}}}",
        samples_ns.len()
    );
    if !verified {
        std::process::exit(1);
    }
}
