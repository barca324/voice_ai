"""Batch audit of human-labelled calls -> agreement metrics.

Run:  python3 src/batch.py [N]        (default 10)
Picks a seeded mix of Fatal / Non-Fatal / Pass SquadStack calls from the audit CSV (S3 links download without a login),
transcribes them in ONE Sarvam batch job, audits each with the same pipeline as the site, and compares with the human labels.
Everything is cached, so a re-run costs nothing for work already done.
"""
import csv
import hashlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
import llm  # noqa: E402
import rubric as R  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATCH = os.path.join(ROOT, "out", "batch")
REC = os.path.join(ROOT, "data", "batch")
os.makedirs(BATCH, exist_ok=True)
os.makedirs(REC, exist_ok=True)
norm = lambda s: re.sub(r"\s+", " ", s).strip()


def human_rows():
    r = list(csv.reader(open(os.path.join(ROOT, "data/raw/Audit Source of truth.csv"), encoding="utf-8")))
    h = [norm(x) for x in r[0]]
    return [dict(zip(h, x)) for x in r[1:] if any(x)]


def pick(n, seed=7, exclude=()):
    rows = [x for x in human_rows() if x["Vendor"] == "SQUADSTACK" and "amazonaws" in x["Call Recording"]
            and x["Offer ID"] not in exclude and x["Overall Quality"] in ("Fatal", "Non-Fatal", "Pass")]
    rng = random.Random(seed)
    want = {"Fatal": round(n * 0.3), "Non-Fatal": round(n * 0.3)}
    want["Pass"] = n - want["Fatal"] - want["Non-Fatal"]
    out = []
    for q, k in want.items():
        pool = [x for x in rows if x["Overall Quality"] == q]
        out += rng.sample(pool, k)
    return out


def download(x):
    p = os.path.join(REC, x["Offer ID"] + ".mp3")
    if not os.path.exists(p):
        open(p, "wb").write(urllib.request.urlopen(x["Call Recording"], timeout=90).read())
    return p


def transcribe_batch(paths):
    """One Sarvam batch job for every file not already cached."""
    todo = [p for p in paths if not os.path.exists(os.path.join(audit.STT_CACHE, audit.sha(p) + ".json"))]
    if not todo:
        return
    from sarvamai import SarvamAI
    print(f"transcribing {len(todo)} files in one batch job", flush=True)
    client = SarvamAI(api_subscription_key=llm.key())
    job = client.speech_to_text_job.create_job(model="saaras:v4", mode="transcribe", language_code="unknown",
                                               with_diarization=True, num_speakers=2)
    job.upload_files(file_paths=todo)
    job.start()
    job.wait_until_complete()
    res = job.get_file_results()
    for f in res["failed"]:
        print("  STT FAILED", f["file_name"], f["error_message"], flush=True)
    out = tempfile.mkdtemp()
    job.download_outputs(output_dir=out)
    by_name = {os.path.basename(p).split(".")[0]: p for p in todo}
    for name in os.listdir(out):
        p = by_name.get(name.split(".")[0])
        if p:
            shutil.copy(os.path.join(out, name), os.path.join(audit.STT_CACHE, audit.sha(p) + ".json"))
    shutil.rmtree(out, ignore_errors=True)


def audit_one(x):
    t0 = time.time()
    p = os.path.join(REC, x["Offer ID"] + ".mp3")
    cache = os.path.join(audit.STT_CACHE, audit.sha(p) + ".json")
    if not os.path.exists(cache):
        return {"offer": x["Offer ID"], "error": "no transcript (STT failed)"}
    try:
        entries = audit.transcribe(p)
        rep = audit.build_report(entries, audit.judge_twice(entries), x["Offer ID"], "batch")
    except Exception as e:
        return {"offer": x["Offer ID"], "error": str(e)[:200]}
    rows = []
    for r in rep["rows"]:
        hv = x.get(R.HUMAN_COLUMNS.get(r["parameter"], ""), "").strip() or "NA"
        rows.append({"parameter": r["parameter"], "human": hv, "agent": r["verdict"], "disputed": r.get("disputed", False),
                     "confidence": r["confidence"], "evidence": r["evidence"][:140]})
    return {"offer": x["Offer ID"], "human_call": x["Call Quality"], "human_overall": x["Overall Quality"],
            "agent_overall": rep["overall"], "flagged": rep["risk"]["flag"], "flags": rep["risk"]["reasons"], "rows": rows,
            "seconds": round(time.time() - t0)}


