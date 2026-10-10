"""Suggest prompt fixes for the failures in an audit report, grounded in the actual Vani prompt text."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm  # noqa: E402
import rubric as R  # noqa: E402
from audit import fmt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_FILE = os.path.join(ROOT, "data", "vani_prompt.txt")

KEYWORDS = {
    "Outcome Accuracy": "intent buying interested requirement confirm",
    "Quantity Accuracy": "quantity pieces units number capture",
    "Quantity Unit Accuracy": "unit kilogram kg piece quantity",
    "Quantity Probing Error": "quantity ask probe already told repeat",
    "Qty Missed Opportunity": "quantity capture shared",
    "Spec1 Accuracy": "specification spec capture confirm",
    "Spec2 Accuracy": "specification spec capture confirm",
    "Spec Missed Opportunity": "specification spec capacity size volunteered additional details capture BFD",
    "Looping Behavior": "repeat probe again twice irritated stop asking",
    "WER": "misheard ambiguity confirm unclear",
    "Additional Details": "additional details extra buyer requirement",
    "OnCall AstBuy Error": "live seller connect pitch transfer",
    "Product Change Accuracy": "product change different product confirm",
    "Category ISQ Error": "options question specification category",
    "Dead air": "silence wait pause",
}


def prompt_paragraphs():
    return [l for l in open(PROMPT_FILE, encoding="utf-8").read().split("\n") if l.strip()]


def core_prompt(max_chars=5000):
    """Role + hard rules from the top of the prompt: what the simulated bot always carries."""
    out, n = [], 0
    for p in prompt_paragraphs():
        if n + len(p) > max_chars:
            break
        out.append(p)
        n += len(p)
    return "\n".join(out)


def excerpts(failed_params, extra="", max_chars=6000):
    words = set(re.findall(r"[a-zA-Z]{4,}", " ".join(KEYWORDS.get(p, p) for p in failed_params) + " " + extra))
    words = {w.lower() for w in words}
    scored = []
    for i, p in enumerate(prompt_paragraphs()):
        s = sum(1 for w in words if w in p.lower())
        if s >= 2:
            scored.append((s, i, p))
    scored.sort(key=lambda t: (-t[0], t[1]))
    picked, n = [], 0
    for s, i, p in scored:
        if n + len(p) > max_chars:
            continue
        picked.append((i, p))
        n += len(p)
    return "\n".join(f"[para {i}] {p}" for i, p in sorted(picked))


def schema_for(params):
    import copy
    sc = copy.deepcopy(SCHEMA)
    sc["properties"]["issues"]["items"]["properties"]["parameter"] = {"type": "string", "enum": params}
    return sc


SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {"type": "object", "properties": {
        "parameter": {"type": "string"},
        "root_cause": {"type": "string"},
        "layer": {"type": "string", "enum": ["prompt", "flow_logic", "asr_stt", "taxonomy_or_mcat", "unclear"]},
        "proposed_patch": {"type": "string"},
        "patch_location": {"type": "string"},
        "buyer_persona": {"type": "string"},
        "expected_behaviour": {"type": "string"}},
        "required": ["parameter", "root_cause", "layer", "proposed_patch", "patch_location", "buyer_persona", "expected_behaviour"],
        "additionalProperties": False}}},
    "required": ["issues"], "additionalProperties": False}

SYSTEM = """You improve VANI, IndiaMART's AI voice bot, by fixing its prompt. You get an audited call: the failed checks with evidence,
the transcript, and the most relevant excerpts of the current Vani prompt. For each distinct root cause (merge failures that share
one cause) output one issue:
- root_cause: why the bot failed, in 1-2 sentences, referencing the prompt behaviour (or its absence).
- layer: prompt / flow_logic / asr_stt / taxonomy_or_mcat / unclear. Only layer=prompt issues get a patch; for the others
  write proposed_patch as "NO PROMPT PATCH: <what team should look at>".
- proposed_patch: ready-to-paste instruction text for the Vani prompt (imperative, short, specific, no more than 5 lines).
- patch_location: where in the prompt it belongs (cite a [para N] from the excerpts if one applies).
- buyer_persona: a 2-3 sentence description of a simulated buyer that would trigger the same failure again (product, quantity,
  specs they know, language, how they talk, what they volunteer unprompted), so the patch can be tested.
- expected_behaviour: what a correct bot does in that situation, observable from the transcript.
Rules: `parameter` must be one of the failed checks you are given (or "Additional Observation" for the audit's free-text
observation). Create issues ONLY for the failed checks and the observation provided - never invent a new failure, and never
mention a parameter that was marked Pass or NA. If a failed check has no prompt-level cause, use layer asr_stt / flow_logic.
Be concrete; do not invent facts absent from the call."""


def suggest(report):
    failed = [r for r in report["rows"] if r["verdict"] in (R.FATAL, R.NONFATAL)]
    if not failed:
        return []
    names = [r["parameter"] for r in failed]
    detail = "\n".join(f"- {r['parameter']}: {r['verdict']}{' [judge runs disagreed - low certainty]' if r.get('disputed') else ''} ({r['kind']}). Evidence: {r['evidence']}. Why: {r['reason']}" for r in failed)
    obs = report.get("observation", "")
    user = (f"FAILED CHECKS:\n{detail}\n\nAUDIT OBSERVATION (free text): {obs}\n\nTRANSCRIPT (BOT = VANI):\n{fmt(report['transcript'])}\n\n"
            f"RELEVANT CURRENT PROMPT EXCERPTS:\n{excerpts(names, detail)}")
    out = llm.chat(SYSTEM, user, schema=schema_for(names + ["Additional Observation"]), name="fixes", max_tokens=3500)
    return out["issues"]
