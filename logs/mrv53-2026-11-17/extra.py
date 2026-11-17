"""MRV-53 extras with the experiment host H2: phase timing on the card (paired with H2's own
untimed runs) and the fake card sweep (no card)."""
import h
import matrix as M

FAKE_EAL = '-l 40 --no-pci --file-prefix=mrv53'


def timing_session(log, config, sess_no, settle_s=15):
    ws = M.CONFIGS[config][2]
    plan = [x for w in ws for x in (('H2', '', w), ('H2', '--account-every 64', w), ('H2', '', w),
                                    ('H2', '--account-every 64', w))]
    return M.session(log, config, 'C1', ('none', 0), plan, sess_no, settle_s=settle_s)


def fake_sweep(log, reps=2):
    rows = []
    for rep in range(reps):
        for cores in (1, 4):
            for w in (64, 1024):
                for k in (1, 2, 4, 8, 32):
                    for acct in ('', ' --account-every 64'):
                        app = f'--fake-card --fake-batch {k} --fake-cores {cores} -w {w} -n 20000000{acct}'
                        old = h.HOSTBIN
                        h.HOSTBIN = M.H2
                        try:
                            e, so, se = h.host_run(log, 'mrv53-fake', f'fake K={k} cores={cores} W={w}{acct}', FAKE_EAL, app,
                                                   extra=dict(fake_k=k, fake_cores=cores, W=w, hostbin='H2',
                                                              timed=bool(acct), rep=rep))
                        finally:
                            h.HOSTBIN = old
                        hh = e.get('host') or {}
                        if e['rc'] != 0 or not hh or (hh['lost'], hh['bad'], hh['torn']) != (0, 0, 0):
                            raise M.RunFailed(f"fake run {e['id']}: rc {e['rc']}\n{so[-600:]}\n{se[-600:]}")
                        rows.append((e['id'], k, cores, w, acct.strip(), hh['mmsg'], round(1e3 / hh['mmsg'], 2)))
                        print(rows[-1], flush=True)
    return rows
