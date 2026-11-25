# FACTS_MMIO — numbers allowed on screen in `MmioLatencyBreakdown`

Every number in `tts/mmio_script_data.py` and `src/mmio/*.tsx` comes from this file. Section ids (A1, C7, …) are the
`src` field of each cue. Values were recomputed from the raw JSONL for this video (2026-11-25) unless marked otherwise.

Testbed: host `saturn` (2× Xeon Gold 6438M, Sapphire Rapids, 32 cores per socket), Marvell CN106 card on PCIe Gen5 ×4
(32 GT/s) under root port `0000:98:01.0` on socket 1. Baseline: `cpu40` at 3.90 GHz, mesh (uncore) 2.20 GHz, card
`rclk` 2.00 GHz. Every read is timed by one asm block `lfence; rdtsc; lfence; <LOAD>; lfence; rdtsc` minus the empty
bracket. `W = 1`, batch 1 (one read in flight) unless a cue says otherwise.

## A. Round 1: how long is one read? (`logs/mrv180-2026-11-24/runs.jsonl`, 45/45 runs, n = 3 × 100,000 reads, 0 mismatches)

- A1 Empty bracket (floor) 12.73 ns p50 (28.0 TSC ticks at 2.2 GHz). The TSC moves 2 ticks at a time: every quantile
  sits on a 0.91 ns grid (75/75 quantiles on even ticks).
- A2 Card register `VF0 BAR0 OUT_CNTS` (4 / 8 B): 516.36 ns net p50, 1.000 TLP per read.
- A3 BAR4 uncached (`UC`), card L3 hit, fixed line 0: 4 / 8 / 16 / 32 B all 627.27 ns p50; one 64 B `vmovdqu64 zmm`
  load 634.55 ns. All 1.000 TLP per read (host `iio_rd_txn`).
- A4 Eight 8 B `mov` loads of one line: 8.000 TLP, 5038.18 ns (7.9× the single 64 B load).
- A5 Four legacy (non-VEX) 16 B `movdqu` loads: 5.000 TLP, 3199.09 ns; the card's TAD counted +4.95 requests per
  iteration, so the fifth read really crossed the link. `machine_clears` ≈ 2.0 per iteration. Not yet separated from a
  pure-VEX `4× vmovdqu`, so "a replayed load" is an inference ([INF] on screen).
- A6 4,000-line walk (all CHA × TAD slices): L3 hit 621.82 ns p50; after card-side `dc civac` (DDR5) 715.45 ns (8 B) /
  722.73 ns (64 B) → +93.6 ns p50. Card PMU: `tad_dss_rd` 1.00 per read, `ddr_reads` 2.01 per read (one 128 B L3 line
  refilled by two 64 B DDR5 bursts).
- A7 CXL.mem reads ~170–250 ns: industry reference for Type-3 cacheable device memory, NOT measured here.

## B. First cut: counters and endpoints (`logs/mrv182-2026-11-24/runs.jsonl`, 249/249 runs, PMU 100 % unmultiplexed)

- B1 No public perf event counts 4–32 B uncached reads. The unnamed CHA TOR encoding `umask=0xc83fde01` (IDI opcode
  `PRd`) counts them 1.0000× per read (`UCRdF` `0xc877de01` counts 64 B loads). CHA → `M2PCIe₁ cha_ncs` → `IRP₁
  outbound_ins` → `IIO₁ part0 mem_rd` → `cmpd`: 1 : 1 : 1 : 1 : 1.
