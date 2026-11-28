//! Shared wire formats, no-SFENCE BAR4 credit-line layout, standard Memcached Binary UDP
//! helpers, lightweight stateful NFV (NAT/LB) engine, On-Path L3-local elastic FIFO,
//! CEIO-style reactive dual-path state machine + 2P1C SW merge ring, co-located 32 MiB
//! cache-sensitive tenant index, and constant-memory FastHist for MRV-209 (MRV-203 E5).

use std::collections::VecDeque;

/// Maximum CN106 card cores / SDP queues supported (`OTX_EP_MAX_RINGS_PER_VF = 8`).
pub const MAX_CARD_CORES: usize = 8;

/// Dedicated card TCP sync/control port for MRV-209 (distinct from MRV-26 lease on 7526).
pub const SYNC_ADDR: &str = "10.10.0.2:7549";
/// Dedicated host TCP control port for MRV-209.
pub const HOST_CTRL_ADDR: &str = "127.0.0.1:7551";

/// Standard Memcached keyspace defaults.
pub const MC_NUM_KEYS: usize = 8192;
pub const MC_KEY_LEN: usize = 16;

/// Standard Memcached Binary Protocol constants (`protocol_binary.h` / `memcached.h`).
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
pub const MC_ITEM_FLAGS: u32 = 0x2090_0001;

pub const MAX_MC_BYTES: usize = 4032;
pub const MAX_FRAG_PAYLOAD: usize = 1008;
pub const MAX_SEG_BYTES: usize = 1024;
pub const MAX_RPC_BYTES: usize = 4096;

/// Max drain burst size per queue poll.
pub const MAX_DRAIN: usize = 32;

/// 100GbE physical link MTU / mbuf data room (matches mercury MTU 1500).
pub const ETH_MBUF_BUF_SIZE: u16 = 2176;
/// Standard SDP link MTU and default compact mbuf data room:
/// `4608 B` holds 4 KiB Memcached values + 48 B SDP header + headroom + OQ info word
/// while keeping 4Q x 512 descriptors tightly within Socket-1 2-way DDIO.
pub const SDP_MTU: u16 = 9000;
pub const SDP_MBUF_BUF_SIZE_JUMBO: u16 = 4608;
pub const SDP_MBUF_BUF_SIZE_COMPACT: u16 = 2176;

/// 8-byte aligned SDP frame header: Eth(14) + IPv4(20) + UDP(8) + Pad(6) = 48 B.
/// Pairs directly with `octeon_ep` devarg `rx_poll_tail_off=48`.
pub const SDP_HDR_LEN: usize = 48;
/// Additional 16 B internal SDP frame envelope (`w0` 8 B + `flow_meta` 8 B) before payload,
/// plus trailing 8 B `w0` copy for torn-DMA protection.
pub const SDP_META_LEN: usize = 16;
pub const SDP_UDP_DST_PORT: u16 = 4809;

/// RoCEv2 UD wire header constants: Eth(14) + IPv4(20) + UDP(8) + BTH(12) + DETH(8) = 62 B.
pub const ROCE_HDR_LEN: usize = 62;
pub const ROCE_ICRC_LEN: usize = 4;
pub const ROCE_UDP_PORT: u16 = 4791;
pub const ROCE_QKEY: u32 = 0x1111_1111;
pub const ROCE_CARD_QPN: u32 = 0x0001_2345;

/// Internal SDP transport opcodes (stored in `w0 >> 56`).
pub const OP_MC_FAST: u8 = 1;
pub const OP_MC_SLOW: u8 = 2;
pub const OP_NFV_FAST: u8 = 3;
pub const OP_NFV_SLOW: u8 = 4;

/// Link-layer RoCEv2 UD opcodes in byte 7 (`w0 >> 56`): always non-zero so they never
/// collide with raw standard Memcached Binary UDP datagrams whose reserved byte 7 is `0x00`.
pub const OP_FRAG: u8 = 0xfa;
pub const OP_NFV_WIRE: u8 = 0xfc;
pub const OP_PROBE_WIRE: u8 = 0xfe;

/// Bit masks for wire metadata.
pub const WIRE_SEQ_MASK: u64 = (1u64 << 38) - 1;

// -----------------------------------------------------------------------------
// BAR4 Slot 15 No-SFENCE Credit Ring Layout (aligned with MRV-212 E1)
// -----------------------------------------------------------------------------

/// Per-queue exclusive 64 B BAR4 credit lines live at `[0 .. MAX_CARD_CORES * 64)`.
pub const CREDIT_BASE: usize = 0;
/// Control/handshake block inside BAR4 slot 15 at +2 MiB (strictly below `READY_OFFSET`).
pub const CTRL_BASE: usize = 2 * 1024 * 1024;
pub const CTRL_CARD_CORES: usize = CTRL_BASE;
pub const CTRL_HOST_RUN_GEN: usize = CTRL_BASE + 8;
pub const CTRL_CARD_ACK_GEN: usize = CTRL_BASE + 16;
pub const CTRL_POLICY_ID: usize = CTRL_BASE + 24;
pub const CTRL_R_EFF: usize = CTRL_BASE + 32;

pub const CREDIT_CELL_MAGIC: u64 = 0xA55A_2090_0000_0000;
pub const CREDIT_FLAG_MOVDIR64B: u64 = 1 << 0;
pub const CREDIT_FLAG_CEIO: u64 = 1 << 1;

/// Byte offset of queue `q`'s dedicated 64 B BAR4 cache line (zero cross-queue sharing).
#[inline(always)]
pub const fn credit_line_offset(q: usize) -> usize {
    assert!(q < MAX_CARD_CORES);
    CREDIT_BASE + q * 64
}

#[inline(always)]
pub const fn credit_sig(taken: u64, run_gen: u64, q: usize) -> u64 {
    taken.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ run_gen ^ ((q as u64) << 48) ^ CREDIT_CELL_MAGIC
}

/// 64-byte atomic credit line written by the host via a single no-SFENCE `MOVDIR64B`:
/// - `w[0]`: monotonic `taken` packet count on queue `q` (post-`port.rx()` / refill-safe)
/// - `w[1]`: `run_gen` (monotonic run identifier so card ignores stale lines)
/// - `w[2]`: `bytes_taken` (byte-granular credit counter)
/// - `w[3]`: `host_tsc` (diagnostic timestamp)
/// - `w[4]`: `flags` (`CREDIT_FLAG_MOVDIR64B`, optional `CREDIT_FLAG_CEIO`)
/// - `w[5]`: `sig` (`credit_sig(taken, run_gen, q)`)
/// - `w[6]`: `slow_taken` (CEIO-style slow-path drained packet count, or 0)
/// - `w[7]`: `taken_tail` (copy of `w[0]` for torn-line verification)
#[repr(C, align(64))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct CreditLine {
    pub w: [u64; 8],
}

