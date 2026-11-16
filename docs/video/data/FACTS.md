# Facts for the host ⇄ CN106xx PCIe datapath video

Every number allowed on screen, with its provenance. Source of truth: `~/dev/marvell/AGENTS.md` and the
`marvell-octeon-dpu-serial-access` skill (measurement log), runs of 2026-11-10/12 with the Rust testbed in
`~/dev/marvell/testbed`. All messages are 64 B, host batch 1, closed loop, unless stated. p50 = median.
`[INF]` = our inference (must carry an [INF] tag on screen).

## §Setup
- Host `saturn`: Intel Xeon Gold 6438M, 2 sockets; the card hangs off NUMA node 1; DPDK lcore 40.
- Card: Marvell OCTEON 10 CN106xx, board `crb106-pcie`, host PCIe address `0000:99:00.0`, link **PCIe Gen5 x4**.
- Card software: Ubuntu 24.04, SDK13.26.10 firmware, kernel 6.12.51. Arm cores at 2.0 GHz; card counter 1 GHz.
- Host DPDK: Marvell DPDK 26.10.1 (octeon_ep VF PMD). Card DPDK: 24.11 (cn10k).

## §Method
- Closed loop: host sends request, card reflects, host matches reply. W = concurrency (max outstanding).
  Batch = messages per doorbell / per `tx_burst` = 1 throughout.
- Wire format: seq, send TSC, tag, 32 B seq-derived payload pattern, trailing seq copy (inline cell: 64 B).
- Rust on both ends (x86-64 host, aarch64 card), fat LTO.
- Cross-check: at W=1, RTT ≈ 1 / message rate.

## §SDP (stock path, descriptor rings)
- W=1 p50 **4.85 µs** (p99 5.0); 0.20 M msg/s. Same-session reruns 4.85–4.96.
- Rate grows ~linearly to W=32 (5.06 M/s, p50 6.0 µs), saturates **7.2–7.7 M msg/s** for W ≥ 128 (RTT = W / rate).

## §Doorbell
- One host core, batch 1, one-way: **8.36 M pkt/s** (≈ 120 ns per packet).
- 1 / 2 / 4 / 8 TX queues (separate doorbells): **8.36 / 7.32 / 6.59 / 7.06 M pkt/s**.
- Burst 8: 36.7 M pkt/s; burst **32: 50.4 M pkt/s** (host → card, 1 core).
- Doorbell = 8 B uncached store (`rte_write64`) to BAR0. [INF] UC doorbells serialize per core at ~120–130 ns.

## §Inline (BAR4 send path)
- Host PF BAR4 = **16 windows × 4 MiB**; a PEM CSR (`BAR4_INDEX`) sets each window's card address.
- `inline_reflector` repoints **window 15** at a 4 MiB hugepage memzone, restores it on exit.
- Host writes 64 B cells with **MOVDIR64B** (one 64-byte direct store), no fence, no doorbell.
- Card core polls the next cell; hugepage memory is coherent with PCIe inbound writes.
- W=1 p50 **2.41 µs** with SDP replies (vs 4.85 SDP).
- Saturation, 1 card core, payload checked: **15.9 M msg/s** (first session), **13.66 M msg/s** (later sessions) →
  say "13.7–15.9, varies by session". SDP pingpong: ~7.5.

## §Order
- MOVDIR64B stores are weakly ordered (WC-like); later cell may land first without a fence.
- SFENCE after every cell: **6.86 M msg/s** at W=128.
- No fence, card skipping the payload check: **26.2 M msg/s**.
- Receiver rule: cell valid only when head seq == tail seq and pattern matches. Partial/torn cells seen: **0**.
- Cf. Liew et al., ASPLOS '26 ("Efficient Remote Memory Ordering"): destination-side ordering.

## §RDTSC
- Plain RDTSC right after the OUT_CNTS MMIO poll: inline W=1 measured **1.87 µs**; true **2.41 µs** (1 / rate).
- RDTSC is not ordered after earlier loads → timestamp taken before the MMIO read returned (~0.5 µs early).
- LFENCE; RDTSC = RDTSCP = **2.41 µs**. Back-to-back cost (TSC ticks): rdtsc **14.7**, lfence;rdtsc **22.0**,
  rdtscp **20.9**. Testbed uses RDTSCP (`dpdk::rdtsc_after_loads`).

