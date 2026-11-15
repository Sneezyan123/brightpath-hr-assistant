"""Lightweight request tracing: each component records a span (input, output, latency)."""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class Span:
    component: str
    input: Any
    output: Any = None
    latency_ms: float = 0.0
    meta: dict = field(default_factory=dict)


class Trace:
    """Collects spans for one user message; saved as one JSON line."""

    def __init__(self, session_id: str, message: str) -> None:
        self.request_id = uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.message = message
        self.started = time.time()
        self.spans: list[Span] = []

    @contextmanager
    def span(self, component: str, input: Any) -> Iterator[Span]:
        s = Span(component=component, input=input)
        t0 = time.perf_counter()
        try:
            yield s
        except Exception as exc:  # record, then re-raise
            s.meta["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            s.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            self.spans.append(s)

    def add(self, component: str, input: Any, output: Any, latency_ms: float = 0.0, **meta: Any) -> None:
        self.spans.append(Span(component, input, output, round(latency_ms, 1), meta))

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "message": self.message,
            "timestamp": self.started,
            "total_ms": round(sum(s.latency_ms for s in self.spans if s.component != "llm_call"), 1),
            "spans": [asdict(s) for s in self.spans],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict(), ensure_ascii=False, default=str) + "\n")
