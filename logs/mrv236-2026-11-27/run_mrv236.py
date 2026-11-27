#!/usr/bin/env python3
"""MRV-236 experiment harness: is card-core cache residency of the target ring
cell on host-write arrival the trigger of the 13.1 M/s inbound bridge
serialization?

Single-variable arms (MRV-82), zero SDP (MRV-49), zero A5 (--poll nc forbidden
until explicit user approval on MRV-236):
  - Pre-check gate: B vs A1 (WC2 W=64, --next-publish batch vs exit).
  - Group A (WC2 lcore 3-4, W=64, 1e7 rounds, batch=1): B, A1, A2, A3, A4.
  - Group S (WC1/shadow, W=64, 1e7 rounds, --next-publish exit): B1, S1, S2, S3.
  - Group L (WC2 lcore 3-4, W=1, 2e6 rounds): L_B, L_A2, L_A3, L_A4.
  - Controls: WC4_B (lcore 3-6, W=64, 1e7 rounds), idle_1s_wc2, idle_1s_wc4.

Modes:
  --self-test --dry-run   Pure offline validation on Socket 0 (zero hardware access).
  --deploy                Upload aarch64 binary to /root/bench/inline_reflector.mrv236.
  --smoke                 EL0 civac/wfe probe + card PMU discovery + B vs A1 gate +
                          quick A3/A4/S2 check + full baseline restore & 21 s EDAC/AER audit.
  --full                  Full n=3 round-rotated interleaved experiment + 21 s audit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pathlib
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv236")
LOG_DIR = ROOT / "logs/mrv236-2026-11-27"
STAMP_DIR = LOG_DIR / "stamps"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
ENV_JSON = LOG_DIR / "env.json"
SMOKE_PROBE_JSON = LOG_DIR / "smoke_probe.json"
SMOKE_RUNS_JSONL = LOG_DIR / "smoke_runs.jsonl"
SMOKE_ENV_JSON = LOG_DIR / "smoke_env.json"

HOST_BIN = ROOT / "testbed/target/release/inline_pingpong"
CARD_LOCAL_BIN = ROOT / "testbed/target/aarch64-unknown-linux-gnu/release/inline_reflector"
CARD_REMOTE_BIN = "/root/bench/inline_reflector.mrv236"
CARD_DEFAULT_BIN = "/root/bench/inline_reflector"
CARD_HOST = "root@10.10.0.2"
CARD_SYNC_ADDR = "10.10.0.2:7527"
CARD_SYNC_PORT = 7527

REPS = 3
MIN_CPU_GHZ = 3.80
CELL_BYTES = 64
ROUNDS_W64 = 10_000_000
ROUNDS_W1 = 2_000_000
WARMUP_W64 = 5_000_000
WARMUP_W1 = 10_000
SETTLE_S = 12.0
RECORD_CAP = 4000
RECORD_SKIP_W64 = 1_000_000
RECORD_SKIP_W1 = 200_000
AUDIT_WAIT_S = 21.0
A1_BREAKOUT_MOPS = 25.0


@dataclass(frozen=True)
class ArmSpec:
    arm: str
    group: str
    lcores: str
    ring_cores: int
    window: int
    rounds: int
    warmup: int
    next_publish: str = "batch"
    poll: str = "ldar"
    roles: str | None = None
    shadow: str | None = None
    stamp: bool = True
    stamp_sparse: bool = True
    is_idle: bool = False
    idle_secs: float = 1.0
    desc: str = ""

    def record_skip(self) -> int:
        if self.rounds <= 200_000:
            return min(self.rounds // 4, 20_000)
        return RECORD_SKIP_W1 if self.window == 1 else RECORD_SKIP_W64

    def card_args(self, extra_pmu: list[str] | None = None) -> list[str]:
        args = [
            CARD_REMOTE_BIN,
            "-l",
            self.lcores,
            "-a",
            "0002:01:00.2",
            "--file-prefix=mrv236",
            "--",
            "--store",
            "--store-wc",
            "--sync",
            CARD_SYNC_ADDR,
            "--next-publish",
            self.next_publish,
            "--poll",
            self.poll,
        ]
        if self.roles:
            args += ["--roles", self.roles]
        if self.shadow:
            args += ["--shadow", self.shadow]
        if self.stamp:
            args.append("--exp-stamp")
        if self.stamp and self.stamp_sparse:
            args.append("--exp-stamp-sparse")
        if extra_pmu:
            args += ["--extra-pmu", ",".join(extra_pmu)]
        return args

    def host_args(
        self,
        stamp_csv: pathlib.Path | None = None,
        rounds_override: int | None = None,
        warmup_override: int | None = None,
    ) -> list[str]:
        rnds = rounds_override if rounds_override is not None else self.rounds
        wm = warmup_override if warmup_override is not None else self.warmup
        cmd = [
            str(HOST_BIN),
            "-l",
            "40",
            "-a",
            "0000:99:02.0",
            "--file-prefix=mrv236",
            "--",
            "--reply",
            "ring",
            "--window",
            str(self.window),
            "--rounds",
            str(rnds),
            "--warmup",
            str(wm),
            "--pmu",
            "--sync",
            CARD_SYNC_ADDR,
        ]
        if self.stamp:
            cmd.append("--stamp")
            if self.stamp_sparse:
                cmd.append("--stamp-sparse")
            skip = min(rnds // 4, self.record_skip())
            cmd += ["--record-cap", str(RECORD_CAP), "--record-skip", str(skip)]
            if stamp_csv is not None:
                cmd += ["--stamp-csv", str(stamp_csv)]
        return cmd


def build_arm_catalog() -> list[ArmSpec]:
    """Complete MRV-236 single-variable matrix (strictly excludes A5 --poll nc and SDP)."""
    return [
        # Group A: baseline B = WC2, W=64, ldar, next_publish=batch
        ArmSpec(
            arm="B",
            group="A",
            lcores="3-4",
            ring_cores=2,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="batch",
            poll="ldar",
            desc="WC2 W=64 baseline (ldar, next_publish=batch)",
        ),
        ArmSpec(
            arm="A1",
            group="A",
            lcores="3-4",
            ring_cores=2,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="exit",
            poll="ldar",
            desc="WC2 W=64 next_offset only on exit (removes shared control-line bounce)",
        ),
        ArmSpec(
            arm="A2",
            group="A",
            lcores="3-4",
            ring_cores=2,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="batch",
            poll="ldr",
            desc="WC2 W=64 plain ldr head poll (no acquire barrier)",
        ),
        ArmSpec(
            arm="A3",
            group="A",
            lcores="3-4",
            ring_cores=2,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="batch",
            poll="wfe",
            desc="WC2 W=64 ldaxr+wfe head poll (removes tight re-read snoops)",
        ),
        ArmSpec(
            arm="A4",
            group="A",
            lcores="3-4",
            ring_cores=2,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="batch",
            poll="civac",
            desc="WC2 W=64 dc civac eviction after empty poll (no core holds line)",
        ),
        # Group S (primary unperturbed, zero single-core stamp overhead):
        # baseline B1 = WC1, W=64, next_publish=exit (~35 M/s steady fast state)
        ArmSpec(
            arm="B1",
            group="S",
            lcores="3",
            ring_cores=1,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="exit",
            poll="ldar",
            stamp=False,
            stamp_sparse=False,
            desc="WC1 W=64 single-core baseline (next_publish=exit, unstamped steady fast state)",
        ),
        ArmSpec(
            arm="S1",
            group="S",
            lcores="3-4",
            ring_cores=1,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="exit",
            poll="ldar",
            roles="ring,shadow",
            shadow="private",
            stamp=False,
            stamp_sparse=False,
            desc="WC1 + shadow core reading SHADOW_NEXT, spinning on private 128 B line (unstamped)",
        ),
        ArmSpec(
            arm="S2",
            group="S",
            lcores="3-4",
            ring_cores=1,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="exit",
            poll="ldar",
            roles="ring,shadow",
            shadow="front",
            stamp=False,
            stamp_sparse=False,
            desc="WC1 + shadow core spinning on frontier ring cell front+0 (unstamped)",
        ),
        ArmSpec(
            arm="S3",
            group="S",
            lcores="3-4",
            ring_cores=1,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="exit",
            poll="ldar",
            roles="ring,shadow",
            shadow="front+4",
            stamp=False,
            stamp_sparse=False,
            desc="WC1 + shadow core spinning on frontier+4 ring cell (unstamped)",
        ),
        # Paired sparse-stamped Group S arms for per-cell f and landing-gap CDF (fast vs serialized WC1)
        ArmSpec(
            arm="B1_sp",
            group="S_sp",
            lcores="3",
            ring_cores=1,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="exit",
            poll="ldar",
            stamp=True,
            stamp_sparse=True,
            desc="WC1 W=64 single-core baseline with sparse per-cell stamps",
        ),
        ArmSpec(
            arm="S2_sp",
            group="S_sp",
            lcores="3-4",
            ring_cores=1,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="exit",
            poll="ldar",
            roles="ring,shadow",
            shadow="front",
            stamp=True,
            stamp_sparse=True,
            desc="WC1 + shadow front+0 with sparse per-cell stamps",
        ),
        # Group L: single-message RTT cost (W=1, 2e6 rounds, WC2)
        ArmSpec(
            arm="L_B",
            group="L",
            lcores="3-4",
            ring_cores=2,
            window=1,
            rounds=ROUNDS_W1,
            warmup=WARMUP_W1,
            next_publish="batch",
            poll="ldar",
            desc="WC2 W=1 baseline RTT (ldar)",
        ),
        ArmSpec(
            arm="L_A2",
            group="L",
            lcores="3-4",
            ring_cores=2,
            window=1,
            rounds=ROUNDS_W1,
            warmup=WARMUP_W1,
            next_publish="batch",
            poll="ldr",
            desc="WC2 W=1 RTT cost of --poll ldr",
        ),
        ArmSpec(
            arm="L_A3",
            group="L",
            lcores="3-4",
            ring_cores=2,
            window=1,
            rounds=ROUNDS_W1,
            warmup=WARMUP_W1,
            next_publish="batch",
            poll="wfe",
            desc="WC2 W=1 RTT cost of --poll wfe",
        ),
        ArmSpec(
            arm="L_A4",
            group="L",
            lcores="3-4",
            ring_cores=2,
            window=1,
            rounds=ROUNDS_W1,
            warmup=WARMUP_W1,
            next_publish="batch",
            poll="civac",
            desc="WC2 W=1 RTT cost of --poll civac",
        ),
        # Controls: WC4 W=64 baseline aligned with MRV-75 + 1 s card idle PMU baselines
        ArmSpec(
            arm="WC4_B",
            group="ctrl",
            lcores="3-6",
            ring_cores=4,
            window=64,
            rounds=ROUNDS_W64,
            warmup=WARMUP_W64,
            next_publish="batch",
            poll="ldar",
            desc="WC4 W=64 control aligned with MRV-75",
        ),
        ArmSpec(
            arm="idle_1s_wc2",
            group="idle",
            lcores="3-4",
            ring_cores=2,
            window=0,
            rounds=0,
            warmup=0,
            next_publish="batch",
            poll="ldar",
            stamp=False,
            stamp_sparse=False,
            is_idle=True,
            idle_secs=1.0,
            desc="Card idle 1 s PMU baseline (WC2 lcore 3-4)",
        ),
        ArmSpec(
            arm="idle_1s_wc4",
            group="idle",
            lcores="3-6",
            ring_cores=4,
            window=0,
            rounds=0,
            warmup=0,
            next_publish="batch",
            poll="ldar",
            stamp=False,
            stamp_sparse=False,
            is_idle=True,
            idle_secs=1.0,
            desc="Card idle 1 s PMU baseline (WC4 lcore 3-6)",
        ),
    ]


def build_interleaved_schedule(arms: list[ArmSpec], reps: int = REPS) -> list[tuple[int, ArmSpec]]:
    """Build round-rotated interleaved schedule with B and A1 first in rep 0 (pre-check gate)."""
    by_name = {a.arm: a for a in arms}
    assert "B" in by_name and "A1" in by_name, "Catalog must contain B and A1 for pre-check gate"
    n = len(arms)
    schedule: list[tuple[int, ArmSpec]] = []
    for rep in range(reps):
        if rep == 0:
            # Pre-check gate: B then A1 first, then remaining arms in catalog order.
            ordered = [by_name["B"], by_name["A1"]] + [a for a in arms if a.arm not in ("B", "A1")]
        else:
            shift = (rep * max(1, n // reps)) % n
            rotated = arms[shift:] + arms[:shift]
            ordered = list(reversed(rotated)) if rep % 2 == 1 else rotated
        for spec in ordered:
            schedule.append((rep, spec))
    return schedule


def evaluate_precheck_gate(b_rec: dict, a1_rec: dict) -> dict:
    """Evaluate B vs A1 pre-check gate per MRV-236 protocol."""
    b_mops = float(b_rec["mops"])
    a1_mops = float(a1_rec["mops"])
    b_tot = max(1, int(b_rec["rounds"]) + int(b_rec["warmup"]))
    a1_tot = max(1, int(a1_rec["rounds"]) + int(a1_rec["warmup"]))
    b_snp = b_rec["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / b_tot
    a1_snp = a1_rec["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / a1_tot
    a1_broke_out = a1_mops >= A1_BREAKOUT_MOPS
    verdict = (
        "A1_BROKE_OUT_CONFOUNDER_PRIMARY" if a1_broke_out else "A1_STILL_SERIALIZED_PROCEED_PRIMARY"
    )
    return {
        "b_mops": round(b_mops, 3),
        "b_snp_per_msg": round(b_snp, 3),
        "a1_mops": round(a1_mops, 3),
        "a1_snp_per_msg": round(a1_snp, 3),
        "a1_broke_out": a1_broke_out,
        "verdict": verdict,
        "continue_full_matrix": True,
    }


def parse_card_pmu_probe(probe_info: dict) -> list[str]:
    """Parse read-only /sys/bus/event_source/devices/ probe and return safe --extra-pmu specs.

    Only uses perf sysfs `events/*` entries for PEM/IOBN PMUs if present; never touches CSRs.
    """
    specs: list[str] = []
    devices = probe_info.get("devices", {})
    for dev, info in sorted(devices.items()):
        low = dev.lower()
        if not any(tok in low for tok in ("pem", "iobn", "pcie")):
            continue
        events = info.get("events", {})
        for ev_name, ev_raw in sorted(events.items()):
            ev_low = ev_name.lower()
            if not any(tok in ev_low for tok in ("wr", "write", "inb", "occ", "req", "ncb", "tlp")):
                continue
            # Parse sysfs format like "event=0x14" or "config=0x08"
            cfg = None
            for part in ev_raw.replace(" ", "").split(","):
                if part.startswith("event=") or part.startswith("config="):
                    val = part.split("=", 1)[1]
                    try:
                        cfg = int(val, 0)
                    except ValueError:
                        cfg = None
            if cfg is not None:
                specs.append(f"{dev}:{ev_name}={hex(cfg)}")
                if len(specs) >= 4:
                    return specs
    return specs


def summarize_stamps(csv_path: pathlib.Path, card_hz: float) -> dict:
    """Summarize MRV-236 11-column sparse per-cell stamp CSV:
    - held fraction f = P(card_waited_polls > 0)
    - landing-interval distribution (card_t_in[seq] - card_t_in[seq-1]) for held vs unheld
    - first-poll-to-complete wait (card_t_in - card_t_first_poll)
    - per-core breakdown (card_core / card_ring_idx)
    - h2c / c2h / card / rtt decomposition via BAR4 clock sync.
    """
    with csv_path.open() as f:
        hdr = f.readline().strip()
        parts = dict(tok.split("=") for tok in hdr.lstrip("# ").split())
        tsc_hz = float(parts["tsc_hz"])
        sb_t, sb_c, sb_rtt = [float(x) for x in parts["sb"].split(",")]
        sa_t, sa_c, sa_rtt = [float(x) for x in parts["sa"].split(",")]
        rows = list(csv.DictReader(f))

    if not rows:
        return {}

    dt_host = sa_t - sb_t
    dt_card = sa_c - sb_c
    slope = (dt_host / dt_card) if dt_card > 0 else (tsc_hz / card_hz)
    nom_slope = tsc_hz / card_hz
    ppm = (slope / nom_slope - 1.0) * 1e6 if nom_slope > 0 else 0.0
    ns_per_host = 1e9 / tsc_hz
    ns_per_card = 1e9 / card_hz

    def to_host(c_ts: float) -> float:
        return sb_t + slope * (c_ts - sb_c)

    def pct(vals: list[float], q: float) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        return s[int((len(s) - 1) * q)]

    def dist_stats(vals: list[float]) -> dict:
        if not vals:
            return {"n": 0, "p50_ns": 0.0, "p90_ns": 0.0, "p99_ns": 0.0, "mean_ns": 0.0}
        return {
            "n": len(vals),
            "p50_ns": round(pct(vals, 0.50), 2),
            "p90_ns": round(pct(vals, 0.90), 2),
            "p99_ns": round(pct(vals, 0.99), 2),
            "mean_ns": round(statistics.mean(vals), 2),
        }

    h2c: list[float] = []
    c2h: list[float] = []
    card_t: list[float] = []
    rtt_s: list[float] = []
    wait_ns_all: list[float] = []
    wait_ns_held: list[float] = []
    cw_all: list[int] = []
    hw_all: list[int] = []
    d_cin_all: list[float] = []
    d_cin_held: list[float] = []
    d_cin_unheld: list[float] = []
    per_core: dict[str, dict[str, int]] = {}
    prev_cin: dict[int, float] = {}

    for r in rows:
        seq = int(r["seq"])
        st = float(r["send_tsc"])
        sn = float(r["seen_tsc"])
        cfp = float(r.get("card_t_first_poll", r["card_t_in"]))
        cin = float(r["card_t_in"])
        cout = float(r["card_t_out"])
        cwp = int(r["card_waited_polls"])
        hwp = int(r["host_waited_polls"])
        ccore = str(r.get("card_core", "0"))
        held = cwp > 0

        cin_h = to_host(cin)
        cout_h = to_host(cout)
        h2c.append((cin_h - st) * ns_per_host)
        c2h.append((sn - cout_h) * ns_per_host)
        card_t.append((cout - cin) * ns_per_card)
        rtt_s.append((sn - st) * ns_per_host)
        w_ns = max(0.0, (cin - cfp) * ns_per_card)
        wait_ns_all.append(w_ns)
        if held:
            wait_ns_held.append(w_ns)
        cw_all.append(cwp)
        hw_all.append(hwp)

        pc = per_core.setdefault(ccore, {"n": 0, "held": 0})
        pc["n"] += 1
        if held:
            pc["held"] += 1

        if (seq - 1) in prev_cin:
            gap_ns = (cin - prev_cin[seq - 1]) * ns_per_card
            d_cin_all.append(gap_ns)
            if held:
                d_cin_held.append(gap_ns)
            else:
                d_cin_unheld.append(gap_ns)
        prev_cin[seq] = cin

    n_total = len(rows)
    n_held = sum(1 for x in cw_all if x > 0)
    f_held = n_held / n_total

    per_core_summary = {
        core: {"n": v["n"], "held": v["held"], "f_held": round(v["held"] / max(1, v["n"]), 4)}
        for core, v in sorted(per_core.items())
    }

    return {
        "n": n_total,
        "ppm": round(ppm, 3),
        "sync_rtt_before_ns": round(sb_rtt * ns_per_host, 1),
        "sync_rtt_after_ns": round(sa_rtt * ns_per_host, 1),
        "f_held": round(f_held, 4),
        "n_held": n_held,
        "n_unheld": n_total - n_held,
        "cw_gt0_pct": round(100.0 * f_held, 2),
        "cw_mean": round(statistics.mean(cw_all), 2),
        "hw_gt0_pct": round(100.0 * sum(1 for x in hw_all if x > 0) / n_total, 2),
        "first_poll_to_in_all": dist_stats(wait_ns_all),
        "first_poll_to_in_held": dist_stats(wait_ns_held),
        "landing_gap_all": dist_stats(d_cin_all),
        "landing_gap_held": dist_stats(d_cin_held),
        "landing_gap_unheld": dist_stats(d_cin_unheld),
        "d_cin_neg_pct": round(100.0 * sum(1 for x in d_cin_all if x < 0) / max(1, len(d_cin_all)), 2),
        "h2c_p50_ns": round(pct(h2c, 0.50), 1),
        "h2c_mean_ns": round(statistics.mean(h2c), 1),
        "h2c_p90_ns": round(pct(h2c, 0.90), 1),
        "c2h_p50_ns": round(pct(c2h, 0.50), 1),
        "c2h_mean_ns": round(statistics.mean(c2h), 1),
        "c2h_p90_ns": round(pct(c2h, 0.90), 1),
        "card_p50_ns": round(pct(card_t, 0.50), 1),
        "rtt_p50_ns": round(pct(rtt_s, 0.50), 1),
        "rtt_mean_ns": round(statistics.mean(rtt_s), 1),
        "per_core": per_core_summary,
    }


def compute_edac_aer_deltas(host_before: dict, host_after: dict, card_before: dict, card_after: dict) -> dict:
    edac_delta = {}
    for k, v in card_after["edac"].items():
        b = card_before["edac"].get(k, {"ce": 0, "ue": 0})
        edac_delta[k] = {"ce_delta": v["ce"] - b["ce"], "ue_delta": v["ue"] - b["ue"]}
    aer_delta = {k: host_after["aer"][k] - host_before["aer"][k] for k in host_after["aer"]}
    all_zero = (
        all(v["ce_delta"] == 0 and v["ue_delta"] == 0 for v in edac_delta.values())
        and all(v == 0 for v in aer_delta.values())
    )
    return {"edac_delta": edac_delta, "aer_delta": aer_delta, "all_zero": all_zero}


# ---- Hardware helpers (ONLY invoked in --deploy / --smoke / --full) ----


def ssh_card(cmd: str) -> str:
    return subprocess.check_output(["ssh", "-o", "BatchMode=yes", CARD_HOST, cmd], text=True)


def wait_for_turbo(min_khz: int = 3_850_000, max_wait_s: float = 120.0) -> int:
    t0 = time.monotonic()
    while True:
        khz = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
        if khz >= min_khz or time.monotonic() - t0 > max_wait_s:
            return khz
        time.sleep(1.0)


def check_host_env() -> dict:
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    freq = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    if gov != "performance":
        raise RuntimeError(f"cpu40 governor is {gov!r}, expected 'performance'")
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        khz = int(p.read_text().strip())
        uncore[p.parent.name] = khz
        if khz != 2_200_000:
            raise RuntimeError(f"uncore {p.parent.name} = {khz} kHz, expected 2200000")
    aer = {}
    for name in ("aer_dev_correctable", "aer_dev_fatal", "aer_dev_nonfatal"):
        p = pathlib.Path(f"/sys/bus/pci/devices/0000:99:00.0/{name}")
        tot = 0
        for line in p.read_text().splitlines():
            if line.startswith("TOTAL_ERR_"):
                tot = int(line.split()[-1])
        aer[name] = tot
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    if paranoid > 0:
        raise RuntimeError(f"perf_event_paranoid = {paranoid}, expected <= 0")
    return {
        "cpu40_governor": gov,
        "cpu40_cur_khz": freq,
        "uncore_khz": uncore,
        "aer": aer,
        "perf_event_paranoid": paranoid,
    }


def read_card_state() -> dict:
    script = r"""python3 - <<'PY'
