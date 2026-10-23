"""Playwright/Chromium browser management.

One Chromium process is shared by the whole engine; every owner (MURCLOUD
user) gets an isolated ``BrowserContext`` - separate cookies, storage,
downloads and tabs. All network traffic of a context passes through the
owner's :class:`NetworkPolicy` via request interception.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..permissions.network import BlockedURL, NetworkPolicy

log = logging.getLogger("service_engine.browser")

MAX_TABS = 8
MAX_CONSOLE_ERRORS = 200
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
SAFE_FILENAME = re.compile(r"[^\w.\-() Ѐ-ӿ]+", re.UNICODE)


def sanitize_filename(name: str, default: str = "download") -> str:
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = SAFE_FILENAME.sub("_", name).strip(" .")
    return (name or default)[:120]


def unique_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 10_000):
        cand = directory / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
    return directory / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"


@dataclass
class BrowserSession:
    owner: str
    context: Any  # playwright BrowserContext
    policy: NetworkPolicy
    downloads_dir: Path
    screenshots_dir: Path
    emit: Callable[..., None]
    max_download_bytes: int
    pages: list = field(default_factory=list)
    active: int = 0
    console_errors: list = field(default_factory=list)
    blocked_requests: list = field(default_factory=list)
    downloads: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    @property
    def page(self):
        if not self.pages:
            raise RuntimeError("no open tabs")
        self.active = max(0, min(self.active, len(self.pages) - 1))
        return self.pages[self.active]

    def touch(self) -> None:
        self.last_used = time.time()

    async def new_page(self):
        if len(self.pages) >= MAX_TABS:
            raise RuntimeError(f"tab limit reached ({MAX_TABS})")
        page = await self.context.new_page()
        return page  # registered by the context "page" handler

    def attach_page(self, page) -> None:
        if page in self.pages:
            return
        self.pages.append(page)
        self.active = len(self.pages) - 1
        page.on("console", lambda msg: self._on_console(page, msg))
        page.on("pageerror", lambda err: self._on_page_error(page, err))
        page.on("load", lambda: self.emit("browser.page_loaded", url=page.url, tab=self.index_of(page)))
        page.on("close", lambda: self._on_close(page))
        page.on("download", lambda d: asyncio.ensure_future(self._on_download(d)))
        if len(self.pages) > MAX_TABS:
            asyncio.ensure_future(page.close())

    def index_of(self, page) -> int:
        try:
            return self.pages.index(page)
        except ValueError:
            return -1

    def _on_close(self, page) -> None:
        if page in self.pages:
            idx = self.pages.index(page)
            self.pages.remove(page)
            if self.active >= idx and self.active > 0:
                self.active -= 1
            self.emit("browser.tab_closed", tab=idx)

    def _record_error(self, entry: dict) -> None:
        self.console_errors.append(entry)
        del self.console_errors[:-MAX_CONSOLE_ERRORS]
        self.emit("browser.console_error", **entry)

    def _on_console(self, page, msg) -> None:
        if msg.type in ("error", "warning"):
            loc = msg.location or {}
            self._record_error({"level": msg.type, "text": msg.text[:2000], "url": loc.get("url", page.url),
                                "line": loc.get("lineNumber"), "ts": time.time()})

    def _on_page_error(self, page, err) -> None:
        self._record_error({"level": "exception", "text": str(err)[:2000], "url": page.url, "ts": time.time()})

    async def _on_download(self, download) -> None:
        """Downloads started by clicking a link. Saved into MUR Drive's Downloads folder."""
        name = sanitize_filename(download.suggested_filename)
        try:
            await self.policy.check_url(download.url)
        except BlockedURL as exc:
            await download.cancel()
            self.emit("browser.download_blocked", url=download.url, reason=exc.reason)
            return
        target = unique_path(self.downloads_dir, name)
        self.emit("browser.download_started", url=download.url, filename=target.name)
        try:
            tmp = await download.path()
            size = os.path.getsize(tmp) if tmp else 0
            if size > self.max_download_bytes:
                self.emit("browser.download_failed", filename=target.name, reason="file exceeds the size limit")
                await download.delete()
                return
            await download.save_as(str(target))
            await download.delete()
        except Exception as exc:  # pragma: no cover - depends on network
            self.emit("browser.download_failed", filename=target.name, reason=str(exc)[:300])
            return
        entry = {"filename": target.name, "path": str(target), "size": target.stat().st_size,
                 "url": download.url, "source": "click", "ts": time.time()}
        self.downloads.append(entry)
        self.emit("browser.download_finished", **entry)


