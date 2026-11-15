---
name: run-evals
description: Run the HR assistant evaluation suite, interpret per-component metrics, and write the analysis into the report.
---

# Run and interpret evals

## Run
- Offline (no API key, free): `python -m eval.run_eval --offline`
  → retrieval (Hit@1, Recall@3, MRR, with/without topic boost), referral gate separability, leave rules accuracy.
- Full: `python -m eval.run_eval` (add `--limit N`, `--ids a,b`, `--types qa,leave`, `--no-judge`, `--calibrate-judge`).
  LLM responses are cached in `.cache/llm/`, so re-runs are free. Delete a case's cache only if the prompt didn't change and you want a fresh sample.
- Outputs: `results/metrics.json`, `results/per_case.jsonl`, `results/eval_report.md`.

## Interpret (per component)
| Component | Metric | Healthy | If low, look at |
|---|---|---|---|
| Router | intent acc, topic acc | ≥ 0.9 | ROUTER_SYSTEM definitions, confusing cases |
| Info collection | follow-up P/R, missing-slot exact match | ≥ 0.85 | `topics.py` required slots, slot normalisation |
| Retrieval | Recall@3, MRR | ≥ 0.9 / ≥ 0.8 | chunk wording, tokeniser, topic boost |
| Answer | key-fact recall, judge correctness (1–5) | ≥ 0.85 / ≥ 4 | ANSWER_SYSTEM, retrieved context |
| Source support | citation precision, judge faithfulness | ≥ 0.9 / ≥ 4.5 | citation validation, prompt |
| Referral | precision, recall, F1 | ≥ 0.85 | BM25 gate threshold, `supported` flag |
| Leave agent | tool acc, args acc, status acc | ≥ 0.9 | LEAVE_AGENT_SYSTEM, date resolution in router |

Always separate **component error** from **upstream error**: an answer that is wrong because
retrieval missed is a retrieval failure. Check `retrieved` in per_case before blaming the answerer.

## Write up
Copy the tables from `results/eval_report.md` into `docs/REPORT.md §5`. For each metric below
target, list the failing ids, the root cause, and the fix you'd try next. Then log the run
with the `crisp-dm-iteration` skill.
