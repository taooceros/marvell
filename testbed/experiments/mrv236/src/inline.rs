//! Host -> card messages pushed as 64 B MMIO writes into card DRAM through one
//! 4 MiB slot of the PF's BAR4 window, ordered at the destination instead of
//! the source: the host issues MOVDIR64B without fences, and the card takes
//! cell `k` only when both copies of its sequence number read `k`, so a cell
//! that has not (fully) arrived is never consumed.
//!
//! Replies come back as frames over the SDP VF, as cells the card's DPI
//! engine writes into a ring in host memory (DMA reply mode; the host reads
//! them with the same destination-side check), or (pull mode, W=1) as a word
//! pair the card stores in its own memory and the host reads through BAR4.
//!
//! Instead of pushing requests through BAR4, the host can also write them
//! into a ring in its own memory that a card `fetch` core reads with DPI
//! (PCIe reads): see [`FETCH_RING_OFFSET`].
//!
//! The card may poll with several cores: core `i` of `n` takes the cells with
//! `(seq - 1) % n == i`, each in its own sequence order.
//!
//! Ring layout in the slot: cells `0..RING_CELLS`, then a three-cell control
//! area: word 0 holds [`READY`] while the card owns the slot, word 1 the
//! number of card cores taking the ring, word 2 the host's reply choice (set
//! before its first cell), words 4-5 the last pull reply, word 7 the nonce of
//! the host run holding the lease ([`testbed::lease`]: the host maps the slot
//! only once the card granted it, and checks READY and its nonce here before
//! writing), words 8.. the next sequence number each ring core expects, and
//! words 16.. the card -> host stream and fetch words. A new host run
//! continues from the smallest next sequence number instead of restarting at
//! 1 (after a run that ended with unanswered cells, those cells' later
//! neighbours are skipped once and their replies lost).

use std::sync::atomic::{AtomicU64, Ordering};

use dpdk::{Eal, Memzone, rdtsc_after_loads};

use crate::closed_loop::{Integrity, Replies, Reply, TAIL_WAIT_US};

/// Host BAR4 slot the card repoints to its own buffer. Firmware maps slots 0-7
/// to SDP PF0 and 8-15 to SDP PF1 BAR4; the host uses only slot 7 (control
/// mailbox) and has no PF1.
pub const SLOT: usize = 15;
pub const SLOT_BYTES: usize = 4 << 20;
pub const RING_CELLS: usize = SLOT_BYTES / 64 - 3;
/// Offset of the ready cookie inside the slot: the start of the control area.
pub const READY_OFFSET: usize = RING_CELLS * 64;
/// Offset of the number of card cores taking the ring.
pub const CORES_OFFSET: usize = READY_OFFSET + 8;
/// Most card cores the control area has next-sequence words for.
pub const MAX_CORES: usize = 8;
/// Offset of the host's reply choice: the IOVA of its reply ring (DMA replies),
/// [`REPLY_PULL`], or 0 (replies over SDP).
pub const REPLY_RING_OFFSET: usize = READY_OFFSET + 16;
/// Word 2 value for pull mode: the card stores `(tag, tsc)` of the last cell
/// it took at [`DONE_OFFSET`] and the host reads it through BAR4 (W=1 only).
/// Never a ring IOVA, which is 64 B aligned.
pub const REPLY_PULL: u64 = 1;
/// Word 2 value for an open-loop host -> card stream: the card takes and
/// checks cells but sends nothing back.
pub const REPLY_NONE: u64 = 2;
/// Offset of the length `n` of an open-loop card -> host stream (0 = none):
/// unprompted, the card's stream cores write cells `k = 1..=n` as
/// `Cell::new(k, k, 1)` into the host ring at [`STREAM_RING_OFFSET`], at
/// [`Cell::reply_offset`]`(k, stream cores)`.
pub const STREAM_OFFSET: usize = READY_OFFSET + 24;
/// Words 4-5 of the control cell (16 B aligned, for one 16 B host read).
pub const DONE_OFFSET: usize = READY_OFFSET + 32;
/// Offset of how many stream cells the host has taken, in order: the card
/// writes cell `k` only once `k <= taken + REPLY_CELLS`. `u64::MAX` lifts the
/// limit (throughput measurement: the host reads nothing while it runs).
pub const TAKEN_OFFSET: usize = READY_OFFSET + 48;
/// Offset of the nonce of the host run holding the lease (0 = none): the
/// card stores it before it answers that run's HELLO.
pub const NONCE_OFFSET: usize = READY_OFFSET + 56;

/// Offset of the next sequence number card core `core` expects.
pub const fn next_offset(core: usize) -> usize {
    assert!(core < MAX_CORES);
    READY_OFFSET + 64 + 8 * core
}

