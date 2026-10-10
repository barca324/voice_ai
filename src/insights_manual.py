"""Root-cause grouping of the 30 failed calls, written by Claude from the human auditors' remarks.

Used because Sarvam credits ran out before src/insights.py (the automatic version of this step) could run.
Same output schema as insights.py, so the dashboard shows either. Re-run src/insights.py when credits return.
Every statement below is taken from an auditor remark (quoted or paraphrased); nothing is inferred beyond them.
"""
import csv, json, os, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out", "insights")

# (call, parameter, human verdict, what the buyer did, what the bot did wrong, short quote, cluster key)
I = [
 ("149775501595","Quantity Accuracy","Fatal","Said the number five twice (\"Panch Panch\").","Recorded it as 55 Kg instead of 5 Kg.","Panch Panch","hear"),
 ("150467291263","Spec1 Accuracy","Fatal","Named the brand \"Dubai Kunafa\".","Recorded \"Dubai Kona\".","Dubai Kunafa","hear"),
 ("151774390467","WER","Fatal","Asked for an \"electronic cable\".","Heard \"Left Hand Cable\", which led to the wrong product category.","electronic cable","hear"),
 ("150792907159","WER","Non-Fatal","Said \"hanji Sir ji\" (yes, sir).","Replied as if the buyer had said to wait; the vendor transcript read \"hold on, hanji sir ji\".","hanji Sir ji","hear"),
 ("144976489875","WER","Non-Fatal","Asked for 100 cartons at least.","Read back 400 cartons and a pack size the buyer never gave, though 100 was recorded.","100 carton chahiye","hear"),
 ("151583069359","Spec2 Accuracy","Fatal","Said the use is a pharmacy college.","Recorded \"hospital use\", the nearest listed option.","Pharmacy college","value"),
 ("150792907159","Spec1 Accuracy","Non-Fatal","Asked for a \"chote dhakkan wali\" (small-cap) one.","Recorded \"1/2 inch\", which was not even in the option list.","chote dhakkan wali","value"),
 ("148254343495","Quantity Unit Accuracy","Fatal","Gave the quantity as 20 \"peti\" (cartons).","Recorded the unit as \"pack\"; the auditor ruled peti is not a pack.","maximum 20 peti","value"),
 ("148367534695","Spec2 Accuracy","Non-Fatal","Said they want all grit sizes.","Recorded a single size, 500.","all grit sizes","value"),
 ("145640522534","Quantity Accuracy","Fatal","Never gave a quantity.","Recorded a quantity anyway.","","value"),
 ("145640522534","Quantity Unit Accuracy","Fatal","Never gave a unit.","Recorded a unit anyway.","","value"),
 ("145229504595","Quantity Unit Accuracy","Fatal","Said 72 pieces.","Recorded 72 sqft and also confirmed 72 pieces aloud.","72 Pieces","value"),
 ("150913620652","Outcome Accuracy","Fatal","Did not confirm the requirement.","The lead was approved anyway.","","approve"),
 ("149551799652","Outcome Accuracy","Fatal","Answered \"No\" to the requirement, then hung up.","The lead was approved anyway.","No","approve"),
 ("151906312167","Spec Missed Opportunity","Fatal","Said the building is G+1 (number of floors).","Did not capture the number of floors.","G+1","missed"),
 ("146501629646","Qty Missed Opportunity","Fatal","Said \"one big bag\" when asked how many kilos.","Did not capture the quantity.","one bag, one big bag","missed"),
 ("150795858885","Spec Missed Opportunity","Non-Fatal","Said the size is 18 by 15.","Recorded 18 ft as the width instead of length 18 ft and width 15 ft.","18/15, 15 ka chahiye","missed"),
 ("151045423163","Spec Missed Opportunity","Non-Fatal","Answered \"Cylinder testing\" to an open question about type.","Did not capture this custom answer.","Cylinder testing","missed"),
 ("145435157959","Spec Missed Opportunity","Non-Fatal","Said \"six meter or twelve meter\" for lifting height.","Did not capture the values after offering meter options.","Chheh meter ya baarah meter","missed"),
 ("149815715067","Spec Missed Opportunity","Fatal","Had to ask for the brand options.","Did not offer the brand options when asking the question.","","skip"),
 ("149793460559","Quantity Probing Error","Fatal","Was never asked for a quantity.","Skipped the quantity question.","","skip"),
 ("145261422385","Spec Missed Opportunity","Fatal","Had only one spec defined, pack size.","Did not probe the remaining spec.","","skip"),
 ("149606162575","Spec Missed Opportunity","Non-Fatal","Could not follow the production-capacity question.","Asked it so quickly that the buyer could not catch it; the buyer later hung up.","","skip"),
 ("146585804767","Spec1 Accuracy","Fatal","Wanted a soil dumping service.","Put the lead under Tipper Trucks and recorded the answer as \"10 wheeler\".","Soil Dumping Service","category"),
 ("146585804767","MCAT Search Error","Non-Fatal","Wanted a service with no matching product category.","Mapped it to the nearest wrong category.","","category"),
 ("146339007375","Outcome Accuracy","Fatal","Changed to paper carry bags.","Could not switch product and left the lead under Handmade Gift, so the wrong seller was matched.","Paper Carry Bags","category"),
 ("147866592359","MCAT Search Error","Fatal","Asked for a syrup.","The lead went to the wrong product category.","","category"),
 ("145229504595","Looping Behavior","Fatal","Waited to be transferred.","Said the call would be transferred, then restarted the whole call flow, five times.","","handover"),
 ("145229504595","OnCall AstBuy Error","Non-Fatal","Waited to be transferred.","Said it would transfer but never did; the record says Transferred, which is wrong.","","handover"),
 ("147866592359","Looping Behavior","Non-Fatal","Was being transferred.","Said the whole transfer sentence twice in one go.","","handover"),
 ("151332292863","Looping Behavior","Non-Fatal","Was waiting on the line.","Said \"good sellers are available on the line\" twice.","abhi line par kafi achhe seller available hain","handover"),
 ("144798266885","Looping Behavior","Non-Fatal","Had just answered.","Said the \"seller is not available, I am IndiaMART\" opening twice.","","handover"),
 ("151766288259","WER","Non-Fatal","Was being connected to a seller.","Cut off the connect sentence after one word (\"mai\") and the call transferred.","mai","handover"),
 ("151561342746","OnCall AstBuy Error","Non-Fatal","Was trying to say something.","Transferred to a live seller without hearing the buyer first.","","handover"),
 ("144976489875","OnCall AstBuy Error","Non-Fatal","A seller was available.","Did not transfer the call; the record says Refused.","","handover"),
 ("150855659463","Dead air","Fatal","Waited on the line.","A long silence on the call; the auditor gave no detail.","","silence"),
 ("151809921685","Dead air","Non-Fatal","Waited on the line.","A silence on the call; the auditor gave no detail.","","silence"),
 ("145229504595","Dead air","Non-Fatal","Waited on the line.","A silence on the call; the auditor gave no detail.","","silence"),
 ("145229504595","WER","Non-Fatal","Not explained by the auditor.","Not explained by the auditor.","","oneoff"),
]

