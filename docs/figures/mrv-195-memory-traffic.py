"""MRV-195 Figure 3: Bidirectional PCIe data movement (inbound DMA/snoop + host
outbound MMIO/MOVDIR64B writes on pure network runs without app-work) and real
host DDR5 memory bandwidth (L3-hot vs 128 MiB pool spill).

Reads raw verified n=3 runs directly from `logs/mrv195-2026-11-25/runs.jsonl`.
All numbers shown in the figure are computed dynamically from the raw jsonl.
"""
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv195-2026-11-25/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def r_half_up(x: float) -> int:
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def f1(x: float) -> str:
    sign = "-" if x < 0 else ""
    v = math.floor(abs(x) * 10.0 + 0.5) / 10.0
    return f"{sign}{v:.1f}"


def assert_verified(r):
    assert r.get("verified") is True, f"unverified record: {r.get('verify_errors')}"
    if r.get("part") == "part0_idle_cal":
        return
    c, h, k = r["client"], r["host"], r["card"]
    assert c["lost"] == 0 and c["bad"] == 0 and c["torn"] == 0 and c["send_err"] == 0
    if r["drive_mode"] == "poisson":
        assert c.get("window_cap_hits", 0) == 0
    eth_d = c.get("merc_ethtool_delta") or {}
    for ek in ("rx_out_of_buffer", "rx_discards_phy", "rx_crc_errors_phy"):
        assert eth_d.get(ek, 0) == 0
    assert k["bad_wire"] == 0 and k["bad_host"] == 0 and k["torn_bar4"] == 0 and k["dma_failed"] == 0
    assert k["eth_imissed"] == 0 and k["eth_ierrors"] == 0 and k["eth_oerrors"] == 0 and k["eth_tx_drops"] == 0
    assert h["bad"] == 0 and h["torn"] == 0 and h["kv_misses"] == 0 and h["tx_drops"] == 0
    assert h["page_faults"] == 0
    assert h["pmu"]["min_running_pct"] >= 99.5


def load_data():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    aw0 = defaultdict(list)
    sat = defaultdict(list)
    p128 = defaultdict(list)
    for r in rows:
        assert_verified(r)
        p = r["part"]
        if p == "part3a_matched_aw0":
            assert r["app_work"] is False
            aw0[(r["policy"], r["workload"], r["key_dist"])].append(r)
        elif p == "part1_sat":
            assert r["app_work"] is False
            sat[(r["policy"], r["workload"], r["key_dist"])].append(r)
        elif p == "part3b_pool128m":
            assert r["app_work"] is False
            p128[(r["workload"], r["key_dist"])].append(r)
    return aw0, sat, p128


def pcie_bytes(rs):
    wr = med([r["host"]["pmu"]["iio1_inb_wr"] * 64.0 / r["host"]["rx_rpcs"] for r in rs])
    rd = med([r["host"]["pmu"]["iio1_inb_rd"] * 64.0 / r["host"]["rx_rpcs"] for r in rs])
    cw = med([r["host"]["pmu"]["iio1_cpu_wr"] * 64.0 / r["host"]["rx_rpcs"] for r in rs])
    return wr, rd, cw

