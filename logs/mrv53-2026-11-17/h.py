import datetime
import glob
import json
import os
import re
import shlex
import subprocess
import time

HOSTBIN = '/tmp/mrv53/t/release/inline_pingpong'
CARDBIN = '/root/bench/inline_reflector-mrv53'
CARD = 'root@10.10.0.2'
CARD_OUT = '/tmp/mrv53-card.out'


class Log:
    def __init__(self, d):
        self.dir, self.out, self.runs = d, d + '/out', d + '/runs.jsonl'
        os.makedirs(self.out, exist_ok=True)

    def next_id(self):
        return sum(1 for _ in open(self.runs)) + 1 if os.path.exists(self.runs) else 1

    def record(self, e):
        with open(self.runs, 'a') as f:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')


def sh(cmd, timeout=600):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def card(cmd, timeout=120):
    return sh(f"ssh -o ConnectTimeout=5 {CARD} {shlex.quote(cmd)}", timeout)


def freqs():
    r = {}
    for c in (40, 104):
        try:
            r[f'cpu{c}_khz'] = int(open(f'/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq').read())
            r[f'cpu{c}_gov'] = open(f'/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_governor').read().strip()
        except Exception as e:
            r[f'cpu{c}'] = str(e)
    for d in sorted(glob.glob('/sys/devices/system/cpu/intel_uncore_frequency/*/')):
        n = d.rstrip('/').split('/')[-1]
        try:
            r[n] = [int(open(d + x).read()) for x in ('min_freq_khz', 'max_freq_khz', 'current_freq_khz')]
        except Exception as e:
            r[n] = str(e)
    return r


HOST_RE = re.compile(r'window (\d+) rounds (\d+) lost (\d+) bad (\d+) torn (\d+) late (\d+) \(max ([\d.]+) us\) \| ([\d.]+) Mmsg/s \| RTT us: min ([\d.]+) p50 ([\d.]+) p90 ([\d.]+) p99 ([\d.]+) p99\.9 ([\d.]+) p99\.99 ([\d.]+) max ([\d.]+) mean ([\d.]+) \| polls (\d+)% empty')
KEYS = ['window', 'rounds', 'lost', 'bad', 'torn', 'late', 'late_max_us', 'mmsg', 'rtt_min', 'rtt_p50', 'rtt_p90', 'rtt_p99', 'rtt_p999', 'rtt_p9999', 'rtt_max', 'rtt_mean', 'empty_pct']


def parse_host(text):
    m = HOST_RE.search(text)
    if not m:
        return None
    return {k: (float(v) if '.' in v else int(v)) for k, v in zip(KEYS, m.groups())}


def parse_tagged(text, tag):
    for line in text.splitlines():
        if line.startswith(tag + ' '):
            return json.loads(line[len(tag) + 1:])
    return None


def edac():
    rc, so, se = card('cat /sys/devices/system/edac/mc/mc*/ue_count /sys/devices/system/edac/mc/mc*/ce_count | tr "\\n" " "; dmesg | grep -ci "sei\\|uer"')
    return so.strip()


def card_freq():
    rc, so, se = card('cat /sys/devices/system/cpu/cpu{3,4,5,6}/cpufreq/scaling_cur_freq | tr "\\n" " "')
    return so.strip()


def card_start(cargs, wait_s=40):
    full = f'{CARDBIN} {cargs}'
    rc, so, se = card(f'rm -f {CARD_OUT}; nohup setsid {full} > {CARD_OUT} 2>&1 < /dev/null & echo $!')
    pid = int(so.strip().split()[-1])
    t0 = time.time()
    while time.time() - t0 < wait_s:
        rc, so, se = card(f'cat {CARD_OUT}; kill -0 {pid} 2>/dev/null && echo ALIVE || echo DEAD')
        if 'hosts lease the slot' in so:
            return pid, full
        if so.strip().endswith('DEAD'):
            raise RuntimeError('card program died:\n' + so)
        time.sleep(0.5)
    raise RuntimeError('card program not ready:\n' + so)


def card_stop(pid, wait_s=20):
    card(f'kill -INT {pid}')
    t0 = time.time()
    while time.time() - t0 < wait_s:
        rc, so, se = card(f'kill -0 {pid} 2>/dev/null && echo ALIVE || echo DEAD')
        if 'DEAD' in so:
            break
        time.sleep(0.3)
    rc, so, se = card(f'cat {CARD_OUT}')
    return so


def host_run(log, group, tag, eal, app, extra=None, timeout=600):
    rid = log.next_id()
    cmd = f'{HOSTBIN} {eal} -- {app}'
    f0 = freqs()
    t0 = time.time()
    rc, so, se = sh(cmd, timeout=timeout)
    dt = time.time() - t0
    f1 = freqs()
    with open(f'{log.out}/{rid:04d}.host', 'w') as f:
        f.write(f'$ {cmd}\n--- stdout\n{so}\n--- stderr\n{se}\n')
    m = re.search(r'(\d+) polls waited on a missing reply', so)
    e = dict(id=rid, group=group, tag=tag, host_cmd=cmd, rc=rc, secs=round(dt, 2), host=parse_host(so),
             exp=parse_tagged(so, 'MRV53'), blocked=int(m.group(1)) if m else None, freq_before=f0, freq_after=f1,
             time=datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'))
    if extra:
        e.update(extra)
    log.record(e)
    return e, so, se


def brief(e):
    h = e.get('host') or {}
    x = e.get('exp') or {}
    s = dict(id=e['id'], tag=e['tag'], rc=e['rc'], mmsg=h.get('mmsg'), lbt=(h.get('lost'), h.get('bad'), h.get('torn')),
             rtt_mean=h.get('rtt_mean'), empty=h.get('empty_pct'), blocked=e.get('blocked'))
    if x and x.get('sampled'):
        hz = x['tsc_hz'] / 1e9
        msgs = max(x.get('gap_replies', 0), 1)
        s['busy_ns_per_msg'] = round((x['send_ticks'] + x['busy_poll_ticks']) / hz / max(msgs, 1), 1)
        s['per_poll'] = x['per_poll']
    return s
