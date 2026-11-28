//! Shared wire formats, tear-proof 64 B cell framing, RoCEv2 ICRC templates,
//! standard Memcached Binary UDP packet helpers, and 1 s FastHist for MRV-206
//! (Dual-host 100GbE Real Upstream Memcached 1.6.45 Benchmark).

use std::sync::atomic::{AtomicU64, Ordering};

/// Maximum CN106 worker cores supported (`eth0 4RxQ/4TxQ` RSS).
pub const MAX_CARD_CORES: usize = 4;

/// Default Memcached keyspace size (8,192 keys; configurable on server/client).
pub const MC_NUM_KEYS: usize = 8192;
/// Fixed ASCII key length ("k000000000000000" .. "k000000000008191").
pub const MC_KEY_LEN: usize = 16;

/// Standard Memcached Binary Protocol constants (protocol_binary.h / memcached.h).
pub const MC_UDP_PORT: u16 = 11211;
pub const MC_UDP_HDR_LEN: usize = 8;
pub const MC_UDP_DATA_SIZE: usize = 1392;
pub const MC_UDP_MAX_PAYLOAD: usize = 1400;
pub const MC_BIN_HDR_LEN: usize = 24;
pub const MC_MAGIC_REQ: u8 = 0x80;
pub const MC_MAGIC_RES: u8 = 0x81;
pub const MC_CMD_GET: u8 = 0x00;
pub const MC_CMD_SET: u8 = 0x01;
pub const MC_CMD_DELETE: u8 = 0x04;
pub const MC_STATUS_OK: u16 = 0x0000;
pub const MC_STATUS_ENOENT: u16 = 0x0001;
pub const MC_ITEM_FLAGS: u32 = 0x2060_0001;
/// DirectPush inline `OP_PUSH` max Memcached UDP payload (`4 cells x 48 B = 192 B`).
/// Any packet with `mc_len > 192` uses native bidirectional DPI DMA (`OP_DMA`).
pub const OP_PUSH_MAX_MC_BYTES: usize = 192;
pub const MAX_MC_BYTES: usize = 4032;
pub const MAX_FRAG_PAYLOAD: usize = 1008;
pub const MAX_SEG_BYTES: usize = 1024;
pub const MAX_RPC_BYTES: usize = 4096;

/// Retained TX/reply pool on saturn: 128 MiB max for async DPI DMA (`OP_DMA`).
pub const TX_POOL_BYTES: usize = 128 * 1024 * 1024;
pub const TX_POOL_SLOT_BYTES: usize = 4096;
pub const TX_POOL_SLOTS: usize = TX_POOL_BYTES / TX_POOL_SLOT_BYTES;

/// BAR4 slot 15 lower 2 MiB partitioned into 4 exclusive per-core SPSC sub-rings
/// (512 KiB per card core, zero cross-core sharing, 128 B default stride).
pub const BAR4_SUBRING_BYTES: usize = 512 * 1024;
pub const BAR4_TOTAL_RING_BYTES: usize = MAX_CARD_CORES * BAR4_SUBRING_BYTES;
pub const BAR4_CELL_STRIDE_DEFAULT: usize = 128;
pub const BAR4_SUBRING_CELLS_128: usize = BAR4_SUBRING_BYTES / 128;
pub const BAR4_SUBRING_MASK_128: usize = BAR4_SUBRING_CELLS_128 - 1;
pub const BAR4_SUBRING_CELLS_64: usize = BAR4_SUBRING_BYTES / 64;
pub const BAR4_SUBRING_MASK_64: usize = BAR4_SUBRING_CELLS_64 - 1;

/// MRV-206 control block inside BAR4 slot 15 (at +2 MiB).
pub const CTRL_BASE: usize = 2 * 1024 * 1024;
pub const CTRL_RX_ZONE_IOVA: usize = CTRL_BASE;
pub const CTRL_HOST_READY_GEN: usize = CTRL_BASE + 8;
pub const CTRL_CARD_ACK_GEN: usize = CTRL_BASE + 16;
pub const CTRL_CARD_CORES: usize = CTRL_BASE + 24;
pub const CTRL_CREDIT_HYST: usize = CTRL_BASE + 32;
pub const CTRL_BAR4_STRIDE: usize = CTRL_BASE + 40;

