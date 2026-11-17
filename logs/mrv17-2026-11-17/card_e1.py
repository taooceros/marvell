"""Card-side counters during a steady closed-loop run: perf stat on the card's ring core (cpu 3) for 1 s
while the host runs (fresh reflector per row, uninstrumented card binary)."""
import json
import re
import subprocess
import sys
import time

sys.path.insert(0, "/tmp/mrv17")
import harness as H

CPERF = "/root/mrv17-perf/perf"
GROUPS = {
    "core1": "cpu_cycles,inst_retired,stall_backend,stall_backend_mem,l1d_cache_refill,l2d_cache_refill,ll_cache_miss_rd",
    "core2": "cpu_cycles,mem_access,bus_access,l1d_cache_lmiss_rd,l2d_cache_lmiss_rd,ll_cache_rd,remote_access",
    "tad1": "tad/tad_req_msh_in_any/,tad/tad_req_occ/,tad/tad_snp_msh_out_any/,tad/tad_rsp_msh_out_retry_mn/,tad/tad_rsp_msh_out_retry_exlmn/,tad/tad_tot_cycle/",
    "tad2": "tad/tad_ifb_occ/,tad/tad_alloc_any/,tad/tad_hit_any/,tad/tad_dat_msh_in_any/,tad/tad_REPLAYS/,tad/tad_tot_cycle/",
}
ROUNDS = {32: 60_000_000, 64: 40_000_000, 128: 40_000_000, 256: 40_000_000, 1024: 150_000_000}


def run(group, w, delay=0.8, dur=1.0):
    tag = f"CARDPMU {group} wc n1 W{w}"
    H.card_start(H.WCN1, H.CARD_ORIG)
    try:
        cmd = f"{H.HOST_OFFICIAL} -l 40 -a 0000:99:02.0 --file-prefix=mrv17 -- --reply ring -w {w} -n {ROUNDS[w]}"
        t0 = time.time()
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(delay)
        scope = "-a" if group.startswith("tad") else "-C 3"
        rc, pout = H.card_sh(f"{CPERF} stat {scope} -x ';' -e {GROUPS[group]} -- sleep {dur} 2>&1", timeout=60)
        out, _ = p.communicate(timeout=600)
        secs = time.time() - t0
    finally:
        log = H.card_stop()
    r = dict(tag=tag, card_cmd=f"{H.CARD_ORIG} {H.WCN1}", host_cmd=cmd, rc=p.returncode, secs=round(secs, 2),
             host=H.parse_host(out), host_out=out[-3000:], card=H.parse_card(log), card_log=log[-2000:],
             card_perf=pout[-4000:], card_perf_dur=dur)
    H.record(r)
    if p.returncode != 0 or not r["host"]:
        raise RuntimeError(f"{tag}: host rc {p.returncode}\n{out[-1500:]}")
    return r


if __name__ == "__main__":
    for w in (64, 1024, 128, 32):
        for g in GROUPS:
            r = run(g, w)
            h = r["host"][0]
            print(r["tag"], h["mmsg"], "lbt", h["lost"], h["bad"], h["torn"], "|", " ".join(
                l.split(";")[0] + ":" + l.split(";")[2] for l in r["card_perf"].splitlines() if ";" in l), flush=True)
