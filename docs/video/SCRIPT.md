# Host ⇄ DPU over PCIe (Marvell OCTEON 10 CN106xx): narrated explainer script

Measurements from `~/dev/marvell` (Rust testbed, 2026-11-10/12). Every number's provenance is in `data/FACTS.md`; `[INF]` marks our inferences.

## Build

1. Edit captions/narration only in `tts/script_data.py`, then `python3 tts/gen_scenes.py --script` (rewrites only the `// <cues>` blocks and this file).
2. Narration: `bun tts/manifest.ts && ~/tts/asr/bin/python tts/synth.py` (edge-tts en-US-AndrewNeural, cached by text), then rerun `gen_scenes.py` to retime.
3. Check: `npx tsc -p .`; `./stills.sh S05 3 9 15` → half-size contact sheet `out/S05-sheet.png`.
4. Final: `./render.sh` → `out/cn106-pcie-datapath.mp4` (1920×1080 @ 30 fps).

## Scenes

| Scene | Title | Length | Cues | Source |
|---|---|---|---|---|
| S01 | Host ⇄ DPU over PCIe | 48 s | 5 | Marvell OCTEON 10 CN106xx · host saturn · Nov 2026 |
| S02 | The testbed | 51 s | 6 | testbed/ (Rust) · host DPDK 26.10.1 · card DPDK 24.11 |
| S03 | Baseline: SDP and its doorbell | 64 s | 7 | pingpong · bench/batch_sweep.sh · octeon_ep PMD |
| S04 | Send path: write into card memory | 60 s | 6 | inline_pingpong (host) · inline_reflector (card) · BAR4 |
| S05 | Order at the destination | 45 s | 5 | testbed::inline · cf. Liew et al., ASPLOS '26 |
| S06 | A measurement trap | 50 s | 5 | dpdk::rdtsc_after_loads · Xeon Gold 6438M |
| S07 | Way back #1: poll memory, not MMIO | 59 s | 6 | octeon_ep PMD · devarg rx_poll_mem=1 (local patch) |
| S08 | Way back #2: the card's DMA engine | 72 s | 6 | inline_reflector --dma · DPI VF 0000:06:00.1 |
| S09 | Way back #3: just read it | 56 s | 5 | inline_pingpong --reply pull --pull-delay-ns 150 |
| S10 | Way back #4: a hidden outbound window | 83 s | 6 | PEM0_REG_NORM_ACC · Cavium BDK · EBF disassembly |
| S11 | Way back #4: card cores store replies | 91 s | 7 | inline_reflector --store · inline_pingpong --reply ring |
| S12 | Scaling out: more card cores | 73 s | 7 | inline_reflector -l 3-6 · host rx_poll_mem=1 |
| S13 | Scaling DMA: fewer, bigger copies | 62 s | 6 | inline_reflector --dma … --dma-coalesce 32 |
| S14 | Why trust these numbers | 42 s | 5 | testbed::closed_loop · --corrupt-every 1000 |
| S15 | Takeaways | 68 s | 6 | ~/dev/marvell · AGENTS.md · testbed/ |

## Cues (caption / narration / provenance)

### S01 Host ⇄ DPU over PCIe (48 s)

1. `   0.6s` How fast can a **64-byte message** make a round trip between a server and the DPU plugged into it?
   - spoken: How fast can a sixty-four byte message make a round trip, between a server and the D P U plugged into it?
   - source: §Setup
2. `   7.4s` Host **saturn** (Xeon Gold 6438M) and a Marvell **OCTEON 10 CN106xx** card, linked by **PCIe Gen5 x4**.
   - spoken: Our host, saturn, is a Xeon Gold server. The card is a Marvell Octeon ten, C N one oh six, on P C I express gen five, by four.
   - source: §Setup
