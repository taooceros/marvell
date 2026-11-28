//! Multi-core CN106 100GbE SmartNIC bridge (`eth0` <-> standard `sdp1-1`) for MRV-209
//! (MRV-203 E5: Real Upstream Memcached 1.6.45 + Stateful NFV + Co-located Socket-1 Tenant).
//!
//! 100% unified standard `SDP` data plane across all 4 single-variable policies:
//!   - `--policy large-sdp`      : direct pass-through into host Large SDP ring (no credit gate)
//!   - `--policy small-sdp-drop` : passive small-ring limit (no card elastic FIFO; immediate drop
//!                                 when `sdp1-1` TX ring backpressures or optional inflight cap hits)
//!   - `--policy ceio-style`     : reactive per-flow credit monitor (`CeioFlowTable`) with off-path
//!                                 rule-switch lag (transient fast-path leak), card DRAM slow-path
//!                                 queue (`OP_*_SLOW`), and phase-exclusivity drain pause
//!   - `--policy credit-sdp`     : On-Path SmartNIC head-resetting L3-local `ElasticFifo` +
//!                                 no-SFENCE `BAR4` `MOVDIR64B` explicit per-queue credit ring
//!                                 (`R_eff <= nb_desc - WMARK - 64`, zero SDP WMARK stalls)

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv209_card runs on the aarch64 CN106 card");
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
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, EthStats, Mbuf, Mempool, Memzone, Port, PortStats, QueuePair, rdtsc_after_loads};
    use testbed::inline::{NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{Bar4Window, restore_defaults};
    use mrv209::{
        CTRL_CARD_ACK_GEN, CTRL_CARD_CORES, CTRL_HOST_RUN_GEN, CTRL_POLICY_ID, CTRL_R_EFF,
        CeioFlowTable, CeioRoute, CeioStats, CreditLine, ETH_MBUF_BUF_SIZE, ElasticFifo, FastHist,
        MAX_CARD_CORES, MAX_DRAIN, MAX_FRAG_PAYLOAD, MAX_MC_BYTES, MAX_RPC_BYTES, MAX_SEG_BYTES,
        MC_MAGIC_REQ, MC_UDP_MAX_PAYLOAD, MC_UDP_PORT, NFV_MIN_LEN, OP_FRAG, OP_MC_FAST,
        OP_MC_SLOW, OP_NFV_FAST, OP_NFV_SLOW, OP_NFV_WIRE, OP_PROBE_WIRE, ROCE_HDR_LEN,
        ROCE_ICRC_LEN, ROCE_UDP_PORT, RoceTemplate, SDP_HDR_LEN, SDP_MBUF_BUF_SIZE_JUMBO, SDP_META_LEN,
        SDP_MTU, SDP_UDP_DST_PORT, SYNC_ADDR, crc32_sw_update, credit_line_offset, mc_udp_opaque,
        mc_udp_req_id, pack_flow_meta, pack_wire_meta, probe_cookie, roce_frag_cnt,
        rpc_seq_from_opaque, sdp_payload_total_bytes, unpack_wire_meta, wire_frag_cnt,
    };

    const ETH_DESC: u16 = 2048;
    const REASM_SLOTS: usize = 4096;
    const REASM_MASK: usize = REASM_SLOTS - 1;
    const HOST_T0_SLOTS: usize = 8 * 4096;

    #[inline(always)]
    fn host_t0_idx(rpc_seq: u64) -> usize {
        ((((rpc_seq >> 34) & 7) as usize) << 12) | ((rpc_seq as usize) & 0xfff)
    }
    const MAX_PEERS: usize = 8;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Policy {
        LargeSdp,
        SmallSdpDrop,
        CeioStyle,
        CreditSdp,
    }

    impl Policy {
        fn uses_bar4(self) -> bool {
            matches!(self, Policy::CreditSdp | Policy::CeioStyle)
        }

        fn id(self) -> u64 {
            match self {
                Policy::LargeSdp => 1,
                Policy::SmallSdpDrop => 2,
                Policy::CeioStyle => 3,
                Policy::CreditSdp => 4,
            }
        }
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = Policy::CreditSdp)]
        policy: Policy,
        #[arg(long, default_value = "0002:02:00.0")]
        eth_dev: String,
        #[arg(long, default_value = "0002:01:00.2")]
        sdp_dev: String,
        #[arg(long)]
        no_sdp_rss: bool,
        /// Card-side SDP RX descriptor count per queue.
        #[arg(long, default_value_t = 2048)]
        sdp_rx_desc: u16,
        /// Card-side SDP TX descriptor count per queue (use 256 for small-sdp-drop so NIX SQ
        /// does not secretly buffer thousands of burst packets).
        #[arg(long, default_value_t = 2048)]
        sdp_tx_desc: u16,
        /// Card-side SDP mbuf data room size (`4608` for jumbo Memcached etc_mix, `2176` for <= 1500 B).
        #[arg(long, default_value_t = SDP_MBUF_BUF_SIZE_JUMBO)]
        sdp_buf_size: u16,
        /// Card-side SDP NPA mbuf pool size (CN106 has 48 GB DDR5; 65535 holds >128 MiB of 2-4 KiB frames).
        #[arg(long, default_value_t = 65535)]
        sdp_pool_size: u32,
        /// Per-queue logical credit cap `R_eff` for `credit-sdp` (default 128; <= nb_desc - WMARK - 64).
        #[arg(long, default_value_t = 128)]
        r_eff: u64,
        /// Per-queue elastic FIFO packet capacity on CN106 (`credit-sdp` / `ceio-style` slow queue).
        #[arg(long, default_value_t = 16384)]
        elastic_cap: usize,
        /// Optional explicit inflight packet cap for `small-sdp-drop` (0 = pure hardware SDP TX backpressure drop).
        #[arg(long, default_value_t = 0)]
        drop_inflight_cap: u64,
        /// CEIO-style per-flow credit threshold before triggering reactive slow-path rule switch.
        #[arg(long, default_value_t = 32)]
        ceio_flow_credit: u32,
        /// CEIO-style off-path RMT rule-switch lag in microseconds (packets leak to fast path during lag).
        #[arg(long, default_value_t = 25)]
        ceio_switch_lag_us: u64,
        /// CEIO-style phase-exclusivity fast-path pause on slow->fast transition in microseconds.
        #[arg(long, default_value_t = 15)]
        ceio_phase_pause_us: u64,
        /// CEIO-style per-flow credit monitoring epoch window in microseconds.
        #[arg(long, default_value_t = 250)]
        ceio_window_us: u64,
        /// CEIO-style global fast-path inflight cap per queue before global reactive offload triggers.
        #[arg(long, default_value_t = 256)]
        ceio_fast_cap: u64,
        /// CEIO-style slow-path drain credit window per queue.
        #[arg(long, default_value_t = 64)]
        ceio_slow_credit: u64,
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        control: String,
        #[arg(long, default_value = SYNC_ADDR)]
        sync: String,
        #[arg(long)]
        restore: bool,
    }

    // -------------------------------------------------------------------------
    // CN106 TAD PMU (100% non-multiplexed: L3 hit/miss & DDR5 spill audit)
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
    // Per-Core Counters & Peer Tables
    // -------------------------------------------------------------------------
    #[derive(Clone, Default)]
    struct CoreSnapshot {
        eth_rx_pkts: u64,
        eth_tx_pkts: u64,
        rpcs_in: u64,
        rpcs_out: u64,
        mc_in: u64,
        nfv_in: u64,
        bad_wire: u64,
        bad_host: u64,
        torn_bar4: u64,
        eth_tx_drops: u64,
        sdp_tx_drops: u64,
        elastic_enqueued: u64,
        elastic_dequeued: u64,
        elastic_drops: u64,
        elastic_max_depth: u64,
        elastic_head_resets: u64,
        credit_stalls: u64,
        credit_updates_seen: u64,
        probes: u64,
        ceio: CeioStats,
    }

    impl CoreSnapshot {
        fn add_assign(&mut self, o: &CoreSnapshot) {
            self.eth_rx_pkts += o.eth_rx_pkts;
            self.eth_tx_pkts += o.eth_tx_pkts;
            self.rpcs_in += o.rpcs_in;
            self.rpcs_out += o.rpcs_out;
            self.mc_in += o.mc_in;
            self.nfv_in += o.nfv_in;
            self.bad_wire += o.bad_wire;
            self.bad_host += o.bad_host;
            self.torn_bar4 += o.torn_bar4;
            self.eth_tx_drops += o.eth_tx_drops;
            self.sdp_tx_drops += o.sdp_tx_drops;
            self.elastic_enqueued += o.elastic_enqueued;
            self.elastic_dequeued += o.elastic_dequeued;
            self.elastic_drops += o.elastic_drops;
            if o.elastic_max_depth > self.elastic_max_depth {
                self.elastic_max_depth = o.elastic_max_depth;
            }
            self.elastic_head_resets += o.elastic_head_resets;
            self.credit_stalls += o.credit_stalls;
            self.credit_updates_seen += o.credit_updates_seen;
            self.probes += o.probes;
            self.ceio.fast_pkts += o.ceio.fast_pkts;
            self.ceio.transient_leak_pkts += o.ceio.transient_leak_pkts;
            self.ceio.slow_enqueued_pkts += o.ceio.slow_enqueued_pkts;
            self.ceio.slow_drained_pkts += o.ceio.slow_drained_pkts;
            self.ceio.rule_switches += o.ceio.rule_switches;
            self.ceio.phase_pauses += o.ceio.phase_pauses;
            self.ceio.pause_stalled_pkts += o.ceio.pause_stalled_pkts;
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
        tpl_256: RoceTemplate,
        tpl_1024: RoceTemplate,
    }

    impl RocePeer {
        fn new(card_mac: [u8; 6], card_ip: [u8; 4], mac: [u8; 6], ip: [u8; 4], qpn: u32) -> Self {
            Self {
                mac,
                ip,
                qpn,
                card_mac,
                card_ip,
                tpl_32: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 32),
                tpl_44: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 44),
                tpl_64: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 64),
                tpl_164: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 164),
                tpl_256: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 256),
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
                256 => self.tpl_256,
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
        host_run_gen: AtomicU64,
        host_retarget_done: AtomicU64,
        active_r_eff: AtomicU64,
        per_core_replies: [AtomicU64; MAX_CARD_CORES],
        host_t0: Vec<AtomicU64>,
        card_mac: [u8; 6],
        card_ip: [u8; 4],
    }

    impl Shared {
        fn new(n_cores: usize, card_mac: [u8; 6], card_ip: [u8; 4], default_r_eff: u64) -> Self {
            Self {
                n_cores,
                quit: AtomicBool::new(false),
                cmd_word: AtomicU64::new(0),
                done_cnt: AtomicU64::new(0),
                cmd_lock: Mutex::new(()),
                per_core_snap: Mutex::new(vec![CoreSnapshot::default(); n_cores]),
                per_core_hist: Mutex::new((0..n_cores).map(|_| FastHist::new()).collect()),
                peers: Mutex::new([None; MAX_PEERS]),
                peer_gen: AtomicU64::new(0),
                host_run_gen: AtomicU64::new(0),
                host_retarget_done: AtomicU64::new(0),
                active_r_eff: AtomicU64::new(default_r_eff),
                per_core_replies: std::array::from_fn(|_| AtomicU64::new(0)),
                host_t0: (0..HOST_T0_SLOTS).map(|_| AtomicU64::new(0)).collect(),
                card_mac,
                card_ip,
            }
        }

        fn register_roce_peer(&self, tid: usize, mac: [u8; 6], ip: [u8; 4], qpn: u32) {
            let p = ClientPeer::Roce(RocePeer::new(self.card_mac, self.card_ip, mac, ip, qpn));
            let mut g = self.peers.lock().unwrap();
            g[tid & (MAX_PEERS - 1)] = Some(p);
            self.peer_gen.fetch_add(1, Ordering::Release);
        }

        fn register_std_udp_peer(&self, tid: usize, mac: [u8; 6], ip: [u8; 4], sport: u16) {
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

    struct QueuedPkt<'e> {
        flow_id: u16,
        mbuf: Mbuf<'e>,
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
        policy: Policy,
        n_cores: usize,
        r_eff: u64,
        tot: &CoreSnapshot,
        hist: &FastHist,
        per_core: &[CoreSnapshot],
        eth_st: &EthStats,
        eth_xstats: &str,
        sdp_st: &EthStats,
        sdp_pool_in_use: u32,
        sdp_pool_avail: u32,
    ) -> String {
        let per_in: Vec<String> = per_core.iter().map(|c| c.rpcs_in.to_string()).collect();
        let per_out: Vec<String> = per_core.iter().map(|c| c.rpcs_out.to_string()).collect();
        format!(
            "{{\"policy\":\"{:?}\",\"n_cores\":{},\"r_eff\":{},\"eth_rx_pkts\":{},\"eth_tx_pkts\":{},\
             \"rpcs_in\":{},\"rpcs_out\":{},\"mc_in\":{},\"nfv_in\":{},\"per_core_in\":[{}],\"per_core_out\":[{}],\
             \"bad_wire\":{},\"bad_host\":{},\"torn_bar4\":{},\"eth_tx_drops\":{},\"sdp_tx_drops\":{},\
             \"elastic_enqueued\":{},\"elastic_dequeued\":{},\"elastic_drops\":{},\"elastic_max_depth\":{},\
             \"elastic_head_resets\":{},\"credit_stalls\":{},\"credit_updates_seen\":{},\"probes\":{},\
             \"ceio\":{{\"fast_pkts\":{},\"transient_leak_pkts\":{},\"slow_enqueued_pkts\":{},\"slow_drained_pkts\":{},\
             \"rule_switches\":{},\"phase_pauses\":{},\"pause_stalled_pkts\":{}}},\
             \"host_rtt_cnt\":{},\"host_rtt_p50_us\":{:.3},\"host_rtt_p99_us\":{:.3},\"host_rtt_p999_us\":{:.3},\"host_rtt_mean_us\":{:.3},\
             \"eth_ipackets\":{},\"eth_opackets\":{},\"eth_ibytes\":{},\"eth_obytes\":{},\
             \"eth_imissed\":{},\"eth_ierrors\":{},\"eth_oerrors\":{},\"eth_rx_nombuf\":{},\"eth_xstats\":{},\
             \"sdp_ipackets\":{},\"sdp_opackets\":{},\"sdp_imissed\":{},\"sdp_ierrors\":{},\"sdp_oerrors\":{},\"sdp_rx_nombuf\":{},\
             \"sdp_pool_in_use\":{},\"sdp_pool_avail\":{}}}",
            policy,
            n_cores,
            r_eff,
            tot.eth_rx_pkts,
            tot.eth_tx_pkts,
            tot.rpcs_in,
            tot.rpcs_out,
            tot.mc_in,
            tot.nfv_in,
            per_in.join(","),
            per_out.join(","),
            tot.bad_wire,
            tot.bad_host,
            tot.torn_bar4,
            tot.eth_tx_drops,
            tot.sdp_tx_drops,
            tot.elastic_enqueued,
            tot.elastic_dequeued,
            tot.elastic_drops,
            tot.elastic_max_depth,
            tot.elastic_head_resets,
            tot.credit_stalls,
            tot.credit_updates_seen,
            tot.probes,
            tot.ceio.fast_pkts,
            tot.ceio.transient_leak_pkts,
            tot.ceio.slow_enqueued_pkts,
            tot.ceio.slow_drained_pkts,
            tot.ceio.rule_switches,
            tot.ceio.phase_pauses,
            tot.ceio.pause_stalled_pkts,
            hist.count(),
            hist.quantile_us(0.50),
            hist.quantile_us(0.99),
            hist.quantile_us(0.999),
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
            sdp_st.rx_nombuf,
            sdp_pool_in_use,
            sdp_pool_avail,
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
            }
        }
    }

    /// Single-shot non-blocking TX flush for `small-sdp-drop`: any packet rejected by
    /// the backpressured small SDP ring is immediately dropped and freed.
    #[inline(always)]
    fn flush_qp_drop<'e>(qp: &mut QueuePair<'_, 'e>, burst: &mut Burst<'e, MAX_DRAIN>, drops: &mut u64) -> usize {
        if burst.is_empty() {
            return 0;
        }
        let sent = qp.tx(burst);
        if !burst.is_empty() {
            *drops += burst.len() as u64;
            burst.clear();
        }
        sent
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
        let total = 42 + dgram.len();
        let dst = m.append(total as u16)?;
        dst[0..6].copy_from_slice(&peer.mac);
        dst[6..12].copy_from_slice(&peer.card_mac);
        dst[12..14].copy_from_slice(&0x0800u16.to_be_bytes());
        let ip_tot = (20 + 8 + dgram.len()) as u16;
        dst[14] = 0x45;
        dst[15] = 0x00;
        dst[16..18].copy_from_slice(&ip_tot.to_be_bytes());
        dst[18..20].copy_from_slice(&0x2090u16.to_be_bytes());
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

    #[inline(always)]
    fn send_reply_to_eth<'e>(
        eth_pool: &Mempool<'e>,
        qp: &mut QueuePair<'_, 'e>,
        eth_tx: &mut Burst<'e, MAX_DRAIN>,
        peer: &ClientPeer,
        rpc_seq: u64,
        payload: &[u8],
        cnt: &mut CoreSnapshot,
    ) {
        let plen = payload.len();
        match peer {
            ClientPeer::StdUdp(up) => {
                let mut off = 0usize;
                while off < plen {
                    let take = MC_UDP_MAX_PAYLOAD.min(plen - off);
                    if let Some(m) = build_std_udp_mbuf(eth_pool, up, &payload[off..off + take]) {
                        if let Err(m_back) = eth_tx.push(m) {
                            flush_qp_blocking(qp, eth_tx, &mut cnt.eth_tx_drops);
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
                let frag_cnt = roce_frag_cnt(plen);
                if frag_cnt == 1 {
                    if let Some(m) = build_roce_raw_mbuf(eth_pool, rp, payload) {
                        if let Err(m_back) = eth_tx.push(m) {
                            flush_qp_blocking(qp, eth_tx, &mut cnt.eth_tx_drops);
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
                        let end = (start + MAX_FRAG_PAYLOAD).min(plen);
                        let chunk = &payload[start..end];
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
                        let wf = pack_wire_meta(rpc_seq, plen as u16, f as u8, frag_cnt, OP_FRAG);
                        pay[0..8].copy_from_slice(&wf.to_le_bytes());
                        pay[8..8 + chunk.len()].copy_from_slice(chunk);
                        if chunk.len() < MAX_FRAG_PAYLOAD {
                            pay[8 + chunk.len()..MAX_SEG_BYTES - 8].fill(0);
                        }
                        pay[MAX_SEG_BYTES - 8..MAX_SEG_BYTES].copy_from_slice(&wf.to_le_bytes());
                        let icrc = tpl.icrc(&dst[ROCE_HDR_LEN..ROCE_HDR_LEN + MAX_SEG_BYTES]);
                        dst[ROCE_HDR_LEN + MAX_SEG_BYTES..total].copy_from_slice(&icrc.to_le_bytes());
                        if let Err(m_back) = eth_tx.push(m) {
                            flush_qp_blocking(qp, eth_tx, &mut cnt.eth_tx_drops);
                            let _ = eth_tx.push(m_back);
                        }
                        cnt.eth_tx_pkts += 1;
                    }
                }
            }
        }
        if eth_tx.len() >= 16 {
            flush_qp_blocking(qp, eth_tx, &mut cnt.eth_tx_drops);
        }
    }

    #[inline(always)]
    fn reasm_index(rpc_seq: u64) -> usize {
        let tid = ((rpc_seq >> 34) & 7) as usize;
        let lseq = (rpc_seq & 511) as usize;
        (tid * 512 + lseq) & REASM_MASK
    }

    fn build_sdp_hdr(dst_mac: [u8; 6], src_mac: [u8; 6], core_id: usize, payload_total: usize) -> [u8; SDP_HDR_LEN] {
        let mut hdr = [0u8; SDP_HDR_LEN];
        hdr[0..6].copy_from_slice(&dst_mac);
        hdr[6..12].copy_from_slice(&src_mac);
        hdr[12..14].copy_from_slice(&0x0800u16.to_be_bytes());
        let ip_tot = (20 + 8 + 6 + payload_total) as u16;
        hdr[14] = 0x45;
        hdr[15] = 0x00;
        hdr[16..18].copy_from_slice(&ip_tot.to_be_bytes());
        hdr[18..20].copy_from_slice(&0x2090u16.to_be_bytes());
        hdr[20..22].copy_from_slice(&0x4000u16.to_be_bytes());
        hdr[22] = 64;
        hdr[23] = 17;
        hdr[26..30].copy_from_slice(&[10, 10, 200, 2]);
        hdr[30..34].copy_from_slice(&[10, 10, 200, 1]);
        let sport = (5000 + core_id as u16).to_be_bytes();
        let dport = SDP_UDP_DST_PORT.to_be_bytes();
        let ulen = (8 + 6 + payload_total) as u16;
        hdr[34..36].copy_from_slice(&sport);
        hdr[36..38].copy_from_slice(&dport);
        hdr[38..40].copy_from_slice(&ulen.to_be_bytes());
        hdr
    }

    #[allow(clippy::too_many_arguments)]
    #[inline(always)]
    fn build_sdp_mbuf<'e>(
        sdp_pool: &Mempool<'e>,
        dst_mac: [u8; 6],
        src_mac: [u8; 6],
        core_idx: usize,
        rpc_seq: u64,
        sdp_op: u8,
        subop: u8,
        flow_id: u16,
        burst_flags: u16,
        card_seq: u32,
        payload: &[u8],
    ) -> Option<Mbuf<'e>> {
        let plen = payload.len();
        let total_bytes = sdp_payload_total_bytes(plen);
        let flen = SDP_HDR_LEN + total_bytes;
        let mut m = sdp_pool.alloc()?;
        let buf = m.append(flen as u16)?;
        let hdr = build_sdp_hdr(dst_mac, src_mac, core_idx, total_bytes);
        buf[..SDP_HDR_LEN].copy_from_slice(&hdr);
        let head_w0 = pack_wire_meta(rpc_seq, plen as u16, subop, wire_frag_cnt(plen), sdp_op);
        let fmeta = pack_flow_meta(flow_id, burst_flags, card_seq);
        buf[SDP_HDR_LEN..SDP_HDR_LEN + 8].copy_from_slice(&head_w0.to_le_bytes());
        buf[SDP_HDR_LEN + 8..SDP_HDR_LEN + SDP_META_LEN].copy_from_slice(&fmeta.to_le_bytes());
        buf[SDP_HDR_LEN + SDP_META_LEN..SDP_HDR_LEN + SDP_META_LEN + plen].copy_from_slice(payload);
        // Trailing copy of head_w0 pairs directly with octeon_ep `rx_poll_tail_off=48`.
        buf[flen - 8..flen].copy_from_slice(&head_w0.to_le_bytes());
        Some(m)
    }

    struct WorkerInput<'p, 'e> {
        eth_qp: QueuePair<'p, 'e>,
        sdp_qp: QueuePair<'p, 'e>,
        reasm_ptr: usize,
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
        let us_to_ticks = |us: u64| -> u64 { ((us as f64) * 1e-6 * tsc_hz) as u64 };

        let tad_pmu = Arc::new(TadPmu::open()?);

        // 1. Physical 100GbE `eth0` (RSS across `n_cores`)
        let eth_id = Port::by_name(&eal, &args.eth_dev)?;
        let eth_sock = Port::socket_of(&eal, eth_id);
        let eth_pool_sz = (16384 * n_cores as u32).min(65535);
        let eth_pool = Mempool::new_with_buf_size(&eal, "m209_eth_pool", eth_pool_sz, ETH_MBUF_BUF_SIZE, eth_sock)?;
        let mut eth_port = Port::start_with_opts_rss_fc(
            &eal,
            eth_id,
            &eth_pool,
            n_cores as u16,
            ETH_DESC,
            ETH_DESC,
            Some(1500),
            n_cores > 1,
            true,
        )?;
        eth_port.promiscuous()?;
        let card_mac = eth_port.mac();
        let card_ip: [u8; 4] = [10, 10, 100, 2];

        // 2. Standard `sdp1-1` data plane (unified across ALL 4 policies!)
        let sdp_id = Port::by_name(&eal, &args.sdp_dev)?;
        let sdp_sock = Port::socket_of(&eal, sdp_id);
        let sdp_pool = Mempool::new_with_buf_size(
            &eal,
            "m209_sdp_pool",
            args.sdp_pool_size,
            args.sdp_buf_size,
            sdp_sock,
        )?;
        let mut sdp_port = Port::start_with_opts_rss(
            &eal,
            sdp_id,
            &sdp_pool,
            n_cores as u16,
            args.sdp_rx_desc,
            args.sdp_tx_desc,
            Some(SDP_MTU),
            !args.no_sdp_rss && n_cores > 1,
        )?;
        sdp_port.promiscuous()?;
        let sdp_card_mac = sdp_port.mac();

        let shared = Arc::new(Shared::new(n_cores, card_mac, card_ip, args.r_eff));
        {
            let sh = Arc::clone(&shared);
            ctrlc::set_handler(move || sh.quit.store(true, Ordering::Relaxed))?;
        }

        // 3. Per-core reassembly buffers for multi-frag > 1024 B RoCEv2 requests
        let mut reasm_mzs: Vec<Memzone<'_>> = Vec::with_capacity(n_cores);
        for c in 0..n_cores {
            let rmz = Memzone::reserve_contiguous(
                &eal,
                &format!("m209_reasm_{c}"),
                REASM_SLOTS * MAX_RPC_BYTES,
                eth_sock,
                4096,
            )?;
            unsafe { std::ptr::write_bytes(rmz.as_ptr(), 0, REASM_SLOTS * MAX_RPC_BYTES) };
            reasm_mzs.push(rmz);
        }

        // 4. Optional BAR4 slot 15 credit-ring window (`credit-sdp` and `ceio-style` only;
        //    zero PEM0 WC / zero DPI DMA — BAR4 carries ONLY 64 B no-SFENCE credit lines).
        let bar4_mz: Option<Memzone<'_>>;
        let _bar4_win: Option<Bar4Window>;
        let lease_listener: Option<TcpListener>;
        if args.policy.uses_bar4() {
            let mz = Memzone::reserve_contiguous(&eal, "m209_bar4", SLOT_BYTES, eth_sock, SLOT_BYTES as u32)?;
            unsafe {
                std::ptr::write_bytes(mz.as_ptr(), 0, SLOT_BYTES);
                mz.as_ptr().add(READY_OFFSET).cast::<u64>().write_volatile(READY);
                mz.as_ptr().add(NONCE_OFFSET).cast::<u64>().write_volatile(0);
                mz.as_ptr().add(CTRL_CARD_CORES).cast::<u64>().write_volatile(n_cores as u64);
                mz.as_ptr().add(CTRL_POLICY_ID).cast::<u64>().write_volatile(args.policy.id());
                mz.as_ptr().add(CTRL_R_EFF).cast::<u64>().write_volatile(args.r_eff);
            }
            let pa = mz.phys_addr().ok_or("rte_mem_virt2phy failed on BAR4 memzone")?;
            let bw = Bar4Window::claim(SLOT, pa)?;
            let ll = TcpListener::bind(&args.control)?;
            bar4_mz = Some(mz);
            _bar4_win = Some(bw);
            lease_listener = Some(ll);
        } else {
            bar4_mz = None;
            _bar4_win = None;
            lease_listener = None;
        }
        let bar4_ptr_usize = bar4_mz.as_ref().map_or(0usize, |m| m.as_ptr() as usize);

        let sync_listener = TcpListener::bind(&args.sync)?;
        sync_listener.set_nonblocking(true)?;

        let (eth_stats, eth_qps) = eth_port.split_queues();
        let _ = eth_stats.stats_reset();
        let _ = eth_stats.xstats_reset();
        let (sdp_stats, sdp_qps) = sdp_port.split_queues();
        let _ = sdp_stats.stats_reset();

        let mut inputs: Vec<WorkerInput<'_, '_>> = Vec::with_capacity(n_cores);
        for (c, (eth_qp, sdp_qp)) in eth_qps.into_iter().zip(sdp_qps.into_iter()).enumerate() {
            inputs.push(WorkerInput {
                eth_qp,
                sdp_qp,
                reasm_ptr: reasm_mzs[c].as_ptr() as usize,
            });
        }

        let policy = args.policy;
        let elastic_cap = args.elastic_cap;
        let drop_inflight_cap = args.drop_inflight_cap;
        let ceio_flow_credit = args.ceio_flow_credit;
        let ceio_switch_lag_ticks = us_to_ticks(args.ceio_switch_lag_us);
        let ceio_phase_pause_ticks = us_to_ticks(args.ceio_phase_pause_us);
        let ceio_window_ticks = us_to_ticks(args.ceio_window_us).max(1);
        let ceio_fast_cap = args.ceio_fast_cap;
        let ceio_slow_credit = args.ceio_slow_credit;

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
            let sdp_pool_srv = &sdp_pool;
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
                                    "DETACH" => {
                                        if bar4_ptr_usize != 0 {
                                            unsafe {
                                                let p = bar4_ptr_usize as *mut u8;
                                                (*p.add(CTRL_HOST_RUN_GEN).cast::<AtomicU64>())
                                                    .store(0, Ordering::Release);
                                            }
                                        }
                                        sh_srv.host_run_gen.store(0, Ordering::Release);
                                        let ok = sh_srv.run_cmd(3).is_some();
                                        format!("{{\"ok\":{ok}}}")
                                    }
                                    "STATS" => match sh_srv.run_cmd(2) {
                                        Some((tot, hist, per_core)) => {
                                            let est = eth_stats_srv.stats().unwrap_or_default();
                                            let exs = read_xstats_json(&eth_stats_srv);
                                            let sst = sdp_stats_srv.stats().unwrap_or_default();
                                            format_stats(
                                                policy,
                                                n_cores,
                                                sh_srv.active_r_eff.load(Ordering::Relaxed),
                                                &tot,
                                                &hist,
                                                &per_core,
                                                &est,
                                                &exs,
                                                &sst,
                                                sdp_pool_srv.in_use_count(),
                                                sdp_pool_srv.avail_count(),
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
                "MRV209_CARD READY policy={:?} n_cores={} r_eff={} elastic_cap={} sdp_rx_desc={} sdp_tx_desc={} sdp_buf_size={} sdp_pool={} mac={:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x} tsc_hz={:.0}",
                policy,
                n_cores,
                args.r_eff,
                elastic_cap,
                args.sdp_rx_desc,
                args.sdp_tx_desc,
                args.sdp_buf_size,
                args.sdp_pool_size,
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
            let sdp_pool_ref = &sdp_pool;
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

                // Per-queue On-Path elastic FIFO (for `credit-sdp` and `ceio-style` slow queue).
                // Head-resetting on drain keeps <= 32 MiB bursts hot in CN106 48 MiB L3 (Pitfall 3).
                let q_cap = if matches!(policy, Policy::CreditSdp | Policy::CeioStyle) {
                    elastic_cap
                } else {
                    0
                };
                let mut elastic_q: ElasticFifo<QueuedPkt<'_>> = ElasticFifo::with_capacity(q_cap);
                let mut ceio_tbl = CeioFlowTable::new(
                    ceio_flow_credit,
                    ceio_switch_lag_ticks,
                    ceio_phase_pause_ticks,
                    ceio_window_ticks,
                );

                // Strictly monotonic credit counters across warmup/measurement for the active run_gen.
                let mut local_run_gen: u64 = 0;
                let mut r_eff: u64 = args.r_eff;
                let mut submitted: u64 = 0;
                let mut taken_cached: u64 = 0;
                let mut slow_submitted: u64 = 0;
                let mut slow_taken_cached: u64 = 0;
                let mut card_seq: u32 = 0;

                let mut cnt = CoreSnapshot::default();
                let mut hist = FastHist::new();
                let mut handled_cmd_gen: u64 = 0;

                let reasm_ptr = inp.reasm_ptr as *mut u8;
                let bar_base_ptr = bar4_ptr_usize as *mut u8;
                let my_credit_ptr = if !bar_base_ptr.is_null() {
                    unsafe { bar_base_ptr.add(credit_line_offset(core_idx)) }
                } else {
                    std::ptr::null_mut()
                };

                let mut iter: u64 = 0;
                let mut std_udp_seq: u64 = 0;

                while !sh_ref.quit.load(Ordering::Relaxed) {
                    iter = iter.wrapping_add(1);

                    // Periodic control & BAR4 handshake check
                    if (iter & 0x1ff) == 0 {
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
                            if cmd == 1 {
                                // RESET: reset delta metrics ONLY (preserve monotonic submitted/taken!)
                                cnt = CoreSnapshot::default();
                                hist.reset();
                                elastic_q.reset_stats_only();
                                ceio_tbl.stats = CeioStats::default();
                                let pgen_now = sh_ref.peer_gen.load(Ordering::Acquire);
                                let pg = sh_ref.peers.lock().unwrap();
                                local_peers = *pg;
                                local_peer_gen = pgen_now;
                            } else if cmd == 2 {
                                cnt.elastic_enqueued = elastic_q.enqueued;
                                cnt.elastic_dequeued = elastic_q.dequeued;
                                cnt.elastic_drops = elastic_q.drops;
                                cnt.elastic_max_depth = elastic_q.max_depth as u64;
                                cnt.elastic_head_resets = elastic_q.head_resets;
                                cnt.ceio = ceio_tbl.stats;
                                sh_ref.per_core_snap.lock().unwrap()[core_idx] = cnt.clone();
                                sh_ref.per_core_hist.lock().unwrap()[core_idx].reset();
                                sh_ref.per_core_hist.lock().unwrap()[core_idx].merge_from(&hist);
                            } else if cmd == 3 {
                                // DETACH: clear queues and reset credit session
                                elastic_q.clear_and_reset_stats();
                                ceio_tbl.reset();
                                local_run_gen = 0;
                                submitted = 0;
                                taken_cached = 0;
                                slow_submitted = 0;
                                slow_taken_cached = 0;
                                sh_ref.per_core_replies[core_idx].store(0, Ordering::Relaxed);
                            }
                            handled_cmd_gen = g;
                            sh_ref.done_cnt.fetch_add(1, Ordering::Release);
                        }

                        // BAR4 host-attach handshake (coordinated by core 0)
                        if policy.uses_bar4() && !bar_base_ptr.is_null() {
                            let mut pending_ack_gen: u64 = 0;
                            if core_idx == 0 {
                                let hgen = unsafe {
                                    (*bar_base_ptr.add(CTRL_HOST_RUN_GEN).cast::<AtomicU64>())
                                        .load(Ordering::Acquire)
                                };
                                if hgen != 0 && hgen != local_run_gen {
                                    let req_r_eff = unsafe {
                                        (*bar_base_ptr.add(CTRL_R_EFF).cast::<AtomicU64>())
                                            .load(Ordering::Acquire)
                                    };
                                    // Zero per-queue BAR4 credit lines before acknowledging new run_gen
                                    unsafe {
                                        std::ptr::write_bytes(bar_base_ptr, 0, MAX_CARD_CORES * 64);
                                        std::arch::asm!("dmb ish", options(nostack, preserves_flags));
                                    }
                                    if req_r_eff > 0 {
                                        sh_ref.active_r_eff.store(req_r_eff, Ordering::Release);
                                    }
                                    sh_ref.host_retarget_done.store(0, Ordering::Release);
                                    sh_ref.host_run_gen.store(hgen, Ordering::Release);
                                    pending_ack_gen = hgen;
                                }
                            }
                            let cur_gen = sh_ref.host_run_gen.load(Ordering::Acquire);
                            if cur_gen != 0 && cur_gen != local_run_gen {
                                local_run_gen = cur_gen;
                                r_eff = sh_ref.active_r_eff.load(Ordering::Acquire).max(1);
                                elastic_q.clear_and_reset_stats();
                                ceio_tbl.reset();
                                submitted = 0;
                                taken_cached = 0;
                                slow_submitted = 0;
                                slow_taken_cached = 0;
                                sh_ref.per_core_replies[core_idx].store(0, Ordering::Relaxed);
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
                    // 0. Refresh local BAR4 credit line from CN106 L3 (~10-20 ns, 0 PCIe reads)
                    // ---------------------------------------------------------
                    if policy.uses_bar4() && local_run_gen != 0 && !my_credit_ptr.is_null() {
                        let mut matched = false;
                        let mut saw_nonzero = false;
                        for _attempt in 0..4 {
                            let cl = unsafe { CreditLine::read_volatile_words(my_credit_ptr) };
                            if cl.w[1] != local_run_gen {
                                matched = true;
                                break;
                            }
                            if let Some((t_new, _bytes, st_new)) = cl.verify(core_idx, local_run_gen) {
                                // Monotonic max makes BAR4 credit return strictly idempotent.
                                if t_new > taken_cached {
                                    taken_cached = t_new;
                                    cnt.credit_updates_seen += 1;
                                }
                                if st_new > slow_taken_cached {
                                    slow_taken_cached = st_new;
                                }
                                matched = true;
                                break;
                            } else if cl.w[0] != 0 || cl.w[7] != 0 {
                                saw_nonzero = true;
                                std::sync::atomic::fence(Ordering::Acquire);
                            } else {
                                matched = true;
                                break;
                            }
                        }
                        if !matched && saw_nonzero {
                            cnt.torn_bar4 += 1;
                        }
                    }

                    // ---------------------------------------------------------
                    // 1. Drain On-Path elastic FIFO / CEIO slow queue into SDP TX when credit allows
                    // ---------------------------------------------------------
                    if !elastic_q.is_empty() {
                        match policy {
                            Policy::CreditSdp => {
                                let inflight = submitted.saturating_sub(taken_cached);
                                if inflight < r_eff {
                                    let budget = ((r_eff - inflight) as usize).min(MAX_DRAIN);
                                    for _ in 0..budget {
                                        let Some(qpkt) = elastic_q.pop() else {
                                            break;
                                        };
                                        if let Err(m_back) = sdp_tx.push(qpkt.mbuf) {
                                            flush_qp_blocking(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                            let _ = sdp_tx.push(m_back);
                                        }
                                        submitted += 1;
                                    }
                                    if !sdp_tx.is_empty() {
                                        flush_qp_blocking(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                    }
                                } else {
                                    cnt.credit_stalls += 1;
                                }
                            }
                            Policy::CeioStyle => {
                                let slow_inflight = slow_submitted.saturating_sub(slow_taken_cached);
                                if slow_inflight < ceio_slow_credit {
                                    let now_tsc = rdtsc_after_loads();
                                    let budget = ((ceio_slow_credit - slow_inflight) as usize).min(MAX_DRAIN);
                                    for _ in 0..budget {
                                        let Some(qpkt) = elastic_q.pop() else {
                                            break;
                                        };
                                        ceio_tbl.on_slow_drained(qpkt.flow_id, now_tsc);
                                        if let Err(m_back) = sdp_tx.push(qpkt.mbuf) {
                                            flush_qp_blocking(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                            let _ = sdp_tx.push(m_back);
                                        }
                                        submitted += 1;
                                        slow_submitted += 1;
                                    }
                                    if !sdp_tx.is_empty() {
                                        flush_qp_blocking(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                    }
                                } else {
                                    cnt.credit_stalls += 1;
                                }
                            }
                            _ => {}
                        }
                    }

                    // ---------------------------------------------------------
                    // 2. Poll 100GbE `eth0` RX -> forward/buffer onto standard `sdp1-1`
                    // ---------------------------------------------------------
                    let n_eth = inp.eth_qp.rx(&mut eth_rx);
                    if n_eth > 0 {
                        let now_rx = rdtsc_after_loads();
                        eth_rx.drain_into(|pkt| {
                            let frame = pkt.data();
                            let Some(rxk) = parse_eth_packet(frame, sh_ref.card_ip) else {
                                return;
                            };

                            // Decode into `(rpc_seq, is_nfv, subop, flow_id, burst_flags, tid, payload_slice)`
                            let decoded: Option<(u64, bool, u8, u16, u16, usize, &[u8])> = match rxk {
                                EthRxKind::ArpReq { src_mac, src_ip } => {
                                    if let Some(m) = build_arp_reply_mbuf(
                                        eth_pool_ref,
                                        sh_ref.card_mac,
                                        sh_ref.card_ip,
                                        src_mac,
                                        src_ip,
                                    ) {
                                        if let Err(m_back) = eth_tx.push(m) {
                                            flush_qp_blocking(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                                            let _ = eth_tx.push(m_back);
                                        }
                                    }
                                    None
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
                                    let flow_id = mc_udp_req_id(payload);
                                    Some((rpc_seq, false, 0, flow_id, 0, tid, payload))
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
                                                sh_ref.card_mac,
                                                sh_ref.card_ip,
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
                                                    flush_qp_blocking(
                                                        &mut inp.eth_qp,
                                                        &mut eth_tx,
                                                        &mut cnt.eth_tx_drops,
                                                    );
                                                    let _ = eth_tx.push(m_back);
                                                }
                                                cnt.eth_tx_pkts += 1;
                                            }
                                        }
                                        None
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
                                            sh_ref.register_roce_peer(tid, src_mac, src_ip, src_qpn);
                                            let pg = sh_ref.peers.lock().unwrap();
                                            local_peers = *pg;
                                            local_peer_gen = sh_ref.peer_gen.load(Ordering::Acquire);
                                        }
                                        let flow_id = mc_udp_req_id(payload);
                                        Some((rpc_seq, false, 0, flow_id, 0, tid, payload))
                                    } else if tag_byte == OP_NFV_WIRE {
                                        let plen = payload.len();
                                        if plen < NFV_MIN_LEN || plen > MAX_SEG_BYTES {
                                            cnt.bad_wire += 1;
                                            return;
                                        }
                                        let w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
                                        let w_tail =
                                            u64::from_le_bytes(payload[plen - 8..plen].try_into().unwrap());
                                        let (rpc_seq, len16, subop, _, _) = unpack_wire_meta(w0);
                                        if rpc_seq == 0 || w0 != w_tail || (len16 as usize) != plen {
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
                                        let flow_id = u16::from_le_bytes([payload[22], payload[23]]);
                                        let burst_flags = (payload[21] & 1) as u16;
                                        Some((rpc_seq, true, subop, flow_id, burst_flags, tid, payload))
                                    } else if tag_byte == OP_FRAG {
                                        if payload.len() != MAX_SEG_BYTES {
                                            cnt.bad_wire += 1;
                                            return;
                                        }
                                        let w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
                                        let w_tail = u64::from_le_bytes(
                                            payload[MAX_SEG_BYTES - 8..MAX_SEG_BYTES].try_into().unwrap(),
                                        );
                                        let (rpc_seq, mc_len_u16, frag_idx, frag_cnt, _) = unpack_wire_meta(w0);
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
                                            sh_ref.register_roce_peer(tid, src_mac, src_ip, src_qpn);
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
                                                reasm_ptr.add(slot * MAX_RPC_BYTES + start),
                                                copy_len,
                                            );
                                        }
                                        rm.mask |= 1u16 << frag_idx;
                                        let full_mask = (1u16 << frag_cnt) - 1;
                                        if rm.mask != full_mask {
                                            return;
                                        }
                                        rm.mask = 0;
                                        let sl = unsafe {
                                            std::slice::from_raw_parts(
                                                reasm_ptr.add(slot * MAX_RPC_BYTES),
                                                mc_len,
                                            )
                                        };
                                        let flow_id = mc_udp_req_id(sl);
                                        Some((rpc_seq, false, 0, flow_id, 0, tid, sl))
                                    } else {
                                        cnt.bad_wire += 1;
                                        None
                                    }
                                }
                            };

                            let Some((rpc_seq, is_nfv, subop, flow_id, burst_flags, _tid, pay)) = decoded else {
                                return;
                            };

                            cnt.rpcs_in += 1;
                            if is_nfv {
                                cnt.nfv_in += 1;
                            } else {
                                cnt.mc_in += 1;
                            }
                            sh_ref.host_t0[host_t0_idx(rpc_seq)].store(now_rx, Ordering::Relaxed);
                            card_seq = card_seq.wrapping_add(1);

                            match policy {
                                Policy::LargeSdp => {
                                    let sdp_op = if is_nfv { OP_NFV_FAST } else { OP_MC_FAST };
                                    if let Some(m) = build_sdp_mbuf(
                                        sdp_pool_ref,
                                        sdp_host_mac,
                                        sdp_card_mac,
                                        core_idx,
                                        rpc_seq,
                                        sdp_op,
                                        subop,
                                        flow_id,
                                        burst_flags,
                                        card_seq,
                                        pay,
                                    ) {
                                        if let Err(m_back) = sdp_tx.push(m) {
                                            flush_qp_blocking(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                            let _ = sdp_tx.push(m_back);
                                        }
                                        submitted += 1;
                                    } else {
                                        cnt.sdp_tx_drops += 1;
                                    }
                                }
                                Policy::SmallSdpDrop => {
                                    // Passive small host ring limit: no elastic card FIFO; drop immediately if
                                    // optional inflight cap or hardware `sdp1-1` TX backpressure is exceeded.
                                    let replies_for_me = sh_ref.per_core_replies[core_idx].load(Ordering::Relaxed);
                                    if drop_inflight_cap > 0
                                        && (submitted + sdp_tx.len() as u64).saturating_sub(replies_for_me)
                                            >= drop_inflight_cap
                                    {
                                        cnt.sdp_tx_drops += 1;
                                        return;
                                    }
                                    let sdp_op = if is_nfv { OP_NFV_FAST } else { OP_MC_FAST };
                                    if let Some(m) = build_sdp_mbuf(
                                        sdp_pool_ref,
                                        sdp_host_mac,
                                        sdp_card_mac,
                                        core_idx,
                                        rpc_seq,
                                        sdp_op,
                                        subop,
                                        flow_id,
                                        burst_flags,
                                        card_seq,
                                        pay,
                                    ) {
                                        if let Err(m_back) = sdp_tx.push(m) {
                                            let sent = flush_qp_drop(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                            submitted += sent as u64;
                                            let _ = sdp_tx.push(m_back);
                                        }
                                    } else {
                                        cnt.sdp_tx_drops += 1;
                                    }
                                }
                                Policy::CreditSdp => {
                                    let sdp_op = if is_nfv { OP_NFV_FAST } else { OP_MC_FAST };
                                    let Some(m) = build_sdp_mbuf(
                                        sdp_pool_ref,
                                        sdp_host_mac,
                                        sdp_card_mac,
                                        core_idx,
                                        rpc_seq,
                                        sdp_op,
                                        subop,
                                        flow_id,
                                        burst_flags,
                                        card_seq,
                                        pay,
                                    ) else {
                                        cnt.sdp_tx_drops += 1;
                                        return;
                                    };
                                    // Fast direct pass-through when FIFO is empty and credit is open: ~0 ns idle tax!
                                    if elastic_q.is_empty() && submitted.saturating_sub(taken_cached) < r_eff {
                                        if let Err(m_back) = sdp_tx.push(m) {
                                            flush_qp_blocking(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                            let _ = sdp_tx.push(m_back);
                                        }
                                        submitted += 1;
                                    } else if elastic_q.push(QueuedPkt { flow_id, mbuf: m }).is_err() {
                                        cnt.sdp_tx_drops += 1;
                                    }
                                }
                                Policy::CeioStyle => {
                                    let global_inflight = submitted.saturating_sub(taken_cached);
                                    let route = ceio_tbl.classify(flow_id, global_inflight, ceio_fast_cap, now_rx);
                                    let sdp_op = match (is_nfv, route) {
                                        (false, CeioRoute::FastPath { .. }) => OP_MC_FAST,
                                        (false, CeioRoute::SlowQueue) => OP_MC_SLOW,
                                        (true, CeioRoute::FastPath { .. }) => OP_NFV_FAST,
                                        (true, CeioRoute::SlowQueue) => OP_NFV_SLOW,
                                    };
                                    let Some(m) = build_sdp_mbuf(
                                        sdp_pool_ref,
                                        sdp_host_mac,
                                        sdp_card_mac,
                                        core_idx,
                                        rpc_seq,
                                        sdp_op,
                                        subop,
                                        flow_id,
                                        burst_flags,
                                        card_seq,
                                        pay,
                                    ) else {
                                        cnt.sdp_tx_drops += 1;
                                        return;
                                    };
                                    match route {
                                        CeioRoute::FastPath { .. } => {
                                            if let Err(m_back) = sdp_tx.push(m) {
                                                flush_qp_blocking(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                                let _ = sdp_tx.push(m_back);
                                            }
                                            submitted += 1;
                                        }
                                        CeioRoute::SlowQueue => {
                                            if elastic_q.push(QueuedPkt { flow_id, mbuf: m }).is_err() {
                                                cnt.sdp_tx_drops += 1;
                                            }
                                        }
                                    }
                                }
                            }
                        });

                        if !sdp_tx.is_empty() {
                            if policy == Policy::SmallSdpDrop {
                                let sent = flush_qp_drop(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                                submitted += sent as u64;
                            } else {
                                flush_qp_blocking(&mut inp.sdp_qp, &mut sdp_tx, &mut cnt.sdp_tx_drops);
                            }
                        }
                        if !eth_tx.is_empty() {
                            flush_qp_blocking(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                        }
                    }

                    // ---------------------------------------------------------
                    // 3. Poll `sdp1-1` RX (replies from saturn) -> forward to `eth0`
                    // ---------------------------------------------------------
                    let n_sdp = inp.sdp_qp.rx(&mut sdp_rx);
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
                            let (rpc_seq, plen_u16, _subop, _fc, _op) = unpack_wire_meta(w0);
                            let plen = plen_u16 as usize;
                            if rpc_seq == 0 || plen < 32 || SDP_META_LEN + plen + 8 > payload.len() {
                                cnt.bad_host += 1;
                                return;
                            }
                            let orig_core = u16::from_be_bytes([frame[34], frame[35]]).saturating_sub(5000) as usize;
                            if orig_core < MAX_CARD_CORES {
                                sh_ref.per_core_replies[orig_core].fetch_add(1, Ordering::Relaxed);
                            }
                            let t0 = sh_ref.host_t0[host_t0_idx(rpc_seq)].swap(0, Ordering::Relaxed);
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
                                send_reply_to_eth(
                                    eth_pool_ref,
                                    &mut inp.eth_qp,
                                    &mut eth_tx,
                                    p,
                                    rpc_seq,
                                    &payload[SDP_META_LEN..SDP_META_LEN + plen],
                                    &mut cnt,
                                );
                                cnt.rpcs_out += 1;
                            }
                        });
                        if !eth_tx.is_empty() {
                            flush_qp_blocking(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                        }
                    }
                }
            });

            Ok(())
        })?;

        Ok(())
    }
}
