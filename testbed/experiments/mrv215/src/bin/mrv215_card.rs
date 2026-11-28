//! Multi-core CN106 100GbE SmartNIC bridge (`eth0` <-> `sdp1-1`) for MRV-215
//! (Linux Kernel `octeon_ep_vf` Zero-Modification Upstream Memcached 1.6.45
//! UDP Socket Control Benchmark).
//!
//! Bridges `mercury` (`mrv206_client` RoCEv2 UD / native UDP 11211) on `eth0`
//! (`0002:02:00.0`) to unmodified Linux kernel `octeon_ep_vf` (`10.10.200.1:11211`)
//! via standard Ethernet/IPv4/UDP frames on `sdp1-1` (`0002:01:00.2`):
//!   - Request path (`eth0 -> sdp1-1`): standard Eth/IPv4(valid csum)/UDP
//!     `10.10.200.2:(5000+tid) -> 10.10.200.1:11211` carrying the raw upstream
//!     Memcached Binary UDP datagram (with automatic RFC 791 IPv4 fragmentation
//!     if `20 + 8 + mc_len > --sdp-mtu`).
//!   - Reply path (`sdp1-1 -> eth0`): standard Eth/IPv4/UDP
//!     `10.10.200.1:11211 -> 10.10.200.2:(5000+tid)` from unmodified `memcached -U 11211`,
//!     handling both single-datagram replies (`<= 1400 B`) and multi-datagram replies
//!     (e.g. 3 x <=1400 B datagrams for `3840 B GET`) seamlessly, plus ARP responder.
//!   - Supports `--rx-mode default | cqe-peek | cqe-direct-safe` and 1..4 cores (`-l 3`
//!     for 1Q single-NAPI-ID busy_poll parity, `-l 3-6` for 4Q MRV-206 topology control).

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv215_card runs on the aarch64 CN106 card");
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
    use std::net::{Ipv4Addr, TcpListener};
    use std::os::fd::RawFd;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{
        Burst, CqePeekStats, Eal, EthStats, Mbuf, Mempool, Port, PortStats, QueuePair,
        rdtsc_after_loads,
    };
    use mrv215::{
        ETH_MBUF_BUF_SIZE, FastHist, MAX_CARD_CORES, MAX_DRAIN, MAX_FRAG_PAYLOAD, MAX_MC_BYTES,
        MAX_PEERS, MAX_RPC_BYTES, MAX_SEG_BYTES, MC_MAGIC_REQ, MC_UDP_MAX_PAYLOAD, MC_UDP_PORT,
        McReasmOutcome, McUdpReassembler, OP_FRAG, OP_PROBE_WIRE, OP_PUSH_MAX_MC_BYTES,
        ROCE_HDR_LEN, ROCE_ICRC_LEN, ROCE_UDP_PORT, RoceTemplate, SDP_CARD_IP, SDP_HOST_IP,
        SDP_MBUF_BUF_SIZE, SDP_MTU, SDP_SPORT_BASE, SYNC_ADDR, SdpRxKind, crc32_sw_update,
        emit_ipv4_udp_fragments, format_mac, mc_udp_opaque, pack_wire_meta, parse_mac,
        parse_sdp_frame, probe_cookie, roce_frag_cnt, rpc_seq_from_opaque, unpack_wire_meta,
        write_arp_reply_frame, write_ipv4_udp_frame,
    };

    const ETH_DESC: u16 = 2048;
    const SDP_DESC: u16 = 2048;
    const ETH_REASM_SLOTS: usize = 2048;
    const ETH_REASM_MASK: usize = ETH_REASM_SLOTS - 1;
    const HOST_T0_SLOTS: usize = MAX_PEERS * 1024;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum RxMode {
        Default,
        CqePeek,
        CqeDirectSafe,
    }

    #[derive(Parser)]
    struct Args {
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
        #[arg(long, default_value_t = SDP_MTU)]
        sdp_mtu: u16,
        #[arg(long, default_value = "ff:ff:ff:ff:ff:ff")]
        host_mac: String,
        #[arg(long, default_value = "10.10.200.1")]
        host_ip: String,
        #[arg(long, default_value = "10.10.200.2")]
        card_sdp_ip: String,
        #[arg(long, default_value = "10.10.100.2")]
        eth_ip: String,
        #[arg(long, default_value = SYNC_ADDR)]
        sync: String,
    }

    // -------------------------------------------------------------------------
    // CN106 TAD PMU (100% non-multiplexed, identical to MRV-206)
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
                    libc::read(
                        *fd,
                        buf.as_mut_ptr().cast::<libc::c_void>(),
                        std::mem::size_of::<[u64; 3]>(),
                    )
                };
                if n == std::mem::size_of::<[u64; 3]>() as isize {
                    let pct = if buf[1] == 0 {
                        100.0
                    } else {
                        (buf[2] as f64 / buf[1] as f64) * 100.0
                    };
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
        eth_tx_drops: u64,
        sdp_tx_drops: u64,
        sdp_rx_dgrams: u64,
        sdp_reasm_ok: u64,
        sdp_reasm_err: u64,
        sdp_arp_replies: u64,
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
            self.eth_tx_drops += o.eth_tx_drops;
            self.sdp_tx_drops += o.sdp_tx_drops;
            self.sdp_rx_dgrams += o.sdp_rx_dgrams;
            self.sdp_reasm_ok += o.sdp_reasm_ok;
            self.sdp_reasm_err += o.sdp_reasm_err;
            self.sdp_arp_replies += o.sdp_arp_replies;
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

    #[inline(always)]
    fn host_t0_slot(rpc_seq: u64) -> usize {
        let tid = ((rpc_seq >> 34) as usize) & (MAX_PEERS - 1);
        let lseq = (rpc_seq as usize) & 1023;
        (tid << 10) | lseq
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
        host_mac: Mutex<[u8; 6]>,
        host_mac_gen: AtomicU64,
        host_t0: Box<[AtomicU64]>,
        card_eth_mac: [u8; 6],
        card_eth_ip: [u8; 4],
        card_sdp_mac: [u8; 6],
        card_sdp_ip: [u8; 4],
        host_sdp_ip: [u8; 4],
        sdp_mtu: usize,
    }

    impl Shared {
        fn new(
            n_cores: usize,
            card_eth_mac: [u8; 6],
            card_eth_ip: [u8; 4],
            card_sdp_mac: [u8; 6],
            card_sdp_ip: [u8; 4],
            host_sdp_ip: [u8; 4],
            host_mac: [u8; 6],
            sdp_mtu: usize,
        ) -> Self {
            let mut host_t0 = Vec::with_capacity(HOST_T0_SLOTS);
            for _ in 0..HOST_T0_SLOTS {
                host_t0.push(AtomicU64::new(0));
            }
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
                host_mac: Mutex::new(host_mac),
                host_mac_gen: AtomicU64::new(1),
                host_t0: host_t0.into_boxed_slice(),
                card_eth_mac,
                card_eth_ip,
                card_sdp_mac,
                card_sdp_ip,
                host_sdp_ip,
                sdp_mtu,
            }
        }

        fn set_host_mac(&self, mac: [u8; 6]) {
            let mut g = self.host_mac.lock().unwrap();
            if *g != mac {
                *g = mac;
                self.host_mac_gen.fetch_add(1, Ordering::Release);
            }
        }

        fn get_host_mac(&self) -> [u8; 6] {
            *self.host_mac.lock().unwrap()
        }

        fn register_roce_peer(&self, tid: usize, mac: [u8; 6], ip: [u8; 4], qpn: u32) {
            let p = ClientPeer::Roce(RocePeer::new(self.card_eth_mac, self.card_eth_ip, mac, ip, qpn));
            let mut g = self.peers.lock().unwrap();
            g[tid & (MAX_PEERS - 1)] = Some(p);
            self.peer_gen.fetch_add(1, Ordering::Release);
        }

        fn register_std_udp_peer(&self, tid: usize, mac: [u8; 6], ip: [u8; 4], sport: u16) {
            let p = ClientPeer::StdUdp(StdUdpPeer {
                mac,
                ip,
                sport,
                card_mac: self.card_eth_mac,
                card_ip: self.card_eth_ip,
            });
            let mut g = self.peers.lock().unwrap();
            g[tid & (MAX_PEERS - 1)] = Some(p);
            self.peer_gen.fetch_add(1, Ordering::Release);
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
    struct EthReasmMeta {
        rpc_seq: u64,
        mc_len: u16,
        mask: u16,
        frag_cnt: u8,
    }

    fn read_xstats_json(st: &PortStats<'_, '_>) -> String {
        let Ok(xs) = st.xstats() else {
            return "{}".to_string();
        };
        let mut parts = Vec::new();
        for (name, val) in xs {
            if (name.contains("drop")
                || name.contains("err")
                || name.contains("miss")
                || name.contains("nombuf")
                || name.contains("pause")
                || name.contains("fcs"))
                && val > 0
            {
                parts.push(format!("\"{name}\":{val}"));
            }
        }
        format!("{{{}}}", parts.join(","))
    }

    #[allow(clippy::too_many_arguments)]
    fn format_stats(
        rx_mode: RxMode,
        n_cores: usize,
        tot: &CoreSnapshot,
        hist: &FastHist,
        per_core: &[CoreSnapshot],
        eth_st: &EthStats,
        eth_xstats: &str,
        sdp_st: &EthStats,
    ) -> String {
        let per_in: Vec<String> = per_core.iter().map(|c| c.rpcs_in.to_string()).collect();
        let per_out: Vec<String> = per_core.iter().map(|c| c.rpcs_out.to_string()).collect();
        format!(
            "{{\"mode\":\"KernelSdp\",\"rx_mode\":\"{:?}\",\"n_cores\":{},\"eth_rx_pkts\":{},\"eth_tx_pkts\":{},\"rpcs_in\":{},\"rpcs_out\":{},\"small_in\":{},\"large_in\":{},\"small_out\":{},\"large_out\":{},\"per_core_in\":[{}],\"per_core_out\":[{}],\"bad_wire\":{},\"bad_host\":{},\"torn_bar4\":0,\"dma_failed\":0,\"eth_tx_drops\":{},\"sdp_tx_drops\":{},\"sdp_rx_dgrams\":{},\"sdp_reasm_ok\":{},\"sdp_reasm_err\":{},\"sdp_arp_replies\":{},\"probes\":{},\"cqe\":{{\"peek_empty\":{},\"peek_hit\":{},\"ldadda_calls\":{},\"pkts_popped\":{},\"w9_wait_retries\":{},\"watchdog_ldadda\":{},\"watchdog_head_mismatch\":{},\"err_pkts_freed\":{},\"mseg_pkts\":{},\"fallback_ldadda\":{}}},\"host_rtt_cnt\":{},\"host_rtt_p50_us\":{:.3},\"host_rtt_p99_us\":{:.3},\"host_rtt_mean_us\":{:.3},\"eth_ipackets\":{},\"eth_opackets\":{},\"eth_ibytes\":{},\"eth_obytes\":{},\"eth_imissed\":{},\"eth_ierrors\":{},\"eth_oerrors\":{},\"eth_rx_nombuf\":{},\"eth_xstats_nz\":{},\"sdp_ipackets\":{},\"sdp_opackets\":{},\"sdp_imissed\":{},\"sdp_ierrors\":{},\"sdp_oerrors\":{},\"sdp_rx_nombuf\":{}}}",
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
            tot.eth_tx_drops,
            tot.sdp_tx_drops,
            tot.sdp_rx_dgrams,
            tot.sdp_reasm_ok,
            tot.sdp_reasm_err,
            tot.sdp_arp_replies,
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
            sdp_st.ipackets,
            sdp_st.opackets,
            sdp_st.imissed,
            sdp_st.ierrors,
            sdp_st.oerrors,
            sdp_st.rx_nombuf
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
        my_mac: [u8; 6],
        my_ip: [u8; 4],
        dst_mac: [u8; 6],
        dst_ip: [u8; 4],
    ) -> Option<Mbuf<'e>> {
        let mut m = pool.alloc()?;
        let dst = m.append(60)?;
        let mut arr = [0u8; 60];
        write_arp_reply_frame(my_mac, my_ip, dst_mac, dst_ip, &mut arr);
        dst.copy_from_slice(&arr);
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
            tpl.crc_seed = crc32_sw_update(0xffff_ffff, &masked);
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
        let flen = (42 + dgram.len()).max(60);
        let dst = m.append(flen as u16)?;
        let written = write_ipv4_udp_frame(
            peer.mac,
            peer.card_mac,
            peer.card_ip,
            peer.ip,
            MC_UDP_PORT,
            peer.sport,
            0x2150,
            dgram,
            dst,
        );
        debug_assert_eq!(written, flen);
        Some(m)
    }

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
    fn eth_reasm_index(rpc_seq: u64) -> usize {
        let tid = ((rpc_seq >> 34) & 7) as usize;
        let lseq = (rpc_seq & 255) as usize;
        (tid * 256 + lseq) & ETH_REASM_MASK
    }

    fn parse_ipv4(s: &str, fallback: [u8; 4]) -> [u8; 4] {
        s.parse::<Ipv4Addr>().map(|a| a.octets()).unwrap_or(fallback)
    }

    struct WorkerInput<'p, 'e> {
        eth_qp: QueuePair<'p, 'e>,
        sdp_qp: QueuePair<'p, 'e>,
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

        // Safety guard: never allow binding control-plane sdp1-0 device.
        if args.sdp_dev.contains("sdp1-0") || args.sdp_dev.trim() == "0002:01:00.1" {
            return Err(format!("REFUSED: control-plane device '{}' is forbidden", args.sdp_dev).into());
        }

        let host_mac = parse_mac(&args.host_mac).map_err(|e| format!("--host-mac: {e}"))?;
        let host_sdp_ip = parse_ipv4(&args.host_ip, SDP_HOST_IP);
        let card_sdp_ip = parse_ipv4(&args.card_sdp_ip, SDP_CARD_IP);
        let card_eth_ip = parse_ipv4(&args.eth_ip, [10, 10, 100, 2]);
        let sdp_mtu = (args.sdp_mtu as usize).clamp(576, SDP_MTU as usize);

        let (eal, _) = Eal::init(eal_args)?;
        let n_cores = eal.lcores();
        if n_cores == 0 || n_cores > MAX_CARD_CORES {
            return Err(format!("expected 1..={} lcores, got {}", MAX_CARD_CORES, n_cores).into());
        }
        let tsc_hz = eal.tsc_hz() as f64;
        let ticks_to_ns = 1e9 / tsc_hz;

        let tad_pmu = Arc::new(TadPmu::open()?);

        // 1. Physical 100GbE `eth0` (1..4 RxQ/TxQ RSS)
        let eth_id = Port::by_name(&eal, &args.eth_dev)?;
        let eth_sock = Port::socket_of(&eal, eth_id);
        let eth_pool_sz = (16384 * n_cores as u32).min(65535);
        let eth_pool = Mempool::new_with_buf_size(&eal, "m215_eth_pool", eth_pool_sz, ETH_MBUF_BUF_SIZE, eth_sock)?;
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
        let card_eth_mac = eth_port.mac();

        // 2. Data-plane `sdp1-1` (`0002:01:00.2`) facing host Linux `octeon_ep_vf` VF0
        let sdp_id = Port::by_name(&eal, &args.sdp_dev)?;
        let sdp_sock = Port::socket_of(&eal, sdp_id);
        let sdp_pool = Mempool::new_with_buf_size(&eal, "m215_sdp_pool", 16384, SDP_MBUF_BUF_SIZE, sdp_sock)?;
        let mut sdp_port = Port::start_with_opts_rss(
            &eal,
            sdp_id,
            &sdp_pool,
            n_cores as u16,
            SDP_DESC,
            SDP_DESC,
            Some(SDP_MTU),
            !args.no_sdp_rss && n_cores > 1,
        )?;
        sdp_port.promiscuous()?;
        let card_sdp_mac = sdp_port.mac();

        let shared = Arc::new(Shared::new(
            n_cores,
            card_eth_mac,
            card_eth_ip,
            card_sdp_mac,
            card_sdp_ip,
            host_sdp_ip,
            host_mac,
            sdp_mtu,
        ));
        {
            let sh = Arc::clone(&shared);
            ctrlc::set_handler(move || sh.quit.store(true, Ordering::Relaxed))?;
        }

        let sync_listener = TcpListener::bind(&args.sync)?;
        sync_listener.set_nonblocking(true)?;

        let (eth_stats, eth_qps) = eth_port.split_queues();
        let _ = eth_stats.stats_reset();
        let _ = eth_stats.xstats_reset();

        let (sdp_stats, sdp_qps) = sdp_port.split_queues();
        let _ = sdp_stats.stats_reset();

        let mut inputs: Vec<WorkerInput<'_, '_>> = Vec::with_capacity(n_cores);
        for (eth_qp, sdp_qp) in eth_qps.into_iter().zip(sdp_qps.into_iter()) {
            inputs.push(WorkerInput { eth_qp, sdp_qp });
        }

        let rx_mode = args.rx_mode;
        let watchdog_interval = args.watchdog_interval;

        std::thread::scope(|scope| -> Result<(), Box<dyn Error>> {
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
                                let trimmed = line.trim();
                                let mut parts = trimmed.split_whitespace();
                                let cmd = parts.next().unwrap_or("");
                                let arg1 = parts.next();
                                let reply = match cmd {
                                    "PING" => "{\"ok\":true}".to_string(),
                                    "INFO" => format!(
                                        "{{\"eth_mac\":\"{}\",\"sdp_mac\":\"{}\",\"host_mac\":\"{}\",\"n_cores\":{},\"sdp_mtu\":{}}}",
                                        format_mac(sh_srv.card_eth_mac),
                                        format_mac(sh_srv.card_sdp_mac),
                                        format_mac(sh_srv.get_host_mac()),
                                        sh_srv.n_cores,
                                        sh_srv.sdp_mtu
                                    ),
                                    "SET_HOST_MAC" => match arg1.and_then(|s| parse_mac(s).ok()) {
                                        Some(m) => {
                                            sh_srv.set_host_mac(m);
                                            format!("{{\"ok\":true,\"host_mac\":\"{}\"}}", format_mac(m))
                                        }
                                        None => "{\"ok\":false,\"err\":\"bad_mac\"}".to_string(),
                                    },
                                    "PMU_SNAP" => pmu_srv.snap_json(),
                                    "RESET" => {
                                        let _ = sh_srv.run_cmd(1);
                                        let _ = eth_stats_srv.stats_reset();
                                        let _ = eth_stats_srv.xstats_reset();
                                        let _ = sdp_stats_srv.stats_reset();
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
                                        let _ = sdp_stats_srv.stats_reset();
                                        "{\"ok\":true}".to_string()
                                    }
                                    "STATS" => match sh_srv.run_cmd(2) {
                                        Some((tot, hist, per_core)) => {
                                            let est = eth_stats_srv.stats().unwrap_or_default();
                                            let exs = read_xstats_json(&eth_stats_srv);
                                            let sst = sdp_stats_srv.stats().unwrap_or_default();
                                            format_stats(
                                                rx_mode,
                                                n_cores,
                                                &tot,
                                                &hist,
                                                &per_core,
                                                &est,
                                                &exs,
                                                &sst,
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

            println!(
                "MRV215_CARD kernel_sdp ready n_cores={} rx_mode={:?} eth_mac={} sdp_mac={} host_mac={} sdp_mtu={} tsc_hz={:.0}",
                n_cores,
                rx_mode,
                format_mac(card_eth_mac),
                format_mac(card_sdp_mac),
                format_mac(host_mac),
                sdp_mtu,
                tsc_hz
            );
            let _ = std::io::stdout().flush();

            let eth_pool_ref = &eth_pool;
            let sdp_pool_ref = &sdp_pool;
            let sh_ref = &shared;

            eal.on_each_lcore(inputs, move |core_idx, mut inp| {
                let mut eth_rx = Burst::<MAX_DRAIN>::new();
                let mut eth_tx = Burst::<MAX_DRAIN>::new();
                let mut sdp_rx = Burst::<MAX_DRAIN>::new();
                let mut sdp_tx = Burst::<MAX_DRAIN>::new();

                let mut local_peers: [Option<ClientPeer>; MAX_PEERS] = [None; MAX_PEERS];
                let mut local_peer_gen: u64 = 0;
                let mut local_host_mac = sh_ref.get_host_mac();
                let mut local_host_mac_gen = sh_ref.host_mac_gen.load(Ordering::Acquire);

                let mut eth_reasm_meta = vec![EthReasmMeta::default(); ETH_REASM_SLOTS];
                let mut eth_reasm_buf = vec![0u8; ETH_REASM_SLOTS * MAX_RPC_BYTES];
                let mut mc_reasm = McUdpReassembler::new();

                let mut cnt = CoreSnapshot::default();
                let mut hist = FastHist::new();
                let mut handled_cmd_gen: u64 = 0;

                let mut iter: u64 = 0;
                let mut ip_id_ctr: u16 = ((core_idx as u16) << 12).wrapping_add(1);
                let mut std_udp_seq: u64 = 0;

                while !sh_ref.quit.load(Ordering::Relaxed) {
                    iter = iter.wrapping_add(1);

                    // Periodic control & peer/host-MAC sync
                    if (iter & 0x3ff) == 0 {
                        let pgen = sh_ref.peer_gen.load(Ordering::Acquire);
                        if pgen != local_peer_gen {
                            let g = sh_ref.peers.lock().unwrap();
                            local_peers = *g;
                            local_peer_gen = pgen;
                        }
                        let mgen = sh_ref.host_mac_gen.load(Ordering::Acquire);
                        if mgen != local_host_mac_gen {
                            local_host_mac = sh_ref.get_host_mac();
                            local_host_mac_gen = mgen;
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
                                mc_reasm.reset();
                                for rm in &mut eth_reasm_meta {
                                    *rm = EthReasmMeta::default();
                                }
                                let pgen_now = sh_ref.peer_gen.load(Ordering::Acquire);
                                let pg = sh_ref.peers.lock().unwrap();
                                local_peers = *pg;
                                local_peer_gen = pgen_now;
                            } else if cmd == 2 {
                                sh_ref.per_core_snap.lock().unwrap()[core_idx] = cnt.clone();
                                sh_ref.per_core_hist.lock().unwrap()[core_idx].reset();
                                sh_ref.per_core_hist.lock().unwrap()[core_idx].merge_from(&hist);
                            }
                            handled_cmd_gen = g;
                            sh_ref.done_cnt.fetch_add(1, Ordering::Release);
                        }
                    }

                    // ---------------------------------------------------------
                    // 1. Poll `eth0` RX -> forward standard IPv4/UDP to `sdp1-1`
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
                        eth_rx.drain_into(|pkt| {
                            let frame = pkt.data();
                            let Some(rxk) = parse_eth_packet(frame, sh_ref.card_eth_ip) else {
                                return;
                            };
                            let (rpc_seq, mc_len, tid, mc_slice): (u64, usize, usize, &[u8]) = match rxk {
                                EthRxKind::ArpReq { src_mac, src_ip } => {
                                    if let Some(m) = build_arp_reply_mbuf(
                                        eth_pool_ref,
                                        sh_ref.card_eth_mac,
                                        sh_ref.card_eth_ip,
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
                                        sh_ref.register_std_udp_peer(tid, src_mac, src_ip, sport);
                                        let pg = sh_ref.peers.lock().unwrap();
                                        local_peers = *pg;
                                        local_peer_gen = sh_ref.peer_gen.load(Ordering::Acquire);
                                    }
                                    std_udp_seq = std_udp_seq.wrapping_add(1);
                                    let rpc_seq = ((tid as u64) << 34) | ((std_udp_seq & 0x1fff_ffff).max(1));
                                    (rpc_seq, mc_len, tid, payload)
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
                                        let w_tail = u64::from_le_bytes(
                                            payload[payload.len() - 8..payload.len()].try_into().unwrap(),
                                        );
                                        let (rpc_seq, _, _, _, _) = unpack_wire_meta(w0);
                                        let cookie = u64::from_le_bytes(payload[16..24].try_into().unwrap());
                                        if w0 == w_tail && cookie == probe_cookie(rpc_seq) {
                                            cnt.probes += 1;
                                            let tmp_peer = RocePeer::new(
                                                sh_ref.card_eth_mac,
                                                sh_ref.card_eth_ip,
                                                src_mac,
                                                src_ip,
                                                src_qpn,
                                            );
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
                                            sh_ref.register_roce_peer(tid, src_mac, src_ip, src_qpn);
                                            let pg = sh_ref.peers.lock().unwrap();
                                            local_peers = *pg;
                                            local_peer_gen = sh_ref.peer_gen.load(Ordering::Acquire);
                                        }
                                        (rpc_seq, mc_len, tid, payload)
                                    } else if tag_byte == OP_FRAG {
                                        if payload.len() != MAX_SEG_BYTES {
                                            cnt.bad_wire += 1;
                                            return;
                                        }
                                        let w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
                                        let w_tail = u64::from_le_bytes(
                                            payload[MAX_SEG_BYTES - 8..MAX_SEG_BYTES].try_into().unwrap(),
                                        );
                                        let (rpc_seq, mc_len_u16, frag_idx, frag_cnt, _op) = unpack_wire_meta(w0);
                                        let mc_len = mc_len_u16 as usize;
                                        if rpc_seq == 0
                                            || w0 != w_tail
                                            || !(2..=4).contains(&frag_cnt)
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
                                            sh_ref.register_roce_peer(tid, src_mac, src_ip, src_qpn);
                                            let pg = sh_ref.peers.lock().unwrap();
                                            local_peers = *pg;
                                            local_peer_gen = sh_ref.peer_gen.load(Ordering::Acquire);
                                        }
                                        let slot = eth_reasm_index(rpc_seq);
                                        let rm = &mut eth_reasm_meta[slot];
                                        if rm.rpc_seq != rpc_seq {
                                            rm.rpc_seq = rpc_seq;
                                            rm.mc_len = mc_len_u16;
                                            rm.mask = 0;
                                            rm.frag_cnt = frag_cnt;
                                        }
                                        let start = (frag_idx as usize) * MAX_FRAG_PAYLOAD;
                                        let copy_len = MAX_FRAG_PAYLOAD.min(mc_len.saturating_sub(start));
                                        let dst_base = slot * MAX_RPC_BYTES + start;
                                        eth_reasm_buf[dst_base..dst_base + copy_len]
                                            .copy_from_slice(&payload[8..8 + copy_len]);
                                        rm.mask |= 1u16 << frag_idx;
                                        let full_mask = (1u16 << frag_cnt) - 1;
                                        if rm.mask != full_mask {
                                            return;
                                        }
                                        rm.mask = 0;
                                        let base = slot * MAX_RPC_BYTES;
                                        (rpc_seq, mc_len, tid, &eth_reasm_buf[base..base + mc_len])
                                    } else {
                                        cnt.bad_wire += 1;
                                        return;
                                    }
                                }
                            };

                            cnt.rpcs_in += 1;
                            if mc_len <= OP_PUSH_MAX_MC_BYTES {
                                cnt.small_in += 1;
                            } else {
                                cnt.large_in += 1;
                            }
                            sh_ref.host_t0[host_t0_slot(rpc_seq)]
                                .store(rdtsc_after_loads(), Ordering::Relaxed);

                            let sport = SDP_SPORT_BASE + (tid as u16);
                            ip_id_ctr = ip_id_ctr.wrapping_add(1);
                            let ip_tot = 20 + 8 + mc_len;
                            if ip_tot <= sh_ref.sdp_mtu {
                                if let Some(mut m) = sdp_pool_ref.alloc() {
                                    let flen = (42 + mc_len).max(60);
                                    if let Some(buf) = m.append(flen as u16) {
                                        write_ipv4_udp_frame(
                                            local_host_mac,
                                            sh_ref.card_sdp_mac,
                                            sh_ref.card_sdp_ip,
                                            sh_ref.host_sdp_ip,
                                            sport,
                                            MC_UDP_PORT,
                                            ip_id_ctr,
                                            mc_slice,
                                            buf,
                                        );
                                        if let Err(m_back) = sdp_tx.push(m) {
                                            flush_qp(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                            let _ = sdp_tx.push(m_back);
                                        }
                                    } else {
                                        cnt.sdp_tx_drops += 1;
                                    }
                                } else {
                                    cnt.sdp_tx_drops += 1;
                                }
                            } else {
                                emit_ipv4_udp_fragments(
                                    local_host_mac,
                                    sh_ref.card_sdp_mac,
                                    sh_ref.card_sdp_ip,
                                    sh_ref.host_sdp_ip,
                                    sport,
                                    MC_UDP_PORT,
                                    ip_id_ctr,
                                    sh_ref.sdp_mtu,
                                    mc_slice,
                                    |frag_bytes| {
                                        if let Some(mut m) = sdp_pool_ref.alloc() {
                                            if let Some(buf) = m.append(frag_bytes.len() as u16) {
                                                buf.copy_from_slice(frag_bytes);
                                                if let Err(m_back) = sdp_tx.push(m) {
                                                    flush_qp(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                                    let _ = sdp_tx.push(m_back);
                                                }
                                            } else {
                                                cnt.sdp_tx_drops += 1;
                                            }
                                        } else {
                                            cnt.sdp_tx_drops += 1;
                                        }
                                    },
                                );
                            }
                        });

                        if !sdp_tx.is_empty() {
                            flush_qp(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                        }
                        if !eth_tx.is_empty() {
                            flush_qp(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                        }
                    }

                    // ---------------------------------------------------------
                    // 2. Poll `sdp1-1` RX from Linux `octeon_ep_vf` -> `eth0`
                    // ---------------------------------------------------------
                    let n_sdp = inp.sdp_qp.rx(&mut sdp_rx);
                    if n_sdp > 0 {
                        let now = rdtsc_after_loads();
                        sdp_rx.drain_into(|pkt| {
                            let frame = pkt.data();
                            let Some(sk) = parse_sdp_frame(frame, sh_ref.card_sdp_ip) else {
                                return;
                            };
                            match sk {
                                SdpRxKind::ArpReq { src_mac, src_ip } => {
                                    if src_ip == sh_ref.host_sdp_ip {
                                        local_host_mac = src_mac;
                                        sh_ref.set_host_mac(src_mac);
                                    }
                                    if let Some(m) = build_arp_reply_mbuf(
                                        sdp_pool_ref,
                                        sh_ref.card_sdp_mac,
                                        sh_ref.card_sdp_ip,
                                        src_mac,
                                        src_ip,
                                    ) {
                                        if let Err(m_back) = sdp_tx.push(m) {
                                            flush_qp(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                            let _ = sdp_tx.push(m_back);
                                        }
                                        cnt.sdp_arp_replies += 1;
                                    }
                                }
                                SdpRxKind::Udp { src_mac, src_ip: _, sport, dport, payload } => {
                                    if sport != MC_UDP_PORT
                                        || dport < SDP_SPORT_BASE
                                        || dport >= SDP_SPORT_BASE + (MAX_PEERS as u16)
                                    {
                                        return;
                                    }
                                    if local_host_mac == [0xff; 6] {
                                        local_host_mac = src_mac;
                                        sh_ref.set_host_mac(src_mac);
                                    }
                                    let tid = (dport - SDP_SPORT_BASE) as usize;
                                    cnt.sdp_rx_dgrams += 1;

                                    let (rpc_seq, mc_slice): (u64, &[u8]) = match mc_reasm.ingest(tid, payload) {
                                        McReasmOutcome::Single { rpc_seq } => (rpc_seq, payload),
                                        McReasmOutcome::Complete { rpc_seq, slot, len } => {
                                            cnt.sdp_reasm_ok += 1;
                                            (rpc_seq, mc_reasm.slot_bytes(slot, len))
                                        }
                                        McReasmOutcome::Incomplete => return,
                                        McReasmOutcome::Error => {
                                            cnt.sdp_reasm_err += 1;
                                            cnt.bad_host += 1;
                                            return;
                                        }
                                    };

                                    let t0 = sh_ref.host_t0[host_t0_slot(rpc_seq)].load(Ordering::Relaxed);
                                    if t0 != 0 {
                                        let ns = ((now.saturating_sub(t0)) as f64 * ticks_to_ns) as u64;
                                        hist.record_ns(ns);
                                    }

                                    let pgen_now = sh_ref.peer_gen.load(Ordering::Acquire);
                                    if pgen_now != local_peer_gen || local_peers[tid].is_none() {
                                        let g = sh_ref.peers.lock().unwrap();
                                        local_peers = *g;
                                        local_peer_gen = pgen_now;
                                    }
                                    if let Some(p) = local_peers[tid].as_ref() {
                                        let mc_len = mc_slice.len();
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
                                        if mc_len <= OP_PUSH_MAX_MC_BYTES {
                                            cnt.small_out += 1;
                                        } else {
                                            cnt.large_out += 1;
                                        }
                                    } else {
                                        cnt.bad_host += 1;
                                    }
                                }
                            }
                        });
                        if !sdp_tx.is_empty() {
                            flush_qp(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                        }
                        if !eth_tx.is_empty() {
                            flush_qp(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                        }
                    }
                }
            });

            Ok(())
        })?;

        Ok(())
    }
}