3. `  18.5s` The stock packet path, **SDP**: **4.85 µs** per round trip, and at most **~7.5 M msg/s**.
   - spoken: The stock packet path, called S D P, takes four point eight five microseconds per round trip, and tops out around seven and a half million messages per second.
   - source: §SDP
4. `  28.9s` Where we ended up: **0.83 µs** one message at a time, and up to **~28 M msg/s** with four card cores.
   - spoken: By the end, one message at a time takes zero point eight three microseconds, and four card cores reach up to about twenty-eight million messages per second.
   - source: §Pull, §MultiSDP
5. `  38.7s` Roadmap: the baseline → a faster send path → four ways back → scaling out → why we trust the numbers.
   - spoken: The road map: the baseline, a faster send path, four different ways back to the host, scaling out, and why we trust the numbers.

### S02 The testbed (51 s)

1. `   0.6s` A **closed loop**: the host sends a request, the card reflects it, the host matches the reply.
   - spoken: The experiment is a closed loop. The host sends a request, the card reflects it back, and the host matches the reply.
   - source: §Method
2. `   8.3s` Each message carries a **sequence number**, the **send timestamp**, a payload **pattern**, and a trailing **seq copy**.
   - spoken: Each message carries a sequence number, its send timestamp, a payload pattern, and a second copy of the sequence number at the very end.
   - source: §Method
3. `  16.6s` **Concurrency W** = messages in flight. **W=1** measures latency; a large W measures message rate.
   - spoken: Concurrency, W, is the number of messages in flight. A window of one measures latency. A large window measures message rate.
   - source: §Method
4. `  24.9s` **Batch** = messages per doorbell. Here the batch is always **1**: one message, one notification.
   - spoken: Batch is the number of messages that share one doorbell. Here the batch is always one: one message, one notification.
   - source: §Method
5. `  32.9s` Both ends are **Rust** on DPDK: x86 host, Arm card, one wire format, one set of checks.
   - spoken: Both ends are written in Rust on top of D P D K: x86 on the host, Arm on the card, with one wire format and one set of checks.
   - source: §Method
6. `  42.8s` Cross-check on every result: at **W=1**, the round-trip time must equal **1 / message rate**.
   - spoken: And every result gets a cross check. With a window of one, the round trip time has to equal one over the message rate.
   - source: §Method

### S03 Baseline: SDP and its doorbell (64 s)

1. `   0.6s` **SDP** is the card's packet engine: descriptor rings on both sides, and a **doorbell** register that says “new work”.
   - spoken: S D P is the card's packet engine. Each side keeps descriptor rings, and a doorbell register announces new work.
   - source: §SDP
2. `   8.2s` W=1 round trip: **4.85 µs** p50. The rate grows with W, then flattens at **7.2–7.7 M msg/s** from W=128.
   - spoken: At a window of one, the median round trip is four point eight five microseconds. The rate grows with the window, then flattens at seven point two to seven point seven million messages per second.
   - source: §SDP
3. `  20.4s` One core sending one packet per doorbell: **8.36 M pkt/s**, about **120 ns** each.
   - spoken: Why so low? One host core, sending one packet per doorbell, manages eight point three six million packets per second. That's about a hundred and twenty nanoseconds each.
   - source: §Doorbell
4. `  30.9s` Over 1, 2, 4, 8 TX queues: **8.36 / 7.32 / 6.59 / 7.06 M pkt/s**. Separate doorbells don't overlap.
   - spoken: Spreading the packets over one, two, four, or eight queues doesn't help at all. Separate doorbells still don't overlap.
   - source: §Doorbell
5. `  39.1s` **[INF]** Each doorbell is an **8-byte uncached store**, and the core issues them **one at a time**.
   - spoken: Our reading: each doorbell is an eight byte uncached store across the link, and a core issues them strictly one at a time.
   - source: §Doorbell [INF]
6. `  47.0s` Batching amortizes it: **32** per doorbell reaches **50.4 M pkt/s**. But a request–reply loop sends one at a time.
   - spoken: Batching does amortize it. Thirty-two packets per doorbell reach fifty million packets per second. But a request and reply loop sends one message at a time.
   - source: §Doorbell
