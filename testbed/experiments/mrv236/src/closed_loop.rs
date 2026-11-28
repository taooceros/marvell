//! Closed-loop request/reply driver shared by the host clients: keep `window`
//! requests outstanding, each reply releasing one new request. How a request
//! leaves the host is the caller's `submit`; how replies come back is a
//! [`Replies`] source: reflected frames on a port ([`FrameReplies`]) or cells
//! the card writes into host memory (`inline::RingReplies`).

use std::error::Error;
use std::fmt;

use dpdk::{Burst, Eal, Port, rdtsc, rdtsc_after_loads};

use testbed::{Latency, parse_reply};

/// One reply as a source hands it over.
pub struct Reply {
    pub seq: u64,
    /// The `tsc` the request carried, as echoed; 0 if the reflector found the
    /// request corrupt.
    pub tsc: u64,
    /// TSC when the reply was seen complete.
    pub at: u64,
    pub integrity: Integrity,
    /// MRV-50/MRV-75/MRV-236 card-side timestamps, empty-poll counters, and core id (0 when unstamped).
    pub card_first_poll_ts: u64,
    pub card_ts: u64,
    pub card_out_ts: u64,
    pub card_write_ticks: u64,
    pub card_waited_polls: u64,
    pub card_core: u64,
    pub card_ring_idx: u64,
    pub waited_polls: u64,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Integrity {
    /// Complete when first seen.
    Whole,
    /// Its head was visible this many TSC ticks before its tail.
    LateTail(u64),
    /// The tail never matched within [`TAIL_WAIT_US`].
    Torn,
}

pub trait Replies {
    /// Hand every reply that has arrived to `on_reply`; returns how many
    /// arrivals there were (including foreign traffic), for loss detection.
    fn poll(&mut self, on_reply: impl FnMut(Reply)) -> usize;
}

/// How long to wait for a lagging frame tail before calling the reply torn.
pub const TAIL_WAIT_US: u64 = 1000;

/// Replies as reflected frames on RX queues `0..queues` of a port (a card
/// that answers from several cores sends on several queues). A frame whose
/// trailing `seq` copy lags its head (the receive path exposed it before all
/// of it had landed) is waited for, up to [`TAIL_WAIT_US`].
pub struct FrameReplies<'p, 'e> {
    port: &'p Port<'e>,
    queues: u16,
    burst: Burst<'e>,
    tail_limit: u64,
}

impl<'p, 'e> FrameReplies<'p, 'e> {
    pub fn new(eal: &Eal, port: &'p Port<'e>, queues: u16) -> Self {
        FrameReplies { port, queues, burst: Burst::new(), tail_limit: eal.tsc_hz() / 1_000_000 * TAIL_WAIT_US }
    }
}

impl Replies for FrameReplies<'_, '_> {
    fn poll(&mut self, mut on_reply: impl FnMut(Reply)) -> usize {
        let mut n = 0;
        for queue in 0..self.queues {
            n += self.port.rx(queue, &mut self.burst);
            let now = rdtsc_after_loads();
            for pkt in self.burst.as_mut_slice() {
                let Some(frame) = parse_reply(pkt.data()) else { continue };
                let (integrity, at) = if frame.tail == frame.seq {
                    (Integrity::Whole, now)
                } else {
                    match wait_for_tail(pkt.data(), frame.seq, now, self.tail_limit) {
                        Some(seen) => (Integrity::LateTail(seen - now), seen),
                        None => (Integrity::Torn, now),
                    }
                };
                on_reply(Reply {
                    seq: frame.seq,
                    tsc: frame.tsc,
                    at,
                    integrity,
                    card_first_poll_ts: 0,
                    card_ts: 0,
                    card_out_ts: 0,
                    card_write_ticks: 0,
                    card_waited_polls: 0,
                    card_core: 0,
                    card_ring_idx: 0,
                    waited_polls: 0,
                });
            }
            self.burst.clear();
        }
        n
    }
}

/// Spin until the frame's trailing `seq` copy equals `seq`, for at most
/// `limit` TSC ticks after `since`; returns the TSC when it did.
fn wait_for_tail(frame: &[u8], seq: u64, since: u64, limit: u64) -> Option<u64> {
    // SAFETY: the last 8 bytes of the frame, inside the mbuf; the device may
    // still be writing them, hence the volatile (unaligned) reads.
    let tail = unsafe { frame.as_ptr().add(frame.len() - 8).cast::<[u8; 8]>() };
    loop {
        let value = u64::from_ne_bytes(unsafe { tail.read_volatile() });
        let now = rdtsc_after_loads();
        if value == seq {
            return Some(now);
        }
        if now - since > limit {
            return None;
        }
        std::hint::spin_loop();
    }
}

pub struct ClosedLoop {
    /// Measured round trips, after `warmup`.
    pub rounds: u64,
    /// Requests kept outstanding (concurrency).
    pub window: u64,
    pub warmup: u64,
}

/// MRV-75 experiment options (default = untouched baseline behavior).
#[derive(Clone, Copy, Default)]
pub struct RunOpts {
    /// Max requests submitted per outer loop iteration (0 = unlimited up to window).
    pub max_send_burst: u64,
    /// Minimum inter-send spacing in TSC ticks between consecutive submits (0 = none).
    pub send_gap_ticks: u64,
    /// Capture up to `record_cap` stamped replies into `Outcome::records` (0 = none).
    pub record_cap: usize,
    /// Skip this many measured messages after `warmup` before recording.
    pub record_skip: u64,
}

pub struct Outcome {
    pub window: u64,
    pub measured: usize,
    pub lost: u64,
    /// Replies that match no outstanding request: unknown or duplicate `seq`,
    /// or a `tsc` that differs from the one sent with that `seq`.
    pub bad: u64,
    /// Replies whose tail lagged their head but arrived; `late_max_us` is the
    /// longest lag.
    pub late: u64,
    pub late_max_us: f64,
    /// Replies whose tail never arrived.
    pub torn: u64,
    pub msg_per_s: f64,
    pub latency: Latency,
    /// Reply polls, and how many found nothing: mostly empty polls at
    /// saturation mean replies, not this core, set the pace.
    pub polls: u64,
    pub empty_polls: u64,
    /// Outer loop iterations that submitted >= 1 request.
    pub send_batches: u64,
    /// Recorded stamped replies:
    /// `[seq, send_tsc, seen_tsc, card_t_first_poll, card_t_in, card_t_out, card_write_ticks, card_waited_polls, card_core, card_ring_idx, host_waited_polls]`.
    pub records: Vec<[u64; 11]>,
}

impl Outcome {
    /// Fails unless every reply matched its request intact.
    pub fn check(&self) -> Result<(), String> {
        if self.bad > 0 || self.torn > 0 {
            return Err(format!("{} bad and {} torn replies", self.bad, self.torn));
        }
        Ok(())
    }
}

impl fmt::Display for Outcome {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "window {} rounds {} lost {} bad {} torn {} late {} (max {:.2} us) | {:.3} Mmsg/s | {} | polls {:.0}% empty",
            self.window,
            self.measured,
            self.lost,
            self.bad,
            self.torn,
            self.late,
            self.late_max_us,
            self.msg_per_s / 1e6,
            self.latency,
            self.empty_polls as f64 * 100.0 / self.polls.max(1) as f64
        )
    }
}

