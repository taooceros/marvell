# FACTS — MRV-96 v3 Advisor Progress Report (Host ⇄ OCTEON 10 DPU Direct PCIe Datapath)

Every number below is recomputed from raw `runs.jsonl` by `data/verify_facts.py`
(`uv run -q --with matplotlib python docs/video-mrv96/data/verify_facts.py`).
All plotted runs verified zero data loss / corruption (`lost = bad = torn = 0`, DSA `mismatches = 0`, `swerror = 0`).
`[INF]` marks interpretations, not direct measurements.
Rule: **ZERO internal issue IDs (`MRV-xx`) or raw `logs/mrvxx` paths** anywhere on screen or in speech.

## §S01 Overview & 897 ns Direct-Path Baseline
- Hardware: Intel Xeon Gold 6438M host (core pinned **3.9 GHz**, uncore **2.2 GHz**) ⇄ **PCIe Gen5 ×4** (zero switch) ⇄ Marvell OCTEON 10 DPU (**24 Arm cores** at **2.0 GHz**).
- Direct path: Forward = `MOVDIR64B` 64-byte write into card BAR4 ring (no doorbell, no DMA). Return = card core `64 B` write-combining (`4 × 16 B`) straight into host RAM.
- 3 same-machine measured `W = 1` RTT anchors:
  - Anchor 1 (Host on-die DSA2 NOP, 0 PCIe hops): **403 ns** (`402.7 ns`) → Xeon on-die round trip (~201 ns one way).
  - Anchor 2 (BAR4 uncached hardware read, across PCIe Gen5 ×4, no card core): **646 ns** (`645.5 ns`) → `+243 ns` PCIe Gen5 ×4 + PEM0 `[INF]`.
  - Anchor 3 (Host ⇄ Card core `W = 1`, `MOVDIR64B` + 1 card core WC): **897 ns** (`897.3 ns`) → `+212 ns` core/cache/IOMMU `[INF]` + `~40 ns` card work (~429 ns one way).
- 3-part progress roadmap: (1) Three things nailed down on direct MMIO, (2) The one open puzzle (`13 M/s` 4-core closed loop), (3) New zero-host-CPU Card → Intel DSA P2P breakthrough (**31.32 GB/s**).

## §S02 Nailed Down #1 — Why One-Way Hits 161.6 M/s
- Sanity check (return traffic OFF vs ON, host pinned `3.9 GHz`, 1 recv + 1 reply core WC): **113.1 vs 112.2 M/s** (no slowdown; earlier `75.6 M/s` drop was an unpinned `~2.1 GHz` sender `[INF]`).
- Frontier polling (one-way forward, `3.9 GHz`, **2 card cores**, `≤ 32 cells/poll`; knob = pause when caught up):
  - `0 µs`: **128.7 M/s** (`93.6%` empty polls, host CHA queue **447** entries / **3.47 µs**).
  - `1 µs`: **132.2 M/s** (`132–134` across modes, `433` entries / `3.28 µs`).
  - `3 µs`: **161.6 M/s** (**+26%**, `24.2 cycles/msg`, queue drops to **360** / **2.23 µs**, `~900` cells settle).
- Location vs card L3 bandwidth (fixed `3 µs` pause; knob = where card reads during pause):
  - Idle spin: **160.4 M/s** (`608 M/s` L3 reqs).
  - Cold RAM outside ring: **160.4–161.4 M/s** (`815–1,033 M/s` L3 reqs, **> 1 billion/s**!).
  - Old ring slots behind frontier: **162.0–162.6 M/s** (`790–948 M/s` L3); just behind: **160.2 M/s** (`728 M/s` L3).
  - **Active ~3 µs write band (ahead)**: crashes to **113.4 M/s** (`472 M/s` L3, snoops `67.9 → 111.9 M`, host CHA **4.03 µs > 3 µs** `[INF]` coherence snoops / write blocking feedback).

## §S03 Nailed Down #2 — Cache/DDIO Shortcuts Active & 64 B Card Coherence
- `W = 1` `897 ns` cache/DDIO single-variable checks (40 runs, `cpu40 ≥ 3.85 GHz`, 100% PMU active):
  - **Host source hot in L1D**: flushing source to cold DRAM adds **+117.3 ns** end-to-end RTT (`897.3 → 1,014.5 ns`).
  - **Card L3 write-allocate ON**: flushing card slot before host write keeps card read flat (`104.0 → 102.0 ns`, `−2.0 ns`) while `tad_alloc_ltg` jumps **+1.00/msg** (`0.11 → 1.11`); flushing *after* write to card DRAM adds **+75.0 ns** read-side (`104.0 → 179.0 ns`).
  - **Host Xeon DDIO ON**: hot reply ring hits `io_hit_itom = 1.00`; flushing before card WC write keeps host read flat (`42.7 → 42.7 ns`, `0.0 ns`); flushing *after* write to host DRAM adds **+87.3 ns** read-side (`42.7 → 130.0 ns`).
- Card core-to-core L3 coherence (2 card cores rewriting own 64 B cells; knob = spacing `d`, Linux reports `128 B`):
  - `0 B` (same cell): **72.3 M/s** per core (**17.8 M/s** snoops).
  - `64 B / 128 B / 256 B / 4 KiB`: **259.8 M/s** per core (**0.52–0.55 M/s** idle snoops). Coherence is strictly **64 B** (core-to-core; PCIe beside polling untested).

