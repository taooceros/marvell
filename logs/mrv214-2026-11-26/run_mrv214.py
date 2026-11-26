#!/usr/bin/python3
"""MRV-214 (MRV-203 E6) Design-Space Sensitivity Sweep Experiment Harness.

Three single-variable sweeps on the standard SDP + On-Path CN106 elastic-FIFO
+ NO-SFENCE BAR4 MOVDIR64B credit-ring stack (`mercury -> 100GbE -> CN106 -> saturn`):

  1. Sweep 1 (`R_eff` & Host Working-Set Span):
     - `1a_reff_left`      : Single OQ (`rx_desc=512`, `pool=768`, `cache=128`, `B=8`, `1500 B`),
                             `R_eff = 16 -> 256` slots (`32 KiB -> 512 KiB` @ 2 KiB/slot) + `rx_desc=256` anchor,
                             pinpointing the left BDP + credit-RTT starvation knee.
     - `1b_reff_single_oq` : Single OQ up to hardware ceiling `OTX_EP_MAX_OQ_DESCRIPTORS = 8192`
                             (`rx_desc = 512 -> 8192`, `1 MiB -> 16 MiB`), pinpointing the right ~8 MiB 2-way DDIO cliff.
     - `1c_span_large_pool`: Single OQ extended working-set span via `cache=0` FIFO mempool
                             (`6, 8, 10, 12, 32, 64, 128 MiB`) + Pitfall-2 dead-writeback control
                             (`R_eff=256, rx_desc=512` with `pool=65536 (128 MiB)` vs `pool=768 (1.5 MiB)`).
     - `1d_span_8q`        : Multi-queue aggregation (`8` card cores -> `8` SDP OQs -> `8` host cores `cpu40..47`),
                             `8Q x (256..8192) = 4 MiB -> 128 MiB` span.

  2. Sweep 2 (NO-SFENCE Credit Return Batch Granularity `B`):
     - `2a_batch_overhead` : `B = 8 / 16 / 32 / 64 / 128` + `credit=off` + `B=32+SFENCE` control at both
                             `64 B` (high-pps 4C->1H) and `1500 B` (100GbE 1C->1H with `app_work` accounting).
     - `2b_batch_starve`   : Refill starvation threshold (`B <= R_eff / 2`) at `R_eff = 128` (`B = 8..128`)
                             and `R_eff = 64` (`B = 8..96`, with `credit_flush_us=50` deadlock guard).

  3. Sweep 3 (On-Path CN106 Card-Core Scaling `1 / 2 / 4 / 8` Cores):
     - `1 / 2 / 4 / 8` CN106 ARM cores across `64 B (uc64)`, `254 B (ud, plen=184)`, and `1500 B (udp, plen=1454)`
       for both `bar4-nofence` (`R_eff=256/q, B=32`) and `credit=off` direct forwarding.

Strict audit & measurement guarantees:
  - n = 3 interleaved replications across conditions.
  - Append-only `attempts.jsonl` (zero silent retries; integrity errors NEVER retried; only
    shared-host Socket-1 clock/mesh contention may wait and re-measure transparently).
  - Two-window measurement: atomic steady-state `SNAP` delta strictly inside `[+100 ms .. -50 ms]`
    for zero drain-tail bias, plus full-run post-drain conservation audit (`c2h_lost == 0`,
    `bad == torn == wire_bad == wire_torn == credit_torn == alloc_fails == 0`).
  - 100.000% non-multiplexed Host (per-worker Core + Socket-1 CHA/IMC/IIO1/M2PCIe1) & Card (TAD + 6x DDR5) PMUs.
  - `--dry-run` and `--self-test` run 100% offline with zero hardware access.
"""

import argparse
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv214")
LOG_DIR = ROOT / "logs/mrv214-2026-11-26"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = str(ROOT / "testbed/target/release/mrv214_host")
CARD_BIN_LOCAL = str(ROOT / "testbed/target/aarch64-unknown-linux-gnu/release/mrv214_card")
CARD_BIN = "/root/bench/mrv214_card"
MERC_SRC_DIR = ROOT / "testbed/mercury-client"
MERC_BIN = "/tmp/mrv214/mrv214_client"

CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]

CARD_CTRL_PORT = 7555
HOST_CTRL_PORT = 7556
MERC_CTRL_PORT = 7557

REPS = 3
WARMUP_SECS = 0.4
MEAS_SECS = 1.5
REF_HZ = 2.2e9
SOCKET1_UNCORE_CPU = 32

