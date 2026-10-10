"""Audio -> audit report.   transcribe (Sarvam batch STT, diarized) -> judge facts (LLM) -> rubric rules (code) -> report."""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".pylibs"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm  # noqa: E402
import rubric as R  # noqa: E402

STT_CACHE = os.path.join(ROOT, "cache", "stt_by_hash")
os.makedirs(STT_CACHE, exist_ok=True)

HANDOVER = re.compile(r"connect|transfer|hold|line par|कनेक्ट|ट्रांसफर|होल्ड|लाइन पर|जोड़|मिला", re.I)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def seed_cache_from_pilot():
    """Re-use transcripts already paid for in the pilot run."""
    rec, old = os.path.join(ROOT, "data", "recordings"), os.path.join(ROOT, "cache", "stt")
    if not os.path.isdir(rec) or not os.path.isdir(old):
        return
    for f in os.listdir(rec):
        src = os.path.join(old, f.rsplit(".", 1)[0] + ".json")
        dst = os.path.join(STT_CACHE, sha(os.path.join(rec, f)) + ".json")
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy(src, dst)


def transcribe(path):
    """Diarized transcript entries [{speaker_id, transcript, start_time_seconds, end_time_seconds}]."""
    cache = os.path.join(STT_CACHE, sha(path) + ".json")
    if os.path.exists(cache):
        return json.load(open(cache))["diarized_transcript"]["entries"]
    from sarvamai import SarvamAI
    client = SarvamAI(api_subscription_key=llm.key())
    job = client.speech_to_text_job.create_job(model="saaras:v4", mode="transcribe", language_code="unknown",
                                               with_diarization=True, num_speakers=2)
    job.upload_files(file_paths=[path])
    job.start()
    job.wait_until_complete()
    res = job.get_file_results()
    if not res["successful"]:
        raise RuntimeError("STT failed: " + str([f["error_message"] for f in res["failed"]]))
    out = tempfile.mkdtemp()
    job.download_outputs(output_dir=out)
    data = json.load(open(os.path.join(out, os.listdir(out)[0])))
    shutil.rmtree(out, ignore_errors=True)
    json.dump(data, open(cache, "w"), ensure_ascii=False)
    return data["diarized_transcript"]["entries"]


def dead_air(entries, noise=False):
    """Silence longer than the rubric threshold. Gaps that follow the bot's transfer-to-seller line are flagged, not counted."""
    limit = R.DEAD_AIR_NOISE_S if noise else R.DEAD_AIR_NORMAL_S
    es = sorted(entries, key=lambda e: e["start_time_seconds"])
    gaps, end = [], 0.0
    for e in es:
        if e["start_time_seconds"] - end > limit:
            handover = any(str(x["speaker_id"]) == "0" and HANDOVER.search(x["transcript"]) and
                           end - 25 <= x["end_time_seconds"] <= end + 0.5 for x in es)
            gaps.append({"from": round(end, 1), "to": round(e["start_time_seconds"], 1),
                         "seconds": round(e["start_time_seconds"] - end, 1), "after_handover": handover})
        end = max(end, e["end_time_seconds"])
    return gaps, limit


def fmt(entries):
    return "\n".join(f"[{e['start_time_seconds']:.1f}s] {'BOT' if str(e['speaker_id']) == '0' else 'BUYER'}: {e['transcript']}"
                     for e in sorted(entries, key=lambda x: x["start_time_seconds"]))


ALL_KINDS = sorted({k for ks in R.KINDS.values() for k in ks} | {"none"})
SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "object", "properties": {
            "buyer_product": {"type": "string"}, "buyer_quantity_and_unit": {"type": "string"},
            "bot_quantity_captured": {"type": "string"}, "buyer_specs_stated": {"type": "string"},
            "bot_specs_captured": {"type": "string"}, "language_switch_or_noise": {"type": "string"}},
            "required": ["buyer_product", "buyer_quantity_and_unit", "bot_quantity_captured", "buyer_specs_stated",
                         "bot_specs_captured", "language_switch_or_noise"], "additionalProperties": False},
        "noise_abruption": {"type": "boolean"},
        "checks": {"type": "array", "items": {"type": "object", "properties": {
            "parameter": {"type": "string", "enum": R.PARAMS},
            "applicable": {"type": "boolean"}, "error": {"type": "boolean"},
            "kind": {"type": "string", "enum": ALL_KINDS},
            "evidence": {"type": "string"}, "reason": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]}},
            "required": ["parameter", "applicable", "error", "kind", "evidence", "reason", "confidence"],
            "additionalProperties": False}},
        "additional_observation": {"type": "string"},
    },
    "required": ["facts", "noise_abruption", "checks", "additional_observation"],
    "additionalProperties": False,
}

