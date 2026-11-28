//! Card-side benchmark for MRV-177:
//! Single-variable comparison of write-frontier collision avoidance on CN106 BAR4:
//!   - Mode 0 (Baseline): direct polling of `cell[next]`
//!   - Mode 1 (Arm A, B-Queue adaptive lookahead): idle `D=1` on `next`; busy ramps
//!     `D` and probes `next + (D - 1) * stride`, with short pure-register spin wait
//!     (`T_bt`) and binary-halving backtrack (`D/2 -> ... -> 1`)
//!   - Mode 2 (Arm B, Decoupled Doorbell): poll dedicated 128 B-aligned `prod_idx`
//!     cache line (`DOORBELL_OFFSET = 0x3ff800`); never touch any data cell until
//!     `prod_idx >= next`; instruments `db_ahead_empty` and `db_ahead_partial` to
//!     detect early-doorbell/data-not-ready incidents when host omits `SFENCE`.
//!
//! Fixed direct path (MRV-82): BAR4 slot 15 (64 B cells, 64 B stride, RING_CELLS=65472)
//! + PEM0 outbound write-combining core stores (`pem_wc`) into host reply ring,
//! guarded by the MRV-26 lease protocol, with 100% non-multiplexed TAD + ARMv8 PMU.

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv177_card runs on the aarch64 CN106 card");
    std::process::exit(2);
}

#[cfg(target_arch = "aarch64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "aarch64")]
mod imp {
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::size_of;
    use std::net::{TcpListener, TcpStream};
    use std::os::fd::RawFd;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::time::{Duration, Instant};

    use clap::Parser;
    use dpdk::{Eal, Memzone, rdtsc};
    use testbed::inline::{
        CORES_OFFSET, Cell, MAX_CORES, NONCE_OFFSET, READY, READY_OFFSET, REPLY_CELLS, REPLY_RING_OFFSET, SLOT,
        SLOT_BYTES, STREAM_CORES_OFFSET, Slot, next_offset,
    };
    use testbed::lease;
    use testbed::mmio::{
        Bar4Window, OUTBOUND_REGION_BYTES, OUTBOUND_REGIONS, OutboundWindow, STORE_REGIONS, restore_defaults,
        store64_gathered,
    };

    /// 4092 KiB data ring (65472 x 64 B cells), leaving a 4 KiB guard/control page
    /// at [0x3ff000..0x400000). Divisible by 1, 2, 4, 8, 16, 32, 64, 128.
    const RING_CELLS_177: usize = 65472;
    /// Dedicated 128 B-aligned (2 KiB-isolated) doorbell line inside the guard page.
    /// 2048 B past the last data cell and 1856 B before READY_OFFSET (0x3fff40).
    const DOORBELL_OFFSET: usize = 0x3ff800;
    const BATCH: usize = 32;

    const MODE_BASELINE: u64 = 0;
    const MODE_BQUEUE: u64 = 1;
    const MODE_DOORBELL: u64 = 2;

    #[inline(always)]
    fn cell_offset_177(seq: u64) -> usize {
        ((seq - 1) as usize % RING_CELLS_177) * 64
    }

    #[inline(always)]
    unsafe fn poll_ready(at: *mut Cell, seq: u64) -> bool {
        let head = unsafe { AtomicU64::from_ptr(&raw mut (*at).seq) }.load(Ordering::Acquire);
        if head != seq {
            return false;
        }
        let tail = unsafe { (&raw const (*at).seq_tail).read_volatile() };
        tail == seq
    }

    #[inline(always)]
    fn highest_pow2_le(x: u64) -> u64 {
        if x <= 1 {
            1
        } else {
            1u64 << (63 - x.leading_zeros())
        }
    }

    #[derive(Parser)]
    struct Args {
        /// Host function replies are written as: DPI numbering, 0 = PF, n = VF n-1.
        #[arg(long, default_value_t = 1)]
        host_vf: u16,
        #[arg(long, default_value_t = 0)]
        host_pf: u8,
        /// Lease TCP listen address (card `sdp1-0`).
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        /// Sync/PMU TCP listen address (card `sdp1-0`).
        #[arg(long, default_value = "10.10.0.2:7527")]
        sync: String,
        /// Only restore BAR4 slot 15 and PEM0 outbound regions 248-255, then exit.
        #[arg(long)]
        restore: bool,
    }

    struct StoreReplies {
        window: OutboundWindow,
        cores: usize,
        target: u64,
        stored: u64,
    }

    impl StoreReplies {
        fn send(&mut self, reply: &Cell, ring: u64) -> Result<(), Box<dyn Error>> {
            if ring != self.target {
                let offset = ring % OUTBOUND_REGION_BYTES;
                if offset + (REPLY_CELLS * 64) as u64 > OUTBOUND_REGION_BYTES {
                    return Err(format!("host reply ring {ring:#x} crosses a 2 GiB outbound region").into());
                }
                // Flush any buffered WC stores before retargeting the region.
                unsafe { std::arch::asm!("dsb sy", options(nostack, preserves_flags)) };
                self.window.retarget(ring - offset)?;
                self.target = ring;
            }
            let at = (self.target % OUTBOUND_REGION_BYTES) as usize + Cell::reply_offset(reply.tag, self.cores);
            let dst = unsafe { self.window.as_ptr().add(at) };
            unsafe { store64_gathered(dst, &reply.words()) };
            self.stored += 1;
            Ok(())
        }
    }

