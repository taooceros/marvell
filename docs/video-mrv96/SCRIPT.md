# Direct PCIe Datapath Advisor Progress Report — Verified Direct-MMIO Findings, Active 13 M/s Closed-Loop Puzzle & Zero-Host-CPU Card → Intel DSA P2P

Raw data: `logs/mrv{24,50,53,54,55,61,70,76,95,97,99}-*/` and `../marvell-mrv106/logs/mrv106-*/` (every plotted run verified, lost = bad = torn = 0). Every on-screen number is recomputed by `data/verify_facts.py` and listed with its source in `data/FACTS.md`; `[INF]` marks our interpretations (spoken as "our reading").

## Build

1. Edit captions/narration only in `tts/script_data.py`, then `python3 tts/gen_scenes.py --script` (rewrites only the `// <cues>` blocks and this file).
2. Narration: `bun tts/manifest.ts && ~/tts/asr/bin/python tts/synth.py` (edge-tts en-US-AndrewNeural +0%, cached by text), then rerun `python3 tts/gen_scenes.py --script` to retime.
3. Check: `npx tsc -p .`; `./stills.sh S05 3 9 15` → half-size contact sheet `out/S05-sheet.png`.
4. Final: `./render.sh` → `out/mrv96-six-experiments.mp4` (1920×1080 @ 30 fps); per-cue QA sheets: `python3 tts/qa_frames.py` → `out/qa/S*.png`.

## Scenes

| Scene | Title | Length | Cues | Source |
|---|---|---|---|---|
| S01 | Direct PCIe path & 897 ns baseline | 70 s | 5 | Xeon host ⇄ OCTEON 10 DPU · PCIe Gen5 ×4 · advisor progress report |
| S02 | Nailed down #1 · Why one-way hits 161.6 M/s | 86 s | 5 | Single-variable forward tests · 81 verified runs · 0 lost, 0 corrupt |
| S03 | Nailed down #2 · Cache/DDIO & 64 B coherence | 77 s | 5 | W = 1 cache/DDIO proof (40 runs) · card L3 spacing sweep (155 runs) |
| S04 | Nailed down #3 · Open-loop breaks lockstep | 72 s | 5 | Lockstep vs open-loop duplex · 1 host core · 483 verified runs |
| S05 | Open puzzle · Why 4-core closed loop stalls | 90 s | 6 | Active investigation · timestamps, delay slopes & WC layout · next on card |
| S06 | New breakthrough · Zero-host-CPU Card → DSA P2P | 92 s | 5 | Card DPI DMA ⇄ Host Intel DSA2 ⇄ Card BAR4 · 182.9M+ verified ops |
| S07 | Executive summary & immediate next steps | 66 s | 4 | Advisor progress summary · verified pillars, open puzzle & roadmap |

## Cues (caption / narration / provenance)

### S01 Direct PCIe path & 897 ns baseline (70 s)

1. `   0.6s` Our direct datapath links an **Intel Xeon** host (`3.9 GHz` core, `2.2 GHz` uncore) to a **Marvell OCTEON 10 DPU** (24 Arm cores, `2.0 GHz`) over **PCIe Gen5 ×4**.
   - spoken: Our direct datapath links an Intel Xeon host at three point nine gigahertz to a Marvell Octeon ten D P U with twenty-four Arm cores over P C I e Gen five by four.
   - source: §S01
2. `  12.8s` Forward: one `MOVDIR64B` writes a **64-byte** cell directly into card memory (no doorbell, no DMA). Return: a card core sends a **64-byte** write-combining reply to host RAM.
   - spoken: Forward, one move dir sixty-four B writes a sixty-four byte message straight into card memory, no doorbell, no D M A. Return, a card core writes a sixty-four byte combining reply to host memory.
   - source: §S01
3. `  26.9s` At `W = 1`, round-trip latency is **897 ns** (`~429 ns` one way). Three same-machine anchors explain the budget: on-die **DSA NOP** takes **403 ns** (zero PCIe hops).
   - spoken: At window one, round-trip latency is eight hundred ninety-seven nanoseconds. Three same-machine anchors explain why: an on-die host D S A no-op with zero P C I e hops already takes four hundred three nanoseconds.
   - source: §S01
