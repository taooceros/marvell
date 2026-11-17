"""MRV-24 harness cells as they ran in the agent session, in order: definitions (EV event
encodings, run/derive, card() which restarted the card's inline_reflector over ssh), the
cells that ran R00-R45 and appended them to runs.jsonl, and a last cell computing
instructions and cycles per message from the results. Not runnable on its own."""

# %% cell 1: 2026-11-16T23:56:18Z toolu_01MqJLjBtoeczAgTvwTNi2MW Harness: run inline_stream under perf stat intervals
import subprocess, re, os, time, json, shlex
PERF='/nix/store/hln3gq5s0pajpqlwcffqadzxglqfhx29-perf-linux-7.3.4/bin/perf'
TB='/home/hongtao/dev/marvell/testbed'
BIN=TB+'/target/release/inline_stream'
EAL='-a 0000:99:02.0 --file-prefix=mrv24 --log-level=lib.eal:error'
LOG='/tmp/mrv24/runs.jsonl'

EV = {
 # IIO (per stack, summed per socket)
 'iio_mmio_wr_txn': 'uncore_iio/event=0xc1,ch_mask=0xff,fc_mask=7,umask=1/',
 'iio_inb_wr_txn':  'uncore_iio/event=0x84,ch_mask=0xff,fc_mask=7,umask=1/',
 'iio_ob_tlp_to_io':'uncore_iio/event=0xd1,ch_mask=0xff,fc_mask=7,umask=8/',
 'iio_clk':         'uncore_iio/event=0x01/',
 # IRP outbound request queue (towards devices)
 'irp_txs_ncb':     'uncore_irp/event=0x0d/',
 'irp_txs_occ':     'uncore_irp/event=0x0c/',
 'irp_clk':         'uncore_irp/event=0x01/',
 # M2PCIe credits into IIO (outbound NCB = MMIO writes)
 'm2p_rej_ncb':     'uncore_m2pcie/event=0x34,umask=0x10/',
 'm2p_acq_ncb':     'uncore_m2pcie/event=0x33,umask=0x0c/',
 'm2p_rxc_ins_cha_ncb':'uncore_m2pcie/event=0x11,umask=0x02/',
 'm2p_rxc_ne_cha_ncb': 'uncore_m2pcie/event=0x10,umask=0x02/',
 'm2p_clk':         'uncore_m2pcie/event=0x01/',
 # CHA TOR
 'cha_ins_wcilf':   'uncore_cha/event=0x35,umask=0xc867ff01/',
 'cha_occ_wcilf':   'uncore_cha/event=0x36,umask=0xc867ff01/',
 'cha_ins_wcil':    'uncore_cha/event=0x35,umask=0xc86fff01/',
 'cha_ins_itom':    'uncore_cha/event=0x35,umask=0xcc43ff04/',
 'cha_occ_itom':    'uncore_cha/event=0x36,umask=0xcc43ff04/',
 'cha_clk':         'uncore_cha/event=0x01/',
}

def perf_run(cmd, events, interval=200, socket='S1'):
    names=list(events)
    evstr=','.join(EV[n] if n in EV else n for n in names)
    full=f"{PERF} stat -a --per-socket -I {interval} -x ';' -e '{evstr}' -- {cmd}"
    p=subprocess.run(full, shell=True, capture_output=True, text=True, cwd=TB)
    out=p.stdout; err=p.stderr
    # parse intervals: time;socket;cpus;count;unit;event;runtime;pct;...
    rows={}
    for line in err.splitlines():
        f=line.split(';')
        if len(f)<6: continue
        try: t=float(f[0])
        except: continue
        sock=f[1]; 
        if sock!=socket: continue
        cnt=f[3]; ev=f[5]
        try: c=float(cnt)
        except: c=float('nan')
        rows.setdefault(t,{})[ev]=c
    # map event strings back to names
    evmap={ (EV[n] if n in EV else n): n for n in names}
    series=[]
    for t in sorted(rows):
        d={}
        for k,v in rows[t].items():
            nm=evmap.get(k, k)
            d[nm]=v
        series.append((t,d))
    return out, err, series

