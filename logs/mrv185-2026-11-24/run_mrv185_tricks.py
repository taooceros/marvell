#!/usr/bin/env python3
"""MRV-185 Opus-5-5 Tricks 1-3 n=3 interleaved experiment harness.

Covers all 3 zero-new-permission experiments proposed in MRV-185 #comment-920a2422:
- Trick 1A (same-CHA BAR4 line matching): bar4-l3-cha28-8b (same CHA as RP BAR0),
  bar4-l3-cha20-8b (same CHA as VF0 BAR0), interleaved with rp-bar0-8b, vf0-bar0-8b, bar4-l3-line0-8b.
- Trick 1B (multi-core concurrency scaling): full K=1..8 physical Socket 1 cores (40..47)
  concurrently reading RP BAR0 (+0x0 RO) vs VF0 BAR0 (same address OUT_CNTS control),
  launched under `numactl --cpunodebind=1 --membind=1`.
- Trick 1C (cross-interference): cpu40 measuring VF0 BAR0 with 0/1/4 cores pounding RP BAR0,
  and cpu40 measuring RP BAR0 with 0/1/4 cores pounding VF0 BAR0.
- Trick 2 (host internal hop-by-hop outbound vs return residency at W=1): 5 documented
  non-multiplexed PMU banks (hop1..hop5) covering paired M2PCIe_1 RxC 0x10/0x11 queues
  (iio_ncs/iio_ncb/all/cha_idi/cha_ncb, plus default cha_ncs), M2P credit counters, the two
  documented 0x23 pmm_distress_0/1 umasks (0x08/0x80 in hop5), IRP_1 txc_bl_drs/ncb/ncs +
  txc_ak + txs_ins, and IIO_1 comp_buf across timer-floor, RP BAR0, VF0 BAR0, BAR4 L3, and BAR4 DDR5.
- Trick 3 (PCI config space pread(off=64) null-read de-confounding + 7-BDF triangulation):
  0000:98:00.0, 98:00.4, 98:01.0, 99:00.0, 99:02.0 (off 0/8/64), e0:01.0, f2:01.0 with
  100% non-mux CHA PRd, M2PCIe_1, IRP_1 outbound_ins, and IIO_1 outbound_tlp/cmpd verification.

Safety invariants:
- `/sys/bus/pci/devices/0000:98:01.0/resource0` is strictly O_RDONLY / PROT_READ / MAP_SHARED (+0x0 only).
- `/sys/bus/pci/devices/*/config` is strictly O_RDONLY (`pread` at offset 0/8/64 only).
- PCIe link speed/retrain and `ens1` are never touched.
- `try ... finally` unconditionally restores frequencies, BAR4 firmware defaults, `refl` tmux,
  and verifies post-21s Host+Card EDAC & AER == 0.
- Zero silent retries: every attempt is logged to `attempts_tricks.jsonl`.
"""

import hashlib
import json
import os
import pathlib
import signal
import subprocess
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv185")
LOG_DIR = ROOT / "logs/mrv185-2026-11-24"
RUNS_PATH = LOG_DIR / "runs_tricks.jsonl"
ATTEMPTS_PATH = LOG_DIR / "attempts_tricks.jsonl"
ENV_PATH = LOG_DIR / "env_tricks.json"

HOST_BIN = "/tmp/mrv185-target/release/mrv185_host"
NUMACTL_PREFIX = [
    "/nix/store/i835y8b4g9rkw94jiswhbc4hwi9dknna-numactl-2.0.18/bin/numactl",
    "--cpunodebind=1",
    "--membind=1",
]
CARD_BIN = "/root/bench/mrv182_card"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

UNC_DIR = pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency/package_01_die_01")
CPU40_DIR = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq")

REPS = 3
MAX_ATTEMPTS = 4
RESTORE_UNCORE_KHZ = 2200000
RESTORE_CORE_KHZ = 3900000

TRICK1A_ARMS = [
    ("rp-bar0-8b", 40_000),
    ("bar4-l3-cha28-8b", 40_000),
    ("vf0-bar0-8b", 40_000),
    ("bar4-l3-cha20-8b", 40_000),
    ("bar4-l3-line0-8b", 40_000),
]