OTX_EP_MIN_SMALL_OQ = 256
OTX_EP_MAX_OQ = 8192
OTX_EP_WMARK_MAX = 256
DROQ_REFILL_THRESHOLD = 64


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expected_wmark(rx_desc: int) -> int:
    return min(OTX_EP_WMARK_MAX, rx_desc // 4) if rx_desc < 2048 else OTX_EP_WMARK_MAX


def max_safe_r_eff(rx_desc: int) -> int:
    return max(0, rx_desc - expected_wmark(rx_desc) - DROQ_REFILL_THRESHOLD)


def l2_bytes_for(fk: str, plen: int) -> int:
    if fk == "uc64":
        return 64 if plen == 0 else 62 + plen
    if fk == "ud":
        return 70 + plen
    if fk == "udp":
        return 46 + plen
    raise ValueError(f"unknown frame_kind={fk}")


def validate_spec(sp: dict) -> list:
    """Validate hardware invariants of a single experiment spec (100% offline)."""
    errs = []
    nc = sp["nc"]
    hc = sp["hc"]
    rxd = sp["rx_desc"]
    pool = sp["pool_size"]
    cache = sp["pool_cache"]
    r_eff = sp["r_eff"]
    batch = sp["credit_batch"]
    credit = sp["credit"]
    fk = sp["fk"]
    plen = sp["plen"]

    if not (1 <= nc <= 8):
        errs.append(f"nc={nc} out of [1..8]")
    if not (1 <= hc <= 8) or hc > nc:
        errs.append(f"hc={hc} invalid for nc={nc}")
    if rxd < OTX_EP_MIN_SMALL_OQ or rxd > OTX_EP_MAX_OQ or (rxd & (rxd - 1)) != 0:
        errs.append(f"rx_desc={rxd} must be power-of-two in [{OTX_EP_MIN_SMALL_OQ}..{OTX_EP_MAX_OQ}]")
    min_pool = rxd + cache + DROQ_REFILL_THRESHOLD + 64
    if pool < min_pool:
        errs.append(f"pool_size={pool} < min_needed={min_pool}")
    if credit != "off":
        safe_max = max_safe_r_eff(rxd)
        if r_eff <= 0 or r_eff > safe_max:
            errs.append(f"r_eff={r_eff} exceeds max_safe_r_eff({rxd})={safe_max} (Pitfall 1)")
        if batch <= 0:
            errs.append(f"credit_batch={batch} must be > 0")
        if batch > r_eff and sp.get("credit_flush_us", 0) <= 0:
            errs.append(f"credit_batch={batch} > r_eff={r_eff} without credit_flush_us deadlock guard")
    if fk not in ("uc64", "ud", "udp"):
        errs.append(f"unknown fk={fk}")
    if fk == "uc64" and plen != 0:
        errs.append(f"uc64 requires plen=0 (got {plen})")
    if fk in ("ud", "udp") and plen < 32:
        errs.append(f"{fk} requires plen>=32 (got {plen})")
    if fk == "udp" and l2_bytes_for(fk, plen) > 1518:
        errs.append(f"udp L2={l2_bytes_for(fk, plen)} exceeds 1518 B MTU")
    if fk == "ud" and plen > 1024:
        errs.append(f"ud plen={plen} exceeds 1024 B RoCEv2 IB_MTU")
    if sp.get("mbatch", 0) <= 0:
        errs.append(f"mbatch={sp.get('mbatch')} must be > 0")
    return errs


# ---------------------------------------------------------------------------
# Experiment Matrix Builders (E6: Sweeps 1, 2, 3)
# ---------------------------------------------------------------------------
def build_matrix(mode: str, frame_1500: str = "udp1500") -> list:
    """Construct the single-variable experiment matrix for MRV-214 (E6)."""
    if frame_1500 == "udp1500":
        big_fk, big_plen, big_lbl = "udp", 1454, "1500b"
    elif frame_1500 == "ud1094":
        big_fk, big_plen, big_lbl = "ud", 1024, "1094b"
    else:
        raise ValueError(f"invalid frame_1500={frame_1500}")

    specs = []

    def add(**kw):
        sp = {
            "part": kw["part"],
            "cfg": kw["cfg"],
            "nc": kw.get("nc", 1),
            "hc": kw.get("hc", 1),
            "fk": kw.get("fk", big_fk),
            "plen": kw.get("plen", big_plen),
            "rx_desc": kw.get("rx_desc", 512),
            "pool_size": kw.get("pool_size", 768),
            "pool_cache": kw.get("pool_cache", 128),
            "r_eff": kw.get("r_eff", 256),
            "r_eff_bytes": kw.get("r_eff_bytes", 0),
            "credit": kw.get("credit", "bar4-nofence"),
            "credit_batch": kw.get("credit_batch", 32),
            "sfence": kw.get("sfence", False),
            "align_refill": kw.get("align_refill", False),
            "credit_flush_us": kw.get("credit_flush_us", 50),
            "fifo_cap": kw.get("fifo_cap", 8192),
            "touch_stride": kw.get("touch_stride", 0),
            "app_work": kw.get("app_work", False),
            "track_iova": kw.get("track_iova", kw["part"].startswith("s1")),
            "rx_poll_mem": kw.get("rx_poll_mem", False),
            "mthreads": kw.get("mthreads", 16),
            "mbatch": kw.get("mbatch", 44 if kw.get("fk", big_fk) == "udp" else 64),
            "gso": kw.get("gso", True),
            "rate_mpps": kw.get("rate_mpps", 0.0),
        }
        specs.append(sp)

    if mode in ("sweep1", "all"):
        # 1a: Left BDP starvation knee on 1 OQ (`R_eff = 16..256` slots, fixed `B=8`, `rx_desc=512`, `pool=768`)
        for r_eff in (16, 24, 32, 48, 64, 96, 128, 192, 256):
            add(
                part="s1a_reff_left",
                cfg=f"s1a_{big_lbl}_r{r_eff}_oq512_b8",
                nc=1, hc=1, rx_desc=512, pool_size=768, pool_cache=128,
                r_eff=r_eff, credit_batch=8,
            )
        # Tight minimum hardware ring anchor (`rx_desc=256`, `512 KiB` OQ)
        add(
            part="s1a_reff_left",
            cfg=f"s1a_{big_lbl}_r128_oq256_b8",
            nc=1, hc=1, rx_desc=256, pool_size=512, pool_cache=128,
            r_eff=128, credit_batch=8,
        )

        # 1b: Right ~8 MiB 2-way DDIO overflow knee on 1 OQ up to OTX_EP_MAX_OQ_DESCRIPTORS=8192 (16 MiB)
        for rxd, r_eff, pool, cache in (
            (512,  256,  768,  128),
            (1024, 512,  1280, 128),
            (2048, 1024, 2304, 128),
            (4096, 2048, 4352, 128),
            (8192, 4096, 8448, 128),
            (8192, 7680, 8448, 0),
        ):
            add(
                part="s1b_reff_single_oq",
                cfg=f"s1b_{big_lbl}_oq{rxd}_r{r_eff}_b8",
                nc=1, hc=1, rx_desc=rxd, pool_size=pool, pool_cache=cache,
                r_eff=r_eff, credit_batch=8, touch_stride=64,
            )

        # 1c: Fine ~8 MiB cliff + extended 32/64/128 MiB working-set span via `cache=0` FIFO mempool
        for rxd, r_eff, pool, tag in (
            (2048, 1024, 3072,  "6m"),
            (2048, 1024, 4096,  "8m"),
            (2048, 1024, 5120,  "10m"),
            (2048, 1024, 6144,  "12m"),
            (8192, 4096, 16384, "32m"),
            (8192, 4096, 32768, "64m"),
            (8192, 4096, 65536, "128m"),
        ):
            add(
                part="s1c_span_large_pool",
                cfg=f"s1c_{big_lbl}_fifo_{tag}_oq{rxd}_r{r_eff}",
                nc=1, hc=1, rx_desc=rxd, pool_size=pool, pool_cache=0,
                r_eff=r_eff, credit_batch=8, touch_stride=64,
            )
        # Pitfall-2 dead-writeback control: tight R_eff=256 & rx_desc=512 but huge 128 MiB FIFO mempool!
        add(
            part="s1c_span_large_pool",
            cfg=f"s1c_{big_lbl}_pitfall2_r256_pool128m",
            nc=1, hc=1, rx_desc=512, pool_size=65536, pool_cache=0,
            r_eff=256, credit_batch=8, touch_stride=64,
        )

        # 1d: Multi-queue aggregation span (`8Q x 256..8192 = 4 MiB -> 128 MiB`, 8 card cores -> 8 host cores)
        for rxd, r_eff, tag in (
            (256,  128,  "4m"),
            (512,  256,  "8m"),
            (1024, 512,  "16m"),
            (2048, 1024, "32m"),
            (4096, 2048, "64m"),
            (8192, 4096, "128m"),
        ):
            add(
                part="s1d_span_8q",
                cfg=f"s1d_{big_lbl}_8q_oq{rxd}_{tag}",
                nc=8, hc=8, rx_desc=rxd, pool_size=rxd + 256, pool_cache=128,
                r_eff=r_eff, credit_batch=16, touch_stride=64, mthreads=16,
            )

    if mode in ("sweep2", "all"):
        # 2a-1: Signaling CPU & reverse PCIe bandwidth at high-pps 64 B (4 card cores -> 1 host core cpu40)
        add(
            part="s2a_batch_64b",
            cfg="s2a_uc64_4c1h_off",
            nc=4, hc=1, fk="uc64", plen=0, rx_desc=512, pool_size=768, pool_cache=128,
            r_eff=256, credit="off", credit_batch=32, mthreads=16, mbatch=64,
        )
        for b in (8, 16, 32, 64, 128):
            add(
                part="s2a_batch_64b",
                cfg=f"s2a_uc64_4c1h_nofence_b{b}",
                nc=4, hc=1, fk="uc64", plen=0, rx_desc=512, pool_size=768, pool_cache=128,
                r_eff=256, credit="bar4-nofence", credit_batch=b, mthreads=16, mbatch=64,
            )
        add(
            part="s2a_batch_64b",
            cfg="s2a_uc64_4c1h_sfence_b32",
            nc=4, hc=1, fk="uc64", plen=0, rx_desc=512, pool_size=768, pool_cache=128,
            r_eff=256, credit="bar4-sfence", credit_batch=32, sfence=True, mthreads=16, mbatch=64,
        )

        # 2a-2: Signaling CPU & reverse PCIe bandwidth at 1500 B 100GbE (1C->1H with app_work accounting)
        add(
            part="s2a_batch_1500b",
            cfg=f"s2a_{big_lbl}_1c1h_off",
            nc=1, hc=1, rx_desc=512, pool_size=768, pool_cache=128,
            r_eff=256, credit="off", credit_batch=32, app_work=True,
        )
        for b in (8, 16, 32, 64, 128):
            add(
                part="s2a_batch_1500b",
                cfg=f"s2a_{big_lbl}_1c1h_nofence_b{b}",
                nc=1, hc=1, rx_desc=512, pool_size=768, pool_cache=128,
                r_eff=256, credit="bar4-nofence", credit_batch=b, app_work=True,
            )
        add(
            part="s2a_batch_1500b",
            cfg=f"s2a_{big_lbl}_1c1h_sfence_b32",
            nc=1, hc=1, rx_desc=512, pool_size=768, pool_cache=128,
            r_eff=256, credit="bar4-sfence", credit_batch=32, sfence=True, app_work=True,
        )

        # 2b: Refill starvation threshold (`B <= R_eff / 2`) at `R_eff = 128` and `R_eff = 64`
        for b in (8, 16, 32, 64, 96, 128):
            add(
                part="s2b_batch_starve",
                cfg=f"s2b_{big_lbl}_r128_b{b}",
                nc=1, hc=1, rx_desc=512, pool_size=768, pool_cache=128,
                r_eff=128, credit="bar4-nofence", credit_batch=b,
            )
        for b in (8, 16, 32, 48, 64, 96):
            add(
                part="s2b_batch_starve",
                cfg=f"s2b_{big_lbl}_r64_b{b}",
                nc=1, hc=1, rx_desc=512, pool_size=768, pool_cache=128,
                r_eff=64, credit="bar4-nofence", credit_batch=b, credit_flush_us=50,
            )

    if mode in ("sweep3", "all"):
        # 3: On-Path CN106 card-core scaling (`1 / 2 / 4 / 8` ARM cores) at 64 B / 254 B / 1500 B
        tiers = [
            ("uc64", 0,    "64b",  24, 64),
            ("ud",   184,  "254b", 24, 64),
            (big_fk, big_plen, big_lbl, 16, 44 if big_fk == "udp" else 64),
        ]
        for fk, plen, lbl, mthreads, mbatch in tiers:
            for nc in (1, 2, 4, 8):
                # Provision enough host cores so host SDP RX never caps CN106 card-core scaling:
                if fk == "uc64":
                    hc = min(nc, 4)
                elif fk == "ud" and plen <= 256:
                    hc = min(nc, 2)
                else:
                    hc = 1
                for credit in ("bar4-nofence", "off"):
                    ctag = "credit" if credit != "off" else "off"
                    add(
                        part="s3_card_cores",
                        cfg=f"s3_{lbl}_c{nc}_h{hc}_{ctag}",
                        nc=nc, hc=hc, fk=fk, plen=plen,
                        rx_desc=512, pool_size=768, pool_cache=128,
                        r_eff=256, credit=credit, credit_batch=32,
                        mthreads=mthreads, mbatch=mbatch,
                    )

    if mode == "smoke":
        add(part="smoke", cfg=f"smoke_s1a_{big_lbl}_r32_b8", nc=1, hc=1, rx_desc=512, pool_size=768, pool_cache=128, r_eff=32, credit_batch=8, track_iova=True)
        add(part="smoke", cfg=f"smoke_s1a_{big_lbl}_r256_b8", nc=1, hc=1, rx_desc=512, pool_size=768, pool_cache=128, r_eff=256, credit_batch=8, track_iova=True)
        add(part="smoke", cfg=f"smoke_s1d_{big_lbl}_8q_oq8192_128m", nc=8, hc=8, rx_desc=8192, pool_size=8448, pool_cache=128, r_eff=4096, credit_batch=16, touch_stride=64, track_iova=True)
        add(part="smoke", cfg="smoke_s2a_uc64_4c1h_b8", nc=4, hc=1, fk="uc64", plen=0, rx_desc=512, pool_size=768, pool_cache=128, r_eff=256, credit_batch=8, mthreads=16, mbatch=64)
        add(part="smoke", cfg="smoke_s2a_uc64_4c1h_b64", nc=4, hc=1, fk="uc64", plen=0, rx_desc=512, pool_size=768, pool_cache=128, r_eff=256, credit_batch=64, mthreads=16, mbatch=64)
        add(part="smoke", cfg="smoke_s3_254b_c4_h2_credit", nc=4, hc=2, fk="ud", plen=184, rx_desc=512, pool_size=768, pool_cache=128, r_eff=256, credit_batch=32, mthreads=24, mbatch=64)

    if mode == "supplement":
        # 1e: Header-only / untouched-payload 2-way DDIO cliff (`touch_stride=0`, `cache=0` FIFO)
        # Directly exposes Intel Sapphire Rapids 2-way DDIO (~8 MiB) write-only cliff vs full-LLC (~60 MiB) in s1c/s1d.
        for pool, tag in (
            (2048,  "4m"),
            (3072,  "6m"),
            (4096,  "8m"),
            (5120,  "10m"),
            (6144,  "12m"),
            (8192,  "16m"),
            (16384, "32m"),
        ):
            add(
                part="s1e_ddio_notouch",
                cfg=f"s1e_{big_lbl}_notouch_{tag}_oq1024_r512",
                nc=1, hc=1, rx_desc=1024, pool_size=pool, pool_cache=0,
                r_eff=512, credit_batch=8, touch_stride=0, track_iova=True,
            )
        # Clock-clean n=3 re-runs of the 2 uncredited (`credit=off`) controls that hit transient GHz dips in main campaign
        add(
            part="s2a_batch_64b",
            cfg="s2a_uc64_4c1h_off",
            nc=4, hc=1, fk="uc64", plen=0, rx_desc=512, pool_size=768, pool_cache=128,
            r_eff=256, credit="off", credit_batch=32, mthreads=16, mbatch=64,
        )
        add(
            part="s3_card_cores",
            cfg="s3_64b_c1_h1_off",
            nc=1, hc=1, fk="uc64", plen=0, rx_desc=512, pool_size=768, pool_cache=128,
            r_eff=256, credit="off", credit_batch=32, mthreads=24, mbatch=64,
        )

    return specs


# ---------------------------------------------------------------------------
# PMU Delta & Verification Math (100% pure functions, unit-tested offline)
# ---------------------------------------------------------------------------
def _delta_triplet(a: list, b: list, min_pct_box: list) -> int:
    dv = b[0] - a[0]
    de = b[1] - a[1]
    dr = b[2] - a[2]
    if de > 0:
        pct = 100.0 * dr / de
        if pct < min_pct_box[0]:
            min_pct_box[0] = pct
    return dv


def compute_host_pmu_delta(p0: dict, p1: dict) -> dict:
    dt = max(p1["t_s"] - p0["t_s"], 1e-9)
    min_pct = [100.0]

    per_core = []
    tot_cycles = tot_instr = tot_ref = tot_llc_l = tot_llc_m = tot_sb = tot_pf = 0
    for c0, c1 in zip(p0["cores"], p1["cores"]):
        cyc = _delta_triplet(c0["cycles"], c1["cycles"], min_pct)
        ins = _delta_triplet(c0["instr"], c1["instr"], min_pct)
        ref = _delta_triplet(c0["ref_cycles"], c1["ref_cycles"], min_pct)
        ll = _delta_triplet(c0["llc_loads"], c1["llc_loads"], min_pct)
        lm = _delta_triplet(c0["llc_load_misses"], c1["llc_load_misses"], min_pct)
        sb = _delta_triplet(c0["sb_stalls"], c1["sb_stalls"], min_pct)
        pf = _delta_triplet(c0["page_faults"], c1["page_faults"], min_pct)
        ghz = (cyc / max(ref, 1)) * (REF_HZ / 1e9)
        per_core.append({
            "cpu": c0["cpu"],
            "ghz": ghz,
            "cycles": cyc,
            "instr": ins,
            "ipc": ins / max(cyc, 1),
            "sb_stall_pct": 100.0 * sb / max(cyc, 1),
            "llc_loads": ll,
            "llc_load_misses": lm,
            "page_faults": pf,
        })
        tot_cycles += cyc
        tot_instr += ins
        tot_ref += ref
        tot_llc_l += ll
        tot_llc_m += lm
        tot_sb += sb
        tot_pf += pf

    u = {k: _delta_triplet(p0["uncore"][k], p1["uncore"][k], min_pct) for k in p0["uncore"]}
    cha_hit = sum(_delta_triplet(a, b, min_pct) for a, b in zip(p0["cha_hit"], p1["cha_hit"]))
    cha_miss = sum(_delta_triplet(a, b, min_pct) for a, b in zip(p0["cha_miss"], p1["cha_miss"]))
    imc_rd = sum(_delta_triplet(a, b, min_pct) for a, b in zip(p0["imc_rd"], p1["imc_rd"]))
    imc_wr = sum(_delta_triplet(a, b, min_pct) for a, b in zip(p0["imc_wr"], p1["imc_wr"]))

    inb_wr = u["iio1_inb_wr"]
    avg_wr_bytes = (4.0 * u["iio1_inb_wr_dw"] / inb_wr) if inb_wr > 0 else 0.0
    ddio_tot = cha_hit + cha_miss
    ddio_hit_pct = (100.0 * cha_hit / ddio_tot) if ddio_tot > 0 else 100.0
    min_core_ghz = min((c["ghz"] for c in per_core), default=0.0)
    mesh_ghz = (u["mesh_clk"] / dt) / 1e9

    return {
        "dt_s": dt,
        "min_running_pct": min_pct[0],
        "per_core": per_core,
        "min_core_ghz": min_core_ghz,
        "cpu40_ghz": per_core[0]["ghz"] if per_core else 0.0,
        "mesh_ghz": mesh_ghz,
        "cycles": tot_cycles,
        "instr": tot_instr,
        "ipc": tot_instr / max(tot_cycles, 1),
        "sb_stall_pct": 100.0 * tot_sb / max(tot_cycles, 1),
        "llc_loads": tot_llc_l,
        "llc_load_misses": tot_llc_m,
        "llc_miss_pct": (100.0 * tot_llc_m / tot_llc_l) if tot_llc_l > 0 else 0.0,
        "page_faults": tot_pf,
        "iio1_inb_wr_mops": inb_wr / dt / 1e6,
        "iio1_inb_wr_gbps": (u["iio1_inb_wr_dw"] * 4.0 * 8.0) / dt / 1e9,
        "iio1_inb_wr_avg_bytes": avg_wr_bytes,
        "iio1_inb_rd_mops": u["iio1_inb_rd"] / dt / 1e6,
        "iio1_cpu_wr_mops": u["iio1_cpu_wr"] / dt / 1e6,
        "iio1_cpu_wr_gbps": (u["iio1_cpu_wr"] * 64.0 * 8.0) / dt / 1e9,
        "cha_io_hit_itom": cha_hit,
        "cha_io_miss_itom": cha_miss,
        "ddio_hit_pct": ddio_hit_pct,
        "imc_rd_mops": imc_rd / dt / 1e6,
        "imc_wr_mops": imc_wr / dt / 1e6,
        "imc_rd_gbps": (imc_rd * 64.0 * 8.0) / dt / 1e9,
        "imc_wr_gbps": (imc_wr * 64.0 * 8.0) / dt / 1e9,
    }


def compute_card_pmu_delta(t0: dict, t1: dict) -> dict:
    dt = max(t1["t_s"] - t0["t_s"], 1e-9)
    min_pct = [100.0]
    out = {"dt_s": dt}
    for k in t0["tad"]:
        dv = _delta_triplet(t0["tad"][k], t1["tad"][k], min_pct)
        out[k + "_mops"] = dv / dt / 1e6
    out["ddr_rd_gbps"] = out.get("ddr_reads_mops", 0.0) * 64.0 * 8.0 / 1e3
    out["ddr_wr_gbps"] = out.get("ddr_writes_mops", 0.0) * 64.0 * 8.0 / 1e3
    out["min_running_pct"] = min_pct[0]
    return out


def verify_run_record(
    sp: dict,
    hp_d: dict,
    cp_d: dict,
    ms: dict,
    cs_fin: dict,
    hs_fin: dict,
) -> tuple:
    """Return `(integrity_errs, clock_errs)` cleanly separating retryable clock jitter from hard errors."""
    integrity_errs = []
    clock_errs = []

    if hp_d["min_running_pct"] < 99.95:
        integrity_errs.append(f"host_pmu_pct={hp_d['min_running_pct']:.3f}")
    if cp_d["min_running_pct"] < 99.95:
        integrity_errs.append(f"card_pmu_pct={cp_d['min_running_pct']:.3f}")
    if hp_d["page_faults"] > 64:
        integrity_errs.append(f"page_faults={hp_d['page_faults']}")
    elif hp_d["page_faults"] != 0:
        clock_errs.append(f"transient_page_faults={hp_d['page_faults']}")
    # Empirical Socket-1 turbo floors grounded in MRV-137 logs (h1>=3.853, h2>=3.869, h4>=3.822 GHz).
    min_ghz_req = 3.85 if sp["hc"] == 1 else (3.75 if sp["hc"] <= 4 else 3.50)
    if hp_d["min_core_ghz"] < min_ghz_req:
        clock_errs.append(f"min_core_ghz={hp_d['min_core_ghz']:.3f}<{min_ghz_req:.2f}")
    if hp_d["mesh_ghz"] < 2.18:
        clock_errs.append(f"mesh_ghz={hp_d['mesh_ghz']:.3f}")
    if cs_fin["wire_bad"] != 0 or cs_fin["wire_torn"] != 0:
        integrity_errs.append(f"card_wire wb={cs_fin['wire_bad']} wt={cs_fin['wire_torn']}")
    if cs_fin["credit_torn"] != 0 or cs_fin["alloc_fails"] != 0:
        integrity_errs.append(f"card_err ct={cs_fin['credit_torn']} af={cs_fin['alloc_fails']}")
    if hs_fin["bad"] != 0 or hs_fin["torn"] != 0 or hs_fin["lease_err"] != 0:
        integrity_errs.append(f"host_err bad={hs_fin['bad']} torn={hs_fin['torn']} lease={hs_fin['lease_err']}")
    c2h_lost = cs_fin["c2h_pkts"] - hs_fin["rx_pkts"]
    if sp["credit"] != "off":
        if c2h_lost != 0:
            integrity_errs.append(f"c2h_lost={c2h_lost} (card={cs_fin['c2h_pkts']} host={hs_fin['rx_pkts']})")
    else:
        # Uncredited (`credit=off`) controls expose passive SDP OQ silent drops (`c2h_lost >= 0`);
        # negative c2h_lost (more host RX than card TX) is still a hard conservation error.
        if c2h_lost < 0:
            integrity_errs.append(f"c2h_lost={c2h_lost} (card={cs_fin['c2h_pkts']} host={hs_fin['rx_pkts']})")
    if hs_fin["rx_pkts"] == 0:
        integrity_errs.append("zero_rx_pkts")
    if ms["cqe_err"] != 0 or ms["post_err"] != 0:
        integrity_errs.append(f"merc_err cqe={ms['cqe_err']} post={ms['post_err']}")
    if ms["phy"]["tx_pause"] != 0 or ms["phy"]["rx_pause"] != 0:
        integrity_errs.append(f"pause tx={ms['phy']['tx_pause']} rx={ms['phy']['rx_pause']}")
    if abs(ms["dt_phy_s"] - ms["dt_app_s"]) > 0.003:
        integrity_errs.append(f"dt_skew={(ms['dt_phy_s'] - ms['dt_app_s']) * 1000:.2f}ms")

    # Sanity: when BAR4 credit is active, card must observe credit updates
    if sp["credit"] != "off" and hs_fin["credit_writes"] == 0:
        integrity_errs.append("credit_writes=0")
    return integrity_errs, clock_errs


# ---------------------------------------------------------------------------
# Lightweight Host Quiescence Probe (only used when mrv214_host is not running)
# ---------------------------------------------------------------------------
def _probe_socket1_mesh_imc(sleep_s: float = 0.25) -> tuple:
    """Open temporary mesh+IMC counters for 250 ms, read delta, and close immediately."""
    import ctypes

    class _PerfEventAttr(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_uint32),
            ("size", ctypes.c_uint32),
            ("config", ctypes.c_uint64),
            ("sample_period", ctypes.c_uint64),
            ("sample_type", ctypes.c_uint64),
            ("read_format", ctypes.c_uint64),
            ("flags", ctypes.c_uint64),
            ("wakeup_events", ctypes.c_uint32),
            ("bp_type", ctypes.c_uint32),
            ("config1", ctypes.c_uint64),
            ("config2", ctypes.c_uint64),
            ("branch_sample_type", ctypes.c_uint64),
            ("sample_regs_user", ctypes.c_uint64),
            ("sample_stack_user", ctypes.c_uint32),
            ("clockid", ctypes.c_int32),
            ("sample_regs_intr", ctypes.c_uint64),
            ("aux_watermark", ctypes.c_uint32),
            ("sample_max_stack", ctypes.c_uint16),
            ("reserved2", ctypes.c_uint16),
            ("aux_sample_size", ctypes.c_uint32),
            ("reserved3", ctypes.c_uint32),
            ("sig_data", ctypes.c_uint64),
            ("config3", ctypes.c_uint64),
        ]

    libc = ctypes.CDLL("libc.so.6", use_errno=True)

    def open_fd(pmu_name: str, cfg: int) -> int:
        ptype = int(pathlib.Path(f"/sys/bus/event_source/devices/{pmu_name}/type").read_text().strip())
        attr = _PerfEventAttr(type=ptype, size=ctypes.sizeof(_PerfEventAttr), config=cfg, read_format=3, flags=1)
        fd = libc.syscall(298, ctypes.byref(attr), -1, SOCKET1_UNCORE_CPU, -1, 0)
        if fd < 0:
            raise OSError(ctypes.get_errno(), f"perf_event_open({pmu_name})")
        libc.ioctl(fd, 0x2400, 0)
        return fd

    def rd(fd: int) -> int:
        raw = os.read(fd, 24)
        return int.from_bytes(raw[0:8], "little")

    fds = []
    try:
        m_fd = open_fd("uncore_m2pcie_1", 0x01)
        fds.append(m_fd)
        imc_fds = []
        for i in range(8):
            f_r = open_fd(f"uncore_imc_{i}", 0xCF05)
            f_w = open_fd(f"uncore_imc_{i}", 0xF005)
            fds.extend([f_r, f_w])
            imc_fds.extend([f_r, f_w])
        t0 = time.monotonic()
        m0 = rd(m_fd)
        i0 = sum(rd(f) for f in imc_fds)
        time.sleep(sleep_s)
        dt = max(time.monotonic() - t0, 1e-9)
        m1 = rd(m_fd)
        i1 = sum(rd(f) for f in imc_fds)
        mesh_ghz = ((m1 - m0) / dt) / 1e9
        imc_gbps = ((i1 - i0) * 64.0 * 8.0) / dt / 1e9
        return mesh_ghz, imc_gbps
    finally:
        for fd in fds:
            libc.ioctl(fd, 0x2401, 0)
            os.close(fd)


