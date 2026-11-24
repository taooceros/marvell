# CC-NIC Does Not Need Cache Coherence — Narration Script (`CcnicPaperStory`)

Total: **8 scenes**, **50 cues**, **12.23 min** (733.7 s).

## Build

```bash
python3 tts/ccnic_gen_scenes.py --script
bun tts/ccnic_manifest.ts
~/tts/asr/bin/python tts/ccnic_synth.py --jobs 4
python3 tts/ccnic_gen_scenes.py --script
./render_ccnic.sh
```

## Scenes

| ID | Title | Cues | Duration | Source |
| --- | --- | ---: | ---: | --- |
| `C01` | Intro | 6 | 83.0 s | — |
| `C02` | 1 · Why Plain PCIe Is Fast | 6 | 82.7 s | Xeon Host → PCIe Gen5 ×4 → Marvell CN106 DPU (HW IOMMU on) |
| `C03` | 2 · Zero Coherence Tax on TX | 6 | 80.4 s | Host TX Retention Pool: 64 KiB → 128 MiB vs 60 MiB Host LLC |
| `C04` | 3 · Taming Tail Latency | 6 | 81.1 s | High-Concurrency Open-Loop Traffic · Host In-Flight Credit Gate |
| `C05` | 4 · DirectPush: Hybrid + Fair CPU | 6 | 111.0 s | Small Direct Push + Native NIC DMA · Fair Zero-Copy SDP Baseline |
| `C06` | 5 · Real 100GbE & 1-Core Line Rate | 7 | 113.4 s | Two-Host 100GbE: saturn ⇄ CN106 ⇄ 3 m DAC ⇄ mercury (ConnectX-5) |
| `C07` | 6 · Active-NIC Burst Buffering | 7 | 100.3 s | 100 M/s RX Bursts (64 KiB → 128 MiB) · 256 KiB Hot Host Ring |
| `C08` | 7 · Paper Contributions & Recipe | 6 | 81.8 s | Synthesis: Cache-Aware PCIe + Native DMA + Credits + Active NIC |

---

## C01: Intro (83.0 s)

- **[0.60–15.40s]** (`CC-NIC ASPLOS'24 / MRV-113`) **Why revisit CC-NIC's premise?** ASPLOS '24 argued PCIe MMIO is too slow, so future NICs must move to **cache-coherent CXL**.
  - *Spoken*: Why revisit C C NIC's premise? At A S P L O S twenty twenty four, C C NIC made a bold claim: ordinary P C I express M M I O is too slow, so future network cards must move to cache-coherent C X L.
- **[15.75–29.39s]** (`MRV-113 / MRV-120`) Our thesis is the opposite: **fast NICs need zero hardware cache coherence**—and **zero** special CPU accelerators like Intel DSA.
  - *Spoken*: Our paper argues the opposite. Fast network cards need zero hardware cache coherence, and zero special C P U accelerators like Intel D S A—plain commercial P C I express is already enough when designed properly.
- **[29.74–45.10s]** (`MRV-113 / MRV-152`) Real off-the-shelf testbed: Xeon host **saturn**, **PCIe Gen5 ×4** Marvell **CN106** DPU with full **IOMMU**, and **100GbE** to **mercury**.
  - *Spoken*: Instead of emulating a NIC on a second C P U socket, we measure real commercial silicon: Xeon host saturn, a P C I express gen five by four Marvell C N one oh six with hardware I O M M U on, and hundred gigabit Ethernet to mercury.
- **[45.45–59.15s]** (`MRV-120 / MRV-155 / MRV-158`) **3,500+ zero-error runs** (**>2.5 B** frames): **0.9 µs** RTT, **1-core 100GbE**, **0%** TX coherence tax, **−98.9%** burst DRAM.
  - *Spoken*: Across thirty-five hundred zero-error runs and two point five billion frames: sub-microsecond latency, one-core hundred gigabit line rate, zero transmit coherence tax, and ninety-eight point nine percent less burst D RAM.
- **[59.50–72.04s]** (`MRV-120`) Four principles drive the gain: **cache-aware PCIe push**, **native NIC DMA**, **credit pacing**, and an **active NIC** for bursts.
  - *Spoken*: Four simple principles drive everything: cache-aware P C I express push, native NIC D M A for large payloads, lightweight credit pacing, and an active NIC that shields host caches during receive bursts.
