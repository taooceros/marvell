//! Shared wire protocol, BAR4 fence-free credit/control layout, CEIO §4.1-4.2
//! reactive dual-path control/merge engines, and CreditRing unified O(1) credit
//! gate for MRV-211 (E3: same-CN106 standard-SDP showdown vs CEIO-style dual-path).

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
pub const SDP_UDP_DST_PORT: u16 = 4211;
pub const SDP_MTU: u16 = 1500;

/// Single-frame payload bounds (64 B..=1408 B, 8 B multiple; fits 1500 B MTU).
pub const MIN_WIRE_PAYLOAD: usize = 64;
pub const MAX_WIRE_PAYLOAD: usize = 1408;
pub const ETH_MBUF_BUF_SIZE: u16 = 2176;
pub const SDP_MBUF_BUF_SIZE: u16 = 2176;

pub const MAX_CARD_CORES: usize = 4;
pub const MAX_FLOWS: usize = 16_384;
pub const MAX_DRAIN: usize = 32;

/// Default CN106 control/sync TCP endpoints (over `ens1` <-> `sdp1-0`).
pub const CARD_SYNC_ADDR: &str = "0.0.0.0:7551";
pub const HOST_CTRL_ADDR: &str = "127.0.0.1:7553";

/// Pitfall 1 hardware constant from `marvell-dpdk/drivers/net/octeon_ep/otx_ep_rxtx.h:24`.
pub const DROQ_REFILL_THRESHOLD: u16 = 64;

/// Default unified hot-ring parameters satisfying Pitfall 1:
///   nb_desc = 512 => WMARK = min(256, 512/4) = 128 => usable HW headroom = 384.
///   R_eff = 256 + (DROQ_REFILL_THRESHOLD - 1) = 319 < 384 => HW NEVER hits WMARK!
pub const DEFAULT_UNIFIED_RX_DESC: u16 = 512;
pub const DEFAULT_UNIFIED_R_EFF_PER_Q: u64 = 256;
pub const DEFAULT_CREDIT_BATCH: u64 = 32;

/// Default CEIO parameters on the exact same LLC credit budget (`C_total = 4 * 256 = 1024`).
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
pub const CLASS_CHURN: u8 = 2;
pub const CLASS_BURST: u8 = 3;

pub const PATH_UNIFIED: u8 = 0;
pub const PATH_CEIO_FAST: u8 = 1;
pub const PATH_CEIO_SLOW: u8 = 2;
pub const PATH_LARGE_SDP: u8 = 3;

pub const FLAG_CEIO_LLC_LEAK: u8 = 1 << 0;
pub const FLAG_PHASE_PAUSED: u8 = 1 << 1;
pub const FLAG_KV_LOOKUP: u8 = 1 << 2;
pub const FLAG_LAZY_RELEASE: u8 = 1 << 3;
pub const FLAG_SLOW_HINT: u8 = 1 << 4;

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
pub const fn pack_key_epoch(kv_key: u32, burst_epoch: u32) -> u64 {
    (kv_key as u64) | ((burst_epoch as u64) << 32)
}

#[inline(always)]
pub const fn unpack_key_epoch(w3: u64) -> (u32, u32) {
    (w3 as u32, (w3 >> 32) as u32)
}

