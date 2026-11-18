"""MRV-76: Does piercing L1D/L2 with cold L3/TAD reads during a 3 us pause slow down h2c MOVDIR64B,
or is the slowdown specifically caused by snoop contention inside the ~3 us in-flight write band?

Reads `logs/mrv76-2026-11-18/runs.jsonl` (33 runs), verifies data integrity and 100% PMU enablement
on both host and card, and plots a 2x2 figure:
  (a) h2c MOVDIR64B throughput (M cells/s & GB/s) and host cpu40 cycles/cell across all 10 groups;
  (b) Card-side L3 reads (`ll_cache_rd`) and TAD mesh requests (`tad_req_msh_in_any`);
  (c) Card-side demand cache refills (`l1d_cache_refill` and `l2d_cache_refill`);
  (d) Card-side TAD snoop invalidations (`tad_snp_msh_out_any`) and host CHA occupancy (`cha_mean_entries` / residency).
"""
import json
import statistics
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv76-2026-11-18/runs.jsonl"


def load_verified_runs():
    runs = [json.loads(line) for line in RUNS_PATH.read_text().splitlines() if line.strip()]
    assert len(runs) == 33, f"expected 33 runs, got {len(runs)}"
    for r in runs:
        assert r["host"]["bad"] == 0 and r["host"]["torn"] == 0 and r["host"]["late"] == 0, r["id"]
        assert r["card"]["partial"] == 0 and r["card"]["corrupt"] == 0 and r["card"]["unroutable"] == 0, r["id"]
        assert r["card"]["sunk"] == r["cells"], r["id"]
        if r["dir"] == "bidir":
            assert r["card"]["sourced"] == r["cells"], r["id"]
        assert r["perf"]["min_pct_enabled"] >= 99.9, r["id"]
        assert r["card"]["pmu"]["min_pct_enabled"] >= 99.9, r["id"]
    return runs


