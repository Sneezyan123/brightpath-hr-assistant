---
name: add-policy
description: Add or edit an HR policy document in data/policies so that retrieval, citations, topics, and evals stay consistent.
---

# Add or edit a policy

1. **Format** (the chunker depends on it):
   ```markdown
   # <Policy Title>
   <one-line intro: optional; becomes chunk #0>

   ## 1. <Section title>
   <self-contained text: repeat the subject ("annual leave", "sick days") instead of "it">
   ```
   File name = doc id (`snake_case.md`). Chunk id = `<doc id>#<section number>`.
   Never renumber existing sections: eval labels reference them. Append new sections at the end.
2. Keep each section focused on one question family, 40–150 words. Put exact numbers in the text.
3. If the section introduces a new topic, add it to `hr_assistant/topics.py`
   (`docs`, `required_slots`) and to the topic list in `prompts.ROUTER_SYSTEM`.
4. If it changes a leave rule, update `hr_assistant/leave_rules.py` in the same change (AGENTS.md §4).
5. Add at least 2 eval cases with the `add-eval-case` skill: one answerable, one near-miss that
   should be referred to HR.
6. Run `python -m eval.run_eval --offline` and check that retrieval Recall@3 did not drop.
