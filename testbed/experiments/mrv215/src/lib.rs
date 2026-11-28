//! Shared wire formats, RoCEv2 ICRC templates, standard Ethernet/IPv4/UDP
//! helpers, multi-datagram Memcached Binary UDP reassembler, and 1 s FastHist
//! for MRV-215 (Linux Kernel `octeon_ep_vf` Zero-Modification Upstream
//! Memcached 1.6.45 UDP Socket Control Benchmark).

/// Maximum CN106 worker cores supported (`eth0` 1..4 RxQ/TxQ RSS).
pub const MAX_CARD_CORES: usize = 4;
/// Maximum mercury client threads / peer slots supported.
pub const MAX_PEERS: usize = 8;

/// Default Memcached keyspace size (8,192 keys; identical to MRV-206).
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

/// Small-message threshold matching MRV-206 (`<= 192 B` Memcached UDP payload).
pub const OP_PUSH_MAX_MC_BYTES: usize = 192;
pub const MAX_MC_BYTES: usize = 4032;
pub const MAX_FRAG_PAYLOAD: usize = 1008;
pub const MAX_SEG_BYTES: usize = 1024;
pub const MAX_RPC_BYTES: usize = 4096;

/// Max DPDK burst drain per poll (identical to MRV-206).
pub const MAX_DRAIN: usize = 32;

/// Link-layer RoCEv2 UD opcodes in byte 7 (`w0 >> 56`): always non-zero, so they
/// never collide with raw standard Memcached UDP datagrams whose reserved byte 7 is `0x00`.
pub const OP_FRAG: u8 = 0xfa;
pub const OP_PROBE_WIRE: u8 = 0xfe;

/// Bit masks for wire metadata.
pub const WIRE_SEQ_MASK: u64 = (1u64 << 38) - 1;

/// Card control/sync TCP port for MRV-215 (same port as MRV-206; exclusive nic-lock).
pub const SYNC_ADDR: &str = "10.10.0.2:7539";
/// Host monitor/populator TCP control port for MRV-215.
pub const HOST_MON_ADDR: &str = "127.0.0.1:7545";

/// Isolated RFC1918 data-plane VF subnet (`sdp1-1` <-> host VF0 `0000:99:02.0`).
/// Disjoint from control plane (`10.10.0.0/24` on `ens1`/`sdp1-0`) and 100GbE (`10.10.100.0/24`).
pub const SDP_HOST_IP: [u8; 4] = [10, 10, 200, 1];
pub const SDP_CARD_IP: [u8; 4] = [10, 10, 200, 2];
pub const SDP_SPORT_BASE: u16 = 5000;

/// 100GbE physical link MTU / mbuf room (matches mercury ens6f1np1 MTU 1500).
pub const ETH_MBUF_BUF_SIZE: u16 = 2176;
/// SDP link MTU / mbuf room (supports both MTU 1500 and MTU 9000 jumbo frames).
pub const SDP_MTU: u16 = 9000;
pub const SDP_MBUF_BUF_SIZE: u16 = 10240;

/// RoCEv2 UD wire header constants: Eth(14) + IPv4(20) + UDP(8) + BTH(12) + DETH(8) = 62 B.
pub const ROCE_HDR_LEN: usize = 62;
pub const ROCE_ICRC_LEN: usize = 4;
pub const ROCE_UDP_PORT: u16 = 4791;
pub const ROCE_QKEY: u32 = 0x1111_1111;
pub const ROCE_CARD_QPN: u32 = 0x0001_2345;

/// Pack 64-bit RoCEv2 wire fragment / probe metadata (identical to MRV-206).
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
#[inline(always)]
pub const fn roce_frag_cnt(mc_len: usize) -> u8 {
    if mc_len <= MAX_SEG_BYTES {
        1
    } else {
        mc_len.div_ceil(MAX_FRAG_PAYLOAD) as u8
    }
}

/// Standard Memcached UDP datagram count for a binary payload of `bin_len` bytes.
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
// Standard Memcached Key, Value, and Workload Profile Helpers (identical to MRV-206)
// -----------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum McProfile {
    V8,
    V128,
    V1024,
    V3840,
    SmallMix,
    EtcMix,
}

