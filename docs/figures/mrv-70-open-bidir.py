"""MRV-70 data figure (revised after Argon & Carbon reviews):
Open-loop bidirectional communication vs unidirectional and closed-loop baselines.

Reads verified raw runs from logs/mrv70-2026-11-18/runs.jsonl (asserting lost = bad = torn = 0 on every run)
and plots a 4-panel summary chart using mrvplot.py:
  (a) Symmetric open-loop bidirectional throughput vs target rate R
  (b) Component breakdown on 1c vs 1+1: h2c_wire_us vs h2c_backlog_us vs c2h_wire_us (with individual reps showing 20-30 M/s bistability and cpu40 TX/RX coupling)
  (c) H2C wire+ring latency (p50 & p90 + normalized card empty-poll rate M empty/s) across 10-40 M/s
  (d) Open-loop paced echo (both echo_rtt_us and echo_tot_us, noting echo_cap=4032 and 36.9 M/s overload) vs Closed-loop W=1..1024
"""
import json
from pathlib import Path
import statistics

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs" / "mrv70-2026-11-18" / "runs.jsonl"


def load_runs():
    rows = [json.loads(line) for line in RUNS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows:
        assert r["rc"] == 0, f"non-zero rc: {r}"
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0, f"integrity error: {r}"
    return rows


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def sample_win_s(run):
    """Actual time span (s) covered by H2C_SAMPLES=2048 (stride 512 => max 1,048,576 cells)."""
    sent = run.get("h2c_sent", 0)
    mps = run.get("h2c_win_mps", 0.0)
    if sent <= 0 or mps <= 0:
        return run.get("win_s", 0.24)
    covered_cells = min(sent * 0.8, 2048 * 512)
    return max(1e-6, covered_cells / (mps * 1e6))


def main():
    runs = load_runs()
    fig, axes = plt.subplots(2, 2, figsize=(15.6, 10.6))
    ax_tput, ax_comp = axes[0]
    ax_saw, ax_echo = axes[1]

    cfg_styles = [
        ("1c_wc_duplex", "1 卡核同核兼收发 (WC)", "#0284c7", "o"),
        ("1p1_wc_split", "1+1 卡核收发分核 (WC)", "#16a34a", "s"),
        ("1p1_dpi_split", "1+1 卡核收发分核 (DPI)", "#0d9488", "D"),
        ("2p2_wc_split", "2+2 卡核收发分核 (WC)", "#d97706", "^"),
        ("4c_wc_duplex", "4 卡核同核兼收发 (WC)", "#dc2626", "v"),
    ]
    rates = [5.0, 10.0, 13.5, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0]

    # Panel (a): Symmetric open-loop bidirectional delivered per-direction rate vs target rate
    ax_tput.plot([0, 60], [0, 60], linestyle=":", color="#94a3b8", linewidth=1.3, label="理想线 (实测 = 设定)")
    for cfg, label, color, marker in cfg_styles:
        xs, ys_h, ys_c = [], [], []
        for r in rates:
            sub = [x for x in runs if x["series"] == "sym_bidir" and x["card_cfg"] == cfg and x["h2c_target_mps"] == r]
            if not sub:
                continue
            xs.append(r)
            ys_h.append(med([x["h2c_win_mps"] for x in sub]))
            ys_c.append(med([x["c2h_win_mps"] for x in sub]))
        ax_tput.plot(xs, ys_h, marker=marker, color=color, linewidth=1.8, markersize=5.5, label=f"{label}")
        ax_tput.plot(xs, ys_c, marker=marker, color=color, linewidth=1.2, linestyle="--", markersize=4.5, alpha=0.75)

    ax_tput.axhline(13.5, color="#64748b", linestyle="-.", linewidth=1.1, alpha=0.7, label="闭环 W=64 慢状态 (13.5 M/s)")
    ax_tput.set_xlim(0, 63)
    ax_tput.set_ylim(0, 63)
    ax_tput.set_xlabel("单方向设定速率 R（M 格/s，双向合计 2R）")
    ax_tput.set_ylabel("单方向实测稳态吞吐（M 格/s，实线 h2c / 虚线 c2h）")
    ax_tput.set_title("(a) 对称双向开环吞吐：跨过 13.5 M/s 无掉速，1+1 达 45–49 M/s × 2（合计 90–97 M/s）", fontsize=10.0)
    ax_tput.legend(fontsize=7.8, loc="upper left")

    # Panel (b): Component breakdown for 1c_wc_duplex vs 1p1_wc_split (h2c_wire vs h2c_backlog vs c2h_wire + individual reps)
    sub_rates_b = [5.0, 10.0, 13.5, 20.0, 25.0, 30.0, 35.0, 40.0]
    for cfg, prefix, c_wire, c_bl, c_c2h, m_style in [
        ("1p1_wc_split", "1+1 分核", "#16a34a", "#15803d", "#86efac", "s"),
        ("1c_wc_duplex", "1c 同核", "#0284c7", "#0369a1", "#7dd3fc", "o"),
    ]:
        rx, hw_med, hb_med, cw_med = [], [], [], []
        for r in sub_rates_b:
            if cfg == "1c_wc_duplex" and r >= 40.0:
                continue  # 1c saturates at 39.7 M/s
            sub = [x for x in runs if x["series"] == "sym_bidir" and x["card_cfg"] == cfg and x["h2c_target_mps"] == r]
            if not sub:
                continue
            rx.append(r)
            hw_med.append(med([x["h2c_wire_us"]["p50"] for x in sub]))
            hb_med.append(med([x["h2c_backlog_us"]["p50"] for x in sub]))
            cw_med.append(med([x["c2h_wire_us"]["p50"] for x in sub]))
            # Plot individual rep h2c_wire_us to show 20-30 M/s bistability transparently
            for x in sub:
                ax_comp.scatter([r], [x["h2c_wire_us"]["p50"]], color=c_wire, marker=m_style, s=18, alpha=0.35)
        ax_comp.plot(rx, hw_med, marker=m_style, color=c_wire, linestyle="-", linewidth=2.0, markersize=5.5, label=f"{prefix} 去程路+卡环 h2c_wire p50")
        ax_comp.plot(rx, hb_med, marker=m_style, color=c_bl, linestyle="--", linewidth=1.6, markersize=4.5, label=f"{prefix} 主机发积压 h2c_backlog p50")
        ax_comp.plot(rx, cw_med, marker=m_style, color=c_c2h, linestyle=":", linewidth=1.8, markersize=4.5, label=f"{prefix} 回程路+主环 c2h_wire p50")

    ax_comp.set_xlim(3, 42)
    ax_comp.set_ylim(-1, 56)
    ax_comp.set_xlabel("单方向设定速率 R（M 格/s）")
    ax_comp.set_ylabel("分阶段延迟 p50（µs，散点为 3 次重复各自的 h2c_wire）")
    ax_comp.set_title("(b) 延迟拆解：1+1 在 30–40 M/s 去程 wire 降至 4.6 µs，高延迟来自 cpu40 单核收发互锁（backlog≈c2h_wire）", fontsize=9.5)
    ax_comp.legend(fontsize=7.3, loc="upper left", ncol=2)

    # Panel (c): H2C wire p50 & p90 + normalized card empty-poll rate (M empty/s)
    cmp_rates = [10.0, 13.5, 20.0, 25.0, 30.0, 35.0, 40.0]
    ax_emp = ax_saw.twinx()
    ax_emp.spines["top"].set_visible(False)
    ax_emp.grid(False)
    for series, cfg, name, color, marker in [
        ("uni_h2c", "1c_wc_duplex", "单向 h2c", "#64748b", "d"),
        ("sym_bidir", "1p1_wc_split", "双向 1+1 分核", "#16a34a", "s"),
        ("sym_bidir", "1c_wc_duplex", "双向 1c 同核", "#0284c7", "o"),
    ]:
        rx, p50s, p90s, emp_rates = [], [], [], []
        for r in cmp_rates:
            if series == "sym_bidir" and cfg == "1c_wc_duplex" and r >= 40.0:
                continue
            sub = [x for x in runs if x["series"] == series and x["card_cfg"] == cfg and x["h2c_target_mps"] == r]
            if not sub:
                continue
            p50 = med([x["h2c_wire_us"]["p50"] for x in sub])
            p90 = med([x["h2c_wire_us"]["p90"] for x in sub])
            er = med([x["card_empty_delta"] / sample_win_s(x) / 1e6 for x in sub])
            rx.append(r)
            p50s.append(p50)
            p90s.append(p90)
            emp_rates.append(er)
        ax_saw.plot(rx, p50s, marker=marker, color=color, linewidth=2.0, markersize=5.5, label=f"{name} wire p50（阴影 p90）")
        ax_saw.fill_between(rx, p50s, p90s, color=color, alpha=0.13)
        if series == "sym_bidir":
            ax_emp.plot(rx, emp_rates, marker=marker, color=color, linestyle="--", linewidth=1.3, markersize=4.0, alpha=0.75, label=f"{name} 卡空轮询率 (M/s)")

    ax_saw.set_xlim(8, 42)
    ax_saw.set_ylim(0, 48)
    ax_emp.set_ylim(0, 36)
    ax_saw.set_xlabel("去程设定速率 R_h2c（M 格/s）")
    ax_saw.set_ylabel("去程 h2c_wire_us（µs，实线 p50 / 阴影 p90）")
    ax_emp.set_ylabel("归一化卡核空轮询率（M empty/s，虚线）", color="#475569")
    ax_saw.set_title("(c) 去程锯齿与归一化空轮询率：1c 在 13.5–25 M/s 仍有 p90 锯齿，30–35 M/s 空轮询降至 <0.04 M/s 后压平", fontsize=9.4)
    lines1, labels1 = ax_saw.get_legend_handles_labels()
    lines2, labels2 = ax_emp.get_legend_handles_labels()
    ax_saw.legend(lines1 + lines2, labels1 + labels2, fontsize=7.4, loc="upper right")

    # Panel (d): Open-loop paced echo (both echo_rtt_us and echo_tot_us) vs Closed-loop W sweep (1c and 4c)
    for cfg, label_prefix, color, marker in [
        ("1c_wc_duplex", "1 卡核 WC", "#0284c7", "o"),
        ("4c_wc_duplex", "4 卡核 WC", "#dc2626", "v"),
    ]:
        cl_x, cl_y, cl_w = [], [], []
        for w in [1, 16, 32, 64, 256, 1024]:
            sub = [x for x in runs if x["series"] == "closed_loop" and x["card_cfg"] == cfg and x["window"] == w]
            if not sub:
                continue
            cl_x.append(med([x["h2c_win_mps"] for x in sub]))
            cl_y.append(med([x["rtt_us"]["p50"] for x in sub]))
            cl_w.append(w)
        ax_echo.plot(cl_x, cl_y, marker=marker, color=color, linestyle="--", linewidth=1.5, alpha=0.7, label=f"{label_prefix} 闭环乒乓 RTT (W=1..1024)")
        for x_v, y_v, w_v in zip(cl_x, cl_y, cl_w):
            if w_v in (1, 32, 64, 256, 1024):
                ax_echo.annotate(f"W={w_v}", (x_v, y_v), textcoords="offset points", xytext=(3, 3), fontsize=7.2, color=color)

        oe_x, oe_rtt, oe_tot_x, oe_tot = [], [], [], []
        for r in [5.0, 10.0, 13.5, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0]:
            sub = [x for x in runs if x["series"] == "open_echo" and x["card_cfg"] == cfg and x["h2c_target_mps"] == r]
            if not sub:
                continue
            mp = med([x["c2h_win_mps"] for x in sub])
            rtt_v = med([x["echo_rtt_us"]["p50"] for x in sub])
            tot_v = med([x["echo_tot_us"]["p50"] for x in sub])
            if rtt_v is not None:
                oe_x.append(mp)
                oe_rtt.append(rtt_v)
            if tot_v is not None and tot_v < 100:
                oe_tot_x.append(mp)
                oe_tot.append(tot_v)
        ax_echo.plot(oe_x, oe_rtt, marker=marker, color=color, linestyle="-", linewidth=2.1, label=f"{label_prefix} 开环回显 echo_rtt p50 (cap=4032)")
        ax_echo.plot(oe_tot_x, oe_tot, marker=marker, color=color, linestyle=":", linewidth=1.4, alpha=0.8, label=f"{label_prefix} 开环回显 echo_tot p50 (未超载)")

    ax_echo.annotate("4c R=40/50 超载\n(36.9 M/s, tot=12–38 ms)", (36.85, 54.9), textcoords="offset points", xytext=(-78, -22), fontsize=7.3, color="#dc2626")
    ax_echo.set_xlim(0, 54)
    ax_echo.set_ylim(0, 60)
    ax_echo.set_xlabel("实测往返吞吐（M 格/s）")
    ax_echo.set_ylabel("往返延迟 p50（µs，实线 echo_rtt / 点线 echo_tot / 虚线闭环）")
    ax_echo.set_title("(d) 请求-回复对比：1c 逐格退避在 20–25 M/s 仅 1.1–1.5 µs；4c 跨过 13.5 M/s 达 35.2 M/s（R≥40 超载）", fontsize=9.4)
    ax_echo.legend(fontsize=7.2, loc="upper left")

    fig.suptitle(
        "MRV-70（评审修订）：直接通路开环双向通讯 vs 单向与闭环基线（主机核 40 固定 3.9 GHz，uncore 2.2 GHz，卡核 2.0 GHz，64 B）",
        fontsize=11.0,
        y=0.995,
    )
    out = save(fig, "mrv-70-open-bidir")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
