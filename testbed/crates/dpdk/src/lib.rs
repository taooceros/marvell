//! Minimal safe layer over `dpdk-sys`: EAL, mbuf pools, ports, owned mbufs,
//! fixed-size bursts, memzones and a DMA queue to a PCIe host.
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

/// A failed DPDK call: the operation and its errno.
#[derive(Debug)]
pub struct Error {
    op: &'static str,
    errno: c_int,
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
        // SAFETY: argv points into `args`, which outlives the call. EAL may
        // permute argv and stores the program name at argv[parsed].
        let parsed = unsafe { sys::rte_eal_init(argv.len() as c_int, argv.as_mut_ptr()) };
        if parsed < 0 {
            // SAFETY: plain read of rte_errno.
            return Err(Error { op: "rte_eal_init", errno: unsafe { sys::dpdk_sys_errno() } });
        }
        let rest = argv[parsed as usize..]
            .iter()
            // SAFETY: every entry still points into `args`.
            .map(|&p| unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned())
            .collect();
        Ok((Eal(PhantomData), rest))
    }

    /// Timestamp counter frequency for [`rdtsc`].
    pub fn tsc_hz(&self) -> u64 {
        // SAFETY: EAL is initialised.
        unsafe { sys::rte_get_tsc_hz() }
    }

    /// Lcores EAL was started with (`-l`), this main one included.
    pub fn lcores(&self) -> usize {
        // SAFETY: EAL is initialised.
        unsafe { sys::rte_lcore_count() as usize }
    }

    /// Run `f(i, inputs[i])` on every lcore at once: `i` = 0 on this (main)
    /// lcore, `1..` on the workers in `-l` order. Returns the results in that
    /// order after all have finished; a panic on any lcore is resumed here.
    /// Needs one input per lcore.
    pub fn on_each_lcore<S: Send, R: Send>(&self, inputs: Vec<S>, f: impl Fn(usize, S) -> R + Sync) -> Vec<R> {
        assert_eq!(inputs.len(), self.lcores(), "one input per lcore");
        struct Job<'f, S, R> {
            f: &'f (dyn Fn(usize, S) -> R + Sync),
            index: usize,
            input: Option<S>,
            out: Option<std::thread::Result<R>>,
        }
        unsafe extern "C" fn run<S, R>(arg: *mut c_void) -> c_int {
            // SAFETY: `arg` is a live Job that only this lcore touches until
            // the main lcore has waited for it.
            let job = unsafe { &mut *arg.cast::<Job<S, R>>() };
            let input = job.input.take().expect("each job runs once");
            job.out = Some(panic::catch_unwind(AssertUnwindSafe(|| (job.f)(job.index, input))));
            0
        }

        let mut jobs: Vec<Job<S, R>> =
            inputs.into_iter().enumerate().map(|(index, input)| Job { f: &f, index, input: Some(input), out: None }).collect();
        let jobs_ptr: Vec<*mut Job<S, R>> = jobs.iter_mut().map(|j| j as *mut _).collect();
        let mut launched = Vec::new();
        let mut failure = None;
        // SAFETY: plain lcore iteration; `u32::MAX` (-1) starts it.
        let mut lcore = unsafe { sys::rte_get_next_lcore(u32::MAX, 1, 0) };
        for &job in &jobs_ptr[1..] {
            // SAFETY: a constant.
            if lcore >= unsafe { sys::dpdk_sys_max_lcore() } {
                failure = Some(Error { op: "rte_get_next_lcore", errno: 19 }); // ENODEV
                break;
            }
            // SAFETY: `job` stays alive and is not touched here until this
            // worker has been waited for below.
            let rc = unsafe { sys::rte_eal_remote_launch(Some(run::<S, R>), job.cast(), lcore) };
            if rc != 0 {
                failure = Some(Error { op: "rte_eal_remote_launch", errno: -rc });
                break;
            }
            launched.push(lcore);
            // SAFETY: as above.
            lcore = unsafe { sys::rte_get_next_lcore(lcore, 1, 0) };
        }
        if failure.is_none() {
            // SAFETY: the main lcore's job, not shared with any worker.
            unsafe { run::<S, R>(jobs_ptr[0].cast()) };
        }
        for &w in &launched {
            // SAFETY: a worker we launched.
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
        // SAFETY: all borrowing objects are gone (enforced by lifetimes).
        unsafe { sys::rte_eal_cleanup() };
    }
}

