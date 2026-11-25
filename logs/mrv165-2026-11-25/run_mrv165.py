#!/usr/bin/env python3
"""MRV-165 full n=3 interleaved verified experiment harness.

Runs two single-variable controlled experiments on the direct BAR4 / PEM0 WC
fast path (MRV-26 lease, zero SDP, zero Intel DSA):
  - Part 1  (control1_kv_buf):   1 card core + concurrent Socket-1 32 MiB KV random
                                 lookup on cpu41, varying only host RX buffer org
                                 across 4 arms (monotonic 128M, lifo 128M,
                                 passive drop 256K, active NIC 256K) + kv-solo.
  - Part 1b (control1b_kv16m):   16 MiB KV sensitivity check at 128 MiB burst + solo.
  - Part 2  (control2_multicore): KV off, burst 1-64 MiB, scaling card parallel
                                 PEM0 WC refill cores 1 -> 2 -> 4 vs Scheme A 128M
                                 (+ 4c 1 MiB ring credit-starvation diagnostic).
"""

import json
import pathlib
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv165")
LOG_DIR = ROOT / "logs/mrv165-2026-11-25"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv165-target/release/mrv165_host"
EAL_ARGS = ["-l", "40", "-a", "0000:99:02.0", "--file-prefix=mrv165", "--log-level=lib.eal:warning"]
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

REPS = 3
MIN_CORE_GHZ = 3.85
MIN_MESH_GHZ = 2.18
MAX_ATTEMPTS = 6

KB = 1024
MB = 1024 * 1024

# Control 1: 512 MiB offered payload per run across 8 burst sizes
C1_BURSTS = [
    ("64KiB",  64 * KB,   8192),
    ("256KiB", 256 * KB,  2048),
    ("1MiB",   1 * MB,    512),
    ("4MiB",   4 * MB,    128),
    ("16MiB",  16 * MB,   32),
    ("32MiB",  32 * MB,   16),
    ("64MiB",  64 * MB,   8),
    ("128MiB", 128 * MB,  4),
]

C1_ARMS = [
    ("monotonic_128m", "monotonic", 128 * MB),
    ("lifo_128m",      "lifo",      128 * MB),
    ("drop_256k",      "drop",      256 * KB),
    ("active_256k",    "active",    256 * KB),
]

# Control 2: 1-64 MiB burst backlog scaling card cores 1 -> 2 -> 4 vs Scheme A 128M
C2_BURSTS = [
    ("1MiB",  1 * MB,  512),
    ("4MiB",  4 * MB,  128),
    ("8MiB",  8 * MB,  64),
    ("16MiB", 16 * MB, 32),
    ("32MiB", 32 * MB, 16),
    ("48MiB", 48 * MB, 10),
    ("64MiB", 64 * MB, 8),
]


def parse_aer_total(path: pathlib.Path) -> int:
    if not path.exists():
        return -1
    for line in path.read_text().splitlines():
        if line.startswith("TOTAL_ERR_"):
            return int(line.split()[-1])
    return 0


def read_host_env() -> dict:
    uncore = {}
    for d in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("package_*")):
        uncore[d.name] = {
            "min": int((d / "min_freq_khz").read_text().strip()),
            "max": int((d / "max_freq_khz").read_text().strip()),
            "cur": int((d / "current_freq_khz").read_text().strip()),
        }
    edac_ue = {}
    edac_ce = {}
    for mc in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*")):
        edac_ue[mc.name] = int((mc / "ue_count").read_text().strip())
        edac_ce[mc.name] = int((mc / "ce_count").read_text().strip())
    rp = pathlib.Path("/sys/bus/pci/devices/0000:98:01.0")
    return {
        "cpu40_governor": pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip(),
        "cpu40_khz": int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip()),
        "cpu41_governor": pathlib.Path("/sys/devices/system/cpu/cpu41/cpufreq/scaling_governor").read_text().strip(),
        "uncore_khz": uncore,
        "perf_event_paranoid": int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip()),
        "node1_hugepages_2m_total": int(
            pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages").read_text().strip()
        ),
        "node1_hugepages_2m_free": int(
            pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages").read_text().strip()
        ),
        "host_edac_ue": edac_ue,
        "host_edac_ce": edac_ce,
        "rp_98_01_aer": {
            "fatal": parse_aer_total(rp / "aer_dev_fatal"),
            "nonfatal": parse_aer_total(rp / "aer_dev_nonfatal"),
            "correctable": parse_aer_total(rp / "aer_dev_correctable"),
        },
    }