SAME = lambda a, b: a == b or (a in ("Pass", "NA") and b in ("Pass", "NA"))


def summarize(res):
    ok = [r for r in res if "error" not in r]
    cells = [(r, c) for r in ok for c in r["rows"] if not (c["human"] == "NA" and c["agent"] == "NA")]
    allc = [(r, c) for r in ok for c in r["rows"]]
    strict = sum(1 for _, c in allc if c["human"] == c["agent"] or (c["human"] == "NA" and c["agent"] == "Pass"))
    lenient = sum(1 for _, c in allc if SAME(c["human"], c["agent"]))
    miss = [(r, c) for r, c in allc if not SAME(c["human"], c["agent"])]
    caught = sum(1 for r, c in miss if r["flagged"] or c["disputed"])

    def conf(key):
        m = {}
        for r in ok:
            m.setdefault(r[key], {}).setdefault(r["agent_overall"], 0)
            m[r[key]][r["agent_overall"]] += 1
        return m
    out = {"calls_requested": len(res), "calls_audited": len(ok), "share_audited": round(len(ok) / max(1, len(res)), 2),
           "parameter_agreement_lenient": f"{lenient}/{len(allc)}", "parameter_agreement_pct": round(100 * lenient / max(1, len(allc))),
           "parameter_agreement_strict": f"{strict}/{len(allc)}"}
    for label, key in (("vs_call_quality", "human_call"), ("vs_overall_quality", "human_overall")):
        m = [(r[key], r["agent_overall"]) for r in ok if r[key] in ("Fatal", "Non-Fatal", "Pass")]
        exact = sum(1 for h, a in m if h == a)
        hf = [(h, a) for h, a in m if h == "Fatal"]
        af = [(h, a) for h, a in m if a == "Fatal"]
        out[label] = {"exact": f"{exact}/{len(m)}", "fatal_recall": f"{sum(1 for h, a in hf if a == 'Fatal')}/{len(hf)}",
                      "fatal_precision": f"{sum(1 for h, a in af if h == 'Fatal')}/{len(af)}",
                      "nonfatal_or_worse_recall": f"{sum(1 for h, a in m if h != 'Pass' and a != 'Pass')}/{sum(1 for h, a in m if h != 'Pass')}",
                      "confusion_human_to_agent": conf(key)}
    out["disagreements"] = len(miss)
    out["disagreements_caught_by_review_flag"] = f"{caught}/{len(miss)}"
    out["calls_flagged"] = f"{sum(1 for r in ok if r['flagged'])}/{len(ok)}"
    pp = {}
    for r, c in miss:
        pp.setdefault(c["parameter"], []).append(f"{c['human']}->{c['agent']}")
    out["disagreements_by_parameter"] = {k: v for k, v in sorted(pp.items(), key=lambda kv: -len(kv[1]))}
    return out


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    pilot = {"144801537785", "144828385995", "144800859863"}
    chosen = pick(n, exclude=pilot)
    print("picked:", [(x["Offer ID"], x["Overall Quality"], x["Call Quality"]) for x in chosen], flush=True)
    paths = [download(x) for x in chosen]
    transcribe_batch(paths)
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(audit_one, chosen))
    json.dump(res, open(os.path.join(BATCH, "results.json"), "w"), ensure_ascii=False, indent=1)
    s = summarize(res)
    json.dump(s, open(os.path.join(BATCH, "summary.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(s, ensure_ascii=False, indent=1))
    for r in res:
        if "error" in r:
            print(r["offer"], "ERROR", r["error"])
        else:
            print(r["offer"], "human call/overall", r["human_call"], "/", r["human_overall"], "| agent", r["agent_overall"],
                  "| flagged" if r["flagged"] else "")


if __name__ == "__main__":
    main()
