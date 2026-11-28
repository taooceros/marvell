//! Shared wire formats, BAR4 credit-line layout, integrity verification, and
//! experiment geometry for MRV-213 (MRV-203 E2: real 100GbE + standard SDP
//! burst protection, concurrent 32 MiB KV tail latency, and full line-rate drain).

use std::fmt;

/// Default card TCP control/sync/PMU port (separate from MRV-26 lease on 7526).
pub const CARD_SYNC_ADDR: &str = "10.10.0.2:7527";
/// Default mercury burst-generator TCP control port.
pub const MERC_CTRL_ADDR: &str = "mercury:7547";
/// Default host-side TCP control port (when orchestrated externally).
pub const HOST_CTRL_ADDR: &str = "127.0.0.1:7531";

/// Maximum host VF0 / card sdp1-1 queues (`OTX_EP_MAX_RINGS_PER_VF = 8`).
pub const MAX_QUEUES: usize = 8;
/// Maximum mercury TX streams / RSS probe flows tracked for sequence continuity.
pub const MAX_STREAMS: usize = 128;
/// Card eth0 IPv4 address (`10.10.100.2`) for ARP replies & packet filtering.
pub const CARD_IP: [u8; 4] = [10, 10, 100, 2];
/// RoCEv2 UDP destination port (used by `uc64`, `ud256`, `ud1094`).
pub const ROCE_UDP_PORT: u16 = 4791;
/// Plain UDP/IPv4 destination port (used by `udp1500` non-root GSO/sendmmsg).
pub const DATA_UDP_PORT: u16 = 4799;

/// Base BTH dest_qp for `uc64` mode (`stream_id = dst_qp - UC64_BASE_QPN`).
pub const UC64_BASE_QPN: u32 = 0x0001_0000;
/// Fixed BTH dest_qp for `ud256` / `ud1094` mode.
pub const UD_CARD_QPN: u32 = 0x0001_2345;
/// Fixed RoCEv2 UD Q_Key.
pub const ROCE_QKEY: u32 = 0x1111_1111;

/// Byte offsets in a DPDK mbuf (without 4 B Ethernet FCS):
/// Eth(14) + IPv4(20) + UDP(8) = 42 B; BTH(12) -> 54 B; DETH(8) -> 62 B.
pub const OFF_ETH_TYPE: usize = 12;
pub const OFF_IPV4: usize = 14;
pub const OFF_UDP: usize = 34;
pub const OFF_UDP_PAYLOAD: usize = 42;
pub const OFF_BTH: usize = 42;
pub const OFF_DETH: usize = 54;
pub const OFF_UD_PAYLOAD: usize = 62;

/// Standard DPDK `octeon_ep` refill threshold (`otx_ep_rxtx.h:24`).
pub const DROQ_REFILL_THRESHOLD: u64 = 64;

/// BAR4 slot 15 per-queue credit-line base offset (below `READY_OFFSET`).
/// Each queue `q in 0..MAX_QUEUES` owns an exclusive 64 B cache line at `q * 64`
/// (512 B total), eliminating cross-core false sharing on CN106 and allowing
/// single-instruction NO-SFENCE `MOVDIR64B` atomic updates from saturn.
pub const CREDIT_BASE: usize = 0;

pub const CREDIT_MAGIC: u64 = 0xA55A_2130_0000_0000;
pub const PAYLOAD_COOKIE_MAGIC: u64 = 0x2130_CAFE_BABE_55AA;
pub const PAYLOAD_MID_XOR: u64 = 0x5555_AAAA_3333_CCCC;
pub const PAYLOAD_TAIL_MAGIC: u64 = 0xA5A5_5A5A_2130_F0F0;
pub const POISON_MAGIC: u64 = 0xDEAD_BEEF_2130_BAD0;

/// Byte offset of queue `q`'s dedicated 64 B BAR4 credit line.
#[inline(always)]
pub const fn credit_line_offset(q: usize) -> usize {
    assert!(q < MAX_QUEUES);
    CREDIT_BASE + q * 64
}

