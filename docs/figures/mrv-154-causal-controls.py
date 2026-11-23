"""MRV-154 Chart 2: Causal isolation controls (bare CN106 microbench, SYNTH gap/injection sweep, W=32 non-regression).

Plotted directly from verified raw log:
    logs/mrv154-2026-11-23/runs.jsonl

Run:
    uv run --with matplotlib python docs/figures/mrv-154-causal-controls.py
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

REPO = Path(__file__).resolve().parents[2]
RUNS_PATH = REPO / "logs" / "mrv154-2026-11-23" / "runs.jsonl"

rows = [json.loads(line) for line in RUNS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def by_cfg(cfg):
    rs = [r for r in rows if r["cfg"] == cfg]
    assert len(rs) == 3, f"expected 3 reps for {cfg}, got {len(rs)}"
    for r in rs:
        assert r["verified"], f"unverified row in {cfg}"
    return rs


def med(xs):
    return statistics.median(xs)


fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15.4, 5.1), gridspec_kw={"width_ratios": [1.08, 1.38, 0.94]})

# ---------------- Panel A: Bare CN106 poll microbenchmark (log scale) ----------------
mb = by_cfg("cn106_bare_poll_bench")
mb_items = [
    ("空 BAR4 轮询\n(L1 ldar)", [r["bench"]["bar4_poll_empty_bare_ns"] for r in mb], "#1F2937"),
    ("NPA mbuf\nalloc+free", [r["bench"]["mbuf_alloc_free_bare_ns"] for r in mb], "#6B7280"),
    ("CNTVCT fence\nrdtsc_after_loads", [r["bench"]["rdtsc_after_loads_bare_ns"] for r in mb], "#9CA3AF"),
    ("空 eth0 RX\n(LDADDA MMIO)", [r["bench"]["eth_rx_empty_bare_ns"] for r in mb], "#C83E31"),
]
y_pos = np.arange(len(mb_items))
mb_meds = [med(vals) for _, vals, _ in mb_items]
ax1.barh(y_pos, mb_meds, height=0.56, color=[c for _, _, c in mb_items], edgecolor="#111827", linewidth=0.7, zorder=3)
for y, (_, vals, _) in zip(y_pos, mb_items):
    ax1.scatter(vals, np.full(3, y), color="white", edgecolor="#111827", s=22, linewidth=0.9, zorder=5)
    m = med(vals)
    txt = f"{m:.2f} ns" if m < 10 else f"{m:.1f} ns"
    ax1.text(m * 1.25, y, txt, va="center", ha="left", fontsize=9.8, fontweight="bold", color="#111827")

# Visual comparison span from BAR4 (0.52 ns) to eth0 RX (581.8 ns)
ratio = mb_meds[3] / mb_meds[0]
ax1.annotate(
    "",
    xy=(mb_meds[3], 0.0),
    xytext=(mb_meds[0] * 10.0, 0.0),
    arrowprops=dict(arrowstyle="<->", color="#C83E31", lw=1.3),
    zorder=4,
)
ax1.plot([mb_meds[3], mb_meds[3]], [0.0, 2.72], color="#C83E31", linestyle=":", linewidth=1.0, alpha=0.7, zorder=2)
ax1.text(
    np.sqrt(mb_meds[0] * 10.0 * mb_meds[3]), 0.22, f"慢 {ratio:,.0f}×", ha="center", va="bottom",
    fontsize=9.8, fontweight="bold", color="#C83E31",
    bbox=dict(boxstyle="round,pad=0.18", facecolor="#FDECEC", edgecolor="#C83E31", lw=0.8),
)

ax1.set_xscale("log")
ax1.set_xlim(0.25, 3600)
ax1.set_yticks(y_pos)
ax1.set_yticklabels([lbl for lbl, _, _ in mb_items], fontsize=9.2)
ax1.set_xlabel("单次调用耗时（ns，对数轴，10,000 次无插桩）", fontsize=9.5)
ax1.set_title("A. CN106 裸操作微基准", fontsize=11, fontweight="bold", pad=10)

# ---------------- Panel B: Card-initiated SYNTH gap + injection controls (64 B) ----------------
synth_specs = [
    ("synth_64_gap0", "gap 0", "#374151"),
    ("synth_64_gap1000", "gap 1µs", "#4B5563"),
    ("synth_64_gap3000", "gap 3µs", "#4B5563"),
    ("synth_64_gap6000", "gap 6µs", "#4B5563"),
    ("synth_64_gap6000_mbufchurn", "6µs+\nmbuf", "#6B7280"),
    ("synth_64_gap0_ethpoll", "0µs+\neth_poll", "#C83E31"),
    ("synth_64_gap6000_ethpoll", "6µs+\neth_poll", "#C83E31"),
]
sx = np.arange(len(synth_specs))
for i, (cfg, lbl, col) in enumerate(synth_specs):
    rs = by_cfg(cfg)
    p50_list = [r["synth"]["p50_us"] for r in rs]
    ep_list = [r["synth"]["eth_polls_avg"] for r in rs]
    p50_m = med(p50_list)
    ep_m = med(ep_list)
    ax2.bar(i, p50_m, width=0.66, color=col, edgecolor="#111827", linewidth=0.7, zorder=3)
    ax2.scatter(i + np.array([-0.10, 0.0, 0.10]), p50_list, color="white", edgecolor="#111827", s=20, linewidth=0.8, zorder=5)
    ax2.text(i, p50_m + 0.03, f"{p50_m:.3f}", ha="center", va="bottom", fontsize=9.1, fontweight="bold", color="#111827")
    if ep_m > 0.1:
        ax2.text(i, 0.07, f"{ep_m:.2f}次", ha="center", va="bottom", fontsize=8.4, color="white", fontweight="bold", zorder=6)

ax2.axvline(4.5, color="#D1D5DB", linestyle="--", linewidth=1.1, zorder=2)
ax2.text(2.0, 1.56, "排除低频冷态与 NPA 干扰\n(0.915–0.925 µs)", ha="center", va="top", fontsize=9.0, color="#374151",
         bbox=dict(boxstyle="round,pad=0.22", facecolor="#F3F4F6", edgecolor="#D1D5DB", lw=0.8))
ax2.text(5.5, 1.56, "注入 eth_poll\n复现 +310 ns", ha="center", va="top", fontsize=9.0, fontweight="bold", color="#C83E31",
         bbox=dict(boxstyle="round,pad=0.22", facecolor="#FDECEC", edgecolor="#C83E31", lw=0.8))

ax2.set_xticks(sx)
ax2.set_xticklabels([lbl for _, lbl, _ in synth_specs], fontsize=8.8)
ax2.set_ylabel("卡侧自发起往返 p50 (µs)", fontsize=9.8)
ax2.set_ylim(0, 1.65)
ax2.set_title("B. 卡侧 SYNTH 因果对照（64 B, W = 1）", fontsize=11, fontweight="bold", pad=10)

# ---------------- Panel C: W = 32 high-concurrency non-regression ----------------
w32_pairs = [
    ("64 B\nW = 32", "direct_baseline_64_w32", "direct_bar4_first_64_w32"),
    ("256 B\nW = 32", "direct_baseline_256_w32", "direct_bar4_first_256_w32"),
]
gx = np.arange(len(w32_pairs))
bw = 0.33
for idx, (lbl, c_base, c_bf) in enumerate(w32_pairs):
    r_base = [r["client"]["achieved_mrps"] for r in by_cfg(c_base)]
    r_bf = [r["client"]["achieved_mrps"] for r in by_cfg(c_bf)]
    m_base, m_bf = med(r_base), med(r_bf)
    ax3.bar(idx - bw / 2, m_base, width=bw, color="#9CA3AF", edgecolor="#111827", linewidth=0.7,
            label="基线" if idx == 0 else None, zorder=3)
    ax3.bar(idx + bw / 2, m_bf, width=bw, color="#1F2937", edgecolor="#111827", linewidth=0.7,
            label="--bar4-first" if idx == 0 else None, zorder=3)
    ax3.scatter((idx - bw / 2) + np.array([-0.05, 0.0, 0.05]), r_base, color="white", edgecolor="#111827", s=20, linewidth=0.8, zorder=5)
    ax3.scatter((idx + bw / 2) + np.array([-0.05, 0.0, 0.05]), r_bf, color="white", edgecolor="#111827", s=20, linewidth=0.8, zorder=5)
    ax3.text(idx - bw / 2, m_base + 0.05, f"{m_base:.3f}", ha="center", va="bottom", fontsize=9.2, fontweight="bold", color="#374151")
    ax3.text(idx + bw / 2, m_bf + 0.05, f"{m_bf:.3f}", ha="center", va="bottom", fontsize=9.2, fontweight="bold", color="#111827")
    pct = (m_bf - m_base) / m_base * 100.0
    sign = "+" if pct >= 0 else "−"
    ax3.text(idx, max(m_base, m_bf) + 0.25, f"Δ {sign}{abs(pct):.2f}%", ha="center", va="bottom", fontsize=9.2, color="#374151")

ax3.set_xticks(gx)
ax3.set_xticklabels([p[0] for p in w32_pairs], fontsize=9.6)
ax3.set_ylabel("端到端吞吐 (Mreq/s)", fontsize=9.8)
ax3.set_ylim(0, 3.55)
ax3.legend(loc="upper right", fontsize=8.6, frameon=True, facecolor="white", edgecolor="#D1D5DB")
ax3.set_title("C. W = 32 自动旁路态对照", fontsize=11, fontweight="bold", pad=10)

fig.suptitle(
    "MRV-154 根因因果对照与 W = 32 自动旁路验证（1C@2.0 GHz ⇄ cpu40@3.90 GHz, Uncore 2.20 GHz, n = 3）",
    fontsize=11.5,
    fontweight="bold",
    y=0.99,
)
save(fig, "mrv-154-causal-controls")
print("Saved mrv-154-causal-controls.{png,svg}")
