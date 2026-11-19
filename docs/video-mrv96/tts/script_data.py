"""Single source of truth for narration, captions, and scene metadata (v3 — Honest Advisor Progress Report).

Run:
  python3 tts/gen_scenes.py --script          # write/refresh cue blocks + SCRIPT.md
  bun tts/manifest.ts                         # extract tts/manifest.json
  ~/tts/asr/bin/python tts/synth.py           # synthesize with edge-tts en-US-AndrewNeural
  python3 tts/gen_scenes.py --script          # retime cue blocks to actual clip durations

Rules:
- Plain, advisor-ready English. ZERO internal issue IDs anywhere.
- Completely honest: clearly separate (1) what is verified & nailed down, (2) the one open closed-loop puzzle
  actively queued for hardware dissection, and (3) our new zero-host-CPU Card -> Intel DSA P2P breakthrough.
- Every number matches data/verify_facts.py. Interpretations carry [INF] on screen and "our reading" in speech.
"""

SCENES = [
    dict(
        id="S01",
        file="S01Overview",
        title="Direct PCIe path & 897 ns baseline",
        stage=None,
        source="Xeon host ⇄ OCTEON 10 DPU · PCIe Gen5 ×4 · advisor progress report",
        cues=[
            (
                "Our direct datapath links an **Intel Xeon** host (`3.9 GHz` core, `2.2 GHz` uncore) to a **Marvell OCTEON 10 DPU** (24 Arm cores, `2.0 GHz`) over **PCIe Gen5 ×4**.",
                "Our direct datapath links an Intel Xeon host at three point nine gigahertz to a Marvell Octeon ten D P U with twenty-four Arm cores over P C I e Gen five by four.",
                "§S01",
            ),
            (
                "Forward: one `MOVDIR64B` writes a **64-byte** cell directly into card memory (no doorbell, no DMA). Return: a card core sends a **64-byte** write-combining reply to host RAM.",
                "Forward, one move dir sixty-four B writes a sixty-four byte message straight into card memory, no doorbell, no D M A. Return, a card core writes a sixty-four byte combining reply to host memory.",
                "§S01",
            ),
            (
                "At `W = 1`, round-trip latency is **897 ns** (`~429 ns` one way). Three same-machine anchors explain the budget: on-die **DSA NOP** takes **403 ns** (zero PCIe hops).",
                "At window one, round-trip latency is eight hundred ninety-seven nanoseconds. Three same-machine anchors explain why: an on-die host D S A no-op with zero P C I e hops already takes four hundred three nanoseconds.",
                "§S01",
            ),
            (
                "Anchor 2: raw **BAR4 PCIe read** (no card core) takes **646 ns** (`+243 ns` link **[INF]**). Anchor 3: full core-to-core hop reaches **897 ns** (`+212 ns` cache/core **[INF]** + `40 ns` card).",
                "Second, a raw B A R four P C I e read without a card core takes six hundred forty-six nanoseconds. Third, adding the card core hop brings the full round trip to eight hundred ninety-seven.",
                "§S01 [INF]",
            ),
            (
                "Our honest progress report in 3 parts: **(1) three direct-MMIO findings nailed down**, **(2) one open puzzle** (`13 M/s` closed loop), and **(3) our new 31.3 GB/s Card → DSA breakthrough**.",
                "Our progress report has three honest parts: three direct-path findings nailed down, the one open puzzle we are actively dissecting on hardware, and our new thirty-one gigabyte per second card-to-D S A breakthrough.",
                "§S01",
            ),
        ],
    ),
    dict(
        id="S02",
        file="S02OneWay",
        title="Nailed down #1 · Why one-way hits 161.6 M/s",
        stage=0,
        source="Single-variable forward tests · 81 verified runs · 0 lost, 0 corrupt",
        cues=[
            (
                "**Finding 1 (One-way ceiling):** Sanity check first — pinned at `3.9 GHz`, return replies OFF vs ON run **113.1 vs 112.2 M/s**. Return traffic is innocent; an earlier **75.6 M/s** drop was unpinned **~2.1 GHz** **[INF]**.",
                "Finding one: how fast is one-way forward? First, pinned at three point nine gigahertz, turning return replies on gives one hundred thirteen point one versus one hundred twelve point two million. Return traffic is innocent; an earlier drop to seventy-five point six was an unpinned clock, our reading.",
                "§S02 [INF]",
            ),
            (
                "With **2 card receive cores**, pure one-way still stalls at **128.7 M/s**. **Knob 1:** pause when caught up (`< 32 cells`): **0 µs** = **128.7** (`93.6%` empty); **1 µs** = **132.2**; **3 µs leaps +26% to 161.6 M/s** (`24.2 cyc/msg`).",
                "With two card receive cores, one-way still stalls at one hundred twenty-eight point seven million. Turning one knob, pause when caught up: zero microseconds hits one twenty-eight point seven, one gives one thirty-two point two, and three microseconds leaps twenty-six percent to one hundred sixty-one point six million.",
                "§S02",
            ),
            (
                "**Why?** Host counters show **447 writes** queuing **3.47 µs** at `0 µs` (**[INF]** out-of-order landing); `3 µs` lets **~900 cells** settle (`360` queued). Did pausing avoid the **write band**, or free **L3 bandwidth**?",
                "Host counters show four hundred forty-seven writes queuing three point five microseconds, our reading being out-of-order landing. Three microseconds lets nine hundred cells settle cleanly. Did pausing avoid the active write band, or simply free card L 3 read bandwidth?",
                "§S02 [INF]",
            ),
            (
                "**Knob 2 (Read target during 3 µs pause):** Cold RAM and old ring slots push card L3 reads to **815–1,033 M/s** (> 1 billion/s), yet forward stays flat at **160.2–162.6 M/s**.",
                "Keeping the three microsecond pause fixed and varying where the core reads: cold memory and old ring slots push card L 3 requests past one billion per second, yet forward throughput stays flat near one hundred sixty-one million.",
                "§S02",
            ),
            (
                "**Only reading ahead inside the active write band** crashes forward to **113.4 M/s** (`472 M/s` L3, snoops `67.9 → 111.9 M`, queue `4.03 µs > 3 µs` **[INF]**). Verdict: **write-band conflict, not L3 bandwidth**.",
                "Only reading ahead inside the active write band crashes throughput to one hundred thirteen point four million, stretching host queuing past four microseconds. Our reading: snoops or write blocking feed back. Verdict: location, not bandwidth.",
                "§S02 [INF]",
            ),
        ],
    ),
    dict(
        id="S03",
        file="S03CacheCoherence",
        title="Nailed down #2 · Cache/DDIO & 64 B coherence",
        stage=1,
        source="W = 1 cache/DDIO proof (40 runs) · card L3 spacing sweep (155 runs)",
        cues=[
            (
                "**Finding 2:** In our **897 ns** `W = 1` baseline, are all 3 cache/DDIO shortcuts already active? We flush **one cache level at a time** and check latency plus PMU counters.",
                "Finding two: in our eight hundred ninety-seven nanosecond baseline, are all three cache and D D I O shortcuts already active? We flush one cache level at a time and check latency plus hardware counters.",
                "§S03",
            ),
            (
                "**Shortcut 1 (Host L1D source):** Flushing the host source line to cold DRAM before `MOVDIR64B` adds **+117.3 ns** end-to-end (`897.3 → 1,014.5 ns`).",
                "First, flushing the host source buffer to cold D RAM before sending adds one hundred seventeen point three nanoseconds to end-to-end round-trip latency.",
                "§S03",
            ),
            (
                "**Shortcut 2 (Card L3 write-allocate):** Flushing the card slot before arrival keeps card read flat (`104 → 102 ns`) while `tad_alloc_ltg` jumps **+1.00/msg**; flushing after arrival to DRAM costs **+75.0 ns**.",
                "Second, flushing the card slot before arrival keeps card read flat while L 3 allocations jump by one per message. Flushing after arrival to card D RAM adds seventy-five nanoseconds on the read side.",
                "§S03",
            ),
            (
                "**Shortcut 3 (Host Xeon DDIO):** Hot replies hit `io_hit_itom = 1.00` into host LLC. Flushing before arrival stays flat (`42.7 ns`); flushing after arrival to host DRAM costs **+87.3 ns**.",
                "Third, on the return path, counters confirm one hundred percent host D D I O cache hits. Flushing before arrival stays flat at forty-two point seven nanoseconds, while forcing a host D RAM read adds eighty-seven point three.",
                "§S03",
            ),
            (
                "**64 B card coherence:** Linux reports a **128-byte** card L3 line. Sweeping spacing `d` between 2 card cores: **0 B** thrashes at **72.3 M/s** (`17.8 M/s` snoops), while **64 B** immediately hits **259.8 M/s** (`0.52 M/s` idle snoops) — same as `128 B–4 KiB`.",
                "Finally, Linux reports a one hundred twenty-eight byte card L 3 line. Sweeping slot spacing between two card cores: zero bytes thrashes at seventy-two point three million with heavy snoops, while sixty-four bytes immediately hits full two hundred fifty-nine point eight million with idle snoops, identical to four kilobytes. Card coherence is strictly sixty-four bytes.",
                "§S03",
            ),
        ],
    ),
    dict(
        id="S04",
        file="S04OpenLoop",
        title="Nailed down #3 · Open-loop breaks lockstep",
        stage=2,
        source="Lockstep vs open-loop duplex · 1 host core · 483 verified runs",
        cues=[
            (
                "**Finding 3:** Can the PCIe link sustain high **bidirectional** traffic without lockstep? Fixed: **1 host core + 1 card core**, WC replies. Knob: **lockstep vs open-loop duplex**.",
                "Finding three: can the link sustain high bidirectional traffic without lockstep? Fixed: one host core, one card core, write-combining replies. One knob: closed-loop lockstep versus independent open-loop duplex.",
                "§S04",
            ),
            (
                "**Wall disappears:** Lockstep (`W = 256`) stalls at **14.2 M/s**. Open loop delivers **5–35 M/s each way** (`70 M/s` combined) at **~1 µs** median (`0.97 µs` fwd, `1.24 µs` ret).",
                "The wall disappears. Lockstep stalls at fourteen point two million, whereas open loop delivers five to thirty-five million each way, seventy million combined, at about one microsecond median.",
                "§S04",
            ),
            (
                "**Peak capacity (1 host core):** Unthrottled `1 card core` hits **41.9 + 41.9 = 83.7 M/s**. Splitting card RX & TX onto **2 cores** drives **91.0 M/s** (WC) and **97.3 M/s** (`49.3 + 47.9`, DMA).",
                "Unthrottled, one host core and one card core hit eighty-three point seven million combined. Splitting card receive and send onto two cores drives ninety-one million with write combining, and ninety-seven point three with D M A.",
                "§S04",
            ),
            (
                "**Honest caveat — tails:** At `35 + 35 M/s`, forward **p99 spikes to 38.7 µs** (`p90 = 5.55 µs`; at `20 + 20`, even `p50` hits **16.9 µs**).",
                "Honest caveat: those one microsecond figures are medians. At thirty-five plus thirty-five, ninety-ninth percentile forward latency spikes to thirty-eight point seven microseconds, and at twenty plus twenty even the median hits sixteen point nine.",
                "§S04",
            ),
            (
                "**Echo cliff:** With request-triggered replies, RTT **p99 jumps** from **1.26 µs** (`10 M/s`) to **35.4 µs** at **13.5 M/s**. The link can fly — sharpening our one open puzzle.",
                "With request-triggered replies, ninety-ninth percentile round trip jumps from one point two six microseconds at ten million to thirty-five point four at thirteen point five. The link can fly, which sharpens our one open puzzle.",
                "§S04",
            ),
        ],
    ),
    dict(
        id="S05",
        file="S05OpenPuzzle",
        title="Open puzzle · Why 4-core closed loop stalls",
        stage=3,
        source="Active investigation · timestamps, delay slopes & WC layout · next on card",
        cues=[
            (
                "**The open puzzle:** By Little's Law at `0.92 µs` RTT, `W = 64` should saturate near **~50 M/s** — and **1 card core at W = 1024 DOES hit 50.5 M/s**!",
                "Now the one open puzzle we have not solved yet. By Little's Law at zero point nine two microseconds round trip, window sixty-four should approach fifty million per second. Indeed, one card core at window ten twenty-four hits fifty point five million.",
                "§S05",
            ),
            (
                "Yet **4 card cores** collapse to **13.1–13.5 M/s** at `W = 64..256` (and only **23.9 M/s** at `W = 1024`, where host polling grows `13.6 → 32.5 ns` across 4 sub-rings). Why?",
                "Yet four card cores collapse to thirteen million across windows sixty-four to two fifty-six, and reach only twenty-three point nine million at window ten twenty-four, where host polling swells across four sub-rings. Why?",
                "§S05",
            ),
            (
                "**Clue 1 (Timestamps):** At `W = 64` (`13.5 M/s`, `3.7 µs` RTT), card work stays **0.04 µs** while **~42 msgs** queue (`3.1–3.2 µs` forward — flipping to **return** `0.7 / 2.7 µs` in 2 runs without page pre-touch).",
                "First clue: timestamps show card work stays zero point zero four microseconds, while forty-two messages pile up in transit, three point one microseconds forward, though flipping to return in two runs without pre-touched pages.",
                "§S05",
            ),
            (
                "**Clue 2 (Both CPUs idle):** Delay injection (`+δ`, `20M` msgs) shows card slope **−0.01** (`76.1 → 75.6 ns`), while **+10 ns on host speeds it up +20%** (`76.2 → 60.9 ns` **[INF]** burst spacing).",
                "Second clue: delay slopes prove neither processor is maxed out. Adding fifty-nine nanoseconds on the card changes nothing, and adding ten nanoseconds on the host actually speeds it up twenty percent, our reading being burst spacing.",
                "§S05 [INF]",
            ),
            (
                "**Already ruled out:** Interleaved 4-core WC stays **99.99%** full 64 B writes (`13.14 vs 13.16 M/s`), and card L3 coherence is **64 B**. Neither causes the `13 M/s` throttle.",
                "We also cleared both obvious four-core hardware suspects: interleaved write-combining stays ninety-nine point nine nine percent full writes, and card L 3 coherence is sixty-four bytes.",
                "§S05",
            ),
            (
                "**Honest status (running next on card):** We have **NOT** yet pinned down which hardware queue throttles those ~42 writes to `~75 ns/msg`. Next on hardware: `MOVDIR64B` burst pacing + host Store Buffer / CHA / IIO vs card TAD counters.",
                "Honest status: we have not yet identified which exact hardware queue throttles those forty-two in-flight writes to seventy-five nanoseconds per message. That experiment is queued next on our card, sweeping host burst pacing with store-buffer, C H A, I I O, and card T A D counters.",
                "§S05",
            ),
        ],
    ),
    dict(
        id="S06",
        file="S06CardToDsa",
        title="New breakthrough · Zero-host-CPU Card → DSA P2P",
        stage=4,
        source="Card DPI DMA ⇄ Host Intel DSA2 ⇄ Card BAR4 · 182.9M+ verified ops",
        cues=[
            (
                "**New breakthrough:** Let the DPU drive the host's **Intel DSA** copy engine directly over bidirectional PCIe P2P — **zero host CPU** in the data path!",
                "Meanwhile, we achieved a major systems breakthrough: letting the D P U drive the host's Intel D S A copy engine directly over bidirectional P C I e peer-to-peer, with zero host C P U in the data path.",
                "§S06",
            ),
            (
                "**Forward P2P (Card → DSA portal):** Card **DPI DMA** 64 B descriptor writes hit **0 drops across 182.9M+ verified ops** — whereas card **WC** portal writes suffer **0.048%–1.217%** silent drops.",
                "Going forward to the host D S A portal, card D P I D M A delivered zero drops across one hundred eighty-two million verified operations, whereas direct write-combining suffered silent drops up to one point two percent.",
                "§S06",
            ),
            (
                "**Reverse P2P (DSA → Card BAR4):** Writing DSA's 32 B completion straight into **Card BAR4** (vs host RAM) cuts `W = 1` period: `NOP` **1.96 → 1.27 µs**, `4 KiB` **3.13 → 2.17 µs**, slashing jitter **1,119 → 163 ns** (`−85%`).",
                "Closing the reverse path into card B A R four cuts single-op round trip from one point nine six to one point two seven microseconds for no-ops, three point one three to two point one seven for four kilobyte copies, and slashes tail jitter eighty-five percent, from one thousand one hundred nineteen down to one hundred sixty-three nanoseconds.",
                "§S06",
            ),
            (
                "**DSA Batch (B = 1 → 16):** Single-op BAR4 P2P hit **15.28 GB/s** (`~131 ns/op` drain bubble). Batching `B = 2, 4, 8, 16` (`4 KiB`, hot `512 KiB` src, `CC = 0`) jumps **2.05× to 31.32 GB/s**!",
                "Single-op B A R four completion initially capped at fifteen point two eight gigabytes per second due to a one hundred thirty-one nanosecond drain bubble. Turning one knob, D S A batch size from one to sixteen, eliminates that bubble and doubles throughput to thirty-one point three two gigabytes per second.",
                "§S06",
            ),
            (
                "**Matches/beats host-RAM completion on BOTH axes:** At `B = 16`, Card BAR4 P2P delivers **31.32 vs 31.10 GB/s**, lower `p50` (**4.42 vs 4.63 µs**), and tighter `p99` (**4.61 vs 5.75 µs**) — zero host CPU!",
                "At batch sixteen, bidirectional card-to-D S A peer-to-peer matches or beats host-memory completion on both axes: thirty-one point three two versus thirty-one point one zero gigabytes per second, lower median latency, and tighter ninety-ninth percentile latency, with zero host C P U cycles.",
                "§S06",
            ),
        ],
    ),
    dict(
        id="S07",
        file="S07Summary",
        title="Executive summary & immediate next steps",
        stage=5,
        source="Advisor progress summary · verified pillars, open puzzle & roadmap",
        cues=[
            (
                "**Three verified direct-path pillars:** (1) One-way `MOVDIR64B` hits **161.6 M/s** (`3 µs` pause avoids write-band conflict); (2) `897 ns` `W = 1` has all 3 cache/DDIO shortcuts active + **64 B** coherence; (3) Open-loop hits **91–97 M/s**.",
                "To summarize our progress. Three direct-path pillars are nailed down: one hundred sixty-one point six million one-way by avoiding the write band, eight hundred ninety-seven nanoseconds with all three cache shortcuts active and sixty-four byte coherence, and ninety-one to ninety-seven million open-loop bidirectional.",
                "§S07",
            ),
            (
                "**Breakthrough pillar:** Zero-host-CPU bidirectional **Card → Intel DSA P2P** reaches **31.32 GB/s** (`B = 16`) and **2.17 µs** (`W = 1`, `163 ns` jitter) with **0 drops over 182.9M+ ops**.",
                "In parallel, our zero-host-C P U card-to-Intel D S A peer-to-peer path reaches thirty-one point three two gigabytes per second and two point one seven microseconds at one hundred sixty-three nanoseconds jitter, with zero drops across one hundred eighty-two million operations.",
                "§S07",
            ),
            (
                "**Active open puzzle (queued next):** Pin down which hardware queue throttles 4-core closed-loop traffic to **13 M/s** (and drives the `35–39 µs` `p99` tail) via host burst pacing + Store Buffer / CHA / IIO / TAD counters.",
                "One open puzzle is queued next on the card right now: pinning down which exact hardware queue throttles four-core closed-loop traffic to thirteen million per second and drives the thirty-five microsecond tail.",
                "§S07",
            ),
            (
                "**Next system milestone:** Combine the **897 ns** direct control ring with **31.3 GB/s** Card → DSA P2P bulk offload into a minimal DPU-driven **RPC & memory-offload layer**.",
                "Next system milestone: combine the eight hundred ninety-seven nanosecond direct control ring with thirty-one gigabyte per second card-driven D S A offload into a minimal D P U to host R P C layer.",
                "§S07",
            ),
        ],
    ),
]
