"""MRV-82 Child Conclusion Sub-Issue Plots (c1..c7).
Following Occam's Razor: each figure shows ONLY the simplest baseline setup and the single variable
changed to prove one conclusion from verified raw runs.jsonl (lost = bad = torn = 0).

    uv run --with matplotlib --with numpy python /home/hongtao/dev/marvell-mrv82/docs/figures/mrv-82-conclusions.py
"""
import importlib
import json
import statistics
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from mrvplot import plt, save  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def plot_c1():
    """Conclusion 1 (from MRV-24): Does return traffic slow MOVDIR64B? No (113.6 vs 112.2 M/s at 3.9 GHz)."""
    m24 = importlib.import_module("mrv-24-summary")
    wc_runs = [r for r in m24.RUNS if r["cfg"] == (1, 1, "wc")]
    alone_39 = [r["rate"] for r in wc_runs if r["dir"] == "h2c" and r["sender"] in m24.FIXED]
    bidir_39 = [r["rate"] for r in wc_runs if r["dir"] == "bidir" and r["sender"] in m24.FIXED]
    bidir_21 = [r["rate"] for r in wc_runs if r["dir"] == "bidir" and r["sender"] not in m24.FIXED and r["ghz"] and r["ghz"] < m24.SLOW_GHZ]

    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    groups = [
        ("单向只发去程\n(发送核锁 3.9 GHz)", alone_39, "#64748b"),
        ("双向同时跑（加回程）\n(发送核锁 3.9 GHz)", bidir_39, "#0284c7"),
        ("双向同时跑（对照）\n(发送核未锁频 2.1 GHz)", bidir_21, "#dc2626"),
    ]
    for i, (label, vals, col) in enumerate(groups):
        med = statistics.median(vals)
        ax.bar(i, med, width=0.46, color=col, alpha=0.88)
        ax.scatter([i] * len(vals), vals, color="#0f172a", s=24, zorder=3)
        ax.text(i, max(vals) + 3, f"{med:.1f} M/s", ha="center", va="bottom", fontsize=10.5, fontweight="bold")

    ax.set_xticks(range(3), [g[0] for g in groups], fontsize=9.8)
    ax.set_ylabel("主机 MOVDIR64B 去程吞吐 (M 格/s)")
    ax.set_ylim(0, 138)
    ax.set_title("最简对照（写合并 1收+1写）：锁频 3.9 GHz 下加回程不掉速，掉到 75.6 M/s 只是发送核掉频", fontsize=10.2, fontweight="bold")
    return save(fig, "mrv-82-c1-bidir-clock")


def plot_c2():
    """Conclusion 2 (from MRV-61): Card polling at write frontier holds H2C at 128.7 M/s; 3 us pause -> 161.6 M/s."""
    r61 = [json.loads(l) for l in (ROOT / "logs/mrv61-2026-11-18/runs.jsonl").read_text().splitlines() if l.strip()]
    for r in r61:
        assert r["host"]["bad"] == 0 and r["host"]["torn"] == 0 and r["card"]["sunk"] == r["cells"] and r["card"]["corrupt"] == 0

    gaps = [0, 1000, 3000]
    labels = ["不停顿 (0 µs)\n死盯写入最前沿", "停 1 µs\n(< 3.5 µs 在途写窗)", "停 3 µs\n(跨过在途写窗)"]
    cols = ["#94a3b8", "#64748b", "#dc2626"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.6, 4.4))
    for i, gap in enumerate(gaps):
        sub = [r for r in r61 if r["dir"] == "h2c" and r["poll_gap_ns"] == gap and (gap == 0 or r["poll_gap_mode"] == "spin")]
        rates = [r["host"]["card_took_mps"] for r in sub]
        chas = [r["perf"]["cha_mean_entries"] for r in sub]
        res_us = [r["perf"]["cha_residency_ns"] / 1000.0 for r in sub]

        med_r = statistics.median(rates)
        ax1.bar(i, med_r, width=0.48, color=cols[i], alpha=0.88)
        ax1.scatter([i] * len(rates), rates, color="#0f172a", s=22, zorder=3)
        ax1.text(i, max(rates) + 3.5, f"{med_r:.1f} M/s", ha="center", va="bottom", fontsize=10, fontweight="bold")

        med_c = statistics.median(chas)
        med_u = statistics.median(res_us)
        ax2.bar(i, med_c, width=0.48, color=cols[i], alpha=0.88)
        ax2.scatter([i] * len(chas), chas, color="#0f172a", s=22, zorder=3)
        ax2.text(i, max(chas) + 10, f"{med_c:.0f} 条\n({med_u:.2f} µs)", ha="center", va="bottom", fontsize=9.2, fontweight="bold")

    ax1.set_xticks(range(3), labels, fontsize=9.2)
    ax1.set_ylabel("单向去程 MOVDIR64B 吞吐 (M 格/s)")
    ax1.set_ylim(0, 188)
    ax1.set_title("(a) 去程吞吐：停 3 µs 跳至单核发射上限 161.6 M/s", fontsize=10, fontweight="bold")

    ax2.set_xticks(range(3), labels, fontsize=9.2)
    ax2.set_ylabel("主机 CHA 在途排队条数")
    ax2.set_ylim(0, 520)
    ax2.axhline(446.6, color="#64748b", linestyle=":", linewidth=1.2, label="CHA 反压顶棚 (~447 条)")
    ax2.legend(loc="upper right", fontsize=8.5)
    ax2.set_title("(b) 主机在途排队：停 3 µs 脱离 447 条反压顶棚", fontsize=10, fontweight="bold")

    fig.suptitle("最简对照（单向去程 + 空转停顿）：只改「卡核取不满 32 格时的停顿时长」（lost=bad=torn=0）", fontsize=10.5, fontweight="bold")
    return save(fig, "mrv-82-c2-frontier-pause")


