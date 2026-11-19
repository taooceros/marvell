# FACTS — MRV-96 video (host ⇄ OCTEON 10 DPU direct path, six single-variable experiments)

Every number below is recomputed from the raw runs by `data/verify_facts.py`
(`uv run -q --with matplotlib python docs/video-mrv96/data/verify_facts.py`, read-only; it asserts
lost = bad = torn = 0, plus the card-side partial/corrupt counters where a run records them, on every run it uses).
Raw data lives in this checkout's `logs/`; the MRV issues on Linear hold the full reports and reviews.
Numbers are medians over repeated runs unless a line says "mean". `[INF]` = our interpretation, not a measurement.
Units: **M/s = million 64-byte messages per second**; µs / ns per message.

## §Setup (hardware, conditions)
- Host `saturn`: Intel Xeon Gold 6438M (`lscpu`: 2 sockets × 32 cores). Sending core = cpu40, performance
  governor, 3.9 GHz; uncore/mesh pinned at 2.2 GHz on both packages (run records: `clocks` / `freq_*`).
- DPU: Marvell OCTEON 10 CN106xx (`crb106-pcie` card), 24 Arm cores at 2.0 GHz
  (`logs/dpu_ttyUSB0.log`: `CPUs=24`, `CORECLK: 2000 Mhz`). Link: PCIe Gen5 ×4 (AGENTS.md).
- Message = one 64-byte cell (one cache line). Forward: one `MOVDIR64B` per message into the card's request
  ring (65,533 cells, 4 MiB) through the host PF BAR4 window; no doorbell, no DMA. Every cell carries a sequence
  number at both ends; receivers check completeness and order (torn / bad / late counters).
- Return: a card core stores the 64-byte reply into the host's reply ring through a PCIe outbound window with
  write-combining (`--store-wc`): four 16-byte stores leave as one 64-byte line write.
- Closed loop = the host keeps at most W messages in flight; a reply frees a slot. Open loop = each side sends
  at a set rate, independently.

## §Method
- One knob per experiment, everything else fixed (MRV-82, "Occam's razor").
- Every plotted/quoted run verified every message: lost = bad = torn = 0 (`verify_facts.py` asserts it).
- Shown latencies are medians (p50) unless labelled p90 / p99.

## §E0 Sanity check — reply traffic does not slow the forward path (MRV-24 → MRV-84)
Source: `logs/mrv24-2026-11-17/{runs,shell-runs,card}.jsonl`, run selection of `docs/figures/mrv-24-summary.py`;
card = 1 receive core + 1 reply core, write-combining replies.
- Forward alone, sender pinned 3.9 GHz: **113.1 M/s** (n = 6, 112.32–113.58).
- Forward with replies flowing (bidir), sender pinned 3.9 GHz: **112.2 M/s** (n = 5, 111.21–112.29).
- Bidir with the sender on an unpinned core measured at **2.07–2.12 GHz**: **75.6 M/s** (n = 10).
- Caveat (MRV-94 review): a one-way run at ~2.1 GHz was never measured, so "the clock caused the drop" is `[INF]`.

