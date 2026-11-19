# Why 161.6 M/s one way but ~13 M/s in a loop? Six single-variable experiments on the host ⇄ DPU direct path (narrated explainer script)

Raw data: `logs/mrv{24,50,53,54,55,61,70,76}-*/` of `~/dev/marvell` (2026-11-17/18; every plotted run verified, lost = bad = torn = 0). Every on-screen number is recomputed by `data/verify_facts.py` and listed with its source in `data/FACTS.md`; `[INF]` marks our interpretations (spoken as "our reading").

## Build

1. Edit captions/narration only in `tts/script_data.py`, then `python3 tts/gen_scenes.py --script` (rewrites only the `// <cues>` blocks and this file).
2. Narration: `bun tts/manifest.ts && ~/tts/asr/bin/python tts/synth.py` (edge-tts en-US-AndrewNeural +0%, cached by text), then rerun `python3 tts/gen_scenes.py --script` to retime.
3. Check: `npx tsc -p .`; `./stills.sh S05 3 9 15` → half-size contact sheet `out/S05-sheet.png`.
4. Final: `./render.sh` → `out/mrv96-six-experiments.mp4` (1920×1080 @ 30 fps); per-cue QA sheets: `python3 tts/qa_frames.py` → `out/qa/S*.png`.

## Scenes

| Scene | Title | Length | Cues | Source |
|---|---|---|---|---|
| S01 | One path, two speeds | 52 s | 5 | host ⇄ DPU direct path · six experiments · Nov 17–18, 2026 |
| S02 | The direct path | 76 s | 6 | Xeon Gold 6438M ⇄ OCTEON 10 CN106 · PCIe Gen5 x4 |
| S03 | Exp 1 · Chasing the frontier | 76 s | 7 | MRV-61 · logs/mrv61-2026-11-18 · 27 runs × 2×10⁸ msgs |
| S04 | Exp 2 · Where the core reads | 78 s | 7 | MRV-76 · logs/mrv76-2026-11-18 · 33 runs × 2×10⁸ msgs |
| S05 | Exp 3 · Inside the slow loop | 84 s | 7 | MRV-50 · logs/mrv50-2026-11-17 · card-side timestamps |
| S06 | Exp 4 · Who sets the pace? | 90 s | 7 | MRV-53 · logs/mrv53-2026-11-17 · 2×10⁷ msgs per run |
| S07 | Exp 5 · Remove the loop | 88 s | 7 | MRV-70 · logs/mrv70-2026-11-18 · 483 runs × 300 ms |
| S08 | Exp 6a · Interleaved replies | 65 s | 5 | MRV-54 · logs/mrv54-2026-11-18 · 4 × 2×10⁷ msgs per point |
| S09 | Exp 6b · 64 B or 128 B? | 64 s | 5 | MRV-55 · logs/mrv55-2026-11-18 · 1 s × 5 per point |
| S10 | What we learned | 67 s | 6 | six single-variable experiments · open items |

## Cues (caption / narration / provenance)

### S01 One path, two speeds (52 s)

1. `   0.6s` A server talks to its **DPU** through direct **64-byte** writes. One way, one host core pushes **161.6 M messages/s** into the card.
   - spoken: A server talks to its D P U through direct sixty-four byte writes. One way, a single host core pushes one hundred sixty-one point six million messages per second.
   - source: §E1
2. `  11.2s` Close the loop, so the host waits for replies before sending more, and the same path settles near **13 M/s**: about **12× slower**.
   - spoken: But close the loop, so the host waits for replies before sending more, and the same path settles near thirteen million per second. Twelve times slower.
   - source: §E1, §E3
3. `  20.1s` Why? We ran **six experiments**. Each keeps the fastest setup fixed and turns **exactly one knob**, so each result has one cause.
   - spoken: Why? We ran six experiments. Each one keeps the fastest setup fixed, and turns exactly one knob, so every difference has a single cause.
   - source: §Method
4. `  29.6s` Experiments **1–2** dissect the one-way path, **3–4** look inside the loop, **5** removes the loop, and **6** rules out two suspects.
   - spoken: Experiments one and two dissect the one-way path, three and four look inside the loop, five removes the loop, and six rules out two suspects.
