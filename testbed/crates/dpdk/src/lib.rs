//! Minimal safe layer over `dpdk-sys`: EAL, mbuf pools, ports, owned mbufs,
//! fixed-size bursts, memzones, per-queue RX/TX handles, stats/xstats,
//! CN10K coherent CQE peek/direct helpers, and a DMA queue to a PCIe host.
//!
//! Every object borrows the [`Eal`] it was created under, so the compiler keeps
//! them from outliving `rte_eal_cleanup`. Pools are never freed before that.

use std::cell::Cell;
use std::ffi::{CStr, CString, c_char, c_int, c_void};
use std::marker::PhantomData;
use std::mem::{ManuallyDrop, MaybeUninit};
use std::panic::{self, AssertUnwindSafe};
use std::ptr::{self, NonNull};
use std::{fmt, slice};

use dpdk_sys as sys;

pub use sys::dpdk_sys_cn10k_rxq_info as Cn10kRxqInfo;
pub use sys::dpdk_sys_cq_oracle as CqOracle;
pub use sys::dpdk_sys_cqe_peek_stats as CqePeekStats;

/// A failed DPDK call: the operation and its errno.
#[derive(Debug)]
pub struct Error {
    op: &'static str,
    errno: c_int,
}

impl Error {
    pub fn errno(&self) -> c_int {
        self.errno
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // SAFETY: rte_strerror returns a static or thread-local C string.
        let msg = unsafe { CStr::from_ptr(sys::rte_strerror(self.errno)) };
        write!(f, "{}: {}", self.op, msg.to_string_lossy())
    }
}

impl std::error::Error for Error {}

pub type Result<T, E = Error> = std::result::Result<T, E>;

/// ethdev calls return `-errno`.
fn check(op: &'static str, ret: c_int) -> Result<()> {
    if ret < 0 { Err(Error { op, errno: -ret }) } else { Ok(()) }
}

/// The initialised Environment Abstraction Layer; cleaned up on drop. Not
/// `Send`: init and cleanup belong to the main lcore. `Sync`, so that objects
/// borrowing it can be shared with the worker lcores of [`Eal::on_each_lcore`].
pub struct Eal(PhantomData<*mut ()>);

// SAFETY: methods taking &Eal only read EAL state that is fixed after init.
unsafe impl Sync for Eal {}

impl Eal {
    /// Initialise EAL from the full command line (program name first) and
    /// return the application arguments: the program name followed by
    /// everything after `--`.
    pub fn init(args: impl IntoIterator<Item = String>) -> Result<(Eal, Vec<String>)> {
        let args: Vec<CString> =
            args.into_iter().map(|a| CString::new(a).expect("NUL in argument")).collect();
        let mut argv: Vec<*mut c_char> = args.iter().map(|a| a.as_ptr().cast_mut()).collect();
        // SAFETY: argv points into `args`, which outlives the call.
        let parsed = unsafe { sys::rte_eal_init(argv.len() as c_int, argv.as_mut_ptr()) };
        if parsed < 0 {
            return Err(Error { op: "rte_eal_init", errno: unsafe { sys::dpdk_sys_errno() } });
        }
        let rest = argv[parsed as usize..]
            .iter()
            .map(|&p| unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned())
            .collect();
        Ok((Eal(PhantomData), rest))
    }

    pub fn tsc_hz(&self) -> u64 {
        unsafe { sys::rte_get_tsc_hz() }
    }

    pub fn lcores(&self) -> usize {
        unsafe { sys::rte_lcore_count() as usize }
    }

    /// Run `f(index, input)` once on each EAL lcore (index 0 on the main lcore,
    /// `1..lcores()` on the workers in EAL order) and return the results.
    pub fn on_each_lcore<S: Send, R: Send>(
        &self,
        inputs: Vec<S>,
        f: impl Fn(usize, S) -> R + Sync,
    ) -> Vec<R> {
        assert_eq!(inputs.len(), self.lcores(), "one input per lcore");
        struct Job<'f, S, R> {
            f: &'f (dyn Fn(usize, S) -> R + Sync),
            index: usize,
            input: Option<S>,
            out: Option<std::thread::Result<R>>,
        }
        unsafe extern "C" fn run<S, R>(arg: *mut c_void) -> c_int {
            let job = unsafe { &mut *arg.cast::<Job<S, R>>() };
            let input = job.input.take().expect("each job runs once");
            job.out = Some(panic::catch_unwind(AssertUnwindSafe(|| (job.f)(job.index, input))));
            0
        }

        let mut jobs: Vec<Job<S, R>> = inputs
            .into_iter()
            .enumerate()
            .map(|(index, input)| Job { f: &f, index, input: Some(input), out: None })
            .collect();
        let jobs_ptr: Vec<*mut Job<S, R>> = jobs.iter_mut().map(|j| j as *mut _).collect();
        let mut launched = Vec::new();
        let mut failure = None;
        let mut lcore = unsafe { sys::rte_get_next_lcore(u32::MAX, 1, 0) };
        for &job in &jobs_ptr[1..] {
            if lcore >= unsafe { sys::dpdk_sys_max_lcore() } {
                failure = Some(Error { op: "rte_get_next_lcore", errno: 19 });
                break;
            }
            let rc = unsafe { sys::rte_eal_remote_launch(Some(run::<S, R>), job.cast(), lcore) };
            if rc != 0 {
                failure = Some(Error { op: "rte_eal_remote_launch", errno: -rc });
                break;
            }
            launched.push(lcore);
            lcore = unsafe { sys::rte_get_next_lcore(lcore, 1, 0) };
        }
        if failure.is_none() {
            unsafe { run::<S, R>(jobs_ptr[0].cast()) };
        }
        for &w in &launched {
            unsafe { sys::rte_eal_wait_lcore(w) };
        }
        if let Some(e) = failure {
            panic!("{e}");
        }
        jobs.into_iter()
            .map(|j| match j.out.expect("every job ran") {
                Ok(r) => r,
                Err(p) => panic::resume_unwind(p),
            })
            .collect()
    }
}

impl Drop for Eal {
    fn drop(&mut self) {
        unsafe { sys::rte_eal_cleanup() };
    }
}

/// Read the CPU timestamp counter (`rte_rdtsc`).
pub fn rdtsc() -> u64 {
    unsafe { sys::rte_rdtsc() }
}

/// Read the timestamp counter only after every earlier load has completed.
/// Plain RDTSC may execute before an outstanding load (an MMIO read of a
/// device counter, a cache miss on a DMA'd line), which makes an arrival
/// timestamp too early. RDTSCP waits for all earlier instructions and loads.
/// On ARM64, DMB ISH + ISB prevents MRS CNTVCT_EL0 from executing early.
pub fn rdtsc_after_loads() -> u64 {
    #[cfg(target_arch = "x86_64")]
    {
        let mut aux = 0;
        unsafe { std::arch::x86_64::__rdtscp(&mut aux) }
    }
    #[cfg(target_arch = "aarch64")]
    {
        std::sync::atomic::fence(std::sync::atomic::Ordering::SeqCst);
        unsafe { std::arch::asm!("isb", options(nostack, preserves_flags)) };
        rdtsc()
    }
    #[cfg(not(any(target_arch = "x86_64", target_arch = "aarch64")))]
    {
        std::sync::atomic::fence(std::sync::atomic::Ordering::SeqCst);
        rdtsc()
    }
}