impl McProfile {
    pub fn from_str_opt(s: &str) -> Option<Self> {
        match s {
            "8" | "get8" | "set8" | "rw8" => Some(McProfile::V8),
            "128" | "get128" | "set128" | "rw128" => Some(McProfile::V128),
            "1024" | "get1024" | "set1024" => Some(McProfile::V1024),
            "3840" | "get3840" | "set3840" => Some(McProfile::V3840),
            "small_mix" | "etc_small" => Some(McProfile::SmallMix),
            "etc_mix" | "mix" | "etc" => Some(McProfile::EtcMix),
            _ => None,
        }
    }

    pub fn val_len_for_key(self, key: usize, num_keys: usize) -> usize {
        match self {
            McProfile::V8 => 8,
            McProfile::V128 => 128,
            McProfile::V1024 => 1024,
            McProfile::V3840 => 3840,
            McProfile::SmallMix => small_mix_val_len_for_key(key, num_keys),
            McProfile::EtcMix => mix_val_len_for_key(key, num_keys),
        }
    }
}

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
///   - `0..40%`:    8 B
///   - `40%..80%`:  128 B
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

/// Small-only Facebook USR/ETC mix (`50% 8 B + 50% 128 B`).
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

/// Verify deterministic self-verifying value payload (identical to `mrv206_client.rs`).
pub fn verify_mc_value(expected_key: u64, val: &[u8]) -> bool {
    let n = val.len();
    if n < 8 || (n & 7) != 0 {
        return false;
    }
    if n == 8 {
        let k32 = u32::from_le_bytes([val[0], val[1], val[2], val[3]]);
        let v16 = u16::from_le_bytes([val[4], val[5]]);
        let tag = u16::from_le_bytes([val[6], val[7]]);
        return k32 == (expected_key as u32) && v16 >= 1 && tag == mc_val8_tag(k32, v16);
    }
    let n_words = n / 8;
    let k = u64::from_le_bytes(val[0..8].try_into().unwrap());
    let ver = u64::from_le_bytes(val[8..16].try_into().unwrap());
    if k != expected_key || ver == 0 {
        return false;
    }
    let mut acc = splitmix64(k ^ ver.rotate_left(17) ^ (n as u64).rotate_left(33));
    for wi in 2..(n_words - 1) {
        let w = u64::from_le_bytes(val[wi * 8..(wi + 1) * 8].try_into().unwrap());
        if w != mc_val_word(k, ver, wi) {
            return false;
        }
        acc = acc.rotate_left(7).wrapping_add(w ^ 0x517c_c1b7_2722_0a95);
    }
    if n_words >= 3 {
        let last = (n_words - 1) * 8;
        let got_acc = u64::from_le_bytes(val[last..last + 8].try_into().unwrap());
        if got_acc != acc {
            return false;
        }
    }
    true
}

/// Build a standard Memcached Binary TCP SET packet (`24 + 8 + 16 + val_len` bytes)
/// for fast out-of-band population of unmodified upstream `memcached`.
pub fn build_mc_set_tcp(opaque: u32, key: u64, ver: u64, val_len: usize, out: &mut [u8]) -> usize {
    let total = MC_BIN_HDR_LEN + 8 + MC_KEY_LEN + val_len;
    let h = &mut out[0..24];
    h.fill(0);
    h[0] = MC_MAGIC_REQ;
    h[1] = MC_CMD_SET;
    h[2..4].copy_from_slice(&(MC_KEY_LEN as u16).to_be_bytes());
    h[4] = 8; // extlen (flags + exptime)
    let body_len = (8 + MC_KEY_LEN + val_len) as u32;
    h[8..12].copy_from_slice(&body_len.to_be_bytes());
    h[12..16].copy_from_slice(&opaque.to_be_bytes());

    out[24..28].copy_from_slice(&MC_ITEM_FLAGS.to_be_bytes());
    out[28..32].copy_from_slice(&0u32.to_be_bytes());

    let mut kbuf = [0u8; MC_KEY_LEN];
    format_mc_key(key, &mut kbuf);
    out[32..32 + MC_KEY_LEN].copy_from_slice(&kbuf);
    fill_mc_value(key, ver, &mut out[32 + MC_KEY_LEN..total]);
    total
}

/// Build a standard Memcached Binary UDP GET packet (`8 + 24 + 16 = 48 B`).
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

