"""OpenAI and OpenRouter via the official OpenAI Python SDK (Chat Completions + function calling).

OpenRouter exposes an OpenAI-compatible API, so it reuses this provider
with a different ``base_url``.
"""

from __future__ import annotations

import json

import openai

from .base import AssistantTurn, Provider, ProviderError, ToolCall, ToolDef, ToolResult, Usage

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, api_key: str, model: str | None = None, *, base_url: str | None = None,
                 timeout: float = 300.0, extra_headers: dict | None = None):
        super().__init__(model or "gpt-5")
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2,
                                         default_headers=extra_headers)

    def new_state(self) -> dict:
        return {"provider": self.name, "messages": []}

    def add_user(self, state: dict, text: str) -> None:
        state["messages"].append({"role": "user", "content": text})

    def add_tool_results(self, state: dict, results: list[ToolResult]) -> None:
        for r in results:
            content = ("ERROR: " + r.content) if r.is_error else r.content
            if r.images:
                content += "\n(A screenshot was captured and shown to the user; images are not sent to this provider.)"
            state["messages"].append({"role": "tool", "tool_call_id": r.id, "content": content})

    async def complete(self, state: dict, system: str, tools: list[ToolDef], *,
                       max_tokens: int = 16000) -> AssistantTurn:
        messages = [{"role": "system", "content": system}, *state["messages"]]
        kwargs = {}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema}} for t in tools]
        try:
            resp = await self.client.chat.completions.create(model=self.model, messages=messages,
                                                             max_completion_tokens=max_tokens, **kwargs)
        except openai.AuthenticationError:
            raise ProviderError(f"{self.name} rejected the API key (401). Check it in MUR Settings → AI.",
                                status=401) from None
        except openai.NotFoundError:
            raise ProviderError(f"model '{self.model}' was not found", status=404) from None
        except openai.RateLimitError:
            raise ProviderError("rate limit reached - try again shortly", retryable=True, status=429) from None
        except openai.BadRequestError as exc:
            raise ProviderError(f"request rejected: {exc.message}", status=400) from None
        except openai.APIStatusError as exc:
            raise ProviderError(f"API error {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500,
                                status=exc.status_code) from None
        except openai.APIConnectionError:
            raise ProviderError(f"cannot reach the {self.name} API (network error)", retryable=True) from None
        if not resp.choices:
            raise ProviderError("the provider returned no choices")
        choice = resp.choices[0]
        msg = choice.message
        native: dict = {"role": "assistant", "content": msg.content or ""}
        calls: list[ToolCall] = []
        if msg.tool_calls:
            native["tool_calls"] = []
            for tc in msg.tool_calls:
                if tc.type != "function":
                    continue
                raw = tc.function.arguments or "{}"
                native["tool_calls"].append({"id": tc.id, "type": "function",
                                             "function": {"name": tc.function.name, "arguments": raw}})
                try:
                    parsed = json.loads(raw)
                    err = None if isinstance(parsed, dict) else "arguments must be a JSON object"
                except json.JSONDecodeError as exc:
                    parsed, err = {}, f"invalid JSON arguments: {exc.msg}"
                calls.append(ToolCall(tc.id, tc.function.name, parsed if isinstance(parsed, dict) else {}, err))
        state["messages"].append(native)
        refusal = getattr(msg, "refusal", None)
        usage = Usage(getattr(resp.usage, "prompt_tokens", 0) or 0, getattr(resp.usage, "completion_tokens", 0) or 0) \
            if resp.usage else Usage()
        if refusal and not calls:
            return AssistantTurn(text=refusal, stop_reason="refusal", usage=usage, model=resp.model,
                                 refusal={"explanation": refusal})
        stop = "tool_use" if calls else ("max_tokens" if choice.finish_reason == "length" else "end_turn")
        return AssistantTurn(text=msg.content or "", tool_calls=calls, stop_reason=stop, usage=usage,
                             model=resp.model)


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    def __init__(self, api_key: str, model: str | None = None, **kw):
        kw.setdefault("base_url", OPENROUTER_BASE_URL)
        kw.setdefault("extra_headers", {"X-Title": "MURCLOUD OS"})
        super().__init__(api_key, model or "anthropic/claude-opus-5.5", **kw)