C = {
 "value": dict(title="Recorded value differs from what the buyer said", layer="prompt",
   summary="The human auditors found, in the bot's saved values, something different from what the buyer said, for example 'peti' saved as 'pack'. This shows only in the bot's logs, not in the recording. The lead then reaches sellers with the wrong size, unit or quantity."),
 "hear": dict(title="Bot mishears what the buyer said", layer="speech_recognition",
   summary="A number, brand or product name is heard wrongly, and the wrong word is saved. A wrong product name also sends the lead to the wrong category."),
 "handover": dict(title="Seller hand-over is repeated, cut off or fails", layer="flow_logic",
   summary="Around the moment the bot connects the buyer to a seller, it repeats lines, stops mid-sentence, transfers before listening, or says it will transfer and does not."),
 "skip": dict(title="Bot skips or rushes a required question", layer="prompt",
   summary="The bot does not ask the quantity or a spec at all, or asks so fast the buyer cannot follow. The lead goes out missing details the seller needs."),
 "category": dict(title="Bot forces a wrong product category", layer="flow_logic",
   summary="When the buyer's need does not fit a listed product, or the buyer changes product, the bot keeps the old or nearest category. The wrong sellers get the lead."),
 "missed": dict(title="Bot misses details the buyer gave in their own words", layer="prompt",
   summary="The buyer volunteers a detail in free form, such as G+1 floors, one big bag, or 18 by 15. The bot does not record it or records only part of it."),
 "approve": dict(title="Bot approves a lead the buyer did not confirm", layer="flow_logic",
   summary="The buyer never confirms the requirement, or says no and hangs up, yet the lead is approved and sent to a seller."),
 "silence": dict(title="Long silences on the line", layer="unclear",
   summary="The auditors marked dead air on these calls but did not say what caused it. Treat the cause as unknown until someone listens to them."),
 "oneoff": dict(title="One-off issues", layer="unclear", summary="Issues the auditor did not explain."),
}

