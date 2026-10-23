"""Service Studio projects: files, run/test/build, preview, export."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import Core
from ..db import User
from ..deps import current_user, get_core, get_db, limit
from ..paths import clean_rel
from ..services.projects import TEMPLATES, project_dict

router = APIRouter(prefix="/api/projects", tags=["projects"])


class NewProject(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    template: str = "blank"
    description: str = Field(default="", max_length=2000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    state: dict | None = None


class FileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str


class PathBody(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class RenameBody(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    new_path: str = Field(min_length=1, max_length=500)


class RunBody(BaseModel):
    path: str = Field(default="", max_length=500)
    test: bool = False


@router.get("")
async def list_projects(user: User = Depends(current_user), core: Core = Depends(get_core),
                        db: AsyncSession = Depends(get_db)):
    return {"projects": await core.projects.list(db, user.id), "templates": list(TEMPLATES)}


@router.post("")
async def create_project(body: NewProject, user: User = Depends(current_user), core: Core = Depends(get_core),
                         db: AsyncSession = Depends(get_db)):
    p = await core.projects.create(db, user.id, body.name, body.template, body.description)
    return project_dict(p)


@router.get("/{project_id}")
async def get_project(project_id: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                      db: AsyncSession = Depends(get_db)):
    p = await core.projects.get(db, user.id, project_id)
    return {**project_dict(p), "files": core.projects.tree(user.id, p.id),
            "previews": [x for x in core.previews.list(user.id) if x["key"] == p.id]}


@router.patch("/{project_id}")
async def patch_project(project_id: str, body: ProjectPatch, user: User = Depends(current_user),
                        core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    p = await core.projects.get(db, user.id, project_id)
    if body.name is not None and body.name.strip():
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description
    if body.state is not None:
        if len(str(body.state)) > 20_000:
            raise HTTPException(413, "state is too large")
        p.state = body.state
    p.updated_at = time.time()
    return project_dict(p)


@router.delete("/{project_id}")
async def delete_project(project_id: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                         db: AsyncSession = Depends(get_db)):
    core.previews.stop(user.id, project_id)
    await core.projects.delete(db, user.id, project_id)
    return {"ok": True}


@router.get("/{project_id}/files")
async def tree(project_id: str, user: User = Depends(current_user), core: Core = Depends(get_core),
               db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, project_id)
    return core.projects.tree(user.id, project_id)


@router.get("/{project_id}/file")
async def read_file(project_id: str, path: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                    db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, project_id)
    return core.projects.read(user.id, project_id, clean_rel(path))


@router.put("/{project_id}/file")
async def write_file(project_id: str, body: FileWrite, user: User = Depends(current_user),
                     core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    p = await core.projects.get(db, user.id, project_id)
    res = core.projects.write(user.id, project_id, clean_rel(body.path), body.content)
    p.updated_at = time.time()
    core.engine.bus.publish(user.id, "project.file_changed", project=project_id, path=res["path"], action="saved")
    return res


@router.post("/{project_id}/mkdir")
async def mkdir(project_id: str, body: PathBody, user: User = Depends(current_user), core: Core = Depends(get_core),
                db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, project_id)
    core.projects.mkdir(user.id, project_id, clean_rel(body.path))
    return {"ok": True}


@router.post("/{project_id}/delete")
async def delete_path(project_id: str, body: PathBody, user: User = Depends(current_user),
                      core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, project_id)
    core.projects.remove(user.id, project_id, clean_rel(body.path))
    return {"ok": True}


@router.post("/{project_id}/rename")
async def rename(project_id: str, body: RenameBody, user: User = Depends(current_user),
                 core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, project_id)
    core.projects.rename(user.id, project_id, clean_rel(body.path), clean_rel(body.new_path))
    return {"ok": True}


@router.post("/{project_id}/run", dependencies=[Depends(limit("run", 60))])
async def run(project_id: str, body: RunBody, user: User = Depends(current_user), core: Core = Depends(get_core),
              db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, project_id)
    root = core.projects.root(user.id, project_id)
    file = clean_rel(body.path) if body.path else None
    if file is None:
        from ..services.runner import manifest_permissions  # entry from app.yml
        try:
            manifest_permissions(root)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        from musya_runtime import load_manifest_file
        file = load_manifest_file(root / "app.yml").entry if (root / "app.yml").is_file() else "main.mysya"
    try:
        r = core.runner.start(user.id, project_id, root, file, test_mode=body.test)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return r.to_dict()


@router.get("/{project_id}/runs/{run_id}")
async def get_run(project_id: str, run_id: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    r = core.runner.get(user.id, run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return r.to_dict()


@router.post("/{project_id}/runs/{run_id}/stop")
async def stop_run(project_id: str, run_id: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    return {"stopped": core.runner.stop(user.id, run_id)}


@router.post("/{project_id}/build")
async def build(project_id: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, project_id)
    return core.projects.build(user.id, project_id)


@router.post("/{project_id}/export")
async def export(project_id: str, user: User = Depends(current_user), core: Core = Depends(get_core),
                 db: AsyncSession = Depends(get_db)):
    """'Deploy' in Studio: validate, then save a versioned zip of the project into MUR Drive /Exports."""
    p = await core.projects.get(db, user.id, project_id)
    report = core.projects.build(user.id, project_id)
    if not report["ok"]:
        raise HTTPException(400, {"message": "fix build errors before exporting", "report": report})
    import tempfile
    from pathlib import Path
    data = core.projects.zip_bytes(user.id, project_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        tmp.write(data)
    try:
        name = f"{p.slug}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        item = await core.drive.import_local(db, user.id, "/Exports", Path(tmp.name), name)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    core.engine.bus.publish(user.id, "drive.changed", path=item.path, action="created")
    return {"path": item.path, "size": item.size, "report": report}


@router.post("/{project_id}/preview/start")
async def preview_start(project_id: str, body: PathBody | None = None, user: User = Depends(current_user),
                        core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    await core.projects.get(db, user.id, project_id)
    folder = core.projects.path(user.id, project_id, clean_rel(body.path) if body else ".")
    try:
        pv = core.previews.start(user.id, project_id, folder)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    rel = folder.relative_to(core.projects.root(user.id, project_id)).as_posix() or "."
    return {**pv.to_dict(), "root": rel, "user_url": f"/p/{sign_preview(core, user.id, project_id, rel)}/index.html"}


@router.post("/{project_id}/preview/stop")
async def preview_stop(project_id: str, user: User = Depends(current_user), core: Core = Depends(get_core)):
    return {"stopped": core.previews.stop(user.id, project_id)}


@router.get("/{project_id}/preview-link")
async def preview_link(project_id: str, folder: str = ".", user: User = Depends(current_user),
                       core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)):
    """A short-lived capability URL for the Studio Preview tab (works inside a sandboxed iframe)."""
    await core.projects.get(db, user.id, project_id)
    rel = clean_rel(folder)
    token = sign_preview(core, user.id, project_id, rel)
    return {"url": f"/p/{token}/index.html", "expires_in": PREVIEW_TTL}


PREVIEW_TTL = 12 * 3600


def sign_preview(core: Core, user_id: str, project_id: str, folder: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"u": user_id, "p": project_id, "f": folder, "e": int(time.time()) + PREVIEW_TTL},
        separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(core.settings.secret_key.encode(), b"preview:" + payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{sig}"


def verify_preview(core: Core, token: str) -> dict:
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(core.settings.secret_key.encode(), b"preview:" + payload.encode(),
                            hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data["e"] < time.time():
            raise ValueError
        return data
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(404, "preview link is invalid or expired") from None


preview_router = APIRouter(tags=["preview"])


@preview_router.get("/p/{token}/{path:path}")
async def preview_file(token: str, path: str, core: Core = Depends(get_core)):
    """Serve project files for the Preview tab. Sandboxed CSP: pages get an opaque origin and cannot
    act as the user against the MURCLOUD API."""
    data = verify_preview(core, token)
    base = clean_rel(data["f"])
    rel = clean_rel(path or "index.html")
    target = core.projects.path(data["u"], data["p"], rel if base == "." else f"{base}/{rel}")
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        raise HTTPException(404, "not found")
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix in (".mysya", ".yml", ".driver"):
        mime = "text/plain"
    headers = {"Content-Security-Policy": "sandbox allow-scripts allow-forms allow-modals allow-popups",
               "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
    return FileResponse(target, media_type=mime, headers=headers)