- **[72.39–81.63s]** (`MRV-113 / MRV-120`) Next, we follow **six natural advisor questions**—each measurement directly motivating the next piece of the design.
  - *Spoken*: Next, we walk through six natural questions a systems advisor would ask step by step, letting each hardware measurement directly motivate the next piece of the design.
## C02: 1 · Why Plain PCIe Is Fast (82.7 s)

*Source: `Xeon Host → PCIe Gen5 ×4 → Marvell CN106 DPU (HW IOMMU on)`*

- **[0.60–10.98s]** (`MRV-148 / MRV-89`) **Question 1**: Wait—isn't ordinary **PCIe MMIO** famously slow and forced through **DRAM** without cache coherence?
  - *Spoken*: Question one: wait—isn't ordinary P C I express M M I O famously slow, and without hardware cache coherence, doesn't every message have to crawl through slow D RAM?
- **[11.33–22.71s]** (`MRV-89 / CC-NIC Fig. 2`) Only if you fence every write. Fencing every 64 B write stalls at **8.10 M/s**—reproducing CC-NIC's slow baseline.
  - *Spoken*: Only if you stall after every write. Issuing a memory fence after every sixty-four byte write stalls one core at eight point one zero million per second—reproducing C C NIC's slow baseline.
- **[23.06–35.36s]** (`MRV-89`) Relax the fence interval and host write queues pipeline in parallel: **1 core** hits **175.2 M/s** (**21.6×**)—**97.9%** of Gen5 ×4 line rate!
  - *Spoken*: Relax the fence interval and host queues pipeline writes in parallel. One host core jumps twenty-one point six times to one hundred seventy-five point two million per second—ninety-seven point nine percent of line rate.
- **[35.71–48.37s]** (`MRV-97`) What about latency? Cold-flushing each hop proves **all three cache shortcuts** already work on commercial PCIe—staying in caches.
  - *Spoken*: That settles raw throughput—now what about round-trip latency? Cold-flushing each hop one at a time proves all three cache shortcuts already work by default on commercial P C I express, keeping the fast path in caches.
- **[48.72–66.10s]** (`MRV-97`) Host **L1** saves **117 ns**; writes hit **card L3** (**99.94%**, **−75 ns**); replies hit **host LLC via DDIO** (**−87 ns**) → **897 ns** RTT.
  - *Spoken*: Host L one saves one hundred seventeen nanoseconds. Writes hit card L three at ninety-nine point nine four percent, saving seventy-five nanoseconds. Replies hit host L L C via Intel D D I O, saving eighty-seven—giving an eight hundred ninety-seven nanosecond round trip.
- **[66.45–81.25s]** (`MRV-148 / MRV-97`) Even with **full IOMMU**, **897 ns** beats stock **SDP** (**2,400 ns**) and real **CXL-NIC** hardware (**1.3–2.1 µs**, no IOMMU).
  - *Spoken*: Even with full hardware I O M M U turned on, eight hundred ninety-seven nanoseconds beats the card's default queue interface nearly three times over, and beats real C X L network cards at one point three to two point one microseconds without I O M M U.
## C03: 2 · Zero Coherence Tax on TX (80.4 s)

*Source: `Host TX Retention Pool: 64 KiB → 128 MiB vs 60 MiB Host LLC`*

- **[0.60–10.08s]** (`MRV-149`) **Question 2**: Push is fast—but what happens when **TCP** must retain sent buffers in host memory until remote ACKs arrive?
  - *Spoken*: Question two: push is fast, but when T C P must retain sent buffers in host memory until remote acks arrive, doesn't a large transmit pool kill performance?
- **[10.43–21.83s]** (`MRV-149`) Separate **two lifetimes**: the **host-to-NIC PCIe hop** is lossless, while **end-to-end TCP retention** lives purely on the host.
  - *Spoken*: The key insight is separating two lifetimes that prior work mixed together. The host to card P C I express hop is lossless, while end-to-end retransmission buffering belongs strictly to the host.
- **[22.18–35.94s]** (`MRV-149 / MRV-116`) Direct push copies **64 B by value** into NIC memory—the NIC **never reads host TX RAM**. Reusing **one 64 B buffer** hits **174.8 M/s**.
  - *Spoken*: Direct push copies sixty-four bytes by value into the NIC, so the NIC never reads host transmit memory back. Overwriting one single sixty-four byte buffer hits one hundred seventy-four point eight million per second with zero pool.