def main():
    runs = load_verified_runs()

    groups = [
        ("单独不停\n0 ns", lambda r: r["dir"] == "h2c" and r["poll_gap_ns"] == 0, "#dc2626"),
        ("停 3 µs\n① spin\n(纯空转)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "spin", "#0f766e"),
        ("停 3 µs\n②a cold\n(64B步进)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "cold-private", "#0284c7"),
        ("停 3 µs\n②b cold-4k\n(4160B跨页)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "cold-private-4k", "#0369a1"),
        ("停 3 µs\n③a far-ring\n(64B步进)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "far-ring", "#2563eb"),
        ("停 3 µs\n③b far-4k\n(4160B跨页)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "far-ring-4k", "#1d4ed8"),
        ("停 3 µs\n④a near-ring\n(-384..-64)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "near-ring", "#4f46e5"),
        ("停 3 µs\n④b next-1\n(已落盘邻格)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "next-1", "#6366f1"),
        ("停 3 µs\n④c write-band\n(next..+384)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "write-band", "#b91c1c"),
        ("双向对照\n0 ns", lambda r: r["dir"] == "bidir" and r["poll_gap_ns"] == 0, "#16a34a"),
    ]

    fig, ((ax_rate, ax_l3), (ax_ref, ax_snp)) = plt.subplots(2, 2, figsize=(16.8, 9.8))
    xs = list(range(len(groups)))
    labels = [g[0] for g in groups]

    h2c_base_med = statistics.median(
        r["host"]["card_took_mps"] for r in runs if r["dir"] == "h2c" and r["poll_gap_ns"] == 0
    )
    bidir_med = statistics.median(
        r["host"]["card_took_mps"] for r in runs if r["dir"] == "bidir" and r["poll_gap_ns"] == 0
    )

    for x, (_, pred, color) in enumerate(groups):
        sub = [r for r in runs if pred(r)]
        assert sub, f"no runs for group {x}"

        # Panel (a): h2c throughput (M cells/s)
        rates = [r["host"]["card_took_mps"] for r in sub]
        med_r = statistics.median(rates)
        cycs = [r["perf"]["core_all"]["CPU40"]["cycles_per_cell"] for r in sub]
        med_cyc = statistics.median(cycs)
        ax_rate.bar(x, med_r, width=0.62, color=color, alpha=0.86)
        for idx_i, y in enumerate(rates):
            ax_rate.scatter(x + (idx_i - (len(rates) - 1) / 2) * 0.07, y, color="#111827", s=15, zorder=4)
        ax_rate.text(
            x,
            max(rates) + 2.2,
            f"{med_r:.1f} M/s\n({med_cyc:.1f} cyc)",
            ha="center",
            va="bottom",
            fontsize=7.3,
        )

        # Panel (b): Card TAD mesh requests & Core L3 reads
        ll_rds = [
            (
                r["card"]["pmu"]["cores"][0]["events"]["ll_cache_rd"]["val"]
                + r["card"]["pmu"]["cores"][1]["events"]["ll_cache_rd"]["val"]
            )
            / 1e6
            for r in sub
        ]
        tad_reqs = [r["card"]["pmu"]["tad"]["tad_req_msh_in_any"]["val"] / 1e6 for r in sub]
        med_ll = statistics.median(ll_rds)
        med_req = statistics.median(tad_reqs)

        w = 0.36
        ax_l3.bar(
            x - w / 2,
            med_ll,
            width=w,
            color="#1e293b",
            alpha=0.88,
            label="卡双核 L3 读 (ll_cache_rd)" if x == 0 else None,
        )
        ax_l3.bar(
            x + w / 2,
            med_req,
            width=w,
            color="#94a3b8",
            alpha=0.88,
            label="卡 TAD 总请求 (tad_req_msh_in_any)" if x == 0 else None,
        )
        for y in ll_rds:
            ax_l3.scatter(x - w / 2, y, color="#0f172a", s=11, zorder=4)
        for y in tad_reqs:
            ax_l3.scatter(x + w / 2, y, color="#0f172a", s=11, zorder=4)
        ax_l3.text(x - w / 2, max(ll_rds) + 16, f"{med_ll:.0f}M", ha="center", va="bottom", fontsize=6.9, color="#0f172a")
        ax_l3.text(x + w / 2, max(tad_reqs) + 16, f"{med_req:.0f}M", ha="center", va="bottom", fontsize=6.9, color="#334155")

        # Panel (c): Card demand L1D and L2D cache refills
        l1_refs = [
            (
                r["card"]["pmu"]["cores"][0]["events"]["l1d_cache_refill"]["val"]
                + r["card"]["pmu"]["cores"][1]["events"]["l1d_cache_refill"]["val"]
            )
            / 1e6
            for r in sub
        ]
        l2_refs = [
            (
                r["card"]["pmu"]["cores"][0]["events"]["l2d_cache_refill"]["val"]
                + r["card"]["pmu"]["cores"][1]["events"]["l2d_cache_refill"]["val"]
            )
            / 1e6
            for r in sub
        ]
        med_l1 = statistics.median(l1_refs)
        med_l2 = statistics.median(l2_refs)
        ax_ref.bar(
            x - w / 2,
            med_l1,
            width=w,
            color="#0284c7",
            alpha=0.86,
            label="卡双核 L1D 需求重填 (l1d_cache_refill)" if x == 0 else None,
        )
        ax_ref.bar(
            x + w / 2,
            med_l2,
            width=w,
            color="#dc2626",
            alpha=0.86,
            label="卡双核 L2D 需求重填 (l2d_cache_refill)" if x == 0 else None,
        )
        for y in l1_refs:
            ax_ref.scatter(x - w / 2, y, color="#0f172a", s=11, zorder=4)
        for y in l2_refs:
            ax_ref.scatter(x + w / 2, y, color="#0f172a", s=11, zorder=4)
        ax_ref.text(x - w / 2, max(l1_refs) + 1.5, f"{med_l1:.1f}M", ha="center", va="bottom", fontsize=6.8, color="#0369a1")
        ax_ref.text(x + w / 2, max(l2_refs) + 1.5, f"{med_l2:.1f}M", ha="center", va="bottom", fontsize=6.8, color="#b91c1c")

        # Panel (d): Card TAD snoop invalidations (M) and Host CHA occupancy (entries)
        tad_snps = [r["card"]["pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / 1e6 for r in sub]
        chas = [r["perf"]["cha_mean_entries"] for r in sub]
        taus = [r["perf"]["cha_residency_ns"] for r in sub]
        med_snp = statistics.median(tad_snps)
        med_cha = statistics.median(chas)
        med_tau = statistics.median(taus)
        ax_snp.bar(x, med_snp, width=0.62, color=color, alpha=0.86)
        for idx_i, y in enumerate(tad_snps):
            ax_snp.scatter(x + (idx_i - (len(tad_snps) - 1) / 2) * 0.07, y, color="#111827", s=15, zorder=4)
        ax_snp.text(
            x,
            max(tad_snps) + 2.2,
            f"{med_snp:.1f}M\nCHA {med_cha:.0f}\n({med_tau/1000:.2f}µs)",
            ha="center",
            va="bottom",
            fontsize=6.9,
        )

    ax_rate.set_xticks(xs, labels, fontsize=7.6)
    ax_rate.set_ylabel("去程 MOVDIR64B 速率（M 格/s）")
    ax_rate.secondary_yaxis("right", functions=(lambda v: v * 0.064, lambda v: v / 0.064)).set_ylabel("GB/s")
    ax_rate.set_ylim(0, 190)
    ax_rate.axhline(bidir_med, color="#16a34a", linestyle="--", linewidth=1, alpha=0.6, label=f"双向对照 {bidir_med:.1f} M/s")
    ax_rate.axhline(h2c_base_med, color="#dc2626", linestyle=":", linewidth=1, alpha=0.6, label=f"单独不停 {h2c_base_med:.1f} M/s")
    ax_rate.legend(fontsize=8.0, loc="lower right")
    ax_rate.set_title("(a) 去程吞吐：环外/远端冷读保持 160.4–162.6 M/s，唯扫入在途写带跌至 113.4 M/s", fontsize=10.0)

    ax_l3.set_xticks(xs, labels, fontsize=7.6)
    ax_l3.set_ylabel("每次运行（2 亿格）卡侧事件数（百万次）")
    ax_l3.set_ylim(0, 1480)
    ax_l3.legend(fontsize=8.0, loc="upper left")
    ax_l3.set_title("(b) 卡侧 L3 读与 TAD 总请求：冷读组高达 978–1280M（+221–522M）仍不降速", fontsize=10.0)

    ax_ref.set_xticks(xs, labels, fontsize=7.6)
    ax_ref.set_ylabel("每次运行（2 亿格）卡双核缓存需求重填（百万次）")
    ax_ref.set_ylim(0, 128)
    ax_ref.legend(fontsize=8.0, loc="upper left")
    ax_ref.set_title("(c) 卡双核 L1D/L2D 需求重填：4160B 跨页冷读达 101.3M / 21.4M 仍保持 161.4–162.6 M/s", fontsize=10.0)

    ax_snp.set_xticks(xs, labels, fontsize=7.6)
    ax_snp.set_ylabel("卡侧 TAD 嗅探发出次数 tad_snp_msh_out_any（百万次）")
    ax_snp.set_ylim(0, 136)
    ax_snp.axhline(67.9, color="#0f766e", linestyle="--", linewidth=1, alpha=0.5, label="spin 绕环背景嗅探 67.9M")
    ax_snp.legend(fontsize=8.0, loc="upper left")
    ax_snp.set_title("(d) 卡侧 TAD 嗅探失效与主机 CHA 排队：在途写带嗅探激增至 111.9M、CHA 堵至 458 条", fontsize=10.0)

    fig.suptitle(
        "MRV-76：停顿 3 µs 跨行冷读穿透 L1D/L2 与 ~3 µs 在途写带嗅探对比（64 B，批=1，每次 2 亿格，核 40 3.9 GHz，uncore 2.2 GHz）",
        fontsize=11.5,
        y=0.995,
    )
    print(save(fig, "mrv-76-cold-read"))


if __name__ == "__main__":
    main()
