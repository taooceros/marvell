//! Shared constants, wire format, and per-core BAR4 credit-line layout for
//! MRV-212 (E1: NO-SFENCE BAR4 MMIO credit signaling CPU cost, control-loop RTT,
//! 100GbE BDP lower bound, Socket-1 interference boundary, data safety, and
//! native SDP SLIST_DBELL/WMARK backpressure ablation).

use testbed::{ETHERTYPE, Mac};

/// Default card TCP sync/control port for MRV-212 (separate from MRV-26 lease on 7526).
pub const SYNC_ADDR: &str = "10.10.0.2:7527";

/// Up to 16 host cores each own an exclusive 64 B BAR4 cache line at offset
/// `CREDIT_BASE + core_idx * 64` (zero lock, zero false sharing, zero overwrite races).
pub const CREDIT_BASE: usize = 0;
pub const MAX_CREDIT_CORES: usize = 16;

/// Control B (MRV-196 neighbor-interference & Opus audit) sink region inside BAR4 slot 15.
/// Starts at 4 KiB (`0x1000`), spans 3 MiB (`49,152 x 64 B` lines), strictly below
/// `READY_OFFSET` (`4,194,112`).
pub const SINK_BASE: usize = 4096;
pub const MAX_SINK_BYTES: usize = 3 << 20;
pub const SINK_LINES_PER_THREAD: usize = 4096; // 256 KiB per thread (8 threads = 2 MiB <= 3 MiB)

pub const CELL_MAGIC: u64 = 0xA55A_2120_0000_0000;
pub const FRAME_MAGIC: u64 = 0x2120_CAFE_BABE_0000;
pub const MID_XOR: u64 = 0x5555_AAAA_5555_AAAA;
pub const POISON_MAGIC: u64 = 0xDEAD_BEEF_2120_BAD0;

/// Byte offset of host core `core_idx`'s dedicated 64 B BAR4 credit line.
#[inline(always)]
pub const fn credit_line_offset(core_idx: usize) -> usize {
    assert!(core_idx < MAX_CREDIT_CORES);
    CREDIT_BASE + core_idx * 64
}

/// 64-byte atomic credit line written via single `MOVDIR64B` (or 8 B `UC write64` to `w[0]`).
/// Layout:
/// - `w[0]`: monotonic `taken` packet count
/// - `w[1]`: `run_gen` (monotonic run id)
/// - `w[2]`: `bytes_taken` (byte-granular credit counter)
/// - `w[3]`: `host_tsc` (diagnostic timestamp)
/// - `w[4]`: `flags` (bit 0 = 64B atomic MOVDIR64B; 0 = 8B UC write64)
/// - `w[5]`: `sig` (`taken * PHI ^ run_gen ^ CELL_MAGIC`)
/// - `w[6]`: reserved (0)
/// - `w[7]`: trailing copy of `taken` (torn-line audit for 64 B atomic MOVDIR64B)
#[repr(C, align(64))]
#[derive(Clone, Copy, Default)]
pub struct CreditLine {
    pub w: [u64; 8],
}

const _: () = assert!(size_of::<CreditLine>() == 64 && align_of::<CreditLine>() == 64);

#[inline(always)]
pub const fn credit_sig(taken: u64, run_gen: u64) -> u64 {
    taken.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ run_gen ^ CELL_MAGIC
}

impl CreditLine {
    #[inline(always)]
    pub const fn new(taken: u64, run_gen: u64, bytes_taken: u64, host_tsc: u64) -> Self {
        Self {
            w: [
                taken,
                run_gen,
                bytes_taken,
                host_tsc,
                1,
                credit_sig(taken, run_gen),
                0,
                taken,
            ],
        }
    }
}

#[inline(always)]
pub const fn sink_cell_sig(seq: u64, global_line: usize, run_tag: u64) -> u64 {
    seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ (global_line as u64) ^ run_tag ^ CELL_MAGIC
}

/// Frame layout (all u64 fields 8-byte aligned; `len >= 60`, `len % 8 == 4` or `len >= 64`):
/// - `0..6`: dst MAC
/// - `6..12`: src MAC
/// - `12..14`: EtherType (`0x88B5`)
/// - `14..16`: `queue_id: u16` (little-endian)
/// - `16..24`: `seq: u64` (1-based monotonic per queue per run)
/// - `24..32`: `run_gen: u64`
/// - `32..40`: `tsc: u64` (echoed host tsc in ping-pong, or card CNTVCT in stream)
/// - `40..48`: `sig: u64`
/// - `(len/2 & !7)..+8` (when `len >= 128`): `mid_sig = sig ^ MID_XOR`
/// - `(len - 8)..len`: `seq_tail = seq` (torn-DMA detector)
#[inline(always)]
pub const fn frame_sig(seq: u64, run_gen: u64, queue_id: u16) -> u64 {
    seq.wrapping_mul(0x9e37_79b9_7f4a_7c15) ^ run_gen ^ ((queue_id as u64) << 48) ^ FRAME_MAGIC
}

