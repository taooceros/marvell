"""MRV-209 Figure 1 (3-second plain-Chinese view): End-to-end 100GbE open-loop p99 latency
and packet drop rate across 4 single-variable policies over unified standard SDP.

100% dynamically computed from `logs/mrv209-2026-11-26/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv209-2026-11-26/runs.jsonl"

POLS = [
    ("large_sdp", "Large SDP（2048 环 · 大池）", "#64748b"),
    ("small_sdp_drop", "Small SDP（256 环 · 无卡缓冲）", "#dc2626"),
    ("ceio_style", "CEIO-style（512 环 · 反应切道）", "#d97706"),
    ("credit_sdp", "CreditSDP 本文（512 环 · 无 SFENCE 信用）", "#1a7f37"),
]


def med(xs):
    return statistics.median(xs)


def assert_verified(r):
    assert r.get("verified") is True and not r.get("errors"), f"unverified: {r.get('run_id')}"
    c, h, k = r["client"], r["host"], r["card"]
    p = h["pmu"]
    assert c["integrity_ok"] and c["bad"] == 0 and c["proto_err"] == 0 and c["val_err"] == 0 and c["torn"] == 0
    if r["policy"] in ("large_sdp", "credit_sdp"):
        assert c["lost"] == 0
    assert h["bad"] == 0 and h["torn"] == 0 and h["page_faults"] == 0 and p["page_faults"] == 0
    assert p["min_running_pct"] >= 99.5 and k["bad_wire"] == 0 and k["bad_host"] == 0 and k["torn_bar4"] == 0


def load_cells():
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    cells = defaultdict(list)
    for r in rows:
        assert_verified(r)
        key = (
            r["section"], r["workload"], r["drive_mode"],
            int(r["rate_krps"]), r["incast_pkts"], r["num_flows"], r["policy"],
        )
        cells[key].append(r)
    return cells


def draw_panel(ax, cells, specs, title, y_headroom=1.28):
    x = np.arange(len(specs))
    w = 0.20
    vals_by_pol, drops_by_pol = {}, {}
    all_max = 0.0
    for pol, _, _ in POLS:
        p99s, drops = [], []
        for s in specs:
            rs = cells[s[0] + (pol,)]
            assert len(rs) == 3
            p99s.append(med([r["client"]["tot_p99_us"] for r in rs]))
            drops.append(med([r["client"]["drop_pct"] for r in rs]))
        vals_by_pol[pol] = p99s
        drops_by_pol[pol] = drops
        all_max = max(all_max, max(p99s))

    for pi, (pol, label, color) in enumerate(POLS):
        p99s, drops = vals_by_pol[pol], drops_by_pol[pol]
        bars = ax.bar(x + (pi - 1.5) * w, p99s, width=w, color=color, edgecolor="white", linewidth=0.9, label=label)
        for i, b in enumerate(bars):
            y = b.get_height()
            d = drops[i]
            vtxt = f"{y:.0f}" if y >= 50 else f"{y:.1f}"
            if d >= 0.005:
                d_str = f"{d:.1f}%" if d >= 0.1 else f"{d:.2f}%"
                txt = f"{vtxt}\n丢{d_str}"
                tc = "#b91c1c" if pol == "small_sdp_drop" else "#b45309"
                fw = "bold"
            else:
                txt = vtxt
                tc = "#1a7f37" if pol == "credit_sdp" else "#334155"
                fw = "bold" if pol == "credit_sdp" else "normal"
            ax.text(
                b.get_x() + b.get_width() / 2, y + all_max * 0.018, txt,
                ha="center", va="bottom", fontsize=8.4, color=tc, fontweight=fw, linespacing=1.08,
            )

    for i in range(len(specs)):
        ls, cs = vals_by_pol["large_sdp"][i], vals_by_pol["credit_sdp"][i]
        pct = (cs - ls) / ls * 100.0
        if pct <= -10.0:
            ymax_i = max(vals_by_pol[p][i] for p, _, _ in POLS)
            ax.text(
                x[i], ymax_i + all_max * 0.115, f"CreditSDP vs 大环 {pct:.0f}%",
                ha="center", va="bottom", fontsize=8.6, fontweight="bold", color="#1a7f37",
            )

    ax.set_xticks(x)
    ax.set_xticklabels([s[1] for s in specs], fontsize=9.6)
    ax.set_ylabel("端到端开环 p99 延迟（µs，越低越好）", fontsize=10.0)
    ax.set_ylim(0, all_max * y_headroom)
    ax.set_title(title, fontsize=10.8, fontweight="bold", pad=9)


def main():
    cells = load_cells()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.4, 5.4))

    mc_specs = [
        (("sec1_mc_microburst", "etc_mix", "poisson_burst", 600, 0, 256), "ETC 混合\n微突发 3.5M"),
        (("sec1_mc_microburst", "websearch_mc", "poisson_burst", 600, 0, 256), "WebSearch\n微突发 3.5M"),
        (("sec2_mc_incast", "etc_mix", "incast", 400, 512, 1024), "ETC 混合\n512包·1K流 Incast"),
    ]
    nfv_specs = [
        (("sec3_nfv_burst_incast", "nfv_nat_256", "poisson_burst", 1200, 0, 256), "NAT 256B\n微突发 6M"),
        (("sec3_nfv_burst_incast", "nfv_lb_256", "incast", 800, 512, 512), "Maglev LB\n512包 Incast"),
        (("sec3_nfv_burst_incast", "nfv_websearch", "incast", 800, 512, 512), "NFV WebSearch\n512包 Incast"),
    ]

    draw_panel(ax1, cells, mc_specs, "(a) 原版 Memcached 1.6.45：微突发 p99 砍半且 0 丢包", y_headroom=1.28)
    draw_panel(ax2, cells, nfv_specs, "(b) 有状态 5 元组 NFV：0 丢包且无 CEIO 切道恶化", y_headroom=1.22)

    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(
        handles, labels, loc="upper center", ncol=4, fontsize=8.8,
        frameon=True, framealpha=0.95, bbox_to_anchor=(0.5, 0.94),
    )
    fig.suptitle(
        "突发与 Incast 下，只有 CreditSDP 同时做到 0 丢包 + 最低尾延迟（统一标准 SDP，n = 3 中位数）",
        fontsize=11.6, fontweight="bold", y=0.99,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.865])
    png = mrvplot.HERE / "mrv-209-e2e-tail-drop.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-209-e2e-tail-drop.svg")
    plt.close(fig)
    print("Saved:", png)


if __name__ == "__main__":
    main()
