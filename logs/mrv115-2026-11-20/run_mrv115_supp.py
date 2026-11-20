#!/usr/bin/env python3
"""MRV-115 supplement: WC4 credit_cap=24 (below WC4 W_crit=28) closed-loop & Poisson,
plus WC1 credit_cap=64 closed-loop (eliminating W=64->128 cliff) & open-loop scaling point."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_mrv115 as base  # noqa: E402


def main():
    env = base.check_host_env()
    edac_before = base.read_card_edac()
    existing = [json.loads(l) for l in base.RUNS_JSONL.read_text().splitlines() if l.strip()]
    run_id = max(r["run_id"] for r in existing)
    print(f"Supplement starting at run_id={run_id + 1}, ENV={env}, EDAC={edac_before}", flush=True)

    with base.RUNS_JSONL.open("a", encoding="utf-8") as f:
        # WC4 is already running (-l 3-6), ensure T_pause = 0
        base.set_card_pause(0)
        wc4_windows = [1, 8, 16, 24, 32, 40, 48, 64, 128, 256]
        for rep in range(base.REPS):
            print(f"\n--- WC4 Supp Rep {rep} ---", flush=True)
            for w in wc4_windows:
                rounds, warmup = base.rounds_for_window(w)
                run_id += 1
                rec = base.run_one(
                    run_id,
                    "p2_cl_wc4",
                    f"cl_wc4_w{w}_credit_24",
                    4,
                    0,
                    rep,
                    ["--window", str(w), "--credit-cap", "24", "--rounds", str(rounds), "--warmup", str(warmup)],
                    {"shaping": "credit_24"},
                )
                base.append_rec(f, rec)

            seed = 115 + rep * 100
            for rate in [10.0, 13.5, 16.0, 20.0]:
                rounds, warmup = base.rounds_for_rate(rate)
                run_id += 1
                rec = base.run_one(
                    run_id,
                    "p1_wc4_poisson",
                    f"wc4_e_{rate:g}m_burst8_c24",
                    4,
                    0,
                    rep,
                    [
                        "--open-mps",
                        str(rate),
                        "--arrival",
                        "poisson",
                        "--seed",
                        str(seed),
                        "--credit-cap",
                        "24",
                        "--max-send-burst",
                        "8",
                        "--rounds",
                        str(rounds),
                        "--warmup",
                        str(warmup),
                    ],
                    {"shaping": "burst8_c24", "target_mps": rate},
                )
                base.append_rec(f, rec)

            for rate in [13.5, 16.0, 20.0]:
                rounds, warmup = base.rounds_for_rate(rate)
                run_id += 1
                rec = base.run_one(
                    run_id,
                    "p1_wc4_paced",
                    f"wc4_p_{rate:g}m_credit_24",
                    4,
                    0,
                    rep,
                    [
                        "--open-mps",
                        str(rate),
                        "--arrival",
                        "paced",
                        "--credit-cap",
                        "24",
                        "--rounds",
                        str(rounds),
                        "--warmup",
                        str(warmup),
                    ],
                    {"shaping": "credit_24", "target_mps": rate},
                )
                base.append_rec(f, rec)

        print("\n=== Switching to WC1 (-l 3) for credit_64 supplement ===", flush=True)
        base.start_card("3")
        base.set_card_pause(0)
        for rep in range(base.REPS):
            print(f"\n--- WC1 Supp Rep {rep} ---", flush=True)
            for w in [64, 128, 256]:
                rounds, warmup = base.rounds_for_window(w)
                run_id += 1
                rec = base.run_one(
                    run_id,
                    "p2_cl_wc1",
                    f"cl_wc1_w{w}_credit_64",
                    1,
                    0,
                    rep,
                    ["--window", str(w), "--credit-cap", "64", "--rounds", str(rounds), "--warmup", str(warmup)],
                    {"shaping": "credit_64"},
                )
                base.append_rec(f, rec)

            for rate in [20.0, 25.0, 30.0]:
                rounds, warmup = base.rounds_for_rate(rate)
                run_id += 1
                rec = base.run_one(
                    run_id,
                    "p1_wc1_paced",
                    f"wc1_p_{rate:g}m_credit_64",
                    1,
                    0,
                    rep,
                    [
                        "--open-mps",
                        str(rate),
                        "--arrival",
                        "paced",
                        "--credit-cap",
                        "64",
                        "--rounds",
                        str(rounds),
                        "--warmup",
                        str(warmup),
                    ],
                    {"shaping": "credit_64", "target_mps": rate},
                )
                base.append_rec(f, rec)

    edac_after = base.read_card_edac()
    print(f"\nSUPPLEMENT DONE (total run_id={run_id}), EDAC AFTER={edac_after}", flush=True)


if __name__ == "__main__":
    main()