5. `  38.8s` Every run verifies every message: **0 lost, 0 corrupt**. Numbers are medians unless marked; **[INF]** marks our interpretation.
   - spoken: Every run verifies every message: zero lost, zero corrupted. Numbers are medians unless noted, and anything tagged as an inference is our interpretation, not a measurement.
   - source: §Method

### S02 The direct path (76 s)

1. `   0.6s` Host: **Intel Xeon**, one core pinned at **3.9 GHz**. DPU: **Marvell OCTEON 10**, 24 Arm cores at **2.0 GHz**. Link: **PCIe Gen5 ×4**.
   - spoken: The hardware: an Intel Xeon host, using one core pinned at three point nine gigahertz, and a Marvell Octeon ten D P U with twenty-four Arm cores at two gigahertz, linked by P C I e Gen five, by four.
   - source: §Setup
2. `  15.2s` Forward: one **MOVDIR64B** instruction writes a whole 64-byte message into a ring in card memory, through a PCIe window. No doorbell, no DMA.
   - spoken: Forward: one move dir sixty-four B instruction writes a whole sixty-four byte message into a ring in card memory, through a P C I e window. No doorbell, no D M A.
   - source: §Setup
3. `  27.9s` A card core **polls** the ring. Sequence numbers at both ends of each message tell it the slot is complete and in order.
   - spoken: A card core polls the ring. Sequence numbers at both ends of each message tell it a slot is complete, and in order.
   - source: §Setup
4. `  34.9s` Return: the card core writes its reply into host memory with **write-combining (WC)**: four 16-byte stores leave as one 64-byte PCIe write.
   - spoken: Return: the card core writes its reply straight into host memory with write combining. Four stores, of sixteen bytes each, merge into a single sixty-four byte P C I e write.
   - source: §Setup
5. `  47.1s` Sanity check first: does reply traffic slow the forward path? Sender pinned at 3.9 GHz: **113.1 → 112.2 M/s**. No.
   - spoken: First, a sanity check: does reply traffic slow the forward path? With the sender pinned, forward throughput only moves from one hundred thirteen point one to one hundred twelve point two million per second. No.
   - source: §E0
6. `  59.5s` An earlier drop to **75.6 M/s** came from an unpinned sender at **~2.1 GHz**. **[INF]** The clock, not PCIe. Lesson: pin every clock.
   - spoken: An earlier drop, to seventy-five point six, appeared only with the sender unpinned, at about two point one gigahertz. Our reading: the clock, not P C I e, though that clock was never run one way. Lesson: pin every clock.
   - source: §E0 [INF]

### S03 Exp 1 · Chasing the frontier (76 s)

1. `   0.6s` **Experiment 1.** One-way traffic only. When a card core catches up with the newest writes, should it keep polling, or step back?
   - spoken: Experiment one. One-way traffic only. When a card core catches up with the newest writes, should it keep polling, or step back?
   - source: §E1
2. `   8.5s` Fixed: the host core sends; **two card cores** receive, each taking up to **32 messages per poll**. Fewer than 32 means it reached the frontier.
   - spoken: Fixed: the host core sends, and two card cores receive, each taking up to thirty-two messages per poll. Fewer than thirty-two means it has reached the frontier.
   - source: §E1
3. `  18.7s` The frontier is fuzzy: host counters show **~440 writes in flight**, each queued **~3.5 µs**. **[INF]** They land out of order, leaving holes.
   - spoken: That frontier is fuzzy. Host counters show about four hundred forty writes in flight, each queued about three and a half microseconds. Our reading: they land out of order, leaving holes.
   - source: §E1 [INF]
4. `  30.2s` Knob = how long the core pauses when it catches up. **0 µs**: it never pauses, **93.6%** of polls come back empty, and throughput sticks at **128.7 M/s**.
   - spoken: The knob: how long the core pauses when it catches up. At zero it never pauses: ninety-three point six percent of its polls come back empty, and throughput sticks at one hundred twenty-eight point seven.
   - source: §E1
