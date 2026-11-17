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
//! the host run holding the lease ([`crate::lease`]: the host maps the slot
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
    /// writing.
    ///
    /// # Safety
    /// `at` points to a live, 64 B-aligned cell.
    pub unsafe fn poll(at: *mut Cell, seq: u64) -> Slot {
        // SAFETY: an aligned field of the cell. The acquire load orders the
        // reads of the rest of the cell after it.
        let head = unsafe { AtomicU64::from_ptr(&raw mut (*at).seq) }.load(Ordering::Acquire);
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
}

/// How far past a missing reply [`RingReplies`] looks for later ones.
const LOOKAHEAD: u64 = 8;

impl<'m, 'e> RingReplies<'m, 'e> {
    /// `ring` must be zeroed and at least `REPLY_CELLS * 64` bytes; `cores`
    /// is the number of card cores answering.
    pub fn new(eal: &Eal, ring: &'m Memzone<'e>, base: u64, cores: usize) -> Self {
        let settle = eal.tsc_hz() / 1_000_000 * TAIL_WAIT_US;
        RingReplies { ring, base, cores, next: 1, partial_since: None, settle, blocked: 0 }
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
            let complete = cell.is_some_and(|c| c.tag == self.next && c.payload_matches(seq));
            if !complete {
                let now = rdtsc_after_loads();
                if now - *self.partial_since.get_or_insert(now) < self.settle {
                    break; // parts still landing: retry on a later poll
                }
            }
            let now = *seen.get_or_insert_with(rdtsc_after_loads);
            let integrity = match (cell, self.partial_since.take()) {
                (None, _) => Integrity::Torn,
                (Some(_), Some(since)) => Integrity::LateTail(now - since),
                (Some(_), None) => Integrity::Whole,
            };
            let tsc = cell.filter(|_| complete).map_or(0, |c| c.tsc);
            on_reply(Reply { seq: self.next, tsc, at: now, integrity });
            self.next += 1;
            n += 1;
        }
        n
    }
}
