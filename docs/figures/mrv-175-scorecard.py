"""MRV-175 Master Scorecard: four-in-one measured summary directly from logs/mrv*/runs*.jsonl.

Panels:
  A: 64 B W=1 RTT ladder (same-card host-NIC MRV-166/97 + real dual-host 100GbE MRV-133/161)
  B: 1-host-core real 100GbE unidirectional throughput (TX MRV-155 Phase 2 + RX MRV-172)
  C: Water-free fair host CPU cycles/pkt ablation & comparison (MRV-166, B=1, exact per-rep cal)
  D: Active-NIC burst buffering host IMC vs card DDR5 traffic duality (MRV-158, 64 KiB -> 128 MiB)

Run:
  uv run --with matplotlib python docs/figures/mrv-175-scorecard.py
"""
import collections
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mrvplot import save

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"

TEAL = "#0F766E"
TEAL_LIGHT = "#5EEAD4"
TEAL_PALE = "#CCFBF1"
SLATE_DARK = "#334155"
SLATE_MID = "#64748B"
AMBER = "#B45309"


def load_jsonl(rel: str):
    return [json.loads(line) for line in (LOGS / rel).read_text().splitlines() if line.strip()]


def med(xs):
    return statistics.median(xs)


def group_by(rows, key_fn):
    g = collections.defaultdict(list)
    for r in rows:
        g[key_fn(r)].append(r)
    return g


# ---------------------------------------------------------------------------
# 1. Load & verify raw data for all 4 panels (assert integrity = 0 everywhere)
# ---------------------------------------------------------------------------

# Panel A: 64 B W=1 latency (MRV-166 same-card, MRV-133 & MRV-161 dual-host 100GbE)
r166 = load_jsonl("mrv166-2026-11-23/runs.jsonl")
for r in r166:
    assert r.get("verified", True)
    assert r.get("lost", 0) == 0 and r.get("bad", 0) == 0 and r.get("torn", 0) == 0
g166 = group_by([r for r in r166 if "cfg" in r], lambda r: r["cfg"])
idle166_map = {r["rep"]: r["units_per_s"] for r in r166 if r.get("part") == "part0_cal"}

lat_same_sdp = med([x["lat_all_us"]["p50"] for x in g166["sdp_fair_64_w1"]])
lat_same_dp = med([x["lat_all_us"]["p50"] for x in g166["directpush_64_w1"]])

r133 = load_jsonl("mrv133-2026-11-21/runs.jsonl")
g133 = group_by([r for r in r133 if r.get("verified") and r.get("part") == "part1_decomp"], lambda r: r["cfg"])
lat_e2e_sdp = med([x["client"]["tot_p50_us"] for x in g133["sdp_64_w1"]])
lat_e2e_dp133 = med([x["client"]["tot_p50_us"] for x in g133["direct_64_w1"]])

r161 = load_jsonl("mrv161-2026-11-24/runs.jsonl")
for r in r161:
    assert r["verified"]
g161 = group_by(r161, lambda r: r["cfg"])
lat_e2e_peek = med([x["client"]["tot_p50_us"] for x in g161["direct_cqe_peek_64_w1"]])
lat_card_peek = med([x["card"]["host_rtt_p50_us"] for x in g161["direct_cqe_peek_64_w1"]])
lat_e2e_direct = med([x["client"]["tot_p50_us"] for x in g161["direct_cqe_direct_64_w1"]])

# Panel B: 1-host-core 100GbE TX (MRV-155 Phase 2) + RX (MRV-172)
r155 = load_jsonl("mrv155-2026-11-23/runs_phase2.jsonl")
g155 = group_by(r155, lambda r: r["cfg"])


def tx_wire(cfg):
    rs = g155[cfg]
    for x in rs:
        assert x["card"]["bad"] == 0 and x["card"]["torn"] == 0 and x["mercury"]["bad"] == 0
    return med([x["mercury"]["phy"]["wire_gbps"] for x in rs])


r172 = load_jsonl("mrv172-2026-11-24/runs.jsonl")
g172 = group_by(r172, lambda r: r["cfg"])


def rx_wire(cfg):
    rs = g172[cfg]
    for x in rs:
        assert x["host"]["bad"] == 0 and x["host"]["torn"] == 0
    return med([x["rates"]["host_wire_gbps"] for x in rs])


def rx_ddio(cfg):
    return med([x["host_pmu"]["ddio_hit_pct"] for x in g172[cfg]])


