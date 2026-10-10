"""Fix pipeline: one suggested fix -> patched prompt -> fixed test pack -> graded runs on both prompts -> verdict -> human decision.

State per fix lives in out/fixes/<id>.json. A fix is created lazily from the reviewed grouping (insights_manual.json).
The test pack is a fixed set of buyer scripts, so the CURRENT and PATCHED prompts face exactly the same buyers (a paired comparison).
Runs are conversations pasted in (typed by a person in the agent's test chat, or driven by Claude); each is graded with the same
audit as real calls. The verdict reuses the simulator-validity gate: if the current prompt does not reproduce the failure, the
result is "inconclusive".
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
import layer2  # noqa: E402
import llm  # noqa: E402
import rubric as R  # noqa: E402
import transcript_import  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "out", "fixes")
PROMPT = os.path.join(ROOT, "data", "vani_prompt_for_agent.txt")
INSIGHTS = os.path.join(ROOT, "out", "insights", "insights_manual.json")
os.makedirs(DIR, exist_ok=True)

DEFAULT_PRODUCT = "Plastic Bags"
DEFAULT_OPTIONS = '{"Size": ["8x10 inch", "9x13 inch"], "Material": ["Virgin", "HMHDPE", "LDPE"]}'


def _p(fid, ext=".json"):
    return os.path.join(DIR, re.sub(r"[^a-z0-9_-]", "", fid) + ext)


def catalog():
    if not os.path.exists(INSIGHTS):
        return []
    ins = json.load(open(INSIGHTS))
    out = []
    for c in ins["clusters"]:
        if c.get("fix"):
            out.append({"id": "f-" + c["key"], "title": c["title"], "rank": c["rank"], "calls": c["calls"], "issues": c["issues"],
                        "fatal": c["fatal"], "parameters": c["parameters"], "plain_summary": c["plain_summary"], "layer": c["layer"],
                        "fix": c["fix"], "found_at": ins["generated_at"]})
    return out


def load(fid):
    base = next((c for c in catalog() if c["id"] == fid), None)
    if not base:
        return None
    saved = json.load(open(_p(fid))) if os.path.exists(_p(fid)) else {}
    d = {**base, "patch": None, "pack": None, "runs": [], "decision": None, **saved}
    d.update({k: base[k] for k in ("title", "rank", "calls", "issues", "fatal", "parameters", "fix")})
    return d


def save(d):
    keep = {k: d[k] for k in ("id", "patch", "pack", "runs", "decision")}
    json.dump(keep, open(_p(d["id"]), "w"), ensure_ascii=False, indent=1)


def is_prompt_fix(d):
    return d["fix"]["fix_type"] == "prompt_patch"


def build_patch(fid):
    d = load(fid)
    if not d or not is_prompt_fix(d):
        raise ValueError("This fix is not a prompt change, so there is nothing to patch. Hand it to the team named in the fix.")
    lines = open(PROMPT, encoding="utf-8").read().split("\n")
    m = re.search(r"line (\d+)", d["fix"]["where"])
    anchor = int(m.group(1)) if m else 36
    anchor = min(max(anchor, 0), len(lines) - 1)
    text = d["fix"]["change"].strip()
    new = lines[:anchor + 1] + [text] + lines[anchor + 1:]
    with open(_p(fid, ".patched.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(new))
    d["patch"] = {"built_at": int(time.time()), "anchor_line": anchor, "inserted": text, "chars_before": len("\n".join(lines)),
                  "chars_after": len("\n".join(new)),
                  "before": [l[:160] for l in lines[max(0, anchor - 1):anchor + 1]], "after": [l[:160] for l in lines[anchor + 1:anchor + 3]]}
    save(d)
    return d


def patched_text(fid):
    fp = _p(fid, ".patched.txt")
    return open(fp, encoding="utf-8").read() if os.path.exists(fp) else None


PACK_SCHEMA = {"type": "object", "properties": {"scripts": {"type": "array", "items": {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["target", "control"]}, "title": {"type": "string"}, "persona": {"type": "string"},
    "lines": {"type": "array", "items": {"type": "string"}}, "expect": {"type": "string"}},
    "required": ["kind", "title", "persona", "lines", "expect"], "additionalProperties": False}}},
    "required": ["scripts"], "additionalProperties": False}

PACK_SYSTEM = """You write a fixed test pack for a voice bot: scripts of what a BUYER says, played turn by turn against the bot, so
two versions of the bot's prompt can be compared on exactly the same buyers.
Write 5 scripts of kind "target": buyers who trigger the failure pattern you are given (use the real examples; vary the trigger so
the pack is not five copies of one case). Then write 3 scripts of kind "control": ordinary cooperative buyers who answer each
question directly; a good fix must not break these.
Adapt every real example to the test product so it sounds like a real buyer of THAT product would speak. Do not copy words that
make no sense for it (for example grit sizes for plastic bags); keep the underlying trigger (a unit word not on the list, an answer
outside the options, a number said twice, a value never given, and so on).
Rules for every script:
- lines: 6 to 8 short things the buyer says, in order, in casual Hindi or Hinglish as a caller on a noisy line speaks (Devanagari or
  Latin script both fine). The lines are fixed and do not react to the bot, so write them so they still make sense whatever the bot asks.
  Put the trigger inside the lines (for target scripts) and make sure the early lines answer the bot's opening question about the product.
