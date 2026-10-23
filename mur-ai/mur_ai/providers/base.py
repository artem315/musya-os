"""Provider-neutral interface for chat models with tool calling.

A provider keeps the conversation in its *native* message format (so
provider-specific content such as Claude thinking blocks round-trips
unchanged) inside a JSON-serialisable ``state`` dict. The agent only sees
:class:`AssistantTurn` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
    parse_error: str | None = None  # set when the model produced invalid JSON arguments


@dataclass
class ToolResult:
    id: str
    content: str
    is_error: bool = False
    images: list = field(default_factory=list)  # [(media_type, base64)] - sent to vision-capable providers


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class AssistantTurn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"  # end_turn | tool_use | max_tokens | refusal | error
    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    refusal: dict | None = None


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Provider:
    name = "base"

    def __init__(self, model: str):
        self.model = model

    def new_state(self) -> dict:
        return {"provider": self.name, "messages": []}

    def add_user(self, state: dict, text: str) -> None:
        raise NotImplementedError

    def add_tool_results(self, state: dict, results: list[ToolResult]) -> None:
        raise NotImplementedError

    async def complete(self, state: dict, system: str, tools: list[ToolDef], *,
                       max_tokens: int = 16000) -> AssistantTurn:
        """Call the model, append its reply to ``state`` and return it."""
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model}