5. `  42.3s` **1 µs**: barely better, **132.2 M/s**. One microsecond is shorter than the ~3 µs landing window, so the core wakes up inside it again.
   - spoken: One microsecond: barely better, one hundred thirty-two point two. That is shorter than the three microsecond landing window, so the core wakes up inside it again.
   - source: §E1 [INF]
6. `  52.1s` **3 µs**: **161.6 M/s**. The band has landed; the core grabs **~900 messages** per wake-up, and the host's write queue drops from **447 to 360**.
   - spoken: Three microseconds: one hundred sixty-one point six. The band has landed, the core grabs about nine hundred messages per wake-up, and the host's write queue drops from four hundred forty-seven to three hundred sixty.
   - source: §E1
7. `  64.9s` Now the host core itself is the limit, **~24 cycles per message**. A receiver glued to the frontier slows the sender; backing off gives **+26%**.
   - spoken: Now the host core itself is the limit, about twenty-four cycles per message. A receiver glued to the frontier slows the sender. Backing off gives twenty-six percent.
   - source: §E1

### S04 Exp 2 · Where the core reads (78 s)

1. `   0.6s` **Experiment 2.** Why does polling at the frontier hurt? Suspect A: **where** the core reads. Suspect B: **how much** it loads the card's shared L3 cache.
   - spoken: Experiment two. Why does polling at the frontier hurt? Suspect A is position: where the core reads. Suspect B is bandwidth: how hard it loads the card's shared L 3 cache.
   - source: §E2
2. `  12.6s` Fixed: the fast case, pausing **3 µs**. Knob = what the core reads during each pause. Reading nothing: **160.4 M/s**.
   - spoken: Fixed: the fast case, pausing three microseconds. The knob: what the core reads during each pause. Reading nothing gives one hundred sixty point four.
   - source: §E2
3. `  22.5s` Read cold memory **outside** the ring: L3 requests rise from **608 to 815 M/s**, past **1 billion/s** with page strides. Throughput: **160.4–161.4**.
   - spoken: Read cold memory outside the ring: requests into the shared cache rise from six hundred eight to eight hundred fifteen million per second, past a billion with page strides. Throughput stays near one hundred sixty-one.
   - source: §E2
4. `  36.1s` Read old slots **half a lap behind**: **162.0**. Slots **just behind** the frontier, already landed: **160.2**. Bandwidth is not the problem.
   - spoken: Old slots half a lap behind: one hundred sixty-two. Slots just behind the frontier, already landed: one hundred sixty point two. Bandwidth is not the problem.
   - source: §E2
5. `  46.6s` Now read just **ahead** of the frontier, inside the ~3 µs band still landing. Throughput collapses to **113.4 M/s**.
   - spoken: Now read just ahead of the frontier, inside the three microsecond band that is still landing. Throughput collapses to one hundred thirteen point four.
   - source: §E2
6. `  55.6s` **[INF]** Touching lines still being written forces snoops or blocks inbound writes; host queueing hits **4.0 µs > 3 µs**, so pauses end too early.
   - spoken: Our reading: touching lines still being written forces cache snoops, or blocks inbound writes. Host queueing grows to four microseconds, longer than the pause, so pauses end too early, and it feeds on itself.
   - source: §E2 [INF]
7. `  69.4s` Verdict: **position, not bandwidth**. A receiver should stay behind the landing band; how much else it reads barely matters.
   - spoken: The verdict: position, not bandwidth. A receiver should stay behind the landing band. How much else it reads barely matters.
   - source: §E2

### S05 Exp 3 · Inside the slow loop (84 s)

1. `   0.6s` **Experiment 3** closes the loop: the host keeps at most **W** messages in flight, and each reply frees a slot for the next request.
   - spoken: Experiment three closes the loop. The host keeps at most W messages in flight, and each reply frees a slot for the next request.
   - source: §E3
2. `   9.5s` Fixed: WC replies, **4 card cores**. Knob = window **W = 1 vs 64**. Card timestamps split each round trip: forward · on card · return.
   - spoken: Fixed: write combining replies and four card cores. The knob: a window of one, versus sixty-four. Card timestamps split each round trip into forward, on-card, and return.
   - source: §E3