def read_card_env() -> dict:
    cmd = (
        "echo cpu3_khz=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000); "
        "echo pem_wc=$(lsmod | grep -c '^pem_wc ' || true); "
        "echo eth0_oper=$(cat /sys/class/net/eth0/operstate 2>/dev/null || echo n/a); "
        "echo eth0_speed=$(cat /sys/class/net/eth0/speed 2>/dev/null || echo n/a); "
        "echo edac_ue=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo -1); "
        "echo edac_ce=$(cat /sys/devices/system/edac/mc/mc0/ce_count 2>/dev/null || echo -1); "
        "echo hp_total=$(cat /sys/kernel/mm/hugepages/hugepages-524288kB/nr_hugepages); "
        "echo hp_free=$(cat /sys/kernel/mm/hugepages/hugepages-524288kB/free_hugepages)"
    )
    out = subprocess.check_output(CARD_SSH + [cmd], text=True)
    kv = {}
    for line in out.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    return {
        "card_cpu3_khz": int(kv["cpu3_khz"]),
        "pem_wc_loaded": int(kv["pem_wc"]),
        "eth0_operstate": kv["eth0_oper"],
        "eth0_speed_mbps": int(kv["eth0_speed"]) if kv["eth0_speed"].isdigit() else kv["eth0_speed"],
        "card_edac_mc0_ue": int(kv["edac_ue"]),
        "card_edac_mc0_ce": int(kv["edac_ce"]),
        "card_hugepages_512m_total": int(kv["hp_total"]),
        "card_hugepages_512m_free": int(kv["hp_free"]),
    }


def verify_card_sync():
    with socket.create_connection(("10.10.0.2", 7527), timeout=3.0) as s:
        s.sendall(b"PING\n")
        resp = s.recv(64).decode().strip()
        assert resp == "OK", f"card sync PING failed: {resp!r}"


def check_gates(rec: dict, expected_drop_per_burst: int) -> list[str]:
    reasons = []
    mode = rec["mode"]
    hp = rec["host_pmu"]
    cp = rec["card_pmu"]
    kv = rec["kv"]

    if rec["lost"] != 0 or rec["bad"] != 0 or rec["torn"] != 0:
        reasons.append(f"integrity lbt={rec['lost']}/{rec['bad']}/{rec['torn']}")
    if hp["min_pct"] < 99.99:
        reasons.append(f"host_pmu min_pct={hp['min_pct']:.2f}")
    if hp["mesh_ghz"] < MIN_MESH_GHZ:
        reasons.append(f"mesh_ghz={hp['mesh_ghz']:.3f}")

    if mode != "kv-solo":
        if hp["rx"]["ghz"] < MIN_CORE_GHZ:
            reasons.append(f"rx_ghz={hp['rx']['ghz']:.3f}")
        if hp["rx"]["page_faults"] != 0:
            reasons.append(f"rx_pf={hp['rx']['page_faults']}")
        if rec["ring_page_kb"] != 2048 or rec["ring_n0"] != 0 or rec["ring_n1"] <= 0:
            reasons.append(f"ring_numa={rec['ring_page_kb']}k/N0={rec['ring_n0']}/N1={rec['ring_n1']}")
        if cp is None:
            reasons.append("missing card_pmu")
        else:
            if cp["min_pct"] < 99.99:
                reasons.append(f"card_pmu min_pct={cp['min_pct']:.2f}")
            exp_drop = expected_drop_per_burst * rec["bursts"]
            if rec["dropped_cells"] != exp_drop:
                reasons.append(f"host_drop={rec['dropped_cells']}!=exp={exp_drop}")
            if cp["dropped_cells"] != exp_drop:
                reasons.append(f"card_drop={cp['dropped_cells']}!=exp={exp_drop}")

    if kv is not None:
        kp = hp["kv"]
        if kv["bad"] != 0:
            reasons.append(f"kv_bad={kv['bad']}")
        if kv["page_faults"] != 0 or kp["page_faults"] != 0:
            reasons.append(f"kv_pf={kv['page_faults']}/{kp['page_faults']}")
        if kp["ghz"] < MIN_CORE_GHZ:
            reasons.append(f"kv_ghz={kp['ghz']:.3f}")
        if rec["kv_page_kb"] != 2048 or rec["kv_n0"] != 0 or rec["kv_n1"] <= 0:
            reasons.append(f"kv_numa={rec['kv_page_kb']}k/N0={rec['kv_n0']}/N1={rec['kv_n1']}")

    return reasons


