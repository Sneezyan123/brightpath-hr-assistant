"""Leave-management tools exposed to the LLM. The ONLY code path that touches leave data.

Security: the acting employee is bound in LeaveTools.__init__ from the session, never from tool arguments.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

from . import db
from .leave_rules import LEAVE_TYPES, evaluate, yearly_entitlement

TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "get_leave_balance",
        "description": "Get the current employee's remaining leave days for a year (all leave types, or one).",
        "parameters": {"type": "object", "properties": {
            "leave_type": {"type": "string", "enum": list(LEAVE_TYPES)},
            "year": {"type": "integer", "description": "Calendar year; defaults to the current year."}}}}},
    {"type": "function", "function": {
        "name": "create_leave_request",
        "description": "Submit a leave request for the current employee. Company rules decide immediately: "
                       "approved, declined (with reason) or pending manager approval.",
        "parameters": {"type": "object", "required": ["leave_type", "start_date", "end_date"], "properties": {
            "leave_type": {"type": "string", "enum": list(LEAVE_TYPES)},
            "start_date": {"type": "string", "description": "First day, YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "Last day (inclusive), YYYY-MM-DD"},
            "reason": {"type": "string", "description": "Optional short reason"}}}}},
    {"type": "function", "function": {
        "name": "list_my_leave_requests",
        "description": "List the current employee's leave requests, optionally filtered by status.",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["pending", "approved", "declined", "cancelled"]}}}}},
    {"type": "function", "function": {
        "name": "cancel_leave_request",
        "description": "Cancel one of the current employee's pending or approved requests that has not started.",
        "parameters": {"type": "object", "required": ["request_id"], "properties": {
            "request_id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "list_pending_approvals",
        "description": "Managers/HR only: list pending leave requests from the current user's team.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "decide_leave_request",
        "description": "Managers/HR only: approve or decline a pending leave request of a team member.",
        "parameters": {"type": "object", "required": ["request_id", "decision"], "properties": {
            "request_id": {"type": "integer"},
            "decision": {"type": "string", "enum": ["approve", "decline"]},
            "comment": {"type": "string"}}}}},
]


class ToolError(ValueError):
    """Invalid tool arguments or a forbidden action; returned to the LLM as an error message."""


class LeaveTools:
    def __init__(self, conn: sqlite3.Connection, employee: dict, today: date) -> None:
        self.conn = conn
        self.employee = employee
        self.today = today

    def execute(self, name: str, args: dict) -> dict:
        fn = getattr(self, name, None)
        if name not in {t["function"]["name"] for t in TOOL_SCHEMAS} or fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except ToolError as exc:
            return {"error": str(exc)}
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}

    # ---- helpers ------------------------------------------------------------
    @staticmethod
    def _date(value: str, field: str) -> date:
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise ToolError(f"{field} must be YYYY-MM-DD, got {value!r}") from exc

    def _used(self, emp_id: str, leave_type: str, year: int) -> float:
        taken = self.conn.execute("SELECT days FROM leave_taken WHERE employee_id=? AND leave_type=? AND year=?",
                                  (emp_id, leave_type, year)).fetchone()
        booked = self.conn.execute(
            "SELECT COALESCE(SUM(working_days),0) FROM leave_requests WHERE employee_id=? AND leave_type=? "
            "AND status IN ('pending','approved') AND substr(start_date,1,4)=?", (emp_id, leave_type, str(year))
        ).fetchone()[0]
        return (taken[0] if taken else 0) + booked

    def _available(self, emp: dict, leave_type: str, year: int) -> float | None:
        ent = yearly_entitlement(emp, leave_type)
        return None if ent is None else round(ent - self._used(emp["id"], leave_type, year), 1)

    def _can_manage(self, target_emp_id: str) -> bool:
        if self.employee["role"] == "hr":
            return True
        target = db.get_employee(self.conn, target_emp_id)
        return self.employee["role"] == "manager" and target is not None and target["manager_id"] == self.employee["id"]

    # ---- tools ----------------------------------------------------------------
    def get_leave_balance(self, leave_type: str | None = None, year: int | None = None) -> dict:
        year = int(year or self.today.year)
        types = [leave_type] if leave_type else [t for t in LEAVE_TYPES if yearly_entitlement(self.employee, t) is not None]
        out = {}
        for t in types:
            if t not in LEAVE_TYPES:
                raise ToolError(f"unknown leave type {t}")
            ent = yearly_entitlement(self.employee, t)
            out[t] = ({"entitled": ent, "used_or_booked": self._used(self.employee["id"], t, year),
                       "available": self._available(self.employee, t, year)} if ent is not None
                      else {"note": "per-event entitlement, no yearly balance"})
        return {"employee": self.employee["name"], "year": year, "balances": out}

    def create_leave_request(self, leave_type: str, start_date: str, end_date: str, reason: str | None = None) -> dict:
        start, end = self._date(start_date, "start_date"), self._date(end_date, "end_date")
        emp = self.employee
        hol = db.holidays_for(self.conn, emp["location"])
        overlaps = self.conn.execute(
            "SELECT COUNT(*) FROM leave_requests WHERE employee_id=? AND status IN ('pending','approved') "
            "AND start_date<=? AND end_date>=?", (emp["id"], end.isoformat(), start.isoformat())).fetchone()[0] > 0
        available = self._available(emp, leave_type, start.year) if leave_type in LEAVE_TYPES else None
        d = evaluate(emp, leave_type, start, end, self.today, hol, available, overlaps)
        cur = self.conn.execute(
            "INSERT INTO leave_requests (employee_id, leave_type, start_date, end_date, working_days, status, reason,"
            " decision_reason, decided_by, created_at, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (emp["id"], leave_type, start.isoformat(), end.isoformat(), d.working_days, d.status, reason,
             d.reason, None if d.status == "pending" else "rules", datetime.now().isoformat(timespec="seconds"),
             None if d.status == "pending" else datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()
        return {"request_id": cur.lastrowid, "status": d.status, "reason": d.reason, "working_days": d.working_days,
                "notes": d.notes, "leave_type": leave_type, "start_date": start.isoformat(), "end_date": end.isoformat()}

    def list_my_leave_requests(self, status: str | None = None) -> dict:
        q, params = "SELECT * FROM leave_requests WHERE employee_id=?", [self.employee["id"]]
        if status:
            q += " AND status=?"
            params.append(status)
        rows = self.conn.execute(q + " ORDER BY start_date", params).fetchall()
        return {"requests": [{k: r[k] for k in ("id", "leave_type", "start_date", "end_date", "working_days",
                                                 "status", "decision_reason")} for r in rows]}

    def cancel_leave_request(self, request_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM leave_requests WHERE id=?", (int(request_id),)).fetchone()
        if row is None or row["employee_id"] != self.employee["id"]:
            raise ToolError(f"request {request_id} not found among your requests")
        if row["status"] not in ("pending", "approved"):
            raise ToolError(f"request {request_id} is {row['status']} and cannot be cancelled")
        if date.fromisoformat(row["start_date"]) <= self.today:
            raise ToolError("leave that has already started cannot be cancelled here; contact HR")
        self.conn.execute("UPDATE leave_requests SET status='cancelled', decided_at=? WHERE id=?",
                          (datetime.now().isoformat(timespec="seconds"), int(request_id)))
        self.conn.commit()
        return {"request_id": int(request_id), "status": "cancelled", "days_returned": row["working_days"]}

    def list_pending_approvals(self) -> dict:
        if self.employee["role"] not in ("manager", "hr"):
            raise ToolError("only managers and HR can see pending approvals")
        rows = self.conn.execute(
            "SELECT r.*, e.name FROM leave_requests r JOIN employees e ON e.id=r.employee_id "
            "WHERE r.status='pending' ORDER BY r.start_date").fetchall()
        rows = [r for r in rows if self._can_manage(r["employee_id"])]
        return {"pending": [{"request_id": r["id"], "employee": r["name"], "leave_type": r["leave_type"],
                             "start_date": r["start_date"], "end_date": r["end_date"],
                             "working_days": r["working_days"], "reason": r["reason"]} for r in rows]}

    def decide_leave_request(self, request_id: int, decision: str, comment: str | None = None) -> dict:
        if decision not in ("approve", "decline"):
            raise ToolError("decision must be 'approve' or 'decline'")
        row = self.conn.execute("SELECT * FROM leave_requests WHERE id=?", (int(request_id),)).fetchone()
        if row is None:
            raise ToolError(f"request {request_id} not found")
        if not self._can_manage(row["employee_id"]):
            raise ToolError("you are not allowed to decide on this request (not your team member)")
        if row["status"] != "pending":
            raise ToolError(f"request {request_id} is already {row['status']}")
        status = "approved" if decision == "approve" else "declined"
        self.conn.execute("UPDATE leave_requests SET status=?, decision_reason=?, decided_by=?, decided_at=? WHERE id=?",
                          (status, comment or f"{status.capitalize()} by {self.employee['name']}", self.employee["id"],
                           datetime.now().isoformat(timespec="seconds"), int(request_id)))
        self.conn.commit()
        return {"request_id": int(request_id), "status": status}
