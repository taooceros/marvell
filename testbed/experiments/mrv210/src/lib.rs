//! Shared constants, wire payload format, and per-core 64 B BAR4 credit-line
//! layout for MRV-210 (MRV-203 E4: 4-8 host-core uniform scaling, RSS skew,
//! and single slow-core 1-10 ms stall isolation comparing `privRing`,
//! `ShRing-style`, `rxBisect-style`, and `CreditSDP`).

/// VF0 hardware limit on CN106 (`OTX_EP_MAX_RINGS_PER_VF = 8`).
pub const MAX_QUEUES: usize = 8;

/// Maximum logical mercury streams tracked for per-stream sequence continuity.
pub const MAX_STREAMS: usize = 256;

/// Control TCP ports (separate from MRV-26 BAR4 lease on `10.10.0.2:7526`).
pub const CARD_CTRL_PORT: u16 = 7550;
pub const HOST_CTRL_PORT: u16 = 7551;
pub const MERC_CTRL_PORT: u16 = 7552;

/// Exclusive 64 B BAR4 cache line per host core (`0..MAX_QUEUES`):
/// byte offset `CREDIT_BASE + core_id * 64` (total 512 B inside BAR4 slot 15,
/// strictly below `READY_OFFSET = 4,194,112`). Zero lock, zero false sharing.
pub const CREDIT_BASE: usize = 0;

/// UDP destination ports recognized by `mrv210_card` and `mrv210_host`:
/// - `ROCE_UDP_PORT` (4791): RoCEv2 `IBV_QPT_UD` hardware-bypass frames (payload at offset 62, 4 B ICRC at tail).
/// - `UDP_DATA_PORT` (21000): standard IPv4 UDP frames (payload at offset 42, zero ICRC; supports exact 1500 B L2).
pub const ROCE_UDP_PORT: u16 = 4791;
pub const UDP_DATA_PORT: u16 = 21000;

/// Byte offsets inside a DPDK `mbuf` (L2 frame without 4 B Ethernet FCS).
pub const OFF_ETH_TYPE: usize = 12;
pub const OFF_IPV4: usize = 14;
pub const OFF_UDP: usize = 34;
pub const OFF_UDP_PAYLOAD: usize = 42;
pub const OFF_UD_PAYLOAD: usize = 62;
pub const ROCE_ICRC_LEN: usize = 4;

/// Minimum MRV-210 application payload size:
/// `[0..8] seq`, `[8..16] cookie`, `[16..24] meta`, `[24..32] tx_tsc`, `[len-8..len] tail`.
pub const MIN_PAYLOAD_LEN: usize = 40;

pub const CREDIT_MAGIC: u64 = 0xA55A_2100_0000_0000;
pub const COOKIE_MAGIC: u64 = 0x2100_55AA_0F0F_F0F0;
pub const TAIL_MAGIC: u64 = 0x2100_A5A5_F0F0_0F0F;
pub const MID_XOR: u64 = 0x5555_AAAA_5555_AAAA;

/// CN106 `octeon_ep` hardware refill batch (`DROQ_REFILL_THRESHOLD` in `otx_ep_rxtx.h:24`).
pub const DROQ_REFILL_THRESHOLD: u64 = 64;

/// Byte offset of host core `q`'s dedicated 64 B BAR4 credit cache line.
#[inline(always)]
pub const fn credit_line_offset(q: usize) -> usize {
    assert!(q < MAX_QUEUES);
    CREDIT_BASE + q * 64
}

#[inline(always)]
pub const fn credit_sig(taken: u64, run_gen: u64, q: usize) -> u64 {
    taken.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ run_gen ^ ((q as u64) << 48) ^ CREDIT_MAGIC
}

/// 64-byte atomic credit line written by host core `q` via a single NO-SFENCE
/// `MOVDIR64B` to `BAR4_SLOT_15 + credit_line_offset(q)`.
///
/// Layout:
/// - `w[0]`: monotonic `taken` packet count (aligned to completed `SLIST_DBELL` refills)
/// - `w[1]`: `run_gen`
/// - `w[2]`: `bytes_taken`
/// - `w[3]`: `host_tsc`
/// - `w[4]`: `flags` (bit 0 = 64 B atomic MOVDIR64B)
/// - `w[5]`: `sig = credit_sig(taken, run_gen, q)`
/// - `w[6]`: `q`
/// - `w[7]`: trailing copy of `taken` (torn-line audit on CN106 ARM core)
#[repr(C, align(64))]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CreditLine {
    pub w: [u64; 8],
}

