"""MRV-35: software vs hardware time per message, from the raw runs in
logs/mrv35-2026-11-17/runs.jsonl. Writes three charts next to this file:

- mrv-35-breakdown: per regime, how much of each resource's time per message is software;
- mrv-35-card-phases: card core time per SDP reply by phase, fake hardware vs real closed loop;
- mrv-35-predict: measured time per message vs what software alone would allow.

    uv run --with matplotlib python docs/figures/mrv-35-sw-hw.py [runs.jsonl]

Software = the same code with the hardware replaced by fakes that answer at once (host: replies
written into a local ring by the submit; card: requests written by the core itself, replies freed
in software or stored to local memory). Every closed-loop run used here has lost/bad/torn 0.
"""
import json
import statistics
import sys
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
RUNS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/mrv35-2026-11-17/runs.jsonl"
runs = [json.loads(line) for line in RUNS.read_text().splitlines()]
mean = statistics.mean


def pick(tag, group=None):
    """Runs with exactly this tag (and group); closed-loop runs must verify."""
    out = [r for r in runs if r["tag"] == tag and (group is None or r["group"] == group) and r["rc"] == 0]
    assert out, tag
    for r in out:
        h = r.get("host")
        if h:
            assert h["lost"] == h["bad"] == h["torn"] == 0, r["id"]
        for c in (r.get("card") or {}).get("cores", []):
            assert c["corrupt"] == c["partial"] == 0, r["id"]
    return out


def ns_per_msg(rs):
    """Measured time per message: 1 / throughput."""
    return mean(1e3 / r["host"]["mmsg"] for r in rs)


def card_busy(rs):
    """Card core busy time per reply (timed phases), averaged over cores and runs."""
    return mean(sum(c["phase_ticks"]) / c["taken"] for r in rs for c in r["card"]["cores"])


def card_phases(rs):
    n = len(rs[0]["card"]["cores"][0]["phase_ticks"])
    return [mean(c["phase_ticks"][i] / c["taken"] for r in rs for c in r["card"]["cores"]) for i in range(n)]


def per_batch(rs):
    return mean(c["taken"] / c["batches"] for r in rs for c in r["card"]["cores"])


def card_loop(rs):
    """Untimed fake run: whole loop per reply (includes writing the fake request)."""
    return mean(c["loop_ticks"] / c["taken"] for r in rs for c in r["card"]["cores"])


def host_fake(w):
    return ns_per_msg(pick(f"fake host W={w} rep0") + pick(f"fake host W={w} rep1"))


# ---- isolation measurements -------------------------------------------------------------
HOST_SW = {w: host_fake(w) for w in (1, 64, 256, 1024)}
HOST_SW[512] = mean([HOST_SW[256], HOST_SW[1024]])  # no fake run at 512; 256 and 1024 agree
SDP_SW32_TIMED = card_busy(pick("1 core null b32 busy"))
SDP_SW32 = card_loop(pick("1 core null b32 control"))
SDP_SW1 = card_loop(pick("1 core null b1 control"))
WC_SW32_TIMED = card_busy(pick("1 core store-local b32 busy"))
WC_SW32 = card_loop(pick("1 core store-local b32 control"))
WC_SW1 = card_loop(pick("1 core store-local b1 control"))

# ---- regimes ----------------------------------------------------------------------------
# (label, runs with the card instrumented where possible, card cores, W, production-loop runs of
#  the same configuration for the reference rate)
A = [
    ("1 卡核", pick("sdp W=512 card 1 core busy-time", "A-sdp"), 1, 512,
     pick("sdp W=512 card 1 core original loop", "A-sdp") + pick("sdp W=512 card 1 core shared pool production loop", "A-sdp")),
    ("2 卡核共用池", pick("sdp W=512 card 2 cores shared pool busy-time", "A-sdp"), 2, 512,
     pick("sdp W=512 card 2 cores shared pool original loop", "A-sdp") + pick("sdp W=512 card 2 cores shared pool production loop", "A-sdp")),
    ("2 卡核每核一个池*", pick("sdp W=512 card 2 cores per-core pool production loop", "A-sdp"), 2, 512, None),
    ("4 卡核", pick("sdp W=512 card 4 cores busy-time", "A-sdp"), 4, 512, pick("sdp W=512 card 4 cores original loop", "A-sdp")),
]
B = [
    ("1 卡核 W=64", pick("wc W=64 card 1 core busy-time"), 1, 64, pick("wc W=64 card 1 core production loop")),
    ("1 卡核 W=1024", pick("wc W=1024 card 1 core busy-time"), 1, 1024, pick("wc W=1024 card 1 core production loop")),
    ("4 卡核 W=64", pick("wc W=64 card 4 cores busy-time"), 4, 64, pick("wc W=64 card 4 cores production loop")),
    ("4 卡核 W=1024", pick("wc W=1024 card 4 cores busy-time"), 4, 1024, pick("wc W=1024 card 4 cores production loop")),
]
W1 = [
    ("A：SDP 回复", pick("sdp W=1 card 1 core production loop"), SDP_SW1),
    ("B：写合并回复", pick("wc W=1 card 1 core production loop"), WC_SW1),
]

