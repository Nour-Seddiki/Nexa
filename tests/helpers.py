"""Shared test scaffolding: a throwaway context and a scripted stand-in for the model."""

from __future__ import annotations

import tempfile
from pathlib import Path

from nexa.market import Market
from nexa.store import Store
from nexa.tools import Context


def temp_context() -> Context:
    """A context whose state file lives in a fresh temp directory."""
    state = Path(tempfile.mkdtemp(prefix="nexa-test-")) / "state.json"
    return Context(Store(state), Market())


def tool_call(name: str, **arguments) -> dict:
    return {"function": {"name": name, "arguments": arguments}}


def say(content: str = "", *calls: dict) -> dict:
    """One assistant message, as ollama would return it."""
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = list(calls)
    return message


class FakeModel:
    """Replays scripted assistant messages and records what it was sent."""

    def __init__(self, *messages: dict):
        self.script = list(messages)
        self.seen: list[list[dict]] = []

    def __call__(self, messages, tools=None, **kwargs):
        self.seen.append([dict(m) for m in messages])
        if not self.script:
            return {"role": "assistant", "content": "done"}
        return self.script.pop(0)

    @property
    def last_tool_results(self) -> list[str]:
        if not self.seen:
            return []
        return [m["content"] for m in self.seen[-1] if m.get("role") == "tool"]
