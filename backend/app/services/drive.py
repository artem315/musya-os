"""MUR Drive: per-user file storage with folders, search, previews and quotas."""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import tempfile
import time
from pathlib import Path
from typing import AsyncIterator

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import DriveItem, new_id
from ..paths import clean_name, clean_path, join, parent_of
from ..storage import Storage

TEXT_PREVIEW_BYTES = 256 * 1024
# Types the browser may render inline. Everything else is served as an attachment.
INLINE_SAFE = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "application/pdf", "audio/mpeg",
               "audio/ogg", "audio/wav", "video/mp4", "video/webm", "text/plain"}
TEXT_EXT = {".txt", ".md", ".mysya", ".yml", ".yaml", ".driver", ".json", ".js", ".ts", ".css", ".html", ".py",
            ".csv", ".xml", ".svg", ".log", ".toml", ".ini", ".sh"}
DEFAULT_FOLDERS = ["/Documents", "/Downloads", "/Screenshots", "/Exports"]


def guess_mime(name: str) -> str:
    mime, _ = mimetypes.guess_type(name)
    if name.endswith((".mysya", ".driver", ".yml", ".yaml")):
        return "text/plain"
    return mime or "application/octet-stream"


def item_dict(item: DriveItem) -> dict:
    return {"id": item.id, "path": item.path, "name": item.name, "parent": item.parent, "is_dir": item.is_dir,
            "size": item.size, "mime": item.mime, "sha256": item.sha256, "created_at": item.created_at,
            "updated_at": item.updated_at}