/// Unified 8 MiB contiguous host RX memzone on saturn (fits inside one 2 GiB PEM0 region
/// and well within 60 MiB LLC / DDIO):
///   [0 MiB .. 1 MiB) : 4 x 1024 x 256 B per-core DDIO-hot RX sub-rings
///   [1 MiB .. 2 MiB) : 4 x 4096 B spaced card -> host WC credit-cursor lines
///   [2 MiB .. 6 MiB) : 4 x 256 x 4096 B exclusive per-core DPI ToHost DMA staging
pub const RX_ZONE_BYTES: usize = 8 * 1024 * 1024;
pub const RX_RING_OFF: usize = 0;
pub const RX_SLOT_BYTES: usize = 256;
pub const RX_SUBRING_SLOTS: usize = 1024;
pub const RX_SUBRING_MASK: usize = RX_SUBRING_SLOTS - 1;
pub const RX_SUBRING_BYTES: usize = RX_SUBRING_SLOTS * RX_SLOT_BYTES;

pub const CREDIT_OFF: usize = 1 * 1024 * 1024;
pub const CREDIT_STRIDE_BYTES: usize = 4096;

pub const LARGE_RX_OFF: usize = 2 * 1024 * 1024;
pub const LARGE_SLOT_BYTES: usize = 4096;
pub const LARGE_SLOTS_PER_CORE: usize = 256;
pub const LARGE_SLOT_MASK: usize = LARGE_SLOTS_PER_CORE - 1;

/// Max drain per poll (kept at 32 across both DirectPush and SDP fair-full).
pub const MAX_DRAIN: usize = 32;

/// Transport operation codes.
pub const OP_PUSH: u8 = 1;
pub const OP_DMA: u8 = 2;
pub const OP_NET: u8 = 3;
pub const OP_PROBE: u8 = 4;
/// Link-layer RoCEv2 UD opcodes in byte 7 (`w0 >> 56`): always non-zero, so they never
/// collide with raw standard Memcached UDP datagrams whose reserved byte 7 is always `0x00`.
pub const OP_FRAG: u8 = 0xfa;
pub const OP_PROBE_WIRE: u8 = 0xfe;
/// Bit masks for wire & cell metadata.
pub const WIRE_SEQ_MASK: u64 = (1u64 << 38) - 1;
pub const CELL_SEQ_MASK: u64 = (1u64 << 36) - 1;

/// Card control/sync TCP port for MRV-206.
pub const SYNC_ADDR: &str = "10.10.0.2:7539";

/// 100GbE physical link MTU / mbuf room (matches mercury ens6f1np1 MTU 1500).
pub const ETH_MBUF_BUF_SIZE: u16 = 2176;
/// SDP link MTU / mbuf room (single jumbo frame up to 4 KiB+ across SDP).
pub const SDP_MTU: u16 = 9000;
pub const SDP_MBUF_BUF_SIZE: u16 = 10240;
/// 8-byte aligned SDP frame header: Eth(14) + IPv4(20) + UDP(8) + Pad(6) = 48 B.
/// Pairs directly with `octeon_ep` devarg `rx_poll_tail_off=48`.
pub const SDP_HDR_LEN: usize = 48;
pub const SDP_UDP_DST_PORT: u16 = 4806;

/// RoCEv2 UD wire header constants: Eth(14) + IPv4(20) + UDP(8) + BTH(12) + DETH(8) = 62 B.
pub const ROCE_HDR_LEN: usize = 62;
pub const ROCE_ICRC_LEN: usize = 4;
pub const ROCE_UDP_PORT: u16 = 4791;
pub const ROCE_QKEY: u32 = 0x1111_1111;
pub const ROCE_CARD_QPN: u32 = 0x0001_2345;