/// Deterministic signature covering `(taken_pkts, taken_bytes, run_gen, queue_id)`.
#[inline(always)]
pub const fn credit_sig(taken_pkts: u64, taken_bytes: u64, run_gen: u64, queue_id: u16) -> u64 {
    taken_pkts.wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ taken_bytes.wrapping_mul(0xbf58_476d_1ce4_e5b9)
        ^ run_gen.rotate_left(17)
        ^ ((queue_id as u64) << 48)
        ^ CREDIT_MAGIC
}

/// 64-byte cache-line-aligned credit record written by a single NO-SFENCE
/// `MOVDIR64B` from the host into CN106 BAR4 slot 15.
///
/// PCIe preserves order for Posted Writes to the same 64 B address, and
/// `MOVDIR64B` delivers all 64 bytes atomically; CN106 validates `w[0] == w[7]`
/// and `w[5] == credit_sig(...)`, then monotonically latches
/// `taken_cached = max(taken_cached, w[0])` (idempotent under coalescing).
#[repr(C, align(64))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct CreditLine {
    pub w: [u64; 8],
}

const _: () = assert!(size_of::<CreditLine>() == 64 && align_of::<CreditLine>() == 64);

impl CreditLine {
    #[inline(always)]
    pub const fn new(
        taken_pkts: u64,
        taken_bytes: u64,
        run_gen: u64,
        host_tsc: u64,
        queue_id: u16,
    ) -> Self {
        Self {
            w: [
                taken_pkts,
                run_gen,
                taken_bytes,
                host_tsc,
                (queue_id as u64) | (1u64 << 32), // bit 32 = 64B atomic MOVDIR64B
                credit_sig(taken_pkts, taken_bytes, run_gen, queue_id),
                0,
                taken_pkts,
            ],
        }
    }

    /// Parse a volatile-loaded 64 B credit line for `expected_run_gen` and `queue_id`.
    /// Returns `Some((taken_pkts, taken_bytes))` when valid, or `None` if not yet
    /// initialized for this run (or torn, tracked separately by `is_torn`).
    #[inline(always)]
    pub const fn verify(&self, expected_run_gen: u64, queue_id: u16) -> CreditPoll {
        let taken_pkts = self.w[0];
        let run_gen = self.w[1];
        let taken_bytes = self.w[2];
        let sig = self.w[5];
        let tail = self.w[7];
        if run_gen == 0 && taken_pkts == 0 {
            return CreditPoll::Empty;
        }
        if run_gen != expected_run_gen {
            return CreditPoll::Stale;
        }
        if tail != taken_pkts || sig != credit_sig(taken_pkts, taken_bytes, run_gen, queue_id) {
            return CreditPoll::Torn;
        }
        CreditPoll::Valid { taken_pkts, taken_bytes }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CreditPoll {
    Empty,
    Stale,
    Torn,
    Valid { taken_pkts: u64, taken_bytes: u64 },
}

/// The 5 single-variable standard-SDP receive architectures in MRV-213 (E2),
/// plus the unloaded `KvSolo` baseline.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Arm {
    /// Unloaded 32 MiB KV random lookup baseline on cpu41 (no NIC bursts).
    KvSolo,
    /// Arm 1: Large SDP monotonic circular ring (8192 desc + 128 MiB FIFO pool).
    LargeMono,
    /// Arm 2: Small passive SDP ring (256 desc, allow_small_oq=1), card drops on full.
    SmallDrop,
    /// Arm 3: Ideal host LIFO stack pool + large SDP ring (8192 desc + 128 MiB stack pool).
    LargeLifo,
    /// Arm 4: Native SDP SLIST_DBELL/WMARK backpressure + card elastic FIFO (zero BAR4 credit).
    NativeBp,
    /// Arm 5: Full design — On-Path SmartNIC elastic FIFO + compact hot SDP ring/pool + NO-SFENCE BAR4 credit.
    CreditRing,
}

impl Arm {
    pub fn as_str(self) -> &'static str {
        match self {
            Arm::KvSolo => "kv-solo",
            Arm::LargeMono => "large-mono",
            Arm::SmallDrop => "small-drop",
            Arm::LargeLifo => "large-lifo",
            Arm::NativeBp => "native-bp",
            Arm::CreditRing => "credit-ring",
        }
    }

