//! Host server on `saturn` Socket 1 for MRV-209 (MRV-203 E5).
//!
//! Runs 100% standard DPDK `octeon_ep` `SDP` data plane (`0000:99:02.0`) on `cpu40`
//! bearing either:
//!   - `--app memcached`: 100% unmodified upstream `Memcached 1.6.45` (`mrv209_mc_adapter.c`,
//!                        zero upstream lines modified, mlocked NUMA-1 slabs, 0 page faults)
//!   - `--app nfv-nat` / `--app nfv-lb`: lightweight stateful 5-tuple NAT / Maglev LB engine
//!
//! Simultaneously runs a co-located cache-sensitive tenant (`TenantIndex`, default `32 MiB`
//! pointer-chasing + self-verifying KV index, pre-faulted & mlocked on NUMA 1) on an
//! independent Socket-1 physical core (`--tenant-cpu 41`), measuring tenant `p50/p99/p999`
//! latency (ns), throughput, and per-core LLC misses alongside `cpu40` core PMU and
//! Socket-1 uncore `CHA` (DDIO hit/miss), `IIO`, and `IMC` DDR5 bandwidth!
//!
//! Single-variable policies (all sharing the identical standard `SDP` data plane):
//!   - `large-sdp`      : large host SDP ring + large non-compact mbuf pool (default privRing)
//!   - `small-sdp-drop` : `allow_small_oq=1` small SDP ring + compact DDIO-hot pool (drops on card)
//!   - `ceio-style`     : fast-path + slow-path frames merged via host 2P1C `SwMergeRing` + BAR4 credit
//!   - `credit-sdp`     : `allow_small_oq=1` compact DDIO-hot SDP ring (`nb_desc=512, WMARK=128`,
//!                        `R_eff=128..256`) + per-queue no-SFENCE `MOVDIR64B` credit return to BAR4

#[cfg(not(target_arch = "x86_64"))]
fn main() {
    eprintln!("mrv209_host runs on the x86_64 host");
    std::process::exit(2);
}

#[cfg(target_arch = "x86_64")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    imp::main()
}

#[cfg(target_arch = "x86_64")]
mod imp {
    use std::error::Error;
    use std::ffi::CStr;
    use std::fs;
    use std::io::{self, Read, Write};
    use std::net::TcpListener;
    use std::os::fd::RawFd;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use clap::{Parser, ValueEnum};
    use dpdk::{Burst, Eal, Mbuf, Mempool, Port, rdtsc, rdtsc_after_loads};
    use testbed::lease::Lease;
    use testbed::mmio::{has_movdir64b, movdir64b, sfence};
    use mrv209::{
        CREDIT_FLAG_CEIO, CREDIT_FLAG_MOVDIR64B, CTRL_CARD_ACK_GEN, CTRL_CARD_CORES,
        CTRL_HOST_RUN_GEN, CTRL_POLICY_ID, CTRL_R_EFF, CreditLine, FastHist, HOST_CTRL_ADDR,
        MAX_CARD_CORES, MAX_DRAIN, MAX_MC_BYTES, MC_CMD_GET, MC_CMD_SET, MC_NUM_KEYS,
        MC_STATUS_ENOENT, MC_STATUS_OK, MC_UDP_DATA_SIZE, NFV_MIN_LEN, NfvEngine, NfvMode,
        OP_MC_FAST, OP_MC_SLOW, OP_NFV_FAST, OP_NFV_SLOW, SDP_HDR_LEN, SDP_MBUF_BUF_SIZE_JUMBO,
        SDP_META_LEN, SDP_MTU, SDP_UDP_DST_PORT, SYNC_ADDR, SwMergeRing, TenantIndex,
        build_mc_set_udp, credit_line_offset, max_safe_r_eff, mix_val_len_for_key, pack_flow_meta,
        pack_wire_meta, sdp_payload_total_bytes, small_mix_val_len_for_key, unpack_flow_meta,
        unpack_wire_meta, websearch_val_len_for_key, wire_frag_cnt,
    };
    use testbed::swap_macs;

    const CTRL_CPU: usize = 44;
    const MRV209_MC_MAX_IOVS: usize = 8;
    const MRV209_MC_MAX_UDP_DGRAMS: usize = 4;

    // -------------------------------------------------------------------------
    // FFI to 100% unmodified upstream Memcached 1.6.45 via mrv209_mc_adapter.c
    // -------------------------------------------------------------------------
    #[repr(C)]
    #[derive(Clone, Copy)]
    struct McReply {
        udp_hdrs: [[u8; 8]; MRV209_MC_MAX_UDP_DGRAMS],
        udp_dgram_cnt: u32,
        iov: [libc::iovec; MRV209_MC_MAX_IOVS],
        iovcnt: u32,
        bin_len: u32,
        total_len: u32,
        status: u16,
        opcode: u8,
        _pad: u8,
    }

    impl Default for McReply {
        fn default() -> Self {
            unsafe { std::mem::zeroed() }
        }
    }

    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    struct McStats {
        get_cmds: u64,
        get_hits: u64,
        get_misses: u64,
        set_cmds: u64,
        delete_hits: u64,
        delete_misses: u64,
        evictions: u64,
        reclaimed: u64,
        curr_items: u64,
        total_items: u64,
        bytes_stored: u64,
        bytes_read: u64,
        bytes_written: u64,
        hashpower: u32,
        maxbytes_mib: u32,
    }

    unsafe extern "C" {
        fn mrv209_mc_init(maxbytes: usize, hashpower: u32, maint_cpu: i32) -> i32;
        fn mrv209_mc_exec(udp_req: *const u8, udp_req_len: usize, out: *mut McReply) -> i32;
        fn mrv209_mc_release();
        fn mrv209_mc_tick_clock();
        fn mrv209_mc_get_stats(st: *mut McStats);
        fn mrv209_mc_reset_stats();
        fn mrv209_mc_shutdown();
        fn mrv209_mc_version() -> *const libc::c_char;
    }

    #[inline(always)]
    fn copy_mc_reply_contiguous(reply: &McReply, dst: &mut [u8]) {
        if reply.udp_dgram_cnt <= 1 {
            dst[0..8].copy_from_slice(&reply.udp_hdrs[0]);
            let mut off = 8usize;
            for i in 0..(reply.iovcnt as usize) {
                let len = reply.iov[i].iov_len;
                if len > 0 {
                    let src = unsafe { std::slice::from_raw_parts(reply.iov[i].iov_base.cast::<u8>(), len) };
                    dst[off..off + len].copy_from_slice(src);
                    off += len;
                }
            }
            return;
        }
        let mut dst_off = 0usize;
        let mut dgram = 0usize;
        let mut chunk_rem = 0usize;
        for i in 0..(reply.iovcnt as usize) {
            let len = reply.iov[i].iov_len;
            if len == 0 {
                continue;
            }
            let src = unsafe { std::slice::from_raw_parts(reply.iov[i].iov_base.cast::<u8>(), len) };
            let mut s_off = 0usize;
            while s_off < len {
                if chunk_rem == 0 {
                    dst[dst_off..dst_off + 8].copy_from_slice(&reply.udp_hdrs[dgram]);
                    dst_off += 8;
                    dgram += 1;
                    chunk_rem = MC_UDP_DATA_SIZE;
                }
                let take = chunk_rem.min(len - s_off);
                dst[dst_off..dst_off + take].copy_from_slice(&src[s_off..s_off + take]);
                dst_off += take;
                s_off += take;
                chunk_rem -= take;
            }
        }
    }