SYSTEM = f"""You audit calls of VANI, IndiaMART's AI voice bot (speaker BOT). A buyer (BUYER) called a seller who was unavailable, so
the call reached VANI, which captures: buying intent, quantity + unit, two specs (spec1, spec2), and additional details (BFD),
then connects the buyer to a live seller if one is available. The audio is noisy 8kHz phone speech; do not blame the bot for
obvious transcription noise.

Report FACTS ONLY for each parameter - do not decide Fatal or Non-Fatal, code does that. For each parameter output:
- applicable: false if the buyer never raised it / it cannot be judged from the transcript (then error=false, kind=none).
- error: true only if the transcript shows the bot got it wrong, missed it, or probed wrongly.
- kind: when error=true, choose one of that parameter's error kinds; otherwise "none".
- evidence: a short quote (under 20 words) with its [time]. reason: under 25 words. confidence: high/medium/low.

Parameters, definitions and allowed error kinds:
{R.rubric_prompt()}

Rules: looping means the same parameter probed MORE than 2 times. Do not mark an error unless you can quote it. If a
parameter is not visible in text (MCAT search, seller pitch status), set applicable=false. noise_abruption=true only if the
transcript shows the line was disrupted by noise. additional_observation: anything else notable (language mismatch, tone,
pronunciation problems you can infer). Evidence rules (important). You have the call transcript, and the bot's saved values ONLY if a section says so. Never write that the
bot "recorded", "captured" or "saved" a value unless it is given in that section. The options the bot offers (for example "5000 litres,
3000 litres") are NOT captured values. What the bot understood can only be shown by its own read-back or confirmation in the call:
quote that. If a parameter could only be judged from the bot's saved values and none were supplied, set applicable=false. In the facts,
write "not visible" for bot_quantity_captured and bot_specs_captured when only the saved values would show them.
Output compact JSON, no extra whitespace. Cover every parameter exactly once."""


RECORDED_NOTE = """

The values the bot RECORDED for this call (from its logs) are given after the transcript. Compare them with what the buyer actually
said. If a recorded value differs from what the buyer said, or the buyer never gave it, that is an error for the matching parameter
(quantity, unit, spec1, spec2, additional details, outcome): quote the buyer's words and the recorded value as the evidence. A
recorded value that matches what was said is correct even if the bot did not repeat it aloud."""


def judge(entries, temperature=0.0, recorded=None):
    user = "Audit this call (BOT = VANI):\n\n" + fmt(entries)
    system = SYSTEM
    if recorded:
        user += "\n\nVALUES THE BOT RECORDED FOR THIS CALL:\n" + json.dumps(recorded, ensure_ascii=False)[:1500]
        system += RECORDED_NOTE
    return llm.chat(system, user, schema=SCHEMA, name="audit", max_tokens=5000, temperature=temperature)


SEVERITY = {R.PASS: 0, R.NA: 0, R.NONFATAL: 1, R.FATAL: 2}


def judge_valid(entries, temperature, recorded=None):
    """One judge run that must cover (nearly) every parameter: an empty or partial answer is retried up to twice."""
    best = None
    for t in (temperature, temperature + 0.2, temperature + 0.4):
        r = judge(entries, t, recorded)
        if best is None or len(r["checks"]) > len(best["checks"]):
            best = r
        if len(r["checks"]) >= len(R.PARAMS) - 2:
            return r
    return best


JUDGE_RUNS = int(os.environ.get("JUDGE_RUNS", "1"))  # 1 = one judge run per call (cheaper); 2 = two runs, disagreements flagged "disputed"


def judge_twice(entries, recorded=None):
    """Judge runs for one call. The name is historical: the number of runs follows JUDGE_RUNS (default 1)."""
    runs = [judge_valid(entries, 0.0, recorded)]
    if JUDGE_RUNS >= 2:
        runs.append(judge_valid(entries, 0.3, recorded))
    return runs


def _row(p, c):
    c = c or {"applicable": False, "error": False, "kind": "none", "evidence": "", "reason": "not reported by judge", "confidence": "low"}
    verdict, rule = R.decide(p, c["applicable"], c["error"], c["kind"])
    return {"parameter": p, "verdict": verdict, "rule": rule, "kind": c["kind"], "evidence": c["evidence"],
            "reason": c["reason"], "confidence": c["confidence"], "disputed": False}


