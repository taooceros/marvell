# English Explainer Facts Ledger — CN106 DPU Directly Driving Host Intel DSA

Every quantitative claim in this video is backed by raw JSONL logs and verified by `data/verify_facts.py`.
Inferences/interpretations are explicitly marked `[INF]` on screen (spoken: "we infer" / "we attribute this to").

## 0. Hardware & Testbed Baseline
- Host: `saturn`, Intel Xeon Gold 6438M (Sapphire Rapids), Socket 1 / NUMA 1, `60 MiB` LLC, Linux 6.17.7 (`intel_iommu=on,sm_on`, `idxd.sva=Y`).
- Host clocks (pinned): `cpu40` `performance` `3.90 GHz`, Uncore (`package_01_die_01`) `2.20 GHz`.
- Intel DSA device: `dsa2` (`0000:f2:01.0`, NUMA 1, IOMMU `dmar6` group 18), dedicated user WQ `wq2.0` (`mode=dedicated`, `size=128`, `block_on_fault=0`, `pasid_enabled=1`, `max_batch_size=1024`), single engine `engine2.0`.
- DSA portal page: `BAR2 + 0x1000`, `4 KiB`, HPA `0x237ffff21000` (`resource2 + 0x1000`).
- Card: Marvell OCTEON 10 CN106 (`crb106-pcie`, Neoverse-N2 `2.00 GHz`, `lcore 3`, PCIe Gen5 x4).
  - Host PF `0000:99:00.0` (BAR4 = 16 x 4 MiB slots; slot 15 HPA `0x223ff3c00000`, `1,024` x 4 KiB `VM_PFNMAP` PTEs pre-populated by `io_remap_pfn_range`).
  - Host VF0 `0000:99:02.0` (IOMMU `dmar0` group 286).
  - Card DPI DMA VFs: `0000:06:00.1` (`FromHost` read-back), `0000:06:00.2` (`ToHost` 64 B atomic portal write & comp zero).
- Zero host CPU forwarding: host init process binds `WQCFG.PASID` via `open("/dev/dsa/wq2.0")` once and maps `portal_iova` (`0x117fe00000`) into VF0 `dmar0`, then sleeps for the entire datapath run.
- Bandwidth unit: decimal `GB/s = 10^9 B/s` (matching raw `runs.jsonl` `gb_s` field).

## 1. Stage 1 — Two-Layer `vmem` Mapping & Submit Primitives (94 raw runs)
Source: `/home/hongtao/dev/marvell/logs/mrv95-2026-11-19/runs.jsonl` (94 rows).
- Protocol constraints (Intel DSA Spec §3.3, §5.3, §9.3):
  - SWQ requires PCIe Non-Posted `DMWr` (`ENQCMD`); Posted `MWr` is silently dropped.
  - DWQ requires 64 B aligned atomic Posted `MWr`; non-64 B writes (e.g. `/dev/mem` `Device-nGnRnE` split into 4 x 16 B) are silently dropped with `SWERROR = 0`.
- Card DPI 32 B read-back floor (`card_dpi_read`, `poll_only`): `p50 = 1,212 ns` (`1.21 µs`), `p99 = 1,389 ns`.
- `W = 1` `p50` latency comparison (3 reps median, `CC = 1`):
  - `NOP`: host `MOVDIR64B` `0.40 µs`, card `pem_wc_none` `1.30 µs`, card `dpi_dma` `1.98 µs` (`−0.68 µs` WC gap), card `dpi_dma` closed-loop `1/rate(W=1)` `1.96 µs`.
  - `4 KiB MEMMOVE`: host `0.80 µs`, card `dpi_dma` `2.04 µs`, closed-loop `1/rate(W=1)` `3.22 µs`.
- DWQ silent drop rate (`SWERROR = 0`, `[INF]` ARM WC opportunistic coalescing splits/merges non-64 B TLP):
  - `wc-dsb` (fixed off=0): `28 / 2,300 = 1.217%`
  - `wc-dgh` (8-line rot): `14 / 2,300 = 0.609%`
  - `wc-none` (8-line rot): `12 / 24,900 = 0.048%`
  - `DPI DMA 64 B`: `0 / 24,900 = 0.000%`, plus `182,913,304` (`> 182M`) pipelined rate ops with `0` lost, `0` bad, `SWERROR = 0`.