- **[36.29–47.93s]** (`MRV-149 / CC-NIC Fig. 15`) On coherent **CC-NIC**, host TCP retention triggers **cross-link invalidations**: **−20%** on retention alone (up to **−73%** with host pools).
  - *Spoken*: On coherent C C NIC, keeping T C P buffers in host memory forces cross-link cache invalidations—costing twenty percent on retention alone, and up to seventy-three percent with host pools.
- **[48.28–62.84s]** (`MRV-149 / MRV-116`) Without coherence, NIC caches **never mirror** host TX RAM: growing the pool **2,000×** (**64 KiB → 128 MiB**) holds **47.5 → 47.1 M/s** (**−0.9%**).
  - *Spoken*: Without coherence, NIC caches never mirror host transmit memory. Growing a sequential pool two thousand times—from sixty-four kilobytes to one hundred twenty-eight megabytes, twice the host cache—keeps throughput flat within zero point nine percent.
- **[63.19–78.97s]** (`MRV-149 / MRV-116 / MRV-125`) Across **816 M** replies, receive **DDIO** stays **99.99993%** (**0** misses at 128 MiB). Random slowdown is purely local DDR5—**0% coherence tax**.
  - *Spoken*: Across eight hundred sixteen million replies, receive D D I O hits stay at ninety-nine point nine nine nine nine percent with zero misses. Any random-access slowdown comes purely from local host D D R five, with zero cross-link coherence tax.
## C04: 3 · Taming Tail Latency (81.1 s)

*Source: `High-Concurrency Open-Loop Traffic · Host In-Flight Credit Gate`*

- **[0.60–12.86s]** (`MRV-150`) **Question 3**: Idle RTT is **0.9 µs**—but **what breaks at high concurrency** on a real DPU, and how do we tame tail latency?
  - *Spoken*: Question three: idle latency is under a microsecond and raw push hits one hundred seventy-five million per second. So what breaks when we crank up high concurrency on a real D P U, and how do we tame tail latency?
- **[13.21–27.09s]** (`MRV-148 / MRV-150`) Real DPU silicon exposed a **hardware contention cliff**: relentless empty NIC polling collides with concurrent inbound writes.
  - *Spoken*: Real D P U silicon exposed a hardware cliff invisible to two-socket emulators. When card cores relentlessly poll empty slots during heavy inbound writes, the card's inbound bridge serializes writes down to roughly thirteen million per second.
- **[27.44–38.50s]** (`MRV-150 / MRV-115`) **~13 M/s** is **not** the link ceiling (push hits **175 M/s**, 1-core RPC **50.4 M/s**)—it is congestion collapse when bursts pile up.
  - *Spoken*: Crucially, thirteen million is not the channel cap—raw push hits one hundred seventy-five million and one-core R P C hits fifty million. It is congestion collapse when unpaced bursts queue up.
- **[38.85–50.95s]** (`MRV-150 / MRV-115`) Without pacing at **16–30 M/s**, host queues back up (**48–248** entries), exploding open-loop **p99** tail latency to **44–51 µs**.
  - *Spoken*: Without pacing at sixteen to thirty million per second, backpressure fills forty-eight to two hundred forty-eight host queue entries, exploding p ninety-nine tail latency to forty-four to fifty-one microseconds.
- **[51.30–65.80s]** (`MRV-150 / MRV-115 / MRV-123`) A **host credit cap** (`32–48` in-flight) clears queues (**1–3** entries), cuts **p99 6–64×** (**44.5 → 7.0 µs** Poisson), and lifts 4C **+43%**.
  - *Spoken*: A simple host credit gate capping thirty-two to forty-eight requests in flight drains queues to one to three entries, cuts tail latency six to sixty-four times—down to seven microseconds under Poisson load—and lifts four-core throughput forty-three percent.
- **[66.15–79.73s]** (`MRV-150 / MRV-123`) **Honest boundary**: host-only credits hold **≤ 2 µs** up to **13.5 M/s** (1C) / **10 M/s** (4C); faster sub-2 µs needs **on-demand card backoff**.
  - *Spoken*: Honest boundary: host-only credits hold under two microseconds up to thirteen point five million on one core or ten million on four. Going faster at sub-two microseconds requires on-demand card backoff after busy bursts.
## C05: 4 · DirectPush: Hybrid + Fair CPU (111.0 s)

*Source: `Small Direct Push + Native NIC DMA · Fair Zero-Copy SDP Baseline`*