def steady(series, key, frac=0.6):
    vals=[d.get(key,0) for t,d in series]
    if not vals: return []
    mx=max(vals)
    return [d for (t,d) in series if d.get(key,0)>=frac*mx]

def summarize(sel, interval_s):
    if not sel: return {}
    keys=set().union(*[d.keys() for d in sel])
    tot={k: sum(d.get(k,0) for d in sel) for k in keys}
    secs=len(sel)*interval_s
    return tot, secs
print('ok')

# %% cell 2: 2026-11-17T00:05:05Z toolu_011mk2yFiuvKFd7PPDE17zkX Counter harness v2 (nested perf: uncore + core)
import subprocess, json, math
U_EVENTS = ['iio_mmio_wr_txn','iio_inb_wr_txn','iio_ob_tlp_to_io','iio_clk',
            'irp_txs_ncb','irp_txs_occ',
            'm2p_rej_ncb','m2p_acq_ncb','m2p_rxc_ins_cha_ncb','m2p_rxc_ne_cha_ncb',
            'cha_ins_wcilf','cha_occ_wcilf','cha_ins_itom','cha_occ_itom']

def parse_u(path, socket='S1'):
    rows={}; pct={}
    evmap={EV[n]:n for n in EV}
    for line in open(path):
        f=line.strip().split(';')
        if len(f)<7: continue
        try: t=float(f[0])
        except: continue
        if f[1]!=socket: continue
        ev=f[5]; nm=evmap.get(ev,ev)
        try: c=float(f[3])
        except: c=float('nan')
        rows.setdefault(t,{})[nm]=c
        try: pct.setdefault(nm,[]).append(float(f[7]))
        except: pass
    return [(t,rows[t]) for t in sorted(rows)], {k:min(v) for k,v in pct.items()}

def parse_c(path):
    c={}
    for line in open(path):
        f=line.strip().split(';')
        if len(f)<4 or not f[0].startswith('CPU'): continue
        try: c[(f[0],f[3].split(':')[0])]=float(f[1])
        except: pass
    out={}
    for (cpu,ev),v in c.items():
        if ev=='cycles' and (cpu,'ref-cycles') in c:
            out[cpu]={'ghz': v/c[(cpu,'ref-cycles')]*2.2, 'cycles':v, 'instr':c.get((cpu,'instructions'))}
    return out

def run(label, args, cpus='40,41', events=U_EVENTS, key='iio_mmio_wr_txn', interval=100):
    evstr=','.join(EV[e] for e in events)
    cmd=(f"{PERF} stat -a --per-socket -I {interval} -x';' -e '{evstr}' -o /tmp/mrv24/u.txt -- "
         f"{PERF} stat -C {cpus} -A -x';' -e cycles,ref-cycles,instructions -o /tmp/mrv24/c.txt -- "
         f"{BIN} {args}")
    p=subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=TB)
    lines=[l for l in (p.stdout+p.stderr).splitlines() if l.startswith(('h2c','c2h','bidir','split')) or 'rror' in l]
    series,pct=parse_u('/tmp/mrv24/u.txt')
    core=parse_c('/tmp/mrv24/c.txt')
    vals=[d.get(key,0) for t,d in series]
    mx=max(vals) if vals else 0
    sel=[d for (t,d) in series if mx>0 and d.get(key,0)>=0.7*mx]
    secs=len(sel)*interval/1000
    tot={k: sum(d.get(k,0) for d in sel) for k in events}
    rec={'label':label,'args':args,'cpus':cpus,'out':lines,'secs':secs,'tot':tot,'pct':pct,'core':core,'t':time.time()}
    with open(LOG,'a') as f: f.write(json.dumps(rec)+'\n')
    return rec