4. `  42.5s` Anchor 2: raw **BAR4 PCIe read** (no card core) takes **646 ns** (`+243 ns` link **[INF]**). Anchor 3: full core-to-core hop reaches **897 ns** (`+212 ns` cache/core **[INF]** + `40 ns` card).
   - spoken: Second, a raw B A R four P C I e read without a card core takes six hundred forty-six nanoseconds. Third, adding the card core hop brings the full round trip to eight hundred ninety-seven.
   - source: §S01 [INF]
5. `  55.7s` Our honest progress report in 3 parts: **(1) three direct-MMIO findings nailed down**, **(2) one open puzzle** (`13 M/s` closed loop), and **(3) our new 31.3 GB/s Card → DSA breakthrough**.
   - spoken: Our progress report has three honest parts: three direct-path findings nailed down, the one open puzzle we are actively dissecting on hardware, and our new thirty-one gigabyte per second card-to-D S A breakthrough.
   - source: §S01

### S02 Nailed down #1 · Why one-way hits 161.6 M/s (86 s)

1. `   0.6s` **Finding 1 (One-way ceiling):** Sanity check first — pinned at `3.9 GHz`, return replies OFF vs ON run **113.1 vs 112.2 M/s**. Return traffic is innocent; an earlier **75.6 M/s** drop was unpinned **~2.1 GHz** **[INF]**.
   - spoken: Finding one: how fast is one-way forward? First, pinned at three point nine gigahertz, turning return replies on gives one hundred thirteen point one versus one hundred twelve point two million. Return traffic is innocent; an earlier drop to seventy-five point six was an unpinned clock, our reading.
   - source: §S02 [INF]
2. `  19.3s` With **2 card receive cores**, pure one-way still stalls at **128.7 M/s**. **Knob 1:** pause when caught up (`< 32 cells`): **0 µs** = **128.7** (`93.6%` empty); **1 µs** = **132.2**; **3 µs leaps +26% to 161.6 M/s** (`24.2 cyc/msg`).
   - spoken: With two card receive cores, one-way still stalls at one hundred twenty-eight point seven million. Turning one knob, pause when caught up: zero microseconds hits one twenty-eight point seven, one gives one thirty-two point two, and three microseconds leaps twenty-six percent to one hundred sixty-one point six million.
   - source: §S02
3. `  38.2s` **Why?** Host counters show **447 writes** queuing **3.47 µs** at `0 µs` (**[INF]** out-of-order landing); `3 µs` lets **~900 cells** settle (`360` queued). Did pausing avoid the **write band**, or free **L3 bandwidth**?
   - spoken: Host counters show four hundred forty-seven writes queuing three point five microseconds, our reading being out-of-order landing. Three microseconds lets nine hundred cells settle cleanly. Did pausing avoid the active write band, or simply free card L 3 read bandwidth?
   - source: §S02 [INF]
4. `  55.0s` **Knob 2 (Read target during 3 µs pause):** Cold RAM and old ring slots push card L3 reads to **815–1,033 M/s** (> 1 billion/s), yet forward stays flat at **160.2–162.6 M/s**.
   - spoken: Keeping the three microsecond pause fixed and varying where the core reads: cold memory and old ring slots push card L 3 requests past one billion per second, yet forward throughput stays flat near one hundred sixty-one million.
   - source: §S02
5. `  69.6s` **Only reading ahead inside the active write band** crashes forward to **113.4 M/s** (`472 M/s` L3, snoops `67.9 → 111.9 M`, queue `4.03 µs > 3 µs` **[INF]**). Verdict: **write-band conflict, not L3 bandwidth**.
   - spoken: Only reading ahead inside the active write band crashes throughput to one hundred thirteen point four million, stretching host queuing past four microseconds. Our reading: snoops or write blocking feed back. Verdict: location, not bandwidth.
   - source: §S02 [INF]

### S03 Nailed down #2 · Cache/DDIO & 64 B coherence (77 s)

1. `   0.6s` **Finding 2:** In our **897 ns** `W = 1` baseline, are all 3 cache/DDIO shortcuts already active? We flush **one cache level at a time** and check latency plus PMU counters.
   - spoken: Finding two: in our eight hundred ninety-seven nanosecond baseline, are all three cache and D D I O shortcuts already active? We flush one cache level at a time and check latency plus hardware counters.
   - source: §S03
