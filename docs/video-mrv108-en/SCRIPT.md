# CN106 DPU Directly Driving Host Intel DSA over Bidirectional PCIe P2P (English Explainer Script)

All measurements come from 244 verified raw runs across `logs/mrv95-2026-11-19/runs.jsonl` (94), `logs/mrv99-2026-11-19/{runs,diag_min_batch}.jsonl` (102), and `logs/mrv106-2026-11-19/runs.jsonl` (48), cross-checked by `data/verify_facts.py`. Every number's provenance is in `data/FACTS.md`; `[INF]` marks inferences.

## Build

1. Edit captions/narration only in `tts/script_data.py`, then `python3 tts/gen_scenes.py --script` (rewrites only the `// <cues>` blocks and this file).
2. Narration: `bun tts/manifest.ts && ~/tts/asr/bin/python tts/synth.py --jobs 4` (edge-tts en-US-AndrewNeural, cached by text), then rerun `gen_scenes.py --script` to retime.
3. Check: `npx tsc -p .`; `./stills.sh S02 3 12 24` -> half-size contact sheet `out/S02-sheet.png`.
4. Final: `./render.sh` -> `out/mrv108-card-dsa-en.mp4` (1920x1080 @ 30 fps); `python3 tts/qa_frames.py` -> `out/qa/S*.png`.

## Scenes

| Scene | Title | Length | Cues | Source |
|---|---|---|---|---|
| S01 | Goal: DPU Directly Driving Host DSA | 74 s | 5 | CN106 DPU <-> Xeon Socket 1 · dsa2/wq2.0 |
| S02 | Challenge 1: Two-Layer vmem Mapping | 123 s | 7 | dmar0 (VFIO) + dmar6 (SVA PASID) · Zero Host Proxy |
| S03 | Pitfall 1: WC Silent Drops vs DPI Atomic | 103 s | 6 | Single Variable: PEM0 WC vs DPI DMA 64 B · Same Portal |
| S04 | Breakthrough 2: Reverse P2P to Card BAR4 | 104 s | 6 | Single Variable: comp Host Memzone -> Card BAR4 Slot 15 |
| S05 | Surprise: Why High-W 4 KiB Halved | 77 s | 5 | W >= 32 Plateaus at 15.3 GB/s · Isolating 131 ns Bubble |
| S06 | The Fix: DSA Batch Full BW + Tight Tail | 122 s | 6 | Single Variable: Batch B = 1..32 · W = 64 · CC = 0 |
| S07 | Summary & Engineering Guardrails | 87 s | 5 | 3-Stage Evolution · Guardrails · Cold DRAM Next |

## Cues (caption / narration / provenance)

### S01 Goal: DPU Directly Driving Host DSA (74 s)

1. `   0.6s` How do we let a Marvell CN106 **DPU** directly drive the host CPU's Intel **DSA** with **zero host CPU forwarding**?
   - spoken: How do we let a Marvell C N one oh six D P U directly drive the host C P U's Intel D S A with zero host C P U forwarding?
   - source: FACTS §0
2. `  10.2s` Intel **DSA** (`dsa2`) is an on-die Xeon copy engine; traditionally, the host CPU must proxy every DPU request, burning cores and adding latency.
   - spoken: Intel D S A, specifically d s a two on Xeon Socket one, is an on-die memory copy engine. Traditionally, the host C P U has to proxy every D P U request, burning cores and adding a software hop.
   - source: FACTS §0
3. `  25.0s` Instead, we build **same-socket bidirectional PCIe P2P**: 64 B descriptors go forward to `dsa2`, and 32 B completions return straight to card memory.
   - spoken: Instead, we build same-socket bidirectional P C I e peer-to-peer: sixty-four-byte descriptors travel forward to d s a two's portal, and thirty-two-byte completions fly straight back to card memory while the host C P U sleeps.
   - source: FACTS §0, §2
4. `  41.2s` Result: a single engine reaches **31.32 GB/s** sustained copy throughput, with `W = 1` loop cycles down to **1.27 µs** (`NOP`) and **2.17 µs** (`4 KiB`), zero errors.
   - spoken: The result: a single engine reaches thirty-one point three two gigabytes per second sustained throughput, with single-in-flight loop times down to one point two seven microseconds for N O P and two point one seven microseconds for four-kilobyte copies, with zero errors.
   - source: FACTS §2, §3
