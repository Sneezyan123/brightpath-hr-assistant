"""Orchestrator: routes each message through intake → info collection → (retrieval → answer | leave agent) → referral."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import db
from .answerer import answer
from .config import Settings, get_settings, today as current_today
from .leave_agent import run_leave_agent
from .llm import ChatModel, LLMError
from .policies import load_chunks
from .retriever import BM25Retriever
from .router import compute_missing, route
from .tools import LeaveTools
from .topics import LEAVE_INTENTS, PROFILE_SLOTS, SLOT_QUESTIONS
from .trace import Trace

MAX_FOLLOW_UPS = 2
HR_CONTACT = "hr@brightpath.example"


@dataclass
class Session:
    employee_id: str | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    history: list[dict] = field(default_factory=list)
    pending: dict | None = None


@dataclass
class Response:
    reply: str
    intent: str
    topic: str | None = None
    slots: dict = field(default_factory=dict)
    asked_slots: list[str] = field(default_factory=list)
    retrieved: list[dict] = field(default_factory=list)   # [{id, score}]
    citations: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)     # [{id, label}] for the UI
    supported: bool | None = None
    referred: bool = False
    referral_reason: str | None = None
    ticket_id: int | None = None
    tool_calls: list[dict] = field(default_factory=list)
    question: str | None = None
    trace: dict = field(default_factory=dict)


class HRAssistant:
    def __init__(self, llm: ChatModel, settings: Settings | None = None, db_path: Path | None = None,
                 today: date | None = None, save_traces: bool = True) -> None:
        self.s = settings or get_settings()
        self.llm = llm
        db_path = db_path or self.s.db_path
        db.init_db(db_path, self.s.seed_path)
        self.conn = db.connect(db_path)
        self.chunks = load_chunks(self.s.policies_dir)
        self.chunk_by_id = {c.id: c for c in self.chunks}
        self.retriever = BM25Retriever(self.chunks)
        self._today = today
        self.save_traces = save_traces

    @property
    def today(self) -> date:
        return self._today or current_today(self.s)

    # ------------------------------------------------------------------
    def handle(self, session: Session, message: str) -> Response:
        trace = Trace(session.session_id, message)
        try:
            resp = self._handle(session, message, trace)
        except LLMError as exc:
            resp = Response(reply=f"Sorry, the language model is unavailable right now ({exc}).", intent="error")
        session.history.append({"role": "user", "content": message})
        session.history.append({"role": "assistant", "content": resp.reply})
        resp.trace = trace.to_dict()
        if self.save_traces:
            trace.save(self.s.trace_path)
        return resp

    def _handle(self, session: Session, message: str, trace: Trace) -> Response:
        employee = db.get_employee(self.conn, session.employee_id)
        profile = {k: employee[k] for k in PROFILE_SLOTS} if employee else {}

        r = route(self.llm, message, session.history, session.pending, self.today, trace)

        # ---- information collection: merge with a pending follow-up -------------
        with trace.span("info_collection", {"router": r.intent, "pending": session.pending}) as sp:
            intent, topic, slots, question = r.intent, r.topic, dict(r.slots), r.standalone_question
            p = session.pending
            if p and (intent == "provide_info" or (intent == p["intent"] and topic in (None, p["topic"]))):
                intent, topic = p["intent"], p["topic"] or topic
                slots = {**p["slots"], **slots}
                # The router is asked to merge the follow-up into one question; if it just echoed the
                # user's short answer, keep the original pending question for retrieval.
                echoed = r.standalone_question.strip().lower() == message.strip().lower()
                question = p["question"] if echoed else r.standalone_question
                attempts = p["attempts"] + 1
            else:
                attempts = 1
                if intent == "provide_info":  # nothing pending: treat as a question
                    intent = "policy_question"
            session.pending = None
            missing = compute_missing(intent, topic, slots, profile)
            if missing and intent == "leave_request" and not employee:
                missing = []  # handled below: guest must pick a profile first
            proceed_anyway = bool(missing) and attempts > MAX_FOLLOW_UPS
            sp.output = {"intent": intent, "topic": topic, "slots": slots, "profile": profile,
                         "missing": missing, "attempt": attempts, "proceed_anyway": proceed_anyway}

        base = dict(intent=intent, topic=topic, slots=slots, question=question)
        if missing and not proceed_anyway:
            session.pending = {"intent": intent, "topic": topic, "question": question, "slots": slots,
                               "asked": missing, "attempts": attempts}
            asks = [SLOT_QUESTIONS[m] for m in missing]
            reply = ("To answer that accurately I need a bit more information:\n- " + "\n- ".join(asks)) \
                if intent == "policy_question" else ("Sure. " + " ".join(asks))
            return Response(reply=reply, asked_slots=missing, **base)

        if intent == "greeting":
            return Response(reply="Hi! I can answer questions about Brightpath HR policies (leave, benefits, onboarding, "
                                  "remote work, expenses) and help you book or check leave.", **base)
        if intent == "out_of_scope":
            return Response(reply="I can only help with HR topics at Brightpath, such as leave, benefits, onboarding, "
                                  "remote work, expenses, and leave requests.", **base)
        if intent == "sensitive":
            return self._refer(base, employee, message, "sensitive", "Sensitive topic: needs a human HR partner.", trace)
        if intent in LEAVE_INTENTS:
            if not employee:
                return Response(reply="To manage leave I need to know who you are. Please pick your employee profile at "
                                      "the top of the page and send the request again.", **base)
            agent = run_leave_agent(self.llm, LeaveTools(self.conn, employee, self.today), message, slots,
                                    session.history, self.today, trace)
            return Response(reply=agent.reply, tool_calls=agent.tool_calls, **base)

        return self._policy_answer(base, employee, profile, slots, question, trace)

    # ------------------------------------------------------------------
    def _policy_answer(self, base: dict, employee: dict | None, profile: dict, slots: dict, question: str,
                       trace: Trace) -> Response:
        slot_text = " ".join(str(v).replace("_", " ") for k, v in slots.items() if k not in ("start_date", "end_date"))
        query = f"{question} {slot_text}".strip()
        with trace.span("retrieval", {"query": query, "topic": base["topic"]}) as sp:
            hits = self.retriever.search(query, k=self.s.retrieval_k, topic=base["topic"])
            sp.output = [{"id": h.chunk.id, "score": h.score} for h in hits]
        retrieved = [{"id": h.chunk.id, "score": h.score} for h in hits]

        top = hits[0].score if hits else 0.0
        with trace.span("referral_gate", {"top_score": top, "threshold": self.s.referral_min_score}) as sp:
            gate_pass = top >= self.s.referral_min_score
            sp.output = {"pass": gate_pass}
        if not gate_pass:
            return self._refer(base, employee, question, "no_policy", f"No policy section matched (top BM25 score {top}).",
                               trace, retrieved=retrieved)

        details = {**profile, **{k: v for k, v in slots.items() if k not in ("start_date", "end_date")}}
        if employee:
            details["fte"] = employee["fte"]
        ans = answer(self.llm, question, hits, details, trace)
        if not ans.supported:
            return self._refer(base, employee, question, "unsupported", "Retrieved policies do not support an answer.",
                               trace, retrieved=retrieved, supported=False)
        sources = [{"id": c, "label": self.chunk_by_id[c].label} for c in ans.citations]
        reply = ans.answer + "\n\nSources: " + "; ".join(s["label"] for s in sources)
        return Response(reply=reply, retrieved=retrieved, citations=ans.citations, sources=sources, supported=True, **base)

    def _refer(self, base: dict, employee: dict | None, question: str, category: str, reason: str, trace: Trace,
               retrieved: list[dict] | None = None, supported: bool | None = None) -> Response:
        with trace.span("hr_referral", {"category": category, "reason": reason}) as sp:
            ticket = db.create_ticket(self.conn, employee["id"] if employee else None, category, question, reason)
            sp.output = {"ticket_id": ticket}
        if category == "sensitive":
            reply = (f"I'm sorry you're dealing with this. This needs a person, not an assistant, so I've opened a "
                     f"confidential ticket (HR-{ticket}) and an HR partner will contact you within 1 working day. "
                     f"You can also email {HR_CONTACT} directly.")
        else:
            reply = (f"I couldn't find a Brightpath policy that answers this, so I've forwarded your question to the HR "
                     f"team (ticket HR-{ticket}). They usually reply within 2 working days, or email {HR_CONTACT}.")
        return Response(reply=reply, referred=True, referral_reason=category, ticket_id=ticket,
                        retrieved=retrieved or [], supported=supported, **base)
