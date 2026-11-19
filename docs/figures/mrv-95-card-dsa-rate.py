"""MRV-95 Figure 2: Sustained card-direct Intel DSA throughput (Mops/s and GB/s)
vs concurrency window W via card DPI DMA 64 B writes -> VF0 dmar0 P2P -> dsa2/wq2.0
(ZERO host CPU forwarding, all 30 runs verified lost=0, bad=0, mismatches=0, swerror=0).
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
JSONL = ROOT / "logs" / "mrv95-2026-11-19" / "runs.jsonl"

rows = [json.loads(line) for line in JSONL.read_text().splitlines() if line.strip()]

WINDOWS = [1, 8, 32, 64, 96]
nop_mops = defaultdict(list)
mm_mops = defaultdict(list)
mm_gbps = defaultdict(list)

for r in rows:
    if r["mode"] == "rate" and r["submitter"] == "card_dpi_dma":
        assert r["lost"] == 0 and r["bad"] == 0, f"unverified rate row: {r}"
        assert "mismatches=0" in r["host_verify"] and "err=0" in r["host_verify"], f"verify fail: {r}"
        w = r["window"]
        if r["op"] == "nop":
            nop_mops[w].append(r["mops_s"])
        elif r["op"] == "memmove" and r["size"] == 4096:
            mm_mops[w].append(r["mops_s"])
            mm_gbps[w].append(r["gb_s"])

nop_med = [statistics.median(nop_mops[w]) for w in WINDOWS]
mm_med = [statistics.median(mm_mops[w]) for w in WINDOWS]
gb_med = [statistics.median(mm_gbps[w]) for w in WINDOWS]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.7))

# Left panel: Mops/s vs W
ax1.plot(WINDOWS, nop_med, "-o", color="#2563eb", linewidth=2.2, markersize=6.5, label="NOP（中位数，3 次重复）")
ax1.plot(WINDOWS, mm_med, "-s", color="#334155", linewidth=2.2, markersize=6.0, label="4 KiB MEMMOVE（中位数，3 次重复）")
for w in WINDOWS:
    ax1.scatter([w] * len(nop_mops[w]), nop_mops[w], color="#2563eb", alpha=0.35, s=22, zorder=3)
    ax1.scatter([w] * len(mm_mops[w]), mm_mops[w], color="#334155", alpha=0.35, s=22, zorder=3)

for w, v in zip(WINDOWS, nop_med):
    dx, dy = (6, 9) if w == 1 else (0, 8)
    ax1.annotate(f"{v:.2f}", (w, v), textcoords="offset points", xytext=(dx, dy),
                 ha="center", fontsize=8.8, fontweight="bold", color="#1e3a8a")
for w, v in zip(WINDOWS, mm_med):
    if w == 1:
        dx, dy = (22, -4)
    elif w == 8:
        dx, dy = (16, -11)
    elif w in (64, 96):
        dx, dy = (0, -16)
    else:
        dx, dy = (0, 9)
    ax1.annotate(f"{v:.2f}", (w, v), textcoords="offset points", xytext=(dx, dy),
                 ha="center", fontsize=8.8, color="#334155")

ax1.set_xticks(WINDOWS)
ax1.set_xlim(-3, 106)
ax1.set_ylim(0, 16.8)
ax1.set_xlabel("在途并发窗口 W（< wq2.0 深度 128）", fontsize=10.5)
ax1.set_ylabel("持续提交与完成速率 (M ops/s)", fontsize=10.5)
ax1.set_title("卡直写 DSA 持续速率（零主机转发，lost=bad=0）", fontsize=11.3, fontweight="bold", pad=10)
ax1.legend(loc="upper left", fontsize=8.8, frameon=True, facecolor="white")

# Right panel: 4 KiB MEMMOVE host LLC-resident bandwidth (10^9 GB/s) vs W
ax2.plot(WINDOWS, gb_med, "-s", color="#2563eb", linewidth=2.4, markersize=6.5,
         label="dsa2 热 LLC 搬运带宽（4 KiB，≤768 KiB 工作集，CC=1）")
for w in WINDOWS:
    ax2.scatter([w] * len(mm_gbps[w]), mm_gbps[w], color="#2563eb", alpha=0.35, s=24, zorder=3)

pcie_desc_gbps = [m * 64.0 / 1000.0 for m in mm_med]
ax2.plot(WINDOWS, pcie_desc_gbps, "--^", color="#64748b", linewidth=1.6, markersize=5.0,
         label="仅描述符 PCIe 写流量（64 B/op，峰 0.46 GB/s；含轮询总比约 6–7×）")

for w, v in zip(WINDOWS, gb_med):
    if w == 1:
        dx, dy = (22, 9)
    elif w == 8:
        dx, dy = (26, -10)
    elif w == 64:
        dx, dy = (0, 11)
    else:
        dx, dy = (0, 10)
    ax2.annotate(f"{v:.2f} GB/s", (w, v), textcoords="offset points", xytext=(dx, dy),
                 ha="center", fontsize=8.8, fontweight="bold", color="#1e3a8a")

ax2.set_xticks(WINDOWS)
ax2.set_xlim(-3, 106)
ax2.set_ylim(0, 42.5)
ax2.set_xlabel("在途并发窗口 W（< wq2.0 深度 128）", fontsize=10.5)
ax2.set_ylabel("主机热 LLC 搬运带宽 (10^9 GB/s)", fontsize=10.5)
ax2.set_title("4 KiB 热 LLC 搬运带宽（768 KiB 工作集，CC=1）", fontsize=11.0, fontweight="bold", pad=10)
ax2.legend(loc="upper left", fontsize=8.0, frameon=True, facecolor="white")

fig.suptitle("MRV-95 · 卡 DPI DMA 直写 dsa2/wq2.0（1 引擎 · SVA PASID · 热 LLC · 零主机转发 · 全部 30 组 lost=bad=0）",
             fontsize=10.3, y=0.99, color="#475569")

out = save(fig, "mrv-95-card-dsa-rate")
print("Saved:", out)