## §E1 Experiment 1 — pausing at the write frontier (MRV-61 → MRV-85)
Source: `logs/mrv61-2026-11-18/runs.jsonl` (27 runs × 2×10⁸ cells, all verified). Fixed: one-way (h2c) only,
host cpu40 3.9 GHz, **2 card receive cores**, each poll takes ≤ 32 cells ("< 32" = caught up with the frontier).
Knob: pause when caught up (`--poll-gap-ns`, mode `spin`).
| pause | M/s (median, range, n) | host write queue (CHA occupancy, residency) | other |
|---|---|---|---|
| 0 µs | **128.7** (125.2–129.1, n = 6) | **447** entries, **3.47 µs** | **93.6 %** of polls empty; 19.3 cells per busy poll; cpu40 30.3 cycles/cell |
| 1 µs | **132.2** (132.15–132.49, n = 3) | **433**, **3.28 µs** | ~198 cells per pause (all three 1 µs modes: 132.2–134.5 M/s) |
| 3 µs | **161.6** (161.5–161.95, n = 3) | **360**, **2.23 µs** | **~900** (907) cells per pause; 0.7 % empty polls; cpu40 **24.2 cycles/cell** |
- 161.6 / 128.7 = 1.256 → **+26 %**. At 3 µs the host core's own issue cost (24.2 cycles at 3.9 GHz ≈ 6.2 ns) is the limit.
- "~440 writes in flight, each queued ~3.5 µs" = CHA occupancy at 0 µs (447 entries, 3.47 µs), measured by host
  uncore counters. That the writes land **out of order** and leave holes is `[INF]` (inferred from occupancy via
  Little's law, not measured — MRV-94).
- With replies flowing (bidir, 0 µs) the forward path ran 158.4–159.2 M/s (not shown; MRV-94 Q6, `[INF]`).

## §E2 Experiment 2 — position, not bandwidth (MRV-76)
Source: `logs/mrv76-2026-11-18/runs.jsonl` (33 runs × 2×10⁸ cells, all verified; card TAD counters 100 % enabled).
Fixed: as E1, pause 3 µs. Knob: what the card core reads during each pause. "L3 requests" = `tad_req_msh_in_any`
per second (all requests into the card's shared L3/TAD).
| pause action | M/s (median, range) | L3 (TAD) requests/s |
|---|---|---|
| no pause (0 µs, reference) | 128.3 (127.9–130.0) | 497 M |
| spin (read nothing) | **160.4** (160.1–160.6) | **608 M** |
| cold reads outside the ring, 64 B stride | **160.4** (160.2–160.7) | **815 M** |
| cold reads outside the ring, 4 KiB+64 B stride | **161.4** (161.1–161.5) | **1033 M** (> 1 billion/s) |
| old ring slots half a lap behind (`next−32768..−16384`) | **162.0** (161.6–162.1) | 790 M |
| same, page stride | **162.6** (162.56–162.62) | 948 M |
| slots just behind the frontier (`next−384..−64`, landed) | **160.2** (159.7–160.2) | 728 M |
| the slot right behind (`next−1`) | 159.8 (159.7–159.9) | 607 M |
| **ahead of the frontier** (`next..next+384`, in-flight band) | **113.4** (113.1–113.8) | 472 M |
- In the write-band case host CHA residency rises to **4.03 µs (> 3 µs pause)**, TAD snoops to 111.9 M per run
  (vs 67.9 M for spin), and pauses per run grow ~2.6×. Mechanism (snoop invalidation of not-yet-landed lines, or
  inbound write head-of-line blocking, amplified by too-short pauses) is `[INF]`.

## §E3 Experiment 3 — where the extra time waits in the closed loop (MRV-50 → MRV-86)
Source: `logs/mrv50-2026-11-17/runs.jsonl` (`stamp_summary` of stamped runs; card-side timestamps synced to
the host clock). Fixed: closed loop, write-combining replies, **4 card cores**, poll batch 32. Knob: W = 1 vs 64.
| run | W | M/s | RTT p50 | forward p50 | on card p50 | return p50 |
|---|---|---|---|---|---|---|
| 188/189 (pages pre-touched) | 1 | 1.006 / 1.007 | **0.92 µs** | **0.45** | **0.04** | **0.43** |
| 190/191 (pages pre-touched) | 64 | **13.55 / 13.52** | 3.68 / 3.70 (host p50 3.7) | **3.11 / 3.21** | **0.04** | **0.52 / 0.49** |
| 113/114 (pages **not** pre-touched) | 64 | **13.39 / 13.38** | 3.82 / 3.83 | **0.73 / 0.71** | 0.04 | **2.67 / 2.71** |
- Extra forward time at W = 64 vs 1: 3.11 − 0.45 ≈ **2.7 µs**. Round trip "3.7 µs" = host RTT p50 of runs 190/191.
- Host send gap inside a burst: p50 **9.1 ns**; card arrival gap: mean 76.5 / 77.3 ns (≈ 1 / 13.5 M/s = 74 ns)
  → "**~75 ns**". Requests waiting in the forward path (Little's law: rate × forward time) =
  13.55 M/s × 3.11 µs = **42.1** (run 191: 43.4) → "**about 42**".
- Product-mode (no stamps) W = 64 runs 101/102: 13.14 / 13.16 M/s; W = 1 runs 99/100: 1.04 M/s.
- Caveat (MRV-94): the queue sits on the return side in runs 113/114 at the same rate, so "13 M/s = one stage's
  service rate" is wrong; "the queue forms where the loop's rhythm puts it" is `[INF]`.

## §E4 Experiment 4 — who sets the pace (MRV-53 → MRV-88)
Source: `logs/mrv53-2026-11-17/runs.jsonl`, analysis functions of `docs/figures/mrv-53-slopes.py`
(warm runs, id ≥ 210) and `docs/figures/mrv-53-phases.py`. W = 64 / 1024 runs send **2×10⁷** messages each;
W = 1 runs 2×10⁶. Delay δ = dependent multiply chain between two fences (host `lfence`, card `isb`).
- W = 1 calibration (WC, 1 card core): host +0 / +10 / +20 / +40 ns → **924.2 / 934.9 / 944.7 / 964.3 ns** per
  message (**+40 ns → +40.1 ns**; host slope **1.01**; card slope 1.11).
- Fast state (WC, 1 card core, W = 1024; this series' baseline 24.8 ns/msg = 40.3 M/s): host slope **1.07**,
  card slope **1.07**.
- Slow state (WC, 4 card cores, W = 64; baseline **76.1 ns/msg = 13.14 M/s**): card +32 / +45 / +59 ns →
  75.9 / 75.7 / **75.6** ns (slope **−0.01**). Host +10 / +20 / +40 ns → **60.9** / 61.4 / 78.3 ns from **76.2**
  (adding 10 ns makes it ~20 % faster; the two-point "host slope 0.05" hides this — MRV-94).
  "Bursts, not code, trigger the slow state" is `[INF]` (pacing test MRV-75 not run yet).
- W = 1024, newer host build (H2): **1 card core 50.5 M/s** (50.08, 50.85) vs **4 card cores 23.9 M/s**
  (23.95, 23.90). Host time per message (timed runs): send **9.2** vs **9.3 ns**, reply polling **13.6** vs **32.5 ns**.
  (Same 4-core config with the older H0 build: 22.55 M/s.)

## §E5 Experiment 5 — remove the loop (MRV-70 → MRV-87)
Source: `logs/mrv70-2026-11-18/runs.jsonl` (483 runs × 300 ms, 3 reps per point, all verified). Fixed: host cpu40 only;
card config `1c_wc_duplex` = **1 card core** that both receives and replies with write-combining. Knob: lockstep
(closed loop) vs open loop (`--dir duplex`, both directions at a set rate R).
- Lockstep W = 256: **14.2 M/s** (13.9–14.2). (Same card core: W = 32 18.4, W = 64 32.7, W = 1024 39.3 — non-monotonic.)
- Open loop R = 5…35 M/s each way: delivered exactly (e.g. 35.0 + 35.0).
- R = 35 + 35: median one-way latency forward **0.97 µs**, return **1.24 µs**; forward p90 **5.55 µs**, forward
  **p99 38.7 µs**, return p99 13.9 µs. R = 13.5 + 13.5: p50 0.71 / 0.64 µs. R = 20 + 20: forward **p50 16.9 µs**.
- Unthrottled: **41.9 + 41.9 = 83.7 M/s** (1 host core + 1 card core).
- Card receive and send on two cores (`1p1_*_split`, still 1 host core): WC replies **91.0 M/s** total
  (45.5 per direction; 90.0–95.9), DMA (DPI) replies **97.3 M/s** (94.1–97.9).
- Request–response with open-loop arrivals (`open_echo`, reply triggered by the request), 1 card core: RTT p99
  1.03 µs at 5 M/s, **1.26 µs at 10 M/s**, **35.4 µs at 13.5 M/s**, 38.5 µs at 20 M/s. With 4 card cores at 13.5 M/s
  even the RTT p50 is 38.6 µs.
- More card cores do not help yet (open loop, unthrottled, per direction): 2 + 2 cores 26.1, 4 cores 24.3 M/s.
- Each run collects ~2,000 latency samples near its end; no p99.9; cores not isolated (MRV-94).

## §E6a Experiment 6a — interleaved replies keep write-combining (MRV-54 → MRV-89)
Source: `logs/mrv54-2026-11-18/runs.jsonl` (4 passes × 2×10⁷ messages per point, all verified). Fixed: closed loop,
write-combining replies, 4 card cores, W = 64. Knob: replies contiguous in one 64 KiB region (`base`) vs
interleaved across regions by tag (`base-interleaved`). Means over the 4 passes.
- Full 64-byte line writes (ItoM) per reply: **1.0001** vs **1.0000**; ItoM share of line writes **99.99 %** in both.
- Throughput: **13.14** vs **13.16 M/s**.
- Caveat (MRV-94): at W = 1024 the same change costs ~3 %: **21.88 → 21.22 M/s**, cause not investigated.

## §E6b Experiment 6b — card-core coherence is per 64 bytes (MRV-55 → MRV-90)
Source: `logs/mrv55-2026-11-18/runs.jsonl` (`mode = write_only`, 1 s × 5 per point, all verified). Fixed: card
cores 3 and 4 each rewrite their own 64-byte cell in 4 KiB-aligned shared memory. Knob: distance d between cells.
| d | writes per core (mean of both cores) | TAD snoops |
|---|---|---|
| 0 B (same cell) | **72.3 M/s** (core 3 63.7, core 4 80.8) | **17.8 M/s** |
| 64 B | **259.8 M/s** | 0.52 M/s |
| 128 B | 259.8 M/s | 0.53 M/s |
| 256 B | 259.8 M/s | 0.55 M/s |
| 4 KiB | 259.8 M/s | 0.55 M/s |
| idle (no writes) | — | **0.52 M/s** |
- Linux on the card reports `coherency_line_size = 128` for L3 (MRV-90 text).
- Caveat: only core ↔ core; PCIe inbound writes next to a polling core (MRV-78) are untested.

## §Next (open items on Linear)
- Explain/remove the slow state: pace host bursts (MRV-75), cap reply polling batch (MRV-73), sweep the mesh clock
  (MRV-94 item 1a); report p99 over long runs (MRV-94 items 3, MRV-27).
- Card cores writing 64-byte descriptors directly into the host's Intel DSA portal (MRV-95, in progress).
- A minimal message layer + a real request–response workload (MRV-94 "system" suggestion, e.g. gRPC offload).

## Differences from the dispatcher's brief (data wins)
- 0 B card-core writes: **72.3 M/s per core** (runs.jsonl, MRV-90), not "24 M/s".
- 4-core closed loop at W = 1024: shown as **23.9 M/s** (same H2 runs as the 9.3 / 32.5 ns breakdown); the
  older-build baseline is 22.55 M/s.
- W = 1 return time is **0.43 µs** (0.45 is the forward time). W = 64 forward time is measured as **3.11–3.21 µs**
  (p50), with ~42 requests waiting (Little's law), instead of the derived "~45 × 75 ns = 3.2–3.6 µs".

## On-screen numbers allowed per scene
- S01: 64-byte, 161.6 M/s, ~13 M/s, 12×, six, one knob, 1–2 / 3–4 / 5 / 6, 0 lost, 0 corrupt.
- S02: 3.9 GHz, 24 Arm cores, 2.0 GHz, Gen5 ×4, 64-byte, 16-byte ×4, 113.1, 112.2, 75.6, ~2.1 GHz, 1 receive + 1 reply core.
- S03: 2 card cores, 32 per poll, ~440 in flight, ~3.5 µs, 0 / 1 / 3 µs, 93.6 %, 128.7, 132.2, 161.6, ~900, 447, 433, 360, 3.47 / 3.28 / 2.23 µs, ~24 cycles, +26 %, 65,533 slots (real ring).
- S04: 3 µs, 160.4, 608, 815, 1033 (> 1 billion), 161.4, 162.0, 162.6, 160.2, 159.8, 113.4, 4.0 µs, 128.3 (reference), ~2.6×, 65,533 slots (real ring).
- S05: W = 1 / 64, 4 card cores, 0.92, 0.45, 0.04, 0.43, 13.5 M/s, 3.7 µs, 3.1–3.2 µs forward (bar segments may show 3.11 | 0.04 | 0.52), ~0.5 return, ~2.7 µs, 9 ns, ~75 ns, ~42, 13.4, 0.7, 2.7 (bar segments may show 0.73 | 0.04 | 2.67), ~13 M/s, 2 runs (113/114) without pre-touched pages.
- S06: δ, +10 / +20 / +40 ns, 924.2 / 934.9 / 944.7 / 964.3, +40.1, slope 1.01, 20 million, W = 1 / 64 / 1024, 1.07 / 1.07, 76.1 → 75.6 (75.9 / 75.7), +32 / +45 / +59, 76.2 → 60.9, 61.4, 78.3, 50.5 vs 23.9, 9.2 / 9.3 ns, 13.6 → 32.5 ns, lfence / isb.
- S07: 1 host core + 1 card core, 14.2, ~13 M/s (the wall), 5 / 10 / 13.5 / 20 / 25 / 30 / 35 each way, 35 + 35, 0.97, 1.24, 41.9 + 41.9 = 83.7, 91 (WC) / 97 (DMA) with 2 card cores, p90 5.55, p99 38.7, 20 + 20 p50 16.9, request–response RTT p99 1.03 (5 M/s), 1.26 (10), 35.4 (13.5), 38.5 (20), 26.1 / 24.3.
- S08: 64 KB regions, 4 card cores, W = 64, 1.0001 vs 1.0000, 99.99 %, 13.14 vs 13.16, 4 × 16 B = 64 B, W = 1024 21.88 → 21.22 (−3 %).
- S09: 128-byte, 64-byte, d = 0 / 64 / 128 / 256 B / 4 KB, 72.3 (63.7 / 80.8), 17.8, 259.8, 0.52.
- S10: 161.6, ~13, 83.7, ~1 µs, tens of µs, 3 µs, +26 %, 99.99 %, 64-byte, DSA, Exp 1–6 labels, card-core counts in words (two for 161.6, four for ~13, one for 83.7).
