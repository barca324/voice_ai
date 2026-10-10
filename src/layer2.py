"""Layer 2 testing: a simulated-buyer experiment for one suggested prompt fix.

  persona (structured, from the real call)  x  buyer styles  x  {baseline prompt, patched prompt}  x  runs
  -> each simulated conversation is audited with the same judge + rubric rules as real calls
  -> validity gate: the BASELINE must reproduce the original failure, otherwise the result is inconclusive
  -> regression set: passing calls replayed with the patch, to catch breakage
  -> failure counts per arm (not a single before/after pair)

Text-only by default (cheap, exact); audio is rendered on demand with Sarvam TTS (see render_audio).
"""
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
import llm  # noqa: E402
import rubric as R  # noqa: E402
import tester  # noqa: E402

PERSONA_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string"}, "true_quantity_and_unit": {"type": "string"},
        "specs_known": {"type": "array", "items": {"type": "string"}},
        "volunteers": {"type": "array", "items": {"type": "string"}},
        "withholds_until_asked": {"type": "array", "items": {"type": "string"}},
        "language_style": {"type": "string"}, "patience": {"type": "string"},
        "quirks": {"type": "array", "items": {"type": "string"}},
        "location": {"type": "string"},
    },
    "required": ["product", "true_quantity_and_unit", "specs_known", "volunteers", "withholds_until_asked",
                 "language_style", "patience", "quirks", "location"],
    "additionalProperties": False,
}
PERSONA_SYSTEM = """From this real call between IndiaMART's bot VANI and a buyer, write the BUYER's ground truth so a simulator can
play the same person. Use only what the transcript supports; write "not stated" when unknown. volunteers = things the buyer said
without being asked; withholds_until_asked = things they only gave when VANI asked; quirks = notable behaviour (language switch,
mumbling, interrupting, giving ranges, correcting the bot, going silent). Keep each item short."""

STYLES = {
    "faithful": "Behave exactly like the real buyer did: same language mix, same quirks, same level of cooperation. Do not be more helpful than they were.",
    "terse": "Answer in 1-3 words whenever possible. Never volunteer anything. Make the bot work for each detail.",
    "chatty": "Talk more than needed: volunteer related details (use, brand, budget, location) once, sometimes in a rambling way, and occasionally answer a different question than the one asked.",
}

BUYER_SYSTEM = """You play a real BUYER on a phone call with IndiaMART's voice bot VANI. Stay strictly in character.
PERSONA (ground truth; never contradict it, never invent a different need):
{persona}
STYLE FOR THIS CALL: {style}
Rules: reply with ONLY the buyer's words, 1 short spoken sentence, casual Hindi/Hinglish like a real caller on a noisy line (use another
language only if the persona does). Give information only according to the persona and style: if VANI has not asked for something the
persona withholds, do not say it. Never help VANI and never correct its mistakes unless a real person would. If VANI asks the same
thing a third time, get irritated; at four, threaten to hang up. If VANI says it is transferring you to a seller, say a short thanks."""


def extract_persona(report):
    text = tester.history_text([("bot" if str(e["speaker_id"]) == "0" else "buyer", e["transcript"]) for e in report["transcript"]])
    return llm.chat(PERSONA_SYSTEM, text[:7000], schema=PERSONA_SCHEMA, name="persona", max_tokens=900)


def conv_to_entries(conv):
    t, out = 0.0, []
    for who, text in conv:
        d = max(1.5, len(text) / 14)
        out.append({"speaker_id": "0" if who == "bot" else "1", "transcript": text, "start_time_seconds": round(t, 1),
                    "end_time_seconds": round(t + d, 1)})
        t += d + 0.6
    return out


def simulate(report, issue, persona, style, patched, max_turns=8):
    first_bot = next(e["transcript"] for e in report["transcript"] if str(e["speaker_id"]) == "0")
    conv = [("bot", first_bot)]
    bsys = tester.bot_system(report, issue, patched)
    usys = BUYER_SYSTEM.format(persona=json.dumps(persona, ensure_ascii=False), style=STYLES[style])
    for _ in range(max_turns):
        buyer = llm.chat(usys, "Conversation so far:\n" + tester.history_text(conv) + "\n\nWhat does the BUYER say next?",
                         temperature=0.5, max_tokens=100).strip().strip('"')
        conv.append(("buyer", buyer or "हाँ"))
        bot = llm.chat(bsys, "Conversation so far:\n" + tester.history_text(conv) + "\n\nWhat does VANI say next?",
                       temperature=0.3, max_tokens=160).strip().strip('"')
        conv.append(("bot", bot))
        if any(k in bot.lower() for k in tester.END):
            break
    return conv