    #[repr(align(128))]
    struct CoreLive {
        os_cpu: AtomicU64,
        seen_epoch: AtomicU64,
        taken: AtomicU64,
        busy_polls: AtomicU64,
        empty_polls: AtomicU64,
        partial: AtomicU64,
        corrupt: AtomicU64,
        bq_probes: AtomicU64,
        bq_probe_hits: AtomicU64,
        bq_wait_hits: AtomicU64,
        bq_backtracks: AtomicU64,
        bq_d_sum: AtomicU64,
        bq_d_gt1_polls: AtomicU64,
        db_empty_polls: AtomicU64,
        db_ahead_empty: AtomicU64,
        db_ahead_partial: AtomicU64,
    }

    impl CoreLive {
        fn new() -> Self {
            CoreLive {
                os_cpu: AtomicU64::new(u64::MAX),
                seen_epoch: AtomicU64::new(0),
                taken: AtomicU64::new(0),
                busy_polls: AtomicU64::new(0),
                empty_polls: AtomicU64::new(0),
                partial: AtomicU64::new(0),
                corrupt: AtomicU64::new(0),
                bq_probes: AtomicU64::new(0),
                bq_probe_hits: AtomicU64::new(0),
                bq_wait_hits: AtomicU64::new(0),
                bq_backtracks: AtomicU64::new(0),
                bq_d_sum: AtomicU64::new(0),
                bq_d_gt1_polls: AtomicU64::new(0),
                db_empty_polls: AtomicU64::new(0),
                db_ahead_empty: AtomicU64::new(0),
                db_ahead_partial: AtomicU64::new(0),
            }
        }
    }

    struct ModeCfg {
        mode: AtomicU64,
        bq_dmax: AtomicU64,
        bq_dmin: AtomicU64,
        bq_tbt_ns: AtomicU64,
        bq_tbt_ticks: AtomicU64,
        bq_ramp_k: AtomicU64,
        bq_fast_dt_ns: AtomicU64,
        bq_fast_dt_ticks: AtomicU64,
        bq_wait_first: AtomicU64,
        epoch: AtomicU64,
    }

    impl ModeCfg {
        fn new() -> Self {
            ModeCfg {
                mode: AtomicU64::new(MODE_BASELINE),
                bq_dmax: AtomicU64::new(16),
                bq_dmin: AtomicU64::new(4),
                bq_tbt_ns: AtomicU64::new(200),
                bq_tbt_ticks: AtomicU64::new(200),
                bq_ramp_k: AtomicU64::new(4),
                bq_fast_dt_ns: AtomicU64::new(0),
                bq_fast_dt_ticks: AtomicU64::new(0),
                bq_wait_first: AtomicU64::new(0),
                epoch: AtomicU64::new(1),
            }
        }
    }

    struct Shared<'a, 'e> {
        ring: &'a Memzone<'e>,
        quit: &'a AtomicBool,
        ring_cores: usize,
        tsc_hz: u64,
        cfg: ModeCfg,
        live: Vec<CoreLive>,
    }

    impl Shared<'_, '_> {
        #[inline(always)]
        fn control(&self, offset: usize) -> &AtomicU64 {
            unsafe { AtomicU64::from_ptr(self.ring.as_ptr().add(offset).cast()) }
        }
    }

    fn ns_to_ticks(ns: u64, hz: u64) -> u64 {
        if ns == 0 {
            0
        } else {
            ((ns as u128 * hz as u128 + 500_000_000) / 1_000_000_000) as u64
        }
    }

    #[derive(Default)]
    struct CoreStats {
        taken: u64,
        stored: u64,
        partial: u64,
        corrupt: u64,
    }

