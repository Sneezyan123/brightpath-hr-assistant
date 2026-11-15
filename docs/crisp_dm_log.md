# CRISP-DM iteration log

One entry per iteration (see the `crisp-dm-iteration` skill). Newest last.

## 2026-11-15 — Business understanding — scope
- Problem: assignment brief, option "HR assistant".
- Change: fixed scope R1–R8 (REPORT §1). Fictional company Brightpath Analytics with two offices, so location matters. Leave approval is rule-based; the LLM only calls tools.
- Result: metric per requirement defined before writing code (REPORT §1.3).

## 2026-11-15 — Data understanding — dataset search
- Problem: the brief asks to check existing datasets before generating synthetic ones.
- Checked: Hugging Face `syncora/hr-policies-qa-dataset` (MIT, <1k synthetic chat QA pairs with no source documents to retrieve from, so citations can't be checked), `adarsh-08/hr-policy-dataset` (instruction/response pairs, no policy corpus), `Jainamshahhh/hr-ops-tools` (tool-use tasks, sparse docs), `Karmane/enterprise-rag-internal-knowledge-search-benchmark` (gated, generic enterprise search).
- Decision: none gives a policy corpus + labelled questions + an employee/leave DB that agree with each other. We wrote 8 fictional policies (51 chunks: 43 numbered sections plus 8 intros), a seed DB, and 61 labelled eval cases ourselves. The public QA sets were used only for inspiration on question phrasing.

## 2026-11-15 — Data preparation — chunking + BM25 baseline
- Change: one chunk per `## N.` section (stable ids `doc#n`), section title weighted ×2, synonym expansion (vacation→annual leave, doctor→medical certificate…).
- Result (offline, gold question): BM25 Hit@1 0.938, Recall@3 1.000, MRR 0.958; +topic boost MRR 0.964.

## 2026-11-15 — Data preparation — "vacation days" paraphrase
- Problem: unit test `how many vacation days do I get` ranked `annual_leave#5` (carry-over, which says "vacation days") above `annual_leave#2` (entitlement).
- Change: section 2 retitled "Annual leave entitlement: how many vacation days you get", plus "vacation days" in its text.
- Result: the paraphrase now ranks #1. Eval set: Hit@1 0.938 → 0.906, MRR 0.964 → 0.948 (qa04, qa26, col04 slip to rank 2–3). Recall@4 (what the answerer sees) stays 1.000.
- Next: if Hit@1 matters, add a cross-encoder/LLM re-ranker over the top 4. Not needed while Recall@4 = 1.

## 2026-11-15 — Modeling — referral gate threshold
- Problem: choose when to skip generation and refer straight to HR.
- Change: threshold sweep over the top BM25 score (offline). AUC answerable vs. should-refer = 0.965, but the highest-scoring unanswerable question (10.2) scores above the lowest answerable one (5.9).
- Decision: threshold 4.0, which blocks 0 answerable questions and catches the clearly unrelated ones.
  Near-misses (salary band, phone plan) are left to the answerer's `supported=false` path. The two signals together are measured by the end-to-end referral F1.
- Update: online, the gate caught 0/8, because the router's rewritten questions score higher (see "first full run" below).

## 2026-11-15 — Modeling — switch to Claude Haiku 5.5
- Problem: OpenRouter's free tier (50 requests/day) can't run the ~150-call eval in one go.
- Change: `llm.py` gained an Anthropic provider (official SDK; OpenAI-style messages/tools converted inside the client). Default model `claude-haiku-5-5`, the cheapest current Claude ($0.10 / $0.50 per 1M tokens), thinking disabled, same model as judge. OpenRouter kept behind `HR_PROVIDER=openrouter`.
- Result: smoke eval (5 cases) all correct; mean latency ≈ 2.2 s per case.

## 2026-11-15 — Evaluation — first full run (61 cases)
- Result: router intent 1.00 / topic 0.98; follow-up F1 1.00; retrieval Recall@4 1.00, MRR 0.953; key facts 1.00; judge correctness 4.88; referral F1 1.00; leave tool/args/status 1.00; judge calibration 31/31.
- Problems: citation precision 0.854 (extra neighbouring sections); judge faithfulness 4.53 with 31% of answers < 5, caused by profile details ("as a full-time employee") that aren't in the cited text; online BM25 gate caught 0/8 unanswerable questions, and `supported=false` caught all 8.
- Next (modeling): answerer instruction "mention profile details only when they change the answer; cite only sections that state a fact you used"; give the judge the profile as a labelled source. Then (data understanding): a harder eval set written by someone else, because near-1.0 scores on self-written cases are optimistic.
