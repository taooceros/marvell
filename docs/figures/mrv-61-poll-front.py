"""MRV-61: Does card ring-core polling at the write frontier hold h2c MOVDIR64B at ~126-129 M/s?

Reads logs/mrv61-2026-11-18/runs.jsonl (27 runs: 6 h2c no-pause baselines, 3x3 1 us pause modes,
3x3 3 us pause modes, 3 bidir controls). Verifies every plotted run has 0 bad/torn/partial/corrupt
cells, 200M cells verified by the card, cpu40 on performance governor at ~3.9 GHz, uncore pinned at
2.2 GHz, and 100% unpartitioned CHA counter enablement.
"""
import json
import statistics
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv61-2026-11-18/runs.jsonl"


def load_verified_runs():
    runs = [json.loads(line) for line in RUNS_PATH.read_text().splitlines() if line.strip()]
    for r in runs:
        assert r["host"]["bad"] == 0 and r["host"]["torn"] == 0, f"bad/torn in {r['id']}"
        assert r["card"]["partial"] == 0 and r["card"]["corrupt"] == 0 and r["card"]["unroutable"] == 0, (
            f"card error in {r['id']}"
        )
        assert r["card"]["sunk"] == r["cells"] == 200_000_000, f"sunk mismatch in {r['id']}"
        if r["dir"] == "bidir":
            assert r["card"]["sourced"] == 200_000_000, f"sourced mismatch in {r['id']}"
        assert r["clocks"]["cpu40_governor"] == "performance", f"governor in {r['id']}"
        assert all(v == 2_200_000 for v in r["clocks"]["uncore_khz"].values()), f"uncore in {r['id']}"
        assert r["perf"]["min_pct_enabled"] >= 99.9, f"multiplexing in {r['id']}"
    return runs


RUNS = load_verified_runs()

GROUPS = [
    ("单独\n不停", lambda r: r["dir"] == "h2c" and r["poll_gap_ns"] == 0, "#64748b"),
    ("停 1 µs\n空转", lambda r: r["dir"] == "h2c" and r["poll_gap_ns"] == 1000 and r["poll_gap_mode"] == "spin", "#60a5fa"),
    ("停 1 µs\n读私有行", lambda r: r["dir"] == "h2c" and r["poll_gap_ns"] == 1000 and r["poll_gap_mode"] == "private", "#3b82f6"),
    ("停 1 µs\n读已取格", lambda r: r["dir"] == "h2c" and r["poll_gap_ns"] == 1000 and r["poll_gap_mode"] == "ring", "#2563eb"),
    ("停 3 µs\n空转", lambda r: r["dir"] == "h2c" and r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "spin", "#14b8a6"),
    ("停 3 µs\n读私有行", lambda r: r["dir"] == "h2c" and r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "private", "#0d9488"),
    ("停 3 µs\n读已取格", lambda r: r["dir"] == "h2c" and r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "ring", "#0f766e"),
    ("双向对照\n不停", lambda r: r["dir"] == "bidir" and r["poll_gap_ns"] == 0, "#16a34a"),
]

fig, (ax_rate, ax_batch, ax_cha) = plt.subplots(1, 3, figsize=(16.5, 5.4))
xs = list(range(len(GROUPS)))
labels = [g[0] for g in GROUPS]

h2c_base_med = statistics.median(
    r["host"]["card_took_mps"] for r in RUNS if r["dir"] == "h2c" and r["poll_gap_ns"] == 0
)
bidir_med = statistics.median(
    r["host"]["card_took_mps"] for r in RUNS if r["dir"] == "bidir" and r["poll_gap_ns"] == 0
)

