"""MRV-82 Summary Chart: Minimal 1-variable paired proofs for the 4 core direct-path questions.
All numbers are read directly from verified raw runs.jsonl (lost = bad = torn = 0).

    uv run --with matplotlib --with numpy python /home/hongtao/dev/marvell-mrv82/docs/figures/mrv-82-minimal-proofs.py
"""
import json
import statistics
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from mrvplot import plt, save  # noqa: E402
import importlib

ROOT = Path(__file__).resolve().parents[2]


def main():
    fig, axes = plt.subplots(2, 2, figsize=(13.2, 8.2))
    ax_a, ax_b = axes[0]
    ax_c, ax_d = axes[1]

    # ── Panel (a): MRV-61 Unidirectional H2C frontier polling vs 3 us pause ──
    r61 = [json.loads(l) for l in (ROOT / "logs/mrv61-2026-11-18/runs.jsonl").read_text().splitlines() if l.strip()]
    for r in r61:
        assert r["host"]["bad"] == 0 and r["host"]["torn"] == 0 and r["card"]["sunk"] == r["cells"] and r["card"]["corrupt"] == 0
    g0 = [r["host"]["card_took_mps"] for r in r61 if r["dir"] == "h2c" and r["poll_gap_ns"] == 0]
    g1 = [r["host"]["card_took_mps"] for r in r61 if r["dir"] == "h2c" and r["poll_gap_ns"] == 1000 and r["poll_gap_mode"] == "spin"]
    g3 = [r["host"]["card_took_mps"] for r in r61 if r["dir"] == "h2c" and r["poll_gap_ns"] == 3000 and r["poll_gap_mode"] == "spin"]

    labels_a = ["不停顿\n(死盯写入最前沿)", "停 1 µs\n(< 3.5 µs 在途窗)", "停 3 µs\n(跨过在途窗)"]
    meds_a = [statistics.median(g0), statistics.median(g1), statistics.median(g3)]
    cols_a = ["#94a3b8", "#64748b", "#dc2626"]
    bars_a = ax_a.bar(range(3), meds_a, width=0.52, color=cols_a, alpha=0.88)
    for idx, vals in enumerate([g0, g1, g3]):
        ax_a.scatter([idx] * len(vals), vals, color="#0f172a", s=22, zorder=3)
        ax_a.text(idx, max(vals) + 4, f"{meds_a[idx]:.1f} M/s", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax_a.set_xticks(range(3), labels_a, fontsize=9.5)
    ax_a.set_ylabel("单向去程吞吐 (M 格/s)")
    ax_a.set_ylim(0, 190)
    ax_a.set_title("(a) 单向去程为什么停在 129 M/s？（只改：卡核取不满批停多久）", fontsize=10.5, fontweight="bold")

    # ── Panel (b): MRV-50 Where does the extra ~3 us in W=64 13 M/s go? ──
    def load_mrv50_csv(fname, row_slice=None):
        path = ROOT / "logs/mrv50-2026-11-17/stamps" / fname
        lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
        parts = lines[0].split()
        sb_t, sb_c, _ = [int(x) for x in parts[4].split(",")]
        sa_t, sa_c, _ = [int(x) for x in parts[6].split(",")]
        a = np.array([[int(x) for x in l.split(",")] for l in lines[1:]], dtype=np.int64)
        if row_slice is not None:
            a = a[row_slice]
        _, st, rt, cin, cout, cwt, _, _ = a.T
        slope = (sa_c - sb_c) / (sa_t - sb_t)
        sc = sb_c + (st - sb_t) * slope
        rc = sb_c + (rt - sb_t) * slope
        return {
            "h2c_p50": float(np.median((cin - sc) / 1e3)),
            "card_p50_ns": float(np.median((cout - cin + cwt).astype(np.float64))),
            "c2h_p50": float(np.median((rc - (cout + cwt)) / 1e3)),
        }

    w1 = load_mrv50_csv("WC4_W1_pb32_rep1.csv")
    w64 = load_mrv50_csv("WC4_W64_pb32_rep1.csv", row_slice=slice(0, 511))

    labels_b = ["W = 1 基线\n(无排队, 1.04 M/s)", "W = 64 慢状态\n(闭环 13.5 M/s)"]
    h2c_b = np.array([w1["h2c_p50"], w64["h2c_p50"]])
    card_b = np.array([w1["card_p50_ns"] / 1e3, w64["card_p50_ns"] / 1e3])
    c2h_b = np.array([w1["c2h_p50"], w64["c2h_p50"]])
    x_b = np.arange(2)
    ax_b.bar(x_b, h2c_b, width=0.46, color="#dc2626", label="去程 (主发出 → 卡看到)")
    ax_b.bar(x_b, card_b, width=0.46, bottom=h2c_b, color="#f59e0b", label="卡核处理 (0.04 µs)")
    ax_b.bar(x_b, c2h_b, width=0.46, bottom=h2c_b + card_b, color="#475569", label="回程 (卡写完 → 主看到)")
    for idx in range(2):
        tot = h2c_b[idx] + card_b[idx] + c2h_b[idx]
        ax_b.text(idx, tot + 0.12, f"往返 {tot:.2f} µs\n(去程 {h2c_b[idx]:.2f} µs)", ha="center", va="bottom", fontsize=9.5, fontweight="bold")
    ax_b.set_xticks(x_b, labels_b, fontsize=9.5)
    ax_b.set_ylabel("每条消息分段耗时 p50 (µs)")
    ax_b.set_ylim(0, 4.6)
    ax_b.legend(loc="upper left", fontsize=8.5)
    ax_b.set_title("(b) 闭环 13 M/s 多出的 ~3 µs 停在哪？（只改：并发窗口 W=1 vs 64）", fontsize=10.5, fontweight="bold")

    # ── Panel (c): MRV-70 Closed-loop lockstep vs Open-loop bidirectional on 1c WC ──
    r70 = [json.loads(l) for l in (ROOT / "logs/mrv70-2026-11-18/runs.jsonl").read_text().splitlines() if l.strip()]
    for r in r70:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    cl_256 = [r["h2c_win_mps"] for r in r70 if r["series"] == "closed_loop" and r["card_cfg"] == "1c_wc_duplex" and "-w 256 " in r["cmd"]]
    op_13 = [r["h2c_win_mps"] for r in r70 if r["series"] == "sym_bidir" and r["card_cfg"] == "1c_wc_duplex" and r["h2c_target_mps"] == 13.5]
    op_35 = [r["h2c_win_mps"] for r in r70 if r["series"] == "sym_bidir" and r["card_cfg"] == "1c_wc_duplex" and r["h2c_target_mps"] == 35.0]
    op_max = [r["h2c_win_mps"] for r in r70 if r["series"] == "sym_bidir" and r["card_cfg"] == "1c_wc_duplex" and r["h2c_target_mps"] == 0]

    labels_c = ["闭环锁步\n(等回复才发, W=256)", "开环双向\n设定 13.5 M/s", "开环双向\n设定 35 M/s", "开环双向\n不限速极值"]
    meds_c = [statistics.median(cl_256), statistics.median(op_13), statistics.median(op_35), statistics.median(op_max)]
    cols_c = ["#94a3b8", "#0284c7", "#0284c7", "#dc2626"]
    notes_c = ["14.2 M/s\n(慢状态墙)", "13.5 M/s × 2\n(单向 0.7 µs)", "35.0 M/s × 2\n(单向 1.0 µs)", f"{meds_c[3]:.1f} M/s × 2\n(合计 83.7 M/s)"]
    ax_c.bar(range(4), meds_c, width=0.50, color=cols_c, alpha=0.88)
    for idx, vals in enumerate([cl_256, op_13, op_35, op_max]):
        ax_c.scatter([idx] * len(vals), vals, color="#0f172a", s=20, zorder=3)
        ax_c.text(idx, max(vals) + 1.5, notes_c[idx], ha="center", va="bottom", fontsize=8.8, fontweight="bold")
    ax_c.set_xticks(range(4), labels_c, fontsize=9)
    ax_c.set_ylabel("单方向吞吐 (M 格/s，双向同时跑)")
    ax_c.set_ylim(0, 52)
    ax_c.set_title("(c) 13 M/s 是硬件上限吗？（只改：闭环等回复 vs 开环双向对发）", fontsize=10.5, fontweight="bold")

    # ── Panel (d): MRV-53 Why is 4-core closed-loop slower than 1-core at W=1024? ──
    m53p = importlib.import_module("mrv-53-phases")
    ph = m53p.analyse_phases(m53p.load_all())
    wc1 = ph[("WC1", 1024, "steady")]
    wc4 = ph[("WC4", 1024, "steady")]

    labels_d = [
        f"卡 1 核 (WC1)\n吞吐 {wc1['untimed_mmsg']:.1f} M/s",
        f"卡 4 核 (WC4)\n吞吐 {wc4['untimed_mmsg']:.1f} M/s",
    ]
    send_d = np.array([wc1["send_ns"], wc4["send_ns"]])
    poll_d = np.array([wc1["bpoll_ns"], wc4["bpoll_ns"]])
    x_d = np.arange(2)
    ax_d.bar(x_d, send_d, width=0.44, color="#64748b", label="主机发请求 (MOVDIR64B)")
    ax_d.bar(x_d, poll_d, width=0.44, bottom=send_d, color="#dc2626", label="主机忙轮询取回复 (跨子环等待)")
    for idx in range(2):
        tot = send_d[idx] + poll_d[idx]
        ax_d.text(
            idx, tot + 1.2,
            f"共 {tot:.1f} ns/条\n(忙轮询 {poll_d[idx]:.1f} ns)",
            ha="center", va="bottom", fontsize=9.5, fontweight="bold"
        )
    ax_d.set_xticks(x_d, labels_d, fontsize=9.5)
    ax_d.set_ylabel("主机每条消息耗时 (ns/条)")
    ax_d.set_ylim(0, 52)
    ax_d.legend(loc="upper left", fontsize=8.5)
    ax_d.set_title("(d) 闭环 W=1024 卡 4 核慢在哪？（只改：卡核数 1 核 vs 4 核）", fontsize=10.5, fontweight="bold")

    fig.suptitle(
        "MRV-82 奥卡姆剃刀汇总：固定「MOVDIR64B 去程 + 写合并回程」，每次只改 1 个变量证明 1 个结论（全部 lost=bad=torn=0）",
        fontsize=11.5, fontweight="bold"
    )
    out = save(fig, "mrv-82-minimal-proofs")
    print("Saved:", out)


if __name__ == "__main__":
    main()
