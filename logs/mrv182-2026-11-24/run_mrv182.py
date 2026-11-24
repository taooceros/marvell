#!/usr/bin/env python3
"""MRV-182 n=3 interleaved experiment harness: fine-grained stage latency decomposition
of host-to-CN106 MMIO reads on saturn (Xeon Gold 6438M SPR + PCIe Gen5 x4 CN106).

Zero-risk, zero-root-blocking, 100% non-multiplexed PMU harness covering:
- Suite A (stage): Host CHA (PRd/UCRdF TOR occupancy) + M2PCIe_1 + IRP_1 + IIO_1 (part0)
  + Card 8-counter TAD (tad_req_occ, tad_ifb_occ, tad_TIME_ELAPSED) + 6-ch DDR5 PMU
- Suite B (core_scan): All 32 Socket 1 physical cores (32..63) + 8 Socket 0 cross-UPI cores
  on BAR4 line0, plus representative cores on BAR4 4K walk & VF0 BAR0, plus zero-root
  schedutil cold (~2.2 GHz) vs warm (~3.9 GHz) core-frequency delta on cpu41
- Suite C1 (host_mem): Local WB L1 / L2 / L3 / local DDR5 / remote Socket-0 DDR5 ladder
- Suite C2 (cfg_pread): Syscall-inclusive PCIe Config Space differential (98:01.0 vs 99:00.0 vs 99:02.0)
- Suite C3 (mlp): Single-core UC 8x unfenced serialization + multi-core K=1,2,4,8 BAR4 scaling
- Suite C4 (card_local): CN106 local Neoverse-N2 L3 vs DDR5 read latency via CNTVCT_EL0 + TAD PMU
"""

import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv182")
LOG_DIR = ROOT / "logs/mrv182-2026-11-24"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv182-target/release/mrv182_host"
CARD_BIN = "/root/bench/mrv182_card"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

REPS = 3
MAX_ATTEMPTS = 4
MIN_MESH_GHZ = 2.18
MIN_CPU_GHZ_WARM = 3.80
MIN_CPU_GHZ_ZMM = 3.00

STAGE_ARMS = [
    "timer-floor",
    "vf0-bar0-8b",
    "bar4-l3-line0-8b",
    "bar4-l3-walk-8b",
    "bar4-l3-walk-64b",
    "bar4-ddr5-walk-8b",
    "bar4-ddr5-walk-64b",
]

SCAN_LINE0_CPUS = list(range(32, 64)) + [0, 4, 8, 12, 16, 20, 24, 28]
SCAN_WALK_CPUS = [32, 36, 40, 44, 48, 52, 56, 60, 0, 8, 16, 24]
SCAN_VF0_CPUS = [32, 40, 48, 56, 0, 16]

HOST_MEM_ARMS = [
    "host-wb-l1",
    "host-wb-l2",
    "host-wb-l3",
    "host-wb-ddr5-local",
    "host-wb-ddr5-remote",
]

CFG_DEVS = ["0000:98:01.0", "0000:99:00.0", "0000:99:02.0"]

MLP_ARMS = [
    ("uc-unfenced-8x", "40", 20_000),
    ("multicore-k1", "40", 40_000),
    ("multicore-k2", "40,41", 40_000),
    ("multicore-k4", "40,41,42,43", 40_000),
    ("multicore-k8", "40,41,42,43,44,45,46,47", 40_000),
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
            "/sys/bus/pci/devices/0000:98:01.0/config",
            "/sys/bus/pci/devices/0000:98:01.0/resource0",
            "/sys/bus/pci/devices/0000:99:00.0/config",
            "/sys/bus/pci/devices/0000:99:00.0/resource0",
            "/sys/bus/pci/devices/0000:99:00.0/resource4",
            "/sys/bus/pci/devices/0000:99:00.0/resource4_wc",
            "/sys/bus/pci/devices/0000:99:02.0/config",
            "/sys/bus/pci/devices/0000:99:02.0/resource0",
            "/sys/devices/system/cpu/intel_uncore_frequency/package_01_die_01/min_freq_khz",
            "/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor",
            "/sys/devices/system/cpu/cpu41/cpufreq/scaling_governor",
        ]
    }
    return {
        "cpu40_governor": pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip(),
        "cpu41_governor": pathlib.Path("/sys/devices/system/cpu/cpu41/cpufreq/scaling_governor").read_text().strip(),
        "cpu0_governor": pathlib.Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text().strip(),
        "uncore_khz": uncore,
        "perf_event_paranoid": int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip()),
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
    subprocess.run(CARD_SSH + ["tmux kill-session -t refl 2>/dev/null || true; pkill -INT -f '[/]root/bench/reflector-rs' || true"], check=False)
    for _ in range(30):
        time.sleep(0.15)
        p = subprocess.run(CARD_SSH + ["pgrep -f '[/]root/bench/reflector-rs' || true"], capture_output=True, text=True)
        if not p.stdout.strip():
            return
    raise RuntimeError("reflector-rs did not stop cleanly")


