# CC-NIC Does Not Need Cache Coherence — Narration Script (`CcnicPaperStory`)

Total: **12 scenes**, **67 cues**, **17.16 min** (1029.7 s).

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
| `C01` | Intro | 5 | 62.9 s | — |
| `C02` | 1 · PCIe MMIO Isn't Slow | 5 | 63.6 s | MRV-148 · MRV-89 (54 runs) · Xeon cpu40 3.9 GHz → CN106 BAR4 |
| `C03` | 1 · Sub-µs & Two DPU Traps | 6 | 91.8 s | MRV-148 · MRV-97 (40) · MRV-76 (33) · MRV-75 (131) · MRV-112 (86) |
| `C04` | 2 · TX Reuse & Zero Coherence Tax | 6 | 91.8 s | MRV-149 · MRV-116 (204 runs) · MRV-125 (165 runs) · 816M writes |
| `C05` | 3 · Taming Inbound Bridge Congestion | 6 | 83.7 s | MRV-150 · MRV-115 (459 runs) · MRV-123 (255 runs) |
| `C06` | 4 · DirectPush: Hybrid + Native DMA | 6 | 90.1 s | MRV-151 · MRV-117 (333 runs) · MRV-127 (228 runs) · Zero DSA |
| `C07` | 4 · Fair Async-Tail SDP Audit | 6 | 109.5 s | MRV-166 (126 runs, verified) · MRV-151 updated 4-layer CPU ledger |
| `C08` | 5 · Real 100GbE & 582 ns Staircase | 5 | 90.6 s | MRV-152 · MRV-118 (303) · MRV-133 (348) · MRV-154 (78 runs) |
| `C09` | 5 · 1-Core 100GbE & 106 B Knee | 6 | 105.0 s | MRV-152 · MRV-135 (198) · MRV-137 (213) · MRV-155 P2 (178 runs) |
| `C10` | 5 · Active-NIC 256 KiB Hot Ring | 6 | 102.9 s | MRV-158 (108 verified runs) · 100 M/s bursts · 64 KiB → 128 MiB |
| `C11` | Scientific Rigor & Boundaries | 5 | 67.5 s | MRV-120 · MRV-37 multi-model reviews · 5 hypotheses tightened |
| `C12` | Takeaways: Fast NICs Without Coherence | 5 | 70.2 s | MRV-113 · MRV-120 · DirectPush pure-PCIe synthesis |

---

## C01: Intro (62.9 s)

- **[0.60–11.42s]** (`MRV-113 / CC-NIC ASPLOS'24`) ASPLOS '24 **CC-NIC** argued PCIe MMIO is too slow—and NICs must move to **cache-coherent** interconnects like CXL.
  - *Spoken*: A S P L O S twenty twenty-four C C NIC argued that ordinary P C I express M M I O is too slow, so NICs must move to cache-coherent C X L.
- **[11.77–20.91s]** (`MRV-113 / MRV-120`) Our measurements find the opposite: **fast NICs need no hardware cache coherence**—and no special CPU accelerators like Intel DSA.
  - *Spoken*: Our measurements find the opposite. Fast NIC interfaces need no hardware cache coherence, and no special C P U accelerators like Intel D S A.
- **[21.26–34.52s]** (`MRV-113 / MRV-152`) Testbed: Xeon **saturn**, commercial **PCIe Gen5 x4** Marvell **CN106**, full **IOMMU**, and a **3 m 100GbE** cable to **mercury**.
  - *Spoken*: Our testbed is off-the-shelf hardware: Xeon host saturn, a P C I express gen five by four Marvell C N one oh six with hardware I O M M U enabled, and a three meter hundred gigabit cable to mercury.
- **[34.87–48.05s]** (`MRV-120 / MRV-154 / MRV-155 / MRV-158`) **3,500+ verified runs**, **>2.5 B frames**, zero errors: **0.9 µs** RTT, **1-core 100GbE**, **0% coherence tax**, **−98.9%** burst DRAM.
  - *Spoken*: Across thirty-five hundred zero-error runs and two point five billion frames: sub-microsecond latency, one-core hundred gigabit line rate, zero coherence tax, and ninety-eight point nine percent less burst D RAM.
- **[48.40–61.52s]** (`MRV-113 / MRV-148..152`) Five-part story: **Premise** → **TX Buffers** → **Flow Control** → **DirectPush & Audit** → **Real 100GbE & Active NIC**.
  - *Spoken*: Here is the five-part story: refuting the slow P C I express premise, transmit buffer reuse, bridge flow control, DirectPush with a fair baseline audit, and real hundred gigabit Ethernet plus an active NIC.
## C02: 1 · PCIe MMIO Isn't Slow (63.6 s)

