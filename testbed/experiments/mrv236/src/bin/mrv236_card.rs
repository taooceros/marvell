//! Card side of the BAR4 inline path. Points host PF BAR4 slot `inline::SLOT`
//! at a 4 MiB hugepage buffer it owns and serves it with its EAL lcores
//! (`-l`), each in one role (`--roles`, in lcore order; default all `ring`):
//!
//! - `ring`: core `i` of the `n` ring cores takes the cells the host writes
//!   into the slot with `(seq - 1) % n == i`, in sequence order (cell `k` only
//!   when both copies of its sequence number read `k`), and reflects each.
//!   If the host published a reply ring, the reply is the request cell
//!   written into that ring as host function (`--host-pf`, `--host-vf`):
//!   copied by the DPI engine (`--dma`) or stored by the core itself through
//!   its own PEM outbound region (`--store`). In pull mode it is a word pair
//!   the host reads back; with `REPLY_NONE` nothing; otherwise a frame over
//!   the SDP VF (DPDK port 0, one TX queue per core). Every cell a core finds
//!   goes out in one submission: one transmit, or one DMA doorbell.
//! - `source`: writes card -> host streams (`STREAM_OFFSET`) the same way.
//!   Without `source` cores, the ring cores write them.
//! - `fetch`: reads the host's request ring in host memory with DPI (PCIe
//!   reads, `--fetch-dma`) instead of having requests pushed through BAR4,
//!   and takes its cells in order (`FETCH_RING_OFFSET`).
//!
//! Hosts get the slot through a lease over TCP on the control path
//! (`--control`, see `testbed::lease`): the reflector stores each run's nonce
//! in the slot before granting it, and on exit takes the slot back from its
//! holder (waiting for it to let go) before the slot and outbound regions
//! are restored. A reflector killed with SIGKILL leaves them changed: the
//! next start, or `--restore` alone, puts back the firmware defaults first.
//!
//! usage: inline_reflector -l CORES [EAL args] -- [--roles ring|source|fetch,...] [--dma DPI_VF,... | --store]
//!        [--fetch-dma DPI_VF] [--control ADDR:PORT]   (run as root)
//!        inline_reflector --restore

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("inline_reflector runs on the aarch64 card");
    std::process::exit(2);
}

