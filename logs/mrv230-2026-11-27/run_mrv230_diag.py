#!/usr/bin/env python3
"""MRV-230 n=3 causal-decomposition diagnostic supplement (pure-host Socket 1).

Uses the exact same compiled mrv230_host binary as run_mrv230.py to separate:
  1. Pure DWQ-full SWERROR drop path with minimal DMA (--dsa-xfer-bytes 64)
  2. Intermediate 4 KiB DMA + flood (--dsa-xfer-bytes 4096)
  3. Cache-allocating DDIO DMA (--dsa-cc) under both bounded credit and 8c flood
Logs to diag_attempts.jsonl, diag_runs.jsonl, and diag_env.json with 21 s EDAC/AER audit.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from run_mrv230 import DEFAULT_BIN, OUT_DIR, ROOT, host_snapshot, validate_run


def build_diag_arms() -> list[dict]:
    arms = []

    def add(vtag, vkind, vcpus, aggr, nc, xfer=65536, cc=False, sfb=512, cha_occ="all", credits=120, tag_mid=""):
        lbl = f"{vtag}__{aggr}_{tag_mid}_{nc}c_b{sfb}"
        if cha_occ != "all":
            lbl += f"_{cha_occ}"
        aggr_cpus = "52,53,54,55,56,57,58,59" if nc == 8 else "52"
        arms.append({
            "label": lbl, "victim": vkind, "victim_cpus": vcpus,
            "aggr": aggr, "aggr_cpus": aggr_cpus, "n_aggr": nc,
            "sfence_batch": sfb, "cha_occ": cha_occ, "dsa_credits": credits,
            "dsa_xfer_bytes": xfer, "dsa_cc": cc,
        })

    victims3 = [
        ("llc", "llc-chase", "40"),
        ("dram", "dram-chase", "40"),
        ("t6c", "stream-triad", "40,41,42,43,44,45"),
    ]
    for vtag, vkind, vcpus in victims3:
        add(vtag, vkind, vcpus, "memmove-64k-stride", 8, xfer=64, cc=False, tag_mid="x64B")
        add(vtag, vkind, vcpus, "memmove-64k-stride", 8, xfer=65536, cc=True, tag_mid="x64K_cc")
        add(vtag, vkind, vcpus, "memmove-64k-credit", 8, xfer=65536, cc=True, tag_mid="x64K_cc")

    for vtag, vkind, vcpus in [("llc", "llc-chase", "40"), ("t6c", "stream-triad", "40,41,42,43,44,45")]:
        add(vtag, vkind, vcpus, "memmove-64k-stride", 8, xfer=4096, cc=False, tag_mid="x4K")
        add(vtag, vkind, vcpus, "memmove-64k-stride", 8, xfer=64, cc=False, cha_occ="wcilf", tag_mid="x64B")
        add(vtag, vkind, vcpus, "memmove-64k-stride", 8, xfer=65536, cc=True, cha_occ="wcilf", tag_mid="x64K_cc")

    return arms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", type=Path, default=DEFAULT_BIN)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--duration-ms", type=int, default=1000)
    ap.add_argument("--warmup-ms", type=int, default=400)
    ap.add_argument("--settle-s", type=int, default=21)
    ap.add_argument("--max-retries", type=int, default=2)
    args = ap.parse_args()

    arms = build_diag_arms()
    att_path = OUT_DIR / "diag_attempts.jsonl"
    run_path = OUT_DIR / "diag_runs.jsonl"
    env_path = OUT_DIR / "diag_env.json"
    for p in (att_path, run_path):
        if p.exists():
            p.unlink()

    code_rev = subprocess.check_output(
        ["jj", "log", "-r", "latest(::@ & files(testbed/))", "--no-graph", "-T",
         'change_id.short() ++ " " ++ commit_id.short() ++ " " ++ description.first_line()'],
        cwd=ROOT, text=True,
    ).strip()

    t_start = time.time()
    pre = host_snapshot(args.bin)
    completed = 0
    total = len(arms) * args.reps
    tag = 5001

    with att_path.open("a") as fa, run_path.open("a") as fr:
        for rep in range(1, args.reps + 1):
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
                        "--dsa-xfer-bytes", str(arm["dsa_xfer_bytes"]),
                        "--cha-occ", arm["cha_occ"],
                        "--min-ghz", f"{min_ghz_arg:.2f}",
                        "--duration-ms", str(args.duration_ms),
                        "--warmup-ms", str(args.warmup_ms),
                        "--run-tag", str(tag),
                    ]
                    if arm["dsa_cc"]:
                        cmd.append("--dsa-cc")
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
                    raise RuntimeError(f"Diag arm {arm['label']} rep {rep} failed after retries")
                fr.write(json.dumps(ok_rec) + "\n")
                fr.flush()
                completed += 1
                ch, st = ok_rec.get("chase"), ok_rec.get("stream")
                vm = f"lat={ch['net_mean_ns']:.2f} p99={ch['p99_ns']:.1f}" if ch else f"bw={st['mean_gb_s']:.2f}"
                print(f"[{completed:2d}/{total}] r{rep} {arm['label']:44s} {vm:18s} mpps={ok_rec['aggr_mpps']:5.1f} cha={ok_rec['cha']['occ_entries']:5.1f} imc={ok_rec['imc']['tot_gb_s']:4.1f}", flush=True)

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
        "issue": "MRV-230-diag",
        "code_commit": code_rev,
        "duration_ms": args.duration_ms,
        "warmup_ms": args.warmup_ms,
        "reps": args.reps,
        "unique_arms": len(arms),
        "planned_runs": total,
        "completed_runs": completed,
        "elapsed_s": round(time.time() - t_start, 2),
        "settle_s": args.settle_s,
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
