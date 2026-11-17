"""MRV-53 checks 2 & 3: host 3-phase timing + accounting-gap breakdown (H2 --account-every 64,
paired with untimed H2) and aligned --fake-card --fake-batch K (cores 1 & 4). Reads only
logs/mrv53-2026-11-17/runs.jsonl; prints the summary tables and saves docs/figures/mrv-53-phases.png.

    uv run --with matplotlib python docs/figures/mrv-53-phases.py
"""

import json
import statistics as st
import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent))
import mrvplot  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / 'logs/mrv53-2026-11-17/runs.jsonl'

CONFIG_ORDER = ['WC1', 'WC4', 'ST1', 'ST4', 'DPI1', 'DPI4']
CONFIG_SHORT = {
    'WC1': '写合并1核',
    'WC4': '写合并4核',
    'ST1': '直写1核',
    'ST4': '直写4核',
    'DPI1': 'DPI 1核',
    'DPI4': 'DPI 4核',
}


def load_all():
    rows = []
    for line in RUNS.read_text().splitlines():
        e = json.loads(line)
        if e.get('rc') != 0:
            continue
        h = e.get('host')
        if isinstance(h, dict) and (h.get('lost') or h.get('bad') or h.get('torn')):
            raise SystemExit(f'run {e["id"]} failed verification')
        rows.append(e)
    return rows


def analyse_phases(rows):
    """Extract paired H2 untimed and --account-every 64 runs per (config, W, state)."""
    by_key = {}
    for e in rows:
        if e.get('group') != 'mrv53' or e.get('hostbin') != 'H2' or e.get('id', 0) < 380:
            continue
        state = 'start-up' if e.get('state') == 'start-up' else 'steady'
        key = (e['config'], e['W'], state)
        slot = by_key.setdefault(key, {'untimed': [], 'timed': []})
        if (e.get('host_extra') or '') == '--account-every 64':
            slot['timed'].append(e)
        elif (e.get('host_extra') or '') == '':
            slot['untimed'].append(e)

    out = {}
    for key, slot in by_key.items():
        if not slot['timed']:
            continue
        untimed_ns = [1e3 / r['host']['mmsg'] for r in slot['untimed']]
        timed_ns = [1e3 / r['host']['mmsg'] for r in slot['timed']]
        sends, bpolls, epolls, tails, k_busys = [], [], [], [], []
        w_over_x_gaps, gap_polls, tail_rounds, half_sends = [], [], [], []
        empty_pcts = []
        for r in slot['timed']:
            x = r['exp']
            h = r['host']
            hz = x['tsc_hz'] / 1e9
            reps = max(x['gap_replies'], 1)
            bp = max(x['busy_polls'], 1)
            tl = max(x['tails'], 1)
            sends.append((x['send_ticks'] / hz) / reps)
            bpolls.append((x['busy_poll_ticks'] / hz) / reps)
            epolls.append((x['empty_poll_ticks'] / hz) / reps)
            tails.append((x['tail_ticks'] * (x['sampled'] / tl) / hz) / reps)
            k_busys.append(reps / bp)
            empty_pcts.append(h['empty_pct'])
            # Accounting gap: W / throughput - mean RTT (in ns)
            w_over_x_gaps.append((r['W'] / h['mmsg'] - h['rtt_mean']) * 1e3)
            # Direct measurement of host turn-around after first_at stamp:
            gap_polls.append((x['gap_ticks'] / hz) / reps)
            tail_rounds.append((x['tail_ticks'] / tl) / hz)
            half_sends.append(0.5 * (x['send_ticks'] / bp) / hz)
        out[key] = {
            'untimed_ns': st.fmean(untimed_ns) if untimed_ns else float('nan'),
            'untimed_mmsg': st.fmean(r['host']['mmsg'] for r in slot['untimed']) if slot['untimed'] else float('nan'),
            'timed_ns': st.fmean(timed_ns),
            'timed_mmsg': st.fmean(r['host']['mmsg'] for r in slot['timed']),
            'send_ns': st.fmean(sends),
            'bpoll_ns': st.fmean(bpolls),
            'epoll_ns': st.fmean(epolls),
            'tail_ns': st.fmean(tails),
            'k_busy': st.fmean(k_busys),
            'empty_pct': st.fmean(empty_pcts),
            'acct_gap_ns': st.fmean(w_over_x_gaps),
            'gap_poll_ns': st.fmean(gap_polls),
            'tail_round_ns': st.fmean(tail_rounds),
            'half_send_ns': st.fmean(half_sends),
        }
    return out