/// Build a standard Memcached Binary UDP SET packet (`8 + 24 + 8 + 16 + val_len` bytes).
pub fn build_mc_set_udp(
    req_id: u16,
    opaque: u32,
    key: u64,
    ver: u64,
    val_len: usize,
    out: &mut [u8],
) -> usize {
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
// Standard Ethernet / IPv4 / UDP & ARP Framing for Linux Kernel `octeon_ep_vf`
// -----------------------------------------------------------------------------

pub fn parse_mac(s: &str) -> Result<[u8; 6], String> {
    let parts: Vec<&str> = s.trim().split(':').collect();
    if parts.len() != 6 {
        return Err(format!("invalid MAC '{s}': expected 6 colon-separated octets"));
    }
    let mut mac = [0u8; 6];
    for (i, p) in parts.iter().enumerate() {
        mac[i] = u8::from_str_radix(p, 16)
            .map_err(|_| format!("invalid hex octet '{p}' in MAC '{s}'"))?;
    }
    Ok(mac)
}

pub fn format_mac(mac: [u8; 6]) -> String {
    format!(
        "{:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x}",
        mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]
    )
}

/// RFC 1071 16-bit one's complement IPv4 header checksum.
#[inline(always)]
pub fn ipv4_checksum(hdr: &[u8]) -> u16 {
    let mut sum: u32 = 0;
    let mut i = 0usize;
    while i + 1 < hdr.len() {
        sum += u16::from_be_bytes([hdr[i], hdr[i + 1]]) as u32;
        i += 2;
    }
    if i < hdr.len() {
        sum += (hdr[i] as u32) << 8;
    }
    while (sum >> 16) != 0 {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    !(sum as u16)
}

/// Write a standard unfragmented Ethernet(14) + IPv4(20) + UDP(8) + `payload` frame.
/// Computes a valid IPv4 header checksum (required by Linux kernel IPv4 RX) and sets
/// IPv4 UDP checksum = 0 (standard RFC 768 no-checksum accepted by Linux `udp4_csum_init`).
/// Returns the Ethernet frame byte length (padded to >= 60 B if needed).
#[inline(always)]
pub fn write_ipv4_udp_frame(
    dst_mac: [u8; 6],
    src_mac: [u8; 6],
    src_ip: [u8; 4],
    dst_ip: [u8; 4],
    sport: u16,
    dport: u16,
    ip_id: u16,
    payload: &[u8],
    out: &mut [u8],
) -> usize {
    let raw_len = 42 + payload.len();
    let frame_len = raw_len.max(60);
    out[0..6].copy_from_slice(&dst_mac);
    out[6..12].copy_from_slice(&src_mac);
    out[12..14].copy_from_slice(&0x0800u16.to_be_bytes());

    let ip_tot = (20 + 8 + payload.len()) as u16;
    out[14] = 0x45;
    out[15] = 0x00;
    out[16..18].copy_from_slice(&ip_tot.to_be_bytes());
    out[18..20].copy_from_slice(&ip_id.to_be_bytes());
    out[20..22].copy_from_slice(&0x4000u16.to_be_bytes()); // DF
    out[22] = 64;
    out[23] = 17; // UDP
    out[24..26].copy_from_slice(&0u16.to_be_bytes());
    out[26..30].copy_from_slice(&src_ip);
    out[30..34].copy_from_slice(&dst_ip);
    let csum = ipv4_checksum(&out[14..34]);
    out[24..26].copy_from_slice(&csum.to_be_bytes());

    let udp_len = (8 + payload.len()) as u16;
    out[34..36].copy_from_slice(&sport.to_be_bytes());
    out[36..38].copy_from_slice(&dport.to_be_bytes());
    out[38..40].copy_from_slice(&udp_len.to_be_bytes());
    out[40..42].copy_from_slice(&0u16.to_be_bytes()); // RFC 768 UDP checksum optional for IPv4

    out[42..raw_len].copy_from_slice(payload);
    if frame_len > raw_len {
        out[raw_len..frame_len].fill(0);
    }
    frame_len
}

/// Build RFC 791 IPv4 fragments carrying a single UDP datagram when `20 + 8 + payload.len() > mtu`.
/// Emits each Ethernet frame via `emit(frame_slice)`. Used automatically if `sdp_mtu < 4000`
/// (e.g. MTU 1500 fallback for `set3840`).
pub fn emit_ipv4_udp_fragments<F>(
    dst_mac: [u8; 6],
    src_mac: [u8; 6],
    src_ip: [u8; 4],
    dst_ip: [u8; 4],
    sport: u16,
    dport: u16,
    ip_id: u16,
    mtu: usize,
    payload: &[u8],
    mut emit: F,
) where
    F: FnMut(&[u8]),
{
    let udp_tot = 8 + payload.len();
    let max_ip_pay = (mtu.saturating_sub(20)) & !7;
    debug_assert!(max_ip_pay >= 8);

    // Build contiguous IP payload: 8 B UDP header + payload.
    let mut ip_pay = [0u8; 8 + MAX_RPC_BYTES];
    ip_pay[0..2].copy_from_slice(&sport.to_be_bytes());
    ip_pay[2..4].copy_from_slice(&dport.to_be_bytes());
    ip_pay[4..6].copy_from_slice(&(udp_tot as u16).to_be_bytes());
    ip_pay[6..8].copy_from_slice(&0u16.to_be_bytes());
    ip_pay[8..udp_tot].copy_from_slice(payload);

    let mut frame = [0u8; 14 + 20 + MAX_RPC_BYTES + 64];
    let mut off = 0usize;
    while off < udp_tot {
        let rem = udp_tot - off;
        let chunk_len = if rem > max_ip_pay { max_ip_pay } else { rem };
        let more_frags = off + chunk_len < udp_tot;
        let frag_off_units = (off / 8) as u16;
        let flags_frag = frag_off_units | if more_frags { 0x2000 } else { 0 };

        let raw_len = 34 + chunk_len;
        let frame_len = raw_len.max(60);
        frame[0..6].copy_from_slice(&dst_mac);
        frame[6..12].copy_from_slice(&src_mac);
        frame[12..14].copy_from_slice(&0x0800u16.to_be_bytes());

        let ip_tot = (20 + chunk_len) as u16;
        frame[14] = 0x45;
        frame[15] = 0x00;
        frame[16..18].copy_from_slice(&ip_tot.to_be_bytes());
        frame[18..20].copy_from_slice(&ip_id.to_be_bytes());
        frame[20..22].copy_from_slice(&flags_frag.to_be_bytes());
        frame[22] = 64;
        frame[23] = 17;
        frame[24..26].copy_from_slice(&0u16.to_be_bytes());
        frame[26..30].copy_from_slice(&src_ip);
        frame[30..34].copy_from_slice(&dst_ip);
        let csum = ipv4_checksum(&frame[14..34]);
        frame[24..26].copy_from_slice(&csum.to_be_bytes());

        frame[34..raw_len].copy_from_slice(&ip_pay[off..off + chunk_len]);
        if frame_len > raw_len {
            frame[raw_len..frame_len].fill(0);
        }
        emit(&frame[..frame_len]);
        off += chunk_len;
    }
}

/// Write a 60 B Ethernet ARP reply frame.
pub fn write_arp_reply_frame(
    my_mac: [u8; 6],
    my_ip: [u8; 4],
    dst_mac: [u8; 6],
    dst_ip: [u8; 4],
    out: &mut [u8; 60],
) {
    out.fill(0);
    out[0..6].copy_from_slice(&dst_mac);
    out[6..12].copy_from_slice(&my_mac);
    out[12..14].copy_from_slice(&0x0806u16.to_be_bytes());
    out[14..16].copy_from_slice(&1u16.to_be_bytes());
    out[16..18].copy_from_slice(&0x0800u16.to_be_bytes());
    out[18] = 6;
    out[19] = 4;
    out[20..22].copy_from_slice(&2u16.to_be_bytes());
    out[22..28].copy_from_slice(&my_mac);
    out[28..32].copy_from_slice(&my_ip);
    out[32..38].copy_from_slice(&dst_mac);
    out[38..42].copy_from_slice(&dst_ip);
}

#[derive(Debug, PartialEq, Eq)]
pub enum SdpRxKind<'a> {
    ArpReq {
        src_mac: [u8; 6],
        src_ip: [u8; 4],
    },
    Udp {
        src_mac: [u8; 6],
        src_ip: [u8; 4],
        sport: u16,
        dport: u16,
        payload: &'a [u8],
    },
}

