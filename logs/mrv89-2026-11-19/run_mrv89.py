#!/usr/bin/env python3
"""MRV-89 experiment harness: host MOVDIR64B parallelism into CN106 BAR4 slot 15.

Single-variable (MRV-82) design, card core silent during bursts + post-burst
full-slot verification + 100%-enabled Host (core/CHA/IIO/IRP) and Card (TAD) PMUs:
  - Exp 1: Single core cpu40, seq64 (2 MiB = 32768 lines), sweep SFENCE interval K
           in {1, 2, 4, 8, 16, 32, 64, 128, 256, 0 (no fence)}.
  - Exp 2: Single core cpu40, no SFENCE, sweep address distribution:
           seq64 (32768 lines) vs stride4k (512 lines, 4 KiB stride) vs same64 (1 line).
  - Exp 3: Multi-core concurrent MOVDIR64B, no SFENCE, disjoint 512 KiB (8192 lines)
           seq64 region per core, 4 s warmup + per-core measured GHz:
           1c (40), 2c_smt (40,104), 2c_phys (40,41), 4c_phys (40,41,42,43).
"""
import json
import pathlib
import socket
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv89")
LOG_DIR = ROOT / "logs/mrv89-2026-11-19"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
HOST_BIN = ROOT / "testbed/target/release/movdir_parallel_host"

REPS = 3


def check_host_env():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    if gov != "performance":
        raise RuntimeError(f"cpu40 governor is {gov!r}, expected 'performance'")
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        khz = int(p.read_text().strip())
        uncore[p.parent.name] = khz
        if khz != 2_200_000:
            raise RuntimeError(f"uncore {p.parent.name} = {khz} kHz, expected 2200000")
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    if paranoid > 0:
        raise RuntimeError(f"perf_event_paranoid = {paranoid}, expected <= 0")
    return {"cpu40_governor": gov, "uncore_khz": uncore, "perf_event_paranoid": paranoid}


def read_card_edac():
    cmd = "cat /sys/devices/system/edac/mc/mc0/ce_count /sys/devices/system/edac/mc/mc0/ue_count /sys/devices/system/edac/ea/ea0/ce_count /sys/devices/system/edac/ea/ea0/ue_count"
    out = subprocess.check_output(["ssh", "root@10.10.0.2", cmd], text=True).strip().splitlines()
    vals = [int(x) for x in out]
    return {"mc0_ce": vals[0], "mc0_ue": vals[1], "ea0_ce": vals[2], "ea0_ue": vals[3]}


def measure_card_idle_tad(rep: int, secs: float = 1.0):
    with socket.create_connection(("10.10.0.2", 7527), timeout=5.0) as s:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        f = s.makefile("rwb", buffering=0)
        f.write(b"START\n")
        resp = f.readline().decode().strip()
        assert resp == "OK", f"idle START failed: {resp}"
        time.sleep(secs)
        f.write(b"STOP same64 0 1 0 1\n")
        line = f.readline().decode().strip()
    card = json.loads(line)
    assert card["min_pct"] >= 99.9, f"idle card PMU mux: {card}"
    return {
        "exp": "idle_card",
        "cond": "idle_1s",
        "rep": rep,
        "target_secs": secs,
        "card": card,
    }


