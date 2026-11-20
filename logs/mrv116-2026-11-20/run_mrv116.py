#!/usr/bin/env python3
"""MRV-116 experiment harness: Host TX send buffer retention pool (64 KiB -> 128 MiB)
zero-degradation verification for non-coherent MOVDIR64B -> BAR4 + WC -> Host DDIO.

Fixed fastest direct-path baseline (MRV-82 Occam's Razor):
  - Host cpu40 (Socket 1, performance governor, 3.90 GHz), Socket 1 uncore 2.20 GHz
  - MOVDIR64B -> BAR4 slot 15 (64 B cell, batch = 1, no SFENCE)
  - Card CN106 (2.00 GHz), 1 card ring core (-l 3, WC1: pem_wc write-combining stores -> Host DDIO)
  - TX retention pool allocated in Socket-1 2 MiB hugepages, pre-touched non-zero (0 page faults)
  - Constant-memory (~108 KiB) FastHist latency histogram (zero LLC/DRAM growth during measurement)
  - 100%-enabled Host Core/CHA/IIO/IMC + Card TAD/ARMv8 PMU strictly bracketing measured rounds

Parts (3 interleaved repeats per config = 153 verified runs):
  - part1_w64 : Closed-loop WC1 peak throughput (W=64, rounds=4,194,304, warmup=2,097,152)
  - part2_w1  : Closed-loop WC1 unloaded latency (W=1, rounds=300,000, warmup=20,000)
  - part3_h2c : Pure unidirectional MOVDIR64B -> BAR4 saturation (card silent + 32,768-cell BAR4 verify,
                rounds=67,108,864, warmup=4,194,304)
"""
import json
import pathlib
import socket
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv116")
LOG_DIR = ROOT / "logs/mrv116-2026-11-20"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
ENV_JSON = LOG_DIR / "env.json"
HOST_BIN = ROOT / "testbed/target/release/inline_pingpong"

REPS = 3
MIN_CPU_GHZ = 3.87
MIN_MESH_GHZ = 2.18

POOL_SIZES = [
    ("64k", 64 * 1024),
    ("4m", 4 * 1024 * 1024),
    ("32m", 32 * 1024 * 1024),
    ("128m", 128 * 1024 * 1024),
]

MODES_ORDERS = [
    ("fill_seq", "fill-retain", "seq"),
    ("cold_seq", "cold-read", "seq"),
    ("fill_rand", "fill-retain", "rand"),
    ("cold_rand", "cold-read", "rand"),
]


def wait_for_turbo(min_khz: int = 3_850_000, max_wait_s: float = 30.0) -> int:
    t0 = time.monotonic()
    p = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq")
    while time.monotonic() - t0 < max_wait_s:
        khz = int(p.read_text().strip())
        if khz >= min_khz:
            return khz
        time.sleep(0.25)
    return int(p.read_text().strip())


def check_host_env() -> dict:
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    cur = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        uncore[p.parent.name] = int(p.read_text().strip())
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    hp_free = int(
        pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages").read_text().strip()
    )
    hp_total = int(
        pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages").read_text().strip()
    )
    assert gov == "performance", f"cpu40 governor is {gov}"
    assert uncore.get("package_01_die_01", 0) >= 2_190_000, f"uncore is {uncore}"
    assert paranoid <= 0, f"perf_event_paranoid is {paranoid}"
    assert hp_free >= 128, f"node1 free 2M hugepages too low: {hp_free}"
    return {
        "cpu40_governor": gov,
        "cpu40_khz": cur,
        "uncore_khz": uncore,
        "perf_event_paranoid": paranoid,
        "node1_hugepages_2m_free": hp_free,
        "node1_hugepages_2m_total": hp_total,
    }


def read_card_state() -> dict:
    cmd = (
        "cat /sys/devices/system/edac/mc/mc0/ce_count "
        "/sys/devices/system/edac/mc/mc0/ue_count "
        "/sys/devices/system/edac/ea/ea0/ce_count "
        "/sys/devices/system/edac/ea/ea0/ue_count; "
        "lsmod | grep -c '^pem_wc ' || true; "
        "cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000"
    )
    out = subprocess.check_output(["ssh", "root@10.10.0.2", cmd], text=True).split()
    return {
        "mc0_ce": int(out[0]),
        "mc0_ue": int(out[1]),
        "ea0_ce": int(out[2]),
        "ea0_ue": int(out[3]),
        "pem_wc_loaded": int(out[4]),
        "card_cpu3_khz": int(out[5]),
    }


