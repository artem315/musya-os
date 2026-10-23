"""MUR AI HTTP API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import Core
from ..db import User
from ..deps import current_user, get_core, get_db, limit
from ..services.ai import conv_summary

router = APIRouter(prefix="/api/ai", tags=["mur-ai"])


class KeyBody(BaseModel):
    provider: str
    api_key: str = Field(min_length=10, max_length=500)
    model: str = Field(default="", max_length=100)


class NewConversation(BaseModel):
    project_id: str
    mode: str = "agent"
    provider: str = "anthropic"
    model: str = Field(default="", max_length=100)
    permissions: list[str] | None = None


class PatchConversation(BaseModel):
    mode: str | None = None
    permissions: list[str] | None = None
    provider: str | None = None
    model: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=120)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ApprovalBody(BaseModel):
    decision: str  # once | session | deny


@router.get("/providers")
async def providers(user: User = Depends(current_user), core: Core = Depends(get_core),
                    db: AsyncSession = Depends(get_db)):
    return {**core.ai.providers(), "keys": await core.ai.list_keys(db, user.id)}


@router.put("/keys")
async def set_key(body: KeyBody, user: User = Depends(current_user), core: Core = Depends(get_core),
                  db: AsyncSession = Depends(get_db)):
    return await core.ai.set_key(db, user.id, body.provider, body.api_key, body.model)


@router.delete("/keys/{provider}")
async def delete_key(provider: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                     db: AsyncSession = Depends(get_db)):
    await core.ai.delete_key(db, user.id, provider)
    return {"ok": True}


@router.get("/conversations")
async def list_conversations(project_id: str | None = None, user: User = Depends(current_user),
                             core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    return await core.ai.list(db, user.id, project_id)


@router.post("/conversations")
async def create_conversation(body: NewConversation, user: User = Depends(current_user),
                              core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    conv = await core.ai.create(db, user.id, body.project_id, mode=body.mode, provider=body.provider,
                                model=body.model, permissions=body.permissions)
    return conv_summary(conv)


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                           db: AsyncSession = Depends(get_db)):
    conv = await core.ai.get(db, user.id, conv_id)
    return {**conv_summary(conv, conv.id in core.ai.running), "transcript": conv.transcript,
            "approvals": [a for a in core.ai.approvals.list(user.id) if a.get("conversation") == conv_id]}


@router.patch("/conversations/{conv_id}")
async def patch_conversation(conv_id: str, body: PatchConversation, user: User = Depends(current_user),
                             core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    if conv_id in core.ai.running and (body.mode is not None or body.permissions is not None):
        raise HTTPException(409, "wait until MUR AI finishes (or stop it) before changing mode or permissions")
    conv = await core.ai.update(db, user.id, conv_id, mode=body.mode, permissions=body.permissions,
                                provider=body.provider, model=body.model, title=body.title)
    return conv_summary(conv)


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                              db: AsyncSession = Depends(get_db)):
    await core.ai.delete(db, user.id, conv_id)
    return {"ok": True}


@router.post("/conversations/{conv_id}/messages", dependencies=[Depends(limit("ai", 30))])
async def send_message(conv_id: str, body: Message, user: User = Depends(current_user),
                       core: Core = Depends(get_core)):
    return await core.ai.send(user.id, conv_id, body.text)


@router.post("/conversations/{conv_id}/stop")
async def stop(conv_id: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    return {"stopped": core.ai.stop(user.id, conv_id)}


@router.get("/approvals")
async def approvals(user: User = Depends(current_user), core: Core = Depends(get_core)):
    return core.ai.approvals.list(user.id)


@router.post("/approvals/{approval_id}")
async def decide(approval_id: str, body: ApprovalBody, user: User = Depends(current_user),
                 core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    if body.decision not in ("once", "session", "deny"):
        raise HTTPException(400, "decision must be once, session or deny")
    if not core.ai.approve(user.id, approval_id, body.decision):
        raise HTTPException(404, "approval request not found or already answered")
    await core.ai.audit(db, user.id, "user", "approval_decision", detail={"approval": approval_id,
                                                                         "decision": body.decision})
    return {"ok": True}


@router.get("/audit")
async def audit(limit_: int = 200, user: User = Depends(current_user), core: Core = Depends(get_core),
                db: AsyncSession = Depends(get_db)):
    return await core.ai.audit_log(db, user.id, min(max(limit_, 1), 1000))


@router.post("/control/{action}")
async def control(action: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    """Same as `service ai <action>` in the terminal."""
    if action not in ("start", "status", "auto", "stop", "chat", "code", "agent"):
        raise HTTPException(400, "unknown action")
    return await core.ai.controller(user.id, action, None)