*Source: `MRV-148 · MRV-89 (54 runs) · Xeon cpu40 3.9 GHz → CN106 BAR4`*

- **[0.60–7.26s]** (`MRV-148`) Report 1 tests CC-NIC's core premise: is ordinary **PCIe direct push** actually slow?
  - *Spoken*: Report one tests the core premise of C C NIC: is ordinary P C I express direct push really slow?
- **[7.61–18.27s]** (`MRV-148 / MRV-89`) Inserting an **SFENCE** after every 64 B `MOVDIR64B` drops throughput to **8.10 M/s**—just like CC-NIC Fig. 2.
  - *Spoken*: Inserting an S fence after every single sixty-four byte move dir sixty-four B write stalls throughput at eight point one zero million per second—just like C C NIC figure two.
- **[18.62–33.30s]** (`MRV-89`) Relax fence interval `K`: **32 host CHA queues** pipeline writes—at `K=64` or unfenced, **1 core hits 175.2 M/s** (**21.6×**).
  - *Spoken*: Relax the fence interval K, and thirty-two host C H A queues pipeline writes in parallel. At K equals sixty-four or unfenced, one core hits one hundred seventy-five point two million per second—twenty-one point six times faster.
- **[33.65–49.59s]** (`MRV-148 / MRV-89`) **175.2 M/s** (**11.21 GB/s**) is **97.9%** of Gen5 x4's 64 B ceiling (**179.0 M/s**); only hammering one fixed address drops to **8.89 M/s**.
  - *Spoken*: With twenty-four bytes of P C I express framing per sixty-four byte write, gen five by four tops out at one hundred seventy-nine million per second—so one core hits ninety-seven point nine percent of line rate. Only hammering one fixed address drops to eight point eight nine million.
- **[49.94–62.16s]** (`MRV-148 / MRV-22`) Why does **DMA** win on large payloads? **128–512 B TLPs** amortize the fixed **24 B** PCIe header, reaching **13.3–15.1 GB/s**.
  - *Spoken*: For large payloads, native D M A still wins by packing contiguous data into one hundred twenty-eight to five hundred twelve byte packets, amortizing the twenty-four byte header up to fifteen gigabytes per second.
## C03: 1 · Sub-µs & Two DPU Traps (91.8 s)

*Source: `MRV-148 · MRV-97 (40) · MRV-76 (33) · MRV-75 (131) · MRV-112 (86)`*

- **[0.60–11.92s]** (`MRV-148 / MRV-97`) Without cache coherence, doesn't every message hit slow **DRAM**? No—on commercial PCIe, **all three cache shortcuts are already on**.
  - *Spoken*: Without cache coherence, doesn't every message crawl through slow D RAM? We cold-flushed each hop one at a time and found all three cache shortcuts are already active on ordinary P C I express.
- **[12.27–30.61s]** (`MRV-97`) Host **L1D** saves **117.3 ns**; BAR4 writes hit **card L3** (**99.94%**, **−75.0 ns**); card WC replies hit **host LLC via DDIO** (**−87.3 ns**).
  - *Spoken*: Host L one source reads save one hundred seventeen nanoseconds. B A R four writes land in card L three at ninety-nine point nine four percent hits, saving seventy-five nanoseconds. Write-combining replies land in host L L C via Intel D D I O, saving eighty-seven nanoseconds.
- **[30.96–47.78s]** (`MRV-148 / MRV-97`) Verified 64 B round trip: **897.3 ns** with full **IOMMU**—beating stock `SDP` (**2,400 ns**) and real **CXL-NIC** (**1.3–2.1 µs**, no IOMMU).
  - *Spoken*: A verified sixty-four byte round trip takes eight hundred ninety-seven point three nanoseconds with hardware I O M M U enabled—nearly three times faster than stock S D P, and faster than real C X L NIC hardware at one point three to two point one microseconds.
- **[48.13–55.31s]** (`MRV-148 / MRV-120`) Testing on a real DPU—not a second CPU socket—exposed **two real PCIe microarchitecture traps** via hardware PMUs.
  - *Spoken*: Running on a real D P U rather than a two-socket C P U emulator also exposed two real microarchitecture traps.
- **[55.66–73.50s]** (`MRV-76 / MRV-55`) **Trap 1 (write-frontier snooping)**: card polling the live **~3 µs** frontier slows push **162.6 → 113.5 M/s**; card L3 granularity is **64 B**.
  - *Spoken*: Trap one: polling the exact cells inside the live three-microsecond write frontier slows host push from one hundred sixty-two point six to one hundred thirteen point five million per second. Reading behind that frontier restores full speed, and card L three granularity is strictly sixty-four bytes.
