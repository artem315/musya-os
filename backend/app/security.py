"""Password hashing, session tokens, secret encryption and rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import deque

from cryptography.fernet import Fernet, InvalidToken

# scrypt parameters: N=2^15, r=8, p=1 (~32 MiB, ~50-100 ms per hash).
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 15, 8, 1
_MAXMEM = 64 * 1024 * 1024

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,40}$")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=_MAXMEM,
                        dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM,
                            dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


# A valid hash used to equalise timing when the username does not exist.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def password_problems(password: str, username: str = "") -> list[str]:
    problems = []
    if len(password) < 8:
        problems.append("at least 8 characters")
    if len(password) > 256:
        problems.append("at most 256 characters")
    if password.lower() in {"password", "12345678", "qwertyui", "murcloud", "password1"}:
        problems.append("too common")
    if username and password.lower() == username.lower():
        problems.append("must differ from the username")
    return problems


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SecretBox:
    """Encrypts API keys at rest (Fernet / AES-128-CBC + HMAC) with a key derived from MUR_SECRET_KEY."""

    def __init__(self, secret_key: str):
        derived = hashlib.sha256(b"murcloud-secretbox-v1:" + secret_key.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return None


class RateLimiter:
    """Sliding-window limiter kept in memory (one backend instance; use Redis when scaling out)."""

    def __init__(self):
        self._hits: dict[str, deque] = {}
        self._last_gc = time.monotonic()

    def hit(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, float]:
        now = time.monotonic()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False, window - (now - q[0])
        q.append(now)
        if now - self._last_gc > 300:
            self._gc(now, window)
        return True, 0.0

    def _gc(self, now: float, window: float) -> None:
        self._last_gc = now
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] > window * 5]:
            del self._hits[k]

    def reset(self) -> None:
        self._hits.clear()
