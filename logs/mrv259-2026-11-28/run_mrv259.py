#!/usr/bin/env python3
"""MRV-259 end-to-end MT_NORMAL_NC (0x44, enable_custom_mair=0) + zero-lookahead u1_ldpq
rerun on KvStore (MRV-195) and zero-mod upstream Memcached 1.6.45 (MRV-206) over 100GbE
(mercury <-> CN106 <-> saturn), with H = 1, 2, 4 host-core scaling.
"""

import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

REPO = pathlib.Path("/home/hongtao/dev/marvell-mrv259")
LOG_DIR = REPO / "logs" / "mrv259-2026-11-28"
LOG_DIR.mkdir(parents=True, exist_ok=True)

KV_HOST_BIN = str(REPO / "testbed/target/release/mrv259_kv_host")
MC_HOST_BIN = str(REPO / "testbed/target/release/mrv259_mc_host")
KV_CARD_BIN = "/tmp/mrv259_kv_card"
MC_CARD_BIN = "/tmp/mrv259_mc_card"
KV_MERC_BIN = "/tmp/mrv195/mrv195_client"
MC_MERC_BIN = "/tmp/mrv206/mrv206_client"

ETH_DEV = "0002:02:00.0"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]


def sh(cmd, check=True, timeout=60):
    return subprocess.run(cmd, text=True, capture_output=True, check=check, timeout=timeout)


def ssh_card(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host, port, msg, timeout=10.0):
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((msg + "\n").encode())
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
            if b.endswith(b"\n"):
                break
        return b"".join(chunks).decode().strip()


