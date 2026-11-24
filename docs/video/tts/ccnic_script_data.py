"""Single source of truth for the Advisor-Ready CcnicPaperStory English explainer video.

Every scene is a dict:
  id:     composition id (C01..C08, kept disjoint from PcieDatapath S01..S15)
  file:   src/ccnic/<file>.tsx
  title:  top-left title (<= 34 ASCII chars so it stays well left of the rail at x=760)
  stage:  index into CCNIC_STAGES (0..6), or None to hide the rail
  source: provenance line under title (<= 64 ASCII chars)
  cues:   list of (caption, spoken_or_None, fact_src)

Caption formatting supports ONLY **accent** and `code` (never single *italic*).
Captions stay <= 138 plain chars (2 crisp lines). Spoken overrides spell acronyms
with spaces and write numbers as words for clean edge-tts pronunciation.
"""

SCENES = [
    # ------------------------------------------------------------------ C01
    dict(
        id="C01",
        file="C01Thesis",
        title="",
        stage=None,
        source="",
        cues=[
            (
                "**Why revisit CC-NIC's premise?** ASPLOS '24 argued PCIe MMIO is too slow, so future NICs must move to **cache-coherent CXL**.",
                "Why revisit C C NIC's premise? At A S P L O S twenty twenty four, C C NIC made a bold claim: ordinary P C I express M M I O is too slow, so future network cards must move to cache-coherent C X L.",
                "CC-NIC ASPLOS'24 / MRV-113",
            ),
            (
                "Our thesis is the opposite: **fast NICs need zero hardware cache coherence**—and **zero** special CPU accelerators like Intel DSA.",
                "Our paper argues the opposite. Fast network cards need zero hardware cache coherence, and zero special C P U accelerators like Intel D S A—plain commercial P C I express is already enough when designed properly.",
                "MRV-113 / MRV-120",
            ),
            (
                "Real off-the-shelf testbed: Xeon host **saturn**, **PCIe Gen5 ×4** Marvell **CN106** DPU with full **IOMMU**, and **100GbE** to **mercury**.",
                "Instead of emulating a NIC on a second C P U socket, we measure real commercial silicon: Xeon host saturn, a P C I express gen five by four Marvell C N one oh six with hardware I O M M U on, and hundred gigabit Ethernet to mercury.",
                "MRV-113 / MRV-152",
            ),
            (
                "**3,500+ zero-error runs** (**>2.5 B** frames): **0.9 µs** RTT, **1-core 100GbE**, **0%** TX coherence tax, **−98.9%** burst DRAM.",
                "Across thirty-five hundred zero-error runs and two point five billion frames: sub-microsecond latency, one-core hundred gigabit line rate, zero transmit coherence tax, and ninety-eight point nine percent less burst D RAM.",
                "MRV-120 / MRV-155 / MRV-158",
            ),
            (
                "Four principles drive the gain: **cache-aware PCIe push**, **native NIC DMA**, **credit pacing**, and an **active NIC** for bursts.",
                "Four simple principles drive everything: cache-aware P C I express push, native NIC D M A for large payloads, lightweight credit pacing, and an active NIC that shields host caches during receive bursts.",
                "MRV-120",
            ),
            (
                "Next, we follow **six natural advisor questions**—each measurement directly motivating the next piece of the design.",
                "Next, we walk through six natural questions a systems advisor would ask step by step, letting each hardware measurement directly motivate the next piece of the design.",
                "MRV-113 / MRV-120",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C02
    dict(
        id="C02",
        file="C02PcieSpeed",
        title="1 · Why Plain PCIe Is Fast",
        stage=0,
        source="Xeon Host → PCIe Gen5 ×4 → Marvell CN106 DPU (HW IOMMU on)",
        cues=[
            (
                "**Question 1**: Wait—isn't ordinary **PCIe MMIO** famously slow and forced through **DRAM** without cache coherence?",
                "Question one: wait—isn't ordinary P C I express M M I O famously slow, and without hardware cache coherence, doesn't every message have to crawl through slow D RAM?",
                "MRV-148 / MRV-89",
            ),
            (
                "Only if you fence every write. Fencing every 64 B write stalls at **8.10 M/s**—reproducing CC-NIC's slow baseline.",
                "Only if you stall after every write. Issuing a memory fence after every sixty-four byte write stalls one core at eight point one zero million per second—reproducing C C NIC's slow baseline.",
                "MRV-89 / CC-NIC Fig. 2",
            ),
            (
                "Relax the fence interval and host write queues pipeline in parallel: **1 core** hits **175.2 M/s** (**21.6×**)—**97.9%** of Gen5 ×4 line rate!",
                "Relax the fence interval and host queues pipeline writes in parallel. One host core jumps twenty-one point six times to one hundred seventy-five point two million per second—ninety-seven point nine percent of line rate.",
                "MRV-89",
            ),
            (
                "What about latency? Cold-flushing each hop proves **all three cache shortcuts** already work on commercial PCIe—staying in caches.",
                "That settles raw throughput—now what about round-trip latency? Cold-flushing each hop one at a time proves all three cache shortcuts already work by default on commercial P C I express, keeping the fast path in caches.",
                "MRV-97",
            ),
            (
                "Host **L1** saves **117 ns**; writes hit **card L3** (**99.94%**, **−75 ns**); replies hit **host LLC via DDIO** (**−87 ns**) → **897 ns** RTT.",
                "Host L one saves one hundred seventeen nanoseconds. Writes hit card L three at ninety-nine point nine four percent, saving seventy-five nanoseconds. Replies hit host L L C via Intel D D I O, saving eighty-seven—giving an eight hundred ninety-seven nanosecond round trip.",
                "MRV-97",
            ),
            (
                "Even with **full IOMMU**, **897 ns** beats stock **SDP** (**2,400 ns**) and real **CXL-NIC** hardware (**1.3–2.1 µs**, no IOMMU).",
                "Even with full hardware I O M M U turned on, eight hundred ninety-seven nanoseconds beats the card's default queue interface nearly three times over, and beats real C X L network cards at one point three to two point one microseconds without I O M M U.",
                "MRV-148 / MRV-97",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C03
    dict(
        id="C03",
        file="C03TxBuffers",
        title="2 · Zero Coherence Tax on TX",
        stage=1,
        source="Host TX Retention Pool: 64 KiB → 128 MiB vs 60 MiB Host LLC",
        cues=[
            (
                "**Question 2**: Push is fast—but what happens when **TCP** must retain sent buffers in host memory until remote ACKs arrive?",
                "Question two: push is fast, but when T C P must retain sent buffers in host memory until remote acks arrive, doesn't a large transmit pool kill performance?",
                "MRV-149",
            ),
            (
                "Separate **two lifetimes**: the **host-to-NIC PCIe hop** is lossless, while **end-to-end TCP retention** lives purely on the host.",
                "The key insight is separating two lifetimes that prior work mixed together. The host to card P C I express hop is lossless, while end-to-end retransmission buffering belongs strictly to the host.",
                "MRV-149",
            ),
            (
                "Direct push copies **64 B by value** into NIC memory—the NIC **never reads host TX RAM**. Reusing **one 64 B buffer** hits **174.8 M/s**.",
                "Direct push copies sixty-four bytes by value into the NIC, so the NIC never reads host transmit memory back. Overwriting one single sixty-four byte buffer hits one hundred seventy-four point eight million per second with zero pool.",
                "MRV-149 / MRV-116",
            ),
            (
                "On coherent **CC-NIC**, host TCP retention triggers **cross-link invalidations**: **−20%** on retention alone (up to **−73%** with host pools).",
                "On coherent C C NIC, keeping T C P buffers in host memory forces cross-link cache invalidations—costing twenty percent on retention alone, and up to seventy-three percent with host pools.",
                "MRV-149 / CC-NIC Fig. 15",
            ),
            (
                "Without coherence, NIC caches **never mirror** host TX RAM: growing the pool **2,000×** (**64 KiB → 128 MiB**) holds **47.5 → 47.1 M/s** (**−0.9%**).",
                "Without coherence, NIC caches never mirror host transmit memory. Growing a sequential pool two thousand times—from sixty-four kilobytes to one hundred twenty-eight megabytes, twice the host cache—keeps throughput flat within zero point nine percent.",
                "MRV-149 / MRV-116",
            ),
            (
                "Across **816 M** replies, receive **DDIO** stays **99.99993%** (**0** misses at 128 MiB). Random slowdown is purely local DDR5—**0% coherence tax**.",
                "Across eight hundred sixteen million replies, receive D D I O hits stay at ninety-nine point nine nine nine nine percent with zero misses. Any random-access slowdown comes purely from local host D D R five, with zero cross-link coherence tax.",
                "MRV-149 / MRV-116 / MRV-125",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C04
    dict(
        id="C04",
        file="C04CreditPacing",
        title="3 · Taming Tail Latency",
        stage=2,
        source="High-Concurrency Open-Loop Traffic · Host In-Flight Credit Gate",
        cues=[
            (
                "**Question 3**: Idle RTT is **0.9 µs**—but **what breaks at high concurrency** on a real DPU, and how do we tame tail latency?",
                "Question three: idle latency is under a microsecond and raw push hits one hundred seventy-five million per second. So what breaks when we crank up high concurrency on a real D P U, and how do we tame tail latency?",
                "MRV-150",
            ),
            (
                "Real DPU silicon exposed a **hardware contention cliff**: relentless empty NIC polling collides with concurrent inbound writes.",
                "Real D P U silicon exposed a hardware cliff invisible to two-socket emulators. When card cores relentlessly poll empty slots during heavy inbound writes, the card's inbound bridge serializes writes down to roughly thirteen million per second.",
                "MRV-148 / MRV-150",
            ),
            (
                "**~13 M/s** is **not** the link ceiling (push hits **175 M/s**, 1-core RPC **50.4 M/s**)—it is congestion collapse when bursts pile up.",
                "Crucially, thirteen million is not the channel cap—raw push hits one hundred seventy-five million and one-core R P C hits fifty million. It is congestion collapse when unpaced bursts queue up.",
                "MRV-150 / MRV-115",
            ),
            (
                "Without pacing at **16–30 M/s**, host queues back up (**48–248** entries), exploding open-loop **p99** tail latency to **44–51 µs**.",
                "Without pacing at sixteen to thirty million per second, backpressure fills forty-eight to two hundred forty-eight host queue entries, exploding p ninety-nine tail latency to forty-four to fifty-one microseconds.",
                "MRV-150 / MRV-115",
            ),
            (
                "A **host credit cap** (`32–48` in-flight) clears queues (**1–3** entries), cuts **p99 6–64×** (**44.5 → 7.0 µs** Poisson), and lifts 4C **+43%**.",
                "A simple host credit gate capping thirty-two to forty-eight requests in flight drains queues to one to three entries, cuts tail latency six to sixty-four times—down to seven microseconds under Poisson load—and lifts four-core throughput forty-three percent.",
                "MRV-150 / MRV-115 / MRV-123",
            ),
            (
                "**Honest boundary**: host-only credits hold **≤ 2 µs** up to **13.5 M/s** (1C) / **10 M/s** (4C); faster sub-2 µs needs **on-demand card backoff**.",
                "Honest boundary: host-only credits hold under two microseconds up to thirteen point five million on one core or ten million on four. Going faster at sub-two microseconds requires on-demand card backoff after busy bursts.",
                "MRV-150 / MRV-123",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C05
    dict(
        id="C05",
        file="C05DirectPush",
        title="4 · DirectPush: Hybrid + Fair CPU",
        stage=3,
        source="Small Direct Push + Native NIC DMA · Fair Zero-Copy SDP Baseline",
        cues=[
            (
                "**Question 4**: Direct push shines at **64 B**—but pushing **4 KiB** wastes host CPU. How do we handle **mixed packet sizes** cleanly?",
                "Question four: direct push shines for sixty-four byte control messages, but pushing four kilobytes line by line wastes host C P U. How do we handle real mixed traffic?",
                "MRV-151",
            ),
            (
                "**DirectPush** splits by size (**zero DSA**): **≤ 256 B** pushes inline (**0.89 µs**); **≥ 512 B** pushes one **64 B** command for **native NIC DMA**.",
                "DirectPush splits cleanly by message size with zero Intel D S A. Messages up to two hundred fifty-six bytes go inline in zero point eight nine microseconds. Larger payloads push a single sixty-four byte command for zero-copy built-in D M A at ninety-seven percent of line rate.",
                "MRV-151 / MRV-120 / MRV-22",
            ),
            (
                "Wider DMA packets amortize PCIe headers: **4 KiB** host CPU drops **2.86×** (**46.6% → 16.3%**), lifting `Mix-Geo` **2.41×** (**7.8 → 18.7 GB/s**).",
                "Wider D M A packets amortize P C I express headers. On four kilobytes, host C P U drops two point eight six times—from forty-six point six to sixteen point three percent—lifting large-heavy Mix-Geo from seven point eight to eighteen point seven gigabytes per second.",
                "MRV-151 / MRV-117",
            ),
            (
                "Dual **small/large credit windows** stop head-of-line blocking: under saturated **18.6 GB/s** 4 KiB load, 64 B **p99** drops **40.5 ms → 2.75 µs**.",
                "Giving small and large traffic two separate credit windows eliminates head-of-line blocking. Under saturated eighteen point six gigabyte per second bulk traffic, sixty-four byte tail latency drops from forty point five milliseconds to two point seven five microseconds—over fourteen thousand times lower.",
                "MRV-151 / MRV-127",
            ),
            (
                "Against a **fair zero-copy SDP** baseline (non-blocking, **100% DDIO**), DirectPush still saves **1.5–2.1×** host CPU and **5.24×** 64 B RTT.",
                "Now for the fairness check every systems advisor asks first: we aggressively tuned the card's stock S D P driver with zero-copy, non-blocking completions, and full D D I O. Even against that fair zero-copy S D P baseline, DirectPush still cuts host C P U by one point five to two point one times across sixty-four bytes to four kilobytes, while delivering five point two four times lower sixty-four byte latency.",
                "MRV-166",
            ),
            (
                "**Honest boundary**: **1–4 KiB** savings mostly avoid DPDK stack overhead; at **64 B**, eliminating TX DMA reads is a **true hardware win**.",
                "One plain-English boundary keeps our claims completely honest. At one to four kilobytes, a modern static-ring network card has an isomorphic P C I express transfer, so our C P U win mainly reflects skipping D P D K software stack overhead. At sixty-four bytes, however, eliminating transmit D M A reads is a genuine hardware win.",
                "MRV-166",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C06
    dict(
        id="C06",
        file="C06Real100gbe",
        title="5 · Real 100GbE & 1-Core Line Rate",
        stage=4,
        source="Two-Host 100GbE: saturn ⇄ CN106 ⇄ 3 m DAC ⇄ mercury (ConnectX-5)",
        cues=[
            (
                "**Question 5**: Do these gains survive a **real 100GbE cable**—and can **one single host core** actually saturate **100 Gbps** end-to-end?",
                "Question five: do these gains still matter over a real hundred gigabit Ethernet cable, and can strictly one host core saturate hundred gigabit line rate?",
                "MRV-152 / MRV-133 / MRV-155",
            ),
            (
                "Over **saturn → CN106 → 3 m DAC → mercury (ConnectX-5)**: end-to-end **64 B** RTT drops **10.21 → 5.96 µs** (**−4.25 µs / −41.6%**).",
                "Connecting saturn through C N one oh six and a three-meter hundred gigabit cable to mercury's ConnectX-five, real wire latency never hides the gain. Sixty-four byte round trip drops from ten point two one to five point nine six microseconds—saving forty-one point six percent.",
                "MRV-152 / MRV-133",
            ),
            (
                "Every size wins on wire: **256 B** saves **3.76 µs** (**−36%**), **4 KiB** saves **2.33 µs**, and `Mix-Geo` 64 B **p99** drops **68%** (**96.5 → 30.7 µs**).",
                "Every packet size wins over the wire: two hundred fifty-six bytes saves three point seven six microseconds, four kilobytes saves two point three three, and mixed sixty-four byte tail latency drops sixty-eight percent to thirty point seven microseconds.",
                "MRV-152 / MRV-133",
            ),
            (
                "Polling cheap local host rings ahead of costly Ethernet status checks trims another **−370 ns** end-to-end (**6.03 → 5.66 µs**, **0.94 µs** on-card).",
                "One clean card-side scheduling rule—polling cheap local host rings before expensive Ethernet port status checks—trims another three hundred seventy nanoseconds directly off end-to-end latency, restoring zero point nine four microseconds on-card.",
                "MRV-154",
            ),
            (
                "**1-core 100GbE** test: into the NIC, **1 host core** pushes **161.4 Mpps**—above 100GbE's 64 B cap (**148.8 Mpps**)—vs optimal SDP's **50.5 Mpps**.",
                "Next, can one single host core drive hundred gigabit line rate? Across P C I express, one DirectPush core sustains one hundred sixty-one point four million packets per second—above hundred gigabit's sixty-four byte limit—while optimal S D P stalls at fifty point five million.",
                "MRV-155",
            ),
            (
                "End-to-end into `mercury` PHY on **1 host core**: **64 B UDP** hits **99.76 Gbps**, **64 B RoCEv2** **99.68 Gbps** (**1.96×** SDP), **256 B** **99.86 Gbps**!",
                "From end to end into mercury's physical port on one host core, sixty-four byte U D P hits ninety-nine point seven six gigabits, sixty-four byte Rocky v two hits ninety-nine point six eight—nearly twice optimal S D P—and two hundred fifty-six bytes hits ninety-nine point eight six gigabits.",
                "MRV-155",
            ),
            (
                "**Honest ceilings**: bare 64 B caps at **99.2 Mpps** (`66.7 Gbps`) on CN106's default single TX scheduler; **1–4 KiB** hits **94.0–96.5 Gbps**.",
                "Two honest hardware ceilings: bare sixty-four byte frames cap at ninety-nine point two million packets per second on this card's default single scheduler—hitting full hundred gigabits from one hundred six byte frames up—while one to four kilobyte D M A reaches ninety-four to ninety-six point five gigabits at the P C I express ceiling.",
                "MRV-155",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C07
    dict(
        id="C07",
        file="C07ActiveNic",
        title="6 · Active-NIC Burst Buffering",
        stage=5,
        source="100 M/s RX Bursts (64 KiB → 128 MiB) · 256 KiB Hot Host Ring",
        cues=[
            (
                "**Question 6**: What about the **receive side** during **100 M/s** traffic bursts—won't bursting into host rings thrash **host LLC** and **DRAM**?",
                "Question six: what about the receive side when a hundred million packet per second burst hits? Won't pouring bursts into host rings thrash host caches and D RAM?",
                "MRV-158",
            ),
            (
                "**Passive NIC (Scheme A)** dumps bursts into a **128 MiB** host ring—blowing past the **8 MiB** DDIO window and **60 MiB** host LLC.",
                "A passive NIC dumps incoming bursts straight into a one hundred twenty-eight megabyte host ring, overflowing the eight megabyte D D I O window and the entire sixty megabyte host cache.",
                "MRV-158",
            ),
            (
                "**Active NIC (Scheme B)** holds burst backlog on the **NIC**; the host keeps only a **256 KiB hot ring**, refilled in place as credits return.",
                "Our active NIC flips that around. Backlog stays buffered on the NIC itself, while the host keeps only a tiny two hundred fifty-six kilobyte hot ring—permanently cache-resident—refilled in place as credits return.",
                "MRV-158",
            ),
            (
                "Zero-backlog overhead is practically zero: **+6 ns** on idle RTT (**0.887 → 0.893 µs**), zero concurrency loss, **176.9 M/s** streaming.",
                "With no burst, credit checks cost just six nanoseconds on idle latency, zero loss under concurrency, and one hundred seventy-six point nine million per second streaming.",
                "MRV-158",
            ),
            (
                "At **128 MiB** bursts: host LLC misses drop **91.4% → 0.0%**, DDIO climbs **16.3% → 100%**, and **host DRAM drops −98.9%** (**972 → 11 MiB**).",
                "Under one hundred twenty-eight megabyte bursts, host L L C read misses plunge from ninety-one point four percent to zero, D D I O stays at one hundred percent, and host D RAM traffic drops ninety-eight point nine percent—from nine hundred seventy-two down to eleven megabytes.",
                "MRV-158",
            ),
            (
                "**Conservation law**: bursts **≤ 32 MiB** sit inside CN106's **48 MiB L3** (~0 system DRAM); **> 48 MiB** cleanly offloads to **card DDR5**.",
                "Dual-side counters reveal clean conservation: bursts up to thirty-two megabytes fit in the card's forty-eight megabyte L three cache, keeping system D RAM traffic near zero. Above forty-eight megabytes, the NIC's dedicated six-channel D D R five absorbs the spill, keeping host memory traffic minimal at about eleven megabytes.",
                "MRV-158",
            ),
            (
                "Refill stays **0.91 µs** (`1.05–1.16 µs` cold) and 128 MiB drain wins **+8.4%**. **Trade-off**: 1 card core drains `1–32 MiB` bursts slightly slower.",
                "Refill stays sub-microsecond—zero point nine one microseconds sequentially, up to one point one six cold—while one hundred twenty-eight megabyte drain wins eight point four percent. Honestly, one card core drains mid-size bursts slightly slower before winning past forty-eight megabytes.",
                "MRV-158",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C08
    dict(
        id="C08",
        file="C08Takeaways",
        title="7 · Paper Contributions & Recipe",
        stage=6,
        source="Synthesis: Cache-Aware PCIe + Native DMA + Credits + Active NIC",
        cues=[
            (
                "**So what are the paper's contributions?** Every design step followed directly from testing **CC-NIC's assumption** on real silicon.",
                "So what are the paper's core contributions? Stepping back, every design step followed directly from testing C C NIC's central assumption on real commercial hardware.",
                "MRV-113 / MRV-120",
            ),
            (
                "**1 · Plain PCIe is fast**: unfenced push hits **175.2 M/s** (**97.9%** line rate), **897 ns** 3-hop cache RTT (with **IOMMU**), and **0%** TX coherence tax.",
                "Contribution one: plain P C I express is already fast—one hundred seventy-five million writes per second on one core, eight hundred ninety-seven nanosecond cache latency with full I O M M U, and zero transmit coherence tax.",
                "MRV-148 / MRV-149",
            ),
            (
                "**2 · DirectPush interface**: small push + **native NIC DMA** (**0 DSA**) + **dual credits** → **14,700×** HoL isolation & **1.5–2.1×** lower CPU vs fair SDP.",
                "Contribution two: DirectPush pairs small inline push with native NIC D M A, uses zero Intel D S A, and adds dual credit windows—delivering fourteen thousand seven hundred times head-of-line isolation and up to two point one times lower host C P U than fair tuned S D P.",
                "MRV-150 / MRV-151 / MRV-166",
            ),
            (
                "**3 · Real 100GbE proof**: **−4.25 µs** (**−42%**) two-host RTT, and **1 single host core** saturating **99.8 Gbps** end-to-end into ConnectX-5.",
                "Contribution three: real hundred gigabit validation—cutting two-host latency forty-two percent and driving ninety-nine point eight gigabits per second from strictly one host core.",
                "MRV-152 / MRV-133 / MRV-155",
            ),
            (
                "**4 · Active-NIC burst isolation**: holding bursts on the NIC behind a **256 KiB** hot host ring cuts host DRAM **−98.9%** at **100% DDIO**.",
                "Contribution four: active-NIC burst isolation—keeping receive bursts on the NIC behind a two hundred fifty-six kilobyte hot host ring cuts host D RAM traffic ninety-eight point nine percent while keeping host caches clean.",
                "MRV-158",
            ),
            (
                "**One-sentence takeaway**: fast NICs do **not** need cache coherence—they need **cache-aware PCIe**, **native DMA**, **credit pacing**, and an **active NIC**.",
                "Here is our one-sentence paper takeaway: high-speed network cards do not need hardware cache coherence. Cache-friendly P C I express, built-in D M A, credit pacing, and an active NIC already get us there today.",
                "MRV-113 / MRV-120",
            ),
        ],
    ),
]