# ---------------------------------------------------------------------------
# Subprocess, SSH, TCP, and Environment Helpers
# ---------------------------------------------------------------------------
def sh(cmd, check=True, timeout=180):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"CMD FAILED ({r.returncode}): {cmd}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
    return r


def ssh_card(script: str, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def ssh_merc(script: str, check=True, timeout=60):
    return sh(MERC_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host: str, port: int, cmd: str, timeout: float = 10.0) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((cmd + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode().strip())


def append_jsonl(path: pathlib.Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")
        f.flush()


def deploy_binaries():
    """Copy card binary to CN106 and compile mercury client on mercury."""
    sh(["scp", "-o", "BatchMode=yes", CARD_BIN_LOCAL, f"root@10.10.0.2:{CARD_BIN}"])
    ssh_merc("mkdir -p /tmp/mrv214")
    sh([
        "scp", "-o", "BatchMode=yes",
        str(MERC_SRC_DIR / "mrv214_verbs_shim.c"),
        str(MERC_SRC_DIR / "mrv214_client.rs"),
        "mercury:/tmp/mrv214/",
    ])
    ssh_merc(
        'export PATH="$HOME/.cargo/bin:$PATH" && '
        "cd /tmp/mrv214 && "
        "gcc -O3 -Wall -Wextra -Werror -c mrv214_verbs_shim.c -o mrv214_verbs_shim.o && "
        "rustc -O --edition 2024 -D warnings mrv214_client.rs -C link-arg=mrv214_verbs_shim.o "
        "-C link-arg=-libverbs -o mrv214_client && "
        "ls -lh /tmp/mrv214/mrv214_client"
    )


def read_env_snapshot() -> dict:
    gov40 = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    cur40 = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {
        p.parent.name: int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz"))
    }
    edac_host = [
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    ]
    rp = pathlib.Path("/sys/bus/pci/devices/0000:98:01.0")
    rp_aer = {
        k: (int((rp / k).read_text().strip()) if (rp / k).exists() else -1)
        for k in ("aer_rootport_total_err_cor", "aer_rootport_total_err_nonfatal", "aer_rootport_total_err_fatal")
    }
    card_raw = ssh_card(
        "echo freq=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq);"
        "echo ea0_ue=$(cat /sys/devices/system/edac/ea/ea0/ue_count 2>/dev/null || echo 0);"
        "echo mc0_ue=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo 0);"
        "echo edac_ue=$(grep . /sys/devices/system/edac/*/*/ue_count 2>/dev/null | awk -F: '{s+=$2} END{print s+0}');"
        "echo sei_uer=$(dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true);"
        "echo bar4_idx7=$(python3 /root/bar4idx7.py | awk '{print $NF}')"
    ).stdout
    cd = {}
    for line in card_raw.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            cd[k] = v
    merc_raw = ssh_merc(
        "ethtool -S ens6f1np1 | grep -E 'tx_packets_phy:|tx_bytes_phy:|rx_crc_errors_phy:|tx_global_pause:|rx_global_pause:'"
    ).stdout
    md = {}
    for line in merc_raw.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            md[k.strip()] = int(v.strip())
    return {
        "ts": utc_now(),
        "saturn": {
            "cpu40_gov": gov40,
            "cpu40_khz": cur40,
            "uncore_khz": uncore,
            "edac_ue": edac_host,
            "edac_ue_sum": sum(edac_host),
            "rp_aer": rp_aer,
        },
        "cn106": cd,
        "mercury": md,
    }


def calibrate_host_app_work() -> dict:
    r = sh([HOST_BIN, "--calibrate-work", "--cal-ms", "1000"])
    for line in r.stdout.splitlines():
        if line.startswith("MRV214_CAL_WORK "):
            return json.loads(line[len("MRV214_CAL_WORK "):])
    raise RuntimeError(f"Missing MRV214_CAL_WORK output:\n{r.stdout}\n{r.stderr}")


# ---------------------------------------------------------------------------
# Stack Lifecycle Manager (minimizes EAL restarts via dynamic TCP CONFIG)
# ---------------------------------------------------------------------------
class Stack:
    def __init__(self):
        self.card_key = None
        self.host_key = None
        self.host_proc = None

    def prepare_card_vfio(self):
        ssh_card(
            "tmux send-keys -t refl C-c 2>/dev/null || true; sleep 0.3; "
            "tmux kill-session -t refl 2>/dev/null || true; "
            "tmux kill-session -t m214c 2>/dev/null || true; "
            "pkill -9 mrv214_card 2>/dev/null || true; "
            "rm -f /dev/hugepages/refl* /dev/hugepages/m214c*; "
            "ip link set eth0 down 2>/dev/null || true; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
            "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
            'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi'
        )

    def stop_host(self):
        if self.host_proc is not None:
            try:
                tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "QUIT", timeout=3.0)
            except Exception:
                pass
            try:
                self.host_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.host_proc.terminate()
                try:
                    self.host_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.host_proc.kill()
            self.host_proc = None
        sh(["bash", "-c", "pkill -9 mrv214_host 2>/dev/null || true; rm -f /dev/hugepages/m214*"], check=False)
        self.host_key = None

    def stop_card(self):
        # Always release host BAR4 lease BEFORE stopping card!
        self.stop_host()
        try:
            tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "QUIT", timeout=3.0)
        except Exception:
            pass
        ssh_card(
            "sleep 0.3; tmux kill-session -t m214c 2>/dev/null || true; "
            "rm -f /dev/hugepages/m214* /tmp/m214c.log",
            check=False,
        )
        self.card_key = None

    def ensure_card(self, mode: str, nc: int, card_pool_size: int = 16384):
        key = (mode, nc, card_pool_size)
        if self.card_key == key:
            return
        self.stop_card()
        lcores = f"2,4-{3 + nc}" if nc > 1 else "2,4"
        if mode == "probe-rss":
            eal_devs = "-a 0002:02:00.0"
        elif mode == "synth":
            eal_devs = "-a 0002:01:00.2"
        else:
            eal_devs = "-a 0002:02:00.0 -a 0002:01:00.2"
        cmd = (
            f"{CARD_BIN} -l {lcores} --main-lcore 2 {eal_devs} --file-prefix=m214c -- "
            f"--mode {mode} --card-pool-size {card_pool_size} --ctrl-port {CARD_CTRL_PORT}"
        )
        r = ssh_card(
            f'rm -f /tmp/m214c.log; '
            f'tmux new-session -d -s m214c "{cmd} > /tmp/m214c.log 2>&1"; '
            f'for i in $(seq 1 80); do grep -q "MRV214_CARD READY" /tmp/m214c.log 2>/dev/null && break; sleep 0.15; done; '
            f'grep "MRV214_CARD READY" /tmp/m214c.log',
            check=False,
        )
        if r.returncode != 0:
            raise RuntimeError("mrv214_card failed:\n" + ssh_card("cat /tmp/m214c.log", check=False).stdout)
        self.card_key = key

    def ensure_host(self, hc: int, sdp_queues: int, rx_desc: int, pool_size: int, pool_cache: int, rx_poll_mem: bool):
        key = (hc, sdp_queues, rx_desc, pool_size, pool_cache, rx_poll_mem)
        if self.host_key == key and self.host_proc is not None and self.host_proc.poll() is None:
            return
        self.stop_host()
        lcores = f"40-{39 + hc}" if hc > 1 else "40"
        devarg = f"0000:99:02.0,ism_enable=0,max_rings={sdp_queues}"
        if rx_desc < 2048:
            devarg += ",allow_small_oq=1"
        if rx_poll_mem:
            devarg += ",rx_poll_mem=1"
        prefix = ["numactl", "--membind=1"] if shutil.which("numactl") else []
        cmd = prefix + [
            HOST_BIN,
            "-l", lcores,
            "-a", devarg,
            "--socket-mem=0,1024",
            "--iova-mode=va",
            "--file-prefix=m214h",
            "--",
            "--sdp-queues", str(sdp_queues),
            "--rx-desc", str(rx_desc),
            "--pool-size", str(pool_size),
            "--pool-cache", str(pool_cache),
            "--ctrl-port", str(HOST_CTRL_PORT),
        ]
        if rx_desc < 2048:
            cmd.append("--allow-small-oq")
        if rx_poll_mem:
            cmd.append("--rx-poll-mem")
        log_path = pathlib.Path("/tmp/m214h.log")
        with open(log_path, "w") as lf:
            self.host_proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
        for _ in range(120):
            if self.host_proc.poll() is not None:
                raise RuntimeError(f"mrv214_host exited early:\n{log_path.read_text()}")
            txt = log_path.read_text()
            if "MRV214_HOST READY" in txt:
                time.sleep(0.05)
                self.host_key = key
                return
            time.sleep(0.1)
        raise RuntimeError(f"mrv214_host ready timeout:\n{log_path.read_text()}")

    def wait_for_quiet_host(self, min_khz: int = 3_850_000, max_imc_gbps: float = 8.0, max_wait_s: float = 180.0):
        """Wait until cpu40 >= min_khz, Socket-1 mesh >= 2.185 GHz, and idle IMC <= max_imc_gbps."""
        p40 = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq")
        t0 = time.monotonic()
        streak = 0
        last_khz = 0
        last_mesh = 0.0
        last_imc = 0.0
        while time.monotonic() - t0 < max_wait_s:
            last_khz = int(p40.read_text().strip())
            if last_khz >= min_khz:
                if self.host_proc is not None and self.host_proc.poll() is None:
                    s0 = tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "PMU_SNAP")
                    time.sleep(0.25)
                    s1 = tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "PMU_SNAP")
                    d = compute_host_pmu_delta(s0, s1)
                    last_mesh = d["mesh_ghz"]
                    last_imc = d["imc_rd_gbps"] + d["imc_wr_gbps"]
                else:
                    last_mesh, last_imc = _probe_socket1_mesh_imc(0.25)
                ok = (last_mesh >= 2.185) and (last_imc <= max_imc_gbps)
            else:
                ok = False
                time.sleep(0.15)
            if ok:
                streak += 1
                if streak >= 2:
                    return
            else:
                if streak == 0 and (time.monotonic() - t0) > 5.0:
                    print(
                        f"  [WAIT_QUIET] cpu40={last_khz/1e6:.2f}GHz mesh={last_mesh:.3f}GHz imc={last_imc:.1f}Gbps",
                        flush=True,
                    )
                streak = 0
                time.sleep(1.0)
        raise RuntimeError(
            f"wait_for_quiet_host timeout ({max_wait_s:.0f}s): "
            f"cpu40={last_khz}kHz mesh={last_mesh:.3f}GHz imc={last_imc:.1f}Gbps"
        )

    def restore_baseline(self, cleanup_mercury: bool = False) -> dict:
        self.stop_card()
        sh(["bash", "-c", "rm -f /dev/hugepages/m214*"], check=False)
        merc_clean_cmd = "pkill -9 mrv214_client 2>/dev/null || true"
        if cleanup_mercury:
            merc_clean_cmd += "; rm -rf /tmp/mrv214*"
        sh(MERC_SSH + [merc_clean_cmd], check=False)
        out = ssh_card(
            f"{CARD_BIN} --restore 2>/dev/null || true; "
            "rmmod pem_wc 2>/dev/null || true; "
            "rm -f /dev/hugepages/m214*; "
            "dpdk-devbind.py -b rvu_nicpf 0002:02:00.0; "
            "sleep 1.5; "
            "ip addr flush dev eth0 2>/dev/null || true; "
            "ip addr add 10.10.100.2/24 dev eth0; "
            "ip link set eth0 up; "
            "sleep 2.0; "
            "tmux kill-session -t refl 2>/dev/null || true; "
            "rm -f /dev/hugepages/refl*; "
            "tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl; "
            "sleep 1.0; "
            "ethtool eth0 | grep -E 'Speed|Link detected'; "
            "ping -c 2 -W 1 10.10.100.1 | tail -n 2; "
            "lsmod | grep pem_wc || echo 'pem_wc not loaded'; "
            "tmux ls"
        ).stdout.strip()
        merc_chk = sh(
            MERC_SSH + ["pgrep -af mrv214_client || echo 'mercury clean'; ls -d /tmp/mrv214* 2>/dev/null || echo 'mercury /tmp/mrv214 clean'"],
            check=False,
        ).stdout.strip()
        return {"restore_stdout": out, "mercury_check": merc_chk}