def run_one(run_id: int, part: str, tag: str, rep: int, app_args: list[str], meta: dict) -> dict:
    cmd = [HOST_BIN] + EAL_ARGS + ["--"] + app_args
    exp_drop_per_burst = meta.get("expected_drop_per_burst", 0)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            print(
                f"  [retry {attempt}/{MAX_ATTEMPTS}] run_id={run_id} {part}/{tag} rep={rep} rc={p.returncode}: "
                f"{p.stderr.strip()[:200]}",
                flush=True,
            )
            time.sleep(0.5)
            continue
        lines = [l for l in p.stdout.splitlines() if l.startswith("MRV165 ")]
        if not lines:
            print(f"  [retry {attempt}/{MAX_ATTEMPTS}] missing MRV165 JSON line", flush=True)
            time.sleep(0.5)
            continue
        rec = json.loads(lines[-1].split("MRV165 ", 1)[1])
        reasons = check_gates(rec, exp_drop_per_burst)
        if reasons:
            print(
                f"  [retry {attempt}/{MAX_ATTEMPTS}] run_id={run_id} {part}/{tag} rep={rep} gate fail: "
                + ", ".join(reasons),
                flush=True,
            )
            time.sleep(0.3)
            continue
        full = {
            "run_id": run_id,
            "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "part": part,
            "tag": tag,
            "rep": rep,
            "attempt": attempt,
            "cmd": " ".join(cmd),
        }
        full.update(meta)
        full.update(rec)
        return full
    raise RuntimeError(f"run_id={run_id} {part}/{tag} rep={rep} failed quality gates after {MAX_ATTEMPTS} attempts")


