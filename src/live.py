"""Live layer: match every newly audited call's problems to the existing patterns.

assign_job(job)   one language-model call: each problem of the call -> a pattern (or none)
aggregate(jobs)   totals for the dashboard's live band
Patterns come from out/insights/insights_manual.json (the reviewed grouping). Results are automatic and unconfirmed.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERNS = os.path.join(ROOT, "out", "insights", "insights_manual.json")

SCHEMA = {"type": "object", "properties": {"assignments": {"type": "array", "items": {"type": "object", "properties": {
    "issue": {"type": "integer"}, "pattern": {"type": "integer"}}, "required": ["issue", "pattern"], "additionalProperties": False}}},
    "required": ["assignments"], "additionalProperties": False}

SYSTEM = """You match newly found problems from a voice-bot call to a list of known failure patterns.
For each numbered problem choose the number of the ONE pattern whose fix would also fix it. Use -1 when none of the patterns
really fits; do not force a match. Answer with one assignment per problem."""


def patterns():
    if not os.path.exists(PATTERNS):
        return []
    return json.load(open(PATTERNS))["clusters"]


def severity(job, parameter):
    for r in (job.get("report") or {}).get("rows", []):
        if r["parameter"] == parameter:
            return r["verdict"] if r["verdict"] in ("Fatal", "Non-Fatal") else "Observation"
    return "Observation"


def assign_job(job):
    """Fills job['live'] = {'items': [{parameter, root_cause, verdict, pattern}]}. Never raises."""
    items = [{"parameter": i["parameter"], "root_cause": i["root_cause"], "verdict": severity(job, i["parameter"]), "pattern": None}
             for i in (job.get("issues") or [])]
    try:
        pats = patterns()
        has_logs = bool(job.get("recorded_values"))
        usable = [k for k, p in enumerate(pats) if has_logs or p.get("evidence_type") != "logs"]  # log-only patterns need the bot's saved values
        if items and usable:
            plist = "\n".join(f"{k}. {pats[k]['title']}: {pats[k]['plain_summary']}" for k in usable)
            ilist = "\n".join(f"{n}. [{x['parameter']}] {x['root_cause']}" for n, x in enumerate(items))
            out = llm.chat(SYSTEM, f"PATTERNS:\n{plist}\n\nPROBLEMS:\n{ilist}", schema=SCHEMA, name="assign", max_tokens=800)
            for a in out["assignments"]:
                if 0 <= a["issue"] < len(items) and a["pattern"] in usable:
                    items[a["issue"]]["pattern"] = a["pattern"]
        job["live"] = {"items": items, "ok": True}
    except Exception as e:
        job["live"] = {"items": items, "ok": False, "error": str(e)[:150]}


def effective_items(job):
    """The call's problems after the human review is applied (if there is one)."""
    base = (job.get("live") or {}).get("items", [])
    rv = job.get("review")
    if not rv:
        return [dict(it, confirmed=False) for it in base]
    out = []
    for i, it in enumerate(base):
        d = (rv.get("items") or {}).get(str(i), {})
        a = d.get("action", "confirm")
        if a == "not_a_problem":
            continue
        pat = d.get("pattern") if a == "reassign" else it["pattern"]
        out.append(dict(it, pattern=pat if isinstance(pat, int) and pat >= 0 else (None if a == "reassign" else it["pattern"]),
                        verdict=d.get("verdict") or it["verdict"], confirmed=True))
    for ad in rv.get("added", []):
        out.append({"parameter": ad["parameter"], "root_cause": ad.get("note") or "Added by the reviewer", "verdict": ad["verdict"],
                    "pattern": ad.get("pattern") if isinstance(ad.get("pattern"), int) and ad.get("pattern") >= 0 else None,
                    "confirmed": True, "added": True})
    return out


def final_overall(job):
    rv = job.get("review") or {}
    return rv.get("overall") or job["report"]["overall"]


def needs_review(job):
    return counted(job) and "review" not in job and (job["report"]["risk"]["flag"] or bool((job.get("live") or {}).get("items")))


