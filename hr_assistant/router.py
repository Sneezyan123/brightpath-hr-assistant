"""Intake: one LLM call for intent, topic, slots, and a standalone question; then deterministic validation."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from .llm import ChatModel
from .prompts import ROUTER_SYSTEM, ROUTER_USER
from .topics import INTENTS, LEAVE_REQUEST_SLOTS, SLOT_VALUES, TOPICS
from .trace import Trace


@dataclass
class RouterResult:
    intent: str
    topic: str | None
    slots: dict
    standalone_question: str
    dropped_slots: dict = field(default_factory=dict)


_ALIASES = {"full-time": "full_time", "fulltime": "full_time", "part-time": "part_time", "parttime": "part_time",
            "vacation": "annual", "holiday": "annual", "immediate": "immediate_family", "other": "other_relative",
            "approved": "approve", "declined": "decline", "reject": "decline"}


def normalize_slots(raw: dict) -> tuple[dict, dict]:
    """Keep only known slots with valid values; return (clean, dropped) so drops are visible in traces."""
    clean, dropped = {}, {}
    for k, v in (raw or {}).items():
        if v in (None, "", [], "null") or k not in SLOT_VALUES:
            if v not in (None, "", [], "null"):
                dropped[k] = v
            continue
        allowed = SLOT_VALUES[k]
        if allowed is not None:
            val = _ALIASES.get(str(v).strip().lower(), str(v).strip().lower().replace(" ", "_").replace("-", "_"))
            (clean if val in allowed else dropped)[k] = val if val in allowed else v
        elif k in ("start_date", "end_date"):
            try:
                clean[k] = date.fromisoformat(str(v)).isoformat()
            except ValueError:
                dropped[k] = v
        else:  # positive integers
            try:
                n = int(float(v))
                (clean if n > 0 else dropped)[k] = n if n > 0 else v
            except (TypeError, ValueError):
                dropped[k] = v
    return clean, dropped


def route(llm: ChatModel, message: str, history: list[dict], pending: dict | None, today: date,
          trace: Trace) -> RouterResult:
    hist = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-6:]) or "(none)"
    pend = json.dumps({k: pending[k] for k in ("intent", "topic", "question", "slots", "asked")}) if pending else "none"
    messages = [
        {"role": "system", "content": ROUTER_SYSTEM.format(today=today.isoformat(), weekday=today.strftime("%A"))},
        {"role": "user", "content": ROUTER_USER.format(pending=pend, history=hist, message=message)},
    ]
    with trace.span("router", {"message": message, "pending": pending}) as sp:
        raw = llm.chat_json(messages, trace=trace, component="router")
        intent = raw.get("intent") if raw.get("intent") in INTENTS else "out_of_scope"
        topic = raw.get("topic") if raw.get("topic") in TOPICS else None
        slots, dropped = normalize_slots(raw.get("slots") or {})
        res = RouterResult(intent, topic, slots, str(raw.get("standalone_question") or message), dropped)
        sp.output = {"intent": res.intent, "topic": res.topic, "slots": res.slots,
                     "standalone_question": res.standalone_question, "dropped_slots": dropped}
    return res


def required_slots(intent: str, topic: str | None) -> list[str]:
    if intent == "policy_question":
        return list(TOPICS.get(topic or "other", TOPICS["other"])["required_slots"])
    if intent == "leave_request":
        return list(LEAVE_REQUEST_SLOTS)
    return []


def compute_missing(intent: str, topic: str | None, slots: dict, profile: dict) -> list[str]:
    """Required slots not provided by the user and not known from the employee profile."""
    return [s for s in required_slots(intent, topic) if s not in slots and s not in profile]
