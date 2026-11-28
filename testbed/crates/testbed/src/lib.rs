//! Shared pieces of the host <-> card benchmarks: the ping-pong wire format,
//! latency summaries & histograms, the closed-loop driver, the BAR4 inline path
//! and lease, MMIO/BAR4-NC mappings, Intel DSA portal helpers, non-multiplexed
//! Linux PMU sampling, and zero-page-fault runtime setup.

pub mod bar4_nc;
pub mod closed_loop;
#[cfg(target_arch = "x86_64")]
pub mod dsa;
pub mod hist;
pub mod inline;
pub mod lease;
pub mod mmio;
pub mod pmu;
pub mod rt;
use std::fmt;
use std::str::FromStr;

/// Requests are raw Ethernet frames with the IEEE local experimental EtherType:
/// `dst(6) src(6) type(2) seq(8) tsc(8) 0… seq(8)`. The trailing copy of `seq`
/// ends the frame, so a receiver that sees the head before the tail of a DMA
/// write notices.
pub const ETHERTYPE: u16 = 0x88b5;
pub const MIN_FRAME: u16 = 60;
const SEQ: usize = 14;
const TSC: usize = SEQ + 8;

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Mac(pub [u8; 6]);

impl FromStr for Mac {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, String> {
        let bytes: Vec<u8> = s
            .split(':')
            .map(|b| u8::from_str_radix(b, 16))
            .collect::<Result<_, _>>()
            .map_err(|e| format!("bad MAC {s:?}: {e}"))?;
        bytes.try_into().map(Mac).map_err(|_| format!("bad MAC {s:?}: need 6 bytes"))
    }
}

impl fmt::Display for Mac {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let [a, b, c, d, e, g] = self.0;
        write!(f, "{a:02x}:{b:02x}:{c:02x}:{d:02x}:{e:02x}:{g:02x}")
    }
}

/// Fill `frame` (already sized, at least [`MIN_FRAME`]) with a request.
pub fn write_request(frame: &mut [u8], dst: Mac, src: Mac, seq: u64, tsc: u64) {
    assert!(frame.len() >= MIN_FRAME as usize);
    frame.fill(0);
    frame[0..6].copy_from_slice(&dst.0);
    frame[6..12].copy_from_slice(&src.0);
    frame[12..14].copy_from_slice(&ETHERTYPE.to_be_bytes());
    frame[SEQ..TSC].copy_from_slice(&seq.to_ne_bytes());
    frame[TSC..TSC + 8].copy_from_slice(&tsc.to_ne_bytes());
    let tail = frame.len() - 8;
    frame[tail..].copy_from_slice(&seq.to_ne_bytes());
}

/// The fields of a reflected request frame.
pub struct FrameReply {
    pub seq: u64,
    pub tsc: u64,
    /// The trailing `seq` copy; differs from `seq` if the frame was read
    /// before all of it had landed.
    pub tail: u64,
}

/// The reply in `frame`, or `None` for other traffic.
pub fn parse_reply(frame: &[u8]) -> Option<FrameReply> {
    let field = |at: usize| Some(u64::from_ne_bytes(frame.get(at..at + 8)?.try_into().ok()?));
    (frame.get(12..14)? == ETHERTYPE.to_be_bytes()).then_some(())?;
    (frame.len() >= MIN_FRAME as usize).then_some(())?;
    Some(FrameReply { seq: field(SEQ)?, tsc: field(TSC)?, tail: field(frame.len() - 8)? })
}

/// Reflect a frame back to its sender.
pub fn swap_macs(frame: &mut [u8]) {
    let (dst, rest) = frame.split_at_mut(6);
    dst.swap_with_slice(&mut rest[..6]);
}

/// Distribution of round-trip times, in microseconds.
pub struct Latency {
    sorted_us: Vec<f64>,
}

impl Latency {
    pub fn from_ticks(mut ticks: Vec<u64>, tsc_hz: u64) -> Self {
        ticks.sort_unstable();
        let us = 1e6 / tsc_hz as f64;
        Latency { sorted_us: ticks.into_iter().map(|t| t as f64 * us).collect() }
    }

    pub fn quantile(&self, q: f64) -> f64 {
        self.sorted_us[((self.sorted_us.len() - 1) as f64 * q) as usize]
    }

    pub fn mean(&self) -> f64 {
        self.sorted_us.iter().sum::<f64>() / self.sorted_us.len() as f64
    }
}

impl fmt::Display for Latency {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let q = |q| self.quantile(q);
        write!(
            f,
            "RTT us: min {:.2} p50 {:.2} p90 {:.2} p99 {:.2} p99.9 {:.2} p99.99 {:.2} max {:.2} mean {:.2}",
            q(0.0),
            q(0.5),
            q(0.9),
            q(0.99),
            q(0.999),
            q(0.9999),
            q(1.0),
            self.mean()
        )
    }
}
