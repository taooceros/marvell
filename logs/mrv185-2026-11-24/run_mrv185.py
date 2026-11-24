#!/usr/bin/env python3
"""MRV-185 n=3 interleaved experiment harness: cracking the 493.8 ns MMIO Read black box
on saturn (Xeon Gold 6438M SPR + Root Port 0000:98:01.0 + PCIe Gen5 x4 CN106).

Safety invariants:
- `/sys/bus/pci/devices/0000:98:01.0/resource0` is opened strictly `O_RDONLY` / `PROT_READ`
  at single offset `+0x0` (zero writes, zero sweeps).
- PCIe link speed/retrain and `ens1` are never touched.
- `try ... finally` unconditionally restores `package_01_die_01/{min,max}_freq_khz = 2200000`,
  `cpu40/cpufreq/scaling_{min,max}_freq = 3900000`, BAR4 firmware defaults (`mrv182_card --restore`),
  and `refl` tmux (`reflector-rs`), and verifies every restored value + post-21s EDAC/AER counts.
- Zero silent retries: every attempt (accepted or rejected) is recorded in `attempts.jsonl`.
"""

import hashlib
import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv185")
LOG_DIR = ROOT / "logs/mrv185-2026-11-24"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv185-target/release/mrv185_host"
CARD_BIN = "/root/bench/mrv182_card"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

UNC_DIR = pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency/package_01_die_01")
CPU40_DIR = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq")

REPS = 3
MAX_ATTEMPTS = 4
RESTORE_UNCORE_KHZ = 2200000
RESTORE_CORE_KHZ = 3900000

PART1_STAGE_ARMS = [
    ("timer-floor", 40_000),
    ("rp-bar0-4b", 40_000),
    ("rp-bar0-8b", 40_000),
    ("vf0-bar0-8b", 40_000),
    ("bar4-l3-line0-8b", 40_000),
    ("bar4-l3-walk-8b", 32_000),
    ("bar4-l3-walk-64b", 32_000),
    ("bar4-ddr5-walk-8b", 32_000),
    ("bar4-ddr5-walk-64b", 32_000),
]

PART1_NEAR_ARMS = [
    ("rp-bar0-4b", 40_000),
    ("rp-bar0-8b", 40_000),
    ("vf0-bar0-8b", 40_000),
    ("bar4-l3-walk-8b", 32_000),
]

PART1_IRP_ENDPOINTS = [
    ("rp-bar0-8b", 40_000),
    ("vf0-bar0-8b", 40_000),
    ("bar4-l3-walk-8b", 32_000),
    ("bar4-ddr5-walk-8b", 32_000),
]
PART1_IRP_MODES = ["occ-ncs", "clk-ins", "txc-ncs"]

PART2_UNCORE_KHZ = [800_000, 1_100_000, 1_400_000, 1_800_000, 2_200_000]
PART2_ENDPOINTS = [
    ("rp-bar0-8b", 40_000),
    ("vf0-bar0-8b", 40_000),
    ("bar4-l3-walk-8b", 32_000),
    ("bar4-ddr5-walk-8b", 32_000),
]

PART3_CORE_KHZ = [1_500_000, 2_200_000, 3_000_000, 3_900_000]
PART3_ENDPOINTS = [
    ("timer-floor", 40_000),
    ("rp-bar0-8b", 40_000),
    ("vf0-bar0-8b", 40_000),
    ("bar4-l3-walk-8b", 32_000),
]

