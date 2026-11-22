#!/usr/bin/env python3
"""MRV-112 Figure 1: Core Single-Variable BAR4 Request-Ring Stride Comparison
Plots directly from logs/mrv112-2026-11-22/runs.jsonl and stamps/*.csv:
  Panel A: Closed-loop throughput across strides 64/128/4096/4224 B (WC2 W=64, WC2 W=128, WC4 W=64)
  Panel B: Card TAD in-flight concurrency (tad_req_occ) & per-request latency (tad_lat) + tad_snp
           (shows 128 B eliminates same-128 B-block MSHR queuing -81.9%, and contrasts id=81 fast vs WC4 4096 B slow)
  Panel C: Cross-core arrival spacing Δcard_t_in conditioned on slow bursts (RTT >= 3 us) vs all-sample
           (directly computed from stamps/*.csv, disclosing core-0 CLOCK_OFFSET -65% poll slowdown on WC2 4096 B)
"""
import collections
import csv
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import mrvplot  # noqa: E402

LOG_DIR = ROOT / "logs/mrv112-2026-11-22"
RUNS = LOG_DIR / "runs.jsonl"

recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
by_key = collections.defaultdict(list)
for r in recs:
    by_key[(r["exp"], r["cond"])].append(r)


def med(exp, cond, fn):
    return statistics.median(fn(r) for r in by_key[(exp, cond)])


def tad_occ(r):
    return r["card_pmu"]["tad"]["tad_req_occ"]["val"] / (r["card_pmu"]["wall_s"] * 2.0e9)


def tad_lat_ns(r):
    occ = r["card_pmu"]["tad"]["tad_req_occ"]["val"]
    msh_in = r["card_pmu"]["tad"]["tad_req_msh_in_any"]["val"]
    return (occ / msh_in) * 0.5


