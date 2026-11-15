"""Grounded answer generation with citation validation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .llm import ChatModel
from .prompts import ANSWER_SYSTEM, ANSWER_USER
from .retriever import Hit
from .trace import Trace

_CITE = re.compile(r"\[([a-z_]+#\d+)\]")


@dataclass
class AnswerResult:
    answer: str
    citations: list[str]
    supported: bool
    invalid_citations: list[str] = field(default_factory=list)


def format_context(hits: list[Hit]) -> str:
    return "\n\n".join(f"[{h.chunk.id}] {h.chunk.doc_title}, §{h.chunk.section_no} {h.chunk.section_title}\n{h.chunk.text}"
                       for h in hits)


def answer(llm: ChatModel, question: str, hits: list[Hit], details: dict, trace: Trace) -> AnswerResult:
    messages = [
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": ANSWER_USER.format(details=json.dumps(details) if details else "unknown",
                                                       question=question, context=format_context(hits))},
    ]
    with trace.span("answer", {"question": question, "chunks": [h.chunk.id for h in hits], "details": details}) as sp:
        raw = llm.chat_json(messages, trace=trace, component="answer")
        text = str(raw.get("answer") or "").strip()
        retrieved = {h.chunk.id for h in hits}
        cited = [str(c).strip("[] ") for c in (raw.get("citations") or [])]
        cited += [c for c in _CITE.findall(text) if c not in cited]
        valid = [c for c in dict.fromkeys(cited) if c in retrieved]
        invalid = [c for c in cited if c not in retrieved]
        supported = bool(raw.get("supported", True)) and bool(text) and bool(valid)
        text = _CITE.sub("", text).replace("  ", " ").strip()
        res = AnswerResult(text, valid, supported, invalid)
        sp.output = {"answer": text, "citations": valid, "invalid_citations": invalid,
                     "model_supported": raw.get("supported"), "supported": supported}
    return res
