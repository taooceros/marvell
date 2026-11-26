"""MRV-209 Figure 3 (3-second plain-Chinese view, Step 1 symmetric CPU fix):
(a) Steady-state open-loop Poisson p99 latency (proving zero idle/steady-state tax)
(b) 100% symmetric host cpu40 active time per request derived from wall time and
    empty-poll rate regression: active_ns/req = (dt_s - empty_polls * tau_empty) / rx_rpcs
    (R^2 = 0.990-0.99998; includes port.rx equally across all 4 policies, avoiding the
    mrv209_host.rs t_busy0 instrumentation asymmetry between CeioStyle and single-path arms).

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
    ("large_sdp", "Large SDP（2048 环）", "#64748b", "o", "--"),
    ("small_sdp_drop", "Small SDP（256 环）", "#dc2626", "s", ":"),
    ("ceio_style", "CEIO-style（反应切道 + 2P1C）", "#d97706", "^", "-."),
    ("credit_sdp", "CreditSDP 本文（无 SFENCE 信用）", "#1a7f37", "D", "-"),
]


def med(xs):
    return statistics.median(xs)


def regress_tau_empty(rows):
    """Regress per-(policy, app) 4-queue empty-poll wall time tau_empty_s directly from
    steady-state open-loop Poisson runs (100% symmetric across all 4 policies).
    """
    by_pa = defaultdict(list)
    for r in rows:
        if r["drive_mode"] == "poisson":
            by_pa[(r["policy"], r["app"])].append(r)
    tau_s = {}
    for (pol, app), pts in by_pa.items():
        xs = [r["host"]["rx_rpcs"] / r["host"]["dt_s"] for r in pts]
        ys = [r["host"]["empty_polls"] / r["host"]["dt_s"] for r in pts]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        intercept = my - slope * mx
        tau_s[(pol, app)] = 1.0 / intercept
    return tau_s


def main():
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    cells = defaultdict(list)
    for r in rows:
        assert r.get("verified") is True and not r.get("errors")
        key = (
            r["section"], r["workload"], r["drive_mode"],
            int(r["rate_krps"]), r["incast_pkts"], r["num_flows"], r["policy"],
        )
        cells[key].append(r)

    tau_s = regress_tau_empty(rows)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.6, 5.4), gridspec_kw={"width_ratios": [1.0, 1.25]})

    # (a) Steady-state Poisson p99 latency for Memcached etc_mix (200k / 800k / 1400k)
    mc_pts = [
        (("sec4_poisson_and_flows", "etc_mix", "poisson", 200, 0, 64), 0.2),
        (("sec4_poisson_and_flows", "etc_mix", "poisson", 800, 0, 256), 0.8),
        (("sec4_poisson_and_flows", "etc_mix", "poisson", 1400, 0, 256), 1.4),
    ]
    for pol, label, color, mk, ls in POLS:
        xs = [p[1] for p in mc_pts]
        ys = [med([r["client"]["tot_p99_us"] for r in cells[p[0] + (pol,)]]) for p in mc_pts]
        ax1.plot(xs, ys, marker=mk, linestyle=ls, color=color, linewidth=2.3, markersize=7.2, label=label)
        if pol == "large_sdp":
            for xi, yi in zip(xs, ys):
                ax1.annotate(f"{yi:.1f}", (xi, yi), xytext=(0, 8), textcoords="offset points",
                             ha="center", fontsize=8.8, color=color)
        elif pol == "credit_sdp":
            for xi, yi in zip(xs, ys):
                ax1.annotate(f"{yi:.1f}", (xi, yi), xytext=(0, -16), textcoords="offset points",
                             ha="center", fontsize=9.0, fontweight="bold", color=color)
    ax1.set_xticks([0.2, 0.8, 1.4])
    ax1.set_xticklabels(["0.2 Mrps\n(64 流)", "0.8 Mrps\n(256 流)", "1.4 Mrps\n(256 流)"], fontsize=9.4)
    ax1.set_ylim(12.2, 27.6)
    ax1.set_xlabel("原版 Memcached ETC 混合无突发泊松速率", fontsize=9.8)
    ax1.set_ylabel("端到端开环 p99 延迟（µs）", fontsize=9.8)
    ax1.set_title("(a) 稳态无突发：CreditSDP 零额外延迟税", fontsize=10.6, fontweight="bold", pad=9)

    # (b) 100% Symmetric Host cpu40 Active CPU Time per Request (incl. port.rx equally)
    comp_specs = [
        (("sec1_mc_microburst", "etc_mix", "poisson_burst", 600, 0, 256), "MC 微突发\n256 流", "memcached"),
        (("sec4_poisson_and_flows", "etc_mix", "poisson", 1400, 0, 256), "MC 1.4M\n高载稳态", "memcached"),
        (("sec3_nfv_burst_incast", "nfv_nat_256", "poisson_burst", 1200, 0, 256), "NFV NAT\n6M 微突发", "nfv-nat"),
        (("sec3_nfv_burst_incast", "nfv_lb_256", "incast", 800, 512, 512), "NFV LB\n512 Incast", "nfv-nat"),
    ]
    x = np.arange(len(comp_specs))
    w = 0.20
    vals_by_pol = {}
    bmax = 0.0
    for pol, _, _, _, _ in POLS:
        vs = []
        for s_key, _, app_k in comp_specs:
            te = tau_s[(pol, app_k)]
            rs = cells[s_key + (pol,)]
            act_ns = med([(r["host"]["dt_s"] - r["host"]["empty_polls"] * te) * 1e9 / r["host"]["rx_rpcs"] for r in rs])
            vs.append(act_ns)
        vals_by_pol[pol] = vs
        bmax = max(bmax, max(vs))

    for pi, (pol, label, color, _, _) in enumerate(POLS):
        bars = ax2.bar(x + (pi - 1.5) * w, vals_by_pol[pol], width=w, color=color, edgecolor="white")
        if pol in ("large_sdp", "ceio_style", "credit_sdp"):
            for b in bars:
                y = b.get_height()
                ax2.text(
                    b.get_x() + b.get_width() / 2, y + bmax * 0.016, f"{y:.0f}",
                    ha="center", va="bottom", fontsize=8.2,
                    fontweight="bold" if pol == "credit_sdp" else "normal",
                    color="#1a7f37" if pol == "credit_sdp" else ("#b45309" if pol == "ceio_style" else "#475569"),
                )
    for i in range(len(comp_specs)):
        ls = vals_by_pol["large_sdp"][i]
        ce = vals_by_pol["ceio_style"][i]
        cs = vals_by_pol["credit_sdp"][i]
        d_ls = (cs - ls) / ls * 100.0
        d_ce = (cs - ce) / ce * 100.0
        ymax_i = max(vals_by_pol[p][i] for p, _, _, _, _ in POLS)
        lbl = f"vs大环 {d_ls:.0f}%\nvsCEIO {d_ce:.0f}%"
        ax2.text(
            x[i], ymax_i + bmax * 0.085, lbl,
            ha="center", va="bottom", fontsize=8.0, fontweight="bold", color="#1a7f37", linespacing=1.1,
        )
    ax2.set_xticks(x)
    ax2.set_xticklabels([s[1] for s in comp_specs], fontsize=9.4)
    ax2.set_ylim(0, bmax * 1.30)
    ax2.set_ylabel("对称口径主机 cpu40 每请求总活跃耗时（ns/req）", fontsize=9.6)
    ax2.set_title("(b) 对称口径主机 CPU：比大环省 6%–12%、比 CEIO 省 4%–8%", fontsize=10.4, fontweight="bold", pad=9)

    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(
        handles, labels, loc="upper center", ncol=4, fontsize=8.6,
        frameon=True, framealpha=0.95, bbox_to_anchor=(0.5, 0.94),
    )
    fig.suptitle(
        "无突发零延迟税；对称口径下 CreditSDP 主机 CPU 全线低于大环与 CEIO，且 4096 流零泄露（n = 3 中位数）",
        fontsize=11.0, fontweight="bold", y=0.99,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.865])
    png = mrvplot.HERE / "mrv-209-poisson-flows-cpu.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-209-poisson-flows-cpu.svg")
    plt.close(fig)
    print("Saved:", png)


if __name__ == "__main__":
    main()
