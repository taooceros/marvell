//! Card-side (CN106 aarch64) On-Path SmartNIC elastic-FIFO + BAR4 credit-gated
//! standard `SDP` forwarder for MRV-214 (E6: design-space sensitivity sweep).
//!
//! Capabilities:
//! - Supports `1 / 2 / 4 / 8` CN106 ARM worker cores (`-l 2,4..`, `--main-lcore 2`):
//!   each worker `c` owns `eth0` (`0002:02:00.0`) RX queue `c` (RSS), a dedicated
//!   per-core `NPA` mempool (`cache = 256` LIFO for Pitfall 3 L3 locality), an
//!   elastic software FIFO `VecDeque<Mbuf>`, its own 64 B BAR4 credit line
//!   `credit_line_offset(c)` in slot 15 (zero false sharing), and `sdp1-1`
//!   (`0002:01:00.2`) TX queue `c` (`MBUF_FAST_FREE`).
//! - Dynamic runtime `CONFIG` over TCP (`10.10.0.2:7555`): reconfigures `r_eff`,
//!   `r_eff_bytes`, `credit=bar4|off`, `frame_kind=uc64|ud|udp`, `payload`,
//!   `fifo_cap`, `run_gen`, and `synth_mpps` across all active workers without
//!   restarting EAL.
//! - MRV-26 BAR4 slot 15 lease server (`10.10.0.2:7526`) + `--restore`.
//! - 100% non-multiplexed CN106 Card PMU (`tad` 4 counters + 6x `mrvl_ddr_pmu_*`
//!   reads/writes) exposed alongside live worker stats in a single `SNAP` / `PMU_SNAP`.