/// Pack 64-bit wire/SDP/DMA frame metadata:
///   bits  0..38: rpc_seq (tid in bits 34..37, local seq in bits 0..34)
///   bits 38..50: mc_len (exact Memcached UDP packet bytes, 0..4095)
///   bits 50..53: frag_idx (0..7)
///   bits 53..56: frag_cnt (1..4)
///   bits 56..64: op (OP_NET / OP_PROBE)
#[inline(always)]
pub const fn pack_wire_meta(rpc_seq: u64, mc_len: u16, frag_idx: u8, frag_cnt: u8, op: u8) -> u64 {
    (rpc_seq & WIRE_SEQ_MASK)
        | (((mc_len as u64) & 0xfff) << 38)
        | (((frag_idx as u64) & 0x7) << 50)
        | (((frag_cnt as u64) & 0x7) << 53)
        | ((op as u64) << 56)
}

#[inline(always)]
pub const fn unpack_wire_meta(meta: u64) -> (u64, u16, u8, u8, u8) {
    let rpc_seq = meta & WIRE_SEQ_MASK;
    let mc_len = ((meta >> 38) & 0xfff) as u16;
    let frag_idx = ((meta >> 50) & 0x7) as u8;
    let frag_cnt = ((meta >> 53) & 0x7) as u8;
    let op = (meta >> 56) as u8;
    (rpc_seq, mc_len, frag_idx, frag_cnt, op)
}

/// Pack 64-bit DirectPush cell metadata (Card->Host PEM0 WC and Host->Card BAR4):
///   bits  0..36: ring_seq
///   bits 36..48: mc_len (exact Memcached UDP packet bytes, 0..4095)
///   bits 48..52: aux (cells 1..4 for OP_PUSH, or frag_cnt 1..4 for OP_DMA)
///   bits 52..56: tid (0..7 mercury client thread ID)
///   bits 56..64: op (OP_PUSH / OP_DMA)
#[inline(always)]
pub const fn pack_cell_meta(ring_seq: u64, mc_len: u16, aux: u8, tid: u8, op: u8) -> u64 {
    (ring_seq & CELL_SEQ_MASK)
        | (((mc_len as u64) & 0xfff) << 36)
        | (((aux as u64) & 0xf) << 48)
        | (((tid as u64) & 0xf) << 52)
        | ((op as u64) << 56)
}

#[inline(always)]
pub const fn unpack_cell_meta(meta: u64) -> (u64, u16, u8, u8, u8) {
    let ring_seq = meta & CELL_SEQ_MASK;
    let mc_len = ((meta >> 36) & 0xfff) as u16;
    let aux = ((meta >> 48) & 0xf) as u8;
    let tid = ((meta >> 52) & 0xf) as u8;
    let op = (meta >> 56) as u8;
    (ring_seq, mc_len, aux, tid, op)
}

/// Exact bijection between Memcached binary 32-bit `opaque` (echoed verbatim by
/// upstream Memcached) and transport `rpc_seq` (`tid` in bits 34..37, 29-bit local seq).
#[inline(always)]
pub const fn rpc_seq_from_opaque(opaque: u32) -> u64 {
    let tid = ((opaque >> 29) & 7) as u64;
    let lseq = (opaque & 0x1fff_ffff) as u64;
    (tid << 34) | lseq
}

#[inline(always)]
pub const fn opaque_from_rpc_seq(rpc_seq: u64) -> u32 {
    let tid = ((rpc_seq >> 34) & 7) as u32;
    let lseq = (rpc_seq & 0x1fff_ffff) as u32;
    (tid << 29) | lseq
}

/// Extract network-order 32-bit `opaque` from a raw Memcached Binary UDP packet
/// (`8 B UDP hdr + 24 B binary header`, where `opaque` lives at bytes `20..24`).
#[inline(always)]
pub fn mc_udp_opaque(mc_pkt: &[u8]) -> u32 {
    u32::from_be_bytes([mc_pkt[20], mc_pkt[21], mc_pkt[22], mc_pkt[23]])
}

