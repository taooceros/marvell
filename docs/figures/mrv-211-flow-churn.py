"""MRV-211 Figure 2: 64 -> 16,384 transient flows across 1 ms / 500 us / 100 us slots (W2, CEIO Fig. 12)
plus host single-core CPU cycle linear decomposition.

100% dynamically computed from `logs/mrv211-2026-11-26/runs.jsonl.gz` (n=3 verified runs per point).
"""
import gzip
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv211-2026-11-26/runs.jsonl.gz"


def load_jsonl(path: Path):
    if not path.exists() and path.suffix == ".gz":
        path = path.with_suffix("")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def med(xs):
    return statistics.median(xs)


def assert_verified(r):
    assert r.get("accepted") is True and not r.get("hard_errors") and not r.get("clock_errors")
    c, k = r["client"], r["card"]
    for fld in ("lost", "bad", "torn", "ooo", "send_err"):
        assert int(c[fld]) == 0, f"client.{fld} != 0"
    for fld in ("bad_wire", "bad_sdp", "eth_tx_drops", "sdp_tx_drops", "card_buf_drops"):
        assert int(k[fld]) == 0, f"card.{fld} != 0"
    assert float(r["card_pmu_delta"]["min_running_pct"]) >= 99.95
    h = r["host"]
    if h is not None:
        for fld in ("bad", "torn", "ooo", "tx_drops"):
            assert int(h[fld]) == 0, f"host.{fld} != 0"
        assert int(h["sdp_port_stats"]["rx_nombuf"]) == 0
        assert int(h["pmu"]["page_faults"]) == 0
        assert float(h["pmu"]["min_running_pct"]) >= 99.95


