"""Plot MRV-99 sustained throughput (W=1, 8, 32, 64, 96) for NOP and 4 KiB MEMMOVE
comparing Host Memzone comp (Control) vs Card PF BAR4 slot 15 comp (Treatment).
Reads raw data from logs/mrv99-2026-11-19/runs.jsonl and diag_min_batch.jsonl.
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

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.8, 4.9))

# --- Left: NOP rate vs W ---
ctrl_nop = [statistics.median([r["mops_s"] for r in g[("nop", 0, w, "host_memzone")]]) for w in ws]
treat_nop = [statistics.median([r["mops_s"] for r in g[("nop", 0, w, "card_bar4")]]) for w in ws]

ax1.plot(ws, ctrl_nop, "o--", color=C_CTRL, lw=2.0, ms=6, label="对照组 主机 Memzone (DPI 读回)")
ax1.plot(ws, treat_nop, "s-", color=C_TREAT, lw=2.4, ms=6.5, label="实验组 卡 BAR4 槽 15 (反向 P2P)")

for w, cv, tv in zip(ws, ctrl_nop, treat_nop):
    if w == 1:
        ax1.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(0, 9),
                     ha="center", fontsize=8.4, fontweight="bold", color=C_TREAT)
        ax1.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(10, -4),
                     ha="left", fontsize=8.2, color="#4b5563")
    elif w == 8:
        ax1.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(0, 8),
                     ha="center", fontsize=8.8, fontweight="bold", color=C_TREAT)
        ax1.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(8, -11),
                     ha="left", fontsize=8.5, color="#4b5563")
    else:
        ax1.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(0, 8),
                     ha="center", fontsize=8.8, fontweight="bold", color=C_TREAT)
        ax1.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(0, -14),
                     ha="center", fontsize=8.5, color="#4b5563")

ax1.annotate("23.01 M/s (+62%)", xy=(96, treat_nop[-1]), xytext=(52, 22.8),
             arrowprops=dict(arrowstyle="->", color=C_TREAT, lw=1.2),
             fontsize=9.0, fontweight="bold", color=C_TREAT,
             bbox=dict(boxstyle="round,pad=0.25", fc="#f0fdfa", ec=C_TREAT, lw=0.9))

ax1.set_title("NOP 持续吞吐 vs 并发窗口 W", fontsize=10.4, fontweight="bold")
ax1.set_xlabel("并发窗口 W（在途描述符数，wq2.0 深度 128）", fontsize=9.8)
ax1.set_ylabel("完成速率 (M ops/s)", fontsize=9.8)
ax1.set_xticks(ws)
ax1.set_xlim(-5, 105)
ax1.set_ylim(0, 26.8)
ax1.legend(loc="upper left", fontsize=8.6, frameon=True, facecolor="white")

# --- Right: 4 KiB MEMMOVE rate vs W ---
ctrl_mm = [statistics.median([r["mops_s"] for r in g[("memmove", 4096, w, "host_memzone")]]) for w in ws]
treat_mm = [statistics.median([r["mops_s"] for r in g[("memmove", 4096, w, "card_bar4")]]) for w in ws]

ax2.plot(ws, ctrl_mm, "o--", color=C_CTRL, lw=2.0, ms=6, label="对照组 主机 Memzone (峰值 30.0 GB/s)")
ax2.plot(ws, treat_mm, "s-", color=C_TREAT, lw=2.4, ms=6.5, label="实验组 卡 BAR4 槽 15 (平台 15.3 GB/s)")

for w, cv, tv in zip(ws, ctrl_mm, treat_mm):
    if w == 1:
        ax2.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(8, 6),
                     ha="left", fontsize=8.4, fontweight="bold", color=C_TREAT)
        ax2.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(10, -5),
                     ha="left", fontsize=8.2, color="#4b5563")
    elif w == 8:
        ax2.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(0, 8),
                     ha="center", fontsize=8.8, fontweight="bold", color=C_TREAT)
        ax2.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(6, -13),
                     ha="left", fontsize=8.5, color="#4b5563")
    else:
        ax2.annotate(f"{cv:.2f}", (w, cv), textcoords="offset points", xytext=(0, 8),
                     ha="center", fontsize=8.8, color="#374151")
        offset_y = 9 if w == 64 else -15
        ax2.annotate(f"{tv:.2f}", (w, tv), textcoords="offset points", xytext=(0, offset_y),
                     ha="center", fontsize=8.8, fontweight="bold", color=C_TREAT)

ax2.annotate("W≤8 实验组胜出\n(+44% / +58%)", xy=(8, treat_mm[1] + 0.52), xytext=(2, 5.75),
             arrowprops=dict(arrowstyle="->", color=C_TREAT, lw=1.2),
             fontsize=8.5, fontweight="bold", color=C_TREAT,
             bbox=dict(boxstyle="round,pad=0.25", fc="#f0fdfa", ec=C_TREAT, lw=0.9))

ax2.annotate("W≥32 锁死在 3.74 M/s (15.3 GB/s)\nmin_batch=1/8/16 均不变 → 非门铃瓶颈",
             xy=(64, treat_mm[3] - 0.16), xytext=(33, 1.35),
             arrowprops=dict(arrowstyle="->", color="#b45309", lw=1.2),
             fontsize=8.4, fontweight="bold", color="#92400e",
             bbox=dict(boxstyle="round,pad=0.25", fc="#fffbeb", ec="#d97706", lw=0.9))

ax2.set_title("4 KiB MEMMOVE (CC=0) 持续吞吐 vs 并发窗口 W", fontsize=10.4, fontweight="bold")
ax2.set_xlabel("并发窗口 W（每槽独立 4 KiB，无重叠）", fontsize=9.8)
ax2.set_ylabel("完成速率 (M ops/s)", fontsize=9.8)
ax2.set_xticks(ws)
ax2.set_xlim(-5, 105)
ax2.set_ylim(0, 8.8)
ax2.legend(loc="upper left", fontsize=8.4, frameon=True, facecolor="white")

ax2_r = ax2.twinx()
ax2_r.set_ylim(0, 8.8 * 4.096)
ax2_r.set_ylabel("有效搬运带宽 (GB/s)", fontsize=9.5, color="#4b5563")
ax2_r.spines["top"].set_visible(False)
ax2_r.grid(False)

fig.suptitle(
    "MRV-99：持续吞吐对比（卡 DPI 64 B 提交 · dsa2 1 引擎 · cpu40 3.9 GHz · uncore 2.2 GHz · 卡 2.0 GHz，1.5 s × 3 次中位数）",
    fontsize=10.3,
    fontweight="bold",
)

png = save(fig, "mrv-99-rate-scaling")
print(f"Saved {png}")
