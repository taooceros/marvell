# MRV-258 Verified Facts (`Bar4PollingWalls`)

All numbers below are verified directly from raw JSONL logs in `/home/hongtao/dev/marvell-mrv258/logs/` (zero lost / bad / torn / corrupt across all runs; 100.0% non-multiplexed host & card PMU) and exact Rust/C source files.

## Source Code
- Host hot loop: `mrv245_host.rs:765-776` (`off = (((s - 1) as usize) & ring_mask) * 128`, `movdir64b(bar_lane_ptr.add(off), &cell)`).
- Card strided zero-lock partitioning: `mrv245_card.rs:962-972` (`k_per_lane = cpl / lanes`, `lane = w / k_per_lane`, `lane_w = w % k_per_lane`, `stride = k_per_lane as u64`, `next = lane_w as u64 + 1`).
- Card `u1_ldpq` (zero lookahead): `mrv245_card.rs:710-730`, `mrv252_card.rs:1095-1102` (`2x ldp q` on `s0` only, head `words[0] == next` & tail `words[7] == next` + payload verify).
- Card `u4_ldpq` (4-slot lookahead): `mrv245_card.rs:735-772`, `mrv252_card.rs:1103-1134` (`8x ldp q` across `s0, s1, s2, s3` striding `3 * K` slots ahead).
- Card `adapt-*` trap: `mrv252_card.rs:1156-1215` (on empty probe pauses 50/100/exp ns via `CNTVCT_EL0`; on single `s0` hit flips `in_busy_burst = true` and fires `u4_ldpq` on next iter).
- Linux kernel TAD PMU `exlmn` definition: `linux-marvell/drivers/perf/marvell_cn10k_tad_pmu.c:285-306` (`tad_snp_msh_out_any` 0x14 = `mn` 0x15 + `exlmn` 0x16, where `exlmn` = excluding MN, i.e., regular cache-line snoops emitted by CN106 48 MiB TAD onto the on-chip CHI mesh).
- Standard Linux `MT_NORMAL_NC (0x44)`: `pgprot_writecombine`, `enable_custom_mair=0` (zero `MAIR_EL1` modification).

## Wall 1: Normal-WB (0xFF) vs Historical 161–175 Mpps
- `MRV-245` `cl_wb_raw_k4_w256` (`H=1, K=4, W=256`, closed-loop): **13.53 M/s**, `l1d_refill = 0.99/pkt`, `0x58 = 1.96/pkt`, `EXLMN = 3.04/pkt`, `CHA = 1.4`.
- `MRV-245` `tx_wb_u1_h1_k4` (`H=1, K=4, W=6144`, one-way): **104.24 M/s**, **`CHA = 546.2`** (severe host neighbor impact).
- Historical WB `161–175 Mpps` regimes (zero data-slot frontier polling):
  1. Card silent (`tx_none_h1` / `tx_none_h2`, `MRV-196` / `MRV-245`): **161.7 M/s** (`H=1`, `CHA = 16.7`) / **175.2 M/s** (`H=2`, `tx_none_h2`). Card never reads → 0 L1D/L2 snoops.
  2. Decoupled 128-cell batch doorbell + 1 tail `SFENCE` (`MRV-155` Stage 1 / `MRV-177`): **161.42 Mpps** (one-way verified). Card polls only the separate `prod_idx` doorbell line, never touches in-flight data slots. Closed-loop `K=1, W=256` (`cl_wb_db32_k1_w256`): **66.67 M/s (`CHA = 7.1`)**. Cost: `W=1` RTT `1,096.4 ns` (`cl_wb_db32_k4_w1`) vs `905.5 ns` (`cl_wb_u1_k4_w1`) → **`+191 ns` (`~190 ns`)** latency tax.

## Breakthrough: Standard Linux MT_NORMAL_NC (0x44)
- `MRV-252` `k4_ow_u1_ldpq` (`H=1, K=4, W=6144`): **159.89 M/s**, `CHA = 16.9`, `l1d_refill = 0.00`, `0x58 = 0.00`, `EXLMN = 0.009/pkt`, `DDR5 rd = 0.0000`, `tad_req_occ = 2.72e8`.
- `MRV-245` `tx_nc_u4_h1_k4`: **160.10 M/s**, `CHA = 28.2`, `l1d_refill = 0.00`, `0x58 = 0.00`, `DDR5 rd = 0.0000`.
- `MRV-245` `tx_nc_u4_h2_k4`: **174.10 M/s**, `l1d_refill = 0.00`, `0x58 = 0.00`, `EXLMN = 0.008/pkt`.
- `MRV-252` `k4_w1_u1_ldpq`: **`904.5 ns` p50** (`960.9 ns` p99).

## Wall 2: Closed-Loop + u4_ldpq EXLMN Avalanche
- `MRV-252` `k4_cl_w64/256/512_u4_ldpq`: **17.51 / 17.58 / 17.67 M/s**, `l1d_refill = 0.00`, `0x58 = 0.00`, **`EXLMN = 1.06/pkt`** (`119×` vs one-way `0.009`), `tad_req_occ = 19.09e8` (**`7.0×–7.1×`** vs `2.72e8` one-way), `CHA = 1.8` (`W=256`).
- `MRV-252` `k4_cl_w256/512_adapt-50`: **19.64 / 19.74 M/s**, `EXLMN = 1.05/pkt` (even though TAD total req rate dropped `48%`, from `317.9` to `166.5 M/s`, lower than `static-u1q` `225.7 M/s`!).
- `MRV-245` supply-demand proof (`u4_ldpq, K=2`):
  - `H=2, K=2, W=512` (1 card core/lane): **87.98 M/s** (`EXLMN = 0.022/pkt`, no collapse).
  - `H=1, K=2, W=256` (2 card cores/lane): **17.96 M/s** (`EXLMN = 1.058/pkt`, instant collapse).
- `MRV-245` over-provisioned `K=8` (`u1_ldp`): `H=1, W=256` **16.27 M/s** (`EXLMN = 1.047/pkt`); `H=2, W=512` **22.84 M/s** (`EXLMN = 0.820/pkt`).

## Solution: Zero-Lookahead static-u1q (u1_ldpq) + Matched K + W <= 256
- `MRV-252` `k4_cl_w256_u1_ldpq`: **46.49 M/s**, `EXLMN = 0.326/pkt` (**`3.2×`** reduction vs `u4_ldpq` `1.058`), `CHA = 4.8`.
- `MRV-252` `k4_cl_w512_u1_ldpq`: **61.36 M/s**, `EXLMN = 0.232/pkt`.
- `MRV-245` `cl_nc_u1q_h1_k2_w256`: **75.75 M/s**, `p50 = 3.26 µs`, `EXLMN = 0.069/pkt`, `CHA = 7.7`.
- `MRV-245` `cl_nc_u1_h2_k4_w512`: **119.65 M/s**, `EXLMN = 0.041/pkt`.
- Co-located host `6c STREAM` (`MRV-252`, idle = **47.69 GB/s**):
  - `NC K=4` one-way: **47.46–47.70 GB/s** (**`99.5%–100.0%`**, zero degradation, `CHA = 19.5–26.6`).
  - `NC K=8` over-provisioned one-way: **41.68–42.27 GB/s** (**`-11.4% ~ -12.6%`**, `CHA = 374.2–382.6`).
  - Wide-window (`W=6144`) partial load (`40M / 80M`, `u1_ldpq`): **`CHA = 250.2 / 306.6`** → proves host credit window `W <= 256` and balanced `K` remain necessary.