def analyse_fake(rows):
    """Extract aligned --fake-card runs (group == 'mrv53-fake')."""
    by_key = {}
    for e in rows:
        if e.get('group') != 'mrv53-fake':
            continue
        key = (e['fake_cores'], e['W'], e['fake_k'], bool(e['timed']))
        by_key.setdefault(key, []).append(e)
    out = {}
    for key, rs in by_key.items():
        ns_list = [1e3 / r['host']['mmsg'] for r in rs]
        mmsg_list = [r['host']['mmsg'] for r in rs]
        d = {'ns': st.fmean(ns_list), 'mmsg': st.fmean(mmsg_list), 'n': len(rs)}
        if key[3]:  # timed
            sends, bpolls, tails = [], [], []
            for r in rs:
                x = r['exp']
                hz = x['tsc_hz'] / 1e9
                reps = max(x['gap_replies'], 1)
                tl = max(x['tails'], 1)
                sends.append((x['send_ticks'] / hz) / reps)
                bpolls.append((x['busy_poll_ticks'] / hz) / reps)
                tails.append((x['tail_ticks'] * (x['sampled'] / tl) / hz) / reps)
            d['send_ns'] = st.fmean(sends)
            d['bpoll_ns'] = st.fmean(bpolls)
            d['tail_ns'] = st.fmean(tails)
        out[key] = d
    return out


def print_tables(phases, fake):
    print('=== CHECK 2: Host 3-phase timing & accounting gap ===')
    for key in sorted(phases.keys(), key=lambda k: (CONFIG_ORDER.index(k[0]), k[2] != 'start-up', k[1])):
        d = phases[key]
        direct = d['gap_poll_ns'] + d['tail_round_ns'] + d['half_send_ns']
        print(
            f"{key[0]:4s} W={key[1]:<4d} ({key[2]:8s}) | untimed {d['untimed_mmsg']:5.2f} M/s ({d['untimed_ns']:5.1f} ns)"
            f" timed {d['timed_mmsg']:5.2f} M/s ({d['timed_ns']:5.1f} ns) | "
            f"send {d['send_ns']:5.1f} + bpoll {d['bpoll_ns']:5.1f} + tail {d['tail_ns']:4.1f} + epoll {d['epoll_ns']:5.1f} ns"
            f" | K_busy={d['k_busy']:5.2f} empty={d['empty_pct']:2.0f}% | "
            f"W/X-RTT={d['acct_gap_ns']:6.0f} ns vs direct={direct:6.0f} ns "
            f"(poll_tail={d['gap_poll_ns']:.0f} + loop_tail={d['tail_round_ns']:.0f} + 0.5*send={d['half_send_ns']:.0f})"
        )

    print('\n=== CHECK 3: Aligned --fake-card --fake-batch K ===')
    for (cores, w, k, timed), d in sorted(fake.items()):
        extra = f" (send={d['send_ns']:.1f} bpoll={d['bpoll_ns']:.1f} tail={d['tail_ns']:.1f})" if timed else ''
        print(f"cores={cores} W={w:<4d} K={k:<2d} {'timed  ' if timed else 'untimed'} -> {d['mmsg']:5.2f} M/s ({d['ns']:5.2f} ns/msg){extra}")