def probe_balanced_flows(stack: Stack, nc: int, fk: str) -> list:
    """Probe flows 1..128 against CN106 eth0 RSS for `(nc, fk)` and return 1:1 balanced list."""
    if nc <= 1:
        return list(range(1, 33))
    stack.ensure_card("probe-rss", nc)
    plen = 0 if fk == "uc64" else 40
    tcp_cmd("10.10.0.2", CARD_CTRL_PORT, f"CONFIG run_gen=0 frame_kind={fk} payload={plen} credit=off")
    tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "START")
    tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "RESET")
    tcp_cmd("mercury", MERC_CTRL_PORT, f"PROBE_RSS mode={fk} count=128", timeout=20.0)
    time.sleep(0.05)
    cs = tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "STATS")
    fq = cs.get("flow_q", [])
    by_q = {q: [] for q in range(nc)}
    for idx, q1 in enumerate(fq):
        if 1 <= q1 <= nc:
            by_q[q1 - 1].append(idx + 1)
    balanced = []
    max_depth = max((len(v) for v in by_q.values()), default=0)
    for d in range(max_depth):
        for q in range(nc):
            if d < len(by_q[q]):
                balanced.append(by_q[q][d])
    missing = [q for q in range(nc) if not by_q[q]]
    if missing:
        print(f"  WARN probe_balanced_flows(nc={nc}, fk={fk}): missing queues {missing}", flush=True)
    else:
        min_d = min(len(v) for v in by_q.values())
        print(f"  RSS probe (nc={nc}, fk={fk}): all {nc} queues covered (min {min_d} flows/q)", flush=True)
    return balanced


