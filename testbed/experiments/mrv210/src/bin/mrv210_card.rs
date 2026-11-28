//! CN106 multi-core (1-8 queues) On-Path SmartNIC forwarder for MRV-210 (MRV-203 E4).
//!
//! Bridges `mercury -> 100GbE -> CN106 eth0 (N RxQs, RSS) -> CN106 sdp1-1 (N TxQs) -> saturn VF0 (N RxQs)`:
//! - `passive`:   Passive NIC forwarding for `privRing`, `ShRing-style`, and `rxBisect-style` arms
//!                (no card elastic FIFO, no BAR4 credit gating; unsent packets on SDP TX full drop as `bp_drops`).
//! - `credit`:    On-Path SmartNIC elastic per-queue FIFO (`fifo[q]` backed by CN106 48 MiB L3 + 48 GB DDR5)
//!                gated strictly by host core `q`'s exclusive 64 B BAR4 credit line (`credit_line_offset(q)`).
//!                Zero cross-core locks, zero false sharing, zero PCIe reads (local L3 poll ~10-20 ns).
//! - `probe-rss`: Records `stream_id -> eth0 RX queue` mapping for deterministic balanced & skewed RSS steering.
//!
//! Safety & Telemetry:
//! - Standard MRV-26 BAR4 slot-15 lease (`testbed::lease::serve` on `10.10.0.2:7526`) + `--restore`.
//! - Built-in ARP responder for `10.10.100.2` while `eth0` is bound to `vfio-pci`.
//! - 100% non-multiplexed CN106 `tad` (48 MiB LLC) + 6-channel `mrvl_ddr_pmu_*` (DDR5) hardware PMU.
//! - End-to-end untouched wire payload verification (`wire_bad`, `wire_torn`, `wire_lost`, `wire_ooo`).

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv210_card runs on the aarch64 CN106 card");
    std::process::exit(2);
}

