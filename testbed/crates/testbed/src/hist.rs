//! Fast multi-resolution latency histogram for high-rate host/card benchmarks.
//!
//! Covers `[0, 1 s]` in four linear resolution bands without floating-point work
//! on the hot recording path:
//! - `[0, 20 us)`: 10 ns bins (2,000 bins)
//! - `[20 us, 200 us)`: 100 ns bins (1,800 bins)
//! - `[200 us, 20 ms)`: 10 us bins (1,980 bins)
//! - `[20 ms, 1 s)`: 1 ms bins (980 bins)
//! - `>= 1 s`: overflow bin

const B0: usize = 2_000;
const B1: usize = 1_800;
const B2: usize = 1_980;
const B3: usize = 980;
const TOTAL_BINS: usize = B0 + B1 + B2 + B3 + 1;

/// Allocation-free hot-path latency histogram (nanosecond input, microsecond queries).
#[derive(Clone, Debug)]
pub struct FastHist {
    bins: Vec<u64>,
    count: u64,
    sum_ns: u128,
    min_ns: u64,
    max_ns: u64,
}

impl Default for FastHist {
    fn default() -> Self {
        Self::new()
    }
}

impl FastHist {
    pub fn new() -> Self {
        Self {
            bins: vec![0u64; TOTAL_BINS],
            count: 0,
            sum_ns: 0,
            min_ns: u64::MAX,
            max_ns: 0,
        }
    }

    pub fn reset(&mut self) {
        self.bins.fill(0);
        self.count = 0;
        self.sum_ns = 0;
        self.min_ns = u64::MAX;
        self.max_ns = 0;
    }

    #[inline(always)]
    pub fn record_ns(&mut self, ns: u64) {
        let idx = if ns < 20_000 {
            (ns / 10) as usize
        } else if ns < 200_000 {
            B0 + ((ns - 20_000) / 100) as usize
        } else if ns < 20_000_000 {
            B0 + B1 + ((ns - 200_000) / 10_000) as usize
        } else if ns < 1_000_000_000 {
            B0 + B1 + B2 + ((ns - 20_000_000) / 1_000_000) as usize
        } else {
            TOTAL_BINS - 1
        };
        self.bins[idx] += 1;
        self.count += 1;
        self.sum_ns += ns as u128;
        if ns < self.min_ns {
            self.min_ns = ns;
        }
        if ns > self.max_ns {
            self.max_ns = ns;
        }
    }

    pub fn merge_from(&mut self, other: &FastHist) {
        for (a, &b) in self.bins.iter_mut().zip(other.bins.iter()) {
            *a += b;
        }
        self.count += other.count;
        self.sum_ns += other.sum_ns;
        if other.min_ns < self.min_ns {
            self.min_ns = other.min_ns;
        }
        if other.max_ns > self.max_ns {
            self.max_ns = other.max_ns;
        }
    }

    pub fn count(&self) -> u64 {
        self.count
    }

    pub fn overflow_cnt(&self) -> u64 {
        self.bins[TOTAL_BINS - 1]
    }

    pub fn min_us(&self) -> f64 {
        if self.count == 0 { 0.0 } else { self.min_ns as f64 / 1000.0 }
    }

    pub fn max_us(&self) -> f64 {
        self.max_ns as f64 / 1000.0
    }

    pub fn mean_us(&self) -> f64 {
        if self.count == 0 {
            0.0
        } else {
            (self.sum_ns as f64) / (self.count as f64) / 1000.0
        }
    }

    pub fn pct_us(&self, q: f64) -> f64 {
        if self.count == 0 {
            return 0.0;
        }
        let target = ((self.count as f64) * q).ceil().max(1.0) as u64;
        let mut acc = 0u64;
        for (i, &c) in self.bins.iter().enumerate() {
            acc += c;
            if acc >= target {
                let ns = if i < B0 {
                    (i as u64) * 10 + 5
                } else if i < B0 + B1 {
                    20_000 + ((i - B0) as u64) * 100 + 50
                } else if i < B0 + B1 + B2 {
                    200_000 + ((i - B0 - B1) as u64) * 10_000 + 5_000
                } else if i < TOTAL_BINS - 1 {
                    20_000_000 + ((i - B0 - B1 - B2) as u64) * 1_000_000 + 500_000
                } else {
                    self.max_ns
                };
                return ns as f64 / 1000.0;
            }
        }
        self.max_us()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fast_hist_bands_and_merge() {
        let mut h1 = FastHist::new();
        for ns in [500, 1_000, 1_500, 25_000, 500_000] {
            h1.record_ns(ns);
        }
        assert_eq!(h1.count(), 5);
        assert_eq!(h1.overflow_cnt(), 0);
        assert!((h1.min_us() - 0.5).abs() < 1e-6);
        assert!((h1.max_us() - 500.0).abs() < 1e-6);
        assert!((h1.pct_us(0.50) - 1.505).abs() < 0.02);

        let mut h2 = FastHist::new();
        h2.record_ns(2_000_000_000);
        h1.merge_from(&h2);
        assert_eq!(h1.count(), 6);
        assert_eq!(h1.overflow_cnt(), 1);
        assert!((h1.max_us() - 2_000_000.0).abs() < 1e-3);
    }
}