def plot_c3():
    """Conclusion 3 (from MRV-50): Where does the extra ~3 us in W=64 13 M/s go? In H2C (outbound)."""
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

    specs = [
        ("W = 1\n（无排队基线，1.04 M/s）", w1),
        ("W = 64\n（慢状态 13.5 M/s）", w64),
    ]
    fig, ax = plt.subplots(figsize=(8.6, 4.0))
    y = np.arange(len(specs))
    h2c = np.array([s[1]["h2c_p50"] for s in specs])
    card = np.array([s[1]["card_p50_ns"] / 1e3 for s in specs])
    c2h = np.array([s[1]["c2h_p50"] for s in specs])

    ax.barh(y, h2c, height=0.44, color="#dc2626", label="去程 h2c（主机发出 → 卡核看到）")
    ax.barh(y, card, left=h2c, height=0.44, color="#f59e0b", label="卡上处理（卡核看到 → 发出回复：恒为 0.04 µs）")
    ax.barh(y, c2h, left=h2c + card, height=0.44, color="#475569", label="回程 c2h（卡核写完 → 主机看到）")

    for i in range(len(specs)):
        tot = h2c[i] + card[i] + c2h[i]
        ax.text(
            tot + 0.10, y[i],
            f"往返 {tot:.2f} µs\n(去程 {h2c[i]:.2f} + 卡 {card[i]:.2f} + 回程 {c2h[i]:.2f} µs)",
            va="center", fontsize=9.2, fontweight="bold"
        )

    ax.set_yticks(y, [s[0] for s in specs], fontsize=9.8)
    ax.invert_yaxis()
    ax.set_xlabel("每条消息分段耗时中位数 p50 (µs)")
    ax.set_xlim(0, 6.2)
    ax.set_ylim(1.55, -0.55)
    ax.legend(loc="upper right", fontsize=8.8)
    ax.set_title("最简对照（写合并 4 卡核闭环）：W=1 → W=64 多出的 ~3 µs 全部停在「去程」，卡上仅 0.04 µs", fontsize=10.0, fontweight="bold")
    return save(fig, "mrv-82-c3-where-3us")


