//! Open-loop ("asynchronous") traffic over the BAR4 inline path: nobody waits
//! for a reply before sending more; only ring credit limits the sender.
//!
//! - `h2c`: host -> card cells, taken and checked by the card, nothing sent
//!   back. `--via movdir`: every EAL lcore MOVDIR64Bs cells into the card's
//!   BAR4 ring (posted writes); credit: the ring cores' next sequence numbers,
//!   read through BAR4 when it runs out. `--via fetch`: this core writes cells
//!   into a request ring in its own memory that the card's `fetch` core reads
//!   with DPI (PCIe reads); credit: the fetch core's next sequence number.
//!   `--via both`: the two at once on separate host and card cores, the
//!   cells split by `--fetch-share` (card `--roles ring,...,fetch`).
//! - `c2h`: the card's stream cores write `--cells` cells into a host memory
//!   ring unprompted (`inline_reflector --dma` or `--store`); this core takes
//!   and checks them in order. Credit: the count taken, written to the card.
//! - `bidir`: `h2c` (lcore 1) and `c2h` (lcore 0) at the same time, each
//!   `--cells` long, with no dependency between them; run the card with
//!   separate roles (e.g. `--roles ring,source` or `fetch,source`).
//! - `split`: request/reply with sending and receiving decoupled: lcore 1
//!   sends requests back to back, lcore 0 takes the replies the card writes
//!   into the host ring; up to a reply ring's worth outstanding.
//!
//! Batch = 1 cell per MOVDIR64B. Start `inline_reflector` on the card first.
//!
//! usage: inline_stream -l CORES [EAL args] -- --dir h2c|c2h|bidir|split [--via movdir|fetch|both] [--cells N]

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("inline_stream runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::error::Error;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::Duration;

    use clap::{Parser, ValueEnum};
    use dpdk::{Eal, Memzone, Port, rdtsc, rdtsc_after_loads};
    use testbed::Latency;
    use testbed::closed_loop::{Integrity, Replies};
    use testbed::inline::{
        CORES_OFFSET, Cell, FETCH_CELLS, FETCH_NEXT_OFFSET, FETCH_RING_OFFSET, FETCH_TRIGGER_OFFSET, MAX_CORES, READY,
        READY_OFFSET, REPLY_CELLS, REPLY_NONE, REPLY_RING_OFFSET, RING_CELLS, RingReplies, SLOT, SLOT_BYTES,
        STREAM_CORES_OFFSET, STREAM_OFFSET, STREAM_RING_OFFSET, TAKEN_OFFSET, next_offset,
    };
    use testbed::mmio::{Mapping, has_movdir64b, movdir64b};

    #[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
    enum Dir {
        H2c,
        C2h,
        Bidir,
        Split,
    }

    /// How host -> card cells travel.
    #[derive(Clone, Copy, PartialEq, Eq, Debug, ValueEnum)]
    enum Via {
        /// MOVDIR64B into the card's BAR4 ring: posted writes.
        Movdir,
        /// A request ring in host memory the card reads with DPI: PCIe reads.
        Fetch,
        /// Both at once, on separate cores, splitting the cells (`h2c` only):
        /// lcore 0 fills the fetch ring, the other lcores MOVDIR64B.
        Both,
        /// The fetch ring, read only when MOVDIR64B doorbells say so, the
        /// doorbells carrying `--inline` of every `--batch` messages
        /// themselves (`h2c` only; card `--roles fetch`).
        Hybrid,
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum)]
        dir: Dir,
        #[arg(long, value_enum, default_value_t = Via::Movdir)]
        via: Via,
        #[arg(long, short = 'n', default_value_t = 20_000_000)]
        cells: u64,
        /// h2c `--via both`: the share of `--cells` that goes through the
        /// fetch ring (the rest by MOVDIR64B).
        #[arg(long, default_value_t = 0.5)]
        fetch_share: f64,
        /// `--via hybrid`: messages per doorbell round.
        #[arg(long, default_value_t = 32)]
        batch: u64,
        /// h2c `--via hybrid`: messages of each batch sent by MOVDIR64B (one
        /// doorbell each; 0 = one doorbell carrying none), the rest through
        /// the fetch ring. `split` needs 0 (inline messages get no reply).
        #[arg(long, default_value_t = 1)]
        inline: u64,
        /// split: requests outstanding at most (1 = ping-pong).
        #[arg(long, default_value_t = REPLY_CELLS as u64)]
        window: u64,
        /// h2c `--via fetch|hybrid`: keep the fetch ring published this long
        /// after the last request was taken (to measure idle read traffic).
        #[arg(long, default_value_t = 0)]
        hold_ms: u64,
        /// c2h: take the ring a whole ring at a time, starting this long after
        /// granting the card credit for it, so no line is touched while the
        /// card may still be writing it (0 = take cells as they arrive).
        #[arg(long, default_value_t = 0)]
        lag_us: u64,
        /// c2h: lift the card's credit limit and read nothing until it is done
        /// (the ring is overwritten in circles; only the last cells are
        /// checked): the card's write throughput with no host interference.
        #[arg(long)]
        blast: bool,
        /// PF BAR4 (sysfs resource file; chowned by bench/host_dpdk_up.sh).
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar: String,
    }

    /// The card's slot of BAR4, mapped by this thread.
    fn map_slot(args: &Args) -> Result<Mapping, Box<dyn Error>> {
        Ok(Mapping::new(&args.bar, (SLOT * SLOT_BYTES) as u64, SLOT_BYTES, false)?)
    }

    /// The earliest ring sequence a card ring core is still waiting for.
    fn card_next(slot: &Mapping, cores: usize) -> u64 {
        (0..cores).map(|core| slot.read_u64(next_offset(core))).min().expect("at least one core")
    }

    fn rate(cells: u64, ticks: u64, hz: u64) -> String {
        let per_s = cells as f64 * hz as f64 / ticks as f64;
        format!("{:.2} M cells/s ({:.2} GB/s of 64 B cells)", per_s / 1e6, per_s * 64.0 / 1e9)
    }

    /// What the card publishes about its cores.
    #[derive(Clone, Copy)]
    struct Card {
        ring_cores: usize,
        stream_cores: usize,
    }

    impl Card {
        fn ring_cores(self) -> Result<usize, String> {
            Some(self.ring_cores).filter(|n| (1..=MAX_CORES).contains(n)).ok_or_else(|| "card has no ring cores".into())
        }

        fn stream_cores(self) -> Result<usize, String> {
            Some(self.stream_cores)
                .filter(|n| (1..=MAX_CORES).contains(n))
                .ok_or_else(|| "card has no stream cores".into())
        }
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let (eal, app_args) = Eal::init(std::env::args())?;
        let args = Args::parse_from(app_args);
        if !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }
        let slot = map_slot(&args)?;
        let cookie = slot.read_u64(READY_OFFSET);
        if cookie != READY {
            return Err(format!("BAR4 slot {SLOT} is not claimed by inline_reflector (cookie {cookie:#x}); not writing").into());
        }
        let card = Card {
            ring_cores: slot.read_u64(CORES_OFFSET) as usize,
            stream_cores: slot.read_u64(STREAM_CORES_OFFSET) as usize,
        };
        match args.dir {
            Dir::H2c => h2c(&eal, &args, &slot, card),
            Dir::C2h => c2h(&eal, &args, &slot, card),
            Dir::Bidir => bidir(&eal, &args, &slot, card),
            Dir::Split => split(&eal, &args, &slot, card),
        }
    }

    /// Host -> card: `--cells` cells, by MOVDIR64B from every lcore, through
    /// a fetch ring from one, or both at once.
    fn h2c(eal: &Eal, args: &Args, slot: &Mapping, card: Card) -> Result<(), Box<dyn Error>> {
        let (n, hz) = (args.cells, eal.tsc_hz());
        match args.via {
            Via::Movdir => {
                let cores = card.ring_cores()?;
                let senders = eal.lcores() as u64;
                let base = card_next(slot, cores) - 1;
                slot.write_u64(REPLY_RING_OFFSET, REPLY_NONE);
                let start = rdtsc();
                let issued = eal.on_each_lcore(vec![(); senders as usize], |j, ()| {
                    let slot = map_slot(args).map_err(|e| e.to_string())?;
                    send_movdir(&slot, cores, base, n, j as u64, senders);
                    Ok::<_, String>(rdtsc())
                });
                let mut last = start;
                for end in issued {
                    last = last.max(end?);
                }
                wait_ring_taken(slot, cores, base + n, hz)?;
                let taken = rdtsc() - start;
                println!(
                    "h2c via movdir {senders} host cores -> {cores} card ring cores, {n} cells: host issued at {}, card \
                     took all at {}",
                    rate(n, last - start, hz),
                    rate(n, taken, hz)
                );
            }
            Via::Fetch => {
                if eal.lcores() != 1 {
                    return Err("--via fetch writes one request ring from one lcore".into());
                }
                let ring = fetch_ring(eal, slot, false)?;
                let start = rdtsc();
                let issued = send_fetch(slot, &ring, n, hz)?;
                let taken = rdtsc() - start;
                std::thread::sleep(Duration::from_millis(args.hold_ms));
                withdraw(slot, FETCH_RING_OFFSET);
                println!(
                    "h2c via fetch 1 host core -> card fetch core, {n} cells: host wrote at {}, card took all at {}",
                    rate(n, issued - start, hz),
                    rate(n, taken, hz)
                );
            }
            Via::Both => {
                let cores = card.ring_cores()?;
                let senders = eal.lcores() as u64 - 1;
                if senders == 0 {
                    return Err("--via both needs a fetch lcore and at least one MOVDIR64B lcore".into());
                }
                let n_fetch = (n as f64 * args.fetch_share.clamp(0.0, 1.0)) as u64;
                let n_movdir = n - n_fetch;
                let base = card_next(slot, cores) - 1;
                slot.write_u64(REPLY_RING_OFFSET, REPLY_NONE);
                let ring = fetch_ring(eal, slot, false)?;
                let start = rdtsc();
                let ends = eal.on_each_lcore(vec![(); senders as usize + 1], |j, ()| {
                    let slot = map_slot(args).map_err(|e| e.to_string())?;
                    if j == 0 {
                        send_fetch(&slot, &ring, n_fetch, hz)?;
                    } else {
                        send_movdir(&slot, cores, base, n_movdir, j as u64 - 1, senders);
                        if j == 1 {
                            wait_ring_taken(&slot, cores, base + n_movdir, hz)?;
                        }
                    }
                    Ok::<_, String>(rdtsc())
                });
                let ends = ends.into_iter().collect::<Result<Vec<_>, _>>()?;
                withdraw(slot, FETCH_RING_OFFSET);
                let (fetched, moved) = (ends[0], ends[1]);
                println!(
                    "h2c via both, {n} cells: fetch {n_fetch} by 1 host core at {} | movdir {n_movdir} by {senders} host \
                     cores -> {cores} card ring cores at {} | together {}",
                    rate(n_fetch, fetched - start, hz),
                    rate(n_movdir, moved - start, hz),
                    rate(n, fetched.max(moved) - start, hz)
                );
            }
            Via::Hybrid => {
                if eal.lcores() != 1 || card.ring_cores != 0 {
                    return Err("--via hybrid runs on one lcore against a card with only a fetch core".into());
                }
                let (batch, inline) = (args.batch.max(1), args.inline.min(args.batch.max(1)));
                let base = slot.read_u64(next_offset(0)) - 1;
                slot.write_u64(REPLY_RING_OFFSET, REPLY_NONE);
                let ring = fetch_ring(eal, slot, true)?;
                let start = rdtsc();
                let sent = send_hybrid(slot, &ring, base, n, batch, inline, hz)?;
                let taken = rdtsc() - start;
                std::thread::sleep(Duration::from_millis(args.hold_ms));
                withdraw(slot, FETCH_RING_OFFSET);
                println!(
                    "h2c via hybrid, batch {batch} inline {inline}, {n} messages ({} inline, {} fetched, {} MOVDIR64B \
                     doorbells): host wrote at {}, card took all at {}",
                    sent.inline,
                    sent.fetched,
                    sent.doorbells,
                    rate(n, sent.written - start, hz),
                    rate(n, taken, hz)
                );
            }
        }
        Ok(())
    }

    /// MOVDIR64B cells `k = lane + 1, lane + 1 + lanes, ...` up to `n` into
    /// the card's BAR4 ring as ring sequence `base + k`.
    fn send_movdir(slot: &Mapping, cores: usize, base: u64, n: u64, lane: u64, lanes: u64) {
        let mut allowed = 0;
        let mut k = lane + 1;
        while k <= n {
            let seq = base + k;
            // Cell `seq` overwrites `seq - RING_CELLS`, which must be taken.
            while seq >= allowed {
                allowed = card_next(slot, cores) + RING_CELLS as u64;
            }
            let cell = Cell::new(seq, k, 1);
            // SAFETY: a 64 B-aligned cell inside the mapped slot; MOVDIR64B
            // support was checked.
            unsafe { movdir64b(slot.as_ptr().add(Cell::offset(seq)), &cell) };
            k += lanes;
        }
    }

    /// Wait (at most 1 s) until the card's ring cores took every cell up to
    /// ring sequence `last`.
    fn wait_ring_taken(slot: &Mapping, cores: usize, last: u64, hz: u64) -> Result<(), String> {
        let deadline = rdtsc() + hz;
        while card_next(slot, cores) <= last {
            if rdtsc() > deadline {
                return Err(format!("card ring cores stopped at sequence {} of {last}", card_next(slot, cores) - 1));
            }
        }
        Ok(())
    }

    /// A zeroed host ring of `cells` cells, aligned to its size (so it never
    /// straddles a card outbound region).
    fn host_ring<'e>(eal: &'e Eal, name: &str, cells: usize) -> Result<Memzone<'e>, Box<dyn Error>> {
        let bytes = cells * 64;
        let ring = Memzone::reserve_contiguous(eal, name, bytes, Port::socket_of(eal, 0), bytes as u32)?;
        // SAFETY: the memzone is ours and that long.
        unsafe { ring.as_ptr().write_bytes(0, bytes) };
        Ok(ring)
    }

    /// Clear a ring address word and give the card time to stop using the
    /// ring before its memory goes.
    fn withdraw(slot: &Mapping, word: usize) {
        slot.write_u64(word, 0);
        std::thread::sleep(Duration::from_millis(10));
    }

    /// A host request ring for the card's fetch core, published once the
    /// fetch core has reset its state (it does so whenever the ring word
    /// changes), so the credit it publishes belongs to this ring.
    /// `triggered`: the fetch core reads only what doorbells publish.
    fn fetch_ring<'e>(eal: &'e Eal, slot: &Mapping, triggered: bool) -> Result<Memzone<'e>, Box<dyn Error>> {
        let ring = host_ring(eal, "fetch_requests", FETCH_CELLS)?;
        slot.write_u64(FETCH_RING_OFFSET, 0);
        std::thread::sleep(Duration::from_millis(1));
        if slot.read_u64(FETCH_NEXT_OFFSET) != 1 {
            return Err("the card's fetch core did not reset (is there one?)".into());
        }
        // UC stores reach the card in order: the mode before the ring.
        slot.write_u64(FETCH_TRIGGER_OFFSET, u64::from(triggered));
        slot.write_u64(FETCH_RING_OFFSET, ring.iova());
        Ok(ring)
    }

    /// Write requests `1..=n` into the fetch ring as fast as the card's
    /// credit allows, then wait (at most 1 s) until it took them all.
    /// Returns the TSC when the last one was written.
    fn send_fetch(slot: &Mapping, ring: &Memzone, n: u64, hz: u64) -> Result<u64, String> {
        let mut allowed = 0;
        for k in 1..=n {
            // Request `k` overwrites `k - FETCH_CELLS`, which must be taken.
            while k >= allowed {
                allowed = slot.read_u64(FETCH_NEXT_OFFSET) + FETCH_CELLS as u64;
            }
            // SAFETY: a 64 B-aligned cell of the ring memzone; only this
            // thread writes it.
            unsafe { Cell::new(k, k, 1).publish(ring.as_ptr().add(Cell::fetch_offset(k)).cast()) };
        }
        let written = rdtsc();
        let deadline = written + hz;
        while slot.read_u64(FETCH_NEXT_OFFSET) <= n {
            if rdtsc() > deadline {
                return Err(format!("card fetched only {} of {n}", slot.read_u64(FETCH_NEXT_OFFSET) - 1));
            }
        }
        Ok(written)
    }

    /// What [`send_hybrid`] sent.
    struct Hybrid {
        inline: u64,
        fetched: u64,
        doorbells: u64,
        /// TSC when the last message was written.
        written: u64,
    }

    /// Send messages `1..=n` in batches of `batch`: per batch, write the
    /// last `batch - inline` into the fetch ring, then MOVDIR64B one doorbell
    /// per inline message (or a single empty one) into the BAR4 ring at ring
    /// sequence `base + 1, ...`, each saying how much of the fetch ring is
    /// published. Wait (at most 1 s) until the card took everything.
    fn send_hybrid(
        slot: &Mapping,
        ring: &Memzone,
        base: u64,
        n: u64,
        batch: u64,
        inline: u64,
        hz: u64,
    ) -> Result<Hybrid, String> {
        let (mut k, mut fetched, mut doorbells) = (0u64, 0u64, 0u64);
        let (mut fetch_allowed, mut ring_allowed) = (0u64, 0u64);
        while k < n {
            let size = batch.min(n - k);
            let carried = inline.min(size);
            // The inline messages lead the batch; the fetched ones follow.
            for _ in carried..size {
                fetched += 1;
                // Request `fetched` overwrites `fetched - FETCH_CELLS`, which
                // must be taken.
                while fetched >= fetch_allowed {
                    fetch_allowed = slot.read_u64(FETCH_NEXT_OFFSET) + FETCH_CELLS as u64;
                }
                // SAFETY: a 64 B-aligned cell of the ring memzone; only this
                // thread writes it.
                unsafe { Cell::new(fetched, fetched, 1).publish(ring.as_ptr().add(Cell::fetch_offset(fetched)).cast()) };
            }
            for message in (k + 1..=k + carried).map(Some).chain((carried == 0).then_some(None)) {
                doorbells += 1;
                ring_doorbell(slot, base + doorbells, message.unwrap_or(0), fetched, &mut ring_allowed);
            }
            k += size;
        }
        let written = rdtsc();
        let deadline = written + hz;
        while slot.read_u64(FETCH_NEXT_OFFSET) <= fetched || slot.read_u64(next_offset(0)) <= base + doorbells {
            if rdtsc() > deadline {
                return Err(format!(
                    "card took {} of {fetched} fetched and {} of {doorbells} doorbells",
                    slot.read_u64(FETCH_NEXT_OFFSET) - 1,
                    slot.read_u64(next_offset(0)) - 1 - base
                ));
            }
        }
        Ok(Hybrid { inline: n - fetched, fetched, doorbells, written })
    }

    /// MOVDIR64B doorbell `seq` into the BAR4 ring: it carries `message`
    /// (0 = none) and says the fetch ring is published up to `published`.
    /// `allowed` caches the ring credit.
    fn ring_doorbell(slot: &Mapping, seq: u64, message: u64, published: u64, allowed: &mut u64) {
        // Doorbell `seq` overwrites `seq - RING_CELLS`, which must be taken.
        while seq >= *allowed {
            *allowed = slot.read_u64(next_offset(0)) + RING_CELLS as u64;
        }
        let cell = Cell::doorbell(seq, message, published);
        // SAFETY: a 64 B-aligned cell inside the mapped slot; MOVDIR64B
        // support was checked.
        unsafe { movdir64b(slot.as_ptr().add(Cell::offset(seq)), &cell) };
    }

    /// A card -> host stream as this core took it.
    struct Taken {
        bad: u64,
        torn: u64,
        late: u64,
        /// TSC of the first cell seen and of the last one taken.
        first: u64,
        end: u64,
        /// Ticks spent in polls (not waiting out a lag).
        reading: u64,
    }

    /// Start an `n`-cell card -> host stream into `ring` and take it in order,
    /// granting credit as it goes (or, with a lag, a ring's worth at a time).
    fn take_stream(eal: &Eal, slot: &Mapping, ring: &Memzone, n: u64, cores: usize, lag_us: u64) -> Result<Taken, String> {
        let hz = eal.tsc_hz();
        slot.write_u64(TAKEN_OFFSET, 0);
        // UC stores reach the card in order: credit before the length.
        slot.write_u64(STREAM_OFFSET, n);
        let mut replies = RingReplies::new(eal, ring, 0, cores);
        let (mut taken, mut bad, mut late, mut torn) = (0u64, 0u64, 0u64, 0u64);
        let (mut first, mut published, mut last_progress) = (None, 0, rdtsc());
        let lag = hz / 1_000_000 * lag_us;
        // With a lag: the end of the ring's worth being taken.
        let (mut round_end, mut reading) = (0, 0);
        while taken < n {
            if lag > 0 && taken == round_end {
                slot.write_u64(TAKEN_OFFSET, taken);
                published = taken;
                round_end = (taken + REPLY_CELLS as u64).min(n);
                let granted = rdtsc();
                while rdtsc() - granted < lag {
                    std::hint::spin_loop();
                }
                first.get_or_insert(granted);
                last_progress = rdtsc();
            }
            let poll_start = rdtsc();
            let found = replies.poll(|r| {
                taken += 1;
                bad += u64::from(r.tsc != 1 || r.seq != taken);
                match r.integrity {
                    Integrity::Whole => {}
                    Integrity::LateTail(_) => late += 1,
                    Integrity::Torn => torn += 1,
                }
            });
            let now = rdtsc();
            reading += now - poll_start;
            if found > 0 {
                first.get_or_insert(now);
                last_progress = now;
            } else if now - last_progress > hz {
                slot.write_u64(STREAM_OFFSET, 0);
                return Err(format!("stream stalled after {taken} of {n} cells"));
            }
            if lag == 0 && taken - published >= REPLY_CELLS as u64 / 8 {
                slot.write_u64(TAKEN_OFFSET, taken);
                published = taken;
            }
        }
        let end = rdtsc_after_loads();
        slot.write_u64(STREAM_OFFSET, 0);
        Ok(Taken { bad, torn, late, first: first.unwrap_or(end), end, reading })
    }

    /// Card -> host: the card writes `--cells` cells, this core takes them.
    fn c2h(eal: &Eal, args: &Args, slot: &Mapping, card: Card) -> Result<(), Box<dyn Error>> {
        let (n, cores, hz) = (args.cells, card.stream_cores()?, eal.tsc_hz());
        let ring = host_ring(eal, "stream_replies", REPLY_CELLS)?;
        slot.write_u64(STREAM_RING_OFFSET, ring.iova());
        if args.blast {
            return blast(eal, slot, &ring, n, cores);
        }
        let t = take_stream(eal, slot, &ring, n, cores, args.lag_us)?;
        withdraw(slot, STREAM_RING_OFFSET);
        println!(
            "c2h {cores} card cores -> 1 host core, {n} cells, lag {} us: bad {} torn {} late {}, host took them at {}; \
             polling took {:.1} ns per cell",
            args.lag_us,
            t.bad,
            t.torn,
            t.late,
            rate(n, t.end - t.first, hz),
            t.reading as f64 * 1e9 / hz as f64 / n as f64
        );
        if t.bad > 0 || t.torn > 0 {
            return Err(format!("{} bad and {} torn cells", t.bad, t.torn).into());
        }
        Ok(())
    }

    /// c2h `--blast`: the card writes `n` cells with no credit limit; sleep
    /// until every card core's last cell has landed, then report the rate.
    fn blast(eal: &Eal, slot: &Mapping, ring: &Memzone, n: u64, cores: usize) -> Result<(), Box<dyn Error>> {
        slot.write_u64(TAKEN_OFFSET, u64::MAX);
        slot.write_u64(STREAM_OFFSET, n);
        let (hz, start) = (eal.tsc_hz(), rdtsc());
        let last = (n.saturating_sub(cores as u64) + 1)..=n;
        let landed = |k: u64| {
            // SAFETY: the head word of a cell inside the ring memzone.
            unsafe { ring.as_ptr().add(Cell::reply_offset(k, cores)).cast::<u64>().read_volatile() == k }
        };
        while !last.clone().all(landed) {
            if rdtsc() - start > 30 * hz {
                slot.write_u64(STREAM_OFFSET, 0);
                return Err("blast did not finish within 30 s".into());
            }
            std::thread::sleep(Duration::from_micros(100));
        }
        let end = rdtsc_after_loads();
        slot.write_u64(STREAM_OFFSET, 0);
        slot.write_u64(TAKEN_OFFSET, 0);
        withdraw(slot, STREAM_RING_OFFSET);
        println!("c2h blast {cores} card cores, {n} cells, host reading nothing: {}", rate(n, end - start, hz));
        Ok(())
    }

    /// One direction of `bidir`, as its lcore saw it: start and end TSC, and
    /// for c2h what was taken.
    struct Leg {
        start: u64,
        end: u64,
        taken: Option<Taken>,
    }

    /// h2c on lcore 1 and c2h on lcore 0 at the same time.
    fn bidir(eal: &Eal, args: &Args, slot: &Mapping, card: Card) -> Result<(), Box<dyn Error>> {
        if eal.lcores() != 2 {
            return Err("--dir bidir needs two lcores (c2h receiver, h2c sender)".into());
        }
        let (n, hz, stream_cores) = (args.cells, eal.tsc_hz(), card.stream_cores()?);
        let stream = host_ring(eal, "stream_replies", REPLY_CELLS)?;
        slot.write_u64(STREAM_RING_OFFSET, stream.iova());
        let (requests, ring_cores, base) = match args.via {
            Via::Movdir => {
                let cores = card.ring_cores()?;
                slot.write_u64(REPLY_RING_OFFSET, REPLY_NONE);
                (None, cores, card_next(slot, cores) - 1)
            }
            Via::Fetch => (Some(fetch_ring(eal, slot, false)?), 0, 0),
            Via::Hybrid => {
                if card.ring_cores != 0 {
                    return Err("--via hybrid needs a card with no ring cores (--roles fetch,source)".into());
                }
                slot.write_u64(REPLY_RING_OFFSET, REPLY_NONE);
                (Some(fetch_ring(eal, slot, true)?), 0, slot.read_u64(next_offset(0)) - 1)
            }
            Via::Both => return Err("--via both is for --dir h2c".into()),
        };
        let legs = eal.on_each_lcore(vec![(); 2], |role, ()| {
            let slot = map_slot(args).map_err(|e| e.to_string())?;
            let start = rdtsc();
            if role == 0 {
                let taken = take_stream(eal, &slot, &stream, n, stream_cores, 0)?;
                return Ok(Leg { start, end: taken.end, taken: Some(taken) });
            }
            match &requests {
                None => {
                    send_movdir(&slot, ring_cores, base, n, 0, 1);
                    wait_ring_taken(&slot, ring_cores, base + n, hz)?;
                }
                Some(ring) if args.via == Via::Hybrid => {
                    send_hybrid(&slot, ring, base, n, args.batch.max(1), args.inline.min(args.batch.max(1)), hz)?;
                }
                Some(ring) => {
                    send_fetch(&slot, ring, n, hz)?;
                }
            }
            Ok::<_, String>(Leg { start, end: rdtsc(), taken: None })
        });
        withdraw(slot, STREAM_RING_OFFSET);
        if requests.is_some() {
            withdraw(slot, FETCH_RING_OFFSET);
        }
        let mut legs = legs.into_iter();
        let c2h = legs.next().expect("receiver")?;
        let h2c = legs.next().expect("sender")?;
        let t = c2h.taken.expect("the receiver took the stream");
        println!(
            "bidir {n} cells each way: h2c via {:?} card took all at {} | c2h {stream_cores} card cores -> 1 host core at \
             {} (bad {} torn {} late {})",
            args.via,
            rate(n, h2c.end - h2c.start, hz),
            rate(n, c2h.end - c2h.start, hz),
            t.bad,
            t.torn,
            t.late
        );
        if t.bad > 0 || t.torn > 0 {
            return Err(format!("{} bad and {} torn cells", t.bad, t.torn).into());
        }
        Ok(())
    }

    /// Request/reply with lcore 1 sending and lcore 0 receiving, at most
    /// `--window` requests outstanding. Requests go by MOVDIR64B to the
    /// card's ring cores, or through a fetch ring to its fetch core (which
    /// must have a writer to reply with), read speculatively or, `--via
    /// hybrid`, when a doorbell rung every `--batch` requests (and before
    /// waiting for credit) says so.
    fn split(eal: &Eal, args: &Args, slot: &Mapping, card: Card) -> Result<(), Box<dyn Error>> {
        if eal.lcores() != 2 {
            return Err("--dir split needs two lcores (receiver, sender)".into());
        }
        let n = args.cells;
        let window = args.window.clamp(1, REPLY_CELLS as u64);
        let batch = args.batch.max(1);
        let ring = host_ring(eal, "stream_replies", REPLY_CELLS)?;
        // The reply ring is published before the first request.
        slot.write_u64(REPLY_RING_OFFSET, ring.iova());
        let (requests, cores, base) = match args.via {
            Via::Movdir => {
                let cores = card.ring_cores()?;
                (None, cores, card_next(slot, cores) - 1)
            }
            Via::Fetch => (Some(fetch_ring(eal, slot, false)?), 1, 0),
            Via::Hybrid => {
                if card.ring_cores != 0 || args.inline != 0 {
                    return Err("split --via hybrid needs --inline 0 and a card with only a fetch core".into());
                }
                (Some(fetch_ring(eal, slot, true)?), 1, 0)
            }
            Via::Both => return Err("--via both is for --dir h2c".into()),
        };
        let hybrid = args.via == Via::Hybrid;
        // Doorbells go after the ring's last (from earlier runs).
        let doorbell_base = slot.read_u64(next_offset(0)) - 1;
        let received = AtomicU64::new(0);
        let hz = eal.tsc_hz();
        let results = eal.on_each_lcore(vec![(); 2], |role, ()| {
            if role == 1 {
                let slot = map_slot(args).map_err(|e| e.to_string())?;
                let (start, mut stalls) = (rdtsc(), 0u64);
                let (mut doorbells, mut rung, mut ring_allowed) = (0u64, 0u64, 0u64);
                for k in 1..=n {
                    // Reply `k` overwrites reply `k - REPLY_CELLS`, which must be
                    // taken (which also bounds the card's backlog, within a
                    // ring or fetch ring's worth).
                    if k > received.load(Ordering::Acquire) + window {
                        // Requests not rung yet would never be read.
                        if hybrid && rung < k - 1 {
                            doorbells += 1;
                            ring_doorbell(&slot, doorbell_base + doorbells, 0, k - 1, &mut ring_allowed);
                            rung = k - 1;
                        }
                        stalls += 1;
                        while k > received.load(Ordering::Acquire) + window {
                            std::hint::spin_loop();
                        }
                    }
                    let cell = Cell::new(base + k, k, rdtsc());
                    match &requests {
                        // SAFETY: as in `send_movdir`.
                        None => unsafe { movdir64b(slot.as_ptr().add(Cell::offset(base + k)), &cell) },
                        // SAFETY: as in `send_fetch`.
                        Some(fetch) => unsafe { cell.publish(fetch.as_ptr().add(Cell::fetch_offset(k)).cast()) },
                    }
                    if hybrid && (k % batch == 0 || k == n) {
                        doorbells += 1;
                        ring_doorbell(&slot, doorbell_base + doorbells, 0, k, &mut ring_allowed);
                        rung = k;
                    }
                }
                return Ok((start, rdtsc(), Vec::new(), stalls));
            }
            let mut replies = RingReplies::new(eal, &ring, base, cores);
            let (mut taken, mut bad, mut rtt) = (0u64, 0u64, Vec::with_capacity(n as usize / 16 + 1));
            let mut last_progress = rdtsc();
            while taken < n {
                let found = replies.poll(|r| {
                    taken += 1;
                    if r.tsc == 0 || r.seq != taken || r.integrity == Integrity::Torn {
                        bad += 1;
                    } else if taken % 16 == 0 {
                        // Its own timestamp: the poll's (taken at its first
                        // reply) can predate a request sent during the poll.
                        rtt.push(rdtsc_after_loads() - r.tsc);
                    }
                });
                received.store(taken, Ordering::Release);
                if found > 0 {
                    last_progress = rdtsc();
                } else if rdtsc() - last_progress > hz {
                    return Err(format!("replies stalled after {taken} of {n}"));
                }
            }
            Ok((0, rdtsc_after_loads(), rtt, bad))
        });
        if requests.is_some() {
            withdraw(slot, FETCH_RING_OFFSET);
        }
        withdraw(slot, REPLY_RING_OFFSET);
        let mut it = results.into_iter();
        let (_, end, rtt, bad) = it.next().expect("receiver")?;
        let (start, issued, _, stalls) = it.next().expect("sender")?;
        println!(
            "split via {:?}{}, window {window}, 1 sender + 1 receiver host core, {cores} card cores, {n} round trips: \
             bad {bad}, {} (sender issued at {}, waited for credit {stalls} times) | RTT of every 16th: {}",
            args.via,
            if hybrid { format!(" (doorbell every {batch})") } else { String::new() },
            rate(n, end - start, hz),
            rate(n, issued - start, hz),
            Latency::from_ticks(rtt, hz)
        );
        if bad > 0 {
            return Err(format!("{bad} bad replies").into());
        }
        Ok(())
    }
}
