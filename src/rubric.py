"""The Vani audit rubric, encoded.

The LLM only reports FACTS per parameter (is there an error, what kind). The Fatal / Non-Fatal decision is made
here, in code, from the rubric table, so grading is consistent and every verdict names the rule that produced it.
"""

PARAMS = [
    "Outcome Accuracy", "Quantity Accuracy", "Quantity Unit Accuracy", "Quantity Probing Error",
    "Qty Missed Opportunity", "Spec1 Accuracy", "Spec2 Accuracy", "Spec Missed Opportunity",
    "Looping Behavior", "WER", "Additional Details", "OnCall AstBuy Error",
    "Product Change Accuracy", "Category ISQ Error", "MCAT Search Error",
]

# what the judge LLM is told about each parameter (definition column of the rubric)
DEFINITIONS = {
    "Outcome Accuracy": "Buyer intent (buying or not) captured correctly.",
    "Quantity Accuracy": "Quantity VALUE captured correctly.",
    "Quantity Unit Accuracy": "Quantity UNIT the buyer said captured correctly.",
    "Quantity Probing Error": "Quantity probed when the buyer already defined it (error); or quantity NOT probed when it was not defined; or the nudge is not proper.",
    "Qty Missed Opportunity": "Buyer shared a quantity but the bot missed capturing it.",
    "Spec1 Accuracy": "If the buyer's answer for spec 1 was captured as something else, and it matters to the buy lead.",
    "Spec2 Accuracy": "If the buyer's answer for spec 2 was captured as something else, and it matters to the buy lead.",
    "Spec Missed Opportunity": "Spec 1 or 2 shared by the buyer was not captured / not even added to the BFD (buyer filled details).",
    "Looping Behavior": "Probing the same parameter (qty, spec, ...) more than 2 times.",
    "WER": "Words wrongly captured (the bot's understanding differs from what the buyer said).",
    "Additional Details": "Additional buyer details (BFD) captured: wrong, irrelevant, duplicate or very generic.",
    "OnCall AstBuy Error": "A live seller was present/available but the bot did not pitch the connection; or it was not pitched and the disposition was wrongly shared.",
    "Product Change Accuracy": "Product wrongly captured / product change or 'no product' handled wrongly.",
    "Category ISQ Error": "The bot's questions/options contradict or confuse the product's specification options.",
    "MCAT Search Error": "Wrong MCAT (product category) selected. Cannot be judged from audio alone.",
}

# the `kind` the judge picks when error=true, per parameter
KINDS = {
    "Outcome Accuracy": ["wrong_intent"],
    "Quantity Accuracy": ["affects_lead", "minor_difference"],
    "Quantity Unit Accuracy": ["affects_lead", "similar_unit"],
    "Quantity Probing Error": ["defined_and_not_asked", "probed_though_defined", "not_defined_but_asked", "nudge_not_proper"],
    "Qty Missed Opportunity": ["missed_clear_voice", "missed_voice_unclear"],
    "Spec1 Accuracy": ["affects_lead", "minor_difference"],
    "Spec2 Accuracy": ["affects_lead", "minor_difference"],
    "Spec Missed Opportunity": ["spec1_or_2_missed", "spec3_missed"],
    "Looping Behavior": ["irritated_or_disconnect_or_opportunity_missed", "no_impact_on_call"],
    "WER": ["affects_lead", "no_major_impact"],
    "Additional Details": ["grossly_wrong_or_irrelevant", "duplicate_or_generic"],
    "OnCall AstBuy Error": ["seller_present_not_pitched", "not_pitched_disposition_wrong"],
    "Product Change Accuracy": ["wrong_product_approved", "minor_product_mismatch"],
    "Category ISQ Error": ["contradicting_isq", "confusing_isq"],
    "MCAT Search Error": ["wrong_mcat"],
}

FATAL, NONFATAL, PASS, NA = "Fatal", "Non-Fatal", "Pass", "NA"