/// Number of 1024 B RoCEv2 UD link-MTU frames for a Memcached UDP message of `mc_len` bytes.
/// Messages `<= 1024 B` travel raw with ZERO wrapper bytes; messages `> 1024 B` are link-segmented.
#[inline(always)]
pub const fn roce_frag_cnt(mc_len: usize) -> u8 {
    if mc_len <= MAX_SEG_BYTES {
        1
    } else {
        mc_len.div_ceil(MAX_FRAG_PAYLOAD) as u8
    }
}

/// Number of 1008 B internal PCIe/SDP segments (for internal 8B-head/8B-tail alignment).
#[inline(always)]
pub const fn wire_frag_cnt(mc_len: usize) -> u8 {
    if mc_len <= MAX_FRAG_PAYLOAD {
        1
    } else {
        mc_len.div_ceil(MAX_FRAG_PAYLOAD) as u8
    }
}

/// Total 64 B-aligned internal PCIe SDP/DMA buffer size (`8 B head + mc_len + pad + 8 B tail`).
#[inline(always)]
pub const fn wire_total_bytes(mc_len: usize) -> usize {
    if mc_len <= MAX_FRAG_PAYLOAD {
        (16 + mc_len).div_ceil(64) * 64
    } else {
        (wire_frag_cnt(mc_len) as usize) * MAX_SEG_BYTES
    }
}

/// Standard Memcached UDP datagram count for a binary payload of `bin_len` bytes (`UDP_DATA_SIZE = 1392`).
#[inline(always)]
pub const fn mc_udp_dgram_cnt(bin_len: usize) -> usize {
    if bin_len == 0 { 1 } else { bin_len.div_ceil(MC_UDP_DATA_SIZE) }
}
/// Number of 64 B DirectPush `OP_PUSH` cells (48 payload bytes per cell).
#[inline(always)]
pub const fn push_cells_for_mc_len(mc_len: usize) -> usize {
    if mc_len == 0 { 1 } else { mc_len.div_ceil(48) }
}

/// Full-cell 64-bit mixing hash over the 6 payload words (`48 B`) of an `McCell`.
/// Combined with `w[0]` into `w[7] = w[0] ^ cell_mix64(w[1..7])`, this detects any
/// partial 16 B PEM0 WC split or torn BAR4 read across all 48 contiguous payload bytes.
#[inline(always)]
pub const fn cell_mix64(w1: u64, w2: u64, w3: u64, w4: u64, w5: u64, w6: u64) -> u64 {
    let mut h = w1.wrapping_mul(0x9e37_79b9_7f4a_7c15);
    h = (h ^ w2).rotate_left(13).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    h = (h ^ w3).rotate_left(17).wrapping_mul(0x94d0_49bb_1331_11eb);
    h = (h ^ w4).rotate_left(23).wrapping_mul(0x9e37_79b9_7f4a_7c15);
    h = (h ^ w5).rotate_left(29).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    h = (h ^ w6).rotate_left(31).wrapping_mul(0x94d0_49bb_1331_11eb);
    h ^ (h >> 32)
}

#[inline(always)]
pub const fn cell_tail_tag(w: &[u64; 8]) -> u64 {
    w[0] ^ cell_mix64(w[1], w[2], w[3], w[4], w[5], w[6])
}

#[inline(always)]
pub const fn probe_cookie(rpc_seq: u64) -> u64 {
    rpc_seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0x55aa_55aa_0f0f_f0f0
}

#[inline(always)]
pub const fn bar4_cell_offset(core: usize, bar_pos: u64, stride: usize) -> usize {
    let mask = if stride == 64 { BAR4_SUBRING_MASK_64 } else { BAR4_SUBRING_MASK_128 };
    core * BAR4_SUBRING_BYTES + (((bar_pos - 1) as usize) & mask) * stride
}

