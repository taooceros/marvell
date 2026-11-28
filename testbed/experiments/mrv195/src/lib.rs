//! Shared wire formats, KV RPC protocol, 128 B-stride exclusive BAR4 sub-ring
//! geometry, DDIO-hot host RX sub-rings, ARM64 hardware RoCEv2 ICRC, and 1 s
//! FastHist for MRV-195 (Dual-host 100GbE Real Lightweight RPC/KV Benchmark).

use std::sync::atomic::{AtomicU64, Ordering};

/// Maximum CN106 worker cores supported (`eth0 4RxQ/4TxQ` RSS).
pub const MAX_CARD_CORES: usize = 4;

/// Number of KV keys in the open-addressing hash store (8192 keys x 4 KiB slab = 32 MiB).
pub const KV_NUM_KEYS: usize = 8192;
/// Power-of-2 hash table buckets (50% load factor -> ~1.5 avg linear probes).
pub const KV_TABLE_BUCKETS: usize = 16384;
pub const KV_TABLE_MASK: usize = KV_TABLE_BUCKETS - 1;
/// Max value words per key (64 cells -> 1 + 63*6 = 379 u64s = 3032 B; stored in 4096 B slot).
pub const KV_SLOT_BYTES: usize = 4096;
pub const KV_MAX_VAL_WORDS: usize = 379;

/// Retained TX/reply pool on saturn: 128 MiB (> 60 MiB Socket-1 LLC) for async
/// DPI DMA (`>= dma_threshold`) bulk replies (`1 KiB / 4 KiB`).
pub const TX_POOL_BYTES: usize = 128 * 1024 * 1024;
pub const TX_POOL_SLOT_BYTES: usize = 4096;
pub const TX_POOL_SLOTS: usize = TX_POOL_BYTES / TX_POOL_SLOT_BYTES; // 32768

/// BAR4 slot 15 lower 2 MiB partitioned into 4 exclusive per-core SPSC sub-rings
/// (512 KiB per card core, zero cross-core sharing, 128 B default stride).
pub const BAR4_SUBRING_BYTES: usize = 512 * 1024;
pub const BAR4_TOTAL_RING_BYTES: usize = MAX_CARD_CORES * BAR4_SUBRING_BYTES; // 2 MiB
pub const BAR4_CELL_STRIDE_DEFAULT: usize = 128;
pub const BAR4_SUBRING_CELLS_128: usize = BAR4_SUBRING_BYTES / 128; // 4096
pub const BAR4_SUBRING_MASK_128: usize = BAR4_SUBRING_CELLS_128 - 1;
pub const BAR4_SUBRING_CELLS_64: usize = BAR4_SUBRING_BYTES / 64; // 8192
pub const BAR4_SUBRING_MASK_64: usize = BAR4_SUBRING_CELLS_64 - 1;

/// MRV-195 control block inside BAR4 slot 15 (at +2 MiB).
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
pub const RX_SUBRING_BYTES: usize = RX_SUBRING_SLOTS * RX_SLOT_BYTES; // 256 KiB

pub const CREDIT_OFF: usize = 1 * 1024 * 1024;
pub const CREDIT_STRIDE_BYTES: usize = 4096;

pub const LARGE_RX_OFF: usize = 2 * 1024 * 1024;
pub const LARGE_SLOT_BYTES: usize = 4096;
pub const LARGE_SLOTS_PER_CORE: usize = 256;
pub const LARGE_SLOT_MASK: usize = LARGE_SLOTS_PER_CORE - 1;

/// Max drain per poll (kept at 32 across both DirectPush and SDP fair-full).
pub const MAX_DRAIN: usize = 32;

/// Transport operation codes packed in `RpcCell::w[0]` bits 56..64.
pub const OP_PUSH: u8 = 1;
pub const OP_DMA: u8 = 2;
pub const OP_NET: u8 = 3;
pub const OP_PROBE: u8 = 4;

/// KV application opcodes packed in `kv_meta` bits 0..8.
pub const KV_GET: u8 = 1;
pub const KV_PUT: u8 = 2;

