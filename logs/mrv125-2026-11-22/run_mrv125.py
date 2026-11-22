#!/usr/bin/env python3
"""MRV-125 experiment harness:
After restoring MAX_DRAIN=32 and shaping away the PEM0 slow state, can a 2 KiB
standard rte_mbuf-stride out-of-order TX retention pool (128 MiB) sustain zero
slow-state collapse and 100% RX DDIO hit?

Fixed fastest direct-path baseline (MRV-82 Occam's Razor, MRV-49 BAR4/MMIO only):
  - Host saturn cpu40 (Socket 1, performance governor, >= 3.86 GHz / 3.90 GHz nominal)
  - Socket 1 uncore 2.20 GHz (mesh >= 2.18 GHz)
  - MOVDIR64B -> BAR4 slot 15 (64 B cell, host send batch = 1, no SFENCE)
  - Card CN106 (2.00 GHz), pem_wc write-combining stores -> Host DDIO (card batch = 32)
  - Restored RingReplies::poll MAX_DRAIN = 32 (single post-drain RDTSCP)
  - Socket-1 2 MiB hugepages pre-touched non-zero (pool_page_kb=2048, N0=0, page_faults=0)
  - Constant-memory FastHist (tot_us & wire_us), warmup >= 2 * pool_slots on 100% of runs
  - 100%-enabled zero-mux Host Core/CHA(4/4)/M2PCIe/IRP1/IIO1/IMC + Card TAD/ARMv8 PMU
  - All runs verified: lost = bad = torn = corrupt = page_faults = 0, 3 interleaved reps.

Tiers (7 tiers; fixed N = 65,536 retained packets for the core 3-tier progression + controls):
  1. 64B_seq_4m       : N=65,536, stride=64 B,   seq,  fill-retain (4 MiB, LLC-hot, prefetcher on)  [Core 1]
  2. 64B_rand_4m      : N=65,536, stride=64 B,   rand, fill-retain (4 MiB, LLC-hot, OOO ACK)        [Core 2]
  3. 2KiB_rand_128m   : N=65,536, stride=2048 B, rand, fill-retain (128 MiB, 2^11 power-of-2 mbuf)  [Core 3]
  4. 2112B_rand_132m  : N=65,536, stride=2112 B, rand, fill-retain (132 MiB, DPDK odd-stride ctrl)  [Anti-alias]
  5. 2KiB_seq_128m    : N=65,536, stride=2048 B, seq,  fill-retain (128 MiB, 2 KiB seq ctrl)        [2KiB-seq]
  6. 64B_rand_128m    : N=2,097,152, stride=64 B, rand, fill-retain (128 MiB true LLC cap overflow) [True-cap]
  7. 2KiB_cold_128m   : N=65,536, stride=2048 B, rand, cold-read   (128 MiB delayed retx / cold rd) [Cold-read]

Parts (n = 3 interleaved reps each):
  - p1_wc1_shaped_bts   : WC1 W=128, T_pause=1000 ns, batch-TX-stamp (main shaped fast-state path)
  - p2a_wc1_unsh_pkt    : WC1 W=64,  T_pause=0,       per-pkt-stamp  (MRV-116 unshaped slow-state baseline)
  - p2b_wc1_shaped_pkt  : WC1 W=128, T_pause=1000 ns, per-pkt-stamp  (isolates RDTSC+MOVDIR64B MLP tax)
  - p2c_wc1_unsh_bts    : WC1 W=64,  T_pause=0,       batch-TX-stamp (single-var unshaped match to p1)
  - p3a_wc1_host_w32    : WC1 W=32,  T_pause=0,       batch-TX-stamp (pure-host safe window, zero card change)
  - p3b_wc4_host_b8     : WC4 W=48,  burst_8, T_pause=0, batch-TX-stamp (MRV-123 pure-host 4-core fast point)
  - p4a_h2c_ceiling     : H2C-only silent card + 32,768-cell BAR4 verify (host single-core DRAM ceiling)
  - p4b_wc1_w1_unloaded : WC1 W=1,   T_pause=0, 4x warmup (6 N=65,536 tiers, zero warmup artifact)
"""
import json
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv125")
LOG_DIR = ROOT / "logs/mrv125-2026-11-22"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
ENV_JSON = LOG_DIR / "env.json"
HOST_BIN = ROOT / "testbed/target/release/inline_pingpong"
CARD_BIN = "/root/bench/inline_reflector.mrv125"