def build_schedule() -> list[tuple[str, str, int, list[str], dict]]:
    sched = []

    # ---- Part 1: Control 1 (32 MiB KV, 1 card core, 4 host buffer arms across 8 burst sizes + kv-solo) ----
    for rep in range(REPS):
        sched.append((
            "control1_kv_buf",
            "kv_solo_32m",
            rep,
            ["--mode", "kv-solo", "--kv-bytes", str(32 * MB), "--solo-ms", "250", "--warmup-ms", "1100"],
            {"arm": "kv_solo", "burst_label": "0", "expected_drop_per_burst": 0},
        ))
        # Rotate arm order per rep to eliminate thermal/order bias
        rot_arms = C1_ARMS[rep % len(C1_ARMS):] + C1_ARMS[:rep % len(C1_ARMS)]
        for b_lbl, b_bytes, n_bursts in C1_BURSTS:
            b_cells = b_bytes // 64
            for arm_name, mode, ring_bytes in rot_arms:
                ring_cells = ring_bytes // 64
                exp_drop = max(0, b_cells - ring_cells) if mode == "drop" else 0
                sched.append((
                    "control1_kv_buf",
                    f"{arm_name}_{b_lbl}",
                    rep,
                    [
                        "--mode", mode,
                        "--host-ring-bytes", str(ring_bytes),
                        "--card-cores", "1",
                        "--burst-bytes", str(b_bytes),
                        "--bursts", str(n_bursts),
                        "--warmup-bytes", str(128 * MB),
                        "--credit-batch", "512",
                        "--pace-ns", "10",
                        "--kv-bytes", str(32 * MB),
                        "--kv-cpu", "41",
                        "--warmup-ms", "1100",
                    ],
                    {"arm": arm_name, "burst_label": b_lbl, "expected_drop_per_burst": exp_drop},
                ))

    # ---- Part 1b: 16 MiB KV sensitivity check (solo + 128 MiB burst across 4 arms) ----
    for rep in range(REPS):
        sched.append((
            "control1b_kv16m",
            "kv_solo_16m",
            rep,
            ["--mode", "kv-solo", "--kv-bytes", str(16 * MB), "--solo-ms", "250", "--warmup-ms", "1100"],
            {"arm": "kv_solo", "burst_label": "0", "expected_drop_per_burst": 0},
        ))
        rot_arms = C1_ARMS[rep % len(C1_ARMS):] + C1_ARMS[:rep % len(C1_ARMS)]
        b_bytes, n_bursts = 128 * MB, 4
        b_cells = b_bytes // 64
        for arm_name, mode, ring_bytes in rot_arms:
            ring_cells = ring_bytes // 64
            exp_drop = max(0, b_cells - ring_cells) if mode == "drop" else 0
            sched.append((
                "control1b_kv16m",
                f"{arm_name}_128MiB_kv16m",
                rep,
                [
                    "--mode", mode,
                    "--host-ring-bytes", str(ring_bytes),
                    "--card-cores", "1",
                    "--burst-bytes", str(b_bytes),
                    "--bursts", str(n_bursts),
                    "--warmup-bytes", str(128 * MB),
                    "--credit-batch", "512",
                    "--pace-ns", "10",
                    "--kv-bytes", str(16 * MB),
                    "--kv-cpu", "41",
                    "--warmup-ms", "1100",
                ],
                {"arm": arm_name, "burst_label": "128MiB", "expected_drop_per_burst": exp_drop},
            ))

    # ---- Part 2: Control 2 (KV off, burst 1-64 MiB, Scheme A 128M vs Active 1/2/4 card cores + 1M-ring diag) ----
    c2_configs = [
        ("scheme_a_128m", "monotonic", 128 * MB, 1, 512),
        ("active_1c",     "active",    256 * KB, 1, 512),
        ("active_2c",     "active",    256 * KB, 2, 512),
        ("active_4c",     "active",    256 * KB, 4, 512),
    ]
    for rep in range(REPS):
        rot_cfg = c2_configs[rep % len(c2_configs):] + c2_configs[:rep % len(c2_configs)]
        for b_lbl, b_bytes, n_bursts in C2_BURSTS:
            for arm_name, mode, ring_bytes, c_cores, cb in rot_cfg:
                sched.append((
                    "control2_multicore",
                    f"{arm_name}_{b_lbl}",
                    rep,
                    [
                        "--mode", mode,
                        "--host-ring-bytes", str(ring_bytes),
                        "--card-cores", str(c_cores),
                        "--burst-bytes", str(b_bytes),
                        "--bursts", str(n_bursts),
                        "--warmup-bytes", str(128 * MB),
                        "--credit-batch", str(cb),
                        "--pace-ns", "10",
                        "--kv-bytes", "0",
                        "--warmup-ms", "100",
                    ],
                    {"arm": arm_name, "burst_label": b_lbl, "expected_drop_per_burst": 0},
                ))
        # Credit-starvation diagnostic: 4 card cores with 1 MiB host ring & credit_batch=256
        for b_lbl, b_bytes, n_bursts in [("16MiB", 16 * MB, 32), ("64MiB", 64 * MB, 8)]:
            sched.append((
                "control2_diag",
                f"active_4c_1mring_{b_lbl}",
                rep,
                [
                    "--mode", "active",
                    "--host-ring-bytes", str(1 * MB),
                    "--card-cores", "4",
                    "--burst-bytes", str(b_bytes),
                    "--bursts", str(n_bursts),
                    "--warmup-bytes", str(128 * MB),
                    "--credit-batch", "256",
                    "--pace-ns", "10",
                    "--kv-bytes", "0",
                    "--warmup-ms", "100",
                ],
                {"arm": "active_4c_1mring", "burst_label": b_lbl, "expected_drop_per_burst": 0},
            ))

    return sched


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    verify_card_sync()
    host_before = read_host_env()
    card_before = read_card_env()
    print(f"Host before: {json.dumps(host_before)}", flush=True)
    print(f"Card before: {json.dumps(card_before)}", flush=True)

    sched = build_schedule()
    total = len(sched)
    print(f"Scheduled {total} verified runs (n={REPS} interleaved) -> {RUNS_PATH}", flush=True)

    t0 = time.monotonic()
    with RUNS_PATH.open("w") as f:
        for idx, (part, tag, rep, app_args, meta) in enumerate(sched, start=1):
            rec = run_one(idx, part, tag, rep, app_args, meta)
            f.write(json.dumps(rec) + "\n")
            f.flush()
            hp = rec["host_pmu"]
            cp = rec["card_pmu"]
            kv = rec["kv"]
            imc = hp["imc_0_6_rd_mib"] + hp["imc_0_6_wr_mib"]
            el = time.monotonic() - t0
            eta = (el / idx) * (total - idx)
            if kv:
                kp = hp["kv"]
                extra = f"kv_p50={kv['p50_ns']:4.1f} p99={kv['p99_ns']:5.1f}ns l3m={kp['l3_miss_pct']:5.2f}% kvGHz={kp['ghz']:.2f}"
            else:
                cd = cp["drain_mops"] if cp else 0.0
                extra = f"hDrain={rec['drain_wall_mops']:5.1f} cDrain={cd:5.1f}M/s iio={hp['iio_inb_wr_b_per_txn']:4.1f}B/t"
            print(
                f"[{idx:3d}/{total}] {part:18s} {tag:26s} r{rep} (try={rec['attempt']}) "
                f"drop={rec['drop_pct']:5.1f}% imc={imc:6.1f}MiB ddio={hp['ddio_hit_pct']:5.1f}% "
                f"{extra} [el={el:.0f}s eta={eta:.0f}s]",
                flush=True,
            )

    host_after = read_host_env()
    card_after = read_card_env()
    elapsed = time.monotonic() - t0
    code_rev = subprocess.check_output(
        ["jj", "-R", str(ROOT), "log", "-r", "@-", "--no-graph", "-T", 'change_id.short() ++ " " ++ commit_id.short() ++ " " ++ description.first_line()'],
        text=True,
    ).strip()

    env_obj = {
        "issue": "MRV-165",
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_commit": code_rev,
        "total_runs": total,
        "elapsed_s": round(elapsed, 2),
        "reps": REPS,
        "host_bin": HOST_BIN,
        "card_cmd": "/root/bench/mrv165_card -l 3 --no-pci --file-prefix=mrv165 --",
        "host_before": host_before,
        "host_after": host_after,
        "card_before": card_before,
        "card_after": card_after,
    }
    ENV_PATH.write_text(json.dumps(env_obj, indent=2) + "\n")
    print(f"\nDONE: {total}/{total} verified runs in {elapsed:.1f}s -> {RUNS_PATH}", flush=True)


if __name__ == "__main__":
    main()
