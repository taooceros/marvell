"""MRV-206 Figure 3: Bidirectional PCIe traffic per request (including host->card
iio1_cpu_wr, card->host iio1_inb_wr, and card->host iio1_inb_rd) and Host DRAM
(IMC) traffic per request across real upstream Memcached 1.6.45 workloads.

Reads raw verified n=3 runs directly from `logs/mrv206-2026-11-26/runs.jsonl`.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import mrvplot

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "logs/mrv206-2026-11-26/runs.jsonl"


def med(xs):
    return statistics.median(xs)


def assert_verified(r):
    assert r.get("verified") is True and not r.get("errors")
    h = r["host"]
    assert h["page_faults"] == 0 and h["pmu"]["page_faults"] == 0 and h["pmu"]["min_running_pct"] >= 99.5


def pcie_breakdown_bytes(r):
    """Exact bidirectional PCIe payload+descriptor bytes/req from Sapphire Rapids IIO1 PMU.
    iio1_inb_wr (0x84 umask=1) and iio1_inb_rd (0x84 umask=4) count 64 B cache-line requests;
    iio1_cpu_wr (0xc1 umask=1) counts 64 B MOVDIR64B writes on DirectPush and 8 B MMIO doorbells on SDP.
    """
    p = r["host"]["pmu"]
    tx = r["host"]["tx_rpcs"]
    cwr_b = (p["iio1_cpu_wr"] / tx) * (64.0 if r["policy"].startswith("direct") else 8.0)
    iwr_b = (p["iio1_inb_wr"] / tx) * 64.0
    ird_b = (p["iio1_inb_rd"] / tx) * 64.0
    return cwr_b, iwr_b, ird_b


def main():
    rows = [json.loads(ln) for ln in RUNS_PATH.read_text().splitlines() if ln.strip()]
    by_id = defaultdict(list)
    for r in rows:
        assert_verified(r)
        if r["section"] == "sat":
            by_id[r["run_id"]].append(r)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.6, 5.1))

    # ---------------- Panel A: Bidirectional PCIe Bytes/Req ----------------
    p_wls = [
        ("get8_zipf", "8 B 读\n(热点)"),
        ("small_mix_zipf", "小值混合\n(热点)"),
        ("etc_mix_zipf", "ETC 混合\n(热点)"),
        ("get3840_zipf", "3.8 KiB 读\n(热点)"),
    ]
    x = np.arange(len(p_wls))
    w = 0.35

    for pi, (pol, short, offset) in enumerate([("sdp_fair", "SDP", -w / 2), ("direct_safe", "DP", w / 2)]):
        cwr_v, iwr_v, ird_v = [], [], []
        for wkey, _ in p_wls:
            rs = by_id[f"sat_{pol}_{wkey}_w64"]
            assert len(rs) == 3
            parts = [pcie_breakdown_bytes(r) for r in rs]
            cwr_v.append(med([t[0] for t in parts]))
            iwr_v.append(med([t[1] for t in parts]))
            ird_v.append(med([t[2] for t in parts]))
        cwr_v = np.array(cwr_v)
        iwr_v = np.array(iwr_v)
        ird_v = np.array(ird_v)
        tot = cwr_v + iwr_v + ird_v
        hatch = "//" if pol == "sdp_fair" else ""
        ax1.bar(x + offset, cwr_v, width=w, color="#1a7f37", hatch=hatch, edgecolor="white",
                label="主机→卡写 (iio1_cpu_wr)" if pi == 1 else None)
        ax1.bar(x + offset, iwr_v, bottom=cwr_v, width=w, color="#2563eb", hatch=hatch, edgecolor="white",
                label="卡→主机写 (iio1_inb_wr)" if pi == 1 else None)
        ax1.bar(x + offset, ird_v, bottom=cwr_v + iwr_v, width=w, color="#f59e0b", hatch=hatch, edgecolor="white",
                label="卡→主机读 (iio1_inb_rd)" if pi == 1 else None)
        for i in range(len(p_wls)):
            lbl = f"{short}\n{tot[i]:.0f}B"
            ax1.text(x[i] + offset, tot[i] + 65, lbl, ha="center", va="bottom", fontsize=7.6,
                     fontweight="bold" if pol == "direct_safe" else "normal",
                     color="#1a7f37" if pol == "direct_safe" else "#475569")

    for i, wkey in enumerate(["get8_zipf", "small_mix_zipf", "etc_mix_zipf", "get3840_zipf"]):
        ts = sum(med([pcie_breakdown_bytes(r)[k] for r in by_id[f"sat_sdp_fair_{wkey}_w64"]]) for k in range(3))
        td = sum(med([pcie_breakdown_bytes(r)[k] for r in by_id[f"sat_direct_safe_{wkey}_w64"]]) for k in range(3))
        pct = (td - ts) / ts * 100.0
        ax1.text(x[i], max(ts, td) + 530, f"{pct:+.1f}%",
                 ha="center", va="bottom", fontsize=9.0, fontweight="bold", color="#1a7f37")

    ax1.set_xticks(x)
    ax1.set_xticklabels([lb for _, lb in p_wls], fontsize=9.0)
    ax1.set_ylabel("双向 PCIe 总流量（B/req，斜线=SDP / 实心=DirectPush）", fontsize=9.5)
    ax1.set_ylim(0, 5480)
    ax1.set_title("(a) 双向 PCIe 每请求字节拆解（含 iio1_cpu_wr）", fontsize=10.8, fontweight="bold", pad=8)
    ax1.legend(loc="upper left", fontsize=8.4, frameon=True, framealpha=0.92)

    # ---------------- Panel B: Host DRAM (IMC) Traffic Bytes/Req ----------------
    m_wls = [
        ("get8_zipf", "8 B 读\n(热点)"),
        ("etc_mix_zipf", "ETC 混合\n(热点)"),
        ("etc_mix_uniform", "ETC 混合\n(均匀)"),
        ("get3840_zipf", "3.8 KiB 读\n(热点)"),
        ("set3840_zipf", "3.8 KiB 写\n(热点)"),
    ]
    mx = np.arange(len(m_wls))
    mw = 0.26
    pols = [
        ("sdp_fair", "SDP fair-full (4Q×512)", "#64748b"),
        ("direct_peek", "DirectPush (窥探版)", "#2563eb"),
        ("direct_safe", "DirectPush (安全直取版)", "#1a7f37"),
    ]
    imc_sdp, imc_safe = [], []
    for pi, (pol, label, color) in enumerate(pols):
        vals = [med([r["host"]["pmu"]["imc_bytes_per_req"] for r in by_id[f"sat_{pol}_{wk}_w64"]]) for wk, _ in m_wls]
        if pol == "sdp_fair":
            imc_sdp = vals
        elif pol == "direct_safe":
            imc_safe = vals
        ax2.bar(mx + (pi - 1) * mw, vals, width=mw, color=color, edgecolor="white", label=label)

    for i, (vs, vd) in enumerate(zip(imc_sdp, imc_safe)):
        ax2.text(mx[i] - mw, vs + 14, f"{vs:.0f}", ha="center", va="bottom", fontsize=7.8, color="#475569")
        ax2.text(mx[i] + mw, vd + 14, f"{vd:.0f}", ha="center", va="bottom", fontsize=8.0, fontweight="bold", color="#1a7f37")
        pct = (vd - vs) / vs * 100.0
        if vs > 50:
            ratio = vs / max(vd, 1e-6)
            txt = f"{pct:.0f}%\n({ratio:.1f}× 省)" if ratio >= 2.0 else f"{pct:.0f}%"
            ax2.text(mx[i] + 0.08, max(vs, vd) + 52, txt, ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#1a7f37")

    ax2.set_xticks(mx)
    ax2.set_xticklabels([lb for _, lb in m_wls], fontsize=9.0)
    ax2.set_ylabel("主机内存控制器 DRAM 流量（IMC B/req）", fontsize=10)
    ax2.set_ylim(0, 860)
    ax2.set_title("(b) 主机 DRAM（IMC）每请求带宽消耗", fontsize=10.8, fontweight="bold", pad=8)
    ax2.legend(loc="upper left", fontsize=8.4, frameon=True, framealpha=0.92)

    # Dynamically pull get1024 padding DRAM read numbers for footnote
    rs_1024_sdp = by_id["sat_sdp_fair_get1024_zipf_w64"]
    rs_1024_dp = by_id["sat_direct_safe_get1024_zipf_w64"]
    imc_1024_sdp = med([r["host"]["pmu"]["imc_bytes_per_req"] for r in rs_1024_sdp])
    imc_1024_dp = med([r["host"]["pmu"]["imc_bytes_per_req"] for r in rs_1024_dp])
    rd_1024_sdp = med([r["host"]["pmu"]["imc_cas_rd"] / r["host"]["tx_rpcs"] for r in rs_1024_sdp])
    rd_1024_dp = med([r["host"]["pmu"]["imc_cas_rd"] / r["host"]["tx_rpcs"] for r in rs_1024_dp])
    ird_1024_lines = med([r["host"]["pmu"]["iio1_inb_rd"] / r["host"]["tx_rpcs"] for r in rs_1024_dp])
    # Reply = 8B UDP hdr + 24B binary hdr + 4B flags + 1024B value = 1060 B -> head+reply touches lines 0..16 (17 lines) + tail line 31 (1 line)
    mc_reply_b = 8 + 24 + 4 + 1024
    touched_head_lines = (8 + mc_reply_b + 63) // 64
    untouched_pad_lines = int(round(ird_1024_lines)) - touched_head_lines - 1
    imc_diff_b = imc_1024_sdp - imc_1024_dp

    fig.suptitle(
        "MRV-206：100% 非复用主机 IIO1 与 IMC 硬件计数（双向 PCIe 与 DRAM 流量，原版 Memcached 1.6.45，n=3 中位数）",
        fontsize=11.4, fontweight="bold", y=0.975,
    )
    fig.text(
        0.5, 0.008,
        f"注：未绘出的 1 KiB 读（get1024）双路同现高 IMC 流量（SDP {imc_1024_sdp:.0f} B / DP {imc_1024_dp:.0f} B，其中 IMC 读占 {rd_1024_sdp:.1f} / {rd_1024_dp:.1f} 行 = {rd_1024_sdp*64:.0f} / {rd_1024_dp*64:.0f} B），\n"
        f"系 wire_total_bytes 将 {mc_reply_b} B 回包扩至 {ird_1024_lines*64:.0f} B（{ird_1024_lines:.0f} 行）后，中间 {untouched_pad_lines} 条未触碰填充行（{untouched_pad_lines*64} B）被网卡非分配式 PCIe RdCurr 直读主存所致（双路同源陪读，叠加常规读写后仅差 ~{imc_diff_b:.0f} B）。",
        ha="center", va="bottom", fontsize=7.5, color="#475569",
    )
    fig.tight_layout(rect=[0.0, 0.068, 1.0, 0.925])
    png = mrvplot.HERE / "mrv-206-pcie-imc.png"
    svg = mrvplot.HERE / "mrv-206-pcie-imc.svg"
    fig.savefig(png)
    fig.savefig(svg)
    plt.close(fig)
    print(
        f"Saved {png} and {svg} (get1024 IMC: sdp={imc_1024_sdp:.1f}B [rd={rd_1024_sdp:.2f}L], "
        f"dp={imc_1024_dp:.1f}B [rd={rd_1024_dp:.2f}L], ird={ird_1024_lines:.2f}L, pad={untouched_pad_lines}L={untouched_pad_lines*64}B)"
    )


if __name__ == "__main__":
    main()
