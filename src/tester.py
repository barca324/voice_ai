"""Test agent: turn a suggested prompt fix into a simulated call, speak it, and audit the audio.

For one issue it runs the SAME buyer persona against (A) the current Vani prompt and (B) the prompt plus the proposed patch,
synthesises both conversations to audio (Sarvam TTS), pushes the audio through the normal audit (STT + judge + rules) and
reports whether the targeted failure went away.

Limits (be honest in the UI): the simulated bot sees the prompt's core + relevant excerpts, not all 160k characters; one run
per variant is a smoke test, not a failure-rate measurement.
"""
import io
import os
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
import fixer  # noqa: E402
import llm  # noqa: E402
import rubric as R  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_VOICE, BUYER_VOICE = "priya", "rahul"
END = ("कनेक्ट", "ट्रांसफर", "connect", "transfer", "धन्यवाद", "thank you", "नमस्ते")

BOT_RULES = """You are VANI, IndiaMART's voice bot, in a live phone call with a buyer. Follow the prompt below. Reply with ONLY the
next thing VANI says: 1-2 short spoken sentences, in the buyer's language (Hindi/Hinglish unless they use English), no lists,
no stage directions, no quotes. Ask at most one question per turn. When you have what you need (or the buyer asks to end),
say you are transferring them to a seller and end politely."""

BUYER_RULES = """You are playing a BUYER on a phone call with IndiaMART's voice bot VANI. Stay strictly in character:
{persona}
REAL BUYER'S ACTUAL WORDS in the original call (same person: reuse their facts, phrasing, language mix and behaviour,
including any Telugu/English switches or unclear answers; do not make them more cooperative than they really were):
{real}
Rules: answer only what VANI asks, in 1 short spoken sentence in casual Hindi/Hinglish (the way a real caller talks on a noisy
line). Share details you know ONLY when asked or when it is natural to volunteer them once. Never help the bot, never correct a
mistake unless a real buyer would. If asked the same thing again, get slightly more irritated. Reply with ONLY the buyer's words."""


def bot_system(report, issue, patched):
    sys_p = BOT_RULES + "\n\nVANI PROMPT (core):\n" + fixer.core_prompt() + "\n\nRELEVANT RULES:\n" + \
        fixer.excerpts([issue["parameter"]], issue["root_cause"], max_chars=3500)
    if patched:
        sys_p += "\n\nIMPORTANT NEW RULE (takes priority):\n" + issue["proposed_patch"]
    return sys_p


def history_text(conv):
    return "\n".join(f"{'VANI' if w == 'bot' else 'BUYER'}: {t}" for w, t in conv)


def simulate(report, issue, patched, max_turns=7):
    first_bot = next(e["transcript"] for e in report["transcript"] if str(e["speaker_id"]) == "0")
    conv = [("bot", first_bot)]
    bsys = bot_system(report, issue, patched)
    real = "\n".join("- " + e["transcript"] for e in report["transcript"] if str(e["speaker_id"]) != "0")[:1800]
    usys = BUYER_RULES.format(persona=issue["buyer_persona"], real=real)
    for _ in range(max_turns):
        buyer = llm.chat(usys, "Conversation so far:\n" + history_text(conv) + "\n\nWhat does the BUYER say next?",
                         temperature=0.4, max_tokens=120).strip().strip('"')
        conv.append(("buyer", buyer or "हाँ"))
        bot = llm.chat(bsys, "Conversation so far:\n" + history_text(conv) + "\n\nWhat does VANI say next?",
                       temperature=0.2, max_tokens=160).strip().strip('"')
        conv.append(("bot", bot))
        if any(k in bot.lower() for k in END):
            break
    return conv


def synthesize(conv, out_path):
    """Speak the conversation: two voices, 8 kHz (telephone-like), short pauses between turns."""
    frames, params = [], None
    for who, text in conv:
        data = llm.tts(text, BOT_VOICE if who == "bot" else BUYER_VOICE)
        with wave.open(io.BytesIO(data)) as w:
            if params is None:
                params = w.getparams()
            frames.append(w.readframes(w.getnframes()))
    pause = b"\x00" * int(params.framerate * 0.5) * params.sampwidth * params.nchannels
    with wave.open(out_path, "wb") as out:
        out.setparams(params)
        out.writeframes(pause.join(frames))
    return out_path


def run_variant(report, issue, patched, out_dir, tag, progress):
    progress(f"{tag}: simulating conversation")
    conv = simulate(report, issue, patched)
    progress(f"{tag}: generating audio ({len(conv)} turns)")
    path = synthesize(conv, os.path.join(out_dir, f"{tag}.wav"))
    progress(f"{tag}: auditing generated audio")
    rep = audit.audit_audio(path, f"{tag}.wav", source="simulated")
    return {"tag": tag, "patched": patched, "conversation": conv, "audio": os.path.basename(path), "report": rep}


def target_verdict(rep, parameter):
    for r in rep["rows"]:
        if r["parameter"] == parameter:
            return r["verdict"]
    return R.NA


def run_test(report, issue, out_dir, progress=lambda m: None):
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    with ThreadPoolExecutor(2) as ex:
        fa = ex.submit(run_variant, report, issue, False, out_dir, "baseline", progress)
        fb = ex.submit(run_variant, report, issue, True, out_dir, "patched", progress)
        a, b = fa.result(), fb.result()
    p = issue["parameter"]
    fails = lambda rep: [r["parameter"] for r in rep["rows"] if r["verdict"] in (R.FATAL, R.NONFATAL)]
    verdict = ("n/a: this issue has no prompt patch" if issue["proposed_patch"].startswith("NO PROMPT PATCH") else None)
    tv = (target_verdict(report, p), target_verdict(a["report"], p), target_verdict(b["report"], p))
    if verdict is None:
        sev = {R.PASS: 0, R.NA: 0, R.NONFATAL: 1, R.FATAL: 2}
        new_fails = [x for x in fails(b["report"]) if x not in fails(a["report"])]
        if sev[tv[2]] < sev[tv[1]] and not new_fails:
            verdict = "improved"
        elif sev[tv[2]] < sev[tv[1]]:
            verdict = "improved but introduced new failures"
        elif sev[tv[2]] == sev[tv[1]] == 0:
            verdict = "no failure reproduced in either run (baseline did not trigger the issue); inconclusive"
        elif sev[tv[2]] == sev[tv[1]]:
            verdict = "no change"
        else:
            verdict = "worse"
    return {"issue": issue, "variants": {"baseline": a, "patched": b},
            "comparison": {"parameter": p, "original_call": tv[0], "baseline_sim": tv[1], "patched_sim": tv[2],
                           "overall": {"original_call": report["overall"], "baseline_sim": a["report"]["overall"],
                                       "patched_sim": b["report"]["overall"]},
                           "failures_baseline": fails(a["report"]), "failures_patched": fails(b["report"]),
                           "verdict": verdict, "runs_per_variant": 1, "seconds": round(time.time() - t0)}}


if __name__ == "__main__":
    import json
    rep = json.load(open(os.path.join(ROOT, "out", "test_report.json")))
    issues = json.load(open(os.path.join(ROOT, "out", "test_issues.json")))
    i = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    res = run_test(rep, issues[i], os.path.join(ROOT, "out", "cli_test"), lambda m: print(m))
    json.dump(res, open(os.path.join(ROOT, "out", "cli_test", "result.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res["comparison"], ensure_ascii=False, indent=1))
    for v in res["variants"].values():
        print("\n==", v["tag"])
        for w, t in v["conversation"]:
            print(f"  {w:5}: {t}")