def derive(rec):
    t=rec['tot']; s=rec['secs'] or float('nan')
    g=lambda k: t.get(k,0)
    d={}
    d['mmio_wr M/s']=g('iio_mmio_wr_txn')/s/1e6
    d['inb_wr M/s']=g('iio_inb_wr_txn')/s/1e6
    d['ob_tlp M/s']=g('iio_ob_tlp_to_io')/s/1e6
    d['iio_clk GHz']=g('iio_clk')/s/1e9
    d['irp_txs_ncb M/s']=g('irp_txs_ncb')/s/1e6
    d['irp_txs avg occ']=g('irp_txs_occ')/g('iio_clk') if g('iio_clk') else float('nan')
    d['irp_txs lat clk']=g('irp_txs_occ')/g('irp_txs_ncb') if g('irp_txs_ncb') else float('nan')
    d['m2p_acq_ncb M/s']=g('m2p_acq_ncb')/s/1e6
    d['m2p_rej_ncb M/s']=g('m2p_rej_ncb')/s/1e6
    d['m2p_rxc_ins_cha_ncb M/s']=g('m2p_rxc_ins_cha_ncb')/s/1e6
    d['m2p_rxc_ne_cha_ncb frac']=g('m2p_rxc_ne_cha_ncb')/(s*2.2e9)
    d['cha_wcilf M/s']=g('cha_ins_wcilf')/s/1e6
    d['cha_wcilf lat ns']=g('cha_occ_wcilf')/g('cha_ins_wcilf')/2.2 if g('cha_ins_wcilf') else float('nan')
    d['cha_itom M/s']=g('cha_ins_itom')/s/1e6
    d['cha_itom lat ns']=g('cha_occ_itom')/g('cha_ins_itom')/2.2 if g('cha_ins_itom') else float('nan')
    return d

def show(rec):
    print('==', rec['label'], '|', rec['args'])
    for l in rec['out']: print('  ', l.replace(' (','\n     (') if False else l)
    print('   core:', {k:(round(v['ghz'],2)) for k,v in rec['core'].items()}, ' steady secs', rec['secs'], ' min pct', {k:v for k,v in rec['pct'].items() if v<99.9})
    d=derive(rec)
    print('   ', '  '.join(f"{k}={v:.3g}" for k,v in d.items()))

E2='-a 0000:99:02.0 --file-prefix=mrv24 --log-level=lib.eal:error'
r=run('test h2c alone s40 (1+1 wc)', f'-l 40 {E2} -- --dir h2c -n 200000000', cpus='40,41')
show(r)

# %% cell 3: 2026-11-17T00:05:40Z toolu_011f2rU1wH9upYcbq7QnmHby 1+1 WC: h2c alone vs bidir counters
EVS=['iio_mmio_wr_txn','iio_inb_wr_txn','m2p_rej_ncb','m2p_acq_ncb','m2p_rxc_ins_cha_ncb','m2p_rxc_ne_cha_ncb','cha_ins_wcilf','cha_occ_wcilf','cha_ins_itom','cha_occ_itom']
N=200000000
res_11wc=[]
for lab,args,key in [
    ('1+1wc h2c alone s40', f'-l 40 {E2} -- --dir h2c -n {N}', 'iio_mmio_wr_txn'),
    ('1+1wc c2h alone r41', f'-l 41 {E2} -- --dir c2h -n {N}', 'iio_inb_wr_txn'),
    ('1+1wc bidir s40 r41', f'-l 40,41 --main-lcore 41 {E2} -- --dir bidir -n {N}', 'iio_mmio_wr_txn'),
    ('1+1wc bidir s41 r40', f'-l 40,41 {E2} -- --dir bidir -n {N}', 'iio_mmio_wr_txn'),
]:
    time.sleep(3)
    r=run(lab,args,cpus='40,41',events=EVS,key=key)
    res_11wc.append(r); show(r)

