#!/usr/bin/env python3
"""MRV-165 Figure 1: Concurrent Socket-1 32 MiB KV p99 tail latency & packet drop rate
across 4 host receive buffer organizations (64 KiB -> 128 MiB bursts).

Reads ALL verified runs from:
  - logs/mrv165-2026-11-25/runs.jsonl (Control 1 primary n=3)
  - logs/mrv165-2026-11-25/confirm_runs.jsonl (paired clean-host confirmation n=3)
Zero filtering or cherry-picking: lines plot pooled medians; faint dots plot every single run.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

import mrvplot  # noqa: F401 - registers CJK font & white style

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs/mrv165-2026-11-25"
BURSTS = ["64KiB", "256KiB", "1MiB", "4MiB", "16MiB", "32MiB", "64MiB", "128MiB"]
BURST_XLABELS = ["64K", "256K", "1M", "4M", "16M", "32M", "64M", "128M"]


def load_data():
    by_tag = defaultdict(list)
    for fname in ("runs.jsonl", "confirm_runs.jsonl"):
        p = LOG_DIR / fname
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            r = json.loads(line)
            if r["part"] in ("control1_kv_buf", "control1_confirm"):
                assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
                assert r["kv"]["bad"] == 0
                by_tag[r["tag"]].append(r)
    return by_tag


def main():
    by_tag = load_data()
    x = np.arange(len(BURSTS), dtype=float)

    solo_p99 = statistics.median(r["kv"]["p99_ns"] for r in by_tag["kv_solo_32m"])

    arms = [
        ("monotonic_128m", "主机 128 MiB 单调大环", "#C82829", "o", "-", 2.3, -0.10, 4),
        ("lifo_128m", "主机 128 MiB LIFO 复用池", "#D97706", "s", "-", 2.3, -0.03, 5),
        ("drop_256k", "主机 256 KiB 热环（满即丢包）", "#64748B", "^", "--", 2.0, +0.05, 5),
        ("active_256k", "主动网卡留存 + 256 KiB 热环", "#0F766E", "D", "-", 3.0, +0.10, 6),
    ]

    fig, (ax_lat, ax_drop) = plt.subplots(1, 2, figsize=(12.8, 5.45))

    # --- Left panel: KV p99 tail latency ---
    ax_lat.axvspan(3.5, 7.45, color="#FEE2E2", alpha=0.33, zorder=0)
    ax_lat.text(
        5.48, 290, "大突发区（超出 60 MiB 共享 L3）",
        ha="center", va="top", fontsize=9.8, color="#991B1B",
    )
    ax_lat.axhline(solo_p99, color="#475569", linestyle=":", linewidth=1.4, zorder=1)
    ax_lat.text(
        0.02, solo_p99 - 14, f"单跑 KV 基线 {solo_p99:.0f} ns",
        ha="left", va="top", fontsize=9.2, color="#475569",
    )

    for arm_key, label, color, marker, ls, lw, dx, z in arms:
        meds = []
        for i, b in enumerate(BURSTS):
            rs = by_tag[f"{arm_key}_{b}"]
            vals = [r["kv"]["p99_ns"] for r in rs]
            meds.append(statistics.median(vals))
            offsets = np.linspace(-0.06, 0.06, len(vals)) + dx
            ax_lat.scatter(
                np.full(len(vals), i) + offsets,
                vals,
                color=color,
                marker=marker,
                s=22,
                alpha=0.28,
                edgecolors="none",
                zorder=z - 1,
            )
        ax_lat.plot(
            x + dx * 0.5,
            meds,
            color=color,
            marker=marker,
            linestyle=ls,
            linewidth=lw,
            markersize=6.8,
            markeredgecolor="white",
            markeredgewidth=0.7,
            label=label,
            zorder=z,
        )

    # Zero-overlap callouts placed in wide-open whitespace
    ax_lat.annotate(
        "单调环全程拖慢\n3.7–6.3 倍 (153–259 ns)",
        xy=(1.95, 256), xytext=(0.25, 194),
        fontsize=9.3, color="#B91C1C",
        arrowprops=dict(arrowstyle="->", color="#C82829", lw=1.2),
        bbox=dict(boxstyle="round,pad=0.30", fc="white", ec="#FCA5A5", alpha=0.96),
    )
    ax_lat.annotate(
        "LIFO 仅救 ≤4 MiB\n64 MiB 仍飙至 205 ns (5.0×)",
        xy=(6.0, 208), xytext=(4.92, 242),
        fontsize=9.2, color="#B45309",
        arrowprops=dict(arrowstyle="->", color="#D97706", lw=1.2),
        bbox=dict(boxstyle="round,pad=0.30", fc="white", ec="#FCD34D", alpha=0.96),
    )
    ax_lat.annotate(
        "主动网卡全程 41 ns\n0 丢包，与单跑一致",
        xy=(6.05, 44), xytext=(4.85, 84),
        fontsize=9.3, color="#0F766E",
        arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.3),
        bbox=dict(boxstyle="round,pad=0.30", fc="#F0FDFA", ec="#5EEAD4", alpha=0.96),
    )
    ax_lat.annotate(
        "极少数共享主机\n背景抖动单轮点",
        xy=(1.08, 131), xytext=(1.55, 130),
        fontsize=8.3, color="#64748B",
        arrowprops=dict(arrowstyle="->", color="#94A3B8", lw=0.9),
    )

    ax_lat.set_title("① 同 CPU 并发 KV 查表 p99 尾延迟（越低越好）", fontsize=11.5, pad=10)
    ax_lat.set_xlabel("单次突发大小（每轮共发送 512 MiB）", fontsize=10.5, labelpad=7)
    ax_lat.set_ylabel("KV 随机查表 p99 尾延迟 (ns)", fontsize=10.5)
    ax_lat.set_xticks(x)
    ax_lat.set_xticklabels(BURST_XLABELS, fontsize=10)
    ax_lat.set_xlim(-0.45, 7.45)
    ax_lat.set_ylim(0, 302)
    ax_lat.yaxis.set_major_locator(mticker.MultipleLocator(50))

    # --- Right panel: Packet drop rate ---
    drop_meds = [statistics.median(r["drop_pct"] for r in by_tag[f"drop_256k_{b}"]) for b in BURSTS]
    active_meds = [statistics.median(r["drop_pct"] for r in by_tag[f"active_256k_{b}"]) for b in BURSTS]

    ax_drop.fill_between(x, 0, drop_meds, color="#EF4444", alpha=0.10, zorder=1)
    ax_drop.plot(
        x, drop_meds,
        color="#64748B", marker="^", linestyle="--", linewidth=2.4,
        markersize=7.2, markeredgecolor="white", markeredgewidth=0.7,
        zorder=4,
    )
    ax_drop.plot(
        x, active_meds,
        color="#0F766E", marker="D", linestyle="-", linewidth=3.0,
        markersize=6.6, markeredgecolor="white", markeredgewidth=0.7,
        zorder=5,
    )

    ax_drop.text(2.38, 72.0, "75.0%", ha="left", va="center", fontsize=9.4, color="#991B1B")
    ax_drop.text(3.15, 86.0, "93.8%", ha="left", va="top", fontsize=9.4, color="#991B1B")
    ax_drop.text(6.95, 92.5, "99.8%", ha="center", va="top", fontsize=9.4, color="#991B1B")

    ax_drop.annotate(
        "被动 256 KiB 热环：\n突发超 256 KiB 即大量丢包\n（1 MiB 丢 75% → 128 MiB 丢 99.8%）",
        xy=(4.5, 97.5), xytext=(2.85, 50),
        fontsize=9.4, color="#475569",
        arrowprops=dict(arrowstyle="->", color="#64748B", lw=1.2),
        bbox=dict(boxstyle="round,pad=0.32", fc="white", ec="#CBD5E1", alpha=0.96),
    )
    ax_drop.annotate(
        "主动网卡留存（及两档主机大环）：\n全程 0.0% 丢包（零丢失 / 零损坏）",
        xy=(5.0, 1.5), xytext=(2.45, 17),
        fontsize=9.4, color="#0F766E",
        arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.3),
        bbox=dict(boxstyle="round,pad=0.30", fc="#F0FDFA", ec="#5EEAD4", alpha=0.96),
    )

    ax_drop.set_title("② 突发丢包率（越低越好）", fontsize=11.5, pad=10)
    ax_drop.set_xlabel("单次突发大小（每轮共发送 512 MiB）", fontsize=10.5, labelpad=7)
    ax_drop.set_ylabel("突发消息丢包率 (%)", fontsize=10.5)
    ax_drop.set_xticks(x)
    ax_drop.set_xticklabels(BURST_XLABELS, fontsize=10)
    ax_drop.set_xlim(-0.45, 7.45)
    ax_drop.set_ylim(-6, 112)
    ax_drop.yaxis.set_major_locator(mticker.MultipleLocator(25))

    fig.suptitle(
        "同 CPU 并发 32 MiB KV 业务：只有「主动网卡留存」既保住 41 ns 尾延迟、又 0 丢包",
        fontsize=13.5, y=0.985,
    )
    handles, labels = ax_lat.get_legend_handles_labels()
    fig.legend(
        handles, labels,
        loc="upper center", bbox_to_anchor=(0.5, 0.925),
        ncol=4, frameon=False, fontsize=9.8, handlelength=2.4, columnspacing=1.5,
    )

    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.875))
    png = Path(__file__).resolve().parent / "mrv-165-kv-protection.png"
    svg = Path(__file__).resolve().parent / "mrv-165-kv-protection.svg"
    fig.savefig(png, dpi=200)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
