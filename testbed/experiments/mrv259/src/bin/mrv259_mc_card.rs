//! Multi-core CN106 100GbE SmartNIC bridge (`eth0 4RxQ/4TxQ` RSS) for MRV-206
//! (Dual-host 100GbE Real Upstream Memcached 1.6.45 Benchmark).
//!
//! Payload-agnostic transport bridge carrying genuine Memcached Binary UDP datagrams
//! across:
//!   - `--mode card-echo`: pure card-side echo (network/NIC baseline)
//!   - `--mode sdp`      : 4-core `eth0` <-> 4-queue `sdp1-1` (MTU 9000, 48 B aligned
//!                         header matching `rx_poll_tail_off=48`) <-> 1-core `saturn`
//!   - `--mode direct`   : 4-core `eth0` <-> DirectPush (`<= 192 B` Memcached UDP via
//!                         1..4 x 64 B PEM0 WC / BAR4 MOVDIR64B cells; `> 192 B` via
//!                         4x exclusive bidirectional DPI DMA VFs) <-> 1-core `saturn`
//!   - `--rx-mode default | cqe-peek | cqe-direct-safe`: selects NIX0 `eth0` RX polling mode
//!                         uniformly on every card core (fairly available to both `sdp` and `direct`).

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv206_card runs on the aarch64 CN106 card");
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
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{
        Burst, CqePeekStats, Eal, EthStats, HostDma, Mbuf, Mempool, Memzone, PcieFunction, Port,
        PortStats, QueuePair, rdtsc_after_loads,
    };
    use testbed::inline::{NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{
        Bar4Window, OUTBOUND_REGION_BYTES, OutboundWindow, restore_defaults, store64_gathered,
    };
    use mrv206::{
        BAR4_CELL_STRIDE_DEFAULT, BAR4_TOTAL_RING_BYTES, CTRL_BAR4_STRIDE, CTRL_CARD_ACK_GEN,
        CTRL_CARD_CORES, CTRL_CREDIT_HYST, CTRL_HOST_READY_GEN, CTRL_RX_ZONE_IOVA, CellPoll,
        ETH_MBUF_BUF_SIZE, FastHist, LARGE_SLOT_MASK, MAX_CARD_CORES, MAX_DRAIN, MAX_FRAG_PAYLOAD,
        MAX_MC_BYTES, MAX_RPC_BYTES, MAX_SEG_BYTES, MC_MAGIC_REQ, MC_UDP_MAX_PAYLOAD, MC_UDP_PORT,
        McCell, OP_DMA, OP_FRAG, OP_NET, OP_PROBE_WIRE, OP_PUSH, OP_PUSH_MAX_MC_BYTES,
        ROCE_HDR_LEN, ROCE_ICRC_LEN, ROCE_UDP_PORT, RX_ZONE_BYTES, RoceTemplate, SDP_HDR_LEN,
        SDP_MBUF_BUF_SIZE, SDP_MTU, SDP_UDP_DST_PORT, SYNC_ADDR, bar4_cell_offset,
        credit_line_offset, large_rx_offset, mc_udp_opaque, pack_cell_meta, pack_wire_meta,
        probe_cookie, push_cells_for_mc_len, roce_frag_cnt, rpc_seq_from_opaque, rx_ring_offset,
        unpack_cell_meta, unpack_wire_meta, wire_frag_cnt, wire_total_bytes,
    };

    const ETH_DESC: u16 = 2048;
    const SDP_DESC: u16 = 2048;
    const DMA_DESC: u16 = 4096;
    const REASM_SLOTS: usize = 2048;
    const REASM_MASK: usize = REASM_SLOTS - 1;
    const STAGE_SLOTS: usize = 256;
    const STAGE_MASK: usize = STAGE_SLOTS - 1;
    const TIMING_MASK: usize = 4095;
    const VCHAN_TO_HOST: u16 = 0;
    const VCHAN_FROM_HOST: u16 = 1;
    const MAX_PEERS: usize = 8;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Mode {
        CardEcho,
        Sdp,
        Direct,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum RxMode {
        Default,
        CqePeek,
        CqeDirectSafe,
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = Mode::Direct)]
        mode: Mode,
        #[arg(long, value_enum, default_value_t = RxMode::CqePeek)]
        rx_mode: RxMode,
        #[arg(long, default_value_t = 4096)]
        watchdog_interval: u64,
        #[arg(long, default_value = "0002:02:00.0")]
        eth_dev: String,
        #[arg(long, default_value = "0002:01:00.2")]
        sdp_dev: String,
        #[arg(long)]
        no_sdp_rss: bool,
        #[arg(long, default_value = "0000:06:00.1,0000:06:00.2,0000:06:00.3,0000:06:00.4")]
        dma_devs: String,
        #[arg(long)]
        no_wc: bool,
        /// Map BAR4 response ring as standard Linux MT_NORMAL_NC (0x44, enable_custom_mair=0)
        /// via /dev/bar4_mair and poll with zero-lookahead 1-pass 2x ldp q (u1_ldpq).
        #[arg(long)]
        bar4_nc: bool,
        #[arg(long, default_value = "/dev/bar4_mair")]
        bar4_nc_dev: String,
        #[arg(long, default_value_t = 0)]
        host_pf: u8,
        #[arg(long, default_value_t = 1)]
        host_vf: u16,
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        #[arg(long, default_value = SYNC_ADDR)]
        sync: String,
        #[arg(long)]
        restore: bool,
    }

    // -------------------------------------------------------------------------
    // CN106 TAD PMU (100% non-multiplexed)
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
        ("tad_snp_sent", 0x2b),
        ("tad_snp_msh_out_exlmn", 0x3b),
    ];

    struct TadPmu {
        fds: Vec<(String, RawFd)>,
    }

    impl TadPmu {
        fn open() -> Result<Self, Box<dyn Error>> {
            let mut fds = Vec::new();
            let typ_str = match fs::read_to_string("/sys/bus/event_source/devices/tad/type") {
                Ok(s) => s,
                Err(_) => return Ok(TadPmu { fds }),
            };
            let typ: u32 = typ_str.trim().parse()?;
            for &(name, cfg) in TAD_EVENTS {
                let attr = PerfEventAttr {
                    type_: typ,
                    size: std::mem::size_of::<PerfEventAttr>() as u32,
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
                    fds.push((name.to_string(), fd as RawFd));
                }
            }
            Ok(TadPmu { fds })
        }

        fn snap_json(&self) -> String {
            let mut parts = Vec::with_capacity(self.fds.len());
            for (name, fd) in &self.fds {
                let mut buf = [0u64; 3];
                let n = unsafe {
                    libc::read(*fd, buf.as_mut_ptr().cast::<libc::c_void>(), std::mem::size_of::<[u64; 3]>())
                };
                if n == std::mem::size_of::<[u64; 3]>() as isize {
                    let pct = if buf[1] == 0 { 100.0 } else { (buf[2] as f64 / buf[1] as f64) * 100.0 };
                    parts.push(format!("\"{name}\":{{\"val\":{},\"pct\":{:.2}}}", buf[0], pct));
                }
            }
            format!("{{{}}}", parts.join(","))
        }
    }

    impl Drop for TadPmu {
        fn drop(&mut self) {
            for (_, fd) in &self.fds {
                unsafe {
                    libc::ioctl(*fd, PERF_EVENT_IOC_DISABLE, 0);
                    libc::close(*fd);
                }
            }
        }
    }

    // -------------------------------------------------------------------------
    // Per-core stats and peer tables
    // -------------------------------------------------------------------------
    #[derive(Clone, Default)]
    struct CoreSnapshot {
        eth_rx_pkts: u64,
        eth_tx_pkts: u64,
        rpcs_in: u64,
        rpcs_out: u64,
        small_in: u64,
        large_in: u64,
        small_out: u64,
        large_out: u64,
        bad_wire: u64,
        bad_host: u64,
        torn_bar4: u64,
        dma_failed: u64,
        eth_tx_drops: u64,
        sdp_tx_drops: u64,
        probes: u64,
        cqe: CqePeekStats,
    }

    impl CoreSnapshot {
        fn add_assign(&mut self, o: &CoreSnapshot) {
            self.eth_rx_pkts += o.eth_rx_pkts;
            self.eth_tx_pkts += o.eth_tx_pkts;
            self.rpcs_in += o.rpcs_in;
            self.rpcs_out += o.rpcs_out;
            self.small_in += o.small_in;
            self.large_in += o.large_in;
            self.small_out += o.small_out;
            self.large_out += o.large_out;
            self.bad_wire += o.bad_wire;
            self.bad_host += o.bad_host;
            self.torn_bar4 += o.torn_bar4;
            self.dma_failed += o.dma_failed;
            self.eth_tx_drops += o.eth_tx_drops;
            self.sdp_tx_drops += o.sdp_tx_drops;
            self.probes += o.probes;
            self.cqe.peek_empty += o.cqe.peek_empty;
            self.cqe.peek_hit += o.cqe.peek_hit;
            self.cqe.ldadda_calls += o.cqe.ldadda_calls;
            self.cqe.pkts_popped += o.cqe.pkts_popped;
            self.cqe.w9_wait_retries += o.cqe.w9_wait_retries;
            self.cqe.watchdog_ldadda += o.cqe.watchdog_ldadda;
            self.cqe.watchdog_head_mismatch += o.cqe.watchdog_head_mismatch;
            self.cqe.err_pkts_freed += o.cqe.err_pkts_freed;
            self.cqe.mseg_pkts += o.cqe.mseg_pkts;
            self.cqe.fallback_ldadda += o.cqe.fallback_ldadda;
        }
    }

    /// Precomputed RoCEv2 UD templates for hot standard Memcached UDP sizes (`32, 44, 64, 164, 1024 B`),
    /// with automatic on-demand template fallback for any other 4-byte-aligned payload length.
    #[derive(Clone, Copy)]
    struct RocePeer {
        mac: [u8; 6],
        ip: [u8; 4],
        qpn: u32,
        card_mac: [u8; 6],
        card_ip: [u8; 4],
        tpl_32: RoceTemplate,
        tpl_44: RoceTemplate,
        tpl_64: RoceTemplate,
        tpl_164: RoceTemplate,
        tpl_1024: RoceTemplate,
    }

    impl RocePeer {
        fn new(card_mac: [u8; 6], card_ip: [u8; 4], mac: [u8; 6], ip: [u8; 4], qpn: u32) -> Self {
            RocePeer {
                mac,
                ip,
                qpn,
                card_mac,
                card_ip,
                tpl_32: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 32),
                tpl_44: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 44),
                tpl_64: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 64),
                tpl_164: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 164),
                tpl_1024: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 1024),
            }
        }

        #[inline(always)]
        fn template_for(&self, payload_len: usize) -> RoceTemplate {
            match payload_len {
                32 => self.tpl_32,
                44 => self.tpl_44,
                64 => self.tpl_64,
                164 => self.tpl_164,
                1024 => self.tpl_1024,
                _ => RoceTemplate::new(self.card_mac, self.mac, self.card_ip, self.ip, self.qpn, payload_len),
            }
        }
    }

    /// Standard IPv4/UDP port 11211 Memcached peer (direct raw Ethernet/IPv4/UDP interoperability).
    #[derive(Clone, Copy)]
    struct StdUdpPeer {
        mac: [u8; 6],
        ip: [u8; 4],
        sport: u16,
        card_mac: [u8; 6],
        card_ip: [u8; 4],
    }

    #[derive(Clone, Copy)]
    enum ClientPeer {
        Roce(RocePeer),
        StdUdp(StdUdpPeer),
    }

    struct Shared {
        n_cores: usize,
        quit: AtomicBool,
        cmd_word: AtomicU64,
        done_cnt: AtomicU64,
        cmd_lock: Mutex<()>,
        per_core_snap: Mutex<Vec<CoreSnapshot>>,
        per_core_hist: Mutex<Vec<FastHist>>,
        peers: Mutex<[Option<ClientPeer>; MAX_PEERS]>,
        peer_gen: AtomicU64,
        host_gen: AtomicU64,
        host_retarget_done: AtomicU64,
        host_rx_iova: AtomicU64,
        credit_hyst: AtomicU64,
        bar4_stride: AtomicU64,
        card_mac: [u8; 6],
        card_ip: [u8; 4],
    }

    impl Shared {
        fn new(n_cores: usize, card_mac: [u8; 6], card_ip: [u8; 4]) -> Self {
            Shared {
                n_cores,
                quit: AtomicBool::new(false),
                cmd_word: AtomicU64::new(0),
                done_cnt: AtomicU64::new(0),
                cmd_lock: Mutex::new(()),
                per_core_snap: Mutex::new(vec![CoreSnapshot::default(); n_cores]),
                per_core_hist: Mutex::new((0..n_cores).map(|_| FastHist::new()).collect()),
                peers: Mutex::new([None; MAX_PEERS]),
                peer_gen: AtomicU64::new(0),
                host_gen: AtomicU64::new(0),
                host_retarget_done: AtomicU64::new(0),
                host_rx_iova: AtomicU64::new(0),
                credit_hyst: AtomicU64::new(8),
                bar4_stride: AtomicU64::new(BAR4_CELL_STRIDE_DEFAULT as u64),
                card_mac,
                card_ip,
            }
        }

        fn register_roce_peer(&self, tid: usize, mac: [u8; 6], ip: [u8; 4], qpn: u32) -> ClientPeer {
            let p = ClientPeer::Roce(RocePeer::new(self.card_mac, self.card_ip, mac, ip, qpn));
            let mut g = self.peers.lock().unwrap();
            g[tid & (MAX_PEERS - 1)] = Some(p);
            self.peer_gen.fetch_add(1, Ordering::Release);
            p
        }

        fn register_std_udp_peer(&self, tid: usize, mac: [u8; 6], ip: [u8; 4], sport: u16) -> ClientPeer {
            let p = ClientPeer::StdUdp(StdUdpPeer {
                mac,
                ip,
                sport,
                card_mac: self.card_mac,
                card_ip: self.card_ip,
            });
            let mut g = self.peers.lock().unwrap();
            g[tid & (MAX_PEERS - 1)] = Some(p);
            self.peer_gen.fetch_add(1, Ordering::Release);
            p
        }
        fn run_cmd(&self, cmd: u64) -> Option<(CoreSnapshot, FastHist, Vec<CoreSnapshot>)> {
            let _guard = self.cmd_lock.lock().unwrap();
            self.done_cnt.store(0, Ordering::Release);
            let prev_gen = self.cmd_word.load(Ordering::Relaxed) >> 4;
            let next_gen = prev_gen + 1;
            self.cmd_word.store((next_gen << 4) | (cmd & 0xf), Ordering::Release);
            let t0 = Instant::now();
            while self.done_cnt.load(Ordering::Acquire) < self.n_cores as u64 {
                if self.quit.load(Ordering::Relaxed) || t0.elapsed() > Duration::from_secs(3) {
                    return None;
                }
                std::thread::sleep(Duration::from_micros(100));
            }
            if cmd == 2 {
                let per = self.per_core_snap.lock().unwrap().clone();
                let mut tot = CoreSnapshot::default();
                for p in &per {
                    tot.add_assign(p);
                }
                let mut merged = FastHist::new();
                for h in self.per_core_hist.lock().unwrap().iter() {
                    merged.merge_from(h);
                }
                Some((tot, merged, per))
            } else {
                Some((CoreSnapshot::default(), FastHist::new(), Vec::new()))
            }
        }
    }

    #[derive(Clone, Copy, Default)]
    struct ReasmMeta {
        rpc_seq: u64,
        mc_len: u16,
        mask: u16,
        frag_cnt: u8,
    }

    struct ToHostJob {
        rpc_seq: u64,
        mc_len: u16,
        total_bytes: usize,
        frag_cnt: u8,
        tid: u8,
        large_slot: usize,
    }

    struct FromHostJob {
        tx_slot: usize,
        rpc_seq: u64,
        mc_len: u16,
        frag_cnt: u8,
        tid: u8,
    }

    struct DrainedPush {
        mc_len: u16,
        tid: u8,
        cells: u8,
        extra: [McCell; 4],
    }

    fn read_xstats_json(st: &PortStats<'_, '_>) -> String {
        let Ok(xs) = st.xstats() else {
            return "{}".to_string();
        };
        let mut parts = Vec::new();
        for (name, val) in xs {
            if name.contains("drop")
                || name.contains("err")
                || name.contains("miss")
                || name.contains("nombuf")
                || name.contains("pause")
                || name.contains("fcs")
            {
                if val > 0 {
                    parts.push(format!("\"{name}\":{val}"));
                }
            }
        }
        format!("{{{}}}", parts.join(","))
    }

    fn format_stats(
        mode: Mode,
        rx_mode: RxMode,
        n_cores: usize,
        tot: &CoreSnapshot,
        hist: &FastHist,
        per_core: &[CoreSnapshot],
        eth_st: &EthStats,
        eth_xstats: &str,
        sdp_st: Option<&EthStats>,
    ) -> String {
        let per_in: Vec<String> = per_core.iter().map(|c| c.rpcs_in.to_string()).collect();
        let per_out: Vec<String> = per_core.iter().map(|c| c.rpcs_out.to_string()).collect();
        let sdp_str = match sdp_st {
            Some(s) => format!(
                ",\"sdp_ipackets\":{},\"sdp_opackets\":{},\"sdp_imissed\":{},\"sdp_ierrors\":{},\"sdp_oerrors\":{},\"sdp_rx_nombuf\":{}",
                s.ipackets, s.opackets, s.imissed, s.ierrors, s.oerrors, s.rx_nombuf
            ),
            None => String::new(),
        };
        format!(
            "{{\"mode\":\"{:?}\",\"rx_mode\":\"{:?}\",\"n_cores\":{},\"eth_rx_pkts\":{},\"eth_tx_pkts\":{},\"rpcs_in\":{},\"rpcs_out\":{},\"small_in\":{},\"large_in\":{},\"small_out\":{},\"large_out\":{},\"per_core_in\":[{}],\"per_core_out\":[{}],\"bad_wire\":{},\"bad_host\":{},\"torn_bar4\":{},\"dma_failed\":{},\"eth_tx_drops\":{},\"sdp_tx_drops\":{},\"probes\":{},\"cqe\":{{\"peek_empty\":{},\"peek_hit\":{},\"ldadda_calls\":{},\"pkts_popped\":{},\"w9_wait_retries\":{},\"watchdog_ldadda\":{},\"watchdog_head_mismatch\":{},\"err_pkts_freed\":{},\"mseg_pkts\":{},\"fallback_ldadda\":{}}},\"host_rtt_cnt\":{},\"host_rtt_p50_us\":{:.3},\"host_rtt_p99_us\":{:.3},\"host_rtt_mean_us\":{:.3},\"eth_ipackets\":{},\"eth_opackets\":{},\"eth_ibytes\":{},\"eth_obytes\":{},\"eth_imissed\":{},\"eth_ierrors\":{},\"eth_oerrors\":{},\"eth_rx_nombuf\":{},\"eth_xstats_nz\":{}{}}}",
            mode,
            rx_mode,
            n_cores,
            tot.eth_rx_pkts,
            tot.eth_tx_pkts,
            tot.rpcs_in,
            tot.rpcs_out,
            tot.small_in,
            tot.large_in,
            tot.small_out,
            tot.large_out,
            per_in.join(","),
            per_out.join(","),
            tot.bad_wire,
            tot.bad_host,
            tot.torn_bar4,
            tot.dma_failed,
            tot.eth_tx_drops,
            tot.sdp_tx_drops,
            tot.probes,
            tot.cqe.peek_empty,
            tot.cqe.peek_hit,
            tot.cqe.ldadda_calls,
            tot.cqe.pkts_popped,
            tot.cqe.w9_wait_retries,
            tot.cqe.watchdog_ldadda,
            tot.cqe.watchdog_head_mismatch,
            tot.cqe.err_pkts_freed,
            tot.cqe.mseg_pkts,
            tot.cqe.fallback_ldadda,
            hist.count(),
            hist.quantile_us(0.50),
            hist.quantile_us(0.99),
            hist.mean_us(),
            eth_st.ipackets,
            eth_st.opackets,
            eth_st.ibytes,
            eth_st.obytes,
            eth_st.imissed,
            eth_st.ierrors,
            eth_st.oerrors,
            eth_st.rx_nombuf,
            eth_xstats,
            sdp_str
        )
    }

    enum EthRxKind<'a> {
        Roce { src_mac: [u8; 6], src_ip: [u8; 4], src_qpn: u32, payload: &'a [u8] },
        StdUdp { src_mac: [u8; 6], src_ip: [u8; 4], sport: u16, payload: &'a [u8] },
        ArpReq { src_mac: [u8; 6], src_ip: [u8; 4] },
    }

    #[inline(always)]
    fn parse_eth_packet<'a>(frame: &'a [u8], card_ip: [u8; 4]) -> Option<EthRxKind<'a>> {
        if frame.len() < 42 {
            return None;
        }
        if frame[12] == 0x08 && frame[13] == 0x06 {
            // ARP request for card_ip (supports standard IPv4/UDP port 11211 clients)
            if frame[20] == 0x00 && frame[21] == 0x01 && frame[38..42] == card_ip {
                let mut src_mac = [0u8; 6];
                src_mac.copy_from_slice(&frame[22..28]);
                let mut src_ip = [0u8; 4];
                src_ip.copy_from_slice(&frame[28..32]);
                return Some(EthRxKind::ArpReq { src_mac, src_ip });
            }
            return None;
        }
        if frame[12] != 0x08 || frame[13] != 0x00 || frame[23] != 17 {
            return None;
        }
        let sport = u16::from_be_bytes([frame[34], frame[35]]);
        let dport = u16::from_be_bytes([frame[36], frame[37]]);
        let udp_len = u16::from_be_bytes([frame[38], frame[39]]) as usize;
        if dport == ROCE_UDP_PORT {
            if frame.len() < ROCE_HDR_LEN + 32 + ROCE_ICRC_LEN || frame[42] != 0x64 {
                return None;
            }
            if udp_len < 8 + 12 + 8 + 32 + ROCE_ICRC_LEN {
                return None;
            }
            let payload_len = udp_len - 8 - 12 - 8 - ROCE_ICRC_LEN;
            let pad_cnt = ((frame[43] >> 4) & 3) as usize;
            let payload_end = ROCE_HDR_LEN + payload_len;
            if payload_end + ROCE_ICRC_LEN > frame.len() || pad_cnt > payload_len {
                return None;
            }
            let mut src_mac = [0u8; 6];
            src_mac.copy_from_slice(&frame[6..12]);
            let mut src_ip = [0u8; 4];
            src_ip.copy_from_slice(&frame[26..30]);
            let src_qpn = u32::from_be_bytes([0, frame[59], frame[60], frame[61]]);
            return Some(EthRxKind::Roce {
                src_mac,
                src_ip,
                src_qpn,
                payload: &frame[ROCE_HDR_LEN..payload_end - pad_cnt],
            });
        }
        if dport == MC_UDP_PORT {
            if udp_len < 8 + 32 {
                return None;
            }
            let mc_len = udp_len - 8;
            if 42 + mc_len > frame.len() {
                return None;
            }
            let mut src_mac = [0u8; 6];
            src_mac.copy_from_slice(&frame[6..12]);
            let mut src_ip = [0u8; 4];
            src_ip.copy_from_slice(&frame[26..30]);
            return Some(EthRxKind::StdUdp {
                src_mac,
                src_ip,
                sport,
                payload: &frame[42..42 + mc_len],
            });
        }
        None
    }

    #[inline(always)]
    fn flush_qp<'e>(qp: &mut QueuePair<'_, 'e>, burst: &mut Burst<'e, MAX_DRAIN>, drops: &mut u64) {
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
            }
        }
    }

    fn build_arp_reply_mbuf<'e>(
        pool: &Mempool<'e>,
        card_mac: [u8; 6],
        card_ip: [u8; 4],
        dst_mac: [u8; 6],
        dst_ip: [u8; 4],
    ) -> Option<Mbuf<'e>> {
        let mut m = pool.alloc()?;
        let dst = m.append(60)?;
        dst.fill(0);
        dst[0..6].copy_from_slice(&dst_mac);
        dst[6..12].copy_from_slice(&card_mac);
        dst[12..14].copy_from_slice(&0x0806u16.to_be_bytes());
        dst[14..16].copy_from_slice(&1u16.to_be_bytes());
        dst[16..18].copy_from_slice(&0x0800u16.to_be_bytes());
        dst[18] = 6;
        dst[19] = 4;
        dst[20..22].copy_from_slice(&2u16.to_be_bytes());
        dst[22..28].copy_from_slice(&card_mac);
        dst[28..32].copy_from_slice(&card_ip);
        dst[32..38].copy_from_slice(&dst_mac);
        dst[38..42].copy_from_slice(&dst_ip);
        Some(m)
    }

    #[inline(always)]
    fn build_roce_raw_mbuf<'e>(pool: &Mempool<'e>, peer: &RocePeer, payload: &[u8]) -> Option<Mbuf<'e>> {
        let pad_cnt = (4 - (payload.len() & 3)) & 3;
        let roce_pay_len = payload.len() + pad_cnt;
        let mut tpl = peer.template_for(roce_pay_len);
        if pad_cnt != 0 {
            tpl.hdr[43] = (pad_cnt as u8) << 4;
            let mut masked = [0u8; 56];
            masked[0..8].fill(0xff);
            masked[8..56].copy_from_slice(&tpl.hdr[14..62]);
            masked[8 + 1] = 0xff;
            masked[8 + 8] = 0xff;
            masked[8 + 10] = 0xff;
            masked[8 + 11] = 0xff;
            masked[8 + 20 + 6] = 0xff;
            masked[8 + 20 + 7] = 0xff;
            masked[8 + 28 + 4] = 0xff;
            tpl.crc_seed = mrv206::crc32_sw_update(0xffff_ffff, &masked);
        }
        let mut m = pool.alloc()?;
        let total = ROCE_HDR_LEN + roce_pay_len + ROCE_ICRC_LEN;
        let dst = m.append(total as u16)?;
        dst[..ROCE_HDR_LEN].copy_from_slice(&tpl.hdr);
        dst[ROCE_HDR_LEN..ROCE_HDR_LEN + payload.len()].copy_from_slice(payload);
        if pad_cnt > 0 {
            dst[ROCE_HDR_LEN + payload.len()..ROCE_HDR_LEN + roce_pay_len].fill(0);
        }
        let icrc = tpl.icrc(&dst[ROCE_HDR_LEN..ROCE_HDR_LEN + roce_pay_len]);
        dst[ROCE_HDR_LEN + roce_pay_len..total].copy_from_slice(&icrc.to_le_bytes());
        Some(m)
    }

    fn build_std_udp_mbuf<'e>(pool: &Mempool<'e>, peer: &StdUdpPeer, dgram: &[u8]) -> Option<Mbuf<'e>> {
        let mut m = pool.alloc()?;
        let total = 42 + dgram.len();
        let dst = m.append(total as u16)?;
        dst[0..6].copy_from_slice(&peer.mac);
        dst[6..12].copy_from_slice(&peer.card_mac);
        dst[12..14].copy_from_slice(&0x0800u16.to_be_bytes());
        let ip_tot = (20 + 8 + dgram.len()) as u16;
        dst[14] = 0x45;
        dst[15] = 0x00;
        dst[16..18].copy_from_slice(&ip_tot.to_be_bytes());
        dst[18..20].copy_from_slice(&0x2060u16.to_be_bytes());
        dst[20..22].copy_from_slice(&0x4000u16.to_be_bytes());
        dst[22] = 64;
        dst[23] = 17;
        dst[24..26].copy_from_slice(&0u16.to_be_bytes());
        dst[26..30].copy_from_slice(&peer.card_ip);
        dst[30..34].copy_from_slice(&peer.ip);
        let mut sum: u32 = 0;
        for i in (14..34).step_by(2) {
            sum += u16::from_be_bytes([dst[i], dst[i + 1]]) as u32;
        }
        while (sum >> 16) != 0 {
            sum = (sum & 0xffff) + (sum >> 16);
        }
        dst[24..26].copy_from_slice(&(!(sum as u16)).to_be_bytes());
        let ulen = (8 + dgram.len()) as u16;
        dst[34..36].copy_from_slice(&MC_UDP_PORT.to_be_bytes());
        dst[36..38].copy_from_slice(&peer.sport.to_be_bytes());
        dst[38..40].copy_from_slice(&ulen.to_be_bytes());
        dst[40..42].copy_from_slice(&0u16.to_be_bytes());
        dst[42..total].copy_from_slice(dgram);
        Some(m)
    }

    /// Emit standard Memcached Binary UDP reply (`mc_slice[0..mc_len]`) out `eth0`:
    ///   - `ClientPeer::StdUdp`: native IPv4/UDP port 11211 packets (1..N `<= 1400 B` standard Memcached datagrams)
    ///   - `ClientPeer::Roce`:   raw zero-wrapper Memcached Binary UDP when `mc_len <= 1024 B`;
    ///                           `1024 B` RoCEv2 UD link-MTU segments (`OP_FRAG`) only when `mc_len > 1024 B`.
    #[inline(always)]
    fn send_mc_to_eth<'e>(
        eth_pool: &Mempool<'e>,
        qp: &mut QueuePair<'_, 'e>,
        eth_tx: &mut Burst<'e, MAX_DRAIN>,
        peer: &ClientPeer,
        rpc_seq: u64,
        mc_slice: &[u8],
        cnt: &mut CoreSnapshot,
    ) {
        let mc_len = mc_slice.len();
        match peer {
            ClientPeer::StdUdp(up) => {
                let mut off = 0usize;
                while off < mc_len {
                    let take = MC_UDP_MAX_PAYLOAD.min(mc_len - off);
                    if let Some(m) = build_std_udp_mbuf(eth_pool, up, &mc_slice[off..off + take]) {
                        if let Err(m_back) = eth_tx.push(m) {
                            flush_qp(qp, eth_tx, &mut cnt.eth_tx_drops);
                            let _ = eth_tx.push(m_back);
                        }
                        cnt.eth_tx_pkts += 1;
                    } else {
                        cnt.eth_tx_drops += 1;
                    }
                    off += take;
                }
            }
            ClientPeer::Roce(rp) => {
                let frag_cnt = roce_frag_cnt(mc_len);
                if frag_cnt == 1 {
                    if let Some(m) = build_roce_raw_mbuf(eth_pool, rp, mc_slice) {
                        if let Err(m_back) = eth_tx.push(m) {
                            flush_qp(qp, eth_tx, &mut cnt.eth_tx_drops);
                            let _ = eth_tx.push(m_back);
                        }
                        cnt.eth_tx_pkts += 1;
                    } else {
                        cnt.eth_tx_drops += 1;
                    }
                } else {
                    let tpl = rp.template_for(MAX_SEG_BYTES);
                    for f in 0..(frag_cnt as usize) {
                        let start = f * MAX_FRAG_PAYLOAD;
                        let end = (start + MAX_FRAG_PAYLOAD).min(mc_len);
                        let chunk = &mc_slice[start..end];
                        let Some(mut m) = eth_pool.alloc() else {
                            cnt.eth_tx_drops += 1;
                            continue;
                        };
                        let total = ROCE_HDR_LEN + MAX_SEG_BYTES + ROCE_ICRC_LEN;
                        let Some(dst) = m.append(total as u16) else {
                            cnt.eth_tx_drops += 1;
                            continue;
                        };
                        dst[..ROCE_HDR_LEN].copy_from_slice(&tpl.hdr);
                        let pay = &mut dst[ROCE_HDR_LEN..ROCE_HDR_LEN + MAX_SEG_BYTES];
                        let wf = pack_wire_meta(rpc_seq, mc_len as u16, f as u8, frag_cnt, OP_FRAG);
                        pay[0..8].copy_from_slice(&wf.to_le_bytes());
                        pay[8..8 + chunk.len()].copy_from_slice(chunk);
                        if chunk.len() < MAX_FRAG_PAYLOAD {
                            pay[8 + chunk.len()..MAX_SEG_BYTES - 8].fill(0);
                        }
                        pay[MAX_SEG_BYTES - 8..MAX_SEG_BYTES].copy_from_slice(&wf.to_le_bytes());
                        let icrc = tpl.icrc(&dst[ROCE_HDR_LEN..ROCE_HDR_LEN + MAX_SEG_BYTES]);
                        dst[ROCE_HDR_LEN + MAX_SEG_BYTES..total].copy_from_slice(&icrc.to_le_bytes());
                        if let Err(m_back) = eth_tx.push(m) {
                            flush_qp(qp, eth_tx, &mut cnt.eth_tx_drops);
                            let _ = eth_tx.push(m_back);
                        }
                        cnt.eth_tx_pkts += 1;
                    }
                }
            }
        }
        if eth_tx.len() >= 16 {
            flush_qp(qp, eth_tx, &mut cnt.eth_tx_drops);
        }
    }

    #[inline(always)]
    fn reasm_index(rpc_seq: u64) -> usize {
        let tid = ((rpc_seq >> 34) & 7) as usize;
        let lseq = (rpc_seq & 255) as usize;
        (tid * 256 + lseq) & REASM_MASK
    }

    /// Build 48 B aligned Eth(14) + IPv4(20) + UDP(8) + Pad(6) template for SDP frames.
    fn build_sdp_hdr(dst_mac: [u8; 6], src_mac: [u8; 6], core_id: usize, payload_len: usize) -> [u8; SDP_HDR_LEN] {
        let mut hdr = [0u8; SDP_HDR_LEN];
        hdr[0..6].copy_from_slice(&dst_mac);
        hdr[6..12].copy_from_slice(&src_mac);
        hdr[12..14].copy_from_slice(&0x0800u16.to_be_bytes());
        let ip_tot = (20 + 8 + 6 + payload_len) as u16;
        hdr[14] = 0x45;
        hdr[15] = 0x00;
        hdr[16..18].copy_from_slice(&ip_tot.to_be_bytes());
        hdr[18..20].copy_from_slice(&0x2060u16.to_be_bytes());
        hdr[20..22].copy_from_slice(&0x4000u16.to_be_bytes());
        hdr[22] = 64;
        hdr[23] = 17;
        hdr[26..30].copy_from_slice(&[10, 10, 200, 2]);
        hdr[30..34].copy_from_slice(&[10, 10, 200, 1]);
        let sport = (5000 + core_id as u16).to_be_bytes();
        let dport = SDP_UDP_DST_PORT.to_be_bytes();
        let ulen = (8 + 6 + payload_len) as u16;
        hdr[34..36].copy_from_slice(&sport);
        hdr[36..38].copy_from_slice(&dport);
        hdr[38..40].copy_from_slice(&ulen.to_be_bytes());
        hdr
    }

    struct WorkerInput<'p, 'e> {
        eth_qp: QueuePair<'p, 'e>,
        sdp_qp: Option<QueuePair<'p, 'e>>,
        wc_win: Option<OutboundWindow>,
        dma: Option<HostDma<'e>>,
        reasm_ptr: usize,
        reasm_iova: u64,
        tx_stage_ptr: usize,
        tx_stage_iova: u64,
    }

    unsafe impl Send for WorkerInput<'_, '_> {}

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
            println!("restored: {:?}", changed);
            return Ok(());
        }

        let (eal, _) = Eal::init(eal_args)?;
        let n_cores = eal.lcores();
        if n_cores == 0 || n_cores > MAX_CARD_CORES {
            return Err(format!("expected 1..={} lcores, got {}", MAX_CARD_CORES, n_cores).into());
        }
        let tsc_hz = eal.tsc_hz() as f64;
        let ticks_to_ns = 1e9 / tsc_hz;

        let tad_pmu = Arc::new(TadPmu::open()?);

        // 1. Physical 100GbE `eth0` (4RxQ/4TxQ RSS)
        let eth_id = Port::by_name(&eal, &args.eth_dev)?;
        let eth_sock = Port::socket_of(&eal, eth_id);
        let eth_pool_sz = (16384 * n_cores as u32).min(65535);
        let eth_pool = Mempool::new_with_buf_size(&eal, "m206_eth_pool", eth_pool_sz, ETH_MBUF_BUF_SIZE, eth_sock)?;
        let mut eth_port = Port::start_with_opts_rss(
            &eal,
            eth_id,
            &eth_pool,
            n_cores as u16,
            ETH_DESC,
            ETH_DESC,
            Some(1500),
            n_cores > 1,
        )?;
        eth_port.promiscuous()?;
        let card_mac = eth_port.mac();
        let card_ip: [u8; 4] = [10, 10, 100, 2];

        let shared = Arc::new(Shared::new(n_cores, card_mac, card_ip));
        {
            let sh = Arc::clone(&shared);
            ctrlc::set_handler(move || sh.quit.store(true, Ordering::Relaxed))?;
        }

        // 2. Optional 4-queue `sdp1-1` in `--mode sdp`
        let sdp_pool: Option<Mempool<'_>>;
        let mut sdp_port: Option<Port<'_>>;
        let sdp_card_mac: [u8; 6];
        if args.mode == Mode::Sdp {
            let sdp_id = Port::by_name(&eal, &args.sdp_dev)?;
            let sdp_sock = Port::socket_of(&eal, sdp_id);
            let sp = Mempool::new_with_buf_size(&eal, "m206_sdp_pool", 16384, SDP_MBUF_BUF_SIZE, sdp_sock)?;
            let port = Port::start_with_opts_rss(
                &eal,
                sdp_id,
                &sp,
                n_cores as u16,
                SDP_DESC,
                SDP_DESC,
                Some(SDP_MTU),
                !args.no_sdp_rss && n_cores > 1,
            )?;
            port.promiscuous()?;
            sdp_card_mac = port.mac();
            sdp_port = Some(port);
            sdp_pool = Some(sp);
        } else {
            sdp_card_mac = [0x02, 0x20, 0x60, 0x00, 0x00, 0x02];
            sdp_port = None;
            sdp_pool = None;
        }

        // 3. Per-core contiguous reasm & tx_stage memzones
        let mut reasm_mzs: Vec<Memzone<'_>> = Vec::with_capacity(n_cores);
        let mut stage_mzs: Vec<Memzone<'_>> = Vec::with_capacity(n_cores);
        for c in 0..n_cores {
            let rmz = Memzone::reserve_contiguous(
                &eal,
                &format!("m206_reasm_{c}"),
                REASM_SLOTS * MAX_RPC_BYTES,
                eth_sock,
                4096,
            )?;
            unsafe { std::ptr::write_bytes(rmz.as_ptr(), 0, REASM_SLOTS * MAX_RPC_BYTES) };
            reasm_mzs.push(rmz);
            let smz = Memzone::reserve_contiguous(
                &eal,
                &format!("m206_stage_{c}"),
                STAGE_SLOTS * MAX_RPC_BYTES,
                eth_sock,
                4096,
            )?;
            unsafe { std::ptr::write_bytes(smz.as_ptr(), 0, STAGE_SLOTS * MAX_RPC_BYTES) };
            stage_mzs.push(smz);
        }

        // 4. DirectPush BAR4 + 4x PEM0 WC windows + 4x exclusive bidirectional DPI VFs
        let bar4_mz: Option<Memzone<'_>>;
        let _bar4_win: Option<Bar4Window>;
        let _bar4_nc: Option<testbed::bar4_nc::Bar4NcMapping>;
        let mut wc_wins: Vec<Option<OutboundWindow>> = Vec::with_capacity(n_cores);
        let mut dmas: Vec<Option<HostDma<'_>>> = Vec::with_capacity(n_cores);

        if args.mode == Mode::Direct {
            let mz = Memzone::reserve_contiguous(&eal, "m206_bar4", SLOT_BYTES, eth_sock, SLOT_BYTES as u32)?;
            unsafe {
                std::ptr::write_bytes(mz.as_ptr(), 0, SLOT_BYTES);
                mz.as_ptr().add(READY_OFFSET).cast::<u64>().write_volatile(READY);
                mz.as_ptr().add(NONCE_OFFSET).cast::<u64>().write_volatile(0);
                mz.as_ptr().add(CTRL_CARD_CORES).cast::<u64>().write_volatile(n_cores as u64);
            }
            let pa = mz.phys_addr().ok_or("rte_mem_virt2phy failed on BAR4 memzone")?;
            let bw = Bar4Window::claim(SLOT, pa)?;
            let dma_list: Vec<&str> = args.dma_devs.split(',').map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
            if dma_list.len() < n_cores {
                return Err(format!("need {} DPI VFs in --dma-devs, got {}", n_cores, dma_list.len()).into());
            }
            let host = PcieFunction { core: 0, pf: args.host_pf, vf: args.host_vf };
            for c in 0..n_cores {
                wc_wins.push(Some(OutboundWindow::claim(255 - c, args.host_pf, args.host_vf, !args.no_wc)?));
                let dma = HostDma::start_bidir(&eal, dma_list[c], DMA_DESC, host)?;
                dmas.push(Some(dma));
            }
            bar4_mz = Some(mz);
            _bar4_win = Some(bw);
            _bar4_nc = if args.bar4_nc {
                let nc = testbed::bar4_nc::Bar4NcMapping::open_and_map(
                    &args.bar4_nc_dev,
                    bar4_mz.as_ref().unwrap().as_ptr(),
                    pa,
                )?;
                eprintln!("mrv259_mc_card: BAR4 response ring mapped MT_NORMAL_NC (par={:#018x})", nc.par_el1);
                Some(nc)
            } else {
                None
            };
        } else {
            for _ in 0..n_cores {
                wc_wins.push(None);
                dmas.push(None);
            }
            bar4_mz = None;
            _bar4_win = None;
            _bar4_nc = None;
        }

        let bar4_ptr_usize = bar4_mz.as_ref().map_or(0usize, |m| m.as_ptr() as usize);
        let bar4_ring_ptr_usize = _bar4_nc.as_ref().map_or(bar4_ptr_usize, |m| m.as_ptr() as usize);
        let use_bar4_nc = args.bar4_nc;

        let sync_listener = TcpListener::bind(&args.sync)?;
        sync_listener.set_nonblocking(true)?;
        let lease_listener = if args.mode == Mode::Direct {
            Some(TcpListener::bind(&args.control)?)
        } else {
            None
        };

        let (eth_stats, eth_qps) = eth_port.split_queues();
        let _ = eth_stats.stats_reset();
        let _ = eth_stats.xstats_reset();

        let (sdp_stats, mut sdp_qps_opt): (Option<PortStats<'_, '_>>, Vec<Option<QueuePair<'_, '_>>>) =
            if let Some(sp) = sdp_port.as_mut() {
                let (st, qps) = sp.split_queues();
                let _ = st.stats_reset();
                (Some(st), qps.into_iter().map(Some).collect())
            } else {
                (None, (0..n_cores).map(|_| None).collect())
            };

        let mut inputs: Vec<WorkerInput<'_, '_>> = Vec::with_capacity(n_cores);
        for (c, eth_qp) in eth_qps.into_iter().enumerate() {
            inputs.push(WorkerInput {
                eth_qp,
                sdp_qp: sdp_qps_opt[c].take(),
                wc_win: wc_wins[c].take(),
                dma: dmas[c].take(),
                reasm_ptr: reasm_mzs[c].as_ptr() as usize,
                reasm_iova: reasm_mzs[c].iova(),
                tx_stage_ptr: stage_mzs[c].as_ptr() as usize,
                tx_stage_iova: stage_mzs[c].iova(),
            });
        }

        let mode = args.mode;
        let rx_mode = args.rx_mode;
        let watchdog_interval = args.watchdog_interval;
        let use_wc = !args.no_wc;

        std::thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            let _lease_srv = if let (Some(l), Some(mz)) = (lease_listener, bar4_mz.as_ref()) {
                let nonce_ref: &AtomicU64 = unsafe { &*mz.as_ptr().add(NONCE_OFFSET).cast::<AtomicU64>() };
                Some(lease::serve(scope, l, nonce_ref)?)
            } else {
                None
            };

            let sh_srv = Arc::clone(&shared);
            let pmu_srv = Arc::clone(&tad_pmu);
            let eth_stats_srv = eth_stats;
            let sdp_stats_srv = sdp_stats;
            scope.spawn(move || {
                unsafe {
                    let mut set: libc::cpu_set_t = std::mem::zeroed();
                    libc::CPU_ZERO(&mut set);
                    libc::CPU_SET(0, &mut set);
                    libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set);
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
                                let reply = match cmd {
                                    "PING" => "{\"ok\":true}".to_string(),
                                    "PMU_SNAP" => pmu_srv.snap_json(),
                                    "RESET" => {
                                        let _ = sh_srv.run_cmd(1);
                                        let _ = eth_stats_srv.stats_reset();
                                        let _ = eth_stats_srv.xstats_reset();
                                        if let Some(ss) = sdp_stats_srv.as_ref() {
                                            let _ = ss.stats_reset();
                                        }
                                        "{\"ok\":true}".to_string()
                                    }
                                    "RESET_PEERS" => {
                                        {
                                            let mut pg = sh_srv.peers.lock().unwrap();
                                            *pg = [None; MAX_PEERS];
                                            sh_srv.peer_gen.fetch_add(1, Ordering::Release);
                                        }
                                        let _ = sh_srv.run_cmd(1);
                                        let _ = eth_stats_srv.stats_reset();
                                        let _ = eth_stats_srv.xstats_reset();
                                        if let Some(ss) = sdp_stats_srv.as_ref() {
                                            let _ = ss.stats_reset();
                                        }
                                        "{\"ok\":true}".to_string()
                                    }
                                    "DETACH" => {
                                        if bar4_ptr_usize != 0 {
                                            unsafe {
                                                let p = bar4_ptr_usize as *mut u8;
                                                (*p.add(CTRL_HOST_READY_GEN).cast::<AtomicU64>()).store(0, Ordering::Release);
                                            }
                                        }
                                        sh_srv.host_gen.store(0, Ordering::Release);
                                        let ok = sh_srv.run_cmd(3).is_some();
                                        format!("{{\"ok\":{ok}}}")
                                    }
                                    "STATS" => match sh_srv.run_cmd(2) {
                                        Some((tot, hist, per_core)) => {
                                            let est = eth_stats_srv.stats().unwrap_or_default();
                                            let exs = read_xstats_json(&eth_stats_srv);
                                            let sst = sdp_stats_srv.as_ref().and_then(|s| s.stats().ok());
                                            format_stats(
                                                mode,
                                                rx_mode,
                                                n_cores,
                                                &tot,
                                                &hist,
                                                &per_core,
                                                &est,
                                                &exs,
                                                sst.as_ref(),
                                            )
                                        }
                                        None => "{\"err\":\"timeout\"}".to_string(),
                                    },
                                    _ => "{\"err\":\"unknown\"}".to_string(),
                                };
                                let _ = writeln!(stream, "{reply}");
                                let _ = stream.flush();
                            }
                        }
                        Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(2));
                        }
                        Err(_) => {
                            std::thread::sleep(Duration::from_millis(5));
                        }
                    }
                }
            });

            let ready_name = match mode {
                Mode::CardEcho => "card_echo ready",
                Mode::Sdp => "sdp ready",
                Mode::Direct => "direct ready",
            };
            println!(
                "MRV206_CARD {ready_name} n_cores={} rx_mode={:?} mac={:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x} tsc_hz={:.0}",
                n_cores,
                rx_mode,
                card_mac[0],
                card_mac[1],
                card_mac[2],
                card_mac[3],
                card_mac[4],
                card_mac[5],
                tsc_hz
            );
            let _ = std::io::stdout().flush();

            let eth_pool_ref = &eth_pool;
            let sdp_pool_ref = sdp_pool.as_ref();
            let sh_ref = &shared;

            eal.on_each_lcore(inputs, move |core_idx, mut inp| {
                let mut eth_rx = Burst::<MAX_DRAIN>::new();
                let mut eth_tx = Burst::<MAX_DRAIN>::new();
                let mut sdp_rx = Burst::<MAX_DRAIN>::new();
                let mut sdp_tx = Burst::<MAX_DRAIN>::new();

                let mut local_peers: [Option<ClientPeer>; MAX_PEERS] = [None; MAX_PEERS];
                let mut local_peer_gen: u64 = 0;

                let mut sdp_host_mac = [0xffu8; 6];
                let mut reasm_meta = vec![ReasmMeta::default(); REASM_SLOTS];
                let mut host_t0 = vec![0u64; TIMING_MASK + 1];
                let mut to_inflight: VecDeque<ToHostJob> = VecDeque::with_capacity(256);
                let mut from_inflight: VecDeque<FromHostJob> = VecDeque::with_capacity(256);
                let mut drained_push: Vec<DrainedPush> = Vec::with_capacity(MAX_DRAIN);
                let mut push_mc_buf = [0u8; 256];

                let mut cnt = CoreSnapshot::default();
                let mut hist = FastHist::new();
                let mut handled_cmd_gen: u64 = 0;

                let mut local_host_gen: u64 = 0;
                let mut host_rx_iova: u64 = 0;
                let mut wc_ptr: *mut u8 = std::ptr::null_mut();
                let mut credit_ptr: *mut u8 = std::ptr::null_mut();
                let mut rx_seq: u64 = 1;
                let mut bar_pos: u64 = 1;
                let mut last_credit_sent: u64 = 0;
                let mut credit_hyst: u64 = 8;
                let mut bar4_stride: usize = BAR4_CELL_STRIDE_DEFAULT;
                let mut large_rx_next: usize = 0;
                let mut tx_stage_next: usize = 0;

                let reasm_ptr = inp.reasm_ptr as *mut u8;
                let tx_stage_ptr = inp.tx_stage_ptr as *mut u8;
                let bar_base_ptr = bar4_ptr_usize as *mut u8;
                let bar_ring_ptr = bar4_ring_ptr_usize as *mut u8;

                let mut iter: u64 = 0;
                let mut std_udp_seq: u64 = 0;

                while !sh_ref.quit.load(Ordering::Relaxed) {
                    iter = iter.wrapping_add(1);

                    // Periodic control & handshake check
                    if (iter & 0x3ff) == 0 {
                        let pgen = sh_ref.peer_gen.load(Ordering::Acquire);
                        if pgen != local_peer_gen {
                            let g = sh_ref.peers.lock().unwrap();
                            local_peers = *g;
                            local_peer_gen = pgen;
                        }

                        let word = sh_ref.cmd_word.load(Ordering::Acquire);
                        let g = word >> 4;
                        if g > handled_cmd_gen {
                            let cmd = word & 0xf;
                            if rx_mode == RxMode::CqeDirectSafe {
                                let _ = inp.eth_qp.cn10k_cqe_direct_sync();
                            }
                            if cmd == 1 {
                                cnt = CoreSnapshot::default();
                                hist.reset();
                                let pgen_now = sh_ref.peer_gen.load(Ordering::Acquire);
                                let pg = sh_ref.peers.lock().unwrap();
                                local_peers = *pg;
                                local_peer_gen = pgen_now;
                            } else if cmd == 2 {
                                sh_ref.per_core_snap.lock().unwrap()[core_idx] = cnt.clone();
                                sh_ref.per_core_hist.lock().unwrap()[core_idx].reset();
                                sh_ref.per_core_hist.lock().unwrap()[core_idx].merge_from(&hist);
                            } else if cmd == 3 {
                                wc_ptr = std::ptr::null_mut();
                                credit_ptr = std::ptr::null_mut();
                                local_host_gen = 0;
                                if let Some(win) = inp.wc_win.as_mut() {
                                    if let Err(e) = win.detach() {
                                        eprintln!("MRV206_CARD core={core_idx} win.detach failed: {e}");
                                    }
                                }
                            }
                            handled_cmd_gen = g;
                            sh_ref.done_cnt.fetch_add(1, Ordering::Release);
                        }

                        // DirectPush host-reconnect handshake (coordinated by core 0)
                        if mode == Mode::Direct && !bar_base_ptr.is_null() {
                            let mut pending_ack_gen: u64 = 0;
                            if core_idx == 0 {
                                let hgen = unsafe {
                                    (*bar_base_ptr.add(CTRL_HOST_READY_GEN).cast::<AtomicU64>())
                                        .load(Ordering::Acquire)
                                };
                                if hgen != 0 && hgen != local_host_gen {
                                    let iova = unsafe {
                                        (*bar_base_ptr.add(CTRL_RX_ZONE_IOVA).cast::<AtomicU64>())
                                            .load(Ordering::Acquire)
                                    };
                                    let hyst = unsafe {
                                        (*bar_base_ptr.add(CTRL_CREDIT_HYST).cast::<AtomicU64>())
                                            .load(Ordering::Acquire)
                                    };
                                    let stride = unsafe {
                                        (*bar_base_ptr.add(CTRL_BAR4_STRIDE).cast::<AtomicU64>())
                                            .load(Ordering::Acquire)
                                    };
                                    unsafe {
                                        std::ptr::write_bytes(bar_ring_ptr, 0, BAR4_TOTAL_RING_BYTES);
                                        if use_bar4_nc {
                                            std::arch::asm!("dsb sy", options(nostack, preserves_flags));
                                        } else {
                                            std::arch::asm!("dmb ish", options(nostack, preserves_flags));
                                        }
                                    }
                                    sh_ref.host_rx_iova.store(iova, Ordering::Release);
                                    sh_ref.credit_hyst.store(hyst.max(1), Ordering::Release);
                                    sh_ref.bar4_stride.store(
                                        if stride == 64 { 64 } else { BAR4_CELL_STRIDE_DEFAULT as u64 },
                                        Ordering::Release,
                                    );
                                    sh_ref.host_retarget_done.store(0, Ordering::Release);
                                    sh_ref.host_gen.store(hgen, Ordering::Release);
                                    pending_ack_gen = hgen;
                                }
                            }
                            let cur_gen = sh_ref.host_gen.load(Ordering::Acquire);
                            if cur_gen != 0 && cur_gen != local_host_gen {
                                local_host_gen = cur_gen;
                                host_rx_iova = sh_ref.host_rx_iova.load(Ordering::Acquire);
                                credit_hyst = sh_ref.credit_hyst.load(Ordering::Acquire);
                                bar4_stride = sh_ref.bar4_stride.load(Ordering::Acquire) as usize;
                                rx_seq = 1;
                                bar_pos = 1;
                                last_credit_sent = 0;
                                large_rx_next = 0;
                                tx_stage_next = 0;
                                to_inflight.clear();
                                from_inflight.clear();
                                if let Some(win) = inp.wc_win.as_mut() {
                                    let aligned_base = host_rx_iova & !((OUTBOUND_REGION_BYTES as u64) - 1);
                                    let off = (host_rx_iova - aligned_base) as usize;
                                    if off + RX_ZONE_BYTES <= (OUTBOUND_REGION_BYTES as usize) {
                                        let r = if use_wc {
                                            win.retarget_range(aligned_base, off, RX_ZONE_BYTES)
                                        } else {
                                            win.retarget(aligned_base)
                                        };
                                        match r {
                                            Ok(()) => {
                                                wc_ptr = if use_wc {
                                                    win.as_ptr()
                                                } else {
                                                    unsafe { win.as_ptr().add(off) }
                                                };
                                                credit_ptr = unsafe { wc_ptr.add(credit_line_offset(core_idx)) };
                                                unsafe { std::arch::asm!("dsb sy; isb", options(nostack, preserves_flags)) };
                                            }
                                            Err(e) => {
                                                eprintln!("MRV206_CARD FATAL core={core_idx} win.retarget_range failed: {e}");
                                                sh_ref.quit.store(true, Ordering::Release);
                                                break;
                                            }
                                        }
                                    } else {
                                        eprintln!("MRV206_CARD FATAL core={core_idx} host_rx_iova crosses 2 GiB PEM0 region");
                                        sh_ref.quit.store(true, Ordering::Release);
                                        break;
                                    }
                                }
                                sh_ref.host_retarget_done.fetch_add(1, Ordering::Release);
                            }
                            if pending_ack_gen != 0 {
                                let t_ack = Instant::now();
                                while sh_ref.host_retarget_done.load(Ordering::Acquire) < sh_ref.n_cores as u64 {
                                    if sh_ref.quit.load(Ordering::Relaxed) || t_ack.elapsed() > Duration::from_secs(2) {
                                        break;
                                    }
                                    std::hint::spin_loop();
                                }
                                if !sh_ref.quit.load(Ordering::Relaxed) {
                                    unsafe {
                                        (*bar_base_ptr.add(CTRL_CARD_ACK_GEN).cast::<AtomicU64>())
                                            .store(pending_ack_gen, Ordering::Release);
                                    }
                                }
                            }
                        }
                    }

                    // ---------------------------------------------------------
                    // 1. Poll `eth0` RX queue (default / cqe-peek / cqe-direct-safe)
                    // ---------------------------------------------------------
                    let n_eth = match rx_mode {
                        RxMode::Default => inp.eth_qp.rx(&mut eth_rx),
                        RxMode::CqePeek => inp.eth_qp.rx_cqe_peek(&mut eth_rx, Some(&mut cnt.cqe), false),
                        RxMode::CqeDirectSafe => inp.eth_qp.rx_cqe_direct_safe(
                            &mut eth_rx,
                            Some(&mut cnt.cqe),
                            true,
                            true,
                            watchdog_interval,
                        ),
                    };

                    if n_eth > 0 {
                        let mut dma_to_queued = false;
                        eth_rx.drain_into(|pkt| {
                            let frame = pkt.data();
                            let Some(rxk) = parse_eth_packet(frame, sh_ref.card_ip) else {
                                return;
                            };
                            let (rpc_seq, mc_len, frag_cnt, tid, mc_slice, reasm_slot): (u64, usize, u8, usize, &[u8], usize) =
                                match rxk {
                                    EthRxKind::ArpReq { src_mac, src_ip } => {
                                        if let Some(m) = build_arp_reply_mbuf(
                                            eth_pool_ref,
                                            sh_ref.card_mac,
                                            sh_ref.card_ip,
                                            src_mac,
                                            src_ip,
                                        ) {
                                            if let Err(m_back) = eth_tx.push(m) {
                                                flush_qp(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                                                let _ = eth_tx.push(m_back);
                                            }
                                        }
                                        return;
                                    }
                                    EthRxKind::StdUdp { src_mac, src_ip, sport, payload } => {
                                        cnt.eth_rx_pkts += 1;
                                        let mc_len = payload.len();
                                        if mc_len < 32
                                            || mc_len > MAX_MC_BYTES
                                            || payload[4] != 0
                                            || payload[5] != 1
                                            || payload[8] != MC_MAGIC_REQ
                                        {
                                            cnt.bad_wire += 1;
                                            return;
                                        }
                                        let tid = MAX_PEERS - 1;
                                        let need_reg = match &local_peers[tid] {
                                            Some(ClientPeer::StdUdp(u)) => {
                                                u.mac != src_mac || u.ip != src_ip || u.sport != sport
                                            }
                                            _ => true,
                                        };
                                        if need_reg {
                                            let _ = sh_ref.register_std_udp_peer(tid, src_mac, src_ip, sport);
                                            let pg = sh_ref.peers.lock().unwrap();
                                            local_peers = *pg;
                                            local_peer_gen = sh_ref.peer_gen.load(Ordering::Acquire);
                                        }
                                        std_udp_seq = std_udp_seq.wrapping_add(1);
                                        let rpc_seq = ((tid as u64) << 34) | ((std_udp_seq & 0x1fff_ffff).max(1));
                                        let fc = wire_frag_cnt(mc_len);
                                        let total_bytes = wire_total_bytes(mc_len);
                                        if mode == Mode::Direct && mc_len > OP_PUSH_MAX_MC_BYTES {
                                            let slot = reasm_index(rpc_seq);
                                            let head_w0 = pack_wire_meta(rpc_seq, mc_len as u16, 0, fc, OP_NET);
                                            unsafe {
                                                let base = reasm_ptr.add(slot * MAX_RPC_BYTES);
                                                std::ptr::copy_nonoverlapping(head_w0.to_le_bytes().as_ptr(), base, 8);
                                                std::ptr::copy_nonoverlapping(payload.as_ptr(), base.add(8), mc_len);
                                                std::ptr::copy_nonoverlapping(
                                                    head_w0.to_le_bytes().as_ptr(),
                                                    base.add(total_bytes - 8),
                                                    8,
                                                );
                                            }
                                            let sl = unsafe {
                                                std::slice::from_raw_parts(
                                                    reasm_ptr.add(slot * MAX_RPC_BYTES + 8),
                                                    mc_len,
                                                )
                                            };
                                            (rpc_seq, mc_len, fc, tid, sl, slot)
                                        } else {
                                            (rpc_seq, mc_len, fc, tid, payload, 0)
                                        }
                                    }
                                    EthRxKind::Roce { src_mac, src_ip, src_qpn, payload } => {
                                        cnt.eth_rx_pkts += 1;
                                        let tag_byte = payload[7];
                                        if tag_byte == OP_PROBE_WIRE {
                                            if payload.len() < 64 {
                                                cnt.bad_wire += 1;
                                                return;
                                            }
                                            let w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
                                            let w_tail = u64::from_le_bytes(payload[payload.len() - 8..payload.len()].try_into().unwrap());
                                            let (rpc_seq, _, _, _, _) = unpack_wire_meta(w0);
                                            let cookie = u64::from_le_bytes(payload[16..24].try_into().unwrap());
                                            if w0 == w_tail && cookie == probe_cookie(rpc_seq) {
                                                cnt.probes += 1;
                                                let tmp_peer = RocePeer::new(sh_ref.card_mac, sh_ref.card_ip, src_mac, src_ip, src_qpn);
                                                let mut rep = [0u8; 64];
                                                rep[0..8].copy_from_slice(&w0.to_le_bytes());
                                                rep[8..16].copy_from_slice(&(core_idx as u64).to_le_bytes());
                                                rep[16..24].copy_from_slice(&cookie.to_le_bytes());
                                                rep[56..64].copy_from_slice(&w0.to_le_bytes());
                                                if let Some(m) = build_roce_raw_mbuf(eth_pool_ref, &tmp_peer, &rep) {
                                                    if let Err(m_back) = eth_tx.push(m) {
                                                        flush_qp(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                                                        let _ = eth_tx.push(m_back);
                                                    }
                                                    cnt.eth_tx_pkts += 1;
                                                }
                                            }
                                            return;
                                        } else if tag_byte == 0 {
                                            // Raw zero-wrapper standard Memcached Binary UDP datagram (<= 1024 B)
                                            let mc_len = payload.len();
                                            if mc_len < 32
                                                || mc_len > MAX_SEG_BYTES
                                                || payload[4] != 0
                                                || payload[5] != 1
                                                || payload[6] != 0
                                                || payload[8] != MC_MAGIC_REQ
                                            {
                                                cnt.bad_wire += 1;
                                                return;
                                            }
                                            let rpc_seq = rpc_seq_from_opaque(mc_udp_opaque(payload));
                                            if rpc_seq == 0 {
                                                cnt.bad_wire += 1;
                                                return;
                                            }
                                            let tid = ((rpc_seq >> 34) & 7) as usize;
                                            let need_reg = match &local_peers[tid] {
                                                Some(ClientPeer::Roce(p)) => {
                                                    p.qpn != src_qpn || p.mac != src_mac || p.ip != src_ip
                                                }
                                                _ => true,
                                            };
                                            if need_reg {
                                                let _ = sh_ref.register_roce_peer(tid, src_mac, src_ip, src_qpn);
                                                let pg = sh_ref.peers.lock().unwrap();
                                                local_peers = *pg;
                                                local_peer_gen = sh_ref.peer_gen.load(Ordering::Acquire);
                                            }
                                            let fc = wire_frag_cnt(mc_len);
                                            let total_bytes = wire_total_bytes(mc_len);
                                            if mode == Mode::Direct && mc_len > OP_PUSH_MAX_MC_BYTES {
                                                let slot = reasm_index(rpc_seq);
                                                let head_w0 = pack_wire_meta(rpc_seq, mc_len as u16, 0, fc, OP_NET);
                                                unsafe {
                                                    let base = reasm_ptr.add(slot * MAX_RPC_BYTES);
                                                    std::ptr::copy_nonoverlapping(head_w0.to_le_bytes().as_ptr(), base, 8);
                                                    std::ptr::copy_nonoverlapping(payload.as_ptr(), base.add(8), mc_len);
                                                    std::ptr::copy_nonoverlapping(
                                                        head_w0.to_le_bytes().as_ptr(),
                                                        base.add(total_bytes - 8),
                                                        8,
                                                    );
                                                }
                                                let sl = unsafe {
                                                    std::slice::from_raw_parts(
                                                        reasm_ptr.add(slot * MAX_RPC_BYTES + 8),
                                                        mc_len,
                                                    )
                                                };
                                                (rpc_seq, mc_len, fc, tid, sl, slot)
                                            } else {
                                                (rpc_seq, mc_len, fc, tid, payload, 0)
                                            }
                                        } else if tag_byte == OP_FRAG {
                                            if payload.len() != MAX_SEG_BYTES {
                                                cnt.bad_wire += 1;
                                                return;
                                            }
                                            let w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
                                            let w_tail = u64::from_le_bytes(payload[MAX_SEG_BYTES - 8..MAX_SEG_BYTES].try_into().unwrap());
                                            let (rpc_seq, mc_len_u16, frag_idx, frag_cnt, _op) = unpack_wire_meta(w0);
                                            let mc_len = mc_len_u16 as usize;
                                            if rpc_seq == 0
                                                || w0 != w_tail
                                                || frag_cnt < 2
                                                || frag_cnt > 4
                                                || frag_idx >= frag_cnt
                                                || mc_len <= MAX_SEG_BYTES
                                                || mc_len > MAX_MC_BYTES
                                            {
                                                cnt.bad_wire += 1;
                                                return;
                                            }
                                            let tid = ((rpc_seq >> 34) & 7) as usize;
                                            let need_reg = match &local_peers[tid] {
                                                Some(ClientPeer::Roce(p)) => {
                                                    p.qpn != src_qpn || p.mac != src_mac || p.ip != src_ip
                                                }
                                                _ => true,
                                            };
                                            if need_reg {
                                                let _ = sh_ref.register_roce_peer(tid, src_mac, src_ip, src_qpn);
                                                let pg = sh_ref.peers.lock().unwrap();
                                                local_peers = *pg;
                                                local_peer_gen = sh_ref.peer_gen.load(Ordering::Acquire);
                                            }
                                            let slot = reasm_index(rpc_seq);
                                            let rm = &mut reasm_meta[slot];
                                            if rm.rpc_seq != rpc_seq {
                                                rm.rpc_seq = rpc_seq;
                                                rm.mc_len = mc_len_u16;
                                                rm.mask = 0;
                                                rm.frag_cnt = frag_cnt;
                                            }
                                            let start = (frag_idx as usize) * MAX_FRAG_PAYLOAD;
                                            let copy_len = MAX_FRAG_PAYLOAD.min(mc_len.saturating_sub(start));
                                            unsafe {
                                                std::ptr::copy_nonoverlapping(
                                                    payload.as_ptr().add(8),
                                                    reasm_ptr.add(slot * MAX_RPC_BYTES + 8 + start),
                                                    copy_len,
                                                );
                                            }
                                            rm.mask |= 1u16 << frag_idx;
                                            let full_mask = (1u16 << frag_cnt) - 1;
                                            if rm.mask != full_mask {
                                                return;
                                            }
                                            rm.mask = 0;
                                            let total_bytes = wire_total_bytes(mc_len);
                                            let head_w0 = pack_wire_meta(rpc_seq, mc_len_u16, 0, frag_cnt, OP_NET);
                                            unsafe {
                                                let base = reasm_ptr.add(slot * MAX_RPC_BYTES);
                                                std::ptr::copy_nonoverlapping(head_w0.to_le_bytes().as_ptr(), base, 8);
                                                std::ptr::copy_nonoverlapping(
                                                    head_w0.to_le_bytes().as_ptr(),
                                                    base.add(total_bytes - 8),
                                                    8,
                                                );
                                            }
                                            let sl = unsafe {
                                                std::slice::from_raw_parts(
                                                    reasm_ptr.add(slot * MAX_RPC_BYTES + 8),
                                                    mc_len,
                                                )
                                            };
                                            (rpc_seq, mc_len, frag_cnt, tid, sl, slot)
                                        } else {
                                            cnt.bad_wire += 1;
                                            return;
                                        }
                                    }
                                };

                            let mc_len_u16 = mc_len as u16;
                            let total_bytes = wire_total_bytes(mc_len);
                            cnt.rpcs_in += 1;
                            if mc_len <= OP_PUSH_MAX_MC_BYTES {
                                cnt.small_in += 1;
                            } else {
                                cnt.large_in += 1;
                            }
                            match mode {
                                Mode::CardEcho => {
                                    // Minimal card-only echo (marks Memcached binary response magic=0x81, status=0)
                                    let mut tmp = [0u8; MAX_MC_BYTES];
                                    tmp[..mc_len].copy_from_slice(mc_slice);
                                    tmp[8] = 0x81; // PROTOCOL_BINARY_RES
                                    tmp[14] = 0;
                                    tmp[15] = 0;
                                    if let Some(p) = local_peers[tid].as_ref() {
                                        send_mc_to_eth(
                                            eth_pool_ref,
                                            &mut inp.eth_qp,
                                            &mut eth_tx,
                                            p,
                                            rpc_seq,
                                            &tmp[..mc_len],
                                            &mut cnt,
                                        );
                                        cnt.rpcs_out += 1;
                                    }
                                }
                                Mode::Sdp => {
                                    if let (Some(sqp), Some(spool)) = (inp.sdp_qp.as_mut(), sdp_pool_ref) {
                                        host_t0[(rpc_seq as usize) & TIMING_MASK] = rdtsc_after_loads();
                                        if let Some(mut m) = spool.alloc() {
                                            let flen = SDP_HDR_LEN + total_bytes;
                                            if let Some(buf) = m.append(flen as u16) {
                                                let hdr = build_sdp_hdr(sdp_host_mac, sdp_card_mac, core_idx, total_bytes);
                                                buf[..SDP_HDR_LEN].copy_from_slice(&hdr);
                                                let head_w0 = pack_wire_meta(rpc_seq, mc_len_u16, 0, frag_cnt, OP_NET);
                                                buf[SDP_HDR_LEN..SDP_HDR_LEN + 8].copy_from_slice(&head_w0.to_le_bytes());
                                                buf[SDP_HDR_LEN + 8..SDP_HDR_LEN + 8 + mc_len].copy_from_slice(mc_slice);
                                                // Stamp final 8 B with head_w0 so octeon_ep rx_poll_tail_off=48 validates full frame!
                                                buf[flen - 8..flen].copy_from_slice(&head_w0.to_le_bytes());
                                                if let Err(m_back) = sdp_tx.push(m) {
                                                    flush_qp(sqp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                                    let _ = sdp_tx.push(m_back);
                                                }
                                            }
                                        } else {
                                            cnt.sdp_tx_drops += 1;
                                        }
                                    }
                                }
                                Mode::Direct => {
                                    if local_host_gen == 0 || wc_ptr.is_null() {
                                        return;
                                    }
                                    host_t0[(rpc_seq as usize) & TIMING_MASK] = rdtsc_after_loads();
                                    if mc_len <= OP_PUSH_MAX_MC_BYTES {
                                        let cells = push_cells_for_mc_len(mc_len);
                                        let my_rx = rx_seq;
                                        rx_seq += 1;
                                        let slot_ptr = unsafe { wc_ptr.add(rx_ring_offset(core_idx, my_rx)) };
                                        // Write extra cells 1..cells FIRST, then Cell 0 LAST
                                        for c in 1..cells {
                                            let mut cell = McCell::default();
                                            cell.w[0] = pack_cell_meta(my_rx, mc_len_u16, c as u8, tid as u8, OP_PUSH);
                                            let start = c * 48;
                                            let end = (start + 48).min(mc_len);
                                            cell.payload_bytes_mut()[..(end - start)]
                                                .copy_from_slice(&mc_slice[start..end]);
                                            cell.seal();
                                            unsafe { store64_gathered(slot_ptr.add(c * 64), &cell.w) };
                                        }
                                        let mut cell0 = McCell::default();
                                        cell0.w[0] = pack_cell_meta(my_rx, mc_len_u16, cells as u8, tid as u8, OP_PUSH);
                                        let first_len = mc_len.min(48);
                                        cell0.payload_bytes_mut()[..first_len].copy_from_slice(&mc_slice[..first_len]);
                                        cell0.seal();
                                        unsafe { store64_gathered(slot_ptr, &cell0.w) };
                                    } else if let Some(dma) = inp.dma.as_mut() {
                                        let lslot = large_rx_next & LARGE_SLOT_MASK;
                                        large_rx_next = large_rx_next.wrapping_add(1);
                                        let src_iova = inp.reasm_iova + (reasm_slot * MAX_RPC_BYTES) as u64;
                                        let dst_iova = host_rx_iova + large_rx_offset(core_idx, lslot) as u64;
                                        loop {
                                            match dma.copy_vchan(VCHAN_TO_HOST, src_iova, dst_iova, total_bytes as u32) {
                                                Ok(true) => {
                                                    dma_to_queued = true;
                                                    to_inflight.push_back(ToHostJob {
                                                        rpc_seq,
                                                        mc_len: mc_len_u16,
                                                        total_bytes,
                                                        frag_cnt,
                                                        tid: tid as u8,
                                                        large_slot: lslot,
                                                    });
                                                    break;
                                                }
                                                Ok(false) => {
                                                    let _ = dma.submit_vchan(VCHAN_TO_HOST);
                                                    let (n, failed) = dma.completed_vchan(VCHAN_TO_HOST, 32);
                                                    if failed {
                                                        cnt.dma_failed += 1;
                                                    }
                                                    for _ in 0..n {
                                                        if let Some(job) = to_inflight.pop_front() {
                                                            let my_rx = rx_seq;
                                                            rx_seq += 1;
                                                            let mut notify = McCell::default();
                                                            notify.w[0] = pack_cell_meta(
                                                                my_rx,
                                                                job.mc_len,
                                                                job.frag_cnt,
                                                                job.tid,
                                                                OP_DMA,
                                                            );
                                                            notify.w[1] = job.rpc_seq;
                                                            notify.w[2] = job.large_slot as u64;
                                                            notify.w[3] = job.total_bytes as u64;
                                                            notify.seal();
                                                            unsafe {
                                                                store64_gathered(
                                                                    wc_ptr.add(rx_ring_offset(core_idx, my_rx)),
                                                                    &notify.w,
                                                                )
                                                            };
                                                        }
                                                    }
                                                }
                                                Err(_) => {
                                                    cnt.dma_failed += 1;
                                                    break;
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        });

                        if dma_to_queued {
                            if let Some(dma) = inp.dma.as_mut() {
                                let _ = dma.submit_vchan(VCHAN_TO_HOST);
                            }
                        }
                        if !sdp_tx.is_empty() {
                            if let Some(sqp) = inp.sdp_qp.as_mut() {
                                flush_qp(sqp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                            }
                        }
                        if !eth_tx.is_empty() {
                            flush_qp(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                        }
                    }

                    // ---------------------------------------------------------
                    // 2. Host-side completion & reply forwarding to `eth0`
                    // ---------------------------------------------------------
                    match mode {
                        Mode::CardEcho => {}
                        Mode::Sdp => {
                            if let Some(sqp) = inp.sdp_qp.as_mut() {
                                let n_sdp = sqp.rx(&mut sdp_rx);
                                if n_sdp > 0 {
                                    let now = rdtsc_after_loads();
                                    sdp_rx.drain_into(|pkt| {
                                        let frame = pkt.data();
                                        if frame.len() < SDP_HDR_LEN + 64 {
                                            return;
                                        }
                                        if frame[12] != 0x08 || frame[13] != 0x00 || frame[23] != 17 {
                                            return;
                                        }
                                        sdp_host_mac.copy_from_slice(&frame[6..12]);
                                        let payload = &frame[SDP_HDR_LEN..];
                                        let w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
                                        let (rpc_seq, mc_len_u16, _fi, _fc, _op) = unpack_wire_meta(w0);
                                        let mc_len = mc_len_u16 as usize;
                                        if rpc_seq == 0 || mc_len < 32 || 8 + mc_len + 8 > payload.len() {
                                            cnt.bad_host += 1;
                                            return;
                                        }
                                        let t0 = host_t0[(rpc_seq as usize) & TIMING_MASK];
                                        if t0 != 0 {
                                            let ns = ((now.saturating_sub(t0)) as f64 * ticks_to_ns) as u64;
                                            hist.record_ns(ns);
                                        }
                                        let tid = ((rpc_seq >> 34) & 7) as usize;
                                        let pgen_now = sh_ref.peer_gen.load(Ordering::Acquire);
                                        if pgen_now != local_peer_gen || local_peers[tid].is_none() {
                                            let g = sh_ref.peers.lock().unwrap();
                                            local_peers = *g;
                                            local_peer_gen = pgen_now;
                                        }
                                        if let Some(p) = local_peers[tid].as_ref() {
                                            send_mc_to_eth(
                                                eth_pool_ref,
                                                &mut inp.eth_qp,
                                                &mut eth_tx,
                                                p,
                                                rpc_seq,
                                                &payload[8..8 + mc_len],
                                                &mut cnt,
                                            );
                                            cnt.rpcs_out += 1;
                                            if mc_len <= OP_PUSH_MAX_MC_BYTES {
                                                cnt.small_out += 1;
                                            } else {
                                                cnt.large_out += 1;
                                            }
                                        }
                                    });
                                    if !eth_tx.is_empty() {
                                        flush_qp(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                                    }
                                }
                            }
                        }
                        Mode::Direct => {
                            // 2a. Reap DPI ToHost completions -> notify host via PEM0 WC
                            if !to_inflight.is_empty() {
                                if let Some(dma) = inp.dma.as_mut() {
                                    let (n, failed) = dma.completed_vchan(VCHAN_TO_HOST, 32);
                                    if failed {
                                        cnt.dma_failed += 1;
                                    }
                                    for _ in 0..n {
                                        if let Some(job) = to_inflight.pop_front() {
                                            let my_rx = rx_seq;
                                            rx_seq += 1;
                                            let mut notify = McCell::default();
                                            notify.w[0] = pack_cell_meta(
                                                my_rx,
                                                job.mc_len,
                                                job.frag_cnt,
                                                job.tid,
                                                OP_DMA,
                                            );
                                            notify.w[1] = job.rpc_seq;
                                            notify.w[2] = job.large_slot as u64;
                                            notify.w[3] = job.total_bytes as u64;
                                            notify.seal();
                                            unsafe {
                                                store64_gathered(
                                                    wc_ptr.add(rx_ring_offset(core_idx, my_rx)),
                                                    &notify.w,
                                                )
                                            };
                                        }
                                    }
                                }
                            }

                            // 2b. Poll this core's exclusive BAR4 sub-ring for host replies
                            if local_host_gen != 0 && !bar_base_ptr.is_null() {
                                drained_push.clear();
                                let mut from_queued = false;
                                let bar_pos_before = bar_pos;

                                for _ in 0..MAX_DRAIN {
                                    let cell_ptr =
                                        unsafe { bar_ring_ptr.add(bar4_cell_offset(core_idx, bar_pos, bar4_stride)) };
                                    let poll_res = if use_bar4_nc {
                                        unsafe { McCell::poll_seq_ldpq(cell_ptr, bar_pos) }
                                    } else {
                                        unsafe { McCell::poll_seq(cell_ptr, bar_pos) }
                                    };
                                    match poll_res {
                                        CellPoll::Empty => break,
                                        CellPoll::Torn => {
                                            cnt.torn_bar4 += 1;
                                            break;
                                        }
                                        CellPoll::Ready(cell) => {
                                            let (_bseq, mc_len_u16, aux, tid, op) = unpack_cell_meta(cell.w[0]);
                                            if op == OP_PUSH {
                                                let cells = aux as usize;
                                                if cells == 0 || cells > 4 || (mc_len_u16 as usize) > OP_PUSH_MAX_MC_BYTES {
                                                    bar_pos += 1;
                                                    cnt.bad_host += 1;
                                                    continue;
                                                }
                                                let mut extra = [McCell::default(); 4];
                                                extra[0] = cell;
                                                let mut all_ready = true;
                                                for c in 1..cells {
                                                    let cp = unsafe {
                                                        bar_ring_ptr.add(bar4_cell_offset(
                                                            core_idx,
                                                            bar_pos + c as u64,
                                                            bar4_stride,
                                                        ))
                                                    };
                                                    let epoll = if use_bar4_nc {
                                                        unsafe { McCell::poll_seq_ldpq(cp, bar_pos + c as u64) }
                                                    } else {
                                                        unsafe { McCell::poll_seq(cp, bar_pos + c as u64) }
                                                    };
                                                    match epoll {
                                                        CellPoll::Ready(ec) => extra[c] = ec,
                                                        _ => {
                                                            all_ready = false;
                                                            break;
                                                        }
                                                    }
                                                }
                                                if !all_ready {
                                                    break;
                                                }
                                                bar_pos += cells as u64;
                                                drained_push.push(DrainedPush {
                                                    mc_len: mc_len_u16,
                                                    tid,
                                                    cells: cells as u8,
                                                    extra,
                                                });
                                            } else if op == OP_DMA {
                                                bar_pos += 1;
                                                let rpc_seq = cell.w[1];
                                                let src_iova = cell.w[2];
                                                let total_bytes = cell.w[3] as usize;
                                                let tx_slot = tx_stage_next & STAGE_MASK;
                                                tx_stage_next = tx_stage_next.wrapping_add(1);
                                                let dst_iova = inp.tx_stage_iova + (tx_slot * MAX_RPC_BYTES) as u64;
                                                if total_bytes == 0 || total_bytes > MAX_RPC_BYTES {
                                                    cnt.bad_host += 1;
                                                    continue;
                                                }
                                                if let Some(dma) = inp.dma.as_mut() {
                                                    match dma.copy_vchan(
                                                        VCHAN_FROM_HOST,
                                                        src_iova,
                                                        dst_iova,
                                                        total_bytes as u32,
                                                    ) {
                                                        Ok(true) => {
                                                            from_queued = true;
                                                            from_inflight.push_back(FromHostJob {
                                                                tx_slot,
                                                                rpc_seq,
                                                                mc_len: mc_len_u16,
                                                                frag_cnt: aux,
                                                                tid,
                                                            });
                                                        }
                                                        _ => {
                                                            cnt.dma_failed += 1;
                                                        }
                                                    }
                                                }
                                            } else {
                                                bar_pos += 1;
                                                cnt.bad_host += 1;
                                            }
                                        }
                                    }
                                }

                                // Update WC credit cursor if advanced past hysteresis
                                if bar_pos > bar_pos_before && !credit_ptr.is_null() {
                                    let consumed = bar_pos - 1;
                                    if consumed.saturating_sub(last_credit_sent) >= credit_hyst {
                                        last_credit_sent = consumed;
                                        let cw = [consumed, 0, 0, 0, 0, 0, 0, consumed];
                                        unsafe { store64_gathered(credit_ptr, &cw) };
                                    }
                                }

                                if from_queued {
                                    if let Some(dma) = inp.dma.as_mut() {
                                        let _ = dma.submit_vchan(VCHAN_FROM_HOST);
                                    }
                                }

                                // Emit drained OP_PUSH replies to `eth0`
                                if !drained_push.is_empty() {
                                    let now = rdtsc_after_loads();
                                    for dr in &drained_push {
                                        let mc_len = dr.mc_len as usize;
                                        for c in 0..(dr.cells as usize) {
                                            let off = c * 48;
                                            let take = (mc_len.saturating_sub(off)).min(48);
                                            push_mc_buf[off..off + take]
                                                .copy_from_slice(&dr.extra[c].payload_bytes()[..take]);
                                        }
                                        let mc_slice = &push_mc_buf[..mc_len];
                                        let rpc_seq = rpc_seq_from_opaque(mc_udp_opaque(mc_slice));
                                        let t0 = host_t0[(rpc_seq as usize) & TIMING_MASK];
                                        if t0 != 0 {
                                            let ns = ((now.saturating_sub(t0)) as f64 * ticks_to_ns) as u64;
                                            hist.record_ns(ns);
                                        }
                                        let tid = (dr.tid as usize) & (MAX_PEERS - 1);
                                        let pgen_now = sh_ref.peer_gen.load(Ordering::Acquire);
                                        if pgen_now != local_peer_gen || local_peers[tid].is_none() {
                                            let g = sh_ref.peers.lock().unwrap();
                                            local_peers = *g;
                                            local_peer_gen = pgen_now;
                                        }
                                        if let Some(p) = local_peers[tid].as_ref() {
                                            send_mc_to_eth(
                                                eth_pool_ref,
                                                &mut inp.eth_qp,
                                                &mut eth_tx,
                                                p,
                                                rpc_seq,
                                                mc_slice,
                                                &mut cnt,
                                            );
                                            cnt.rpcs_out += 1;
                                            cnt.small_out += 1;
                                        } else {
                                            cnt.bad_host += 1;
                                        }
                                    }
                                    if !eth_tx.is_empty() {
                                        flush_qp(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                                    }
                                }
                            }

                            // 2c. Reap DPI FromHost completions -> transmit out `eth0`
                            if !from_inflight.is_empty() {
                                if let Some(dma) = inp.dma.as_mut() {
                                    let (n, failed) = dma.completed_vchan(VCHAN_FROM_HOST, 32);
                                    if failed {
                                        cnt.dma_failed += 1;
                                    }
                                    if n > 0 {
                                        let now = rdtsc_after_loads();
                                        for _ in 0..n {
                                            if let Some(job) = from_inflight.pop_front() {
                                                let t0 = host_t0[(job.rpc_seq as usize) & TIMING_MASK];
                                                if t0 != 0 {
                                                    let ns = ((now.saturating_sub(t0)) as f64 * ticks_to_ns) as u64;
                                                    hist.record_ns(ns);
                                                }
                                                let mc_len = job.mc_len as usize;
                                                let _ = job.frag_cnt;
                                                let mc_slice = unsafe {
                                                    std::slice::from_raw_parts(
                                                        tx_stage_ptr.add(job.tx_slot * MAX_RPC_BYTES + 8),
                                                        mc_len,
                                                    )
                                                };
                                                let tid = (job.tid as usize) & (MAX_PEERS - 1);
                                                let pgen_now = sh_ref.peer_gen.load(Ordering::Acquire);
                                                if pgen_now != local_peer_gen || local_peers[tid].is_none() {
                                                    let g = sh_ref.peers.lock().unwrap();
                                                    local_peers = *g;
                                                    local_peer_gen = pgen_now;
                                                }
                                                if let Some(p) = local_peers[tid].as_ref() {
                                                    send_mc_to_eth(
                                                        eth_pool_ref,
                                                        &mut inp.eth_qp,
                                                        &mut eth_tx,
                                                        p,
                                                        job.rpc_seq,
                                                        mc_slice,
                                                        &mut cnt,
                                                    );
                                                    cnt.rpcs_out += 1;
                                                    cnt.large_out += 1;
                                                }
                                            }
                                        }
                                        if !eth_tx.is_empty() {
                                            flush_qp(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            });

            if let Some(srv) = _lease_srv {
                srv.close();
            }
            Ok(())
        })?;

        Ok(())
    }
}
