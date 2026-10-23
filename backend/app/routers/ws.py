"""WebSockets: live events for the desktop, and the authenticated MUR PC VNC proxy."""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..deps import COOKIE, resolve_user

router = APIRouter()
log = logging.getLogger("murcloud.ws")


def origin_allowed(ws: WebSocket) -> bool:
    """Reject cross-site WebSocket hijacking: Origin must be this host or an allowed origin."""
    origin = ws.headers.get("origin")
    if origin is None:
        return True  # non-browser clients (Android app native code, CLI) - they still need a token
    core = ws.app.state.core
    if origin in core.settings.allowed_origins:
        return True
    host = ws.headers.get("host", "")
    return urlsplit(origin).netloc == host


async def authenticate(ws: WebSocket):
    core = ws.app.state.core
    token = ws.cookies.get(COOKIE) or ws.query_params.get("token")
    async with core.db.sessionmaker() as db:
        user = await resolve_user(core, db, token)
        await db.commit()
    return user


def _clean(event: dict) -> dict:
    return {k: v for k, v in event.items() if k != "owner"}


@router.websocket("/api/ws")
async def events(ws: WebSocket):
    if not origin_allowed(ws):
        await ws.close(code=4403)
        return
    user = await authenticate(ws)
    if user is None:
        await ws.close(code=4401)
        return
    core = ws.app.state.core
    await ws.accept()
    sub = core.engine.bus.subscribe(user.id, maxsize=1000)
    await ws.send_json({"type": "hello", "user": user.username, "version": "10.0.0-beta"})

    async def pump():
        while True:
            ev = await sub.get()
            await ws.send_text(json.dumps(_clean(ev), ensure_ascii=False, default=str))

    async def receive():
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "ping":
                await ws.send_json({"type": "pong", "ts": msg.get("ts")})
            elif kind == "browser.frame":
                try:
                    frame = await core.engine.frame(user.id, int(msg.get("quality", 55)))
                except Exception as exc:
                    frame = None
                    await ws.send_json({"type": "browser.frame_error", "message": str(exc)[:200]})
                if frame is not None:
                    await ws.send_json({"type": "browser.frame", **frame})

    pump_task = asyncio.ensure_future(pump())
    try:
        await receive()
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        pump_task.cancel()
        sub.close()


@router.websocket("/api/pc/{pc_id}/vnc")
async def vnc_proxy(ws: WebSocket, pc_id: str):
    """Bridge noVNC in the user's browser to websockify inside their MUR PC container."""
    import websockets

    if not origin_allowed(ws):
        await ws.close(code=4403)
        return
    user = await authenticate(ws)
    if user is None:
        await ws.close(code=4401)
        return
    core = ws.app.state.core
    try:
        ip, port, _ = await core.pc.target(user.id, pc_id)
    except Exception as exc:
        await ws.accept()
        await ws.close(code=4404, reason=str(getattr(exc, "detail", exc))[:100])
        return
    sub = "binary" if "binary" in (ws.headers.get("sec-websocket-protocol") or "") else None
    await ws.accept(subprotocol=sub)
    try:
        # proxy=None: the container is on an internal network and must never be reached via an HTTP proxy.
        async with websockets.connect(f"ws://{ip}:{port}/websockify", subprotocols=["binary"],
                                      max_size=None, open_timeout=10, proxy=None) as upstream:
            async def down():
                async for data in upstream:
                    if isinstance(data, bytes):
                        await ws.send_bytes(data)
                    else:
                        await ws.send_text(data)

            async def up():
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if msg.get("bytes") is not None:
                        await upstream.send(msg["bytes"])
                    elif msg.get("text") is not None:
                        await upstream.send(msg["text"])

            tasks = [asyncio.ensure_future(down()), asyncio.ensure_future(up())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except Exception as exc:
        log.info("vnc proxy closed: %s", exc)
    finally:
        if ws.application_state != WebSocketState.DISCONNECTED:
            try:
                await ws.close()
            except RuntimeError:
                pass