3. `  22.2s` **W = 1**, nothing queues: **0.92 µs** round trip = **0.45** forward + **0.04** on card + **0.43** return.
   - spoken: With a window of one, nothing queues. The round trip is zero point nine two microseconds: zero point four five forward, zero point zero four on the card, and zero point four three back.
   - source: §E3
4. `  34.5s` **W = 64**: **13.5 M/s**, round trip **3.7 µs**. On card still **0.04**, return **~0.5**. The extra **~2.7 µs** waits on the **forward** side.
   - spoken: At sixty-four: thirteen and a half million per second, and a three point seven microsecond round trip. On-card time is unchanged and the return is about half a microsecond, so the extra two point seven waits on the forward side.
   - source: §E3
5. `  48.5s` The host fires requests in bursts **9 ns** apart, but they reach the card core one every **~75 ns**. About **42** wait in between.
   - spoken: The host fires its requests in bursts, nine nanoseconds apart. But they reach the card core only once every seventy-five nanoseconds or so. About forty-two requests wait in between.
   - source: §E3
6. `  60.9s` Caveat: two runs that skipped pre-touching memory pages ran at the same **13.4 M/s**, with the queue on the **return** side: **0.7 µs** forward, **2.7 µs** back.
   - spoken: A caveat: two runs that skipped pre-touching memory pages ran at the same thirteen point four million, but with the queue on the return side: zero point seven microseconds forward, two point seven back.
   - source: §E3 caveat
7. `  73.4s` **[INF]** So ~13 M/s is not one stage's speed limit; the queue forms wherever the loop's rhythm puts it.
   - spoken: So, our reading: thirteen million per second is not the speed limit of any one stage. The queue forms wherever the rhythm of the loop puts it.
   - source: §E3 [INF]

### S06 Exp 4 · Who sets the pace? (90 s)

1. `   0.6s` **Experiment 4**: who sets the pace? Add a known delay **δ** to every message on one side, and watch the time per message.
   - spoken: Experiment four asks who sets the pace. The trick: add a known delay, delta, to every message on one side, and watch the time per message.
   - source: §E4
2. `  10.0s` δ is a chain of dependent multiplies between two fences, so the CPU can't hide it. At W = 1 it adds what we ask: **+40 ns → +40.1 ns**.
   - spoken: Delta is a chain of dependent multiplies between two fences, so the processor cannot hide it. With a window of one, it adds what we ask: forty nanoseconds become forty point one.
   - source: §E4
3. `  21.4s` Each run sends **20 million** messages; time per message = total ÷ 20 M. **Slope 1**: that side is on the critical path. **Slope 0**: it was waiting anyway.
   - spoken: Each run sends twenty million messages, so time per message resolves changes of a fraction of a nanosecond. Slope one: that side is on the critical path. Slope zero: it was waiting anyway.
   - source: §E4
4. `  33.6s` Fast state (1 card core, W = 1024): host slope **1.07**, card slope **1.07**. Both sides are on the critical path, tightly interlocked.
   - spoken: Fast state, one card core, window ten twenty-four: both slopes are one point zero seven. Host and card code are both on the critical path, tightly interlocked.
   - source: §E4
5. `  44.1s` Slow state (4 card cores, W = 64): adding up to **59 ns** on the card changes nothing, **76.1 → 75.6 ns** per message. The card just waits.
   - spoken: Slow state, four card cores, window sixty-four: adding up to fifty-nine nanoseconds on the card changes nothing. Seventy-six point one nanoseconds per message becomes seventy-five point six. The card just waits.
   - source: §E4
6. `  57.7s` Surprise on the host: **+10 ns makes it faster**, **76.2 → 60.9 ns** per message. **[INF]** The host's bursts, not its code, seem to trigger the slow state.
   - spoken: The surprise is on the host: adding ten nanoseconds makes it faster, from seventy-six point two to sixty point nine nanoseconds per message. Our reading: the host's bursts, not its code, seem to trigger the slow state.
   - source: §E4 [INF]
7. `  71.6s` At W = 1024, **4 card cores lose to 1**: **23.9 vs 50.5 M/s**. Sending costs the same **~9 ns**; reply polling grows **13.6 → 32.5 ns** across four rings.
   - spoken: And at window ten twenty-four, four card cores do worse than one: twenty-three point nine, versus fifty point five million per second. Sending still costs nine nanoseconds, but reply polling grows from thirteen point six to thirty-two point five, waiting across four rings.
   - source: §E4

