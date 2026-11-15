# Brightpath HR Assistant

NLP Assignment 1 prototype: an HR assistant that **collects missing information**, **answers policy questions
with cited sources**, **refers to HR** when policies don't cover a question, and **manages leave requests** in SQLite
through LLM tool calls only. Company, people, and policies are fictional.

## Submission contents

| Required by the assignment | Where |
|---|---|
| Code + setup and run instructions | this README, `hr_assistant/`, `app/` |
| High-level system diagram + data flow | [docs/REPORT.md §4](docs/REPORT.md) (Mermaid) |
| Business logic kept in scope | [docs/REPORT.md §1](docs/REPORT.md), leave rules in `hr_assistant/leave_rules.py` |
| CRISP-DM process | [docs/REPORT.md](docs/REPORT.md) (one section per phase), [docs/crisp_dm_log.md](docs/crisp_dm_log.md) |
| Evaluation of every component + results | [docs/REPORT.md §5](docs/REPORT.md), [results/eval_report.md](results/eval_report.md) |
| Agentic coding setup (agents file, skills) | [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), [.claude/skills/](.claude/skills/) |
| Demo video (≤ 10 min) | link added here after upload |

- Report (CRISP-DM, diagram, components, evals): [docs/REPORT.md](docs/REPORT.md)
- Agent instructions: [AGENTS.md](AGENTS.md) · skills: [.claude/skills/](.claude/skills/) · iteration log: [docs/crisp_dm_log.md](docs/crisp_dm_log.md)

## Setup
Python 3.11+.
```bash
pip install -r requirements.txt
```
```bash
cp .env.example .env
```
Put your Anthropic API key into `.env` (`ANTHROPIC_API_KEY`). The default model is **Claude Haiku 5.5**
(`claude-haiku-5-5`, the cheapest current Claude model at $0.10 / $0.50 per 1M tokens), and a full eval run costs a few cents.
OpenRouter free models are still supported: set `HR_PROVIDER=openrouter` and `OPENROUTER_API_KEY` (see `.env.example`).
All LLM responses are cached in `.cache/llm/`, so re-runs are free.

```bash
python -m hr_assistant.db --reset
```

## Run the app
```bash
python -m uvicorn app.server:app --reload
```
Open http://127.0.0.1:8000, pick a profile (or stay Guest), and chat. The right panel shows the trace of every component.
For a reproducible demo, set `HR_TODAY=2026-11-16` in `.env` (the date the eval cases use).

Demo script:
1. Guest: "How much parental leave can I take?" → follow-up questions → "Primary caregiver, full-time" → answer + source.
2. Anna Berzina: "How many vacation days do I get?" → answered from the profile, no follow-up.
3. Anna: "Can I bring my dog to the office?" → HR ticket.
4. Anna: "Book annual leave from 7 to 11 December" → approved; "Book annual leave 23–27 November" → declined (notice).
5. Peter Novak (part-time): "What's my leave balance?" → 2 days left.
6. Laura Kalnina (manager): "Show pending requests from my team" → "Approve request 1".
7. Anna: "Approve leave request 1" → refused (not a manager).

## Tests and evaluation
```bash
python -m pytest -q
```
```bash
python -m eval.run_eval --offline
```
```bash
python -m eval.run_eval --calibrate-judge
```
Results go to `results/eval_report.md`, `results/metrics.json`, and `results/per_case.jsonl`. Use `--ids qa01,lv02`, `--types leave`,
`--limit 10`, or `--no-judge` to save quota.

## Layout
```
hr_assistant/  router, retriever, answerer, leave agent, tools, rules, db, tracing, LLM client
app/           FastAPI server + static UI
data/          policies/*.md, seed.json
eval/          cases.jsonl, metrics.py, run_eval.py
tests/         offline tests (scripted fake LLM)
docs/          REPORT.md, crisp_dm_log.md
```