const _: () = assert!(size_of::<CreditLine>() == 64 && align_of::<CreditLine>() == 64);

impl CreditLine {
    #[inline(always)]
    pub const fn new(q: usize, taken: u64, run_gen: u64, bytes_taken: u64, host_tsc: u64) -> Self {
        Self {
            w: [
                taken,
                run_gen,
                bytes_taken,
                host_tsc,
                1,
                credit_sig(taken, run_gen, q),
                q as u64,
                taken,
            ],
        }
    }

    #[inline(always)]
    pub const fn is_valid(&self, q: usize, expected_run_gen: u64) -> bool {
        self.w[1] == expected_run_gen
            && self.w[0] == self.w[7]
            && self.w[6] == q as u64
            && self.w[5] == credit_sig(self.w[0], expected_run_gen, q)
    }
}

/// Pack `(stream_id: u32, target_q: u16, run_gen_lo: u16)` into `meta: u64`.
#[inline(always)]
pub const fn pack_meta(stream_id: u32, target_q: u16, run_gen: u64) -> u64 {
    ((stream_id as u64) << 32) | ((target_q as u64) << 16) | (run_gen & 0xffff)
}

#[inline(always)]
pub const fn unpack_meta(meta: u64) -> (u32, u16, u16) {
    let stream_id = (meta >> 32) as u32;
    let target_q = ((meta >> 16) & 0xffff) as u16;
    let gen_lo = (meta & 0xffff) as u16;
    (stream_id, target_q, gen_lo)
}

#[inline(always)]
pub const fn pkt_cookie(stream_id: u32, seq: u64, run_gen: u64) -> u64 {
    let x = seq.wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ (stream_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9)
        ^ run_gen.wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ COOKIE_MAGIC
}

#[inline(always)]
pub const fn pkt_tail(stream_id: u32, seq: u64, run_gen: u64) -> u64 {
    let x = seq.wrapping_mul(0x6c62_272e_07bb_0142)
        ^ (stream_id as u64).wrapping_mul(0x517c_c1b7_2722_0a95)
        ^ run_gen.wrapping_mul(0x2545_f491_4f6c_dd1d);
    let v = x ^ TAIL_MAGIC;
    if v == 0 { 1 } else { v }
}