panel_b_labels = [
    "TX 64B UDP\n(110B L2)",
    "TX 256B\nRoCEv2",
    "TX 4 KiB\n(DPI 读)",
    "RX 40B UD\n(110B,cpp=1)",
    "RX 72B UC\n(134B,cpp=2)",
    "RX 4 KiB\n(DPI 写)",
]
panel_b_dp = [
    tx_wire("s2_dp_raw110_c4"),
    tx_wire("s2_dp_roce256_c4"),
    tx_wire("s2_dp_roce4096_c4"),
    rx_wire("s2_dp_110B"),
    rx_wire("s2_dp_134B"),
    rx_wire("s2_dp_4096B"),
]
panel_b_sdp = [
    tx_wire("s2_sdp_raw110_ism1_c4"),
    tx_wire("s2_sdp_roce256_ism1_c4"),
    tx_wire("s2_sdp_roce4096_ism1_c4"),
    max(rx_wire("s2_sdp_ism1_110B"), rx_wire("s2_sdp_rpm1_110B")),
    max(rx_wire("s2_sdp_ism1_134B"), rx_wire("s2_sdp_rpm1_134B")),
    max(rx_wire("s2_sdp_ism1_4096B"), rx_wire("s2_sdp_rpm1_4096B")),
]
rx_4k_ddio_dp = rx_ddio("s2_dp_4096B")
rx_4k_ddio_ism = rx_ddio("s2_sdp_ism1_4096B")

# Panel C: MRV-166 fair CPU cycles/pkt (B=1, exact per-rep calibration matching MRV-166)
def cpu_cyc(cfg):
    rs = g166[cfg]
    return med([
        (1.0 - x["app_work_per_s"] / idle166_map[x["rep"]]) * x["host_pmu"]["cycles"] / x["measured"]
        for x in rs
    ])


c_64_sdp = cpu_cyc("sdp_fair_64_r1500")
c_64_dp = cpu_cyc("directpush_64_r1500")
c_1k_sdp = cpu_cyc("sdp_fair_1024_r1000")
c_1k_dp = cpu_cyc("directpush_1024_r1000")
c_4k_sync = cpu_cyc("sdp_sync_4096_r1000")
c_4k_sdp = cpu_cyc("sdp_fair_4096_r1000")
c_4k_dp = cpu_cyc("directpush_4096_r1000")

# Panel D: MRV-158 active-NIC burst buffering (64 KiB -> 128 MiB)
r158 = load_jsonl("mrv158-2026-11-23/runs.jsonl")
for r in r158:
    assert r.get("lost", 0) == 0 and r.get("bad", 0) == 0 and r.get("torn", 0) == 0
g158 = group_by([r for r in r158 if r["part"] == "part2_backlog"], lambda r: r["tag"])

burst_sizes = [
    ("64KiB", 0.0625),
    ("256KiB", 0.25),
    ("1MiB", 1.0),
    ("4MiB", 4.0),
    ("8MiB", 8.0),
    ("16MiB", 16.0),
    ("32MiB", 32.0),
    ("48MiB", 48.0),
    ("64MiB", 64.0),
    ("96MiB", 96.0),
    ("128MiB", 128.0),
]
x_mib = [s[1] for s in burst_sizes]
imc_a = [med([x["host_pmu"]["imc_rd_mib"] + x["host_pmu"]["imc_wr_mib"] for x in g158[f"scheme_a_128m_{lbl}"]]) for lbl, _ in burst_sizes]
imc_b = [med([x["host_pmu"]["imc_rd_mib"] + x["host_pmu"]["imc_wr_mib"] for x in g158[f"scheme_b_256k_{lbl}"]]) for lbl, _ in burst_sizes]
ddr_b = [med([(x["card_pmu"]["ddr_reads"] + x["card_pmu"]["ddr_writes"]) * 64.0 / 1048576.0 for x in g158[f"scheme_b_256k_{lbl}"]]) for lbl, _ in burst_sizes]

# ---------------------------------------------------------------------------
# 2. Plot 2x2 Master Scorecard
# ---------------------------------------------------------------------------
fig, axes = plt.subplots(2, 2, figsize=(15.4, 10.2))
fig.suptitle(
    "CC-NIC does not need Cache Coherence：商用 PCIe Gen5×4（CN106 + IOMMU 开 · 零 DSA）四大核心实测成绩单",
    fontsize=15.0,
    fontweight="bold",
    color="#0F172A",
    y=0.985,
)

