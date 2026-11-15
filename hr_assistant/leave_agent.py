"""LLM tool-calling loop for leave management. All DB access goes through tools.LeaveTools."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from .llm import ChatModel
from .prompts import LEAVE_AGENT_SYSTEM, LEAVE_AGENT_USER
from .tools import TOOL_SCHEMAS, LeaveTools
from .trace import Trace

MAX_STEPS = 5


@dataclass
class AgentResult:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)  # [{name, args, result}]


def run_leave_agent(llm: ChatModel, tools: LeaveTools, message: str, slots: dict, history: list[dict],
                    today: date, trace: Trace) -> AgentResult:
    emp = tools.employee
    messages: list[dict] = [{"role": "system", "content": LEAVE_AGENT_SYSTEM.format(
        name=emp["name"], employee_id=emp["id"], role=emp["role"], location=emp["location"].capitalize(),
        today=today.isoformat(), weekday=today.strftime("%A"))}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history[-4:]]
    messages.append({"role": "user", "content": LEAVE_AGENT_USER.format(message=message, slots=json.dumps(slots))})

    calls: list[dict] = []
    with trace.span("leave_agent", {"message": message, "slots": slots}) as sp:
        reply = ""
        for _ in range(MAX_STEPS):
            res = llm.chat(messages, tools=TOOL_SCHEMAS, trace=trace, component="leave_agent")
            if not res["tool_calls"]:
                reply = res["content"].strip()
                break
            messages.append({"role": "assistant", "content": res["content"] or "", "tool_calls": res["tool_calls"]})
            for tc in res["tool_calls"]:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args, result = {}, {"error": "arguments were not valid JSON"}
                else:
                    with trace.span(f"tool:{name}", args) as tsp:
                        result = tools.execute(name, args)
                        tsp.output = result
                calls.append({"name": name, "args": args, "result": result})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", name), "name": name,
                                 "content": json.dumps(result, default=str)})
        if not reply:
            reply = _fallback_reply(calls)
        sp.output = {"reply": reply, "tool_calls": [c["name"] for c in calls]}
    return AgentResult(reply, calls)


def _fallback_reply(calls: list[dict]) -> str:
    """Used when the model stops without a final message: summarise the last tool result verbatim."""
    if not calls:
        return "Sorry, I couldn't process that leave request. Please try rephrasing it."
    last = calls[-1]["result"]
    if "error" in last:
        return f"That didn't work: {last['error']}."
    if "status" in last and "request_id" in last:
        return f"Request #{last['request_id']} is {last['status']}. {last.get('reason', '')}".strip()
    return json.dumps(last, default=str)