/// Offset of the IOVA of the host ring card -> host streams go to; set before
/// the stream length.
pub const STREAM_RING_OFFSET: usize = READY_OFFSET + 128;
/// Offset of the number of card cores writing card -> host streams.
pub const STREAM_CORES_OFFSET: usize = READY_OFFSET + 136;
/// Offset of the IOVA of a host request ring of [`FETCH_CELLS`] cells (0 =
/// none): the host writes request `k` (`seq` = `k`, counted from 1 for each
/// new ring) into cell `(k - 1) % FETCH_CELLS` with [`Cell::publish`], and a
/// card `fetch` core reads the ring with DPI, takes the cells in order and
/// publishes the next one it expects at [`FETCH_NEXT_OFFSET`] (the host's
/// credit: cell `k` may be written once `k - FETCH_CELLS` was taken).
pub const FETCH_RING_OFFSET: usize = READY_OFFSET + 144;
pub const FETCH_NEXT_OFFSET: usize = READY_OFFSET + 152;
pub const FETCH_CELLS: usize = 16384;
/// Offset of how the fetch core decides what to read, set before the fetch
/// ring: 0 = speculatively (it keeps reads in flight ahead of the next
/// request, published or not), 1 = triggered by doorbells: the host also
/// writes [`Cell::doorbell`]s into the BAR4 ring (which the fetch core then
/// takes, so the card must run no ring cores), and the fetch core reads only
/// the requests the latest doorbell says are published.
pub const FETCH_TRIGGER_OFFSET: usize = READY_OFFSET + 160;
/// MRV-50/MRV-75 experiment: card counter written by ring core 0 on empty
/// polls when `--exp-stamp` is enabled, read by host via BAR4 to align clocks.
pub const CLOCK_OFFSET: usize = READY_OFFSET + 168;

/// "INLINERD": written by the card after it claims the slot, cleared before it releases it.
pub const READY: u64 = u64::from_le_bytes(*b"INLINERD");
/// Cells in the host reply ring, split evenly into one sub-ring per card core
/// (see [`Cell::reply_offset`]). Divisible by every core count up to
/// [`MAX_CORES`], and larger than any window.
pub const REPLY_CELLS: usize = 4096;
const _: () = assert!(REPLY_CELLS.is_multiple_of(MAX_CORES) && REPLY_CELLS / MAX_CORES >= 256);

/// One message. `seq` is the ring sequence (starts at 1; zeroed memory is
/// "empty"); `tag` and `tsc` are echoed back in the reply.
#[repr(C, align(64))]
#[derive(Clone, Copy)]
pub struct Cell {
    pub seq: u64,
    pub tsc: u64,
    pub tag: u64,
    pub payload: [u8; 32],
    pub seq_tail: u64,
}

const _: () = assert!(size_of::<Cell>() == 64);

impl Cell {
    pub fn new(seq: u64, tag: u64, tsc: u64) -> Self {
        Cell { seq, tsc, tag, payload: Self::pattern(seq), seq_tail: seq }
    }

    /// A doorbell for the fetch core, ring sequence `seq` in the BAR4 ring:
    /// it carries message `message` inline (0 = none) and says requests
    /// `1..=published` are in the host fetch ring.
    pub fn doorbell(seq: u64, message: u64, published: u64) -> Self {
        Cell::new(seq, message, published)
    }

    /// Payload derived from `seq`, so the receiver can check every byte.
    pub fn pattern(seq: u64) -> [u8; 32] {
        let mut p = [0; 32];
        for (word, v) in p.chunks_exact_mut(8).zip(Self::pattern_words(seq)) {
            word.copy_from_slice(&v.to_ne_bytes());
        }
        p
    }

    /// [`Cell::pattern`] as the words it lies in memory as.
    fn pattern_words(seq: u64) -> [u64; 4] {
        std::array::from_fn(|i| (seq ^ (i as u64).wrapping_mul(0xa5a5_a5a5_a5a5_a5a5)).wrapping_mul(0x9e37_79b9_7f4a_7c15))
    }

    /// Whether the payload is [`Cell::pattern`]`(seq)`, compared a word at a
    /// time (a byte-array compare of a cell just copied word by word stalls
    /// on store forwarding).
    pub fn payload_matches(&self, seq: u64) -> bool {
        let (w, p) = (self.words(), Self::pattern_words(seq));
        w[3] == p[0] && w[4] == p[1] && w[5] == p[2] && w[6] == p[3]
    }

    /// MRV-50/MRV-75/MRV-236 experiment: payload check for a stamped reply
    /// where `w[3..6]` carry packed card timestamps, empty-poll count, and
    /// taking core id while `w[6]` stays `pattern_words(seq)[3]`.
    pub fn payload_tail_matches(&self, seq: u64) -> bool {
        let (w, p) = (self.words(), Self::pattern_words(seq));
        self.payload_matches(seq) || (w[3] != 0 && (w[4] >> 32) < 100_000_000 && w[6] == p[3])
    }

    /// The cell as it lies in memory, as eight native-endian words.
    pub fn words(&self) -> [u64; 8] {
        // SAFETY: `Cell` is `repr(C)`, 64 bytes without padding, and any 64
        // bytes are a valid `[u64; 8]`.
        unsafe { std::mem::transmute::<Cell, [u64; 8]>(*self) }
    }

    fn from_words(words: [u64; 8]) -> Cell {
        // SAFETY: as in `words`, the other way round.
        unsafe { std::mem::transmute::<[u64; 8], Cell>(words) }
    }

    /// Byte offset of message `seq` in the slot.
    pub fn offset(seq: u64) -> usize {
        ((seq - 1) as usize % RING_CELLS) * 64
    }