    fn serve_ring(sh: &Shared<'_, '_>, ring_index: usize, mut writer: StoreReplies) -> Result<CoreStats, Box<dyn Error>> {
        let base = sh.ring.as_ptr();
        let db_ptr = unsafe { base.add(DOORBELL_OFFSET).cast::<u64>() };
        let stride = sh.ring_cores as u64;
        let mut next = ring_index as u64 + 1;
        let mut st = CoreStats::default();
        let live = &sh.live[ring_index];

        let os_cpu = unsafe { libc::sched_getcpu() };
        live.os_cpu.store(os_cpu as u64, Ordering::Release);

        // Local cached config (reloaded whenever cfg.epoch changes).
        let mut local_epoch = 0u64;
        let mut mode = MODE_BASELINE;
        let mut bq_dmax = 16u64;
        let mut bq_dmin = 4u64;
        let mut bq_tbt_ticks = 200u64;
        let mut bq_ramp_k = 4u64;
        let mut bq_fast_dt_ticks = 0u64;
        let mut bq_wait_first = 0u64;

        // Per-core B-Queue adaptive state.
        let mut d = 1u64;
        let mut fast_streak = 0u64;
        let mut last_hit_tsc = 0u64;

        // Local mirror of live counters (flushed on every busy poll and every 256 empty polls).
        let mut loc_busy = 0u64;
        let mut loc_empty = 0u64;
        let mut loc_bq_probes = 0u64;
        let mut loc_bq_probe_hits = 0u64;
        let mut loc_bq_wait_hits = 0u64;
        let mut loc_bq_backtracks = 0u64;
        let mut loc_bq_d_sum = 0u64;
        let mut loc_bq_d_gt1 = 0u64;
        let mut loc_db_empty = 0u64;
        let mut loc_db_ahead_empty = 0u64;
        let mut loc_db_ahead_partial = 0u64;

        let flush_live = |live: &CoreLive,
                          taken: u64,
                          busy: u64,
                          empty: u64,
                          partial: u64,
                          corrupt: u64,
                          bq_probes: u64,
                          bq_probe_hits: u64,
                          bq_wait_hits: u64,
                          bq_backtracks: u64,
                          bq_d_sum: u64,
                          bq_d_gt1: u64,
                          db_empty: u64,
                          db_ahead_empty: u64,
                          db_ahead_partial: u64| {
            live.taken.store(taken, Ordering::Relaxed);
            live.busy_polls.store(busy, Ordering::Relaxed);
            live.empty_polls.store(empty, Ordering::Relaxed);
            live.partial.store(partial, Ordering::Relaxed);
            live.corrupt.store(corrupt, Ordering::Relaxed);
            live.bq_probes.store(bq_probes, Ordering::Relaxed);
            live.bq_probe_hits.store(bq_probe_hits, Ordering::Relaxed);
            live.bq_wait_hits.store(bq_wait_hits, Ordering::Relaxed);
            live.bq_backtracks.store(bq_backtracks, Ordering::Relaxed);
            live.bq_d_sum.store(bq_d_sum, Ordering::Relaxed);
            live.bq_d_gt1_polls.store(bq_d_gt1, Ordering::Relaxed);
            live.db_empty_polls.store(db_empty, Ordering::Relaxed);
            live.db_ahead_empty.store(db_ahead_empty, Ordering::Relaxed);
            live.db_ahead_partial.store(db_ahead_partial, Ordering::Relaxed);
        };

        let mut iter_tick = 0u64;
        while !sh.quit.load(Ordering::Relaxed) {
            // Check config epoch periodically (every 64 loops or when empty) for zero hot-path cost.
            if (iter_tick & 63) == 0 {
                let ep = sh.cfg.epoch.load(Ordering::Acquire);
                if ep != local_epoch {
                    mode = sh.cfg.mode.load(Ordering::Relaxed);
                    bq_dmax = sh.cfg.bq_dmax.load(Ordering::Relaxed).max(1);
                    bq_dmin = sh.cfg.bq_dmin.load(Ordering::Relaxed).clamp(1, bq_dmax);
                    bq_tbt_ticks = sh.cfg.bq_tbt_ticks.load(Ordering::Relaxed);
                    bq_ramp_k = sh.cfg.bq_ramp_k.load(Ordering::Relaxed).max(1);
                    bq_fast_dt_ticks = sh.cfg.bq_fast_dt_ticks.load(Ordering::Relaxed);
                    bq_wait_first = sh.cfg.bq_wait_first.load(Ordering::Relaxed);
                    d = 1;
                    fast_streak = 0;
                    last_hit_tsc = 0;
                    local_epoch = ep;
                    live.seen_epoch.store(ep, Ordering::Release);
                }
            }
            iter_tick = iter_tick.wrapping_add(1);

            let first = next;
            let mut reply_ring = 0u64;

            if mode == MODE_DOORBELL {
                // Arm B: poll dedicated prod_idx doorbell line ONLY; never touch any data cell while prod_idx < next.
                let prod_idx = unsafe { AtomicU64::from_ptr(db_ptr) }.load(Ordering::Acquire);
                if prod_idx < next {
                    loc_empty += 1;
                    loc_db_empty += 1;
                    if (loc_empty & 255) == 0 {
                        flush_live(
                            live, st.taken, loc_busy, loc_empty, st.partial, st.corrupt, loc_bq_probes,
                            loc_bq_probe_hits, loc_bq_wait_hits, loc_bq_backtracks, loc_bq_d_sum, loc_bq_d_gt1,
                            loc_db_empty, loc_db_ahead_empty, loc_db_ahead_partial,
                        );
                    }
                    continue;
                }
                for _ in 0..BATCH {
                    if next > prod_idx {
                        break;
                    }
                    let cell = match unsafe { Cell::poll(base.add(cell_offset_177(next)).cast(), next) } {
                        Slot::Empty => {
                            loc_db_ahead_empty += 1;
                            break;
                        }
                        Slot::Partial => {
                            loc_db_ahead_partial += 1;
                            st.partial += 1;
                            break;
                        }
                        Slot::Complete(cell) => cell,
                    };
                    let intact = cell.payload_matches(next);
                    if !intact {
                        st.corrupt += 1;
                    }
                    let tsc = if intact { cell.tsc } else { 0 };
                    if reply_ring == 0 {
                        reply_ring = sh.control(REPLY_RING_OFFSET).load(Ordering::Relaxed);
                    }
                    if reply_ring != 0 {
                        writer.send(&Cell { tsc, ..cell }, reply_ring)?;
                    }
                    next += stride;
                }
                let taken = (next - first) / stride;
                if taken == 0 {
                    loc_empty += 1;
                    if (loc_empty & 255) == 0 {
                        flush_live(
                            live, st.taken, loc_busy, loc_empty, st.partial, st.corrupt, loc_bq_probes,
                            loc_bq_probe_hits, loc_bq_wait_hits, loc_bq_backtracks, loc_bq_d_sum, loc_bq_d_gt1,
                            loc_db_empty, loc_db_ahead_empty, loc_db_ahead_partial,
                        );
                    }
                    continue;
                }
                st.taken += taken;
                loc_busy += 1;
                sh.control(next_offset(ring_index)).store(next, Ordering::Relaxed);
                flush_live(
                    live, st.taken, loc_busy, loc_empty, st.partial, st.corrupt, loc_bq_probes, loc_bq_probe_hits,
                    loc_bq_wait_hits, loc_bq_backtracks, loc_bq_d_sum, loc_bq_d_gt1, loc_db_empty, loc_db_ahead_empty,
                    loc_db_ahead_partial,
                );
                continue;
            }

            // Mode 0 (Baseline) or Mode 1 (Arm A: B-Queue adaptive lookahead).
            let used_d = if mode == MODE_BQUEUE { d } else { 1 };
            let mut drain_cap = BATCH;
            if used_d > 1 {
                loc_bq_probes += 1;
                let probe_seq = next + (used_d - 1) * stride;
                let probe_ptr = unsafe { base.add(cell_offset_177(probe_seq)).cast::<Cell>() };
                if bq_wait_first == 1 {
                    // Wait-first B-Queue: spin T_bt on cntvct_el0 (ZERO BAR4 loads) before touching
                    // any cell so host writes land without L1/L2 snoop landmines.
                    if bq_tbt_ticks > 0 {
                        let t0 = rdtsc();
                        while rdtsc().wrapping_sub(t0) < bq_tbt_ticks {
                            std::hint::spin_loop();
                        }
                    }
                    if unsafe { poll_ready(probe_ptr, probe_seq) } {
                        loc_bq_wait_hits += 1;
                        // Drain ONLY the verified D cells; do NOT read probe_seq + stride (write frontier).
                        drain_cap = (used_d as usize).min(BATCH);
                    } else {
                        // Partial batch or burst ended: drain directly from `next` up to used_d-1
                        // without probing intermediate D/2..2 lines (avoids extra snoop landmines).
                        loc_bq_backtracks += 1;
                        drain_cap = ((used_d - 1) as usize).clamp(1, BATCH);
                    }
                } else if bq_wait_first == 2 {
                    // Probe-first + exact-D bounded drain (no overshoot onto write frontier,
                    // no intermediate D/2..2 snoop landmines).
                    if unsafe { poll_ready(probe_ptr, probe_seq) } {
                        loc_bq_probe_hits += 1;
                        drain_cap = (used_d as usize).min(BATCH);
                    } else {
                        if bq_tbt_ticks > 0 {
                            let t0 = rdtsc();
                            while rdtsc().wrapping_sub(t0) < bq_tbt_ticks {
                                std::hint::spin_loop();
                            }
                        }
                        if unsafe { poll_ready(probe_ptr, probe_seq) } {
                            loc_bq_wait_hits += 1;
                            drain_cap = (used_d as usize).min(BATCH);
                        } else {
                            loc_bq_backtracks += 1;
                            drain_cap = ((used_d - 1) as usize).clamp(1, BATCH);
                        }
                    }
                } else {
                    // Textbook B-Queue (Wang et al., IJPP '13): probe next+D-1 first, then short wait,
                    // then binary-halving backtrack D/2 -> ... -> 1.
                    if unsafe { poll_ready(probe_ptr, probe_seq) } {
                        loc_bq_probe_hits += 1;
                    } else {
                        if bq_tbt_ticks > 0 {
                            let t0 = rdtsc();
                            while rdtsc().wrapping_sub(t0) < bq_tbt_ticks {
                                std::hint::spin_loop();
                            }
                        }
                        if unsafe { poll_ready(probe_ptr, probe_seq) } {
                            loc_bq_wait_hits += 1;
                        } else {
                            loc_bq_backtracks += 1;
                            let mut d_try = used_d >> 1;
                            while d_try > 1 {
                                let try_seq = next + (d_try - 1) * stride;
                                let try_ptr = unsafe { base.add(cell_offset_177(try_seq)).cast::<Cell>() };
                                if unsafe { poll_ready(try_ptr, try_seq) } {
                                    break;
                                }
                                d_try >>= 1;
                            }
                        }
                    }
                }
            }

            for _ in 0..drain_cap {
                let cell = match unsafe { Cell::poll(base.add(cell_offset_177(next)).cast(), next) } {
                    Slot::Empty => break,
                    Slot::Partial => {
                        st.partial += 1;
                        break;
                    }
                    Slot::Complete(cell) => cell,
                };
                let intact = cell.payload_matches(next);
                if !intact {
                    st.corrupt += 1;
                }
                let tsc = if intact { cell.tsc } else { 0 };
                if reply_ring == 0 {
                    reply_ring = sh.control(REPLY_RING_OFFSET).load(Ordering::Relaxed);
                }
                if reply_ring != 0 {
                    writer.send(&Cell { tsc, ..cell }, reply_ring)?;
                }
                next += stride;
            }

            let taken = (next - first) / stride;
            if taken == 0 {
                loc_empty += 1;
                if mode == MODE_BQUEUE && used_d > 1 {
                    // Wait+backtrack found 0 cells -> burst ended: reset D = 1 immediately.
                    d = 1;
                    fast_streak = 0;
                }
                if (loc_empty & 255) == 0 {
                    flush_live(
                        live, st.taken, loc_busy, loc_empty, st.partial, st.corrupt, loc_bq_probes, loc_bq_probe_hits,
                        loc_bq_wait_hits, loc_bq_backtracks, loc_bq_d_sum, loc_bq_d_gt1, loc_db_empty,
                        loc_db_ahead_empty, loc_db_ahead_partial,
                    );
                }
                continue;
            }

            st.taken += taken;
            loc_busy += 1;
            if mode == MODE_BQUEUE {
                loc_bq_d_sum += used_d;
                if used_d > 1 {
                    loc_bq_d_gt1 += 1;
                }
                if bq_fast_dt_ticks > 0 {
                    let now = rdtsc();
                    if last_hit_tsc != 0 && now.wrapping_sub(last_hit_tsc) <= bq_fast_dt_ticks {
                        fast_streak = fast_streak.saturating_add(taken);
                    } else {
                        fast_streak = taken;
                    }
                    last_hit_tsc = now;
                }
                if taken >= bq_ramp_k {
                    if d == 1 {
                        d = highest_pow2_le(taken).clamp(bq_dmin, bq_dmax);
                    } else if taken >= d {
                        d = (d * 2).clamp(bq_dmin, bq_dmax);
                    } else {
                        d = highest_pow2_le(taken).clamp(bq_dmin, bq_dmax);
                    }
                } else if bq_fast_dt_ticks > 0 && fast_streak >= bq_ramp_k {
                    // Sustained high-rate stream (e.g. breaking out of 4C 13.1 M/s cliff where taken=1/poll).
                    d = if d == 1 { bq_dmin } else { (d * 2).min(bq_dmax) };
                    fast_streak = 0;
                } else {
                    // Light load (taken < ramp_k): reset D = 1.
                    d = 1;
                }
            }
            sh.control(next_offset(ring_index)).store(next, Ordering::Relaxed);
            flush_live(
                live, st.taken, loc_busy, loc_empty, st.partial, st.corrupt, loc_bq_probes, loc_bq_probe_hits,
                loc_bq_wait_hits, loc_bq_backtracks, loc_bq_d_sum, loc_bq_d_gt1, loc_db_empty, loc_db_ahead_empty,
                loc_db_ahead_partial,
            );
        }
        st.stored = writer.stored;
        Ok(st)
    }