FIX = {
 "value": dict(fix_type="prompt_patch",
   change="Record the buyer's own words exactly when they differ from the listed options (\"peti\" stays \"peti\", \"all sizes\" stays \"all sizes\", \"pharmacy college\" stays as said). Never replace a value with the closest option. If it is unclear which option the buyer means, repeat their words back once and ask.",
   where="Next to the existing custom-value rule (prompt line 652). Also reword line 977, \"Use the closest applicable unit naturally within the question\", so it only governs how the question is phrased, never what is recorded.",
   already_covered="Yes, partly. Line 652 says \"Preserve custom values without forcing them into listed options\" and line 627 says \"Accept buyer-provided custom units\". Line 977 (\"use the closest applicable unit\") may pull the other way. So the bot may be ignoring a rule it already has.",
   risk="Free-text values may not map to the downstream option lists, so someone must check how a custom value is stored.",
   test_buyer="A buyer who asks for 20 peti of diapers, then says the use is a pharmacy college, then says all sizes. Check each is recorded as said."),
 "hear": dict(fix_type="speech_recognition_check",
   change="Add the misheard terms (Dubai Kunafa, electronic cable, hanji) to the agent's keyterms or pronunciations, and have the bot read back any quantity that looks implausible (for example 55 kg for a small order) once.",
   where="Speech-recognition keyterms and pronunciations in the agent configuration; plus one exception to the no-reconfirm rule (prompt line 130).",
   already_covered="No existing rule found for reading a doubtful number back. Line 130 says \"Do not repeat, paraphrase, summarize, or reconfirm the value\", which stops this check.",
   risk="Extra confirmations add a turn and can annoy buyers if used on every value, so restrict it to implausible numbers.",
   test_buyer="A buyer who says \"panch panch\" for five kilos in a noisy line. Check the bot records 5, or reads it back."),
 "handover": dict(fix_type="prompt_patch",
   change="Say the transfer line once and finish the whole sentence before connecting. If the transfer does not happen or the seller does not pick up, say so once and close politely. Never restart the call flow. Listen for the buyer's reply before transferring.",
   where="In the finalisation and transfer section, next to the existing no-repeat rules (lines 184 and 359). The failed transfers also need an engineering check of the transfer tool.",
   already_covered="Partly. Line 184 says \"Do not repeat a closing already delivered\" and line 359 says \"Do not repeatedly explain the routing process\", but nothing covers a transfer that fails or a sentence cut off mid-way.",
   risk="Ending the call after one failed transfer could lose leads that a retry would have saved.",
   test_buyer="A buyer who agrees to be connected while the transfer tool is made to fail. Check the bot says so once and closes."),
 "skip": dict(fix_type="prompt_patch",
   change="Ask the quantity once even when the buyer is slow or distracted. When offering spec options, name at most three, then wait for the answer.",
   where="In the quantity and specification collection sections, next to line 898.",
   already_covered="Possibly in conflict. Line 898 says to skip the repetition and move forward whenever information is missing, and that may make the bot skip required questions too eagerly.",
   risk="Asking required questions more firmly could irritate buyers who are in a hurry; keep the limit of one extra ask.",
   test_buyer="A buyer who answers every question with one word and takes long pauses. Check quantity is still asked once and options are not read in a rush."),
 "missed": dict(fix_type="prompt_patch",
   change="When the buyer gives a detail in their own words, record all of it as said: a two-part size such as 18 by 15 goes into its fields (length 18, width 15), two values such as six or twelve meter are both kept, and a quantity such as one big bag is recorded as said. Ask once only if the meaning is unclear.",
   where="Next to the existing custom-value rule (prompt line 652) and the volunteered-answers rule (line 41).",
   already_covered="Partly. Line 652 says to preserve custom values and line 41 says to accept volunteered answers, but nothing covers two-part values, answers with \"or\", or free-form quantities such as \"one big bag\".",
   risk="Splitting a two-part value into the wrong fields could assign length and width the wrong way round, so the bot should keep the value as said when it is unsure.",
   test_buyer="A buyer who says the size is 18 by 15, then says six meter or twelve meter, then asks for one big bag."),
 "approve": dict(fix_type="prompt_patch",
   change="If the buyer says no to the requirement, or ends the call before confirming it, do not create a lead. Set the buyer disposition to BL Deleted and the call outcome to No Product Requirement, then close politely. If a no could refer only to one question, ask once whether they still need the product.",
   where="Next to the exhausted-confirmation rule (prompt line 37).",
   already_covered="Partly. Line 37 covers a confirmation that is exhausted after repeated asks, but not an explicit \"No\" or a hang-up after the opening question.",
   risk="A \"no\" to a single question could end a call that should continue, which is why the rule asks once when it is unclear.",
   test_buyer="A buyer who answers \"No\" to the opening question and hangs up, and another who says no to one spec but still wants the product."),
 "category": dict(fix_type="config_change",
   change="Decide what the bot should do when no product category fits or the buyer changes product: ask the buyer to confirm the new product, and do not create a lead under a category that does not match.",
   where="Product-change handling and category mapping, which the product team owns.",
   already_covered="The prompt has a product-change section, but the failures show the new category was not applied. Check whether the product-change tool call is made.",
   risk="Declining a lead when nothing fits loses a lead that a human might have matched manually.",
   test_buyer="A buyer who asks for a soil dumping service, then switches to paper carry bags."),
}

