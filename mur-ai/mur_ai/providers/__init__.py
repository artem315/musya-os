"""AI providers for MUR AI Core."""

from __future__ import annotations

import os

from .base import (AssistantTurn, Provider, ProviderError, ToolCall, ToolDef, ToolResult, Usage)

PROVIDERS = {
    "anthropic": {"label": "Anthropic (Claude)", "default_model": "claude-opus-5-5",
                  "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]},
    "openai": {"label": "OpenAI", "default_model": "gpt-5", "models": []},
    "openrouter": {"label": "OpenRouter", "default_model": "anthropic/claude-opus-5.5", "models": []},
}


def _scripted_allowed(flag: bool | None) -> bool:
    return flag if flag is not None else os.environ.get("MUR_AI_ALLOW_SCRIPTED") == "1"


def available_providers(allow_scripted: bool | None = None) -> dict:
    out = dict(PROVIDERS)
    if _scripted_allowed(allow_scripted):
        out["scripted"] = {"label": "Test double (scripted, not an AI)", "default_model": "scripted-test-double",
                           "models": []}
    return out


def make_provider(name: str, api_key: str | None, model: str | None = None, *, base_url: str | None = None,
                  effort: str = "high", allow_scripted: bool | None = None) -> Provider:
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        if not api_key:
            raise ProviderError("no Anthropic API key configured (MUR Settings → AI)")
        return AnthropicProvider(api_key, model, effort=effort, base_url=base_url)
    if name == "openai":
        from .openai_provider import OpenAIProvider
        if not api_key:
            raise ProviderError("no OpenAI API key configured (MUR Settings → AI)")
        return OpenAIProvider(api_key, model, base_url=base_url)
    if name == "openrouter":
        from .openai_provider import OpenRouterProvider
        if not api_key:
            raise ProviderError("no OpenRouter API key configured (MUR Settings → AI)")
        return OpenRouterProvider(api_key, model)
    if name == "scripted" and _scripted_allowed(allow_scripted):
        from .scripted import ScriptedProvider
        from .demo_script import demo_site_turns
        return ScriptedProvider(demo_site_turns)
    raise ProviderError(f"unknown AI provider '{name}'")


__all__ = ["Provider", "ProviderError", "AssistantTurn", "ToolCall", "ToolDef", "ToolResult", "Usage",
           "make_provider", "available_providers", "PROVIDERS"]
