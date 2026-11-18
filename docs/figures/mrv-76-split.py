"""MRV-76 Occam's Razor single-variable split plots (MRV-82 style).

Reads `logs/mrv76-2026-11-18/runs.jsonl` and generates three single-variable comparison charts:
  1. `mrv-76-split-a-pause.png`     — Variable A: Pause vs No Pause (`0 ns` vs `3 us spin`)
  2. `mrv-76-split-b-bandwidth.png` — Variable B: L3/TAD Cold Read Load (away from write band: `spin` vs `far-ring` vs `cold-private`)
  3. `mrv-76-split-c-position.png`  — Variable C: Read Position relative to Frontier `next` (`far-ring` -> `near-ring` -> `next-1` -> `write-band`)
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
        assert r["perf"]["min_pct_enabled"] >= 99.9, r["id"]
        assert r["card"]["pmu"]["min_pct_enabled"] >= 99.9, r["id"]
    return runs


def plot_var_a(runs):
    groups = [
        ("不停顿 (0 ns)\n死盯队头 next", lambda r: r["dir"] == "h2c" and r["poll_gap_ns"] == 0, "#dc2626"),
        ("停顿 3 µs (spin)\n原地空转不读内存", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "spin", "#0f766e"),
    ]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.2))
    xs = [0, 1]
    labels = [g[0] for g in groups]

    for x, (_, pred, color) in enumerate(groups):
        sub = [r for r in runs if pred(r)]
        rates = [r["host"]["card_took_mps"] for r in sub]
        taus = [r["perf"]["cha_residency_ns"] / 1000.0 for r in sub]
        chas = [r["perf"]["cha_mean_entries"] for r in sub]
        med_r = statistics.median(rates)
        med_tau = statistics.median(taus)
        med_cha = statistics.median(chas)

        ax1.bar(x, med_r, width=0.46, color=color, alpha=0.86)
        for idx_i, y in enumerate(rates):
            ax1.scatter(x + (idx_i - (len(rates) - 1) / 2) * 0.04, y, color="#111827", s=18, zorder=4)
        ax1.text(x, max(rates) + 3.0, f"{med_r:.1f} M/s\n({med_r*0.064:.2f} GB/s)", ha="center", va="bottom", fontsize=9.0, fontweight="bold")

        ax2.bar(x, med_cha, width=0.46, color=color, alpha=0.86)
        for idx_i, y in enumerate(chas):
            ax2.scatter(x + (idx_i - (len(chas) - 1) / 2) * 0.04, y, color="#111827", s=18, zorder=4)
        ax2.text(x, max(chas) + 10, f"{med_cha:.0f} 条在途\n(停留 {med_tau:.2f} µs)", ha="center", va="bottom", fontsize=9.0, fontweight="bold")

    ax1.set_xticks(xs, labels, fontsize=9.0)
    ax1.set_ylabel("去程 MOVDIR64B 吞吐（M 格/s）")
    ax1.set_ylim(0, 192)
    ax1.set_title("(a) 去程吞吐：停 3 µs 从 128.3 跳到 160.4 M/s", fontsize=10.2)

    ax2.set_xticks(xs, labels, fontsize=9.0)
    ax2.set_ylabel("主机 32 个 CHA 平均在途条数")
    ax2.set_ylim(0, 520)
    ax2.axhline(438.3, color="#dc2626", linestyle=":", linewidth=1, alpha=0.6, label="单核在途反压顶棚 ~440 条")
    ax2.legend(fontsize=8.5, loc="lower right")
    ax2.set_title("(b) 主机在途写排队：停 3 µs 脱离反压顶棚", fontsize=10.2)

    fig.suptitle("变量 1（停不停）：取不满 32 格时停 3 µs 即可解除前沿反压（64 B，每次 2 亿格）", fontsize=10.8, y=0.98)
    print(save(fig, "mrv-76-split-a-pause"))


def plot_var_b(runs):
    groups = [
        ("① spin (纯空转)\n停顿期 0 次内存读", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "spin", "#0f766e"),
        ("② cold-private (64B步进)\n扫读环外 4 MiB 独立冷大页", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "cold-private", "#0284c7"),
        ("③ far-ring (64B步进)\n扫读环内半圈外已消费冷区", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "far-ring", "#2563eb"),
    ]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.4))
    xs = list(range(len(groups)))
    labels = [g[0] for g in groups]

    for x, (_, pred, color) in enumerate(groups):
        sub = [r for r in runs if pred(r)]
        req_rates = [
            r["card"]["pmu"]["tad"]["tad_req_msh_in_any"]["val"]
            / (r["card"]["pmu"]["tad"]["tad_req_msh_in_any"]["enabled_ns"] / 1e9)
            / 1e6
            for r in sub
        ]
        req_tots = [r["card"]["pmu"]["tad"]["tad_req_msh_in_any"]["val"] / 1e6 for r in sub]
        rates = [r["host"]["card_took_mps"] for r in sub]
        med_req_r = statistics.median(req_rates)
        med_req_t = statistics.median(req_tots)
        med_r = statistics.median(rates)

        ax1.bar(x, med_req_r, width=0.52, color=color, alpha=0.86)
        for idx_i, y in enumerate(req_rates):
            ax1.scatter(x + (idx_i - (len(req_rates) - 1) / 2) * 0.05, y, color="#111827", s=16, zorder=4)
        ax1.text(x, max(req_rates) + 22, f"{med_req_r:.0f} M/s\n(总计 {med_req_t:.0f}M)", ha="center", va="bottom", fontsize=8.2, fontweight="bold")

        ax2.bar(x, med_r, width=0.52, color=color, alpha=0.86)
        for idx_i, y in enumerate(rates):
            ax2.scatter(x + (idx_i - (len(rates) - 1) / 2) * 0.05, y, color="#111827", s=16, zorder=4)
        ax2.text(x, max(rates) + 2.5, f"{med_r:.1f} M/s", ha="center", va="bottom", fontsize=8.8, fontweight="bold")

    ax1.set_xticks(xs, labels, fontsize=8.5)
    ax1.set_ylabel("卡侧 TAD 网格请求速率（M 次/s）")
    ax1.set_ylim(0, 1020)
    ax1.set_title("(a) 卡侧 L3/TAD 请求压力：冷读组推高至 790–815 M/s", fontsize=10.0)

    ax2.set_xticks(xs, labels, fontsize=8.5)
    ax2.set_ylabel("去程 MOVDIR64B 吞吐（M 格/s）")
    ax2.set_ylim(0, 192)
    ax2.axhline(160.44, color="#0f766e", linestyle="--", linewidth=1, alpha=0.6, label="spin 空转基线 160.4 M/s")
    ax2.axhline(128.32, color="#dc2626", linestyle=":", linewidth=1, alpha=0.6, label="不停顿 0 ns 128.3 M/s")
    ax2.legend(fontsize=8.2, loc="lower right")
    ax2.set_title("(b) 去程吞吐：加 2.2–2.6 亿次 TAD 请求，去程仍稳居 160.4–162.0 M/s", fontsize=9.8)

    fig.suptitle("变量 2（停顿时读不读无关冷内存）：远离在途写带时，猛读 L3/TAD 丝毫不降速", fontsize=10.8, y=0.98)
    print(save(fig, "mrv-76-split-b-bandwidth"))


def plot_var_c(runs):
    groups = [
        ("③ far-ring\nnext-32768..-16384\n(半圈前·已落盘)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "far-ring", "#2563eb"),
        ("④a near-ring\nnext-384..-64\n(队头后方·已落盘)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "near-ring", "#4f46e5"),
        ("④b next-1\nnext-1\n(前一格·已落盘)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "next-1", "#6366f1"),
        ("④c write-band\nnext..next+384\n(队头前方·在途写带!)", lambda r: r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "write-band", "#b91c1c"),
    ]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.5))
    xs = list(range(len(groups)))
    labels = [g[0] for g in groups]

    for x, (_, pred, color) in enumerate(groups):
        sub = [r for r in runs if pred(r)]
        rates = [r["host"]["card_took_mps"] for r in sub]
        snps_per_cell = [r["card"]["pmu"]["tad"]["tad_snp_msh_out_any"]["val"] / r["cells"] for r in sub]
        pauses_k = [(r["card"]["ring_cores"][0]["pauses"] + r["card"]["ring_cores"][1]["pauses"]) / 1e3 for r in sub]
        taus = [r["perf"]["cha_residency_ns"] / 1000.0 for r in sub]

        med_r = statistics.median(rates)
        med_snp = statistics.median(snps_per_cell)
        med_p = statistics.median(pauses_k)
        med_tau = statistics.median(taus)

        ax1.bar(x, med_r, width=0.52, color=color, alpha=0.86)
        for idx_i, y in enumerate(rates):
            ax1.scatter(x + (idx_i - (len(rates) - 1) / 2) * 0.05, y, color="#111827", s=16, zorder=4)
        ax1.text(x, max(rates) + 2.8, f"{med_r:.1f} M/s", ha="center", va="bottom", fontsize=9.0, fontweight="bold")

        ax2.bar(x, med_snp, width=0.52, color=color, alpha=0.86)
        for idx_i, y in enumerate(snps_per_cell):
            ax2.scatter(x + (idx_i - (len(snps_per_cell) - 1) / 2) * 0.05, y, color="#111827", s=16, zorder=4)
        ax2.text(
            x,
            max(snps_per_cell) + 0.02,
            f"{med_snp:.2f} 次/格\nτ_CHA {med_tau:.2f}µs\n(停顿 {med_p:.0f}k 次)",
            ha="center",
            va="bottom",
            fontsize=7.8,
            fontweight="bold" if x == 3 else "normal",
        )

    ax1.axvline(2.5, color="#b91c1c", linestyle="--", linewidth=1.4, alpha=0.7)
    ax1.text(2.58, 160, "队头 next 前方 →\n(在途写带)", ha="left", va="center", fontsize=8.2, color="#b91c1c", fontweight="bold")
    ax1.set_xticks(xs, labels, fontsize=8.0)
    ax1.set_ylabel("去程 MOVDIR64B 吞吐（M 格/s）")
    ax1.set_ylim(0, 196)
    ax1.axhline(128.32, color="#dc2626", linestyle=":", linewidth=1, alpha=0.6, label="不停顿 0 ns 128.3 M/s")
    ax1.legend(fontsize=8.2, loc="lower left")
    ax1.set_title("(a) 去程吞吐：仅跨过 next 读入在途写带时跌至 113.4 M/s", fontsize=10.0)

    ax2.axvline(2.5, color="#b91c1c", linestyle="--", linewidth=1.4, alpha=0.7)
    ax2.set_xticks(xs, labels, fontsize=8.0)
    ax2.set_ylabel("每格平均 TAD 嗅探次数（tad_snp / 2亿格）")
    ax2.set_ylim(0, 0.74)
    ax2.set_title("(b) 每格 TAD 嗅探与主机排队：写带内嗅探与停顿翻倍", fontsize=10.0)

    fig.suptitle("变量 3（停顿时读请求环的哪个位置）：读 next 后方不降速，读 next 前方在途写带才降速", fontsize=10.8, y=0.98)
    print(save(fig, "mrv-76-split-c-position"))


def main():
    runs = load_verified_runs()
    plot_var_a(runs)
    plot_var_b(runs)
    plot_var_c(runs)


if __name__ == "__main__":
    main()
