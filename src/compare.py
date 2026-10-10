"""Step 3: agent verdicts vs human audit labels (the Audit Source of truth CSV).

Run:  python3 src/compare.py     -> prints a per-parameter table, writes out/agreement.json
"""
import csv
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# judge parameter -> human CSV column (whitespace-normalised)
COLS = {
    "Outcome Accuracy": "Outcome Accuracy (Intent)",
    "Quantity Accuracy": "Quantity Accuracy (Numeric)",
    "Quantity Unit Accuracy": "Quantity Unit Accuracy",
    "Quantity Probing Error": "Quantity Probing Error",
    "Quantity Missed Opportunity": "Quantity Missed Opportunity (Capture)",
    "Spec1 Accuracy": "Spec1 Accuracy",
    "Spec2 Accuracy": "Spec2 Accuracy",
    "Spec Missed Opportunity": "Spec Missed Opportunity",
    "Additional Details Accuracy": "Additional Details Accuracy",
    "Additional Details Missed Opportunity": "Additional Details Missed Opportunity",
    "Looping Behavior": "Looping Behavior",
    "Product Change Accuracy": "Product Change/ No Product Accuracy",
    "Category ISQ Error": "Category ISQ's Error",
}


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def human_rows():
    r = list(csv.reader(open(os.path.join(ROOT, "data/raw/Audit Source of truth.csv"), encoding="utf-8")))
    h = [norm(x) for x in r[0]]
    return {x[h.index("Offer ID")]: dict(zip(h, x)) for x in r[1:] if any(x)}


def main():
    verdicts = json.load(open(os.path.join(ROOT, "out/verdicts.json")))
    humans = human_rows()
    report, agree, total = {}, 0, 0
    for offer, v in verdicts.items():
        h = humans[offer]
        rows = []
        for item in v["verdicts"]:
            col = COLS[item["parameter"]]
            hv = h.get(col, "") or "(blank)"
            rows.append({"parameter": item["parameter"], "human": hv, "agent": item["verdict"],
                         "match": hv == item["verdict"], "confidence": item["confidence"],
                         "evidence": item["evidence"], "reason": item["reason"]})
        rows.append({"parameter": "Dead air", "human": h.get("Dead air", "") or "(blank)",
                     "agent": v["dead_air"]["verdict"],
                     "match": (h.get("Dead air", "") or "(blank)") == v["dead_air"]["verdict"],
                     "confidence": "high", "evidence": str(v["dead_air"]["gaps"]), "reason": "computed from timestamps"})
        # NA vs blank on the human side both mean 'not applicable'
        for r in rows:
            if r["human"] in ("(blank)",) and r["agent"] == "NA":
                r["match"] = True
        report[offer] = {"human_call_quality": h["Call Quality"], "human_overall": h["Overall Quality"],
                         "agent_overall": v["overall"], "rows": rows}
        n = sum(1 for r in rows if r["match"])
        agree, total = agree + n, total + len(rows)
        print(f"\n=== {offer}  human call/overall: {h['Call Quality']}/{h['Overall Quality']}   agent overall: {v['overall']}")
        print(f"{'parameter':40} {'human':10} {'agent':10} match  conf")
        for r in rows:
            print(f"{r['parameter']:40} {r['human']:10} {r['agent']:10} {'  ok' if r['match'] else '  XX'}   {r['confidence']}")
        print(f"parameter agreement: {n}/{len(rows)}")
    print(f"\nTOTAL parameter agreement: {agree}/{total}")
    json.dump(report, open(os.path.join(ROOT, "out/agreement.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
