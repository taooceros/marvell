"""MRV-53 start-up state: every datum is one fresh card program start and one host run right away
(the start-up state lasts ~6 s after the card program is ready)."""
import time

import h
import matrix as M

N_COLD = 5_000_000


def one(log, config, card, knob, hostname, hextra, w, sess_no):
    full_args = M.card_args(config, knob if card == 'C1' else ('none', 0))
    h.CARDBIN = M.C0 if card == 'C0' else M.C1
    pid, full = h.card_start(full_args)
    t_start = time.time()
    err, e = None, None
    try:
        hb = {'H0': M.H0, 'H1': M.H1, 'H2': M.H2}[hostname]
        tag = f'{config} W={w} start-up card {card} {knob[0]}={knob[1]}ns host {hostname} {hextra}'.strip()
        e = M.run(log, hb, tag, f'--reply ring -w {w} -n {N_COLD} {hextra}'.strip(),
                  dict(config=config, W=w, card=card, card_knob=knob[0], card_knob_ns=knob[1], hostbin=hostname,
                       host_extra=hextra, session=sess_no, card_cmd=full, state='start-up',
                       since_card_start_s=round(time.time() - t_start, 1)))
    except Exception as ex:
        err = ex
    finally:
        cout = h.card_stop(pid)
        with open(f'{log.out}/card-s{sess_no:03d}.txt', 'w') as f:
            f.write(f'$ {full}\n{cout}')
        h.CARDBIN = M.C1
    if err:
        raise err
    if 'corrupt payloads 0' not in cout or 'failed 0' not in cout:
        raise M.RunFailed(f'session {sess_no}: card reported corrupt payloads or failed copies:\n{cout[-1500:]}')
    print(f'  s{sess_no} {config} W={w} {card} {knob} {hostname} {hextra} -> {e["host"]["mmsg"]} M/s', flush=True)
    return e


def conditions(w):
    """(card, knob, host, host extra) for one W, interleaved so no condition sits at one end."""
    c = [('C1', ('none', 0), 'H0', '')]
    c += [('C1', ('delay', d), 'H0', '') for d in (30, 45, 60)]
    c += [('C1', ('none', 0), 'H1', f'--host-delay-ns {d}') for d in (10, 20, 40)]
    c += [('C1', ('none', 0), 'H0', '')]
    c += [('C1', ('hold', d), 'H0', '') for d in (0, 500, 1000, 2000)]
    c += [('C1', ('none', 0), 'H1', ''), ('C0', ('none', 0), 'H0', ''), ('C1', ('none', 0), 'H0', '')]
    return c
