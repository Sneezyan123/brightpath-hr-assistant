"""Domain schema: topics → policy documents and the slots needed to answer them."""
from __future__ import annotations

# required_slots: information the answer depends on. Profile slots are auto-filled for logged-in users.
TOPICS: dict[str, dict] = {
    "annual_leave":          {"docs": ["annual_leave"], "required_slots": ["employment_type"]},
    "sick_leave":            {"docs": ["sick_leave"], "required_slots": []},
    "parental_leave":        {"docs": ["parental_leave"], "required_slots": ["caregiver_role", "employment_type"]},
    "bereavement_leave":     {"docs": ["other_leave"], "required_slots": ["relationship"]},
    "unpaid_leave":          {"docs": ["other_leave"], "required_slots": []},
    "study_leave":           {"docs": ["other_leave"], "required_slots": []},
    "public_holidays":       {"docs": ["other_leave"], "required_slots": ["location"]},
    "onboarding":            {"docs": ["onboarding"], "required_slots": []},
    "health_insurance":      {"docs": ["benefits"], "required_slots": ["location", "employment_type"]},
    "pension":               {"docs": ["benefits"], "required_slots": ["location"]},
    "wellness_learning":     {"docs": ["benefits"], "required_slots": []},
    "remote_work":           {"docs": ["remote_work"], "required_slots": []},
    "work_abroad":           {"docs": ["remote_work"], "required_slots": ["duration_weeks"]},
    "expenses_travel":       {"docs": ["expenses"], "required_slots": []},
    "equipment_it":          {"docs": ["expenses"], "required_slots": []},
    "payroll_personal_data": {"docs": ["expenses"], "required_slots": []},
    "other":                 {"docs": [], "required_slots": []},
}

PROFILE_SLOTS = ("employment_type", "location")
LEAVE_REQUEST_SLOTS = ("leave_type", "start_date", "end_date")

SLOT_VALUES: dict[str, set[str] | None] = {
    "employment_type": {"full_time", "part_time", "contractor"},
    "location": {"riga", "lisbon"},
    "caregiver_role": {"primary", "secondary"},
    "relationship": {"immediate_family", "other_relative"},
    "leave_type": {"annual", "sick", "parental", "bereavement", "unpaid", "study"},
    "decision": {"approve", "decline"},
    "duration_weeks": None,   # positive int
    "start_date": None,       # ISO date
    "end_date": None,
    "request_id": None,       # positive int
}

SLOT_QUESTIONS: dict[str, str] = {
    "employment_type": "Are you employed full-time, part-time, or as a contractor?",
    "location": "Which office are you based in: Riga or Lisbon?",
    "caregiver_role": "Will you be the primary or the secondary caregiver?",
    "relationship": "Was the person an immediate family member (partner, child, parent, sibling) or another relative?",
    "duration_weeks": "For how many weeks would you like to work from abroad, and from which country?",
    "leave_type": "Which type of leave is it: annual, sick, parental, bereavement, unpaid, or study?",
    "start_date": "What is the first day of the leave?",
    "end_date": "What is the last day of the leave?",
}

INTENTS = ("policy_question", "leave_request", "leave_status", "leave_cancel", "leave_manage",
           "provide_info", "sensitive", "greeting", "out_of_scope")
LEAVE_INTENTS = {"leave_request", "leave_status", "leave_cancel", "leave_manage"}
