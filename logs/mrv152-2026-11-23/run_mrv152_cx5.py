#!/usr/bin/env python3
"""MRV-152 pure mercury ConnectX-5 (mlx5_2) W=1 self-loopback sweep.

Interleaved n=3 replicates across {64, 256, 1024, 4096} B (exact MRV-133 sizes),
with mlx5_2 unicast_rcv_packets / tx_packets_phy / rx_packets_phy hardware-counter
verification on every run. Zero CN106 card access.
"""

import json
import pathlib
import subprocess
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv152/logs/mrv152-2026-11-23")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"
SIZES = [64, 256, 1024, 4096]
REPS = 3
WARMUP = 10_000
ROUNDS = 100_000


def ssh_merc(cmd: str) -> str:
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "mercury", cmd],
        check=True,
        text=True,
        capture_output=True,
        timeout=60,
    )
    return r.stdout.strip()


def read_counters() -> dict:
    out = ssh_merc(
        "cat /sys/class/infiniband/mlx5_2/ports/1/counters/unicast_rcv_packets "
        "/sys/class/infiniband/mlx5_2/ports/1/counters/unicast_xmit_packets; "
        "ethtool -S ens6f1np1 | awk '/tx_packets_phy:|rx_packets_phy:/{print $2}'"
    )
    vals = [int(x) for x in out.splitlines() if x.strip()]
    return {
        "unicast_rcv_packets": vals[0],
        "unicast_xmit_packets": vals[1],
        "tx_packets_phy": vals[2],
        "rx_packets_phy": vals[3],
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["scp", "-q", str(OUT_DIR / "mrv152_cx5_loopback.rs"), "mercury:/tmp/mrv152/"],
        check=True,
    )
    ssh_merc(
        'export PATH="$HOME/.cargo/bin:$PATH"; '
        "rustc --edition 2021 -O /tmp/mrv152/mrv152_cx5_loopback.rs "
        "-C link-arg=/tmp/mrv152/mrv152_verbs_shim.o -C link-arg=-libverbs "
        "-o /tmp/mrv152/mrv152_cx5_loopback"
    )

    env = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "host": "mercury",
        "dev": "mlx5_2",
        "netdev": "ens6f1np1",
        "ip": "10.10.100.1",
        "sgid_index": 3,
        "cpu": 0,
        "numa_node": int(ssh_merc("cat /sys/class/infiniband/mlx5_2/device/numa_node")),
        "cpu_model": ssh_merc("lscpu | awk -F: '/Model name/{gsub(/^ +/,\"\",$2); print $2}'"),
        "scaling_governor": ssh_merc("cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "fw_ver": ssh_merc("cat /sys/class/infiniband/mlx5_2/fw_ver"),
        "board_id": ssh_merc("cat /sys/class/infiniband/mlx5_2/board_id"),
        "link_speed": ssh_merc("ethtool ens6f1np1 | awk -F: '/Speed:/{gsub(/^ +/,\"\",$2); print $2}'"),
        "reps": REPS,
        "warmup": WARMUP,
        "rounds": ROUNDS,
    }
    ENV_PATH.write_text(json.dumps(env, indent=2) + "\n")

    records = []
    for rep in range(1, REPS + 1):
        for size in SIZES:
            c0 = read_counters()
            out = ssh_merc(
                f"/tmp/mrv152/mrv152_cx5_loopback --dev mlx5_2 --sgid 3 --ip 10.10.100.1 "
                f"--cpu 0 --size {size} --warmup {WARMUP} --rounds {ROUNDS}"
            )
            c1 = read_counters()
            rec = json.loads(out)
            frag_cnt = rec["frag_cnt"]
            expected_pkts = (WARMUP + ROUNDS) * frag_cnt
            d_rcv = c1["unicast_rcv_packets"] - c0["unicast_rcv_packets"]
            d_phy_tx = c1["tx_packets_phy"] - c0["tx_packets_phy"]
            d_phy_rx = c1["rx_packets_phy"] - c0["rx_packets_phy"]
            rec["rep"] = rep
            rec["cfg"] = f"mercury_cx5_loopback_{size}_w1"
            rec["hw_delta"] = {
                "unicast_rcv_packets": d_rcv,
                "expected_pkts": expected_pkts,
                "tx_packets_phy": d_phy_tx,
                "rx_packets_phy": d_phy_rx,
            }
            assert rec["verified"], f"unverified run: {rec}"
            assert d_rcv == expected_pkts, f"hw rcv mismatch: {d_rcv} != {expected_pkts}"
            assert d_phy_tx == 0 and d_phy_rx == 0, f"unexpected phy traffic: tx={d_phy_tx}, rx={d_phy_rx}"
            records.append(rec)
            print(
                f"rep {rep} | {size:>4} B ({frag_cnt}f): p50={rec['p50_us']:.3f} us, "
                f"p99={rec['p99_us']:.3f} us, inv_rate={rec['inv_rate_us']:.3f} us, "
                f"hw_rcv=+{d_rcv} (phy=0)"
            )

    RUNS_PATH.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    print(f"Wrote {len(records)} verified runs to {RUNS_PATH}")


if __name__ == "__main__":
    main()
