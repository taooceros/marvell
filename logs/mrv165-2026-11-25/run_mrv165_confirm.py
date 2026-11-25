#!/usr/bin/env python3
"""MRV-165 paired clean-host confirmation pass for the 6 transient-noise points in Control 1.

Runs n=3 interleaved confirmations (rep=3..5) for:
  - kv_solo_32m (clean-host anchor)
  - drop_256k_64KiB & active_256k_64KiB
  - drop_256k_256KiB & active_256k_256KiB
  - lifo_128m_4MiB
  - drop_256k_32MiB & active_256k_32MiB

Logs EVERY attempt transparently to confirm_attempts.jsonl and passing runs to confirm_runs.jsonl
without modifying the original 204-run archive (runs.jsonl).
"""

import json
import pathlib
import socket
import subprocess
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv165")
LOG_DIR = ROOT / "logs/mrv165-2026-11-25"
CONFIRM_RUNS = LOG_DIR / "confirm_runs.jsonl"
CONFIRM_ATTEMPTS = LOG_DIR / "confirm_attempts.jsonl"
CONFIRM_ENV = LOG_DIR / "confirm_env.json"

HOST_BIN = "/tmp/mrv165-target/release/mrv165_host"
EAL_ARGS = ["-l", "40", "-a", "0000:99:02.0", "--file-prefix=mrv165", "--log-level=lib.eal:warning"]
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

MIN_CORE_GHZ = 3.85
MIN_MESH_GHZ = 2.18
MAX_ATTEMPTS = 6
KB = 1024
MB = 1024 * 1024


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
    edac_ue, edac_ce = {}, {}
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


def build_schedule():
    arms = [
        ("kv_solo_32m", None, None, None, None, None),
        ("drop_256k_64KiB", "drop_256k", "drop", 256 * KB, 64 * KB, 8192),
        ("active_256k_64KiB", "active_256k", "active", 256 * KB, 64 * KB, 8192),
        ("drop_256k_256KiB", "drop_256k", "drop", 256 * KB, 256 * KB, 2048),
        ("active_256k_256KiB", "active_256k", "active", 256 * KB, 256 * KB, 2048),
        ("lifo_128m_4MiB", "lifo_128m", "lifo", 128 * MB, 4 * MB, 128),
        ("drop_256k_32MiB", "drop_256k", "drop", 256 * KB, 32 * MB, 16),
        ("active_256k_32MiB", "active_256k", "active", 256 * KB, 32 * MB, 16),
    ]
    sched = []
    for rep in (3, 4, 5):
        rot = arms[(rep - 3) % len(arms):] + arms[:(rep - 3) % len(arms)]
        for tag, arm_name, mode, ring_bytes, b_bytes, n_bursts in rot:
            if tag == "kv_solo_32m":
                sched.append((
                    "control1_confirm",
                    tag,
                    rep,
                    ["--mode", "kv-solo", "--kv-bytes", str(32 * MB), "--solo-ms", "250", "--warmup-ms", "1100"],
                    {"arm": "kv_solo", "burst_label": "0", "expected_drop_per_burst": 0},
                ))
            else:
                b_lbl = tag.rsplit("_", 1)[1]
                exp_drop = max(0, (b_bytes // 64) - (ring_bytes // 64)) if mode == "drop" else 0
                sched.append((
                    "control1_confirm",
                    tag,
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
    return sched


def main():
    verify_card_sync()
    host_before = read_host_env()
    card_before = read_card_env()
    sched = build_schedule()
    total = len(sched)
    t0 = time.monotonic()

    passed_count = 0
    total_attempts = 0
    with CONFIRM_RUNS.open("a") as frun, CONFIRM_ATTEMPTS.open("a") as fatt:
        for idx, (part, tag, rep, app_args, meta) in enumerate(sched, start=1):
            cmd = [HOST_BIN] + EAL_ARGS + ["--"] + app_args
            exp_drop_per_burst = meta["expected_drop_per_burst"]
            done = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                total_attempts += 1
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if p.returncode != 0:
                    att_rec = {
                        "run_id": 204 + idx, "ts_utc": ts, "part": part, "tag": tag,
                        "rep": rep, "attempt": attempt, "passed": False,
                        "fail_reasons": [f"rc={p.returncode}: {p.stderr.strip()[:200]}"],
                    }
                    fatt.write(json.dumps(att_rec) + "\n")
                    fatt.flush()
                    time.sleep(0.5)
                    continue
                lines = [l for l in p.stdout.splitlines() if l.startswith("MRV165 ")]
                if not lines:
                    att_rec = {
                        "run_id": 204 + idx, "ts_utc": ts, "part": part, "tag": tag,
                        "rep": rep, "attempt": attempt, "passed": False,
                        "fail_reasons": ["missing MRV165 JSON line"],
                    }
                    fatt.write(json.dumps(att_rec) + "\n")
                    fatt.flush()
                    time.sleep(0.5)
                    continue
                rec = json.loads(lines[-1].split("MRV165 ", 1)[1])
                reasons = check_gates(rec, exp_drop_per_burst)
                full = {
                    "run_id": 204 + idx,
                    "ts_utc": ts,
                    "part": part,
                    "tag": tag,
                    "rep": rep,
                    "attempt": attempt,
                    "cmd": " ".join(cmd),
                }
                full.update(meta)
                full.update(rec)
                att_full = dict(full)
                att_full["passed"] = (len(reasons) == 0)
                att_full["fail_reasons"] = reasons
                fatt.write(json.dumps(att_full) + "\n")
                fatt.flush()
                if reasons:
                    print(f"  [retry {attempt}] {tag} r{rep}: {', '.join(reasons)}", flush=True)
                    time.sleep(0.3)
                    continue
                frun.write(json.dumps(full) + "\n")
                frun.flush()
                passed_count += 1
                kv = full["kv"]
                hp = full["host_pmu"]
                imc = hp["imc_all_rd_mib"] + hp["imc_all_wr_mib"]
                print(
                    f"[{idx:02d}/{total}] {tag:22s} r{rep} att={attempt} | "
                    f"p50={kv['p50_ns']:.1f} p99={kv['p99_ns']:.1f} p999={kv['p999_ns']:.1f} "
                    f"l3m={hp['kv']['l3_miss_pct']:.2f}% imc={imc:.1f}MiB "
                    f"drop={full['drop_pct']:.2f}% kvGHz={hp['kv']['ghz']:.3f}",
                    flush=True,
                )
                done = True
                break
            if not done:
                raise RuntimeError(f"{tag} r{rep} failed after {MAX_ATTEMPTS} attempts")

    elapsed = time.monotonic() - t0
    host_after = read_host_env()
    card_after = read_card_env()
    env = {
        "issue": "MRV-165",
        "purpose": "paired_clean_host_confirmation_for_6_transient_noise_runs",
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "passed_runs": passed_count,
        "total_attempts": total_attempts,
        "elapsed_s": round(elapsed, 2),
        "host_bin": HOST_BIN,
        "card_cmd": "/root/bench/mrv165_card -l 3 --no-pci --file-prefix=mrv165 --",
        "host_before": host_before,
        "host_after": host_after,
        "card_before": card_before,
        "card_after": card_after,
    }
    CONFIRM_ENV.write_text(json.dumps(env, indent=2) + "\n")
    print(f"DONE: {passed_count}/{total} passed ({total_attempts} total attempts) in {elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
