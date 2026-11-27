#!/usr/bin/env python3
"""MRV-230 n=3 interleaved pure-host Socket 1 DSA Portal (wq2.0) experiment harness.

Zero CN106 card access. Logs every attempt to attempts.jsonl, accepted verified runs
to runs.jsonl, and pre/post (21 s settle) host EDAC/AER/clock/DSA audits to env.json.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
DEFAULT_BIN = Path("/tmp/mrv230-target/release/mrv230_host")

AGGR_CPUS = {
    0: "52",
    1: "52",
    2: "52,53",
    4: "52,53,54,55",
    8: "52,53,54,55,56,57,58,59",
}
VICTIMS = [
    ("llc", "llc-chase", "40"),
    ("dram", "dram-chase", "40"),
    ("t6c", "stream-triad", "40,41,42,43,44,45"),
]


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
    eng_groups = {f"engine2.{i}": read_txt(f"/sys/bus/dsa/devices/engine2.{i}/group_id") for i in range(4)}
    return {
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cpu40_gov": read_txt("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor"),
        "cpu40_cur_khz": read_txt("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq"),
        "cpu40_max_khz": read_txt("/sys/devices/system/cpu/cpu40/cpufreq/scaling_max_freq"),
        "uncore_khz": uncore,
        "perf_event_paranoid": read_txt("/proc/sys/kernel/perf_event_paranoid"),
        "node1_free_hugepages_2m": read_txt("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages"),
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
    def add(vtag, vkind, vcpus, aggr, nc, sfb=512, cha_occ="all", credits=120, suffix=""):
        lbl = f"{vtag}__{aggr}_{nc}c_b{sfb}" if nc > 0 else f"{vtag}__{aggr}"
        if cha_occ != "all":
            lbl += f"_{cha_occ}"
        if suffix:
            lbl += f"_{suffix}"
        arms.append({
            "label": lbl, "victim": vkind, "victim_cpus": vcpus,
            "aggr": aggr, "aggr_cpus": AGGR_CPUS[nc], "n_aggr": nc,
            "sfence_batch": sfb, "cha_occ": cha_occ, "dsa_credits": credits,
        })

    if smoke:
        for vtag, vkind, vcpus in [("llc", "llc-chase", "40"), ("t6c", "stream-triad", "40,41,42,43,44,45")]:
            add(vtag, vkind, vcpus, "idle", 0)
            add(vtag, vkind, vcpus, "noop-stride", 8, 512)
            add(vtag, vkind, vcpus, "memmove-64k-credit", 8, 512)
            add(vtag, vkind, vcpus, "memmove-64k-stride", 8, 512)
        return arms

    # Full matrix across all 3 Socket 1 victims (cha_occ=all)
    for vtag, vkind, vcpus in VICTIMS:
        add(vtag, vkind, vcpus, "idle", 0)
        for nc in (1, 2, 4, 8):
            add(vtag, vkind, vcpus, "l1-spin", nc)
        for aggr in ("noop-stride", "noop-single", "memmove-64k-stride", "memmove-64k-single", "memmove-64k-credit"):
            for nc in (1, 2, 4, 8):
                add(vtag, vkind, vcpus, aggr, nc, 512)
        # Single-variable SFENCE cadence ablation (b=0 unfenced & b=128 vs b=512 above)
        for aggr in ("noop-stride", "memmove-64k-stride"):
            for sfb in (0, 128):
                add(vtag, vkind, vcpus, aggr, 8, sfb)
        add(vtag, vkind, vcpus, "noop-stride", 1, 0)

    # Secondary WCiLF-specific CHA residency pass on key arms (llc + t6c)
    for vtag, vkind, vcpus in [("llc", "llc-chase", "40"), ("t6c", "stream-triad", "40,41,42,43,44,45")]:
        for aggr, nc, sfb in [
            ("noop-stride", 1, 512), ("noop-stride", 8, 512), ("noop-stride", 8, 0),
            ("noop-single", 1, 512), ("noop-single", 8, 512),
            ("memmove-64k-stride", 1, 512), ("memmove-64k-stride", 8, 512), ("memmove-64k-stride", 8, 0),
            ("memmove-64k-credit", 8, 512),
        ]:
            add(vtag, vkind, vcpus, aggr, nc, sfb, cha_occ="wcilf")
    return arms


def validate_run(rec: dict, arm: dict) -> list[str]:
    reasons = []
    if rec.get("host_pmu_min_pct", 0.0) < 100.0:
        reasons.append(f"pmu_pct={rec.get('host_pmu_min_pct')}")
    if rec.get("page_faults", 1) != 0 or rec.get("victim_page_faults", 1) != 0 or rec.get("aggr_page_faults", 1) != 0:
        reasons.append(f"pf={rec.get('page_faults')}/{rec.get('victim_page_faults')}/{rec.get('aggr_page_faults')}")
    if not rec.get("drain_ok", False):
        reasons.append(f"drain_ok={rec.get('drain_ok')}")
    if rec.get("dsa_comp_errors", 1) != 0 or rec.get("dsa_verify_errors", 1) != 0:
        reasons.append(f"dsa_err={rec.get('dsa_comp_errors')}/{rec.get('dsa_verify_errors')}")
    if rec.get("dsa2_pmu", {}).get("cpl_err", 1) != 0:
        reasons.append(f"cpl_err={rec['dsa2_pmu'].get('cpl_err')}")
    if arm["aggr"] == "memmove-64k-credit" and rec.get("dsa2_pmu", {}).get("dwq_full", 1) != 0:
        reasons.append(f"credit_dwq_full={rec['dsa2_pmu'].get('dwq_full')}")
    if rec.get("chase") and rec["chase"].get("bad_nodes", 1) != 0:
        reasons.append(f"bad_nodes={rec['chase'].get('bad_nodes')}")
    if rec.get("stream") and rec["stream"].get("verify_errors", 1) != 0:
        reasons.append(f"stream_err={rec['stream'].get('verify_errors')}")
    total_cores = len(arm["victim_cpus"].split(",")) + arm["n_aggr"]
    min_ghz_gate = 3.75 if total_cores <= 9 else 3.40
    if rec.get("victim_min_ghz", 0.0) < min_ghz_gate:
        reasons.append(f"v_ghz={rec.get('victim_min_ghz')}<{min_ghz_gate}")
    if arm["n_aggr"] > 0 and rec.get("aggr_min_ghz", 0.0) < min_ghz_gate:
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
        ["jj", "log", "-r", "latest(::@ & files(testbed/))", "--no-graph", "-T", 'change_id.short() ++ " " ++ commit_id.short() ++ " " ++ description.first_line()'],
        cwd=ROOT, text=True,
    ).strip()

    t_start = time.time()
    pre = host_snapshot(args.bin)
    completed = 0
    total = len(arms) * reps
    tag = 1

    with att_path.open("a") as fa, run_path.open("a") as fr:
        for rep in range(1, reps + 1):
            order = arms if (rep % 2 == 1) else list(reversed(arms))
            for arm in order:
                ok_rec = None
                total_cores = len(arm["victim_cpus"].split(",")) + arm["n_aggr"]
                min_ghz_arg = 3.80 if total_cores <= 9 else 3.45
                for attempt in range(1, args.max_retries + 2):
                    cmd = [
                        str(args.bin),
                        "--victim", arm["victim"],
                        "--victim-cpus", arm["victim_cpus"],
                        "--aggr", arm["aggr"],
                        "--aggr-cpus", arm["aggr_cpus"],
                        "--sfence-batch", str(arm["sfence_batch"]),
                        "--dsa-credits", str(arm["dsa_credits"]),
                        "--cha-occ", arm["cha_occ"],
                        "--min-ghz", f"{min_ghz_arg:.2f}",
                        "--duration-ms", str(dur_ms),
                        "--warmup-ms", str(args.warmup_ms),
                        "--run-tag", str(tag),
                    ]
                    tag += 1
                    proc = subprocess.run(cmd, capture_output=True, text=True)
                    if proc.returncode != 0:
                        rec = {"label": arm["label"], "rep": rep, "attempt": attempt, "accepted": False,
                               "reject_reasons": [f"rc={proc.returncode}:{proc.stderr.strip()[:200]}"]}
                        fa.write(json.dumps(rec) + "\n")
                        fa.flush()
                        continue
                    rec = json.loads(proc.stdout.strip())
                    reasons = validate_run(rec, arm)
                    rec["label"] = arm["label"]
                    rec["rep"] = rep
                    rec["attempt"] = attempt
                    rec["accepted"] = (len(reasons) == 0)
                    rec["reject_reasons"] = reasons
                    fa.write(json.dumps(rec) + "\n")
                    fa.flush()
                    if rec["accepted"]:
                        ok_rec = rec
                        break
                if ok_rec is None:
                    raise RuntimeError(f"Arm {arm['label']} rep {rep} failed after retries")
                fr.write(json.dumps(ok_rec) + "\n")
                fr.flush()
                completed += 1
                ch, st = ok_rec.get("chase"), ok_rec.get("stream")
                vm = f"lat={ch['net_mean_ns']:.2f} p99={ch['p99_ns']:.1f}" if ch else f"bw={st['mean_gb_s']:.2f}"
                print(f"[{completed:3d}/{total}] r{rep} {arm['label']:36s} {vm:18s} mpps={ok_rec['aggr_mpps']:6.1f} cha={ok_rec['cha']['occ_entries']:5.1f} imc={ok_rec['imc']['tot_gb_s']:4.1f}", flush=True)

    if not args.smoke and args.settle_s > 0:
        print(f"Settling {args.settle_s}s for post-run EDAC/AER audit...", flush=True)
        time.sleep(args.settle_s)
    post = host_snapshot(args.bin)
    edac_ce_delta = {k: post["host_edac_ce"].get(k, 0) - pre["host_edac_ce"].get(k, 0) for k in pre["host_edac_ce"]}
    edac_ue_delta = {k: post["host_edac_ue"].get(k, 0) - pre["host_edac_ue"].get(k, 0) for k in pre["host_edac_ue"]}
    aer_delta = {}
    for kind, pvals in post["dsa2_aer"].items():
        aer_delta[kind] = {k: v - pre["dsa2_aer"].get(kind, {}).get(k, 0) for k, v in pvals.items()}
    pkg1 = post["uncore_khz"]["package_01_die_01"]
    uncore_pkg1_ok = (pkg1["min"] == "2200000" and pkg1["max"] == "2200000" and pkg1["cur"] == "2200000")
    cpu40_ok = (post["cpu40_gov"] == "performance" and post["cpu40_max_khz"] == "3900000")
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
    }
    env = {
        "issue": "MRV-230",
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