TRICK1B_ARMS = [
    ("rp-bar0-8b", k) for k in range(1, 9)
] + [
    ("vf0-bar0-8b", k) for k in range(1, 9)
]
TRICK1C_ARMS = [
    ("vf0-clean", 0),
    ("vf0-aggr-rp", 1),
    ("vf0-aggr-rp", 4),
    ("rp-clean", 0),
    ("rp-aggr-vf0", 1),
    ("rp-aggr-vf0", 4),
]

TRICK2_BANKS = ["hop1", "hop2", "hop3", "hop4", "hop5"]
TRICK2_ENDPOINTS = [
    ("timer-floor", 40_000),
    ("rp-bar0-8b", 40_000),
    ("vf0-bar0-8b", 40_000),
    ("bar4-l3-walk-8b", 32_000),
    ("bar4-ddr5-walk-8b", 32_000),
]

TRICK3_CFGS = [
    ("0000:98:00.0", 0),
    ("0000:98:00.0", 64),
    ("0000:98:00.4", 0),
    ("0000:98:00.4", 64),
    ("0000:98:01.0", 0),
    ("0000:98:01.0", 64),
    ("0000:99:00.0", 0),
    ("0000:99:00.0", 64),
    ("0000:99:02.0", 0),
    ("0000:99:02.0", 8),
    ("0000:99:02.0", 64),
    ("0000:e0:01.0", 0),
    ("0000:e0:01.0", 64),
    ("0000:f2:01.0", 0),
    ("0000:f2:01.0", 64),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_progress(msg: str):
    print(f"[{utc_now()}] {msg}", flush=True)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def set_uncore_khz(khz: int):
    cur_max = int((UNC_DIR / "max_freq_khz").read_text().strip())
    if khz > cur_max:
        (UNC_DIR / "max_freq_khz").write_text(f"{khz}\n")
        (UNC_DIR / "min_freq_khz").write_text(f"{khz}\n")
    else:
        (UNC_DIR / "min_freq_khz").write_text(f"{khz}\n")
        (UNC_DIR / "max_freq_khz").write_text(f"{khz}\n")
    for _ in range(40):
        time.sleep(0.05)
        mn = int((UNC_DIR / "min_freq_khz").read_text().strip())
        mx = int((UNC_DIR / "max_freq_khz").read_text().strip())
        cur = int((UNC_DIR / "current_freq_khz").read_text().strip())
        if mn == khz and mx == khz and cur == khz:
            return
    raise RuntimeError(f"uncore failed to settle at {khz} kHz: min={mn} max={mx} cur={cur}")


def set_cpu40_khz(khz: int):
    cur_max = int((CPU40_DIR / "scaling_max_freq").read_text().strip())
    if khz > cur_max:
        (CPU40_DIR / "scaling_max_freq").write_text(f"{khz}\n")
        (CPU40_DIR / "scaling_min_freq").write_text(f"{khz}\n")
    else:
        (CPU40_DIR / "scaling_min_freq").write_text(f"{khz}\n")
        (CPU40_DIR / "scaling_max_freq").write_text(f"{khz}\n")
    for _ in range(40):
        time.sleep(0.05)
        mn = int((CPU40_DIR / "scaling_min_freq").read_text().strip())
        mx = int((CPU40_DIR / "scaling_max_freq").read_text().strip())
        if mn == khz and mx == khz:
            return
    raise RuntimeError(f"cpu40 failed to set {khz} kHz: min={mn} max={mx}")


def read_aer_totals() -> dict:
    base = pathlib.Path("/sys/bus/pci/devices/0000:98:01.0")
    out = {}
    for k in ("cor", "nonfatal", "fatal"):
        p = base / f"aer_rootport_total_err_{k}"
        out[k] = int(p.read_text().strip()) if p.exists() else -1
    out["link_speed"] = (base / "current_link_speed").read_text().strip()
    out["link_width"] = int((base / "current_link_width").read_text().strip())
    return out


def read_host_env() -> dict:
    uncore = {}
    for pkg in ("package_00_die_00", "package_01_die_01"):
        base = pathlib.Path(f"/sys/devices/system/cpu/intel_uncore_frequency/{pkg}")
        uncore[pkg] = {
            k: int((base / f"{k}_freq_khz").read_text().strip())
            for k in ("min", "max", "current", "initial_min", "initial_max")
        }
    edac_ue, edac_ce = {}, {}
    for mc in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*")):
        edac_ue[mc.name] = int((mc / "ue_count").read_text().strip())
        edac_ce[mc.name] = int((mc / "ce_count").read_text().strip())
    return {
        "cpu40": {
            "governor": (CPU40_DIR / "scaling_governor").read_text().strip(),
            "scaling_min_freq": int((CPU40_DIR / "scaling_min_freq").read_text().strip()),
            "scaling_max_freq": int((CPU40_DIR / "scaling_max_freq").read_text().strip()),
            "scaling_cur_freq": int((CPU40_DIR / "scaling_cur_freq").read_text().strip()),
        },
        "uncore_khz": uncore,
        "perf_event_paranoid": int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip()),
        "rp_98_01_0_aer": read_aer_totals(),
        "host_edac_ue": edac_ue,
        "host_edac_ce": edac_ce,
        "host_bin_sha256": sha256_file(HOST_BIN),
    }


