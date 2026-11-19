# FACTS — MRV-96 v2 video (host ⇄ OCTEON 10 DPU direct path, strict single-variable detective chain)

Every number below is recomputed from the raw runs by `data/verify_facts.py`
(`uv run -q --with matplotlib python docs/video-mrv96/data/verify_facts.py`, read-only; it asserts
lost = bad = torn = 0 on every run it uses).
Numbers are medians over repeated runs unless a line says "mean". `[INF]` = our interpretation, not a measurement.
Units: **M/s = million 64-byte messages per second**; µs / ns per message.
Rule: **ZERO internal issue IDs (`MRV-xx`) or raw `logs/mrvxx` paths** anywhere on screen or in speech.

## §Setup (S01)
- Host: Intel Xeon Gold 6438M, sending core pinned at **3.9 GHz**, uncore/mesh pinned at **2.2 GHz**.
- DPU: Marvell OCTEON 10 CN106, **24 Arm cores** at **2.0 GHz**. Link: **PCIe Gen5 ×4**.
- Message: **64-byte** cell (sequence number at both ends).
- Forward: one `MOVDIR64B` into a 65,533-cell (4 MiB) card-memory ring through PCIe BAR4; no doorbell, no DMA.
- Return: card core stores 64-byte reply into host reply ring with **write-combining (WC)** (`4 × 16 B = 64 B`).
- Central mystery: **161.6 M/s** one way vs **~13 M/s** closed loop (`W = 64`, 4 card cores) — **12×** slower.
- Integrity: **0 lost, 0 bad, 0 torn** on every plotted run.

## §E0 Sanity check — return traffic vs forward writes (S02)
- Fixed: host pinned **3.9 GHz**, uncore **2.2 GHz**, **1 receive + 1 reply core**, WC replies.
- Forward alone (replies OFF): **113.1 M/s** (n = 6, 112.32–113.58).
- Bidirectional (replies ON): **112.2 M/s** (n = 5, 111.21–112.29).
- Earlier unpinned sender (~**2.1 GHz**, 2.07–2.12 GHz): **75.6 M/s** (n = 10) → `[INF]` clock drop, not PCIe backpressure.

## §E1 Exp 1 — card frontier polling (S03)
- Fixed: one-way forward, host **3.9 GHz**, **2 card cores**, ≤ **32 cells/poll** (`< 32` = caught up).
- Knob: pause when caught up (`0 µs`, `1 µs`, `3 µs`):
  - `0 µs`: **128.7 M/s**, **93.6%** empty polls, **447** CHA entries / **3.47 µs**, 30.3 cycles/cell.
  - `1 µs`: **132.2 M/s** (`132–134` across pause modes), **433** entries / **3.28 µs**, ~198 cells/pause.
  - `3 µs`: **161.6 M/s** (**+26%**), **360** entries / **2.23 µs**, **~900** (`907`) cells/pause, **24.2 cycles/cell**, 0.7% empty polls.
- `[INF]`: ~447 writes in flight land slightly out of order, leaving temporary holes inside the ~3.5 µs landing band.

## §E2 Exp 2 — write-band conflict vs L3 bandwidth (S04)
- Fixed: one-way forward, **3 µs pause**, 2 card cores. Knob: **where** the core reads during each 3 µs pause.
- Reference (`0 µs` no pause): **128.3 M/s** (`497 M/s` L3 reqs, `81.9 M` snoops, `3.42 µs` CHA).
- Idle spin (read nothing): **160.4 M/s** (`608 M/s` L3, `67.9 M` snoops, `2.13 µs` CHA).
- Cold RAM outside ring (64 B stride): **160.4 M/s** (`815 M/s` L3).
- Cold RAM outside ring (4 KiB+64 B stride): **161.4 M/s** (`1,033 M/s` L3, > 1 billion/s).
- Old ring slots half a lap behind: **162.0 M/s** (`790 M/s` L3) / page stride **162.6 M/s** (`948 M/s` L3).
- Just behind frontier (`next−384..−64`): **160.2 M/s** (`728 M/s` L3); `next−1`: **159.8 M/s** (`607 M/s`).
- **Ahead inside active write band** (`next..next+384`): **113.4 M/s** (`472 M/s` L3, **111.9 M** snoops, **4.03 µs > 3 µs** CHA).
- `[INF]`: coherence snoops or inbound-write head-of-line blocking stretch queuing past 3 µs, creating feedback.

