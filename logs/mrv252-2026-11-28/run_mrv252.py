#!/usr/bin/env python3
import json, subprocess, sys, time, os

BIN = "./testbed/target/release/mrv252_host"
OUT = "logs/mrv252-2026-11-28/runs.jsonl"

POLL_MODES_5 = ["u4_ldpq", "u1_ldpq", "adapt-50", "adapt-100", "adapt-exp"]
POLL_MODES_4 = ["u4_ldpq", "adapt-50", "adapt-100", "adapt-exp"]

def build_matrix():
    conds = []
    # Group 1: H=1, K=4, 4 regimes
    for pm in POLL_MODES_5:
        conds.append(("g1_w1", f"k4_w1_{pm}", [
            "--host-cores", "1", "--card-cores", "4", "--work-mode", "closed",
            "--window", "1", "--map-mode", "normal_nc", "--poll-mode", pm
        ]))
        conds.append(("g1_ow", f"k4_ow_{pm}", [
            "--host-cores", "1", "--card-cores", "4", "--work-mode", "tx_oneway",
            "--window", "6144", "--map-mode", "normal_nc", "--poll-mode", pm
        ]))
        for w in [64, 256, 512]:
            conds.append(("g1_cl", f"k4_cl_w{w}_{pm}", [
                "--host-cores", "1", "--card-cores", "4", "--work-mode", "closed",
                "--window", str(w), "--map-mode", "normal_nc", "--poll-mode", pm
            ]))
        for rate in [10, 40, 80]:
            conds.append(("g1_ol", f"k4_ol_{rate}m_{pm}", [
                "--host-cores", "1", "--card-cores", "4", "--work-mode", "tx_oneway",
                "--window", "6144", "--map-mode", "normal_nc", "--poll-mode", pm,
                "--target-mpps", str(rate)
            ]))
    # Group 2: Over-provisioned H=1, K=8
    for pm in POLL_MODES_4:
        conds.append(("g2_k8_ow", f"k8_ow_{pm}", [
            "--host-cores", "1", "--card-cores", "8", "--work-mode", "tx_oneway",
            "--window", "6144", "--map-mode", "normal_nc", "--poll-mode", pm
        ]))
        conds.append(("g2_k8_cl", f"k8_cl_w256_{pm}", [
            "--host-cores", "1", "--card-cores", "8", "--work-mode", "closed",
            "--window", "256", "--map-mode", "normal_nc", "--poll-mode", pm
        ]))
    # Group 3: Co-located Socket-1 neighbor (6c STREAM + 16 MiB L3)
    conds.append(("g3_nb", "nb_idle", [
        "--work-mode", "idle_neighbor", "--neighbor", "stream6_l3"
    ]))
    for k in [4, 8]:
        for pm in ["u4_ldpq", "adapt-50", "adapt-exp"]:
            conds.append(("g3_nb", f"nb_k{k}_ow_{pm}", [
                "--host-cores", "1", "--card-cores", str(k), "--work-mode", "tx_oneway",
                "--window", "6144", "--map-mode", "normal_nc", "--poll-mode", pm,
                "--neighbor", "stream6_l3"
            ]))
    return conds

def main():
    conds = build_matrix()
    total = len(conds) * 3
    print(f"MRV-252 matrix: {len(conds)} conditions x 3 reps = {total} runs", flush=True)
    idx = 0
    bad_runs = 0
    t_start = time.time()
    with open(OUT, "w") as fout:
        for rep in range(3):
            for exp, cond, extra in conds:
                idx += 1
                cmd = [
                    BIN, "--exp", exp, "--cond", cond, "--rep", str(rep),
                    "--warmup-ms", "250", "--duration-ms", "800"
                ] + extra
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                line = None
                for l in p.stdout.splitlines():
                    if l.startswith("MRV252_RUN "):
                        line = l[len("MRV252_RUN "):]
                        break
                if p.returncode != 0 or line is None:
                    print(f"[FAIL {idx}/{total}] {cond} rep={rep}: rc={p.returncode} err={p.stderr.strip()[:200]}", flush=True)
                    bad_runs += 1
                    continue
                obj = json.loads(line)
                fout.write(json.dumps(obj) + "\n")
                fout.flush()
                cs = obj.get("card_stats", {})
                lost = obj.get("lost", 0)
                bot = obj.get("bad_or_torn", 0)
                corrupt = cs.get("corrupt", 0)
                if lost or bot or corrupt:
                    print(f"[WARN {idx}/{total}] {cond} rep={rep}: lost={lost} bot={bot} corrupt={corrupt}", flush=True)
                    bad_runs += 1
                if idx % 20 == 0 or idx == total:
                    el = time.time() - t_start
                    print(f"[{idx}/{total}] {el:.1f}s last={cond} mops={obj.get('mops',0):.2f} rtt_p50={obj.get('rtt_p50_ns',0):.1f}", flush=True)
    print(f"DONE: {idx} runs in {time.time()-t_start:.1f}s, bad_runs={bad_runs}", flush=True)
    sys.exit(1 if bad_runs else 0)

if __name__ == "__main__":
    main()