    pub fn card_policy(self) -> &'static str {
        match self {
            Arm::KvSolo => "idle",
            Arm::LargeMono | Arm::LargeLifo => "passthrough",
            Arm::SmallDrop => "drop",
            Arm::NativeBp => "native-bp",
            Arm::CreditRing => "credit-ring",
        }
    }

    pub fn uses_bar4(self) -> bool {
        self == Arm::CreditRing
    }

    pub fn retains_in_host(self) -> bool {
        matches!(self, Arm::LargeMono | Arm::LargeLifo)
    }

    pub fn expects_drops(self) -> bool {
        self == Arm::SmallDrop
    }
}

impl fmt::Display for Arm {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Real 100GbE packet profile sent from `mercury` (`mlx5_2` / `ens6f1np1`, zero root).
///
/// Exact L2 Ethernet frame sizes (including 4 B FCS counted by NIC PHY counters):
/// - `Uc64`:    `IBV_QPT_UC` 0 B payload -> 64 B L2 (`60 B` mbuf, 84 B wire; HW BTH.PSN + ICRC + FCS)
/// - `Ud256`:   `IBV_QPT_UD` 186 B payload -> 256 B L2 (`252 B` mbuf, 276 B wire; inline RoCEv2 UD)
/// - `Udp1500`: Linux `UDP_SEGMENT` GSO / `sendmmsg` 1454 B payload -> 1500 B L2 (`1496 B` mbuf, 1520 B wire; zero IP frag on MTU 1500)
/// - `Ud1094`:  Optional `IBV_QPT_UD` 1024 B payload -> 1094 B L2 (`1090 B` mbuf, 1114 B wire; max single-MTU RoCEv2 UD on MTU 1500)
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PktProfile {
    Uc64,
    Ud256,
    Udp1500,
    Ud1094,
}

impl PktProfile {
    pub fn as_str(self) -> &'static str {
        match self {
            PktProfile::Uc64 => "64B",
            PktProfile::Ud256 => "256B",
            PktProfile::Udp1500 => "1500B",
            PktProfile::Ud1094 => "1094B",
        }
    }

    pub fn from_name(s: &str) -> Option<Self> {
        match s.to_ascii_lowercase().as_str() {
            "64" | "64b" | "uc64" => Some(PktProfile::Uc64),
            "256" | "256b" | "ud256" => Some(PktProfile::Ud256),
            "1500" | "1500b" | "udp1500" => Some(PktProfile::Udp1500),
            "1094" | "1094b" | "ud1094" => Some(PktProfile::Ud1094),
            _ => None,
        }
    }

    /// Exact L2 Ethernet frame size on wire (including 4 B Ethernet FCS).
    pub const fn l2_bytes(self) -> usize {
        match self {
            PktProfile::Uc64 => 64,
            PktProfile::Ud256 => 254,
            PktProfile::Udp1500 => 1500,
            PktProfile::Ud1094 => 1094,
        }
    }

    /// Wire bytes per frame (L2 + 20 B Ethernet preamble/SFD/IFG).
    pub const fn wire_bytes(self) -> usize {
        self.l2_bytes() + 20
    }

    /// DPDK mbuf `data_len` (L2 minus 4 B FCS).
    pub const fn mbuf_len(self) -> usize {
        self.l2_bytes() - 4
    }

    /// Application payload bytes inside the transport header (0 for `Uc64`).
    pub const fn payload_len(self) -> usize {
        match self {
            PktProfile::Uc64 => 0,
            PktProfile::Ud256 => 184,    // 62 (Eth+IP+UDP+BTH+DETH) + 184 (4B/8B aligned) + 4(ICRC) + 4(FCS) = 254
            PktProfile::Udp1500 => 1454, // 42 (Eth+IP+UDP) + 1454 + 4(FCS) = 1500
            PktProfile::Ud1094 => 1024,  // 62 + 1024 + 4 + 4 = 1094
        }
    }

    /// Byte offset inside the mbuf where application payload begins.
    pub const fn payload_offset(self) -> usize {
        match self {
            PktProfile::Uc64 => OFF_BTH + 12,
            PktProfile::Ud256 | PktProfile::Ud1094 => OFF_UD_PAYLOAD,
            PktProfile::Udp1500 => OFF_UDP_PAYLOAD,
        }
    }