7. `  56.6s` So: skip the descriptor ring and its doorbell, and write the message **straight into card memory**.
   - spoken: So the plan is to skip the descriptor ring and its doorbell, and write the message straight into card memory.

### S04 Send path: write into card memory (60 s)

1. `   0.6s` The card's PCIe function exposes **BAR4**: 16 windows of **4 MiB**; a card register sets where each one points.
   - spoken: The card's P C I function exposes BAR four: sixteen windows of four megabytes, and a card register decides where each one points.
   - source: §Inline
2. `  10.0s` On the card, `inline_reflector` repoints **window 15** at a 4 MiB hugepage, and restores it on exit.
   - spoken: On the card, our reflector repoints window fifteen at a four megabyte hugepage, and restores the old mapping when it exits.
   - source: §Inline
3. `  19.0s` The host writes each 64 B **cell** with one **MOVDIR64B**: a single 64-byte store, and no doorbell.
   - spoken: The host writes each sixty-four byte cell with one move dir sixty-four B instruction: a single sixty-four byte store, and no doorbell.
   - source: §Inline
4. `  28.3s` A card core **polls** the next cell in its ring. The hugepage sees PCIe writes coherently: nothing to flush.
   - spoken: A card core simply polls the next cell in its ring. The hugepage sees incoming P C I express writes coherently, so nothing needs flushing.
   - source: §Inline
5. `  38.2s` W=1 round trip: **4.85 → 2.41 µs**, with replies still coming back over SDP.
   - spoken: The round trip drops from four point eight five to two point four one microseconds, and that's with replies still coming back over S D P.
   - source: §Inline
6. `  47.0s` One card core becomes the limit: **13.7–15.9 M msg/s** while checking every payload (varies by session), vs **~7.5** for SDP.
   - spoken: Throughput roughly doubles. One card core becomes the limit, at fourteen to sixteen million messages per second while checking every payload, against seven and a half for S D P.
   - source: §Inline, §MultiSDP

### S05 Order at the destination (45 s)

1. `   0.6s` MOVDIR64B stores are **weakly ordered**: without a fence, cell 8 may land before cell 7.
   - spoken: Move dir stores are weakly ordered. Without a fence, cell eight may land in card memory before cell seven.
   - source: §Order
2. `   7.8s` The textbook fix: an **SFENCE** after every cell. It works, but caps the host at **6.86 M msg/s** (W=128).
   - spoken: The textbook fix is an S fence after every cell. It works, but it caps the host at six point eight six million messages per second.
   - source: §Order
3. `  16.5s` Instead, the **receiver** checks: a cell counts only when **head seq = tail seq** and the pattern matches.
   - spoken: Instead, the receiver checks. A cell counts only when its head sequence number equals the tail copy, and the payload pattern matches.
   - source: §Order
4. `  25.2s` No fences: the host pushes **26.2 M msg/s** when the card skips the payload check. Torn cells seen: **0**.
   - spoken: Without fences, the host pushes twenty-six million messages per second when the card skips the payload check. Torn cells seen across every run: zero.
   - source: §Order, §Verify