/// A pool of packet buffers.
pub struct Mempool<'e> {
    raw: NonNull<sys::rte_mempool>,
    _eal: PhantomData<&'e Eal>,
}

// SAFETY: DPDK mempools are multi-producer/multi-consumer; lcores may allocate
// from and free into one concurrently.
unsafe impl Send for Mempool<'_> {}
unsafe impl Sync for Mempool<'_> {}

impl<'e> Mempool<'e> {
    pub fn default_data_room() -> u16 {
        unsafe { sys::dpdk_sys_mbuf_default_buf_size() }
    }

    pub fn new(eal: &'e Eal, name: &str, size: u32, socket: i32) -> Result<Self> {
        Self::new_with_buf_size(eal, name, size, Self::default_data_room(), socket)
    }

    pub fn new_with_buf_size(
        eal: &'e Eal,
        name: &str,
        size: u32,
        data_room_size: u16,
        socket: i32,
    ) -> Result<Self> {
        let cache = if size >= 1024 { 256 } else { 0 };
        Self::new_with_opts(eal, name, size, cache, data_room_size, socket, None)
    }

    pub fn new_with_cache(
        eal: &'e Eal,
        name: &str,
        size: u32,
        cache_size: u32,
        data_room_size: u16,
        socket: i32,
    ) -> Result<Self> {
        Self::new_with_opts(eal, name, size, cache_size, data_room_size, socket, None)
    }

    pub fn new_with_opts(
        _eal: &'e Eal,
        name: &str,
        size: u32,
        cache_size: u32,
        data_room_size: u16,
        socket: i32,
        ops_name: Option<&str>,
    ) -> Result<Self> {
        let name = CString::new(name).expect("NUL in pool name");
        let raw = match ops_name {
            Some(ops) => {
                let ops_c = CString::new(ops).expect("NUL in ops name");
                unsafe {
                    sys::rte_pktmbuf_pool_create_by_ops(
                        name.as_ptr(),
                        size,
                        cache_size,
                        0,
                        data_room_size,
                        socket,
                        ops_c.as_ptr(),
                    )
                }
            }
            None => unsafe {
                sys::rte_pktmbuf_pool_create(
                    name.as_ptr(),
                    size,
                    cache_size,
                    0,
                    data_room_size,
                    socket,
                )
            },
        };
        let errno = || unsafe { sys::dpdk_sys_errno() };
        let raw = NonNull::new(raw)
            .ok_or_else(|| Error { op: "rte_pktmbuf_pool_create", errno: errno() })?;
        Ok(Mempool { raw, _eal: PhantomData })
    }

    pub fn alloc(&self) -> Option<Mbuf<'e>> {
        NonNull::new(unsafe { sys::rte_pktmbuf_alloc(self.raw.as_ptr()) })
            .map(|raw| Mbuf { raw, _eal: PhantomData })
    }

    pub fn alloc_burst<const N: usize>(&self, burst: &mut Burst<'e, N>, count: usize) -> usize {
        let n = count.min(N - burst.len);
        if n == 0 {
            return 0;
        }
        let rc = unsafe {
            sys::dpdk_sys_pktmbuf_alloc_bulk(
                self.raw.as_ptr(),
                burst.raw_slots().add(burst.len),
                n as u32,
            )
        };
        if rc == 0 {
            burst.len += n;
            n
        } else {
            0
        }
    }

    pub fn in_use_count(&self) -> u32 {
        unsafe { sys::dpdk_sys_mempool_in_use_count(self.raw.as_ptr()) }
    }

    pub fn avail_count(&self) -> u32 {
        unsafe { sys::dpdk_sys_mempool_avail_count(self.raw.as_ptr()) }
    }
}

impl Drop for Mempool<'_> {
    fn drop(&mut self) {
        unsafe { sys::rte_mempool_free(self.raw.as_ptr()) };
    }
}

/// An owned packet buffer, returned to its pool on drop.
#[repr(transparent)]
pub struct Mbuf<'e> {
    raw: NonNull<sys::rte_mbuf>,
    _eal: PhantomData<&'e Eal>,
}

// SAFETY: an owned mbuf may move to another lcore and be freed there.
unsafe impl Send for Mbuf<'_> {}

impl<'e> Mbuf<'e> {
    pub fn data(&self) -> &[u8] {
        unsafe {
            let m = self.raw.as_ptr();
            slice::from_raw_parts(sys::dpdk_sys_mbuf_data(m), sys::dpdk_sys_mbuf_data_len(m).into())
        }
    }

    pub fn data_mut(&mut self) -> &mut [u8] {
        unsafe {
            let m = self.raw.as_ptr();
            slice::from_raw_parts_mut(
                sys::dpdk_sys_mbuf_data(m),
                sys::dpdk_sys_mbuf_data_len(m).into(),
            )
        }
    }

    pub fn data_ptr(&self) -> *const u8 {
        unsafe { sys::dpdk_sys_mbuf_data(self.raw.as_ptr()) }
    }

    pub fn data_mut_ptr(&mut self) -> *mut u8 {
        unsafe { sys::dpdk_sys_mbuf_data(self.raw.as_ptr()) }
    }

    pub fn pkt_len(&self) -> usize {
        unsafe { sys::dpdk_sys_mbuf_pkt_len(self.raw.as_ptr()) as usize }
    }

    pub fn data_len(&self) -> usize {
        unsafe { sys::dpdk_sys_mbuf_data_len(self.raw.as_ptr()) as usize }
    }

    pub fn nb_segs(&self) -> u16 {
        unsafe { sys::dpdk_sys_mbuf_nb_segs(self.raw.as_ptr()) }
    }

    pub fn ol_flags(&self) -> u64 {
        unsafe { sys::dpdk_sys_mbuf_ol_flags(self.raw.as_ptr()) }
    }

    pub fn buf_len(&self) -> u16 {
        unsafe { sys::dpdk_sys_mbuf_buf_len(self.raw.as_ptr()) }
    }

    pub fn data_off(&self) -> u16 {
        unsafe { sys::dpdk_sys_mbuf_data_off(self.raw.as_ptr()) }
    }

    pub fn read_linear(&self, dst: &mut [u8]) -> usize {
        unsafe {
            sys::dpdk_sys_mbuf_read_linear(self.raw.as_ptr(), dst.as_mut_ptr(), dst.len() as u32)
                as usize
        }
    }

    pub fn poison_all_segs(&mut self, tag: u64) {
        unsafe { sys::dpdk_sys_mbuf_poison_all_segs(self.raw.as_ptr(), tag) };
    }