    /// Expected UDP destination port.
    pub const fn udp_dst_port(self) -> u16 {
        match self {
            PktProfile::Udp1500 => DATA_UDP_PORT,
            PktProfile::Uc64 | PktProfile::Ud256 | PktProfile::Ud1094 => ROCE_UDP_PORT,
        }
    }

    /// Compact slot stride (64 B aligned) for CN106's L3/DDR5 elastic slab FIFO.
    pub const fn card_slab_stride(self) -> usize {
        match self {
            PktProfile::Uc64 => 64,
            PktProfile::Ud256 => 256,
            PktProfile::Udp1500 => 1536,
            PktProfile::Ud1094 => 1152,
        }
    }

    /// Recommended DPDK mbuf `data_room_size` on the host so even a 128 MiB
    /// host pool (2.1M packets @ 64 B) fits comfortably inside saturn's 4 GiB
    /// Node-1 hugepages while respecting `octeon_ep` (`data_room - 128 >= mbuf_len + 8`).
    pub const fn host_data_room(self) -> u16 {
        match self {
            PktProfile::Uc64 => 256,     // 128 headroom + 128 buf >= 60 + 8 (384 B/mbuf -> 805 MiB @ 2.1M)
            PktProfile::Ud256 => 512,    // 128 headroom + 384 buf >= 252 + 8 (640 B/mbuf -> 335 MiB @ 524K)
            PktProfile::Udp1500 => 2176, // 128 headroom + 2048 buf >= 1496 + 8 (standard MTU 1500)
            PktProfile::Ud1094 => 2176,
        }
    }
}

/// Exact number of packets in a burst of `burst_bytes` L2 bytes, rounded up to
/// a multiple of `align_pkts` (typically `threads * batch` or `64`).
#[inline(always)]
pub const fn burst_packet_count(burst_bytes: usize, profile: PktProfile, align_pkts: usize) -> u64 {
    let raw = burst_bytes.div_ceil(profile.l2_bytes());
    let a = if align_pkts == 0 { 1 } else { align_pkts };
    (raw.div_ceil(a) * a) as u64
}

/// Pack payload metadata word:
/// `[run_gen_16 (48..64) | burst_id_16 (32..48) | stream_id_16 (16..32) | l2_bytes_16 (0..16)]`.
#[inline(always)]
pub const fn pack_payload_meta(run_gen: u64, burst_id: u16, stream_id: u16, l2_bytes: u16) -> u64 {
    ((run_gen & 0xffff) << 48)
        | ((burst_id as u64) << 32)
        | ((stream_id as u64) << 16)
        | (l2_bytes as u64)
}

#[inline(always)]
pub const fn unpack_payload_meta(meta: u64) -> (u16, u16, u16, u16) {
    (
        ((meta >> 48) & 0xffff) as u16,
        ((meta >> 32) & 0xffff) as u16,
        ((meta >> 16) & 0xffff) as u16,
        (meta & 0xffff) as u16,
    )
}

#[inline(always)]
pub const fn payload_cookie(stream_id: u16, run_gen: u64, seq: u64) -> u64 {
    seq.wrapping_mul(0x9e37_79b9_7f4a_7c15)
        ^ ((stream_id as u64).wrapping_mul(0xbf58_476d_1ce4_e5b9))
        ^ ((run_gen & 0xffff).wrapping_mul(0x94d0_49bb_1331_11eb))
        ^ PAYLOAD_COOKIE_MAGIC
}

#[inline(always)]
pub const fn payload_tail(stream_id: u16, run_gen: u64, seq: u64) -> u64 {
    let v = seq.wrapping_mul(0x6c62_272e_07bb_0142)
        ^ ((stream_id as u64).wrapping_mul(0x517c_c1b7_2722_0a95))
        ^ ((run_gen & 0xffff).wrapping_mul(0x2545_f491_4f6c_dd1d))
        ^ PAYLOAD_TAIL_MAGIC;
    if v == 0 { 1 } else { v }
}