- Sustained pipelined throughput (`card_dpi_dma`, `CC = 1`, hot LLC `<= 768 KiB`):
  - `NOP`: `W=1: 0.51`, `W=8: 3.41`, `W=32: 8.88`, `W=64: 12.66`, `W=96: 14.23 M/s`.
  - `4 KiB MEMMOVE`: `W=1: 1.27`, `W=8: 7.50`, `W=32: 22.60`, `W=64: 29.00`, `W=96: 29.53 GB/s` (`7.21 M/s`).
  - Descriptor traffic: `0.46 GB/s`; total PCIe traffic incl. comp zero + DPI polling read-back ~`6–7x`.

## 2. Stage 2 — Reverse-P2P Completion to Card BAR4 Slot 15 (102 raw runs)
Source: `/home/hongtao/dev/marvell/logs/mrv99-2026-11-19/runs.jsonl` (90 rows) + `diag_min_batch.jsonl` (12 rows) = `102` runs, all `0` lost / `0` bad / `0` mismatches / `SWERROR = 0`.
- Single-variable setup: pure DPI submit (`0000:06:00.2`), `VFIO_DMA_MAP_FLAG_WRITE` portal, zero `pem_wc`, `dsa2/wq2.0` single engine, `CC = 0`. Only `completion_addr` changes (`HostMemzone` vs `Card BAR4` slot 15 under lease).
- Reverse-P2P mechanism: `io_remap_pfn_range` pre-populates `1,024` x 4 KiB `VM_PFNMAP` PTEs (`HPA 0x223ff3c00000`) at `mmap()` time -> `dmar6` SVA hits directly with zero page faults -> 32 B completion lands in CN106 L3 (`TAD`) -> card core polls locally via `LDARB` and clears in `~1 ns`.
- `W = 1` symmetric closed-loop cycle `1 / rate(W=1)` (`HostMemzone` -> `Card BAR4`):
  - `NOP`: `1.96 µs` -> `1.27 µs` (`-35.1%`, `1.54x`).
  - `4 KiB MEMMOVE`: `3.13 µs` -> `2.17 µs` (`-30.6%`, `1.44x`).
- `W = 1` tail jitter (`p99 - p50`, `4 KiB MEMMOVE`): `1,119 ns` (`p50 2.18 / p99 3.30 µs`) -> `163 ns` (`p50 2.09 / p99 2.25 µs`, `-85.4%`).
- `W = 1..96` sustained scaling:
  - `NOP` (`Card BAR4`): `W=96` reaches `23.01 M/s` (`43.5 ns/op`, `+61.9%` vs `HostMemzone` `14.22 M/s`).
  - `4 KiB MEMMOVE` (`Card BAR4`): `W=1: 1.89`, `W=8: 11.65` (`+57.8%`), `W=32: 15.37`, `W=64: 15.28`, `W=96: 15.31 GB/s` (`3.74 M/s`, `267.5 ns/op`) — plateaus at half of `HostMemzone` `29.98 GB/s` (`7.32 M/s`, `136.6 ns/op`).
- Diagnosis of the `~131 ns` drain bubble:
  - `diag_min_batch` (`W=64`, `4 KiB`, `Card BAR4`): `min_batch = 1 / 8 / 16` cuts doorbells `16x` (`3.54M -> 0.44M -> 0.22M`), rate stays flat at `3.73 M/s` (`15.28 GB/s`).
  - Marginal copy slope (`4 KiB -> 64 KiB -> 1 MiB` on `Card BAR4`): `133.1 -> 128.2 ns / 4 KiB` = `30.8–31.9 GB/s`.
  - Subtraction: `267.5 - 136.6 = 130.9 ns/op` (`~131 ns`) per-op `dst -> BAR4` reverse-P2P drain bubble `[INF]`.

