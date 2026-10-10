"""Regroup: cluster the reviewed baseline problems plus every live problem, rank, and propose fixes for the top patterns.
Writes out/insights/insights_live.json (same shape as the other findings files)."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixer  # noqa: E402
import insights as I  # noqa: E402
import llm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out", "insights")
W = {"Fatal": 3, "Non-Fatal": 1, "Observation": 1}


def run(live_issues, progress=lambda m: None):
    base = json.load(open(os.path.join(OUT, "insights_manual.json")))["all_issues"]
    issues = []
    for b in base:
        issues.append({"id": len(issues), "source": "reviewed", "call": b["offer"], "parameter": b["parameter"], "verdict": b["human_verdict"],
                       "text": f"buyer: {b['trigger']} | bot: {b['bot_error']}", "quote": b.get("quote", "")})
    for l in live_issues:
        issues.append({"id": len(issues), "source": "live", "call": l["call"], "parameter": l["parameter"], "verdict": l["verdict"],
                       "text": l["root_cause"] + (f" (a reviewer placed this in: {l['hint']})" if l.get("hint") else ""), "quote": ""})
    progress(f"Grouping {len(issues)} problems ({len(live_issues)} from new calls)")
    listing = "\n".join(f"{i['id']}. [{i['parameter']}] {i['text']}" for i in issues)
    cl = llm.chat(I.CLUSTER_SYSTEM, listing, schema=I.CLUSTER_SCHEMA, name="clusters", max_tokens=3500)["clusters"]
    seen, clusters = set(), []
    for c in cl:
        ms = [m for m in c["members"] if 0 <= m < len(issues) and m not in seen]
        seen.update(ms)
        if ms:
            clusters.append({**c, "members": ms})
    left = [i["id"] for i in issues if i["id"] not in seen]
    if left:
        clusters.append({"title": "One-off issues", "plain_summary": "Issues that did not fit another group.", "layer": "unclear", "members": left})
    for c in clusters:
        mem = [issues[m] for m in c["members"]]
        c.update(issues=len(mem), calls=len({m["call"] for m in mem}), fatal=sum(m["verdict"] == "Fatal" for m in mem),
                 score=sum(W.get(m["verdict"], 1) for m in mem), parameters=sorted({m["parameter"] for m in mem}),
                 from_new_calls=sum(1 for m in mem if m["source"] == "live"),
                 examples=[{"offer": m["call"], "parameter": m["parameter"], "verdict": m["verdict"], "trigger": m["text"],
                            "bot_error": "", "quote": m["quote"]} for m in mem[:4]])
    clusters.sort(key=lambda c: (c["title"].lower().startswith("one-off"), -c["score"], -c["calls"]))
    for r, c in enumerate(clusters, 1):
        c["rank"] = r
    progress("Proposing fixes for the top patterns")
    for c in [c for c in clusters if not c["title"].lower().startswith("one-off")][:4]:
        ex = "\n".join(f"- [{issues[m]['parameter']}] {issues[m]['text']}" for m in c["members"][:10])
        try:
            c["fix"] = llm.chat(I.FIX_SYSTEM, f"GROUP: {c['title']}\n{c['plain_summary']}\nISSUES:\n{ex}\n\nEXCERPTS OF THE CURRENT PROMPT:\n"
                                f"{fixer.excerpts(c['parameters'], ex, max_chars=7000)}", schema=I.FIX_SCHEMA, name="fix", max_tokens=1200)
        except Exception as e:
            c["fix"] = None
    named = [c for c in clusters if not c["title"].lower().startswith("one-off")]
    by_param = {}
    for i in issues:
        d = by_param.setdefault(i["parameter"], {"Fatal": 0, "Non-Fatal": 0})
        d["Fatal" if i["verdict"] == "Fatal" else "Non-Fatal"] += 1
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M"),
           "generated_by": f"Sarvam pipeline, regrouped with {len(live_issues)} problems from new calls (automatic, unconfirmed)",
           "calls": len({i["call"] for i in issues}), "issues": len(issues),
           "by_parameter": dict(sorted(by_param.items(), key=lambda kv: -(kv[1]["Fatal"] * 3 + kv[1]["Non-Fatal"]))),
           "clusters": clusters,
           "quality": {"clusters": len(named), "issues_in_named_clusters": sum(c["issues"] for c in named),
                       "coverage_pct": round(100 * sum(c["issues"] for c in named) / max(1, len(issues))), "human_grouping_done": False},
           "vendor_note": "Reviewed problems come from SquadStack's bot; new-call problems come from whatever was uploaded. The 'already covered' checks use Vani's own prompt."}
    json.dump(out, open(os.path.join(OUT, "insights_live.json"), "w"), ensure_ascii=False, indent=1)
    return out