def build_schedule():
    # Each entry: dict of run parameters. All ops_per_thread are multiples of 32768 and 256.
    exp1 = [
        {"exp": "exp1_fence", "cond": "k1",   "cpus": "40", "pattern": "seq64", "fence_k": 1,   "region_cells": 32768, "ops_per_thread":   8_388_608, "warmup_ms": 1000},
        {"exp": "exp1_fence", "cond": "k2",   "cpus": "40", "pattern": "seq64", "fence_k": 2,   "region_cells": 32768, "ops_per_thread":  16_777_216, "warmup_ms": 1000},
        {"exp": "exp1_fence", "cond": "k4",   "cpus": "40", "pattern": "seq64", "fence_k": 4,   "region_cells": 32768, "ops_per_thread":  33_554_432, "warmup_ms": 1000},
        {"exp": "exp1_fence", "cond": "k8",   "cpus": "40", "pattern": "seq64", "fence_k": 8,   "region_cells": 32768, "ops_per_thread":  67_108_864, "warmup_ms": 1000},
        {"exp": "exp1_fence", "cond": "k16",  "cpus": "40", "pattern": "seq64", "fence_k": 16,  "region_cells": 32768, "ops_per_thread": 100_663_296, "warmup_ms": 1000},
        {"exp": "exp1_fence", "cond": "k32",  "cpus": "40", "pattern": "seq64", "fence_k": 32,  "region_cells": 32768, "ops_per_thread": 167_772_160, "warmup_ms": 1000},
        {"exp": "exp1_fence", "cond": "k64",  "cpus": "40", "pattern": "seq64", "fence_k": 64,  "region_cells": 32768, "ops_per_thread": 201_326_592, "warmup_ms": 1000},
        {"exp": "exp1_fence", "cond": "k128", "cpus": "40", "pattern": "seq64", "fence_k": 128, "region_cells": 32768, "ops_per_thread": 201_326_592, "warmup_ms": 1000},
        {"exp": "exp1_fence", "cond": "k256", "cpus": "40", "pattern": "seq64", "fence_k": 256, "region_cells": 32768, "ops_per_thread": 201_326_592, "warmup_ms": 1000},
        {"exp": "exp1_fence", "cond": "kinf", "cpus": "40", "pattern": "seq64", "fence_k": 0,   "region_cells": 32768, "ops_per_thread": 201_326_592, "warmup_ms": 1000},
    ]
    exp2 = [
        {"exp": "exp2_pattern", "cond": "seq64",    "cpus": "40", "pattern": "seq64",    "fence_k": 0, "region_cells": 32768, "ops_per_thread": 201_326_592, "warmup_ms": 1000},
        {"exp": "exp2_pattern", "cond": "stride4k", "cpus": "40", "pattern": "stride4k", "fence_k": 0, "region_cells": 32768, "ops_per_thread": 201_326_592, "warmup_ms": 1000},
        {"exp": "exp2_pattern", "cond": "same64",   "cpus": "40", "pattern": "same64",   "fence_k": 0, "region_cells": 1,     "ops_per_thread":  10_485_760, "warmup_ms": 1000},
    ]
    exp3 = [
        {"exp": "exp3_cores", "cond": "1c",      "cpus": "40",          "pattern": "seq64", "fence_k": 0, "region_cells": 8192, "ops_per_thread": 201_326_592, "warmup_ms": 4000},
        {"exp": "exp3_cores", "cond": "2c_smt",  "cpus": "40,104",      "pattern": "seq64", "fence_k": 0, "region_cells": 8192, "ops_per_thread": 100_663_296, "warmup_ms": 4000},
        {"exp": "exp3_cores", "cond": "2c_phys", "cpus": "40,41",       "pattern": "seq64", "fence_k": 0, "region_cells": 8192, "ops_per_thread": 100_663_296, "warmup_ms": 4000},
        {"exp": "exp3_cores", "cond": "4c_phys", "cpus": "40,41,42,43", "pattern": "seq64", "fence_k": 0, "region_cells": 8192, "ops_per_thread":  50_331_648, "warmup_ms": 4000},
    ]
    return exp1 + exp2 + exp3


