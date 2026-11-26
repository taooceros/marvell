#!/usr/bin/env python3
"""MRV-217 Figure 2: E2 real 100GbE 1500 B bursts (4/16/64/128 MiB) + concurrent 32 MiB KV
directly answering OSDI Reviewer #1 on BP=1 (D=256/512/2048) vs Large SDP vs CreditRing.

100% dynamically computed from `logs/mrv217-2026-11-26/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "logs" / "mrv217-2026-11-26" / "runs.jsonl"

BURSTS = ["4MiB", "16MiB", "64MiB", "128MiB"]
BURST_LABELS = ["4 MiB", "16 MiB", "64 MiB", "128 MiB"]


def med(xs):
    return statistics.median(xs)


def load_verified():
    by_tag = defaultdict(list)
    for ln in RUNS.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r["part"] != "V3_E2":
            continue
        assert r["accepted"] is True
        assert r["csr_pre"] == r["csr_post"]
        assert r["host_pmu"]["min_pct"] >= 99.99
        kv = r["kv"]
        assert kv["bad"] == 0 and kv["page_faults"] == 0 and r["host_pmu"]["kv"]["page_faults"] == 0
        if r["arm"] != "kv-solo":
            assert r["bad"] == 0 and r["torn"] == 0 and r["host_pmu"]["rx"]["page_faults"] == 0
            assert r["card_pmu"]["min_pct"] >= 99.99
            assert r["loss_ledger"]["residual_unaccounted"] == 0
            assert r["card_stats"]["eth"]["imissed"] == 0 and r["card_stats"]["fifo_drops"] == 0
            assert r["mercury"]["phy"]["tx_pause"] == 0 and r["mercury"]["phy"]["rx_pause"] == 0
        by_tag[r["tag"]].append(r)
    return by_tag


def main():
    by_tag = load_verified()
    solo_rows = by_tag["kv_solo"]
    assert len(solo_rows) == 3
    solo_p99 = med([r["kv"]["p99_ns"] for r in solo_rows])
    solo_p999 = med([r["kv"]["p999_ns"] for r in solo_rows])

    b0_drops = [med([r["drop_pct"] for r in by_tag[f"{b}_bp0_d256"]]) for b in BURSTS]
    arms = [
        ("large_mono",  "大环 Large SDP (D=8192, FIFO)", "#dc2626", "o", "-", 2.0),
        ("bp0_d256",    f"BP=0 默认小环 (D=256, 丢包{min(b0_drops):.0f}–{max(b0_drops):.0f}%)", "#94a3b8", "^", ":", 1.6),
        ("bp1_d2048",   "BP=1 默认最小环 (D=2048)",      "#d97706", "s", "--", 2.0),
        ("bp1_d512",    "BP=1 + 卡FIFO (D=512, WMARK=128)", "#0369a1", "v", "-.", 2.1),
        ("bp1_d256",    "BP=1 + 卡FIFO (D=256, WMARK=64)",  "#0284c7", "P", "-", 2.3),
        ("credit_ring", "CreditRing (D=512, BAR4 R=256)", "#15803d", "D", "-", 2.5),
    ]

    for a, _, _, _, _, _ in arms:
        for b in BURSTS:
            assert len(by_tag[f"{b}_{a}"]) == 3, f"expected n=3 for {b}_{a}"

    x = np.arange(len(BURSTS))
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(17.6, 5.7), gridspec_kw={"width_ratios": [1.15, 1.05, 1.05]})

    # ---- Panel A: Concurrent 32 MiB KV p99 (solid) + 128 MiB p999 callout ----
    ax1.axhline(solo_p99, color="#334155", linestyle=":", linewidth=1.3, zorder=1)
    ax1.text(0.03, solo_p99 - 4.0, f"空载基线 p99={solo_p99:.0f} ns (p999={solo_p999:.0f} ns)", fontsize=8.3, color="#334155", va="top")

    for key, lbl, col, mk, ls, lw in arms:
        ys = [med([r["kv"]["p99_ns"] for r in by_tag[f"{b}_{key}"]]) for b in BURSTS]
        lo = [y - min(r["kv"]["p99_ns"] for r in by_tag[f"{b}_{key}"]) for y, b in zip(ys, BURSTS)]
        hi = [max(r["kv"]["p99_ns"] for r in by_tag[f"{b}_{key}"]) - y for y, b in zip(ys, BURSTS)]
        ax1.errorbar(x, ys, yerr=[lo, hi], label=lbl, color=col, marker=mk, linestyle=ls, linewidth=lw, markersize=5.8, capsize=2.5, zorder=5 if key in ("credit_ring", "bp1_d256") else 3)

    lm_128_99 = med([r["kv"]["p99_ns"] for r in by_tag["128MiB_large_mono"]])
    lm_128_999 = med([r["kv"]["p999_ns"] for r in by_tag["128MiB_large_mono"]])
    b2048_128_99 = med([r["kv"]["p99_ns"] for r in by_tag["128MiB_bp1_d2048"]])
    b2048_128_999 = med([r["kv"]["p999_ns"] for r in by_tag["128MiB_bp1_d2048"]])
    b256_128_99 = med([r["kv"]["p99_ns"] for r in by_tag["128MiB_bp1_d256"]])
    b256_128_999 = med([r["kv"]["p999_ns"] for r in by_tag["128MiB_bp1_d256"]])
    cr_128_99 = med([r["kv"]["p99_ns"] for r in by_tag["128MiB_credit_ring"]])
    cr_128_999 = med([r["kv"]["p999_ns"] for r in by_tag["128MiB_credit_ring"]])

    ax1.annotate(
        f"Large SDP:\np99={lm_128_99:.0f} / p999={lm_128_999:.0f} ns",
        xy=(3, lm_128_99), xytext=(1.92, lm_128_99 + 20),
        fontsize=8.2, color="#b91c1c", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#fecaca", alpha=0.94),
        arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=0.95),
    )
    ax1.annotate(
        f"BP=1 D=2048:\np99={b2048_128_99:.0f} / p999={b2048_128_999:.0f} ns",
        xy=(3, b2048_128_99), xytext=(2.08, b2048_128_99 + 18),
        fontsize=8.0, color="#b45309", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#fde68a", alpha=0.94),
        arrowprops=dict(arrowstyle="->", color="#b45309", lw=0.95),
    )
    ax1.annotate(
        f"BP=1 D=256/512 与 CreditRing:\np99={b256_128_99:.0f} / p999={b256_128_999:.0f}–{cr_128_999:.0f} ns",
        xy=(3, cr_128_99), xytext=(1.42, 62.0),
        fontsize=8.0, color="#166534", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.24", fc="white", ec="#86efac", alpha=0.94),
        arrowprops=dict(arrowstyle="->", color="#166534", lw=0.95),
    )

    ax1.set_xticks(x)
    ax1.set_xticklabels(BURST_LABELS, fontsize=9.3)
    ax1.set_xlabel("单次 100GbE 突发规模（1500 B）", fontsize=9.8)
    ax1.set_ylabel("同 Socket 并发 32 MiB KV p99 延迟（ns）", fontsize=9.8)
    ax1.set_ylim(0, max(lm_128_99, b2048_128_99) + 74)
    ax1.set_title("A. 并发 32 MiB KV 尾延迟（p99 曲线 + 128M p999）", fontsize=10.3, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=7.4, framealpha=0.94)

    # ---- Panel B: Host DDIO LLC Hit Rate (%) + Drop Rate Callout ----
    for key, lbl, col, mk, ls, lw in arms:
        ys = [med([r["host_pmu"]["ddio_hit_pct"] for r in by_tag[f"{b}_{key}"]]) for b in BURSTS]
        ax2.plot(x, ys, label=lbl, color=col, marker=mk, linestyle=ls, linewidth=lw, markersize=5.8, zorder=5 if key in ("credit_ring", "bp1_d256") else 3)

    b0_drop_128 = med([r["drop_pct"] for r in by_tag["128MiB_bp0_d256"]])
    b256_ddio_128 = med([r["host_pmu"]["ddio_hit_pct"] for r in by_tag["128MiB_bp1_d256"]])
    b512_ddio_128 = med([r["host_pmu"]["ddio_hit_pct"] for r in by_tag["128MiB_bp1_d512"]])
    b2048_ddio_128 = med([r["host_pmu"]["ddio_hit_pct"] for r in by_tag["128MiB_bp1_d2048"]])
    cr_ddio_128 = med([r["host_pmu"]["ddio_hit_pct"] for r in by_tag["128MiB_credit_ring"]])
    lm_ddio_128 = med([r["host_pmu"]["ddio_hit_pct"] for r in by_tag["128MiB_large_mono"]])
    for key in ("large_mono", "bp1_d2048", "bp1_d512", "bp1_d256", "credit_ring"):
        for b in BURSTS:
            assert all(r["lost_pkts"] == 0 and r["drop_pct"] == 0.0 for r in by_tag[f"{b}_{key}"]), f"unexpected drop in {b}_{key}"
    ax2.text(1.5, 111.5, f"除 BP=0 (丢包 {b0_drop_128:.1f}%) 外，其余 5 档全突发实测严格 0.00% 丢包",
             ha="center", va="center", fontsize=8.0, color="#0f172a",
             bbox=dict(boxstyle="round,pad=0.26", fc="#f0fdf4", ec="#16a34a", lw=0.75))
    ax2.annotate(f"BP=1 D=256/512: {b256_ddio_128:.1f}% / {b512_ddio_128:.1f}%\nCreditRing: {cr_ddio_128:.1f}%",
                 xy=(3, b256_ddio_128), xytext=(0.10, 74.0),
                 fontsize=8.0, color="#0369a1", fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.24", fc="white", ec="#bae6fd", alpha=0.94),
                 arrowprops=dict(arrowstyle="->", color="#0369a1", lw=0.9))
    ax2.annotate(f"BP=1 D=2048 (默认最小环): {b2048_ddio_128:.1f}%",
                 xy=(3, b2048_ddio_128), xytext=(0.55, max(20.0, b2048_ddio_128 - 22)),
                 fontsize=8.0, color="#b45309", fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color="#b45309", lw=0.9))
    ax2.annotate(f"Large SDP: {lm_ddio_128:.1f}%",
                 xy=(3, lm_ddio_128), xytext=(1.6, 13.0),
                 fontsize=8.1, color="#b91c1c", fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=0.9))

    ax2.set_xticks(x)
    ax2.set_xticklabels(BURST_LABELS, fontsize=9.3)
    ax2.set_xlabel("单次 100GbE 突发规模（1500 B）", fontsize=9.8)
    ax2.set_ylabel("主机 CHA DDIO 命中率（%）", fontsize=9.8)
    ax2.set_ylim(0, 118)
    ax2.set_title("B. 主机 DDIO 命中率（0.00% 丢包档 vs BP=0）", fontsize=10.3, fontweight="bold")

    # ---- Panel C: 128 MiB Host IMC Traffic (GB/s) & Net PCIe SDP Drain (Gbps) ----
    c_arms = [
        ("large_mono",  "Large SDP\nD=8192",   "#dc2626"),
        ("bp1_d2048",   "BP=1\nD=2048",        "#d97706"),
        ("bp1_d512",    "BP=1\nD=512",         "#0369a1"),
        ("bp1_d256",    "BP=1\nD=256",         "#0284c7"),
        ("credit_ring", "CreditRing\nR=256",   "#15803d"),
    ]
    xc = np.arange(len(c_arms))
    imc_vals = [med([r["derived"]["host_imc_tot_gbps"] for r in by_tag[f"128MiB_{k}"]]) for k, _, _ in c_arms]
    net_vals = [
        med([r["derived"]["net_pcie_drain_wire_gbps"] for r in by_tag[f"128MiB_{k}"]])
        if by_tag[f"128MiB_{k}"][0]["derived"]["net_pcie_drain_wire_gbps"] is not None else 0.0
        for k, _, _ in c_arms
    ]

    w = 0.38
    ax3_r = ax3.twinx()
    ax3_r.spines["top"].set_visible(False)
    b1 = ax3.bar(xc - w / 2, imc_vals, width=w, color=[c for _, _, c in c_arms], edgecolor="#1e293b", linewidth=0.6, label="主机 IMC 总带宽 (GB/s, 左轴)", zorder=3)
    b2 = ax3_r.bar(xc + w / 2, net_vals, width=w, color="#cbd5e1", hatch="///", edgecolor="#334155", linewidth=0.7, label="经 PCIe SDP 净排空 (Gbps, 右轴)", zorder=3)

    ax3.set_xticks(xc)
    ax3.set_xticklabels([lbl for _, lbl, _ in c_arms], fontsize=8.4)
    ax3.set_ylabel("主机 IMC 总流量（GB/s，左轴）", fontsize=9.6, color="#1e293b")
    ax3_r.set_ylabel("经 PCIe SDP 净排空带宽（Gbps，右轴）", fontsize=9.6, color="#334155")
    ax3.set_ylim(0, max(imc_vals) * 1.48)
    ax3_r.set_ylim(0, 134)
    ax3_r.axhline(100.0, color="#0284c7", linestyle="--", linewidth=1.0, alpha=0.6)
    ax3.set_title("C. 128 MiB 突发：主机 IMC 流量 vs 经 PCIe 净排空", fontsize=10.3, fontweight="bold")

    for i, (iv, nv) in enumerate(zip(imc_vals, net_vals)):
        ax3.text(xc[i] - w / 2, iv + max(imc_vals) * 0.025, f"{iv:.1f}", ha="center", va="bottom", fontsize=7.9, fontweight="bold", color="#1e293b")
        if nv > 0:
            ax3_r.text(xc[i] + w / 2, nv + 2.5, f"{nv:.1f}G", ha="center", va="bottom", fontsize=7.7, fontweight="bold", color="#0f172a")
        else:
            ax3_r.text(xc[i] + w / 2, 4.0, "主机\nDRAM", ha="center", va="bottom", fontsize=7.2, color="#64748b")

    h1, l1 = ax3.get_legend_handles_labels()
    h2, l2 = ax3_r.get_legend_handles_labels()
    ax3.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=7.4, framealpha=0.92)

    fig.suptitle("MRV-217 验证 3（E2 实测 100GbE 1500 B 突发 + 并发 32 MiB KV）：BP=1 (D=256/512) 追平 CreditRing，默认最小环 D=2048 仍溢出 DDIO", fontsize=11.6, fontweight="bold", y=0.995)
    png = mrvplot.save(fig, "mrv-217-e2-burst-kv-ddio")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