/// Parse an Ethernet frame received on `sdp1-1` from Linux `octeon_ep_vf`.
/// Correctly honors IPv4 IHL, IPv4 `total_len`, and UDP `ulen` (stripping Ethernet padding).
#[inline(always)]
pub fn parse_sdp_frame<'a>(frame: &'a [u8], card_ip: [u8; 4]) -> Option<SdpRxKind<'a>> {
    if frame.len() < 42 {
        return None;
    }
    if frame[12] == 0x08 && frame[13] == 0x06 {
        if frame[20] == 0x00 && frame[21] == 0x01 && frame[38..42] == card_ip {
            let mut src_mac = [0u8; 6];
            src_mac.copy_from_slice(&frame[22..28]);
            let mut src_ip = [0u8; 4];
            src_ip.copy_from_slice(&frame[28..32]);
            return Some(SdpRxKind::ArpReq { src_mac, src_ip });
        }
        return None;
    }
    if frame[12] != 0x08 || frame[13] != 0x00 {
        return None;
    }
    let ver_ihl = frame[14];
    if (ver_ihl >> 4) != 4 {
        return None;
    }
    let ihl = ((ver_ihl & 0x0f) as usize) * 4;
    if ihl < 20 || 14 + ihl + 8 > frame.len() {
        return None;
    }
    if frame[23] != 17 {
        return None;
    }
    // Reject IPv4 fragments from host (upstream Memcached UDP replies are <= 1400 B).
    let frag_field = u16::from_be_bytes([frame[20], frame[21]]);
    if (frag_field & 0x3fff) != 0 {
        return None;
    }
    let ip_tot = u16::from_be_bytes([frame[16], frame[17]]) as usize;
    if ip_tot < ihl + 8 || 14 + ip_tot > frame.len() {
        return None;
    }
    let udp_off = 14 + ihl;
    let sport = u16::from_be_bytes([frame[udp_off], frame[udp_off + 1]]);
    let dport = u16::from_be_bytes([frame[udp_off + 2], frame[udp_off + 3]]);
    let udp_len = u16::from_be_bytes([frame[udp_off + 4], frame[udp_off + 5]]) as usize;
    if udp_len < 8 || udp_off + udp_len > 14 + ip_tot {
        return None;
    }
    let mut src_mac = [0u8; 6];
    src_mac.copy_from_slice(&frame[6..12]);
    let mut src_ip = [0u8; 4];
    src_ip.copy_from_slice(&frame[26..30]);
    Some(SdpRxKind::Udp {
        src_mac,
        src_ip,
        sport,
        dport,
        payload: &frame[udp_off + 8..udp_off + udp_len],
    })
}

