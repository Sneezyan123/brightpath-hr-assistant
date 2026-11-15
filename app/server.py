"""FastAPI web server. Exposes chat only; leave data is never served directly (AGENTS.md §4)."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hr_assistant import db
from hr_assistant.agent import HRAssistant, Session
from hr_assistant.config import get_settings
from hr_assistant.llm import LLMClient

STATIC = Path(__file__).parent / "static"
settings = get_settings()
assistant = HRAssistant(LLMClient(settings), settings)
sessions: dict[str, Session] = {}

app = FastAPI(title="Brightpath HR Assistant")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatIn(BaseModel):
    session_id: str | None = None
    employee_id: str | None = None
    message: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/employees")
def employees() -> dict:
    """Login picker: names and roles only."""
    return {"employees": db.list_employees(assistant.conn), "today": assistant.today.isoformat(),
            "model": settings.model, "provider": settings.provider, "llm_ready": bool(settings.api_key)}


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    if not body.message.strip():
        raise HTTPException(400, "empty message")
    sess = sessions.get(body.session_id or "")
    if sess is None or sess.employee_id != body.employee_id:
        sess = Session(employee_id=body.employee_id or None)
        sessions[sess.session_id] = sess
    resp = assistant.handle(sess, body.message.strip())
    return {"session_id": sess.session_id, **asdict(resp)}