def plot_c4():
    """Conclusion 4 (from MRV-70): 13 M/s is caused by closed-loop lockstep; open-loop bidir hits 35-42 M/s x 2."""
    r70 = [json.loads(l) for l in (ROOT / "logs/mrv70-2026-11-18/runs.jsonl").read_text().splitlines() if l.strip()]
    for r in r70:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.4))

    cl_256 = [r["h2c_win_mps"] for r in r70 if r["series"] == "closed_loop" and r["card_cfg"] == "1c_wc_duplex" and "-w 256 " in r["cmd"]]
    rates = [5.0, 10.0, 13.5, 20.0, 25.0, 30.0, 35.0]
    op_h2c, op_c2h, lat_h2c, lat_c2h = [], [], [], []
    for rt in rates:
        sub = [r for r in r70 if r["series"] == "sym_bidir" and r["card_cfg"] == "1c_wc_duplex" and r["h2c_target_mps"] == rt]
        op_h2c.append(statistics.median(r["h2c_win_mps"] for r in sub))
        op_c2h.append(statistics.median(r["c2h_win_mps"] for r in sub))
        lat_h2c.append(statistics.median(r["h2c_tot_us"]["p50"] for r in sub))
        lat_c2h.append(statistics.median(r["c2h_tot_us"]["p50"] for r in sub))
    op_max = statistics.median(r["h2c_win_mps"] for r in r70 if r["series"] == "sym_bidir" and r["card_cfg"] == "1c_wc_duplex" and r["h2c_target_mps"] == 0)

    ax1.plot([0, 42], [0, 42], linestyle=":", color="#94a3b8", label="理想线（实测 = 设定）")
    ax1.plot(rates, op_h2c, "o-", color="#0284c7", lw=2.2, label="开环双向：去程实测 (M/s)")
    ax1.plot(rates, op_c2h, "s--", color="#16a34a", lw=1.8, label="开环双向：回程实测 (M/s)")
    ax1.axhline(statistics.median(cl_256), color="#dc2626", linestyle="-.", lw=1.8, label=f"闭环锁步慢状态 (14.2 M/s)")
    ax1.axhline(op_max, color="#0f172a", linestyle=":", lw=1.4, label=f"开环不限速极值 ({op_max:.1f} M/s × 2)")
    ax1.set_xlabel("单方向设定速率 R (M 格/s，双向同时发)")
    ax1.set_ylabel("单方向实测吞吐 (M 格/s)")
    ax1.set_xlim(0, 40)
    ax1.set_ylim(0, 48)
    ax1.legend(loc="upper left", fontsize=8.2)
    ax1.set_title("(a) 吞吐：去掉闭环锁步后，畅通跨过 13–14 M/s", fontsize=10, fontweight="bold")

    ax2.plot(rates, lat_h2c, "o-", color="#0284c7", lw=2.2, label="开环去程单向延迟 p50 (µs)")
    ax2.plot(rates, lat_c2h, "s--", color="#16a34a", lw=2.0, label="开环回程单向延迟 p50 (µs)")
    ax2.annotate("30–35 M/s × 2 时\n去程 0.85–0.97 µs\n回程 1.13–1.24 µs", xy=(35.0, lat_h2c[-1]), xytext=(23.5, 6.5),
                 fontsize=8.8, fontweight="bold", arrowprops=dict(arrowstyle="->", color="#0284c7", lw=1.2))
    ax2.set_xlabel("单方向设定速率 R (M 格/s)")
    ax2.set_ylabel("单向延迟中位数 p50 (µs)")
    ax2.set_xlim(0, 38)
    ax2.set_ylim(0, 20)
    ax2.legend(loc="upper left", fontsize=8.5)
    ax2.set_title("(b) 单向延迟：在 30–35 M/s × 2 下双方向均约 1 µs", fontsize=10, fontweight="bold")

    fig.suptitle("最简对照（1 主机核 + 1 卡核写合并）：只改「闭环等回复 vs 开环双向对发」（lost=bad=torn=0）", fontsize=10.5, fontweight="bold")
    return save(fig, "mrv-82-c4-open-vs-closed")