PART4_WC_ARMS = [
    "wc-8b",
    "wc-16b-movdqu",
    "wc-16b-vmovntdqa",
    "wc-64b-4x16-vmovntdqa",
    "wc-64b-1x64-vmovntdqa",
    "wc-64b-vmovdqu64",
    "wc-64b-4x16-movdqu",
    "uc-64b-4x16-vmovntdqa",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_perm_info(path: str) -> dict:
    try:
        st = os.stat(path)
        return {
            "exists": True,
            "uid": st.st_uid,
            "gid": st.st_gid,
            "mode_oct": oct(stat.S_IMODE(st.st_mode)),
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
        }
    except OSError as e:
        return {"exists": False, "error": str(e)}


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
    raise RuntimeError(
        f"uncore failed to settle at {khz} kHz: min={mn} max={mx} cur={cur}"
    )


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
    perms = {
        p: file_perm_info(p)
        for p in [
            "/sys/bus/pci/devices/0000:98:01.0/resource0",
            "/sys/devices/system/cpu/intel_uncore_frequency/package_01_die_01/min_freq_khz",
            "/sys/devices/system/cpu/intel_uncore_frequency/package_01_die_01/max_freq_khz",
            "/sys/devices/system/cpu/cpu40/cpufreq/scaling_min_freq",
            "/sys/devices/system/cpu/cpu40/cpufreq/scaling_max_freq",
            "/sys/bus/pci/devices/0000:99:00.0/resource4",
            "/sys/bus/pci/devices/0000:99:00.0/resource4_wc",
        ]
    }
    return {
        "cpu40": {
            "governor": (CPU40_DIR / "scaling_governor").read_text().strip(),
            "scaling_min_freq": int((CPU40_DIR / "scaling_min_freq").read_text().strip()),
            "scaling_max_freq": int((CPU40_DIR / "scaling_max_freq").read_text().strip()),
            "scaling_cur_freq": int((CPU40_DIR / "scaling_cur_freq").read_text().strip()),
        },
        "cpu48_governor": pathlib.Path(
            "/sys/devices/system/cpu/cpu48/cpufreq/scaling_governor"
        ).read_text().strip(),
        "uncore_khz": uncore,
        "perf_event_paranoid": int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip()),
        "rp_98_01_0_aer": read_aer_totals(),
        "host_edac_ue": edac_ue,
        "host_edac_ce": edac_ce,
        "permissions": perms,
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
        f"nohup {CARD_BIN} -l 3 --no-pci --file-prefix=mrv185c --log-level=lib.eal:warning "
        f"-- --control 10.10.0.2:7526 --sync 10.10.0.2:7527 "
        f"> /tmp/mrv185_card.log 2>&1 < /dev/null & echo $!"
    )
    pid = subprocess.check_output(CARD_SSH + [cmd], text=True).strip()
    for _ in range(50):
        time.sleep(0.15)
        log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv185_card.log 2>/dev/null || true"], text=True)
        if "mrv182_card: BAR4 slot 15" in log:
            return pid, log.strip()
    log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv185_card.log 2>/dev/null || true"], text=True)
    raise RuntimeError(f"mrv182_card failed to start:\n{log}")


def stage_cmd(arm: str, cpu: int, rounds: int, irp_mode: str = "occ-clk", target_ghz: float = 3.85) -> list[str]:
    if arm == "vf0-bar0-8b":
        return [
            HOST_BIN,
            "-l", str(cpu),
            "-a", "0000:99:02.0",
            "--file-prefix=mrv185",
            "--log-level=lib.eal:warning",
            "--",
            "--mode", "stage",
            "--arm", arm,
            "--cpu", str(cpu),
            "--irp-mode", irp_mode,
            "--target-ghz", f"{target_ghz:.2f}",
            "-n", str(rounds),
        ]
    return [
        HOST_BIN,
        "--mode", "stage",
        "--arm", arm,
        "--cpu", str(cpu),
        "--irp-mode", irp_mode,
        "--target-ghz", f"{target_ghz:.2f}",
        "-n", str(rounds),
    ]


def wc_cmd(arm: str, rounds: int = 32_000) -> list[str]:
    return [
        HOST_BIN,
        "--mode", "wc",
        "--arm", arm,
        "--cpu", "40",
        "--irp-mode", "occ-clk",
        "--target-ghz", "3.85",
        "-n", str(rounds),
    ]


