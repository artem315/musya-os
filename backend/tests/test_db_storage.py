"""MUR_STORAGE=database: everything survives a restart that wipes the local filesystem (Render Free)."""

import io
import os
import time
import uuid

from conftest import CSRF, make_settings, register, wait_run
from fastapi.testclient import TestClient


def _db_url(tmp_path) -> str:
    # The database lives outside both data dirs, like an external Postgres.
    return os.environ.get("MUR_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}"


def _app(data_dir, db_url, site=None):
    from app.main import create_app
    return create_app(make_settings(data_dir, site, MUR_STORAGE="database", DATABASE_URL=db_url))


def test_everything_survives_a_wiped_filesystem(tmp_path, site):
    db_url = _db_url(tmp_path)
    user = "free" + uuid.uuid4().hex[:6]
    with TestClient(_app(tmp_path / "disk-1", db_url, site)) as c:
        register(c, user)
        pid = c.post("/api/projects", json={"name": "Free Site", "template": "website"}, headers=CSRF).json()["id"]
        c.put(f"/api/projects/{pid}/file", json={"path": "lib/util.mysya", "content": 'let x = "кот"\n'}, headers=CSRF)
        c.post(f"/api/projects/{pid}/mkdir", json={"path": "empty/folder"}, headers=CSRF)
        c.post(f"/api/projects/{pid}/delete", json={"path": "script.js"}, headers=CSRF)
        # a script writing a file (its app.yml must grant files.write) and a terminal line writing a file
        c.put(f"/api/projects/{pid}/file", json={"path": "app.yml", "content":
              "name: Free Site\nversion: 1.0.0\nentry: main.mysya\npermissions: [files.read, files.write]\n"},
              headers=CSRF)
        c.put(f"/api/projects/{pid}/file", json={"path": "main.mysya",
              "content": 'file.write("out/result.txt", "from script")\nprint "ok"'}, headers=CSRF)
        run = c.post(f"/api/projects/{pid}/run", json={}, headers=CSRF).json()
        assert wait_run(c, pid, run["id"])["status"] == "done"
        c.post("/api/engine/terminal", json={"line": 'file.write("repl.txt", "from terminal")', "project_id": pid},
               headers=CSRF)
        blob = bytes(range(256)) * 5000  # 1.28 MB -> several chunks
        c.post("/api/drive/upload", files={"file": ("big.bin", io.BytesIO(blob), "application/octet-stream")},
               data={"path": "/Documents"}, headers=CSRF)
        c.post("/api/drive/upload", files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
               data={"path": "/"}, headers=CSRF)
        c.post("/api/packages/musya-hello/install", json={}, headers=CSRF)
        time.sleep(0.5)
    # Nothing on the first disk is needed any more.
    import shutil
    shutil.rmtree(tmp_path / "disk-1")

    with TestClient(_app(tmp_path / "disk-2", db_url, site)) as c:
        r = c.post("/api/auth/login", json={"username": user, "password": "murmur-secret-1"}, headers=CSRF)
        assert r.status_code == 200
        files = {f["path"] for f in c.get(f"/api/projects/{pid}/files").json()}
        assert {"index.html", "style.css", "app.yml", "lib/util.mysya", "out/result.txt", "repl.txt",
                "empty", "empty/folder"} <= files
        assert "script.js" not in files
        read = lambda p: c.get(f"/api/projects/{pid}/file", params={"path": p}).json()["content"]
        assert read("lib/util.mysya") == 'let x = "кот"\n'
        assert read("out/result.txt") == "from script" and read("repl.txt") == "from terminal"
        assert c.get("/api/drive/download", params={"path": "/Documents/big.bin"}).content == blob
        assert c.get("/api/drive/download", params={"path": "/empty.txt"}).content == b""
        run = c.post("/api/packages/musya-hello/run", headers=CSRF).json()
        assert wait_run(c, run["project_id"], run["id"])["status"] == "done"
        # deleting a project removes its mirror
        c.delete(f"/api/projects/{pid}", headers=CSRF)
        c.post("/api/drive/delete", json={"path": "/Documents/big.bin"}, headers=CSRF)

    with TestClient(_app(tmp_path / "disk-3", db_url, site)) as c:
        c.post("/api/auth/login", json={"username": user, "password": "murmur-secret-1"}, headers=CSRF)
        assert c.get(f"/api/projects/{pid}").status_code == 404
        assert not list((tmp_path / "disk-3").rglob(f"projects/{pid}/*"))
        assert c.get("/api/drive/download", params={"path": "/Documents/big.bin"}).status_code == 404


def test_libpq_urls_are_normalized():
    from app.config import normalize_postgres_url as n
    assert n("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert n("postgresql://u:p@ep-1.neon.tech/neondb?sslmode=require&channel_binding=require") == \
        "postgresql+asyncpg://u:p@ep-1.neon.tech/neondb?ssl=require"
    assert n("postgresql://u:p@x.pooler.supabase.com:6543/postgres?pgbouncer=true").endswith("?pgbouncer=true")
    assert n("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"