const _: () = assert!(size_of::<CreditLine>() == 64 && align_of::<CreditLine>() == 64);

impl CreditLine {
    #[inline(always)]
    pub const fn new(
        q: usize,
        taken: u64,
        run_gen: u64,
        bytes_taken: u64,
        host_tsc: u64,
        flags: u64,
        slow_taken: u64,
    ) -> Self {
        Self {
            w: [
                taken,
                run_gen,
                bytes_taken,
                host_tsc,
                flags,
                credit_sig(taken, run_gen, q),
                slow_taken,
                taken,
            ],
        }
    }

    /// Validate a 64 B credit line read word-by-word from CN106 local BAR4 backing memory.
    #[inline(always)]
    pub const fn verify(&self, q: usize, expected_run_gen: u64) -> Option<(u64, u64, u64)> {
        if self.w[1] != expected_run_gen {
            return None;
        }
        if self.w[0] != self.w[7] {
            return None;
        }
        if self.w[5] != credit_sig(self.w[0], expected_run_gen, q) {
            return None;
        }
        Some((self.w[0], self.w[2], self.w[6]))
    }

    /// Read a 64 B credit line word-by-word via volatile 64-bit loads (avoids store-forwarding
    /// stalls and struct byte-copy tearing).
    ///
    /// # Safety
    /// `ptr` must be 64-byte aligned and readable for 64 bytes.
    #[inline(always)]
    pub unsafe fn read_volatile_words(ptr: *const u8) -> Self {
        let p = ptr.cast::<u64>();
        let mut w = [0u64; 8];
        for i in 0..8 {
            w[i] = unsafe { p.add(i).read_volatile() };
        }
        Self { w }
    }
}

/// Pitfall 1 safety invariant check for CreditSDP on CN106 `octeon_ep`:
/// Hardware backpressure watermark is `WMARK = min(256, nb_desc / 4)` when `nb_desc < 2048`
/// (`allow_small_oq=1`), and `DROQ_REFILL_THRESHOLD = 64` delays descriptor refill by up to
/// 63 packets. Keeping `r_eff <= nb_desc - wmark - 64` guarantees SDP hardware never enters
/// WMARK backpressure or drop state.
pub const DROQ_REFILL_THRESHOLD: u16 = 64;

#[inline(always)]
pub const fn sdp_wmark_for_desc(nb_desc: u16) -> u16 {
    if nb_desc < 2048 {
        let q = nb_desc / 4;
        if q < 256 { q } else { 256 }
    } else {
        256
    }
}

#[inline(always)]
pub const fn max_safe_r_eff(nb_desc: u16) -> u16 {
    let wmark = sdp_wmark_for_desc(nb_desc);
    let reserved = wmark + DROQ_REFILL_THRESHOLD;
    if nb_desc > reserved {
        nb_desc - reserved
    } else {
        0
    }
}

// -----------------------------------------------------------------------------
// Wire & SDP Metadata Packing
// -----------------------------------------------------------------------------

/// Pack 64-bit wire/SDP frame metadata:
///   bits  0..38: `rpc_seq` (`tid` in bits 34..37, local seq in bits 0..34)
///   bits 38..50: `payload_len` (0..4095)
///   bits 50..53: `frag_idx` (or NFV sub-opcode 0=NAT, 1=LB)
///   bits 53..56: `frag_cnt` (1..4)
///   bits 56..64: `op` (`OP_MC_FAST`, `OP_MC_SLOW`, `OP_NFV_FAST`, `OP_NFV_SLOW`, `OP_FRAG`, `OP_NFV_WIRE`, `OP_PROBE_WIRE`)
#[inline(always)]
pub const fn pack_wire_meta(rpc_seq: u64, payload_len: u16, frag_idx: u8, frag_cnt: u8, op: u8) -> u64 {
    (rpc_seq & WIRE_SEQ_MASK)
        | (((payload_len as u64) & 0xfff) << 38)
        | (((frag_idx as u64) & 0x7) << 50)
        | (((frag_cnt as u64) & 0x7) << 53)
        | ((op as u64) << 56)
}

#[inline(always)]
pub const fn unpack_wire_meta(meta: u64) -> (u64, u16, u8, u8, u8) {
    let rpc_seq = meta & WIRE_SEQ_MASK;
    let payload_len = ((meta >> 38) & 0xfff) as u16;
    let frag_idx = ((meta >> 50) & 0x7) as u8;
    let frag_cnt = ((meta >> 53) & 0x7) as u8;
    let op = (meta >> 56) as u8;
    (rpc_seq, payload_len, frag_idx, frag_cnt, op)
}

/// Pack secondary 64-bit SDP metadata word (`flow_meta` at `SDP_HDR_LEN + 8`):
///   bits  0..16: `flow_id` (0..65535)
///   bits 16..32: `flags` (bit 0 = burst/incast tag)
///   bits 32..64: `card_seq` (per-queue monotonic card sequence)
#[inline(always)]
pub const fn pack_flow_meta(flow_id: u16, flags: u16, card_seq: u32) -> u64 {
    (flow_id as u64) | ((flags as u64) << 16) | ((card_seq as u64) << 32)
}

#[inline(always)]
pub const fn unpack_flow_meta(v: u64) -> (u16, u16, u32) {
    ((v & 0xffff) as u16, ((v >> 16) & 0xffff) as u16, (v >> 32) as u32)
}

/// Bijection between Memcached binary 32-bit `opaque` and transport `rpc_seq`:
///   bits 29..32 of `opaque` -> `tid` (0..7)
///   bits  0..29 of `opaque` -> `local_seq`
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

#[inline(always)]
pub fn mc_udp_opaque(mc_pkt: &[u8]) -> u32 {
    u32::from_be_bytes([mc_pkt[20], mc_pkt[21], mc_pkt[22], mc_pkt[23]])
}

/// Extract Memcached 16-bit UDP request_id (also carries flow hash bits for CEIO-style).
#[inline(always)]
pub fn mc_udp_req_id(mc_pkt: &[u8]) -> u16 {
    u16::from_be_bytes([mc_pkt[0], mc_pkt[1]])
}

#[inline(always)]
pub const fn roce_frag_cnt(payload_len: usize) -> u8 {
    if payload_len <= MAX_SEG_BYTES {
        1
    } else {
        payload_len.div_ceil(MAX_FRAG_PAYLOAD) as u8
    }
}

#[inline(always)]
pub const fn wire_frag_cnt(payload_len: usize) -> u8 {
    if payload_len == 0 {
        1
    } else {
        payload_len.div_ceil(MAX_FRAG_PAYLOAD) as u8
    }
}

