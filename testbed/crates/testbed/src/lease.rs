//! Who may use BAR4 slot [`SLOT`]: a lease the card's `inline_reflector`
//! grants a host inline tool over TCP on the control path (host `ens1` ⇄ card
//! `sdp1-0`, the path ssh uses), never through BAR4 itself. A host maps the
//! slot only after the reflector that owns it has confirmed it for this run,
//! and stops using it once the reflector takes it back.
//!
//! 1. The host connects to the reflector's control address and sends HELLO
//!    with a random per-run nonce.
//! 2. The reflector has already pointed the slot at its own buffer and
//!    written [`READY`] there. It stores the nonce at [`NONCE_OFFSET`] of that
//!    buffer and answers READY with the same nonce (BUSY while another host
//!    holds the lease).
//! 3. The host maps the slot and, before its first write, checks the READY
//!    cookie and the nonce through it ([`Lease::map_slot`]). A READY left in
//!    the buffer of a reflector that was killed, or an answer meant for
//!    another run, carries another nonce.
//! 4. The connection stays open for the whole run. An exiting reflector sends
//!    REVOKE and waits for the host to stop and close the connection before
//!    it restores the slot, however long that takes (a host that is stopped,
//!    e.g. by SIGSTOP, may not have seen the REVOKE yet); the kernel closes
//!    the socket of a reflector that dies, even by `kill -9`. Either way the
//!    host sees the lease end ([`Lease::check`]) and stops touching the slot.
//!
//! Messages are records of eight little-endian u64 words: magic, protocol
//! version, nonce, then per-message fields.

