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

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14.0, 6.0))
    x = np.arange(len(sizes))
    width = 0.19
    offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]

    # Panel (a): Burst-onset full-run P99 latency (us)
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
                max(pts) + max_p99 * 0.022,
                f"{b.get_height():.0f}",
                ha="center",
                va="bottom",
                fontsize=8.3,
                fontweight="bold" if pol in ("unified", "ceio") else "normal",
                color=color if pol in ("unified", "ceio") else "#334155",
            )

    ax1.set_xticks(x)
    ax1.set_xticklabels([f"{s} B" for s in sizes], fontsize=10.5)
    ax1.set_xlabel("突发数据包大小", fontsize=10.5)
    ax1.set_ylabel("端到端全段 P99 延迟（µs，越低越好）", fontsize=10.5)
    ax1.set_ylim(0, max_p99 * 1.24)
    ax1.set_title("(a) 微秒级突发骤起下的全段 P99 尾延迟", fontsize=11.4, fontweight="bold", pad=10)

    # Panel (b): Co-located 32 MiB KV tenant P99 latency (ns) + DDIO vs LLC read-capacity distinction
    max_kv = 0.0
    ddio_by_pol = defaultdict(list)
    for (pol, label, color), off in zip(policies, offsets):
        vals = []
        raw_pts = []
        for sz in sizes:
            rs = by_key[(pol, sz)]
            pts = [float(r["host"]["coloc_kv"]["p99_ns"]) for r in rs]
            vals.append(med(pts))
            raw_pts.append(pts)
            ddio_by_pol[pol].append(med([float(r["host"]["pmu"]["ddio_hit_pct"]) for r in rs]))
        max_kv = max(max_kv, max(max(p) for p in raw_pts))
        bars = ax2.bar(x + off, vals, width=width * 0.92, color=color, alpha=0.90, label=label, zorder=3)
        for xi, pts, b in zip(x + off, raw_pts, bars):
            ax2.scatter([xi] * len(pts), pts, color="#111827", s=14, zorder=4, alpha=0.75)
            ax2.text(
                b.get_x() + b.get_width() / 2,
                max(pts) + max_kv * 0.025,
                f"{b.get_height():.0f}",
                ha="center",
                va="bottom",
                fontsize=8.3,
                fontweight="bold" if pol in ("unified", "ceio") else "normal",
                color=color if pol in ("unified", "ceio") else "#334155",
            )

    lsdp_ddio_min = min(ddio_by_pol["large_sdp"])
    lsdp_ddio_max = max(ddio_by_pol["large_sdp"])
    ceio_ddios = ddio_by_pol["ceio"] + ddio_by_pol["ceio_zero_rmt"]
    ceio_ddio_min = min(ceio_ddios)
    ceio_ddio_max = max(ceio_ddios)
    u_ddio_min = min(ddio_by_pol["unified"])

    # Clean annotations in upper empty space separating DDIO thrashing vs LLC read contention
    ax2.annotate(
        f"仅大环 SDP 冲穿 DDIO\n实测 DDIO {lsdp_ddio_min:.1f}%–{lsdp_ddio_max:.1f}%",
        xy=(0 + 1.5 * width, 276),
        xytext=(-0.38, 342),
        arrowprops={"arrowstyle": "->", "color": "#475569", "lw": 1.2},
        fontsize=8.3,
        fontweight="bold",
        color="#334155",
        bbox={"boxstyle": "round,pad=0.32", "fc": "#f8fafc", "ec": "#cbd5e1", "lw": 0.9},
        zorder=5,
    )
    ax2.annotate(
        f"CEIO 仍守住 DDIO {ceio_ddio_min:.1f}%–{ceio_ddio_max:.1f}%\n抬高源于 2048 项快环池 (~9–13.5 MiB)\n争用 60 MiB 共享 LLC 读容量",
        xy=(2 - 0.5 * width, 193),
        xytext=(0.76, 330),
        arrowprops={"arrowstyle": "->", "color": "#dc2626", "lw": 1.2, "connectionstyle": "arc3,rad=-0.10"},
        fontsize=8.1,
        fontweight="bold",
        color="#991b1b",
        bbox={"boxstyle": "round,pad=0.32", "fc": "#fef2f2", "ec": "#fecaca", "lw": 0.9},
        zorder=5,
    )
    ax2.text(
        -0.40,
        184,
        f"统一环 DDIO\n≥ {u_ddio_min:.2f}%",
        fontsize=7.6,
        fontweight="bold",
        color="#15803d",
        bbox={"boxstyle": "round,pad=0.22", "fc": "#f0fdf4", "ec": "#bbf7d0", "lw": 0.8},
        zorder=5,
    )

    ax2.set_xticks(x)
    ax2.set_xticklabels([f"{s} B" for s in sizes], fontsize=10.5)
    ax2.set_xlabel("突发数据包大小", fontsize=10.5)
    ax2.set_ylabel("同机缓存邻居 P99 延迟（ns，越低越好）", fontsize=10.5)
    ax2.set_ylim(0, max_kv * 1.52)
    ax2.set_title("(b) 同 Socket 共置 KV 查表租户受扰与缓存机制区分", fontsize=11.4, fontweight="bold", pad=10)

    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4, frameon=False, fontsize=9.8, bbox_to_anchor=(0.5, 0.885))
    fig.suptitle(
        "场景 1（微秒级突发骤起）：统一信用环 P99 尾延迟更低，全程守住同机缓存邻居",
        fontsize=12.0,
        fontweight="bold",
        y=0.975,
    )
    fig.text(
        0.5,
        0.922,
        "固定条件：1 主机核 cpu40 @ 3.90 GHz（Uncore 2.20 GHz）+ 4 卡核（CEIO 另占第 5 核）｜W = 1,024，发送批 ≤ 16，DPDK burst = 32｜每 25 ms 注入 400 µs 停顿，共置 32 MiB KV @ cpu41，n = 3 中位数（黑点为单次实测）",
        ha="center",
        va="center",
        fontsize=8.6,
        color="#475569",
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.83])
    png = mrvplot.HERE / "mrv-211-burst-onset.png"
    svg = mrvplot.HERE / "mrv-211-burst-onset.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