/// KV response status packed in `kv_meta` bits 16..24.
pub const KV_STATUS_REQ: u8 = 0;
pub const KV_STATUS_OK: u8 = 1;
pub const KV_STATUS_MISS: u8 = 2;
pub const KV_STATUS_ERR: u8 = 3;

/// 38-bit sequence mask shared across wire frames and BAR4/RX cells.
pub const SEQ_MASK: u64 = (1u64 << 38) - 1;

/// Card control/sync TCP port for MRV-195.
pub const SYNC_ADDR: &str = "10.10.0.2:7529";

/// 100GbE physical link MTU / mbuf room (matches mercury ens6f1np1 MTU 1500).
pub const ETH_MBUF_BUF_SIZE: u16 = 2176;
/// SDP link MTU / mbuf room (single jumbo frame up to 4 KiB+ across SDP).
pub const SDP_MTU: u16 = 9000;
pub const SDP_MBUF_BUF_SIZE: u16 = 10240;
/// 8-byte aligned SDP frame header: Eth(14) + IPv4(20) + UDP(8) + Pad(6) = 48 B.
/// Pairs directly with `octeon_ep` devarg `rx_poll_tail_off=48`.
pub const SDP_HDR_LEN: usize = 48;
pub const SDP_UDP_DST_PORT: u16 = 4799;

/// RoCEv2 UD wire header constants: Eth(14) + IPv4(20) + UDP(8) + BTH(12) + DETH(8) = 62 B.
pub const ROCE_HDR_LEN: usize = 62;
pub const ROCE_ICRC_LEN: usize = 4;
pub const ROCE_UDP_PORT: u16 = 4791;
pub const ROCE_QKEY: u32 = 0x1111_1111;
pub const ROCE_CARD_QPN: u32 = 0x0001_2345;
pub const MAX_SEG_BYTES: usize = 1024;
pub const MAX_RPC_BYTES: usize = 4096;

#[inline(always)]
pub const fn pack_meta(rpc_seq: u64, bytes_div64: u16, frag_idx: u8, frag_cnt: u8, op: u8) -> u64 {
    (rpc_seq & SEQ_MASK)
        | (((bytes_div64 as u64) & 0x3ff) << 38)
        | (((frag_idx as u64) & 0xf) << 48)
        | (((frag_cnt as u64) & 0xf) << 52)
        | ((op as u64) << 56)
}

#[inline(always)]
pub const fn unpack_meta(meta: u64) -> (u64, u16, u8, u8, u8) {
    let rpc_seq = meta & SEQ_MASK;
    let bytes_div64 = ((meta >> 38) & 0x3ff) as u16;
    let frag_idx = ((meta >> 48) & 0xf) as u8;
    let frag_cnt = ((meta >> 52) & 0xf) as u8;
    let op = (meta >> 56) as u8;
    (rpc_seq, bytes_div64, frag_idx, frag_cnt, op)
}

#[inline(always)]
pub const fn pack_kv_meta(kv_op: u8, val_cells: u8, status: u8, cksum32: u32) -> u64 {
    (kv_op as u64)
        | ((val_cells as u64) << 8)
        | ((status as u64) << 16)
        | ((cksum32 as u64) << 32)
}

#[inline(always)]
pub const fn unpack_kv_meta(kv_meta: u64) -> (u8, u8, u8, u32) {
    let kv_op = (kv_meta & 0xff) as u8;
    let val_cells = ((kv_meta >> 8) & 0xff) as u8;
    let status = ((kv_meta >> 16) & 0xff) as u8;
    let cksum32 = (kv_meta >> 32) as u32;
    (kv_op, val_cells, status, cksum32)
}

/// Number of 64-bit value words carried inside an RPC message of `cells` x 64 B.
/// Cell 0 holds 1 word (`w[6]`); each extra cell `1..cells` holds 6 words (`w[1..7]`).
#[inline(always)]
pub const fn val_words_for_cells(cells: usize) -> usize {
    if cells == 0 {
        0
    } else {
        1 + (cells - 1) * 6
    }
}

