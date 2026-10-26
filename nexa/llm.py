"""The local model: one non-streaming call to Ollama's /api/chat, with tools.

stdlib only, so the tests and `nexa doctor` run on a bare interpreter. The default
model is Qwen3.5 9B, which reasons before it picks a tool; its `thinking` text is
returned separately and is never shown to the user or fed back as context.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config


class LlmError(RuntimeError):
    """Talking to the local model failed in a way a retry will not fix."""


def _post(path: str, payload: dict, *, host: str | None = None, timeout: int | None = None) -> dict:
    url = f"{(host or config.HOST).rstrip('/')}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        if exc.code == 404 and "model" in detail.lower():
            raise LlmError(
                f"{payload.get('model')} is not installed. Run: ollama pull {payload.get('model')}"
            ) from None
        raise LlmError(f"ollama returned HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise LlmError(
            f"cannot reach ollama at {url} ({exc.reason}). Start it with `ollama serve`, "
            f"or point NEXA_HOST at the right address."
        ) from None
    except TimeoutError:
        raise LlmError(
            f"the model took longer than {timeout or config.TIMEOUT}s to answer. "
            f"Raise NEXA_TIMEOUT, or use a smaller NEXA_NUM_CTX."
        ) from None


def chat(messages: list[dict], tools: list[dict] | None = None, *, model: str | None = None,
         host: str | None = None, think: bool | None = None, temperature: float | None = None,
         num_ctx: int | None = None, timeout: int | None = None) -> dict:
    """One turn. Returns the assistant message dict (content / thinking / tool_calls)."""
    payload = {
        "model": model or config.MODEL,
        "messages": messages,
        "stream": False,
        "think": config.THINK if think is None else think,
        "options": {
            "temperature": config.TEMPERATURE if temperature is None else temperature,
            "num_ctx": num_ctx or config.NUM_CTX,
        },
    }
    if tools:
        payload["tools"] = tools
    data = _post("/api/chat", payload, host=host, timeout=timeout)
    message = data.get("message")
    if not isinstance(message, dict):
        raise LlmError(f"unexpected reply from ollama: {str(data)[:300]}")
    return message


def tool_calls(message: dict) -> list[tuple[str, dict]]:
    """(name, arguments) for each tool call in an assistant message."""
    calls = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        calls.append((name, args if isinstance(args, dict) else {}))
    return calls


def installed_models(host: str | None = None, timeout: int = 10) -> list[str]:
    url = f"{(host or config.HOST).rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LlmError(f"cannot reach ollama at {url}: {exc}") from None
    return [m.get("name", "") for m in data.get("models", [])]


def doctor(host: str | None = None, model: str | None = None) -> list[tuple[bool, str]]:
    """Checks `nexa doctor` prints: server reachable, model present, tool calling works."""
    host = host or config.HOST
    model = model or config.MODEL
    checks: list[tuple[bool, str]] = []
    try:
        names = installed_models(host)
    except LlmError as exc:
        return [(False, str(exc))]
    checks.append((True, f"ollama is up at {host}"))

    present = model in names or any(n.split(":")[0] == model.split(":")[0] for n in names)
    checks.append((present, f"model {model}" + ("" if present else f" is missing (have: {', '.join(names) or 'none'})")))
    if not present:
        return checks

    probe = [{
        "type": "function",
        "function": {
            "name": "ping",
            "description": "Reply to a readiness check.",
            "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        },
    }]
    try:
        msg = chat([{"role": "user", "content": "Call the ping tool with ok=true."}],
                   probe, model=model, host=host, think=False, timeout=180)
    except LlmError as exc:
        checks.append((False, f"tool calling: {exc}"))
        return checks
    names_called = [n for n, _ in tool_calls(msg)]
    checks.append((bool(names_called), "tool calling works" if names_called
                   else f"the model answered without calling a tool: {str(msg.get('content'))[:120]}"))
    return checks