- persona: one sentence on who the buyer is and what they want, for the product given.
- expect: one sentence on what a correct bot does when it meets this buyer.
- title: under 8 words."""


def make_pack(fid, product, options):
    d = load(fid)
    if not d:
        raise ValueError("unknown fix")
    ex = "\n".join(f"- [{e['parameter']}] buyer: {e['trigger']} | bot: {e['bot_error']}" + (f" (quote: {e['quote']})" if e.get("quote") else "")
                   for e in json.load(open(INSIGHTS))["all_issues"] if e["cluster"] == fid[2:])[:2400]
    user = (f"PATTERN: {d['title']}\n{d['plain_summary']}\n\nREAL EXAMPLES:\n{ex}\n\nTHE FIX UNDER TEST:\n{d['fix']['change']}\n\n"
            f"THE TEST AGENT IS SET UP FOR THIS PRODUCT: {product}\nIts option lists: {options}")
    out = llm.chat(PACK_SYSTEM, user, schema=PACK_SCHEMA, name="pack", max_tokens=4000)
    scripts = []
    for n, s in enumerate(out["scripts"], 1):
        scripts.append({"id": f"s{n}", **s})
    d["pack"] = {"created_at": int(time.time()), "product": product, "options": options, "scripts": scripts}
    d["runs"] = []
    save(d)
    return d


EXPECT_SCHEMA = {"type": "object", "properties": {"met": {"type": "boolean"}, "evidence": {"type": "string"}, "reason": {"type": "string"}},
                 "required": ["met", "evidence", "reason"], "additionalProperties": False}
EXPECT_SYSTEM = """You check ONE thing about a call between a buyer and IndiaMART's voice bot VANI. You get the transcript and a statement of
what a correct bot does for this buyer. Decide whether the bot did it.
met=true only if the transcript shows the bot doing it. If the transcript does not show it either way, or the bot did the opposite,
met=false. evidence: a short verbatim quote (under 20 words) from the bot's lines, or "". reason: one short sentence."""


def expect_judge(text, expect, votes=3):
    """Majority of three independent checks; `disputed` when they were not unanimous."""
    runs = [llm.chat(EXPECT_SYSTEM, f"CORRECT BOT BEHAVIOUR: {expect}\n\nTRANSCRIPT:\n{text[:6000]}", schema=EXPECT_SCHEMA, name="met",
                     max_tokens=400, temperature=t) for t in (0.0, 0.2, 0.4)[:votes]]
    yes = sum(r["met"] for r in runs)
    met = yes * 2 > len(runs)
    pick = next(r for r in runs if r["met"] == met)
    return {"met": met, "disputed": yes not in (0, len(runs)), "evidence": pick["evidence"], "reason": pick["reason"], "votes": f"{yes}/{len(runs)}"}