#[inline(always)]
pub const fn wire_cookie(global_seq: u64, flow_id: u16, flow_seq: u64, len_bytes: usize) -> u64 {
    let x = global_seq
        .wrapping_mul(0x9e37_79b9_7f4a_7c15)
        .wrapping_add((flow_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ flow_seq.wrapping_mul(0x94d0_49bb_1331_11eb)
        ^ ((len_bytes as u64) << 40)
        ^ 0x55aa_55aa_0f0f_f0f0;
    (x ^ (x >> 29)) | 1
}

#[inline(always)]
pub const fn probe_cookie(seq: u64) -> u64 {
    seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0xa5a5_5a5a_1234_5678
}

#[inline(always)]
pub const fn splitmix64(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ (x >> 31)
}

/// Deterministic cache-line payload word at byte offset `off` (off in 48..len-8).
#[inline(always)]
pub const fn payload_word(cookie: u64, word_idx: usize) -> u64 {
    cookie.wrapping_add((word_idx as u64).wrapping_mul(0x9e37_79b9_7f4a_7c15)) ^ 0x3c3c_c3c3_5a5a_a5a5
}

/// Write a complete verified wire frame into `buf` (length `64..=1408`, multiple of 8).
pub fn encode_wire_pkt(
    buf: &mut [u8],
    global_seq: u64,
    client_tsc: u64,
    flow_id: u16,
    flow_seq: u64,
    kv_key: u32,
    burst_epoch: u32,
    pkt_class: u8,
    path_tag: u8,
    op: u8,
    flags: u8,
) {
    let len = buf.len();
    debug_assert!(len >= MIN_WIRE_PAYLOAD && len <= MAX_WIRE_PAYLOAD && len.is_multiple_of(8));
    let w0 = pack_meta(global_seq, len, pkt_class, path_tag, op, flags);
    let w2 = pack_flow_meta(flow_id, flow_seq);
    let w3 = pack_key_epoch(kv_key, burst_epoch);
    let cookie = wire_cookie(global_seq, flow_id, flow_seq, len);

    buf[0..8].copy_from_slice(&w0.to_le_bytes());
    buf[8..16].copy_from_slice(&client_tsc.to_le_bytes());
    buf[16..24].copy_from_slice(&w2.to_le_bytes());
    buf[24..32].copy_from_slice(&w3.to_le_bytes());
    buf[32..40].copy_from_slice(&0u64.to_le_bytes()); // card_rx_tsc stamped on card
    buf[40..48].copy_from_slice(&cookie.to_le_bytes());

    // Stamp one deterministic word per 64 B cache line in payload body so full-line
    // reads on the host touch every cache line cleanly.
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

/// Stamp card path tag, flags, and `card_rx_tsc` while keeping `head_tag == tail_tag`.
#[inline(always)]
pub fn stamp_card_meta(payload: &mut [u8], path_tag: u8, add_flags: u8, card_rx_tsc: u64) {
    let len = payload.len();
    if len < MIN_WIRE_PAYLOAD {
        return;
    }
    let old_w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
    let cleared = old_w0 & !(0x3u64 << 50);
    let new_w0 = cleared | (((path_tag as u64) & 0x3) << 50) | (((add_flags as u64) & 0x7f) << 56);
    payload[0..8].copy_from_slice(&new_w0.to_le_bytes());
    payload[32..40].copy_from_slice(&card_rx_tsc.to_le_bytes());
    payload[len - 8..len].copy_from_slice(&new_w0.to_le_bytes());
}

/// Verify packet framing and touch every 64 B cache line of the payload on the host.
/// Returns `Ok((meta, client_tsc, flow_id, flow_seq, kv_key, burst_epoch, card_rx_tsc, checksum_xor))`
/// or `Err(is_torn)`.
#[inline(always)]
pub fn verify_and_touch_wire_pkt(
    payload: &[u8],
    touch_all_lines: bool,
) -> Result<(WireMeta, u64, u16, u64, u32, u32, u64, u64), bool> {
    let len = payload.len();
    if len < MIN_WIRE_PAYLOAD || len > MAX_WIRE_PAYLOAD || !len.is_multiple_of(8) {
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
    let (kv_key, burst_epoch) = unpack_key_epoch(u64::from_le_bytes(payload[24..32].try_into().unwrap()));
    let card_rx_tsc = u64::from_le_bytes(payload[32..40].try_into().unwrap());
    let cookie = u64::from_le_bytes(payload[40..48].try_into().unwrap());
    if cookie != wire_cookie(meta.global_seq, flow_id, flow_seq, len) {
        return Err(false);
    }

    let mut acc = cookie;
    if touch_all_lines && len > MIN_WIRE_PAYLOAD {
        // Touch one 8 B word per 64 B cache line (plus first & last body words) to trigger
        // authentic hardware LLC/DDIO cache hits or DRAM misses on every line of the packet.
        let end = len - 8;
        let mut off = 48usize;
        while off + 8 <= end {
            let wi = (off - 48) >> 3;
            let w = u64::from_le_bytes(payload[off..off + 8].try_into().unwrap());
            if w != payload_word(cookie, wi) {
                return Err(false);
            }
            acc ^= w.rotate_left((wi & 63) as u32);
            off += 64;
        }
        // Also verify last word before tail_tag
        let last_off = end - 8;
        let last_wi = (last_off - 48) >> 3;
        let lw = u64::from_le_bytes(payload[last_off..last_off + 8].try_into().unwrap());
        if lw != payload_word(cookie, last_wi) {
            return Err(false);
        }
        acc ^= lw;
    }
    Ok((meta, client_tsc, flow_id, flow_seq, kv_key, burst_epoch, card_rx_tsc, acc))
}

/// Write a compact 64 B ACK reply payload back to `mercury`.
#[inline(always)]
pub fn encode_wire_ack64(
    dst: &mut [u8],
    global_seq: u64,
    client_tsc: u64,
    flow_id: u16,
    flow_seq: u64,
    kv_key: u32,
    burst_epoch: u32,
    pkt_class: u8,
    path_tag: u8,
    flags: u8,
    host_aux: u64,
) {
    debug_assert!(dst.len() >= MIN_WIRE_PAYLOAD);
    let w0 = pack_meta(global_seq, MIN_WIRE_PAYLOAD, pkt_class, path_tag, OP_ACK, flags);
    let w2 = pack_flow_meta(flow_id, flow_seq);
    let w3 = pack_key_epoch(kv_key, burst_epoch);
    let cookie = wire_cookie(global_seq, flow_id, flow_seq, MIN_WIRE_PAYLOAD);
    dst[0..8].copy_from_slice(&w0.to_le_bytes());
    dst[8..16].copy_from_slice(&client_tsc.to_le_bytes());
    dst[16..24].copy_from_slice(&w2.to_le_bytes());
    dst[24..32].copy_from_slice(&w3.to_le_bytes());
    dst[32..40].copy_from_slice(&host_aux.to_le_bytes());
    dst[40..48].copy_from_slice(&cookie.to_le_bytes());
    dst[48..56].copy_from_slice(&payload_word(cookie, 0).to_le_bytes());
    dst[56..64].copy_from_slice(&w0.to_le_bytes());
}

// -----------------------------------------------------------------------------
// BAR4 Slot 15 fence-free MOVDIR64B signaling layout (under testbed::lease)
// -----------------------------------------------------------------------------

pub const BAR4_HOST_CREDIT_OFF: usize = 0x0000;
pub const BAR4_CEIO_PULL_OFF: usize = 0x0040;
pub const BAR4_CARD_META_OFF: usize = 0x0100;

pub const CREDIT_MAGIC: u64 = 0xC211_0001;
pub const PULL_MAGIC_16: u64 = 0xC212;

/// Exclusive 64 B BAR4 cache line written by host `cpu40` with fence-free `MOVDIR64B`.
/// Carries monotonic consumed counts for up to 4 SDP RX queues (`taken_q[0..4]`) in one
/// atomic 64 B posted write; CN106 ARM cores read it from local L3 (~10-20 ns) with
/// `taken_cached = max(taken_cached, new_taken)` for idempotency.
#[repr(C, align(64))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct HostCreditLine {
    pub magic_gen: u64,
    pub taken_q: [u64; 4],
    pub epoch_gen: u64,
    pub flags: u64,
    pub tail_magic_gen: u64,
}

const _: () = assert!(size_of::<HostCreditLine>() == 64 && align_of::<HostCreditLine>() == 64);

impl HostCreditLine {
    #[inline(always)]
    pub const fn new(seq_gen: u64, taken_q: [u64; 4], epoch_gen: u64, flags: u64) -> Self {
        let mg = (CREDIT_MAGIC << 32) | (seq_gen & 0xffff_ffff);
        HostCreditLine { magic_gen: mg, taken_q, epoch_gen, flags, tail_magic_gen: mg }
    }

    /// Tear-proof read from BAR4-mapped card DRAM (checks `w0 == w7` and magic).
    #[inline(always)]
    pub unsafe fn poll(ptr: *const u8) -> Option<Self> {
        let p = ptr.cast::<AtomicU64>();
        let w0 = unsafe { (*p).load(Ordering::Acquire) };
        if (w0 >> 32) != CREDIT_MAGIC {
            return None;
        }
        let w7 = unsafe { (*p.add(7)).load(Ordering::Acquire) };
        if w7 != w0 {
            return None;
        }
        let t0 = unsafe { (*p.add(1)).load(Ordering::Acquire) };
        let t1 = unsafe { (*p.add(2)).load(Ordering::Acquire) };
        let t2 = unsafe { (*p.add(3)).load(Ordering::Acquire) };
        let t3 = unsafe { (*p.add(4)).load(Ordering::Acquire) };
        let epoch_gen = unsafe { (*p.add(5)).load(Ordering::Acquire) };
        let flags = unsafe { (*p.add(6)).load(Ordering::Acquire) };
        let w0_recheck = unsafe { (*p).load(Ordering::Acquire) };
        if w0_recheck != w0 {
            return None;
        }
        Some(HostCreditLine {
            magic_gen: w0,
            taken_q: [t0, t1, t2, t3],
            epoch_gen,
            flags,
            tail_magic_gen: w7,
        })
    }

    /// Tear-proof read that strictly rejects stale credit lines from earlier RESET epochs.
    #[inline(always)]
    pub unsafe fn poll_for_epoch(ptr: *const u8, expected_epoch: u64) -> Option<Self> {
        let line = unsafe { Self::poll(ptr)? };
        if line.epoch_gen == expected_epoch { Some(line) } else { None }
    }
}

/// Exclusive 64 B BAR4 cache line written by host in `--mode ceio` via fence-free `MOVDIR64B`
/// to report CEIO §4.2 slow-path head pointers and `async_recv()` pull targets.
#[repr(C, align(64))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct CeioSlowPullLine {
    pub magic_gen: u64,
    pub slow_head_q: [u64; 4],
    pub pull_target_q: [u32; 4],
    pub tail_magic_gen: u64,
}

const _: () = assert!(size_of::<CeioSlowPullLine>() == 64 && align_of::<CeioSlowPullLine>() == 64);

impl CeioSlowPullLine {
    /// Pack `PULL_MAGIC_16` (bits 48..64), `epoch_gen & 0xffff` (bits 32..48), and `seq_gen` (bits 0..32)
    /// into `magic_gen` (mirrored at `tail_magic_gen` for 64 B tear-proof verification).
    #[inline(always)]
    pub const fn new(seq_gen: u64, epoch_gen: u64, slow_head_q: [u64; 4], pull_target_q: [u32; 4]) -> Self {
        let mg = (PULL_MAGIC_16 << 48) | ((epoch_gen & 0xffff) << 32) | (seq_gen & 0xffff_ffff);
        CeioSlowPullLine { magic_gen: mg, slow_head_q, pull_target_q, tail_magic_gen: mg }
    }

    #[inline(always)]
    pub const fn epoch_gen(&self) -> u64 {
        (self.magic_gen >> 32) & 0xffff
    }

    #[inline(always)]
    pub unsafe fn poll(ptr: *const u8) -> Option<Self> {
        let p = ptr.cast::<AtomicU64>();
        let w0 = unsafe { (*p).load(Ordering::Acquire) };
        if (w0 >> 48) != PULL_MAGIC_16 {
            return None;
        }
        let w7 = unsafe { (*p.add(7)).load(Ordering::Acquire) };
        if w7 != w0 {
            return None;
        }
        let h0 = unsafe { (*p.add(1)).load(Ordering::Acquire) };
        let h1 = unsafe { (*p.add(2)).load(Ordering::Acquire) };
        let h2 = unsafe { (*p.add(3)).load(Ordering::Acquire) };
        let h3 = unsafe { (*p.add(4)).load(Ordering::Acquire) };
        let p01 = unsafe { (*p.add(5)).load(Ordering::Acquire) };
        let p23 = unsafe { (*p.add(6)).load(Ordering::Acquire) };
        let w0_recheck = unsafe { (*p).load(Ordering::Acquire) };
        if w0_recheck != w0 {
            return None;
        }
        Some(CeioSlowPullLine {
            magic_gen: w0,
            slow_head_q: [h0, h1, h2, h3],
            pull_target_q: [p01 as u32, (p01 >> 32) as u32, p23 as u32, (p23 >> 32) as u32],
            tail_magic_gen: w7,
        })
    }

    /// Tear-proof read that strictly rejects stale slow-path pull lines from earlier RESET epochs.
    #[inline(always)]
    pub unsafe fn poll_for_epoch(ptr: *const u8, expected_epoch: u64) -> Option<Self> {
        let line = unsafe { Self::poll(ptr)? };
        if line.epoch_gen() == (expected_epoch & 0xffff) { Some(line) } else { None }
    }
}

/// Pitfall 1 safety verifier: ensures our logical credit limit `r_eff` and host refill
/// threshold (`DROQ_REFILL_THRESHOLD = 64`) can never drive `SDP` `SLIST_DBELL` below `WMARK`.
pub const fn verify_sdp_credit_safety(nb_desc: u16, r_eff: u64, credit_batch: u64) -> bool {
    if nb_desc < 256 || !nb_desc.is_power_of_two() {
        return false;
    }
    let wmark: u64 = if nb_desc < 2048 {
        let q = (nb_desc / 4) as u64;
        if q < 256 { q } else { 256 }
    } else {
        256
    };
    let usable_hw = (nb_desc as u64).saturating_sub(wmark);
    // Worst-case unrefilled host descriptors is DROQ_REFILL_THRESHOLD - 1 = 63.
    let worst_case_hw_occ = r_eff.saturating_add((DROQ_REFILL_THRESHOLD - 1) as u64);
    r_eff > 0 && credit_batch > 0 && credit_batch <= r_eff / 2 && worst_case_hw_occ < usable_hw
}

// -----------------------------------------------------------------------------
// Unified Single-Path O(1) Credit-Gated Priority FIFO (Our Architecture)
// -----------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, Default)]
pub struct UnifiedStats {
    pub direct_pushes: u64,
    pub queued_hi: u64,
    pub queued_lo: u64,
    pub credit_stalls: u64,
    pub locality_resets: u64,
    pub max_queue_depth: u64,
}

/// Per-host-ring O(1) credit gate with two-tier in-NIC priority FIFO (`hi` for RPC,
/// `lo` for bulk/burst). Strictly independent of flow count (`O(1)` per ring).
/// Resets backing queue locality when empty (Pitfall 3).
pub struct UnifiedCreditQueue<T> {
    r_eff: u64,
    prio_reserve: u64,
    pushed: u64,
    taken_cached: u64,
    q_hi: VecDeque<T>,
    q_lo: VecDeque<T>,
    pub stats: UnifiedStats,
}

impl<T> UnifiedCreditQueue<T> {
    pub fn new(r_eff: u64, prio_reserve: u64, cap_hint: usize) -> Self {
        assert!(r_eff > 0);
        let reserve = prio_reserve.min(r_eff / 2);
        UnifiedCreditQueue {
            r_eff,
            prio_reserve: reserve,
            pushed: 0,
            taken_cached: 0,
            q_hi: VecDeque::with_capacity(cap_hint.min(4096)),
            q_lo: VecDeque::with_capacity(cap_hint),
            stats: UnifiedStats::default(),
        }
    }

    #[inline(always)]
    pub fn reset(&mut self) {
        self.pushed = 0;
        self.taken_cached = 0;
        self.q_hi.clear();
        self.q_lo.clear();
        self.stats = UnifiedStats::default();
    }

    #[inline(always)]
    pub fn update_taken(&mut self, new_taken: u64) {
        if new_taken > self.taken_cached {
            self.taken_cached = new_taken;
        }
    }

    #[inline(always)]
    pub fn inflight(&self) -> u64 {
        self.pushed.saturating_sub(self.taken_cached)
    }

    #[inline(always)]
    pub fn queued_len(&self) -> usize {
        self.q_hi.len() + self.q_lo.len()
    }

    #[inline(always)]
    pub fn can_push_hi(&self) -> bool {
        self.inflight() < self.r_eff
    }

    #[inline(always)]
    pub fn can_push_lo(&self) -> bool {
        self.inflight() < self.r_eff.saturating_sub(self.prio_reserve)
    }

    /// Admit an incoming packet into the unified elastic queue.
    /// Returns `Some(pkt)` for immediate zero-queue direct push when queue is empty and credit
    /// is available; otherwise enqueues in on-NIC L3/DDR5 FIFO (`None`).
    #[inline(always)]
    pub fn admit(&mut self, pkt: T, is_hi_prio: bool) -> Option<T> {
        if is_hi_prio {
            if self.q_hi.is_empty() && self.can_push_hi() {
                self.pushed += 1;
                self.stats.direct_pushes += 1;
                return Some(pkt);
            }
            self.q_hi.push_back(pkt);
            self.stats.queued_hi += 1;
        } else {
            if self.q_hi.is_empty() && self.q_lo.is_empty() && self.can_push_lo() {
                self.pushed += 1;
                self.stats.direct_pushes += 1;
                return Some(pkt);
            }
            self.q_lo.push_back(pkt);
            self.stats.queued_lo += 1;
        }
        let d = (self.q_hi.len() + self.q_lo.len()) as u64;
        if d > self.stats.max_queue_depth {
            self.stats.max_queue_depth = d;
        }
        None
    }

    /// Pop next credit-authorized packet in priority-FIFO order (`q_hi` strictly before `q_lo`).
    #[inline(always)]
    pub fn pop_authorized(&mut self) -> Option<T> {
        if !self.q_hi.is_empty() {
            if self.can_push_hi() {
                let p = self.q_hi.pop_front();
                self.pushed += 1;
                self.maybe_locality_reset();
                return p;
            }
            self.stats.credit_stalls += 1;
            return None;
        }
        if !self.q_lo.is_empty() {
            if self.can_push_lo() {
                let p = self.q_lo.pop_front();
                self.pushed += 1;
                self.maybe_locality_reset();
                return p;
            }
            self.stats.credit_stalls += 1;
            return None;
        }
        None
    }

    #[inline(always)]
    fn maybe_locality_reset(&mut self) {
        if self.q_hi.is_empty() && self.q_lo.is_empty() {
            // Reset ring indices to slot 0 so repeated <= 32 MiB bursts stay pinned in hot card L3.
            self.q_hi.clear();
            self.q_lo.clear();
            self.stats.locality_resets += 1;
        }
    }
}

// -----------------------------------------------------------------------------
// Faithful CEIO §4.1-4.2 Reactive Dual-Path Control Plane & 2P1C SW Merge Ring
// -----------------------------------------------------------------------------

/// Per-flow RMT steering action (CEIO §4.1 Fig. 6 & §4.2 phase exclusivity).
pub const RMT_ACT_FAST: u32 = 0;
pub const RMT_ACT_SLOW: u32 = 1;
pub const RMT_ACT_PHASE_PAUSE: u32 = 2;

/// Lock-free RMT match-action table entry shared between datapath workers (`eth0` RX)
/// and the asynchronous CEIO Flow Controller core.
#[repr(C, align(64))]
pub struct RmtRuleEntry {
    /// Current steering action (`RMT_ACT_FAST`, `RMT_ACT_SLOW`, `RMT_ACT_PHASE_PAUSE`).
    pub action: AtomicU32,
    /// Lane (0..MAX_CARD_CORES-1) that last saw this flow.
    pub lane: AtomicU32,
    /// Monotonic fast-path hardware hit counter polled by CEIO Flow Controller (CEIO Fig. 6).
    pub fast_hits: AtomicU64,
    /// Monotonic count of packets diverted to on-NIC DRAM slow-path ring.
    pub slow_enq: AtomicU64,
    /// Monotonic count of slow-path packets drained to host.
    pub slow_drn: AtomicU64,
    /// Last arrival timestamp (ticks) for active-flow tracking.
    pub last_seen_tsc: AtomicU64,
    /// Timestamp (ticks) when fast-path credits logically ran out (0 if credits > 0).
    pub credit_exhausted_tsc: AtomicU64,
    /// Flow flags (e.g. lazy release / bulk indicator).
    pub flow_flags: AtomicU64,
    /// Lane fast-path sequence number of this flow's most recent fast packet (FAST->SLOW phase barrier).
    pub last_fast_lane_seq: AtomicU64,
    /// Lane slow-path sequence number of this flow's most recent drained slow packet (SLOW->FAST phase barrier).
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
            flow_flags: AtomicU64::new(0),
            last_fast_lane_seq: AtomicU64::new(0),
            last_slow_lane_seq: AtomicU64::new(0),
        }
    }

    pub fn reset(&self, default_action: u32) {
        self.action.store(default_action, Ordering::Relaxed);
        self.lane.store(0, Ordering::Relaxed);
        self.fast_hits.store(0, Ordering::Relaxed);
        self.slow_enq.store(0, Ordering::Relaxed);
        self.slow_drn.store(0, Ordering::Relaxed);
        self.last_seen_tsc.store(0, Ordering::Relaxed);
        self.credit_exhausted_tsc.store(0, Ordering::Relaxed);
        self.flow_flags.store(0, Ordering::Relaxed);
        self.last_fast_lane_seq.store(0, Ordering::Relaxed);
        self.last_slow_lane_seq.store(0, Ordering::Relaxed);
    }
}

