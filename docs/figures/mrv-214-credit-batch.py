#!/usr/bin/env python3
"""MRV-214 Fig 2: NO-SFENCE credit return batch granularity B (CPU/PCIe overhead vs starvation)."""
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt

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


def stats3(rs, fn):
    vals = [fn(x) for x in rs]
    med = statistics.median(vals)
    return med, med - min(vals), max(vals) - med


def main():
    by_cfg = load_runs()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.4))
    fig.suptitle(
        "MRV-214 扫描二：无 SFENCE 信用批回粒度 B 的双面权衡（高频开销 vs 补推饥饿阈值）",
        fontsize=12.8,
        fontweight="bold",
        y=0.985,
    )
    fig.text(
        0.5,
        0.935,
        "左图：64 B 满载 (4 卡核→1 主核 cpu40, R_eff=256) · 右图：1500 B (1C→1H) B ≤ R_eff/2 饥饿阈值 · n=3 中位数(min–max，64 B 无信用对照 n=5)",
        ha="center",
        fontsize=9.2,
        color="#444444",
    )

    # ---------------- Panel A: 64 B CPU + reverse PCIe overhead vs B ----------------
    bs = [8, 16, 32, 64, 128]
    rx_m, rx_lo, rx_hi = [], [], []
    wr_m, wr_lo, wr_hi = [], [], []
    cyc_m = []
    for b in bs:
        rs = by_cfg[f"s2a_uc64_4c1h_nofence_b{b}"]
        m, l, h = stats3(rs, lambda x: x["rates"]["host_rx_mpps"])
        rx_m.append(m); rx_lo.append(l); rx_hi.append(h)
        mw, lw, hw = stats3(rs, lambda x: x["host_pmu"]["iio1_cpu_wr_gbps"])
        wr_m.append(mw); wr_lo.append(lw); wr_hi.append(hw)
        cyc_m.append(statistics.median([x["rates"]["cycles_per_pkt"] for x in rs]))

    rs_sf = by_cfg["s2a_uc64_4c1h_sfence_b32"]
    sf_rx, sf_lo, sf_hi = stats3(rs_sf, lambda x: x["rates"]["host_rx_mpps"])
    sf_sb = statistics.median([x["host_pmu"]["sb_stall_pct"] for x in rs_sf])

    rs_off = by_cfg["s2a_uc64_4c1h_off"]
    off_rx = statistics.median([x["rates"]["host_rx_mpps"] for x in rs_off])
    off_lost = statistics.median([x["c2h_lost"] for x in rs_off])
    b64_1500_wr = statistics.median([x["host_pmu"]["iio1_cpu_wr_gbps"] for x in by_cfg["s2a_1500b_1c1h_nofence_b64"]])

    ax1.axvspan(27, 74, color="#eaf7ee", alpha=0.65, zorder=0)
    l1 = ax1.errorbar(
        bs, rx_m, yerr=[rx_lo, rx_hi], fmt="o-", color="#1f5aa6", linewidth=2.3, markersize=7,
        capsize=3.5, label="无 SFENCE 主核吞吐 (0 丢包)", zorder=4,
    )
    l2 = ax1.errorbar(
        [32], [sf_rx], yerr=[[sf_lo], [sf_hi]], fmt="^", color="#d97706", markersize=9,
        capsize=3.5, label=f"B=32 加 SFENCE: {sf_rx:.1f}M (SB停顿 {sf_sb:.1f}%)", zorder=5,
    )
    ax1.axhline(off_rx, color="#888888", linestyle=":", linewidth=1.2, zorder=1)
    ax1.text(
        150, off_rx + 1.8,
        f"无信用对照: {off_rx:.1f} Mpps（静默丢 {off_lost/1e6:.2f}M 包！）",
        ha="right", fontsize=8.2, color="#555555", fontweight="bold",
    )

    ax1.set_xscale("log", base=2)
    ax1.set_xticks(bs)
    ax1.set_xticklabels([str(b) for b in bs])
    ax1.set_xlim(6.8, 158)
    ax1.set_ylim(22, 98)
    ax1.set_xlabel("信用批回粒度 B（每 B 包发 1 笔 64 B MOVDIR64B）", fontsize=10)
    ax1.set_ylabel("主机单核无丢包收包率（Mpps）", fontsize=10, color="#1f5aa6")
    ax1.tick_params(axis="y", labelcolor="#1f5aa6")
    ax1.set_title("A. 64 B 高频开销：B=32–64 为最优甜点（低 CPU & 低反向 PCIe）", fontsize=10.5, fontweight="bold", pad=8)

    ax1_r = ax1.twinx()
    ax1_r.spines["top"].set_visible(False)
    l3 = ax1_r.errorbar(
        bs, wr_m, yerr=[wr_lo, wr_hi], fmt="s--", color="#0f766e", linewidth=1.9, markersize=6,
        capsize=3, label="反向 PCIe 总写带宽 (Gbps, 右轴)", zorder=3,
    )
    ax1_r.set_ylabel("主机→卡反向 PCIe 写带宽（IIO1，Gbps）", fontsize=10, color="#0f766e")
    ax1_r.tick_params(axis="y", labelcolor="#0f766e")
    ax1_r.set_ylim(0, 4.3)
    ax1_r.grid(False)

    ax1.annotate(
        f"最优 B=64\n{rx_m[3]:.1f}M ({cyc_m[3]:.1f} cyc/pkt)\n反向 {wr_m[3]:.2f} Gbps",
        xy=(64, rx_m[3]), xytext=(34, 68.5), fontsize=8.4, fontweight="bold", color="#136f45",
        bbox=dict(boxstyle="round,pad=0.26", fc="white", ec="#136f45", alpha=0.94),
        arrowprops=dict(arrowstyle="->", color="#136f45", lw=1.1),
    )
    ax1.annotate(
        f"B=8 过频\n{rx_m[0]:.1f}M ({cyc_m[0]:.1f} cyc/pkt)\n反向 {wr_m[0]:.2f}G",
        xy=(8, rx_m[0]), xytext=(10.5, 25.5), fontsize=8.2, color="#a61c1c",
        arrowprops=dict(arrowstyle="->", color="#a61c1c", lw=1.0),
    )
    ax1.annotate(
        f"B=128=R_eff/2\n卡停顿↑ → {rx_m[4]:.1f}M",
        xy=(128, rx_m[4]), xytext=(68, 41.0), fontsize=8.2, color="#9a3412",
        arrowprops=dict(arrowstyle="->", color="#9a3412", lw=1.0),
    )
    ax1.text(
        150, 24.2,
        f"注：1500 B 线速下 B=64 反向写仅 {b64_1500_wr:.2f} Gbps",
        ha="right", fontsize=8.0, color="#444444",
    )

    handles = [l1, l2, l3]
    labels = [h.get_label() for h in handles]
    ax1.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.01, 0.99), fontsize=7.9, framealpha=0.93)

    # ---------------- Panel B: 1500 B Refill Starvation Threshold (B <= R_eff / 2) ----------------
    b128 = [8, 16, 32, 64, 96, 128]
    y128, lo128, hi128 = [], [], []
    for b in b128:
        m, l, h = stats3(by_cfg[f"s2b_1500b_r128_b{b}"], lambda x: x["rates"]["host_wire_gbps"])
        y128.append(m); lo128.append(l); hi128.append(h)

    b64 = [8, 16, 32, 48, 64, 96]
    y64, lo64, hi64 = [], [], []
    for b in b64:
        m, l, h = stats3(by_cfg[f"s2b_1500b_r64_b{b}"], lambda x: x["rates"]["host_wire_gbps"])
        y64.append(m); lo64.append(l); hi64.append(h)

    ax2.axhline(98.6, color="#666666", linestyle="--", linewidth=1.1, zorder=1)
    ax2.axvline(64, color="#1f5aa6", linestyle=":", linewidth=1.3, zorder=1)
    ax2.axvline(32, color="#c0392b", linestyle=":", linewidth=1.3, zorder=1)

    ax2.errorbar(
        b128, y128, yerr=[lo128, hi128], fmt="o-", color="#1f5aa6", linewidth=2.3, markersize=6.8,
        capsize=3.5, label="R_eff = 128 (安全阈值 B ≤ 64)", zorder=4,
    )
    ax2.errorbar(
        b64, y64, yerr=[lo64, hi64], fmt="s-", color="#dc2626", linewidth=2.2, markersize=6.5,
        capsize=3.5, label="R_eff = 64 (紧窗口，B > 64 触发 50 µs 兜底)", zorder=3,
    )

    ax2.set_xticks([8, 16, 32, 48, 64, 96, 128])
    ax2.set_xlim(2, 136)
    ax2.set_ylim(0, 114)
    ax2.set_xlabel("信用批回粒度 B（包）", fontsize=10)
    ax2.set_ylabel("主机有效接收线速（Gbps，1500 B UDP）", fontsize=10)
    ax2.set_title("B. 补推饥饿阈值：B 超过 R_eff/2 后吞吐骤降，超 R_eff 崩盘", fontsize=10.5, fontweight="bold", pad=8)

    ax2.text(66.5, 102.5, "R_eff=128 的\nB=R_eff/2 边界", fontsize=8.3, color="#1f5aa6", fontweight="bold")
    ax2.annotate(
        f"B ≤ 64 全满速\n({y128[3]:.1f} Gbps)",
        xy=(64, y128[3]), xytext=(16, 102.0), fontsize=8.6, fontweight="bold", color="#136f45",
        arrowprops=dict(arrowstyle="->", color="#136f45", lw=1.1),
    )
    ax2.annotate(
        f"B=96 → {y128[4]:.1f}G\nB=128 → {y128[5]:.1f}G",
        xy=(96, y128[4]), xytext=(85, 59), fontsize=8.5, color="#1f5aa6",
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#1f5aa6", alpha=0.92),
        arrowprops=dict(arrowstyle="->", color="#1f5aa6", lw=1.0),
    )
    ax2.annotate(
        f"B=96 > R_eff=64\n崩至 {y64[-1]:.1f}G\n(50 µs 超时兜底防死锁)",
        xy=(96, y64[-1]), xytext=(88, 30), fontsize=8.2, fontweight="bold", color="#b91c1c",
        bbox=dict(boxstyle="round,pad=0.26", fc="#fff5f5", ec="#dc2626", alpha=0.94),
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.1),
    )
    ax2.legend(loc="lower left", fontsize=8.4, framealpha=0.92)

    fig.tight_layout(rect=[0, 0, 1, 0.91])
    out = mrvplot.save(fig, "mrv-214-credit-batch")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