def add_run(fid, arm, script_id, text, who=""):
    d = load(fid)
    if not d or not d.get("pack"):
        raise ValueError("Create the test pack first.")
    if arm not in ("baseline", "patched") or script_id not in {s["id"] for s in d["pack"]["scripts"]}:
        raise ValueError("Unknown arm or script.")
    entries = transcript_import.parse(text)
    if len(entries) < 2:
        raise ValueError("Could not find two turns. Start each line with Vani: or Buyer:.")
    script = next(x for x in d["pack"]["scripts"] if x["id"] == script_id)
    j = expect_judge(audit.fmt(entries), script["expect"])
    run = {"arm": arm, "script": script_id, "at": int(time.time()), "by": who[:60], "text": text[:6000], "met": j["met"],
           "disputed": j["disputed"], "votes": j["votes"], "evidence": j["evidence"], "reason": j["reason"],
           "overall": "Pass" if j["met"] else "Fatal", "target_failed": not j["met"]}
    d = load(fid)
    d["runs"] = [r for r in d["runs"] if not (r["arm"] == arm and r["script"] == script_id)] + [run]
    save(d)
    return run


def clear_runs(fid, arm=None):
    d = load(fid)
    d["runs"] = [r for r in d["runs"] if arm and r["arm"] != arm]
    save(d)


def decide(fid, status, by, note):
    d = load(fid)
    if status not in ("approved", "rejected"):
        raise ValueError("status must be approved or rejected")
    d["decision"] = {"status": status, "by": (by or "Anonymous")[:60], "note": (note or "")[:400], "at": int(time.time())}
    save(d)


def progress(d):
    """Stage summary used by the list and the detail page."""
    runs = d.get("runs", [])
    scripts = d["pack"]["scripts"] if d.get("pack") else []
    need = len(scripts) * 2
    have = len({(r["arm"], r["script"]) for r in runs})
    prompt_fix = is_prompt_fix(d)
    cells = [{"kind": next((s["kind"] for s in scripts if s["id"] == r["script"]), "target"), "style": r["script"], "arm": r["arm"],
              "overall": r["overall"], "target_failed": r["target_failed"]} for r in runs]
    have_arms = {(c["kind"], c["arm"]) for c in cells}
    if not cells:
        summary = None
    elif ("target", "baseline") not in have_arms or ("target", "patched") not in have_arms:
        summary = {"verdict": "incomplete", "why": "Add target-script runs for both the current and the patched prompt to see a result.",
                   "baseline": layer2.rate([c for c in cells if c["kind"] == "target" and c["arm"] == "baseline"]),
                   "patched": layer2.rate([c for c in cells if c["kind"] == "target" and c["arm"] == "patched"]),
                   "control_baseline": layer2.rate([c for c in cells if c["kind"] == "control" and c["arm"] == "baseline"]),
                   "control_patched": layer2.rate([c for c in cells if c["kind"] == "control" and c["arm"] == "patched"]),
                   "reproduction_rate": 0, "has_controls": any(c["kind"] == "control" for c in cells), "errors": 0}
    else:
        summary = layer2.summarize(cells)
    tested_at = max((r["at"] for r in runs), default=None) if need and have >= need else None
    found = time.mktime(time.strptime(d["found_at"], "%Y-%m-%d %H:%M"))
    return {"prompt_fix": prompt_fix, "patch_done": bool(d.get("patch")), "pack_done": bool(d.get("pack")),
            "runs_have": have, "runs_need": need, "tested": bool(tested_at), "tested_at": tested_at, "summary": summary,
            "decided": bool(d.get("decision")), "found_at": int(found),
            "hours_found_to_tested": round((tested_at - found) / 3600, 1) if tested_at else None}


def public(d):
    out = {k: d[k] for k in ("id", "title", "rank", "calls", "issues", "fatal", "parameters", "plain_summary", "layer", "fix", "found_at",
                             "patch", "pack", "runs", "decision")}
    out["progress"] = progress(d)
    return out
