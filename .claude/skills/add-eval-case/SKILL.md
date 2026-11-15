---
name: add-eval-case
description: Add labelled evaluation cases to eval/cases.jsonl for the router, retrieval, answers, referral, or leave agent.
---

# Add an evaluation case

One JSON object per line in `eval/cases.jsonl`. Ids are unique: `qa..`, `ref..`, `col..`, `lv..`, `oos..`, `gr..`.

```json
{"id": "qa30", "type": "qa", "employee_id": "E001",
 "turns": ["How many vacation days do I get?"],
 "gold": {
   "intent": "policy_question",            // intent of the FIRST turn (provide_info is never gold)
   "topic": "annual_leave",
   "missing_first_turn": [],               // slots the assistant should ask for after turn 1
   "slots_final": {},                      // slots expected after the last turn (optional)
   "relevant": ["annual_leave#2"],         // chunk ids that answer it; [] if unanswerable
   "must_include": ["25"],                 // short facts that must appear in the answer
   "reference": "Full-time employees get 25 working days per year.",
   "should_refer": false
 }}
```

Leave cases (`type: "leave"`) use instead:
`"tool": "create_leave_request", "args": {"leave_type": "annual", "start_date": "2026-12-07", "end_date": "2026-12-11"}, "final_status": "approved", "check_request_id": null`.
Eval "today" is fixed by `EVAL_TODAY` in `eval/run_eval.py` (2026-11-16, a Monday). Compute dates and working days against it and `data/seed.json`.

Labelling rules:
- `relevant` lists every section that is needed. Don't list sections that are only loosely related.
- `must_include` uses facts that appear verbatim in the policy (numbers, names). Keep it to 1–2 items.
- `employee_id: null` = guest, so profile slots (location, employment_type) must be asked.
- Check the label against `data/policies/*.md` and `leave_rules.py` before saving.
- Balance the set: keep roughly 1 referral case for every 3 answerable cases.