def snp_per_msg(r):
    return r["card_pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / (r["rounds"] + r["warmup"])


def burst_slow_stats(r):
    """Compute slow-burst (burst median RTT >= 3.0 us) sorted Δcard_t_in and h2c directly from CSV."""
    p = ROOT / r["stamp_csv"]
    with p.open() as f:
        hdr = f.readline().strip()
        parts = dict(tok.split("=") for tok in hdr.lstrip("# ").split())
        tsc_hz = float(parts["tsc_hz"])
        sb_t, sb_c, _ = [float(x) for x in parts["sb"].split(",")]
        sa_t, sa_c, _ = [float(x) for x in parts["sa"].split(",")]
        rows = list(csv.DictReader(f))
    card_hz = float(r["card_pmu"]["tsc_hz"])
    slope = (sa_t - sb_t) / (sa_c - sb_c)
    ns_h = 1e9 / tsc_hz
    ns_c = 1e9 / card_hz

    def to_host(c_ts):
        return sb_t + slope * (c_ts - sb_c)

    def pct(vals, q):
        s = sorted(vals)
        return s[int((len(s) - 1) * q)]

    by_blk = collections.defaultdict(list)
    for row in rows:
        by_blk[int(row["seq"]) >> 6].append(row)

    slow_dcin, slow_h2c = [], []
    n_slow_bursts = 0
    for blk in by_blk.values():
        rtts_us = [(float(x["seen_tsc"]) - float(x["send_tsc"])) * ns_h / 1000.0 for x in blk]
        if statistics.median(rtts_us) >= 3.0:
            n_slow_bursts += 1
            cins = sorted(float(x["card_t_in"]) for x in blk)
            for i in range(1, len(cins)):
                slow_dcin.append((cins[i] - cins[i - 1]) * ns_c)
            for x in blk:
                slow_h2c.append((to_host(float(x["card_t_in"])) - float(x["send_tsc"])) * ns_h / 1000.0)
    return {
        "n_slow_bursts": n_slow_bursts,
        "p05": pct(slow_dcin, 0.05),
        "p50": pct(slow_dcin, 0.50),
        "p90": pct(slow_dcin, 0.90),
        "h2c_p50_us": pct(slow_h2c, 0.50),
    }


STRIDES = [64, 128, 4096, 4224]
X_LABELS = ["64 B\n(基线紧排)", "128 B\n(跨 128 B 块)", "4096 B\n(跨 4 KiB 页)", "4224 B\n(4096+128 反别名)"]
xs = np.arange(len(STRIDES))

fig, axes = plt.subplots(1, 3, figsize=(16.8, 5.4))

# ---- Panel A: Throughput across strides (WC2 W=64 core + WC2 W=128 + WC4 W=64) ----
ax = axes[0]
series = [
    ("core_wc2_w64", lambda s: f"wc2_w64_s{s}", "WC2 W=64（主实验，n=5）", "#dc2626", "o", "-"),
    ("ctrl_wc2_w128", lambda s: f"wc2_w128_s{s}", "WC2 W=128（深窗对照，n=3）", "#2563eb", "s", "--"),
    ("ctrl_wc4_w64", lambda s: f"wc4_w64_s{s}", "WC4 W=64（4 卡核对照，n=3）", "#0f766e", "^", ":"),
]
ax.axhspan(12.2, 15.6, color="#fee2e2", alpha=0.55, zorder=0)
ax.axhline(34.96, color="#475569", ls="-.", lw=1.2, alpha=0.85)
ax.text(0.05, 35.6, "MRV-75 快态参考：WC1 W=64 (64 B) = 35.0 M/s", fontsize=7.6, color="#334155")

for exp, cond_fn, lbl, col, mk, ls in series:
    ys = [med(exp, cond_fn(s), lambda r: r["mops"]) for s in STRIDES]
    ax.plot(xs, ys, ls=ls, marker=mk, color=col, lw=2.2, ms=6.5, label=lbl, zorder=3)
    for i, s in enumerate(STRIDES):
        for r in by_key[(exp, cond_fn(s))]:
            ax.scatter(i, r["mops"], color=col, s=18, alpha=0.4, zorder=2)

wc2_ys = [med("core_wc2_w64", f"wc2_w64_s{s}", lambda r: r["mops"]) for s in STRIDES]
wc4_ys = [med("ctrl_wc4_w64", f"wc4_w64_s{s}", lambda r: r["mops"]) for s in STRIDES]
for i, (y2, y4) in enumerate(zip(wc2_ys, wc4_ys)):
    ax.annotate(f"{y2:.2f}", (i, y2), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8.3, fontweight="bold", color="#b91c1c")
    ax.annotate(f"{y4:.2f}", (i, y4), xytext=(0, -14), textcoords="offset points", ha="center", fontsize=7.8, color="#0f766e")

ax.annotate(
    "4096 B 别名放大批 (18.5 条)\n偶跨悬崖，p99 仍锁 4.66 µs；\n反别名 4224 B 落回 15.0 M/s",
    xy=(2, wc2_ys[2]),
    xytext=(1.60, 24.4),
    fontsize=7.4,
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.25", fc="#fff5f5", ec="#fca5a5"),
    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.0),
)

ax.set_xticks(xs)
ax.set_xticklabels(X_LABELS, fontsize=8.2)
ax.set_ylim(8, 39.8)
ax.set_ylabel("闭环吞吐 (M msg/s)")
ax.set_title("A. 闭环吞吐：跨 128 B 与跨 4 KiB 页均未破慢态")
ax.legend(loc="upper left", bbox_to_anchor=(0.01, 0.84), fontsize=7.5, frameon=True, facecolor="white")

# ---- Panel B: Card TAD in-flight (tad_req_occ), tad_lat, and tad_snp ----
ax = axes[1]
wc2_occ = [med("core_wc2_w64", f"wc2_w64_s{s}", tad_occ) for s in STRIDES]
wc4_occ = [med("ctrl_wc4_w64", f"wc4_w64_s{s}", tad_occ) for s in STRIDES]
wc2_lat = [med("core_wc2_w64", f"wc2_w64_s{s}", tad_lat_ns) for s in STRIDES]
wc4_lat = [med("ctrl_wc4_w64", f"wc4_w64_s{s}", tad_lat_ns) for s in STRIDES]
wc2_snp = [med("core_wc2_w64", f"wc2_w64_s{s}", snp_per_msg) for s in STRIDES]
r81 = by_key[("stamp_wc2", "wc2_w64_s4096_sp")][0]
r81_occ = tad_occ(r81)
r81_lat = tad_lat_ns(r81)
drop_pct = (wc2_occ[1] - wc2_occ[0]) / wc2_occ[0] * 100.0