#[inline(always)]
pub const fn rx_ring_offset(core: usize, rx_seq: u64) -> usize {
    RX_RING_OFF + core * RX_SUBRING_BYTES + (((rx_seq - 1) as usize) & RX_SUBRING_MASK) * RX_SLOT_BYTES
}

#[inline(always)]
pub const fn credit_line_offset(core: usize) -> usize {
    CREDIT_OFF + core * CREDIT_STRIDE_BYTES
}

#[inline(always)]
pub const fn large_rx_offset(core: usize, large_slot: usize) -> usize {
    LARGE_RX_OFF + (core * LARGE_SLOTS_PER_CORE + (large_slot & LARGE_SLOT_MASK)) * LARGE_SLOT_BYTES
}

/// 64 B aligned DirectPush cell carrying 48 contiguous payload bytes in `w[1..7]`
/// with `w[0]` metadata and `w[7] = cell_tail_tag(&w)`.
#[repr(C, align(64))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct McCell {
    pub w: [u64; 8],
}

const _: () = assert!(size_of::<McCell>() == 64);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CellPoll {
    Empty,
    Ready(McCell),
    Torn,
}

impl McCell {
    #[inline(always)]
    pub fn seal(&mut self) {
        self.w[7] = cell_tail_tag(&self.w);
    }

    #[inline(always)]
    pub fn payload_bytes(&self) -> &[u8] {
        unsafe { std::slice::from_raw_parts((&self.w[1] as *const u64).cast::<u8>(), 48) }
    }

    #[inline(always)]
    pub fn payload_bytes_mut(&mut self) -> &mut [u8] {
        unsafe { std::slice::from_raw_parts_mut((&mut self.w[1] as *mut u64).cast::<u8>(), 48) }
    }

    /// Tear-proof poll verifying `w[0]` sequence AND full 48 B integrity tag `w[7]`.
    #[inline(always)]
    pub unsafe fn poll_seq(ptr: *const u8, expected_seq: u64) -> CellPoll {
        let p = ptr.cast::<AtomicU64>();
        let w0 = unsafe { (*p).load(Ordering::Acquire) };
        if (w0 & CELL_SEQ_MASK) != (expected_seq & CELL_SEQ_MASK) {
            return CellPoll::Empty;
        }
        let mut cell = McCell { w: [0; 8] };
        cell.w[0] = w0;
        for i in 1..8 {
            cell.w[i] = unsafe { (*p.add(i)).load(Ordering::Acquire) };
        }
        if cell.w[7] != cell_tail_tag(&cell.w) {
            return CellPoll::Torn;
        }
        CellPoll::Ready(cell)
    }
    /// Zero-lookahead 1-pass 64 B `2x ldp q` poll for `MT_NORMAL_NC` BAR4 response rings.
    /// Reads ONLY the current slot into 4 NEON registers in one 64 B pass, with exact
    /// header+tail verification (`w[7] == cell_tail_tag(&w)`).
    #[inline(always)]
    pub unsafe fn poll_seq_ldpq(ptr: *const u8, expected_seq: u64) -> CellPoll {
        #[cfg(target_arch = "aarch64")]
        {
            use std::arch::aarch64::{uint64x2_t, vgetq_lane_u64};
            let (q0, q1, q2, q3): (uint64x2_t, uint64x2_t, uint64x2_t, uint64x2_t);
            unsafe {
                std::arch::asm!(
                    "ldp {q0:q}, {q1:q}, [{p}, #0]",
                    "ldp {q2:q}, {q3:q}, [{p}, #32]",
                    p = in(reg) ptr,
                    q0 = out(vreg) q0,
                    q1 = out(vreg) q1,
                    q2 = out(vreg) q2,
                    q3 = out(vreg) q3,
                    options(nostack, preserves_flags),
                );
                let w0 = vgetq_lane_u64::<0>(q0);
                if (w0 & CELL_SEQ_MASK) != (expected_seq & CELL_SEQ_MASK) {
                    return CellPoll::Empty;
                }
                let w = [
                    w0,
                    vgetq_lane_u64::<1>(q0),
                    vgetq_lane_u64::<0>(q1),
                    vgetq_lane_u64::<1>(q1),
                    vgetq_lane_u64::<0>(q2),
                    vgetq_lane_u64::<1>(q2),
                    vgetq_lane_u64::<0>(q3),
                    vgetq_lane_u64::<1>(q3),
                ];
                if w[7] != cell_tail_tag(&w) {
                    return CellPoll::Torn;
                }
                CellPoll::Ready(McCell { w })
            }
        }
        #[cfg(not(target_arch = "aarch64"))]
        {
            unsafe { Self::poll_seq(ptr, expected_seq) }
        }
    }

