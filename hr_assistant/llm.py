"""OpenRouter chat client with disk cache, rate limiting, retries, tracing, and JSON parsing."""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Protocol

import anthropic
import httpx

from .config import Settings, get_settings
from .trace import Trace


class LLMError(RuntimeError):
    """Raised when the LLM provider cannot produce a usable response."""


class ChatModel(Protocol):
    """Interface used by components, so tests can plug in a scripted fake."""

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None, max_tokens: int = 1200,
             trace: Trace | None = None, component: str = "llm") -> dict: ...

    def chat_json(self, messages: list[dict], *, trace: Trace | None = None, component: str = "llm",
                  max_tokens: int = 1200) -> dict: ...


_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)


def parse_json(text: str) -> dict:
    """Extract the first top-level JSON object from a model reply (tolerates code fences and prose)."""
    cleaned = _FENCE.sub("", text or "").strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:i + 1])
    raise ValueError("unterminated JSON object in reply")


def _from_openai(data: dict) -> dict:
    msg = data["choices"][0]["message"]
    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or [],
            "model": data.get("model", ""), "usage": data.get("usage", {})}


def _to_anthropic_payload(model: str, messages: list[dict], tools: list[dict] | None, max_tokens: int) -> dict:
    """Convert OpenAI-style messages/tools to a Claude Messages API request.

    - system messages → top-level `system`
    - assistant tool_calls → `tool_use` blocks; role "tool" → `tool_result` blocks, consecutive ones merged
      into ONE user message (splitting them discourages parallel tool calls)
    - thinking disabled (allowed on Haiku 5.5 at its default effort): these are short JSON/tool steps and it
      keeps cost minimal. No temperature: Claude 5.x models reject sampling parameters; outputs are cached instead.
    """
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    out: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            continue
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                    and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant" and m.get("tool_calls"):
            blocks: list[dict] = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            for tc in m["tool_calls"]:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": args})
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": role, "content": m["content"]})
    payload: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": out,
                               "thinking": {"type": "disabled"}}
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"],
                             "input_schema": t["function"]["parameters"]} for t in tools]
    return payload


class LLMClient:
    """LLM client for Anthropic (Claude, via the official SDK) or OpenRouter (OpenAI-compatible HTTP)."""

    def __init__(self, settings: Settings | None = None, model: str | None = None,
                 fallbacks: list[str] | None = None) -> None:
        self.s = settings or get_settings()
        self.model = model or self.s.model
        self.fallbacks = self.s.fallback_models if fallbacks is None else fallbacks
        self.network_calls = 0
        self.cache_hits = 0
        self._last_call = 0.0
        self._anthropic: anthropic.Anthropic | None = None

    # ---- public API -------------------------------------------------------
    def chat(self, messages: list[dict], *, tools: list[dict] | None = None, max_tokens: int = 1200,
             trace: Trace | None = None, component: str = "llm") -> dict:
        """Messages and tools use the OpenAI-style shape internally; the Anthropic path converts them."""
        if self.s.provider == "anthropic":
            payload = _to_anthropic_payload(self.model, messages, tools, max_tokens)
        else:
            payload = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
            if self.fallbacks:
                payload["models"] = [self.model, *[m for m in self.fallbacks if m != self.model]]
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

        key = hashlib.sha256(json.dumps({"provider": self.s.provider, **payload}, sort_keys=True,
                                        ensure_ascii=False).encode()).hexdigest()
        cache_file = self.s.cache_dir / f"{key}.json"
        t0 = time.perf_counter()
        cached = self.s.use_cache and cache_file.exists()
        if cached:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            self.cache_hits += 1
        else:
            data = self._call_anthropic(payload) if self.s.provider == "anthropic" else _from_openai(self._post(payload))
            self.network_calls += 1
            if self.s.use_cache:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        result = {**data, "cached": cached}
        if trace is not None:
            trace.add("llm_call", {"for": component, "messages": messages, "tools": [t["function"]["name"] for t in tools or []]},
                      {"content": result["content"], "tool_calls": result["tool_calls"]},
                      (time.perf_counter() - t0) * 1000, model=result["model"], usage=result["usage"], cached=cached)
        return result

    def chat_json(self, messages: list[dict], *, trace: Trace | None = None, component: str = "llm",
                  max_tokens: int = 1200) -> dict:
        """Chat and parse a JSON object; one repair attempt if the first reply isn't valid JSON."""
        res = self.chat(messages, trace=trace, component=component, max_tokens=max_tokens)
        try:
            return parse_json(res["content"])
        except (ValueError, json.JSONDecodeError):
            repair = messages + [
                {"role": "assistant", "content": res["content"]},
                {"role": "user", "content": "Your reply was not a valid JSON object. Reply with only the JSON object."},
            ]
            res2 = self.chat(repair, trace=trace, component=component + ".repair", max_tokens=max_tokens)
            try:
                return parse_json(res2["content"])
            except (ValueError, json.JSONDecodeError) as exc:
                raise LLMError(f"{component}: model did not return valid JSON") from exc

    # ---- transport --------------------------------------------------------
    def _call_anthropic(self, payload: dict) -> dict:
        """Call the Claude Messages API via the official SDK and normalise the reply."""
        if not self.s.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set. Add it to .env.")
        if self._anthropic is None:
            self._anthropic = anthropic.Anthropic(api_key=self.s.api_key, max_retries=4, timeout=120.0)
        try:
            msg = self._anthropic.messages.create(**payload)
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY in .env.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError(f"Anthropic permission denied: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached after retries; try again in a minute.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Cannot reach the Anthropic API (network error).") from exc
        if msg.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        text = "".join(b.text for b in msg.content if b.type == "text")
        calls = [{"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                 for b in msg.content if b.type == "tool_use"]
        return {"content": text, "tool_calls": calls, "model": msg.model,
                "usage": {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}}

    def _post(self, payload: dict) -> dict:
        if not self.s.api_key:
            raise LLMError("OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key.")
        headers = {
            "Authorization": f"Bearer {self.s.api_key}",
            "Content-Type": "application/json",
            "X-Title": "HR Assistant course prototype",
        }
        delay = 5.0
        last_err = "unknown error"
        for _ in range(6):
            wait = self.s.min_interval_s - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            try:
                r = httpx.post(self.s.base_url, json=payload, headers=headers, timeout=120)
            except httpx.HTTPError as exc:
                last_err = f"network error: {exc}"
            else:
                if r.status_code == 200:
                    data = r.json()
                    if data.get("choices") and data["choices"][0].get("message") is not None:
                        return data
                    last_err = f"provider error: {str(data.get('error', data))[:300]}"
                elif r.status_code == 429:
                    if "per-day" in r.text or "per day" in r.text:
                        raise LLMError("OpenRouter daily free-model limit reached. Wait for the reset or add credits "
                                       "(cached responses in .cache/llm keep working).")
                    last_err = "rate limited (429)"
                elif r.status_code in (408, 500, 502, 503, 504):
                    last_err = f"HTTP {r.status_code}"
                else:
                    raise LLMError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
            time.sleep(delay)
            delay = min(delay * 2, 60)
        raise LLMError(f"OpenRouter request failed after retries ({last_err})")
