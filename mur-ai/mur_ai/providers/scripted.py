"""Scripted provider - a deterministic stand-in model for automated tests.

It is NOT an AI: it replays a fixed list of turns. MURCLOUD only offers it
when ``MUR_AI_ALLOW_SCRIPTED=1`` (CI / end-to-end tests), and the UI labels
it "Test double". The real tools still execute for real; only the model is
replaced.
"""

from __future__ import annotations

import uuid
from typing import Callable

from .base import AssistantTurn, Provider, ToolCall, ToolDef, ToolResult


class ScriptedProvider(Provider):
    name = "scripted"

    def __init__(self, turns: list[dict] | Callable[[dict], dict] | None = None, model: str = "scripted-test-double"):
        super().__init__(model)
        self.turns = turns if turns is not None else []
        self.calls = 0
        self.seen_tools: list[list[str]] = []

    def add_user(self, state: dict, text: str) -> None:
        state["messages"].append({"role": "user", "content": text})

    def add_tool_results(self, state: dict, results: list[ToolResult]) -> None:
        state["messages"].append({"role": "tool_results", "results": [r.__dict__ for r in results]})

    async def complete(self, state: dict, system: str, tools: list[ToolDef], *, max_tokens: int = 16000):
        self.seen_tools.append([t.name for t in tools])
        if callable(self.turns):
            spec = self.turns(state)
        elif self.calls < len(self.turns):
            spec = self.turns[self.calls]
        else:
            spec = {"text": "Done."}
        self.calls += 1
        calls = [ToolCall(c.get("id") or uuid.uuid4().hex[:8], c["name"], c.get("input", {}))
                 for c in spec.get("tool_calls", [])]
        state["messages"].append({"role": "assistant", "text": spec.get("text", ""),
                                  "tool_calls": [c.__dict__ for c in calls]})
        return AssistantTurn(text=spec.get("text", ""), tool_calls=calls,
                             stop_reason="tool_use" if calls else "end_turn", model=self.model)