def drain_card_fifo(timeout_s: float = 0.25):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        st = tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "STATS")
        if st.get("fifo_len", 0) == 0:
            break
        time.sleep(0.005)
    time.sleep(0.015)


def run_one(
    stack: Stack,
    sp: dict,
    rep: int,
    run_gen: int,
    flow_map: dict,
    cal_work: dict,
    meas_s: float = MEAS_SECS,
) -> tuple:
    nc = sp["nc"]
    hc = sp["hc"]
    fk = sp["fk"]
    plen = sp["plen"]
    l2_b = l2_bytes_for(fk, plen)

    stack.ensure_card("wire", nc)
    stack.ensure_host(hc, nc, sp["rx_desc"], sp["pool_size"], sp["pool_cache"], sp["rx_poll_mem"])
    stack.wait_for_quiet_host()

    # Dynamic CONFIG on card, host, and mercury with unique run_gen
    card_credit = "off" if sp["credit"] == "off" else "bar4"
    c_cfg = tcp_cmd(
        "10.10.0.2",
        CARD_CTRL_PORT,
        f"CONFIG run_gen={run_gen} r_eff={sp['r_eff']} r_eff_bytes={sp['r_eff_bytes']} "
        f"credit={card_credit} frame_kind={fk} payload={plen} fifo_cap={sp['fifo_cap']}",
    )
    if not c_cfg.get("ok"):
        raise RuntimeError(f"card CONFIG failed: {c_cfg}")

    h_cfg = tcp_cmd(
        "127.0.0.1",
        HOST_CTRL_PORT,
        f"CONFIG run_gen={run_gen} credit={sp['credit']} credit_batch={sp['credit_batch']} "
        f"sfence={1 if sp['sfence'] else 0} align_refill={1 if sp['align_refill'] else 0} "
        f"credit_flush_us={sp['credit_flush_us']} frame_kind={fk} payload={plen} "
        f"touch_stride={sp['touch_stride']} app_work={1 if sp['app_work'] else 0} "
        f"track_iova={1 if sp['track_iova'] else 0}",
    )
    if not h_cfg.get("ok"):
        raise RuntimeError(f"host CONFIG failed: {h_cfg}")

    fls = flow_map.get((nc, fk), [])
    farg = (" flows=" + ",".join(str(x) for x in fls[:sp["mthreads"]])) if fls else ""
    m_cfg = tcp_cmd(
        "mercury",
        MERC_CTRL_PORT,
        f"CONFIG mode={fk} threads={sp['mthreads']} payload={plen} batch={sp['mbatch']} "
        f"rate_mpps={sp['rate_mpps']} run_gen={run_gen} gso={1 if sp['gso'] else 0}{farg}",
    )
    if not m_cfg.get("ok"):
        raise RuntimeError(f"mercury CONFIG failed: {m_cfg}")

    try:
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "START")
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "START")
        tcp_cmd("mercury", MERC_CTRL_PORT, "START")

        # Warmup -> clean drain boundary -> RESET
        time.sleep(WARMUP_SECS)
        tcp_cmd("mercury", MERC_CTRL_PORT, "STOP")
        drain_card_fifo()
        tcp_cmd("mercury", MERC_CTRL_PORT, "RESET")
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "RESET")
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "RESET")

        # Steady-state measurement window
        t_start = time.monotonic()
        tcp_cmd("mercury", MERC_CTRL_PORT, "START")
        time.sleep(0.10)
        h0 = tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "SNAP")
        c0 = tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "SNAP")
        time.sleep(max(meas_s - 0.15, 0.25))
        h1 = tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "SNAP")
        c1 = tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "SNAP")
        time.sleep(0.05)
        tcp_cmd("mercury", MERC_CTRL_PORT, "STOP")
        t_end = time.monotonic()

        # Drain completely for exact full-run conservation audit (`c2h_lost == 0`)
        drain_card_fifo()
        ms = tcp_cmd("mercury", MERC_CTRL_PORT, "STATS")
        cs_fin = tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "STATS")
        hs_fin = tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "STATS")
    finally:
        try:
            tcp_cmd("mercury", MERC_CTRL_PORT, "STOP", timeout=2.0)
        except Exception:
            pass
        try:
            tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "STOP", timeout=2.0)
        except Exception:
            pass
        try:
            tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "STOP", timeout=2.0)
        except Exception:
            pass

    hp_d = compute_host_pmu_delta(h0["pmu"], h1["pmu"])
    cp_d = compute_card_pmu_delta(c0["pmu"], c1["pmu"])
    dt_steady = hp_d["dt_s"]

    d_hrx = h1["rx_pkts"] - h0["rx_pkts"]
    d_hcw = h1["credit_writes"] - h0["credit_writes"]
    d_hwork = h1["app_work_steps"] - h0["app_work_steps"]
    d_cc2h = c1["c2h_pkts"] - c0["c2h_pkts"]
    d_cerx = c1["eth_rx_pkts"] - c0["eth_rx_pkts"]
    d_cstalls = c1["credit_stalls"] - c0["credit_stalls"]
    d_cfifo_drop = c1["fifo_drops"] - c0["fifo_drops"]

    cyc_per_step = cal_work.get("cycles_per_step", 0.0) if sp["app_work"] else 0.0
    net_io_cycles = max(0.0, hp_d["cycles"] - d_hwork * cyc_per_step)
    rates = {
        "dt_wall_s": t_end - t_start,
        "dt_steady_s": dt_steady,
        "l2_bytes": l2_b,
        "merc_phy_mpps": ms["phy"]["tx_mpps"],
        "merc_phy_wire_gbps": ms["phy"]["wire_gbps"],
        "card_eth_rx_mpps": d_cerx / dt_steady / 1e6,
        "card_c2h_mpps": d_cc2h / dt_steady / 1e6,
        "host_rx_mpps": d_hrx / dt_steady / 1e6,
        "host_l2_gbps": (d_hrx * l2_b * 8.0) / dt_steady / 1e9,
        "host_wire_gbps": (d_hrx * (l2_b + 20) * 8.0) / dt_steady / 1e9,
        "credit_writes_mpps": d_hcw / dt_steady / 1e6,
        "cycles_per_pkt": hp_d["cycles"] / max(d_hrx, 1),
        "io_cycles_per_pkt": net_io_cycles / max(d_hrx, 1),
        "steady_credit_stalls": d_cstalls,
        "steady_fifo_drops": d_cfifo_drop,
        "unique_iova_slots": hs_fin.get("unique_iova_slots", 0),
        "measured_iova_unique_mib": hs_fin.get("measured_iova_unique_mib", 0.0),
        "measured_iova_span_mib": hs_fin.get("measured_iova_span_mib", 0.0),
    }
    int_errs, clk_errs = verify_run_record(sp, hp_d, cp_d, ms, cs_fin, hs_fin)
    all_errs = int_errs + clk_errs
    rec = {
        "ts": utc_now(),
        "part": sp["part"],
        "cfg": sp["cfg"],
        "rep": rep,
        "run_gen": run_gen,
        "spec": sp,
        "rates": rates,
        "host_pmu": hp_d,
        "card_pmu": cp_d,
        "mercury": ms,
        "card_final": cs_fin,
        "host_final": hs_fin,
        "c2h_lost": cs_fin["c2h_pkts"] - hs_fin["rx_pkts"],
        "verified": len(all_errs) == 0,
        "integrity_errors": int_errs,
        "clock_errors": clk_errs,
    }
    return rec, int_errs, clk_errs