class BrowserPool:
    """Owns the Playwright driver, the Chromium process and per-owner contexts."""

    def __init__(self, headless: bool = True, executable_path: str | None = None,
                 proxy: str | None = None, launch_args: list[str] | None = None):
        self.headless = headless
        self.executable_path = executable_path or os.environ.get("MUR_CHROMIUM_PATH") or None
        self.proxy = proxy if proxy is not None else (os.environ.get("MUR_BROWSER_PROXY") or None)
        self.launch_args = launch_args or []
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()
        self.sessions: dict[str, BrowserSession] = {}

    async def browser(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright

                if self._pw is None:
                    self._pw = await async_playwright().start()
                kwargs: dict[str, Any] = {"headless": self.headless,
                                          "args": ["--disable-dev-shm-usage", *self.launch_args]}
                if self.executable_path:
                    kwargs["executable_path"] = self.executable_path
                if self.proxy:
                    # Local preview servers must never be sent to the upstream proxy.
                    kwargs["proxy"] = {"server": self.proxy, "bypass": "127.0.0.1,localhost,::1"}
                self._browser = await self._pw.chromium.launch(**kwargs)
                log.info("Chromium %s launched", self._browser.version)
            return self._browser

    async def open_session(self, owner: str, policy: NetworkPolicy, downloads_dir: Path,
                           screenshots_dir: Path, emit: Callable[..., None],
                           max_download_bytes: int) -> BrowserSession:
        existing = self.sessions.get(owner)
        if existing is not None:
            existing.touch()
            return existing
        browser = await self.browser()
        context = await browser.new_context(viewport=DEFAULT_VIEWPORT, accept_downloads=True,
                                            user_agent=None, locale=os.environ.get("MUR_BROWSER_LOCALE", "en-US"),
                                            service_workers="block")
        context.set_default_timeout(15_000)
        session = BrowserSession(owner=owner, context=context, policy=policy, downloads_dir=downloads_dir,
                                 screenshots_dir=screenshots_dir, emit=emit,
                                 max_download_bytes=max_download_bytes)

        async def guard(route, request):
            try:
                await policy.check_url(request.url, navigation=False)
            except BlockedURL as exc:
                entry = {"url": request.url[:500], "reason": exc.reason, "ts": time.time()}
                session.blocked_requests.append(entry)
                del session.blocked_requests[:-100]
                emit("browser.request_blocked", **entry)
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        async def ws_guard(ws):
            http_url = re.sub(r"^ws", "http", ws.url, count=1)
            try:
                await policy.check_url(http_url, navigation=False)
            except BlockedURL as exc:
                emit("browser.request_blocked", url=ws.url[:500], reason=exc.reason, ts=time.time())
                await ws.close(code=1008, reason="blocked by MURCLOUD network policy")
                return
            ws.connect_to_server()

        await context.route("**/*", guard)
        # WebSocket connections bypass route(); check their targets as well.
        await context.route_web_socket(re.compile(".*"), ws_guard)
        context.on("page", session.attach_page)
        self.sessions[owner] = session
        page = await context.new_page()
        session.attach_page(page)
        return session

    async def close_session(self, owner: str) -> bool:
        session = self.sessions.pop(owner, None)
        if session is None:
            return False
        try:
            await session.context.close()
        except Exception:  # pragma: no cover
            pass
        return True

    async def shutdown(self) -> None:
        for owner in list(self.sessions):
            await self.close_session(owner)
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # pragma: no cover
                pass
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    @property
    def version(self) -> str | None:
        return self._browser.version if self._browser is not None else None
