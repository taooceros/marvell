//! Host side of the BAR4 inline path: each request is one MOVDIR64B of a 64 B
//! cell into card DRAM through PF BAR4 slot `inline::SLOT` (no doorbell, no DMA
//! read, no fence). Replies come back as frames over the SDP VF (DPDK port 0,
//! `--reply sdp`), as cells the card writes into a ring in host memory (`--reply
//! ring`; the card runs `inline_reflector --dma` (DPI engine) or `--store` (core
//! stores through PEM outbound regions)), or pulled from card memory (`--reply
//! pull`). Batch = 1, concurrency = `--window`. Start `inline_reflector` on the
//! card first: the slot is used only under its lease (`--card`,
//! `testbed::lease`), and the run stops when the reflector takes it back.
//!
//! usage: inline_pingpong [EAL args] -- [--rounds N] [--window W] [--fence] [--reply sdp|ring|pull] [--card ADDR:PORT]

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
    use std::fs::{self, File};
    use std::io::{self, BufRead, BufReader, BufWriter, Write};
    use std::mem::size_of;
    use std::net::TcpStream;
    use std::os::fd::RawFd;
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Eal, Mempool, Memzone, Port, rdtsc, rdtsc_after_loads};
    use testbed::Latency;
    use mrv236::closed_loop::{ClosedLoop, FrameReplies, Integrity, Outcome, Replies, Reply, RunOpts};
    use mrv236::inline::{
        CLOCK_OFFSET, CORES_OFFSET, Cell, DONE_OFFSET, MAX_CORES, READY, READY_OFFSET, REPLY_CELLS, REPLY_PULL,
        REPLY_RING_OFFSET, RING_CELLS, RingReplies, SLOT, next_offset,
    };
    use testbed::lease::{self, Lease, LeasedSlot};
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};

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
        /// The card reflector's lease address (TCP): BAR4 is mapped only after
        /// it granted this run the slot.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card: String,
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
        /// MRV-75 experiment: enable direct host core+uncore PMU and card sync PMU.
        #[arg(long, hide = true)]
        pmu: bool,
        /// MRV-75 experiment: card sync TCP address for PMU_START/PMU_STOP.
        #[arg(long, hide = true, default_value = "10.10.0.2:7527")]
        sync: String,
        /// MRV-75 experiment: cap requests submitted per poll loop iteration (0 = unlimited).
        #[arg(long, hide = true, default_value_t = 0)]
        max_send_burst: u64,
        /// MRV-75 experiment: minimum inter-send gap in nanoseconds (0 = none).
        #[arg(long, hide = true, default_value_t = 0)]
        send_gap_ns: u64,
        /// MRV-50/MRV-75 experiment: expect card-stamped replies and perform BAR4 clock sync.
        #[arg(long, hide = true)]
        stamp: bool,
        /// MRV-50/MRV-75 experiment: sparse per-reply stamping on (seq & 63) < 8.
        #[arg(long, hide = true)]
        stamp_sparse: bool,
        /// MRV-75 experiment: max stamped replies to record.
        #[arg(long, hide = true, default_value_t = 0)]
        record_cap: usize,
        /// MRV-75 experiment: skip this many measured messages before recording.
        #[arg(long, hide = true, default_value_t = 0)]
        record_skip: u64,
        /// MRV-75 experiment: write recorded stamped replies to this CSV file.
        #[arg(long, hide = true)]
        stamp_csv: Option<String>,
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let (eal, app_args) = Eal::init(std::env::args())?;
        let args = Args::parse_from(app_args);
        if !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }
        const { assert!(1024 <= RING_CELLS && 1024 < REPLY_CELLS) };

        let lease = Lease::acquire(&args.card)?;
        eprintln!(
            "inline_pingpong: inline_reflector pid {} at {} granted BAR4 slot {SLOT} (nonce {:#018x})",
            lease.reflector_pid(),
            args.card,
            lease.nonce()
        );
        let slot = lease.map_slot(&args.bar)?;
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

        let mut host_pmu_json = "null".to_string();
        let mut card_pmu_json = "null".to_string();
        let mut wall_s = 0.0f64;
        let mut blocked = 0u64;
        let mut clock_sync: Option<((u64, u64, u64), (u64, u64, u64))> = None;

        let outcome = match args.reply {
            ReplyPath::Sdp => {
                slot.write_u64(REPLY_RING_OFFSET, 0);
                let queues = cores as u16;
                let pool = Mempool::new(&eal, "inline", 8191 * u32::from(queues), socket)?;
                let port = Port::start(&eal, PORT, &pool, queues, DESC)?;
                drive(&eal, &args, &slot, base, &mut FrameReplies::new(&eal, &port, queues))?
            }
            ReplyPath::Ring => {
                let bytes = REPLY_CELLS * 64;
                let ring = Memzone::reserve_contiguous(&eal, "inline_replies", bytes, socket, bytes as u32)?;
                // SAFETY: the memzone is ours and that long.
                unsafe { ring.as_ptr().write_bytes(0, bytes) };
                slot.check()?;
                slot.write_u64(REPLY_RING_OFFSET, ring.iova());
                sfence();
                let mut replies = RingReplies::new(&eal, &ring, base, cores);
                if args.stamp {
                    replies = replies.stamped(args.stamp_sparse);
                }
                let mut sync_conn = if args.pmu {
                    let s = TcpStream::connect(&args.sync)?;
                    s.set_nodelay(true)?;
                    let r = BufReader::new(s.try_clone()?);
                    Some((s, r))
                } else if args.stamp {
                    TcpStream::connect(&args.sync).ok().and_then(|s| {
                        let _ = s.set_nodelay(true);
                        s.try_clone().ok().map(|c| (s, BufReader::new(c)))
                    })
                } else {
                    None
                };
                let sync_before = if args.stamp {
                    Some(sync_clock_bracketed(&slot, &mut sync_conn)?)
                } else {
                    None
                };
                let host_pmu = if args.pmu {
                    let cpu = unsafe { libc::sched_getcpu() };
                    Some(HostPmu::open(cpu)?)
                } else {
                    None
                };
                let mut pmu_s0 = None;
                let mut pmu_s1 = None;
                let mut t0 = Instant::now();
                let outcome = drive_bracketed(
                    &eal,
                    &args,
                    &slot,
                    base,
                    &mut replies,
                    |starting| {
                        if starting {
                            if args.pmu {
                                if let Some((w, r)) = &mut sync_conn {
                                    let resp = sync_cmd(w, r, "PMU_START")?;
                                    if resp != "OK" {
                                        return Err(format!("card PMU_START failed: {resp}").into());
                                    }
                                }
                            }
                            pmu_s0 = host_pmu.as_ref().map(HostPmu::snapshot);
                            t0 = Instant::now();
                        } else {
                            wall_s = t0.elapsed().as_secs_f64();
                            pmu_s1 = host_pmu.as_ref().map(HostPmu::snapshot);
                            if args.pmu {
                                if let Some((w, r)) = &mut sync_conn {
                                    card_pmu_json = sync_cmd(w, r, "PMU_STOP")?;
                                }
                            }
                        }
                        Ok(())
                    },
                );
                if let (Some(s0), Some(s1)) = (pmu_s0, pmu_s1) {
                    host_pmu_json = format_host_pmu(&s0, &s1, wall_s);
                }
                if let Some(sb) = sync_before {
                    let sa = sync_clock_bracketed(&slot, &mut sync_conn)?;
                    clock_sync = Some((sb, sa));
                }
                blocked = replies.blocked();
                println!(
                    "reply ring at IOVA {:#x}: {} polls waited on a missing reply while a later one had landed",
                    ring.iova(),
                    blocked
                );
                if !slot.revoked() {
                    slot.write_u64(REPLY_RING_OFFSET, 0);
                }
                std::thread::sleep(Duration::from_millis(10));
                outcome?
            }
            ReplyPath::Pull => {
                slot.write_u64(DONE_OFFSET, 0);
                slot.write_u64(REPLY_RING_OFFSET, REPLY_PULL);
                sfence();
                let delay = eal.tsc_hz() * args.pull_delay_ns / 1_000_000_000;
                let outcome = drive(&eal, &args, &slot, base, &mut PullReplies { slot: &slot, next: 1, delay, armed: true });
                if !slot.revoked() {
                    slot.write_u64(REPLY_RING_OFFSET, 0);
                }
                outcome?
            }
        };
        if let Some(csv_path) = &args.stamp_csv {
            write_stamp_csv(csv_path, eal.tsc_hz(), clock_sync, &outcome.records)?;
        }
        let mode = if args.fence { "movdir64b+sfence" } else { "movdir64b" };
        let reply = match args.reply {
            ReplyPath::Sdp => "sdp".to_owned(),
            ReplyPath::Ring => "ring".to_owned(),
            ReplyPath::Pull => format!("pull delay {} ns", args.pull_delay_ns),
        };
        println!("inline {mode} reply {reply} {outcome}");
        let ((sb_t, sb_c, sb_rtt), (sa_t, sa_c, sa_rtt)) = clock_sync.unwrap_or(((0, 0, 0), (0, 0, 0)));
        println!(
            "MRV75 {{\"window\":{},\"cores\":{},\"rounds\":{},\"warmup\":{},\"measured\":{},\
             \"lost\":{},\"bad\":{},\"torn\":{},\"late\":{},\"late_max_us\":{:.4},\
             \"msg_per_s\":{:.3},\"mops\":{:.6},\"ns_per_msg\":{:.4},\"wall_s\":{:.6},\
             \"tsc_hz\":{},\"polls\":{},\"empty_polls\":{},\"send_batches\":{},\"blocked\":{},\
             \"rtt_min_us\":{:.4},\"rtt_p50_us\":{:.4},\"rtt_p90_us\":{:.4},\"rtt_p99_us\":{:.4},\"rtt_mean_us\":{:.4},\
             \"max_send_burst\":{},\"send_gap_ns\":{},\"stamp\":{},\"stamp_sparse\":{},\
             \"clock_sync\":{{\"sb_t\":{},\"sb_c\":{},\"sb_rtt\":{},\"sa_t\":{},\"sa_c\":{},\"sa_rtt\":{},\"records\":{}}},\
             \"host_pmu\":{},\"card_pmu\":{}}}",
            outcome.window,
            cores,
            args.rounds,
            args.warmup,
            outcome.measured,
            outcome.lost,
            outcome.bad,
            outcome.torn,
            outcome.late,
            outcome.late_max_us,
            outcome.msg_per_s,
            outcome.msg_per_s / 1e6,
            1e9 / outcome.msg_per_s,
            wall_s,
            eal.tsc_hz(),
            outcome.polls,
            outcome.empty_polls,
            outcome.send_batches,
            blocked,
            outcome.latency.quantile(0.0),
            outcome.latency.quantile(0.5),
            outcome.latency.quantile(0.9),
            outcome.latency.quantile(0.99),
            outcome.latency.mean(),
            args.max_send_burst,
            args.send_gap_ns,
            args.stamp,
            args.stamp_sparse,
            sb_t,
            sb_c,
            sb_rtt,
            sa_t,
            sa_c,
            sa_rtt,
            outcome.records.len(),
            host_pmu_json,
            card_pmu_json,
        );
        outcome.check()?;
        Ok(())
    }

    /// Uncached reads of the control cell: the cost of one host -> card ->
    /// host round trip through PEM to card memory.
    fn probe_reads(eal: &Eal, slot: &LeasedSlot, n: u64) -> Result<(), Box<dyn Error>> {
        let (mut narrow, mut wide) = (Vec::with_capacity(n as usize), Vec::with_capacity(n as usize));
        for _ in 0..n {
            slot.check()?;
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
        slot: &'s LeasedSlot<'s>,
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
            if self.slot.revoked() {
                return 0; // the closed loop stops at its next request
            }
            let [tag, tsc] = self.slot.read_16(DONE_OFFSET);
            let at = rdtsc_after_loads();
            if tag < self.next {
                return 0;
            }
            on_reply(Reply {
                seq: tag,
                tsc,
                at,
                integrity: Integrity::Whole,
                card_first_poll_ts: 0,
                card_ts: 0,
                card_out_ts: 0,
                card_write_ticks: 0,
                card_waited_polls: 0,
                card_core: 0,
                card_ring_idx: 0,
                waited_polls: 0,
            });
            self.next = tag + 1;
            self.armed = true;
            1
        }
    }

    fn drive(
        eal: &Eal,
        args: &Args,
        slot: &LeasedSlot,
        base: u64,
        replies: &mut impl Replies,
    ) -> Result<Outcome, Box<dyn Error>> {
        drive_bracketed(eal, args, slot, base, replies, |_| Ok(()))
    }

    fn drive_bracketed(
        eal: &Eal,
        args: &Args,
        slot: &LeasedSlot,
        base: u64,
        replies: &mut impl Replies,
        on_phase: impl FnMut(bool) -> Result<(), Box<dyn Error>>,
    ) -> Result<Outcome, Box<dyn Error>> {
        let run = ClosedLoop { rounds: args.rounds, window: args.window, warmup: args.warmup };
        let send_gap_ticks = if args.send_gap_ns > 0 {
            ((args.send_gap_ns as u128 * eal.tsc_hz() as u128 + 500_000_000) / 1_000_000_000) as u64
        } else {
            0
        };
        let opts = RunOpts {
            max_send_burst: args.max_send_burst,
            send_gap_ticks,
            record_cap: args.record_cap,
            record_skip: args.record_skip,
        };
        run.run_bracketed(eal, replies, opts, on_phase, |seq, tsc| {
            slot.check()?;
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

    /// BAR4 UC 8B read clock alignment: take 64 samples, return minimum-RTT (host_mid, card_ts, rtt_ticks).
    fn sync_clock(slot: &LeasedSlot) -> Result<(u64, u64, u64), Box<dyn Error>> {
        let mut best = (0u64, 0u64, u64::MAX);
        for _ in 0..64 {
            slot.check()?;
            let t0 = rdtsc_after_loads();
            let card_c = slot.read_u64(CLOCK_OFFSET);
            let t1 = rdtsc_after_loads();
            let rtt = t1.wrapping_sub(t0);
            if card_c > 0 && rtt < best.2 {
                best = (t0.wrapping_add(rtt / 2), card_c, rtt);
            }
        }
        if best.2 == u64::MAX {
            return Err("sync_clock: card CLOCK_OFFSET is 0 (is card running --exp-stamp?)".into());
        }
        Ok(best)
    }

    /// Bracket `sync_clock` with `CLOCK_SPIN_START`/`CLOCK_SPIN_STOP` on the card sync
    /// socket when available so `CLOCK_OFFSET` stays fresh even when ring cores sleep in WFE.
    fn sync_clock_bracketed(
        slot: &LeasedSlot,
        sync_conn: &mut Option<(TcpStream, BufReader<TcpStream>)>,
    ) -> Result<(u64, u64, u64), Box<dyn Error>> {
        let mut started = false;
        if let Some((w, r)) = sync_conn.as_mut() {
            if let Ok(resp) = sync_cmd(w, r, "CLOCK_SPIN_START") {
                started = resp == "OK";
            }
        }
        std::thread::sleep(Duration::from_millis(2));
        let res = sync_clock(slot);
        if started {
            if let Some((w, r)) = sync_conn.as_mut() {
                let _ = sync_cmd(w, r, "CLOCK_SPIN_STOP");
            }
        }
        res
    }

    fn write_stamp_csv(
        path: &str,
        tsc_hz: u64,
        clock_sync: Option<((u64, u64, u64), (u64, u64, u64))>,
        records: &[[u64; 11]],
    ) -> Result<(), Box<dyn Error>> {
        let f = File::create(path)?;
        let mut w = BufWriter::new(f);
        let ((sb_t, sb_c, sb_rtt), (sa_t, sa_c, sa_rtt)) = clock_sync.unwrap_or(((0, 0, 0), (0, 0, 0)));
        writeln!(w, "# tsc_hz={tsc_hz} sb={sb_t},{sb_c},{sb_rtt} sa={sa_t},{sa_c},{sa_rtt}")?;
        writeln!(
            w,
            "seq,send_tsc,seen_tsc,card_t_first_poll,card_t_in,card_t_out,card_write_ticks,card_waited_polls,card_core,card_ring_idx,host_waited_polls"
        )?;
        for r in records {
            writeln!(
                w,
                "{},{},{},{},{},{},{},{},{},{},{}",
                r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]
            )?;
        }
        w.flush()?;
        Ok(())
    }

    fn sync_cmd(
        writer: &mut TcpStream,
        reader: &mut BufReader<TcpStream>,
        cmd: &str,
    ) -> Result<String, Box<dyn Error>> {
        writer.write_all(cmd.as_bytes())?;
        writer.write_all(b"\n")?;
        writer.flush()?;
        let mut resp = String::new();
        reader.read_line(&mut resp)?;
        Ok(resp.trim().to_string())
    }

    // ---- Direct Host Core + Socket-1 Uncore PMU (zero multiplexing) ----
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const REF_HZ: f64 = 2.2e9;

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

    const PERF_TYPE_HARDWARE: u32 = 0;
    const PERF_TYPE_HW_CACHE: u32 = 3;
    const PERF_TYPE_RAW: u32 = 4;
    const PERF_COUNT_HW_CPU_CYCLES: u64 = 0;
    const PERF_COUNT_HW_INSTRUCTIONS: u64 = 1;
    const PERF_COUNT_HW_REF_CPU_CYCLES: u64 = 9;
    const CORE_RESOURCE_STALLS_SB: u64 = 0x08a2;
    const CORE_L1D_PEND_MISS_FB_FULL: u64 = 0x0248;
    const CORE_LLC_LOAD_MISSES: u64 = 0x10002;

    const CHA_TOR_INSERTS_IA_WCILF: u64 = 0x00c8_67ff_0000_0135;
    const CHA_TOR_OCCUPANCY_IA_WCILF: u64 = 0x00c8_67ff_0000_0136;
    const CHA_TOR_INSERTS_IO_HIT_ITOM: u64 = 0x00cc_43fd_0000_0435;
    const CHA_TOR_INSERTS_IO_MISS_ITOM: u64 = 0x00cc_43fe_0000_0435;
    const UNCORE_CLOCKTICKS: u64 = 0x01;
    const IIO_TXN_REQ_BY_CPU_MEM_WRITE: u64 = 0x0007_0ff0_0000_01c1;
    const IIO_DATA_REQ_OF_CPU_MEM_WRITE: u64 = 0x0007_0ff0_0000_0184;
    const IRP_TXS_REQUEST_OCCUPANCY: u64 = 0x0c;
    const IRP_TXS_DATA_INSERTS_NCS: u64 = 0x0e;

    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    struct PerfCounter {
        fd: RawFd,
    }

    #[derive(Clone, Copy, Default)]
    struct Sample {
        val: u64,
        enabled_ns: u64,
        running_ns: u64,
    }

    impl Sample {
        fn delta(self, earlier: Self) -> Self {
            Sample {
                val: self.val.saturating_sub(earlier.val),
                enabled_ns: self.enabled_ns.saturating_sub(earlier.enabled_ns),
                running_ns: self.running_ns.saturating_sub(earlier.running_ns),
            }
        }
        fn pct(self) -> f64 {
            if self.enabled_ns == 0 {
                0.0
            } else {
                (self.running_ns as f64 / self.enabled_ns as f64) * 100.0
            }
        }
    }

    impl PerfCounter {
        fn open(type_: u32, config: u64, cpu: i32) -> io::Result<Self> {
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
            let c = PerfCounter { fd: fd as RawFd };
            let rc = unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) };
            if rc != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> Sample {
            let mut buf = [0u64; 3];
            let n = unsafe {
                libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>())
            };
            assert_eq!(n, size_of::<[u64; 3]>() as isize, "short read on perf fd");
            Sample { val: buf[0], enabled_ns: buf[1], running_ns: buf[2] }
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

    fn read_pmu_type(name: &str) -> Result<u32, Box<dyn Error>> {
        let p = format!("/sys/bus/event_source/devices/{name}/type");
        let s = fs::read_to_string(&p).map_err(|e| format!("read {p}: {e}"))?;
        Ok(s.trim().parse::<u32>()?)
    }

    struct HostPmu {
        cpu: i32,
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        instr: PerfCounter,
        sb_stall: PerfCounter,
        fb_full: PerfCounter,
        llc_load_misses: PerfCounter,
        cha_ins: Vec<PerfCounter>,
        cha_occ: Vec<PerfCounter>,
        cha_io_hit: Vec<PerfCounter>,
        cha_io_miss: Vec<PerfCounter>,
        mesh_clk: PerfCounter,
        irp1_occ: PerfCounter,
        irp1_ins: PerfCounter,
        iio1_cpu_wr: PerfCounter,
        iio1_inb_wr: PerfCounter,
        iio1_clk: PerfCounter,
    }

    struct HostSnap {
        cpu: i32,
        cycles: Sample,
        ref_cycles: Sample,
        instr: Sample,
        sb_stall: Sample,
        fb_full: Sample,
        llc_load_misses: Sample,
        cha_ins: Vec<Sample>,
        cha_occ: Vec<Sample>,
        cha_io_hit: Vec<Sample>,
        cha_io_miss: Vec<Sample>,
        mesh_clk: Sample,
        irp1_occ: Sample,
        irp1_ins: Sample,
        iio1_cpu_wr: Sample,
        iio1_inb_wr: Sample,
        iio1_clk: Sample,
    }

    impl HostPmu {
        fn open(cpu: i32) -> Result<Self, Box<dyn Error>> {
            let cycles = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu)?;
            let ref_cycles = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, cpu)?;
            let instr = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu)?;
            let sb_stall = PerfCounter::open(PERF_TYPE_RAW, CORE_RESOURCE_STALLS_SB, cpu)?;
            let fb_full = PerfCounter::open(PERF_TYPE_RAW, CORE_L1D_PEND_MISS_FB_FULL, cpu)?;
            let llc_load_misses = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOAD_MISSES, cpu)?;

            let (mut cha_ins, mut cha_occ, mut cha_io_hit, mut cha_io_miss) =
                (Vec::with_capacity(32), Vec::with_capacity(32), Vec::with_capacity(32), Vec::with_capacity(32));
            for i in 0..32 {
                let t = read_pmu_type(&format!("uncore_cha_{i}"))?;
                cha_ins.push(PerfCounter::open(t, CHA_TOR_INSERTS_IA_WCILF, SOCKET1_UNCORE_CPU)?);
                cha_occ.push(PerfCounter::open(t, CHA_TOR_OCCUPANCY_IA_WCILF, SOCKET1_UNCORE_CPU)?);
                cha_io_hit.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_HIT_ITOM, SOCKET1_UNCORE_CPU)?);
                cha_io_miss.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_MISS_ITOM, SOCKET1_UNCORE_CPU)?);
            }
            let mesh_clk = PerfCounter::open(read_pmu_type("uncore_m2pcie_1")?, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let irp1_t = read_pmu_type("uncore_irp_1")?;
            let irp1_occ = PerfCounter::open(irp1_t, IRP_TXS_REQUEST_OCCUPANCY, SOCKET1_UNCORE_CPU)?;
            let irp1_ins = PerfCounter::open(irp1_t, IRP_TXS_DATA_INSERTS_NCS, SOCKET1_UNCORE_CPU)?;
            let iio1_t = read_pmu_type("uncore_iio_1")?;
            let iio1_cpu_wr = PerfCounter::open(iio1_t, IIO_TXN_REQ_BY_CPU_MEM_WRITE, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_wr = PerfCounter::open(iio1_t, IIO_DATA_REQ_OF_CPU_MEM_WRITE, SOCKET1_UNCORE_CPU)?;
            let iio1_clk = PerfCounter::open(iio1_t, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            Ok(HostPmu {
                cpu,
                cycles,
                ref_cycles,
                instr,
                sb_stall,
                fb_full,
                llc_load_misses,
                cha_ins,
                cha_occ,
                cha_io_hit,
                cha_io_miss,
                mesh_clk,
                irp1_occ,
                irp1_ins,
                iio1_cpu_wr,
                iio1_inb_wr,
                iio1_clk,
            })
        }

        fn snapshot(&self) -> HostSnap {
            HostSnap {
                cpu: self.cpu,
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                instr: self.instr.read(),
                sb_stall: self.sb_stall.read(),
                fb_full: self.fb_full.read(),
                llc_load_misses: self.llc_load_misses.read(),
                cha_ins: self.cha_ins.iter().map(PerfCounter::read).collect(),
                cha_occ: self.cha_occ.iter().map(PerfCounter::read).collect(),
                cha_io_hit: self.cha_io_hit.iter().map(PerfCounter::read).collect(),
                cha_io_miss: self.cha_io_miss.iter().map(PerfCounter::read).collect(),
                mesh_clk: self.mesh_clk.read(),
                irp1_occ: self.irp1_occ.read(),
                irp1_ins: self.irp1_ins.read(),
                iio1_cpu_wr: self.iio1_cpu_wr.read(),
                iio1_inb_wr: self.iio1_inb_wr.read(),
                iio1_clk: self.iio1_clk.read(),
            }
        }
    }

    fn format_host_pmu(s0: &HostSnap, s1: &HostSnap, wall_s: f64) -> String {
        let mut min_pct = 100.0f64;
        let mut chk = |s: Sample| {
            min_pct = min_pct.min(s.pct());
            s.val
        };
        let cyc = chk(s1.cycles.delta(s0.cycles));
        let refc = chk(s1.ref_cycles.delta(s0.ref_cycles));
        let ins = chk(s1.instr.delta(s0.instr));
        let sb = chk(s1.sb_stall.delta(s0.sb_stall));
        let fb = chk(s1.fb_full.delta(s0.fb_full));
        let llc_m = chk(s1.llc_load_misses.delta(s0.llc_load_misses));

        let mut cha_ins_sum = 0u64;
        let mut cha_occ_sum = 0u64;
        let mut cha_hit_sum = 0u64;
        let mut cha_miss_sum = 0u64;
        let mut active_chas = 0u32;
        for i in 0..32 {
            let ci = chk(s1.cha_ins[i].delta(s0.cha_ins[i]));
            let co = chk(s1.cha_occ[i].delta(s0.cha_occ[i]));
            let ch = chk(s1.cha_io_hit[i].delta(s0.cha_io_hit[i]));
            let cm = chk(s1.cha_io_miss[i].delta(s0.cha_io_miss[i]));
            if ci > 1000 {
                active_chas += 1;
            }
            cha_ins_sum += ci;
            cha_occ_sum += co;
            cha_hit_sum += ch;
            cha_miss_sum += cm;
        }
        let mesh_clk = chk(s1.mesh_clk.delta(s0.mesh_clk));
        let irp1_occ = chk(s1.irp1_occ.delta(s0.irp1_occ));
        let irp1_ins = chk(s1.irp1_ins.delta(s0.irp1_ins));
        let iio1_cpu_wr = chk(s1.iio1_cpu_wr.delta(s0.iio1_cpu_wr));
        let iio1_inb_wr = chk(s1.iio1_inb_wr.delta(s0.iio1_inb_wr));
        let iio1_clk = chk(s1.iio1_clk.delta(s0.iio1_clk));

        let ghz = if refc > 0 { cyc as f64 / refc as f64 * (REF_HZ / 1e9) } else { 0.0 };
        let sb_pct = if cyc > 0 { sb as f64 * 100.0 / cyc as f64 } else { 0.0 };
        let fb_pct = if cyc > 0 { fb as f64 * 100.0 / cyc as f64 } else { 0.0 };
        let mesh_ghz = if wall_s > 0.0 { mesh_clk as f64 / wall_s / 1e9 } else { 0.0 };
        let cha_inflight = if mesh_clk > 0 { cha_occ_sum as f64 / mesh_clk as f64 } else { 0.0 };
        let cha_res_ns = if cha_ins_sum > 0 && mesh_ghz > 0.0 {
            (cha_occ_sum as f64 / cha_ins_sum as f64) / mesh_ghz
        } else {
            0.0
        };
        let irp1_inflight_nom = if wall_s > 0.0 { irp1_occ as f64 / (wall_s * 1e9) } else { 0.0 };
        let irp1_inflight_clk = if iio1_clk > 0 { irp1_occ as f64 / iio1_clk as f64 } else { 0.0 };
        let iio1_clk_ghz = if wall_s > 0.0 { iio1_clk as f64 / wall_s / 1e9 } else { 0.0 };

        format!(
            "{{\"cpu\":{},\"min_pct\":{:.2},\"cycles\":{},\"ref_cycles\":{},\"instr\":{},\
             \"ghz\":{:.4},\"sb_stall\":{},\"sb_pct\":{:.3},\"fb_full\":{},\"fb_pct\":{:.3},\
             \"llc_load_misses\":{},\"active_chas\":{},\"cha_ins_wcilf\":{},\"cha_occ_wcilf\":{},\
             \"cha_inflight\":{:.4},\"cha_res_ns\":{:.2},\"cha_io_hit_itom\":{},\"cha_io_miss_itom\":{},\
             \"mesh_clk\":{},\"mesh_ghz\":{:.4},\"irp1_occ\":{},\"irp1_ins\":{},\
             \"irp1_inflight_nom\":{:.4},\"irp1_inflight_clk\":{:.4},\
             \"iio1_cpu_wr\":{},\"iio1_inb_wr\":{},\"iio1_clk\":{},\"iio1_clk_ghz\":{:.4}}}",
            s1.cpu, min_pct, cyc, refc, ins, ghz, sb, sb_pct, fb, fb_pct, llc_m,
            active_chas, cha_ins_sum, cha_occ_sum, cha_inflight, cha_res_ns,
            cha_hit_sum, cha_miss_sum, mesh_clk, mesh_ghz,
            irp1_occ, irp1_ins, irp1_inflight_nom, irp1_inflight_clk,
            iio1_cpu_wr, iio1_inb_wr, iio1_clk, iio1_clk_ghz
        )
    }
}