# %% cell 4: 2026-11-17T00:08:41Z toolu_01C1foHDznKjjPrhxjs6ynWC Card reflector restart helper
def card(flags, tag):
    """Stop the running inline_reflector (Ctrl-C, keep its summary), start a new one with flags."""
    script = f'''
tmux send-keys -t inl C-c 2>/dev/null; sleep 3
if [ -f /tmp/inl.log ]; then grep -aE "^(reflected|per core|stream writing|fetched)" /tmp/inl.log | sed "s/^/  prev: /"; cp /tmp/inl.log /tmp/inl_prev_$(date +%s).log; fi
tmux kill-session -t inl 2>/dev/null; sleep 1
tmux new-session -d -s inl "/root/bench/inline_reflector {flags} 2>&1 | tee -i /tmp/inl.log"
for i in $(seq 1 20); do grep -aq "BAR4 slot" /tmp/inl.log && break; sleep 1; done
grep -aE "BAR4 slot|rror|DMAs|fetches|stores" /tmp/inl.log | sed "s/^/  new: /"
'''
    p=subprocess.run(['ssh','root@10.10.0.2',script],capture_output=True,text=True)
    print(f'[card {tag}]'); print(p.stdout[-3000:], p.stderr[-500:])

card('-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,ring,source,source --store --store-wc', '2+2 wc')

# %% cell 5: 2026-11-17T00:08:53Z toolu_018EevUCMwx9mVzZ2w27kyGR 2+2 WC: host-limited h2c alone vs bidir counters
res_22wc=[]
for lab,args,cpus,key in [
    ('2+2wc h2c alone s40', f'-l 40 {E2} -- --dir h2c -n {N}', '40,41', 'iio_mmio_wr_txn'),
    ('2+2wc c2h alone r41', f'-l 41 {E2} -- --dir c2h -n {N}', '40,41', 'iio_inb_wr_txn'),
    ('2+2wc bidir s40 r41', f'-l 40,41 --main-lcore 41 {E2} -- --dir bidir -n {N}', '40,41', 'iio_mmio_wr_txn'),
    ('2+2wc h2c alone s104', f'-l 104 {E2} -- --dir h2c -n {N}', '41,104', 'iio_mmio_wr_txn'),
    ('2+2wc bidir s104 r41', f'-l 41,104 {E2} -- --dir bidir -n {N}', '41,104', 'iio_mmio_wr_txn'),
]:
    time.sleep(2)
    r=run(lab,args,cpus=cpus,events=EVS,key=key)
    res_22wc.append(r); show(r)

# %% cell 6: 2026-11-17T00:10:52Z toolu_01H8JNiU4ougHG9zHgBYM9Xe 2+2 WC: card-side per-core rates alone vs bidir
def host(args, label):
    p=subprocess.run(f"{BIN} {args}", shell=True, capture_output=True, text=True, cwd=TB)
    lines=[l for l in (p.stdout+p.stderr).splitlines() if l.startswith(('h2c','c2h','bidir','split')) or 'rror' in l]
    rec={'label':label,'args':args,'out':lines,'t':time.time()}
    with open(LOG,'a') as f: f.write(json.dumps(rec)+'\n')
    print(f'  [{label}]', *[re.sub(r' \(([0-9.]+) GB/s of 64 B cells\)','',l) for l in lines])
    return rec

C22='-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,ring,source,source --store --store-wc'
card(C22,'2+2 wc fresh')
host(f'-l 41 {E2} -- --dir c2h -n {N}', '2+2wc c2h alone r41')
card(C22,'2+2 wc (summary = c2h alone)')
host(f'-l 40 {E2} -- --dir h2c -n {N}', '2+2wc h2c alone s40')
card(C22,'2+2 wc (summary = h2c alone)')
host(f'-l 40,41 --main-lcore 41 {E2} -- --dir bidir -n {N}', '2+2wc bidir s40 r41')
card(C22,'2+2 wc (summary = bidir)')