def plot_c5():
    """Conclusion 5 (from MRV-53): Who decides closed-loop throughput? Host vs Card slope + 1c vs 4c poll wait."""
    m53s = importlib.import_module("mrv-53-slopes")
    m53p = importlib.import_module("mrv-53-phases")
    warm = m53s.analyse(m53s.load(), warm=True)
    ph = m53p.analyse_phases(m53p.load_all())

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.5))

    # Left panel: Critical path sensitivity (slope of ns/msg vs +delta)
    cases = [
        ("卡 1 核 W=1024\n(快状态 50.5 M/s)", warm[("WC1", 1024)]["host_fit"], warm[("WC1", 1024)]["card_fit"]),
        ("卡 4 核 W=64\n(慢状态 13.1 M/s)", warm[("WC4", 64)]["host_fit"], warm[("WC4", 64)]["card_fit"]),
    ]
    x = np.arange(len(cases))
    h_sl = [c[1] for c in cases]
    c_sl = [c[2] for c in cases]
    ax1.bar(x - 0.18, h_sl, width=0.32, color="#0284c7", label="主机每条加 +1 ns 忙等 → 周期变化")
    ax1.bar(x + 0.18, c_sl, width=0.32, color="#dc2626", label="卡核每条加 +1 ns 忙等 → 周期变化")
    for i in range(len(cases)):
        ax1.text(i - 0.18, max(0.05, h_sl[i]) + 0.05, f"斜率 {h_sl[i]:.2f}", ha="center", va="bottom", fontsize=9, fontweight="bold", color="#0284c7")
        ax1.text(i + 0.18, max(0.05, c_sl[i]) + 0.05, f"斜率 {c_sl[i]:.2f}", ha="center", va="bottom", fontsize=9, fontweight="bold", color="#dc2626")
    ax1.axhline(1.0, color="#94a3b8", linestyle=":", lw=1.2, label="斜率 = 1（100% 在关键路径上）")
    ax1.axhline(0.0, color="#cbd5e1", linestyle="-", lw=1.0)
    ax1.set_xticks(x, [c[0] for c in cases], fontsize=9.5)
    ax1.set_ylabel("每条消息耗时增量 ÷ 注入延迟 (ns/ns)")
    ax1.set_ylim(-0.18, 1.38)
    ax1.legend(loc="upper right", fontsize=8.2)
    ax1.set_title("(a) 谁在关键路径：快状态两边紧凑，13 M/s 慢状态两边都在空等", fontsize=9.8, fontweight="bold")

    # Right panel: Why is 4c slower than 1c at W=1024?
    wc1 = ph[("WC1", 1024, "steady")]
    wc4 = ph[("WC4", 1024, "steady")]
    x2 = np.arange(2)
    sends = np.array([wc1["send_ns"], wc4["send_ns"]])
    polls = np.array([wc1["bpoll_ns"], wc4["bpoll_ns"]])
    ax2.bar(x2, sends, width=0.44, color="#64748b", label="主机发请求 (MOVDIR64B)")
    ax2.bar(x2, polls, width=0.44, bottom=sends, color="#dc2626", label="主机忙轮询取回复 (跨卡核子环等待)")
    for i, d in enumerate([wc1, wc4]):
        tot = d["send_ns"] + d["bpoll_ns"]
        ax2.text(i, tot + 1.2, f"共 {tot:.1f} ns/条\n(忙轮询 {d['bpoll_ns']:.1f} ns)", ha="center", va="bottom", fontsize=9.2, fontweight="bold")
    ax2.set_xticks(x2, [f"卡 1 核 W=1024\n({wc1['untimed_mmsg']:.1f} M/s)", f"卡 4 核 W=1024\n({wc4['untimed_mmsg']:.1f} M/s)"], fontsize=9.5)
    ax2.set_ylabel("主机每条消息耗时拆解 (ns/条)")
    ax2.set_ylim(0, 50)
    ax2.legend(loc="upper left", fontsize=8.5)
    ax2.set_title("(b) 卡 4 核慢在哪：主机在轮询里跨 4 个子环干等回复 (+19 ns/条)", fontsize=9.8, fontweight="bold")

    fig.suptitle("最简对照（写合并闭环）：只改「受控忙等 δ」与「卡核数 1 核 vs 4 核」（lost=bad=torn=0）", fontsize=10.5, fontweight="bold")
    return save(fig, "mrv-82-c5-who-decides")


def plot_c6():
    """Conclusion 6 (from MRV-54): Interleaving replies across segments does NOT break 64B write-combining."""
    r54 = [json.loads(l) for l in (ROOT / "logs/mrv54-2026-11-18/runs.jsonl").read_text().splitlines() if l.strip()]
    for r in r54:
        assert r["host"]["lost"] == 0 and r["host"]["bad"] == 0 and r["host"]["torn"] == 0

    g1 = [r for r in r54 if r["window"] == 64 and r["group"] == "base"]
    g4 = [r for r in r54 if r["window"] == 64 and r["group"] == "base-interleaved"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 4.2))
    labels = ["本段连续写 (G1)\n每个卡核只写自己那一段", "跨段交错写 (G4)\n按 tag 交替写到两个 64KiB 段"]
    cols = ["#64748b", "#0284c7"]

    for i, sub in enumerate([g1, g4]):
        mmsg = [r["host"]["mmsg"] for r in sub]
        itom = [r["iio"]["itom_per_reply"] for r in sub]
        med_m = statistics.mean(mmsg)
        med_i = statistics.mean(itom)

        ax1.bar(i, med_m, width=0.44, color=cols[i], alpha=0.88)
        ax1.scatter([i] * len(mmsg), mmsg, color="#0f172a", s=24, zorder=3)
        ax1.text(i, max(mmsg) + 0.3, f"{med_m:.2f} M/s", ha="center", va="bottom", fontsize=10, fontweight="bold")

        ax2.bar(i, med_i, width=0.44, color=cols[i], alpha=0.88)
        ax2.scatter([i] * len(itom), itom, color="#0f172a", s=24, zorder=3)
        ax2.text(i, med_i + 0.03, f"{med_i:.4f} 次/条\n(整行写 99.99%)", ha="center", va="bottom", fontsize=9.5, fontweight="bold")

    ax1.set_xticks(range(2), labels, fontsize=9.2)
    ax1.set_ylabel("闭环吞吐 (M 格/s，W = 64)")
    ax1.set_ylim(0, 17.5)
    ax1.set_title("(a) 闭环吞吐：连续写 vs 跨段交错写完全持平", fontsize=10, fontweight="bold")

    ax2.set_xticks(range(2), labels, fontsize=9.2)
    ax2.set_ylabel("每条回复触发的 PCIe 64B 整行写 (ItoM)")
    ax2.set_ylim(0, 1.25)
    ax2.axhline(1.0, color="#dc2626", linestyle=":", lw=1.2, label="理想整行合并 (1.000 次/条)")
    ax2.legend(loc="lower right", fontsize=8.5)
    ax2.set_title("(b) 硬件写合并：每条 64B 回复始终合成 1 次整行写", fontsize=10, fontweight="bold")

    fig.suptitle("最简对照（写合并 4 卡核闭环，按序号取请求）：只改「回复本段连续写 vs 跨段交错写」（lost=bad=torn=0）", fontsize=10.2, fontweight="bold")
    return save(fig, "mrv-82-c6-wc-interleave")