## 3. Stage 3 — DSA `Batch` Aggregation (`B = 1..32`, 48 raw runs)
Source: `/home/hongtao/dev/marvell-mrv106/logs/mrv106-2026-11-19/runs.jsonl` (48 rows: 24 rate + 24 w1_lat, all `0` lost/bad/dyn_lost/dyn_bad/comp32_bad/mismatches, `SWERROR = 0`).
- Single-variable setup: fixed `W = 64` total in-flight `4 KiB` copies, `CC = 0`, single engine `engine2.0`, `Card BAR4` slot 15, zero host forwarding; only `B in {1, 2, 4, 8, 16, 32}` varies (`opcode = 0x01` Batch for `B >= 2`, sub-descriptors `RCR = 0`, 1 x 32 B `BAR4` completion per batch).
- Working-set caveat: active working set is `516 KiB` (`src 256 KiB + dst 256 KiB + subdesc 4 KiB`, `< 0.9%` of `60 MiB` LLC) — hot LLC reads (`src` & `subdesc`) + `CC = 0` write-through (`WiL`) to DDR5.
- Sustained throughput (`W = 64`, `Card BAR4`):
  - `B = 1` (`64` batches): `15.28 GB/s` (`3.73 M/s`, `268.0 ns / 4K`)
  - `B = 2` (`32` batches): `23.35 GB/s` (`5.70 M/s`, `175.4 ns / 4K`, `350.9 ns/batch`)
  - `B = 4` (`16` batches): `31.13 GB/s` (`7.60 M/s`, `131.6 ns / 4K`, `526.2 ns/batch`, `2.04x`, `99.4%` of peak)
  - `B = 8` (`8` batches): `31.25 GB/s` (`7.63 M/s`, `131.1 ns / 4K`)
  - `B = 16` (`4` batches): `31.32 GB/s` (`7.65 M/s`, `130.8 ns / 4K`, `2.05x`)
  - `B = 32` (`2` batches): `30.57 GB/s` (`7.46 M/s`, `134.0 ns / 4K`, `-2.4%` shallower `W/B = 2` pipeline `[INF]`)
  - Reference `HostMemzone`: `B = 1: 29.98 GB/s`, `B = 16: 31.10 GB/s`.
- Two-stage pipeline overlap `[INF]`: against `tau_copy = 130.77 ns / 4 KiB`, non-overlap residual `Delta_batch = batch_cyc - B * tau_copy` shrinks from `+89.3 ns` (`B=2`) to `+3.2 / +2.3 / +0.0 ns/batch` (`B = 4 / 8 / 16`), fitting a two-stage parallel envelope `max(tau_ctrl_comp(B), B * tau_copy)`.
- Single-batch latency (`W = 1` batch in flight, `Card BAR4`):
  - `B = 1`: `p50 2.09 / p99 2.29 µs` (`Δ 201 ns`), `loop_mean 2.19 µs` (vs Host `3.01 µs`, `-27.2%`)
  - `B = 2`: `p50 2.52 / p99 2.65 µs` (`Δ 133 ns`)
  - `B = 4` (sweet spot): `p50 2.84 / p99 3.02 µs` (`Δ 184 ns`)
  - `B = 8`: `p50 3.42 / p99 3.62 µs` (`Δ 200 ns`)
  - `B = 16`: `p50 4.42 / p99 4.61 µs` (`Δ 190 ns`), `loop_mean 4.53 µs` (vs Host `5.74 µs`, `-21.1%`)
  - `B = 32`: `p50 6.64 / p99 6.86 µs` (`Δ 220 ns`)
  - Pure Batch (`B = 2..32`) linear regression `p50(B) = 2,278.4 + 136.1 * B ns` extrapolates to `2,414.4 ns` at `B = 1` vs direct `MEMMOVE` `2,085.0 ns` -> **`+329 ns`** Batch indirect sub-descriptor fetch step!
  - Head-to-head `B = 16` (`Card BAR4` vs `HostMemzone`): throughput `31.32 vs 31.10 GB/s`, `p50 4.42 vs 4.63 µs`, `p99 4.61 vs 5.75 µs` (`Δ 190 vs 1,118 ns`), symmetric `loop_mean 4.53 vs 5.74 µs` (`-21.1%`).

## 4. Engineering Guardrails & Next Step
- Portal mapping: `VFIO_DMA_MAP_FLAG_WRITE` (write-only, no read).
- Card submitter: zero `pem_wc`, pure DPI 64 B atomic `MWr`.
- SVA `PASID` isolation: `open("/dev/dsa/wq2.0")` binds the entire sleeping process `mm_struct` into `dmar6`; isolate in a minimal dedicated holder process + `BAR4` lease handshake.
- Next step: `> 128 MiB` cold DRAM working set (`256 MiB` ring) & multi-engine scaling.