    fn other_reflectors() -> Vec<u32> {
        let me = std::process::id();
        let Ok(dir) = fs::read_dir("/proc") else { return Vec::new() };
        dir.flatten()
            .filter_map(|e| e.file_name().to_str()?.parse::<u32>().ok())
            .filter(|&pid| pid != me)
            .filter(|pid| {
                let cmdline = fs::read(format!("/proc/{pid}/cmdline")).unwrap_or_default();
                let argv0 = cmdline.split(|&b| b == 0).next().unwrap_or_default();
                let name = argv0.rsplit(|&b| b == b'/').next().unwrap_or_default();
                name.starts_with(b"inline_reflector") || name.starts_with(b"mrv177_card")
            })
            .collect()
    }

    fn recover(restore_only: bool) -> Result<(), Box<dyn Error>> {
        let others = other_reflectors();
        if !others.is_empty() {
            return Err(format!("another reflector runs (pid {others:?})").into());
        }
        let changed = restore_defaults(SLOT)?;
        for c in &changed {
            println!("mrv177_card: restored {c}");
        }
        if restore_only && changed.is_empty() {
            println!("mrv177_card: nothing to restore");
        }
        Ok(())
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let restore_only = std::env::args().skip(1).any(|a| a == "--restore");
        recover(restore_only)?;
        if restore_only {
            return Ok(());
        }
        let (eal, app_args) = Eal::init(std::env::args())?;
        let args = Args::parse_from(app_args);
        let cores = eal.lcores();
        if !(1..=MAX_CORES).contains(&cores) || cores > STORE_REGIONS {
            return Err(format!("unsupported core count {cores}").into());
        }

        let listener = TcpListener::bind(&args.control)
            .map_err(|e| format!("control address --control {}: {e}", args.control))?;
        let sync_listener = TcpListener::bind(&args.sync)
            .map_err(|e| format!("sync address --sync {}: {e}", args.sync))?;
        sync_listener.set_nonblocking(true)?;

        let quit = Arc::new(AtomicBool::new(false));
        let q = quit.clone();
        ctrlc::set_handler(move || q.store(true, Ordering::Relaxed))?;

        let mut writers = Vec::with_capacity(cores);
        for index in 0..cores {
            let region = OUTBOUND_REGIONS - 1 - index;
            let window = OutboundWindow::claim(region, args.host_pf, args.host_vf, true)?;
            println!(
                "mrv177_card: core {index} stores via PEM0 outbound region {region} (was {:#x}) WC -> host pf {} vf {}",
                window.previous(),
                args.host_pf,
                args.host_vf
            );
            writers.push(StoreReplies { window, cores, target: 0, stored: 0 });
        }

        let ring = Memzone::reserve_contiguous(&eal, "mrv177_ring", SLOT_BYTES, 0, SLOT_BYTES as u32)?;
        unsafe { ring.as_ptr().write_bytes(0, SLOT_BYTES) };
        let pa = ring.phys_addr().ok_or("rte_mem_virt2phy failed (run as root)")?;
        let tsc_hz = eal.tsc_hz();

        let live = (0..cores).map(|_| CoreLive::new()).collect();
        let shared = Shared { ring: &ring, quit: &quit, ring_cores: cores, tsc_hz, cfg: ModeCfg::new(), live };

        // Declared after `ring`, so Bar4Window restores slot 15 before ring is freed.
        let window = Bar4Window::claim(SLOT, pa)?;
        shared.control(CORES_OFFSET).store(cores as u64, Ordering::Relaxed);
        shared.control(STREAM_CORES_OFFSET).store(cores as u64, Ordering::Relaxed);
        for index in 0..cores {
            shared.control(next_offset(index)).store(index as u64 + 1, Ordering::Relaxed);
        }
        shared.control(READY_OFFSET).store(READY, Ordering::Release);
        println!(
            "mrv177_card: BAR4 slot {SLOT} -> pa {pa:#x} (was {:#x}); cores={cores}; tsc_hz={tsc_hz}; control={} sync={}",
            window.previous(),
            args.control,
            args.sync
        );

        let plans: Vec<(usize, StoreReplies)> = writers.into_iter().enumerate().collect();
        let results = std::thread::scope(|scope| -> Result<_, Box<dyn Error>> {
            let server = lease::serve(scope, listener, shared.control(NONCE_OFFSET))?;
            let sh_ref = &shared;
            let sync_handle = scope.spawn(move || serve_sync(sync_listener, sh_ref));
            let results = eal.on_each_lcore(plans, |_, (index, writer)| {
                serve_ring(&shared, index, writer).map_err(|e| e.to_string())
            });
            shared.control(READY_OFFSET).store(0, Ordering::Release);
            server.close();
            let _ = sync_handle.join();
            Ok(results)
        })?;

        for (i, r) in results.into_iter().enumerate() {
            let st = r.map_err(|e| format!("core {i}: {e}"))?;
            println!(
                "mrv177_card: core {i} taken={} stored={} partial={} corrupt={}",
                st.taken, st.stored, st.partial, st.corrupt
            );
        }
        Ok(())
    }