class Harness:
    def __init__(self):
        self.card_key = None
        self.host_key = None
        self.host_proc = None

    def stop_host(self):
        if self.host_proc is not None:
            for port in (7531, 7541):
                try:
                    tcp_cmd("127.0.0.1", port, "QUIT", timeout=2.0)
                except Exception:
                    pass
            try:
                self.host_proc.wait(timeout=5)
            except Exception:
                self.host_proc.kill()
                self.host_proc.wait(timeout=3)
            self.host_proc = None
        self.host_key = None
        sh(["pkill", "-x", "mrv259_kv_host"], check=False)
        sh(["pkill", "-x", "mrv259_mc_host"], check=False)
        time.sleep(0.2)

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m259 C-c 2>/dev/null || true; sleep 0.3; "
            "tmux kill-session -t m259 2>/dev/null || true; "
            "pkill -x mrv259_kv_card 2>/dev/null || true; "
            "pkill -x mrv259_mc_card 2>/dev/null || true; "
            "sleep 0.2; rm -f /dev/hugepages/m259*",
            check=False,
        )
        self.card_key = None

    def ensure(self, app, policy, host_cores, card_cores=4):
        """app in ('kv', 'mc'); policy in ('direct_wb', 'direct_nc_u1q', 'sdp_fair')."""
        c_key = (app, policy, card_cores)
        h_key = (app, "sdp" if policy == "sdp_fair" else "direct", host_cores, card_cores)

        if self.card_key != c_key:
            self.stop_card()
            ssh_card(
                "tmux kill-session -t refl 2>/dev/null || true; "
                "pkill -x inline_reflector 2>/dev/null || true; "
                "ip link set eth0 down 2>/dev/null || true; "
                "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
                'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
                "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
                'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi; '
                "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
                "lsmod | grep -q '^bar4_mair ' || insmod /root/bench/bar4_mair.ko enable_custom_mair=0"
            )
            lcores = "3-6" if card_cores == 4 else "3-4"
            card_bin = KV_CARD_BIN if app == "kv" else MC_CARD_BIN
            if policy == "sdp_fair":
                ready_tok = "sdp ready"
                cmd = (
                    f"{card_bin} -l {lcores} -a {ETH_DEV} -a 0002:01:00.2 --file-prefix=m259 -- "
                    f"--mode sdp --rx-mode cqe-direct-safe"
                )
            else:
                ready_tok = "direct ready"
                nc_flag = " --bar4-nc" if policy == "direct_nc_u1q" else ""
                dma_extra = " --dma-threshold 1024" if app == "kv" else ""
                cmd = (
                    f"{card_bin} -l {lcores} -a {ETH_DEV} "
                    f"-a 0000:06:00.1 -a 0000:06:00.2 -a 0000:06:00.3 -a 0000:06:00.4 "
                    f"--file-prefix=m259 -- --mode direct --rx-mode cqe-direct-safe{dma_extra}{nc_flag}"
                )
            ssh_card(
                f'rm -f /tmp/m259_card.log; '
                f'tmux new-session -d -s m259 "{cmd} > /tmp/m259_card.log 2>&1"; '
                f'for i in $(seq 1 80); do grep -q "{ready_tok}" /tmp/m259_card.log 2>/dev/null && break; sleep 0.15; done; '
                f'grep "{ready_tok}" /tmp/m259_card.log'
            )
            self.card_key = c_key

        if self.host_key != h_key or self.host_proc is None or self.host_proc.poll() is not None:
            self.stop_host()
            hmode = h_key[1]
            host_bin = KV_HOST_BIN if app == "kv" else MC_HOST_BIN
            ready_tok = "MRV195_HOST READY" if app == "kv" else "MRV206_HOST READY"
            if hmode == "sdp":
                tail_off = 48 if app == "mc" else 48
                devarg = (
                    f"0000:99:02.0,rx_poll_mem=1,rx_poll_tail_off={tail_off},"
                    f"ism_enable=1,allow_small_oq=1,max_rings={card_cores}"
                )
                extra = [
                    "--mode", "sdp",
                    "--sdp-queues", str(card_cores),
                    "--sdp-rx-desc", "256",
                    "--sdp-tx-desc", "2048",
                    "--sdp-compact-pool", "true",
                    "--sdp-zero-copy", "true",
                    "--host-cores", str(host_cores),
                ]
            else:
                devarg = "0000:99:02.0"
                extra = [
                    "--mode", "direct",
                    "--tx-pool-slots", "1024",
                    "--host-cores", str(host_cores),
                ]
                if app == "kv":
                    extra += ["--dma-threshold", "1024"]
            prof_args = ["--kv-profile", "mix"] if app == "kv" else ["--mc-profile", "etc_mix"]
            cmd = [
                host_bin, "-l", "40", "-a", devarg,
                "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m259h", "--",
            ] + prof_args + ["--pmu", "true"] + extra
            log_path = pathlib.Path("/tmp/m259_host.log")
            with open(log_path, "w") as logf:
                self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
            t0 = time.time()
            while time.time() - t0 < 20.0:
                if self.host_proc.poll() is not None:
                    raise RuntimeError(f"host exited early:\n{log_path.read_text()}")
                txt = log_path.read_text() if log_path.exists() else ""
                if ready_tok in txt:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError(f"host ready timeout:\n{log_path.read_text()}")
            self.host_key = h_key

    def run_one(self, app, policy, workload, host_cores, card_cores, threads, window, warmup_ms, duration_ms, seed, rep):
        self.ensure(app, policy, host_cores, card_cores)
        ctrl_port = 7531 if app == "kv" else 7541
        sync_port = 7529 if app == "kv" else 7539
        if app == "kv":
            prof = "put4096" if workload == "put4096" else ("get256" if workload == "get256" else ("put64" if workload == "put64" else ("get64" if workload == "get64" else "mix")))
        else:
            prof = "get8" if workload == "get8" else "etc_mix"

        tcp_cmd("127.0.0.1", ctrl_port, f"RESET {prof}")
        tcp_cmd("10.10.0.2", sync_port, "RESET_PEERS")
        merc_bin = KV_MERC_BIN if app == "kv" else MC_MERC_BIN
        kdist = "uniform" if app == "kv" else ("uniform" if workload == "get8" else "zipf")
        mcmd = (
            f"{merc_bin} --dev mlx5_2 --sgid 3 --cpu 4 --threads {threads} --card-queues {card_cores} "
            f"--mode closed --workload {workload} --key-dist {kdist} "
            f"--window {window} --rate-krps 0.000 "
            f"--warmup-ms {warmup_ms} --duration-ms {duration_ms} --seed {seed} --sync-reset"
        )
        p = subprocess.Popen(
            MERC_SSH + [mcmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        lines = []
        t0 = time.time()
        warmup_seen = False
        while time.time() - t0 < 30.0:
            line = p.stdout.readline()
            if not line:
                if p.poll() is not None:
                    break
                time.sleep(0.01)
                continue
            lines.append(line)
            if "WARMUP_DONE" in line:
                warmup_seen = True
                break
        if not warmup_seen:
            out, err = p.communicate(timeout=5)
            raise RuntimeError(f"client no WARMUP_DONE:\n{''.join(lines)}{out}\n{err}")

        tcp_cmd("127.0.0.1", ctrl_port, "RESET")
        tcp_cmd("10.10.0.2", sync_port, "RESET")
        p.stdin.write("GO\n")
        p.stdin.flush()
        out, err = p.communicate(timeout=45)
        lines.append(out)
        full_out = "".join(lines)
        if p.returncode != 0:
            raise RuntimeError(f"client rc={p.returncode}:\n{full_out}\n{err}")

        card_raw = tcp_cmd("10.10.0.2", sync_port, "STATS")
        host_raw = tcp_cmd("127.0.0.1", ctrl_port, "STATS")
        client_json = None
        for ln in full_out.splitlines():
            ln = ln.strip()
            if "CLIENT_JSON " in ln:
                ln = ln.split("CLIENT_JSON ", 1)[1].strip()
            if ln.startswith("{") and ln.endswith("}"):
                client_json = json.loads(ln)
        if client_json is None:
            raise RuntimeError(f"no client json:\n{full_out}")

        card_json = json.loads(card_raw)
        host_json = json.loads(host_raw)

        rec = {
            "ts": time.time(),
            "app": app,
            "policy": policy,
            "workload": workload,
            "host_cores": host_cores,
            "card_cores": card_cores,
            "threads": threads,
            "window": window,
            "rep": rep,
            "client": client_json,
            "host": host_json,
            "card": card_json,
        }
        return rec


def check_clean(rec):
    c = rec["client"]
    h = rec["host"]
    k = rec["card"]
    errs = []
    for fld in ("bad", "corrupt", "timeouts", "lost"):
        if c.get(fld, 0) != 0:
            errs.append(f"client.{fld}={c.get(fld)}")
    for fld in ("bad", "torn", "tx_drops", "mc_errors"):
        if h.get(fld, 0) != 0:
            errs.append(f"host.{fld}={h.get(fld)}")
    for fld in ("bad_wire", "bad_host", "torn_bar4", "dma_failed", "eth_tx_drops", "sdp_tx_drops"):
        if k.get(fld, 0) != 0:
            errs.append(f"card.{fld}={k.get(fld)}")
    return errs


def fmt_summary(rec):
    c = rec["client"]
    h = rec["host"]
    k = rec["card"]
    mrps = c.get("achieved_mrps", c.get("mrps", 0.0))
    p50 = c.get("tot_p50_us", 0.0)
    p99 = c.get("tot_p99_us", 0.0)
    hrtt = k.get("host_rtt_us", {}).get("p50", 0.0)
    tad = k.get("tad", {})
    exlmn = tad.get("tad_snp_msh_out_exlmn", 0)
    rpcs = max(k.get("rpcs_out", 1), 1)
    busy_ns = h.get("busy_ns_per_req", 0.0)
    return (
        f"{rec['app']:2s} {rec['policy']:13s} H={rec['host_cores']} K={rec['card_cores']} "
        f"{rec['workload']:7s} W={rec['window']:3d} rep={rec['rep']} | "
        f"{mrps:6.2f} Mrps  p50={p50:6.2f}us p99={p99:6.2f}us  "
        f"host_rtt={hrtt:5.2f}us  busy={busy_ns:5.1f}ns  exlmn/rpc={exlmn/rpcs:5.2f}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--out", default=str(LOG_DIR / "runs.jsonl"))
    args = ap.parse_args()

    h = Harness()
    out_path = pathlib.Path(args.out)
    try:
        if args.smoke:
            cases = [
                ("kv", "direct_wb", "get64", 1, 4, 8, 256),
                ("kv", "direct_nc_u1q", "get64", 1, 4, 8, 256),
                ("kv", "direct_wb", "get64", 4, 4, 8, 256),
                ("kv", "direct_nc_u1q", "get64", 4, 4, 8, 256),
                ("mc", "direct_wb", "get8", 1, 4, 8, 256),
                ("mc", "direct_nc_u1q", "get8", 1, 4, 8, 256),
                ("mc", "direct_wb", "get8", 4, 4, 8, 256),
                ("mc", "direct_nc_u1q", "get8", 4, 4, 8, 256),
            ]
            for idx, (app, pol, wl, hc, kc, th, w) in enumerate(cases):
                rec = h.run_one(app, pol, wl, hc, kc, th, w, 1500, 2000, 1000 + idx, 0)
                errs = check_clean(rec)
                print(fmt_summary(rec), "OK" if not errs else f"ERR: {errs}", flush=True)
        elif args.full:
            with open(out_path, "a") as fout:
                for rep in range(3):
                    # Group 1: KvStore
                    kv_cases = [
                        # H=1 W=1 RTT
                        ("kv", "direct_wb", "get64", 1, 4, 1, 1),
                        ("kv", "direct_nc_u1q", "get64", 1, 4, 1, 1),
                        ("kv", "sdp_fair", "get64", 1, 4, 1, 1),
                        ("kv", "direct_wb", "get256", 1, 4, 1, 1),
                        ("kv", "direct_nc_u1q", "get256", 1, 4, 1, 1),
                        ("kv", "sdp_fair", "get256", 1, 4, 1, 1),
                        ("kv", "direct_wb", "put4096", 1, 4, 1, 1),
                        ("kv", "direct_nc_u1q", "put4096", 1, 4, 1, 1),
                        ("kv", "sdp_fair", "put4096", 1, 4, 1, 1),
                        # H=1 saturation
                        ("kv", "direct_wb", "get64", 1, 4, 8, 256),
                        ("kv", "direct_nc_u1q", "get64", 1, 4, 8, 256),
                        ("kv", "direct_nc_u1q", "get64", 1, 2, 8, 256),
                        ("kv", "sdp_fair", "get64", 1, 4, 8, 256),
                        ("kv", "direct_wb", "get256", 1, 4, 8, 256),
                        ("kv", "direct_nc_u1q", "get256", 1, 4, 8, 256),
                        ("kv", "sdp_fair", "get256", 1, 4, 8, 256),
                        ("kv", "direct_wb", "put64", 1, 4, 8, 256),
                        ("kv", "direct_nc_u1q", "put64", 1, 4, 8, 256),
                        ("kv", "sdp_fair", "put64", 1, 4, 8, 256),
                        ("kv", "direct_wb", "put4096", 1, 4, 8, 64),
                        ("kv", "direct_nc_u1q", "put4096", 1, 4, 8, 64),
                        ("kv", "sdp_fair", "put4096", 1, 4, 8, 64),
                        ("kv", "direct_wb", "mix", 1, 4, 8, 256),
                        ("kv", "direct_nc_u1q", "mix", 1, 4, 8, 256),
                        ("kv", "sdp_fair", "mix", 1, 4, 8, 256),
                        # H=2 and H=4 multi-host-core scaling
                        ("kv", "direct_wb", "get64", 2, 4, 8, 256),
                        ("kv", "direct_nc_u1q", "get64", 2, 4, 8, 256),
                        ("kv", "sdp_fair", "get64", 2, 4, 8, 256),
                        ("kv", "direct_wb", "get64", 4, 4, 8, 256),
                        ("kv", "direct_nc_u1q", "get64", 4, 4, 8, 256),
                        ("kv", "sdp_fair", "get64", 4, 4, 8, 256),
                        ("kv", "direct_wb", "get256", 4, 4, 8, 256),
                        ("kv", "direct_nc_u1q", "get256", 4, 4, 8, 256),
                        ("kv", "sdp_fair", "get256", 4, 4, 8, 256),
                        ("kv", "direct_wb", "mix", 4, 4, 8, 256),
                        ("kv", "direct_nc_u1q", "mix", 4, 4, 8, 256),
                        ("kv", "sdp_fair", "mix", 4, 4, 8, 256),
                    ]
                    # Group 2: Memcached 1.6.45
                    mc_cases = [
                        ("mc", "direct_wb", "get8", 1, 4, 1, 1),
                        ("mc", "direct_nc_u1q", "get8", 1, 4, 1, 1),
                        ("mc", "sdp_fair", "get8", 1, 4, 1, 1),
                        ("mc", "direct_wb", "get8", 1, 4, 8, 256),
                        ("mc", "direct_nc_u1q", "get8", 1, 4, 8, 256),
                        ("mc", "sdp_fair", "get8", 1, 4, 8, 256),
                        ("mc", "direct_wb", "etc_mix", 1, 4, 8, 256),
                        ("mc", "direct_nc_u1q", "etc_mix", 1, 4, 8, 256),
                        ("mc", "sdp_fair", "etc_mix", 1, 4, 8, 256),
                        # H=4 multi-host-core scaling
                        ("mc", "direct_wb", "get8", 4, 4, 8, 256),
                        ("mc", "direct_nc_u1q", "get8", 4, 4, 8, 256),
                        ("mc", "sdp_fair", "get8", 4, 4, 8, 256),
                        ("mc", "direct_wb", "etc_mix", 4, 4, 8, 256),
                        ("mc", "direct_nc_u1q", "etc_mix", 4, 4, 8, 256),
                        ("mc", "sdp_fair", "etc_mix", 4, 4, 8, 256),
                    ]
                    for idx, (app, pol, wl, hc, kc, th, w) in enumerate(kv_cases + mc_cases):
                        for attempt in range(3):
                            try:
                                rec = h.run_one(app, pol, wl, hc, kc, th, w, 1200, 2000, 2000 + rep * 1000 + idx * 10 + attempt, rep)
                                errs = check_clean(rec)
                                if not errs:
                                    print(fmt_summary(rec), "OK", flush=True)
                                    fout.write(json.dumps(rec) + "\n")
                                    fout.flush()
                                    break
                                print(fmt_summary(rec), f"RETRY({attempt}) ERR:{errs}", flush=True)
                            except Exception as e:
                                print(f"RETRY({attempt}) {app} {pol} H={hc} {wl}: {str(e).splitlines()[0]}", flush=True)
                                h.stop_host()
    finally:
        h.stop_card()


if __name__ == "__main__":
    main()