2. `  14.0s` **Shortcut 1 (Host L1D source):** Flushing the host source line to cold DRAM before `MOVDIR64B` adds **+117.3 ns** end-to-end (`897.3 → 1,014.5 ns`).
   - spoken: First, flushing the host source buffer to cold D RAM before sending adds one hundred seventeen point three nanoseconds to end-to-end round-trip latency.
   - source: §S03
3. `  23.6s` **Shortcut 2 (Card L3 write-allocate):** Flushing the card slot before arrival keeps card read flat (`104 → 102 ns`) while `tad_alloc_ltg` jumps **+1.00/msg**; flushing after arrival to DRAM costs **+75.0 ns**.
   - spoken: Second, flushing the card slot before arrival keeps card read flat while L 3 allocations jump by one per message. Flushing after arrival to card D RAM adds seventy-five nanoseconds on the read side.
   - source: §S03
4. `  37.1s` **Shortcut 3 (Host Xeon DDIO):** Hot replies hit `io_hit_itom = 1.00` into host LLC. Flushing before arrival stays flat (`42.7 ns`); flushing after arrival to host DRAM costs **+87.3 ns**.
   - spoken: Third, on the return path, counters confirm one hundred percent host D D I O cache hits. Flushing before arrival stays flat at forty-two point seven nanoseconds, while forcing a host D RAM read adds eighty-seven point three.
   - source: §S03
5. `  52.7s` **64 B card coherence:** Linux reports a **128-byte** card L3 line. Sweeping spacing `d` between 2 card cores: **0 B** thrashes at **72.3 M/s** (`17.8 M/s` snoops), while **64 B** immediately hits **259.8 M/s** (`0.52 M/s` idle snoops) — same as `128 B–4 KiB`.
   - spoken: Finally, Linux reports a one hundred twenty-eight byte card L 3 line. Sweeping slot spacing between two card cores: zero bytes thrashes at seventy-two point three million with heavy snoops, while sixty-four bytes immediately hits full two hundred fifty-nine point eight million with idle snoops, identical to four kilobytes. Card coherence is strictly sixty-four bytes.
   - source: §S03

### S04 Nailed down #3 · Open-loop breaks lockstep (72 s)

1. `   0.6s` **Finding 3:** Can the PCIe link sustain high **bidirectional** traffic without lockstep? Fixed: **1 host core + 1 card core**, WC replies. Knob: **lockstep vs open-loop duplex**.
   - spoken: Finding three: can the link sustain high bidirectional traffic without lockstep? Fixed: one host core, one card core, write-combining replies. One knob: closed-loop lockstep versus independent open-loop duplex.
   - source: §S04
2. `  14.6s` **Wall disappears:** Lockstep (`W = 256`) stalls at **14.2 M/s**. Open loop delivers **5–35 M/s each way** (`70 M/s` combined) at **~1 µs** median (`0.97 µs` fwd, `1.24 µs` ret).
   - spoken: The wall disappears. Lockstep stalls at fourteen point two million, whereas open loop delivers five to thirty-five million each way, seventy million combined, at about one microsecond median.
   - source: §S04
3. `  26.9s` **Peak capacity (1 host core):** Unthrottled `1 card core` hits **41.9 + 41.9 = 83.7 M/s**. Splitting card RX & TX onto **2 cores** drives **91.0 M/s** (WC) and **97.3 M/s** (`49.3 + 47.9`, DMA).
   - spoken: Unthrottled, one host core and one card core hit eighty-three point seven million combined. Splitting card receive and send onto two cores drives ninety-one million with write combining, and ninety-seven point three with D M A.
   - source: §S04
4. `  41.0s` **Honest caveat — tails:** At `35 + 35 M/s`, forward **p99 spikes to 38.7 µs** (`p90 = 5.55 µs`; at `20 + 20`, even `p50` hits **16.9 µs**).
   - spoken: Honest caveat: those one microsecond figures are medians. At thirty-five plus thirty-five, ninety-ninth percentile forward latency spikes to thirty-eight point seven microseconds, and at twenty plus twenty even the median hits sixteen point nine.
   - source: §S04