#[cfg(not(target_arch = "aarch64"))]
fn main() {
    eprintln!("mrv214_card runs on the aarch64 CN106 card");
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
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::thread;
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, EthStats, Mbuf, Mempool, Memzone, Port, PortStats, QueuePair};
    use testbed::inline::{CORES_OFFSET, NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
    use testbed::lease;
    use testbed::mmio::{Bar4Window, restore_defaults};
    use mrv214::{
        CARD_CTRL_PORT, CARD_IP, CreditCheck, CreditLine, FrameKind, MAX_CARD_CORES, OFF_BTH,
        OFF_ETH_TYPE, OFF_UDP_PAYLOAD, PayloadCheck, StreamTracker, UC64_BASE_QPN,
        credit_line_offset, parse_kv_tokens, verify_payload, write_synth_udp_frame,
    };

    const BURST_N: usize = 64;
    const ETH_RX_DESC: u16 = 2048;
    const ETH_TX_DESC: u16 = 512;
    const SDP_DESC: u16 = 2048;
    const FLOW_PROBE_SLOTS: usize = 128;

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum Mode {
        Wire,
        Synth,
        ProbeRss,
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = Mode::Wire)]
        mode: Mode,
        /// Physical 100GbE eth0 PCI BDF.
        #[arg(long, default_value = "0002:02:00.0")]
        eth_dev: String,
        /// Card SDP VF PCI BDF.
        #[arg(long, default_value = "0002:01:00.2")]
        sdp_dev: String,
        /// Per-core NPA mempool size (must exceed ETH_RX_DESC + SDP_DESC + fifo_cap).
        #[arg(long, default_value_t = 16384)]
        card_pool_size: u32,
        /// Default initial frame kind (`uc64`, `ud`, `udp`).
        #[arg(long, default_value = "udp")]
        frame_kind: String,
        /// Default initial payload length in bytes (`0` for uc64, `184` for ud254, `1454` for udp1500).
        #[arg(long, default_value_t = 1454)]
        payload_bytes: usize,
        /// Default initial logical credit limit R_eff (packets per queue).
        #[arg(long, default_value_t = 256)]
        r_eff: u64,
        /// Default initial byte-granular credit limit (0 = packet R_eff).
        #[arg(long, default_value_t = 0)]
        r_eff_bytes: u64,
        /// Default initial elastic FIFO capacity per queue (clamped to pool headroom).
        #[arg(long, default_value_t = 8192)]
        fifo_cap: usize,
        /// Disable BAR4 credit gating initially (can be toggled dynamically via CONFIG).
        #[arg(long)]
        no_credit: bool,
        /// MRV-26 BAR4 slot-15 lease TCP address.
        #[arg(long, default_value = lease::DEFAULT_ADDR)]
        lease_addr: String,
        /// Control/PMU TCP listen port on `10.10.0.2`.
        #[arg(long, default_value_t = CARD_CTRL_PORT)]
        ctrl_port: u16,
        /// Restore BAR4 slot 15 and PEM0 outbound regions 248-255 to firmware defaults and exit.
        #[arg(long)]
        restore: bool,
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

    // ---- 100% Non-Multiplexed CN106 Card PMU (TAD 48 MiB LLC + 6x DDR5) ----

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

    const DDR_PMU_NAMES: &[&str] = &[
        "mrvl_ddr_pmu_87e1c0000000",
        "mrvl_ddr_pmu_87e1c1000000",
        "mrvl_ddr_pmu_87e1c2000000",
        "mrvl_ddr_pmu_87e1c3000000",
        "mrvl_ddr_pmu_87e1c4000000",
        "mrvl_ddr_pmu_87e1c5000000",
    ];
    const DDR_WRITES_CFG: u64 = 0x64;
    const DDR_READS_CFG: u64 = 0x65;

    struct PerfCounter {
        fd: RawFd,
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

        fn read(&self) -> [u64; 3] {
            let mut buf = [0u64; 3];
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast(), size_of::<[u64; 3]>()) };
            if n == size_of::<[u64; 3]>() as isize { buf } else { [0, 0, 0] }
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
        tad: Vec<(&'static str, PerfCounter)>,
        ddr_rd: Vec<PerfCounter>,
        ddr_wr: Vec<PerfCounter>,
        t0: Instant,
    }

    impl CardPmu {
        fn open() -> Self {
            let mut tad = Vec::new();
            if let Ok(s) = fs::read_to_string("/sys/bus/event_source/devices/tad/type") {
                if let Ok(ty) = s.trim().parse::<u32>() {
                    for &(name, cfg) in TAD_EVENTS {
                        if let Ok(c) = PerfCounter::open(ty, cfg, 0) {
                            tad.push((name, c));
                        }
                    }
                }
            }
            let mut ddr_rd = Vec::new();
            let mut ddr_wr = Vec::new();
            for &name in DDR_PMU_NAMES {
                let p = format!("/sys/bus/event_source/devices/{name}/type");
                if let Ok(s) = fs::read_to_string(&p) {
                    if let Ok(ty) = s.trim().parse::<u32>() {
                        if let Ok(c) = PerfCounter::open(ty, DDR_READS_CFG, 0) {
                            ddr_rd.push(c);
                        }
                        if let Ok(c) = PerfCounter::open(ty, DDR_WRITES_CFG, 0) {
                            ddr_wr.push(c);
                        }
                    }
                }
            }
            Self { tad, ddr_rd, ddr_wr, t0: Instant::now() }
        }

        fn snap_json(&self) -> String {
            let t_s = self.t0.elapsed().as_secs_f64();
            let mut entries = Vec::with_capacity(self.tad.len() + 2);
            for (name, c) in &self.tad {
                let v = c.read();
                entries.push(format!("\"{name}\":[{},{},{}]", v[0], v[1], v[2]));
            }
            let sum_vec = |vec: &[PerfCounter]| -> [u64; 3] {
                let mut tot = [0u64; 3];
                for c in vec {
                    let v = c.read();
                    tot[0] = tot[0].saturating_add(v[0]);
                    tot[1] = tot[1].saturating_add(v[1]);
                    tot[2] = tot[2].saturating_add(v[2]);
                }
                tot
            };
            let dr = sum_vec(&self.ddr_rd);
            let dw = sum_vec(&self.ddr_wr);
            entries.push(format!("\"ddr_reads\":[{},{},{}]", dr[0], dr[1], dr[2]));
            entries.push(format!("\"ddr_writes\":[{},{},{}]", dw[0], dw[1], dw[2]));
            format!("{{\"t_s\":{t_s:.6},\"tad\":{{{}}}}}", entries.join(","))
        }
    }

    #[derive(Clone, Copy, Debug)]
    struct RunCfg {
        run_gen: u64,
        r_eff: u64,
        r_eff_bytes: u64,
        credit_bar4: bool,
        frame_kind: FrameKind,
        payload_bytes: usize,
        fifo_cap: usize,
        synth_mpps: f64,
    }

    #[repr(align(64))]
    struct AtomicCoreLive {
        eth_rx_pkts: AtomicU64,
        eth_rx_bytes: AtomicU64,
        arp_replies: AtomicU64,
        wire_lost: AtomicU64,
        wire_bad: AtomicU64,
        wire_torn: AtomicU64,
        fifo_len: AtomicU64,
        fifo_peak: AtomicU64,
        fifo_drops: AtomicU64,
        c2h_pkts: AtomicU64,
        c2h_bytes: AtomicU64,
        tx_retries: AtomicU64,
        alloc_fails: AtomicU64,
        credit_updates: AtomicU64,
        credit_stalls: AtomicU64,
        credit_rereads: AtomicU64,
        credit_torn: AtomicU64,
        taken_cached: AtomicU64,
        submitted: AtomicU64,
    }

    impl AtomicCoreLive {
        fn new() -> Self {
            Self {
                eth_rx_pkts: AtomicU64::new(0),
                eth_rx_bytes: AtomicU64::new(0),
                arp_replies: AtomicU64::new(0),
                wire_lost: AtomicU64::new(0),
                wire_bad: AtomicU64::new(0),
                wire_torn: AtomicU64::new(0),
                fifo_len: AtomicU64::new(0),
                fifo_peak: AtomicU64::new(0),
                fifo_drops: AtomicU64::new(0),
                c2h_pkts: AtomicU64::new(0),
                c2h_bytes: AtomicU64::new(0),
                tx_retries: AtomicU64::new(0),
                alloc_fails: AtomicU64::new(0),
                credit_updates: AtomicU64::new(0),
                credit_stalls: AtomicU64::new(0),
                credit_rereads: AtomicU64::new(0),
                credit_torn: AtomicU64::new(0),
                taken_cached: AtomicU64::new(0),
                submitted: AtomicU64::new(0),
            }
        }
    }

    #[derive(Default)]
    struct CoreLocal {
        eth_rx_pkts: u64,
        eth_rx_bytes: u64,
        arp_replies: u64,
        wire_lost: u64,
        wire_bad: u64,
        wire_torn: u64,
        fifo_peak: u64,
        fifo_drops: u64,
        c2h_pkts: u64,
        c2h_bytes: u64,
        tx_retries: u64,
        alloc_fails: u64,
        credit_updates: u64,
        credit_stalls: u64,
        credit_rereads: u64,
        credit_torn: u64,
    }

    impl CoreLocal {
        #[inline(always)]
        fn publish(&self, dst: &AtomicCoreLive, fifo_len: usize, taken: u64, submitted: u64) {
            dst.eth_rx_pkts.store(self.eth_rx_pkts, Ordering::Relaxed);
            dst.eth_rx_bytes.store(self.eth_rx_bytes, Ordering::Relaxed);
            dst.arp_replies.store(self.arp_replies, Ordering::Relaxed);
            dst.wire_lost.store(self.wire_lost, Ordering::Relaxed);
            dst.wire_bad.store(self.wire_bad, Ordering::Relaxed);
            dst.wire_torn.store(self.wire_torn, Ordering::Relaxed);
            dst.fifo_len.store(fifo_len as u64, Ordering::Relaxed);
            dst.fifo_peak.store(self.fifo_peak, Ordering::Relaxed);
            dst.fifo_drops.store(self.fifo_drops, Ordering::Relaxed);
            dst.c2h_pkts.store(self.c2h_pkts, Ordering::Relaxed);
            dst.c2h_bytes.store(self.c2h_bytes, Ordering::Relaxed);
            dst.tx_retries.store(self.tx_retries, Ordering::Relaxed);
            dst.alloc_fails.store(self.alloc_fails, Ordering::Relaxed);
            dst.credit_updates.store(self.credit_updates, Ordering::Relaxed);
            dst.credit_stalls.store(self.credit_stalls, Ordering::Relaxed);
            dst.credit_rereads.store(self.credit_rereads, Ordering::Relaxed);
            dst.credit_torn.store(self.credit_torn, Ordering::Relaxed);
            dst.taken_cached.store(taken, Ordering::Relaxed);
            dst.submitted.store(submitted, Ordering::Relaxed);
        }
    }

    struct Shared {
        mode: Mode,
        n_cores: usize,
        card_pool_size: u32,
        eth_mac: [u8; 6],
        bar4_base: usize,
        active: AtomicBool,
        quit: AtomicBool,
        cfg_gen: AtomicU64,
        reset_gen: AtomicU64,
        cfg: Mutex<RunCfg>,
        cfg_ack: [AtomicU64; MAX_CARD_CORES],
        reset_ack: [AtomicU64; MAX_CARD_CORES],
        live: Vec<AtomicCoreLive>,
        flow_q: [AtomicU64; FLOW_PROBE_SLOTS],
        reset_wall: Mutex<Instant>,
    }

    impl Shared {
        fn new(mode: Mode, n_cores: usize, card_pool_size: u32, eth_mac: [u8; 6], bar4_base: usize, init_cfg: RunCfg) -> Self {
            let mut live = Vec::with_capacity(n_cores);
            for _ in 0..n_cores {
                live.push(AtomicCoreLive::new());
            }
            Self {
                mode,
                n_cores,
                card_pool_size,
                eth_mac,
                bar4_base,
                active: AtomicBool::new(false),
                quit: AtomicBool::new(false),
                cfg_gen: AtomicU64::new(0),
                reset_gen: AtomicU64::new(0),
                cfg: Mutex::new(init_cfg),
                cfg_ack: std::array::from_fn(|_| AtomicU64::new(0)),
                reset_ack: std::array::from_fn(|_| AtomicU64::new(0)),
                live,
                flow_q: std::array::from_fn(|_| AtomicU64::new(0)),
                reset_wall: Mutex::new(Instant::now()),
            }
        }

        fn wait_ack(acks: &[AtomicU64; MAX_CARD_CORES], n_cores: usize, target: u64) {
            let t0 = Instant::now();
            while t0.elapsed() < Duration::from_secs(3) {
                if (0..n_cores).all(|c| acks[c].load(Ordering::Acquire) >= target) {
                    return;
                }
                std::hint::spin_loop();
            }
        }

        fn stats_json(
            &self,
            eth_st: Option<&PortStats<'_, '_>>,
            sdp_st: Option<&PortStats<'_, '_>>,
            pmu: Option<&CardPmu>,
        ) -> String {
            let elapsed_s = self.reset_wall.lock().unwrap().elapsed().as_secs_f64();
            let mut eth_rx_pkts = 0u64;
            let mut eth_rx_bytes = 0u64;
            let mut arp_replies = 0u64;
            let mut wire_lost = 0u64;
            let mut wire_bad = 0u64;
            let mut wire_torn = 0u64;
            let mut fifo_len = 0u64;
            let mut fifo_peak = 0u64;
            let mut fifo_drops = 0u64;
            let mut c2h_pkts = 0u64;
            let mut c2h_bytes = 0u64;
            let mut tx_retries = 0u64;
            let mut alloc_fails = 0u64;
            let mut credit_updates = 0u64;
            let mut credit_stalls = 0u64;
            let mut credit_rereads = 0u64;
            let mut credit_torn = 0u64;

            let mut per_q_rx = Vec::with_capacity(self.n_cores);
            let mut per_q_c2h = Vec::with_capacity(self.n_cores);
            let mut per_q_wl = Vec::with_capacity(self.n_cores);
            let mut per_q_fifo = Vec::with_capacity(self.n_cores);
            let mut per_q_stalls = Vec::with_capacity(self.n_cores);

            for c in 0..self.n_cores {
                let l = &self.live[c];
                let rx = l.eth_rx_pkts.load(Ordering::Relaxed);
                let c2h = l.c2h_pkts.load(Ordering::Relaxed);
                let wl = l.wire_lost.load(Ordering::Relaxed);
                let fl = l.fifo_len.load(Ordering::Relaxed);
                let st = l.credit_stalls.load(Ordering::Relaxed);
                per_q_rx.push(rx.to_string());
                per_q_c2h.push(c2h.to_string());
                per_q_wl.push(wl.to_string());
                per_q_fifo.push(fl.to_string());
                per_q_stalls.push(st.to_string());

                eth_rx_pkts += rx;
                eth_rx_bytes += l.eth_rx_bytes.load(Ordering::Relaxed);
                arp_replies += l.arp_replies.load(Ordering::Relaxed);
                wire_lost += wl;
                wire_bad += l.wire_bad.load(Ordering::Relaxed);
                wire_torn += l.wire_torn.load(Ordering::Relaxed);
                fifo_len += fl;
                fifo_peak = fifo_peak.max(l.fifo_peak.load(Ordering::Relaxed));
                fifo_drops += l.fifo_drops.load(Ordering::Relaxed);
                c2h_pkts += c2h;
                c2h_bytes += l.c2h_bytes.load(Ordering::Relaxed);
                tx_retries += l.tx_retries.load(Ordering::Relaxed);
                alloc_fails += l.alloc_fails.load(Ordering::Relaxed);
                credit_updates += l.credit_updates.load(Ordering::Relaxed);
                credit_stalls += st;
                credit_rereads += l.credit_rereads.load(Ordering::Relaxed);
                credit_torn += l.credit_torn.load(Ordering::Relaxed);
            }

            let eth_s: EthStats = eth_st.and_then(|h| h.stats().ok()).unwrap_or_default();
            let sdp_s: EthStats = sdp_st.and_then(|h| h.stats().ok()).unwrap_or_default();
            let mut rpm_ok = 0u64;
            if let Some(h) = eth_st {
                if let Ok(xs) = h.xstats() {
                    for (k, v) in xs {
                        if k == "rpm_rx_aFramesReceivedOK" {
                            rpm_ok = v;
                            break;
                        }
                    }
                }
            }

            let flow_q_str = if self.mode == Mode::ProbeRss {
                let v: Vec<String> = self.flow_q.iter().map(|a| a.load(Ordering::Relaxed).to_string()).collect();
                format!(",\"flow_q\":[{}]", v.join(","))
            } else {
                String::new()
            };
            let pmu_str = match pmu {
                Some(p) => format!(",\"pmu\":{}", p.snap_json()),
                None => String::new(),
            };
            let cfg = *self.cfg.lock().unwrap();
            format!(
                "{{\"elapsed_s\":{elapsed_s:.6},\"n_cores\":{},\"run_gen\":{},\"r_eff\":{},\"credit_bar4\":{},\
                 \"eth_rx_pkts\":{eth_rx_pkts},\"eth_rx_bytes\":{eth_rx_bytes},\"arp_replies\":{arp_replies},\
                 \"wire_lost\":{wire_lost},\"wire_bad\":{wire_bad},\"wire_torn\":{wire_torn},\
                 \"fifo_len\":{fifo_len},\"fifo_peak\":{fifo_peak},\"fifo_drops\":{fifo_drops},\
                 \"c2h_pkts\":{c2h_pkts},\"c2h_bytes\":{c2h_bytes},\"tx_retries\":{tx_retries},\"alloc_fails\":{alloc_fails},\
                 \"credit_updates\":{credit_updates},\"credit_stalls\":{credit_stalls},\
                 \"credit_rereads\":{credit_rereads},\"credit_torn\":{credit_torn},\
                 \"per_q_rx\":[{}],\"per_q_c2h\":[{}],\"per_q_wl\":[{}],\"per_q_fifo\":[{}],\"per_q_stalls\":[{}],\
                 \"eth\":{{\"ipackets\":{},\"ibytes\":{},\"imissed\":{},\"ierrors\":{},\"rx_nombuf\":{},\"rpm_rx_ok\":{}}},\
                 \"sdp\":{{\"opackets\":{},\"obytes\":{},\"oerrors\":{}}}{flow_q_str}{pmu_str}}}",
                self.n_cores,
                cfg.run_gen,
                cfg.r_eff,
                cfg.credit_bar4,
                per_q_rx.join(","),
                per_q_c2h.join(","),
                per_q_wl.join(","),
                per_q_fifo.join(","),
                per_q_stalls.join(","),
                eth_s.ipackets,
                eth_s.ibytes,
                eth_s.imissed,
                eth_s.ierrors,
                eth_s.rx_nombuf,
                rpm_ok,
                sdp_s.opackets,
                sdp_s.obytes,
                sdp_s.oerrors,
            )
        }
    }

    #[inline(always)]
    fn try_handle_arp<'e>(
        m: Mbuf<'e>,
        eth_mac: [u8; 6],
        eth_qp: &mut QueuePair<'_, 'e>,
        cnt: &mut CoreLocal,
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

    /// Read queue `q`'s dedicated 64 B BAR4 credit line from local CN106 L3 memory
    /// using ARMv8 load-acquire ordering (`w[0]` -> Acquire -> `w[1..7]` -> Acquire -> `w[7]`).
    #[inline(always)]
    unsafe fn read_credit_line(ptr: *const u64) -> CreditLine {
        let mut line = CreditLine::default();
        line.w[0] = unsafe { ptr.read_volatile() };
        std::sync::atomic::fence(Ordering::Acquire);
        for i in 1..7 {
            line.w[i] = unsafe { ptr.add(i).read_volatile() };
        }
        std::sync::atomic::fence(Ordering::Acquire);
        line.w[7] = unsafe { ptr.add(7).read_volatile() };
        line
    }

    #[inline(always)]
    fn poll_credit(
        ptr: *const u64,
        q: u64,
        run_gen: u64,
        taken_cached: &mut u64,
        bytes_cached: &mut u64,
        cnt: &mut CoreLocal,
    ) {
        const MAX_SEQLOCK_RETRIES: usize = 16;
        for attempt in 0..MAX_SEQLOCK_RETRIES {
            let line = unsafe { read_credit_line(ptr) };
            match line.verify(run_gen, q) {
                CreditCheck::Ok { taken, bytes_taken } => {
                    if attempt > 0 {
                        cnt.credit_rereads += 1;
                    }
                    if taken > *taken_cached {
                        *taken_cached = taken;
                        if bytes_taken > *bytes_cached {
                            *bytes_cached = bytes_taken;
                        }
                        cnt.credit_updates += 1;
                    }
                    return;
                }
                CreditCheck::StaleGen => return,
                CreditCheck::Torn => {
                    std::hint::spin_loop();
                }
            }
        }
        cnt.credit_torn += 1;
    }

    struct WorkerInput<'p, 'e> {
        core_idx: usize,
        eth_qp: Option<QueuePair<'p, 'e>>,
        sdp_qp: Option<QueuePair<'p, 'e>>,
        pool: Option<&'p Mempool<'e>>,
    }

    fn run_worker<'p, 'e>(sh: &Shared, mut inp: WorkerInput<'p, 'e>) {
        let c = inp.core_idx;
        let max_safe_fifo = (sh.card_pool_size as usize).saturating_sub(4608).max(256);
        let mut cfg = *sh.cfg.lock().unwrap();
        let mut seen_cfg_gen = sh.cfg_gen.load(Ordering::Acquire);
        let mut seen_reset_gen = sh.reset_gen.load(Ordering::Acquire);

        let mut rx_burst: Burst<'e, BURST_N> = Burst::new();
        let mut tx_burst: Burst<'e, BURST_N> = Burst::new();
        let mut fifo: VecDeque<Mbuf<'e>> = VecDeque::with_capacity(cfg.fifo_cap.min(max_safe_fifo));
        let mut tracker = StreamTracker::new();
        let mut cnt = CoreLocal::default();

        let mut taken_cached = 0u64;
        let mut bytes_cached = 0u64;
        let mut submitted = 0u64;
        let mut bytes_submitted = 0u64;
        let mut synth_seq = 1u64;
        let cntfrq = read_cntfrq() as f64;
        let mut next_synth_cnt = read_cntvct() as f64;
        let mut idle_iters = 0u32;

        let credit_ptr = if sh.bar4_base != 0 {
            (sh.bar4_base + credit_line_offset(c)) as *mut u64
        } else {
            std::ptr::null_mut()
        };

        let host_mac = [0x02, 0x00, 0x00, 0x00, 0x21, 0x40];

        while !sh.quit.load(Ordering::Acquire) {
            // Dynamic CONFIG barrier: drain old FIFO, reset credit state & BAR4 line, apply new R_eff.
            let cg = sh.cfg_gen.load(Ordering::Acquire);
            if cg != seen_cfg_gen {
                fifo.clear();
                tx_burst.clear();
                rx_burst.clear();
                if let Some(eth) = inp.eth_qp.as_mut() {
                    while eth.rx(&mut rx_burst) > 0 {
                        rx_burst.clear();
                    }
                }
                if !credit_ptr.is_null() {
                    for i in 0..8 {
                        unsafe { credit_ptr.add(i).write_volatile(0) };
                    }
                }
                cfg = *sh.cfg.lock().unwrap();
                let eff_cap = cfg.fifo_cap.min(max_safe_fifo);
                if fifo.capacity() < eff_cap {
                    fifo.reserve(eff_cap - fifo.capacity());
                }
                taken_cached = 0;
                bytes_cached = 0;
                submitted = 0;
                bytes_submitted = 0;
                synth_seq = 1;
                next_synth_cnt = read_cntvct() as f64;
                tracker.reset();
                cnt = CoreLocal::default();
                cnt.publish(&sh.live[c], 0, 0, 0);
                seen_cfg_gen = cg;
                sh.cfg_ack[c].store(cg, Ordering::Release);
                continue;
            }

            // Counter-only RESET barrier (preserves live credit continuity if already running).
            let rg = sh.reset_gen.load(Ordering::Acquire);
            if rg != seen_reset_gen {
                tracker.reset();
                cnt = CoreLocal::default();
                cnt.fifo_peak = fifo.len() as u64;
                cnt.publish(&sh.live[c], fifo.len(), taken_cached, submitted);
                seen_reset_gen = rg;
                sh.reset_ack[c].store(rg, Ordering::Release);
            }

            let eff_cap = cfg.fifo_cap.min(max_safe_fifo);
            let fk = cfg.frame_kind;
            let plen = cfg.payload_bytes;
            let is_uc_zero = fk == FrameKind::Uc64 && plen == 0;
            let pay_off = fk.payload_offset();
            let frame_l2 = fk.l2_bytes(plen) as u64;
            let active = sh.active.load(Ordering::Relaxed);
            let mut did_work = false;

            match sh.mode {
                Mode::ProbeRss => {
                    let eth = inp.eth_qp.as_mut().unwrap();
                    let n = eth.rx(&mut rx_burst);
                    if n > 0 {
                        did_work = true;
                        rx_burst.drain_into(|m| {
                            let Some(m) = try_handle_arp(m, sh.eth_mac, eth, &mut cnt) else {
                                return;
                            };
                            let dlen = m.data_len();
                            let p = m.data_ptr();
                            cnt.eth_rx_pkts += 1;
                            if is_uc_zero {
                                if dlen >= 60 {
                                    let bth_op = unsafe { *p.add(OFF_BTH) };
                                    let dst_qp = ((unsafe { *p.add(OFF_BTH + 5) } as u32) << 16)
                                        | ((unsafe { *p.add(OFF_BTH + 6) } as u32) << 8)
                                        | (unsafe { *p.add(OFF_BTH + 7) } as u32);
                                    if bth_op == 0x24 && dst_qp >= UC64_BASE_QPN {
                                        let sid = (dst_qp - UC64_BASE_QPN) as usize;
                                        if sid < FLOW_PROBE_SLOTS {
                                            sh.flow_q[sid].store((c + 1) as u64, Ordering::Relaxed);
                                        }
                                    }
                                }
                            } else if dlen >= pay_off + plen && plen >= 32 {
                                let slice = unsafe { std::slice::from_raw_parts(p.add(pay_off), plen) };
                                if let PayloadCheck::Ok { stream_id, .. } = verify_payload(slice, plen) {
                                    let sid = stream_id as usize;
                                    if sid < FLOW_PROBE_SLOTS {
                                        sh.flow_q[sid].store((c + 1) as u64, Ordering::Relaxed);
                                    }
                                }
                            }
                        });
                    }
                }
                Mode::Wire => {
                    let eth = inp.eth_qp.as_mut().unwrap();
                    let sdp = inp.sdp_qp.as_mut().unwrap();

                    if cfg.credit_bar4 && !credit_ptr.is_null() {
                        poll_credit(
                            credit_ptr,
                            c as u64,
                            cfg.run_gen,
                            &mut taken_cached,
                            &mut bytes_cached,
                            &mut cnt,
                        );
                    }

                    // 1. Poll eth0 RX and admit verified packets into elastic FIFO (or fast-path tx_burst).
                    let n = eth.rx(&mut rx_burst);
                    if n > 0 {
                        did_work = true;
                        rx_burst.drain_into(|m| {
                            let Some(m) = try_handle_arp(m, sh.eth_mac, eth, &mut cnt) else {
                                return;
                            };
                            if !active {
                                return;
                            }
                            let dlen = m.data_len();
                            let p = m.data_ptr();
                            cnt.eth_rx_pkts += 1;
                            cnt.eth_rx_bytes += (dlen + 4) as u64;

                            if is_uc_zero {
                                if dlen < 60 {
                                    cnt.wire_bad += 1;
                                    return;
                                }
                                let etype = u16::from_be_bytes(unsafe {
                                    [*p.add(OFF_ETH_TYPE), *p.add(OFF_ETH_TYPE + 1)]
                                });
                                let bth_op = unsafe { *p.add(OFF_BTH) };
                                let dst_qp = ((unsafe { *p.add(OFF_BTH + 5) } as u32) << 16)
                                    | ((unsafe { *p.add(OFF_BTH + 6) } as u32) << 8)
                                    | (unsafe { *p.add(OFF_BTH + 7) } as u32);
                                let psn = ((unsafe { *p.add(OFF_BTH + 9) } as u64) << 16)
                                    | ((unsafe { *p.add(OFF_BTH + 10) } as u64) << 8)
                                    | (unsafe { *p.add(OFF_BTH + 11) } as u64);
                                if etype != 0x0800 || bth_op != 0x24 || dst_qp < UC64_BASE_QPN {
                                    cnt.wire_bad += 1;
                                    return;
                                }
                                cnt.wire_lost += tracker.observe_uc64(dst_qp - UC64_BASE_QPN, psn);
                            } else {
                                if dlen < pay_off + plen {
                                    cnt.wire_bad += 1;
                                    return;
                                }
                                let slice = unsafe { std::slice::from_raw_parts(p.add(pay_off), plen) };
                                match verify_payload(slice, plen) {
                                    PayloadCheck::Ok { stream_id, seq, .. } => {
                                        cnt.wire_lost += tracker.observe_seq(stream_id, seq);
                                    }
                                    PayloadCheck::Torn => {
                                        cnt.wire_torn += 1;
                                        return;
                                    }
                                    PayloadCheck::Bad => {
                                        cnt.wire_bad += 1;
                                        return;
                                    }
                                }
                            }

                            // Direct zero-queue fast path when FIFO is empty and credit allows;
                            // otherwise buffer in elastic software FIFO up to `eff_cap`.
                            let avail_now = if cfg.credit_bar4 {
                                let in_burst = tx_burst.len() as u64;
                                if cfg.r_eff_bytes > 0 {
                                    bytes_cached
                                        .saturating_add(cfg.r_eff_bytes)
                                        .saturating_sub(bytes_submitted + in_burst * frame_l2)
                                        / frame_l2
                                } else {
                                    taken_cached
                                        .saturating_add(cfg.r_eff)
                                        .saturating_sub(submitted + in_burst)
                                }
                            } else {
                                BURST_N as u64
                            };

                            if fifo.is_empty() && avail_now > 0 && !tx_burst.is_full() {
                                let _ = tx_burst.push(m);
                            } else if fifo.len() < eff_cap {
                                fifo.push_back(m);
                                let fl = fifo.len() as u64;
                                if fl > cnt.fifo_peak {
                                    cnt.fifo_peak = fl;
                                }
                            } else {
                                cnt.fifo_drops += 1;
                            }
                        });
                    }

                    // 2. Drain elastic FIFO into tx_burst strictly respecting BAR4 credit limit R_eff.
                    if !fifo.is_empty() {
                        let avail_credit = if cfg.credit_bar4 {
                            let in_burst = tx_burst.len() as u64;
                            if cfg.r_eff_bytes > 0 {
                                bytes_cached
                                    .saturating_add(cfg.r_eff_bytes)
                                    .saturating_sub(bytes_submitted + in_burst * frame_l2)
                                    / frame_l2
                            } else {
                                taken_cached
                                    .saturating_add(cfg.r_eff)
                                    .saturating_sub(submitted + in_burst)
                            }
                        } else {
                            BURST_N as u64
                        };

                        if cfg.credit_bar4 && avail_credit == 0 && tx_burst.is_empty() {
                            cnt.credit_stalls += 1;
                        } else {
                            let can_pop = (avail_credit as usize).min(BURST_N - tx_burst.len());
                            for _ in 0..can_pop {
                                if let Some(m) = fifo.pop_front() {
                                    let _ = tx_burst.push(m);
                                } else {
                                    break;
                                }
                            }
                        }
                    }

                    // 3. Flush tx_burst to sdp1-1 TX queue `c` (never drop already-admitted packets).
                    if !tx_burst.is_empty() {
                        did_work = true;
                        let before = tx_burst.len();
                        let sent = sdp.tx(&mut tx_burst);
                        if sent > 0 {
                            submitted += sent as u64;
                            bytes_submitted += (sent as u64) * frame_l2;
                            cnt.c2h_pkts += sent as u64;
                            cnt.c2h_bytes += (sent as u64) * frame_l2;
                        }
                        if sent < before {
                            cnt.tx_retries += 1;
                        }
                    }
                }
                Mode::Synth => {
                    let sdp = inp.sdp_qp.as_mut().unwrap();
                    let pool = inp.pool.unwrap();

                    if cfg.credit_bar4 && !credit_ptr.is_null() {
                        poll_credit(
                            credit_ptr,
                            c as u64,
                            cfg.run_gen,
                            &mut taken_cached,
                            &mut bytes_cached,
                            &mut cnt,
                        );
                    }

                    if active {
                        let per_core_ticks = if cfg.synth_mpps > 0.0 {
                            let core_mpps = cfg.synth_mpps / (sh.n_cores as f64);
                            cntfrq / (core_mpps * 1e6)
                        } else {
                            0.0
                        };
                        let mut want = 32usize;
                        if per_core_ticks > 0.0 {
                            let now = read_cntvct() as f64;
                            if now < next_synth_cnt {
                                want = 0;
                            } else {
                                let due = ((now - next_synth_cnt) / per_core_ticks) as usize + 1;
                                want = due.min(32);
                                next_synth_cnt += (want as f64) * per_core_ticks;
                                if now - next_synth_cnt > per_core_ticks * 64.0 {
                                    next_synth_cnt = now;
                                }
                            }
                        }
                        let synth_plen = plen.max(32);
                        let mbuf_len = (OFF_UDP_PAYLOAD + synth_plen) as u16;
                        let l2_synth = (mbuf_len as u64) + 4;
                        let room = eff_cap.saturating_sub(fifo.len());
                        let n_gen = want.min(room);
                        if want > room {
                            cnt.fifo_drops += (want - room) as u64;
                        }
                        for _ in 0..n_gen {
                            if let Some(mut m) = pool.alloc() {
                                if let Some(buf) = m.append(mbuf_len) {
                                    write_synth_udp_frame(
                                        buf,
                                        host_mac,
                                        sh.eth_mac,
                                        c as u32,
                                        synth_seq,
                                        cfg.run_gen as u16,
                                    );
                                    synth_seq += 1;
                                    fifo.push_back(m);
                                    cnt.eth_rx_pkts += 1;
                                    cnt.eth_rx_bytes += l2_synth;
                                }
                            } else {
                                cnt.alloc_fails += 1;
                                break;
                            }
                        }
                        let fl = fifo.len() as u64;
                        if fl > cnt.fifo_peak {
                            cnt.fifo_peak = fl;
                        }

                        if !fifo.is_empty() {
                            let avail_credit = if cfg.credit_bar4 {
                                let in_burst = tx_burst.len() as u64;
                                if cfg.r_eff_bytes > 0 {
                                    bytes_cached
                                        .saturating_add(cfg.r_eff_bytes)
                                        .saturating_sub(bytes_submitted + in_burst * l2_synth)
                                        / l2_synth
                                } else {
                                    taken_cached
                                        .saturating_add(cfg.r_eff)
                                        .saturating_sub(submitted + in_burst)
                                }
                            } else {
                                BURST_N as u64
                            };
                            if cfg.credit_bar4 && avail_credit == 0 && tx_burst.is_empty() {
                                cnt.credit_stalls += 1;
                            } else {
                                let can_pop = (avail_credit as usize).min(BURST_N - tx_burst.len());
                                for _ in 0..can_pop {
                                    if let Some(m) = fifo.pop_front() {
                                        let _ = tx_burst.push(m);
                                    } else {
                                        break;
                                    }
                                }
                            }
                        }

                        if !tx_burst.is_empty() {
                            did_work = true;
                            let before = tx_burst.len();
                            let sent = sdp.tx(&mut tx_burst);
                            if sent > 0 {
                                submitted += sent as u64;
                                bytes_submitted += (sent as u64) * l2_synth;
                                cnt.c2h_pkts += sent as u64;
                                cnt.c2h_bytes += (sent as u64) * l2_synth;
                            }
                            if sent < before {
                                cnt.tx_retries += 1;
                            }
                        }
                    }
                }
            }

            if did_work {
                idle_iters = 0;
                cnt.publish(&sh.live[c], fifo.len(), taken_cached, submitted);
            } else {
                idle_iters = idle_iters.wrapping_add(1);
                if idle_iters & 0x3ff == 0 {
                    cnt.publish(&sh.live[c], fifo.len(), taken_cached, submitted);
                }
            }
        }

        fifo.clear();
        tx_burst.clear();
        rx_burst.clear();
        cnt.publish(&sh.live[c], 0, taken_cached, submitted);
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        if std::env::args().any(|a| a == "--restore") {
            let changed = restore_defaults(SLOT)?;
            println!("MRV214_CARD RESTORED changes={}", changed.len());
            for line in changed {
                println!("  {line}");
            }
            return Ok(());
        }

        let (eal, rest) = Eal::init(std::env::args())?;
        let args = Args::parse_from(rest);

        let total_lcores = eal.lcores();
        assert!(
            total_lcores >= 2 && total_lcores - 1 <= MAX_CARD_CORES,
            "need 1 main lcore + 1..={MAX_CARD_CORES} worker lcores (got {total_lcores})"
        );
        let n_cores = total_lcores - 1;
        let init_fk = FrameKind::parse(&args.frame_kind)
            .ok_or_else(|| format!("invalid --frame-kind {}", args.frame_kind))?;

        let need_eth = matches!(args.mode, Mode::Wire | Mode::ProbeRss);
        let need_sdp = matches!(args.mode, Mode::Wire | Mode::Synth);
        let need_bar4 = matches!(args.mode, Mode::Wire | Mode::Synth);

        let data_room = 2048 + 128;
        let mut pools: Vec<Mempool<'_>> = Vec::with_capacity(n_cores);
        for c in 0..n_cores {
            pools.push(Mempool::new_with_cache(
                &eal,
                &format!("mrv214_p{c}"),
                args.card_pool_size,
                256,
                data_room,
                0,
            )?);
        }
        let pool_refs: Vec<&Mempool<'_>> = pools.iter().collect();

        let mut eth_port: Option<Port<'_>> = None;
        let mut eth_mac = [0x40, 0x60, 0x5a, 0x02, 0xed, 0x68];
        let mut eth_rss_hf = 0u64;
        if need_eth {
            let pid = Port::by_name(&eal, &args.eth_dev)?;
            let (port, _) = Port::start_fast(
                &eal,
                pid,
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
            let _ = port.promiscuous();
            eth_mac = port.mac();
            eth_rss_hf = port.rss_hf();
            let _ = port.stats_reset();
            let _ = port.xstats_reset();
            eth_port = Some(port);
        }

        let mut sdp_port: Option<Port<'_>> = None;
        if need_sdp {
            let pid = Port::by_name(&eal, &args.sdp_dev)?;
            let (port, _) = Port::start_fast(
                &eal,
                pid,
                &pool_refs,
                1,
                n_cores as u16,
                512,
                SDP_DESC,
                None,
                false,
                true,
                false,
            )?;
            let _ = port.stats_reset();
            sdp_port = Some(port);
        }

        let mut bar_mz_hold: Option<Memzone<'_>> = None;
        let mut bar_win_hold: Option<Bar4Window> = None;
        let mut bar4_base = 0usize;
        if need_bar4 {
            let mz = Memzone::reserve_contiguous(&eal, "mrv214_bar4", SLOT_BYTES, 0, SLOT_BYTES as u32)?;
            unsafe { std::ptr::write_bytes(mz.as_ptr(), 0, SLOT_BYTES) };
            let pa = mz.phys_addr().ok_or("BAR4 memzone phys_addr failed")?;
            let win = Bar4Window::claim(SLOT, pa)?;
            bar4_base = mz.as_ptr() as usize;
            unsafe {
                (&*((bar4_base + READY_OFFSET) as *const AtomicU64)).store(READY, Ordering::Release);
                (&*((bar4_base + CORES_OFFSET) as *const AtomicU64)).store(n_cores as u64, Ordering::Release);
            }
            bar_win_hold = Some(win);
            bar_mz_hold = Some(mz);
        }

        let init_cfg = RunCfg {
            run_gen: 1,
            r_eff: args.r_eff,
            r_eff_bytes: args.r_eff_bytes,
            credit_bar4: !args.no_credit,
            frame_kind: init_fk,
            payload_bytes: args.payload_bytes,
            fifo_cap: args.fifo_cap,
            synth_mpps: 0.0,
        };
        let sh = Arc::new(Shared::new(args.mode, n_cores, args.card_pool_size, eth_mac, bar4_base, init_cfg));

        let quit_sig = Arc::clone(&sh);
        let _ = ctrlc::set_handler(move || {
            quit_sig.active.store(false, Ordering::Release);
            quit_sig.quit.store(true, Ordering::Release);
        });

        let eth_stats = eth_port.as_ref().map(|p| p.stats_handle());
        let sdp_stats = sdp_port.as_ref().map(|p| p.stats_handle());

        let mut eth_qps: Vec<Option<QueuePair<'_, '_>>> = match eth_port.as_ref() {
            Some(p) => p.split_queue_pairs().into_iter().map(Some).collect(),
            None => (0..n_cores).map(|_| None).collect(),
        };
        let mut sdp_qps: Vec<Option<QueuePair<'_, '_>>> = match sdp_port.as_ref() {
            Some(p) => p.split_queue_pairs().into_iter().map(Some).collect(),
            None => (0..n_cores).map(|_| None).collect(),
        };

        // Build inputs: index 0 is main lcore (control + lease + PMU), indices 1..=n_cores are workers.
        let mut inputs: Vec<Option<WorkerInput<'_, '_>>> = Vec::with_capacity(total_lcores);
        inputs.push(None);
        for c in 0..n_cores {
            inputs.push(Some(WorkerInput {
                core_idx: c,
                eth_qp: eth_qps[c].take(),
                sdp_qp: sdp_qps[c].take(),
                pool: Some(&pools[c]),
            }));
        }

        let sh_ref = &sh;
        let pmu = CardPmu::open();
        let ctrl_port = args.ctrl_port;
        let lease_addr = args.lease_addr.clone();

        eal.on_each_lcore(inputs, move |lcore_idx, inp_opt| {
            if lcore_idx > 0 {
                run_worker(sh_ref, inp_opt.unwrap());
                return;
            }

            // Main lcore: serve MRV-26 BAR4 lease + TCP control server inside std::thread::scope.
            thread::scope(|scope| {
                let _lease_srv = if need_bar4 {
                    let listener = TcpListener::bind(&lease_addr).expect("bind MRV-26 lease listener");
                    let nonce_word: &AtomicU64 =
                        unsafe { &*((bar4_base + NONCE_OFFSET) as *const AtomicU64) };
                    Some(lease::serve(scope, listener, nonce_word).expect("start lease server"))
                } else {
                    None
                };

                let ctrl = TcpListener::bind(("0.0.0.0", ctrl_port)).expect("bind card ctrl port");
                ctrl.set_nonblocking(true).expect("ctrl nonblocking");
                println!(
                    "MRV214_CARD READY mode={:?} cores={} rss_hf={:#x} ctrl_port={} bar4={}",
                    sh_ref.mode, n_cores, eth_rss_hf, ctrl_port, need_bar4
                );

                while !sh_ref.quit.load(Ordering::Acquire) {
                    match ctrl.accept() {
                        Ok((mut sock, _)) => {
                            let _ = sock.set_nonblocking(false);
                            let _ = sock.set_read_timeout(Some(Duration::from_secs(5)));
                            let mut reader = BufReader::new(sock.try_clone().unwrap());
                            let mut line = String::new();
                            if reader.read_line(&mut line).unwrap_or(0) == 0 {
                                continue;
                            }
                            let cmd = line.trim();
                            let reply = if cmd == "PING" {
                                "{\"ok\":true}".to_string()
                            } else if let Some(rest) = cmd.strip_prefix("CONFIG ") {
                                sh_ref.active.store(false, Ordering::Release);
                                let mut next = *sh_ref.cfg.lock().unwrap();
                                let mut parse_err: Option<String> = None;
                                for (k, v) in parse_kv_tokens(rest) {
                                    match k {
                                        "run_gen" => next.run_gen = v.parse().unwrap_or(next.run_gen),
                                        "r_eff" => next.r_eff = v.parse().unwrap_or(next.r_eff),
                                        "r_eff_bytes" => {
                                            next.r_eff_bytes = v.parse().unwrap_or(next.r_eff_bytes)
                                        }
                                        "credit" => {
                                            next.credit_bar4 = match v {
                                                "bar4" | "bar4-nofence" | "bar4-sfence" | "on" | "1" => true,
                                                "off" | "0" => false,
                                                _ => {
                                                    parse_err = Some(format!("bad credit={v}"));
                                                    next.credit_bar4
                                                }
                                            };
                                        }
                                        "frame_kind" => match FrameKind::parse(v) {
                                            Some(fk) => next.frame_kind = fk,
                                            None => parse_err = Some(format!("bad frame_kind={v}")),
                                        },
                                        "payload" => next.payload_bytes = v.parse().unwrap_or(next.payload_bytes),
                                        "fifo_cap" => next.fifo_cap = v.parse().unwrap_or(next.fifo_cap),
                                        "synth_mpps" => next.synth_mpps = v.parse().unwrap_or(next.synth_mpps),
                                        _ => {}
                                    }
                                }
                                if let Some(err) = parse_err {
                                    format!("{{\"ok\":false,\"err\":\"{err}\"}}")
                                } else {
                                    *sh_ref.cfg.lock().unwrap() = next;
                                    let epoch = sh_ref.cfg_gen.fetch_add(1, Ordering::AcqRel) + 1;
                                    Shared::wait_ack(&sh_ref.cfg_ack, n_cores, epoch);
                                    for a in &sh_ref.flow_q {
                                        a.store(0, Ordering::Relaxed);
                                    }
                                    if let Some(h) = eth_stats.as_ref() {
                                        let _ = h.stats_reset();
                                        let _ = h.xstats_reset();
                                    }
                                    if let Some(h) = sdp_stats.as_ref() {
                                        let _ = h.stats_reset();
                                    }
                                    *sh_ref.reset_wall.lock().unwrap() = Instant::now();
                                    format!(
                                        "{{\"ok\":true,\"run_gen\":{},\"r_eff\":{},\"r_eff_bytes\":{},\"credit_bar4\":{},\"frame_kind\":\"{}\",\"payload\":{},\"fifo_cap\":{}}}",
                                        next.run_gen,
                                        next.r_eff,
                                        next.r_eff_bytes,
                                        next.credit_bar4,
                                        next.frame_kind.as_str(),
                                        next.payload_bytes,
                                        next.fifo_cap
                                    )
                                }
                            } else if cmd == "START" {
                                sh_ref.active.store(true, Ordering::Release);
                                "{\"ok\":true}".to_string()
                            } else if cmd == "STOP" {
                                sh_ref.active.store(false, Ordering::Release);
                                "{\"ok\":true}".to_string()
                            } else if cmd == "RESET" {
                                for a in &sh_ref.flow_q {
                                    a.store(0, Ordering::Relaxed);
                                }
                                let epoch = sh_ref.reset_gen.fetch_add(1, Ordering::AcqRel) + 1;
                                Shared::wait_ack(&sh_ref.reset_ack, n_cores, epoch);
                                if let Some(h) = eth_stats.as_ref() {
                                    let _ = h.stats_reset();
                                    let _ = h.xstats_reset();
                                }
                                if let Some(h) = sdp_stats.as_ref() {
                                    let _ = h.stats_reset();
                                }
                                *sh_ref.reset_wall.lock().unwrap() = Instant::now();
                                "{\"ok\":true}".to_string()
                            } else if cmd == "PMU_SNAP" {
                                pmu.snap_json()
                            } else if cmd == "SNAP" {
                                sh_ref.stats_json(eth_stats.as_ref(), sdp_stats.as_ref(), Some(&pmu))
                            } else if cmd == "STATS" {
                                sh_ref.stats_json(eth_stats.as_ref(), sdp_stats.as_ref(), None)
                            } else if cmd == "QUIT" {
                                sh_ref.active.store(false, Ordering::Release);
                                sh_ref.quit.store(true, Ordering::Release);
                                let _ = writeln!(sock, "{{\"ok\":true}}");
                                break;
                            } else {
                                "{\"ok\":false,\"err\":\"unknown cmd\"}".to_string()
                            };
                            let _ = writeln!(sock, "{reply}");
                        }
                        Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(1));
                        }
                        Err(_) => {
                            thread::sleep(Duration::from_millis(1));
                        }
                    }
                }

                // Cleanly revoke BAR4 lease BEFORE restoring BAR4 slot 15.
                if let Some(srv) = _lease_srv {
                    srv.close();
                }
            });
        });

        if need_bar4 {
            unsafe {
                (&*((bar4_base + READY_OFFSET) as *const AtomicU64)).store(0, Ordering::Release);
            }
        }
        drop(bar_win_hold);
        drop(bar_mz_hold);
        drop(sdp_port);
        drop(eth_port);
        Ok(())
    }
}
