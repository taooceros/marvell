//! Shared constants, wire formats, BAR4 per-queue credit-line layout, and
//! offline-testable validation helpers for MRV-214 (E6: standard SDP + On-Path
//! credit ring design-space sensitivity sweep: `R_eff` / working-set span,
//! no-SFENCE credit return batch `B`, and CN106 card-core scaling `1..=8`).

use testbed::inline::READY_OFFSET;

/// Maximum VF0 SDP queues (`OTX_EP_MAX_RINGS_PER_VF = 8`) and card worker cores.
pub const MAX_QUEUES: usize = 8;
pub const MAX_CARD_CORES: usize = 8;

/// Hardware limits of CN106 `octeon_ep` OQ (`otx_ep_common.h:38-43`, `otx_ep_rxtx.h:24`).
pub const OTX_EP_MIN_SMALL_OQ_DESC: u16 = 256;
pub const OTX_EP_MIN_DEFAULT_OQ_DESC: u16 = 2048;
pub const OTX_EP_MAX_OQ_DESC: u16 = 8192;
pub const OTX_EP_OQ_BUF_BYTES: usize = 2048;
pub const OTX_EP_OQ_WMARK_MAX: u16 = 256;
pub const DROQ_REFILL_THRESHOLD: u16 = 64;

/// Maximum mercury sender streams tracked for per-stream sequence continuity.
pub const MAX_STREAMS: usize = 64;

/// Dedicated control TCP ports for MRV-214 (MRV-26 BAR4 lease stays on `10.10.0.2:7526`).
pub const CARD_CTRL_PORT: u16 = 7555;
pub const HOST_CTRL_PORT: u16 = 7556;
pub const MERC_CTRL_PORT: u16 = 7557;

/// Base BTH dest_qp for `uc64` mode (`stream_id = dst_qp - UC64_BASE_QPN`).
pub const UC64_BASE_QPN: u32 = 0x0001_0000;
/// Destination UDP port base for `udp` mode (`1500 B` / `256 B` standard Ethernet UDP).
pub const UDP_BASE_PORT: u16 = 14000;
/// Card `eth0` IPv4 address (`10.10.100.2`).
pub const CARD_IP: [u8; 4] = [10, 10, 100, 2];

/// Byte offsets in a DPDK mbuf (without 4 B FCS):
/// - `udp`: Eth(14) + IPv4(20) + UDP(8) = 42 B -> payload starts at 42.
/// - `uc64`: Eth(14) + IPv4(20) + UDP(8) = 42 B -> BTH(12) at 42..54, ICRC(4) at 54..58, pad(2) -> 60 B + 4 FCS = 64 B L2.
/// - `ud`: Eth(14) + IPv4(20) + UDP(8) + BTH(12) + DETH(8) = 62 B -> payload starts at 62.
pub const OFF_ETH_TYPE: usize = 12;
pub const OFF_IPV4: usize = 14;
pub const OFF_UDP: usize = 34;
pub const OFF_UDP_PAYLOAD: usize = 42;
pub const OFF_BTH: usize = 42;
pub const OFF_UD_PAYLOAD: usize = 62;

/// Per-queue exclusive 64 B BAR4 cache lines inside BAR4 slot 15 (`[0 .. 1024)`),
/// strictly below `READY_OFFSET` (`4,194,112`).
/// Each queue `q in 0..8` owns its own 64 B line -> zero locks, zero false sharing
/// across both host cores and CN106 ARM cores.
pub const CREDIT_BASE: usize = 0;
pub const MAX_CREDIT_LINES: usize = 16;
/// Dedicated host->card GO/sync line at slot 15 (`offset = 15 * 64 = 960`).
pub const GO_LINE_IDX: usize = 15;

/// BAR4 control words in the standard control tail `[READY_OFFSET + 64 .. READY_OFFSET + 128)`.
pub const CTRL_CARD_CORES: usize = READY_OFFSET + 8;

pub const CREDIT_MAGIC: u64 = 0xA55A_2140_0000_0000;
pub const MID_XOR: u64 = 0x5555_AAAA_5555_AAAA;