def main():
    aw0, sat, p128 = load_data()

    n_reps = len(aw0[("direct_safe", "get64", "zipf")])
    hot_pool_mib = int(round(med([r["tx_pool_slots"] for r in sat[("direct_safe", "get1024", "zipf")]]) * 4096 / (1024 * 1024)))
    big_pool_mib = int(round(med([r["tx_pool_slots"] for r in p128[("get1024", "zipf")]]) * 4096 / (1024 * 1024)))

    wls_a_spec = [
        ("get64", "zipf", "64 B 读"),
        ("get256", "zipf", "256 B 读"),
        ("mix", "zipf", "混合大小值"),
        ("get1024", "zipf", "1 KiB 读"),
    ]
    wls_a = []
    for wl, kd, base in wls_a_spec:
        rk_m = med([r["rate_krps"] for r in aw0[("direct_safe", wl, kd)]]) / 1000.0
        r_txt = f"{rk_m:.0f}M" if abs(rk_m - round(rk_m)) < 0.05 else f"{rk_m:.1f}M"
        wls_a.append((wl, kd, f"{base}\n同速 @{r_txt}"))

    w4_s, r4_s, c4_s = pcie_bytes(aw0[("sdp_fair", "get4096", "zipf")])
    w4_d, r4_d, c4_d = pcie_bytes(aw0[("direct_safe", "get4096", "zipf")])
    inb4_s, inb4_d = w4_s + r4_s, w4_d + r4_d
    bi4_s, bi4_d = inb4_s + c4_s, inb4_d + c4_d
    inb4_diff_pct = (inb4_d - inb4_s) / inb4_s * 100.0
    bi4_diff_pct = (bi4_d - bi4_s) / bi4_s * 100.0

    small_inb_cuts = []
    small_bi_cuts = []
    for wl in ("get64", "get256"):
        ws, rs_, cs = pcie_bytes(aw0[("sdp_fair", wl, "zipf")])
        wd, rd_, cd = pcie_bytes(aw0[("direct_safe", wl, "zipf")])
        small_inb_cuts.append((ws + rs_ - (wd + rd_)) / (ws + rs_) * 100.0)
        small_bi_cuts.append((ws + rs_ + cs - (wd + rd_ + cd)) / (ws + rs_ + cs) * 100.0)
    wls_b = [
        ("get1024", "zipf", "1 KiB 读（热点）"),
        ("get4096", "zipf", "4 KiB 读（热点）"),
        ("mix", "zipf", "混合大小值（热点）"),
    ]
    dram_hot_vals = []
    dram_big_vals = []
    dram_sdp_vals = []
    tp_drops = []
    bw_ratios = []
    for wl, kd, _ in wls_b:
        assert len(sat[("direct_safe", wl, kd)]) == n_reps
        assert len(sat[("sdp_fair", wl, kd)]) == n_reps
        assert len(p128[(wl, kd)]) == n_reps
        g_sdp = med([r["host"]["pmu"]["imc_gbps"] for r in sat[("sdp_fair", wl, kd)]])
        g_hot = med([r["host"]["pmu"]["imc_gbps"] for r in sat[("direct_safe", wl, kd)]])
        g_big = med([r["host"]["pmu"]["imc_gbps"] for r in p128[(wl, kd)]])
        dram_sdp_vals.append(g_sdp)
        dram_hot_vals.append(g_hot)
        dram_big_vals.append(g_big)
        bw_ratios.append(g_big / g_hot)
        tp_h = med([r["client"]["achieved_mrps"] for r in sat[("direct_safe", wl, kd)]])
        tp_b = med([r["client"]["achieved_mrps"] for r in p128[(wl, kd)]])
        tp_drops.append((tp_b - tp_h) / tp_h * 100.0)

    hot_all_lo = min(dram_sdp_vals + dram_hot_vals)
    hot_all_hi = max(dram_sdp_vals + dram_hot_vals)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.8, 6.4))

    # ==== Panel A: Bidirectional PCIe bytes/req (inbound DMA write/read + host outbound write) ====
    ax1.grid(axis="y", visible=False)
    ax1.grid(axis="x", alpha=0.28)
    y_a = np.arange(len(wls_a))
    bh = 0.33

    sdp_wr, sdp_rd, sdp_cw = [], [], []
    ds_wr, ds_rd, ds_cw = [], [], []
    for wl, kd, _ in wls_a:
        assert len(aw0[("sdp_fair", wl, kd)]) == n_reps
        assert len(aw0[("direct_safe", wl, kd)]) == n_reps
        ws, rs_, cs = pcie_bytes(aw0[("sdp_fair", wl, kd)])
        wd, rd_, cd = pcie_bytes(aw0[("direct_safe", wl, kd)])
        sdp_wr.append(ws)
        sdp_rd.append(rs_)
        sdp_cw.append(cs)
        ds_wr.append(wd)
        ds_rd.append(rd_)
        ds_cw.append(cd)

    sdp_inb = [w + r for w, r in zip(sdp_wr, sdp_rd)]
    ds_inb = [w + r for w, r in zip(ds_wr, ds_rd)]
    y_sdp = y_a - bh / 2 - 0.025
    y_ds = y_a + bh / 2 + 0.025

    ax1.barh(y_sdp, sdp_wr, height=bh, color="#94a3b8", label="SDP：网卡入向写主机", zorder=3)
    ax1.barh(y_sdp, sdp_rd, left=sdp_wr, height=bh, color="#475569", label="SDP：网卡入向回读主机", zorder=3)
    ax1.barh(y_sdp, sdp_cw, left=sdp_inb, height=bh, color="#f59e0b", edgecolor="white", linewidth=0.6, zorder=3)

    ax1.barh(y_ds, ds_wr, height=bh, color="#86efac", label="DirectPush：网卡入向写主机", zorder=3)
    ax1.barh(y_ds, ds_rd, left=ds_wr, height=bh, color="#1a7f37", label="DirectPush：网卡入向回读主机", zorder=3)
    ax1.barh(
        y_ds, ds_cw, left=ds_inb, height=bh, color="#f59e0b", edgecolor="white", linewidth=0.6,
        label="主机出向写网卡（直推 / 门铃）", zorder=3,
    )

    max_xa = max(inb + cw for inb, cw in zip(sdp_inb, sdp_cw))
    for i in range(len(wls_a)):
        inb_s = sdp_inb[i]
        inb_d = ds_inb[i]
        bi_s = inb_s + sdp_cw[i]
        bi_d = inb_d + ds_cw[i]
        bi_diff = (bi_d - bi_s) / bi_s * 100.0
        inb_diff = (inb_d - inb_s) / inb_s * 100.0
        ax1.text(
            bi_s + 24, y_sdp[i],
            f"{r_half_up(bi_s)} B（入向 {r_half_up(inb_s)}）",
            va="center", ha="left", fontsize=8.8, color="#475569",
        )
        if ds_rd[i] < 1.0:
            ds_lbl = f"{r_half_up(bi_d)} B ({f1(bi_diff)}%)\n入向 {r_half_up(inb_d)} B ({r_half_up(inb_diff)}%，零回读)"
        else:
            ds_lbl = f"{r_half_up(bi_d)} B ({f1(bi_diff)}%)\n入向 {r_half_up(inb_d)} B ({r_half_up(inb_diff)}%)"
        ax1.text(
            bi_d + 24, y_ds[i] + 0.02,
            ds_lbl,
            va="center", ha="left", fontsize=8.8, fontweight="bold", color="#116329", linespacing=1.18,
        )

    ax1.set_xlim(0, max_xa * 1.56)
    ax1.set_yticks(y_a)
    ax1.set_yticklabels([w[2] for w in wls_a], fontsize=10.2, fontweight="bold")
    ax1.invert_yaxis()
    ax1.set_xlabel("双向 PCIe 跨线流量（入向 DMA + 主机出向写，字节/请求，越短越好）", fontsize=10.0, labelpad=8)
    ax1.set_title(
        f"A. 双向 PCIe 流量：小请求双向降 {f1(min(small_bi_cuts))}%–{f1(max(small_bi_cuts))}%（入向监听降 {r_half_up(min(small_inb_cuts))}%–{r_half_up(max(small_inb_cuts))}%）",
        fontsize=10.6, fontweight="bold", pad=12,
    )
    ax1.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#cbd5e1", framealpha=0.96, fontsize=8.6)
    # ==== Panel B: Real host DDR5 bandwidth (L3-hot vs 128 MiB pool spill) ====
    ax2.grid(axis="y", visible=False)
    ax2.grid(axis="x", alpha=0.28)
    y_b = np.arange(len(wls_b))
    bh2 = 0.24

    ax2.barh(y_b - bh2, dram_sdp_vals, height=bh2 * 0.90, color="#64748b", label="最优异步 SDP（紧致热池）", zorder=3)
    ax2.barh(y_b, dram_hot_vals, height=bh2 * 0.90, color="#1a7f37", label=f"DirectPush（{hot_pool_mib} MiB 热池，命中缓存）", zorder=3)
    ax2.barh(y_b + bh2, dram_big_vals, height=bh2 * 0.90, color="#dc2626", alpha=0.88, label=f"DirectPush（{big_pool_mib} MiB 大池，溢出缓存）", zorder=3)

    max_xb = max(dram_big_vals)
    for i in range(len(wls_b)):
        lo_g = min(dram_sdp_vals[i], dram_hot_vals[i])
        hi_g = max(dram_sdp_vals[i], dram_hot_vals[i])
        ax2.text(
            hi_g + 0.30, y_b[i] - bh2 * 0.45,
            f"均仅 {lo_g:.2f}–{hi_g:.2f} GB/s（缓存全命中）",
            va="center", ha="left", fontsize=9.1, fontweight="bold", color="#116329",
        )
        b_req = med([r["host"]["pmu"]["imc_bytes_per_req"] for r in p128[(wls_b[i][0], wls_b[i][1])]])
        ax2.text(
            dram_big_vals[i] + 0.30, y_b[i] + bh2,
            f"{f1(dram_big_vals[i])} GB/s（{r_half_up(b_req)} B/请求，吞吐 {r_half_up(tp_drops[i])}%）",
            va="center", ha="left", fontsize=9.4, fontweight="bold", color="#b91c1c",
        )

    ax2.set_xlim(0, max_xb * 1.92)
    ax2.set_ylim(3.45, -0.55)
    ax2.set_yticks(y_b)
    ax2.set_yticklabels([w[2] for w in wls_b], fontsize=10.3, fontweight="bold")
    ax2.set_xlabel("饱和态主机 DDR5 内存条实测带宽（GB/s，越短越好）", fontsize=10.4, labelpad=8)
    ax2.set_title(
        f"B. DDR5 主存带宽：热池近零，大池溢出暴涨 {r_half_up(min(bw_ratios))}–{r_half_up(max(bw_ratios))}×",
        fontsize=11.2, fontweight="bold", pad=12,
    )
    ax2.legend(
        loc="lower center", bbox_to_anchor=(0.5, 0.01), ncol=1,
        frameon=True, facecolor="white", edgecolor="#cbd5e1", framealpha=0.97, fontsize=8.8,
    )

    fig.suptitle(
        f"MRV-195 双向 PCIe 流量与主存带宽：小请求双向降 {f1(min(small_bi_cuts))}%–{f1(max(small_bi_cuts))}%（入向降 {r_half_up(min(small_inb_cuts))}%–{r_half_up(max(small_inb_cuts))}%），大池溢出主存暴涨 {r_half_up(min(bw_ratios))}–{r_half_up(max(bw_ratios))}×",
        fontsize=12.1, fontweight="bold", y=0.97,
    )
    fig.text(
        0.5, 0.015,
        f"全部取纯网络无背景计算轮次（{n_reps} 轮交错中位数）。左图补齐主机出向写，区分双向总跨线与入向 DMA/缓存监听（64/256 B 消除 100% 非投递回读往返；4 KiB 读双向 {r_half_up(bi4_d)} vs {r_half_up(bi4_s)} B，{f1(bi4_diff_pct)}%，入向 {f1(inb4_diff_pct)}%）；\n"
        f"右图实测 DDR5 带宽：热池驻留缓存时两路均仅 {hot_all_lo:.2f}–{hot_all_hi:.2f} GB/s；DirectPush 大值发送池扩至 {big_pool_mib} MiB 溢出缓存后暴涨 {r_half_up(min(bw_ratios))}–{r_half_up(max(bw_ratios))}×、吞吐跌 {r_half_up(abs(max(tp_drops)))}%–{r_half_up(abs(min(tp_drops)))}%（≤ 256 B 零发送池不受影响）。",
        ha="center", va="bottom", fontsize=8.4, color="#475569",
    )
    fig.tight_layout(rect=[0.0, 0.075, 1.0, 0.925])
    png = mrvplot.HERE / "mrv-195-memory-traffic.png"
    svg = mrvplot.HERE / "mrv-195-memory-traffic.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(f"Saved {png} and {svg}")


if __name__ == "__main__":
    main()