## §E3 Exp 3 — where time sits in the 13 M/s closed loop (S05)
- Fixed: closed loop, WC replies, **4 card cores**, synchronized card & host timestamps. Knob: **W = 1** vs **W = 64**.
- `W = 1` (pre-touched): `1.01 M/s`, RTT **0.92 µs** = **0.45 µs** fwd + **0.04 µs** card + **0.43 µs** ret.
- `W = 64` (pre-touched): **13.5 M/s** (`13.55 / 13.52`), RTT **3.7 µs** (`3.68 / 3.70`) = **3.11–3.21 µs** (`3.1–3.2`) fwd + **0.04 µs** card + **0.49–0.52 µs** (`~0.5`) ret (`+2.7 µs` forward).
- Host burst send gap: **9.1 ns**; card arrival gap: **~75 ns** (`76.5–77.3 ns`) → **~42** (`42.1–43.4`) messages queued in transit.
- Caveat (2 runs without pre-touched pages): **13.4 M/s**, RTT **3.8 µs** = **0.71–0.73 µs** (`0.7`) fwd + **0.04 µs** card + **2.67–2.71 µs** (`2.7`) ret.
- `[INF]`: ~13 M/s is not one stage's speed limit; the queue forms wherever the loop's rhythm puts it.

## §E4 Exp 4 — who owns the critical path (S06)
- Fixed: closed loop, WC replies, **20M** messages/run. Knob: inject fenced dependent-multiply delay **δ** on ONE side.
- Calibration (`W = 1`, 1 card core): host `+0 / +10 / +20 / +40 ns` → `924.2 / 934.9 / 944.7 / 964.3 ns` (**+40.1 ns**, slope **1.01**).
- Fast state (1 card core, `W = 1024`, slope-series base `24.8 ns/msg = 40.3 M/s`): host slope **1.07**, card slope **1.07**.
- Slow state (4 card cores, `W = 64`, base **76.1 ns/msg = 13.1 M/s**):
  - Card `+32 / +45 / +59 ns` → `75.9 / 75.7 / 75.6 ns` (slope **−0.01**).
  - Host `+0 / +10 / +20 / +40 ns` → `76.2 / 60.9 / 61.4 / 78.3 ns` (**+10 ns = 20% faster**!).
- 4-core clue (`W = 1024`, newer build): **1 card core 50.5 M/s** vs **4 card cores 23.9 M/s**; host send **9.2 vs 9.3 ns**, reply polling **13.6 → 32.5 ns** across 4 reply sub-rings.
- `[INF]`: both CPUs are mostly idle in the slow state; spacing host bursts helps break the slow rhythm.

## §E6a & §E6b Exp 5 — ruling out two 4-core hardware suspects (S07)
- **5a Write-combining**: fixed closed loop, 4 card cores, `W = 64`; knob contiguous vs interleaved across **64 KB** regions.
  - Full 64 B writes (ItoM) per reply: **1.0001 vs 1.0000** (**99.99%** full lines in both).
  - Throughput: **13.14 vs 13.16 M/s**. Why: `4 × 16 B = 64 B` fills one WC line completely.
  - Caveat (`W = 1024`): `21.88 → 21.22 M/s` (`−3%`, unexplained), still `99.99%` full lines.
- **5b Card L3 coherence**: Linux reports **128-byte** coherency line. Fixed: 2 card cores rewriting own 64 B cells; knob spacing `d`:
  - `0 B`: **72.3 M/s** per core (`17.8 M/s` snoops).
  - `64 B`: **259.8 M/s** per core (`0.52 M/s` snoops = idle `0.52 M/s`).
  - `128 B / 256 B / 4 KB`: **259.8 M/s** (`0.53–0.55 M/s` snoops).
  - Caveat: core-to-core only; PCIe writes beside a polling core remain untested.

## §E5 Exp 6 — breaking the loop: open-loop bidirectional (S08)
- Fixed: **1 host core + 1 card core**, WC replies. Knob: closed-loop **lockstep** vs **open-loop duplex**.
- Lockstep (`W = 256`): **14.2 M/s**.
- Open loop (`5–35 M/s` each way): delivered cleanly (`70 M/s` combined at `35 + 35`).
- At `35 + 35 M/s`: median `p50` **0.97 µs** fwd / **1.24 µs** ret; `p90` **5.55 µs** fwd; **p99 38.7 µs** fwd / **13.9 µs** ret.
- At `20 + 20 M/s`: forward `p50` **16.9 µs**.
- Peak unthrottled: `1c` = **41.9 + 41.9 = 83.7 M/s**; `2c split` WC = **91.0 M/s** (`45.5 + 45.5`); `2c split` DMA = **97.3 M/s** (`49.3 + 47.9`).
- Open-loop request–response echo (`1 card core`): RTT `p99` = **1.03 µs** (`5 M/s`), **1.26 µs** (`10 M/s`), **35.4 µs** (`13.5 M/s`), **38.5 µs** (`20 M/s`).

## §Next (S09)
- Kill slow state & `35–39 µs` `p99` tail: pace host bursts, cap reply-poll batches, sweep mesh clock, measure long-run `p99`.
- Real systems: let card cores directly drive host **Intel DSA** copy engine; build a minimal **RPC / message layer** for real DPU-to-host offload.
