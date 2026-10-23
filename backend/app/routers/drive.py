"""MUR Drive HTTP API."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import Core
from ..db import User
from ..deps import current_user, get_core, get_db, limit
from ..paths import clean_path
from ..services.drive import INLINE_SAFE, item_dict

router = APIRouter(prefix="/api/drive", tags=["drive"])


class PathBody(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class MoveBody(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    new_path: str = Field(min_length=1, max_length=1024)


class TextBody(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=2 * 1024 * 1024)


class ToProjectBody(BaseModel):
    path: str
    project_id: str
    dest: str = Field(default="", max_length=500)


@router.get("/list")
async def list_dir(path: str = "/", user: User = Depends(current_user), core: Core = Depends(get_core),
                   db: AsyncSession = Depends(get_db)):
    path = clean_path(path)
    return {"path": path, "items": await core.drive.list(db, user.id, path),
            "used": await core.drive.used_bytes(db, user.id), "quota": core.drive.quota_bytes}


@router.post("/mkdir")
async def mkdir(body: PathBody, user: User = Depends(current_user), core: Core = Depends(get_core),
                db: AsyncSession = Depends(get_db)):
    item = await core.drive.mkdir(db, user.id, body.path)
    core.engine.bus.publish(user.id, "drive.changed", path=item.path, action="created")
    return item_dict(item)


@router.post("/upload", dependencies=[Depends(limit("upload", 60))])
async def upload(file: UploadFile = File(...), path: str = Form("/"), overwrite: bool = Form(False),
                 user: User = Depends(current_user), core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    item = await core.drive.upload(db, user.id, path, file, overwrite)
    core.engine.bus.publish(user.id, "drive.changed", path=item.path, action="created")
    return item_dict(item)


@router.put("/text")
async def write_text(body: TextBody, user: User = Depends(current_user), core: Core = Depends(get_core),
                     db: AsyncSession = Depends(get_db)):
    item = await core.drive.write_text(db, user.id, body.path, body.content)
    core.engine.bus.publish(user.id, "drive.changed", path=item.path, action="modified")
    return item_dict(item)


@router.get("/download")
async def download(path: str, inline: bool = False, user: User = Depends(current_user), core: Core = Depends(get_core),
                   db: AsyncSession = Depends(get_db)):
    item = await core.drive.require(db, user.id, path)
    if item.is_dir:
        raise HTTPException(400, "cannot download a folder")
    disposition = "inline" if inline and item.mime in INLINE_SAFE else "attachment"
    media = item.mime if disposition == "inline" else "application/octet-stream"
    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(item.name)}",
               "Content-Length": str(item.size), "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=60"}
    return StreamingResponse(core.drive.stream(item), media_type=media, headers=headers)


@router.get("/preview")
async def preview(path: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                  db: AsyncSession = Depends(get_db)):
    item = await core.drive.require(db, user.id, path)
    return {**item_dict(item), **await core.drive.text_preview(item)}


@router.post("/rename")
async def rename(body: MoveBody, user: User = Depends(current_user), core: Core = Depends(get_core),
                 db: AsyncSession = Depends(get_db)):
    item = await core.drive.rename(db, user.id, body.path, body.new_path)
    core.engine.bus.publish(user.id, "drive.changed", path=item.path, action="renamed")
    return item_dict(item)


@router.post("/delete")
async def delete(body: PathBody, user: User = Depends(current_user), core: Core = Depends(get_core),
                 db: AsyncSession = Depends(get_db)):
    n = await core.drive.delete(db, user.id, body.path)
    core.engine.bus.publish(user.id, "drive.changed", path=clean_path(body.path), action="deleted")
    return {"deleted": n}


@router.get("/search")
async def search(q: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                 db: AsyncSession = Depends(get_db)):
    return {"query": q, "items": await core.drive.search(db, user.id, q[:200])}


@router.post("/to-project")
async def to_project(body: ToProjectBody, user: User = Depends(current_user), core: Core = Depends(get_core),
                     db: AsyncSession = Depends(get_db)):
    """Copy a Drive file/folder into a Service Studio project."""
    await core.projects.get(db, user.id, body.project_id)
    from ..paths import clean_rel
    dest = core.projects.path(user.id, body.project_id, clean_rel(body.dest) if body.dest else ".")
    await core.drive.export_tree(db, user.id, body.path, dest)
    core.engine.bus.publish(user.id, "project.file_changed", project=body.project_id, path=body.dest or ".",
                            action="imported")
    return {"ok": True}
