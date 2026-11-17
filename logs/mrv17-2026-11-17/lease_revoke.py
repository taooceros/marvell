"""Multi-lcore inline_pingpong under the lease: stop the reflector mid-run (it takes the slot back) and
check every lane stops promptly with the lease error, the host exits non-zero, and nothing hangs."""
import re
import subprocess
import sys
import time

sys.path.insert(0, "/tmp/mrv17")
import harness as H

HB = "/tmp/mrv17/tr/release/inline_pingpong"
CB = "/root/bench/inline_reflector-mrv17-lease"
CARGS = "-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --store --store-wc"


def run(h, lcores, w=64, after=2.5):
    tag = f"LEASE REVOKE H{h} wc n4 W{w}"
    H.card_start(CARGS, CB)
    cmd = f"{HB} -l {lcores} -a 0000:99:02.0 --file-prefix=mrv17 -- --reply ring -w {w} -n 500000000 --host-lcores {h}"
    t0 = time.time()
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(after)
    still_running = p.poll() is None
    t_revoke = time.time()
    H.card_sh("tmux send-keys -t mrv17 C-c")
    try:
        out, _ = p.communicate(timeout=20)
        hung = False
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        hung = True
    t_exit = time.time()
    log = H.card_stop()
    lane_errs = re.findall(r"host lcore (\d+): ([^;\n]*)", out)
    r = dict(tag=tag, card_cmd=f"{CB} {CARGS}", host_cmd=cmd, rc=p.returncode, host=[], host_out=out[-3000:],
             card_log=log[-2500:], running_at_revoke=still_running, exit_after_revoke_s=round(t_exit - t_revoke, 2),
             hung=hung, lane_errors=lane_errs)
    H.record(r)
    return r, out, log


if __name__ == "__main__":
    for h, lc in ((2, "40-41"), (4, "40-43"), (1, "40")):
        r, out, log = run(h, lc)
        print(f"== {r['tag']}: running at revoke {r['running_at_revoke']}, host exit {r['exit_after_revoke_s']} s after "
              f"the reflector was told to stop, rc {r['rc']}, hung {r['hung']}")
        for line in out.splitlines():
            if re.search(r"lease|took BAR4|closed|Error|error|lcore", line) and not line.startswith("EAL"):
                print("   host:", line[:300])
        for line in log.splitlines():
            if re.search(r"lease|revok|released|took|reflected", line):
                print("   card:", line[:300])
