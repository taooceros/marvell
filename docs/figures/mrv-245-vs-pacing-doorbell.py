"""MRV-245 Figure 3: Head-to-head three-way comparison of:
  1) Standard Linux MT_NORMAL_NC (0x44) — zero MAIR edit, zero SFENCE, zero doorbell
  2) Normal-WB + Software Pacing (MRV-115/123 hysteresis credit & burst/gap)
  3) Normal-WB + Decoupled Coalesced Doorbell (MRV-177 Arm B2 SFENCE + prod_idx)
across all three tracks: W=1 unloaded RTT, W=256 closed-loop, and H=1 one-way TX,
including symmetric disclosure of NC K=8 over-provisioned CHA vs doorbell all-K CHA safety.

Reads raw verified n=3 runs directly from logs/mrv245-2026-11-27/runs.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv245-2026-11-27/runs.jsonl"


def load_runs():
    by = defaultdict(list)
    for ln in RUNS_PATH.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        cp = r["card_pmu"]
        assert r["lost"] == 0 and r["bad_or_torn"] == 0
        assert sum(w["corrupt"] for w in cp["workers"]) == 0
        assert sum(w["partial"] for w in cp["workers"]) == 0
        assert sum(w["db_ahead_empty"] for w in cp["workers"]) == 0
        assert sum(w["db_ahead_partial"] for w in cp["workers"]) == 0
        assert r["host_min_pct"] >= 99.9 and cp["min_pct"] >= 99.9
        by[r["cond"]].append(r)
    return by


def stats(rs, fn):
    vals = [fn(r) for r in rs]
    return statistics.median(vals), min(vals), max(vals)


def main():
    by = load_runs()
    ceil_h1 = stats(by["tx_none_h1"], lambda r: r["mops"])[0]
    cha_nc_u4_k8 = stats(by["tx_nc_u4_h1_k8"], lambda r: r["host_pmu"]["cha_inflight"])[0]
    cha_nc_u1_k8 = stats(by["tx_nc_u1_h1_k8"], lambda r: r["host_pmu"]["cha_inflight"])[0]

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(17.4, 6.0), gridspec_kw={"width_ratios": [0.85, 1.15, 1.62]}
    )

    # ------------------------------------------------------------------
    # Panel A: Track 1 — W=1 Unloaded RTT
    # ------------------------------------------------------------------
    a_specs = [
        ("cl_nc_u1_k4_w1", "MT_NORMAL_NC\n(u1, K=4)", "#16a34a"),
        ("cl_wb_u1_k4_w1", "Normal-WB\n无控 (K=4)", "#94a3b8"),
        ("cl_wb_hyst32b16_k4_w1", "软件 pacing\n(C=32 B=16)", "#d97706"),
        ("cl_wb_db32_k4_w1", "解耦门铃\n(SFENCE+prod)", "#2563eb"),
    ]
    xa = np.arange(len(a_specs))
    p50_a = [stats(by[c], lambda r: r["rtt_p50_ns"])[0] for c, _, _ in a_specs]
    p99_a = [stats(by[c], lambda r: r["rtt_p99_ns"])[0] for c, _, _ in a_specs]
    tax_ns = p50_a[3] - p50_a[0]

    ax1.bar(xa, p50_a, width=0.56, color=[s[2] for s in a_specs], alpha=0.88, zorder=3, label="p50")
    ax1.scatter(xa, p99_a, color="#0f172a", marker="D", s=34, zorder=5, label="p99")
    for xi, a, b in zip(xa, p50_a, p99_a):
        ax1.plot([xi, xi], [a, b], color="#334155", lw=1.2, zorder=4)
        ax1.text(
            xi, b + 14, f"{a:.0f}\n(p99 {b:.0f})",
            ha="center", va="bottom", fontsize=7.9, color="#0f172a",
            fontweight="bold" if xi in (0, 3) else "normal",
        )

    ax1.annotate(
        f"+{tax_ns:.0f} ns\n固定结构税",
        xy=(3, p50_a[3]), xytext=(2.12, 1215),
        ha="center", fontsize=8.3, fontweight="bold", color="#1d4ed8",
        arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.1),
    )
    ax1.set_xticks(xa)
    ax1.set_xticklabels([s[1] for s in a_specs], fontsize=8.2)
    ax1.set_ylim(750, 1295)
    ax1.set_ylabel("空载单包 RTT（ns，越低越好）", fontsize=9.8)
    ax1.set_title("赛道 1 · 空载 W=1 延迟", fontsize=10.6, fontweight="bold", pad=8)
    ax1.legend(loc="upper left", fontsize=8.0, frameon=True)

    # ------------------------------------------------------------------
    # Panel B: Track 2 — High-Concurrency Closed-Loop (W=256, H=1)
    # ------------------------------------------------------------------
    b_specs = [
        ("cl_wb_raw_k4_w256", "WB 无控\n(K=4)", "#dc2626"),
        ("cl_wb_hyst32b16_k4_w256", "软件 pacing\n(C32 B16, K=4)", "#d97706"),
        ("cl_wb_db32_k4_w256", "解耦门铃\nB32 (K=4)", "#60a5fa"),
        ("cl_wb_db32_k1_w256", "解耦门铃\nB32 (K=1)", "#2563eb"),
        ("cl_nc_u1_h1_k2_w256", "NC 标量\nu1 (K=2)", "#0ea5e9"),
        ("cl_nc_u1q_h1_k2_w256", "NC NEON\nu1q (K=2) ★", "#16a34a"),
    ]
    xb = np.arange(len(b_specs))
    bm, blo, bhi = [], [], []
    bp50, bp99, bcha = [], [], []
    for c, _, _ in b_specs:
        m, mn, mx = stats(by[c], lambda r: r["mops"])
        bm.append(m); blo.append(m - mn); bhi.append(mx - m)
        bp50.append(stats(by[c], lambda r: r["rtt_p50_ns"])[0] / 1000.0)
        bp99.append(stats(by[c], lambda r: r["rtt_p99_ns"])[0] / 1000.0)
        bcha.append(stats(by[c], lambda r: r["host_pmu"]["cha_inflight"])[0])

    ax2.bar(
        xb, bm, width=0.58, color=[s[2] for s in b_specs], alpha=0.88,
        yerr=[blo, bhi], capsize=3.2, error_kw={"elinewidth": 1.2, "ecolor": "#1e293b"},
        zorder=3,
    )
    for xi, m, hi, p5, p9, ch in zip(xb, bm, bhi, bp50, bp99, bcha):
        ax2.text(
            xi, m + hi + 2.2,
            f"{m:.1f} M/s\np50 {p5:.1f}µs\np99 {p9:.1f}µs\nCHA {ch:.1f}",
            ha="center", va="bottom", fontsize=7.4, color="#0f172a",
            fontweight="bold" if xi == 5 else "normal",
            linespacing=1.18,
        )

    ax2.set_xticks(xb)
    ax2.set_xticklabels([s[1] for s in b_specs], fontsize=8.0)
    ax2.set_ylim(0, 102)
    ax2.set_ylabel("闭环吞吐（Mpps，越高越好）", fontsize=9.8)
    ax2.set_title("赛道 2 · 高并发闭环（W=256, H=1）", fontsize=10.6, fontweight="bold", pad=8)

    # ------------------------------------------------------------------
    # Panel C: Track 3 — One-Way TX (H=1, full 64 B verify), symmetric CHA disclosure
    # ------------------------------------------------------------------
    c_specs = [
        ("tx_wb_pace32g200_k4", "pacing\nB32 g200\n(K=4)", "#d97706"),
        ("tx_wb_u1_h1_k4", "WB 无控\n(K=4)", "#dc2626"),
        ("tx_wb_db32_k4", "门铃 B32\n(K=4)", "#bfdbfe"),
        ("tx_wb_db32_k2", "门铃 B32\n(K=2)", "#93c5fd"),
        ("tx_wb_db64_k4", "门铃 B64\n(K=4)", "#60a5fa"),
        ("tx_wb_db64_k2", "门铃 B64\n(K=2)", "#3b82f6"),
        ("tx_wb_db128_k2", "门铃 B128\n(K=2)", "#1d4ed8"),
        ("tx_nc_u4_h1_k8", "NC 超配\nu4 (K=8)", "#ea580c"),
        ("tx_nc_u4_h1_k4", "NC 匹配\nu4 (K=4) ★", "#16a34a"),
    ]
    xc = np.arange(len(c_specs))
    cm, clo, chi, ccha = [], [], [], []
    for c, _, _ in c_specs:
        m, mn, mx = stats(by[c], lambda r: r["mops"])
        cm.append(m); clo.append(m - mn); chi.append(mx - m)
        ccha.append(stats(by[c], lambda r: r["host_pmu"]["cha_inflight"])[0])

    ax3.axhline(ceil_h1, color="#475569", ls="--", lw=1.3, zorder=2)
    ax3.text(
        -0.42, ceil_h1 + 3.5,
        f"H=1 单主机核卡静默基线：{ceil_h1:.1f} M/s",
        fontsize=8.1, color="#334155", fontweight="bold",
    )

    ax3.bar(
        xc, cm, width=0.60, color=[s[2] for s in c_specs], alpha=0.88,
        yerr=[clo, chi], capsize=3.0, error_kw={"elinewidth": 1.2, "ecolor": "#1e293b"},
        zorder=3,
    )
    for xi, m, hi, ch in zip(xc, cm, chi, ccha):
        danger = ch >= 330
        cha_txt = f"CHA {ch:.0f}\n(超330)" if danger else f"CHA {ch:.1f}\n(安全)"
        txt_col = "#b91c1c" if danger else ("#15803d" if xi == 8 else "#0f172a")
        ax3.text(
            xi, m + hi + 2.8,
            f"{m:.1f}\n{cha_txt}",
            ha="center", va="bottom", fontsize=6.8, color=txt_col,
            fontweight="bold" if (danger or xi == 8) else "normal",
            linespacing=1.14,
            bbox=dict(boxstyle="round,pad=0.08", fc="white", ec="none", alpha=0.90),
            zorder=6,
        )

    ax3.set_xticks(xc)
    ax3.set_xticklabels([s[1] for s in c_specs], fontsize=7.2)
    ax3.set_ylim(0, 212)
    ax3.set_ylabel("单向校验吞吐（Mpps，越高越好）", fontsize=9.8)
    ax3.set_title("赛道 3 · 单向直推（H=1，补齐同批 B32/B64 门铃与 NC 超配）", fontsize=10.4, fontweight="bold", pad=8)

    fig.suptitle(
        "MRV-245 · 三方正面对决：MT_NORMAL_NC (0x44) vs 软件 pacing vs 解耦合并门铃",
        fontsize=12.6, fontweight="bold", y=0.99,
    )
    fig.text(
        0.5, 0.008,
        (
            f"公平性披露：赛道 2 同标量 u1 下 2核 NC ({bm[4]:.1f}) 与 1核门铃 ({bm[3]:.1f}) 持平，u1q NEON 达 {bm[5]:.1f} M/s；"
            f"赛道 3 门铃走慢分支 (72.4 ins/p vs NC 31.9，同紧循环 B=128 理论 ~138 M/s，真实 SFENCE+门铃约 ~135 ns)；"
            f"pacing B32 g200 因 200 ns 空隙诱发卡核追平背压致 CHA={ccha[0]:.0f}"
        ),
        ha="center", fontsize=7.6, color="#334155", fontweight="bold",
    )
    fig.tight_layout(rect=[0, 0.04, 1, 0.945])
    png = mrvplot.HERE / "mrv-245-vs-pacing-doorbell.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-245-vs-pacing-doorbell.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