5. `  56.8s` **Echo cliff:** With request-triggered replies, RTT **p99 jumps** from **1.26 µs** (`10 M/s`) to **35.4 µs** at **13.5 M/s**. The link can fly — sharpening our one open puzzle.
   - spoken: With request-triggered replies, ninety-ninth percentile round trip jumps from one point two six microseconds at ten million to thirty-five point four at thirteen point five. The link can fly, which sharpens our one open puzzle.
   - source: §S04

### S05 Open puzzle · Why 4-core closed loop stalls (90 s)

1. `   0.6s` **The open puzzle:** By Little's Law at `0.92 µs` RTT, `W = 64` should saturate near **~50 M/s** — and **1 card core at W = 1024 DOES hit 50.5 M/s**!
   - spoken: Now the one open puzzle we have not solved yet. By Little's Law at zero point nine two microseconds round trip, window sixty-four should approach fifty million per second. Indeed, one card core at window ten twenty-four hits fifty point five million.
   - source: §S05
2. `  15.4s` Yet **4 card cores** collapse to **13.1–13.5 M/s** at `W = 64..256` (and only **23.9 M/s** at `W = 1024`, where host polling grows `13.6 → 32.5 ns` across 4 sub-rings). Why?
   - spoken: Yet four card cores collapse to thirteen million across windows sixty-four to two fifty-six, and reach only twenty-three point nine million at window ten twenty-four, where host polling swells across four sub-rings. Why?
   - source: §S05
3. `  29.3s` **Clue 1 (Timestamps):** At `W = 64` (`13.5 M/s`, `3.7 µs` RTT), card work stays **0.04 µs** while **~42 msgs** queue (`3.1–3.2 µs` forward — flipping to **return** `0.7 / 2.7 µs` in 2 runs without page pre-touch).
   - spoken: First clue: timestamps show card work stays zero point zero four microseconds, while forty-two messages pile up in transit, three point one microseconds forward, though flipping to return in two runs without pre-touched pages.
   - source: §S05
4. `  44.0s` **Clue 2 (Both CPUs idle):** Delay injection (`+δ`, `20M` msgs) shows card slope **−0.01** (`76.1 → 75.6 ns`), while **+10 ns on host speeds it up +20%** (`76.2 → 60.9 ns` **[INF]** burst spacing).
   - spoken: Second clue: delay slopes prove neither processor is maxed out. Adding fifty-nine nanoseconds on the card changes nothing, and adding ten nanoseconds on the host actually speeds it up twenty percent, our reading being burst spacing.
   - source: §S05 [INF]
5. `  58.2s` **Already ruled out:** Interleaved 4-core WC stays **99.99%** full 64 B writes (`13.14 vs 13.16 M/s`), and card L3 coherence is **64 B**. Neither causes the `13 M/s` throttle.
   - spoken: We also cleared both obvious four-core hardware suspects: interleaved write-combining stays ninety-nine point nine nine percent full writes, and card L 3 coherence is sixty-four bytes.
   - source: §S05
6. `  70.2s` **Honest status (running next on card):** We have **NOT** yet pinned down which hardware queue throttles those ~42 writes to `~75 ns/msg`. Next on hardware: `MOVDIR64B` burst pacing + host Store Buffer / CHA / IIO vs card TAD counters.
   - spoken: Honest status: we have not yet identified which exact hardware queue throttles those forty-two in-flight writes to seventy-five nanoseconds per message. That experiment is queued next on our card, sweeping host burst pacing with store-buffer, C H A, I I O, and card T A D counters.
   - source: §S05

### S06 New breakthrough · Zero-host-CPU Card → DSA P2P (92 s)

1. `   0.6s` **New breakthrough:** Let the DPU drive the host's **Intel DSA** copy engine directly over bidirectional PCIe P2P — **zero host CPU** in the data path!
   - spoken: Meanwhile, we achieved a major systems breakthrough: letting the D P U drive the host's Intel D S A copy engine directly over bidirectional P C I e peer-to-peer, with zero host C P U in the data path.
   - source: §S06
2. `  15.2s` **Forward P2P (Card → DSA portal):** Card **DPI DMA** 64 B descriptor writes hit **0 drops across 182.9M+ verified ops** — whereas card **WC** portal writes suffer **0.048%–1.217%** silent drops.
   - spoken: Going forward to the host D S A portal, card D P I D M A delivered zero drops across one hundred eighty-two million verified operations, whereas direct write-combining suffered silent drops up to one point two percent.
   - source: §S06
