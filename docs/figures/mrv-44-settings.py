"""MRV-44: which settings were active behind each figure, read from the raw runs (MRV-17 data):
reply path and card core count from the reflector command line, how the card cores' reply
stores were mapped from the reflector's own log line per core ("core N stores to the host
through PEM0 outbound region R ... write-combining | Device-nGnRnE"), card and host programs from
the two command lines, rx_poll_mem from the host EAL arguments, and the W values and counts.

A write-combining region is only ever mapped through /dev/pem_wc (card-kmod/pem_wc, see
testbed/crates/testbed/src/mmio.rs OutboundWindow::retarget); without the module the reflector
fails at the first reply, so a run that got every reply back had the module loaded.

    uv run --with matplotlib python docs/figures/mrv-44-settings.py [runs.jsonl]
"""
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
runs = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/mrv17-2026-11-17/runs.jsonl"

# (figure, run group, tag pattern)
GROUPS = [
    ("吞吐 vs W\nW=1024 左", "写合并，卡 1 核", r"^OFFICIAL wc n1 H1$"),
    ("吞吐 vs W\nW=1024 左", "写合并，卡 4 核", r"^OFFICIAL wc n4 H1$"),
    ("吞吐 vs W", "DPI，卡 4 核", r"^OFFICIAL dpi n4 H1$"),
    ("吞吐 vs W", "直写，卡 4 核", r"^OFFICIAL store n4 H1$"),
    ("吞吐 vs W", "SDP + rx_poll_mem，卡 4 核", r"^OFFICIAL sdp mempoll n4 H1$"),
    ("吞吐 vs W", "SDP 读 OUT_CNTS，卡 4 核", r"^OFFICIAL sdp outcnts n4 H1$"),
    ("W=1024 左（星）", "租约版重测，卡 1 核", r"^LEASE H1 wc n1 W1024$"),
    ("W=1024 左（星）", "租约版重测，卡 4 核", r"^LEASE H1 wc n4 W1024$"),
    ("W=1024 右", "主机程序各版本，卡 1 核", r"^(XhostAB2|XhostAB3|Xbisect2?) wc n1 W1024 |^Xdiv "),
    ("卡核停 / 主机停", "卡 1 核", r"^X3 wc n1 (host|card)gap\d+$"),
    ("卡核停 / 主机停", "卡 4 核", r"^X3b wc n4 cardgap\d+$"),
    ("窗口里的\n消息停在哪", "采样，卡 1 核", r"^LANDED wc n1 W(128|1024) probe rep\d$"),
]
CARD_PROG = {
    "/root/bench/inline_reflector": "11-13 原版",
    "/root/bench/inline_reflector-mrv17": "诊断版（加空轮询统计）",
    "/root/bench/inline_reflector-base": "main 编的不加统计版",
    "/root/bench/inline_reflector-mrv17-lease": "kkvvxrou 租约版",
}
HOST_PROG = {
    "/home/hongtao/dev/marvell/testbed/target/release/inline_pingpong": "MRV-17（主检出，当时带隐藏实验参数）",
    "/tmp/mrv17/tx/release/inline_pingpong": "MRV-17 实验版（llotrzzl）",
    "/tmp/mrv17/tr/release/inline_pingpong": "kkvvxrou 租约版",
    "/tmp/mrv17/inline_pingpong.mainline": "main（MRV-17 之前）",
    "/tmp/mrv17/inline_pingpong.varA": "main + closed_loop.rs",
    "/tmp/mrv17/inline_pingpong.varB": "main + inline.rs",
    "/tmp/mrv17/inline_pingpong.varB2": "main + RingReplies",
    "/tmp/mrv17/inline_pingpong.mrv17side": "MRV-17（另一个检出）",
}


def host_prog(cmd):
    return next(tok for tok in cmd.split() if tok.endswith("inline_pingpong") or ".inline_pingpong" in tok
                or re.search(r"inline_pingpong\.\w+$", tok))


def reply_path(card_args):
    if "--store-wc" in card_args:
        return "--store --store-wc"
    if "--store" in card_args:
        return "--store"
    if "--dma" in card_args:
        return f"--dma（{card_args.split('--dma ')[1].split()[0].count(',') + 1} 个 DPI VF）"
    return "SDP（不带参数）"


def cores(card_cmd):
    spec = re.search(r" -l (\S+)", card_cmd).group(1)
    lo, _, hi = spec.partition("-")
    return int(hi or lo) - int(lo) + 1