5. `  58.4s` We'll walk through four steps: **two-layer vmem mapping** -> **DPI atomic submit** -> **reverse-P2P completions** -> **DSA Batch aggregation**.
   - spoken: We will walk through four steps: setting up two-layer virtual memory mapping, replacing write-combining with D P I atomic writes, eliminating read-back via reverse P 2 P, and clearing a hidden drain bubble with D S A Batch.
   - source: FACTS §1-§3

### S02 Challenge 1: Two-Layer vmem Mapping (123 s)

1. `   0.6s` To drive `dsa2` directly from the DPU without the host CPU, we must pick the right queue mode and wire **two independent vmem mappings**.
   - spoken: To drive d s a two directly from the D P U without host C P U forwarding, we first pick the right hardware queue and then wire two independent virtual memory mappings.
   - source: FACTS §1
2. `  11.4s` Protocol trap: **SWQ** requires CPU-only `ENQCMD` (`DMWr`), and `/dev/mem` splits writes into 4 x 16 B—both are silently dropped. We must use **DWQ** (`dsa2/wq2.0`).
   - spoken: First, two protocol traps: a shared work queue, or S W Q, only accepts C P U-only E N Q C M D writes, and uncached device memory splits writes into four sixteen-byte pieces. Both are silently dropped, so we must use a dedicated work queue, d s a two w q two point zero, which takes a single sixty-four-byte atomic write.
   - source: FACTS §1
3. `  35.9s` **Mapping 1 (Card -> Portal)**: `VFIO_IOMMU_MAP_DMA` (`FLAG_WRITE` only) maps `BAR2 + 0x1000` (`HPA 0x237ffff21000`) into card VF0's `dmar0` as `portal_iova`.
   - spoken: Mapping one connects the card to the D S A portal. At startup, a host init process uses V F I O I O M M U map D M A with write-only permission to map B A R two plus zero x one thousand into card V F zero's d m a r zero page table, creating portal I O V A.
   - source: FACTS §0, §1
4. `  56.7s` When the card issues a 64 B PCIe write to `portal_iova`, same-socket IIO translates it via `dmar0` and routes it straight into `dsa2`—bypassing the host CPU.
   - spoken: Now when the card sends a sixty-four-byte P C I e write to portal I O V A, the same-socket I I O translates it through d m a r zero and routes it straight into d s a two's portal, completely bypassing the host C P U.
   - source: FACTS §1
5. `  73.4s` **Mapping 2 (DSA -> Host Buffers)**: `src`, `dst`, and `comp` inside the 64 B descriptor are translated separately by `dsa2`'s own IOMMU, `dmar6`.
   - spoken: Mapping two lets D S A access host buffers. The source, destination, and completion addresses inside the sixty-four-byte descriptor are translated by a completely separate I O M M U, d m a r six.
   - source: FACTS §1
6. `  88.5s` The host init process calls `open("/dev/dsa/wq2.0")` once, binding its page-table `PASID` into `WQCFG.PASID` via **SVA**, then **sleeps** during the datapath.
   - spoken: The host process calls open on w q two point zero just once. Shared virtual addressing, or S V A, binds its page-table P A S I D into the work queue configuration, and the process then sleeps with zero datapath involvement.
   - source: FACTS §0, §1
7. ` 104.7s` The card simply fills ordinary host process `VA`s; `dsa2` attaches `PASID` automatically and walks `dmar6`—working on the first try with `SWERROR = 0`.
   - spoken: The card simply writes ordinary host virtual addresses into the descriptor, and d s a two automatically attaches that P A S I D to walk d m a r six. Both page tables relay seamlessly, with S W E R R O R staying zero.
   - source: FACTS §1

### S03 Pitfall 1: WC Silent Drops vs DPI Atomic (103 s)

1. `   0.6s` Holding the exact same `portal_iova` fixed, we isolate one variable—the card-side submit primitive: **ARM core PEM0 WC** vs **DPI DMA 64 B**.
   - spoken: Holding the exact same portal I O V A fixed, we change just one variable: the card-side submit primitive, comparing A R M core write-combining against the card's D P I D M A engine.
   - source: FACTS §1
2. `  13.9s` Card-core **PEM0 WC** gathers four 16 B stores into 64 B, hitting **1.30 µs** `W = 1 NOP` latency—**0.68 µs** faster than DPI submit (`1.98 µs`).
   - spoken: At first glance, card-core write-combining looks great: four consecutive sixteen-byte stores coalesce into sixty-four bytes, hitting one point three zero microseconds for a single N O P, zero point six eight microseconds faster than D P I.
   - source: FACTS §1
