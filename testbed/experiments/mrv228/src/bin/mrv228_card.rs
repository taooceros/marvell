//! Multi-core CN106 100GbE SmartNIC bridge (`eth0` RSS <-> standard `sdp1-1`)
//! for MRV-228: multi-flow multiplexing (`F = 16 / 64 / 256 / 1,024` flows
//! sharing `4` standard SDP hardware queues).
//!
//! Card modes:
//!   - `--mode native-bp`   : Arms 1 & 2 (`BP=1` stop-drain vs host-drain).
//!                            `nb_txq = 4` (`ROC_NIX_TM_SDP` `0x3008..0x300b`),
//!                            small fair `--sdp-tx-desc 256`, on-NIC per-lane
//!                            elastic FIFO absorbs bursts whenever CN106 hardware
//!                            backpressure stalls `sdp1-1 TX[lane]`. ZERO BAR4.
//!   - `--mode large-sdp`   : Arm 3 (`Large SDP`, `D=2048`), direct push. ZERO BAR4.
//!   - `--mode ceio`        : Arm 4 (faithful MRV-211 fortified CEIO dual-path:
//!                            `4` Fast + `4` Slow SDP queues, dedicated 5th CN106
//!                            ARM core Flow Controller with exact per-flow BAR4
//!                            consumption visibility, RMT rewrite & phase-exclusivity).
//!   - `--mode per-flow-cr` : Arm 5 (OUR DESIGN: `nb_txq = 4`, `--sdp-tx-desc 256`,
//!                            packed BAR4 per-flow fence-free credit ring [16 flows
//!                            per 64 B line -> 1,024 flows in 4 KiB], gating packets
//!                            BEFORE the shared hardware SDP queue so slow/elephant
//!                            flows stay in CN106 L3+DDR5 while healthy RPC flows
//!                            sharing the exact same HW queue fly unimpeded!).

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv228_card runs on the aarch64 CN106 card");
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
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU32, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, Mbuf, Mempool, Memzone, Port, PortStats, QueuePair, rdtsc_after_loads};
    use testbed::inline::{NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{Bar4Window, restore_defaults};
    use mrv228::{
        BAR4_CARD_META_OFF, CARD_SYNC_ADDR, CLASS_RPC_HI, CeioControllerConfig, CeioControllerStats,
        CeioFlowController, CeioSlowPullLine, DEFAULT_CEIO_C_TOTAL, DEFAULT_CEIO_PULL_WINDOW,
        DEFAULT_PRIO_RESERVE, DEFAULT_R_EFF_PER_Q, DEFAULT_R_FLOW, ETH_MBUF_BUF_SIZE,
        FLAG_CEIO_LLC_LEAK, FLAG_PHASE_PAUSED, FLAG_SLOW_HINT, FastHist, HostQueueCreditLine,
        MAX_CARD_CORES, MAX_DRAIN, MAX_FLOWS, MAX_WIRE_PAYLOAD, MIN_WIRE_PAYLOAD, OP_ACK, OP_DATA,
        OP_PROBE, PATH_CEIO_FAST, PATH_CEIO_SLOW, PATH_DIRECT_SDP, PATH_PER_FLOW_CR,
        PerFlowCreditScheduler, PerFlowSchedStats, RMT_ACT_FAST, RMT_ACT_PHASE_PAUSE, RMT_ACT_SLOW,
        ROCE_HDR_LEN, ROCE_ICRC_LEN, ROCE_UDP_PORT, RmtRuleEntry, RoceTemplate, SDP_HDR_LEN,
        SDP_MBUF_BUF_SIZE, SDP_MTU, SDP_UDP_DST_PORT, pack_meta, probe_cookie, read_bar4_flow_taken,
        stamp_card_meta, unpack_flow_meta, unpack_meta,
    };

    const ETH_DESC: u16 = 4096;
    const SDP_RX_DESC: u16 = 1024;
    const MAX_PEERS: usize = 16;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Mode {
        NativeBp,
        LargeSdp,
        Ceio,
        PerFlowCr,
    }

    impl Mode {
        fn as_str(self) -> &'static str {
            match self {
                Mode::NativeBp => "native-bp",
                Mode::LargeSdp => "large-sdp",
                Mode::Ceio => "ceio",
                Mode::PerFlowCr => "per-flow-cr",
            }
        }
        fn id(self) -> u64 {
            match self {
                Mode::NativeBp => 1,
                Mode::LargeSdp => 2,
                Mode::Ceio => 3,
                Mode::PerFlowCr => 4,
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = Mode::PerFlowCr)]
        mode: Mode,
        #[arg(long, default_value = "0002:02:00.0")]
        eth_dev: String,
        #[arg(long, default_value = "0002:01:00.2")]
        sdp_dev: String,
        #[arg(long, default_value_t = 4)]
        data_cores: usize,
        #[arg(long, default_value_t = 256)]
        sdp_tx_desc: u16,
        #[arg(long, default_value_t = 65536)]
        eth_pool_size: u32,
        #[arg(long, default_value_t = 131072)]
        sdp_pool_size: u32,
        #[arg(long, default_value_t = DEFAULT_R_EFF_PER_Q)]
        r_eff_per_q: u64,
        #[arg(long, default_value_t = DEFAULT_R_FLOW)]
        r_flow: u32,
        #[arg(long, default_value_t = DEFAULT_PRIO_RESERVE)]
        prio_reserve: u64,
        #[arg(long, default_value_t = DEFAULT_CEIO_C_TOTAL)]
        ceio_c_total: u64,
        #[arg(long, default_value_t = 256)]
        ceio_max_fast_flows: usize,
        #[arg(long, default_value_t = 10)]
        ceio_ctrl_us: u64,
        #[arg(long, default_value_t = 15)]
        ceio_rmt_rewrite_us: u64,
        #[arg(long, default_value_t = 5000)]
        ceio_inactive_us: u64,
        #[arg(long, default_value_t = 250)]
        ceio_rr_period_us: u64,
        #[arg(long, default_value_t = 8)]
        ceio_rr_batch: usize,
        #[arg(long, default_value_t = 20)]
        ceio_phase_pause_us: u64,
        #[arg(long, default_value_t = DEFAULT_CEIO_PULL_WINDOW)]
        ceio_pull_window: u32,
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        #[arg(long, default_value = CARD_SYNC_ADDR)]
        sync: String,
        #[arg(long)]
        restore: bool,
    }

    // -------------------------------------------------------------------------
    // CN106 100% Non-Multiplexed Hardware PMU: TAD (48 MiB L3) + 6x DDR5
    // -------------------------------------------------------------------------
    #[repr(C)]
    #[derive(Default)]
    struct PerfEventAttr {
        type_: u32,
        size: u32,
        config: u64,
        sample_period: u64,
        sample_type: u64,
        read_format: u64,
        flags: u64,
        wakeup_events: u32,
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

    const PERF_FORMAT_TOTAL_TIME_ENABLED: u64 = 1 << 0;
    const PERF_FORMAT_TOTAL_TIME_RUNNING: u64 = 1 << 1;
    const PERF_ATTR_FLAG_DISABLED: u64 = 1 << 0;
    const PERF_EVENT_IOC_ENABLE: libc::c_ulong = 0x2400;
    const PERF_EVENT_IOC_DISABLE: libc::c_ulong = 0x2401;

    const TAD_EVENTS: &[(&str, u64)] = &[
        ("tad_req_msh_in_any", 0x01),
        ("tad_req_ltg_hit", 0x16),
        ("tad_req_ltg_miss", 0x17),
        ("tad_req_msh_out_dss_wr", 0x0d),
    ];

    const DDR_PMU_NAMES: &[&str] = &[
        "mrvl_ddr_pmu_87e1c0000000",
        "mrvl_ddr_pmu_87e1c1000000",
        "mrvl_ddr_pmu_87e1c2000000",
        "mrvl_ddr_pmu_87e1c3000000",
        "mrvl_ddr_pmu_87e1c4000000",
        "mrvl_ddr_pmu_87e1c5000000",
    ];
    const DDR_READS_CFG: u64 = 0x65;
    const DDR_WRITES_CFG: u64 = 0x64;

    struct CardPmu {
        tad_fds: Vec<(String, RawFd)>,
        ddr_rd_fds: Vec<(String, RawFd)>,
        ddr_wr_fds: Vec<(String, RawFd)>,
    }

    impl CardPmu {
        fn open_one(typ: u32, cfg: u64) -> Option<RawFd> {
            let attr = PerfEventAttr {
                type_: typ,
                size: size_of::<PerfEventAttr>() as u32,
                config: cfg,
                read_format: PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING,
                flags: PERF_ATTR_FLAG_DISABLED,
                ..Default::default()
            };
            let fd = unsafe {
                libc::syscall(
                    libc::SYS_perf_event_open,
                    &raw const attr,
                    -1i32 as libc::pid_t,
                    0i32 as libc::c_int,
                    -1i32 as libc::c_int,
                    0 as libc::c_ulong,
                )
            };
            if fd >= 0 {
                unsafe { libc::ioctl(fd as RawFd, PERF_EVENT_IOC_ENABLE, 0) };
                Some(fd as RawFd)
            } else {
                None
            }
        }

        fn open() -> Self {
            let mut tad_fds = Vec::new();
            if let Ok(s) = fs::read_to_string("/sys/bus/event_source/devices/tad/type") {
                if let Ok(typ) = s.trim().parse::<u32>() {
                    for &(name, cfg) in TAD_EVENTS {
                        if let Some(fd) = Self::open_one(typ, cfg) {
                            tad_fds.push((name.to_string(), fd));
                        }
                    }
                }
            }
            let mut ddr_rd_fds = Vec::new();
            let mut ddr_wr_fds = Vec::new();
            for (ch, &dev) in DDR_PMU_NAMES.iter().enumerate() {
                let p = format!("/sys/bus/event_source/devices/{dev}/type");
                if let Ok(s) = fs::read_to_string(p) {
                    if let Ok(typ) = s.trim().parse::<u32>() {
                        if let Some(fd) = Self::open_one(typ, DDR_READS_CFG) {
                            ddr_rd_fds.push((format!("ddr_ch{ch}_rd"), fd));
                        }
                        if let Some(fd) = Self::open_one(typ, DDR_WRITES_CFG) {
                            ddr_wr_fds.push((format!("ddr_ch{ch}_wr"), fd));
                        }
                    }
                }
            }
            CardPmu { tad_fds, ddr_rd_fds, ddr_wr_fds }
        }

        fn read_fd(fd: RawFd) -> (u64, u64, u64) {
            let mut buf = [0u64; 3];
            let n = unsafe {
                libc::read(fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>())
            };
            if n == size_of::<[u64; 3]>() as isize {
                (buf[0], buf[1], buf[2])
            } else {
                (0, 0, 0)
            }
        }

        fn snap_json(&self) -> String {
            let mut parts = Vec::new();
            for (name, fd) in self.tad_fds.iter().chain(self.ddr_rd_fds.iter()).chain(self.ddr_wr_fds.iter()) {
                let (val, ena, run) = Self::read_fd(*fd);
                let pct = if ena == 0 { 100.0 } else { (run as f64 / ena as f64) * 100.0 };
                parts.push(format!("\"{name}\":{{\"val\":{val},\"ena\":{ena},\"run\":{run},\"pct\":{pct:.2}}}"));
            }
            format!("{{{}}}", parts.join(","))
        }
    }

    impl Drop for CardPmu {
        fn drop(&mut self) {
            for (_, fd) in self.tad_fds.iter().chain(self.ddr_rd_fds.iter()).chain(self.ddr_wr_fds.iter()) {
                unsafe {
                    libc::ioctl(*fd, PERF_EVENT_IOC_DISABLE, 0);
                    libc::close(*fd);
                }
            }
        }
    }

    #[derive(Clone, Copy)]
    struct ClientPeer {
        tpl64: RoceTemplate,
    }

    impl ClientPeer {
        fn new(card_mac: [u8; 6], card_ip: [u8; 4], mac: [u8; 6], ip: [u8; 4], qpn: u32) -> Self {
            ClientPeer {
                tpl64: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, MIN_WIRE_PAYLOAD),
            }
        }
    }

    #[derive(Clone, Default)]
    struct CoreSnapshot {
        eth_rx_pkts: u64,
        eth_tx_pkts: u64,
        sdp_fast_tx_pkts: u64,
        sdp_slow_tx_pkts: u64,
        sdp_rx_pkts: u64,
        sdp_tx_stalls: u64,
        bad_wire: u64,
        bad_sdp: u64,
        eth_tx_drops: u64,
        sdp_tx_drops: u64,
        card_buf_drops: u64,
        probes: u64,
        bp_fifo_enq: u64,
        bp_fifo_max_depth: u64,
        slow_enq_total: u64,
        slow_drn_total: u64,
        slow_max_depth: u64,
        phase_pause_hold_pkts: u64,
        pf_sched: PerFlowSchedStats,
    }

    struct Shared {
        mode: Mode,
        n_data: usize,
        card_mac: [u8; 6],
        card_ip: [u8; 4],
        quit: AtomicBool,
        reset_gen: AtomicU64,
        reset_ack: [AtomicU64; MAX_CARD_CORES + 1],
        peers: Mutex<Vec<Option<ClientPeer>>>,
        core_snaps: Vec<Mutex<CoreSnapshot>>,
        core_hists: Vec<Mutex<FastHist>>,
        rmt: Vec<RmtRuleEntry>,
        flow_notified: Vec<AtomicU8>,
        obs_queue: Mutex<Vec<u16>>,
        host_taken_q: [AtomicU64; MAX_CARD_CORES],
        host_slow_head_q: [AtomicU64; MAX_CARD_CORES],
        epoch_synced: AtomicBool,
        ceio_ctrl_snap: Mutex<CeioControllerStats>,
        ceio_active_flows: AtomicU32,
        peer_gen: AtomicU64,
        active_epoch: AtomicU64,
        ticks_to_ns: f64,
    }

    impl Shared {
        fn new(mode: Mode, n_data: usize, card_mac: [u8; 6], card_ip: [u8; 4], ticks_to_ns: f64) -> Self {
            let mut core_snaps = Vec::with_capacity(n_data);
            let mut core_hists = Vec::with_capacity(n_data);
            for _ in 0..n_data {
                core_snaps.push(Mutex::new(CoreSnapshot::default()));
                core_hists.push(Mutex::new(FastHist::new()));
            }
            let rmt = (0..MAX_FLOWS).map(|_| RmtRuleEntry::new()).collect();
            let flow_notified = (0..MAX_FLOWS).map(|_| AtomicU8::new(0)).collect();
            Shared {
                mode,
                n_data,
                card_mac,
                card_ip,
                quit: AtomicBool::new(false),
                reset_gen: AtomicU64::new(0),
                reset_ack: [const { AtomicU64::new(0) }; MAX_CARD_CORES + 1],
                peers: Mutex::new(vec![None; MAX_PEERS]),
                core_snaps,
                core_hists,
                rmt,
                flow_notified,
                obs_queue: Mutex::new(Vec::with_capacity(1024)),
                host_taken_q: [const { AtomicU64::new(0) }; MAX_CARD_CORES],
                host_slow_head_q: [const { AtomicU64::new(0) }; MAX_CARD_CORES],
                epoch_synced: AtomicBool::new(false),
                ceio_ctrl_snap: Mutex::new(CeioControllerStats::default()),
                ceio_active_flows: AtomicU32::new(0),
                peer_gen: AtomicU64::new(0),
                active_epoch: AtomicU64::new(0),
                ticks_to_ns,
            }
        }

        fn register_peer(&self, tid: usize, mac: [u8; 6], ip: [u8; 4], qpn: u32) -> Option<ClientPeer> {
            if tid >= MAX_PEERS {
                return None;
            }
            let peer = ClientPeer::new(self.card_mac, self.card_ip, mac, ip, qpn);
            let mut guard = self.peers.lock().unwrap();
            guard[tid] = Some(peer);
            Some(peer)
        }

        fn clear_peers(&self) {
            let mut guard = self.peers.lock().unwrap();
            for p in guard.iter_mut() {
                *p = None;
            }
            self.peer_gen.fetch_add(1, Ordering::AcqRel);
        }

        #[inline(always)]
        fn notify_flow_observed(&self, fid: u16) {
            let idx = (fid as usize) & (MAX_FLOWS - 1);
            if self.flow_notified[idx].load(Ordering::Relaxed) == 0
                && self.flow_notified[idx].swap(1, Ordering::AcqRel) == 0
            {
                if let Ok(mut q) = self.obs_queue.try_lock() {
                    q.push(fid);
                } else {
                    self.flow_notified[idx].store(0, Ordering::Release);
                }
            }
        }

        fn trigger_reset(&self, total_lcores: usize, new_epoch: u64) {
            self.epoch_synced.store(false, Ordering::Release);
            self.active_epoch.store(new_epoch, Ordering::Release);
            for f in &self.flow_notified {
                f.store(0, Ordering::Relaxed);
            }
            if let Ok(mut q) = self.obs_queue.lock() {
                q.clear();
            }
            let next = self.reset_gen.fetch_add(1, Ordering::AcqRel) + 1;
            let deadline = Instant::now() + Duration::from_secs(3);
            while Instant::now() < deadline {
                let mut all_ok = true;
                for i in 0..total_lcores {
                    if self.reset_ack[i].load(Ordering::Acquire) < next {
                        all_ok = false;
                        break;
                    }
                }
                if all_ok {
                    break;
                }
                std::thread::sleep(Duration::from_millis(1));
            }
        }
    }

    #[inline(always)]
    fn parse_roce_packet(frame: &[u8]) -> Option<([u8; 6], [u8; 4], u32, &[u8])> {
        if frame.len() < ROCE_HDR_LEN + MIN_WIRE_PAYLOAD + ROCE_ICRC_LEN {
            return None;
        }
        if frame[12] != 0x08 || frame[13] != 0x00 || frame[23] != 17 {
            return None;
        }
        let dport = u16::from_be_bytes([frame[36], frame[37]]);
        if dport != ROCE_UDP_PORT || frame[42] != 0x64 {
            return None;
        }
        let udp_len = u16::from_be_bytes([frame[38], frame[39]]) as usize;
        if udp_len < 8 + 12 + 8 + MIN_WIRE_PAYLOAD + ROCE_ICRC_LEN {
            return None;
        }
        let payload_len = udp_len - 8 - 12 - 8 - ROCE_ICRC_LEN;
        let payload_end = ROCE_HDR_LEN + payload_len;
        if payload_end + ROCE_ICRC_LEN > frame.len() {
            return None;
        }
        let mut src_mac = [0u8; 6];
        src_mac.copy_from_slice(&frame[6..12]);
        let mut src_ip = [0u8; 4];
        src_ip.copy_from_slice(&frame[26..30]);
        let src_qpn = u32::from_be_bytes([0, frame[59], frame[60], frame[61]]);
        Some((src_mac, src_ip, src_qpn, &frame[ROCE_HDR_LEN..payload_end]))
    }

    #[inline(always)]
    fn build_roce_ack64<'e>(pool: &Mempool<'e>, peer: &ClientPeer, ack_payload: &[u8]) -> Option<Mbuf<'e>> {
        if ack_payload.len() != MIN_WIRE_PAYLOAD {
            return None;
        }
        let mut m = pool.alloc()?;
        let total = ROCE_HDR_LEN + MIN_WIRE_PAYLOAD + ROCE_ICRC_LEN;
        let dst = m.append(total as u16)?;
        dst[..ROCE_HDR_LEN].copy_from_slice(&peer.tpl64.hdr);
        dst[ROCE_HDR_LEN..ROCE_HDR_LEN + MIN_WIRE_PAYLOAD].copy_from_slice(ack_payload);
        let icrc = peer.tpl64.icrc(&dst[ROCE_HDR_LEN..ROCE_HDR_LEN + MIN_WIRE_PAYLOAD]);
        dst[ROCE_HDR_LEN + MIN_WIRE_PAYLOAD..total].copy_from_slice(&icrc.to_le_bytes());
        Some(m)
    }

    fn build_sdp_hdr(dst_mac: [u8; 6], src_mac: [u8; 6], lane_id: usize, payload_len: usize) -> [u8; SDP_HDR_LEN] {
        let mut hdr = [0u8; SDP_HDR_LEN];
        hdr[0..6].copy_from_slice(&dst_mac);
        hdr[6..12].copy_from_slice(&src_mac);
        hdr[12..14].copy_from_slice(&0x0800u16.to_be_bytes());
        let ip_tot = (20 + 8 + 6 + payload_len) as u16;
        hdr[14] = 0x45;
        hdr[16..18].copy_from_slice(&ip_tot.to_be_bytes());
        hdr[18..20].copy_from_slice(&0x2280u16.to_be_bytes());
        hdr[20..22].copy_from_slice(&0x4000u16.to_be_bytes());
        hdr[22] = 64;
        hdr[23] = 17;
        hdr[26..30].copy_from_slice(&[10, 10, 228, 2]);
        hdr[30..34].copy_from_slice(&[10, 10, 228, 1]);
        let sport = (5228 + lane_id as u16).to_be_bytes();
        let dport = SDP_UDP_DST_PORT.to_be_bytes();
        let ulen = (8 + 6 + payload_len) as u16;
        hdr[34..36].copy_from_slice(&sport);
        hdr[36..38].copy_from_slice(&dport);
        hdr[38..40].copy_from_slice(&ulen.to_be_bytes());
        hdr
    }

    #[inline(always)]
    fn build_sdp_mbuf<'e>(
        sdp_pool: &Mempool<'e>,
        dst_mac: [u8; 6],
        src_mac: [u8; 6],
        lane_id: usize,
        payload: &[u8],
        path_tag: u8,
        add_flags: u8,
        card_rx_tsc: u64,
    ) -> Option<Mbuf<'e>> {
        let mut m = sdp_pool.alloc()?;
        let flen = SDP_HDR_LEN + payload.len();
        let dst = m.append(flen as u16)?;
        let hdr = build_sdp_hdr(dst_mac, src_mac, lane_id, payload.len());
        dst[..SDP_HDR_LEN].copy_from_slice(&hdr);
        dst[SDP_HDR_LEN..flen].copy_from_slice(payload);
        stamp_card_meta(&mut dst[SDP_HDR_LEN..flen], path_tag, add_flags, card_rx_tsc);
        Some(m)
    }

    #[inline(always)]
    fn flush_qp_blocking<'e>(qp: &mut QueuePair<'_, 'e>, burst: &mut Burst<'e, MAX_DRAIN>, drops: &mut u64) {
        let mut retries = 0u32;
        while !burst.is_empty() {
            let sent = qp.tx(burst);
            if sent == 0 {
                retries += 1;
                if retries > 200_000 {
                    *drops += burst.len() as u64;
                    burst.clear();
                    break;
                }
                std::hint::spin_loop();
            }
        }
    }

    #[inline(always)]
    fn push_or_flush_blocking<'e>(
        qp: &mut QueuePair<'_, 'e>,
        burst: &mut Burst<'e, MAX_DRAIN>,
        pkt: Mbuf<'e>,
        drops: &mut u64,
    ) {
        if let Err(m) = burst.push(pkt) {
            flush_qp_blocking(qp, burst, drops);
            let _ = burst.push(m);
        }
    }

    struct QueuedSdpPkt<'e> {
        flow_id: u16,
        card_rx_tsc: u64,
        fast_barrier: u64,
        mbuf: Mbuf<'e>,
    }

    enum LcoreRole<'p, 'e> {
        DataWorker {
            lane: usize,
            eth_qp: QueuePair<'p, 'e>,
            sdp_fast_qp: QueuePair<'p, 'e>,
            sdp_slow_qp: Option<QueuePair<'p, 'e>>,
        },
        CeioController,
    }

    fn format_stats_json(
        shared: &Shared,
        eth_stats: &PortStats<'_, '_>,
        sdp_stats: &PortStats<'_, '_>,
        pmu: &CardPmu,
    ) -> String {
        let mut tot = CoreSnapshot::default();
        let mut h_all = FastHist::new();
        for c in 0..shared.n_data {
            let s = shared.core_snaps[c].lock().unwrap().clone();
            tot.eth_rx_pkts += s.eth_rx_pkts;
            tot.eth_tx_pkts += s.eth_tx_pkts;
            tot.sdp_fast_tx_pkts += s.sdp_fast_tx_pkts;
            tot.sdp_slow_tx_pkts += s.sdp_slow_tx_pkts;
            tot.sdp_rx_pkts += s.sdp_rx_pkts;
            tot.sdp_tx_stalls += s.sdp_tx_stalls;
            tot.bad_wire += s.bad_wire;
            tot.bad_sdp += s.bad_sdp;
            tot.eth_tx_drops += s.eth_tx_drops;
            tot.sdp_tx_drops += s.sdp_tx_drops;
            tot.card_buf_drops += s.card_buf_drops;
            tot.probes += s.probes;
            tot.bp_fifo_enq += s.bp_fifo_enq;
            tot.bp_fifo_max_depth = tot.bp_fifo_max_depth.max(s.bp_fifo_max_depth);
            tot.slow_enq_total += s.slow_enq_total;
            tot.slow_drn_total += s.slow_drn_total;
            tot.slow_max_depth = tot.slow_max_depth.max(s.slow_max_depth);
            tot.phase_pause_hold_pkts += s.phase_pause_hold_pkts;
            tot.pf_sched.direct_pushes += s.pf_sched.direct_pushes;
            tot.pf_sched.queued_rpc += s.pf_sched.queued_rpc;
            tot.pf_sched.queued_bulk += s.pf_sched.queued_bulk;
            tot.pf_sched.drained_rpc += s.pf_sched.drained_rpc;
            tot.pf_sched.drained_bulk += s.pf_sched.drained_bulk;
            tot.pf_sched.flow_credit_stalls += s.pf_sched.flow_credit_stalls;
            tot.pf_sched.q_credit_stalls += s.pf_sched.q_credit_stalls;
            tot.pf_sched.fifo_max_depth = tot.pf_sched.fifo_max_depth.max(s.pf_sched.fifo_max_depth);
            tot.pf_sched.fifo_drops += s.pf_sched.fifo_drops;
            tot.pf_sched.max_backlogged_flows = tot.pf_sched.max_backlogged_flows.max(s.pf_sched.max_backlogged_flows);
            h_all.merge_from(&shared.core_hists[c].lock().unwrap());
        }
        let est = eth_stats.stats().unwrap_or_default();
        let sst = sdp_stats.stats().unwrap_or_default();
        let ctrl = *shared.ceio_ctrl_snap.lock().unwrap();
        let act_f = shared.ceio_active_flows.load(Ordering::Relaxed);
        let pause_mean_us = if ctrl.phase_pauses > 0 {
            (ctrl.phase_pause_ticks_total as f64 / ctrl.phase_pauses as f64) * shared.ticks_to_ns / 1000.0
        } else {
            0.0
        };
        let pause_max_us = (ctrl.max_phase_pause_ticks as f64) * shared.ticks_to_ns / 1000.0;
        let pmu_json = pmu.snap_json();
        let eth_str = format!(
            "{{\"ipackets\":{},\"opackets\":{},\"ibytes\":{},\"obytes\":{},\"imissed\":{},\"ierrors\":{},\"oerrors\":{},\"rx_nombuf\":{}}}",
            est.ipackets, est.opackets, est.ibytes, est.obytes, est.imissed, est.ierrors, est.oerrors, est.rx_nombuf
        );
        let sdp_str = format!(
            "{{\"ipackets\":{},\"opackets\":{},\"ibytes\":{},\"obytes\":{},\"imissed\":{},\"ierrors\":{},\"oerrors\":{},\"rx_nombuf\":{}}}",
            sst.ipackets, sst.opackets, sst.ibytes, sst.obytes, sst.imissed, sst.ierrors, sst.oerrors, sst.rx_nombuf
        );
        format!(
            "{{\"mode\":\"{}\",\"n_data\":{},\"eth_rx_pkts\":{},\"eth_tx_pkts\":{},\"sdp_fast_tx_pkts\":{},\"sdp_slow_tx_pkts\":{},\"sdp_rx_pkts\":{},\"sdp_tx_stalls\":{},\"bad_wire\":{},\"bad_sdp\":{},\"eth_tx_drops\":{},\"sdp_tx_drops\":{},\"card_buf_drops\":{},\"probes\":{},\"bp_fifo_enq\":{},\"bp_fifo_max_depth\":{},\"slow_enq_total\":{},\"slow_drn_total\":{},\"slow_max_depth\":{},\"phase_pause_hold_pkts\":{},\"pf_sched\":{{\"direct_pushes\":{},\"queued_rpc\":{},\"queued_bulk\":{},\"drained_rpc\":{},\"drained_bulk\":{},\"flow_credit_stalls\":{},\"q_credit_stalls\":{},\"fifo_max_depth\":{},\"fifo_drops\":{},\"max_backlogged_flows\":{}}},\"ceio_ctrl\":{{\"ctrl_loops\":{},\"rmt_fast_to_slow\":{},\"rmt_slow_to_fast\":{},\"phase_pauses\":{},\"phase_pause_mean_us\":{:.2},\"phase_pause_max_us\":{:.2},\"transient_llc_leak_pkts\":{},\"credit_reallocs\":{},\"debt_events\":{},\"rr_reactivations\":{},\"slow_fallback_new_flows\":{},\"active_flows_now\":{},\"active_flows_peak\":{}}},\"card_res_p50_us\":{:.3},\"card_res_p99_us\":{:.3},\"card_res_max_us\":{:.3},\"eth_port_stats\":{},\"sdp_port_stats\":{},\"pmu\":{}}}",
            shared.mode.as_str(),
            shared.n_data,
            tot.eth_rx_pkts,
            tot.eth_tx_pkts,
            tot.sdp_fast_tx_pkts,
            tot.sdp_slow_tx_pkts,
            tot.sdp_rx_pkts,
            tot.sdp_tx_stalls,
            tot.bad_wire,
            tot.bad_sdp,
            tot.eth_tx_drops,
            tot.sdp_tx_drops,
            tot.card_buf_drops,
            tot.probes,
            tot.bp_fifo_enq,
            tot.bp_fifo_max_depth,
            tot.slow_enq_total,
            tot.slow_drn_total,
            tot.slow_max_depth,
            tot.phase_pause_hold_pkts,
            tot.pf_sched.direct_pushes,
            tot.pf_sched.queued_rpc,
            tot.pf_sched.queued_bulk,
            tot.pf_sched.drained_rpc,
            tot.pf_sched.drained_bulk,
            tot.pf_sched.flow_credit_stalls,
            tot.pf_sched.q_credit_stalls,
            tot.pf_sched.fifo_max_depth,
            tot.pf_sched.fifo_drops,
            tot.pf_sched.max_backlogged_flows,
            ctrl.ctrl_loops,
            ctrl.rmt_fast_to_slow,
            ctrl.rmt_slow_to_fast,
            ctrl.phase_pauses,
            pause_mean_us,
            pause_max_us,
            ctrl.transient_llc_leak_pkts,
            ctrl.credit_reallocs,
            ctrl.debt_events,
            ctrl.rr_reactivations,
            ctrl.slow_fallback_new_flows,
            act_f,
            ctrl.active_flows_peak,
            h_all.pct_us(0.50),
            h_all.pct_us(0.99),
            h_all.max_us(),
            eth_str,
            sdp_str,
            pmu_json
        )
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

        if args.restore {
            let changed = restore_defaults(SLOT)?;
            println!("restored: {changed:?}");
            return Ok(());
        }

        let (eal, _) = Eal::init(eal_args)?;
        let total_lcores = eal.lcores();
        let n_data = args.data_cores;
        if n_data < 2 || n_data > MAX_CARD_CORES {
            return Err(format!("data_cores must be 2..={MAX_CARD_CORES} (for ROC_NIX_TM_SDP), got {n_data}").into());
        }
        let need_lcores = if args.mode == Mode::Ceio { n_data + 1 } else { n_data };
        if total_lcores != need_lcores {
            return Err(format!(
                "mode {} with data_cores={} requires {} EAL lcores, got {}",
                args.mode.as_str(),
                n_data,
                need_lcores,
                total_lcores
            )
            .into());
        }

        let tsc_hz = eal.tsc_hz() as f64;
        let ticks_to_ns = 1e9 / tsc_hz;
        let us_to_ticks = |us: u64| -> u64 { ((us as f64) * 1e-6 * tsc_hz).round() as u64 };
        let card_pmu = Arc::new(CardPmu::open());

        // 1. Physical 100GbE `eth0` (4 RSS queues, flow control disabled before start)
        let eth_id = Port::by_name(&eal, &args.eth_dev)?;
        let eth_sock = Port::socket_of(&eal, eth_id);
        let eth_pool = Mempool::new_with_opts(
            &eal,
            "m228_eth_pool",
            args.eth_pool_size,
            256,
            ETH_MBUF_BUF_SIZE,
            eth_sock,
            None,
        )?;
        let mut eth_port = Port::start_with_opts_rss_fc(
            &eal,
            eth_id,
            &eth_pool,
            n_data as u16,
            ETH_DESC,
            ETH_DESC,
            Some(1500),
            true,
            true,
        )?;
        eth_port.promiscuous()?;
        let card_mac = eth_port.mac();
        let card_ip: [u8; 4] = [10, 10, 100, 2];

        let shared = Arc::new(Shared::new(args.mode, n_data, card_mac, card_ip, ticks_to_ns));
        {
            let sh = Arc::clone(&shared);
            ctrlc::set_handler(move || sh.quit.store(true, Ordering::Relaxed))?;
        }

        // 2. Standard `sdp1-1` (`nb_txq = 4` for native-bp/large-sdp/per-flow-cr; `8` for ceio)
        let sdp_id = Port::by_name(&eal, &args.sdp_dev)?;
        let sdp_sock = Port::socket_of(&eal, sdp_id);
        let sdp_queues = if args.mode == Mode::Ceio { (n_data * 2) as u16 } else { n_data as u16 };
        let sdp_pool = Mempool::new_with_opts(
            &eal,
            "m228_sdp_pool",
            args.sdp_pool_size,
            256,
            SDP_MBUF_BUF_SIZE,
            sdp_sock,
            None,
        )?;
        let mut sdp_port = Port::start_with_opts_rss_fc(
            &eal,
            sdp_id,
            &sdp_pool,
            sdp_queues,
            SDP_RX_DESC,
            args.sdp_tx_desc,
            Some(SDP_MTU),
            true,
            false,
        )?;
        sdp_port.promiscuous()?;
        let sdp_card_mac = sdp_port.mac();
        let sdp_host_mac: [u8; 6] = [0x02, 0x22, 0x80, 0x00, 0x00, 0x01];

        // 3. BAR4 Slot 15 lease ONLY for `per-flow-cr` and `ceio` (ZERO BAR4 for `native-bp`/`large-sdp`!)
        let use_bar4 = matches!(args.mode, Mode::PerFlowCr | Mode::Ceio);
        let bar4_mz: Option<Memzone<'_>>;
        let _bar4_win: Option<Bar4Window>;
        if use_bar4 {
            let mz = Memzone::reserve_contiguous(&eal, "m228_bar4", SLOT_BYTES, eth_sock, SLOT_BYTES as u32)?;
            unsafe {
                std::ptr::write_bytes(mz.as_ptr(), 0, SLOT_BYTES);
                mz.as_ptr().add(READY_OFFSET).cast::<u64>().write_volatile(READY);
                mz.as_ptr().add(NONCE_OFFSET).cast::<u64>().write_volatile(0);
                let meta = mz.as_ptr().add(BAR4_CARD_META_OFF).cast::<u64>();
                meta.add(0).write_volatile(n_data as u64);
                meta.add(1).write_volatile(args.mode.id());
                meta.add(2).write_volatile(args.r_eff_per_q);
                meta.add(3).write_volatile(args.r_flow as u64);
            }
            let pa = mz.phys_addr().ok_or("rte_mem_virt2phy failed on BAR4 memzone")?;
            let bw = Bar4Window::claim(SLOT, pa)?;
            bar4_mz = Some(mz);
            _bar4_win = Some(bw);
        } else {
            bar4_mz = None;
            _bar4_win = None;
        }
        let bar4_ptr_usize = bar4_mz.as_ref().map_or(0usize, |m| m.as_ptr() as usize);

        let sync_listener = TcpListener::bind(&args.sync)?;
        sync_listener.set_nonblocking(true)?;
        let lease_listener = if use_bar4 { Some(TcpListener::bind(&args.control)?) } else { None };

        let (eth_stats, eth_qps) = eth_port.split_queues();
        let _ = eth_stats.stats_reset();
        let _ = eth_stats.xstats_reset();
        let (sdp_stats, sdp_qps_raw) = sdp_port.split_queues();
        let _ = sdp_stats.stats_reset();
        let mut sdp_qps: Vec<Option<QueuePair<'_, '_>>> = sdp_qps_raw.into_iter().map(Some).collect();

        let mut roles: Vec<LcoreRole<'_, '_>> = Vec::with_capacity(total_lcores);
        for (c, eth_qp) in eth_qps.into_iter().enumerate() {
            let sdp_fast_qp = sdp_qps[c].take().unwrap();
            let sdp_slow_qp = if args.mode == Mode::Ceio { sdp_qps[c + n_data].take() } else { None };
            roles.push(LcoreRole::DataWorker { lane: c, eth_qp, sdp_fast_qp, sdp_slow_qp });
        }
        if args.mode == Mode::Ceio {
            roles.push(LcoreRole::CeioController);
        }

        let ceio_cfg = CeioControllerConfig {
            c_total: args.ceio_c_total,
            max_fast_flows: args.ceio_max_fast_flows,
            rmt_rewrite_ticks: us_to_ticks(args.ceio_rmt_rewrite_us),
            inactive_timeout_ticks: us_to_ticks(args.ceio_inactive_us),
            reactivate_period_ticks: us_to_ticks(args.ceio_rr_period_us),
            reactivate_batch: args.ceio_rr_batch,
            phase_pause_min_ticks: us_to_ticks(args.ceio_phase_pause_us),
        };
        let ceio_ctrl_ticks = us_to_ticks(args.ceio_ctrl_us);
        let init_pull_window = args.ceio_pull_window as u64;
        let r_eff_per_q = args.r_eff_per_q;
        let r_flow = args.r_flow;
        let prio_reserve = args.prio_reserve;
        let mode = args.mode;

        std::thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            let _lease_srv = if let (Some(l), Some(mz)) = (lease_listener, bar4_mz.as_ref()) {
                let nonce_ref: &AtomicU64 = unsafe { &*mz.as_ptr().add(NONCE_OFFSET).cast::<AtomicU64>() };
                Some(lease::serve(scope, l, nonce_ref)?)
            } else {
                None
            };

            let sh_srv = Arc::clone(&shared);
            let pmu_srv = Arc::clone(&card_pmu);
            let eth_stats_srv = eth_stats;
            let sdp_stats_srv = sdp_stats;
            scope.spawn(move || {
                unsafe {
                    let mut set: libc::cpu_set_t = std::mem::zeroed();
                    libc::CPU_ZERO(&mut set);
                    libc::CPU_SET(0, &mut set);
                    libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set);
                }
                while !sh_srv.quit.load(Ordering::Relaxed) {
                    match sync_listener.accept() {
                        Ok((mut stream, _)) => {
                            let _ = stream.set_nodelay(true);
                            let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
                            let mut reader = BufReader::new(stream.try_clone().unwrap());
                            let mut line = String::new();
                            if reader.read_line(&mut line).is_ok() {
                                let cmd = line.trim();
                                let resp = if cmd == "PING" {
                                    format!(
                                        "{{\"ok\":true,\"mode\":\"{}\",\"n_data\":{}}}\n",
                                        sh_srv.mode.as_str(),
                                        sh_srv.n_data
                                    )
                                } else if cmd == "RESET_PEERS" {
                                    sh_srv.clear_peers();
                                    "{\"ok\":true}\n".to_string()
                                } else if cmd == "RESET" || cmd.starts_with("RESET ") {
                                    let ep = cmd
                                        .strip_prefix("RESET ")
                                        .and_then(|s| s.trim().parse::<u64>().ok())
                                        .unwrap_or_else(|| sh_srv.active_epoch.load(Ordering::Acquire).wrapping_add(1));
                                    let _ = eth_stats_srv.stats_reset();
                                    let _ = eth_stats_srv.xstats_reset();
                                    let _ = sdp_stats_srv.stats_reset();
                                    sh_srv.trigger_reset(total_lcores, ep);
                                    format!("{{\"ok\":true,\"epoch\":{ep},\"pmu\":{}}}\n", pmu_srv.snap_json())
                                } else if cmd == "STATS" {
                                    format!("{}\n", format_stats_json(&sh_srv, &eth_stats_srv, &sdp_stats_srv, &pmu_srv))
                                } else if cmd == "QUIT" {
                                    sh_srv.quit.store(true, Ordering::Relaxed);
                                    "{\"ok\":true}\n".to_string()
                                } else {
                                    format!("{{\"ok\":false,\"err\":\"unknown cmd {cmd}\"}}\n")
                                };
                                let _ = stream.write_all(resp.as_bytes());
                            }
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(2));
                        }
                        Err(_) => {
                            std::thread::sleep(Duration::from_millis(5));
                        }
                    }
                }
            });

            println!(
                "MRV228_CARD READY mode={} n_data={} total_lcores={} sdp_tx_desc={} r_eff={} r_flow={} prio_reserve={}",
                mode.as_str(),
                n_data,
                total_lcores,
                args.sdp_tx_desc,
                r_eff_per_q,
                r_flow,
                prio_reserve
            );
            let _ = std::io::stdout().flush();

            eal.on_each_lcore(roles, |lcore_idx, role| match role {
                LcoreRole::CeioController => {
                    let mut ctrl = CeioFlowController::new(ceio_cfg);
                    ctrl.reset(&shared.rmt);
                    let mut local_reset_gen = 0u64;
                    let mut last_poll_tsc = rdtsc_after_loads();
                    let mut obs_buf: Vec<u16> = Vec::with_capacity(1024);
                    let mut snap_ctr = 0u64;
                    let bar4_ptr = bar4_ptr_usize as *const u8;

                    while !shared.quit.load(Ordering::Relaxed) {
                        let rg = shared.reset_gen.load(Ordering::Acquire);
                        if rg != local_reset_gen {
                            ctrl.reset(&shared.rmt);
                            for l in 0..MAX_CARD_CORES {
                                shared.host_taken_q[l].store(0, Ordering::Relaxed);
                                shared.host_slow_head_q[l].store(0, Ordering::Relaxed);
                            }
                            *shared.ceio_ctrl_snap.lock().unwrap() = CeioControllerStats::default();
                            shared.ceio_active_flows.store(0, Ordering::Relaxed);
                            local_reset_gen = rg;
                            shared.reset_ack[lcore_idx].store(rg, Ordering::Release);
                            last_poll_tsc = rdtsc_after_loads();
                            continue;
                        }

                        let now = rdtsc_after_loads();
                        if ceio_ctrl_ticks > 0 && now.saturating_sub(last_poll_tsc) < ceio_ctrl_ticks {
                            std::hint::spin_loop();
                            continue;
                        }
                        last_poll_tsc = now;

                        obs_buf.clear();
                        if let Ok(mut q) = shared.obs_queue.try_lock() {
                            obs_buf.append(&mut q);
                        }
                        let mut slow_head = [0u64; MAX_CARD_CORES];
                        for l in 0..MAX_CARD_CORES {
                            slow_head[l] = shared.host_slow_head_q[l].load(Ordering::Acquire);
                        }
                        let synced = shared.epoch_synced.load(Ordering::Acquire);
                        ctrl.poll_step(now, &shared.rmt, &slow_head, &obs_buf, |fid| {
                            if synced && !bar4_ptr.is_null() {
                                unsafe { read_bar4_flow_taken(bar4_ptr, fid) }
                            } else {
                                0
                            }
                        });

                        snap_ctr = snap_ctr.wrapping_add(1);
                        if (snap_ctr & 0x3f) == 0 {
                            if let Ok(mut st) = shared.ceio_ctrl_snap.try_lock() {
                                *st = ctrl.stats;
                            }
                            shared.ceio_active_flows.store(ctrl.active_flow_count() as u32, Ordering::Relaxed);
                        }
                    }
                    *shared.ceio_ctrl_snap.lock().unwrap() = ctrl.stats;
                    shared.ceio_active_flows.store(ctrl.active_flow_count() as u32, Ordering::Relaxed);
                }

                LcoreRole::DataWorker { lane, mut eth_qp, mut sdp_fast_qp, mut sdp_slow_qp } => {
                    let mut eth_rx: Burst<'_, MAX_DRAIN> = Burst::new();
                    let mut eth_tx: Burst<'_, MAX_DRAIN> = Burst::new();
                    let mut sdp_fast_tx: Burst<'_, MAX_DRAIN> = Burst::new();
                    let mut sdp_slow_tx: Burst<'_, MAX_DRAIN> = Burst::new();
                    let mut sdp_rx_a: Burst<'_, MAX_DRAIN> = Burst::new();
                    let mut sdp_rx_b: Burst<'_, MAX_DRAIN> = Burst::new();

                    let mut pf_sched: PerFlowCreditScheduler<QueuedSdpPkt<'_>> =
                        PerFlowCreditScheduler::new(lane, r_eff_per_q, r_flow, prio_reserve, 98304);
                    let mut bp_fifo: VecDeque<QueuedSdpPkt<'_>> = VecDeque::with_capacity(65536);
                    let mut slow_ring: VecDeque<QueuedSdpPkt<'_>> = VecDeque::with_capacity(65536);
                    let mut phase_hold: VecDeque<QueuedSdpPkt<'_>> = VecDeque::with_capacity(4096);
                    let mut flow_hold_cnt: Vec<u32> = vec![0u32; MAX_FLOWS];
                    let mut fast_sent_lane: u64 = 0;
                    let mut slow_sent_lane: u64 = 0;
                    let mut pull_target_lane: u64 = init_pull_window;

                    let mut peer_cache: [Option<ClientPeer>; MAX_PEERS] = [None; MAX_PEERS];
                    let mut cnt = CoreSnapshot::default();
                    let mut hist = FastHist::new();
                    let mut local_reset_gen = 0u64;
                    let mut local_epoch = 0u64;
                    let mut local_epoch_synced = false;
                    let mut local_peer_gen = shared.peer_gen.load(Ordering::Acquire);
                    let mut iter = 0u64;

                    let bar4_ptr = bar4_ptr_usize as *const u8;

                    while !shared.quit.load(Ordering::Relaxed) {
                        let pg = shared.peer_gen.load(Ordering::Acquire);
                        if pg != local_peer_gen {
                            peer_cache = [None; MAX_PEERS];
                            local_peer_gen = pg;
                        }
                        let rg = shared.reset_gen.load(Ordering::Acquire);
                        if rg != local_reset_gen {
                            while eth_qp.rx(&mut eth_rx) > 0 {
                                eth_rx.clear();
                            }
                            while sdp_fast_qp.rx(&mut sdp_rx_a) > 0 {
                                sdp_rx_a.clear();
                            }
                            if let Some(qp) = sdp_slow_qp.as_mut() {
                                while qp.rx(&mut sdp_rx_b) > 0 {
                                    sdp_rx_b.clear();
                                }
                            }
                            eth_tx.clear();
                            sdp_fast_tx.clear();
                            sdp_slow_tx.clear();
                            pf_sched.reset();
                            bp_fifo.clear();
                            slow_ring.clear();
                            phase_hold.clear();
                            flow_hold_cnt.fill(0);
                            fast_sent_lane = 0;
                            slow_sent_lane = 0;
                            pull_target_lane = init_pull_window;
                            cnt = CoreSnapshot::default();
                            hist.reset();
                            *shared.core_snaps[lane].lock().unwrap() = CoreSnapshot::default();
                            shared.core_hists[lane].lock().unwrap().reset();
                            local_epoch = shared.active_epoch.load(Ordering::Acquire);
                            local_epoch_synced = false;
                            local_reset_gen = rg;
                            shared.reset_ack[lcore_idx].store(rg, Ordering::Release);
                            continue;
                        }

                        // 1. Refresh BAR4 queue/pull lines from CN106 local L3 (zero PCIe read!)
                        if !bar4_ptr.is_null() {
                            if let Some(cl) = unsafe { HostQueueCreditLine::poll_for_epoch(bar4_ptr, local_epoch) } {
                                if !local_epoch_synced {
                                    local_epoch_synced = true;
                                    if lane == 0 {
                                        shared.epoch_synced.store(true, Ordering::Release);
                                    }
                                }
                                let t = cl.taken_q[lane];
                                if mode == Mode::PerFlowCr {
                                    pf_sched.update_q_taken(t);
                                } else if mode == Mode::Ceio {
                                    let prev = shared.host_taken_q[lane].load(Ordering::Relaxed);
                                    if t > prev {
                                        shared.host_taken_q[lane].store(t, Ordering::Release);
                                    }
                                }
                            }
                            if mode == Mode::Ceio && local_epoch_synced {
                                if let Some(pl) = unsafe { CeioSlowPullLine::poll_for_epoch(bar4_ptr, local_epoch) } {
                                    let sh = pl.slow_head_q[lane];
                                    let prev_sh = shared.host_slow_head_q[lane].load(Ordering::Relaxed);
                                    if sh > prev_sh {
                                        shared.host_slow_head_q[lane].store(sh, Ordering::Release);
                                    }
                                    let pt = pl.pull_target_q[lane] as u64;
                                    if pt > pull_target_lane {
                                        pull_target_lane = pt;
                                    }
                                }
                            }
                        }

                        // 2. Poll `eth0` RX queue `lane`
                        let n_rx = eth_qp.rx(&mut eth_rx);
                        if n_rx > 0 {
                            let now = rdtsc_after_loads();
                            eth_rx.drain_into(|pkt| {
                                let frame = pkt.data();
                                let Some((src_mac, src_ip, src_qpn, pay)) = parse_roce_packet(frame) else {
                                    cnt.bad_wire += 1;
                                    return;
                                };
                                if pay.len() < MIN_WIRE_PAYLOAD || pay.len() > MAX_WIRE_PAYLOAD {
                                    cnt.bad_wire += 1;
                                    return;
                                }
                                let w0 = u64::from_le_bytes(pay[0..8].try_into().unwrap());
                                let w_end = u64::from_le_bytes(pay[pay.len() - 8..pay.len()].try_into().unwrap());
                                if w0 != w_end {
                                    cnt.bad_wire += 1;
                                    return;
                                }
                                let Some(meta) = unpack_meta(w0) else {
                                    cnt.bad_wire += 1;
                                    return;
                                };
                                let tid = ((meta.global_seq >> 34) & 0xf) as usize;
                                if tid < MAX_PEERS && peer_cache[tid].is_none() {
                                    if let Some(p) = shared.register_peer(tid, src_mac, src_ip, src_qpn) {
                                        peer_cache[tid] = Some(p);
                                    }
                                }

                                if meta.op == OP_PROBE {
                                    cnt.probes += 1;
                                    let p = shared.register_peer(tid, src_mac, src_ip, src_qpn);
                                    if tid < MAX_PEERS {
                                        peer_cache[tid] = p;
                                    }
                                    if let Some(peer) = p {
                                        let mut ack = [0u8; MIN_WIRE_PAYLOAD];
                                        let p_w0 = pack_meta(meta.global_seq, MIN_WIRE_PAYLOAD, 0, PATH_DIRECT_SDP, OP_PROBE, 0);
                                        ack[0..8].copy_from_slice(&p_w0.to_le_bytes());
                                        ack[8..16].copy_from_slice(&(lane as u64).to_le_bytes());
                                        ack[16..24].copy_from_slice(&probe_cookie(meta.global_seq).to_le_bytes());
                                        ack[56..64].copy_from_slice(&p_w0.to_le_bytes());
                                        if let Some(m) = build_roce_ack64(&eth_pool, &peer, &ack) {
                                            push_or_flush_blocking(&mut eth_qp, &mut eth_tx, m, &mut cnt.eth_tx_drops);
                                            cnt.eth_tx_pkts += 1;
                                        }
                                    }
                                    return;
                                }

                                if meta.op != OP_DATA {
                                    cnt.bad_wire += 1;
                                    return;
                                }
                                cnt.eth_rx_pkts += 1;
                                let (flow_id, _flow_seq) =
                                    unpack_flow_meta(u64::from_le_bytes(pay[16..24].try_into().unwrap()));

                                match mode {
                                    Mode::LargeSdp => {
                                        if let Some(m) = build_sdp_mbuf(
                                            &sdp_pool,
                                            sdp_host_mac,
                                            sdp_card_mac,
                                            lane,
                                            pay,
                                            PATH_DIRECT_SDP,
                                            0,
                                            now,
                                        ) {
                                            push_or_flush_blocking(&mut sdp_fast_qp, &mut sdp_fast_tx, m, &mut cnt.sdp_tx_drops);
                                            cnt.sdp_fast_tx_pkts += 1;
                                        } else {
                                            cnt.card_buf_drops += 1;
                                        }
                                    }

                                    Mode::NativeBp => {
                                        if let Some(m) = build_sdp_mbuf(
                                            &sdp_pool,
                                            sdp_host_mac,
                                            sdp_card_mac,
                                            lane,
                                            pay,
                                            PATH_DIRECT_SDP,
                                            0,
                                            now,
                                        ) {
                                            if bp_fifo.is_empty() {
                                                if let Err(m_back) = sdp_fast_tx.push(m) {
                                                    let sent = sdp_fast_qp.tx(&mut sdp_fast_tx);
                                                    cnt.sdp_fast_tx_pkts += sent as u64;
                                                    if sent == 0 {
                                                        cnt.sdp_tx_stalls += 1;
                                                    }
                                                    if let Err(m_still) = sdp_fast_tx.push(m_back) {
                                                        bp_fifo.push_back(QueuedSdpPkt {
                                                            flow_id,
                                                            card_rx_tsc: now,
                                                            fast_barrier: 0,
                                                            mbuf: m_still,
                                                        });
                                                        cnt.bp_fifo_enq += 1;
                                                        let d = bp_fifo.len() as u64;
                                                        if d > cnt.bp_fifo_max_depth {
                                                            cnt.bp_fifo_max_depth = d;
                                                        }
                                                    }
                                                }
                                            } else {
                                                bp_fifo.push_back(QueuedSdpPkt {
                                                    flow_id,
                                                    card_rx_tsc: now,
                                                    fast_barrier: 0,
                                                    mbuf: m,
                                                });
                                                cnt.bp_fifo_enq += 1;
                                                let d = bp_fifo.len() as u64;
                                                if d > cnt.bp_fifo_max_depth {
                                                    cnt.bp_fifo_max_depth = d;
                                                }
                                            }
                                        } else {
                                            cnt.card_buf_drops += 1;
                                        }
                                    }

                                    Mode::PerFlowCr => {
                                        let is_rpc = meta.pkt_class == CLASS_RPC_HI;
                                        if let Some(m) = build_sdp_mbuf(
                                            &sdp_pool,
                                            sdp_host_mac,
                                            sdp_card_mac,
                                            lane,
                                            pay,
                                            PATH_PER_FLOW_CR,
                                            0,
                                            now,
                                        ) {
                                            let qp_pkt = QueuedSdpPkt {
                                                flow_id,
                                                card_rx_tsc: now,
                                                fast_barrier: 0,
                                                mbuf: m,
                                            };
                                            let synced = local_epoch_synced;
                                            if let Some(ready) = pf_sched.admit(flow_id, qp_pkt, is_rpc, |f| {
                                                if synced && !bar4_ptr.is_null() {
                                                    unsafe { read_bar4_flow_taken(bar4_ptr, f) }
                                                } else {
                                                    0
                                                }
                                            }) {
                                                hist.record_ns(0);
                                                push_or_flush_blocking(
                                                    &mut sdp_fast_qp,
                                                    &mut sdp_fast_tx,
                                                    ready.mbuf,
                                                    &mut cnt.sdp_tx_drops,
                                                );
                                                cnt.sdp_fast_tx_pkts += 1;
                                            }
                                        } else {
                                            cnt.card_buf_drops += 1;
                                        }
                                    }

                                    Mode::Ceio => {
                                        let fidx = (flow_id as usize) & (MAX_FLOWS - 1);
                                        shared.notify_flow_observed(flow_id);
                                        let entry = &shared.rmt[fidx];
                                        entry.lane.store(lane as u32, Ordering::Relaxed);
                                        entry.last_seen_tsc.store(now, Ordering::Relaxed);

                                        let act = entry.action.load(Ordering::Acquire);
                                        let has_slow_backlog =
                                            entry.slow_enq.load(Ordering::Relaxed) > entry.slow_drn.load(Ordering::Relaxed);
                                        let slow_in_flight = shared.host_slow_head_q[lane].load(Ordering::Acquire)
                                            < entry.last_slow_lane_seq.load(Ordering::Acquire);
                                        let has_hold = flow_hold_cnt[fidx] > 0;
                                        if act == RMT_ACT_PHASE_PAUSE || has_hold {
                                            if let Some(m) = build_sdp_mbuf(
                                                &sdp_pool,
                                                sdp_host_mac,
                                                sdp_card_mac,
                                                lane,
                                                pay,
                                                PATH_CEIO_FAST,
                                                FLAG_PHASE_PAUSED,
                                                now,
                                            ) {
                                                if act == RMT_ACT_PHASE_PAUSE {
                                                    cnt.phase_pause_hold_pkts += 1;
                                                }
                                                flow_hold_cnt[fidx] += 1;
                                                phase_hold.push_back(QueuedSdpPkt {
                                                    flow_id,
                                                    card_rx_tsc: now,
                                                    fast_barrier: 0,
                                                    mbuf: m,
                                                });
                                            } else {
                                                cnt.card_buf_drops += 1;
                                            }
                                        } else if act == RMT_ACT_FAST && !has_slow_backlog && !slow_in_flight {
                                            let leak_flag = if entry.credit_exhausted_tsc.load(Ordering::Relaxed) != 0 {
                                                FLAG_CEIO_LLC_LEAK
                                            } else {
                                                0
                                            };
                                            entry.fast_hits.fetch_add(1, Ordering::Relaxed);
                                            if let Some(m) = build_sdp_mbuf(
                                                &sdp_pool,
                                                sdp_host_mac,
                                                sdp_card_mac,
                                                lane,
                                                pay,
                                                PATH_CEIO_FAST,
                                                leak_flag,
                                                now,
                                            ) {
                                                hist.record_ns(0);
                                                push_or_flush_blocking(
                                                    &mut sdp_fast_qp,
                                                    &mut sdp_fast_tx,
                                                    m,
                                                    &mut cnt.sdp_tx_drops,
                                                );
                                                fast_sent_lane += 1;
                                                entry.last_fast_lane_seq.store(fast_sent_lane, Ordering::Release);
                                                cnt.sdp_fast_tx_pkts += 1;
                                            } else {
                                                cnt.card_buf_drops += 1;
                                            }
                                        } else if let Some(m) = build_sdp_mbuf(
                                            &sdp_pool,
                                            sdp_host_mac,
                                            sdp_card_mac,
                                            lane,
                                            pay,
                                            PATH_CEIO_SLOW,
                                            FLAG_SLOW_HINT,
                                            now,
                                        ) {
                                            let fb = entry.last_fast_lane_seq.load(Ordering::Acquire);
                                            entry.slow_enq.fetch_add(1, Ordering::Release);
                                            slow_ring.push_back(QueuedSdpPkt {
                                                flow_id,
                                                card_rx_tsc: now,
                                                fast_barrier: fb,
                                                mbuf: m,
                                            });
                                            cnt.slow_enq_total += 1;
                                            let d = slow_ring.len() as u64;
                                            if d > cnt.slow_max_depth {
                                                cnt.slow_max_depth = d;
                                            }
                                        } else {
                                            cnt.card_buf_drops += 1;
                                        }
                                    }
                                }
                            });
                        }

                        // 3. Mode-specific non-blocking FIFO drain + outbound SDP TX
                        match mode {
                            Mode::NativeBp => {
                                if !sdp_fast_tx.is_empty() {
                                    let sent = sdp_fast_qp.tx(&mut sdp_fast_tx);
                                    cnt.sdp_fast_tx_pkts += sent as u64;
                                    if sent == 0 {
                                        cnt.sdp_tx_stalls += 1;
                                    }
                                }
                                if !bp_fifo.is_empty() && !sdp_fast_tx.is_full() {
                                    let now = rdtsc_after_loads();
                                    while !sdp_fast_tx.is_full() {
                                        let Some(qp_pkt) = bp_fifo.pop_front() else { break };
                                        let res_ns = ((now.saturating_sub(qp_pkt.card_rx_tsc)) as f64 * ticks_to_ns) as u64;
                                        hist.record_ns(res_ns);
                                        let _ = sdp_fast_tx.push(qp_pkt.mbuf);
                                    }
                                    let sent = sdp_fast_qp.tx(&mut sdp_fast_tx);
                                    cnt.sdp_fast_tx_pkts += sent as u64;
                                    if sent == 0 && !sdp_fast_tx.is_empty() {
                                        cnt.sdp_tx_stalls += 1;
                                    }
                                }
                            }

                            Mode::LargeSdp => {
                                if !sdp_fast_tx.is_empty() {
                                    flush_qp_blocking(&mut sdp_fast_qp, &mut sdp_fast_tx, &mut cnt.sdp_tx_drops);
                                }
                            }

                            Mode::PerFlowCr => {
                                if pf_sched.total_queued() > 0 {
                                    if !bar4_ptr.is_null() && !pf_sched.can_push_q(false) {
                                        if let Some(cl) = unsafe { HostQueueCreditLine::poll_for_epoch(bar4_ptr, local_epoch) } {
                                            pf_sched.update_q_taken(cl.taken_q[lane]);
                                        }
                                    }
                                    let now = rdtsc_after_loads();
                                    let synced = local_epoch_synced;
                                    let mut drained = 0usize;
                                    while drained < MAX_DRAIN {
                                        if sdp_fast_tx.is_full() {
                                            flush_qp_blocking(&mut sdp_fast_qp, &mut sdp_fast_tx, &mut cnt.sdp_tx_drops);
                                        }
                                        let Some(pkt) = pf_sched.pop_authorized(|f| {
                                            if synced && !bar4_ptr.is_null() {
                                                unsafe { read_bar4_flow_taken(bar4_ptr, f) }
                                            } else {
                                                0
                                            }
                                        }) else {
                                            break;
                                        };
                                        let res_ns = ((now.saturating_sub(pkt.card_rx_tsc)) as f64 * ticks_to_ns) as u64;
                                        hist.record_ns(res_ns);
                                        let _ = sdp_fast_tx.push(pkt.mbuf);
                                        cnt.sdp_fast_tx_pkts += 1;
                                        drained += 1;
                                    }
                                }
                                if !sdp_fast_tx.is_empty() {
                                    flush_qp_blocking(&mut sdp_fast_qp, &mut sdp_fast_tx, &mut cnt.sdp_tx_drops);
                                }
                            }

                            Mode::Ceio => {
                                if !slow_ring.is_empty() {
                                    let now = rdtsc_after_loads();
                                    let mut drained = 0usize;
                                    let host_fast_done = shared.host_taken_q[lane].load(Ordering::Acquire);
                                    while drained < MAX_DRAIN && slow_sent_lane < pull_target_lane {
                                        if let Some(front) = slow_ring.front() {
                                            if host_fast_done < front.fast_barrier {
                                                break;
                                            }
                                        }
                                        if sdp_slow_tx.is_full() {
                                            flush_qp_blocking(
                                                sdp_slow_qp.as_mut().unwrap(),
                                                &mut sdp_slow_tx,
                                                &mut cnt.sdp_tx_drops,
                                            );
                                        }
                                        let Some(pkt) = slow_ring.pop_front() else { break };
                                        let fidx = (pkt.flow_id as usize) & (MAX_FLOWS - 1);
                                        slow_sent_lane += 1;
                                        shared.rmt[fidx].last_slow_lane_seq.store(slow_sent_lane, Ordering::Release);
                                        shared.rmt[fidx].slow_drn.fetch_add(1, Ordering::Release);
                                        let res_ns = ((now.saturating_sub(pkt.card_rx_tsc)) as f64 * ticks_to_ns) as u64;
                                        hist.record_ns(res_ns);
                                        let _ = sdp_slow_tx.push(pkt.mbuf);
                                        cnt.sdp_slow_tx_pkts += 1;
                                        cnt.slow_drn_total += 1;
                                        drained += 1;
                                    }
                                }
                                if !phase_hold.is_empty() {
                                    let now = rdtsc_after_loads();
                                    while let Some(front) = phase_hold.front() {
                                        let fidx = (front.flow_id as usize) & (MAX_FLOWS - 1);
                                        let entry = &shared.rmt[fidx];
                                        let act = entry.action.load(Ordering::Acquire);
                                        let slow_clear = entry.slow_enq.load(Ordering::Relaxed)
                                            <= entry.slow_drn.load(Ordering::Relaxed)
                                            && shared.host_slow_head_q[lane].load(Ordering::Acquire)
                                                >= entry.last_slow_lane_seq.load(Ordering::Acquire);
                                        if act == RMT_ACT_FAST && slow_clear {
                                            let pkt = phase_hold.pop_front().unwrap();
                                            flow_hold_cnt[fidx] = flow_hold_cnt[fidx].saturating_sub(1);
                                            entry.fast_hits.fetch_add(1, Ordering::Relaxed);
                                            let res_ns = ((now.saturating_sub(pkt.card_rx_tsc)) as f64 * ticks_to_ns) as u64;
                                            hist.record_ns(res_ns);
                                            push_or_flush_blocking(
                                                &mut sdp_fast_qp,
                                                &mut sdp_fast_tx,
                                                pkt.mbuf,
                                                &mut cnt.sdp_tx_drops,
                                            );
                                            fast_sent_lane += 1;
                                            entry.last_fast_lane_seq.store(fast_sent_lane, Ordering::Release);
                                            cnt.sdp_fast_tx_pkts += 1;
                                        } else if act == RMT_ACT_SLOW {
                                            let mut pkt = phase_hold.pop_front().unwrap();
                                            flow_hold_cnt[fidx] = flow_hold_cnt[fidx].saturating_sub(1);
                                            let flen = pkt.mbuf.data_len();
                                            if flen >= SDP_HDR_LEN + MIN_WIRE_PAYLOAD {
                                                let frame = pkt.mbuf.data_mut();
                                                stamp_card_meta(
                                                    &mut frame[SDP_HDR_LEN..flen],
                                                    PATH_CEIO_SLOW,
                                                    FLAG_SLOW_HINT,
                                                    pkt.card_rx_tsc,
                                                );
                                            }
                                            let fb = entry.last_fast_lane_seq.load(Ordering::Acquire);
                                            entry.slow_enq.fetch_add(1, Ordering::Release);
                                            slow_ring.push_back(QueuedSdpPkt {
                                                flow_id: pkt.flow_id,
                                                card_rx_tsc: pkt.card_rx_tsc,
                                                fast_barrier: fb,
                                                mbuf: pkt.mbuf,
                                            });
                                            cnt.slow_enq_total += 1;
                                        } else {
                                            break;
                                        }
                                    }
                                }
                                if !sdp_fast_tx.is_empty() {
                                    flush_qp_blocking(&mut sdp_fast_qp, &mut sdp_fast_tx, &mut cnt.sdp_tx_drops);
                                }
                                if !sdp_slow_tx.is_empty() {
                                    flush_qp_blocking(
                                        sdp_slow_qp.as_mut().unwrap(),
                                        &mut sdp_slow_tx,
                                        &mut cnt.sdp_tx_drops,
                                    );
                                }
                            }
                        }

                        // 4. Poll `sdp1-1` RX for host 64 B ACKs and forward out `eth0` TX
                        let mut forward_sdp_rx = |burst: &mut Burst<'_, MAX_DRAIN>| {
                            burst.drain_into(|pkt| {
                                let f = pkt.data();
                                if f.len() < SDP_HDR_LEN + MIN_WIRE_PAYLOAD {
                                    cnt.bad_sdp += 1;
                                    return;
                                }
                                let pay = &f[SDP_HDR_LEN..SDP_HDR_LEN + MIN_WIRE_PAYLOAD];
                                let w0 = u64::from_le_bytes(pay[0..8].try_into().unwrap());
                                let w_end = u64::from_le_bytes(pay[56..64].try_into().unwrap());
                                if w0 != w_end {
                                    cnt.bad_sdp += 1;
                                    return;
                                }
                                let Some(meta) = unpack_meta(w0) else {
                                    cnt.bad_sdp += 1;
                                    return;
                                };
                                if meta.op != OP_ACK {
                                    cnt.bad_sdp += 1;
                                    return;
                                }
                                cnt.sdp_rx_pkts += 1;
                                let tid = ((meta.global_seq >> 34) & 0xf) as usize;
                                let mut peer_opt = if tid < MAX_PEERS { peer_cache[tid] } else { None };
                                if peer_opt.is_none() && tid < MAX_PEERS {
                                    let g = shared.peers.lock().unwrap();
                                    peer_opt = g[tid];
                                    peer_cache[tid] = peer_opt;
                                }
                                if let Some(peer) = peer_opt {
                                    if let Some(m) = build_roce_ack64(&eth_pool, &peer, pay) {
                                        push_or_flush_blocking(&mut eth_qp, &mut eth_tx, m, &mut cnt.eth_tx_drops);
                                        cnt.eth_tx_pkts += 1;
                                    }
                                }
                            });
                        };

                        if sdp_fast_qp.rx(&mut sdp_rx_a) > 0 {
                            forward_sdp_rx(&mut sdp_rx_a);
                        }
                        if let Some(qp) = sdp_slow_qp.as_mut() {
                            if qp.rx(&mut sdp_rx_b) > 0 {
                                forward_sdp_rx(&mut sdp_rx_b);
                            }
                        }
                        if !eth_tx.is_empty() {
                            flush_qp_blocking(&mut eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                        }

                        iter = iter.wrapping_add(1);
                        if (iter & 0xfff) == 0 {
                            cnt.pf_sched = pf_sched.stats;
                            if let Ok(mut g) = shared.core_snaps[lane].try_lock() {
                                *g = cnt.clone();
                            }
                            if let Ok(mut hg) = shared.core_hists[lane].try_lock() {
                                *hg = FastHist::new();
                                hg.merge_from(&hist);
                            }
                        }
                    }

                    cnt.pf_sched = pf_sched.stats;
                    *shared.core_snaps[lane].lock().unwrap() = cnt;
                    let mut hg = shared.core_hists[lane].lock().unwrap();
                    *hg = FastHist::new();
                    hg.merge_from(&hist);
                }
            });

            Ok(())
        })?;

        Ok(())
    }
}