    /// Byte offset of the reply to loop message `k` (the request's `tag`) in
    /// the host reply ring when `cores` card cores answer: core `(k - 1) %
    /// cores` owns sub-ring `(k - 1) % cores` and fills it in order, so the
    /// replies to the cells one core takes together are contiguous and can go
    /// out as one DMA copy.
    pub fn reply_offset(k: u64, cores: usize) -> usize {
        let per_core = REPLY_CELLS / cores;
        let (core, nth) = ((k - 1) as usize % cores, (k - 1) as usize / cores);
        (core * per_core + nth % per_core) * 64
    }

    /// Byte offset of request `k` in a host fetch ring ([`FETCH_RING_OFFSET`]).
    pub fn fetch_offset(k: u64) -> usize {
        ((k - 1) as usize % FETCH_CELLS) * 64
    }

    /// Write this cell into cacheable memory at `at` that a device reads, its
    /// head word last (release): any snapshot of the line that holds the new
    /// head holds the rest too.
    ///
    /// # Safety
    /// `at` points to a live, 64 B-aligned cell no one else writes.
    pub unsafe fn publish(self, at: *mut Cell) {
        let words = self.words();
        let at = at.cast::<u64>();
        // SAFETY: per the caller; the head word is written atomically.
        unsafe {
            for (i, &w) in words.iter().enumerate().skip(1) {
                at.add(i).write(w);
            }
            AtomicU64::from_ptr(at).store(words[0], Ordering::Release);
        }
    }

    /// Look for message `seq` in the cell at `at`, which a device may be
    /// writing, using the default acquire-load polling mode (`PollMode::Ldar`).
    ///
    /// # Safety
    /// `at` points to a live, 64 B-aligned cell.
    pub unsafe fn poll(at: *mut Cell, seq: u64) -> Slot {
        // SAFETY: forwarded from caller.
        unsafe { Self::poll_with_mode(at, seq, PollMode::Ldar) }
    }

    /// Look for message `seq` in the cell at `at`, varying only how the head
    /// sequence word is read (`ldar` / `ldr` / `wfe` / `civac`).
    ///
    /// # Safety
    /// `at` points to a live, 64 B-aligned cell.
    pub unsafe fn poll_with_mode(at: *mut Cell, seq: u64, mode: PollMode) -> Slot {
        // SAFETY: an aligned field of the cell.
        let seq_ptr = unsafe { &raw mut (*at).seq };
        let head = match mode {
            PollMode::Ldar => unsafe { AtomicU64::from_ptr(seq_ptr) }.load(Ordering::Acquire),
            PollMode::Ldr => unsafe { seq_ptr.read_volatile() },
            PollMode::Wfe => {
                #[cfg(target_arch = "aarch64")]
                {
                    let v: u64;
                    // SAFETY: aligned 64-bit load-acquire-exclusive of head word.
                    unsafe {
                        std::arch::asm!(
                            "ldaxr {v}, [{p}]",
                            v = out(reg) v,
                            p = in(reg) seq_ptr,
                            options(nostack, preserves_flags, readonly),
                        );
                    }
                    if v != seq {
                        // SAFETY: EL0 WFE waits for an invalidation/event clearing the monitor.
                        unsafe { std::arch::asm!("wfe", options(nostack, preserves_flags)) };
                        return Slot::Empty;
                    }
                    // SAFETY: clear exclusive monitor once the cell is ready.
                    unsafe { std::arch::asm!("clrex", options(nostack, preserves_flags)) };
                    v
                }
                #[cfg(not(target_arch = "aarch64"))]
                {
                    let v = unsafe { AtomicU64::from_ptr(seq_ptr) }.load(Ordering::Acquire);
                    if v != seq {
                        std::hint::spin_loop();
                        return Slot::Empty;
                    }
                    v
                }
            }
            PollMode::Civac => {
                let v = unsafe { AtomicU64::from_ptr(seq_ptr) }.load(Ordering::Acquire);
                if v != seq {
                    #[cfg(target_arch = "aarch64")]
                    // SAFETY: aligned cache-line VA inside our ring buffer; Linux sets SCTLR_EL1.UCI.
                    unsafe {
                        std::arch::asm!(
                            "dc civac, {p}",
                            "dsb ish",
                            p = in(reg) at,
                            options(nostack, preserves_flags),
                        );
                    }
                    return Slot::Empty;
                }
                v
            }
        };
        if head != seq {
            return Slot::Empty;
        }
        // SAFETY: as above; volatile because the device may still be writing,
        // one word at a time (a volatile read of the whole cell compiles to 64
        // byte loads).
        let cell = Cell::from_words(std::array::from_fn(|i| unsafe { at.cast::<u64>().add(i).read_volatile() }));
        if cell.seq_tail != seq { Slot::Partial } else { Slot::Complete(cell) }
    }
}

/// How the card polls the head word of the next request cell (MRV-236).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum PollMode {
    /// Acquire load (`ldar` on aarch64): existing baseline.
    #[default]
    Ldar,
    /// Plain volatile load (`ldr` on aarch64), no acquire barrier.
    Ldr,
    /// `ldaxr` + `wfe` when empty, `clrex` when ready (and on loop exit).
    Wfe,
    /// Acquire load, followed by `dc civac` + `dsb ish` on empty polls.
    Civac,
}