3. `  30.3s` Batch testing exposes silent drops across all three WC variants: `wc-none` **0.048%**, `wc-dgh` **0.609%**, and `wc-dsb` **1.217%**, all with `SWERROR = 0`.
   - spoken: Bulk testing immediately exposes silent drops across all three write-combining variants: from zero point zero four eight percent up to one point two one seven percent, all with S W E R R O R equal to zero.
   - source: FACTS §1
4. `  45.0s` We infer ARM WC is opportunistic coalescing: any split `< 64 B` or merged `> 64 B` TLP is silently ignored by DWQ, stalling in-order reclaim.
   - spoken: We infer that A R M write-combining is opportunistic coalescing, not an architectural sixty-four-byte atomic write. Any split or oversized T L P is silently ignored by the dedicated queue, stalling in-order reclaim.
   - source: FACTS §1 [INF]
5. `  60.3s` Switching to **DPI DMA (06:00.2)** hardware-atomic 64 B Posted `MWr` to the same portal yields **0 / 24,900** `W = 1` drops and **> 182M** zero-error ops!
   - spoken: Switching to the card's D P I D M A engine for hardware-atomic sixty-four-byte posted writes to the exact same portal yields zero drops out of twenty-four thousand nine hundred single-op trials and over one hundred eighty-two million pipelined ops with zero errors.
   - source: FACTS §1
6. `  77.6s` Hot LLC (`<= 768 KiB`, `CC = 1`) reaches **14.23 M/s** `NOP` and **29.53 GB/s** `4 KiB` copy—descriptors are only `0.46 GB/s`, yet polling lifts PCIe traffic to `6–7x`.
   - spoken: With a hot L L C working set under seven hundred sixty-eight kilobytes, pure D P I reaches fourteen point two three million N O P s per second and twenty-nine point five three gigabytes per second of four-kilobyte copies. Descriptors are just zero point four six gigabytes per second, yet read-back polling pushes total P C I e traffic up six to seven times.
   - source: FACTS §1

### S04 Breakthrough 2: Reverse P2P to Card BAR4 (104 s)

1. `   0.6s` Breaking down `W = 1 NOP (1.98 µs)` reveals a huge floor: because completions sit in host RAM, each 32 B DPI sync read-back costs **1.21 µs** alone!
   - spoken: Breaking down single-op latency reveals a glaring floor. Because completions live in host memory, every thirty-two-byte synchronous D P I read-back costs one point two one microseconds all by itself.
   - source: FACTS §1, §2
2. `  14.5s` That **1.21 µs** is **61%** of `W = 1 NOP`; any `4 KiB` copy missing Poll #1 waits a full extra round, causing **1,119 ns** jitter and a **3.13 µs** loop.
   - spoken: That one point two one microseconds is sixty-one percent of N O P latency. Worse, any four-kilobyte copy that barely misses the first poll waits a whole extra round, creating one thousand one hundred nineteen nanoseconds of quantization jitter and a three point one three microsecond loop.
   - source: FACTS §1, §2
3. `  33.6s` Single-variable change: keep pure DPI submit, write-only portal, and `CC = 0` fixed—only move `completion_addr` from Host Memzone to **Card PF BAR4 slot 15**.
   - spoken: To eliminate read-back cleanly, we change just one variable: keeping pure D P I submit, write-only portal, and C C zero fixed, we point the completion address at card P F B A R four slot fifteen.
   - source: FACTS §2
4. `  47.5s` Under a safety lease, the sleeping host `mmap`s `BAR4` slot 15; `io_remap_pfn_range` pre-populates all **1,024** `VM_PFNMAP` PTEs (`HPA 0x223ff3c00000`).
   - spoken: Protected by a safety lease handshake, the sleeping host process memory-maps B A R four slot fifteen, and the kernel synchronously pre-populates all one thousand twenty-four four-kilobyte page table entries.
   - source: FACTS §2
5. `  60.9s` `dsa2` hits `dmar6` with zero page faults and pushes 32 B completions via **reverse PCIe P2P** straight into card **L3 (TAD)**; the core polls locally via `LDARB` (`~1 ns` clear).
   - spoken: D S A two hits d m a r six with zero page faults and pushes thirty-two-byte completions via reverse P C I e P 2 P straight into card L 3 cache. The card core polls locally with L D A R B and clears in about one nanosecond, eliminating D P I read-backs completely.
   - source: FACTS §2