## §S04 Nailed Down #3 — Open-Loop Bidirectional Breaks Lockstep
- Fixed: 1 host core + 1 card core, WC replies. Knob: closed-loop lockstep vs open-loop bidirectional duplex.
- Lockstep (`W = 256`, 1 card core): **14.2 M/s**.
- Open-loop duplex (`5–35 M/s` each way): met cleanly; at **35 + 35 M/s** (`70 M/s` combined), median `p50` is **0.97 µs** forward / **1.24 µs** return (`~1 µs`).
- Peak unthrottled (1 host core): `1 card core` **41.9 + 41.9 = 83.7 M/s**; `2 card cores split` **91.0 M/s** (`45.5` each way, WC) and **97.3 M/s** (`49.3 + 47.9`, DMA).
- Honest tail caveat: at `35 + 35 M/s`, forward **`p99 = 38.7 µs`** (`p90 = 5.55 µs`, return `p99 = 13.9 µs`; at `20 + 20`, forward `p50 = 16.9 µs`). In open-loop request–response **Echo**, RTT `p99` jumps from **1.26 µs** (`10 M/s`) to **35.4 µs** at **13.5 M/s**.

## §S05 Open Problem (Honest Status) — Why Does 4-Core Closed-Loop Stall at 13 M/s?
- The Little's Law puzzle: at `0.92 µs` (`897 ns`) RTT, `W = 64` SHOULD saturate near **~50 M/s** — and **1 card core at `W = 1024` DOES hit `50.5 M/s`**! Yet **4 card cores** collapse to **13.1–13.5 M/s** at `W = 64..256` (and only **23.9 M/s** at `W = 1024`, where host reply-polling grows `13.6 → 32.5 ns`).
- What we HAVE measured inside the `13 M/s` slow state:
  - Timestamps (`W = 64`, `13.5 M/s`, RTT `3.7 µs`): card work stays **0.04 µs**, **~42 messages** queue (`3.1–3.2 µs` forward, `~0.5 µs` return; flipping to return `0.7 / 2.7 µs` in 2 runs without page pre-touch).
  - Delay-injection slopes (`20M` msgs/run): card slope **−0.01** (`76.1 → 75.6 ns` up to `+59 ns`), host **`+10 ns` speeds it up +20%** (`76.2 → 60.9 ns` `[INF]` burst pacing breaks slow rhythm) — **both CPUs sit idle**.
  - Hardware suspects already cleared: interleaved WC stays **99.99%** full 64 B writes (`13.14 vs 13.16 M/s`), and card L3 coherence is **64 B** (not `128 B`).
- **Honest open question (running next on hardware)**: We have NOT yet pinned down which hardware queue throttles those ~42 in-flight writes to `~75 ns/msg`. Next experiment on the card: sweep `MOVDIR64B` pacing & parallel ingress with host Store Buffer / CHA / IIO vs card TAD counters.

## §S06 New Breakthrough — Zero-Host-CPU Bidirectional PCIe P2P to Intel DSA
- Forward P2P (Card → Host `dsa2/wq2.0`, zero host CPU forwarding):
  - Card **DPI DMA** 64 B descriptor writes: **0 drops** across **182.9M+ ops** (`lost = bad = mismatches = 0`).
  - Card **WC** direct portal writes: **0.048%–1.217%** silent drops (`12/24,900` none, `14/2,300` DGH, `28/2,300` DSB).
- Reverse P2P (`dsa2` writes 32 B completion directly into Card BAR4 vs Host RAM):
  - `W = 1` closed-loop period: `NOP` **1.96 → 1.27 µs** (`−35%`), `4 KiB` **3.13 → 2.17 µs** (`−31%`).
  - `W = 1` `4 KiB` latency jitter (`p99 − p50`): **1,119 ns → 163 ns** (`−85%`, eliminating the `1.21 µs` host-RAM DPI poll jump).
- DSA Batch (`B = 1 → 2 → 4 → 8 → 16`, `4 KiB`, `W = 64 ops`, hot `512 KiB` `src`, `CC = 0` write-through `dst`):
  - Eliminates the `~131 ns/op` per-op drain bubble (`268.0 → 130.8 ns/op`): Card BAR4 P2P jumps **`15.28 → 23.35 → 31.13 → 31.25 → 31.32 GB/s`** (**2.05×**!).
  - At `B = 16`, Card BAR4 P2P matches/beats Host-RAM completion on BOTH throughput and `W = 1` batch latency: **`31.32` vs `31.10 GB/s`**, `p50` **`4.42` vs `4.63 µs`**, `p99` **`4.61` vs `5.75 µs`**.

## §S07 Executive Summary & Immediate Next Steps
- 3 verified pillars (`161.6 M/s` one-way + `897 ns` cache/DDIO & `64 B` coherence + `91–97 M/s` open-loop + `31.32 GB/s` zero-host-CPU Card → DSA P2P), 1 active open puzzle (`13 M/s` 4-core closed loop + `35–39 µs` `p99` tail), and immediate next hardware + system milestones.