w = 0.30
b_wc2 = ax.bar(xs - w / 2, wc2_occ, w, color="#ef4444", alpha=0.82, label="WC2 W=64 TAD 在途 (条，左轴)", zorder=3)
b_wc4 = ax.bar(xs + w / 2, wc4_occ, w, color="#0f766e", alpha=0.82, label="WC4 W=64 TAD 在途 (条，左轴)", zorder=3)

for i, bar in enumerate(b_wc2):
    h = bar.get_height()
    ax.text(bar.get_x() + bar.get_width() / 2, h + 0.008, f"{h:.3f}\n({wc2_lat[i]:.1f}ns)", ha="center", va="bottom", fontsize=6.6, color="#991b1b")
for i, bar in enumerate(b_wc4):
    h = bar.get_height()
    ax.text(bar.get_x() + bar.get_width() / 2, h + 0.008, f"{h:.3f}\n({wc4_lat[i]:.1f}ns)", ha="center", va="bottom", fontsize=6.6, color="#065f46")

# Contrast true fast state id=81 (26.3 M/s) at 4096 B against pure slow WC4 4096 B
sc_fast = ax.scatter(2 - w / 2, r81_occ, marker="*", s=135, color="#2563eb", edgecolor="white", lw=0.7, zorder=5,
                     label=f"真快态 id=81 ({r81['mops']:.1f} M/s): TAD={r81_occ:.3f}")
ax.annotate(
    f"快态 id=81 ({r81['mops']:.1f} M/s)\nTAD={r81_occ:.3f} ({r81_lat:.1f}ns) ≪ 1\n反低于慢态 WC4 4096B!",
    xy=(2 - w / 2, r81_occ),
    xytext=(0.76, 0.255),
    fontsize=6.8,
    color="#1d4ed8",
    bbox=dict(boxstyle="round,pad=0.22", fc="#eff6ff", ec="#93c5fd"),
    arrowprops=dict(arrowstyle="->", color="#2563eb", lw=1.0),
)
ax.annotate(
    f"128 B 拆开同块 MSHR 排队\nTAD 在途 {drop_pct:.1f}%",
    xy=(1 - w / 2, wc2_occ[1] + 0.058),
    xytext=(0.58, 0.140),
    fontsize=6.7,
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.20", fc="#fff5f5", ec="#fca5a5"),
    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=0.9),
)

ax.set_xticks(xs)
ax.set_xticklabels(X_LABELS, fontsize=8.2)
ax.set_ylim(0, 0.58)
ax.set_ylabel("卡 TAD 在途并发 tad_req_occ (条，括号为单笔驻留)", color="#1e293b")

ax2 = ax.twinx()
ax2.spines["top"].set_visible(False)
l_snp, = ax2.plot(xs, wc2_snp, "--D", color="#d97706", lw=1.8, ms=5.5, label="WC2 W=64 TAD 嗅探 (次/条，右轴)", zorder=4)
for i, sv in enumerate(wc2_snp):
    dx = -16 if i == 2 else 0
    dy = 7 if i != 2 else 11
    ax2.annotate(f"{sv:.2f}", (i, sv), xytext=(dx, dy), textcoords="offset points", ha="center", fontsize=7.2, fontweight="bold", color="#b45309")
ax2.set_ylim(0, 5.4)
ax2.set_ylabel("卡 TAD 嗅探 tad_snp (次/条)", color="#b45309")
ax2.tick_params(axis="y", labelcolor="#b45309")
ax2.grid(False)

h1, l1 = ax.get_legend_handles_labels()
h2, l2_lbl = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2_lbl, loc="upper left", fontsize=6.7, frameon=True, facecolor="white")
ax.set_title("B. 卡 TAD 并发与嗅探：128 B 仅消同块排队，TAD>1 非通用快态判据")

