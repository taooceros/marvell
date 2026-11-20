#!/usr/bin/env python3
"""MRV-115 Figure 2: Poisson Arrivals & Exact Hardware Scaling Law (wire p99 = W_credit * 76.4 ns)."""
import collections
import json
import pathlib
import statistics
import sys

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mrvplot  # noqa: E402

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv115")
RUNS = ROOT / "logs/mrv115-2026-11-20/runs.jsonl"

recs = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
for r in recs:
    assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0

by_key = collections.defaultdict(list)
for r in recs:
    if r["mode"] == "open_echo":
        key = (r["part"], r["card_cores"], r["arrival"], r["target_mps"], r["shaping"], r["card_pause_ns"])
        by_key[key].append(r)


def med(key, fn):
    return statistics.median(fn(r) for r in by_key[key])


fig, axes = plt.subplots(1, 2, figsize=(14.0, 5.4))

# ---- Panel A: Poisson Arrivals (WC1 10/15/20 M/s & WC4 10/13.5/16 M/s) ----
ax = axes[0]
groups = [
    ("WC1 10M\n泊松", ("p1_wc1_poisson", 1, "poisson", 10.0, "unshaped", 0), ("p1_wc1_poisson", 1, "poisson", 10.0, "credit_32", 0)),
    ("WC1 15M\n泊松", ("p1_wc1_poisson", 1, "poisson", 15.0, "unshaped", 0), ("p1_wc1_poisson", 1, "poisson", 15.0, "credit_32", 0)),
    ("WC1 20M\n泊松", ("p1_wc1_poisson", 1, "poisson", 20.0, "unshaped", 0), ("p1_wc1_poisson", 1, "poisson", 20.0, "credit_32", 0)),
    ("WC4 10M\n泊松", ("p1_wc4_poisson", 4, "poisson", 10.0, "unshaped", 0), ("p1_wc4_poisson", 4, "poisson", 10.0, "burst8_c24", 0)),
    ("WC4 13.5M\n泊松", ("p1_wc4_poisson", 4, "poisson", 13.5, "unshaped", 0), ("p1_wc4_poisson", 4, "poisson", 13.5, "burst8_c24", 0)),
    ("WC4 16M\n泊松", ("p1_wc4_poisson", 4, "poisson", 16.0, "unshaped", 0), ("p1_wc4_poisson", 4, "poisson", 16.0, "burst8_c24", 0)),
]

x = np.arange(len(groups))
w = 0.26
u_tot = [med(ku, lambda r: r["tot_us"]["p99"]) for _, ku, _ in groups]
s_tot = [med(ks, lambda r: r["tot_us"]["p99"]) for _, _, ks in groups]
s_wire = [med(ks, lambda r: r["wire_us"]["p99"]) for _, _, ks in groups]

b1 = ax.bar(x - w, u_tot, width=w, color="#ef4444", alpha=0.88, label="无整形 (W_cap=4032) 端到端 p99")
b2 = ax.bar(x, s_tot, width=w, color="#2563eb", alpha=0.90, label="主机整形 (WC1 C=32 / WC4 C=24) 端到端 p99")
b3 = ax.bar(x + w, s_wire, width=w, color="#60a5fa", alpha=0.92, hatch="//", edgecolor="#1e40af", label="主机整形 线端 wire p99")

ax.axhline(2.0, color="#0f766e", ls=":", lw=1.4, label="目标线 p99 = 2.0 µs")
ax.set_yscale("log")
ax.set_ylim(0.7, 250)
ax.set_xticks(x)
ax.set_xticklabels([g[0] for g in groups], fontsize=8.5)
ax.set_ylabel("泊松到达往返尾延迟 p99 (µs，对数坐标)")
ax.set_title("A. 泊松随机到达（3 组随机种子）：主机整形压低尾延迟并锁死线端")

for bars in (b1, b2, b3):
    for rect in bars:
        h = rect.get_height()
        lbl = f"{h:.0f}" if h >= 25 else f"{h:.1f}"
        ax.text(rect.get_x() + rect.get_width() / 2, h * 1.09, lbl, ha="center", va="bottom", fontsize=7.3, fontweight="bold")

# Add WC1 20M Poisson + T_pause=1000ns star marker on x=2
y_ep = med(("p1_wc1_pause1000", 1, "poisson", 20.0, "credit_48_p1000", 1000), lambda r: r["tot_us"]["p99"])
ax.scatter([2.0], [y_ep], marker="*", s=160, color="#7c3aed", zorder=6, label=f"对照：WC1 20M 泊松 + 卡退避 1 µs ({y_ep:.2f} µs)")

ax.legend(loc="upper left", fontsize=7.3, frameon=True, facecolor="white")

