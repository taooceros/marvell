"""Single source of truth for narration, captions, and scene metadata (v2 — strict single-variable detective chain).

Run:
  python3 tts/gen_scenes.py --script          # write/refresh cue blocks + SCRIPT.md
  bun tts/manifest.ts                         # extract tts/manifest.json
  ~/tts/asr/bin/python tts/synth.py           # synthesize with edge-tts en-US-AndrewNeural
  python3 tts/gen_scenes.py --script          # retime cue blocks to actual clip durations

Rules:
- Plain, advisor-ready English. ZERO internal issue IDs anywhere.
- Every experiment follows the exact 4-step pattern:
  (1) Question / Suspect -> (2) Fixed Baseline + ONE Knob -> (3) Single-Variable Data + Verdict -> (4) Mechanism ("Why") + Bridge.
- Every number matches data/verify_facts.py. Interpretations carry [INF] on screen and "our reading" in speech.
"""

SCENES = [
    dict(
        id="S01",
        file="S01Mystery",
        title="The direct path & the 12× mystery",
        stage=None,
        source="Xeon host ⇄ OCTEON 10 DPU · PCIe Gen5 ×4 · 64-byte messages",
        cues=[
            (
                "An **Intel Xeon** host (one core pinned at **3.9 GHz**) talks to a **Marvell OCTEON 10 DPU** (24 Arm cores at **2.0 GHz**) over **PCIe Gen5 ×4**.",
                "An Intel Xeon host, one core pinned at three point nine gigahertz, talks to a Marvell Octeon ten D P U with twenty-four Arm cores at two gigahertz over P C I e Gen five by four.",
                "§Setup",
            ),
            (
                "Forward: one `MOVDIR64B` instruction writes a **64-byte** message directly into a card-memory ring over PCIe — no doorbell, no DMA.",
                "Forward: one move dir sixty-four B instruction writes a sixty-four byte message straight into a card-memory ring over P C I e. No doorbell, no D M A.",
                "§Setup",
            ),
            (
                "Return: a card core polls the ring, checks sequence tags, and sends a **64-byte** reply to host memory via **write-combining** (`4 × 16 B`).",
                "Return: a card core polls the ring, checks sequence tags, and writes a sixty-four byte reply into host memory with write combining: four sixteen-byte stores merged into one P C I e write.",
                "§Setup",
            ),
            (
                "The mystery: one way, one host core hits **161.6 M/s**. Close the loop (`W = 64`, 4 card cores), and the same path collapses **12× to ~13 M/s**.",
                "Here is the mystery. One way, one host core pushes one hundred sixty-one point six million messages per second. Close the loop at window sixty-four with four card cores, and it collapses twelve times to about thirteen million.",
                "§E1, §E3",
            ),
            (
                "Our method (**Occam's razor**): fix this fastest baseline, turn **one knob per experiment**, verify every message (**0 lost, 0 corrupt**), and tag **[INF]**.",
                "Our method: Occam's razor. Fix this fastest baseline, turn one knob per experiment, verify every message for zero loss or corruption, and tag interpretations as our reading.",
                "§Method",
            ),
            (
                "Our 4-step hunt: **rule out return traffic** & solve **one-way**, dissect the **13 M/s loop**, clear **two 4-core suspects**, then **break the loop**.",
                "Our four-step hunt: sanity-check return traffic and solve one-way, step inside the thirteen million loop, clear two four-core suspects, and finally break the loop.",
                "§Method",
            ),
        ],
    ),
    dict(
        id="S02",
        file="S02Sanity",
        title="Sanity check · Return traffic vs forward",
        stage=0,
        source="Forward vs bidirectional · host pinned 3.9 GHz · 21 verified runs",
        cues=[
            (
                "**Suspect 0:** Test the simplest idea first — do return replies from card to host choke forward `MOVDIR64B` writes on PCIe?",
                "Start with the simplest suspect. Do return replies from card to host choke forward move dir sixty-four B writes on P C I e?",
                "§E0",
            ),
            (
                "**Fixed:** host sender pinned **3.9 GHz** (uncore **2.2 GHz**), **1 receive + 1 reply core** with WC. **One knob:** replies **OFF vs ON**.",
                "Fixed baseline: host sender pinned at three point nine gigahertz, uncore two point two, one receive core plus one write-combining reply core. One knob: replies off versus on.",
                "§E0",
            ),
            (
                "**Result:** forward alone runs **113.1 M/s**; with replies flowing it stays **112.2 M/s**. **Verdict: No** — return traffic does not slow forward writes.",
                "Result: forward alone runs one hundred thirteen point one million per second; with replies flowing it stays one hundred twelve point two. Verdict: no, return traffic does not slow forward writes.",
                "§E0",
            ),
            (
                "An earlier drop to **75.6 M/s** had an unpinned sender at **~2.1 GHz** — **[INF]** a clock drop, not PCIe backpressure. Rule zero: pin every clock.",
                "An earlier drop to seventy-five point six came only from an unpinned sender at two point one gigahertz. Our reading: clock drop, not P C I e, though two point one was never run one way. Always pin clocks.",
                "§E0 [INF]",
            ),
            (
                "**Next:** Return traffic is innocent. Give pure one-way traffic **2 receive cores** — yet forward still stalls at **128.7 M/s**. What holds it back?",
                "Return traffic is innocent. Next, give pure one-way traffic two receive cores so the receiver is not the limit, yet forward still stalls at one hundred twenty-eight point seven million. Why?",
                "§E0 -> §E1",
            ),
        ],
    ),
    dict(
        id="S03",
        file="S03Frontier",
        title="Exp 1 · Card frontier polling",
        stage=1,
        source="One-way forward · 2 card cores · 27 verified runs × 2×10⁸ msgs",
        cues=[
            (
                "**Question:** Two card cores polling non-stop catch up to the **write frontier** where `MOVDIR64B` writes are landing. Does chasing that frontier slow the host?",
                "Experiment one. Two card cores polling non-stop catch right up to the write frontier where host writes are still landing. Does chasing that frontier slow the host?",
                "§E1",
            ),
            (
                "**Fixed:** one-way forward, host **3.9 GHz**, **2 card cores**, up to **32 cells/poll** (`< 32` = caught up). **One knob:** pause when caught up (**0, 1, or 3 µs**).",
                "Fixed baseline: one-way forward, host three point nine gigahertz, two card cores taking up to thirty-two cells per poll. One knob: how long a core pauses when caught up: zero, one, or three microseconds.",
                "§E1",
            ),
            (
                "**0 µs** (non-stop): **93.6%** of polls are empty; throughput sticks at **128.7 M/s**. **1 µs**: barely moves, **132.2 M/s** (`132–134` across modes).",
                "At zero microseconds, ninety-three point six percent of polls return empty, and throughput sticks at one hundred twenty-eight point seven million. One microsecond barely moves it, to one hundred thirty-two point two.",
                "§E1",
            ),
            (
                "**3 µs** pause: throughput leaps **+26% to 161.6 M/s** — saturating the host core at **24.2 cycles per message**.",
                "At three microseconds, throughput leaps twenty-six percent to one hundred sixty-one point six million per second, saturating the host core at twenty-four point two cycles per message.",
                "§E1",
            ),
            (
                "**Why:** Host counters show **447 writes** queuing **3.47 µs** at 0 µs (**[INF]** landing out of order). `1 µs` wakes inside that band; `3 µs` lets **~900 cells** settle (`360` queued, `2.23 µs`).",
                "Why? Host counters show four hundred forty-seven writes queuing three point five microseconds, our reading being out-of-order landing. One microsecond wakes inside that band; three lets nine hundred cells settle, cutting the queue to three hundred sixty.",
                "§E1 [INF]",
            ),
            (
                "**Next:** Did pausing 3 µs help because the core avoided the **active write band**, or simply because it freed **card L3 read bandwidth**?",
                "Wait. During a three microsecond pause, the core stopped reading completely. Did pausing help by avoiding the active write band, or simply by freeing card L 3 read bandwidth?",
                "§E1 -> §E2",
            ),
        ],
    ),
    dict(
        id="S04",
        file="S04WriteBand",
        title="Exp 2 · Write-band conflict vs L3 bandwidth",
        stage=1,
        source="One-way · 3 µs pause · 33 verified runs × 2×10⁸ msgs · card L3 counters",
        cues=[
            (
                "**Two suspects:** Did the 3 µs pause unlock 161.6 M/s by cutting **card L3 read bandwidth**, or by staying out of the **in-flight write band**?",
                "Experiment two splits those two suspects: total L 3 read bandwidth on the card, versus colliding with the exact addresses still being written.",
                "§E2",
            ),
            (
                "**Fixed:** the fast **3 µs pause** baseline (2 card cores). **One knob:** **where** the core reads during each pause — nothing, cold RAM, old ring slots, or the write band.",
                "Fixed baseline: the fast three microsecond pause from experiment one. One knob: where the core reads during each pause — nothing, cold memory, old ring slots, or ahead inside the active write band.",
                "§E2",
            ),
            (
                "Idle spin gives **160.4 M/s** (`608 M/s` L3). Cold RAM pushes L3 reads to **815–1,033 M/s** (> 1 billion/s), yet forward stays **160.4–161.4 M/s**.",
                "Reading nothing gives one hundred sixty point four million at six hundred eight million L 3 requests per second. Cold memory pushes L 3 reads past one billion per second, yet forward stays at one hundred sixty-one million.",
                "§E2",
            ),
            (
                "Old ring slots half a lap back (**162.0–162.6**) and slots just behind (**160.2**) stay full speed. **Only reading ahead inside the write band** crashes to **113.4 M/s**.",
                "Old ring slots half a lap back stay at one hundred sixty-two million, and slots just behind stay at one hundred sixty point two. Only reading ahead inside the active write band crashes throughput to one hundred thirteen point four.",
                "§E2",
            ),
            (
                "**Why:** L3 bandwidth is innocent (write band uses only `472 M/s` L3). **[INF]** Touching landing lines spikes snoops (`67.9 → 111.9 M`) and pushes host queueing to **4.03 µs > 3 µs**.",
                "Verdict: location, not bandwidth. Touching lines still landing spikes snoops and pushes host queuing to four microseconds, longer than the pause. Our reading: snoops or write blocking feed back so pauses end too early.",
                "§E2 [INF]",
            ),
            (
                "**Next:** One-way is solved: stay behind the ~3 µs band to hit **161.6 M/s**. Now step inside the **13 M/s closed loop**: where do the extra microseconds sit?",
                "One-way is solved: stay behind the three microsecond band and hit one hundred sixty-one point six million. Now step inside the thirteen million closed loop. Where do the extra microseconds sit?",
                "§E2 -> §E3",
            ),
        ],
    ),
    dict(
        id="S05",
        file="S05WhereTime",
        title="Exp 3 · Where time sits in the 13 M/s loop",
        stage=2,
        source="Closed-loop timestamps · 4 card cores · WC replies · W = 1 vs 64",
        cues=[
            (
                "**Question:** In a closed loop, the host caps in-flight requests at window **W** and waits for replies. At **13.5 M/s**, which leg is slow: **forward, card, or return**?",
                "Experiment three opens up the closed loop, where the host caps in-flight requests at window W and waits for replies. At thirteen point five million, which leg is slow: forward, on-card, or return?",
                "§E3",
            ),
            (
                "**Fixed:** closed loop, WC replies, **4 card cores**, synchronized timestamps. **One knob:** window **W = 1** (no queue) vs **W = 64** (slow state).",
                "Fixed baseline: closed loop, write-combining replies, four card cores, and synchronized hardware timestamps. One knob: window one with no queueing, versus window sixty-four in the slow state.",
                "§E3",
            ),
            (
                "**W = 1** (no queue): median round trip is **0.92 µs** = **0.45 µs** forward + **0.04 µs** on card + **0.43 µs** return.",
                "At window one, nothing queues. Round trip is zero point nine two microseconds: zero point four five forward, zero point zero four on card, and zero point four three back.",
                "§E3",
            ),
            (
                "**W = 64** (**13.5 M/s**): round trip swells to **3.7 µs**. On-card stays **0.04 µs**, return **~0.5 µs** (`0.49–0.52`), while forward swells to **3.1–3.2 µs** (`+2.7 µs`).",
                "At sixty-four, throughput stalls at thirteen point five million and round trip swells to three point seven microseconds. On-card stays zero point zero four and return stays half a microsecond; the extra two point seven waits on the forward leg.",
                "§E3",
            ),
            (
                "**Why + caveat:** Host sends **9.1 ns** apart; card sees one every **~75 ns** → **~42 queued**. Yet 2 runs without page pre-touch hit **13.4 M/s** with the queue on **return** (`0.7` / `2.7 µs`) — **[INF]** loop rhythm.",
                "The host sends nine nanoseconds apart while the card sees one every seventy-five nanoseconds, queuing about forty-two messages. Caveat: two runs without pre-touched pages hit the same thirteen point four million, but flipped the queue to return. Our reading: loop rhythm, not one fixed stage limit.",
                "§E3 [INF]",
            ),
            (
                "**Next:** About **42 messages** pile up in transit at 13 M/s. Are the host CPU or card CPUs actually busy, or are **both sitting idle**?",
                "About forty-two messages pile up in transit at thirteen million per second. Next question: is either the host or card C P U actually busy, or are both sitting idle?",
                "§E3 -> §E4",
            ),
        ],
    ),
    dict(
        id="S06",
        file="S06CriticalPath",
        title="Exp 4 · Who owns the critical path?",
        stage=2,
        source="Closed-loop delay injection · 2×10⁷ msgs/run · host vs card slopes",
        cues=[
            (
                "**Question:** In the 13 M/s slow state, who sets the pace? **One knob:** inject an un-hideable delay **δ** (`10–59 ns`, fenced multiplies) per message on **one side**.",
                "Experiment four asks who owns the critical path. One knob: inject an un-hideable delay delta of ten to fifty-nine nanoseconds per message on either the host or the card.",
                "§E4",
            ),
            (
                "**Calibration:** Average **20M** messages (sub-ns precision). At `W = 1`, `+40 ns` adds `+40.1 ns` (slope **1.01**). **Slope ≈ 1** = bottleneck; **slope ≈ 0** = idle.",
                "Over twenty million messages we resolve fractions of a nanosecond. At window one, forty nanoseconds adds forty point one, slope one point zero one. Slope one means bottleneck; slope zero means idle.",
                "§E4",
            ),
            (
                "**Fast state** (1 card core, `W = 1024`): both slopes **1.07** (interlocked). **Slow state** (4 cores, `W = 64`, `76.1 ns/msg`): **+59 ns** on card changes nothing (**76.1 → 75.6 ns**, slope **−0.01**).",
                "Fast state, one core at window ten twenty-four: both slopes are one point zero seven, tightly interlocked. Slow state, four cores at sixty-four: adding fifty-nine nanoseconds on the card changes nothing, seventy-six point one to seventy-five point six.",
                "§E4",
            ),
            (
                "**Host surprise:** in the slow state, **+10 ns** makes it **20% faster** (**76.2 → 60.9 ns/msg**)! **[INF]** Both CPUs are idle; spacing host bursts breaks the slow rhythm.",
                "Surprise on the host: adding ten nanoseconds makes the slow state twenty percent faster, dropping from seventy-six point two to sixty point nine nanoseconds. Our reading: both processors sit idle, and spacing host bursts breaks the slow rhythm.",
                "§E4 [INF]",
            ),
            (
                "**4-core clue:** At `W = 1024`, **4 card cores lose to 1** (**23.9 vs 50.5 M/s**). Sending stays **9.2–9.3 ns**, while reply polling jumps **13.6 → 32.5 ns** across 4 sub-rings.",
                "Another four-core clue: at window ten twenty-four, four card cores lose to one, twenty-three point nine versus fifty point five million. Sending stays nine nanoseconds, while reply polling grows from thirteen point six to thirty-two point five across four rings.",
                "§E4",
            ),
            (
                "**Next:** Four card cores appear in both slowdowns. Before blaming the loop itself, rule out two **4-core hardware suspects**: broken **WC** and **128 B false sharing**.",
                "Four card cores show up in both slowdowns. Before blaming the closed loop itself, we must rule out two classic four-core hardware suspects: broken write combining, and one-hundred-twenty-eight byte false sharing.",
                "§E4 -> §E5",
            ),
        ],
    ),
    dict(
        id="S07",
        file="S07RuledOut",
        title="Exp 5 · Ruling out two 4-core suspects",
        stage=3,
        source="4-core WC layout (64 runs) · card L3 spacing sweep (155 runs)",
        cues=[
            (
                "**Suspect 5a (Write-combining):** With 4 card cores, replies scatter across **64 KB host regions** by tag. Does interleaving break 64-byte write-combining?",
                "Experiment five clears both four-core suspects, one knob each. First: with four card cores, replies scatter across sixty-four kilobyte host regions. Does interleaving break write combining?",
                "§E6a",
            ),
            (
                "**Fixed:** closed loop, 4 cores, `W = 64`. **One knob:** contiguous vs interleaved. **Result:** **1.0001 vs 1.0000** full 64 B writes/reply (**99.99%**), **13.14 vs 13.16 M/s**.",
                "Fixed: closed loop, four cores, window sixty-four. One knob: contiguous versus interleaved regions. Host counters measure ninety-nine point nine nine percent full sixty-four byte writes in both, at thirteen point one four versus thirteen point one six million.",
                "§E6a",
            ),
            (
                "**Why 5a is innocent:** Each 64 B reply fills one whole WC line (`4 × 16 B`) and flushes before the next starts. (Caveat: `W = 1024` sees an unexplained `−3%`, `21.88 → 21.22`.)",
                "Why? Each sixty-four byte reply fills one complete combining buffer by itself and flushes whole. Caveat: window ten twenty-four sees an unexplained three percent dip, but the counter proves write combining stays intact.",
                "§E6a",
            ),
            (
                "**Suspect 5b (128 B false sharing):** Linux reports a **128-byte** card L3 coherency line — if true, cores touching adjacent 64 B slots would thrash.",
                "Second suspect: Linux reports a one hundred twenty-eight byte coherency line for card L 3. If true, cores writing neighboring sixty-four byte slots would thrash.",
                "§E6b",
            ),
            (
                "**Fixed:** 2 card cores rewriting own 64 B slots. **One knob:** spacing `d` (`0 B`–`4 KB`). **0 B**: **72.3 M/s** (`17.8 M/s` snoops). **64 B**: full **259.8 M/s** (`0.52 M/s` idle snoops) — same as `128 B–4 KB`.",
                "Fixed: two card cores rewriting their own sixty-four byte slots. One knob: spacing from zero bytes to four kilobytes. Zero bytes drops to seventy-two point three million with seventeen point eight million snoops. At sixty-four bytes, both hit two hundred fifty-nine point eight million with idle snoops, identical to four kilobytes.",
                "§E6b",
            ),
            (
                "**Next:** Card L3 coherence is **64 B** (core-to-core; PCIe beside polling untested). Both 4-core suspects are innocent — one test remains: **break the closed loop**.",
                "So card-core coherence is sixty-four bytes, though P C I e beside a polling core remains untested. Both four-core suspects are innocent. One decisive test remains: break closed-loop lockstep itself.",
                "§E6b -> §E7",
            ),
        ],
    ),
    dict(
        id="S08",
        file="S08OpenLoop",
        title="Exp 6 · Breaking the loop (open-loop)",
        stage=4,
        source="Lockstep vs open-loop duplex · 1 host core · 483 verified runs",
        cues=[
            (
                "**Hypothesis:** If **closed-loop lockstep** (waiting for replies before sending) creates the 13 M/s wall, independent **open-loop** bidirectional traffic should blow past it.",
                "Experiment six tests the decisive hypothesis. If waiting for replies in lockstep creates the thirteen million wall, independent open-loop bidirectional traffic should blow right past it.",
                "§E5",
            ),
            (
                "**Fixed:** **1 host core + 1 card core**, WC replies. **One knob:** **lockstep** vs **open-loop duplex** (both sides transmit independently at target rate `R`).",
                "Fixed baseline: one host core, one card core, and write-combining replies. One knob: closed-loop lockstep, versus open-loop duplex where both sides send independently at a target rate.",
                "§E5",
            ),
            (
                "**Wall gone:** Lockstep stalls at **14.2 M/s** (`W = 256`). Open loop hits every target **5–35 M/s each way** (`70 M/s` total) at **~1 µs** median (`0.97` fwd, `1.24 µs` ret at `35 + 35`).",
                "The wall vanishes. Lockstep stalls at fourteen point two million. Open loop hits every target from five to thirty-five million each way, seventy million combined, at about one microsecond median: zero point nine seven forward and one point two four back.",
                "§E5",
            ),
            (
                "**Peak capacity:** Unthrottled, 1 host + 1 card core reaches **41.9 + 41.9 = 83.7 M/s**. Splitting card RX & TX onto 2 cores hits **91.0 M/s** (WC) and **97.3 M/s** (`49.3 + 47.9`, DMA).",
                "Unthrottled, one host core and one card core hit forty-one point nine each way, eighty-three point seven total. Splitting card receive and send onto two cores hits ninety-one million with write combining, and ninety-seven point three with D M A.",
                "§E5",
            ),
            (
                "**Honest caveat — tails:** Those `~1 µs` numbers are medians (`p50`). At `35 + 35`, forward **p99 spikes to 38.7 µs** (at `20 + 20`, even `p50` hits `16.9 µs`).",
                "Honest caveat: those one microsecond numbers are medians. At thirty-five plus thirty-five, ninety-ninth percentile forward latency spikes to thirty-eight point seven microseconds, and at twenty plus twenty even the median hits sixteen point nine.",
                "§E5 tails",
            ),
            (
                "**Echo test:** With request-triggered replies, RTT **p99 jumps** from **1.26 µs** (`10 M/s`) to **35.4 µs** (`13.5 M/s`). **Verdict:** the link is fast; lockstep & tails are our target.",
                "With request-triggered replies, ninety-ninth percentile round trip jumps from one point two six microseconds at ten million to thirty-five point four at thirteen point five. Verdict: the link can fly; lockstep and tails are our target.",
                "§E5 echo",
            ),
        ],
    ),
    dict(
        id="S09",
        file="S09CausalMap",
        title="The complete causal map & what's next",
        stage=5,
        source="Six single-variable experiments · verified causal map & roadmap",
        cues=[
            (
                "**1 · One-way solved (161.6 M/s):** Return traffic is innocent (`113.1 vs 112.2`). Pausing **3 µs** behind the write band unlocks **+26%** — location conflict, not L3 bandwidth.",
                "Here is the complete causal map. First, one-way is solved: return traffic is innocent, and pausing three microseconds behind the write band unlocks one hundred sixty-one point six million, driven by write-band conflict, not L 3 bandwidth.",
                "§E0, §E1, §E2",
            ),
            (
                "**2 · Closed loop diagnosed (~13 M/s):** Not busy CPUs (`0.04 µs` card, slopes ≈ 0), not broken WC (`99.99%`), not 128 B false sharing (`64 B`). **~42 msgs** queue; `+10 ns` host gap speeds it up **20%**.",
                "Second, the thirteen million loop is diagnosed: neither C P U is busy, write combining is ninety-nine point nine nine percent intact, and L 3 coherence is sixty-four bytes. Forty-two messages queue in transit, and ten nanoseconds of host spacing speeds it up twenty percent.",
                "§E3, §E4, §E6a, §E6b",
            ),
            (
                "**3 · Open loop unlocked (83.7–97.3 M/s):** Breaking lockstep removes the wall (`35 + 35 M/s` at **~1 µs** `p50`, up to **97.3 M/s** combined), leaving a **35–39 µs** `p99` tail.",
                "Third, breaking lockstep unlocks thirty-five million each way at one microsecond median, up to ninety-seven point three million combined from one host core, leaving a thirty-five to thirty-nine microsecond tail.",
                "§E5",
            ),
            (
                "**Next step 1 — Kill slow state & tail:** Pace host bursts, cap reply-poll batches, sweep the mesh clock, and track long-run **p99**.",
                "Next step one: kill the slow state and tail by pacing host bursts, capping reply-poll batches, sweeping the mesh clock, and tracking long-run ninety-ninth percentile latency.",
                "§Next",
            ),
            (
                "**Next step 2 — Build real systems:** Let card cores drive the host's **Intel DSA** copy engine directly, and build a minimal **RPC / message layer** for real offload.",
                "Next step two: put this direct path to work by letting card cores drive the host's Intel D S A copy engine directly, and building a minimal R P C layer for real offload.",
                "§Next",
            ),
        ],
    ),
]