# ==================== PANEL A: Latency ====================
ax = axes[0, 0]
labels_a = [
    "单机 64B · 公平 SDP\n(MRV-166)",
    "单机 64B · DirectPush\n(MRV-166/97)",
    "双机 100G · SDP\n(MRV-133)",
    "双机 100G · DP 基线\n(MRV-133)",
    "双机 100G · DP+CQE窥探\n(MRV-161 主方案)",
    "双机 100G · DP+CQE直取\n(MRV-161 探索臂)",
]
vals_a = [lat_same_sdp, lat_same_dp, lat_e2e_sdp, lat_e2e_dp133, lat_e2e_peek, lat_e2e_direct]
colors_a = [SLATE_MID, TEAL, SLATE_MID, "#475569", TEAL, TEAL_LIGHT]
y_pos = np.array([6.0, 5.0, 3.2, 2.2, 1.2, 0.2])

bars = ax.barh(y_pos, vals_a, height=0.66, color=colors_a, edgecolor="#0F172A", linewidth=0.6)
bars[-1].set_hatch("//")
bars[-1].set_edgecolor(TEAL)

ax.axhline(4.1, color="#CBD5E1", linestyle="-", linewidth=1.0)
ax.axvline(1.3, color=AMBER, linestyle="--", linewidth=1.4, alpha=0.9)
ax.text(
    1.52, 4.1,
    "真 CXL-NIC 文献参考线 ~1.3 µs（MICRO '25 Fig.14）",
    fontsize=8.6, color=AMBER, va="center", fontweight="bold",
    bbox=dict(boxstyle="round,pad=0.25", fc="#FFFBEB", ec=AMBER, lw=0.8),
)

for y, v, idx in zip(y_pos, vals_a, range(len(vals_a))):
    extra = ""
    if idx == 1:
        extra = f"  (快 {lat_same_sdp / lat_same_dp:.2f}×)"
    elif idx == 4:
        extra = f"  (主机增量 {lat_card_peek:.3f} µs)"
    elif idx == 5:
        extra = "  (待 MRV-170 验真)"
    ax.text(v + 0.16, y, f"{v:.3f} µs{extra}", va="center", fontsize=9.0,
            fontweight="bold" if idx in (1, 4) else "normal",
            color=TEAL if idx in (1, 4) else "#1E293B")

ax.set_yticks(y_pos)
ax.set_yticklabels(labels_a, fontsize=9.0)
ax.set_xlim(0, 12.8)
ax.set_ylim(-0.45, 6.7)
ax.set_xlabel("64 B W=1 空载往返延迟 p50（µs，越低越好）", fontsize=10)
ax.set_title("A. 延迟阶梯：单机 0.885 µs 胜 CXL-NIC，双机 100G 降至 5.285 µs", fontsize=11.0, fontweight="bold", loc="left", pad=8)
ax.grid(axis="x", alpha=0.35)
ax.grid(axis="y", visible=False)

# ==================== PANEL B: 1-Core 100GbE TX & RX ====================
ax = axes[0, 1]
x = np.arange(len(panel_b_labels))
w = 0.36
b1 = ax.bar(x - w / 2, panel_b_sdp, w, label="同卡最优 SDP（1 核主机）", color=SLATE_MID, edgecolor="#1E293B", linewidth=0.5)
b2 = ax.bar(x + w / 2, panel_b_dp, w, label="DirectPush（1 核主机）", color=TEAL, edgecolor="#0F172A", linewidth=0.5)

ax.axhline(100.0, color=AMBER, linestyle="--", linewidth=1.2, alpha=0.9)
ax.text(-0.46, 97.5, "↑ 100G", fontsize=8.4, color=AMBER, ha="left", va="top", fontweight="bold")
ax.axvline(2.5, color="#CBD5E1", linestyle=":", linewidth=1.5)
ax.text(1.0, 114.5, "单向发包 TX（MRV-155）", ha="center", fontsize=9.2, fontweight="bold", color="#334155")
ax.text(4.0, 114.5, "单向收包 RX（MRV-172）", ha="center", fontsize=9.2, fontweight="bold", color=TEAL)

for rect in b1:
    h = rect.get_height()
    ax.text(rect.get_x() + rect.get_width() / 2, h + 1.4, f"{h:.1f}", ha="center", va="bottom", fontsize=8.0, color="#475569")
for rect in b2:
    h = rect.get_height()
    ax.text(rect.get_x() + rect.get_width() / 2, h + 1.4, f"{h:.1f}", ha="center", va="bottom", fontsize=8.3, fontweight="bold", color=TEAL)

