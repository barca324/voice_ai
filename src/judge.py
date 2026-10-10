"""Step 2: audit a transcribed call against the Vani rubric.

Run:  python3 src/judge.py            (uses cache/stt, writes cache/judge + out/verdicts.json)

The judge sees ONLY the diarized transcript and the rubric - never the human labels.
Dead air is computed in code from timestamps, not by the LLM.
Parameters that cannot be assessed from text (WER, pronunciation, MCAT search) are reported NA.
"""
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STT = os.path.join(ROOT, "cache", "stt")
JUDGE = os.path.join(ROOT, "cache", "judge")
OUT = os.path.join(ROOT, "out")
MODEL = "sarvam-105b"

DEAD_AIR_SECONDS = 5.0

PARAMS = [
    "Outcome Accuracy", "Quantity Accuracy", "Quantity Unit Accuracy",
    "Quantity Probing Error", "Quantity Missed Opportunity",
    "Spec1 Accuracy", "Spec2 Accuracy", "Spec Missed Opportunity",
    "Additional Details Accuracy", "Additional Details Missed Opportunity",
    "Looping Behavior", "Product Change Accuracy", "Category ISQ Error",
]

RUBRIC = """You are a strict quality auditor for VANI, IndiaMART's AI voice bot that calls buyers who showed interest
in a product (the seller they tried was unavailable). The bot (speaker S0) captures: buyer intent, quantity + unit,
two specifications (spec1, spec2, e.g. capacity/size/occasion), and additional buyer details (BFD), then hands over.
The buyer is speaker S1. Judge the BOT's behaviour from the transcript. Transcripts come from noisy 8kHz phone audio,
so do not blame the bot for obvious transcription noise.

Rubric (verdict per parameter: Pass, Non-Fatal, Fatal, or NA when it does not apply / cannot be judged from text):
- Outcome Accuracy: buyer's buying intent captured correctly. Always Fatal if wrong.
- Quantity Accuracy: quantity value captured correctly. Fatal if the error changes the lead; Non-Fatal if a minor difference (10-20).
- Quantity Unit Accuracy: unit the buyer said captured correctly. Fatal if it changes the requirement; Non-Fatal for similar units.
- Quantity Probing Error: asked for quantity when buyer had already given it, or failed to ask/nudge properly. Fatal if quantity was
  defined and not asked; Non-Fatal if not defined and asked.
- Quantity Missed Opportunity: buyer shared a quantity and the bot did not capture it. Fatal; Non-Fatal if the buyer's voice was unclear.
- Spec1 Accuracy / Spec2 Accuracy: specification the buyer gave captured correctly. Fatal if it affects the lead; Non-Fatal if minor.
- Spec Missed Opportunity: spec 1 or 2 shared by the buyer was not captured / not even added to BFD. Fatal; Non-Fatal only for a missed spec 3.
- Additional Details Accuracy: buyer's extra details (BFD) captured. Fatal if grossly wrong or irrelevant; Non-Fatal if duplicate or very generic.
- Additional Details Missed Opportunity: buyer volunteered a relevant extra detail and the bot did not capture it. Non-Fatal unless it changes the lead.
- Looping Behavior: bot probed the same parameter more than twice. Fatal if the buyer got irritated, the call ended, or an opportunity was missed; else Non-Fatal.
- Product Change Accuracy: buyer asked for a different/no product and the bot handled it wrongly. Fatal if the wrong product was approved.
- Category ISQ Error: bot's questions contradict or confuse the product's specification options. Fatal if ISQs contradict.

Rules: cite the transcript turn that justifies every non-Pass verdict (quote it). If you are unsure, say so with low confidence instead
of guessing Fatal. A call's overall verdict is Fatal if any parameter is Fatal, Non-Fatal if any is Non-Fatal, else Pass.
Keep every "evidence" quote under 20 words and every "reason" under 25 words. Respond with compact JSON only, no extra whitespace."""

SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "object",
            "properties": {
                "buyer_product": {"type": "string"},
                "buyer_quantity_stated": {"type": "string"},
                "bot_quantity_captured": {"type": "string"},
                "buyer_specs_stated": {"type": "string"},
                "bot_specs_captured": {"type": "string"},
            },
            "required": ["buyer_product", "buyer_quantity_stated", "bot_quantity_captured",
                         "buyer_specs_stated", "bot_specs_captured"],
            "additionalProperties": False,
        },
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "parameter": {"type": "string", "enum": PARAMS},
                    "verdict": {"type": "string", "enum": ["Pass", "Non-Fatal", "Fatal", "NA"]},
                    "evidence": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["parameter", "verdict", "evidence", "reason", "confidence"],
                "additionalProperties": False,
            },
        },
        "improvement": {"type": "string"},
    },
    "required": ["facts", "verdicts", "improvement"],
    "additionalProperties": False,
}


def load_key():
    for line in open(os.path.join(ROOT, ".env")):
        if line.startswith("SARVAM_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("SARVAM_API_KEY missing in .env")


def format_transcript(entries):
    lines = []
    for e in sorted(entries, key=lambda x: x["start_time_seconds"]):
        who = "BOT" if str(e["speaker_id"]) == "0" else "BUYER"
        lines.append(f"[{e['start_time_seconds']:.1f}s] {who}: {e['transcript']}")
    return "\n".join(lines)


def dead_air(entries, threshold=DEAD_AIR_SECONDS):
    """Gaps (seconds) where nobody speaks, longer than the threshold."""
    spans = sorted((e["start_time_seconds"], e["end_time_seconds"]) for e in entries)
    gaps, end = [], 0.0
    for s, t in spans:
        if s - end > threshold:
            gaps.append({"from": round(end, 1), "to": round(s, 1), "seconds": round(s - end, 1)})
        end = max(end, t)
    return gaps


def call_llm(transcript_text, temperature=0):
    body = {
        "model": MODEL,
        "temperature": temperature,
        "max_tokens": 4000,
        "reasoning_effort": None,  # reasoning ate the whole token budget; disable it
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": "Audit this call. Transcript (S0=BOT, S1=BUYER):\n\n" + transcript_text},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "audit", "strict": True, "schema": SCHEMA},
        },
    }
    key = load_key()
    req = urllib.request.Request(
        "https://api.sarvam.ai/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "api-subscription-key": key,
                 "Authorization": "Bearer " + key},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def overall(verdicts):
    vs = {v["verdict"] for v in verdicts}
    return "Fatal" if "Fatal" in vs else "Non-Fatal" if "Non-Fatal" in vs else "Pass"


def main():
    os.makedirs(JUDGE, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    results = {}
    for f in sorted(os.listdir(STT)):
        offer = f.split(".")[0]
        entries = json.load(open(os.path.join(STT, f)))["diarized_transcript"]["entries"]
        cache = os.path.join(JUDGE, f"{offer}.json")
        parsed = None
        if os.path.exists(cache):
            raw = json.load(open(cache))
            parsed = json.loads(raw["choices"][0]["message"]["content"])
        else:
            for attempt, temp in enumerate([0, 0.2, 0.4]):
                raw = call_llm(format_transcript(entries), temp)
                try:
                    parsed = json.loads(raw["choices"][0]["message"].get("content") or "")
                    json.dump(raw, open(cache, "w"), ensure_ascii=False, indent=1)
                    break
                except json.JSONDecodeError:
                    print(f"{offer}: invalid JSON (attempt {attempt + 1}, finish={raw['choices'][0].get('finish_reason')}), retrying")
            if parsed is None:
                raise SystemExit(f"{offer}: no valid JSON after 3 attempts")
        gaps = dead_air(entries)
        parsed["dead_air"] = {"gaps": gaps, "verdict": "Fatal" if any(g["seconds"] > 8 for g in gaps) else
                              "Non-Fatal" if gaps else "Pass"}
        parsed["overall"] = overall(parsed["verdicts"] + [{"verdict": parsed["dead_air"]["verdict"]}])
        results[offer] = parsed
        print(offer, "->", parsed["overall"], "| tokens:", raw.get("usage", {}).get("total_tokens"))
    json.dump(results, open(os.path.join(OUT, "verdicts.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
