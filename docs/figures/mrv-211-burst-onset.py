"""MRV-211 Figure 1: Microsecond-scale burst onset (W1) — tail latency & co-located cache tenant isolation.

100% dynamically computed from `logs/mrv211-2026-11-26/runs.jsonl.gz` (n=3 verified runs per point).
"""
import gzip
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv211-2026-11-26/runs.jsonl.gz"


def load_jsonl(path: Path):
    if not path.exists() and path.suffix == ".gz":
        path = path.with_suffix("")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def med(xs):
    return statistics.median(xs)


def assert_verified(r):
    assert r.get("accepted") is True and not r.get("hard_errors") and not r.get("clock_errors")
    c, k = r["client"], r["card"]
    for fld in ("lost", "bad", "torn", "ooo", "send_err"):
        assert int(c[fld]) == 0, f"client.{fld} != 0"
    for fld in ("bad_wire", "bad_sdp", "eth_tx_drops", "sdp_tx_drops", "card_buf_drops"):
        assert int(k[fld]) == 0, f"card.{fld} != 0"
    assert float(r["card_pmu_delta"]["min_running_pct"]) >= 99.95
    h = r["host"]
    if h is not None:
        for fld in ("bad", "torn", "ooo", "tx_drops"):
            assert int(h[fld]) == 0, f"host.{fld} != 0"
        assert int(h["sdp_port_stats"]["rx_nombuf"]) == 0
        assert int(h["pmu"]["page_faults"]) == 0
        assert float(h["pmu"]["min_running_pct"]) >= 99.95


def main():
    runs = load_jsonl(RUNS_PATH)
    by_key = defaultdict(list)
    for r in runs:
        assert_verified(r)
        sp = r["spec"]
        if sp["suite"] == "w1":
            by_key[(sp["policy"], int(sp["pkt_bytes"]))].append(r)

    sizes = [512, 1024, 1400]
    policies = [
        ("unified", "统一信用环（本文）", "#15803d"),
        ("ceio", "CEIO 双路径", "#dc2626"),
        ("ceio_zero_rmt", "CEIO 零改表理想版", "#f59e0b"),
        ("large_sdp", "大环标准 SDP", "#64748b"),
    ]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.2))
    x = np.arange(len(sizes))
    width = 0.19
    offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]

    # Panel (a): Burst-onset P99 latency (us)
    max_p99 = 0.0
    for (pol, label, color), off in zip(policies, offsets):
        vals = []
        raw_pts = []
        for sz in sizes:
            rs = by_key[(pol, sz)]
            assert len(rs) == 3, f"Expected n=3 for W1 {pol} {sz}B, got {len(rs)}"
            pts = [float(r["client"]["p99_us"]) for r in rs]
            vals.append(med(pts))
            raw_pts.append(pts)
        max_p99 = max(max_p99, max(max(p) for p in raw_pts))
        bars = ax1.bar(x + off, vals, width=width * 0.92, color=color, alpha=0.90, label=label, zorder=3)
        for xi, pts, b in zip(x + off, raw_pts, bars):
            ax1.scatter([xi] * len(pts), pts, color="#111827", s=14, zorder=4, alpha=0.75)
            ax1.text(
                b.get_x() + b.get_width() / 2,
                max(pts) + max_p99 * 0.02,
                f"{b.get_height():.0f}",
                ha="center",
                va="bottom",
                fontsize=8.2,
                fontweight="bold" if pol in ("unified", "ceio") else "normal",
                color=color if pol in ("unified", "ceio") else "#334155",
            )

    ax1.set_xticks(x)
    ax1.set_xticklabels([f"{s} B" for s in sizes], fontsize=10.5)
    ax1.set_xlabel("突发数据包大小", fontsize=10.5)
    ax1.set_ylabel("端到端 P99 延迟（µs，越低越好）", fontsize=10.5)
    ax1.set_ylim(0, max_p99 * 1.24)
    ax1.set_title("(a) 微秒级突发骤起下的 P99 尾延迟", fontsize=11.5, fontweight="bold", pad=10)

    # Panel (b): Co-located 32 MiB KV tenant P99 latency (ns) + DDIO hit rate note
    max_kv = 0.0
    for (pol, label, color), off in zip(policies, offsets):
        vals = []
        raw_pts = []
        ddios = []
        for sz in sizes:
            rs = by_key[(pol, sz)]
            pts = [float(r["host"]["coloc_kv"]["p99_ns"]) for r in rs]
            vals.append(med(pts))
            raw_pts.append(pts)
            ddios.append(med([float(r["host"]["pmu"]["ddio_hit_pct"]) for r in rs]))
        max_kv = max(max_kv, max(max(p) for p in raw_pts))
        bars = ax2.bar(x + off, vals, width=width * 0.92, color=color, alpha=0.90, label=label, zorder=3)
        for xi, pts, b, dd in zip(x + off, raw_pts, bars, ddios):
            ax2.scatter([xi] * len(pts), pts, color="#111827", s=14, zorder=4, alpha=0.75)
            ax2.text(
                b.get_x() + b.get_width() / 2,
                max(pts) + max_kv * 0.02,
                f"{b.get_height():.0f}",
                ha="center",
                va="bottom",
                fontsize=8.2,
                fontweight="bold" if pol in ("unified", "ceio") else "normal",
                color=color if pol in ("unified", "ceio") else "#334155",
            )

    ax2.set_xticks(x)
    ax2.set_xticklabels([f"{s} B" for s in sizes], fontsize=10.5)
    ax2.set_xlabel("突发数据包大小", fontsize=10.5)
    ax2.set_ylabel("同机缓存邻居 P99 延迟（ns，越低越好）", fontsize=10.5)
    ax2.set_ylim(0, max_kv * 1.24)
    ax2.set_title("(b) 同 Socket 共置 KV 查表租户受扰程度", fontsize=11.5, fontweight="bold", pad=10)

    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4, frameon=False, fontsize=9.8, bbox_to_anchor=(0.5, 0.925))
    fig.suptitle(
        "场景 1（微秒级突发骤起）：统一信用环 P99 尾延迟更低，且全程守住同机缓存邻居（n = 3 中位数，黑点为单次实测）",
        fontsize=11.6,
        fontweight="bold",
        y=0.975,
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.865])
    png = mrvplot.HERE / "mrv-211-burst-onset.png"
    svg = mrvplot.HERE / "mrv-211-burst-onset.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