use std::io::{self, Read, Write};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
use std::ops::Deref;
use std::os::fd::AsRawFd;
use std::sync::Arc;
use std::sync::atomic::{AtomicU8, AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use crate::inline::{NONCE_OFFSET, READY, READY_OFFSET, SLOT, SLOT_BYTES};
use crate::mmio::Mapping;

/// The reflector's control address: card `sdp1-0`.
pub const DEFAULT_ADDR: &str = "10.10.0.2:7526";
/// How long a host waits for the connection and for READY.
pub const TIMEOUT: Duration = Duration::from_secs(2);
/// How often an exiting reflector reports that the holder has not let go.
const WAIT_REPORT: Duration = Duration::from_secs(2);

const VERSION: u64 = 1;
/// Host -> card: `[HELLO, VERSION, nonce, host pid, 0..]`.
const HELLO: u64 = u64::from_le_bytes(*b"INLHELLO");
// Card -> host: `[READY, VERSION, nonce, slot, reflector pid, 0..]`, or
// `[BUSY, VERSION, nonce, slot, reflector pid, holder's host pid, 0..]`, and
// later `[REVOKE, VERSION, nonce, slot, 0..]`.
const BUSY: u64 = u64::from_le_bytes(*b"INLBUSY!");
const REVOKE: u64 = u64::from_le_bytes(*b"INLREVOK");

type Record = [u64; 8];

fn send(mut to: impl Write, record: Record) -> io::Result<()> {
    let mut bytes = [0u8; 64];
    for (b, w) in bytes.chunks_exact_mut(8).zip(record) {
        b.copy_from_slice(&w.to_le_bytes());
    }
    to.write_all(&bytes)
}

fn recv(mut from: impl Read) -> io::Result<Record> {
    let mut bytes = [0u8; 64];
    from.read_exact(&mut bytes)?;
    Ok(std::array::from_fn(|i| u64::from_le_bytes(bytes[8 * i..8 * i + 8].try_into().expect("8 bytes"))))
}

/// A random nonce, never 0 (0 in the slot = no host).
fn fresh_nonce() -> io::Result<u64> {
    let mut bytes = [0u8; 8];
    std::fs::File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(u64::from_le_bytes(bytes) | 1)
}

/// [`Lease::state`] values.
const HELD: u8 = 0;
const REVOKED: u8 = 1;
const CLOSED: u8 = 2;

/// Host side: this run's right to use BAR4 slot [`SLOT`], held as long as
/// the connection to the reflector stays open; released on drop.
pub struct Lease {
    stream: TcpStream,
    card: String,
    nonce: u64,
    reflector_pid: u64,
    /// [`HELD`], or how the lease ended; set by `watcher`.
    state: Arc<AtomicU8>,
    watcher: Option<thread::JoinHandle<()>>,
}

impl Lease {
    /// Ask the reflector at `card` (`host:port`) for the slot. Touches no
    /// BAR4: fails with the reason if no reflector confirms this run.
    pub fn acquire(card: &str) -> Result<Lease, String> {
        let untouched = format!("BAR4 slot {SLOT} not touched");
        let addr = card
            .to_socket_addrs()
            .ok()
            .and_then(|mut a| a.next())
            .ok_or_else(|| format!("bad card address {card:?}; {untouched}"))?;
        let nonce = fresh_nonce().map_err(|e| format!("nonce: {e}; {untouched}"))?;
        let stream = TcpStream::connect_timeout(&addr, TIMEOUT).map_err(|e| match e.kind() {
            io::ErrorKind::ConnectionRefused => format!(
                "{card}: connection refused, so no inline_reflector is running (or only one older than the lease, \
                 MRV-26); {untouched}"
            ),
            io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => {
                format!("{card}: no answer within {TIMEOUT:?} (card down, or ens1/sdp1-0 not up?); {untouched}")
            }
            _ => format!("{card}: {e}; {untouched}"),
        })?;
        let io_err = |e: io::Error| match e.kind() {
            io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => {
                format!("{card}: no READY within {TIMEOUT:?}; {untouched}")
            }
            _ => format!("{card}: {e}; {untouched}"),
        };
        stream.set_read_timeout(Some(TIMEOUT)).map_err(io_err)?;
        stream.set_write_timeout(Some(TIMEOUT)).map_err(io_err)?;
        stream.set_nodelay(true).map_err(io_err)?;
        send(&stream, [HELLO, VERSION, nonce, u64::from(std::process::id()), 0, 0, 0, 0]).map_err(io_err)?;
        let reflector_pid = match recv(&stream).map_err(io_err)? {
            [READY, VERSION, n, slot, pid, ..] if n == nonce && slot == SLOT as u64 => pid,
            [READY, VERSION, n, slot, ..] => {
                return Err(format!(
                    "{card}: READY for slot {slot}, nonce {n:#018x}, but this run asked for slot {SLOT} with nonce \
                     {nonce:#018x} (a stale answer); {untouched}"
                ));
            }
            [BUSY, VERSION, _, _, pid, holder, ..] => {
                return Err(format!(
                    "{card}: inline_reflector (pid {pid}) is busy: host pid {holder} holds slot {SLOT}; {untouched}"
                ));
            }
            [magic @ (READY | BUSY), version, ..] => {
                return Err(format!(
                    "{card}: {} speaks lease protocol {version}, this tool {VERSION}; {untouched}",
                    String::from_utf8_lossy(&magic.to_le_bytes())
                ));
            }
            [magic, ..] => return Err(format!("{card}: unexpected answer {magic:#018x}; {untouched}")),
        };
        stream.set_read_timeout(None).map_err(io_err)?;
        let state = Arc::new(AtomicU8::new(HELD));
        let (reader, flag) = (stream.try_clone().map_err(io_err)?, state.clone());
        let watcher = thread::Builder::new()
            .name("lease".into())
            .spawn(move || {
                // The reflector sends nothing but REVOKE; the end of the stream
                // means it is gone. Either ends the lease.
                let how = if matches!(recv(&reader), Ok([REVOKE, ..])) { REVOKED } else { CLOSED };
                flag.store(how, Ordering::Release);
            })
            .map_err(io_err)?;
        Ok(Lease { stream, card: card.to_owned(), nonce, reflector_pid, state, watcher: Some(watcher) })
    }

    pub fn nonce(&self) -> u64 {
        self.nonce
    }

    pub fn reflector_pid(&self) -> u64 {
        self.reflector_pid
    }

    /// Whether the lease has ended: stop using the slot.
    pub fn revoked(&self) -> bool {
        self.state.load(Ordering::Acquire) != HELD
    }

    /// Fails once the lease has ended, saying how.
    pub fn check(&self) -> Result<(), String> {
        if self.revoked() { Err(self.ended()) } else { Ok(()) }
    }

    #[cold]
    fn ended(&self) -> String {
        if self.state.load(Ordering::Acquire) == REVOKED {
            format!("inline_reflector at {} took BAR4 slot {SLOT} back (it is exiting); stopped using the slot", self.card)
        } else {
            format!("connection to inline_reflector at {} closed (killed?); stopped using BAR4 slot {SLOT}", self.card)
        }
    }

    /// Map the slot from the PF's BAR4 resource file `bar` and check,
    /// through it, that it holds this reflector's READY and this run's nonce.
    /// The only way the host tools map BAR4.
    pub fn map_slot(&self, bar: &str) -> Result<LeasedSlot<'_>, String> {
        self.check()?;
        let map = Mapping::new(bar, (SLOT * SLOT_BYTES) as u64, SLOT_BYTES, false)
            .map_err(|e| format!("mapping BAR4 slot {SLOT} from {bar}: {e}"))?;
        let (cookie, nonce) = (map.read_u64(READY_OFFSET), map.read_u64(NONCE_OFFSET));
        if cookie != READY || nonce != self.nonce {
            let found = if cookie == READY { "a READY cookie".to_owned() } else { format!("cookie {cookie:#x} (not READY)") };
            return Err(format!(
                "BAR4 slot {SLOT} holds {found} with nonce {nonce:#018x}, this run's is {:#018x}: the slot does not \
                 point at the buffer of the reflector that answered (a stale READY); not writing",
                self.nonce
            ));
        }
        Ok(LeasedSlot { map, lease: self, state: &self.state })
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        // Every slot mapping is gone (they borrow the lease): let go.
        let _ = self.stream.shutdown(Shutdown::Both);
        if let Some(w) = self.watcher.take() {
            let _ = w.join();
        }
    }
}

