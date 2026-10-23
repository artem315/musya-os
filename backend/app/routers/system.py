"""System info, settings, packages, app state, MUR PC, health."""

from __future__ import annotations

import os
import platform
import time
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import Core
from ..db import AppState, User
from ..deps import current_user, get_core, get_db
from .auth import user_dict

router = APIRouter(prefix="/api", tags=["system"])
VERSION = "10.0.0-beta"
ALLOWED_SETTINGS = {"theme", "accent", "wallpaper", "ai_mode", "ai_provider", "language", "reduce_motion",
                    "sound", "dock", "font_size", "desktop_icons"}


@router.get("/health")
async def health(core: Core = Depends(get_core)):
    return {"ok": True, "version": VERSION, "uptime": round(time.time() - core.started_at)}


@router.get("/system/info")
async def info(user: User = Depends(current_user), core: Core = Depends(get_core)):
    return {"name": "MURCLOUD OS", "edition": "10 000 ULTRA", "version": VERSION, "developer": "ARTYONIX STUDIOS",
            "python": platform.python_version(), "platform": platform.platform(terse=True),
            "browser": core.engine.pool.version, "storage": core.settings.storage_backend,
            "database": core.db.engine.url.get_backend_name(), "pc": await core.pc.availability(),
            "registration_open": core.settings.registration_open}


@router.get("/system/stats")
async def stats(user: User = Depends(current_user), core: Core = Depends(get_core),
                db: AsyncSession = Depends(get_db)):
    """Real host metrics (the server MURCLOUD runs on)."""
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(core.settings.data_dir))
    proc = psutil.Process(os.getpid())
    st = await core.engine.status(user.id)
    return {"cpu_percent": psutil.cpu_percent(interval=0.2), "cpu_count": psutil.cpu_count(),
            "load": os.getloadavg() if hasattr(os, "getloadavg") else None,
            "memory": {"total": vm.total, "used": vm.used, "percent": vm.percent},
            "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
            "process": {"rss": proc.memory_info().rss, "threads": proc.num_threads()},
            "uptime": round(time.time() - core.started_at),
            "browser_sessions": len(core.engine.pool.sessions), "my_browser": st["running"],
            "my_queue": st["queue"], "ai_running": sum(1 for c in core.ai._owner_of.values() if c == user.id),
            "runs": len(core.runner.active(user.id)), "previews": len(core.previews.list(user.id)),
            "drive_used": await core.drive.used_bytes(db, user.id), "drive_quota": core.drive.quota_bytes}


class SettingsBody(BaseModel):
    settings: dict[str, Any]
    display_name: str | None = Field(default=None, max_length=80)


@router.patch("/settings")
async def update_settings(body: SettingsBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    bad = set(body.settings) - ALLOWED_SETTINGS
    if bad:
        raise HTTPException(400, f"unknown settings: {', '.join(sorted(bad))}")
    for k, v in body.settings.items():
        if not isinstance(v, (str, int, float, bool, list)) or len(str(v)) > 2000:
            raise HTTPException(400, f"invalid value for {k}")
    user.settings = {**(user.settings or {}), **body.settings}
    if body.display_name:
        user.display_name = body.display_name.strip()
    return user_dict(user)


# ------------------------------------------------------------------ app state

class StateBody(BaseModel):
    value: dict


APPS = {"soup", "gaming", "desktop", "rtx", "browser", "studio"}


@router.get("/apps/{app}/state/{key}")
async def get_state(app: str, key: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if app not in APPS or len(key) > 80:
        raise HTTPException(404, "unknown app")
    row = (await db.execute(select(AppState).where(AppState.user_id == user.id, AppState.app == app,
                                                   AppState.key == key))).scalar_one_or_none()
    return {"value": row.value if row else None, "updated_at": row.updated_at if row else None}


@router.put("/apps/{app}/state/{key}")
async def put_state(app: str, key: str, body: StateBody, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db)):
    if app not in APPS or len(key) > 80:
        raise HTTPException(404, "unknown app")
    if len(str(body.value)) > 64_000:
        raise HTTPException(413, "state is too large")
    row = (await db.execute(select(AppState).where(AppState.user_id == user.id, AppState.app == app,
                                                   AppState.key == key))).scalar_one_or_none()
    if row is None:
        row = AppState(user_id=user.id, app=app, key=key)
        db.add(row)
    row.value, row.updated_at = body.value, time.time()
    return {"ok": True, "updated_at": row.updated_at}


# ------------------------------------------------------------------ packages

class InstallBody(BaseModel):
    approved_permissions: list[str] = Field(default_factory=list)


class InstallFromProject(BaseModel):
    project_id: str
    approved_permissions: list[str] = Field(default_factory=list)


@router.get("/packages")
async def packages(user: User = Depends(current_user), core: Core = Depends(get_core),
                   db: AsyncSession = Depends(get_db)):
    return {"catalog": core.packages.catalog(), "installed": await core.packages.installed(db, user.id)}


@router.post("/packages/{package_id}/install")
async def install(package_id: str, body: InstallBody, user: User = Depends(current_user),
                  core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    return await core.packages.install(db, user.id, package_id, approved_permissions=body.approved_permissions)


@router.post("/packages/install-from-project")
async def install_from_project(body: InstallFromProject, user: User = Depends(current_user),
                               core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, body.project_id)
    return await core.packages.install(db, user.id, "", approved_permissions=body.approved_permissions,
                                       source_dir=core.projects.root(user.id, body.project_id), source="project")


@router.delete("/packages/{package_id}")
async def uninstall(package_id: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                    db: AsyncSession = Depends(get_db)):
    await core.packages.uninstall(db, user.id, package_id)
    return {"ok": True}


@router.post("/packages/{package_id}/run")
async def run_package(package_id: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                      db: AsyncSession = Depends(get_db)):
    row = await core.packages.get_installed(db, user.id, package_id)
    root = core.packages.install_dir(user.id, package_id)
    try:
        run = core.runner.start(user.id, f"pkg:{package_id}", root, row.manifest.get("entry", "main.mysya"),
                                permissions=set(row.permissions), source="package")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return run.to_dict()


# ------------------------------------------------------------------ MUR PC

class PCCreate(BaseModel):
    name: str = Field(default="MUR PC", max_length=40)
    resolution: str = "1280x800"


class PCLaunch(BaseModel):
    app: str


@router.get("/pc")
async def pc_list(user: User = Depends(current_user), core: Core = Depends(get_core)):
    avail = await core.pc.availability()
    sessions = await core.pc.list(user.id) if avail["available"] else []
    return {"availability": avail, "sessions": sessions}


@router.post("/pc")
async def pc_create(body: PCCreate, user: User = Depends(current_user), core: Core = Depends(get_core)):
    return await core.pc.create(user.id, body.name, body.resolution)


@router.post("/pc/{pc_id}/{action}")
async def pc_action(pc_id: str, action: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    return await core.pc.action(user.id, pc_id, action)


@router.post("/pc/{pc_id}/launch/app")
async def pc_launch(pc_id: str, body: PCLaunch, user: User = Depends(current_user), core: Core = Depends(get_core)):
    return await core.pc.launch(user.id, pc_id, body.app)


@router.get("/pc/{pc_id}/stats")
async def pc_stats(pc_id: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    return await core.pc.stats(user.id, pc_id)


@router.get("/pc/{pc_id}/connect")
async def pc_connect(pc_id: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    _, _, password = await core.pc.target(user.id, pc_id)
    return {"ws_path": f"/api/pc/{pc_id}/vnc", "password": password}