REPS = 3
MIN_CPU_GHZ = 3.86
MIN_MESH_GHZ = 2.18

TIERS = [
    {
        "cfg": "64B_seq_4m",
        "tx_mode": "fill-retain",
        "tx_order": "seq",
        "slots": 65536,
        "stride": 64,
        "warmup": 262144,
        "role": "core",
    },
    {
        "cfg": "64B_rand_4m",
        "tx_mode": "fill-retain",
        "tx_order": "rand",
        "slots": 65536,
        "stride": 64,
        "warmup": 262144,
        "role": "core",
    },
    {
        "cfg": "2KiB_rand_128m",
        "tx_mode": "fill-retain",
        "tx_order": "rand",
        "slots": 65536,
        "stride": 2048,
        "warmup": 262144,
        "role": "core",
    },
    {
        "cfg": "2112B_rand_132m",
        "tx_mode": "fill-retain",
        "tx_order": "rand",
        "slots": 65536,
        "stride": 2112,
        "warmup": 262144,
        "role": "anti_alias",
    },
    {
        "cfg": "2KiB_seq_128m",
        "tx_mode": "fill-retain",
        "tx_order": "seq",
        "slots": 65536,
        "stride": 2048,
        "warmup": 262144,
        "role": "stride_seq",
    },
    {
        "cfg": "64B_rand_128m",
        "tx_mode": "fill-retain",
        "tx_order": "rand",
        "slots": 2097152,
        "stride": 64,
        "warmup": 4194304,
        "role": "true_llc_cap",
    },
    {
        "cfg": "2KiB_cold_128m",
        "tx_mode": "cold-read",
        "tx_order": "rand",
        "slots": 65536,
        "stride": 2048,
        "warmup": 262144,
        "role": "cold_read",
    },
]


def wait_for_turbo(min_khz: int = 3_850_000, max_wait_s: float = 45.0) -> int:
    t0 = time.monotonic()
    p = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq")
    while time.monotonic() - t0 < max_wait_s:
        khz = int(p.read_text().strip())
        if khz >= min_khz:
            return khz
        time.sleep(0.25)
    return int(p.read_text().strip())


def check_host_env() -> dict:
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    cur = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        uncore[p.parent.name] = int(p.read_text().strip())
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    hp_free = int(
        pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages").read_text().strip()
    )
    hp_total = int(
        pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages").read_text().strip()
    )
    assert gov == "performance", f"cpu40 governor is {gov}"
    assert uncore.get("package_01_die_01", 0) >= 2_190_000, f"uncore is {uncore}"
    assert paranoid <= 0, f"perf_event_paranoid is {paranoid}"
    assert hp_free >= 128, f"node1 free 2M hugepages too low: {hp_free}"
    return {
        "cpu40_governor": gov,
        "cpu40_khz": cur,
        "uncore_khz": uncore,
        "perf_event_paranoid": paranoid,
        "node1_hugepages_2m_free": hp_free,
        "node1_hugepages_2m_total": hp_total,
    }


def read_card_state() -> dict:
    cmd = (
        "cat /sys/devices/system/edac/mc/mc0/ce_count "
        "/sys/devices/system/edac/mc/mc0/ue_count "
        "/sys/devices/system/edac/ea/ea0/ce_count "
        "/sys/devices/system/edac/ea/ea0/ue_count; "
        "lsmod | grep -c '^pem_wc ' || true; "
        "cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000"
    )
    out = subprocess.check_output(["ssh", "root@10.10.0.2", cmd], text=True).split()
    return {
        "mc0_ce": int(out[0]),
        "mc0_ue": int(out[1]),
        "ea0_ce": int(out[2]),
        "ea0_ue": int(out[3]),
        "pem_wc_loaded": int(out[4]),
        "card_cpu3_khz": int(out[5]),
    }


def stop_card():
    subprocess.run(
        [
            "ssh",
            "root@10.10.0.2",
            "pkill -INT -f inline_reflector.mrv125 2>/dev/null || true; "
            "for i in $(seq 1 40); do pgrep -f inline_reflector.mrv125 >/dev/null || break; sleep 0.2; done; "
            "tmux kill-session -t mrv125 2>/dev/null || true",
        ],
        check=True,
    )