# %% cell 7: 2026-11-17T00:12:06Z toolu_01LAFSpH8h94VTuoFVY6HZXm 1+1 DPI c2h: h2c alone vs bidir (fixed and cold sender)
EVS2=['iio_mmio_wr_txn','iio_inb_wr_txn','m2p_rej_ncb','m2p_acq_ncb','m2p_rxc_ins_cha_ncb','m2p_rxc_ne_cha_ncb','cha_ins_wcilf','cha_occ_wcilf','cha_ins_itom','cha_occ_itom']
C11D='-l 3,4 -a 0002:01:00.2 -a 0000:06:00.1 -a 0000:06:00.2 --file-prefix=inl -- --roles ring,source --dma 0000:06:00.1,0000:06:00.2'
card(C11D,'1+1 dpi')
res_11d=[]
for lab,args,cpus,key in [
    ('1+1dpi h2c alone s40', f'-l 40 {E2} -- --dir h2c -n {N}', '40,41', 'iio_mmio_wr_txn'),
    ('1+1dpi c2h alone r41', f'-l 41 {E2} -- --dir c2h -n {N}', '40,41', 'iio_inb_wr_txn'),
    ('1+1dpi bidir s40 r41', f'-l 40,41 --main-lcore 41 {E2} -- --dir bidir -n {N}', '40,41', 'iio_mmio_wr_txn'),
]:
    time.sleep(2)
    r=run(lab,args,cpus=cpus,events=EVS2,key=key); res_11d.append(r); show(r)
time.sleep(12)
r=run('1+1dpi bidir s47cold r40', f'-l 40,47 {E2} -- --dir bidir -n 50000000', cpus='40,47', events=EVS2, key='iio_mmio_wr_txn'); res_11d.append(r); show(r)

# %% cell 8: 2026-11-17T00:13:31Z toolu_016yfmxKEyBtq4Zd3RvPgUDs Plain-store c2h and fetch control
C11S='-l 3,4 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,source --store'
card(C11S,'1+1 device store')
res_11s=[]
for lab,args,cpus,key in [
    ('1+1store h2c alone s40', f'-l 40 {E2} -- --dir h2c -n {N}', '40,41', 'iio_mmio_wr_txn'),
    ('1+1store c2h alone r41', f'-l 41 {E2} -- --dir c2h -n 50000000', '40,41', 'iio_inb_wr_txn'),
    ('1+1store bidir s40 r41', f'-l 40,41 --main-lcore 41 {E2} -- --dir bidir -n 50000000', '40,41', 'iio_mmio_wr_txn'),
]:
    time.sleep(2)
    r=run(lab,args,cpus=cpus,events=EVS2,key=key); res_11s.append(r); show(r)

EVF=['iio_mmio_wr_txn','iio_inb_wr_txn','m2p_rej_ncb','m2p_acq_ncb','m2p_rxc_ins_cha_ncb','m2p_rxc_ne_cha_ncb','cha_ins_itom','cha_occ_itom']
EV['iio_inb_rd_txn']='uncore_iio/event=0x84,ch_mask=0xff,fc_mask=7,umask=4/'
EV['cha_ins_rdcur']='uncore_cha/event=0x35,umask=0xc8f3ff04/'
EV['cha_occ_rdcur']='uncore_cha/event=0x36,umask=0xc8f3ff04/'
EVF=['iio_mmio_wr_txn','iio_inb_wr_txn','iio_inb_rd_txn','m2p_rej_ncb','cha_ins_itom','cha_occ_itom','cha_ins_rdcur','cha_occ_rdcur']
C11F='-l 3,4 -a 0002:01:00.2 -a 0000:06:00.3 --file-prefix=inl -- --roles fetch,source --fetch-dma 0000:06:00.3 --store --store-wc'
card(C11F,'fetch+source wc')
res_11f=[]
for lab,args,cpus,key in [
    ('fetch h2c alone s40', f'-l 40 {E2} -- --dir h2c --via fetch -n {N}', '40,41', 'iio_inb_rd_txn'),
    ('fetch c2h alone r41', f'-l 41 {E2} -- --dir c2h -n {N}', '40,41', 'iio_inb_wr_txn'),
    ('fetch bidir s40 r41', f'-l 40,41 --main-lcore 41 {E2} -- --dir bidir --via fetch -n {N}', '40,41', 'iio_inb_rd_txn'),
]:
    time.sleep(2)
    r=run(lab,args,cpus=cpus,events=EVF,key=key); res_11f.append(r)
    print('==',lab); [print('  ',re.sub(r' \(([0-9.]+) GB/s of 64 B cells\)','',l)) for l in r['out']]
    t=r['tot']; s=r['secs']
    print('   core', {k:round(v['ghz'],2) for k,v in r['core'].items()}, 'inb_wr M/s %.1f inb_rd M/s %.1f mmio_wr M/s %.2f m2p_rej %.3g itom lat %.1f ns rdcur M/s %.1f rdcur lat %.1f ns pct %s'%(
        t['iio_inb_wr_txn']/s/1e6, t['iio_inb_rd_txn']/s/1e6, t['iio_mmio_wr_txn']/s/1e6, t['m2p_rej_ncb']/s, 
        (t['cha_occ_itom']/t['cha_ins_itom']/2.2 if t['cha_ins_itom'] else float('nan')), t['cha_ins_rdcur']/s/1e6,
        (t['cha_occ_rdcur']/t['cha_ins_rdcur']/2.2 if t['cha_ins_rdcur'] else float('nan')), {k:v for k,v in r['pct'].items() if v<99.9}))