// -----------------------------------------------------------------------------
// Multi-Datagram Standard Memcached Binary UDP Reply Reassembler (1..4 datagrams)
// -----------------------------------------------------------------------------

const MC_REASM_PER_TID: usize = 512;
const MC_REASM_SLOTS: usize = MAX_PEERS * MC_REASM_PER_TID;

#[derive(Clone, Copy, Default)]
struct McReasmSlotMeta {
    active: bool,
    req_id: u16,
    dg_tot: u16,
    mask: u8,
    total_len: usize,
    rpc_seq: u64,
}

#[derive(Debug, PartialEq, Eq)]
pub enum McReasmOutcome {
    /// Single-datagram reply (`dg_tot == 1`): caller forwards original `dgram` directly.
    Single { rpc_seq: u64 },
    /// Multi-datagram reply completed: caller reads `reasm.slot_bytes(slot, len)`.
    Complete { rpc_seq: u64, slot: usize, len: usize },
    /// Valid fragment stored; waiting for remaining datagrams.
    Incomplete,
    /// Malformed header or unexpected length.
    Error,
}

/// Reassembles multi-datagram Memcached Binary UDP replies emitted by upstream
/// `memcached.c:transmit_udp()` (`UDP_DATA_SIZE = 1392`, `UDP_MAX_PAYLOAD_SIZE = 1400`)
/// into the exact contiguous `[dg0 | dg1 | ...]` representation expected by `mrv206_client`.
pub struct McUdpReassembler {
    meta: Vec<McReasmSlotMeta>,
    buf: Vec<u8>,
}

impl Default for McUdpReassembler {
    fn default() -> Self {
        Self::new()
    }
}

