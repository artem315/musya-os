"""Folder mirroring for hosts without a persistent disk (MUR_STORAGE=database).

Scripts, the MUSYA runtime, the Service Engine and preview servers need real folders,
so projects and installed packages stay on the local filesystem. On Render Free that
filesystem is wiped on every restart, so with ``MUR_STORAGE=database`` every project /
package folder is mirrored into the ``stored_files`` table:

* changes are marked dirty (API writes, AI tools, script runs, terminal lines, installs)
  and flushed a few seconds later, and once more on shutdown;
* at startup every mirrored folder is written back before the app serves requests.

Only changed files are written (compared by SHA-256); deleted files are removed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path

from musya_runtime import safe_join
from sqlalchemy import delete, select

from ..db import StoredFile

log = logging.getLogger("murcloud.persist")

MAX_FILE = 5 * 1024 * 1024
MAX_SCOPE = 50 * 1024 * 1024
FLUSH_DELAY = 3.0


class FolderMirror:
    def __init__(self, core):
        self.core = core
        self.enabled = core.settings.storage_backend == "database"
        self.dirty: set[tuple[str, str]] = set()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ scopes
    def folder(self, user_id: str, scope: str) -> Path:
        kind, _, key = scope.partition(":")
        if kind == "project":
            return self.core.projects.root(user_id, key)
        if kind == "package":
            return self.core.packages.install_dir(user_id, key)
        raise ValueError(f"unknown scope {scope!r}")

    def mark(self, user_id: str, scope: str) -> None:
        if self.enabled and user_id and scope:
            self.dirty.add((user_id, scope))

    def mark_project(self, user_id: str, project_id: str) -> None:
        if project_id.startswith("pkg:"):
            self.mark(user_id, "package:" + project_id[4:])
        else:
            self.mark(user_id, "project:" + project_id)

    # ------------------------------------------------------------ save
    @staticmethod
    def _scan(root: Path) -> dict[str, tuple[str, int, bytes]]:
        files: dict[str, tuple[str, int, bytes]] = {}
        total = 0
        if not root.is_dir():
            return files
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if p.is_dir():
                files[rel.as_posix() + "/"] = ("", 0, b"")
                continue
            if not p.is_file():
                continue
            size = p.stat().st_size
            if size > MAX_FILE or total + size > MAX_SCOPE:
                log.warning("not persisting %s (%d bytes): over the mirror limit", p, size)
                continue
            data = p.read_bytes()
            total += size
            files[rel.as_posix()] = (hashlib.sha256(data).hexdigest(), size, data)
        return files

    async def snapshot(self, user_id: str, scope: str) -> None:
        root = self.folder(user_id, scope)
        files = await asyncio.to_thread(self._scan, root)
        async with self.core.db.sessionmaker() as db:
            rows = {r.path: r for r in (await db.execute(
                select(StoredFile).where(StoredFile.user_id == user_id, StoredFile.scope == scope))).scalars()}
            for path, (sha, size, data) in files.items():
                row = rows.pop(path, None)
                if row is None:
                    db.add(StoredFile(user_id=user_id, scope=scope, path=path, sha256=sha, size=size, data=data))
                elif row.sha256 != sha:
                    row.sha256, row.size, row.data = sha, size, data
            for row in rows.values():  # deleted locally
                await db.delete(row)
            await db.commit()

    async def remove(self, user_id: str, scope: str) -> None:
        self.dirty.discard((user_id, scope))
        if not self.enabled:
            return
        async with self.core.db.sessionmaker() as db:
            await db.execute(delete(StoredFile).where(StoredFile.user_id == user_id, StoredFile.scope == scope))
            await db.commit()

    async def flush(self) -> None:
        async with self._lock:
            pending, self.dirty = self.dirty, set()
            for user_id, scope in pending:
                try:
                    await self.snapshot(user_id, scope)
                except Exception:
                    log.exception("could not persist %s/%s", user_id, scope)
                    self.dirty.add((user_id, scope))

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_DELAY)
            if self.dirty:
                await self.flush()

    # ------------------------------------------------------------ restore
    async def restore_all(self) -> int:
        """Write every mirrored folder back to disk (called once at startup)."""
        count = 0
        async with self.core.db.sessionmaker() as db:
            result = await db.stream(select(StoredFile).order_by(StoredFile.user_id, StoredFile.scope,
                                                                 StoredFile.path))
            current: tuple[str, str] | None = None
            root: Path | None = None
            async for row in result.scalars():
                if (row.user_id, row.scope) != current:
                    current = (row.user_id, row.scope)
                    try:
                        root = self.folder(row.user_id, row.scope)
                    except ValueError:
                        root = None
                        continue
                    if root.exists():
                        shutil.rmtree(root)  # the database copy is authoritative after a restart
                    root.mkdir(parents=True, exist_ok=True)
                if root is None:
                    continue
                target = safe_join(root, row.path.rstrip("/"))
                if row.path.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(row.data)
                    count += 1
        return count

    async def start(self) -> None:
        if not self.enabled:
            return
        restored = await self.restore_all()
        log.info("restored %d mirrored files from the database", restored)
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self.enabled:
            await self.flush()
