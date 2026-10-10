"""Agreement between the automatic audit and the human auditors, over every call that has both verdicts."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import batch  # noqa: E402

ROOT = batch.ROOT
SOURCES = [("mixed", os.path.join(ROOT, "out", "batch", "results.json")), ("failed", os.path.join(ROOT, "out", "failed30", "results.json"))]


def compute():
    res, parts = [], {}
    for name, fp in SOURCES:
        if os.path.exists(fp):
            ok = [r for r in json.load(open(fp)) if "error" not in r]
            parts[name] = len(ok)
            res += ok
    if not res:
        return None
    s = batch.summarize(res)
    tp = fn = fp = 0
    for r in res:
        for c in r["rows"]:
            h, a = c["human"], c["agent"]
            if h in ("Fatal", "Non-Fatal"):
                tp += a in ("Fatal", "Non-Fatal")
                fn += a not in ("Fatal", "Non-Fatal")
            elif a in ("Fatal", "Non-Fatal"):
                fp += 1
    oq = s["vs_overall_quality"]
    hum_fatal = sum(1 for r in res if r["human_overall"] == "Fatal")
    caught = sum(1 for r in res if r["human_overall"] == "Fatal" and r["agent_overall"] == "Fatal")
    sv = None
    svp = os.path.join(ROOT, "out", "saved_values", "results.json")
    if os.path.exists(svp):
        d = json.load(open(svp))
        sv = {"calls": d["calls"], "testable": d["testable"], "flagged_item": d["flagged_item"], "label": d["label"],
              "rows": [{"offer": r["offer"], "testable": r["testable"], "recorded": r.get("recorded", {}), "human": r.get("human_items", {}),
                        "audit": r.get("audit_items", {}), "hit": r.get("flagged_item", False), "why": r.get("why", "")} for r in d["results"]]}
    return {"saved_values_test": sv, "calls": len(res), "parts": parts, "overall_exact": oq["exact"], "confusion_overall": oq["confusion_human_to_agent"],
            "confusion_call": s["vs_call_quality"]["confusion_human_to_agent"], "exact_call": s["vs_call_quality"]["exact"],
            "fatal_calls": hum_fatal, "fatal_caught": caught, "fatal_caught_pct": round(100 * caught / max(1, hum_fatal)),
            "human_failures": tp + fn, "failures_caught": tp, "failures_caught_pct": round(100 * tp / max(1, tp + fn)),
            "extra_failures": fp, "disagreements_by_parameter": s["disagreements_by_parameter"],
            "disagreements": s["disagreements"], "calls_flagged": s["calls_flagged"],
            "why": "The auditors compare what the buyer said with the values the bot recorded (from vendor dashboards and logs). Our audit only has the call audio, so it cannot see what the bot recorded."}