impl McUdpReassembler {
    pub fn new() -> Self {
        McUdpReassembler {
            meta: vec![McReasmSlotMeta::default(); MC_REASM_SLOTS],
            buf: vec![0u8; MC_REASM_SLOTS * MAX_RPC_BYTES],
        }
    }

    pub fn reset(&mut self) {
        for m in &mut self.meta {
            *m = McReasmSlotMeta::default();
        }
    }

    #[inline(always)]
    pub fn slot_bytes(&self, slot: usize, len: usize) -> &[u8] {
        let base = slot * MAX_RPC_BYTES;
        &self.buf[base..base + len]
    }

    #[inline(always)]
    pub fn ingest(&mut self, tid: usize, dgram: &[u8]) -> McReasmOutcome {
        if tid >= MAX_PEERS || dgram.len() < MC_UDP_HDR_LEN || dgram.len() > MC_UDP_MAX_PAYLOAD {
            return McReasmOutcome::Error;
        }
        let req_id = u16::from_be_bytes([dgram[0], dgram[1]]);
        let dg_seq = u16::from_be_bytes([dgram[2], dgram[3]]);
        let dg_tot = u16::from_be_bytes([dgram[4], dgram[5]]);
        let dg_resv = u16::from_be_bytes([dgram[6], dgram[7]]);
        if dg_resv != 0 || dg_tot == 0 || dg_tot > 4 || dg_seq >= dg_tot {
            return McReasmOutcome::Error;
        }
        if dg_tot == 1 {
            if dgram.len() < MC_UDP_HDR_LEN + MC_BIN_HDR_LEN || dgram[8] != MC_MAGIC_RES {
                return McReasmOutcome::Error;
            }
            let rpc_seq = rpc_seq_from_opaque(mc_udp_opaque(dgram));
            if rpc_seq == 0 {
                return McReasmOutcome::Error;
            }
            return McReasmOutcome::Single { rpc_seq };
        }

        // Multi-datagram response (`dg_tot` in 2..=4, e.g. 3 datagrams for `get3840`).
        let is_last = dg_seq == dg_tot - 1;
        if !is_last && dgram.len() != MC_UDP_MAX_PAYLOAD {
            return McReasmOutcome::Error;
        }
        let dst_off = (dg_seq as usize) * MC_UDP_MAX_PAYLOAD;
        if dst_off + dgram.len() > MAX_MC_BYTES {
            return McReasmOutcome::Error;
        }
        let slot = tid * MC_REASM_PER_TID + ((req_id as usize) & (MC_REASM_PER_TID - 1));
        let m = &mut self.meta[slot];
        if !m.active || m.req_id != req_id || m.dg_tot != dg_tot {
            m.active = true;
            m.req_id = req_id;
            m.dg_tot = dg_tot;
            m.mask = 0;
            m.total_len = 0;
            m.rpc_seq = 0;
        }
        if dg_seq == 0 {
            if dgram.len() < MC_UDP_HDR_LEN + MC_BIN_HDR_LEN || dgram[8] != MC_MAGIC_RES {
                m.active = false;
                return McReasmOutcome::Error;
            }
            let rpc_seq = rpc_seq_from_opaque(mc_udp_opaque(dgram));
            if rpc_seq == 0 {
                m.active = false;
                return McReasmOutcome::Error;
            }
            m.rpc_seq = rpc_seq;
        }
        if is_last {
            m.total_len = dst_off + dgram.len();
        }
        let base = slot * MAX_RPC_BYTES + dst_off;
        self.buf[base..base + dgram.len()].copy_from_slice(dgram);
        m.mask |= 1u8 << (dg_seq as u8);

        let full_mask = (1u8 << (dg_tot as u8)) - 1;
        if m.mask == full_mask {
            let rpc_seq = m.rpc_seq;
            let len = m.total_len;
            m.active = false;
            m.mask = 0;
            if rpc_seq == 0 || len < MC_UDP_HDR_LEN + MC_BIN_HDR_LEN {
                return McReasmOutcome::Error;
            }
            McReasmOutcome::Complete { rpc_seq, slot, len }
        } else {
            McReasmOutcome::Incomplete
        }
    }
}