# (parameter, kind) -> verdict ; and the rubric wording that justifies it
RULES = {
    ("Outcome Accuracy", "wrong_intent"): (FATAL, "Outcome accuracy errors are always Fatal"),
    ("Quantity Accuracy", "affects_lead"): (FATAL, "Wrong quantity value that affects the lead is Fatal (category-dependent)"),
    ("Quantity Accuracy", "minor_difference"): (NONFATAL, "Minor difference (10-20) can be overlooked"),
    ("Quantity Unit Accuracy", "affects_lead"): (FATAL, "Wrong unit impacting the buy lead is Fatal"),
    ("Quantity Unit Accuracy", "similar_unit"): (NONFATAL, "Similar-value units are Non-Fatal"),
    ("Quantity Probing Error", "defined_and_not_asked"): (FATAL, "Fatal if quantity was defined and not asked"),
    ("Quantity Probing Error", "probed_though_defined"): (FATAL, "Fatal if quantity was defined and the bot still probed"),
    ("Quantity Probing Error", "not_defined_but_asked"): (NONFATAL, "Non-Fatal if not defined and asked"),
    ("Quantity Probing Error", "nudge_not_proper"): (NONFATAL, "Improper nudge is Non-Fatal"),
    ("Qty Missed Opportunity", "missed_clear_voice"): (FATAL, "Quantity shared but missed is Fatal"),
    ("Qty Missed Opportunity", "missed_voice_unclear"): (NONFATAL, "If the voice was not clear, Non-Fatal"),
    ("Spec1 Accuracy", "affects_lead"): (FATAL, "Spec captured wrongly with impact on the buy lead is Fatal (category-dependent)"),
    ("Spec1 Accuracy", "minor_difference"): (NONFATAL, "Minor difference can be overlooked"),
    ("Spec2 Accuracy", "affects_lead"): (FATAL, "Spec captured wrongly with impact on the buy lead is Fatal (category-dependent)"),
    ("Spec2 Accuracy", "minor_difference"): (NONFATAL, "Minor difference can be overlooked"),
    ("Spec Missed Opportunity", "spec1_or_2_missed"): (FATAL, "Spec 1 or 2 shared by the buyer but not captured / not even in the BFD is Fatal"),
    ("Spec Missed Opportunity", "spec3_missed"): (NONFATAL, "Only a missed spec 3 (not even in BFD) is Non-Fatal"),
    ("Looping Behavior", "irritated_or_disconnect_or_opportunity_missed"): (FATAL, "Probing >2 times and the buyer got irritated / call disconnected / quantity or spec opportunity missed is Fatal"),
    ("Looping Behavior", "no_impact_on_call"): (NONFATAL, "Probing >2 times with no impact on the call is Non-Fatal"),
    ("WER", "affects_lead"): (FATAL, "Word captured wrongly with impact on the buy lead is Fatal"),
    ("WER", "no_major_impact"): (NONFATAL, "Word wrongly captured but no major impact on the buy lead is Non-Fatal"),
    ("Additional Details", "grossly_wrong_or_irrelevant"): (FATAL, "Grossly wrong / irrelevant details impacting the buy lead is Fatal"),
    ("Additional Details", "duplicate_or_generic"): (NONFATAL, "Duplicate or very generic non-impacting details are Non-Fatal"),
    ("OnCall AstBuy Error", "seller_present_not_pitched"): (FATAL, "Seller present but not pitched is Fatal"),
    ("OnCall AstBuy Error", "not_pitched_disposition_wrong"): (NONFATAL, "Not pitched but disposition wrongly shared is Non-Fatal"),
    ("Product Change Accuracy", "wrong_product_approved"): (FATAL, "Fatal if a wrong product was approved"),
    ("Product Change Accuracy", "minor_product_mismatch"): (NONFATAL, "Minor product mismatch that was not approved as the lead"),
    ("Category ISQ Error", "contradicting_isq"): (FATAL, "Fatal if the ISQs are contradicting"),
    ("Category ISQ Error", "confusing_isq"): (NONFATAL, "Confusing but not contradicting ISQs"),
    ("MCAT Search Error", "wrong_mcat"): (FATAL, "Wrong MCAT selected"),
}

DEAD_AIR_NORMAL_S = 5.0
DEAD_AIR_NOISE_S = 8.0  # "for noise abruption consider 8 seconds"

# the human audit CSV column for each parameter, used when comparing with human auditors
HUMAN_COLUMNS = {
    "Outcome Accuracy": "Outcome Accuracy (Intent)", "Quantity Accuracy": "Quantity Accuracy (Numeric)",
    "Quantity Unit Accuracy": "Quantity Unit Accuracy", "Quantity Probing Error": "Quantity Probing Error",
    "Qty Missed Opportunity": "Quantity Missed Opportunity (Capture)", "Spec1 Accuracy": "Spec1 Accuracy",
    "Spec2 Accuracy": "Spec2 Accuracy", "Spec Missed Opportunity": "Spec Missed Opportunity",
    "Looping Behavior": "Looping Behavior", "WER": "WER/STT", "Additional Details": "Additional Details Accuracy",
    "OnCall AstBuy Error": "OnCall AstBuy Error (Transferred)", "Product Change Accuracy": "Product Change/ No Product Accuracy",
    "Category ISQ Error": "Category ISQ's Error", "MCAT Search Error": "MCAT/ Search errors",
    "Dead air": "Dead air",
}


def decide(parameter, applicable, error, kind):
    """Facts -> (verdict, rule). The only place severity is decided."""
    if not applicable:
        return NA, "Not applicable to this call"
    if not error:
        return PASS, "No error found"
    verdict, why = RULES.get((parameter, kind), (None, None))
    if verdict is None:  # judge named an error but no valid kind: do not guess severity
        return NONFATAL, f"Error reported with unrecognised kind '{kind}'; defaulted to Non-Fatal, needs human review"
    return verdict, why


def overall(verdicts):
    vs = set(verdicts)
    return FATAL if FATAL in vs else NONFATAL if NONFATAL in vs else PASS


def rubric_prompt():
    lines = []
    for p in PARAMS:
        lines.append(f"- {p}: {DEFINITIONS[p]}  (error kinds: {', '.join(KINDS[p])})")
    return "\n".join(lines)
