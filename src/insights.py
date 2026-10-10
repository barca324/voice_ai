"""Cross-call root-cause grouping.

Run:  python3 src/insights.py
Reads out/failed30/results.json (30 audited, human-confirmed failed calls) and writes out/insights/insights.json:
  1. per call: root causes (what the buyer did, what the bot did wrong, which layer is responsible), grounded in the
     human auditors' own remarks and the diarized transcript
  2. clusters across calls, ranked by severity x frequency
  3. one fix proposal per top cluster, written after checking the existing prompt so rules are not duplicated
  4. grouping-quality figures and a CSV a human can use to group the same issues by hand
"""
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
import batch  # noqa: E402
import fixer  # noqa: E402
import llm  # noqa: E402
import rubric as R  # noqa: E402

ROOT = batch.ROOT
SRC = os.path.join(ROOT, "out", "failed30", "results.json")
OUT = os.path.join(ROOT, "out", "insights")
os.makedirs(OUT, exist_ok=True)

LAYERS = ["prompt", "speech_recognition", "language_config", "option_lists", "flow_logic", "unclear"]

CALL_SCHEMA = {"type": "object", "properties": {"issues": {"type": "array", "items": {"type": "object", "properties": {
    "parameter": {"type": "string"},
    "trigger": {"type": "string"}, "bot_error": {"type": "string"}, "quote": {"type": "string"},
    "layer": {"type": "string", "enum": LAYERS}},
    "required": ["parameter", "trigger", "bot_error", "quote", "layer"], "additionalProperties": False}}},
    "required": ["issues"], "additionalProperties": False}

CALL_SYSTEM = """You explain why a call between IndiaMART's AI voice bot (BOT) and a buyer failed, for a team that fixes the bot.
You get the diarized transcript, the rubric parameters the human auditors marked as failures, and the auditors' own remarks.
Write ONE issue per failed parameter (use the parameter names given):
- trigger: what the buyer said or did that set it off, in one plain sentence (no jargon).
- bot_error: what the bot did wrong, in one plain sentence.
- quote: a short verbatim quote (under 20 words) from the transcript that shows it. Copy it exactly; if none fits, use "".
- layer: which part most likely needs the fix: prompt (the bot's instructions), speech_recognition (the bot misheard), language_config
  (a language the bot was not set up to speak), option_lists (the product's size/capacity/option lists were wrong or too narrow),
  flow_logic (order or routing of the conversation), unclear.
Base this on the auditors' remarks first, then the transcript. Never invent facts. Keep every field short."""

CLUSTER_SCHEMA = {"type": "object", "properties": {"clusters": {"type": "array", "items": {"type": "object", "properties": {
    "title": {"type": "string"}, "plain_summary": {"type": "string"},
    "layer": {"type": "string", "enum": LAYERS}, "members": {"type": "array", "items": {"type": "integer"}}},
    "required": ["title", "plain_summary", "layer", "members"], "additionalProperties": False}}},
    "required": ["clusters"], "additionalProperties": False}

CLUSTER_SYSTEM = """You group call-failure issues from IndiaMART's voice bot by ROOT CAUSE, so one fix can solve a whole group.
Each issue has an id, a rubric parameter, what the buyer did, and what the bot did wrong. Group issues that would be fixed by the
same change, even when their rubric parameters differ (for example 'bot repeats a question' under Looping and under Quantity
Probing may be one cause). Rules:
- Make between 4 and 8 groups. Every issue id must appear in exactly one group. Use a final group titled "One-off issues" only for
  issues that fit nowhere else.
- title: a plain-English name a new team member understands, under 9 words, describing the behaviour (not the parameter).
- plain_summary: two short sentences: what happens, and why it hurts the lead.
- layer: the part that most likely needs the fix (prompt / speech_recognition / language_config / option_lists / flow_logic / unclear).
- members: the issue ids."""

