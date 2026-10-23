"""Application container: creates and wires every MURCLOUD service."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from service_engine import BrowserPool, ServiceEngine
from service_engine.preview import PreviewManager

from .config import Settings
from .db import Database
from .security import RateLimiter, SecretBox
from .services.ai import AIService
from .services.drive import Drive
from .services.packages import Packages
from .services.pc import PCManager
from .services.persist import FolderMirror
from .services.projects import Projects
from .services.runner import Runner
from .storage import make_storage

log = logging.getLogger("murcloud")


class Core:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.started_at = time.time()
        self.db = Database(settings.database_url)
        self.storage = make_storage(settings, self.db)
        self.secretbox = SecretBox(settings.secret_key)
        self.limiter = RateLimiter()
        self.drive = Drive(self.storage, settings.max_upload_mb * 1024 * 1024, settings.user_quota_mb * 1024 * 1024)
        self.projects = Projects(settings)
        lean = ["--renderer-process-limit=1", "--disable-extensions", "--disable-background-networking",
                "--disable-component-update", "--js-flags=--max-old-space-size=160"]
        self.engine = ServiceEngine(headless=settings.headless, allow_private=settings.ssrf_allow_private,
                                    allowed_hosts=set(settings.ssrf_allow_hosts),
                                    max_download_bytes=settings.max_upload_mb * 1024 * 1024,
                                    pool=BrowserPool(headless=settings.headless,
                                                     launch_args=lean if settings.low_memory else None),
                                    idle_timeout=300.0 if settings.low_memory else 1800.0)
        self.engine.dir_resolver = self._engine_dir
        self.previews = PreviewManager(self.engine)
        self.ai = AIService(self)
        self.runner = Runner(self.engine, ai_ask=self.ai.ask)
        self.packages = Packages(self)
        self.mirror = FolderMirror(self)
        self.projects.on_change = self.mirror.mark_project
        self.pc = PCManager(settings, self.secretbox)
        self.engine.ai_controller = self.ai.controller
        self.engine.package_installer = self.packages.service_install
        self.engine.bus.add_listener(self._on_event)
        self._import_tasks: set[asyncio.Task] = set()

    def _engine_dir(self, owner: str, kind: str) -> Path:
        return self.settings.user_dir(owner) / "engine" / kind

    def _on_event(self, event: dict) -> None:
        """Copy browser downloads and screenshots into the owner's MUR Drive; mark changed folders."""
        kind = event.get("type")
        owner = event.get("owner", "")
        if kind == "project.file_changed" and event.get("project"):
            self.mirror.mark_project(owner, str(event["project"]))
        elif kind == "run.finished":
            self.mirror.mark_project(owner, str((event.get("run") or {}).get("project_id", "")))
        elif kind == "package.installed" and event.get("package"):
            self.mirror.mark(owner, f"package:{event['package']}")
        if kind == "browser.download_finished":
            folder, sub, delete = "/Downloads", "downloads", True
        elif kind == "browser.screenshot":
            folder, sub, delete = "/Screenshots", "screenshots", False
        else:
            return
        name = event.get("filename")
        if not name or "/" in name:
            return
        path = self._engine_dir(event["owner"], sub) / name
        task = asyncio.ensure_future(self._import(event["owner"], folder, path, delete=delete))
        self._import_tasks.add(task)
        task.add_done_callback(self._import_tasks.discard)

    async def _import(self, user_id: str, folder: str, path: Path, delete: bool) -> None:
        if not path.is_file():
            return
        try:
            async with self.db.sessionmaker() as db:
                item = await self.drive.import_local(db, user_id, folder, path)
                await db.commit()
            self.engine.bus.publish(user_id, "drive.changed", path=item.path, action="created")
            if delete:
                path.unlink(missing_ok=True)
        except Exception as exc:  # quota exceeded etc. - tell the user
            self.engine.bus.publish(user_id, "drive.import_failed", file=path.name, reason=str(exc)[:200])

    async def startup(self) -> None:
        await self.db.create_all()
        await self.mirror.start()  # MUR_STORAGE=database: bring projects back after a restart
        log.info("MURCLOUD 10.0.0 Beta backend started (db=%s, storage=%s)", self.db.engine.url.get_backend_name(),
                 self.settings.storage_backend)

    async def shutdown(self) -> None:
        self.previews.stop_all()
        tasks = list(self.ai.running.values())
        for t in tasks:
            t.cancel()
        if tasks:  # let cancelled turns save their transcripts
            await asyncio.wait(tasks, timeout=10)
        await self.engine.shutdown()
        await self.mirror.stop()  # last flush before the database closes
        await self.db.dispose()
