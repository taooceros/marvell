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
        "左图：64 B 满载 (4C→1H cpu40, R_eff=256, 固定 BURST_N=64 批尾连发 64/B 笔) · 右图：1500 B (1C→1H) B ≤ R_eff/2 · n=3 中位数(无信用 n=5)",
        ha="center",
        fontsize=8.9,
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

    def cyc64_parts(cfg):
        rs = by_cfg[cfg]
        tot = statistics.median([x["rates"]["cycles_per_pkt"] * 64.0 for x in rs])
        sb = statistics.median([x["rates"]["cycles_per_pkt"] * 64.0 * x["host_pmu"]["sb_stall_pct"] / 100.0 for x in rs])
        nsb = statistics.median([x["rates"]["cycles_per_pkt"] * 64.0 * (1.0 - x["host_pmu"]["sb_stall_pct"] / 100.0) for x in rs])
        return tot, sb, nsb

    tot64, sb64, nsb64 = cyc64_parts("s2a_uc64_4c1h_nofence_b64")
    tot8, sb8, nsb8 = cyc64_parts("s2a_uc64_4c1h_nofence_b8")
    nsb_share_pct = (nsb8 - nsb64) / (tot8 - tot64) * 100.0
    b8_rs = by_cfg["s2a_uc64_4c1h_nofence_b8"]
    b8_upd = statistics.median([x["card_final"]["credit_updates"] / x["card_final"]["elapsed_s"] / 1e6 for x in b8_rs])
    b8_wr = statistics.median([x["host_final"]["credit_writes"] / x["host_final"]["elapsed_s"] / 1e6 for x in b8_rs])
    b8_overwrite_pct = (1.0 - b8_upd / b8_wr) * 100.0

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
    ax1.set_ylim(16, 99)
    ax1.set_xlabel("信用批回粒度 B（s2a_uc64 固定 BURST_N=64 批尾连发 64/B 笔 MOVDIR64B）", fontsize=9.4)
    ax1.set_ylabel("主机单核无丢包收包率（Mpps）", fontsize=10, color="#1f5aa6")
    ax1.tick_params(axis="y", labelcolor="#1f5aa6")
    ax1.set_title("A. 64 B (BURST_N=64)：B < 64 同行连发致串行退化，B = 64 峰值", fontsize=10.3, fontweight="bold", pad=8)
    ax1_r = ax1.twinx()
    ax1_r.spines["top"].set_visible(False)
    l3 = ax1_r.errorbar(
        bs, wr_m, yerr=[wr_lo, wr_hi], fmt="s--", color="#0f766e", linewidth=1.9, markersize=6,
        capsize=3, label="反向 PCIe 总写带宽 (Gbps, 右轴)", zorder=3,
    )
    ax1_r.set_ylabel("主机→卡反向 PCIe 写带宽（IIO1，Gbps）", fontsize=10, color="#0f766e")
    ax1_r.tick_params(axis="y", labelcolor="#0f766e")
    ax1_r.set_ylim(-0.35, 4.35)
    ax1_r.grid(False)

    ax1.annotate(
        f"峰值 B=64（每批仅发 1 笔）\n{rx_m[3]:.1f}M ({cyc_m[3]:.1f} cyc/pkt)\n反向 {wr_m[3]:.2f} Gbps",
        xy=(64, rx_m[3]), xytext=(28.5, 67.5), fontsize=8.2, fontweight="bold", color="#136f45",
        bbox=dict(boxstyle="round,pad=0.26", fc="white", ec="#136f45", alpha=0.94),
        arrowprops=dict(arrowstyle="->", color="#136f45", lw=1.1),
    )
    ax1.annotate(
        f"B=8 批尾连发 8 笔同行写\n{rx_m[0]:.1f}M ({cyc_m[0]:.1f} cyc/pkt, 反向 {wr_m[0]:.2f}G)\n{b8_overwrite_pct:.0f}% 覆写 · ~{nsb_share_pct:.0f}% 为非 SB 串行",
        xy=(8, rx_m[0]), xytext=(10.2, 26.8), fontsize=7.9, color="#a61c1c",
        bbox=dict(boxstyle="round,pad=0.22", fc="#fff8f8", ec="#dc2626", alpha=0.92),
        arrowprops=dict(arrowstyle="->", color="#a61c1c", lw=1.0),
    )
    ax1.annotate(
        f"B=128=R_eff/2\n卡停顿↑ → {rx_m[4]:.1f}M",
        xy=(128, rx_m[4]), xytext=(68, 43.0), fontsize=8.2, color="#9a3412",
        arrowprops=dict(arrowstyle="->", color="#9a3412", lw=1.0),
    )
    ax1.text(
        150, 18.6,
        f"生产规则：每批 rx_burst 至多发 1 笔最新 taken（1500 B B=64 反向写 {b64_1500_wr:.2f}G）",
        ha="right", fontsize=7.8, fontweight="bold", color="#136f45",
        bbox=dict(boxstyle="round,pad=0.22", fc="#eaf7ee", ec="#15803d", alpha=0.92),
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