- **[0.60–10.66s]** (`MRV-151`) **Question 4**: Direct push shines at **64 B**—but pushing **4 KiB** wastes host CPU. How do we handle **mixed packet sizes** cleanly?
  - *Spoken*: Question four: direct push shines for sixty-four byte control messages, but pushing four kilobytes line by line wastes host C P U. How do we handle real mixed traffic?
- **[11.01–28.31s]** (`MRV-151 / MRV-120 / MRV-22`) **DirectPush** splits by size (**zero DSA**): **≤ 256 B** pushes inline (**0.89 µs**); **≥ 512 B** pushes one **64 B** command for **native NIC DMA**.
  - *Spoken*: DirectPush splits cleanly by message size with zero Intel D S A. Messages up to two hundred fifty-six bytes go inline in zero point eight nine microseconds. Larger payloads push a single sixty-four byte command for zero-copy built-in D M A at ninety-seven percent of line rate.
- **[28.66–45.30s]** (`MRV-151 / MRV-117`) Wider DMA packets amortize PCIe headers: **4 KiB** host CPU drops **2.86×** (**46.6% → 16.3%**), lifting `Mix-Geo` **2.41×** (**7.8 → 18.7 GB/s**).
  - *Spoken*: Wider D M A packets amortize P C I express headers. On four kilobytes, host C P U drops two point eight six times—from forty-six point six to sixteen point three percent—lifting large-heavy Mix-Geo from seven point eight to eighteen point seven gigabytes per second.
- **[45.65–63.09s]** (`MRV-151 / MRV-127`) Dual **small/large credit windows** stop head-of-line blocking: under saturated **18.6 GB/s** 4 KiB load, 64 B **p99** drops **40.5 ms → 2.75 µs**.
  - *Spoken*: Giving small and large traffic two separate credit windows eliminates head-of-line blocking. Under saturated eighteen point six gigabyte per second bulk traffic, sixty-four byte tail latency drops from forty point five milliseconds to two point seven five microseconds—over fourteen thousand times lower.
- **[63.44–89.14s]** (`MRV-166`) Against a **fair zero-copy SDP** baseline (non-blocking, **100% DDIO**), DirectPush still saves **1.5–2.1×** host CPU and **5.24×** 64 B RTT.
  - *Spoken*: Now for the fairness check every systems advisor asks first: we aggressively tuned the card's stock S D P driver with zero-copy, non-blocking completions, and full D D I O. Even against that fair zero-copy S D P baseline, DirectPush still cuts host C P U by one point five to two point one times across sixty-four bytes to four kilobytes, while delivering five point two four times lower sixty-four byte latency.
- **[89.49–109.55s]** (`MRV-166`) **Honest boundary**: **1–4 KiB** savings mostly avoid DPDK stack overhead; at **64 B**, eliminating TX DMA reads is a **true hardware win**.
  - *Spoken*: One plain-English boundary keeps our claims completely honest. At one to four kilobytes, a modern static-ring network card has an isomorphic P C I express transfer, so our C P U win mainly reflects skipping D P D K software stack overhead. At sixty-four bytes, however, eliminating transmit D M A reads is a genuine hardware win.
## C06: 5 · Real 100GbE & 1-Core Line Rate (113.4 s)

*Source: `Two-Host 100GbE: saturn ⇄ CN106 ⇄ 3 m DAC ⇄ mercury (ConnectX-5)`*

- **[0.60–9.50s]** (`MRV-152 / MRV-133 / MRV-155`) **Question 5**: Do these gains survive a **real 100GbE cable**—and can **one single host core** actually saturate **100 Gbps** end-to-end?
  - *Spoken*: Question five: do these gains still matter over a real hundred gigabit Ethernet cable, and can strictly one host core saturate hundred gigabit line rate?
- **[9.85–26.45s]** (`MRV-152 / MRV-133`) Over **saturn → CN106 → 3 m DAC → mercury (ConnectX-5)**: end-to-end **64 B** RTT drops **10.21 → 5.96 µs** (**−4.25 µs / −41.6%**).
  - *Spoken*: Connecting saturn through C N one oh six and a three-meter hundred gigabit cable to mercury's ConnectX-five, real wire latency never hides the gain. Sixty-four byte round trip drops from ten point two one to five point nine six microseconds—saving forty-one point six percent.