class Drive:
    def __init__(self, storage: Storage, max_upload_bytes: int, quota_bytes: int):
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes
        self.quota_bytes = quota_bytes

    async def get(self, db: AsyncSession, user_id: str, path: str) -> DriveItem | None:
        path = clean_path(path)
        if path == "/":
            return None
        return (await db.execute(select(DriveItem).where(DriveItem.user_id == user_id,
                                                         DriveItem.path == path))).scalar_one_or_none()

    async def require(self, db: AsyncSession, user_id: str, path: str) -> DriveItem:
        item = await self.get(db, user_id, path)
        if item is None:
            raise HTTPException(404, f"'{path}' not found")
        return item

    async def used_bytes(self, db: AsyncSession, user_id: str) -> int:
        total = (await db.execute(select(func.coalesce(func.sum(DriveItem.size), 0))
                                  .where(DriveItem.user_id == user_id))).scalar_one()
        return int(total or 0)

    async def ensure_defaults(self, db: AsyncSession, user_id: str) -> None:
        for folder in DEFAULT_FOLDERS:
            await self.mkdir(db, user_id, folder, exist_ok=True)

    async def list(self, db: AsyncSession, user_id: str, parent: str) -> list[dict]:
        parent = clean_path(parent)
        if parent != "/":
            folder = await self.require(db, user_id, parent)
            if not folder.is_dir:
                raise HTTPException(400, "not a folder")
        rows = (await db.execute(select(DriveItem).where(DriveItem.user_id == user_id, DriveItem.parent == parent)
                                 .order_by(DriveItem.is_dir.desc(), DriveItem.name))).scalars().all()
        return [item_dict(r) for r in rows]

    async def mkdir(self, db: AsyncSession, user_id: str, path: str, exist_ok: bool = False) -> DriveItem:
        path = clean_path(path)
        if path == "/":
            raise HTTPException(400, "root already exists")
        existing = await self.get(db, user_id, path)
        if existing is not None:
            if existing.is_dir and exist_ok:
                return existing
            raise HTTPException(409, f"'{path}' already exists")
        parent = parent_of(path)
        if parent != "/":
            await self.mkdir(db, user_id, parent, exist_ok=True)
        item = DriveItem(user_id=user_id, path=path, parent=parent, name=path.rsplit("/", 1)[-1], is_dir=True,
                         mime="inode/directory")
        db.add(item)
        await db.flush()
        return item

    async def _unique_path(self, db: AsyncSession, user_id: str, parent: str, name: str) -> str:
        candidate = join(parent, name)
        if await self.get(db, user_id, candidate) is None:
            return candidate
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        for i in range(1, 1000):
            cand = join(parent, f"{stem} ({i}){'.' + ext if ext else ''}")
            if await self.get(db, user_id, cand) is None:
                return cand
        raise HTTPException(409, "too many files with this name")

    async def store_file(self, db: AsyncSession, user_id: str, parent: str, name: str, tmp: Path, *,
                         overwrite: bool = False) -> DriveItem:
        """Register a finished temp file in the Drive (quota-checked)."""
        parent = clean_path(parent)
        name = clean_name(name)
        size = tmp.stat().st_size
        if size > self.max_upload_bytes:
            raise HTTPException(413, "file is larger than the upload limit")
        if parent != "/":
            await self.mkdir(db, user_id, parent, exist_ok=True)
        target = join(parent, name)
        existing = await self.get(db, user_id, target)
        if existing is not None and existing.is_dir:
            raise HTTPException(409, f"a folder named '{name}' exists")
        if existing is not None and not overwrite:
            target = await self._unique_path(db, user_id, parent, name)
            existing = None
        used = await self.used_bytes(db, user_id) - (existing.size if existing else 0)
        if used + size > self.quota_bytes:
            raise HTTPException(507, "MUR Drive quota exceeded")
        sha = hashlib.sha256()
        with open(tmp, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                sha.update(chunk)
        item = existing or DriveItem(id=new_id(), user_id=user_id, path=target, parent=parent,
                                     name=target.rsplit("/", 1)[-1])
        item.storage_key = item.storage_key or f"{user_id}/{item.id}"
        await self.storage.put_file(item.storage_key, tmp)
        item.size, item.sha256, item.mime, item.is_dir = size, sha.hexdigest(), guess_mime(item.name), False
        item.updated_at = time.time()
        if existing is None:
            db.add(item)
        await db.flush()
        return item

    async def upload(self, db: AsyncSession, user_id: str, parent: str, upload: UploadFile,
                     overwrite: bool = False) -> DriveItem:
        name = clean_name((upload.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1])
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            size = 0
            try:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise HTTPException(413, f"file is larger than {self.max_upload_bytes // (1024 * 1024)} MB")
                    tmp.write(chunk)
                tmp.flush()
                return await self.store_file(db, user_id, parent, name, Path(tmp.name), overwrite=overwrite)
            finally:
                Path(tmp.name).unlink(missing_ok=True)

    async def write_text(self, db: AsyncSession, user_id: str, path: str, content: str) -> DriveItem:
        path = clean_path(path)
        data = content.encode("utf-8")
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
        try:
            return await self.store_file(db, user_id, parent_of(path), path.rsplit("/", 1)[-1], Path(tmp.name),
                                         overwrite=True)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    async def import_local(self, db: AsyncSession, user_id: str, folder: str, src: Path,
                           name: str | None = None) -> DriveItem:
        return await self.store_file(db, user_id, folder, name or src.name, src)

    async def stream(self, item: DriveItem) -> AsyncIterator[bytes]:
        async for chunk in self.storage.open_stream(item.storage_key):
            yield chunk

    async def text_preview(self, item: DriveItem) -> dict:
        ext = Path(item.name).suffix.lower()
        if item.is_dir:
            raise HTTPException(400, "folders have no preview")
        if ext in TEXT_EXT or item.mime.startswith("text/"):
            raw = await self.storage.read_bytes(item.storage_key, TEXT_PREVIEW_BYTES)
            return {"kind": "text", "text": raw.decode("utf-8", errors="replace"),
                    "truncated": item.size > TEXT_PREVIEW_BYTES}
        if item.mime in INLINE_SAFE:
            kind = item.mime.split("/")[0] if item.mime != "application/pdf" else "pdf"
            return {"kind": kind}
        return {"kind": "binary"}

    async def rename(self, db: AsyncSession, user_id: str, path: str, new_path: str) -> DriveItem:
        item = await self.require(db, user_id, path)
        new_path = clean_path(new_path)
        if new_path == item.path:
            return item
        if await self.get(db, user_id, new_path) is not None:
            raise HTTPException(409, f"'{new_path}' already exists")
        if item.is_dir and (new_path + "/").startswith(item.path + "/"):
            raise HTTPException(400, "cannot move a folder into itself")
        new_parent = parent_of(new_path)
        if new_parent != "/":
            parent_item = await self.mkdir(db, user_id, new_parent, exist_ok=True)
            if not parent_item.is_dir:
                raise HTTPException(400, "destination parent is a file")
        old = item.path
        item.path, item.parent, item.name, item.updated_at = new_path, new_parent, new_path.rsplit("/", 1)[-1], time.time()
        if item.is_dir:
            rows = (await db.execute(select(DriveItem).where(DriveItem.user_id == user_id,
                                                             DriveItem.path.like(_like_prefix(old), escape="\\"))
                                     .execution_options(populate_existing=True))).scalars().all()
            for r in rows:
                if r.id == item.id:
                    continue
                r.path = new_path + r.path[len(old):]
                r.parent = new_path + r.parent[len(old):]
        await db.flush()
        return item

    async def delete(self, db: AsyncSession, user_id: str, path: str) -> int:
        item = await self.require(db, user_id, path)
        victims = [item]
        if item.is_dir:
            victims += (await db.execute(select(DriveItem).where(DriveItem.user_id == user_id,
                                                                 DriveItem.path.like(_like_prefix(item.path), escape="\\"))
                                         )).scalars().all()
        for v in victims:
            if not v.is_dir and v.storage_key:
                await self.storage.delete(v.storage_key)
            await db.delete(v)
        await db.flush()
        return len(victims)

    async def search(self, db: AsyncSession, user_id: str, query: str, limit: int = 100) -> list[dict]:
        q = query.strip()
        if not q:
            return []
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = (await db.execute(select(DriveItem).where(DriveItem.user_id == user_id,
                                                         or_(DriveItem.name.ilike(pattern, escape="\\")))
                                 .order_by(DriveItem.updated_at.desc()).limit(limit))).scalars().all()
        return [item_dict(r) for r in rows]

    async def export_tree(self, db: AsyncSession, user_id: str, path: str, dest: Path) -> None:
        """Copy a Drive folder to a local directory (used to import into projects)."""
        item = await self.require(db, user_id, path)
        if not item.is_dir:
            await self.storage.copy_to_path(item.storage_key, dest / item.name)
            return
        rows = (await db.execute(select(DriveItem).where(DriveItem.user_id == user_id,
                                                         DriveItem.path.like(_like_prefix(item.path), escape="\\")))).scalars().all()
        for r in rows:
            rel = r.path[len(item.path) + 1:]
            target = dest / rel
            if r.is_dir:
                target.mkdir(parents=True, exist_ok=True)
            else:
                await self.storage.copy_to_path(r.storage_key, target)


def _like_prefix(path: str) -> str:
    return path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


def safe_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
