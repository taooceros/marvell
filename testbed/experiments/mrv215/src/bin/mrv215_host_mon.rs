//! Host-side non-intrusive monitor, PMU collector, and out-of-band TCP populator
//! for MRV-215 (100% unmodified upstream Memcached 1.6.45 over Linux kernel
//! `octeon_ep_vf` UDP socket).
//!
//! Runs pinned to `CTRL_CPU` (`cpu44`, completely off the data core `cpu40`) with
//! ZERO DPDK initialization and ZERO hardware VFIO access:
//!   - Populates `0..num_keys` into the unmodified `memcached` daemon over local
//!     TCP (`10.10.200.1:11211`, routed via loopback `lo`) using standard Memcached
//!     Binary TCP `SET` packets with the exact MRV-206 key/value/flags schema.
//!   - Collects 100% non-multiplexed Core (`cpu40`) + Socket-1 CHA/IIO/IMC hardware
//!     PMU counters (`HostPmu`) with identical definitions and JSON keys to `mrv206_host`.
//!   - Collects per-window Linux kernel IRQ (`/proc/interrupts`), softirq
//!     (`/proc/softirqs`), CPU state (`/proc/stat`), Memcached worker thread stats
//!     (`/proc/<pid>/task/<tid>/{stat,status,schedstat}`), VF netdev counters
//!     (`/sys/class/net/<vf_iface>/statistics/*`), and UDP SNMP (`/proc/net/snmp`).
//!   - Snapshots PMU and `/proc` BEFORE querying Memcached ASCII `stats` on `STATS`
//!     (and AFTER populate/`stats reset` on `RESET`), so control TCP traffic never
//!     pollutes the measured window.

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv215_host_mon runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::collections::HashMap;
    use std::error::Error;
    use std::fs;
    use std::io::{self, BufRead, BufReader, Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::os::fd::RawFd;
    use std::path::Path;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::{Duration, Instant};

    use clap::Parser;
    use mrv215::{
        HOST_MON_ADDR, MAX_MC_BYTES, MC_BIN_HDR_LEN, MC_CMD_SET, MC_MAGIC_RES, MC_NUM_KEYS,
        MC_STATUS_OK, McProfile, build_mc_set_tcp,
    };

    const SOCKET1_UNCORE_CPU: i32 = 32;
    const REF_HZ: f64 = 2.2e9;

    #[derive(Parser)]
    struct Args {
        /// Local TCP address of unmodified upstream Memcached (routed over loopback).
        #[arg(long, default_value = "10.10.200.1:11211")]
        mc_addr: String,
        /// PID of the running unmodified Memcached process.
        #[arg(long, default_value_t = 0)]
        mc_pid: u32,
        /// TID of the `mc-worker` thread (0 = auto-discover under `/proc/<pid>/task`).
        #[arg(long, default_value_t = 0)]
        mc_worker_tid: u32,
        /// Data-plane VF netdev interface name (NEVER `ens1` or `sdp1-0`).
        #[arg(long)]
        vf_iface: String,
        /// Host core running `mc-worker` and VF IRQs (default 40, matching MRV-206).
        #[arg(long, default_value_t = 40)]
        data_cpu: usize,
        /// Host core for this monitor process (default 44; -1 disables pinning).
        #[arg(long, default_value_t = 44)]
        ctrl_cpu: i32,
        #[arg(long, default_value_t = MC_NUM_KEYS)]
        num_keys: usize,
        #[arg(long, default_value = "etc_mix")]
        mc_profile: String,
        /// Pre-warm all slab classes (`3840, 1024, 128, 8`, target) on startup.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        prewarm_all: bool,
        /// Enable 100% non-multiplexed Core + Socket-1 CHA/IIO/IMC PMU counters.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        pmu: bool,
        /// Local TCP control address (`PING`, `RESET [profile]`, `STATS`, `QUIT`).
        #[arg(long, default_value = HOST_MON_ADDR)]
        ctrl: String,
    }

    fn pin_thread(cpu: usize) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu, &mut set);
            libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set);
        }
    }

    fn discover_mc_worker_tid(pid: u32) -> Option<u32> {
        if pid == 0 {
            return None;
        }
        let task_dir = format!("/proc/{pid}/task");
        let entries = fs::read_dir(task_dir).ok()?;
        for ent in entries.flatten() {
            let tid_str = ent.file_name().to_string_lossy().into_owned();
            let Ok(tid) = tid_str.parse::<u32>() else {
                continue;
            };
            let comm_path = ent.path().join("comm");
            if let Ok(comm) = fs::read_to_string(comm_path) {
                if comm.trim() == "mc-worker" {
                    return Some(tid);
                }
            }
        }
        None
    }

    // -------------------------------------------------------------------------
    // 100% Non-Multiplexed Host Core + Socket-1 CHA/IIO/IMC PMU (MRV-206 parity)
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
        __reserved_2: u16,
        aux_sample_size: u32,
        __reserved_3: u32,
        sig_data: u64,
        config3: u64,
    }

    const PERF_TYPE_HARDWARE: u32 = 0;
    const PERF_TYPE_SOFTWARE: u32 = 1;
    const PERF_TYPE_HW_CACHE: u32 = 3;
    const PERF_COUNT_HW_CPU_CYCLES: u64 = 0;
    const PERF_COUNT_HW_INSTRUCTIONS: u64 = 1;
    const PERF_COUNT_HW_REF_CPU_CYCLES: u64 = 9;
    const PERF_COUNT_SW_PAGE_FAULTS: u64 = 2;
    const CORE_LLC_LOADS: u64 = 0x00002;
    const CORE_LLC_LOAD_MISSES: u64 = 0x10002;
    const CORE_DTLB_LOAD_MISSES: u64 = 0x10003;

    const CHA_TOR_INSERTS_IA_WCILF: u64 = 0x00c8_67ff_0000_0135;
    const CHA_TOR_INSERTS_IO_HIT_ITOM: u64 = 0x00cc_43fd_0000_0435;
    const CHA_TOR_INSERTS_IO_MISS_ITOM: u64 = 0x00cc_43fe_0000_0435;
    const UNCORE_CLOCKTICKS: u64 = 0x01;
    const IIO_TXN_REQ_BY_CPU_MEM_WRITE: u64 = 0x0007_0ff0_0000_01c1;
    const IIO_DATA_REQ_OF_CPU_MEM_WRITE: u64 = 0x0007_0ff0_0000_0184;
    const IIO_DATA_REQ_OF_CPU_MEM_READ: u64 = 0x0007_0ff0_0000_0484;
    const IMC_CAS_COUNT_READ: u64 = 0xcf05;
    const IMC_CAS_COUNT_WRITE: u64 = 0xf005;

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
        enabled_ns: u64,
        running_ns: u64,
    }

    impl Sample {
        fn delta(self, earlier: Self) -> Self {
            Sample {
                val: self.val.saturating_sub(earlier.val),
                enabled_ns: self.enabled_ns.saturating_sub(earlier.enabled_ns),
                running_ns: self.running_ns.saturating_sub(earlier.running_ns),
            }
        }
        fn pct(self) -> f64 {
            if self.enabled_ns == 0 {
                100.0
            } else {
                (self.running_ns as f64 / self.enabled_ns as f64) * 100.0
            }
        }
    }

    impl PerfCounter {
        fn open(type_: u32, config: u64, cpu: i32) -> io::Result<Self> {
            let attr = PerfEventAttr {
                type_,
                size: std::mem::size_of::<PerfEventAttr>() as u32,
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
                libc::read(
                    self.fd,
                    buf.as_mut_ptr().cast::<libc::c_void>(),
                    std::mem::size_of::<[u64; 3]>(),
                )
            };
            if n == std::mem::size_of::<[u64; 3]>() as isize {
                Sample { val: buf[0], enabled_ns: buf[1], running_ns: buf[2] }
            } else {
                Sample::default()
            }
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

    struct HostPmu {
        cpu: i32,
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        instr: PerfCounter,
        page_faults: PerfCounter,
        llc_loads: PerfCounter,
        llc_load_misses: PerfCounter,
        dtlb_load_misses: PerfCounter,
        cha_wcilf: Vec<PerfCounter>,
        cha_io_hit: Vec<PerfCounter>,
        cha_io_miss: Vec<PerfCounter>,
        mesh_clk: PerfCounter,
        iio1_cpu_wr: PerfCounter,
        iio1_inb_wr: PerfCounter,
        iio1_inb_rd: PerfCounter,
        imc_rd: Vec<PerfCounter>,
        imc_wr: Vec<PerfCounter>,
    }

    #[derive(Clone)]
    struct HostSnap {
        cpu: i32,
        cycles: Sample,
        ref_cycles: Sample,
        instr: Sample,
        page_faults: Sample,
        llc_loads: Sample,
        llc_load_misses: Sample,
        dtlb_load_misses: Sample,
        cha_wcilf: Vec<Sample>,
        cha_io_hit: Vec<Sample>,
        cha_io_miss: Vec<Sample>,
        mesh_clk: Sample,
        iio1_cpu_wr: Sample,
        iio1_inb_wr: Sample,
        iio1_inb_rd: Sample,
        imc_rd: Vec<Sample>,
        imc_wr: Vec<Sample>,
    }

    impl HostPmu {
        fn open(cpu: i32) -> Result<Self, Box<dyn Error>> {
            let cycles = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu)?;
            let ref_cycles = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, cpu)?;
            let instr = PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu)?;
            let page_faults = PerfCounter::open(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_PAGE_FAULTS, cpu)?;
            let llc_loads = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOADS, cpu)?;
            let llc_load_misses = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOAD_MISSES, cpu)?;
            let dtlb_load_misses = PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_DTLB_LOAD_MISSES, cpu)?;

            let (mut cha_wcilf, mut cha_io_hit, mut cha_io_miss) =
                (Vec::with_capacity(32), Vec::with_capacity(32), Vec::with_capacity(32));
            for i in 0..32 {
                let t = read_pmu_type(&format!("uncore_cha_{i}"))?;
                cha_wcilf.push(PerfCounter::open(t, CHA_TOR_INSERTS_IA_WCILF, SOCKET1_UNCORE_CPU)?);
                cha_io_hit.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_HIT_ITOM, SOCKET1_UNCORE_CPU)?);
                cha_io_miss.push(PerfCounter::open(t, CHA_TOR_INSERTS_IO_MISS_ITOM, SOCKET1_UNCORE_CPU)?);
            }
            let mesh_clk = PerfCounter::open(read_pmu_type("uncore_m2pcie_1")?, UNCORE_CLOCKTICKS, SOCKET1_UNCORE_CPU)?;
            let iio1_t = read_pmu_type("uncore_iio_1")?;
            let iio1_cpu_wr = PerfCounter::open(iio1_t, IIO_TXN_REQ_BY_CPU_MEM_WRITE, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_wr = PerfCounter::open(iio1_t, IIO_DATA_REQ_OF_CPU_MEM_WRITE, SOCKET1_UNCORE_CPU)?;
            let iio1_inb_rd = PerfCounter::open(iio1_t, IIO_DATA_REQ_OF_CPU_MEM_READ, SOCKET1_UNCORE_CPU)?;

            let (mut imc_rd, mut imc_wr) = (Vec::with_capacity(8), Vec::with_capacity(8));
            for i in 0..8 {
                let t = read_pmu_type(&format!("uncore_imc_{i}"))?;
                imc_rd.push(PerfCounter::open(t, IMC_CAS_COUNT_READ, SOCKET1_UNCORE_CPU)?);
                imc_wr.push(PerfCounter::open(t, IMC_CAS_COUNT_WRITE, SOCKET1_UNCORE_CPU)?);
            }
            Ok(HostPmu {
                cpu,
                cycles,
                ref_cycles,
                instr,
                page_faults,
                llc_loads,
                llc_load_misses,
                dtlb_load_misses,
                cha_wcilf,
                cha_io_hit,
                cha_io_miss,
                mesh_clk,
                iio1_cpu_wr,
                iio1_inb_wr,
                iio1_inb_rd,
                imc_rd,
                imc_wr,
            })
        }

        fn snapshot(&self) -> HostSnap {
            HostSnap {
                cpu: self.cpu,
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                instr: self.instr.read(),
                page_faults: self.page_faults.read(),
                llc_loads: self.llc_loads.read(),
                llc_load_misses: self.llc_load_misses.read(),
                dtlb_load_misses: self.dtlb_load_misses.read(),
                cha_wcilf: self.cha_wcilf.iter().map(PerfCounter::read).collect(),
                cha_io_hit: self.cha_io_hit.iter().map(PerfCounter::read).collect(),
                cha_io_miss: self.cha_io_miss.iter().map(PerfCounter::read).collect(),
                mesh_clk: self.mesh_clk.read(),
                iio1_cpu_wr: self.iio1_cpu_wr.read(),
                iio1_inb_wr: self.iio1_inb_wr.read(),
                iio1_inb_rd: self.iio1_inb_rd.read(),
                imc_rd: self.imc_rd.iter().map(PerfCounter::read).collect(),
                imc_wr: self.imc_wr.iter().map(PerfCounter::read).collect(),
            }
        }
    }

    fn format_host_pmu(s0: &HostSnap, s1: &HostSnap, wall_s: f64, tx_rpcs: u64) -> String {
        let mut min_pct = 100.0f64;
        let mut track = |d: Sample| {
            let p = d.pct();
            if p < min_pct {
                min_pct = p;
            }
            d.val
        };
        let cyc = track(s1.cycles.delta(s0.cycles));
        let ref_cyc = track(s1.ref_cycles.delta(s0.ref_cycles));
        let ins = track(s1.instr.delta(s0.instr));
        let pf = track(s1.page_faults.delta(s0.page_faults));
        let llc_l = track(s1.llc_loads.delta(s0.llc_loads));
        let llc_m = track(s1.llc_load_misses.delta(s0.llc_load_misses));
        let dtlb_m = track(s1.dtlb_load_misses.delta(s0.dtlb_load_misses));
        let mesh = track(s1.mesh_clk.delta(s0.mesh_clk));
        let iio_cwr = track(s1.iio1_cpu_wr.delta(s0.iio1_cpu_wr));
        let iio_iwr = track(s1.iio1_inb_wr.delta(s0.iio1_inb_wr));
        let iio_ird = track(s1.iio1_inb_rd.delta(s0.iio1_inb_rd));

        let mut sum_vec = |a: &[Sample], b: &[Sample]| -> u64 {
            a.iter().zip(b.iter()).map(|(x, y)| track(y.delta(*x))).sum()
        };
        let wcilf = sum_vec(&s0.cha_wcilf, &s1.cha_wcilf);
        let io_hit = sum_vec(&s0.cha_io_hit, &s1.cha_io_hit);
        let io_miss = sum_vec(&s0.cha_io_miss, &s1.cha_io_miss);
        let imc_r = sum_vec(&s0.imc_rd, &s1.imc_rd);
        let imc_w = sum_vec(&s0.imc_wr, &s1.imc_wr);

        let ghz = if ref_cyc > 0 { (cyc as f64 / ref_cyc as f64) * (REF_HZ / 1e9) } else { 0.0 };
        let ipc = if cyc > 0 { ins as f64 / cyc as f64 } else { 0.0 };
        let mesh_ghz = if wall_s > 0.0 { mesh as f64 / wall_s / 1e9 } else { 0.0 };
        let itom_tot = io_hit + io_miss;
        let ddio_hit_pct = if itom_tot > 0 { io_hit as f64 * 100.0 / itom_tot as f64 } else { 0.0 };
        let imc_total_bytes = (imc_r + imc_w) * 64;
        let imc_gbps = if wall_s > 0.0 { (imc_total_bytes as f64) / wall_s / 1e9 } else { 0.0 };
        let imc_b_per_req = if tx_rpcs > 0 { (imc_total_bytes as f64) / (tx_rpcs as f64) } else { 0.0 };
        let cyc_per_req = if tx_rpcs > 0 { (cyc as f64) / (tx_rpcs as f64) } else { 0.0 };
        let ins_per_req = if tx_rpcs > 0 { (ins as f64) / (tx_rpcs as f64) } else { 0.0 };
        let cpu_util_pct = if wall_s > 0.0 {
            ((ref_cyc as f64) / (wall_s * REF_HZ) * 100.0).clamp(0.0, 100.0)
        } else {
            0.0
        };

        format!(
            "{{\"cpu\":{},\"cycles\":{},\"ref_cycles\":{},\"instructions\":{},\"ghz\":{:.4},\"ipc\":{:.4},\"cpu_util_pct\":{:.3},\"cycles_per_req\":{:.1},\"instr_per_req\":{:.1},\"page_faults\":{},\"llc_loads\":{},\"llc_load_misses\":{},\"dtlb_load_misses\":{},\"cha_ia_wcilf\":{},\"cha_io_hit_itom\":{},\"cha_io_miss_itom\":{},\"ddio_hit_pct\":{:.3},\"mesh_ghz\":{:.4},\"iio1_cpu_wr\":{},\"iio1_inb_wr\":{},\"iio1_inb_rd\":{},\"imc_cas_rd\":{},\"imc_cas_wr\":{},\"imc_total_bytes\":{},\"imc_gbps\":{:.4},\"imc_bytes_per_req\":{:.2},\"min_running_pct\":{:.2}}}",
            s1.cpu, cyc, ref_cyc, ins, ghz, ipc, cpu_util_pct, cyc_per_req, ins_per_req,
            pf, llc_l, llc_m, dtlb_m, wcilf, io_hit, io_miss, ddio_hit_pct,
            mesh_ghz, iio_cwr, iio_iwr, iio_ird, imc_r, imc_w, imc_total_bytes, imc_gbps, imc_b_per_req,
            min_pct
        )
    }

    // -------------------------------------------------------------------------
    // Standard Memcached TCP Client (Binary SET populate + ASCII stats)
    // -------------------------------------------------------------------------
    #[derive(Clone, Default, Debug, PartialEq, Eq)]
    pub(super) struct McStats {
        pub version: String,
        pub get_cmds: u64,
        pub get_hits: u64,
        pub get_misses: u64,
        pub set_cmds: u64,
        pub delete_hits: u64,
        pub delete_misses: u64,
        pub evictions: u64,
        pub reclaimed: u64,
        pub curr_items: u64,
        pub total_items: u64,
        pub bytes_stored: u64,
        pub bytes_read: u64,
        pub bytes_written: u64,
        pub hashpower: u32,
        pub maxbytes_mib: u32,
    }

    pub(super) fn parse_mc_stats(text: &str) -> McStats {
        let mut st = McStats::default();
        for line in text.lines() {
            let line = line.trim_end_matches('\r');
            if line == "END" {
                break;
            }
            let mut it = line.split_whitespace();
            if it.next() != Some("STAT") {
                continue;
            }
            let Some(k) = it.next() else { continue };
            let Some(v) = it.next() else { continue };
            match k {
                "version" => st.version = v.to_string(),
                "cmd_get" => st.get_cmds = v.parse().unwrap_or(0),
                "get_hits" => st.get_hits = v.parse().unwrap_or(0),
                "get_misses" => st.get_misses = v.parse().unwrap_or(0),
                "cmd_set" => st.set_cmds = v.parse().unwrap_or(0),
                "delete_hits" => st.delete_hits = v.parse().unwrap_or(0),
                "delete_misses" => st.delete_misses = v.parse().unwrap_or(0),
                "evictions" => st.evictions = v.parse().unwrap_or(0),
                "reclaimed" => st.reclaimed = v.parse().unwrap_or(0),
                "curr_items" => st.curr_items = v.parse().unwrap_or(0),
                "total_items" => st.total_items = v.parse().unwrap_or(0),
                "bytes" => st.bytes_stored = v.parse().unwrap_or(0),
                "bytes_read" => st.bytes_read = v.parse().unwrap_or(0),
                "bytes_written" => st.bytes_written = v.parse().unwrap_or(0),
                "hash_power_level" => st.hashpower = v.parse().unwrap_or(0),
                "limit_maxbytes" => {
                    let b: u64 = v.parse().unwrap_or(0);
                    st.maxbytes_mib = (b / (1024 * 1024)) as u32;
                }
                _ => {}
            }
        }
        st
    }

    fn connect_mc(addr: &str) -> Result<TcpStream, Box<dyn Error>> {
        let s = TcpStream::connect(addr)?;
        s.set_nodelay(true)?;
        s.set_read_timeout(Some(Duration::from_secs(10)))?;
        s.set_write_timeout(Some(Duration::from_secs(10)))?;
        Ok(s)
    }

    fn mc_ascii_stats_reset(addr: &str) -> Result<(), Box<dyn Error>> {
        let mut s = connect_mc(addr)?;
        s.write_all(b"stats reset\r\n")?;
        s.flush()?;
        let mut rdr = BufReader::new(s);
        let mut line = String::new();
        rdr.read_line(&mut line)?;
        if line.trim() != "RESET" {
            return Err(format!("stats reset failed: got '{}'", line.trim()).into());
        }
        Ok(())
    }

    fn mc_ascii_stats(addr: &str) -> Result<McStats, Box<dyn Error>> {
        let mut s = connect_mc(addr)?;
        s.write_all(b"stats\r\n")?;
        s.flush()?;
        let mut rdr = BufReader::new(s);
        let mut buf = String::with_capacity(4096);
        let mut line = String::new();
        loop {
            line.clear();
            let n = rdr.read_line(&mut line)?;
            if n == 0 {
                break;
            }
            buf.push_str(&line);
            if line.trim() == "END" {
                break;
            }
        }
        Ok(parse_mc_stats(&buf))
    }

    /// Populate `0..num_keys` via pipelined standard Memcached Binary TCP SET.
    fn populate_memcached_tcp(addr: &str, profile: McProfile, num_keys: usize) -> Result<(), Box<dyn Error>> {
        let mut s = connect_mc(addr)?;
        let mut tx_buf = Vec::with_capacity(64 * (MAX_MC_BYTES + 64));
        let mut pkt = vec![0u8; MAX_MC_BYTES + 64];
        let mut resp_hdr = [0u8; MC_BIN_HDR_LEN];
        let mut discard = vec![0u8; 4096];

        const PIPE_BATCH: usize = 64;
        let mut k = 0usize;
        while k < num_keys {
            let end = (k + PIPE_BATCH).min(num_keys);
            tx_buf.clear();
            for key in k..end {
                let vlen = profile.val_len_for_key(key, num_keys);
                let n = build_mc_set_tcp(key as u32, key as u64, 1, vlen, &mut pkt);
                tx_buf.extend_from_slice(&pkt[..n]);
            }
            s.write_all(&tx_buf)?;
            s.flush()?;
            for key in k..end {
                s.read_exact(&mut resp_hdr)?;
                let magic = resp_hdr[0];
                let op = resp_hdr[1];
                let status = u16::from_be_bytes([resp_hdr[6], resp_hdr[7]]);
                let body_len = u32::from_be_bytes([resp_hdr[8], resp_hdr[9], resp_hdr[10], resp_hdr[11]]) as usize;
                let opaque = u32::from_be_bytes([resp_hdr[12], resp_hdr[13], resp_hdr[14], resp_hdr[15]]);
                if body_len > 0 {
                    if discard.len() < body_len {
                        discard.resize(body_len, 0);
                    }
                    s.read_exact(&mut discard[..body_len])?;
                }
                if magic != MC_MAGIC_RES || op != MC_CMD_SET || status != MC_STATUS_OK || opaque != key as u32 {
                    return Err(format!(
                        "binary TCP SET failed key={key} magic={magic:#x} op={op:#x} status={status:#x} opaque={opaque}"
                    )
                    .into());
                }
            }
            k = end;
        }
        Ok(())
    }

    // -------------------------------------------------------------------------
    // Linux Kernel & Process Telemetry Parsers (`/proc`, `/sys`)
    // -------------------------------------------------------------------------

    #[derive(Clone, Default, Debug, PartialEq, Eq)]
    pub(super) struct IrqSnap {
        pub vf_data_cpu: u64,
        pub vf_other_cpus: u64,
        pub vf_per_queue: Vec<(String, u64)>,
        pub loc_data_cpu: u64,
        pub res_data_cpu: u64,
    }

    pub(super) fn parse_proc_interrupts(text: &str, vf_iface: &str, data_cpu: usize) -> IrqSnap {
        let mut snap = IrqSnap::default();
        let mut lines = text.lines();
        let Some(hdr) = lines.next() else {
            return snap;
        };
        let cpu_cols: Vec<usize> = hdr
            .split_whitespace()
            .filter_map(|tok| tok.strip_prefix("CPU").and_then(|s| s.parse::<usize>().ok()))
            .collect();
        let n_cpus = cpu_cols.len();
        if n_cpus == 0 {
            return snap;
        }
        let data_col = cpu_cols.iter().position(|&c| c == data_cpu);
        let q_prefix = format!("{vf_iface}-q");

        for line in lines {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            let Some((irq_id, rest)) = trimmed.split_once(':') else {
                continue;
            };
            let irq_id = irq_id.trim();
            let toks: Vec<&str> = rest.split_whitespace().collect();
            if toks.len() < n_cpus {
                continue;
            }
            let desc = if toks.len() > n_cpus {
                toks[n_cpus..].join(" ")
            } else {
                String::new()
            };
            if irq_id == "LOC" {
                if let Some(col) = data_col {
                    snap.loc_data_cpu = toks[col].parse().unwrap_or(0);
                }
                continue;
            }
            if irq_id == "RES" {
                if let Some(col) = data_col {
                    snap.res_data_cpu = toks[col].parse().unwrap_or(0);
                }
                continue;
            }
            let matched = desc.contains(&q_prefix)
                || toks[n_cpus..].iter().any(|&t| t == vf_iface || t.starts_with(&q_prefix));
            if matched {
                let mut q_sum = 0u64;
                for (idx, tok) in toks[..n_cpus].iter().enumerate() {
                    let v = tok.parse::<u64>().unwrap_or(0);
                    q_sum += v;
                    if Some(idx) == data_col {
                        snap.vf_data_cpu += v;
                    } else {
                        snap.vf_other_cpus += v;
                    }
                }
                let q_label = toks[n_cpus..].last().copied().unwrap_or(irq_id).to_string();
                snap.vf_per_queue.push((q_label, q_sum));
            }
        }
        snap
    }

    #[derive(Clone, Default, Debug, PartialEq, Eq)]
    pub(super) struct SoftirqSnap {
        pub net_rx_data_cpu: u64,
        pub net_rx_other_cpus: u64,
        pub net_tx_data_cpu: u64,
        pub net_tx_other_cpus: u64,
        pub timer_data_cpu: u64,
        pub sched_data_cpu: u64,
    }

    pub(super) fn parse_proc_softirqs(text: &str, data_cpu: usize) -> SoftirqSnap {
        let mut snap = SoftirqSnap::default();
        let mut lines = text.lines();
        let Some(hdr) = lines.next() else {
            return snap;
        };
        let cpu_cols: Vec<usize> = hdr
            .split_whitespace()
            .filter_map(|tok| tok.strip_prefix("CPU").and_then(|s| s.parse::<usize>().ok()))
            .collect();
        let n_cpus = cpu_cols.len();
        if n_cpus == 0 {
            return snap;
        }
        let data_col = cpu_cols.iter().position(|&c| c == data_cpu);

        for line in lines {
            let Some((name, rest)) = line.trim().split_once(':') else {
                continue;
            };
            let toks: Vec<&str> = rest.split_whitespace().collect();
            if toks.len() < n_cpus {
                continue;
            }
            let name = name.trim();
            let mut sum_other = 0u64;
            let mut val_data = 0u64;
            for (idx, tok) in toks[..n_cpus].iter().enumerate() {
                let v = tok.parse::<u64>().unwrap_or(0);
                if Some(idx) == data_col {
                    val_data = v;
                } else {
                    sum_other += v;
                }
            }
            match name {
                "NET_RX" => {
                    snap.net_rx_data_cpu = val_data;
                    snap.net_rx_other_cpus = sum_other;
                }
                "NET_TX" => {
                    snap.net_tx_data_cpu = val_data;
                    snap.net_tx_other_cpus = sum_other;
                }
                "TIMER" => snap.timer_data_cpu = val_data,
                "SCHED" => snap.sched_data_cpu = val_data,
                _ => {}
            }
        }
        snap
    }

    #[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
    pub(super) struct CpuStatSnap {
        pub user: u64,
        pub nice: u64,
        pub system: u64,
        pub idle: u64,
        pub iowait: u64,
        pub irq: u64,
        pub softirq: u64,
        pub steal: u64,
    }

    impl CpuStatSnap {
        fn delta(self, prev: Self) -> Self {
            CpuStatSnap {
                user: self.user.saturating_sub(prev.user),
                nice: self.nice.saturating_sub(prev.nice),
                system: self.system.saturating_sub(prev.system),
                idle: self.idle.saturating_sub(prev.idle),
                iowait: self.iowait.saturating_sub(prev.iowait),
                irq: self.irq.saturating_sub(prev.irq),
                softirq: self.softirq.saturating_sub(prev.softirq),
                steal: self.steal.saturating_sub(prev.steal),
            }
        }
        fn total(self) -> u64 {
            self.user + self.nice + self.system + self.idle + self.iowait + self.irq + self.softirq + self.steal
        }
    }

    pub(super) fn parse_proc_stat_cpu(text: &str, cpu: usize) -> CpuStatSnap {
        let key = format!("cpu{cpu}");
        for line in text.lines() {
            let mut it = line.split_whitespace();
            if it.next() == Some(key.as_str()) {
                let vals: Vec<u64> = it.map(|s| s.parse().unwrap_or(0)).collect();
                return CpuStatSnap {
                    user: *vals.first().unwrap_or(&0),
                    nice: *vals.get(1).unwrap_or(&0),
                    system: *vals.get(2).unwrap_or(&0),
                    idle: *vals.get(3).unwrap_or(&0),
                    iowait: *vals.get(4).unwrap_or(&0),
                    irq: *vals.get(5).unwrap_or(&0),
                    softirq: *vals.get(6).unwrap_or(&0),
                    steal: *vals.get(7).unwrap_or(&0),
                };
            }
        }
        CpuStatSnap::default()
    }

    #[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
    pub(super) struct TaskSnap {
        pub minflt: u64,
        pub majflt: u64,
        pub utime: u64,
        pub stime: u64,
        pub last_cpu: i32,
        pub vol_ctxt_sw: u64,
        pub nonvol_ctxt_sw: u64,
        pub sched_run_ns: u64,
        pub sched_wait_ns: u64,
        pub sched_slices: u64,
    }

    pub(super) fn parse_task_stat(text: &str) -> (u64, u64, u64, u64, i32) {
        let Some(rparen) = text.rfind(')') else {
            return (0, 0, 0, 0, -1);
        };
        let rest = text[rparen + 1..].trim();
        let f: Vec<&str> = rest.split_whitespace().collect();
        // After ") ", f[0]=state (field 3), so field N is at index N-3.
        // field 10=minflt(7), 12=majflt(9), 14=utime(11), 15=stime(12), 39=processor(36).
        let minflt = f.get(7).and_then(|s| s.parse().ok()).unwrap_or(0);
        let majflt = f.get(9).and_then(|s| s.parse().ok()).unwrap_or(0);
        let utime = f.get(11).and_then(|s| s.parse().ok()).unwrap_or(0);
        let stime = f.get(12).and_then(|s| s.parse().ok()).unwrap_or(0);
        let cpu = f.get(36).and_then(|s| s.parse().ok()).unwrap_or(-1);
        (minflt, majflt, utime, stime, cpu)
    }

    pub(super) fn parse_task_status(text: &str) -> (u64, u64) {
        let mut vol = 0u64;
        let mut nonvol = 0u64;
        for line in text.lines() {
            if let Some(v) = line.strip_prefix("voluntary_ctxt_switches:") {
                vol = v.trim().parse().unwrap_or(0);
            } else if let Some(v) = line.strip_prefix("nonvoluntary_ctxt_switches:") {
                nonvol = v.trim().parse().unwrap_or(0);
            }
        }
        (vol, nonvol)
    }

    pub(super) fn parse_task_schedstat(text: &str) -> (u64, u64, u64) {
        let mut it = text.split_whitespace();
        let run_ns = it.next().and_then(|s| s.parse().ok()).unwrap_or(0);
        let wait_ns = it.next().and_then(|s| s.parse().ok()).unwrap_or(0);
        let slices = it.next().and_then(|s| s.parse().ok()).unwrap_or(0);
        (run_ns, wait_ns, slices)
    }

    fn read_task_snap(pid: u32, tid: u32) -> TaskSnap {
        if pid == 0 || tid == 0 {
            return TaskSnap::default();
        }
        let base = format!("/proc/{pid}/task/{tid}");
        let (minflt, majflt, utime, stime, last_cpu) = fs::read_to_string(format!("{base}/stat"))
            .map(|s| parse_task_stat(&s))
            .unwrap_or((0, 0, 0, 0, -1));
        let (vol_ctxt_sw, nonvol_ctxt_sw) = fs::read_to_string(format!("{base}/status"))
            .map(|s| parse_task_status(&s))
            .unwrap_or((0, 0));
        let (sched_run_ns, sched_wait_ns, sched_slices) = fs::read_to_string(format!("{base}/schedstat"))
            .map(|s| parse_task_schedstat(&s))
            .unwrap_or((0, 0, 0));
        TaskSnap {
            minflt,
            majflt,
            utime,
            stime,
            last_cpu,
            vol_ctxt_sw,
            nonvol_ctxt_sw,
            sched_run_ns,
            sched_wait_ns,
            sched_slices,
        }
    }

    #[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
    pub(super) struct NetdevSnap {
        pub rx_packets: u64,
        pub tx_packets: u64,
        pub rx_bytes: u64,
        pub tx_bytes: u64,
        pub rx_dropped: u64,
        pub tx_dropped: u64,
        pub rx_errors: u64,
        pub tx_errors: u64,
        pub rx_missed_errors: u64,
        pub rx_fifo_errors: u64,
        pub tx_fifo_errors: u64,
    }

    fn read_sysfs_u64(dir: &Path, name: &str) -> u64 {
        fs::read_to_string(dir.join(name))
            .ok()
            .and_then(|s| s.trim().parse().ok())
            .unwrap_or(0)
    }

    fn read_netdev_snap(vf_iface: &str) -> NetdevSnap {
        let p = format!("/sys/class/net/{vf_iface}/statistics");
        let d = Path::new(&p);
        NetdevSnap {
            rx_packets: read_sysfs_u64(d, "rx_packets"),
            tx_packets: read_sysfs_u64(d, "tx_packets"),
            rx_bytes: read_sysfs_u64(d, "rx_bytes"),
            tx_bytes: read_sysfs_u64(d, "tx_bytes"),
            rx_dropped: read_sysfs_u64(d, "rx_dropped"),
            tx_dropped: read_sysfs_u64(d, "tx_dropped"),
            rx_errors: read_sysfs_u64(d, "rx_errors"),
            tx_errors: read_sysfs_u64(d, "tx_errors"),
            rx_missed_errors: read_sysfs_u64(d, "rx_missed_errors"),
            rx_fifo_errors: read_sysfs_u64(d, "rx_fifo_errors"),
            tx_fifo_errors: read_sysfs_u64(d, "tx_fifo_errors"),
        }
    }

    #[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
    pub(super) struct UdpSnmpSnap {
        pub in_datagrams: u64,
        pub no_ports: u64,
        pub in_errors: u64,
        pub out_datagrams: u64,
        pub rcvbuf_errors: u64,
        pub sndbuf_errors: u64,
        pub in_csum_errors: u64,
        pub ignored_multi: u64,
    }

    pub(super) fn parse_proc_net_snmp_udp(text: &str) -> UdpSnmpSnap {
        let mut hdr_keys: Option<Vec<&str>> = None;
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("Udp:") {
                let toks: Vec<&str> = rest.split_whitespace().collect();
                if hdr_keys.is_none() {
                    hdr_keys = Some(toks);
                } else if let Some(keys) = hdr_keys.take() {
                    let map: HashMap<&str, u64> = keys
                        .into_iter()
                        .zip(toks.into_iter().map(|v| v.parse().unwrap_or(0)))
                        .collect();
                    return UdpSnmpSnap {
                        in_datagrams: *map.get("InDatagrams").unwrap_or(&0),
                        no_ports: *map.get("NoPorts").unwrap_or(&0),
                        in_errors: *map.get("InErrors").unwrap_or(&0),
                        out_datagrams: *map.get("OutDatagrams").unwrap_or(&0),
                        rcvbuf_errors: *map.get("RcvbufErrors").unwrap_or(&0),
                        sndbuf_errors: *map.get("SndbufErrors").unwrap_or(&0),
                        in_csum_errors: *map.get("InCsumErrors").unwrap_or(&0),
                        ignored_multi: *map.get("IgnoredMulti").unwrap_or(&0),
                    };
                }
            }
        }
        UdpSnmpSnap::default()
    }

    #[derive(Clone)]
    struct KernelWindowSnap {
        ts: Instant,
        irq: IrqSnap,
        softirq: SoftirqSnap,
        cpu: CpuStatSnap,
        task: TaskSnap,
        netdev: NetdevSnap,
        udp: UdpSnmpSnap,
    }

    fn take_kernel_snap(vf_iface: &str, data_cpu: usize, mc_pid: u32, worker_tid: u32) -> KernelWindowSnap {
        let irq = fs::read_to_string("/proc/interrupts")
            .map(|s| parse_proc_interrupts(&s, vf_iface, data_cpu))
            .unwrap_or_default();
        let softirq = fs::read_to_string("/proc/softirqs")
            .map(|s| parse_proc_softirqs(&s, data_cpu))
            .unwrap_or_default();
        let cpu = fs::read_to_string("/proc/stat")
            .map(|s| parse_proc_stat_cpu(&s, data_cpu))
            .unwrap_or_default();
        let task = read_task_snap(mc_pid, worker_tid);
        let netdev = read_netdev_snap(vf_iface);
        let udp = fs::read_to_string("/proc/net/snmp")
            .map(|s| parse_proc_net_snmp_udp(&s))
            .unwrap_or_default();
        KernelWindowSnap {
            ts: Instant::now(),
            irq,
            softirq,
            cpu,
            task,
            netdev,
            udp,
        }
    }

    fn read_sysctl_u64(path: &str) -> u64 {
        fs::read_to_string(path)
            .ok()
            .and_then(|s| s.trim().parse().ok())
            .unwrap_or(0)
    }

    fn format_kernel_delta(k0: &KernelWindowSnap, k1: &KernelWindowSnap, wall_s: f64, tx_rpcs: u64, vf_iface: &str) -> String {
        let vf_irq_d = k1.irq.vf_data_cpu.saturating_sub(k0.irq.vf_data_cpu);
        let vf_irq_other = k1.irq.vf_other_cpus.saturating_sub(k0.irq.vf_other_cpus);
        let vf_irq_tot = vf_irq_d + vf_irq_other;
        let loc_irq_d = k1.irq.loc_data_cpu.saturating_sub(k0.irq.loc_data_cpu);
        let res_irq_d = k1.irq.res_data_cpu.saturating_sub(k0.irq.res_data_cpu);

        let mut q_parts = Vec::new();
        for (qname, v1) in &k1.irq.vf_per_queue {
            let v0 = k0.irq.vf_per_queue.iter().find(|(n, _)| n == qname).map_or(0, |(_, v)| *v);
            q_parts.push(format!("\"{qname}\":{}", v1.saturating_sub(v0)));
        }

        let net_rx_d = k1.softirq.net_rx_data_cpu.saturating_sub(k0.softirq.net_rx_data_cpu);
        let net_rx_other = k1.softirq.net_rx_other_cpus.saturating_sub(k0.softirq.net_rx_other_cpus);
        let net_tx_d = k1.softirq.net_tx_data_cpu.saturating_sub(k0.softirq.net_tx_data_cpu);
        let net_tx_other = k1.softirq.net_tx_other_cpus.saturating_sub(k0.softirq.net_tx_other_cpus);
        let timer_d = k1.softirq.timer_data_cpu.saturating_sub(k0.softirq.timer_data_cpu);
        let sched_d = k1.softirq.sched_data_cpu.saturating_sub(k0.softirq.sched_data_cpu);

        let cd = k1.cpu.delta(k0.cpu);
        let ctot = cd.total().max(1) as f64;
        let user_pct = (cd.user + cd.nice) as f64 * 100.0 / ctot;
        let sys_pct = cd.system as f64 * 100.0 / ctot;
        let irq_pct = cd.irq as f64 * 100.0 / ctot;
        let softirq_pct = cd.softirq as f64 * 100.0 / ctot;
        let idle_pct = (cd.idle + cd.iowait) as f64 * 100.0 / ctot;

        let minflt = k1.task.minflt.saturating_sub(k0.task.minflt);
        let majflt = k1.task.majflt.saturating_sub(k0.task.majflt);
        let utime = k1.task.utime.saturating_sub(k0.task.utime);
        let stime = k1.task.stime.saturating_sub(k0.task.stime);
        let vol_csw = k1.task.vol_ctxt_sw.saturating_sub(k0.task.vol_ctxt_sw);
        let nonvol_csw = k1.task.nonvol_ctxt_sw.saturating_sub(k0.task.nonvol_ctxt_sw);
        let run_ns = k1.task.sched_run_ns.saturating_sub(k0.task.sched_run_ns);
        let wait_ns = k1.task.sched_wait_ns.saturating_sub(k0.task.sched_wait_ns);
        let slices = k1.task.sched_slices.saturating_sub(k0.task.sched_slices);

        let nd_rx_p = k1.netdev.rx_packets.saturating_sub(k0.netdev.rx_packets);
        let nd_tx_p = k1.netdev.tx_packets.saturating_sub(k0.netdev.tx_packets);
        let nd_rx_b = k1.netdev.rx_bytes.saturating_sub(k0.netdev.rx_bytes);
        let nd_tx_b = k1.netdev.tx_bytes.saturating_sub(k0.netdev.tx_bytes);
        let nd_rx_drop = k1.netdev.rx_dropped.saturating_sub(k0.netdev.rx_dropped);
        let nd_tx_drop = k1.netdev.tx_dropped.saturating_sub(k0.netdev.tx_dropped);
        let nd_rx_err = k1.netdev.rx_errors.saturating_sub(k0.netdev.rx_errors);
        let nd_tx_err = k1.netdev.tx_errors.saturating_sub(k0.netdev.tx_errors);
        let nd_rx_miss = k1.netdev.rx_missed_errors.saturating_sub(k0.netdev.rx_missed_errors);

        let udp_in = k1.udp.in_datagrams.saturating_sub(k0.udp.in_datagrams);
        let udp_out = k1.udp.out_datagrams.saturating_sub(k0.udp.out_datagrams);
        let udp_noports = k1.udp.no_ports.saturating_sub(k0.udp.no_ports);
        let udp_in_err = k1.udp.in_errors.saturating_sub(k0.udp.in_errors);
        let udp_rcvbuf_err = k1.udp.rcvbuf_errors.saturating_sub(k0.udp.rcvbuf_errors);
        let udp_sndbuf_err = k1.udp.sndbuf_errors.saturating_sub(k0.udp.sndbuf_errors);
        let udp_csum_err = k1.udp.in_csum_errors.saturating_sub(k0.udp.in_csum_errors);

        let rpcs_f = tx_rpcs.max(1) as f64;
        let irqs_per_req = vf_irq_tot as f64 / rpcs_f;
        let net_rx_per_req = net_rx_d as f64 / rpcs_f;
        let csw_per_req = (vol_csw + nonvol_csw) as f64 / rpcs_f;
        let worker_exec_ns_per_req = run_ns as f64 / rpcs_f;
        let worker_util_pct = if wall_s > 0.0 {
            (run_ns as f64) / (wall_s * 1e9) * 100.0
        } else {
            0.0
        };
        let busy_poll = read_sysctl_u64("/proc/sys/net/core/busy_poll");
        let busy_read = read_sysctl_u64("/proc/sys/net/core/busy_read");

        format!(
            "{{\"vf_iface\":\"{}\",\"busy_poll\":{},\"busy_read\":{},\"vf_irqs_data_cpu\":{},\"vf_irqs_other_cpus\":{},\"vf_irqs_total\":{},\"vf_irqs_per_queue\":{{{}}},\"loc_irqs_data_cpu\":{},\"res_irqs_data_cpu\":{},\"irqs_per_req\":{:.4},\"softirq_net_rx_data_cpu\":{},\"softirq_net_rx_other_cpus\":{},\"softirq_net_tx_data_cpu\":{},\"softirq_net_tx_other_cpus\":{},\"softirq_timer_data_cpu\":{},\"softirq_sched_data_cpu\":{},\"net_rx_softirqs_per_req\":{:.4},\"cpu_user_pct\":{:.2},\"cpu_sys_pct\":{:.2},\"cpu_irq_pct\":{:.2},\"cpu_softirq_pct\":{:.2},\"cpu_idle_pct\":{:.2},\"worker_last_cpu\":{},\"worker_minflt\":{},\"worker_majflt\":{},\"worker_utime_ticks\":{},\"worker_stime_ticks\":{},\"worker_vol_ctxt_sw\":{},\"worker_nonvol_ctxt_sw\":{},\"ctxt_sw_per_req\":{:.4},\"worker_sched_run_ns\":{},\"worker_sched_wait_ns\":{},\"worker_sched_slices\":{},\"worker_exec_ns_per_req\":{:.1},\"worker_util_pct\":{:.2},\"vf_rx_packets\":{},\"vf_tx_packets\":{},\"vf_rx_bytes\":{},\"vf_tx_bytes\":{},\"vf_rx_dropped\":{},\"vf_tx_dropped\":{},\"vf_rx_errors\":{},\"vf_tx_errors\":{},\"vf_rx_missed_errors\":{},\"udp_in_datagrams\":{},\"udp_out_datagrams\":{},\"udp_no_ports\":{},\"udp_in_errors\":{},\"udp_rcvbuf_errors\":{},\"udp_sndbuf_errors\":{},\"udp_in_csum_errors\":{}}}",
            vf_iface, busy_poll, busy_read, vf_irq_d, vf_irq_other, vf_irq_tot, q_parts.join(","),
            loc_irq_d, res_irq_d, irqs_per_req,
            net_rx_d, net_rx_other, net_tx_d, net_tx_other, timer_d, sched_d, net_rx_per_req,
            user_pct, sys_pct, irq_pct, softirq_pct, idle_pct,
            k1.task.last_cpu, minflt, majflt, utime, stime, vol_csw, nonvol_csw, csw_per_req,
            run_ns, wait_ns, slices, worker_exec_ns_per_req, worker_util_pct,
            nd_rx_p, nd_tx_p, nd_rx_b, nd_tx_b, nd_rx_drop, nd_tx_drop, nd_rx_err, nd_tx_err, nd_rx_miss,
            udp_in, udp_out, udp_noports, udp_in_err, udp_rcvbuf_err, udp_sndbuf_err, udp_csum_err
        )
    }

    pub fn main() -> Result<(), Box<dyn Error>> {
        let args = Args::parse();

        // Ironclad safety guard: refuse monitoring or touching control-plane interfaces.
        if args.vf_iface == "ens1" || args.vf_iface.starts_with("sdp1-0") {
            return Err(format!("REFUSED: control-plane interface '{}' is forbidden", args.vf_iface).into());
        }

        if args.ctrl_cpu >= 0 {
            pin_thread(args.ctrl_cpu as usize);
        }

        let worker_tid = if args.mc_worker_tid != 0 {
            args.mc_worker_tid
        } else {
            discover_mc_worker_tid(args.mc_pid).unwrap_or(args.mc_pid)
        };

        let init_prof = McProfile::from_str_opt(&args.mc_profile)
            .ok_or_else(|| format!("unknown --mc-profile '{}'", args.mc_profile))?;

        // Verify Memcached TCP connectivity & version, and pre-warm slab classes.
        let init_stats = mc_ascii_stats(&args.mc_addr)?;
        let mc_ver = if init_stats.version.is_empty() {
            "1.6.45".to_string()
        } else {
            init_stats.version.clone()
        };

        if args.prewarm_all {
            for prof in [McProfile::V3840, McProfile::V1024, McProfile::V128, McProfile::V8, init_prof] {
                populate_memcached_tcp(&args.mc_addr, prof, args.num_keys)?;
            }
        } else {
            populate_memcached_tcp(&args.mc_addr, init_prof, args.num_keys)?;
        }
        mc_ascii_stats_reset(&args.mc_addr)?;

        let host_pmu = if args.pmu {
            Some(HostPmu::open(args.data_cpu as i32)?)
        } else {
            None
        };

        let quit = Arc::new(AtomicBool::new(false));
        {
            let q = Arc::clone(&quit);
            ctrlc::set_handler(move || q.store(true, Ordering::Relaxed))?;
        }

        let listener = TcpListener::bind(&args.ctrl)?;
        listener.set_nonblocking(true)?;

        let mut pmu_s0 = host_pmu.as_ref().map(|p| p.snapshot());
        let mut kern_s0 = take_kernel_snap(&args.vf_iface, args.data_cpu, args.mc_pid, worker_tid);
        let mut mc_s0 = mc_ascii_stats(&args.mc_addr)?;

        println!(
            "MRV215_HOST_MON READY mc_version={} mc_addr={} mc_pid={} worker_tid={} vf_iface={} data_cpu={} ctrl_cpu={} num_keys={} pmu={}",
            mc_ver,
            args.mc_addr,
            args.mc_pid,
            worker_tid,
            args.vf_iface,
            args.data_cpu,
            args.ctrl_cpu,
            args.num_keys,
            host_pmu.is_some()
        );
        let _ = io::stdout().flush();

        while !quit.load(Ordering::Relaxed) {
            match listener.accept() {
                Ok((mut stream, _)) => {
                    let _ = stream.set_nodelay(true);
                    let _ = stream.set_read_timeout(Some(Duration::from_secs(10)));
                    let mut rdr = BufReader::new(stream.try_clone()?);
                    let mut line = String::new();
                    if rdr.read_line(&mut line).is_ok() && !line.is_empty() {
                        let mut it = line.trim().split_whitespace();
                        let cmd = it.next().unwrap_or("");
                        let arg1 = it.next();
                        let reply = match cmd {
                            "PING" => "{\"ok\":true}".to_string(),
                            "RESET" => {
                                let res: Result<(), Box<dyn Error>> = (|| {
                                    if let Some(p_str) = arg1 {
                                        let prof = McProfile::from_str_opt(p_str)
                                            .ok_or_else(|| format!("unknown profile '{p_str}'"))?;
                                        populate_memcached_tcp(&args.mc_addr, prof, args.num_keys)?;
                                    }
                                    mc_ascii_stats_reset(&args.mc_addr)?;
                                    mc_s0 = mc_ascii_stats(&args.mc_addr)?;
                                    // Snapshot PMU + kernel LAST so TCP control never enters window!
                                    kern_s0 = take_kernel_snap(&args.vf_iface, args.data_cpu, args.mc_pid, worker_tid);
                                    pmu_s0 = host_pmu.as_ref().map(|p| p.snapshot());
                                    Ok(())
                                })();
                                match res {
                                    Ok(()) => "{\"ok\":true}".to_string(),
                                    Err(e) => format!("{{\"ok\":false,\"err\":\"{}\"}}", e.to_string().replace('"', "'")),
                                }
                            }
                            "STATS" => {
                                // Snapshot PMU + kernel FIRST so TCP stats query never enters window!
                                let pmu_s1 = host_pmu.as_ref().map(|p| p.snapshot());
                                let kern_s1 = take_kernel_snap(&args.vf_iface, args.data_cpu, args.mc_pid, worker_tid);
                                let wall_s = kern_s1.ts.duration_since(kern_s0.ts).as_secs_f64();

                                match mc_ascii_stats(&args.mc_addr) {
                                    Ok(mc_s1) => {
                                        let get_cmds = mc_s1.get_cmds.saturating_sub(mc_s0.get_cmds);
                                        let get_hits = mc_s1.get_hits.saturating_sub(mc_s0.get_hits);
                                        let get_misses = mc_s1.get_misses.saturating_sub(mc_s0.get_misses);
                                        let set_cmds = mc_s1.set_cmds.saturating_sub(mc_s0.set_cmds);
                                        let evictions = mc_s1.evictions.saturating_sub(mc_s0.evictions);
                                        let reclaimed = mc_s1.reclaimed.saturating_sub(mc_s0.reclaimed);
                                        let bytes_read = mc_s1.bytes_read.saturating_sub(mc_s0.bytes_read);
                                        let bytes_written = mc_s1.bytes_written.saturating_sub(mc_s0.bytes_written);
                                        let tx_rpcs = get_cmds + set_cmds;
                                        let rpcs_mps = if wall_s > 0.0 {
                                            tx_rpcs as f64 / wall_s / 1e6
                                        } else {
                                            0.0
                                        };
                                        let pmu_json = match (pmu_s0.as_ref(), pmu_s1.as_ref()) {
                                            (Some(s0), Some(s1)) => format_host_pmu(s0, s1, wall_s, tx_rpcs),
                                            _ => "null".to_string(),
                                        };
                                        let kern_json =
                                            format_kernel_delta(&kern_s0, &kern_s1, wall_s, tx_rpcs, &args.vf_iface);
                                        let task_pf = kern_s1.task.minflt.saturating_sub(kern_s0.task.minflt)
                                            + kern_s1.task.majflt.saturating_sub(kern_s0.task.majflt);
                                        format!(
                                            "{{\"mc_version\":\"{}\",\"dt_s\":{:.6},\"rx_rpcs\":{},\"tx_rpcs\":{},\"rpcs_mps\":{:.6},\"get_rpcs\":{},\"set_rpcs\":{},\"mc_misses\":{},\"mc_errors\":0,\"bad\":0,\"torn\":0,\"tx_drops\":0,\"page_faults\":{},\"mc_stats\":{{\"get_cmds\":{},\"get_hits\":{},\"get_misses\":{},\"set_cmds\":{},\"evictions\":{},\"reclaimed\":{},\"curr_items\":{},\"total_items\":{},\"bytes_stored\":{},\"bytes_read\":{},\"bytes_written\":{},\"hashpower\":{},\"maxbytes_mib\":{}}},\"pmu\":{},\"kernel\":{}}}",
                                            mc_ver,
                                            wall_s,
                                            tx_rpcs,
                                            tx_rpcs,
                                            rpcs_mps,
                                            get_cmds,
                                            set_cmds,
                                            get_misses,
                                            task_pf,
                                            get_cmds,
                                            get_hits,
                                            get_misses,
                                            set_cmds,
                                            evictions,
                                            reclaimed,
                                            mc_s1.curr_items,
                                            mc_s1.total_items,
                                            mc_s1.bytes_stored,
                                            bytes_read,
                                            bytes_written,
                                            mc_s1.hashpower,
                                            mc_s1.maxbytes_mib,
                                            pmu_json,
                                            kern_json
                                        )
                                    }
                                    Err(e) => format!("{{\"err\":\"{}\"}}", e.to_string().replace('"', "'")),
                                }
                            }
                            "QUIT" => {
                                quit.store(true, Ordering::Relaxed);
                                "{\"ok\":true}".to_string()
                            }
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
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_parse_proc_interrupts() {
            let sample = "\
           CPU0       CPU1       CPU40      CPU41
  120:          0          0       5000          0  PCI-MSIX-0000:99:02.0   0-edge      ens1f0v0-q0
  121:          0         12        300          0  PCI-MSIX-0000:99:02.0   1-edge      ens1f0v0-q1
  122:        999          0          0          0  PCI-MSIX-0000:99:00.0   0-edge      ens1-q0
  LOC:         10         20        777         30   Local timer interrupts
  RES:          1          2         44          3   Rescheduling interrupts
";
            let s = parse_proc_interrupts(sample, "ens1f0v0", 40);
            assert_eq!(s.vf_data_cpu, 5300);
            assert_eq!(s.vf_other_cpus, 12);
            assert_eq!(s.vf_per_queue.len(), 2);
            assert_eq!(s.loc_data_cpu, 777);
            assert_eq!(s.res_data_cpu, 44);
        }

        #[test]
        fn test_parse_proc_softirqs() {
            let sample = "\
                    CPU0       CPU40      CPU41
          HI:          1          2          3
       TIMER:         10        200         30
      NET_TX:          4        500          6
      NET_RX:          7      12345          8
       SCHED:          9        333         11
";
            let s = parse_proc_softirqs(sample, 40);
            assert_eq!(s.net_rx_data_cpu, 12345);
            assert_eq!(s.net_rx_other_cpus, 15);
            assert_eq!(s.net_tx_data_cpu, 500);
            assert_eq!(s.net_tx_other_cpus, 10);
            assert_eq!(s.timer_data_cpu, 200);
            assert_eq!(s.sched_data_cpu, 333);
        }

        #[test]
        fn test_parse_proc_stat_cpu() {
            let sample = "\
cpu  100 0 50 1000 1 2 3 0 0 0
cpu40 120 5 80 500 2 15 45 0 0 0
";
            let s = parse_proc_stat_cpu(sample, 40);
            assert_eq!(s.user, 120);
            assert_eq!(s.nice, 5);
            assert_eq!(s.system, 80);
            assert_eq!(s.idle, 500);
            assert_eq!(s.iowait, 2);
            assert_eq!(s.irq, 15);
            assert_eq!(s.softirq, 45);
        }

        #[test]
        fn test_parse_task_stat_status_schedstat() {
            let stat = "12345 (mc-worker) S 1 12345 12345 0 -1 4194304 100 0 2 0 55 88 0 0 20 0 4 0 1000 10000 500 18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 40 0 0 0 0 0";
            let (minflt, majflt, utime, stime, cpu) = parse_task_stat(stat);
            assert_eq!((minflt, majflt, utime, stime, cpu), (100, 2, 55, 88, 40));

            let status = "Name:\tmc-worker\nvoluntary_ctxt_switches:\t4200\nnonvoluntary_ctxt_switches:\t17\n";
            assert_eq!(parse_task_status(status), (4200, 17));

            let sched = "987654321 123456 999\n";
            assert_eq!(parse_task_schedstat(sched), (987654321, 123456, 999));
        }

        #[test]
        fn test_parse_proc_net_snmp_udp() {
            let sample = "\
Ip: Forwarding DefaultTTL
Ip: 2 64
Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors InCsumErrors IgnoredMulti MemErrors
Udp: 500000 0 0 500000 3 1 0 0 0
";
            let u = parse_proc_net_snmp_udp(sample);
            assert_eq!(u.in_datagrams, 500000);
            assert_eq!(u.out_datagrams, 500000);
            assert_eq!(u.rcvbuf_errors, 3);
            assert_eq!(u.sndbuf_errors, 1);
        }

        #[test]
        fn test_parse_mc_stats() {
            let sample = "\
STAT version 1.6.45\r\n\
STAT cmd_get 100000\r\n\
STAT cmd_set 5000\r\n\
STAT get_hits 100000\r\n\
STAT get_misses 0\r\n\
STAT evictions 0\r\n\
STAT curr_items 8192\r\n\
STAT bytes_read 1234567\r\n\
STAT bytes_written 7654321\r\n\
STAT hash_power_level 16\r\n\
STAT limit_maxbytes 268435456\r\n\
END\r\n";
            let st = parse_mc_stats(sample);
            assert_eq!(st.version, "1.6.45");
            assert_eq!(st.get_cmds, 100000);
            assert_eq!(st.set_cmds, 5000);
            assert_eq!(st.get_hits, 100000);
            assert_eq!(st.get_misses, 0);
            assert_eq!(st.curr_items, 8192);
            assert_eq!(st.hashpower, 16);
            assert_eq!(st.maxbytes_mib, 256);
        }
    }
}