def failed(rep, parameter):
    """Did the targeted check fail in this audited conversation?"""
    if parameter == "Additional Observation":
        return rep["overall"] != R.PASS
    return any(r["parameter"] == parameter and r["verdict"] in (R.FATAL, R.NONFATAL) for r in rep["rows"])


def one_cell(spec):
    report, issue, persona, style, arm, run, kind, parameter = spec
    conv = simulate(report, issue, persona, style, arm == "patched")
    rep = audit.build_report(conv_to_entries(conv), audit.judge_twice(conv_to_entries(conv)), source="simulated")
    return {"kind": kind, "style": style, "arm": arm, "run": run, "conversation": conv, "overall": rep["overall"],
            "target_failed": failed(rep, parameter),
            "failures": [{"parameter": r["parameter"], "verdict": r["verdict"], "disputed": r.get("disputed", False)}
                         for r in rep["rows"] if r["verdict"] in (R.FATAL, R.NONFATAL)]}


def run_layer2(report, issue, controls, runs=1, progress=lambda done, total, msg: None):
    """controls: list of audited reports whose overall verdict was Pass (regression set)."""
    parameter = issue["parameter"]
    progress(0, 1, "Building the buyer persona from the real call")
    persona = extract_persona(report)
    specs = [(report, issue, persona, s, arm, k, "target", parameter) for s in STYLES for arm in ("baseline", "patched") for k in range(runs)]
    for c in controls[:2]:
        cp = extract_persona(c)
        specs += [(c, issue, cp, "faithful", arm, 0, "control", parameter) for arm in ("baseline", "patched")]
    total, done, lock, cells = len(specs), [0], threading.Lock(), []

    def work(sp):
        try:
            r = one_cell(sp)
        except Exception as e:  # one bad conversation must not sink the experiment
            r = {"kind": sp[6], "style": sp[3], "arm": sp[4], "run": sp[5], "conversation": [], "overall": None,
                 "target_failed": None, "failures": [], "error": str(e)[:200]}
        with lock:
            done[0] += 1
            cells.append(r)
            progress(done[0], total, f"{done[0]}/{total} simulated calls audited")
        return r

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(work, specs))
    order = {"target": 0, "control": 1}
    cells.sort(key=lambda c: (order[c["kind"]], list(STYLES).index(c["style"]), c["arm"] != "baseline", c["run"]))
    return {"persona": persona, "runs_per_cell": runs, "parameter": parameter, "cells": cells, "summary": summarize(cells)}


def rate(cs):
    ok = [c for c in cs if c["target_failed"] is not None]
    return {"failed": sum(1 for c in ok if c["target_failed"]), "n": len(ok),
            "fatal": sum(1 for c in ok if c["overall"] == R.FATAL)}


def summarize(cells):
    t = [c for c in cells if c["kind"] == "target"]
    base, pat = rate([c for c in t if c["arm"] == "baseline"]), rate([c for c in t if c["arm"] == "patched"])
    cb, cp = rate([c for c in cells if c["kind"] == "control" and c["arm"] == "baseline"]), \
        rate([c for c in cells if c["kind"] == "control" and c["arm"] == "patched"])
    errors = sum(1 for c in cells if c.get("error"))
    repro = base["failed"] / base["n"] if base["n"] else 0.0
    if not base["n"] or not pat["n"]:
        verdict, why = "error", "Too many simulated calls failed to run."
    elif repro < 0.3:
        verdict = "inconclusive"
        why = (f"The simulator reproduced the original failure in only {base['failed']} of {base['n']} unpatched calls "
               f"(need at least 30%), so it cannot show whether the patch helps.")
    elif cp["fatal"] > cb["fatal"]:
        verdict, why = "regression", f"On passing control calls the patch produced {cp['fatal']} Fatal vs {cb['fatal']} unpatched."
    elif pat["failed"] < base["failed"] and pat["n"] and pat["failed"] / pat["n"] < repro:
        verdict, why = "improved", f"Targeted failure in {pat['failed']}/{pat['n']} patched calls vs {base['failed']}/{base['n']} unpatched."
    elif pat["failed"] > base["failed"]:
        verdict, why = "worse", f"Targeted failure in {pat['failed']}/{pat['n']} patched calls vs {base['failed']}/{base['n']} unpatched."
    else:
        verdict, why = "no change", f"Targeted failure in {pat['failed']}/{pat['n']} patched vs {base['failed']}/{base['n']} unpatched."
    return {"verdict": verdict, "why": why, "baseline": base, "patched": pat, "control_baseline": cb, "control_patched": cp,
            "reproduction_rate": round(repro, 2), "errors": errors,
            "has_controls": bool(cb["n"] or cp["n"])}


def render_audio(conv, path):
    """Speak one simulated conversation (Sarvam TTS, two voices, 8 kHz)."""
    return tester.synthesize([tuple(x) for x in conv], path)
