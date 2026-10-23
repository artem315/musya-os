"""The MUR AI agent loop: model ↔ tools with policy checks, approvals, events and audit."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..providers import Provider, ProviderError, ToolDef, ToolResult
from ..safety import MODE_CATEGORIES, Decision, Mode, SessionPolicy, redact, redact_obj
from ..tools import TOOLS, TOOLS_BY_NAME, SchemaError, Tool, ToolOutput, Workspace
from .prompt import build_system_prompt

TOOL_TIMEOUT = 240.0
APPROVAL_TIMEOUT = 600.0

EventSink = Callable[[str, dict], Any]
AuditSink = Callable[[dict], Awaitable[None] | None]


@dataclass
class Conversation:
    id: str
    owner: str
    project_id: str
    provider_state: dict
    policy: SessionPolicy = field(default_factory=SessionPolicy)
    transcript: list[dict] = field(default_factory=list)
    title: str = "New chat"
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "model_calls": 0})

    def to_dict(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "title": self.title, "mode": self.policy.mode.value,
                "permissions": sorted(self.policy.permissions), "always_allow": sorted(self.policy.always_allow),
                "transcript": self.transcript, "usage": self.usage}


class ApprovalBroker:
    """Holds tool calls waiting for the user's decision."""

    def __init__(self):
        self.pending: dict[str, tuple[str, asyncio.Future, dict]] = {}

    async def request(self, owner: str, info: dict, timeout: float = APPROVAL_TIMEOUT) -> str:
        approval_id = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self.pending[approval_id] = (owner, fut, info)
        info["approval_id"] = approval_id
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return "timeout"
        finally:
            self.pending.pop(approval_id, None)

    def resolve(self, owner: str, approval_id: str, decision: str) -> bool:
        item = self.pending.get(approval_id)
        if item is None or item[0] != owner or decision not in ("once", "session", "deny"):
            return False
        if not item[1].done():
            item[1].set_result(decision)
        return True

    def list(self, owner: str) -> list[dict]:
        return [info for (o, _, info) in self.pending.values() if o == owner]

    def deny_all(self, owner: str) -> None:
        for aid, (o, fut, _) in list(self.pending.items()):
            if o == owner and not fut.done():
                fut.set_result("deny")


