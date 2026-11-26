"""MRV-206 Figure 2: Open-loop Poisson latency-throughput curves (p50 and p99)
on unmodified upstream Memcached 1.6.45 for 8 B GET (Zipf) and Facebook ETC Mix (Zipf).

100% dynamically computed from `logs/mrv206-2026-11-26/runs.jsonl` and `attempts.jsonl`
(zero hardcoded knee rates, percentages, or failure counts):
  - Solid markers: verified lossless points (`n = 3`, 0 lost / 0 window-cap / 0 page faults).
  - Half-filled / annotated `n=2*`: `direct_peek etc_mix` 1.80-2.70 Mrps (rep 3 halted at
    1.80 Mrps by a transient 1.4 ms stall with `lost = 0`, `wcap = 325,766`).
  - Hollow markers connected by dotted tail: saturating knee attempts from `attempts.jsonl`
    (non-CRC loss or window-cap saturation) so the p99 knee rise is directly visible.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv206-2026-11-26/runs.jsonl"
ATTS_PATH = ROOT / "logs/mrv206-2026-11-26/attempts.jsonl"


def med(xs):
    return statistics.median(xs)


def assert_verified(r):
    assert r.get("verified") is True and not r.get("errors")
    c, h, k = r["client"], r["host"], r["card"]
    assert c["lost"] == 0 and c["bad"] == 0 and c["proto_err"] == 0 and c["val_err"] == 0 and c["torn"] == 0
    if r["drive_mode"] == "poisson":
        assert c.get("window_cap_hits", 0) == 0
    assert h["page_faults"] == 0 and h["pmu"]["page_faults"] == 0 and h["pmu"]["min_running_pct"] >= 99.5
    assert h["sdp_rx_nombuf"] == 0 and k["eth_imissed"] == 0


def main():
    runs = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    atts = [json.loads(ln) for ln in ATTS_PATH.read_text().splitlines() if ln.strip()]

    pois_ok = defaultdict(list)
    for r in runs:
        assert_verified(r)
        if r["section"] == "poisson":
            pois_ok[(r["policy"], r["workload"], int(round(r["rate_krps"])))].append(r)

    # Collect non-CRC saturating knee attempts (window_cap_hits > 0 or lost > crc + 10)
    pois_sat = defaultdict(list)
    for a in atts:
        if a["section"] != "poisson" or a.get("verified"):
            continue
        c = a["client"]
        crc = (c.get("merc_ethtool_delta") or {}).get("rx_crc_errors_phy", 0)
        if crc == 0 and (c.get("window_cap_hits", 0) > 0 or c.get("lost", 0) > 10):
            pois_sat[(a["policy"], a["workload"], int(round(a["rate_krps"])))].append(a)

    pols = [
        ("sdp_fair", "SDP fair-full (4Q×512)", "#64748b", "s"),
        ("direct_peek", "DirectPush (窥探版)", "#2563eb", "^"),
        ("direct_safe", "DirectPush (安全直取版)", "#1a7f37", "o"),
    ]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.4))

    # ---------------- Panel A: get8 Zipf Poisson (all points n=3) ----------------
    rates_get8 = sorted({rk for (p, wl, rk) in pois_ok if wl == "get8"})
    for pol, label, color, mk in pols:
        xs, p50s, p99s = [], [], []
        for rk in rates_get8:
            rs = pois_ok[(pol, "get8", rk)]
            if len(rs) == 3:
                xs.append(rk / 1000.0)
                p50s.append(med([r["client"]["tot_p50_us"] for r in rs]))
                p99s.append(med([r["client"]["tot_p99_us"] for r in rs]))
        ax1.plot(xs, p99s, marker=mk, color=color, linewidth=2.2, markersize=5.5, label=f"{label} p99")
        ax1.plot(xs, p50s, marker=mk, color=color, linewidth=1.3, markersize=4.0, linestyle="--", alpha=0.8)

    p50_s15 = med([r["client"]["tot_p50_us"] for r in pois_ok[("sdp_fair", "get8", 1500)]])
    p50_d15 = med([r["client"]["tot_p50_us"] for r in pois_ok[("direct_safe", "get8", 1500)]])
    p99_s36 = med([r["client"]["tot_p99_us"] for r in pois_ok[("sdp_fair", "get8", 3600)]])
    p99_d36 = med([r["client"]["tot_p99_us"] for r in pois_ok[("direct_safe", "get8", 3600)]])
    ax1.annotate(
        f"1.5M p50：{p50_s15:.1f} → {p50_d15:.1f} µs\n（-{(p50_s15 - p50_d15) / p50_s15 * 100:.0f}%）",
        xy=(1.5, p50_d15), xytext=(1.55, 1.9),
        arrowprops={"arrowstyle": "->", "color": "#1a7f37", "lw": 1.1},
        fontsize=8.7, fontweight="bold", color="#1a7f37",
    )
    ax1.annotate(
        f"3.6M p99：{p99_s36:.1f} → {p99_d36:.1f} µs\n（-{(p99_s36 - p99_d36) / p99_s36 * 100:.0f}%）",
        xy=(3.6, p99_d36), xytext=(1.88, 22.8),
        arrowprops={"arrowstyle": "->", "color": "#1a7f37", "lw": 1.1},
        fontsize=8.7, fontweight="bold", color="#1a7f37",
    )
    ax1.set_xlabel("开环泊松到达率（Mrps）", fontsize=10)
    ax1.set_ylabel("端到端延迟（µs，实线 p99 / 虚线 p50）", fontsize=10)
    ax1.set_xlim(0.05, 3.85)
    ax1.set_ylim(0, 28.5)
    ax1.set_title("(a) 8 B GET（Zipf s=0.99，全点 n=3 无损）", fontsize=10.8, fontweight="bold", pad=8)
    ax1.legend(loc="upper left", fontsize=8.3, frameon=True, framealpha=0.92)

    # ---------------- Panel B: Facebook ETC Mix Zipf Poisson ----------------
    rates_etc = sorted({rk for (p, wl, rk) in list(pois_ok.keys()) + list(pois_sat.keys()) if wl == "etc_mix"})
    max_n3_rate = {}
    for pol, label, color, mk in pols:
        xs3, p50_3, p99_3 = [], [], []
        xs2, p50_2, p99_2 = [], [], []
        for rk in rates_etc:
            rs = pois_ok[(pol, "etc_mix", rk)]
            if len(rs) == 3:
                xs3.append(rk / 1000.0)
                p50_3.append(med([r["client"]["tot_p50_us"] for r in rs]))
                p99_3.append(med([r["client"]["tot_p99_us"] for r in rs]))
            elif len(rs) == 2:
                xs2.append(rk / 1000.0)
                p50_2.append(med([r["client"]["tot_p50_us"] for r in rs]))
                p99_2.append(med([r["client"]["tot_p99_us"] for r in rs]))
        max_n3_rate[pol] = max(xs3)
        # Solid line for verified n=3 lossless region
        ax2.plot(xs3, p99_3, marker=mk, color=color, linewidth=2.2, markersize=5.5, label=f"{label} p99 (n=3)")
        ax2.plot(xs3, p50_3, marker=mk, color=color, linewidth=1.3, markersize=4.0, linestyle="--", alpha=0.8)
        if xs2:
            ax2.plot([xs3[-1]] + xs2, [p99_3[-1]] + p99_2, color=color, linewidth=1.6, linestyle="-.", alpha=0.85)
            ax2.plot(xs2, p99_2, marker=mk, linestyle="none", color=color, markerfacecolor="#bfdbfe", markersize=6.0)
            ax2.plot([xs3[-1]] + xs2, [p50_3[-1]] + p50_2, color=color, linewidth=1.1, linestyle=":", alpha=0.75)
            ax2.plot(xs2, p50_2, marker=mk, linestyle="none", color=color, markerfacecolor="#bfdbfe", markersize=4.5)
            ax2.annotate(
                "窥探版 n=2*",
                xy=(xs2[2], p99_2[2] - 0.5), xytext=(2.55, 16.2),
                ha="center", va="center", fontsize=7.8, color="#1d4ed8", fontweight="bold",
                arrowprops={"arrowstyle": "->", "color": "#1d4ed8", "lw": 0.9},
            )
        # Plot first saturating knee rate from attempts.jsonl (hollow marker)
        last_ok_x = xs2[-1] if xs2 else xs3[-1]
        last_ok_p99 = p99_2[-1] if xs2 else p99_3[-1]
        sat_rates = sorted(rk for rk in rates_etc if rk / 1000.0 > last_ok_x and pois_sat[(pol, "etc_mix", rk)])
        if sat_rates:
            srk = sat_rates[0]
            s_atts = pois_sat[(pol, "etc_mix", srk)]
            sp99 = med([a["client"]["tot_p99_us"] for a in s_atts])
            ax2.plot([last_ok_x, srk / 1000.0], [last_ok_p99, sp99], color=color, linewidth=1.3, linestyle=":", alpha=0.8)
            ax2.plot(
                srk / 1000.0, sp99, marker=mk, markersize=7.0, color=color,
                markerfacecolor="white", markeredgewidth=1.8, linestyle="none",
            )

    # Dynamic knee annotations computed strictly from runs.jsonl & attempts.jsonl
    sdp_max = max_n3_rate["sdp_fair"]
    dp_max = max_n3_rate["direct_safe"]
    knee_gain_pct = (dp_max - sdp_max) / sdp_max * 100.0
    sdp_next_rk = min(rk for rk in rates_etc if rk / 1000.0 > sdp_max)
    sdp_next_fails = len({a["rep"] for a in pois_sat[("sdp_fair", "etc_mix", sdp_next_rk)]})
    dp_next_rk = min(rk for rk in rates_etc if rk / 1000.0 > dp_max)
    dp_next_fails = len({a["rep"] for a in pois_sat[("direct_safe", "etc_mix", dp_next_rk)]})

    sdp_last_p99 = med([r["client"]["tot_p99_us"] for r in pois_ok[("sdp_fair", "etc_mix", int(round(sdp_max * 1000)))]])
    dp_last_p99 = med([r["client"]["tot_p99_us"] for r in pois_ok[("direct_safe", "etc_mix", int(round(dp_max * 1000)))]])
    p50_es = med([r["client"]["tot_p50_us"] for r in pois_ok[("sdp_fair", "etc_mix", 1400)]])
    p50_ed = med([r["client"]["tot_p50_us"] for r in pois_ok[("direct_safe", "etc_mix", 1400)]])
    sm99_es = med([r["client"]["small_p99_us"] for r in pois_ok[("sdp_fair", "etc_mix", 1400)]])
    sm99_ed = med([r["client"]["small_p99_us"] for r in pois_ok[("direct_safe", "etc_mix", 1400)]])

    ax2.axvline(sdp_max, color="#64748b", linestyle=":", linewidth=1.1)
    ax2.axvline(dp_max, color="#1a7f37", linestyle=":", linewidth=1.1)
    ax2.annotate(
        f"SDP 3/3 无损上限 {sdp_max:.2f}M\n（{sdp_next_rk/1000:.2f}M 起 {sdp_next_fails}/3 轮丢包，空心点）",
        xy=(sdp_max, sdp_last_p99), xytext=(0.96, 33.5),
        arrowprops={"arrowstyle": "->", "color": "#475569", "lw": 1.1},
        fontsize=8.0, color="#475569", fontweight="bold",
    )
    ax2.annotate(
        f"DP 安全直取 3/3 稳过 {dp_max:.2f}M\n（无损拐点 +{knee_gain_pct:.1f}%；{dp_next_rk/1000:.2f}M {dp_next_fails}/3 轮窗满）",
        xy=(dp_max, dp_last_p99), xytext=(1.42, 46.5),
        arrowprops={"arrowstyle": "->", "color": "#1a7f37", "lw": 1.1},
        fontsize=8.1, color="#1a7f37", fontweight="bold",
    )
    ax2.annotate(
        f"1.4M：p50 {p50_es:.1f}→{p50_ed:.1f} µs (-{(p50_es-p50_ed)/p50_es*100:.0f}%)\n小请求 p99 {sm99_es:.1f}→{sm99_ed:.1f} µs (-{(sm99_es-sm99_ed)/sm99_es*100:.0f}%)",
        xy=(1.40, p50_ed), xytext=(0.30, 1.2),
        arrowprops={"arrowstyle": "->", "color": "#1a7f37", "lw": 1.1},
        fontsize=8.1, color="#1a7f37", fontweight="bold",
    )
    # Legend proxy for hollow knee marker
    ax2.plot([], [], marker="o", linestyle=":", color="#334155", markerfacecolor="white",
             markeredgewidth=1.6, markersize=6.5, label="空心点：attempts 饱和拐点（丢包/窗满）")

    ax2.set_xlabel("开环泊松到达率（Mrps）", fontsize=10)
    ax2.set_ylabel("端到端延迟（µs，实线 p99 / 虚线 p50）", fontsize=10)
    ax2.set_xlim(0.05, 3.18)
    ax2.set_ylim(0, 62.0)
    ax2.set_title("(b) Facebook ETC 混合负载（40% 8B + 40% 128B 95/5 + 20% 3.8KiB）", fontsize=10.3, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", fontsize=7.9, frameon=True, framealpha=0.92)

    # Dynamically pull direct_peek etc_mix 1.80M r=1..2 vs r=3 host_rtt_mean_us for footnote
    pk18_ok = pois_ok[("direct_peek", "etc_mix", 1800)]
    pk18_sat = pois_sat[("direct_peek", "etc_mix", 1800)]
    hrtt_ok = med([r["card"]["host_rtt_mean_us"] for r in pk18_ok])
    hrtt_r3 = pk18_sat[0]["card"]["host_rtt_mean_us"]
    p99_r3 = pk18_sat[0]["client"]["tot_p99_us"]
    p999_r3 = pk18_sat[0]["client"]["tot_p999_us"]
    wcap_r3 = pk18_sat[0]["client"]["window_cap_hits"]

    fig.suptitle(
        "MRV-206：开环泊松负载–延迟与拐点曲线（原版 Memcached 1.6.45，实心=无损验证点，空心=拐点饱和尝试）",
        fontsize=11.4, fontweight="bold", y=0.975,
    )
    fig.text(
        0.5, 0.008,
        f"*注：DirectPush（窥探版）ETC 混合第 3 轮在 1.80M 因共享宿主 cpu40 单次 ~{p999_r3/1000:.1f} ms 内核调度抢占"
        f"（卡测主机往返均值 {hrtt_ok:.2f}→{hrtt_r3:.2f} µs，0 丢包，p99={p99_r3:.2f} µs，wcap={wcap_r3:,}）触发早停，"
        "故 1.80–2.70M 为前 2 轮中位数（n=2）；安全直取版与 SDP 均含完整 n=3。",
        ha="center", va="bottom", fontsize=7.6, color="#475569",
    )
    fig.tight_layout(rect=[0.0, 0.048, 1.0, 0.925])
    png = mrvplot.HERE / "mrv-206-poisson-knee.png"
    svg = mrvplot.HERE / "mrv-206-poisson-knee.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg} (sdp_max={sdp_max:.2f}M, dp_max={dp_max:.2f}M, gain=+{knee_gain_pct:.1f}%)")


if __name__ == "__main__":
    main()
