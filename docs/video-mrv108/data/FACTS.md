# MRV-108 Facts Ledger — CN106 DPU 零主机 CPU 转发直驱主机 Intel DSA

Every single quantitative claim in this video is backed by raw JSONL logs and verified by `data/verify_facts.py`.
Inferences/interpretations are explicitly marked `[INF]` (spoken: "我们推断" / "我们认为").

## 0. Hardware & Testbed Baseline
- Host: `saturn`, Intel Xeon Gold 6438M (Sapphire Rapids), Socket 1 / NUMA 1, `60 MiB` LLC, Linux 6.17.7 (`intel_iommu=on,sm_on`, `idxd.sva=Y`).
- Host clocks (pinned): `cpu40` `performance` `3.90 GHz`, Uncore (`package_01_die_01`) `2.20 GHz`.
- Intel DSA device: `dsa2` (`0000:f2:01.0`, NUMA 1, IOMMU `dmar6` group 18), dedicated user WQ `wq2.0` (`mode=dedicated`, `size=128`, `block_on_fault=0`, `pasid_enabled=1`, `max_batch_size=1024`), single engine `engine2.0`.
- DSA portal page: `BAR2 + 0x1000`, `4 KiB`, HPA `0x237ffff21000` (`resource2 + 0x1000`).
- Card: Marvell OCTEON 10 CN106 (`crb106-pcie`, Neoverse-N2 `2.00 GHz`, `lcore 3`, PCIe Gen5 x4).
  - Host PF `0000:99:00.0` (BAR4 = 16 × 4 MiB slots; slot 15 HPA `0x223ff3c00000`, `1,024` × 4 KiB `VM_PFNMAP` PTEs pre-populated by `io_remap_pfn_range`).
  - Host VF0 `0000:99:02.0` (IOMMU `dmar0` group 286).
  - Card DPI DMA VFs: `0000:06:00.1` (`FromHost` read-back), `0000:06:00.2` (`ToHost` 64 B atomic portal write & comp zero).
- Zero host CPU forwarding: host init process binds `WQCFG.PASID` via `open("/dev/dsa/wq2.0")` once and maps `portal_iova` (`0x117fe00000`) into VF0 `dmar0`, then blocks/sleeps during the entire datapath run.
- Bandwidth unit: decimal `GB/s = 10^9 B/s` (matching raw `runs.jsonl` `gb_s` field).

## 1. Stage 1 — Two-Layer `vmem` Mapping & Submit Primitives (MRV-95)
Source: `/home/hongtao/dev/marvell/logs/mrv95-2026-11-19/runs.jsonl` (94 rows).
- Hardware protocol constraints (Intel DSA Spec §3.3, §5.3, §9.3):
  - SWQ requires PCIe Non-Posted `DMWr` (`ENQCMD`); Posted `MWr` is silently dropped.
  - DWQ requires 64 B aligned atomic Posted `MWr`; non-64 B writes (e.g. `/dev/mem` `Device-nGnRnE` split into 4 × 16 B) are silently dropped with `SWERROR = 0`.
- Card DPI 32 B read-back floor (`card_dpi_read`, `poll_only`): `p50 = 1,212 ns` (`1.21 µs`), `p99 = 1,389 ns`.
- `W = 1` `p50` latency comparison (`runs.jsonl`, 3 reps median, `CC = 1`):
  - `NOP`: host `MOVDIR64B` `0.40 µs` (`402.7 ns`), card `pem_wc_none` `1.30 µs` (`1,301 ns`), card `dpi_dma` `1.98 µs` (`1,976 ns`), card `dpi_dma` closed-loop `1/rate(W=1)` `1.96 µs` (`1,963 ns`).
  - `64 B MEMMOVE`: host `0.59 µs`, card `pem_wc_none` `2.54 µs`, card `dpi_dma` `1.98 µs`.
  - `4 KiB MEMMOVE`: host `0.80 µs`, card `pem_wc_none` `2.55 µs`, card `dpi_dma` `2.04 µs`, card `dpi_dma` closed-loop `1/rate(W=1)` `3.22 µs`.
  - `64 KiB MEMMOVE`: host `2.92 µs`, card `pem_wc_none` `3.86 µs`, card `dpi_dma` `4.47 µs`.
  - `1 MiB MEMMOVE`: host `33.59 µs`, card `pem_wc_none` `35.24 µs`, card `dpi_dma` `35.31 µs`.