### S07 Exp 5 · Remove the loop (88 s)

1. `   0.6s` **Experiment 5** removes the loop. Fixed: **one host core, one card core**, WC replies.
   - spoken: Experiment five removes the loop. Fixed: one host core, one card core, and write combining replies.
   - source: §E5
2. `   8.2s` Knob = **lockstep vs open loop**. Lockstep: a new request waits for an earlier reply. Open loop: each side sends at a set rate, independently.
   - spoken: The knob: lockstep, versus open loop. In lockstep, each new request waits for an earlier reply. In open loop, host and card each send at a set rate, independently, like two people talking at once.
   - source: §E5
3. `  21.6s` Lockstep: **14.2 M/s**. Open loop: every target from **5 to 35 M/s each way** is delivered exactly. The 13 M/s wall is gone.
   - spoken: Lockstep gives fourteen point two million per second. Open loop delivers every target exactly, from five up to thirty-five million per second, each way. The thirteen million wall is gone.
   - source: §E5
4. `  33.7s` At **35 + 35 M/s**, median one-way latency is **0.97 µs** forward and **1.24 µs** return.
   - spoken: At thirty-five plus thirty-five, the median one-way latency is zero point nine seven microseconds forward, and one point two four back.
   - source: §E5
5. `  43.0s` Unthrottled: **41.9 + 41.9 = 83.7 M/s** from one host core and one card core. Card receive and send on two cores: **91 M/s** (97 with DMA replies).
   - spoken: Unthrottled, one host core and one card core move forty-one point nine each way, eighty-three point seven in total. With the card's receiving and sending split over two cores: ninety-one, or ninety-seven with D M A replies.
   - source: §E5
6. `  58.2s` But those are medians. At 35 + 35 the forward **p99 is 38.7 µs**; at 20 + 20 even the median is **16.9 µs**. The tail is unexplained.
   - spoken: But those are medians. At thirty-five plus thirty-five, the ninety-ninth percentile forward latency is thirty-eight point seven microseconds, and at twenty plus twenty even the median is sixteen point nine. That tail is unexplained.
   - source: §E5 tails
7. `  72.7s` When each reply is **triggered by a request**, p99 jumps to **35 µs** from **13.5 M/s**. Open loop sidesteps the wall; it does not explain it.
   - spoken: And when each reply is triggered by a request, as in real request-response traffic, the ninety-ninth percentile jumps to thirty-five microseconds from thirteen and a half million per second. Open loop sidesteps the wall; it does not explain it.
   - source: §E5 echo

### S08 Exp 6a · Interleaved replies (65 s)

1. `   0.6s` **Experiment 6** rules out two suspects. First: with 4 card cores, replies land in different **64 KB regions**. Does that break write-combining?
   - spoken: Experiment six rules out two suspects. First: with four card cores, replies land in different sixty-four kilobyte regions of host memory. Does jumping around break write combining?
   - source: §E6a
2. `  12.4s` Fixed: closed loop, 4 card cores, W = 64. Knob = replies written **contiguously** in one region vs **interleaved** across regions.
   - spoken: Fixed: the closed loop, four card cores, and a window of sixty-four. The knob: replies written contiguously in one region, versus interleaved across regions.
   - source: §E6a
3. `  23.5s` Host counters: full 64-byte line writes per reply **1.0001 vs 1.0000**, i.e. **99.99%** full lines. Throughput **13.14 vs 13.16 M/s**.
   - spoken: Host counters see one full sixty-four byte line write per reply in both layouts: ninety-nine point nine nine percent full lines. Throughput: thirteen point one four versus thirteen point one six.
   - source: §E6a
4. `  36.6s` Why: each reply **is** one aligned 64-byte line. Its four 16-byte stores fill the combining buffer exactly, and it leaves whole before the next reply starts.
   - spoken: Why? Each reply is itself one aligned sixty-four byte line. Its four stores, of sixteen bytes each, fill the combining buffer exactly, and it leaves as one whole write before the next reply even starts.
   - source: §E6a