# %% cell 9: 2026-11-17T00:14:59Z toolu_016cYG6Hz8C7DECiaJNGhvVx Split mode: DPI and WC replies, fixed vs cold sender
def corerun(label, args, cpus):
    cmd=f"{PERF} stat -C {cpus} -A -x';' -e cycles,ref-cycles,instructions -o /tmp/mrv24/c.txt -- {BIN} {args}"
    p=subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=TB)
    lines=[l for l in (p.stdout+p.stderr).splitlines() if l.startswith(('h2c','c2h','bidir','split')) or 'rror' in l]
    core=parse_c('/tmp/mrv24/c.txt')
    rec={'label':label,'args':args,'out':lines,'core':core,'t':time.time()}
    with open(LOG,'a') as f: f.write(json.dumps(rec)+'\n')
    print(f'== {label}'); [print('  ',re.sub(r' \(([0-9.]+) GB/s of 64 B cells\)','',l)) for l in lines]
    print('   core', {k:round(v['ghz'],2) for k,v in core.items()})
    return rec

card('-l 3 -a 0002:01:00.2 -a 0000:06:00.1 --file-prefix=inl -- --dma 0000:06:00.1', '1 ring dpi')
NS=50000000
time.sleep(12)
corerun('split dpi1 s41cold r40', f'-l 40,41 {E2} -- --dir split -n {NS}', '40,41')
corerun('split dpi1 s41warm r40', f'-l 40,41 {E2} -- --dir split -n {NS}', '40,41')
corerun('split dpi1 s40 r41', f'-l 40,41 --main-lcore 41 {E2} -- --dir split -n {NS}', '40,41')
corerun('split dpi1 s40 r41 again', f'-l 40,41 --main-lcore 41 {E2} -- --dir split -n {NS}', '40,41')

# %% cell 10: 2026-11-17T00:16:08Z toolu_011o4o9yokNFuQDpUTukdSoG Closed loop W sweep, WC replies, 4 card cores
PP=TB+'/target/release/inline_pingpong'
def pprun(label, args, cpus):
    cmd=f"{PERF} stat -C {cpus} -A -x';' -e cycles,ref-cycles -o /tmp/mrv24/c.txt -- {PP} {args}"
    p=subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=TB)
    out=(p.stdout+p.stderr).splitlines()
    lines=[l for l in out if re.search(r'M (msg|cells|round)|RTT|rror|bad|torn|Mmsg|M/s', l)]
    core=parse_c('/tmp/mrv24/c.txt')
    rec={'label':label,'args':args,'out':lines,'core':core,'t':time.time()}
    with open(LOG,'a') as f: f.write(json.dumps(rec)+'\n')
    print(f'== {label}', {k:round(v['ghz'],2) for k,v in core.items()})
    for l in lines[-4:]: print('   ', l[:400])
    return rec

card('-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --store --store-wc', '4 ring wc')
for w in [64, 256, 512, 1024]:
    pprun(f'closed W={w} c40', f'-l 40 {E2} -- --reply ring -w {w} -n 5000000', '40')