- **[26.80–42.28s]** (`MRV-152 / MRV-133`) Every size wins on wire: **256 B** saves **3.76 µs** (**−36%**), **4 KiB** saves **2.33 µs**, and `Mix-Geo` 64 B **p99** drops **68%** (**96.5 → 30.7 µs**).
  - *Spoken*: Every packet size wins over the wire: two hundred fifty-six bytes saves three point seven six microseconds, four kilobytes saves two point three three, and mixed sixty-four byte tail latency drops sixty-eight percent to thirty point seven microseconds.
- **[42.63–57.11s]** (`MRV-154`) Polling cheap local host rings ahead of costly Ethernet status checks trims another **−370 ns** end-to-end (**6.03 → 5.66 µs**, **0.94 µs** on-card).
  - *Spoken*: One clean card-side scheduling rule—polling cheap local host rings before expensive Ethernet port status checks—trims another three hundred seventy nanoseconds directly off end-to-end latency, restoring zero point nine four microseconds on-card.
- **[57.46–74.16s]** (`MRV-155`) **1-core 100GbE** test: into the NIC, **1 host core** pushes **161.4 Mpps**—above 100GbE's 64 B cap (**148.8 Mpps**)—vs optimal SDP's **50.5 Mpps**.
  - *Spoken*: Next, can one single host core drive hundred gigabit line rate? Across P C I express, one DirectPush core sustains one hundred sixty-one point four million packets per second—above hundred gigabit's sixty-four byte limit—while optimal S D P stalls at fifty point five million.
- **[74.51–92.23s]** (`MRV-155`) End-to-end into `mercury` PHY on **1 host core**: **64 B UDP** hits **99.76 Gbps**, **64 B RoCEv2** **99.68 Gbps** (**1.96×** SDP), **256 B** **99.86 Gbps**!
  - *Spoken*: From end to end into mercury's physical port on one host core, sixty-four byte U D P hits ninety-nine point seven six gigabits, sixty-four byte Rocky v two hits ninety-nine point six eight—nearly twice optimal S D P—and two hundred fifty-six bytes hits ninety-nine point eight six gigabits.
- **[92.58–112.00s]** (`MRV-155`) **Honest ceilings**: bare 64 B caps at **99.2 Mpps** (`66.7 Gbps`) on CN106's default single TX scheduler; **1–4 KiB** hits **94.0–96.5 Gbps**.
  - *Spoken*: Two honest hardware ceilings: bare sixty-four byte frames cap at ninety-nine point two million packets per second on this card's default single scheduler—hitting full hundred gigabits from one hundred six byte frames up—while one to four kilobyte D M A reaches ninety-four to ninety-six point five gigabits at the P C I express ceiling.
## C07: 6 · Active-NIC Burst Buffering (100.3 s)

*Source: `100 M/s RX Bursts (64 KiB → 128 MiB) · 256 KiB Hot Host Ring`*

- **[0.60–9.50s]** (`MRV-158`) **Question 6**: What about the **receive side** during **100 M/s** traffic bursts—won't bursting into host rings thrash **host LLC** and **DRAM**?
  - *Spoken*: Question six: what about the receive side when a hundred million packet per second burst hits? Won't pouring bursts into host rings thrash host caches and D RAM?
- **[9.85–21.11s]** (`MRV-158`) **Passive NIC (Scheme A)** dumps bursts into a **128 MiB** host ring—blowing past the **8 MiB** DDIO window and **60 MiB** host LLC.
  - *Spoken*: A passive NIC dumps incoming bursts straight into a one hundred twenty-eight megabyte host ring, overflowing the eight megabyte D D I O window and the entire sixty megabyte host cache.
- **[21.46–33.92s]** (`MRV-158`) **Active NIC (Scheme B)** holds burst backlog on the **NIC**; the host keeps only a **256 KiB hot ring**, refilled in place as credits return.
  - *Spoken*: Our active NIC flips that around. Backlog stays buffered on the NIC itself, while the host keeps only a tiny two hundred fifty-six kilobyte hot ring—permanently cache-resident—refilled in place as credits return.
- **[34.27–44.71s]** (`MRV-158`) Zero-backlog overhead is practically zero: **+6 ns** on idle RTT (**0.887 → 0.893 µs**), zero concurrency loss, **176.9 M/s** streaming.
  - *Spoken*: With no burst, credit checks cost just six nanoseconds on idle latency, zero loss under concurrency, and one hundred seventy-six point nine million per second streaming.