/// 64 B aligned SDP payload envelope: `8 B w0 + 8 B flow_meta + payload_len + pad + 8 B w0_tail`.
#[inline(always)]
pub const fn sdp_payload_total_bytes(payload_len: usize) -> usize {
    let raw = SDP_META_LEN + payload_len + 8;
    let aligned = (raw + 63) & !63;
    if aligned < 64 { 64 } else { aligned }
}

#[inline(always)]
pub const fn mc_udp_dgram_cnt(bin_len: usize) -> usize {
    if bin_len == 0 {
        1
    } else {
        bin_len.div_ceil(MC_UDP_DATA_SIZE)
    }
}

#[inline(always)]
pub const fn probe_cookie(rpc_seq: u64) -> u64 {
    rpc_seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ 0x55aa_55aa_0f0f_f0f0
}

// -----------------------------------------------------------------------------
// Standard Memcached Key & Self-Verifying Value Helpers
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
///   - `0..40%`:   8 B
///   - `40%..80%`: 128 B
///   - `80%..100%`: 3840 B
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

/// WebSearch-style heavy-tailed value length table across `0..num_keys`:
///   - `0..50%`:   64 B   (small metadata / query shards)
///   - `50%..75%`: 256 B  (medium index snippets)
///   - `75%..90%`: 1024 B (posting list blocks)
///   - `90%..100%`: 3840 B (large document / burst chunks)
#[inline(always)]
pub const fn websearch_val_len_for_key(key: usize, num_keys: usize) -> usize {
    let p50 = num_keys / 2;
    let p75 = (num_keys * 3) / 4;
    let p90 = (num_keys * 9) / 10;
    if key < p50 {
        64
    } else if key < p75 {
        256
    } else if key < p90 {
        1024
    } else {
        3840
    }
}

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

