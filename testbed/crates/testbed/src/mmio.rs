//! Raw MMIO: mapping a file range (a PCI BAR through sysfs, or `/dev/mem`),
//! MOVDIR64B on the host, and on the card the CN10K PEM windows: `BAR4_INDEX`
//! (host -> card memory) and the outbound region table (card core -> host).

use std::ffi::CString;
use std::io;
use std::ptr::NonNull;

/// A shared read/write mapping of `len` bytes of a file at `offset`;
/// unmapped on drop.
pub struct Mapping {
    ptr: NonNull<u8>,
    len: usize,
}

impl Mapping {
    /// `sync` opens with `O_SYNC`, which makes `/dev/mem` map memory uncached.
    pub fn new(path: &str, offset: u64, len: usize, sync: bool) -> io::Result<Self> {
        let cpath = CString::new(path).expect("NUL in path");
        let flags = libc::O_RDWR | if sync { libc::O_SYNC } else { 0 };
        // SAFETY: plain syscalls on a valid C string; the fd is closed once
        // mapped (the mapping keeps its own reference).
        unsafe {
            let fd = libc::open(cpath.as_ptr(), flags);
            if fd < 0 {
                return Err(io::Error::last_os_error());
            }
            let prot = libc::PROT_READ | libc::PROT_WRITE;
            let p = libc::mmap(std::ptr::null_mut(), len, prot, libc::MAP_SHARED, fd, offset as libc::off_t);
            let err = io::Error::last_os_error();
            libc::close(fd);
            if p == libc::MAP_FAILED {
                return Err(err);
            }
            Ok(Mapping { ptr: NonNull::new_unchecked(p.cast()), len })
        }
    }

    pub fn as_ptr(&self) -> *mut u8 {
        self.ptr.as_ptr()
    }

    /// One 64-bit load at `offset` (a single access: device registers must
    /// not be read piecewise).
    pub fn read_u64(&self, offset: usize) -> u64 {
        assert!(offset.is_multiple_of(8) && offset + 8 <= self.len);
        // SAFETY: aligned and inside the mapping.
        unsafe { self.ptr.as_ptr().add(offset).cast::<u64>().read_volatile() }
    }

    /// One 64-bit store at `offset`.
    pub fn write_u64(&self, offset: usize, value: u64) {
        assert!(offset.is_multiple_of(8) && offset + 8 <= self.len);
        // SAFETY: aligned and inside the mapping.
        unsafe { self.ptr.as_ptr().add(offset).cast::<u64>().write_volatile(value) }
    }
}

impl Drop for Mapping {
    fn drop(&mut self) {
        // SAFETY: mapped by `new` with this length.
        unsafe { libc::munmap(self.ptr.as_ptr().cast(), self.len) };
    }
}

// SAFETY: a mapping is an address range; moving it to another thread is fine
// (it is not `Sync`: concurrent use would need the caller's protocol).
unsafe impl Send for Mapping {}

#[cfg(target_arch = "x86_64")]
pub use x86::*;

#[cfg(target_arch = "x86_64")]
mod x86 {
    use std::arch::asm;
    use std::arch::x86_64::{__cpuid_count, _mm_sfence};

    /// CPUID.(EAX=7, ECX=0):ECX bit 28.
    pub fn has_movdir64b() -> bool {
        #[allow(unused_unsafe)]
        let ecx = unsafe { __cpuid_count(7, 0) }.ecx;
        ecx & (1 << 28) != 0
    }

    /// Copy 64 bytes from `src` to `dst` as one direct store: 64-byte write
    /// atomicity, not cached, weakly ordered with other stores (no fence), and
    /// independent of the destination's memory type.
    ///
    /// # Safety
    /// `dst` must be 64-byte aligned and writable for 64 bytes, and the CPU
    /// must support MOVDIR64B.
    pub unsafe fn movdir64b<T>(dst: *mut u8, src: &T) {
        const { assert!(size_of::<T>() == 64 && align_of::<T>() == 64) };
        debug_assert!((dst as usize).is_multiple_of(64));
        // SAFETY: per the caller; reads 64 bytes of `src`.
        unsafe { asm!("movdir64b {dst}, [{src}]", dst = in(reg) dst, src = in(reg) src, options(nostack, preserves_flags)) };
    }

    pub fn sfence() {
        // SAFETY: a fence has no preconditions.
        unsafe { _mm_sfence() };
    }

