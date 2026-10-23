"""FastAPI dependencies: database session, current user, CSRF and rate limits."""

from __future__ import annotations

import time
from typing import AsyncIterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .core import Core
from .db import AuthSession, User
from .security import token_id

COOKIE = "mur_session"
CSRF_HEADER = "x-mur-csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_core(request: Request) -> Core:
    return request.app.state.core


async def get_db(core: Core = Depends(get_core)) -> AsyncIterator[AsyncSession]:
    async with core.db.sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


def client_ip(request: Request) -> str:
    # Trust X-Forwarded-For only from the reverse proxy configured in uvicorn (--forwarded-allow-ips).
    return request.client.host if request.client else "unknown"


def extract_token(request: Request) -> tuple[str | None, bool]:
    """Returns (token, via_cookie)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    tok = request.cookies.get(COOKIE)
    return (tok, True) if tok else (None, False)


async def resolve_user(core: Core, db: AsyncSession, token: str | None) -> User | None:
    if not token or len(token) > 200:
        return None
    sess = await db.get(AuthSession, token_id(token))
    if sess is None or sess.expires_at < time.time():
        return None
    user = await db.get(User, sess.user_id)
    if user is None:
        return None
    if time.time() - sess.last_seen > 300:
        sess.last_seen = time.time()
    return user


async def current_user(request: Request, core: Core = Depends(get_core), db: AsyncSession = Depends(get_db)) -> User:
    token, via_cookie = extract_token(request)
    user = await resolve_user(core, db, token)
    if user is None:
        raise HTTPException(401, "not signed in")
    # CSRF: cookie-authenticated state changes must carry a custom header, which other origins
    # cannot add without passing CORS preflight.
    if via_cookie and request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "missing CSRF header")
    ok, retry = core.limiter.hit(f"user:{user.id}", core.settings.rate_limit_per_minute)
    if not ok:
        raise HTTPException(429, f"too many requests, retry in {int(retry) + 1}s",
                            headers={"Retry-After": str(int(retry) + 1)})
    request.state.user = user
    return user


def limit(name: str, per_minute: int):
    async def dep(request: Request, core: Core = Depends(get_core)) -> None:
        user = getattr(request.state, "user", None)
        key = f"{name}:{user.id if user else client_ip(request)}"
        ok, retry = core.limiter.hit(key, per_minute)
        if not ok:
            raise HTTPException(429, f"rate limit for {name} reached, retry in {int(retry) + 1}s",
                                headers={"Retry-After": str(int(retry) + 1)})
    return dep
