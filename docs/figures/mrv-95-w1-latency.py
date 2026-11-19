"""MRV-95 Figure 1: W=1 latency (host MOVDIR64B vs card-direct DPI DMA, zero host
forwarding) and card-side 64 B submission primitive silent-drop rate at DWQ portal.
Reads raw records from logs/mrv95-2026-11-19/runs.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
JSONL = ROOT / "logs" / "mrv95-2026-11-19" / "runs.jsonl"

rows = [json.loads(line) for line in JSONL.read_text().splitlines() if line.strip()]

# 1. Verified W=1 latency (bad=0, mismatches=0, swerror=0)
OPS = [("nop", 0, "NOP"), ("memmove", 64, "64 B"), ("memmove", 4096, "4 KiB"),
       ("memmove", 65536, "64 KiB"), ("memmove", 1048576, "1 MiB")]
lat = defaultdict(list)
rate_w1 = defaultdict(list)
poll_only = []
ablation = defaultdict(lambda: [0, 0])  # [bad, rounds]

for r in rows:
    if r["mode"] == "poll_only" and r["submitter"] == "card_dpi_read":
        poll_only.append(r["p50_ns"] / 1000.0)
    elif r["mode"] == "w1_lat":
        sub = r["submitter"]
        if sub in ("host_movdir64b", "card_dpi_dma"):
            assert r.get("bad", 0) == 0, f"unexpected bad in {r}"
            assert r.get("mismatches", 0) == 0 or "mismatches=0" in r.get("host_verify", ""), f"mismatch in {r}"
            lat[(sub, r["op"], r["size"])].append(r["p50_ns"] / 1000.0)
        if sub in ("card_pem_wc_dsb", "card_pem_wc_dgh", "card_pem_wc_none", "card_dpi_dma"):
            ablation[sub][0] += r.get("bad", 0)
            ablation[sub][1] += r["rounds"]
    elif r["mode"] == "rate" and r["submitter"] == "card_dpi_dma" and r["window"] == 1:
        assert r["lost"] == 0 and r["bad"] == 0, f"unexpected bad in {r}"
        rate_w1[(r["op"], r["size"])].append(1.0 / r["mops_s"])

poll_med_us = statistics.median(poll_only)
host_med = [statistics.median(lat[("host_movdir64b", op, sz)]) for op, sz, _ in OPS]
card_med = [statistics.median(lat[("card_dpi_dma", op, sz)]) for op, sz, _ in OPS]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.7), gridspec_kw={"width_ratios": [1.45, 1.0]})

# Left panel: W=1 latency comparison (log scale to read 0.4 us .. 35 us cleanly)
x = np.arange(len(OPS))
w = 0.36
b1 = ax1.bar(x - w / 2, host_med, width=w, color="#94a3b8", edgecolor="#475569", linewidth=0.8,
             label="主机 MOVDIR64B（对照，bad=0）")
b2 = ax1.bar(x + w / 2, card_med, width=w, color="#2563eb", edgecolor="#1e40af", linewidth=0.8,
             label="卡直写 DPI DMA 单次 p50（不含清零，bad=0）")

# Plot closed-loop 1/rate(W=1) on NOP (x=0) and 4 KiB (x=2) from raw rate runs
cl_x = [0 + w / 2, 2 + w / 2]
cl_y = [statistics.median(rate_w1[("nop", 0)]), statistics.median(rate_w1[("memmove", 4096)])]
ax1.scatter(cl_x, cl_y, marker="D", s=44, color="#0f172a", zorder=5,
            label="卡闭环 1/rate(W=1)（含清零+轮询台阶）")
ax1.annotate(f"闭环 {cl_y[1]:.2f}", (cl_x[1], cl_y[1]), textcoords="offset points", xytext=(0, 8),
             ha="center", fontsize=8.0, fontweight="bold", color="#0f172a")

ax1.axhline(poll_med_us, color="#64748b", linestyle="--", linewidth=1.2,
            label=f"卡单次 DPI 读回 comp 底噪 ({poll_med_us:.2f} µs)")

ax1.set_yscale("log")
ax1.set_ylim(0.25, 95)
ax1.set_xticks(x)
ax1.set_xticklabels([lbl for _, _, lbl in OPS], fontsize=10.5)
ax1.set_ylabel("W=1 延迟 (µs，对数坐标)", fontsize=10.5)
ax1.set_title("W=1 端到端延迟：主机直发 vs 卡直写（零转发）", fontsize=11.5, fontweight="bold", pad=10)

for rect, val in zip(b1, host_med):
    ax1.text(rect.get_x() + rect.get_width() / 2, val * 1.10, f"{val:.2f}",
             ha="center", va="bottom", fontsize=8.5, color="#334155")
for i, (rect, val) in enumerate(zip(b2, card_med)):
    va_pos = val * 1.12 if i != 2 else val * 1.07
    ax1.text(rect.get_x() + rect.get_width() / 2, va_pos, f"{val:.2f}",
             ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#1e3a8a")

ax1.legend(loc="upper left", fontsize=8.1, frameon=True, facecolor="white", framealpha=0.92)

# Right panel: Card submission primitive DWQ silent drop rate
SUBS = [
    ("card_pem_wc_dsb", "PEM0 WC\n+ dsb st\n(off=0)", "#94a3b8"),
    ("card_pem_wc_dgh", "PEM0 WC\n+ DGH\n(轮转8行)", "#94a3b8"),
    ("card_pem_wc_none", "PEM0 WC\n无栅栏\n(轮转8行)", "#64748b"),
    ("card_dpi_dma", "DPI DMA\n64 B 写\n(硬件原子)", "#2563eb"),
]
rates = []
counts = []
for key, _, _ in SUBS:
    bad, tot = ablation[key]
    rates.append(100.0 * bad / tot)
    counts.append((bad, tot))

x2 = np.arange(len(SUBS))
bars = ax2.bar(x2, rates, width=0.55, color=[c for _, _, c in SUBS], edgecolor="#334155", linewidth=0.8)
ax2.set_xticks(x2)
ax2.set_xticklabels([lbl for _, lbl, _ in SUBS], fontsize=9.5)
ax2.set_ylabel("DWQ 门户静默丢弃率 (%)", fontsize=10.5)
ax2.set_ylim(0, max(rates) * 1.32)
ax2.set_title("卡侧 64 B 提交原语的 DWQ 静默丢弃率（SWERROR=0）", fontsize=11.0, fontweight="bold", pad=10)

for rect, rate, (bad, tot), (key, _, _) in zip(bars, rates, counts, SUBS):
    color = "#1e3a8a" if key == "card_dpi_dma" else "#334155"
    weight = "bold" if key == "card_dpi_dma" else "normal"
    ax2.text(rect.get_x() + rect.get_width() / 2, rate + 0.04,
             f"{rate:.3f}%\n({bad}/{tot})", ha="center", va="bottom",
             fontsize=8.5, fontweight=weight, color=color)

fig.suptitle("MRV-95 · CN106 直驱同插槽 Intel DSA (dsa2/wq2.0 · 1 引擎 · 主机核 3.9 GHz / Uncore 2.2 GHz · 卡核 2.0 GHz)",
             fontsize=10.5, y=0.99, color="#475569")

out = save(fig, "mrv-95-w1-latency")
print("Saved:", out)