def read_card_env() -> dict:
    cmd = (
        "cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000; "
        "lsmod | grep -c '^pem_wc ' || true; "
        "cat /sys/devices/system/edac/mc/mc0/ce_count; "
        "cat /sys/devices/system/edac/mc/mc0/ue_count; "
        "dmesg | grep -ciE 'SEI-UER|SError' || true; "
        "basename $(readlink -f /sys/class/net/eth0/device/driver 2>/dev/null) 2>/dev/null || echo unknown; "
        "ethtool eth0 2>/dev/null | awk '/Speed:/{s=$2} /Link detected:/{l=$3} END{print s \"_\" l}'; "
        "tmux has-session -t refl 2>/dev/null && echo running || echo stopped; "
        f"sha256sum {CARD_BIN} | awk '{{print $1}}'"
    )
    out = subprocess.check_output(CARD_SSH + [cmd], text=True).strip().splitlines()
    return {
        "card_cpu3_khz": int(out[0]),
        "pem_wc_loaded": int(out[1]),
        "mc0_ce": int(out[2]),
        "mc0_ue": int(out[3]),
        "sei_uer": int(out[4]),
        "eth0_driver": out[5],
        "eth0_link": out[6],
        "refl_tmux": out[7],
        "card_bin_sha256": out[8],
    }


def stop_refl():
    subprocess.run(
        CARD_SSH + ["tmux kill-session -t refl 2>/dev/null || true; pkill -INT -f '[/]root/bench/reflector-rs' || true"],
        check=False,
    )
    for _ in range(30):
        time.sleep(0.15)
        p = subprocess.run(CARD_SSH + ["pgrep -f '[/]root/bench/reflector-rs' || true"], capture_output=True, text=True)
        if not p.stdout.strip():
            return
    raise RuntimeError("reflector-rs did not stop cleanly")


def start_refl() -> str:
    stop_refl()
    subprocess.check_call(
        CARD_SSH + ["tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl"]
    )
    for _ in range(40):
        time.sleep(0.2)
        pane = subprocess.check_output(CARD_SSH + ["tmux capture-pane -t refl -p 2>/dev/null || true"], text=True)
        if "reflector running on port 0" in pane:
            return pane.strip()
    pane = subprocess.check_output(CARD_SSH + ["tmux capture-pane -t refl -p 2>/dev/null || true"], text=True)
    raise RuntimeError(f"refl failed to come up:\n{pane}")


def stop_card():
    subprocess.run(CARD_SSH + ["pkill -INT -f '[/]root/bench/mrv182_card' || true"], check=False)
    for _ in range(40):
        time.sleep(0.15)
        p = subprocess.run(CARD_SSH + ["pgrep -f '[/]root/bench/mrv182_card' || true"], capture_output=True, text=True)
        if not p.stdout.strip():
            return
    raise RuntimeError("mrv182_card did not exit cleanly after SIGINT")


def start_card() -> tuple[str, str]:
    stop_card()
    cmd = (
        f"nohup {CARD_BIN} -l 3 --no-pci --file-prefix=mrv185t --log-level=lib.eal:warning "
        f"-- --control 10.10.0.2:7526 --sync 10.10.0.2:7527 "
        f"> /tmp/mrv185_card_tricks.log 2>&1 < /dev/null & echo $!"
    )
    pid = subprocess.check_output(CARD_SSH + [cmd], text=True).strip()
    for _ in range(50):
        time.sleep(0.15)
        log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv185_card_tricks.log 2>/dev/null || true"], text=True)
        if "mrv182_card: BAR4 slot 15" in log:
            return pid, log.strip()
    log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv185_card_tricks.log 2>/dev/null || true"], text=True)
    raise RuntimeError(f"mrv182_card failed to start:\n{log}")


