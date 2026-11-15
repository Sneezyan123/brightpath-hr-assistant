# AGENTS.md — instructions for AI coding agents

This file is the single source of truth for any coding agent (Claude Code, Codex, Cursor…)
working in this repository. `CLAUDE.md` imports it. Read it fully before changing code.

## 1. What this project is

**Brightpath HR Assistant** — a course prototype (NLP Assignment 1) that answers employee
questions about HR policies and manages leave requests.

| Capability | Must do |
|---|---|
| Information collection | Detect the intent and topic, extract slots, ask a follow-up when a required slot is missing |
| Policy QA | Retrieve the relevant policy section and answer **only** from it, citing `doc#section` |
| HR referral | Open an HR ticket when policies don't support an answer, or the topic is sensitive |
| Leave management | Create/list/cancel/approve leave **only through LLM tool calls** on the SQLite DB |
| Evaluation | Every component has its own metric in `eval/` |

Company, people, and policies are **fictional** (Brightpath Analytics, offices in Riga and Lisbon).

## 2. Process: follow CRISP-DM

Every piece of work belongs to one CRISP-DM phase. Say which phase you are in when you start a
task, and log the iteration in `docs/crisp_dm_log.md` (date, phase, what changed, metric before → after).

1. **Business understanding**: requirements live in `docs/REPORT.md §1`. Don't add features outside them without asking.
2. **Data understanding**: policies in `data/policies/`, seed data in `data/seed.json`, eval cases in `eval/cases.jsonl`.
3. **Data preparation**: chunking (`policies.py`), tokenisation (`retriever.py`), DB seeding (`db.py`).
4. **Modeling**: router, retriever, answerer, leave agent, and their prompts in `prompts.py`.
5. **Evaluation**: `python -m eval.run_eval`. No change to a component is done until its metric was re-run.
6. **Deployment**: `app/server.py` + `app/static/index.html` (local web UI with a trace panel).

Iterate: if an eval shows a weakness, go back to the earliest phase that explains it
(e.g. low retrieval recall → data preparation/chunking, not prompt tweaking).

## 3. Repository map: read only what the task needs

```
hr_assistant/
  config.py       settings from .env (model ids, paths, HR_TODAY override)
  llm.py          LLM client (Anthropic SDK default, OpenRouter optional): disk cache, retries, JSON parsing
  trace.py        per-request trace spans (shown in UI, appended to logs/traces.jsonl)
  topics.py       topic → policy docs, required slots, follow-up questions
  policies.py     markdown policy loader → section chunks (id = "<doc>#<section>")
  retriever.py    BM25 (no external deps) + topic boost
  router.py       intake: intent, topic, slots, standalone question (1 LLM call)
  answerer.py     grounded answer + citation validation (1 LLM call)
  leave_rules.py  deterministic leave approval rules (NO LLM)
  db.py           SQLite schema, seed, queries
  tools.py        tool schemas + executor: the ONLY code path that reads/writes leave data
  leave_agent.py  LLM tool-calling loop over tools.py
  agent.py        orchestrator: HRAssistant.handle(session, message) -> Response
app/              FastAPI server + static chat UI
eval/             cases.jsonl, metrics.py, run_eval.py
tests/            offline pytest (scripted fake LLM; no network)
docs/             REPORT.md, crisp_dm_log.md
.claude/skills/   reusable workflows for agents (see §7)
```

## 4. Hard rules (architecture invariants)

- **Leave data is reachable only via `tools.py`**, which is called only by `leave_agent.py`.
  The web server must never expose endpoints that read or write leave requests or balances.
- The employee identity comes from the session, **never from LLM arguments**. Tools take it from `LeaveTools.employee`.
- Approval decisions are made by `leave_rules.py` (deterministic). The LLM only chooses tools and arguments and phrases the result.
- Policy answers must cite chunk ids that were actually retrieved. `answerer.py` strips invalid citations; no valid citation → referral.
- Rules in `leave_rules.py` and the text of `data/policies/*.md` must agree. When you change one, change the other in the same commit and re-run evals.
- Model ids, API keys, and paths come from `config.py` / `.env`. Never hard-code a key. Never commit `.env`.
- All LLM calls go through `LLMClient` (cache + trace). Temperature 0 everywhere except where justified in a comment.
- Prompts live in `prompts.py` as constants, not inline in logic.

## 5. Code conventions

- Python ≥ 3.11, standard library first. Allowed deps: see `requirements.txt`; ask before adding one.
- Type hints on all public functions; `from __future__ import annotations`; dataclasses for structured results.
- Small pure functions; side effects (DB, network, files) at the edges.
- Each component returns a structured result **and** records a trace span (`trace.span(...)`).
- No bare `except:`; catch specific errors and surface a user-safe message.
- Docstrings: one line saying what the function does and anything surprising.

## 6. Definition of done

1. `python -m pytest -q` passes (offline, no API key needed).
2. `python -m eval.run_eval --offline` runs, and so does the full eval if the change touches an LLM component.
3. `docs/REPORT.md` updated if behaviour, metrics, or the architecture diagram changed.
4. Iteration logged in `docs/crisp_dm_log.md`.

## 7. Skills (in `.claude/skills/`)

| Skill | Use when |
|---|---|
| `crisp-dm-iteration` | starting any improvement: forces phase choice, baseline metric, log entry |
| `add-policy` | adding or editing a policy document |
| `add-eval-case` | adding labelled cases to `eval/cases.jsonl` |
| `run-evals` | running evaluation, interpreting metrics, writing the analysis |
| `debug-trace` | a conversation went wrong: localise the failing component from `logs/traces.jsonl` |

## 8. Commands

```bash
pip install -r requirements.txt
python -m hr_assistant.db --reset              # recreate data/hr.db from data/seed.json
python -m uvicorn app.server:app --reload      # UI at http://127.0.0.1:8000
python -m pytest -q
python -m eval.run_eval --offline              # retrieval + rules, no LLM
python -m eval.run_eval                        # full eval (uses the configured LLM, cached in .cache/)
```

## 9. Context hygiene for agents

- Don't read `.cache/`, `logs/`, or `results/per_case.jsonl` whole. Grep them.
- For an LLM behaviour bug, read the prompt in `prompts.py` plus the trace, not the whole package.
- LLM calls cost money (Claude Haiku 5.5 by default) or quota (OpenRouter free tier: ≈50/day). Don't loop full evals; use `--limit`/`--ids`.
- Claude 5.x models reject `temperature`; reproducibility comes from the response cache, not sampling settings.
