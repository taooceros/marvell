#!/usr/bin/env python3
"""Verify every quantitative claim in data/FACTS.md directly against raw runs.jsonl logs."""

import json
import statistics
import sys
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
    r95 = load(R95)
    r99 = load(R99)
    d99 = load(R99_DIAG)
    r106 = load(R106)

    assert len(r95) == 94, len(r95)
    assert len(r99) == 90 and len(d99) == 12, (len(r99), len(d99))
    assert len(r106) == 48, len(r106)

    # --- MRV-95 ---
    poll_p50 = med([r["p50_ns"] for r in r95 if r.get("mode") == "poll_only"])
    approx(poll_p50, 1212.0, 1.0, "dpi_read_floor_ns")

    host_nop = med([r["p50_ns"] for r in r95 if r.get("submitter") == "host_movdir64b" and r.get("mode") == "w1_lat" and r.get("op") == "nop"])
    wc_nop = med([r["p50_ns"] for r in r95 if r.get("submitter") == "card_pem_wc_none" and r.get("mode") == "w1_lat" and r.get("op") == "nop"])
    dpi_nop = med([r["p50_ns"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "w1_lat" and r.get("op") == "nop"])
    approx(host_nop / 1000, 0.40, 0.01, "host_nop_us")
    approx(wc_nop / 1000, 1.30, 0.01, "wc_nop_us")
    approx(dpi_nop / 1000, 1.98, 0.01, "dpi_nop_us")

    for sub, exp_bad, exp_tot, exp_pct in [
        ("card_pem_wc_dsb", 28, 2300, 1.217),
        ("card_pem_wc_dgh", 14, 2300, 0.609),
        ("card_pem_wc_none", 12, 24900, 0.048),
        ("card_dpi_dma", 0, 24900, 0.000),
    ]:
        rs = [r for r in r95 if r.get("submitter") == sub and r.get("mode") == "w1_lat"]
        bad = sum(r["bad"] for r in rs)
        tot = sum(r["rounds"] for r in rs)
        assert bad == exp_bad and tot == exp_tot, (sub, bad, tot)
        approx(100.0 * bad / tot, exp_pct, 0.002, f"drop_{sub}")

    dpi_rate_ops = sum(r["ops"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "rate")
    assert dpi_rate_ops == 182913304, dpi_rate_ops
    assert sum(r["lost"] + r["bad"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "rate") == 0

    nop96 = med([r["mops_s"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "rate" and r.get("op") == "nop" and r.get("window") == 96])
    mm96 = med([r["gb_s"] for r in r95 if r.get("submitter") == "card_dpi_dma" and r.get("mode") == "rate" and r.get("op") == "memmove" and r.get("window") == 96])
    approx(nop96, 14.23, 0.01, "mrv95_nop96")
    approx(mm96, 29.53, 0.01, "mrv95_mm96_gbs")

    # --- MRV-99 ---
    assert sum(r.get("lost", 0) + r.get("bad", 0) for r in r99 + d99) == 0

    def m99(ct, mode, op, sz, w=1, field="p50_ns"):
        rs = [r for r in r99 if r["comp_target"] == ct and r["mode"] == mode and r["op"] == op and r["size"] == sz and r.get("window", 1) == w]
        return med([r[field] for r in rs])

    approx(1000.0 / m99("host_memzone", "rate", "nop", 0, 1, "mops_s") / 1000, 1.96, 0.01, "mrv99_host_nop_w1")
    approx(1000.0 / m99("card_bar4", "rate", "nop", 0, 1, "mops_s") / 1000, 1.27, 0.01, "mrv99_bar4_nop_w1")
    approx(1000.0 / m99("host_memzone", "rate", "memmove", 4096, 1, "mops_s") / 1000, 3.13, 0.01, "mrv99_host_4k_w1")
    approx(1000.0 / m99("card_bar4", "rate", "memmove", 4096, 1, "mops_s") / 1000, 2.17, 0.01, "mrv99_bar4_4k_w1")

    h4k_j = m99("host_memzone", "w1_lat", "memmove", 4096, 1, "p99_ns") - m99("host_memzone", "w1_lat", "memmove", 4096, 1, "p50_ns")
    c4k_j = m99("card_bar4", "w1_lat", "memmove", 4096, 1, "p99_ns") - m99("card_bar4", "w1_lat", "memmove", 4096, 1, "p50_ns")
    approx(h4k_j, 1119.0, 1.0, "mrv99_host_4k_jitter")
    approx(c4k_j, 163.0, 1.0, "mrv99_bar4_4k_jitter")

    approx(m99("card_bar4", "rate", "nop", 0, 96, "mops_s"), 23.01, 0.01, "mrv99_bar4_nop96")
    approx(m99("card_bar4", "rate", "memmove", 4096, 96, "mops_s"), 3.74, 0.01, "mrv99_bar4_4k96_mops")
    approx(m99("card_bar4", "rate", "memmove", 4096, 96, "gb_s"), 15.31, 0.02, "mrv99_bar4_4k96_gbs")
    approx(m99("host_memzone", "rate", "memmove", 4096, 96, "gb_s"), 29.98, 0.02, "mrv99_host_4k96_gbs")

    for mb in [1, 8, 16]:
        rs = [r for r in d99 if r["comp_target"] == "card_bar4" and r["op"] == "memmove" and r["min_batch"] == mb]
        approx(rs[0]["mops_s"], 3.73, 0.01, f"diag_mb_{mb}")

    # --- MRV-106 ---
    assert sum(r.get("lost", 0) + r.get("bad", 0) + r.get("dyn_lost", 0) + r.get("dyn_bad", 0) for r in r106) == 0

    def m106(ct, mode, B, field):
        rs = [r for r in r106 if r["comp_target"] == ct and r["mode"] == mode and r["batch_size"] == B]
        return med([r[field] for r in rs])

    for B, exp_gb in [(1, 15.28), (2, 23.35), (4, 31.13), (8, 31.25), (16, 31.32), (32, 30.57)]:
        approx(m106("card_bar4", "rate", B, "gb_s"), exp_gb, 0.02, f"mrv106_bar4_B{B}_gbs")

    approx(m106("host_memzone", "rate", 16, "gb_s"), 31.10, 0.02, "mrv106_host_B16_gbs")
    approx(m106("card_bar4", "w1_lat", 4, "p50_ns") / 1000, 2.84, 0.01, "mrv106_bar4_B4_p50")
    approx(m106("card_bar4", "w1_lat", 16, "p50_ns") / 1000, 4.42, 0.01, "mrv106_bar4_B16_p50")
    approx(m106("card_bar4", "w1_lat", 16, "p99_ns") / 1000, 4.61, 0.01, "mrv106_bar4_B16_p99")
    approx(m106("host_memzone", "w1_lat", 16, "p50_ns") / 1000, 4.63, 0.01, "mrv106_host_B16_p50")
    approx(m106("host_memzone", "w1_lat", 16, "p99_ns") / 1000, 5.75, 0.01, "mrv106_host_B16_p99")

    jitters = [m106("card_bar4", "w1_lat", B, "p99_ns") - m106("card_bar4", "w1_lat", B, "p50_ns") for B in [1, 2, 4, 8, 16, 32]]
    approx(min(jitters), 133.0, 1.0, "mrv106_min_jitter")
    approx(max(jitters), 220.0, 1.0, "mrv106_max_jitter")

    print("ALL FACTS VERIFIED OK (94 + 102 + 48 = 244 raw runs checked)")


if __name__ == "__main__":
    main()
