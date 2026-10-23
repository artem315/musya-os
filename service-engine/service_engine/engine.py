"""The MUSYA Service Engine: per-owner command queues on top of Playwright.

Every owner has one FIFO queue and one worker task, so browser actions from
the Studio terminal, ``.mysya`` scripts and MUR AI never interleave inside a
single page. Each submitted command becomes a :class:`Job` with its own
timeout, status, timing and result; jobs can be cancelled while queued or
running. Every state change is published on the :class:`EventBus`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .browser import BrowserPool, BrowserSession
from .commands import REGISTRY, CommandContext, CommandError, capture_frame
from .events import EventBus
from .permissions import NetworkPolicy, PermissionDenied, check

log = logging.getLogger("service_engine")

JOB_STATES = ("queued", "running", "done", "failed", "cancelled", "timeout")
MAX_QUEUE = 100
MAX_JOB_HISTORY = 200


@dataclass
class Job:
    id: str
    owner: str
    verb: str
    args: list
    source: str  # terminal | script | ai | api
    timeout: float
    status: str = "queued"
    result: Any = None
    error: dict | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    future: asyncio.Future | None = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def command_line(self) -> str:
        def fmt(a):
            return f'"{a}"' if isinstance(a, str) and (" " in a or not a) else str(a)
        return " ".join(["service", self.verb, *(fmt(a) for a in self.args)])

    def to_dict(self, include_result: bool = True) -> dict:
        d = {"id": self.id, "verb": self.verb, "args": _short(self.args), "source": self.source,
             "status": self.status, "command": self.command_line[:300], "timeout": self.timeout,
             "created_at": self.created_at, "started_at": self.started_at, "finished_at": self.finished_at,
             "duration_ms": int((self.finished_at - self.started_at) * 1000)
             if self.finished_at and self.started_at else None, "error": self.error}
        if include_result:
            d["result"] = _short(self.result)
        return d


def _short(v: Any, depth: int = 0) -> Any:
    """Trim large values (HTML, page text) for events and logs."""
    if isinstance(v, str):
        return v if len(v) <= 2000 else v[:2000] + f"… (+{len(v) - 2000} chars)"
    if depth > 4:
        return "…"
    if isinstance(v, list):
        return [_short(x, depth + 1) for x in v[:50]]
    if isinstance(v, dict):
        return {k: _short(x, depth + 1) for k, x in list(v.items())[:50] if k != "image"}
    return v


class ServiceError(Exception):
    def __init__(self, message: str, kind: str = "ServiceError", job: Job | None = None):
        super().__init__(message)
        self.kind = kind
        self.job = job


class OwnerState:
    def __init__(self):
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.current: Job | None = None
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []


class ServiceEngine:
    def __init__(self, *, headless: bool = True, data_root: str | Path | None = None,
                 downloads_dir: str | Path | None = None, screenshots_dir: str | Path | None = None,
                 allow_private: bool = False, allowed_hosts: set[str] | None = None,
                 max_download_bytes: int = 100 * 1024 * 1024, bus: EventBus | None = None,
                 pool: BrowserPool | None = None, idle_timeout: float = 1800.0):
        self.bus = bus or EventBus()
        self.pool = pool or BrowserPool(headless=headless)
        self.data_root = Path(data_root) if data_root else None
        self._downloads_dir = Path(downloads_dir) if downloads_dir else None
        self._screenshots_dir = Path(screenshots_dir) if screenshots_dir else None
        self.allow_private = allow_private
        self.allowed_hosts = set(allowed_hosts or ())
        self.max_download_bytes = max_download_bytes
        self.idle_timeout = idle_timeout
        self.owners: dict[str, OwnerState] = {}
        self.policies: dict[str, NetworkPolicy] = {}
        # Hooks set by the MURCLOUD backend.
        self.ai_controller: Callable[[str, str, Any], Awaitable[Any]] | None = None
        self.package_installer: Callable[[str, str], Awaitable[Any]] | None = None
        self.dir_resolver: Callable[[str, str], Path] | None = None
        self._reaper: asyncio.Task | None = None

    # ------------------------------------------------------------- paths
    def _dir(self, owner: str, kind: str) -> Path:
        if self.dir_resolver is not None:
            d = Path(self.dir_resolver(owner, kind))
        elif kind == "downloads" and self._downloads_dir:
            d = self._downloads_dir
        elif kind == "screenshots" and self._screenshots_dir:
            d = self._screenshots_dir
        elif self.data_root:
            d = self.data_root / owner / kind
        else:
            d = Path.cwd() / ".murcloud" / owner / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def downloads_dir_for(self, owner: str) -> Path:
        return self._dir(owner, "downloads")

    def screenshots_dir_for(self, owner: str) -> Path:
        return self._dir(owner, "screenshots")

    def policy_for(self, owner: str) -> NetworkPolicy:
        pol = self.policies.get(owner)
        if pol is None:
            pol = NetworkPolicy(allow_private=self.allow_private, allowed_hosts=set(self.allowed_hosts))
            self.policies[owner] = pol
        return pol

    # ---------------------------------------------------------- sessions
    async def ensure_session(self, owner: str) -> BrowserSession:
        def emit(type_: str, **data):
            self.bus.publish(owner, type_, **data)

        new = owner not in self.pool.sessions
        session = await self.pool.open_session(owner, self.policy_for(owner), self.downloads_dir_for(owner),
                                               self.screenshots_dir_for(owner), emit, self.max_download_bytes)
        if new:
            emit("browser.started", browser=self.pool.version)
            if self._reaper is None or self._reaper.done():
                self._reaper = asyncio.ensure_future(self._reap_idle())
        return session

    async def close_session(self, owner: str) -> bool:
        closed = await self.pool.close_session(owner)
        if closed:
            self.bus.publish(owner, "browser.stopped")
        return closed

    async def _reap_idle(self) -> None:
        while self.pool.sessions:
            await asyncio.sleep(min(60.0, self.idle_timeout))
            now = time.time()
            for owner, s in list(self.pool.sessions.items()):
                st = self.owners.get(owner)
                busy = st is not None and (st.current is not None or not st.queue.empty())
                if not busy and now - s.last_used > self.idle_timeout:
                    log.info("closing idle browser session of %s", owner)
                    await self.close_session(owner)

    # --------------------------------------------------------------- jobs
    def _state(self, owner: str) -> OwnerState:
        st = self.owners.get(owner)
        if st is None:
            st = self.owners[owner] = OwnerState()
        if st.worker is None or st.worker.done():
            st.worker = asyncio.ensure_future(self._worker(owner, st))
        return st

    def submit(self, owner: str, verb: str, args: list | None = None, *, source: str = "api",
               permissions: set[str] | None = None, timeout: float | None = None) -> Job:
        """Validate and enqueue a command. Returns immediately with a queued :class:`Job`."""
        args = list(args or [])
        spec = REGISTRY.get(verb)
        if spec is None:
            raise ServiceError(f"unknown command 'service {verb}'. Try 'service help'.", "UnknownCommand")
        try:
            check(verb, permissions)
        except PermissionDenied as exc:
            self.bus.publish(owner, "job.denied", verb=verb, permission=exc.permission, source=source)
            raise ServiceError(str(exc), "PermissionError") from None
        if not spec.min_args <= len(args) <= spec.max_args:
            exp = spec.min_args if spec.min_args == spec.max_args else f"{spec.min_args}-{spec.max_args}"
            raise ServiceError(f"'service {verb}' expects {exp} argument(s). Usage: {spec.usage}", "UsageError")
        st = self._state(owner)
        if st.queue.qsize() >= MAX_QUEUE:
            raise ServiceError("command queue is full", "QueueFull")
        t = spec.timeout if timeout is None else max(1.0, min(float(timeout), 600.0))
        job = Job(id=uuid.uuid4().hex[:12], owner=owner, verb=verb, args=args, source=source, timeout=t)
        job.future = asyncio.get_running_loop().create_future()
        st.jobs[job.id] = job
        st.order.append(job.id)
        while len(st.order) > MAX_JOB_HISTORY:
            old = st.jobs.get(st.order[0])
            if old and old.status in ("queued", "running"):
                break
            st.jobs.pop(st.order.pop(0), None)
        st.queue.put_nowait(job)
        self.bus.publish(owner, "job.queued", job=job.to_dict())
        return job

    async def execute(self, owner: str, verb: str, args: list | None = None, **kw) -> Any:
        """Submit and wait for the result (raises :class:`ServiceError` on failure)."""
        job = self.submit(owner, verb, args, **kw)
        try:
            return await asyncio.shield(job.future)
        except asyncio.CancelledError:
            self.cancel(owner, job.id)
            raise

    def cancel(self, owner: str, job_id: str | None = None) -> list[str]:
        """Cancel one job, or (``job_id=None``) the running job and everything queued."""
        st = self.owners.get(owner)
        if st is None:
            return []
        targets = [st.jobs[job_id]] if job_id and job_id in st.jobs else (
            [] if job_id else [j for j in st.jobs.values() if j.status in ("queued", "running")])
        cancelled = []
        for job in targets:
            if job.status == "queued":
                self._finish(job, "cancelled", error={"type": "Cancelled", "message": "cancelled before start"})
                cancelled.append(job.id)
            elif job.status == "running" and job.task is not None:
                job.task.cancel()
                cancelled.append(job.id)
        return cancelled

    def _finish(self, job: Job, status: str, result: Any = None, error: dict | None = None) -> None:
        job.status = status
        job.result = result
        job.error = error
        job.finished_at = time.time()
        if job.future is not None and not job.future.done():
            if status == "done":
                job.future.set_result(result)
            else:
                job.future.set_exception(ServiceError(error["message"] if error else status,
                                                      error["type"] if error else status, job))
                job.future.exception()  # mark retrieved; callers may not await it
        event = "job.finished" if status == "done" else f"job.{status}"
        self.bus.publish(job.owner, event, job=job.to_dict())

    async def _worker(self, owner: str, st: OwnerState) -> None:
        while True:
            job = await st.queue.get()
            if job.status != "queued":
                continue
            st.current = job
            job.status = "running"
            job.started_at = time.time()
            self.bus.publish(owner, "job.started", job=job.to_dict(include_result=False))
            job.task = asyncio.ensure_future(self._run(job))
            try:
                result = await asyncio.wait_for(asyncio.shield(job.task), timeout=job.timeout)
                self._finish(job, "done", result=result)
            except asyncio.TimeoutError:
                job.task.cancel()
                self._finish(job, "timeout", error={"type": "Timeout",
                                                    "message": f"'{job.command_line}' timed out after {job.timeout:g}s"})
            except asyncio.CancelledError:
                if job.task.cancelled():
                    self._finish(job, "cancelled", error={"type": "Cancelled", "message": "cancelled by user"})
                else:  # the worker itself is being cancelled (shutdown)
                    job.task.cancel()
                    self._finish(job, "cancelled", error={"type": "Cancelled", "message": "engine shutting down"})
                    raise
            except CommandError as exc:
                self._finish(job, "failed", error={"type": exc.kind, "message": str(exc)})
            except ServiceError as exc:
                self._finish(job, "failed", error={"type": exc.kind, "message": str(exc)})
            except Exception as exc:  # unexpected failure inside Playwright etc.
                log.exception("job %s crashed", job.id)
                self._finish(job, "failed", error={"type": type(exc).__name__,
                                                   "message": str(exc).splitlines()[0][:500] if str(exc) else repr(exc)})
            finally:
                st.current = None

    async def _run(self, job: Job) -> Any:
        spec = REGISTRY[job.verb]
        session = None
        if spec.needs_browser:
            session = await self.ensure_session(job.owner)
            session.touch()
        elif job.verb not in ("start", "stop", "status"):
            session = self.pool.sessions.get(job.owner)
        ctx = CommandContext(engine=self, owner=job.owner, job=job, session=session)
        return await spec.handler(ctx, job.args)

    # ------------------------------------------------------------ queries
    def jobs(self, owner: str, limit: int = 50) -> list[dict]:
        st = self.owners.get(owner)
        if st is None:
            return []
        return [st.jobs[j].to_dict() for j in st.order[-limit:] if j in st.jobs][::-1]

    def get_job(self, owner: str, job_id: str) -> Job | None:
        st = self.owners.get(owner)
        return st.jobs.get(job_id) if st else None

    async def status(self, owner: str) -> dict:
        s = self.pool.sessions.get(owner)
        st = self.owners.get(owner)
        tabs = []
        if s is not None:
            for i, p in enumerate(s.pages):
                try:
                    title = await p.title()
                except Exception:
                    title = ""
                tabs.append({"index": i, "url": p.url, "title": title, "active": i == s.active})
        return {
            "running": s is not None,
            "browser": self.pool.version,
            "tabs": tabs,
            "active_tab": s.active if s else None,
            "queue": st.queue.qsize() if st else 0,
            "current": st.current.to_dict(include_result=False) if st and st.current else None,
            "downloads": len(s.downloads) if s else 0,
            "console_errors": len(s.console_errors) if s else 0,
            "blocked_requests": len(s.blocked_requests) if s else 0,
            "ai_attached": self.ai_controller is not None,
        }

    async def frame(self, owner: str, quality: int = 60) -> dict | None:
        s = self.pool.sessions.get(owner)
        if s is None or not s.pages:
            return None
        return await capture_frame(s, quality)

    async def shutdown(self) -> None:
        for st in self.owners.values():
            if st.worker is not None:
                st.worker.cancel()
        for st in self.owners.values():
            if st.worker is not None:
                try:
                    await st.worker
                except (asyncio.CancelledError, Exception):
                    pass
        if self._reaper is not None:
            self._reaper.cancel()
        await self.pool.shutdown()