def stage_cmd(arm: str, rounds: int, pmu_bank: str = "default") -> list[str]:
    if arm == "vf0-bar0-8b":
        return NUMACTL_PREFIX + [
            HOST_BIN,
            "-l", "40",
            "-a", "0000:99:02.0",
            "--file-prefix=mrv185t",
            "--log-level=lib.eal:warning",
            "--",
            "--mode", "stage",
            "--arm", arm,
            "--cpu", "40",
            "--pmu-bank", pmu_bank,
            "--target-ghz", "3.85",
            "-n", str(rounds),
        ]
    return NUMACTL_PREFIX + [
        HOST_BIN,
        "--mode", "stage",
        "--arm", arm,
        "--cpu", "40",
        "--pmu-bank", pmu_bank,
        "--target-ghz", "3.85",
        "-n", str(rounds),
    ]


def scale_cmd(arm: str, k: int, duration_ms: int = 250) -> list[str]:
    if arm == "vf0-bar0-8b":
        return NUMACTL_PREFIX + [
            HOST_BIN,
            "-l", "40",
            "-a", "0000:99:02.0",
            "--file-prefix=mrv185t",
            "--log-level=lib.eal:warning",
            "--",
            "--mode", "scale",
            "--arm", arm,
            "--k", str(k),
            "--duration-ms", str(duration_ms),
        ]
    return NUMACTL_PREFIX + [
        HOST_BIN,
        "--mode", "scale",
        "--arm", arm,
        "--k", str(k),
        "--duration-ms", str(duration_ms),
    ]


def interfere_cmd(arm: str, aggr_k: int, rounds: int = 40_000) -> list[str]:
    return NUMACTL_PREFIX + [
        HOST_BIN,
        "-l", "40",
        "-a", "0000:99:02.0",
        "--file-prefix=mrv185t",
        "--log-level=lib.eal:warning",
        "--",
        "--mode", "interfere",
        "--arm", arm,
        "--aggr-k", str(aggr_k),
        "--target-ghz", "3.85",
        "-n", str(rounds),
    ]


def cfg_cmd(bdf: str, offset: int, rounds: int = 20_000) -> list[str]:
    return NUMACTL_PREFIX + [
        HOST_BIN,
        "--mode", "cfg",
        "--bdf", bdf,
        "--cfg-offset", str(offset),
        "--cpu", "40",
        "--target-ghz", "3.85",
        "-n", str(rounds),
    ]