# %% cell 11: 2026-11-17T00:16:51Z toolu_01XMapXJ1txRsQtTXTokjDuU Split with WC replies (4 card cores), fixed vs cold sender
time.sleep(12)
corerun('split wc4 s41cold r40', f'-l 40,41 {E2} -- --dir split -n {NS}', '40,41')
corerun('split wc4 s40 r41', f'-l 40,41 --main-lcore 41 {E2} -- --dir split -n {NS}', '40,41')
corerun('split wc4 s40 r41 again', f'-l 40,41 --main-lcore 41 {E2} -- --dir split -n {NS}', '40,41')

# %% cell 12: 2026-11-17T00:17:19Z toolu_016eTt9izyttpAz76a34bmCx Active-stack IIO counters incl. inbound arbitration (2+2 WC)
IIOA={'c1':'uncore_iio/event=0xc1,ch_mask=0xff,fc_mask=7,umask=1/','84':'uncore_iio/event=0x84,ch_mask=0xff,fc_mask=7,umask=1/',
      'req_wr':'uncore_iio/event=0x86,ch_mask=0xff,fc_mask=7,umask=0x10/','won_wr':'uncore_iio/event=0x87,ch_mask=0xff,fc_mask=7,umask=0x10/'}
IIOB={'req_data':'uncore_iio/event=0x86,ch_mask=0xff,fc_mask=7,umask=0x20/','won_data':'uncore_iio/event=0x87,ch_mask=0xff,fc_mask=7,umask=0x20/',
      'req_own':'uncore_iio/event=0x86,ch_mask=0xff,fc_mask=7,umask=4/','won_own':'uncore_iio/event=0x87,ch_mask=0xff,fc_mask=7,umask=4/'}
def perbox(label, args, evs, secs_key):
    evstr=','.join(evs.values())
    cmd=f"{PERF} stat -a --per-socket --no-merge -x';' -e '{evstr}' -o /tmp/mrv24/nb.txt -- {BIN} {args}"
    p=subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=TB)
    lines=[re.sub(r' \(([0-9.]+) GB/s of 64 B cells\)','',l) for l in (p.stdout+p.stderr).splitlines() if l.startswith(('h2c','c2h','bidir','split')) or 'rror' in l]
    inv={v:k for k,v in evs.items()}
    tab={}
    for line in open('/tmp/mrv24/nb.txt'):
        f=line.strip().split(';')
        if len(f)<5 or f[0]!='S1': continue
        ev=f[4]
        m=re.match(r'(uncore_iio_\d+)/(.*)/$', ev) or re.match(r'(uncore_iio_\d+)(.*)', ev)
        # perf prints e.g. "uncore_iio_5/event=0xc1,.../" ; normalise
        box=re.search(r'uncore_iio_\d+', ev).group(0) if re.search(r'uncore_iio_\d+', ev) else ev
        key=None
        for k,v in evs.items():
            if v.replace('uncore_iio/','').rstrip('/') in ev: key=k
        try: c=float(f[2])
        except: c=float('nan')
        tab.setdefault(box,{})[key]=c
    print('==',label); [print('  ',l) for l in lines]
    for box,d in sorted(tab.items()):
        if any(v>1e6 for v in d.values() if v==v): print('   ',box, {k:f'{v:.3e}' for k,v in d.items()})
    rec={'label':label,'args':args,'out':lines,'perbox':tab,'t':time.time()}
    with open(LOG,'a') as f: f.write(json.dumps(rec)+'\n')
    return tab

card(C22,'2+2 wc for per-box')
for lab,args in [('h2c alone s40', f'-l 40 {E2} -- --dir h2c -n {N}'),
                 ('c2h alone r41', f'-l 41 {E2} -- --dir c2h -n {N}'),
                 ('bidir s40 r41', f'-l 40,41 --main-lcore 41 {E2} -- --dir bidir -n {N}')]:
    for nm,evs in [('A',IIOA),('B',IIOB)]:
        time.sleep(1)
        perbox(f'2+2wc {lab} [{nm}]', args, evs, None)

