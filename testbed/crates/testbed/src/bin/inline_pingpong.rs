//! Host side of the BAR4 inline path: each request is one MOVDIR64B of a 64 B
//! cell into card DRAM through PF BAR4 slot `inline::SLOT` (no doorbell, no DMA
//! read, no fence). Replies come back as frames over the SDP VF (DPDK port 0,
//! `--reply sdp`), as cells the card writes into a ring in host memory (`--reply
//! ring`; the card runs `inline_reflector --dma` (DPI engine) or `--store` (core
//! stores through PEM outbound regions)), or pulled from card memory (`--reply
//! pull`). Batch = 1, concurrency = `--window`. Start `inline_reflector` on the
//! card first.
//!
//! usage: inline_pingpong [EAL args] -- [--rounds N] [--window W] [--fence] [--reply sdp|ring|pull]

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("inline_pingpong runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::error::Error;
    use std::time::Duration;

    use clap::{Parser, ValueEnum};
    use dpdk::{Eal, Mempool, Memzone, Port, rdtsc, rdtsc_after_loads};
    use testbed::Latency;
    use testbed::closed_loop::{ClosedLoop, FrameReplies, Integrity, Outcome, Replies, Reply};
    use testbed::inline::{
        CORES_OFFSET, Cell, DONE_OFFSET, MAX_CORES, READY, READY_OFFSET, REPLY_CELLS, REPLY_PULL, REPLY_RING_OFFSET,
        RING_CELLS, RingReplies, SLOT, SLOT_BYTES, next_offset,
    };
    use testbed::mmio::{Mapping, has_movdir64b, movdir64b, sfence};

    const PORT: u16 = 0;
    const DESC: u16 = 2048;

    #[derive(Clone, Copy, ValueEnum)]
    enum ReplyPath {
        /// Frames over the SDP VF.
        Sdp,
        /// The card writes cells into a host memory ring (DPI DMA or core stores).
        Ring,
        /// The host reads the card's reply word pair through BAR4 (W=1 only).
        Pull,
    }

    #[derive(Parser)]
    struct Args {
        /// Measured round trips (after warmup).
        #[arg(long, short = 'n', default_value_t = 1_000_000)]
        rounds: u64,
        /// Requests kept outstanding.
        #[arg(long, short = 'w', default_value_t = 1, value_parser = clap::value_parser!(u64).range(1..=1024))]
        window: u64,
        #[arg(long, default_value_t = 20_000)]
        warmup: u64,
        /// SFENCE after every cell (source-side ordering, for comparison).
        #[arg(long)]
        fence: bool,
        #[arg(long, value_enum, default_value_t = ReplyPath::Sdp)]
        reply: ReplyPath,
        /// PF BAR4 (sysfs resource file; chowned by bench/host_dpdk_up.sh).
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar: String,
        /// Instead of the ping-pong, time N uncached 8 B and 16 B reads of card
        /// DRAM through the slot (one PCIe round trip plus the card memory access).
        #[arg(long)]
        probe_reads: Option<u64>,
        /// Pull mode: wait this long after a request before the first read, so
        /// the read does not reach the card before the card has answered (a
        /// miss costs a second ~0.63 us read). 150 ns was best on saturn/CN106.
        #[arg(long, default_value_t = 150)]
        pull_delay_ns: u64,
        /// Fault injection: flip one payload byte in every Nth cell, to show the
        /// card's payload check and the host's reply check catch it.
        #[arg(long, hide = true)]
        corrupt_every: Option<u64>,
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let (eal, app_args) = Eal::init(std::env::args())?;
        let args = Args::parse_from(app_args);
        if !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }
        const { assert!(1024 <= RING_CELLS && 1024 < REPLY_CELLS) };

        let slot = Mapping::new(&args.bar, (SLOT * SLOT_BYTES) as u64, SLOT_BYTES, false)?;
        let cookie = slot.read_u64(READY_OFFSET);
        if cookie != READY {
            return Err(format!("BAR4 slot {SLOT} is not claimed by inline_reflector (cookie {cookie:#x}); not writing").into());
        }
        // Ring sequence numbers continue from the earliest cell a card core is
        // waiting for.
        let cores = slot.read_u64(CORES_OFFSET) as usize;
        if !(1..=MAX_CORES).contains(&cores) {
            return Err(format!("card publishes {cores} cores").into());
        }
        let base = (0..cores)
            .map(|core| slot.read_u64(next_offset(core)))
            .min()
            .and_then(|next| next.checked_sub(1))
            .ok_or("card published no next sequence")?;
        if let Some(n) = args.probe_reads {
            return probe_reads(&eal, &slot, n);
        }
        if matches!(args.reply, ReplyPath::Pull) && args.window != 1 {
            return Err("--reply pull keeps one word pair per run: needs --window 1".into());
        }
        let socket = Port::socket_of(&eal, PORT);

        let outcome = match args.reply {
            ReplyPath::Sdp => {
                slot.write_u64(REPLY_RING_OFFSET, 0);
                // Card core i transmits on its TX queue i, which the SDP delivers
                // to this VF's RX queue i: one queue per card core.
                let queues = cores as u16;
                let pool = Mempool::new(&eal, "inline", 8191 * u32::from(queues), socket)?;
                let port = Port::start(&eal, PORT, &pool, queues, DESC)?;
                drive(&eal, &args, &slot, base, &mut FrameReplies::new(&eal, &port, queues))?
            }
            ReplyPath::Ring => {
                // The card writes as host VF0, so this must be memory DPDK has
                // mapped for the VF's IOMMU domain (any memzone is). Aligned to
                // its size, so it never straddles a card outbound region.
                let bytes = REPLY_CELLS * 64;
                let ring = Memzone::reserve_contiguous(&eal, "inline_replies", bytes, socket, bytes as u32)?;
                // SAFETY: the memzone is ours and that long.
                unsafe { ring.as_ptr().write_bytes(0, bytes) };
                slot.write_u64(REPLY_RING_OFFSET, ring.iova());
                sfence(); // the ring address reaches the card before the first cell
                let mut replies = RingReplies::new(&eal, &ring, base, cores);
                let outcome = drive(&eal, &args, &slot, base, &mut replies);
                println!(
                    "reply ring at IOVA {:#x}: {} polls waited on a missing reply while a later one had landed",
                    ring.iova(),
                    replies.blocked()
                );
                slot.write_u64(REPLY_RING_OFFSET, 0);
                // Let any reply still in flight (to a request counted lost) land
                // before the ring is unmapped.
                std::thread::sleep(Duration::from_millis(10));
                outcome?
            }
            ReplyPath::Pull => {
                slot.write_u64(DONE_OFFSET, 0); // no stale tag from an earlier run
                slot.write_u64(REPLY_RING_OFFSET, REPLY_PULL);
                sfence();
                let delay = eal.tsc_hz() * args.pull_delay_ns / 1_000_000_000;
                let outcome = drive(&eal, &args, &slot, base, &mut PullReplies { slot: &slot, next: 1, delay, armed: true });
                slot.write_u64(REPLY_RING_OFFSET, 0);
                outcome?
            }
        };
        let mode = if args.fence { "movdir64b+sfence" } else { "movdir64b" };
        let reply = match args.reply {
            ReplyPath::Sdp => "sdp".to_owned(),
            ReplyPath::Ring => "ring".to_owned(),
            ReplyPath::Pull => format!("pull delay {} ns", args.pull_delay_ns),
        };
        println!("inline {mode} reply {reply} {outcome}");
        outcome.check()?;
        Ok(())
    }

    /// Uncached reads of the control cell: the cost of one host -> card ->
    /// host round trip through PEM to card memory.
    fn probe_reads(eal: &Eal, slot: &Mapping, n: u64) -> Result<(), Box<dyn Error>> {
        let (mut narrow, mut wide) = (Vec::with_capacity(n as usize), Vec::with_capacity(n as usize));
        for _ in 0..n {
            let t0 = rdtsc_after_loads();
            let cookie = slot.read_u64(READY_OFFSET);
            let t1 = rdtsc_after_loads();
            let [cookie16, _] = slot.read_16(READY_OFFSET);
            let t2 = rdtsc_after_loads();
            if cookie != READY || cookie16 != READY {
                return Err("slot released during the probe".into());
            }
            narrow.push(t1 - t0);
            wide.push(t2 - t1);
        }
        println!("BAR4 UC 8 B read of card DRAM, {n} reads: {}", Latency::from_ticks(narrow, eal.tsc_hz()));
        println!("BAR4 UC 16 B read of card DRAM, {n} reads: {}", Latency::from_ticks(wide, eal.tsc_hz()));
        Ok(())
    }

    /// Pull mode: poll the card's last-reply word pair `(tag, tsc)` with one
    /// 16 B uncached read per poll.
    struct PullReplies<'s> {
        slot: &'s Mapping,
        next: u64,
        /// TSC ticks to wait before the first read after a request.
        delay: u64,
        armed: bool,
    }

    impl Replies for PullReplies<'_> {
        fn poll(&mut self, mut on_reply: impl FnMut(Reply)) -> usize {
            if self.armed {
                // First poll since the last reply, i.e. right after the next
                // request (W=1).
                let start = rdtsc();
                while rdtsc() - start < self.delay {
                    std::hint::spin_loop();
                }
                self.armed = false;
            }
            let [tag, tsc] = self.slot.read_16(DONE_OFFSET);
            let at = rdtsc_after_loads();
            if tag < self.next {
                return 0;
            }
            on_reply(Reply { seq: tag, tsc, at, integrity: Integrity::Whole });
            self.next = tag + 1;
            self.armed = true;
            1
        }
    }

    fn drive(
        eal: &Eal,
        args: &Args,
        slot: &Mapping,
        base: u64,
        replies: &mut impl Replies,
    ) -> Result<Outcome, Box<dyn Error>> {
        let run = ClosedLoop { rounds: args.rounds, window: args.window, warmup: args.warmup };
        run.run(eal, replies, |seq, tsc| {
            let mut cell = Cell::new(base + seq, seq, tsc);
            if args.corrupt_every.is_some_and(|n| seq.is_multiple_of(n)) {
                cell.payload[0] ^= 1;
            }
            // SAFETY: the offset is a 64 B-aligned cell inside the mapped slot, and
            // MOVDIR64B support was checked above.
            unsafe { movdir64b(slot.as_ptr().add(Cell::offset(base + seq)), &cell) };
            if args.fence {
                sfence();
            }
            Ok(())
        })
    }
}