def start_card(lcores: str, expected_cores: int):
    stop_card()
    cmd = (
        f'lsmod | grep -q "^pem_wc " || insmod /root/bench/pem_wc.ko; '
        f"{CARD_BIN} --restore; "
        f'tmux new-session -d -s mrv125 "{CARD_BIN} -l {lcores} -a 0002:01:00.2 --file-prefix=mrv125 '
        f'-- --store --store-wc --control 10.10.0.2:7526 --sync 10.10.0.2:7527 > /tmp/mrv125_card.log 2>&1"'
    )
    subprocess.run(["ssh", "root@10.10.0.2", cmd], check=True)
    for _ in range(40):
        time.sleep(0.25)
        try:
            with socket.create_connection(("10.10.0.2", 7527), timeout=2.0) as s:
                s.sendall(b"SET_PAUSE 0\n")
                resp = s.recv(128).decode().strip()
                if resp.startswith("OK"):
                    time.sleep(0.3)
                    return
        except OSError:
            pass
    log = subprocess.check_output(["ssh", "root@10.10.0.2", "cat /tmp/mrv125_card.log 2>/dev/null || true"], text=True)
    raise RuntimeError(f"card reflector ({lcores}, expected {expected_cores}) failed to start:\n{log}")


def run_one(run_id: int, part: str, part_spec: dict, tier: dict, rep: int, epoch: int) -> dict:
    for attempt in range(6):
        wait_for_turbo()
        cmd = [
            str(HOST_BIN),
            "-l",
            "40",
            "-a",
            "0000:99:02.0",
            "--file-prefix=mrv125",
            "--",
            "--reply",
            "ring",
            "--pmu",
            "--tx-mode",
            tier["tx_mode"],
            "--tx-order",
            tier["tx_order"],
            "--tx-pool-slots",
            str(tier["slots"]),
            "--tx-slot-stride",
            str(tier["stride"]),
            "--warmup",
            str(tier["warmup"]),
        ]
        if part_spec["mode"] == "h2c_only":
            cmd += ["--h2c-only", "-n", str(part_spec["rounds"]), "--epoch", str(epoch)]
        else:
            cmd += [
                "-w",
                str(part_spec["window"]),
                "-n",
                str(part_spec["rounds"]),
                "--card-pause-ns",
                str(part_spec["card_pause_ns"]),
            ]
            if part_spec.get("max_send_burst", 0) > 0:
                cmd += ["--max-send-burst", str(part_spec["max_send_burst"])]
            if part_spec.get("credit_cap", 0) > 0:
                cmd += ["--credit-cap", str(part_spec["credit_cap"])]
            if part_spec.get("release_batch", 1) > 1:
                cmd += ["--release-batch", str(part_spec["release_batch"])]
            if part_spec.get("tx_stamp_batch", False):
                cmd += ["--tx-stamp-batch"]

        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            raise RuntimeError(
                f"run_id={run_id} {part}/{tier['cfg']} failed rc={proc.returncode}:\n{proc.stderr}\n{proc.stdout}"
            )
        payload = None
        for line in proc.stdout.splitlines():
            if line.startswith("MRV125 "):
                payload = json.loads(line[7:])
                break
        if payload is None:
            raise RuntimeError(f"run_id={run_id} {part}/{tier['cfg']} missing MRV125 JSON")

        hp = payload["host_pmu"]
        cp = payload["card_pmu"]
        n = payload["measured"]
        wcilf_ratio = hp["cha_ins_wcilf"] / n

        ok = True
        reasons = []
        if payload["max_drain"] != 32:
            ok = False
            reasons.append(f"max_drain={payload['max_drain']} != 32")
        if payload["warmup"] < 2 * payload["tx_pool_slots"]:
            ok = False
            reasons.append(f"warmup={payload['warmup']} < 2*slots={2*payload['tx_pool_slots']}")
        if payload["lost"] != 0 or payload["bad"] != 0 or payload["torn"] != 0:
            ok = False
            reasons.append(f"integrity l/b/t={payload['lost']}/{payload['bad']}/{payload['torn']}")
        if any(c["corrupt"] != 0 for c in cp["cores"]):
            ok = False
            reasons.append(f"card corrupt={[c['corrupt'] for c in cp['cores']]}")
        if hp["min_pct"] < 99.99 or cp["min_pct"] < 99.99:
            ok = False
            reasons.append(f"pmu mux host={hp['min_pct']} card={cp['min_pct']}")
        if hp["page_faults"] != 0:
            ok = False
            reasons.append(f"page_faults={hp['page_faults']}")
        if hp["ghz"] < MIN_CPU_GHZ:
            ok = False
            reasons.append(f"cpu40 ghz={hp['ghz']:.4f} < {MIN_CPU_GHZ}")
        if hp["mesh_ghz"] < MIN_MESH_GHZ:
            ok = False
            reasons.append(f"mesh_ghz={hp['mesh_ghz']:.4f} < {MIN_MESH_GHZ}")
        if abs(wcilf_ratio - 1.0) > 0.005:
            ok = False
            reasons.append(f"wcilf/n={wcilf_ratio:.5f}")
        if payload["pool_page_kb"] != 2048 or payload["pool_numa_n0"] != 0 or payload["pool_numa_n1"] <= 0:
            ok = False
            reasons.append(
                f"hugepage check kb={payload['pool_page_kb']} n0={payload['pool_numa_n0']} n1={payload['pool_numa_n1']}"
            )
        if part_spec["mode"] == "closed_loop":
            tot_taken = sum(c["taken"] for c in cp["cores"])
            itom = hp["cha_io_hit_itom"] + hp["cha_io_miss_itom"]
            if tot_taken != n:
                ok = False
                reasons.append(f"card taken={tot_taken} != {n}")
            if abs(itom / n - 1.0) > 0.005:
                ok = False
                reasons.append(f"itom/n={itom/n:.5f}")
            if payload["cores"] != part_spec["card_cores"]:
                ok = False
                reasons.append(f"cores={payload['cores']} != {part_spec['card_cores']}")
        else:
            bv = payload.get("bar4_verify") or {}
            if bv.get("verified") != 32768 or bv.get("mismatches") != 0 or bv.get("torn") != 0:
                ok = False
                reasons.append(f"bar4_verify={bv}")

        if ok:
            return {
                "run_id": run_id,
                "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
                "part": part,
                "cfg": tier["cfg"],
                "role": tier["role"],
                "rep": rep,
                "attempt": attempt,
                **payload,
            }
        print(
            f"  [retry {attempt+1}/6] run_id={run_id} {part}/{tier['cfg']} rep={rep}: {', '.join(reasons)}",
            flush=True,
        )
        time.sleep(0.8)

    raise RuntimeError(f"run_id={run_id} {part}/{tier['cfg']} rep={rep} failed quality checks after 6 attempts")


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    host_before = check_host_env()
    card_before = read_card_state()
    assert card_before["mc0_ue"] == 0 and card_before["ea0_ue"] == 0, f"Card EDAC UE before: {card_before}"
    print(f"Host before: {host_before}", flush=True)
    print(f"Card before: {card_before}", flush=True)

    parts_plan = [
        (
            "p1_wc1_shaped_bts",
            {
                "mode": "closed_loop",
                "lcores": "3",
                "card_cores": 1,
                "window": 128,
                "card_pause_ns": 1000,
                "tx_stamp_batch": True,
                "rounds": 4_194_304,
                "tiers": TIERS,
            },
        ),
        (
            "p2a_wc1_unsh_pkt",
            {
                "mode": "closed_loop",
                "lcores": "3",
                "card_cores": 1,
                "window": 64,
                "card_pause_ns": 0,
                "tx_stamp_batch": False,
                "rounds": 4_194_304,
                "tiers": TIERS,
            },
        ),
        (
            "p2b_wc1_shaped_pkt",
            {
                "mode": "closed_loop",
                "lcores": "3",
                "card_cores": 1,
                "window": 128,
                "card_pause_ns": 1000,
                "tx_stamp_batch": False,
                "rounds": 4_194_304,
                "tiers": TIERS,
            },
        ),
        (
            "p2c_wc1_unsh_bts",
            {
                "mode": "closed_loop",
                "lcores": "3",
                "card_cores": 1,
                "window": 64,
                "card_pause_ns": 0,
                "tx_stamp_batch": True,
                "rounds": 4_194_304,
                "tiers": TIERS,
            },
        ),
        (
            "p3a_wc1_host_w32",
            {
                "mode": "closed_loop",
                "lcores": "3",
                "card_cores": 1,
                "window": 32,
                "card_pause_ns": 0,
                "tx_stamp_batch": True,
                "rounds": 4_194_304,
                "tiers": TIERS,
            },
        ),
        (
            "p4a_h2c_ceiling",
            {
                "mode": "h2c_only",
                "lcores": "3",
                "card_cores": 1,
                "rounds": 33_554_432,
                "tiers": TIERS,
            },
        ),
        (
            "p4b_wc1_w1_unloaded",
            {
                "mode": "closed_loop",
                "lcores": "3",
                "card_cores": 1,
                "window": 1,
                "card_pause_ns": 0,
                "tx_stamp_batch": False,
                "rounds": 262_144,
                "tiers": [t for t in TIERS if t["slots"] == 65536],
            },
        ),
        (
            "p3b_wc4_host_b8",
            {
                "mode": "closed_loop",
                "lcores": "3-6",
                "card_cores": 4,
                "window": 48,
                "max_send_burst": 8,
                "card_pause_ns": 0,
                "tx_stamp_batch": True,
                "rounds": 4_194_304,
                "tiers": TIERS,
            },
        ),
    ]

    total_planned = sum(len(spec["tiers"]) * REPS for _, spec in parts_plan)
    run_id = 0
    epoch = 300
    current_lcores = None
    t_start = time.monotonic()

    with RUNS_JSONL.open("w") as f:
        for part, spec in parts_plan:
            if spec["lcores"] != current_lcores:
                print(f"\n>>> Starting card reflector -l {spec['lcores']} ({spec['card_cores']} cores) <<<", flush=True)
                start_card(spec["lcores"], spec["card_cores"])
                current_lcores = spec["lcores"]
            print(
                f"\n=== Starting {part} ({len(spec['tiers'])} tiers x {REPS} interleaved reps) ===",
                flush=True,
            )
            for rep in range(REPS):
                for tier in spec["tiers"]:
                    run_id += 1
                    epoch += 1
                    rec = run_one(run_id, part, spec, tier, rep, epoch)
                    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
                    f.flush()
                    hp = rec["host_pmu"]
                    cp = rec["card_pmu"]
                    n = rec["measured"]
                    if spec["mode"] == "closed_loop":
                        tot_taken = sum(c["taken"] for c in cp["cores"])
                        tot_busy = sum(c["busy_polls"] for c in cp["cores"])
                        cbat = tot_taken / max(1, tot_busy)
                        snp = cp["tad"]["tad_snp_msh_out_any"]["val"] / n
                        lat_s = f"p50={rec['tot_us']['p50']:5.2f} p99={rec['tot_us']['p99']:5.2f}us"
                        card_s = f"cbat={cbat:4.1f} snp={snp:4.2f}"
                    else:
                        lat_s = "h2c_only           "
                        card_s = "card=silent        "
                    print(
                        f"[{run_id:3d}/{total_planned}] {part:20s} r{rep} {tier['cfg']:16s} | "
                        f"{rec['mops']:6.2f} M/s ({rec['gbps']:5.2f} GB/s) | {lat_s} | {card_s} | "
                        f"ddio={hp['ddio_hit_pct']:6.2f}% llcM={hp['llc_load_misses']/n:.2f} "
                        f"imc={hp['imc_cas_rd']/n:.2f}/{hp['imc_cas_wr']/n:.2f} ghz={hp['ghz']:.2f}",
                        flush=True,
                    )

    stop_card()
    host_after = check_host_env()
    card_after = read_card_state()
    elapsed_s = time.monotonic() - t_start
    assert card_after["mc0_ue"] == card_before["mc0_ue"] and card_after["ea0_ue"] == card_before["ea0_ue"], (
        f"Card EDAC UE changed: before={card_before} after={card_after}"
    )
    env_doc = {
        "issue": "MRV-125",
        "elapsed_s": round(elapsed_s, 2),
        "total_runs": run_id,
        "reps": REPS,
        "max_drain": 32,
        "host_before": host_before,
        "host_after": host_after,
        "card_before": card_before,
        "card_after": card_after,
    }
    ENV_JSON.write_text(json.dumps(env_doc, indent=2) + "\n")
    print(f"\nDONE: {run_id} verified runs in {elapsed_s:.1f}s -> {RUNS_JSONL}", flush=True)
    print(f"Card after: {card_after}", flush=True)


if __name__ == "__main__":
    main()