- **[73.85–90.43s]** (`MRV-75 / MRV-112`) **Trap 2 (PEM0/IOBN cliff)**: concurrent RPC + empty card polling serializes inbound writes to **76.4 ns (~13.1 M/s)** across **64–4224 B** strides.
  - *Spoken*: Trap two: under concurrent load plus relentless empty card polling, inbound writes serialize inside the P E M zero bridge at seventy-six point four nanoseconds—thirteen point one million per second—invariant from sixty-four to four thousand two hundred twenty-four byte strides.
## C04: 2 · TX Reuse & Zero Coherence Tax (91.8 s)

*Source: `MRV-149 · MRV-116 (204 runs) · MRV-125 (165 runs) · 816M writes`*

- **[0.60–10.20s]** (`MRV-149`) Report 2 splits **two TX buffer lifetimes**: **host-to-NIC transfer** vs **upper-layer TCP retransmission retention**.
  - *Spoken*: Report two examines transmit buffers by splitting two distinct lifetimes: transferring to the NIC, and retaining packets for T C P retransmission.
- **[10.55–26.39s]** (`MRV-149 / MRV-116`) PCIe is **lossless** and pushes **64 B by value**—the NIC never reads host TX RAM back. Reusing **one 64 B buffer** hits **174.8 M/s** (**97.7%**).
  - *Spoken*: Because P C I express is lossless and direct push copies sixty-four bytes by value, the NIC never reads host transmit memory back. Overwriting one single sixty-four byte buffer hits one hundred seventy-four point eight million per second—zero buffer pool needed.
- **[26.74–40.76s]** (`MRV-149 / MRV-120`) For **TCP retention**, **CC-NIC Fig. 15** pays cross-link invalidations (**−20%**), plus **−50%** at 4 KiB stride and **−73%** with host pools.
  - *Spoken*: When T C P must retain sent buffers until remote acks, C C NIC figure fifteen pays cross-link invalidations that cut throughput twenty percent—climbing to fifty percent at four kilobyte strides and seventy-three percent with host pools.
- **[41.11–59.09s]** (`MRV-149 / MRV-116`) With direct push, growing a sequential pool **2,000×** (**64 KiB → 128 MiB**) holds **47.5 → 47.1 M/s** (**−0.9%**, **99.99993%** DDIO, **0** misses).
  - *Spoken*: With direct push, the host retention pool never enters NIC caches. Growing it two thousand times from sixty-four kilobytes to one hundred twenty-eight megabytes holds throughput flat at forty-seven point one million per second, with ninety-nine point nine nine nine nine percent D D I O hits and zero misses.
- **[59.44–73.26s]** (`MRV-149 / MRV-125`) Why did a **2048 B-stride random pool** halve speed (**44.4 → 22.3 M/s**)? Power-of-two `2^11` stride aliases into **1/32** of host LLC sets!
  - *Spoken*: When a two-kilobyte random message-buffer pool cut speed in half from forty-four point four to twenty-two point three million, the culprit was host-local: two thousand forty-eight is a power of two, colliding into one out of thirty-two host L L C sets.
- **[73.61–90.45s]** (`MRV-149 / MRV-125`) Add **64 B** → **2112 B** stride: speed rebounds to **43.9 M/s**! True 128 MiB random hits host DDR5 (**21.5 M/s**) with **100% DDIO**.
  - *Spoken*: Pad each slot by sixty-four bytes to two thousand one hundred twelve bytes, and speed rebounds to forty-three point nine million. Even a truly random one hundred twenty-eight megabyte set hits local D D R five while NIC D D I O stays one hundred percent—zero coherence tax.
## C05: 3 · Taming Inbound Bridge Congestion (83.7 s)

*Source: `MRV-150 · MRV-115 (459 runs) · MRV-123 (255 runs)`*

- **[0.60–14.58s]** (`MRV-150`) Myth buster: **~13 M/s is NOT the channel cap** (raw push hits **175 M/s**, 1C RPC **50.4 M/s**)—it is a concurrent polling collision trap.
  - *Spoken*: Report three tames the thirteen million per second bridge cliff. First, thirteen million is not the channel cap—raw push hits one hundred seventy-five million and one-core R P C hits fifty million. It is a specific polling collision trap.
- **[14.93–28.07s]** (`MRV-150 / MRV-115`) Unshaped at **16–30 M/s**, serialized writes back up into **48–248** host CHA entries, exploding open-loop **p99** to **44–51 µs**.
  - *Spoken*: Under open-loop arrivals at sixteen to thirty million per second, serialized writes back up into forty-eight to two hundred forty-eight host C H A entries, exploding tail latency to forty-four to fifty-one microseconds.