class MurAgent:
    def __init__(self, provider: Provider, workspace: Workspace, *, approvals: ApprovalBroker,
                 emit: EventSink | None = None, audit: AuditSink | None = None, tools: list[Tool] | None = None):
        self.provider = provider
        self.ws = workspace
        self.approvals = approvals
        self._emit = emit
        self._audit = audit
        self.tools = tools or TOOLS

    # ------------------------------------------------------------- helpers
    def emit(self, conv: Conversation, type_: str, **data) -> None:
        if self._emit is not None:
            self._emit(type_, {"conversation": conv.id, **data})

    async def audit(self, conv: Conversation, **record) -> None:
        if self._audit is None:
            return
        record = {"conversation": conv.id, "owner": conv.owner, "project": conv.project_id, "ts": time.time(),
                  **redact_obj(record)}
        res = self._audit(record)
        if asyncio.iscoroutine(res):
            await res

    def offered_tools(self, mode: Mode) -> list[Tool]:
        cats = MODE_CATEGORIES[mode]
        return [t for t in self.tools if t.category in cats]

    def add_item(self, conv: Conversation, item: dict) -> dict:
        item.setdefault("id", uuid.uuid4().hex[:10])
        item.setdefault("ts", time.time())
        conv.transcript.append(item)
        del conv.transcript[:-400]
        return item

    # -------------------------------------------------------------- run
    async def run(self, conv: Conversation, user_text: str) -> str:
        policy = conv.policy
        self.ws.ai_permissions = set(policy.permissions)
        if conv.title == "New chat":
            conv.title = user_text.strip().split("\n", 1)[0][:60] or "New chat"
        self.add_item(conv, {"type": "user", "text": user_text})
        self.emit(conv, "ai.turn_started", mode=policy.mode.value, text=user_text)
        await self.audit(conv, event="user_message", detail={"mode": policy.mode.value, "text": user_text[:2000]})
        self.provider.add_user(conv.provider_state, user_text)

        system = build_system_prompt(conv.project_id, policy.mode.value)
        offered = self.offered_tools(policy.mode)
        tool_defs = [ToolDef(t.name, t.description, t.schema) for t in offered]
        final_text = ""
        tool_calls_done = 0
        try:
            for _ in range(policy.max_model_calls):
                self.emit(conv, "ai.thinking")
                try:
                    turn = await self.provider.complete(conv.provider_state, system, tool_defs)
                except ProviderError as exc:
                    item = self.add_item(conv, {"type": "error", "text": str(exc)})
                    self.emit(conv, "ai.error", item=item)
                    await self.audit(conv, event="provider_error", detail={"error": str(exc)})
                    return str(exc)
                conv.usage["model_calls"] += 1
                conv.usage["input_tokens"] += turn.usage.input_tokens
                conv.usage["output_tokens"] += turn.usage.output_tokens
                if turn.text:
                    final_text = turn.text
                    item = self.add_item(conv, {"type": "assistant", "text": turn.text, "model": turn.model})
                    self.emit(conv, "ai.message", item=item)
                if turn.stop_reason == "refusal":
                    item = self.add_item(conv, {"type": "error", "text": "The model declined this request.",
                                                "refusal": turn.refusal})
                    self.emit(conv, "ai.error", item=item)
                    break
                if not turn.tool_calls:
                    if turn.stop_reason == "max_tokens":
                        item = self.add_item(conv, {"type": "error", "text": "The reply hit the output limit."})
                        self.emit(conv, "ai.error", item=item)
                    break
                results: list[ToolResult] = []
                for call in turn.tool_calls:
                    tool_calls_done += 1
                    if tool_calls_done > policy.max_tool_calls:
                        results.append(ToolResult(call.id, "tool call limit for this turn reached - stop and "
                                                           "summarise progress for the user", True))
                        continue
                    results.append(await self.handle_call(conv, call))
                self.provider.add_tool_results(conv.provider_state, results)
                if tool_calls_done > policy.max_tool_calls + 5:
                    break
            else:
                item = self.add_item(conv, {"type": "error", "text": "Stopped: step limit for one request reached."})
                self.emit(conv, "ai.error", item=item)
        except asyncio.CancelledError:
            item = self.add_item(conv, {"type": "error", "text": "Stopped by the user."})
            self.emit(conv, "ai.error", item=item)
            self.approvals.deny_all(conv.owner)
            await self.audit(conv, event="cancelled", detail={})
            raise
        finally:
            self.emit(conv, "ai.turn_finished", usage=conv.usage)
        return final_text

    async def handle_call(self, conv: Conversation, call) -> ToolResult:
        tool = TOOLS_BY_NAME.get(call.name)
        item = self.add_item(conv, {"type": "tool", "tool": call.name, "input": redact_obj(call.input),
                                    "status": "checking"})
        offered = {t.name for t in self.offered_tools(conv.policy.mode)}

        def finish(status: str, text: str, is_error: bool, **extra) -> ToolResult:
            item.update(status=status, result=redact(text[:4000]), **extra)
            self.emit(conv, "ai.tool", item=item)
            return ToolResult(call.id, text, is_error)

        if tool is None or call.name not in offered:
            res = finish("denied", f"tool '{call.name}' is not available in {conv.policy.mode.value} mode", True)
            await self.audit(conv, event="tool_denied", tool=call.name, detail={"reason": "not offered"})
            return res
        if call.parse_error:
            return finish("failed", f"invalid arguments: {call.parse_error}", True)
        try:
            tool.validate(call.input)
        except SchemaError as exc:
            return finish("failed", f"invalid arguments: {exc}", True)

        risk = tool.risk_for(call.input)
        perm = tool.permission_for(call.input)
        decision, reason = conv.policy.decide(tool.name, risk, perm, tool.category)
        item.update(risk=risk.value, permission=perm, reason=reason)

        if decision == Decision.DENY:
            await self.audit(conv, event="tool_denied", tool=tool.name, risk=risk.value,
                             detail={"reason": reason, "input": call.input})
            return finish("denied", f"DENIED: {reason}", True)

        if decision == Decision.ASK:
            item["status"] = "awaiting_approval"
            info = {"conversation": conv.id, "tool": tool.name, "input": redact_obj(call.input), "risk": risk.value,
                    "permission": perm, "reason": reason, "item": item["id"]}
            approval = asyncio.ensure_future(self.approvals.request(conv.owner, info))
            await asyncio.sleep(0)  # let request() register the approval id
            self.emit(conv, "ai.approval_required", approval=info, item=item)
            answer = await approval
            await self.audit(conv, event="approval", tool=tool.name, risk=risk.value,
                             detail={"answer": answer, "input": call.input})
            if answer in ("deny", "timeout"):
                msg = "the user denied this action" if answer == "deny" else "no answer from the user (timed out)"
                return finish("denied", f"DENIED: {msg}. Do not retry it; explain or choose another way.", True,
                              decision=answer)
            if answer == "session":
                conv.policy.always_allow.add(tool.name)
            item["decision"] = answer

        item["status"] = "running"
        self.emit(conv, "ai.tool", item=item)
        started = time.monotonic()
        try:
            out: ToolOutput = await asyncio.wait_for(tool.handler(self.ws, call.input), TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            out = ToolOutput(f"tool timed out after {TOOL_TIMEOUT:g}s", True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # report real failures to the model
            msg = str(exc).splitlines()[0][:500] if str(exc) else type(exc).__name__
            out = ToolOutput(f"tool crashed: {type(exc).__name__}: {msg}", True)
        duration = int((time.monotonic() - started) * 1000)
        await self.audit(conv, event="tool_call", tool=tool.name, risk=risk.value, ok=not out.is_error,
                         duration_ms=duration, detail={"input": call.input, "result": out.content[:2000]})
        result = finish("failed" if out.is_error else "done", out.content, out.is_error, data=_ui_data(out.data),
                        duration_ms=duration)
        result.images = out.images
        return result


def _ui_data(data: dict) -> dict:
    try:
        json.dumps(data)
        return redact_obj(data)
    except (TypeError, ValueError):
        return {}


__all__ = ["MurAgent", "Conversation", "ApprovalBroker"]
