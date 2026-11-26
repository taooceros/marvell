#!/usr/bin/env python3
"""Generate Figure 4: Co-located 32 MiB KV tail latency protection, burst drop rate,
and multi-core NIC refill convergence at the PCIe Gen5 x4 64 B TLP wire ceiling.

Reads ALL verified runs directly from logs/mrv165-2026-11-25/{runs,confirm_runs}.jsonl.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

OUT_DIR = Path(__file__).resolve().parent
LOG_DIR = Path("/home/hongtao/dev/marvell/logs/mrv165-2026-11-25")

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 8.5,
    "axes.edgecolor": "#334155",
    "axes.linewidth": 0.85,
    "axes.grid": True,
    "grid.color": "#E2E8F0",
    "grid.linestyle": "--",
    "grid.linewidth": 0.65,
    "svg.fonttype": "none",
})

C_TEAL = "#0F766E"
C_RED = "#B91C1C"
C_AMBER = "#D97706"
C_SLATE = "#64748B"
C_NAVY = "#1D4ED8"
C_DARK = "#0F172A"

BURSTS = ["64KiB", "256KiB", "1MiB", "4MiB", "16MiB", "32MiB", "64MiB", "128MiB"]
BLABS = ["64K", "256K", "1M", "4M", "16M", "32M", "64M", "128M"]
MC_BURSTS = ["1MiB", "4MiB", "8MiB", "16MiB", "32MiB", "48MiB", "64MiB"]
MC_LABS = ["1M", "4M", "8M", "16M", "32M", "48M", "64M"]
PCIE_64B_CEILING = 128e9 * (128.0 / 130.0) / 8.0 / 88.0 / 1e6  # 179.02 M/s


def main():
    by_tag_kv = defaultdict(list)
    by_tag_mc = defaultdict(list)
    for fname in ("runs.jsonl", "confirm_runs.jsonl"):
        p = LOG_DIR / fname
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
            if r["part"] in ("control1_kv_buf", "control1_confirm"):
                assert r["kv"]["bad"] == 0
                by_tag_kv[r["tag"]].append(r)
            elif r["part"] in ("control2_multicore", "control2_diag"):
                by_tag_mc[(r["arm"], r["burst_label"])].append(r)

    x = np.arange(len(BURSTS), dtype=float)
    solo_p99 = statistics.median(r["kv"]["p99_ns"] for r in by_tag_kv["kv_solo_32m"])

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(11.6, 3.35))

    # --- Panel (a) ---
    ax1.axvspan(3.5, 7.4, color="#FEE2E2", alpha=0.35, zorder=0)
    ax1.axhline(solo_p99, color=C_SLATE, ls=":", lw=1.3, zorder=1)
    ax1.text(0.0, solo_p99 - 16, f"Solo KV p99: {solo_p99:.0f} ns", fontsize=6.9, color=C_SLATE)

    arms = [
        ("monotonic_128m", "Host 128 MiB Monotonic", C_RED, "o", "-", 2.0),
        ("lifo_128m", "Host 128 MiB Ideal LIFO", C_AMBER, "s", "-", 2.0),
        ("drop_256k", "Passive 256 KiB (Drop)", C_SLATE, "^", "--", 1.7),
        ("active_256k", "CreditRing (256 KiB Hot)", C_TEAL, "D", "-", 2.4),
    ]
    for key, label, col, mk, ls, lw in arms:
        meds = [statistics.median(r["kv"]["p99_ns"] for r in by_tag_kv[f"{key}_{b}"]) for b in BURSTS]
        ax1.plot(x, meds, color=col, marker=mk, ls=ls, lw=lw, ms=4.8,
                 markeredgecolor="white", markeredgewidth=0.5, label=label)

    ax1.annotate("3.7-6.3x worse\n(p999 289-315 ns)", xy=(2, 259), xytext=(0.15, 188),
                 fontsize=6.9, color=C_RED, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.0),
                 bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="#FCA5A5", alpha=0.95))
    ax1.annotate("LIFO fails >=16 MiB\n(205 ns; p999 293 ns)", xy=(6, 205), xytext=(3.15, 258),
                 fontsize=6.8, color=C_AMBER, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_AMBER, lw=1.0),
                 bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="#FCD34D", alpha=0.95))
    ax1.annotate("41-42 ns (0% drop)", xy=(6, 41), xytext=(3.85, 84),
                 fontsize=7.0, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=1.1),
                 bbox=dict(boxstyle="round,pad=0.18", fc="#F0FDFA", ec="#5EEAD4", alpha=0.95))

    ax1.set_title("(a) Co-Located 32 MiB KV p99 Latency", fontsize=9.0, fontweight="bold", pad=6)
    ax1.set_xlabel("Burst Size (512 MiB total / run)", fontsize=8.3)
    ax1.set_ylabel("KV Lookup p99 Latency (ns)", fontsize=8.3)
    ax1.set_xticks(x)
    ax1.set_xticklabels(BLABS, fontsize=7.3)
    ax1.set_xlim(-0.4, 7.4)
    ax1.set_ylim(0, 325)
    ax1.legend(loc="center left", fontsize=6.35, frameon=True, framealpha=0.94, bbox_to_anchor=(0.01, 0.43))

    # --- Panel (b) ---
    drop_meds = [statistics.median(r["drop_pct"] for r in by_tag_kv[f"drop_256k_{b}"]) for b in BURSTS]
    act_meds = [statistics.median(r["drop_pct"] for r in by_tag_kv[f"active_256k_{b}"]) for b in BURSTS]
    ax2.fill_between(x, 0, drop_meds, color="#EF4444", alpha=0.12)
    ax2.plot(x, drop_meds, "^--", color=C_RED, lw=2.0, ms=5.0, label="Passive 256 KiB (Drop)")
    ax2.plot(x, act_meds, "D-", color=C_TEAL, lw=2.4, ms=4.8, label="CreditRing (Active NIC)")
    ax2.annotate("75.0% -> 99.8%\nburst loss", xy=(4, drop_meds[4]), xytext=(2.8, 52),
                 fontsize=7.2, color=C_RED, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.05),
                 bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#FCA5A5", alpha=0.95))
    ax2.annotate("0.0% loss across\nall burst sizes", xy=(5, 0.5), xytext=(2.7, 17),
                 fontsize=7.2, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=1.05),
                 bbox=dict(boxstyle="round,pad=0.2", fc="#F0FDFA", ec="#5EEAD4", alpha=0.95))
    ax2.set_title("(b) Burst Packet Loss Rate", fontsize=9.0, fontweight="bold", pad=6)
    ax2.set_xlabel("Burst Size", fontsize=8.3)
    ax2.set_ylabel("Dropped Packets (%)", fontsize=8.3)
    ax2.set_xticks(x)
    ax2.set_xticklabels(BLABS, fontsize=7.3)
    ax2.set_xlim(-0.4, 7.4)
    ax2.set_ylim(-5, 112)
    ax2.legend(loc="upper left", fontsize=6.7, frameon=True, framealpha=0.94)

    # --- Panel (c) ---
    xm = np.arange(len(MC_BURSTS), dtype=float)
    ax3.axvspan(3.85, 5.15, color="#FEF3C7", alpha=0.45, zorder=0)
    ax3.text(4.5, 224, "Crossover\n32-48 MiB", ha="center", va="top", fontsize=6.8, color=C_AMBER, fontweight="bold")
    ax3.axhline(PCIE_64B_CEILING, color=C_DARK, ls="--", lw=1.35, zorder=2)

    mc_series = [
        ("scheme_a_128m", "Host 128 MiB Ring", C_RED, "o", "-", 2.0),
        ("active_1c", "CreditRing (1 NIC core)", C_TEAL, "D", "-", 2.3),
        ("active_2c", "CreditRing (2 NIC cores)", C_NAVY, "s", "--", 1.7),
        ("active_4c", "CreditRing (4 NIC cores)", "#7C3AED", "^", ":", 1.7),
    ]
    for pref, label, col, mk, ls, lw in mc_series:
        vals = [statistics.median(r["drain_wall_mops"] for r in by_tag_mc[(pref, b)]) for b in MC_BURSTS]
        ax3.plot(xm, vals, color=col, marker=mk, ls=ls, lw=lw, ms=4.5, label=label)

    diag_x = [3.0, 6.0]
    diag_y = [statistics.median(r["drain_wall_mops"] for r in by_tag_mc[("active_4c_1mring", b)]) for b in ("16MiB", "64MiB")]
    ax3.scatter(diag_x, diag_y, marker="*", s=65, color="#111827", zorder=6, label="4c + 1 MiB hot ring")

    ax3.annotate("PCIe Gen5 x4 64 B\nwall (179.0 M/s)", xy=(1.8, PCIE_64B_CEILING), xytext=(-0.25, 152),
                 fontsize=6.6, color=C_DARK, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_DARK, lw=1.0),
                 bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="#CBD5E1", alpha=0.95))
    ax3.annotate("+12.6% faster drain\n(178.0 vs 158.1 M/s)", xy=(6, 177.5), xytext=(2.75, 141),
                 fontsize=6.8, color=C_TEAL, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color=C_TEAL, lw=1.05),
                 bbox=dict(boxstyle="round,pad=0.18", fc="#F0FDFA", ec="#5EEAD4", alpha=0.95))

    ax3.set_title("(c) Multi-Core NIC Refill & Wire Wall", fontsize=9.0, fontweight="bold", pad=6)
    ax3.set_xlabel("Burst Size (no concurrent KV)", fontsize=8.3)
    ax3.set_ylabel("Host Drain Throughput (M msgs/s)", fontsize=8.3)
    ax3.set_xticks(xm)
    ax3.set_xticklabels(MC_LABS, fontsize=7.4)
    ax3.set_xlim(-0.4, 6.45)
    ax3.set_ylim(135, 310)
    ax3.legend(loc="upper right", fontsize=6.25, frameon=True, framealpha=0.94)

    for ax in (ax1, ax2, ax3):
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.tight_layout(pad=0.4, w_pad=1.0)
    fig.savefig(OUT_DIR / "fig4_mrv165_kv_and_refill.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT_DIR / "fig4_mrv165_kv_and_refill.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("Generated fig4_mrv165_kv_and_refill.{png,svg}")


if __name__ == "__main__":
    main()