    // ---- Card TAD (8 non-multiplexed counters) + per-core ARMv8 PMU sync server ----
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

    struct PerfCounter {
        name: &'static str,
        fd: RawFd,
    }

    #[derive(Clone, Copy, Default)]
    struct CounterSample {
        name: &'static str,
        value: u64,
        enabled_ns: u64,
        running_ns: u64,
    }

    impl CounterSample {
        fn delta_from(self, earlier: Self) -> Self {
            CounterSample {
                name: self.name,
                value: self.value.saturating_sub(earlier.value),
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
        fn open(name: &'static str, type_: u32, config: u64, cpu: i32) -> io::Result<Self> {
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
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>()) };
            assert_eq!(n, size_of::<[u64; 3]>() as isize, "short read on {}", self.name);
            CounterSample { name: self.name, value: buf[0], enabled_ns: buf[1], running_ns: buf[2] }
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
        cores: Vec<(usize, u64, Vec<PerfCounter>)>,
    }

    impl CardPmu {
        fn open(sh: &Shared) -> Result<Self, String> {
            let tad_type =
                read_sysfs_u32("/sys/bus/event_source/devices/tad/type").ok_or("cannot read tad PMU type")?;
            let tad_cpu = read_sysfs_u32("/sys/bus/event_source/devices/tad/cpumask").unwrap_or(0) as i32;
            let mut tad = Vec::with_capacity(TAD_EVENTS.len());
            for &(name, cfg) in TAD_EVENTS {
                tad.push(PerfCounter::open(name, tad_type, cfg, tad_cpu).map_err(|e| format!("tad {name}: {e}"))?);
            }
            let arm_type = read_sysfs_u32("/sys/bus/event_source/devices/armv8_pmuv3/type")
                .ok_or("cannot read armv8_pmuv3 type")?;
            let mut cores = Vec::with_capacity(sh.ring_cores);
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
                let mut evs = Vec::with_capacity(CORE_EVENTS.len());
                for &(name, cfg) in CORE_EVENTS {
                    evs.push(
                        PerfCounter::open(name, arm_type, cfg, cpu as i32)
                            .map_err(|e| format!("core{i} {name}: {e}"))?,
                    );
                }
                cores.push((i, cpu, evs));
            }
            Ok(CardPmu { tad, cores })
        }
    }

