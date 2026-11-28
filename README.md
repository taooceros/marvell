# Marvell OCTEON 10 (CN106xx) DPU Datapath Workspace

Host (`saturn`, x86_64) ⇄ OCTEON 10 CN106xx (`crb106-pcie`, aarch64) PCIe datapath testbed, experiments, kernel modules, raw logs, and figures.

For full agent operating rules, Linear workflow, and hardware safety procedures, see [`AGENTS.md`](AGENTS.md). For hard-won hardware/SDK/OS gotchas already solved in `main`, see [`docs/NUANCES.md`](docs/NUANCES.md).

## Repo Map

| Path | What lives here |
| --- | --- |
| `testbed/crates/` | **Shared Rust infrastructure** (`dpdk-sys`, `dpdk`, `testbed` library + 6 baseline binaries) |
| `testbed/experiments/` | Per-issue Cargo members (`mrv<N>/`), auto-discovered by `testbed/Cargo.toml` |
| `testbed/mc-adapter/` | Unmodified upstream Memcached 1.6.45 C adapters (linked by Memcached experiment crates) |
| `testbed/mercury-client/` | Remote `mercury` RoCEv2/UDP load-generator client sources |
| `card-kmod/` | Card-side Linux 6.12 kernel modules (`pem_wc/`, `bar4_mair/`) |
| `bench/` | Root-only host setup scripts (`host_dpdk_up.sh`, `host_power.sh`, `host_ddio.sh`, `host_vf_kernel.sh`, `host_dsa.sh`) and early C prototypes |
| `card-scripts/` | Card boot units, netplan config, and CSR helpers |
| `host-tools/` | Serial console utilities and local `octeon_ep` driver patches |
| `logs/` | Raw `runs.jsonl` outputs and `run_mrv<N>.py` experiment drivers |
| `docs/` | Figures (`docs/figures/`), papers, explainer video sources, and [`docs/NUANCES.md`](docs/NUANCES.md) |

## Where Does My Code Go? (Upstream-First)

Always contribute back to `main` so bugfixes, primitives, and hardware nuances are never lost or copy-pasted across issues:

| Kind of change | Where it goes | Rule |
| --- | --- | --- |
| Reusable helper, DPDK binding, bugfix, or hardware workaround | `testbed/crates/{dpdk-sys,dpdk,testbed}`, `card-kmod/`, or `bench/` | **Preferred.** Put it in the shared layer first instead of duplicating it inside an experiment crate. |
| Experiment-specific protocol, workload, or host/card binaries | `testbed/experiments/mrv<N>/` | Keep it thin: depend on `dpdk` / `testbed` (or sibling protocol crates). Scaffold with `mrv-new`. |
| Raw data, plot scripts, or newly discovered gotchas | `logs/mrv<N>-<date>/`, `docs/figures/mrv-<N>-*`, and [`docs/NUANCES.md`](docs/NUANCES.md) | Add a 1–2 line entry to `docs/NUANCES.md` whenever you hit a non-obvious hardware/SDK/OS nuance. |

## Shared Infrastructure Already in `main`

Check these before writing new code:

- **`dpdk-sys` / `dpdk`**: static-inline DPDK fast-path bindings (`wrap_static_fns`), `Eal`, `Mempool`, `Port`, `Mbuf`, `Burst`, `Memzone`, `RxQueue`, `TxQueue`, `QueuePair`, `PortStats`, `HostDma`, CN10K CQE peek/direct helpers, `Port::bar0_ptr`, `rdtsc_after_loads`, flow-control disable (`fc.autoneg = 0`).
- **`testbed` library** (`testbed/crates/testbed/src/`):
  - `mmio`: PCI BAR / `/dev/mem` mappings, host `MOVDIR64B`/`SFENCE`, card `BAR4_INDEX` & PEM0 outbound region control (`retarget_range`, `detach`).
  - `lease`: TCP control lease (`10.10.0.2:7526`) guarding BAR4 slot-15 access.
  - `inline`: BAR4 inline request/reply ring & word-wise `Cell::poll`.
  - `bar4_nc`: `/dev/bar4_mair` wrapper for standard Linux `MT_NORMAL_NC (0x44)` BAR4 mapping (paired with `card-kmod/bar4_mair/`).
  - `dsa`: Intel DSA portal (`wq2.0`) mmap, DRAIN gate, and non-multiplexed `dsa2` PMU.
  - `hist`: allocation-free multi-resolution `FastHist` (`[0, 1 s]`).
  - `pmu`: non-multiplexed `perf_event_open` helpers for x86_64 uncore and aarch64 CN10K PMUv3/TAD/DDR.
  - `rt`: `mlockall`, NUMA `MPOL_BIND`, CPU affinity, and `getrusage` page-fault audit.
  - `closed_loop`: verified closed-loop request/reply driver.
- **Baseline binaries** (`testbed/crates/testbed/src/bin/`): `pingpong`, `reflector`, `inline_pingpong`, `inline_reflector`, `inline_stream`, `card_coherency`.

## Contributing to `main` in 4 Steps

1. **Create an isolated `jj` workspace** (when editing code while other work runs; `devenv.nix` automatically shares `../marvell/{marvell-dpdk,sysroot-card}`):
   ```sh
   jj workspace add ../marvell-mrv<N> -r main
   cd ../marvell-mrv<N>
   ```
2. **Add shared code or scaffold a thin experiment crate**:
   - Prefer extending `testbed/crates/*`, `card-kmod/`, or `bench/` for anything reusable.
   - For a new experiment crate under `testbed/experiments/mrv<N>/`:
     ```sh
     devenv shell -- mrv-new <N> "short description"
     ```
3. **Build and verify** (pin background builds to Socket 0 with `taskset -c 0-31` if Socket 1 is running live measurements):
   ```sh
   devenv shell -- mrv-build            # build core crates (host + aarch64 card)
   devenv shell -- mrv-build mrv<N>     # build one experiment crate (host + aarch64 card)
   devenv shell -- mrv-check            # workspace check + unit tests (pre-land gate)
   ```
   Outputs land in `testbed/target/release/` (host) and `testbed/target/aarch64-unknown-linux-gnu/release/` (card).
4. **Record nuances, land on `main`, and clean up**:
   - If you hit a new hardware/SDK/OS pitfall, add a short bullet to [`docs/NUANCES.md`](docs/NUANCES.md).
   - Commit with a strictly English message (`MRV-<N> (<short topic>): ...`), advance `main` (`jj bookmark set main -r <change>`), push (`jj git push --bookmark main`), and remove the temporary workspace (`jj workspace forget marvell-mrv<N> && rm -rf ../marvell-mrv<N>`).
