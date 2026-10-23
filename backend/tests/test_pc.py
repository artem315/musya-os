"""MUR PC against a real Docker daemon (skipped when Docker or the image is unavailable)."""

import time

import pytest
from conftest import CSRF, make_settings, register
from fastapi.testclient import TestClient

docker = pytest.importorskip("docker")


def _docker_ready(image: str) -> bool:
    try:
        cli = docker.from_env()
        cli.ping()
        cli.images.get(image)
        return True
    except Exception:
        return False


IMAGE = "murcloud/mur-pc:10.0"
pytestmark = pytest.mark.skipif(not _docker_ready(IMAGE), reason="Docker or the MUR PC image is not available")


def test_real_remote_desktop_session(tmp_path):
    from app.main import create_app
    app = create_app(make_settings(tmp_path, None, MUR_PC_ENABLED="1", MUR_PC_IMAGE=IMAGE,
                                   MUR_PC_NETWORK="murcloud-pc-test"))
    with TestClient(app) as c:
        register(c, "pcuser")
        assert c.get("/api/pc").json()["availability"]["available"] is True
        pc = c.post("/api/pc", json={"name": "Test PC", "resolution": "1024x768"}, headers=CSRF).json()
        pid = pc["id"]
        try:
            # second user cannot see or reach it
            other = register(c, "pcother")
            h = {"Authorization": f"Bearer {other['token']}"}
            assert c.get("/api/pc", headers=h).json()["sessions"] == []
            assert c.get(f"/api/pc/{pid}/connect", headers=h).status_code == 404
            c.post("/api/auth/login", json={"username": "pcuser", "password": "murmur-secret-1"}, headers=CSRF)

            conn = c.get(f"/api/pc/{pid}/connect").json()
            assert conn["ws_path"] == f"/api/pc/{pid}/vnc" and len(conn["password"]) == 8
            # wait for websockify inside the container, then speak RFB through the proxy
            banner = None
            for _ in range(60):
                try:
                    with c.websocket_connect(conn["ws_path"], subprotocols=["binary"]) as ws:
                        banner = ws.receive_bytes()
                        break
                except Exception:
                    time.sleep(0.5)
            assert banner and banner.startswith(b"RFB 003."), banner
            st = c.get(f"/api/pc/{pid}/stats").json()
            assert st["status"] == "running" and st["memory_limit"] > 0
            assert c.post(f"/api/pc/{pid}/launch/app", json={"app": "terminal"}, headers=CSRF).json() == {"launched": "terminal"}
            assert c.post(f"/api/pc/{pid}/launch/app", json={"app": "rm -rf /"}, headers=CSRF).status_code == 400
            # isolation flags actually applied
            info = docker.from_env().containers.get(pid).attrs["HostConfig"]
            assert info["CapDrop"] == ["ALL"] and "no-new-privileges:true" in info["SecurityOpt"]
            assert info["Memory"] > 0 and info["PidsLimit"] == 512 and not info["PortBindings"]
        finally:
            c.post(f"/api/pc/{pid}/delete", headers=CSRF)
        assert c.get("/api/pc").json()["sessions"] == []
