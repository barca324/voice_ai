"""Upper-bound test: if the audit is GIVEN the bot's saved values, does it flag the mismatch?

Run:  python3 src/saved_values.py
For the lead-data-only Fatal calls whose error is not visible in the conversation, the saved value is taken from the human
auditors' own remarks (one extraction per call that copies only WHAT WAS SAVED, never the verdict), handed to the audit as
`recorded`, and the audit's verdicts on the lead-data items are compared with the humans'.
Label everywhere: "upper bound, uses human remarks, not real logs".
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
import batch  # noqa: E402
import llm  # noqa: E402
import rubric as R  # noqa: E402

OUT = os.path.join(batch.ROOT, "out", "saved_values")
os.makedirs(OUT, exist_ok=True)
CALLS = ["148187263152", "149775501595", "150467291263", "151906312167", "146585804767", "146339007375", "151583069359", "150792907159"]
LEAD = ["Outcome Accuracy", "Quantity Accuracy", "Quantity Unit Accuracy", "Quantity Probing Error", "Qty Missed Opportunity", "Spec1 Accuracy",
        "Spec2 Accuracy", "Spec Missed Opportunity", "Additional Details", "Product Change Accuracy", "Category ISQ Error", "MCAT Search Error"]

SCHEMA = {"type": "object", "properties": {"saved": {"type": "array", "items": {"type": "object", "properties": {
    "field": {"type": "string"}, "value": {"type": "string"}}, "required": ["field", "value"], "additionalProperties": False}}},
    "required": ["saved"], "additionalProperties": False}
SYSTEM = """You read a human auditor's remarks about one call and copy out ONLY what the bot SAVED in the lead (its recorded values).
Return a list of {field, value}: field is one of outcome, quantity, unit, spec1, spec2, additional_details, category, product.
Only include a field when a remark states its saved value (for example "bot captured 55 Kg", "updated as pack", "mapped in Tipper
Trucks"), and then value is exactly that saved value. Use "nothing saved" only when a remark says the bot failed to capture that
field. A remark such as "Not defined" or a seller/call id says nothing about saved values: skip it. Never invent a field, and ignore
ids, WER numbers and seller details. If no remark states any saved value, return an empty list.
Do NOT include what the buyer said, opinions or verdicts."""


# Saved values as stated in the human auditors' remarks (read by hand from each remark; the automatic extraction misread some).
FROM_REMARKS = {
    "148187263152": {"quantity": "60 sq ft"},
    "149775501595": {"quantity": "55 Kg"},
    "150467291263": {"spec1": "Dubai Kona"},
    "151906312167": {"spec: Number of Floors": "not captured"},
    "146585804767": {"product/category": "Tipper Trucks", "spec: Number of Wheels": "10 wheeler"},
    "146339007375": {"outcome": "approved", "product/category": "Handmade Gift"},
    "151583069359": {"spec2": "hospital use"},
    "150792907159": {"spec1": "1/2 inch"},
}


def remarks(h):
    return "\n".join(f"{k}: {v.strip()}" for k, v in h.items() if k.endswith("Remarks") and v.strip() and v.strip().upper() != "NA")


def main():
    H = {x["Offer ID"]: x for x in batch.human_rows()}
    res = []
    for o in CALLS:
        h = H[o]
        recorded = FROM_REMARKS[o]
        if not recorded:
            res.append({"offer": o, "recorded": {}, "testable": False, "why": "the humans' remarks do not state a saved value"})
            print(o, "NOT TESTABLE: no saved value in the remarks", flush=True)
            continue
        entries = audit.transcribe(os.path.join(batch.REC, o + ".mp3"))
        rep = audit.build_report(entries, audit.judge_twice(entries, recorded), o, "saved-values-test", recorded)
        rows = {r["parameter"]: r for r in rep["rows"]}
        hum = {p: h.get(R.HUMAN_COLUMNS[p], "").strip() for p in LEAD}
        hum = {p: v for p, v in hum.items() if v in ("Fatal", "Non-Fatal")}
        hit = {p: rows[p]["verdict"] for p in hum if rows[p]["verdict"] in ("Fatal", "Non-Fatal")}
        res.append({"offer": o, "recorded": recorded, "testable": True, "human_items": hum, "audit_items": {p: rows[p]["verdict"] for p in hum},
                    "evidence": {p: rows[p]["evidence"][:160] for p in hit}, "flagged_item": bool(hit), "agent_overall": rep["overall"],
                    "human_overall": h["Overall Quality"]})
        print(o, "recorded:", recorded, "\n  human:", hum, "\n  audit:", {p: rows[p]["verdict"] for p in hum}, "overall", rep["overall"], flush=True)
    t = [r for r in res if r["testable"]]
    s = {"calls": len(res), "testable": len(t), "flagged_item": sum(r["flagged_item"] for r in t),
         "overall_fatal": sum(r["agent_overall"] == "Fatal" for r in t),
         "label": "Upper bound: saved values were taken from the human auditors' remarks, not from real logs.", "results": res}
    json.dump(s, open(os.path.join(OUT, "results.json"), "w"), ensure_ascii=False, indent=1)
    print(f"\nflagged a lead-data item the humans marked: {s['flagged_item']}/{s['testable']} testable of {len(res)}; overall Fatal: {s['overall_fatal']}/{s['testable']}")


if __name__ == "__main__":
    main()