3. `  30.8s` **Reverse P2P (DSA → Card BAR4):** Writing DSA's 32 B completion straight into **Card BAR4** (vs host RAM) cuts `W = 1` period: `NOP` **1.96 → 1.27 µs**, `4 KiB` **3.13 → 2.17 µs**, slashing jitter **1,119 → 163 ns** (`−85%`).
   - spoken: Closing the reverse path into card B A R four cuts single-op round trip from one point nine six to one point two seven microseconds for no-ops, three point one three to two point one seven for four kilobyte copies, and slashes tail jitter eighty-five percent, from one thousand one hundred nineteen down to one hundred sixty-three nanoseconds.
   - source: §S06
4. `  52.5s` **DSA Batch (B = 1 → 16):** Single-op BAR4 P2P hit **15.28 GB/s** (`~131 ns/op` drain bubble). Batching `B = 2, 4, 8, 16` (`4 KiB`, hot `512 KiB` src, `CC = 0`) jumps **2.05× to 31.32 GB/s**!
   - spoken: Single-op B A R four completion initially capped at fifteen point two eight gigabytes per second due to a one hundred thirty-one nanosecond drain bubble. Turning one knob, D S A batch size from one to sixteen, eliminates that bubble and doubles throughput to thirty-one point three two gigabytes per second.
   - source: §S06
5. `  71.9s` **Matches/beats host-RAM completion on BOTH axes:** At `B = 16`, Card BAR4 P2P delivers **31.32 vs 31.10 GB/s**, lower `p50` (**4.42 vs 4.63 µs**), and tighter `p99` (**4.61 vs 5.75 µs**) — zero host CPU!
   - spoken: At batch sixteen, bidirectional card-to-D S A peer-to-peer matches or beats host-memory completion on both axes: thirty-one point three two versus thirty-one point one zero gigabytes per second, lower median latency, and tighter ninety-ninth percentile latency, with zero host C P U cycles.
   - source: §S06

### S07 Executive summary & immediate next steps (66 s)

1. `   0.6s` **Three verified direct-path pillars:** (1) One-way `MOVDIR64B` hits **161.6 M/s** (`3 µs` pause avoids write-band conflict); (2) `897 ns` `W = 1` has all 3 cache/DDIO shortcuts active + **64 B** coherence; (3) Open-loop hits **91–97 M/s**.
   - spoken: To summarize our progress. Three direct-path pillars are nailed down: one hundred sixty-one point six million one-way by avoiding the write band, eight hundred ninety-seven nanoseconds with all three cache shortcuts active and sixty-four byte coherence, and ninety-one to ninety-seven million open-loop bidirectional.
   - source: §S07
2. `  20.1s` **Breakthrough pillar:** Zero-host-CPU bidirectional **Card → Intel DSA P2P** reaches **31.32 GB/s** (`B = 16`) and **2.17 µs** (`W = 1`, `163 ns` jitter) with **0 drops over 182.9M+ ops**.
   - spoken: In parallel, our zero-host-C P U card-to-Intel D S A peer-to-peer path reaches thirty-one point three two gigabytes per second and two point one seven microseconds at one hundred sixty-three nanoseconds jitter, with zero drops across one hundred eighty-two million operations.
   - source: §S07
3. `  38.1s` **Active open puzzle (queued next):** Pin down which hardware queue throttles 4-core closed-loop traffic to **13 M/s** (and drives the `35–39 µs` `p99` tail) via host burst pacing + Store Buffer / CHA / IIO / TAD counters.
   - spoken: One open puzzle is queued next on the card right now: pinning down which exact hardware queue throttles four-core closed-loop traffic to thirteen million per second and drives the thirty-five microsecond tail.
   - source: §S07
4. `  50.6s` **Next system milestone:** Combine the **897 ns** direct control ring with **31.3 GB/s** Card → DSA P2P bulk offload into a minimal DPU-driven **RPC & memory-offload layer**.
   - spoken: Next system milestone: combine the eight hundred ninety-seven nanosecond direct control ring with thirty-one gigabyte per second card-driven D S A offload into a minimal D P U to host R P C layer.
   - source: §S07
