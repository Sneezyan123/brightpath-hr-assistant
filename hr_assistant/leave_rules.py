"""Deterministic leave approval rules. Mirrors data/policies/*.md; change both together.

The LLM never decides approvals: it only calls create_leave_request, which runs evaluate().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

PROBATION_DAYS = 90
LEAVE_TYPES = ("annual", "sick", "parental", "bereavement", "unpaid", "study")

# Yearly entitlement in working days (annual is scaled by FTE). None = per-event limit, no yearly balance.
YEARLY_ENTITLEMENT = {"annual": 25, "sick": 15, "study": 5, "unpaid": 30, "parental": None, "bereavement": None}
PER_EVENT_MAX = {"parental": 80, "bereavement": 5}


@dataclass
class Decision:
    status: str              # approved | declined | pending
    reason: str
    working_days: float
    notes: list[str] = field(default_factory=list)


def working_days(start: date, end: date, holidays: set[date]) -> int:
    """Mon–Fri days in [start, end] that are not public holidays of the employee's office."""
    n, d = 0, start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            n += 1
        d += timedelta(days=1)
    return n


def yearly_entitlement(employee: dict, leave_type: str) -> float | None:
    base = YEARLY_ENTITLEMENT.get(leave_type)
    if base is None:
        return None
    if employee["employment_type"] == "contractor":
        return 0
    return round(base * employee["fte"], 1) if leave_type == "annual" else base


def in_probation(employee: dict, on: date) -> bool:
    return (on - date.fromisoformat(employee["start_date"])).days < PROBATION_DAYS


def evaluate(employee: dict, leave_type: str, start: date, end: date, today: date, holidays: set[date],
             available: float | None, overlaps: bool) -> Decision:
    """Apply company rules to a leave request and return the decision with a human-readable reason."""
    if leave_type not in LEAVE_TYPES:
        return Decision("declined", f"Unknown leave type '{leave_type}'.", 0)
    if end < start:
        return Decision("declined", "The end date is before the start date.", 0)
    wd = working_days(start, end, holidays)
    if wd == 0:
        return Decision("declined", "The selected period contains no working days (weekends/public holidays only).", 0)
    if employee["employment_type"] == "contractor":
        return Decision("declined", "Contractors are not covered by company leave policies; time off follows your "
                        "service agreement. Please contact your agency or contract owner.", wd)
    if overlaps:
        return Decision("declined", "The dates overlap with another pending or approved request.", wd)

    notice = (start - today).days
    if leave_type == "sick":
        if notice < -7:
            return Decision("declined", "Sick leave can be registered at most 7 calendar days after the first day.", wd)
        notes = ["A doctor's note is required from the 4th consecutive working day; upload it within 5 working "
                 "days."] if wd > 3 else []
        if available is not None and wd > available:
            notes.append("Paid sick days are used up; statutory sickness benefit applies to the remaining days.")
        return Decision("approved", "Auto-approved: sick leave registered within 7 days.", wd, notes)

    if notice < 0:
        return Decision("declined", "Leave cannot start in the past (except sick leave).", wd)

    if leave_type == "bereavement":
        if wd > PER_EVENT_MAX["bereavement"]:
            return Decision("declined", "Bereavement leave is at most 5 working days per event.", wd)
        return Decision("approved", "Auto-approved: bereavement leave needs no notice.", wd)

    if leave_type == "parental":
        if wd > PER_EVENT_MAX["parental"]:
            return Decision("declined", "Parental leave is at most 80 working days (16 weeks) for the primary caregiver.", wd)
        notes = [] if notice >= 56 else ["Parental leave should be requested at least 8 weeks in advance."]
        return Decision("pending", "Parental leave always needs manager approval.", wd, notes)

    probation = in_probation(employee, today)
    if leave_type in ("unpaid", "study") and probation:
        return Decision("declined", f"{leave_type.capitalize()} leave is available only after the 90-day probation period.", wd)
    if available is not None and wd > available:
        return Decision("declined", f"Not enough {leave_type} leave balance: requested {wd} working days, "
                        f"{available:g} available.", wd)

    if leave_type == "unpaid":
        if notice < 14:
            return Decision("declined", "Unpaid leave must be requested at least 14 calendar days in advance.", wd)
        return Decision("pending", "Unpaid leave always needs manager approval.", wd)
    if leave_type == "study":
        if notice < 14:
            return Decision("declined", "Study leave must be requested at least 14 calendar days in advance.", wd)
        return Decision("approved", "Auto-approved: study leave with enough notice and balance.", wd)

    # annual
    if wd > 3 and notice < 10:
        return Decision("declined", "Annual leave of more than 3 working days needs at least 10 calendar days' "
                        f"notice (you gave {notice}).", wd)
    if probation:
        return Decision("pending", "Annual leave during the probation period needs manager approval.", wd)
    if wd > 10:
        return Decision("pending", "Annual leave longer than 10 working days needs manager approval.", wd)
    return Decision("approved", "Auto-approved: enough balance and notice, no overlap.", wd)