#[cfg(target_arch = "aarch64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "aarch64")]
mod imp {
    use std::collections::VecDeque;
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Write};
    use std::mem::size_of;
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::thread;
    use std::time::Instant;

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, EthStats, Mbuf, Mempool, Memzone, Port, QueuePair};
    use testbed::inline::{CORES_OFFSET, NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{Bar4Window, restore_defaults};
    use mrv210::{
        CARD_CTRL_PORT, MAX_QUEUES, MAX_STREAMS, PayloadCheck, StreamTracker, credit_line_offset,
        credit_sig, extract_payload, verify_payload,
    };

    const BURST_N: usize = 64;
    const ETH_RX_DESC: u16 = 2048;
    const ETH_TX_DESC: u16 = 512;
    const SDP_RX_DESC: u16 = 512;
    const SDP_TX_DESC: u16 = 512;
    const CARD_IP: [u8; 4] = [10, 10, 100, 2];

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum CardMode {
        Passive,
        Credit,
        ProbeRss,
    }

    impl CardMode {
        fn parse_str(s: &str) -> Option<Self> {
            match s {
                "passive" => Some(Self::Passive),
                "credit" => Some(Self::Credit),
                "probe-rss" | "probe_rss" => Some(Self::ProbeRss),
                _ => None,
            }
        }

        fn as_str(self) -> &'static str {
            match self {
                Self::Passive => "passive",
                Self::Credit => "credit",
                Self::ProbeRss => "probe-rss",
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = CardMode::Credit)]
        mode: CardMode,
        /// Per-queue logical credit window `R_eff` (packets) in `credit` mode.
        #[arg(long, default_value_t = 192)]
        r_eff: u64,
        /// Per-queue card elastic FIFO capacity (packets) in `credit` mode.
        #[arg(long, default_value_t = 75000)]
        fifo_cap: usize,
        /// Per-core CN106 NPA mempool size (81919 per core fits 2 GiB hugepages and absorbs >10 ms 100GbE stalls).
        #[arg(long, default_value_t = 81919)]
        pool_size: u32,
        #[arg(long, default_value = "0002:02:00.0")]
        eth_dev: String,
        /// Card SDP VF PCI BDF.
        #[arg(long, default_value = "0002:01:00.2")]
        sdp_dev: String,
        /// MRV-26 BAR4 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        lease_addr: String,
        /// Control/PMU TCP listen port on `10.10.0.2`.
        #[arg(long, default_value_t = CARD_CTRL_PORT)]
        ctrl_port: u16,
        /// Expected wire payload bytes (0 = accept any valid MRV-210 payload >= 40 B).
        #[arg(long, default_value_t = 0)]
        expected_payload: usize,
        /// Restore BAR4 slot 15 and PEM0 outbound regions 248-255 and exit.
        #[arg(long)]
        restore: bool,
    }

    // ---- CN106 Card PMU (TAD 48 MiB LLC + 6x mrvl_ddr_pmu_* DDR5, 100% non-multiplexed) ----

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
        ("tad_alloc_any", 0x1c),
        ("tad_hit_any", 0x1f),
        ("tad_req_msh_out_dss_wr", 0x0d),
    ];

    const DDR_READS_CFG: u64 = 0x65;
    const DDR_WRITES_CFG: u64 = 0x64;

    struct PerfCounter {
        name: String,
        fd: RawFd,
    }

    impl PerfCounter {
        fn open(name: String, type_: u32, config: u64, cpu: i32) -> io::Result<Self> {
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
            if unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) } != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> (u64, u64, u64) {
            let mut buf = [0u64; 3];
            let n = unsafe {
                libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>())
            };
            assert_eq!(n, size_of::<[u64; 3]>() as isize);
            (buf[0], buf[1], buf[2])
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
        fs::read_to_string(path).ok()?.trim().parse::<u32>().ok()
    }

    struct CardPmu {
        tad: Vec<PerfCounter>,
        ddr_rd: Vec<PerfCounter>,
        ddr_wr: Vec<PerfCounter>,
        t0: Instant,
    }

    impl CardPmu {
        fn open() -> Self {
            let mut tad = Vec::new();
            if let Some(t) = read_sysfs_u32("/sys/bus/event_source/devices/tad/type") {
                for &(name, cfg) in TAD_EVENTS {
                    if let Ok(c) = PerfCounter::open(name.to_string(), t, cfg, 0) {
                        tad.push(c);
                    }
                }
            }
            let mut ddr_names: Vec<String> = fs::read_dir("/sys/bus/event_source/devices")
                .into_iter()
                .flatten()
                .filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|n| n.starts_with("mrvl_ddr_pmu_"))
                .collect();
            ddr_names.sort();
            let mut ddr_rd = Vec::new();
            let mut ddr_wr = Vec::new();
            for dev in ddr_names {
                let p = format!("/sys/bus/event_source/devices/{dev}/type");
                if let Some(t) = read_sysfs_u32(&p) {
                    if let Ok(c) = PerfCounter::open(format!("{dev}_rd"), t, DDR_READS_CFG, 0) {
                        ddr_rd.push(c);
                    }
                    if let Ok(c) = PerfCounter::open(format!("{dev}_wr"), t, DDR_WRITES_CFG, 0) {
                        ddr_wr.push(c);
                    }
                }
            }
            Self { tad, ddr_rd, ddr_wr, t0: Instant::now() }
        }

        fn snap_json(&self) -> String {
            let t_s = self.t0.elapsed().as_secs_f64();
            let mut entries: Vec<String> = Vec::new();
            for c in &self.tad {
                let (v, e, r) = c.read();
                entries.push(format!("\"{}\":[{v},{e},{r}]", c.name));
            }
            let (mut dr_v, mut dr_e, mut dr_r) = (0u64, 0u64, 0u64);
            let mut dr_min_pct = 100.0f64;
            for c in &self.ddr_rd {
                let (v, e, r) = c.read();
                dr_v = dr_v.wrapping_add(v);
                dr_e = dr_e.wrapping_add(e);
                dr_r = dr_r.wrapping_add(r);
                if e > 0 {
                    dr_min_pct = dr_min_pct.min(100.0 * r as f64 / e as f64);
                }
            }
            let (mut dw_v, mut dw_e, mut dw_r) = (0u64, 0u64, 0u64);
            let mut dw_min_pct = 100.0f64;
            for c in &self.ddr_wr {
                let (v, e, r) = c.read();
                dw_v = dw_v.wrapping_add(v);
                dw_e = dw_e.wrapping_add(e);
                dw_r = dw_r.wrapping_add(r);
                if e > 0 {
                    dw_min_pct = dw_min_pct.min(100.0 * r as f64 / e as f64);
                }
            }
            entries.push(format!("\"ddr_reads\":[{dr_v},{dr_e},{dr_r}]"));
            entries.push(format!("\"ddr_writes\":[{dw_v},{dw_e},{dw_r}]"));
            format!(
                "{{\"ok\":true,\"t_s\":{t_s:.6},\"ddr_channels\":{},\"ddr_rd_min_pct\":{dr_min_pct:.3},\"ddr_wr_min_pct\":{dw_min_pct:.3},\"tad\":{{{}}}}}",
                self.ddr_rd.len(),
                entries.join(",")
            )
        }
    }

    // ---- Shared Control & Per-Queue Telemetry ----

    #[derive(Clone, Copy)]
    struct RuntimeCfg {
        mode: CardMode,
        r_eff: u64,
        fifo_cap: usize,
        run_gen: u64,
        expected_payload: usize,
    }

    #[derive(Clone, Default)]
    struct QueueCnt {
        eth_rx_pkts: u64,
        eth_rx_bytes: u64,
        arp_replies: u64,
        wire_bad: u64,
        wire_torn: u64,
        wire_lost: u64,
        wire_ooo: u64,
        c2h_pkts: u64,
        c2h_bytes: u64,
        bp_drops: u64,
        sdp_tx_retries: u64,
        fifo_enq: u64,
        fifo_peak: u64,
        fifo_depth_now: u64,
        fifo_full_drops: u64,
        credit_stalls: u64,
        credit_updates: u64,
        credit_reread: u64,
        credit_torn: u64,
        taken_cached: u64,
        submitted: u64,
    }

    impl QueueCnt {
        fn to_json(&self) -> String {
            format!(
                "{{\"eth_rx_pkts\":{},\"eth_rx_bytes\":{},\"arp_replies\":{},\"wire_bad\":{},\"wire_torn\":{},\"wire_lost\":{},\"wire_ooo\":{},\"c2h_pkts\":{},\"c2h_bytes\":{},\"bp_drops\":{},\"sdp_tx_retries\":{},\"fifo_enq\":{},\"fifo_peak\":{},\"fifo_depth_now\":{},\"fifo_full_drops\":{},\"credit_stalls\":{},\"credit_updates\":{},\"credit_reread\":{},\"credit_torn\":{},\"taken_cached\":{},\"submitted\":{}}}",
                self.eth_rx_pkts,
                self.eth_rx_bytes,
                self.arp_replies,
                self.wire_bad,
                self.wire_torn,
                self.wire_lost,
                self.wire_ooo,
                self.c2h_pkts,
                self.c2h_bytes,
                self.bp_drops,
                self.sdp_tx_retries,
                self.fifo_enq,
                self.fifo_peak,
                self.fifo_depth_now,
                self.fifo_full_drops,
                self.credit_stalls,
                self.credit_updates,
                self.credit_reread,
                self.credit_torn,
                self.taken_cached,
                self.submitted,
            )
        }
    }

    struct SyncSlot {
        ack_gen: AtomicU64,
        snap: Mutex<QueueCnt>,
    }

    impl SyncSlot {
        fn new() -> Self {
            Self { ack_gen: AtomicU64::new(0), snap: Mutex::new(QueueCnt::default()) }
        }
    }

    struct Shared {
        eth_mac: [u8; 6],
        bar_base_addr: usize,
        active: AtomicBool,
        quit: AtomicBool,
        /// `(gen << 4) | cmd`: 1 = RESET, 2 = SNAP, 3 = APPLY_CFG_AND_RESET
        cmd_word: AtomicU64,
        cfg: Mutex<RuntimeCfg>,
        slots: Vec<SyncSlot>,
        flow_q: [AtomicU64; MAX_STREAMS],
        eth_base: Mutex<EthStats>,
        sdp_base: Mutex<EthStats>,
        eth_xbase: Mutex<Vec<(String, u64)>>,
        t_reset: Mutex<Instant>,
    }

    impl Shared {
        fn broadcast_cmd(&self, cmd: u64) -> Vec<QueueCnt> {
            let cur = self.cmd_word.load(Ordering::Relaxed);
            let next_gen = (cur >> 4) + 1;
            self.cmd_word.store((next_gen << 4) | cmd, Ordering::Release);
            let deadline = Instant::now() + std::time::Duration::from_secs(5);
            for slot in &self.slots {
                while slot.ack_gen.load(Ordering::Acquire) < next_gen {
                    if Instant::now() >= deadline || self.quit.load(Ordering::Relaxed) {
                        break;
                    }
                    std::hint::spin_loop();
                }
            }
            self.slots.iter().map(|s| s.snap.lock().unwrap().clone()).collect()
        }

        fn clear_bar4_credits(&self) {
            if self.bar_base_addr == 0 {
                return;
            }
            // Zero all 8 exclusive 64 B credit lines [0..512) inside BAR4 slot 15.
            unsafe {
                std::ptr::write_bytes(self.bar_base_addr as *mut u8, 0, MAX_QUEUES * 64);
                std::arch::asm!("dsb ish", options(nostack, preserves_flags));
            }
        }
    }

    #[inline(always)]
    fn try_handle_arp<'e>(
        m: Mbuf<'e>,
        eth_mac: [u8; 6],
        eth_qp: &mut QueuePair<'_, 'e>,
        cnt: &mut QueueCnt,
    ) -> Option<Mbuf<'e>> {
        let d = m.data();
        if d.len() >= 42 && d[12] == 0x08 && d[13] == 0x06 {
            if d[20] == 0x00 && d[21] == 0x01 && d[38..42] == CARD_IP {
                let mut reply = m;
                let b = reply.data_mut();
                let mut req_mac = [0u8; 6];
                let mut req_ip = [0u8; 4];
                req_mac.copy_from_slice(&b[22..28]);
                req_ip.copy_from_slice(&b[28..32]);
                b[0..6].copy_from_slice(&req_mac);
                b[6..12].copy_from_slice(&eth_mac);
                b[20] = 0x00;
                b[21] = 0x02;
                b[22..28].copy_from_slice(&eth_mac);
                b[28..32].copy_from_slice(&CARD_IP);
                b[32..38].copy_from_slice(&req_mac);
                b[38..42].copy_from_slice(&req_ip);
                let _ = eth_qp.send(reply);
                cnt.arp_replies += 1;
            }
            return None;
        }
        Some(m)
    }

    /// Poll host core `q`'s exclusive 64 B BAR4 credit line directly from CN106 local L3.
    /// Host delivers 64 B atomically via MOVDIR64B, while ARMv8 issues separate 8 B loads;
    /// use Acquire barriers + seqlock retry (up to 8 attempts) to tolerate concurrent 64 B
    /// overwrites while still flagging genuine corruption as `credit_torn`.
    #[inline(always)]
    fn poll_bar4_credit(
        bar_base: *const u8,
        q: usize,
        run_gen: u64,
        taken_cached: &mut u64,
        updates: &mut u64,
        reread: &mut u64,
        torn: &mut u64,
    ) {
        let p = unsafe { bar_base.add(credit_line_offset(q)).cast::<u64>() };
        let w0_peek = unsafe { p.add(0).read_volatile() };
        if w0_peek <= *taken_cached {
            return;
        }
        for attempt in 0..8u32 {
            let w0 = unsafe { p.add(0).read_volatile() };
            std::sync::atomic::fence(Ordering::Acquire);
            let w1 = unsafe { p.add(1).read_volatile() };
            let w5 = unsafe { p.add(5).read_volatile() };
            std::sync::atomic::fence(Ordering::Acquire);
            let w7 = unsafe { p.add(7).read_volatile() };
            if w1 != run_gen {
                if attempt == 0 && w0 == w7 {
                    return;
                }
                continue;
            }
            if w0 == w7 && w5 == credit_sig(w0, run_gen, q) {
                if attempt > 0 {
                    *reread += 1;
                }
                if w0 > *taken_cached {
                    *taken_cached = w0;
                    *updates += 1;
                }
                return;
            }
        }
        *torn += 1;
    }

    struct WorkerInput<'p, 'e> {
        q: usize,
        eth_qp: QueuePair<'p, 'e>,
        sdp_qp: QueuePair<'p, 'e>,
    }

    fn worker_main<'p, 'e>(mut inp: WorkerInput<'p, 'e>, sh: &Shared) {
        let q = inp.q;
        let q1 = (q + 1) as u64;
        let bar_base = sh.bar_base_addr as *const u8;

        let mut cfg = *sh.cfg.lock().unwrap();
        let mut cnt = QueueCnt::default();
        let mut tracker = StreamTracker::new();
        let mut handled_cmd_gen = 0u64;

        let mut fifo: VecDeque<Mbuf<'e>> = VecDeque::with_capacity(cfg.fifo_cap.min(131_072));
        let mut rx_burst: Burst<'e, BURST_N> = Burst::new();
        let mut tx_burst: Burst<'e, BURST_N> = Burst::new();

        while !sh.quit.load(Ordering::Relaxed) {
            let cw = sh.cmd_word.load(Ordering::Acquire);
            let cg = cw >> 4;
            if cg > handled_cmd_gen {
                let cmd = cw & 0xf;
                if cmd == 1 || cmd == 3 {
                    if cmd == 3 {
                        cfg = *sh.cfg.lock().unwrap();
                    }
                    fifo.clear();
                    tx_burst.clear();
                    rx_burst.clear();
                    tracker.reset();
                    cnt = QueueCnt::default();
                    *sh.slots[q].snap.lock().unwrap() = cnt.clone();
                } else if cmd == 2 {
                    cnt.fifo_depth_now = fifo.len() as u64;
                    cnt.wire_ooo = tracker.ooo;
                    *sh.slots[q].snap.lock().unwrap() = cnt.clone();
                }
                handled_cmd_gen = cg;
                sh.slots[q].ack_gen.store(cg, Ordering::Release);
            }

            // 1. Poll eth0 RX queue `q`
            let n = inp.eth_qp.rx(&mut rx_burst);
            let active = sh.active.load(Ordering::Relaxed);

            if n > 0 {
                rx_burst.drain_into(|m| {
                    let Some(m) = try_handle_arp(m, sh.eth_mac, &mut inp.eth_qp, &mut cnt) else {
                        return;
                    };
                    let dlen = m.data_len();
                    let wire_len = (dlen + 4) as u64;

                    if cfg.mode == CardMode::ProbeRss {
                        if let Some(pay) = extract_payload(m.data()) {
                            if let PayloadCheck::Ok { stream_id, .. } = verify_payload(pay, 0, cfg.run_gen) {
                                if (stream_id as usize) < MAX_STREAMS {
                                    sh.flow_q[stream_id as usize].store(q1, Ordering::Relaxed);
                                }
                                cnt.eth_rx_pkts += 1;
                                cnt.eth_rx_bytes += wire_len;
                            }
                        }
                        return;
                    }

                    if !active {
                        return;
                    }

                    cnt.eth_rx_pkts += 1;
                    cnt.eth_rx_bytes += wire_len;

                    // Verify untouched wire payload from mercury (zero mutation!)
                    match extract_payload(m.data()) {
                        Some(pay) => match verify_payload(pay, cfg.expected_payload, cfg.run_gen) {
                            PayloadCheck::Ok { stream_id, .. } => {
                                if (stream_id as usize) < MAX_STREAMS {
                                    sh.flow_q[stream_id as usize].store(q1, Ordering::Relaxed);
                                }
                                cnt.wire_lost += tracker.observe(stream_id, unsafe {
                                    pay.as_ptr().cast::<u64>().read_unaligned()
                                });
                            }
                            PayloadCheck::Bad => {
                                cnt.wire_bad += 1;
                                return;
                            }
                            PayloadCheck::Torn => {
                                cnt.wire_torn += 1;
                                return;
                            }
                        },
                        None => {
                            // Ignore stray background non-MRV210 frames (e.g. IPv6 RA/LLDP)
                            return;
                        }
                    }

                    match cfg.mode {
                        CardMode::Passive => {
                            if tx_burst.push(m).is_err() {
                                cnt.bp_drops += 1;
                            }
                        }
                        CardMode::Credit => {
                            if fifo.len() < cfg.fifo_cap {
                                fifo.push_back(m);
                                cnt.fifo_enq += 1;
                                let d = fifo.len() as u64;
                                if d > cnt.fifo_peak {
                                    cnt.fifo_peak = d;
                                }
                            } else {
                                cnt.fifo_full_drops += 1;
                            }
                        }
                        CardMode::ProbeRss => {}
                    }
                });
            }

            // 2. Forward to sdp1-1 TX queue `q` (1-to-1 -> Host VF0 RX queue `q`)
            match cfg.mode {
                CardMode::Passive => {
                    if !tx_burst.is_empty() {
                        let before = tx_burst.len();
                        let sent_bytes: u64 = tx_burst.as_slice().iter().map(|m| m.data_len() as u64).sum();
                        let sent = inp.sdp_qp.tx(&mut tx_burst);
                        let unsent = before - sent;
                        if sent > 0 {
                            cnt.c2h_pkts += sent as u64;
                            cnt.submitted += sent as u64;
                            if unsent == 0 {
                                cnt.c2h_bytes += sent_bytes;
                            } else {
                                let rem_bytes: u64 = tx_burst.as_slice().iter().map(|m| m.data_len() as u64).sum();
                                cnt.c2h_bytes += sent_bytes.saturating_sub(rem_bytes);
                            }
                        }
                        if unsent > 0 {
                            // Passive NIC drops excess packets when host SDP OQ hits WMARK + SQ fills
                            cnt.bp_drops += unsent as u64;
                            tx_burst.clear();
                        }
                    }
                }
                CardMode::Credit => {
                    if !active {
                        continue;
                    }
                    // Poll host core `q`'s exclusive 64 B BAR4 credit line from local L3
                    poll_bar4_credit(
                        bar_base,
                        q,
                        cfg.run_gen,
                        &mut cnt.taken_cached,
                        &mut cnt.credit_updates,
                        &mut cnt.credit_reread,
                        &mut cnt.credit_torn,
                    );

                    if fifo.is_empty() {
                        continue;
                    }

                    let inflight = cnt.submitted.saturating_sub(cnt.taken_cached);
                    if inflight >= cfg.r_eff {
                        cnt.credit_stalls += 1;
                        continue;
                    }

                    let budget = (cfg.r_eff - inflight).min(BURST_N as u64) as usize;
                    let take = budget.min(fifo.len());
                    for _ in 0..take {
                        if let Some(m) = fifo.pop_front() {
                            if tx_burst.push(m).is_err() {
                                break;
                            }
                        }
                    }
                    if !tx_burst.is_empty() {
                        let before = tx_burst.len();
                        let mut lens = [0u64; BURST_N];
                        for (i, m) in tx_burst.as_slice().iter().enumerate() {
                            lens[i] = m.data_len() as u64;
                        }
                        let sent = inp.sdp_qp.tx(&mut tx_burst);
                        if sent > 0 {
                            cnt.c2h_pkts += sent as u64;
                            cnt.submitted += sent as u64;
                            for &l in &lens[..sent] {
                                cnt.c2h_bytes += l;
                            }
                        }
                        if sent < before {
                            // Preserve strict FIFO ordering and 0% loss: return unsent mbufs to FIFO front!
                            cnt.sdp_tx_retries += (before - sent) as u64;
                            while let Some(m) = tx_burst.pop() {
                                fifo.push_front(m);
                            }
                        }
                    }
                }
                CardMode::ProbeRss => {}
            }
        }
        fifo.clear();
        tx_burst.clear();
        rx_burst.clear();
    }

    fn eth_stats_delta(cur: EthStats, base: EthStats) -> EthStats {
        EthStats {
            ipackets: cur.ipackets.saturating_sub(base.ipackets),
            opackets: cur.opackets.saturating_sub(base.opackets),
            ibytes: cur.ibytes.saturating_sub(base.ibytes),
            obytes: cur.obytes.saturating_sub(base.obytes),
            imissed: cur.imissed.saturating_sub(base.imissed),
            ierrors: cur.ierrors.saturating_sub(base.ierrors),
            oerrors: cur.oerrors.saturating_sub(base.oerrors),
            rx_nombuf: cur.rx_nombuf.saturating_sub(base.rx_nombuf),
        }
    }

    fn eth_stats_json(s: EthStats) -> String {
        format!(
            "{{\"ipackets\":{},\"opackets\":{},\"ibytes\":{},\"obytes\":{},\"imissed\":{},\"ierrors\":{},\"oerrors\":{},\"rx_nombuf\":{}}}",
            s.ipackets, s.opackets, s.ibytes, s.obytes, s.imissed, s.ierrors, s.oerrors, s.rx_nombuf
        )
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        if std::env::args().any(|a| a == "--restore") {
            let changed = restore_defaults(SLOT)?;
            println!("MRV210_CARD RESTORED changes={}", changed.len());
            for c in changed {
                println!("  {c}");
            }
            return Ok(());
        }

        let (eal, rest) = Eal::init(std::env::args())?;
        let args = Args::parse_from(rest);

        let total_lcores = eal.lcores();
        if total_lcores < 2 || total_lcores - 1 > MAX_QUEUES {
            return Err(format!(
                "need 1 main lcore + 1..={MAX_QUEUES} worker lcores (got {total_lcores})"
            )
            .into());
        }
        let n_cores = total_lcores - 1;

        // Per-core dedicated CN106 NPA mempools (Pitfall 3: LIFO locality in 48 MiB L3 + 48 GB DDR5)
        let data_room = 2048 + 128;
        let mut owned_pools: Vec<Mempool<'_>> = Vec::with_capacity(n_cores);
        for q in 0..n_cores {
            owned_pools.push(Mempool::new_with_cache(
                &eal,
                &format!("mrv210_p{q}"),
                args.pool_size,
                256,
                data_room,
                0,
            )?);
        }
        let pool_refs: Vec<&Mempool<'_>> = owned_pools.iter().collect();

        // Start physical 100GbE `eth0` with `n_cores` RSS RX queues + `n_cores` TX queues (for ARP)
        let eth_pid = Port::by_name(&eal, &args.eth_dev)?;
        let (mut eth_port, _) = Port::start_fast(
            &eal,
            eth_pid,
            &pool_refs,
            n_cores as u16,
            n_cores as u16,
            ETH_RX_DESC,
            ETH_TX_DESC,
            None,
            n_cores > 1,
            true,
            true,
        )?;
        let _ = eth_port.promiscuous();
        let eth_mac = eth_port.mac();
        let eth_rss_hf = eth_port.rss_hf();
        let _ = eth_port.stats_reset();

        // Start `sdp1-1` with `n_cores` TX queues (1-to-1 -> Host VF0 RX queues 0..n_cores-1)
        let sdp_pid = Port::by_name(&eal, &args.sdp_dev)?;
        let (mut sdp_port, _) = Port::start_fast(
            &eal,
            sdp_pid,
            &pool_refs,
            1,
            n_cores as u16,
            SDP_RX_DESC,
            SDP_TX_DESC,
            None,
            false,
            true,
            false,
        )?;
        let _ = sdp_port.stats_reset();

        // Always map BAR4 slot 15 and serve MRV-26 lease so switching arms does not need card restart
        let bar_mz = Memzone::reserve_contiguous(&eal, "mrv210_bar4", SLOT_BYTES, 0, SLOT_BYTES as u32)?;
        unsafe { std::ptr::write_bytes(bar_mz.as_ptr(), 0, SLOT_BYTES) };
        let bar_pa = bar_mz.phys_addr().ok_or("BAR4 memzone phys_addr failed")?;
        let _bar_win = Bar4Window::claim(SLOT, bar_pa)?;
        let bar_base_addr = bar_mz.as_ptr() as usize;
        unsafe {
            (&*((bar_base_addr + READY_OFFSET) as *const AtomicU64)).store(READY, Ordering::Release);
            (&*((bar_base_addr + CORES_OFFSET) as *const AtomicU64)).store(n_cores as u64, Ordering::Release);
        }

        let (eth_stats_h, eth_qps) = eth_port.split_queues();
        let (sdp_stats_h, sdp_qps) = sdp_port.split_queues();

        let shared = Arc::new(Shared {
            eth_mac,
            bar_base_addr,
            active: AtomicBool::new(false),
            quit: AtomicBool::new(false),
            cmd_word: AtomicU64::new(0),
            cfg: Mutex::new(RuntimeCfg {
                mode: args.mode,
                r_eff: args.r_eff,
                fifo_cap: args.fifo_cap,
                run_gen: 1,
                expected_payload: args.expected_payload,
            }),
            slots: (0..n_cores).map(|_| SyncSlot::new()).collect(),
            flow_q: std::array::from_fn(|_| AtomicU64::new(0)),
            eth_base: Mutex::new(eth_stats_h.stats().unwrap_or_default()),
            sdp_base: Mutex::new(sdp_stats_h.stats().unwrap_or_default()),
            eth_xbase: Mutex::new(eth_stats_h.xstats().unwrap_or_default()),
            t_reset: Mutex::new(Instant::now()),
        });

        let sh_sig = Arc::clone(&shared);
        let _ = ctrlc::set_handler(move || {
            sh_sig.quit.store(true, Ordering::Release);
        });

        let pmu = CardPmu::open();
        let ctrl_listener = TcpListener::bind(("0.0.0.0", args.ctrl_port))?;
        let _ = ctrl_listener.set_nonblocking(true);

        let mut lcore_inputs: Vec<Option<WorkerInput<'_, '_>>> = Vec::with_capacity(total_lcores);
        lcore_inputs.push(None);
        for (q, (eth_qp, sdp_qp)) in eth_qps.into_iter().zip(sdp_qps.into_iter()).enumerate() {
            lcore_inputs.push(Some(WorkerInput { q, eth_qp, sdp_qp }));
        }

        let lease_addr = args.lease_addr.clone();
        let ctrl_port = args.ctrl_port;
        let init_mode = args.mode.as_str();
        let init_reff = args.r_eff;
        let init_fcap = args.fifo_cap;

        thread::scope(|scope| {
            let l_tcp = TcpListener::bind(&lease_addr).expect("bind lease TCP");
            let nonce_ref = unsafe { &*((bar_base_addr + NONCE_OFFSET) as *const AtomicU64) };
            let lease_srv = lease::serve(scope, l_tcp, nonce_ref).expect("start lease server");

            let _ = eal.on_each_lcore(lcore_inputs, |lcore_idx, opt_inp| {
                if let Some(inp) = opt_inp {
                    worker_main(inp, &shared);
                    return;
                }
                debug_assert_eq!(lcore_idx, 0);
                println!(
                    "MRV210_CARD READY mode={init_mode} queues={n_cores} r_eff={init_reff} fifo_cap={init_fcap} rss_hf={eth_rss_hf:#x} ctrl={ctrl_port}"
                );

                while !shared.quit.load(Ordering::Relaxed) {
                    match ctrl_listener.accept() {
                        Ok((mut s, _)) => {
                            let _ = s.set_nonblocking(false);
                            let mut r = BufReader::new(s.try_clone().unwrap());
                            let mut line = String::new();
                            if r.read_line(&mut line).unwrap_or(0) == 0 {
                                continue;
                            }
                            let cmd = line.trim();
                            let reply = if cmd == "PING" {
                                "{\"ok\":true}".to_string()
                            } else if cmd == "START" {
                                shared.active.store(true, Ordering::Release);
                                "{\"ok\":true}".to_string()
                            } else if cmd == "STOP" {
                                shared.active.store(false, Ordering::Release);
                                "{\"ok\":true}".to_string()
                            } else if cmd == "CLEAR_BAR4" {
                                shared.clear_bar4_credits();
                                "{\"ok\":true}".to_string()
                            } else if cmd == "RESET" {
                                for fq in &shared.flow_q {
                                    fq.store(0, Ordering::Relaxed);
                                }
                                let _ = shared.broadcast_cmd(1);
                                *shared.eth_base.lock().unwrap() = eth_stats_h.stats().unwrap_or_default();
                                *shared.sdp_base.lock().unwrap() = sdp_stats_h.stats().unwrap_or_default();
                                *shared.eth_xbase.lock().unwrap() = eth_stats_h.xstats().unwrap_or_default();
                                *shared.t_reset.lock().unwrap() = Instant::now();
                                "{\"ok\":true}".to_string()
                            } else if let Some(rest) = cmd.strip_prefix("CONFIG ") {
                                shared.active.store(false, Ordering::Release);
                                {
                                    let mut c = shared.cfg.lock().unwrap();
                                    for tok in rest.split_whitespace() {
                                        if let Some((k, v)) = tok.split_once('=') {
                                            match k {
                                                "mode" => {
                                                    if let Some(m) = CardMode::parse_str(v) {
                                                        c.mode = m;
                                                    }
                                                }
                                                "r_eff" => {
                                                    if let Ok(x) = v.parse::<u64>() {
                                                        c.r_eff = x.max(1);
                                                    }
                                                }
                                                "fifo_cap" => {
                                                    if let Ok(x) = v.parse::<usize>() {
                                                        let max_safe = (args.pool_size as usize).saturating_sub(4096).max(64);
                                                        c.fifo_cap = x.clamp(64, max_safe);
                                                    }
                                                }
                                                "run_gen" => {
                                                    if let Ok(x) = v.parse::<u64>() {
                                                        c.run_gen = x;
                                                    }
                                                }
                                                "expected_payload" => {
                                                    if let Ok(x) = v.parse::<usize>() {
                                                        c.expected_payload = x;
                                                    }
                                                }
                                                _ => {}
                                            }
                                        }
                                    }
                                }
                                shared.clear_bar4_credits();
                                for fq in &shared.flow_q {
                                    fq.store(0, Ordering::Relaxed);
                                }
                                let _ = shared.broadcast_cmd(3);
                                *shared.eth_base.lock().unwrap() = eth_stats_h.stats().unwrap_or_default();
                                *shared.sdp_base.lock().unwrap() = sdp_stats_h.stats().unwrap_or_default();
                                *shared.eth_xbase.lock().unwrap() = eth_stats_h.xstats().unwrap_or_default();
                                *shared.t_reset.lock().unwrap() = Instant::now();
                                let c = *shared.cfg.lock().unwrap();
                                format!(
                                    "{{\"ok\":true,\"mode\":\"{}\",\"r_eff\":{},\"fifo_cap\":{},\"run_gen\":{},\"expected_payload\":{}}}",
                                    c.mode.as_str(),
                                    c.r_eff,
                                    c.fifo_cap,
                                    c.run_gen,
                                    c.expected_payload
                                )
                            } else if cmd == "PMU_SNAP" {
                                pmu.snap_json()
                            } else if cmd == "STATS" {
                                let qs = shared.broadcast_cmd(2);
                                let dt_s = shared.t_reset.lock().unwrap().elapsed().as_secs_f64();
                                let mut tot = QueueCnt::default();
                                let mut per_q_json = Vec::with_capacity(qs.len());
                                for qc in &qs {
                                    tot.eth_rx_pkts += qc.eth_rx_pkts;
                                    tot.eth_rx_bytes += qc.eth_rx_bytes;
                                    tot.arp_replies += qc.arp_replies;
                                    tot.wire_bad += qc.wire_bad;
                                    tot.wire_torn += qc.wire_torn;
                                    tot.wire_lost += qc.wire_lost;
                                    tot.wire_ooo += qc.wire_ooo;
                                    tot.c2h_pkts += qc.c2h_pkts;
                                    tot.c2h_bytes += qc.c2h_bytes;
                                    tot.bp_drops += qc.bp_drops;
                                    tot.sdp_tx_retries += qc.sdp_tx_retries;
                                    tot.fifo_enq += qc.fifo_enq;
                                    tot.fifo_peak = tot.fifo_peak.max(qc.fifo_peak);
                                    tot.fifo_depth_now += qc.fifo_depth_now;
                                    tot.fifo_full_drops += qc.fifo_full_drops;
                                    tot.credit_stalls += qc.credit_stalls;
                                    tot.credit_updates += qc.credit_updates;
                                    tot.credit_reread += qc.credit_reread;
                                    tot.credit_torn += qc.credit_torn;
                                    tot.taken_cached += qc.taken_cached;
                                    tot.submitted += qc.submitted;
                                    per_q_json.push(qc.to_json());
                                }
                                let eth_cur = eth_stats_h.stats().unwrap_or_default();
                                let eth_d = eth_stats_delta(eth_cur, *shared.eth_base.lock().unwrap());
                                let sdp_cur = sdp_stats_h.stats().unwrap_or_default();
                                let sdp_d = eth_stats_delta(sdp_cur, *shared.sdp_base.lock().unwrap());

                                let xcur = eth_stats_h.xstats().unwrap_or_default();
                                let xbase = shared.eth_xbase.lock().unwrap();
                                let mut xparts = Vec::new();
                                for (i, (name, v)) in xcur.iter().enumerate() {
                                    if name == "rpm_rx_aFramesReceivedOK"
                                        || name == "rpm_rx_ifInErrors"
                                        || name == "rx_missed_errors"
                                    {
                                        let bv = xbase.get(i).map(|x| x.1).unwrap_or(0);
                                        xparts.push(format!("\"{name}\":{}", v.saturating_sub(bv)));
                                    }
                                }

                                let fq_str: Vec<String> = shared
                                    .flow_q
                                    .iter()
                                    .map(|a| a.load(Ordering::Relaxed).to_string())
                                    .collect();
                                let c = *shared.cfg.lock().unwrap();
                                format!(
                                    "{{\"ok\":true,\"mode\":\"{}\",\"queues\":{},\"r_eff\":{},\"dt_s\":{dt_s:.6},\"eth_rx_pkts\":{},\"eth_rx_bytes\":{},\"arp_replies\":{},\"wire_bad\":{},\"wire_torn\":{},\"wire_lost\":{},\"wire_ooo\":{},\"c2h_pkts\":{},\"c2h_bytes\":{},\"bp_drops\":{},\"sdp_tx_retries\":{},\"fifo_enq\":{},\"fifo_peak_max\":{},\"fifo_depth_now\":{},\"fifo_full_drops\":{},\"credit_stalls\":{},\"credit_updates\":{},\"credit_reread\":{},\"credit_torn\":{},\"eth\":{},\"sdp\":{},\"eth_xstats\":{{{}}},\"per_q\":[{}],\"flow_q\":[{}]}}",
                                    c.mode.as_str(),
                                    n_cores,
                                    c.r_eff,
                                    tot.eth_rx_pkts,
                                    tot.eth_rx_bytes,
                                    tot.arp_replies,
                                    tot.wire_bad,
                                    tot.wire_torn,
                                    tot.wire_lost,
                                    tot.wire_ooo,
                                    tot.c2h_pkts,
                                    tot.c2h_bytes,
                                    tot.bp_drops,
                                    tot.sdp_tx_retries,
                                    tot.fifo_enq,
                                    tot.fifo_peak,
                                    tot.fifo_depth_now,
                                    tot.fifo_full_drops,
                                    tot.credit_stalls,
                                    tot.credit_updates,
                                    tot.credit_reread,
                                    tot.credit_torn,
                                    eth_stats_json(eth_d),
                                    eth_stats_json(sdp_d),
                                    xparts.join(","),
                                    per_q_json.join(","),
                                    fq_str.join(",")
                                )
                            } else if cmd == "QUIT" {
                                shared.active.store(false, Ordering::Release);
                                shared.quit.store(true, Ordering::Release);
                                "{\"ok\":true}".to_string()
                            } else {
                                "{\"ok\":false,\"err\":\"unknown_cmd\"}".to_string()
                            };
                            let _ = writeln!(s, "{reply}");
                            let _ = s.flush();
                        }
                        Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                            thread::sleep(std::time::Duration::from_micros(200));
                        }
                        Err(_) => {
                            thread::sleep(std::time::Duration::from_millis(1));
                        }
                    }
                }
            });

            lease_srv.close();
        });

        Ok(())
    }
}