# %% cell 13: 2026-11-17T00:18:25Z toolu_019d47yy7KARcNkj3XgRNd8C Per-box IIO counters on card stack (fixed parser)
IIOA={'c1':'uncore_iio/event=0xc1,ch_mask=0xff,fc_mask=7,umask=1/','84':'uncore_iio/event=0x84,ch_mask=0xff,fc_mask=7,umask=1/',
      'req_own':'uncore_iio/event=0x86,ch_mask=0xff,fc_mask=7,umask=4/','won_own':'uncore_iio/event=0x87,ch_mask=0xff,fc_mask=7,umask=4/'}
IIOB={'req_wr':'uncore_iio/event=0x86,ch_mask=0xff,fc_mask=7,umask=0x10/','won_wr':'uncore_iio/event=0x87,ch_mask=0xff,fc_mask=7,umask=0x10/',
      'req_fin':'uncore_iio/event=0x86,ch_mask=0xff,fc_mask=7,umask=8/','won_fin':'uncore_iio/event=0x87,ch_mask=0xff,fc_mask=7,umask=8/'}
def perbox2(label, args, evs):
    evstr=','.join(evs.values())
    cmd=f"{PERF} stat -a --no-merge -x';' -e '{evstr}' -o /tmp/mrv24/nb.txt -- {BIN} {args}"
    p=subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=TB)
    lines=[re.sub(r' \(([0-9.]+) GB/s of 64 B cells\)','',l) for l in (p.stdout+p.stderr).splitlines() if l.startswith(('h2c','c2h','bidir','split')) or 'rror' in l]
    tab={}; rt={}
    for line in open('/tmp/mrv24/nb.txt'):
        f=line.strip().split(';')
        if len(f)<6 or f[0]!='CPU32': continue
        m=re.match(r'(uncore_iio_\d+)/(.*)/$', f[3])
        if not m: continue
        box, body = m.group(1), m.group(2)
        key=[k for k,v in evs.items() if v=='uncore_iio/'+body+'/']
        key=key[0] if key else body
        tab.setdefault(box,{})[key]=float(f[1]) if f[1] not in ('','<not counted>') else float('nan')
        rt[box]=float(f[4])/1e9
    d=tab.get('uncore_iio_1',{}); secs=rt.get('uncore_iio_1',float('nan'))
    print('==',label); [print('  ',l) for l in lines]
    print('    uncore_iio_1 (S1):', {k:f'{v/secs/1e6:.1f} M/s' for k,v in d.items()}, f'over {secs:.2f}s')
    others={b:{k:v for k,v in dd.items() if v>1e6} for b,dd in tab.items() if b!='uncore_iio_1'}
    others={b:v for b,v in others.items() if v}
    if others: print('    other boxes with >1e6:', others)
    rec={'label':label,'args':args,'out':lines,'perbox':tab,'secs':rt,'t':time.time()}
    with open(LOG,'a') as f: f.write(json.dumps(rec)+'\n')
    return d, secs

pb={}
for lab,args in [('h2c alone s40', f'-l 40 {E2} -- --dir h2c -n {N}'),
                 ('c2h alone r41', f'-l 41 {E2} -- --dir c2h -n {N}'),
                 ('bidir s40 r41', f'-l 40,41 --main-lcore 41 {E2} -- --dir bidir -n {N}')]:
    for nm,evs in [('A',IIOA),('B',IIOB)]:
        time.sleep(1)
        pb[(lab,nm)]=perbox2(f'2+2wc {lab} [{nm}]', args, evs)

# %% cell 14: 2026-11-17T00:19:02Z toolu_018sJTBP72qAvJXAcUeDfDYB Instructions/cycles per message on the sender core
for r in res_22wc[:3]+res_11wc[:1]:
    c=r['core']; n=200e6
    for cpu,v in c.items():
        if v['instr']: print(r['label'], cpu, f"{v['ghz']:.2f} GHz  instr/cell {v['instr']/n:.1f}  cycles/cell {v['cycles']/n:.1f}")
recs=[json.loads(l) for l in open(LOG)]
for r in recs:
    if r['label'].startswith('split'):
        c=r['core']; n=50e6
        for cpu,v in c.items():
            print(r['label'], cpu, f"{v['ghz']:.2f} GHz  instr/msg {v['instr']/n:.1f}  cycles/msg {v['cycles']/n:.1f}")