C_HOST_SW, C_HOST_REST = "#2563eb", "#bfdbfe"
C_CARD_SW, C_CARD_HW, C_CARD_REST = "#059669", "#f59e0b", "#d1fae5"
C_HW = "#ea580c"


def resource_bars(ax, regimes, path_sw32, path_sw1, title):
    xs, labels = [], []
    for i, (label, rs, cores, w, prod) in enumerate(regimes):
        t = ns_per_msg(rs)
        x = i * 3
        # host core: it never idles, so its time per message is 1 / throughput
        hs = HOST_SW[w]
        ax.bar(x, hs, color=C_HOST_SW, width=0.9)
        ax.bar(x, t - hs, bottom=hs, color=C_HOST_REST, width=0.9)
        ax.text(x, t, f"{t:.0f}", ha="center", va="bottom", fontsize=8)
        # card core: cores / throughput per reply it handles
        ct = cores * t
        timed = rs[0]["card"]["cores"] and rs[0]["card"]["cores"][0]["phase_ticks"][0] > 0
        big = timed and per_batch(rs) >= 9
        sw = path_sw32 if (big or not timed) else path_sw1
        ax.bar(x + 1, sw, color=C_CARD_SW, width=0.9)
        if big:
            busy = card_busy(rs)
            ax.bar(x + 1, busy - sw, bottom=sw, color=C_CARD_HW, width=0.9)
            ax.bar(x + 1, max(ct - busy, 0), bottom=busy, color=C_CARD_REST, width=0.9)
        else:
            ax.bar(x + 1, ct - sw, bottom=sw, color=C_CARD_REST, width=0.9, hatch="//", edgecolor="#6ee7b7")
        ax.text(x + 1, ct, f"{ct:.0f}", ha="center", va="bottom", fontsize=8)
        mmsg = mean(r["host"]["mmsg"] for r in rs)
        xs.append(x + 0.5)
        if prod:
            ref = mean(r["host"]["mmsg"] for r in prod)
            labels.append(f"{label}\n计时版 {mmsg:.1f} M/s\n（产品版 {ref:.1f}）")
        else:
            labels.append(f"{label}\n产品版 {mmsg:.1f} M/s")
    ax.set_xticks(xs, labels, fontsize=8)
    ax.set_ylabel("每条消息的时间 (ns)")
    ax.set_title(title, fontsize=10)


fig, axes = plt.subplots(1, 3, figsize=(15, 5.2), gridspec_kw={"width_ratios": [1.1, 2, 2]})
# W=1: one message at a time, so host, card and transit add up
ax = axes[0]
for i, (label, rs, card_sw) in enumerate(W1):
    t = ns_per_msg(rs)
    hs = HOST_SW[1]
    ax.bar(i, hs, color=C_HOST_SW, width=0.6, label="主机：假硬件代码（代理）" if i == 0 else None)
    ax.bar(i, card_sw, bottom=hs, color=C_CARD_SW, width=0.6, label="卡：假硬件代码（代理）" if i == 0 else None)
    ax.bar(i, t - hs - card_sw, bottom=hs + card_sw, color=C_HW, width=0.6, label="其余：硬件往返\n+ 代理没覆盖的软件" if i == 0 else None)
    ax.text(i, t, f"{t:.0f} ns\n代理 {hs + card_sw:.0f}", ha="center", va="bottom", fontsize=8)
ax.set_xticks(range(len(W1)), [w[0] for w in W1], fontsize=9)
ax.set_ylabel("每条消息的时间 (ns)")
ax.set_title("W=1（一次一条，各段相加）", fontsize=10)
ax.legend(fontsize=7, loc="upper right")
ax.set_ylim(0, 3000)