/// Byte offset of queue `q`'s dedicated 64 B BAR4 credit line inside slot 15.
#[inline(always)]
pub const fn credit_line_offset(q: usize) -> usize {
    assert!(q < MAX_CREDIT_LINES);
    CREDIT_BASE + q * 64
}

/// 64-byte cache-line-aligned credit line published by the host via a single
/// NO-SFENCE `MOVDIR64B` (64 B atomic posted write; PCIe preserves order to the
/// same address; card applies idempotent `max(taken_cached, new_taken)`).
///
/// Layout (`[u64; 8]`):
/// - `w[0]`: monotonic `taken` packet count
/// - `w[1]`: `run_gen` (monotonic run identifier; isolates runs cleanly)
/// - `w[2]`: `bytes_taken` (byte-granular credit counter)
/// - `w[3]`: `host_tsc` (diagnostic timestamp)
/// - `w[4]`: `flags` (bit 0 = 64 B atomic MOVDIR64B, bit 1 = SFENCE used, bit 2 = align_refill)
/// - `w[5]`: `sig = credit_sig(taken, run_gen, queue_id)`
/// - `w[6]`: `queue_id`
/// - `w[7]`: trailing copy of `taken` (torn-line audit on CN106)
#[repr(C, align(64))]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CreditLine {
    pub w: [u64; 8],
}

const _: () = assert!(size_of::<CreditLine>() == 64 && align_of::<CreditLine>() == 64);

#[inline(always)]
pub const fn credit_sig(taken: u64, run_gen: u64, queue_id: u64) -> u64 {
    taken
        .wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ run_gen.wrapping_mul(0xbf58_476d_1ce4_e5b9)
        ^ (queue_id << 48)
        ^ CREDIT_MAGIC
}

impl CreditLine {
    #[inline(always)]
    pub const fn new(
        queue_id: u64,
        taken: u64,
        run_gen: u64,
        bytes_taken: u64,
        host_tsc: u64,
        flags: u64,
    ) -> Self {
        Self {
            w: [
                taken,
                run_gen,
                bytes_taken,
                host_tsc,
                flags,
                credit_sig(taken, run_gen, queue_id),
                queue_id,
                taken,
            ],
        }
    }

    /// Verify atomicity, signature, queue ownership, and run generation.
    #[inline(always)]
    pub const fn verify(&self, expected_run_gen: u64, expected_queue: u64) -> CreditCheck {
        if self.w[1] != expected_run_gen {
            return CreditCheck::StaleGen;
        }
        if self.w[0] != self.w[7] {
            return CreditCheck::Torn;
        }
        if self.w[6] != expected_queue || self.w[5] != credit_sig(self.w[0], self.w[1], self.w[6]) {
            return CreditCheck::Torn;
        }
        CreditCheck::Ok { taken: self.w[0], bytes_taken: self.w[2] }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CreditCheck {
    StaleGen,
    Torn,
    Ok { taken: u64, bytes_taken: u64 },
}

/// Supported wire frame profiles on the `mercury -> CN106 -> saturn` 100GbE path.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FrameKind {
    /// Exact 64 B L2 RoCEv2 UC zero-payload frame (HW 24-bit BTH.PSN + ICRC + FCS).
    Uc64,
    /// RoCEv2 UD frame (`70 + payload_bytes` L2; e.g. `184 B` -> `254 B` L2, `1024 B` -> `1094 B` L2).
    Ud,
    /// Standard Ethernet IPv4+UDP frame (`46 + payload_bytes` L2; e.g. `1454 B` -> exact `1500 B` L2).
    Udp,
}

impl FrameKind {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "uc64" | "uc" => Some(Self::Uc64),
            "ud" => Some(Self::Ud),
            "udp" => Some(Self::Udp),
            _ => None,
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Uc64 => "uc64",
            Self::Ud => "ud",
            Self::Udp => "udp",
        }
    }

    /// Byte offset where the self-verifying payload begins inside an mbuf (without 4 B FCS).
    pub const fn payload_offset(self) -> usize {
        match self {
            Self::Uc64 => OFF_BTH + 12,
            Self::Ud => OFF_UD_PAYLOAD,
            Self::Udp => OFF_UDP_PAYLOAD,
        }
    }

    /// Exact L2 Ethernet frame size on the wire (including 4 B FCS).
    pub const fn l2_bytes(self, payload_bytes: usize) -> usize {
        match self {
            Self::Uc64 => {
                if payload_bytes == 0 {
                    64
                } else {
                    62 + payload_bytes
                }
            }
            Self::Ud => 70 + payload_bytes,
            Self::Udp => 46 + payload_bytes,
        }
    }

    /// Exact mbuf data length (`l2_bytes - 4` FCS).
    pub const fn mbuf_len(self, payload_bytes: usize) -> usize {
        self.l2_bytes(payload_bytes) - 4
    }
}

