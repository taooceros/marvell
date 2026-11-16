//! Closed-loop request/reply driver shared by the host clients: keep `window`
//! requests outstanding, each reply releasing one new request. How a request
//! leaves the host is the caller's `submit`; how replies come back is a
//! [`Replies`] source: reflected frames on a port ([`FrameReplies`]) or cells
//! the card writes into host memory (`inline::RingReplies`).

use std::error::Error;
use std::fmt;

use dpdk::{Burst, Eal, Port, rdtsc, rdtsc_after_loads};

use crate::{Latency, parse_reply};

/// One reply as a source hands it over.
pub struct Reply {
    pub seq: u64,
    /// The `tsc` the request carried, as echoed; 0 if the reflector found the
    /// request corrupt.
    pub tsc: u64,
    /// TSC when the reply was seen complete.
    pub at: u64,
    pub integrity: Integrity,
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
                on_reply(Reply { seq: frame.seq, tsc: frame.tsc, at, integrity });
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
        mut submit: impl FnMut(u64, u64) -> Result<(), Box<dyn Error>>,
    ) -> Result<Outcome, Box<dyn Error>> {
        assert!((self.window as usize) < PENDING);
        let hz = eal.tsc_hz();
        let total = self.warmup + self.rounds;
        let (mut sent, mut done, mut lost, mut bad) = (0u64, 0u64, 0u64, 0u64);
        let (mut late, mut late_max, mut torn) = (0u64, 0u64, 0u64);
        let mut pending = vec![0u64; PENDING];
        let mut rtt = Vec::with_capacity(self.rounds as usize);
        let mut first_measured = 0;
        let mut last_rx = rdtsc();
        let (mut polls, mut empty_polls) = (0u64, 0u64);

        while done < total {
            while sent < total && sent - done < self.window {
                sent += 1;
                let tsc = rdtsc();
                pending[sent as usize % PENDING] = tsc;
                submit(sent, tsc)?;
            }

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
                // 100 ms of silence: count the outstanding requests as lost
                // (a late reply for one of them then counts as bad).
                lost += sent - done;
                done = sent;
                pending.fill(0);
                last_rx = now;
                if lost > 1000 {
                    return Err(format!("no replies (lost {lost}); is the reflector up?").into());
                }
            }
        }

        let secs = (rdtsc() - first_measured) as f64 / hz as f64;
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
        })
    }
}
