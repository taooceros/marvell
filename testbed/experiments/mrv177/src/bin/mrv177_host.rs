//! Host-side benchmark for MRV-177:
//! Single-variable three-arm comparison on fixed CN106 BAR4 64 B direct push:
//!   - Baseline (`--doorbell none` + card `baseline`)
//!   - Arm A (`--doorbell none` + card `bqueue`)
//!   - Arm B (`--doorbell per-pkt|adaptive` + `--db-sfence true|false` + card `doorbell`)
//!
//! Workloads:
//!   - `--mode closed`: closed-loop W in {1, 2, 32, 64, 128} (optional `--credit-cap`)
//!   - `--mode open`: open-loop Poisson 10-25 M/s (optional `--credit-cap`)
//!   - `--mode burst-tail`: repeated `burst_len=32` followed by `tail_len=1..2` tail packets
//!     (either immediate residual tail `--tail-wait-drain false --tail-gap-ns 0`, or
//!      post-pause isolated tail `--tail-wait-drain true --tail-gap-ns 5000`), recording
//!     burst-body RTT and tail-packet RTT in separate verified distributions.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv177_host runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::{MaybeUninit, size_of};
    use std::net::TcpStream;
    use std::os::fd::RawFd;
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Eal, Memzone, Port, rdtsc, rdtsc_after_loads};
    use testbed::Latency;
    use testbed::closed_loop::{Integrity, Reply, TAIL_WAIT_US};
    use testbed::inline::{
        CORES_OFFSET, Cell, MAX_CORES, REPLY_CELLS, REPLY_RING_OFFSET, Slot, next_offset,
    };
    use testbed::lease::{self, Lease, LeasedSlot};
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};

    const PORT: u16 = 0;
    const RING_CELLS_177: usize = 65472;
    const DOORBELL_OFFSET: usize = 0x3ff800;
    const PENDING: usize = 8192;
    const LOOKAHEAD: u64 = 8;
    const MAX_DRAIN: usize = 32;

    #[inline(always)]
    fn cell_offset_177(seq: u64) -> usize {
        ((seq - 1) as usize % RING_CELLS_177) * 64
    }

    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct DoorbellCell {
        prod_idx: u64,
        pad: [u64; 6],
        prod_tail: u64,
    }
    const _: () = assert!(size_of::<DoorbellCell>() == 64);

    #[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
    enum WorkloadMode {
        Closed,
        Open,
        BurstTail,
    }

    #[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
    enum DoorbellMode {
        None,
        PerPkt,
        Adaptive,
    }

    #[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
    enum ArrivalArg {
        Paced,
        Poisson,
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = WorkloadMode::Closed)]
        mode: WorkloadMode,
        #[arg(long, short = 'n', default_value_t = 1_000_000)]
        rounds: u64,
        #[arg(long, default_value_t = 20_000)]
        warmup: u64,
        #[arg(long, short = 'w', default_value_t = 1, value_parser = clap::value_parser!(u64).range(1..=1024))]
        window: u64,
        /// Open-loop offered rate in Mmsg/s.
        #[arg(long, default_value_t = 10.0)]
        open_mps: f64,
        #[arg(long, value_enum, default_value_t = ArrivalArg::Poisson)]
        arrival: ArrivalArg,
        #[arg(long, default_value_t = 177)]
        seed: u64,
        /// Optional host hardware in-flight credit cap (0 = unlimited: window in closed, 4032 in open).
        #[arg(long, default_value_t = 0)]
        credit_cap: u64,

        /// Burst-tail parameters.
        #[arg(long, default_value_t = 32)]
        burst_len: u64,
        #[arg(long, default_value_t = 2)]
        tail_len: u64,
        /// Pause in ns between burst and tail packets.
        #[arg(long, default_value_t = 0)]
        tail_gap_ns: u64,
        /// Whether to wait for all `burst_len` replies to drain before starting `tail_gap_ns`.
        #[arg(long, default_value_t = false, action = clap::ArgAction::Set)]
        tail_wait_drain: bool,
        /// Idle pause in ns between burst-tail cycles so each cycle starts truly idle.
        #[arg(long, default_value_t = 10_000)]
        cycle_idle_ns: u64,
        /// Measured cycles in burst-tail mode.
        #[arg(long, default_value_t = 20_000)]
        cycles: u64,
        #[arg(long, default_value_t = 1_000)]
        warmup_cycles: u64,

        /// Doorbell publication mode (Arm B).
        #[arg(long, value_enum, default_value_t = DoorbellMode::None)]
        doorbell: DoorbellMode,
        /// Issue SFENCE before writing the doorbell cell (ordered publication).
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        db_sfence: bool,
        /// Adaptive doorbell batch threshold (packets per doorbell under load).
        #[arg(long, default_value_t = 16)]
        db_batch: u64,
        /// Adaptive doorbell light-load immediate threshold (inflight <= db_light rings immediately).
        #[arg(long, default_value_t = 4)]
        db_light: u64,
        /// Adaptive doorbell max holdback flush timeout in ns.
        #[arg(long, default_value_t = 200)]
        db_flush_ns: u64,

        /// PF BAR4 sysfs path.
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar: String,
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card: String,
        #[arg(long, default_value = "10.10.0.2:7527")]
        sync: String,
        #[arg(long)]
        pmu: bool,
    }

    struct Mrv177RingReplies<'m, 'e> {
        ring: &'m Memzone<'e>,
        base: u64,
        cores: usize,
        next: u64,
        partial_since: Option<u64>,
        settle: u64,
        blocked: u64,
    }

    impl<'m, 'e> Mrv177RingReplies<'m, 'e> {
        fn new(eal: &Eal, ring: &'m Memzone<'e>, base: u64, cores: usize) -> Self {
            Mrv177RingReplies {
                ring,
                base,
                cores,
                next: 1,
                partial_since: None,
                settle: eal.tsc_hz() / 1_000_000 * TAIL_WAIT_US,
                blocked: 0,
            }
        }

        #[inline(always)]
        fn cell(&self, k: u64) -> *mut Cell {
            unsafe { self.ring.as_ptr().add(Cell::reply_offset(k, self.cores)) }.cast()
        }

        fn poll(&mut self, mut on_reply: impl FnMut(Reply)) -> usize {
            let mut batch = [MaybeUninit::<(u64, u64, Integrity)>::uninit(); MAX_DRAIN];
            let mut n = 0;
            while n < MAX_DRAIN {
                let seq = self.base + self.next;
                let cell = match unsafe { Cell::poll(self.cell(self.next), seq) } {
                    Slot::Empty => {
                        let later_landed = self.cores > 1
                            && (1..=LOOKAHEAD)
                                .any(|d| unsafe { self.cell(self.next + d).cast::<u64>().read_volatile() == seq + d });
                        self.blocked += u64::from(later_landed);
                        break;
                    }
                    Slot::Partial => None,
                    Slot::Complete(cell) => Some(cell),
                };
                let complete = cell.is_some_and(|c| c.tag == self.next && c.payload_matches(seq));
                if !complete {
                    let now = rdtsc_after_loads();
                    if now.saturating_sub(*self.partial_since.get_or_insert(now)) < self.settle {
                        break;
                    }
                }
                let integrity = match (cell, self.partial_since.take()) {
                    (None, _) => Integrity::Torn,
                    (Some(_), Some(since)) => Integrity::LateTail(rdtsc_after_loads().saturating_sub(since)),
                    (Some(_), None) => Integrity::Whole,
                };
                let tsc = cell.filter(|_| complete).map_or(0, |c| c.tsc);
                batch[n].write((self.next, tsc, integrity));
                self.next += 1;
                n += 1;
            }
            if n > 0 {
                let now = rdtsc_after_loads();
                for slot in &batch[..n] {
                    let (seq, tsc, integrity) = unsafe { slot.assume_init() };
                    on_reply(Reply { seq, tsc, at: now, integrity });
                }
            }
            n
        }
    }

    struct DbEmitter {
        mode: DoorbellMode,
        sfence_before: bool,
        batch: u64,
        light: u64,
        flush_ticks: u64,
        last_db_sent: u64,
        last_db_tsc: u64,
        rings_total: u64,
        rings_measured: u64,
        measured_active: bool,
    }

    impl DbEmitter {
        fn new(args: &Args, hz: u64) -> Self {
            let flush_ticks = ((args.db_flush_ns as u128 * hz as u128 + 500_000_000) / 1_000_000_000) as u64;
            DbEmitter {
                mode: args.doorbell,
                sfence_before: args.db_sfence,
                batch: args.db_batch.max(1),
                light: args.db_light,
                flush_ticks,
                last_db_sent: 0,
                last_db_tsc: 0,
                rings_total: 0,
                rings_measured: 0,
                measured_active: false,
            }
        }

        #[inline(always)]
        fn ring_now(&mut self, slot: &LeasedSlot, base: u64, sent: u64, now: u64) -> Result<(), Box<dyn Error>> {
            slot.check()?;
            if self.sfence_before {
                sfence();
            }
            let prod = base + sent;
            let cell = DoorbellCell { prod_idx: prod, pad: [0; 6], prod_tail: prod };
            unsafe { movdir64b(slot.as_ptr().add(DOORBELL_OFFSET), &cell) };
            self.last_db_sent = sent;
            self.last_db_tsc = now;
            self.rings_total += 1;
            if self.measured_active {
                self.rings_measured += 1;
            }
            Ok(())
        }

        #[inline(always)]
        fn on_cell_sent(&mut self, slot: &LeasedSlot, base: u64, sent: u64, now: u64) -> Result<(), Box<dyn Error>> {
            match self.mode {
                DoorbellMode::None => Ok(()),
                DoorbellMode::PerPkt => self.ring_now(slot, base, sent, now),
                DoorbellMode::Adaptive => {
                    if sent - self.last_db_sent >= self.batch {
                        self.ring_now(slot, base, sent, now)?;
                    }
                    Ok(())
                }
            }
        }

        #[inline(always)]
        fn maybe_flush(
            &mut self,
            slot: &LeasedSlot,
            base: u64,
            sent: u64,
            done: u64,
            force: bool,
            now: u64,
        ) -> Result<(), Box<dyn Error>> {
            if self.mode != DoorbellMode::Adaptive || sent <= self.last_db_sent {
                return Ok(());
            }
            let inflight = sent - done;
            let unrung = sent - self.last_db_sent;
            let should_ring = force
                || unrung >= self.batch
                || inflight <= self.light
                || self.last_db_sent <= done
                || (self.flush_ticks > 0 && now.saturating_sub(self.last_db_tsc) >= self.flush_ticks);
            if should_ring {
                self.ring_now(slot, base, sent, now)?;
            }
            Ok(())
        }
    }

    fn pretouch_u64(cap: usize) -> Vec<u64> {
        let mut v = vec![1u64; cap];
        std::hint::black_box(v.as_mut_ptr());
        v.clear();
        v
    }

    fn lat_json(l: &Latency) -> String {
        format!(
            "{{\"min\":{:.4},\"p50\":{:.4},\"p90\":{:.4},\"p95\":{:.4},\"p99\":{:.4},\"p999\":{:.4},\"max\":{:.4},\"mean\":{:.4}}}",
            l.quantile(0.0),
            l.quantile(0.5),
            l.quantile(0.9),
            l.quantile(0.95),
            l.quantile(0.99),
            l.quantile(0.999),
            l.quantile(1.0),
            l.mean(),
        )
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

    #[inline(always)]
    fn submit_cell(slot: &LeasedSlot, base: u64, seq: u64, tsc: u64) -> Result<(), Box<dyn Error>> {
        slot.check()?;
        let cell = Cell::new(base + seq, seq, tsc);
        unsafe { movdir64b(slot.as_ptr().add(cell_offset_177(base + seq)), &cell) };
        Ok(())
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let (eal, app_args) = Eal::init(std::env::args())?;
        let args = Args::parse_from(app_args);
        if !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }

        let lease = Lease::acquire(&args.card)?;
        let slot = lease.map_slot(&args.bar)?;
        let cores = slot.read_u64(CORES_OFFSET) as usize;
        if !(1..=MAX_CORES).contains(&cores) {
            return Err(format!("card publishes {cores} cores").into());
        }
        let base = (0..cores)
            .map(|core| slot.read_u64(next_offset(core)))
            .min()
            .and_then(|next| next.checked_sub(1))
            .ok_or("card published no next sequence")?;

        let socket = Port::socket_of(&eal, PORT);
        let bytes = REPLY_CELLS * 64;
        let ring = Memzone::reserve_contiguous(&eal, "mrv177_replies", bytes, socket, bytes as u32)?;
        unsafe { ring.as_ptr().write_bytes(0, bytes) };
        slot.check()?;
        slot.write_u64(REPLY_RING_OFFSET, ring.iova());
        sfence();

        let hz = eal.tsc_hz();

        // Prime every card core once before PMU/measurement so every core's
        // StoreReplies::retarget (PEM0 outbound CSR write + DSB) finishes before
        // t_origin / PMU_START.
        {
            let mut prime_replies = Mrv177RingReplies::new(&eal, &ring, base, cores);
            let prime_n = cores as u64;
            for seq in 1..=prime_n {
                submit_cell(&slot, base, seq, 1)?;
            }
            if args.doorbell != DoorbellMode::None {
                sfence();
                let prod = base + prime_n;
                let db_cell = DoorbellCell { prod_idx: prod, pad: [0; 6], prod_tail: prod };
                unsafe { movdir64b(slot.as_ptr().add(DOORBELL_OFFSET), &db_cell) };
            }
            let mut prime_done = 0u64;
            let t_prime = rdtsc();
            while prime_done < prime_n {
                prime_replies.poll(|r| {
                    if r.seq >= 1
                        && r.seq <= prime_n
                        && r.tsc == 1
                        && matches!(r.integrity, Integrity::Whole | Integrity::LateTail(_))
                    {
                        prime_done += 1;
                    }
                });
                if rdtsc().saturating_sub(t_prime) > hz {
                    return Err("priming round-trip timed out".into());
                }
            }
            // Clear the primed reply slots so the real run starts from a fresh ring at new base.
            unsafe { ring.as_ptr().write_bytes(0, bytes) };
            sfence();
        }
        let base = base + cores as u64;

        let mut replies = Mrv177RingReplies::new(&eal, &ring, base, cores);
        let host_pmu = if args.pmu {
            let cpu = unsafe { libc::sched_getcpu() };
            Some(HostPmu::open(cpu)?)
        } else {
            None
        };
        let mut sync_conn = if args.pmu {
            let s = TcpStream::connect(&args.sync)?;
            s.set_nodelay(true)?;
            let r = BufReader::new(s.try_clone()?);
            Some((s, r))
        } else {
            None
        };

        let mut db = DbEmitter::new(&args, hz);
        let mut pmu_s0 = None;
        let mut pmu_s1 = None;
        let mut t0 = Instant::now();
        let mut wall_s = 0.0f64;
        let mut card_pmu_json = "null".to_string();

        let start_pmu = |sync_conn: &mut Option<(TcpStream, BufReader<TcpStream>)>,
                         pmu_s0: &mut Option<HostSnap>,
                         t0: &mut Instant|
         -> Result<(), Box<dyn Error>> {
            if let Some((w, r)) = sync_conn {
                let resp = sync_cmd(w, r, "PMU_START")?;
                if resp != "OK" {
                    return Err(format!("card PMU_START failed: {resp}").into());
                }
            }
            *pmu_s0 = host_pmu.as_ref().map(HostPmu::snapshot);
            *t0 = Instant::now();
            Ok(())
        };
        let stop_pmu = |sync_conn: &mut Option<(TcpStream, BufReader<TcpStream>)>,
                        pmu_s1: &mut Option<HostSnap>,
                        wall_s: &mut f64,
                        t0: &Instant,
                        card_pmu_json: &mut String|
         -> Result<(), Box<dyn Error>> {
            *wall_s = t0.elapsed().as_secs_f64();
            *pmu_s1 = host_pmu.as_ref().map(HostPmu::snapshot);
            if let Some((w, r)) = sync_conn {
                *card_pmu_json = sync_cmd(w, r, "PMU_STOP")?;
            }
            Ok(())
        };
        let db_mode_s = match args.doorbell {
            DoorbellMode::None => "none",
            DoorbellMode::PerPkt => "per_pkt",
            DoorbellMode::Adaptive => "adaptive",
        };

        let json_line = match args.mode {
            WorkloadMode::Closed => {
                let total = args.warmup + args.rounds;
                let hw_cap = if args.credit_cap > 0 { args.credit_cap.min(args.window) } else { args.window };
                let separate_queue = hw_cap < args.window;
                let mut pending = vec![[1u64; 2]; PENDING];
                std::hint::black_box(pending.as_mut_ptr());
                pending.fill([0, 0]);
                let mut tot_rtt = pretouch_u64(args.rounds as usize);
                let mut wire_rtt = pretouch_u64(args.rounds as usize);

                let (mut submitted, mut sent, mut done, mut lost, mut bad) = (0u64, 0u64, 0u64, 0u64, 0u64);
                let (mut late, mut late_max, mut torn) = (0u64, 0u64, 0u64);
                let (mut polls, mut empty_polls, mut max_inflight) = (0u64, 0u64, 0u64);
                let (mut inflight_sum, mut inflight_samples) = (0u128, 0u64);
                let mut first_measured = 0u64;

                db.measured_active = true;
                start_pmu(&mut sync_conn, &mut pmu_s0, &mut t0)?;
                let mut last_rx = rdtsc();

                while done < total {
                    if separate_queue {
                        let now_sub = rdtsc();
                        while submitted < total && submitted - done < args.window {
                            submitted += 1;
                            pending[submitted as usize % PENDING][0] = now_sub;
                        }
                    }
                    let limit_seq = if separate_queue { submitted } else { total };
                    while sent < limit_seq && sent - done < hw_cap {
                        sent += 1;
                        let tsc = rdtsc();
                        let s = &mut pending[sent as usize % PENDING];
                        if !separate_queue {
                            s[0] = tsc;
                        }
                        s[1] = tsc;
                        submit_cell(&slot, base, sent, tsc)?;
                        db.on_cell_sent(&slot, base, sent, tsc)?;
                        let cur_inf = sent - done;
                        if cur_inf > max_inflight {
                            max_inflight = cur_inf;
                        }
                    }
                    let now_pre = rdtsc();
                    db.maybe_flush(&slot, base, sent, done, sent == total, now_pre)?;

                    let n = replies.poll(|r| {
                        let s = &mut pending[r.seq as usize % PENDING];
                        if r.seq == 0 || r.seq > sent || r.tsc == 0 || s[1] != r.tsc {
                            bad += 1;
                            return;
                        }
                        let submit_tsc = s[0];
                        *s = [0, 0];
                        done += 1;
                        match r.integrity {
                            Integrity::Whole => {}
                            Integrity::LateTail(w) => {
                                late += 1;
                                late_max = late_max.max(w);
                            }
                            Integrity::Torn => {
                                torn += 1;
                                return;
                            }
                        }
                        if r.seq > args.warmup {
                            if tot_rtt.is_empty() {
                                first_measured = r.tsc;
                            }
                            tot_rtt.push(r.at.saturating_sub(submit_tsc));
                            wire_rtt.push(r.at.saturating_sub(r.tsc));
                        }
                    });
                    let now = rdtsc();
                    polls += 1;
                    empty_polls += u64::from(n == 0);
                    inflight_sum += (sent - done) as u128;
                    inflight_samples += 1;
                    if n > 0 {
                        last_rx = now;
                    } else if now.saturating_sub(last_rx) > hz / 2 {
                        lost += sent - done;
                        stop_pmu(&mut sync_conn, &mut pmu_s1, &mut wall_s, &t0, &mut card_pmu_json)?;
                        return Err(format!("closed-loop stalled (lost {lost})").into());
                    }
                }
                let loop_end = rdtsc();
                stop_pmu(&mut sync_conn, &mut pmu_s1, &mut wall_s, &t0, &mut card_pmu_json)?;

                let secs = loop_end.saturating_sub(first_measured).max(1) as f64 / hz as f64;
                let measured = tot_rtt.len();
                let mops = (measured as f64 / secs) / 1e6;
                let tot_l = Latency::from_ticks(tot_rtt, hz);
                let wire_l = Latency::from_ticks(wire_rtt, hz);
                let host_pmu_json = match (&pmu_s0, &pmu_s1) {
                    (Some(s0), Some(s1)) => format_host_pmu(s0, s1, wall_s),
                    _ => "null".into(),
                };
                let db_rate_mps = if wall_s > 0.0 { db.rings_total as f64 / wall_s / 1e6 } else { 0.0 };
                let pkts_per_db = if db.rings_total > 0 { total as f64 / db.rings_total as f64 } else { 0.0 };
                format!(
                    "MRV177 {{\"mode\":\"closed\",\"cores\":{},\"window\":{},\"credit_cap\":{},\
                     \"doorbell\":\"{}\",\"db_sfence\":{},\"db_batch\":{},\"db_light\":{},\"db_flush_ns\":{},\
                     \"db_rings\":{},\"db_rate_mps\":{:.4},\"pkts_per_db\":{:.3},\
                     \"rounds\":{},\"warmup\":{},\"measured\":{},\"lost\":{},\"bad\":{},\"torn\":{},\
                     \"late\":{},\"late_max_us\":{:.4},\"mops\":{:.6},\"achieved_mps\":{:.6},\
                     \"wall_s\":{:.6},\"tsc_hz\":{},\"polls\":{},\"empty_polls\":{},\"blocked\":{},\
                     \"max_inflight\":{},\"mean_inflight\":{:.4},\"tot_us\":{},\"wire_us\":{},\
                     \"host_pmu\":{},\"card_pmu\":{}}}",
                    cores,
                    args.window,
                    hw_cap,
                    db_mode_s,
                    args.db_sfence,
                    args.db_batch,
                    args.db_light,
                    args.db_flush_ns,
                    db.rings_total,
                    db_rate_mps,
                    pkts_per_db,
                    args.rounds,
                    args.warmup,
                    measured,
                    lost,
                    bad,
                    torn,
                    late,
                    late_max as f64 * 1e6 / hz as f64,
                    mops,
                    mops,
                    wall_s,
                    hz,
                    polls,
                    empty_polls,
                    replies.blocked,
                    max_inflight,
                    inflight_sum as f64 / inflight_samples.max(1) as f64,
                    lat_json(&tot_l),
                    lat_json(&wire_l),
                    host_pmu_json,
                    card_pmu_json,
                )
            }

            WorkloadMode::Open => {
                let total = args.warmup + args.rounds;
                let hw_cap = if args.credit_cap == 0 {
                    (REPLY_CELLS - 64) as u64
                } else {
                    args.credit_cap.min((REPLY_CELLS - 64) as u64)
                };
                let mean_q16 = (hz as f64 / (args.open_mps * 1e6)) * 65536.0;
                let pace_q16 = mean_q16.round().max(1.0) as u64;
                let exp_table: Vec<u64> = if args.arrival == ArrivalArg::Poisson {
                    let mut tbl = vec![1u64; 65536];
                    for (i, s) in tbl.iter_mut().enumerate() {
                        let u = (i as f64 + 0.5) / 65536.0;
                        *s = (-u.ln() * mean_q16).round().max(1.0) as u64;
                    }
                    std::hint::black_box(tbl.as_mut_ptr());
                    tbl
                } else {
                    Vec::new()
                };

                let mut pending = vec![[1u64; 2]; PENDING];
                std::hint::black_box(pending.as_mut_ptr());
                pending.fill([0, 0]);
                let mut tot_ticks = pretouch_u64(args.rounds as usize);
                let mut wire_ticks = pretouch_u64(args.rounds as usize);
                let mut queue_ticks = pretouch_u64(args.rounds as usize);

                let (mut sent, mut done, mut lost, mut bad) = (0u64, 0u64, 0u64, 0u64);
                let (mut late, mut late_max, mut torn) = (0u64, 0u64, 0u64);
                let (mut polls, mut empty_polls, mut max_inflight, mut credit_waits) = (0u64, 0u64, 0u64, 0u64);
                let (mut inflight_sum, mut inflight_samples) = (0u128, 0u64);
                let mut rng = if args.seed == 0 { 0x9e37_79b9_7f4a_7c15 } else { args.seed };
                let mut sched_q16 = 0u64;
                let (mut meas_sched_start, mut meas_sched_end) = (0u64, 0u64);
                let (mut meas_done_start, mut meas_done_end) = (0u64, 0u64);
                let (mut steady_start_tsc, mut steady_start_done, mut steady_end_tsc, mut steady_end_done) =
                    (0u64, 0u64, 0u64, 0u64);
                let mid_lo = args.warmup + args.rounds / 10;
                let mid_hi = args.warmup + (args.rounds * 9) / 10;
                let (mut mid_start_tsc, mut mid_start_done, mut mid_end_tsc, mut mid_end_done) = (0u64, 0u64, 0u64, 0u64);

                db.measured_active = true;
                start_pmu(&mut sync_conn, &mut pmu_s0, &mut t0)?;
                let t_origin = rdtsc();
                let mut next_sched = t_origin;
                let mut last_rx = t_origin;

                while done < total {
                    let mut now = rdtsc();
                    let mut sent_this_iter = 0u64;
                    while sent < total && now >= next_sched {
                        if sent - done >= hw_cap {
                            credit_waits += 1;
                            break;
                        }
                        sent += 1;
                        let send_tsc = if sent_this_iter == 0 { now } else { rdtsc() };
                        let sched_tsc = next_sched;
                        if sent == args.warmup + 1 {
                            meas_sched_start = sched_tsc;
                        }
                        if sent == total {
                            meas_sched_end = sched_tsc;
                        }
                        pending[sent as usize % PENDING] = [sched_tsc, send_tsc];
                        submit_cell(&slot, base, sent, send_tsc)?;
                        db.on_cell_sent(&slot, base, sent, send_tsc)?;
                        sent_this_iter += 1;
                        let cur_inf = sent - done;
                        if cur_inf > max_inflight {
                            max_inflight = cur_inf;
                        }
                        if args.arrival == ArrivalArg::Paced {
                            sched_q16 = sched_q16.wrapping_add(pace_q16);
                        } else {
                            rng ^= rng << 13;
                            rng ^= rng >> 7;
                            rng ^= rng << 17;
                            sched_q16 = sched_q16.wrapping_add(exp_table[(rng & 65535) as usize]);
                        }
                        next_sched = t_origin.wrapping_add(sched_q16 >> 16);
                        now = send_tsc;
                    }
                    db.maybe_flush(&slot, base, sent, done, sent == total, now)?;

                    let n = replies.poll(|r| {
                        let s = &mut pending[r.seq as usize % PENDING];
                        if r.seq == 0 || r.seq > sent || r.tsc == 0 || s[1] != r.tsc {
                            bad += 1;
                            return;
                        }
                        let sched_tsc = s[0];
                        *s = [0, 0];
                        done += 1;
                        match r.integrity {
                            Integrity::Whole => {}
                            Integrity::LateTail(w) => {
                                late += 1;
                                late_max = late_max.max(w);
                            }
                            Integrity::Torn => {
                                torn += 1;
                                return;
                            }
                        }
                        if r.seq == args.warmup {
                            meas_done_start = r.at;
                        }
                        if r.seq > args.warmup {
                            if meas_done_start == 0 {
                                meas_done_start = r.at;
                            }
                            meas_done_end = r.at;
                            tot_ticks.push(r.at.saturating_sub(sched_tsc));
                            wire_ticks.push(r.at.saturating_sub(r.tsc));
                            queue_ticks.push(r.tsc.saturating_sub(sched_tsc));
                        }
                    });
                    let now_after = rdtsc();
                    if done >= args.warmup && sent < total {
                        if steady_start_tsc == 0 {
                            steady_start_tsc = now_after;
                            steady_start_done = done;
                        }
                        steady_end_tsc = now_after;
                        steady_end_done = done;
                    }
                    if sent >= mid_lo && sent < mid_hi {
                        if mid_start_tsc == 0 {
                            mid_start_tsc = now_after;
                            mid_start_done = done;
                        }
                        mid_end_tsc = now_after;
                        mid_end_done = done;
                    }
                    polls += 1;
                    empty_polls += u64::from(n == 0);
                    inflight_sum += (sent - done) as u128;
                    inflight_samples += 1;
                    if n > 0 || sent == done {
                        last_rx = now_after;
                    } else if now_after.saturating_sub(last_rx) > hz / 2 {
                        lost += sent - done;
                        stop_pmu(&mut sync_conn, &mut pmu_s1, &mut wall_s, &t0, &mut card_pmu_json)?;
                        return Err(format!("open-loop stalled (lost {lost})").into());
                    }
                }
                stop_pmu(&mut sync_conn, &mut pmu_s1, &mut wall_s, &t0, &mut card_pmu_json)?;

                let measured = tot_ticks.len();
                let done_s = meas_done_end.saturating_sub(meas_done_start).max(1) as f64 / hz as f64;
                let sched_s = meas_sched_end.saturating_sub(meas_sched_start).max(1) as f64 / hz as f64;
                let achieved_done_mps = (measured as f64 / done_s) / 1e6;
                let achieved_mps = if steady_end_tsc > steady_start_tsc {
                    let steady_s = (steady_end_tsc - steady_start_tsc) as f64 / hz as f64;
                    ((steady_end_done.saturating_sub(steady_start_done)) as f64 / steady_s) / 1e6
                } else {
                    achieved_done_mps
                };
                let achieved_mid80_mps = if mid_end_tsc > mid_start_tsc {
                    let mid_s = (mid_end_tsc - mid_start_tsc) as f64 / hz as f64;
                    ((mid_end_done.saturating_sub(mid_start_done)) as f64 / mid_s) / 1e6
                } else {
                    achieved_mps
                };
                let offered_win_mps = ((args.rounds.saturating_sub(1)) as f64 / sched_s) / 1e6;
                let tot_l = Latency::from_ticks(tot_ticks, hz);
                let wire_l = Latency::from_ticks(wire_ticks, hz);
                let queue_l = Latency::from_ticks(queue_ticks, hz);
                let host_pmu_json = match (&pmu_s0, &pmu_s1) {
                    (Some(s0), Some(s1)) => format_host_pmu(s0, s1, wall_s),
                    _ => "null".into(),
                };
                let db_rate_mps = if wall_s > 0.0 { db.rings_total as f64 / wall_s / 1e6 } else { 0.0 };
                let pkts_per_db = if db.rings_total > 0 { total as f64 / db.rings_total as f64 } else { 0.0 };
                let arr_s = match args.arrival {
                    ArrivalArg::Paced => "paced",
                    ArrivalArg::Poisson => "poisson",
                };
                format!(
                    "MRV177 {{\"mode\":\"open\",\"arrival\":\"{}\",\"seed\":{},\"cores\":{},\
                     \"offered_mps\":{:.4},\"offered_win_mps\":{:.4},\"achieved_mps\":{:.6},\
                     \"achieved_mid80_mps\":{:.6},\"achieved_done_mps\":{:.6},\"credit_cap\":{},\
                     \"doorbell\":\"{}\",\"db_sfence\":{},\"db_batch\":{},\"db_light\":{},\"db_flush_ns\":{},\
                     \"db_rings\":{},\"db_rate_mps\":{:.4},\"pkts_per_db\":{:.3},\
                     \"rounds\":{},\"warmup\":{},\"measured\":{},\"lost\":{},\"bad\":{},\"torn\":{},\
                     \"late\":{},\"late_max_us\":{:.4},\"wall_s\":{:.6},\"tsc_hz\":{},\
                     \"polls\":{},\"empty_polls\":{},\"blocked\":{},\"max_inflight\":{},\"mean_inflight\":{:.4},\
                     \"credit_waits\":{},\"tot_us\":{},\"wire_us\":{},\"queue_us\":{},\
                     \"host_pmu\":{},\"card_pmu\":{}}}",
                    arr_s,
                    args.seed,
                    cores,
                    args.open_mps,
                    offered_win_mps,
                    achieved_mps,
                    achieved_mid80_mps,
                    achieved_done_mps,
                    hw_cap,
                    db_mode_s,
                    args.db_sfence,
                    args.db_batch,
                    args.db_light,
                    args.db_flush_ns,
                    db.rings_total,
                    db_rate_mps,
                    pkts_per_db,
                    args.rounds,
                    args.warmup,
                    measured,
                    lost,
                    bad,
                    torn,
                    late,
                    late_max as f64 * 1e6 / hz as f64,
                    wall_s,
                    hz,
                    polls,
                    empty_polls,
                    replies.blocked,
                    max_inflight,
                    inflight_sum as f64 / inflight_samples.max(1) as f64,
                    credit_waits,
                    lat_json(&tot_l),
                    lat_json(&wire_l),
                    lat_json(&queue_l),
                    host_pmu_json,
                    card_pmu_json,
                )
            }

            WorkloadMode::BurstTail => {
                // Burst-stop tail latency workload:
                // Each cycle sends `burst_len` packets, optionally drains and/or waits `tail_gap_ns`,
                // then sends `tail_len` tail packets and waits for all replies, then idles `cycle_idle_ns`.
                let total_cycles = args.warmup_cycles + args.cycles;
                let pkts_per_cycle = args.burst_len + args.tail_len;
                let gap_ticks = ((args.tail_gap_ns as u128 * hz as u128 + 500_000_000) / 1_000_000_000) as u64;
                let idle_ticks = ((args.cycle_idle_ns as u128 * hz as u128 + 500_000_000) / 1_000_000_000) as u64;

                let mut pending = vec![0u64; PENDING];
                std::hint::black_box(pending.as_mut_ptr());
                pending.fill(0);
                let mut is_tail = vec![false; PENDING];
                let mut burst_rtt = pretouch_u64((args.cycles * args.burst_len) as usize);
                let mut tail_rtt = pretouch_u64((args.cycles * args.tail_len) as usize);

                let (mut sent, mut done, lost, mut bad) = (0u64, 0u64, 0u64, 0u64);
                let (mut late, mut late_max, mut torn) = (0u64, 0u64, 0u64);

                let drain_until = |target_done: u64,
                                   sent: u64,
                                   done: &mut u64,
                                   pending: &mut [u64],
                                   is_tail: &[bool],
                                   record: bool,
                                   burst_rtt: &mut Vec<u64>,
                                   tail_rtt: &mut Vec<u64>,
                                   bad: &mut u64,
                                   late: &mut u64,
                                   late_max: &mut u64,
                                   torn: &mut u64,
                                   replies: &mut Mrv177RingReplies,
                                   db: &mut DbEmitter,
                                   slot: &LeasedSlot|
                 -> Result<(), Box<dyn Error>> {
                    let mut last_rx = rdtsc();
                    while *done < target_done {
                        let now = rdtsc();
                        db.maybe_flush(slot, base, sent, *done, false, now)?;
                        let n = replies.poll(|r| {
                            let idx = r.seq as usize % PENDING;
                            let send_tsc = pending[idx];
                            if r.seq == 0 || r.seq > sent || r.tsc == 0 || send_tsc != r.tsc {
                                *bad += 1;
                                return;
                            }
                            pending[idx] = 0;
                            *done += 1;
                            match r.integrity {
                                Integrity::Whole => {}
                                Integrity::LateTail(w) => {
                                    *late += 1;
                                    *late_max = (*late_max).max(w);
                                }
                                Integrity::Torn => {
                                    *torn += 1;
                                    return;
                                }
                            }
                            if record {
                                let dt = r.at.saturating_sub(send_tsc);
                                if is_tail[idx] {
                                    tail_rtt.push(dt);
                                } else {
                                    burst_rtt.push(dt);
                                }
                            }
                        });
                        if n > 0 {
                            last_rx = rdtsc();
                        } else if rdtsc().saturating_sub(last_rx) > hz / 2 {
                            return Err(format!("burst-tail stalled waiting for {target_done}, done={}", *done).into());
                        }
                    }
                    Ok(())
                };

                for cyc in 0..total_cycles {
                    if cyc == args.warmup_cycles {
                        // Pipe is 100% drained and idle between cycles: bracket PMU cleanly on measured cycles only!
                        db.measured_active = true;
                        start_pmu(&mut sync_conn, &mut pmu_s0, &mut t0)?;
                    }
                    let record = cyc >= args.warmup_cycles;

                    // Phase 1: send burst_len packets back-to-back.
                    for _ in 0..args.burst_len {
                        sent += 1;
                        let tsc = rdtsc();
                        let idx = sent as usize % PENDING;
                        pending[idx] = tsc;
                        is_tail[idx] = false;
                        submit_cell(&slot, base, sent, tsc)?;
                        db.on_cell_sent(&slot, base, sent, tsc)?;
                    }
                    if args.tail_wait_drain {
                        // Drain burst completely before pausing and sending isolated tail packets.
                        let burst_end_seq = sent;
                        drain_until(
                            burst_end_seq,
                            sent,
                            &mut done,
                            &mut pending,
                            &is_tail,
                            record,
                            &mut burst_rtt,
                            &mut tail_rtt,
                            &mut bad,
                            &mut late,
                            &mut late_max,
                            &mut torn,
                            &mut replies,
                            &mut db,
                            &slot,
                        )?;
                    }
                    if gap_ticks > 0 {
                        let g0 = rdtsc();
                        while rdtsc().wrapping_sub(g0) < gap_ticks {
                            let now = rdtsc();
                            db.maybe_flush(&slot, base, sent, done, false, now)?;
                            std::hint::spin_loop();
                        }
                    }

                    // Phase 2: send tail_len packets (1 or 2).
                    for _ in 0..args.tail_len {
                        sent += 1;
                        let tsc = rdtsc();
                        let idx = sent as usize % PENDING;
                        pending[idx] = tsc;
                        is_tail[idx] = true;
                        submit_cell(&slot, base, sent, tsc)?;
                        db.on_cell_sent(&slot, base, sent, tsc)?;
                    }
                    // Note: do NOT force-flush doorbell here! Let natural adaptive doorbell
                    // (light-load immediate OR flush_ticks holdback) govern tail packet latency faithfully!
                    let cycle_end_seq = sent;
                    if let Err(e) = drain_until(
                        cycle_end_seq,
                        sent,
                        &mut done,
                        &mut pending,
                        &is_tail,
                        record,
                        &mut burst_rtt,
                        &mut tail_rtt,
                        &mut bad,
                        &mut late,
                        &mut late_max,
                        &mut torn,
                        &mut replies,
                        &mut db,
                        &slot,
                    ) {
                        let _ = sent - done;
                        stop_pmu(&mut sync_conn, &mut pmu_s1, &mut wall_s, &t0, &mut card_pmu_json)?;
                        return Err(e);
                    }

                    // Phase 3: idle gap before next cycle so card resets to true idle.
                    if idle_ticks > 0 && cyc + 1 < total_cycles {
                        let i0 = rdtsc();
                        while rdtsc().wrapping_sub(i0) < idle_ticks {
                            std::hint::spin_loop();
                        }
                    }
                }
                stop_pmu(&mut sync_conn, &mut pmu_s1, &mut wall_s, &t0, &mut card_pmu_json)?;

                let burst_l = Latency::from_ticks(burst_rtt, hz);
                let tail_l = Latency::from_ticks(tail_rtt, hz);
                let host_pmu_json = match (&pmu_s0, &pmu_s1) {
                    (Some(s0), Some(s1)) => format_host_pmu(s0, s1, wall_s),
                    _ => "null".into(),
                };
                let measured_pkts = args.cycles * pkts_per_cycle;
                let db_rate_mps = if wall_s > 0.0 { db.rings_measured as f64 / wall_s / 1e6 } else { 0.0 };
                let pkts_per_db = if db.rings_measured > 0 {
                    measured_pkts as f64 / db.rings_measured as f64
                } else {
                    0.0
                };
                format!(
                    "MRV177 {{\"mode\":\"burst_tail\",\"cores\":{},\"burst_len\":{},\"tail_len\":{},\
                     \"tail_gap_ns\":{},\"tail_wait_drain\":{},\"cycle_idle_ns\":{},\
                     \"cycles\":{},\"warmup_cycles\":{},\"measured\":{},\
                     \"doorbell\":\"{}\",\"db_sfence\":{},\"db_batch\":{},\"db_light\":{},\"db_flush_ns\":{},\
                     \"db_rings\":{},\"db_rate_mps\":{:.4},\"pkts_per_db\":{:.3},\
                     \"lost\":{},\"bad\":{},\"torn\":{},\"late\":{},\"late_max_us\":{:.4},\
                     \"wall_s\":{:.6},\"tsc_hz\":{},\"blocked\":{},\
                     \"burst_us\":{},\"tail_us\":{},\"tot_us\":{},\
                     \"host_pmu\":{},\"card_pmu\":{}}}",
                    cores,
                    args.burst_len,
                    args.tail_len,
                    args.tail_gap_ns,
                    args.tail_wait_drain,
                    args.cycle_idle_ns,
                    args.cycles,
                    args.warmup_cycles,
                    measured_pkts,
                    db_mode_s,
                    args.db_sfence,
                    args.db_batch,
                    args.db_light,
                    args.db_flush_ns,
                    db.rings_measured,
                    db_rate_mps,
                    pkts_per_db,
                    lost,
                    bad,
                    torn,
                    late,
                    late_max as f64 * 1e6 / hz as f64,
                    wall_s,
                    hz,
                    replies.blocked,
                    lat_json(&burst_l),
                    lat_json(&tail_l),
                    lat_json(&tail_l),
                    host_pmu_json,
                    card_pmu_json,
                )
            }
        };

        if !slot.revoked() {
            slot.write_u64(REPLY_RING_OFFSET, 0);
        }
        std::thread::sleep(Duration::from_millis(5));
        println!("{json_line}");
        Ok(())
    }

    // ---- Direct Host Core + Socket-1 Uncore PMU (100% non-multiplexed) ----
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
    const PERF_TYPE_SOFTWARE: u32 = 1;
    const PERF_TYPE_HW_CACHE: u32 = 3;
    const PERF_TYPE_RAW: u32 = 4;
    const PERF_COUNT_HW_CPU_CYCLES: u64 = 0;
    const PERF_COUNT_HW_INSTRUCTIONS: u64 = 1;
    const PERF_COUNT_HW_REF_CPU_CYCLES: u64 = 9;
    const PERF_COUNT_SW_PAGE_FAULTS: u64 = 2;
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
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>()) };
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
        page_faults: PerfCounter,
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
        page_faults: Sample,
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
            let page_faults = PerfCounter::open(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_PAGE_FAULTS, cpu)?;

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
                page_faults,
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
                page_faults: self.page_faults.read(),
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
        let pf = s1.page_faults.delta(s0.page_faults).val;

        let (mut cha_ins_sum, mut cha_occ_sum, mut cha_hit_sum, mut cha_miss_sum) = (0u64, 0u64, 0u64, 0u64);
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
        let irp1_inflight_clk = if iio1_clk > 0 { irp1_occ as f64 / iio1_clk as f64 } else { 0.0 };
        let iio1_clk_ghz = if wall_s > 0.0 { iio1_clk as f64 / wall_s / 1e9 } else { 0.0 };

        format!(
            "{{\"cpu\":{},\"min_pct\":{:.2},\"cycles\":{},\"ref_cycles\":{},\"instr\":{},\
             \"ghz\":{:.4},\"sb_stall\":{},\"sb_pct\":{:.3},\"fb_full\":{},\"fb_pct\":{:.3},\
             \"llc_load_misses\":{},\"page_faults\":{},\"active_chas\":{},\
             \"cha_ins_wcilf\":{},\"cha_occ_wcilf\":{},\"cha_inflight\":{:.4},\"cha_res_ns\":{:.2},\
             \"cha_io_hit_itom\":{},\"cha_io_miss_itom\":{},\"mesh_clk\":{},\"mesh_ghz\":{:.4},\
             \"irp1_occ\":{},\"irp1_ins\":{},\"irp1_inflight_clk\":{:.4},\
             \"iio1_cpu_wr\":{},\"iio1_inb_wr\":{},\"iio1_clk\":{},\"iio1_clk_ghz\":{:.4}}}",
            s1.cpu,
            min_pct,
            cyc,
            refc,
            ins,
            ghz,
            sb,
            sb_pct,
            fb,
            fb_pct,
            llc_m,
            pf,
            active_chas,
            cha_ins_sum,
            cha_occ_sum,
            cha_inflight,
            cha_res_ns,
            cha_hit_sum,
            cha_miss_sum,
            mesh_clk,
            mesh_ghz,
            irp1_occ,
            irp1_ins,
            irp1_inflight_clk,
            iio1_cpu_wr,
            iio1_inb_wr,
            iio1_clk,
            iio1_clk_ghz
        )
    }
}
