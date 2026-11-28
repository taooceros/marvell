#!/usr/bin/env python3
"""Summarize MRV-259 runs.jsonl into median [min, max] tables for reporting."""

import collections
import json
import pathlib
import statistics

RUNS_PATH = pathlib.Path(__file__).resolve().parent / "runs.jsonl"


def main():
    rows = []
    with open(RUNS_PATH) as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    print(f"Total verified runs in runs.jsonl: {len(rows)}")

    groups = collections.defaultdict(list)
    for r in rows:
        key = (r["app"], r["workload"], r["host_cores"], r["card_cores"], r["window"], r["policy"])
        groups[key].append(r)

    print(
        f"{'app':3s} {'wl':7s} {'H':1s} {'K':1s} {'W':3s} {'policy':13s} {'n':2s} | "
        f"{'Mrps(med)':>10s} {'p50(us)':>8s} {'p99(us)':>8s} {'busy_ns':>8s} {'exlmn/r':>8s} {'snp/r':>8s}"
    )
    print("-" * 96)
    for key in sorted(groups.keys()):
        rs = groups[key]
        app, wl, hc, kc, win, pol = key
        mrps = statistics.median(r["client"]["achieved_mrps"] for r in rs)
        p50 = statistics.median(r["client"]["tot_p50_us"] for r in rs)
        p99 = statistics.median(r["client"]["tot_p99_us"] for r in rs)
        busy = statistics.median(r["host"].get("busy_ns_per_req", 0.0) for r in rs)
        exlmn = statistics.median(
            ((r["card"].get("tad_pmu") or r["card"].get("tad") or {}).get("tad_snp_msh_out_exlmn", 0))
            / max(r["card"].get("rpcs_out", 1), 1)
            for r in rs
        )
        snp = statistics.median(
            ((r["card"].get("tad_pmu") or r["card"].get("tad") or {}).get("tad_snp_sent", 0))
            / max(r["card"].get("rpcs_out", 1), 1)
            for r in rs
        )
        print(
            f"{app:3s} {wl:7s} {hc:1d} {kc:1d} {win:3d} {pol:13s} {len(rs):2d} | "
            f"{mrps:10.3f} {p50:8.2f} {p99:8.2f} {busy:8.1f} {exlmn:8.3f} {snp:8.3f}"
        )


if __name__ == "__main__":
    main()
