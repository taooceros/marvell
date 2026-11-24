# Verified Facts Ledger — "CC-NIC Does Not Need Cache Coherence" (`CcnicPaperStory`)

Every number in this video is verified directly against the 5 paper-story reports (`MRV-113`, `MRV-120`, `MRV-148`–`MRV-152`), the 4 audited follow-up experiments (`MRV-166`, `MRV-154`, `MRV-155`, `MRV-158`), and the raw JSONL logs under `logs/mrv*/runs.jsonl`.
**Strictly excluded**: Intel DSA (removed from paper scope in MRV-120); retracted sync-tail `late_tails` claim and inflated default-SDP `3.6×–7.9×` headline claims (replaced by MRV-166 fair async-tail SDP ledger).

---

## Global Testbed & Verification Scope (`MRV-113`, `MRV-120`, `MRV-26`)
- **Host (`saturn`)**: Intel Xeon Gold 6438M (Sapphire Rapids), pinned single core `cpu40 @ 3.90 GHz`, Socket-1 uncore pinned `2.20 GHz`, `60 MiB` LLC (`~8 MiB` 2-way Intel DDIO window), `2 MiB` pre-touched hugepages (`page_faults = 0`), **`intel_iommu=on,sm_on` always enabled**.
- **NIC / DPU (`CN106xx`)**: Marvell OCTEON 10 `crb106-pcie` on **PCIe Gen5 ×4** (`32 GT/s × 4 × 128/130 / 8 = 15.75 GB/s` raw per direction), 24 ARM Neoverse-N2 cores `@ 2.00 GHz`, `48 MiB` L3 (`64 B` coherence granularity; `128 B` LLC cache line), 6-channel `48 GB` DDR5, native `DPI DMA` engine, physical `100GbE` port `eth0` (`RPM0 + NIX0`).
- **Remote peer (`mercury`)**: Xeon 6438M + Mellanox ConnectX-5 (`mlx5_2`, PCIe Gen3 ×16), connected via **3 m passive 100GbE DAC** (`No FEC`).
- **Safety & Scale**: BAR4 lease handshake (`MRV-26`), `> 2.5 billion` messages/Ethernet frames across **15+ hardware experiments** (`3,050+` core verified runs plus `126` MRV-166 + `78` MRV-154 + `178` MRV-155 P2 + `108` MRV-158 = `3,540+` verified runs), **`0` torn, `0` corrupt, `0` SEI-UER, `0` EDAC UE**, `100%` non-multiplexed Host/Card PMU.

---

## Report 1/5 — Premise & Microarchitecture (`MRV-148`, `MRV-89`, `MRV-97`, `MRV-76`, `MRV-55`, `MRV-75`, `MRV-112`)
- **Refuting "PCIe MMIO is slow" (`MRV-89`, 54 runs)**:
  - `64 B` `MOVDIR64B` to BAR4 (`> 4 GiB` 4DW header → `88 B` wire TLP: `4 B STP + 16 B hdr + 64 B payload + 4 B LCRC`).
  - Theoretical PCIe Gen5 ×4 64 B MWr ceiling: `15.7538 GB/s / 88 B = 179.02 M/s` (`11.46 GB/s` payload, `72.7%` efficiency).
  - Measured `SFENCE` interval sweep (`1` host core, `batch = 1`):
    - `K = 1` (fence every write, as in CC-NIC Fig. 2): **`8.10 M/s`**
    - `K = 2`: `15.67 M/s`; `K = 4`: `30.19 M/s`; `K = 8`: `55.34 M/s`; `K = 16`: `101.16 M/s`; `K = 32`: `165.85 M/s`
    - `K = 64`: `175.19 M/s`; `K = ∞` (unfenced): **`175.20 M/s`** (`11.21 GB/s`, **`97.9%`** of PCIe Gen5 ×4 64 B line rate — **`21.6×`** faster than `K = 1`!).
    - Writing the *same* 64 B BAR4 address repeatedly collapses to **`8.89 M/s`** (per-core serialization).
  - Why DMA beats `MOVDIR64B` on large payloads: amortizes fixed `24 B` MWr / `20 B` CplD TLP overhead across `128–512 B` TLPs (`128 B`: `84.2%` / `13.27 GB/s`; `256 B`: `91.4%` / `14.40 GB/s`; `512 B`: `95.5%` / `15.05 GB/s`). Native `DPI DMA` measured `201.6 M 64B-lines/s` (`12.90 GB/s`, `97%` of 128 B TLP ceiling, `MRV-22`).