ET = {"value": "logs", "hear": "mixed", "handover": "call", "skip": "call", "category": "logs", "missed": "logs", "approve": "mixed", "silence": "call", "oneoff": "mixed"}


def build():
    issues, by = [], {}
    for n, (call, par, ver, trig, err, quote, key) in enumerate(I):
        issues.append({"id": n, "offer": call, "parameter": par, "human_verdict": ver, "agent_verdict": "NA", "trigger": trig,
                       "bot_error": err, "quote": quote, "layer": C[key]["layer"], "cluster": key})
        by.setdefault(key, []).append(n)
    W = {"Fatal": 3, "Non-Fatal": 1}
    clusters = []
    for key, mem in by.items():
        m = [issues[i] for i in mem]
        c = dict(C[key]); c["plain_summary"] = c.pop("summary")
        c.update(members=mem, issues=len(m), calls=len({x["offer"] for x in m}), fatal=sum(x["human_verdict"] == "Fatal" for x in m),
                 score=sum(W[x["human_verdict"]] for x in m), parameters=sorted({x["parameter"] for x in m}),
                 examples=[{"offer": x["offer"], "parameter": x["parameter"], "verdict": x["human_verdict"], "trigger": x["trigger"],
                            "bot_error": x["bot_error"], "quote": x["quote"]} for x in m[:4]], detected_by_agent=None)
        if key in FIX: c["fix"] = FIX[key]
        c["key"] = key
        c["evidence_type"] = ET[key]
        clusters.append(c)
    clusters.sort(key=lambda c: (c["key"] in ("oneoff",), c["key"] == "silence", -c["score"], -c["calls"]))
    for r, c in enumerate(clusters, 1): c["rank"] = r
    bp = {}
    for i in issues:
        d = bp.setdefault(i["parameter"], {"Fatal": 0, "Non-Fatal": 0}); d[i["human_verdict"]] += 1
    named = [c for c in clusters if c["key"] not in ("oneoff", "silence")]
    calls = sorted({i["offer"] for i in issues})
    ins = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "generated_by": "Grouped by Claude from the human auditors' remarks",
           "calls": len(calls), "issues": len(issues), "fatal_calls": 18, "nonfatal_calls": 12,
           "by_parameter": dict(sorted(bp.items(), key=lambda kv: -(kv[1]["Fatal"] * 3 + kv[1]["Non-Fatal"]))),
           "clusters": clusters,
           "quality": {"clusters": len(named), "issues_in_named_clusters": sum(c["issues"] for c in named),
                       "coverage_pct": round(100 * sum(c["issues"] for c in named) / len(issues)), "agent_detected_pct": None, "human_grouping_done": False},
           "vendor_note": "These 30 calls come from SquadStack's bot. The 'already covered' checks use Vani's own prompt, so they show what Vani already says, not what SquadStack's bot was told."}
    ins["all_issues"] = issues
    json.dump(ins, open(os.path.join(OUT, "insights_manual.json"), "w"), ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "grouping_sheet.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["issue_id", "call", "parameter", "severity", "what_the_buyer_did", "what_the_bot_did_wrong", "your_group_name"])
        for i in issues: w.writerow([i["id"], i["offer"], i["parameter"], i["human_verdict"], i["trigger"], i["bot_error"], ""])
    print(len(calls), "calls,", len(issues), "issues,", len(named), "named clusters, coverage", ins["quality"]["coverage_pct"], "%")
    for c in clusters: print(f"#{c['rank']} {c['title']} | {c['calls']} calls, {c['issues']} issues, {c['fatal']} fatal, score {c['score']} | {c['layer']}")
build()