ax.annotate(
    f"RX 4 KiB 同满 100G：\nDP DDIO {rx_4k_ddio_dp:.0f}% vs SDP ISM2048 {rx_4k_ddio_ism:.1f}%",
    xy=(5 + w / 2, 100.1),
    xytext=(3.42, 76.5),
    fontsize=7.9,
    color="#0F172A",
    ha="center",
    bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=TEAL, lw=0.9, alpha=0.96),
    arrowprops=dict(arrowstyle="->", color=TEAL, lw=1.0),
)

ax.set_xticks(x)
ax.set_xticklabels(panel_b_labels, fontsize=8.4)
ax.set_ylim(0, 134)
ax.set_ylabel("等效 100GbE 物理线速（Gbps，n=3 中位数）", fontsize=10)
ax.set_title("B. 严格 1 颗主机核 100GbE 单向吞吐：小包近倍领先、RX 满 / TX 94–100G", fontsize=10.6, fontweight="bold", loc="left", pad=8)
ax.legend(loc="upper center", ncol=2, fontsize=8.7, frameon=True, facecolor="white", framealpha=0.96)
ax.grid(axis="y", alpha=0.35)
ax.grid(axis="x", visible=False)

# ==================== PANEL C: Fair Host CPU Cycles (MRV-166) ====================
ax = axes[1, 0]
x_c = np.arange(3)
w_c = 0.25

sdp_fair_vals = [c_64_sdp, c_1k_sdp, c_4k_sdp]
dp_vals = [c_64_dp, c_1k_dp, c_4k_dp]

ax.bar(x_c[2] - w_c * 1.15, c_4k_sync, w_c * 0.9, label="旧强对照 SDP sync-tail（含 54.8% 等尾水分）",
       color="#E2E8F0", edgecolor="#94A3B8", hatch="///", linewidth=0.9)
ax.bar(x_c - w_c * 0.1, sdp_fair_vals, w_c * 1.05, label="公平异步 SDP（fair-full，等尾=0%）",
       color=SLATE_MID, edgecolor="#1E293B", linewidth=0.6)
ax.bar(x_c + w_c * 1.0, dp_vals, w_c * 1.05, label="DirectPush（小包直推 / 大包 DPI DMA）",
       color=TEAL, edgecolor="#0F172A", linewidth=0.6)

ax.text(x_c[2] - w_c * 1.15, c_4k_sync + 26, f"{c_4k_sync:.0f}\n(已挤干)", ha="center", va="bottom", fontsize=8.0, color="#64748B")
for i in range(3):
    v_s, v_d = sdp_fair_vals[i], dp_vals[i]
    ratio = v_s / v_d
    ax.text(x_c[i] - w_c * 0.1, v_s + 24, f"{v_s:.0f}", ha="center", va="bottom", fontsize=9.0, color="#334155")
    ax.text(x_c[i] + w_c * 1.0, v_d + 24, f"{v_d:.0f}", ha="center", va="bottom", fontsize=9.0, fontweight="bold", color=TEAL)
    ymid = max(v_s, v_d) + 145
    ax.text(x_c[i] + w_c * 0.45, ymid, f"{ratio:.2f}×", ha="center", va="bottom", fontsize=10.2,
            fontweight="bold", color=TEAL,
            bbox=dict(boxstyle="round,pad=0.22", fc=TEAL_PALE, ec=TEAL, lw=0.8))

ax.set_xticks(x_c + w_c * 0.45)
ax.set_xticklabels(["64 B @ 1.5 M/s\n（硬件免读+复用）", "1 KiB @ 1.0 M/s\n（主要为 DPDK 栈税）", "4 KiB @ 1.0 M/s\n（主要为 DPDK 栈税）"], fontsize=9.0)
ax.set_ylim(0, 1780)
ax.set_ylabel("主机网络核耗（cycles / 包，B=1，越低越好）", fontsize=10)
ax.set_title("C. 挤干水分后的公平主机核账本（MRV-166，B=1，n=3 中位数）", fontsize=11.0, fontweight="bold", loc="left", pad=8)
ax.legend(loc="upper left", fontsize=8.3, frameon=True, facecolor="white", framealpha=0.96)
ax.grid(axis="y", alpha=0.35)
ax.grid(axis="x", visible=False)

# ==================== PANEL D: Active-NIC Burst Duality (MRV-158) ====================
ax = axes[1, 1]
# Raw data verified: <= 32 MiB card DDR5 <= 0.19 MiB (effectively L3-only); 48 MiB spills 100.56 MiB to card DDR5!
ax.axvspan(0.045, 32.0, color="#F0FDFA", alpha=0.85, zorder=0)
ax.axvline(32.0, color=TEAL, linestyle="--", linewidth=1.25, alpha=0.85)
ax.axvline(48.0, color=AMBER, linestyle=":", linewidth=1.15, alpha=0.75)

