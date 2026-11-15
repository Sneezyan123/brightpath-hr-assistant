---
name: debug-trace
description: Localise which component of the HR assistant caused a bad reply by reading request traces.
---

# Debug a conversation with traces

Every request appends one JSON line to `logs/traces.jsonl`
(`{request_id, session_id, message, spans: [...]}`). The UI's Trace panel shows the same spans.

1. Find the request: `grep -n "<part of the user message>" logs/traces.jsonl | tail -1`.
2. Walk the spans in order and stop at the first wrong output:
   - `router`: wrong intent/topic/slots → router prompt or slot normalisation
   - `info_collection`: asked for a slot that was given, or didn't ask → `topics.py` / merge logic in `agent.py`
   - `retrieval`: gold chunk not in top-k → policy wording / tokeniser / topic boost
   - `referral_gate`: score threshold too high or too low
   - `answer`: claim not in the excerpts, missing citation → ANSWER_SYSTEM
   - `leave_agent` / `tool:*`: wrong tool or args → LEAVE_AGENT_SYSTEM; wrong decision → `leave_rules.py`
   - `llm_call`: raw prompt and completion, model, cache hit, tokens
3. Reproduce offline: add the conversation as an eval case (`add-eval-case` skill) so the fix is measured.
