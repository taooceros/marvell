//! Host-side DPDK standard-SDP (`octeon_ep`) benchmark for MRV-213 (MRV-203 E2:
//! real 100GbE + standard SDP burst protection, concurrent 32 MiB KV tail latency,
//! and full line-rate drain).
//!
//! Single-variable 5-arm comparison (data plane 100% standard `octeon_ep` SDP RX):
//!   0) `kv-solo`:     Unloaded Socket-1 32 MiB KV random lookup baseline (`cpu41`)
//!   1) `large-mono`:  Large SDP OQ (8192 desc) + >=128 MiB strict FIFO (`ring_sp_sc`, cache=0) pool
//!   2) `small-drop`:  Small passive SDP OQ (256 desc, `allow_small_oq=1`) + compact LIFO pool; card drops on full
//!   3) `large-lifo`:  Large SDP OQ (8192 desc) + >=128 MiB ideal LIFO (`stack`, cache=0) pool
//!   4) `native-bp`:   Small SDP OQ (`allow_small_oq=1`) + compact LIFO pool + card elastic FIFO via native
//!                     `SDP SLIST_DBELL / WMARK` backpressure (zero BAR4 credit ablation)
//!   5) `credit-ring`: Compact hot SDP OQ (512 desc, `WMARK=128`, `R_eff=128..256` strictly `< 512-128-64`)
//!                     + per-queue compact LIFO (`stack`) hot pool (Pitfall 2)
//!                     + NO-SFENCE `MOVDIR64B -> BAR4` explicit credit aligned after `DROQ_REFILL_THRESHOLD=64`
//!                     (Pitfalls 1 & 4) under MRV-26 lease (`testbed::lease`).
//!
//! Pinning & Telemetry:
//!   - RX core: `cpu40` (Socket 1, DPDK EAL main lcore)
//!   - Concurrent 32 MiB KV worker: `cpu41` (Socket 1 independent physical core, no SMT sharing,
//!     Node 1 `MPOL_BIND` 2 MiB hugepages, zero page faults)
//!   - Burst TCP trigger helper thread: pinned to `cpu0` (Socket 0, never disturbs Socket 1)
//!   - 100% non-multiplexed Host PMU (`cpu40` RX + `cpu41` KV + 32x CHA DDIO + M2PCIe1 + 4x IIO1 + 8x IMC)
//!     paired with CN106 card PMU (ARMv8 cores + TAD L3 + 6-ch DDR5) and `mercury` PHY stats.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv213_host runs on the x86_64 host");
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
    use std::mem::size_of;
    use std::net::TcpStream;
    use std::os::fd::RawFd;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, EthStats, Mbuf, Mempool, Port, rdtsc, rdtsc_after_loads};
    use testbed::lease::{self, Lease, LeasedSlot};
    use testbed::mmio::{has_movdir64b, movdir64b};
    use mrv213::{
        Arm, CARD_SYNC_ADDR, CreditLine, DROQ_REFILL_THRESHOLD, MAX_QUEUES, MERC_CTRL_ADDR,
        PktProfile, StreamTracker, WireCheck, burst_packet_count, credit_line_offset,
        credit_window_avoids_wmark, poison_wire_mbuf, verify_wire_frame,
    };

    const PORT: u16 = 0;
    const BURST_N: usize = 32;
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const HELPER_CPU_SOCK0: i32 = 0;
    const HUGE_2M: usize = 2 << 20;

    // NUMA policy syscalls
    const MPOL_BIND: i32 = 2;
    const MPOL_MF_STRICT: u32 = 1 << 0;
    const MPOL_MF_MOVE: u32 = 1 << 1;
    const MPOL_F_NODE: u64 = 1 << 0;
    const MPOL_F_ADDR: u64 = 1 << 1;

    // KV worker phases
    const KV_PHASE_WARMUP: u32 = 0;
    const KV_PHASE_CALIB: u32 = 1;
    const KV_PHASE_MEASURE: u32 = 2;
    const KV_PHASE_STOP: u32 = 3;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum ArmArg {
        KvSolo,
        LargeMono,
        SmallDrop,
        LargeLifo,
        NativeBp,
        CreditRing,
    }

    impl From<ArmArg> for Arm {
        fn from(v: ArmArg) -> Self {
            match v {
                ArmArg::KvSolo => Arm::KvSolo,
                ArmArg::LargeMono => Arm::LargeMono,
                ArmArg::SmallDrop => Arm::SmallDrop,
                ArmArg::LargeLifo => Arm::LargeLifo,
                ArmArg::NativeBp => Arm::NativeBp,
                ArmArg::CreditRing => Arm::CreditRing,
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum)]
        arm: ArmArg,
        /// Packet size profile (`64b`, `256b`, `1500b`, `1094b`).
        #[arg(long, default_value = "1500b")]
        profile: String,
        /// Burst payload size in L2 bytes (`65536` ..= `134217728`).
        #[arg(long, default_value_t = 16 * 1024 * 1024)]
        burst_bytes: usize,
        /// Number of measured bursts.
        #[arg(long, default_value_t = 8)]
        bursts: usize,
        /// Number of warmup bursts before PMU start (minimum 1 when not kv-solo).
        #[arg(long, default_value_t = 2)]
        warmup_bursts: usize,
        /// Number of SDP RX queues (must match CN106 card worker cores, 1..=8).
        #[arg(long, default_value_t = 4)]
        sdp_queues: u16,
        /// Host SDP RX descriptor count per queue (256/512 with allow_small_oq=1, or 8192 for large arms).
        #[arg(long, default_value_t = 512)]
        rx_desc: u16,
        /// Host SDP TX descriptor count per queue (2048 minimum on octeon_ep IQ).
        #[arg(long, default_value_t = 2048)]
        tx_desc: u16,
        /// Optional explicit host mbuf data room size (0 = auto from profile).
        #[arg(long, default_value_t = 0)]
        rx_data_room: u16,
        /// Host pool bytes target for `large-mono` / `large-lifo` (default 128 MiB of packet payload).
        #[arg(long, default_value_t = 128 * 1024 * 1024)]
        large_pool_bytes: usize,
        /// Extra mbufs above `rx_desc` per queue for compact hot pools (Arms 2/4/5, Pitfall 2).
        #[arg(long, default_value_t = 128)]
        compact_headroom: u32,
        /// Logical credit window `R_eff` per queue in packets (Arm 5 `credit-ring`).
        #[arg(long, default_value_t = 256)]
        r_eff: u64,
        /// Optional byte-granular credit window `R_eff_bytes` per queue (0 = packet-only).
        #[arg(long, default_value_t = 0)]
        r_eff_bytes: u64,
        /// Credit return batch size `B` in packets per queue (default 64, aligned to DROQ_REFILL_THRESHOLD).
        #[arg(long, default_value_t = 64)]
        credit_batch: u64,
        /// Allow `r_eff + 64 >= rx_desc - WMARK` (only for intentional ablation tests).
        #[arg(long)]
        allow_unsafe_reff: bool,
        /// Card elastic FIFO storage mode (`compact` or `mbuf`).
        #[arg(long, default_value = "compact")]
        card_fifo_mode: String,
        /// Mercury TX threads (default 16 for 64B/256B, 8 for 1500B).
        #[arg(long, default_value_t = 8)]
        merc_threads: usize,
        /// Mercury TX batch size per doorbell/sendmmsg.
        #[arg(long, default_value_t = 32)]
        merc_batch: usize,
        /// Optional mercury offered rate cap in Mpps (0 = full 100GbE wire rate).
        #[arg(long, default_value_t = 0.0)]
        merc_rate_mpps: f64,
        /// Comma-separated RSS-balanced flow labels or UDP source ports for mercury (optional).
        #[arg(long, default_value = "")]
        merc_flows: String,
        /// Settle wait in microseconds after mercury finishes firing a burst before host starts draining (Arms 2/4/5).
        #[arg(long, default_value_t = 250)]
        burst_settle_us: u64,
        /// Enable payload poison before freeing mbufs back to the pool (torn/stale DMA audit).
        #[arg(long)]
        poison_mbufs: bool,
        /// Concurrent KV random lookup table size in bytes (0 disables KV; power of 2, default 32 MiB).
        #[arg(long, default_value_t = 32 * 1024 * 1024)]
        kv_bytes: usize,
        /// Socket 1 physical core for concurrent KV worker (independent of RX cpu40, no SMT sharing).
        #[arg(long, default_value_t = 41)]
        kv_cpu: i32,
        /// Dependent KV lookups chained per latency sample (1 = single-lookup p50/p99/p999).
        #[arg(long, default_value_t = 1)]
        kv_ops_per_sample: usize,
        /// Measurement duration in ms for `kv-solo` arm.
        #[arg(long, default_value_t = 500)]
        solo_ms: u64,
        /// Minimum active-core GHz required before starting measurement (0 disables check).
        #[arg(long, default_value_t = 3.85)]
        min_ghz: f64,
        /// Minimum warmup wall time in ms for turbo ramp + LLC steady state.
        #[arg(long, default_value_t = 1100)]
        warmup_ms: u64,
        /// Base run generation tag (unique across invocations).
        #[arg(long, default_value_t = 100)]
        run_gen: u64,
        /// PF BAR4 sysfs resource file.
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar4: String,
        /// Card MRV-26 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        card_lease: String,
        /// Card control/sync/PMU TCP address.
        #[arg(long, default_value = CARD_SYNC_ADDR)]
        card_sync: String,
        /// Mercury burst generator TCP control address.
        #[arg(long, default_value = MERC_CTRL_ADDR)]
        merc_ctrl: String,
    }

    // -------------------------------------------------------------------------
    // NUMA Node 1 2 MiB HugePage Allocator & Pinning (from MRV-165)
    // -------------------------------------------------------------------------
    struct NumaHugeBuf {
        ptr: *mut u8,
        len: usize,
    }

    unsafe impl Send for NumaHugeBuf {}
    unsafe impl Sync for NumaHugeBuf {}

    impl NumaHugeBuf {
        fn alloc_node1(bytes: usize) -> Result<Self, Box<dyn Error>> {
            let len = bytes.div_ceil(HUGE_2M) * HUGE_2M;
            let ptr = unsafe {
                libc::mmap(
                    ptr::null_mut(),
                    len,
                    libc::PROT_READ | libc::PROT_WRITE,
                    libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_HUGETLB,
                    -1,
                    0,
                )
            };
            if ptr == libc::MAP_FAILED {
                return Err(format!(
                    "mmap(MAP_HUGETLB, {} MiB) failed: {}",
                    len >> 20,
                    io::Error::last_os_error()
                )
                .into());
            }
            let nodemask: [u64; 1] = [1u64 << 1];
            let ret = unsafe {
                libc::syscall(
                    libc::SYS_mbind,
                    ptr,
                    len,
                    MPOL_BIND,
                    nodemask.as_ptr(),
                    64usize,
                    (MPOL_MF_STRICT | MPOL_MF_MOVE) as usize,
                )
            };
            if ret != 0 {
                let err = io::Error::last_os_error();
                unsafe { libc::munmap(ptr, len) };
                return Err(format!("mbind(node=1, {} MiB) failed: {err}", len >> 20).into());
            }
            let p = ptr.cast::<u8>();
            let mut off = 0usize;
            while off < len {
                unsafe { p.add(off).write_volatile(0) };
                off += 4096;
            }
            for &check_off in &[0usize, len / 2, len - 4096] {
                let mut node: i32 = -1;
                let r = unsafe {
                    libc::syscall(
                        libc::SYS_get_mempolicy,
                        &mut node as *mut i32,
                        ptr::null_mut::<u64>(),
                        0usize,
                        p.add(check_off),
                        (MPOL_F_NODE | MPOL_F_ADDR) as usize,
                    )
                };
                if r != 0 || node != 1 {
                    unsafe { libc::munmap(ptr, len) };
                    return Err(format!("NUMA verify failed at off={check_off}: ret={r} node={node}").into());
                }
            }
            Ok(Self { ptr: p, len })
        }

        fn as_ptr(&self) -> *mut u8 {
            self.ptr
        }
    }

    impl Drop for NumaHugeBuf {
        fn drop(&mut self) {
            unsafe {
                libc::munmap(self.ptr.cast::<libc::c_void>(), self.len);
            }
        }
    }

    fn thread_page_faults() -> u64 {
        unsafe {
            let mut ru: libc::rusage = std::mem::zeroed();
            if libc::getrusage(libc::RUSAGE_THREAD, &mut ru) == 0 {
                (ru.ru_minflt + ru.ru_majflt) as u64
            } else {
                0
            }
        }
    }

    fn pin_thread(cpu: i32) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu as usize, &mut set);
            libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &raw const set);
        }
    }

    fn check_numa_maps(addr: usize) -> (usize, usize, usize) {
        let Ok(txt) = fs::read_to_string("/proc/self/numa_maps") else {
            return (0, 0, 0);
        };
        let mut best = (0usize, 0usize, 0usize);
        for line in txt.lines() {
            let mut it = line.split_whitespace();
            let Some(base_hex) = it.next() else { continue };
            let Ok(base) = usize::from_str_radix(base_hex, 16) else { continue };
            if base <= addr {
                let (mut kb, mut n0, mut n1) = (4usize, 0usize, 0usize);
                for tok in it {
                    if let Some(v) = tok.strip_prefix("kernelpagesize_kB=") {
                        kb = v.parse().unwrap_or(4);
                    } else if let Some(v) = tok.strip_prefix("N0=") {
                        n0 = v.parse().unwrap_or(0);
                    } else if let Some(v) = tok.strip_prefix("N1=") {
                        n1 = v.parse().unwrap_or(0);
                    }
                }
                if base == addr {
                    return (kb, n0, n1);
                }
                best = (kb, n0, n1);
            }
        }
        best
    }

    // -------------------------------------------------------------------------
    // Concurrent Socket-1 32 MiB Cache-Sensitive KV Random Lookup Worker (`cpu41`)
    // -------------------------------------------------------------------------
    #[repr(C, align(64))]
    #[derive(Clone, Copy)]
    struct KvEntry {
        key: u64,
        val: [u64; 6],
        next_salt: u64,
    }

    const _: () = assert!(size_of::<KvEntry>() == 64);

    #[inline(always)]
    fn splitmix64(mut x: u64) -> u64 {
        x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
        x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        x ^ (x >> 31)
    }

    fn init_kv_table(buf: &NumaHugeBuf, kv_bytes: usize) {
        let n = kv_bytes / size_of::<KvEntry>();
        let entries = buf.as_ptr().cast::<KvEntry>();
        for i in 0..n {
            let k = (i as u64) ^ 0x55aa_2130_9911_2233;
            let salt = splitmix64((i as u64).wrapping_mul(0x213_cafe_0001) ^ 0xfeed_face);
            let mut val = [0u64; 6];
            for (j, slot) in val.iter_mut().enumerate() {
                *slot = salt.wrapping_add((j as u64 + 1).wrapping_mul(0x1111_2222_3333_4445));
            }
            unsafe {
                entries.add(i).write(KvEntry { key: k, val, next_salt: salt });
            }
        }
    }

    #[inline(always)]
    unsafe fn rdtsc_begin() -> u64 {
        let lo: u32;
        let hi: u32;
        unsafe {
            std::arch::asm!(
                "lfence",
                "rdtsc",
                "lfence",
                out("eax") lo,
                out("edx") hi,
                options(nostack, nomem, preserves_flags),
            );
        }
        ((hi as u64) << 32) | (lo as u64)
    }

    #[inline(always)]
    unsafe fn rdtsc_end(dep: u64) -> u64 {
        let lo: u32;
        let hi: u32;
        unsafe {
            std::arch::asm!(
                "rdtscp",
                in("rdi") dep,
                out("eax") lo,
                out("edx") hi,
                out("ecx") _,
                options(nostack, nomem, preserves_flags),
            );
        }
        ((hi as u64) << 32) | (lo as u64)
    }

    fn calibrate_kv_timer_floor() -> u64 {
        let mut samples = [0u64; 256];
        let mut acc = 0u64;
        for s in &mut samples {
            let t0 = unsafe { rdtsc_begin() };
            let t1 = unsafe { rdtsc_end(acc) };
            *s = t1.saturating_sub(t0);
            acc ^= t1;
        }
        std::hint::black_box(acc);
        samples.sort_unstable();
        samples[samples.len() / 2]
    }

    // L1d-resident dual-resolution latency histogram (24 KiB total):
    // 2048 fine bins of 2 ns (0..4096 ns) + 1024 coarse bins of 64 ns (4096..69632 ns)
    const KV_FINE_BINS: usize = 2048;
    const KV_FINE_STEP_NS: f64 = 2.0;
    const KV_COARSE_BINS: usize = 1024;
    const KV_COARSE_STEP_NS: f64 = 64.0;

    struct KvReport {
        lookups: u64,
        samples: u64,
        bad: u64,
        page_faults: u64,
        wall_s: f64,
        timer_floor_ns: f64,
        mean_ns: f64,
        min_ns: f64,
        p50_ns: f64,
        p90_ns: f64,
        p95_ns: f64,
        p99_ns: f64,
        p999_ns: f64,
        max_ns: f64,
        overflow: u64,
        checksum_acc: u64,
    }

    impl KvReport {
        fn to_json(&self) -> String {
            let mlookups_s = if self.wall_s > 0.0 {
                self.lookups as f64 / self.wall_s / 1e6
            } else {
                0.0
            };
            format!(
                "{{\"lookups\":{},\"samples\":{},\"bad\":{},\"page_faults\":{},\"wall_s\":{:.6},\
                 \"mlookups_s\":{:.4},\"timer_floor_ns\":{:.2},\"mean_ns\":{:.2},\"min_ns\":{:.2},\
                 \"p50_ns\":{:.2},\"p90_ns\":{:.2},\"p95_ns\":{:.2},\"p99_ns\":{:.2},\
                 \"p999_ns\":{:.2},\"max_ns\":{:.2},\"overflow\":{},\"checksum_acc\":{}}}",
                self.lookups,
                self.samples,
                self.bad,
                self.page_faults,
                self.wall_s,
                mlookups_s,
                self.timer_floor_ns,
                self.mean_ns,
                self.min_ns,
                self.p50_ns,
                self.p90_ns,
                self.p95_ns,
                self.p99_ns,
                self.p999_ns,
                self.max_ns,
                self.overflow,
                self.checksum_acc,
            )
        }
    }

    fn kv_percentile(
        fine: &[u64; KV_FINE_BINS],
        coarse: &[u64; KV_COARSE_BINS],
        total: u64,
        max_ns: f64,
        q: f64,
    ) -> f64 {
        if total == 0 {
            return 0.0;
        }
        let target = (((total - 1) as f64) * q).round() as u64;
        let mut acc = 0u64;
        for (i, &c) in fine.iter().enumerate() {
            if acc + c > target {
                return (i as f64 + 0.5) * KV_FINE_STEP_NS;
            }
            acc += c;
        }
        let fine_cap = KV_FINE_BINS as f64 * KV_FINE_STEP_NS;
        for (i, &c) in coarse.iter().enumerate() {
            if acc + c > target {
                return fine_cap + (i as f64 + 0.5) * KV_COARSE_STEP_NS;
            }
            acc += c;
        }
        max_ns
    }

    fn run_kv_worker(
        cpu: i32,
        table_ptr: usize,
        kv_bytes: usize,
        ops_per_sample: usize,
        hz: u64,
        phase: Arc<AtomicU32>,
        ready: Arc<AtomicBool>,
        calib_done: Arc<AtomicBool>,
    ) -> KvReport {
        pin_thread(cpu);
        let entries = table_ptr as *const KvEntry;
        let n_entries = kv_bytes / size_of::<KvEntry>();
        let mask = (n_entries - 1) as u64;

        let mut fine = Box::new([0u64; KV_FINE_BINS]);
        let mut coarse = Box::new([0u64; KV_COARSE_BINS]);
        fine.fill(0);
        coarse.fill(0);
        let _ = std::hint::black_box(calibrate_kv_timer_floor());
        let _ = thread_page_faults();

        // Warm all 2 MiB TLB entries & lines sequentially once
        let mut state: u64 = 0x1234_5678_9abc_def1;
        let mut acc: u64 = 0;
        for i in 0..n_entries {
            let e = unsafe { &*entries.add(i) };
            acc ^= e.key ^ e.next_salt;
        }
        state ^= acc;

        ready.store(true, Ordering::Release);

        while phase.load(Ordering::Acquire) == KV_PHASE_WARMUP {
            for _ in 0..64 {
                state = splitmix64(state);
                let idx = (state & mask) as usize;
                let e = unsafe { &*entries.add(idx) };
                state ^= e.next_salt;
            }
        }

        let floor_ticks = calibrate_kv_timer_floor();
        calib_done.store(true, Ordering::Release);
        loop {
            let p = phase.load(Ordering::Acquire);
            if p == KV_PHASE_MEASURE || p == KV_PHASE_STOP {
                break;
            }
            for _ in 0..16 {
                state = splitmix64(state);
                let idx = (state & mask) as usize;
                let e = unsafe { &*entries.add(idx) };
                state ^= e.next_salt;
            }
        }

        let ns_per_tick = 1e9 / hz as f64;
        let timer_floor_ns = floor_ticks as f64 * ns_per_tick;
        let fine_bins_per_tick = (ns_per_tick / KV_FINE_STEP_NS) / ops_per_sample as f64;
        let fine_mul = (fine_bins_per_tick * ((1u64 << 32) as f64)) as u64;
        let coarse_ratio = (KV_COARSE_STEP_NS / KV_FINE_STEP_NS).round() as usize;

        let mut samples = 0u64;
        let mut bad = 0u64;
        let mut sum_net_ticks = 0u64;
        let mut min_net_ticks = u64::MAX;
        let mut max_net_ticks = 0u64;
        let mut overflow = 0u64;

        let pf0 = thread_page_faults();
        let t_wall0 = Instant::now();

        while phase.load(Ordering::Acquire) == KV_PHASE_MEASURE {
            for _ in 0..16 {
                let mut idx_state = state;
                let t0 = unsafe { rdtsc_begin() };
                let mut dep = 0u64;
                for _ in 0..ops_per_sample {
                    idx_state = splitmix64(idx_state);
                    let idx = (idx_state & mask) as usize;
                    let e = unsafe { &*entries.add(idx) };
                    let expected_key = (idx as u64) ^ 0x55aa_2130_9911_2233;
                    bad += u64::from((e.key ^ expected_key) != 0);
                    let salt = e.next_salt ^ e.val[0];
                    dep ^= salt;
                    idx_state ^= e.next_salt;
                }
                let t1 = unsafe { rdtsc_end(dep) };
                state = idx_state;
                acc ^= dep;

                let raw_dt = t1.saturating_sub(t0);
                let net_dt = raw_dt.saturating_sub(floor_ticks);
                samples += 1;
                sum_net_ticks += net_dt;
                if net_dt < min_net_ticks {
                    min_net_ticks = net_dt;
                }
                if net_dt > max_net_ticks {
                    max_net_ticks = net_dt;
                }
                let bin = (((net_dt as u128) * (fine_mul as u128)) >> 32) as usize;
                if bin < KV_FINE_BINS {
                    fine[bin] += 1;
                } else {
                    let cb = (bin - KV_FINE_BINS) / coarse_ratio;
                    if cb < KV_COARSE_BINS {
                        coarse[cb] += 1;
                    } else {
                        overflow += 1;
                    }
                }
            }
        }

        let wall_s = t_wall0.elapsed().as_secs_f64();
        let pf1 = thread_page_faults();
        let lookups = samples * ops_per_sample as u64;
        let scale = ns_per_tick / ops_per_sample as f64;
        let mean_ns = if samples > 0 {
            (sum_net_ticks as f64 / samples as f64) * scale
        } else {
            0.0
        };
        let min_ns = if samples > 0 { min_net_ticks as f64 * scale } else { 0.0 };
        let max_ns = max_net_ticks as f64 * scale;

        KvReport {
            lookups,
            samples,
            bad,
            page_faults: pf1.saturating_sub(pf0),
            wall_s,
            timer_floor_ns,
            mean_ns,
            min_ns,
            p50_ns: kv_percentile(&fine, &coarse, samples, max_ns, 0.50),
            p90_ns: kv_percentile(&fine, &coarse, samples, max_ns, 0.90),
            p95_ns: kv_percentile(&fine, &coarse, samples, max_ns, 0.95),
            p99_ns: kv_percentile(&fine, &coarse, samples, max_ns, 0.99),
            p999_ns: kv_percentile(&fine, &coarse, samples, max_ns, 0.999),
            max_ns,
            overflow,
            checksum_acc: acc,
        }
    }

    // -------------------------------------------------------------------------
    // 100% Non-Multiplexed Sapphire Rapids Host PMU
    // (`cpu40` RX + `cpu41` KV + 32x CHA DDIO + M2PCIe1 + 4x IIO1 + 8x IMC)
    // -------------------------------------------------------------------------
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
        reserved2: u16,
        aux_sample_size: u32,
        reserved3: u32,
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

    const CORE_DTLB_LOAD_MISSES: u64 = 0x10003;
    const OCR_READS_TO_CORE_L3_MISS_CFG: u64 = 0x012a;
    const OCR_READS_TO_CORE_L3_MISS_RSP: u64 = 0x3F3F_C044_77;
    const OCR_READS_TO_CORE_ANY_CFG: u64 = 0x012b;
    const OCR_READS_TO_CORE_ANY_RSP: u64 = 0x3F3F_FC44_77;
    const CORE_L3_HIT: u64 = 0x04d1; // mem_load_retired.l3_hit
    const CORE_L3_MISS: u64 = 0x20d1; // mem_load_retired.l3_miss

    const CHA_TOR_INSERTS_IO_HIT_ITOM: u64 = 0x00cc_43fd_0000_0435;
    const CHA_TOR_INSERTS_IO_MISS_ITOM: u64 = 0x00cc_43fe_0000_0435;
    const UNCORE_CLOCKTICKS: u64 = 0x01;
    const IMC_CAS_COUNT_READ: u64 = 0xcf05;
    const IMC_CAS_COUNT_WRITE: u64 = 0xf005;

    const fn iio_cfg(event: u8, umask: u8, ch_mask: u64, fc_mask: u64) -> u64 {
        (event as u64) | ((umask as u64) << 8) | (ch_mask << 36) | (fc_mask << 48)
    }
    const IIO_INB_WR_TXN: u64 = iio_cfg(0x84, 0x01, 0xff, 7);
    const IIO_INB_WR_DW: u64 = iio_cfg(0x83, 0x01, 0xff, 7);
    const IIO_INB_RD_TXN: u64 = iio_cfg(0x84, 0x04, 0xff, 7);
    const IIO_CPU_WR_TXN: u64 = iio_cfg(0xc1, 0x01, 0xff, 7);

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
        en: u64,
        run: u64,
    }

    impl Sample {
        fn delta(self, prev: Self) -> Self {
            Self {
                val: self.val.saturating_sub(prev.val),
                en: self.en.saturating_sub(prev.en),
                run: self.run.saturating_sub(prev.run),
            }
        }
        fn pct(self) -> f64 {
            if self.en == 0 {
                100.0
            } else {
                (self.run as f64 / self.en as f64) * 100.0
            }
        }
    }

    impl PerfCounter {
        fn open(type_: u32, config: u64, cpu: i32) -> io::Result<Self> {
            Self::open_ext(type_, config, 0, cpu)
        }

        fn open_ext(type_: u32, config: u64, config1: u64, cpu: i32) -> io::Result<Self> {
            let attr = PerfEventAttr {
                type_,
                size: size_of::<PerfEventAttr>() as u32,
                config,
                config1,
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
            Sample { val: buf[0], en: buf[1], run: buf[2] }
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

    struct CorePmu {
        cpu: i32,
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        instr: PerfCounter,
        page_faults: PerfCounter,
        dtlb_load_misses: PerfCounter,
        ocr_l3_miss: Option<PerfCounter>,
        ocr_l3_all: Option<PerfCounter>,
        l3_hit: Option<PerfCounter>,
        l3_miss: Option<PerfCounter>,
    }

    #[derive(Clone, Copy, Default)]
    struct CoreSnap {
        cpu: i32,
        cycles: Sample,
        ref_cycles: Sample,
        instr: Sample,
        page_faults: Sample,
        dtlb_load_misses: Sample,
        ocr_l3_miss: Sample,
        ocr_l3_all: Sample,
        l3_hit: Sample,
        l3_miss: Sample,
    }

    impl CorePmu {
        fn open(cpu: i32, with_l3_retired: bool) -> Result<Self, Box<dyn Error>> {
            let (ocr_l3_miss, ocr_l3_all, l3_hit, l3_miss) = if with_l3_retired {
                (
                    None,
                    None,
                    Some(PerfCounter::open(PERF_TYPE_RAW, CORE_L3_HIT, cpu)?),
                    Some(PerfCounter::open(PERF_TYPE_RAW, CORE_L3_MISS, cpu)?),
                )
            } else {
                (
                    Some(PerfCounter::open_ext(
                        PERF_TYPE_RAW,
                        OCR_READS_TO_CORE_L3_MISS_CFG,
                        OCR_READS_TO_CORE_L3_MISS_RSP,
                        cpu,
                    )?),
                    Some(PerfCounter::open_ext(
                        PERF_TYPE_RAW,
                        OCR_READS_TO_CORE_ANY_CFG,
                        OCR_READS_TO_CORE_ANY_RSP,
                        cpu,
                    )?),
                    None,
                    None,
                )
            };
            Ok(CorePmu {
                cpu,
                cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu)?,
                ref_cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, cpu)?,
                instr: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu)?,
                page_faults: PerfCounter::open(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_PAGE_FAULTS, cpu)?,
                dtlb_load_misses: PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_DTLB_LOAD_MISSES, cpu)?,
                ocr_l3_miss,
                ocr_l3_all,
                l3_hit,
                l3_miss,
            })
        }

        fn snap(&self) -> CoreSnap {
            CoreSnap {
                cpu: self.cpu,
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                instr: self.instr.read(),
                page_faults: self.page_faults.read(),
                dtlb_load_misses: self.dtlb_load_misses.read(),
                ocr_l3_miss: self.ocr_l3_miss.as_ref().map(PerfCounter::read).unwrap_or_default(),
                ocr_l3_all: self.ocr_l3_all.as_ref().map(PerfCounter::read).unwrap_or_default(),
                l3_hit: self.l3_hit.as_ref().map(PerfCounter::read).unwrap_or_default(),
                l3_miss: self.l3_miss.as_ref().map(PerfCounter::read).unwrap_or_default(),
            }
        }
    }

    struct HostPmu {
        rx: CorePmu,
        kv: Option<CorePmu>,
        cha_io_hit: Vec<PerfCounter>,
        cha_io_miss: Vec<PerfCounter>,
        mesh_clk: PerfCounter,
        iio_wr_txn: PerfCounter,
        iio_wr_dw: PerfCounter,
        iio_rd_txn: PerfCounter,
        iio_cpu_wr: PerfCounter,
        imc_rd: Vec<PerfCounter>,
        imc_wr: Vec<PerfCounter>,
    }

    struct HostSnap {
        rx: CoreSnap,
        kv: Option<CoreSnap>,
        cha_io_hit: Vec<Sample>,
        cha_io_miss: Vec<Sample>,
        mesh_clk: Sample,
        iio_wr_txn: Sample,
        iio_wr_dw: Sample,
        iio_rd_txn: Sample,
        iio_cpu_wr: Sample,
        imc_rd: Vec<Sample>,
        imc_wr: Vec<Sample>,
    }

    impl HostPmu {
        fn open(rx_cpu: i32, kv_cpu: Option<i32>) -> Result<Self, Box<dyn Error>> {
            let rx = CorePmu::open(rx_cpu, false)?;
            let kv = match kv_cpu {
                Some(c) => Some(CorePmu::open(c, true)?),
                None => None,
            };
            let (mut cha_io_hit, mut cha_io_miss) = (Vec::with_capacity(32), Vec::with_capacity(32));
            for i in 0..32 {
                let t = read_pmu_type(&format!("uncore_cha_{i}"))?;
                cha_io_hit.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_HIT_ITOM, SOCKET1_UNCORE_CPU)?);
                cha_io_miss.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_MISS_ITOM, SOCKET1_UNCORE_CPU)?);
            }
            let mesh_clk =
                PerfCounter::open(read_pmu_type("uncore_m2pcie_1")?, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let iio_t = read_pmu_type("uncore_iio_1")?;
            let iio_wr_txn = PerfCounter::open(iio_t, IIO_INB_WR_TXN, SOCKET1_UNCORE_CPU)?;
            let iio_wr_dw = PerfCounter::open(iio_t, IIO_INB_WR_DW, SOCKET1_UNCORE_CPU)?;
            let iio_rd_txn = PerfCounter::open(iio_t, IIO_INB_RD_TXN, SOCKET1_UNCORE_CPU)?;
            let iio_cpu_wr = PerfCounter::open(iio_t, IIO_CPU_WR_TXN, SOCKET1_UNCORE_CPU)?;
            let (mut imc_rd, mut imc_wr) = (Vec::with_capacity(8), Vec::with_capacity(8));
            for i in 0..8 {
                let t = read_pmu_type(&format!("uncore_imc_{i}"))?;
                imc_rd.push(PerfCounter::open(t, IMC_CAS_COUNT_READ, SOCKET1_UNCORE_CPU)?);
                imc_wr.push(PerfCounter::open(t, IMC_CAS_COUNT_WRITE, SOCKET1_UNCORE_CPU)?);
            }
            Ok(HostPmu {
                rx,
                kv,
                cha_io_hit,
                cha_io_miss,
                mesh_clk,
                iio_wr_txn,
                iio_wr_dw,
                iio_rd_txn,
                iio_cpu_wr,
                imc_rd,
                imc_wr,
            })
        }

        fn snapshot(&self) -> HostSnap {
            HostSnap {
                rx: self.rx.snap(),
                kv: self.kv.as_ref().map(CorePmu::snap),
                cha_io_hit: self.cha_io_hit.iter().map(PerfCounter::read).collect(),
                cha_io_miss: self.cha_io_miss.iter().map(PerfCounter::read).collect(),
                mesh_clk: self.mesh_clk.read(),
                iio_wr_txn: self.iio_wr_txn.read(),
                iio_wr_dw: self.iio_wr_dw.read(),
                iio_rd_txn: self.iio_rd_txn.read(),
                iio_cpu_wr: self.iio_cpu_wr.read(),
                imc_rd: self.imc_rd.iter().map(PerfCounter::read).collect(),
                imc_wr: self.imc_wr.iter().map(PerfCounter::read).collect(),
            }
        }

        fn measure_min_ghz(&self, check_rx: bool, window_ms: u64, tsc_hz: u64) -> (f64, f64, f64) {
            let rx0_c = self.rx.cycles.read().val;
            let rx0_r = self.rx.ref_cycles.read().val;
            let kv0 = self.kv.as_ref().map(|k| (k.cycles.read().val, k.ref_cycles.read().val));
            std::thread::sleep(Duration::from_millis(window_ms));
            let rx1_c = self.rx.cycles.read().val;
            let rx1_r = self.rx.ref_cycles.read().val;
            let kv1 = self.kv.as_ref().map(|k| (k.cycles.read().val, k.ref_cycles.read().val));
            let rx_ghz = if rx1_r > rx0_r {
                (rx1_c - rx0_c) as f64 / (rx1_r - rx0_r) as f64 * (tsc_hz as f64 / 1e9)
            } else {
                0.0
            };
            let kv_ghz = match (kv0, kv1) {
                (Some((c0, r0)), Some((c1, r1))) if r1 > r0 => {
                    (c1 - c0) as f64 / (r1 - r0) as f64 * (tsc_hz as f64 / 1e9)
                }
                _ => 0.0,
            };
            let mut min_g = f64::INFINITY;
            if check_rx {
                min_g = min_g.min(rx_ghz);
            }
            if self.kv.is_some() {
                min_g = min_g.min(kv_ghz);
            }
            if min_g.is_infinite() {
                min_g = 0.0;
            }
            (min_g, rx_ghz, kv_ghz)
        }
    }

    fn format_core_snap(s0: &CoreSnap, s1: &CoreSnap, tsc_hz: u64, with_l3_ret: bool, min_pct: &mut f64) -> String {
        let mut chk = |s: Sample| {
            *min_pct = min_pct.min(s.pct());
            s.val
        };
        let cyc = chk(s1.cycles.delta(s0.cycles));
        let ref_cyc = chk(s1.ref_cycles.delta(s0.ref_cycles));
        let instr = chk(s1.instr.delta(s0.instr));
        let pf = chk(s1.page_faults.delta(s0.page_faults));
        let dtlb = chk(s1.dtlb_load_misses.delta(s0.dtlb_load_misses));
        let ghz = if ref_cyc > 0 { (cyc as f64 / ref_cyc as f64) * (tsc_hz as f64 / 1e9) } else { 0.0 };
        if with_l3_ret {
            let l3_hit = chk(s1.l3_hit.delta(s0.l3_hit));
            let l3_miss = chk(s1.l3_miss.delta(s0.l3_miss));
            let l3_tot = l3_hit + l3_miss;
            let l3_miss_pct = if l3_tot > 0 { 100.0 * l3_miss as f64 / l3_tot as f64 } else { 0.0 };
            format!(
                "{{\"cpu\":{},\"ghz\":{:.4},\"cycles\":{},\"ref_cycles\":{},\"instructions\":{},\
                 \"page_faults\":{},\"dtlb_load_misses\":{},\"l3_hit\":{},\"l3_miss\":{},\"l3_miss_pct\":{:.4}}}",
                s0.cpu, ghz, cyc, ref_cyc, instr, pf, dtlb, l3_hit, l3_miss, l3_miss_pct
            )
        } else {
            let ocr_miss = chk(s1.ocr_l3_miss.delta(s0.ocr_l3_miss));
            let ocr_all = chk(s1.ocr_l3_all.delta(s0.ocr_l3_all));
            let ocr_miss_pct = if ocr_all > 0 { 100.0 * ocr_miss as f64 / ocr_all as f64 } else { 0.0 };
            format!(
                "{{\"cpu\":{},\"ghz\":{:.4},\"cycles\":{},\"ref_cycles\":{},\"instructions\":{},\
                 \"page_faults\":{},\"dtlb_load_misses\":{},\"ocr_l3_all\":{},\"ocr_l3_miss\":{},\
                 \"ocr_miss_pct\":{:.4}}}",
                s0.cpu, ghz, cyc, ref_cyc, instr, pf, dtlb, ocr_all, ocr_miss, ocr_miss_pct
            )
        }
    }

    fn format_host_pmu(s0: &HostSnap, s1: &HostSnap, wall_s: f64, tsc_hz: u64) -> String {
        let mut min_pct = 100.0f64;
        let rx_json = format_core_snap(&s0.rx, &s1.rx, tsc_hz, false, &mut min_pct);
        let kv_json = match (&s0.kv, &s1.kv) {
            (Some(k0), Some(k1)) => format_core_snap(k0, k1, tsc_hz, true, &mut min_pct),
            _ => "null".to_string(),
        };
        let mut chk = |s: Sample| {
            min_pct = min_pct.min(s.pct());
            s.val
        };
        let mesh_clk = chk(s1.mesh_clk.delta(s0.mesh_clk));
        let iio_wr_txn = chk(s1.iio_wr_txn.delta(s0.iio_wr_txn));
        let iio_wr_dw = chk(s1.iio_wr_dw.delta(s0.iio_wr_dw));
        let iio_rd_txn = chk(s1.iio_rd_txn.delta(s0.iio_rd_txn));
        let iio_cpu_wr = chk(s1.iio_cpu_wr.delta(s0.iio_cpu_wr));
        let mut cha_hit = 0u64;
        let mut cha_miss = 0u64;
        for (a, b) in s0.cha_io_hit.iter().zip(&s1.cha_io_hit) {
            cha_hit += chk(b.delta(*a));
        }
        for (a, b) in s0.cha_io_miss.iter().zip(&s1.cha_io_miss) {
            cha_miss += chk(b.delta(*a));
        }
        let mut imc_rd_ch = [0u64; 8];
        let mut imc_wr_ch = [0u64; 8];
        let mut imc_rd_all = 0u64;
        let mut imc_wr_all = 0u64;
        for i in 0..8 {
            imc_rd_ch[i] = chk(s1.imc_rd[i].delta(s0.imc_rd[i]));
            imc_wr_ch[i] = chk(s1.imc_wr[i].delta(s0.imc_wr[i]));
            imc_rd_all += imc_rd_ch[i];
            imc_wr_all += imc_wr_ch[i];
        }
        let imc_0_6_rd = imc_rd_ch[0] + imc_rd_ch[6];
        let imc_0_6_wr = imc_wr_ch[0] + imc_wr_ch[6];
        let mesh_ghz = (mesh_clk as f64 / wall_s.max(1e-9)) / 1e9;
        let ddio_tot = cha_hit + cha_miss;
        let ddio_hit_pct = if ddio_tot > 0 { 100.0 * cha_hit as f64 / ddio_tot as f64 } else { 100.0 };
        let iio_wr_bytes = iio_wr_dw * 4;
        let iio_b_per_txn = if iio_wr_txn > 0 { iio_wr_bytes as f64 / iio_wr_txn as f64 } else { 0.0 };
        let iio_gb_s = iio_wr_bytes as f64 / wall_s.max(1e-9) / 1e9;
        let to_mib = |cas: u64| cas as f64 * 64.0 / (1024.0 * 1024.0);
        let ch_rd_s: Vec<String> = imc_rd_ch.iter().map(|v| v.to_string()).collect();
        let ch_wr_s: Vec<String> = imc_wr_ch.iter().map(|v| v.to_string()).collect();

        format!(
            "{{\"wall_s\":{:.6},\"min_pct\":{:.2},\"mesh_ghz\":{:.4},\"rx\":{},\"kv\":{},\
             \"cha_io_hit_itom\":{},\"cha_io_miss_itom\":{},\"ddio_hit_pct\":{:.4},\
             \"iio_inb_wr_txn\":{},\"iio_inb_wr_bytes\":{},\"iio_inb_wr_b_per_txn\":{:.2},\"iio_inb_wr_gb_s\":{:.4},\
             \"iio_inb_rd_txn\":{},\"iio_cpu_wr_txn\":{},\
             \"imc_0_6_cas_rd\":{},\"imc_0_6_cas_wr\":{},\"imc_0_6_rd_mib\":{:.4},\"imc_0_6_wr_mib\":{:.4},\
             \"imc_all_cas_rd\":{},\"imc_all_cas_wr\":{},\"imc_all_rd_mib\":{:.4},\"imc_all_wr_mib\":{:.4},\
             \"imc_ch_rd\":[{}],\"imc_ch_wr\":[{}]}}",
            wall_s,
            min_pct,
            mesh_ghz,
            rx_json,
            kv_json,
            cha_hit,
            cha_miss,
            ddio_hit_pct,
            iio_wr_txn,
            iio_wr_bytes,
            iio_b_per_txn,
            iio_gb_s,
            iio_rd_txn,
            iio_cpu_wr,
            imc_0_6_rd,
            imc_0_6_wr,
            to_mib(imc_0_6_rd),
            to_mib(imc_0_6_wr),
            imc_rd_all,
            imc_wr_all,
            to_mib(imc_rd_all),
            to_mib(imc_wr_all),
            ch_rd_s.join(","),
            ch_wr_s.join(","),
        )
    }

    // -------------------------------------------------------------------------
    // TCP Sync Helpers & Socket-0 Asynchronous Mercury Burst Trigger
    // -------------------------------------------------------------------------
    fn tcp_rpc(addr: &str, cmd: &str, timeout_secs: u64) -> Result<String, Box<dyn Error>> {
        let mut s = TcpStream::connect(addr)?;
        s.set_nodelay(true)?;
        s.set_read_timeout(Some(Duration::from_secs(timeout_secs)))?;
        s.set_write_timeout(Some(Duration::from_secs(5)))?;
        writeln!(s, "{cmd}")?;
        s.flush()?;
        let mut r = BufReader::new(s);
        let mut line = String::new();
        let n = r.read_line(&mut line)?;
        if n == 0 {
            return Err(format!("EOF from {addr} on '{cmd}'").into());
        }
        Ok(line.trim().to_string())
    }

    struct MercTrigger {
        req_gen: AtomicU64,
        done_gen: AtomicU64,
        quit: AtomicBool,
        cmd: Mutex<String>,
        reply: Mutex<Result<String, String>>,
    }

    impl MercTrigger {
        fn new() -> Self {
            Self {
                req_gen: AtomicU64::new(0),
                done_gen: AtomicU64::new(0),
                quit: AtomicBool::new(false),
                cmd: Mutex::new(String::new()),
                reply: Mutex::new(Ok(String::new())),
            }
        }

        fn fire(&self, cmd: String) -> u64 {
            *self.cmd.lock().unwrap() = cmd;
            self.req_gen.fetch_add(1, Ordering::AcqRel) + 1
        }

        fn is_done(&self, gen_id: u64) -> bool {
            self.done_gen.load(Ordering::Acquire) >= gen_id
        }

        fn wait_reply_spin(&self, gen_id: u64) -> Result<String, Box<dyn Error>> {
            let t0 = Instant::now();
            while !self.is_done(gen_id) {
                if t0.elapsed() > Duration::from_secs(30) {
                    return Err("timeout waiting for mercury burst reply".into());
                }
                std::hint::spin_loop();
            }
            match self.reply.lock().unwrap().clone() {
                Ok(s) => Ok(s),
                Err(e) => Err(e.into()),
            }
        }
    }

    fn format_eth_stats(s: &EthStats) -> String {
        format!(
            "{{\"ipackets\":{},\"ibytes\":{},\"imissed\":{},\"ierrors\":{},\"rx_nombuf\":{}}}",
            s.ipackets, s.ibytes, s.imissed, s.ierrors, s.rx_nombuf
        )
    }

    fn percentile_f64(sorted: &[f64], q: f64) -> f64 {
        if sorted.is_empty() {
            return 0.0;
        }
        let idx = (((sorted.len() - 1) as f64) * q).round() as usize;
        sorted[idx.min(sorted.len() - 1)]
    }

    /// Per-queue NO-SFENCE BAR4 credit state aligned strictly after `cnxk_ep_rx_refill`
    /// (`DROQ_REFILL_THRESHOLD = 64`) has written descriptors and rung `SLIST_DBELL`.
    struct CreditState {
        taken_pkts: [u64; MAX_QUEUES],
        taken_bytes: [u64; MAX_QUEUES],
        unrefilled: [u64; MAX_QUEUES],
        refilled_pkts: [u64; MAX_QUEUES],
        refilled_bytes: [u64; MAX_QUEUES],
        published_pkts: [u64; MAX_QUEUES],
        bar4_writes: u64,
    }

    impl CreditState {
        fn new() -> Self {
            Self {
                taken_pkts: [0; MAX_QUEUES],
                taken_bytes: [0; MAX_QUEUES],
                unrefilled: [0; MAX_QUEUES],
                refilled_pkts: [0; MAX_QUEUES],
                refilled_bytes: [0; MAX_QUEUES],
                published_pkts: [0; MAX_QUEUES],
                bar4_writes: 0,
            }
        }

        /// Called immediately AFTER `port.rx(q, &mut burst)` returns: if `unrefilled[q] >= 64`
        /// before that call, `cnxk_ep_recv_pkts_avx` just executed `cnxk_ep_rx_refill(droq)`
        /// and knocked `SLIST_DBELL` at the top of `rx_burst`, so those descriptors are now
        /// safely visible to CN106 SDP hardware and eligible for NO-SFENCE `MOVDIR64B` credit.
        #[inline(always)]
        fn on_post_rx_poll(
            &mut self,
            q: usize,
            l2_bytes: u64,
            credit_batch: u64,
            run_gen: u64,
            bar4: Option<&LeasedSlot<'_>>,
        ) -> Result<(), Box<dyn Error>> {
            if self.unrefilled[q] >= DROQ_REFILL_THRESHOLD {
                let refilled = self.unrefilled[q];
                self.unrefilled[q] = 0;
                self.refilled_pkts[q] += refilled;
                self.refilled_bytes[q] += refilled * l2_bytes;
                if let Some(slot) = bar4 {
                    if self.refilled_pkts[q].saturating_sub(self.published_pkts[q]) >= credit_batch {
                        slot.check()?;
                        let line = CreditLine::new(
                            self.refilled_pkts[q],
                            self.refilled_bytes[q],
                            run_gen,
                            rdtsc(),
                            q as u16,
                        );
                        // Pitfall 4: single 64 B atomic NO-SFENCE MOVDIR64B to exclusive BAR4 line.
                        unsafe {
                            let dst = slot.as_ptr().add(credit_line_offset(q));
                            movdir64b(dst, &line);
                        }
                        self.published_pkts[q] = self.refilled_pkts[q];
                        self.bar4_writes += 1;
                    }
                }
            }
            Ok(())
        }

        #[inline(always)]
        fn on_consumed(&mut self, q: usize, n: u64, l2_bytes: u64) {
            self.taken_pkts[q] += n;
            self.taken_bytes[q] += n * l2_bytes;
            self.unrefilled[q] += n;
        }

        /// Force-publish any residual `< 64` taken packets at end of burst after draining
        /// (safe because `R_eff + 64 < rx_desc - WMARK`).
        fn flush_all(
            &mut self,
            nq: usize,
            run_gen: u64,
            bar4: Option<&LeasedSlot<'_>>,
        ) -> Result<(), Box<dyn Error>> {
            if let Some(slot) = bar4 {
                for q in 0..nq {
                    if self.taken_pkts[q] > self.published_pkts[q] {
                        slot.check()?;
                        let line = CreditLine::new(
                            self.taken_pkts[q],
                            self.taken_bytes[q],
                            run_gen,
                            rdtsc(),
                            q as u16,
                        );
                        unsafe {
                            let dst = slot.as_ptr().add(credit_line_offset(q));
                            movdir64b(dst, &line);
                        }
                        self.published_pkts[q] = self.taken_pkts[q];
                        self.bar4_writes += 1;
                    }
                }
            }
            Ok(())
        }
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let mut raw_args: Vec<String> = std::env::args().collect();
        let dash = raw_args.iter().position(|a| a == "--");
        let (eal_args, app_args) = match dash {
            Some(i) => (
                raw_args[..i].to_vec(),
                std::iter::once(raw_args[0].clone()).chain(raw_args.drain(i + 1..)).collect(),
            ),
            None => (vec![raw_args[0].clone()], raw_args),
        };
        let args = Args::parse_from(app_args);
        let arm: Arm = args.arm.into();
        let profile = PktProfile::from_name(&args.profile)
            .ok_or_else(|| format!("unknown --profile '{}'", args.profile))?;

        let nq = args.sdp_queues as usize;
        if nq == 0 || nq > MAX_QUEUES {
            return Err(format!("sdp_queues={nq} must be in 1..={MAX_QUEUES}").into());
        }
        if args.kv_bytes > 0 && (!args.kv_bytes.is_power_of_two() || args.kv_bytes < 4096) {
            return Err(format!("kv_bytes={} must be 0 or power-of-two >= 4096", args.kv_bytes).into());
        }
        if arm == Arm::CreditRing {
            if !has_movdir64b() {
                return Err("CPU lacks MOVDIR64B required for credit-ring arm".into());
            }
            if !args.allow_unsafe_reff && !credit_window_avoids_wmark(args.rx_desc, args.r_eff) {
                return Err(format!(
                    "Pitfall 1 violation: r_eff={} + 64 >= rx_desc={} - WMARK (pass --allow-unsafe-reff to override)",
                    args.r_eff, args.rx_desc
                )
                .into());
            }
        }

        let (eal, _) = Eal::init(eal_args)?;
        let tsc_hz = eal.tsc_hz();
        let rx_cpu = unsafe { libc::sched_getcpu() };
        let socket = Port::socket_of(&eal, PORT);

        // Bind process memory policy to Node 1 and lock pages to prevent AutoNUMA/minor page faults.
        unsafe {
            let nodemask: [u64; 1] = [1u64 << 1];
            let _ = libc::syscall(libc::SYS_set_mempolicy, MPOL_BIND, nodemask.as_ptr(), 64usize);
        }

        // Allocate optional Node-1 2 MiB hugepage KV table on independent physical core (`cpu41`)
        let kv_buf = if args.kv_bytes > 0 {
            let b = NumaHugeBuf::alloc_node1(args.kv_bytes)?;
            init_kv_table(&b, args.kv_bytes);
            Some(b)
        } else {
            None
        };
        let (kv_page_kb, kv_n0, kv_n1) = match &kv_buf {
            Some(b) => check_numa_maps(b.as_ptr() as usize),
            None => (0, 0, 0),
        };
        unsafe {
            let _ = libc::mlockall(libc::MCL_CURRENT | libc::MCL_FUTURE);
        }
        let host_pmu = HostPmu::open(rx_cpu, if args.kv_bytes > 0 { Some(args.kv_cpu) } else { None })?;

        // ---- Mode 0: Unloaded KV solo baseline ----
        if arm == Arm::KvSolo {
            let Some(kv_buf_ref) = kv_buf.as_ref() else {
                return Err("kv-solo requires --kv-bytes > 0".into());
            };
            let phase = Arc::new(AtomicU32::new(KV_PHASE_WARMUP));
            let ready = Arc::new(AtomicBool::new(false));
            let calib_done = Arc::new(AtomicBool::new(false));
            let kv_ptr = kv_buf_ref.as_ptr() as usize;
            let kv_bytes = args.kv_bytes;
            let kv_cpu = args.kv_cpu;
            let ops = args.kv_ops_per_sample;
            let p_c = Arc::clone(&phase);
            let r_c = Arc::clone(&ready);
            let cd_c = Arc::clone(&calib_done);

            let kv_handle = std::thread::spawn(move || {
                run_kv_worker(kv_cpu, kv_ptr, kv_bytes, ops, tsc_hz, p_c, r_c, cd_c)
            });
            while !ready.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(1));
            }
            let wu_t0 = Instant::now();
            while wu_t0.elapsed() < Duration::from_millis(args.warmup_ms) {
                std::thread::sleep(Duration::from_millis(50));
            }
            if args.min_ghz > 0.0 {
                let deadline = Instant::now() + Duration::from_secs(6);
                while Instant::now() < deadline {
                    let (mg, _, _) = host_pmu.measure_min_ghz(false, 100, tsc_hz);
                    if mg >= args.min_ghz {
                        break;
                    }
                }
            }
            phase.store(KV_PHASE_CALIB, Ordering::Release);
            while !calib_done.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(1));
            }
            let p0 = host_pmu.snapshot();
            let t0 = Instant::now();
            phase.store(KV_PHASE_MEASURE, Ordering::Release);
            std::thread::sleep(Duration::from_millis(args.solo_ms));
            phase.store(KV_PHASE_STOP, Ordering::Release);
            let p1 = host_pmu.snapshot();
            let wall_s = t0.elapsed().as_secs_f64();
            let kv_rep = kv_handle.join().expect("kv worker panicked");
            let pmu_json = format_host_pmu(&p0, &p1, wall_s, tsc_hz);

            println!(
                "MRV213 {{\"arm\":\"kv-solo\",\"profile\":\"{}\",\"l2_bytes\":{},\"burst_bytes\":0,\"burst_pkts\":0,\
                 \"bursts\":0,\"offered_pkts\":0,\"rx_pkts\":0,\"lost_pkts\":0,\"bad\":0,\"torn\":0,\"gaps\":0,\"ooo\":0,\
                 \"kv_bytes\":{},\"kv_page_kb\":{},\"kv_n0\":{},\"kv_n1\":{},\
                 \"kv\":{},\"host_pmu\":{},\"card_pmu\":null,\"card_stats\":null,\"mercury\":null}}",
                profile.as_str(),
                profile.l2_bytes(),
                args.kv_bytes,
                kv_page_kb,
                kv_n0,
                kv_n1,
                kv_rep.to_json(),
                pmu_json,
            );
            return Ok(());
        }

        // ---- Arms 1..5: Standard DPDK `octeon_ep` SDP RX ----
        let data_room = if args.rx_data_room > 0 {
            args.rx_data_room
        } else {
            profile.host_data_room()
        };
        let burst_align = (args.merc_threads * args.merc_batch).max(64);
        let burst_pkts = burst_packet_count(args.burst_bytes, profile, burst_align);

        // Configure host mempool architecture cleanly per arm (Pitfall 2!)
        let (pool_ops_name, pool_size_total, per_q_pools): (&str, u32, Vec<Mempool<'_>>) = match arm {
            Arm::LargeMono => {
                // Arm 1: single strict FIFO (`ring_sp_sc`, cache=0) spanning >= large_pool_bytes
                let min_pkts = args.large_pool_bytes.div_ceil(profile.l2_bytes()).max(burst_pkts as usize);
                let sz = (min_pkts + nq * (args.rx_desc as usize) + 4096) as u32;
                let mp = Mempool::new_with_opts(
                    &eal,
                    "m213_large_mono",
                    sz,
                    0,
                    data_room,
                    socket,
                    Some("ring_sp_sc"),
                )?;
                ("ring_sp_sc", sz, vec![mp])
            }
            Arm::LargeLifo => {
                // Arm 3: single ideal LIFO (`stack`, cache=0) spanning >= large_pool_bytes
                let min_pkts = args.large_pool_bytes.div_ceil(profile.l2_bytes()).max(burst_pkts as usize);
                let sz = (min_pkts + nq * (args.rx_desc as usize) + 4096) as u32;
                let mp = Mempool::new_with_opts(
                    &eal,
                    "m213_large_lifo",
                    sz,
                    0,
                    data_room,
                    socket,
                    Some("stack"),
                )?;
                ("stack", sz, vec![mp])
            }
            Arm::SmallDrop | Arm::NativeBp | Arm::CreditRing => {
                // Arms 2/4/5 (Pitfall 2): per-queue compact dedicated LIFO (`stack`, cache=0) hot pool
                let per_q = (args.rx_desc as u32) + args.compact_headroom;
                let mut v = Vec::with_capacity(nq);
                for q in 0..nq {
                    let mp = Mempool::new_with_opts(
                        &eal,
                        &format!("m213_hot_q{q}"),
                        per_q,
                        0,
                        data_room,
                        socket,
                        Some("stack"),
                    )?;
                    v.push(mp);
                }
                ("stack-compact", per_q * nq as u32, v)
            }
            Arm::KvSolo => unreachable!(),
        };

        let pool_refs: Vec<&Mempool<'_>> = if per_q_pools.len() == 1 {
            vec![&per_q_pools[0]; nq]
        } else {
            per_q_pools.iter().collect()
        };

        let port = Port::start_per_queue_ex(
            &eal,
            PORT,
            &pool_refs,
            args.rx_desc,
            args.tx_desc,
            Some(1500),
            false,
        )?;
        let _ = port.promiscuous();

        // Optional MRV-26 BAR4 lease (ONLY for Arm 5 `credit-ring`!)
        let lease_opt: Option<Lease> = if arm.uses_bar4() {
            Some(Lease::acquire(&args.card_lease)?)
        } else {
            None
        };
        let bar4_slot: Option<LeasedSlot<'_>> = match lease_opt.as_ref() {
            Some(l) => Some(l.map_slot(&args.bar4)?),
            None => None,
        };

        // Configure mercury burst generator once (QPs / UDP sockets stay hot across warmup & measure)
        let merc_mode = match profile {
            PktProfile::Uc64 => "uc64",
            PktProfile::Ud256 => "ud256",
            PktProfile::Udp1500 => "udp1500",
            PktProfile::Ud1094 => "ud1094",
        };
        let flows_tok = if args.merc_flows.is_empty() {
            String::new()
        } else {
            format!(" flows={}", args.merc_flows)
        };
        let wu_run_gen = args.run_gen;
        let meas_run_gen = args.run_gen + 1;

        let merc_cfg_cmd = format!(
            "CONFIG mode={} threads={} batch={} rate_mpps={:.3} run_gen={}{}",
            merc_mode, args.merc_threads, args.merc_batch, args.merc_rate_mpps, wu_run_gen, flows_tok
        );
        let merc_cfg_res = tcp_rpc(&args.merc_ctrl, &merc_cfg_cmd, 15)?;
        if !merc_cfg_res.contains("\"ok\":true") {
            return Err(format!("mercury CONFIG failed: {merc_cfg_res}").into());
        }

        // Configure CN106 card for warmup
        let card_cfg_wu = format!(
            "CONFIG policy={} profile={} fifo_mode={} run_gen={} r_eff={} r_eff_bytes={}",
            arm.card_policy(),
            profile.as_str(),
            args.card_fifo_mode,
            wu_run_gen,
            args.r_eff,
            args.r_eff_bytes,
        );
        let card_cfg_res = tcp_rpc(&args.card_sync, &card_cfg_wu, 10)?;
        if !card_cfg_res.contains("\"ok\":true") {
            return Err(format!("card CONFIG failed: {card_cfg_res}").into());
        }

        // Spawn Socket-0 async mercury trigger thread + optional Socket-1 cpu41 KV thread
        let merc_trig = Arc::new(MercTrigger::new());
        let merc_addr_bg = args.merc_ctrl.clone();
        let trig_bg = Arc::clone(&merc_trig);
        let trig_handle = std::thread::spawn(move || {
            pin_thread(HELPER_CPU_SOCK0);
            let mut handled = 0u64;
            while !trig_bg.quit.load(Ordering::Relaxed) {
                let rg = trig_bg.req_gen.load(Ordering::Acquire);
                if rg > handled {
                    let cmd = trig_bg.cmd.lock().unwrap().clone();
                    let res = tcp_rpc(&merc_addr_bg, &cmd, 30).map_err(|e| e.to_string());
                    *trig_bg.reply.lock().unwrap() = res;
                    handled = rg;
                    trig_bg.done_gen.store(rg, Ordering::Release);
                } else {
                    std::thread::sleep(Duration::from_micros(25));
                }
            }
        });

        let kv_phase = Arc::new(AtomicU32::new(KV_PHASE_WARMUP));
        let kv_ready = Arc::new(AtomicBool::new(false));
        let kv_calib = Arc::new(AtomicBool::new(false));
        let kv_handle = if let Some(kb) = kv_buf.as_ref() {
            let p_c = Arc::clone(&kv_phase);
            let r_c = Arc::clone(&kv_ready);
            let cd_c = Arc::clone(&kv_calib);
            let ptr = kb.as_ptr() as usize;
            let bytes = args.kv_bytes;
            let cpu = args.kv_cpu;
            let ops = args.kv_ops_per_sample;
            Some(std::thread::spawn(move || {
                run_kv_worker(cpu, ptr, bytes, ops, tsc_hz, p_c, r_c, cd_c)
            }))
        } else {
            None
        };
        if kv_handle.is_some() {
            while !kv_ready.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(1));
            }
        }

        let l2_bytes = profile.l2_bytes() as u64;
        let wire_bytes = profile.wire_bytes() as u64;
        let retain_in_host = arm.retains_in_host();
        let expect_drops = arm.expects_drops();
        let mut rx_burst: Burst<'_, BURST_N> = Burst::new();
        let mut host_backlog: Vec<Mbuf<'_>> = if retain_in_host {
            let cap = (burst_pkts as usize) + 1024;
            let mut v: Vec<Mbuf<'_>> = Vec::with_capacity(cap);
            // Pre-fault spare capacity pages so zero minor page faults occur during burst arrival.
            unsafe {
                ptr::write_bytes(v.as_mut_ptr(), 0, v.capacity());
            }
            v
        } else {
            Vec::new()
        };

        // Helper closure: execute one burst and return `(rx_pkts, bad, torn, drain_ticks)`
        let mut run_one_burst = |burst_idx: u16,
                                 cur_run_gen: u64,
                                 credit: &mut CreditState,
                                 tracker: &mut StreamTracker|
         -> Result<(u64, u64, u64, u64), Box<dyn Error>> {
            let bcmd = format!("BURST burst_id={burst_idx} pkts={burst_pkts}");
            let gen_id = merc_trig.fire(bcmd);
            let mut b_rx = 0u64;
            let mut b_bad = 0u64;
            let mut b_torn = 0u64;
            let drain_ticks: u64;

            if retain_in_host {
                // Arms 1 (`large-mono`) & 3 (`large-lifo`):
                // Pull burst from SDP into host backlog queue while mercury fires (forcing
                // all B_bytes of packet data into host memory via standard SDP DMA), then
                // verify & drain host backlog at full speed.
                host_backlog.clear();
                let mut idle_after_tx: Option<Instant> = None;
                let hard_deadline = Instant::now() + Duration::from_secs(10);
                while (host_backlog.len() as u64) < burst_pkts && Instant::now() < hard_deadline {
                    let mut got_any = false;
                    for q in 0..nq {
                        let n = port.rx(q as u16, &mut rx_burst);
                        if n > 0 {
                            got_any = true;
                            rx_burst.drain_into(|m| host_backlog.push(m));
                        }
                    }
                    if !got_any && merc_trig.is_done(gen_id) {
                        let t0 = *idle_after_tx.get_or_insert_with(Instant::now);
                        if t0.elapsed() > Duration::from_millis(20) {
                            break;
                        }
                    } else if got_any {
                        idle_after_tx = None;
                    }
                }
                let _ = merc_trig.wait_reply_spin(gen_id)?;

                let t_d0 = rdtsc_after_loads();
                for mut m in host_backlog.drain(..) {
                    match verify_wire_frame(m.data(), profile, cur_run_gen) {
                        WireCheck::Ok { stream_id, seq, is_psn24, .. } => {
                            tracker.observe(stream_id, seq, is_psn24);
                            b_rx += 1;
                        }
                        WireCheck::Bad => b_bad += 1,
                        WireCheck::Torn => b_torn += 1,
                        WireCheck::Ignore => {}
                    }
                    if args.poison_mbufs {
                        poison_wire_mbuf(m.data_mut(), profile);
                    }
                    // Drop returns mbuf directly to FIFO (`ring_sp_sc`) or LIFO (`stack`) pool
                    drop(m);
                }
                let t_d1 = rdtsc_after_loads();
                drain_ticks = t_d1.saturating_sub(t_d0);
            } else {
                // Arms 2 (`small-drop`), 4 (`native-bp`), 5 (`credit-ring`):
                // Host consumer pauses during burst arrival while mercury fires at 100GbE;
                // CN106 either drops on small-ring overflow (Arm 2) or absorbs in card
                // L3/DDR5 elastic FIFO (Arms 4 & 5). Then host drains at full line rate!
                let _ = merc_trig.wait_reply_spin(gen_id)?;
                if args.burst_settle_us > 0 {
                    let settle_ticks = (tsc_hz / 1_000_000) * args.burst_settle_us;
                    let t_s = rdtsc();
                    while rdtsc().saturating_sub(t_s) < settle_ticks {
                        std::hint::spin_loop();
                    }
                }

                let t_d0 = rdtsc_after_loads();
                let mut last_rx_tsc = t_d0;
                let mut idle_since: Option<Instant> = None;
                let drain_deadline = Instant::now() + Duration::from_secs(10);
                let idle_limit = if expect_drops {
                    Duration::from_millis(2)
                } else {
                    Duration::from_millis(50)
                };

                while b_rx < burst_pkts && Instant::now() < drain_deadline {
                    let mut got_any = false;
                    for q in 0..nq {
                        let n = port.rx(q as u16, &mut rx_burst);
                        // Pitfall 1 & 4: publish NO-SFENCE BAR4 credit strictly AFTER
                        // `port.rx` triggered `cnxk_ep_rx_refill` + `SLIST_DBELL`!
                        credit.on_post_rx_poll(q, l2_bytes, args.credit_batch, cur_run_gen, bar4_slot.as_ref())?;
                        if n > 0 {
                            got_any = true;
                            last_rx_tsc = rdtsc_after_loads();
                            for m in rx_burst.as_mut_slice() {
                                match verify_wire_frame(m.data(), profile, cur_run_gen) {
                                    WireCheck::Ok { stream_id, seq, is_psn24, .. } => {
                                        tracker.observe(stream_id, seq, is_psn24);
                                        b_rx += 1;
                                    }
                                    WireCheck::Bad => b_bad += 1,
                                    WireCheck::Torn => b_torn += 1,
                                    WireCheck::Ignore => {}
                                }
                                if args.poison_mbufs {
                                    poison_wire_mbuf(m.data_mut(), profile);
                                }
                            }
                            rx_burst.clear();
                            credit.on_consumed(q, n as u64, l2_bytes);
                        }
                    }
                    if got_any {
                        idle_since = None;
                    } else {
                        // In credit-ring, ensure residual < 64 credit tail is flushed when momentarily idle
                        if arm.uses_bar4() {
                            credit.flush_all(nq, cur_run_gen, bar4_slot.as_ref())?;
                        }
                        let t0 = *idle_since.get_or_insert_with(Instant::now);
                        if t0.elapsed() >= idle_limit {
                            break;
                        }
                    }
                }
                if arm.uses_bar4() {
                    credit.flush_all(nq, cur_run_gen, bar4_slot.as_ref())?;
                }
                let t_d1 = rdtsc_after_loads();
                drain_ticks = if b_rx == burst_pkts {
                    t_d1.saturating_sub(t_d0)
                } else if b_rx > 0 {
                    last_rx_tsc.saturating_sub(t_d0)
                } else {
                    0
                };
            }

            Ok((b_rx, b_bad, b_torn, drain_ticks))
        };

        // ---- Warmup Phase ----
        let wu_t0 = Instant::now();
        let mut wu_credit = CreditState::new();
        let mut wu_tracker = StreamTracker::new();
        for b in 1..=args.warmup_bursts.max(1) {
            let _ = run_one_burst(b as u16, wu_run_gen, &mut wu_credit, &mut wu_tracker)?;
        }
        // Keep cpu40 spinning hot through minimum warmup wall time so both cpu40 & cpu41 hold 3.90 GHz
        let mut flush_burst: Burst<'_, BURST_N> = Burst::new();
        while wu_t0.elapsed() < Duration::from_millis(args.warmup_ms) {
            for q in 0..nq {
                let n = port.rx(q as u16, &mut flush_burst);
                if n > 0 {
                    flush_burst.clear();
                }
            }
            std::hint::spin_loop();
        }
        if args.min_ghz > 0.0 {
            let deadline = Instant::now() + Duration::from_secs(6);
            while Instant::now() < deadline {
                let (mg, _, _) = host_pmu.measure_min_ghz(true, 100, tsc_hz);
                if mg >= args.min_ghz {
                    break;
                }
            }
        }
        let _ = tcp_rpc(&args.card_sync, "DRAIN_WAIT timeout_ms=2000", 5)?;

        // ---- Clean Measurement Boundary (switch run_gen to meas_run_gen!) ----
        if kv_handle.is_some() {
            kv_phase.store(KV_PHASE_CALIB, Ordering::Release);
            while !kv_calib.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(1));
            }
        }

        let merc_rst = tcp_rpc(&args.merc_ctrl, &format!("SET_RUN_GEN {meas_run_gen}"), 5)?;
        if !merc_rst.contains("\"ok\":true") {
            return Err(format!("mercury SET_RUN_GEN failed: {merc_rst}").into());
        }
        let _ = tcp_rpc(&args.merc_ctrl, "RESET", 5)?;
        let card_cfg_meas = format!(
            "CONFIG policy={} profile={} fifo_mode={} run_gen={} r_eff={} r_eff_bytes={}",
            arm.card_policy(),
            profile.as_str(),
            args.card_fifo_mode,
            meas_run_gen,
            args.r_eff,
            args.r_eff_bytes,
        );
        let card_m_res = tcp_rpc(&args.card_sync, &card_cfg_meas, 10)?;
        if !card_m_res.contains("\"ok\":true") {
            return Err(format!("card meas CONFIG failed: {card_m_res}").into());
        }
        let _ = port.stats_reset();
        // Drain any lingering stale packets from warmup
        for q in 0..nq {
            while port.rx(q as u16, &mut flush_burst) > 0 {
                flush_burst.clear();
            }
        }

        // ---- Measured Window ----
        let _ = tcp_rpc(&args.card_sync, "PMU_START", 5)?;
        let pmu0 = host_pmu.snapshot();
        let wall_t0 = Instant::now();
        if kv_handle.is_some() {
            kv_phase.store(KV_PHASE_MEASURE, Ordering::Release);
        }

        let mut meas_credit = CreditState::new();
        let mut meas_tracker = StreamTracker::new();
        let mut total_rx = 0u64;
        let mut total_bad = 0u64;
        let mut total_torn = 0u64;
        let mut total_drain_ticks = 0u64;
        let mut per_burst_drain_us: Vec<f64> = Vec::with_capacity(args.bursts);

        for b in 1..=args.bursts {
            let (brx, bbad, btorn, dticks) =
                run_one_burst(b as u16, meas_run_gen, &mut meas_credit, &mut meas_tracker)?;
            total_rx += brx;
            total_bad += bbad;
            total_torn += btorn;
            total_drain_ticks += dticks;
            per_burst_drain_us.push((dticks as f64 / tsc_hz as f64) * 1e6);
        }

        if kv_handle.is_some() {
            kv_phase.store(KV_PHASE_STOP, Ordering::Release);
        }
        let wall_s = wall_t0.elapsed().as_secs_f64();
        let pmu1 = host_pmu.snapshot();
        let card_pmu_json = tcp_rpc(&args.card_sync, "PMU_STOP", 10)?;
        let card_stats_json = tcp_rpc(&args.card_sync, "STATS", 10)?;
        let merc_stats_json = tcp_rpc(&args.merc_ctrl, "STATS", 10)?;
        let sdp_port_st = port.stats().unwrap_or_default();

        merc_trig.quit.store(true, Ordering::Relaxed);
        let _ = trig_handle.join();
        let kv_rep = kv_handle.map(|h| h.join().expect("kv worker panicked"));

        let offered_pkts = burst_pkts * args.bursts as u64;
        let lost_pkts = offered_pkts.saturating_sub(total_rx);
        let drop_pct = if offered_pkts > 0 {
            100.0 * lost_pkts as f64 / offered_pkts as f64
        } else {
            0.0
        };
        let drain_s = (total_drain_ticks as f64 / tsc_hz as f64).max(1e-9);
        let drain_mpps = total_rx as f64 / drain_s / 1e6;
        let drain_l2_gbps = (total_rx as f64 * l2_bytes as f64 * 8.0) / drain_s / 1e9;
        let drain_wire_gbps = (total_rx as f64 * wire_bytes as f64 * 8.0) / drain_s / 1e9;

        per_burst_drain_us.sort_by(|a, b| a.total_cmp(b));
        let drain_p50_us = percentile_f64(&per_burst_drain_us, 0.50);
        let drain_p99_us = percentile_f64(&per_burst_drain_us, 0.99);
        let drain_max_us = per_burst_drain_us.last().copied().unwrap_or(0.0);

        let bar4_lease_ok = bar4_slot.as_ref().map_or(true, |s| s.check().is_ok());
        let host_pmu_json = format_host_pmu(&pmu0, &pmu1, wall_s, tsc_hz);
        let kv_json = kv_rep.map_or_else(|| "null".to_string(), |r| r.to_json());

        println!(
            "MRV213 {{\"arm\":\"{}\",\"profile\":\"{}\",\"l2_bytes\":{},\"wire_bytes\":{},\
             \"burst_bytes\":{},\"burst_pkts\":{},\"bursts\":{},\"sdp_queues\":{},\"rx_desc\":{},\
             \"rx_data_room\":{},\"pool_ops\":\"{}\",\"pool_size\":{},\"r_eff\":{},\"r_eff_bytes\":{},\
             \"credit_batch\":{},\"card_fifo_mode\":\"{}\",\"run_gen\":{},\
             \"offered_pkts\":{},\"rx_pkts\":{},\"lost_pkts\":{},\"drop_pct\":{:.4},\
             \"bad\":{},\"torn\":{},\"gaps\":{},\"ooo\":{},\
             \"bar4_writes\":{},\"bar4_lease_ok\":{},\"wall_s\":{:.6},\
             \"drain_s\":{:.6},\"drain_mpps\":{:.6},\"drain_l2_gbps\":{:.4},\"drain_wire_gbps\":{:.4},\
             \"drain_p50_us\":{:.2},\"drain_p99_us\":{:.2},\"drain_max_us\":{:.2},\
             \"kv_bytes\":{},\"kv_page_kb\":{},\"kv_n0\":{},\"kv_n1\":{},\
             \"sdp_port\":{},\"kv\":{},\"host_pmu\":{},\"card_pmu\":{},\"card_stats\":{},\"mercury\":{}}}",
            arm.as_str(),
            profile.as_str(),
            l2_bytes,
            wire_bytes,
            args.burst_bytes,
            burst_pkts,
            args.bursts,
            nq,
            args.rx_desc,
            data_room,
            pool_ops_name,
            pool_size_total,
            args.r_eff,
            args.r_eff_bytes,
            args.credit_batch,
            args.card_fifo_mode,
            meas_run_gen,
            offered_pkts,
            total_rx,
            lost_pkts,
            drop_pct,
            total_bad,
            total_torn,
            meas_tracker.gaps,
            meas_tracker.out_of_order,
            meas_credit.bar4_writes,
            bar4_lease_ok,
            wall_s,
            drain_s,
            drain_mpps,
            drain_l2_gbps,
            drain_wire_gbps,
            drain_p50_us,
            drain_p99_us,
            drain_max_us,
            args.kv_bytes,
            kv_page_kb,
            kv_n0,
            kv_n1,
            format_eth_stats(&sdp_port_st),
            kv_json,
            host_pmu_json,
            card_pmu_json,
            card_stats_json,
            merc_stats_json,
        );

        Ok(())
    }
}
