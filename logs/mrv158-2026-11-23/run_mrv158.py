#!/usr/bin/env python3
"""MRV-158 full n=3 interleaved verified experiment harness.

Single-variable comparison (MRV-82):
  - Part 1: Zero-backlog credit-check overhead (credit_check OFF vs ON)
            on W=1 RTT, W=64 RTT/throughput, and C2H stream push.
  - Part 2: Controlled burst backlog (64 KiB -> 128 MiB) changing ONLY
            host ring size: Scheme A (128 MiB host ring) vs
            Scheme B (active NIC backlog + 256 KiB host hot ring).
  - Part 3A: Scheme B credit-return -> new-cell-landing single-hop latency
             across queued card backlog sizes (512 KiB -> 128 MiB, crossing 48 MiB card L3).

Hardware invariants (no sudo):
  - Host: cpu40 @ 3.90 GHz (performance), Socket 1 uncore @ 2.20 GHz, NUMA node 1 2 MiB hugepages
  - Card: cpu3 @ 2.00 GHz, 48 MiB L3 (128 B line), 48 GB DDR5, PEM0 outbound WC (`pem_wc`)
  - Zero SDP, zero Intel DSA; BAR4 slot 15 accessed exclusively via MRV-26 lease.
  - Strict gates on every run: lost=bad=torn=0, card corrupt=0, host & card PMU min_pct >= 99.99%,
    host page_faults=0, 2 MiB node-1 hugepages, cpu40 >= 3.85 GHz, mesh >= 2.18 GHz.
"""

import json
import pathlib
import socket
import subprocess
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv158")
LOG_DIR = ROOT / "logs/mrv158-2026-11-23"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv158-target/release/mrv158_host"
EAL_ARGS = ["-l", "40", "-a", "0000:99:02.0", "--file-prefix=mrv158", "--log-level=lib.eal:warning"]
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

REPS = 3
MIN_HOST_GHZ = 3.85
MIN_MESH_GHZ = 2.18

KB = 1024
MB = 1024 * 1024

BACKLOG_SIZES = [
    ("64KiB", 64 * KB),
    ("256KiB", 256 * KB),
    ("1MiB", 1 * MB),
    ("4MiB", 4 * MB),
    ("8MiB", 8 * MB),
    ("16MiB", 16 * MB),
    ("32MiB", 32 * MB),
    ("48MiB", 48 * MB),
    ("64MiB", 64 * MB),
    ("96MiB", 96 * MB),
    ("128MiB", 128 * MB),
]

REFILL_SIZES = [
    ("512KiB", 512 * KB),
    ("1MiB", 1 * MB),
    ("4MiB", 4 * MB),
    ("16MiB", 16 * MB),
    ("32MiB", 32 * MB),
    ("48MiB", 48 * MB),
    ("64MiB", 64 * MB),
    ("96MiB", 96 * MB),
    ("128MiB", 128 * MB),
]


def read_host_env() -> dict:
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
    edac_ue = {}
    for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count")):
        edac_ue[p.parent.name] = int(p.read_text().strip())
    assert gov == "performance", f"cpu40 governor={gov}"
    assert uncore.get("package_01_die_01", 0) >= 2_190_000, f"uncore={uncore}"
    assert paranoid <= 0, f"perf_event_paranoid={paranoid}"
    assert hp_free >= 256, f"node1 2M hugepages free too low: {hp_free}"
    return {
        "cpu40_governor": gov,
        "cpu40_khz": cur,
        "uncore_khz": uncore,
        "perf_event_paranoid": paranoid,
        "node1_hugepages_2m_free": hp_free,
        "node1_hugepages_2m_total": hp_total,
        "host_edac_ue": edac_ue,
    }


def read_card_env() -> dict:
    cmd = (
        "cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000; "
        "lsmod | grep -c '^pem_wc ' || true; "
        "for p in /sys/devices/system/edac/mc/mc*/ue_count; do echo $(basename $(dirname $p))=$(cat $p); done; "
        "cat /sys/devices/system/cpu/cpu3/cache/index3/size /sys/devices/system/cpu/cpu3/cache/index3/coherency_line_size"
    )
    out = subprocess.check_output(CARD_SSH + [cmd], text=True).strip().splitlines()
    cpu3_khz = int(out[0])
    pem_wc = int(out[1])
    edac = {}
    idx = 2
    while idx < len(out) and "=" in out[idx]:
        k, v = out[idx].split("=", 1)
        edac[k] = int(v)
        idx += 1
    l3_size = out[idx] if idx < len(out) else ""
    l3_line = out[idx + 1] if idx + 1 < len(out) else ""
    return {
        "card_cpu3_khz": cpu3_khz,
        "pem_wc_loaded": pem_wc,
        "card_edac_ue": edac,
        "card_l3_size": l3_size,
        "card_l3_line_bytes": l3_line,
    }