resource_bars(axes[1], A, SDP_SW32_TIMED, SDP_SW1, "A：SDP 回复，W=512，rx_poll_mem=1（各资源并行）")
resource_bars(axes[2], B, WC_SW32_TIMED, WC_SW1, "B：写合并回复（各资源并行）")
handles = [
    plt.Rectangle((0, 0), 1, 1, color=C_HOST_SW), plt.Rectangle((0, 0), 1, 1, color=C_HOST_REST),
    plt.Rectangle((0, 0), 1, 1, color=C_CARD_SW), plt.Rectangle((0, 0), 1, 1, color=C_CARD_HW),
    plt.Rectangle((0, 0), 1, 1, color=C_CARD_REST),
    plt.Rectangle((0, 0), 1, 1, facecolor=C_CARD_REST, hatch="//", edgecolor="#6ee7b7"),
]
names = ["主机：假硬件下的同一段代码（软件代理）", "主机其余：等硬件、空轮询（A 还含收包驱动，未分开）", "卡：假硬件下的同一段代码（软件代理）",
         "卡忙、比假硬件多出的（同一段代码多花的为主；含 tx_burst 驱动与软件释放之差、计时开销之差，未分开）",
         "卡空闲", "卡其余：等待和硬件（每批 1–3 条，分不开）"]
axes[1].legend(handles, names, fontsize=7, loc="upper left")
for ax in axes[1:]:
    ax.set_ylim(0, 470)
axes[1].text(0.0, -0.30, "* 每核一个池用产品版卡程序（不能分段计时，卡条只分出软件）；分段计时版在这个配置下掉进每批 2.6 条的慢状态（19.9 M/s）。\n"
             "计时版：卡核分段读计数器，吞吐和产品版不同；软件代理含造假请求/假回复的几 ns，不含 MOVDIR64B 指令和 tx_burst 驱动。",
             transform=axes[1].transAxes, fontsize=7, va="top")
fig.suptitle("每条消息的时间 vs 假硬件下同一段代码的时间（软件代理）。64 B，主机批大小 1，主机核 40 3.9 GHz，uncore 2.2 GHz，卡核 2.0 GHz\n"
             "左：W=1 串行相加；中、右：每组左条=主机核（1/吞吐），右条=卡核（卡核数/吞吐），各资源并行，时间由最忙的那个决定",
             fontsize=9)
print(save(fig, "mrv-35-breakdown"))

# ---- card phases ------------------------------------------------------------------------
PH = ["取请求格（读格+核对）", "分配 mbuf", "写帧", "发送：tx_burst（假：软件释放）", "写进度字"]
PC = ["#0ea5e9", "#f59e0b", "#a855f7", "#10b981", "#94a3b8"]
cols = [
    ("软件：1 核\n假请求、null 发送", pick("1 core null b32 busy")),
    ("软件：2 核共用池\n假请求、null 发送", pick("2 cores shared null b32 busy")),
    ("软件：2 核每核一个池\n假请求、null 发送", pick("2 cores per-core null b32 busy")),
    ("真闭环：1 核\n24.2 M/s", pick("sdp W=512 card 1 core busy-time", "A-sdp")),
    ("真闭环：2 核共用池\n30.3 M/s", pick("sdp W=512 card 2 cores shared pool busy-time", "A-sdp")),
]
fig, ax = plt.subplots(figsize=(10, 5.2))
for i, (label, rs) in enumerate(cols):
    ph = card_phases(rs)
    bottom = 0
    for j, v in enumerate(ph):
        ax.bar(i, v, bottom=bottom, color=PC[j], width=0.6, label=PH[j] if i == 0 else None)
        if v >= 3:
            ax.text(i, bottom + v / 2, f"{v:.1f}", ha="center", va="center", fontsize=8)
        bottom += v
    ax.text(i, bottom, f"{bottom:.1f} ns\n每批 {per_batch(rs):.1f} 条", ha="center", va="bottom", fontsize=8)
ax.set_xticks(range(len(cols)), [c[0] for c in cols], fontsize=8)
ax.set_ylabel("每条回复卡核的时间 (ns)")
ax.set_ylim(0, 80)
ax.legend(fontsize=8, loc="upper left")
ax.set_title("卡核每条 SDP 回复的时间按段拆（同一段分段代码；每段边界读一次卡计数器，约 20 ns/次，每批 32 条时每条约 3 ns，两边都含）\n"
             "真闭环：主机核 40，W=512，rx_poll_mem=1，64 B；卡核 2.0 GHz，99.9% 在忙", fontsize=9)
