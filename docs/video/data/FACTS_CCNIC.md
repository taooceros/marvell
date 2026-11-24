# Facts ledger — `CcnicPaperStory` ("CC-NIC does not need cache coherence")

Every number said or shown in the video, keyed by the `fact_src` ids used in `tts/ccnic_script_data.py`.
Our measurements were recomputed from the raw logs (`logs/<issue>-<date>/runs*.jsonl`) on 2026-11-24 by an
independent check (all 14 items OK; integrity fields lost = bad = torn = 0 in every run used); F-E2E-FLOOR and
F-E2E-PEEK were added the same day from `logs/mrv154-2026-11-23/runs.jsonl` and `logs/mrv161-2026-11-24/runs.jsonl`
(median of 3 reps; every run used is verified with lost = bad = torn = 0). Paper numbers were
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
  match (and its payload checks out). Messages/frames verified with torn = 0 across the CC-NIC experiments ≈ 35.2 billion
  (≈ 13.2 billion without the one-way bulk datasets mrv89, mrv76, mrv155 phase 2) → said as "more than 2.5 billion"
  (the paper's figure) and "not one was read torn" (a part still landing is waited for, never consumed).
- **F-ORDER-ATOMIC**: MOVDIR64B is one 64-byte direct store with 64-byte write atomicity, not cached and weakly ordered
  with other stores (Intel SDM, MOVDIR64B; `testbed/crates/testbed/src/mmio.rs`, `movdir64b` doc). The receiver still
  checks every cell: no torn cell in 600 M open-loop cells (MRV-22) is a measurement, not a guarantee
  (`~/dev/datapath-kb/docs/report/x86-mmio/001.movdir64b-ordering.md`).
- **F-ORDER-PARTS**: NIC replies through a write-combining window are normally one gathered 64 B write; "when gathering
  fails the pieces land in any order, which the host's every-part check absorbs" (`inline_reflector.rs`,
  `StoreReplies`); `inline.rs` `RingReplies` checks every part (sequence number at both ends, tag, payload) and retries
  a reply that does not match yet, reporting it torn only after `TAIL_WAIT_US`. Multi-line pushes (≤ 256 B inline;
  MRV-117/127 `mrv127_host.rs` `send_one`) write the middle lines, then the tail line, then the header line, each one
  MOVDIR64B with no fence; the receiver takes the message only when the header's head/tail sequence numbers, the tail
  line's sequence number and the lines in between check out (`mrv127_card.rs`, `partial`; host side `late`), e.g.
  MRV-117 `directpush` 256 B W=16: 13 replies first seen incomplete, 0 torn.
- **F-ORDER-SEQ**: unfenced MOVDIR64B to different lines can arrive out of order (datapath-kb `x86-mmio/001`, DSA portals
  on saturn: two adjacent lines inverted in 200/200 trials); every receiver reads its ring strictly in sequence
  (`inline.rs` `RingReplies`; `mrv127_card.rs` polls `bar_pos` in order), so a cell that lands early waits in its slot.
  The numbers 42/43 on screen are schematic.
- **F-ORDER-COH**: CC-NIC §3.2: "The descriptor metadata includes a ready flag, which the host sets after other
  descriptor fields are written. Once the flag is set, the NIC receives the signal and the descriptor content in one
  access." Background (standard): coherence keeps the copies of one cache line consistent; the order in which writes to
  different lines become visible is the memory-consistency model's job (Sorin, Hill & Wood, *A Primer on Memory
  Consistency and Cache Coherence*).
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
- **F-ORDER-DMA**: host (MRV-117/127 `mrv127_host.rs` `send_one`, `OP_DMA`): one `sfence()` before the 64 B command per
  message, whose payload was written with ordinary stores; without it a MOVDIR64B can overtake older ordinary stores and
  the device reads stale data (datapath-kb `x86-mmio/001`, DSA on saturn: up to 4.2–5.2 % stale under memory load, 0 in
  268 M with SFENCE). NIC (`mrv127_card.rs`): the reply payload, then the 64 B completion, on the same ToHost DPI queue
  (one requester, posted writes); PCIe does not let a posted write pass an earlier one unless Relaxed Ordering/IDO
  applies (PCIe Base Spec ordering rules). The host polls only the completion, then checks the payload's first and last
  words: a completion seen before its payload (`late`) = 0 and torn = 0 in every bulk-only run (MRV-117 `directpush`
  1/4/8 KiB W=1/16 and `part3_cpu` 4 KiB; MRV-127 `p1_4k_*`, `p2_512_w32_t512_dma_rand`, `p4_aw*_0m_*`; MRV-166
  `directpush` 1/4 KiB).
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
- **F-E2E-FLOOR** (MRV-161, `logs/mrv161-2026-11-24/runs.jsonl`, group `card_echo`, 64 B W=1, 3 reps each, verified,
  lost = bad = torn = 0): the card answers mercury's requests itself, the host never takes part. Median of the per-rep
  p50 at mercury: stock receive polling 4.255 µs, `--cqe-peek` 4.315 µs, exploratory `--cqe-direct` 3.795 µs → said
  "three point eight to four point three microseconds", shown "3.8–4.3 µs", "the floor of the cable and the Ethernet
  engines" (mercury's ConnectX-5 and its stack, the 3 m cable, the CN106's Ethernet engine with its receive poll).
  Matches MRV-133's 4.275 µs (F-E2E). Not used: MRV-154's 4.625 µs (`card_echo_64_w1`), measured with the client
  that was not fixed yet, whose 60 ms sleep left the clocks cold (+350 ns against MRV-133, per MRV-154).
