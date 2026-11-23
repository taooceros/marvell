"""Plot MRV-99 sustained throughput (W=1, 8, 32, 64, 96) for NOP and 4 KiB MEMMOVE
from raw logs/mrv99-2026-11-19/runs.jsonl and diag_min_batch.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs" / "mrv99-2026-11-19" / "runs.jsonl"
DIAG = ROOT / "logs" / "mrv99-2026-11-19" / "diag_min_batch.jsonl"

rows = [json.loads(line) for line in RUNS.read_text().splitlines() if line.strip()]
diag_rows = [json.loads(line) for line in DIAG.read_text().splitlines() if line.strip()]

for r in rows + diag_rows:
    assert r.get("lost", 0) == 0 and r.get("bad", 0) == 0
    assert "mismatches=0" in r["host_verify"]
    assert "swerror=00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000" in r["host_verify"]

g = defaultdict(list)
for r in rows:
    if r["mode"] == "rate":
        g[(r["op"], r["size"], r["window"], r["comp_target"])].append(r)

ws = [1, 8, 32, 64, 96]
C_CTRL = "#6b7280"
C_TREAT = "#0f766e"

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.0, 4.9))

# --- Left: NOP rate vs W ---
ctrl_nop = [statistics.median([r["mops_s"] for r in g[("nop", 0, w, "host_memzone")]]) for w in ws]
treat_nop = [statistics.median([r["mops_s"] for r in g[("nop", 0, w, "card_bar4")]]) for w in ws]

ax1.plot(ws, ctrl_nop, "o--", color=C_CTRL, lw=2.0, ms=6, label="对照组：写主机内存（需跨 PCIe 读回）")
ax1.plot(ws, treat_nop, "s-", color=C_TREAT, lw=2.4, ms=6.5, label="实验组：直写卡侧 BAR4（免读回）")

for w, cv, tv in zip(ws, ctrl_nop, treat_nop):
    if w == 1:
        ax1.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(-2, 10),
                     ha="center", fontsize=8.6, fontweight="bold", color=C_TREAT)
        ax1.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(14, -4),
                     ha="left", fontsize=8.4, color="#4b5563")
    else:
        ax1.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(0, 8),
                     ha="center", fontsize=8.8, fontweight="bold", color=C_TREAT)
        ax1.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(0, -14),
                     ha="center", fontsize=8.5, color="#4b5563")

ax1.annotate("全线领先，最高 23.01 M/s (+62%)", xy=(96, treat_nop[-1]), xytext=(36, 22.8),
             arrowprops=dict(arrowstyle="->", color=C_TREAT, lw=1.2),
             fontsize=9.0, fontweight="bold", color=C_TREAT,
             bbox=dict(boxstyle="round,pad=0.28", fc="#f0fdfa", ec=C_TREAT, lw=0.9))

ax1.set_title("NOP（空操作）持续吞吐 vs 并发数 W", fontsize=10.4, fontweight="bold")
ax1.set_xlabel("并发数 W（同时在途任务数）", fontsize=9.8)
ax1.set_ylabel("完成速率 (M ops/s)", fontsize=9.8)
ax1.set_xticks(ws)
ax1.set_xlim(-5, 105)
ax1.set_ylim(0, 26.8)
ax1.legend(loc="upper left", fontsize=8.5, frameon=True, facecolor="white")

# --- Right: 4 KiB MEMMOVE rate vs W ---
ctrl_mm = [statistics.median([r["mops_s"] for r in g[("memmove", 4096, w, "host_memzone")]]) for w in ws]
treat_mm = [statistics.median([r["mops_s"] for r in g[("memmove", 4096, w, "card_bar4")]]) for w in ws]

ax2.plot(ws, ctrl_mm, "o--", color=C_CTRL, lw=2.0, ms=6, label="对照组：写主机内存（峰值 30.0 GB/s）")
ax2.plot(ws, treat_mm, "s-", color=C_TREAT, lw=2.4, ms=6.5, label="实验组：直写卡侧 BAR4（平台 15.3 GB/s）")

for w, cv, tv in zip(ws, ctrl_mm, treat_mm):
    if w == 1:
        ax2.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(10, 4),
                     ha="left", fontsize=8.6, fontweight="bold", color=C_TREAT)
        ax2.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(10, -8),
                     ha="left", fontsize=8.4, color="#4b5563")
    elif w == 8:
        ax2.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(0, 8),
                     ha="center", fontsize=8.8, fontweight="bold", color=C_TREAT)
        ax2.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(10, -4),
                     ha="left", fontsize=8.5, color="#4b5563")
    elif w == 32:
        ax2.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(14, -12),
                     ha="left", fontsize=8.6, color="#4b5563")
        ax2.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(0, -14),
                     ha="center", fontsize=8.8, fontweight="bold", color=C_TREAT)
    else:
        ax2.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(0, 8),
                     ha="center", fontsize=8.6, color="#4b5563")
        ax2.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(0, -14),
                     ha="center", fontsize=8.8, fontweight="bold", color=C_TREAT)

ax2.annotate("低并发（W≤8）实验组胜出\n(+44% ~ +58%)", xy=(8, treat_mm[1] + 0.55), xytext=(-2, 5.85),
             arrowprops=dict(arrowstyle="->", color=C_TREAT, lw=1.2),
             fontsize=8.8, fontweight="bold", color=C_TREAT,
             bbox=dict(boxstyle="round,pad=0.28", fc="#f0fdfa", ec=C_TREAT, lw=0.9))

ax2.annotate("高并发（W≥32）卡死在 3.74 M/s（15.3 GB/s）\n每笔多等 ~131 ns；已排除卡侧门铃与拷贝引擎瓶颈",
             xy=(64, treat_mm[3] - 0.35), xytext=(24, 1.35),
             arrowprops=dict(arrowstyle="->", color="#b45309", lw=1.2),
             fontsize=8.5, fontweight="bold", color="#92400e",
             bbox=dict(boxstyle="round,pad=0.28", fc="#fffbeb", ec="#d97706", lw=0.9))

ax2.set_title("4 KiB 内存拷贝持续吞吐 vs 并发数 W", fontsize=10.4, fontweight="bold")
ax2.set_xlabel("并发数 W（同时在途任务数）", fontsize=9.8)
ax2.set_ylabel("完成速率 (M ops/s)", fontsize=9.8)
ax2.set_xticks(ws)
ax2.set_xlim(-5, 105)
ax2.set_ylim(0, 8.8)
ax2.legend(loc="upper left", fontsize=8.4, frameon=True, facecolor="white")

ax2_r = ax2.twinx()
ax2_r.set_ylim(0, 8.8 * 4.096)
ax2_r.set_ylabel("有效搬运带宽 (GB/s, 10⁹ B/s)", fontsize=9.5, color="#4b5563")
ax2_r.spines["top"].set_visible(False)
ax2_r.grid(False)

fig.suptitle(
    "MRV-99 持续吞吐对比：NOP 全线领先，但 4 KiB 拷贝在高并发下掉到一半（1.5 s × 3 次中位数）",
    fontsize=10.8, fontweight="bold", y=0.99,
)

png = save(fig, "mrv-99-rate-scaling")
print(f"Saved {png}")
