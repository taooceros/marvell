# Facts ledger — `CcnicPaperStory` ("CC-NIC does not need cache coherence")

Every number said or shown in the video, keyed by the `fact_src` ids used in `tts/ccnic_script_data.py`.
Our measurements were recomputed from the raw logs (`logs/<issue>-<date>/runs*.jsonl`) on 2026-11-24 by an
independent check (all 14 items OK; integrity fields lost = bad = torn = 0 in every run used). Paper numbers were
checked against the PDFs: CC-NIC (Schuh et al., ASPLOS '24, `homes.cs.washington.edu/~arvind/papers/CC-NIC.pdf`) and
CXL-NIC (Ji et al., MICRO '25, "Re-architecting End-host Networking with CXL", `saksham.web.illinois.edu/assets/pdf/cxl-nic.pdf`).

Excluded on purpose: Intel DSA (out of the paper's scope since MRV-120); the retracted SDP `late_tails` claim and the
inflated default-SDP host-CPU multipliers (3.6×–7.9×, 8–11×) — host-CPU claims use only the MRV-166 fair-SDP ledger;
any claim that we beat CC-NIC's emulated numbers.

## Testbed — F-TESTBED
- Host `saturn`: Intel Xeon Gold 6438M (Sapphire Rapids), measured core cpu40 at 3.90 GHz, uncore 2.20 GHz,
  `intel_iommu=on,sm_on` in every run.
- NIC: Marvell OCTEON 10 CN106 (`crb106-pcie`), PCIe Gen5 ×4, ARM cores at 2.00 GHz, 48 MiB L3.
- Peer `mercury`: ConnectX-5, 3 m 100GbE cable to the CN106's `eth0` (chapter 10 only).

## The debate (C02–C03)
- **F-PCIE-IF** (schematic, standard knowledge): descriptor-ring NICs — the CPU writes a descriptor in host memory and
  rings a doorbell (MMIO write); the NIC DMA-reads the descriptor, then the payload; receive = NIC writes packet and
  completion into host memory, the CPU polls. Crossing counts in C02 are a schematic (tagged SCHEMATIC).
- **F-CCNIC-LAT**: minimum 64 B loopback latency of PCIe NICs 2116 ns (CX6) and 3809 ns (E810) → "2.1–3.8 µs"
  (CC-NIC §5.3, Ice Lake platform).
- **F-CCNIC-ABS**: "We model CC-NIC using Intel's Ice Lake and Sapphire Rapids UPI interconnects … CC-NIC has 77% lower
  minimum latency … than today's PCIe NICs" (CC-NIC abstract). Host and NIC share cache lines; data moved by
  coherence (loads/stores), no doorbells or DMA engine in the CC-NIC design.
- **F-CCNIC-EMU**: "one socket acts as a software NIC" (CC-NIC §4); minimum loopback 490 ns (ICX) / 650 ns (SPR) (§5.3).
- **F-CCNIC-UNOPT**: "This case applies the E810 interface across UPI and achieves lower 64B packet rates than the
  native PCIe E810 … Relative to CC-NIC, this version shows 79% lower throughput and 2.1× higher minimum latency" (§5.2).
  Said as "a lower packet rate than the same card on plain PCIe" — not "slower" in general: its minimum latency
  (2.1 × 490 ns ≈ 1 µs, our arithmetic) is still below the E810's 3809 ns.
- **F-CCNIC-IF**: CC-NIC's interface redesign: signals inlined in the descriptor (§3.2, Fig. 6b) and metadata placed in
  writer-homed memory (§3.2).
- **F-CXLNIC**: real CXL 1.1 FPGA (Intel Agilex-7). Lowest-latency request combination uses "only non-coherent (NC)
  requests for all operations" (§7.1); the latency-optimal design (§7.2) is mostly non-coherent: NC-P pushes RX packets
  and completions into the host LLC, NC-read for TX, with one coherent read (CS-read) to cache RX descriptors — said
  as "relied mostly on non-coherent requests". Fig. 14
  (read from the figure, 64 B): CXL-NIC ≈ 1.3 µs, CC-NIC design on the same FPGA ≈ 2.1 µs. CXL devices "are not
  protected or enforced by the IOMMU like PCIe devices" (§8). Its PCIe premise: MMIO ">8 µs for 512B" and "<0.3GB/s" (§1).

## Coherence is not needed (C04–C05)
- **F-PREMISE**: CC-NIC Fig. 2 issues "an sfence barrier after each write"; "the MMIO WC data path cannot achieve high
  throughput without extensive batching … at least 4KB per barrier; with 64B packets … a batching factor of 64" (§2.2).
- **F-FENCE** (MRV-89, `logs/mrv89-2026-11-19/runs.jsonl`, `exp1_fence`, 1 thread): 64 B MOVDIR64B, batch 1:
  fence every write 8.10 M/s (8.1043); no fence 175.20 M/s (175.2022) → 21.6×; 64 B write ceiling of PCIe Gen5 ×4
  = 179.02 M/s → 97.9 %.
- **F-ORDER**: each 64 B cell carries the sequence number at head and tail; the receiver accepts it only when both
  match. Messages/frames verified with torn = 0 across the CC-NIC experiments ≈ 35.2 billion (≈ 13.2 billion without
  the one-way bulk datasets mrv89, mrv76, mrv155 phase 2) → said as "more than 2.5 billion" (the paper's figure).
- **F-CACHES** (MRV-97, `logs/mrv97-2026-11-19/runs.jsonl`, `cond=base`): card L3 hit 99.943 % for the card's reads of
  pushed cells; card write-combined replies land in the host LLC via DDIO (1.00 ItoM per message).
- **F-RTT** (MRV-97): 64 B W=1 round trip p50 897.27 ns ("897 ns"; "0.9 µs" in bar charts), IOMMU on.
- **F-SAMECARD** (MRV-166, `logs/mrv166-2026-11-23/runs.jsonl`, `part1_w1`): same card, same harness, 64 B W=1 p50:
  fairly tuned SDP 4.635 µs vs DirectPush 0.885 µs → 5.24× ("4.6 µs", "5.2×").

## Coherence is not free (C06)
- **F-RETAIN-CCNIC**: CC-NIC Fig. 15 (56 SPR cores, 64 B): TX buffers retained by the application → 20 % throughput
  reduction (§5.4).
- **F-REUSE** (MRV-116, `part3_h2c`, `cfg=stack_0`): one core rewriting a single 64 B buffer, one-way host→card
  174.84 M/s (mean of 3; 97.7 % of 179.02).
- **F-RETAIN** (MRV-116, `part1_w64`, `fill_seq_64k` → `fill_seq_128m`, closed loop W=64): 47.52 → 47.10 M/s
  (−0.87 %, said "less than one percent", shown "−0.9%"); pool 64 KiB → 128 MiB = 2,048× ("2,000×"); host LLC 60 MiB.
- **F-IOMMU**: every run with `intel_iommu=on,sm_on`; CC-NIC §6 expects IOMMU translation to apply to coherent devices
  (future work); CXL-NIC §8 as above.

## Real hardware (C07–C09)
- **F-BRIDGE** (MRV-75, `part1_xray`, `wc4_w64`): inbound writes serialized at 76.39 ns/message = 13.09 M/s
  ("~76 ns", "~13 M/s"); host-side queues and card L3 occupancy nearly empty during the stall; MRV-112 stride sweep
  rules out cache-line/page effects (per-cell demand polling triggers the serialization).
- **F-CREDIT** (MRV-123, `open_echo`, 1 card core, 20 M/s Poisson): tot p99 44.457 µs without a credit cap → 7.0105 µs
  with credit cap 48, release batch 32 (wire p99 3.23 µs); unshaped host CHA backlog 48.5–248.4 entries → 1.4–3.1 with
  credits (MRV-115). Push alone 175.2 M/s (F-FENCE).
- **F-CREDIT-LIMIT** (MRV-115/123/150): host-only credits cannot hold end-to-end p99 ≤ 2 µs at 20 M/s (best 7.01 µs);
  demand-gated card backoff is the open item (MRV-143).
- **F-BULK** (MRV-117, `part3_cpu`, 4 KiB @ 1.0 M/s): host CPU share full-size push 46.60 % vs DirectPush (NIC DPI DMA
  for the payload) 16.32 %; size split ≤ 256 B inline, ≥ 512 B by DMA (MRV-127 `dma_threshold=512`). A 4 KiB payload
  = 64 lines of 64 B.
- **F-HOL** (MRV-127, `p3_8m_2p5m_shr64` vs `p3_8m_2p5m_spl32`): 64 B p99 under saturating 4 KiB traffic 40,500 µs
  ("40.5 ms") with one shared window vs 2.755 µs ("2.75 µs") with separate windows (32, 32) + line cap 32.
- **F-CPU** (MRV-166, fair-full SDP vs DirectPush, host cycles per packet, B=1): 64 B 731 → 474 (1.54×),
  1 KiB 956 → 462 (2.07×), 4 KiB 1011 → 508 (1.99×) → "1.5–2.1×". Attribution (MRV-166/167): at 1–4 KiB much of the
  gap is DPDK software-stack overhead; at 64 B the saving is the interface itself (no TX DMA read, immediate reuse).
- **F-BURST** (MRV-158, `part2_backlog`, 128 MiB bursts at 100 M/s, scheme A 128 MiB host ring vs scheme B 256 KiB host
  ring + NIC-held backlog): host LLC read-miss share 91.35 % → 0.013 % ("91.4% → 0%"); DDIO hit 16.3 % → 100 %;
  host memory-controller traffic 971.52 → 10.69 MiB (−98.9 %); ≤ 32 MiB bursts stay in the card's 48 MiB L3
  (≈ 0 DRAM system-wide), larger bursts go to the card's own DDR5; no backlog W=1 mean 0.8868 → 0.8928 µs (+6 ns).

## End to end (C10)
- **F-E2E** (MRV-133, `part1_decomp`, same session): two hosts over real 100GbE, 64 B W=1 p50: SDP 10.205 µs,
  DirectPush 5.955 µs (−4.25 µs, −41.6 %, said "forty-two percent"); card-only echo (no host) 4.275 µs → host–NIC part
  5.93 µs vs 1.68 µs.
- **F-1CORE** (MRV-155 phase 2, `runs_phase2.jsonl`, measured at mercury's PHY, strictly 1 host core): 64 B UDP payloads
  (110 B L2) 99.764 Gbps / 95.93 Mpps vs best SDP (`ism_enable=1`) 52.62 Gbps / 50.60 Mpps; bare 64 B frames
  99.23 Mpps vs SDP 50.27 Mpps (1.97×), at the card's default single-scheduler TX ceiling 99.77 Mpps (`s0_raw64`).

## Scope and positioning (C11)
- **F-SCOPE**: non-coherent PCIe cannot do host–NIC atomics on a shared allocator (CC-NIC §3.4) or fine-grained shared
  mutable state (paper outline §6.2).
- **F-RELATED**: Liew et al. (HotOS '25, ASPLOS '26) propose PCIe/ISA/root-complex extensions for ordered MMIO datapaths;
  we use unmodified commodity hardware with receiver-side ordering (paper outline §6.1).
- **F-THESIS**: paper outline abstract/§1 (MRV-113, MRV-120): the benefit comes from the interface (push into the
  reader's cache, local polling, credits, NIC DMA for bulk), not from the coherent interconnect.