ax.text(
    0.06, 1130,
    "≤ 32 MiB：卡 L3 全吸收（卡 DDR5 ≤ 0.2 MiB，全系统 DRAM ≈ 0）",
    fontsize=8.3, color=TEAL, fontweight="bold", va="center",
    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=TEAL, alpha=0.95),
)
ax.text(
    49.0, 1130,
    "≥ 48 MiB 开始溢入卡 DDR5",
    fontsize=8.1, color=AMBER, fontweight="bold", va="center",
    bbox=dict(boxstyle="round,pad=0.22", fc="#FFFBEB", ec=AMBER, alpha=0.92),
)

ax.plot(x_mib, imc_a, marker="o", linewidth=2.3, markersize=5.8, color=SLATE_DARK,
        label="方案 A（主机 128 MiB 大环）：主机 IMC 读写")
ax.plot(x_mib, imc_b, marker="s", linewidth=2.5, markersize=5.8, color=TEAL,
        label="方案 B（主机 256 KiB 热环）：主机 IMC 读写")
ax.plot(x_mib, ddr_b, marker="^", linewidth=2.1, markersize=5.8, linestyle="--", color=AMBER,
        label="方案 B（主动网卡留存）：卡 6 通道 DDR5 读写")

ax.annotate(
    f"128 MiB 突发：971.5 → 10.7 MiB\n主机 DRAM −{(1 - imc_b[-1]/imc_a[-1])*100:.1f}%",
    xy=(128.0, imc_b[-1] + 6),
    xytext=(5.5, 520.0),
    fontsize=8.8,
    fontweight="bold",
    color=TEAL,
    ha="center",
    bbox=dict(boxstyle="round,pad=0.3", fc=TEAL_PALE, ec=TEAL, lw=1.0),
    arrowprops=dict(arrowstyle="->", color=TEAL, lw=1.3),
)
ax.annotate(
    f"48M 溢出\n{ddr_b[7]:.0f} MiB",
    xy=(48.0, ddr_b[7]),
    xytext=(10.0, 120.0),
    fontsize=7.9,
    color=AMBER,
    ha="center",
    arrowprops=dict(arrowstyle="->", color=AMBER, lw=0.9),
)

# Right-margin clean endpoint labels (no overlap!)
ax.text(138.0, ddr_b[-1] + 18, f"卡 DDR5\n{ddr_b[-1]:.0f} MiB", ha="left", va="center", fontsize=8.2, fontweight="bold", color=AMBER)
ax.text(138.0, imc_a[-1] - 48, f"主机 A\n{imc_a[-1]:.0f} MiB", ha="left", va="center", fontsize=8.2, fontweight="bold", color=SLATE_DARK)
ax.text(138.0, imc_b[-1] + 8, f"主机 B\n{imc_b[-1]:.1f} MiB", ha="left", va="center", fontsize=8.2, fontweight="bold", color=TEAL)

ax.set_xscale("log", base=2)
ax.set_xticks([0.0625, 0.25, 1, 4, 16, 32, 64, 128])
ax.set_xticklabels(["64K", "256K", "1M", "4M", "16M", "32M", "64M", "128M"], fontsize=8.8)
ax.set_xlim(0.045, 340)
ax.set_ylim(-35, 1230)
ax.set_xlabel("单轮突发积压大小（4 轮累计 512 MiB @ 100 M/s，64 B）", fontsize=10)
ax.set_ylabel("主存总读写流量（MiB，越低越好）", fontsize=10)
ax.set_title("D. 主动网卡突发蓄水：主机 DRAM −98.9% 与全系统守恒对偶（MRV-158）", fontsize=11.0, fontweight="bold", loc="left", pad=8)
ax.legend(loc="upper left", bbox_to_anchor=(0.01, 0.87), fontsize=8.0, frameon=True, facecolor="white", framealpha=0.95)
ax.grid(True, alpha=0.3)

fig.tight_layout(rect=[0, 0, 1, 0.955])
png = save(fig, "mrv-175-scorecard")
print(f"Saved: {png}")
print(f"Panel C exact cycles: 64B {c_64_sdp:.1f} vs {c_64_dp:.1f}; 1K {c_1k_sdp:.1f} vs {c_1k_dp:.1f}; 4K sync {c_4k_sync:.1f}, fair {c_4k_sdp:.1f} vs {c_4k_dp:.1f}")