impl PollMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            PollMode::Ldar => "ldar",
            PollMode::Ldr => "ldr",
            PollMode::Wfe => "wfe",
            PollMode::Civac => "civac",
        }
    }
}

/// When ring cores publish their `next` sequence number into the shared control
/// cache line (`next_offset(core)`) (MRV-236).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum NextPublish {
    /// After every busy batch (existing baseline).
    #[default]
    Batch,
    /// Only once when the ring core exits (eliminates inter-core bouncing of `next_offset`).
    Exit,
}

impl NextPublish {
    pub const fn as_str(self) -> &'static str {
        match self {
            NextPublish::Batch => "batch",
            NextPublish::Exit => "exit",
        }
    }
}

/// Target cache line spun on by a `shadow` role core (MRV-236 S1-S3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShadowMode {
    /// Read `SHADOW_NEXT`, then spin on the shadow core's own 128 B-aligned private line (S1).
    Private,
    /// Read `SHADOW_NEXT` (`front`), then spin on ring cell `front + offset` (`front` = 0, `front+N` = N) (S2/S3).
    Front(u64),
}

impl ShadowMode {
    /// Target ring sequence number when spinning on the request ring (`None` for `Private`).
    pub const fn target_seq(self, front: u64) -> Option<u64> {
        match self {
            ShadowMode::Private => None,
            ShadowMode::Front(delta) => Some(front.saturating_add(delta)),
        }
    }
}

impl std::fmt::Display for ShadowMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ShadowMode::Private => f.write_str("private"),
            ShadowMode::Front(0) => f.write_str("front"),
            ShadowMode::Front(n) => write!(f, "front+{n}"),
        }
    }
}

impl std::str::FromStr for ShadowMode {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        if s == "private" {
            return Ok(ShadowMode::Private);
        }
        if s == "front" {
            return Ok(ShadowMode::Front(0));
        }
        if let Some(rest) = s.strip_prefix("front+") {
            let n = rest
                .parse::<u64>()
                .map_err(|_| format!("invalid --shadow offset '{s}': expected private|front|front+N"))?;
            return Ok(ShadowMode::Front(n));
        }
        Err(format!("invalid --shadow value '{s}': expected private|front|front+N"))
    }
}

/// Parse an optional `--extra-pmu` perf event spec `dev:name=config` (decimal or `0x` hex).
pub fn parse_extra_pmu_spec(spec: &str) -> Result<(&str, &str, u64), String> {
    let (dev, rest) = spec
        .split_once(':')
        .ok_or_else(|| format!("bad --extra-pmu '{spec}': expected dev:name=config"))?;
    let (name, val) = rest
        .split_once('=')
        .ok_or_else(|| format!("bad --extra-pmu '{spec}': expected dev:name=config"))?;
    if dev.is_empty() || name.is_empty() {
        return Err(format!("bad --extra-pmu '{spec}': empty device or event name"));
    }
    let cfg = if let Some(hex) = val.strip_prefix("0x").or_else(|| val.strip_prefix("0X")) {
        u64::from_str_radix(hex, 16)
    } else {
        val.parse::<u64>()
    }
    .map_err(|e| format!("bad --extra-pmu config '{val}': {e}"))?;
    Ok((dev, name, cfg))
}


/// Validate `inline_reflector` role counts against `--shadow`, `--dma`, and `--fetch-*` options.
#[allow(clippy::too_many_arguments)]
pub fn validate_reflector_layout(
    lcores: usize,
    roles_len: usize,
    ring_cores: usize,
    source_cores: usize,
    fetch_cores: usize,
    shadow_cores: usize,
    shadow: Option<ShadowMode>,
    dma_len: usize,
    has_fetch_dma: bool,
    fetch_chunk: u64,
    fetch_depth: usize,
) -> Result<(), String> {
    if roles_len != lcores {
        return Err(format!("--roles names {roles_len} cores, EAL runs {lcores}"));
    }
    let active_cores = lcores.saturating_sub(shadow_cores);
    let stream_cores = if source_cores > 0 { source_cores } else { ring_cores };
    if ring_cores > MAX_CORES || stream_cores > MAX_CORES {
        return Err(format!("the control area has room for {MAX_CORES} ring and {MAX_CORES} stream cores"));
    }
    if shadow_cores > 0 && shadow.is_none() {
        return Err("`shadow` role requires --shadow private|front|front+N".into());
    }
    if shadow_cores == 0 && shadow.is_some() {
        return Err("--shadow requires at least one `shadow` core in --roles".into());
    }
    if shadow_cores > 0 && ring_cores == 0 {
        return Err("`shadow` role requires at least one `ring` core".into());
    }
    if dma_len > 0 && dma_len != active_cores {
        return Err(format!("--dma needs one DPI VF per non-shadow core ({active_cores}), got {dma_len}"));
    }
    if fetch_cores > 1 || (fetch_cores == 1) != has_fetch_dma {
        return Err("one `fetch` core goes with one --fetch-dma VF".into());
    }
    if fetch_chunk == 0
        || !FETCH_CELLS.is_multiple_of(fetch_chunk as usize)
        || fetch_chunk as usize * fetch_depth > FETCH_CELLS
    {
        return Err(format!("--fetch-chunk must divide {FETCH_CELLS}, and chunk x depth must fit in it"));
    }
    Ok(())
}

