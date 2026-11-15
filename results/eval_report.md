# Evaluation report

Generated 2026-11-15 19:06 · eval date 2026-11-16 · {"model": "claude-haiku-5-5", "judge": "claude-haiku-5-5", "cases": 61, "llm_network_calls": 220, "llm_cache_hits": 16}

## Offline components (no LLM)

### Retrieval: bm25 (gold question)

| Metric | Value |
|---|---|
| n | 32 |
| hit@1 | 0.906 |
| recall@3 | 1.000 |
| recall@4 | 1.000 |
| mrr | 0.943 |
| misses@4 | [] |

### Retrieval: bm25+topic_boost (gold question)

| Metric | Value |
|---|---|
| n | 32 |
| hit@1 | 0.906 |
| recall@3 | 1.000 |
| recall@4 | 1.000 |
| mrr | 0.948 |
| misses@4 | [] |

### Referral gate (top BM25 score)

| Metric | Value |
|---|---|
| auc_answerable_vs_refer | 0.965 |
| answerable_top_score_min | 5.872 |
| refer_top_score_max | 10.219 |
| current_threshold | 4.000 |
| at_current | {'threshold': 4.0, 'precision': 1.0, 'recall': 0.375, 'f1': 0.545, 'answerable_blocked': 0} |

### Leave rules engine (gold arguments)

| Metric | Value |
|---|---|
| n | 11 |
| accuracy | 1 |
| errors | [] |

## Online components (LLM)

### Router / intake (first turn)

| Metric | Value |
|---|---|
| n | 61 |
| intent_accuracy | 1 |
| topic_accuracy | 0.980 |
| intent_confusions | {} |

### Information collection

| Metric | Value |
|---|---|
| follow_up_precision | 1.000 |
| follow_up_recall | 1.000 |
| follow_up_f1 | 1.000 |
| missing_slots_exact_match | 1 |
| slot_value_accuracy | 1 |
| completed_after_follow_up | 1 |

### Retrieval (router query, end-to-end)

| Metric | Value |
|---|---|
| n | 32 |
| hit@1 | 0.906 |
| recall@4 | 1.000 |
| mrr | 0.953 |
| misses | [] |

### Answer correctness

| Metric | Value |
|---|---|
| n_answerable | 32 |
| n_answered | 32 |
| key_fact_recall_answered | 1.000 |
| end_to_end_correct_rate | 1 |
| judge_correctness_mean | 4.875 |
| judge_correctness_ge4 | 1 |
| low_scored | [] |

### Source support

| Metric | Value |
|---|---|
| citation_precision | 0.854 |
| citation_recall | 1.000 |
| judge_faithfulness_mean | 4.531 |
| judge_faithfulness_eq5 | 0.688 |

### Referral to HR

| Metric | Value |
|---|---|
| precision | 1.000 |
| recall | 1.000 |
| f1 | 1.000 |
| accuracy | 1.000 |
| by_reason | {'unsupported': 8, 'sensitive': 1} |
| false_referrals | [] |
| missed_referrals | [] |

### Leave agent (tool use + DB outcome)

| Metric | Value |
|---|---|
| n | 17 |
| tool_selection_accuracy | 1 |
| argument_accuracy | 1 |
| final_status_accuracy | 1 |
| reply_fact_recall | 1.000 |
| failures | [] |

### Judge calibration (correct vs corrupted answers)

| Metric | Value |
|---|---|
| n | 31 |
| mean_correct_answer | 4.968 |
| mean_corrupted_answer | 1.161 |
| pairwise_accuracy | 1 |
| faithfulness_pairwise_accuracy | 1 |

### Latency

| Metric | Value |
|---|---|
| per_case_mean | 2189.279 |
| answer_mean | 1036.905 |
| hr_referral_mean | 2.767 |
| info_collection_mean | 0.000 |
| leave_agent_mean | 1834.525 |
| referral_gate_mean | 0.000 |
| retrieval_mean | 0.168 |
| router_mean | 1026.895 |
| tool_mean | 2.681 |
