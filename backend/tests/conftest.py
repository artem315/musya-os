import http.server
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

if os.path.exists("/opt/pw-browsers/chromium"):  # preinstalled browser in some sandboxes
    os.environ.setdefault("MUR_CHROMIUM_PATH", "/opt/pw-browsers/chromium")

SITE = {
    "/": "<html><head><title>Backend Test Site</title></head><body><h1>Hello MURCLOUD</h1>"
         "<a href='/two'>Next page</a></body></html>",
    "/two": "<html><head><title>Two</title></head><body><p>Second</p></body></html>",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = SITE.get(self.path)
        if self.path == "/file.txt":
            data = b"downloaded by murcloud"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="session")
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def make_settings(data_dir, site=None, **env):
    from app.config import Settings
    old = dict(os.environ)
    os.environ.update({"MUR_DATA_DIR": str(data_dir), "MUR_AI_ALLOW_SCRIPTED": "1",
                       "DATABASE_URL": os.environ.get("MUR_TEST_DATABASE_URL", ""),
                       "MUR_SSRF_ALLOW_HOSTS": site or "", "MUR_RATE_LIMIT": "100000", **env})
    try:
        return Settings()
    finally:
        os.environ.clear()
        os.environ.update(old)


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("murcloud-data")


def _reset_postgres():
    url = os.environ.get("MUR_TEST_DATABASE_URL")
    if not url:
        return
    import asyncio
    from app.db import Base, Database

    async def go():
        db = Database(url.replace("postgresql://", "postgresql+asyncpg://"))
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.dispose()
    asyncio.run(go())


@pytest.fixture(scope="session")
def client(data_dir, site):
    _reset_postgres()
    from app.main import create_app
    app = create_app(make_settings(data_dir, site))
    with TestClient(app) as c:
        yield c


CSRF = {"X-MUR-CSRF": "1"}


def register(client, username, password="murmur-secret-1"):
    client.app.state.core.limiter.reset()
    r = client.post("/api/auth/register", json={"username": username, "password": password}, headers=CSRF)
    assert r.status_code == 200, r.text
    return r.json()


def wait_run(client, project_id, run_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/projects/{project_id}/runs/{run_id}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.2)
    raise AssertionError("run did not finish")
