# Hardware & Testbed Nuances

Concise ledger of non-obvious hardware, SDK, OS, and measurement pitfalls discovered on `saturn` ⇄ OCTEON 10 CN106xx, together with the shared code in `main` that prevents them.

> **When you hit a new nuance:** add a 1–2 line entry here in the same `main` commit (symptom → rule → shared helper/MRV issue) so future work never rediscovers it the hard way.

---

## Host CPU, Clocks & Memory

- **Uncore frequency shifts W=1 RTT by ~0.3 µs**: host uncore idles at 0.8 GHz and ramps slowly, causing bimodal latency (0.92 vs 1.23 µs). Always pin before measurements with `sudo bench/host_power.sh pin` (2.2 GHz uncore, `performance` governor on cpu40/104) and `restore` afterwards.
- **Idle Socket-1 worker cores start at ~2.2 GHz**: `host_power.sh pin` only sets `performance` on cpu40/104; other node-1 cores under `schedutil` can stay near base clock for ~3 s after waking (MRV-24). Put the measured core on cpu40/104 or pin it (`sudo CPU=<n> bench/host_power.sh pin`), and check with `perf stat -C <cpu> -e cycles,ref-cycles`.
- **Plain `rdtsc` under-measures MMIO polling RTT by ~0.5 µs**: `rdtsc` executes speculatively before the preceding MMIO load completes. Always timestamp arrivals with `dpdk::rdtsc_after_loads()` (`RDTSCP`).
- **Minor page faults distort tail latency**: always lock memory and bind NUMA policy via `testbed::rt` (`mlockall` + `MPOL_BIND` + `PageFaults` audit) before entering measurement loops.
- **Background builds disturb live Socket-1 measurements**: pin background `cargo build` / `mrv-check` runs to Socket 0 (`taskset -c 0-31`) when Socket 1 is running live card benchmarks.

## BAR4, MMIO & Card Memory Ordering

- **Never touch BAR4 slot 15 without an active lease**: host tools must acquire a lease from `inline_reflector` via `testbed::lease` (`10.10.0.2:7526`, READY + nonce check, `LeasedSlot::check`) before mapping or writing slot 15. After a `kill -9` on the card, run `inline_reflector --restore` to reset slot 15 (`0x2ff71`) and PEM0 regions 248–255 (MRV-26).
- **Never `read_volatile` a 64 B cell struct on the card**: LLVM lowers it to 64 byte loads plus a store-forwarding stall. Always read cells word by word with `testbed::inline::Cell::poll`.
- **Single 64-bit loads only on card CSR `/dev/mem` mmaps**: never slice (`m[o:o+8]`) or iterate an mmap array (raises SEI-UER errors; EDAC logs them ~20 s later). Read known registers via single 64-bit accesses only, as in `card-scripts/bar4idx7.py`, and never probe guessed CSR offsets.
- **Card BAR4 ring `MT_NORMAL_NC` needs no `MAIR_EL1` edit**: use `card-kmod/bar4_mair/` (`enable_custom_mair=0`) + `testbed::bar4_nc` to map the 4 MiB BAR4 slot-15 ring as standard Linux `MT_NORMAL_NC (0x44)` (MRV-245, MRV-259).
- **PEM0 outbound regions (248–255) are store-only**: loading through an outbound region that the host does not complete raises an SError on the card. Always restore regions on exit (`ctrlc` with `termination` feature + `testbed::mmio`).

## DPDK, `octeon_ep` & Card Control Path

- **Disabling flow control requires `fc.autoneg = 0`**: setting `fc.mode = RTE_ETH_FC_NONE` alone fails or is ignored unless `autoneg = 0`. Use `dpdk_sys_eth_disable_flow_ctrl` in `testbed/crates/dpdk-sys/wrapper.h`.
- **`rx_poll_mem=1` sees the info word before the frame tail**: this experimental `octeon_ep` devarg counts RX packets from the in-buffer info word instead of reading `OUT_CNTS`; only callers that verify a trailing `seq` copy may enable it.
- **Never bind `sdp1-0` (`ens1`) to DPDK**: `sdp1-0` is the SSH control path (`10.10.0.2`). DPDK uses host VF0 (`0000:99:02.0`) ⇄ card `sdp1-1` (`0002:01:00.2`).
- **Unpatched `octeon_ep` stalls host RTNL when the card dies**: reading `/sys/class/net/ens1/speed` or running `ethtool ens1` while the card heartbeat is lost blocks RTNL for 8 s per call (MRV-31). Keep `host-tools/octeon_ep-patches/0001-*` applied.
- **Card-initiated reboot leaves host `ens1` down**: `octeon_ep` does not recover `ens1` after a `systemctl reboot` on the card; run `sudo bench/host_boot_persist.sh reload` on the host to reload the driver and recreate VFs.

## Build & Secondary `jj` Workspaces

- **Never symlink `sysroot-card/`**: a symlinked sysroot breaks aarch64 linking against `libmvec.so.1`. `devenv.nix` automatically falls back to `../marvell/{marvell-dpdk,sysroot-card}` in secondary `../marvell-<issue>` workspaces.
- **New DPDK C functions go in `dpdk-sys`**: add them to `FUNCTIONS` in `testbed/crates/dpdk-sys/build.rs` (and `wrapper.h` for macros/bitfields) — never write ad-hoc `extern "C"` blocks in experiment crates.
- **Host cross-language LTO needs `-flto -mno-rtm`**: `saturn` has TSX disabled so `rustc -C target-cpu=native` omits `rtm`, while `libdpdk.pc` passes `-mrtm`. `devenv.nix` sets `CFLAGS_x86_64_unknown_linux_gnu = "-flto -mno-rtm"` so LTO inlining works cleanly.

---

### Adding a New Nuance

```markdown
- **<Short symptom or rule>**: <one-sentence cause and what to do instead; point to `testbed::...` / `bench/...`> (MRV-<N>).
```
