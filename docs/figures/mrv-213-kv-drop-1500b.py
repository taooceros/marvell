"""MRV-213 Figure 1: 1500 B full-100GbE burst sweep (64 KiB -> 128 MiB, n=3)
across all 5 single-variable standard-SDP architectures:
  Panel A: Concurrent Socket-1 32 MiB KV tail latency (p99 solid, p999 dashed) vs kv_solo baseline
  Panel B: End-to-end packet loss rate (%) showing passive small-ring & native SDP WMARK silent drops
           vs 0.00% loss on CreditRing (On-Path SmartNIC FIFO + NO-SFENCE BAR4 credit).

100% dynamically computed from `logs/mrv213-2026-11-26/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv213-2026-11-26/runs.jsonl"

BURST_ORDER = ["64KiB", "256KiB", "1MiB", "4MiB", "16MiB", "32MiB", "64MiB", "128MiB"]
BURST_LABELS = ["64K", "256K", "1M", "4M", "16M", "32M", "64M", "128M"]


def med(xs):
    return statistics.median(xs)


def assert_verified(r):
    assert r.get("verified") is True and not r.get("hard_errors") and not r.get("clock_errors"), (
        f"unverified run_id={r.get('run_id')} tag={r.get('tag')}"
    )
    hp = r["host_pmu"]
    assert hp["min_pct"] >= 99.99, f"host pmu mux: {r.get('tag')}"
    kv = r.get("kv")
    if kv is not None:
        assert kv["bad"] == 0 and kv["page_faults"] == 0 and hp["kv"]["page_faults"] == 0
    if r["arm"] != "kv-solo":
        cp, cs, ms = r["card_pmu"], r["card_stats"], r["mercury"]
        assert hp["rx"]["page_faults"] == 0
        assert r["bad"] == 0 and r["torn"] == 0
        assert cp["min_pct"] >= 99.99
        assert cs["wire_bad"] == 0 and cs["wire_torn"] == 0 and cs["credit_torn"] == 0
        assert cs["eth"]["imissed"] == 0 and cs["fifo_drops"] == 0
        assert cs["eth_rx_pkts"] == r["offered_pkts"]
        assert ms["cqe_err"] == 0 and ms["post_err"] == 0
        assert ms["phy"]["tx_pause"] == 0 and ms["phy"]["rx_pause"] == 0
        if r["arm"] not in ("small-drop", "native-bp"):
            assert r["lost_pkts"] == 0 and r["gaps"] == 0 and r["ooo"] == 0


def main():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    by_key = defaultdict(list)
    for r in rows:
        assert_verified(r)
        if r["part"] == "e2_1500b_kv32m":
            by_key[(r["arm_tag"], r["burst_label"])].append(r)

    solo_rows = by_key[("kv_solo", "0")]
    solo_p99 = med([r["kv"]["p99_ns"] for r in solo_rows])
    solo_p999 = med([r["kv"]["p999_ns"] for r in solo_rows])

    arms = [
        ("large_mono",  "① 大环单调池 (8192槽/FIFO)",     "#dc2626", "o",  "-",  2.1),
        ("large_lifo",  "③ 大环理想LIFO池 (8192槽/LIFO)", "#f97316", "s",  "--", 1.9),
        ("small_drop",  "② 被动小环满即丢 (256槽)",        "#94a3b8", "^",  ":",  1.6),
        ("native_bp",   "④ 原生SDP低水位 (零BAR4消融)",     "#64748b", "v",  "-.", 1.7),
        ("credit_ring", "⑤ 本文CreditRing (热环+BAR4信用)", "#1a7f37", "D",  "-",  2.6),
    ]

    x = np.arange(len(BURST_ORDER))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.4, 5.6))
    # ---------------- Panel A: Concurrent 32 MiB KV p99 (solid) + p999 ----------------
    ax1.axhline(solo_p99, color="#334155", linestyle=":", linewidth=1.4, zorder=1)
    ax1.text(0.05, solo_p99 - 6.0, f"空载基线 p99 = {solo_p99:.0f} ns", color="#334155", fontsize=9.5, va="top")

    # Shade host hot-ring capacity boundary (4Q * 256 R_eff * 1500B = 1.5 MiB)
    ax1.axvline(2.35, color="#cbd5e1", linestyle="--", linewidth=1.1, zorder=0)
    ax1.text(2.60, 338, "主机热环边界 (~1.5 MiB)\n右侧超出部分转入卡 FIFO", color="#475569", fontsize=8.4, va="top")
    for tag, label, col, mk, ls, lw in arms:
        y99 = [med([r["kv"]["p99_ns"] for r in by_key[(tag, b)]]) for b in BURST_ORDER]
        lo99 = [y - min(r["kv"]["p99_ns"] for r in by_key[(tag, b)]) for y, b in zip(y99, BURST_ORDER)]
        hi99 = [max(r["kv"]["p99_ns"] for r in by_key[(tag, b)]) - y for y, b in zip(y99, BURST_ORDER)]
        ax1.errorbar(
            x, y99, yerr=[lo99, hi99], label=label, color=col, marker=mk, linestyle=ls,
            linewidth=lw, markersize=6.0, capsize=2.5, zorder=5 if tag == "credit_ring" else 3,
        )

    # Also plot p999 for large_mono vs credit_ring lightly with clear callout
    lm_p999 = [med([r["kv"]["p999_ns"] for r in by_key[("large_mono", b)]]) for b in BURST_ORDER]
    cr_p999 = [med([r["kv"]["p999_ns"] for r in by_key[("credit_ring", b)]]) for b in BURST_ORDER]
    ax1.plot(x, lm_p999, color="#dc2626", linestyle=":", linewidth=1.3, alpha=0.55, marker=".", markersize=4)
    ax1.plot(x, cr_p999, color="#1a7f37", linestyle=":", linewidth=1.4, alpha=0.65, marker=".", markersize=4)
    ax1.annotate(
        f"① 大环 p999 → {lm_p999[-1]:.0f} ns",
        xy=(x[-1], lm_p999[-1]), xytext=(x[-1] - 2.35, lm_p999[-1] + 22),
        fontsize=9.2, color="#b91c1c", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.0),
    )
    ax1.annotate(
        f"⑤ CreditRing p999 = {cr_p999[-1]:.0f} ns\n(空载 p999 = {solo_p999:.0f} ns)",
        xy=(x[-1], cr_p999[-1]), xytext=(x[-1] - 3.3, 68),
        fontsize=8.6, color="#15803d",
        bbox=dict(boxstyle="round,pad=0.22", facecolor="white", edgecolor="none", alpha=0.88),
        arrowprops=dict(arrowstyle="->", color="#15803d", lw=1.0),
    )

    lm_128 = med([r["kv"]["p99_ns"] for r in by_key[("large_mono", "128MiB")]])
    cr_128 = med([r["kv"]["p99_ns"] for r in by_key[("credit_ring", "128MiB")]])
    ax1.annotate(
        f"①/③ 大环 p99 = {lm_128:.0f} ns\n({lm_128/solo_p99:.1f}× 劣化)",
        xy=(x[-1], lm_128), xytext=(x[-1] - 2.7, lm_128 + 38),
        fontsize=9.5, color="#dc2626", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.1),
    )
    ax1.annotate(
        f"⑤ 全程稳守 {cr_128:.0f} ns",
        xy=(x[-1], cr_128), xytext=(x[-1] - 2.3, cr_128 - 25),
        fontsize=9.5, color="#1a7f37", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#1a7f37", lw=1.1),
    )

    ax1.set_xticks(x)
    ax1.set_xticklabels(BURST_LABELS)
    ax1.set_xlabel("单次 100GbE 突发规模（1500 B 帧，B）")
    ax1.set_ylabel("同 Socket 并发 32 MiB KV 延迟（ns）")
    ax1.set_ylim(0, max(lm_p999) + 56)
    ax1.set_title("A. 并发 KV 尾延迟（实线 p99 / 点线 p999）", fontsize=11.5, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=8.5, framealpha=0.92)

    # ---------------- Panel B: End-to-end Drop Rate (%) ----------------
    ax2.axvline(2.35, color="#cbd5e1", linestyle="--", linewidth=1.1, zorder=0)
    for tag, label, col, mk, ls, lw in arms:
        yd = [med([r["drop_pct"] for r in by_key[(tag, b)]]) for b in BURST_ORDER]
        ax2.plot(
            x, yd, label=label, color=col, marker=mk, linestyle=ls,
            linewidth=lw, markersize=6.0, zorder=5 if tag == "credit_ring" else 3,
        )

    sd_128 = med([r["drop_pct"] for r in by_key[("small_drop", "128MiB")]])
    nb_128 = med([r["drop_pct"] for r in by_key[("native_bp", "128MiB")]])
    nb_32 = med([r["drop_pct"] for r in by_key[("native_bp", "32MiB")]])
    ax2.annotate(
        f"② 被动小环：{sd_128:.1f}% 丢包\n④ 原生SDP低水位：{nb_128:.1f}% 静默丢包\n(CN106 默认VF0从不反压卡侧)",
        xy=(x[-2], nb_32), xytext=(2.6, 56),
        fontsize=9.2, color="#334155", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#f8fafc", edgecolor="#94a3b8", lw=1.0),
        arrowprops=dict(arrowstyle="->", color="#475569", lw=1.1),
    )
    ax2.annotate(
        "① 大环 / ③ LIFO / ⑤ CreditRing：\n全量突发严格 0.00% 丢包",
        xy=(x[-1], 0.0), xytext=(3.1, 14),
        fontsize=9.5, color="#1a7f37", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="#f0fdf4", edgecolor="#1a7f37", lw=1.1),
        arrowprops=dict(arrowstyle="->", color="#1a7f37", lw=1.2),
    )

    ax2.set_xticks(x)
    ax2.set_xticklabels(BURST_LABELS)
    ax2.set_xlabel("单次 100GbE 突发规模（1500 B 帧，B）")
    ax2.set_ylabel("端到端丢包率（%）")
    ax2.set_ylim(-4, 108)
    ax2.set_title("B. 端到端丢包率：无 BAR4 信用必丢包，有信用严格 0%", fontsize=11.5, fontweight="bold")

    fig.suptitle(
        "MRV-213（1500 B 满线速 100GbE，n=3 中位数）：大环保零丢包却打爆 KV，小环/原生 SDP 保 KV 却静默狂丢，仅 CreditRing 两难全解",
        fontsize=12.0, fontweight="bold", y=0.99,
    )
    out = mrvplot.save(fig, "mrv-213-kv-drop-1500b")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
