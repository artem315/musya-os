"""Service Engine HTTP API and MUR Terminal."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from musya_runtime import MusyaError, parse
from musya_runtime.parser import ast as A
from pydantic import BaseModel, Field
from service_engine import ServiceError, describe_commands
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import Core
from ..db import AuditLog, User
from ..deps import current_user, get_core, get_db, limit

router = APIRouter(prefix="/api/engine", tags=["service-engine"])


class CommandBody(BaseModel):
    verb: str = Field(min_length=1, max_length=40)
    args: list[Any] = Field(default_factory=list, max_length=5)
    timeout: float | None = None
    wait: bool = True


class LineBody(BaseModel):
    line: str = Field(min_length=1, max_length=20_000)
    project_id: str


@router.get("/commands")
async def commands():
    return describe_commands()


@router.get("/status")
async def status(user: User = Depends(current_user), core: Core = Depends(get_core)):
    st = await core.engine.status(user.id)
    st["previews"] = core.previews.list(user.id)
    return st


@router.post("/command", dependencies=[Depends(limit("engine", 240))])
async def command(body: CommandBody, user: User = Depends(current_user), core: Core = Depends(get_core)):
    """Run one service command. With wait=false returns the queued job immediately."""
    if body.verb in ("ai", "install"):
        raise HTTPException(400, f"use the MUR AI / Package Manager API for 'service {body.verb}'")
    if len(core.engine.pool.sessions) >= core.settings.max_browser_sessions and user.id not in core.engine.pool.sessions:
        raise HTTPException(503, "all browser slots are busy - try again later")
    try:
        job = core.engine.submit(user.id, body.verb, body.args, source="api", timeout=body.timeout)
    except ServiceError as exc:
        raise HTTPException(400, {"type": exc.kind, "message": str(exc)}) from None
    if not body.wait:
        return job.to_dict()
    try:
        await job.future
    except ServiceError:
        pass
    return job.to_dict()


@router.post("/parse")
async def parse_line(body: LineBody, user: User = Depends(current_user)):
    """Parse a `service ...` line with the MUSYA parser (used by the Studio terminal for highlighting)."""
    try:
        prog = parse(body.line)
    except MusyaError as exc:
        return {"ok": False, "error": exc.to_dict()}
    cmds = [s.expr.verb for s in prog.body if isinstance(s, A.ExprStmt) and isinstance(s.expr, A.ServiceCommand)]
    return {"ok": True, "statements": len(prog.body), "service_commands": cmds}


@router.post("/terminal", dependencies=[Depends(limit("terminal", 240))])
async def terminal(body: LineBody, user: User = Depends(current_user), core: Core = Depends(get_core),
                   db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, body.project_id)
    root = core.projects.root(user.id, body.project_id)
    db.add(AuditLog(user_id=user.id, actor="user", event="terminal", detail={"line": body.line[:500]}))
    return await core.runner.terminal(user.id, body.project_id, root, body.line)


@router.get("/jobs")
async def jobs(limit_: int = 50, user: User = Depends(current_user), core: Core = Depends(get_core)):
    return core.engine.jobs(user.id, min(max(limit_, 1), 200))


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    return {"cancelled": core.engine.cancel(user.id, job_id)}


@router.post("/cancel-all")
async def cancel_all(user: User = Depends(current_user), core: Core = Depends(get_core)):
    return {"cancelled": core.engine.cancel(user.id)}


@router.get("/events")
async def events(user: User = Depends(current_user), core: Core = Depends(get_core)):
    return [{k: v for k, v in e.items() if k != "owner"} for e in core.engine.bus.history(user.id, 200)]


@router.get("/frame")
async def frame(quality: int = 55, user: User = Depends(current_user), core: Core = Depends(get_core)):
    """A live JPEG frame of the active tab for MUR Browser."""
    try:
        f = await core.engine.frame(user.id, max(20, min(quality, 90)))
    except Exception as exc:
        raise HTTPException(409, f"no frame available: {str(exc)[:200]}") from None
    if f is None:
        raise HTTPException(404, "browser is not running")
    return f


@router.get("/screenshots/{name}")
async def screenshot(name: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "bad name")
    p = core.engine.screenshots_dir_for(user.id) / name
    if not p.is_file():
        raise HTTPException(404, "screenshot not found")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