- **[45.06–61.22s]** (`MRV-158`) At **128 MiB** bursts: host LLC misses drop **91.4% → 0.0%**, DDIO climbs **16.3% → 100%**, and **host DRAM drops −98.9%** (**972 → 11 MiB**).
  - *Spoken*: Under one hundred twenty-eight megabyte bursts, host L L C read misses plunge from ninety-one point four percent to zero, D D I O stays at one hundred percent, and host D RAM traffic drops ninety-eight point nine percent—from nine hundred seventy-two down to eleven megabytes.
- **[61.57–81.47s]** (`MRV-158`) **Conservation law**: bursts **≤ 32 MiB** sit inside CN106's **48 MiB L3** (~0 system DRAM); **> 48 MiB** cleanly offloads to **card DDR5**.
  - *Spoken*: Dual-side counters reveal clean conservation: bursts up to thirty-two megabytes fit in the card's forty-eight megabyte L three cache, keeping system D RAM traffic near zero. Above forty-eight megabytes, the NIC's dedicated six-channel D D R five absorbs the spill, keeping host memory traffic minimal at about eleven megabytes.
- **[81.82–98.94s]** (`MRV-158`) Refill stays **0.91 µs** (`1.05–1.16 µs` cold) and 128 MiB drain wins **+8.4%**. **Trade-off**: 1 card core drains `1–32 MiB` bursts slightly slower.
  - *Spoken*: Refill stays sub-microsecond—zero point nine one microseconds sequentially, up to one point one six cold—while one hundred twenty-eight megabyte drain wins eight point four percent. Honestly, one card core drains mid-size bursts slightly slower before winning past forty-eight megabytes.
## C08: 7 · Paper Contributions & Recipe (81.8 s)

*Source: `Synthesis: Cache-Aware PCIe + Native DMA + Credits + Active NIC`*

- **[0.60–10.44s]** (`MRV-113 / MRV-120`) **So what are the paper's contributions?** Every design step followed directly from testing **CC-NIC's assumption** on real silicon.
  - *Spoken*: So what are the paper's core contributions? Stepping back, every design step followed directly from testing C C NIC's central assumption on real commercial hardware.
- **[10.79–24.65s]** (`MRV-148 / MRV-149`) **1 · Plain PCIe is fast**: unfenced push hits **175.2 M/s** (**97.9%** line rate), **897 ns** 3-hop cache RTT (with **IOMMU**), and **0%** TX coherence tax.
  - *Spoken*: Contribution one: plain P C I express is already fast—one hundred seventy-five million writes per second on one core, eight hundred ninety-seven nanosecond cache latency with full I O M M U, and zero transmit coherence tax.
- **[25.00–42.56s]** (`MRV-150 / MRV-151 / MRV-166`) **2 · DirectPush interface**: small push + **native NIC DMA** (**0 DSA**) + **dual credits** → **14,700×** HoL isolation & **1.5–2.1×** lower CPU vs fair SDP.
  - *Spoken*: Contribution two: DirectPush pairs small inline push with native NIC D M A, uses zero Intel D S A, and adds dual credit windows—delivering fourteen thousand seven hundred times head-of-line isolation and up to two point one times lower host C P U than fair tuned S D P.
- **[42.91–53.37s]** (`MRV-152 / MRV-133 / MRV-155`) **3 · Real 100GbE proof**: **−4.25 µs** (**−42%**) two-host RTT, and **1 single host core** saturating **99.8 Gbps** end-to-end into ConnectX-5.
  - *Spoken*: Contribution three: real hundred gigabit validation—cutting two-host latency forty-two percent and driving ninety-nine point eight gigabits per second from strictly one host core.
- **[53.72–67.38s]** (`MRV-158`) **4 · Active-NIC burst isolation**: holding bursts on the NIC behind a **256 KiB** hot host ring cuts host DRAM **−98.9%** at **100% DDIO**.
  - *Spoken*: Contribution four: active-NIC burst isolation—keeping receive bursts on the NIC behind a two hundred fifty-six kilobyte hot host ring cuts host D RAM traffic ninety-eight point nine percent while keeping host caches clean.
- **[67.73–80.43s]** (`MRV-113 / MRV-120`) **One-sentence takeaway**: fast NICs do **not** need cache coherence—they need **cache-aware PCIe**, **native DMA**, **credit pacing**, and an **active NIC**.
  - *Spoken*: Here is our one-sentence paper takeaway: high-speed network cards do not need hardware cache coherence. Cache-friendly P C I express, built-in D M A, credit pacing, and an active NIC already get us there today.