- **Sub-microsecond RTT without coherence (`MRV-97`, 40 runs)**:
  - `64 B` `W = 1` round trip (`MOVDIR64B → BAR4`, card `WC` reply → host DDIO): **`897.3 ns`**.
  - Comparisons: stock same-card `SDP` raw ping-pong **`2,400 ns`** (`~2.7×` slower; full app harness `4.64–4.85 µs`); real CXL 1.1 hardware **`CXL-NIC`** (Ji et al., MICRO '25) **`1.3–2.1 µs`** (without IOMMU).
  - Three default cache shortcuts verified by cold-flushing one hop at a time:
    1. Host TX source in L1D saves **`117.3 ns`** (end-to-end) vs cold DRAM;
    2. Card BAR4 write lands in card `48 MiB` L3 (**`99.94%`** hit), saving **`75.0 ns`** on card read;
    3. Card WC reply lands in host LLC via **Intel DDIO** (`1.00 ItoM/msg`), saving **`87.3 ns`** on host read.
- **Two real DPU microarchitecture traps (`MRV-76`, `MRV-55`, `MRV-75`, `MRV-112`)**:
  - **Trap 1 — Live write-frontier polling (`MRV-76`, `MRV-55`)**: Card core polling the live `~3 µs` write frontier slows host push from **`162.6 M/s`** to **`113.5 M/s`**. Reading behind the `~3 µs` frontier restores `160.4–162.6 M/s` even at `1,033 M/s` card L3 read rate. Card L3 coherence granularity is strictly **`64 B`**.
  - **Trap 2 — `PEM0/IOBN` inbound bridge serialization cliff (`MRV-75`, `MRV-112`, 131 + 86 runs)**: Under concurrent closed-loop traffic (`W = 48–128`) + relentless empty card polling, inbound writes serialize at **`76.4 ns/msg` (`~13.1 M/s`)**, even while host CHA and card L3 TAD (`occ 0.02–0.12`) sit nearly empty. Widening ring cell stride (`64 / 128 / 4096 / 4224 B`, `MRV-112`) leaves all four locked at **`13.12–13.55 M/s`** (3.3% spread), proving bridge serialization rather than cache-line or page-prefetch contention.

---

## Report 2/5 — TX Buffer Reuse & Zero Coherence Tax (`MRV-149`, `MRV-116`, `MRV-125`)
- **Two distinct TX buffer lifetimes**:
  1. **Host → NIC hop (lossless PCIe)**: Direct push copies `64 B` by value into card BAR4 memory; NIC never reads host TX memory back. Reusing a single `64 B` buffer in-place (`stack_0`, `MRV-116`) hits **`174.8 M/s`** (`11.19 GB/s`, **`97.7%`** of Gen5 ×4 line rate) with **zero TX buffer pool**.
  2. **Upper-layer retention (e.g., TCP retransmission pool)**:
     - **CC-NIC Fig. 15** (ASPLOS '24, 56 cores): Step 1 disabling hot-stack TX reuse (TCP retention) drops throughput **`−20%`** from cross-link invalidations; Step 2 `4 KiB` buffer stride brings cumulative drop to **`−50%`**; Step 3 removing NIC-managed buffers brings cumulative drop to **`−73%`** (`1.3×` latency).
     - **DirectPush sequential retention (`64 KiB → 128 MiB`, exceeding `60 MiB` host LLC, `MRV-116`)**:
       - Closed-loop `W = 64` stays virtually flat: `stack 47.68` → `64 KiB 47.52` → `4 MiB 48.10` → `32 MiB 47.80` → `128 MiB 47.22 M/s` (**`−0.9%`** across a `2,000×` pool expansion, `MRV-149` rounded `47.5 → 47.1 M/s`).
       - Idle `W = 1` RTT holds constant at **`0.91 µs`**.
       - Across `297` closed-loop runs (`816M` card replies), host RX DDIO hit rate is **`99.99993%`** (`99.999964%` MRV-116 + `99.999914%` MRV-125), with **`0` DDIO misses at `128 MiB`** — **zero cross-link coherence tax**.
     - **Unidirectional H2C retention boundary (`MRV-116`)**: `stack 174.8 M/s`, `32 MiB 153.9 M/s`, `128 MiB 128.1 M/s` (`−17.5%`, single host core hitting local DDR5 + PCIe ceiling).
- **Diagnosing `2 KiB` random-pool drop (`MRV-125`, 165 runs, shaped `p1` avoiding `PEM0` cliff, `65,536` pkts)**:
  - Compact seq `4 MiB`: **`50.9 M/s`** (`50.92`)
  - Compact rand `4 MiB`: **`44.4 M/s`** (`44.37`)
  - `2048 B` (`2^11`) stride rand (`128 MiB` span, only `4 MiB` active!): collapses to **`22.3 M/s`** (`22.28`) — power-of-two stride locks low 5 LLC set bits to `0`, shrinking effective LLC to **`1/32` (`1.875 MiB`)**.
  - **One-line falsification**: Add `64 B` to stride → **`2112 B` odd-line stride** (`132 MiB` span): rebounds immediately to **`43.9 M/s`** (`43.93`)!
  - True `128 MiB` fully random active working set (`> 60 MiB` LLC): **`21.5 M/s`** (`21.48`) — pure host-local DDR5 bound, while NIC RX DDIO stays **`100%` (`0` misses)**.

---

## Report 3/5 — Flow Control & Tail Latency (`MRV-150`, `MRV-115`, `MRV-123`)
- **Clarification**: `~13 M/s` is NOT the channel ceiling (`175.2 M/s` raw push, `50.4 M/s` 1-core RPC, `128 M/s` saturated unidirectional); it is a specific concurrent polling serialization cliff (`76.4–77 ns/msg`).
- **Host in-flight credit cap (`MRV-115` 459 runs + `MRV-123` 255 runs)**:
  - Unshaped at `16–30 M/s`: writes back up into host CHA (`48.5–248.4` entries), sending end-to-end `tot p99` to `35–51 µs` (`44.5 µs` in MRV-123 / `50.8 µs` in MRV-115 at `20 M/s` Poisson; `756–1960 µs` at `25–30 M/s` paced).
  - Adding a host credit cap (`32–48` in-flight) drops host CHA backlog to **`1.4–3.1` entries**, cuts `16–30 M/s` `tot p99` by **`6–64×`** (`20 M/s` Poisson optimal **`7.01 µs`** tot / `3.23 µs` wire in MRV-123 `C=48, B=32`), and lifts 4-card-core cliff throughput **`+43%`**.
- **Honest arithmetic boundary (`[18, 25]` gap)**:
  - Under relentless card polling, a burst drain obeys `0.58 µs + W × 77 ns`:
    - Staying `≤ 2.0 µs` requires **`W ≤ 18`** in-flight;
    - Sustaining `20 M/s` at `~1.25 µs` RTT requires **`W ≥ 20 × 1.25 = 25`** in-flight (Little's Law).
  - Host-only shaping cannot satisfy both at `20 M/s` (`C = 32` caps out at `17.9 M/s`); honest `≤ 2 µs` safe zone is **`≤ 13.5 M/s`** (1 card core) / **`≤ 10 M/s`** (4 card cores).
  - Static card backoff (`1.0–1.8 µs` pause on every empty poll) reaches `~2.3 µs` at `20 M/s`, but degrades idle `0.9 µs` RTT to `> 2 µs`. Solution: **on-demand busy-burst backoff** (pause only after draining a busy burst, never on idle single packets).

---

## Report 4/5 — `DirectPush` Hybrid Design & Fair Async-Tail Audit (`MRV-151`, `MRV-117`, `MRV-127`, `MRV-166`)
- **Pure-NIC hybrid architecture (Zero Intel DSA, Zero hidden cores)**:
  - Small messages (`≤ 256 B`): cache-shortcut direct push (`MOVDIR64B` + `WC`).
  - Large payloads (`≥ 512 B / 1 KiB`): host pushes one `64 B` descriptor; NIC's built-in **`DPI DMA`** (`201.6 M lines/s` = `97%` line rate, `fetch 187.6 M/s`) transfers directly from/to host's `128 MiB` retention pool with zero host `memcpy`.
- **Why hybrid beats full-size direct push (`MRV-117`, 333 runs; `MRV-127`, 228 runs)**:
  - At `4 KiB @ 1.0 M/s`, switching large payloads from full-size direct push to native `DPI DMA` cuts host CPU **`2.86×`** (`46.6% → 16.3%`, host LLC read misses `14.7 → 0.00/pkt`) and hits `18.83 GB/s` bidirectional.
  - On `Mix-Geo` (large-heavy), full-size direct push collapses to **`7.76 GB/s`** (`64 B p99 = 19.5 µs`), whereas `DirectPush` delivers **`18.68 GB/s`** (`2.41×`) with `64 B p99 = 1.50 µs`.
  - Mixed-workload throughput beats `SDP` by **`2.16×`** (`Mix-Geo`) to **`3.13×`** (`Mix-Ads`, `13.06 vs 4.17 M/s`).
  - **Dual credit windows (`W_small=32, W_large=32, C_line=32`, `MRV-127`)**:
    - Eliminates `256 B` cliff (`5.00 Mreq/s`, `+11%` vs `SDP`); switching `512 B` to DMA speeds it up `3.47×`.
    - Under open-loop saturated `4 KiB` (`18.6 GB/s`), `64 B` small-packet `p99` drops from **`40.5 ms`** (shared window) to **`2.75 µs`** (**`14,701×`** HoL isolation) at full `8.00 Mreq/s`.
    - Random-page `4 KiB` KV access across `128 MiB` (`98.4%` cross-page): **`0.0%`** degradation (honest note: `64 × 2 MiB` hugepages fit inside IOTLB hot set).
- **MRV-166 Fair Async-Tail SDP Audit (`126/126` verified runs — replaces retracted `late_tails` / inflated `SDP` claims!)**:
  - Single-variable `4 KiB @ 1.0 M/s` (`8.19 GB/s` bidir) host-CPU ablation (`1H + 1C`, `cpu40 @ 3.90 GHz`):
    1. Default `SDP` (`OUT_CNTS` MMIO + copy): `3862 cyc/pkt` (`99.0%`)
    2. `+rx_poll_mem=1` (drop MMIO poll, still copy): `2350 cyc` (`60.2%`, `−1513 cyc`)
    3. `SDP (sync-tail, +zero_copy)`: `1490 cyc` (`38.2%`, **`late_tails = 54.78%`**, `DDIO = 45.6%`)
    4. **`SDP (async-tail, rx_poll_tail_off=16)`**: **`1031 cyc`** (`26.4%`, **`late_tails = 0.00%`**, real cut **`−459 cyc/pkt`**!)
    5. `+ism_enable=1` + `512-slot DDIO RX ring & separate pools`: `DDIO 45.2% → 100.0%`, host DRAM writes **`35.6 → 0.5 CAS/pkt` (`71×` cut)**
    6. **`SDP (fair-full, B=1)`**: **`1011 cyc`** (`25.90%`) vs **`DirectPush (B=1)`**: **`508 cyc`** (`13.01%`) → **`1.99×`**
    7. **`B=8` batched**: `SDP (fair-full)` **`858 cyc`** (`21.97%`) vs `DirectPush` **`461 cyc`** (`11.82%`) → **`1.86×`**
  - **Four-layer clean CPU ledger (`64 B / 1 KiB / 4 KiB`, `SDP fair-full` vs `DirectPush`)**:
    - `64 B @ 1.5 M/s`: `B=1` `731 → 474 cyc` (`1.54×`); `B=8` `419 → 212 cyc` (`1.98×`); same-card idle `W=1 p50` **`4.635 → 0.885 µs` (`5.24×` faster)**.
    - `1 KiB @ 1.0 M/s`: `B=1` `956 → 462 cyc` (`2.07×`); `B=8` `694 → 416 cyc` (`1.67×`).
    - `4 KiB @ 1.0 M/s`: `B=1` `1011 → 508 cyc` (`1.99×`); `B=8` `858 → 461 cyc` (`1.86×`).
    - **Layer breakdown**:
      - Layer 1 — Short app quantum (`τ_app = 330.6 ns`, measured): **`1.54×–2.07×`** (`B=1`) / **`1.67×–1.98×`** (`B=8`)
      - Layer 2 — Typical microservice quantum (`τ_app = 5 µs`, `[INF]`): **`1.35×–1.81×`**, matching empty-poll-stripped net processing **`1.33×–1.79×`**
      - Layer 3 — Subtracting CN106 software two-line tail-check tax (`1 KiB` measured `+114 cyc` total / `+120 cyc` net, `[INF]`): **`1.26×–1.53×`** (`+107–256 cyc/pkt`, generic DPDK `mbuf`/`rx_refill`/`flush_iq` stack overhead)
      - Layer 4 — Honest boundary vs modern bare-ring NICs (`mlx5`/`E810` + HW CQE): **`1–4 KiB` is PCIe-isomorphic** (no hardware CPU win claimed); **`64 B` small packets have a real hardware/interface win** (zero TX DMA PCIe read `0.0` vs `2.2/pkt`, instant local reuse, `5.24×` same-card idle RTT).

---

## Report 5/5 — Real 100GbE Wire & Follow-Ups (`MRV-152`, `MRV-118`, `MRV-133`, `MRV-135`, `MRV-137`, `MRV-154`, `MRV-155`, `MRV-158`)
- **Real dual-host 100GbE latency (`MRV-133`, 348 runs; `MRV-118` NIX loopback 303 runs)**:
  - Topology: `mercury` (ConnectX-5) ⇄ `3 m` passive `100GbE` DAC ⇄ CN106 `eth0` (`RPM0 + NIX0`) ⇄ `saturn`.
  - `64 B` wire `W = 1` RTT: `SDP` **`10.21 µs`** → `DirectPush` **`5.96 µs`** — saves **`4.25 µs` (`−41.6%`)**! Subtracting `4.28 µs` fixed wire+card baseline (`card_echo`), host-NIC interface drops from `5.93 µs` to `1.68 µs`.
  - `256 B` saves **`3.76 µs`** (`−36.2%`); `4 KiB` (`DPI DMA`) saves **`2.33 µs`** over strong `SDP` (`−3.17 µs` over default `SDP`).
  - `Mix-Geo` `64 B` `p99` drops `68.2%` (`96.5 → 30.7 µs`).
- **MRV-154 — Root-causing the `+0.41 µs` on-card gap (`1.305 → 0.935 µs`, 78 runs)**:
  - Why did the on-card PCIe hop read `1.305 µs` over 100GbE vs `0.89 µs` standalone?
  - Not NPA `mbuf` free (`18.7 ns`; deferring free only cut `10 ns`). Not `6 µs` cold arrivals (`SYNTH` stays `0.915–0.925 µs`).
  - **Root cause**: CN106 `cnxk` driver's empty `eth0` RX poll executes synchronous MMIO atomic `LDADDA NIX_LF_CQ_OP_STATUS`, blocking **`581.8 ns`** — **`1,119×`** slower than local L1 `BAR4` poll (`0.52 ns`)!
  - The 1:1 polling loop fell into `2.01` empty `eth0` polls per packet, quantizing `0.935 µs` onto a **`2 × 582 ns` staircase** (`1.305 µs`).
  - Single-variable `--bar4-first` (`W = 1` light-load knob): on-card `64 B` `CNTVCT p50` drops **`1.305 → 0.935 µs` (`−370 ns`)** (`256 B: 1.355 → 1.205 µs`), and `mercury` end-to-end drops 1:1 **`−370 ns`** (`6.025 → 5.655 µs` same-round; `W = 32` auto-bypasses with zero overhead). Note: full general fix via `cq_ctx->caching = 1` CQE peek is Backlog `MRV-161`.
- **4-Core RSS Scale-Out & Lockstep Discovery (`MRV-135`, 198 runs; `MRV-137`, 213 runs)**:
  - `4RxQ/4TxQ` RSS `256 B` microbursts hit the exact **`13.3 M lines/s`** `PEM0` cliff (end-to-end queues explode `> 127–669 ms`, CHA `38.6`). Card-cursor line credits (`C_line = 48`) lift steady capacity `+69%` to **`22.5 M lines/s`** (CHA `2.1`).
  - Exclusive `128 B`-stride sub-rings (`MRV-137`): **1 host core** reaches **`33.24 M lines/s`** (`99.1%` of 4 host cores' `33.53 M`) and **`48.2 M lines/s`** unshaped (`83.8%` of 4-core `card_echo`). The `33.5 M` cap was prototype credit-writeback lockstep (`T_cycle = 1.91 µs`), not host CPU.
- **MRV-155 — Single Host Core Driving 100GbE End-to-End (`178` Phase 2 runs, unbiased `mercury` PHY)**:
  - **Stage 1 (1 host core → BAR4, no wire)**: `DirectPush` (`batch_cells = 128`, 1 `SFENCE` + batched doorbell → `0.00 M/s` TAD retries) pushes **`161.42 Mpps`** (`64 B` cells — beating `100GbE` `64 B` L2 line rate `148.81 Mpps` by **`+8.5%`**!). Same-condition optimal `ISM=1` `SDP` caps at **`50.52 Mpps`** (`dpdk-testpmd` bare TX `64.66 Mpps`, 4-core `testpmd` `107.61 Mpps` — proving `64 B` `SDP` is single-host-core CPU bound).
  - **Stage 0 (CN106 `eth0` default single `SMQ[0]` cap)**: Card TX caps at **`99.77 Mpps`** (`67.04 Gbps` wire for `64 B` L2). Across L2 frame sizes (`64 → 84 → 100 → 106 → 128 → 1500 B`), the `99.7 Mpps` packet cap intersects the `100 Gbps` wire cap at the exact **`106 B` L2 knee** (`99.22 Mpps × 126 B × 8 = 100.01 Gbps`).
  - **Stage 2 (1 host core → CN106 → 3 m DAC → `mercury` PHY)**:
    - `64 B` L2 (`84 B` wire): `99.23 Mpps` (`66.69 Gbps` wire) vs optimal `SDP` `50.27 Mpps` (**`1.97×`**).
    - `64 B` UDP payload (`110 B` L2, static 5-tuple template + L1 payload): **`95.93 Mpps` / `99.76 Gbps`** vs `SDP` `50.60 Mpps` (`52.62 Gbps`) — **`1.90×`**, **full 100GbE line rate on 1 host core!**
    - `64 B` RoCEv2 UD (`134 B` L2): **`80.91 Mpps` / `99.68 Gbps`** vs `SDP` `41.35 Mpps` (`50.94 Gbps`) — **`1.96×`**, `0.0%` vs `69.1%` host `sb_stall`.
    - `256 B` RoCEv2 UD (`326 B` L2): **`36.08 Mpps` / `99.86 Gbps`** vs `SDP` `84.11 Gbps` (**`1.19×`**).
    - `1 KiB` / `4 KiB` RoCEv2 (`DPI DMA`, `MTU=1500` `4×1K` frags): **`93.99 / 96.49 Gbps`** (vs `SDP` `90.48 / 90.33 Gbps`), capped by the `86.4–88.7 Gbps` host→card payload transfer ceiling (`~92%` wire share).
- **MRV-158 — Active-NIC RX Burst Absorption (`256 KiB` Hot Host Ring vs `128 MiB` Host Ring, `108/108` verified runs)**:
  - Setup: `1` host core + `1` card core, `64 B` msgs, `100 M/s` bursts (`64 KiB → 128 MiB`), Scheme A (`128 MiB` monotonic host ring) vs Scheme B (backlog stays on active NIC, host keeps **`256 KiB`** hot ring, credit refill per `512` msgs).
  - **Zero-backlog overhead**: `W = 1` mean `0.887 → 0.893 µs` (**`+6 ns`**); `W = 32` throughput `13.05 → 13.30 M/s` (zero regression); credit-on streaming reaches `176.91 M/s` (`11.32 GB/s`, `100%` DDIO).
  - **Burst protection (`128 MiB` × 4 bursts = `512 MiB` payload)**:
    - Host LLC read misses: **`91.4%` → `0.0%`** (`0.01–0.02%`)!
    - Host DDIO hit rate: **`16.3%` → `100.0%`**!
    - Host IMC DDR5 DRAM traffic: **`971.52 MiB` (`1.90 lines/pkt`) → `10.69 MiB` (`≈ 0.02 lines/pkt`)** — **`−98.9%` host DRAM traffic**!
  - **System-wide conservation duality**:
    - `≤ 32 MiB`: Absorbed completely in CN106's `48 MiB` L3 (`0.00` card DDR5) → truly eliminates system-wide DRAM traffic (`≈ 0.02/pkt` total vs Scheme A's `0.70–1.60/pkt`).
    - `> 48 MiB` (`128 MiB`): Offloads `1.98` accesses/pkt (`1,014.41 MiB`) onto CN106's dedicated 6-channel DDR5 (exact conservation dual to Scheme A's `1.90` host lines/pkt), keeping card drain steady at **`175.36 M/s` (`−0.14%` across the `48 MiB` L3 boundary)**!
  - **Refill latency & drain trade-off**:
    - Single-queue sequential credit refill holds **`0.905 µs` (`p99 0.945 µs`)** across `512 KiB → 128 MiB`; cold first-packet upper bound (multi-flow switch) is **`1.05–1.16 µs`** (`0.970 → 1.155 µs`).
    - Drain speed: at `128 MiB`, Scheme B beats Scheme A by **`8.4%`** (`5.69 vs 6.21 ns/pkt`); honest trade-off at `1–32 MiB` where single card core drains slightly slower (`4.44–5.66 vs 3.07–5.39 ns/pkt`, `+1.8%–15.4%` over full arrival+drain cycle) before winning at `≥ 48 MiB`.