    pub fn chain(&mut self, tail: Mbuf<'e>) -> Result<()> {
        let tail_raw = tail.into_raw();
        let rc = unsafe { sys::dpdk_sys_mbuf_chain(self.raw.as_ptr(), tail_raw) };
        if rc != 0 {
            unsafe { sys::rte_pktmbuf_free(tail_raw) };
            return Err(Error { op: "rte_pktmbuf_chain", errno: -rc });
        }
        Ok(())
    }

    pub fn iova(&self) -> u64 {
        unsafe { sys::dpdk_sys_mbuf_iova(self.raw.as_ptr()) }
    }

    pub fn append(&mut self, len: u16) -> Option<&mut [u8]> {
        let tail = unsafe { sys::rte_pktmbuf_append(self.raw.as_ptr(), len) };
        NonNull::new(tail.cast::<u8>())
            .map(|p| unsafe { slice::from_raw_parts_mut(p.as_ptr(), len.into()) })
    }

    pub fn set_len(&mut self, len: u16) {
        // SAFETY: caller ensures `len` fits inside data room.
        unsafe { sys::dpdk_sys_pktmbuf_set_len(self.raw.as_ptr(), len) };
    }

    pub fn trim(&mut self, len: u16) -> Result<()> {
        check("dpdk_sys_mbuf_trim", unsafe { sys::dpdk_sys_mbuf_trim(self.raw.as_ptr(), len) })
    }

    fn into_raw(self) -> *mut sys::rte_mbuf {
        ManuallyDrop::new(self).raw.as_ptr()
    }
}

impl Drop for Mbuf<'_> {
    fn drop(&mut self) {
        unsafe { sys::rte_pktmbuf_free(self.raw.as_ptr()) };
    }
}

/// Up to `N` packets moved through a queue together: one `rx` poll or one
/// `tx` doorbell.
pub struct Burst<'e, const N: usize = 32> {
    len: usize,
    pkts: [MaybeUninit<Mbuf<'e>>; N],
}

impl<'e, const N: usize> Burst<'e, N> {
    pub fn new() -> Self {
        Burst { len: 0, pkts: [const { MaybeUninit::uninit() }; N] }
    }

    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    pub fn is_full(&self) -> bool {
        self.len == N
    }

    pub fn push(&mut self, pkt: Mbuf<'e>) -> Result<(), Mbuf<'e>> {
        if self.is_full() {
            return Err(pkt);
        }
        self.pkts[self.len].write(pkt);
        self.len += 1;
        Ok(())
    }

    pub fn pop(&mut self) -> Option<Mbuf<'e>> {
        if self.len == 0 {
            return None;
        }
        self.len -= 1;
        Some(unsafe { self.pkts[self.len].assume_init_read() })
    }

    pub fn as_slice(&self) -> &[Mbuf<'e>] {
        unsafe { slice::from_raw_parts(self.pkts.as_ptr().cast(), self.len) }
    }

    pub fn as_mut_slice(&mut self) -> &mut [Mbuf<'e>] {
        unsafe { slice::from_raw_parts_mut(self.pkts.as_mut_ptr().cast(), self.len) }
    }

    pub fn alloc_bulk(&mut self, pool: &Mempool<'e>, count: usize) -> bool {
        debug_assert!(self.len + count <= N);
        if count == 0 {
            return true;
        }
        let rc = unsafe {
            sys::dpdk_sys_pktmbuf_alloc_bulk(
                pool.raw.as_ptr(),
                self.raw_slots().add(self.len),
                count as u32,
            )
        };
        if rc == 0 {
            self.len += count;
            true
        } else {
            false
        }
    }

    /// Move every packet out of the burst in order without freeing.
    pub fn drain_into(&mut self, mut f: impl FnMut(Mbuf<'e>)) {
        let len = self.len;
        self.len = 0;
        for i in 0..len {
            let pkt = unsafe { self.pkts[i].assume_init_read() };
            f(pkt);
        }
    }

    /// Free packets one by one in reverse order so a LIFO mempool cache hands
    /// them back in their original FIFO order.
    pub fn clear_reverse(&mut self) {
        while self.len > 0 {
            self.len -= 1;
            let m = unsafe { self.pkts[self.len].assume_init_read() };
            drop(m);
        }
    }

    /// Free every packet still held.
    pub fn clear(&mut self) {
        if self.len > 0 {
            unsafe { sys::rte_pktmbuf_free_bulk(self.raw_slots(), self.len as u32) };
            self.len = 0;
        }
    }

    fn raw_slots(&mut self) -> *mut *mut sys::rte_mbuf {
        self.pkts.as_mut_ptr().cast()
    }
}

impl<const N: usize> Default for Burst<'_, N> {
    fn default() -> Self {
        Self::new()
    }
}

impl<const N: usize> Drop for Burst<'_, N> {
    fn drop(&mut self) {
        self.clear();
    }
}

/// A physically contiguous, aligned block of hugepage memory for a device to
/// reach by address; freed on drop.
pub struct Memzone<'e> {
    raw: NonNull<sys::rte_memzone>,
    _eal: PhantomData<&'e Eal>,
}

unsafe impl Send for Memzone<'_> {}
unsafe impl Sync for Memzone<'_> {}

impl<'e> Memzone<'e> {
    pub fn reserve_contiguous(
        _eal: &'e Eal,
        name: &str,
        len: usize,
        socket: i32,
        align: u32,
    ) -> Result<Self> {
        let name = CString::new(name).expect("NUL in memzone name");
        let raw =
            unsafe { sys::dpdk_sys_memzone_reserve_contig(name.as_ptr(), len, socket, align) };
        let errno = || unsafe { sys::dpdk_sys_errno() };
        let raw = NonNull::new(raw.cast_mut())
            .ok_or_else(|| Error { op: "rte_memzone_reserve_aligned", errno: errno() })?;
        Ok(Memzone { raw, _eal: PhantomData })
    }

    pub fn as_ptr(&self) -> *mut u8 {
        unsafe { sys::dpdk_sys_memzone_addr(self.raw.as_ptr()) }.cast()
    }

    pub fn iova(&self) -> u64 {
        unsafe { sys::dpdk_sys_memzone_iova(self.raw.as_ptr()) }
    }

    pub fn phys_addr(&self) -> Option<u64> {
        let pa = unsafe { sys::rte_mem_virt2phy(self.as_ptr().cast()) };
        (pa != u64::MAX).then_some(pa)
    }
}