6. `  81.7s` All **102** runs zero-error: `W = 1` loops drop to **1.27 µs** (`NOP`, `-35.1%`) and **2.17 µs** (`4 KiB`, `-30.6%`), jitter falls to **163 ns**, and `W = 96 NOP` hits **23.01 M/s**!
   - spoken: Across one hundred two zero-error runs, single-in-flight loops drop to one point two seven microseconds for N O P and two point one seven microseconds for four-kilobyte copies, tail jitter plunges to one hundred sixty-three nanoseconds, and high-concurrency N O P surges to twenty-three point zero one million ops per second.
   - source: FACTS §2

### S05 Surprise: Why High-W 4 KiB Halved (77 s)

1. `   0.6s` Yet high-concurrency `4 KiB` copies hit a sudden cliff: for `W >= 32`, reverse-P2P stalls at **3.74 M/s (15.3 GB/s)**—half of Host Memzone's **30.0 GB/s**!
   - spoken: Just when victory seemed complete, high-concurrency four-kilobyte copies hit a sudden cliff: at concurrency thirty-two and above, reverse P 2 P stalls at three point seven four million ops per second, or fifteen point three gigabytes per second, half the host-completion baseline.
   - source: FACTS §2
2. `  17.6s` Rule out link limits: the exact same bidirectional P2P path drives `NOP` to **23.01 M/s** (`43.5 ns/op`)—**6.1x** faster than `3.74 M/s`.
   - spoken: First, we rule out link saturation: the exact same bidirectional P 2 P path already runs N O P at twenty-three point zero one million ops per second, or forty-three point five nanoseconds per op, over six times faster than the stall.
   - source: FACTS §2
3. `  33.2s` Rule out card doorbells: raising `min_batch = 1 -> 8 -> 16` at `W = 64` cuts doorbells **16x** (`3.54M -> 0.22M`), yet rate stays flat at **3.73 M/s (15.28 GB/s)**.
   - spoken: Next, we rule out card-side doorbells: raising minimum submit batching from one to eight to sixteen cuts doorbell writes sixteen-fold, yet throughput stays flat at three point seven three million ops per second.
   - source: FACTS §2
4. `  46.3s` Rule out engine slowdown: single-op marginal slope across `4 KiB -> 64 KiB -> 1 MiB` on `BAR4` remains **30.8–31.9 GB/s** (`133 -> 128 ns / 4 KiB`).
   - spoken: Finally, we check the copy engine itself: across single-op sizes from four kilobytes to one megabyte, marginal copy bandwidth stays at thirty point eight to thirty-one point nine gigabytes per second.
   - source: FACTS §2
5. `  58.5s` Subtraction nails it: `267.5 - 136.6 = 130.9 ns/op`—we infer a per-copy **~131 ns drain bubble** waiting for `dst` writes before each `BAR4` completion!
   - spoken: Subtracting the one hundred thirty-six point six nanosecond copy baseline from two hundred sixty-seven point five leaves one hundred thirty-one nanoseconds per op. We infer a per-copy drain bubble waiting for host writes to drain before issuing each reverse P 2 P completion.
   - source: FACTS §2 [INF]

### S06 The Fix: DSA Batch Full BW + Tight Tail (122 s)

1. `   0.6s` If per-op `BAR4` completions cause the `131 ns` bubble, the fix is native **DSA Batch (opcode = 0x01)**: let `B` copies share **one** reverse-P2P completion!
   - spoken: Since per-copy B A R four completions cause the one hundred thirty-one nanosecond bubble, the fix is native Intel D S A Batch: aggregate B copies so they share a single reverse P 2 P completion write.
   - source: FACTS §3
2. `  14.3s` Single-variable sweep: hold `W = 64` total in-flight `4 KiB` copies, `CC = 0`, single engine, and `BAR4` fixed—vary only **B = 1 -> 2 -> 4 -> 8 -> 16 -> 32** (`RCR = 0`).
   - spoken: Holding total in-flight copies fixed at sixty-four with C C zero on one engine, we sweep only batch size B from one to thirty-two, turning off sub-descriptor completions so only the top-level Batch writes thirty-two bytes to B A R four.
   - source: FACTS §3
3. `  30.3s` Throughput surges: **15.28** (`B=1`) -> **23.35** (`B=2`) -> **31.13** (`B=4`) -> **31.32 GB/s** (`B=16`)—a **2.05x** jump down to `130.8 ns / 4 KiB`!
   - spoken: Throughput jumps immediately: from fifteen point two eight at B equals one, to twenty-three point three five at B equals two, then thirty-one point one three at B equals four, peaking at thirty-one point three two gigabytes per second at B equals sixteen—a two point zero five times gain.
   - source: FACTS §3