print(save(fig, "mrv-35-card-phases"))

# ---- prediction vs measurement ------------------------------------------------------------
rows = [
    ("A W=1", ns_per_msg(W1[0][1]), HOST_SW[1] + SDP_SW1, "硬件往返"),
    ("A 1 卡核", ns_per_msg(pick("sdp W=512 card 1 core original loop", "A-sdp")), max(HOST_SW[512], SDP_SW32), "卡核在顶"),
    ("A 2 卡核共用池", ns_per_msg(pick("sdp W=512 card 2 cores shared pool production loop", "A-sdp")), max(HOST_SW[512], SDP_SW32 / 2), "卡核在顶"),
    ("A 2 卡核每核一个池", ns_per_msg(pick("sdp W=512 card 2 cores per-core pool production loop", "A-sdp")), max(HOST_SW[512], SDP_SW32 / 2), "未定位（卡没计时）"),
    ("A 4 卡核", ns_per_msg(pick("sdp W=512 card 4 cores original loop", "A-sdp")), max(HOST_SW[512], SDP_SW32 / 4), "回程（MRV-15 定位）"),
    ("B W=1", ns_per_msg(W1[1][1]), HOST_SW[1] + WC_SW1, "硬件往返"),
    ("B 1 卡核 W=64", ns_per_msg(pick("wc W=64 card 1 core production loop")), max(HOST_SW[64], WC_SW32), "回程（MRV-17 定位）"),
    ("B 1 卡核 W=1024", ns_per_msg(pick("wc W=1024 card 1 core production loop")), max(HOST_SW[1024], WC_SW32), "主机核在顶"),
    ("B 4 卡核 W=64", ns_per_msg(pick("wc W=64 card 4 cores production loop")), max(HOST_SW[64], WC_SW32 / 4), "回程（MRV-17 定位）"),
    ("B 4 卡核 W=1024", ns_per_msg(pick("wc W=1024 card 4 cores production loop")), max(HOST_SW[1024], WC_SW32 / 4), "主机核在顶，IPC 1.15"),
]
fig, ax = plt.subplots(figsize=(12, 5))
xs = range(len(rows))
ax.bar(xs, [r[1] for r in rows], color="#cbd5e1", width=0.6, label="实测：每条消息的时间（1/吞吐；W=1 是一个来回）")
ax.scatter(xs, [r[2] for r in rows], color="#059669", zorder=3, s=40, label="假硬件代码的预测（软件代理）：W=1 两侧相加；其余取主机、卡÷核数中较慢的")
for x, (_, meas, pred, dom) in zip(xs, rows):
    ax.text(x, meas * 1.08, f"{meas:.0f}\n代理/实测 {100 * pred / meas:.0f}%", ha="center", va="bottom", fontsize=8)
ax.set_yscale("log")
ax.set_ylim(4, 6000)
ax.set_xticks(list(xs), [f"{r[0]}\n{r[3]}" for r in rows], fontsize=8)
ax.set_ylabel("每条消息的时间 (ns，对数)")
ax.legend(fontsize=8, loc="upper right")
ax.set_title("假硬件代码的预测 vs 实测（产品版卡程序，未加计时）。代理换掉了几样操作：含造假请求/假回复的几 ns，\n"
             "不含 MOVDIR64B 指令、tx_burst 驱动；A 的主机软件不含 octeon_ep 收包驱动。64 B，主机批大小 1，主机核 40 3.9 GHz，"
             "uncore 2.2 GHz，卡核 2.0 GHz；A：W=512、rx_poll_mem=1", fontsize=9)
print(save(fig, "mrv-35-predict"))

for name, val in [("host sw W=1", HOST_SW[1]), ("host sw W=64", HOST_SW[64]), ("host sw W=512", HOST_SW[512]),
                  ("host sw W=1024", HOST_SW[1024]), ("card sdp sw b32 timed", SDP_SW32_TIMED), ("card sdp sw b32", SDP_SW32),
                  ("card sdp sw b1", SDP_SW1), ("card wc sw b32 timed", WC_SW32_TIMED), ("card wc sw b32", WC_SW32),
                  ("card wc sw b1", WC_SW1)]:
    print(f"{name}: {val:.1f} ns")
for r in rows:
    print(f"{r[0]}: measured {r[1]:.1f} ns, software-only {r[2]:.1f} ns ({100 * r[2] / r[1]:.0f}%), {r[3]}")