    impl super::Mapping {
        /// One 16-byte load (MOVDQA) at `offset`, as two native-endian words.
        pub fn read_16(&self, offset: usize) -> [u64; 2] {
            assert!(offset.is_multiple_of(16) && offset + 16 <= self.len);
            let v: std::arch::x86_64::__m128i;
            // SAFETY: aligned and inside the mapping; a plain load.
            unsafe {
                asm!("movdqa {v}, [{p}]", v = out(xmm_reg) v, p = in(reg) self.as_ptr().add(offset),
                     options(nostack, preserves_flags, readonly));
                std::mem::transmute::<std::arch::x86_64::__m128i, [u64; 2]>(v)
            }
        }
    }
}

#[cfg(target_arch = "aarch64")]
pub use card::*;

#[cfg(target_arch = "aarch64")]
mod card {
    use std::io;

    use super::Mapping;

    /// PEM0 CSR block and its `BAR4_INDEX(0..15)` array: entry `i` maps host
    /// BAR4 offset `i * 4 MiB` to card physical `(entry >> 4) << 22`, bit 0 = valid.
    const PEM0_CSR: u64 = 0x8e00_0000_0000;
    const BAR4_INDEX: usize = 0x700;
    pub const BAR4_SLOTS: usize = 16;
    pub const BAR4_SLOT_BYTES: usize = 4 << 20;
    /// Where firmware (SDK13.26.10, EP mode) points slot `i`: card physical
    /// `0xbfa000000 + i * 4 MiB`, the BAR4 space of SDP PF0 (slots 0-7; slot 7
    /// = 0x2fef1, the octep control mailbox) and PF1 (slots 8-15; slot 15 =
    /// 0x2ff71).
    pub const fn bar4_firmware_entry(slot: usize) -> u64 {
        ((0xb_fa00_0000 >> 22) + slot as u64) << 4 | 1
    }

    /// One host BAR4 slot pointed at memory we own; the previous mapping is
    /// restored on drop.
    pub struct Bar4Window {
        csr: Mapping,
        slot: usize,
        old: u64,
    }

    impl Bar4Window {
        /// Point host BAR4 slot `slot` at card physical address `pa` (4 MiB aligned).
        /// Needs root (`/dev/mem`).
        pub fn claim(slot: usize, pa: u64) -> io::Result<Self> {
            assert!(slot < BAR4_SLOTS && pa.is_multiple_of(BAR4_SLOT_BYTES as u64));
            let csr = Mapping::new("/dev/mem", PEM0_CSR, 0x10000, true)?;
            let reg = BAR4_INDEX + 8 * slot;
            let old = csr.read_u64(reg);
            let new = (pa >> 22) << 4 | 1;
            csr.write_u64(reg, new);
            let now = csr.read_u64(reg);
            if now != new {
                csr.write_u64(reg, old);
                return Err(io::Error::other(format!("BAR4_INDEX{slot} reads {now:#x} after writing {new:#x}")));
            }
            Ok(Bar4Window { csr, slot, old })
        }

        pub fn previous(&self) -> u64 {
            self.old
        }
    }

    impl Drop for Bar4Window {
        fn drop(&mut self) {
            self.csr.write_u64(BAR4_INDEX + 8 * self.slot, self.old);
        }
    }

    /// Put back what an `inline_reflector` that died without its cleanup
    /// (`kill -9`) left behind: BAR4 slot `slot` to the firmware's entry, and
    /// the outbound regions `--store` claims (the top [`STORE_REGIONS`]) to
    /// zero, their EP-mode value. Single 64-bit accesses of those registers
    /// only, each write read back. Returns a line per register it changed.
    /// Only while no reflector runs: it would pull the slot from under one.
    pub fn restore_defaults(slot: usize) -> io::Result<Vec<String>> {
        assert!(slot < BAR4_SLOTS);
        let csr = Mapping::new("/dev/mem", PEM0_CSR, 0x10000, true)?;
        let mut changed = Vec::new();
        let mut put = |name: String, reg: usize, value: u64| {
            let was = csr.read_u64(reg);
            if was == value {
                return Ok(());
            }
            csr.write_u64(reg, value);
            let now = csr.read_u64(reg);
            if now != value {
                return Err(io::Error::other(format!("{name} reads {now:#x} after writing {value:#x}")));
            }
            changed.push(format!("{name} {was:#x} -> {now:#x}"));
            Ok(())
        };
        put(format!("BAR4_INDEX{slot}"), BAR4_INDEX + 8 * slot, bar4_firmware_entry(slot))?;
        let geometry = csr.read_u64(CONST_ACC);
        if geometry & 0xffff_ffff != (31 << 16 | OUTBOUND_REGIONS as u64) {
            return Err(io::Error::other(format!("PEM0_CONST_ACC {geometry:#x}: not 256 regions of 2 GiB")));
        }
        for index in OUTBOUND_REGIONS - STORE_REGIONS..OUTBOUND_REGIONS {
            put(format!("PEM0_REG_NORM_ACC{index}"), REG_NORM_ACC + 16 * index, 0)?;
        }
        Ok(changed)
    }