def run_cmd_verified(
    suite: str,
    label: str,
    rep: int,
    cmd: list[str],
    validator,
    campaign_id: int = 1,
    meta: dict | None = None,
) -> dict:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        t0 = utc_now()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        line = None
        for l in reversed(p.stdout.splitlines()):
            if l.strip().startswith("{"):
                line = l.strip()
                break
        rec = {
            "campaign_id": campaign_id,
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
        time.sleep(0.25)
    raise RuntimeError(f"suite={suite} label={label} rep={rep} failed after {MAX_ATTEMPTS} attempts")


def make_validator(
    expect_uncore_ghz: float,
    expect_core_ghz: float | None = None,
    min_core_ghz: float = 3.80,
    check_m2p: bool = True,
):
    def _v(d: dict) -> list[str]:
        r = []
        if d["mismatches"] != 0:
            r.append(f"mismatches={d['mismatches']}")
        if d["page_faults"] != 0:
            r.append(f"page_faults={d['page_faults']}")
        if d["host_pmu_min_pct"] < 99.99:
            r.append(f"host_pmu_min_pct={d['host_pmu_min_pct']}")
        if d["card_pmu"].get("min_pct", 0.0) < 99.99:
            r.append(f"card_pmu_min_pct={d['card_pmu'].get('min_pct')}")
        ap = d.get("aperf_mperf_ghz", 0.0)
        cg = d.get("cpu_ghz", 0.0)
        if expect_core_ghz is not None:
            if abs(ap - expect_core_ghz) > 0.06:
                r.append(f"aperf_mperf_ghz={ap:.4f}!=target {expect_core_ghz:.2f}")
            if abs(cg - expect_core_ghz) > 0.06:
                r.append(f"cpu_ghz={cg:.4f}!=target {expect_core_ghz:.2f}")
        else:
            if ap < min_core_ghz:
                r.append(f"aperf_mperf_ghz={ap:.4f}<{min_core_ghz}")
            if cg < min_core_ghz:
                r.append(f"cpu_ghz={cg:.4f}<{min_core_ghz}")
        mg = d.get("mesh_ghz", 0.0)
        if abs(mg - expect_uncore_ghz) > 0.03:
            r.append(f"mesh_ghz={mg:.4f}!=target {expect_uncore_ghz:.2f}")
        if check_m2p and "m2pcie1" in d and "ghz" in d["m2pcie1"]:
            m2g = d["m2pcie1"]["ghz"]
            if abs(m2g - expect_uncore_ghz) > 0.03:
                r.append(f"m2p_ghz={m2g:.4f}!=target {expect_uncore_ghz:.2f}")
        return r
    return _v


def log_progress(msg: str):
    print(f"[{utc_now()}] {msg}", flush=True)


def main():
    def _sig_handler(signum, _frame):
        raise SystemExit(f"received signal {signum}")

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Append-only attempts.jsonl: determine next campaign_id without ever deleting prior attempts.
    prev_campaign = 0
    if ATTEMPTS_PATH.exists():
        for line in ATTEMPTS_PATH.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                prev_campaign = max(prev_campaign, int(d.get("campaign_id", 1)))
    campaign_id = prev_campaign + 1
    if RUNS_PATH.exists() and RUNS_PATH.stat().st_size > 0:
        archive = LOG_DIR / f"runs_campaign{prev_campaign or 1}_aborted.jsonl"
        RUNS_PATH.rename(archive)
        log_progress(f"Archived prior partial runs.jsonl -> {archive.name}")
    if ENV_PATH.exists() and ENV_PATH.stat().st_size > 0 and prev_campaign >= 1:
        env_archive = LOG_DIR / f"env_campaign{prev_campaign}_aborted.json"
        ENV_PATH.rename(env_archive)
        log_progress(f"Archived prior env.json -> {env_archive.name}")

    env_doc = {
        "issue": "MRV-185",
        "campaign_id": campaign_id,
        "started_utc": utc_now(),
        "host_before": read_host_env(),
        "card_before": read_card_env(),
    }

    total_ok = 0
    try:
        log_progress(f"Starting MRV-185 campaign {campaign_id}: pinning baseline uncore=2200000 kHz, cpu40=3900000 kHz")
        set_uncore_khz(RESTORE_UNCORE_KHZ)
        set_cpu40_khz(RESTORE_CORE_KHZ)

        log_progress("Stopping refl and starting mrv182_card")
        stop_refl()
        card_pid, card_start_log = start_card()
        env_doc["card_start"] = {"pid": card_pid, "log": card_start_log}

        # ------------------------------------------------------------------
        # PART 1: Crack 493.8 ns black box (RP resource0 + VF0 + BAR4 L3/DDR5)
        #         on cpu40 & near-CHA cpu48 + 4-mode non-mux IRP_1 bank
        # ------------------------------------------------------------------
        log_progress("=== PART 1A: 6-stage endpoint matrix on cpu40 (n=3 interleaved) ===")
        for rep in range(1, REPS + 1):
            arms = PART1_STAGE_ARMS if rep % 2 == 1 else list(reversed(PART1_STAGE_ARMS))
            for arm, rounds in arms:
                min_c = 3.00 if arm.endswith("-64b") else 3.80
                r = run_cmd_verified(
                    "part1_stage",
                    f"cpu40/{arm}",
                    rep,
                    stage_cmd(arm, 40, rounds, irp_mode="occ-clk", target_ghz=3.85),
                    make_validator(2.20, min_core_ghz=min_c),
                    campaign_id=campaign_id,
                    meta={"set_uncore_khz": 2200000, "set_core_khz": 3900000},
                )
                total_ok += 1
                log_progress(
                    f"  P1A rep={rep} {arm}: net_p50={r['net_ns']['p50']:.2f} "
                    f"mean={r['net_ns']['mean']:.2f} TOR={r['cha']['tor_residency_ns']:.2f} "
                    f"irp0c={r['irp1']['c0']} aperf={r['aperf_mperf_ghz']:.3f} mesh={r['mesh_ghz']:.3f}"
                )

        log_progress("=== PART 1B: Near-CHA cpu48 verification (n=3 interleaved, schedutil) ===")
        for rep in range(1, REPS + 1):
            arms = PART1_NEAR_ARMS if rep % 2 == 1 else list(reversed(PART1_NEAR_ARMS))
            for arm, rounds in arms:
                # cpu48 uses schedutil (only cpu40 cpufreq is chowned); walk arms settle ~3.60 GHz due to SYNC RPCs.
                min_c = 3.50 if arm == "bar4-l3-walk-8b" else 3.80
                r = run_cmd_verified(
                    "part1_near",
                    f"cpu48/{arm}",
                    rep,
                    stage_cmd(arm, 48, rounds, irp_mode="occ-clk", target_ghz=3.85),
                    make_validator(2.20, min_core_ghz=min_c),
                    campaign_id=campaign_id,
                    meta={"set_uncore_khz": 2200000, "set_core_khz": None, "cpu48_governor": "schedutil"},
                )
                total_ok += 1
                log_progress(
                    f"  P1B rep={rep} cpu48/{arm}: net_p50={r['net_ns']['p50']:.2f} "
                    f"mean={r['net_ns']['mean']:.2f} TOR={r['cha']['tor_residency_ns']:.2f} "
                    f"aperf={r['aperf_mperf_ghz']:.3f}"
                )

        log_progress("=== PART 1C: 100% non-mux IRP_1 4-counter calibration (occ-ncs, clk-ins, txc-ncs) ===")
        for rep in range(1, REPS + 1):
            for irp_mode in PART1_IRP_MODES:
                for arm, rounds in PART1_IRP_ENDPOINTS:
                    r = run_cmd_verified(
                        "part1_irp",
                        f"{irp_mode}/{arm}",
                        rep,
                        stage_cmd(arm, 40, rounds, irp_mode=irp_mode, target_ghz=3.85),
                        make_validator(2.20, min_core_ghz=3.80),
                        campaign_id=campaign_id,
                        meta={"set_uncore_khz": 2200000, "set_core_khz": 3900000},
                    )
                    total_ok += 1
                    log_progress(
                        f"  P1C rep={rep} {irp_mode}/{arm}: c0({r['irp1']['c0_name']})={r['irp1']['c0']} "
                        f"c1({r['irp1']['c1_name']})={r['irp1']['c1']} occ_cyc={r['irp1']['occ_cyc_per_read']:.4f}"
                    )

        # ------------------------------------------------------------------
        # PART 2: Socket 1 Uncore sweep (0.8 / 1.1 / 1.4 / 1.8 / 2.2 GHz)
        # ------------------------------------------------------------------
        log_progress("=== PART 2: Socket 1 Uncore sweep 0.8..2.2 GHz (n=3 interleaved) ===")
        set_cpu40_khz(RESTORE_CORE_KHZ)
        for rep in range(1, REPS + 1):
            freqs = PART2_UNCORE_KHZ if rep % 2 == 1 else list(reversed(PART2_UNCORE_KHZ))
            for f_khz in freqs:
                f_ghz = f_khz / 1e6
                set_uncore_khz(f_khz)
                for arm, rounds in PART2_ENDPOINTS:
                    r = run_cmd_verified(
                        "part2_uncore",
                        f"u{f_khz}/{arm}",
                        rep,
                        stage_cmd(arm, 40, rounds, irp_mode="occ-clk", target_ghz=3.85),
                        make_validator(f_ghz, min_core_ghz=3.80),
                        campaign_id=campaign_id,
                        meta={"set_uncore_khz": f_khz, "set_core_khz": 3900000},
                    )
                    total_ok += 1
                    log_progress(
                        f"  P2 rep={rep} u={f_ghz:.1f}G {arm}: net_p50={r['net_ns']['p50']:.2f} "
                        f"mean={r['net_ns']['mean']:.2f} TOR={r['cha']['tor_residency_ns']:.2f} "
                        f"mesh={r['mesh_ghz']:.4f} m2p={r['m2pcie1']['ghz']:.4f} iio={r['iio1']['active_ghz']:.4f}"
                    )
        # Immediately restore Uncore to 2.20 GHz after Part 2.
        set_uncore_khz(RESTORE_UNCORE_KHZ)

        # ------------------------------------------------------------------
        # PART 3: cpu40 Core sweep (1.5 / 2.2 / 3.0 / 3.9 GHz) at Uncore 2.20 GHz
        # ------------------------------------------------------------------
        log_progress("=== PART 3: cpu40 Core sweep 1.5..3.9 GHz at Uncore 2.20 GHz (n=3 interleaved) ===")
        set_uncore_khz(RESTORE_UNCORE_KHZ)
        for rep in range(1, REPS + 1):
            freqs = PART3_CORE_KHZ if rep % 2 == 1 else list(reversed(PART3_CORE_KHZ))
            for f_khz in freqs:
                f_ghz = f_khz / 1e6
                set_cpu40_khz(f_khz)
                for arm, rounds in PART3_ENDPOINTS:
                    r = run_cmd_verified(
                        "part3_core",
                        f"c{f_khz}/{arm}",
                        rep,
                        stage_cmd(arm, 40, rounds, irp_mode="occ-clk", target_ghz=0.0),
                        make_validator(2.20, expect_core_ghz=f_ghz),
                        campaign_id=campaign_id,
                        meta={"set_uncore_khz": 2200000, "set_core_khz": f_khz},
                    )
                    total_ok += 1
                    log_progress(
                        f"  P3 rep={rep} c={f_ghz:.1f}G {arm}: raw_mean={r['raw_ns']['mean']:.2f} "
                        f"fl_p50={r['floor_ns']['p50']:.2f} net_mean={r['net_ns']['mean']:.2f} "
                        f"TOR={r['cha']['tor_residency_ns']:.2f} aperf={r['aperf_mperf_ghz']:.4f}"
                    )
        # Immediately restore cpu40 to 3.90 GHz after Part 3.
        set_cpu40_khz(RESTORE_CORE_KHZ)

        # ------------------------------------------------------------------
        # PART 4: BAR4 WC (`resource4_wc`) vs UC control read comparison
        # ------------------------------------------------------------------
        log_progress("=== PART 4: BAR4 WC (resource4_wc) vs UC read matrix (n=3 interleaved) ===")
        for rep in range(1, REPS + 1):
            arms = PART4_WC_ARMS if rep % 2 == 1 else list(reversed(PART4_WC_ARMS))
            for arm in arms:
                min_c = 3.00 if ("vmovdqu64" in arm or "1x64" in arm) else 3.80
                r = run_cmd_verified(
                    "part4_wc",
                    arm,
                    rep,
                    wc_cmd(arm, rounds=32_000),
                    make_validator(2.20, min_core_ghz=min_c, check_m2p=False),
                    campaign_id=campaign_id,
                    meta={"set_uncore_khz": 2200000, "set_core_khz": 3900000},
                )
                total_ok += 1
                log_progress(
                    f"  P4 rep={rep} {arm}: net_p50={r['net_ns']['p50']:.2f} "
                    f"tlp/op={r['iio1']['tlp_per_op']:.4f} cmpd_B={r['iio1']['cmpd_bytes_per_op']:.1f} "
                    f"PRd={r['cha']['prd_ins']} UCRdF={r['cha']['ucrdf_ins']}"
                )

        log_progress(f"ALL 4 PARTS COMPLETED: {total_ok} verified runs!")

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
        ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
        log_progress(f"Wrote {ENV_PATH}; restore_errors={restore_errors}")
        if restore_errors:
            raise RuntimeError(f"Restore verification errors: {restore_errors}")


if __name__ == "__main__":
    main()
