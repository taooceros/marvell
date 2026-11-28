#!/usr/bin/env python3
"""MRV-253 n=3 interleaved pure-host Socket 1 DSA Portal (wq2.0) disjoint cache-line partitioning harness.

Pure host Socket 1, ZERO CN106 touch.
Tests shared-64 vs disjoint K in {1, 2, 4, 8} across 8c/16c/24c/31c/32c physical cores and
32t_16x2 / 32t_24p8s / 48t_24x2 / 62t_31x2 SMT topologies, with strictly disjoint co-located
neighbors: 6c STREAM Triad (WB + physical DDR5 IMC BW), 6c STREAM Triad-NT (zero-ItoM),
6c STREAM RO (pure read-only), 16 MiB L3 chase, 256 MiB DRAM chase, and standalone (none).
Includes same-core l1-spin controls to subtract 2.80 GHz turbo droop cleanly.
Logs accepted verified runs to runs.jsonl and pre/post (21 s settle) host EDAC/AER audits to env.json.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
DEFAULT_BIN = Path("/tmp/mrv253-target/release/mrv253_host")


def csv_range(*ranges) -> str:
    cpus = []
    for r in ranges:
        if isinstance(r, int):
            cpus.append(r)
        else:
            cpus.extend(range(r[0], r[1] + 1))
    return ",".join(str(c) for c in cpus)


# Strictly disjoint from Group A victims (cpu40..45, SMT siblings cpu104..109)
AGGR_CPUS = {
    "0c": "",
    "8c": csv_range((52, 59)),
    "16c": csv_range((48, 63)),
    "24c": csv_range((46, 63), (32, 37)),
    "32t_16x2": csv_range((48, 63), (112, 127)),
    "32t_24p8s": csv_range((46, 63), (32, 37), (116, 123)),
    "48t_24x2": csv_range((46, 63), (32, 37), (110, 127), (96, 101)),
    # Group B (victim=llc on cpu40 only, cpu104 unused): all 31 other physical cores
    "31c": csv_range((32, 39), (41, 63)),
    "62t_31x2": csv_range((32, 39), (41, 63), (96, 103), (105, 127)),
    # Group B standalone (victim=none): all 32 physical cores on Socket 1
    "32c": csv_range((32, 63)),
}

AGGR_PHYS_COUNT = {
    "0c": 0, "8c": 8, "16c": 16, "24c": 24,
    "32t_16x2": 16, "32t_24p8s": 24, "48t_24x2": 24,
    "31c": 31, "62t_31x2": 31, "32c": 32,
}

VICTIM_SPEC = {
    "t6c": ("stream-triad", "40,41,42,43,44,45", 6),
    "t6c_nt": ("stream-triad-nt", "40,41,42,43,44,45", 6),
    "t6c_ro": ("stream-ro", "40,41,42,43,44,45", 6),
    "llc": ("llc-chase", "40", 1),
    "dram": ("dram-chase", "40", 1),
    "none": ("none", "", 0),
}


def read_txt(p: str) -> str:
    try:
        return Path(p).read_text().strip()
    except OSError:
        return "na"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def parse_aer(dev: str) -> dict:
    out = {}
    for kind in ("aer_dev_correctable", "aer_dev_nonfatal", "aer_dev_fatal"):
        p = Path(f"/sys/bus/pci/devices/{dev}/{kind}")
        if not p.exists():
            continue
        vals = {}
        for ln in p.read_text().splitlines():
            parts = ln.split()
            if len(parts) == 2 and parts[1].isdigit():
                v = int(parts[1])
                if v != 0 or parts[0].startswith("TOTAL_"):
                    vals[parts[0]] = v
        out[kind] = vals
    return out


def host_snapshot(bin_path: Path) -> dict:
    uncore = {}
    for pkg in ("package_00_die_00", "package_01_die_01"):
        base = f"/sys/devices/system/cpu/intel_uncore_frequency/{pkg}"
        uncore[pkg] = {
            "cur": read_txt(f"{base}/current_freq_khz"),
            "min": read_txt(f"{base}/min_freq_khz"),
            "max": read_txt(f"{base}/max_freq_khz"),
        }
    edac_ce, edac_ue = {}, {}
    for i in range(8):
        ce = read_txt(f"/sys/devices/system/edac/mc/mc{i}/ce_count")
        ue = read_txt(f"/sys/devices/system/edac/mc/mc{i}/ue_count")
        if ce != "na":
            edac_ce[f"mc{i}"] = int(ce)
            edac_ue[f"mc{i}"] = int(ue)
    eng_groups = {
        f"engine2.{i}": read_txt(f"/sys/bus/dsa/devices/engine2.{i}/group_id")
        for i in range(4)
    }
    s1_govs = {}
    for c in range(32, 64):
        g = read_txt(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_governor")
        s1_govs[g] = s1_govs.get(g, 0) + 1
    return {
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cn106_touched": False,
        "cpu40_gov": read_txt("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor"),
        "cpu40_cur_khz": read_txt("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq"),
        "cpu40_max_khz": read_txt("/sys/devices/system/cpu/cpu40/cpufreq/scaling_max_freq"),
        "socket1_phys_governor_counts": s1_govs,
        "uncore_khz": uncore,
        "perf_event_paranoid": read_txt("/proc/sys/kernel/perf_event_paranoid"),
        "node1_free_hugepages_2m": read_txt(
            "/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages"
        ),
        "host_edac_ce": edac_ce,
        "host_edac_ue": edac_ue,
        "dsa2_aer": parse_aer("0000:f2:01.0"),
        "dsa2": {
            "state": read_txt("/sys/bus/dsa/devices/dsa2/state"),
            "errors": read_txt("/sys/bus/dsa/devices/dsa2/errors"),
            "wq2_0_mode": read_txt("/sys/bus/dsa/devices/wq2.0/mode"),
            "wq2_0_size": read_txt("/sys/bus/dsa/devices/wq2.0/size"),
            "wq2_0_group": read_txt("/sys/bus/dsa/devices/wq2.0/group_id"),
            "wq2_0_bof": read_txt("/sys/bus/dsa/devices/wq2.0/block_on_fault"),
            "engine_groups": eng_groups,
        },
        "host_bin_sha256": sha256_file(bin_path) if bin_path.exists() else "missing",
    }


def set_socket1_uncore(min_khz: str, max_khz: str) -> bool:
    base = Path("/sys/devices/system/cpu/intel_uncore_frequency/package_01_die_01")
    try:
        (base / "min_freq_khz").write_text(min_khz + "\n")
        (base / "max_freq_khz").write_text(max_khz + "\n")
        return True
    except OSError:
        return False


def build_arms() -> list[dict]:
    arms = []
    seen = set()

    def add(group: str, vtag: str, aggr: str, topo: str, part: str = "shared", k: int = 64):
        vkind, vcpus, vphys = VICTIM_SPEC[vtag]
        acpus = AGGR_CPUS[topo]
        aphys = AGGR_PHYS_COUNT[topo]
        if aggr == "idle":
            lbl = f"{vtag}__idle"
            part_lbl = "shared"
            k_val = 64
        elif aggr == "l1-spin":
            lbl = f"{vtag}__l1-spin_{topo}"
            part_lbl = "shared"
            k_val = 64
        else:
            part_lbl = part
            k_val = k if part == "disjoint" else 64
            suffix = f"disjK{k_val}" if part == "disjoint" else "shared64"
            lbl = f"{vtag}__{aggr}_{suffix}_{topo}"
        if lbl in seen:
            return
        seen.add(lbl)
        arms.append({
            "group": group,
            "label": lbl,
            "victim_tag": vtag,
            "victim": vkind,
            "victim_cpus": vcpus,
            "victim_phys": vphys,
            "aggr": aggr,
            "partition_mode": part_lbl,
            "lines_per_core": k_val,
            "topo": topo,
            "aggr_cpus": acpus,
            "aggr_phys": aphys,
        })

    # Group A: 6c STREAM Triad (WB) & 16 MiB LLC chase across full topology + K matrix
    for vtag in ("t6c", "llc"):
        add("A", vtag, "idle", "0c")
        for topo in ("8c", "16c", "24c", "32t_16x2", "32t_24p8s", "48t_24x2"):
            if topo != "8c":
                add("A", vtag, "l1-spin", topo)
            add("A", vtag, "noop-stride", topo, "shared", 64)
            for k in (1, 2, 4, 8):
                add("A", vtag, "noop-stride", topo, "disjoint", k)

    # Group A key-point controls: 256 MiB DRAM chase, 6c STREAM Triad-NT, 6c STREAM RO
    for vtag in ("dram", "t6c_nt", "t6c_ro"):
        add("A", vtag, "idle", "0c")
        for topo in ("16c", "24c", "48t_24x2"):
            add("A", vtag, "l1-spin", topo)
            add("A", vtag, "noop-stride", topo, "shared", 64)
            for k in (2, 4):
                add("A", vtag, "noop-stride", topo, "disjoint", k)

    # Group B: 31c physical & 62t SMT with 1c 16 MiB L3 chase victim
    for topo in ("31c", "62t_31x2"):
        add("B", "llc", "l1-spin", topo)
        add("B", "llc", "noop-stride", topo, "shared", 64)
        for k in (1, 2, 4, 8):
            add("B", "llc", "noop-stride", topo, "disjoint", k)

    # Group B standalone: 32c physical (0 victims) ceiling
    add("B", "none", "noop-stride", "32c", "shared", 64)
    for k in (1, 2, 4, 8):
        add("B", "none", "noop-stride", "32c", "disjoint", k)

    return arms


def run_one(bin_path: Path, arm: dict, rep: int, duration_ms: int, warmup_ms: int) -> dict:
    cmd = [
        str(bin_path),
        "--victim", arm["victim"],
        "--aggr", arm["aggr"],
        "--partition-mode", arm["partition_mode"],
        "--lines-per-core", str(arm["lines_per_core"]),
        "--victim-cpus", arm["victim_cpus"],
        "--attacker-cpus", arm["aggr_cpus"],
        "--cha-occ", "wcilf",
        "--duration-ms", str(duration_ms),
        "--warmup-ms", str(warmup_ms),
        "--run-tag", str(rep),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, check=True)
    rec = json.loads(p.stdout.strip().splitlines()[-1])
    rec["group"] = arm["group"]
    rec["label"] = arm["label"]
    rec["victim_tag"] = arm["victim_tag"]
    rec["topo"] = arm["topo"]
    rec["rep"] = rep
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", type=Path, default=DEFAULT_BIN)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--duration-ms", type=int, default=600)
    ap.add_argument("--warmup-ms", type=int, default=300)
    ap.add_argument("--settle-s", type=int, default=21)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    runs_path = OUT_DIR / "runs.jsonl"
    env_path = OUT_DIR / "env.json"

    pre = host_snapshot(args.bin)
    orig_min = pre["uncore_khz"]["package_01_die_01"]["min"]
    orig_max = pre["uncore_khz"]["package_01_die_01"]["max"]
    uncore_locked = set_socket1_uncore("2200000", "2200000")

    arms = build_arms()
    total = len(arms) * args.reps
    print(f"[MRV-253] Starting {len(arms)} arms x {args.reps} interleaved reps = {total} runs (uncore_locked={uncore_locked})", flush=True)
    t0 = time.time()
    done = 0
    bad = 0

    try:
        with runs_path.open("w") as f:
            for rep in range(1, args.reps + 1):
                order = arms if (rep % 2 == 1) else list(reversed(arms))
                for arm in order:
                    rec = run_one(args.bin, arm, rep, args.duration_ms, args.warmup_ms)
                    # Basic sanity checks
                    assert rec["host_pmu_min_pct"] >= 99.9, f"PMU mux: {rec['host_pmu_min_pct']}"
                    assert rec["victim_aggr_phys_overlap"] == 0, "core overlap!"
                    assert rec["drain_ok"], "DSA drain failed!"
                    if rec["stream"] is not None:
                        assert rec["stream"]["verify_errors"] == 0, "STREAM verify error!"
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    done += 1
                    if done % 32 == 0 or done == total:
                        el = time.time() - t0
                        eta = el * (total - done) / max(done, 1)
                        print(f"[MRV-253] {done}/{total} ({el:.0f}s elapsed, ~{eta:.0f}s ETA) last={arm['label']} wcilf={rec['cha']['occ_entries']:.1f} iio={rec['iio0']['cpu_wr_mpps']:.1f}", flush=True)
    finally:
        if uncore_locked and orig_min != "na" and orig_max != "na":
            set_socket1_uncore(orig_min, orig_max)

    print(f"[MRV-253] Settling {args.settle_s}s for post-run EDAC/AER zero-delta audit...", flush=True)
    time.sleep(args.settle_s)
    post = host_snapshot(args.bin)

    edac_ce_delta = {k: post["host_edac_ce"][k] - pre["host_edac_ce"].get(k, 0) for k in post["host_edac_ce"]}
    edac_ue_delta = {k: post["host_edac_ue"][k] - pre["host_edac_ue"].get(k, 0) for k in post["host_edac_ue"]}
    aer_delta = {}
    for kind, pvals in post["dsa2_aer"].items():
        pre_vals = pre["dsa2_aer"].get(kind, {})
        aer_delta[kind] = {k: v - pre_vals.get(k, 0) for k, v in pvals.items()}

    env = {
        "experiment": "MRV-253",
        "cn106_touched": False,
        "uncore_locked_2200mhz": uncore_locked,
        "n_arms": len(arms),
        "reps": args.reps,
        "total_runs": done,
        "duration_ms": args.duration_ms,
        "warmup_ms": args.warmup_ms,
        "settle_s": args.settle_s,
        "elapsed_s": round(time.time() - t0, 1),
        "pre": pre,
        "post": post,
        "delta": {
            "edac_ce": edac_ce_delta,
            "edac_ue": edac_ue_delta,
            "dsa2_aer": aer_delta,
            "all_zero": (
                all(v == 0 for v in edac_ce_delta.values())
                and all(v == 0 for v in edac_ue_delta.values())
                and all(v == 0 for d in aer_delta.values() for v in d.values())
            ),
        },
    }
    env_path.write_text(json.dumps(env, indent=2) + "\n")
    print(f"[MRV-253] DONE: {done} runs in {env['elapsed_s']}s; EDAC/AER all_zero={env['delta']['all_zero']}", flush=True)
    return 0 if env["delta"]["all_zero"] else 1


if __name__ == "__main__":
    sys.exit(main())
