"""MRV-35 follow-up (direct paths, MRV-49): how fast each side's code runs against fake hardware
(the software ceiling), and which per-message time sets the throughput. Reads
logs/mrv35-2026-11-17/runs.jsonl and writes two charts next to this file:

- mrv-35-ceiling: measured throughput per state vs the fake-hardware ceilings of host and card;
- mrv-35-critical: per message, the interval between messages (1 / throughput, the bottleneck's
  service time) against each side's fake-hardware time; and the mean round trip split into the
  unloaded round trip (W=1) and the excess under load (queueing plus any load-dependent slowdown).

    uv run --with matplotlib python docs/figures/mrv-35-critical.py [runs.jsonl]

Fake hardware: host = the closed loop and ring reader with each reply written into a local ring
by the submit (no MOVDIR64B); card = the ring loop with the core writing its own requests and
storing each 64 B reply into local memory (the application code all three direct reply paths
share; no WC/Device window, no DPI driver). SDP is one reference point (frames freed in software
instead of tx_burst; its host receive driver cannot be faked, so it has no host ceiling).
Every run used has rc 0, lost/bad/torn 0 and no corrupt or partial cells.
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
    out = [r for r in runs if r["tag"] == tag and (group is None or r["group"] == group) and r["rc"] == 0]
    assert out, tag
    for r in out:
        if r.get("host"):
            assert r["host"]["lost"] == r["host"]["bad"] == r["host"]["torn"] == 0, r["id"]
        for c in (r.get("card") or {}).get("cores", []):
            assert c["corrupt"] == c["partial"] == 0, r["id"]
    return out


def rate(rs):
    return mean(r["host"]["mmsg"] for r in rs)


def rtt(rs):
    return mean(r["host"]["rtt_mean"] for r in rs)


def fake_card_ns(kind, cores, batch):
    """Card loop time per reply on one core (ns) against fake hardware."""
    rs = pick(f"ceiling {kind} {cores} core{'s' if cores > 1 else ''} b{batch}")
    return mean(c["loop_ticks"] / c["taken"] for r in rs for c in r["card"]["cores"])


def fake_host_ns(w):
    return mean(1e3 / r["host"]["mmsg"] for t in (f"fake host W={w} rep0", f"fake host W={w} rep1") for r in pick(t, "H-fake"))


W1 = {  # path -> W=1 runs
    "SDP": pick("sdp W=1 card 1 core production loop"),
    "写合并": pick("wc W=1 card 1 core production loop"),
    "直写": pick("store W=1 card 4 cores production loop"),
    "DPI": pick("dpi W=1 card 4 cores production loop"),
}
RTT0 = {k: rtt(v) for k, v in W1.items()}
# label, path, card cores, W, production runs, card fake kind
STATES = [
    ("SDP\n1 卡核 W=512", "SDP", 1, 512, pick("sdp W=512 card 1 core original loop", "A-sdp") + pick("sdp W=512 card 1 core shared pool production loop", "A-sdp"), "sdp-null shared"),
    ("写合并\n1 卡核 W=64", "写合并", 1, 64, pick("wc W=64 card 1 core production loop"), "wc-local"),
    ("写合并\n1 卡核 W=1024", "写合并", 1, 1024, pick("wc W=1024 card 1 core production loop"), "wc-local"),
    ("写合并\n4 卡核 W=64", "写合并", 4, 64, pick("wc W=64 card 4 cores production loop"), "wc-local"),
    ("写合并\n4 卡核 W=1024", "写合并", 4, 1024, pick("wc W=1024 card 4 cores production loop"), "wc-local"),
    ("直写\n4 卡核 W=64", "直写", 4, 64, pick("store W=64 card 4 cores production loop"), "wc-local"),
    ("直写\n4 卡核 W=1024", "直写", 4, 1024, pick("store W=1024 card 4 cores production loop"), "wc-local"),
    ("DPI\n4 卡核 W=64", "DPI", 4, 64, pick("dpi W=64 card 4 cores production loop"), "wc-local"),
    ("DPI\n4 卡核 W=1024", "DPI", 4, 1024, pick("dpi W=1024 card 4 cores production loop"), "wc-local"),
]
rows = []
for label, path, cores, w, prod, kind in STATES:
    x = rate(prod)
    rows.append(dict(label=label, path=path, cores=cores, w=w, rate=x, interval=1e3 / x, rtt=rtt(prod),
                     ghz=mean(r["perf"]["ghz"] for r in prod), host=fake_host_ns(w) if path != "SDP" else None,
                     card_b1=fake_card_ns(kind, cores, 1) / cores, card_b32=fake_card_ns(kind, cores, 32) / cores,
                     hide=x * RTT0[path]))

C_REAL, C_HOST, C_CARD = "#dc2626", "#60a5fa", "#9ca3af"
# The host loop pays its per-poll work (two TSC reads, the poll loop) once per poll: per message
# it costs fake W=1 (one reply per poll) at most and fake W>=32 (32 per poll) at least.
HOST1 = fake_host_ns(1)

# ---- chart 1: throughput vs fake-hardware ceilings ----------------------------------------
fig, ax = plt.subplots(figsize=(13, 4.8))
labels, k = [], 0
for path, rs in W1.items():
    card1 = fake_card_ns("sdp-null shared" if path == "SDP" else "wc-local", 1, 1)
    real = rate(rs)
    ax.bar(k - 0.2, real, 0.36, color=C_REAL)
    if path != "SDP":
        ceil = 1e3 / (fake_host_ns(1) + card1)
        ax.bar(k + 0.2, ceil, 0.36, color=C_CARD)
        ax.text(k + 0.2, ceil * 1.12, f"{ceil:.0f}", ha="center", fontsize=8)
    ax.text(k - 0.2, real * 1.12, f"{real:.2f}", ha="center", fontsize=8)
    labels.append(f"{path}\nW=1")
    k += 1
for r in rows:
    ax.bar(k - 0.27, r["rate"], 0.26, color=C_REAL)
    if r["host"]:
        ax.bar(k, 1e3 / r["host"], 0.26, color=C_HOST)
        ax.plot([k - 0.12, k + 0.12], [1e3 / HOST1] * 2, color="#111827", lw=1.5)
    ax.bar(k + 0.27, 1e3 / r["card_b32"], 0.26, color=C_CARD)
    ax.plot([k + 0.15, k + 0.39], [1e3 / r["card_b1"]] * 2, color="#111827", lw=1.5)
    ax.text(k - 0.27, r["rate"] * 1.12, f"{r['rate']:.1f}", ha="center", fontsize=8)
    labels.append(r["label"])
    k += 1
ax.set_yscale("log")
ax.set_ylim(0.3, 800)
ax.set_xticks(range(k), labels, fontsize=7.5)
ax.set_ylabel("吞吐 (Mmsg/s，对数)")
ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=C_REAL), plt.Rectangle((0, 0), 1, 1, color=C_HOST),
                   plt.Rectangle((0, 0), 1, 1, color=C_CARD), plt.Line2D([0], [0], color="#111827", lw=1.5)],
          labels=["实测", "主机代码，假硬件，每次轮询 32 条", "卡代码，假硬件，每批 32 条（W=1：两侧串行）", "黑线：每次轮询 / 每批只有 1 条时"],
          fontsize=7.5, ncol=4, loc="upper left")
ax.set_title("假硬件上限 vs 实测：64 B，主机批大小 1，主机核 40，uncore 2.2 GHz，卡核 2.0 GHz；SDP 只作参照（rx_poll_mem=1，主机收包驱动无法假，没有主机上限）",
             fontsize=9)
print(save(fig, "mrv-35-ceiling"))

# ---- chart 2: bottleneck service time vs each side's code; latency = unloaded + excess -----
fig, (a1, a2) = plt.subplots(1, 2, figsize=(17, 5.2), gridspec_kw={"width_ratios": [1.1, 1]})
xs = list(range(len(rows)))
a1.bar([x - 0.27 for x in xs], [r["interval"] for r in rows], 0.26, color=C_REAL, label="间隔 = 1/吞吐（瓶颈每条的服务时间）")
a1.bar(xs, [HOST1 if r["host"] else 0 for r in rows], 0.26, color=C_HOST, label="主机代码，假硬件，每次轮询 1 条（黑线：32 条）")
for x, r in zip(xs, rows):
    if r["host"]:
        a1.plot([x - 0.13, x + 0.13], [r["host"]] * 2, color="#111827", lw=1.5)
a1.bar([x + 0.27 for x in xs], [r["card_b32"] if r["path"] == "SDP" else r["card_b1"] for r in rows], 0.26, color=C_CARD,
       label="卡代码，假硬件，÷ 卡核数（每批 1 条，较慢的一端；SDP 按实测每批 32 条）")
for x, r in zip(xs, rows):
    a1.text(x - 0.27, r["interval"] + 1.5, f"{r['interval']:.0f}", ha="center", fontsize=8, color=C_REAL)
a1.set_xticks(xs, [r["label"] for r in rows], fontsize=6.8)
a1.set_ylabel("每条消息 (ns)")
a1.set_ylim(0, 100)
a1.legend(fontsize=7.5, loc="upper left")
a1.set_title("每条消息：瓶颈的服务时间（红）vs 两侧代码在假硬件上的时间", fontsize=9)

a2.bar(xs, [RTT0[r["path"]] for r in rows], 0.55, color="#d1d5db", label="空载往返（同一路径 W=1 实测）")
a2.bar(xs, [r["rtt"] - RTT0[r["path"]] for r in rows], 0.55, bottom=[RTT0[r["path"]] for r in rows],
       color=C_REAL, alpha=0.35, label="多出的：排队 + 负载下变慢")
for x, r in zip(xs, rows):
    a2.text(x, r["rtt"] + 0.6, f"{r['rtt']:.1f}\n{r['hide']:.0f}/{r['w']}", ha="center", fontsize=7)
a2.set_xticks(xs, [r["label"] for r in rows], fontsize=6.8)
a2.set_ylabel("平均往返 (µs)")
a2.set_ylim(0, 62)
a2.legend(fontsize=7.5, loc="upper left")
a2.set_title("每条消息的往返；柱上 N/W = 盖住空载往返要的在途数（吞吐 × 空载往返）/ 实际在途上限", fontsize=9)
fig.suptitle("64 B，主机批大小 1，并发 = W，产品版卡程序；主机核 40（实测频率见 runs.jsonl），uncore 2.2 GHz，卡核 2.0 GHz", fontsize=9)
print(save(fig, "mrv-35-critical"))

for r in rows:
    print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
print("RTT0", {k: round(v, 2) for k, v in RTT0.items()}, "host W=1", round(fake_host_ns(1), 2))