    /// Tear-proof read of a 64 B credit-cursor line (`w[0] == w[7]`).
    #[inline(always)]
    pub unsafe fn poll_credit(ptr: *const u8) -> Option<u64> {
        let p = ptr.cast::<AtomicU64>();
        let w0 = unsafe { (*p).load(Ordering::Acquire) };
        let w7 = unsafe { (*p.add(7)).load(Ordering::Acquire) };
        if w0 == w7 { Some(w0) } else { None }
    }
}

// -----------------------------------------------------------------------------
// Standard Memcached Key & Self-Verifying Value Helpers (shared by server populate & client)
// -----------------------------------------------------------------------------

#[inline(always)]
pub const fn splitmix64(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ (x >> 31)
}

#[inline(always)]
pub fn format_mc_key(key: u64, dst: &mut [u8; MC_KEY_LEN]) {
    dst[0] = b'k';
    let mut v = key;
    for i in (1..MC_KEY_LEN).rev() {
        dst[i] = b'0' + ((v % 10) as u8);
        v /= 10;
    }
}

/// Mixed Facebook ETC-style value length table across `0..num_keys`:
///   - `0..40%`:    8 B   (tiny USR/ETC metadata; 1-cell 64 B DirectPush)
///   - `40%..80%`:  128 B (classic ETC median; 4-cell 256 B DirectPush)
///   - `80%..100%`: 3840 B (large value; DPI DMA / SDP jumbo)
#[inline(always)]
pub const fn mix_val_len_for_key(key: usize, num_keys: usize) -> usize {
    let p40 = (num_keys * 4) / 10;
    let p80 = (num_keys * 8) / 10;
    if key < p40 {
        8
    } else if key < p80 {
        128
    } else {
        3840
    }
}

/// Small-only Facebook USR/ETC mix (`50% 8 B + 50% 128 B`, all `<= 192 B` inline).
#[inline(always)]
pub const fn small_mix_val_len_for_key(key: usize, num_keys: usize) -> usize {
    if key < num_keys / 2 { 8 } else { 128 }
}

#[inline(always)]
pub const fn mc_val_word(key: u64, ver: u64, word_idx: usize) -> u64 {
    splitmix64(
        key.wrapping_mul(0x9e37_79b9_7f4a_7c15)
            ^ ver.wrapping_mul(0xbf58_476d_1ce4_e5b9)
            ^ (word_idx as u64).wrapping_mul(0x94d0_49bb_1331_11eb),
    )
}

#[inline(always)]
pub const fn mc_val8_tag(key: u32, ver: u16) -> u16 {
    let h = splitmix64(((key as u64) << 16) | (ver as u64));
    ((h ^ (h >> 32)) & 0xffff) as u16
}