def verify_card_sync_up():
    with socket.create_connection(("10.10.0.2", 7527), timeout=3.0) as s:
        s.sendall(b"SET_VERIFY seq\n")
        resp = s.recv(128).decode().strip()
        assert resp.startswith("OK"), f"card sync check failed: {resp}"


def build_configs() -> list[dict]:
    cfgs = [
        {"cfg": "stack_0", "tx_mode": "stack", "tx_order": "seq", "tx_pool_bytes": 0, "size_label": "stack"},
    ]
    for mode_tag, tx_mode, tx_order in MODES_ORDERS:
        for sz_label, sz_bytes in POOL_SIZES:
            cfgs.append(
                {
                    "cfg": f"{mode_tag}_{sz_label}",
                    "tx_mode": tx_mode,
                    "tx_order": tx_order,
                    "tx_pool_bytes": sz_bytes,
                    "size_label": sz_label,
                }
            )
    return cfgs


def run_one(run_id: int, part: str, spec: dict, rep: int, epoch: int) -> dict:
    for attempt in range(6):
        wait_for_turbo()
        cmd = [
            str(HOST_BIN),
            "-l",
            "40",
            "-a",
            "0000:99:02.0",
            "--file-prefix=mrv116",
            "--",
            "--reply",
            "ring",
            "--pmu",
            "--tx-mode",
            spec["tx_mode"],
            "--tx-order",
            spec["tx_order"],
            "--tx-pool-bytes",
            str(spec["tx_pool_bytes"]),
        ]
        if part == "part1_w64":
            cmd += ["-w", "64", "-n", "4194304", "--warmup", "2097152"]
        elif part == "part2_w1":
            cmd += ["-w", "1", "-n", "300000", "--warmup", "20000"]
        elif part == "part3_h2c":
            cmd += ["--h2c-only", "-n", "67108864", "--warmup", "4194304", "--epoch", str(epoch)]
        elif part == "part4_w16":
            cmd += ["-w", "16", "-n", "1048576", "--warmup", "262144"]
        else:
            raise ValueError(part)

        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(
                f"run_id={run_id} {part}/{spec['cfg']} failed rc={proc.returncode}:\n{proc.stderr}\n{proc.stdout}"
            )
        payload = None
        for line in proc.stdout.splitlines():
            if line.startswith("MRV116 "):
                payload = json.loads(line[7:])
                break
        if payload is None:
            raise RuntimeError(f"run_id={run_id} {part}/{spec['cfg']} missing MRV116 JSON")

        hp = payload["host_pmu"]
        cp = payload["card_pmu"]
        n = payload["measured"]
        wcilf_ratio = hp["cha_ins_wcilf"] / n

        ok = True
        reasons = []
        if payload["lost"] != 0 or payload["bad"] != 0 or payload["torn"] != 0:
            ok = False
            reasons.append(f"integrity l/b/t={payload['lost']}/{payload['bad']}/{payload['torn']}")
        if cp["cores"][0]["corrupt"] != 0:
            ok = False
            reasons.append(f"card corrupt={cp['cores'][0]['corrupt']}")
        if hp["min_pct"] < 99.99 or cp["min_pct"] < 99.99:
            ok = False
            reasons.append(f"pmu mux host={hp['min_pct']} card={cp['min_pct']}")
        if hp["page_faults"] != 0:
            ok = False
            reasons.append(f"page_faults={hp['page_faults']}")
        if hp["ghz"] < MIN_CPU_GHZ:
            ok = False
            reasons.append(f"cpu40 ghz={hp['ghz']:.4f} < {MIN_CPU_GHZ}")
        if hp["mesh_ghz"] < MIN_MESH_GHZ:
            ok = False
            reasons.append(f"mesh_ghz={hp['mesh_ghz']:.4f} < {MIN_MESH_GHZ}")
        if abs(wcilf_ratio - 1.0) > 0.005:
            ok = False
            reasons.append(f"wcilf/n={wcilf_ratio:.5f}")
        if spec["tx_pool_bytes"] > 0:
            if payload["pool_page_kb"] != 2048 or payload["pool_numa_n0"] != 0:
                ok = False
                reasons.append(
                    f"hugepage check page_kb={payload['pool_page_kb']} n0={payload['pool_numa_n0']}"
                )
        if part in ("part1_w64", "part2_w1", "part4_w16"):
            taken = cp["cores"][0]["taken"]
            itom = hp["cha_io_hit_itom"] + hp["cha_io_miss_itom"]
            if taken != n:
                ok = False
                reasons.append(f"card taken={taken} != {n}")
            if abs(itom / n - 1.0) > 0.005:
                ok = False
                reasons.append(f"itom/n={itom/n:.5f}")
        else:
            bv = payload.get("bar4_verify") or {}
            if bv.get("verified") != 32768 or bv.get("mismatches") != 0 or bv.get("torn") != 0:
                ok = False
                reasons.append(f"bar4_verify={bv}")

        if ok:
            return {
                "run_id": run_id,
                "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
                "part": part,
                "cfg": spec["cfg"],
                "size_label": spec["size_label"],
                "rep": rep,
                "attempt": attempt,
                **payload,
            }
        print(
            f"  [retry {attempt+1}/6] run_id={run_id} {part}/{spec['cfg']} rep={rep}: {', '.join(reasons)}",
            flush=True,
        )
        time.sleep(0.8)

    raise RuntimeError(f"run_id={run_id} {part}/{spec['cfg']} rep={rep} failed quality checks after 6 attempts")


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    host_before = check_host_env()
    card_before = read_card_state()
    verify_card_sync_up()
    assert card_before["mc0_ue"] == 0, f"Card mc0_ue before: {card_before}"
    assert card_before["pem_wc_loaded"] == 1, f"pem_wc not loaded: {card_before}"
    print(f"Host before: {host_before}", flush=True)
    print(f"Card before: {card_before}", flush=True)

    cfgs = build_configs()
    parts = ["part1_w64", "part2_w1", "part3_h2c", "part4_w16"]
    only_part = sys.argv[1] if len(sys.argv) > 1 else None
    append_mode = only_part is not None
    if only_part is not None:
        assert only_part in parts, f"unknown part {only_part}"
        parts = [only_part]
    records = []
    run_id = 0
    epoch = 100
    if append_mode and RUNS_JSONL.exists():
        existing = [json.loads(l) for l in RUNS_JSONL.read_text().splitlines() if l.strip()]
        run_id = max((r["run_id"] for r in existing), default=0)
        epoch = run_id + 200

    t_start = time.monotonic()
    total_planned = run_id + len(parts) * REPS * len(cfgs)
    with RUNS_JSONL.open("a" if append_mode else "w") as f:
        for part in parts:
            print(f"\n=== Starting {part} ({len(cfgs)} configs x {REPS} interleaved reps) ===", flush=True)
            for rep in range(REPS):
                for spec in cfgs:
                    run_id += 1
                    epoch += 1
                    rec = run_one(run_id, part, spec, rep, epoch)
                    records.append(rec)
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    hp = rec["host_pmu"]
                    n = rec["measured"]
                    lat = rec.get("lat_us") or {}
                    lat_s = f"p50={lat['p50']:.3f} p99={lat['p99']:.3f}us" if lat else "h2c_only"
                    print(
                        f"[{run_id:3d}/{total_planned}] {part} rep={rep} {spec['cfg']:16s} | "
                        f"{rec['mops']:7.3f} M/s ({rec['gbps']:6.3f} GB/s) | {lat_s:22s} | "
                        f"ghz={hp['ghz']:.3f} ddio={hp['ddio_hit_pct']:6.2f}% "
                        f"llc_m/m={hp['llc_load_misses']/n:.3f} "
                        f"imc_rd/wr={hp['imc_cas_rd']/n:.2f}/{hp['imc_cas_wr']/n:.2f}",
                        flush=True,
                    )
    host_after = check_host_env()
    card_after = read_card_state()
    elapsed_s = time.monotonic() - t_start
    assert card_after["mc0_ue"] == card_before["mc0_ue"] and card_after["ea0_ue"] == card_before["ea0_ue"], (
        f"Card EDAC UE changed: before={card_before} after={card_after}"
    )
    all_count = sum(1 for _ in RUNS_JSONL.open())
    prev_env = json.loads(ENV_JSON.read_text()) if append_mode and ENV_JSON.exists() else {}
    env_doc = {
        "issue": "MRV-116",
        "elapsed_s": round(prev_env.get("elapsed_s", 0.0) + elapsed_s, 2),
        "total_runs": all_count,
        "reps": REPS,
        "host_before": prev_env.get("host_before", host_before),
        "host_after": host_after,
        "card_before": prev_env.get("card_before", card_before),
        "card_after": card_after,
    }
    ENV_JSON.write_text(json.dumps(env_doc, indent=2) + "\n")
    print(f"\nDONE: +{len(records)} runs (total {all_count}) in {elapsed_s:.1f}s -> {RUNS_JSONL}", flush=True)
    print(f"Card after: {card_after}", flush=True)


if __name__ == "__main__":
    main()