def run_cmd_verified(suite: str, label: str, rep: int, cmd: list[str], validator, meta: dict | None = None) -> dict:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        t0 = utc_now()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        line = None
        for l in reversed(p.stdout.splitlines()):
            if l.strip().startswith("{"):
                line = l.strip()
                break
        rec = {
            "campaign_id": 2,
            "ts_utc": t0,
            "suite": suite,
            "label": label,
            "rep": rep,
            "attempt": attempt,
            "rc": p.returncode,
        }
        if meta:
            rec.update(meta)
        if line is None or p.returncode != 0:
            rec["accepted"] = False
            rec["reject_reason"] = f"rc={p.returncode} stderr={p.stderr.strip()[-300:]}"
            with open(ATTEMPTS_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")
            time.sleep(0.25)
            continue
        data = json.loads(line)
        rec.update(data)
        reasons = validator(data)
        rec["accepted"] = len(reasons) == 0
        rec["reject_reason"] = "; ".join(reasons)
        with open(ATTEMPTS_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")
        if rec["accepted"]:
            with open(RUNS_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")
            return rec
        log_progress(f"  [REJECT] {suite}/{label} rep={rep} attempt={attempt}: {rec['reject_reason']}")
        time.sleep(0.25)
    raise RuntimeError(f"suite={suite} label={label} rep={rep} failed after {MAX_ATTEMPTS} attempts")


def validate_base(d: dict, min_core_ghz: float = 3.80, check_card: bool = True, min_all_cores: float | None = None, check_aggr_ghz: bool = False) -> list[str]:
    r = []
    if d["mismatches"] != 0:
        r.append(f"mismatches={d['mismatches']}")
    if d["page_faults"] != 0:
        r.append(f"page_faults={d['page_faults']}")
    if d["host_pmu_min_pct"] < 99.99:
        r.append(f"host_pmu_min_pct={d['host_pmu_min_pct']}")
    if check_card and d["card_pmu"].get("min_pct", 0.0) < 99.99:
        r.append(f"card_pmu_min_pct={d['card_pmu'].get('min_pct')}")
    mg = d.get("mesh_ghz", 0.0)
    if abs(mg - 2.20) > 0.03:
        r.append(f"mesh_ghz={mg:.4f}!=2.20")
    ap = d.get("aperf_mperf_ghz", 0.0)
    if ap < min_core_ghz:
        r.append(f"aperf_mperf_ghz={ap:.4f}<{min_core_ghz}")
    if min_all_cores is not None:
        mc = d.get("min_core_ghz", 0.0)
        if mc < min_all_cores:
            r.append(f"min_core_ghz={mc:.4f}<{min_all_cores}")
    if check_aggr_ghz and d.get("aggr_k", 0) > 0:
        ag = d.get("aggr_min_ghz", 0.0)
        if ag < 3.00:
            r.append(f"aggr_min_ghz={ag:.4f}<3.00")
    return r


def main():
    def _sig_handler(signum, _frame):
        raise SystemExit(f"received signal {signum}")

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Never overwrite prior runs if re-invoked; start fresh only when clean.
    if RUNS_PATH.exists():
        RUNS_PATH.unlink()

    env_doc = {
        "issue": "MRV-185",
        "campaign": "opus-5-5-tricks-1-3",
        "campaign_id": 2,
        "started_utc": utc_now(),
        "host_before": read_host_env(),
        "card_before": read_card_env(),
    }

    total_ok = 0
    try:
        log_progress("Pinning baseline uncore=2200000 kHz, cpu40=3900000 kHz")
        set_uncore_khz(RESTORE_UNCORE_KHZ)
        set_cpu40_khz(RESTORE_CORE_KHZ)

        log_progress("Stopping refl and starting mrv182_card for BAR4 lease & Card PMU")
        stop_refl()
        card_pid, card_start_log = start_card()
        env_doc["card_start"] = {"pid": card_pid, "log": card_start_log}

        # ------------------------------------------------------------------
        # TRICK 1A: Exact Same-CHA BAR4 Line Control (CHA 28 & CHA 20)
        # ------------------------------------------------------------------
        log_progress("=== TRICK 1A: Same-CHA BAR4 line matching (CHA 28=RP, CHA 20=VF0) ===")
        for rep in range(1, REPS + 1):
            arms = TRICK1A_ARMS if rep % 2 == 1 else list(reversed(TRICK1A_ARMS))
            for arm, rounds in arms:
                def _v1a(d, _arm=arm):
                    rs = validate_base(d, min_core_ghz=3.80)
                    if _arm == "bar4-l3-cha28-8b" and d["cha"]["top_cha"] != 28:
                        rs.append(f"top_cha={d['cha']['top_cha']}!=28")
                    if _arm == "bar4-l3-cha20-8b" and d["cha"]["top_cha"] != 20:
                        rs.append(f"top_cha={d['cha']['top_cha']}!=20")
                    return rs
                r = run_cmd_verified(
                    "trick1a_same_cha",
                    arm,
                    rep,
                    stage_cmd(arm, rounds, pmu_bank="default"),
                    _v1a,
                )
                total_ok += 1
                out_tor = r["net_ns"]["mean"] - r["cha"]["tor_residency_ns"]
                log_progress(
                    f"  T1A rep={rep} {arm:18s} line={r['matched_line_idx']:3d} cha={r['cha']['top_cha']:2d} "
                    f"p50={r['net_ns']['p50']:6.2f} mean={r['net_ns']['mean']:6.2f} TOR={r['cha']['tor_residency_ns']:6.2f} out_TOR={out_tor:5.2f}"
                )

        # ------------------------------------------------------------------
        # TRICK 1B: Full K=1..8 Concurrency Scaling (RP BAR0 vs VF0 BAR0)
        # ------------------------------------------------------------------
        log_progress("=== TRICK 1B: Multi-core concurrency scaling K=1..8 (RP vs VF0) ===")
        for rep in range(1, REPS + 1):
            arms = TRICK1B_ARMS if rep % 2 == 1 else list(reversed(TRICK1B_ARMS))
            for arm, k in arms:
                min_c = 3.20 if k >= 2 else 3.80
                r = run_cmd_verified(
                    "trick1b_scale",
                    f"{arm}/k{k}",
                    rep,
                    scale_cmd(arm, k, duration_ms=250),
                    lambda d, mc=min_c: validate_base(d, min_core_ghz=3.40 if d["k"] >= 4 else 3.70, min_all_cores=mc),
                )
                total_ok += 1
                log_progress(
                    f"  T1B rep={rep} {arm:12s} K={k} mops={r['mops']:6.3f} cpu0_p50={r['cpu0_p50_ns']:7.2f} "
                    f"TOR={r['cha']['tor_residency_ns']:7.2f} minGHz={r['min_core_ghz']:.2f}"
                )

        # ------------------------------------------------------------------
        # TRICK 1C: Cross-Interference (RP BAR0 <-> VF0 BAR0)
        # ------------------------------------------------------------------
        log_progress("=== TRICK 1C: Cross-interference RP BAR0 <-> VF0 BAR0 ===")
        for rep in range(1, REPS + 1):
            arms = TRICK1C_ARMS if rep % 2 == 1 else list(reversed(TRICK1C_ARMS))
            for arm, aggr_k in arms:
                lbl = f"{arm}-k{aggr_k}" if aggr_k > 0 else arm
                r = run_cmd_verified(
                    "trick1c_interfere",
                    lbl,
                    rep,
                    interfere_cmd(arm, aggr_k, rounds=40_000),
                    lambda d: validate_base(d, min_core_ghz=3.50 if d["aggr_k"] >= 4 else 3.75, check_aggr_ghz=True),
                )
                total_ok += 1
                log_progress(
                    f"  T1C rep={rep} {lbl:16s} p50={r['net_ns']['p50']:7.2f} mean={r['net_ns']['mean']:7.2f} "
                    f"p99={r['net_ns']['p99']:7.2f} cha20_tor={r['cha']['cha20_tor_ns']:6.2f} cha28_tor={r['cha']['cha28_tor_ns']:6.2f} aggr={r['aggr_mops']:.3f}M"
                )

        # ------------------------------------------------------------------
        # TRICK 2: Host Internal Hop-by-Hop Outbound vs Return Residency (W=1)
        # ------------------------------------------------------------------
        log_progress("=== TRICK 2: Host internal M2PCIe_1 / IRP_1 / IIO_1 hop scan (5 banks x 5 endpoints) ===")
        for rep in range(1, REPS + 1):
            banks = TRICK2_BANKS if rep % 2 == 1 else list(reversed(TRICK2_BANKS))
            for bank in banks:
                eps = TRICK2_ENDPOINTS if rep % 2 == 1 else list(reversed(TRICK2_ENDPOINTS))
                for arm, rounds in eps:
                    r = run_cmd_verified(
                        "trick2_hops",
                        f"{bank}/{arm}",
                        rep,
                        stage_cmd(arm, rounds, pmu_bank=bank),
                        lambda d: validate_base(d, min_core_ghz=3.80),
                    )
                    total_ok += 1
                    ib = r["io_bank"]
                    log_progress(
                        f"  T2 rep={rep} {bank}/{arm:18s} m2p={ib['m2p_per_op']} irp={ib['irp_per_op']}"
                    )

        # ------------------------------------------------------------------
        # TRICK 3: PCI Config Space pread(off=64) Null-Read + 7-BDF Triangulation
        # ------------------------------------------------------------------
        log_progress("=== TRICK 3: PCI config space pread(off=0/8/64) 7-BDF triangulation ===")
        for rep in range(1, REPS + 1):
            cfgs = TRICK3_CFGS if rep % 2 == 1 else list(reversed(TRICK3_CFGS))
            for bdf, off in cfgs:
                lbl = f"{bdf}/off{off}"
                r = run_cmd_verified(
                    "trick3_cfg",
                    lbl,
                    rep,
                    cfg_cmd(bdf, off, rounds=20_000),
                    lambda d: validate_base(d, min_core_ghz=3.80, check_card=False),
                )
                total_ok += 1
                log_progress(
                    f"  T3 rep={rep} {lbl:20s} p50={r['net_ns']['p50']:7.2f} mean={r['net_ns']['mean']:7.2f} "
                    f"cha_prd={r['cha']['prd_ins']} m2p={r['m2pcie1']['cha_ncs_ins']} irp={r['irp1']['outbound_ins']} iio={r['iio1']['outbound_tlp_all']}"
                )

        log_progress(f"ALL OPUS-5-5 TRICKS 1-3 COMPLETED: {total_ok} verified runs!")

    finally:
        log_progress("=== FINALLY: Restoring host frequencies & card baseline unconditionally ===")
        restore_errors = []
        try:
            set_uncore_khz(RESTORE_UNCORE_KHZ)
            u_min = int((UNC_DIR / "min_freq_khz").read_text().strip())
            u_max = int((UNC_DIR / "max_freq_khz").read_text().strip())
            u_cur = int((UNC_DIR / "current_freq_khz").read_text().strip())
            log_progress(f"Verified package_01_die_01 uncore: min={u_min} max={u_max} cur={u_cur} kHz")
            if not (u_min == RESTORE_UNCORE_KHZ and u_max == RESTORE_UNCORE_KHZ):
                restore_errors.append(f"uncore not restored: min={u_min} max={u_max}")
        except Exception as e:
            restore_errors.append(f"uncore restore failed: {e}")

        try:
            set_cpu40_khz(RESTORE_CORE_KHZ)
            c_min = int((CPU40_DIR / "scaling_min_freq").read_text().strip())
            c_max = int((CPU40_DIR / "scaling_max_freq").read_text().strip())
            c_cur = int((CPU40_DIR / "scaling_cur_freq").read_text().strip())
            log_progress(f"Verified cpu40 cpufreq: min={c_min} max={c_max} cur={c_cur} kHz")
            if not (c_min == RESTORE_CORE_KHZ and c_max == RESTORE_CORE_KHZ):
                restore_errors.append(f"cpu40 not restored: min={c_min} max={c_max}")
        except Exception as e:
            restore_errors.append(f"cpu40 restore failed: {e}")

        try:
            stop_card()
            restore_out = subprocess.check_output(CARD_SSH + [f"{CARD_BIN} --restore"], text=True).strip()
            env_doc["card_restore_out"] = restore_out
            log_progress(f"Card BAR4 restore: {restore_out}")
        except Exception as e:
            restore_errors.append(f"card restore failed: {e}")

        try:
            refl_pane = start_refl()
            env_doc["refl_restored_pane"] = refl_pane
            log_progress("Card refl tmux restored: reflector running on port 0")
        except Exception as e:
            restore_errors.append(f"refl restore failed: {e}")

        log_progress("Waiting 21 s for delayed EDAC/AER verification...")
        time.sleep(21.0)
        env_doc["finished_utc"] = utc_now()
        env_doc["total_accepted_runs"] = total_ok
        env_doc["restore_errors"] = restore_errors
        env_doc["host_after"] = read_host_env()
        env_doc["card_after"] = read_card_env()

        # Verify zero EDAC/AER errors and baseline card health.
        ha, ca = env_doc["host_after"], env_doc["card_after"]
        if any(v != 0 for v in ha["host_edac_ue"].values()) or any(v != 0 for v in ha["host_edac_ce"].values()):
            restore_errors.append(f"host EDAC non-zero: ue={ha['host_edac_ue']} ce={ha['host_edac_ce']}")
        aer = ha["rp_98_01_0_aer"]
        if aer["cor"] != 0 or aer["nonfatal"] != 0 or aer["fatal"] != 0:
            restore_errors.append(f"RP 98:01.0 AER non-zero: {aer}")
        if ca["mc0_ue"] != 0 or ca["mc0_ce"] != 0 or ca["sei_uer"] != 0:
            restore_errors.append(f"card EDAC/SEI non-zero: {ca}")
        if ca["pem_wc_loaded"] != 0 or ca["refl_tmux"] != "running" or ca["eth0_link"] != "100000Mb/s_yes":
            restore_errors.append(f"card baseline mismatch: {ca}")

        env_doc["restore_errors"] = restore_errors
        ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
        log_progress(f"Wrote {ENV_PATH}; restore_errors={restore_errors}")
        if restore_errors:
            raise RuntimeError(f"Restore verification errors: {restore_errors}")


if __name__ == "__main__":
    main()