# ---------------------------------------------------------------------------
# Offline Self-Test & Dry-Run (100% zero hardware access!)
# ---------------------------------------------------------------------------
def run_offline_self_test() -> int:
    print("[SELF-TEST] Validating full E6 matrix specs and PMU/verification math offline...")
    for m in ("smoke", "sweep1", "sweep2", "sweep3", "all"):
        for f1500 in ("udp1500", "ud1094"):
            specs = build_matrix(m, frame_1500=f1500)
            assert len(specs) > 0, f"empty matrix for {m}"
            seen_cfgs = set()
            for sp in specs:
                errs = validate_spec(sp)
                assert not errs, f"invalid spec {sp['cfg']}: {errs}"
                assert sp["cfg"] not in seen_cfgs, f"duplicate cfg {sp['cfg']}"
                seen_cfgs.add(sp["cfg"])

    # Synthetic 100% non-mux PMU delta + verification test
    p0 = {
        "t_s": 10.0,
        "cores": [{
            "cpu": 40,
            "cycles": [1000, 1000, 1000],
            "instr": [2000, 1000, 1000],
            "ref_cycles": [1000, 1000, 1000],
            "llc_loads": [500, 1000, 1000],
            "llc_load_misses": [5, 1000, 1000],
            "sb_stalls": [10, 1000, 1000],
            "page_faults": [0, 1000, 1000],
        }],
        "uncore": {
            "mesh_clk": [0, 1000, 1000],
            "iio1_inb_wr": [0, 1000, 1000],
            "iio1_inb_wr_dw": [0, 1000, 1000],
            "iio1_inb_rd": [0, 1000, 1000],
            "iio1_cpu_wr": [0, 1000, 1000],
        },
        "cha_hit": [[0, 1000, 1000]] * 32,
        "cha_miss": [[0, 1000, 1000]] * 32,
        "imc_rd": [[0, 1000, 1000]] * 8,
        "imc_wr": [[0, 1000, 1000]] * 8,
    }
    p1 = {
        "t_s": 11.0,
        "cores": [{
            "cpu": 40,
            "cycles": [1000 + 3_900_000_000, 2000, 2000],
            "instr": [2000 + 7_800_000_000, 2000, 2000],
            "ref_cycles": [1000 + 2_200_000_000, 2000, 2000],
            "llc_loads": [500 + 10_000_000, 2000, 2000],
            "llc_load_misses": [5 + 100_000, 2000, 2000],
            "sb_stalls": [10 + 39_000_000, 2000, 2000],
            "page_faults": [0, 2000, 2000],
        }],
        "uncore": {
            "mesh_clk": [2_200_000_000, 2000, 2000],
            "iio1_inb_wr": [195_000_000, 2000, 2000],
            "iio1_inb_wr_dw": [195_000_000 * 16, 2000, 2000],
            "iio1_inb_rd": [1_000_000, 2000, 2000],
            "iio1_cpu_wr": [250_000, 2000, 2000],
        },
        "cha_hit": [[6_000_000, 2000, 2000]] * 32,
        "cha_miss": [[0, 2000, 2000]] * 32,
        "imc_rd": [[10_000, 2000, 2000]] * 8,
        "imc_wr": [[10_000, 2000, 2000]] * 8,
    }
    hp = compute_host_pmu_delta(p0, p1)
    assert abs(hp["cpu40_ghz"] - 3.90) < 1e-6
    assert abs(hp["mesh_ghz"] - 2.20) < 1e-6
    assert abs(hp["ddio_hit_pct"] - 100.0) < 1e-6
    assert abs(hp["iio1_inb_wr_avg_bytes"] - 64.0) < 1e-6
    assert abs(hp["iio1_cpu_wr_mops"] - 0.25) < 1e-6

    c0 = {"t_s": 10.0, "tad": {"tad_req_msh_in_any": [0, 100, 100], "ddr_reads": [0, 100, 100], "ddr_writes": [0, 100, 100]}}
    c1 = {"t_s": 11.0, "tad": {"tad_req_msh_in_any": [500_000_000, 200, 200], "ddr_reads": [1_000_000, 200, 200], "ddr_writes": [2_000_000, 200, 200]}}
    cp = compute_card_pmu_delta(c0, c1)
    assert cp["min_running_pct"] == 100.0
    assert abs(cp["ddr_rd_gbps"] - 0.512) < 1e-6

    sp_ok = build_matrix("smoke")[0]
    ms = {"dt_phy_s": 1.5, "dt_app_s": 1.5, "cqe_err": 0, "post_err": 0, "phy": {"tx_pause": 0, "rx_pause": 0}}
    cs_fin = {"wire_bad": 0, "wire_torn": 0, "credit_torn": 0, "alloc_fails": 0, "c2h_pkts": 10_000_000}
    hs_fin = {"bad": 0, "torn": 0, "lease_err": 0, "rx_pkts": 10_000_000, "credit_writes": 312_500}
    ie, ce = verify_run_record(sp_ok, hp, cp, ms, cs_fin, hs_fin)
    assert ie == [] and ce == [], f"unexpected errors: {ie} {ce}"

    # Verify conservation error is caught
    hs_bad = dict(hs_fin, rx_pkts=9_999_999)
    ie2, ce2 = verify_run_record(sp_ok, hp, cp, ms, cs_fin, hs_bad)
    assert any("c2h_lost=1" in e for e in ie2) and ce2 == []

    # Verify mesh clock dip alone triggers retryable clock error
    hp_mesh_low = dict(hp, mesh_ghz=2.10)
    ie3, ce3 = verify_run_record(sp_ok, hp_mesh_low, cp, ms, cs_fin, hs_fin)
    assert ie3 == [] and any("mesh_ghz=" in e for e in ce3)

    # Verify single-core vs 4-core vs 8-core GHz thresholds (MRV-137 calibrated)
    hp_378 = dict(hp, min_core_ghz=3.78)
    _, ce4 = verify_run_record(sp_ok, hp_378, cp, ms, cs_fin, hs_fin)
    assert any("min_core_ghz=" in e for e in ce4)
    sp_4c = dict(sp_ok, hc=4)
    _, ce4b = verify_run_record(sp_4c, hp_378, cp, ms, cs_fin, hs_fin)
    assert ce4b == []
    hp_355 = dict(hp, min_core_ghz=3.55)
    _, ce4c = verify_run_record(sp_4c, hp_355, cp, ms, cs_fin, hs_fin)
    assert any("min_core_ghz=" in e for e in ce4c)
    sp_8c = dict(sp_ok, hc=8)
    _, ce5 = verify_run_record(sp_8c, hp_355, cp, ms, cs_fin, hs_fin)
    assert ce5 == []
    hp_pf_small = dict(hp, page_faults=5)
    ie_pf1, ce_pf1 = verify_run_record(sp_ok, hp_pf_small, cp, ms, cs_fin, hs_fin)
    assert ie_pf1 == [] and any("transient_page_faults=5" in e for e in ce_pf1)
    hp_pf_big = dict(hp, page_faults=100)
    ie_pf2, ce_pf2 = verify_run_record(sp_ok, hp_pf_big, cp, ms, cs_fin, hs_fin)
    assert any("page_faults=100" in e for e in ie_pf2) and ce_pf2 == []
    all_specs = build_matrix("all")
    print(f"[SELF-TEST] PASS: {len(all_specs)} E6 conditions validated ({len(all_specs) * REPS} runs @ n={REPS}), 0 invariant violations.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="MRV-214 (E6) Design-Space Sensitivity Sweep Harness")
    ap.add_argument("--mode", choices=["smoke", "sweep1", "sweep2", "sweep3", "all", "supplement"], default="all")
    ap.add_argument("--frame-1500", choices=["udp1500", "ud1094"], default="udp1500")
    ap.add_argument("--reps", type=int, default=REPS)
    ap.add_argument("--meas-s", type=float, default=MEAS_SECS)
    ap.add_argument("--deploy", action="store_true", help="Copy card binary and build mercury client before running")
    ap.add_argument("--cleanup-mercury", action="store_true", help="Remove /tmp/mrv214* on mercury during baseline restore")
    ap.add_argument("--dry-run", action="store_true", help="Print & validate planned matrix offline without touching hardware")
    ap.add_argument("--self-test", action="store_true", help="Run offline unit tests on matrix invariants & PMU math")
    args = ap.parse_args()

    if args.self_test:
        return run_offline_self_test()

    specs = build_matrix(args.mode, frame_1500=args.frame_1500)
    for sp in specs:
        errs = validate_spec(sp)
        if errs:
            raise ValueError(f"Spec invariant error on {sp['cfg']}: {errs}")

    if args.dry_run:
        by_part = {}
        for sp in specs:
            by_part.setdefault(sp["part"], []).append(sp)
        print(f"MRV-214 E6 DRY-RUN mode={args.mode} frame_1500={args.frame_1500} reps={args.reps} total_cfgs={len(specs)} total_runs={len(specs)*args.reps}")
        for part, items in by_part.items():
            print(f"  [{part}] ({len(items)} cfgs):")
            for sp in items:
                span_mib = (sp["nc"] * sp["pool_size"] * 2048) / (1024 * 1024)
                print(
                    f"    {sp['cfg']:38s} | nc={sp['nc']} hc={sp['hc']} {sp['fk']}/{l2_bytes_for(sp['fk'], sp['plen'])}B "
                    f"oq={sp['rx_desc']:4d} pool={sp['pool_size']:5d}(c={sp['pool_cache']:3d}, {span_mib:5.1f}MiB) "
                    f"r_eff={sp['r_eff']:4d} credit={sp['credit']:12s} B={sp['credit_batch']:3d}"
                )
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if args.deploy:
        deploy_binaries()

    env_before = read_env_snapshot()
    assert env_before["saturn"]["cpu40_gov"] == "performance", f"cpu40 governor not performance: {env_before['saturn']}"
    assert env_before["saturn"]["uncore_khz"].get("package_01_die_01", 0) >= 2_190_000, f"uncore not pinned: {env_before['saturn']}"

    cal_work = calibrate_host_app_work()
    print("APP_WORK CAL:", json.dumps(cal_work), flush=True)

    stack = Stack()
    stack.prepare_card_vfio()

    sh(MERC_SSH + ["pkill -9 mrv214_client 2>/dev/null || true"], check=False)
    mp = subprocess.Popen(
        MERC_SSH + [f"{MERC_BIN} --port {MERC_CTRL_PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    ready_line = mp.stdout.readline().strip()
    print("MERC READY:", ready_line, flush=True)
    if "MRV214_CLIENT READY" not in ready_line:
        raise RuntimeError(f"mercury client failed to start: {ready_line}")

    flow_map = {}
    needed_probes = sorted({(sp["nc"], sp["fk"]) for sp in specs if sp["nc"] > 1})
    for nc, fk in needed_probes:
        flow_map[(nc, fk)] = probe_balanced_flows(stack, nc, fk)

    is_smoke = (args.mode == "smoke")
    is_supp = (args.mode == "supplement")
    out_attempts = (LOG_DIR / "smoke.jsonl") if is_smoke else ATTEMPTS_PATH
    out_runs = (LOG_DIR / "smoke.jsonl") if is_smoke else RUNS_PATH
    out_env = (LOG_DIR / "env_smoke.json") if is_smoke else ((LOG_DIR / "env_supplement.json") if is_supp else ENV_PATH)
    done = 0
    failed = 0
    clock_retries = 0
    run_gen = 1

    try:
        for rep in range(args.reps):
            # Interleaved replication order: reverse on odd reps to cancel thermal/drift bias
            order = specs if (rep % 2 == 0) else list(reversed(specs))
            for idx, sp in enumerate(order, 1):
                retries_here = 0
                while True:
                    run_gen += 1
                    rec, int_errs, clk_errs = run_one(
                        stack, sp, rep, run_gen, flow_map, cal_work, meas_s=args.meas_s
                    )
                    rec["clock_retries_before_accept"] = retries_here
                    append_jsonl(out_attempts, rec)

                    if not int_errs and not clk_errs:
                        if not is_smoke:
                            append_jsonl(out_runs, rec)
                        done += 1
                        r = rec["rates"]
                        hp = rec["host_pmu"]
                        print(
                            f"[rep={rep+1}/{args.reps} {idx:2d}/{len(order)}] {sp['cfg']:38s} | "
                            f"rx={r['host_rx_mpps']:6.2f}M ({r['host_wire_gbps']:5.1f}G) "
                            f"cyc={r['io_cycles_per_pkt']:5.1f} ddio={hp['ddio_hit_pct']:5.1f}% "
                            f"imc={hp['imc_rd_gbps']:4.1f}/{hp['imc_wr_gbps']:4.1f}G "
                            f"cpuWr={hp['iio1_cpu_wr_mops']:5.2f}M stalls={r['steady_credit_stalls']} OK",
                            flush=True,
                        )
                        break

                    if not int_errs and clk_errs and retries_here < 3:
                        retries_here += 1
                        clock_retries += 1
                        print(f"  [CLOCK_RETRY #{retries_here}] {sp['cfg']} rep={rep} errs={clk_errs}", flush=True)
                        time.sleep(3.0)
                        continue

                    failed += 1
                    print(
                        f"[rep={rep+1}/{args.reps} {idx:2d}/{len(order)}] {sp['cfg']:38s} | "
                        f"FAIL int={int_errs} clk={clk_errs}",
                        flush=True,
                    )
                    break
    finally:
        try:
            tcp_cmd("mercury", MERC_CTRL_PORT, "QUIT", timeout=2.0)
        except Exception:
            pass
        mp.terminate()
        try:
            mp.wait(timeout=3)
        except Exception:
            mp.kill()
        cleanup_merc = args.cleanup_mercury or (args.mode == "all")
        restore_info = stack.restore_baseline(cleanup_mercury=cleanup_merc)
        print("POST-RESTORE: waiting 21 s for delayed EDAC/AER/SEI-UER audit...", flush=True)
        time.sleep(21.0)
        env_after = read_env_snapshot()
        deltas = {
            "saturn_edac_ue_delta": env_after["saturn"]["edac_ue_sum"] - env_before["saturn"]["edac_ue_sum"],
            "saturn_rp_aer_delta": {
                k: env_after["saturn"]["rp_aer"][k] - env_before["saturn"]["rp_aer"][k]
                for k in env_before["saturn"]["rp_aer"]
            },
            "cn106_ea0_ue_delta": int(env_after["cn106"].get("ea0_ue", 0)) - int(env_before["cn106"].get("ea0_ue", 0)),
            "cn106_mc0_ue_delta": int(env_after["cn106"].get("mc0_ue", 0)) - int(env_before["cn106"].get("mc0_ue", 0)),
            "cn106_edac_ue_delta": int(env_after["cn106"].get("edac_ue", 0)) - int(env_before["cn106"].get("edac_ue", 0)),
            "cn106_sei_uer_delta": int(env_after["cn106"].get("sei_uer", 0)) - int(env_before["cn106"].get("sei_uer", 0)),
        }
        out_env.write_text(json.dumps({
            "before": env_before,
            "after": env_after,
            "deltas": deltas,
            "restore": restore_info,
            "cal_work": cal_work,
            "flow_map": {f"{k[0]}_{k[1]}": v for k, v in flow_map.items()},
            "done": done,
            "failed": failed,
            "clock_retries": clock_retries,
        }, indent=2) + "\n")
        print(f"EDAC/AER DELTAS: {json.dumps(deltas)}", flush=True)
        print(f"FINISHED: done={done} failed={failed} clock_retries={clock_retries}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