/// Pack end-to-end metadata: `(stream_id << 32) | (payload_bytes << 16) | (run_gen_lo16)`.
#[inline(always)]
pub const fn pack_meta(stream_id: u32, payload_bytes: u16, run_gen_lo16: u16) -> u64 {
    ((stream_id as u64) << 32) | ((payload_bytes as u64) << 16) | (run_gen_lo16 as u64)
}

#[inline(always)]
pub const fn unpack_meta(meta: u64) -> (u32, u16, u16) {
    let stream_id = (meta >> 32) as u32;
    let payload_bytes = ((meta >> 16) & 0xffff) as u16;
    let run_gen_lo16 = (meta & 0xffff) as u16;
    (stream_id, payload_bytes, run_gen_lo16)
}

/// Deterministic end-to-end cookie written by `mercury` (and verified untouched on CN106 + `saturn`).
#[inline(always)]
pub const fn merc_cookie(stream_id: u32, seq: u64, run_gen_lo16: u16) -> u64 {
    let x = seq.wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ ((stream_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ ((run_gen_lo16 as u64).wrapping_mul(0x94d0_49bb_1331_11eb));
    x ^ 0x55aa_55aa_0f0f_f0f0
}

/// Deterministic end-to-end tail sentinel written at `payload[plen - 8..plen]`.
#[inline(always)]
pub const fn merc_tail(stream_id: u32, seq: u64, run_gen_lo16: u16) -> u64 {
    let x = seq.wrapping_mul(0x6c62_272e_07bb_0142)
        ^ ((stream_id as u64).wrapping_mul(0x517c_c1b7_2722_0a95))
        ^ ((run_gen_lo16 as u64).wrapping_mul(0x2545_f491_4f6c_dd1d));
    let v = x ^ 0xa5a5_5a5a_f0f0_0f0f;
    if v == 0 { 1 } else { v }
}

/// Stamp self-verifying `[seq, cookie, meta, ..., mid_sig, tail]` into `payload` (`plen >= 32`).
#[inline(always)]
pub fn stamp_payload(payload: &mut [u8], stream_id: u32, seq: u64, run_gen_lo16: u16) {
    let plen = payload.len();
    debug_assert!(plen >= 32);
    let ck = merc_cookie(stream_id, seq, run_gen_lo16);
    let meta = pack_meta(stream_id, plen as u16, run_gen_lo16);
    let tl = merc_tail(stream_id, seq, run_gen_lo16);
    // SAFETY: `plen >= 32`; offsets `0, 8, 16, mid, plen-8` are strictly in bounds.
    unsafe {
        let p = payload.as_mut_ptr();
        p.cast::<u64>().write_unaligned(seq);
        p.add(8).cast::<u64>().write_unaligned(ck);
        p.add(16).cast::<u64>().write_unaligned(meta);
        if plen >= 128 {
            let mid = (plen / 2) & !7;
            p.add(mid).cast::<u64>().write_unaligned(ck ^ MID_XOR);
        }
        p.add(plen - 8).cast::<u64>().write_unaligned(tl);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PayloadCheck {
    Ok { stream_id: u32, seq: u64, run_gen_lo16: u16 },
    Bad,
    Torn,
}

/// Verify `[seq, cookie, meta, ..., mid_sig, tail]` inside `payload` (`plen >= 32`).
#[inline(always)]
pub fn verify_payload(payload: &[u8], expected_plen: usize) -> PayloadCheck {
    if payload.len() < expected_plen || expected_plen < 32 {
        return PayloadCheck::Bad;
    }
    // SAFETY: bounds verified above.
    let (seq, ck, meta, tl) = unsafe {
        let p = payload.as_ptr();
        (
            p.cast::<u64>().read_unaligned(),
            p.add(8).cast::<u64>().read_unaligned(),
            p.add(16).cast::<u64>().read_unaligned(),
            p.add(expected_plen - 8).cast::<u64>().read_unaligned(),
        )
    };
    let (stream_id, plen_u16, run_gen_lo16) = unpack_meta(meta);
    if seq == 0 || usize::from(plen_u16) != expected_plen {
        return PayloadCheck::Bad;
    }
    let want_tl = merc_tail(stream_id, seq, run_gen_lo16);
    if tl != want_tl {
        return PayloadCheck::Torn;
    }
    let want_ck = merc_cookie(stream_id, seq, run_gen_lo16);
    if ck != want_ck {
        return PayloadCheck::Bad;
    }
    if expected_plen >= 128 {
        let mid = (expected_plen / 2) & !7;
        let mid_v = unsafe { payload.as_ptr().add(mid).cast::<u64>().read_unaligned() };
        if mid_v != (want_ck ^ MID_XOR) {
            return PayloadCheck::Torn;
        }
    }
    PayloadCheck::Ok { stream_id, seq, run_gen_lo16 }
}

/// Build a valid synthetic Ethernet+IPv4+UDP frame into `buf` (for card-local synth mode).
pub fn write_synth_udp_frame(
    buf: &mut [u8],
    dst_mac: [u8; 6],
    src_mac: [u8; 6],
    stream_id: u32,
    seq: u64,
    run_gen_lo16: u16,
) {
    let total = buf.len();
    debug_assert!(total >= OFF_UDP_PAYLOAD + 32);
    buf[0..6].copy_from_slice(&dst_mac);
    buf[6..12].copy_from_slice(&src_mac);
    buf[12..14].copy_from_slice(&0x0800u16.to_be_bytes());
    let ip_len = (total - 14) as u16;
    buf[14] = 0x45;
    buf[15] = 0;
    buf[16..18].copy_from_slice(&ip_len.to_be_bytes());
    buf[18..20].copy_from_slice(&0u16.to_be_bytes());
    buf[20..22].copy_from_slice(&0x4000u16.to_be_bytes());
    buf[22] = 64;
    buf[23] = 17; // UDP
    buf[24..26].copy_from_slice(&0u16.to_be_bytes());
    buf[26..30].copy_from_slice(&[10, 10, 100, 1]);
    buf[30..34].copy_from_slice(&CARD_IP);
    let udp_len = (total - 34) as u16;
    let sport = UDP_BASE_PORT.wrapping_add((stream_id & 0x0fff) as u16);
    let dport = UDP_BASE_PORT;
    buf[34..36].copy_from_slice(&sport.to_be_bytes());
    buf[36..38].copy_from_slice(&dport.to_be_bytes());
    buf[38..40].copy_from_slice(&udp_len.to_be_bytes());
    buf[40..42].copy_from_slice(&0u16.to_be_bytes());
    stamp_payload(&mut buf[OFF_UDP_PAYLOAD..], stream_id, seq, run_gen_lo16);
}

/// Per-stream monotonic sequence / PSN continuity tracker.
pub struct StreamTracker {
    seen: [bool; MAX_STREAMS],
    last_seq: [u64; MAX_STREAMS],
}

impl Default for StreamTracker {
    fn default() -> Self {
        Self::new()
    }
}

impl StreamTracker {
    pub fn new() -> Self {
        Self { seen: [false; MAX_STREAMS], last_seq: [0; MAX_STREAMS] }
    }

    pub fn reset(&mut self) {
        self.seen.fill(false);
        self.last_seq.fill(0);
    }

    #[inline(always)]
    pub fn observe_seq(&mut self, stream_id: u32, seq: u64) -> u64 {
        let idx = (stream_id as usize) & (MAX_STREAMS - 1);
        if !self.seen[idx] {
            self.seen[idx] = true;
            self.last_seq[idx] = seq;
            return 0;
        }
        let prev = self.last_seq[idx];
        if seq > prev {
            let gap = seq - prev - 1;
            self.last_seq[idx] = seq;
            gap
        } else {
            0
        }
    }

    #[inline(always)]
    pub fn observe_uc64(&mut self, stream_id: u32, psn24: u64) -> u64 {
        let idx = (stream_id as usize) & (MAX_STREAMS - 1);
        let p = psn24 & 0x00ff_ffff;
        if !self.seen[idx] {
            self.seen[idx] = true;
            self.last_seq[idx] = p;
            return 0;
        }
        let prev = self.last_seq[idx];
        let diff = p.wrapping_sub(prev) & 0x00ff_ffff;
        self.last_seq[idx] = p;
        if diff > 1 && diff < 0x0080_0000 {
            diff - 1
        } else {
            0
        }
    }
}

/// Compact L1D-resident (16 KiB = 131,072 bits) tracker of distinct 2 KiB mbuf
/// IOVA slots and physical address span observed during a run.
/// Zero L3 footprint; enabled dynamically (`track_iova=1`) in Sweep 1 so the
/// DMA working-set span is measured empirically rather than assumed.
pub struct IovaTracker {
    bits: Box<[u64; 2048]>,
    base_iova: u64,
    min_iova: u64,
    max_iova: u64,
    unique_slots: u64,
    has_sample: bool,
}

impl Default for IovaTracker {
    fn default() -> Self {
        Self::new()
    }
}

impl IovaTracker {
    const BIT_MASK: usize = (2048 * 64) - 1;

    pub fn new() -> Self {
        Self {
            bits: Box::new([0u64; 2048]),
            base_iova: 0,
            min_iova: u64::MAX,
            max_iova: 0,
            unique_slots: 0,
            has_sample: false,
        }
    }

    pub fn reset(&mut self) {
        if self.has_sample {
            self.bits.fill(0);
        }
        self.base_iova = 0;
        self.min_iova = u64::MAX;
        self.max_iova = 0;
        self.unique_slots = 0;
        self.has_sample = false;
    }

    #[inline(always)]
    pub fn observe(&mut self, iova: u64) {
        if !self.has_sample {
            self.has_sample = true;
            self.base_iova = iova;
            self.min_iova = iova;
            self.max_iova = iova;
        } else {
            if iova < self.min_iova {
                self.min_iova = iova;
            }
            if iova > self.max_iova {
                self.max_iova = iova;
            }
        }
        // DPDK mbuf stride is >= 2304 B (> 2048 B), so distinct mbufs map to distinct
        // `>> 11` slots across up to 256 MiB of contiguous hugepage pool without collision.
        let idx = ((iova.wrapping_sub(self.base_iova) >> 11) as usize) & Self::BIT_MASK;
        let word = &mut self.bits[idx >> 6];
        let mask = 1u64 << (idx & 63);
        if (*word & mask) == 0 {
            *word |= mask;
            self.unique_slots += 1;
        }
    }

    pub fn unique_slots(&self) -> u64 {
        self.unique_slots
    }

    /// Total byte span `[min_iova .. max_iova + 2048)` observed across this pool.
    pub fn span_bytes(&self) -> u64 {
        if self.has_sample {
            self.max_iova.saturating_sub(self.min_iova).saturating_add(OTX_EP_OQ_BUF_BYTES as u64)
        } else {
            0
        }
    }
}

/// Exact hardware backpressure watermark configured by `cnxk_ep_vf.c:272-274`.
pub const fn expected_wmark(rx_desc: u16) -> u16 {
    if rx_desc < OTX_EP_MIN_DEFAULT_OQ_DESC {
        let q = rx_desc / 4;
        if q < OTX_EP_OQ_WMARK_MAX { q } else { OTX_EP_OQ_WMARK_MAX }
    } else {
        OTX_EP_OQ_WMARK_MAX
    }
}

/// Maximum safe logical credit window `R_eff` on an `octeon_ep` OQ of `rx_desc`
/// descriptors such that SDP hardware never hits `CNXK_EP_R_OUT_WMARK` even with
/// `DROQ_REFILL_THRESHOLD = 64` refill lag (Pitfall 1 in MRV-203 #comment-558e7687).
pub const fn max_safe_r_eff(rx_desc: u16) -> u16 {
    rx_desc.saturating_sub(expected_wmark(rx_desc)).saturating_sub(DROQ_REFILL_THRESHOLD)
}

/// Validate `rx_desc` against CN106 `octeon_ep` hardware constraints.
pub fn validate_rx_desc(rx_desc: u16, allow_small_oq: bool) -> Result<(), String> {
    if !rx_desc.is_power_of_two() {
        return Err(format!("rx_desc={rx_desc} must be a power of two"));
    }
    let min_desc = if allow_small_oq { OTX_EP_MIN_SMALL_OQ_DESC } else { OTX_EP_MIN_DEFAULT_OQ_DESC };
    if rx_desc < min_desc || rx_desc > OTX_EP_MAX_OQ_DESC {
        return Err(format!(
            "rx_desc={rx_desc} out of bounds [{min_desc}..={OTX_EP_MAX_OQ_DESC}] (allow_small_oq={allow_small_oq})"
        ));
    }
    Ok(())
}

/// Validate per-queue mempool size against `rx_desc` and `pool_cache` so `cnxk_ep_rx_refill`
/// never experiences `rx_alloc_failure`.
pub fn validate_pool_size(rx_desc: u16, pool_size: u32, pool_cache: u32) -> Result<(), String> {
    let min_needed = u32::from(rx_desc) + pool_cache + u32::from(DROQ_REFILL_THRESHOLD) + 64;
    if pool_size < min_needed {
        return Err(format!(
            "pool_size={pool_size} too small for rx_desc={rx_desc} + cache={pool_cache} + margin (need >= {min_needed})"
        ));
    }
    Ok(())
}

/// Parse a `key=val` token list (shared by card & host dynamic TCP `CONFIG`).
pub fn parse_kv_tokens(s: &str) -> Vec<(&str, &str)> {
    s.split_whitespace().filter_map(|tok| tok.split_once('=')).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credit_line_roundtrip_and_torn_detection() {
        let line = CreditLine::new(3, 123_456, 7, 123_456 * 1500, 999_999, 1);
        assert_eq!(line.verify(7, 3), CreditCheck::Ok { taken: 123_456, bytes_taken: 123_456 * 1500 });
        assert_eq!(line.verify(8, 3), CreditCheck::StaleGen);
        assert_eq!(line.verify(7, 2), CreditCheck::Torn);

        let mut torn = line;
        torn.w[7] = 123_455;
        assert_eq!(torn.verify(7, 3), CreditCheck::Torn);

        let mut bad_sig = line;
        bad_sig.w[5] ^= 1;
        assert_eq!(bad_sig.verify(7, 3), CreditCheck::Torn);
    }

    #[test]
    fn credit_line_offsets_stay_below_ready_and_non_overlapping() {
        for q in 0..MAX_CREDIT_LINES {
            let off = credit_line_offset(q);
            assert_eq!(off % 64, 0);
            assert!(off + 64 <= READY_OFFSET);
            if q + 1 < MAX_CREDIT_LINES {
                assert_eq!(credit_line_offset(q + 1) - off, 64);
            }
        }
    }

    #[test]
    fn frame_sizes_and_payload_roundtrip() {
        assert_eq!(FrameKind::Uc64.l2_bytes(0), 64);
        assert_eq!(FrameKind::Uc64.mbuf_len(0), 60);
        assert_eq!(FrameKind::Ud.l2_bytes(184), 254);
        assert_eq!(FrameKind::Ud.l2_bytes(1024), 1094);
        assert_eq!(FrameKind::Udp.l2_bytes(1454), 1500);
        assert_eq!(FrameKind::Udp.mbuf_len(1454), 1496);

        for &plen in &[32usize, 64, 184, 1024, 1454] {
            let mut buf = vec![0u8; plen];
            stamp_payload(&mut buf, 5, 42_001, 9);
            assert_eq!(
                verify_payload(&buf, plen),
                PayloadCheck::Ok { stream_id: 5, seq: 42_001, run_gen_lo16: 9 }
            );

            // Corrupt tail -> Torn
            let mut torn_tail = buf.clone();
            torn_tail[plen - 1] ^= 0xff;
            assert_eq!(verify_payload(&torn_tail, plen), PayloadCheck::Torn);

            // Corrupt midpoint (for >= 128 B) -> Torn
            if plen >= 128 {
                let mut torn_mid = buf.clone();
                let mid = (plen / 2) & !7;
                torn_mid[mid] ^= 0x01;
                assert_eq!(verify_payload(&torn_mid, plen), PayloadCheck::Torn);
            }

            // Corrupt cookie -> Bad
            let mut bad_ck = buf.clone();
            bad_ck[8] ^= 0x10;
            assert_eq!(verify_payload(&bad_ck, plen), PayloadCheck::Bad);
        }
    }

    #[test]
    fn synth_udp_frame_verifies_cleanly() {
        let plen = 1454usize;
        let mut frame = vec![0u8; FrameKind::Udp.mbuf_len(plen)];
        assert_eq!(frame.len(), 1496);
        write_synth_udp_frame(&mut frame, [1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1], 7, 1001, 3);
        assert_eq!(u16::from_be_bytes([frame[12], frame[13]]), 0x0800);
        assert_eq!(frame[23], 17);
        assert_eq!(
            verify_payload(&frame[OFF_UDP_PAYLOAD..], plen),
            PayloadCheck::Ok { stream_id: 7, seq: 1001, run_gen_lo16: 3 }
        );
    }

    #[test]
    fn stream_tracker_detects_gaps_and_uc24_wrap() {
        let mut tr = StreamTracker::new();
        assert_eq!(tr.observe_seq(0, 1), 0);
        assert_eq!(tr.observe_seq(0, 2), 0);
        assert_eq!(tr.observe_seq(0, 5), 2);

        assert_eq!(tr.observe_uc64(1, 0x00ff_fffe), 0);
        assert_eq!(tr.observe_uc64(1, 0x00ff_ffff), 0);
        assert_eq!(tr.observe_uc64(1, 0x0000_0000), 0);
        assert_eq!(tr.observe_uc64(1, 0x0000_0003), 2);
    }

    #[test]
    fn hardware_oq_wmark_and_safe_r_eff_bounds() {
        assert_eq!(expected_wmark(256), 64);
        assert_eq!(max_safe_r_eff(256), 128);
        assert_eq!(expected_wmark(512), 128);
        assert_eq!(max_safe_r_eff(512), 320);
        assert_eq!(expected_wmark(1024), 256);
        assert_eq!(max_safe_r_eff(1024), 704);
        assert_eq!(expected_wmark(2048), 256);
        assert_eq!(max_safe_r_eff(2048), 1728);
        assert_eq!(expected_wmark(4096), 256);
        assert_eq!(max_safe_r_eff(4096), 3776);
        assert_eq!(expected_wmark(8192), 256);
        assert_eq!(max_safe_r_eff(8192), 7872);

        assert!(validate_rx_desc(256, true).is_ok());
        assert!(validate_rx_desc(256, false).is_err());
        assert!(validate_rx_desc(8192, true).is_ok());
        assert!(validate_rx_desc(16384, true).is_err());
        assert!(validate_rx_desc(300, true).is_err());

        assert!(validate_pool_size(512, 768, 128).is_ok());
        assert!(validate_pool_size(512, 600, 128).is_err());
        assert!(validate_pool_size(8192, 8448, 0).is_ok());
        assert!(validate_pool_size(8192, 65536, 0).is_ok());
    }

    #[test]
    fn iova_tracker_counts_exact_distinct_mbufs_up_to_128m() {
        let mut tr = IovaTracker::new();
        let base = 0x1000_0000_0000u64;
        let stride = 2304u64; // sizeof(rte_mbuf)=128 + default buf_size=2176
        for &n in &[512u64, 8192, 65536] {
            tr.reset();
            for round in 0..2 {
                for i in 0..n {
                    let idx = if round == 0 { i } else { n - 1 - i };
                    tr.observe(base + idx * stride);
                }
            }
            assert_eq!(tr.unique_slots(), n);
            assert_eq!(tr.span_bytes(), (n - 1) * stride + 2048);
        }
    }
}
