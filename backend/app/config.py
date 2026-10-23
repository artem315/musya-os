"""MURCLOUD backend configuration (environment variables only - no secrets in the repo)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    env: str = field(default_factory=lambda: os.environ.get("MUR_ENV", "development"))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("MUR_DATA_DIR", "./data")).resolve())
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    secret_key: str = field(default_factory=lambda: os.environ.get("MUR_SECRET_KEY", ""))
    cookie_secure: bool = field(default_factory=lambda: _bool("MUR_COOKIE_SECURE", False))
    allowed_origins: list[str] = field(default_factory=lambda: _list("MUR_ALLOWED_ORIGINS"))
    registration_open: bool = field(default_factory=lambda: _bool("MUR_REGISTRATION_OPEN", True))
    session_days: int = field(default_factory=lambda: _int("MUR_SESSION_DAYS", 14))
    max_upload_mb: int = field(default_factory=lambda: _int("MUR_MAX_UPLOAD_MB", 100))
    user_quota_mb: int = field(default_factory=lambda: _int("MUR_USER_QUOTA_MB", 2048))
    storage_backend: str = field(default_factory=lambda: os.environ.get("MUR_STORAGE", "local"))
    s3_bucket: str = field(default_factory=lambda: os.environ.get("MUR_S3_BUCKET", ""))
    s3_endpoint: str | None = field(default_factory=lambda: os.environ.get("MUR_S3_ENDPOINT") or None)
    s3_region: str | None = field(default_factory=lambda: os.environ.get("MUR_S3_REGION") or None)
    frontend_dist: Path | None = field(default_factory=lambda: Path(os.environ["MUR_FRONTEND_DIST"]).resolve()
                                       if os.environ.get("MUR_FRONTEND_DIST") else None)
    packages_dir: Path = field(default_factory=lambda: Path(os.environ.get(
        "MUR_PACKAGES_DIR", Path(__file__).resolve().parents[2] / "packages")).resolve())
    headless: bool = field(default_factory=lambda: _bool("MUR_BROWSER_HEADLESS", True))
    max_browser_sessions: int = field(default_factory=lambda: _int("MUR_MAX_BROWSER_SESSIONS", 20))
    # Admin-provided AI key used when a user has none (optional; costs are billed to the operator).
    server_ai_provider: str = field(default_factory=lambda: os.environ.get("MUR_AI_PROVIDER", "anthropic"))
    server_ai_key: str = field(default_factory=lambda: os.environ.get("MUR_AI_KEY", ""))
    server_ai_model: str = field(default_factory=lambda: os.environ.get("MUR_AI_MODEL", ""))
    allow_scripted_ai: bool = field(default_factory=lambda: _bool("MUR_AI_ALLOW_SCRIPTED", False))
    pc_enabled: bool = field(default_factory=lambda: _bool("MUR_PC_ENABLED", False))
    pc_image: str = field(default_factory=lambda: os.environ.get("MUR_PC_IMAGE", "murcloud/mur-pc:10.0"))
    pc_memory: str = field(default_factory=lambda: os.environ.get("MUR_PC_MEMORY", "2g"))
    pc_cpus: float = field(default_factory=lambda: float(os.environ.get("MUR_PC_CPUS", "1.5")))
    pc_network: str = field(default_factory=lambda: os.environ.get("MUR_PC_NETWORK", "murcloud-pc"))
    pc_max_sessions: int = field(default_factory=lambda: _int("MUR_PC_MAX_SESSIONS", 5))
    # Allow the browser to reach private addresses (ONLY for local development / tests).
    ssrf_allow_private: bool = field(default_factory=lambda: _bool("MUR_SSRF_ALLOW_PRIVATE", False))
    ssrf_allow_hosts: list[str] = field(default_factory=lambda: _list("MUR_SSRF_ALLOW_HOSTS"))
    rate_limit_per_minute: int = field(default_factory=lambda: _int("MUR_RATE_LIMIT", 600))

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite+aiosqlite:///{self.data_dir / 'murcloud.db'}"
        elif self.database_url.startswith("postgres://"):
            self.database_url = "postgresql+asyncpg://" + self.database_url[len("postgres://"):]
        elif self.database_url.startswith("postgresql://"):
            self.database_url = "postgresql+asyncpg://" + self.database_url[len("postgresql://"):]
        if not self.secret_key:
            if self.is_production:
                raise RuntimeError("MUR_SECRET_KEY must be set in production")
            key_file = self.data_dir / ".secret_key"
            if not key_file.exists():
                key_file.write_text(secrets.token_urlsafe(48))
                key_file.chmod(0o600)
            self.secret_key = key_file.read_text().strip()
        if len(self.secret_key) < 32:
            raise RuntimeError("MUR_SECRET_KEY must be at least 32 characters")
        if self.is_production and not self.cookie_secure:
            raise RuntimeError("MUR_COOKIE_SECURE=1 is required in production (HTTPS only)")

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def user_dir(self, user_id: str) -> Path:
        d = self.data_dir / "users" / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(settings: Settings | None = None) -> None:
    global _settings
    _settings = settings