#[inline(always)]
pub const fn frag_cnt_for_bytes(msg_bytes: usize) -> u8 {
    if msg_bytes > MAX_SEG_BYTES {
        (msg_bytes / MAX_SEG_BYTES) as u8
    } else {
        1
    }
}

#[inline(always)]
pub const fn splitmix64(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ (x >> 31)
}

/// Deterministic non-zero 64-bit value word for `(key, version, word_idx)`.
#[inline(always)]
pub const fn kv_value_word(key: u64, version: u64, word_idx: usize) -> u64 {
    let x = key
        .wrapping_mul(0x9e37_79b9_7f4a_7c15)
        .wrapping_add(version.wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ (word_idx as u64).wrapping_mul(0x94d0_49bb_1331_11eb);
    (x ^ (x >> 29)) | 1
}

/// Fold one 64-bit value word into a running 64-bit checksum accumulator.
#[inline(always)]
pub const fn cksum_step(acc: u64, word: u64) -> u64 {
    acc.rotate_left(7).wrapping_add(word ^ 0x517c_c1b7_2722_0a95)
}

#[inline(always)]
pub const fn cksum_init(key: u64, version: u64, val_cells: u8) -> u64 {
    key.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ version.rotate_left(13) ^ ((val_cells as u64) << 40)
}

#[inline(always)]
pub const fn cksum_finish(acc: u64) -> u32 {
    let z = acc ^ (acc >> 32);
    let out = (z as u32) ^ ((z >> 16) as u32).rotate_left(9);
    if out == 0 { 1 } else { out }
}

/// Default key size class (`cells` in `{1, 4, 16, 64}`) for mixed workload:
///   - `0..3277`    (40%): 64 B  (1 cell)
///   - `3277..6554` (40%): 256 B (4 cells)
///   - `6554..8192` (20%): 4096 B (64 cells)
#[inline(always)]
pub const fn mix_key_cells(key: usize) -> u8 {
    if key < 3277 {
        1
    } else if key < 6554 {
        4
    } else {
        64
    }
}

#[inline(always)]
pub const fn probe_cookie(rpc_seq: u64) -> u64 {
    rpc_seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0x55aa_55aa_0f0f_f0f0
}

/// Offset of cell `bar_pos` (1-based) in core `core`'s exclusive 512 KiB BAR4 sub-ring.
#[inline(always)]
pub const fn bar4_cell_offset(core: usize, bar_pos: u64, stride: usize) -> usize {
    let idx = if stride == 128 {
        ((bar_pos - 1) as usize) & BAR4_SUBRING_MASK_128
    } else {
        ((bar_pos - 1) as usize) & BAR4_SUBRING_MASK_64
    };
    core * BAR4_SUBRING_BYTES + idx * stride
}

/// Offset of slot `rx_seq` (1-based) in core `core`'s host RX sub-ring (256 KiB each).
#[inline(always)]
pub const fn rx_ring_offset(core: usize, rx_seq: u64) -> usize {
    RX_RING_OFF + core * RX_SUBRING_BYTES + (((rx_seq - 1) as usize) & RX_SUBRING_MASK) * RX_SLOT_BYTES
}

/// Offset of core `core`'s 64 B WC credit-cursor line inside host `rx_zone` (4 KiB spaced).
#[inline(always)]
pub const fn credit_line_offset(core: usize) -> usize {
    CREDIT_OFF + core * CREDIT_STRIDE_BYTES
}

/// Offset of slot `large_slot` in core `core`'s exclusive large RX DMA staging buffer.
#[inline(always)]
pub const fn large_rx_offset(core: usize, large_slot: usize) -> usize {
    LARGE_RX_OFF + (core * LARGE_SLOTS_PER_CORE + (large_slot & LARGE_SLOT_MASK)) * LARGE_SLOT_BYTES
}

/// 64 B aligned RPC header/control/payload cell.
#[repr(C, align(64))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct RpcCell {
    pub w: [u64; 8],
}

const _: () = assert!(size_of::<RpcCell>() == 64);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CellPoll {
    Empty,
    Ready(RpcCell),
    Torn,
}