# ---- Panel B: Exact Hardware Scaling Law (wire p99 vs W_credit) & Host CHA Collapse ----
ax = axes[1]
credits = [24, 32, 48, 64]
shps = ["credit_24", "credit_32", "credit_48", "credit_64"]
wire_p99_20 = [med(("p1_wc1_paced", 1, "paced", 20.0, s, 0), lambda r: r["wire_us"]["p99"]) for s in shps]
cha_20 = [med(("p1_wc1_paced", 1, "paced", 20.0, s, 0), lambda r: r["host_pmu"]["cha_inflight"]) for s in shps]

c_line = np.linspace(18, 72, 200)
ax.plot(c_line, c_line * 0.0764, "--", color="#64748b", lw=1.8, label="理论慢态排空上界：W_credit × 76.4 ns")
ax.plot(credits, wire_p99_20, "-o", color="#1d4ed8", lw=2.4, ms=7.5, label="实测 WC1 20 M/s 线端 wire p99 (µs)")

# Shade infeasible vs feasible trade-off
ax.axvspan(18, 26.2, color="#fef3c7", alpha=0.45, zorder=0)
ax.axvline(26.2, color="#b45309", ls=":", lw=1.3)
ax.axhline(2.0, color="#0f766e", ls=":", lw=1.4, label="2.0 µs 目标线（需 W_credit ≤ 26）")

offsets = [(3.5, -0.55), (3.5, -0.60), (3.2, -0.65), (-14.5, 0.35)]
for c, y, ch, (dx, dy) in zip(credits, wire_p99_20, cha_20, offsets):
    th = c * 0.0764
    ax.annotate(
        f"C={c}: 实测 {y:.2f} µs\n(理论 {th:.2f} µs, CHA={ch:.2f})",
        xy=(c, y),
        xytext=(c + dx, y + dy),
        arrowprops=dict(arrowstyle="->", color="#1e3a8a", lw=1.1),
        fontsize=7.6,
        fontweight="bold",
        color="#1e3a8a",
        bbox=dict(boxstyle="round,pad=0.2", fc="#eff6ff", ec="#bfdbfe"),
    )

# Summary inset for Unshaped CHA blowup
u_cha1 = med(("p1_wc1_paced", 1, "paced", 20.0, "unshaped", 0), lambda r: r["host_pmu"]["cha_inflight"])
u_cha4 = med(("p1_wc4_paced", 4, "paced", 20.0, "unshaped", 0), lambda r: r["host_pmu"]["cha_inflight"])
u_wire1 = med(("p1_wc1_paced", 1, "paced", 20.0, "unshaped", 0), lambda r: r["wire_us"]["p99"])
u_wire4 = med(("p1_wc4_paced", 4, "paced", 20.0, "unshaped", 0), lambda r: r["wire_us"]["p99"])
ax.text(
    20.2,
    4.95,
    f"对比无整形 (W_cap=4032, 20 M/s)：\n"
    f"• WC1：wire p99={u_wire1:.1f} µs，CHA={u_cha1:.1f} 条（反压）\n"
    f"• WC4：wire p99={u_wire4:.1f} µs，CHA={u_cha4:.1f} 条（严重反压）\n"
    f"• 主机整形后 CHA 恒为 2.04 条（102.5 ns 无拥塞）",
    fontsize=7.6,
    color="#991b1b",
    bbox=dict(boxstyle="round,pad=0.28", fc="#fef2f2", ec="#fca5a5"),
)
ax.text(18.6, 2.55, "C ≤ 26 区间：\n20 M/s 下低于\n利特尔容量下限\n(R·RTT ≈ 25)", fontsize=7.5, color="#92400e", fontweight="bold")
ax.set_xlim(18, 72)
ax.set_ylim(0.7, 6.2)
ax.set_xlabel("主机在途信用上限 W_credit（条）")
ax.set_ylabel("线端往返尾延迟 wire p99 (µs)")
ax.set_title("B. 双边硬件约束：wire p99 ≈ W_credit × 76.4 ns，CHA 反压归零")
ax.legend(loc="lower right", fontsize=7.4, frameon=True, facecolor="white")

fig.suptitle(
    "MRV-115 泊松到达验证与 20 M/s 残余尾延迟的精确硬件标度律\n"
    "固定基线：MOVDIR64B + WC，64 B，cpu40 3.90 GHz，uncore 2.20 GHz，卡 2.00 GHz（3 轮交错中位数，主卡 PMU 100% 启用）",
    fontsize=10.3,
    fontweight="bold",
)
out = mrvplot.save(fig, "mrv-115-poisson-and-scaling")
print(f"Saved {out}")
