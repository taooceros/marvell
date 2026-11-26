#!/usr/bin/env python3
"""MRV-214 Fig 3: On-Path CN106 card-core scaling (1/2/4/8 cores) across 64 B / 254 B / 1500 B."""
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv214-2026-11-26/runs.jsonl"


def load_runs():
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    by_cfg = {}
    for r in rows:
        assert r["verified"]
        assert r["host_pmu"]["page_faults"] == 0
        assert r["host_final"]["bad"] == 0 and r["host_final"]["torn"] == 0
        if r["spec"]["credit"] != "off":
            assert r["c2h_lost"] == 0
        by_cfg.setdefault(r["cfg"], []).append(r)
    return by_cfg


def stats_n(rs, fn):
    vals = [fn(x) for x in rs]
    med = statistics.median(vals)
    return med, med - min(vals), max(vals) - med


def main():
    by_cfg = load_runs()
    cores = [1, 2, 4, 8]
    x_pos = np.arange(len(cores))

    def get_curve(lbl, hc_fn, ctag, metric_fn):
        ms, los, his = [], [], []
        for nc in cores:
            hc = hc_fn(nc)
            rs = by_cfg[f"s3_{lbl}_c{nc}_h{hc}_{ctag}"]
            m, l, h = stats_n(rs, metric_fn)
            ms.append(m); los.append(l); his.append(h)
        return ms, los, his

    g1500_c, lo1500, hi1500 = get_curve("1500b", lambda nc: 1, "credit", lambda x: x["rates"]["host_wire_gbps"])
    g254_c, lo254, hi254 = get_curve("254b", lambda nc: min(nc, 2), "credit", lambda x: x["rates"]["host_wire_gbps"])
    p254_c, _, _ = get_curve("254b", lambda nc: min(nc, 2), "credit", lambda x: x["rates"]["host_rx_mpps"])
    g64_c, lo64, hi64 = get_curve("64b", lambda nc: min(nc, 4), "credit", lambda x: x["rates"]["host_wire_gbps"])
    g64_o, _, _ = get_curve("64b", lambda nc: min(nc, 4), "off", lambda x: x["rates"]["host_wire_gbps"])
    p64_c, plo_c, phi_c = get_curve("64b", lambda nc: min(nc, 4), "credit", lambda x: x["rates"]["host_rx_mpps"])
    p64_o, plo_o, phi_o = get_curve("64b", lambda nc: min(nc, 4), "off", lambda x: x["rates"]["host_rx_mpps"])
    lost_o = [
        statistics.median([x["c2h_lost"] for x in by_cfg[f"s3_64b_c{nc}_h{min(nc,4)}_off"]])
        for nc in cores
    ]
    fifo_drop_c8 = statistics.median([x["rates"]["steady_fifo_drops"] for x in by_cfg["s3_64b_c8_h4_credit"]])
    s2_b32 = statistics.median([x["rates"]["host_rx_mpps"] for x in by_cfg["s2a_uc64_4c1h_nofence_b32"]])
    s2_b64 = statistics.median([x["rates"]["host_rx_mpps"] for x in by_cfg["s2a_uc64_4c1h_nofence_b64"]])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.4))
    fig.suptitle(
        "MRV-214 扫描三：On-Path CN106 卡核扩展（1–8 核）达成 100GbE 线速的算力边界与无丢包优势",
        fontsize=12.8,
        fontweight="bold",
        y=0.985,
    )
    fig.text(
        0.5,
        0.935,
        "CN106 2.0 GHz (1/2/4/8 卡核) ⇄ saturn 3.9 GHz (ism_enable=0, rx_desc=512, R_eff=256/Q, B=32 无 SFENCE；254 B 配 hc=1–2、64 B 配 hc=1–4 主核)",
        ha="center",
        fontsize=8.9,
        color="#444444",
    )

    # ---------------- Panel A: 100GbE line-rate boundary across frame sizes ----------------
    ax1.axhline(100.0, color="#666666", linestyle="--", linewidth=1.2, zorder=1)
    ax1.text(0.05, 102.2, "100GbE 物理线速上限", fontsize=8.4, color="#555555")

    ax1.errorbar(
        x_pos, g1500_c, yerr=[lo1500, hi1500], fmt="s-", color="#15803d", linewidth=2.3, markersize=7,
        capsize=3.5, label="1500 B UDP (hc=1 单主核, C2H 0 丢包)", zorder=4,
    )
    ax1.errorbar(
        x_pos, g254_c, yerr=[lo254, hi254], fmt="o-", color="#1f5aa6", linewidth=2.3, markersize=7,
        capsize=3.5, label="254 B UD (hc=min(nc,2) 多主核, C2H 0 丢包)", zorder=4,
    )
    ax1.errorbar(
        x_pos, g64_c, yerr=[lo64, hi64], fmt="D-", color="#d97706", linewidth=2.3, markersize=6.8,
        capsize=3.5, label="64 B UC (hc=min(nc,4) 多主核, C2H 0 丢包)", zorder=4,
    )
    ax1.plot(
        x_pos, g64_o, "D--", color="#9ca3af", linewidth=1.5, markersize=5.2,
        label=f"64 B 无信用对照 (hc=min(nc,4), 8 核 OQ 静默丢 {lost_o[3]/1e6:.2f}M)", zorder=2,
    )
    ax1.set_xticks(x_pos)
    ax1.set_xticklabels([f"{c} 核" for c in cores], fontsize=10)
    ax1.set_xlim(-0.35, 3.45)
    ax1.set_ylim(0, 114)
    ax1.set_xlabel("CN106 On-Path ARM 卡核数（每核独占 eth0 RSS + SDP TX + BAR4 信用行）", fontsize=9.8)
    ax1.set_ylabel("主机有效接收线速（Gbps，含 20 B 线开销）", fontsize=10)
    ax1.set_title("A. 100GbE 线速所需最小卡核数：1500 B 需 1 核，254 B 需 4 核", fontsize=10.5, fontweight="bold", pad=8)

    ax1.annotate(
        f"1 核即满线速\n{g1500_c[0]:.1f} Gbps",
        xy=(0, g1500_c[0]), xytext=(0.18, 82), fontsize=8.6, fontweight="bold", color="#15803d",
        bbox=dict(boxstyle="round,pad=0.24", fc="white", ec="#15803d", alpha=0.92),
        arrowprops=dict(arrowstyle="->", color="#15803d", lw=1.1),
    )
    ax1.annotate(
        f"4 卡核 + hc=2 主核\n达 {g254_c[2]:.1f}G ({p254_c[2]:.2f}M, C2H 0 丢)",
        xy=(2, g254_c[2]), xytext=(1.55, 74), fontsize=8.4, fontweight="bold", color="#1f5aa6",
        bbox=dict(boxstyle="round,pad=0.24", fc="white", ec="#1f5aa6", alpha=0.92),
        arrowprops=dict(arrowstyle="->", color="#1f5aa6", lw=1.1),
    )
    ax1.annotate(
        f"8 卡核 + hc=4 主核\n达 {g64_c[3]:.1f}G ({p64_c[3]:.1f}M, C2H 0 丢)",
        xy=(3, g64_c[3]), xytext=(1.72, 34), fontsize=8.4, fontweight="bold", color="#b45309",
        bbox=dict(boxstyle="round,pad=0.24", fc="white", ec="#d97706", alpha=0.92),
        arrowprops=dict(arrowstyle="->", color="#d97706", lw=1.1),
    )
    ax1.legend(loc="lower right", fontsize=7.8, framealpha=0.92)

    # ---------------- Panel B: 64 B packet rate & silent drop comparison ----------------
    w = 0.34
    ax2.bar(
        x_pos - w / 2, p64_c, width=w, yerr=[plo_c, phi_c], capsize=3.5,
        color="#1f5aa6", edgecolor="#163e73", linewidth=0.8,
        label="BAR4 无 SFENCE 信用环（C2H c2h_lost = 0；过载由入口 FIFO 削峰）", zorder=3,
    )
    ax2.bar(
        x_pos + w / 2, p64_o, width=w, yerr=[plo_o, phi_o], capsize=3.5,
        color="#d1d5db", edgecolor="#6b7280", linewidth=0.8, hatch="//",
        label="无信用对照（SDP OQ 静默丢包）", zorder=3,
    )

    for i, (vc, hc_err, vo, ho_err, lo_v) in enumerate(zip(p64_c, phi_c, p64_o, phi_o, lost_o)):
        ax2.text(x_pos[i] - w / 2 - 0.03, vc + hc_err + 2.2, f"{vc:.1f}M", ha="center", va="bottom",
                 fontsize=8.4, fontweight="bold", color="#1f5aa6")
        if lo_v >= 1e6:
            drop_txt = f"{vo:.1f}M\n丢{lo_v/1e6:.2f}M!"
            col = "#dc2626"
            fw = "bold"
        else:
            drop_txt = f"{vo:.1f}M\n丢 {int(lo_v)}"
            col = "#991b1b"
            fw = "normal"
        ax2.text(x_pos[i] + w / 2 + 0.05, vo + ho_err + 2.2, drop_txt, ha="center", va="bottom",
                 fontsize=7.9, color=col, fontweight=fw)

    ax2.annotate(
        f"8 卡核 + hc=4 主核过载反转：\n信用环反高 +{p64_c[3]-p64_o[3]:.1f}M (+{(p64_c[3]/p64_o[3]-1)*100:.1f}%)\nC2H c2h_lost = 0（入口 FIFO 削峰 {fifo_drop_c8/1e6:.2f}M）\nvs 无信用 OQ 静默丢 {lost_o[3]/1e6:.2f}M!",
        xy=(3 - w / 2, p64_c[3] + 1.0), xytext=(-0.32, 71), fontsize=8.0, fontweight="bold", color="#136f45",
        bbox=dict(boxstyle="round,pad=0.28", fc="#eaf7ee", ec="#15803d", alpha=0.94),
        arrowprops=dict(arrowstyle="->", color="#15803d", lw=1.2, connectionstyle="arc3,rad=-0.16"),
    )
    ax2.text(
        -0.32, 108.5,
        f"注：单主核 64 B 无丢包上限为 {s2_b32:.1f}–{s2_b64:.1f} Mpps（扫描二）",
        fontsize=7.9, color="#444444",
    )

    ax2.set_xticks(x_pos)
    ax2.set_xticklabels([f"{c} 卡核\n(hc={min(c,4)} 主核)" for c in cores], fontsize=9.2)
    ax2.set_ylim(0, 138)
    ax2.set_xlabel("CN106 卡核数 / saturn 多主核配置（64 B UC 线速压力 148.8 Mpps）", fontsize=9.8)
    ax2.set_ylabel("主机有效收包率（Mpps）", fontsize=10)
    ax2.set_title("B. 64 B 极限包率（多主核）：4 核近线性，8 核有信用反超无信用", fontsize=10.3, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", fontsize=8.0, framealpha=0.92)

    fig.tight_layout(rect=[0, 0, 1, 0.91])
    out = mrvplot.save(fig, "mrv-214-card-core-scaling")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
