//! Multi-core CN106 100GbE SmartNIC bridge (`eth0 4RxQ/4TxQ` RSS) for MRV-195
//! (Real Dual-Host 100GbE Lightweight RPC/KV Benchmark).
//!
//! Supports:
//!   - `--mode card-echo`: pure card-side echo (network/NIC baseline)
//!   - `--mode sdp`      : 4-core `eth0` <-> 4-queue `sdp1-1` (MTU 9000, 48 B aligned
//!                         header matching `rx_poll_tail_off=48`) <-> 1-core `saturn`
//!   - `--mode direct`   : 4-core `eth0` <-> DirectPush (`<= 256 B` PEM0 WC + 128 B-stride
//!                         exclusive BAR4 sub-rings) + 4x bidirectional DPI DMA VFs
//!                         (`>= 1024 B`, vchan 0 ToHost + vchan 1 FromHost) <-> 1-core `saturn`
//!   - `--rx-mode default | cqe-peek | cqe-direct-safe`: selects NIX0 `eth0` RX polling mode
//!                         uniformly on every card core (fairly available to both `sdp` and `direct`).

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv195_card runs on the aarch64 CN106 card");
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
    use mrv195::{
        BAR4_CELL_STRIDE_DEFAULT, BAR4_TOTAL_RING_BYTES, CTRL_BAR4_STRIDE, CTRL_CARD_ACK_GEN,
        CTRL_CARD_CORES, CTRL_CREDIT_HYST, CTRL_HOST_READY_GEN, CTRL_RX_ZONE_IOVA, CellPoll,
        ETH_MBUF_BUF_SIZE, FastHist, KV_STATUS_OK, LARGE_SLOT_MASK, MAX_CARD_CORES, MAX_DRAIN,
        MAX_RPC_BYTES, MAX_SEG_BYTES, OP_DMA, OP_NET, OP_PROBE, OP_PUSH, ROCE_HDR_LEN,
        ROCE_ICRC_LEN, ROCE_UDP_PORT, RX_ZONE_BYTES, RoceTemplate, RpcCell, SDP_HDR_LEN,
        SDP_MBUF_BUF_SIZE, SDP_MTU, SDP_UDP_DST_PORT, SYNC_ADDR, bar4_cell_offset,
        credit_line_offset, large_rx_offset, pack_kv_meta, pack_meta, probe_cookie, rx_ring_offset,
        unpack_kv_meta, unpack_meta,
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
        /// Watchdog interval in packets for `--rx-mode cqe-direct-safe` (default 4096).
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
        #[arg(long, default_value_t = 1024)]
        dma_threshold: usize,
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
    // Per-core stats and cross-core sync
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

    #[derive(Clone, Copy)]
    struct ClientPeer {
        mac: [u8; 6],
        ip: [u8; 4],
        qpn: u32,
        tpl_64: RoceTemplate,
        tpl_256: RoceTemplate,
        tpl_1024: RoceTemplate,
    }

    impl ClientPeer {
        fn new(card_mac: [u8; 6], card_ip: [u8; 4], mac: [u8; 6], ip: [u8; 4], qpn: u32) -> Self {
            ClientPeer {
                mac,
                ip,
                qpn,
                tpl_64: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 64),
                tpl_256: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 256),
                tpl_1024: RoceTemplate::new(card_mac, mac, card_ip, ip, qpn, 1024),
            }
        }

        #[inline(always)]
        fn template_for(&self, seg_len: usize) -> Option<&RoceTemplate> {
            match seg_len {
                64 => Some(&self.tpl_64),
                256 => Some(&self.tpl_256),
                1024 => Some(&self.tpl_1024),
                _ => None,
            }
        }
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

        fn register_peer(&self, tid: usize, mac: [u8; 6], ip: [u8; 4], qpn: u32) -> ClientPeer {
            let p = ClientPeer::new(self.card_mac, self.card_ip, mac, ip, qpn);
            let mut g = self.peers.lock().unwrap();
            g[tid & (MAX_PEERS - 1)] = Some(p);
            self.peer_gen.fetch_add(1, Ordering::Release);
            p
        }

        fn run_cmd(&self, cmd: u64) -> Option<(CoreSnapshot, FastHist, Vec<CoreSnapshot>)> {
            let _guard = self.cmd_lock.lock().unwrap();
            let prev = self.cmd_word.load(Ordering::Relaxed) >> 4;
            let next_gen = prev + 1;
            self.done_cnt.store(0, Ordering::Release);
            self.cmd_word.store((next_gen << 4) | (cmd & 0xf), Ordering::Release);
            let t0 = Instant::now();
            while self.done_cnt.load(Ordering::Acquire) < self.n_cores as u64 {
                if self.quit.load(Ordering::Relaxed) || t0.elapsed() > Duration::from_secs(3) {
                    return None;
                }
                std::thread::sleep(Duration::from_micros(100));
            }
            if cmd == 2 {
                let snaps = self.per_core_snap.lock().unwrap().clone();
                let mut tot = CoreSnapshot::default();
                for s in &snaps {
                    tot.add_assign(s);
                }
                let hists = self.per_core_hist.lock().unwrap();
                let mut merged = FastHist::new();
                for h in hists.iter() {
                    merged.merge_from(h);
                }
                Some((tot, merged, snaps))
            } else {
                Some((CoreSnapshot::default(), FastHist::new(), Vec::new()))
            }
        }
    }

    #[derive(Clone, Copy, Default)]
    struct ReasmMeta {
        rpc_seq: u64,
        mask: u16,
        frag_cnt: u8,
        rpc_bytes: usize,
        client_tsc: u64,
    }

    struct ToHostJob {
        rpc_seq: u64,
        rpc_bytes: usize,
        frag_cnt: u8,
        large_slot: usize,
        client_tsc: u64,
    }

    struct FromHostJob {
        tx_slot: usize,
        rpc_seq: u64,
        rpc_bytes: usize,
        frag_cnt: u8,
    }

    #[derive(Clone, Copy)]
    struct DrainedPush {
        rpc_seq: u64,
        bytes_div64: u16,
        frag_cnt: u8,
        cells: u8,
        extra: [RpcCell; 4],
    }

    fn read_xstats_json(st: &PortStats<'_, '_>) -> String {
        let Ok(xs) = st.xstats() else {
            return String::new();
        };
        let mut parts = Vec::new();
        for (name, val) in xs {
            if val == 0 {
                continue;
            }
            if name.contains("drop")
                || name.contains("err")
                || name.contains("miss")
                || name.contains("rx_octs")
                || name.contains("tx_octs")
                || name.contains("rx_ucast")
                || name.contains("tx_ucast")
            {
                parts.push(format!("\"{name}\":{val}"));
            }
        }
        parts.join(",")
    }

    fn format_stats(
        mode: Mode,
        rx_mode: RxMode,
        n_cores: usize,
        tot: &CoreSnapshot,
        hist: &FastHist,
        per_core: &[CoreSnapshot],
        est: &EthStats,
        exs: &str,
        sst: Option<&EthStats>,
    ) -> String {
        let per_core_in: Vec<String> = per_core.iter().map(|c| c.rpcs_in.to_string()).collect();
        let per_core_out: Vec<String> = per_core.iter().map(|c| c.rpcs_out.to_string()).collect();
        let sdp_str = match sst {
            Some(s) => format!(
                ",\"sdp_ipackets\":{},\"sdp_opackets\":{},\"sdp_imissed\":{},\"sdp_ierrors\":{},\"sdp_oerrors\":{},\"sdp_rx_nombuf\":{}",
                s.ipackets, s.opackets, s.imissed, s.ierrors, s.oerrors, s.rx_nombuf
            ),
            None => String::new(),
        };
        format!(
            "{{\"mode\":\"{:?}\",\"rx_mode\":\"{:?}\",\"n_cores\":{},\"eth_rx_pkts\":{},\"eth_tx_pkts\":{},\"rpcs_in\":{},\"rpcs_out\":{},\"small_in\":{},\"large_in\":{},\"small_out\":{},\"large_out\":{},\"per_core_in\":[{}],\"per_core_out\":[{}],\"bad_wire\":{},\"bad_host\":{},\"torn_bar4\":{},\"dma_failed\":{},\"eth_tx_drops\":{},\"sdp_tx_drops\":{},\"probes\":{},\"cqe\":{{\"peek_empty\":{},\"peek_hit\":{},\"ldadda_calls\":{},\"pkts_popped\":{},\"w9_wait_retries\":{},\"watchdog_ldadda\":{},\"watchdog_head_mismatch\":{},\"err_pkts_freed\":{},\"mseg_pkts\":{},\"fallback_ldadda\":{}}},\"host_rtt_cnt\":{},\"host_rtt_p50_us\":{:.3},\"host_rtt_p99_us\":{:.3},\"host_rtt_mean_us\":{:.3},\"eth_ipackets\":{},\"eth_opackets\":{},\"eth_ibytes\":{},\"eth_obytes\":{},\"eth_imissed\":{},\"eth_ierrors\":{},\"eth_oerrors\":{},\"eth_rx_nombuf\":{},\"eth_xstats\":{{{}}}{}}}",
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
            per_core_in.join(","),
            per_core_out.join(","),
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
            hist.pct_us(0.50),
            hist.pct_us(0.99),
            hist.mean_us(),
            est.ipackets,
            est.opackets,
            est.ibytes,
            est.obytes,
            est.imissed,
            est.ierrors,
            est.oerrors,
            est.rx_nombuf,
            exs,
            sdp_str
        )
    }

    #[inline(always)]
    fn parse_roce_packet(frame: &[u8]) -> Option<([u8; 6], [u8; 4], u32, &[u8])> {
        if frame.len() < ROCE_HDR_LEN + 64 + ROCE_ICRC_LEN {
            return None;
        }
        if frame[12] != 0x08 || frame[13] != 0x00 || frame[23] != 17 {
            return None;
        }
        let dport = u16::from_be_bytes([frame[36], frame[37]]);
        if dport != ROCE_UDP_PORT {
            return None;
        }
        if frame[42] != 0x64 {
            return None;
        }
        let udp_len = u16::from_be_bytes([frame[38], frame[39]]) as usize;
        if udp_len < 8 + 12 + 8 + 64 + ROCE_ICRC_LEN {
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
    fn build_roce_tx_mbuf<'e>(pool: &Mempool<'e>, peer: &ClientPeer, payload: &[u8]) -> Option<Mbuf<'e>> {
        let tpl = peer.template_for(payload.len())?;
        let mut m = pool.alloc()?;
        let total = ROCE_HDR_LEN + payload.len() + ROCE_ICRC_LEN;
        let dst = m.append(total as u16)?;
        dst[..ROCE_HDR_LEN].copy_from_slice(&tpl.hdr);
        dst[ROCE_HDR_LEN..ROCE_HDR_LEN + payload.len()].copy_from_slice(payload);
        let icrc = tpl.icrc(&dst[ROCE_HDR_LEN..ROCE_HDR_LEN + payload.len()]);
        dst[ROCE_HDR_LEN + payload.len()..total].copy_from_slice(&icrc.to_le_bytes());
        Some(m)
    }

    /// Build a small (`64 B` or `256 B`) DirectPush `OP_PUSH` reply directly into an `eth0` mbuf.
    #[inline(always)]
    fn build_roce_drained_mbuf<'e>(pool: &Mempool<'e>, peer: &ClientPeer, dr: &DrainedPush) -> Option<Mbuf<'e>> {
        let cells = dr.cells as usize;
        let rpc_bytes = cells * 64;
        let tpl = peer.template_for(rpc_bytes)?;
        let mut m = pool.alloc()?;
        let total = ROCE_HDR_LEN + rpc_bytes + ROCE_ICRC_LEN;
        let dst = m.append(total as u16)?;
        dst[..ROCE_HDR_LEN].copy_from_slice(&tpl.hdr);

        let pay = &mut dst[ROCE_HDR_LEN..ROCE_HDR_LEN + rpc_bytes];
        let wire_meta = pack_meta(dr.rpc_seq, dr.bytes_div64, 0, dr.frag_cnt, OP_NET);
        pay[0..8].copy_from_slice(&wire_meta.to_le_bytes());
        for k in 1..7 {
            pay[k * 8..(k + 1) * 8].copy_from_slice(&dr.extra[0].w[k].to_le_bytes());
        }
        pay[56..64].copy_from_slice(&wire_meta.to_le_bytes());
        for idx in 1..cells {
            let base = idx * 64;
            let cell_tag = wire_meta ^ (idx as u64);
            pay[base..base + 8].copy_from_slice(&cell_tag.to_le_bytes());
            for k in 1..7 {
                pay[base + k * 8..base + (k + 1) * 8].copy_from_slice(&dr.extra[idx].w[k].to_le_bytes());
            }
            let tail_tag = if idx + 1 == cells { wire_meta } else { cell_tag };
            pay[base + 56..base + 64].copy_from_slice(&tail_tag.to_le_bytes());
        }

        let icrc = tpl.icrc(&dst[ROCE_HDR_LEN..ROCE_HDR_LEN + rpc_bytes]);
        dst[ROCE_HDR_LEN + rpc_bytes..total].copy_from_slice(&icrc.to_le_bytes());
        Some(m)
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

    #[inline(always)]
    fn send_rpc_to_eth<'e>(
        eth_pool: &Mempool<'e>,
        qp: &mut QueuePair<'_, 'e>,
        eth_tx: &mut Burst<'e, MAX_DRAIN>,
        peer: &ClientPeer,
        payload: &[u8],
        frag_cnt: u8,
        cnt: &mut CoreSnapshot,
    ) {
        let seg_len = if frag_cnt > 1 { MAX_SEG_BYTES } else { payload.len() };
        for f in 0..(frag_cnt as usize) {
            let seg = &payload[f * seg_len..(f + 1) * seg_len];
            if let Some(m) = build_roce_tx_mbuf(eth_pool, peer, seg) {
                if eth_tx.push(m).is_err() {
                    flush_qp(qp, eth_tx, &mut cnt.eth_tx_drops);
                    if let Some(m2) = build_roce_tx_mbuf(eth_pool, peer, seg) {
                        let _ = eth_tx.push(m2);
                    }
                }
                cnt.eth_tx_pkts += 1;
            } else {
                cnt.eth_tx_drops += 1;
            }
        }
        if eth_tx.len() >= 16 {
            flush_qp(qp, eth_tx, &mut cnt.eth_tx_drops);
        }
    }

    /// Collision-free reassembly slot index across all 8 mercury threads x 256 in-flight/thread.
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
        hdr[18..20].copy_from_slice(&0x1950u16.to_be_bytes());
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
        let eth_pool = Mempool::new_with_buf_size(&eal, "m195_eth_pool", eth_pool_sz, ETH_MBUF_BUF_SIZE, eth_sock)?;
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
            let sp = Mempool::new_with_buf_size(&eal, "m195_sdp_pool", 16384, SDP_MBUF_BUF_SIZE, sdp_sock)?;
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
            sdp_card_mac = [0x02, 0x19, 0x50, 0x00, 0x00, 0x02];
            sdp_port = None;
            sdp_pool = None;
        }

        // 3. Per-core contiguous reasm & tx_stage memzones
        let mut reasm_mzs: Vec<Memzone<'_>> = Vec::with_capacity(n_cores);
        let mut stage_mzs: Vec<Memzone<'_>> = Vec::with_capacity(n_cores);
        for c in 0..n_cores {
            let rmz = Memzone::reserve_contiguous(
                &eal,
                &format!("m195_reasm_{c}"),
                REASM_SLOTS * MAX_RPC_BYTES,
                eth_sock,
                4096,
            )?;
            unsafe { std::ptr::write_bytes(rmz.as_ptr(), 0, REASM_SLOTS * MAX_RPC_BYTES) };
            reasm_mzs.push(rmz);
            let smz = Memzone::reserve_contiguous(
                &eal,
                &format!("m195_stage_{c}"),
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
            let mz = Memzone::reserve_contiguous(&eal, "m195_bar4", SLOT_BYTES, eth_sock, SLOT_BYTES as u32)?;
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
                if args.dma_threshold > 0 {
                    wc_wins.push(Some(OutboundWindow::claim(255 - c, args.host_pf, args.host_vf, !args.no_wc)?));
                } else {
                    wc_wins.push(None);
                }
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
                eprintln!("mrv259_kv_card: BAR4 response ring mapped MT_NORMAL_NC (par={:#018x})", nc.par_el1);
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
        let dma_threshold = args.dma_threshold;
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
                "MRV195_CARD {ready_name} n_cores={} rx_mode={:?} mac={:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x} tsc_hz={:.0}",
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

                while !sh_ref.quit.load(Ordering::Relaxed) {
                    iter = iter.wrapping_add(1);

                    // Periodic control & handshake check
                    if (iter & 0x3ff) == 0 {
                        // Sync shared peer table if updated by another core
                        let pgen = sh_ref.peer_gen.load(Ordering::Acquire);
                        if pgen != local_peer_gen {
                            let g = sh_ref.peers.lock().unwrap();
                            local_peers = *g;
                            local_peer_gen = pgen;
                        }

                        // Sync RESET / STATS commands
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
                                // Safe detach before host unmaps VF0 IOMMU memory:
                                // drain WC buffers, unmap /dev/pem_wc, and zero PEM0_REG_NORM_ACC.
                                wc_ptr = std::ptr::null_mut();
                                credit_ptr = std::ptr::null_mut();
                                local_host_gen = 0;
                                if let Some(win) = inp.wc_win.as_mut() {
                                    if let Err(e) = win.detach() {
                                        eprintln!("MRV195_CARD core={core_idx} win.detach failed: {e}");
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
                                    // Zero BAR4 ring area before acking host
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
                                                // Host already zeroes all 8 MiB of `rx_zone` before setting
                                                // `CTRL_HOST_READY_GEN`; avoid an immediate all-zero WC store here.
                                                unsafe { std::arch::asm!("dsb sy; isb", options(nostack, preserves_flags)) };
                                            }
                                            Err(e) => {
                                                eprintln!("MRV195_CARD FATAL core={core_idx} win.retarget_range({aligned_base:#x}, {off:#x}, {RX_ZONE_BYTES}) failed: {e}");
                                                sh_ref.quit.store(true, Ordering::Release);
                                                break;
                                            }
                                        }
                                    } else {
                                        eprintln!("MRV195_CARD FATAL core={core_idx} host_rx_iova={host_rx_iova:#x} crosses 2 GiB PEM0 region");
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
                            let Some((src_mac, src_ip, src_qpn, payload)) = parse_roce_packet(frame) else {
                                return;
                            };
                            cnt.eth_rx_pkts += 1;

                            let w0 = u64::from_le_bytes(payload[0..8].try_into().unwrap());
                            let w7 = u64::from_le_bytes(payload[56..64].try_into().unwrap());
                            let (rpc_seq, bytes_div64, frag_idx, frag_cnt, op) = unpack_meta(w0);
                            let rpc_bytes = (bytes_div64 as usize) * 64;
                            if rpc_seq == 0
                                || w0 != w7
                                || frag_cnt == 0
                                || frag_idx >= frag_cnt
                                || rpc_bytes == 0
                                || rpc_bytes > MAX_RPC_BYTES
                            {
                                cnt.bad_wire += 1;
                                return;
                            }

                            // Handle RSS flow-label probe directly on eth0
                            if op == OP_PROBE {
                                let cookie = u64::from_le_bytes(payload[16..24].try_into().unwrap());
                                if cookie == probe_cookie(rpc_seq) {
                                    cnt.probes += 1;
                                    let tmp_peer = ClientPeer::new(sh_ref.card_mac, sh_ref.card_ip, src_mac, src_ip, src_qpn);
                                    let mut rep = [0u8; 64];
                                    rep[0..8].copy_from_slice(&w0.to_le_bytes());
                                    rep[8..16].copy_from_slice(&(core_idx as u64).to_le_bytes());
                                    rep[16..24].copy_from_slice(&cookie.to_le_bytes());
                                    rep[56..64].copy_from_slice(&w0.to_le_bytes());
                                    send_rpc_to_eth(eth_pool_ref, &mut inp.eth_qp, &mut eth_tx, &tmp_peer, &rep, 1, &mut cnt);
                                }
                                return;
                            }

                            let tid = ((rpc_seq >> 34) & 7) as usize;
                            let need_reg = match &local_peers[tid] {
                                Some(p) => p.qpn != src_qpn || p.mac != src_mac || p.ip != src_ip,
                                None => true,
                            };
                            if need_reg {
                                let _ = sh_ref.register_peer(tid, src_mac, src_ip, src_qpn);
                                let pg = sh_ref.peers.lock().unwrap();
                                local_peers = *pg;
                                local_peer_gen = sh_ref.peer_gen.load(Ordering::Acquire);
                            }

                            if payload.len() > 64 {
                                let last_tail = u64::from_le_bytes(
                                    payload[payload.len() - 8..payload.len()].try_into().unwrap(),
                                );
                                if last_tail != w0 {
                                    cnt.bad_wire += 1;
                                    return;
                                }
                            }

                            let (complete_slice, reasm_slot): (&[u8], usize) = if frag_cnt == 1 {
                                if payload.len() != rpc_bytes {
                                    cnt.bad_wire += 1;
                                    return;
                                }
                                if mode == Mode::Direct && rpc_bytes >= dma_threshold {
                                    let slot = reasm_index(rpc_seq);
                                    unsafe {
                                        std::ptr::copy_nonoverlapping(
                                            payload.as_ptr(),
                                            reasm_ptr.add(slot * MAX_RPC_BYTES),
                                            rpc_bytes,
                                        );
                                    }
                                    (
                                        unsafe {
                                            std::slice::from_raw_parts(
                                                reasm_ptr.add(slot * MAX_RPC_BYTES),
                                                rpc_bytes,
                                            )
                                        },
                                        slot,
                                    )
                                } else {
                                    (payload, 0)
                                }
                            } else {
                                let seg_len = rpc_bytes / (frag_cnt as usize);
                                if payload.len() != seg_len || rpc_bytes != (frag_cnt as usize) * seg_len {
                                    cnt.bad_wire += 1;
                                    return;
                                }
                                let slot = reasm_index(rpc_seq);
                                let rm = &mut reasm_meta[slot];
                                if rm.rpc_seq != rpc_seq {
                                    rm.rpc_seq = rpc_seq;
                                    rm.mask = 0;
                                    rm.frag_cnt = frag_cnt;
                                    rm.rpc_bytes = rpc_bytes;
                                    rm.client_tsc = 0;
                                }
                                unsafe {
                                    std::ptr::copy_nonoverlapping(
                                        payload.as_ptr(),
                                        reasm_ptr.add(slot * MAX_RPC_BYTES + (frag_idx as usize) * seg_len),
                                        seg_len,
                                    );
                                }
                                rm.mask |= 1u16 << frag_idx;
                                let full_mask = (1u16 << frag_cnt) - 1;
                                if rm.mask != full_mask {
                                    return;
                                }
                                rm.mask = 0;
                                (
                                    unsafe {
                                        std::slice::from_raw_parts(
                                            reasm_ptr.add(slot * MAX_RPC_BYTES),
                                            rpc_bytes,
                                        )
                                    },
                                    slot,
                                )
                            };
                            let client_tsc = u64::from_le_bytes(complete_slice[8..16].try_into().unwrap());
                            cnt.rpcs_in += 1;
                            if rpc_bytes <= 256 {
                                cnt.small_in += 1;
                            } else {
                                cnt.large_in += 1;
                            }

                            match mode {
                                Mode::CardEcho => {
                                    // Pure network echo: mark KV status = OK and echo payload back
                                    let mut tmp = [0u8; MAX_RPC_BYTES];
                                    tmp[..rpc_bytes].copy_from_slice(complete_slice);
                                    let kv_m = u64::from_le_bytes(tmp[16..24].try_into().unwrap());
                                    let (kv_op, vc, _, ck) = unpack_kv_meta(kv_m);
                                    let ok_m = pack_kv_meta(kv_op, vc, KV_STATUS_OK, ck);
                                    tmp[16..24].copy_from_slice(&ok_m.to_le_bytes());
                                    if let Some(p) = local_peers[tid].as_ref() {
                                        send_rpc_to_eth(
                                            eth_pool_ref,
                                            &mut inp.eth_qp,
                                            &mut eth_tx,
                                            p,
                                            &tmp[..rpc_bytes],
                                            frag_cnt,
                                            &mut cnt,
                                        );
                                        cnt.rpcs_out += 1;
                                    }
                                }
                                Mode::Sdp => {
                                    if let (Some(sqp), Some(spool)) = (inp.sdp_qp.as_mut(), sdp_pool_ref) {
                                        host_t0[(rpc_seq as usize) & TIMING_MASK] = rdtsc_after_loads();
                                        if let Some(mut m) = spool.alloc() {
                                            let flen = SDP_HDR_LEN + rpc_bytes;
                                            if let Some(buf) = m.append(flen as u16) {
                                                let hdr = build_sdp_hdr(sdp_host_mac, sdp_card_mac, core_idx, rpc_bytes);
                                                buf[..SDP_HDR_LEN].copy_from_slice(&hdr);
                                                buf[SDP_HDR_LEN..flen].copy_from_slice(complete_slice);
                                                // Stamp final 8 B with head tag `w0` (`frag_idx = 0`) so
                                                // `octeon_ep` `rx_poll_tail_off=48` validates the full frame!
                                                let head_tag = u64::from_le_bytes(
                                                    buf[SDP_HDR_LEN..SDP_HDR_LEN + 8].try_into().unwrap(),
                                                );
                                                buf[flen - 8..flen].copy_from_slice(&head_tag.to_le_bytes());
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
                                    if rpc_bytes < dma_threshold {
                                        let cells = rpc_bytes / 64;
                                        let my_rx = rx_seq;
                                        rx_seq += 1;
                                        let slot_ptr = unsafe { wc_ptr.add(rx_ring_offset(core_idx, my_rx)) };
                                        let c0_meta = pack_meta(my_rx, bytes_div64, 0, frag_cnt, OP_PUSH);
                                        // Write extra cells c=1..cells FIRST, preserving w[1..7] verbatim
                                        for c in 1..cells {
                                            let mut words = [0u64; 8];
                                            let base = c * 64;
                                            let tag = c0_meta ^ (c as u64);
                                            words[0] = tag;
                                            for k in 1..7 {
                                                words[k] = u64::from_le_bytes(
                                                    complete_slice[base + k * 8..base + (k + 1) * 8]
                                                        .try_into()
                                                        .unwrap(),
                                                );
                                            }
                                            words[7] = tag;
                                            unsafe { store64_gathered(slot_ptr.add(base), &words) };
                                        }
                                        // Write Cell 0 LAST with 4-chunk 16B ring_seq tags so host verifies all 4 STP pieces:
                                        // chunk0: w[0]=c0_meta, chunk1: w[2] bits 24..32 = (my_rx as u8),
                                        // chunk2: w[4] bits 48..64 = (my_rx as u16), chunk3: w[7]=c0_meta.
                                        let mut w = [0u64; 8];
                                        w[0] = c0_meta;
                                        for k in 1..7 {
                                            w[k] = u64::from_le_bytes(
                                                complete_slice[k * 8..(k + 1) * 8].try_into().unwrap(),
                                            );
                                        }
                                        w[2] = (w[2] & !(0xffu64 << 24)) | (((my_rx & 0xff) as u64) << 24);
                                        w[4] = (w[4] & 0x0000_ffff_ffff_ffff) | (((my_rx & 0xffff) as u64) << 48);
                                        w[7] = c0_meta;
                                        unsafe { store64_gathered(slot_ptr, &w) };
                                    } else if let Some(dma) = inp.dma.as_mut() {
                                        let lslot = large_rx_next & LARGE_SLOT_MASK;
                                        large_rx_next = large_rx_next.wrapping_add(1);
                                        let src_iova = inp.reasm_iova + (reasm_slot * MAX_RPC_BYTES) as u64;
                                        let dst_iova = host_rx_iova + large_rx_offset(core_idx, lslot) as u64;
                                        loop {
                                            match dma.copy_vchan(VCHAN_TO_HOST, src_iova, dst_iova, rpc_bytes as u32) {
                                                Ok(true) => {
                                                    dma_to_queued = true;
                                                    to_inflight.push_back(ToHostJob {
                                                        rpc_seq,
                                                        rpc_bytes,
                                                        frag_cnt,
                                                        large_slot: lslot,
                                                        client_tsc,
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
                                                            let m0 = pack_meta(
                                                                my_rx,
                                                                (job.rpc_bytes / 64) as u16,
                                                                0,
                                                                job.frag_cnt,
                                                                OP_DMA,
                                                            );
                                                            let notify = [
                                                                m0,
                                                                job.client_tsc,
                                                                job.rpc_seq,
                                                                (job.large_slot as u64) | (((my_rx & 0xffff) as u64) << 48),
                                                                job.rpc_bytes as u64,
                                                                m0,
                                                                m0,
                                                                m0,
                                                            ];
                                                            unsafe {
                                                                store64_gathered(
                                                                    wc_ptr.add(rx_ring_offset(core_idx, my_rx)),
                                                                    &notify,
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
                                        let (rpc_seq, bytes_div64, _fi, frag_cnt, _op) = unpack_meta(w0);
                                        let rpc_bytes = (bytes_div64 as usize) * 64;
                                        if rpc_seq == 0 || rpc_bytes == 0 || rpc_bytes > payload.len() || frag_cnt == 0 {
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
                                            send_rpc_to_eth(
                                                eth_pool_ref,
                                                &mut inp.eth_qp,
                                                &mut eth_tx,
                                                p,
                                                &payload[..rpc_bytes],
                                                frag_cnt,
                                                &mut cnt,
                                            );
                                            cnt.rpcs_out += 1;
                                            if rpc_bytes <= 256 {
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
                                            let m0 = pack_meta(
                                                my_rx,
                                                (job.rpc_bytes / 64) as u16,
                                                0,
                                                job.frag_cnt,
                                                OP_DMA,
                                            );
                                            let notify = [
                                                m0,
                                                job.client_tsc,
                                                job.rpc_seq,
                                                (job.large_slot as u64) | (((my_rx & 0xffff) as u64) << 48),
                                                job.rpc_bytes as u64,
                                                m0,
                                                m0,
                                                m0,
                                            ];
                                            unsafe {
                                                store64_gathered(
                                                    wc_ptr.add(rx_ring_offset(core_idx, my_rx)),
                                                    &notify,
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
                                        unsafe { RpcCell::poll_seq_ldpq(cell_ptr, bar_pos) }
                                    } else {
                                        unsafe { RpcCell::poll_seq(cell_ptr, bar_pos) }
                                    };
                                    match poll_res {
                                        CellPoll::Empty => break,
                                        CellPoll::Torn => {
                                            cnt.torn_bar4 += 1;
                                            break;
                                        }
                                        CellPoll::Ready(cell) => {
                                            let (_bseq, bytes_div64, _fidx, frag_cnt, op) = unpack_meta(cell.w[0]);
                                            let rpc_bytes = (bytes_div64 as usize) * 64;
                                            if op == OP_PUSH {
                                                let cells = rpc_bytes / 64;
                                                if cells == 0 || cells > 4 {
                                                    bar_pos += 1;
                                                    cnt.bad_host += 1;
                                                    continue;
                                                }
                                                let mut extra = [RpcCell::default(); 4];
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
                                                        unsafe { RpcCell::poll_seq_ldpq(cp, bar_pos + c as u64) }
                                                    } else {
                                                        unsafe { RpcCell::poll_seq(cp, bar_pos + c as u64) }
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
                                                let rpc_seq = extra[0].w[3];
                                                drained_push.push(DrainedPush {
                                                    rpc_seq,
                                                    bytes_div64,
                                                    frag_cnt,
                                                    cells: cells as u8,
                                                    extra,
                                                });
                                            } else if op == OP_DMA {
                                                bar_pos += 1;
                                                let rpc_seq = cell.w[2];
                                                let src_iova = cell.w[3];
                                                let tx_slot = tx_stage_next & STAGE_MASK;
                                                tx_stage_next = tx_stage_next.wrapping_add(1);
                                                let dst_iova = inp.tx_stage_iova + (tx_slot * MAX_RPC_BYTES) as u64;
                                                if let Some(dma) = inp.dma.as_mut() {
                                                    match dma.copy_vchan(
                                                        VCHAN_FROM_HOST,
                                                        src_iova,
                                                        dst_iova,
                                                        rpc_bytes as u32,
                                                    ) {
                                                        Ok(true) => {
                                                            from_queued = true;
                                                            from_inflight.push_back(FromHostJob {
                                                                tx_slot,
                                                                rpc_seq,
                                                                rpc_bytes,
                                                                frag_cnt,
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
                                        let t0 = host_t0[(dr.rpc_seq as usize) & TIMING_MASK];
                                        if t0 != 0 {
                                            let ns = ((now.saturating_sub(t0)) as f64 * ticks_to_ns) as u64;
                                            hist.record_ns(ns);
                                        }
                                        let tid = ((dr.rpc_seq >> 34) & 7) as usize;
                                        let pgen_now = sh_ref.peer_gen.load(Ordering::Acquire);
                                        if pgen_now != local_peer_gen || local_peers[tid].is_none() {
                                            let g = sh_ref.peers.lock().unwrap();
                                            local_peers = *g;
                                            local_peer_gen = pgen_now;
                                        }
                                        if let Some(p) = local_peers[tid].as_ref() {
                                            if let Some(m) = build_roce_drained_mbuf(eth_pool_ref, p, dr) {
                                                if eth_tx.push(m).is_err() {
                                                    flush_qp(&mut inp.eth_qp, &mut eth_tx, &mut cnt.eth_tx_drops);
                                                    if let Some(m2) = build_roce_drained_mbuf(eth_pool_ref, p, dr) {
                                                        let _ = eth_tx.push(m2);
                                                    }
                                                }
                                                cnt.eth_tx_pkts += 1;
                                                cnt.rpcs_out += 1;
                                                cnt.small_out += 1;
                                            } else {
                                                eprintln!("MRV195_CARD build_roce_drained_mbuf None core={core_idx} cells={} seq={:#x}", dr.cells, dr.rpc_seq);
                                                cnt.eth_tx_drops += 1;
                                            }
                                        } else {
                                            eprintln!("MRV195_CARD missing peer core={core_idx} tid={tid} seq={:#x}", dr.rpc_seq);
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
                                                let slice = unsafe {
                                                    std::slice::from_raw_parts(
                                                        tx_stage_ptr.add(job.tx_slot * MAX_RPC_BYTES),
                                                        job.rpc_bytes,
                                                    )
                                                };
                                                let tid = ((job.rpc_seq >> 34) & 7) as usize;
                                                let pgen_now = sh_ref.peer_gen.load(Ordering::Acquire);
                                                if pgen_now != local_peer_gen || local_peers[tid].is_none() {
                                                    let g = sh_ref.peers.lock().unwrap();
                                                    local_peers = *g;
                                                    local_peer_gen = pgen_now;
                                                }
                                                if let Some(p) = local_peers[tid].as_ref() {
                                                    send_rpc_to_eth(
                                                        eth_pool_ref,
                                                        &mut inp.eth_qp,
                                                        &mut eth_tx,
                                                        p,
                                                        slice,
                                                        job.frag_cnt,
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