/// Read the CPU timestamp counter (`rte_rdtsc`).
pub fn rdtsc() -> u64 {
    // SAFETY: reads a CPU counter.
    unsafe { sys::rte_rdtsc() }
}

/// Read the timestamp counter only after every earlier load has completed.
/// Plain RDTSC may execute before an outstanding load (an MMIO read of a
/// device counter, a cache miss on a DMA'd line), which makes an arrival
/// timestamp too early. RDTSCP waits for all earlier instructions and loads
/// (same effect as LFENCE;RDTSC, measured equal). On ARM64, DMB ISH + ISB
/// prevents MRS CNTVCT_EL0 from executing before earlier loads complete.
pub fn rdtsc_after_loads() -> u64 {
    #[cfg(target_arch = "x86_64")]
    {
        let mut aux = 0;
        // SAFETY: RDTSCP only reads the TSC and IA32_TSC_AUX.
        unsafe { std::arch::x86_64::__rdtscp(&mut aux) }
    }
    #[cfg(target_arch = "aarch64")]
    {
        std::sync::atomic::fence(std::sync::atomic::Ordering::SeqCst);
        // SAFETY: ISB orders the subsequent MRS CNTVCT_EL0 after the fence.
        unsafe { std::arch::asm!("isb", options(nostack, preserves_flags)) };
        rdtsc()
    }
    #[cfg(not(any(target_arch = "x86_64", target_arch = "aarch64")))]
    {
        std::sync::atomic::fence(std::sync::atomic::Ordering::SeqCst);
        rdtsc()
    }
}

/// A pool of packet buffers with DPDK's default data room.
pub struct Mempool<'e> {
    raw: NonNull<sys::rte_mempool>,
    _eal: PhantomData<&'e Eal>,
}

// SAFETY: DPDK mempools are multi-producer/multi-consumer; lcores may allocate
// from and free into one concurrently.
unsafe impl Send for Mempool<'_> {}
unsafe impl Sync for Mempool<'_> {}

impl<'e> Mempool<'e> {
    pub fn new(_eal: &'e Eal, name: &str, size: u32, socket: i32) -> Result<Self> {
        let name = CString::new(name).expect("NUL in pool name");
        // SAFETY: valid C string; EAL is initialised.
        let raw = unsafe {
            sys::rte_pktmbuf_pool_create(
                name.as_ptr(),
                size,
                256,
                0,
                sys::dpdk_sys_mbuf_default_buf_size(),
                socket,
            )
        };
        let errno = || unsafe { sys::dpdk_sys_errno() };
        let raw = NonNull::new(raw).ok_or_else(|| Error { op: "rte_pktmbuf_pool_create", errno: errno() })?;
        Ok(Mempool { raw, _eal: PhantomData })
    }

    pub fn alloc(&self) -> Option<Mbuf<'e>> {
        // SAFETY: the pool is alive for 'e.
        NonNull::new(unsafe { sys::rte_pktmbuf_alloc(self.raw.as_ptr()) })
            .map(|raw| Mbuf { raw, _eal: PhantomData })
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

impl Mbuf<'_> {
    pub fn data(&self) -> &[u8] {
        // SAFETY: data/data_len describe the first segment, owned by self.
        unsafe {
            let m = self.raw.as_ptr();
            slice::from_raw_parts(sys::dpdk_sys_mbuf_data(m), sys::dpdk_sys_mbuf_data_len(m).into())
        }
    }

    pub fn data_mut(&mut self) -> &mut [u8] {
        // SAFETY: as above, and &mut self gives exclusive access.
        unsafe {
            let m = self.raw.as_ptr();
            slice::from_raw_parts_mut(sys::dpdk_sys_mbuf_data(m), sys::dpdk_sys_mbuf_data_len(m).into())
        }
    }

