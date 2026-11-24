#!/usr/bin/env python3
"""MRV-177 Figure 3: Closed-loop W=32/64/128 (symmetric C_line=64 & unshaped)
throughput cliff breakthrough, card TAD snoop/ll_cache_rd accounting,
early-doorbell ordering proof, and 4C TAD retries.

Reads verified runs directly from logs/mrv177-2026-11-24/runs.jsonl.
"""
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv177-2026-11-24/runs.jsonl"


def load_verified():
    by_cfg = defaultdict(list)
    with RUNS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            cp = r["card_pmu"]
            hp = r["host_pmu"]
            corrupt = sum(c["corrupt"] for c in cp["cores"])
            assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0 and corrupt == 0
            assert hp["min_pct"] >= 99.99 and cp["min_pct"] >= 99.99
            by_cfg[(r["phase"], r["workload"], r["arm"])].append(r)
    return by_cfg


def med_err(vals):
    s = sorted(vals)
    m = s[len(s) // 2]
    return m, m - s[0], s[-1] - m


def snoop_per_msg(r):
    cp = r["card_pmu"]
    tt = sum(c["taken"] for c in cp["cores"])
    return cp["tad"]["tad_snp_msh_out_any"]["val"] / max(1, tt)


def ll_rd_per_msg(r):
    cp = r["card_pmu"]
    tt = sum(c["taken"] for c in cp["cores"])
    return sum(c["ll_cache_rd"] for c in cp["cores"]) / max(1, tt)


def retry_per_msg(r):
    cp = r["card_pmu"]
    tt = sum(c["taken"] for c in cp["cores"])
    return cp["tad"]["tad_rsp_msh_out_retry_exlmn"]["val"] / max(1, tt)


def main():
    by_cfg = load_verified()

    fig, axes = plt.subplots(2, 2, figsize=(14.4, 9.4))
    fig.suptitle(
        "MRV-177 闭环高并发（W=32/64/128 对称 C_line=64 与无整形）破 13.1 M/s 掉档与硬件 PMU 归因（n = 3 中位数）",
        fontsize=12.6,
        fontweight="bold",
        y=0.982,
    )

    arm_styles = {
        "base": ("Baseline（紧盯 next）", "#475569", "o", "-"),
        "bq_probe": ("Arm A bq_probe", "#D97706", "s", "-"),
        "bq_wait": ("Arm A 变体 bq_wait", "#7C3AED", "^", "--"),
        "db_pkt_f": ("Arm B1 逐包门铃", "#DC2626", "v", "-"),
        "db_adp_f": ("Arm B2 批合并门铃", "#0F766E", "D", "-"),
    }

    # Symmetric 4-point x-axis on both 1C and 4C:
    # W=32, W=64, W=128 (C_line=64) [all hw_cap<=64], and W=128 (unshaped)
    x_pos = np.array([0, 1, 2, 3], dtype=float)
    x_labels = ["W=32", "W=64", "W=128\n(C_line=64)", "W=128\n(无整形)"]

    for col, phase in enumerate(["1C", "4C"]):
        ax = axes[0, col]
        ax.axvspan(2.5, 3.72, color="#F8FAFC", zorder=0)
        ax.axvline(2.5, color="#CBD5E1", linestyle="--", linewidth=1.1, zorder=1)
        ax.axhline(13.1, color="#94A3B8", linestyle="--", linewidth=1.1, zorder=1)
        ax.text(-0.22, 10.6 if phase == "4C" else 10.7, "13.1 M/s 掉档线", fontsize=7.8, color="#64748B", ha="left")
        ax.text(3.10, 53.8 if phase == "1C" else 46.0, "无整形对照", fontsize=8.0, color="#64748B",
                ha="center", fontweight="bold")

        for arm in ["base", "bq_probe", "bq_wait", "db_pkt_f", "db_adp_f"]:
            lbl, color, mk, ls = arm_styles[arm]
            if arm == "bq_probe":
                lbl = "Arm A bq_probe (fast_dt=0)" if phase == "1C" else "Arm A bq_probe (fast_dt=480ns)"

            pts = [
                by_cfg[(phase, "w32", arm)],
                by_cfg[(phase, "w64", arm)],
                by_cfg[(phase, "w128", f"{arm}_c64")],
                by_cfg[(phase, "w128", arm)],
            ]
            ys, los, his = [], [], []
            for rs in pts:
                m, lo, hi = med_err([r["achieved_mps"] for r in rs])
                ys.append(m); los.append(lo); his.append(hi)

            lw = 2.2 if arm == "db_adp_f" else 1.7
            ms = 6.8 if arm == "db_adp_f" else 5.8
            z = 4 if arm == "db_adp_f" else 3

            # Symmetric C_line=64 regime (x=0,1,2)
            ax.errorbar(
                x_pos[:3], ys[:3], yerr=[los[:3], his[:3]], label=lbl, color=color,
                marker=mk, linestyle=ls, linewidth=lw, markersize=ms, capsize=2.8, zorder=z,
            )
            # Dotted bridge into unshaped W=128 column (x=2->3)
            ax.plot(
                x_pos[2:], ys[2:], color=color, linestyle=":", linewidth=1.3, alpha=0.65, zorder=z - 1,
            )
            ax.errorbar(
                [x_pos[3]], [ys[3]], yerr=[[los[3]], [his[3]]], color=color,
                marker=mk, linestyle="none", markersize=ms, capsize=2.8, zorder=z,
            )

        if phase == "1C":
            m_base64 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w64", "base")]])[0]
            m_base128_c64 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "base_c64")]])[0]
            p99_base128_c64 = med_err([r["tot_us"]["p99"] for r in by_cfg[("1C", "w128", "base_c64")]])[0]
            m_base128 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "base")]])[0]
            m_bqp64 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w64", "bq_probe")]])[0]
            m_bqw64 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w64", "bq_wait")]])[0]
            m_bqw128_c64 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "bq_wait_c64")]])[0]
            m_bqw128 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "bq_wait")]])[0]
            m_adp128_c64 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "db_adp_f_c64")]])[0]
            p99_adp128_c64 = med_err([r["tot_us"]["p99"] for r in by_cfg[("1C", "w128", "db_adp_f_c64")]])[0]
            m_adp128 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "db_adp_f")]])[0]
            m_dbp128 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "db_pkt_f")]])[0]
            drop64 = (1.0 - m_bqw64 / m_base64) * 100.0
            drop128 = (1.0 - m_bqw128_c64 / m_base128_c64) * 100.0

            ax.annotate(
                f"无整形 {m_adp128:.2f} M/s (Base 的 {m_adp128/m_base128:.2f}×)\n"
                f"C=64: {m_adp128_c64:.2f} (p99 {p99_adp128_c64:.2f} vs Base {p99_base128_c64:.2f} µs)",
                xy=(3.0, m_adp128), xytext=(1.88, 46.8),
                ha="center", fontsize=7.5, fontweight="bold", color="#0F766E",
                bbox=dict(boxstyle="round,pad=0.24", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
                arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.0),
            )
            ax.annotate(
                f"bq_wait 有限窗倒退 {drop64:.0f}%–{drop128:.0f}%\n"
                f"{m_bqw64:.2f}/{m_bqw128_c64:.2f} vs Base {m_base64:.2f}/{m_base128_c64:.2f}",
                xy=(2.0, m_bqw128_c64 + 0.5), xytext=(1.70, 23.4),
                ha="center", fontsize=7.3, fontweight="bold", color="#6D28D9",
                bbox=dict(boxstyle="round,pad=0.22", facecolor="#F5F3FF", edgecolor="#7C3AED", lw=0.8),
                arrowprops=dict(arrowstyle="->", color="#7C3AED", lw=0.9),
            )
            ax.annotate(
                f"bq_probe 嗅探地雷：{m_base64:.1f} → {m_bqp64:.2f}",
                xy=(1.0, m_bqp64), xytext=(0.10, 2.3),
                fontsize=7.6, fontweight="bold", color="#B45309",
                bbox=dict(boxstyle="round,pad=0.22", facecolor="#FFFBEB", edgecolor="#D97706", lw=0.8),
                arrowprops=dict(arrowstyle="->", color="#D97706", lw=1.0),
            )
            ax.text(3.09, m_bqw128 - 0.6, f"bq_wait {m_bqw128:.1f}", fontsize=7.7, color="#6D28D9")
            ax.text(3.09, m_base128 + 0.5, f"Base {m_base128:.2f}", fontsize=7.7, color="#475569", fontweight="bold")
            ax.text(3.09, m_dbp128 - 0.6, f"逐包 {m_dbp128:.2f}", fontsize=7.7, color="#B91C1C", fontweight="bold")
        else:
            m_adp64 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w64", "db_adp_f")]])[0]
            m_adp128_c64 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "db_adp_f_c64")]])[0]
            m_adp128 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "db_adp_f")]])[0]
            m_adp128_uf, lo_uf, hi_uf = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "db_adp_uf")]])
            m_base64 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w64", "base")]])[0]
            m_base128 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "base")]])[0]
            m_bqp128_c64 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "bq_probe_c64")]])[0]
            m_bqw128 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "bq_wait")]])[0]
            m_dbp128 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "db_pkt_f")]])[0]

            # Highlight 4C W=128 db_adp_uf (no SFENCE) drop at x=3
            ax.errorbar(
                [3.0], [m_adp128_uf], yerr=[[lo_uf], [hi_uf]], color="#0284C7",
                marker="X", linestyle="none", markersize=7.8, capsize=2.8, zorder=5,
                label="Arm B2 无 SFENCE (W=128)",
            )
            ax.annotate(
                f"C_line=64 稳住 {m_adp128_c64:.2f} M/s\n"
                f"(W=64: {m_adp64:.2f}, Base 的 {m_adp64/m_base64:.2f}×)",
                xy=(2.0, m_adp128_c64 + 0.4), xytext=(1.92, 42.5),
                ha="center", fontsize=7.5, fontweight="bold", color="#0F766E",
                bbox=dict(boxstyle="round,pad=0.24", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
                arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.0),
            )
            ax.annotate(
                f"4C bq_probe ({m_bqp128_c64:.2f}): tbt_ns=200 < 307.7 ns\n"
                "参数代数死锁 (hit=0%, bt=100%)；\n"
                "物理限制见左图 1C 倒退与图 4 泊松",
                xy=(1.5, 13.3), xytext=(1.88, 25.0),
                ha="center", fontsize=7.1, fontweight="bold", color="#B45309",
                bbox=dict(boxstyle="round,pad=0.22", facecolor="#FFFBEB", edgecolor="#D97706", lw=0.85),
                arrowprops=dict(arrowstyle="->", color="#D97706", lw=0.95),
            )
            ax.text(3.09, m_adp128 - 0.5, f"无整形 {m_adp128:.2f}", fontsize=7.7, color="#0F766E", fontweight="bold")
            ax.text(3.09, m_bqw128 + 1.0, f"bq_wait {m_bqw128:.1f}", fontsize=7.5, color="#6D28D9")
            ax.text(3.09, m_adp128_uf - 1.3, f"无SFENCE {m_adp128_uf:.2f}", fontsize=7.5, color="#0284C7", fontweight="bold")
            ax.text(3.09, m_base128 - 1.5, f"Base {m_base128:.2f}", fontsize=7.5, color="#475569", fontweight="bold")
            ax.text(3.09, m_dbp128 - 0.5, f"逐包 {m_dbp128:.2f}", fontsize=7.5, color="#B91C1C", fontweight="bold")

        ax.set_xticks(x_pos)
        ax.set_xticklabels(x_labels, fontsize=8.8)
        ax.set_xlim(-0.28, 3.72)
        ax.set_ylim(0, 57 if phase == "1C" else 49)
        ax.set_ylabel("闭环吞吐 (Mmsg/s)", fontsize=9.3)
        ax.set_title(f"{phase} 闭环吞吐：W≤64 与 W=128 对称 C_line=64 vs 无整形", fontsize=10.2, fontweight="bold")
        ax.legend(loc="upper left", fontsize=7.3, frameon=True)

    # Bottom-left: Card TAD snoop/msg + 4C high-inflight db_adp snoop accounting
    ax = axes[1, 0]
    groups = [
        ("1C W=64", "1C", "w64", False),
        ("1C W=128\n(无整形)", "1C", "w128", False),
        ("4C W=64", "4C", "w64", False),
        ("4C W=128\n(C_line=64)", "4C", "w128", True),
    ]
    bar_arms = [
        ("base", "Baseline", "#475569"),
        ("bq_probe", "Arm A bq_probe", "#D97706"),
        ("bq_wait", "Arm A bq_wait", "#7C3AED"),
        ("db_pkt_f", "Arm B1 逐包", "#DC2626"),
        ("db_adp_f", "Arm B2 批合并", "#0F766E"),
    ]
    gx = np.arange(len(groups))
    bw = 0.155
    for idx, (arm, lbl, color) in enumerate(bar_arms):
        ys, los, his = [], [], []
        for _, ph, wl, use_c64 in groups:
            akey = f"{arm}_c64" if use_c64 else arm
            m, lo, hi = med_err([snoop_per_msg(r) for r in by_cfg[(ph, wl, akey)]])
            ys.append(m); los.append(lo); his.append(hi)
        offs = gx + (idx - 2) * bw
        ax.bar(
            offs, ys, width=bw, color=color, edgecolor="#1E293B", linewidth=0.55,
            yerr=[los, his], capsize=1.8, label=lbl, alpha=0.92,
        )
        for j, v in enumerate(ys):
            if v >= 2.0 or arm == "db_adp_f" or (j == 1 and arm == "bq_wait"):
                dx = -0.04 if (j >= 2 and arm == "base") else (0.04 if (j >= 2 and arm == "bq_probe") else 0.0)
                dy = 0.45 if (j >= 2 and arm == "bq_probe") else 0.13
                ax.text(offs[j] + dx, v + his[j] + dy, f"{v:.2f}", ha="center", va="bottom", fontsize=7.0,
                        fontweight="bold" if arm in ("db_adp_f", "db_pkt_f") else "normal", color="#0F172A")

    # 5th group: 4C high-inflight db_adp degradation snoop breakdown
    ax.axvline(3.52, color="#CBD5E1", linestyle="--", linewidth=1.1, zorder=1)
    ax.axvspan(3.52, 4.55, color="#F0F9FF", alpha=0.55, zorder=0)
    hi_specs = [
        ("W128\n无整形", ("4C", "w128", "db_adp_f"), "#0D9488"),
        ("W128\n无SF", ("4C", "w128", "db_adp_uf"), "#0284C7"),
        ("25M\n无整形", ("4C", "p25", "db_adp_f"), "#1D4ED8"),
    ]
    hi_xs = [3.74, 4.02, 4.30]
    for xi, (sub_lbl, key, col_c) in zip(hi_xs, hi_specs):
        m, lo, hi = med_err([snoop_per_msg(r) for r in by_cfg[key]])
        ax.bar(
            [xi], [m], width=0.24, color=col_c, edgecolor="#0F172A", linewidth=0.6,
            yerr=[[lo], [hi]], capsize=2.0, alpha=0.92,
        )
        ax.text(xi, m + hi + 0.14, f"{m:.2f}", ha="center", va="bottom", fontsize=7.2,
                fontweight="bold", color="#0C4A6E")
        ax.text(xi, 0.20, sub_lbl, ha="center", va="bottom", fontsize=6.4, color="#FFFFFF", fontweight="bold")
    snp_w64 = med_err([snoop_per_msg(r) for r in by_cfg[("4C", "w64", "db_adp_f")]])[0]
    snp_uf = med_err([snoop_per_msg(r) for r in by_cfg[("4C", "w128", "db_adp_uf")]])[0]
    snp_p25_c64 = med_err([snoop_per_msg(r) for r in by_cfg[("4C", "p25", "db_adp_f_c64")]])[0]
    snp_p25_un = med_err([snoop_per_msg(r) for r in by_cfg[("4C", "p25", "db_adp_f")]])[0]
    dsnp_lo = snp_p25_un - snp_p25_c64
    dsnp_hi = snp_uf - snp_w64
    ll_d_lo = med_err([ll_rd_per_msg(r) for r in by_cfg[("4C", "p25", "db_adp_f")]])[0] - med_err([ll_rd_per_msg(r) for r in by_cfg[("4C", "p25", "db_adp_f_c64")]])[0]
    ll_d_hi = med_err([ll_rd_per_msg(r) for r in by_cfg[("4C", "w128", "db_adp_uf")]])[0] - med_err([ll_rd_per_msg(r) for r in by_cfg[("4C", "w64", "db_adp_f")]])[0]
    db_cap_lo = 4.0 / med_err([r["pkts_per_db"] for r in by_cfg[("4C", "w128", "db_adp_uf")]])[0]
    db_cap_hi = 4.0 / med_err([r["pkts_per_db"] for r in by_cfg[("4C", "p25", "db_adp_f")]])[0]

    acct_txt = (
        f"4C 高在途 db_adp 嗅探跃升 (+{dsnp_lo:.2f}..+{dsnp_hi:.2f}/包)\n"
        f"• 共享门铃行 1W4R 理论上限仅 {db_cap_lo:.2f}–{db_cap_hi:.2f}/包\n"
        f"• ll_cache_rd 同步 +{ll_d_lo:.2f}..+{ll_d_hi:.2f}/包（l1d 几乎不变）\n"
        "• 主因：64B 共占 128B L3 行 + N2 L2 步长预取越界撞在写批"
    )
    ax.text(
        0.015, 0.77, acct_txt, transform=ax.transAxes, ha="left", va="top", fontsize=7.1,
        color="#0C4A6E",
        bbox=dict(boxstyle="round,pad=0.30", facecolor="#F0F9FF", edgecolor="#0284C7", lw=0.85),
    )

    ax.set_xticks(list(gx) + [4.02])
    ax.set_xticklabels([g[0] for g in groups] + ["4C 高在途\ndb_adp 退化"], fontsize=8.3)
    ax.set_xlim(-0.5, 4.55)
    ax.set_ylabel("卡侧 TAD snoop / 包", fontsize=9.3)
    ax.set_ylim(0, 10.8)
    ax.set_title("卡侧硬件 PMU：TAD 跨核/缓存嗅探次数与 4C 高在途归因（warmup+measured）", fontsize=10.0, fontweight="bold")
    ax.legend(loc="upper left", fontsize=7.3, frameon=True, ncol=2)

    # Bottom-right: SFENCE ordering proof (db_ahead_empty) + 4C TAD retry/msg proof
    ax = axes[1, 1]
    ae_items = [
        ("1C W=64\ndb_pkt_uf", "1C", "w64", "db_pkt_uf"),
        ("1C W=128\ndb_pkt_uf", "1C", "w128", "db_pkt_uf"),
        ("4C W=64\ndb_pkt_uf", "4C", "w64", "db_pkt_uf"),
        ("4C W=128\ndb_pkt_uf", "4C", "w128", "db_pkt_uf"),
        ("全量带 SFENCE\n(db_pkt_f / db_adp_f)", "ALL", "FENCED", "FENCED"),
    ]
    ax_x = np.arange(len(ae_items))
    ae_vals, ae_lo, ae_hi = [], [], []
    fenced_n = 0
    for _, ph, wl, arm in ae_items:
        if ph == "ALL":
            all_fenced_ae = []
            for (_, _, a), rs in by_cfg.items():
                if a in ("db_pkt_f", "db_pkt_f_c64", "db_adp_f", "db_adp_f_c64"):
                    for r in rs:
                        assert r["db_sfence"] is True
                        all_fenced_ae.append(sum(c["db_ahead_empty"] for c in r["card_pmu"]["cores"]))
            assert max(all_fenced_ae) == 0
            fenced_n = len(all_fenced_ae)
            ae_vals.append(0.0); ae_lo.append(0.0); ae_hi.append(0.0)
        else:
            m, lo, hi = med_err([sum(c["db_ahead_empty"] for c in r["card_pmu"]["cores"]) for r in by_cfg[(ph, wl, arm)]])
            ae_vals.append(m); ae_lo.append(lo); ae_hi.append(hi)

    bar_cols = ["#DC2626", "#DC2626", "#EA580C", "#EA580C", "#0F766E"]
    ax.bar(ax_x, ae_vals, width=0.52, color=bar_cols, edgecolor="#1E293B", linewidth=0.7,
           yerr=[ae_lo, ae_hi], capsize=3, alpha=0.88)
    for i, v in enumerate(ae_vals):
        lbl_txt = f"{int(round(v)):,}" if v > 0 else f"0（全 {fenced_n} 条）"
        ax.text(ax_x[i], v + ae_hi[i] + 280, lbl_txt, ha="center", va="bottom",
                fontsize=8.0, fontweight="bold", color="#0F766E" if v == 0 else "#991B1B")

    ret_bt_res = med_err([retry_per_msg(r) for r in by_cfg[("4C", "burst32_resid2", "db_pkt_f")]])[0]
    ret_bt_pau = med_err([retry_per_msg(r) for r in by_cfg[("4C", "burst32_pause_iso2", "db_pkt_f")]])[0]
    ret_w32 = med_err([retry_per_msg(r) for r in by_cfg[("4C", "w32", "db_pkt_f")]])[0]
    ret_w128 = med_err([retry_per_msg(r) for r in by_cfg[("4C", "w128", "db_pkt_f_c64")]])[0]
    ret_adp_max = max(
        retry_per_msg(r)
        for (ph, _, a), rs in by_cfg.items()
        if ph == "4C" and a in ("db_adp_f", "db_adp_f_c64")
        for r in rs
    )
    uf_ae_max = max(
        sum(c["db_ahead_empty"] for c in r["card_pmu"]["cores"])
        for r in by_cfg[("4C", "w128", "db_adp_uf")]
    )
    uf_mps = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "db_adp_uf")]])[0]
    box_txt = (
        "4C TAD 重试（retry_exlmn / 包）与 SFENCE 节拍\n"
        f"• 逐包门铃 burst-tail: {ret_bt_pau:.2f}–{ret_bt_res:.2f}\n"
        f"• 逐包门铃 W=32 / W=128(c64): {ret_w32:.2f} / {ret_w128:.3f}\n"
        f"• 批合并门铃 db_adp_f 全量最大: {ret_adp_max:.4f}\n"
        f"• 4C W=128 db_adp_uf: 早读={uf_ae_max}，但失 SFENCE 节拍跌至 {uf_mps:.2f} M/s"
    )
    ax.text(
        0.97, 0.94, box_txt, transform=ax.transAxes, ha="right", va="top", fontsize=7.6,
        bbox=dict(boxstyle="round,pad=0.36", facecolor="#F8FAFC", edgecolor="#64748B", lw=0.9),
    )

    ax.set_xticks(ax_x)
    ax.set_xticklabels([it[0] for it in ae_items], fontsize=8.1)
    ax.set_ylabel("门铃超车数据格次数 db_ahead_empty", fontsize=9.3)
    ax.set_ylim(0, 14800)
    ax.set_title("无 SFENCE 早到门铃实证与 4C TAD 门铃冲撞重试", fontsize=10.1, fontweight="bold")

    fig.subplots_adjust(top=0.915, hspace=0.34, wspace=0.17)
    png = save(fig, "mrv-177-closed-cliff")
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