- DWQ silent drop rate (`SWERROR = 0`, `[INF]` ARM WC opportunistic coalescing splits/merges non-64 B TLP):
  - `card_pem_wc_dsb` (fixed off=0): `28 / 2,300 = 1.217%`
  - `card_pem_wc_dgh` (8-line rot): `14 / 2,300 = 0.609%`
  - `card_pem_wc_none` (8-line rot): `12 / 24,900 = 0.048%`
  - `card_dpi_dma` (hardware atomic 64 B MWr): `0 / 24,900 = 0.000%`, plus `182,913,304` (`> 1.82 亿`) pipelined rate ops with `0` lost, `0` bad, `SWERROR = 0`.
- Sustained pipelined throughput (`card_dpi_dma`, `CC = 1`, hot LLC `≤ 768 KiB` = `1.3%` of `60 MiB` LLC):
  - `NOP`: `W=1: 0.51`, `W=8: 3.41`, `W=32: 8.88`, `W=64: 12.66`, `W=96: 14.23 M ops/s`.
  - `4 KiB MEMMOVE`: `W=1: 1.27`, `W=8: 7.50`, `W=32: 22.60`, `W=64: 29.00`, `W=96: 29.53 GB/s` (`7.21 M ops/s`).
  - Descriptor PCIe traffic at `7.21 M ops/s`: `0.46 GB/s`; total PCIe ratio incl. comp zero + DPI polling read-back ≈ `6–7×`.

## 2. Stage 2 — Reverse-P2P Completion to Card BAR4 Slot 15 (MRV-99)
Source: `/home/hongtao/dev/marvell/logs/mrv99-2026-11-19/runs.jsonl` (90 rows) + `diag_min_batch.jsonl` (12 rows) = `102` runs, all `0` lost / `0` bad / `0` mismatches / `SWERROR = 0`.
- Single-variable setup: both groups use pure DPI submit (`0000:06:00.2`), `VFIO_DMA_MAP_FLAG_WRITE` portal, zero `pem_wc`, `dsa2/wq2.0` single engine, `CC = 0`. Only `completion_addr` changes (`HostMemzone` vs `CardBar4` slot 15 under lease).
- Reverse-P2P mechanism: `io_remap_pfn_range` pre-populates `1,024` × 4 KiB `VM_PFNMAP` PTEs (`HPA 0x223ff3c00000`) at `mmap()` time → `dmar6` SVA hits directly without fault → 32 B completion lands directly in CN106 L3 (`TAD`) → card core polls locally via `LDARB` and clears in `~1 ns`.
- `W = 1` symmetric closed-loop cycle `1 / rate(W=1)` (`HostMemzone` → `CardBar4`):
  - `NOP`: `1.96 µs` (`1,955 ns`) → `1.27 µs` (`1,269 ns`), `-35.1%` (`1.54×`).
  - `4 KiB MEMMOVE`: `3.13 µs` (`3,128 ns`) → `2.17 µs` (`2,172 ns`), `-30.6%` (`1.44×`).
- `W = 1` single-op `w1_lat` & jitter (`p99 − p50`):
  - `NOP`: `p50` `1.99 → 1.34 µs` (`-32.7%`).
  - `4 KiB MEMMOVE`: `p50` `2.18 → 2.09 µs`, `p99` `3.30 → 2.25 µs` (`-1.05 µs`), jitter `p99 − p50`: `1,119 ns → 163 ns` (eliminates `1.21 µs` DPI polling step!).
- `W = 1..96` sustained scaling (`CardBar4` vs `HostMemzone`):
  - `NOP` (`CardBar4`): `W=1: 0.79`, `W=8: 4.59`, `W=32: 12.66`, `W=64: 19.10`, `W=96: 23.01 M/s` (`+61.9%` over `HostMemzone` `14.22 M/s`, `43.5 ns/op`).
  - `4 KiB MEMMOVE` (`CardBar4`): `W=1: 1.89`, `W=8: 11.65` (`2.84 vs 1.80 M/s`, `+57.8%`), `W=32: 15.37` (`3.75 M/s`), `W=64: 15.28` (`3.73 M/s`), `W=96: 15.31 GB/s` (`3.74 M/s`, `267.5 ns/op`) — plateaus at half of `HostMemzone` `29.98 GB/s` (`7.32 M/s`, `136.6 ns/op`).
- Diagnosis of the `~131 ns` drain bubble:
  - `diag_min_batch` (`W=64`, `4 KiB`, `CardBar4`): `min_batch = 1 / 8 / 16` cuts doorbells `16×` (`3,538,919 → 442,649 → 221,338`), yet rate stays flat at `3.73 M/s` (`15.28 GB/s`) → strictly rules out card doorbell bottleneck.
  - Marginal copy slope on `CardBar4` `w1_lat` (`4 KiB → 64 KiB → 1 MiB`): `133.1 → 128.2 ns / 4 KiB` = `30.8–31.9 GB/s` → single-engine copy BW is intact!
  - Subtraction: `267.5 − 136.6 = 130.9 ns/op` (`~131 ns`) per-op `dst → BAR4` reverse-P2P completion drain bubble `[INF]`.