def regions(groups):
    """'write-combining 252-255' etc. from every run's per-core reflector lines."""
    kinds = set()
    regs = set()
    for log in groups:
        for _core, region, kind in re.findall(r"core (\d+) stores to the host through PEM0 outbound region (\d+) "
                                              r"\(was [^)]*\) as host pf \d+ vf \d+, ([\w-]+)", log):
            kinds.add(kind)
            regs.add(int(region))
    if not kinds:
        return "核不写（DMA / SDP 引擎写）"
    assert len(kinds) == 1, kinds
    kind = kinds.pop()
    name = "写合并 /dev/pem_wc" if kind == "write-combining" else "Device-nGnRnE /dev/mem"
    span = f"{min(regs)}" if len(regs) == 1 else f"{min(regs)}–{max(regs)}"
    return f"{name}，区 {span}"


rows = [json.loads(line) for line in runs.read_text().splitlines()]
table = []
for fig_name, group, pat in GROUPS:
    sel = [r for r in rows if re.search(pat, r["tag"]) and r.get("rc", 0) == 0 and r.get("host")]
    assert sel, group
    for r in sel:
        h = r["host"][0]
        assert h["lost"] == h["bad"] == h["torn"] == 0, r["tag"]
    card_cmds = {r["card_cmd"].split(" -- ")[0].split()[0] for r in sel}
    card_args = {re.sub(r" --poll-gap-ns \d+", "", r["card_cmd"].split(" -- ")[1]) if " -- " in r["card_cmd"] else ""
                 for r in sel}
    assert len(card_args) == 1, (group, card_args)
    ncores = {cores(r["card_cmd"]) for r in sel}
    assert len(ncores) == 1, (group, ncores)
    hosts = OrderedDict((HOST_PROG[host_prog(r["host_cmd"])], None) for r in sel)
    mem = {"rx_poll_mem=1" in r["host_cmd"] for r in sel}
    assert len(mem) == 1, group
    ws = sorted({int(r["host"][0]["window"]) for r in sel})
    gap_sides = [side for side, key in (("卡上", "card_cmd"), ("主机", "host_cmd"))
                 if any(int(g) > 0 for r in sel for g in re.findall(r"--poll-gap-ns (\d+)", r[key]))]
    table.append([
        fig_name,
        f"{group}（{len(sel)} 次）",
        reply_path(card_args.pop()) + (f"\n（另加{'或'.join(gap_sides)} --poll-gap-ns）" if gap_sides else ""),
        str(ncores.pop()),
        regions(r.get("card_log", "") for r in sel),
        " / ".join(CARD_PROG[c] for c in sorted(card_cmds)),
        "\n".join(hosts) if len(hosts) <= 2 else f"{len(hosts)} 个（见 W=1024 图右）",
        "是" if mem.pop() else "否",
        f"{ws[0]}–{ws[-1]}（{len(ws)} 档）" if len(ws) > 3 else " / ".join(map(str, ws)),
        "全 0",
    ])

cols = ["图", "运行组", "回程（卡上参数）", "卡核", "卡核写回复的映射", "卡上程序", "主机程序", "rx_poll_mem", "W", "lost/bad/torn"]
fig, ax = plt.subplots(figsize=(17, 0.42 * len(table) + 1.3))
ax.axis("off")
tb = ax.table(cellText=table, colLabels=cols, cellLoc="left", colLoc="left", bbox=[0, 0, 1, 1],
              colWidths=[0.07, 0.15, 0.12, 0.03, 0.15, 0.1, 0.18, 0.06, 0.07, 0.06])
tb.auto_set_font_size(False)
tb.set_fontsize(8)
for (row, col), cell in tb.get_celld().items():
    cell.set_edgecolor("#d1d5db")
    if row == 0:
        cell.set_facecolor("#f3f4f6")
        cell.set_text_props(weight="bold")
    elif "写合并" in table[row - 1][4]:
        if col == 4:
            cell.set_facecolor("#fef3c7")
ax.set_title("MRV-44 各图背后的设置（从 runs.jsonl 的两边命令行和卡上程序日志读出）\n"
             "共同条件：主机核 40（3.9 GHz）；主机经 sysfs resource4（不是 resource4_wc）映射 BAR4 槽，用 MOVDIR64B 一次写 64 B 请求；64 B，批大小 1",
             fontsize=10)
print(save(fig, "mrv-44-settings"))