/// Verify a polled reply cell and extract its `CardStamp` if this reply is stamped.
pub fn decode_ring_reply(
    cell: Option<Cell>,
    seq: u64,
    expected_tag: u64,
    stamp: bool,
    stamp_sparse: bool,
) -> (bool, bool, CardStamp) {
    let complete = cell.is_some_and(|c| {
        c.tag == expected_tag && if stamp { c.payload_tail_matches(seq) } else { c.payload_matches(seq) }
    });
    let per_reply_ts = stamp && (!stamp_sparse || (expected_tag & 63) < 8);
    let card_stamp = if per_reply_ts {
        cell.filter(|_| complete).map_or(CardStamp::default(), |c| unpack_card_stamp_words(&c.words()))
    } else {
        CardStamp::default()
    };
    (complete, per_reply_ts, card_stamp)
}
/// Clear the local exclusive monitor on aarch64 (used before exiting a `PollMode::Wfe` loop).
#[inline]
pub fn clear_exclusive_monitor() {
    #[cfg(target_arch = "aarch64")]
    // SAFETY: CLREX has no memory preconditions.
    unsafe {
        std::arch::asm!("clrex", options(nostack, preserves_flags));
    }
}

/// Packed per-cell card stamp decoded from reply payload words `w[3..6]` (MRV-236).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CardStamp {
    /// Card counter when the core first polled this cell (`card_t_in` if already landed).
    pub t_first_poll: u64,
    /// Card counter when the core saw this cell complete.
    pub t_in: u64,
    /// Card counter when the core started storing the reply.
    pub t_out: u64,
    /// Previous reply store duration in card counter ticks (clamped to 20 bits).
    pub write_ticks: u64,
    /// Empty polls on this cell before it was seen complete (`> 0` => held on landing).
    pub waited_polls: u64,
    /// OS CPU id of the card core that took this cell.
    pub core: u64,
    /// Ring core index (`0..ring_cores`) that took this cell.
    pub ring_idx: u64,
}

pub const STAMP_WRITE_TICKS_MASK: u64 = 0x000f_ffff;

/// Pack `(t_in, first_poll_delta, waited_polls, os_cpu, ring_idx)` into payload words `w[3..6]`
/// before `StoreReplies` fills in `out_delta` and `write_ticks`.
pub fn pack_card_stamp_entry(
    t_in: u64,
    t_first_poll: u64,
    waited_polls: u32,
    os_cpu: u8,
    ring_idx: u8,
) -> [u64; 3] {
    let first_delta = if t_first_poll != 0 {
        t_in.saturating_sub(t_first_poll).min(u64::from(u32::MAX))
    } else {
        0
    };
    let meta = (u64::from(waited_polls) << 32)
        | (u64::from(os_cpu) << 24)
        | (u64::from(ring_idx & 0x0f) << 20);
    [t_in, first_delta, meta]
}

/// Finish packing a stamped reply right before storing it (`t_out` and `last_write_ticks`).
pub fn finalize_card_stamp_words(words: &mut [u64; 8], t_out: u64, last_write_ticks: u64) {
    let out_delta = t_out.saturating_sub(words[3]).min(u64::from(u32::MAX));
    words[4] = (words[4] & 0xffff_ffff) | (out_delta << 32);
    words[5] = (words[5] & !STAMP_WRITE_TICKS_MASK) | (last_write_ticks & STAMP_WRITE_TICKS_MASK);
}

/// Unpack `CardStamp` from reply payload words `w[3..6]`.
pub fn unpack_card_stamp_words(words: &[u64; 8]) -> CardStamp {
    let t_in = words[3];
    let first_delta = words[4] & 0xffff_ffff;
    let out_delta = words[4] >> 32;
    CardStamp {
        t_first_poll: t_in.saturating_sub(first_delta),
        t_in,
        t_out: t_in.wrapping_add(out_delta),
        write_ticks: words[5] & STAMP_WRITE_TICKS_MASK,
        waited_polls: words[5] >> 32,
        core: (words[5] >> 24) & 0xff,
        ring_idx: (words[5] >> 20) & 0x0f,
    }
}

/// What a receiver finds where message `seq` goes.
pub enum Slot {
    /// Not there yet (an older message's cell, or zeroes).
    Empty,
    /// Its head has landed but its tail has not.
    Partial,
    Complete(Cell),
}

/// Host side of ring reply mode: the ring the card writes replies into (by DPI
/// DMA or core stores), read in sequence order. A reply is the request cell
/// echoed unchanged (so the DPI engine can copy it straight from the card's
/// ring), except that a card that found the payload corrupt zeroes `tsc`.
/// Every part of a reply is checked (sequence number at both ends, tag,
/// payload pattern); one that does not match yet is taken to be still
/// landing — core stores through a write-combining window may arrive in
/// pieces, in any order — and retried, and only after [`TAIL_WAIT_US`] is it
/// reported (as torn if its tail never matched, else as bad).
pub struct RingReplies<'m, 'e> {
    ring: &'m Memzone<'e>,
    /// Ring sequence of loop message 0: message `k` went out as `base + k`.
    base: u64,
    /// Card cores answering, which sets the sub-ring layout.
    cores: usize,
    next: u64,
    /// TSC when reply `next` was first seen begun but not complete.
    partial_since: Option<u64>,
    /// TSC ticks to wait for an incomplete reply.
    settle: u64,
    /// Polls that found reply `next` missing while a later one (within
    /// [`LOOKAHEAD`]) had landed: replies arriving out of order. Counted only
    /// with several card cores (one writer fills its ring in order), since
    /// peeking at lines the card is still writing costs the poller.
    blocked: u64,
    stamp: bool,
    stamp_sparse: bool,
    empty_on_next: u64,
}

