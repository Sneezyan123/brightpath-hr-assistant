from __future__ import annotations

from datetime import date

from hr_assistant import db
from hr_assistant.leave_rules import working_days
from hr_assistant.llm import parse_json
from hr_assistant.policies import load_chunks, parse_policy
from hr_assistant.retriever import BM25Retriever
from hr_assistant.router import compute_missing, normalize_slots
from hr_assistant.tools import LeaveTools

from .conftest import TODAY


def test_policy_chunking(settings):
    chunks = load_chunks(settings.policies_dir)
    ids = {c.id for c in chunks}
    assert {"annual_leave#2", "sick_leave#3", "benefits#7", "expenses#6"} <= ids
    assert all(c.text for c in chunks)


def test_parse_policy_requires_title():
    try:
        parse_policy("x", "no title")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_retrieval_finds_synonym_query(settings):
    r = BM25Retriever(load_chunks(settings.policies_dir))
    assert r.search("how many vacation days do I get", k=3)[0].chunk.id == "annual_leave#2"
    assert "sick_leave#3" in [h.chunk.id for h in r.search("when do I need a doctor's note", k=3)]


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}


def test_anthropic_payload_conversion():
    from hr_assistant.llm import _to_anthropic_payload
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "book"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "a", "arguments": "{\"x\": 1}"}},
                {"id": "t2", "type": "function", "function": {"name": "b", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t1", "name": "a", "content": "{}"},
            {"role": "tool", "tool_call_id": "t2", "name": "b", "content": "{}"}]
    tools = [{"type": "function", "function": {"name": "a", "description": "d", "parameters": {"type": "object"}}}]
    p = _to_anthropic_payload("claude-haiku-5-5", msgs, tools, 100)
    assert p["system"] == "sys" and "temperature" not in p
    assert [m["role"] for m in p["messages"]] == ["user", "assistant", "user"]
    assert p["messages"][1]["content"][0] == {"type": "tool_use", "id": "t1", "name": "a", "input": {"x": 1}}
    assert len(p["messages"][2]["content"]) == 2   # both tool results in ONE user message
    assert p["tools"][0]["input_schema"] == {"type": "object"}


def test_slot_normalisation_and_missing():
    clean, dropped = normalize_slots({"employment_type": "Full-time", "location": "Paris", "start_date": "2026-12-01",
                                      "duration_weeks": "3", "foo": "bar"})
    assert clean == {"employment_type": "full_time", "start_date": "2026-12-01", "duration_weeks": 3}
    assert set(dropped) == {"location", "foo"}
    assert compute_missing("policy_question", "parental_leave", {}, {}) == ["caregiver_role", "employment_type"]
    assert compute_missing("policy_question", "parental_leave", {"caregiver_role": "primary"},
                           {"employment_type": "full_time"}) == []
    assert compute_missing("leave_request", None, {"leave_type": "annual"}, {}) == ["start_date", "end_date"]


def test_working_days_skip_weekend_and_holiday():
    hol = {date(2026, 11, 18)}
    assert working_days(date(2026, 11, 16), date(2026, 11, 22), hol) == 4


def test_balance_and_rules(conn):
    emp = db.get_employee(conn, "E001")
    t = LeaveTools(conn, emp, TODAY)
    assert t.get_leave_balance("annual")["balances"]["annual"]["available"] == 13
    assert t.create_leave_request("annual", "2026-12-07", "2026-12-11")["status"] == "approved"
    assert t.create_leave_request("annual", "2026-11-23", "2026-11-27")["status"] == "declined"   # notice
    assert t.create_leave_request("annual", "2026-12-08", "2026-12-08")["status"] == "declined"   # overlap
    assert t.get_leave_balance("annual")["balances"]["annual"]["available"] == 8


def test_probation_contractor_and_parental(conn):
    e2 = LeaveTools(conn, db.get_employee(conn, "E002"), TODAY)
    assert e2.create_leave_request("annual", "2026-12-04", "2026-12-04")["status"] == "pending"
    assert e2.create_leave_request("study", "2026-12-14", "2026-12-14")["status"] == "declined"
    e4 = LeaveTools(conn, db.get_employee(conn, "E004"), TODAY)
    assert e4.create_leave_request("annual", "2026-12-07", "2026-12-11")["status"] == "declined"
    e1 = LeaveTools(conn, db.get_employee(conn, "E001"), TODAY)
    assert e1.create_leave_request("parental", "2027-02-01", "2027-02-26")["status"] == "pending"


def test_permissions(conn):
    e1 = LeaveTools(conn, db.get_employee(conn, "E001"), TODAY)
    assert "error" in e1.execute("decide_leave_request", {"request_id": 1, "decision": "approve"})
    assert "error" in e1.execute("list_pending_approvals", {})
    assert "error" in e1.execute("cancel_leave_request", {"request_id": 1})            # not E001's request
    m2 = LeaveTools(conn, db.get_employee(conn, "M002"), TODAY)
    assert "error" in m2.execute("decide_leave_request", {"request_id": 1, "decision": "approve"})  # other team
    m1 = LeaveTools(conn, db.get_employee(conn, "M001"), TODAY)
    assert [p["request_id"] for p in m1.execute("list_pending_approvals", {})["pending"]] == [1]
    assert m1.execute("decide_leave_request", {"request_id": 1, "decision": "approve"})["status"] == "approved"
    assert e1.execute("cancel_leave_request", {"request_id": 2})["status"] == "cancelled"