/// Per-flow credit & phase state maintained exclusively by the CEIO Flow Controller
/// (implements CEIO SIGCOMM '25 Algorithm 1 + §4.1 Q1-Q3 + §4.2 phase exclusivity).
#[derive(Clone, Copy, Debug)]
pub struct CeioFlowState {
    pub flow_id: u16,
    pub active: bool,
    pub in_slow: bool,
    pub phase_paused: bool,
    pub phase_pause_start_tsc: u64,
    pub credits: i64,
    pub owed_total: i64, // sum_j o_{ij} in Algorithm 1 (flow i owes credits to new flows)
    pub last_hits: u64,
    pub fast_inflight: u64,
    pub last_active_tsc: u64,
    pub pending_switch_to_slow_at: u64,
    pub pending_switch_to_fast_at: u64,
}

impl Default for CeioFlowState {
    fn default() -> Self {
        CeioFlowState {
            flow_id: 0,
            active: false,
            in_slow: false,
            phase_paused: false,
            phase_pause_start_tsc: 0,
            credits: 0,
            owed_total: 0,
            last_hits: 0,
            fast_inflight: 0,
            last_active_tsc: 0,
            pending_switch_to_slow_at: 0,
            pending_switch_to_fast_at: 0,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct CeioControllerConfig {
    /// Total LLC credit pool `C_total` (CEIO Eq. 1; matches Unified total R_eff).
    pub c_total: u64,
    /// Maximum concurrent fast-path flows sharing `C_total` before new flows start in slow path.
    pub max_fast_flows: usize,
    /// RMT rule rewrite latency in ticks (e.g., 0 for software table or realistic BF3/DOCA lag).
    pub rmt_rewrite_ticks: u64,
    /// Flow inactivity timeout in ticks (CEIO §4.1 Q3).
    pub inactive_timeout_ticks: u64,
    /// Round-robin reactivation period in ticks and batch size (CEIO §4.1 Q3 & §6.3 Fig. 12).
    pub reactivate_period_ticks: u64,
    pub reactivate_batch: usize,
    /// Minimum phase-exclusivity drain confirm hold in ticks (0 = wait purely for host slow_head ack).
    pub phase_pause_min_ticks: u64,
    /// Lazy credit release batch size (CEIO §4.1).
    pub lazy_release_batch: u64,
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

/// Full CEIO §4.1-4.2 Flow Controller implementing Algorithm 1 credit assignment/debt
/// redistribution, active/inactive reclamation, round-robin reactivation, and phase exclusivity.
pub struct CeioFlowController {
    pub cfg: CeioControllerConfig,
    pub flows: Vec<CeioFlowState>,
    /// Active fast-path flow IDs (bounded by `max_fast_flows`).
    active_list: Vec<u16>,
    /// Inactive / slow-path flow queue awaiting round-robin reactivation (CEIO §4.1 Q3).
    inactive_rr: VecDeque<u16>,
    in_inactive_rr: Vec<bool>,
    /// Under-allocated flows `I` in CEIO Algorithm 1 (owe credits to newer flows).
    debt_set: Vec<u16>,
    free_credits: i64,
    last_taken_per_lane: [u64; MAX_CARD_CORES],
    last_slow_head_per_lane: [u64; MAX_CARD_CORES],
    lane_slow_drn_base: [u64; MAX_CARD_CORES],
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
            last_taken_per_lane: [0; MAX_CARD_CORES],
            last_slow_head_per_lane: [0; MAX_CARD_CORES],
            lane_slow_drn_base: [0; MAX_CARD_CORES],
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
        self.last_taken_per_lane = [0; MAX_CARD_CORES];
        self.last_slow_head_per_lane = [0; MAX_CARD_CORES];
        self.lane_slow_drn_base = [0; MAX_CARD_CORES];
        self.last_rr_tsc = 0;
        self.stats = CeioControllerStats::default();
        for (i, st) in self.flows.iter_mut().enumerate() {
            *st = CeioFlowState::default();
            st.flow_id = i as u16;
        }
        // New unseen flows start with default FAST rule up to max_fast_flows, or SLOW if > capacity.
        for (i, e) in rmt.iter().enumerate() {
            let init_act = if i < self.cfg.max_fast_flows { RMT_ACT_FAST } else { RMT_ACT_SLOW };
            e.reset(init_act);
        }
    }

    /// Admit a newly observed flow using CEIO Algorithm 1 (Eq. 2 + lines 1-15).
    fn admit_new_flow(&mut self, fid: u16, now_tsc: u64, rmt: &[RmtRuleEntry]) {
        let idx = fid as usize;
        if self.flows[idx].active {
            return;
        }
        if self.active_list.len() >= self.cfg.max_fast_flows {
            // Active fast-path table full: place in slow path & enqueue for round-robin reactivation.
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
        let c_flow = ((self.cfg.c_total as i64) / n_new).max(1);
        let mut granted: i64 = 0;

        // First draw from unallocated free credit pool.
        let take_free = self.free_credits.min(c_flow);
        self.free_credits -= take_free;
        granted += take_free;

        // CEIO Algorithm 1 lines 3-15: redistribute `(1/n) * C_flow` from each existing flow.
        if granted < c_flow && n_old > 0 {
            let needed = c_flow - granted;
            let per_existing = (needed + n_old - 1) / n_old;
            for &efid in &self.active_list {
                let est = &mut self.flows[efid as usize];
                if est.credits >= per_existing {
                    est.credits -= per_existing;
                    granted += per_existing;
                } else {
                    let avail = est.credits.max(0);
                    let short = per_existing - avail;
                    est.credits -= avail;
                    granted += avail;
                    if short > 0 {
                        est.owed_total += short;
                        if !self.debt_set.contains(&efid) {
                            self.debt_set.push(efid);
                        }
                        self.stats.debt_events += 1;
                    }
                }
                if granted >= c_flow {
                    break;
                }
            }
        }

        let st = &mut self.flows[idx];
        st.active = true;
        st.in_slow = false;
        st.phase_paused = false;
        st.credits = granted.max(1);
        st.owed_total = 0;
        st.last_active_tsc = now_tsc;
        self.active_list.push(fid);
        rmt[idx].action.store(RMT_ACT_FAST, Ordering::Release);
        self.stats.credit_reallocs += 1;
        if (self.active_list.len() as u64) > self.stats.active_flows_peak {
            self.stats.active_flows_peak = self.active_list.len() as u64;
        }
    }

    /// CEIO Algorithm 1 lines 16-25: release `gamma` credits back to flow `fid`,
    /// first paying down any owed debt `o_{ij}` before replenishing `c_i`.
    fn release_credits(&mut self, fid: u16, mut gamma: i64) {
        if gamma <= 0 {
            return;
        }
        let idx = fid as usize;
        let owed = self.flows[idx].owed_total;
        if owed > 0 {
            let pay = owed.min(gamma);
            self.flows[idx].owed_total -= pay;
            gamma -= pay;
            // Redistribute repaid debt into free pool / newest flows.
            self.free_credits += pay;
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

    /// One complete reactive poll cycle of the CEIO Flow Controller.
    /// Reads RMT hardware hit counters, host fast-path `taken_q`, and host slow-path `slow_head_q`,
    /// performs credit debit/release, RMT rule updates (FAST <-> SLOW), phase-exclusivity
    /// pause-and-drain confirmation, and round-robin inactive flow reactivation.
    pub fn poll_step(
        &mut self,
        now_tsc: u64,
        rmt: &[RmtRuleEntry],
        host_taken_q: &[u64; MAX_CARD_CORES],
        host_slow_head_q: &[u64; MAX_CARD_CORES],
        observed_flows: &[u16],
    ) {
        self.stats.ctrl_loops += 1;

        // 1. Admit any newly observed flows from datapath notification ring.
        for &fid in observed_flows {
            if (fid as usize) < MAX_FLOWS && !self.flows[fid as usize].active {
                self.admit_new_flow(fid, now_tsc, rmt);
            }
        }

        // 2. Compute per-lane host fast-path credit releases since last poll (CEIO Fig. 6).
        let mut lane_released = [0u64; MAX_CARD_CORES];
        for l in 0..MAX_CARD_CORES {
            if host_taken_q[l] > self.last_taken_per_lane[l] {
                lane_released[l] = host_taken_q[l] - self.last_taken_per_lane[l];
                self.last_taken_per_lane[l] = host_taken_q[l];
            }
            if host_slow_head_q[l] > self.last_slow_head_per_lane[l] {
                self.last_slow_head_per_lane[l] = host_slow_head_q[l];
            }
        }

        // 3. Scan active flows: debit RMT hit deltas, credit host releases, trigger FAST -> SLOW.
        let mut lane_active_inflight = [0u64; MAX_CARD_CORES];
        let mut to_reclaim: Vec<u16> = Vec::new();
        let active_snapshot_len = self.active_list.len();
        for ai in 0..active_snapshot_len {
            let fid = self.active_list[ai];
            let idx = fid as usize;
            let entry = &rmt[idx];
            let hits = entry.fast_hits.load(Ordering::Acquire);
            let last_seen = entry.last_seen_tsc.load(Ordering::Relaxed);
            let lane = (entry.lane.load(Ordering::Relaxed) as usize).min(MAX_CARD_CORES - 1);

            let delta_hits = hits.saturating_sub(self.flows[idx].last_hits);
            if delta_hits > 0 {
                self.flows[idx].last_hits = hits;
                self.flows[idx].fast_inflight += delta_hits;
                self.flows[idx].last_active_tsc = now_tsc.max(last_seen);
                let prev_cred = self.flows[idx].credits;
                self.flows[idx].credits -= delta_hits as i64;
                if self.flows[idx].credits < 0 {
                    // Packets arrived during reactive monitoring/rule-rewrite lag after credits hit 0!
                    let overshoot = if prev_cred > 0 {
                        (-self.flows[idx].credits) as u64
                    } else {
                        delta_hits
                    };
                    self.stats.transient_llc_leak_pkts += overshoot;
                    if entry.credit_exhausted_tsc.load(Ordering::Relaxed) == 0 {
                        entry.credit_exhausted_tsc.store(now_tsc, Ordering::Relaxed);
                    }
                }
            }
            lane_active_inflight[lane] += self.flows[idx].fast_inflight;
        }

        // Proportionally attribute per-lane host fast-path releases to flows on that lane
        // (matching lazy credit release of fast-path ring head pointer updates in CEIO §4.1-4.2),
        // capping per-flow credits at `c_flow_cap` so sibling flows on the same lane cannot starve.
        let c_flow_cap = ((self.cfg.c_total as i64) / (active_snapshot_len.max(1) as i64)).max(1);
        for ai in 0..active_snapshot_len {
            let fid = self.active_list[ai];
            let idx = fid as usize;
            let lane = (rmt[idx].lane.load(Ordering::Relaxed) as usize).min(MAX_CARD_CORES - 1);
            let rel_lane = lane_released[lane];
            if rel_lane > 0 && self.flows[idx].fast_inflight > 0 {
                let total_inf = lane_active_inflight[lane].max(1);
                let mut share = ((rel_lane as u128 * self.flows[idx].fast_inflight as u128) / (total_inf as u128)) as u64;
                if share == 0 {
                    share = rel_lane.min(self.flows[idx].fast_inflight);
                }
                let headroom = (c_flow_cap - self.flows[idx].credits).max(0) as u64 + self.flows[idx].owed_total.max(0) as u64;
                share = share.min(self.flows[idx].fast_inflight).min(lane_released[lane]).min(headroom);
                if share > 0 {
                    self.flows[idx].fast_inflight -= share;
                    lane_released[lane] -= share;
                    lane_active_inflight[lane] = lane_active_inflight[lane].saturating_sub(share);
                    self.release_credits(fid, share as i64);
                }
            }
        }
        // Second pass: attribute any remaining lane releases to active flows on that lane still below c_flow_cap.
        for ai in 0..active_snapshot_len {
            let fid = self.active_list[ai];
            let idx = fid as usize;
            let lane = (rmt[idx].lane.load(Ordering::Relaxed) as usize).min(MAX_CARD_CORES - 1);
            let rel_lane = lane_released[lane];
            if rel_lane > 0 {
                let need = (c_flow_cap - self.flows[idx].credits).max(0) as u64 + self.flows[idx].owed_total.max(0) as u64;
                let give = rel_lane.min(need);
                if give > 0 {
                    self.flows[idx].fast_inflight = self.flows[idx].fast_inflight.saturating_sub(give);
                    lane_released[lane] -= give;
                    self.release_credits(fid, give as i64);
                }
            }
        }
        // Any remaining lane release (e.g. from flows already reclaimed) returns to free_credits (capped at c_total).
        let mut total_assigned: i64 = self.free_credits;
        for &fid in &self.active_list {
            total_assigned += self.flows[fid as usize].credits.max(0);
        }
        let cap_i64 = self.cfg.c_total as i64;
        if total_assigned < cap_i64 {
            for &rem in &lane_released {
                if rem > 0 {
                    let room = (cap_i64 - total_assigned).max(0);
                    let add = (rem as i64).min(room);
                    self.free_credits += add;
                    total_assigned += add;
                }
            }
        }
        // Redistribute unallocated free_credits back to active flows below c_flow_cap so repaid debt / reclaimed
        // credits cannot strand permanently while active flows wait to re-enter fast path.
        if self.free_credits > 0 && active_snapshot_len > 0 {
            for ai in 0..active_snapshot_len {
                if self.free_credits <= 0 {
                    break;
                }
                let idx = self.active_list[ai] as usize;
                let need = (c_flow_cap - self.flows[idx].credits).max(0);
                if need > 0 {
                    let give = need.min(self.free_credits);
                    self.flows[idx].credits += give;
                    self.free_credits -= give;
                }
            }
        }

        // 4. Execute FAST -> SLOW switches, phase-exclusivity drain checks, and inactive reclamation.
        let resume_thresh = (c_flow_cap / 2).max(1);
        for ai in 0..active_snapshot_len {
            let fid = self.active_list[ai];
            let idx = fid as usize;
            let entry = &rmt[idx];
            let lane = (entry.lane.load(Ordering::Relaxed) as usize).min(MAX_CARD_CORES - 1);
            let st = &mut self.flows[idx];

            if !st.in_slow && st.credits <= 0 {
                if st.pending_switch_to_slow_at == 0 {
                    st.pending_switch_to_slow_at = now_tsc.saturating_add(self.cfg.rmt_rewrite_ticks);
                }
                if now_tsc >= st.pending_switch_to_slow_at {
                    // Complete RMT rewrite: FAST -> SLOW + activate phase exclusivity!
                    st.in_slow = true;
                    st.phase_paused = true;
                    st.phase_pause_start_tsc = now_tsc;
                    st.pending_switch_to_slow_at = 0;
                    st.pending_switch_to_fast_at = 0;
                    entry.action.store(RMT_ACT_SLOW, Ordering::Release);
                    self.stats.rmt_fast_to_slow += 1;
                    self.stats.phase_pauses += 1;
                }
            } else if !st.in_slow && st.credits > 0 {
                st.pending_switch_to_slow_at = 0;
                entry.credit_exhausted_tsc.store(0, Ordering::Relaxed);
            } else if st.in_slow {
                // Step 1: once fast credits recover (>= resume_thresh) & min_hold_ok, enter RMT_ACT_PHASE_PAUSE
                //         so arriving packets stop extending slow_ring while existing slow packets drain!
                // Step 2: once card slow_ring drained (slow_drn >= slow_enq), host slow_head caught up,
                //         and RMT rewrite latency elapsed, flip back to RMT_ACT_FAST!
                let slow_enq = entry.slow_enq.load(Ordering::Acquire);
                let slow_drn = entry.slow_drn.load(Ordering::Acquire);
                let card_drained = slow_drn >= slow_enq;
                let host_confirmed = self.last_slow_head_per_lane[lane] >= self.lane_slow_drn_base[lane]
                    && self.last_slow_head_per_lane[lane] >= entry.last_slow_lane_seq.load(Ordering::Acquire);
                let min_hold_ok = now_tsc.saturating_sub(st.phase_pause_start_tsc) >= self.cfg.phase_pause_min_ticks;
                if st.pending_switch_to_fast_at == 0 {
                    if min_hold_ok && st.credits >= resume_thresh {
                        entry.action.store(RMT_ACT_PHASE_PAUSE, Ordering::Release);
                        st.pending_switch_to_fast_at = now_tsc.saturating_add(self.cfg.rmt_rewrite_ticks);
                    } else if !card_drained {
                        self.lane_slow_drn_base[lane] = self.last_slow_head_per_lane[lane].saturating_add(1);
                    }
                } else if card_drained && host_confirmed && now_tsc >= st.pending_switch_to_fast_at {
                    let pause_ticks = now_tsc.saturating_sub(st.phase_pause_start_tsc);
                    self.stats.phase_pause_ticks_total += pause_ticks;
                    if pause_ticks > self.stats.max_phase_pause_ticks {
                        self.stats.max_phase_pause_ticks = pause_ticks;
                    }
                    st.in_slow = false;
                    st.phase_paused = false;
                    st.pending_switch_to_fast_at = 0;
                    entry.credit_exhausted_tsc.store(0, Ordering::Relaxed);
                    entry.action.store(RMT_ACT_FAST, Ordering::Release);
                    self.stats.rmt_slow_to_fast += 1;
                }
            }

            // Reclaim credits from inactive flows (CEIO §4.1 Q3).
            if self.cfg.inactive_timeout_ticks > 0
                && now_tsc.saturating_sub(st.last_active_tsc) >= self.cfg.inactive_timeout_ticks
                && !st.phase_paused
                && st.fast_inflight == 0
            {
                to_reclaim.push(fid);
            }
        }

        for fid in to_reclaim {
            let idx = fid as usize;
            if let Some(pos) = self.active_list.iter().position(|&x| x == fid) {
                self.active_list.swap_remove(pos);
            }
            let reclaimed = self.flows[idx].credits.max(0);
            self.free_credits += reclaimed;
            self.flows[idx].credits = 0;
            self.flows[idx].active = false;
            self.flows[idx].in_slow = true;
            rmt[idx].action.store(RMT_ACT_SLOW, Ordering::Release);
            if !self.in_inactive_rr[idx] {
                self.in_inactive_rr[idx] = true;
                self.inactive_rr.push_back(fid);
            }
        }

        // 5. Periodic round-robin re-activation of inactive/slow flows (CEIO §4.1 Q3 & §6.3 Fig. 12).
        if self.cfg.reactivate_period_ticks == 0
            || now_tsc.saturating_sub(self.last_rr_tsc) >= self.cfg.reactivate_period_ticks
        {
            self.last_rr_tsc = now_tsc;
            let mut moved = 0usize;
            while moved < self.cfg.reactivate_batch && self.active_list.len() < self.cfg.max_fast_flows {
                let Some(fid) = self.inactive_rr.pop_front() else {
                    break;
                };
                self.in_inactive_rr[fid as usize] = false;
                if !self.flows[fid as usize].active {
                    self.admit_new_flow(fid, now_tsc, rmt);
                    self.stats.rr_reactivations += 1;
                    moved += 1;
                }
            }
        }
    }

    pub fn active_flow_count(&self) -> usize {
        self.active_list.len()
    }
}

// -----------------------------------------------------------------------------
// Host-Side CEIO §4.2 Two-Producer One-Consumer (2P1C) Software Merge Ring
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
    pub async_pull_reqs: u64,
    pub sw_ring_full_stalls: u64,
}

/// CEIO §4.2 2P1C Software Ring unifying Fast-Path HW ring (`SDP` Q0..3) and
/// Slow-Path HW ring (`SDP` Q4..7) into one ordered application-facing `async_recv()` ring.
/// Enforces phase-ordered merging: drains all available fast-path packets of the current
/// phase before switching producer to slow-path packets.
enum SwRingSlot<T> {
    Empty(#[allow(dead_code)] u8),
    Filled(SwRingEntry<T>),
}

pub struct SwMergeRing2P1C<T> {
    buf: Vec<SwRingSlot<T>>,
    head: usize,
    tail: usize,
    count: usize,
    cap: usize,
    active_producer: SwProducer,
    pub stats: SwMergeStats,
}

impl<T> SwMergeRing2P1C<T> {
    pub fn new(cap: usize) -> Self {
        assert!(cap > 0);
        let mut buf = Vec::with_capacity(cap);
        // Non-zero Empty(1) payload forces Linux to fault every backing page up front (zero hot-path page faults).
        for _ in 0..cap {
            buf.push(SwRingSlot::Empty(1));
        }
        std::hint::black_box(&buf);
        SwMergeRing2P1C {
            buf,
            head: 0,
            tail: 0,
            count: 0,
            cap,
            active_producer: SwProducer::FastPath,
            stats: SwMergeStats::default(),
        }
    }

    pub fn reset(&mut self) {
        while self.count > 0 {
            self.buf[self.head] = SwRingSlot::Empty(1);
            self.head += 1;
            if self.head == self.cap {
                self.head = 0;
            }
            self.count -= 1;
        }
        self.head = 0;
        self.tail = 0;
        self.active_producer = SwProducer::FastPath;
        self.stats = SwMergeStats::default();
    }

    #[inline(always)]
    pub fn len(&self) -> usize {
        self.count
    }

    #[inline(always)]
    pub fn is_empty(&self) -> bool {
        self.count == 0
    }

    #[inline(always)]
    pub fn push_from(&mut self, producer: SwProducer, lane: u8, item: T) -> Result<(), T> {
        if self.count >= self.cap {
            self.stats.sw_ring_full_stalls += 1;
            return Err(item);
        }
        if producer != self.active_producer {
            self.active_producer = producer;
            self.stats.phase_switches += 1;
        }
        match producer {
            SwProducer::FastPath => self.stats.fast_merged += 1,
            SwProducer::SlowPath => self.stats.slow_merged += 1,
        }
        self.buf[self.tail] = SwRingSlot::Filled(SwRingEntry { producer, lane, item });
        self.tail += 1;
        if self.tail == self.cap {
            self.tail = 0;
        }
        self.count += 1;
        Ok(())
    }

    #[inline(always)]
    pub fn pop(&mut self) -> Option<SwRingEntry<T>> {
        if self.count == 0 {
            return None;
        }
        let slot = std::mem::replace(&mut self.buf[self.head], SwRingSlot::Empty(1));
        self.head += 1;
        if self.head == self.cap {
            self.head = 0;
        }
        self.count -= 1;
        match slot {
            SwRingSlot::Filled(e) => Some(e),
            SwRingSlot::Empty(_) => None,
        }
    }
}

/// Per-flow strict ordering verifier (tracks highest seen `flow_seq` across 16,384 flows).
pub struct FlowOrderTracker {
    expected: Vec<u64>,
    pub ooo_count: u64,
    pub dup_count: u64,
    pub gap_count: u64,
}

impl FlowOrderTracker {
    pub fn new() -> Self {
        FlowOrderTracker { expected: vec![0u64; MAX_FLOWS], ooo_count: 0, dup_count: 0, gap_count: 0 }
    }

    pub fn reset(&mut self) {
        self.expected.fill(0);
        self.ooo_count = 0;
        self.dup_count = 0;
        self.gap_count = 0;
    }

    #[inline(always)]
    pub fn observe(&mut self, flow_id: u16, flow_seq: u64) {
        let idx = (flow_id as usize) & (MAX_FLOWS - 1);
        let exp = self.expected[idx];
        if exp == 0 {
            self.expected[idx] = flow_seq + 1;
            return;
        }
        if flow_seq == exp {
            self.expected[idx] = flow_seq + 1;
        } else if flow_seq < exp {
            self.ooo_count += 1;
            if flow_seq + 1 == exp {
                self.dup_count += 1;
            }
        } else {
            self.gap_count += flow_seq - exp;
            self.expected[idx] = flow_seq + 1;
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
    let mut i = 0usize;
    while i < 256 {
        let mut c = i as u32;
        let mut k = 0;
        while k < 8 {
            if (c & 1) != 0 {
                c = 0xedb8_8320 ^ (c >> 1);
            } else {
                c >>= 1;
            }
            k += 1;
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
    use core::arch::aarch64::{__crc32b, __crc32d};
    let mut ptr = data.as_ptr();
    let mut len = data.len();
    while len >= 8 {
        let v = unsafe { ptr.cast::<u64>().read_unaligned() };
        crc = __crc32d(crc, v);
        ptr = unsafe { ptr.add(8) };
        len -= 8;
    }
    while len > 0 {
        let b = unsafe { *ptr };
        crc = __crc32b(crc, b);
        ptr = unsafe { ptr.add(1) };
        len -= 1;
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
    pub crc_seed: u32,
    pub payload_len: usize,
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

        let ip_tot = (20 + 8 + 12 + 8 + payload_len + ROCE_ICRC_LEN) as u16;
        hdr[14] = 0x45;
        hdr[15] = 0x00;
        hdr[16..18].copy_from_slice(&ip_tot.to_be_bytes());
        hdr[18..20].copy_from_slice(&0x2110u16.to_be_bytes());
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

        let udp_len = (8 + 12 + 8 + payload_len + ROCE_ICRC_LEN) as u16;
        hdr[34..36].copy_from_slice(&ROCE_UDP_PORT.to_be_bytes());
        hdr[36..38].copy_from_slice(&ROCE_UDP_PORT.to_be_bytes());
        hdr[38..40].copy_from_slice(&udp_len.to_be_bytes());
        hdr[40..42].copy_from_slice(&0u16.to_be_bytes());

        hdr[42] = 0x64;
        hdr[43] = 0x00;
        hdr[44..46].copy_from_slice(&0xffffu16.to_be_bytes());
        hdr[46..50].copy_from_slice(&(dst_qpn & 0x00ff_ffff).to_be_bytes());
        hdr[50..54].copy_from_slice(&0u32.to_be_bytes());

        hdr[54..58].copy_from_slice(&ROCE_QKEY.to_be_bytes());
        hdr[58..62].copy_from_slice(&(ROCE_CARD_QPN & 0x00ff_ffff).to_be_bytes());

        let mut masked = [0u8; 56];
        masked[0..8].fill(0xff);
        masked[8..56].copy_from_slice(&hdr[14..62]);
        masked[8 + 1] = 0xff;
        masked[8 + 8] = 0xff;
        masked[8 + 10] = 0xff;
        masked[8 + 11] = 0xff;
        masked[8 + 20 + 6] = 0xff;
        masked[8 + 20 + 7] = 0xff;
        masked[8 + 28 + 4] = 0xff;

        let crc_seed = crc32_sw_update(0xffff_ffff, &masked);
        RoceTemplate { hdr, crc_seed, payload_len }
    }

    #[inline(always)]
    pub fn icrc(&self, payload: &[u8]) -> u32 {
        !crc32_update(self.crc_seed, payload)
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
    bins: Vec<u64>,
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
        FastHist { bins: vec![0u64; TOTAL_BINS], count: 0, sum_ns: 0, min_ns: u64::MAX, max_ns: 0 }
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
        for (a, &b) in self.bins.iter_mut().zip(other.bins.iter()) {
            *a += b;
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

    pub fn max_us(&self) -> f64 {
        self.max_ns as f64 / 1000.0
    }

    pub fn mean_us(&self) -> f64 {
        if self.count == 0 { 0.0 } else { (self.sum_ns as f64) / (self.count as f64) / 1000.0 }
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

// -----------------------------------------------------------------------------
// Comprehensive offline unit tests (executed on Socket 0, zero hardware access)
// -----------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wire_roundtrip_and_tear_detection() {
        for &len in &[64usize, 256, 512, 1024, 1408] {
            let mut buf = vec![0u8; len];
            encode_wire_pkt(
                &mut buf,
                0x1_2345_6789,
                99887766,
                4097,
                42,
                1234,
                7,
                CLASS_RPC_HI,
                PATH_UNIFIED,
                OP_DATA,
                FLAG_KV_LOOKUP,
            );
            stamp_card_meta(&mut buf, PATH_CEIO_FAST, FLAG_CEIO_LLC_LEAK, 555_000);
            let parsed = verify_and_touch_wire_pkt(&buf, true).expect("valid packet");
            assert_eq!(parsed.0.global_seq, 0x1_2345_6789);
            assert_eq!(parsed.0.len_bytes, len);
            assert_eq!(parsed.0.pkt_class, CLASS_RPC_HI);
            assert_eq!(parsed.0.path_tag, PATH_CEIO_FAST);
            assert_eq!(parsed.0.op, OP_DATA);
            assert_eq!(parsed.0.flags, FLAG_KV_LOOKUP | FLAG_CEIO_LLC_LEAK);
            assert_eq!(parsed.1, 99887766);
            assert_eq!(parsed.2, 4097);
            assert_eq!(parsed.3, 42);
            assert_eq!(parsed.4, 1234);
            assert_eq!(parsed.5, 7);
            assert_eq!(parsed.6, 555_000);

            // Corrupt trailing tag -> must detect as torn (Err(true))
            buf[len - 1] ^= 0xff;
            assert_eq!(verify_and_touch_wire_pkt(&buf, true), Err(true));
        }
    }

    #[test]
    fn bar4_credit_and_pull_lines_tear_proof() {
        let line = HostCreditLine::new(17, [100, 200, 300, 400], 3, 1);
        let ptr = (&raw const line).cast::<u8>();
        let got = unsafe { HostCreditLine::poll(ptr) }.expect("valid credit line");
        assert_eq!(got, line);

        let pull = CeioSlowPullLine::new(9, 3, [10, 20, 30, 40], [128, 256, 384, 512]);
        let pptr = (&raw const pull).cast::<u8>();
        let got_p = unsafe { CeioSlowPullLine::poll_for_epoch(pptr, 3) }.expect("valid pull line");
        assert_eq!(got_p, pull);
    }

    #[test]
    fn stale_epoch_bar4_credit_and_pull_lines_rejected() {
        let mut uq: UnifiedCreditQueue<u64> = UnifiedCreditQueue::new(32, 0, 128);
        for s in 1..=32u64 {
            assert!(uq.admit(s, false).is_some());
        }
        // Credit gate is now full (inflight == 32).
        assert!(uq.admit(33, false).is_none());

        // Feed a stale epoch=1 HostCreditLine carrying pre-reset taken=10_000_000 while card is on epoch=2.
        let stale_credit = HostCreditLine::new(999, [10_000_000; 4], 1, 1);
        let cptr = (&raw const stale_credit).cast::<u8>();
        assert!(unsafe { HostCreditLine::poll_for_epoch(cptr, 2) }.is_none());
        if let Some(cl) = unsafe { HostCreditLine::poll_for_epoch(cptr, 2) } {
            uq.update_taken(cl.taken_q[0]);
        }
        // Gate must still hold tightly!
        assert_eq!(uq.inflight(), 32);
        assert!(uq.pop_authorized().is_none());

        // Feed a matching epoch=2 line -> gate advances cleanly.
        let fresh_credit = HostCreditLine::new(1, [16, 0, 0, 0], 2, 1);
        let fptr = (&raw const fresh_credit).cast::<u8>();
        let cl = unsafe { HostCreditLine::poll_for_epoch(fptr, 2) }.expect("matching epoch accepted");
        uq.update_taken(cl.taken_q[0]);
        assert_eq!(uq.inflight(), 16);
        assert_eq!(uq.pop_authorized(), Some(33));

        // Verify stale epoch CeioSlowPullLine is also strictly rejected.
        let stale_pull = CeioSlowPullLine::new(500, 1, [5_000_000; 4], [5_000_128; 4]);
        let pptr = (&raw const stale_pull).cast::<u8>();
        assert!(unsafe { CeioSlowPullLine::poll_for_epoch(pptr, 2) }.is_none());
        let fresh_pull = CeioSlowPullLine::new(1, 2, [0; 4], [128; 4]);
        let fpptr = (&raw const fresh_pull).cast::<u8>();
        assert_eq!(unsafe { CeioSlowPullLine::poll_for_epoch(fpptr, 2) }, Some(fresh_pull));
    }

    #[test]
    fn pitfall1_sdp_wmark_safety_check() {
        assert!(verify_sdp_credit_safety(512, 256, 32));
        assert!(verify_sdp_credit_safety(512, 256, 64));
        assert!(verify_sdp_credit_safety(512, 128, 32));
        // Unsafe: R_eff=350 + 63 = 413 >= 512 - 128 = 384 (would trigger WMARK!)
        assert!(!verify_sdp_credit_safety(512, 350, 32));
        // Unsafe: batch > R_eff/2 (deadlock risk)
        assert!(!verify_sdp_credit_safety(512, 128, 96));
    }

    #[test]
    fn unified_o1_credit_priority_and_fifo_invariants() {
        let mut q: UnifiedCreditQueue<(u8, u64)> = UnifiedCreditQueue::new(64, 16, 256);
        let mut out_hi = Vec::new();
        let mut out_lo = Vec::new();

        // Blast 100 bulk packets: first 48 (= 64 - 16 reserve) push directly, 52 queue in NIC FIFO.
        for s in 1..=100u64 {
            if let Some((_, seq)) = q.admit((CLASS_BULK, s), false) {
                out_lo.push(seq);
            }
        }
        assert_eq!(out_lo.len(), 48);
        assert_eq!(q.queued_len(), 52);

        // Even while bulk is saturated, 16 latency-sensitive RPC packets pass immediately!
        for s in 1..=16u64 {
            let p = q.admit((CLASS_RPC_HI, s), true);
            assert!(p.is_some(), "RPC seq {s} must bypass bulk via prio_reserve");
            out_hi.push(p.unwrap().1);
        }
        assert_eq!(q.inflight(), 64);

        // 17th RPC queues in q_hi and drains strictly ahead of queued bulk packets.
        assert!(q.admit((CLASS_RPC_HI, 17), true).is_none());
        q.update_taken(32);
        let first_pop = q.pop_authorized().unwrap();
        assert_eq!(first_pop, (CLASS_RPC_HI, 17));
        out_hi.push(first_pop.1);

        // Drain the rest in strict FIFO order.
        let mut taken = 32u64;
        while q.queued_len() > 0 {
            if let Some((cls, seq)) = q.pop_authorized() {
                if cls == CLASS_RPC_HI {
                    out_hi.push(seq);
                } else {
                    out_lo.push(seq);
                }
            } else {
                taken += 32;
                q.update_taken(taken);
            }
        }
        assert_eq!(out_hi, (1..=17).collect::<Vec<_>>());
        assert_eq!(out_lo, (1..=100).collect::<Vec<_>>());
        assert!(q.stats.locality_resets >= 1);
    }

    #[test]
    fn ceio_burst_onset_leakage_and_phase_pause_and_2p1c_ordering() {
        let rmt: Vec<RmtRuleEntry> = (0..MAX_FLOWS).map(|_| RmtRuleEntry::new()).collect();
        let cfg = CeioControllerConfig {
            c_total: 64,
            max_fast_flows: 64,
            rmt_rewrite_ticks: 100,
            inactive_timeout_ticks: 100_000,
            reactivate_period_ticks: 500,
            reactivate_batch: 8,
            phase_pause_min_ticks: 50,
            lazy_release_batch: 16,
        };
        let mut ctrl = CeioFlowController::new(cfg);
        ctrl.reset(&rmt);

        let mut sw = SwMergeRing2P1C::<u64>::new(256);
        let mut order = FlowOrderTracker::new();
        let mut slow_buf: VecDeque<u64> = VecDeque::new();
        let mut taken = [0u64; MAX_CARD_CORES];
        let mut slow_head = [0u64; MAX_CARD_CORES];

        // Flow 0 admitted with C_total = 64 credits.
        ctrl.poll_step(10, &rmt, &taken, &slow_head, &[0]);
        assert_eq!(rmt[0].action.load(Ordering::Relaxed), RMT_ACT_FAST);

        // Sudden microburst of 96 packets arrives before reactive rule switch completes!
        for seq in 1..=96u64 {
            let act = rmt[0].action.load(Ordering::Acquire);
            if act == RMT_ACT_FAST {
                rmt[0].fast_hits.fetch_add(1, Ordering::Relaxed);
                sw.push_from(SwProducer::FastPath, 0, seq).unwrap();
            } else {
                rmt[0].slow_enq.fetch_add(1, Ordering::Relaxed);
                slow_buf.push_back(seq);
            }
            if seq == 80 {
                // Controller detects overshoot at t=20 (schedules RMT rewrite at t=120).
                ctrl.poll_step(20, &rmt, &taken, &slow_head, &[]);
            }
            if seq == 88 {
                // RMT rewrite completes at t=130 -> diverts 89..=96 to slow path!
                ctrl.poll_step(130, &rmt, &taken, &slow_head, &[]);
            }
        }
        assert!(ctrl.stats.transient_llc_leak_pkts >= 16, "must capture burst-onset LLC leakage");
        assert_eq!(ctrl.stats.rmt_fast_to_slow, 1);
        assert_eq!(ctrl.stats.phase_pauses, 1);
        assert_eq!(rmt[0].action.load(Ordering::Relaxed), RMT_ACT_SLOW);
        assert!(!slow_buf.is_empty());

        // Host drains all fast-path packets first (phase exclusivity!), releasing fast credits.
        while let Some(e) = sw.pop() {
            order.observe(0, e.item);
            taken[0] += 1;
        }
        // Then async_recv pulls slow-path packets into SW ring.
        while let Some(seq) = slow_buf.pop_front() {
            rmt[0].slow_drn.fetch_add(1, Ordering::Relaxed);
            sw.push_from(SwProducer::SlowPath, 0, seq).unwrap();
        }
        while let Some(e) = sw.pop() {
            order.observe(0, e.item);
            slow_head[0] += 1;
        }
        assert_eq!(order.ooo_count, 0, "phase exclusivity + 2P1C SW ring must preserve strict order");
        assert_eq!(order.gap_count, 0);

        // Confirm slow->fast re-enable after slow_head caught up + RMT rewrite latency.
        ctrl.poll_step(200, &rmt, &taken, &slow_head, &[]);
        assert_eq!(rmt[0].action.load(Ordering::Relaxed), RMT_ACT_PHASE_PAUSE);
        ctrl.poll_step(350, &rmt, &taken, &slow_head, &[]);
        assert_eq!(rmt[0].action.load(Ordering::Relaxed), RMT_ACT_FAST);
        assert_eq!(ctrl.stats.rmt_slow_to_fast, 1);
    }

    #[test]
    fn ceio_high_flow_churn_triggers_slow_fallback() {
        let rmt: Vec<RmtRuleEntry> = (0..MAX_FLOWS).map(|_| RmtRuleEntry::new()).collect();
        let cfg = CeioControllerConfig {
            c_total: 1024,
            max_fast_flows: 256,
            rmt_rewrite_ticks: 0,
            inactive_timeout_ticks: 10_000,
            reactivate_period_ticks: 500,
            reactivate_batch: 8,
            phase_pause_min_ticks: 0,
            lazy_release_batch: 64,
        };
        let mut ctrl = CeioFlowController::new(cfg);
        ctrl.reset(&rmt);
        let taken = [0u64; MAX_CARD_CORES];
        let slow_head = [0u64; MAX_CARD_CORES];

        // Rapidly introduce 2,048 transient flows in 100-tick slots.
        let mut t = 0u64;
        for batch in 0..128u16 {
            let obs: Vec<u16> = (0..16u16).map(|k| batch * 16 + k).collect();
            t += 100;
            ctrl.poll_step(t, &rmt, &taken, &slow_head, &obs);
        }
        assert!(ctrl.stats.slow_fallback_new_flows > 1000, "2048 rapid churn flows must spill to slow path");
    }

    #[test]
    fn roce_template_icrc_deterministic() {
        let tpl = RoceTemplate::new(
            [0x02, 0x00, 0x00, 0x00, 0x00, 0x02],
            [0x02, 0x00, 0x00, 0x00, 0x00, 0x01],
            [10, 10, 100, 2],
            [10, 10, 100, 1],
            0x0001_0001,
            512,
        );
        let payload = vec![0xabu8; 512];
        let c1 = tpl.icrc(&payload);
        let c2 = tpl.icrc(&payload);
        assert_eq!(c1, c2);
        assert_ne!(c1, 0);
    }

    #[test]
    fn fast_hist_percentiles() {
        let mut h = FastHist::new();
        for i in 1..=1000u64 {
            h.record_ns(i * 100); // 0.1 us .. 100.0 us
        }
        assert_eq!(h.count(), 1000);
        let p50 = h.pct_us(0.50);
        let p99 = h.pct_us(0.99);
        assert!((p50 - 50.0).abs() < 1.0, "p50={p50}");
        assert!((p99 - 99.0).abs() < 1.0, "p99={p99}");
    }
}
