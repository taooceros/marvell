"""MRV-53 matrix: card delay / card hold sessions with the product host (H0), host delay with the
product host + delay knob (H1), phase timing with the experiment host (H2). Every run is logged
via h.host_run; any failed or unverified run stops the matrix."""
import time

import h

H0 = '/tmp/mrv53/tp/release/inline_pingpong'   # product host (main b03acf9f)
H1 = '/tmp/mrv53/te/release/inline_pingpong'   # product host + --host-delay-ns (fenced multiply chain)
H2 = '/tmp/mrv53/t/release/inline_pingpong'    # experiment host (fake card, phase timing, stamps)
C0 = '/root/bench/inline_reflector'             # product card program
C1 = '/root/bench/inline_reflector-mrv53'       # experiment card program: --exp-delay-ns / --exp-hold-ns / --exp-stamp
EAL = '-l 40 -a 0000:99:02.0 --file-prefix=mrv53'
DPI_VFS = ['0000:06:00.1', '0000:06:00.2', '0000:06:00.3', '0000:06:00.4']
CARD_EAL = '-a 0002:01:00.2 --file-prefix=inl'
CONFIGS = {
    'WC1': (f'-l 3 {CARD_EAL}', '--store --store-wc', [1, 64, 1024]),
    'WC4': (f'-l 3-6 {CARD_EAL}', '--store --store-wc', [64, 1024]),
    'ST1': (f'-l 3 {CARD_EAL}', '--store', [1, 64, 1024]),
    'ST4': (f'-l 3-6 {CARD_EAL}', '--store', [64, 1024]),
    'DPI1': (f'-l 3 -a {DPI_VFS[0]} {CARD_EAL}', f'--dma {DPI_VFS[0]}', [1, 64, 1024]),
    'DPI4': (f'-l 3-6 {" ".join(f"-a {v}" for v in DPI_VFS)} {CARD_EAL}', '--dma ' + ','.join(DPI_VFS), [64, 1024]),
}


class RunFailed(RuntimeError):
    pass


def n_for(w):
    return 2_000_000 if w == 1 else 20_000_000


def run(log, hostbin, tag, app, extra):
    old = h.HOSTBIN
    h.HOSTBIN = hostbin
    try:
        e, so, se = h.host_run(log, 'mrv53', tag, EAL, app, extra=extra)
    finally:
        h.HOSTBIN = old
    hh = e.get('host') or {}
    if e['rc'] != 0 or not hh or (hh['lost'], hh['bad'], hh['torn']) != (0, 0, 0):
        raise RunFailed(f"run {e['id']} {tag}: rc {e['rc']} host {hh}\n{so[-800:]}\n{se[-800:]}")
    return e


def card_args(config, knob):
    eal, app, _ = CONFIGS[config]
    kind, ns = knob
    kn = {'none': '', 'delay': f' --exp-delay-ns {ns}', 'hold': f' --exp-hold-ns {ns}', 'stamp': ' --exp-stamp'}[kind]
    return f'{eal} -- {app}{kn}'


def session(log, config, card, knob, plan, sess_no, settle_s=0):
    """One card program start (`card` C0 = product, C1 = experiment with `knob` = (kind, ns));
    `plan` is a list of (host name, extra host args, W). Waits `settle_s` after the start."""
    full_args = card_args(config, knob if card == 'C1' else ('none', 0))
    h.CARDBIN = C0 if card == 'C0' else C1
    pid, full = h.card_start(full_args)
    t_start = time.time()
    if settle_s:
        time.sleep(settle_s)
    rows, err = [], None
    try:
        for hname, hextra, w in plan:
            hb = {'H0': H0, 'H1': H1, 'H2': H2}[hname]
            tag = f'{config} W={w} card {card} {knob[0]}={knob[1]}ns host {hname} {hextra}'.strip()
            e = run(log, hb, tag, f'--reply ring -w {w} -n {n_for(w)} {hextra}'.strip(),
                    dict(config=config, W=w, card=card, card_knob=knob[0], card_knob_ns=knob[1], hostbin=hname,
                         host_extra=hextra, session=sess_no, card_cmd=full,
                         since_card_start_s=round(time.time() - t_start, 1)))
            rows.append((e['id'], w, hname, hextra, e['host']['mmsg'], e['since_card_start_s']))
    except Exception as ex:  # stop the card program, then re-raise
        err = ex
    finally:
        cout = h.card_stop(pid)
        with open(f'{log.out}/card-s{sess_no:03d}.txt', 'w') as f:
            f.write(f'$ {full}\n{cout}')
        h.CARDBIN = C1
    ok = 'corrupt payloads 0' in cout and 'failed 0' in cout
    print(f'session {sess_no} {config} {card} {knob} card-ok={ok}', flush=True)
    for r in rows:
        print('   ', r, flush=True)
    if err:
        raise err
    if not ok:
        raise RunFailed(f'session {sess_no}: card reported corrupt payloads or failed copies:\n{cout[-1500:]}')
    return rows
