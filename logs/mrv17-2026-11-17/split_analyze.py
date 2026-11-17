"""Where the window sits: from inline_pingpong --sample-card-next raw samples (sent,seen,acked,found)."""
import glob
import json
import re
import statistics

runs = {}
for line in open("/tmp/mrv17/runs.jsonl"):
    x = json.loads(line)
    if x["tag"].startswith("SPLIT2 ") and x.get("rc") == 0 and x.get("host"):
        runs[x["tag"]] = x


def pct(v, p):
    v = sorted(v)
    return v[(len(v) - 1) * p // 100] if v else None


rows = []
for tag, x in sorted(runs.items(), key=lambda kv: (int(re.search(r"W(\d+)", kv[0]).group(1)), kv[0])):
    out = open(x["out_path"]).read()
    m = re.search(r"card-next raw \(sent,seen,acked,found(?:,us)?\): (.*)", out)
    if not m:
        continue
    s = [tuple(int(v) for v in t.split(",")[:4]) for t in m.group(1).split()]
    s = s[len(s) // 4:]
    marked = "--mark-taken" in x["card_cmd"]
    infl = [a - b for a, b, c, d in s]
    unacked = [a - c for a, b, c, d in s]
    acked_unseen = [c - b for a, b, c, d in s]
    r = dict(tag=tag, mmsg=x["host"][0]["mmsg"], p50=x["host"][0]["p50"], empty=x["host"][0]["empty_pct"], n=len(s),
             inflight=pct(infl, 50), unacked=(pct(unacked, 10), pct(unacked, 50), pct(unacked, 90)),
             acked_unseen=(pct(acked_unseen, 10), pct(acked_unseen, 50), pct(acked_unseen, 90)))
    if marked:
        unfound = [a - d for a, b, c, d in s]
        found_unseen = [d - b for a, b, c, d in s]
        found_unacked = [d - c for a, b, c, d in s]
        r.update(unfound=(pct(unfound, 10), pct(unfound, 50), pct(unfound, 90)),
                 found_unseen=(pct(found_unseen, 10), pct(found_unseen, 50), pct(found_unseen, 90)),
                 found_unacked=(pct(found_unacked, 10), pct(found_unacked, 50), pct(found_unacked, 90)))
    card = x.get("card", {})
    r["card_per_busy"] = card.get("per_core")
    rows.append(r)
for r in rows:
    print(json.dumps(r))