/// How far past a missing reply [`RingReplies`] looks for later ones.
const LOOKAHEAD: u64 = 8;

impl<'m, 'e> RingReplies<'m, 'e> {
    /// `ring` must be zeroed and at least `REPLY_CELLS * 64` bytes; `cores`
    /// is the number of card cores answering.
    pub fn new(eal: &Eal, ring: &'m Memzone<'e>, base: u64, cores: usize) -> Self {
        let settle = eal.tsc_hz() / 1_000_000 * TAIL_WAIT_US;
        RingReplies {
            ring,
            base,
            cores,
            next: 1,
            partial_since: None,
            settle,
            blocked: 0,
            stamp: false,
            stamp_sparse: false,
            empty_on_next: 0,
        }
    }

    /// MRV-50/MRV-75 experiment: expect stamped replies.
    pub fn stamped(mut self, sparse: bool) -> Self {
        self.stamp = true;
        self.stamp_sparse = sparse;
        self
    }

    /// Polls that waited on a missing reply while a later one had landed.
    pub fn blocked(&self) -> u64 {
        self.blocked
    }

    fn cell(&self, k: u64) -> *mut Cell {
        // SAFETY: a 64 B-aligned cell inside the ring memzone.
        unsafe { self.ring.as_ptr().add(Cell::reply_offset(k, self.cores)) }.cast()
    }
}

