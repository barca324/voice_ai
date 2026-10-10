"""Audit N human-confirmed FAILED calls (for root-cause grouping).

Run:  python3 src/failed30.py [N]     (default 30)
Only calls the human auditors marked Fatal or Non-Fatal on at least one conversation parameter are chosen, so every call
has a real failure to explain. Calls already used in the pilot or the first batch are excluded.
"""
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
import batch  # noqa: E402
import rubric as R  # noqa: E402

ROOT = batch.ROOT
OUT = os.path.join(ROOT, "out", "failed30")
os.makedirs(OUT, exist_ok=True)


def human_fails(x):
    return [p for p, col in R.HUMAN_COLUMNS.items() if x.get(col, "").strip() in ("Fatal", "Non-Fatal")]


def pick_failed(n, seed=11, exclude=()):
    rows = [x for x in batch.human_rows() if x["Vendor"] == "SQUADSTACK" and "amazonaws" in x["Call Recording"]
            and x["Offer ID"] not in exclude and x["Overall Quality"] in ("Fatal", "Non-Fatal") and human_fails(x)]
    rng = random.Random(seed)
    fatal = [x for x in rows if x["Overall Quality"] == "Fatal"]
    nonfatal = [x for x in rows if x["Overall Quality"] == "Non-Fatal"]
    k = round(n * 0.6)
    return rng.sample(fatal, k) + rng.sample(nonfatal, n - k)


def transcribe_chunks(paths, size=15):
    for i in range(0, len(paths), size):
        batch.transcribe_batch(paths[i:i + size])


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    used = {"144801537785", "144828385995", "144800859863"}
    prev = os.path.join(ROOT, "out", "batch", "results.json")
    if os.path.exists(prev):
        used |= {r["offer"] for r in json.load(open(prev))}
    chosen = pick_failed(n, exclude=used)
    print(f"picked {len(chosen)} failed calls", flush=True)
    paths = [batch.download(x) for x in chosen]
    transcribe_chunks(paths)

    def one(x):
        r = batch.audit_one(x)
        r["human_fails"] = human_fails(x)
        r["human_remarks"] = {p: x.get(col.replace("Accuracy", "Accuracy") + " Remarks", "") for p, col in []}
        r["remarks"] = (x.get("Overall Remarks", "") or "")[:300]
        r["category"] = x.get("Category", "")
        return r
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(one, chosen))
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), ensure_ascii=False, indent=1)
    ok = [r for r in res if "error" not in r]
    print(f"audited {len(ok)}/{len(res)}", flush=True)
    print("done", time.strftime("%H:%M:%S"), flush=True)


if __name__ == "__main__":
    main()
