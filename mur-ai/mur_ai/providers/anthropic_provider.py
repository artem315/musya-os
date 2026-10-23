"""Claude via the official Anthropic Python SDK (Messages API, manual tool loop)."""

from __future__ import annotations

import anthropic

from .base import AssistantTurn, Provider, ProviderError, ToolCall, ToolDef, ToolResult, Usage

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str | None = None, *, effort: str = "high",
                 base_url: str | None = None, timeout: float = 300.0):
        super().__init__(model or DEFAULT_MODEL)
        self.effort = effort
        self.client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, timeout=timeout,
                                               max_retries=2)

    def add_user(self, state: dict, text: str) -> None:
        msgs = state["messages"]
        block = {"type": "text", "text": text}
        if msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"].append(block)  # e.g. after a refused turn: keep roles alternating
        else:
            msgs.append({"role": "user", "content": [block]})

    def add_tool_results(self, state: dict, results: list[ToolResult]) -> None:
        # All results of one assistant turn go back in a single user message.
        blocks = []
        for r in results:
            content: list = [{"type": "text", "text": r.content or "(empty)"}]
            content += [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}}
                        for mt, data in r.images[:2]]
            blocks.append({"type": "tool_result", "tool_use_id": r.id, "content": content, "is_error": r.is_error})
        state["messages"].append({"role": "user", "content": blocks})

    async def complete(self, state: dict, system: str, tools: list[ToolDef], *,
                       max_tokens: int = 16000) -> AssistantTurn:
        kwargs = {}
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                               for t in tools]
        try:
            resp = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=state["messages"],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kwargs,
            )
        except anthropic.AuthenticationError:
            raise ProviderError("Anthropic rejected the API key (401). Check it in MUR Settings → AI.",
                                status=401) from None
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError(f"Anthropic denied the request: {exc.message}", status=403) from None
        except anthropic.NotFoundError:
            raise ProviderError(f"model '{self.model}' was not found for this API key", status=404) from None
        except anthropic.RateLimitError:
            raise ProviderError("Anthropic rate limit reached - try again shortly", retryable=True,
                                status=429) from None
        except anthropic.BadRequestError as exc:
            raise ProviderError(f"Anthropic rejected the request: {exc.message}", status=400) from None
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error {exc.status_code}: {exc.message}",
                                retryable=exc.status_code >= 500, status=exc.status_code) from None
        except anthropic.APIConnectionError:
            raise ProviderError("cannot reach the Anthropic API (network error)", retryable=True) from None

        # Keep the assistant content exactly as returned (thinking blocks included).
        content = [block.model_dump(mode="json", exclude_none=True) for block in resp.content]
        state["messages"].append({"role": "assistant", "content": content})

        if resp.stop_reason == "refusal":
            details = resp.stop_details.model_dump(mode="json") if getattr(resp, "stop_details", None) else None
            # Drop the refused assistant turn so the conversation can continue.
            state["messages"].pop()
            return AssistantTurn(text="The model declined this request.", stop_reason="refusal",
                                 model=resp.model, refusal=details)

        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, input=b.input if isinstance(b.input, dict) else {})
                 for b in resp.content if b.type == "tool_use"]
        usage = Usage(getattr(resp.usage, "input_tokens", 0) or 0, getattr(resp.usage, "output_tokens", 0) or 0)
        stop = "tool_use" if calls else ("max_tokens" if resp.stop_reason == "max_tokens" else "end_turn")
        return AssistantTurn(text=text, tool_calls=calls, stop_reason=stop, usage=usage, model=resp.model)