FIX_SCHEMA = {"type": "object", "properties": {
    "fix_type": {"type": "string", "enum": ["prompt_patch", "config_change", "speech_recognition_check", "option_list_update"]},
    "change": {"type": "string"}, "where": {"type": "string"},
    "already_covered": {"type": "string"}, "risk": {"type": "string"}, "test_buyer": {"type": "string"}},
    "required": ["fix_type", "change", "where", "already_covered", "risk", "test_buyer"], "additionalProperties": False}

FIX_SYSTEM = """You propose ONE fix for a group of related failures in IndiaMART's voice bot. You get the group's issues and
excerpts of the bot's CURRENT prompt that may already cover the behaviour. Rules:
- First check the excerpts. If an existing rule already covers the behaviour, say which one in already_covered and propose
  strengthening or clarifying it (fix_type prompt_patch), never a duplicate rule. If none does, write "No existing rule found".
- fix_type: prompt_patch for behaviour the instructions control; config_change if the bot was not set up for a language or setting;
  speech_recognition_check if the bot misheard; option_list_update if the product's option lists were wrong.
- change: for prompt_patch, ready-to-paste instruction text of at most 4 lines; otherwise one sentence on what to change and who owns it.
- where: where in the prompt or config it goes.
- risk: one sentence on what else this change could break.
- test_buyer: a two-sentence description of a simulated buyer that triggers this failure, so the fix can be tested."""


def remarks_for(row):
    out = []
    for k, v in row.items():
        if k.endswith("Remarks") and v and v.strip() and v.strip().upper() != "NA":
            out.append(f"{k.replace(' Remarks', '').replace('Remarks', '').strip()}: {v.strip()}")
    return out[:8]


def call_issues(item, hrow):
    p = os.path.join(batch.REC, item["offer"] + ".mp3")
    entries = audit.transcribe(p)
    sev = {x: hrow.get(R.HUMAN_COLUMNS[x], "").strip() for x in item["human_fails"]}
    user = (f"Failed parameters (human verdicts): {json.dumps(sev, ensure_ascii=False)}\n"
            f"Auditor remarks: {json.dumps(remarks_for(hrow), ensure_ascii=False)}\n\nTRANSCRIPT:\n{audit.fmt(entries)[:9000]}")
    out = llm.chat(CALL_SYSTEM, user, schema=CALL_SCHEMA, name="issues", max_tokens=1600)
    res = []
    for i in out["issues"]:
        verdict = sev.get(i["parameter"], "")
        if verdict not in ("Fatal", "Non-Fatal"):
            continue
        agent = next((r for r in item["rows"] if r["parameter"] == i["parameter"]), {})
        res.append({"offer": item["offer"], "parameter": i["parameter"], "human_verdict": verdict,
                    "agent_verdict": agent.get("agent", "NA"), "trigger": i["trigger"], "bot_error": i["bot_error"],
                    "quote": i["quote"], "layer": i["layer"]})
    return res