5. `  35.1s` Same idea as **destination-side ordering** (Liew et al., ASPLOS '26): pay for order where it's checked, not where it's sent.
   - spoken: It's the same idea as destination side ordering, from Liew and colleagues at ASPLOS twenty-six: pay for ordering where it's checked, not where it's sent.
   - source: §Order

### S06 A measurement trap (50 s)

1. `   0.6s` Before working on the way back, we nearly fooled ourselves: the inline path first measured **1.87 µs**.
   - spoken: Before working on the way back, we nearly fooled ourselves. The inline path first measured one point eight seven microseconds.
   - source: §RDTSC
2. `   9.1s` But **1 / message rate** said **2.41 µs**. At W=1 they must agree, so one of them was wrong.
   - spoken: But one over the message rate said two point four one. At a window of one those must agree, so one of them was wrong.
   - source: §RDTSC
3. `  16.4s` The host polls an SDP counter with an **MMIO read**, then reads the clock with **RDTSC**, which doesn't wait for loads.
   - spoken: The host polls an S D P counter with an M M I O read, then reads the clock with R D T S C. And R D T S C doesn't wait for earlier loads.
   - source: §RDTSC
4. `  27.4s` So the timestamp was taken **before the read returned**: about **0.5 µs** shaved off every round trip.
   - spoken: So the arrival timestamp was taken before the read had returned, shaving about half a microsecond off every round trip.
   - source: §RDTSC
5. `  35.0s` Fix: **RDTSCP** waits for earlier loads. **LFENCE; RDTSC** gives the same **2.41 µs**. Cost: **20.9** vs **22.0** TSC ticks.
   - spoken: The fix is R D T S C P, which waits for earlier loads. An L fence plus R D T S C gives the same two point four one microseconds; R D T S C P is one instruction, and slightly cheaper.
   - source: §RDTSC

### S07 Way back #1: poll memory, not MMIO (59 s)

1. `   0.6s` An **uncached read** of card memory costs **0.63–0.66 µs**: one full round trip over PCIe.
   - spoken: Any uncached read across the link is expensive. Reading card memory from the host takes zero point six three to zero point six six microseconds: one full round trip.
   - source: §Budget
2. `  11.8s` The stock driver learns about replies by reading the **OUT_CNTS** register: an MMIO read on every poll.
   - spoken: Yet the stock driver learns about new replies by reading the out counts register: an M M I O read on every poll.
   - source: §MemPoll
3. `  20.0s` SDP also writes an **info word** into host memory with each frame. Polling that instead: **2.41 → 2.00 µs**.
   - spoken: But S D P also writes an info word into host memory, with each frame. Polling that instead cuts the round trip from two point four one to two microseconds.
   - source: §MemPoll
4. `  30.6s` The catch: the info word lands with the frame's **head**. The **tail** can arrive up to **6.3 µs** later.
   - spoken: The catch: the info word lands with the head of the frame. The tail can arrive up to six point three microseconds later.
   - source: §MemPoll
5. `  37.9s` Late tails per million frames: **0** at 64 B · ≤ **2** at 256–512 B · up to **35** at 1024 B · up to **193** at 1514 B.
   - spoken: Per million frames, late tails go from zero at sixty-four bytes, to two or fewer at two fifty-six and five twelve, to as many as a hundred ninety-three at full size. None were lost.
   - source: §MemPoll
6. `  50.3s` So `rx_poll_mem=1` stays **opt-in**: it is safe only because the testbed waits for the trailing seq copy.
   - spoken: So this mode stays opt-in. It is safe here only because the testbed waits for the trailing sequence copy before trusting a frame.
   - source: §MemPoll

### S08 Way back #2: the card's DMA engine (72 s)

1. `   0.6s` The card has its own DMA engine, **DPI**. The reflector copies each reply straight into a **ring in host memory**.
   - spoken: The card has its own D M A engine, called D P I. The reflector uses it to copy each reply straight into a ring in host memory.
   - source: §DMA
2. `  10.5s` DPI writes as host **VF0**, so it must target an address inside the host DPDK process: the **IOMMU** is on.
   - spoken: The D P I writes on behalf of the host's virtual function zero, so it must target an address inside the host D P D K process. The I O M M U is on.
   - source: §DMA
3. `  21.1s` The host polls only its own memory, with no MMIO at all. W=1 round trip: **1.33–1.60 µs** p50, run to run.
   - spoken: Now the host polls only its own memory, with no M M I O at all. The median round trip is one point three three to one point six microseconds, depending on the run.
   - source: §DMA
4. `  32.1s` Card side, measured: poll loop **18 ns** · cell seen → DMA doorbell **59–67 ns** · DPI doorbell → done **675 ns**.
   - spoken: On the card, we timed each piece. The poll loop takes eighteen nanoseconds. Seeing a cell and ringing the D M A doorbell takes about sixty. And the D P I needs six hundred seventy-five nanoseconds to finish the copy.
   - source: §Budget
5. `  46.7s` That 675 ns ignores the source line's cache state, and cleaning caches first only made it slower: it's **inside DPI**.
   - spoken: Those six hundred seventy-five nanoseconds don't change with the cache state of the source, and cleaning caches first only made things slower. The cost is inside the D P I.
   - source: §DMA
6. `  57.5s` **[INF]** Budget: ~0.3 host→card + ~0.08 card + ~0.6 DPI + ~0.3 card→host ≈ **1.3 µs**. One-way halves are inferred.
   - spoken: Adding it up, we estimate point three microseconds host to card, point zero eight on the card, point six in the D P I, and point three back. The one way halves are our inference, from splitting the measured read in two.
   - source: §Budget [INF]

### S09 Way back #3: just read it (56 s)

1. `   0.6s` If each DPI copy takes **~0.67 µs**, why not let the **host fetch the reply** with one read?
   - spoken: If each D P I copy takes about two thirds of a microsecond, why not let the host fetch the reply itself, with a single read?
   - source: §Pull
2. `   8.8s` The card stores (tag, timestamp) in its control cell. The host waits **150 ns**, then does one **16 B uncached read**.
   - spoken: The card stores the tag and timestamp in its control cell. The host waits a hundred and fifty nanoseconds, then does a single sixteen byte uncached read.
   - source: §Pull
3. `  18.8s` W=1 p50: **0.83 µs** (repeats 0.83 / 0.83 / 1.04). The fastest round trip of all.
   - spoken: The median round trip is zero point eight three microseconds. Repeats gave point eight three, point eight three, and one point zero four. The fastest of all.
   - source: §Pull
4. `  29.4s` Wait sweep (p50): 0 ns **0.89** bimodal · 50–100 ns **1.43** · 150 ns **0.83** · 200 **0.88** · 300 **0.98** · 400 **1.09**.
   - spoken: The wait matters. With no wait, results are bimodal. At fifty to a hundred nanoseconds the first read misses, and a second read costs one point four three. A hundred fifty is the sweet spot; longer waits only add time.
   - source: §Pull
5. `  43.5s` The price: the host core **stalls 0.63 µs** per read, so pull fits only **W=1**, at **1.17 M msg/s**.
   - spoken: The price: the host core stalls for point six three microseconds on every read. So pull only fits one message at a time, at about one point two million messages per second.
   - source: §Pull

### S10 Way back #4: a hidden outbound window (83 s)

1. `   0.6s` Can a card core write host memory **itself**, with no DMA engine? Marvell's open drivers only set up **host → card** windows (BAR4).
   - spoken: Can a card core write host memory by itself, with no D M A engine in between? Marvell's open source drivers only set up windows in the other direction, host to card.
   - source: §StoreFind
2. `  11.5s` Marvell's open docs stop at inbound BAR4. But the open Cavium **BDK** in coreboot documents `PEM()_REG_NORM()_ACC`: “for access to memory from cores”.
   - spoken: Marvell's open documents stop at inbound BAR 4. But the open source Cavium B D K, which lives in coreboot, documents a PEM table for memory access from the cores.
   - source: §StoreFind
3. `  23.7s` **256 regions**, each mapping **2 GiB** of card-core addresses onto PCIe. Its `pf / vf / vf_active` fields “must be zero in RC mode”: an **endpoint** may use them.
   - spoken: It has two hundred fifty-six regions, each mapping two gigabytes of core address space onto P C I express. Its function fields must be zero in root complex mode, which hints that an endpoint can send writes as a chosen function.
   - source: §StoreFind
4. `  38.5s` Disassembling the card firmware gave CN106's real layout: table at PEM0 **+0x4000 + 16·i**; region i at **0x8800_0000_0000 + i·2 GiB**; all 256 unused in EP mode.
   - spoken: That header describes the older C N nine chips, and the offsets differ. So we disassembled the card's firmware, found where it sets up this table, and read off the real layout for C N one oh six. In endpoint mode, all two hundred fifty-six regions sit unused.
   - source: §StoreFind
5. `  55.4s` Point region **255** at the host reply ring, writing as host **VF0** (`vf_active`, vf 0). It worked on the first try, through the host **IOMMU**.
   - spoken: We pointed region two fifty-five at the host's reply ring, writing as the host's virtual function zero. It worked on the first try, straight through the host I O M M U.
   - source: §StoreFind
6. `  66.4s` Lesson: one read-only probe of a CSR the firmware reads (`0x87e0_0300_1000`) raised a card **SEI-UER** error (2 → 3). The firmware runs in the secure world.
   - spoken: One lesson along the way: a single read of a register that the firmware also reads raised a hardware error on the card. The firmware runs in the secure world, and Linux can't safely touch the same address. The card survived, and no further errors appeared.
   - source: §StoreFind

### S11 Way back #4: card cores store replies (91 s)

1. `   0.6s` Each card core now writes its 64 B replies straight into the host ring. W=1 p50: **0.94–0.98 µs** (some runs **1.23–1.27**).
   - spoken: Now each card core writes its sixty-four byte replies straight into the host's ring. The median round trip is zero point nine four to zero point nine eight microseconds, with some runs at one point two three to one point two seven.
   - source: §Store
2. `  15.1s` The first **push** under 1 µs: DPI DMA took **1.33–1.60 µs**. On the card, the stores plus a completion barrier cost **21 ns** per reply, vs **675 ns** for DPI.
   - spoken: That makes it the first push path under one microsecond; the D P I took one point three three to one point six. On the card, the stores plus a completion barrier cost twenty-one nanoseconds per reply, against six hundred seventy-five for the D P I.
   - source: §Store, §Budget
3. `  31.0s` Trap 1: one 64-byte `ST1` store is split in flight; head and tail land **before the middle**, and the host read a stale payload.
   - spoken: But two traps showed up as real bad data. First: a single sixty-four byte store instruction gets split on the way, and the head and tail can land before the middle. The host then reads a stale payload.
   - source: §Store
4. `  43.2s` Trap 2: PEM write merging (`wnmerge=0`) reorders the pieces too. Fix: four 16 B `STP` stores, **head last**, merging off.
   - spoken: Second: letting the PEM merge writes reorders the pieces too. The fix: four separate stores of sixteen bytes each, writing the head last, with merging turned off.
   - source: §Store
5. `  54.1s` Verified: **>40 M** replies with lost / bad / torn **0**; 100 corrupted on purpose, **100** caught by the host.
   - spoken: With that, over forty million replies came back with zero lost, bad or torn. And of a hundred replies corrupted on purpose, the host caught all hundred.
   - source: §Store
6. `  64.5s` Rate: 1 card core **13.9–17.7 M msg/s**, 2–4 cores **13–17.6**. Every host poll finds a reply: the **host** is the limit.
   - spoken: Throughput is thirteen point nine to seventeen point seven million messages per second with one card core, and no better with two to four. Every host poll finds a reply waiting, so the host is the limit.
   - source: §Store
7. `  76.7s` **[INF]** Likely cause: four partial writes per reply make the host core and PCIe fight over one cache line. Next: one full-line write per reply.
   - spoken: Our guess, not yet verified: each reply arrives as four partial writes to one cache line, so the polling core and the link keep fighting over that line. The next step is a single full line write per reply.
   - source: §Store [INF]

### S12 Scaling out: more card cores (73 s)

1. `   0.6s` One card core with SDP replies: **13.66 M msg/s**, while the host's memory polls come back **97%** empty.
   - spoken: With one card core and S D P replies, the rate stops at thirteen point seven million, while ninety-seven percent of the host's polls come back empty. The card is the limit.
   - source: §MultiSDP
2. `  11.1s` Spread the ring across cores: core i takes every n-th cell and replies on its own SDP queue.
   - spoken: So we spread the ring across card cores. With four cores, each core takes every fourth cell, and replies on its own S D P queue.
   - source: §MultiSDP
3. `  20.2s` Gotcha: card TX queue i lands on host RX queue i. With one host queue, the other cores' replies **silently vanished**.
   - spoken: One gotcha: card queue zero lands on host queue zero, card queue one on host queue one, and so on. With a single host queue, the other cores' replies silently vanished. Now the host polls one queue for each card core.
   - source: §MultiSDP
4. `  33.9s` 1 core **13.66** → 2 cores **17.1–17.2 M msg/s**. Neither side looks saturated at 2 cores: **unexplained** so far.
   - spoken: Two cores reach seventeen point two million. But at two cores neither side looks saturated, and we can't yet explain that plateau.
   - source: §MultiSDP
5. `  42.1s` At 4 cores the **host** becomes the bottleneck: four OUT_CNTS reads per poll → **13.0–13.3 M msg/s** (W=512).
   - spoken: At four cores, the host becomes the bottleneck. Reading four out counts registers on every poll drags the rate down to thirteen million.
   - source: §MultiSDP
6. `  50.8s` Poll memory instead: **24.4–27.9 M msg/s** at W=1024, about **1.8 GB/s** of 64-byte messages.
   - spoken: Polling memory instead lifts it to between twenty-four and twenty-eight million messages per second, about one point eight gigabytes per second of sixty-four byte messages.
   - source: §MultiSDP
7. `  60.9s` The price: W=1 gets slower with more queues. 2 cores: **2.76 µs** with OUT_CNTS, **2.06 µs** polling memory.
   - spoken: The price is latency. One message at a time gets slower with more queues: two point seven six microseconds with two cores, or two point zero six when polling memory.
   - source: §MultiSDP

### S13 Scaling DMA: fewer, bigger copies (62 s)

1. `   0.6s` Same trick for DMA: one DPI queue per card core. It got **worse**: **13–15 M msg/s** in total.
   - spoken: We tried the same trick for D M A, with one D P I queue per card core. It got worse: thirteen to fifteen million in total.
   - source: §MultiDMA
2. `   9.1s` DPI doorbell → done: **0.67 µs** (1 queue, W=1) → **5.8 µs** (2 queues, loaded) → **21–24 µs** (4). The engine is saturated.
   - spoken: The D P I's completion time shows why: point six seven microseconds for one idle queue, five point eight with two loaded queues, and over twenty with four. The engine itself is saturated.
   - source: §MultiDMA
3. `  21.3s` So send fewer copies: split the host ring into **per-core sub-rings**, so one core's batch of replies is **contiguous**.
   - spoken: So the answer is fewer copies. We split the host's reply ring into per-core sub-rings, so one core's batch of replies sits side by side.
   - source: §MultiDMA
4. `  30.3s` Then one DMA covers a batch: **15–19 replies per copy**. One core: **15.1 → 20.0–20.9 M msg/s**.
   - spoken: Then a single D M A covers the whole batch, fifteen to nineteen replies per copy. One card core goes from fifteen to about twenty-one million messages per second.
   - source: §MultiDMA
5. `  40.9s` Plus one host timestamp per poll instead of per reply: **21.7–23.2 M msg/s** (host polls now 77–83% empty).
   - spoken: Taking one host timestamp per poll, instead of one per reply, adds about ten percent more: twenty-two to twenty-three million, and the host is mostly idle again.
   - source: §MultiDMA
6. `  51.4s` With 2–4 cores each poll finds only **1.3–2.7** cells: little to coalesce. DMA stays best on **one core**.
   - spoken: With two to four cores, each poll finds only one to three cells, so there's little to coalesce. D M A stays best on a single core.
   - source: §MultiDMA

### S14 Why trust these numbers (42 s)

1. `   0.6s` Every reply is matched to an **outstanding request**: its sequence number and its exact send timestamp.
   - spoken: Every reply is matched to an outstanding request: its sequence number, and its exact send timestamp.
   - source: §Verify
2. `   7.5s` It must also carry the **trailing seq copy** and the right **payload pattern**, or it counts as **torn** or **bad**.
   - spoken: It must also carry the trailing sequence copy and the right payload pattern. Otherwise it counts as torn, or bad, and the run fails.
   - source: §Verify
3. `  16.3s` Every run in this video: lost **0**, bad **0**, torn **0**, and the card's count equals the host's exactly.
   - spoken: Across every run in this video: zero lost, zero bad, zero torn, and the card's count equals the host's exactly.
   - source: §Verify
4. `  25.1s` Do the checks fire? Corrupt every 1000th payload on purpose: the card flags **100**, the host flags **100**.
   - spoken: Do the checks actually fire? We corrupted every thousandth payload on purpose. The card flagged a hundred, and the host flagged the same hundred.
   - source: §Verify
5. `  33.7s` And the link stays clean: host **AER** error counters read **0** for the card and its root port.
   - spoken: And the link stays clean: the host's error reporting counters read zero, for both the card and its root port.
   - source: §Verify

### S15 Takeaways (68 s)

1. `   0.6s` W=1 ladder: SDP **4.85** → inline + SDP **2.41** → poll memory **2.00** → DPI DMA **~1.4** → card stores **~0.96** → pull **0.83 µs**.
   - spoken: Here's the latency ladder: four point eight five with S D P, two point four one writing inline, two by polling memory, about one point four with the card's D M A, about point nine six when card cores store directly, and zero point eight three with pull.
   - source: §SDP, §Inline, §MemPoll, §DMA, §Store, §Pull
2. `  16.4s` Lesson 1: **uncached accesses set the pace**, not bandwidth: ~120 ns per doorbell, ~0.63 µs per MMIO read.
   - spoken: Lesson one: uncached accesses set the pace, not bandwidth. About a hundred twenty nanoseconds per doorbell, and point six three microseconds per read.
   - source: §Doorbell, §Budget
3. `  26.1s` Lesson 2: **check order at the destination** instead of fencing at the source. It more than doubled the send rate.
   - spoken: Lesson two: check ordering at the destination instead of fencing at the source. It more than doubled the send rate.
   - source: §Order
4. `  32.9s` Max rate: SDP + 4 cores + mem polling, **24–28 M/s** · Balanced: DMA + coalescing, **22–23 M/s** · Fastest push: card stores, **~0.96 µs** · Min latency: pull, **0.83 µs**.
   - spoken: So pick by goal. Maximum rate: S D P replies on four card cores, with memory polling. High rate with decent latency: D M A on one core, with coalescing. The fastest push: card cores storing directly. And the absolute minimum: pull.
   - source: §MultiSDP, §MultiDMA, §Store, §Pull
5. `  49.2s` Still open: the host-side ceiling for card stores (**~18 M/s**), the 2-core plateau at **17.2 M/s**, two-mode W=1 runs (**0.97 vs 1.23 µs**), a multi-core host.
   - spoken: Still open: the host side ceiling for card stores at about eighteen million, the two-core plateau at seventeen million, runs that settle in one of two latency modes, and a multi-core host.
   - source: §Store, §Open
6. `  60.8s` Measure the pieces, cross-check the totals, verify every byte.
   - spoken: Measure the pieces, cross check the totals, and verify every byte. Thanks for watching.