    /// PEM0's outbound region table `PEM()_REG_NORM()_ACC(0..255)`: a core
    /// store to `OUTBOUND_BASE + i * 2 GiB + off` leaves PEM0 as a PCIe memory
    /// write to bus address `entry.BA << 31 | off`, issued as the function the
    /// entry names. Offsets come from the CN10K boot firmware (which fills the
    /// table in RC mode), field layout from the CN9 BDK headers in coreboot;
    /// in EP mode firmware leaves every entry zero.
    const REG_NORM_ACC: usize = 0x4000;
    const CONST_ACC: usize = 0x218;
    const OUTBOUND_BASE: u64 = 0x8800_0000_0000;
    pub const OUTBOUND_REGIONS: usize = 256;
    pub const OUTBOUND_REGION_BYTES: u64 = 1 << 31;
    /// Outbound regions `inline_reflector --store` may claim, one per core
    /// from the top: 248-255, the ones `pem_wc` maps.
    pub const STORE_REGIONS: usize = 8;

    /// One outbound region of PEM0, owned for writes to the host; the entry's
    /// previous value is restored on drop. Only ever store through it: a load
    /// the host does not complete raises an SError on the card.
    pub struct OutboundWindow {
        csr: Mapping,
        /// `None` until a write-combining region first targets the host.
        region: Option<Mapping>,
        wc: bool,
        index: usize,
        old: u64,
        /// The entry's function and attribute bits, without the bus address.
        attrs: u64,
    }

    impl OutboundWindow {
        /// Claim entry `index`, issuing writes as host function `vf` of PF
        /// `pf` (DPI numbering: 0 = the PF itself, n = its VF n-1). The region
        /// targets bus address 0 until [`OutboundWindow::retarget`]. With `wc`
        /// it is mapped write-combining (Normal Non-cacheable, through the
        /// `pem_wc` card kernel module) so the core may gather stores into
        /// large writes; otherwise Device-nGnRnE through /dev/mem, where every
        /// store leaves as its own <= 16 B write. Needs root.
        pub fn claim(index: usize, pf: u8, vf: u16, wc: bool) -> io::Result<Self> {
            assert!(index < OUTBOUND_REGIONS && pf < 16 && vf <= 256);
            let csr = Mapping::new("/dev/mem", PEM0_CSR, 0x10000, true)?;
            let geometry = csr.read_u64(CONST_ACC);
            if geometry & 0xffff_ffff != (31 << 16 | OUTBOUND_REGIONS as u64) {
                return Err(io::Error::other(format!("PEM0_CONST_ACC {geometry:#x}: not 256 regions of 2 GiB")));
            }
            // Normal memory may be read speculatively: a write-combining
            // region is mapped only once it targets host memory (retarget).
            let region = (!wc)
                .then(|| {
                    let base = OUTBOUND_BASE + index as u64 * OUTBOUND_REGION_BYTES;
                    Mapping::new("/dev/mem", base, OUTBOUND_REGION_BYTES as usize, true)
                })
                .transpose()?;
            // CTYPE 0 (memory), writes strictly ordered and snooped, no read or
            // write merging: merged writes were measured to land torn and out
            // of order (stale 8 B words even under a sequence number in every
            // 16 B part), and merging gave no lower latency.
            const RNMERGE: u64 = 1 << 41;
            const WNMERGE: u64 = 1 << 42;
            const VF_ACTIVE: u64 = 1 << 53;
            let function = match vf {
                0 => u64::from(pf) << 46,
                n => u64::from(pf) << 46 | VF_ACTIVE | u64::from(n - 1) << 54,
            };
            let reg = REG_NORM_ACC + 16 * index;
            let old = csr.read_u64(reg);
            let mut window = OutboundWindow { csr, region, wc, index, old, attrs: function | RNMERGE | WNMERGE };
            window.retarget(0)?;
            Ok(window)
        }