def build_report(entries, raws, filename="", source="upload", recorded=False):
    if isinstance(raws, dict):
        raws = [raws]
    raw = raws[0]
    rows = []
    for p in R.PARAMS:
        runs = [_row(p, {c["parameter"]: c for c in r["checks"]}.get(p)) for r in raws]
        row = max(runs, key=lambda x: SEVERITY[x["verdict"]])  # keep the more severe reading so Fatal is not missed
        if len({SEVERITY[x["verdict"]] for x in runs}) > 1:
            row = dict(row, disputed=True, confidence="low",
                       reason=row["reason"] + " | judge runs disagreed: " + " vs ".join(x["verdict"] for x in runs))
        rows.append(row)
    raw = dict(raw, noise_abruption=any(r["noise_abruption"] for r in raws))
    gaps, limit = dead_air(entries, raw["noise_abruption"])
    counted = [g for g in gaps if not g["after_handover"]]
    da = R.FATAL if counted else R.PASS
    rows.append({"parameter": "Dead air", "verdict": da,
                 "rule": f"Dead air more than {limit:.0f}s is Fatal" + (" (8s because of noise abruption)" if raw["noise_abruption"] else ""),
                 "kind": "gap" if counted else "none",
                 "evidence": "; ".join(f"{g['seconds']}s at {g['from']}-{g['to']}s" for g in counted) or "no silence over the limit",
                 "reason": "measured from diarized timestamps", "confidence": "high", "disputed": False})
    overall = R.overall([r["verdict"] for r in rows])
    reported = {c["parameter"] for r_ in raws for c in r_["checks"]}
    missing = [p_ for p_ in R.PARAMS if p_ not in reported]
    incomplete = len(missing) >= 6
    if incomplete:  # never report a confident Pass when the judge left most parameters unanswered
        overall = R.NA

    risk = []
    if incomplete:
        risk.append(f"Audit incomplete: the judge answered only {len(R.PARAMS) - len(missing)} of {len(R.PARAMS)} checks. Run the audit again.")
    if overall == R.FATAL:
        risk.append("Fatal verdict: human review before any prompt change")
    for r in rows:
        if r["verdict"] not in (R.NA, R.PASS) and r["confidence"] == "low":
            risk.append(f"Low confidence on {r['parameter']}")
        if r.get("disputed"):
            risk.append(f"Judge runs disagreed on {r['parameter']}")
        if "unrecognised kind" in r["rule"]:
            risk.append(f"{r['parameter']}: judge gave no valid error kind")
    if any(g["after_handover"] for g in gaps):
        risk.append("Long silence after handover to seller was not counted as dead air; confirm by listening")
    if raw["facts"]["language_switch_or_noise"].strip().lower() not in ("", "none", "no", "n/a"):
        risk.append("Language switch or noise noted: " + raw["facts"]["language_switch_or_noise"][:100])
    return {"filename": filename, "source": source, "overall": overall, "incomplete": incomplete, "recorded_values_used": bool(recorded),
            "risk": {"flag": bool(risk), "reasons": risk}, "rows": rows, "facts": raw["facts"],
            "observation": raw["additional_observation"], "dead_air_gaps": gaps,
            "transcript": sorted(entries, key=lambda e: e["start_time_seconds"]),
            "not_assessable_from_audio_text": ["WER (needs reference words), pronunciation, MCAT search, seller pitch status are best-effort from text"]}


def audit_audio(path, filename="", progress=lambda s, m: None, source="upload", recorded=None):
    progress("transcribe", "Transcribing and separating bot/buyer speech")
    entries = transcribe(path)
    progress("judge", "Auditing against the rubric")
    raws = judge_twice(entries, recorded)
    return build_report(entries, raws, filename, source, recorded)


seed_cache_from_pilot()

if __name__ == "__main__":
    rep = audit_audio(sys.argv[1], os.path.basename(sys.argv[1]), lambda s, m: print(s, "-", m))
    print(json.dumps({k: rep[k] for k in ("overall", "risk")}, ensure_ascii=False, indent=1))
    for r in rep["rows"]:
        print(f"{r['parameter']:26} {r['verdict']:10} {r['kind']:28} {r['confidence']:6} {r['evidence'][:70]}")