import glob, json, os, mmap, ctypes, subprocess
edac = {}
for p in sorted(glob.glob("/sys/devices/system/edac/*/*/ce_count")):
    d = os.path.dirname(p)
    key = os.path.relpath(d, "/sys/devices/system/edac")
    edac[key] = {
        "ce": int(open(os.path.join(d, "ce_count")).read().strip()),
        "ue": int(open(os.path.join(d, "ue_count")).read().strip()),
    }
# Single 64-bit loads of known PEM0 registers ONLY (never slice mmap!)
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x8e0000000000)
q = (ctypes.c_uint64 * 8192).from_buffer(m)
bar4 = {str(i): f"0x{q[(0x700 + i*8)>>3]:x}" for i in range(16)}
outb = {str(i): f"0x{q[(0x4000 + i*16)>>3]:x}" for i in range(248, 256)}
del q; m.close(); os.close(fd)

pem_wc_loaded = "pem_wc" in open("/proc/modules").read()
eth0_out = subprocess.check_output(["ethtool", "eth0"], text=True)
eth0_speed = next((l.split(":",1)[1].strip() for l in eth0_out.splitlines() if "Speed:" in l), "unknown")
eth0_link = next((l.split(":",1)[1].strip() for l in eth0_out.splitlines() if "Link detected:" in l), "unknown")
tmux_ls = subprocess.run(["tmux", "ls"], capture_output=True, text=True).stdout.strip()
print(json.dumps({
    "edac": edac,
    "bar4_index": bar4,
    "outbound_248_255": outb,
    "pem_wc_loaded": pem_wc_loaded,
    "eth0_speed": eth0_speed,
    "eth0_link": eth0_link,
    "tmux_ls": tmux_ls,
}))
PY"""
    return json.loads(ssh_card(script).strip())


def deploy_card_binary() -> dict:
    if not CARD_LOCAL_BIN.exists():
        raise FileNotFoundError(f"Missing compiled aarch64 card binary: {CARD_LOCAL_BIN}")
    local_sha = hashlib.sha256(CARD_LOCAL_BIN.read_bytes()).hexdigest()
    default_sha_before = ssh_card(f"sha256sum {CARD_DEFAULT_BIN}").split()[0]
    tmp_remote = f"{CARD_REMOTE_BIN}.tmp.{os.getpid()}"
    subprocess.run(
        ["scp", "-o", "BatchMode=yes", str(CARD_LOCAL_BIN), f"{CARD_HOST}:{tmp_remote}"],
        check=True,
    )
    ssh_card(f"chmod +x {tmp_remote} && mv -f {tmp_remote} {CARD_REMOTE_BIN}")
    remote_sha = ssh_card(f"sha256sum {CARD_REMOTE_BIN}").split()[0]
    default_sha_after = ssh_card(f"sha256sum {CARD_DEFAULT_BIN}").split()[0]
    if remote_sha != local_sha:
        raise RuntimeError(f"Deploy checksum mismatch: local={local_sha} remote={remote_sha}")
    if default_sha_after != default_sha_before:
        raise RuntimeError("Default /root/bench/inline_reflector changed unexpectedly!")
    print(f"Deployed {CARD_REMOTE_BIN} (sha256={remote_sha[:12]}, default reflector untouched)", flush=True)
    return {"remote_bin": CARD_REMOTE_BIN, "sha256": remote_sha, "default_sha256": default_sha_after}


def probe_card_el0_ops() -> str:
    """Verify EL0 dc civac and ldaxr/wfe/clrex execute cleanly without SIGILL."""
    out = ssh_card(f"{CARD_REMOTE_BIN} --probe-el0-ops").strip()
    if not out.startswith("OK el0_ops=civac,wfe"):
        raise RuntimeError(f"EL0 probe unexpected output: {out!r}")
    print(f"Card EL0 ops probe PASS: {out}", flush=True)
    return out


def probe_card_pmus() -> dict:
    """Inspect /sys/bus/event_source/devices/ and perf list for L2D_CACHE_INVAL (0x58) and PEM/IOBN PMUs."""
    script = r"""python3 - <<'PY'