## §MemPoll (return path option 1: `rx_poll_mem=1`)
- Stock PMD polls the **OUT_CNTS** register (MMIO read) every poll.
- Local PMD patch: poll the per-frame **info word** in host memory instead. Inline W=1 **2.41 → 2.00 µs**.
- SDP writes the info word with the frame head; the tail can land later: up to **6.3 µs** lag; none lost within 1 ms.
- Late tails per 1 M frames (W=1 / W=32): 64 B 0/0; 256 B 2/0; 512 B 1/0–1; 1024 B 0/35; 1514 B 37–47 / 170–193.
- Kept opt-in; testbed waits for the trailing seq copy (`FrameReplies`).

## §DMA (return path option 2: card DPI DMA)
- Card DPI engine (PF `0000:06:00.0`, VF `0000:06:00.1` on vfio-pci) copies each 64 B reply into a host memzone ring.
- DPI writes as host **VF0** → target must be an IOVA in the host DPDK process (host **IOMMU on**).
- Host polls only its own memory, no MMIO. W=1 p50 **1.33–1.60 µs** (run-to-run noise).
- DPI doorbell → completion **674–675 ns** (p99 720), independent of the source line's cache state;
  `DC CVAC/CIVAC` cleaning first made it slower (removed). DMA straight from the request cell: +5–7 % throughput.

## §Budget
- Host UC read of card DRAM through BAR4 (8 B or 16 B): **0.63–0.66 µs** p50 = one PCIe round trip.
- Card: poll loop **18 ns** per iteration; cell seen → DPI doorbell **59–67 ns**; DPI doorbell → done **675 ns**.
- [INF] DMA RTT ≈ host→card ~0.3 + card ~0.08 + DPI ~0.6 + card→host ~0.3 + host poll ≈ 1.3 µs
  (one-way halves inferred by halving the 0.63 read).

## §Pull (return path option 3)
- Card stores (tag, tsc) in the control cell (release order). Host waits `--pull-delay-ns`, then one **16 B UC read**.
- 150 ns wait: W=1 p50 **0.83 µs** (p99 0.90); repeats **0.83 / 0.83 / 1.04**; **1.17 M msg/s**.
- Wait sweep p50: 0 ns **0.89** (bimodal, p90 1.46) · 50 / 100 ns **1.43** (first read misses, second hits) ·
  150 **0.83** · 200 **0.88** · 300 **0.98** · 400 **1.09** µs.
- Cost: host core stalls **0.63 µs** per read → W=1 only. Verified 4.22 M pull replies.

## §StoreFind (return path option 4: how the card-core → host window was found, 2026-11-12)
- Marvell open Linux `pcie-marvell-cnxk-ep.c`, DAO `lib/pem`, octep agent: inbound BAR4 (host → card) only.
- Open Cavium BDK in coreboot (`bdk-csrs-pem.h`, CN9): `PEM()_REG_NORM()_ACC` outbound region table "for access to
  memory from cores"; **256 regions × 2 GiB** of core address space → PCIe address; fields `pf`, `vf`, `vf_active`
  "in RC mode must be zero" (→ usable in EP mode). Also rnmerge / wnmerge bits.
- CN10K offsets differ from the CN9 header; real ones from disassembling the card firmware (EBF, SPI backup):
  table at PEM0 CSR **+0x4000 + 16·i** (`0x8e00_0000_4000`), geometry `PEM0_CONST_ACC` at **+0x218** (256 × 2^31);
  region i at core PA **0x8800_0000_0000 + i·2 GiB**. EP mode: all 256 entries **0** (unused).
- Region **255** pointed at the host reply ring, writes sent as host **VF0** (`vf_active`, vf 0): worked first try,
  through the host IOMMU (no DMAR issue seen; host DMAR log not checked — no permission). Per core: region 255 − core;
  restored on exit. Stores only (a load through a region the host does not complete raises an SError).
- Incident: a read-only probe of **`0x87e0_0300_1000`** (a CSR the secure-world EBF reads) raised card **SEI-UER**
  (count **2 → 3**); the non-secure read returned junk. Card stayed up; no further errors in later tests.

## §Store (card core stores replies into host memory; `inline_reflector --store`, host `--reply ring`)
- W=1 p50 **0.94–0.98 µs** (some runs **1.23–1.27 µs**; two modes per host run, cause unknown, seen on other paths).
  vs DPI DMA 1.33–1.60, SDP 2.41, pull 0.83. First push path under 1 µs.