#[inline(always)]
pub fn write_frame(
    buf: &mut [u8],
    dst: Mac,
    src: Mac,
    queue_id: u16,
    seq: u64,
    run_gen: u64,
    tsc: u64,
) {
    let len = buf.len();
    debug_assert!(len >= 64);
    buf[0..6].copy_from_slice(&dst.0);
    buf[6..12].copy_from_slice(&src.0);
    buf[12..14].copy_from_slice(&ETHERTYPE.to_be_bytes());
    buf[14..16].copy_from_slice(&queue_id.to_le_bytes());
    let sig = frame_sig(seq, run_gen, queue_id);
    buf[16..24].copy_from_slice(&seq.to_le_bytes());
    buf[24..32].copy_from_slice(&run_gen.to_le_bytes());
    buf[32..40].copy_from_slice(&tsc.to_le_bytes());
    buf[40..48].copy_from_slice(&sig.to_le_bytes());
    if len >= 128 {
        let mid = (len / 2) & !7;
        buf[mid..mid + 8].copy_from_slice(&(sig ^ MID_XOR).to_le_bytes());
    }
    buf[len - 8..len].copy_from_slice(&seq.to_le_bytes());
}

/// Fast header-only stamp for pre-initialized Ethernet frames (dst/src/ethertype/queue already set).
#[inline(always)]
pub fn stamp_frame_fast(buf: &mut [u8], queue_id: u16, seq: u64, run_gen: u64, tsc: u64) {
    let len = buf.len();
    let sig = frame_sig(seq, run_gen, queue_id);
    // SAFETY: `len >= 64` and slices are within `buf`.
    unsafe {
        let p = buf.as_mut_ptr();
        p.add(16).cast::<u64>().write_unaligned(seq);
        p.add(24).cast::<u64>().write_unaligned(run_gen);
        p.add(32).cast::<u64>().write_unaligned(tsc);
        p.add(40).cast::<u64>().write_unaligned(sig);
        if len >= 128 {
            let mid = (len / 2) & !7;
            p.add(mid).cast::<u64>().write_unaligned(sig ^ MID_XOR);
        }
        p.add(len - 8).cast::<u64>().write_unaligned(seq);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FrameCheck {
    Ok { queue_id: u16, seq: u64, run_gen: u64, tsc: u64 },
    Bad,
    Torn,
}

#[inline(always)]
pub fn verify_frame(buf: &[u8], expected_len: usize, expected_run_gen: u64) -> FrameCheck {
    if buf.len() != expected_len || buf.len() < 64 {
        return FrameCheck::Bad;
    }
    let etype = u16::from_be_bytes([buf[12], buf[13]]);
    if etype != ETHERTYPE {
        return FrameCheck::Bad;
    }
    let queue_id = u16::from_le_bytes([buf[14], buf[15]]);
    // SAFETY: `buf.len() >= 64`.
    let (seq, run_gen, tsc, sig, tail) = unsafe {
        let p = buf.as_ptr();
        (
            p.add(16).cast::<u64>().read_unaligned(),
            p.add(24).cast::<u64>().read_unaligned(),
            p.add(32).cast::<u64>().read_unaligned(),
            p.add(40).cast::<u64>().read_unaligned(),
            p.add(expected_len - 8).cast::<u64>().read_unaligned(),
        )
    };
    if run_gen != expected_run_gen {
        return FrameCheck::Bad;
    }
    if tail != seq {
        return FrameCheck::Torn;
    }
    let want_sig = frame_sig(seq, run_gen, queue_id);
    if sig != want_sig {
        return FrameCheck::Bad;
    }
    if expected_len >= 128 {
        let mid = (expected_len / 2) & !7;
        let mid_val = unsafe { buf.as_ptr().add(mid).cast::<u64>().read_unaligned() };
        if mid_val != (want_sig ^ MID_XOR) {
            return FrameCheck::Torn;
        }
    }
    FrameCheck::Ok { queue_id, seq, run_gen, tsc }
}

/// Overwrite header/mid/tail words with a poison sentinel before returning an mbuf
/// to the pool (used in Control C dynamic-rebind verification so stale/unwritten
/// DMA buffers are caught immediately).
#[inline(always)]
pub fn poison_frame(buf: &mut [u8], seq_salt: u64) {
    let len = buf.len();
    if len < 64 {
        return;
    }
    let p = POISON_MAGIC ^ seq_salt;
    unsafe {
        let ptr = buf.as_mut_ptr();
        ptr.add(16).cast::<u64>().write_unaligned(p);
        ptr.add(24).cast::<u64>().write_unaligned(p);
        ptr.add(40).cast::<u64>().write_unaligned(p);
        if len >= 128 {
            let mid = (len / 2) & !7;
            ptr.add(mid).cast::<u64>().write_unaligned(p);
        }
        ptr.add(len - 8).cast::<u64>().write_unaligned(p);
    }
}