def make_figure(phases, fake):
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16.2, 5.0), gridspec_kw={'width_ratios': [1.35, 1.05, 1.0]})

    # Panel (a): Host 3-phase breakdown for W=64 and W=1024 states
    p_keys = []
    if ('WC1', 64, 'start-up') in phases:
        p_keys.append(('WC1', 64, 'start-up'))
    for c in CONFIG_ORDER:
        for w in (64, 1024):
            if (c, w, 'steady') in phases:
                p_keys.append((c, w, 'steady'))

    xs = list(range(len(p_keys)))
    send_v = [phases[k]['send_ns'] for k in p_keys]
    bpoll_v = [phases[k]['bpoll_ns'] + phases[k]['tail_ns'] for k in p_keys]
    epoll_v = [phases[k]['epoll_ns'] for k in p_keys]
    untimed_v = [phases[k]['untimed_ns'] for k in p_keys]

    ax1.bar(xs, send_v, width=0.58, color='#2563eb', label='发请求 (send)')
    ax1.bar(xs, bpoll_v, width=0.58, bottom=send_v, color='#60a5fa', label='有回复轮询 + 循环尾')
    ax1.bar(
        xs,
        epoll_v,
        width=0.58,
        bottom=[s + b for s, b in zip(send_v, bpoll_v)],
        color='#cbd5e1',
        label='空轮询等待 (empty poll)',
    )
    ax1.plot(xs, untimed_v, 'd', color='#dc2626', markersize=6, label='不计时 1/吞吐 (ns/条)')

    for x, k in zip(xs, p_keys):
        d = phases[k]
        top = max(d['send_ns'] + d['bpoll_ns'] + d['tail_ns'] + d['epoll_ns'], d['untimed_ns'])
        ax1.text(x, top + 4, f"K={d['k_busy']:.0f}", ha='center', va='bottom', fontsize=7, color='#334155')

    labels_a = [
        f"{CONFIG_SHORT[c]}\nW={w}{'(起)' if st_name == 'start-up' else ''}" for c, w, st_name in p_keys
    ]
    ax1.set_xticks(xs, labels_a, fontsize=7.2, rotation=25, ha='right')
    ax1.set_ylabel('每条消息耗时 (ns)')
    ax1.set_title('(a) 主机三段计时 vs 1/吞吐（柱顶 K = 每次非空轮询条数）', fontsize=9.5)
    ax1.legend(fontsize=7.5, loc='upper left', frameon=False)
    if untimed_v:
        ax1.set_ylim(0, max(untimed_v) * 1.18)

    # Panel (b): Accounting gap W/X - RTT vs directly measured turn-around (poll tail + loop tail + 0.5*send)
    # Focus on W=64 states + WC1/WC4 W=1024
    g_keys = [k for k in p_keys if k[1] == 64 or k[0] in ('WC1', 'WC4')]
    g_xs = list(range(len(g_keys)))
    w_bar = 0.35
    inferred_us = [phases[k]['acct_gap_ns'] / 1e3 for k in g_keys]
    poll_tail_us = [phases[k]['gap_poll_ns'] / 1e3 for k in g_keys]
    loop_tail_us = [phases[k]['tail_round_ns'] / 1e3 for k in g_keys]
    half_send_us = [phases[k]['half_send_ns'] / 1e3 for k in g_keys]

    ax2.bar([x - w_bar / 2 for x in g_xs], inferred_us, width=w_bar, color='#94a3b8', label='推算：W/吞吐 − 平均往返')
    ax2.bar([x + w_bar / 2 for x in g_xs], poll_tail_us, width=w_bar, color='#d95f02', label='实测①：同批轮询尾 (now − first_at)')
    ax2.bar(
        [x + w_bar / 2 for x in g_xs],
        half_send_us,
        width=w_bar,
        bottom=poll_tail_us,
        color='#2563eb',
        label='实测②：下轮半批发送等待',
    )
    ax2.bar(
        [x + w_bar / 2 for x in g_xs],
        loop_tail_us,
        width=w_bar,
        bottom=[p + h for p, h in zip(poll_tail_us, half_send_us)],
        color='#0f172a',
        label='实测③：循环尾 (tail)',
    )
    labels_b = [
        f"{CONFIG_SHORT[c]}\nW={w}{'(起)' if st_name == 'start-up' else ''}" for c, w, st_name in g_keys
    ]
    ax2.set_xticks(g_xs, labels_b, fontsize=7.2, rotation=25, ha='right')
    ax2.set_ylabel('记账时间差 (µs)')
    ax2.set_title('(b) 记账时间差：Little 定律推算 vs 分段实测', fontsize=9.5)
    ax2.legend(fontsize=7.2, loc='upper right', frameon=False)

    # Panel (c): Aligned --fake-card --fake-batch K
    ks = [1, 2, 4, 8, 32]
    for cores, color, marker, name in ((1, '#1f77b4', 'o', '假硬件 1核'), (4, '#16a34a', 's', '假硬件 4核')):
        ys_u = [fake[(cores, 64, k, False)]['ns'] for k in ks if (cores, 64, k, False) in fake]
        ks_u = [k for k in ks if (cores, 64, k, False) in fake]
        if ks_u:
            ax3.plot(ks_u, ys_u, f'-{marker}', color=color, lw=1.8, label=f'{name}（产品循环 W=64）')
            for k_val, y_val in zip(ks_u, ys_u):
                if k_val in (1, 4, 32) and cores == 1:
                    ax3.annotate(f'{y_val:.1f}', (k_val, y_val), textcoords='offset points', xytext=(0, 6), ha='center', fontsize=7.5, color=color)

    ax3.axhline(28.7, color='#64748b', ls=':', lw=1.0, label='旧假硬件 K=1 (28.7 ns)')
    ax3.axhline(13.2, color='#94a3b8', ls='--', lw=1.0, label='旧假硬件 K=32 (13.2 ns)')
    ax3.set_xscale('log', base=2)
    ax3.set_xticks(ks, [str(k) for k in ks])
    ax3.set_xlabel('每次轮询拿到的回复条数 K (--fake-batch)')
    ax3.set_ylabel('主机每条消息耗时 (ns)')
    ax3.set_ylim(0, 48)
    ax3.set_title('(c) 对齐后主机假硬件耗时 vs 批量 K', fontsize=9.5)
    ax3.legend(fontsize=7.2, loc='upper right', frameon=False)

    fig.suptitle('MRV-53 检查 2 & 3：主机三段计时、记账时间差实测分解与假硬件批量对齐（64 B，主机核 40 @ 3.9 GHz，uncore 2.2 GHz）', fontsize=10)
    return mrvplot.save(fig, 'mrv-53-phases')


if __name__ == '__main__':
    rows = load_all()
    phases = analyse_phases(rows)
    fake = analyse_fake(rows)
    print_tables(phases, fake)
    if phases and fake:
        print('wrote', make_figure(phases, fake))
