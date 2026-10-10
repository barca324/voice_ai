# Vani test agent: setup in the Sarvam dashboard (Option B)

Goal: a safe **copy** of Vani that you can chat with, so we can compare the current prompt with a patched one.
Never commit or deploy this copy.

## 1. Create the agent
1. Voice Agents -> Agents -> **Create from scratch**.
2. Name it `Vani_prod_copy_test`.
3. Open the agent's instructions (start state) and paste the whole file
   `/home/imart/hacki/data/vani_prompt_for_agent.txt` (158,206 characters). Replace anything already there.
   - Copy with: `xclip -selection clipboard < /home/imart/hacki/data/vani_prompt_for_agent.txt`
     (or open the file in an editor and select all).
4. Save as a draft.

## 2. Add the 13 variables (names must match exactly)
The prompt refers to these. Set the defaults below for the first test case (a fire extinguisher refilling buyer).
Change `product_name`, `specification_options` and `quantity_unit_options` per test case.

| Variable | Default value | Meaning |
|---|---|---|
| is_enrich | 1 | enrichment call |
| ast_seller_pns | 9999999999 | live seller number; empty means no seller available |
| ast_flow_live | true | live seller connect flow active |
| pcd_pitch | false | seller connection already pitched |
| product_name | Fire Extinguisher Refilling | product the buyer enquired about |
| quantity_unit_options | ["Piece", "Kg"] | allowed units |
| specification_options | {"Capacity": ["4 kg", "6 kg"]} | spec name -> options |
| buyer_name | (empty) | known buyer name |
| updated_buyer_name | (empty) | name captured in the call |
| buyer_city | (empty) | known city |
| triangulation_response_value | (empty) | location check result |
| triangulation_response_city | (empty) | city from the location check |
| language_name | hindi | call language |

These defaults are guesses for a standard flow with a live seller. If you know the real values for the May calls, use those.


## 2b. Add these 25 MORE variables (the prompt reads and writes them)
The platform reports `variable not found` / `reference not found` until each exists. Names must match exactly.

| Variable | Default |
|---|---|
| additional_details | (empty) |
| already_bought_handled | 0 |
| ast_call_remark | (empty) |
| bl_converted | 0 |
| buyer_disposition | (empty) |
| call_outcome | (empty) |
| call_step | (empty) |
| connect_ast_call | (empty) |
| current_phase | (empty) |
| identity_challenge_count | 0 |
| identity_revealed | 0 |
| image_reference | (empty) |
| lead_type | LIVE |
| off_topic_counter | 0 |
| quantity | (empty) |
| quantity_asked_count | 0 |
| quantity_unit | (empty) |
| seller_ask_count | 0 |
| specifications | (empty) |
| turn_intent | (empty) |
| updated_city_name | (empty) |
| updated_state_name | (empty) |
| ast_buy_confirmed_tool | (empty) |
| requested_another_product_tool | (empty) |
| update_ast_buy_variables_by_city_tool | (empty) |

Notes
- The last three are **tools** in production (product check, city lookup, seller transfer). The test copy has no such tools, so add them
  as empty variables only to satisfy the validator. Their side effects will not happen, so this copy tests the conversation
  (what the bot asks and captures), not the integrations.
- Counters start at 0 and the prompt increments them itself. `lead_type` = LIVE is an assumption.
- Also set the agent's opening message to: `Hello, do you require {{product_name}}?` (the real Vani opens by confirming the product).

## 3. Run a test conversation
1. Open the agent's **test chat** (the playground in the Voice Agents product).
2. Play the buyer yourself, using the persona and style the site shows under the fix's "Test scenario".
3. Copy the whole chat.

## 4. Audit it on the site
Open http://127.0.0.1:8791 -> **Or paste a chat transcript** -> paste the chat with each line starting
`Vani:` or `Buyer:` (also accepted: Agent:, Assistant:, User:, Customer:) -> **Audit transcript**.

## 5. Compare current vs patched
1. Run 3 or more conversations with the prompt as it is. Paste each into the site. Note the failures.
2. Add the suggested patch text to the prompt (at the location the fix names). Save the draft.
3. Run the same buyer script again, 3 or more times. Paste each into the site.
4. Compare the failure counts. Then **remove the patch** so the copy matches production again.