    /// Grow the packet by `len` bytes at the tail and return them, or `None`
    /// if the buffer has no room.
    pub fn append(&mut self, len: u16) -> Option<&mut [u8]> {
        // SAFETY: on success DPDK returns `len` writable bytes inside the buffer.
        let tail = unsafe { sys::rte_pktmbuf_append(self.raw.as_ptr(), len) };
        NonNull::new(tail.cast::<u8>())
            .map(|p| unsafe { slice::from_raw_parts_mut(p.as_ptr(), len.into()) })
    }

    fn into_raw(self) -> *mut sys::rte_mbuf {
        ManuallyDrop::new(self).raw.as_ptr()
    }
}

impl Drop for Mbuf<'_> {
    fn drop(&mut self) {
        // SAFETY: self owns the mbuf.
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

    /// Add a packet to the burst; gives it back if the burst is full.
    pub fn push(&mut self, pkt: Mbuf<'e>) -> Result<(), Mbuf<'e>> {
        if self.is_full() {
            return Err(pkt);
        }
        self.pkts[self.len].write(pkt);
        self.len += 1;
        Ok(())
    }

    pub fn as_mut_slice(&mut self) -> &mut [Mbuf<'e>] {
        // SAFETY: the first `len` slots are initialised.
        unsafe { slice::from_raw_parts_mut(self.pkts.as_mut_ptr().cast(), self.len) }
    }

    /// Free every packet still held.
    pub fn clear(&mut self) {
        if self.len > 0 {
            // SAFETY: the first `len` slots hold owned mbufs (Mbuf is a
            // transparent pointer); ownership passes to DPDK.
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

// SAFETY: the handle is only read (address, IOVA); accesses to the memory
// itself go through raw pointers whose users synchronise them.
unsafe impl Send for Memzone<'_> {}
unsafe impl Sync for Memzone<'_> {}

impl<'e> Memzone<'e> {
    pub fn reserve_contiguous(_eal: &'e Eal, name: &str, len: usize, socket: i32, align: u32) -> Result<Self> {
        let name = CString::new(name).expect("NUL in memzone name");
        // SAFETY: valid C string; EAL is initialised.
        let raw = unsafe { sys::dpdk_sys_memzone_reserve_contig(name.as_ptr(), len, socket, align) };
        let errno = || unsafe { sys::dpdk_sys_errno() };
        let raw = NonNull::new(raw.cast_mut()).ok_or_else(|| Error { op: "rte_memzone_reserve_aligned", errno: errno() })?;
        Ok(Memzone { raw, _eal: PhantomData })
    }

    pub fn as_ptr(&self) -> *mut u8 {
        // SAFETY: the memzone is alive.
        unsafe { sys::dpdk_sys_memzone_addr(self.raw.as_ptr()) }.cast()
    }

    /// Device-visible (IOVA) address of the first byte.
    pub fn iova(&self) -> u64 {
        // SAFETY: the memzone is alive.
        unsafe { sys::dpdk_sys_memzone_iova(self.raw.as_ptr()) }
    }

    /// Physical address of the first byte (needs root: DPDK reads /proc/self/pagemap).
    pub fn phys_addr(&self) -> Option<u64> {
        // SAFETY: the address lies inside a live memzone.
        let pa = unsafe { sys::rte_mem_virt2phy(self.as_ptr().cast()) };
        (pa != u64::MAX).then_some(pa)
    }
}

impl Drop for Memzone<'_> {
    fn drop(&mut self) {
        // SAFETY: reserved by us, freed once.
        unsafe { sys::rte_memzone_free(self.raw.as_ptr()) };
    }
}

/// A started ethdev port; stopped on drop. Not `Sync`: DPDK queues are not
/// thread-safe, so lcores other than the owner get [`TxQueue`] handles.
pub struct Port<'e> {
    id: u16,
    queues: u16,
    _eal: PhantomData<&'e Eal>,
    _not_sync: PhantomData<Cell<()>>,
}

impl<'e> Port<'e> {
    /// NUMA node of port `id` (allocate its pool there).
    pub fn socket_of(_eal: &Eal, id: u16) -> i32 {
        // SAFETY: plain query.
        unsafe { sys::rte_eth_dev_socket_id(id) }
    }

    /// Configure port `id` with `queues` RX and TX queues of `desc` descriptors
    /// each, receiving into `pool`, and start it.
    pub fn start(eal: &'e Eal, id: u16, pool: &Mempool<'e>, queues: u16, desc: u16) -> Result<Self> {
        let socket = Self::socket_of(eal, id) as u32;
        let conf = sys::rte_eth_conf::default();
        // SAFETY: valid port/queue ids and configuration pointers for the calls.
        unsafe {
            check("rte_eth_dev_configure", sys::rte_eth_dev_configure(id, queues, queues, &conf))?;
            for q in 0..queues {
                let rx = sys::rte_eth_rx_queue_setup(id, q, desc, socket, std::ptr::null(), pool.raw.as_ptr());
                check("rte_eth_rx_queue_setup", rx)?;
                let tx = sys::rte_eth_tx_queue_setup(id, q, desc, socket, std::ptr::null());
                check("rte_eth_tx_queue_setup", tx)?;
            }
            check("rte_eth_dev_start", sys::rte_eth_dev_start(id))?;
        }
        Ok(Port { id, queues, _eal: PhantomData, _not_sync: PhantomData })
    }

    /// One handle per TX queue, each usable from (and movable to) one lcore;
    /// the port cannot transmit itself while they exist.
    pub fn tx_queues(&mut self) -> Vec<TxQueue<'_, 'e>> {
        (0..self.queues).map(|queue| TxQueue { port: self.id, queue, _port: PhantomData }).collect()
    }

    pub fn mac(&self) -> [u8; 6] {
        let mut mac = sys::rte_ether_addr::default();
        // SAFETY: valid port id and output pointer.
        unsafe { sys::rte_eth_macaddr_get(self.id, &mut mac) };
        mac.addr_bytes
    }

    pub fn promiscuous(&self) -> Result<()> {
        // SAFETY: valid port id.
        check("rte_eth_promiscuous_enable", unsafe { sys::rte_eth_promiscuous_enable(self.id) })
    }

    /// Poll `queue` into the free slots of `burst`; returns how many arrived.
    pub fn rx<const N: usize>(&self, queue: u16, burst: &mut Burst<'e, N>) -> usize {
        let free = (N - burst.len) as u16;
        // SAFETY: the slots after `len` are free; DPDK fills up to `free`.
        let n = unsafe { sys::rte_eth_rx_burst(self.id, queue, burst.raw_slots().add(burst.len), free) };
        burst.len += usize::from(n);
        n.into()
    }

    /// Transmit all of `burst` with one call (one doorbell). Packets the ring
    /// could not take stay in `burst`; returns how many were sent.
    pub fn tx<const N: usize>(&self, queue: u16, burst: &mut Burst<'e, N>) -> usize {
        tx_burst(self.id, queue, burst)
    }

    /// Transmit one packet with its own doorbell (batch = 1). Gives the packet
    /// back if the ring is full.
    pub fn send(&self, queue: u16, pkt: Mbuf<'e>) -> Result<(), Mbuf<'e>> {
        let mut raw = pkt.into_raw();
        // SAFETY: one owned mbuf; on success DPDK takes it.
        match unsafe { sys::rte_eth_tx_burst(self.id, queue, &mut raw, 1) } {
            1 => Ok(()),
            _ => Err(Mbuf { raw: NonNull::new(raw).unwrap(), _eal: PhantomData }),
        }
    }
}

impl Drop for Port<'_> {
    fn drop(&mut self) {
        // SAFETY: valid, started port.
        unsafe { sys::rte_eth_dev_stop(self.id) };
    }
}

/// Transmit all of `burst` on `port`/`queue` with one doorbell; the unsent
/// packets stay in `burst`.
fn tx_burst<const N: usize>(port: u16, queue: u16, burst: &mut Burst<'_, N>) -> usize {
    // SAFETY: the first `len` slots hold owned mbufs; DPDK takes the first `sent`.
    let sent = usize::from(unsafe { sys::rte_eth_tx_burst(port, queue, burst.raw_slots(), burst.len as u16) });
    // SAFETY: moves the unsent owned mbufs to the front; slots past the new
    // `len` are treated as uninitialised again.
    unsafe { ptr::copy(burst.raw_slots().add(sent), burst.raw_slots(), burst.len - sent) };
    burst.len -= sent;
    sent
}

/// Exclusive use of one TX queue of a started port, from any one lcore.
pub struct TxQueue<'p, 'e> {
    port: u16,
    queue: u16,
    _port: PhantomData<&'p mut Port<'e>>,
}

impl<'e> TxQueue<'_, 'e> {
    /// Transmit all of `burst` with one doorbell. Packets the ring could not
    /// take stay in `burst`; returns how many were sent.
    pub fn tx<const N: usize>(&mut self, burst: &mut Burst<'e, N>) -> usize {
        tx_burst(self.port, self.queue, burst)
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

/// A started dmadev with one vchan that copies between local memory and the
/// memory of a PCIe host, acting as function `host` (whose IOMMU domain
/// translates the host addresses); stopped and closed on drop.
pub struct HostDma<'e> {
    dev: i16,
    _eal: PhantomData<&'e Eal>,
}

impl<'e> HostDma<'e> {
    /// `name` is the dmadev name, e.g. the DPI VF's PCI address.
    pub fn start(_eal: &'e Eal, name: &str, nb_desc: u16, host: PcieFunction, dir: HostDir) -> Result<Self> {
        let cname = CString::new(name).expect("NUL in dmadev name");
        // SAFETY: valid C string; EAL is initialised.
        let dev = unsafe { sys::rte_dma_get_dev_id_by_name(cname.as_ptr()) };
        check("rte_dma_get_dev_id_by_name", dev)?;
        let to_host = dir == HostDir::ToHost;
        // SAFETY: valid dmadev id.
        unsafe {
            let setup = sys::dpdk_sys_dma_setup_pcie(dev as i16, nb_desc, to_host, host.core, host.pf, host.vf);
            check("rte_dma_configure/vchan_setup", setup)?;
            check("rte_dma_start", sys::rte_dma_start(dev as i16))?;
        }
        Ok(HostDma { dev: dev as i16, _eal: PhantomData })
    }

    /// Enqueue a copy of `len` bytes from IOVA `src` to IOVA `dst` (the host
    /// side is `dst` for [`HostDir::ToHost`], `src` for [`HostDir::FromHost`])
    /// without ringing the doorbell. Returns false if the ring is full.
    pub fn copy(&mut self, src: u64, dst: u64, len: u32) -> Result<bool> {
        const ENOSPC: c_int = 28; // Linux
        // SAFETY: started device, vchan 0. The addresses are only used by the
        // device; the caller keeps the source alive until the copy completes.
        match unsafe { sys::rte_dma_copy(self.dev, 0, src, dst, len, 0) } {
            r if r >= 0 => Ok(true),
            r if r == -ENOSPC => Ok(false),
            r => Err(Error { op: "rte_dma_copy", errno: -r }),
        }
    }

    /// Ring the doorbell for every copy enqueued since the last submit.
    pub fn submit(&mut self) -> Result<()> {
        // SAFETY: started device, vchan 0.
        check("rte_dma_submit", unsafe { sys::rte_dma_submit(self.dev, 0) })
    }

    /// Reap up to `max` finished copies, in order: `(count, any failed)`.
    pub fn completed(&mut self, max: u16) -> (u16, bool) {
        let (mut last, mut failed) = (0u16, false);
        // SAFETY: started device, vchan 0, valid output pointers.
        let n = unsafe { sys::rte_dma_completed(self.dev, 0, max, &mut last, &mut failed) };
        (n, failed)
    }
}

impl Drop for HostDma<'_> {
    fn drop(&mut self) {
        // SAFETY: valid, started device.
        unsafe {
            sys::rte_dma_stop(self.dev);
            sys::rte_dma_close(self.dev);
        }
    }
}
