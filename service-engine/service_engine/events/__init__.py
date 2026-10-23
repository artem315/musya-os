"""In-process event bus.

Events are plain dicts ``{"type": str, "owner": str, "ts": float, ...}``.
Each subscriber gets its own bounded queue; slow subscribers drop the oldest
events instead of blocking the engine. The backend forwards an owner's
events to that owner's WebSocket connections only.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Callable

Event = dict[str, Any]


class Subscription:
    def __init__(self, bus: EventBus, owner: str | None, maxsize: int):
        self.bus = bus
        self.owner = owner
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def offer(self, event: Event) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        self.queue.put_nowait(event)

    async def get(self, timeout: float | None = None) -> Event:
        if timeout is None:
            return await self.queue.get()
        return await asyncio.wait_for(self.queue.get(), timeout)

    def close(self) -> None:
        self.bus.unsubscribe(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class EventBus:
    def __init__(self, history: int = 200):
        self._subs: list[Subscription] = []
        self._listeners: list[Callable[[Event], None]] = []
        self._history: dict[str, deque] = {}
        self._history_size = history

    def subscribe(self, owner: str | None = None, maxsize: int = 500) -> Subscription:
        """Subscribe to one owner's events (``None`` = every event, for internal use)."""
        sub = Subscription(self, owner, maxsize)
        self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)

    def add_listener(self, fn: Callable[[Event], None]) -> None:
        self._listeners.append(fn)

    def publish(self, owner: str, type_: str, **data: Any) -> Event:
        event = {"type": type_, "owner": owner, "ts": time.time(), **data}
        self._history.setdefault(owner, deque(maxlen=self._history_size)).append(event)
        for sub in list(self._subs):
            if sub.owner is None or sub.owner == owner:
                sub.offer(event)
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # pragma: no cover - listeners must not break the engine
                pass
        return event

    def history(self, owner: str, limit: int = 100) -> list[Event]:
        return list(self._history.get(owner, ()))[-limit:]


__all__ = ["EventBus", "Subscription", "Event"]
