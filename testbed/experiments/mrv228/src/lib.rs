//! Shared wire protocol, packed BAR4 per-flow fence-free MOVDIR64B credit ring
//! (16 flows/64 B cache line -> 1,024 flows in 4 KiB BAR4), card-side per-flow
//! credit-gated scheduler (`PerFlowCreditScheduler`), and fortified CEIO dual-path
//! baseline (`CeioFlowController` + `SwMergeRing2P1C`) for MRV-228.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};

// -----------------------------------------------------------------------------
// Network & SDP constants (aligned with proven CN106 + CX-5 100GbE topology)
// -----------------------------------------------------------------------------

pub const ROCE_UDP_PORT: u16 = 4791;
pub const ROCE_QKEY: u32 = 0x1111_1111;
pub const ROCE_CARD_QPN: u32 = 0x0001_2345;
pub const ROCE_HDR_LEN: usize = 62;
pub const ROCE_ICRC_LEN: usize = 4;

/// 48 B aligned Eth(14) + IPv4(20) + UDP(8) + Pad(6) header over `sdp1-1` <-> VF0.
/// Matches `octeon_ep` devarg `rx_poll_mem=1,rx_poll_tail_off=48`.
pub const SDP_HDR_LEN: usize = 48;
pub const SDP_UDP_DST_PORT: u16 = 4228;
pub const SDP_MTU: u16 = 1500;

/// Single-frame payload bounds (64 B..=1408 B, 8 B multiple; fits 1500 B MTU).
pub const MIN_WIRE_PAYLOAD: usize = 64;
pub const MAX_WIRE_PAYLOAD: usize = 1408;
pub const ETH_MBUF_BUF_SIZE: u16 = 2176;
pub const SDP_MBUF_BUF_SIZE: u16 = 2176;
pub const ACK_MBUF_BUF_SIZE: u16 = 512;

pub const MAX_CARD_CORES: usize = 4;
pub const MAX_FLOWS: usize = 1024;
pub const MAX_FLOWS_PER_LANE: usize = MAX_FLOWS / MAX_CARD_CORES; // 256
pub const MAX_DRAIN: usize = 32;

/// Default CN106 & saturn control TCP endpoints.
pub const CARD_SYNC_ADDR: &str = "0.0.0.0:7561";
pub const HOST_CTRL_ADDR: &str = "127.0.0.1:7563";

/// Standard DPDK `octeon_ep` refill threshold (`otx_ep_rxtx.h:24`).
pub const DROQ_REFILL_THRESHOLD: u16 = 64;

/// Tight hot-ring defaults shared IDENTICALLY by `bp1-stop` and `per-flow-cr`:
///   nb_desc = 256 (with allow_small_oq=1) => WMARK = 64 => usable HW headroom = 192.
///   r_eff_q = 128 => 128 + (64 - 1) = 191 < 192 => HW NEVER hits WMARK (Pitfall 1 safe)!
///   r_flow  = 32  => 1 stalled/elephant flow uses <= 25% of HW queue, leaving 96 slots open!
pub const DEFAULT_SMALL_RX_DESC: u16 = 256;
pub const DEFAULT_R_EFF_PER_Q: u64 = 128;
pub const DEFAULT_R_FLOW: u32 = 32;
pub const DEFAULT_PRIO_RESERVE: u64 = 32;
pub const DEFAULT_CREDIT_BATCH: u64 = 16;

/// Default CEIO parameters (faithful MRV-211 fortified baseline).
pub const DEFAULT_CEIO_FAST_RX_DESC: u16 = 2048;
pub const DEFAULT_CEIO_SLOW_RX_DESC: u16 = 512;
pub const DEFAULT_CEIO_C_TOTAL: u64 = 1024;
pub const DEFAULT_CEIO_LAZY_BATCH: u64 = 64;
pub const DEFAULT_CEIO_PULL_WINDOW: u32 = 128;

// -----------------------------------------------------------------------------
// Wire header encoding (64 B..=1408 B, head_tag == tail_tag for tear checks)
// -----------------------------------------------------------------------------

pub const SEQ_MASK: u64 = (1u64 << 38) - 1;

pub const OP_DATA: u8 = 1;
pub const OP_PROBE: u8 = 2;
pub const OP_ACK: u8 = 3;

pub const CLASS_BULK: u8 = 0;
pub const CLASS_RPC_HI: u8 = 1;

pub const PATH_PER_FLOW_CR: u8 = 0;
pub const PATH_CEIO_FAST: u8 = 1;
pub const PATH_CEIO_SLOW: u8 = 2;
pub const PATH_DIRECT_SDP: u8 = 3;

pub const FLAG_CEIO_LLC_LEAK: u8 = 1 << 0;
pub const FLAG_PHASE_PAUSED: u8 = 1 << 1;
pub const FLAG_SLOW_HINT: u8 = 1 << 2;
pub const FLAG_STALL_FLOW: u8 = 1 << 3;