/// Outstanding requests by `seq % PENDING`: the `tsc` each was sent with
/// (0 = free). Larger than any window, so live entries never collide.
const PENDING: usize = 4096;

impl ClosedLoop {
    /// `submit(seq, tsc)` sends request `seq` (1-based) stamped with `tsc`;
    /// the reflector must echo both back through `replies`.
    pub fn run(
        &self,
        eal: &Eal,
        replies: &mut impl Replies,
        submit: impl FnMut(u64, u64) -> Result<(), Box<dyn Error>>,
    ) -> Result<Outcome, Box<dyn Error>> {
        self.run_with_opts(eal, replies, RunOpts::default(), submit)
    }

    pub fn run_with_opts(
        &self,
        eal: &Eal,
        replies: &mut impl Replies,
        opts: RunOpts,
        submit: impl FnMut(u64, u64) -> Result<(), Box<dyn Error>>,
    ) -> Result<Outcome, Box<dyn Error>> {
        self.run_bracketed(eal, replies, opts, |_| Ok(()), submit)
    }

    pub fn run_bracketed(
        &self,
        eal: &Eal,
        replies: &mut impl Replies,
        opts: RunOpts,
        mut on_phase: impl FnMut(bool) -> Result<(), Box<dyn Error>>,
        mut submit: impl FnMut(u64, u64) -> Result<(), Box<dyn Error>>,
    ) -> Result<Outcome, Box<dyn Error>> {
        assert!((self.window as usize) < PENDING);
        let hz = eal.tsc_hz();
        let total = self.warmup + self.rounds;
        let (mut sent, mut done, mut lost, mut bad) = (0u64, 0u64, 0u64, 0u64);
        let (mut late, mut late_max, mut torn) = (0u64, 0u64, 0u64);
        let mut pending = vec![0u64; PENDING];
        // Real 4 KiB physical page non-zero pre-touch (fixes MRV-50 calloc zero-page fault at seq=20512).
        let mut rtt = vec![1u64; self.rounds as usize];
        std::hint::black_box(rtt.as_mut_ptr());
        rtt.clear();
        let mut records: Vec<[u64; 11]> = if opts.record_cap > 0 {
            let mut v = vec![[1u64; 11]; opts.record_cap];
            std::hint::black_box(v.as_mut_ptr());
            v.clear();
            v
        } else {
            Vec::new()
        };
        let mut first_measured = 0;
        let (mut polls, mut empty_polls, mut send_batches) = (0u64, 0u64, 0u64);
        let burst_limit = if opts.max_send_burst == 0 { u64::MAX } else { opts.max_send_burst };
        let gap_ticks = opts.send_gap_ticks;
        let mut last_send_tsc = 0u64;
        let record_start = self.warmup + opts.record_skip;
        on_phase(true)?;
        let mut last_rx = rdtsc();

        while done < total {
            let mut sent_this_iter = 0u64;
            while sent < total && sent - done < self.window && sent_this_iter < burst_limit {
                if gap_ticks > 0 && last_send_tsc != 0 {
                    while rdtsc().wrapping_sub(last_send_tsc) < gap_ticks {
                        std::hint::spin_loop();
                    }
                }
                sent += 1;
                let tsc = rdtsc();
                if gap_ticks > 0 {
                    last_send_tsc = tsc;
                }
                pending[sent as usize % PENDING] = tsc;
                submit(sent, tsc)?;
                sent_this_iter += 1;
            }
            send_batches += u64::from(sent_this_iter > 0);

            let n = replies.poll(|r| {
                let slot = &mut pending[r.seq as usize % PENDING];
                if r.seq == 0 || r.seq > sent || r.tsc == 0 || *slot != r.tsc {
                    bad += 1;
                    return;
                }
                *slot = 0;
                done += 1;
                match r.integrity {
                    Integrity::Whole => {}
                    Integrity::LateTail(waited) => {
                        late += 1;
                        late_max = late_max.max(waited);
                    }
                    Integrity::Torn => {
                        torn += 1;
                        return;
                    }
                }
                if opts.record_cap > 0 && r.card_ts > 1 && r.seq > record_start && records.len() < opts.record_cap {
                    records.push([
                        r.seq,
                        r.tsc,
                        r.at,
                        r.card_first_poll_ts,
                        r.card_ts,
                        r.card_out_ts,
                        r.card_write_ticks,
                        r.card_waited_polls,
                        r.card_core,
                        r.card_ring_idx,
                        r.waited_polls,
                    ]);
                }
                if r.seq > self.warmup {
                    if rtt.is_empty() {
                        first_measured = r.tsc;
                    }
                    rtt.push(r.at - r.tsc);
                }
            });

            let now = rdtsc();
            polls += 1;
            empty_polls += u64::from(n == 0);
            if n > 0 {
                last_rx = now;
            } else if now - last_rx > hz / 10 {
                lost += sent - done;
                done = sent;
                pending.fill(0);
                last_rx = now;
                if lost > 1000 {
                    return Err(format!("no replies (lost {lost}); is the reflector up?").into());
                }
            }
        }
        let loop_end_tsc = rdtsc();
        on_phase(false)?;

        let secs = (loop_end_tsc - first_measured) as f64 / hz as f64;
        let measured = rtt.len();
        Ok(Outcome {
            window: self.window,
            measured,
            lost,
            bad,
            late,
            late_max_us: late_max as f64 * 1e6 / hz as f64,
            torn,
            msg_per_s: measured as f64 / secs,
            latency: Latency::from_ticks(rtt, hz),
            polls,
            empty_polls,
            send_batches,
            records,
        })
    }
}