def main():
    runs = load_jsonl(RUNS_PATH)
    by_key = defaultdict(list)
    card_echo_vals = []
    u_runs, c_runs = [], []
    for r in runs:
        assert_verified(r)
        sp = r["spec"]
        if sp["suite"] == "w2":
            if sp["policy"] == "card_echo":
                card_echo_vals.append(float(r["client"]["tx_gbps_wire"]))
            else:
                by_key[(sp["policy"], int(sp["slot_us"]), int(sp["total_flows"]))].append(r)
                if sp["policy"] == "unified":
                    u_runs.append(r)
                elif sp["policy"] == "ceio":
                    c_runs.append(r)

    assert len(card_echo_vals) == 6, f"Expected 6 card_echo runs, got {len(card_echo_vals)}"
    assert len(u_runs) == 81 and len(c_runs) == 81
    card_ceil = med(card_echo_vals)

    # Dynamically compute host CPU cycle ceiling & CEIO linear regression across all 162 runs
    u_gbps_med = med([float(r["client"]["tx_gbps_wire"]) for r in u_runs])
    u_cyc_arr = np.array([float(r["host"]["pmu"]["cycles"]) / float(r["host"]["rx_pkts"]) for r in u_runs])
    u_cyc_mean = float(np.mean(u_cyc_arr))
    u_cyc_std = float(np.std(u_cyc_arr, ddof=1))

    c_slow_frac = np.array([float(r["client"]["slow_frac_pct"]) / 100.0 for r in c_runs])
    c_cyc_arr = np.array([float(r["host"]["pmu"]["cycles"]) / float(r["host"]["rx_pkts"]) for r in c_runs])
    slope, intercept = np.polyfit(c_slow_frac, c_cyc_arr, 1)
    pred = intercept + slope * c_slow_frac
    r2 = 1.0 - float(np.sum((c_cyc_arr - pred) ** 2) / np.sum((c_cyc_arr - np.mean(c_cyc_arr)) ** 2))
    fixed_tax = intercept - u_cyc_mean
    fixed_share = fixed_tax / (fixed_tax + slope) * 100.0
    slow_share = slope / (fixed_tax + slope) * 100.0

    flows = [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384]
    flow_labels = ["64", "128", "256", "512", "1K", "2K", "4K", "8K", "16K"]
    slots = [
        (1000, "(a) 1 ms 切换周期（慢切换）"),
        (500, "(b) 500 µs 切换周期（中速切换）"),
        (100, "(c) 100 µs 切换周期（极速瞬变）"),
    ]

    fig, axes_grid = plt.subplots(2, 2, figsize=(14.0, 8.6))
    tput_axes = [axes_grid[0, 0], axes_grid[0, 1], axes_grid[1, 0]]
    ax_cpu = axes_grid[1, 1]
    x_pos = np.arange(len(flows))

    for idx, (ax, (slot_us, title)) in enumerate(zip(tput_axes, slots)):
        ax.axvline(2.0, color="#94a3b8", linestyle="--", linewidth=1.3, zorder=1)
        ax.text(2.14, 49.8, "CEIO 快道上限\n（256 流）", fontsize=8.4, color="#475569", va="center")
        for pol, label, color, mk in (
            ("unified", "统一信用环（本文）", "#15803d", "o"),
            ("ceio", "CEIO 双路径", "#dc2626", "s"),
        ):
            meds, mins, maxs = [], [], []
            for nf in flows:
                rs = by_key[(pol, slot_us, nf)]
                assert len(rs) == 3, f"Expected n=3 for W2 {pol} {slot_us}us {nf}f, got {len(rs)}"
                vs = [float(r["client"]["tx_gbps_wire"]) for r in rs]
                meds.append(med(vs))
                mins.append(min(vs))
                maxs.append(max(vs))
            ax.fill_between(x_pos, mins, maxs, color=color, alpha=0.14, zorder=2)
            ax.plot(x_pos, meds, marker=mk, color=color, linewidth=2.4, markersize=5.5, label=label, zorder=3)

        u16 = med([float(r["client"]["tx_gbps_wire"]) for r in by_key[("unified", slot_us, 16384)]])
        c16 = med([float(r["client"]["tx_gbps_wire"]) for r in by_key[("ceio", slot_us, 16384)]])
        slow16 = med([float(r["client"]["slow_frac_pct"]) for r in by_key[("ceio", slot_us, 16384)]])
        fb16 = med([float(r["card"]["ceio"]["slow_fallback_new_flows"]) for r in by_key[("ceio", slot_us, 16384)]])
        gain_pct = (u16 - c16) / c16 * 100.0
        note = f"16K 流：Unified +{gain_pct:.1f}%\n({u16:.1f} vs {c16:.1f} Gbps)｜CEIO 慢道 {slow16:.1f}%"
        if fb16 > 0:
            note += f"\n表满新流直落慢道 {fb16:,.0f} 次"
        ax.annotate(
            note,
            xy=(8, c16),
            xytext=(2.22, 34.0 if fb16 > 0 else 34.8),
            arrowprops={"arrowstyle": "->", "color": "#dc2626", "lw": 1.2, "connectionstyle": "arc3,rad=0.12"},
            fontsize=8.2,
            fontweight="bold",
            color="#991b1b",
            bbox={"boxstyle": "round,pad=0.28", "fc": "#fef2f2", "ec": "#fecaca", "lw": 0.9},
        )
        if idx == 0:
            ax.text(
                5.15,
                u_gbps_med + 1.4,
                f"单主机核标准 SDP 软件上限 ~{u_gbps_med:.1f} Gbps（{u_cyc_mean:.1f} cyc/包）",
                ha="center",
                va="bottom",
                fontsize=8.3,
                fontweight="bold",
                color="#15803d",
                bbox={"fc": "white", "ec": "none", "pad": 0.8, "alpha": 0.88},
            )
        else:
            ax.text(
                7.9,
                u16 + 1.2,
                f"单核 SDP 上限 ~{u16:.1f} Gbps",
                ha="right",
                va="bottom",
                fontsize=8.5,
                fontweight="bold",
                color="#15803d",
            )

        ax.set_xticks(x_pos)
        ax.set_xticklabels(flow_labels, fontsize=9.2)
        ax.set_xlabel("总并发流数（每周期 16 条活跃）", fontsize=9.8)
        ax.set_ylabel("实测线速吞吐（Gbps，512 B 包）", fontsize=9.8)
        ax.set_title(title, fontsize=11.0, fontweight="bold", pad=8)
        ax.set_ylim(32, 62.5)

    # Panel (d): Host single-core cpu40 cycle decomposition across all 162 runs
    ax_cpu.axhline(u_cyc_mean, color="#15803d", linestyle="--", linewidth=1.6, zorder=2)
    ax_cpu.scatter(
        np.zeros_like(u_cyc_arr),
        u_cyc_arr,
        color="#15803d",
        s=18,
        alpha=0.55,
        zorder=3,
        label="统一信用环实测（81 轮）",
    )
    ax_cpu.scatter(
        c_slow_frac * 100.0,
        c_cyc_arr,
        color="#dc2626",
        s=18,
        alpha=0.55,
        zorder=3,
        label="CEIO 双路径实测（81 轮）",
    )
    xs_fit = np.linspace(0.0, 100.0, 101)
    ys_fit = intercept + slope * (xs_fit / 100.0)
    ax_cpu.plot(xs_fit, ys_fit, color="#991b1b", linewidth=2.2, zorder=4)
    ax_cpu.axhline(intercept, color="#94a3b8", linestyle=":", linewidth=1.2, zorder=1)
    ax_cpu.scatter([0.0], [intercept], facecolors="white", edgecolors="#991b1b", s=46, linewidths=1.8, zorder=5)

    # Vertical bracket 1: 0% slow fixed tax (+74.5 cyc/pkt, 68%)
    ax_cpu.annotate(
        "",
        xy=(4.5, intercept),
        xytext=(4.5, u_cyc_mean),
        arrowprops={"arrowstyle": "<->", "color": "#dc2626", "lw": 1.6},
        zorder=5,
    )
    ax_cpu.text(
        8.5,
        (u_cyc_mean + intercept) / 2.0,
        f"+{fixed_tax:.1f} cyc/包（占 {fixed_share:.0f}%）\n0% 慢道已由 8Q + SwMergeRing 锁定",
        va="center",
        ha="left",
        fontsize=8.3,
        fontweight="bold",
        color="#991b1b",
        bbox={"boxstyle": "round,pad=0.28", "fc": "#fef2f2", "ec": "#fecaca", "lw": 0.9},
        zorder=5,
    )

    # Vertical bracket 2: 0% -> 100% slow increment (+35.1 cyc/pkt, 32%)
    y_100 = intercept + slope
    ax_cpu.annotate(
        "",
        xy=(103.0, y_100),
        xytext=(103.0, intercept),
        arrowprops={"arrowstyle": "<->", "color": "#475569", "lw": 1.5},
        zorder=5,
    )
    ax_cpu.annotate(
        f"+{slope:.1f} cyc/包（占 {slow_share:.0f}%）\n慢道拉取增量",
        xy=(102.5, (intercept + y_100) / 2.0),
        xytext=(54.0, 398.0),
        arrowprops={"arrowstyle": "->", "color": "#475569", "lw": 1.1, "connectionstyle": "arc3,rad=0.14"},
        va="center",
        ha="left",
        fontsize=8.1,
        fontweight="bold",
        color="#334155",
        bbox={"boxstyle": "round,pad=0.26", "fc": "#f8fafc", "ec": "#cbd5e1", "lw": 0.9},
        zorder=5,
    )

    ax_cpu.text(
        4.0,
        463.0,
        f"CEIO 回归（81 轮）：cyc/pkt = {intercept:.1f} + {slope:.1f} × slow_frac（R² = {r2:.3f}）",
        va="center",
        ha="left",
        fontsize=8.5,
        fontweight="bold",
        color="#7f1d1d",
        bbox={"boxstyle": "round,pad=0.28", "fc": "#fff1f2", "ec": "#fecdd3", "lw": 0.9},
        zorder=5,
    )
    ax_cpu.text(
        98.5,
        u_cyc_mean - 5.0,
        f"Unified 单主机核 SDP 上限：{u_cyc_mean:.1f} ± {u_cyc_std:.1f} cyc/包",
        va="top",
        ha="right",
        fontsize=8.3,
        fontweight="bold",
        color="#15803d",
    )

    ax_cpu.set_xlim(-5, 108)
    ax_cpu.set_ylim(318, 480)
    ax_cpu.set_xticks([0, 20, 40, 60, 80, 100])
    ax_cpu.set_xticklabels(["0%", "20%", "40%", "60%", "80%", "100%"], fontsize=9.2)
    ax_cpu.set_xlabel("慢道流量占比 slow_frac（%）", fontsize=9.8)
    ax_cpu.set_ylabel("主机单核 CPU 开销（cyc/包）", fontsize=9.8)
    ax_cpu.set_title("(d) 主机单核 CPU 周期拆解（全 162 轮实测回归）", fontsize=11.0, fontweight="bold", pad=8)

    handles, labels = tput_axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, fontsize=10.0, bbox_to_anchor=(0.5, 0.905))
    fig.suptitle(
        f"场景 2（64 → 16,384 瞬变流）：统一信用环稳守单主机核 SDP 上限 ~{u_gbps_med:.1f} Gbps，CEIO 固定双环税锁定 {fixed_share:.0f}% 劣势（卡端直回上限 {card_ceil:.1f} Gbps）",
        fontsize=11.4,
        fontweight="bold",
        y=0.978,
    )
    fig.text(
        0.5,
        0.938,
        "固定条件：1 主机核 cpu40 @ 3.90 GHz（Uncore 2.20 GHz）+ 4 卡核（CEIO 另占第 5 核）｜W = 1,024，发送批 ≤ 16，DPDK burst = 32｜512 B 包，每周期 16 条活跃流，n = 3 中位数",
        ha="center",
        va="center",
        fontsize=8.6,
        color="#475569",
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.875])
    png = mrvplot.HERE / "mrv-211-flow-churn.png"
    svg = mrvplot.HERE / "mrv-211-flow-churn.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