def verify_card_sync():
    with socket.create_connection(("10.10.0.2", 7527), timeout=3.0) as s:
        s.sendall(b"PING\n")
        resp = s.recv(64).decode().strip()
        assert resp == "OK", f"card sync PING failed: {resp!r}"


def wait_for_turbo(min_khz: int = 3_850_000, max_wait_s: float = 15.0):
    p = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq")
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_wait_s:
        if int(p.read_text().strip()) >= min_khz:
            return
        time.sleep(0.05)


def run_one(run_id: int, part: str, tag: str, rep: int, app_args: list[str], meta: dict) -> dict:
    cmd = [HOST_BIN] + EAL_ARGS + ["--"] + app_args
    for attempt in range(6):
        wait_for_turbo()
        t0 = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if p.returncode != 0:
            raise RuntimeError(f"run_id={run_id} {part}/{tag} rep={rep} rc={p.returncode}:\n{p.stdout}\n{p.stderr}")
        payload = None
        for line in p.stdout.splitlines():
            if line.startswith("MRV158 "):
                payload = json.loads(line[7:])
                break
        if payload is None:
            raise RuntimeError(f"run_id={run_id} {part}/{tag} missing MRV158 JSON:\n{p.stdout}")

        hp = payload["host_pmu"]
        cp = payload["card_pmu"]
        reasons = []
        if payload["lost"] != 0 or payload["bad"] != 0 or payload["torn"] != 0:
            reasons.append(f"integrity l/b/t={payload['lost']}/{payload['bad']}/{payload['torn']}")
        if cp["corrupt"] != 0:
            reasons.append(f"card corrupt={cp['corrupt']}")
        if hp["min_pct"] < 99.99 or cp["min_pct"] < 99.99:
            reasons.append(f"pmu mux host={hp['min_pct']} card={cp['min_pct']}")
        if hp["page_faults"] != 0:
            reasons.append(f"page_faults={hp['page_faults']}")
        if hp["ghz"] < MIN_HOST_GHZ:
            reasons.append(f"cpu40 ghz={hp['ghz']:.4f} < {MIN_HOST_GHZ}")
        if hp["mesh_ghz"] < MIN_MESH_GHZ:
            reasons.append(f"mesh_ghz={hp['mesh_ghz']:.4f} < {MIN_MESH_GHZ}")
        if payload["ring_page_kb"] != 2048 or payload["ring_n0"] != 0:
            reasons.append(f"ring hugepage/numa kb={payload['ring_page_kb']} n0={payload['ring_n0']}")

        if not reasons:
            return {
                "run_id": run_id,
                "ts_utc": datetime.fromtimestamp(t0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "part": part,
                "tag": tag,
                "rep": rep,
                "attempt": attempt,
                "cmd": " ".join(cmd),
                **meta,
                **payload,
            }
        print(f"  [retry {attempt+1}/6] run_id={run_id} {part}/{tag} rep={rep}: {', '.join(reasons)}", flush=True)
        time.sleep(0.5)
    raise RuntimeError(f"run_id={run_id} {part}/{tag} rep={rep} failed quality gates after 6 attempts")


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    verify_card_sync()
    host_before = read_host_env()
    card_before = read_card_env()
    print(f"Host before: {host_before}", flush=True)
    print(f"Card before: {card_before}", flush=True)

    schedule = []

    # Part 1: Zero-backlog credit-check overhead (n=3 interleaved)
    p1_specs = [
        ("w1_credit_off", ["--mode", "zero-rtt", "--host-ring-bytes", str(256 * KB), "--credit-check", "0", "-w", "1", "--warmup", "50000", "-n", "500000"], {"subpart": "w1"}),
        ("w1_credit_on",  ["--mode", "zero-rtt", "--host-ring-bytes", str(256 * KB), "--credit-check", "1", "-w", "1", "--warmup", "50000", "-n", "500000"], {"subpart": "w1"}),
        ("w32_credit_off", ["--mode", "zero-rtt", "--host-ring-bytes", str(256 * KB), "--credit-check", "0", "-w", "32", "--warmup", "200000", "-n", "4000000"], {"subpart": "w32"}),
        ("w32_credit_on",  ["--mode", "zero-rtt", "--host-ring-bytes", str(256 * KB), "--credit-check", "1", "-w", "32", "--warmup", "200000", "-n", "4000000"], {"subpart": "w32"}),
        ("stream_credit_on", ["--mode", "zero-stream", "--host-ring-bytes", str(256 * KB), "--credit-check", "1", "-n", "20000000"], {"subpart": "stream"}),
    ]
    for rep in range(REPS):
        order = p1_specs if rep % 2 == 0 else list(reversed(p1_specs))
        for tag, app_args, meta in order:
            schedule.append(("part1_zero_backlog", tag, rep, app_args, meta))

    # Part 2: Burst backlog 64 KiB -> 128 MiB, Scheme A (128 MiB host ring) vs Scheme B (256 KiB hot ring)
    for rep in range(REPS):
        sizes = BACKLOG_SIZES if rep % 2 == 0 else list(reversed(BACKLOG_SIZES))
        schemes = [("scheme_a_128m", 128 * MB), ("scheme_b_256k", 256 * KB)]
        if rep % 2 == 1:
            schemes = list(reversed(schemes))
        for sz_label, b_bytes in sizes:
            bursts = max(4, (256 * MB) // b_bytes)
            for sc_label, ring_bytes in schemes:
                tag = f"{sc_label}_{sz_label}"
                app_args = [
                    "--mode", "backlog",
                    "--host-ring-bytes", str(ring_bytes),
                    "--burst-bytes", str(b_bytes),
                    "--bursts", str(bursts),
                    "--warmup-bytes", str(128 * MB),
                    "--credit-batch", "512",
                    "--pace-ns", "10",
                ]
                meta = {"scheme": sc_label, "size_label": sz_label}
                schedule.append(("part2_backlog", tag, rep, app_args, meta))

    # Part 3A: Scheme B single-hop credit-return -> new-cell-landing latency (512 KiB -> 128 MiB)
    for rep in range(REPS):
        sizes = REFILL_SIZES if rep % 2 == 0 else list(reversed(REFILL_SIZES))
        for sz_label, b_bytes in sizes:
            tag = f"refill_{sz_label}"
            app_args = [
                "--mode", "refill-hop",
                "--host-ring-bytes", str(256 * KB),
                "--burst-bytes", str(b_bytes),
                "--bursts", "4",
                "--probes-per-burst", "2048",
                "--credit-batch", "512",
                "--pace-ns", "10",
            ]
            meta = {"scheme": "scheme_b_256k", "size_label": sz_label}
            schedule.append(("part3_refill_hop", tag, rep, app_args, meta))

    print(f"Total scheduled verified runs: {len(schedule)}", flush=True)
    t_all = time.monotonic()

    with RUNS_PATH.open("w") as f:
        for run_id, (part, tag, rep, app_args, meta) in enumerate(schedule, 1):
            rec = run_one(run_id, part, tag, rep, app_args, meta)
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if part == "part1_zero_backlog":
                if rec["mode"] == "zero-rtt":
                    print(
                        f"[{run_id:3d}/{len(schedule)}] {part}/{tag} r{rep}: "
                        f"p50={rec['lat_us']['p50']:.3f} p99={rec['lat_us']['p99']:.3f} us | "
                        f"mops={rec['mops']:.3f} | h={rec['host_pmu']['ghz']:.2f}G",
                        flush=True,
                    )
                else:
                    print(
                        f"[{run_id:3d}/{len(schedule)}] {part}/{tag} r{rep}: "
                        f"mops={rec['mops']:.2f} gbps={rec['gbps']:.2f} card_push={rec['card_pmu']['push_mops']:.2f}",
                        flush=True,
                    )
            elif part == "part2_backlog":
                n = rec["measured_cells"]
                hp, cp = rec["host_pmu"], rec["card_pmu"]
                print(
                    f"[{run_id:3d}/{len(schedule)}] {part}/{tag:21s} r{rep}: "
                    f"arr={rec['arrival_mops']:5.1f}M/s drain={rec['drain_wall_ns_per_cell']:5.2f}ns "
                    f"h_L3m={hp['ocr_l3_miss']/n:.3f} h_IMCr={hp['imc_cas_rd']/n:.3f} h_IMCw={hp['imc_cas_wr']/n:.3f} "
                    f"ddio={hp['ddio_hit_pct']:5.1f}% | c_drn={cp['drain_mops']:6.2f} c_DDRr={cp['ddr_reads']/n:.2f}",
                    flush=True,
                )
            else:
                h = rec["hop_us"]
                print(
                    f"[{run_id:3d}/{len(schedule)}] {part}/{tag:16s} r{rep}: "
                    f"hop p50={h['p50']:.3f} p99={h['p99']:.3f} mean={h['mean']:.3f} first={rec['first_hop_mean_us']:.3f} us",
                    flush=True,
                )

    host_after = read_host_env()
    card_after = read_card_env()
    elapsed = time.monotonic() - t_all
    env = {
        "issue": "MRV-158",
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_runs": len(schedule),
        "elapsed_s": round(elapsed, 2),
        "reps": REPS,
        "host_bin": HOST_BIN,
        "card_cmd": "/root/bench/mrv158_card -l 3 --no-pci --file-prefix=mrv158 --",
        "host_before": host_before,
        "host_after": host_after,
        "card_before": card_before,
        "card_after": card_after,
    }
    ENV_PATH.write_text(json.dumps(env, indent=2) + "\n")
    print(f"DONE: {len(schedule)}/{len(schedule)} verified runs in {elapsed:.1f}s", flush=True)
    print(f"Host after: {host_after}", flush=True)
    print(f"Card after: {card_after}", flush=True)


if __name__ == "__main__":
    main()