    type SwArr = [u64; 14];

    struct CardSnap {
        at: Instant,
        tad: Vec<CounterSample>,
        cores: Vec<(usize, u64, Vec<CounterSample>, SwArr)>,
    }

    fn snap_card(pmu: &CardPmu, sh: &Shared, is_start: bool) -> CardSnap {
        let snap_sw = || -> Vec<(usize, u64, SwArr)> {
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
                            l.partial.load(Ordering::Relaxed),
                            l.corrupt.load(Ordering::Relaxed),
                            l.bq_probes.load(Ordering::Relaxed),
                            l.bq_probe_hits.load(Ordering::Relaxed),
                            l.bq_wait_hits.load(Ordering::Relaxed),
                            l.bq_backtracks.load(Ordering::Relaxed),
                            l.bq_d_sum.load(Ordering::Relaxed),
                            l.bq_d_gt1_polls.load(Ordering::Relaxed),
                            l.db_empty_polls.load(Ordering::Relaxed),
                            l.db_ahead_empty.load(Ordering::Relaxed),
                            l.db_ahead_partial.load(Ordering::Relaxed),
                        ],
                    )
                })
                .collect()
        };
        if is_start {
            let tad = pmu.tad.iter().map(PerfCounter::read).collect();
            let core_evs: Vec<Vec<CounterSample>> =
                pmu.cores.iter().map(|(_, _, evs)| evs.iter().map(PerfCounter::read).collect()).collect();
            let sws = snap_sw();
            let at = Instant::now();
            let cores = sws.into_iter().zip(core_evs).map(|((i, c, sw), ev)| (i, c, ev, sw)).collect();
            CardSnap { at, tad, cores }
        } else {
            let at = Instant::now();
            let sws = snap_sw();
            let core_evs: Vec<Vec<CounterSample>> =
                pmu.cores.iter().map(|(_, _, evs)| evs.iter().map(PerfCounter::read).collect()).collect();
            let tad = pmu.tad.iter().map(PerfCounter::read).collect();
            let cores = sws.into_iter().zip(core_evs).map(|((i, c, sw), ev)| (i, c, ev, sw)).collect();
            CardSnap { at, tad, cores }
        }
    }

    fn format_card_snap(s0: &CardSnap, s1: &CardSnap, sh: &Shared) -> String {
        let wall_s = s1.at.duration_since(s0.at).as_secs_f64();
        let mut min_pct = 100.0f64;
        let mut tad_parts = Vec::with_capacity(s1.tad.len());
        for (a, b) in s0.tad.iter().zip(&s1.tad) {
            let d = b.delta_from(*a);
            min_pct = min_pct.min(d.pct());
            tad_parts.push(format!("\"{}\":{{\"val\":{},\"pct\":{:.2}}}", d.name, d.value, d.pct()));
        }
        let mut core_parts = Vec::with_capacity(s1.cores.len());
        for ((idx, cpu, ev0, sw0), (_, _, ev1, sw1)) in s0.cores.iter().zip(&s1.cores) {
            let mut ev_parts = Vec::with_capacity(ev1.len());
            for (a, b) in ev0.iter().zip(ev1) {
                let d = b.delta_from(*a);
                min_pct = min_pct.min(d.pct());
                ev_parts.push(format!("\"{}\":{}", d.name, d.value));
            }
            let dsw: SwArr = std::array::from_fn(|k| sw1[k].saturating_sub(sw0[k]));
            core_parts.push(format!(
                "{{\"ring_idx\":{},\"cpu\":{},\"taken\":{},\"busy_polls\":{},\"empty_polls\":{},\
                 \"partial\":{},\"corrupt\":{},\"bq_probes\":{},\"bq_probe_hits\":{},\"bq_wait_hits\":{},\
                 \"bq_backtracks\":{},\"bq_d_sum\":{},\"bq_d_gt1_polls\":{},\"db_empty_polls\":{},\
                 \"db_ahead_empty\":{},\"db_ahead_partial\":{},{}}}",
                idx,
                cpu,
                dsw[0],
                dsw[1],
                dsw[2],
                dsw[3],
                dsw[4],
                dsw[5],
                dsw[6],
                dsw[7],
                dsw[8],
                dsw[9],
                dsw[10],
                dsw[11],
                dsw[12],
                dsw[13],
                ev_parts.join(",")
            ));
        }
        let mode_s = match sh.cfg.mode.load(Ordering::Relaxed) {
            MODE_BASELINE => "baseline",
            MODE_BQUEUE => "bqueue",
            MODE_DOORBELL => "doorbell",
            _ => "unknown",
        };
        format!(
            "{{\"wall_s\":{:.6},\"tsc_hz\":{},\"card_mode\":\"{}\",\
             \"bq_dmax\":{},\"bq_dmin\":{},\"bq_tbt_ns\":{},\"bq_ramp_k\":{},\"bq_fast_dt_ns\":{},\"bq_wait_first\":{},\
             \"min_pct\":{:.2},\"tad\":{{{}}},\"cores\":[{}]}}",
            wall_s,
            sh.tsc_hz,
            mode_s,
            sh.cfg.bq_dmax.load(Ordering::Relaxed),
            sh.cfg.bq_dmin.load(Ordering::Relaxed),
            sh.cfg.bq_tbt_ns.load(Ordering::Relaxed),
            sh.cfg.bq_ramp_k.load(Ordering::Relaxed),
            sh.cfg.bq_fast_dt_ns.load(Ordering::Relaxed),
            sh.cfg.bq_wait_first.load(Ordering::Relaxed),
            min_pct,
            tad_parts.join(","),
            core_parts.join(",")
        )
    }

    fn apply_set_mode(rest: &str, sh: &Shared) -> String {
        let mut parts = rest.split_whitespace();
        let Some(mode_name) = parts.next() else {
            return "ERR missing mode".into();
        };
        let mode = match mode_name {
            "baseline" => MODE_BASELINE,
            "bqueue" => MODE_BQUEUE,
            "doorbell" => MODE_DOORBELL,
            other => return format!("ERR unknown mode {other}"),
        };
        let mut dmax = 16u64;
        let mut dmin = if sh.ring_cores > 1 { 2u64 } else { 4u64 };
        let mut tbt_ns = 200u64;
        let mut ramp_k = 4u64;
        let mut fast_dt_ns = 0u64;
        let mut wait_first = 0u64;
        for kv in parts {
            let Some((k, v)) = kv.split_once('=') else {
                return format!("ERR bad param {kv}");
            };
            let Ok(val) = v.parse::<u64>() else {
                return format!("ERR bad int {kv}");
            };
            match k {
                "dmax" => dmax = val,
                "dmin" => dmin = val,
                "tbt_ns" => tbt_ns = val,
                "ramp_k" => ramp_k = val,
                "fast_dt_ns" => fast_dt_ns = val,
                "wait_first" => wait_first = val,
                _ => return format!("ERR unknown param {k}"),
            }
        }
        sh.cfg.mode.store(mode, Ordering::Relaxed);
        sh.cfg.bq_dmax.store(dmax, Ordering::Relaxed);
        sh.cfg.bq_dmin.store(dmin, Ordering::Relaxed);
        sh.cfg.bq_tbt_ns.store(tbt_ns, Ordering::Relaxed);
        sh.cfg.bq_tbt_ticks.store(ns_to_ticks(tbt_ns, sh.tsc_hz), Ordering::Relaxed);
        sh.cfg.bq_ramp_k.store(ramp_k, Ordering::Relaxed);
        sh.cfg.bq_fast_dt_ns.store(fast_dt_ns, Ordering::Relaxed);
        sh.cfg.bq_fast_dt_ticks.store(ns_to_ticks(fast_dt_ns, sh.tsc_hz), Ordering::Relaxed);
        sh.cfg.bq_wait_first.store(wait_first, Ordering::Relaxed);
        let target_ep = sh.cfg.epoch.fetch_add(1, Ordering::AcqRel) + 1;
        // Wait until all ring cores acknowledge the new mode epoch.
        let t0 = Instant::now();
        while t0.elapsed() < Duration::from_secs(2) {
            if (0..sh.ring_cores).all(|i| sh.live[i].seen_epoch.load(Ordering::Acquire) == target_ep) {
                return format!("OK epoch={target_ep} mode={mode_name}");
            }
            std::thread::sleep(Duration::from_micros(50));
        }
        "ERR timeout waiting for cores to switch mode".into()
    }

    fn serve_sync(listener: TcpListener, sh: &Shared) {
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
        while !sh.quit.load(Ordering::Relaxed) {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let cmd = line.trim();
            let reply = if cmd == "PING" {
                "OK".to_string()
            } else if cmd == "PMU_START" {
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
                        format_card_snap(&s0, &s1, sh)
                    }
                    _ => "ERR no PMU_START".to_string(),
                }
            } else if let Some(rest) = cmd.strip_prefix("SET_MODE ") {
                apply_set_mode(rest, sh)
            } else {
                format!("ERR unknown cmd: {cmd}")
            };
            if writeln!(stream, "{reply}").is_err() || stream.flush().is_err() {
                break;
            }
        }
    }
}
