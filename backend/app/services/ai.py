"""MUR AI inside MURCLOUD: conversations, provider keys, running turns, approvals, audit."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException
from mur_ai import (DEFAULT_AI_PERMISSIONS, ApprovalBroker, Conversation, Mode, MurAgent, ProviderError,
                    SessionPolicy, Workspace, available_providers, make_provider)
from mur_ai.safety import AI_PERMISSIONS, redact
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AIConversation, ApiKey, AuditLog, Project


def conv_summary(c: AIConversation, running: bool = False) -> dict:
    return {"id": c.id, "project_id": c.project_id, "title": c.title, "mode": c.mode, "provider": c.provider,
            "model": c.model, "permissions": c.permissions, "always_allow": c.always_allow, "usage": c.usage or {},
            "created_at": c.created_at, "updated_at": c.updated_at, "running": running}


class AIService:
    def __init__(self, core):
        self.core = core
        self.approvals = ApprovalBroker()
        self.running: dict[str, asyncio.Task] = {}  # conversation id -> task
        self.active: dict[str, str] = {}  # user id -> conversation id used by `service ai ...`
        self._owner_of: dict[str, str] = {}  # running conversation id -> user id

    @property
    def settings(self):
        return self.core.settings

    # ----------------------------------------------------------------- keys
    async def set_key(self, db: AsyncSession, user_id: str, provider: str, api_key: str, model: str = "") -> dict:
        if provider not in ("anthropic", "openai", "openrouter"):
            raise HTTPException(400, "provider must be anthropic, openai or openrouter")
        api_key = api_key.strip()
        if len(api_key) < 10 or len(api_key) > 500 or any(c.isspace() for c in api_key):
            raise HTTPException(400, "that does not look like an API key")
        row = (await db.execute(select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.provider == provider))
               ).scalar_one_or_none()
        if row is None:
            row = ApiKey(user_id=user_id, provider=provider, encrypted_key="", hint="")
            db.add(row)
        row.encrypted_key = self.core.secretbox.encrypt(api_key)
        row.hint = "…" + api_key[-4:]
        row.model = model.strip()[:100]
        row.created_at = time.time()
        await db.flush()
        await self.audit(db, user_id, "user", "api_key_saved", detail={"provider": provider})
        return {"provider": provider, "hint": row.hint, "model": row.model}

    async def delete_key(self, db: AsyncSession, user_id: str, provider: str) -> None:
        row = (await db.execute(select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.provider == provider))
               ).scalar_one_or_none()
        if row is not None:
            await db.delete(row)
            await self.audit(db, user_id, "user", "api_key_deleted", detail={"provider": provider})

    async def list_keys(self, db: AsyncSession, user_id: str) -> list[dict]:
        rows = (await db.execute(select(ApiKey).where(ApiKey.user_id == user_id))).scalars().all()
        return [{"provider": r.provider, "hint": r.hint, "model": r.model, "created_at": r.created_at} for r in rows]

    def providers(self) -> dict:
        provs = available_providers(self.settings.allow_scripted_ai)
        return {"providers": provs, "server_key": bool(self.settings.server_ai_key),
                "server_provider": self.settings.server_ai_provider if self.settings.server_ai_key else None,
                "permissions": sorted(AI_PERMISSIONS), "default_permissions": sorted(DEFAULT_AI_PERMISSIONS),
                "modes": [m.value for m in Mode]}

    async def resolve_provider(self, db: AsyncSession, user_id: str, provider: str, model: str):
        if provider == "scripted":
            if not self.settings.allow_scripted_ai:
                raise ProviderError("the scripted test provider is disabled on this server")
            return make_provider("scripted", None, allow_scripted=True)
        row = (await db.execute(select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.provider == provider))
               ).scalar_one_or_none()
        key = self.core.secretbox.decrypt(row.encrypted_key) if row else None
        model = model or (row.model if row else "")
        if not key and self.settings.server_ai_key and provider == self.settings.server_ai_provider:
            key, model = self.settings.server_ai_key, model or self.settings.server_ai_model
        if not key:
            raise ProviderError(f"No {provider} API key. Add one in MUR Settings → AI (it is stored encrypted).")
        return make_provider(provider, key, model or None)

    # -------------------------------------------------------- conversations
    async def create(self, db: AsyncSession, user_id: str, project_id: str, *, mode: str = "agent",
                     provider: str = "anthropic", model: str = "", permissions: list[str] | None = None) -> AIConversation:
        project = await db.get(Project, project_id)
        if project is None or project.user_id != user_id:
            raise HTTPException(404, "project not found")
        if mode not in {m.value for m in Mode}:
            raise HTTPException(400, "invalid mode")
        if provider not in available_providers(self.settings.allow_scripted_ai):
            raise HTTPException(400, "unknown provider")
        perms = sorted(set(permissions if permissions is not None else DEFAULT_AI_PERMISSIONS) & set(AI_PERMISSIONS))
        conv = AIConversation(user_id=user_id, project_id=project_id, mode=mode, provider=provider, model=model[:100],
                              permissions=perms, always_allow=[], provider_state={}, transcript=[],
                              usage={"input_tokens": 0, "output_tokens": 0, "model_calls": 0})
        db.add(conv)
        await db.flush()
        self.active[user_id] = conv.id
        return conv

    async def get(self, db: AsyncSession, user_id: str, conv_id: str) -> AIConversation:
        conv = await db.get(AIConversation, conv_id)
        if conv is None or conv.user_id != user_id:
            raise HTTPException(404, "conversation not found")
        return conv

    async def list(self, db: AsyncSession, user_id: str, project_id: str | None = None) -> list[dict]:
        q = select(AIConversation).where(AIConversation.user_id == user_id)
        if project_id:
            q = q.where(AIConversation.project_id == project_id)
        rows = (await db.execute(q.order_by(AIConversation.updated_at.desc()).limit(100))).scalars().all()
        return [conv_summary(r, r.id in self.running) for r in rows]

    async def update(self, db: AsyncSession, user_id: str, conv_id: str, *, mode: str | None = None,
                     permissions: list[str] | None = None, provider: str | None = None, model: str | None = None,
                     title: str | None = None) -> AIConversation:
        conv = await self.get(db, user_id, conv_id)
        if mode is not None:
            if mode not in {m.value for m in Mode}:
                raise HTTPException(400, "invalid mode")
            conv.mode = mode
        if permissions is not None:
            conv.permissions = sorted(set(permissions) & set(AI_PERMISSIONS))
        if provider is not None and provider != conv.provider:
            if provider not in available_providers(self.settings.allow_scripted_ai):
                raise HTTPException(400, "unknown provider")
            if conv.id in self.running:
                raise HTTPException(409, "cannot switch provider while MUR AI is working")
            conv.provider, conv.provider_state = provider, {}
            conv.transcript = list(conv.transcript or []) + [
                {"type": "notice", "text": f"Switched provider to {provider}; model context starts fresh.",
                 "ts": time.time()}]
        if model is not None:
            conv.model = model[:100]
        if title is not None:
            conv.title = title.strip()[:120] or conv.title
        conv.updated_at = time.time()
        await db.flush()
        await self.audit(db, user_id, "user", "ai_settings", detail={"conversation": conv.id, "mode": conv.mode,
                                                                    "permissions": conv.permissions})
        return conv

    async def delete(self, db: AsyncSession, user_id: str, conv_id: str) -> None:
        conv = await self.get(db, user_id, conv_id)
        self.stop(user_id, conv_id)
        await db.delete(conv)

    # -------------------------------------------------------------- running
    def workspace(self, user_id: str, project_id: str, permissions: set[str]) -> Workspace:
        async def changed(path: str, action: str) -> None:
            self.core.engine.bus.publish(user_id, "project.file_changed", project=project_id, path=path, action=action)

        return Workspace(owner=user_id, project_id=project_id, root=self.core.projects.root(user_id, project_id),
                         engine=self.core.engine, previews=self.core.previews,
                         public_preview_url=lambda pid: self._preview_url(user_id, pid),
                         on_file_changed=changed, ai_permissions=permissions)

    def _preview_url(self, user_id: str, project_id: str) -> str:
        from ..routers.projects import sign_preview
        p = self.core.previews.get(user_id, project_id)
        folder = "."
        if p is not None:
            root = self.core.projects.root(user_id, project_id)
            folder = p.root.relative_to(root).as_posix() if p.root != root else "."
        return f"/p/{sign_preview(self.core, user_id, project_id, folder)}/index.html"

    async def send(self, user_id: str, conv_id: str, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise HTTPException(400, "message is empty")
        if len(text) > 20_000:
            raise HTTPException(413, "message is too long")
        if conv_id in self.running:
            raise HTTPException(409, "MUR AI is still working on the previous message")
        if sum(1 for c in self.running if self._owner_of.get(c) == user_id) >= 2:
            raise HTTPException(429, "two MUR AI tasks are already running")
        async with self.core.db.sessionmaker() as db:
            conv_row = await self.get(db, user_id, conv_id)
            try:
                provider = await self.resolve_provider(db, user_id, conv_row.provider, conv_row.model)
            except ProviderError as exc:
                raise HTTPException(400, str(exc)) from None
            conv = Conversation(
                id=conv_row.id, owner=user_id, project_id=conv_row.project_id,
                provider_state=conv_row.provider_state if conv_row.provider_state.get("provider") == provider.name
                else provider.new_state(),
                policy=SessionPolicy(mode=Mode(conv_row.mode), permissions=set(conv_row.permissions),
                                     always_allow=set(conv_row.always_allow)),
                transcript=list(conv_row.transcript or []), title=conv_row.title,
                usage=dict(conv_row.usage or {"input_tokens": 0, "output_tokens": 0, "model_calls": 0}))
        self.active[user_id] = conv_id
        ws = self.workspace(user_id, conv.project_id, set(conv.policy.permissions))
        agent = MurAgent(provider, ws, approvals=self.approvals,
                         emit=lambda t, d: self.core.engine.bus.publish(user_id, t, **d),
                         audit=lambda rec: self._audit_from_agent(user_id, rec))
        task = asyncio.ensure_future(self._run(agent, conv, text))
        self.running[conv_id] = task
        self._owner_of[conv_id] = user_id
        return {"conversation": conv_id, "status": "running", "provider": provider.name, "model": provider.model}

    async def _run(self, agent: MurAgent, conv: Conversation, text: str) -> None:
        try:
            await agent.run(conv, text)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # report, never hide
            conv.transcript.append({"type": "error", "text": f"internal error: {type(exc).__name__}: {exc}"[:500],
                                    "ts": time.time()})
            self.core.engine.bus.publish(conv.owner, "ai.error", conversation=conv.id,
                                         item={"type": "error", "text": str(exc)[:500]})
        finally:
            self.running.pop(conv.id, None)
            self._owner_of.pop(conv.id, None)
            async with self.core.db.sessionmaker() as db:
                row = await db.get(AIConversation, conv.id)
                if row is not None:
                    row.provider_state = conv.provider_state
                    row.transcript = conv.transcript
                    row.usage = conv.usage
                    row.title = conv.title
                    row.mode = conv.policy.mode.value
                    row.always_allow = sorted(conv.policy.always_allow)
                    row.updated_at = time.time()
                    await db.commit()
            self.core.engine.bus.publish(conv.owner, "ai.saved", conversation=conv.id)

    def stop(self, user_id: str, conv_id: str | None = None) -> bool:
        stopped = False
        for cid, task in list(self.running.items()):
            if self._owner_of.get(cid) == user_id and (conv_id is None or cid == conv_id):
                task.cancel()
                stopped = True
        if stopped:
            self.approvals.deny_all(user_id)
        return stopped

    def approve(self, user_id: str, approval_id: str, decision: str) -> bool:
        return self.approvals.resolve(user_id, approval_id, decision)

    # -------------------------------------------------------------- audit
    async def audit(self, db: AsyncSession, user_id: str, actor: str, event: str, *, tool: str = "", risk: str = "",
                    ok: bool = True, detail: dict | None = None) -> None:
        db.add(AuditLog(user_id=user_id, actor=actor, event=event, tool=tool, risk=risk, ok=ok, detail=detail or {}))

    async def _audit_from_agent(self, user_id: str, rec: dict) -> None:
        async with self.core.db.sessionmaker() as db:
            db.add(AuditLog(user_id=user_id, actor="ai", event=rec.get("event", ""), tool=rec.get("tool", ""),
                            risk=rec.get("risk", ""), ok=bool(rec.get("ok", True)),
                            detail={k: v for k, v in rec.items() if k not in ("event", "tool", "risk", "ok", "owner")}))
            await db.commit()

    async def audit_log(self, db: AsyncSession, user_id: str, limit: int = 200) -> list[dict]:
        rows = (await db.execute(select(AuditLog).where(AuditLog.user_id == user_id)
                                 .order_by(AuditLog.id.desc()).limit(limit))).scalars().all()
        return [{"id": r.id, "ts": r.ts, "actor": r.actor, "event": r.event, "tool": r.tool, "risk": r.risk,
                 "ok": r.ok, "detail": r.detail} for r in rows]

    # -------------------------------------------------- one-shot & controller
    async def ask(self, user_id: str, prompt: str, options: dict) -> str:
        """``ai.ask(prompt)`` from MUSYA scripts: one model call, no tools."""
        provider_name = str(options.get("provider") or "anthropic")
        async with self.core.db.sessionmaker() as db:
            provider = await self.resolve_provider(db, user_id, provider_name, str(options.get("model") or ""))
            db.add(AuditLog(user_id=user_id, actor="script", event="ai_ask", detail={"prompt": redact(prompt[:500])}))
            await db.commit()
        state = provider.new_state()
        provider.add_user(state, prompt[:20_000])
        turn = await provider.complete(state, "You are MUR AI answering a question from a MUSYA script. "
                                              "Answer concisely in plain text.", [], max_tokens=4000)
        return turn.text

    async def controller(self, user_id: str, action: str, arg: Any) -> dict:
        """Backs `service ai start|status|auto|stop|chat|code|agent`."""
        async with self.core.db.sessionmaker() as db:
            conv_id = self.active.get(user_id)
            conv = await db.get(AIConversation, conv_id) if conv_id else None
            if conv is None or conv.user_id != user_id:
                conv = (await db.execute(select(AIConversation).where(AIConversation.user_id == user_id)
                                         .order_by(AIConversation.updated_at.desc()).limit(1))).scalar_one_or_none()
            if action == "start" and conv is None:
                project = (await db.execute(select(Project).where(Project.user_id == user_id)
                                            .order_by(Project.updated_at.desc()).limit(1))).scalar_one_or_none()
                if project is None:
                    project = await self.core.projects.create(db, user_id, "My first project")
                conv = await self.create(db, user_id, project.id)
            if conv is None:
                return {"active": False, "message": "no MUR AI conversation yet - run 'service ai start'"}
            self.active[user_id] = conv.id
            if action in ("auto", "chat", "code", "agent"):
                if conv.id in self.running:
                    return {"error": "MUR AI is working; change the mode after it finishes", "mode": conv.mode}
                conv.mode = action
                conv.updated_at = time.time()
            elif action == "stop":
                self.stop(user_id, conv.id)
            await db.commit()
            keys = await self.list_keys(db, user_id)
            return {"active": True, "conversation": conv.id, "title": conv.title, "mode": conv.mode,
                    "provider": conv.provider, "running": conv.id in self.running,
                    "pending_approvals": len(self.approvals.list(user_id)),
                    "provider_configured": any(k["provider"] == conv.provider for k in keys)
                    or bool(self.settings.server_ai_key) or conv.provider == "scripted",
                    "action": action}