impl Drop for Memzone<'_> {
    fn drop(&mut self) {
        unsafe { sys::rte_memzone_free(self.raw.as_ptr()) };
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct EthStats {
    pub ipackets: u64,
    pub opackets: u64,
    pub ibytes: u64,
    pub obytes: u64,
    pub imissed: u64,
    pub ierrors: u64,
    pub oerrors: u64,
    pub rx_nombuf: u64,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct PortConfigEx {
    pub mtu: u32,
    pub lpbk_mode: bool,
    pub rx_scatter: bool,
    pub rx_udp_cksum: bool,
    pub rx_ipv4_cksum: bool,
    pub rx_keep_crc: bool,
    pub tx_multi_segs: bool,
}

fn eth_stats_get(port: u16) -> Result<EthStats> {
    let mut st = sys::rte_eth_stats::default();
    check("rte_eth_stats_get", unsafe { sys::rte_eth_stats_get(port, &mut st) })?;
    Ok(EthStats {
        ipackets: st.ipackets,
        opackets: st.opackets,
        ibytes: st.ibytes,
        obytes: st.obytes,
        imissed: st.imissed,
        ierrors: st.ierrors,
        oerrors: st.oerrors,
        rx_nombuf: st.rx_nombuf,
    })
}

fn eth_xstats_get(port: u16) -> Result<Vec<(String, u64)>> {
    let len = unsafe { sys::rte_eth_xstats_get_names(port, std::ptr::null_mut(), 0) };
    if len < 0 {
        return Err(Error { op: "rte_eth_xstats_get_names", errno: -len });
    }
    let n = len as usize;
    if n == 0 {
        return Ok(Vec::new());
    }
    let mut names = vec![sys::rte_eth_xstat_name::default(); n];
    let got_names = unsafe { sys::rte_eth_xstats_get_names(port, names.as_mut_ptr(), n as u32) };
    if got_names < 0 {
        return Err(Error { op: "rte_eth_xstats_get_names", errno: -got_names });
    }
    let mut vals = vec![sys::rte_eth_xstat::default(); n];
    let got_vals = unsafe { sys::rte_eth_xstats_get(port, vals.as_mut_ptr(), n as u32) };
    if got_vals < 0 {
        return Err(Error { op: "rte_eth_xstats_get", errno: -got_vals });
    }
    let count = (got_names as usize).min(got_vals as usize);
    let mut out = Vec::with_capacity(count);
    for i in 0..count {
        let raw_name = &names[i].name;
        let nul = raw_name.iter().position(|&c| c == 0).unwrap_or(raw_name.len());
        let bytes: Vec<u8> = raw_name[..nul].iter().map(|&c| c as u8).collect();
        out.push((String::from_utf8_lossy(&bytes).into_owned(), vals[i].value));
    }
    Ok(out)
}

/// A started ethdev port; stopped on drop. Not `Sync`: DPDK queues are not
/// thread-safe, so lcores other than the owner get [`TxQueue`], [`RxQueue`], or
/// [`QueuePair`] handles.
pub struct Port<'e> {
    id: u16,
    rx_queues: u16,
    tx_queues: u16,
    rss_hf: u64,
    _eal: PhantomData<&'e Eal>,
    _not_sync: PhantomData<Cell<()>>,
}

impl<'e> Port<'e> {
    pub fn socket_of(_eal: &Eal, id: u16) -> i32 {
        unsafe { sys::rte_eth_dev_socket_id(id) }
    }

    pub fn by_name(_eal: &Eal, name: &str) -> Result<u16> {
        let c = CString::new(name).expect("NUL in port name");
        let mut id = 0u16;
        check("rte_eth_dev_get_port_by_name", unsafe {
            sys::rte_eth_dev_get_port_by_name(c.as_ptr(), &mut id)
        })?;
        Ok(id)
    }

    pub fn rss_hf(&self) -> u64 {
        self.rss_hf
    }

    pub fn start(eal: &'e Eal, id: u16, pool: &Mempool<'e>, queues: u16, desc: u16) -> Result<Self> {
        Self::start_with_mtu(eal, id, pool, queues, desc, None)
    }

    pub fn start_with_mtu(
        eal: &'e Eal,
        id: u16,
        pool: &Mempool<'e>,
        queues: u16,
        desc: u16,
        mtu: Option<u16>,
    ) -> Result<Self> {
        Self::start_ex(
            eal,
            id,
            pool,
            queues,
            desc,
            PortConfigEx { mtu: mtu.unwrap_or(1500) as u32, ..Default::default() },
        )
    }

    pub fn start_with_opts_rss(
        eal: &'e Eal,
        id: u16,
        rx_pool: &Mempool<'e>,
        queues: u16,
        rx_desc: u16,
        tx_desc: u16,
        mtu: Option<u16>,
        enable_rss: bool,
    ) -> Result<Self> {
        Self::start_with_opts_rss_fc(eal, id, rx_pool, queues, rx_desc, tx_desc, mtu, enable_rss, false)
    }

    pub fn start_with_opts_rss_fc(
        eal: &'e Eal,
        id: u16,
        rx_pool: &Mempool<'e>,
        queues: u16,
        rx_desc: u16,
        tx_desc: u16,
        mtu: Option<u16>,
        enable_rss: bool,
        disable_fc: bool,
    ) -> Result<Self> {
        let socket = Self::socket_of(eal, id) as u32;
        let mut rss_hf = 0u64;
        unsafe {
            check(
                "dpdk_sys_eth_dev_configure_rss",
                sys::dpdk_sys_eth_dev_configure_rss(id, queues, queues, enable_rss, &mut rss_hf),
            )?;
            if let Some(m) = mtu {
                check("rte_eth_dev_set_mtu", sys::rte_eth_dev_set_mtu(id, m))?;
            }
            for q in 0..queues {
                let rx = sys::rte_eth_rx_queue_setup(
                    id,
                    q,
                    rx_desc,
                    socket,
                    std::ptr::null(),
                    rx_pool.raw.as_ptr(),
                );
                check("rte_eth_rx_queue_setup", rx)?;
                let tx = sys::rte_eth_tx_queue_setup(id, q, tx_desc, socket, std::ptr::null());
                check("rte_eth_tx_queue_setup", tx)?;
            }
            if disable_fc {
                let fc_rc = sys::dpdk_sys_eth_disable_flow_ctrl(id);
                if fc_rc != 0 && fc_rc != -95 {
                    check("dpdk_sys_eth_disable_flow_ctrl", fc_rc)?;
                }
            }
            check("rte_eth_dev_start", sys::rte_eth_dev_start(id))?;
        }
        Ok(Port {
            id,
            rx_queues: queues,
            tx_queues: queues,
            rss_hf,
            _eal: PhantomData,
            _not_sync: PhantomData,
        })
    }

    pub fn start_per_queue(
        eal: &'e Eal,
        id: u16,
        rx_pools: &[&Mempool<'e>],
        rx_desc: u16,
        tx_desc: u16,
    ) -> Result<Self> {
        Self::start_per_queue_ex(eal, id, rx_pools, rx_desc, tx_desc, None, false)
    }

    pub fn start_per_queue_ex(
        eal: &'e Eal,
        id: u16,
        rx_pools: &[&Mempool<'e>],
        rx_desc: u16,
        tx_desc: u16,
        mtu: Option<u16>,
        enable_rss: bool,
    ) -> Result<Self> {
        let rx_descs = vec![rx_desc; rx_pools.len()];
        Self::start_with_queue_cfg(eal, id, rx_pools, &rx_descs, tx_desc, mtu, enable_rss, false)
    }

    pub fn start_with_queue_cfg(
        eal: &'e Eal,
        id: u16,
        rx_pools: &[&Mempool<'e>],
        rx_descs: &[u16],
        tx_desc: u16,
        mtu: Option<u16>,
        enable_rss: bool,
        disable_fc: bool,
    ) -> Result<Self> {
        assert_eq!(rx_pools.len(), rx_descs.len());
        let queues = rx_pools.len() as u16;
        assert!(queues > 0, "at least one queue required");
        let socket = Self::socket_of(eal, id) as u32;
        let mut rss_hf = 0u64;
        unsafe {
            check(
                "dpdk_sys_eth_dev_configure_rss",
                sys::dpdk_sys_eth_dev_configure_rss(id, queues, queues, enable_rss, &mut rss_hf),
            )?;
            if let Some(m) = mtu {
                check("rte_eth_dev_set_mtu", sys::rte_eth_dev_set_mtu(id, m))?;
            }
            for q in 0..queues {
                let rx = sys::rte_eth_rx_queue_setup(
                    id,
                    q,
                    rx_descs[q as usize],
                    socket,
                    std::ptr::null(),
                    rx_pools[q as usize].raw.as_ptr(),
                );
                check("rte_eth_rx_queue_setup", rx)?;
                let tx = sys::rte_eth_tx_queue_setup(id, q, tx_desc, socket, std::ptr::null());
                check("rte_eth_tx_queue_setup", tx)?;
            }
            if disable_fc {
                let fc_rc = sys::dpdk_sys_eth_disable_flow_ctrl(id);
                if fc_rc != 0 && fc_rc != -95 {
                    check("dpdk_sys_eth_disable_flow_ctrl", fc_rc)?;
                }
            }
            check("rte_eth_dev_start", sys::rte_eth_dev_start(id))?;
        }
        Ok(Port {
            id,
            rx_queues: queues,
            tx_queues: queues,
            rss_hf,
            _eal: PhantomData,
            _not_sync: PhantomData,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub fn start_fast(
        eal: &'e Eal,
        id: u16,
        rx_pools: &[&Mempool<'e>],
        rx_queues: u16,
        tx_queues: u16,
        rx_desc: u16,
        tx_desc: u16,
        mtu: Option<u16>,
        enable_rss: bool,
        fast_free: bool,
        disable_fc: bool,
    ) -> Result<(Self, u64)> {
        let socket = Self::socket_of(eal, id) as u32;
        let mut rss_hf = 0u64;
        let mut tx_off = 0u64;
        unsafe {
            check(
                "dpdk_sys_eth_dev_configure_fast",
                sys::dpdk_sys_eth_dev_configure_fast(
                    id,
                    rx_queues,
                    tx_queues,
                    enable_rss,
                    fast_free,
                    &mut rss_hf,
                    &mut tx_off,
                ),
            )?;
            if let Some(m) = mtu {
                check("rte_eth_dev_set_mtu", sys::rte_eth_dev_set_mtu(id, m))?;
            }
            if disable_fc {
                let _ = sys::dpdk_sys_eth_disable_flow_ctrl(id);
            }
            for q in 0..rx_queues {
                let mp = rx_pools[(q as usize).min(rx_pools.len() - 1)];
                let rx = sys::rte_eth_rx_queue_setup(
                    id,
                    q,
                    rx_desc,
                    socket,
                    std::ptr::null(),
                    mp.raw.as_ptr(),
                );
                check("rte_eth_rx_queue_setup", rx)?;
            }
            for q in 0..tx_queues {
                let tx =
                    sys::dpdk_sys_eth_dev_tx_queue_setup_fast(id, q, tx_desc, socket, tx_off);
                check("dpdk_sys_eth_dev_tx_queue_setup_fast", tx)?;
            }
            check("rte_eth_dev_start", sys::rte_eth_dev_start(id))?;
        }
        Ok((
            Port {
                id,
                rx_queues,
                tx_queues,
                rss_hf,
                _eal: PhantomData,
                _not_sync: PhantomData,
            },
            tx_off,
        ))
    }

    pub fn start_ex(
        eal: &'e Eal,
        id: u16,
        pool: &Mempool<'e>,
        queues: u16,
        desc: u16,
        cfg: PortConfigEx,
    ) -> Result<Self> {
        let socket = Self::socket_of(eal, id) as u32;
        unsafe {
            let mut rx_ol = 0u64;
            if cfg.rx_scatter {
                rx_ol |= sys::dpdk_sys_rx_offload_scatter();
            }
            if cfg.rx_udp_cksum {
                rx_ol |= sys::dpdk_sys_rx_offload_udp_cksum();
            }
            if cfg.rx_ipv4_cksum {
                rx_ol |= sys::dpdk_sys_rx_offload_ipv4_cksum();
            }
            if cfg.rx_keep_crc {
                rx_ol |= sys::dpdk_sys_rx_offload_keep_crc();
            }
            let mut tx_ol = 0u64;
            if cfg.tx_multi_segs {
                tx_ol |= sys::dpdk_sys_tx_offload_multi_segs();
            }
            let mtu = if cfg.mtu == 0 { 1500 } else { cfg.mtu };
            check(
                "dpdk_sys_eth_dev_configure_ex",
                sys::dpdk_sys_eth_dev_configure_ex(
                    id,
                    queues,
                    queues,
                    mtu,
                    if cfg.lpbk_mode { 1 } else { 0 },
                    rx_ol,
                    tx_ol,
                ),
            )?;
            if mtu != 1500 {
                check("rte_eth_dev_set_mtu", sys::rte_eth_dev_set_mtu(id, mtu as u16))?;
            }
            for q in 0..queues {
                let rx = sys::rte_eth_rx_queue_setup(
                    id,
                    q,
                    desc,
                    socket,
                    std::ptr::null(),
                    pool.raw.as_ptr(),
                );
                check("rte_eth_rx_queue_setup", rx)?;
                let tx = sys::rte_eth_tx_queue_setup(id, q, desc, socket, std::ptr::null());
                check("rte_eth_tx_queue_setup", tx)?;
            }
            check("rte_eth_dev_start", sys::rte_eth_dev_start(id))?;
        }
        Ok(Port {
            id,
            rx_queues: queues,
            tx_queues: queues,
            rss_hf: 0,
            _eal: PhantomData,
            _not_sync: PhantomData,
        })
    }

    pub fn dev_stop(&self) -> Result<()> {
        check("rte_eth_dev_stop", unsafe { sys::rte_eth_dev_stop(self.id) })
    }

    /// Disable Ethernet pause/flow-control on a started port (stops and
    /// restarts the port as required by CN10K cnxk PMD).
    pub fn disable_flow_ctrl(&self) -> Result<()> {
        unsafe {
            check("rte_eth_dev_stop", sys::rte_eth_dev_stop(self.id))?;
            let fc_res = check(
                "dpdk_sys_eth_disable_flow_ctrl",
                sys::dpdk_sys_eth_disable_flow_ctrl(self.id),
            );
            let start_res = check("rte_eth_dev_start", sys::rte_eth_dev_start(self.id));
            fc_res?;
            start_res
        }
    }

    pub fn dev_start(&self) -> Result<()> {
        check("rte_eth_dev_start", unsafe { sys::rte_eth_dev_start(self.id) })
    }

    pub fn rx_queue_stop(&self, queue: u16) -> Result<()> {
        check("rte_eth_dev_rx_queue_stop", unsafe { sys::rte_eth_dev_rx_queue_stop(self.id, queue) })
    }

    pub fn rx_queue_start(&self, queue: u16) -> Result<()> {
        check("rte_eth_dev_rx_queue_start", unsafe {
            sys::rte_eth_dev_rx_queue_start(self.id, queue)
        })
    }

    pub fn set_rx_drop_re(&self, ena: bool) -> Result<()> {
        check("dpdk_sys_cn10k_rx_drop_re_set", unsafe {
            sys::dpdk_sys_cn10k_rx_drop_re_set(self.id, if ena { 1 } else { 0 })
        })
    }

    pub fn bar0_ptr(&self) -> Option<*mut u8> {
        let p = unsafe { sys::dpdk_sys_port_bar0(self.id) };
        if p.is_null() { None } else { Some(p.cast()) }
    }

    pub fn stats(&self) -> Result<EthStats> {
        eth_stats_get(self.id)
    }

    pub fn stats_reset(&self) -> Result<()> {
        check("rte_eth_stats_reset", unsafe { sys::rte_eth_stats_reset(self.id) })
    }

    pub fn reset_stats(&self) -> Result<()> {
        self.stats_reset()
    }

    pub fn xstats(&self) -> Result<Vec<(String, u64)>> {
        eth_xstats_get(self.id)
    }

    pub fn xstats_reset(&self) -> Result<()> {
        check("rte_eth_xstats_reset", unsafe { sys::rte_eth_xstats_reset(self.id) })
    }

    pub fn stats_handle(&self) -> PortStats<'_, 'e> {
        PortStats { port: self.id, _port: PhantomData }
    }

    pub fn tx_queues(&mut self) -> Vec<TxQueue<'_, 'e>> {
        (0..self.tx_queues).map(|queue| TxQueue { port: self.id, queue, _port: PhantomData }).collect()
    }

    pub fn split_queues(&mut self) -> (PortStats<'_, 'e>, Vec<QueuePair<'_, 'e>>) {
        let st = PortStats { port: self.id, _port: PhantomData };
        let n = self.rx_queues.max(self.tx_queues);
        let qps = (0..n).map(|queue| QueuePair { port: self.id, queue, _port: PhantomData }).collect();
        (st, qps)
    }

    pub fn split_queue_pairs(&self) -> Vec<QueuePair<'_, 'e>> {
        let n = self.rx_queues.max(self.tx_queues);
        (0..n).map(|queue| QueuePair { port: self.id, queue, _port: PhantomData }).collect()
    }

    pub fn split_rx_tx_queues(
        &mut self,
    ) -> (PortStats<'_, 'e>, Vec<RxQueue<'_, 'e>>, Vec<TxQueue<'_, 'e>>) {
        let st = PortStats { port: self.id, _port: PhantomData };
        let rxs = (0..self.rx_queues)
            .map(|queue| RxQueue { port: self.id, queue, _port: PhantomData })
            .collect();
        let txs = (0..self.tx_queues)
            .map(|queue| TxQueue { port: self.id, queue, _port: PhantomData })
            .collect();
        (st, rxs, txs)
    }

    pub fn mac(&self) -> [u8; 6] {
        let mut mac = sys::rte_ether_addr::default();
        unsafe { sys::rte_eth_macaddr_get(self.id, &mut mac) };
        mac.addr_bytes
    }

    pub fn promiscuous(&self) -> Result<()> {
        check("rte_eth_promiscuous_enable", unsafe { sys::rte_eth_promiscuous_enable(self.id) })
    }

    pub fn rx<const N: usize>(&self, queue: u16, burst: &mut Burst<'e, N>) -> usize {
        let free = (N - burst.len) as u16;
        let n = unsafe {
            sys::rte_eth_rx_burst(self.id, queue, burst.raw_slots().add(burst.len), free)
        };
        burst.len += usize::from(n);
        n.into()
    }

    pub fn rx_cqe_peek<const N: usize>(
        &self,
        queue: u16,
        burst: &mut Burst<'e, N>,
        stats: Option<&mut CqePeekStats>,
        diag: bool,
    ) -> usize {
        let free = (N - burst.len) as u16;
        let st_ptr = stats.map_or(std::ptr::null_mut(), |s| s as *mut _);
        let n = unsafe {
            sys::dpdk_sys_cn10k_rx_burst_cqe_peek(
                self.id,
                queue,
                burst.raw_slots().add(burst.len),
                free,
                st_ptr,
                if diag { 1 } else { 0 },
            )
        };
        burst.len += usize::from(n);
        n.into()
    }

    pub fn rx_cqe_direct<const N: usize>(
        &self,
        queue: u16,
        burst: &mut Burst<'e, N>,
        stats: Option<&mut CqePeekStats>,
    ) -> usize {
        let free = (N - burst.len) as u16;
        let st_ptr = stats.map_or(std::ptr::null_mut(), |s| s as *mut _);
        let n = unsafe {
            sys::dpdk_sys_cn10k_rx_burst_cqe_direct(
                self.id,
                queue,
                burst.raw_slots().add(burst.len),
                free,
                st_ptr,
            )
        };
        burst.len += usize::from(n);
        n.into()
    }

    pub fn rx_cqe_direct_safe<const N: usize>(
        &self,
        queue: u16,
        burst: &mut Burst<'e, N>,
        stats: Option<&mut CqePeekStats>,
        full_clear: bool,
        drop_err: bool,
        watchdog_interval: u64,
    ) -> usize {
        let free = (N - burst.len) as u16;
        let st_ptr = stats.map_or(std::ptr::null_mut(), |s| s as *mut _);
        let mut flags = 0u32;
        if full_clear {
            flags |= 1;
        }
        if drop_err {
            flags |= 2;
        }
        let n = unsafe {
            sys::dpdk_sys_cn10k_rx_burst_cqe_direct_safe(
                self.id,
                queue,
                burst.raw_slots().add(burst.len),
                free,
                st_ptr,
                flags,
                watchdog_interval,
            )
        };
        burst.len += usize::from(n);
        n.into()
    }

    pub fn cn10k_rxq_info(&self, queue: u16) -> Option<Cn10kRxqInfo> {
        let mut info = Cn10kRxqInfo::default();
        let rc = unsafe { sys::dpdk_sys_cn10k_rxq_inspect(self.id, queue, &mut info) };
        if rc == 0 { Some(info) } else { None }
    }

    pub fn cn10k_cq_oracle(&self, queue: u16) -> Option<CqOracle> {
        let mut out = CqOracle::default();
        let rc = unsafe { sys::dpdk_sys_cn10k_cq_oracle_check(self.id, queue, &mut out) };
        if rc == 0 { Some(out) } else { None }
    }

    pub fn cn10k_cqe_direct_sync(&self, queue: u16) -> std::result::Result<CqOracle, i32> {
        let mut out = CqOracle::default();
        let rc = unsafe { sys::dpdk_sys_cn10k_cqe_direct_sync(self.id, queue, &mut out) };
        if rc == 0 { Ok(out) } else { Err(rc) }
    }

    pub fn cn10k_cq_read_slot(&self, queue: u16, slot_idx: u32) -> Option<[u64; 16]> {
        let mut words = [0u64; 16];
        let rc =
            unsafe { sys::dpdk_sys_cn10k_cq_read_slot(self.id, queue, slot_idx, words.as_mut_ptr()) };
        if rc == 0 { Some(words) } else { None }
    }

    pub fn cn10k_cq_write_slot(&self, queue: u16, slot_idx: u32, words: &[u64; 16]) -> bool {
        unsafe { sys::dpdk_sys_cn10k_cq_write_slot(self.id, queue, slot_idx, words.as_ptr()) == 0 }
    }

    pub fn cn10k_cq_trigger_door_err(&self, queue: u16) -> u64 {
        unsafe { sys::dpdk_sys_cn10k_cq_trigger_door_err(self.id, queue) }
    }

    pub fn rx_burst_mode_str(&self, queue: u16) -> String {
        let mut buf = [0 as c_char; 256];
        let rc = unsafe {
            sys::dpdk_sys_eth_rx_burst_mode_str(self.id, queue, buf.as_mut_ptr(), buf.len())
        };
        if rc != 0 {
            return format!("err({rc})");
        }
        let nul = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
        let bytes: Vec<u8> = buf[..nul].iter().map(|&c| c as u8).collect();
        String::from_utf8_lossy(&bytes).into_owned()
    }

    pub fn cn10k_cqe_peek_w0_w9_bench(&self, queue: u16, iters: u64) -> u64 {
        unsafe { sys::dpdk_sys_cn10k_cqe_peek_w0_w9_bench(self.id, queue, iters) }
    }

    pub fn cn10k_cqe_clear_bench(&self, queue: u16, iters: u64, full: bool) {
        unsafe {
            sys::dpdk_sys_cn10k_cqe_clear_bench(self.id, queue, iters, if full { 1 } else { 0 })
        };
    }

    pub fn tx<const N: usize>(&self, queue: u16, burst: &mut Burst<'e, N>) -> usize {
        tx_burst(self.id, queue, burst)
    }

    pub fn send(&self, queue: u16, pkt: Mbuf<'e>) -> Result<(), Mbuf<'e>> {
        let mut raw = pkt.into_raw();
        match unsafe { sys::rte_eth_tx_burst(self.id, queue, &mut raw, 1) } {
            1 => Ok(()),
            _ => Err(Mbuf { raw: NonNull::new(raw).unwrap(), _eal: PhantomData }),
        }
    }
}

impl Drop for Port<'_> {
    fn drop(&mut self) {
        unsafe { sys::rte_eth_dev_stop(self.id) };
    }
}

fn tx_burst<const N: usize>(port: u16, queue: u16, burst: &mut Burst<'_, N>) -> usize {
    let sent =
        usize::from(unsafe { sys::rte_eth_tx_burst(port, queue, burst.raw_slots(), burst.len as u16) });
    unsafe { ptr::copy(burst.raw_slots().add(sent), burst.raw_slots(), burst.len - sent) };
    burst.len -= sent;
    sent
}

/// Exclusive use of one RX queue of a started port, from any one lcore.
pub struct RxQueue<'p, 'e> {
    port: u16,
    queue: u16,
    _port: PhantomData<&'p Port<'e>>,
}

unsafe impl Send for RxQueue<'_, '_> {}

impl<'e> RxQueue<'_, 'e> {
    pub fn queue(&self) -> u16 {
        self.queue
    }

    pub fn queue_id(&self) -> u16 {
        self.queue
    }

    pub fn rx<const N: usize>(&mut self, burst: &mut Burst<'e, N>) -> usize {
        let free = (N - burst.len) as u16;
        let n = unsafe {
            sys::rte_eth_rx_burst(self.port, self.queue, burst.raw_slots().add(burst.len), free)
        };
        burst.len += usize::from(n);
        n.into()
    }
}

/// Exclusive use of one TX queue of a started port, from any one lcore.
pub struct TxQueue<'p, 'e> {
    port: u16,
    queue: u16,
    _port: PhantomData<&'p Port<'e>>,
}

unsafe impl Send for TxQueue<'_, '_> {}

impl<'e> TxQueue<'_, 'e> {
    pub fn queue(&self) -> u16 {
        self.queue
    }

    pub fn queue_id(&self) -> u16 {
        self.queue
    }

    pub fn tx<const N: usize>(&mut self, burst: &mut Burst<'e, N>) -> usize {
        tx_burst(self.port, self.queue, burst)
    }

    pub fn send(&mut self, pkt: Mbuf<'e>) -> Result<(), Mbuf<'e>> {
        let mut raw = pkt.into_raw();
        match unsafe { sys::rte_eth_tx_burst(self.port, self.queue, &mut raw, 1) } {
            1 => Ok(()),
            _ => Err(Mbuf { raw: NonNull::new(raw).unwrap(), _eal: PhantomData }),
        }
    }
}

/// Exclusive use of one `(RX, TX)` queue pair of a started port, from any one lcore.
pub struct QueuePair<'p, 'e> {
    port: u16,
    queue: u16,
    _port: PhantomData<&'p Port<'e>>,
}

unsafe impl Send for QueuePair<'_, '_> {}

impl<'e> QueuePair<'_, 'e> {
    pub fn queue(&self) -> u16 {
        self.queue
    }

    pub fn queue_id(&self) -> u16 {
        self.queue
    }

    pub fn rx<const N: usize>(&mut self, burst: &mut Burst<'e, N>) -> usize {
        let free = (N - burst.len) as u16;
        let n = unsafe {
            sys::rte_eth_rx_burst(self.port, self.queue, burst.raw_slots().add(burst.len), free)
        };
        burst.len += usize::from(n);
        n.into()
    }

    pub fn rx_cqe_peek<const N: usize>(
        &mut self,
        burst: &mut Burst<'e, N>,
        stats: Option<&mut CqePeekStats>,
        diag: bool,
    ) -> usize {
        let free = (N - burst.len) as u16;
        let st_ptr = stats.map_or(std::ptr::null_mut(), |s| s as *mut _);
        let n = unsafe {
            sys::dpdk_sys_cn10k_rx_burst_cqe_peek(
                self.port,
                self.queue,
                burst.raw_slots().add(burst.len),
                free,
                st_ptr,
                if diag { 1 } else { 0 },
            )
        };
        burst.len += usize::from(n);
        n.into()
    }

    pub fn rx_cqe_direct_safe<const N: usize>(
        &mut self,
        burst: &mut Burst<'e, N>,
        stats: Option<&mut CqePeekStats>,
        full_clear: bool,
        drop_err: bool,
        watchdog_interval: u64,
    ) -> usize {
        let free = (N - burst.len) as u16;
        let st_ptr = stats.map_or(std::ptr::null_mut(), |s| s as *mut _);
        let mut flags = 0u32;
        if full_clear {
            flags |= 1;
        }
        if drop_err {
            flags |= 2;
        }
        let n = unsafe {
            sys::dpdk_sys_cn10k_rx_burst_cqe_direct_safe(
                self.port,
                self.queue,
                burst.raw_slots().add(burst.len),
                free,
                st_ptr,
                flags,
                watchdog_interval,
            )
        };
        burst.len += usize::from(n);
        n.into()
    }

    pub fn cn10k_cqe_direct_sync(&mut self) -> std::result::Result<CqOracle, i32> {
        let mut out = CqOracle::default();
        let rc = unsafe { sys::dpdk_sys_cn10k_cqe_direct_sync(self.port, self.queue, &mut out) };
        if rc == 0 { Ok(out) } else { Err(rc) }
    }

    pub fn tx<const N: usize>(&mut self, burst: &mut Burst<'e, N>) -> usize {
        tx_burst(self.port, self.queue, burst)
    }

    pub fn send(&mut self, pkt: Mbuf<'e>) -> Result<(), Mbuf<'e>> {
        let mut raw = pkt.into_raw();
        match unsafe { sys::rte_eth_tx_burst(self.port, self.queue, &mut raw, 1) } {
            1 => Ok(()),
            _ => Err(Mbuf { raw: NonNull::new(raw).unwrap(), _eal: PhantomData }),
        }
    }
}

#[derive(Clone, Copy)]
pub struct PortStats<'p, 'e> {
    port: u16,
    _port: PhantomData<&'p Port<'e>>,
}

unsafe impl Send for PortStats<'_, '_> {}
unsafe impl Sync for PortStats<'_, '_> {}

impl PortStats<'_, '_> {
    pub fn stats(&self) -> Result<EthStats> {
        eth_stats_get(self.port)
    }

    pub fn stats_reset(&self) -> Result<()> {
        check("rte_eth_stats_reset", unsafe { sys::rte_eth_stats_reset(self.port) })
    }

    pub fn xstats(&self) -> Result<Vec<(String, u64)>> {
        eth_xstats_get(self.port)
    }

    pub fn xstats_reset(&self) -> Result<()> {
        check("rte_eth_xstats_reset", unsafe { sys::rte_eth_xstats_reset(self.port) })
    }
}

/// A PCIe function on the host side of PCIe core `core`. For cnxk DPI, `vf`
/// 0 is PF `pf` itself and host VF n is `vf = n + 1` (as DAO uses it).
#[derive(Clone, Copy, Debug)]
pub struct PcieFunction {
    pub core: u8,
    pub pf: u8,
    pub vf: u16,
}

/// Which way a [`HostDma`] copies.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HostDir {
    /// Local memory into host memory: posted PCIe writes.
    ToHost,
    /// Host memory into local memory: PCIe reads, answered by completions.
    FromHost,
}

/// A started dmadev with one or two vchans copying between local memory and the
/// memory of a PCIe host, acting as function `host`; stopped and closed on drop.
pub struct HostDma<'e> {
    dev: i16,
    _eal: PhantomData<&'e Eal>,
}

unsafe impl Send for HostDma<'_> {}

impl<'e> HostDma<'e> {
    pub fn start(
        _eal: &'e Eal,
        name: &str,
        nb_desc: u16,
        host: PcieFunction,
        dir: HostDir,
    ) -> Result<Self> {
        let cname = CString::new(name).expect("NUL in dmadev name");
        let dev = unsafe { sys::rte_dma_get_dev_id_by_name(cname.as_ptr()) };
        check("rte_dma_get_dev_id_by_name", dev)?;
        let to_host = dir == HostDir::ToHost;
        unsafe {
            let setup = sys::dpdk_sys_dma_setup_pcie(
                dev as i16,
                nb_desc,
                to_host,
                host.core,
                host.pf,
                host.vf,
            );
            check("rte_dma_configure/vchan_setup", setup)?;
            check("rte_dma_start", sys::rte_dma_start(dev as i16))?;
        }
        Ok(HostDma { dev: dev as i16, _eal: PhantomData })
    }

    pub fn start_bidir(
        _eal: &'e Eal,
        name: &str,
        nb_desc: u16,
        host: PcieFunction,
    ) -> Result<Self> {
        let cname = CString::new(name).expect("NUL in dmadev name");
        let dev = unsafe { sys::rte_dma_get_dev_id_by_name(cname.as_ptr()) };
        check("rte_dma_get_dev_id_by_name", dev)?;
        unsafe {
            let setup = sys::dpdk_sys_dma_setup_pcie_bidir(
                dev as i16,
                nb_desc,
                host.core,
                host.pf,
                host.vf,
            );
            check("rte_dma_configure/vchan_setup_bidir", setup)?;
            check("rte_dma_start", sys::rte_dma_start(dev as i16))?;
        }
        Ok(HostDma { dev: dev as i16, _eal: PhantomData })
    }

    pub fn copy_vchan(&mut self, vchan: u16, src: u64, dst: u64, len: u32) -> Result<bool> {
        const ENOSPC: c_int = 28;
        match unsafe { sys::rte_dma_copy(self.dev, vchan, src, dst, len, 0) } {
            r if r >= 0 => Ok(true),
            r if r == -ENOSPC => Ok(false),
            r => Err(Error { op: "rte_dma_copy", errno: -r }),
        }
    }

    pub fn submit_vchan(&mut self, vchan: u16) -> Result<()> {
        check("rte_dma_submit", unsafe { sys::rte_dma_submit(self.dev, vchan) })
    }

    pub fn completed_vchan(&mut self, vchan: u16, max: u16) -> (u16, bool) {
        let (mut last, mut failed) = (0u16, false);
        let n = unsafe { sys::rte_dma_completed(self.dev, vchan, max, &mut last, &mut failed) };
        (n, failed)
    }

    pub fn copy(&mut self, src: u64, dst: u64, len: u32) -> Result<bool> {
        self.copy_vchan(0, src, dst, len)
    }

    pub fn submit(&mut self) -> Result<()> {
        self.submit_vchan(0)
    }

    pub fn completed(&mut self, max: u16) -> (u16, bool) {
        self.completed_vchan(0, max)
    }
}

impl Drop for HostDma<'_> {
    fn drop(&mut self) {
        unsafe {
            sys::rte_dma_stop(self.dev);
            sys::rte_dma_close(self.dev);
        }
    }
}
