---
name: crisp-dm-iteration
description: Run one CRISP-DM improvement iteration on the HR assistant. Use before any change meant to improve quality, add a feature, or fix a failing eval case.
---

# CRISP-DM iteration

1. **Pick the phase.** State which phase the change belongs to (business understanding, data
   understanding, data preparation, modeling, evaluation, deployment) and why. Choose the
   earliest phase that explains the problem. Example: wrong chunk retrieved → data preparation
   (chunking/tokenisation) before modeling (prompts).
2. **Baseline.** Record the current metric for the affected component:
   - retrieval / rules: `python -m eval.run_eval --offline`
   - LLM components: `python -m eval.run_eval --ids <case ids>` (cached, cheap)
3. **Change** the smallest thing that could move the metric. Keep AGENTS.md §4 invariants.
4. **Re-evaluate** with the same command. Check that other components didn't regress.
5. **Log** in `docs/crisp_dm_log.md`:
   ```
   ## <YYYY-MM-DD> — <phase> — <short title>
   - Problem: <metric + failing case ids>
   - Change: <what and where>
   - Result: <metric before → after>
   - Next: <what to try if not solved>
   ```
6. If the change altered behaviour, metrics, or architecture, update `docs/REPORT.md`.