/// The slot's mapping under a lease: a [`Mapping`] whose user calls
/// [`LeasedSlot::check`] right before each access (one load).
pub struct LeasedSlot<'l> {
    map: Mapping,
    lease: &'l Lease,
    /// The lease's state word, read directly.
    state: &'l AtomicU8,
}

impl<'l> LeasedSlot<'l> {
    #[inline]
    pub fn check(&self) -> Result<(), String> {
        if self.revoked() { Err(self.lease.ended()) } else { Ok(()) }
    }

    #[inline]
    pub fn revoked(&self) -> bool {
        self.state.load(Ordering::Acquire) != HELD
    }

    /// The lease, for mapping the slot again on another thread.
    pub fn lease(&self) -> &'l Lease {
        self.lease
    }
}

impl Deref for LeasedSlot<'_> {
    type Target = Mapping;

    fn deref(&self) -> &Mapping {
        &self.map
    }
}

/// Card side: answers HELLOs on a thread of its own, granting the slot to
/// one host at a time. [`LeaseServer::close`] takes the slot back.
pub struct LeaseServer<'scope> {
    /// Closing it wakes the thread.
    wake: Option<io::PipeWriter>,
    thread: Option<thread::ScopedJoinHandle<'scope, ()>>,
}

/// Serve `listener` from a thread of `scope`. `nonce` is the slot buffer's
/// [`NONCE_OFFSET`] word; the slot must already point at that buffer and hold
/// [`READY`].
pub fn serve<'scope>(
    scope: &'scope thread::Scope<'scope, '_>,
    listener: TcpListener,
    nonce: &'scope AtomicU64,
) -> io::Result<LeaseServer<'scope>> {
    let (wake_rx, wake_tx) = io::pipe()?;
    let thread =
        thread::Builder::new().name("lease".into()).spawn_scoped(scope, move || run(listener, wake_rx, nonce))?;
    Ok(LeaseServer { wake: Some(wake_tx), thread: Some(thread) })
}

impl LeaseServer<'_> {
    /// Stop granting the slot and take it back from its holder, if any:
    /// REVOKE, then wait for the host to close the connection (it stops using
    /// the slot first), with no time limit. Returns once that is done, so the
    /// slot may be restored.
    pub fn close(mut self) {
        self.stop();
    }

    fn stop(&mut self) {
        drop(self.wake.take());
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

impl Drop for LeaseServer<'_> {
    fn drop(&mut self) {
        self.stop();
    }
}

/// The host holding the lease.
struct Holder {
    stream: TcpStream,
    peer: SocketAddr,
    pid: u64,
    nonce: u64,
    since: Instant,
}

