@AGENTS.md

## Claude Code specifics

- Project skills are in `.claude/skills/`. Invoke the matching skill before you start (see AGENTS.md §7).
- Plan multi-file changes first and name the CRISP-DM phase in the plan.
- After editing an LLM component, run `python -m eval.run_eval --ids <affected ids>` rather than the full suite.
- Ask before adding dependencies, changing the DB schema, or changing leave rules.