// -----------------------------------------------------------------------------
// Hardware-Accelerated RoCEv2 ICRC & Precomputed Templates (identical to MRV-206)
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
        let csum = ipv4_checksum(&hdr[14..34]);
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_opaque_rpc_seq_bijection() {
        for tid in 0u64..8 {
            for lseq in [1u64, 42, 0x1234_5678, 0x1fff_ffff] {
                let rpc_seq = (tid << 34) | lseq;
                let opaque = opaque_from_rpc_seq(rpc_seq);
                assert_eq!(rpc_seq_from_opaque(opaque), rpc_seq);
            }
        }
    }

    #[test]
    fn test_mc_values_and_corruption_detection() {
        for &len in &[8usize, 128, 1024, 3840] {
            let mut buf = vec![0u8; len];
            fill_mc_value(1337, 5, &mut buf);
            assert!(verify_mc_value(1337, &buf));
            assert!(!verify_mc_value(1338, &buf));
            buf[len - 1] ^= 0x01;
            assert!(!verify_mc_value(1337, &buf));
        }
    }

    #[test]
    fn test_ipv4_udp_roundtrip_and_padding_strip() {
        let dst_mac = [0x00, 0x00, 0x00, 0x01, 0x01, 0x01];
        let src_mac = [0x02, 0x20, 0x60, 0x00, 0x00, 0x02];
        let mut payload = [0u8; 48];
        let plen = build_mc_get_udp(7, opaque_from_rpc_seq((2 << 34) | 99), 42, &mut payload);
        let mut frame = [0u8; 128];
        let flen = write_ipv4_udp_frame(
            dst_mac,
            src_mac,
            SDP_CARD_IP,
            SDP_HOST_IP,
            SDP_SPORT_BASE + 2,
            MC_UDP_PORT,
            0x2150,
            &payload[..plen],
            &mut frame,
        );
        assert_eq!(flen, 42 + 48);
        assert_eq!(ipv4_checksum(&frame[14..34]), 0);

        // Append trailing Ethernet pad bytes and ensure parser strips them via IP/UDP lengths.
        let mut padded = vec![0u8; flen + 12];
        padded[..flen].copy_from_slice(&frame[..flen]);
        match parse_sdp_frame(&padded, SDP_CARD_IP) {
            Some(SdpRxKind::Udp { src_mac: sm, src_ip: si, sport, dport, payload: pay }) => {
                assert_eq!(sm, src_mac);
                assert_eq!(si, SDP_CARD_IP);
                assert_eq!(sport, SDP_SPORT_BASE + 2);
                assert_eq!(dport, MC_UDP_PORT);
                assert_eq!(pay, &payload[..plen]);
            }
            other => panic!("unexpected parse result: {other:?}"),
        }
    }

    #[test]
    fn test_ipv4_fragmentation_for_set3840_mtu1500() {
        let mut req = vec![0u8; MAX_RPC_BYTES];
        let n = build_mc_set_udp(11, opaque_from_rpc_seq((1 << 34) | 7), 77, 1, 3840, &mut req);
        assert_eq!(n, 8 + 24 + 8 + 16 + 3840);
        let mut frags: Vec<Vec<u8>> = Vec::new();
        emit_ipv4_udp_fragments(
            [1, 2, 3, 4, 5, 6],
            [6, 5, 4, 3, 2, 1],
            SDP_CARD_IP,
            SDP_HOST_IP,
            SDP_SPORT_BASE + 1,
            MC_UDP_PORT,
            0x4321,
            1500,
            &req[..n],
            |f| frags.push(f.to_vec()),
        );
        assert_eq!(frags.len(), 3);
        let mut reasm = Vec::new();
        for (idx, f) in frags.iter().enumerate() {
            assert!(f.len() <= 1514);
            assert_eq!(ipv4_checksum(&f[14..34]), 0);
            let ff = u16::from_be_bytes([f[20], f[21]]);
            let mf = (ff & 0x2000) != 0;
            let off_bytes = ((ff & 0x1fff) as usize) * 8;
            assert_eq!(mf, idx + 1 < frags.len());
            assert_eq!(off_bytes, reasm.len());
            let ip_tot = u16::from_be_bytes([f[16], f[17]]) as usize;
            reasm.extend_from_slice(&f[34..14 + ip_tot]);
        }
        assert_eq!(reasm.len(), 8 + n);
        assert_eq!(u16::from_be_bytes([reasm[0], reasm[1]]), SDP_SPORT_BASE + 1);
        assert_eq!(u16::from_be_bytes([reasm[2], reasm[3]]), MC_UDP_PORT);
        assert_eq!(u16::from_be_bytes([reasm[4], reasm[5]]) as usize, 8 + n);
        assert_eq!(&reasm[8..], &req[..n]);
    }

    #[test]
    fn test_mc_udp_reassembler_single_and_multidgram_ooo() {
        let mut reasm = McUdpReassembler::new();
        let rpc_seq = (3u64 << 34) | 123;
        let opaque = opaque_from_rpc_seq(rpc_seq);
        let req_id = 123u16;

        // 1. Single datagram reply (get8 = 8 + 24 + 4 + 8 = 44 B)
        let mut dg_single = [0u8; 44];
        dg_single[0..2].copy_from_slice(&req_id.to_be_bytes());
        dg_single[4..6].copy_from_slice(&1u16.to_be_bytes());
        dg_single[8] = MC_MAGIC_RES;
        dg_single[20..24].copy_from_slice(&opaque.to_be_bytes());
        assert_eq!(reasm.ingest(3, &dg_single), McReasmOutcome::Single { rpc_seq });

        // 2. Three-datagram reply (get3840: bin_len = 24 + 4 + 3840 = 3868 -> 1400 + 1400 + 1092 = 3892 B)
        let mut bin = vec![0u8; MC_BIN_HDR_LEN + 4 + 3840];
        bin[0] = MC_MAGIC_RES;
        bin[1] = MC_CMD_GET;
        bin[4] = 4;
        bin[8..12].copy_from_slice(&(4u32 + 3840).to_be_bytes());
        bin[12..16].copy_from_slice(&opaque.to_be_bytes());
        bin[16..24].copy_from_slice(&1u64.to_be_bytes());
        bin[24..28].copy_from_slice(&MC_ITEM_FLAGS.to_be_bytes());
        fill_mc_value(7000, 1, &mut bin[28..]);

        let mut dgs: Vec<Vec<u8>> = Vec::new();
        let tot = mc_udp_dgram_cnt(bin.len()) as u16;
        assert_eq!(tot, 3);
        for d in 0..tot {
            let start = (d as usize) * MC_UDP_DATA_SIZE;
            let end = (start + MC_UDP_DATA_SIZE).min(bin.len());
            let mut dg = Vec::with_capacity(8 + (end - start));
            dg.extend_from_slice(&req_id.to_be_bytes());
            dg.extend_from_slice(&d.to_be_bytes());
            dg.extend_from_slice(&tot.to_be_bytes());
            dg.extend_from_slice(&0u16.to_be_bytes());
            dg.extend_from_slice(&bin[start..end]);
            dgs.push(dg);
        }

        // Feed out-of-order: seq=2, then seq=0, then seq=1
        assert_eq!(reasm.ingest(3, &dgs[2]), McReasmOutcome::Incomplete);
        assert_eq!(reasm.ingest(3, &dgs[0]), McReasmOutcome::Incomplete);
        match reasm.ingest(3, &dgs[1]) {
            McReasmOutcome::Complete { rpc_seq: got_seq, slot, len } => {
                assert_eq!(got_seq, rpc_seq);
                assert_eq!(len, 3892);
                let combined = reasm.slot_bytes(slot, len);
                assert_eq!(&combined[0..1400], dgs[0].as_slice());
                assert_eq!(&combined[1400..2800], dgs[1].as_slice());
                assert_eq!(&combined[2800..3892], dgs[2].as_slice());
            }
            other => panic!("expected Complete, got {other:?}"),
        }
    }

    #[test]
    fn test_parse_mac_and_tcp_set() {
        let mac = parse_mac("00:00:00:01:01:01").unwrap();
        assert_eq!(mac, [0, 0, 0, 1, 1, 1]);
        assert_eq!(format_mac(mac), "00:00:00:01:01:01");
        assert!(parse_mac("invalid").is_err());

        let mut buf = vec![0u8; 256];
        let n = build_mc_set_tcp(99, 123, 1, 128, &mut buf);
        assert_eq!(n, 24 + 8 + 16 + 128);
        assert_eq!(buf[0], MC_MAGIC_REQ);
        assert_eq!(buf[1], MC_CMD_SET);
        assert_eq!(&buf[32..48], b"k000000000000123");
        assert!(verify_mc_value(123, &buf[48..n]));
    }
}