/// Fill deterministic self-verifying value payload (`val_len` must be 8 or a multiple of 8 >= 16).
pub fn fill_mc_value(key: u64, ver: u64, dst: &mut [u8]) {
    let n = dst.len();
    debug_assert!(n >= 8 && n % 8 == 0);
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

/// Build a standard Memcached Binary UDP SET packet (`8 + 24 + 8 + 16 + val_len` bytes).
pub fn build_mc_set_udp(req_id: u16, opaque: u32, key: u64, ver: u64, val_len: usize, out: &mut [u8]) -> usize {
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
    h[4] = 8; // extlen (flags + exptime)
    let body_len = (8 + MC_KEY_LEN + val_len) as u32;
    h[8..12].copy_from_slice(&body_len.to_be_bytes());
    h[12..16].copy_from_slice(&opaque.to_be_bytes());

    // Extras: flags + expiration=0
    out[32..36].copy_from_slice(&MC_ITEM_FLAGS.to_be_bytes());
    out[36..40].copy_from_slice(&0u32.to_be_bytes());

    let mut kbuf = [0u8; MC_KEY_LEN];
    format_mc_key(key, &mut kbuf);
    out[40..40 + MC_KEY_LEN].copy_from_slice(&kbuf);
    fill_mc_value(key, ver, &mut out[40 + MC_KEY_LEN..total]);
    total
}

/// Build a standard Memcached Binary UDP DELETE packet (`8 + 24 + 16 = 48 B`).
pub fn build_mc_delete_udp(req_id: u16, opaque: u32, key: u64, out: &mut [u8]) -> usize {
    let total = MC_UDP_HDR_LEN + MC_BIN_HDR_LEN + MC_KEY_LEN;
    out[0..2].copy_from_slice(&req_id.to_be_bytes());
    out[2..4].copy_from_slice(&0u16.to_be_bytes());
    out[4..6].copy_from_slice(&1u16.to_be_bytes());
    out[6..8].copy_from_slice(&0u16.to_be_bytes());

    let h = &mut out[8..32];
    h.fill(0);
    h[0] = MC_MAGIC_REQ;
    h[1] = MC_CMD_DELETE;
    h[2..4].copy_from_slice(&(MC_KEY_LEN as u16).to_be_bytes());
    h[8..12].copy_from_slice(&(MC_KEY_LEN as u32).to_be_bytes());
    h[12..16].copy_from_slice(&opaque.to_be_bytes());

    let mut kbuf = [0u8; MC_KEY_LEN];
    format_mc_key(key, &mut kbuf);
    out[32..total].copy_from_slice(&kbuf);
    total
}

// -----------------------------------------------------------------------------
// Hardware-Accelerated RoCEv2 ICRC & Precomputed Templates
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
        crc = CRC32_TABLE[((crc ^ b as u32) & 0xff) as usize] ^ (crc >> 8);
    }
    crc
}

#[cfg(target_arch = "aarch64")]
#[target_feature(enable = "crc")]
#[inline]
unsafe fn crc32_hw_update_aarch64(mut crc: u32, data: &[u8]) -> u32 {
    use core::arch::aarch64::{__crc32b, __crc32d};
    let mut ptr = data.as_ptr();
    let mut rem = data.len();
    while rem >= 8 {
        let v = unsafe { std::ptr::read_unaligned(ptr.cast::<u64>()) };
        crc = __crc32d(crc, v);
        ptr = unsafe { ptr.add(8) };
        rem -= 8;
    }
    while rem > 0 {
        let b = unsafe { *ptr };
        crc = __crc32b(crc, b);
        ptr = unsafe { ptr.add(1) };
        rem -= 1;
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
        hdr[18..20].copy_from_slice(&0x1234u16.to_be_bytes());
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
// Constant-memory high-resolution latency histogram up to 1.0 s (~54 KiB)
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
        FastHist {
            bins: vec![0u64; TOTAL_BINS],
            count: 0,
            sum_ns: 0,
            min_ns: u64::MAX,
            max_ns: 0,
        }
    }

    pub fn reset(&mut self) {
        self.bins.fill(0);
        self.count = 0;
        self.sum_ns = 0;
        self.min_ns = u64::MAX;
        self.max_ns = 0;
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
    pub fn record_ns(&mut self, ns: u64) {
        let b = Self::bin_for_ns(ns);
        unsafe {
            *self.bins.get_unchecked_mut(b) += 1;
        }
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
        if other.count == 0 {
            return;
        }
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
            (self.sum_ns as f64 / self.count as f64) / 1000.0
        }
    }

    pub fn quantile_us(&self, q: f64) -> f64 {
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
}
