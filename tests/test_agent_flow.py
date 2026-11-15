"""End-to-end orchestration with a scripted LLM: no network."""
from __future__ import annotations

from hr_assistant.agent import HRAssistant, Session

from .conftest import TODAY, ScriptedLLM


def make(tmp_path, settings, script):
    return HRAssistant(ScriptedLLM(script), settings, db_path=tmp_path / "a.db", today=TODAY, save_traces=False)


def test_policy_answer_with_citation(tmp_path, settings):
    a = make(tmp_path, settings, {
        "router": [{"intent": "policy_question", "topic": "annual_leave", "slots": {},
                    "standalone_question": "How many vacation days do I get per year?"}],
        "answer": [{"answer": "You get 25 working days per year [annual_leave#2].", "citations": ["annual_leave#2"],
                    "supported": True}],
    })
    r = a.handle(Session("E001"), "How many vacation days do I get?")
    assert r.citations == ["annual_leave#2"] and not r.referred
    assert "25 working days" in r.reply and "Annual Leave Policy" in r.reply
    assert [s["component"] for s in r.trace["spans"]][:2] == ["router", "info_collection"]


def test_follow_up_then_answer(tmp_path, settings):
    a = make(tmp_path, settings, {
        "router": [
            {"intent": "policy_question", "topic": "parental_leave", "slots": {}, "standalone_question": "How much parental leave can I take?"},
            {"intent": "provide_info", "topic": "parental_leave", "slots": {"caregiver_role": "primary", "employment_type": "full_time"},
             "standalone_question": "How much parental leave does a full-time primary caregiver get?"},
        ],
        "answer": [{"answer": "16 weeks fully paid.", "citations": ["parental_leave#2"], "supported": True}],
    })
    s = Session(None)
    r1 = a.handle(s, "How much parental leave can I take?")
    assert r1.asked_slots == ["caregiver_role", "employment_type"] and s.pending
    r2 = a.handle(s, "Primary, full-time")
    assert r2.intent == "policy_question" and r2.citations == ["parental_leave#2"] and s.pending is None


def test_unsupported_and_invalid_citation_refer(tmp_path, settings):
    a = make(tmp_path, settings, {
        "router": [{"intent": "policy_question", "topic": "payroll_personal_data", "slots": {},
                    "standalone_question": "What is the salary band for a senior analyst?"}] * 2,
        "answer": [{"answer": "", "citations": [], "supported": False},
                   {"answer": "Bands are 50-70k [made_up#1].", "citations": ["made_up#1"], "supported": True}],
    })
    r1 = a.handle(Session("E001"), "salary band?")
    r2 = a.handle(Session("E001"), "salary band?")
    assert r1.referred and r1.ticket_id and r2.referred and r2.referral_reason == "unsupported"


def test_sensitive_goes_to_hr(tmp_path, settings):
    a = make(tmp_path, settings, {"router": [{"intent": "sensitive", "topic": None, "slots": {}, "standalone_question": "x"}]})
    r = a.handle(Session("E003"), "My lead harasses me")
    assert r.referred and r.referral_reason == "sensitive"


def test_leave_request_via_tools(tmp_path, settings):
    a = make(tmp_path, settings, {
        "router": [{"intent": "leave_request", "topic": "annual_leave",
                    "slots": {"leave_type": "annual", "start_date": "2026-12-07", "end_date": "2026-12-11"},
                    "standalone_question": "Book annual leave 7-11 Dec"}],
        "leave_agent": [{"calls": [("create_leave_request", {"leave_type": "annual", "start_date": "2026-12-07",
                                                             "end_date": "2026-12-11"})]},
                        "Done: request #4 is approved."],
    })
    r = a.handle(Session("E001"), "Book annual leave from 7 to 11 December")
    assert r.tool_calls[0]["result"]["status"] == "approved"
    row = a.conn.execute("SELECT status FROM leave_requests WHERE id=4").fetchone()
    assert row[0] == "approved"


def test_guest_cannot_manage_leave(tmp_path, settings):
    a = make(tmp_path, settings, {"router": [{"intent": "leave_status", "topic": None, "slots": {}, "standalone_question": "x"}]})
    r = a.handle(Session(None), "What's my balance?")
    assert not r.tool_calls and "profile" in r.reply