- B2 The CHA holds the TOR entry until the completion (`CplD`) returns, so `T_CHA_TOR = occupancy ÷ inserts`
  (Little's law) covers everything below the CHA; `net − T_CHA_TOR` = core + core⇄CHA mesh.
- B3 `cpu40`, card register: net p50 516.36, net mean 517.69, `T_CHA_TOR` 495.56, outside the TOR 21.76 ns. Near core
  `cpu48` (CHA 20): TOR 493.79 ns → the black box below the CHA is 493.8–495.6 ns.
- B4 All 32 socket-1 cores, line 0 (CHA 23): 617.3–627.3 ns, a 10.0 ns spread (22 mesh cycles). Six cores release the
  TOR ~19 cycles early: ΔTOR −8.6…−9.3 ns, outside the TOR +8.7…+9.6 ns, totals conserved within 0.04–0.17 ns.
- B5 K = 1 / 2 / 4 / 8 cores reading distinct lines: 1.54 → 3.08 → 6.18 → 12.23 Mops/s (8.0×), per-core p50
  617.3–621.5 ns: no queueing. One core, 8 unfenced UC reads: 621.7 ns per read, `W_eff = 1.00`.
- B6 Endpoint differences: BAR4 L3 walk − register +105.5 ns p50 (+105.2 mean, +104.0 TOR); DDR5 − L3 +92.7 ns p50 /
  +101.6 ns mean (+101.2 TOR).
- B7 Card causality check: L3-miss fill-buffer occupancy ΔIFB = 161.6 cycles (160.3 with the L3 background). At 1.00 GHz
  that is 161.6 ns, more than the host's whole +101.6 ns: impossible. At `rclk` 2.00 GHz: 80.8 ns. Card-core local L3
  pointer chase: 10.1 ns.
- B8 Config reads through sysfs, `99:00.0 − 98:01.0` = +209.1 ns p50, mixed with four kernel/hardware confounders, so
  round 1 did not subtract it.

## C. Second cut: root port, clock sweeps and three tricks (`logs/mrv185-2026-11-24/{runs,runs_tricks}.jsonl`, 207 + 201 = 408 runs, 0 errors)

- C1 Six one-time `chown` grants, no root shell: root port `0000:98:01.0/resource0` mapped `O_RDONLY` / `PROT_READ`,
  one offset `+0x0`; uncore and `cpu40` frequency sysfs; `resource4_wc`.
- C2 Endpoints (net mean / p50 / TOR, ns): root port BAR0 8 B 205.45 / 203.64 / 184.44; card register 517.52 / 516.36 /
  494.99; BAR4 L3 walk 622.67 / 620.91 / 599.23; DDR5 walk 724.29 / 714.55 / 701.08; line 0 628.45 / 626.36.
- C3 Reviewer caveat: a config read of `98:01.0` never leaves the root port yet shows the same M2PCIe₁ / IRP₁ / IIO₁
  1 : 1 signature as a card read, so matching counters do not prove a shared path. A root-port BAR0 read turns around
  inside the root port.
- C4 Trick 1A, same-CHA gate (outside-TOR time): CHA 28 root port 21.2 vs BAR4 line 13 22.3 ns; CHA 20 register 22.7 vs
  BAR4 line 9 22.3 ns. Same CHA 20: BAR4 L3 − register = +99.2 ns mean / +100.0 ns p50.
- C5 Trick 1B, same address, K = 1…8 cores: root port 4.37 → 5.66 M/s (1.30×), register 1.85 → 2.04 M/s (1.10×); both
  saturate at 1 / TOR → same-address reads serialize at the CHA, not in the root port.
- C6 Register − root port = +312.1 ns mean / +312.7 ns p50 (isolated, `W = 1`): host PCIe TL/DLL/PHY + Gen5 ×4 link +
  PEM0 + PEM0 → SDP CSR − root-port register access ([INF] composition; the run measures the path difference).
- C7 Mesh sweep 0.8 / 1.1 / 1.4 / 1.8 / 2.2 GHz (socket 1): CHA and M2PCIe₁ follow it; IRP₁ and IIO₁ do not. TOR fits
  (recomputed): root port 125.19 + 128.98 / f, register 438.65 + 123.67 / f, L3 541.74 + 126.51 / f, DDR5 642.20 +
  127.93 / f; RMSE 0.36–0.65 ns (R² 0.9997–0.9999, carbon-4). Slopes 123.7–129.0 mesh cycles = 56.2–58.6 ns at 2.2 GHz
  (CHA ⇄ M2PCIe₁ inside the TOR). Root-port intercept 125.2 ns = IRP₁ + IIO₁ + root port.
- C8 Core sweep 1.5 / 2.2 / 3.0 / 3.9 GHz (APERF/MPERF checked). Outside-TOR fits on the `floor_mean` basis: root port
  11.14 + 41.10 / f, register 12.17 + 41.16 / f, L3 12.90 + 42.64 / f → core share 41.1–42.6 cycles = 10.5–10.9 ns at
  3.9 GHz. Mesh-sweep outside-TOR slopes 24.6–28.1 mesh cycles = 11.2–12.8 ns (core ⇄ CHA mesh). The two sweeps agree
  within 0.73 ns. (The `floor_p50` basis gives a 1/f_core intercept of 10.3–12.1 ns with the 0.86 ns tilt of D2; the
  video shows the floor-independent 11.2–12.8 ns.)
- C9 Trick 2, per-hop residency at `W = 1`: `M2PCIe₁ rxc_ne.cha_ncs ÷ ins` = 2.00 counts = 1 mesh cycle (the event
  counts twice per cycle) = 0.45 ns; `IRP₁ txc_bl_ncs` = 1.00 cycle = 1.00 ns; `IRP₁ 0x0c` = 0. The hops are
  pass-through: nothing waits in them.
- C10 Trick 1C, interference: one core reading the root port (4.42 M/s) makes `cpu40`'s register read 517.3 → 494.6 ns p50
  (−22.7), TOR 495.6 → 477.4 ns (−18.1); with 4 register readers + 1 root-port reader TOR reaches 459.8 ns. The root-port
  read itself changes +0.0…+0.9 ns. Alone, a register read starts every ~541 ns (~400 ns of idle IO stack); root-port
  reads every ~233 ns ([INF] fine-grained idle gating in IIO₁ / root port).
- C11 Warm (IO stack kept busy) register − root port = +290.9 ns p50 (494.55 − 203.64) / +295.0 ns mean (500.44 −
  205.45). The isolated +312.1 ns includes 18–23 ns of wake-up that only the register path pays.
- C12 Trick 3, `pread(fd, buf, 4, 64)`: a normal user may read only 64 bytes of config space, so it returns 0 bytes
  after the full syscall, kernfs and `pci_config_pm_runtime_get/put` path; M2PCIe₁ / IRP₁ / IIO₁ = 0 on all 7 devices.
  T(64) 227.3–229.1 ns, root port +17.3 ns (244.5), VF0 +20.9 ns (248.2): runtime-PM path. H = T(0) − T(64):
  H(99:00.0) 679.1 vs H(99:02.0) 677.3 ns, so the 19.1 ns PF/VF gap was kernel. ΔH = H(99:00.0) − H(98:01.0) = +221.8 ns
  mean / +223.6 ns p50 (cross-link config read, pure hardware only as a device difference: H itself still holds the
  shared `pci_lock` + `CF8` constant). ~90 ns below register − root port: PEM0 → SDP CSR + root-port path depth +
  wake-up ([INF]).
- C13 WC mapping: `resource4_wc` 4 × 16 B `vmovntdqa` = 4.0000 TLP, 2484.6 ns p50, same as UC. `octep_main.c:1482–1485`
  `ioremap()`s the whole 64 MiB BAR4 at driver load (PAT `UC-`); a later user `mmap` of `resource4_wc` is silently
  rewritten to `UC-` (`reserve_pfn_range`, `strict_prot = 0`): no error, no dmesg. One 64 B `vmovntdqa zmm` load:
  1.0000 TLP (`UCRdF`), 628.2 ns, 3.95× faster than four 16 B loads.

## D. carbon-4 corrections (review of round 2)

- D1 The IO clock was first reported as 0.96–0.99 GHz. After each timed loop the harness read 65 CHA counters first
  (260–500 µs with no reads in flight), the idle IO clock gated to ~0.03 GHz and diluted `clk ÷ running time`
  (9.3 ms window 0.962 GHz, 21.6 ms 0.988 GHz, 250 ms 0.999 GHz). In-loop `iio_clk ÷ wall_s` = 1.0001–1.0020 GHz
  (carbon-4; per-group medians recomputed here 0.9994–1.0020 GHz). Shown on screen as 1.00 GHz.
- D2 `floor_p50` (on the 2-tick grid) − `floor_mean` flips with the core clock (−0.25 → +0.42 ns): a −0.66 ns tilt
  across the core sweep, slope +1.7 cycles, intercept −0.86 ns. On the `floor_mean` basis the worst two-sweep mismatch
  drops from 1.16 to 0.73 ns.
- D3 Wake-up asymmetry 18–23 ns and warm register − root port, see C10–C11.

## E. Six-stage verdict (8 B read into card L3, means unless noted)

| Stage | Where | Value | Second method |
| --- | --- | --- | --- |
| 1 | core (3.9 GHz) | 10.5–10.9 ns | mesh-sweep fixed part (C8) |
| 2 | core ⇄ CHA mesh, outside TOR | 11.2–12.8 ns | core-sweep intercept (C8) |
| 3 | CHA ⇄ M2PCIe₁ mesh, inside TOR | 56.2–58.6 ns | per-hop residency (C9) |
| 4 | IRP₁ + IIO₁ (1 GHz) + root port | 125.2 ns | root-port total 205.5 ns (C2) |
| 5 | host PCIe layers + link + PEM0 + SDP path | +290.9–312.7 ns | ΔH config read +221.8 ns (C12) |
| 6 | BAR4 window + IOBN + L3 (DDR5 miss) | +103.1–105.5 ns (+92.7–101.6 ns) | card PMU (A6, B7) |

Totals: root port 205.5 ns, register 517.5 ns, L3 (4,000-line walk) 622.7 ns mean (line 0: 627.3 ns p50), DDR5 724.3 ns.

## F. DirectPush (earlier work on the same testbed)

- F1 Posted writes both ways (host `MOVDIR64B` push, card pushes the reply into host cache, each side polls its own
  cache): round trip ~885–897 ns, versus one MMIO read ~610–620 ns. "Most of it is the same physical round trip" is
  an inference ([INF]).
