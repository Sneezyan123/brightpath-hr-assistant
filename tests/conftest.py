from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from hr_assistant import db
from hr_assistant.config import get_settings

TODAY = date(2026, 11, 16)


class ScriptedLLM:
    """Fake LLM: returns queued responses per component ('router', 'answer', 'leave_agent')."""

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.seen: list[tuple[str, list]] = []

    def _next(self, component: str):
        self.seen.append((component, []))
        return self.script[component].pop(0)

    def chat_json(self, messages, *, trace=None, component="llm", max_tokens=1200) -> dict:
        return self._next(component)

    def chat(self, messages, *, tools=None, max_tokens=1200, trace=None, component="llm") -> dict:
        r = self._next(component)
        if isinstance(r, str):
            return {"content": r, "tool_calls": []}
        calls = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                 for i, (n, a) in enumerate(r["calls"])]
        return {"content": "", "tool_calls": calls}


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def conn(tmp_path: Path, settings):
    p = tmp_path / "t.db"
    db.init_db(p, settings.seed_path)
    c = db.connect(p)
    yield c
    c.close()
