"""Complete the remaining MRV-53 runs from session 416 onwards."""
import sys
import time

sys.path.insert(0, '/tmp/mrv53')
import cold as C
import extra as E
import h
import matrix as M

LOG = h.Log('/home/hongtao/dev/marvell-mrv35/logs/mrv53-2026-11-17')


def cleanup_card():
    rc, so, se = h.card('for p in $(pgrep -f inline_reflector); do kill -INT $p; done; sleep 2; pgrep -fa inline_reflector | grep -v pgrep || true')
    print('cleanup_card:', so.strip(), flush=True)


def run_steady_config(config, sess_start, settle_s=10, skip_first=0):
    Ws = M.CONFIGS[config][2]
    base2 = [('H0', '', w) for w in Ws for _ in range(2)]
    hseq = ['', '10', '20', '40', '', '40', '20', '10', '']
    hostd = [x for w in Ws for x in ([('H0', '', w)] + [('H1', f'--host-delay-ns {d}' if d else '', w) for d in hseq] + [('H0', '', w)])]
    plan = [('C0', ('none', 0), base2), ('C1', ('none', 0), hostd)]
    plan += [('C1', ('delay', d), base2) for d in (30, 45, 60)]
    plan += [('C1', ('hold', d), base2) for d in (0, 500, 1000, 2000)]
    plan += [('C1', ('none', 0), base2)]
    sess_no = sess_start
    for idx, (card, knob, p) in enumerate(plan):
        sess_no += 1
        if idx < skip_first:
            continue
        M.session(LOG, config, card, knob, p, sess_no, settle_s=settle_s)
    return sess_no


def main():
    t0 = time.time()
    cleanup_card()
    print('Initial freqs:', h.freqs(), 'card:', h.card_freq(), 'edac:', h.edac(), flush=True)

    # 1. Finish WC4 steady-state (sessions 411..415 are already done: skip_first=5 -> runs 416..420)
    print('=== Finishing WC4 steady-state (sessions 416..420) ===', flush=True)
    run_steady_config('WC4', 410, settle_s=15, skip_first=5)

    # 2. Run WC1 W=64 start-up state (cold.py, sessions 501..514)
    print('=== Running WC1 W=64 start-up state (sessions 501..514) ===', flush=True)
    s_cold = 500
    for card, knob, hostname, hextra in C.conditions(64):
        s_cold += 1
        C.one(LOG, 'WC1', card, knob, hostname, hextra, 64, s_cold)

    # 3. Run H2 3-phase timing on WC1 (start-up W=64 + steady-state) and WC4 (steady-state)
    print('=== Running H2 phase timing on WC1 (start-up & steady) and WC4 ===', flush=True)
    # 600: WC1 W=64 start-up timing (1 untimed + 1 timed in separate fresh starts)
    C.one(LOG, 'WC1', 'C1', ('none', 0), 'H2', '', 64, 599)
    C.one(LOG, 'WC1', 'C1', ('none', 0), 'H2', '--account-every 64', 64, 600)
    E.timing_session(LOG, 'WC1', 601, settle_s=10)
    E.timing_session(LOG, 'WC4', 602, settle_s=10)

    # 4. Unload pem_wc before ST1, ST4, DPI1, DPI4
    rc, so, se = h.card('rmmod pem_wc; lsmod | grep -c pem_wc || true')
    print('rmmod pem_wc ->', rc, so.strip(), se.strip(), flush=True)

    # 5. Steady-state matrix for ST1 (421..430), ST4 (431..440), DPI1 (441..450), DPI4 (451..460)
    sess = 420
    for cfg in ('ST1', 'ST4', 'DPI1', 'DPI4'):
        print(f'=== Running {cfg} steady-state ===', flush=True)
        sess = run_steady_config(cfg, sess, settle_s=10, skip_first=0)

    # 6. H2 phase timing for ST1 (603), ST4 (604), DPI1 (605), DPI4 (606)
    for idx, cfg in enumerate(('ST1', 'ST4', 'DPI1', 'DPI4'), start=603):
        print(f'=== Running {cfg} H2 phase timing (session {idx}) ===', flush=True)
        E.timing_session(LOG, cfg, idx, settle_s=10)

    # 7. Fake-card sweep (no card)
    print('=== Running fake-card sweep ===', flush=True)
    E.fake_sweep(LOG, reps=2)

    print('ALL DONE in', round(time.time() - t0, 1), 's | edac:', h.edac(), flush=True)


if __name__ == '__main__':
    main()