def main():
    t0 = time.time()
    items = [r for r in json.load(open(SRC)) if "error" not in r]
    humans = {x["Offer ID"]: x for x in batch.human_rows()}
    with ThreadPoolExecutor(3) as ex:
        per_call = list(ex.map(lambda it: call_issues(it, humans[it["offer"]]), items))
    issues = []
    for lst in per_call:
        for i in lst:
            i["id"] = len(issues)
            issues.append(i)
    print(f"{len(issues)} issues from {len(items)} calls", flush=True)

    listing = "\n".join(f"{i['id']}. [{i['parameter']}] buyer: {i['trigger']} | bot: {i['bot_error']}" for i in issues)
    cl = llm.chat(CLUSTER_SYSTEM, listing, schema=CLUSTER_SCHEMA, name="clusters", max_tokens=3500)["clusters"]
    seen, clusters = set(), []
    for c in cl:
        ms = [m for m in c["members"] if 0 <= m < len(issues) and m not in seen]
        seen.update(ms)
        if ms:
            clusters.append({**c, "members": ms})
    left = [i["id"] for i in issues if i["id"] not in seen]
    if left:
        one = next((c for c in clusters if c["title"].lower().startswith("one-off")), None)
        if one:
            one["members"] += left
        else:
            clusters.append({"title": "One-off issues", "plain_summary": "Issues that did not fit another group.",
                             "layer": "unclear", "members": left})

    W = {"Fatal": 3, "Non-Fatal": 1}
    for c in clusters:
        mem = [issues[m] for m in c["members"]]
        c["issues"] = len(mem)
        c["calls"] = len({m["offer"] for m in mem})
        c["fatal"] = sum(1 for m in mem if m["human_verdict"] == "Fatal")
        c["score"] = sum(W[m["human_verdict"]] for m in mem)
        c["parameters"] = sorted({m["parameter"] for m in mem})
        c["examples"] = [{"offer": m["offer"], "parameter": m["parameter"], "verdict": m["human_verdict"], "trigger": m["trigger"],
                          "bot_error": m["bot_error"], "quote": m["quote"]} for m in mem[:4]]
        c["detected_by_agent"] = sum(1 for m in mem if m["agent_verdict"] in ("Fatal", "Non-Fatal"))
    clusters.sort(key=lambda c: (c["title"].lower().startswith("one-off"), -c["score"], -c["calls"]))
    for r, c in enumerate(clusters, 1):
        c["rank"] = r

    def propose(c):
        if c["title"].lower().startswith("one-off"):
            return None
        mem = [issues[m] for m in c["members"]]
        ex = "\n".join(f"- [{m['parameter']}] buyer: {m['trigger']} | bot: {m['bot_error']}" for m in mem[:10])
        user = (f"GROUP: {c['title']}\n{c['plain_summary']}\nLikely layer: {c['layer']}\nISSUES:\n{ex}\n\n"
                f"EXCERPTS OF THE CURRENT PROMPT:\n{fixer.excerpts(c['parameters'], ex, max_chars=7000)}")
        try:
            return llm.chat(FIX_SYSTEM, user, schema=FIX_SCHEMA, name="fix", max_tokens=1200)
        except Exception as e:
            return {"error": str(e)[:150]}
    top = [c for c in clusters if not c["title"].lower().startswith("one-off")][:4]
    with ThreadPoolExecutor(2) as ex:
        for c, f in zip(top, ex.map(propose, top)):
            c["fix"] = f

    by_param = {}
    for i in issues:
        d = by_param.setdefault(i["parameter"], {"Fatal": 0, "Non-Fatal": 0})
        d[i["human_verdict"]] += 1
    named = [c for c in clusters if not c["title"].lower().startswith("one-off")]
    ins = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "calls": len(items), "issues": len(issues),
           "fatal_calls": sum(1 for r in items if r["human_overall"] == "Fatal"),
           "nonfatal_calls": sum(1 for r in items if r["human_overall"] == "Non-Fatal"),
           "by_parameter": dict(sorted(by_param.items(), key=lambda kv: -(kv[1]["Fatal"] * 3 + kv[1]["Non-Fatal"]))),
           "clusters": clusters,
           "quality": {"clusters": len(named), "issues_in_named_clusters": sum(c["issues"] for c in named),
                       "coverage_pct": round(100 * sum(c["issues"] for c in named) / max(1, len(issues))),
                       "agent_detected_pct": round(100 * sum(1 for i in issues if i["agent_verdict"] in ("Fatal", "Non-Fatal")) / max(1, len(issues))),
                       "human_grouping_done": False},
           "all_issues": issues}
    json.dump(ins, open(os.path.join(OUT, "insights.json"), "w"), ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "grouping_sheet.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["issue_id", "call", "parameter", "severity", "what_the_buyer_did", "what_the_bot_did_wrong", "your_group_name"])
        for i in issues:
            w.writerow([i["id"], i["offer"], i["parameter"], i["human_verdict"], i["trigger"], i["bot_error"], ""])
    print(f"done in {round(time.time() - t0)}s; {len(named)} named clusters, coverage {ins['quality']['coverage_pct']}%", flush=True)
    for c in clusters:
        print(f"#{c['rank']} {c['title']} | {c['calls']} calls, {c['issues']} issues, {c['fatal']} fatal | {c['layer']}", flush=True)


if __name__ == "__main__":
    main()
