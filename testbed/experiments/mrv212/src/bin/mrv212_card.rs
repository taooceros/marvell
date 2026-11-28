//! Card-side server for MRV-212 (E1: NO-SFENCE BAR4 MMIO credit signaling CPU
//! cost, control-loop RTT, 100GbE BDP lower bound, Socket-1 interference
//! boundary, static/dynamic data safety, and native SDP SLIST_DBELL/WMARK
//! backpressure ablation).
//!
//! Runs on CN106 aarch64 card with:
//! - MRV-26 BAR4 slot 15 lease server (`10.10.0.2:7526`)
//! - TCP sync / PMU / run-control server (`10.10.0.2:7527`)
//! - 100% non-multiplexed CN106 TAD (8 counters) + 6-ch DDR5 PMU
//! - Standard `sdp1-1` (`0002:01:00.2`) TX/RX queues (1 or 2 queues)
//! - Optional CN106 `DPI FromHost` (`0000:06:00.1`) for `hostmem-dpi` credit arm

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv212_card runs on the aarch64 CN106 card");
    std::process::exit(2);
}

#[cfg(target_arch = "aarch64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "aarch64")]
mod imp {
    use std::arch::asm;
    use std::collections::VecDeque;
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::size_of;
    use std::net::{TcpListener, TcpStream};
    use std::os::fd::RawFd;
    use std::sync::{Arc, Mutex};
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, Ordering};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::Parser;
    use dpdk::{Burst, Eal, HostDir, HostDma, Mbuf, Mempool, Memzone, PcieFunction, Port};
    use testbed::inline::{NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{Bar4Window, restore_defaults};
    use mrv212::{
        CREDIT_BASE, MAX_CREDIT_CORES, MAX_SINK_BYTES, SINK_BASE, SYNC_ADDR, credit_line_offset,
        sink_cell_sig, write_frame,
    };
    use testbed::{Mac, swap_macs};

    const PORT: u16 = 0;
    const MAX_QUEUES: usize = 2;

    #[inline(always)]
    fn dsb_sy() {
        unsafe { asm!("dsb sy", options(nostack, preserves_flags)) };
    }

    #[inline(always)]
    fn read_cntfrq() -> u64 {
        let v: u64;
        unsafe { asm!("mrs {v}, cntfrq_el0", v = out(reg) v, options(nostack, preserves_flags, nomem)) };
        v
    }

    #[inline(always)]
    fn read_cntvct() -> u64 {
        let v: u64;
        unsafe { asm!("mrs {v}, cntvct_el0", v = out(reg) v, options(nostack, preserves_flags, nomem)) };
        v
    }

    // ---- 100% non-multiplexed CN106 TAD (8 counters) + 6-ch DDR5 PMU ----
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
                self.run as f64 * 100.0 / self.en as f64
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
            let c = Self { fd: fd as RawFd };
            if unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) } != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> Sample {
            let mut buf = [0u64; 3];
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast(), size_of::<[u64; 3]>()) };
            assert_eq!(n, size_of::<[u64; 3]>() as isize);
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

    struct CardPmu {
        tad_req_in: PerfCounter,
        tad_dat_in: PerfCounter,
        tad_alloc: PerfCounter,
        tad_hit: PerfCounter,
        tad_dss_wr: PerfCounter,
        tad_req_occ: PerfCounter,
        tad_ifb_occ: PerfCounter,
        tad_retry: PerfCounter,
        ddr_writes: Vec<PerfCounter>,
        ddr_reads: Vec<PerfCounter>,
    }

    struct CardSnap {
        tad_req_in: Sample,
        tad_dat_in: Sample,
        tad_alloc: Sample,
        tad_hit: Sample,
        tad_dss_wr: Sample,
        tad_req_occ: Sample,
        tad_ifb_occ: Sample,
        tad_retry: Sample,
        ddr_writes: Vec<Sample>,
        ddr_reads: Vec<Sample>,
        wall: Instant,
    }

    fn read_sysfs_u32(path: &str) -> io::Result<u32> {
        fs::read_to_string(path)?.trim().parse().map_err(io::Error::other)
    }

    impl CardPmu {
        fn open() -> io::Result<Self> {
            let tad_type = read_sysfs_u32("/sys/bus/event_source/devices/tad/type")?;
            let mut ddr_writes = Vec::new();
            let mut ddr_reads = Vec::new();
            let mut entries: Vec<String> = fs::read_dir("/sys/bus/event_source/devices")?
                .filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|n| n.starts_with("mrvl_ddr_pmu_"))
                .collect();
            entries.sort();
            for name in entries {
                let t = read_sysfs_u32(&format!("/sys/bus/event_source/devices/{name}/type"))?;
                ddr_writes.push(PerfCounter::open(t, 0x64, 0)?);
                ddr_reads.push(PerfCounter::open(t, 0x65, 0)?);
            }
            Ok(Self {
                tad_req_in: PerfCounter::open(tad_type, 0x01, 0)?,
                tad_dat_in: PerfCounter::open(tad_type, 0x09, 0)?,
                tad_alloc: PerfCounter::open(tad_type, 0x1c, 0)?,
                tad_hit: PerfCounter::open(tad_type, 0x1f, 0)?,
                tad_dss_wr: PerfCounter::open(tad_type, 0x0d, 0)?,
                tad_req_occ: PerfCounter::open(tad_type, 0x24, 0)?,
                tad_ifb_occ: PerfCounter::open(tad_type, 0x23, 0)?,
                tad_retry: PerfCounter::open(tad_type, 0x10, 0)?,
                ddr_writes,
                ddr_reads,
            })
        }

        fn snap(&self) -> CardSnap {
            CardSnap {
                tad_req_in: self.tad_req_in.read(),
                tad_dat_in: self.tad_dat_in.read(),
                tad_alloc: self.tad_alloc.read(),
                tad_hit: self.tad_hit.read(),
                tad_dss_wr: self.tad_dss_wr.read(),
                tad_req_occ: self.tad_req_occ.read(),
                tad_ifb_occ: self.tad_ifb_occ.read(),
                tad_retry: self.tad_retry.read(),
                ddr_writes: self.ddr_writes.iter().map(|c| c.read()).collect(),
                ddr_reads: self.ddr_reads.iter().map(|c| c.read()).collect(),
                wall: Instant::now(),
            }
        }

        fn delta_json(s0: &CardSnap, s1: &CardSnap) -> String {
            let mut min_pct = 100.0f64;
            let mut d = |a: Sample, b: Sample| -> u64 {
                let x = b.delta(a);
                min_pct = min_pct.min(x.pct());
                x.val
            };
            let tad_req_in = d(s0.tad_req_in, s1.tad_req_in);
            let tad_dat_in = d(s0.tad_dat_in, s1.tad_dat_in);
            let tad_alloc = d(s0.tad_alloc, s1.tad_alloc);
            let tad_hit = d(s0.tad_hit, s1.tad_hit);
            let tad_dss_wr = d(s0.tad_dss_wr, s1.tad_dss_wr);
            let tad_req_occ = d(s0.tad_req_occ, s1.tad_req_occ);
            let tad_ifb_occ = d(s0.tad_ifb_occ, s1.tad_ifb_occ);
            let tad_retry = d(s0.tad_retry, s1.tad_retry);
            let mut ddr_wr = 0u64;
            let mut ddr_rd = 0u64;
            for (a, b) in s0.ddr_writes.iter().zip(s1.ddr_writes.iter()) {
                ddr_wr = ddr_wr.saturating_add(d(*a, *b));
            }
            for (a, b) in s0.ddr_reads.iter().zip(s1.ddr_reads.iter()) {
                ddr_rd = ddr_rd.saturating_add(d(*a, *b));
            }
            let wall_ns = s1.wall.saturating_duration_since(s0.wall).as_nanos() as u64;
            format!(
                "{{\"tad_req_in\":{tad_req_in},\"tad_dat_in\":{tad_dat_in},\"tad_alloc\":{tad_alloc},\"tad_hit\":{tad_hit},\"tad_dss_wr\":{tad_dss_wr},\"tad_req_occ\":{tad_req_occ},\"tad_ifb_occ\":{tad_ifb_occ},\"tad_retry\":{tad_retry},\"ddr_writes\":{ddr_wr},\"ddr_reads\":{ddr_rd},\"ddr_channels\":{},\"wall_ns\":{wall_ns},\"min_pct\":{min_pct:.2}}}",
                s0.ddr_writes.len()
            )
        }
    }

    #[derive(Parser)]
    struct Args {
        /// MRV-26 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        /// Sync / PMU / run-control TCP address.
        #[arg(long, default_value = SYNC_ADDR)]
        sync: String,
        /// CN106 DPI VF PCI BDF (e.g. 0000:06:00.1) for hostmem-dpi credit polling.
        #[arg(long)]
        dpi_vf: Option<String>,
        /// Number of sdp1-1 queues (1 or 2).
        #[arg(long, default_value_t = 1)]
        queues: u16,
        /// sdp1-1 TX ring descriptors per queue.
        #[arg(long, default_value_t = 512)]
        tx_desc: u16,
        /// sdp1-1 RX ring descriptors per queue.
        #[arg(long, default_value_t = 512)]
        rx_desc: u16,
        /// Card NPA mbuf pool size per queue (sized to absorb Control D bursts in CN106 L3/DDR5).
        #[arg(long, default_value_t = 131071)]
        pool_size: u32,
        /// Restore firmware BAR4/PEM0 defaults and exit.
        #[arg(long)]
        restore: bool,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum RunMode {
        Idle,
        Stream,
        CreditRtt,
        PingPong,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum CreditMode {
        Off,
        Bar4NoFence,
        Bar4Sfence,
        Bar4Uc64,
        HostMemDpi,
        NativeBp,
    }

    #[derive(Clone, Copy)]
    struct RunConfig {
        mode: RunMode,
        credit: CreditMode,
        host_mac: Mac,
        frame_size: u16,
        run_gen: u64,
        queues: usize,
        r_eff: u64,
        r_eff_bytes: u64,
        target_mpps: f64,
        q1_rate_frac: f64,
        use_fifo: bool,
        fifo_cap: usize,
        host_taken_iova: u64,
    }

    impl Default for RunConfig {
        fn default() -> Self {
            Self {
                mode: RunMode::Idle,
                credit: CreditMode::Off,
                host_mac: Mac([0; 6]),
                frame_size: 64,
                run_gen: 1,
                queues: 1,
                r_eff: 256,
                r_eff_bytes: 0,
                target_mpps: 0.0,
                q1_rate_frac: 1.0,
                use_fifo: false,
                fifo_cap: 131072,
                host_taken_iova: 0,
            }
        }
    }

    #[derive(Clone, Default)]
    struct RunStats {
        q_sent: [u64; MAX_QUEUES],
        q_tx_retries: [u64; MAX_QUEUES],
        q_fifo_enq: [u64; MAX_QUEUES],
        q_fifo_peak: [u64; MAX_QUEUES],
        q_fifo_full: [u64; MAX_QUEUES],
        q_credit_stalls: [u64; MAX_QUEUES],
        q_credit_updates: [u64; MAX_QUEUES],
        q_credit_reread: [u64; MAX_QUEUES],
        q_credit_torn: [u64; MAX_QUEUES],
        q_alloc_fail: [u64; MAX_QUEUES],
        rx_pkts: u64,
        dpi_polls: u64,
        dpi_failed: u64,
        wall_ns: u64,
        eth_opackets: u64,
        eth_oerrors: u64,
        eth_ipackets: u64,
        eth_imissed: u64,
    }

    impl RunStats {
        fn to_json(&self) -> String {
            let total_sent = self.q_sent[0] + self.q_sent[1];
            let total_retries = self.q_tx_retries[0] + self.q_tx_retries[1];
            let total_credit_stalls = self.q_credit_stalls[0] + self.q_credit_stalls[1];
            let total_credit_updates = self.q_credit_updates[0] + self.q_credit_updates[1];
            let total_credit_reread = self.q_credit_reread[0] + self.q_credit_reread[1];
            let total_credit_torn = self.q_credit_torn[0] + self.q_credit_torn[1];
            let total_alloc_fail = self.q_alloc_fail[0] + self.q_alloc_fail[1];
            format!(
                "{{\"tx_sent\":{total_sent},\"q0_sent\":{},\"q1_sent\":{},\"tx_retries\":{total_retries},\"q0_tx_retries\":{},\"q1_tx_retries\":{},\"q0_fifo_enq\":{},\"q1_fifo_enq\":{},\"q0_fifo_peak\":{},\"q1_fifo_peak\":{},\"q0_fifo_full\":{},\"q1_fifo_full\":{},\"credit_stalls\":{total_credit_stalls},\"q0_credit_stalls\":{},\"q1_credit_stalls\":{},\"credit_updates\":{total_credit_updates},\"credit_reread\":{total_credit_reread},\"credit_torn\":{total_credit_torn},\"alloc_fail\":{total_alloc_fail},\"rx_pkts\":{},\"dpi_polls\":{},\"dpi_failed\":{},\"wall_ns\":{},\"eth_opackets\":{},\"eth_oerrors\":{},\"eth_ipackets\":{},\"eth_imissed\":{}}}",
                self.q_sent[0],
                self.q_sent[1],
                self.q_tx_retries[0],
                self.q_tx_retries[1],
                self.q_fifo_enq[0],
                self.q_fifo_enq[1],
                self.q_fifo_peak[0],
                self.q_fifo_peak[1],
                self.q_fifo_full[0],
                self.q_fifo_full[1],
                self.q_credit_stalls[0],
                self.q_credit_stalls[1],
                self.rx_pkts,
                self.dpi_polls,
                self.dpi_failed,
                self.wall_ns,
                self.eth_opackets,
                self.eth_oerrors,
                self.eth_ipackets,
                self.eth_imissed,
            )
        }
    }

    struct WorkerCtrl {
        cmd_state: AtomicU8, // 0=idle, 1=start_requested, 2=running, 3=stop_requested
        init_state: AtomicU8, // 0=starting, 1=ready, 2=failed
        init_err: Mutex<Option<String>>,
        card_mac: Mutex<Mac>,
        dpi_ready: AtomicBool,
        reset_window: AtomicBool,
        cfg: Mutex<RunConfig>,
        stats: Mutex<RunStats>,
    }
    impl WorkerCtrl {
        fn new() -> Self {
            Self {
                cmd_state: AtomicU8::new(0),
                init_state: AtomicU8::new(0),
                init_err: Mutex::new(None),
                card_mac: Mutex::new(Mac([0; 6])),
                dpi_ready: AtomicBool::new(false),
                reset_window: AtomicBool::new(false),
                cfg: Mutex::new(RunConfig::default()),
                stats: Mutex::new(RunStats::default()),
            }
        }
    }

    #[inline(always)]
    fn poll_bar4_credit(
        bar4_ptr: *const u8,
        q: usize,
        credit: CreditMode,
        run_gen: u64,
        taken_cached: &mut u64,
        bytes_cached: &mut u64,
        updates: &mut u64,
        reread: &mut u64,
        torn: &mut u64,
    ) {
        let p = unsafe { bar4_ptr.add(credit_line_offset(q)).cast::<u64>() };
        match credit {
            CreditMode::Bar4NoFence | CreditMode::Bar4Sfence => {
                let w0 = unsafe { p.add(0).read_volatile() };
                let w1 = unsafe { p.add(1).read_volatile() };
                let w2 = unsafe { p.add(2).read_volatile() };
                let w7 = unsafe { p.add(7).read_volatile() };
                if w1 == run_gen {
                    if w0 == w7 {
                        if w0 > *taken_cached {
                            *taken_cached = w0;
                            *bytes_cached = (*bytes_cached).max(w2);
                            *updates += 1;
                        }
                    } else {
                        // Distinguish benign concurrent atomic 64 B overwrite between two 8 B LDRs
                        // from a genuinely torn line by immediately re-reading once.
                        let r0 = unsafe { p.add(0).read_volatile() };
                        let r1 = unsafe { p.add(1).read_volatile() };
                        let r2 = unsafe { p.add(2).read_volatile() };
                        let r7 = unsafe { p.add(7).read_volatile() };
                        if r1 == run_gen && r0 == r7 {
                            *reread += 1;
                            if r0 > *taken_cached {
                                *taken_cached = r0;
                                *bytes_cached = (*bytes_cached).max(r2);
                                *updates += 1;
                            }
                        } else {
                            *torn += 1;
                        }
                    }
                }
            }
            CreditMode::Bar4Uc64 => {
                let w0 = unsafe { p.add(0).read_volatile() };
                if w0 > *taken_cached {
                    *taken_cached = w0;
                    *updates += 1;
                }
            }
            _ => {}
        }
    }

    #[inline(always)]
    fn poll_hostmem_dpi(
        dpi_opt: &mut Option<(Memzone<'_>, HostDma<'_>)>,
        host_taken_iova: u64,
        run_gen: u64,
        dpi_inflight: &mut usize,
        taken_cached: &mut u64,
        bytes_cached: &mut u64,
        updates: &mut u64,
        dpi_polls: &mut u64,
        dpi_failed: &mut u64,
    ) {
        if host_taken_iova == 0 {
            return;
        }
        if let Some((mirror, dma)) = dpi_opt {
            if *dpi_inflight > 0 {
                let (done, fail) = dma.completed(8);
                if fail {
                    *dpi_failed += 1;
                }
                if done > 0 {
                    *dpi_inflight = dpi_inflight.saturating_sub(done as usize);
                    *dpi_polls += done as u64;
                    let mp = mirror.as_ptr().cast::<u64>();
                    let w0 = unsafe { mp.add(0).read_volatile() };
                    let w1 = unsafe { mp.add(1).read_volatile() };
                    let w2 = unsafe { mp.add(2).read_volatile() };
                    let w7 = unsafe { mp.add(7).read_volatile() };
                    if w1 == run_gen && w0 == w7 && w0 > *taken_cached {
                        *taken_cached = w0;
                        *bytes_cached = (*bytes_cached).max(w2);
                        *updates += 1;
                    }
                }
            }
            if *dpi_inflight == 0 {
                if let Ok(true) = dma.copy(host_taken_iova, mirror.iova(), 64) {
                    if dma.submit().is_ok() {
                        *dpi_inflight = 1;
                    }
                }
            }
        }
    }

    fn data_worker(
        eal: &Eal,
        bar4_ptr_usize: usize,
        queues: u16,
        tx_desc: u16,
        rx_desc: u16,
        pool_size: u32,
        dpi_vf: Option<&str>,
        ctrl: &WorkerCtrl,
        quit: &AtomicBool,
    ) -> Result<(), String> {
        let bar4_ptr = bar4_ptr_usize as *const u8;
        let init_res = (|| -> Result<(Vec<Mempool<'_>>, Port<'_>, Option<(Memzone<'_>, HostDma<'_>)>), String> {
            let sock = Port::socket_of(eal, PORT);
            let mut pools_owned: Vec<Mempool<'_>> = Vec::with_capacity(queues as usize);
            for q in 0..queues {
                let p = Mempool::new_with_cache(
                    eal,
                    &format!("mrv212_card_pool_q{q}"),
                    pool_size,
                    256,
                    Mempool::default_data_room(),
                    sock,
                )
                .map_err(|e| format!("card mempool q{q}({pool_size}): {e}"))?;
                pools_owned.push(p);
            }
            let port = {
                let pool_refs: Vec<&Mempool<'_>> = pools_owned.iter().collect();
                Port::start_per_queue(eal, PORT, &pool_refs, rx_desc, tx_desc)
                    .map_err(|e| format!("sdp1-1 Port::start_per_queue(q={queues},rx={rx_desc},tx={tx_desc}): {e}"))?
            };
            let _ = port.promiscuous();
            let dpi = if let Some(vf) = dpi_vf {
                let mirror = Memzone::reserve_contiguous(eal, "mrv212_dpi_credit", 4096, 0, 4096)
                    .map_err(|e| format!("dpi mirror memzone: {e}"))?;
                let dma = HostDma::start(
                    eal,
                    vf,
                    4096,
                    PcieFunction { core: 0, pf: 0, vf: 1 },
                    HostDir::FromHost,
                )
                .map_err(|e| format!("HostDma::start({vf}): {e}"))?;
                Some((mirror, dma))
            } else {
                None
            };
            Ok((pools_owned, port, dpi))
        })();

        let (pools_owned, port, mut dpi_opt) = match init_res {
            Ok(v) => {
                *ctrl.card_mac.lock().unwrap() = Mac(v.1.mac());
                ctrl.dpi_ready.store(v.2.is_some(), Ordering::Release);
                ctrl.init_state.store(1, Ordering::Release);
                v
            }
            Err(msg) => {
                eprintln!("mrv212_card worker init failed: {msg}");
                *ctrl.init_err.lock().unwrap() = Some(msg.clone());
                ctrl.init_state.store(2, Ordering::Release);
                return Err(msg);
            }
        };

        let card_mac = Mac(port.mac());
        let cntfrq = read_cntfrq() as f64;

        while !quit.load(Ordering::Acquire) {
            if ctrl.cmd_state.load(Ordering::Acquire) != 1 {
                thread::sleep(Duration::from_millis(1));
                continue;
            }
            let cfg = *ctrl.cfg.lock().unwrap();
            let active_q = cfg.queues.min(queues as usize).min(MAX_QUEUES).max(1);
            let _ = port.reset_stats();
            let eth0 = port.stats().unwrap_or_default();

            let mut drain_b: Burst<'_, 32> = Burst::new();
            for q in 0..queues {
                while port.rx(q, &mut drain_b) > 0 {
                    drain_b.clear();
                }
            }

            let mut st = RunStats::default();
            let mut taken_cached = [0u64; MAX_QUEUES];
            let mut bytes_cached = [0u64; MAX_QUEUES];
            let mut seq_gen = [1u64; MAX_QUEUES];
            let mut submitted = [0u64; MAX_QUEUES];
            let mut bytes_submitted = [0u64; MAX_QUEUES];
            let mut tx_bursts: [Burst<'_, 32>; MAX_QUEUES] = [Burst::new(), Burst::new()];
            let mut fifos: [VecDeque<Mbuf<'_>>; MAX_QUEUES] = [
                VecDeque::with_capacity(if cfg.use_fifo { cfg.fifo_cap.min(262144) } else { 0 }),
                VecDeque::with_capacity(if cfg.use_fifo { cfg.fifo_cap.min(262144) } else { 0 }),
            ];
            if let Some((mirror, _)) = &dpi_opt {
                unsafe { std::ptr::write_bytes(mirror.as_ptr(), 0, 64) };
                dsb_sy();
            }
            let mut dpi_inflight: usize = 0;

            let start_wall = Instant::now();
            let start_cnt = read_cntvct();
            let per_q_ticks: [f64; MAX_QUEUES] = if cfg.target_mpps > 0.0 {
                let q0_mpps = if active_q == 1 {
                    cfg.target_mpps
                } else {
                    cfg.target_mpps / (1.0 + cfg.q1_rate_frac.max(0.001))
                };
                let q1_mpps = q0_mpps * cfg.q1_rate_frac.max(0.001);
                [cntfrq / (q0_mpps * 1e6), cntfrq / (q1_mpps * 1e6)]
            } else {
                [0.0, 0.0]
            };
            let mut next_gen_cnt: [f64; MAX_QUEUES];
            let _ = start_cnt;
            ctrl.reset_window.store(false, Ordering::Release);
            ctrl.cmd_state.store(2, Ordering::Release);

            match cfg.mode {
                RunMode::Idle => {}
                RunMode::Stream => {
                    // Wait for host RX loop GO signal in BAR4 slot 15 (`credit_line_offset(15)`)
                    // so `off` and `native-bp` experience zero startup drops before host enters RX loop.
                    let go_ptr = unsafe { bar4_ptr.add(credit_line_offset(15)).cast::<u64>() };
                    while ctrl.cmd_state.load(Ordering::Acquire) == 2 && !quit.load(Ordering::Acquire) {
                        if unsafe { go_ptr.read_volatile() } == cfg.run_gen {
                            break;
                        }
                        std::hint::spin_loop();
                    }
                    let go_cnt = read_cntvct();
                    next_gen_cnt = [go_cnt as f64, go_cnt as f64];

                    while ctrl.cmd_state.load(Ordering::Acquire) == 2 && !quit.load(Ordering::Acquire) {
                        if ctrl.reset_window.swap(false, Ordering::AcqRel) {
                            for q in 0..MAX_QUEUES {
                                st.q_fifo_peak[q] = fifos[q].len() as u64;
                                st.q_tx_retries[q] = 0;
                                st.q_credit_stalls[q] = 0;
                                st.q_credit_reread[q] = 0;
                                st.q_credit_torn[q] = 0;
                            }
                        }
                        if cfg.credit == CreditMode::HostMemDpi {
                            poll_hostmem_dpi(
                                &mut dpi_opt,
                                cfg.host_taken_iova,
                                cfg.run_gen,
                                &mut dpi_inflight,
                                &mut taken_cached[0],
                                &mut bytes_cached[0],
                                &mut st.q_credit_updates[0],
                                &mut st.dpi_polls,
                                &mut st.dpi_failed,
                            );
                        }

                        for q in 0..active_q {
                            if matches!(
                                cfg.credit,
                                CreditMode::Bar4NoFence | CreditMode::Bar4Sfence | CreditMode::Bar4Uc64
                            ) {
                                poll_bar4_credit(
                                    bar4_ptr,
                                    q,
                                    cfg.credit,
                                    cfg.run_gen,
                                    &mut taken_cached[q],
                                    &mut bytes_cached[q],
                                    &mut st.q_credit_updates[q],
                                    &mut st.q_credit_reread[q],
                                    &mut st.q_credit_torn[q],
                                );
                            }

                            let gated = matches!(
                                cfg.credit,
                                CreditMode::Bar4NoFence
                                    | CreditMode::Bar4Sfence
                                    | CreditMode::Bar4Uc64
                                    | CreditMode::HostMemDpi
                            );

                            if cfg.use_fifo {
                                let mut want_gen: usize = 32;
                                if per_q_ticks[q] > 0.0 {
                                    let now = read_cntvct() as f64;
                                    if now < next_gen_cnt[q] {
                                        want_gen = 0;
                                    } else {
                                        let due = ((now - next_gen_cnt[q]) / per_q_ticks[q]) as usize + 1;
                                        want_gen = due.min(32);
                                        next_gen_cnt[q] += (want_gen as f64) * per_q_ticks[q];
                                        if now - next_gen_cnt[q] > per_q_ticks[q] * 64.0 {
                                            next_gen_cnt[q] = now;
                                        }
                                    }
                                }
                                let room = cfg.fifo_cap.saturating_sub(fifos[q].len());
                                if want_gen > room {
                                    st.q_fifo_full[q] += (want_gen - room) as u64;
                                    want_gen = room;
                                }
                                if want_gen > 0 {
                                    let now_ts = read_cntvct();
                                    for _ in 0..want_gen {
                                        if let Some(mut m) = pools_owned[q].alloc() {
                                            if let Some(buf) = m.append(cfg.frame_size) {
                                                write_frame(
                                                    buf,
                                                    cfg.host_mac,
                                                    card_mac,
                                                    q as u16,
                                                    seq_gen[q],
                                                    cfg.run_gen,
                                                    now_ts,
                                                );
                                                seq_gen[q] += 1;
                                                fifos[q].push_back(m);
                                                st.q_fifo_enq[q] += 1;
                                            }
                                        } else {
                                            st.q_alloc_fail[q] += 1;
                                            break;
                                        }
                                    }
                                    if (fifos[q].len() as u64) > st.q_fifo_peak[q] {
                                        st.q_fifo_peak[q] = fifos[q].len() as u64;
                                    }
                                }

                                let avail_credit: usize = if gated {
                                    let c = if cfg.r_eff_bytes > 0 {
                                        bytes_cached[q]
                                            .saturating_add(cfg.r_eff_bytes)
                                            .saturating_sub(bytes_submitted[q])
                                            / (cfg.frame_size as u64)
                                    } else {
                                        taken_cached[q]
                                            .saturating_add(cfg.r_eff)
                                            .saturating_sub(submitted[q])
                                    };
                                    c.min(32) as usize
                                } else {
                                    32
                                };

                                if gated && avail_credit == 0 && tx_bursts[q].is_empty() && !fifos[q].is_empty() {
                                    st.q_credit_stalls[q] += 1;
                                } else {
                                    while tx_bursts[q].len() < 32
                                        && (!gated || tx_bursts[q].len() < avail_credit)
                                    {
                                        if let Some(m) = fifos[q].pop_front() {
                                            let _ = tx_bursts[q].push(m);
                                        } else {
                                            break;
                                        }
                                    }
                                    if !tx_bursts[q].is_empty() {
                                        let before = tx_bursts[q].len();
                                        let sent = port.tx(q as u16, &mut tx_bursts[q]);
                                        st.q_sent[q] += sent as u64;
                                        submitted[q] += sent as u64;
                                        bytes_submitted[q] += (sent as u64) * (cfg.frame_size as u64);
                                        if sent < before {
                                            st.q_tx_retries[q] += 1;
                                        }
                                    }
                                }
                            } else {
                                if !tx_bursts[q].is_empty() {
                                    let before = tx_bursts[q].len();
                                    let sent = port.tx(q as u16, &mut tx_bursts[q]);
                                    st.q_sent[q] += sent as u64;
                                    submitted[q] += sent as u64;
                                    bytes_submitted[q] += (sent as u64) * (cfg.frame_size as u64);
                                    if sent < before {
                                        st.q_tx_retries[q] += 1;
                                        continue;
                                    }
                                }
                                let mut can_send: usize = 32;
                                if gated {
                                    let c = if cfg.r_eff_bytes > 0 {
                                        bytes_cached[q]
                                            .saturating_add(cfg.r_eff_bytes)
                                            .saturating_sub(bytes_submitted[q])
                                            / (cfg.frame_size as u64)
                                    } else {
                                        taken_cached[q]
                                            .saturating_add(cfg.r_eff)
                                            .saturating_sub(submitted[q])
                                    };
                                    can_send = (c as usize).min(32);
                                    if can_send == 0 {
                                        st.q_credit_stalls[q] += 1;
                                        continue;
                                    }
                                }
                                if per_q_ticks[q] > 0.0 {
                                    let now = read_cntvct() as f64;
                                    if now < next_gen_cnt[q] {
                                        continue;
                                    }
                                    let due = ((now - next_gen_cnt[q]) / per_q_ticks[q]) as usize + 1;
                                    let n = due.min(can_send);
                                    can_send = n;
                                    next_gen_cnt[q] += (n as f64) * per_q_ticks[q];
                                    if now - next_gen_cnt[q] > per_q_ticks[q] * 64.0 {
                                        next_gen_cnt[q] = now;
                                    }
                                }
                                let now_ts = read_cntvct();
                                for _ in 0..can_send {
                                    if let Some(mut m) = pools_owned[q].alloc() {
                                        if let Some(buf) = m.append(cfg.frame_size) {
                                            write_frame(
                                                buf,
                                                cfg.host_mac,
                                                card_mac,
                                                q as u16,
                                                seq_gen[q],
                                                cfg.run_gen,
                                                now_ts,
                                            );
                                            seq_gen[q] += 1;
                                            let _ = tx_bursts[q].push(m);
                                        }
                                    } else {
                                        st.q_alloc_fail[q] += 1;
                                        break;
                                    }
                                }
                                if !tx_bursts[q].is_empty() {
                                    let before = tx_bursts[q].len();
                                    let sent = port.tx(q as u16, &mut tx_bursts[q]);
                                    st.q_sent[q] += sent as u64;
                                    submitted[q] += sent as u64;
                                    bytes_submitted[q] += (sent as u64) * (cfg.frame_size as u64);
                                    if sent < before {
                                        st.q_tx_retries[q] += 1;
                                    }
                                }
                            }
                        }
                    }
                }
                RunMode::CreditRtt => {
                    while ctrl.cmd_state.load(Ordering::Acquire) == 2 && !quit.load(Ordering::Acquire) {
                        if cfg.credit == CreditMode::HostMemDpi {
                            poll_hostmem_dpi(
                                &mut dpi_opt,
                                cfg.host_taken_iova,
                                cfg.run_gen,
                                &mut dpi_inflight,
                                &mut taken_cached[0],
                                &mut bytes_cached[0],
                                &mut st.q_credit_updates[0],
                                &mut st.dpi_polls,
                                &mut st.dpi_failed,
                            );
                        } else {
                            poll_bar4_credit(
                                bar4_ptr,
                                0,
                                cfg.credit,
                                cfg.run_gen,
                                &mut taken_cached[0],
                                &mut bytes_cached[0],
                                &mut st.q_credit_updates[0],
                                &mut st.q_credit_reread[0],
                                &mut st.q_credit_torn[0],
                            );
                        }
                        while submitted[0] < taken_cached[0] {
                            let next_seq = submitted[0] + 1;
                            if let Some(mut m) = pools_owned[0].alloc() {
                                if let Some(buf) = m.append(cfg.frame_size) {
                                    write_frame(
                                        buf,
                                        cfg.host_mac,
                                        card_mac,
                                        0,
                                        next_seq,
                                        cfg.run_gen,
                                        read_cntvct(),
                                    );
                                    let _ = tx_bursts[0].push(m);
                                    while !tx_bursts[0].is_empty()
                                        && ctrl.cmd_state.load(Ordering::Acquire) == 2
                                    {
                                        let sent = port.tx(0, &mut tx_bursts[0]);
                                        if sent > 0 {
                                            st.q_sent[0] += sent as u64;
                                            submitted[0] += sent as u64;
                                        } else {
                                            st.q_tx_retries[0] += 1;
                                        }
                                    }
                                }
                            } else {
                                st.q_alloc_fail[0] += 1;
                                break;
                            }
                        }
                    }
                }
                RunMode::PingPong => {
                    let mut rx_b: Burst<'_, 32> = Burst::new();
                    while ctrl.cmd_state.load(Ordering::Acquire) == 2 && !quit.load(Ordering::Acquire) {
                        if cfg.credit == CreditMode::HostMemDpi {
                            poll_hostmem_dpi(
                                &mut dpi_opt,
                                cfg.host_taken_iova,
                                cfg.run_gen,
                                &mut dpi_inflight,
                                &mut taken_cached[0],
                                &mut bytes_cached[0],
                                &mut st.q_credit_updates[0],
                                &mut st.dpi_polls,
                                &mut st.dpi_failed,
                            );
                        }

                        if port.rx(0, &mut rx_b) == 0 {
                            continue;
                        }
                        st.rx_pkts += rx_b.len() as u64;
                        if matches!(
                            cfg.credit,
                            CreditMode::Bar4NoFence | CreditMode::Bar4Sfence | CreditMode::Bar4Uc64
                        ) {
                            poll_bar4_credit(
                                bar4_ptr,
                                0,
                                cfg.credit,
                                cfg.run_gen,
                                &mut taken_cached[0],
                                &mut bytes_cached[0],
                                &mut st.q_credit_updates[0],
                                &mut st.q_credit_reread[0],
                                &mut st.q_credit_torn[0],
                            );
                            while submitted[0] >= taken_cached[0].saturating_add(cfg.r_eff)
                                && ctrl.cmd_state.load(Ordering::Acquire) == 2
                            {
                                st.q_credit_stalls[0] += 1;
                                poll_bar4_credit(
                                    bar4_ptr,
                                    0,
                                    cfg.credit,
                                    cfg.run_gen,
                                    &mut taken_cached[0],
                                    &mut bytes_cached[0],
                                    &mut st.q_credit_updates[0],
                                    &mut st.q_credit_reread[0],
                                    &mut st.q_credit_torn[0],
                                );
                            }
                        }
                        for pkt in rx_b.as_mut_slice() {
                            swap_macs(pkt.data_mut());
                        }
                        while !rx_b.is_empty() && ctrl.cmd_state.load(Ordering::Acquire) == 2 {
                            let sent = port.tx(0, &mut rx_b);
                            if sent > 0 {
                                st.q_sent[0] += sent as u64;
                                submitted[0] += sent as u64;
                            } else {
                                st.q_tx_retries[0] += 1;
                            }
                        }
                        rx_b.clear();
                    }
                }
            }

            if let Some((_, dma)) = &mut dpi_opt {
                let d0 = Instant::now();
                while dpi_inflight > 0 && d0.elapsed() < Duration::from_secs(3) {
                    let (done, fail) = dma.completed(16);
                    if fail {
                        st.dpi_failed += 1;
                    }
                    dpi_inflight = dpi_inflight.saturating_sub(done as usize);
                }
            }
            for q in 0..MAX_QUEUES {
                tx_bursts[q].clear();
                fifos[q].clear();
            }

            st.wall_ns = start_wall.elapsed().as_nanos() as u64;
            let eth1 = port.stats().unwrap_or_default();
            st.eth_opackets = eth1.opackets.saturating_sub(eth0.opackets);
            st.eth_oerrors = eth1.oerrors.saturating_sub(eth0.oerrors);
            st.eth_ipackets = eth1.ipackets.saturating_sub(eth0.ipackets);
            st.eth_imissed = eth1.imissed.saturating_sub(eth0.imissed);

            *ctrl.stats.lock().unwrap() = st;
            ctrl.cmd_state.store(0, Ordering::Release);
        }
        Ok(())
    }

    fn parse_run_start(tokens: &[&str]) -> Result<RunConfig, String> {
        let mut cfg = RunConfig::default();
        for tok in tokens {
            let Some((k, v)) = tok.split_once('=') else {
                return Err(format!("bad kv token: {tok}"));
            };
            match k {
                "mode" => {
                    cfg.mode = match v {
                        "stream" => RunMode::Stream,
                        "credit-rtt" => RunMode::CreditRtt,
                        "pingpong" => RunMode::PingPong,
                        _ => return Err(format!("unknown mode {v}")),
                    };
                }
                "credit" => {
                    cfg.credit = match v {
                        "off" => CreditMode::Off,
                        "bar4-nofence" => CreditMode::Bar4NoFence,
                        "bar4-sfence" => CreditMode::Bar4Sfence,
                        "bar4-uc64" => CreditMode::Bar4Uc64,
                        "hostmem-dpi" => CreditMode::HostMemDpi,
                        "native-bp" => CreditMode::NativeBp,
                        _ => return Err(format!("unknown credit {v}")),
                    };
                }
                "host_mac" => {
                    cfg.host_mac = v.parse().map_err(|e| format!("bad host_mac {v}: {e}"))?;
                }
                "frame_size" => cfg.frame_size = v.parse().map_err(|e| format!("frame_size: {e}"))?,
                "run_gen" => cfg.run_gen = v.parse().map_err(|e| format!("run_gen: {e}"))?,
                "queues" => cfg.queues = v.parse().map_err(|e| format!("queues: {e}"))?,
                "r_eff" => cfg.r_eff = v.parse().map_err(|e| format!("r_eff: {e}"))?,
                "r_eff_bytes" => cfg.r_eff_bytes = v.parse().map_err(|e| format!("r_eff_bytes: {e}"))?,
                "target_mpps" => cfg.target_mpps = v.parse().map_err(|e| format!("target_mpps: {e}"))?,
                "q1_rate_frac" => cfg.q1_rate_frac = v.parse().map_err(|e| format!("q1_rate_frac: {e}"))?,
                "use_fifo" => cfg.use_fifo = v == "1" || v == "true",
                "fifo_cap" => cfg.fifo_cap = v.parse().map_err(|e| format!("fifo_cap: {e}"))?,
                "host_taken_iova" => {
                    cfg.host_taken_iova = if let Some(h) = v.strip_prefix("0x") {
                        u64::from_str_radix(h, 16).map_err(|e| format!("host_taken_iova: {e}"))?
                    } else {
                        v.parse().map_err(|e| format!("host_taken_iova: {e}"))?
                    };
                }
                _ => return Err(format!("unknown key {k}")),
            }
        }
        Ok(cfg)
    }

    fn handle_client(
        stream: TcpStream,
        bar4_ptr: *mut u8,
        pmu: &CardPmu,
        ctrl: &WorkerCtrl,
        queues: u16,
        tx_desc: u16,
        cntfrq: u64,
        quit: &AtomicBool,
    ) {
        let _ = stream.set_nodelay(true);
        let Ok(reader_stream) = stream.try_clone() else { return };
        let mut reader = BufReader::new(reader_stream);
        let mut writer = stream;
        let mut snap0: Option<CardSnap> = None;
        let mut line = String::new();

        while !quit.load(Ordering::Acquire) {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {}
                Err(_) => break,
            }
            let cmd = line.trim();
            if cmd.is_empty() {
                continue;
            }
            let parts: Vec<&str> = cmd.split_whitespace().collect();
            let resp = match parts[0] {
                "PING" => {
                    let mac = *ctrl.card_mac.lock().unwrap();
                    let dpi = ctrl.dpi_ready.load(Ordering::Acquire);
                    format!(
                        "{{\"ok\":true,\"mac\":\"{mac}\",\"queues\":{queues},\"tx_desc\":{tx_desc},\"cntfrq\":{cntfrq},\"dpi_ready\":{dpi}}}"
                    )
                }
                "CLEAR_BAR4" => {
                    let n_sink: usize = parts.get(1).and_then(|s| s.parse().ok()).unwrap_or(0);
                    let max_sink_lines = MAX_SINK_BYTES / 64;
                    if n_sink > max_sink_lines {
                        "{\"ok\":false,\"err\":\"sink_lines_exceeds_3mib\"}".to_string()
                    } else {
                        unsafe {
                            std::ptr::write_bytes(bar4_ptr.add(CREDIT_BASE), 0, MAX_CREDIT_CORES * 64);
                            if n_sink > 0 {
                                std::ptr::write_bytes(bar4_ptr.add(SINK_BASE), 0, n_sink * 64);
                            }
                        }
                        dsb_sy();
                        format!("{{\"ok\":true,\"cleared_credit\":{MAX_CREDIT_CORES},\"cleared_sink\":{n_sink}}}")
                    }
                }
                "PMU_START" => {
                    ctrl.reset_window.store(true, Ordering::Release);
                    snap0 = Some(pmu.snap());
                    "{\"ok\":true}".to_string()
                }
                "PMU_STOP" => {
                    let s1 = pmu.snap();
                    if let Some(s0) = snap0.take() {
                        format!("{{\"ok\":true,\"pmu\":{}}}", CardPmu::delta_json(&s0, &s1))
                    } else {
                        "{\"ok\":false,\"err\":\"no_pmu_start\"}".to_string()
                    }
                }
                "VERIFY_BAR4" => {
                    if parts.len() < 4 {
                        "{\"ok\":false,\"err\":\"bad_args\"}".to_string()
                    } else {
                        let run_tag: u64 = parts[1].parse().unwrap_or(0);
                        let n_threads: usize = parts[2].parse().unwrap_or(0);
                        let lpt: usize = parts[3].parse().unwrap_or(0);
                        let total_lines = n_threads.saturating_mul(lpt);
                        if total_lines > MAX_SINK_BYTES / 64 {
                            "{\"ok\":false,\"err\":\"sink_oob\"}".to_string()
                        } else {
                            dsb_sy();
                            let mut checked = 0u64;
                            let mut zero = 0u64;
                            let mut bad = 0u64;
                            let mut torn = 0u64;
                            for t in 0..n_threads {
                                let written: u64 =
                                    parts.get(4 + t).and_then(|s| s.parse().ok()).unwrap_or(0);
                                let expect_lines = (written as usize).min(lpt);
                                let base_line = t * lpt;
                                for l in 0..expect_lines {
                                    let g_line = base_line + l;
                                    let p = unsafe { bar4_ptr.add(SINK_BASE + g_line * 64).cast::<u64>() };
                                    let w0 = unsafe { p.add(0).read_volatile() };
                                    let w1 = unsafe { p.add(1).read_volatile() };
                                    let w2 = unsafe { p.add(2).read_volatile() };
                                    let w7 = unsafe { p.add(7).read_volatile() };
                                    checked += 1;
                                    if w0 == 0 && w7 == 0 {
                                        zero += 1;
                                    } else if w0 != w7 {
                                        torn += 1;
                                    } else if w2 != run_tag || w1 != sink_cell_sig(w0, g_line, run_tag) {
                                        bad += 1;
                                    }
                                }
                            }
                            format!("{{\"ok\":true,\"checked\":{checked},\"zero\":{zero},\"bad\":{bad},\"torn\":{torn}}}")
                        }
                    }
                }
                "RUN_START" => {
                    if ctrl.cmd_state.load(Ordering::Acquire) != 0 {
                        "{\"ok\":false,\"err\":\"worker_busy\"}".to_string()
                    } else {
                        match parse_run_start(&parts[1..]) {
                            Ok(cfg) => {
                                *ctrl.cfg.lock().unwrap() = cfg;
                                ctrl.cmd_state.store(1, Ordering::Release);
                                let t0 = Instant::now();
                                while ctrl.cmd_state.load(Ordering::Acquire) != 2
                                    && t0.elapsed() < Duration::from_secs(3)
                                {
                                    thread::sleep(Duration::from_millis(1));
                                }
                                if ctrl.cmd_state.load(Ordering::Acquire) == 2 {
                                    "{\"ok\":true}".to_string()
                                } else {
                                    "{\"ok\":false,\"err\":\"worker_start_timeout\"}".to_string()
                                }
                            }
                            Err(e) => format!("{{\"ok\":false,\"err\":\"{e}\"}}"),
                        }
                    }
                }
                "RUN_STOP" => {
                    if ctrl.cmd_state.load(Ordering::Acquire) == 2 {
                        ctrl.cmd_state.store(3, Ordering::Release);
                    }
                    let t0 = Instant::now();
                    while ctrl.cmd_state.load(Ordering::Acquire) != 0
                        && t0.elapsed() < Duration::from_secs(5)
                    {
                        thread::sleep(Duration::from_millis(1));
                    }
                    if ctrl.cmd_state.load(Ordering::Acquire) == 0 {
                        let st = ctrl.stats.lock().unwrap().to_json();
                        format!("{{\"ok\":true,\"stats\":{st}}}")
                    } else {
                        "{\"ok\":false,\"err\":\"worker_stop_timeout\"}".to_string()
                    }
                }
                _ => "{\"ok\":false,\"err\":\"unknown_cmd\"}".to_string(),
            };
            if writeln!(writer, "{resp}").is_err() || writer.flush().is_err() {
                break;
            }
        }
    }

    fn other_reflectors() -> Vec<u32> {
        let me = std::process::id();
        let mut pids = Vec::new();
        let Ok(dir) = fs::read_dir("/proc") else { return pids };
        for ent in dir.flatten() {
            let Ok(pid) = ent.file_name().to_string_lossy().parse::<u32>() else { continue };
            if pid == me {
                continue;
            }
            let Ok(cmd) = fs::read(ent.path().join("cmdline")) else { continue };
            let argv0 = cmd.split(|&b| b == 0).next().unwrap_or(&[]);
            let name = String::from_utf8_lossy(argv0);
            let base = name.rsplit('/').next().unwrap_or("");
            if matches!(
                base,
                "inline_reflector" | "reflector-rs" | "mrv196_card" | "mrv212_card" | "mrv166_card" | "mrv195_card"
            ) {
                pids.push(pid);
            }
        }
        pids
    }

    fn recover(restore_only: bool) -> Result<(), Box<dyn Error>> {
        let others = other_reflectors();
        if !others.is_empty() {
            return Err(format!("another card server/reflector is running (pid {:?}); stop it first", others).into());
        }
        let changed = restore_defaults(SLOT)?;
        if changed.is_empty() {
            if restore_only {
                println!("BAR4 slot 15 and PEM0 outbound regions already at firmware defaults");
            }
        } else {
            eprintln!("restored after unclean exit: {}", changed.join(", "));
        }
        Ok(())
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        if std::env::args().any(|a| a == "--restore") {
            return recover(true);
        }
        recover(false)?;
        let (eal, app_args) = Eal::init(std::env::args())?;
        let args = Args::parse_from(app_args);
        if args.restore {
            return Ok(());
        }
        if eal.lcores() != 2 {
            return Err("mrv212_card needs exactly 2 lcores (e.g. -l 3,4): main for lease/sync + worker for SDP/DPI".into());
        }

        let quit = Arc::new(AtomicBool::new(false));
        let q_sig = quit.clone();
        ctrlc::set_handler(move || q_sig.store(true, Ordering::Release))?;

        let pmu = CardPmu::open()?;
        let cntfrq = read_cntfrq();

        let slot_mz = Memzone::reserve_contiguous(&eal, "mrv212_bar4_slot15", SLOT_BYTES, 0, SLOT_BYTES as u32)?;
        unsafe { std::ptr::write_bytes(slot_mz.as_ptr(), 0, SLOT_BYTES) };
        let bar4_pa = slot_mz.phys_addr().ok_or("memzone has no physical address")?;
        let bar4_win = Bar4Window::claim(SLOT, bar4_pa)?;
        let bar4_prev = bar4_win.previous();
        let ready_ptr = unsafe { slot_mz.as_ptr().add(READY_OFFSET).cast::<AtomicU64>() };
        let nonce_ref: &AtomicU64 = unsafe { &*slot_mz.as_ptr().add(NONCE_OFFSET).cast::<AtomicU64>() };
        nonce_ref.store(0, Ordering::Release);
        unsafe { (*ready_ptr).store(READY, Ordering::Release) };
        dsb_sy();

        let lease_listener = Mutex::new(Some(TcpListener::bind(&args.control)?));
        let sync_listener = {
            let l = TcpListener::bind(&args.sync)?;
            l.set_nonblocking(true)?;
            Mutex::new(Some(l))
        };

        let ctrl = WorkerCtrl::new();
        let bar4_ptr_usize = slot_mz.as_ptr() as usize;
        let ready_ptr_usize = ready_ptr as usize;
        let dpi_vf_ref = args.dpi_vf.as_deref();
        struct CardStopOnDrop<'a> {
            quit: &'a AtomicBool,
            cmd_state: &'a AtomicU8,
        }
        impl Drop for CardStopOnDrop<'_> {
            fn drop(&mut self) {
                self.quit.store(true, Ordering::Release);
                let _ = self.cmd_state.compare_exchange(2, 3, Ordering::AcqRel, Ordering::Acquire);
            }
        }

        let results = eal.on_each_lcore(vec![0usize, 1usize], |idx, _| -> Result<(), String> {
            let _stop_guard = CardStopOnDrop { quit: &quit, cmd_state: &ctrl.cmd_state };
            if idx == 1 {
                return data_worker(
                    &eal,
                    bar4_ptr_usize,
                    args.queues,
                    args.tx_desc,
                    args.rx_desc,
                    args.pool_size,
                    dpi_vf_ref,
                    &ctrl,
                    &quit,
                );
            }
            let t0 = Instant::now();
            while ctrl.init_state.load(Ordering::Acquire) == 0 && t0.elapsed() < Duration::from_secs(10) {
                thread::sleep(Duration::from_millis(5));
            }
            if ctrl.init_state.load(Ordering::Acquire) != 1 {
                let err = ctrl.init_err.lock().unwrap().clone().unwrap_or_else(|| "init timeout".into());
                quit.store(true, Ordering::Release);
                return Err(format!("worker init failed: {err}"));
            }

            let mac = *ctrl.card_mac.lock().unwrap();
            println!(
                "mrv212_card BAR4 slot {SLOT} ready pa=0x{bar4_pa:x} (was 0x{:x}) sdp_mac={mac} queues={} tx_desc={} dpi={} control={} sync={}",
                bar4_prev,
                args.queues,
                args.tx_desc,
                args.dpi_vf.as_deref().unwrap_or("none"),
                args.control,
                args.sync
            );
            let _ = io::stdout().flush();

            let ll = lease_listener.lock().unwrap().take().unwrap();
            let sl = sync_listener.lock().unwrap().take().unwrap();
            thread::scope(|scope| {
                let lease_srv = lease::serve(scope, ll, nonce_ref)
                    .expect("start MRV-26 lease server");
                while !quit.load(Ordering::Acquire) {
                    match sl.accept() {
                        Ok((stream, _)) => {
                            handle_client(
                                stream,
                                bar4_ptr_usize as *mut u8,
                                &pmu,
                                &ctrl,
                                args.queues,
                                args.tx_desc,
                                cntfrq,
                                &quit,
                            );
                        }
                        Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(5));
                        }
                        Err(e) => {
                            eprintln!("sync accept err: {e}");
                            thread::sleep(Duration::from_millis(20));
                        }
                    }
                }
                if ctrl.cmd_state.load(Ordering::Acquire) == 2 {
                    ctrl.cmd_state.store(3, Ordering::Release);
                }
                lease_srv.close();
            });
            unsafe { (*(ready_ptr_usize as *const AtomicU64)).store(0, Ordering::Release) };
            dsb_sy();
            Ok(())
        });
        for r in results {
            r.map_err(io::Error::other)?;
        }

        drop(bar4_win);
        println!("mrv212_card exited cleanly and restored BAR4 slot {SLOT}");
        Ok(())
    }
}