/// Stamp untouched end-to-end MRV-210 payload verification fields into `pay` (`pay.len() >= 40`).
#[inline(always)]
pub fn stamp_payload(pay: &mut [u8], stream_id: u32, target_q: u16, seq: u64, run_gen: u64, tx_tsc: u64) {
    let len = pay.len();
    debug_assert!(len >= MIN_PAYLOAD_LEN);
    let ck = pkt_cookie(stream_id, seq, run_gen);
    let meta = pack_meta(stream_id, target_q, run_gen);
    let tl = pkt_tail(stream_id, seq, run_gen);
    // SAFETY: `len >= 40`; all offsets are strictly inside `[0..len)`.
    unsafe {
        let p = pay.as_mut_ptr();
        p.cast::<u64>().write_unaligned(seq);
        p.add(8).cast::<u64>().write_unaligned(ck);
        p.add(16).cast::<u64>().write_unaligned(meta);
        p.add(24).cast::<u64>().write_unaligned(tx_tsc);
        if len >= 128 {
            let mid = (len / 2) & !7;
            p.add(mid).cast::<u64>().write_unaligned(ck ^ MID_XOR);
        }
        p.add(len - 8).cast::<u64>().write_unaligned(tl);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PayloadCheck {
    Ok {
        stream_id: u32,
        target_q: u16,
        seq: u64,
        tx_tsc: u64,
    },
    Bad,
    Torn,
}

/// Locate the MRV-210 application payload slice inside a raw Ethernet frame (excluding 4 B FCS).
/// Recognizes both RoCEv2 UD (`dport == 4791`, offset 62, 4 B ICRC trimmed) and
/// standard IPv4 UDP (`dport == 21000`, offset 42, zero ICRC).
#[inline(always)]
pub fn extract_payload(frame: &[u8]) -> Option<&[u8]> {
    let len = frame.len();
    if len < OFF_UDP + 8 {
        return None;
    }
    if frame[OFF_ETH_TYPE] != 0x08 || frame[OFF_ETH_TYPE + 1] != 0x00 {
        return None;
    }
    if frame[OFF_IPV4 + 9] != 17 {
        return None;
    }
    let dport = u16::from_be_bytes([frame[OFF_UDP + 2], frame[OFF_UDP + 3]]);
    if dport == ROCE_UDP_PORT {
        if len < OFF_UD_PAYLOAD + MIN_PAYLOAD_LEN + ROCE_ICRC_LEN {
            return None;
        }
        Some(&frame[OFF_UD_PAYLOAD..len - ROCE_ICRC_LEN])
    } else if dport == UDP_DATA_PORT {
        if len < OFF_UDP_PAYLOAD + MIN_PAYLOAD_LEN {
            return None;
        }
        Some(&frame[OFF_UDP_PAYLOAD..len])
    } else {
        None
    }
}

/// Verify an extracted MRV-210 payload slice against `expected_payload_len` (0 = any >= 40)
/// and `expected_run_gen` (0 = accept any run_gen encoded in `meta`? No: if `expected_run_gen != 0`,
/// enforce exact match on both `gen_lo` and `cookie`/`tail`).
#[inline(always)]
pub fn verify_payload(pay: &[u8], expected_payload_len: usize, expected_run_gen: u64) -> PayloadCheck {
    let len = pay.len();
    if len < MIN_PAYLOAD_LEN {
        return PayloadCheck::Bad;
    }
    if expected_payload_len != 0 && len != expected_payload_len {
        return PayloadCheck::Bad;
    }
    // SAFETY: `len >= 40`.
    let (seq, ck, meta, tx_tsc, tl) = unsafe {
        let p = pay.as_ptr();
        (
            p.cast::<u64>().read_unaligned(),
            p.add(8).cast::<u64>().read_unaligned(),
            p.add(16).cast::<u64>().read_unaligned(),
            p.add(24).cast::<u64>().read_unaligned(),
            p.add(len - 8).cast::<u64>().read_unaligned(),
        )
    };
    if seq == 0 {
        return PayloadCheck::Bad;
    }
    let (stream_id, target_q, gen_lo) = unpack_meta(meta);
    let rg = if expected_run_gen != 0 {
        if gen_lo != (expected_run_gen & 0xffff) as u16 {
            return PayloadCheck::Bad;
        }
        expected_run_gen
    } else {
        gen_lo as u64
    };
    let want_tl = pkt_tail(stream_id, seq, rg);
    if tl != want_tl {
        return PayloadCheck::Torn;
    }
    let want_ck = pkt_cookie(stream_id, seq, rg);
    if ck != want_ck {
        return PayloadCheck::Bad;
    }
    if len >= 128 {
        let mid = (len / 2) & !7;
        let mid_val = unsafe { pay.as_ptr().add(mid).cast::<u64>().read_unaligned() };
        if mid_val != (want_ck ^ MID_XOR) {
            return PayloadCheck::Torn;
        }
    }
    PayloadCheck::Ok { stream_id, target_q, seq, tx_tsc }
}

/// Per-stream monotonic sequence tracker (detects genuine packet loss without false positives).
pub struct StreamTracker {
    seen: [bool; MAX_STREAMS],
    last_seq: [u64; MAX_STREAMS],
    pub ooo: u64,
}

impl StreamTracker {
    pub fn new() -> Self {
        Self { seen: [false; MAX_STREAMS], last_seq: [0; MAX_STREAMS], ooo: 0 }
    }

    pub fn reset(&mut self) {
        self.seen.fill(false);
        self.last_seq.fill(0);
        self.ooo = 0;
    }

    /// Observe `seq` on `stream_id`; returns number of skipped (lost) sequence numbers.
    #[inline(always)]
    pub fn observe(&mut self, stream_id: u32, seq: u64) -> u64 {
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
            self.ooo += 1;
            0
        }
    }
}

impl Default for StreamTracker {
    fn default() -> Self {
        Self::new()
    }
}

/// Compute normalized per-queue weights for `n_queues` (`1..=8`) under
/// `uniform`, `ratio4to1` (hot Q0 gets 4x weight of each remaining queue), or
/// `zipf` (`w_q = 1 / (q + 1)^alpha`).
pub fn queue_weights(dist: &str, n_queues: usize, zipf_alpha: f64) -> Vec<f64> {
    let n = n_queues.clamp(1, MAX_QUEUES);
    let raw: Vec<f64> = match dist {
        "ratio4to1" | "4:1" => {
            if n == 1 {
                vec![1.0]
            } else {
                let mut v = vec![1.0; n];
                v[0] = 4.0;
                v
            }
        }
        "zipf" => (0..n).map(|i| 1.0 / ((i + 1) as f64).powf(zipf_alpha.max(0.0))).collect(),
        _ => vec![1.0; n],
    };
    let sum: f64 = raw.iter().sum();
    raw.into_iter().map(|x| x / sum).collect()
}

/// Deterministic `DROQ_REFILL_THRESHOLD = 64`-aligned refill credit tracker.
///
/// Matches `cnxk_ep_recv_pkts_avx` (`cnxk_ep_rx_avx.c:103-110`):
/// `cnxk_ep_rx_refill(droq)` executes at the *beginning* of `rte_eth_rx_burst()`
/// whenever `droq->refill_count >= 64`. Therefore, after an `rx_burst` call that
/// started with `unfilled >= 64` returns `got` packets, all `unfilled` prior
/// packets have already been refilled and rung via `SLIST_DBELL`, and the new
/// `unfilled` count is `got`.
#[derive(Clone, Copy, Debug, Default)]
pub struct RefillTracker {
    pub taken: u64,
    pub refilled: u64,
    pub last_published: u64,
}

impl RefillTracker {
    pub const fn new() -> Self {
        Self { taken: 0, refilled: 0, last_published: 0 }
    }

    /// Record the return of one `rte_eth_rx_burst()` call returning `got` packets.
    #[inline(always)]
    pub fn on_rx_burst(&mut self, got: u64) {
        let prev_unfilled = self.taken.saturating_sub(self.refilled);
        if prev_unfilled >= DROQ_REFILL_THRESHOLD {
            self.refilled = self.taken;
        }
        self.taken = self.taken.wrapping_add(got);
    }

    /// Value eligible for `BAR4` credit publication (`refilled` if `align_refill`, else `taken`).
    #[inline(always)]
    pub const fn publishable(&self, align_refill: bool) -> u64 {
        if align_refill { self.refilled } else { self.taken }
    }

    /// Return `Some(new_credit)` if at least `credit_batch` new credits are publishable.
    #[inline(always)]
    pub fn poll_credit_due(&mut self, credit_batch: u64, align_refill: bool) -> Option<u64> {
        let pub_val = self.publishable(align_refill);
        if pub_val.saturating_sub(self.last_published) >= credit_batch.max(1) {
            self.last_published = pub_val;
            Some(pub_val)
        } else {
            None
        }
    }

    /// Force-flush any unpublished refilled credits (e.g., right after a stall drain).
    #[inline(always)]
    pub fn flush_credit(&mut self, align_refill: bool) -> Option<u64> {
        let pub_val = self.publishable(align_refill);
        if pub_val > self.last_published {
            self.last_published = pub_val;
            Some(pub_val)
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credit_line_offsets_exclusive_cachelines() {
        for q in 0..MAX_QUEUES {
            let off = credit_line_offset(q);
            assert_eq!(off % 64, 0);
            if q > 0 {
                assert_eq!(off - credit_line_offset(q - 1), 64);
            }
        }
        assert!(credit_line_offset(MAX_QUEUES - 1) + 64 <= 4096);
    }

    #[test]
    fn credit_line_roundtrip_and_torn_detection() {
        let mut line = CreditLine::new(3, 1024, 7, 1024 * 1500, 9999);
        assert!(line.is_valid(3, 7));
        assert!(!line.is_valid(2, 7));
        assert!(!line.is_valid(3, 8));
        // Simulate torn write where tail != head
        line.w[7] = 960;
        assert!(!line.is_valid(3, 7));
    }

    #[test]
    fn payload_stamp_verify_ud_and_udp_and_torn() {
        // Exact 1500 B L2 UDP frame: 14 Eth + 20 IP + 8 UDP + 1454 payload = 1496 B (without 4 B FCS)
        let mut udp_frame = vec![0u8; 42 + 1454];
        udp_frame[12] = 0x08;
        udp_frame[13] = 0x00;
        udp_frame[23] = 17;
        udp_frame[36..38].copy_from_slice(&UDP_DATA_PORT.to_be_bytes());
        stamp_payload(&mut udp_frame[42..], 17, 5, 42, 9, 123456);
        let pay = extract_payload(&udp_frame).expect("extract udp payload");
        assert_eq!(pay.len(), 1454);
        assert_eq!(
            verify_payload(pay, 1454, 9),
            PayloadCheck::Ok { stream_id: 17, target_q: 5, seq: 42, tx_tsc: 123456 }
        );
        // Flip middle byte -> Torn
        let mid = 42 + ((1454 / 2) & !7);
        udp_frame[mid] ^= 0x01;
        assert_eq!(verify_payload(extract_payload(&udp_frame).unwrap(), 1454, 9), PayloadCheck::Torn);
        // Restore middle, flip tail -> Torn
        udp_frame[mid] ^= 0x01;
        let last = udp_frame.len() - 1;
        udp_frame[last] ^= 0x80;
        assert_eq!(verify_payload(extract_payload(&udp_frame).unwrap(), 1454, 9), PayloadCheck::Torn);

        // RoCEv2 UD 254 B L2 frame: 62 hdr + 184 payload + 4 ICRC = 250 B (without 4 B FCS)
        let mut ud_frame = vec![0u8; 62 + 184 + 4];
        ud_frame[12] = 0x08;
        ud_frame[13] = 0x00;
        ud_frame[23] = 17;
        ud_frame[36..38].copy_from_slice(&ROCE_UDP_PORT.to_be_bytes());
        stamp_payload(&mut ud_frame[62..62 + 184], 3, 2, 100, 11, 777);
        let upay = extract_payload(&ud_frame).expect("extract ud payload");
        assert_eq!(upay.len(), 184);
        assert_eq!(
            verify_payload(upay, 184, 11),
            PayloadCheck::Ok { stream_id: 3, target_q: 2, seq: 100, tx_tsc: 777 }
        );
    }

    #[test]
    fn refill_tracker_matches_cnxk_ep_rx_avx_semantics() {
        let mut rt = RefillTracker::new();
        // Call 1: rx 32 -> unfilled becomes 32, nothing refilled yet
        rt.on_rx_burst(32);
        assert_eq!(rt.refilled, 0);
        assert_eq!(rt.poll_credit_due(64, true), None);
        // Call 2: rx 32 -> started with unfilled=32 < 64, so still not refilled yet
        rt.on_rx_burst(32);
        assert_eq!(rt.refilled, 0);
        assert_eq!(rt.poll_credit_due(64, true), None);
        // Call 3: rx 16 -> started with unfilled=64 >= 64 -> cnxk_ep_rx_refill ran for 64!
        rt.on_rx_burst(16);
        assert_eq!(rt.refilled, 64);
        assert_eq!(rt.poll_credit_due(64, true), Some(64));
        assert_eq!(rt.poll_credit_due(64, true), None);
        // Empty poll after another 48 (unfilled=64) triggers refill to 128
        rt.on_rx_burst(48);
        assert_eq!(rt.refilled, 64);
        rt.on_rx_burst(0);
        assert_eq!(rt.refilled, 128);
        assert_eq!(rt.poll_credit_due(64, true), Some(128));
    }

    #[test]
    fn queue_weights_uniform_ratio4to1_and_zipf() {
        let u = queue_weights("uniform", 8, 1.0);
        assert_eq!(u.len(), 8);
        assert!((u.iter().sum::<f64>() - 1.0).abs() < 1e-12);
        assert!((u[0] - 0.125).abs() < 1e-12);

        let r = queue_weights("ratio4to1", 8, 1.0);
        assert!((r[0] / r[1] - 4.0).abs() < 1e-12);
        assert!((r.iter().sum::<f64>() - 1.0).abs() < 1e-12);

        let z = queue_weights("zipf", 8, 1.0);
        assert!(z[0] > z[1] && z[1] > z[7]);
        assert!((z[0] / z[7] - 8.0).abs() < 1e-12);
        assert!((z.iter().sum::<f64>() - 1.0).abs() < 1e-12);
    }

    #[test]
    fn stream_tracker_detects_gaps_and_ooo() {
        let mut st = StreamTracker::new();
        assert_eq!(st.observe(5, 1), 0);
        assert_eq!(st.observe(5, 2), 0);
        assert_eq!(st.observe(5, 5), 2);
        assert_eq!(st.observe(5, 4), 0);
        assert_eq!(st.ooo, 1);
    }
}