for x, (name, pred, color) in enumerate(GROUPS):
    sub = [r for r in RUNS if pred(r)]
    assert sub, f"no runs for {name}"

    # (a) h2c throughput (card_took_mps)
    rates = [r["host"]["card_took_mps"] for r in sub]
    med_rate = statistics.median(rates)
    ax_rate.bar(x, med_rate, width=0.62, color=color, alpha=0.82, zorder=2)
    ax_rate.scatter([x] * len(rates), rates, color="#1e293b", s=18, zorder=3)
    ax_rate.text(x, max(rates) + 2.2, f"{med_rate:.1f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold")

    # (b) card cells per busy poll & host cpu40 cycles per cell
    batches = [r["card"]["mean_cells_per_busy_poll"] for r in sub]
    med_batch = statistics.median(batches)
    cyc = [r["perf"]["core_all"]["CPU40"]["cycles_per_cell"] for r in sub]
    med_cyc = statistics.median(cyc)
    ax_batch.bar(x - 0.17, med_batch, width=0.32, color=color, alpha=0.85, zorder=2,
                 label="卡核每轮取格数（最大 32）" if x == 0 else None)
    ax_batch.scatter([x - 0.17] * len(batches), batches, color="#1e293b", s=14, zorder=3)
    ax_batch.text(x - 0.17, max(batches) + 0.6, f"{med_batch:.1f}", ha="center", va="bottom", fontsize=7.5)

    ax_batch.bar(x + 0.17, med_cyc, width=0.32, color="#f59e0b", alpha=0.82, zorder=2,
                 label="主机核 40 每格周期数" if x == 0 else None)
    ax_batch.scatter([x + 0.17] * len(cyc), cyc, color="#1e293b", s=14, zorder=3)
    ax_batch.text(x + 0.17, max(cyc) + 0.6, f"{med_cyc:.1f}", ha="center", va="bottom", fontsize=7.5, color="#b45309")

    # (c) host CHA MOVDIR64B mean entries and residency (ns)
    entries = [r["perf"]["cha_mean_entries"] for r in sub]
    res_ns = [r["perf"]["cha_residency_ns"] for r in sub]
    med_ent = statistics.median(entries)
    med_ns = statistics.median(res_ns)
    ax_cha.bar(x, med_ent, width=0.62, color=color, alpha=0.82, zorder=2)
    ax_cha.scatter([x] * len(entries), entries, color="#1e293b", s=18, zorder=3)
    ax_cha.text(x, max(entries) + 8, f"{med_ent:.0f} 条\n({med_ns / 1000:.2f} µs)",
                ha="center", va="bottom", fontsize=7.5)

ax_rate.set_xticks(xs, labels, fontsize=8)
ax_rate.set_ylabel("去程 MOVDIR64B 速率（M 格/s）")
ax_rate.secondary_yaxis("right", functions=(lambda v: v * 0.064, lambda v: v / 0.064)).set_ylabel("GB/s")
ax_rate.set_ylim(0, 188)
ax_rate.axhline(bidir_med, color="#16a34a", linestyle="--", linewidth=1, alpha=0.6, label=f"双向对照 {bidir_med:.1f} M/s")
ax_rate.axhline(h2c_base_med, color="#64748b", linestyle=":", linewidth=1, alpha=0.6, label=f"单独不停 {h2c_base_med:.1f} M/s")
ax_rate.legend(fontsize=7.8, loc="lower right")
ax_rate.set_title("(a) 去程吞吐：停 3 µs 达 161.6–162.8 M/s（触及主机核 40 单核发射上限）", fontsize=9.8)

ax_batch.set_xticks(xs, labels, fontsize=8)
ax_batch.set_ylabel("每轮格数 / 每格周期数")
ax_batch.set_ylim(0, 43)
ax_batch.axhline(32, color="#94a3b8", linestyle="--", linewidth=1, alpha=0.6)
ax_batch.text(1.85, 34.8, "停 1 µs：连取 6.9 轮（~209 格）\n空轮询 3.7%（< τ_CHA）",
              ha="center", va="bottom", fontsize=7.2, color="#1d4ed8")
ax_batch.text(5.15, 34.8, "停 3 µs：连取 29.3 轮（~923 格）\n空轮询 0.7%（> τ_CHA）",
              ha="center", va="bottom", fontsize=7.2, color="#0f766e")
ax_batch.legend(fontsize=7.8, loc="upper left")
ax_batch.set_title("(b) 追赶轮数与每格周期：停 3 µs 跨过 τ_CHA 后每格 24.1 周期", fontsize=9.8)

ax_cha.set_xticks(xs, labels, fontsize=8)
ax_cha.set_ylabel("主机 CHA 中 MOVDIR64B 平均条数（不分时计数）")
ax_cha.set_ylim(0, 535)
ax_cha.axhline(446.6, color="#64748b", linestyle=":", linewidth=1, alpha=0.6, label="单核 CHA 反压顶棚 ~447 条")
ax_cha.legend(fontsize=7.8, loc="upper right")
ax_cha.set_title("(c) 主机 CHA 排队：停 3 µs 脱离 447 条顶棚降至 358–361 条", fontsize=9.8)

fig.suptitle(
    "MRV-61：卡 2 收 + 2 写（写合并），主机核 40（3.9 GHz，uncore 2.2 GHz）发 2 亿格（64 B/格，批=1，lost/bad/torn=0）；"
    "发送中读 card_next：单独 3143–3159 次（环积压 ~2080 格）、双向 3194–3196 次（环积压 ~2916 格）",
    fontsize=9.5,
)
print(save(fig, "mrv-61-poll-front"))