impl Replies for RingReplies<'_, '_> {
    fn poll(&mut self, mut on_reply: impl FnMut(Reply)) -> usize {
        let mut n = 0;
        // One timestamp per poll, taken when its first reply is seen (as for a
        // received burst): an RDTSCP per reply cost ~10 ns of a ~50 ns budget.
        let mut seen = None;
        while n < 32 {
            let seq = self.base + self.next;
            let cell = match unsafe { Cell::poll(self.cell(self.next), seq) } {
                Slot::Empty => {
                    self.empty_on_next += 1;
                    let later_landed = self.cores > 1
                        && (1..=LOOKAHEAD).any(|d| {
                            // SAFETY: as in `cell`; only the head word is read.
                            unsafe { self.cell(self.next + d).cast::<u64>().read_volatile() == seq + d }
                        });
                    self.blocked += u64::from(later_landed);
                    break;
                }
                Slot::Partial => None,
                Slot::Complete(cell) => Some(cell),
            };
            let (complete, per_reply_ts, stamp) =
                decode_ring_reply(cell, seq, self.next, self.stamp, self.stamp_sparse);
            if !complete {
                let now = rdtsc_after_loads();
                if now - *self.partial_since.get_or_insert(now) < self.settle {
                    break; // parts still landing: retry on a later poll
                }
            }
            let now = if per_reply_ts { rdtsc_after_loads() } else { *seen.get_or_insert_with(rdtsc_after_loads) };
            let integrity = match (cell, self.partial_since.take()) {
                (None, _) => Integrity::Torn,
                (Some(_), Some(since)) => Integrity::LateTail(now - since),
                (Some(_), None) => Integrity::Whole,
            };
            let tsc = cell.filter(|_| complete).map_or(0, |c| c.tsc);
            let waited_polls = std::mem::replace(&mut self.empty_on_next, 0);
            on_reply(Reply {
                seq: self.next,
                tsc,
                at: now,
                integrity,
                card_first_poll_ts: stamp.t_first_poll,
                card_ts: stamp.t_in,
                card_out_ts: stamp.t_out,
                card_write_ticks: stamp.write_ticks,
                card_waited_polls: stamp.waited_polls,
                card_core: stamp.core,
                card_ring_idx: stamp.ring_idx,
                waited_polls,
            });
            self.next += 1;
            n += 1;
        }
        n
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn card_stamp_roundtrip_and_integrity() {
        let seq = 42_000;
        let mut cell = Cell::new(seq, 7, 123_456);
        let packed = pack_card_stamp_entry(10_000_500, 10_000_120, 19, 4, 1);
        cell.payload[..8].copy_from_slice(&packed[0].to_ne_bytes());
        cell.payload[8..16].copy_from_slice(&packed[1].to_ne_bytes());
        cell.payload[16..24].copy_from_slice(&packed[2].to_ne_bytes());
        let mut words = cell.words();
        finalize_card_stamp_words(&mut words, 10_000_580, 35);
        let stamped = Cell::from_words(words);
        assert!(stamped.payload_tail_matches(seq));
        let got = unpack_card_stamp_words(&stamped.words());
        assert_eq!(got.t_first_poll, 10_000_120);
        assert_eq!(got.t_in, 10_000_500);
        assert_eq!(got.t_out, 10_000_580);
        assert_eq!(got.write_ticks, 35);
        assert_eq!(got.waited_polls, 19);
        assert_eq!(got.core, 4);
        assert_eq!(got.ring_idx, 1);

        // Corrupting the sentinel word w[6] must fail integrity check.
        let mut bad_words = words;
        bad_words[6] ^= 1;
        assert!(!Cell::from_words(bad_words).payload_tail_matches(seq));
    }

    #[test]
    fn card_stamp_unheld_cell_first_poll_equals_t_in() {
        let mut cell = Cell::new(100, 100, 999);
        let packed = pack_card_stamp_entry(5_000_000, 0, 0, 3, 0);
        cell.payload[..8].copy_from_slice(&packed[0].to_ne_bytes());
        cell.payload[8..16].copy_from_slice(&packed[1].to_ne_bytes());
        cell.payload[16..24].copy_from_slice(&packed[2].to_ne_bytes());
        let mut words = cell.words();
        finalize_card_stamp_words(&mut words, 5_000_040, 12);
        let got = unpack_card_stamp_words(&words);
        assert_eq!(got.t_first_poll, 5_000_000);
        assert_eq!(got.t_in, 5_000_000);
        assert_eq!(got.waited_polls, 0);
        assert_eq!(got.core, 3);
        assert_eq!(got.ring_idx, 0);
    }

    #[test]
    fn poll_with_mode_all_variants() {
        let mut cell = Cell::new(0, 0, 0);
        for mode in [PollMode::Ldar, PollMode::Ldr, PollMode::Wfe, PollMode::Civac] {
            assert!(matches!(unsafe { Cell::poll_with_mode(&raw mut cell, 1, mode) }, Slot::Empty));
        }
        cell.seq = 1;
        for mode in [PollMode::Ldar, PollMode::Ldr, PollMode::Wfe, PollMode::Civac] {
            assert!(matches!(unsafe { Cell::poll_with_mode(&raw mut cell, 1, mode) }, Slot::Partial));
        }
        cell = Cell::new(1, 1, 77);
        for mode in [PollMode::Ldar, PollMode::Ldr, PollMode::Wfe, PollMode::Civac] {
            match unsafe { Cell::poll_with_mode(&raw mut cell, 1, mode) } {
                Slot::Complete(c) => {
                    assert_eq!(c.seq, 1);
                    assert_eq!(c.tag, 1);
                    assert_eq!(c.tsc, 77);
                    assert!(c.payload_matches(1));
                }
                _ => panic!("expected Complete for {mode:?}"),
            }
        }
    }

    #[test]
    fn shadow_mode_parse_and_target() {
        assert_eq!("private".parse::<ShadowMode>().unwrap(), ShadowMode::Private);
        assert_eq!(ShadowMode::Private.to_string(), "private");
        assert_eq!(ShadowMode::Private.target_seq(100), None);

        assert_eq!("front".parse::<ShadowMode>().unwrap(), ShadowMode::Front(0));
        assert_eq!(ShadowMode::Front(0).to_string(), "front");
        assert_eq!(ShadowMode::Front(0).target_seq(100), Some(100));

        assert_eq!("front+4".parse::<ShadowMode>().unwrap(), ShadowMode::Front(4));
        assert_eq!(ShadowMode::Front(4).to_string(), "front+4");
        assert_eq!(ShadowMode::Front(4).target_seq(100), Some(104));

        assert_eq!("front+0".parse::<ShadowMode>().unwrap(), ShadowMode::Front(0));
        assert!("front-1".parse::<ShadowMode>().is_err());
        assert!("front+".parse::<ShadowMode>().is_err());
        assert!("other".parse::<ShadowMode>().is_err());
    }

    #[test]
    fn card_stamp_saturates_long_first_poll_wait() {
        let mut cell = Cell::new(1, 1, 55);
        // Simulate a 10-second idle wait before first request arrives (exceeds u32::MAX at 1 GHz).
        let t_first = 1_000u64;
        let t_in = t_first + 10_000_000_000u64;
        let packed = pack_card_stamp_entry(t_in, t_first, u32::MAX, 6, 3);
        cell.payload[..8].copy_from_slice(&packed[0].to_ne_bytes());
        cell.payload[8..16].copy_from_slice(&packed[1].to_ne_bytes());
        cell.payload[16..24].copy_from_slice(&packed[2].to_ne_bytes());
        let mut words = cell.words();
        finalize_card_stamp_words(&mut words, t_in + 120, 0x0020_0000);
        let stamped = Cell::from_words(words);
        assert!(stamped.payload_tail_matches(1));
        let got = unpack_card_stamp_words(&stamped.words());
        assert_eq!(got.t_in, t_in);
        assert_eq!(got.t_in - got.t_first_poll, u64::from(u32::MAX));
        assert_eq!(got.t_out, t_in + 120);
        assert_eq!(got.waited_polls, u64::from(u32::MAX));
        assert_eq!(got.core, 6);
        assert_eq!(got.ring_idx, 3);
    }

    #[test]
    fn extra_pmu_spec_parsing() {
        assert_eq!(parse_extra_pmu_spec("pem0:inb_wr=0x1a").unwrap(), ("pem0", "inb_wr", 0x1a));
        assert_eq!(parse_extra_pmu_spec("iobn:occ=42").unwrap(), ("iobn", "occ", 42));
        assert!(parse_extra_pmu_spec("missing_colon").is_err());
        assert!(parse_extra_pmu_spec("dev:missing_eq").is_err());
        assert!(parse_extra_pmu_spec(":ev=1").is_err());
        assert!(parse_extra_pmu_spec("dev:=1").is_err());
        assert!(parse_extra_pmu_spec("dev:ev=0xzz").is_err());
    }

    #[test]
    fn shadow_frontier_offset_stays_inside_ring() {
        let mode = "front+4".parse::<ShadowMode>().unwrap();
        for front in [1u64, 2, RING_CELLS as u64 - 2, RING_CELLS as u64, RING_CELLS as u64 + 100] {
            let target = mode.target_seq(front).unwrap();
            let off = Cell::offset(target);
            assert!(off % 64 == 0 && off < READY_OFFSET, "offset {off} escaped ring for front={front}");
        }
        assert_eq!(PollMode::Ldar.as_str(), "ldar");
        assert_eq!(PollMode::Ldr.as_str(), "ldr");
        assert_eq!(PollMode::Wfe.as_str(), "wfe");
        assert_eq!(PollMode::Civac.as_str(), "civac");
        assert_eq!(NextPublish::Batch.as_str(), "batch");
        assert_eq!(NextPublish::Exit.as_str(), "exit");
    }

    #[test]
    fn decode_ring_reply_sparse_and_integrity() {
        // Sparse-window tag (tag=5 -> (5 & 63) < 8): stamped payload decodes all 4 MRV-236 fields.
        let seq = 1005;
        let tag = 5;
        let mut cell = Cell::new(seq, tag, 777);
        let packed = pack_card_stamp_entry(20_000_900, 20_000_500, 7, 4, 1);
        cell.payload[..8].copy_from_slice(&packed[0].to_ne_bytes());
        cell.payload[8..16].copy_from_slice(&packed[1].to_ne_bytes());
        cell.payload[16..24].copy_from_slice(&packed[2].to_ne_bytes());
        let mut words = cell.words();
        finalize_card_stamp_words(&mut words, 20_000_960, 22);
        let stamped_cell = Cell::from_words(words);

        let (complete, per_ts, st) = decode_ring_reply(Some(stamped_cell), seq, tag, true, true);
        assert!(complete && per_ts);
        assert_eq!(st.t_first_poll, 20_000_500);
        assert_eq!(st.t_in, 20_000_900);
        assert_eq!(st.t_out, 20_000_960);
        assert_eq!(st.waited_polls, 7);
        assert_eq!(st.core, 4);
        assert_eq!(st.ring_idx, 1);

        // Non-sparse tag (tag=10 -> (10 & 63) >= 8): unstamped cell completes, zeroed CardStamp.
        let plain_cell = Cell::new(1010, 10, 888);
        let (complete2, per_ts2, st2) = decode_ring_reply(Some(plain_cell), 1010, 10, true, true);
        assert!(complete2 && !per_ts2);
        assert_eq!(st2, CardStamp::default());

        // Wrong tag or torn cell must fail completeness.
        let (complete3, _, _) = decode_ring_reply(Some(stamped_cell), seq, tag + 1, true, true);
        assert!(!complete3);
        let (complete4, _, _) = decode_ring_reply(None, seq, tag, true, true);
        assert!(!complete4);
    }

    #[test]
    fn validate_reflector_layout_arms() {
        // B / A1-A4: WC2 (2 ring cores)
        assert!(validate_reflector_layout(2, 2, 2, 0, 0, 0, None, 0, false, 64, 16).is_ok());
        // B1: WC1 (1 ring core)
        assert!(validate_reflector_layout(1, 1, 1, 0, 0, 0, None, 0, false, 64, 16).is_ok());
        // S1 / S2 / S3: 1 ring + 1 shadow with --shadow
        assert!(validate_reflector_layout(2, 2, 1, 0, 0, 1, Some(ShadowMode::Private), 0, false, 64, 16).is_ok());
        assert!(validate_reflector_layout(2, 2, 1, 0, 0, 1, Some(ShadowMode::Front(0)), 0, false, 64, 16).is_ok());
        assert!(validate_reflector_layout(2, 2, 1, 0, 0, 1, Some(ShadowMode::Front(4)), 0, false, 64, 16).is_ok());
        // WC4: 4 ring cores
        assert!(validate_reflector_layout(4, 4, 4, 0, 0, 0, None, 0, false, 64, 16).is_ok());
        // Error cases
        assert!(validate_reflector_layout(2, 2, 1, 0, 0, 1, None, 0, false, 64, 16).is_err());
        assert!(validate_reflector_layout(1, 1, 1, 0, 0, 0, Some(ShadowMode::Private), 0, false, 64, 16).is_err());
        assert!(validate_reflector_layout(1, 1, 0, 0, 0, 1, Some(ShadowMode::Private), 0, false, 64, 16).is_err());
        assert!(validate_reflector_layout(2, 2, 1, 0, 0, 1, Some(ShadowMode::Private), 2, false, 64, 16).is_err());
        assert!(validate_reflector_layout(2, 2, 1, 0, 0, 1, Some(ShadowMode::Private), 1, false, 64, 16).is_ok());
    }
}
