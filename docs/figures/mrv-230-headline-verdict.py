"""MRV-230 Figure 1: 3-second headline verdict comparing Intel DSA Portal (wq2.0)
regimes against CN106 PCIe BAR4 (MRV-196) on co-located Socket-1 neighbor memory
bandwidth (6c STREAM Triad) and L3 cache tail latency (16 MiB, K=32 window p99).

Highlights the physical reversal: 8c pure-portal NOOP/64B is already 100%
backpressured (IRP0=7.71, M2PCIe0=99.9%) but kept below the 330-430 CHA TOR
cliff by the 4 KiB (64-line) narrow window, whereas CN106 BAR4 exposes 32,768
lines (CHA 654) and DSA 64K flood + active DMA crosses the cliff (CHA 472-489).

Reads raw verified n=3 runs directly from:
  - logs/mrv230-2026-11-27/runs.jsonl
  - logs/mrv230-2026-11-27/diag_runs.jsonl
  - logs/mrv196-2026-11-25/runs.jsonl
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
M230_MAIN = ROOT / "logs/mrv230-2026-11-27/runs.jsonl"
M230_DIAG = ROOT / "logs/mrv230-2026-11-27/diag_runs.jsonl"
M196_MAIN = ROOT / "logs/mrv196-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def load_m230():
    by = defaultdict(list)
    for path in (M230_MAIN, M230_DIAG):
        for ln in path.read_text().splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            assert r["accepted"] is True
            assert r["host_pmu_min_pct"] == 100.0
            assert r["page_faults"] == 0 and r["victim_page_faults"] == 0 and r["aggr_page_faults"] == 0
            assert r["drain_ok"] is True
            assert r["dsa_comp_errors"] == 0 and r["dsa_verify_errors"] == 0
            assert r["dsa2_pmu"]["cpl_err"] == 0
            if r["chase"]:
                assert r["chase"]["bad_nodes"] == 0
            if r["stream"]:
                assert r["stream"]["verify_errors"] == 0
            by[r["label"]].append(r)
    return by


def load_m196():
    by = defaultdict(list)
    for ln in M196_MAIN.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        assert r["accepted"] is True
        assert r["host_pmu_min_pct"] == 100.0
        assert r["page_faults"] == 0 and r["victim_page_faults"] == 0 and r["aggr_page_faults"] == 0
        if r["chase"]:
            assert r["chase"]["bad_nodes"] == 0
        if r["stream"]:
            assert r["stream"]["verify_errors"] == 0
        if r["aggr"] == "bar4":
            assert r["card_pmu"]["min_pct"] == 100.0
            cv = r["card_verify"]
            assert cv["zero_lines"] == 0 and cv["torn_lines"] == 0 and cv["bad_lines"] == 0
        by[r["label"]].append(r)
    return by


def main():
    m230 = load_m230()
    m196 = load_m196()

    t6c_base = med([r["stream"]["mean_gb_s"] for r in m230["t6c__idle"]])
    t6c_l1 = med([r["stream"]["mean_gb_s"] for r in m230["t6c__l1-spin_8c_b512"]])
    llc_p99_base = med([r["chase"]["p99_ns"] for r in m230["llc__idle"]])
    dram_p99_base = med([r["chase"]["p99_ns"] for r in m230["dram__idle"]])

    t6c_base_196 = med([r["stream"]["mean_gb_s"] for r in m196["t6c__idle"]])
    llc_p99_base_196 = med([r["chase"]["p99_ns"] for r in m196["llc__idle"]])

    # (src, key, label, color, kind)
    rows_spec = [
        ("m230", "noop-stride_8c_b512", "DSA 8 核空包狂发 (NOOP)\n143 Mpps · 4 KiB 窄窗 · 0 DMA", "#1a7f37", "ok"),
        ("m230", "memmove-64k-stride_x64B_8c_b512", "DSA 8 核 64B 狂发灌爆\n142 Mpps · 94.6% 丢弃 · 0.5G DMA", "#1a7f37", "ok_drop"),
        ("m230", "memmove-64k-credit_8c_b512", "DSA 8 核 64K 守信用*\n0.3 Mpps · 0% 丢弃 · 19.2G DMA", "#d97706", "credit"),
        ("m230", "memmove-64k-stride_x64K_cc_8c_b512", "DSA 8 核 64K 灌爆 (写 L3)\n117 Mpps · 99.9% 丢弃 · 7.0G DMA", "#ea580c", "cc_cliff"),
        ("m230", "memmove-64k-stride_8c_b512", "DSA 8 核 64K 灌爆 (写主存)\n117 Mpps · 99.9% 丢弃 · 6.9G DMA", "#dc2626", "dsa_cliff"),
        ("m196", "bar4_8c_max_b512", "对照：CN106 8 核无控 BAR4\n175.5 Mpps · 32,768 行宽窗 (MRV-196)", "#881337", "nic_cliff"),
    ]

    bw_drops, bw_gbs, imcs, llc_p99s, llc_ranges, dram_p99s = [], [], [], [], [], []
    llc_chas, dram_chas = [], []
    for src, key, _, _, _ in rows_spec:
        ds = m230 if src == "m230" else m196
        base_bw = t6c_base if src == "m230" else t6c_base_196
        t_runs = ds[f"t6c__{key}"]
        l_runs = ds[f"llc__{key}"]
        d_runs = ds[f"dram__{key}"]
        gb = med([r["stream"]["mean_gb_s"] for r in t_runs])
        drop = (base_bw - gb) / base_bw * 100.0
        imc = med([r["imc"]["tot_gb_s"] for r in t_runs])
        lp = [r["chase"]["p99_ns"] for r in l_runs]
        dp = med([r["chase"]["p99_ns"] for r in d_runs])
        lc = med([r["cha"]["occ_entries"] for r in l_runs])
        dc = med([r["cha"]["occ_entries"] for r in d_runs])
        bw_drops.append(drop)
        bw_gbs.append(gb)
        imcs.append(imc)
        llc_p99s.append(med(lp))
        llc_ranges.append((min(lp), max(lp)))
        dram_p99s.append(dp)
        llc_chas.append(lc)
        dram_chas.append(dc)

    y = np.arange(len(rows_spec))
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(15.4, 6.6), sharey=True, gridspec_kw={"width_ratios": [1.12, 1.05]}
    )
    bar_h = 0.56

    # ---- Panel A: Neighbor 6-core STREAM bandwidth drop ----
    ax1.axvline(5.0, color="#1a7f37", linestyle="--", lw=1.2, alpha=0.8, zorder=1)
    ax1.text(5.8, -0.52, "5% 干扰阈值线", fontsize=8.5, color="#1a7f37", va="center", ha="left")
    ax1.grid(axis="y", visible=False)
    ax1.grid(axis="x", alpha=0.28)

    for i, (_, _, _, col, kind) in enumerate(rows_spec):
        val = max(0.0, bw_drops[i])
        ax1.barh(i, max(val, 0.7), height=bar_h, color=col, alpha=0.90, zorder=3)
        if kind == "ok":
            ax1.text(
                6.2, i, f"-{val:.1f}%  (已满反压，64 行窄窗封顶 CHA {dram_chas[i]:.0f})",
                va="center", ha="left", fontsize=8.9, fontweight="bold", color="#116329",
            )
        elif kind == "ok_drop":
            ax1.text(
                6.2, i, f"-{val:.1f}%  (94.6% 丢弃 + 64 行窄窗 CHA {dram_chas[i]:.0f})",
                va="center", ha="left", fontsize=8.9, fontweight="bold", color="#116329",
            )
        elif kind == "credit":
            ax1.text(
                val + 1.3, i, f"-{val:.1f}%*\n(MEMMOVE 双倍主存吃满 {imcs[i]:.1f}G)",
                va="center", ha="left", fontsize=8.6, fontweight="bold", color="#b45309",
            )
        elif kind == "cc_cliff":
            ax1.text(
                val + 1.3, i, f"-{val:.1f}%  (IMC 仅 {imcs[i]:.1f}G 未满!)",
                va="center", ha="left", fontsize=9.0, fontweight="bold", color="#c2410c",
            )
        elif kind == "dsa_cliff":
            ax1.text(
                val + 1.3, i, f"-{val:.1f}%  (IMC 仅 {imcs[i]:.1f}G 未满!)",
                va="center", ha="left", fontsize=9.1, fontweight="bold", color="#b91c1c",
            )
        else:
            ax1.text(
                val + 1.3, i, f"-{val:.1f}%  (IMC {imcs[i]:.1f}G, CHA {dram_chas[i]:.0f})",
                va="center", ha="left", fontsize=9.0, fontweight="bold", color="#881337",
            )

    ax1.set_xlim(0, 118)
    ax1.set_xticks([0, 20, 40, 60, 80])
    ax1.set_xticklabels(["0%", "-20%", "-40%", "-60%", "-80%"], fontsize=9.4)
    ax1.set_yticks(y)
    ax1.set_yticklabels([r[2] for r in rows_spec], fontsize=9.1, fontweight="bold")
    ax1.invert_yaxis()
    ax1.set_xlabel(f"邻居 6 核内存带宽下降（相对空载 {t6c_base:.1f} / l1-spin 8核 {t6c_l1:.1f} GB/s，越短越好）", fontsize=9.2, labelpad=8)
    ax1.set_title("A. 邻居 6 核内存带宽：窄窗纯丢弃守住基线，叠加 64K DMA 才掉崖", fontsize=10.4, fontweight="bold", pad=12)

    # ---- Panel B: Neighbor L3 p99 tail latency (K=32 window average) ----
    ax2.axvline(llc_p99_base, color="#6e7781", linestyle=":", lw=1.2, zorder=1)
    ax2.text(llc_p99_base + 14, -0.52, f"空载基线 {llc_p99_base:.1f} ns", fontsize=8.5, color="#57606a", va="center")
    ax2.grid(axis="y", visible=False)
    ax2.grid(axis="x", alpha=0.28)

    for i, (_, _, _, col, kind) in enumerate(rows_spec):
        p99 = llc_p99s[i]
        lo, hi = llc_ranges[i]
        bar_col = "#64748b" if kind == "credit" else col
        ax2.barh(i, p99, height=bar_h, color=bar_col, alpha=0.88, zorder=3)
        if kind in ("ok", "ok_drop"):
            ax2.text(
                p99 + 14, i, f"{p99:.1f} ns  (无变化, CHA {llc_chas[i]:.0f} < 330)",
                va="center", ha="left", fontsize=9.0, fontweight="bold", color="#116329",
            )
        elif kind == "credit":
            ax2.text(
                p99 + 14, i, f"{p99:.1f} ns  (L3 无影响; 主存 K=8 p99 {dram_p99s[i]:.0f} ns*)",
                va="center", ha="left", fontsize=8.7, fontweight="bold", color="#b45309",
            )
        elif kind == "cc_cliff":
            dr_ratio = dram_p99s[i] / dram_p99_base
            ax2.text(
                p99 + 14, i, f"{p99:.1f} ns  (L3 轮 CHA {llc_chas[i]:.0f}<330; 主存 K=8 p99 {dram_p99s[i]:.0f} ns, {dr_ratio:.1f}x!)",
                va="center", ha="left", fontsize=8.5, fontweight="bold", color="#c2410c",
            )
        elif kind == "dsa_cliff":
            ratio = p99 / llc_p99_base
            ax2.text(
                p99 + 14, i, f"{p99:.0f} ns  ({ratio:.1f}x 暴涨, n=3 范围 {lo:.0f}–{hi:.0f} 双峰)",
                va="center", ha="left", fontsize=9.0, fontweight="bold", color="#b91c1c",
            )
        else:
            ratio = p99 / llc_p99_base_196
            ax2.text(
                p99 + 14, i, f"{p99:.0f} ns  ({ratio:.0f}x 暴涨)",
                va="center", ha="left", fontsize=9.5, fontweight="bold", color="#881337",
            )

    ax2.set_xlim(0, 960)
    ax2.set_xticks([0, 200, 400, 600, 800])
    ax2.set_xticklabels(["0", "200 ns", "400 ns", "600 ns", "800 ns"], fontsize=9.4)
    ax2.set_xlabel("邻居 L3 缓存尾延迟 p99（16 MiB 随机读，K = 32 跳窗口平均，越短越好）", fontsize=9.2, labelpad=8)
    ax2.set_title("B. 邻居 L3 尾延迟（K=32 窗口 p99）：单开无感，混流后暴涨近 4 倍", fontsize=10.4, fontweight="bold", pad=12)

    fig.suptitle(
        "MRV-230 核心结论：4 KiB 窄门户将纯洪峰/丢弃压在 CHA 悬崖下，但叠加活跃 DMA 仍堵爆片上队列",
        fontsize=11.8, fontweight="bold", y=0.972,
    )
    fig.text(
        0.5, 0.012,
        "*注：NOOP/64B 8 核在 142–143 Mpps 已 100% 满反压（IRP0=7.71, M2PCIe0=99.9%），靠 4 KiB (64 行) 窄门户窗将 CHA 封顶在 313–320（恰好压在 330–430 悬崖下），\n而 CN106 BAR4 32,768 行宽窗将 CHA 灌至 654。Socket 1 为双通道 DDR5 (~63.7 GB/s)，MEMMOVE 具 2× 主存读写放大；L3/DRAM p99 为 K=32 / K=8 跳窗口均值（n=3 中位数）。",
        ha="center", va="bottom", fontsize=8.0, color="#57606a",
    )
    fig.tight_layout(rect=[0.0, 0.085, 1.0, 0.93])
    png = mrvplot.HERE / "mrv-230-headline-verdict.png"
    fig.savefig(png)
    fig.savefig(mrvplot.HERE / "mrv-230-headline-verdict.svg")
    plt.close(fig)
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