/// Stamp untouched end-to-end integrity header, midpoint, and tail into a
/// payload buffer (`len >= 40`) for `Ud256`, `Udp1500`, or `Ud1094`.
#[inline(always)]
pub fn stamp_payload(payload: &mut [u8], stream_id: u16, run_gen: u64, burst_id: u16, seq: u64, tx_tsc: u64, l2_bytes: u16) {
    let len = payload.len();
    debug_assert!(len >= 40);
    let meta = pack_payload_meta(run_gen, burst_id, stream_id, l2_bytes);
    let cookie = payload_cookie(stream_id, run_gen, seq);
    let tail = payload_tail(stream_id, run_gen, seq);
    let mid = (len / 2) & !7;
    unsafe {
        let p = payload.as_mut_ptr();
        p.cast::<u64>().write_unaligned(seq);
        p.add(8).cast::<u64>().write_unaligned(meta);
        p.add(16).cast::<u64>().write_unaligned(cookie);
        p.add(24).cast::<u64>().write_unaligned(tx_tsc);
        if mid >= 32 && mid + 8 <= len - 8 {
            p.add(mid).cast::<u64>().write_unaligned(cookie ^ PAYLOAD_MID_XOR);
        }
        p.add(len - 8).cast::<u64>().write_unaligned(tail);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WireCheck {
    /// Not a data packet for this experiment (e.g. ARP or background traffic).
    Ignore,
    /// Valid data packet with verified header, midpoint, and tail.
    Ok { stream_id: u16, burst_id: u16, seq: u64, is_psn24: bool },
    /// Corrupt header/length/cookie.
    Bad,
    /// Torn DMA write (valid header/cookie, mismatched mid or tail sentinel).
    Torn,
}

/// Verify an untouched wire Ethernet frame received on CN106 `eth0` or saturn `octeon_ep`.
///
/// When `expected_run_gen == 0`, accepts any `run_gen` (used on card before run sync).
#[inline(always)]
pub fn verify_wire_frame(frame: &[u8], profile: PktProfile, expected_run_gen: u64) -> WireCheck {
    if frame.len() < 42 {
        return WireCheck::Ignore;
    }
    // Check IPv4 + UDP + matching destination port
    if frame[OFF_ETH_TYPE] != 0x08 || frame[OFF_ETH_TYPE + 1] != 0x00 {
        return WireCheck::Ignore;
    }
    if frame[OFF_IPV4 + 9] != 17 {
        return WireCheck::Ignore;
    }
    let dst_port = u16::from_be_bytes([frame[OFF_UDP + 2], frame[OFF_UDP + 3]]);
    if dst_port != profile.udp_dst_port() {
        return WireCheck::Ignore;
    }

    if frame.len() != profile.mbuf_len() {
        return WireCheck::Bad;
    }

    if profile == PktProfile::Uc64 {
        // IBV_QPT_UC 0-byte payload: 60 B frame without FCS.
        // BTH opcode at OFF_BTH (0x24 = UC Send Only), dst_qp at OFF_BTH+5..8, psn at OFF_BTH+9..12.
        let bth_op = frame[OFF_BTH];
        if bth_op != 0x24 {
            return WireCheck::Bad;
        }
        let dqpn = ((frame[OFF_BTH + 5] as u32) << 16)
            | ((frame[OFF_BTH + 6] as u32) << 8)
            | (frame[OFF_BTH + 7] as u32);
        if dqpn < UC64_BASE_QPN || (dqpn - UC64_BASE_QPN) >= MAX_STREAMS as u32 {
            return WireCheck::Bad;
        }
        let stream_id = (dqpn - UC64_BASE_QPN) as u16;
        let psn = ((frame[OFF_BTH + 9] as u64) << 16)
            | ((frame[OFF_BTH + 10] as u64) << 8)
            | (frame[OFF_BTH + 11] as u64);
        // Check that the trailing 4-byte RoCEv2 ICRC word was not left at poison sentinel.
        let tail_u32 = unsafe { frame.as_ptr().add(frame.len() - 4).cast::<u32>().read_unaligned() };
        if tail_u32 == (POISON_MAGIC as u32) {
            return WireCheck::Torn;
        }
        return WireCheck::Ok { stream_id, burst_id: 0, seq: psn, is_psn24: true };
    }

    let off = profile.payload_offset();
    let plen = profile.payload_len();
    if off + plen > frame.len() {
        return WireCheck::Bad;
    }
    let p = unsafe { frame.as_ptr().add(off) };
    let seq = unsafe { p.cast::<u64>().read_unaligned() };
    let meta = unsafe { p.add(8).cast::<u64>().read_unaligned() };
    let cookie = unsafe { p.add(16).cast::<u64>().read_unaligned() };
    let tail = unsafe { p.add(plen - 8).cast::<u64>().read_unaligned() };

    let (rg16, burst_id, stream_id, l2_bytes) = unpack_payload_meta(meta);
    if l2_bytes as usize != profile.l2_bytes() || (stream_id as usize) >= MAX_STREAMS {
        return WireCheck::Bad;
    }
    if expected_run_gen != 0 && rg16 != (expected_run_gen & 0xffff) as u16 {
        return WireCheck::Ignore;
    }
    let rg_eff = rg16 as u64;
    let want_cookie = payload_cookie(stream_id, rg_eff, seq);
    if cookie != want_cookie {
        return WireCheck::Bad;
    }
    let mid = (plen / 2) & !7;
    if mid >= 32 && mid + 8 <= plen - 8 {
        let mid_val = unsafe { p.add(mid).cast::<u64>().read_unaligned() };
        if mid_val != (want_cookie ^ PAYLOAD_MID_XOR) {
            return WireCheck::Torn;
        }
    }
    if tail != payload_tail(stream_id, rg_eff, seq) {
        return WireCheck::Torn;
    }
    WireCheck::Ok { stream_id, burst_id, seq, is_psn24: false }
}

/// Overwrite key words inside an mbuf with `POISON_MAGIC` before freeing back to
/// the pool so any stale/unwritten DMA buffer is detected immediately.
#[inline(always)]
pub fn poison_wire_mbuf(frame: &mut [u8], profile: PktProfile) {
    let len = frame.len();
    if len < 60 {
        return;
    }
    let p = frame.as_mut_ptr();
    unsafe {
        if profile == PktProfile::Uc64 {
            p.add(len - 4).cast::<u32>().write_unaligned(POISON_MAGIC as u32);
        } else {
            let off = profile.payload_offset();
            let plen = profile.payload_len();
            if off + plen <= len {
                let b = p.add(off);
                b.cast::<u64>().write_unaligned(POISON_MAGIC);
                b.add(16).cast::<u64>().write_unaligned(POISON_MAGIC);
                b.add(plen - 8).cast::<u64>().write_unaligned(POISON_MAGIC);
            }
        }
    }
}

/// Per-stream monotonic sequence gap detector (handles both 24-bit UC PSN wrap
/// and 64-bit UD/UDP sequence numbers).
#[derive(Clone)]
pub struct StreamTracker {
    seen: [bool; MAX_STREAMS],
    last_seq: [u64; MAX_STREAMS],
    pub gaps: u64,
    pub out_of_order: u64,
    pub pkts: u64,
}

impl Default for StreamTracker {
    fn default() -> Self {
        Self::new()
    }
}

impl StreamTracker {
    pub const fn new() -> Self {
        Self {
            seen: [false; MAX_STREAMS],
            last_seq: [0; MAX_STREAMS],
            gaps: 0,
            out_of_order: 0,
            pkts: 0,
        }
    }

    pub fn reset(&mut self) {
        self.seen = [false; MAX_STREAMS];
        self.last_seq = [0; MAX_STREAMS];
        self.gaps = 0;
        self.out_of_order = 0;
        self.pkts = 0;
    }

    #[inline(always)]
    pub fn observe(&mut self, stream_id: u16, seq: u64, is_psn24: bool) {
        let idx = stream_id as usize;
        if idx >= MAX_STREAMS {
            return;
        }
        self.pkts += 1;
        if !self.seen[idx] {
            self.seen[idx] = true;
            self.last_seq[idx] = seq;
            return;
        }
        if is_psn24 {
            let diff = seq.wrapping_sub(self.last_seq[idx]) & 0x00ff_ffff;
            if diff == 1 {
                self.last_seq[idx] = seq;
            } else if diff > 1 && diff < 0x0080_0000 {
                self.gaps += diff - 1;
                self.last_seq[idx] = seq;
            } else if diff != 0 {
                self.out_of_order += 1;
            }
        } else if seq > self.last_seq[idx] {
            let diff = seq - self.last_seq[idx];
            if diff > 1 {
                self.gaps += diff - 1;
            }
            self.last_seq[idx] = seq;
        } else {
            self.out_of_order += 1;
        }
    }
}

/// Verify Pitfall-1 hardware safety invariant for `CreditRing`:
/// `r_eff + DROQ_REFILL_THRESHOLD < rx_desc - wmark`, where
/// `wmark = if rx_desc < 2048 { min(256, rx_desc / 4) } else { 256 }`.
#[inline(always)]
pub const fn sdp_wmark(rx_desc: u16) -> u64 {
    if rx_desc < 2048 {
        let q = (rx_desc / 4) as u64;
        if q < 256 { q } else { 256 }
    } else {
        256
    }
}

#[inline(always)]
pub const fn credit_window_avoids_wmark(rx_desc: u16, r_eff: u64) -> bool {
    let hw_headroom = (rx_desc as u64).saturating_sub(sdp_wmark(rx_desc));
    r_eff.saturating_add(DROQ_REFILL_THRESHOLD) < hw_headroom
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_udp_frame(profile: PktProfile, stream_id: u16, run_gen: u64, burst_id: u16, seq: u64) -> Vec<u8> {
        let mut frame = vec![0u8; profile.mbuf_len()];
        frame[OFF_ETH_TYPE] = 0x08;
        frame[OFF_ETH_TYPE + 1] = 0x00;
        frame[OFF_IPV4] = 0x45;
        frame[OFF_IPV4 + 9] = 17;
        frame[OFF_UDP + 2..OFF_UDP + 4].copy_from_slice(&profile.udp_dst_port().to_be_bytes());
        if profile == PktProfile::Uc64 {
            frame[OFF_BTH] = 0x24;
            let dqpn = UC64_BASE_QPN + stream_id as u32;
            frame[OFF_BTH + 5] = ((dqpn >> 16) & 0xff) as u8;
            frame[OFF_BTH + 6] = ((dqpn >> 8) & 0xff) as u8;
            frame[OFF_BTH + 7] = (dqpn & 0xff) as u8;
            frame[OFF_BTH + 9] = ((seq >> 16) & 0xff) as u8;
            frame[OFF_BTH + 10] = ((seq >> 8) & 0xff) as u8;
            frame[OFF_BTH + 11] = (seq & 0xff) as u8;
            frame[56..60].copy_from_slice(&0x1234_5678u32.to_le_bytes());
        } else {
            let off = profile.payload_offset();
            let plen = profile.payload_len();
            stamp_payload(
                &mut frame[off..off + plen],
                stream_id,
                run_gen,
                burst_id,
                seq,
                0xdead_beef,
                profile.l2_bytes() as u16,
            );
        }
        frame
    }

    #[test]
    fn test_profile_geometry() {
        assert_eq!(PktProfile::Uc64.l2_bytes(), 64);
        assert_eq!(PktProfile::Uc64.mbuf_len(), 60);
        assert_eq!(PktProfile::Ud256.l2_bytes(), 254);
        assert_eq!(PktProfile::Ud256.mbuf_len(), 250);
        assert_eq!(OFF_UD_PAYLOAD + PktProfile::Ud256.payload_len() + 4, 250);
        assert_eq!(PktProfile::Udp1500.l2_bytes(), 1500);
        assert_eq!(PktProfile::Udp1500.mbuf_len(), 1496);
        assert_eq!(OFF_UDP_PAYLOAD + PktProfile::Udp1500.payload_len(), 1496);
        assert_eq!(PktProfile::Ud1094.l2_bytes(), 1094);
        assert_eq!(OFF_UD_PAYLOAD + PktProfile::Ud1094.payload_len() + 4, 1090);
        // Verify octeon_ep buffer room fits packet + 8B DROQ info word
        for p in [PktProfile::Uc64, PktProfile::Ud256, PktProfile::Udp1500, PktProfile::Ud1094] {
            let buf_room = (p.host_data_room() as usize) - 128;
            assert!(buf_room >= p.mbuf_len() + 8, "{p:?} buf_room={buf_room}");
        }
    }

    #[test]
    fn test_wire_verify_and_torn_detection() {
        for profile in [PktProfile::Uc64, PktProfile::Ud256, PktProfile::Udp1500, PktProfile::Ud1094] {
            let mut f = make_udp_frame(profile, 3, 42, 7, 12345);
            let res = verify_wire_frame(&f, profile, 42);
            match res {
                WireCheck::Ok { stream_id, seq, .. } => {
                    assert_eq!(stream_id, 3);
                    assert_eq!(seq, 12345);
                }
                other => panic!("expected Ok for {profile:?}, got {other:?}"),
            }

            // Corrupt tail -> Torn
            if profile == PktProfile::Uc64 {
                poison_wire_mbuf(&mut f, profile);
                assert_eq!(verify_wire_frame(&f, profile, 42), WireCheck::Torn);
            } else {
                let tail_off = profile.payload_offset() + profile.payload_len() - 8;
                f[tail_off] ^= 0xff;
                assert_eq!(verify_wire_frame(&f, profile, 42), WireCheck::Torn);
                f[tail_off] ^= 0xff;
                // Corrupt midpoint -> Torn
                let mid = (profile.payload_len() / 2) & !7;
                f[profile.payload_offset() + mid] ^= 0x01;
                assert_eq!(verify_wire_frame(&f, profile, 42), WireCheck::Torn);
                f[profile.payload_offset() + mid] ^= 0x01;
                // Corrupt cookie -> Bad
                f[profile.payload_offset() + 16] ^= 0x01;
                assert_eq!(verify_wire_frame(&f, profile, 42), WireCheck::Bad);
                // Wrong run_gen -> Ignore
                let f2 = make_udp_frame(profile, 3, 99, 7, 12345);
                assert_eq!(verify_wire_frame(&f2, profile, 42), WireCheck::Ignore);
            }
        }
    }

    #[test]
    fn test_credit_line_verify_and_wmark_invariant() {
        let mut line = CreditLine::new(1024, 1024 * 1500, 77, 99999, 2);
        assert_eq!(
            line.verify(77, 2),
            CreditPoll::Valid { taken_pkts: 1024, taken_bytes: 1024 * 1500 }
        );
        assert_eq!(line.verify(78, 2), CreditPoll::Stale);
        assert_eq!(line.verify(77, 3), CreditPoll::Torn);
        line.w[7] = 1023;
        assert_eq!(line.verify(77, 2), CreditPoll::Torn);

        // Pitfall 1: rx_desc=512 (WMARK=128 -> headroom=384), R_eff=256 + 64 = 320 < 384 (safe!)
        assert!(credit_window_avoids_wmark(512, 256));
        assert!(credit_window_avoids_wmark(512, 128));
        assert!(!credit_window_avoids_wmark(512, 320));
        assert!(!credit_window_avoids_wmark(256, 128)); // 256 - 64 = 192; 128 + 64 = 192 not < 192
        assert!(credit_window_avoids_wmark(256, 96));
    }

    #[test]
    fn test_stream_tracker_psn24_wrap_and_gaps() {
        let mut tr = StreamTracker::new();
        tr.observe(0, 0x00ff_fffe, true);
        tr.observe(0, 0x00ff_ffff, true);
        tr.observe(0, 0x0000_0000, true);
        tr.observe(0, 0x0000_0001, true);
        assert_eq!(tr.gaps, 0);
        assert_eq!(tr.out_of_order, 0);
        tr.observe(0, 0x0000_0004, true);
        assert_eq!(tr.gaps, 2);

        let mut tr64 = StreamTracker::new();
        tr64.observe(1, 10, false);
        tr64.observe(1, 11, false);
        tr64.observe(1, 15, false);
        tr64.observe(1, 14, false);
        assert_eq!(tr64.gaps, 3);
        assert_eq!(tr64.out_of_order, 1);
    }

    #[test]
    fn test_burst_packet_count() {
        assert_eq!(burst_packet_count(64 * 1024, PktProfile::Uc64, 64), 1024);
        assert_eq!(burst_packet_count(128 * 1024 * 1024, PktProfile::Uc64, 64), 2_097_152);
        let n256 = burst_packet_count(128 * 1024 * 1024, PktProfile::Ud256, 64);
        assert_eq!(n256 % 64, 0);
        assert!(n256 * 254 >= 128 * 1024 * 1024);
        let n1500 = burst_packet_count(128 * 1024 * 1024, PktProfile::Udp1500, 64);
        assert_eq!(n1500 % 64, 0);
        assert!(n1500 * 1500 >= 128 * 1024 * 1024);
    }
}