    #[inline(never)]
    fn prefault_stack() {
        let mut buf = [0u8; 64 * 1024];
        for i in (0..buf.len()).step_by(4096) {
            unsafe { std::ptr::write_volatile(buf.as_mut_ptr().add(i), (i & 0xff) as u8) };
        }
        std::hint::black_box(&buf);
    }

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

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum AppKind {
        Memcached,
        NfvNat,
        NfvLb,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
    enum McProfile {
        #[value(name = "8")]
        V8,
        #[value(name = "128")]
        V128,
        #[value(name = "1024")]
        V1024,
        #[value(name = "3840")]
        V3840,
        #[value(name = "small_mix")]
        SmallMix,
        #[value(name = "etc_mix")]
        EtcMix,
        #[value(name = "websearch_mc")]
        WebSearchMc,
    }

    impl McProfile {
        fn from_str_opt(s: &str) -> Option<Self> {
            match s {
                "8" | "get8" | "set8" | "rw8" => Some(McProfile::V8),
                "128" | "get128" | "set128" | "rw128" => Some(McProfile::V128),
                "1024" | "get1024" | "set1024" => Some(McProfile::V1024),
                "3840" | "get3840" | "set3840" => Some(McProfile::V3840),
                "small_mix" | "etc_small" => Some(McProfile::SmallMix),
                "etc_mix" | "mix" | "etc" => Some(McProfile::EtcMix),
                "websearch_mc" | "websearch" => Some(McProfile::WebSearchMc),
                _ => None,
            }
        }

        fn val_len_for_key(self, key: usize, num_keys: usize) -> usize {
            match self {
                McProfile::V8 => 8,
                McProfile::V128 => 128,
                McProfile::V1024 => 1024,
                McProfile::V3840 => 3840,
                McProfile::SmallMix => small_mix_val_len_for_key(key, num_keys),
                McProfile::EtcMix => mix_val_len_for_key(key, num_keys),
                McProfile::WebSearchMc => websearch_val_len_for_key(key, num_keys),
            }
        }
    }

    fn populate_memcached(profile: McProfile, num_keys: usize) -> Result<(), Box<dyn Error>> {
        let mut req = [0u8; MAX_MC_BYTES + 64];
        let mut rep = McReply::default();
        for k in 0..num_keys {
            let vlen = profile.val_len_for_key(k, num_keys);
            let n = build_mc_set_udp((k & 0xffff) as u16, k as u32, k as u64, 1, vlen, &mut req);
            let rc = unsafe { mrv209_mc_exec(req.as_ptr(), n, &mut rep) };
            let st = rep.status;
            unsafe { mrv209_mc_release() };
            if rc != 0 || st != MC_STATUS_OK {
                return Err(format!("populate_memcached key={k} vlen={vlen} failed rc={rc} status={st:#x}").into());
            }
        }
        unsafe { mrv209_mc_reset_stats() };
        Ok(())
    }

    #[derive(Parser)]
    struct Args {
        #[arg(long, value_enum, default_value_t = Policy::CreditSdp)]
        policy: Policy,
        #[arg(long, value_enum, default_value_t = AppKind::Memcached)]
        app: AppKind,
        #[arg(long, value_enum, default_value_t = McProfile::EtcMix)]
        mc_profile: McProfile,
        #[arg(long, default_value_t = MC_NUM_KEYS)]
        num_keys: usize,
        #[arg(long, default_value_t = 256)]
        mc_mem_mb: usize,
        #[arg(long, default_value_t = 16)]
        mc_hashpower: u32,
        #[arg(long, default_value = testbed::lease::DEFAULT_ADDR)]
        card: String,
        #[arg(long, default_value = "/sys/bus/pci/devices/0000:99:00.0/resource4")]
        bar4: String,
        #[arg(long, default_value_t = 4)]
        sdp_queues: u16,
        #[arg(long, default_value_t = 512)]
        sdp_rx_desc: u16,
        #[arg(long, default_value_t = 2048)]
        sdp_tx_desc: u16,
        #[arg(long, default_value_t = SDP_MBUF_BUF_SIZE_JUMBO)]
        sdp_buf_size: u16,
        /// Explicit host SDP mbuf pool size (0 = auto: compact for credit-sdp/small-sdp-drop,
        /// large 32768 for large-sdp).
        #[arg(long, default_value_t = 0)]
        sdp_pool_size: u32,
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        sdp_zero_copy: bool,
        /// Logical credit window `R_eff` per queue advertised to card (`credit-sdp`).
        #[arg(long, default_value_t = 128)]
        r_eff: u64,
        /// Batch size `B` (packets taken per no-SFENCE BAR4 MOVDIR64B credit return).
        #[arg(long, default_value_t = 32)]
        credit_batch: u64,
        /// Enable co-located cache-sensitive tenant on independent Socket-1 physical core.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        tenant: bool,
        /// Independent Socket-1 physical core for co-located tenant (default cpu41, core 9).
        #[arg(long, default_value_t = 41)]
        tenant_cpu: usize,
        /// Working-set size of co-located tenant index in MiB (default 32 MiB inside 60 MiB L3).
        #[arg(long, default_value_t = 32)]
        tenant_ws_mb: usize,
        /// Standalone solo tenant calibration duration in ms (0 = normal server mode).
        #[arg(long, default_value_t = 0)]
        tenant_solo_ms: u64,
        /// Enable 100% non-multiplexed Core + Tenant Core + Socket-1 CHA/IIO/IMC PMU.
        #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
        pmu: bool,
        #[arg(long, default_value = HOST_CTRL_ADDR)]
        ctrl: String,
    }

    #[inline(always)]
    fn prep_sdp_mbuf<'e>(
        mut pkt: Mbuf<'e>,
        flen: usize,
        target_flen: usize,
        zero_copy: bool,
        tx_pool: Option<&Mempool<'e>>,
    ) -> Option<Mbuf<'e>> {
        if zero_copy {
            if target_flen > flen {
                pkt.append((target_flen - flen) as u16)?;
            } else if target_flen < flen {
                pkt.trim((flen - target_flen) as u16).ok()?;
            }
            swap_macs(pkt.data_mut());
            Some(pkt)
        } else {
            let tp = tx_pool?;
            let mut m = tp.alloc()?;
            let dst = m.append(target_flen as u16)?;
            let src = pkt.data();
            dst[0..6].copy_from_slice(&src[6..12]);
            dst[6..12].copy_from_slice(&src[0..6]);
            dst[12..SDP_HDR_LEN].copy_from_slice(&src[12..SDP_HDR_LEN]);
            Some(m)
        }
    }

    // -------------------------------------------------------------------------
    // 100% Non-Multiplexed Host Core + Co-Located Tenant Core + Socket-1 Uncore PMU
    // -------------------------------------------------------------------------
    const SOCKET1_UNCORE_CPU: i32 = 32;
    const REF_HZ: f64 = 2.2e9;

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
            Self {
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
            let c = PerfCounter { fd: fd as RawFd };
            let rc = unsafe { libc::ioctl(c.fd, PERF_EVENT_IOC_ENABLE, 0) };
            if rc != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(c)
        }

        fn read(&self) -> Sample {
            let mut buf = [0u64; 3];
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr().cast::<libc::c_void>(), size_of::<[u64; 3]>()) };
            if n == size_of::<[u64; 3]>() as isize {
                Sample {
                    val: buf[0],
                    enabled_ns: buf[1],
                    running_ns: buf[2],
                }
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

    struct CoreCounters {
        cpu: i32,
        cycles: PerfCounter,
        ref_cycles: PerfCounter,
        instr: PerfCounter,
        page_faults: PerfCounter,
        llc_loads: PerfCounter,
        llc_load_misses: PerfCounter,
        dtlb_load_misses: PerfCounter,
    }

    #[derive(Clone, Copy, Default)]
    struct CoreSnap {
        cpu: i32,
        cycles: Sample,
        ref_cycles: Sample,
        instr: Sample,
        page_faults: Sample,
        llc_loads: Sample,
        llc_load_misses: Sample,
        dtlb_load_misses: Sample,
    }

    impl CoreCounters {
        fn open(cpu: i32) -> Result<Self, Box<dyn Error>> {
            Ok(Self {
                cpu,
                cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, cpu)?,
                ref_cycles: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES, cpu)?,
                instr: PerfCounter::open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, cpu)?,
                page_faults: PerfCounter::open(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_PAGE_FAULTS, cpu)?,
                llc_loads: PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOADS, cpu)?,
                llc_load_misses: PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_LLC_LOAD_MISSES, cpu)?,
                dtlb_load_misses: PerfCounter::open(PERF_TYPE_HW_CACHE, CORE_DTLB_LOAD_MISSES, cpu)?,
            })
        }

        fn snapshot(&self) -> CoreSnap {
            CoreSnap {
                cpu: self.cpu,
                cycles: self.cycles.read(),
                ref_cycles: self.ref_cycles.read(),
                instr: self.instr.read(),
                page_faults: self.page_faults.read(),
                llc_loads: self.llc_loads.read(),
                llc_load_misses: self.llc_load_misses.read(),
                dtlb_load_misses: self.dtlb_load_misses.read(),
            }
        }
    }

    struct HostPmu {
        app_core: CoreCounters,
        tenant_core: Option<CoreCounters>,
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
        app_core: CoreSnap,
        tenant_core: Option<CoreSnap>,
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
        fn open(app_cpu: i32, tenant_cpu: Option<i32>) -> Result<Self, Box<dyn Error>> {
            let app_core = CoreCounters::open(app_cpu)?;
            let tenant_core = match tenant_cpu {
                Some(tc) if tc != app_cpu => Some(CoreCounters::open(tc)?),
                _ => None,
            };

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
            Ok(Self {
                app_core,
                tenant_core,
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
                app_core: self.app_core.snapshot(),
                tenant_core: self.tenant_core.as_ref().map(CoreCounters::snapshot),
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
        let cyc = track(s1.app_core.cycles.delta(s0.app_core.cycles));
        let ref_cyc = track(s1.app_core.ref_cycles.delta(s0.app_core.ref_cycles));
        let ins = track(s1.app_core.instr.delta(s0.app_core.instr));
        let pf = track(s1.app_core.page_faults.delta(s0.app_core.page_faults));
        let llc_l = track(s1.app_core.llc_loads.delta(s0.app_core.llc_loads));
        let llc_m = track(s1.app_core.llc_load_misses.delta(s0.app_core.llc_load_misses));
        let dtlb_m = track(s1.app_core.dtlb_load_misses.delta(s0.app_core.dtlb_load_misses));

        let tenant_pmu_str = match (s0.tenant_core, s1.tenant_core) {
            (Some(t0), Some(t1)) => {
                let t_cyc = track(t1.cycles.delta(t0.cycles));
                let t_ref = track(t1.ref_cycles.delta(t0.ref_cycles));
                let t_ins = track(t1.instr.delta(t0.instr));
                let t_pf = track(t1.page_faults.delta(t0.page_faults));
                let t_llc_l = track(t1.llc_loads.delta(t0.llc_loads));
                let t_llc_m = track(t1.llc_load_misses.delta(t0.llc_load_misses));
                let t_ghz = if t_ref > 0 { (t_cyc as f64 / t_ref as f64) * (REF_HZ / 1e9) } else { 0.0 };
                let t_miss_pct = if t_llc_l > 0 { (t_llc_m as f64 * 100.0) / (t_llc_l as f64) } else { 0.0 };
                format!(
                    "{{\"cpu\":{},\"cycles\":{},\"instructions\":{},\"ghz\":{:.4},\"page_faults\":{},\"llc_loads\":{},\"llc_load_misses\":{},\"llc_miss_pct\":{:.3}}}",
                    t1.cpu, t_cyc, t_ins, t_ghz, t_pf, t_llc_l, t_llc_m, t_miss_pct
                )
            }
            _ => "null".to_string(),
        };

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
        let app_llc_miss_pct = if llc_l > 0 { llc_m as f64 * 100.0 / llc_l as f64 } else { 0.0 };
        let imc_total_bytes = (imc_r + imc_w) * 64;
        let imc_rd_gbps = if wall_s > 0.0 { (imc_r * 64) as f64 / wall_s / 1e9 } else { 0.0 };
        let imc_wr_gbps = if wall_s > 0.0 { (imc_w * 64) as f64 / wall_s / 1e9 } else { 0.0 };
        let imc_gbps = if wall_s > 0.0 { (imc_total_bytes as f64) / wall_s / 1e9 } else { 0.0 };
        let imc_b_per_req = if tx_rpcs > 0 { (imc_total_bytes as f64) / (tx_rpcs as f64) } else { 0.0 };
        let cyc_per_req = if tx_rpcs > 0 { (cyc as f64) / (tx_rpcs as f64) } else { 0.0 };
        let ins_per_req = if tx_rpcs > 0 { (ins as f64) / (tx_rpcs as f64) } else { 0.0 };

        format!(
            "{{\"cpu\":{},\"cycles\":{},\"ref_cycles\":{},\"instructions\":{},\"ghz\":{:.4},\"ipc\":{:.4},\
             \"cycles_per_req\":{:.1},\"instr_per_req\":{:.1},\"page_faults\":{},\"llc_loads\":{},\"llc_load_misses\":{},\"llc_miss_pct\":{:.3},\
             \"dtlb_load_misses\":{},\"tenant_pmu\":{},\
             \"cha_ia_wcilf\":{},\"cha_io_hit_itom\":{},\"cha_io_miss_itom\":{},\"ddio_hit_pct\":{:.3},\
             \"mesh_ghz\":{:.4},\"iio1_cpu_wr\":{},\"iio1_inb_wr\":{},\"iio1_inb_rd\":{},\
             \"imc_cas_rd\":{},\"imc_cas_wr\":{},\"imc_total_bytes\":{},\"imc_rd_gbps\":{:.4},\"imc_wr_gbps\":{:.4},\"imc_gbps\":{:.4},\
             \"imc_bytes_per_req\":{:.2},\"min_running_pct\":{:.2}}}",
            s1.app_core.cpu,
            cyc,
            ref_cyc,
            ins,
            ghz,
            ipc,
            cyc_per_req,
            ins_per_req,
            pf,
            llc_l,
            llc_m,
            app_llc_miss_pct,
            dtlb_m,
            tenant_pmu_str,
            wcilf,
            io_hit,
            io_miss,
            ddio_hit_pct,
            mesh_ghz,
            iio_cwr,
            iio_iwr,
            iio_ird,
            imc_r,
            imc_w,
            imc_total_bytes,
            imc_rd_gbps,
            imc_wr_gbps,
            imc_gbps,
            imc_b_per_req,
            min_pct,
        )
    }

    fn pin_thread(cpu: usize) {
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut set);
            libc::CPU_SET(cpu, &mut set);
            libc::sched_setaffinity(0, size_of::<libc::cpu_set_t>(), &set);
        }
    }

    fn read_minor_faults() -> u64 {
        let mut ru: libc::rusage = unsafe { std::mem::zeroed() };
        unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut ru) };
        (ru.ru_minflt + ru.ru_majflt) as u64
    }

    fn read_numa_maps_huge() -> String {
        let mut n0 = 0u64;
        let mut n1 = 0u64;
        let mut kb = 0u64;
        if let Ok(s) = fs::read_to_string("/proc/self/numa_maps") {
            for line in s.lines() {
                if !line.contains("huge") {
                    continue;
                }
                for tok in line.split_whitespace() {
                    if let Some(v) = tok.strip_prefix("N0=") {
                        n0 += v.parse::<u64>().unwrap_or(0);
                    } else if let Some(v) = tok.strip_prefix("N1=") {
                        n1 += v.parse::<u64>().unwrap_or(0);
                    } else if let Some(v) = tok.strip_prefix("kernelpagesize_kB=") {
                        kb = v.parse::<u64>().unwrap_or(0);
                    }
                }
            }
        }
        format!("page_kb={kb},N0={n0},N1={n1}")
    }

    // -------------------------------------------------------------------------
    // Co-Located Socket-1 Cache-Sensitive Tenant Worker
    // -------------------------------------------------------------------------
    #[derive(Clone, Default)]
    struct TenantStats {
        enabled: bool,
        cpu: usize,
        ws_mb: usize,
        dt_s: f64,
        ops: u64,
        bad: u64,
        p50_ns: f64,
        p95_ns: f64,
        p99_ns: f64,
        p999_ns: f64,
        mean_ns: f64,
    }

    impl TenantStats {
        fn to_json(&self) -> String {
            if !self.enabled {
                return "null".to_string();
            }
            let mops = if self.dt_s > 0.0 { self.ops as f64 / self.dt_s / 1e6 } else { 0.0 };
            format!(
                "{{\"cpu\":{},\"ws_mb\":{},\"dt_s\":{:.6},\"ops\":{},\"mops\":{:.4},\"bad\":{},\
                 \"p50_ns\":{:.2},\"p95_ns\":{:.2},\"p99_ns\":{:.2},\"p999_ns\":{:.2},\"mean_ns\":{:.2}}}",
                self.cpu,
                self.ws_mb,
                self.dt_s,
                self.ops,
                mops,
                self.bad,
                self.p50_ns,
                self.p95_ns,
                self.p99_ns,
                self.p999_ns,
                self.mean_ns,
            )
        }
    }

    struct TenantCoord {
        cmd_word: AtomicU64,
        done_gen: AtomicU64,
        snap: Mutex<TenantStats>,
    }

    impl TenantCoord {
        fn new() -> Self {
            Self {
                cmd_word: AtomicU64::new(0),
                done_gen: AtomicU64::new(0),
                snap: Mutex::new(TenantStats::default()),
            }
        }

        fn call(&self, cmd: u64, quit: &AtomicBool) -> TenantStats {
            let prev = self.cmd_word.load(Ordering::Relaxed) >> 4;
            let next_gen = prev + 1;
            self.cmd_word.store((next_gen << 4) | (cmd & 0xf), Ordering::Release);
            let t0 = Instant::now();
            while self.done_gen.load(Ordering::Acquire) < next_gen {
                if quit.load(Ordering::Relaxed) || t0.elapsed() > Duration::from_secs(3) {
                    return TenantStats::default();
                }
                std::thread::sleep(Duration::from_micros(50));
            }
            self.snap.lock().unwrap().clone()
        }
    }

    fn calibrate_tsc_hz_local() -> f64 {
        let t0 = Instant::now();
        let c0 = rdtsc_after_loads();
        std::thread::sleep(Duration::from_millis(50));
        let c1 = rdtsc_after_loads();
        let dt = t0.elapsed().as_secs_f64();
        (c1.saturating_sub(c0) as f64) / dt
    }

    // -------------------------------------------------------------------------
    // Host Application & Transport Telemetry
    // -------------------------------------------------------------------------
    #[derive(Clone, Default)]
    struct HostStats {
        t_reset_tsc: u64,
        dt_s: f64,
        rx_rpcs: u64,
        tx_rpcs: u64,
        get_rpcs: u64,
        set_rpcs: u64,
        nfv_rpcs: u64,
        mc_misses: u64,
        mc_errors: u64,
        nfv_errors: u64,
        bad: u64,
        torn: u64,
        tx_drops: u64,
        sdp_imissed: u64,
        sdp_ierrors: u64,
        sdp_oerrors: u64,
        sdp_rx_nombuf: u64,
        sdp_pool_in_use: u32,
        sdp_pool_avail: u32,
        bar4_credit_writes: u64,
        ceio_fast_merged: u64,
        ceio_slow_merged: u64,
        empty_polls: u64,
        busy_polls: u64,
        busy_tsc: u64,
        page_faults: u64,
        mc_stats: McStats,
        tenant: TenantStats,
        pmu_json: String,
    }

    impl HostStats {
        fn reset(&mut self, now_tsc: u64) {
            *self = HostStats::default();
            self.t_reset_tsc = now_tsc;
            self.pmu_json = "null".to_string();
        }

        fn to_json(&self, policy: Policy, app: AppKind, tsc_hz: f64, numa: &str, mc_ver: &str) -> String {
            let rpcs_mps = if self.dt_s > 0.0 { self.tx_rpcs as f64 / self.dt_s / 1e6 } else { 0.0 };
            let busy_ns_per_req = if self.tx_rpcs > 0 && tsc_hz > 0.0 {
                (self.busy_tsc as f64 / tsc_hz) * 1e9 / (self.tx_rpcs as f64)
            } else {
                0.0
            };
            let ms = &self.mc_stats;
            format!(
                "{{\"policy\":\"{:?}\",\"app\":\"{:?}\",\"mc_version\":\"{}\",\"dt_s\":{:.6},\
                 \"rx_rpcs\":{},\"tx_rpcs\":{},\"rpcs_mps\":{:.6},\"get_rpcs\":{},\"set_rpcs\":{},\"nfv_rpcs\":{},\
                 \"mc_misses\":{},\"mc_errors\":{},\"nfv_errors\":{},\"bad\":{},\"torn\":{},\"tx_drops\":{},\
                 \"sdp_imissed\":{},\"sdp_ierrors\":{},\"sdp_oerrors\":{},\"sdp_rx_nombuf\":{},\
                 \"sdp_pool_in_use\":{},\"sdp_pool_avail\":{},\
                 \"bar4_credit_writes\":{},\"ceio_fast_merged\":{},\"ceio_slow_merged\":{},\
                 \"empty_polls\":{},\"busy_polls\":{},\"busy_ns_per_req\":{:.1},\"page_faults\":{},\"numa\":\"{}\",\
                 \"mc_stats\":{{\"get_cmds\":{},\"get_hits\":{},\"get_misses\":{},\"set_cmds\":{},\"evictions\":{},\
                 \"reclaimed\":{},\"curr_items\":{},\"total_items\":{},\"bytes_stored\":{},\"bytes_read\":{},\"bytes_written\":{},\
                 \"hashpower\":{},\"maxbytes_mib\":{}}},\"tenant\":{},\"pmu\":{}}}",
                policy,
                app,
                mc_ver,
                self.dt_s,
                self.rx_rpcs,
                self.tx_rpcs,
                rpcs_mps,
                self.get_rpcs,
                self.set_rpcs,
                self.nfv_rpcs,
                self.mc_misses,
                self.mc_errors,
                self.nfv_errors,
                self.bad,
                self.torn,
                self.tx_drops,
                self.sdp_imissed,
                self.sdp_ierrors,
                self.sdp_oerrors,
                self.sdp_rx_nombuf,
                self.sdp_pool_in_use,
                self.sdp_pool_avail,
                self.bar4_credit_writes,
                self.ceio_fast_merged,
                self.ceio_slow_merged,
                self.empty_polls,
                self.busy_polls,
                busy_ns_per_req,
                self.page_faults,
                numa,
                ms.get_cmds,
                ms.get_hits,
                ms.get_misses,
                ms.set_cmds,
                ms.evictions,
                ms.reclaimed,
                ms.curr_items,
                ms.total_items,
                ms.bytes_stored,
                ms.bytes_read,
                ms.bytes_written,
                ms.hashpower,
                ms.maxbytes_mib,
                self.tenant.to_json(),
                self.pmu_json,
            )
        }
    }

    struct Coord {
        quit: AtomicBool,
        cmd_word: AtomicU64,
        done_gen: AtomicU64,
        cmd_lock: Mutex<()>,
        reset_profile: Mutex<Option<McProfile>>,
        snap: Mutex<HostStats>,
    }

    impl Coord {
        fn new() -> Self {
            Self {
                quit: AtomicBool::new(false),
                cmd_word: AtomicU64::new(0),
                done_gen: AtomicU64::new(0),
                cmd_lock: Mutex::new(()),
                reset_profile: Mutex::new(None),
                snap: Mutex::new(HostStats::default()),
            }
        }

        fn call(&self, cmd: u64, profile: Option<McProfile>) -> Option<HostStats> {
            let _g = self.cmd_lock.lock().unwrap();
            *self.reset_profile.lock().unwrap() = profile;
            let prev = self.cmd_word.load(Ordering::Relaxed) >> 4;
            let next_gen = prev + 1;
            self.cmd_word.store((next_gen << 4) | (cmd & 0xf), Ordering::Release);
            let t0 = Instant::now();
            while self.done_gen.load(Ordering::Acquire) < next_gen {
                if self.quit.load(Ordering::Relaxed) || t0.elapsed() > Duration::from_secs(10) {
                    return None;
                }
                std::thread::sleep(Duration::from_micros(100));
            }
            Some(self.snap.lock().unwrap().clone())
        }
    }

    struct MergedPkt<'e> {
        queue: u16,
        pkt: Mbuf<'e>,
    }

    struct QueueCredits {
        taken: [u64; MAX_CARD_CORES],
        bytes_taken: [u64; MAX_CARD_CORES],
        slow_taken: [u64; MAX_CARD_CORES],
        credit_sent: [u64; MAX_CARD_CORES],
        slow_sent: [u64; MAX_CARD_CORES],
    }

    impl Default for QueueCredits {
        fn default() -> Self {
            Self {
                taken: [0; MAX_CARD_CORES],
                bytes_taken: [0; MAX_CARD_CORES],
                slow_taken: [0; MAX_CARD_CORES],
                credit_sent: [0; MAX_CARD_CORES],
                slow_sent: [0; MAX_CARD_CORES],
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    #[inline(always)]
    fn process_sdp_pkt<'e>(
        app: AppKind,
        zero_copy: bool,
        port: &Port<'e>,
        tx_mbuf_pool_opt: Option<&Mempool<'e>>,
        nfv_engine: &mut Option<NfvEngine>,
        mc_reply: &mut McReply,
        q: u16,
        pkt: Mbuf<'e>,
        qcred: &mut QueueCredits,
        stats: &mut HostStats,
        tx_burst: &mut Burst<'e, MAX_DRAIN>,
    ) {
        let flen = pkt.data().len();
        let qu = q as usize;
        // Credit every dequeued SDP frame unconditionally BEFORE any validation early return
        // so malformed/short/torn frames never leak card credit.
        qcred.taken[qu] += 1;
        qcred.bytes_taken[qu] += flen as u64;
        if flen >= SDP_HDR_LEN + 8 {
            let w0_peek = u64::from_le_bytes(pkt.data()[SDP_HDR_LEN..SDP_HDR_LEN + 8].try_into().unwrap());
            let op_peek = (w0_peek >> 56) as u8;
            if op_peek == OP_MC_SLOW || op_peek == OP_NFV_SLOW {
                qcred.slow_taken[qu] += 1;
            }
        }
        if flen < SDP_HDR_LEN + 64 {
            return;
        }
        {
            let d = pkt.data();
            if d[12] != 0x08 || d[13] != 0x00 || d[23] != 17 {
                return;
            }
            if u16::from_be_bytes([d[36], d[37]]) != SDP_UDP_DST_PORT {
                return;
            }
        }

        let w0 = u64::from_le_bytes(pkt.data()[SDP_HDR_LEN..SDP_HDR_LEN + 8].try_into().unwrap());
        let fmeta = u64::from_le_bytes(
            pkt.data()[SDP_HDR_LEN + 8..SDP_HDR_LEN + SDP_META_LEN].try_into().unwrap(),
        );
        let (rpc_seq, plen_u16, subop, _fc, _sdp_op) = unpack_wire_meta(w0);
        let (flow_id, burst_flags, card_seq) = unpack_flow_meta(fmeta);
        let plen = plen_u16 as usize;
        let req_total = sdp_payload_total_bytes(plen);
        if rpc_seq == 0 || plen < 32 || plen > MAX_MC_BYTES || SDP_HDR_LEN + req_total > flen {
            stats.bad += 1;
            return;
        }
        let tail_w = u64::from_le_bytes(
            pkt.data()[SDP_HDR_LEN + req_total - 8..SDP_HDR_LEN + req_total]
                .try_into()
                .unwrap(),
        );
        if tail_w != w0 {
            stats.torn += 1;
            return;
        }

        stats.rx_rpcs += 1;

        match app {
            AppKind::Memcached => {
                let mc_req_ptr = unsafe { pkt.data().as_ptr().add(SDP_HDR_LEN + SDP_META_LEN) };
                let rc = unsafe { mrv209_mc_exec(mc_req_ptr, plen, mc_reply) };
                if rc != 0 {
                    stats.mc_errors += 1;
                    unsafe { mrv209_mc_release() };
                    return;
                }
                if mc_reply.opcode == MC_CMD_GET {
                    stats.get_rpcs += 1;
                } else if mc_reply.opcode == MC_CMD_SET {
                    stats.set_rpcs += 1;
                }
                if mc_reply.status == MC_STATUS_ENOENT {
                    stats.mc_misses += 1;
                } else if mc_reply.status != MC_STATUS_OK {
                    stats.mc_errors += 1;
                }

                let resp_mc_len = mc_reply.total_len as usize;
                let resp_total = sdp_payload_total_bytes(resp_mc_len);
                let target_flen = SDP_HDR_LEN + resp_total;
                let Some(mut m) = prep_sdp_mbuf(pkt, flen, target_flen, zero_copy, tx_mbuf_pool_opt) else {
                    unsafe { mrv209_mc_release() };
                    stats.tx_drops += 1;
                    return;
                };

                let data = m.data_mut();
                let ip_tot = (20 + 8 + 6 + resp_total) as u16;
                let udp_len = (8 + 6 + resp_total) as u16;
                data[16..18].copy_from_slice(&ip_tot.to_be_bytes());
                data[38..40].copy_from_slice(&udp_len.to_be_bytes());

                let out_w0 = pack_wire_meta(
                    rpc_seq,
                    resp_mc_len as u16,
                    subop,
                    wire_frag_cnt(resp_mc_len),
                    OP_MC_FAST,
                );
                let out_fmeta = pack_flow_meta(flow_id, burst_flags, card_seq);
                data[SDP_HDR_LEN..SDP_HDR_LEN + 8].copy_from_slice(&out_w0.to_le_bytes());
                data[SDP_HDR_LEN + 8..SDP_HDR_LEN + SDP_META_LEN].copy_from_slice(&out_fmeta.to_le_bytes());
                copy_mc_reply_contiguous(
                    mc_reply,
                    &mut data[SDP_HDR_LEN + SDP_META_LEN..SDP_HDR_LEN + SDP_META_LEN + resp_mc_len],
                );
                data[target_flen - 8..target_flen].copy_from_slice(&out_w0.to_le_bytes());
                unsafe { mrv209_mc_release() };

                if let Err(p_back) = tx_burst.push(m) {
                    while !tx_burst.is_empty() {
                        let _ = port.tx(q, tx_burst);
                    }
                    let _ = tx_burst.push(p_back);
                }
                stats.tx_rpcs += 1;
            }
            AppKind::NfvNat | AppKind::NfvLb => {
                if plen < NFV_MIN_LEN {
                    stats.nfv_errors += 1;
                    return;
                }
                let Some(mut m) = prep_sdp_mbuf(pkt, flen, flen, zero_copy, tx_mbuf_pool_opt) else {
                    stats.tx_drops += 1;
                    return;
                };
                let data = m.data_mut();
                let nfv_slice = &mut data[SDP_HDR_LEN + SDP_META_LEN..SDP_HDR_LEN + SDP_META_LEN + plen];
                if let Some(eng) = nfv_engine.as_mut() {
                    if eng.process_in_place(nfv_slice, rdtsc()).is_err() {
                        stats.nfv_errors += 1;
                        return;
                    }
                }
                let out_w0 = pack_wire_meta(rpc_seq, plen as u16, subop, 1, OP_NFV_FAST);
                data[SDP_HDR_LEN..SDP_HDR_LEN + 8].copy_from_slice(&out_w0.to_le_bytes());
                data[flen - 8..flen].copy_from_slice(&out_w0.to_le_bytes());
                if let Err(p_back) = tx_burst.push(m) {
                    while !tx_burst.is_empty() {
                        let _ = port.tx(q, tx_burst);
                    }
                    let _ = tx_burst.push(p_back);
                }
                stats.nfv_rpcs += 1;
                stats.tx_rpcs += 1;
            }
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

        // Standalone solo co-located tenant calibration (zero network traffic baseline)
        if args.tenant_solo_ms > 0 {
            pin_thread(args.tenant_cpu);
            prefault_stack();
            let tsc_hz = calibrate_tsc_hz_local();
            let ticks_to_ns = 1e9 / tsc_hz;
            let idx = TenantIndex::new(args.tenant_ws_mb, 20901)?;
            let _ = unsafe { libc::mlockall(libc::MCL_CURRENT | libc::MCL_FUTURE) };
            let pmu = if args.pmu {
                Some(HostPmu::open(40, Some(args.tenant_cpu as i32))?)
            } else {
                None
            };
            let mut cur = 0usize;
            let mut sink = 0u64;
            // Warm up cache footprint and ramp schedutil CPU frequency to steady turbo (~3.9 GHz)
            let w0 = Instant::now();
            while w0.elapsed() < Duration::from_millis(3500) {
                let (nxt, v, _) = idx.probe_once(cur);
                cur = nxt;
                sink ^= v;
            }
            let mut hist = FastHist::new();
            let mut ops = 0u64;
            let mut bad = 0u64;
            let s0 = pmu.as_ref().map(HostPmu::snapshot);
            let t0 = Instant::now();
            let dur = Duration::from_millis(args.tenant_solo_ms);
            while t0.elapsed() < dur {
                let c0 = rdtsc_after_loads();
                let (nxt, v, ok) = idx.probe_once(cur);
                let c1 = rdtsc_after_loads();
                cur = nxt;
                sink ^= v;
                if !ok {
                    bad += 1;
                }
                let ns = ((c1.saturating_sub(c0)) as f64 * ticks_to_ns) as u64;
                hist.record_ns(ns);
                ops += 1;
            }
            let dt_s = t0.elapsed().as_secs_f64();
            let pmu_json = match (pmu.as_ref(), s0.as_ref()) {
                (Some(p), Some(snap0)) => format_host_pmu(snap0, &p.snapshot(), dt_s, ops),
                _ => "null".to_string(),
            };
            std::hint::black_box(sink);
            let ts = TenantStats {
                enabled: true,
                cpu: args.tenant_cpu,
                ws_mb: args.tenant_ws_mb,
                dt_s,
                ops,
                bad,
                p50_ns: hist.quantile_ns(0.50),
                p95_ns: hist.quantile_ns(0.95),
                p99_ns: hist.quantile_ns(0.99),
                p999_ns: hist.quantile_ns(0.999),
                mean_ns: hist.mean_ns(),
            };
            println!("{{\"solo_tenant\":true,\"tenant\":{},\"pmu\":{}}}", ts.to_json(), pmu_json);
            return Ok(());
        }

        if args.policy.uses_bar4() && !has_movdir64b() {
            return Err("CPU lacks MOVDIR64B".into());
        }

        if args.policy == Policy::CreditSdp {
            let safe_max = max_safe_r_eff(args.sdp_rx_desc) as u64;
            if args.r_eff > safe_max {
                eprintln!(
                    "WARN: r_eff={} exceeds Pitfall-1 safe bound {} for sdp_rx_desc={} (WMARK + 64 refill reserve)",
                    args.r_eff, safe_max, args.sdp_rx_desc
                );
            }
        }

        let (eal, _) = Eal::init(eal_args)?;
        let tsc_hz = eal.tsc_hz() as f64;
        let ticks_to_ns = 1e9 / tsc_hz;
        let cpu = unsafe { libc::sched_getcpu() };
        let socket = Port::socket_of(&eal, 0);

        // Initialize application backend on NUMA 1 (either unmodified Memcached 1.6.45 or stateful NFV)
        let mc_ver = if args.app == AppKind::Memcached {
            let mc_rc = unsafe {
                mrv209_mc_init(args.mc_mem_mb * 1024 * 1024, args.mc_hashpower, CTRL_CPU as i32)
            };
            if mc_rc != 0 {
                return Err(format!("mrv209_mc_init failed rc={mc_rc}").into());
            }
            let ver = unsafe { CStr::from_ptr(mrv209_mc_version()).to_string_lossy().into_owned() };
            for prof in [
                McProfile::V3840,
                McProfile::V1024,
                McProfile::V128,
                McProfile::V8,
                McProfile::WebSearchMc,
                args.mc_profile,
            ] {
                populate_memcached(prof, args.num_keys)?;
            }
            ver
        } else {
            "nfv".to_string()
        };

        let mut nfv_engine = match args.app {
            AppKind::NfvNat => {
                let mut e = NfvEngine::new(NfvMode::Nat);
                e.prewarm_flows(4096);
                Some(e)
            }
            AppKind::NfvLb => {
                let mut e = NfvEngine::new(NfvMode::Lb);
                e.prewarm_flows(4096);
                Some(e)
            }
            AppKind::Memcached => None,
        };
        prefault_stack();

        // Standard DPDK SDP port `0000:99:02.0` (identical data plane across all 4 policies!)
        let n_sdp_q = args.sdp_queues.min(MAX_CARD_CORES as u16).max(1);
        let rx_pool_sz = if args.sdp_pool_size > 0 {
            args.sdp_pool_size
        } else {
            match args.policy {
                // Default privRing large non-compact pool spanning ~70-180 MiB across host DRAM
                // with guaranteed headroom above `n_sdp_q * sdp_rx_desc` so rx_nombuf stays 0.
                Policy::LargeSdp => ((n_sdp_q as u32) * (args.sdp_rx_desc as u32 + 512) + 4095).max(32768),
                // Pitfall 2: compact DDIO-hot pool sized strictly for active descriptors + TX reclaim headroom
                Policy::SmallSdpDrop | Policy::CreditSdp | Policy::CeioStyle => {
                    ((n_sdp_q as u32) * (args.sdp_rx_desc as u32 + 512) + 1023).max(4095)
                }
            }
        };
        let rx_pool = Mempool::new_with_buf_size(&eal, "m209_rx_pool", rx_pool_sz, args.sdp_buf_size, socket)?;
        let tx_mbuf_pool_opt = if !args.sdp_zero_copy {
            Some(Mempool::new_with_buf_size(&eal, "m209_tx_pool", 2047, args.sdp_buf_size, socket)?)
        } else {
            None
        };
        let port = Port::start_with_opts_rss(
            &eal,
            0,
            &rx_pool,
            n_sdp_q,
            args.sdp_rx_desc,
            args.sdp_tx_desc,
            Some(SDP_MTU),
            false,
        )?;
        let _ = port.promiscuous();

        // Optional BAR4 slot 15 credit lease (`credit-sdp` & `ceio-style` ONLY; zero data-plane BAR4 writes)
        let lease_opt: Option<Lease> = if args.policy.uses_bar4() {
            Some(Lease::acquire(&args.card)?)
        } else {
            None
        };
        let bar4_slot = match lease_opt.as_ref() {
            Some(l) => Some(l.map_slot(&args.bar4)?),
            None => None,
        };
        let mut run_gen: u64 = 0;
        if let Some(slot) = bar4_slot.as_ref() {
            let cc = slot.read_u64(CTRL_CARD_CORES);
            if cc != n_sdp_q as u64 {
                eprintln!("WARN: card CTRL_CARD_CORES={cc} differs from host sdp_queues={n_sdp_q}");
            }
            run_gen = ((std::process::id() as u64) << 16) | (rdtsc_after_loads() & 0xffff) | 1;
            slot.write_u64(CTRL_POLICY_ID, args.policy.id());
            slot.write_u64(CTRL_R_EFF, args.r_eff);
            sfence();
            slot.write_u64(CTRL_HOST_RUN_GEN, run_gen);
            sfence();
            let t0 = Instant::now();
            loop {
                if slot.lease().check().is_err() {
                    return Err("BAR4 lease lost during handshake".into());
                }
                if slot.read_u64(CTRL_CARD_ACK_GEN) == run_gen {
                    break;
                }
                if t0.elapsed() > Duration::from_secs(5) {
                    return Err("timeout waiting for card CTRL_CARD_ACK_GEN".into());
                }
                std::thread::sleep(Duration::from_millis(1));
            }
        }

        let tenant_cpu_opt = if args.tenant { Some(args.tenant_cpu as i32) } else { None };
        let host_pmu = if args.pmu {
            Some(HostPmu::open(cpu, tenant_cpu_opt)?)
        } else {
            None
        };
        let numa_str = read_numa_maps_huge();

        let coord = Arc::new(Coord::new());
        let tenant_coord = Arc::new(TenantCoord::new());
        {
            let c = Arc::clone(&coord);
            ctrlc::set_handler(move || c.quit.store(true, Ordering::Relaxed))?;
        }

        let listener = TcpListener::bind(&args.ctrl)?;
        listener.set_nonblocking(true)?;

        std::thread::scope(|scope| -> Result<(), Box<dyn Error>> {
            // Spawn co-located Socket-1 cache-sensitive tenant thread on independent physical core (`cpu41`)
            if args.tenant {
                let t_quit = Arc::clone(&coord);
                let t_coord = Arc::clone(&tenant_coord);
                let t_cpu = args.tenant_cpu;
                let t_ws_mb = args.tenant_ws_mb;
                let ready_flag = Arc::new(AtomicBool::new(false));
                let ready_tx = Arc::clone(&ready_flag);
                scope.spawn(move || {
                    pin_thread(t_cpu);
                    prefault_stack();
                    let idx = TenantIndex::new(t_ws_mb, 20901).expect("TenantIndex alloc failed");
                    let mut cur = 0usize;
                    let mut sink = 0u64;
                    let mut hist = FastHist::new();
                    let mut ops = 0u64;
                    let mut bad = 0u64;
                    let mut t_reset = rdtsc_after_loads();
                    let mut handled_gen = 0u64;
                    // Touch working set once before publishing ready
                    for _ in 0..idx.len().min(65536) {
                        let (nxt, v, _) = idx.probe_once(cur);
                        cur = nxt;
                        sink ^= v;
                    }
                    ready_tx.store(true, Ordering::Release);

                    let mut iter = 0u64;
                    while !t_quit.quit.load(Ordering::Relaxed) {
                        let c0 = rdtsc_after_loads();
                        let (nxt, v, ok) = idx.probe_once(cur);
                        let c1 = rdtsc_after_loads();
                        cur = nxt;
                        sink ^= v;
                        if !ok {
                            bad += 1;
                        }
                        let ns = ((c1.saturating_sub(c0)) as f64 * ticks_to_ns) as u64;
                        hist.record_ns(ns);
                        ops += 1;
                        iter = iter.wrapping_add(1);

                        if (iter & 0x3ff) == 0 {
                            let w = t_coord.cmd_word.load(Ordering::Acquire);
                            let g = w >> 4;
                            if g > handled_gen {
                                let cmd = w & 0xf;
                                let now = rdtsc_after_loads();
                                if cmd == 1 {
                                    hist.reset();
                                    ops = 0;
                                    bad = 0;
                                    t_reset = now;
                                } else {
                                    let dt_s = now.saturating_sub(t_reset) as f64 / tsc_hz;
                                    *t_coord.snap.lock().unwrap() = TenantStats {
                                        enabled: true,
                                        cpu: t_cpu,
                                        ws_mb: t_ws_mb,
                                        dt_s,
                                        ops,
                                        bad,
                                        p50_ns: hist.quantile_ns(0.50),
                                        p95_ns: hist.quantile_ns(0.95),
                                        p99_ns: hist.quantile_ns(0.99),
                                        p999_ns: hist.quantile_ns(0.999),
                                        mean_ns: hist.mean_ns(),
                                    };
                                }
                                handled_gen = g;
                                t_coord.done_gen.store(g, Ordering::Release);
                            }
                        }
                    }
                    std::hint::black_box(sink);
                });
                let tw = Instant::now();
                while !ready_flag.load(Ordering::Acquire) {
                    if tw.elapsed() > Duration::from_secs(10) {
                        return Err("timeout waiting for co-located tenant init".into());
                    }
                    std::thread::sleep(Duration::from_millis(5));
                }
            }

            // Spawn control / Memcached clock thread pinned off data cores (`cpu44`)
            let coord_srv = Arc::clone(&coord);
            let numa_srv = numa_str.clone();
            let mc_ver_srv = mc_ver.clone();
            let policy_srv = args.policy;
            let app_srv = args.app;
            scope.spawn(move || {
                pin_thread(CTRL_CPU);
                prefault_stack();
                let mut rx_buf = [0u8; 256];
                let mut reply_buf = String::with_capacity(4096);
                let mut last_tick = Instant::now();
                while !coord_srv.quit.load(Ordering::Relaxed) {
                    if app_srv == AppKind::Memcached && last_tick.elapsed() >= Duration::from_millis(500) {
                        unsafe { mrv209_mc_tick_clock() };
                        last_tick = Instant::now();
                    }
                    match listener.accept() {
                        Ok((mut stream, _)) => {
                            let _ = stream.set_nodelay(true);
                            let _ = stream.set_read_timeout(Some(Duration::from_secs(10)));
                            let mut n_read = 0usize;
                            while n_read < rx_buf.len() {
                                match stream.read(&mut rx_buf[n_read..]) {
                                    Ok(0) => break,
                                    Ok(k) => {
                                        n_read += k;
                                        if rx_buf[..n_read].contains(&b'\n') {
                                            break;
                                        }
                                    }
                                    Err(_) => break,
                                }
                            }
                            if n_read > 0 {
                                let line = std::str::from_utf8(&rx_buf[..n_read]).unwrap_or("");
                                let mut it = line.trim().split_whitespace();
                                let cmd = it.next().unwrap_or("");
                                let arg1 = it.next();
                                reply_buf.clear();
                                match cmd {
                                    "PING" => reply_buf.push_str("{\"ok\":true}"),
                                    "RESET" => {
                                        let prof = arg1.and_then(McProfile::from_str_opt);
                                        let _ = coord_srv.call(1, prof);
                                        reply_buf.push_str("{\"ok\":true}");
                                    }
                                    "STATS" => match coord_srv.call(2, None) {
                                        Some(st) => {
                                            reply_buf.push_str(&st.to_json(
                                                policy_srv,
                                                app_srv,
                                                tsc_hz,
                                                &numa_srv,
                                                &mc_ver_srv,
                                            ));
                                        }
                                        None => reply_buf.push_str("{\"err\":\"timeout\"}"),
                                    },
                                    "QUIT" => {
                                        match coord_srv.call(3, None) {
                                            Some(st) => {
                                                reply_buf.push_str(&st.to_json(
                                                    policy_srv,
                                                    app_srv,
                                                    tsc_hz,
                                                    &numa_srv,
                                                    &mc_ver_srv,
                                                ));
                                            }
                                            None => reply_buf.push_str("{\"ok\":true}"),
                                        }
                                        coord_srv.quit.store(true, Ordering::Relaxed);
                                    }
                                    _ => reply_buf.push_str("{\"err\":\"unknown\"}"),
                                }
                                reply_buf.push('\n');
                                let _ = stream.write_all(reply_buf.as_bytes());
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

            // Warm stats/PMU/JSON buffers before READY so measurement takes 0 page faults
            let mut stats = HostStats::default();
            stats.reset(rdtsc_after_loads());
            let mut pmu_s0 = host_pmu.as_ref().map(HostPmu::snapshot);
            if args.app == AppKind::Memcached {
                unsafe { mrv209_mc_get_stats(&mut stats.mc_stats) };
            }
            if let (Some(p), Some(s0)) = (host_pmu.as_ref(), pmu_s0.as_ref()) {
                stats.pmu_json = format_host_pmu(s0, &p.snapshot(), 0.001, 1);
            }
            std::hint::black_box(stats.to_json(args.policy, args.app, tsc_hz, &numa_str, &mc_ver));
            let _ = port.stats_reset();
            if args.tenant {
                let _ = tenant_coord.call(1, &coord.quit);
            }
            stats.reset(rdtsc_after_loads());
            let mut pmu_t0 = Instant::now();
            pmu_s0 = host_pmu.as_ref().map(HostPmu::snapshot);
            let mut flt_base = read_minor_faults();

            println!(
                "MRV209_HOST READY policy={:?} app={:?} mc_version={} cpu={} tenant={} tenant_cpu={} tenant_ws_mb={} sdp_queues={} sdp_rx_desc={} sdp_buf_size={} sdp_pool_sz={} r_eff={} credit_batch={} numa={} pmu={}",
                args.policy,
                args.app,
                mc_ver,
                cpu,
                args.tenant,
                args.tenant_cpu,
                args.tenant_ws_mb,
                n_sdp_q,
                args.sdp_rx_desc,
                args.sdp_buf_size,
                rx_pool_sz,
                args.r_eff,
                args.credit_batch,
                numa_str,
                host_pmu.is_some()
            );
            let _ = std::io::stdout().flush();

            let mut handled_gen: u64 = 0;
            let mut rx_burst = Burst::<MAX_DRAIN>::new();
            let mut tx_bursts: Vec<Burst<'_, MAX_DRAIN>> = (0..n_sdp_q).map(|_| Burst::<MAX_DRAIN>::new()).collect();
            let mut sw_merge: SwMergeRing<MergedPkt<'_>> = SwMergeRing::with_capacity(1024);

            // Per-queue monotonic credit state (strictly monotonic across RESETs for the active run_gen)
            let mut qcred = QueueCredits::default();
            let credit_batch = args.credit_batch.max(1);
            let credit_flags = if args.policy == Policy::CeioStyle {
                CREDIT_FLAG_MOVDIR64B | CREDIT_FLAG_CEIO
            } else {
                CREDIT_FLAG_MOVDIR64B
            };

            let mut mc_reply = McReply::default();
            let mut iter: u64 = 0;
            let mut idle_streak: u64 = 0;
            let _ = unsafe { libc::mlockall(libc::MCL_CURRENT | libc::MCL_FUTURE) };
            let emit_credit = |q: usize,
                               slot_opt: Option<&testbed::lease::LeasedSlot<'_>>,
                               taken: u64,
                               bytes_taken: u64,
                               slow_taken: u64,
                               stats: &mut HostStats| {
                if let Some(slot) = slot_opt {
                    // Pitfall 4: single 64 B atomic no-SFENCE MOVDIR64B to queue q's exclusive BAR4 line.
                    // Hardware `SLIST_DBELL` (UC BAR0 MMIO written by `cnxk_ep_rx_refill`) guarantees
                    // descriptor refill visibility while BAR4 provides zero-fence credit pacing.
                    let cl = CreditLine::new(q, taken, run_gen, bytes_taken, rdtsc(), credit_flags, slow_taken);
                    unsafe {
                        movdir64b(slot.as_ptr().add(credit_line_offset(q)), &cl);
                    }
                    stats.bar4_credit_writes += 1;
                }
            };

            while !coord.quit.load(Ordering::Relaxed) {
                iter = iter.wrapping_add(1);
                if (iter & 0x3ff) == 0 {
                    if let Some(slot) = bar4_slot.as_ref() {
                        if slot.lease().check().is_err() {
                            break;
                        }
                    }
                    let word = coord.cmd_word.load(Ordering::Acquire);
                    let g = word >> 4;
                    if g > handled_gen {
                        let cmd = word & 0xf;
                        let now = rdtsc_after_loads();
                        if cmd == 1 {
                            if args.app == AppKind::Memcached {
                                if let Some(prof) = coord.reset_profile.lock().unwrap().take() {
                                    if let Err(e) = populate_memcached(prof, args.num_keys) {
                                        eprintln!("MRV209_HOST populate error: {e}");
                                    }
                                } else {
                                    unsafe { mrv209_mc_reset_stats() };
                                }
                            }
                            if let Some(nfv) = nfv_engine.as_mut() {
                                nfv.reset_stats();
                            }
                            sw_merge.reset_stats();
                            let _ = port.stats_reset();
                            if args.tenant {
                                let _ = tenant_coord.call(1, &coord.quit);
                            }
                            stats.reset(now);
                            pmu_t0 = Instant::now();
                            pmu_s0 = host_pmu.as_ref().map(HostPmu::snapshot);
                            flt_base = read_minor_faults();
                        } else {
                            let wall_s = pmu_t0.elapsed().as_secs_f64();
                            stats.dt_s = now.saturating_sub(stats.t_reset_tsc) as f64 / tsc_hz;
                            stats.page_faults = read_minor_faults().saturating_sub(flt_base);
                            stats.sdp_pool_in_use = rx_pool.in_use_count();
                            stats.sdp_pool_avail = rx_pool.avail_count();
                            stats.ceio_fast_merged = sw_merge.fast_merged;
                            stats.ceio_slow_merged = sw_merge.slow_merged;
                            if let Ok(pst) = port.stats() {
                                stats.sdp_imissed = pst.imissed;
                                stats.sdp_ierrors = pst.ierrors;
                                stats.sdp_oerrors = pst.oerrors;
                                stats.sdp_rx_nombuf = pst.rx_nombuf;
                            }
                            if args.app == AppKind::Memcached {
                                unsafe { mrv209_mc_get_stats(&mut stats.mc_stats) };
                            }
                            if args.tenant {
                                stats.tenant = tenant_coord.call(2, &coord.quit);
                            }
                            stats.pmu_json = match (host_pmu.as_ref(), pmu_s0.as_ref()) {
                                (Some(p), Some(s0)) => {
                                    let s1 = p.snapshot();
                                    format_host_pmu(s0, &s1, wall_s, stats.tx_rpcs)
                                }
                                _ => "null".to_string(),
                            };
                            *coord.snap.lock().unwrap() = stats.clone();
                        }
                        handled_gen = g;
                        coord.done_gen.store(g, Ordering::Release);
                        if cmd == 3 {
                            break;
                        }
                    }
                }

                let mut got_any = false;
                if args.policy == Policy::CeioStyle {
                    // CEIO-style: receive standard SDP bursts, demux fast vs slow into 2P1C SW merge ring,
                    // then drain merged stream into Memcached / NFV.
                    let t_busy0 = rdtsc();
                    for q in 0..n_sdp_q {
                        let n = port.rx(q, &mut rx_burst);
                        if n == 0 {
                            continue;
                        }
                        got_any = true;
                        rx_burst.drain_into(|pkt| {
                            let is_slow = if pkt.data().len() >= SDP_HDR_LEN + 8 {
                                let w0 = u64::from_le_bytes(
                                    pkt.data()[SDP_HDR_LEN..SDP_HDR_LEN + 8].try_into().unwrap(),
                                );
                                let op = (w0 >> 56) as u8;
                                op == OP_MC_SLOW || op == OP_NFV_SLOW
                            } else {
                                false
                            };
                            if is_slow {
                                sw_merge.push_slow(MergedPkt { queue: q, pkt });
                            } else {
                                sw_merge.push_fast(MergedPkt { queue: q, pkt });
                            }
                        });
                    }
                    if got_any {
                        idle_streak = 0;
                        while let Some(mp) = sw_merge.pop() {
                            let qu = mp.queue as usize;
                            process_sdp_pkt(
                                args.app,
                                args.sdp_zero_copy,
                                &port,
                                tx_mbuf_pool_opt.as_ref(),
                                &mut nfv_engine,
                                &mut mc_reply,
                                mp.queue,
                                mp.pkt,
                                &mut qcred,
                                &mut stats,
                                &mut tx_bursts[qu],
                            );
                        }
                        for q in 0..n_sdp_q {
                            let qu = q as usize;
                            while !tx_bursts[qu].is_empty() {
                                if port.tx(q, &mut tx_bursts[qu]) == 0 {
                                    std::hint::spin_loop();
                                }
                            }
                            if qcred.taken[qu] - qcred.credit_sent[qu] >= credit_batch
                                || qcred.slow_taken[qu] - qcred.slow_sent[qu] >= (credit_batch / 2).max(1)
                            {
                                emit_credit(
                                    qu,
                                    bar4_slot.as_ref(),
                                    qcred.taken[qu],
                                    qcred.bytes_taken[qu],
                                    qcred.slow_taken[qu],
                                    &mut stats,
                                );
                                qcred.credit_sent[qu] = qcred.taken[qu];
                                qcred.slow_sent[qu] = qcred.slow_taken[qu];
                            }
                        }
                        stats.busy_tsc += rdtsc().wrapping_sub(t_busy0);
                        stats.busy_polls += 1;
                    } else {
                        idle_streak = idle_streak.saturating_add(1);
                        // Flush residual credits once after brief genuine idle (~64 empty polls, ~1-2 us)
                        // so bursts strictly batch at `credit_batch` while idle tails never stall.
                        if idle_streak == 64 {
                            for qu in 0..(n_sdp_q as usize) {
                                if qcred.taken[qu] > qcred.credit_sent[qu] || qcred.slow_taken[qu] > qcred.slow_sent[qu] {
                                    emit_credit(
                                        qu,
                                        bar4_slot.as_ref(),
                                        qcred.taken[qu],
                                        qcred.bytes_taken[qu],
                                        qcred.slow_taken[qu],
                                        &mut stats,
                                    );
                                    qcred.credit_sent[qu] = qcred.taken[qu];
                                    qcred.slow_sent[qu] = qcred.slow_taken[qu];
                                }
                            }
                        }
                        stats.empty_polls += 1;
                    }
                } else {
                    // Unified single-path SDP receive loop (`large-sdp`, `small-sdp-drop`, `credit-sdp`)
                    for q in 0..n_sdp_q {
                        let n = port.rx(q, &mut rx_burst);
                        if n == 0 {
                            continue;
                        }
                        let t_busy0 = rdtsc();
                        got_any = true;
                        let qu = q as usize;
                        rx_burst.drain_into(|pkt| {
                            process_sdp_pkt(
                                args.app,
                                args.sdp_zero_copy,
                                &port,
                                tx_mbuf_pool_opt.as_ref(),
                                &mut nfv_engine,
                                &mut mc_reply,
                                q,
                                pkt,
                                &mut qcred,
                                &mut stats,
                                &mut tx_bursts[qu],
                            );
                        });
                        while !tx_bursts[qu].is_empty() {
                            if port.tx(q, &mut tx_bursts[qu]) == 0 {
                                std::hint::spin_loop();
                            }
                        }
                        // CreditSDP: issue one no-SFENCE MOVDIR64B per `credit_batch` packets AFTER port.rx()!
                        if args.policy == Policy::CreditSdp && qcred.taken[qu] - qcred.credit_sent[qu] >= credit_batch {
                            emit_credit(
                                qu,
                                bar4_slot.as_ref(),
                                qcred.taken[qu],
                                qcred.bytes_taken[qu],
                                0,
                                &mut stats,
                            );
                            qcred.credit_sent[qu] = qcred.taken[qu];
                        }
                        stats.busy_tsc += rdtsc().wrapping_sub(t_busy0);
                    }
                    if got_any {
                        idle_streak = 0;
                        stats.busy_polls += 1;
                    } else {
                        idle_streak = idle_streak.saturating_add(1);
                        if args.policy == Policy::CreditSdp && idle_streak == 64 {
                            for qu in 0..(n_sdp_q as usize) {
                                if qcred.taken[qu] > qcred.credit_sent[qu] {
                                    emit_credit(
                                        qu,
                                        bar4_slot.as_ref(),
                                        qcred.taken[qu],
                                        qcred.bytes_taken[qu],
                                        0,
                                        &mut stats,
                                    );
                                    qcred.credit_sent[qu] = qcred.taken[qu];
                                }
                            }
                        }
                        stats.empty_polls += 1;
                    }
                }
            }

            if args.policy.uses_bar4() {
                if let Ok(mut s) = std::net::TcpStream::connect_timeout(
                    &SYNC_ADDR.parse().unwrap(),
                    Duration::from_secs(2),
                ) {
                    let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
                    let _ = writeln!(s, "DETACH");
                    let _ = s.flush();
                    let mut resp_b = [0u8; 64];
                    let _ = s.read(&mut resp_b);
                }
            }

            let now_end = rdtsc_after_loads();
            stats.dt_s = now_end.saturating_sub(stats.t_reset_tsc) as f64 / tsc_hz;
            if args.app == AppKind::Memcached {
                unsafe { mrv209_mc_get_stats(&mut stats.mc_stats) };
            }
            println!(
                "MRV209_HOST_DONE {}",
                stats.to_json(args.policy, args.app, tsc_hz, &numa_str, &mc_ver)
            );
            if args.app == AppKind::Memcached {
                unsafe { mrv209_mc_shutdown() };
            }
            Ok(())
        })?;

        Ok(())
    }
}
