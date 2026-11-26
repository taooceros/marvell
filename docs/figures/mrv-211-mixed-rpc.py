"""MRV-211 Figure 3: Mixed latency-sensitive RPC + large bulk burst (W3, CEIO Fig. 10a / Table 4).

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
        if sp["suite"] == "w3":
            by_key[(sp["policy"], sp["tag"])].append(r)

    ratios = [
        ("w3_mixed_3to1", "轻突发\n(RPC 3 : 突发 1)"),
        ("w3_mixed_1to1", "均衡混跑\n(RPC 1 : 突发 1)"),
        ("w3_mixed_1to3", "重突发\n(RPC 1 : 突发 3)"),
    ]
    policies = [
        ("unified", "统一信用环（带优先级）", "#15803d"),
        ("unified_noprio", "统一信用环（无优先级）", "#0ea5e9"),
        ("ceio", "CEIO 双路径", "#dc2626"),
        ("large_sdp", "大环标准 SDP", "#64748b"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(15.2, 5.0))
    x = np.arange(len(ratios))
    width = 0.20
    offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]

    panels = [
        (axes[0], lambda r: float(r["client"]["rpc_p99_us"]), "RPC P99 尾延迟（µs，越低越好）", "(a) 小包 RPC P99 尾延迟", "{:.1f}"),
        (axes[1], lambda r: float(r["client"]["tx_gbps_wire"]), "混跑总吞吐（Gbps，越高越好）", "(b) 100GbE 实测总吞吐", "{:.1f}"),
        (axes[2], lambda r: float(r["host"]["coloc_kv"]["p99_ns"]), "邻居 KV 查表 P99（ns，越低越好）", "(c) 同机缓存邻居受扰", "{:.0f}"),
    ]

    for ax, fn, ylabel, title, fmt in panels:
        ymax = 0.0
        for (pol, label, color), off in zip(policies, offsets):
            vals, raw_pts = [], []
            for tag, _ in ratios:
                rs = by_key[(pol, tag)]
                assert len(rs) == 3, f"Expected n=3 for W3 {pol} {tag}, got {len(rs)}"
                pts = [fn(r) for r in rs]
                vals.append(med(pts))
                raw_pts.append(pts)
            ymax = max(ymax, max(max(p) for p in raw_pts))
            bars = ax.bar(x + off, vals, width=width * 0.92, color=color, alpha=0.90, label=label, zorder=3)
            for xi, pts, b in zip(x + off, raw_pts, bars):
                ax.scatter([xi] * len(pts), pts, color="#111827", s=12, zorder=4, alpha=0.72)
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    max(pts) + ymax * 0.02,
                    fmt.format(b.get_height()),
                    ha="center",
                    va="bottom",
                    fontsize=7.1 if fmt == "{:.1f}" else 7.8,
                    fontweight="bold" if pol in ("unified", "ceio") else "normal",
                    color=color if pol in ("unified", "ceio") else "#334155",
                )
        ax.set_xticks(x)
        ax.set_xticklabels([lbl for _, lbl in ratios], fontsize=9.2)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_ylim(0, ymax * 1.24)
        ax.set_title(title, fontsize=11, fontweight="bold", pad=8)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4, frameon=False, fontsize=9.6, bbox_to_anchor=(0.5, 0.925))
    fig.suptitle(
        "场景 3（256 B RPC + 1400 B 大突发混跑）：中重突发下全面领先，全程稳守同机缓存隔离（n = 3 中位数）",
        fontsize=11.8,
        fontweight="bold",
        y=0.975,
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.865])
    png = mrvplot.HERE / "mrv-211-mixed-rpc.png"
    svg = mrvplot.HERE / "mrv-211-mixed-rpc.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