import glob, json, os, subprocess

devices = {}
base = "/sys/bus/event_source/devices"
for dev in sorted(os.listdir(base)):
    dpath = os.path.join(base, dev)
    info = {}
    tpath = os.path.join(dpath, "type")
    if os.path.exists(tpath):
        info["type"] = open(tpath).read().strip()
    cpath = os.path.join(dpath, "cpumask")
    if os.path.exists(cpath):
        info["cpumask"] = open(cpath).read().strip()
    evdir = os.path.join(dpath, "events")
    if os.path.isdir(evdir):
        evs = {}
        for name in sorted(os.listdir(evdir)):
            ep = os.path.join(evdir, name)
            if os.path.isfile(ep):
                try:
                    evs[name] = open(ep).read().strip()
                except OSError:
                    pass
        info["events"] = evs
    devices[dev] = info

import ctypes, shutil
perf_bin = shutil.which("perf")
perf_inval = []
if perf_bin:
    perf_inval = subprocess.run(
        ["sh", "-c", f"{perf_bin} list 2>/dev/null | grep -iE 'l2d_cache_inval|pem|iobn' | head -n 40"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()

# Directly probe perf_event_open syscall on armv8_pmuv3 config=0x58 (L2D_CACHE_INVAL)
SYS_perf_event_open = 241  # aarch64
libc = ctypes.CDLL(None, use_errno=True)
class PerfAttr(ctypes.Structure):
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
        ("__reserved_2", ctypes.c_uint16),
        ("aux_sample_size", ctypes.c_uint32),
        ("__reserved_3", ctypes.c_uint32),
        ("sig_data", ctypes.c_uint64),
        ("config3", ctypes.c_uint64),
    ]
arm_type = int(devices.get("armv8_pmuv3", {}).get("type", "8"))
attr = PerfAttr()
attr.type = arm_type
attr.size = ctypes.sizeof(PerfAttr)
attr.config = 0x58
fd = libc.syscall(SYS_perf_event_open, ctypes.byref(attr), -1, 3, -1, 0)
if fd >= 0:
    os.close(fd)
    probe_0x58_rc = 0
    probe_0x58_msg = "perf_event_open(armv8_pmuv3, config=0x58, cpu=3) succeeded"
else:
    err = ctypes.get_errno()
    probe_0x58_rc = err
    probe_0x58_msg = f"perf_event_open errno={err} ({os.strerror(err)})"

print(json.dumps({
    "devices": devices,
    "perf_bin": perf_bin,
    "perf_list_matches": perf_inval,
    "perf_stat_0x58_rc": probe_0x58_rc,
    "perf_stat_0x58_stderr": probe_0x58_msg,
}))
PY"""
    info = json.loads(ssh_card(script).strip())
    extra_specs = parse_card_pmu_probe(info)
    info["discovered_extra_pmu_specs"] = extra_specs
    dev_names = sorted(info["devices"].keys())
    print(f"Card /sys/bus/event_source/devices/: {dev_names}", flush=True)
    print(f"Card perf stat 0x58 rc={info['perf_stat_0x58_rc']}, extra_pmu_specs={extra_specs}", flush=True)
    return info


def stop_card_reflector():
    ssh_card(
        "pkill -INT -f '^/root/bench/inline_reflector' 2>/dev/null || true; "
        "for _ in $(seq 1 60); do pgrep -f '^/root/bench/inline_reflector' >/dev/null || break; sleep 0.05; done"
    )


def start_card_arm(spec: ArmSpec, extra_pmu: list[str] | None = None, settle_s: float = 1.5):
    cmd_str = " ".join(spec.card_args(extra_pmu))
    script = f"""
pkill -INT -f '^/root/bench/inline_reflector' 2>/dev/null || true
for _ in $(seq 1 60); do pgrep -f '^/root/bench/inline_reflector' >/dev/null || break; sleep 0.05; done
rm -rf /var/run/dpdk/mrv236 /dev/hugepages/mrv236map_*
sleep 0.1
lsmod | grep -q pem_wc || insmod /root/bench/pem_wc.ko
rm -f /tmp/mrv236_card.log
nohup {cmd_str} </dev/null > /tmp/mrv236_card.log 2>&1 &
for _ in $(seq 1 80); do
  grep -q "hosts lease the slot at" /tmp/mrv236_card.log 2>/dev/null && exit 0
  sleep 0.1
done
cat /tmp/mrv236_card.log >&2; exit 1
"""
    ssh_card(script)
    if settle_s > 0:
        time.sleep(settle_s)


def measure_card_idle(spec: ArmSpec, rep: int, env_info: dict) -> dict:
    with socket.create_connection(("10.10.0.2", CARD_SYNC_PORT), timeout=5.0) as s:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        f = s.makefile("rwb", buffering=0)
        f.write(b"PMU_START\n")
        resp = f.readline().decode().strip()
        if resp != "OK":
            raise RuntimeError(f"idle PMU_START failed: {resp}")
        time.sleep(spec.idle_secs)
        f.write(b"PMU_STOP\n")
        line = f.readline().decode().strip()
    cp = json.loads(line)
    if cp["min_pct"] < 99.9:
        raise RuntimeError(f"idle card PMU mux: {cp}")
    wall = max(1e-9, cp["wall_s"])
    card_ghzs = [round(c["cpu_cycles"] / wall / 1e9, 4) for c in cp["cores"]]
    rec = {
        "issue": "MRV-236",
        "arm": spec.arm,
        "group": spec.group,
        "rep": rep,
        "batch": 0,
        "concurrency": 0,
        "lcores": spec.lcores,
        "ring_cores": spec.ring_cores,
        "target_secs": spec.idle_secs,
        "card_core_ghz": card_ghzs,
        "card_pmu": cp,
        "env": env_info,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    snp_s = cp["tad"]["tad_snp_msh_out_any"]["val"] / wall / 1e6
    print(f"[{spec.group:4s}] {spec.arm:12s} r{rep} | idle {wall:.2f}s | TAD_snp={snp_s:5.2f} M/s | card_ghz={card_ghzs}", flush=True)
    return rec


def run_arm_once(
    spec: ArmSpec,
    rep: int,
    env_info: dict,
    extra_pmu: list[str] | None = None,
    stamp_dir: pathlib.Path | None = None,
    rounds_override: int | None = None,
    warmup_override: int | None = None,
    settle_s: float = SETTLE_S,
) -> dict:
    start_card_arm(spec, extra_pmu=extra_pmu, settle_s=settle_s)
    if spec.is_idle:
        return measure_card_idle(spec, rep, env_info)
    csv_path = None
    if spec.stamp and stamp_dir is not None:
        stamp_dir.mkdir(parents=True, exist_ok=True)
        csv_path = stamp_dir / f"{spec.arm}_rep{rep}.csv"

    cmd = spec.host_args(stamp_csv=csv_path, rounds_override=rounds_override, warmup_override=warmup_override)
    for attempt in range(1, 25):
        wait_for_turbo(3_850_000, 120.0)
        p = subprocess.run(cmd, text=True, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(
                f"{spec.arm} rep={rep} failed (rc={p.returncode}):\n{p.stderr[-600:]}\n{p.stdout[-600:]}"
            )
        mrv_lines = [l for l in p.stdout.splitlines() if l.startswith("MRV75 ")]
        if not mrv_lines:
            raise RuntimeError(f"No MRV75 JSON line for {spec.arm} rep={rep}: {p.stdout[-400:]}")
        rec = json.loads(mrv_lines[-1][6:])
        hp, cp = rec["host_pmu"], rec["card_pmu"]
        if rec["lost"] != 0 or rec["bad"] != 0 or rec["torn"] != 0:
            raise RuntimeError(
                f"Integrity error {spec.arm} rep={rep}: lost={rec['lost']} bad={rec['bad']} torn={rec['torn']}"
            )
        if any(c["corrupt"] != 0 for c in cp["cores"]):
            raise RuntimeError(f"Card corrupt payload in {spec.arm} rep={rep}: {cp['cores']}")
        if hp["min_pct"] < 99.9 or cp["min_pct"] < 99.9:
            raise RuntimeError(
                f"PMU multiplexing in {spec.arm} rep={rep}: host={hp['min_pct']} card={cp['min_pct']}"
            )
        if hp["ghz"] < MIN_CPU_GHZ:
            print(
                f"  [retry {attempt}] {spec.arm} rep={rep}: cpu40 throttled to {hp['ghz']:.2f} GHz, restarting card arm...",
                flush=True,
            )
            time.sleep(3.0)
            start_card_arm(spec, extra_pmu=extra_pmu, settle_s=settle_s)
            continue
        break
    else:
        raise RuntimeError(f"{spec.arm} rep={rep}: cpu40 stayed below {MIN_CPU_GHZ} GHz after retries")

    wall_c = max(1e-9, cp["wall_s"])
    card_core_ghz = [round(c["cpu_cycles"] / wall_c / 1e9, 4) for c in cp["cores"]]
    shadow_core_ghz = [round(c["cpu_cycles"] / wall_c / 1e9, 4) for c in cp.get("shadows", [])]
    gbps = rec["mops"] * CELL_BYTES * 8.0 / 1000.0

    rec.update(
        {
            "issue": "MRV-236",
            "arm": spec.arm,
            "group": spec.group,
            "rep": rep,
            "batch": 1,
            "concurrency": spec.window,
            "lcores": spec.lcores,
            "ring_cores": spec.ring_cores,
            "next_publish": spec.next_publish,
            "poll_mode": spec.poll,
            "roles": spec.roles or "ring",
            "shadow_mode": spec.shadow,
            "settle_s": settle_s,
            "gbps": round(gbps, 4),
            "card_core_ghz": card_core_ghz,
            "shadow_core_ghz": shadow_core_ghz,
            "env": env_info,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    if csv_path is not None and csv_path.exists():
        rec["stamp_csv"] = str(csv_path.relative_to(ROOT))
        rec["stamp_summary"] = summarize_stamps(csv_path, float(cp["tsc_hz"]))

    tot = rec["rounds"] + rec["warmup"]
    snp = cp["tad"]["tad_snp_msh_out_any"]["val"] / tot
    tad_occ = cp["tad"]["tad_req_occ"]["val"] / (wall_c * 2.0e9)
    l2 = sum(c["l2d_cache_refill"] for c in cp["cores"]) / tot
    ss = rec.get("stamp_summary", {})
    f_str = f" f={ss['f_held']:.3f} gapH={ss['landing_gap_held']['p50_ns']:.1f}ns gapU={ss['landing_gap_unheld']['p50_ns']:.1f}ns" if ss else ""
    print(
        f"[{spec.group:4s}] {spec.arm:6s} r{rep} | {rec['mops']:6.2f} M/s ({gbps:5.2f} Gb/s) "
        f"RTT p50={rec['rtt_p50_us']:5.2f} p99={rec['rtt_p99_us']:5.2f}us | "
        f"snp/m={snp:4.2f} TADocc={tad_occ:4.2f} L2/m={l2:4.2f} | "
        f"ghz={hp['ghz']:.2f}/c{card_core_ghz}{f_str}",
        flush=True,
    )
    return rec


def restore_baseline():
    print("\n=== Restoring card baseline (--restore, rmmod pem_wc, default refl) ===", flush=True)
    ssh_card(
        "pkill -INT -f '^/root/bench/inline_reflector' 2>/dev/null || true; "
        "for _ in $(seq 1 60); do pgrep -f '^/root/bench/inline_reflector' >/dev/null || break; sleep 0.05; done; "
        "tmux kill-session -t refl 2>/dev/null || true; "
        "rm -rf /var/run/dpdk/mrv236 /dev/hugepages/mrv236map_*; "
        "sleep 0.2; "
        f"{CARD_DEFAULT_BIN} --restore; "
        "lsmod | grep -q pem_wc && rmmod pem_wc || true; "
        "rm -f /tmp/mrv236_card.log; "
        f"tmux new-session -d -s refl '{CARD_DEFAULT_BIN} -l 3 -a 0002:01:00.2 --file-prefix=refl'; "
        "sleep 2.0; tmux ls"
    )

def verify_and_write_audit(
    out_path: pathlib.Path,
    mode: str,
    host_before: dict,
    card_before: dict,
    total_runs: int,
    extra: dict | None = None,
) -> dict:
    print(f"Waiting {AUDIT_WAIT_S:.0f} s for delayed card EDAC / host AER audit...", flush=True)
    time.sleep(AUDIT_WAIT_S)
    host_after = check_host_env()
    card_after = read_card_state()
    deltas = compute_edac_aer_deltas(host_before, host_after, card_before, card_after)
    record = {
        "issue": "MRV-236",
        "mode": mode,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_runs": total_runs,
        "audit_wait_s": AUDIT_WAIT_S,
        "host_before": host_before,
        "host_after": host_after,
        "aer_delta": deltas["aer_delta"],
        "card_before": card_before,
        "card_after": card_after,
        "edac_delta": deltas["edac_delta"],
        "all_zero_edac_aer_delta": deltas["all_zero"],
    }
    if extra:
        record.update(extra)
    out_path.write_text(json.dumps(record, indent=2) + "\n")
    if not deltas["all_zero"]:
        raise RuntimeError(f"Non-zero EDAC/AER delta: {deltas}")
    if card_after["pem_wc_loaded"]:
        raise RuntimeError("pem_wc module still loaded after restore!")
    if card_after["eth0_link"] != "yes" or card_after["eth0_speed"] != "100000Mb/s":
        raise RuntimeError(f"Card eth0 not 100G UP: {card_after}")
    if "refl:" not in card_after["tmux_ls"]:
        raise RuntimeError(f"Default refl tmux missing: {card_after['tmux_ls']}")
    for i in range(248, 256):
        if card_after["outbound_248_255"][str(i)] != "0x0":
            raise RuntimeError(f"PEM0 outbound region {i} not 0: {card_after['outbound_248_255']}")
    print(f"BASELINE RESTORE & 21s EDAC/AER ZERO-DELTA AUDIT PASSED -> {out_path}", flush=True)
    return record


def run_smoke(skip_deploy: bool, smoke_rounds: int):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    smoke_stamps = LOG_DIR / "smoke_stamps"
    smoke_stamps.mkdir(parents=True, exist_ok=True)
    host_before = check_host_env()
    card_before = read_card_state()
    deploy_info = None if skip_deploy else deploy_card_binary()

    total_runs = 0
    gate = None
    el0_probe = None
    pmu_probe = None
    try:
        el0_probe = probe_card_el0_ops()
        pmu_probe = probe_card_pmus()
        extra_pmu = pmu_probe["discovered_extra_pmu_specs"]
        SMOKE_PROBE_JSON.write_text(
            json.dumps({"el0_probe": el0_probe, "pmu_probe": pmu_probe, "deploy": deploy_info}, indent=2) + "\n"
        )

        by_name = {a.arm: a for a in build_arm_catalog()}
        # Unstamped B vs sparse-stamped B check + pre-check gate (B vs A1) + A3 (wfe) + A4 (civac) + S2 (shadow front)
        b_unstamped = ArmSpec(**{**by_name["B"].__dict__, "arm": "B_nostamp", "stamp": False, "stamp_sparse": False})
        smoke_arms = [by_name["B"], by_name["A1"], b_unstamped, by_name["A3"], by_name["A4"], by_name["S2"]]

        recs = {}
        with SMOKE_RUNS_JSONL.open("w") as out_f:
            for spec in smoke_arms:
                rec = run_arm_once(
                    spec,
                    rep=0,
                    env_info=host_before,
                    extra_pmu=extra_pmu,
                    stamp_dir=smoke_stamps,
                    rounds_override=smoke_rounds,
                    warmup_override=min(20_000, smoke_rounds // 10),
                )
                total_runs += 1
                rec["id"] = total_runs
                out_f.write(json.dumps(rec) + "\n")
                out_f.flush()
                recs[spec.arm] = rec

        gate = evaluate_precheck_gate(recs["B"], recs["A1"])
        print(f"\n=== SMOKE PRE-CHECK GATE (B vs A1): {json.dumps(gate)} ===", flush=True)
        # Verify sparse-stamping overhead vs unstamped B
        b_diff_pct = (recs["B"]["mops"] - recs["B_nostamp"]["mops"]) / max(1e-9, recs["B_nostamp"]["mops"]) * 100.0
        print(
            f"Sparse-stamp overhead check: B={recs['B']['mops']:.2f} vs B_nostamp={recs['B_nostamp']['mops']:.2f} M/s ({b_diff_pct:+.2f}%)",
            flush=True,
        )
    finally:
        restore_baseline()

    verify_and_write_audit(
        SMOKE_ENV_JSON,
        mode="smoke",
        host_before=host_before,
        card_before=card_before,
        total_runs=total_runs,
        extra={"el0_probe": el0_probe, "precheck_gate": gate, "deploy": deploy_info},
    )


def run_full(skip_deploy: bool, reps: int):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STAMP_DIR.mkdir(parents=True, exist_ok=True)
    for old_csv in STAMP_DIR.glob("*.csv"):
        old_csv.unlink()
    host_before = check_host_env()
    card_before = read_card_state()
    deploy_info = None if skip_deploy else deploy_card_binary()

    total_runs = 0
    gate = None
    el0_probe = None
    pmu_probe = None
    try:
        el0_probe = probe_card_el0_ops()
        pmu_probe = probe_card_pmus()
        extra_pmu = pmu_probe["discovered_extra_pmu_specs"]
        SMOKE_PROBE_JSON.write_text(
            json.dumps({"el0_probe": el0_probe, "pmu_probe": pmu_probe, "deploy": deploy_info}, indent=2) + "\n"
        )

        arms = build_arm_catalog()
        schedule = build_interleaved_schedule(arms, reps=reps)
        rep0_gate_recs: dict[str, dict] = {}

        with RUNS_JSONL.open("w") as out_f:
            for rep, spec in schedule:
                rec = run_arm_once(
                    spec,
                    rep=rep,
                    env_info=host_before,
                    extra_pmu=extra_pmu,
                    stamp_dir=STAMP_DIR,
                )
                total_runs += 1
                rec["id"] = total_runs
                out_f.write(json.dumps(rec) + "\n")
                out_f.flush()
                if rep == 0 and spec.arm in ("B", "A1"):
                    rep0_gate_recs[spec.arm] = rec
                    if len(rep0_gate_recs) == 2:
                        gate = evaluate_precheck_gate(rep0_gate_recs["B"], rep0_gate_recs["A1"])
                        print(f"\n=== PRE-CHECK GATE (rep 0 B vs A1): {json.dumps(gate)} ===\n", flush=True)
    finally:
        restore_baseline()

    verify_and_write_audit(
        ENV_JSON,
        mode="full",
        host_before=host_before,
        card_before=card_before,
        total_runs=total_runs,
        extra={"el0_probe": el0_probe, "precheck_gate": gate, "deploy": deploy_info},
    )


# ---- Offline self-test & dry-run (ZERO hardware / SSH / Socket 1 access) ----


def run_self_test(dry_run: bool):
    print("=== MRV-236 harness offline self-test ===", flush=True)
    arms = build_arm_catalog()
    names = [a.arm for a in arms]
    expected_arms = {
        "B",
        "A1",
        "A2",
        "A3",
        "A4",
        "B1",
        "S1",
        "S2",
        "S3",
        "B1_sp",
        "S2_sp",
        "L_B",
        "L_A2",
        "L_A3",
        "L_A4",
        "WC4_B",
        "idle_1s_wc2",
        "idle_1s_wc4",
    }
    assert set(names) == expected_arms, f"Arm mismatch: got {set(names)} expected {expected_arms}"
    assert len(names) == len(expected_arms), "Duplicate arm names"

    # 1. Verify no A5 (--poll nc), no SDP, no default binary overwrite, exact rounds/W
    for a in arms:
        assert a.poll in ("ldar", "ldr", "wfe", "civac"), f"Forbidden poll mode: {a.poll}"
        cargs = a.card_args(extra_pmu=["pem0:inb_wr=0x10"])
        assert cargs[0] == CARD_REMOTE_BIN and cargs[0] != CARD_DEFAULT_BIN
        assert "--store" in cargs and "--store-wc" in cargs
        assert "--poll" in cargs and "nc" not in cargs
        if not a.is_idle:
            hargs = a.host_args(stamp_csv=STAMP_DIR / f"{a.arm}_rep0.csv")
            assert "--reply" in hargs and hargs[hargs.index("--reply") + 1] == "ring"
            assert "sdp" not in hargs
            if a.window == 64:
                assert a.rounds == ROUNDS_W64
            elif a.window == 1:
                assert a.rounds == ROUNDS_W1
            else:
                raise AssertionError(f"Unexpected window {a.window} in {a.arm}")
        if a.arm in ("S1", "S2", "S3"):
            assert a.next_publish == "exit" and a.roles == "ring,shadow" and a.shadow is not None
        if a.arm == "B1":
            assert a.next_publish == "exit" and a.roles is None and a.shadow is None

    # 2. Verify interleaved schedule (rep 0 starts with B then A1, every arm appears reps times)
    sched = build_interleaved_schedule(arms, reps=REPS)
    assert len(sched) == len(arms) * REPS
    assert sched[0][1].arm == "B" and sched[1][1].arm == "A1"
    for a in arms:
        reps_seen = [r for r, s in sched if s.arm == a.arm]
        assert reps_seen == [0, 1, 2], f"Arm {a.arm} reps={reps_seen}"
    # Verify rep 0, 1, 2 orders differ (rotated interleaving)
    ord0 = [s.arm for r, s in sched if r == 0]
    ord1 = [s.arm for r, s in sched if r == 1]
    ord2 = [s.arm for r, s in sched if r == 2]
    assert ord0 != ord1 and ord1 != ord2 and ord0 != ord2

    # 3. Verify pre-check gate logic in both branches
    mock_b = {"mops": 13.1, "rounds": 10_000_000, "warmup": 20_000, "card_pmu": {"tad": {"tad_snp_msh_out_any": {"val": 35_070_000}}}}
    mock_a1_slow = {"mops": 13.2, "rounds": 10_000_000, "warmup": 20_000, "card_pmu": {"tad": {"tad_snp_msh_out_any": {"val": 34_000_000}}}}
    mock_a1_fast = {"mops": 31.5, "rounds": 10_000_000, "warmup": 20_000, "card_pmu": {"tad": {"tad_snp_msh_out_any": {"val": 10_500_000}}}}
    g_slow = evaluate_precheck_gate(mock_b, mock_a1_slow)
    g_fast = evaluate_precheck_gate(mock_b, mock_a1_fast)
    assert not g_slow["a1_broke_out"] and g_slow["continue_full_matrix"]
    assert g_fast["a1_broke_out"] and g_fast["continue_full_matrix"]

    # 4. Verify 11-column stamp CSV summary (held vs unheld landing gap & per-core & first-poll wait)
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "synthetic.csv"
        # tsc_hz = 2.2 GHz, card_hz = 1.0 GHz -> slope = 2.2
        lines = [
            "# tsc_hz=2200000000 sb=1000000,100000,1400 sa=2201000000,1000100000,1400",
            "seq,send_tsc,seen_tsc,card_t_first_poll,card_t_in,card_t_out,card_write_ticks,card_waited_polls,card_core,card_ring_idx,host_waited_polls",
            # seq 100..103: held (gap ~76 ns, waited_polls > 0, alternating cores 3 & 4)
            "100,2000000,2002200,554500,554545,554565,18,5,3,0,1",
            "101,2000167,2002367,554571,554621,554641,18,4,4,1,0",
            "102,2000334,2002534,554647,554697,554717,18,6,3,0,1",
            "103,2000501,2002701,554723,554773,554793,18,3,4,1,0",
            # seq 200..203: unheld (gap ~28 ns, waited_polls == 0, first_poll == t_in)
            "200,3000000,3002000,1009091,1009091,1009111,16,0,3,0,0",
            "201,3000062,3002062,1009119,1009119,1009139,16,0,4,1,0",
            "202,3000124,3002124,1009147,1009147,1009167,16,0,3,0,0",
            "203,3000186,3002186,1009175,1009175,1009195,16,0,4,1,0",
        ]
        p.write_text("\n".join(lines) + "\n")
        ss = summarize_stamps(p, 1_000_000_000.0)
        assert ss["n"] == 8 and ss["n_held"] == 4 and ss["n_unheld"] == 4
        assert abs(ss["f_held"] - 0.5) < 1e-6
        assert abs(ss["landing_gap_held"]["p50_ns"] - 76.0) < 0.1, ss["landing_gap_held"]
        assert abs(ss["landing_gap_unheld"]["p50_ns"] - 28.0) < 0.1, ss["landing_gap_unheld"]
        assert abs(ss["first_poll_to_in_held"]["p50_ns"] - 50.0) < 5.0, ss["first_poll_to_in_held"]
        assert set(ss["per_core"].keys()) == {"3", "4"}
        assert ss["per_core"]["3"]["n"] == 4 and ss["per_core"]["4"]["n"] == 4

    # 5. Verify PEM/IOBN sysfs PMU probe parser
    empty_probe = {"devices": {"tad": {"type": "9"}, "armv8_pmuv3": {"type": "8"}}}
    assert parse_card_pmu_probe(empty_probe) == []
    pem_probe = {
        "devices": {
            "tad": {"type": "9"},
            "pem0": {"type": "10", "events": {"inb_wr_req": "event=0x12", "clk": "event=0x01"}},
            "iobn": {"type": "11", "events": {"inb_occ": "config=0x2a"}},
        }
    }
    parsed_specs = parse_card_pmu_probe(pem_probe)
    assert "pem0:inb_wr_req=0x12" in parsed_specs and "iobn:inb_occ=0x2a" in parsed_specs

    # 6. Verify EDAC/AER delta calculator
    hb = {"aer": {"aer_dev_correctable": 0, "aer_dev_fatal": 0, "aer_dev_nonfatal": 0}}
    cb = {"edac": {"mc/mc0": {"ce": 0, "ue": 0}, "ea/ea0": {"ce": 0, "ue": 3}}}
    assert compute_edac_aer_deltas(hb, hb, cb, cb)["all_zero"]
    ca_bad = {"edac": {"mc/mc0": {"ce": 0, "ue": 0}, "ea/ea0": {"ce": 0, "ue": 4}}}
    assert not compute_edac_aer_deltas(hb, hb, cb, ca_bad)["all_zero"]

    # 7. Verify compiled binaries exist locally and have expected ELF headers (pure local file read)
    for label, bpath, expected_machine in (
        ("host inline_pingpong", HOST_BIN, 0x3E),       # EM_X86_64
        ("card inline_reflector", CARD_LOCAL_BIN, 0xB7), # EM_AARCH64
    ):
        assert bpath.exists(), f"Missing compiled binary {bpath}"
        hdr = bpath.read_bytes()[:20]
        assert hdr[:4] == b"\x7fELF", f"{label} is not ELF"
        e_machine = int.from_bytes(hdr[18:20], "little")
        assert e_machine == expected_machine, f"{label} e_machine={hex(e_machine)}, expected {hex(expected_machine)}"

    print(f"ALL OFFLINE SELF-TESTS PASSED ({len(arms)} arms x {REPS} reps = {len(sched)} full runs)!", flush=True)
    if dry_run:
        print("\n=== DRY-RUN SCHEDULE PREVIEW (ZERO HARDWARE COMMANDS EXECUTED) ===")
        for rep in range(REPS):
            rep_arms = [s for r, s in sched if r == rep]
            print(f"Round {rep}: " + " -> ".join(a.arm for a in rep_arms))
        print("\n=== PER-ARM COMMAND PREVIEW ===")
        for a in arms:
            print(f"  [{a.group:4s}] {a.arm:12s} | card: {' '.join(a.card_args())}")
            if not a.is_idle:
                print(f"         {'':12s} | host: {' '.join(a.host_args(STAMP_DIR / f'{a.arm}_rep0.csv'))}")


def main():
    ap = argparse.ArgumentParser(description="MRV-236 experiment harness")
    ap.add_argument("--self-test", action="store_true", help="Run offline self-tests (zero hardware access)")
    ap.add_argument("--dry-run", action="store_true", help="Print dry-run schedule/commands (zero hardware access)")
    ap.add_argument("--deploy", action="store_true", help="Deploy card binary to /root/bench/inline_reflector.mrv236")
    ap.add_argument("--smoke", action="store_true", help="Run EL0 probe + PMU probe + B/A1 gate + A3/A4/S2 smoke")
    ap.add_argument("--full", action="store_true", help="Run full n=3 interleaved experiment matrix")
    ap.add_argument("--skip-deploy", action="store_true", help="Skip scp deploy in --smoke/--full")
    ap.add_argument("--smoke-rounds", type=int, default=1_000_000, help="Rounds per arm in --smoke (default 1e6)")
    ap.add_argument("--reps", type=int, default=REPS, help="Repetitions in --full (default 3)")
    args = ap.parse_args()

    if args.self_test or args.dry_run:
        run_self_test(dry_run=args.dry_run)
        return
    if args.deploy and not (args.smoke or args.full):
        deploy_card_binary()
        return
    if args.smoke:
        run_smoke(skip_deploy=args.skip_deploy, smoke_rounds=args.smoke_rounds)
        return
    if args.full:
        run_full(skip_deploy=args.skip_deploy, reps=args.reps)
        return
    ap.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