fn run(listener: TcpListener, wake: io::PipeReader, nonce_word: &AtomicU64) {
    let pollfd = |fd| libc::pollfd { fd, events: libc::POLLIN, revents: 0 };
    let mut holder: Option<Holder> = None;
    loop {
        let mut fds = [
            pollfd(listener.as_raw_fd()),
            pollfd(wake.as_raw_fd()),
            pollfd(holder.as_ref().map_or(-1, |h| h.stream.as_raw_fd())),
        ];
        // SAFETY: three initialised pollfds (poll skips a negative fd).
        if unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, -1) } < 0 {
            let e = io::Error::last_os_error();
            if e.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            eprintln!("lease: poll: {e}; no more hosts");
            break;
        }
        if fds[1].revents != 0 {
            break; // closing
        }
        if fds[2].revents != 0 {
            // A host sends nothing after HELLO: its connection became readable
            // because it closed it (or broke the protocol, which ends the lease
            // too: closing the connection tells the host).
            let h = holder.take().expect("polled the holder");
            nonce_word.store(0, Ordering::Release);
            println!(
                "lease: host {} (pid {}) released slot {SLOT} after {:.1} s",
                h.peer,
                h.pid,
                h.since.elapsed().as_secs_f64()
            );
        }
        if fds[0].revents != 0 {
            match listener.accept() {
                Ok((stream, peer)) => {
                    if let Some(h) = hello(stream, peer, holder.as_ref(), nonce_word) {
                        holder = Some(h);
                    }
                }
                Err(e) => eprintln!("lease: accept: {e}"),
            }
        }
    }
    if let Some(h) = holder {
        revoke(h);
    }
    nonce_word.store(0, Ordering::Release);
}

/// Answer one connection's HELLO; returns the new holder if it got the slot.
fn hello(stream: TcpStream, peer: SocketAddr, holder: Option<&Holder>, nonce_word: &AtomicU64) -> Option<Holder> {
    let me = u64::from(std::process::id());
    let setup = stream
        .set_read_timeout(Some(TIMEOUT))
        .and_then(|()| stream.set_write_timeout(Some(TIMEOUT)))
        .and_then(|()| stream.set_nodelay(true));
    let [magic, version, nonce, host_pid, ..] = match setup.and_then(|()| recv(&stream)) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("lease: {peer}: no HELLO ({e}); closed");
            return None;
        }
    };
    if magic != HELLO || version != VERSION {
        eprintln!("lease: {peer}: not a HELLO of version {VERSION} ({magic:#018x}, version {version}); closed");
        return None;
    }
    if let Some(h) = holder {
        let _ = send(&stream, [BUSY, VERSION, nonce, SLOT as u64, me, h.pid, 0, 0]);
        println!("lease: host {peer} (pid {host_pid}) refused: host {} (pid {}) holds slot {SLOT}", h.peer, h.pid);
        return None;
    }
    // Before READY: the host checks it through the slot.
    nonce_word.store(nonce, Ordering::Release);
    let ready = [READY, VERSION, nonce, SLOT as u64, me, 0, 0, 0];
    if let Err(e) = send(&stream, ready).and_then(|()| stream.set_read_timeout(None)) {
        nonce_word.store(0, Ordering::Release);
        eprintln!("lease: {peer}: READY not sent ({e}); closed");
        return None;
    }
    println!("lease: host {peer} (pid {host_pid}) took slot {SLOT}, nonce {nonce:#018x}");
    Some(Holder { stream, peer, pid: host_pid, nonce, since: Instant::now() })
}

/// Take the slot back from its holder and wait, without a time limit, until
/// it lets go: restoring the slot under a host that still holds the lease
/// (stopped, say, and yet to see the REVOKE) would aim its next access at the
/// firmware's window.
fn revoke(h: Holder) {
    let start = Instant::now();
    if let Err(e) = send(&h.stream, [REVOKE, VERSION, h.nonce, SLOT as u64, 0, 0, 0, 0]) {
        println!("lease: REVOKE to host {} (pid {}) failed ({e}); waiting for its connection to close", h.peer, h.pid);
    }
    let _ = h.stream.set_read_timeout(Some(WAIT_REPORT));
    let mut buf = [0u8; 64];
    loop {
        match (&h.stream).read(&mut buf) {
            Ok(0) => break,
            Ok(_) => {}
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(e) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => println!(
                "lease: host {} (pid {}) still holds slot {SLOT} after {:.0} s; waiting for it to let go before \
                 restoring the slot (stop that host; SIGKILL of this reflector would leave the slot on its buffer)",
                h.peer,
                h.pid,
                start.elapsed().as_secs_f64()
            ),
            // A reset or other error: the connection, and the host's lease, are gone.
            Err(_) => break,
        }
    }
    println!(
        "lease: took slot {SLOT} back from host {} (pid {}): released after {:.1} ms",
        h.peer,
        h.pid,
        start.elapsed().as_secs_f64() * 1e3
    );
}
