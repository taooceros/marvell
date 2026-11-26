"""MRV-209 Figure 2 (3-second plain-Chinese view): Compact-ring DDIO & IMC DRAM savings
and co-located 32 MiB tenant tail latency (honestly showing NFV 1.5M ~1% LLC-miss step).

100% dynamically computed from `logs/mrv209-2026-11-26/runs.jsonl` and `env.json`.
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
ENV_PATH = ROOT / "logs/mrv209-2026-11-26/env.json"

POLS = [
    ("large_sdp", "Large SDP（2048 环）", "#64748b"),
    ("small_sdp_drop", "Small SDP（256 环 · 会丢包）", "#dc2626"),
    ("ceio_style", "CEIO-style（512 环 · 反应切道）", "#d97706"),
    ("credit_sdp", "CreditSDP 本文（512 环 · 0 丢包）", "#1a7f37"),
]


def med(xs):
    return statistics.median(xs)


def main():
    env = json.loads(ENV_PATH.read_text())
    solo_p99 = env["solo_tenant"]["tenant"]["p99_ns"]
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    cells = defaultdict(list)
    for r in rows:
        assert r.get("verified") is True and not r.get("errors")
        key = (
            r["section"], r["workload"], r["drive_mode"],
            int(r["rate_krps"]), r["incast_pkts"], r["num_flows"], r["policy"],
        )
        cells[key].append(r)

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16.0, 5.4), gridspec_kw={"width_ratios": [1.0, 1.0, 1.18]})
    w = 0.20

    # (a) DDIO hit rate (%) — 3 decisive workloads
    ddio_specs = [
        (("sec1_mc_microburst", "etc_mix", "poisson_burst", 600, 0, 256), "MC ETC\n微突发"),
        (("sec3_nfv_burst_incast", "nfv_nat_1024", "poisson_burst", 800, 0, 256), "NFV 1KiB\n4M 微突发"),
        (("sec3_nfv_burst_incast", "nfv_websearch", "incast", 800, 512, 512), "NFV WS\n512 Incast"),
    ]
    x3 = np.arange(len(ddio_specs))
    for pi, (pol, label, color) in enumerate(POLS):
        vals = [med([r["host"]["pmu"]["ddio_hit_pct"] for r in cells[s[0] + (pol,)]]) for s in ddio_specs]
        bars = ax1.bar(x3 + (pi - 1.5) * w, vals, width=w, color=color, edgecolor="white", label=label)
        if pol in ("large_sdp", "credit_sdp"):
            for b in bars:
                y = b.get_height()
                ax1.text(
                    b.get_x() + b.get_width() / 2, y + 1.2, f"{y:.1f}%",
                    ha="center", va="bottom", fontsize=8.2,
                    fontweight="bold" if pol == "credit_sdp" else "normal",
                    color="#1a7f37" if pol == "credit_sdp" else "#475569",
                )
    ax1.set_xticks(x3)
    ax1.set_xticklabels([s[1] for s in ddio_specs], fontsize=9.2)
    ax1.set_ylim(25, 111)
    ax1.set_ylabel("Socket-1 DDIO 写命中率（%）", fontsize=9.8)
    ax1.set_title("(a) 紧致小环把 DDIO 稳在 96%–99%", fontsize=10.5, fontweight="bold", pad=8)

    # (b) Host IMC DRAM bandwidth (GB/s) — 3 decisive workloads
    imc_specs = [
        (("sec4_poisson_and_flows", "etc_mix", "poisson", 1400, 0, 256), "MC ETC\n1.4M 稳态"),
        (("sec1_mc_microburst", "etc_mix", "poisson_burst", 600, 0, 256), "MC ETC\n微突发"),
        (("sec3_nfv_burst_incast", "nfv_nat_1024", "poisson_burst", 800, 0, 256), "NFV 1KiB\n4M 微突发"),
    ]
    imc_vals = {}
    imc_max = 0.0
    for pol, _, _ in POLS:
        vs = [med([r["host"]["pmu"]["imc_gbps"] for r in cells[s[0] + (pol,)]]) for s in imc_specs]
        imc_vals[pol] = vs
        imc_max = max(imc_max, max(vs))
    for pi, (pol, label, color) in enumerate(POLS):
        bars = ax2.bar(x3 + (pi - 1.5) * w, imc_vals[pol], width=w, color=color, edgecolor="white")
        if pol in ("large_sdp", "credit_sdp"):
            for b in bars:
                y = b.get_height()
                ax2.text(
                    b.get_x() + b.get_width() / 2, y + imc_max * 0.018, f"{y:.2f}",
                    ha="center", va="bottom", fontsize=8.2,
                    fontweight="bold" if pol == "credit_sdp" else "normal",
                    color="#1a7f37" if pol == "credit_sdp" else "#475569",
                )
    for i in range(len(imc_specs)):
        pct = (imc_vals["credit_sdp"][i] - imc_vals["large_sdp"][i]) / imc_vals["large_sdp"][i] * 100.0
        ymax_i = max(imc_vals[p][i] for p, _, _ in POLS)
        ax2.text(
            x3[i], ymax_i + imc_max * 0.11, f"{pct:.0f}%",
            ha="center", va="bottom", fontsize=9.0, fontweight="bold", color="#1a7f37",
        )
    ax2.set_xticks(x3)
    ax2.set_xticklabels([s[1] for s in imc_specs], fontsize=9.2)
    ax2.set_ylim(0, imc_max * 1.26)
    ax2.set_ylabel("Socket-1 IMC 内存读写带宽（GB/s）", fontsize=9.8)
    ax2.set_title("(b) 主机 DDR5 带宽降 64%–83%", fontsize=10.5, fontweight="bold", pad=8)

    # (c) Co-located 32 MiB tenant p99 latency (ns) — includes NFV 1.5M honest caveat!
    ten_specs = [
        (("sec4_poisson_and_flows", "etc_mix", "poisson", 1400, 0, 256), "MC ETC\n1.4M 稳态"),
        (("sec1_mc_microburst", "etc_mix", "poisson_burst", 600, 0, 256), "MC ETC\n微突发"),
        (("sec3_nfv_burst_incast", "nfv_nat_256", "poisson_burst", 1200, 0, 256), "NFV NAT\n6M 微突发"),
        (("sec4_poisson_and_flows", "nfv_nat_256", "poisson", 1500, 0, 4096), "NFV NAT\n1.5M 稳态*"),
    ]
    x4 = np.arange(len(ten_specs))
    ten_vals = {}
    ten_max = 0.0
    for pol, _, _ in POLS:
        vs = [med([r["host"]["tenant"]["p99_ns"] for r in cells[s[0] + (pol,)]]) for s in ten_specs]
        ten_vals[pol] = vs
        ten_max = max(ten_max, max(vs))
    for pi, (pol, label, color) in enumerate(POLS):
        bars = ax3.bar(x4 + (pi - 1.5) * w, ten_vals[pol], width=w, color=color, edgecolor="white")
        for i, b in enumerate(bars):
            if pol in ("large_sdp", "credit_sdp") or (i == 3 and pol == "ceio_style"):
                y = b.get_height()
                ax3.text(
                    b.get_x() + b.get_width() / 2, y + 3.5, f"{y:.0f}",
                    ha="center", va="bottom", fontsize=8.0,
                    fontweight="bold" if pol == "credit_sdp" else "normal",
                    color="#1a7f37" if pol == "credit_sdp" else ("#b45309" if pol == "ceio_style" else "#475569"),
                )
    ax3.axhline(solo_p99, color="#0f172a", linestyle="--", linewidth=1.3, label=f"租户独跑基线 ({solo_p99:.1f} ns)")
    for i in range(3):
        ls, cs = ten_vals["large_sdp"][i], ten_vals["credit_sdp"][i]
        pct = (cs - ls) / ls * 100.0
        ymax_i = max(ten_vals[p][i] for p, _, _ in POLS)
        ax3.text(
            x4[i], ymax_i + 20.0, f"{pct:.0f}%",
            ha="center", va="bottom", fontsize=8.8, fontweight="bold", color="#1a7f37",
        )
    ax3.set_xticks(x4)
    ax3.set_xticklabels([s[1] for s in ten_specs], fontsize=9.0)
    ax3.set_ylim(0, ten_max * 1.25)
    ax3.set_ylabel("同 Socket 共置 32 MiB 租户 p99（ns）", fontsize=9.8)
    ax3.set_title("(c) 共置租户尾延迟（*含 1% 缺失台阶波动）", fontsize=10.5, fontweight="bold", pad=8)
    ax3.legend(loc="upper right", fontsize=8.4, frameon=True, framealpha=0.92)

    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(
        handles, labels, loc="upper center", ncol=4, fontsize=8.8,
        frameon=True, framealpha=0.95, bbox_to_anchor=(0.5, 0.94),
    )
    fig.suptitle(
        "紧致热环把主机内存带宽砍掉 64%–83% 并保护 DDIO 与共置租户（三档小环缓存收益相当，唯 CreditSDP 0 丢包）",
        fontsize=11.2, fontweight="bold", y=0.99,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.865])
    png = mrvplot.HERE / "mrv-209-ddio-tenant-imc.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-209-ddio-tenant-imc.svg")
    plt.close(fig)
    print("Saved:", png)


if __name__ == "__main__":
    main()