        /// Point the region at host bus addresses `[base, base + 2 GiB)`.
        pub fn retarget(&mut self, base: u64) -> io::Result<()> {
            assert!(base.is_multiple_of(OUTBOUND_REGION_BYTES));
            let reg = REG_NORM_ACC + 16 * self.index;
            let new = self.attrs | base >> 31;
            self.csr.write_u64(reg, new);
            let now = self.csr.read_u64(reg); // also orders the write before later stores
            if now != new {
                return Err(io::Error::other(format!("PEM0_REG_NORM_ACC{} reads {now:#x} after writing {new:#x}", self.index)));
            }
            if self.wc && self.region.is_none() && base != 0 {
                let offset = self.index as u64 * OUTBOUND_REGION_BYTES;
                self.region = Some(Mapping::new("/dev/pem_wc", offset, OUTBOUND_REGION_BYTES as usize, false)?);
            }
            Ok(())
        }

        /// Start of the region in this process (device memory: store only).
        pub fn as_ptr(&self) -> *mut u8 {
            self.region.as_ref().expect("retarget a write-combining region to the host before storing").as_ptr()
        }

        pub fn previous(&self) -> u64 {
            self.old
        }
    }

    impl Drop for OutboundWindow {
        fn drop(&mut self) {
            self.region = None; // unmapped before the entry changes
            self.csr.write_u64(REG_NORM_ACC + 16 * self.index, self.old);
        }
    }

    /// Store 64 bytes to device memory as four 16-byte stores, the first 16
    /// bytes last: in-order posted writes then make the head visible only
    /// after the rest.
    ///
    /// # Safety
    /// `dst` is 16 B aligned and points to 64 writable bytes of device memory.
    pub unsafe fn store64_head_last(dst: *mut u8, words: &[u64; 8]) {
        // SAFETY: per the caller; plain stores.
        unsafe {
            std::arch::asm!(
                "stp {w2}, {w3}, [{p}, #16]",
                "stp {w4}, {w5}, [{p}, #32]",
                "stp {w6}, {w7}, [{p}, #48]",
                "stp {w0}, {w1}, [{p}]",
                p = in(reg) dst,
                w0 = in(reg) words[0], w1 = in(reg) words[1], w2 = in(reg) words[2], w3 = in(reg) words[3],
                w4 = in(reg) words[4], w5 = in(reg) words[5], w6 = in(reg) words[6], w7 = in(reg) words[7],
                options(nostack, preserves_flags),
            );
        }
    }

    /// Store 64 bytes to write-combining device memory as four contiguous
    /// 16 B `STP`s in address order, so the core gathers them into one 64 B
    /// write. Measured on CN106 (Neoverse N2) into the host, host IIO counters
    /// for write size: this form gave 100% full-line writes and the highest
    /// rate; with a `DGH` after it, also 100% but 25% slower on one core;
    /// eight `STR`s + `DGH` (Linux's `__iowrite64_copy` pattern) only 13-48%
    /// full lines. W=1 latency was the same for all. Gathering is still
    /// opportunistic: a block can leave in pieces, in any order (a few per
    /// 10 M), so the reader must check every part.
    ///
    /// # Safety
    /// `dst` is 64 B aligned and points to 64 writable bytes of
    /// write-combining device memory.
    pub unsafe fn store64_gathered(dst: *mut u8, words: &[u64; 8]) {
        // SAFETY: per the caller; plain stores.
        unsafe {
            std::arch::asm!(
                "stp {w0}, {w1}, [{p}]", "stp {w2}, {w3}, [{p}, #16]",
                "stp {w4}, {w5}, [{p}, #32]", "stp {w6}, {w7}, [{p}, #48]",
                p = in(reg) dst,
                w0 = in(reg) words[0], w1 = in(reg) words[1], w2 = in(reg) words[2], w3 = in(reg) words[3],
                w4 = in(reg) words[4], w5 = in(reg) words[5], w6 = in(reg) words[6], w7 = in(reg) words[7],
                options(nostack, preserves_flags),
            );
        }
    }
}
