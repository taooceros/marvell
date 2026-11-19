#!/usr/bin/env python3
"""Verify every quantitative claim in data/FACTS.md directly against all 244 raw runs."""

import json
import statistics
from pathlib import Path

R95 = Path("/home/hongtao/dev/marvell/logs/mrv95-2026-11-19/runs.jsonl")
R99 = Path("/home/hongtao/dev/marvell/logs/mrv99-2026-11-19/runs.jsonl")
R99_DIAG = Path("/home/hongtao/dev/marvell/logs/mrv99-2026-11-19/diag_min_batch.jsonl")
R106 = Path("/home/hongtao/dev/marvell-mrv106/logs/mrv106-2026-11-19/runs.jsonl")


def load(p: Path):
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def med(xs):
    return statistics.median(xs)


def approx(a, b, tol, label):
    if abs(a - b) > tol:
        raise AssertionError(f"MISMATCH {label}: got {a}, expected {b} (tol={tol})")


def main():
    r95, r99, d99, r106 = load(R95), load(R99), load(R99_DIAG), load(R106)
    assert (len(r95), len(r99), len(d99), len(r106)) == (94, 90, 12, 48)

    # --- Stage 1 (MRV-95) ---
    approx(med([r["p50_ns"] for r in r95 if r.get("mode") == "poll_only"]), 1212.0, 1.0, "dpi_read_floor_ns")
    approx(med([r["p50_ns"] for r in r95 if r.get("submitter") == "host_movdir64b" and r.get("mode") == "w1_lat" and r.get("op") == "nop"]) / 1000, 0.40, 0.01, "host_nop")
    approx(med([r["p50_ns"] for r in r95 if r.get("submitter") == "card_pem_wc_none" and r.get("mode") == "w1_lat" and r.get("op") == "nop"]) / 1000, 1.30, 0.01, "wc_nop")
    approx(med([r["p50_ns"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "w1_lat" and r.get("op") == "nop"]) / 1000, 1.98, 0.01, "dpi_nop")

    for sub, exp_bad, exp_tot, exp_pct in [
        ("card_pem_wc_dsb", 28, 2300, 1.217),
        ("card_pem_wc_dgh", 14, 2300, 0.609),
        ("card_pem_wc_none", 12, 24900, 0.048),
        ("card_dpi_dma", 0, 24900, 0.000),
    ]:
        rs = [r for r in r95 if r.get("submitter") == sub and r.get("mode") == "w1_lat"]
        bad, tot = sum(r["bad"] for r in rs), sum(r["rounds"] for r in rs)
        assert (bad, tot) == (exp_bad, exp_tot)
        approx(100.0 * bad / tot, exp_pct, 0.002, f"drop_{sub}")

    assert sum(r["ops"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "rate") == 182913304
    assert sum(r["lost"] + r["bad"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "rate") == 0
    approx(med([r["mops_s"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "rate" and r.get("op") == "nop" and r.get("window") == 96]), 14.23, 0.01, "nop96")
    approx(med([r["gb_s"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "rate" and r.get("op") == "memmove" and r.get("window") == 96]), 29.53, 0.01, "mm96")

    # --- Stage 2 (MRV-99) ---
    assert sum(r.get("lost", 0) + r.get("bad", 0) for r in r99 + d99) == 0

    def m99(ct, mode, op, sz, w=1, field="p50_ns"):
        return med([r[field] for r in r99 if r["comp_target"] == ct and r["mode"] == mode and r["op"] == op and r["size"] == sz and r.get("window", 1) == w])

    approx(1.0 / m99("host_memzone", "rate", "nop", 0, 1, "mops_s"), 1.96, 0.01, "host_nop_w1")
    approx(1.0 / m99("card_bar4", "rate", "nop", 0, 1, "mops_s"), 1.27, 0.01, "bar4_nop_w1")
    approx(1.0 / m99("host_memzone", "rate", "memmove", 4096, 1, "mops_s"), 3.13, 0.01, "host_4k_w1")
    approx(1.0 / m99("card_bar4", "rate", "memmove", 4096, 1, "mops_s"), 2.17, 0.01, "bar4_4k_w1")
    approx(m99("host_memzone", "w1_lat", "memmove", 4096, 1, "p99_ns") - m99("host_memzone", "w1_lat", "memmove", 4096, 1, "p50_ns"), 1119.0, 1.0, "host_4k_jitter")
    approx(m99("card_bar4", "w1_lat", "memmove", 4096, 1, "p99_ns") - m99("card_bar4", "w1_lat", "memmove", 4096, 1, "p50_ns"), 163.0, 1.0, "bar4_4k_jitter")
    approx(m99("card_bar4", "rate", "nop", 0, 96, "mops_s"), 23.01, 0.01, "bar4_nop96")
    approx(m99("card_bar4", "rate", "memmove", 4096, 96, "gb_s"), 15.31, 0.02, "bar4_4k96")
    approx(m99("host_memzone", "rate", "memmove", 4096, 96, "gb_s"), 29.98, 0.02, "host_4k96")

    # --- Stage 3 (MRV-106 + MRV-107 refinements) ---
    assert sum(r.get("lost", 0) + r.get("bad", 0) + r.get("dyn_lost", 0) + r.get("dyn_bad", 0) for r in r106) == 0

    def m106(ct, mode, B, field):
        return med([r[field] for r in r106 if r["comp_target"] == ct and r["mode"] == mode and r["batch_size"] == B])

    for B, exp_gb in [(1, 15.28), (2, 23.35), (4, 31.13), (8, 31.25), (16, 31.32), (32, 30.57)]:
        approx(m106("card_bar4", "rate", B, "gb_s"), exp_gb, 0.02, f"bar4_B{B}")

    # Two-stage non-overlap residuals relative to tau_copy = 130.77 ns / 4 KiB
    tau_copy = m106("card_bar4", "rate", 16, "cycle_ns")
    approx(tau_copy, 130.77, 0.02, "tau_copy")
    approx(m106("card_bar4", "rate", 2, "batch_cycle_ns") - 2 * tau_copy, 89.3, 0.3, "delta_B2")
    approx(m106("card_bar4", "rate", 4, "batch_cycle_ns") - 4 * tau_copy, 3.2, 0.3, "delta_B4")
    approx(m106("card_bar4", "rate", 8, "batch_cycle_ns") - 8 * tau_copy, 2.3, 0.3, "delta_B8")
    approx(m106("card_bar4", "rate", 16, "batch_cycle_ns") - 16 * tau_copy, 0.0, 0.2, "delta_B16")

    # OLS linear fit on pure Batch B in {2,4,8,16,32} for +329 ns indirect fetch step
    bs = [2, 4, 8, 16, 32]
    ys = [m106("card_bar4", "w1_lat", B, "p50_ns") for B in bs]
    mx, my = sum(bs) / len(bs), sum(ys) / len(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(bs, ys)) / sum((x - mx) ** 2 for x in bs)
    intercept = my - slope * mx
    extrap_b1 = intercept + slope * 1.0
    step_ns = extrap_b1 - m106("card_bar4", "w1_lat", 1, "p50_ns")
    approx(step_ns, 329.4, 1.5, "batch_indirect_fetch_step_ns")

    # Symmetric loop_mean checks
    approx(m106("card_bar4", "w1_lat", 1, "loop_mean_ns") / 1000, 2.19, 0.01, "loop_bar4_B1")
    approx(m106("host_memzone", "w1_lat", 1, "loop_mean_ns") / 1000, 3.01, 0.01, "loop_host_B1")
    approx(m106("card_bar4", "w1_lat", 16, "loop_mean_ns") / 1000, 4.53, 0.01, "loop_bar4_B16")
    approx(m106("host_memzone", "w1_lat", 16, "loop_mean_ns") / 1000, 5.74, 0.01, "loop_host_B16")

    print(f"ALL ENGLISH FACTS VERIFIED OK (244 raw runs; Batch indirect step = +{step_ns:.1f} ns)")


if __name__ == "__main__":
    main()
