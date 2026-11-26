#!/usr/bin/env python3
"""Figure 6 (E5 / MRV-209): Unmodified upstream Memcached 1.6.45 + stateful NFV + co-located 32 MiB tenant."""
import json
import statistics
from collections import defaultdict
import numpy as np
import matplotlib.pyplot as plt
from paper_style import (
    LOGS_DIR, apply_style, clean_ax, save_fig,
    C_CREDIT, C_LARGE, C_CEIO, C_SMALL,
)

apply_style()


def med(xs):
    return statistics.median(xs)


def load_runs():
    rows = []
    with open(LOGS_DIR / "mrv209-2026-11-26" / "runs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            assert r["verified"] and r["client"]["bad"] == 0 and r["client"]["torn"] == 0
            rows.append(r)
    return rows


def regress_tau_empty(rows):
    by_pa = defaultdict(list)
    for r in rows:
        if r["drive_mode"] == "poisson":
            by_pa[(r["policy"], r["app"])].append(r)
    tau_s = {}
    for (pol, app), pts in by_pa.items():
        xs = [r["host"]["rx_rpcs"] / r["host"]["dt_s"] for r in pts]
        ys = [r["host"]["empty_polls"] / r["host"]["dt_s"] for r in pts]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        intercept = my - slope * mx
        tau_s[(pol, app)] = 1.0 / intercept
    return tau_s


def main():
    rows = load_runs()
    cells = defaultdict(list)
    for r in rows:
        k = (r["section"], r["workload"], r["drive_mode"],
             int(r["rate_krps"]), r["incast_pkts"], r["num_flows"], r["policy"])
        cells[k].append(r)
    tau_s = regress_tau_empty(rows)

    pols = [
        ("large_sdp",      "Large SDP",         C_LARGE),
        ("small_sdp_drop", "Small SDP (drop)",  C_SMALL),
        ("ceio_style",     "CEIO-style",        C_CEIO),
        ("credit_sdp",     "CreditRing (ours)", C_CREDIT),
    ]

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(11.1, 3.52), gridspec_kw={"width_ratios": [1.14, 1.06, 1.12], "wspace": 0.34}
    )

    # =========================================================================
    # (a) Burst / Incast Open-Loop p99 Tail Latency & Drop Rate
    # =========================================================================
    clean_ax(ax1)
    a_specs = [
        (("sec1_mc_microburst",    "etc_mix",       "poisson_burst", 600, 0,   256),  "MC ETC\nBurst"),
        (("sec1_mc_microburst",    "websearch_mc",  "poisson_burst", 600, 0,   256),  "MC Web\nBurst"),
        (("sec2_mc_incast",        "etc_mix",       "incast",        400, 384, 256),  "MC Inc.\n384/t"),
        (("sec2_mc_incast",        "etc_mix",       "incast",        400, 512, 1024), "MC Inc.\n512/t"),
        (("sec3_nfv_burst_incast", "nfv_lb_256",    "incast",        800, 512, 512),  "NFV LB\nIncast"),
    ]
    xa = np.arange(len(a_specs))
    wa = 0.20
    handles = []
    for pi, (pol, lbl, col) in enumerate(pols):
        p99s = [med([r["client"]["tot_p99_us"] for r in cells[k + (pol,)]]) for k, _ in a_specs]
        drops = [med([r["client"]["drop_pct"] for r in cells[k + (pol,)]]) for k, _ in a_specs]
        bars = ax1.bar(xa + (pi - 1.5) * wa, p99s, width=wa, color=col, edgecolor="#1E293B", lw=0.45, label=lbl, zorder=3)
        handles.append(bars)
        for bi, (b, v, d) in enumerate(zip(bars, p99s, drops)):
            cx = b.get_x() + b.get_width() / 2
            if pol == "small_sdp_drop" and d > 0.05:
                ax1.text(cx, v + 14, f"{d:.1f}% drop", ha="center", va="bottom",
                         fontsize=6.1, color=C_LARGE, fontweight="bold", rotation=90,
                         bbox=dict(boxstyle="round,pad=0.05", fc="white", ec="none", alpha=0.82))
            elif pol == "credit_sdp":
                ax1.text(cx + 0.07, v + 12, f"{v:.0f}", ha="center", va="bottom",
                         fontsize=6.3, color=C_CREDIT, fontweight="bold",
                         bbox=dict(boxstyle="round,pad=0.04", fc="white", ec="none", alpha=0.85))
    etc_l = med([r["client"]["tot_p99_us"] for r in cells[a_specs[0][0] + ("large_sdp",)]])
    etc_c = med([r["client"]["tot_p99_us"] for r in cells[a_specs[0][0] + ("credit_sdp",)]])
    web_l = med([r["client"]["tot_p99_us"] for r in cells[a_specs[1][0] + ("large_sdp",)]])
    web_c = med([r["client"]["tot_p99_us"] for r in cells[a_specs[1][0] + ("credit_sdp",)]])
    d_etc = (etc_c / etc_l - 1.0) * 100.0
    d_web = (web_c / web_l - 1.0) * 100.0
    ax1.annotate(f"{d_etc:.0f}% / {d_web:.0f}% p99\nvs Large SDP\n(0.00% drops)",
                 xy=(0.30, etc_c), xytext=(0.95, 765),
                 ha="center", fontsize=6.8, color=C_CREDIT, fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.18", fc="#F0FDFA", ec=C_CREDIT, lw=0.65),
                 arrowprops=dict(arrowstyle="->", color=C_CREDIT, lw=0.85))
    ax1.set_xticks(xa)
    ax1.set_xticklabels([lbl for _, lbl in a_specs], fontsize=7.0)
    ax1.set_ylabel("Open-Loop Request p99 (µs)")
    ax1.set_ylim(0, 920)
    ax1.set_title("(a) Burst / Incast Tail & Drops", pad=8)

    # =========================================================================
    # (b) Host Memory Bandwidth (IMC GB/s) Offload on High-Pressure Workloads
    # =========================================================================
    clean_ax(ax2)
    b_specs = [
        (("sec4_poisson_and_flows","etc_mix",       "poisson",       1400, 0,   256),  "MC 1.4M\nPoisson"),
        (("sec1_mc_microburst",    "etc_mix",       "poisson_burst", 600,  0,   256),  "MC ETC\nBurst"),
        (("sec2_mc_incast",        "etc_mix",       "incast",        400,  512, 1024), "MC Inc.\n512/t"),
        (("sec3_nfv_burst_incast", "nfv_nat_1024",  "poisson_burst", 800,  0,   256),  "NFV NAT\n1024 B"),
        (("sec3_nfv_burst_incast", "nfv_websearch", "incast",        800,  512, 512),  "NFV Web\nIncast"),
    ]
    xb = np.arange(len(b_specs))
    imc_by_pol = {}
    for pi, (pol, lbl, col) in enumerate(pols):
        vs = [med([r["host"]["pmu"]["imc_gbps"] for r in cells[k + (pol,)]]) for k, _ in b_specs]
        imc_by_pol[pol] = vs
        ax2.bar(xb + (pi - 1.5) * wa, vs, width=wa, color=col, edgecolor="#1E293B", lw=0.45, zorder=3)

    for i in range(len(b_specs)):
        pct = (imc_by_pol["credit_sdp"][i] - imc_by_pol["large_sdp"][i]) / imc_by_pol["large_sdp"][i] * 100.0
        ymax = max(imc_by_pol[p][i] for p, _, _ in pols)
        ax2.text(xb[i], ymax + 0.12, f"{pct:.0f}%", ha="center", va="bottom",
                 fontsize=7.3, color=C_CREDIT, fontweight="bold")
    k14_b = b_specs[0][0]
    t_l = med([r["host"]["tenant"]["p99_ns"] for r in cells[k14_b + ("large_sdp",)]])
    t_c = med([r["host"]["tenant"]["p99_ns"] for r in cells[k14_b + ("credit_sdp",)]])
    t_pct = (t_c / t_l - 1.0) * 100.0
    dd_all = [med([r["host"]["pmu"]["ddio_hit_pct"] for r in cells[k + ("credit_sdp",)]]) for k, _ in b_specs]
    ax2.text(2.65, 3.05, f"Compact hot ring:\nDDIO {min(dd_all):.1f}–{max(dd_all):.1f}%\nTenant p99 (MC 1.4M):\n{t_l:.1f} → {t_c:.1f} ns ({t_pct:.0f}%)",
             ha="center", va="top", fontsize=6.7, color=C_CREDIT, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.20", fc="#F0FDFA", ec=C_CREDIT, lw=0.65))
    ax2.set_xticks(xb)
    ax2.set_xticklabels([lbl for _, lbl in b_specs], fontsize=6.9)
    ax2.set_ylabel("Host Memory Bandwidth (GB/s)")
    ax2.set_ylim(0, 3.65)
    ax2.set_title("(b) Host IMC & Tenant Isolation", pad=8)

    # =========================================================================
    # (c) Symmetric Host CPU Cost (independent tau_empty) & Idle Headroom
    # =========================================================================
    clean_ax(ax3)
    c_specs = [
        (("sec4_poisson_and_flows","etc_mix",      "poisson",       1400, 0,   256),  "memcached", "MC 1.4M\nPoisson"),
        (("sec1_mc_microburst",    "etc_mix",      "poisson_burst", 600,  0,   256),  "memcached", "MC ETC\nBurst"),
        (("sec2_mc_incast",        "etc_mix",      "incast",        400,  512, 1024), "memcached", "MC Inc.\n512/t"),
        (("sec3_nfv_burst_incast", "nfv_nat_256",  "poisson_burst", 1200, 0,   256),  "nfv-nat",   "NFV NAT\n6M Burst"),
        (("sec3_nfv_burst_incast", "nfv_lb_256",   "incast",        800,  512, 512),  "nfv-nat",   "NFV LB\nIncast"),
    ]
    xc = np.arange(len(c_specs))
    for pi, (pol, lbl, col) in enumerate(pols):
        vs = []
        for k, app_k, _ in c_specs:
            rs = cells[k + (pol,)]
            te = tau_s[(pol, app_k)]
            vs.append(med([(r["host"]["dt_s"] - r["host"]["empty_polls"] * te) * 1e9 / r["host"]["rx_rpcs"] for r in rs]))
        bars = ax3.bar(xc + (pi - 1.5) * wa, vs, width=wa, color=col, edgecolor="#1E293B", lw=0.45, zorder=3)
        if pol == "credit_sdp":
            for b, v in zip(bars, vs):
                ax3.text(b.get_x() + b.get_width() / 2 + 0.07, v + 8, f"{v:.0f}",
                         ha="center", va="bottom", fontsize=6.3, color=C_CREDIT, fontweight="bold",
                         bbox=dict(boxstyle="round,pad=0.04", fc="white", ec="none", alpha=0.85))

    k14 = ("sec4_poisson_and_flows", "etc_mix", "poisson", 1400, 0, 256)
    idle_pct = {}
    for pol in ("credit_sdp", "ceio_style", "large_sdp"):
        te = tau_s[(pol, "memcached")]
        idle_pct[pol] = med([r["host"]["empty_polls"] * te / r["host"]["dt_s"] * 100.0 for r in cells[k14 + (pol,)]])
    gain_ceio = (idle_pct["credit_sdp"] / idle_pct["ceio_style"] - 1.0) * 100.0
    gain_large = (idle_pct["credit_sdp"] / idle_pct["large_sdp"] - 1.0) * 100.0

    ax3.text(3.60, 678,
             f"MC 1.4M idle headroom:\n"
             f"CreditRing {idle_pct['credit_sdp']:.1f}% vs\n"
             f"CEIO {idle_pct['ceio_style']:.1f}% (+{gain_ceio:.1f}%)\n"
             f"Large {idle_pct['large_sdp']:.1f}% (+{gain_large:.1f}%)",
             ha="center", va="top", fontsize=6.4, color=C_CREDIT, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.20", fc="#F0FDFA", ec=C_CREDIT, lw=0.65))
    ax3.set_xticks(xc)
    ax3.set_xticklabels([lbl for _, _, lbl in c_specs], fontsize=6.9)
    ax3.set_ylabel("Active Host CPU (ns / req)")
    ax3.set_ylim(0, 705)
    ax3.set_title("(c) Symmetric CPU & Idle Margin", pad=8)

    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 1.07),
               ncol=4, fontsize=8.4, frameon=True, columnspacing=1.5)
    save_fig(fig, "fig6_e5_apps")


if __name__ == "__main__":
    main()
