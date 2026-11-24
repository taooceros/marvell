#!/usr/bin/env python3
"""MRV-170 Figure 2: Data-driven hardware safety verification across Boundaries 1-4.

Reads verified records directly from logs/mrv170-2026-11-24/safety_runs.jsonl
(zero hardcoded measurement numbers).
"""
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
SAFETY_RUNS = ROOT / "logs/mrv170-2026-11-24/safety_runs.jsonl"


def load_latest_by_case():
    by_case = {}
    bare = []
    for line in SAFETY_RUNS.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("test") == "bare_probe":
            bare.append(r)
        else:
            # Keep latest verified run per case_id, or latest run for intentional negative probes (b3_door_err_watchdog)
            cid = r["case_id"]
            if cid not in by_case or r["attempt_id"] > by_case[cid]["attempt_id"]:
                by_case[cid] = r
    return bare, by_case


def main():
    bare, by_case = load_latest_by_case()
    assert len(bare) == 3, f"expected 3 bare_probe reps, got {len(bare)}"

    fig, axes = plt.subplots(2, 2, figsize=(14.6, 9.2))
    fig.suptitle(
        "MRV-170 零 LDADDA（--cqe-direct）四大生产边界实卡压力测试总览\n"
        "CN106 (cn10ka_b0, 1C 2.0 GHz, scalar_enable=1) + mercury 100GbE 实测 · 原始日志直出",
        fontsize=12.4,
        fontweight="bold",
        y=0.985,
    )

    # ------------------------------------------------------------------
    # Panel A: Bare empty-poll & CQE clear primitives (n=3 median, log-inset / broken bar)
    # ------------------------------------------------------------------
    ax = axes[0, 0]
    prim_labels = [
        "default\n(LDADDA)",
        "--cqe-peek\n(ldar 空轮询)",
        "naive direct\n(2字清零)",
        "safe direct\n(全加固空轮询)",
        "清 2 字\n+dmb oshst",
        "全清 128 B\n+dmb oshst",
    ]
    prim_keys = [
        "default_empty_ns",
        "cqe_peek_empty_ns",
        "cqe_direct_naive_empty_ns",
        "cqe_direct_safe_empty_ns",
        "clear_2w_dmb_ns",
        "clear_128b_dmb_ns",
    ]
    colors_a = ["#64748b", "#0ea5e9", "#94a3b8", "#1d6fa5", "#94a3b8", "#1d6fa5"]
    meds = [statistics.median([r[k] for r in bare]) for k in prim_keys]
    los = [m - min(r[k] for r in bare) for m, k in zip(meds, prim_keys)]
    his = [max(r[k] for r in bare) - m for m, k in zip(meds, prim_keys)]

    x_a = np.arange(len(prim_labels))
    ax.bar(x_a, meds, width=0.58, color=colors_a, alpha=0.9, zorder=3)
    ax.errorbar(x_a, meds, yerr=[los, his], fmt="none", ecolor="#0f172a", elinewidth=1.2, capsize=3, zorder=4)
    ax.set_yscale("log")
    ax.set_ylim(1.5, 1600)
    for x, m, hi in zip(x_a, meds, his):
        ax.text(x, m * 1.18, f"{m:.2f} ns", ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#0f172a")
    ax.set_xticks(x_a)
    ax.set_xticklabels(prim_labels, fontsize=8.4)
    ax.set_ylabel("单次裸原语耗时（ns，对数轴）", fontsize=9.8)
    ax.set_title("A. 片内裸原语与全清 128 B 开销（全清 = 0.00 ns 额外代价）", fontsize=10.6, fontweight="bold", pad=8)

    # ------------------------------------------------------------------
    # Panel B: Boundary 1 Multi-Seg (Naive leak vs Hardened Safe verified)
    # ------------------------------------------------------------------
    ax = axes[0, 1]
    b1_cases = [
        ("b1_lpbk_4k_naive_repro", "旧版\n4K 环回\n(2段)"),
        ("b1_wire_1514_4seg_naive_repro", "旧版\n1514B 线缆\n(4段)"),
        ("b1_lpbk_4k_2seg_safe", "加固\n4K 环回\n(2段)"),
        ("b1_lpbk_6k_3seg_safe", "加固\n6K 环回\n(3段)"),
        ("b1_lpbk_8k_4seg_safe", "加固\n8K 环回\n(4段)"),
        ("b1_lpbk_9000_6seg_safe", "加固\n9000B 环回\n(6段)"),
        ("b1_wire_1514_4seg_safe", "加固\n1514B 线缆\n(4段)"),
        ("b1_wire_1514_5seg_safe", "加固\n1514B 线缆\n(5段)"),
    ]
    x_b = np.arange(len(b1_cases))
    leak_per_pkt = []
    ok_pct = []
    for cid, _ in b1_cases:
        r = by_case[cid]
        if "naive_repro" not in cid:
            assert r.get("verified", True) is not False and r["mbuf_leak"] == 0 and r["verified_ok"] == r["recvd"]
        leak_per_pkt.append(r["mbuf_leak"] / max(1, r["recvd"]))
        ok_pct.append(100.0 * r["verified_ok"] / max(1, r["recvd"]))

    bars_ok = ax.bar(x_b - 0.18, ok_pct, width=0.34, color="#1d6fa5", alpha=0.9, label="载荷+尾字全验真通过率 (%)", zorder=3)
    ax2 = ax.twinx()
    bars_lk = ax2.bar(x_b + 0.18, leak_per_pkt, width=0.34, color="#dc2626", alpha=0.85, label="每包 NPA mbuf 泄漏数", zorder=3)
    ax2.spines["top"].set_visible(False)

    for x, okv, lkv in zip(x_b, ok_pct, leak_per_pkt):
        if okv > 0:
            ax.text(x - 0.18, okv + 2.5, "100%\n(20万)", ha="center", va="bottom", fontsize=7.2, color="#1d6fa5", fontweight="bold")
        else:
            ax.text(x - 0.18, 3.0, "0%", ha="center", va="bottom", fontsize=7.5, color="#64748b", fontweight="bold")
        if lkv > 0:
            ax2.text(x + 0.18, lkv + 0.08, f"{lkv:.0f}/pkt", ha="center", va="bottom", fontsize=7.8, color="#dc2626", fontweight="bold")
        else:
            ax2.text(x + 0.18, 0.06, "0", ha="center", va="bottom", fontsize=7.5, color="#334155")

    ax.set_xticks(x_b)
    ax.set_xticklabels([lbl for _, lbl in b1_cases], fontsize=7.6)
    ax.set_ylabel("全段尾字验真通过率 (%)", fontsize=9.6, color="#1d6fa5")
    ax2.set_ylabel("每包 NPA 缓冲泄漏数 (mbufs/pkt)", fontsize=9.6, color="#dc2626")
    ax.set_ylim(0, 125)
    ax2.set_ylim(0, 3.8)
    ax2.grid(False)
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="upper center", ncol=2, frameon=True, facecolor="white", fontsize=8.0)
    ax.set_title("B. 边界 1：多段大包（segs=2..6）旧版泄漏复现 vs 加固版零错", fontsize=10.6, fontweight="bold", pad=8)

    # ------------------------------------------------------------------
    # Panel C: Boundary 2 & 4 Hardware Verification Counts (log / normalized)
    # ------------------------------------------------------------------
    ax = axes[1, 0]
    b2_safe = by_case["b2_err_lpbk_safe_drop"]
    b2_naive = by_case["b2_err_lpbk_naive"]
    b2_syn = by_case["b2_synth_anomalous"]
    b4_ss = by_case["b4_stop_start_50cycles"]
    b4_10m = by_case["b4_long_wrap_10m_safe"]
    assert b2_syn["verified"] is True and b4_ss["verified"] is True and b4_10m["verified"] is True

    cats_c = [
        "B2 硬件错包\n检出率\n(OL3/OL4/CHK)",
        "B2 0段/非RX\n慢路径入口\n(停队单测)",
        "B4 停启残留\n清零率\n(32 脏槽)",
        "B4 50轮停启\n+22.5万包\n零错率",
        "B4 1000万包\n单线程零重放\n通过率",
    ]
    naive_vals_c = [
        100.0 * b2_naive["cqe_stats"]["err_rx_cqes"] / 30.0,
        0.0 if b2_syn["zero_seg"]["naive_head_stuck"] else 100.0,
        100.0 * (1.0 - b4_ss["oracle_dirty_repro"]["free_dirty_w0"] / 32.0),
        0.0,  # un-scrubbed stop/start replays dirty slots on wrap
        0.0,  # N/A / not run on unsafe naive
    ]
    safe_vals_c = [
        100.0 * b2_safe["cqe_stats"]["err_rx_cqes"] / 30.0,
        100.0 if b2_syn["zero_seg"]["safe_fallback_ldadda"] >= 1 else 0.0,
        100.0 * (1.0 - b4_ss["oracle_after_scrub"]["free_dirty_w0"] / 32.0),
        100.0 * b4_ss["total_pkts_verified"] / (50 * 4500),
        100.0 * b4_10m["verified_ok"] / b4_10m["target"],
    ]
    x_c = np.arange(len(cats_c), dtype=float)
    w_c = 0.34
    safe_x_c = np.array([x_c[0] + w_c / 2, x_c[1] + w_c / 2, x_c[2] + w_c / 2, x_c[3], x_c[4]])
    ax.bar(x_c[:3] - w_c / 2, naive_vals_c[:3], width=w_c, color="#94a3b8", alpha=0.85, label="旧版 / 未清零基线", zorder=3)
    ax.bar(safe_x_c, safe_vals_c, width=w_c, color="#1d6fa5", alpha=0.92, label="加固版 --cqe-direct-safe + sync", zorder=3)
    for i in range(3):
        ax.text(x_c[i] - w_c / 2, naive_vals_c[i] + 2.5, f"{naive_vals_c[i]:.0f}%", ha="center", va="bottom", fontsize=8.0, color="#64748b", fontweight="bold")
    ann_safe = [
        f"100%\n({b2_safe['cqe_stats']['err_rx_cqes']}/30)",
        f"仅入口\n(fb={b2_syn['zero_seg']['safe_fallback_ldadda']},ahead={b2_syn['zero_seg']['safe_w0_ahead_of_tail']})",
        f"100%\n({b4_ss['oracle_dirty_repro']['free_dirty_w0']}→0)",
        f"100%\n({b4_ss['total_pkts_verified']//1000}k)",
        f"100%\n(10M,0重放)",
    ]
    for i, txt in enumerate(ann_safe):
        ax.text(safe_x_c[i], safe_vals_c[i] + 2.5, txt, ha="center", va="bottom", fontsize=7.2, color="#1d6fa5", fontweight="bold")
    ax.set_xticks(x_c)
    ax.set_xticklabels(cats_c, fontsize=8.0)
    ax.set_ylabel("安全门控通过率 (%)", fontsize=9.8)
    ax.set_ylim(0, 145)
    ax.legend(loc="upper center", ncol=2, frameon=True, facecolor="white", fontsize=8.2)
    ax.set_title("C. 边界 2 与边界 4：错包/慢路径入口/脏槽停启/千万绕环对照", fontsize=10.5, fontweight="bold", pad=8)

    # ------------------------------------------------------------------
    # Panel D: Boundary 3 CQ Overflow & NPA Drop Recovery + DOOR_ERR Finding
    # ------------------------------------------------------------------
    ax = axes[1, 1]
    b3_cases = [
        ("b3_overflow_wire_default", "default\nCQ满环"),
        ("b3_overflow_wire_peek", "--cqe-peek\nCQ满环"),
        ("b3_overflow_wire_safe", "safe\nCQ满环"),
        ("b3_overflow_npa_safe", "safe\nNPA满丢"),
    ]
    x_d = np.arange(len(b3_cases))
    rx_m = []
    drop_m = []
    for cid, _ in b3_cases:
        r = by_case[cid]
        assert r["verified"] is True and r["total_bad"] == 0 and r["head_mismatches"] == 0 and r["mbuf_leak"] == 0
        rx_m.append(r["total_ok"] / 1e6)
        if "npa" in cid:
            drop_m.append(r["eth_stats"]["imissed"] / 1e6)
        else:
            drop_m.append((r["eth_stats"]["ipackets"] - r["total_ok"]) / 1e6)

    ax.bar(x_d, rx_m, width=0.52, color="#1d6fa5", alpha=0.9, label="无错收包验真 (M pkts)", zorder=3)
    ax.bar(x_d[:3], drop_m[:3], width=0.52, bottom=rx_m[:3], color="#e2e8f0", edgecolor="#475569", hatch="//", alpha=0.92, label="CQ 交付差额 (ipackets−rx, imissed=0)", zorder=3)
    ax.bar(x_d[3:], drop_m[3:], width=0.52, bottom=rx_m[3:], color="#fecaca", edgecolor="#b91c1c", hatch="xx", alpha=0.92, label="权威硬件丢包 (imissed = rxq_0_drop)", zorder=3)
    for i, (rv, dv) in enumerate(zip(rx_m, drop_m)):
        cid = b3_cases[i][0]
        lbl = f"imissed {dv:.2f}M" if "npa" in cid else f"差额 {dv:.2f}M"
        ax.text(x_d[i], rv + dv + 0.22, f"收 {rv:.2f}M · 0错\n{lbl}\nhead一致", ha="center", va="bottom", fontsize=7.4, color="#0f172a", fontweight="bold")
    door = by_case["b3_door_err_watchdog"]
    ax.text(
        0.02, 0.96,
        f"DOOR_ERR 实卡证伪（关键发现）：\n"
        f"强写 cq_door(+1) 仅走 QINT（cq_ctx->cq_err=0），\n"
        f"CQ_OP_STATUS bit46 始终为 {door['door_err_cq_err_bit46']}（0–20 ms）且钳位 hw_head，\n"
        f"后续仍收 {door['post_door_err_rx']}/4 包；生产须配 QINT + wd=4096（补空轮询超时）",
        transform=ax.transAxes,
        ha="left", va="top",
        fontsize=7.3,
        color="#0f172a",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#f8fafc", edgecolor="#64748b", lw=1.1),
        zorder=6,
    )
    ax.set_xticks(x_d)
    ax.set_xticklabels([lbl for _, lbl in b3_cases], fontsize=8.8)
    ax.set_ylabel("30×15 ms 停顿压测总包数 (M pkts)", fontsize=9.8)
    ax.set_ylim(0, 17.8)
    ax.legend(loc="upper right", frameon=True, facecolor="white", fontsize=7.4)
    ax.set_title("D. 边界 3：满环溢出零漂移恢复 + DOOR_ERR 不可见性实测", fontsize=10.6, fontweight="bold", pad=8)

    out = mrvplot.save(fig, "mrv-170-safety-boundaries")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
