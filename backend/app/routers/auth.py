"""Registration, login, sessions."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import Core
from ..db import AuditLog, AuthSession, User
from ..deps import COOKIE, client_ip, current_user, extract_token, get_core, get_db, limit
from ..security import (DUMMY_HASH, USERNAME_RE, hash_password, new_token, password_problems, token_id,
                        verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])
LOCK_AFTER = 5
LOCK_SECONDS = 300


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=256)


class Register(Credentials):
    display_name: str = Field(default="", max_length=80)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


def user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "is_admin": u.is_admin,
            "settings": u.settings or {}, "created_at": u.created_at}


async def _open_session(core: Core, db: AsyncSession, user: User, request: Request, response: Response) -> dict:
    token = new_token()
    expires = time.time() + core.settings.session_days * 86400
    db.add(AuthSession(id=token_id(token), user_id=user.id, expires_at=expires,
                       user_agent=request.headers.get("user-agent", "")[:300], ip=client_ip(request)))
    # Drop this user's expired sessions while we are here.
    await db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at < time.time()))
    response.set_cookie(COOKIE, token, max_age=core.settings.session_days * 86400, httponly=True,
                        secure=core.settings.cookie_secure, samesite="lax", path="/")
    # The token is also returned for non-browser clients (Bearer auth); the web UI relies on the cookie.
    return {"user": user_dict(user), "token": token, "expires_at": expires}


@router.post("/register", dependencies=[Depends(limit("register", 5))])
async def register(body: Register, request: Request, response: Response, core: Core = Depends(get_core),
                   db: AsyncSession = Depends(get_db)):
    if not core.settings.registration_open:
        users = (await db.execute(select(func.count(User.id)))).scalar_one()
        if users > 0:
            raise HTTPException(403, "registration is closed on this server")
    if not USERNAME_RE.match(body.username):
        raise HTTPException(400, "username: 3-40 latin letters, digits, '.', '_' or '-'")
    problems = password_problems(body.password, body.username)
    if problems:
        raise HTTPException(400, "password: " + ", ".join(problems))
    exists = (await db.execute(select(User.id).where(func.lower(User.username) == body.username.lower()))).first()
    if exists:
        raise HTTPException(409, "this username is taken")
    first_user = (await db.execute(select(func.count(User.id)))).scalar_one() == 0
    user = User(username=body.username, display_name=(body.display_name or body.username).strip()[:80],
                password_hash=hash_password(body.password), is_admin=first_user,
                settings={"theme": "neon", "accent": "#a855f7", "wallpaper": "galaxy", "ai_mode": "agent"})
    db.add(user)
    await db.flush()
    await core.drive.ensure_defaults(db, user.id)
    project = await core.projects.create(db, user.id, "Musya Hello", "blank")
    await core.projects.create(db, user.id, "Musya Browser", "browser")
    db.add(AuditLog(user_id=user.id, actor="user", event="registered", detail={"first_project": project.id}))
    return await _open_session(core, db, user, request, response)


@router.post("/login", dependencies=[Depends(limit("login", 10))])
async def login(body: Credentials, request: Request, response: Response, core: Core = Depends(get_core),
                db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(func.lower(User.username) == body.username.lower()))).scalar_one_or_none()
    if user is None:
        verify_password(body.password, DUMMY_HASH)  # same timing as a real check
        raise HTTPException(401, "wrong username or password")
    if user.locked_until > time.time():
        raise HTTPException(429, f"too many failed attempts, try again in {int(user.locked_until - time.time()) + 1}s")
    if not verify_password(body.password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= LOCK_AFTER:
            user.locked_until, user.failed_logins = time.time() + LOCK_SECONDS, 0
        db.add(AuditLog(user_id=user.id, actor="user", event="login_failed", ok=False, detail={"ip": client_ip(request)}))
        await db.commit()
        raise HTTPException(401, "wrong username or password")
    user.failed_logins = 0
    db.add(AuditLog(user_id=user.id, actor="user", event="login", detail={"ip": client_ip(request)}))
    return await _open_session(core, db, user, request, response)


@router.post("/logout")
async def logout(request: Request, response: Response, user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_db), core: Core = Depends(get_core)):
    token, _ = extract_token(request)
    if token:
        sess = await db.get(AuthSession, token_id(token))
        if sess is not None:
            await db.delete(sess)
    response.delete_cookie(COOKIE, path="/")
    core.runner.close_repls(user.id)
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return user_dict(user)


@router.get("/sessions")
async def sessions(request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    token, _ = extract_token(request)
    current = token_id(token) if token else ""
    rows = (await db.execute(select(AuthSession).where(AuthSession.user_id == user.id)
                             .order_by(AuthSession.last_seen.desc()))).scalars().all()
    return [{"id": r.id[:12], "current": r.id == current, "created_at": r.created_at, "last_seen": r.last_seen,
             "expires_at": r.expires_at, "user_agent": r.user_agent, "ip": r.ip} for r in rows]


@router.post("/sessions/revoke-others")
async def revoke_others(request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    token, _ = extract_token(request)
    await db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != token_id(token or "")))
    return {"ok": True}


@router.post("/password", dependencies=[Depends(limit("password", 5))])
async def change_password(body: PasswordChange, request: Request, user: User = Depends(current_user),
                          db: AsyncSession = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "current password is wrong")
    problems = password_problems(body.new_password, user.username)
    if problems:
        raise HTTPException(400, "password: " + ", ".join(problems))
    user.password_hash = hash_password(body.new_password)
    token, _ = extract_token(request)
    await db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != token_id(token or "")))
    db.add(AuditLog(user_id=user.id, actor="user", event="password_changed"))
    return {"ok": True}
