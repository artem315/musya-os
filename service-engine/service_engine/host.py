"""Bridge between the MUSYA interpreter and the Service Engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from musya_runtime import MusyaPermissionError, MusyaRuntimeError, SandboxHost

from .commands import describe_commands
from .engine import ServiceEngine, ServiceError

# Engine events forwarded to ``on <name>`` handlers inside scripts.
SCRIPT_EVENTS = {
    "browser.page_loaded": "page_loaded",
    "browser.download_finished": "download_finished",
    "browser.download_failed": "download_failed",
    "browser.console_error": "console_error",
    "browser.screenshot": "screenshot",
    "browser.request_blocked": "request_blocked",
}


class EngineHost(SandboxHost):
    """A sandboxed project host whose ``service`` verbs run on a real :class:`ServiceEngine`."""

    def __init__(self, root: str | Path, engine: ServiceEngine, permissions: set[str] | None = None, *,
                 owner: str = "local", source: str = "script", output_sink=None,
                 ai_ask: Callable[[str, dict], Awaitable[str]] | None = None, **kw):
        super().__init__(root, permissions, output_sink=output_sink, **kw)
        self.engine = engine
        self.owner = owner
        self.source = source
        self._ai_ask = ai_ask
        self.interpreter = None
        self._sub = None
        self._pump: asyncio.Task | None = None

    def attach(self, interpreter) -> None:
        """Start forwarding engine events of this owner to the interpreter."""
        self.interpreter = interpreter
        self._sub = self.engine.bus.subscribe(self.owner)
        self._pump = asyncio.ensure_future(self._forward())

    def detach(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
        if self._sub is not None:
            self._sub.close()

    async def _forward(self) -> None:
        while True:
            ev = await self._sub.get()
            name = SCRIPT_EVENTS.get(ev["type"])
            if name is None and ev["type"].startswith("custom."):
                continue  # emitted by the script itself; already queued locally
            if name and self.interpreter is not None:
                data = {k: v for k, v in ev.items() if k not in ("owner", "type", "image")}
                self.interpreter.deliver_event(name, data)

    async def service(self, verb: str, args: list, line: int | None = None) -> Any:
        if verb == "help":
            return describe_commands()
        try:
            result = await self.engine.execute(self.owner, verb, args, source=self.source,
                                               permissions=self.permissions)
        except ServiceError as exc:
            if exc.kind == "PermissionError":
                raise MusyaPermissionError(str(exc), line) from None
            raise MusyaRuntimeError(str(exc), line, kind=exc.kind) from None
        # Give event pumps a chance to deliver page_loaded etc. before the next statement.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return result

    async def ai_ask(self, prompt: str, options: dict) -> str:
        if self._ai_ask is None:
            return await super().ai_ask(prompt, options)
        return await self._ai_ask(prompt, options)