pub fn fill_mc_value(key: u64, ver: u64, dst: &mut [u8]) {
    let n = dst.len();
    debug_assert!(n >= 8 && n.is_multiple_of(8));
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

pub fn verify_mc_value(expected_key: u64, val: &[u8]) -> bool {
    let n = val.len();
    if n < 8 || !n.is_multiple_of(8) {
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

pub fn build_mc_get_udp(req_id: u16, opaque: u32, key: u64, out: &mut [u8]) -> usize {
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
// Lightweight Stateful NFV Workload (DPDK NAT / Maglev LB, ShRing/rxBisect comparable)
// -----------------------------------------------------------------------------

pub const NFV_HDR_LEN: usize = 48;
pub const NFV_MIN_LEN: usize = 64;
pub const NFV_SUBOP_NAT: u8 = 0;
pub const NFV_SUBOP_LB: u8 = 1;
pub const NFV_TABLE_SIZE: usize = 65_536;
pub const NFV_TABLE_MASK: usize = NFV_TABLE_SIZE - 1;
pub const NFV_NAT_PUBLIC_IP: u32 = 0xCB00_7101; // 203.0.113.1
pub const NFV_LB_VIP: u32 = 0x0A0A_FE01; // 10.10.254.1
pub const NFV_NUM_BACKENDS: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NfvMode {
    Nat,
    Lb,
}

#[repr(C, align(64))]
#[derive(Clone, Copy, Default)]
struct NfvFlowEntry {
    src_ip: u32,
    dst_ip: u32,
    sport: u16,
    dport: u16,
    proto: u8,
    valid: u8,
    nat_port: u16,
    mapped_ip: u32,
    state_epoch: u32,
    pkts: u64,
    bytes: u64,
    last_tsc: u64,
    _pad: [u64; 2],
}

const _: () = assert!(size_of::<NfvFlowEntry>() == 64);

/// Stateful 5-tuple NAT / Maglev LB engine with a 4 MiB cache-line-aligned flow table
/// (65,536 flows x 64 B), comparable to ShRing / rxBisect stateful NFV evaluations.
pub struct NfvEngine {
    mode: NfvMode,
    table: Vec<NfvFlowEntry>,
    backends: [u32; NFV_NUM_BACKENDS],
    pub flows_created: u64,
    pub flows_hit: u64,
    pub pkts_processed: u64,
    pub bytes_processed: u64,
}

impl NfvEngine {
    pub fn new(mode: NfvMode) -> Self {
        let mut backends = [0u32; NFV_NUM_BACKENDS];
        for (i, b) in backends.iter_mut().enumerate() {
            *b = 0x0A14_0001u32 + (i as u32); // 10.20.0.1 .. 10.20.0.64
        }
        Self {
            mode,
            table: vec![NfvFlowEntry::default(); NFV_TABLE_SIZE],
            backends,
            flows_created: 0,
            flows_hit: 0,
            pkts_processed: 0,
            bytes_processed: 0,
        }
    }

    pub fn reset_stats(&mut self) {
        self.flows_created = 0;
        self.flows_hit = 0;
        self.pkts_processed = 0;
        self.bytes_processed = 0;
    }

    pub fn prewarm_flows(&mut self, num_flows: usize) {
        let n = num_flows.min(NFV_TABLE_SIZE);
        for f in 0..n {
            let (src_ip, dst_ip, sport, dport, proto) = nfv_five_tuple(f as u16, self.mode);
            let h = nfv_flow_hash(src_ip, dst_ip, sport, dport, proto);
            let idx = (h as usize) & NFV_TABLE_MASK;
            let mapped_ip = match self.mode {
                NfvMode::Nat => NFV_NAT_PUBLIC_IP,
                NfvMode::Lb => self.backends[(h as usize) % NFV_NUM_BACKENDS],
            };
            let nat_port = expected_nfv_port(self.mode, f as u16, dport);
            self.table[idx] = NfvFlowEntry {
                src_ip,
                dst_ip,
                sport,
                dport,
                proto,
                valid: 1,
                nat_port,
                mapped_ip,
                state_epoch: 1,
                pkts: 0,
                bytes: 0,
                last_tsc: 0,
                _pad: [0; 2],
            };
        }
    }

    /// Process one stateful NFV frame in-place (`buf.len() >= NFV_MIN_LEN`).
    /// Wire layout inside NFV payload (`len` bytes, multiple of 8, `>= 64`):
    ///   `0..8`:   `w0` (`pack_wire_meta(rpc_seq, len, subop, 1, OP_NFV_WIRE)`)
    ///   `8..12`:  `src_ip` (LE)
    ///   `12..16`: `dst_ip` (LE)
    ///   `16..18`: `sport` (LE)
    ///   `18..20`: `dport` (LE)
    ///   `20`:     `proto`
    ///   `21`:     `flags` (bit 0 = burst tag; response sets bit 7 = 0x80)
    ///   `22..24`: `flow_id` (LE)
    ///   `24..28`: `mapped_ip` (filled by NFV)
    ///   `28..30`: `mapped_port` (filled by NFV)
    ///   `30..32`: `hdr_csum16` (16-bit folded header checksum computed by NFV)
    ///   `32..40`: `flow_pkt_cnt` (monotonic per-flow counter filled by NFV)
    ///   `40..48`: `req_sig` (echoed / transformed checksum verifying payload integrity)
    ///   `48..(len-8)`: payload data
    ///   `(len-8)..len`: trailing `w0` copy
    #[inline(always)]
    pub fn process_in_place(&mut self, buf: &mut [u8], now_tsc: u64) -> Result<(), ()> {
        let len = buf.len();
        if len < NFV_MIN_LEN || len > MAX_SEG_BYTES {
            return Err(());
        }
        let w0 = u64::from_le_bytes(buf[0..8].try_into().unwrap());
        let w_tail = u64::from_le_bytes(buf[len - 8..len].try_into().unwrap());
        if w0 != w_tail {
            return Err(());
        }
        let (_rpc_seq, plen, _subop, _fc, op) = unpack_wire_meta(w0);
        if op != OP_NFV_WIRE || (plen as usize) != len {
            return Err(());
        }

        let src_ip = u32::from_le_bytes(buf[8..12].try_into().unwrap());
        let dst_ip = u32::from_le_bytes(buf[12..16].try_into().unwrap());
        let sport = u16::from_le_bytes(buf[16..18].try_into().unwrap());
        let dport = u16::from_le_bytes(buf[18..20].try_into().unwrap());
        let proto = buf[20];
        let flow_id = u16::from_le_bytes(buf[22..24].try_into().unwrap());

        let h = nfv_flow_hash(src_ip, dst_ip, sport, dport, proto);
        let idx = (h as usize) & NFV_TABLE_MASK;
        let entry = &mut self.table[idx];
        if entry.valid != 0
            && entry.src_ip == src_ip
            && entry.dst_ip == dst_ip
            && entry.sport == sport
            && entry.dport == dport
            && entry.proto == proto
        {
            self.flows_hit += 1;
        } else {
            let mapped_ip = match self.mode {
                NfvMode::Nat => NFV_NAT_PUBLIC_IP,
                NfvMode::Lb => self.backends[(h as usize) % NFV_NUM_BACKENDS],
            };
            entry.src_ip = src_ip;
            entry.dst_ip = dst_ip;
            entry.sport = sport;
            entry.dport = dport;
            entry.proto = proto;
            entry.valid = 1;
            entry.mapped_ip = mapped_ip;
            entry.nat_port = expected_nfv_port(self.mode, flow_id, dport);
            entry.state_epoch = entry.state_epoch.wrapping_add(1).max(1);
            entry.pkts = 0;
            entry.bytes = 0;
            self.flows_created += 1;
        }
        entry.pkts += 1;
        entry.bytes += len as u64;
        entry.last_tsc = now_tsc;

        let mapped_ip = entry.mapped_ip;
        let mapped_port = entry.nat_port;
        let flow_pkts = entry.pkts;

        // Rewrite response fields + compute 16-bit header fold
        buf[21] |= 0x80;
        buf[24..28].copy_from_slice(&mapped_ip.to_le_bytes());
        buf[28..30].copy_from_slice(&mapped_port.to_le_bytes());
        let csum = nfv_hdr_checksum(src_ip, dst_ip, mapped_ip, sport, dport, mapped_port, proto);
        buf[30..32].copy_from_slice(&csum.to_le_bytes());
        buf[32..40].copy_from_slice(&flow_pkts.to_le_bytes());
        let req_sig = u64::from_le_bytes(buf[40..48].try_into().unwrap());
        let resp_sig = req_sig ^ splitmix64(((mapped_ip as u64) << 16) | (mapped_port as u64));
        buf[40..48].copy_from_slice(&resp_sig.to_le_bytes());

        self.pkts_processed += 1;
        self.bytes_processed += len as u64;
        Ok(())
    }
}

#[inline(always)]
pub const fn nfv_five_tuple(flow_id: u16, mode: NfvMode) -> (u32, u32, u16, u16, u8) {
    let f = flow_id as u32;
    let src_ip = 0x0A00_0001u32 + (f & 0xffff);
    let dst_ip = match mode {
        NfvMode::Nat => 0xC633_6401u32 + (f & 0xff),
        NfvMode::Lb => NFV_LB_VIP,
    };
    let sport = 10000u16.wrapping_add(flow_id);
    let dport = 80u16 + (flow_id & 0x3f);
    (src_ip, dst_ip, sport, dport, 17)
}

#[inline(always)]
pub const fn nfv_flow_hash(src_ip: u32, dst_ip: u32, sport: u16, dport: u16, proto: u8) -> u64 {
    let a = ((src_ip as u64) << 32) | (dst_ip as u64);
    let b = ((sport as u64) << 24) | ((dport as u64) << 8) | (proto as u64);
    splitmix64(a ^ b.rotate_left(19))
}

#[inline(always)]
pub const fn expected_nfv_ip(mode: NfvMode, src_ip: u32, dst_ip: u32, sport: u16, dport: u16, proto: u8) -> u32 {
    match mode {
        NfvMode::Nat => NFV_NAT_PUBLIC_IP,
        NfvMode::Lb => {
            let h = nfv_flow_hash(src_ip, dst_ip, sport, dport, proto);
            0x0A14_0001u32 + ((h as usize % NFV_NUM_BACKENDS) as u32)
        }
    }
}

#[inline(always)]
pub const fn expected_nfv_port(mode: NfvMode, flow_id: u16, dport: u16) -> u16 {
    match mode {
        NfvMode::Nat => 32768u16.wrapping_add(flow_id),
        NfvMode::Lb => dport,
    }
}

#[inline(always)]
pub const fn nfv_hdr_checksum(
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

pub fn build_nfv_req(
    rpc_seq: u64,
    flow_id: u16,
    mode: NfvMode,
    burst_flag: bool,
    pkt_len: usize,
    out: &mut [u8],
) -> usize {
    let len = pkt_len.clamp(NFV_MIN_LEN, MAX_SEG_BYTES) & !7;
    let subop = match mode {
        NfvMode::Nat => NFV_SUBOP_NAT,
        NfvMode::Lb => NFV_SUBOP_LB,
    };
    let w0 = pack_wire_meta(rpc_seq, len as u16, subop, 1, OP_NFV_WIRE);
    let (src_ip, dst_ip, sport, dport, proto) = nfv_five_tuple(flow_id, mode);
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

pub fn verify_nfv_resp(buf: &[u8], expected_rpc_seq: u64, flow_id: u16, mode: NfvMode, expected_len: usize) -> bool {
    if buf.len() != expected_len || expected_len < NFV_MIN_LEN {
        return false;
    }
    let w0 = u64::from_le_bytes(buf[0..8].try_into().unwrap());
    let w_tail = u64::from_le_bytes(buf[expected_len - 8..expected_len].try_into().unwrap());
    if w0 != w_tail {
        return false;
    }
    let (rpc_seq, plen, _subop, _fc, op) = unpack_wire_meta(w0);
    if op != OP_NFV_WIRE || rpc_seq != expected_rpc_seq || (plen as usize) != expected_len {
        return false;
    }
    if (buf[21] & 0x80) == 0 {
        return false;
    }
    let (src_ip, dst_ip, sport, dport, proto) = nfv_five_tuple(flow_id, mode);
    let exp_ip = expected_nfv_ip(mode, src_ip, dst_ip, sport, dport, proto);
    let exp_port = expected_nfv_port(mode, flow_id, dport);
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
// On-Path SmartNIC Head-Resetting L3-Local Elastic FIFO (Pitfall 3)
// -----------------------------------------------------------------------------

/// Bounded ring FIFO that resets `head = 0, tail = 0` whenever it drains empty so
/// repeated `<= 32 MiB` bursts stay hot inside CN106's `48 MiB` L3 cache rather than
/// walking linearly across DDR5.
pub struct ElasticFifo<T> {
    slots: Vec<Option<T>>,
    head: usize,
    tail: usize,
    len: usize,
    cap: usize,
    pub enqueued: u64,
    pub dequeued: u64,
    pub drops: u64,
    pub max_depth: usize,
    pub head_resets: u64,
}

impl<T> ElasticFifo<T> {
    pub fn with_capacity(cap: usize) -> Self {
        let mut slots = Vec::with_capacity(cap);
        slots.resize_with(cap, || None);
        Self {
            slots,
            head: 0,
            tail: 0,
            len: 0,
            cap,
            enqueued: 0,
            dequeued: 0,
            drops: 0,
            max_depth: 0,
            head_resets: 0,
        }
    }

    #[inline(always)]
    pub fn len(&self) -> usize {
        self.len
    }

    #[inline(always)]
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    #[inline(always)]
    pub fn capacity(&self) -> usize {
        self.cap
    }

    #[inline(always)]
    pub fn push(&mut self, item: T) -> Result<(), T> {
        if self.cap == 0 || self.len >= self.cap {
            self.drops += 1;
            return Err(item);
        }
        self.slots[self.tail] = Some(item);
        self.tail += 1;
        if self.tail == self.cap {
            self.tail = 0;
        }
        self.len += 1;
        self.enqueued += 1;
        if self.len > self.max_depth {
            self.max_depth = self.len;
        }
        Ok(())
    }

    #[inline(always)]
    pub fn pop(&mut self) -> Option<T> {
        if self.len == 0 {
            return None;
        }
        let item = self.slots[self.head].take();
        self.head += 1;
        if self.head == self.cap {
            self.head = 0;
        }
        self.len -= 1;
        self.dequeued += 1;
        if self.len == 0 && (self.head != 0 || self.tail != 0) {
            // Pitfall 3: reset indices to 0 on drain so recurring bursts reuse L3-hot slots.
            self.head = 0;
            self.tail = 0;
            self.head_resets += 1;
        }
        item
    }

    pub fn clear_and_reset_stats(&mut self) {
        while self.pop().is_some() {}
        self.head = 0;
        self.tail = 0;
        self.enqueued = 0;
        self.dequeued = 0;
        self.drops = 0;
        self.max_depth = 0;
        self.head_resets = 0;
    }

    pub fn reset_stats_only(&mut self) {
        self.enqueued = 0;
        self.dequeued = 0;
        self.drops = 0;
        self.max_depth = self.len;
        self.head_resets = 0;
    }
}

// -----------------------------------------------------------------------------
// CEIO-Style Reactive Dual-Path Per-Flow State Machine & Host 2P1C SW Merge Ring
// -----------------------------------------------------------------------------

pub const CEIO_FLOW_TABLE_SIZE: usize = 16_384;
pub const CEIO_FLOW_TABLE_MASK: usize = CEIO_FLOW_TABLE_SIZE - 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CeioFlowPhase {
    Fast,
    Switching { ready_at_tsc: u64 },
    Slow,
    DrainPause { resume_at_tsc: u64 },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CeioRoute {
    /// Forward immediately on fast path (either healthy fast-path or transient leak during rule switch).
    FastPath { transient_leak: bool },
    /// Divert to card DRAM slow-path queue.
    SlowQueue,
}

#[derive(Clone, Copy)]
struct CeioFlowSlot {
    phase: CeioFlowPhase,
    recent_pkts: u32,
    slow_queued: u32,
    last_epoch: u64,
}

impl Default for CeioFlowSlot {
    fn default() -> Self {
        Self {
            phase: CeioFlowPhase::Fast,
            recent_pkts: 0,
            slow_queued: 0,
            last_epoch: 0,
        }
    }
}

#[derive(Clone, Copy, Default, Debug)]
pub struct CeioStats {
    pub fast_pkts: u64,
    pub transient_leak_pkts: u64,
    pub slow_enqueued_pkts: u64,
    pub slow_drained_pkts: u64,
    pub rule_switches: u64,
    pub phase_pauses: u64,
    pub pause_stalled_pkts: u64,
}

/// Per-core CEIO-style reactive dual-path controller running on the same CN106 hardware:
/// monitors per-flow credit pressure, incurs off-path RMT rule-switch lag (`switch_lag_ticks`)
/// where burst packets transiently leak onto the host fast path, diverts sustained bursts
/// to the slow card DRAM ring, and enforces `phase exclusivity` (`phase_pause_ticks`) when
/// draining slow->fast to preserve per-flow order.
pub struct CeioFlowTable {
    slots: Vec<CeioFlowSlot>,
    flow_credit_limit: u32,
    switch_lag_ticks: u64,
    phase_pause_ticks: u64,
    window_ticks: u64,
    pub stats: CeioStats,
}

impl CeioFlowTable {
    pub fn new(
        flow_credit_limit: u32,
        switch_lag_ticks: u64,
        phase_pause_ticks: u64,
        window_ticks: u64,
    ) -> Self {
        Self {
            slots: vec![CeioFlowSlot::default(); CEIO_FLOW_TABLE_SIZE],
            flow_credit_limit: flow_credit_limit.max(4),
            switch_lag_ticks,
            phase_pause_ticks,
            window_ticks: window_ticks.max(1),
            stats: CeioStats::default(),
        }
    }

    pub fn reset(&mut self) {
        for s in &mut self.slots {
            *s = CeioFlowSlot::default();
        }
        self.stats = CeioStats::default();
    }

    #[inline(always)]
    pub fn classify(&mut self, flow_id: u16, global_inflight: u64, global_fast_cap: u64, now_tsc: u64) -> CeioRoute {
        let idx = (flow_id as usize) & CEIO_FLOW_TABLE_MASK;
        let epoch = now_tsc / self.window_ticks;
        let s = &mut self.slots[idx];
        if s.last_epoch != epoch {
            s.recent_pkts = 0;
            s.last_epoch = epoch;
        }
        s.recent_pkts = s.recent_pkts.saturating_add(1);

        match s.phase {
            CeioFlowPhase::Fast => {
                let over_flow = s.recent_pkts > self.flow_credit_limit;
                let over_global = global_inflight >= global_fast_cap;
                if over_flow || over_global {
                    self.stats.rule_switches += 1;
                    if self.switch_lag_ticks == 0 {
                        s.phase = CeioFlowPhase::Slow;
                        s.slow_queued += 1;
                        self.stats.slow_enqueued_pkts += 1;
                        CeioRoute::SlowQueue
                    } else {
                        s.phase = CeioFlowPhase::Switching {
                            ready_at_tsc: now_tsc.wrapping_add(self.switch_lag_ticks),
                        };
                        self.stats.transient_leak_pkts += 1;
                        CeioRoute::FastPath { transient_leak: true }
                    }
                } else {
                    self.stats.fast_pkts += 1;
                    CeioRoute::FastPath { transient_leak: false }
                }
            }
            CeioFlowPhase::Switching { ready_at_tsc } => {
                if now_tsc >= ready_at_tsc {
                    s.phase = CeioFlowPhase::Slow;
                    s.slow_queued += 1;
                    self.stats.slow_enqueued_pkts += 1;
                    CeioRoute::SlowQueue
                } else {
                    self.stats.transient_leak_pkts += 1;
                    CeioRoute::FastPath { transient_leak: true }
                }
            }
            CeioFlowPhase::Slow => {
                s.slow_queued += 1;
                self.stats.slow_enqueued_pkts += 1;
                CeioRoute::SlowQueue
            }
            CeioFlowPhase::DrainPause { resume_at_tsc } => {
                if now_tsc >= resume_at_tsc && s.slow_queued == 0 {
                    s.phase = CeioFlowPhase::Fast;
                    self.stats.fast_pkts += 1;
                    CeioRoute::FastPath { transient_leak: false }
                } else {
                    self.stats.pause_stalled_pkts += 1;
                    s.slow_queued += 1;
                    self.stats.slow_enqueued_pkts += 1;
                    CeioRoute::SlowQueue
                }
            }
        }
    }

    /// Called when one packet of `flow_id` is drained from the card slow-path DRAM queue.
    #[inline(always)]
    pub fn on_slow_drained(&mut self, flow_id: u16, now_tsc: u64) {
        let idx = (flow_id as usize) & CEIO_FLOW_TABLE_MASK;
        let s = &mut self.slots[idx];
        s.slow_queued = s.slow_queued.saturating_sub(1);
        self.stats.slow_drained_pkts += 1;
        if s.slow_queued == 0 && matches!(s.phase, CeioFlowPhase::Slow) {
            self.stats.phase_pauses += 1;
            if self.phase_pause_ticks == 0 {
                s.phase = CeioFlowPhase::Fast;
            } else {
                s.phase = CeioFlowPhase::DrainPause {
                    resume_at_tsc: now_tsc.wrapping_add(self.phase_pause_ticks),
                };
            }
        }
    }
}

/// Host-side 2P1C software merge ring (`SW ring`) used exclusively by the `ceio-style`
/// baseline to merge fast-path (`OP_*_FAST`) and slow-path (`OP_*_SLOW`) deliveries
/// before dispatching to upstream Memcached or NFV.
pub struct SwMergeRing<T> {
    fast_q: VecDeque<T>,
    slow_q: VecDeque<T>,
    pub fast_merged: u64,
    pub slow_merged: u64,
}

impl<T> SwMergeRing<T> {
    pub fn with_capacity(cap: usize) -> Self {
        Self {
            fast_q: VecDeque::with_capacity(cap),
            slow_q: VecDeque::with_capacity(cap),
            fast_merged: 0,
            slow_merged: 0,
        }
    }

    #[inline(always)]
    pub fn push_fast(&mut self, item: T) {
        self.fast_q.push_back(item);
        self.fast_merged += 1;
    }

    #[inline(always)]
    pub fn push_slow(&mut self, item: T) {
        self.slow_q.push_back(item);
        self.slow_merged += 1;
    }

    /// Prioritize slow-path draining before newly arrived fast-path packets (CEIO order preservation).
    #[inline(always)]
    pub fn pop(&mut self) -> Option<T> {
        if let Some(s) = self.slow_q.pop_front() {
            Some(s)
        } else {
            self.fast_q.pop_front()
        }
    }

    pub fn clear(&mut self) {
        self.fast_q.clear();
        self.slow_q.clear();
    }

    pub fn reset_stats(&mut self) {
        self.fast_merged = 0;
        self.slow_merged = 0;
    }
}

// -----------------------------------------------------------------------------
// Co-Located Socket-1 Cache-Sensitive Tenant Index (default 32 MiB = 524,288 x 64 B)
// -----------------------------------------------------------------------------

#[repr(C, align(64))]
#[derive(Clone, Copy)]
pub struct TenantEntry {
    pub next_idx: u64,
    pub key: u64,
    pub value: u64,
    pub checksum: u64,
    pub pad: [u64; 4],
}

const _: () = assert!(size_of::<TenantEntry>() == 64 && align_of::<TenantEntry>() == 64);

/// Pointer-chasing + self-verifying cache-line KV index resident in Socket-1 L3 (default 32 MiB).
/// Because each lookup depends on the previous entry's `next_idx` (single cycle-covering random
/// permutation), the CPU hardware prefetcher cannot mask LLC evictions caused by Leaky DMA.
pub struct TenantIndex {
    ptr: *mut TenantEntry,
    len: usize,
    bytes: usize,
    mmapped: bool,
}

unsafe impl Send for TenantIndex {}

impl TenantIndex {
    /// Allocate, pre-fault, and `mlock` a `ws_mb` MiB cache-line index on the caller's NUMA node.
    pub fn new(ws_mb: usize, seed: u64) -> Result<Self, String> {
        let bytes = ws_mb.max(1) * 1024 * 1024;
        let len = bytes / size_of::<TenantEntry>();
        let raw = unsafe {
            libc::mmap(
                std::ptr::null_mut(),
                bytes,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_POPULATE,
                -1,
                0,
            )
        };
        if raw == libc::MAP_FAILED {
            return Err("TenantIndex mmap failed".to_string());
        }
        let ptr = raw.cast::<TenantEntry>();
        // Build a single random Sattolo cycle across `0..len` so every cache line is visited
        // uniformly and unpredictably with zero short cycles.
        let mut perm: Vec<usize> = (0..len).collect();
        let mut rng = seed ^ 0x2090_cafe_babe_0001;
        for i in (1..len).rev() {
            rng = splitmix64(rng);
            let j = (rng as usize) % i;
            perm.swap(i, j);
        }
        let entries = unsafe { std::slice::from_raw_parts_mut(ptr, len) };
        for i in 0..len {
            let next_idx = perm[i] as u64;
            let key = i as u64;
            let value = splitmix64(key ^ seed);
            let checksum = next_idx ^ key.rotate_left(13) ^ value.rotate_left(29) ^ 0x2090_5a5a_a5a5_1234;
            entries[i] = TenantEntry {
                next_idx,
                key,
                value,
                checksum,
                pad: [value ^ 1, value ^ 2, value ^ 3, value ^ 4],
            };
        }
        let _ = unsafe { libc::mlock(raw, bytes) };
        Ok(Self {
            ptr,
            len,
            bytes,
            mmapped: true,
        })
    }

    #[inline(always)]
    pub fn len(&self) -> usize {
        self.len
    }

    #[inline(always)]
    pub fn bytes(&self) -> usize {
        self.bytes
    }

    /// Execute one dependent cache-line lookup at `idx`, verify integrity, and return
    /// `(next_idx, value, ok)`.
    #[inline(always)]
    pub fn probe_once(&self, idx: usize) -> (usize, u64, bool) {
        debug_assert!(idx < self.len);
        // SAFETY: `idx < self.len` and `self.ptr` is valid for `self.len` entries.
        let e = unsafe { &*self.ptr.add(idx) };
        let expect = e.next_idx ^ e.key.rotate_left(13) ^ e.value.rotate_left(29) ^ 0x2090_5a5a_a5a5_1234;
        let ok = e.checksum == expect && e.key == (idx as u64);
        (e.next_idx as usize, e.value, ok)
    }
}

impl Drop for TenantIndex {
    fn drop(&mut self) {
        if self.mmapped && !self.ptr.is_null() {
            unsafe {
                let _ = libc::munlock(self.ptr.cast::<libc::c_void>(), self.bytes);
                let _ = libc::munmap(self.ptr.cast::<libc::c_void>(), self.bytes);
            }
            self.ptr = std::ptr::null_mut();
        }
    }
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
        RoceTemplate {
            hdr,
            crc_seed,
            payload_len,
        }
    }

    #[inline(always)]
    pub fn icrc(&self, payload: &[u8]) -> u32 {
        !crc32_update(self.crc_seed, payload)
    }
}

// -----------------------------------------------------------------------------
// Constant-Memory High-Resolution Latency Histogram up to 1.0 s (~60 KiB)
// 1 ns resolution below 2 us (ideal for 32 MiB tenant KV lookups), 10 ns up to 20 us,
// 100 ns up to 200 us, 10 us up to 20 ms, 1 ms up to 1 s.
// -----------------------------------------------------------------------------

const B0_1NS: usize = 2000;   // [0 .. 2,000 ns) @ 1 ns
const B1_10NS: usize = 1800;  // [2,000 .. 20,000 ns) @ 10 ns
const B2_100NS: usize = 1800; // [20,000 .. 200,000 ns) @ 100 ns
const B3_10US: usize = 1980;  // [200,000 .. 20,000,000 ns) @ 10 us
const B4_1MS: usize = 980;    // [20,000,000 .. 1,000,000,000 ns) @ 1 ms
const TOTAL_BINS: usize = B0_1NS + B1_10NS + B2_100NS + B3_10US + B4_1MS + 1;

#[derive(Clone)]
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
        Self {
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
        if ns < 2_000 {
            ns as usize
        } else if ns < 20_000 {
            B0_1NS + ((ns - 2_000) / 10) as usize
        } else if ns < 200_000 {
            B0_1NS + B1_10NS + ((ns - 20_000) / 100) as usize
        } else if ns < 20_000_000 {
            B0_1NS + B1_10NS + B2_100NS + ((ns - 200_000) / 10_000) as usize
        } else if ns < 1_000_000_000 {
            B0_1NS + B1_10NS + B2_100NS + B3_10US + ((ns - 20_000_000) / 1_000_000) as usize
        } else {
            TOTAL_BINS - 1
        }
    }

    fn ns_for_bin(b: usize) -> f64 {
        if b < B0_1NS {
            b as f64 + 0.5
        } else if b < B0_1NS + B1_10NS {
            2_000.0 + ((b - B0_1NS) as f64) * 10.0 + 5.0
        } else if b < B0_1NS + B1_10NS + B2_100NS {
            20_000.0 + ((b - B0_1NS - B1_10NS) as f64) * 100.0 + 50.0
        } else if b < B0_1NS + B1_10NS + B2_100NS + B3_10US {
            200_000.0 + ((b - B0_1NS - B1_10NS - B2_100NS) as f64) * 10_000.0 + 5_000.0
        } else if b < B0_1NS + B1_10NS + B2_100NS + B3_10US + B4_1MS {
            20_000_000.0 + ((b - B0_1NS - B1_10NS - B2_100NS - B3_10US) as f64) * 1_000_000.0 + 500_000.0
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

    pub fn mean_ns(&self) -> f64 {
        if self.count == 0 {
            0.0
        } else {
            self.sum_ns as f64 / self.count as f64
        }
    }

    pub fn mean_us(&self) -> f64 {
        self.mean_ns() / 1000.0
    }

    pub fn quantile_ns(&self, q: f64) -> f64 {
        if self.count == 0 {
            return 0.0;
        }
        let target = ((self.count as f64) * q).ceil().max(1.0) as u64;
        let mut cum = 0u64;
        for b in 0..TOTAL_BINS {
            cum += self.bins[b];
            if cum >= target {
                return Self::ns_for_bin(b);
            }
        }
        self.max_ns as f64
    }

    pub fn quantile_us(&self, q: f64) -> f64 {
        self.quantile_ns(q) / 1000.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credit_line_roundtrip_and_torn_detection() {
        let mut line = CreditLine::new(2, 1024, 77, 131_072, 9999, CREDIT_FLAG_MOVDIR64B, 12);
        assert_eq!(line.verify(2, 77), Some((1024, 131_072, 12)));
        assert_eq!(line.verify(3, 77), None);
        assert_eq!(line.verify(2, 78), None);
        line.w[7] = 1023;
        assert_eq!(line.verify(2, 77), None);
    }

    #[test]
    fn pitfall1_r_eff_safety_bounds() {
        assert_eq!(sdp_wmark_for_desc(512), 128);
        assert_eq!(max_safe_r_eff(512), 320);
        assert!(128 <= max_safe_r_eff(512));
        assert!(256 <= max_safe_r_eff(512));
        assert_eq!(sdp_wmark_for_desc(256), 64);
        assert_eq!(max_safe_r_eff(256), 128);
    }

    #[test]
    fn wire_and_flow_meta_roundtrip() {
        let seq = (3u64 << 34) | 0x12345;
        let w0 = pack_wire_meta(seq, 3896, 2, 4, OP_MC_SLOW);
        assert_eq!(unpack_wire_meta(w0), (seq, 3896, 2, 4, OP_MC_SLOW));
        let opq = opaque_from_rpc_seq(seq);
        assert_eq!(rpc_seq_from_opaque(opq), seq);
        let fm = pack_flow_meta(4095, 1, 987_654);
        assert_eq!(unpack_flow_meta(fm), (4095, 1, 987_654));
    }

    #[test]
    fn memcached_udp_build_and_value_verify() {
        let mut pkt = [0u8; 4096];
        for vlen in [8usize, 64, 128, 256, 1024, 3840] {
            let n = build_mc_set_udp(42, 0x2000_0007, 123, 5, vlen, &mut pkt);
            assert_eq!(n, 8 + 24 + 8 + 16 + vlen);
            assert_eq!(mc_udp_req_id(&pkt[..n]), 42);
            assert_eq!(mc_udp_opaque(&pkt[..n]), 0x2000_0007);
            assert!(verify_mc_value(123, &pkt[56..n]));
            assert!(!verify_mc_value(124, &pkt[56..n]));
            pkt[56] ^= 0xff;
            assert!(!verify_mc_value(123, &pkt[56..n]));
        }
        let gn = build_mc_get_udp(7, 0x4000_0009, 55, &mut pkt);
        assert_eq!(gn, 48);
        assert_eq!(pkt[7], 0);
        assert_eq!(pkt[8], MC_MAGIC_REQ);
        assert_eq!(pkt[9], MC_CMD_GET);
    }

    #[test]
    fn nfv_nat_and_lb_roundtrip() {
        for mode in [NfvMode::Nat, NfvMode::Lb] {
            let mut eng = NfvEngine::new(mode);
            eng.prewarm_flows(128);
            let mut buf = [0u8; 1024];
            for (i, &sz) in [64usize, 256, 1024].iter().enumerate() {
                let rpc_seq = (1u64 << 34) | (i as u64 + 1);
                let flow_id = (i as u16) * 17 + 3;
                let n = build_nfv_req(rpc_seq, flow_id, mode, i == 1, sz, &mut buf);
                assert_eq!(n, sz);
                assert!(eng.process_in_place(&mut buf[..n], 1000 + i as u64).is_ok());
                assert!(verify_nfv_resp(&buf[..n], rpc_seq, flow_id, mode, sz));
                // Tamper check
                buf[24] ^= 1;
                assert!(!verify_nfv_resp(&buf[..n], rpc_seq, flow_id, mode, sz));
            }
            assert_eq!(eng.pkts_processed, 3);
        }
    }

    #[test]
    fn elastic_fifo_resets_head_on_drain() {
        let mut q = ElasticFifo::with_capacity(4);
        assert!(q.push(10).is_ok());
        assert!(q.push(20).is_ok());
        assert_eq!(q.pop(), Some(10));
        assert_eq!(q.pop(), Some(20));
        assert_eq!(q.head_resets, 1);
        assert_eq!(q.head, 0);
        assert_eq!(q.tail, 0);
        for v in 0..4 {
            assert!(q.push(v).is_ok());
        }
        assert!(q.push(99).is_err());
        assert_eq!(q.drops, 1);
        assert_eq!(q.max_depth, 4);
    }

    #[test]
    fn ceio_flow_table_transitions_and_sw_merge_ring() {
        let mut tbl = CeioFlowTable::new(4, 100, 50, 10_000);
        // First 4 packets go fast
        for t in 0..4 {
            assert_eq!(tbl.classify(7, 1, 100, t), CeioRoute::FastPath { transient_leak: false });
        }
        // 5th packet triggers rule switch with lag -> transient leak
        assert_eq!(tbl.classify(7, 1, 100, 10), CeioRoute::FastPath { transient_leak: true });
        assert_eq!(tbl.classify(7, 1, 100, 50), CeioRoute::FastPath { transient_leak: true });
        assert_eq!(tbl.stats.rule_switches, 1);
        assert_eq!(tbl.stats.transient_leak_pkts, 2);
        // After lag expires, enters Slow
        assert_eq!(tbl.classify(7, 1, 100, 120), CeioRoute::SlowQueue);
        // Draining slow enters DrainPause (phase exclusivity)
        tbl.on_slow_drained(7, 130);
        assert_eq!(tbl.stats.phase_pauses, 1);
        // Packet arriving during DrainPause is stalled to slow queue
        assert_eq!(tbl.classify(7, 1, 100, 140), CeioRoute::SlowQueue);
        assert_eq!(tbl.stats.pause_stalled_pkts, 1);
        tbl.on_slow_drained(7, 150);
        // After pause window elapses, returns to Fast
        assert_eq!(tbl.classify(7, 1, 100, 300), CeioRoute::FastPath { transient_leak: false });

        let mut sw = SwMergeRing::with_capacity(8);
        sw.push_fast(1);
        sw.push_slow(2);
        assert_eq!(sw.pop(), Some(2));
        assert_eq!(sw.pop(), Some(1));
    }

    #[test]
    fn tenant_index_full_cycle_integrity() {
        let idx = TenantIndex::new(1, 20901).expect("1 MiB tenant index");
        assert_eq!(idx.len(), 16_384);
        let mut cur = 0usize;
        for _ in 0..1024 {
            let (next, _val, ok) = idx.probe_once(cur);
            assert!(ok);
            assert!(next < idx.len());
            cur = next;
        }
    }

    #[test]
    fn fast_hist_sub_microsecond_precision() {
        let mut h = FastHist::new();
        for ns in 1..=1000u64 {
            h.record_ns(ns);
        }
        assert!((h.quantile_ns(0.50) - 500.5).abs() < 2.0);
        assert!((h.quantile_ns(0.99) - 990.5).abs() < 2.0);
        assert!((h.quantile_ns(0.999) - 999.5).abs() < 2.0);
    }
}