# ---- Panel C: Cross-core arrival spacing Δcard_t_in (slow-burst conditional vs all-sample) ----
ax = axes[2]
stamp_groups = [
    ("stamp_wc2", "wc2_w64_s64_sp", "WC2 64B", "#dc2626"),
    ("stamp_wc2", "wc2_w64_s128_sp", "WC2 128B", "#ea580c"),
    ("stamp_wc2", "wc2_w64_s4096_sp", "WC2 4096B", "#b91c1c"),
    ("stamp_wc2", "wc2_w64_s4224_sp", "WC2 4224B", "#991b1b"),
    ("stamp_wc4", "wc4_w64_s64_sp", "WC4 64B", "#0f766e"),
    ("stamp_wc4", "wc4_w64_s128_sp", "WC4 128B", "#0d9488"),
    ("stamp_wc4", "wc4_w64_s4096_sp", "WC4 4096B", "#14b8a6"),
    ("stamp_wc4", "wc4_w64_s4224_sp", "WC4 4224B", "#047857"),
]
cx = np.arange(len(stamp_groups))

ax.axhspan(70, 80, color="#fee2e2", alpha=0.55, zorder=0)
ax.axhline(76.4, color="#991b1b", ls="--", lw=1.1, alpha=0.8)
ax.text(5.45, 140.0, "红带：MRV-75 串行节拍 76.4 ns", ha="center", fontsize=7.3, color="#991b1b",
        bbox=dict(boxstyle="round,pad=0.20", fc="#fff5f5", ec="#fca5a5"))

for i, (exp, cond, lbl, col) in enumerate(stamp_groups):
    r = by_key[(exp, cond)][0]
    ss = r["stamp_summary"]
    sb = burst_slow_stats(r)
    # Plot slow-burst conditional range & median for all 8 groups
    ax.vlines(i, sb["p05"], sb["p90"], color=col, lw=2.1, alpha=0.55, zorder=2)
    ax.scatter(i, sb["p50"], color=col, s=56, zorder=4)
    y_lbl = sb["p90"] + 3.0
    if cond == "wc2_w64_s4096_sp":
        # Also show the all-sample median biased by core-0 CLOCK_OFFSET write (-65% poll rate)
        ax.scatter(i, ss["dcin_sorted_p50_ns"], facecolors="white", edgecolors="#d97706", s=62, lw=1.8, zorder=4)
        ax.annotate(
            "",
            xy=(i, sb["p50"] - 3.0),
            xytext=(i, ss["dcin_sorted_p50_ns"] + 3.5),
            arrowprops=dict(arrowstyle="->", color="#d97706", ls=":", lw=1.2),
        )
        ax.text(i, sb["p90"] + 3.0, f"慢:{sb['p50']:.0f}ns\n({sb['h2c_p50_us']:.1f}µs)", ha="center", va="bottom", fontsize=6.8, fontweight="bold", color="#991b1b")
        ax.annotate(
            f"全样本 {ss['dcin_sorted_p50_ns']:.0f}ns\n(写钟核0空轮询-65%\n翻入快态; 慢态33组=74ns)",
            xy=(i, ss["dcin_sorted_p50_ns"]),
            xytext=(3.75, 11.0),
            fontsize=6.6,
            color="#b45309",
            bbox=dict(boxstyle="round,pad=0.20", fc="#fffbeb", ec="#fcd34d"),
            arrowprops=dict(arrowstyle="->", color="#d97706", lw=0.9),
        )
    else:
        ax.text(i, y_lbl, f"{sb['p50']:.0f}ns\n({sb['h2c_p50_us']:.1f}µs)", ha="center", va="bottom", fontsize=6.8, color="#1e293b")

ax.axvline(3.5, color="#94a3b8", ls=":", lw=1.2)
ax.set_xticks(cx)
ax.set_xticklabels([g[2] for g in stamp_groups], rotation=28, ha="right", fontsize=7.8)
ax.set_ylim(0, 156)
ax.set_ylabel("跨核到达间隔 Δcard_t_in (ns，慢态突发 p05–p90 与中位数)")
ax.set_title("C. 慢态突发对时节拍（RTT≥3µs，全档锁死 74–78 ns）")

fig.suptitle(
    "MRV-112：BAR4 请求环步长单变量对比（MOVDIR64B + WC，64 B，cpu40 3.9 GHz，uncore 2.2 GHz，卡 2.0 GHz，PMU 100% 无复用）",
    fontsize=10.8,
    fontweight="bold",
)
out = mrvplot.save(fig, "mrv-112-stride-comparison")
print(f"Saved {out}")
