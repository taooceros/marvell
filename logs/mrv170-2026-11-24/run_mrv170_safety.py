#!/usr/bin/env python3
"""MRV-170 host-side hardware safety experiment harness (Boundaries 1-4).

Exception-safe atomic attempts ledger: every invocation logs `started` and
finalizes exit code / timeout / exception / validation status into `attempts.jsonl`.
Validation failures raise after logging so no failure can be silently treated as pass.
"""

import argparse
import json
import pathlib
import subprocess
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv170/logs/mrv170-2026-11-24")
RAW_DIR = OUT_DIR / "raw_safety"
ATTEMPTS_PATH = OUT_DIR / "attempts.jsonl"
RUNS_PATH = OUT_DIR / "safety_runs.jsonl"

CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]
CARD_BIN = "/root/bench/mrv170_safety"
MERC_UDP_BIN = "/tmp/mrv170/mrv170_udp_gen"
ETH_DEV = "0002:02:00.0,scalar_enable=1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_attempt_id() -> int:
    if not ATTEMPTS_PATH.exists():
        return 1
    with ATTEMPTS_PATH.open() as f:
        return sum(1 for _ in f) + 1


def log_attempt(record: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with ATTEMPTS_PATH.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def log_run(record: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with RUNS_PATH.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def ensure_card_dpdk_bound() -> None:
    cmd = CARD_SSH + [
        "tmux kill-session -t refl 2>/dev/null || true; "
        "ip link set eth0 down 2>/dev/null || true; "
        "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
        'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
        "rm -f /dev/hugepages/m170*"
    ]
    r = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"Failed to bind eth0 to vfio-pci: {r.stderr}")


def parse_json_line(text: str, prefix: str):
    for line in text.splitlines():
        if prefix in line:
            idx = line.index(prefix) + len(prefix)
            return json.loads(line[idx:].strip())
    return None


def validate_safety_record(case_id: str, p: dict, merc_p: dict | None = None, expected_count: int | None = None) -> tuple[bool, list[str]]:
    errs = []
    t = p.get("test")
    if t in ("b1_mseg_lpbk", "b1_mseg_wire"):
        if "naive_repro" in case_id:
            if p.get("mbuf_leak", 0) <= 0:
                errs.append("expected positive mbuf_leak for naive repro")
            if p.get("data_len_gt_buf_len", 0) <= 0:
                errs.append("expected data_len_gt_buf_len > 0 for naive repro")
        else:
            if expected_count is not None and p.get("recvd") != expected_count:
                errs.append(f"recvd={p.get('recvd')} != expected={expected_count}")
            if p.get("verified_ok") != p.get("recvd") or p.get("recvd", 0) == 0:
                errs.append(f"verified_ok={p.get('verified_ok')} != recvd={p.get('recvd')}")
            if p.get("mbuf_leak", -1) != 0:
                errs.append(f"mbuf_leak={p.get('mbuf_leak')}")
            if p.get("err_stale_tail", 0) != 0 or p.get("err_stale_mid", 0) != 0:
                errs.append("stale payload errors")
            if p.get("min_segs", 0) < 2:
                errs.append(f"min_segs={p.get('min_segs')} < 2")
            cs = p.get("cqe_stats", {})
            o = p.get("oracle", {})
            if cs.get("fatal_cq_err", 0) != 0 or o.get("cq_err", 0) != 0 or o.get("op_err", 0) != 0:
                errs.append("fatal/cq/op err")
            if not o.get("head_match", False):
                errs.append("oracle head mismatch")
    elif t == "b2_err_lpbk":
        if p.get("mbuf_leak", -1) != 0:
            errs.append(f"mbuf_leak={p.get('mbuf_leak')}")
        if not p.get("oracle", {}).get("head_match", False):
            errs.append("head mismatch")
        if p.get("memout", {}).get("post_memout_healthy_rx", 0) != 8:
            errs.append("post_memout_healthy_rx != 8")
        for c in p.get("cases", []):
            if c.get("healthy_followup_rx", 0) != 4:
                errs.append(f"{c.get('mode')} followup != 4")
    elif t == "b2_synth_anomalous":
        if not p.get("zero_seg", {}).get("naive_head_stuck", False):
            errs.append("expected naive head stuck on zero-seg")
        if p.get("zero_seg", {}).get("safe_fallback_ldadda", 0) < 1:
            errs.append("expected safe fallback_ldadda >= 1 on zero-seg")
        if p.get("non_rx", {}).get("safe_fallback_ldadda", 0) < 1:
            errs.append("expected safe fallback_ldadda >= 1 on non-rx")
    elif t == "b3_overflow_wire":
        if p.get("total_bad", 0) != 0 or p.get("head_mismatches", 0) != 0 or p.get("cq_errs", 0) != 0:
            errs.append("bad/head_mismatch/cq_err in b3 overflow")
        if p.get("total_ok", 0) != p.get("total_rx", 0) or p.get("total_ok", 0) < 100_000:
            errs.append(f"total_ok={p.get('total_ok')} != total_rx={p.get('total_rx')}")
        if p.get("mbuf_leak", -1) != 0:
            errs.append(f"mbuf_leak={p.get('mbuf_leak')}")
        if "npa" in case_id:
            if p.get("eth_stats", {}).get("imissed", 0) < 10_000:
                errs.append(f"expected imissed >= 10000 for npa drop, got {p.get('eth_stats', {}).get('imissed')}")
        else:
            if p.get("max_pending_seen", 0) < 3890:
                errs.append(f"max_pending_seen={p.get('max_pending_seen')} < 3890 (CQ did not saturate)")
        if merc_p is not None and merc_p.get("sent", 0) < 1_000_000:
            errs.append(f"merc sent={merc_p.get('sent')} too low")
    elif t == "b3_door_err_watchdog":
        if p.get("healthy_recvd", 0) != 20_000 or p.get("healthy_head_mismatch", 0) != 0 or p.get("healthy_cq_err", 0) != 0:
            errs.append("healthy watchdog failed")
        if p.get("door_err_cq_err_bit46", 0) != 1 or p.get("watchdog_detected_cq_err", 0) < 1:
            errs.append("door_err CQ_ERR not detected by watchdog")
    elif t == "b4_stop_start":
        if p.get("oracle_dirty_repro", {}).get("free_dirty_w0", 0) == 0:
            errs.append("expected dirty slots reproduced after dev_stop flush")
        if p.get("oracle_after_scrub", {}).get("free_dirty_w0", 1) != 0:
            errs.append("scrub left dirty w0")
        if p.get("total_bad", 0) != 0 or p.get("dirty_post_sync_any", 0) != 0 or p.get("head_mismatch_any", 0) != 0 or p.get("cq_err_any", 0) != 0 or p.get("mbuf_leak", 1) != 0:
            errs.append("stop-start stress errors")
    elif t == "b4_long_wrap_wire":
        target = expected_count or 10_000_000
        if merc_p is not None and merc_p.get("sent", 0) != target:
            errs.append(f"merc sent={merc_p.get('sent')} != target={target}")
        if p.get("recvd", 0) != target or p.get("verified_ok") != target:
            errs.append(f"long wrap recvd={p.get('recvd')} verified={p.get('verified_ok')} target={target}")
        if p.get("seq_out_of_order", -1) != 0:
            errs.append(f"seq_out_of_order={p.get('seq_out_of_order')} != 0")
        if p.get("err_bad", 0) != 0 or p.get("err_tail", 0) != 0 or p.get("mbuf_leak", 1) != 0:
            errs.append(f"long wrap integrity/leak error bad={p.get('err_bad')} tail={p.get('err_tail')} leak={p.get('mbuf_leak')}")
        o = p.get("oracle", {})
        if not o.get("head_match", False) or o.get("hw_pending", 1) != 0 or o.get("free_dirty_w0", 1) != 0 or o.get("free_dirty_w9", 1) != 0 or o.get("free_dirty_w10_15", 1) != 0:
            errs.append(f"long wrap oracle dirty/pending/head mismatch: {o}")
        if p.get("cqe_stats", {}).get("fatal_cq_err", 0) != 0:
            errs.append("long wrap fatal_cq_err")
    return (len(errs) == 0), errs


def run_card_only(
    case_id: str,
    card_args: list[str],
    timeout: int = 120,
    is_retry: bool = False,
    expected_count: int | None = None,
    raise_on_val_fail: bool = True,
) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    aid = next_attempt_id()
    log_path = RAW_DIR / f"attempt_{aid:04d}_{case_id}.log"
    card_cmd = f"rm -f /dev/hugepages/m170*; {CARD_BIN} -l 3 -a {ETH_DEV} --file-prefix=m170 -- " + " ".join(card_args)
    full_cmd = CARD_SSH + [card_cmd]
    t0 = utc_now()
    exit_code = -999
    err_str = None
    out_text = ""
    parsed = None
    try:
        r = subprocess.run(full_cmd, text=True, capture_output=True, timeout=timeout)
        exit_code = r.returncode
        out_text = r.stdout + ("\n---STDERR---\n" + r.stderr if r.stderr else "")
        parsed = parse_json_line(r.stdout, "MRV170_SAFETY_JSON ")
    except subprocess.TimeoutExpired as e:
        exit_code = -124
        err_str = f"TimeoutExpired({timeout}s)"
        so = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        se = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        out_text = so + ("\n---STDERR---\n" + se if se else "")
        parsed = parse_json_line(so, "MRV170_SAFETY_JSON ")
    except Exception as e:
        exit_code = -1
        err_str = repr(e)
    finally:
        t1 = utc_now()
        log_path.write_text(out_text)
        rec = {
            "attempt_id": aid,
            "case_id": case_id,
            "kind": "card_only",
            "is_retry": is_retry,
            "ts_start": t0,
            "ts_end": t1,
            "cmd": card_cmd,
            "exit_code": exit_code,
            "error": err_str,
            "log_path": str(log_path),
            "parsed": parsed,
        }
        log_attempt(rec)

    if exit_code != 0 or parsed is None:
        raise RuntimeError(f"case {case_id} failed rc={exit_code} err={err_str}, see {log_path}")
    ok, val_errs = validate_safety_record(case_id, parsed, None, expected_count)
    run_rec = {"case_id": case_id, "attempt_id": aid, "is_retry": is_retry, "ts": t1, "verified": ok, "val_errs": val_errs, **parsed}
    log_run(run_rec)
    tag = "PASS" if ok else f"FAIL({','.join(val_errs)})"
    print(f"[{tag}] {case_id} (attempt #{aid}): {json.dumps(parsed)[:240]}", flush=True)
    if not ok and raise_on_val_fail:
        raise RuntimeError(f"validation failed for {case_id}: {val_errs}")
    return run_rec


def run_wire_pair(
    case_id: str,
    card_args: list[str],
    merc_args: list[str],
    timeout: int = 120,
    is_retry: bool = False,
    expected_count: int | None = None,
    raise_on_val_fail: bool = True,
) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    aid = next_attempt_id()
    card_log = RAW_DIR / f"attempt_{aid:04d}_{case_id}_card.log"
    merc_log = RAW_DIR / f"attempt_{aid:04d}_{case_id}_merc.log"
    card_cmd = f"rm -f /dev/hugepages/m170*; {CARD_BIN} -l 3 -a {ETH_DEV} --file-prefix=m170 -- " + " ".join(card_args)
    merc_cmd = f"{MERC_UDP_BIN} " + " ".join(merc_args)
    t0 = utc_now()
    ready = False
    card_rc = -999
    merc_rc = -999
    card_text = ""
    merc_out = ""
    card_parsed = None
    merc_parsed = None
    err_str = None
    card_p = None
    try:
        card_p = subprocess.Popen(
            CARD_SSH + [card_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        card_lines = []
        t_wait = time.time()
        while time.time() - t_wait < 20.0:
            line = card_p.stdout.readline()
            if not line:
                if card_p.poll() is not None:
                    break
                time.sleep(0.02)
                continue
            card_lines.append(line)
            if "MRV170_WIRE_READY" in line:
                ready = True
                break

        if ready:
            time.sleep(0.1)
            mr = subprocess.run(MERC_SSH + [merc_cmd], text=True, capture_output=True, timeout=timeout)
            merc_rc = mr.returncode
            merc_out = mr.stdout + ("\n---STDERR---\n" + mr.stderr if mr.stderr else "")
        else:
            card_p.kill()

        rest_out, _ = card_p.communicate(timeout=timeout)
        card_lines.append(rest_out or "")
        card_text = "".join(card_lines)
        card_rc = card_p.returncode
        card_parsed = parse_json_line(card_text, "MRV170_SAFETY_JSON ")
        merc_parsed = parse_json_line(merc_out, "MRV170_UDP_GEN_JSON ")
    except subprocess.TimeoutExpired as e:
        err_str = f"TimeoutExpired({timeout}s)"
        if card_p is not None and card_p.poll() is None:
            card_p.kill()
            try:
                ro, _ = card_p.communicate(timeout=5)
                card_text += ro or ""
            except Exception:
                pass
        so = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        se = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        merc_out += so + ("\n---STDERR---\n" + se if se else "")
    except Exception as e:
        err_str = repr(e)
        if card_p is not None and card_p.poll() is None:
            card_p.kill()
    finally:
        t1 = utc_now()
        card_log.write_text(card_text)
        merc_log.write_text(merc_out)
        rec = {
            "attempt_id": aid,
            "case_id": case_id,
            "kind": "wire_pair",
            "is_retry": is_retry,
            "ts_start": t0,
            "ts_end": t1,
            "card_cmd": card_cmd,
            "merc_cmd": merc_cmd,
            "ready": ready,
            "card_exit_code": card_rc,
            "merc_exit_code": merc_rc,
            "error": err_str,
            "card_log_path": str(card_log),
            "merc_log_path": str(merc_log),
            "card_parsed": card_parsed,
            "merc_parsed": merc_parsed,
        }
        log_attempt(rec)

    if not ready or card_rc != 0 or merc_rc != 0 or card_parsed is None:
        raise RuntimeError(f"wire case {case_id} failed ready={ready} card_rc={card_rc} merc_rc={merc_rc} err={err_str}")
    ok, val_errs = validate_safety_record(case_id, card_parsed, merc_parsed, expected_count)
    run_rec = {
        "case_id": case_id,
        "attempt_id": aid,
        "is_retry": is_retry,
        "ts": t1,
        "verified": ok,
        "val_errs": val_errs,
        "merc_gen": merc_parsed,
        **card_parsed,
    }
    log_run(run_rec)
    tag = "PASS" if ok else f"FAIL({','.join(val_errs)})"
    print(f"[{tag}] {case_id} (attempt #{aid}): {json.dumps(card_parsed)[:240]}", flush=True)
    if not ok and raise_on_val_fail:
        raise RuntimeError(f"validation failed for {case_id}: {val_errs}")
    return run_rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["all", "bare", "b1", "b2", "b2synth", "b3", "b3npa", "b3door", "b4", "b4wrap"], default="all")
    ap.add_argument("--retry", action="store_true")
    args = ap.parse_args()

    ensure_card_dpdk_bound()

    if args.suite in ("all", "bare"):
        for rep in range(1, 4):
            run_card_only(f"bare_probe_rep{rep}", ["--test", "bare-probe"], is_retry=args.retry)

    if args.suite in ("all", "b1"):
        run_card_only(
            "b1_lpbk_4k_naive_repro",
            ["--test", "b1-mseg-lpbk", "--policy", "cqe-direct-naive", "--mtu", "9000",
             "--buf-size", "2176", "--frame-bytes", "4096", "--count", "64", "--poison", "true"],
            is_retry=args.retry,
        )
        for name, buf_sz, fbytes, pol, cnt in [
            ("b1_lpbk_4k_2seg_safe", 2176, 4096, "cqe-direct-safe", 200000),
            ("b1_lpbk_6k_3seg_safe", 2176, 6144, "cqe-direct-safe", 200000),
            ("b1_lpbk_8k_4seg_safe", 2176, 8192, "cqe-direct-safe", 200000),
            ("b1_lpbk_9000_6seg_safe", 1664, 9000, "cqe-direct-safe", 200000),
            ("b1_lpbk_9000_6seg_peek", 1664, 9000, "cqe-peek", 100000),
            ("b1_lpbk_9000_6seg_default", 1664, 9000, "default", 100000),
        ]:
            run_card_only(
                name,
                ["--test", "b1-mseg-lpbk", "--policy", pol, "--mtu", "9000",
                 "--buf-size", str(buf_sz), "--frame-bytes", str(fbytes),
                 "--count", str(cnt), "--poison", "true", "--timeout-sec", "45"],
                timeout=90,
                is_retry=args.retry,
                expected_count=cnt,
            )

        run_wire_pair(
            "b1_wire_1514_4seg_naive_repro",
            ["--test", "b1-mseg-wire", "--policy", "cqe-direct-naive", "--buf-size", "512",
             "--count", "128", "--poison", "true", "--timeout-sec", "15"],
            ["--payload", "1472", "--count", "128", "--threads", "1", "--batch", "16", "--pace-us", "100"],
            is_retry=args.retry,
        )
        for name, buf_sz, pol, cnt in [
            ("b1_wire_1514_4seg_safe", 512, "cqe-direct-safe", 200000),
            ("b1_wire_1514_5seg_safe", 384, "cqe-direct-safe", 200000),
            ("b1_wire_1514_5seg_peek", 384, "cqe-peek", 100000),
            ("b1_wire_1514_5seg_default", 384, "default", 100000),
        ]:
            run_wire_pair(
                name,
                ["--test", "b1-mseg-wire", "--policy", pol, "--buf-size", str(buf_sz),
                 "--count", str(cnt), "--poison", "true", "--timeout-sec", "30"],
                ["--payload", "1472", "--count", str(cnt), "--threads", "1", "--batch", "32", "--pace-us", "50"],
                timeout=60,
                is_retry=args.retry,
                expected_count=cnt,
            )

    if args.suite in ("all", "b2"):
        for name, pol, drop_err in [
            ("b2_err_lpbk_safe_drop", "cqe-direct-safe", True),
            ("b2_err_lpbk_safe_pass", "cqe-direct-safe", False),
            ("b2_err_lpbk_naive", "cqe-direct-naive", False),
            ("b2_err_lpbk_peek", "cqe-peek", False),
            ("b2_err_lpbk_default", "default", False),
        ]:
            extra = ["--drop-err"] if drop_err else []
            run_card_only(
                name,
                ["--test", "b2-err-lpbk", "--policy", pol, "--mtu", "1500", "--buf-size", "2176"] + extra,
                is_retry=args.retry,
            )
    if args.suite in ("all", "b2", "b2synth"):
        run_card_only("b2_synth_anomalous", ["--test", "b2-synth-anomalous"], is_retry=args.retry)

    if args.suite in ("all", "b3"):
        for name, pol in [
            ("b3_overflow_wire_safe", "cqe-direct-safe"),
            ("b3_overflow_wire_peek", "cqe-peek"),
            ("b3_overflow_wire_default", "default"),
        ]:
            run_wire_pair(
                name,
                ["--test", "b3-overflow-wire", "--policy", pol, "--no-fc", "--stall-ms", "15",
                 "--cycles", "30", "--timeout-sec", "30"],
                ["--payload", "128", "--duration-ms", "3000", "--threads", "4", "--batch", "64"],
                timeout=60,
                is_retry=args.retry,
            )
    if args.suite in ("all", "b3", "b3npa"):
        run_wire_pair(
            "b3_overflow_npa_safe",
            ["--test", "b3-overflow-wire", "--policy", "cqe-direct-safe", "--no-fc",
             "--pool-size", "2048", "--stall-ms", "15", "--cycles", "30", "--timeout-sec", "30"],
            ["--payload", "128", "--duration-ms", "3000", "--threads", "4", "--batch", "64"],
            timeout=60,
            is_retry=args.retry,
        )
    if args.suite in ("all", "b3", "b3door"):
        # Note: probe whether CN106 latches CQ_ERR bit 46 after DOOR_ERR; do not abort rest of suite on negative silicon finding
        run_card_only(
            "b3_door_err_watchdog",
            ["--test", "b3-door-err-watchdog"],
            is_retry=args.retry,
            raise_on_val_fail=False,
        )

    if args.suite in ("all", "b4"):
        run_card_only(
            "b4_stop_start_50cycles",
            ["--test", "b4-stop-start", "--cycles", "50", "--timeout-sec", "60"],
            timeout=120,
            is_retry=args.retry,
        )
    if args.suite in ("all", "b4", "b4wrap"):
        run_wire_pair(
            "b4_long_wrap_10m_safe",
            ["--test", "b4-long-wrap-wire", "--policy", "cqe-direct-safe", "--count", "10000000",
             "--watchdog-interval", "4096", "--full-clear", "true", "--timeout-sec", "90"],
            ["--payload", "64", "--count", "10000000", "--threads", "1", "--batch", "64"],
            timeout=120,
            is_retry=args.retry,
            expected_count=10_000_000,
        )


if __name__ == "__main__":
    main()