#[cfg(target_arch = "aarch64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "aarch64")]
mod imp {
    use std::collections::VecDeque;
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::size_of;
    use std::net::{TcpListener, TcpStream};
    use std::os::fd::RawFd;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, HostDir, HostDma, Mempool, Memzone, PcieFunction, Port, TxQueue, rdtsc};
    use mrv236::inline::{
        CLOCK_OFFSET, CORES_OFFSET, Cell, DONE_OFFSET, FETCH_CELLS, FETCH_NEXT_OFFSET, FETCH_RING_OFFSET,
        FETCH_TRIGGER_OFFSET, NONCE_OFFSET, NextPublish, PollMode, READY, READY_OFFSET, REPLY_CELLS,
        REPLY_NONE, REPLY_PULL, REPLY_RING_OFFSET, SLOT, SLOT_BYTES, STREAM_CORES_OFFSET, STREAM_OFFSET,
        STREAM_RING_OFFSET, ShadowMode, Slot, TAKEN_OFFSET, clear_exclusive_monitor, finalize_card_stamp_words,
        next_offset, pack_card_stamp_entry, parse_extra_pmu_spec, validate_reflector_layout,
    };
    use testbed::lease;
    use testbed::mmio::{
        Bar4Window, OUTBOUND_REGION_BYTES, OUTBOUND_REGIONS, OutboundWindow, STORE_REGIONS, restore_defaults,
        store64_gathered, store64_head_last,
    };
    use testbed::{Mac, write_request};

    const PORT: u16 = 0;
    const DESC: u16 = 2048;
    const REPLY_FRAME: u16 = 64;
    /// DMA ring size; at most this many copies are outstanding. cnxk sizes its
    /// command-chunk pool from it (~480 commands per chunk) plus 16 per lcore,
    /// and the pool must hold 1.5x its 16-entry cache: with one lcore that
    /// takes 8+ chunks, i.e. nb_desc above ~3.4k.
    const DMA_DESC: u16 = 4096;
    /// Local reply cells the DPI engine copies from, reused round-robin once
    /// the copy that read them has completed. Also sizes the per-copy submit
    /// stamps, so it must exceed DMA_DESC.
    const STAGE_CELLS: usize = 8192;
    const BATCH: usize = 32;

    /// Where the DPI engine reads a reply from. (Cleaning the stage line with
    /// `DC CVAC`/`DC CIVAC` + `DSB` before the doorbell was measured too: +0.15-
    /// 0.25 us at W=1 and a third of the W=64 rate, so it is not offered.)
    #[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
    enum DmaSource {
        /// `request` with one core (its cells are contiguous, so a batch is one
        /// copy), `stage` with several (theirs are strided).
        Auto,
        /// The request cell itself (written by PEM, only read by the core); a
        /// corrupt request still goes through a stage cell.
        Request,
        /// A copy the core writes into a stage cell (dirty in its cache).
        Stage,
    }

    /// What a core does (see the module documentation).
    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Role {
        Ring,
        Source,
        Fetch,
        Shadow,
    }

    /// CLI wrapper for `mrv236::inline::NextPublish` (MRV-236).
    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum NextPublishArg {
        Batch,
        Exit,
    }

    impl From<NextPublishArg> for NextPublish {
        fn from(v: NextPublishArg) -> Self {
            match v {
                NextPublishArg::Batch => NextPublish::Batch,
                NextPublishArg::Exit => NextPublish::Exit,
            }
        }
    }

    /// CLI wrapper for `mrv236::inline::PollMode` (MRV-236; `nc` intentionally omitted pending user approval).
    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum PollArg {
        Ldar,
        Ldr,
        Wfe,
        Civac,
    }

    impl From<PollArg> for PollMode {
        fn from(v: PollArg) -> Self {
            match v {
                PollArg::Ldar => PollMode::Ldar,
                PollArg::Ldr => PollMode::Ldr,
                PollArg::Wfe => PollMode::Wfe,
                PollArg::Civac => PollMode::Civac,
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        /// MAC of the host VF the SDP replies go to.
        #[arg(long, default_value = "00:00:00:01:01:01")]
        peer: Mac,
        /// Role of each lcore, in `-l` order (default: every core `ring`).
        #[arg(long, value_enum, value_delimiter = ',')]
        roles: Vec<Role>,
        /// DPI VFs (dmadev names, e.g. 0000:06:00.1) for replies and streams,
        /// one per core in lcore order.
        #[arg(long, value_delimiter = ',', conflicts_with = "store")]
        dma: Vec<String>,
        /// DPI VF the `fetch` core reads the host request ring with.
        #[arg(long)]
        fetch_dma: Option<String>,
        /// Cells per DPI read of the host request ring. Throughput follows
        /// the bytes in flight (chunk x depth) up to ~64 KiB: 32 KiB gave
        /// 170 M cells/s (10.9 GB/s: ~3 us per read under load), 64-128 KiB
        /// 187-189.
        #[arg(long, default_value_t = 64)]
        fetch_chunk: u64,
        /// DPI reads of the host request ring kept in flight.
        #[arg(long, default_value_t = 16)]
        fetch_depth: usize,
        /// Store replies with the cores themselves, each through its own PEM0
        /// outbound region (posted PCIe writes; no DMA engine).
        #[arg(long)]
        store: bool,
        /// With `--store`: map the regions write-combining (needs the
        /// `pem_wc` card kernel module, `card-kmod/pem_wc`) and store each
        /// reply as one gatherable 64 B block (one full-line PCIe write).
        #[arg(long, requires = "store")]
        store_wc: bool,
        /// Host function replies are written as: DPI numbering, 0 = the PF,
        /// n = its VF n-1.
        #[arg(long, default_value_t = 1)]
        host_vf: u16,
        #[arg(long, default_value_t = 0)]
        host_pf: u8,
        /// PCIe core (PEM) the host is behind.
        #[arg(long, default_value_t = 0)]
        pem: u8,
        /// Time the card's share of each DMA reply (cell seen -> DMA submitted ->
        /// completion reaped) and the poll loop period; printed on exit.
        #[arg(long)]
        timing: bool,
        #[arg(long, value_enum, default_value_t = DmaSource::Auto)]
        dma_source: DmaSource,
        /// Most replies one DMA copy carries: replies a core takes together
        /// that are contiguous both locally and in the host ring go out as one
        /// copy (1 = one copy per reply).
        #[arg(long, default_value_t = BATCH as u32, value_parser = clap::value_parser!(u32).range(1..=BATCH as i64))]
        dma_coalesce: u32,
        /// Address hosts ask for the slot on (TCP, card `sdp1-0`).
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        /// Only put back what a reflector killed with SIGKILL left behind
        /// (BAR4 slot 15, outbound regions 248-255), then exit. Needs no EAL
        /// arguments.
        #[arg(long)]
        restore: bool,
        /// MRV-75 experiment: TCP sync address for PMU_START/PMU_STOP/SET_PAUSE (empty = disabled).
        #[arg(long, hide = true, default_value = "")]
        sync: String,
        /// MRV-75 experiment: pause duration in ns on empty ring polls (0 = no pause).
        #[arg(long, hide = true, default_value_t = 0)]
        empty_pause_ns: u64,
        /// MRV-50/MRV-75 experiment: stamp card_t_in/out/waited into reply payload words 0..2.
        #[arg(long, hide = true)]
        exp_stamp: bool,
        /// MRV-50/MRV-75 experiment: with --exp-stamp, only stamp tags with (tag & 63) < 8.
        #[arg(long, hide = true)]
        exp_stamp_sparse: bool,
        /// MRV-236: when ring cores write `next_offset(core)` (`batch` = every busy batch, `exit` = once on exit).
        #[arg(long, value_enum, default_value_t = NextPublishArg::Batch)]
        next_publish: NextPublishArg,
        /// MRV-236: head-word read strategy in `Cell::poll` (`ldar` | `ldr` | `wfe` | `civac`).
        #[arg(long, value_enum, default_value_t = PollArg::Ldar)]
        poll: PollArg,
        /// MRV-236: target line for `shadow` role cores (`private` | `front` | `front+N`).
        #[arg(long)]
        shadow: Option<ShadowMode>,
        /// MRV-236: run an offline EL0 probe of `dc civac` and `ldaxr`/`wfe`/`clrex` (no EAL, no BAR4), then exit.
        #[arg(long, hide = true)]
        probe_el0_ops: bool,
        /// MRV-236: optional extra perf PMU counters discovered under /sys/bus/event_source/devices/
        /// (format `dev:name=0xHEX`, e.g. PEM/IOBN perf PMU; pure perf_event_open, zero CSR access).
        #[arg(long, hide = true, value_delimiter = ',')]
        extra_pmu: Vec<String>,
    }

    /// Card-side timestamps for DMA replies (`--timing`), in counter ticks.
    struct Stamps {
        /// Submit time of each copy, by copy index.
        submitted: Vec<u64>,
        /// Cell seen complete -> doorbell rung, per batch.
        to_submit: Vec<u64>,
        /// Doorbell -> completion seen by `reap`, per copy (granularity: one
        /// poll loop iteration).
        to_complete: Vec<u64>,
    }

    /// A copy being built: `cells` replies from `src` to `dst`, `staged` of
    /// them in stage cells.
    #[derive(Clone, Copy)]
    struct Run {
        src: u64,
        dst: u64,
        cells: u32,
        staged: u32,
    }

    /// Replies one core's DPI VF copies into its sub-ring of the host's reply
    /// ring, as few copies as contiguity allows.
    struct DmaReplies<'e> {
        dma: HostDma<'e>,
        stage: Memzone<'e>,
        source: DmaSource,
        /// Card cores sharing the host ring, which sets its sub-ring layout.
        cores: usize,
        coalesce: u32,
        run: Option<Run>,
        /// Copies enqueued / doorbelled / completed, and completions that
        /// reported an error.
        issued: u64,
        submitted: u64,
        completed: u64,
        failed: u64,
        /// Replies sent, and stage cells written / released.
        replies: u64,
        staged: u64,
        unstaged: u64,
        /// Stage cells each outstanding copy reads, oldest first.
        in_flight: VecDeque<u32>,
        stamps: Option<Stamps>,
    }

    impl DmaReplies<'_> {
        /// Add `reply` (the request echoed, `tag` = host loop seq) to the copy
        /// being built; `request` is the IOVA of the request cell if `reply`
        /// equals it.
        fn send(&mut self, reply: &Cell, request: Option<u64>, ring: u64) -> Result<(), Box<dyn Error>> {
            let (src, staged) = match (self.source, request) {
                (DmaSource::Request, Some(iova)) => (iova, 0),
                _ => {
                    while self.staged - self.unstaged >= STAGE_CELLS as u64 {
                        self.flush()?; // every stage cell is in some copy: free some
                        self.submit()?;
                        self.reap();
                    }
                    let offset = (self.staged as usize % STAGE_CELLS) * 64;
                    // SAFETY: a cell inside the stage memzone that no outstanding
                    // copy reads (checked above).
                    unsafe { self.stage.as_ptr().add(offset).cast::<Cell>().write(*reply) };
                    self.staged += 1;
                    (self.stage.iova() + offset as u64, 1)
                }
            };
            let dst = ring + Cell::reply_offset(reply.tag, self.cores) as u64;
            self.replies += 1;
            if let Some(run) = &mut self.run {
                let len = u64::from(run.cells) * 64;
                if run.src + len == src && run.dst + len == dst && run.cells < self.coalesce {
                    run.cells += 1;
                    run.staged += staged;
                    return Ok(());
                }
            }
            self.flush()?;
            self.run = Some(Run { src, dst, cells: 1, staged });
            Ok(())
        }

        /// Enqueue the copy being built.
        fn flush(&mut self) -> Result<(), Box<dyn Error>> {
            let Some(run) = self.run.take() else { return Ok(()) };
            while !self.dma.copy(run.src, run.dst, run.cells * 64)? {
                self.submit()?; // ring full: what is queued must start before it can complete
                self.reap();
            }
            self.in_flight.push_back(run.staged);
            self.issued += 1;
            Ok(())
        }

        fn submit(&mut self) -> Result<(), Box<dyn Error>> {
            if self.submitted < self.issued {
                self.dma.submit()?;
                if let Some(s) = &mut self.stamps {
                    let now = rdtsc();
                    for i in self.submitted..self.issued {
                        s.submitted[i as usize % STAGE_CELLS] = now;
                    }
                }
                self.submitted = self.issued;
            }
            Ok(())
        }

        fn reap(&mut self) {
            let (n, failed) = self.dma.completed(BATCH as u16 * 2);
            if let (Some(s), true) = (&mut self.stamps, n > 0) {
                let now = rdtsc();
                for i in self.completed..self.completed + u64::from(n) {
                    s.to_complete.push(now - s.submitted[i as usize % STAGE_CELLS]);
                }
            }
            for _ in 0..n {
                self.unstaged += u64::from(self.in_flight.pop_front().expect("a completion per copy"));
            }
            self.completed += u64::from(n);
            self.failed += u64::from(failed);
        }
    }

    /// Replies one core stores straight into its sub-ring of the host's reply
    /// ring, through its own PEM outbound region: posted PCIe writes, no DMA
    /// engine and nothing to complete.
    ///
    /// Device-nGnRnE region (default): a reply is four 16 B `STP`s of X
    /// registers, head last, without PEM write merging: separate stores to one
    /// peripheral arrive in program order, so the host never sees a head
    /// without the rest. Every store leaves as its own 16 B write (4 per
    /// reply, ~62 M replies/s at most from any number of cores). Measured and
    /// dropped: one 64 B `ST1` and two 32 B `STP`s of Q registers (the core
    /// splits them into 16 B writes that land in any order), and PEM-merged
    /// writes (torn below 16 B).
    ///
    /// Write-combining region (`--store-wc`): a reply is one gathered 64 B
    /// block, normally one full-line write (~177 M replies/s from 4 cores);
    /// when gathering fails the pieces land in any order, which the host's
    /// every-part check absorbs.
    struct StoreReplies {
        window: OutboundWindow,
        wc: bool,
        cores: usize,
        /// The host ring the region points at (0 = none yet).
        target: u64,
        stored: u64,
        /// `--timing`: ticks from the first store to its completion (`DSB`).
        durations: Option<Vec<u64>>,
        last_write_ticks: u64,
    }

    impl StoreReplies {
        fn send(&mut self, reply: &Cell, ring: u64, active_stamp: bool) -> Result<(), Box<dyn Error>> {
            if ring != self.target {
                let offset = ring % OUTBOUND_REGION_BYTES;
                if offset + (REPLY_CELLS * 64) as u64 > OUTBOUND_REGION_BYTES {
                    return Err(format!("host reply ring {ring:#x} crosses a 2 GiB outbound region").into());
                }
                if self.wc {
                    unsafe { std::arch::asm!("dsb sy", options(nostack, preserves_flags)) };
                }
                self.window.retarget(ring - offset)?;
                self.target = ring;
            }
            let at = (self.target % OUTBOUND_REGION_BYTES) as usize + Cell::reply_offset(reply.tag, self.cores);
            let dst = unsafe { self.window.as_ptr().add(at) };
            let start = self.durations.is_some().then(rdtsc);
            let mut words = reply.words();
            let t_out = if active_stamp {
                let t = rdtsc();
                finalize_card_stamp_words(&mut words, t, self.last_write_ticks);
                t
            } else {
                0
            };
            if self.wc {
                unsafe { store64_gathered(dst, &words) };
            } else {
                unsafe { store64_head_last(dst, &words) };
            }
            if active_stamp {
                self.last_write_ticks = rdtsc().wrapping_sub(t_out);
            }
            if let (Some(d), Some(start)) = (&mut self.durations, start) {
                unsafe { std::arch::asm!("dsb sy", options(nostack, preserves_flags)) };
                d.push(rdtsc() - start);
            }
            self.stored += 1;
            Ok(())
        }
    }

    /// How a core writes replies into the host's reply ring.
    enum RingWriter<'e> {
        Dma(DmaReplies<'e>),
        Store(StoreReplies),
    }

    /// What every core reads.
    #[repr(align(64))]
    struct CoreLive {
        os_cpu: AtomicU64,
        taken: AtomicU64,
        busy_polls: AtomicU64,
        empty_polls: AtomicU64,
        pauses: AtomicU64,
        partial: AtomicU64,
        corrupt: AtomicU64,
    }

    impl CoreLive {
        fn new() -> Self {
            CoreLive {
                os_cpu: AtomicU64::new(u64::MAX),
                taken: AtomicU64::new(0),
                busy_polls: AtomicU64::new(0),
                empty_polls: AtomicU64::new(0),
                pauses: AtomicU64::new(0),
                partial: AtomicU64::new(0),
                corrupt: AtomicU64::new(0),
            }
        }
    }
    /// Dedicated 128 B-aligned frontier word shared from ring core 0 to shadow core(s)
    /// (written ONLY when `shadow_cores > 0`, read identically in S1-S3 so its cost cancels).
    #[repr(align(128))]
    struct ShadowNext(AtomicU64);
    const _: () = assert!(size_of::<ShadowNext>() == 128 && align_of::<ShadowNext>() == 128);

    /// Per-shadow-core live counters (128 B aligned to prevent false sharing).
    #[repr(align(128))]
    struct ShadowLive {
        os_cpu: AtomicU64,
        polls: AtomicU64,
    }
    const _: () = assert!(size_of::<ShadowLive>() == 128 && align_of::<ShadowLive>() == 128);

    impl ShadowLive {
        fn new() -> Self {
            ShadowLive { os_cpu: AtomicU64::new(u64::MAX), polls: AtomicU64::new(0) }
        }
    }

    /// What every core reads.
    struct Shared<'a, 'e> {
        ring: &'a Memzone<'e>,
        pool: &'a Mempool<'e>,
        quit: &'a AtomicBool,
        args: &'a Args,
        me: Mac,
        /// Cores taking the BAR4 request ring, writing streams, and running shadow probes.
        ring_cores: usize,
        stream_cores: usize,
        shadow_cores: usize,
        tsc_hz: u64,
        empty_pause_ns: AtomicU64,
        empty_pause_ticks: AtomicU64,
        live: Vec<CoreLive>,
        shadow_live: Vec<ShadowLive>,
        shadow_next: ShadowNext,
    }
    impl Shared<'_, '_> {
        /// A word of the slot's control area.
        fn control(&self, offset: usize) -> &AtomicU64 {
            // SAFETY: an aligned word inside the ring memzone, which outlives self.
            unsafe { AtomicU64::from_ptr(self.ring.as_ptr().add(offset).cast()) }
        }
    }

    /// One core's counters at exit.
    #[derive(Default)]
    struct CoreStats {
        sdp: u64,
        pulled: u64,
        /// Cells taken without a reply (host -> card stream).
        sunk: u64,
        /// Cells written to the host unprompted (card -> host stream).
        sourced: u64,
        /// Counter ticks spent writing them (credit waits excluded).
        source_ticks: u64,
        stored: u64,
        dma_replies: u64,
        dma_copies: u64,
        dma_done: u64,
        dma_failed: u64,
        partial: u64,
        corrupt: u64,
        unroutable: u64,
        taken: u64,
        loops: u64,
        empty: u64,
        to_submit: Vec<u64>,
        to_complete: Vec<u64>,
        store_durations: Vec<u64>,
        /// `fetch` core: cells taken from the host request ring, DPI reads
        /// issued, and reads whose data was discarded (behind a missing cell,
        /// or already taken); triggered, the doorbells it took from the BAR4
        /// ring and the messages they carried inline.
        fetched: u64,
        fetch_reads: u64,
        fetch_stale: u64,
        doorbells: u64,
        inline: u64,
        /// `shadow` core: total shadow poll iterations.
        shadow_polls: u64,
    }

    /// Reflect ring core `ring_index`'s share of the BAR4 request ring until
    /// `quit`; with a `stream_index`, also write that share of card -> host
    /// streams.
    fn serve_ring<'e>(
        sh: &Shared<'_, 'e>,
        ring_index: usize,
        stream_index: Option<usize>,
        mut txq: TxQueue<'_, 'e>,
        mut writer: Option<RingWriter<'e>>,
    ) -> Result<CoreStats, Box<dyn Error>> {
        let base = sh.ring.as_ptr();
        let requests_iova = sh.ring.iova(); // the request cells, as the DPI engine sees them
        let stride = sh.ring_cores as u64;
        let mut next = ring_index as u64 + 1;
        let mut st = CoreStats::default();
        let mut burst: Burst = Burst::new();
        let mut streamed = 0;
        let live = &sh.live[ring_index];
        let os_cpu = unsafe { libc::sched_getcpu() };
        live.os_cpu.store(os_cpu as u64, Ordering::Release);
        let poll_mode: PollMode = sh.args.poll.into();
        let next_publish: NextPublish = sh.args.next_publish.into();
        let publish_shadow = sh.shadow_cores > 0 && ring_index == 0;
        let stamp = sh.args.exp_stamp;
        let stamp_sparse = sh.args.exp_stamp_sparse;
        let mut empty_on_next = 0u32;
        let mut first_poll_ts = 0u64;
        let mut tag_offset = 0u64;
        let mut loc_busy = 0u64;
        let mut loc_pauses = 0u64;

        while !sh.quit.load(Ordering::Relaxed) {
            let stream = sh.control(STREAM_OFFSET).load(Ordering::Acquire);
            if stream != streamed {
                streamed = stream;
                if let (Some(writer), Some(index), true) = (&mut writer, stream_index, stream != 0) {
                    let (written, ticks) = source(sh, index, writer, stream)?;
                    st.sourced += written;
                    st.source_ticks += ticks;
                }
            }
            st.loops += 1;
            let first = next;
            let mut reply_ring = None;
            let mut seen_at = None;
            for _ in 0..BATCH {
                let cell = match unsafe { Cell::poll_with_mode(base.add(Cell::offset(next)).cast(), next, poll_mode) } {
                    Slot::Empty => {
                        if stamp {
                            if empty_on_next == 0 && first_poll_ts == 0 {
                                let est_tag = next.wrapping_sub(tag_offset);
                                if !stamp_sparse || (est_tag & 63) < 8 {
                                    first_poll_ts = rdtsc();
                                }
                            }
                            empty_on_next = empty_on_next.saturating_add(1);
                        }
                        break;
                    }
                    Slot::Partial => {
                        if stamp && empty_on_next == 0 && first_poll_ts == 0 {
                            let est_tag = next.wrapping_sub(tag_offset);
                            if !stamp_sparse || (est_tag & 63) < 8 {
                                first_poll_ts = rdtsc();
                            }
                        }
                        st.partial += 1;
                        live.partial.store(st.partial, Ordering::Relaxed);
                        break;
                    }
                    Slot::Complete(cell) => cell,
                };
                if stamp {
                    tag_offset = next.wrapping_sub(cell.tag);
                }
                let active_stamp = stamp && (!stamp_sparse || (cell.tag & 63) < 8);
                let found = if active_stamp { rdtsc() } else { 0 };
                if sh.args.timing {
                    seen_at.get_or_insert_with(rdtsc);
                }
                let intact = cell.payload_matches(next);
                if !intact {
                    st.corrupt += 1;
                    live.corrupt.store(st.corrupt, Ordering::Relaxed);
                }
                let tsc = if intact { cell.tsc } else { 0 };
                let mut reply = Cell { tsc, ..cell };
                if active_stamp {
                    let waited = std::mem::replace(&mut empty_on_next, 0);
                    let first_ts = std::mem::replace(&mut first_poll_ts, 0);
                    let packed = pack_card_stamp_entry(found, first_ts, waited, os_cpu as u8, ring_index as u8);
                    reply.payload[..8].copy_from_slice(&packed[0].to_ne_bytes());
                    reply.payload[8..16].copy_from_slice(&packed[1].to_ne_bytes());
                    reply.payload[16..24].copy_from_slice(&packed[2].to_ne_bytes());
                } else if stamp {
                    empty_on_next = 0;
                    first_poll_ts = 0;
                }
                let ring_iova = *reply_ring.get_or_insert_with(|| sh.control(REPLY_RING_OFFSET).load(Ordering::Relaxed));
                match (&mut writer, ring_iova) {
                    (_, REPLY_NONE) => st.sunk += 1,
                    (_, REPLY_PULL) => {
                        sh.control(DONE_OFFSET + 8).store(tsc, Ordering::Relaxed);
                        sh.control(DONE_OFFSET).store(cell.tag, Ordering::Release);
                        st.pulled += 1;
                    }
                    (Some(RingWriter::Dma(dma)), ring) if ring != 0 => {
                        let request = (intact && !active_stamp).then(|| requests_iova + Cell::offset(next) as u64);
                        dma.send(&reply, request, ring)?
                    }
                    (Some(RingWriter::Store(store)), ring) if ring != 0 => store.send(&reply, ring, active_stamp)?,
                    (None, ring) if ring != 0 => st.unroutable += 1,
                    _ => {
                        let mut rframe = sh.pool.alloc().ok_or("mbuf pool exhausted")?;
                        let frame = rframe.append(REPLY_FRAME).ok_or("frame larger than mbuf")?;
                        write_request(frame, sh.args.peer, sh.me, cell.tag, tsc);
                        if burst.push(rframe).is_err() {
                            unreachable!("BATCH fits a burst");
                        }
                    }
                }
                next += stride;
            }
            if next == first {
                st.empty += 1;
                if ring_index == 0 && stamp && sh.args.sync.is_empty() {
                    sh.control(CLOCK_OFFSET).store(rdtsc(), Ordering::Relaxed);
                }
                let pause_ticks = sh.empty_pause_ticks.load(Ordering::Relaxed);
                if pause_ticks > 0 {
                    let t0 = rdtsc();
                    loop {
                        let now = rdtsc();
                        if ring_index == 0 && stamp && sh.args.sync.is_empty() {
                            sh.control(CLOCK_OFFSET).store(now, Ordering::Relaxed);
                        }
                        if now.wrapping_sub(t0) >= pause_ticks {
                            break;
                        }
                        std::hint::spin_loop();
                    }
                    loc_pauses += 1;
                }
                if (st.empty & 255) == 0 {
                    live.empty_polls.store(st.empty, Ordering::Relaxed);
                    live.pauses.store(loc_pauses, Ordering::Relaxed);
                }
                continue;
            }
            st.taken += (next - first) / stride;
            loc_busy += 1;
            live.taken.store(st.taken, Ordering::Relaxed);
            live.busy_polls.store(loc_busy, Ordering::Relaxed);
            live.empty_polls.store(st.empty, Ordering::Relaxed);
            live.pauses.store(loc_pauses, Ordering::Relaxed);
            if next_publish == NextPublish::Batch {
                sh.control(next_offset(ring_index)).store(next, Ordering::Relaxed);
            }
            if publish_shadow {
                sh.shadow_next.0.store(next, Ordering::Release);
            }
            while !burst.is_empty() {
                st.sdp += txq.tx(&mut burst) as u64;
            }
            if let Some(RingWriter::Dma(dma)) = &mut writer {
                dma.flush()?;
                let rang = dma.submitted < dma.issued;
                dma.submit()?;
                if let (Some(s), Some(seen), true) = (&mut dma.stamps, seen_at, rang) {
                    s.to_submit.push(rdtsc() - seen);
                }
                dma.reap();
            }
        }
        if poll_mode == PollMode::Wfe {
            clear_exclusive_monitor();
        }
        if next_publish == NextPublish::Exit {
            sh.control(next_offset(ring_index)).store(next, Ordering::Relaxed);
        }
        finish_writer(writer, &mut st);
        Ok(st)
    }

    /// A `shadow` core (MRV-236 S1-S3): reads the 128 B-aligned `SHADOW_NEXT`
    /// frontier word identically in every shadow arm, then tight-polls either
    /// its own 128 B-aligned private cache line (`private`) or ring cell
    /// `front + offset` (`front` / `front+N`). Takes no cells and sends no replies.
    fn serve_shadow(sh: &Shared, shadow_index: usize, mode: ShadowMode) -> Result<CoreStats, Box<dyn Error>> {
        #[repr(align(128))]
        struct PrivateLine([AtomicU64; 16]);

        let mut st = CoreStats::default();
        let base = sh.ring.as_ptr();
        let live = &sh.shadow_live[shadow_index];
        let os_cpu = unsafe { libc::sched_getcpu() };
        live.os_cpu.store(os_cpu as u64, Ordering::Release);
        let private = PrivateLine(std::array::from_fn(|_| AtomicU64::new(0)));
        let private_ptr = &private.0[0];

        let mut polls = 0u64;
        while !sh.quit.load(Ordering::Relaxed) {
            let front = sh.shadow_next.0.load(Ordering::Acquire).max(1);
            let val = match mode.target_seq(front) {
                None => private_ptr.load(Ordering::Acquire),
                Some(target_seq) => {
                    // SAFETY: 64 B-aligned head word of a live ring cell.
                    let cell_seq_ptr = unsafe { base.add(Cell::offset(target_seq)).cast::<u64>() };
                    unsafe { AtomicU64::from_ptr(cell_seq_ptr) }.load(Ordering::Acquire)
                }
            };
            std::hint::black_box(val);
            polls = polls.wrapping_add(1);
            if (polls & 255) == 0 {
                live.polls.store(polls, Ordering::Relaxed);
            }
        }
        live.polls.store(polls, Ordering::Relaxed);
        st.shadow_polls = polls;
        Ok(st)
    }

    /// Wait for a writer's last copies and record its counters.
    fn finish_writer(writer: Option<RingWriter>, st: &mut CoreStats) {
        match writer {
            Some(RingWriter::Dma(mut dma)) => {
                // Let the last copies finish before the stage memory goes
                // (bounded, in case the engine stopped).
                for _ in 0..10_000_000 {
                    if dma.completed == dma.issued || dma.failed > 0 {
                        break;
                    }
                    dma.reap();
                }
                (st.dma_replies, st.dma_copies) = (dma.replies, dma.issued);
                (st.dma_done, st.dma_failed) = (dma.completed, dma.failed);
                if let Some(s) = dma.stamps.take() {
                    (st.to_submit, st.to_complete) = (s.to_submit, s.to_complete);
                }
            }
            Some(RingWriter::Store(mut store)) => {
                st.stored = store.stored;
                st.store_durations = store.durations.take().unwrap_or_default();
            }
            None => {}
        }
    }

    /// A `source` core: write stream core `stream_index`'s share of every
    /// card -> host stream until `quit`.
    fn serve_source(sh: &Shared, stream_index: usize, mut writer: RingWriter) -> Result<CoreStats, Box<dyn Error>> {
        let mut st = CoreStats::default();
        let mut streamed = 0;
        while !sh.quit.load(Ordering::Relaxed) {
            let stream = sh.control(STREAM_OFFSET).load(Ordering::Acquire);
            if stream == streamed {
                std::hint::spin_loop();
                continue;
            }
            streamed = stream;
            if stream != 0 {
                let (written, ticks) = source(sh, stream_index, &mut writer, stream)?;
                st.sourced += written;
                st.source_ticks += ticks;
            }
        }
        finish_writer(Some(writer), &mut st);
        Ok(st)
    }

    /// Write stream core `index`'s share of an `n`-cell card -> host stream
    /// (cells `k` with `(k - 1) % stream cores == index`, see
    /// [`STREAM_OFFSET`]) into the host's stream ring, as fast as the host's
    /// [`TAKEN_OFFSET`] credit allows (`u64::MAX`: no limit, the ring is
    /// overwritten in circles). Returns the cells written and the counter
    /// ticks spent writing them (credit waits excluded).
    fn source(sh: &Shared, index: usize, writer: &mut RingWriter, n: u64) -> Result<(u64, u64), Box<dyn Error>> {
        // Set before the stream length, which the caller loaded with acquire.
        let ring = sh.control(STREAM_RING_OFFSET).load(Ordering::Relaxed);
        if ring == 0 {
            return Err(format!("stream of {n} cells without a host stream ring").into());
        }
        let stride = sh.stream_cores as u64;
        let (mut k, mut written, mut ticks) = (index as u64 + 1, 0, 0);
        while k <= n && !sh.quit.load(Ordering::Relaxed) {
            let taken = sh.control(TAKEN_OFFSET).load(Ordering::Relaxed);
            let limit = taken.saturating_add(REPLY_CELLS as u64).min(n);
            if k > limit {
                continue; // no credit
            }
            let start = rdtsc();
            let mut batch = 0;
            while k <= limit && batch < BATCH {
                let cell = Cell::new(k, k, 1);
                match writer {
                    RingWriter::Dma(dma) => dma.send(&cell, None, ring)?,
                    RingWriter::Store(store) => store.send(&cell, ring, false)?,
                }
                k += stride;
                batch += 1;
            }
            if let RingWriter::Dma(dma) = writer {
                dma.flush()?;
                dma.submit()?;
                dma.reap();
            }
            ticks += rdtsc() - start;
            written += batch as u64;
        }
        Ok((written, ticks))
    }

    /// The `fetch` core's reader of the host request ring: DPI reads copy
    /// `chunk` cells at a time from the host ring into the same place in a
    /// local mirror, `depth` reads in flight.
    struct Fetcher<'e> {
        dma: HostDma<'e>,
        mirror: Memzone<'e>,
        chunk: u64,
        depth: usize,
    }

    /// A `fetch` core: whenever the host publishes a request ring, read it
    /// with DPI and take its cells in order until the host withdraws it; if
    /// the host also published a reply ring and the core has a writer, reply
    /// to each request like a ring core (one core: the reply ring is not
    /// split).
    ///
    /// Reads cover one chunk at most (a ring never wraps inside one) and
    /// complete in order. A read is used from the next request expected up
    /// to the first one not (completely) written yet; reading resumes there,
    /// and reads already in flight behind it are discarded as they complete.
    /// What is read depends on [`FETCH_TRIGGER_OFFSET`]: speculatively, whole
    /// chunks up to `depth` chunks ahead, whether published or not (so a
    /// reader that has caught up with the host keeps re-reading the
    /// frontier); triggered, only the requests the doorbells in the BAR4 ring
    /// say are published (taking those doorbells and their inline messages
    /// too), so nothing is read twice unless the host's writes were not
    /// visible yet.
    fn serve_fetch(sh: &Shared, mut f: Fetcher, mut writer: Option<RingWriter>) -> Result<CoreStats, Box<dyn Error>> {
        let mut st = CoreStats::default();
        let mirror = f.mirror.as_ptr();
        let mirror_iova = f.mirror.iova();
        let bar = sh.ring.as_ptr();
        let ahead = f.depth as u64 * f.chunk;
        // Requests `first..=last` of each read in flight, oldest first.
        let mut in_flight: VecDeque<(u64, u64)> = VecDeque::with_capacity(f.depth);
        // Next request expected, next to read, and published so far.
        let (mut ring, mut next, mut issue, mut published) = (0u64, 1u64, 1u64, u64::MAX);
        let mut triggered = false;
        let mut doorbell = sh.control(next_offset(0)).load(Ordering::Relaxed);
        while !sh.quit.load(Ordering::Relaxed) {
            let current = sh.control(FETCH_RING_OFFSET).load(Ordering::Acquire);
            if current != ring {
                // A new host run (or none): reads still in flight belong to
                // the old ring; let them land before reusing the mirror.
                while !in_flight.is_empty() {
                    let (n, failed) = f.dma.completed(f.depth as u16);
                    if failed {
                        return Err("a DPI read of the host request ring failed".into());
                    }
                    in_flight.drain(..usize::from(n));
                }
                // SAFETY: the mirror memzone is ours and that long; no read
                // is in flight.
                unsafe { mirror.write_bytes(0, FETCH_CELLS * 64) };
                (ring, next, issue) = (current, 1, 1);
                triggered = sh.control(FETCH_TRIGGER_OFFSET).load(Ordering::Relaxed) != 0 && sh.ring_cores == 0;
                published = if triggered { 0 } else { u64::MAX };
                sh.control(FETCH_NEXT_OFFSET).store(next, Ordering::Release);
            }
            if ring == 0 {
                std::hint::spin_loop();
                continue;
            }
            if triggered {
                let first_doorbell = doorbell;
                for _ in 0..BATCH {
                    // SAFETY: a 64 B-aligned cell of the BAR4 ring, which only
                    // this core takes (there are no ring cores).
                    let at = unsafe { bar.add(Cell::offset(doorbell)) }.cast::<Cell>();
                    match unsafe { Cell::poll(at, doorbell) } {
                        Slot::Complete(cell) if cell.payload_matches(doorbell) => {
                            st.inline += u64::from(cell.tag != 0);
                            published = published.max(cell.tsc);
                            doorbell += 1;
                        }
                        Slot::Empty => break,
                        _ => {
                            st.partial += 1;
                            break;
                        }
                    }
                }
                if doorbell > first_doorbell {
                    st.doorbells += doorbell - first_doorbell;
                    sh.control(next_offset(0)).store(doorbell, Ordering::Relaxed);
                }
            }
            let mut rang = false;
            while in_flight.len() < f.depth && issue <= published && issue < next + ahead {
                // Up to the end of the chunk holding `issue`, and what is published.
                let last = ((issue - 1) / f.chunk + 1) * f.chunk;
                let last = last.min(published);
                let off = Cell::fetch_offset(issue) as u64;
                let bytes = u32::try_from((last - issue + 1) * 64).expect("a chunk fits a copy");
                if !f.dma.copy(ring + off, mirror_iova + off, bytes)? {
                    break;
                }
                in_flight.push_back((issue, last));
                issue = last + 1;
                rang = true;
                st.fetch_reads += 1;
            }
            if rang {
                f.dma.submit()?;
            }
            let (n, failed) = f.dma.completed(f.depth as u16);
            if failed {
                return Err("a DPI read of the host request ring failed".into());
            }
            let first = next;
            // Set before the first request, so read once it was taken.
            let replies = sh.control(REPLY_RING_OFFSET).load(Ordering::Relaxed);
            let replies = (![0, REPLY_NONE, REPLY_PULL].contains(&replies)).then_some(replies);
            let replying = replies.filter(|_| writer.is_some());
            for (from, last) in in_flight.drain(..usize::from(n)) {
                if from > next || last < next {
                    st.fetch_stale += 1; // behind a request not written yet, or already taken
                    continue;
                }
                while next <= last {
                    // SAFETY: a 64 B-aligned cell inside the mirror, which no
                    // read in flight targets before its completion.
                    let at = unsafe { mirror.add(Cell::fetch_offset(next)) }.cast::<Cell>();
                    let cell = match unsafe { Cell::poll(at, next) } {
                        Slot::Complete(cell) if cell.tag == next && cell.payload_matches(next) => cell,
                        Slot::Empty => break,
                        _ => {
                            st.partial += 1;
                            break;
                        }
                    };
                    if let Some(ring) = replying {
                        match writer.as_mut().expect("replying needs a writer") {
                            // The mirror cell stays as it is while the copy
                            // runs: a later read of its chunk brings the same
                            // bytes, as the host rewrites it only after this
                            // reply.
                            RingWriter::Dma(dma) => {
                                dma.send(&cell, Some(mirror_iova + Cell::fetch_offset(next) as u64), ring)?
                            }
                            RingWriter::Store(store) => store.send(&cell, ring, false)?,
                        }
                    }
                    next += 1;
                }
                if next <= last {
                    issue = next; // stopped at a request not written yet: read from it again
                }
            }
            if let Some(RingWriter::Dma(dma)) = &mut writer {
                dma.flush()?;
                dma.submit()?;
                dma.reap();
            }
            if next > first {
                let taken = next - first;
                st.fetched += taken;
                match (replies, replying) {
                    (None, _) => st.sunk += taken,
                    (Some(_), None) => st.unroutable += taken, // host wants replies, we have no writer
                    _ => {}
                }
                sh.control(FETCH_NEXT_OFFSET).store(next, Ordering::Release);
            }
        }
        // Reads still in flight must land before the mirror goes.
        for _ in 0..10_000_000 {
            if in_flight.is_empty() {
                break;
            }
            let (n, _) = f.dma.completed(f.depth as u16);
            in_flight.drain(..usize::from(n));
        }
        finish_writer(writer, &mut st);
        Ok(st)
    }

    /// `p50 / p99 / mean` of `ticks` in ns.
    fn summary(ticks: &[u64], hz: u64) -> String {
        if ticks.is_empty() {
            return "no samples".into();
        }
        let mut sorted = ticks.to_vec();
        sorted.sort_unstable();
        let ns = |t: u64| t as f64 * 1e9 / hz as f64;
        let q = |q: f64| ns(sorted[((sorted.len() - 1) as f64 * q) as usize]);
        let mean = sorted.iter().sum::<u64>() as f64 / sorted.len() as f64;
        format!("p50 {:.0} p99 {:.0} mean {:.0} ns ({} samples)", q(0.5), q(0.99), ns(1) * mean, sorted.len())
    }

    /// The reply/stream writer of a ring or source core: its own PEM outbound
    /// region (`--store`) or the `index`-th `--dma` VF, or none. `layout` is
    /// the number of card cores sharing its host ring.
    fn make_writer<'e>(
        eal: &'e Eal,
        args: &Args,
        core: usize,
        index: usize,
        layout: usize,
        host: PcieFunction,
        socket: i32,
    ) -> Result<Option<RingWriter<'e>>, Box<dyn Error>> {
        if args.store {
            if args.pem != 0 {
                return Err("--store drives PEM0 only".into());
            }
            if index >= STORE_REGIONS {
                return Err(format!("--store takes one of the top {STORE_REGIONS} outbound regions per core").into());
            }
            let region = OUTBOUND_REGIONS - 1 - index;
            let window = OutboundWindow::claim(region, args.host_pf, args.host_vf, args.store_wc)?;
            println!(
                "inline_reflector: core {core} stores to the host through PEM0 outbound region {region} (was {:#x}) as \
                 host pf {} vf {}, {}",
                window.previous(),
                args.host_pf,
                args.host_vf,
                if args.store_wc { "write-combining" } else { "Device-nGnRnE" }
            );
            return Ok(Some(RingWriter::Store(StoreReplies {
                window,
                wc: args.store_wc,
                cores: layout,
                target: 0,
                stored: 0,
                durations: args.timing.then(|| Vec::with_capacity(1 << 22)),
                last_write_ticks: 0,
            })));
        }
        let Some(name) = args.dma.get(index) else { return Ok(None) };
        let dma = HostDma::start(eal, name, DMA_DESC, host, HostDir::ToHost)?;
        let stage = Memzone::reserve_contiguous(eal, &format!("dma_stage{core}"), STAGE_CELLS * 64, socket, 64)?;
        println!("inline_reflector: core {core} DMAs to the host via {name} as host function {host:?}");
        let stamps = args.timing.then(|| Stamps {
            submitted: vec![0; STAGE_CELLS],
            to_submit: Vec::with_capacity(1 << 22),
            to_complete: Vec::with_capacity(1 << 22),
        });
        let source = match args.dma_source {
            DmaSource::Auto if layout == 1 => DmaSource::Request,
            DmaSource::Auto => DmaSource::Stage,
            s => s,
        };
        Ok(Some(RingWriter::Dma(DmaReplies {
            dma,
            stage,
            source,
            cores: layout,
            coalesce: args.dma_coalesce,
            run: None,
            issued: 0,
            submitted: 0,
            completed: 0,
            failed: 0,
            replies: 0,
            staged: 0,
            unstaged: 0,
            in_flight: VecDeque::with_capacity(usize::from(DMA_DESC)),
            stamps,
        })))
    }

    /// What one lcore runs.
    enum Plan<'p, 'e> {
        Ring { index: usize, stream: Option<usize>, txq: TxQueue<'p, 'e>, writer: Option<RingWriter<'e>> },
        Source { index: usize, writer: RingWriter<'e> },
        Fetch { fetcher: Fetcher<'e>, writer: Option<RingWriter<'e>> },
        Shadow { index: usize, mode: ShadowMode },
    }
    /// Other processes running an `inline_reflector*` binary.
    fn other_reflectors() -> Vec<u32> {
        let me = std::process::id();
        let Ok(dir) = std::fs::read_dir("/proc") else { return Vec::new() };
        dir.flatten()
            .filter_map(|e| e.file_name().to_str()?.parse::<u32>().ok())
            .filter(|&pid| pid != me)
            .filter(|pid| {
                let cmdline = std::fs::read(format!("/proc/{pid}/cmdline")).unwrap_or_default();
                let argv0 = cmdline.split(|&b| b == 0).next().unwrap_or_default();
                let name = argv0.rsplit(|&b| b == b'/').next().unwrap_or_default();
                name.starts_with(b"inline_reflector")
            })
            .collect()
    }

    /// Before EAL: unless another reflector runs (it owns the slot), put
    /// back what one killed with SIGKILL left changed.
    fn recover(restore_only: bool) -> Result<(), Box<dyn Error>> {
        let others = other_reflectors();
        if !others.is_empty() {
            return Err(format!(
                "another inline_reflector runs (pid {others:?}): leaving BAR4 slot {SLOT} and the outbound regions alone"
            )
            .into());
        }
        let changed = restore_defaults(SLOT)?;
        for c in &changed {
            println!("inline_reflector: restored {c} (left by a reflector that did not exit cleanly)");
        }
        if restore_only && changed.is_empty() {
            println!(
                "inline_reflector: nothing to restore (BAR4 slot {SLOT} and outbound regions {}-{} hold their defaults)",
                OUTBOUND_REGIONS - STORE_REGIONS,
                OUTBOUND_REGIONS - 1
            );
        }
        Ok(())
    }

    /// Pre-EAL EL0 instruction probe (`--probe-el0-ops`) for MRV-236 `--smoke`:
    /// verifies `dc civac` + `dsb ish` and `ldaxr` + `wfe` + `clrex` execute cleanly
    /// in EL0 without SIGILL and wake on a cross-thread store to the watched cell.
    fn run_el0_probe() -> Result<(), Box<dyn Error>> {
        let cell = Arc::new(std::cell::UnsafeCell::new(Cell::new(0, 0, 0)));
        let ptr = cell.get();
        // 1. Test PollMode::Civac on Empty, then Complete.
        match unsafe { Cell::poll_with_mode(ptr, 1, PollMode::Civac) } {
            Slot::Empty => {}
            _ => return Err("el0 probe: civac expected Empty".into()),
        }
        unsafe { Cell::new(1, 1, 11).publish(ptr) };
        match unsafe { Cell::poll_with_mode(ptr, 1, PollMode::Civac) } {
            Slot::Complete(c) if c.seq == 1 && c.payload_matches(1) => {}
            _ => return Err("el0 probe: civac expected Complete".into()),
        }
        // 2. Test PollMode::Wfe: arm monitor, wake via helper thread store, then Complete + clrex.
        let cell_addr = ptr as usize;
        let waker = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(2));
            // SAFETY: cell outlives this thread (joined below).
            unsafe { Cell::new(2, 2, 22).publish(cell_addr as *mut Cell) };
        });
        let mut wfe_empties = 0u64;
        let start = Instant::now();
        loop {
            match unsafe { Cell::poll_with_mode(ptr, 2, PollMode::Wfe) } {
                Slot::Empty => {
                    wfe_empties += 1;
                    if start.elapsed() > Duration::from_secs(2) {
                        clear_exclusive_monitor();
                        let _ = waker.join();
                        return Err("el0 probe: wfe timed out waiting for publish".into());
                    }
                }
                Slot::Partial => {}
                Slot::Complete(c) if c.seq == 2 && c.payload_matches(2) => break,
                Slot::Complete(_) => {
                    clear_exclusive_monitor();
                    let _ = waker.join();
                    return Err("el0 probe: wfe saw corrupt cell".into());
                }
            }
        }
        clear_exclusive_monitor();
        waker.join().map_err(|_| "el0 probe: waker panicked")?;
        println!("OK el0_ops=civac,wfe wfe_empties={wfe_empties}");
        Ok(())
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        if std::env::args().skip(1).any(|a| a == "--probe-el0-ops") {
            return run_el0_probe();
        }
        let restore_only = std::env::args().skip(1).any(|a| a == "--restore");
        recover(restore_only)?;
        if restore_only {
            return Ok(());
        }
        let (eal, app_args) = Eal::init(std::env::args())?;
        let args = Args::parse_from(app_args);
        let cores = eal.lcores();
        let roles = if args.roles.is_empty() { vec![Role::Ring; cores] } else { args.roles.clone() };
        let count = |r: Role| roles.iter().filter(|&&x| x == r).count();
        let (ring_cores, source_cores, fetch_cores, shadow_cores) =
            (count(Role::Ring), count(Role::Source), count(Role::Fetch), count(Role::Shadow));
        let stream_cores = if source_cores > 0 { source_cores } else { ring_cores };
        validate_reflector_layout(
            cores,
            roles.len(),
            ring_cores,
            source_cores,
            fetch_cores,
            shadow_cores,
            args.shadow,
            args.dma.len(),
            args.fetch_dma.is_some(),
            args.fetch_chunk,
            args.fetch_depth,
        )?;
        // Before any register changes: a busy or missing control address
        // fails here.
        let listener =
            TcpListener::bind(&args.control).map_err(|e| format!("control address --control {}: {e}", args.control))?;
        let quit = Arc::new(AtomicBool::new(false));
        let q = quit.clone();
        ctrlc::set_handler(move || q.store(true, Ordering::Relaxed))?;

        let socket = Port::socket_of(&eal, PORT);
        let pool = Mempool::new(&eal, "inline_reflector", 32767, socket)?;
        // One TX queue per ring core: the SDP delivers card TX queue i to host
        // VF RX queue i, and the host opens one per ring core.
        let mut port = Port::start(&eal, PORT, &pool, ring_cores.max(1) as u16, DESC)?;
        let me = Mac(port.mac());
        let host = PcieFunction { core: args.pem, pf: args.host_pf, vf: args.host_vf };
        let mut txqs = port.tx_queues().into_iter();
        let (mut ring_index, mut stream_index, mut shadow_index, mut writer_index) = (0, 0, 0, 0);
        let mut plans = Vec::with_capacity(cores);
        for (core, &role) in roles.iter().enumerate() {
            if role == Role::Shadow {
                let mode = args.shadow.expect("checked above");
                println!("inline_reflector: core {core} runs shadow role ({mode})");
                plans.push(Plan::Shadow { index: shadow_index, mode });
                shadow_index += 1;
                continue;
            }
            // Ring cores lay their replies out by ring core, source cores
            // their streams by stream core; the fetch core is alone.
            let layout = match role {
                Role::Ring => ring_cores,
                Role::Source => stream_cores,
                Role::Fetch => 1,
                Role::Shadow => unreachable!(),
            };
            let writer = make_writer(&eal, &args, core, writer_index, layout, host, socket)?;
            writer_index += 1;
            if role == Role::Fetch {
                let name = args.fetch_dma.as_deref().expect("checked above");
                let dma = HostDma::start(&eal, name, DMA_DESC, host, HostDir::FromHost)?;
                let mirror = Memzone::reserve_contiguous(&eal, "fetch_mirror", FETCH_CELLS * 64, socket, 64)?;
                println!(
                    "inline_reflector: core {core} fetches host requests via {name} as host function {host:?}, {} cells \
                     per read, {} reads in flight",
                    args.fetch_chunk, args.fetch_depth
                );
                let fetcher = Fetcher { dma, mirror, chunk: args.fetch_chunk, depth: args.fetch_depth };
                plans.push(Plan::Fetch { fetcher, writer });
                continue;
            }
            if role == Role::Ring {
                let stream = (source_cores == 0).then_some(ring_index);
                let txq = txqs.next().expect("one TX queue per ring core");
                plans.push(Plan::Ring { index: ring_index, stream, txq, writer });
                ring_index += 1;
            } else {
                let writer = writer.ok_or("a `source` core needs --dma or --store")?;
                plans.push(Plan::Source { index: stream_index, writer });
                stream_index += 1;
            }
        }

        let ring = Memzone::reserve_contiguous(&eal, "inline_ring", SLOT_BYTES, socket, SLOT_BYTES as u32)?;
        // SAFETY: the memzone is ours and SLOT_BYTES long.
        unsafe { ring.as_ptr().write_bytes(0, SLOT_BYTES) };
        let pa = ring.phys_addr().ok_or("rte_mem_virt2phy failed (run as root)")?;
        let tsc_hz = eal.tsc_hz();
        let init_pause_ticks = if args.empty_pause_ns > 0 {
            ((args.empty_pause_ns as u128 * tsc_hz as u128 + 500_000_000) / 1_000_000_000) as u64
        } else {
            0
        };
        let live = (0..ring_cores.max(1)).map(|_| CoreLive::new()).collect();
        let shadow_live = (0..shadow_cores).map(|_| ShadowLive::new()).collect();
        let shared = Shared {
            ring: &ring,
            pool: &pool,
            quit: &quit,
            args: &args,
            me,
            ring_cores,
            stream_cores,
            shadow_cores,
            tsc_hz,
            empty_pause_ns: AtomicU64::new(args.empty_pause_ns),
            empty_pause_ticks: AtomicU64::new(init_pause_ticks),
            live,
            shadow_live,
            shadow_next: ShadowNext(AtomicU64::new(1)),
        };
        let sync_listener = if !args.sync.is_empty() {
            let l = TcpListener::bind(&args.sync).map_err(|e| format!("sync address --sync {}: {e}", args.sync))?;
            l.set_nonblocking(true)?;
            Some(l)
        } else {
            None
        };

        // Declared after `ring`, so it is dropped (slot restored) before the memory is freed.
        let window = Bar4Window::claim(SLOT, pa)?;
        shared.control(CORES_OFFSET).store(ring_cores as u64, Ordering::Relaxed);
        shared.control(STREAM_CORES_OFFSET).store(stream_cores as u64, Ordering::Relaxed);
        // Without ring cores, a triggered fetch core takes the BAR4 ring
        // (its doorbells) as ring core 0 would.
        for index in 0..ring_cores.max(1) {
            shared.control(next_offset(index)).store(index as u64 + 1, Ordering::Relaxed);
        }
        shared.control(FETCH_NEXT_OFFSET).store(1, Ordering::Relaxed);
        shared.control(READY_OFFSET).store(READY, Ordering::Release);
        println!(
            "inline_reflector: BAR4 slot {SLOT} -> pa {pa:#x} (was {:#x}); roles {roles:?}; SDP replies to {} from {me}; \
             hosts lease the slot at {}",
            window.previous(),
            args.peer,
            args.control
        );

        let results = std::thread::scope(|scope| -> Result<_, Box<dyn Error>> {
            let server = lease::serve(scope, listener, shared.control(NONCE_OFFSET))?;
            let sh_ref = &shared;
            let sync_handle = sync_listener.map(|sl| scope.spawn(move || serve_sync(sl, sh_ref)));
            let results = eal.on_each_lcore(plans, |_, plan| {
                match plan {
                    Plan::Ring { index, stream, txq, writer } => serve_ring(&shared, index, stream, txq, writer),
                    Plan::Source { index, writer } => serve_source(&shared, index, writer),
                    Plan::Fetch { fetcher, writer } => serve_fetch(&shared, fetcher, writer),
                    Plan::Shadow { index, mode } => serve_shadow(&shared, index, mode),
                }
                .map_err(|e| e.to_string())
            });
            shared.control(READY_OFFSET).store(0, Ordering::Release);
            server.close();
            if let Some(h) = sync_handle {
                let _ = h.join();
            }
            Ok(results)
        })?;

        let mut stats = Vec::with_capacity(cores);
        for (core, r) in results.into_iter().enumerate() {
            stats.push(r.map_err(|e| format!("core {core}: {e}"))?);
        }
        let sum = |f: fn(&CoreStats) -> u64| stats.iter().map(f).sum::<u64>();
        println!(
            "reflected {} (sdp {}, pull {}, stored {}, dma {} in {} copies: completed {}, failed {}), partial cells \
             seen {}, corrupt payloads {}, no ring writer for {}; streams: sunk {}, sourced {} (included in \
             stored/dma)",
            sum(|s| s.sdp + s.pulled + s.stored + s.dma_replies - s.sourced),
            sum(|s| s.sdp),
            sum(|s| s.pulled),
            sum(|s| s.stored),
            sum(|s| s.dma_replies),
            sum(|s| s.dma_copies),
            sum(|s| s.dma_done),
            sum(|s| s.dma_failed),
            sum(|s| s.partial),
            sum(|s| s.corrupt),
            sum(|s| s.unroutable),
            sum(|s| s.sunk),
            sum(|s| s.sourced),
        );
        if fetch_cores > 0 {
            println!(
                "fetched {} host requests with {} DPI reads ({} discarded behind a missing cell or already taken); \
                 {} doorbells carrying {} messages inline",
                sum(|s| s.fetched),
                sum(|s| s.fetch_reads),
                sum(|s| s.fetch_stale),
                sum(|s| s.doorbells),
                sum(|s| s.inline)
            );
        }
        // Cells per iteration that found any: near 1 means a core keeps up with
        // the host; near BATCH means requests queue up behind it. (Idle time
        // between host runs does not affect it.)
        let per_core: Vec<String> = stats
            .iter()
            .zip(&roles)
            .map(|(s, role)| match role {
                Role::Ring => format!(
                    "ring {} cells, {:.1} per busy poll",
                    s.taken,
                    s.taken as f64 / (s.loops - s.empty).max(1) as f64
                ),
                Role::Source => format!("source {} cells", s.sourced),
                Role::Fetch => format!("fetch {} cells", s.fetched),
                Role::Shadow => format!("shadow {} polls", s.shadow_polls),
            })
            .collect();
        println!("per core: {}", per_core.join("; "));
        if sum(|s| s.sourced) > 0 {
            let hz = eal.tsc_hz() as f64;
            let rates: Vec<String> = stats
                .iter()
                .map(|s| format!("{:.1} M/s", s.sourced as f64 * hz / s.source_ticks.max(1) as f64 / 1e6))
                .collect();
            println!("stream writing rate per core (credit waits excluded): {}", rates.join("; "));
        }
        if args.timing {
            let hz = eal.tsc_hz();
            let all = |f: fn(&CoreStats) -> &Vec<u64>| stats.iter().flat_map(|s| f(s).iter().copied()).collect::<Vec<_>>();
            println!("timing: counter {} MHz", hz / 1_000_000);
            if args.store {
                println!("timing: one reply's stores until complete (DSB): {}", summary(&all(|s| &s.store_durations), hz));
            } else {
                println!("timing: cell seen -> DMA doorbell rung: {}", summary(&all(|s| &s.to_submit), hz));
                println!("timing: DMA doorbell -> completion reaped: {}", summary(&all(|s| &s.to_complete), hz));
                for (core, s) in stats.iter().enumerate() {
                    println!("timing: core {core} doorbell -> completion: {}", summary(&s.to_complete, hz));
                }
            }
        }
        Ok(())
    }

    // ---- MRV-75 Card TAD + per-core ARMv8 PMU sync server ----
    #[repr(C)]
    #[derive(Default)]
    struct PerfEventAttr {
        type_: u32,
        size: u32,
        config: u64,
        sample_period_or_freq: u64,
        sample_type: u64,
        read_format: u64,
        flags: u64,
        wakeup_events_or_watermark: u32,
        bp_type: u32,
        config1: u64,
        config2: u64,
        branch_sample_type: u64,
        sample_regs_user: u64,
        sample_stack_user: u32,
        clockid: i32,
        sample_regs_intr: u64,
        aux_watermark: u32,
        sample_max_stack: u16,
        __reserved_2: u16,
        aux_sample_size: u32,
        __reserved_3: u32,
        sig_data: u64,
        config3: u64,
    }

    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    const TAD_EVENTS: &[(&str, u64)] = &[
        ("tad_req_msh_in_any", 0x01),
        ("tad_dat_msh_in_any", 0x09),
        ("tad_req_msh_out_dss_wr", 0x0d),
        ("tad_rsp_msh_out_retry_exlmn", 0x10),
        ("tad_snp_msh_out_any", 0x14),
        ("tad_alloc_any", 0x1c),
        ("tad_hit_any", 0x1f),
        ("tad_req_occ", 0x24),
    ];

    const CORE_EVENTS: &[(&str, u64)] = &[
        ("l2d_cache_refill", 0x17),
        ("l1d_cache_refill", 0x03),
        ("ll_cache_rd", 0x36),
        ("cpu_cycles", 0x11),
        ("inst_retired", 0x08),
    ];
    /// Optional Neoverse N2 per-core event (MRV-236): opened opportunistically if supported by PMU.
    const OPTIONAL_CORE_EVENTS: &[(&str, u64)] = &[("l2d_cache_inval", 0x58)];
    struct PerfCounter {
        name: String,
        fd: RawFd,
    }

    #[derive(Clone, Default)]
    struct CounterSample {
        name: String,
        value: u64,
        enabled_ns: u64,
        running_ns: u64,
    }

    impl CounterSample {
        fn delta_from(&self, earlier: &Self) -> Self {
            CounterSample {
                name: self.name.clone(),
                value: self.value.saturating_sub(earlier.value),
                enabled_ns: self.enabled_ns.saturating_sub(earlier.enabled_ns),
                running_ns: self.running_ns.saturating_sub(earlier.running_ns),
            }
        }
        fn pct(&self) -> f64 {
            if self.enabled_ns == 0 {
                0.0
            } else {
                (self.running_ns as f64 / self.enabled_ns as f64) * 100.0
            }
        }
    }

    impl PerfCounter {
        fn open(name: impl Into<String>, type_: u32, config: u64, cpu: i32) -> io::Result<Self> {
            let name = name.into();
            let attr = PerfEventAttr {
                type_,
                size: size_of::<PerfEventAttr>() as u32,
                config,
                read_format: PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING,
                flags: PERF_ATTR_FLAG_DISABLED,
                ..Default::default()
            };
            let fd = unsafe {
                libc::syscall(
                    libc::SYS_perf_event_open,
                    &raw const attr,
                    -1i32 as libc::pid_t,
                    cpu as libc::c_int,
                    -1i32 as libc::c_int,
                    0 as libc::c_ulong,
                )
            };
            if fd < 0 {
                return Err(io::Error::last_os_error());
            }
            let c = PerfCounter { name, fd: fd as RawFd };
            let rc = unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) };
            if rc != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> CounterSample {
            let mut buf = [0u64; 3];
            let n = unsafe {
                libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>())
            };
            assert_eq!(n, size_of::<[u64; 3]>() as isize, "short read on {}", self.name);
            CounterSample { name: self.name.clone(), value: buf[0], enabled_ns: buf[1], running_ns: buf[2] }
        }
    }

    impl Drop for PerfCounter {
        fn drop(&mut self) {
            unsafe {
                libc::ioctl(self.fd, PERF_EVENT_IOC_DISABLE, 0);
                libc::close(self.fd);
            }
        }
    }

    fn read_sysfs_u32(path: &str) -> Option<u32> {
        let s = fs::read_to_string(path).ok()?;
        let first = s.trim().split(&[',', '-'][..]).next()?;
        first.parse::<u32>().ok()
    }

    struct CardPmu {
        tad: Vec<PerfCounter>,
        extra: Vec<PerfCounter>,
        cores: Vec<(usize, u64, Vec<PerfCounter>)>,
        shadows: Vec<(usize, u64, Vec<PerfCounter>)>,
        l2d_cache_inval_supported: bool,
    }

    impl CardPmu {
        fn open_core_events(arm_type: u32, cpu: i32, tag: &str) -> Result<(Vec<PerfCounter>, bool), String> {
            let mut evs = Vec::with_capacity(CORE_EVENTS.len() + OPTIONAL_CORE_EVENTS.len());
            for &(name, cfg) in CORE_EVENTS {
                evs.push(PerfCounter::open(name, arm_type, cfg, cpu).map_err(|e| format!("{tag} {name}: {e}"))?);
            }
            let mut inval_ok = false;
            for &(name, cfg) in OPTIONAL_CORE_EVENTS {
                if let Ok(c) = PerfCounter::open(name, arm_type, cfg, cpu) {
                    evs.push(c);
                    inval_ok = true;
                }
            }
            Ok((evs, inval_ok))
        }

        fn open(sh: &Shared) -> Result<Self, String> {
            let tad_type = read_sysfs_u32("/sys/bus/event_source/devices/tad/type")
                .ok_or_else(|| "cannot read tad PMU type".to_string())?;
            let tad_cpu = read_sysfs_u32("/sys/bus/event_source/devices/tad/cpumask").unwrap_or(0) as i32;
            let mut tad = Vec::with_capacity(TAD_EVENTS.len());
            for &(name, cfg) in TAD_EVENTS {
                tad.push(PerfCounter::open(name, tad_type, cfg, tad_cpu).map_err(|e| format!("tad {name}: {e}"))?);
            }
            let arm_type = read_sysfs_u32("/sys/bus/event_source/devices/armv8_pmuv3/type")
                .ok_or_else(|| "cannot read armv8_pmuv3 type".to_string())?;
            let mut cores = Vec::with_capacity(sh.ring_cores);
            let mut inval_supported = false;
            for i in 0..sh.ring_cores {
                let mut cpu = u64::MAX;
                for _ in 0..100 {
                    cpu = sh.live[i].os_cpu.load(Ordering::Acquire);
                    if cpu != u64::MAX {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(5));
                }
                if cpu == u64::MAX {
                    return Err(format!("ring core {i} did not publish os_cpu"));
                }
                let (evs, ok) = Self::open_core_events(arm_type, cpu as i32, &format!("core{i}"))?;
                inval_supported |= ok;
                cores.push((i, cpu, evs));
            }
            let mut shadows = Vec::with_capacity(sh.shadow_cores);
            for i in 0..sh.shadow_cores {
                let mut cpu = u64::MAX;
                for _ in 0..100 {
                    cpu = sh.shadow_live[i].os_cpu.load(Ordering::Acquire);
                    if cpu != u64::MAX {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(5));
                }
                if cpu == u64::MAX {
                    return Err(format!("shadow core {i} did not publish os_cpu"));
                }
                let (evs, ok) = Self::open_core_events(arm_type, cpu as i32, &format!("shadow{i}"))?;
                inval_supported |= ok;
                shadows.push((i, cpu, evs));
            }
            let mut extra = Vec::with_capacity(sh.args.extra_pmu.len());
            for spec in &sh.args.extra_pmu {
                let (dev, name, cfg) = parse_extra_pmu_spec(spec)?;
                let pmu_type = read_sysfs_u32(&format!("/sys/bus/event_source/devices/{dev}/type"))
                    .ok_or_else(|| format!("cannot read extra PMU {dev} type"))?;
                let pmu_cpu = read_sysfs_u32(&format!("/sys/bus/event_source/devices/{dev}/cpumask")).unwrap_or(0) as i32;
                let full_name = format!("{dev}.{name}");
                extra.push(
                    PerfCounter::open(full_name.clone(), pmu_type, cfg, pmu_cpu)
                        .map_err(|e| format!("extra {full_name}: {e}"))?,
                );
            }
            Ok(CardPmu { tad, extra, cores, shadows, l2d_cache_inval_supported: inval_supported })
        }
    }

    struct CardSnap {
        at: Instant,
        tad: Vec<CounterSample>,
        extra: Vec<CounterSample>,
        cores: Vec<(usize, u64, Vec<CounterSample>, [u64; 6])>,
        shadows: Vec<(usize, u64, Vec<CounterSample>, u64)>,
    }

    fn snap_card(pmu: &CardPmu, sh: &Shared, is_start: bool) -> CardSnap {
        let snap_sw = || -> Vec<(usize, u64, [u64; 6])> {
            pmu.cores
                .iter()
                .map(|(idx, cpu, _)| {
                    let l = &sh.live[*idx];
                    (
                        *idx,
                        *cpu,
                        [
                            l.taken.load(Ordering::Relaxed),
                            l.busy_polls.load(Ordering::Relaxed),
                            l.empty_polls.load(Ordering::Relaxed),
                            l.pauses.load(Ordering::Relaxed),
                            l.partial.load(Ordering::Relaxed),
                            l.corrupt.load(Ordering::Relaxed),
                        ],
                    )
                })
                .collect()
        };
        let snap_shadow_sw = || -> Vec<(usize, u64, u64)> {
            pmu.shadows
                .iter()
                .map(|(idx, cpu, _)| (*idx, *cpu, sh.shadow_live[*idx].polls.load(Ordering::Relaxed)))
                .collect()
        };
        if is_start {
            let tad = pmu.tad.iter().map(PerfCounter::read).collect();
            let extra = pmu.extra.iter().map(PerfCounter::read).collect();
            let core_evs: Vec<Vec<CounterSample>> =
                pmu.cores.iter().map(|(_, _, evs)| evs.iter().map(PerfCounter::read).collect()).collect();
            let shadow_evs: Vec<Vec<CounterSample>> =
                pmu.shadows.iter().map(|(_, _, evs)| evs.iter().map(PerfCounter::read).collect()).collect();
            let sws = snap_sw();
            let s_sws = snap_shadow_sw();
            let at = Instant::now();
            let cores = sws
                .into_iter()
                .zip(core_evs)
                .map(|((idx, cpu, sw), samples)| (idx, cpu, samples, sw))
                .collect();
            let shadows = s_sws
                .into_iter()
                .zip(shadow_evs)
                .map(|((idx, cpu, polls), samples)| (idx, cpu, samples, polls))
                .collect();
            CardSnap { at, tad, extra, cores, shadows }
        } else {
            let at = Instant::now();
            let sws = snap_sw();
            let s_sws = snap_shadow_sw();
            let core_evs: Vec<Vec<CounterSample>> =
                pmu.cores.iter().map(|(_, _, evs)| evs.iter().map(PerfCounter::read).collect()).collect();
            let shadow_evs: Vec<Vec<CounterSample>> =
                pmu.shadows.iter().map(|(_, _, evs)| evs.iter().map(PerfCounter::read).collect()).collect();
            let extra = pmu.extra.iter().map(PerfCounter::read).collect();
            let tad = pmu.tad.iter().map(PerfCounter::read).collect();
            let cores = sws
                .into_iter()
                .zip(core_evs)
                .map(|((idx, cpu, sw), samples)| (idx, cpu, samples, sw))
                .collect();
            let shadows = s_sws
                .into_iter()
                .zip(shadow_evs)
                .map(|((idx, cpu, polls), samples)| (idx, cpu, samples, polls))
                .collect();
            CardSnap { at, tad, extra, cores, shadows }
        }
    }

    fn format_card_snap(s0: &CardSnap, s1: &CardSnap, sh: &Shared, pmu: &CardPmu) -> String {
        let wall_s = s1.at.duration_since(s0.at).as_secs_f64();
        let mut min_pct = 100.0f64;
        let mut tad_parts = Vec::with_capacity(s1.tad.len());
        for (a, b) in s0.tad.iter().zip(&s1.tad) {
            let d = b.delta_from(a);
            min_pct = min_pct.min(d.pct());
            tad_parts.push(format!("\"{}\":{{\"val\":{},\"pct\":{:.2}}}", d.name, d.value, d.pct()));
        }
        let mut extra_parts = Vec::with_capacity(s1.extra.len());
        for (a, b) in s0.extra.iter().zip(&s1.extra) {
            let d = b.delta_from(a);
            min_pct = min_pct.min(d.pct());
            extra_parts.push(format!("\"{}\":{{\"val\":{},\"pct\":{:.2}}}", d.name, d.value, d.pct()));
        }
        let mut core_parts = Vec::with_capacity(s1.cores.len());
        for ((idx, cpu, ev0, sw0), (_, _, ev1, sw1)) in s0.cores.iter().zip(&s1.cores) {
            let mut ev_parts = Vec::with_capacity(ev1.len());
            for (a, b) in ev0.iter().zip(ev1) {
                let d = b.delta_from(a);
                min_pct = min_pct.min(d.pct());
                ev_parts.push(format!("\"{}\":{}", d.name, d.value));
            }
            let taken = sw1[0].saturating_sub(sw0[0]);
            let busy = sw1[1].saturating_sub(sw0[1]);
            let empty = sw1[2].saturating_sub(sw0[2]);
            let pauses = sw1[3].saturating_sub(sw0[3]);
            let partial = sw1[4].saturating_sub(sw0[4]);
            let corrupt = sw1[5].saturating_sub(sw0[5]);
            core_parts.push(format!(
                "{{\"ring_idx\":{},\"cpu\":{},\"taken\":{},\"busy_polls\":{},\"empty_polls\":{},\
                 \"pauses\":{},\"partial\":{},\"corrupt\":{},{}}}",
                idx, cpu, taken, busy, empty, pauses, partial, corrupt, ev_parts.join(",")
            ));
        }
        let mut shadow_parts = Vec::with_capacity(s1.shadows.len());
        for ((idx, cpu, ev0, p0), (_, _, ev1, p1)) in s0.shadows.iter().zip(&s1.shadows) {
            let mut ev_parts = Vec::with_capacity(ev1.len());
            for (a, b) in ev0.iter().zip(ev1) {
                let d = b.delta_from(a);
                min_pct = min_pct.min(d.pct());
                ev_parts.push(format!("\"{}\":{}", d.name, d.value));
            }
            let polls = p1.saturating_sub(*p0);
            shadow_parts.push(format!(
                "{{\"shadow_idx\":{},\"cpu\":{},\"polls\":{},{}}}",
                idx, cpu, polls, ev_parts.join(",")
            ));
        }
        let poll_mode: PollMode = sh.args.poll.into();
        let next_publish: NextPublish = sh.args.next_publish.into();
        let shadow_json = match sh.args.shadow {
            Some(m) => format!("\"{m}\""),
            None => "null".to_string(),
        };
        format!(
            "{{\"wall_s\":{:.6},\"tsc_hz\":{},\"poll_mode\":\"{}\",\"next_publish\":\"{}\",\
             \"shadow_mode\":{},\"l2d_cache_inval_supported\":{},\
             \"empty_pause_ns\":{},\"empty_pause_ticks\":{},\
             \"min_pct\":{:.2},\"tad\":{{{}}},\"extra_pmu\":{{{}}},\"cores\":[{}],\"shadows\":[{}]}}",
            wall_s,
            sh.tsc_hz,
            poll_mode.as_str(),
            next_publish.as_str(),
            shadow_json,
            pmu.l2d_cache_inval_supported,
            sh.empty_pause_ns.load(Ordering::Relaxed),
            sh.empty_pause_ticks.load(Ordering::Relaxed),
            min_pct,
            tad_parts.join(","),
            extra_parts.join(","),
            core_parts.join(","),
            shadow_parts.join(",")
        )
    }

    /// Pin a helper thread to a non-EAL OS CPU (CPU 0) so it never preempts ring/shadow lcores.
    fn pin_helper_thread_cpu0() {
        // SAFETY: zero-initialized cpu_set_t with bit 0 set; best-effort affinity move for current thread.
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(0, &mut set);
            let _ = libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &raw const set);
        }
    }

    fn serve_sync(listener: TcpListener, sh: &Shared) {
        pin_helper_thread_cpu0();
        let mut pmu: Option<CardPmu> = None;
        let mut start_snap: Option<CardSnap> = None;
        while !sh.quit.load(Ordering::Relaxed) {
            match listener.accept() {
                Ok((stream, _)) => {
                    let _ = stream.set_nonblocking(false);
                    let _ = stream.set_nodelay(true);
                    handle_sync_client(stream, sh, &mut pmu, &mut start_snap);
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(5));
                }
                Err(_) => break,
            }
        }
    }

    fn handle_sync_client(
        mut stream: TcpStream,
        sh: &Shared,
        pmu: &mut Option<CardPmu>,
        start_snap: &mut Option<CardSnap>,
    ) {
        let Ok(cloned) = stream.try_clone() else { return };
        let mut reader = BufReader::new(cloned);
        let mut line = String::new();
        let mut spin_stop: Option<(Arc<AtomicBool>, std::thread::JoinHandle<()>)> = None;
        let stop_clock_spin = |slot: &mut Option<(Arc<AtomicBool>, std::thread::JoinHandle<()>)>| {
            if let Some((flag, handle)) = slot.take() {
                flag.store(true, Ordering::Relaxed);
                let _ = handle.join();
            }
        };
        while !sh.quit.load(Ordering::Relaxed) {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let cmd = line.trim();
            let reply = if cmd == "PMU_START" {
                if pmu.is_none() {
                    match CardPmu::open(sh) {
                        Ok(p) => *pmu = Some(p),
                        Err(e) => {
                            let _ = writeln!(stream, "ERR {e}");
                            let _ = stream.flush();
                            continue;
                        }
                    }
                }
                *start_snap = Some(snap_card(pmu.as_ref().unwrap(), sh, true));
                "OK".to_string()
            } else if cmd == "PMU_STOP" {
                match (pmu.as_ref(), start_snap.take()) {
                    (Some(p), Some(s0)) => {
                        let s1 = snap_card(p, sh, false);
                        format_card_snap(&s0, &s1, sh, p)
                    }
                    _ => "ERR no PMU_START".to_string(),
                }
            } else if cmd == "CLOCK_SPIN_START" {
                stop_clock_spin(&mut spin_stop);
                let flag = Arc::new(AtomicBool::new(false));
                let f2 = flag.clone();
                let clock_addr = unsafe { sh.ring.as_ptr().add(CLOCK_OFFSET) } as usize;
                let handle = std::thread::spawn(move || {
                    pin_helper_thread_cpu0();
                    let deadline = Instant::now() + Duration::from_millis(500);
                    // SAFETY: `ring` outlives `serve_sync` (joined inside thread::scope while `ring` is live).
                    let clock_word = unsafe { AtomicU64::from_ptr(clock_addr as *mut u64) };
                    while !f2.load(Ordering::Relaxed) && Instant::now() < deadline {
                        clock_word.store(rdtsc(), Ordering::Relaxed);
                    }
                });
                spin_stop = Some((flag, handle));
                "OK".to_string()
            } else if cmd == "CLOCK_SPIN_STOP" {
                stop_clock_spin(&mut spin_stop);
                "OK".to_string()
            } else if let Some(rest) = cmd.strip_prefix("SET_PAUSE ") {
                match rest.trim().parse::<u64>() {
                    Ok(ns) => {
                        let ticks = if ns > 0 {
                            ((ns as u128 * sh.tsc_hz as u128 + 500_000_000) / 1_000_000_000) as u64
                        } else {
                            0
                        };
                        sh.empty_pause_ns.store(ns, Ordering::Relaxed);
                        sh.empty_pause_ticks.store(ticks, Ordering::Release);
                        format!("OK {ticks}")
                    }
                    Err(e) => format!("ERR bad ns: {e}"),
                }
            } else {
                format!("ERR unknown cmd: {cmd}")
            };
            if writeln!(stream, "{reply}").is_err() || stream.flush().is_err() {
                break;
            }
        }
        stop_clock_spin(&mut spin_stop);
    }
}