4. `  48.6s` We infer two-stage pipelining `max(τ_comp, B·τ_copy)`: non-overlap residual drops to **+3.2 ns** at `B = 4` (`0.0 ns` at `B = 16`); `B = 32` (`W/B = 2`) dips to **30.57 GB/s**.
   - spoken: We infer a two-stage parallel pipeline where data copying overlaps reverse completion draining: non-overlap overhead drops to just three point two nanoseconds per batch by B equals four, beating naive serial amortization. At B equals thirty-two, only two batches remain in flight, dipping slightly to thirty point five seven.
   - source: FACTS §3 [INF]
5. `  70.2s` Tight tails stay intact: `p99 - p50` is **133–220 ns** across all `B` (despite a **+329 ns** Batch fetch step); sweet spot **B = 4** hits **2.84 µs** + **99.4%** peak BW!
   - spoken: Tight latency tails stay intact across every batch size, with p ninety-nine minus p fifty locked between one hundred thirty-three and two hundred twenty nanoseconds, even accounting for a three hundred twenty-nine nanosecond indirect descriptor fetch step. B equals four is the sweet spot: two point eight four microseconds for ninety-nine point four percent of peak bandwidth.
   - source: FACTS §3
6. `  92.9s` At **B = 16**, `BAR4` beats Host Memzone on throughput (**31.32 vs 31.10 GB/s**), tail (**4.61 vs 5.75 µs**), and symmetric loop (**4.53 vs 5.74 µs**; `516 KiB` hot LLC `src` + DDR5 `dst`).
   - spoken: At B equals sixteen, card B A R four wins on throughput, thirty-one point three two versus thirty-one point one zero gigabytes per second, on p ninety-nine latency, four point six one versus five point seven five microseconds, and on symmetric full-loop mean, four point five three versus five point seven four microseconds. Note that our five hundred sixteen kilobyte active set hits hot L L C on reads while writing through to D D R five.
   - source: FACTS §3

### S07 Summary & Engineering Guardrails (87 s)

1. `   0.6s` **Stage 1 (Direct Submit)**: two-layer `vmem` (`dmar0` + `dmar6` SVA) + DPI 64 B atomic write reaches **29.53 GB/s** with zero host forwarding, but leaves a `1.21 µs` read-back floor.
   - spoken: Recapping our three single-variable stages: stage one wired two-layer virtual mapping with D P I sixty-four-byte atomic writes, hitting twenty-nine point five three gigabytes per second with zero host forwarding, but leaving a one point two one microsecond read-back floor.
   - source: FACTS §1
2. `  18.2s` **Stage 2 (Reverse P2P)**: 32 B completions straight to card `BAR4` slot 15 cut `W = 1` loops to **1.27 / 2.17 µs** (jitter **163 ns**), while exposing the `131 ns` drain bubble.
   - spoken: Stage two added reverse P 2 P completions into card B A R four slot fifteen, cutting single-op loops to one point two seven and two point one seven microseconds with one hundred sixty-three nanoseconds of jitter, while exposing the one hundred thirty-one nanosecond drain bubble.
   - source: FACTS §2
3. `  36.2s` **Stage 3 (DSA Batch B = 4 / 16)**: sharing one reverse completion erases the bubble, lifting throughput to **31.32 GB/s** with **full bandwidth + tight tails**.
   - spoken: Stage three shared one reverse completion across a D S A Batch, erasing the bubble and pushing single-engine throughput to thirty-one point three two gigabytes per second—delivering both full bandwidth and tight latency tails.
   - source: FACTS §3
4. `  51.3s` Three guardrails: map the portal **FLAG_WRITE only**, ban `pem_wc` (pure **DPI atomic**), and isolate SVA `PASID` in a **minimal sleeping process** with a `BAR4` lease.
   - spoken: Three guardrails for production: map the portal write-only, ban card write-combining in favor of pure D P I atomic writes, and isolate the S V A P A S I D inside a minimal sleeping process guarded by a B A R four lease.
   - source: FACTS §4
5. `  68.2s` Next: benchmark **> 128 MiB cold DRAM** rings and multi-engine scaling—our zero-host-forwarding DPU <-> Xeon DSA bidirectional P2P loop is live!
   - spoken: Next, we will measure cold D R A M working sets larger than one hundred twenty-eight megabytes and scale across multiple engines. Our zero-host-forwarding D P U to Xeon D S A bidirectional P 2 P loop is now end-to-end proven. Thanks for watching!
   - source: FACTS §4
