"""SQLite storage: employees, leave entitlements, leave requests, HR tickets.

Leave tables are read and written only by hr_assistant.tools (AGENTS.md §4).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL, employment_type TEXT NOT NULL,
  fte REAL NOT NULL, start_date TEXT NOT NULL, role TEXT NOT NULL, manager_id TEXT
);
CREATE TABLE IF NOT EXISTS leave_taken (
  employee_id TEXT NOT NULL, leave_type TEXT NOT NULL, year INTEGER NOT NULL, days REAL NOT NULL,
  PRIMARY KEY (employee_id, leave_type, year)
);
CREATE TABLE IF NOT EXISTS leave_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, leave_type TEXT NOT NULL,
  start_date TEXT NOT NULL, end_date TEXT NOT NULL, working_days REAL NOT NULL, status TEXT NOT NULL,
  reason TEXT, decision_reason TEXT, decided_by TEXT, created_at TEXT NOT NULL, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS holidays (location TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY (location, day));
CREATE TABLE IF NOT EXISTS hr_tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT, category TEXT NOT NULL, question TEXT NOT NULL,
  reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path, seed_path: Path, reset: bool = False) -> None:
    """Create the schema and load seed data (idempotent unless reset=True)."""
    if reset and path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    conn = connect(path)
    conn.executescript(SCHEMA)
    if fresh:
        _seed(conn, json.loads(seed_path.read_text(encoding="utf-8")))
    conn.commit()
    conn.close()


def _seed(conn: sqlite3.Connection, seed: dict) -> None:
    # Imported here to avoid a cycle: leave_rules needs nothing from db, db needs working_days.
    from .leave_rules import working_days

    for e in seed["employees"]:
        conn.execute("INSERT INTO employees VALUES (:id,:name,:location,:employment_type,:fte,:start_date,:role,:manager_id)", e)
    for emp_id, taken in seed["entitlements_taken_2026"].items():
        if emp_id.startswith("_"):
            continue
        for lt, days in taken.items():
            conn.execute("INSERT INTO leave_taken VALUES (?,?,?,?)", (emp_id, lt, 2026, days))
    for loc, days in seed["holidays"].items():
        conn.executemany("INSERT INTO holidays VALUES (?,?)", [(loc, d) for d in days])
    for r in seed["requests"]:
        loc = conn.execute("SELECT location FROM employees WHERE id=?", (r["employee_id"],)).fetchone()[0]
        hol = {date.fromisoformat(d) for d in seed["holidays"][loc]}
        wd = working_days(date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"]), hol)
        conn.execute(
            "INSERT INTO leave_requests (employee_id, leave_type, start_date, end_date, working_days, status, reason,"
            " decision_reason, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (r["employee_id"], r["leave_type"], r["start_date"], r["end_date"], wd, r["status"], r.get("reason"),
             r.get("decision_reason"), "2026-11-01T09:00:00"),
        )


# ---- read helpers used by the orchestrator (no leave data here) ---------------
def get_employee(conn: sqlite3.Connection, employee_id: str | None) -> dict | None:
    if not employee_id:
        return None
    row = conn.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    return dict(row) if row else None


def list_employees(conn: sqlite3.Connection) -> list[dict]:
    """Public directory for the login picker: id, name, role, office (no leave data)."""
    rows = conn.execute("SELECT id, name, role, location, employment_type FROM employees ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def holidays_for(conn: sqlite3.Connection, location: str) -> set[date]:
    return {date.fromisoformat(r[0]) for r in conn.execute("SELECT day FROM holidays WHERE location=?", (location,))}


def create_ticket(conn: sqlite3.Connection, employee_id: str | None, category: str, question: str, reason: str) -> int:
    cur = conn.execute(
        "INSERT INTO hr_tickets (employee_id, category, question, reason, created_at) VALUES (?,?,?,?,?)",
        (employee_id, category, question, reason, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return int(cur.lastrowid)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Initialise the HR database")
    p.add_argument("--reset", action="store_true", help="delete and re-seed the database")
    args = p.parse_args()
    s = get_settings()
    init_db(s.db_path, s.seed_path, reset=args.reset)
    print(f"Database ready at {s.db_path}")