def expected_verified_cells(spec):
    n_threads = len(spec["cpus"].split(","))
    if spec["pattern"] == "seq64":
        return n_threads * spec["region_cells"]
    elif spec["pattern"] == "stride4k":
        return n_threads * (spec["region_cells"] // 64)
    elif spec["pattern"] == "same64":
        return n_threads
    raise ValueError(spec["pattern"])


def run_one(spec, rep: int, epoch: int, env_info: dict):
    cmd = [
        str(HOST_BIN),
        "--cpus", spec["cpus"],
        "--pattern", spec["pattern"],
        "--fence-k", str(spec["fence_k"]),
        "--region-cells", str(spec["region_cells"]),
        "--ops-per-thread", str(spec["ops_per_thread"]),
        "--warmup-ms", str(spec["warmup_ms"]),
        "--epoch", str(epoch),
        "--exp", spec["exp"],
        "--cond", spec["cond"],
        "--rep", str(rep),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    rec = json.loads(lines[-1])
    rec["env"] = env_info
    rec["timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Strict integrity & PMU verification.
    exp_v = expected_verified_cells(spec)
    c = rec["card"]
    if c["mismatches"] != 0 or c["torn_cells"] != 0 or c["verified_cells"] != exp_v:
        raise RuntimeError(f"Card verification failed for {spec['exp']}/{spec['cond']} rep {rep}: {c} (expected verified={exp_v})")
    if rec["host_min_pct"] < 99.9 or c["min_pct"] < 99.9:
        raise RuntimeError(f"PMU multiplexing detected: host={rec['host_min_pct']} card={c['min_pct']}")
    tot = rec["total_ops"]
    cha_ins = rec["cha"]["inserts_wcilf"]
    iio_wr = rec["iio1"]["cpu_mem_wr"]
    if abs(cha_ins - tot) / tot > 0.005:
        raise RuntimeError(f"CHA inserts mismatch: {cha_ins} vs {tot}")
    if abs(iio_wr - tot) / tot > 0.005:
        raise RuntimeError(f"IIO cpu_mem_wr mismatch: {iio_wr} vs {tot}")
    return rec


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env_info = check_host_env()
    edac_before = read_card_edac()
    print(f"Host env OK: {env_info}")
    print(f"Card EDAC before: {edac_before}")

    schedule = build_schedule()
    records = []
    epoch = 100

    with RUNS_JSONL.open("w") as out_f:
        for rep in range(REPS):
            idle_rec = measure_card_idle_tad(rep, secs=1.0)
            idle_rec["timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            out_f.write(json.dumps(idle_rec) + "\n")
            out_f.flush()
            idle_req_s = idle_rec["card"]["tad"]["tad_req_msh_in_any"]["val"] / (
                idle_rec["card"]["tad"]["tad_req_msh_in_any"]["enabled_ns"] / 1e9
            )
            print(f"[rep {rep}] idle_card: tad_req={idle_req_s/1e6:.2f} M/s")

            # Alternate direction across reps to cancel any thermal/ordering drift.
            order = schedule if rep % 2 == 0 else list(reversed(schedule))
            for spec in order:
                epoch += 1
                rec = run_one(spec, rep, epoch, env_info)
                records.append(rec)
                out_f.write(json.dumps(rec) + "\n")
                out_f.flush()
                ghz_str = ",".join(f"c{x['cpu']}:{x['ghz']:.2f}" for x in rec["cores"])
                tad = rec["card"]["tad"]
                tad_ratio = tad["tad_req_msh_in_any"]["val"] / rec["total_ops"]
                tad_occ_per_req = tad["tad_req_occ"]["val"] / max(1, tad["tad_req_msh_in_any"]["val"])
                print(
                    f"[rep {rep}] {spec['exp']:12s} {spec['cond']:8s} | "
                    f"{rec['mops']:7.2f} M/s ({rec['gbps']:5.2f} GB/s, {rec['ns_per_op']:6.2f} ns) | "
                    f"CHA={rec['cha']['inflight']:6.1f} ({rec['cha']['active_chas']:2d}ch, {rec['cha']['residency_ns']:6.1f}ns) | "
                    f"IRP={rec['irp1']['inflight']:4.2f} | TAD req/op={tad_ratio:.2f} occ/req={tad_occ_per_req:5.2f} | "
                    f"{ghz_str} | v={rec['card']['verified_cells']} err=0"
                )

    edac_after = read_card_edac()
    print(f"Card EDAC after:  {edac_after}")
    if edac_after != edac_before:
        raise RuntimeError(f"EDAC delta non-zero! before={edac_before} after={edac_after}")
    print(f"\nALL {len(records)} RUNS PASSED (0 mismatches, 0 torn, 100% PMU, EDAC delta 0/0).")
    print(f"Saved to {RUNS_JSONL}")


if __name__ == "__main__":
    main()
