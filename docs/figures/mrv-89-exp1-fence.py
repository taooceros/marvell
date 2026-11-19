#!/usr/bin/env python3
"""MRV-89 Exp 1 chart: MOVDIR64B parallelism & throughput vs SFENCE interval K.

Reads raw verified runs from logs/mrv89-2026-11-19/runs.jsonl and writes
docs/figures/mrv-89-exp1-fence.{png,svg} using mrvplot styling.
"""
import collections
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mrvplot  # noqa: E402, F401
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

RUNS = ROOT / "logs/mrv89-2026-11-19/runs.jsonl"
PCIE_WIRE_MOPS = 179.02  # 32 GT/s * 4 * (128/130) / (64 + 24 B TLP)

ORDER = ["k1", "k2", "k4", "k8", "k16", "k32", "k64", "k128", "k256", "kinf"]
LABELS = ["1", "2", "4", "8", "16", "32", "64", "128", "256", "∞\n(无栅栏)"]


def main():
    rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
    exp1 = collections.defaultdict(list)
    for r in rows:
        if r.get("exp") == "exp1_fence":
            assert r["card"]["mismatches"] == 0 and r["card"]["torn_cells"] == 0
            assert r["host_min_pct"] >= 99.9 and r["card"]["min_pct"] >= 99.9
            exp1[r["cond"]].append(r)

    xs = np.arange(len(ORDER))
    med_mops, med_cha, med_res, med_irp = [], [], [], []
    all_mops = []
    for k in ORDER:
        rs = exp1[k]
        assert len(rs) >= 3
        m = [r["mops"] for r in rs]
        med_mops.append(statistics.median(m))
        med_cha.append(statistics.median(r["cha"]["inflight"] for r in rs))
        med_res.append(statistics.median(r["cha"]["residency_ns"] for r in rs))
        med_irp.append(statistics.median(r["irp1"]["inflight"] for r in rs))
        all_mops.append(m)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.1))

    # --- Left panel: Throughput (Mops/s & decimal GB/s) ---
    ax1.axhline(PCIE_WIRE_MOPS, color="#64748b", ls="--", lw=1.3,
                label=f"Gen5 ×4 64B 线速上限 ({PCIE_WIRE_MOPS:.1f} M/s · 11.46 GB/s)")
    ax1.axvspan(5.5, 9.45, color="#eff6ff", alpha=0.85, zorder=0)
    ax1.text(7.5, 24, "链路饱和区\n175.2 M/s · 11.21 GB/s\n(97.9% 线速)",
             ha="center", va="bottom", fontsize=8.5, color="#1d4ed8")

    for i, vals in enumerate(all_mops):
        ax1.scatter([xs[i]] * len(vals), vals, color="#93c5fd", s=22, zorder=3)
    ax1.plot(xs, med_mops, "-o", color="#2563eb", lw=2.2, ms=6, zorder=4,
             label="实测中位数 (单核 cpu40, 3.9 GHz)")

    annots_a = [
        (0, 6, 10, "left"),
        (4, 14, -4, "left"),
        (5, -6, -18, "center"),
        (6, 0, -18, "center"),
        (9, 0, -18, "center"),
    ]
    for idx, dx, dy, ha in annots_a:
        v = med_mops[idx]
        ax1.annotate(f"{v:.1f}", xy=(xs[idx], v), xytext=(dx, dy),
                     textcoords="offset points", ha=ha, fontsize=8.5,
                     fontweight="bold", color="#1e3a8a")

    ax1.set_xticks(xs)
    ax1.set_xticklabels(LABELS, fontsize=8.5)
    ax1.set_xlabel("每 K 条 MOVDIR64B 插入 1 次 SFENCE（写栅栏间隔 K）", fontsize=9.5)
    ax1.set_ylabel("直写速率 (M ops/s)", fontsize=9.5, color="#1e293b")
    ax1.set_ylim(0, 220)
    ax1.set_title("(a) 放宽 SFENCE 间隔：K≥64 单核即压满 Gen5 ×4", fontsize=10.5, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=8, frameon=True, facecolor="white", framealpha=0.95)

    ax1_r = ax1.twinx()
    ax1_r.spines["top"].set_visible(False)
    ax1_r.set_ylim(0, 220 * 64 / 1000.0)
    ax1_r.set_ylabel("有效载荷带宽 (GB/s, 十进制 10⁹ B/s)", fontsize=9, color="#475569")
    ax1_r.tick_params(axis="y", labelcolor="#475569")
    ax1_r.grid(False)

    # --- Right panel: Host CHA in-flight parallelism & CHA residency ---
    ax2.axvspan(5.5, 9.45, color="#eff6ff", alpha=0.85, zorder=0)
    l1, = ax2.plot(xs, med_cha, "-o", color="#2563eb", lw=2.2, ms=6,
                   label="主机 CHA 平均在途条数 (32 CHA 合计)")
    l2, = ax2.plot(xs, med_irp, "-s", color="#0f766e", lw=1.8, ms=5,
                   label="主机 IRP1 出站队列深度 (饱和 ≈ 7.65)")
    ax2.set_yscale("log")
    ax2.set_ylim(0.1, 1000)
    ax2.set_xticks(xs)
    ax2.set_xticklabels(LABELS, fontsize=8.5)
    ax2.set_xlabel("SFENCE 间隔 K", fontsize=9.5)
    ax2.set_ylabel("平均在途并行条数（对数坐标）", fontsize=9.5, color="#1e3a8a")
    ax2.set_title("(b) 硬件并行度与排队拐点（K≥64 后 CHA 排队积压）", fontsize=10.5, fontweight="bold")

    for idx, dx, dy, ha in [(0, 0, 9, "center"), (5, 0, 9, "center"), (6, 0, 9, "center"), (9, -4, 10, "right")]:
        ax2.annotate(f"{med_cha[idx]:.1f}", xy=(xs[idx], med_cha[idx]),
                     xytext=(dx, dy), textcoords="offset points", ha=ha,
                     fontsize=8.3, fontweight="bold", color="#1e3a8a")

    ax2_r = ax2.twinx()
    ax2_r.spines["top"].set_visible(False)
    l3, = ax2_r.plot(xs, med_res, "--^", color="#d97706", lw=1.8, ms=5.5,
                     label="CHA TOR 平均停留时间 (ns, 右轴)")
    ax2_r.set_ylim(0, 1850)
    ax2_r.set_ylabel("CHA TOR 平均停留时间 (ns)", fontsize=9, color="#b45309")
    ax2_r.tick_params(axis="y", labelcolor="#b45309")
    ax2_r.grid(False)

    ax2_r.annotate("无拥塞区: 103–109 ns", xy=(3.5, 107), xytext=(4.8, 340),
                   arrowprops=dict(arrowstyle="->", color="#b45309", lw=1.2),
                   fontsize=8.3, color="#b45309", ha="center",
                   bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#fcd34d", lw=0.8))
    ax2_r.annotate("1523 ns", xy=(9, med_res[9]), xytext=(-12, -2),
                   textcoords="offset points", ha="right", va="center",
                   fontsize=8.3, fontweight="bold", color="#b45309")

    lines = [l1, l2, l3]
    ax2.legend(lines, [l.get_label() for l in lines], loc="upper left",
               fontsize=7.9, frameon=True, facecolor="white", framealpha=0.95)

    fig.suptitle(
        "MRV-89 实验 1：单核 cpu40 (3.9 GHz, uncore 2.2 GHz) 静态模板顺序写 BAR4 槽 15 (2 MiB) · 卡核静默 · 3 次重复",
        fontsize=10.3, y=0.975, color="#334155",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    png = HERE / "mrv-89-exp1-fence.png"
    fig.savefig(png, dpi=200)
    fig.savefig(HERE / "mrv-89-exp1-fence.svg")
    plt.close(fig)
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