def start_refl():
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
        f"nohup {CARD_BIN} -l 3 --no-pci --file-prefix=mrv182c --log-level=lib.eal:warning "
        f"-- --control 10.10.0.2:7526 --sync 10.10.0.2:7527 "
        f"> /tmp/mrv182_card.log 2>&1 < /dev/null & echo $!"
    )
    pid = subprocess.check_output(CARD_SSH + [cmd], text=True).strip()
    for _ in range(50):
        time.sleep(0.15)
        log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv182_card.log 2>/dev/null || true"], text=True)
        if "mrv182_card: BAR4 slot 15" in log:
            return pid, log.strip()
    log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv182_card.log 2>/dev/null || true"], text=True)
    raise RuntimeError(f"mrv182_card failed to start:\n{log}")


def run_cmd_verified(suite: str, label: str, rep: int, cmd: list[str], validator, pre_sleep: float = 0.0) -> dict:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if pre_sleep > 0:
            time.sleep(pre_sleep)
        t0 = utc_now()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        line = None
        for l in reversed(p.stdout.splitlines()):
            if l.strip().startswith("{"):
                line = l.strip()
                break
        rec = {
            "ts_utc": t0,
            "suite": suite,
            "label": label,
            "rep": rep,
            "attempt": attempt,
            "rc": p.returncode,
        }
        if line is None or p.returncode != 0:
            rec["accepted"] = False
            rec["reject_reason"] = f"rc={p.returncode} stderr={p.stderr.strip()[-300:]}"
            with open(ATTEMPTS_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")
            time.sleep(0.2)
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


def validate_stage(arm: str):
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
        min_cpu = MIN_CPU_GHZ_ZMM if arm.endswith("-64b") else MIN_CPU_GHZ_WARM
        if d["cpu_ghz"] < min_cpu:
            r.append(f"cpu_ghz={d['cpu_ghz']:.4f}<{min_cpu}")
        if d["mesh_ghz"] < MIN_MESH_GHZ:
            r.append(f"mesh_ghz={d['mesh_ghz']:.4f}<{MIN_MESH_GHZ}")
        return r
    return _v


def validate_core_scan(cold: bool = False):
    def _v(d: dict) -> list[str]:
        r = []
        if d["mismatches"] != 0:
            r.append(f"mismatches={d['mismatches']}")
        if d["page_faults"] != 0:
            r.append(f"page_faults={d['page_faults']}")
        if d["host_pmu_min_pct"] < 99.99:
            r.append(f"host_pmu_min_pct={d['host_pmu_min_pct']}")
        if cold:
            if d["cpu_ghz"] > 3.20:
                r.append(f"cold_cpu_ghz={d['cpu_ghz']:.4f}>3.20")
        else:
            if d["cpu_ghz"] < MIN_CPU_GHZ_WARM:
                r.append(f"cpu_ghz={d['cpu_ghz']:.4f}<{MIN_CPU_GHZ_WARM}")
        if d["mesh1_ghz"] < MIN_MESH_GHZ:
            r.append(f"mesh1_ghz={d['mesh1_ghz']:.4f}<{MIN_MESH_GHZ}")
        return r
    return _v


def validate_host_mem(d: dict) -> list[str]:
    r = []
    if d["mismatches"] != 0:
        r.append(f"mismatches={d['mismatches']}")
    if d["page_faults"] != 0:
        r.append(f"page_faults={d['page_faults']}")
    if d["host_pmu_min_pct"] < 99.99:
        r.append(f"host_pmu_min_pct={d['host_pmu_min_pct']}")
    if d["cpu_ghz"] < MIN_CPU_GHZ_WARM:
        r.append(f"cpu_ghz={d['cpu_ghz']:.4f}<{MIN_CPU_GHZ_WARM}")
    if d["mesh_ghz"] < MIN_MESH_GHZ:
        r.append(f"mesh_ghz={d['mesh_ghz']:.4f}<{MIN_MESH_GHZ}")
    if d["verified_node"] != d["numa_node"]:
        r.append(f"numa={d['verified_node']}!={d['numa_node']}")
    return r


def validate_cfg(d: dict) -> list[str]:
    r = []
    if d["mismatches"] != 0:
        r.append(f"mismatches={d['mismatches']}")
    if d["page_faults"] != 0:
        r.append(f"page_faults={d['page_faults']}")
    if d["host_pmu_min_pct"] < 99.99:
        r.append(f"host_pmu_min_pct={d['host_pmu_min_pct']}")
    if d["cpu_ghz"] < MIN_CPU_GHZ_WARM:
        r.append(f"cpu_ghz={d['cpu_ghz']:.4f}<{MIN_CPU_GHZ_WARM}")
    if d["mesh_ghz"] < MIN_MESH_GHZ:
        r.append(f"mesh_ghz={d['mesh_ghz']:.4f}<{MIN_MESH_GHZ}")
    return r


def validate_mlp(d: dict) -> list[str]:
    r = []
    if d["mismatches"] != 0:
        r.append(f"mismatches={d['mismatches']}")
    if d["page_faults"] != 0:
        r.append(f"page_faults={d['page_faults']}")
    if d["host_pmu_min_pct"] < 99.99:
        r.append(f"host_pmu_min_pct={d['host_pmu_min_pct']}")
    if d["card_pmu"].get("min_pct", 0.0) < 99.99:
        r.append(f"card_pmu_min_pct={d['card_pmu'].get('min_pct')}")
    min_c = d.get("min_cpu_ghz", d.get("cpu_ghz", 0.0))
    if min_c < 3.40:  # 8-core turbo can step modestly below single-core 3.9 GHz
        r.append(f"min_cpu_ghz={min_c:.4f}<3.40")
    if d["mesh_ghz"] < MIN_MESH_GHZ:
        r.append(f"mesh_ghz={d['mesh_ghz']:.4f}<{MIN_MESH_GHZ}")
    return r


def validate_card_local(d: dict) -> list[str]:
    res = d["result"]
    r = []
    if res["mismatches"] != 0:
        r.append(f"mismatches={res['mismatches']}")
    if res["l3_pmu"]["min_pct"] < 99.99:
        r.append(f"l3_min_pct={res['l3_pmu']['min_pct']}")
    if res["ddr_pmu"]["min_pct"] < 99.99:
        r.append(f"ddr_min_pct={res['ddr_pmu']['min_pct']}")
    return r


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for p in (RUNS_PATH, ATTEMPTS_PATH):
        if p.exists():
            p.unlink()

    host_before = read_host_env()
    card_before = read_card_env()
    print(f"Initial card state: {card_before}", flush=True)

    card_banner = ""
    restore_out = ""
    refl_pane = ""
    cleanup_errors: list[str] = []
    t_all = time.monotonic()
    try:
        stop_refl()
        card_pid, card_banner = start_card()
        print(f"Stopped refl; started mrv182_card pid={card_pid}: {card_banner}", flush=True)
        for rep in range(REPS):
            print(f"\n================ REP {rep}/{REPS - 1} ================", flush=True)

            # ---- Suite A: Stage PMU Decomposition (interleaved rotation) ----
            shift = (rep * 3) % len(STAGE_ARMS)
            order_a = STAGE_ARMS[shift:] + STAGE_ARMS[:shift]
            for arm in order_a:
                rds = "96000" if "-walk-" in arm else "100000"
                if arm == "vf0-bar0-8b":
                    cmd = [
                        HOST_BIN, "-l", "40", "-a", "0000:99:02.0",
                        "--file-prefix=mrv182h", "--log-level=lib.eal:warning", "--",
                        "--mode", "stage", "--arm", arm, "--rounds", rds, "--warmup", "5000",
                    ]
                else:
                    cmd = [
                        HOST_BIN, "--mode", "stage", "--arm", arm, "--cpu", "40",
                        "--rounds", rds, "--warmup", "5000",
                    ]
                rec = run_cmd_verified("stage", arm, rep, cmd, validate_stage(arm))
                cp = rec["card_pmu"]
                print(
                    f"[stage r{rep}] {arm:18s} net_p50={rec['net_ns']['p50']:6.1f} net_mean={rec['net_ns']['mean']:6.1f} "
                    f"TOR={rec['cha']['tor_residency_ns']:6.1f} core<->cha={rec['cha']['core_to_cha_est_ns']:5.1f} "
                    f"cha_ins={rec['cha']['ins']:6d} iio_txn={rec['iio1']['mem_rd_txn_p0']:6d} "
                    f"tad_in={cp['tad_req_in']:6d} tad_occ={cp['tad_req_occ']:8d} ifb_occ={cp['tad_ifb_occ']:8d}",
                    flush=True,
                )

            # ---- Suite C1: Host WB Memory Hierarchy Ladder ----
            shift_hm = (rep * 2) % len(HOST_MEM_ARMS)
            for arm in HOST_MEM_ARMS[shift_hm:] + HOST_MEM_ARMS[:shift_hm]:
                cmd = [
                    HOST_BIN, "--mode", "host-mem", "--arm", arm, "--cpu", "40",
                    "--rounds", "100000", "--warmup", "5000",
                ]
                rec = run_cmd_verified("host_mem", arm, rep, cmd, validate_host_mem)
                print(
                    f"[host_mem r{rep}] {arm:20s} net_p50={rec['net_ns']['p50']:6.1f} net_mean={rec['net_ns']['mean']:6.1f} "
                    f"cha({rec['cha']['event']})={rec['cha']['ins']:6d} tor={rec['cha']['tor_residency_ns']:5.1f}ns "
                    f"node={rec['verified_node']}",
                    flush=True,
                )

            # ---- Suite C2: Syscall-Inclusive PCIe Config Space Differential ----
            shift_cfg = rep % len(CFG_DEVS)
            for dev in CFG_DEVS[shift_cfg:] + CFG_DEVS[:shift_cfg]:
                cmd = [
                    HOST_BIN, "--mode", "cfg-pread", "--pci-dev", dev, "--cpu", "40",
                    "--rounds", "50000", "--warmup", "2000",
                ]
                rec = run_cmd_verified("cfg_pread", dev, rep, cmd, validate_cfg)
                print(
                    f"[cfg_pread r{rep}] {dev} vid={rec['vid_did']} net_p50={rec['net_ns']['p50']:6.1f} "
                    f"net_mean={rec['net_ns']['mean']:6.1f} iio_tlp={rec['iio1']['outbound_tlp']:5d} "
                    f"iio_clk/rd={rec['iio1']['iio_clk_per_read']:6.1f}",
                    flush=True,
                )

            # ---- Suite C3: MLP / Serialization & Multi-core Scaling ----
            shift_mlp = (rep * 2) % len(MLP_ARMS)
            for arm_name, cpus_str, rds in MLP_ARMS[shift_mlp:] + MLP_ARMS[:shift_mlp]:
                cmd = [
                    HOST_BIN, "--mode", "mlp", "--arm", arm_name, "--cpu", "40",
                    "--cpus", cpus_str, "--rounds", str(rds), "--warmup", "2000",
                ]
                rec = run_cmd_verified("mlp", arm_name, rep, cmd, validate_mlp)
                if arm_name == "uc-unfenced-8x":
                    print(
                        f"[mlp r{rep}] {arm_name:15s} 8x_p50={rec['net_ns']['p50']:7.1f}ns "
                        f"per_rd={rec['per_read_net_p50_ns']:6.1f}ns iio_txn={rec['iio1']['mem_rd_txn_p0']}",
                        flush=True,
                    )
                else:
                    print(
                        f"[mlp r{rep}] {arm_name:15s} K={rec['k_cores']} agg={rec['agg_mops']:5.2f} Mops/s "
                        f"mean_p50={rec['mean_core_net_p50_ns']:6.1f}ns tor={rec['cha']['tor_residency_ns']:6.1f}ns "
                        f"min_ghz={rec['min_cpu_ghz']:.2f}",
                        flush=True,
                    )

            # ---- Suite C4: CN106 Local ARM Core L3 vs DDR5 ----
            cmd = [HOST_BIN, "--mode", "card-local", "--rounds", "4000"]
            rec = run_cmd_verified("card_local", "cn106-local", rep, cmd, validate_card_local)
            res = rec["result"]
            print(
                f"[card_local r{rep}] L3_mean={res['l3_mean_ns']:6.1f}ns DDR5_mean={res['ddr_mean_ns']:6.1f}ns "
                f"delta={res['ddr_mean_ns']-res['l3_mean_ns']:+5.1f}ns",
                flush=True,
            )

            # ---- Suite B1: All 32 Socket-1 physical cores + 8 Socket-0 cores (BAR4 line 0) ----
            cpus_b1 = list(SCAN_LINE0_CPUS)
            if rep % 2 == 1:
                cpus_b1.reverse()
            for cpu in cpus_b1:
                cmd = [
                    HOST_BIN, "--mode", "core-scan", "--arm", "bar4-l3-line0-8b",
                    "--cpu", str(cpu), "--rounds", "20000", "--warmup", "2000",
                ]
                rec = run_cmd_verified("core_scan_line0", f"cpu{cpu}", rep, cmd, validate_core_scan(False))
                if cpu in (32, 40, 48, 56, 63, 0, 16, 28):
                    print(
                        f"[scan_line0 r{rep}] cpu={cpu:2d} S{rec['socket']} net_p50={rec['net_ns']['p50']:6.1f} "
                        f"S0_tor={rec['cha_s0']['tor_ns']:6.1f}({rec['cha_s0']['ins']}) "
                        f"S1_tor={rec['cha_s1']['tor_ns']:6.1f}({rec['cha_s1']['ins']}) "
                        f"upi_ncs={rec['m2pcie1']['upi_ncs_ins']} ghz={rec['cpu_ghz']:.2f}",
                        flush=True,
                    )
            print(f"[scan_line0 r{rep}] completed all {len(cpus_b1)} physical cores", flush=True)

            # ---- Suite B2: Representative cores on BAR4 4K walk (all 32 CHAs) ----
            cpus_b2 = list(SCAN_WALK_CPUS)
            if rep % 2 == 1:
                cpus_b2.reverse()
            for cpu in cpus_b2:
                cmd = [
                    HOST_BIN, "--mode", "core-scan", "--arm", "bar4-l3-walk-8b",
                    "--cpu", str(cpu), "--rounds", "20000", "--warmup", "2000",
                ]
                rec = run_cmd_verified("core_scan_walk", f"cpu{cpu}", rep, cmd, validate_core_scan(False))
            print(f"[scan_walk r{rep}] completed all {len(cpus_b2)} cores", flush=True)

            # ---- Suite B3: Representative cores on VF0 BAR0 OUT_CNTS (via EAL) ----
            cpus_b3 = list(SCAN_VF0_CPUS)
            if rep % 2 == 1:
                cpus_b3.reverse()
            for cpu in cpus_b3:
                cmd = [
                    HOST_BIN, "-l", "40", "-a", "0000:99:02.0",
                    "--file-prefix=mrv182h", "--log-level=lib.eal:warning", "--",
                    "--mode", "core-scan", "--arm", "vf0-bar0-8b", "--cpu", str(cpu),
                    "--rounds", "20000", "--warmup", "2000",
                ]
                rec = run_cmd_verified("core_scan_vf0", f"cpu{cpu}", rep, cmd, validate_core_scan(False))
                print(
                    f"[scan_vf0 r{rep}] cpu={cpu:2d} S{rec['socket']} net_p50={rec['net_ns']['p50']:6.1f} "
                    f"S0_tor={rec['cha_s0']['tor_ns']:6.1f} S1_tor={rec['cha_s1']['tor_ns']:6.1f} ghz={rec['cpu_ghz']:.2f}",
                    flush=True,
                )

            # ---- Suite B4: Zero-root Core-Clock Sensitivity (idle schedutil cold vs warm) ----
            for arm, test_cpu in (("bar4-l3-line0-8b", 41), ("bar4-l3-walk-8b", 42)):
                cmd_cold = [
                    HOST_BIN, "--mode", "core-scan", "--arm", arm,
                    "--cpu", str(test_cpu), "--cold", "--rounds", "20000", "--warmup", "200",
                ]
                rec_c = run_cmd_verified("core_freq_cold", f"cpu{test_cpu}_{arm}_cold", rep, cmd_cold, validate_core_scan(True), pre_sleep=2.0)
                cmd_warm = [
                    HOST_BIN, "--mode", "core-scan", "--arm", arm,
                    "--cpu", str(test_cpu), "--rounds", "20000", "--warmup", "2000",
                ]
                rec_w = run_cmd_verified("core_freq_warm", f"cpu{test_cpu}_{arm}_warm", rep, cmd_warm, validate_core_scan(False))
                print(
                    f"[core_freq r{rep}] cpu{test_cpu} {arm}: cold({rec_c['cpu_ghz']:.2f}G)={rec_c['net_ns']['p50']:.1f}ns "
                    f"(TOR={rec_c['cha_s1']['tor_ns']:.1f}) vs warm({rec_w['cpu_ghz']:.2f}G)={rec_w['net_ns']['p50']:.1f}ns "
                    f"(TOR={rec_w['cha_s1']['tor_ns']:.1f})",
                    flush=True,
                )

        elapsed = time.monotonic() - t_all
        print(f"\nAll suites completed in {elapsed:.1f}s!", flush=True)
    finally:
        print("\n=== RESTORING CARD ===", flush=True)
        try:
            stop_card()
        except Exception as e:
            cleanup_errors.append(f"stop_card: {e}")
            print(f"WARN stop_card: {e}", flush=True)
        try:
            restore_out = subprocess.check_output(CARD_SSH + [f"{CARD_BIN} --restore"], text=True).strip()
            print(f"restore: {restore_out}", flush=True)
        except Exception as e:
            cleanup_errors.append(f"restore: {e}")
            print(f"WARN restore: {e}", flush=True)
        try:
            refl_pane = start_refl()
            print(f"refl restored: {refl_pane.splitlines()[-1] if refl_pane else ''}", flush=True)
        except Exception as e:
            cleanup_errors.append(f"start_refl: {e}")
            print(f"WARN start_refl: {e}", flush=True)
    if cleanup_errors:
        raise RuntimeError("card cleanup errors: " + "; ".join(cleanup_errors))

    print("Waiting 21s for post-restore EDAC / SEI-UER recheck...", flush=True)
    time.sleep(21.0)
    host_after = read_host_env()
    card_after = read_card_env()
    env = {
        "generated_utc": utc_now(),
        "reps": REPS,
        "card_banner": card_banner,
        "restore_output": restore_out,
        "refl_restored_pane": refl_pane,
        "host_before": host_before,
        "host_after": host_after,
        "card_before": card_before,
        "card_after": card_after,
        "edac_clean": (
            all(v == 0 for v in host_after["host_edac_ue"].values())
            and all(v == 0 for v in host_after["host_edac_ce"].values())
            and card_after["mc0_ue"] == 0
            and card_after["mc0_ce"] == 0
            and card_after["sei_uer"] == card_before["sei_uer"]
        ),
        "card_restored_clean": (
            card_after["eth0_driver"] == "rvu_nicpf"
            and card_after["eth0_link"] == "100000Mb/s_yes"
            and card_after["refl_tmux"] == "running"
            and card_after["pem_wc_loaded"] == 0
        ),
    }
    ENV_PATH.write_text(json.dumps(env, indent=2) + "\n")
    print(f"Saved {ENV_PATH}: edac_clean={env['edac_clean']}, card_restored_clean={env['card_restored_clean']}", flush=True)
    if not (env["edac_clean"] and env["card_restored_clean"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