## 3. Stage 3 — DSA `Batch` Aggregation (`B = 1..32`, MRV-106)
Source: `/home/hongtao/dev/marvell-mrv106/logs/mrv106-2026-11-19/runs.jsonl` (48 rows: 24 rate + 24 w1_lat, all `0` lost/bad/dyn_lost/dyn_bad/comp32_bad/mismatches, `SWERROR = 0`).
- Single-variable setup: fixed `W = 64` total in-flight `4 KiB MEMMOVE`s, `CC = 0`, single engine `engine2.0`, `CardBar4` slot 15, zero host forwarding; only `B ∈ {1, 2, 4, 8, 16, 32}` varies (`opcode = 0x01` Batch, sub-descriptors `RCR = 0`, 1 × 32 B `BAR4` completion per `B` ops). Source working set `512 KiB` hits hot LLC; `dst` (`CC = 0`) writes through to DDR5.
- Sustained throughput (`W = 64`, `CardBar4`):
  - `B = 1` (`64` batches): `15.28 GB/s` (`3.73 M/s`, `268.04 ns / 4K`)
  - `B = 2` (`32` batches): `23.35 GB/s` (`5.70 M/s`, `175.44 ns / 4K`)
  - `B = 4` (`16` batches): `31.13 GB/s` (`7.60 M/s`, `131.56 ns / 4K`, `2.04×`, `99.4%` of peak)
  - `B = 8` (`8` batches): `31.25 GB/s` (`7.63 M/s`, `131.06 ns / 4K`)
  - `B = 16` (`4` batches): `31.32 GB/s` (`7.65 M/s`, `130.77 ns / 4K`, `2.05×` vs `B=1`)
  - `B = 32` (`2` batches): `30.57 GB/s` (`7.46 M/s`, `133.98 ns / 4K`, `-2.4%` shallower pipeline `[INF]`)
  - Reference `HostMemzone`: `B = 1: 29.98 GB/s`, `B = 16: 31.10 GB/s`.
  - Why `B = 4` eliminates 100% of the bubble `[INF]`: `4 × 131 ns ≈ 524 ns` of DRAM copy completely overlaps the reverse-P2P completion pacing across in-flight batches.
- Single-batch latency (`W = 1` batch in flight, `CardBar4`):
  - `B = 1`: `p50 = 2.09 µs` (`2,085 ns`), `p99 = 2.29 µs` (`2,286 ns`), `p99 − p50 = 201 ns`
  - `B = 2`: `p50 = 2.52 µs` (`2,515 ns`), `p99 = 2.65 µs` (`2,648 ns`), `p99 − p50 = 133 ns`
  - `B = 4` (sweet spot): `p50 = 2.84 µs` (`2,840 ns`), `p99 = 3.02 µs` (`3,024 ns`), `p99 − p50 = 184 ns`
  - `B = 8`: `p50 = 3.42 µs` (`3,416 ns`), `p99 = 3.62 µs` (`3,616 ns`), `p99 − p50 = 200 ns`
  - `B = 16`: `p50 = 4.42 µs` (`4,418 ns`), `p99 = 4.61 µs` (`4,608 ns`), `p99 − p50 = 190 ns`
  - `B = 32`: `p50 = 6.64 µs` (`6,639 ns`), `p99 = 6.86 µs` (`6,859 ns`), `p99 − p50 = 220 ns`
  - Tail spread across all `B = 1..32`: `p99 − p50 = 133–220 ns`.
- Head-to-head `B = 16` (`CardBar4` vs `HostMemzone`):
  - Throughput: `31.32 vs 31.10 GB/s`
  - `W = 1` `p50`: `4.42 vs 4.63 µs` (`-214 ns`)
  - `W = 1` `p99`: `4.61 vs 5.75 µs` (`-1,142 ns`, `p99 − p50`: `190 vs 1,118 ns`)

## 4. Engineering Guardrails & Next Step
- Portal mapping: `VFIO_DMA_MAP_FLAG_WRITE` (write-only, no read).
- Card submitter: zero `pem_wc`, pure DPI 64 B atomic `MWr`.
- SVA `PASID` isolation: `open("/dev/dsa/wq2.0")` binds the entire sleeping process `mm_struct` into `dmar6`; use a minimal dedicated holder process + `BAR4` lease handshake.
- Next experiment: `> 128 MiB` cold DRAM working set & multi-engine scaling.