#[inline(always)]
pub const fn pack_meta(
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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WireMeta {
    pub global_seq: u64,
    pub len_bytes: usize,
    pub pkt_class: u8,
    pub path_tag: u8,
    pub op: u8,
    pub flags: u8,
}

#[inline(always)]
pub const fn unpack_meta(w0: u64) -> Option<WireMeta> {
    if (w0 >> 63) == 0 {
        return None;
    }
    let global_seq = w0 & SEQ_MASK;
    let len_bytes = (((w0 >> 38) & 0x3ff) as usize) << 3;
    let pkt_class = ((w0 >> 48) & 0x3) as u8;
    let path_tag = ((w0 >> 50) & 0x3) as u8;
    let op = ((w0 >> 52) & 0xf) as u8;
    let flags = ((w0 >> 56) & 0x7f) as u8;
    Some(WireMeta { global_seq, len_bytes, pkt_class, path_tag, op, flags })
}

#[inline(always)]
pub const fn pack_flow_meta(flow_id: u16, flow_seq: u64) -> u64 {
    (flow_id as u64) | ((flow_seq & ((1u64 << 48) - 1)) << 16)
}

#[inline(always)]
pub const fn unpack_flow_meta(w2: u64) -> (u16, u64) {
    ((w2 & 0xffff) as u16, w2 >> 16)
}

#[inline(always)]
pub const fn pack_aux_epoch(aux_key: u32, burst_epoch: u32) -> u64 {
    (aux_key as u64) | ((burst_epoch as u64) << 32)
}

#[inline(always)]
pub const fn unpack_aux_epoch(w3: u64) -> (u32, u32) {
    (w3 as u32, (w3 >> 32) as u32)
}

#[inline(always)]
pub const fn wire_cookie(global_seq: u64, flow_id: u16, flow_seq: u64, len_bytes: usize) -> u64 {
    global_seq.wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ ((flow_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ (flow_seq.wrapping_mul(0x94d0_49bb_1331_11eb))
        ^ ((len_bytes as u64).wrapping_mul(0x6c62_272e_07bb_0142))
        ^ 0x2280_cafe_babe_55aa
}

#[inline(always)]
pub const fn probe_cookie(seq: u64) -> u64 {
    seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0xa5a5_5a5a_2280_5678
}

#[inline(always)]
pub const fn splitmix64(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ (x >> 31)
}

#[inline(always)]
pub const fn payload_word(cookie: u64, word_idx: usize) -> u64 {
    cookie.wrapping_add((word_idx as u64).wrapping_mul(0x9e37_79b9_7f4a_7c15)) ^ 0x3c3c_c3c3_5a5a_a5a5
}

pub fn encode_wire_pkt(
    buf: &mut [u8],
    global_seq: u64,
    client_tsc: u64,
    flow_id: u16,
    flow_seq: u64,
    aux_key: u32,
    burst_epoch: u32,
    pkt_class: u8,
    path_tag: u8,
    op: u8,
    flags: u8,
) {
    let len = buf.len();
    debug_assert!(len >= MIN_WIRE_PAYLOAD && len <= MAX_WIRE_PAYLOAD && (len & 7) == 0);
    let w0 = pack_meta(global_seq, len, pkt_class, path_tag, op, flags);
    let w2 = pack_flow_meta(flow_id, flow_seq);
    let w3 = pack_aux_epoch(aux_key, burst_epoch);
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

#[inline(always)]
pub fn stamp_card_meta(payload: &mut [u8], path_tag: u8, add_flags: u8, card_rx_tsc: u64) {
    let len = payload.len();
    if len < MIN_WIRE_PAYLOAD {
        return;
    }
    let w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
    let cleared = w0 & !(0x3u64 << 50);
    let new_w0 = cleared | (((path_tag as u64) & 0x3) << 50) | (((add_flags as u64) & 0x7f) << 56);
    payload[0..8].copy_from_slice(&new_w0.to_le_bytes());
    payload[32..40].copy_from_slice(&card_rx_tsc.to_le_bytes());
    payload[len - 8..len].copy_from_slice(&new_w0.to_le_bytes());
}

/// Verify packet framing and touch every 64 B cache line of the payload on the host.
/// Returns `Ok((meta, client_tsc, flow_id, flow_seq, aux_key, burst_epoch, card_rx_tsc, checksum_xor))`
/// or `Err(is_torn)`.
#[inline(always)]
pub fn verify_and_touch_wire_pkt(
    payload: &[u8],
    touch_all_lines: bool,
) -> Result<(WireMeta, u64, u16, u64, u32, u32, u64, u64), bool> {
    let len = payload.len();
    if len < MIN_WIRE_PAYLOAD || len > MAX_WIRE_PAYLOAD || (len & 7) != 0 {
        return Err(false);
    }
    let w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
    let w_end = u64::from_le_bytes(payload[len - 8..len].try_into().unwrap());
    if w0 != w_end {
        return Err(true);
    }
    let Some(meta) = unpack_meta(w0) else {
        return Err(false);
    };
    if meta.len_bytes != len {
        return Err(false);
    }
    let client_tsc = u64::from_le_bytes(payload[8..16].try_into().unwrap());
    let (flow_id, flow_seq) = unpack_flow_meta(u64::from_le_bytes(payload[16..24].try_into().unwrap()));
    let (aux_key, burst_epoch) = unpack_aux_epoch(u64::from_le_bytes(payload[24..32].try_into().unwrap()));
    let card_rx_tsc = u64::from_le_bytes(payload[32..40].try_into().unwrap());
    let ck = u64::from_le_bytes(payload[40..48].try_into().unwrap());
    let expected_ck = wire_cookie(meta.global_seq, flow_id, flow_seq, len);
    if ck != expected_ck {
        return Err(false);
    }
    let mut acc = ck;
    if len > MIN_WIRE_PAYLOAD {
        let mid_idx = ((len - 56) / 16) as usize;
        let mid_off = 48 + mid_idx * 8;
        let mid_w = u64::from_le_bytes(payload[mid_off..mid_off + 8].try_into().unwrap());
        if mid_w != payload_word(ck, mid_idx) {
            return Err(false);
        }
        acc ^= mid_w;
        if touch_all_lines {
            let mut off = 64usize;
            while off + 8 <= len - 8 {
                let w = u64::from_le_bytes(payload[off..off + 8].try_into().unwrap());
                acc ^= w;
                off += 64;
            }
        }
    }
    Ok((meta, client_tsc, flow_id, flow_seq, aux_key, burst_epoch, card_rx_tsc, acc))
}

#[inline(always)]
pub fn encode_wire_ack64(
    dst: &mut [u8],
    global_seq: u64,
    client_tsc: u64,
    flow_id: u16,
    flow_seq: u64,
    aux_key: u32,
    burst_epoch: u32,
    pkt_class: u8,
    path_tag: u8,
    flags: u8,
    host_aux: u64,
) {
    debug_assert!(dst.len() >= MIN_WIRE_PAYLOAD);
    let w0 = pack_meta(global_seq, MIN_WIRE_PAYLOAD, pkt_class, path_tag, OP_ACK, flags);
    let w2 = pack_flow_meta(flow_id, flow_seq);
    let w3 = pack_aux_epoch(aux_key, burst_epoch);
    let ck = wire_cookie(global_seq, flow_id, flow_seq, MIN_WIRE_PAYLOAD);
    dst[0..8].copy_from_slice(&w0.to_le_bytes());
    dst[8..16].copy_from_slice(&client_tsc.to_le_bytes());
    dst[16..24].copy_from_slice(&w2.to_le_bytes());
    dst[24..32].copy_from_slice(&w3.to_le_bytes());
    dst[32..40].copy_from_slice(&host_aux.to_le_bytes());
    dst[40..48].copy_from_slice(&ck.to_le_bytes());
    dst[48..56].copy_from_slice(&payload_word(ck, 0).to_le_bytes());
    dst[56..64].copy_from_slice(&w0.to_le_bytes());
}

// -----------------------------------------------------------------------------
// BAR4 Slot 15 Per-Flow Fence-Free MOVDIR64B Credit Layout (under testbed::lease)
//
//   0x0000..0x0040 (64 B):  HostQueueCreditLine (aggregate taken_q[0..4] + epoch)
//   0x0040..0x0080 (64 B):  CeioSlowPullLine    (only for Arm 4 CEIO slow pull)
//   0x0100..0x0140 (64 B):  Card metadata
//   0x0200..0x1200 (4 KiB): 64 x FlowCreditLine = 1,024 per-flow u32 credits
//                           (16 flows packed into every 64 B MOVDIR64B cache line!)
// -----------------------------------------------------------------------------

pub const BAR4_Q_CREDIT_OFF: usize = 0x0000;
pub const BAR4_CEIO_PULL_OFF: usize = 0x0040;
pub const BAR4_CARD_META_OFF: usize = 0x0100;
pub const BAR4_FLOW_CREDIT_BASE: usize = 0x0200;

pub const FLOWS_PER_CREDIT_LINE: usize = 16;
pub const FLOW_CREDIT_GROUPS: usize = MAX_FLOWS / FLOWS_PER_CREDIT_LINE; // 64 lines
pub const FLOW_CREDIT_BYTES: usize = FLOW_CREDIT_GROUPS * 64; // 4,096 B = 4 KiB

pub const CREDIT_MAGIC: u64 = 0xC228_0001;
pub const PULL_MAGIC_16: u64 = 0xC229;

/// Single 64 B BAR4 cache line packing 16 flows' monotonic `u32` consumed counters.
/// Written atomically with ONE fence-free `MOVDIR64B` instruction from `saturn` `cpu40`.
/// 1,024 flows occupy 64 lines = exactly 4 KiB of BAR4 Slot 15!
#[repr(C, align(64))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct FlowCreditLine {
    pub taken: [u32; FLOWS_PER_CREDIT_LINE],
}

const _: () = assert!(size_of::<FlowCreditLine>() == 64 && align_of::<FlowCreditLine>() == 64);
const _: () = assert!(FLOW_CREDIT_GROUPS == 64 && FLOW_CREDIT_BYTES == 4096);

#[inline(always)]
pub const fn flow_credit_line_off(group: usize) -> usize {
    BAR4_FLOW_CREDIT_BASE + group * 64
}

#[inline(always)]
pub unsafe fn read_bar4_flow_taken(bar4_base: *const u8, fid: u16) -> u32 {
    let idx = (fid as usize) & (MAX_FLOWS - 1);
    let ptr = unsafe { bar4_base.add(BAR4_FLOW_CREDIT_BASE + idx * 4).cast::<u32>() };
    unsafe { ptr.read_volatile() }
}

/// Exclusive 64 B BAR4 cache line written via fence-free `MOVDIR64B` carrying
/// aggregate hardware-queue consumed counts `taken_q[0..4]` and the active `epoch_gen`.
#[repr(C, align(64))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct HostQueueCreditLine {
    pub magic_gen: u64,
    pub taken_q: [u64; 4],
    pub epoch_gen: u64,
    pub flags: u64,
    pub sig: u64,
}

const _: () = assert!(size_of::<HostQueueCreditLine>() == 64 && align_of::<HostQueueCreditLine>() == 64);

impl HostQueueCreditLine {
    #[inline(always)]
    pub const fn compute_sig(magic_gen: u64, taken_q: [u64; 4], epoch_gen: u64, flags: u64) -> u64 {
        magic_gen.wrapping_mul(0x9e37_79b9_7f4a_7c15)
            ^ taken_q[0].wrapping_mul(0xbf58_476d_1ce4_e5b9)
            ^ taken_q[1].wrapping_mul(0x94d0_49bb_1331_11eb)
            ^ taken_q[2].wrapping_mul(0x6c62_272e_07bb_0142)
            ^ taken_q[3].wrapping_mul(0x517c_c1b7_2722_0a95)
            ^ epoch_gen.wrapping_mul(0x2545_f491_4f6c_dd1d)
            ^ flags
            ^ 0x2280_55aa_aa55_00ff
    }

    #[inline(always)]
    pub const fn new(seq_gen: u64, taken_q: [u64; 4], epoch_gen: u64, flags: u64) -> Self {
        let magic_gen = (CREDIT_MAGIC << 32) | (seq_gen & 0xffff_ffff);
        let sig = Self::compute_sig(magic_gen, taken_q, epoch_gen, flags);
        HostQueueCreditLine { magic_gen, taken_q, epoch_gen, flags, sig }
    }

    #[inline(always)]
    pub unsafe fn poll_for_epoch(bar4_base: *const u8, expected_epoch: u64) -> Option<Self> {
        let p = unsafe { bar4_base.add(BAR4_Q_CREDIT_OFF).cast::<u64>() };
        let magic_gen = unsafe { p.add(0).read_volatile() };
        if (magic_gen >> 32) != CREDIT_MAGIC {
            return None;
        }
        let t0 = unsafe { p.add(1).read_volatile() };
        let t1 = unsafe { p.add(2).read_volatile() };
        let t2 = unsafe { p.add(3).read_volatile() };
        let t3 = unsafe { p.add(4).read_volatile() };
        let epoch_gen = unsafe { p.add(5).read_volatile() };
        let flags = unsafe { p.add(6).read_volatile() };
        let sig = unsafe { p.add(7).read_volatile() };
        if expected_epoch != 0 && epoch_gen != expected_epoch {
            return None;
        }
        let taken_q = [t0, t1, t2, t3];
        if sig == Self::compute_sig(magic_gen, taken_q, epoch_gen, flags) {
            Some(HostQueueCreditLine { magic_gen, taken_q, epoch_gen, flags, sig })
        } else {
            None
        }
    }
}

/// Exclusive 64 B BAR4 cache line for Arm 4 (`ceio`) slow-path head pointers & pull targets.
#[repr(C, align(64))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct CeioSlowPullLine {
    pub magic_epoch_gen: u64,
    pub slow_head_q: [u64; 4],
    pub pull_target_q: [u32; 4],
    pub sig: u64,
}

const _: () = assert!(size_of::<CeioSlowPullLine>() == 64 && align_of::<CeioSlowPullLine>() == 64);

impl CeioSlowPullLine {
    #[inline(always)]
    pub const fn compute_sig(magic_epoch_gen: u64, slow_head_q: [u64; 4], pull_target_q: [u32; 4]) -> u64 {
        let p01 = (pull_target_q[0] as u64) | ((pull_target_q[1] as u64) << 32);
        let p23 = (pull_target_q[2] as u64) | ((pull_target_q[3] as u64) << 32);
        magic_epoch_gen.wrapping_mul(0x9e37_79b9_7f4a_7c15)
            ^ slow_head_q[0].wrapping_mul(0xbf58_476d_1ce4_e5b9)
            ^ slow_head_q[1].wrapping_mul(0x94d0_49bb_1331_11eb)
            ^ slow_head_q[2].wrapping_mul(0x6c62_272e_07bb_0142)
            ^ slow_head_q[3].wrapping_mul(0x517c_c1b7_2722_0a95)
            ^ p01.wrapping_mul(0x2545_f491_4f6c_dd1d)
            ^ p23
            ^ 0x2289_cc33_33cc_ff00
    }

    #[inline(always)]
    pub const fn new(seq_gen: u64, epoch_gen: u64, slow_head_q: [u64; 4], pull_target_q: [u32; 4]) -> Self {
        let magic_epoch_gen =
            (PULL_MAGIC_16 << 48) | ((epoch_gen & 0xffff) << 32) | (seq_gen & 0xffff_ffff);
        let sig = Self::compute_sig(magic_epoch_gen, slow_head_q, pull_target_q);
        CeioSlowPullLine { magic_epoch_gen, slow_head_q, pull_target_q, sig }
    }

    #[inline(always)]
    pub unsafe fn poll_for_epoch(bar4_base: *const u8, expected_epoch: u64) -> Option<Self> {
        let p = unsafe { bar4_base.add(BAR4_CEIO_PULL_OFF).cast::<u64>() };
        let magic_epoch_gen = unsafe { p.add(0).read_volatile() };
        if (magic_epoch_gen >> 48) != PULL_MAGIC_16 {
            return None;
        }
        if expected_epoch != 0 && ((magic_epoch_gen >> 32) & 0xffff) != (expected_epoch & 0xffff) {
            return None;
        }
        let s0 = unsafe { p.add(1).read_volatile() };
        let s1 = unsafe { p.add(2).read_volatile() };
        let s2 = unsafe { p.add(3).read_volatile() };
        let s3 = unsafe { p.add(4).read_volatile() };
        let p01 = unsafe { p.add(5).read_volatile() };
        let p23 = unsafe { p.add(6).read_volatile() };
        let sig = unsafe { p.add(7).read_volatile() };
        let slow_head_q = [s0, s1, s2, s3];
        let pull_target_q = [
            (p01 & 0xffff_ffff) as u32,
            (p01 >> 32) as u32,
            (p23 & 0xffff_ffff) as u32,
            (p23 >> 32) as u32,
        ];
        if sig == Self::compute_sig(magic_epoch_gen, slow_head_q, pull_target_q) {
            Some(CeioSlowPullLine { magic_epoch_gen, slow_head_q, pull_target_q, sig })
        } else {
            None
        }
    }
}

/// Pitfall 1 safety verifier: ensures `r_eff + (DROQ_REFILL_THRESHOLD - 1) < rx_desc - wmark`.
pub const fn verify_sdp_credit_safety(nb_desc: u16, r_eff: u64, credit_batch: u64) -> bool {
    if nb_desc < 256 || !nb_desc.is_power_of_two() {
        return false;
    }
    let wmark = if nb_desc < 2048 {
        let q = (nb_desc / 4) as u64;
        if q < 256 { q } else { 256 }
    } else {
        256
    };
    let usable_hw = (nb_desc as u64) - wmark;
    let worst_occ = r_eff + (DROQ_REFILL_THRESHOLD as u64 - 1);
    worst_occ < usable_hw && credit_batch > 0 && credit_batch <= r_eff / 2
}

// -----------------------------------------------------------------------------
// Arm 5: Per-Flow Credit-Gated Scheduler on CN106 (`PerFlowCreditScheduler`)
//
// Operates BEFORE packets enter the shared hardware SDP queue `lane` (`0..4`):
//   1. Per-flow credit check: `flow_submitted[lf] - flow_taken[lf] < r_flow`
//      -> isolates any slow/stalled/elephant flow to its own on-NIC FIFO in
//         48 MiB L3 + 6-ch DDR5 while leaving healthy flows on the SAME HW queue
//         100% unblocked (zero in-queue HoL)!
//   2. Per-HW-queue aggregate check: `q_submitted - q_taken < r_eff_q`
//      (with `prio_reserve` reserved for `CLASS_RPC_HI`)
//      -> keeps the shared HW SDP ring strictly below WMARK & DDIO-hot (~99-100%).
// -----------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, Default)]
pub struct PerFlowSchedStats {
    pub direct_pushes: u64,
    pub queued_rpc: u64,
    pub queued_bulk: u64,
    pub drained_rpc: u64,
    pub drained_bulk: u64,
    pub flow_credit_stalls: u64,
    pub q_credit_stalls: u64,
    pub fifo_max_depth: u64,
    pub fifo_drops: u64,
    pub max_backlogged_flows: u64,
}

pub struct PerFlowCreditScheduler<T> {
    pub lane: usize,
    pub r_eff_q: u64,
    pub r_flow: u32,
    pub prio_reserve: u64,
    pub max_total_queued: usize,
    pub q_submitted: u64,
    pub q_taken_cached: u64,
    flow_submitted: [u32; MAX_FLOWS_PER_LANE],
    flow_taken_cached: [u32; MAX_FLOWS_PER_LANE],
    flow_is_rpc: [bool; MAX_FLOWS_PER_LANE],
    fifos: Vec<VecDeque<T>>,
    in_backlog: [bool; MAX_FLOWS_PER_LANE],
    backlogged_rpc: Vec<u16>,
    backlogged_bulk: Vec<u16>,
    rr_cursor: usize,
    total_queued: usize,
    pub stats: PerFlowSchedStats,
}

impl<T> PerFlowCreditScheduler<T> {
    pub fn new(lane: usize, r_eff_q: u64, r_flow: u32, prio_reserve: u64, max_total_queued: usize) -> Self {
        let mut fifos = Vec::with_capacity(MAX_FLOWS_PER_LANE);
        for _ in 0..MAX_FLOWS_PER_LANE {
            fifos.push(VecDeque::new());
        }
        PerFlowCreditScheduler {
            lane,
            r_eff_q,
            r_flow: r_flow.max(1),
            prio_reserve: prio_reserve.min(r_eff_q.saturating_sub(1)),
            max_total_queued,
            q_submitted: 0,
            q_taken_cached: 0,
            flow_submitted: [0; MAX_FLOWS_PER_LANE],
            flow_taken_cached: [0; MAX_FLOWS_PER_LANE],
            flow_is_rpc: [false; MAX_FLOWS_PER_LANE],
            fifos,
            in_backlog: [false; MAX_FLOWS_PER_LANE],
            backlogged_rpc: Vec::with_capacity(MAX_FLOWS_PER_LANE),
            backlogged_bulk: Vec::with_capacity(MAX_FLOWS_PER_LANE),
            rr_cursor: 0,
            total_queued: 0,
            stats: PerFlowSchedStats::default(),
        }
    }

    pub fn reset(&mut self) {
        self.q_submitted = 0;
        self.q_taken_cached = 0;
        self.flow_submitted.fill(0);
        self.flow_taken_cached.fill(0);
        self.flow_is_rpc.fill(false);
        for f in &mut self.fifos {
            f.clear();
        }
        self.in_backlog.fill(false);
        self.backlogged_rpc.clear();
        self.backlogged_bulk.clear();
        self.rr_cursor = 0;
        self.total_queued = 0;
        self.stats = PerFlowSchedStats::default();
    }

    #[inline(always)]
    pub fn local_idx(fid: u16) -> usize {
        ((fid as usize) >> 2) & (MAX_FLOWS_PER_LANE - 1)
    }

    #[inline(always)]
    pub fn global_fid(&self, lf: usize) -> u16 {
        ((lf << 2) | (self.lane & 3)) as u16
    }

    #[inline(always)]
    pub fn update_q_taken(&mut self, taken: u64) {
        if taken > self.q_taken_cached {
            self.q_taken_cached = taken;
        }
    }

    #[inline(always)]
    pub fn update_flow_taken(&mut self, lf: usize, taken: u32) {
        let cur = self.flow_taken_cached[lf];
        if (taken.wrapping_sub(cur) as i32) > 0 {
            self.flow_taken_cached[lf] = taken;
        }
    }

    #[inline(always)]
    pub fn q_in_flight(&self) -> u64 {
        self.q_submitted.saturating_sub(self.q_taken_cached)
    }

    #[inline(always)]
    pub fn flow_in_flight(&self, lf: usize) -> u32 {
        self.flow_submitted[lf].wrapping_sub(self.flow_taken_cached[lf])
    }

    #[inline(always)]
    pub fn can_push_q(&self, is_rpc: bool) -> bool {
        let cap = if is_rpc {
            self.r_eff_q
        } else {
            self.r_eff_q.saturating_sub(self.prio_reserve)
        };
        self.q_in_flight() < cap
    }

    #[inline(always)]
    pub fn total_queued(&self) -> usize {
        self.total_queued
    }

    /// Admit an incoming packet for `fid` (`fid % 4 == self.lane`).
    /// Returns `Some(pkt)` for immediate direct TX onto the shared SDP queue,
    /// or `None` if diverted into this flow's on-NIC elastic FIFO (or dropped on cap).
    #[inline(always)]
    pub fn admit(
        &mut self,
        fid: u16,
        pkt: T,
        is_rpc: bool,
        mut refresh_flow: impl FnMut(u16) -> u32,
    ) -> Option<T> {
        let lf = Self::local_idx(fid);
        self.flow_is_rpc[lf] = is_rpc;

        // Preserve strict per-flow FIFO order: if this flow already has backlog, append to tail.
        if self.fifos[lf].is_empty() {
            if self.flow_in_flight(lf) >= self.r_flow {
                let fresh = refresh_flow(fid);
                self.update_flow_taken(lf, fresh);
            }
            let flow_ok = self.flow_in_flight(lf) < self.r_flow;
            let q_ok = self.can_push_q(is_rpc);
            if flow_ok && q_ok {
                self.flow_submitted[lf] = self.flow_submitted[lf].wrapping_add(1);
                self.q_submitted += 1;
                self.stats.direct_pushes += 1;
                return Some(pkt);
            }
            if !flow_ok {
                self.stats.flow_credit_stalls += 1;
            } else {
                self.stats.q_credit_stalls += 1;
            }
        }

        if self.total_queued >= self.max_total_queued {
            self.stats.fifo_drops += 1;
            return None;
        }
        self.fifos[lf].push_back(pkt);
        self.total_queued += 1;
        if is_rpc {
            self.stats.queued_rpc += 1;
        } else {
            self.stats.queued_bulk += 1;
        }
        if (self.total_queued as u64) > self.stats.fifo_max_depth {
            self.stats.fifo_max_depth = self.total_queued as u64;
        }
        if !self.in_backlog[lf] {
            self.in_backlog[lf] = true;
            if is_rpc {
                self.backlogged_rpc.push(lf as u16);
            } else {
                self.backlogged_bulk.push(lf as u16);
            }
            let nb = (self.backlogged_rpc.len() + self.backlogged_bulk.len()) as u64;
            if nb > self.stats.max_backlogged_flows {
                self.stats.max_backlogged_flows = nb;
            }
        }
        None
    }

    /// Pop up to one credit-authorized packet from backlogged flows (RPC first, then RR bulk).
    #[inline(always)]
    pub fn pop_authorized(&mut self, mut refresh_flow: impl FnMut(u16) -> u32) -> Option<T> {
        if self.total_queued == 0 {
            return None;
        }
        // 1. Prioritize any backlogged RPC flow
        if !self.backlogged_rpc.is_empty() && self.can_push_q(true) {
            let mut i = 0;
            while i < self.backlogged_rpc.len() {
                let lf = self.backlogged_rpc[i] as usize;
                let fid = self.global_fid(lf);
                if self.flow_in_flight(lf) >= self.r_flow {
                    let fresh = refresh_flow(fid);
                    self.update_flow_taken(lf, fresh);
                }
                if self.flow_in_flight(lf) < self.r_flow {
                    if let Some(pkt) = self.fifos[lf].pop_front() {
                        self.total_queued -= 1;
                        self.flow_submitted[lf] = self.flow_submitted[lf].wrapping_add(1);
                        self.q_submitted += 1;
                        self.stats.drained_rpc += 1;
                        if self.fifos[lf].is_empty() {
                            self.in_backlog[lf] = false;
                            self.backlogged_rpc.swap_remove(i);
                        }
                        return Some(pkt);
                    }
                }
                i += 1;
            }
        }

        // 2. Round-robin across backlogged bulk flows
        let n_bulk = self.backlogged_bulk.len();
        if n_bulk > 0 && self.can_push_q(false) {
            for step in 0..n_bulk {
                let idx = (self.rr_cursor + step) % n_bulk;
                let lf = self.backlogged_bulk[idx] as usize;
                let fid = self.global_fid(lf);
                if self.flow_in_flight(lf) >= self.r_flow {
                    let fresh = refresh_flow(fid);
                    self.update_flow_taken(lf, fresh);
                }
                if self.flow_in_flight(lf) < self.r_flow {
                    if let Some(pkt) = self.fifos[lf].pop_front() {
                        self.total_queued -= 1;
                        self.flow_submitted[lf] = self.flow_submitted[lf].wrapping_add(1);
                        self.q_submitted += 1;
                        self.stats.drained_bulk += 1;
                        if self.fifos[lf].is_empty() {
                            self.in_backlog[lf] = false;
                            self.backlogged_bulk.swap_remove(idx);
                            if !self.backlogged_bulk.is_empty() {
                                self.rr_cursor = idx % self.backlogged_bulk.len();
                            } else {
                                self.rr_cursor = 0;
                            }
                        } else {
                            self.rr_cursor = (idx + 1) % n_bulk;
                        }
                        return Some(pkt);
                    }
                }
            }
        }
        None
    }
}

// -----------------------------------------------------------------------------
// Arm 4: Faithful Fortified CEIO §4.1-4.2 Dual-Path Controller & 2P1C Merge Ring
// (Upgraded with per-flow BAR4 consumption visibility for zero-strawman fairness!)
// -----------------------------------------------------------------------------

pub const RMT_ACT_FAST: u32 = 0;
pub const RMT_ACT_SLOW: u32 = 1;
pub const RMT_ACT_PHASE_PAUSE: u32 = 2;

#[repr(C, align(64))]
pub struct RmtRuleEntry {
    pub action: AtomicU32,
    pub lane: AtomicU32,
    pub fast_hits: AtomicU64,
    pub slow_enq: AtomicU64,
    pub slow_drn: AtomicU64,
    pub last_seen_tsc: AtomicU64,
    pub credit_exhausted_tsc: AtomicU64,
    pub last_fast_lane_seq: AtomicU64,
    pub last_slow_lane_seq: AtomicU64,
}

impl Default for RmtRuleEntry {
    fn default() -> Self {
        Self::new()
    }
}

impl RmtRuleEntry {
    pub const fn new() -> Self {
        RmtRuleEntry {
            action: AtomicU32::new(RMT_ACT_FAST),
            lane: AtomicU32::new(0),
            fast_hits: AtomicU64::new(0),
            slow_enq: AtomicU64::new(0),
            slow_drn: AtomicU64::new(0),
            last_seen_tsc: AtomicU64::new(0),
            credit_exhausted_tsc: AtomicU64::new(0),
            last_fast_lane_seq: AtomicU64::new(0),
            last_slow_lane_seq: AtomicU64::new(0),
        }
    }

    pub fn reset(&self, init_act: u32) {
        self.action.store(init_act, Ordering::Relaxed);
        self.lane.store(0, Ordering::Relaxed);
        self.fast_hits.store(0, Ordering::Relaxed);
        self.slow_enq.store(0, Ordering::Relaxed);
        self.slow_drn.store(0, Ordering::Relaxed);
        self.last_seen_tsc.store(0, Ordering::Relaxed);
        self.credit_exhausted_tsc.store(0, Ordering::Relaxed);
        self.last_fast_lane_seq.store(0, Ordering::Relaxed);
        self.last_slow_lane_seq.store(0, Ordering::Relaxed);
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct CeioFlowState {
    pub flow_id: u16,
    pub active: bool,
    pub in_slow: bool,
    pub phase_paused: bool,
    pub credits: i64,
    pub owed_total: i64,
    pub fast_inflight: i64,
    pub last_fast_hits: u64,
    pub last_flow_taken: u32,
    pub pending_rmt_act: u32,
    pub rmt_apply_tsc: u64,
    pub pause_start_tsc: u64,
    pub last_active_tsc: u64,
}

#[derive(Clone, Copy, Debug)]
pub struct CeioControllerConfig {
    pub c_total: u64,
    pub max_fast_flows: usize,
    pub rmt_rewrite_ticks: u64,
    pub inactive_timeout_ticks: u64,
    pub reactivate_period_ticks: u64,
    pub reactivate_batch: usize,
    pub phase_pause_min_ticks: u64,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct CeioControllerStats {
    pub ctrl_loops: u64,
    pub rmt_fast_to_slow: u64,
    pub rmt_slow_to_fast: u64,
    pub phase_pauses: u64,
    pub phase_pause_ticks_total: u64,
    pub max_phase_pause_ticks: u64,
    pub transient_llc_leak_pkts: u64,
    pub credit_reallocs: u64,
    pub debt_events: u64,
    pub rr_reactivations: u64,
    pub slow_fallback_new_flows: u64,
    pub active_flows_peak: u64,
}

pub struct CeioFlowController {
    pub cfg: CeioControllerConfig,
    pub flows: Vec<CeioFlowState>,
    active_list: Vec<u16>,
    inactive_rr: VecDeque<u16>,
    in_inactive_rr: Vec<bool>,
    debt_set: Vec<u16>,
    free_credits: i64,
    last_rr_tsc: u64,
    pub stats: CeioControllerStats,
}

impl CeioFlowController {
    pub fn new(cfg: CeioControllerConfig) -> Self {
        let mut flows = Vec::with_capacity(MAX_FLOWS);
        for f in 0..MAX_FLOWS {
            let mut st = CeioFlowState::default();
            st.flow_id = f as u16;
            flows.push(st);
        }
        CeioFlowController {
            free_credits: cfg.c_total as i64,
            cfg,
            flows,
            active_list: Vec::with_capacity(cfg.max_fast_flows.max(64)),
            inactive_rr: VecDeque::with_capacity(MAX_FLOWS),
            in_inactive_rr: vec![false; MAX_FLOWS],
            debt_set: Vec::with_capacity(cfg.max_fast_flows.max(64)),
            last_rr_tsc: 0,
            stats: CeioControllerStats::default(),
        }
    }

    pub fn reset(&mut self, rmt: &[RmtRuleEntry]) {
        self.free_credits = self.cfg.c_total as i64;
        self.active_list.clear();
        self.inactive_rr.clear();
        self.in_inactive_rr.fill(false);
        self.debt_set.clear();
        self.last_rr_tsc = 0;
        self.stats = CeioControllerStats::default();
        for (i, st) in self.flows.iter_mut().enumerate() {
            *st = CeioFlowState::default();
            st.flow_id = i as u16;
        }
        for (i, e) in rmt.iter().enumerate() {
            let init_act = if i < self.cfg.max_fast_flows { RMT_ACT_FAST } else { RMT_ACT_SLOW };
            e.reset(init_act);
        }
    }

    pub fn active_flow_count(&self) -> usize {
        self.active_list.len()
    }

    pub fn conserved_total_credits(&self) -> i64 {
        let active_sum: i64 = self
            .flows
            .iter()
            .filter(|f| f.active)
            .map(|f| f.credits + f.fast_inflight - f.owed_total)
            .sum();
        self.free_credits + active_sum
    }

    fn admit_new_flow(&mut self, fid: u16, now_tsc: u64, rmt: &[RmtRuleEntry]) {
        let idx = (fid as usize) & (MAX_FLOWS - 1);
        if self.flows[idx].active {
            return;
        }
        if self.active_list.len() >= self.cfg.max_fast_flows {
            self.flows[idx].in_slow = true;
            self.flows[idx].credits = 0;
            self.flows[idx].last_active_tsc = now_tsc;
            rmt[idx].action.store(RMT_ACT_SLOW, Ordering::Release);
            if !self.in_inactive_rr[idx] {
                self.in_inactive_rr[idx] = true;
                self.inactive_rr.push_back(fid);
            }
            self.stats.slow_fallback_new_flows += 1;
            return;
        }

        let n_old = self.active_list.len() as i64;
        let n_new = n_old + 1;
        // Strict DDIO budget share without `.max(4)` over-granting when max_fast_flows > c_total / 4.
        let c_flow = ((self.cfg.c_total as i64) / n_new).max(1);
        let mut granted: i64 = 0;
        let take_free = self.free_credits.min(c_flow);
        self.free_credits -= take_free;
        granted += take_free;

        // Pass 1 (strictly conserved, zero debt): reclaim ONLY idle surplus above the new quota
        // `c_flow` from existing active flows. When existing flows are idle, Pass 1 alone always
        // raises `granted == c_flow` with zero debt and leaves every existing flow >= `c_flow`.
        if granted < c_flow && n_old > 0 {
            for &efid in &self.active_list {
                let est = &mut self.flows[efid as usize];
                if est.credits > c_flow {
                    let take = (est.credits - c_flow).min(c_flow - granted);
                    est.credits -= take;
                    granted += take;
                    if granted >= c_flow {
                        break;
                    }
                }
            }
        }

        // Pass 2 (genuine in-flight deficit only): if Pass 1 could not raise `c_flow` because
        // existing flows hold credits tied up in flight above `c_flow`, record debt ONLY on flows
        // whose net share (`credits + fast_inflight - owed_total`) exceeds `c_flow`, and ONLY for
        // credits actually lent into `granted` (1:1 strict conservation; retired on repayment).
        if granted < c_flow && n_old > 0 {
            let mut still_needed = c_flow - granted;
            let per_existing = (still_needed + n_old - 1) / n_old;
            for sweep in 0..2 {
                if still_needed <= 0 {
                    break;
                }
                for &efid in &self.active_list {
                    if still_needed <= 0 {
                        break;
                    }
                    let est = &mut self.flows[efid as usize];
                    let total_share = est.credits + est.fast_inflight - est.owed_total;
                    let excess_share = (total_share - c_flow).max(0);
                    if excess_share <= 0 {
                        continue;
                    }
                    let cap = if sweep == 0 { per_existing.min(still_needed) } else { still_needed };
                    let take = excess_share.min(cap);
                    let from_idle = est.credits.max(0).min(take);
                    est.credits -= from_idle;
                    granted += from_idle;
                    still_needed -= from_idle;
                    let from_debt = take - from_idle;
                    if from_debt > 0 {
                        est.owed_total += from_debt;
                        granted += from_debt;
                        still_needed -= from_debt;
                        if !self.debt_set.contains(&efid) {
                            self.debt_set.push(efid);
                        }
                        self.stats.debt_events += 1;
                    }
                }
            }
        }

        let st = &mut self.flows[idx];
        st.active = true;
        st.in_slow = false;
        st.phase_paused = false;
        st.credits = granted;
        st.owed_total = 0;
        st.fast_inflight = 0;
        st.last_active_tsc = now_tsc;
        self.active_list.push(fid);
        rmt[idx].action.store(RMT_ACT_FAST, Ordering::Release);
        self.stats.credit_reallocs += 1;
        if (self.active_list.len() as u64) > self.stats.active_flows_peak {
            self.stats.active_flows_peak = self.active_list.len() as u64;
        }
    }

    fn release_credits(&mut self, fid: u16, mut gamma: i64) {
        if gamma <= 0 {
            return;
        }
        let idx = fid as usize;
        let owed = self.flows[idx].owed_total;
        if owed > 0 {
            // Debt credits were already lent to newly admitted flows in `admit_new_flow`, so
            // repaying `pay` retires the IOU without adding synthetic credits to `free_credits`.
            let pay = owed.min(gamma);
            self.flows[idx].owed_total -= pay;
            gamma -= pay;
            if self.flows[idx].owed_total == 0 {
                if let Some(pos) = self.debt_set.iter().position(|&x| x == fid) {
                    self.debt_set.swap_remove(pos);
                }
            }
        }
        if gamma > 0 {
            self.flows[idx].credits += gamma;
        }
    }

    /// One asynchronous CEIO Flow Controller step using exact per-flow host consumed counts.
    pub fn poll_step(
        &mut self,
        now_tsc: u64,
        rmt: &[RmtRuleEntry],
        host_slow_head: &[u64; MAX_CARD_CORES],
        observed_flows: &[u16],
        mut read_flow_taken: impl FnMut(u16) -> u32,
    ) {
        self.stats.ctrl_loops += 1;

        for &fid in observed_flows {
            self.admit_new_flow(fid, now_tsc, rmt);
        }

        let active_n = self.active_list.len().max(1) as i64;
        let c_flow_cap = ((self.cfg.c_total as i64) / active_n).max(1);
        let resume_thresh = (c_flow_cap / 2).max(1);

        let mut i = 0;
        while i < self.active_list.len() {
            let fid = self.active_list[i];
            let idx = fid as usize;
            let entry = &rmt[idx];

            // 1. Apply any pending RMT rewrite whose rewrite lag has elapsed
            if self.flows[idx].rmt_apply_tsc != 0 && now_tsc >= self.flows[idx].rmt_apply_tsc {
                let target = self.flows[idx].pending_rmt_act;
                entry.action.store(target, Ordering::Release);
                self.flows[idx].rmt_apply_tsc = 0;
                if target == RMT_ACT_SLOW {
                    self.flows[idx].in_slow = true;
                    self.stats.rmt_fast_to_slow += 1;
                    entry.credit_exhausted_tsc.store(0, Ordering::Relaxed);
                } else if target == RMT_ACT_FAST {
                    self.flows[idx].in_slow = false;
                    self.flows[idx].phase_paused = false;
                    self.stats.rmt_slow_to_fast += 1;
                    entry.credit_exhausted_tsc.store(0, Ordering::Relaxed);
                }
            }

            // 2. Subtract newly transmitted fast-path packets
            let cur_hits = entry.fast_hits.load(Ordering::Relaxed);
            let dh = cur_hits.saturating_sub(self.flows[idx].last_fast_hits);
            if dh > 0 {
                self.flows[idx].last_fast_hits = cur_hits;
                self.flows[idx].last_active_tsc = now_tsc;
                if self.flows[idx].credits <= 0 {
                    self.stats.transient_llc_leak_pkts += dh;
                }
                self.flows[idx].credits -= dh as i64;
                self.flows[idx].fast_inflight += dh as i64;
            }

            // 3. Add newly host-consumed packets for THIS exact flow from BAR4 (fast-path credits only)
            let cur_taken = read_flow_taken(fid);
            let dt = cur_taken.wrapping_sub(self.flows[idx].last_flow_taken) as i32;
            if dt > 0 {
                self.flows[idx].last_flow_taken = cur_taken;
                let dt_fast = (dt as i64).min(self.flows[idx].fast_inflight);
                if dt_fast > 0 {
                    self.flows[idx].fast_inflight -= dt_fast;
                    self.release_credits(fid, dt_fast);
                    if self.flows[idx].credits > c_flow_cap {
                        let excess = self.flows[idx].credits - c_flow_cap;
                        self.flows[idx].credits = c_flow_cap;
                        self.free_credits += excess;
                    }
                }
            }

            // 4. Exhausted fast-path credits -> schedule Fast->Slow RMT rewrite
            if !self.flows[idx].in_slow
                && !self.flows[idx].phase_paused
                && self.flows[idx].rmt_apply_tsc == 0
                && self.flows[idx].credits <= 0
            {
                entry.credit_exhausted_tsc.store(now_tsc.max(1), Ordering::Relaxed);
                self.flows[idx].pending_rmt_act = RMT_ACT_SLOW;
                self.flows[idx].rmt_apply_tsc = now_tsc.saturating_add(self.cfg.rmt_rewrite_ticks);
                if self.cfg.rmt_rewrite_ticks == 0 {
                    entry.action.store(RMT_ACT_SLOW, Ordering::Release);
                    self.flows[idx].in_slow = true;
                    self.flows[idx].rmt_apply_tsc = 0;
                    self.stats.rmt_fast_to_slow += 1;
                    entry.credit_exhausted_tsc.store(0, Ordering::Relaxed);
                }
            }

            // 5. Slow->Fast recovery with CEIO §4.2 phase-exclusivity drain confirmation
            let slow_enq = entry.slow_enq.load(Ordering::Relaxed);
            let slow_drn = entry.slow_drn.load(Ordering::Relaxed);
            let lane = (entry.lane.load(Ordering::Relaxed) as usize).min(MAX_CARD_CORES - 1);
            let host_drained_slow = host_slow_head[lane] >= entry.last_slow_lane_seq.load(Ordering::Acquire);
            if self.flows[idx].in_slow && !self.flows[idx].phase_paused && self.flows[idx].rmt_apply_tsc == 0 {
                if self.flows[idx].credits >= resume_thresh && slow_enq <= slow_drn && host_drained_slow {
                    self.flows[idx].phase_paused = true;
                    self.flows[idx].pause_start_tsc = now_tsc;
                    entry.action.store(RMT_ACT_PHASE_PAUSE, Ordering::Release);
                    self.stats.phase_pauses += 1;
                }
            } else if self.flows[idx].phase_paused {
                let elapsed = now_tsc.saturating_sub(self.flows[idx].pause_start_tsc);
                if slow_enq <= slow_drn && host_drained_slow && elapsed >= self.cfg.phase_pause_min_ticks {
                    self.stats.phase_pause_ticks_total += elapsed;
                    if elapsed > self.stats.max_phase_pause_ticks {
                        self.stats.max_phase_pause_ticks = elapsed;
                    }
                    self.flows[idx].phase_paused = false;
                    self.flows[idx].pending_rmt_act = RMT_ACT_FAST;
                    self.flows[idx].rmt_apply_tsc = now_tsc.saturating_add(self.cfg.rmt_rewrite_ticks);
                    if self.cfg.rmt_rewrite_ticks == 0 {
                        entry.action.store(RMT_ACT_FAST, Ordering::Release);
                        self.flows[idx].in_slow = false;
                        self.flows[idx].rmt_apply_tsc = 0;
                        self.stats.rmt_slow_to_fast += 1;
                    }
                }
            }

            // 6. Reclaim inactive flows ONLY when overflow flows are actually waiting for a fast-table slot
            // (`!self.inactive_rr.is_empty()`), so F <= max_fast_flows (16/64/256) suffers ZERO spurious evictions!
            let table_pressure = !self.inactive_rr.is_empty();
            let seen = entry.last_seen_tsc.load(Ordering::Relaxed).max(self.flows[idx].last_active_tsc);
            let inactive = table_pressure
                && self.cfg.inactive_timeout_ticks > 0
                && !self.flows[idx].phase_paused
                && self.flows[idx].fast_inflight == 0
                && self.flows[idx].owed_total == 0
                && now_tsc.saturating_sub(seen) >= self.cfg.inactive_timeout_ticks;
            if inactive {
                let reclaimed = self.flows[idx].credits.max(0);
                self.free_credits += reclaimed;
                self.flows[idx].credits = 0;
                self.flows[idx].active = false;
                self.flows[idx].in_slow = true;
                entry.action.store(RMT_ACT_SLOW, Ordering::Release);
                self.active_list.swap_remove(i);
                if !self.in_inactive_rr[idx] {
                    self.in_inactive_rr[idx] = true;
                    self.inactive_rr.push_back(fid);
                }
                continue;
            }
            i += 1;
        }

        // 7. Periodic round-robin reactivation from slow-path queue
        if self.cfg.reactivate_period_ticks > 0
            && now_tsc.saturating_sub(self.last_rr_tsc) >= self.cfg.reactivate_period_ticks
        {
            self.last_rr_tsc = now_tsc;
            let mut n_react = 0usize;
            while n_react < self.cfg.reactivate_batch
                && self.active_list.len() < self.cfg.max_fast_flows
                && !self.inactive_rr.is_empty()
            {
                let Some(fid) = self.inactive_rr.pop_front() else { break };
                let idx = fid as usize;
                self.in_inactive_rr[idx] = false;
                if !self.flows[idx].active {
                    self.admit_new_flow(fid, now_tsc, rmt);
                    self.stats.rr_reactivations += 1;
                    n_react += 1;
                }
            }
        }
    }
}

// -----------------------------------------------------------------------------
// Host-Side CEIO §4.2 2P1C Software Merge Ring
// -----------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SwProducer {
    FastPath,
    SlowPath,
}

#[derive(Clone, Copy, Debug)]
pub struct SwRingEntry<T> {
    pub producer: SwProducer,
    pub lane: u8,
    pub item: T,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SwMergeStats {
    pub fast_merged: u64,
    pub slow_merged: u64,
    pub phase_switches: u64,
}

enum SwRingSlot<T> {
    Empty(#[allow(dead_code)] u8),
    Occupied(SwRingEntry<T>),
}

pub struct SwMergeRing2P1C<T> {
    fast_q: Vec<SwRingSlot<T>>,
    slow_q: Vec<SwRingSlot<T>>,
    mask: usize,
    fast_head: usize,
    fast_tail: usize,
    fast_len: usize,
    slow_head: usize,
    slow_tail: usize,
    slow_len: usize,
    last_producer: Option<SwProducer>,
    pub stats: SwMergeStats,
}

impl<T> SwMergeRing2P1C<T> {
    pub fn new(cap: usize) -> Self {
        let cap_pow2 = cap.max(64).next_power_of_two();
        let mut fast_q = Vec::with_capacity(cap_pow2);
        let mut slow_q = Vec::with_capacity(cap_pow2);
        for _ in 0..cap_pow2 {
            fast_q.push(SwRingSlot::Empty(1));
            slow_q.push(SwRingSlot::Empty(1));
        }
        SwMergeRing2P1C {
            fast_q,
            slow_q,
            mask: cap_pow2 - 1,
            fast_head: 0,
            fast_tail: 0,
            fast_len: 0,
            slow_head: 0,
            slow_tail: 0,
            slow_len: 0,
            last_producer: None,
            stats: SwMergeStats::default(),
        }
    }

    pub fn reset(&mut self) {
        while self.pop().is_some() {}
        self.fast_head = 0;
        self.fast_tail = 0;
        self.fast_len = 0;
        self.slow_head = 0;
        self.slow_tail = 0;
        self.slow_len = 0;
        self.last_producer = None;
        self.stats = SwMergeStats::default();
    }

    #[inline(always)]
    pub fn push_from(&mut self, prod: SwProducer, lane: u8, item: T) -> Result<(), T> {
        let cap = self.mask + 1;
        match prod {
            SwProducer::FastPath => {
                if self.fast_len >= cap {
                    return Err(item);
                }
                self.fast_q[self.fast_tail] = SwRingSlot::Occupied(SwRingEntry { producer: prod, lane, item });
                self.fast_tail = (self.fast_tail + 1) & self.mask;
                self.fast_len += 1;
                Ok(())
            }
            SwProducer::SlowPath => {
                if self.slow_len >= cap {
                    return Err(item);
                }
                self.slow_q[self.slow_tail] = SwRingSlot::Occupied(SwRingEntry { producer: prod, lane, item });
                self.slow_tail = (self.slow_tail + 1) & self.mask;
                self.slow_len += 1;
                Ok(())
            }
        }
    }

    #[inline(always)]
    pub fn pop(&mut self) -> Option<SwRingEntry<T>> {
        let ev = if self.fast_len > 0 {
            let slot = std::mem::replace(&mut self.fast_q[self.fast_head], SwRingSlot::Empty(1));
            self.fast_head = (self.fast_head + 1) & self.mask;
            self.fast_len -= 1;
            self.stats.fast_merged += 1;
            match slot {
                SwRingSlot::Occupied(e) => e,
                SwRingSlot::Empty(_) => unreachable!(),
            }
        } else if self.slow_len > 0 {
            let slot = std::mem::replace(&mut self.slow_q[self.slow_head], SwRingSlot::Empty(1));
            self.slow_head = (self.slow_head + 1) & self.mask;
            self.slow_len -= 1;
            self.stats.slow_merged += 1;
            match slot {
                SwRingSlot::Occupied(e) => e,
                SwRingSlot::Empty(_) => unreachable!(),
            }
        } else {
            return None;
        };
        if let Some(prev) = self.last_producer {
            if prev != ev.producer {
                self.stats.phase_switches += 1;
            }
        }
        self.last_producer = Some(ev.producer);
        Some(ev)
    }
}

// -----------------------------------------------------------------------------
// Per-flow strict ordering verifier (pre-touched to prevent page faults)
// -----------------------------------------------------------------------------

pub struct FlowOrderTracker {
    expected_next: Vec<u64>,
    pub ooo_count: u64,
    pub dup_count: u64,
    pub gap_count: u64,
}

impl FlowOrderTracker {
    pub fn new() -> Self {
        let mut expected_next = vec![1u64; MAX_FLOWS];
        std::hint::black_box(&expected_next);
        expected_next.fill(0);
        FlowOrderTracker { expected_next, ooo_count: 0, dup_count: 0, gap_count: 0 }
    }

    pub fn reset(&mut self) {
        self.expected_next.fill(0);
        self.ooo_count = 0;
        self.dup_count = 0;
        self.gap_count = 0;
    }

    #[inline(always)]
    pub fn observe(&mut self, flow_id: u16, flow_seq: u64) {
        let idx = (flow_id as usize) & (MAX_FLOWS - 1);
        let exp = self.expected_next[idx];
        if exp == 0 {
            self.expected_next[idx] = flow_seq + 1;
        } else if flow_seq == exp {
            self.expected_next[idx] = flow_seq + 1;
        } else if flow_seq < exp {
            if flow_seq + 1 == exp {
                self.dup_count += 1;
            } else {
                self.ooo_count += 1;
            }
        } else {
            self.gap_count += flow_seq - exp;
            self.expected_next[idx] = flow_seq + 1;
        }
    }
}

impl Default for FlowOrderTracker {
    fn default() -> Self {
        Self::new()
    }
}

// -----------------------------------------------------------------------------
// RoCEv2 UD ICRC-32 + Reply Template (proven on CN106 eth0 <-> mercury mlx5_2)
// -----------------------------------------------------------------------------

const CRC32_TABLE: [u32; 256] = {
    let mut t = [0u32; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u32;
        let mut j = 0;
        while j < 8 {
            c = if (c & 1) != 0 { 0xedb8_8320 ^ (c >> 1) } else { c >> 1 };
            j += 1;
        }
        t[i] = c;
        i += 1;
    }
    t
};

#[inline(always)]
pub fn crc32_sw_update(mut crc: u32, data: &[u8]) -> u32 {
    for &b in data {
        crc = CRC32_TABLE[((crc ^ (b as u32)) & 0xff) as usize] ^ (crc >> 8);
    }
    crc
}

#[cfg(target_arch = "aarch64")]
#[target_feature(enable = "crc")]
#[inline]
unsafe fn crc32_hw_update_aarch64(mut crc: u32, data: &[u8]) -> u32 {
    use core::arch::aarch64::{__crc32b, __crc32d, __crc32w};
    let mut i = 0usize;
    let len = data.len();
    while i + 8 <= len {
        let v = u64::from_le_bytes(data[i..i + 8].try_into().unwrap());
        crc = __crc32d(crc, v);
        i += 8;
    }
    while i + 4 <= len {
        let v = u32::from_le_bytes(data[i..i + 4].try_into().unwrap());
        crc = __crc32w(crc, v);
        i += 4;
    }
    while i < len {
        crc = __crc32b(crc, data[i]);
        i += 1;
    }
    crc
}

#[inline(always)]
pub fn crc32_update(crc: u32, data: &[u8]) -> u32 {
    #[cfg(target_arch = "aarch64")]
    {
        unsafe { crc32_hw_update_aarch64(crc, data) }
    }
    #[cfg(not(target_arch = "aarch64"))]
    {
        crc32_sw_update(crc, data)
    }
}

#[derive(Clone, Copy)]
pub struct RoceTemplate {
    pub hdr: [u8; ROCE_HDR_LEN],
    pub base_crc: u32,
}

impl RoceTemplate {
    pub fn new(
        src_mac: [u8; 6],
        dst_mac: [u8; 6],
        src_ip: [u8; 4],
        dst_ip: [u8; 4],
        dst_qpn: u32,
        payload_len: usize,
    ) -> Self {
        let mut hdr = [0u8; ROCE_HDR_LEN];
        hdr[0..6].copy_from_slice(&dst_mac);
        hdr[6..12].copy_from_slice(&src_mac);
        hdr[12..14].copy_from_slice(&0x0800u16.to_be_bytes());

        let udp_len = (8 + 12 + 8 + payload_len + ROCE_ICRC_LEN) as u16;
        let ip_len = 20 + udp_len;
        hdr[14] = 0x45;
        hdr[15] = 0x00;
        hdr[16..18].copy_from_slice(&ip_len.to_be_bytes());
        hdr[18..20].copy_from_slice(&0x2280u16.to_be_bytes());
        hdr[20..22].copy_from_slice(&0x4000u16.to_be_bytes());
        hdr[22] = 64;
        hdr[23] = 17;
        hdr[24..26].copy_from_slice(&0u16.to_be_bytes());
        hdr[26..30].copy_from_slice(&src_ip);
        hdr[30..34].copy_from_slice(&dst_ip);

        let mut sum: u32 = 0;
        for i in (14..34).step_by(2) {
            sum += u16::from_be_bytes([hdr[i], hdr[i + 1]]) as u32;
        }
        while (sum >> 16) != 0 {
            sum = (sum & 0xffff) + (sum >> 16);
        }
        let csum = !(sum as u16);
        hdr[24..26].copy_from_slice(&csum.to_be_bytes());

        hdr[34..36].copy_from_slice(&ROCE_UDP_PORT.to_be_bytes());
        hdr[36..38].copy_from_slice(&ROCE_UDP_PORT.to_be_bytes());
        hdr[38..40].copy_from_slice(&udp_len.to_be_bytes());
        hdr[40..42].copy_from_slice(&0u16.to_be_bytes());

        hdr[42] = 0x64;
        hdr[43] = 0x00;
        hdr[44..46].copy_from_slice(&0xffffu16.to_be_bytes());
        hdr[46] = 0x00;
        hdr[47] = ((dst_qpn >> 16) & 0xff) as u8;
        hdr[48] = ((dst_qpn >> 8) & 0xff) as u8;
        hdr[49] = (dst_qpn & 0xff) as u8;
        hdr[50..54].copy_from_slice(&0u32.to_be_bytes());

        hdr[54..58].copy_from_slice(&ROCE_QKEY.to_be_bytes());
        hdr[58] = 0x00;
        hdr[59] = ((ROCE_CARD_QPN >> 16) & 0xff) as u8;
        hdr[60] = ((ROCE_CARD_QPN >> 8) & 0xff) as u8;
        hdr[61] = (ROCE_CARD_QPN & 0xff) as u8;

        let mut icrc_hdr = [0xffu8; 8 + (ROCE_HDR_LEN - 14)];
        icrc_hdr[8..].copy_from_slice(&hdr[14..ROCE_HDR_LEN]);
        icrc_hdr[8 + 1] = 0xff;
        icrc_hdr[8 + 8] = 0xff;
        icrc_hdr[8 + 10] = 0xff;
        icrc_hdr[8 + 11] = 0xff;
        icrc_hdr[8 + 26] = 0xff;
        icrc_hdr[8 + 27] = 0xff;
        icrc_hdr[8 + 32] = 0xff;

        let base_crc = crc32_update(0xffff_ffff, &icrc_hdr);
        RoceTemplate { hdr, base_crc }
    }

    #[inline(always)]
    pub fn icrc(&self, payload: &[u8]) -> u32 {
        !crc32_update(self.base_crc, payload)
    }
}

// -----------------------------------------------------------------------------
// Constant-memory high-resolution latency histogram (~54 KiB, up to 1 s)
// -----------------------------------------------------------------------------

const B0: usize = 2000;
const B1: usize = 1800;
const B2: usize = 1980;
const B3: usize = 980;
const TOTAL_BINS: usize = B0 + B1 + B2 + B3 + 1;

pub struct FastHist {
    bins: Box<[u64; TOTAL_BINS]>,
    count: u64,
    sum_ns: u128,
    min_ns: u64,
    max_ns: u64,
}

impl Default for FastHist {
    fn default() -> Self {
        Self::new()
    }
}

impl FastHist {
    pub fn new() -> Self {
        let mut bins = Box::new([1u64; TOTAL_BINS]);
        std::hint::black_box(&bins);
        bins.fill(0);
        FastHist { bins, count: 0, sum_ns: 0, min_ns: u64::MAX, max_ns: 0 }
    }

    pub fn reset(&mut self) {
        self.bins.fill(0);
        self.count = 0;
        self.sum_ns = 0;
        self.min_ns = u64::MAX;
        self.max_ns = 0;
    }

    #[inline(always)]
    pub fn record_ns(&mut self, ns: u64) {
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

    pub fn merge_from(&mut self, other: &FastHist) {
        for i in 0..TOTAL_BINS {
            self.bins[i] += other.bins[i];
        }
        self.count += other.count;
        self.sum_ns += other.sum_ns;
        if other.min_ns < self.min_ns {
            self.min_ns = other.min_ns;
        }
        if other.max_ns > self.max_ns {
            self.max_ns = other.max_ns;
        }
    }

    pub fn count(&self) -> u64 {
        self.count
    }

    pub fn mean_us(&self) -> f64 {
        if self.count == 0 {
            0.0
        } else {
            (self.sum_ns as f64) / (self.count as f64) / 1000.0
        }
    }

    pub fn max_us(&self) -> f64 {
        self.max_ns as f64 / 1000.0
    }

    pub fn pct_us(&self, q: f64) -> f64 {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wire_roundtrip_and_tear_check() {
        let mut buf = [0u8; 256];
        encode_wire_pkt(&mut buf, 42, 99999, 123, 7, 55, 2, CLASS_RPC_HI, PATH_PER_FLOW_CR, OP_DATA, 0);
        let (meta, tsc, fid, fseq, aux, ep, _, _) = verify_and_touch_wire_pkt(&buf, true).unwrap();
        assert_eq!(meta.global_seq, 42);
        assert_eq!(tsc, 99999);
        assert_eq!(fid, 123);
        assert_eq!(fseq, 7);
        assert_eq!(aux, 55);
        assert_eq!(ep, 2);

        buf[255] ^= 1;
        assert_eq!(verify_and_touch_wire_pkt(&buf, true), Err(true));
    }

    #[test]
    fn bar4_1024_flows_fit_in_4kib_and_pitfall1_safe() {
        assert_eq!(FLOW_CREDIT_BYTES, 4096);
        assert_eq!(FLOW_CREDIT_GROUPS, 64);
        assert_eq!(size_of::<FlowCreditLine>(), 64);
        assert!(verify_sdp_credit_safety(DEFAULT_SMALL_RX_DESC, DEFAULT_R_EFF_PER_Q, DEFAULT_CREDIT_BATCH));
        assert!(!verify_sdp_credit_safety(256, 130, 16)); // 130 + 63 = 193 >= 192
    }

    #[test]
    fn per_flow_credit_isolates_stalled_flow_zero_hol_across_256_flows_on_same_hw_q() {
        let mut sched: PerFlowCreditScheduler<u64> =
            PerFlowCreditScheduler::new(0, DEFAULT_R_EFF_PER_Q, DEFAULT_R_FLOW, DEFAULT_PRIO_RESERVE, 65536);
        let mut taken_flow = [0u32; MAX_FLOWS];
        let mut q_taken = 0u64;

        // Flow 0 stalls on host: host drains HW queue (`q_taken` advances) so HW ring isn't blocked,
        // but does NOT advance `taken_flow[0]`!
        let mut flow0_direct = 0u64;
        for seq in 0..500u64 {
            if sched.admit(0, seq, false, |f| taken_flow[f as usize]).is_some() {
                flow0_direct += 1;
                q_taken += 1;
                sched.update_q_taken(q_taken);
            }
        }
        assert_eq!(flow0_direct, DEFAULT_R_FLOW as u64);
        assert_eq!(sched.total_queued(), 500 - DEFAULT_R_FLOW as usize);

        // Now all 255 healthy RPC flows (fid = 4, 8, ..., 1020) share the EXACT SAME HW queue 0:
        // EVERY SINGLE ONE sails straight through with ZERO queueing!
        let mut healthy_direct = 0u64;
        for round in 0..10u64 {
            for k in 1..256u16 {
                let fid = k * 4;
                let res = sched.admit(fid, round * 1000 + k as u64, true, |f| taken_flow[f as usize]);
                assert!(res.is_some(), "healthy RPC flow {fid} must NOT be HoL-blocked by stalled flow 0");
                healthy_direct += 1;
                taken_flow[fid as usize] += 1;
                q_taken += 1;
                sched.update_q_taken(q_taken);
            }
        }
        assert_eq!(healthy_direct, 2550);
        assert_eq!(sched.stats.queued_rpc, 0);

        // Flow 0 unblocks -> drains all 468 remaining packets strictly in order!
        let mut drained = Vec::new();
        while sched.total_queued() > 0 {
            taken_flow[0] += DEFAULT_R_FLOW;
            while let Some(seq) = sched.pop_authorized(|f| taken_flow[f as usize]) {
                drained.push(seq);
                q_taken += 1;
                sched.update_q_taken(q_taken);
            }
        }
        assert_eq!(drained.len(), 500 - DEFAULT_R_FLOW as usize);
        for (i, &seq) in drained.iter().enumerate() {
            assert_eq!(seq, (DEFAULT_R_FLOW as u64) + (i as u64));
        }
    }

    #[test]
    fn ceio_controller_demotes_stalled_flow_and_recovers() {
        let cfg = CeioControllerConfig {
            c_total: 1024,
            max_fast_flows: 256,
            rmt_rewrite_ticks: 0,
            inactive_timeout_ticks: 1_000_000,
            reactivate_period_ticks: 10_000,
            reactivate_batch: 8,
            phase_pause_min_ticks: 0,
        };
        let rmt: Vec<RmtRuleEntry> = (0..MAX_FLOWS).map(|_| RmtRuleEntry::new()).collect();
        let mut ctrl = CeioFlowController::new(cfg);
        ctrl.reset(&rmt);

        let taken_flow = [0u32; MAX_FLOWS];
        let slow_head = [0u64; MAX_CARD_CORES];
        ctrl.poll_step(100, &rmt, &slow_head, &[0, 4], |f| taken_flow[f as usize]);
        assert_eq!(rmt[0].action.load(Ordering::Relaxed), RMT_ACT_FAST);
        assert_eq!(rmt[4].action.load(Ordering::Relaxed), RMT_ACT_FAST);

        // Flow 0 exhausts its fast credits while flow 4 stays healthy
        rmt[0].fast_hits.store(600, Ordering::Relaxed);
        rmt[4].fast_hits.store(10, Ordering::Relaxed);
        ctrl.poll_step(200, &rmt, &slow_head, &[], |f| if f == 4 { 10 } else { 0 });
        assert_eq!(rmt[0].action.load(Ordering::Relaxed), RMT_ACT_SLOW);
        assert_eq!(rmt[4].action.load(Ordering::Relaxed), RMT_ACT_FAST);
    }

    #[test]
    fn ceio_controller_conserves_credits_zero_phantom_debt_f16_to_1024() {
        let slow_head = [0u64; MAX_CARD_CORES];
        for &f_count in &[16usize, 64, 256, 1024] {
            let cfg = CeioControllerConfig {
                c_total: 1024,
                max_fast_flows: f_count,
                rmt_rewrite_ticks: 0,
                inactive_timeout_ticks: 1_000_000,
                reactivate_period_ticks: 10_000,
                reactivate_batch: 8,
                phase_pause_min_ticks: 0,
            };
            let rmt: Vec<RmtRuleEntry> = (0..MAX_FLOWS).map(|_| RmtRuleEntry::new()).collect();
            let mut ctrl = CeioFlowController::new(cfg);
            ctrl.reset(&rmt);

            let mut taken_flow = [0u32; MAX_FLOWS];
            // Admit F idle flows one by one (reproduces Scenario B pre-burst ramp)
            for n in 0..f_count {
                let fid = n as u16;
                ctrl.poll_step((n as u64 + 1) * 100, &rmt, &slow_head, &[fid], |f| taken_flow[f as usize]);
                let expected_min = (1024 / (n as i64 + 1)).max(1);
                assert_eq!(ctrl.conserved_total_credits(), 1024, "conservation broken at F={f_count} n={}", n + 1);
                assert_eq!(ctrl.stats.debt_events, 0, "phantom debt at F={f_count} n={}", n + 1);
                for k in 0..=n {
                    assert!(
                        ctrl.flows[k].credits >= expected_min,
                        "flow {k} drained below quota {expected_min} (got {}) at F={f_count} n={}",
                        ctrl.flows[k].credits,
                        n + 1
                    );
                    assert_eq!(ctrl.flows[k].owed_total, 0);
                    assert_eq!(rmt[k].action.load(Ordering::Relaxed), RMT_ACT_FAST);
                }
            }
            let exact_quota = 1024 / (f_count as i64);
            assert_eq!(ctrl.free_credits, 0);
            for k in 0..f_count {
                assert_eq!(ctrl.flows[k].credits, exact_quota);
            }
            assert_eq!(ctrl.stats.rmt_fast_to_slow, 0);

            // At F = 256 (quota = 4), healthy 1-pkt in-flight RPCs across all 256 flows trigger zero demotions/debt
            if f_count == 256 {
                for r in 0..10u64 {
                    for k in 0..256 {
                        rmt[k].fast_hits.fetch_add(1, Ordering::Relaxed);
                    }
                    ctrl.poll_step(50_000 + r * 200, &rmt, &slow_head, &[], |f| taken_flow[f as usize]);
                    assert_eq!(ctrl.conserved_total_credits(), 1024);
                    assert_eq!(ctrl.stats.rmt_fast_to_slow, 0);
                    assert_eq!(ctrl.stats.debt_events, 0);
                    for k in 0..256 {
                        taken_flow[k] += 1;
                    }
                    ctrl.poll_step(50_100 + r * 200, &rmt, &slow_head, &[], |f| taken_flow[f as usize]);
                    assert_eq!(ctrl.conserved_total_credits(), 1024);
                }
            }
        }

        // Genuine in-flight debt test: Flow 0 holds 1000 packets in flight while 255 new flows join;
        // debt is charged ONLY to Flow 0, strictly conserved, and slow-path completions do not inflate free_credits.
        let cfg = CeioControllerConfig {
            c_total: 1024,
            max_fast_flows: 256,
            rmt_rewrite_ticks: 0,
            inactive_timeout_ticks: 1_000_000,
            reactivate_period_ticks: 10_000,
            reactivate_batch: 8,
            phase_pause_min_ticks: 0,
        };
        let rmt: Vec<RmtRuleEntry> = (0..MAX_FLOWS).map(|_| RmtRuleEntry::new()).collect();
        let mut ctrl = CeioFlowController::new(cfg);
        ctrl.reset(&rmt);
        let mut taken_flow = [0u32; MAX_FLOWS];
        ctrl.poll_step(100, &rmt, &slow_head, &[0], |f| taken_flow[f as usize]);
        rmt[0].fast_hits.store(1000, Ordering::Relaxed);
        ctrl.poll_step(200, &rmt, &slow_head, &[], |f| taken_flow[f as usize]);
        assert_eq!(ctrl.flows[0].credits, 24);
        assert_eq!(ctrl.flows[0].fast_inflight, 1000);

        for fid in 1..256u16 {
            ctrl.poll_step(300 + fid as u64 * 10, &rmt, &slow_head, &[fid], |f| taken_flow[f as usize]);
            let quota = 1024 / (fid as i64 + 1);
            assert_eq!(ctrl.conserved_total_credits(), 1024);
            assert_eq!(ctrl.flows[fid as usize].credits, quota);
            assert_eq!(ctrl.flows[fid as usize].owed_total, 0);
        }
        for fid in 1..256usize {
            assert_eq!(ctrl.flows[fid].credits, 4);
            assert_eq!(ctrl.flows[fid].owed_total, 0);
        }
        assert_eq!(ctrl.flows[0].credits, 0);
        assert_eq!(ctrl.flows[0].owed_total, 996);
        assert!(ctrl.stats.debt_events > 0);

        // Flow 0 drains its 1000 fast packets + 5000 slow-path packets: exact recovery, zero credit inflation!
        taken_flow[0] = 6000;
        ctrl.poll_step(10_000, &rmt, &slow_head, &[], |f| taken_flow[f as usize]);
        assert_eq!(ctrl.flows[0].owed_total, 0);
        assert_eq!(ctrl.flows[0].fast_inflight, 0);
        assert_eq!(ctrl.flows[0].credits, 4);
        assert_eq!(ctrl.free_credits, 0);
        assert_eq!(ctrl.conserved_total_credits(), 1024);
    }
}
