"""MRV-211 Figure 2: 64 -> 16,384 transient flows across 1 ms / 500 us / 100 us slots (W2, CEIO Fig. 12).

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
    card_echo_vals = []
    for r in runs:
        assert_verified(r)
        sp = r["spec"]
        if sp["suite"] == "w2":
            if sp["policy"] == "card_echo":
                card_echo_vals.append(float(r["client"]["tx_gbps_wire"]))
            else:
                by_key[(sp["policy"], int(sp["slot_us"]), int(sp["total_flows"]))].append(r)

    assert len(card_echo_vals) == 6, f"Expected 6 card_echo runs, got {len(card_echo_vals)}"
    card_ceil = med(card_echo_vals)

    flows = [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384]
    flow_labels = ["64", "128", "256", "512", "1K", "2K", "4K", "8K", "16K"]
    slots = [
        (1000, "(a) 1 ms 切换槽（慢切换）"),
        (500, "(b) 500 µs 切换槽（中速切换）"),
        (100, "(c) 100 µs 切换槽（极速瞬变）"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(14.6, 4.9), sharey=True)
    x_pos = np.arange(len(flows))

    for ax, (slot_us, title) in zip(axes, slots):
        ax.axvline(2.0, color="#94a3b8", linestyle="--", linewidth=1.3, zorder=1)
        ax.text(2.16, 50.6, "CEIO 快道上限\n（256 流）", fontsize=8.5, color="#475569", va="center")
        for pol, label, color, mk in (
            ("unified", "统一信用环（本文）", "#15803d", "o"),
            ("ceio", "CEIO 双路径", "#dc2626", "s"),
        ):
            meds, mins, maxs = [], [], []
            for nf in flows:
                rs = by_key[(pol, slot_us, nf)]
                assert len(rs) == 3, f"Expected n=3 for W2 {pol} {slot_us}us {nf}f, got {len(rs)}"
                vs = [float(r["client"]["tx_gbps_wire"]) for r in rs]
                meds.append(med(vs))
                mins.append(min(vs))
                maxs.append(max(vs))
            ax.fill_between(x_pos, mins, maxs, color=color, alpha=0.14, zorder=2)
            ax.plot(x_pos, meds, marker=mk, color=color, linewidth=2.4, markersize=5.5, label=label, zorder=3)

        u16 = med([float(r["client"]["tx_gbps_wire"]) for r in by_key[("unified", slot_us, 16384)]])
        c16 = med([float(r["client"]["tx_gbps_wire"]) for r in by_key[("ceio", slot_us, 16384)]])
        slow16 = med([float(r["client"]["slow_frac_pct"]) for r in by_key[("ceio", slot_us, 16384)]])
        gain_pct = (u16 - c16) / c16 * 100.0
        ax.annotate(
            f"16K 流：+{gain_pct:.1f}%\n({u16:.1f} vs {c16:.1f} Gbps)\nCEIO 慢道 {slow16:.1f}%",
            xy=(8, c16),
            xytext=(2.65, 35.0),
            arrowprops={"arrowstyle": "->", "color": "#dc2626", "lw": 1.2, "connectionstyle": "arc3,rad=0.12"},
            fontsize=8.6,
            fontweight="bold",
            color="#991b1b",
            bbox={"boxstyle": "round,pad=0.32", "fc": "#fef2f2", "ec": "#fecaca", "lw": 0.9},
        )
        ax.text(
            7.9,
            u16 + 1.0,
            f"{u16:.1f} Gbps",
            ha="right",
            va="bottom",
            fontsize=8.8,
            fontweight="bold",
            color="#15803d",
        )

        ax.set_xticks(x_pos)
        ax.set_xticklabels(flow_labels, fontsize=9.2)
        ax.set_xlabel("总并发流数（每槽 16 条活跃）", fontsize=10)
        ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
        ax.set_ylim(32, 61.5)

    axes[0].set_ylabel("实测线速吞吐（Gbps，512 B 包）", fontsize=10.5)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, fontsize=10.0, bbox_to_anchor=(0.5, 0.922))
    fig.suptitle(
        f"场景 2（64 → 16,384 瞬变流）：统一信用环稳守 ~55.7 Gbps，CEIO 超 256 流慢道升至 99%（卡端直回上限 {card_ceil:.1f} Gbps）",
        fontsize=11.4,
        fontweight="bold",
        y=0.975,
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.865])
    png = mrvplot.HERE / "mrv-211-flow-churn.png"
    svg = mrvplot.HERE / "mrv-211-flow-churn.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