- **F-E2E-PEEK** (MRV-161 `dual_closed_64`, 64 B W=1, 3 reps each, verified, lost = bad = torn = 0; a later session
  than MRV-133, so the row is tagged "LATER RUN"): the stock `rte_eth_rx_burst()` issues the atomic
  `LDADDA NIX_LF_CQ_OP_STATUS` on every poll, 578.51 ns even when the queue is empty (`cqe_probe_bare`, median of 3);
  MRV-154 found 2.01 such empty polls per request while the host's reply is pending (`--bar4-first`: card-timed
  1.305 → 0.935 µs). With `--cqe-peek` the card first loads the next receive completion entry (CQE) from its own cache
  (NIX0 `cq_ctx->caching = 1`), 4.89 ns when empty, and issues the `LDADDA` only on a hit. Card-timed host–NIC round
  trip (`card.host_rtt_p50_us`, CNTVCT in 10 ns bins): 0.935 µs in all 3 reps (mean 0.942–0.947 µs) vs 1.255 µs with
  stock polling in the same session → "back under a microsecond", shown "0.935 µs" (single host: 0.897 µs, F-RTT).
  End to end at mercury: 5.285 µs (5.285 / 5.285 / 5.295) → "5.29 µs", down from MRV-133's 5.955 µs; the same
  session's stock polling gave 5.545 µs, so 0.26 µs of that drop is the peek itself and the rest differs between
  sessions [inference]. Row 3 is drawn as 4.35 µs (= 5.285 − 0.935) + 0.935 µs; by subtraction with the same
  session's echo (4.315 µs) the host–NIC part is 0.97 µs, also under 1 µs.
  Exploratory arm `--cqe-direct` (`exploratory: true` in the log; skips the `LDADDA` on a hit too): 4.755 µs end to
  end (4.755 / 4.755 / 4.765), host–NIC 0.935 µs → "four point seven six in a controlled exploration", shown in a
  dashed "EXPLORATORY · NO LDADDA" tag. MRV-161 lists four open risks (visibility order of multi-segment packets, a
  deadlock on zero-segment error packets, blindness to `CQ_ERR`, replay of dirty slots), tracked in MRV-170, so it is
  not claimed as a result.
- **F-1CORE** (MRV-155 phase 2, `runs_phase2.jsonl`, measured at mercury's PHY, strictly 1 host core): 64 B UDP payloads
  (110 B L2) 99.764 Gbps / 95.93 Mpps vs best SDP (`ism_enable=1`) 52.62 Gbps / 50.60 Mpps; bare 64 B frames
  99.23 Mpps vs SDP 50.27 Mpps (1.97×), at the card's default TX ceiling 99.77 Mpps (`s0_raw64`). MRV-163 (3 reps, lost =
  bad = torn = 0): per-queue SMQ and every RLIMIT variant stay at ~99.6–100.1 Mpps, so the cap sits upstream of the
  single scheduler [inference] → said as "the card's own transmit path, about a hundred million packets per second".
- **F-E2E-SIZES** (MRV-133 `part1_decomp`, same session as F-E2E, W=1, 3 reps each, all verified, lost = torn = 0;
  median of the per-rep p50): SDP vs DirectPush 64 B 10.205 vs 5.955 µs, 256 B 10.375 vs 6.615, 1 KiB 10.945 vs 9.835,
  4 KiB 15.105 vs 11.935 µs (card-only echo 4.275 / 5.015 / 5.445 / 7.575 µs). Newer sessions (MRV-152/154/161) ran
  DirectPush only (5.2–6.0 µs p50 at 64 B, session-dependent), so MRV-133 stays the latest same-session pair; C10 uses
  MRV-161 only for the echo floor and the host–NIC part (F-E2E-FLOOR, F-E2E-PEEK), shown as a later run.
- **F-E2E-LOAD** (MRV-133 `part3_openloop`, 64 B Poisson, 3 reps each, verified): p99 vs offered load, SDP / DirectPush:
  0.5 M/s 14.315 / 7.825 µs, 1.0 M/s 16.415 / 9.205, 1.5 M/s 20.05 / 11.105, 2.0 M/s 26.25 / 12.615, 2.5 M/s 29.05 /
  16.395; SDP 2.75 M/s 36.05; DirectPush 3.0 M/s 19.095, 3.5 M/s 22.55 µs. Said: at 2 M/s, 12.6 vs 26.3 µs.

## Scope and positioning (C11)
- **F-SCOPE**: non-coherent PCIe cannot do host–NIC atomics on a shared allocator (CC-NIC §3.4) or fine-grained shared
  mutable state (paper outline §6.2).
- **F-RELATED**: Liew et al. (HotOS '25, ASPLOS '26) propose PCIe/ISA/root-complex extensions for ordered MMIO datapaths;
  we use unmodified commodity hardware with receiver-side ordering (paper outline §6.1).
- **F-THESIS**: paper outline abstract/§1 (MRV-113, MRV-120): the benefit comes from the interface (push into the
  reader's cache, local polling, credits, NIC DMA for bulk), not from the coherent interconnect.