5. `  50.2s` Caveat: equal throughput in the slow state is weak evidence. At **W = 1024** the same change costs **3%** (**21.88 → 21.22 M/s**), still unexplained.
   - spoken: One caveat: equal throughput in the slow state is weak evidence, since both sides were waiting. At window ten twenty-four the same change costs three percent, still unexplained. The line count is the real proof.
   - source: §E6a caveat

### S09 Exp 6b · 64 B or 128 B? (64 s)

1. `   0.6s` Second suspect: Linux reports a **128-byte** coherency line for the card's L3. Then cores writing **neighboring 64-byte slots** would fight.
   - spoken: Second suspect: Linux reports a one hundred twenty-eight byte coherency line for the card's L 3 cache. If true, two cores writing neighboring sixty-four byte slots would fight over one line.
   - source: §E6b
2. `  13.4s` Fixed: two card cores, each rewriting **its own** 64-byte slot. Knob = the distance between the slots: **0 B, 64 B, 128 B, 256 B, 4 KB**.
   - spoken: Fixed: two card cores, each rewriting its own sixty-four byte slot, as fast as it can. The knob: the distance between the two slots, from zero bytes up to four kilobytes.
   - source: §E6b
3. `  25.7s` Same slot (0 B): each core manages only **72.3 M writes/s**, and coherence snoops jump to **17.8 M/s**.
   - spoken: When both write the same slot, each core manages only seventy-two point three million writes per second, and coherence snoops jump to seventeen point eight million per second.
   - source: §E6b
4. `  36.8s` **64 B apart**, inside one 128-byte block: **259.8 M/s** per core, snoops at the idle background, **0.52 M/s**. Same at 128 B, 256 B, 4 KB.
   - spoken: At sixty-four bytes apart, still inside one Linux-sized line, each core runs at two hundred fifty-nine point eight million, and snoops stay at the idle background. The same at one twenty-eight, two fifty-six, and four kilobytes.
   - source: §E6b
5. `  51.1s` So card cores track coherence per **64 bytes**. Still untested: PCIe writes landing right next to a polling core.
   - spoken: So the card's cores track coherence per sixty-four bytes, and neighboring slots are safe. Still untested: P C I e writes landing right next to a slot that a core is polling.
   - source: §E6b caveat

### S10 What we learned (67 s)

1. `   0.6s` One way, the path is fast as long as receivers **stay behind the landing band**: **161.6 M/s** from one host core.
   - spoken: Putting it together. One way, the path is fast as long as receivers stay behind the band of writes still landing: one hundred sixty-one point six million per second from one host core.
   - source: §E1, §E2
2. `  11.8s` In a closed loop it settles near **13 M/s**: both sides mostly wait, the queue sits **in transit**, and the host's **send pacing** changes it.
   - spoken: In a closed loop, it settles near thirteen million. Both sides mostly wait, the queue sits in transit, forward or back, and the pacing of the host's sends changes it.
   - source: §E3, §E4
3. `  22.3s` Without the loop, one host core moves **83.7 M/s** both ways at **~1 µs** median latency. But **p99 tails** of tens of µs remain.
   - spoken: Without the loop, one host core moves eighty-three point seven million per second in both directions, at about one microsecond median latency. But tail latencies of tens of microseconds remain.
   - source: §E5
4. `  34.0s` Ruled out: reply interleaving does **not** break write-combining, and card cores do **not** interfere at 64-byte spacing.
   - spoken: Ruled out: interleaving replies does not break write combining, and card cores do not interfere at sixty-four byte spacing.
   - source: §E6a, §E6b
5. `  41.7s` Next: explain and remove the slow state (pace host bursts, cap reply polling, sweep the mesh clock), and report p99 over long runs.
   - spoken: Next, we want to explain and remove the slow state: pace the host's bursts, cap how many replies it polls at once, and sweep the mesh clock. And report tail latency over long runs.
   - source: §Next
6. `  54.0s` Then: let card cores drive the host's **Intel DSA** engine directly, and build a **minimal message layer** with a real request–response workload.
   - spoken: Then two directions. Let card cores drive the host's Intel D S A copy engine directly, and build a minimal message layer, tested with a real request-response workload.
   - source: §Next