def plot_c7():
    """Conclusion 7 (from MRV-55): Card L3 coherency between cores is 64 B, not 128 B."""
    r55 = [json.loads(l) for l in (ROOT / "logs/mrv55-2026-11-18/runs.jsonl").read_text().splitlines() if l.strip()]
    for r in r55:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0

    dists = [0, 64, 128, 4096]
    labels = ["0 B\n(写同一个 64B 格)", "64 B\n(同 128B 块相邻格)", "128 B\n(不同 128B 块)", "4 KiB\n(不同内存页)"]
    cols = ["#dc2626", "#0284c7", "#0284c7", "#64748b"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.6, 4.3))
    for i, d in enumerate(dists):
        sub = [r for r in r55 if r["mode"] == "write_only" and r["dist_bytes"] == d]
        mops = [(r["core0_mops"] + r["core1_mops"]) / 2.0 for r in sub]
        snps = [r["tad"]["tad_snp_msh_out_any"]["per_s"] / 1e6 for r in sub]

        med_m = statistics.mean(mops)
        med_s = statistics.mean(snps)

        ax1.bar(i, med_m, width=0.48, color=cols[i], alpha=0.88)
        ax1.scatter([i] * len(mops), mops, color="#0f172a", s=20, zorder=3)
        ax1.text(i, max(mops) + 6, f"{med_m:.1f} M/s", ha="center", va="bottom", fontsize=9.5, fontweight="bold")

        ax2.bar(i, med_s, width=0.48, color=cols[i], alpha=0.88)
        ax2.scatter([i] * len(snps), snps, color="#0f172a", s=20, zorder=3)
        ax2.text(i, max(snps) + 0.5, f"{med_s:.2f} M/s", ha="center", va="bottom", fontsize=9.5, fontweight="bold")

    ax1.set_xticks(range(4), labels, fontsize=9)
    ax1.set_ylabel("每核 64 B 格写入速度 (M 格/s)")
    ax1.set_ylim(0, 305)
    ax1.set_title("(a) 写入速度：隔开 64 B 即满速 259.8 M/s（与 128 B 完全相同）", fontsize=9.8, fontweight="bold")

    idle_snp = statistics.mean(r["tad"]["tad_snp_msh_out_any"]["per_s"] / 1e6 for r in r55 if r["mode"] == "idle")
    ax2.axhline(idle_snp, color="#64748b", linestyle=":", lw=1.3, label=f"全卡空闲背景嗅探 ({idle_snp:.2f} M/s)")
    ax2.set_xticks(range(4), labels, fontsize=9)
    ax2.set_ylabel("卡侧 TAD 跨核嗅探速率 (M 次/s)")
    ax2.set_ylim(0, 21)
    ax2.legend(loc="upper right", fontsize=8.5)
    ax2.set_title("(b) 硬件嗅探：隔开 64 B 零额外跨核嗅探，只有 0 B 同格才冲突", fontsize=9.8, fontweight="bold")

    fig.suptitle("最简对照（两个卡核各自写一个 64 B 格）：只改「两格之间的字节距离 d」（lost=bad=torn=0）", fontsize=10.3, fontweight="bold")
    return save(fig, "mrv-82-c7-card-64b")


if __name__ == "__main__":
    for fn in (plot_c1, plot_c2, plot_c3, plot_c4, plot_c5, plot_c6, plot_c7):
        print("Saved:", fn())