- **[28.42–43.98s]** (`MRV-150 / MRV-115 / MRV-123`) A **host credit cap** (`W=32–48`) empties CHA (**1.4–3.1**), cuts **p99 6–64×** (**44.5 → 7.01 µs** Poisson), and lifts 4C cliff **+43%**.
  - *Spoken*: Adding a host in-flight credit gate drains C H A backlog down to one point four to three point one entries, cuts tail latency six to sixty-four times—down to seven point zero one microseconds at twenty million Poisson—and eliminates the four-core collapse.
- **[44.33–52.01s]** (`MRV-150 / MRV-123`) Honest boundary: can host-only credits hold **≤ 2 µs** at **20 M/s**? No—PMUs reveal an unavoidable **[18, 25] arithmetic gap**.
  - *Spoken*: Can host-only credits hold two microseconds at twenty million per second? Hardware counters prove no, due to a strict arithmetic gap.
- **[52.36–68.46s]** (`MRV-150 / MRV-123`) Serial drain is **0.58 µs + W × 77 ns** → `≤ 2 µs` needs **W ≤ 18**, while **20 M/s** at `1.25 µs` RTT needs **W ≥ 25** (Little's Law).
  - *Spoken*: Serial drain takes zero point five eight microseconds plus W times seventy-seven nanoseconds. Staying under two microseconds needs W at most eighteen, yet twenty million per second at one point two five microseconds requires at least twenty-five in flight.
- **[68.81–82.29s]** (`MRV-150 / MRV-143`) Host-only `≤ 2 µs` safe zone: **≤ 13.5 M/s** (1C) / **≤ 10 M/s** (4C). Static pauses hurt **0.9 µs** idle RTT → **on-demand busy-burst backoff**.
  - *Spoken*: So host-only shaping holds two microseconds up to thirteen point five million on one card core and ten million on four. Static pauses hurt zero point nine microsecond idle latency, pointing to on-demand busy-burst backoff.
## C06: 4 · DirectPush: Hybrid + Native DMA (90.1 s)

*Source: `MRV-151 · MRV-117 (333 runs) · MRV-127 (228 runs) · Zero DSA`*

- **[0.60–10.04s]** (`MRV-151`) Datacenter traffic mixes small RPCs (**≤ 256 B**) and bulk payloads (**1–4 KiB**). Pushing 4 KiB line-by-line wastes CPU and clogs BAR4.
  - *Spoken*: Report four assembles the complete interface for mixed workloads. Pushing four kilobytes line by line wastes host C P U and clogs the B A R four window.
- **[10.39–26.79s]** (`MRV-151 / MRV-120 / MRV-22`) We **removed Intel DSA** in MRV-120: every commercial NIC ships native **DMA** (`DPI DMA` hits **201.6 M lines/s**, **97%** line rate).
  - *Spoken*: We removed Intel D S A completely to stay universal. Every commercial P C I express NIC already ships native D M A—on C N one oh six, D P I D M A reaches ninety-seven percent of line rate with zero special C P U accelerators.
- **[27.14–41.60s]** (`MRV-151 / MRV-117`) **DirectPush** splits by size: **≤ 256 B** goes inline (**0.885 µs**); **≥ 512 B** pushes one **64 B command** and card **DPI DMA** moves data zero-copy.
  - *Spoken*: DirectPush splits cleanly by size: up to two hundred fifty-six bytes goes inline in zero point eight eight five microseconds; five hundred twelve bytes and up pushes a single sixty-four byte command for zero-copy card D P I D M A.
- **[41.95–59.61s]** (`MRV-151 / MRV-117`) Bare-ring **4 KiB** ablation: native **DPI DMA** cuts host CPU **2.86×** (**46.6% → 16.3%**), zeroes LLC misses, and lifts `Mix-Geo` **7.76 → 18.68 GB/s**.
  - *Spoken*: Switching four kilobytes from full push to native D P I D M A cuts host C P U two point eight six times—from forty-six point six to sixteen point three percent—zeroes L L C misses, and lifts Mix-Geo from seven point seven six to eighteen point six eight gigabytes per second.
- **[59.96–76.56s]** (`MRV-151 / MRV-127`) **Dual credit windows** (`W_small=32`, `W_large=32`) shield 64 B under saturated **18.6 GB/s** 4 KiB: **40.5 ms → 2.75 µs** (**14,701×** HoL isolation).
  - *Spoken*: Under open-loop mixed loads, splitting small and large credits into two independent windows slashes sixty-four byte tail latency from forty point five milliseconds to two point seven five microseconds—fourteen thousand seven hundred times lower under saturated four kilobyte traffic.
- **[76.91–88.67s]** (`MRV-151 / MRV-127`) `98.4%` random cross-page KV in **128 MiB** sees **0.0%** DMA drop (honest note: 64 × 2 MiB hugepages fit inside the hot IOTLB).
  - *Spoken*: Random cross-page key-value access across one hundred twenty-eight megabytes sees zero D M A degradation—noting honestly that sixty-four two-megabyte pages fit inside the hot I O T L B.
## C07: 4 · Fair Async-Tail SDP Audit (109.5 s)

*Source: `MRV-166 (126 runs, verified) · MRV-151 updated 4-layer CPU ledger`*

- **[0.60–16.50s]** (`MRV-151 / MRV-166`) Review caught an unfair artifact: our strong `SDP` baseline spun synchronously on **54.8%** of 4 KiB tails—so we retracted it and ran **MRV-166**.
  - *Spoken*: During review, we caught an implementation flaw: our optimized S D P baseline spun synchronously waiting for fifty-four point eight percent of four kilobyte tails. We retracted that claim and ran M R V one sixty-six to make S D P completely fair.
- **[16.85–32.11s]** (`MRV-166`) **MRV-166** single-variable ladder (**4 KiB @ 1 M/s**): default **3862 cyc** (`99%`) → `rx_poll_mem` **2350** → sync-tail **1490** (`38.2%`).
  - *Spoken*: On four kilobytes at one million per second, default S D P burns three thousand eight hundred sixty-two cycles—ninety-nine percent of a core. Memory polling cuts it to two thousand three hundred fifty, and zero-copy to one thousand four hundred ninety cycles.
- **[32.46–43.62s]** (`MRV-166`) Switching `SDP` to **async tail poll** (`rx_poll_tail_off=16`) zeroes `late_tails` (**54.8% → 0.0%**) and cuts **−459 cyc/pkt** → **1031 cyc**.
  - *Spoken*: Switching S D P to non-blocking async tail polling zeroes late-tail spins from fifty-four point eight percent to zero, cutting four hundred fifty-nine cycles per packet down to one thousand thirty-one.
- **[43.97–64.87s]** (`MRV-166`) `+ism_enable=1` + **512-slot DDIO RX ring** lifts DDIO **45.2% → 100%**, cuts DRAM writes **71×** → **SDP fair-full 1011** vs **DirectPush 508 cyc**.
  - *Spoken*: Adding I S M completion counting and a five hundred twelve slot D D I O ring lifts S D P's D D I O hits to one hundred percent and cuts host D RAM writes seventy-one times. Fair S D P lands at one thousand eleven cycles versus five hundred eight for DirectPush—or eight hundred fifty-eight versus four hundred sixty-one at batch eight.
- **[65.22–88.76s]** (`MRV-166`) **4-layer CPU ledger** (`64 B / 1 KiB / 4 KiB`): **1.54–2.07×** (`330 ns`) → **[INF] 1.35–1.81×** (`5 µs`) → **[INF] 1.26–1.53×** DPDK stack tax.
  - *Spoken*: Across sixty-four bytes, one kilobyte, and four kilobytes, the clean gap splits into four layers: one point five four to two point zero seven times at short work quanta, one point three five to one point eight one at five microseconds, and one point two six to one point five three times of generic D P D K stack overhead after C N one oh six's two-line tail check.
- **[89.11–108.13s]** (`MRV-166`) Honest boundary: **1–4 KiB** is PCIe-isomorphic to bare-ring NICs (`mlx5`/`E810`); **64 B** keeps a **real HW win** (**0 vs 2.2** TX reads, **5.24×** RTT).
  - *Spoken*: Honest boundary: at one to four kilobytes, DirectPush is P C I express isomorphic to modern static-ring NICs with hardware completion queues. At sixty-four bytes, however, eliminating transmit D M A reads and instant reuse is a true hardware win, with five point two four times lower same-card idle latency.
## C08: 5 · Real 100GbE & 582 ns Staircase (90.6 s)

*Source: `MRV-152 · MRV-118 (303) · MRV-133 (348) · MRV-154 (78 runs)`*

- **[0.60–10.42s]** (`MRV-152 / MRV-133`) Report 5 connects a **real 100GbE physical wire**: **mercury (ConnectX-5) ⇄ 3 m DAC ⇄ CN106 eth0 (RPM0+NIX0) ⇄ saturn**.
  - *Spoken*: Report five plugs in a real three-meter hundred gigabit Ethernet cable between mercury's ConnectX-five and C N one oh six's physical Ethernet port zero.
- **[10.77–30.39s]** (`MRV-152 / MRV-133`) Wire latency doesn't hide the gain: **64 B** RTT drops **10.21 → 5.96 µs** (**−4.25 µs / −41.6%**); **256 B** saves **3.76 µs**; **4 KiB** saves **2.33 µs**.
  - *Spoken*: The wire never drowns out the gain. Sixty-four byte round trip drops from ten point two one to five point nine six microseconds—saving four point two five microseconds, or forty-one point six percent. Two hundred fifty-six bytes saves three point seven six microseconds, and four kilobytes saves two point three three.
- **[30.74–48.28s]** (`MRV-152 / MRV-154`) Minus **4.28 µs** fixed wire+MAC floor (`card_echo`), host-NIC drops **5.93 → 1.68 µs**—yet on-card PCIe read **1.305 µs** instead of **0.89 µs**. Why?
  - *Spoken*: Subtracting the four point two eight microsecond fixed wire floor cuts the host-NIC part from five point nine three down to one point six eight microseconds. Still, the on-card P C I express hop read one point three zero five microseconds instead of zero point eight nine. Why?
- **[48.63–68.19s]** (`MRV-154`) **MRV-154** ruled out `mbuf` free (`18.7 ns`): CN106's empty `eth0` poll executes synchronous MMIO **LDADDA**, blocking **581.8 ns** (**1,119×** slower than BAR4 `0.52 ns`)!
  - *Spoken*: M R V one fifty-four found the culprit: every empty Ethernet port zero receive poll executes a synchronous M M I O atomic instruction, L D add A, stalling the card core for five hundred eighty-one point eight nanoseconds—over one thousand one hundred times slower than a half-nanosecond local B A R four poll.
- **[68.54–89.16s]** (`MRV-154`) The 1:1 loop trapped `W=1` in **2 empty eth0 polls** (`2 × 582 ns`). **--bar4-first** restores **0.935 µs** on-card and cuts **−370 ns** 1:1 end-to-end!
  - *Spoken*: The one-for-one polling loop always trapped single packets inside two empty Ethernet polls, creating a two-times five hundred eighty-two nanosecond staircase. Prioritizing B A R four snaps the P C I express hop back to zero point nine three five microseconds and cuts three hundred seventy nanoseconds one-to-one off mercury's end-to-end latency.
## C09: 5 · 1-Core 100GbE & 106 B Knee (105.0 s)

*Source: `MRV-152 · MRV-135 (198) · MRV-137 (213) · MRV-155 P2 (178 runs)`*

- **[0.60–18.56s]** (`MRV-152 / MRV-135 / MRV-137`) **4-core 100GbE RSS**: unshaped `256 B` bursts hit **13.3 M lines/s**; line credits reach **22.5 M**, exclusive `128 B` sub-rings **33.2 / 48.2 M**—on **1 host core**!
  - *Spoken*: On four-core hundred gigabit R S S, unshaped bursts hit the exact thirteen point three million line cliff. Line credits lifted steady rate to twenty-two point five million, and exclusive sub-rings let one host core hit thirty-three point two million—or forty-eight point two million unshaped—matching four host cores.
- **[18.91–28.01s]** (`MRV-155`) Ultimate stress test (**MRV-155**): can **strictly 1 host core** saturate **100 Gbps** end-to-end into `mercury`?
  - *Spoken*: That led to M R V one fifty-five: keeping saturn strictly at one single host core, can DirectPush saturate a real hundred gigabit link into mercury?
- **[28.36–48.64s]** (`MRV-155`) **Stage 1 (1 host core → BAR4)**: `DirectPush` pushes **161.42 Mpps** (above 100GbE 64 B max **148.81 Mpps**) vs optimal `ISM=1` `SDP` **50.52 Mpps**.
  - *Spoken*: We isolated four stages. At Stage one into B A R four, one host core using batched doorbells sustains one hundred sixty-one point four two million sixty-four byte cells per second—beating hundred gigabit wire rate's one hundred forty-eight point eight million. Optimal S D P with I S M stalls at fifty point five million on that same host core.
- **[48.99–67.87s]** (`MRV-155`) **Stage 0 (card eth0 alone)**: CN106's default single `SMQ[0]` caps at **99.77 Mpps**—meeting **100 Gbps** at the exact **106 B L2 knee**!
  - *Spoken*: At Stage zero, testing Ethernet port zero alone found C N one oh six's default single scheduler queue caps at ninety-nine point seven seven million packets per second. Adding twenty wire bytes per frame, ninety-nine point two million times one hundred twenty-six bytes hits one hundred gigabits at an exact one hundred six byte knee.
- **[68.22–88.52s]** (`MRV-155`) **Stage 2 (1 host core → mercury PHY)**: **64 B UDP (110 B L2)** hits **99.76 Gbps**, **64 B RoCEv2** **99.68 Gbps** (**1.90–1.96×** `SDP`), **256 B** **99.86 Gbps**!
  - *Spoken*: At full Stage two end-to-end, once headers cross the one hundred six byte knee—using static card templates and L one payloads—one host core drives ninety-nine point seven six gigabits for U D P, ninety-nine point six eight for Rocky v two—nearly twice optimal S D P—and ninety-nine point eight six at two hundred fifty-six bytes.
- **[88.87–103.63s]** (`MRV-155`) At **1–4 KiB** (`DPI DMA`), wire rate sits at **94.0–96.5 Gbps** as payload meets the **86–89 Gbps** host-to-card PCIe transfer ceiling.
  - *Spoken*: At one kilobyte and four kilobytes via native D P I D M A, wire rate lands at ninety-four to ninety-six point five gigabits as headers shrink to eight percent and payload meets the eighty-six to eighty-nine gigabit host-to-card transfer ceiling.
## C10: 5 · Active-NIC 256 KiB Hot Ring (102.9 s)

*Source: `MRV-158 (108 verified runs) · 100 M/s bursts · 64 KiB → 128 MiB`*

- **[0.60–12.32s]** (`MRV-158 / MRV-153`) **MRV-158** tests an **active NIC** on the RX path: during **100 M/s** traffic bursts, **don't dump 128 MiB into host RAM!**
  - *Spoken*: Report two left receive buffering open. M R V one fifty-eight tests an active NIC: during hundred million per second traffic bursts, stop dumping one hundred twenty-eight megabytes into host memory.
- **[12.67–25.35s]** (`MRV-158`) **Scheme A** (`128 MiB` host ring) vs **Scheme B** (backlog stays on **active NIC**; host keeps only a **256 KiB hot ring** refilled by credits).
  - *Spoken*: Scheme A pours bursts into a one hundred twenty-eight megabyte host ring. Scheme B buffers the backlog on the active NIC, keeping just a two hundred fifty-six kilobyte hot ring on the host refilled in place as credits return.
- **[25.70–38.50s]** (`MRV-158`) Zero-backlog overhead is practically zero: **W=1 mean +6 ns** (`0.887 → 0.893 µs`), `W=32` zero regression, streaming **176.9 M/s**.
  - *Spoken*: With zero backlog, checking credits costs only six nanoseconds on single-packet mean latency, zero regression under concurrency, and streaming still reaches one hundred seventy-six point nine million per second.
- **[38.85–60.09s]** (`MRV-158`) At **128 MiB bursts** (`512 MiB` total): host LLC misses **91.4% → 0.0%**, DDIO **16.3% → 100%**, **host DRAM traffic −98.9%** (**971.5 → 10.7 MiB**).
  - *Spoken*: Under one hundred twenty-eight megabyte bursts, Scheme A blows past host cache, hitting ninety-one point four percent L L C misses and nine hundred seventy-one megabytes of host D RAM traffic. Scheme B locks host reads into two hundred fifty-six kilobytes: zero percent misses, one hundred percent D D I O, and ninety-eight point nine percent less host D RAM.
- **[60.44–78.54s]** (`MRV-158`) **Conservation duality**: **≤ 32 MiB** fits in CN106's **48 MiB L3** (**≈ 0.02** system DRAM/pkt); **> 48 MiB** offloads **1.98/pkt** to card **6-ch DDR5**.
  - *Spoken*: System-wide counters reveal clean conservation: bursts up to thirty-two megabytes fit in the card's forty-eight megabyte L three cache, eliminating system D RAM traffic. Above forty-eight megabytes, the NIC's dedicated six-channel D D R five offloads the full two X memory traffic off the host.
- **[78.89–101.53s]** (`MRV-158`) Past 48 MiB L3, card drain holds **175.4 M/s** (**−0.14%**), beating Scheme A **+8.4%** at 128 MiB; credit refill stays **0.905 µs** (**1.05–1.16 µs** cold).
  - *Spoken*: Across the card L three wall, drain holds steady at one hundred seventy-five point four million per second, beating Scheme A by eight point four percent at one hundred twenty-eight megabytes. Sequential refill stays at zero point nine zero five microseconds, with a one point zero five to one point one six microsecond cold bound and a modest mid-burst single-core trade-off.
## C11: Scientific Rigor & Boundaries (67.5 s)

*Source: `MRV-120 · MRV-37 multi-model reviews · 5 hypotheses tightened`*

- **[0.60–11.30s]** (`MRV-120 / MRV-37`) Every experiment underwent **independent multi-model audit (MRV-37)**—actively **tightening or retracting five optimistic claims**.
  - *Spoken*: What shaped this entire story is scientific discipline. Every experiment went through independent multi-model verification, and whenever a claim did not hold up, we tightened or retracted it.
- **[11.65–25.31s]** (`MRV-120 / MRV-112 / MRV-123`) **1. Host-only 20 M/s ≤ 2 µs**: falsified by **[18, 25]** gap (`7.01 µs` best). **2. Stride fix**: `64–4224 B` all stay locked at `~13 M/s`.
  - *Spoken*: First, host-only credit shaping cannot hit two microseconds at twenty million per second because of the eighteen-versus-twenty-five gap. Second, widening ring stride up to four kilobytes did not fix the thirteen million bridge cliff.
- **[25.66–40.90s]** (`MRV-166 / MRV-151`) **3. Sync-tail SDP retracted (MRV-166)**: async tail poll zeroes `late_tails` (**−459 cyc**), leaving **1.26–1.53×** DPDK stack tax (`1–4 KiB` bare-ring equal).
  - *Spoken*: Third, we retracted our early synchronous tail-spin claim against S D P: M R V one sixty-six proved non-blocking tail polling eliminates the wait, separating generic D P D K stack overhead from real sixty-four byte hardware gains.
- **[41.25–54.25s]** (`MRV-120 / MRV-149 / MRV-127`) **4. Exact CC-NIC Fig. 15 & host limits**: only **−20%** is TX retention; `2 KiB` drop is `1/32` LLC aliasing; `128 MiB` fits `64 × 2 MiB` IOTLB.
  - *Spoken*: Fourth, we corrected C C NIC figure fifteen to separate the twenty percent retention tax from stride aliasing, and noted where our one hundred twenty-eight megabyte pool stays inside the two-megabyte hugepage I O T L B.
- **[54.60–66.12s]** (`MRV-137 / MRV-155 / MRV-158`) **5. Exposed ceilings**: 4C `33.5 M` lockstep (`1.91 µs`), `SMQ[0]` **99.8 Mpps** default, and `1–32 MiB` 1C drain trade-off—all tracked in Backlog.
  - *Spoken*: Fifth, we exposed every remaining ceiling—credit lockstep, the default ninety-nine point eight million packet scheduler cap, and the mid-burst single-core drain trade-off—all tracked cleanly in backlog.
## C12: Takeaways: Fast NICs Without Coherence (70.2 s)

*Source: `MRV-113 · MRV-120 · DirectPush pure-PCIe synthesis`*

- **[0.60–15.68s]** (`MRV-148 / MRV-120`) Across all **five reports**: ordinary PCIe Gen5 already delivers **175.2 M/s**, **897 ns** 3-hop cache RTT, and **full IOMMU**—no CXL needed.
  - *Spoken*: Across all five reports, off-the-shelf P C I express gen five already delivers one hundred seventy-five million writes per second on one core, eight hundred ninety-seven nanosecond cache round trips, and full hardware I O M M U—no C X L needed.
- **[16.03–24.21s]** (`MRV-149 / MRV-150`) Dropping coherence **eliminates** TX invalidation tax (**99.99993%** DDIO, **0** misses at 128 MiB), while **credits** tame real DPU bridge cliffs.
  - *Spoken*: Dropping cache coherence removes cross-link cache invalidation tax completely, while credit shaping controls real D P U bridge congestion.
- **[24.56–44.36s]** (`MRV-151 / MRV-152 / MRV-154 / MRV-155`) **DirectPush** (small push + **native NIC DMA**, **0 DSA**): **14,701×** HoL isolation, **−4.25 µs** on 100GbE (**0.935 µs** on-card), **99.8 Gbps** on **1 core**.
  - *Spoken*: DirectPush pairs small push with native NIC D M A and zero Intel D S A: fourteen thousand seven hundred times head-of-line isolation, saving four point two five microseconds on real hundred gigabit wire, zero point nine three five microseconds on-card, and full hundred gigabit line rate from one host core.
- **[44.71–56.71s]** (`MRV-158`) An **active NIC** holding RX bursts behind a **256 KiB hot host ring** cuts host DRAM **−98.9%** and keeps host LLC **100% clean**.
  - *Spoken*: And an active NIC absorbing receive bursts behind a two hundred fifty-six kilobyte hot host ring slashes host D RAM traffic by ninety-eight point nine percent while keeping host caches completely clean.
- **[57.06–68.78s]** (`MRV-113 / MRV-120`) **CC-NIC does not need cache coherence**—it needs cache-aware PCIe, native NIC DMA, credit flow control, and an active NIC.
  - *Spoken*: The takeaway is simple: high-speed network cards do not need hardware cache coherence. They need cache-aware P C I express, native NIC D M A, credit flow control, and an active NIC.