impl RpcCell {
    /// Poll a 64 B PEM0 WC card->host cell verifying all four 16 B STP pieces carry `expected_seq`:
    ///   - OP_PUSH: chunk0 w[0], chunk1 w[2] bits 24..32, chunk2 w[4] bits 48..64, chunk3 w[7]
    ///   - OP_DMA : chunk0 w[0], chunk1 w[3] bits 48..64, chunk2 w[5]==w[0],     chunk3 w[7]
    #[inline(always)]
    pub unsafe fn poll_seq_wc4(ptr: *const u8, expected_seq: u64) -> CellPoll {
        let p = ptr.cast::<AtomicU64>();
        let w0 = unsafe { (*p).load(Ordering::Acquire) };
        if (w0 & SEQ_MASK) != (expected_seq & SEQ_MASK) {
            return CellPoll::Empty;
        }
        let w7 = unsafe { (*p.add(7)).load(Ordering::Acquire) };
        if w7 != w0 {
            return CellPoll::Torn;
        }
        let mut cell = RpcCell { w: [0; 8] };
        cell.w[0] = w0;
        cell.w[7] = w7;
        for i in 1..7 {
            cell.w[i] = unsafe { (*p.add(i)).load(Ordering::Acquire) };
        }
        let op = (w0 >> 56) as u8;
        if op == OP_PUSH {
            let tag1 = ((cell.w[2] >> 24) & 0xff) as u64;
            let tag2 = (cell.w[4] >> 48) & 0xffff;
            if tag1 != (expected_seq & 0xff) || tag2 != (expected_seq & 0xffff) {
                return CellPoll::Torn;
            }
        } else if op == OP_DMA {
            let tag1 = (cell.w[3] >> 48) & 0xffff;
            if tag1 != (expected_seq & 0xffff) || cell.w[5] != w0 || cell.w[6] != w0 {
                return CellPoll::Torn;
            }
        }
        CellPoll::Ready(cell)
    }
    #[inline(always)]
    pub unsafe fn poll_seq(ptr: *const u8, expected_seq: u64) -> CellPoll {
        let p = ptr.cast::<AtomicU64>();
        let w0 = unsafe { (*p).load(Ordering::Acquire) };
        if (w0 & SEQ_MASK) != (expected_seq & SEQ_MASK) {
            return CellPoll::Empty;
        }
        let w7 = unsafe { (*p.add(7)).load(Ordering::Acquire) };
        if w7 != w0 {
            return CellPoll::Torn;
        }
        let mut cell = RpcCell { w: [0; 8] };
        cell.w[0] = w0;
        cell.w[7] = w7;
        for i in 1..7 {
            cell.w[i] = unsafe { (*p.add(i)).load(Ordering::Relaxed) };
        }
        CellPoll::Ready(cell)
    }
    /// Zero-lookahead 1-pass 64 B `2x ldp q` poll for `MT_NORMAL_NC` BAR4 response rings.
    /// Reads ONLY the current slot into 4 NEON registers in one 64 B pass, with exact
    /// header+tail verification (`w[7] == w[0]`).
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
                if (w0 & SEQ_MASK) != (expected_seq & SEQ_MASK) {
                    return CellPoll::Empty;
                }
                let w7 = vgetq_lane_u64::<1>(q3);
                if w7 != w0 {
                    return CellPoll::Torn;
                }
                let w = [
                    w0,
                    vgetq_lane_u64::<1>(q0),
                    vgetq_lane_u64::<0>(q1),
                    vgetq_lane_u64::<1>(q1),
                    vgetq_lane_u64::<0>(q2),
                    vgetq_lane_u64::<1>(q2),
                    vgetq_lane_u64::<0>(q3),
                    w7,
                ];
                CellPoll::Ready(RpcCell { w })
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

/// Precomputed IEEE CRC-32 table (reflected polynomial 0xedb88320).
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

/// Precomputed 62-byte RoCEv2 UD Ethernet/IPv4/UDP/BTH/DETH reply template +
/// precomputed CRC-32 running state over the 56 masked header bytes.
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

/// Constant-memory high-resolution latency histogram up to 1.0 s (~54 KiB).
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

    pub fn overflow_cnt(&self) -> u64 {
        self.bins[TOTAL_BINS - 1]
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