- Card cost: **21 ns** per reply including the completion barrier (DSB) vs DPI doorbell → done **675 ns**.
- Trap 1: one 64 B `ST1` (single instruction) → pieces land out of order (head + tail before middle) → stale payload (bad).
- Trap 2: PEM write merging on (`wnmerge=0`) + ascending stores → bad too (merging reorders).
- Fix: 4 × 16 B `STP`, head written last, merging off (`wnmerge=1`): **0** lost/bad/torn over **> 40 M** replies;
  fault injection **100 → 100** caught by the host.
- Rate W=512/1024: 1 card core **13.9–17.7 M msg/s**; 2–4 cores **13–17.6**; host polls **0 %** empty → host-bound.
  [INF] suspected cause: 4 partial-line writes per reply = cache-line ping-pong with the polling host core (unverified).
  Next step: one full-line write per reply.

## §MultiSDP (multi-core card, SDP replies; W=512 / W=1024)
- 1 card core: **13.66 M msg/s**; host memory polls **97 %** empty → card-bound.
- Core i takes seqs with (seq − 1) % n == i; one SDP TX queue per core.
- Gotcha: card SDP TX queue i → host VF RX queue i; with 1 host queue the other cores' replies were silently lost.
  Host now opens/polls one RX queue per card core.
- 2 cores: **17.1–17.2 M msg/s** (host polls 87–91 % empty; cause of plateau unknown).
- 4 cores, OUT_CNTS: **13.0–13.3** (W=512, host polls 0 % empty: 4 MMIO reads per poll) / 22.5–26.5 (W=1024).
- 4 cores, `rx_poll_mem=1`: 22.7–26.3 (W=512) / **24.4–27.9 M msg/s** (W=1024) ≈ **1.8 GB/s** of 64 B messages.
  Card cores then 4.2 cells per busy poll (not saturated).
- W=1 worse with more queues: 2 cores **2.76 µs** OUT_CNTS, **2.06 µs** mempoll (1 core: 2.41 / 2.00).

## §MultiDMA (multi-core DMA + coalescing)
- One DPI VF per card core (4 VFs `0000:06:00.1–.4`): total **13–15 M msg/s** (single queue, same session: 15.1).
- DPI doorbell → completion p50: **0.67 µs** (1 queue, W=1) → **5.8 µs** (2 queues, loaded) → **21–24 µs** (4 queues).
- Coalescing (`--dma-coalesce`, default 32): host reply ring split into **per-core sub-rings** so one core's batch is
  contiguous → one copy for many replies.
- 1 core: **15.1 → 20.0–20.9 M msg/s**, ~**15–19** replies per copy.
- Plus one host RDTSCP per poll instead of per reply: **21.7–23.2 M msg/s** (host polls **77–83 %** empty).
- 2–4 cores: **1.3–2.7** cells per busy poll → coalescing barely helps (12.5–19.7, noisy).
- W=1 DMA 1 core: 1.38–1.45 µs.

## §Verify
- Every reply matched to its outstanding request (seq + exact send TSC); trailing seq copy; payload pattern.
- All runs: lost **0**, bad **0**, torn **0**; card count == host count.
- Fault injection `--corrupt-every 1000`: card corrupt **100**, host bad **100**.
- Host AER counters (card PF `0000:99:00.0` and root port `0000:98:01.0`): all **0**.

## §Open
- 2-core SDP plateau at 17.2 M/s unexplained. ~0.2 µs run-level mode shifts (DMA 1.36 vs 1.57, pull 0.83 vs 1.04,
  card stores 0.97 vs 1.23).
- Card-store throughput host-bound at ~18 M/s (cause unverified). Host multi-core not done; DPI PF parameters /
  engine mask untried (need approval).

## Recommended configurations
- Max rate: SDP replies + 4 card cores + host `rx_poll_mem=1`: **24–28 M msg/s**.
- Balanced: DMA + 1 card core + coalescing: W=1 **~1.4 µs**, **22–23 M msg/s**.
- Fastest push: card core stores: W=1 **~0.96 µs**, 13.9–17.7 M msg/s (host-bound).
- Min latency: pull: **0.83 µs** (W=1 only).