def review_stats(jobs):
    rev = [j for j in jobs if counted(j) and j.get("review")]
    kept = moved = dropped = added = sev_changed = 0
    reasons = {}
    overall_same = overall_up = overall_down = 0
    rank = {"Pass": 0, "Non-Fatal": 1, "Fatal": 2}
    for j in rev:
        rv = j["review"]
        for d in (rv.get("items") or {}).values():
            a = d.get("action", "confirm")
            kept += a == "confirm"
            moved += a == "reassign"
            dropped += a == "not_a_problem"
            sev_changed += bool(d.get("verdict"))
            if d.get("reason"):
                reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
        added += len(rv.get("added", []))
        auto, fin = j["report"]["overall"], rv.get("overall") or j["report"]["overall"]
        if auto == fin:
            overall_same += 1
        elif rank.get(fin, 0) > rank.get(auto, 0):
            overall_up += 1
        else:
            overall_down += 1
    real = kept + moved
    total = kept + moved + dropped
    pct = lambda a, b: round(100 * a / b) if b else None
    return {"reviewed_calls": len(rev), "to_review": sum(1 for j in jobs if needs_review(j)),
            "overall_kept": overall_same, "overall_raised": overall_up, "overall_lowered": overall_down,
            "overall_kept_pct": pct(overall_same, len(rev)),
            "problems_reviewed": total, "problems_real_pct": pct(real, total), "false_problems": dropped,
            "matches_correct": kept, "matches_moved": moved, "match_accuracy_pct": pct(kept, kept + moved),
            "missed_problems_added": added, "recall_pct": pct(real, real + added), "severity_changed": sev_changed,
            "override_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1]))}


def counted(job):
    """Every call with a finished, complete audit counts. (Simulated test conversations are not real calls.)"""
    return (job.get("status") == "done" and job.get("report") and job["report"].get("source") != "simulated"
            and not job["report"].get("incomplete"))


def aggregate(jobs):
    calls = [j for j in jobs if counted(j)]
    pending = sum(1 for j in calls if "live" not in j)
    by = {}
    by_param = {}
    recent = []
    unmatched = matched = 0
    for j in sorted(calls, key=lambda x: -x["created"]):
        items = effective_items(j)
        for it in items:
            if it["verdict"] in ("Fatal", "Non-Fatal"):
                bp = by_param.setdefault(it["parameter"], {"Fatal": 0, "Non-Fatal": 0})
                bp[it["verdict"]] += 1
            if it["pattern"] is None:
                unmatched += 1
            else:
                matched += 1
                d = by.setdefault(it["pattern"], {"issues": 0, "calls": set(), "fatal": 0, "confirmed": 0})
                d["issues"] += 1
                d["confirmed"] += bool(it.get("confirmed"))
                d["calls"].add(j["id"])
                d["fatal"] += it["verdict"] == "Fatal"
        recent.append({"id": j["id"], "filename": j["filename"], "created": j["created"], "overall": final_overall(j),
                       "flagged": j["report"]["risk"]["flag"], "items": items, "assigned": "live" in j, "reviewed": "review" in j})
    return {"calls": len(calls), "with_problems": sum(1 for r in recent if r["items"]), "problems": matched + unmatched,
            "matched": matched, "unmatched": unmatched, "pending": pending, "by_parameter": by_param,
            "by_pattern": {k: {"issues": v["issues"], "calls": len(v["calls"]), "fatal": v["fatal"], "confirmed": v["confirmed"]} for k, v in by.items()},
            "incomplete": sum(1 for j in jobs if j.get("status") == "done" and (j.get("report") or {}).get("incomplete")),
            "reviewed_calls": sum(1 for j in calls if "review" in j), "to_review": sum(1 for j in calls if needs_review(j)),
            "recent": recent[:12]}


def collect_issues(jobs):
    out = []
    for j in jobs:
        if counted(j):
            pats = patterns()
            for it in effective_items(j):
                hint = pats[it["pattern"]]["title"] if it.get("confirmed") and it.get("pattern") is not None and it["pattern"] < len(pats) else ""
                out.append({"call": j["filename"], **it, "hint": hint})
    return out
