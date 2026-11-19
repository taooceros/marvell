"""MRV-97: Single-variable W=1 cache & DDIO latency and PMU evidence plots.

Reads verified raw data from logs/mrv97-2026-11-19/runs.jsonl (40 runs: 8 conditions x 5 interleaved reps,
all lost = bad = torn = 0, all PMU counters 100.0% running with zero multiplexing).

Usage:
    uv run --with matplotlib python /home/hongtao/dev/marvell-mrv97/docs/figures/mrv-97-cache-ddio.py
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mrvplot import plt, save  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv97-2026-11-19/runs.jsonl"
GHZ_THRESH = 3.85


def load_runs():
    rows = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    assert len(rows) == 40, f"expected 40 runs, got {len(rows)}"
    for r in rows:
        assert r["host"]["lost"] == 0 and r["host"]["bad"] == 0 and r["host"]["torn"] == 0
        for d in (r["host_perf_s1"], r["host_perf_cpu40"], r["card"]["tad"], r["card"]["core"]):
            for v in d.values():
                assert v["pct"] == 100.0, f"PMU multiplexed: {r['id']} {v}"
    by_cond = defaultdict(list)
    for r in rows:
        by_cond[r["cond"]].append(r)
    return by_cond


def split_vals(rs, fn):
    hi = [fn(r) for r in rs if r["clocks"]["cpu40_ghz"] >= GHZ_THRESH]
    lo = [fn(r) for r in rs if r["clocks"]["cpu40_ghz"] < GHZ_THRESH]
    return hi, lo


def plot_latency(by_cond):
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(13.6, 4.7))

    # --- Panel (a): Control A (W=1 RTT, host L1D src vs cold DRAM src) ---
    a_specs = [
        ("base", "基线\n(源热在主机 L1D)", "#2563eb"),
        ("A_cold_src", "发前刷冷源行\n(源在主机 DRAM)", "#dc2626"),
    ]
    a_meds = []
    for i, (cond, lbl, col) in enumerate(a_specs):
        hi, lo = split_vals(by_cond[cond], lambda r: r["host"]["rtt_ns"]["p50"])
        med = statistics.median(hi)
        a_meds.append(med)
        ax1.bar(i, med, width=0.46, color=col, alpha=0.86)
        ax1.scatter([i - 0.05] * len(hi), hi, color="#0f172a", s=28, zorder=4,
                    label="3.85–3.90 GHz" if i == 0 else None)
        if lo:
            ax1.scatter([i + 0.07] * len(lo), lo, facecolors="none", edgecolors="#64748b",
                        linewidths=1.4, s=34, zorder=4, label="2.91–3.74 GHz (同机降频)" if i == 0 else None)
        ax1.text(i, max(hi + lo) + 22, f"{med:.1f} ns", ha="center", va="bottom", fontsize=10, fontweight="bold")
    da = a_meds[1] - a_meds[0]
    ax1.annotate(
        f"+{da:.1f} ns",
        xy=(0.76, a_meds[1] - 6), xytext=(0.35, 1145),
        ha="center", fontsize=10.5, fontweight="bold", color="#b91c1c",
        arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.4),
    )
    ax1.set_xticks(range(2), [s[1] for s in a_specs], fontsize=9.2)
    ax1.set_ylabel("W=1 往返延迟 p50 (ns)")
    ax1.set_ylim(0, 1280)
    ax1.set_title("(a) 发端从 cache 写：L1D 读源省 ~117 ns", fontsize=10.2, fontweight="bold")
    ax1.legend(loc="lower right", fontsize=7.8, framealpha=0.92)

    # --- Panel (b): Control B (card_read_ns, B0 hot L3 vs B1 cold-before vs B2 DRAM ctrl) ---
    b_specs = [
        ("B0_card_hot_wait", "B0 热态\n(BAR4 环在卡 L3)", "#2563eb"),
        ("B1_card_cold_pre", "B1 写前刷冷\n(进卡分配进 L3)", "#0284c7"),
        ("B2_card_dram_ctrl", "B2 写后刷冷\n(卡 DRAM 对照)", "#dc2626"),
    ]
    b_meds = []
    for i, (cond, lbl, col) in enumerate(b_specs):
        hi, lo = split_vals(by_cond[cond], lambda r: r["card"]["card_read_ns"]["p50"])
        med = statistics.median(hi)
        b_meds.append(med)
        ax2.bar(i, med, width=0.50, color=col, alpha=0.86)
        ax2.scatter([i - 0.05] * len(hi), hi, color="#0f172a", s=26, zorder=4)
        if lo:
            ax2.scatter([i + 0.06] * len(lo), lo, facecolors="none", edgecolors="#64748b", linewidths=1.4, s=32, zorder=4)
        ax2.text(i, max(hi + lo) + 6, f"{med:.0f} ns", ha="center", va="bottom", fontsize=10, fontweight="bold")
    db = b_meds[2] - b_meds[0]
    ax2.annotate(
        f"+{db:.0f} ns\n(DRAM vs L3)",
        xy=(1.74, b_meds[2] - 2), xytext=(1.02, 188),
        ha="center", fontsize=9.5, fontweight="bold", color="#b91c1c",
        arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.4),
    )
    ax2.text(1, b_meds[1] * 0.45, "持平\n(-2 ns)", ha="center", va="center", fontsize=9, fontweight="bold", color="white")
    ax2.set_xticks(range(3), [s[1] for s in b_specs], fontsize=8.8)
    ax2.set_ylabel("卡核单次读延迟 p50 (ns，含计时围栏)")
    ax2.set_ylim(0, 232)
    ax2.set_title("(b) 去程进卡写到卡 L3：冷行也分配进 L3，省 ~75 ns", fontsize=10.0, fontweight="bold")

    # --- Panel (c): Control C (host_read_ns, C0 hot LLC vs C1 cold-before vs C2 DRAM ctrl) ---
    c_specs = [
        ("C0_host_hot_wait", "C0 热态\n(回复在主机 LLC)", "#2563eb"),
        ("C1_host_cold_pre", "C1 写前刷冷\n(DDIO 分配进 LLC)", "#0284c7"),
        ("C2_host_dram_ctrl", "C2 写后刷冷\n(主机 DRAM 对照)", "#dc2626"),
    ]
    c_meds = []
    for i, (cond, lbl, col) in enumerate(c_specs):
        hi, lo = split_vals(by_cond[cond], lambda r: r["host"]["read_ns"]["p50"])
        med = statistics.median(hi)
        c_meds.append(med)
        ax3.bar(i, med, width=0.50, color=col, alpha=0.86)
        ax3.scatter([i - 0.05] * len(hi), hi, color="#0f172a", s=26, zorder=4)
        if lo:
            ax3.scatter([i + 0.06] * len(lo), lo, facecolors="none", edgecolors="#64748b", linewidths=1.4, s=32, zorder=4)
        ax3.text(i, max(hi + lo) + 5, f"{med:.1f} ns", ha="center", va="bottom", fontsize=10, fontweight="bold")
    dc = c_meds[2] - c_meds[0]
    ax3.annotate(
        f"+{dc:.1f} ns\n(DRAM vs LLC)",
        xy=(1.74, c_meds[2] - 2), xytext=(1.02, 138),
        ha="center", fontsize=9.5, fontweight="bold", color="#b91c1c",
        arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.4),
    )
    ax3.text(1, c_meds[1] * 0.45, "0.0 ns 差", ha="center", va="center", fontsize=8.8, fontweight="bold", color="white")
    ax3.set_xticks(range(3), [s[1] for s in c_specs], fontsize=8.8)
    ax3.set_ylabel("主机核单次读延迟 p50 (ns)")
    ax3.set_ylim(0, 176)
    ax3.set_title("(c) 回程主机 DDIO 写到 LLC：冷行也进 LLC，省 ~87 ns", fontsize=10.0, fontweight="bold")

    fig.suptitle(
        "MRV-97 单变量 W=1 缓存对照（MOVDIR64B + 1 卡核写合并，64 B，批=1，cpu40=3.9 GHz / uncore=2.2 GHz / 卡=2.0 GHz，lost=bad=torn=0）",
        fontsize=10.3, fontweight="bold", y=0.99,
    )
    return save(fig, "mrv-97-w1-cache-latency")


def plot_pmu(by_cond):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 4.6))

    # --- Panel (a): Card PMU for B0, B1, B2 ---
    b_conds = ["B0_card_hot_wait", "B1_card_cold_pre", "B2_card_dram_ctrl"]
    b_labels = ["B0 热 L3", "B1 写前刷冷\n(进卡分配 L3)", "B2 写后刷冷\n(卡 DRAM 对照)"]
    w = 0.24
    xs = list(range(len(b_conds)))

    def med_card(cond, bucket, key):
        rs = [r for r in by_cond[cond] if r["clocks"]["cpu40_ghz"] >= GHZ_THRESH]
        return statistics.median([r["card"][bucket][key]["val"] / r["card"]["pmu_rounds"] for r in rs])

    dss_wr = [med_card(c, "tad", "tad_req_msh_out_dss_wr") for c in b_conds]
    alloc_ltg = [med_card(c, "tad", "tad_alloc_ltg") for c in b_conds]
    l3_miss = [med_card(c, "core", "ll_cache_miss_rd") for c in b_conds]

    ax1.bar([x - w for x in xs], dss_wr, width=w, color="#64748b", label="tad_req_msh_out_dss_wr (刷冷写 DRAM)")
    ax1.bar(xs, alloc_ltg, width=w, color="#2563eb", label="tad_alloc_ltg (卡 L3 128 B 块分配)")
    ax1.bar([x + w for x in xs], l3_miss, width=w, color="#dc2626", label="ll_cache_miss_rd (卡核 L3 读未命中)")
    for x, v1, v2, v3 in zip(xs, dss_wr, alloc_ltg, l3_miss):
        ax1.text(x - w, v1 + 0.03, f"{v1:.2f}", ha="center", va="bottom", fontsize=8.4)
        ax1.text(x, v2 + 0.03, f"{v2:.2f}", ha="center", va="bottom", fontsize=8.4, fontweight="bold", color="#1d4ed8")
        ax1.text(x + w, v3 + 0.03, f"{v3:.2f}", ha="center", va="bottom", fontsize=8.4, color="#b91c1c")

    ax1.set_xticks(xs, b_labels, fontsize=9.2)
    ax1.set_ylabel("每轮事件数 (次 / 条)")
    ax1.set_ylim(0, 1.48)
    ax1.set_title("(a) 卡端 PMU：B1 刷冷落 DRAM 后，入站写直接分配进卡 L3 (+1.00/条)", fontsize=9.8, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=8.1, framealpha=0.92)

    # --- Panel (b): Host PMU for base, A, C0, C1, C2 ---
    h_conds = ["base", "A_cold_src", "C0_host_hot_wait", "C1_host_cold_pre", "C2_host_dram_ctrl"]
    h_labels = ["base\n(热源)", "A\n(冷源)", "C0\n(热 LLC)", "C1\n(写前刷冷)", "C2\n(写后 DRAM)"]
    xs2 = list(range(len(h_conds)))

    def med_host_s1(cond, key):
        rs = [r for r in by_cond[cond] if r["clocks"]["cpu40_ghz"] >= GHZ_THRESH]
        return statistics.median([r["host_perf_s1"][key]["val"] / r["total_rounds"] for r in rs])

    def med_host_c40(cond, key):
        rs = [r for r in by_cond[cond] if r["clocks"]["cpu40_ghz"] >= GHZ_THRESH]
        return statistics.median([r["host_perf_cpu40"][key]["val"] / r["total_rounds"] for r in rs])

    hit_i = [med_host_s1(c, "unc_cha_tor_inserts.io_hit_itom") for c in h_conds]
    mis_i = [med_host_s1(c, "unc_cha_tor_inserts.io_miss_itom") for c in h_conds]
    l3_m = [med_host_c40(c, "mem_load_retired.l3_miss") for c in h_conds]

    ax2.bar([x - w for x in xs2], hit_i, width=w, color="#2563eb", label="io_hit_itom (DDIO 命中 LLC 写)")
    ax2.bar(xs2, mis_i, width=w, color="#0284c7", label="io_miss_itom (DDIO 冷行分配进 LLC)")
    ax2.bar([x + w for x in xs2], l3_m, width=w, color="#dc2626", label="cpu40 L3_miss (主机核读回主存)")
    for x, v1, v2, v3 in zip(xs2, hit_i, mis_i, l3_m):
        if v1 >= 0.05:
            ax2.text(x - w, v1 + 0.03, f"{v1:.2f}", ha="center", va="bottom", fontsize=8.0, color="#1d4ed8")
        if v2 >= 0.05:
            ax2.text(x, v2 + 0.03, f"{v2:.2f}", ha="center", va="bottom", fontsize=8.0, fontweight="bold", color="#0369a1")
        ax2.text(x + w, v3 + 0.03, f"{v3:.2f}", ha="center", va="bottom", fontsize=8.0,
                 fontweight="bold" if v3 > 0.5 else "normal", color="#b91c1c" if v3 > 0.5 else "#64748b")

    ax2.set_xticks(xs2, h_labels, fontsize=9.0)
    ax2.set_ylabel("每轮事件数 (次 / 条)")
    ax2.set_ylim(0, 1.48)
    ax2.set_title("(b) 主机 PMU：C1 走 DDIO io_miss_itom 进 LLC，L3_miss 仅 0.04", fontsize=9.7, fontweight="bold")
    ax2.legend(loc="upper center", ncol=1, fontsize=8.0, framealpha=0.92)

    fig.suptitle(
        "MRV-97 双端硬件 PMU 实证（所有事件 100.0% 采样无复用；取 cpu40 ≥ 3.85 GHz 稳频子集 n=3..4 中位数）",
        fontsize=10.3, fontweight="bold", y=0.99,
    )
    return save(fig, "mrv-97-pmu-evidence")


def main():
    by_cond = load_runs()
    p1 = plot_latency(by_cond)
    p2 = plot_pmu(by_cond)
    print("Wrote:", p1, p2)


if __name__ == "__main__":
    main()
