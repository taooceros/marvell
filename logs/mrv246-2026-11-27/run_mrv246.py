#!/usr/bin/env python3
"""MRV-246 n=3 interleaved pure-host Socket 1 DSA Portal (wq2.0) core-scaling harness.

Scales 4 KiB (64-line) noop-stride, 64 B (1-line) noop-fixed, and same-core l1-spin
controls across 0..32 physical cores and 32t/48t/62t/64t SMT topologies on Socket 1
with strictly disjoint co-located neighbors (6c STREAM Triad, 16 MiB LLC chase,
256 MiB DRAM chase) and standalone (0 victims).

Zero CN106 card access. Logs every attempt to attempts.jsonl, accepted verified runs
to runs.jsonl, and pre/post (21 s settle) host EDAC/AER/clock/DSA audits to env.json.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
DEFAULT_BIN = Path("/tmp/mrv246-target/release/mrv246_host")


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
    "1c": "52",
    "4c": csv_range((52, 55)),
    "8c": csv_range((52, 59)),
    "12c": csv_range((52, 63)),
    "16c": csv_range((48, 63)),
    "20c": csv_range((46, 63), (32, 33)),
    "24c": csv_range((46, 63), (32, 37)),
    # 32t SMT topology 1: 16 physical cores x 2 SMT siblings
    "32t_16x2": csv_range((48, 63), (112, 127)),
    # 32t SMT topology 2: 24 physical cores + 8 SMT siblings (of cpu52..59)
    "32t_24p8s": csv_range((46, 63), (32, 37), (116, 123)),
    # 48t SMT: 24 physical cores x 2 SMT siblings
    "48t_24x2": csv_range((46, 63), (32, 37), (110, 127), (96, 101)),
    # Group B (victim=llc on cpu40 only, cpu104 unused): all 31 other physical cores
    "31c": csv_range((32, 39), (41, 63)),
    "62t_31x2": csv_range((32, 39), (41, 63), (96, 103), (105, 127)),
    # Group B standalone (victim=none): all 32 physical cores / 64 SMT threads on Socket 1
    "32c": csv_range((32, 63)),
    "64t_32x2": csv_range((32, 63), (96, 127)),
}

AGGR_PHYS_COUNT = {
    "0c": 0, "1c": 1, "4c": 4, "8c": 8, "12c": 12, "16c": 16, "20c": 20, "24c": 24,
    "32t_16x2": 16, "32t_24p8s": 24, "48t_24x2": 24,
    "31c": 31, "62t_31x2": 31, "32c": 32, "64t_32x2": 32,
}

VICTIM_SPEC = {
    "t6c": ("stream-triad", "40,41,42,43,44,45", 6),
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


def build_arms(smoke: bool) -> list[dict]:
    arms = []
    seen = set()

    def add(group: str, vtag: str, aggr: str, topo: str, cha_occ: str = "all", sfb: int = 512):
        vkind, vcpus, vphys = VICTIM_SPEC[vtag]
        acpus = AGGR_CPUS[topo]
        aphys = AGGR_PHYS_COUNT[topo]
        lbl = f"{vtag}__{aggr}_{topo}" if aggr != "idle" else f"{vtag}__idle"
        if cha_occ != "all":
            lbl += f"_{cha_occ}"
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
            "topo": topo,
            "aggr_cpus": acpus,
            "aggr_phys": aphys,
            "sfence_batch": sfb,
            "cha_occ": cha_occ,
        })

    if smoke:
        add("A", "t6c", "idle", "0c", "all")
        add("A", "t6c", "noop-stride", "8c", "all")
        add("A", "t6c", "noop-stride", "24c", "all")
        add("A", "t6c", "noop-fixed", "24c", "all")
        add("A", "t6c", "l1-spin", "24c", "all")
        add("A", "llc", "noop-stride", "32t_16x2", "all")
        add("B", "llc", "noop-stride", "31c", "all")
        add("B", "none", "noop-stride", "32c", "wcilf")
        return arms

    # ---- Group A: Core scaling with 6c STREAM & 16 MiB L3 (cha_occ=all) ----
    stride_topos = ["1c", "4c", "8c", "12c", "16c", "20c", "24c", "32t_16x2", "32t_24p8s", "48t_24x2"]
    fixed_topos = ["1c", "8c", "16c", "24c", "32t_16x2"]
    spin_topos = ["1c", "8c", "16c", "24c", "32t_16x2", "48t_24x2"]

    for vtag in ("t6c", "llc"):
        add("A", vtag, "idle", "0c", "all")
        for topo in stride_topos:
            add("A", vtag, "noop-stride", topo, "all")
        for topo in fixed_topos:
            add("A", vtag, "noop-fixed", topo, "all")
        for topo in spin_topos:
            add("A", vtag, "l1-spin", topo, "all")

    # Group A key points on 256 MiB DRAM chase (cha_occ=all)
    add("A", "dram", "idle", "0c", "all")
    for topo in ("1c", "8c", "16c", "24c", "32t_16x2", "32t_24p8s", "48t_24x2"):
        add("A", "dram", "noop-stride", topo, "all")
    for topo in ("8c", "24c"):
        add("A", "dram", "noop-fixed", topo, "all")
    for topo in ("8c", "16c", "24c", "48t_24x2"):
        add("A", "dram", "l1-spin", topo, "all")

    # ---- Group B: 31 physical cores (+ 62t SMT) with 1c L3 chase ONLY ----
    for topo in ("31c", "62t_31x2"):
        add("B", "llc", "noop-stride", topo, "all")
        add("B", "llc", "l1-spin", topo, "all")
    add("B", "llc", "noop-fixed", "31c", "all")

    # ---- Group B: Standalone (0 victims) full scaling ladder (BOTH all & wcilf) ----
    standalone_stride = [
        "1c", "4c", "8c", "12c", "16c", "20c", "24c", "31c", "32c",
        "32t_16x2", "32t_24p8s", "48t_24x2", "64t_32x2",
    ]
    standalone_fixed = ["1c", "8c", "16c", "24c", "32c", "32t_16x2"]
    for occ in ("all", "wcilf"):
        for topo in standalone_stride:
            add("B", "none", "noop-stride", topo, occ)
        for topo in standalone_fixed:
            add("B", "none", "noop-fixed", topo, occ)

    # ---- Paired WCiLF pass on t6c & llc scaling ladders ----
    for topo in stride_topos:
        add("A", "t6c", "noop-stride", topo, "wcilf")
    for topo in ("8c", "24c"):
        add("A", "t6c", "noop-fixed", topo, "wcilf")

    for topo in stride_topos + ["31c", "62t_31x2"]:
        grp = "B" if topo in ("31c", "62t_31x2") else "A"
        add(grp, "llc", "noop-stride", topo, "wcilf")
    for topo in ("8c", "24c"):
        add("A", "llc", "noop-fixed", topo, "wcilf")

    return arms


def ghz_targets(arm: dict) -> tuple[float, float]:
    """Return (warmup_min_ghz, accept_min_ghz) matching Xeon 6438M active-core turbo curve."""
    tot_phys = arm["victim_phys"] + arm["aggr_phys"]
    if tot_phys <= 8:
        return (3.60, 3.45)
    if tot_phys <= 16:
        return (3.15, 2.95)
    if tot_phys <= 22:
        return (2.80, 2.55)
    # 23..32 physical cores active on Socket 1: all-core turbo ~2.60-2.80 GHz
    # (l1-spin + AVX2 STREAM at 30+ cores can sit slightly lower under TDP)
    if arm["aggr"] == "l1-spin":
        return (2.55, 2.15)
    return (2.60, 2.35)


def validate_run(rec: dict, arm: dict) -> list[str]:
    reasons = []
    if rec.get("host_pmu_min_pct", 0.0) < 100.0:
        reasons.append(f"pmu_pct={rec.get('host_pmu_min_pct')}")
    if (
        rec.get("page_faults", 1) != 0
        or rec.get("victim_page_faults", 1) != 0
        or rec.get("aggr_page_faults", 1) != 0
    ):
        reasons.append(
            f"pf={rec.get('page_faults')}/{rec.get('victim_page_faults')}/{rec.get('aggr_page_faults')}"
        )
    if rec.get("victim_aggr_phys_overlap", 1) != 0:
        reasons.append(f"overlap={rec.get('victim_aggr_phys_overlap')}")
    if not rec.get("drain_ok", False):
        reasons.append(f"drain_ok={rec.get('drain_ok')}")
    if rec.get("dsa_comp_errors", 1) != 0 or rec.get("dsa_verify_errors", 1) != 0:
        reasons.append(f"dsa_err={rec.get('dsa_comp_errors')}/{rec.get('dsa_verify_errors')}")
    if rec.get("dsa2_pmu", {}).get("cpl_err", 1) != 0:
        reasons.append(f"cpl_err={rec['dsa2_pmu'].get('cpl_err')}")
    if rec.get("chase") and rec["chase"].get("bad_nodes", 1) != 0:
        reasons.append(f"bad_nodes={rec['chase'].get('bad_nodes')}")
    if rec.get("stream") and rec["stream"].get("verify_errors", 1) != 0:
        reasons.append(f"stream_err={rec['stream'].get('verify_errors')}")
    mesh_ghz = rec.get("cha", {}).get("mesh_ghz", 0.0)
    if abs(mesh_ghz - 2.20) > 0.05:
        reasons.append(f"mesh_ghz={mesh_ghz:.4f}")
    _, min_ghz_gate = ghz_targets(arm)
    if arm["victim_phys"] > 0 and rec.get("victim_min_ghz", 0.0) < min_ghz_gate:
        reasons.append(f"v_ghz={rec.get('victim_min_ghz')}<{min_ghz_gate}")
    if arm["aggr_phys"] > 0 and rec.get("aggr_min_ghz", 0.0) < min_ghz_gate:
        reasons.append(f"a_ghz={rec.get('aggr_min_ghz')}<{min_ghz_gate}")
    return reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", type=Path, default=DEFAULT_BIN)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--duration-ms", type=int, default=1000)
    ap.add_argument("--warmup-ms", type=int, default=400)
    ap.add_argument("--settle-s", type=int, default=21)
    ap.add_argument("--max-retries", type=int, default=2)
    args = ap.parse_args()

    reps = 1 if args.smoke else args.reps
    dur_ms = 400 if args.smoke else args.duration_ms
    arms = build_arms(args.smoke)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    att_path = OUT_DIR / ("smoke_attempts.jsonl" if args.smoke else "attempts.jsonl")
    run_path = OUT_DIR / ("smoke_runs.jsonl" if args.smoke else "runs.jsonl")
    env_path = OUT_DIR / ("smoke_env.json" if args.smoke else "env.json")

    for p in (att_path, run_path):
        if p.exists():
            p.unlink()

    code_rev = subprocess.check_output(
        [
            "jj", "log", "-r", "latest(::@ & files(testbed/))", "--no-graph",
            "-T", 'change_id.short() ++ " " ++ commit_id.short() ++ " " ++ description.first_line()',
        ],
        cwd=ROOT,
        text=True,
    ).strip()

    t_start = time.time()
    pre = host_snapshot(args.bin)
    completed = 0
    total = len(arms) * reps
    tag = 1

    print(
        f"MRV-246 start: smoke={args.smoke} arms={len(arms)} reps={reps} total={total} "
        f"dur={dur_ms}ms code={code_rev}",
        flush=True,
    )

    with att_path.open("a") as fa, run_path.open("a") as fr:
        for rep in range(1, reps + 1):
            order = arms if (rep % 2 == 1) else list(reversed(arms))
            for arm in order:
                ok_rec = None
                warmup_ghz, _ = ghz_targets(arm)
                for attempt in range(1, args.max_retries + 2):
                    cmd = [
                        str(args.bin),
                        "--victim", arm["victim"],
                        "--aggr", arm["aggr"],
                        "--coord-cpu", "0",
                        "--sfence-batch", str(arm["sfence_batch"]),
                        "--cha-occ", arm["cha_occ"],
                        "--min-ghz", f"{warmup_ghz:.2f}",
                        "--duration-ms", str(dur_ms),
                        "--warmup-ms", str(args.warmup_ms),
                        "--run-tag", str(tag),
                    ]
                    if arm["victim_cpus"]:
                        cmd += ["--victim-cpus", arm["victim_cpus"]]
                    if arm["aggr_cpus"]:
                        cmd += ["--attacker-cpus", arm["aggr_cpus"]]
                    tag += 1
                    proc = subprocess.run(cmd, capture_output=True, text=True)
                    if proc.returncode != 0:
                        rec = {
                            "group": arm["group"],
                            "label": arm["label"],
                            "topo": arm["topo"],
                            "rep": rep,
                            "attempt": attempt,
                            "accepted": False,
                            "reject_reasons": [f"rc={proc.returncode}:{proc.stderr.strip()[:200]}"],
                        }
                        fa.write(json.dumps(rec) + "\n")
                        fa.flush()
                        continue
                    rec = json.loads(proc.stdout.strip())
                    reasons = validate_run(rec, arm)
                    rec["group"] = arm["group"]
                    rec["label"] = arm["label"]
                    rec["topo"] = arm["topo"]
                    rec["rep"] = rep
                    rec["attempt"] = attempt
                    rec["accepted"] = len(reasons) == 0
                    rec["reject_reasons"] = reasons
                    fa.write(json.dumps(rec) + "\n")
                    fa.flush()
                    if rec["accepted"]:
                        ok_rec = rec
                        break
                    else:
                        print(
                            f"  RETRY r{rep} {arm['label']} att={attempt} reasons={reasons}",
                            flush=True,
                        )
                if ok_rec is None:
                    raise RuntimeError(f"Arm {arm['label']} rep {rep} failed after retries")
                fr.write(json.dumps(ok_rec) + "\n")
                fr.flush()
                completed += 1
                ch, st = ok_rec.get("chase"), ok_rec.get("stream")
                if ch:
                    vm = f"lat={ch['net_mean_ns']:6.1f} p99={ch['p99_ns']:6.1f}"
                elif st:
                    vm = f"bw={st['mean_gb_s']:6.2f} GB/s    "
                else:
                    vm = "standalone          "
                print(
                    f"[{completed:3d}/{total}] r{rep} {arm['label']:36s} {vm} "
                    f"mpps={ok_rec['aggr_mpps']:5.1f} cha={ok_rec['cha']['occ_entries']:5.1f} "
                    f"({ok_rec['cha_occ_mode']:5s}) irp={ok_rec['irp0']['occ_entries']:4.2f} "
                    f"vGHz={ok_rec['victim_mean_ghz']:.2f} aGHz={ok_rec['aggr_mean_ghz']:.2f}",
                    flush=True,
                )

    if not args.smoke and args.settle_s > 0:
        print(f"Settling {args.settle_s}s for post-run EDAC/AER audit...", flush=True)
        time.sleep(args.settle_s)
    post = host_snapshot(args.bin)
    edac_ce_delta = {
        k: post["host_edac_ce"].get(k, 0) - pre["host_edac_ce"].get(k, 0)
        for k in pre["host_edac_ce"]
    }
    edac_ue_delta = {
        k: post["host_edac_ue"].get(k, 0) - pre["host_edac_ue"].get(k, 0)
        for k in pre["host_edac_ue"]
    }
    aer_delta = {}
    for kind, pvals in post["dsa2_aer"].items():
        aer_delta[kind] = {
            k: v - pre["dsa2_aer"].get(kind, {}).get(k, 0) for k, v in pvals.items()
        }
    pkg1 = post["uncore_khz"]["package_01_die_01"]
    uncore_pkg1_ok = (
        pkg1["min"] == "2200000" and pkg1["max"] == "2200000" and pkg1["cur"] == "2200000"
    )
    cpu40_ok = post["cpu40_gov"] == "performance" and post["cpu40_max_khz"] == "3900000"
    audit_reasons = []
    if any(v != 0 for v in edac_ce_delta.values()) or any(v != 0 for v in edac_ue_delta.values()):
        audit_reasons.append("edac_nonzero_delta")
    if any(v != 0 for d in aer_delta.values() for v in d.values()):
        audit_reasons.append("aer_nonzero_delta")
    if not uncore_pkg1_ok:
        audit_reasons.append(f"uncore_pkg1={pkg1}")
    if not cpu40_ok:
        audit_reasons.append(f"cpu40={post['cpu40_gov']}/{post['cpu40_max_khz']}")
    audit = {
        "audit_pass": len(audit_reasons) == 0,
        "reasons": audit_reasons,
        "edac_ce_delta": edac_ce_delta,
        "edac_ue_delta": edac_ue_delta,
        "dsa2_aer_delta": aer_delta,
        "uncore_pkg1_ok": uncore_pkg1_ok,
        "cpu40_ok": cpu40_ok,
        "cn106_touched": False,
    }
    env = {
        "issue": "MRV-246",
        "smoke": args.smoke,
        "code_commit": code_rev,
        "duration_ms": dur_ms,
        "warmup_ms": args.warmup_ms,
        "reps": reps,
        "unique_arms": len(arms),
        "planned_runs": total,
        "completed_runs": completed,
        "elapsed_s": round(time.time() - t_start, 2),
        "settle_s": 0 if args.smoke else args.settle_s,
        "pre_host": pre,
        "post_host": post,
        "audit": audit,
    }
    env_path.write_text(json.dumps(env, indent=2) + "\n")
    print(f"DONE {completed}/{total} in {env['elapsed_s']}s audit_pass={audit['audit_pass']} -> {env_path}")
    if not audit["audit_pass"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
