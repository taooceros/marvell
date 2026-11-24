#!/usr/bin/env python3
"""MRV-177 Figure 3: Closed-loop W=32/64/128 throughput cliff breakthrough,
card TAD snoop/msg, early-doorbell ordering proof, and 4C TAD retries.

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


def retry_per_msg(r):
    cp = r["card_pmu"]
    tt = sum(c["taken"] for c in cp["cores"])
    return cp["tad"]["tad_rsp_msh_out_retry_exlmn"]["val"] / max(1, tt)


def main():
    by_cfg = load_verified()

    fig, axes = plt.subplots(2, 2, figsize=(13.8, 9.0))
    fig.suptitle(
        "MRV-177 闭环高并发（W=32/64/128）破 13.1 M/s 掉档与硬件 PMU 机理验证（n = 3 中位数）",
        fontsize=13,
        fontweight="bold",
        y=0.98,
    )

    arm_styles = {
        "base": ("Baseline（紧盯 next）", "#475569", "o", "-"),
        "bq_probe": ("Arm A bq_probe", "#D97706", "s", "-"),
        "bq_wait": ("Arm A 变体 bq_wait", "#7C3AED", "^", "--"),
        "db_pkt_f": ("Arm B1 逐包门铃", "#DC2626", "v", "-"),
        "db_adp_f": ("Arm B2 批合并门铃（无整形）", "#0F766E", "D", "-"),
    }

    ws = [("w32", 32), ("w64", 64), ("w128", 128)]
    x_pos = np.arange(len(ws))

    for col, phase in enumerate(["1C", "4C"]):
        ax = axes[0, col]
        ax.axhline(13.1, color="#94A3B8", linestyle="--", linewidth=1.2, zorder=1)
        ax.text(-0.18, 10.6 if phase == "4C" else 10.7, "13.1 M/s 掉档线", fontsize=7.9, color="#64748B", ha="left")

        for arm in ["base", "bq_probe", "bq_wait", "db_pkt_f", "db_adp_f"]:
            lbl, color, mk, ls = arm_styles[arm]
            if arm == "bq_probe":
                lbl = "Arm A bq_probe (fast_dt=0)" if phase == "1C" else "Arm A bq_probe (fast_dt=480ns)"
            ys, los, his = [], [], []
            for wname, _ in ws:
                m, lo, hi = med_err([r["achieved_mps"] for r in by_cfg[(phase, wname, arm)]])
                ys.append(m); los.append(lo); his.append(hi)
            ax.errorbar(
                x_pos, ys, yerr=[los, his], label=lbl, color=color, marker=mk,
                linestyle=ls, linewidth=2.2 if arm == "db_adp_f" else 1.7,
                markersize=7 if arm == "db_adp_f" else 6, capsize=3, zorder=4 if arm == "db_adp_f" else 3,
            )

        if phase == "4C":
            m64, lo64, hi64 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w64", "db_adp_f")]])
            m128, lo128, hi128 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "db_adp_f_c64")]])
            ax.errorbar(
                [1, 2], [m64, m128], yerr=[[lo64, lo128], [hi64, hi128]],
                label="Arm B2 批合并（C_line=64）", color="#0284C7", marker="P",
                linestyle=":", linewidth=2.0, markersize=7.5, capsize=3, zorder=5,
            )
            ax.annotate(
                f"C_line=64 稳住 {m128:.2f} M/s\n（W=64: {m64:.2f} M/s）",
                xy=(2, m128), xytext=(1.15, 41.2),
                fontsize=8.2, fontweight="bold", color="#0284C7",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#F0F9FF", edgecolor="#0284C7", lw=0.9),
                arrowprops=dict(arrowstyle="->", color="#0284C7", lw=1.0),
            )
            m_base128 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "base")]])[0]
            m_bqp128 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "bq_probe")]])[0]
            m_dbp128 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "db_pkt_f")]])[0]
            ax.text(2.06, m_base128 + 1.0, f"Base {m_base128:.2f}", fontsize=8.0, color="#475569", fontweight="bold")
            ax.text(2.06, m_bqp128 - 2.4, f"bq_probe {m_bqp128:.2f}", fontsize=8.0, color="#B45309")
            ax.text(2.06, m_dbp128 - 0.5, f"逐包 {m_dbp128:.2f}", fontsize=8.0, color="#B91C1C", fontweight="bold")
            m_adp128 = med_err([r["achieved_mps"] for r in by_cfg[("4C", "w128", "db_adp_f")]])[0]
            ax.text(2.06, m_adp128 - 0.6, f"无整形 {m_adp128:.2f}", fontsize=8.0, color="#0F766E")
        else:
            m_adp128 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "db_adp_f")]])[0]
            m_bqp64 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w64", "bq_probe")]])[0]
            m_base64 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w64", "base")]])[0]
            m_base128 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "base")]])[0]
            m_bqw128 = med_err([r["achieved_mps"] for r in by_cfg[("1C", "w128", "bq_wait")]])[0]
            ax.annotate(
                f"{m_adp128:.2f} M/s\n(Baseline 的 {m_adp128/m_base128:.2f}×)",
                xy=(2, m_adp128), xytext=(1.05, 48.0),
                fontsize=8.5, fontweight="bold", color="#0F766E",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#F0FDFA", edgecolor="#0F766E", lw=0.9),
                arrowprops=dict(arrowstyle="->", color="#0F766E", lw=1.0),
            )
            ax.annotate(
                f"嗅探地雷：{m_base64:.1f} → {m_bqp64:.2f}",
                xy=(1, m_bqp64), xytext=(0.12, 2.8),
                fontsize=8.0, fontweight="bold", color="#B45309",
                bbox=dict(boxstyle="round,pad=0.22", facecolor="#FFFBEB", edgecolor="#D97706", lw=0.8),
                arrowprops=dict(arrowstyle="->", color="#D97706", lw=1.0),
            )
            ax.text(2.06, m_bqw128 - 0.8, f"bq_wait {m_bqw128:.1f}", fontsize=8.0, color="#6D28D9")
            ax.text(2.06, m_base128 + 0.6, f"Base {m_base128:.2f}", fontsize=8.0, color="#475569", fontweight="bold")

        ax.set_xticks(x_pos)
        ax.set_xticklabels([f"W={v}" for _, v in ws], fontsize=9.5)
        ax.set_xlim(-0.22, 2.55)
        ax.set_ylim(0, 56 if phase == "1C" else 48)
        ax.set_ylabel("闭环吞吐 (Mmsg/s)", fontsize=9.5)
        ax.set_title(f"{phase} 闭环吞吐 vs 并发度 W（W≤64 等价 C_line=64）", fontsize=10.5, fontweight="bold")
        ax.legend(loc="upper left", fontsize=7.8, frameon=True)

    # Bottom-left: Card TAD snoop/msg at W=64 and W=128 (1C & 4C)
    ax = axes[1, 0]
    groups = [
        ("1C W=64", "1C", "w64", False),
        ("1C W=128", "1C", "w128", False),
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
    bw = 0.16
    for idx, (arm, lbl, color) in enumerate(bar_arms):
        ys, los, his = [], [], []
        for _, ph, wl, use_c64 in groups:
            akey = f"{arm}_c64" if use_c64 else arm
            m, lo, hi = med_err([snoop_per_msg(r) for r in by_cfg[(ph, wl, akey)]])
            ys.append(m); los.append(lo); his.append(hi)
        offs = gx + (idx - 2) * bw
        ax.bar(
            offs, ys, width=bw, color=color, edgecolor="#1E293B", linewidth=0.6,
            yerr=[los, his], capsize=2, label=lbl, alpha=0.92,
        )
        for j, v in enumerate(ys):
            if v >= 2.0 or arm == "db_adp_f" or (j == 1 and arm == "bq_wait"):
                dx = -0.04 if (j >= 2 and arm == "base") else (0.04 if (j >= 2 and arm == "bq_probe") else 0.0)
                dy = 0.48 if (j >= 2 and arm == "bq_probe") else 0.14
                ax.text(offs[j] + dx, v + his[j] + dy, f"{v:.2f}", ha="center", va="bottom", fontsize=7.2,
                        fontweight="bold" if arm in ("db_adp_f", "db_pkt_f") else "normal", color="#0F172A")

    ax.set_xticks(gx)
    ax.set_xticklabels([g[0] for g in groups], fontsize=8.8)
    ax.set_ylabel("卡侧 TAD snoop / 包", fontsize=9.5)
    ax.set_ylim(0, 10.6)
    ax.set_title("卡侧硬件 PMU：TAD 跨核/缓存嗅探次数（覆盖 warmup+measured）", fontsize=10.3, fontweight="bold")
    ax.legend(loc="upper left", fontsize=7.8, frameon=True, ncol=2)

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
                fontsize=8.2, fontweight="bold", color="#0F766E" if v == 0 else "#991B1B")

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
    box_txt = (
        "4C TAD 重试（retry_exlmn / 包）\n"
        f"• 逐包门铃 burst-tail: {ret_bt_pau:.2f}–{ret_bt_res:.2f}\n"
        f"• 逐包门铃 W=32 / W=128(c64): {ret_w32:.2f} / {ret_w128:.3f}\n"
        f"• 批合并门铃 db_adp_f 全量最大: {ret_adp_max:.4f}"
    )
    ax.text(
        0.97, 0.94, box_txt, transform=ax.transAxes, ha="right", va="top", fontsize=8.0,
        bbox=dict(boxstyle="round,pad=0.38", facecolor="#F8FAFC", edgecolor="#64748B", lw=0.9),
    )

    ax.set_xticks(ax_x)
    ax.set_xticklabels([it[0] for it in ae_items], fontsize=8.2)
    ax.set_ylabel("门铃超车数据格次数 db_ahead_empty", fontsize=9.3)
    ax.set_ylim(0, 14500)
    ax.set_title("无 SFENCE 早到门铃实证与 4C TAD 门铃冲撞重试", fontsize=10.3, fontweight="bold")

    fig.subplots_adjust(top=0.91, hspace=0.33, wspace=0.18)
    png = save(fig, "mrv-177-closed-cliff")
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
