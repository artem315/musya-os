"""End-to-end API tests against the real app (SQLite, real Chromium, real files)."""

import io
import time

import pytest
from conftest import CSRF, make_settings, register, wait_run
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def musya(client):
    data = register(client, "musya")
    projects = client.get("/api/projects").json()["projects"]
    return {"user": data["user"], "token": data["token"], "projects": {p["name"]: p for p in projects}}


def test_first_user_is_admin_and_defaults_created(musya):
    assert musya["user"]["is_admin"] is True
    assert {"Musya Hello", "Musya Browser"} <= set(musya["projects"])


def test_register_validation(client, musya):
    assert client.post("/api/auth/register", json={"username": "musya", "password": "another-pass-1"},
                       headers=CSRF).status_code == 409
    r = client.post("/api/auth/register", json={"username": "kot", "password": "short"}, headers=CSRF)
    assert r.status_code in (400, 422)
    assert client.post("/api/auth/register", json={"username": "bad name!", "password": "long-enough-1"},
                       headers=CSRF).status_code == 400


def test_password_is_hashed(client, musya):
    import asyncio
    from app.db import User
    from sqlalchemy import select
    core = client.app.state.core

    async def fetch():
        async with core.db.sessionmaker() as db:
            return (await db.execute(select(User).where(User.username == "musya"))).scalar_one()
    user = client.portal.call(fetch) if hasattr(client, "portal") and client.portal else asyncio.run(fetch())
    assert user.password_hash.startswith("scrypt$") and "murmur-secret-1" not in user.password_hash


def test_me_csrf_and_bearer(client, musya):
    assert client.get("/api/auth/me").json()["username"] == "musya"
    # cookie auth without CSRF header is refused for state changes
    r = client.post("/api/projects", json={"name": "x"})
    assert r.status_code == 403
    # bearer auth needs no CSRF header
    r = TestClient.post(client, "/api/projects", json={"name": "Bearer Project"},
                        headers={"Authorization": f"Bearer {musya['token']}"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_login_and_lockout(client, musya):
    r = client.post("/api/auth/login", json={"username": "musya", "password": "wrong"}, headers=CSRF)
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "MUSYA", "password": "murmur-secret-1"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["user"]["username"] == "musya"
    # a separate user gets locked after 5 failures
    register(client, "lockme")
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "lockme", "password": "bad-password"}, headers=CSRF)
    r = client.post("/api/auth/login", json={"username": "lockme", "password": "murmur-secret-1"}, headers=CSRF)
    assert r.status_code == 429
    # back to musya's session
    client.post("/api/auth/login", json={"username": "musya", "password": "murmur-secret-1"}, headers=CSRF)


def test_project_files_and_diagnostics(client, musya):
    p = client.post("/api/projects", json={"name": "Сайт Муси", "template": "website"}, headers=CSRF).json()
    pid = p["id"]
    files = {f["path"] for f in client.get(f"/api/projects/{pid}/files").json()}
    assert {"index.html", "style.css", "script.js", "app.yml", "main.mysya"} <= files
    r = client.put(f"/api/projects/{pid}/file", json={"path": "lib/util.mysya", "content": "let x = (1 +\n"},
                   headers=CSRF).json()
    assert r["diagnostics"] and r["diagnostics"][0]["severity"] == "error"
    r = client.put(f"/api/projects/{pid}/file", json={"path": "lib/util.mysya", "content": "let x = 1\n"},
                   headers=CSRF).json()
    assert r["diagnostics"] == []
    assert client.get(f"/api/projects/{pid}/file", params={"path": "lib/util.mysya"}).json()["content"] == "let x = 1\n"
    assert client.post(f"/api/projects/{pid}/rename", json={"path": "lib/util.mysya", "new_path": "lib/u.mysya"},
                       headers=CSRF).status_code == 200
    assert client.get(f"/api/projects/{pid}/file", params={"path": "../../../etc/passwd"}).status_code == 400
    bad = client.put(f"/api/projects/{pid}/file", json={"path": "app.yml", "content": "name: x\nversion: nope\n"},
                     headers=CSRF).json()
    assert any("semantic" in d["message"] for d in bad["diagnostics"])
    build = client.post(f"/api/projects/{pid}/build", headers=CSRF).json()
    assert build["ok"] is False
    client.put(f"/api/projects/{pid}/file", json={"path": "app.yml", "content":
               "name: Site\nversion: 1.0.0\nentry: main.mysya\npermissions: [files.read]\ntests: [site_test.mysya]\n"},
               headers=CSRF)
    assert client.post(f"/api/projects/{pid}/build", headers=CSRF).json()["ok"] is True
    exp = client.post(f"/api/projects/{pid}/export", headers=CSRF).json()
    assert exp["path"].startswith("/Exports/") and exp["size"] > 100
    musya["site_project"] = pid


def test_run_hello_and_tests(client, musya):
    pid = musya["projects"]["Musya Hello"]["id"]
    run = client.post(f"/api/projects/{pid}/run", json={}, headers=CSRF).json()
    done = wait_run(client, pid, run["id"])
    assert done["status"] == "done", done
    assert "МУРГОТОВО!" in done["output"][-1]
    sp = musya["site_project"]
    run = client.post(f"/api/projects/{sp}/run", json={"path": "site_test.mysya", "test": True}, headers=CSRF).json()
    done = wait_run(client, sp, run["id"])
    assert done["status"] == "done" and done["tests"][0]["passed"] is True


def test_run_browser_script_with_manifest_permissions(client, musya, site):
    pid = musya["projects"]["Musya Browser"]["id"]
    script = (f'service load "http://{site}/"\nservice wait "Hello MURCLOUD"\nlet s = service screenshot "api-shot"\n'
              'service click "Next page"\nprint service.title(), s.filename\n')
    client.put(f"/api/projects/{pid}/file", json={"path": "main.mysya", "content": script}, headers=CSRF)
    run = client.post(f"/api/projects/{pid}/run", json={}, headers=CSRF).json()
    assert "browser.screenshot" in run["permissions"] and "browser.download" not in run["permissions"]
    done = wait_run(client, pid, run["id"])
    assert done["status"] == "done", done
    assert done["output"] == ["Two api-shot.png"]
    # screenshot was copied into MUR Drive
    time.sleep(0.5)
    items = client.get("/api/drive/list", params={"path": "/Screenshots"}).json()["items"]
    assert any(i["name"] == "api-shot.png" for i in items)
    # download is not in this app's manifest -> PermissionError
    client.put(f"/api/projects/{pid}/file", json={"path": "dl.mysya", "content":
               f'service download "http://{site}/file.txt"'}, headers=CSRF)
    run = client.post(f"/api/projects/{pid}/run", json={"path": "dl.mysya"}, headers=CSRF).json()
    done = wait_run(client, pid, run["id"])
    assert done["status"] == "failed" and done["error"]["type"] == "PermissionError"


def test_engine_api_commands_and_ssrf(client, musya, site):
    r = client.post("/api/engine/command", json={"verb": "load", "args": [f"http://{site}/two"]}, headers=CSRF).json()
    assert r["status"] == "done" and r["result"]["title"] == "Two"
    r = client.post("/api/engine/command", json={"verb": "search", "args": ["Second"]}, headers=CSRF).json()
    assert r["result"]["count"] == 1
    r = client.post("/api/engine/command", json={"verb": "load", "args": ["http://169.254.169.254/latest"]},
                    headers=CSRF).json()
    assert r["status"] == "failed" and r["error"]["type"] == "BlockedURL"
    r = client.post("/api/engine/command", json={"verb": "download", "args": [f"http://{site}/file.txt"]},
                    headers=CSRF).json()
    assert r["status"] == "done"
    time.sleep(0.5)
    items = client.get("/api/drive/list", params={"path": "/Downloads"}).json()["items"]
    assert any(i["name"] == "file.txt" and i["size"] == 22 for i in items)
    frame = client.get("/api/engine/frame").json()
    assert frame["image"].startswith("data:image/jpeg;base64,")
    st = client.get("/api/engine/status").json()
    assert st["running"] and st["tabs"]
    jobs = client.get("/api/engine/jobs").json()
    assert jobs[0]["verb"] in ("download", "load", "status")


def test_terminal_repl(client, musya):
    pid = musya["projects"]["Musya Hello"]["id"]
    t = lambda line: client.post("/api/engine/terminal", json={"line": line, "project_id": pid}, headers=CSRF).json()
    assert t("let x = 21 * 2")["output"] == []
    assert t("x")["output"] == ["42"]
    assert t('print "мур", x')["output"] == ["мур 42"]
    st = t("service status")
    assert st["result"]["running"] is True
    assert "MUR Terminal" in t("help")["output"][0]
    assert t("ls")["output"]
    err = t("let = 1")
    assert err["error"]["type"] == "SyntaxError"
    ai = t("service ai status")
    assert "active" in ai["result"]


def test_websocket_events(client, musya):
    pid = musya["projects"]["Musya Hello"]["id"]
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        client.post(f"/api/projects/{pid}/run", json={}, headers=CSRF)
        seen = set()
        for _ in range(200):
            ev = ws.receive_json()
            seen.add(ev["type"])
            if ev["type"] == "run.finished":
                break
        assert {"run.started", "run.output", "run.finished"} <= seen
        ws.send_json({"type": "ping", "ts": 1})
        for _ in range(50):
            if ws.receive_json()["type"] == "pong":
                break
        else:
            raise AssertionError("no pong")


def test_websocket_rejects_foreign_origin_and_anonymous(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_drive_crud_isolation_and_preview(client, musya):
    up = client.post("/api/drive/upload", files={"file": ("notes.txt", io.BytesIO("Мур мур".encode()), "text/plain")},
                     data={"path": "/Documents"}, headers=CSRF)
    assert up.status_code == 200, up.text
    assert client.get("/api/drive/download", params={"path": "/Documents/notes.txt"}).content == "Мур мур".encode()
    prev = client.get("/api/drive/preview", params={"path": "/Documents/notes.txt"}).json()
    assert prev["kind"] == "text" and prev["text"] == "Мур мур"
    client.post("/api/drive/mkdir", json={"path": "/Documents/Cats"}, headers=CSRF)
    r = client.post("/api/drive/rename", json={"path": "/Documents/notes.txt", "new_path": "/Documents/Cats/n.txt"},
                    headers=CSRF)
    assert r.status_code == 200
    r = client.post("/api/drive/rename", json={"path": "/Documents/Cats", "new_path": "/Archive/Cats"}, headers=CSRF)
    assert r.status_code == 200
    assert client.get("/api/drive/download", params={"path": "/Archive/Cats/n.txt"}).status_code == 200
    assert client.get("/api/drive/search", params={"q": "n.t"}).json()["items"][0]["path"] == "/Archive/Cats/n.txt"
    assert client.get("/api/drive/list", params={"path": "/../etc"}).status_code == 400
    # another user cannot see it
    other = register(client, "krasavchik")
    h = {"Authorization": f"Bearer {other['token']}"}
    assert client.get("/api/drive/download", params={"path": "/Archive/Cats/n.txt"}, headers=h).status_code == 404
    assert client.get(f"/api/projects/{musya['site_project']}", headers=h).status_code == 404
    client.post("/api/auth/login", json={"username": "musya", "password": "murmur-secret-1"}, headers=CSRF)
    assert client.post("/api/drive/delete", json={"path": "/Archive"}, headers=CSRF).json()["deleted"] == 3


def test_preview_links(client, musya):
    pid = musya["site_project"]
    link = client.get(f"/api/projects/{pid}/preview-link").json()["url"]
    r = client.get(link)
    assert r.status_code == 200 and "<html" in r.text and "sandbox" in r.headers["content-security-policy"]
    css = client.get(link.replace("index.html", "style.css"))
    assert css.status_code == 200
    token = link.split("/")[2]
    assert client.get(f"/p/{token[:-1]}x/index.html").status_code == 404
    started = client.post(f"/api/projects/{pid}/preview/start", json={"path": "."}, headers=CSRF).json()
    assert started["url"].startswith("http://127.0.0.1:")
    r = client.post("/api/engine/command", json={"verb": "load", "args": [started["url"]]}, headers=CSRF).json()
    assert r["status"] == "done", r
    client.post(f"/api/projects/{pid}/preview/stop", headers=CSRF)


def test_ai_keys_are_encrypted_and_hidden(client, musya):
    r = client.put("/api/ai/keys", json={"provider": "anthropic", "api_key": "sk-ant-test-1234567890abcd"},
                   headers=CSRF).json()
    assert r["hint"] == "…abcd"
    provs = client.get("/api/ai/providers").json()
    assert provs["keys"][0]["hint"] == "…abcd" and "api_key" not in str(provs["keys"])
    import asyncio
    from app.db import ApiKey
    from sqlalchemy import select
    core = client.app.state.core

    async def fetch():
        async with core.db.sessionmaker() as db:
            return (await db.execute(select(ApiKey))).scalars().all()
    rows = client.portal.call(fetch)
    assert rows and all("sk-ant-test" not in r.encrypted_key for r in rows)
    assert core.secretbox.decrypt(rows[0].encrypted_key) == "sk-ant-test-1234567890abcd"
    client.delete("/api/ai/keys/anthropic", headers=CSRF)


def test_ai_without_key_reports_error(client, musya):
    pid = musya["projects"]["Musya Hello"]["id"]
    conv = client.post("/api/ai/conversations", json={"project_id": pid, "provider": "anthropic"}, headers=CSRF).json()
    r = client.post(f"/api/ai/conversations/{conv['id']}/messages", json={"text": "hi"}, headers=CSRF)
    assert r.status_code == 400 and "API key" in r.json()["error"]["message"]


def wait_conv(client, cid, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        c = client.get(f"/api/ai/conversations/{cid}").json()
        if not c["running"]:
            return c
        if c["approvals"]:
            return c
        time.sleep(0.3)
    raise AssertionError("conversation did not finish")


def test_ai_agent_builds_site_with_approvals(client, musya):
    """The full readiness scenario: ask MUR AI to build & open a site, approve its tools, check results."""
    p = client.post("/api/projects", json={"name": "AI Site"}, headers=CSRF).json()
    conv = client.post("/api/ai/conversations", json={"project_id": p["id"], "provider": "scripted", "mode": "agent"},
                       headers=CSRF).json()
    r = client.post(f"/api/ai/conversations/{conv['id']}/messages",
                    json={"text": "Создай красивый сайт для Муси, запусти и открой его"}, headers=CSRF)
    assert r.status_code == 200, r.text
    approved = 0
    for _ in range(30):
        c = wait_conv(client, conv["id"])
        if not c["approvals"]:
            break
        for a in c["approvals"]:
            assert a["risk"] in ("medium", "high")
            client.post(f"/api/ai/approvals/{a['approval_id']}", json={"decision": "session"}, headers=CSRF)
            approved += 1
        time.sleep(0.2)
    c = client.get(f"/api/ai/conversations/{conv['id']}").json()
    assert not c["running"]
    tools = [i for i in c["transcript"] if i["type"] == "tool"]
    assert all(t["status"] == "done" for t in tools), [(t["tool"], t["status"], t.get("result")) for t in tools]
    assert approved >= 2  # write_file (approved for session) and browser/preview steps
    assert "МУРГОТОВО" in [i for i in c["transcript"] if i["type"] == "assistant"][-1]["text"]
    files = {f["path"] for f in client.get(f"/api/projects/{p['id']}/files").json()}
    assert {"site/index.html", "site/style.css", "site/script.js"} <= files
    audit = client.get("/api/ai/audit").json()
    assert any(a["event"] == "tool_call" and a["tool"] == "write_file" for a in audit)
    assert any(a["event"] == "approval" for a in audit)
    shots = client.get("/api/drive/list", params={"path": "/Screenshots"}).json()["items"]
    assert any(i["name"].startswith("musya-site") for i in shots)


def test_packages_install_with_permission_confirmation(client, musya):
    cat = client.get("/api/packages").json()["catalog"]
    ids = {c["id"] for c in cat if c["valid"]}
    assert {"murx-click", "mur-browser", "musya-hello", "murx-theme-pack"} <= ids
    r = client.post("/api/packages/murx-theme-pack/install", json={}, headers=CSRF)
    assert r.status_code == 403 and r.json()["error"]["needs_approval"] == ["files.write"]
    r = client.post("/api/packages/murx-theme-pack/install", json={"approved_permissions": ["files.write"]},
                    headers=CSRF)
    assert r.status_code == 200
    run = client.post("/api/packages/murx-theme-pack/run", headers=CSRF).json()
    done = wait_run(client, run["project_id"], run["id"])
    assert done["status"] == "done", done
    assert client.post("/api/packages/musya-hello/install", json={}, headers=CSRF).status_code == 200
    names = [p["id"] for p in client.get("/api/packages").json()["installed"]]
    assert "murx-theme-pack" in names and "musya-hello" in names
    assert client.delete("/api/packages/musya-hello", headers=CSRF).status_code == 200


def test_app_state_and_settings(client, musya):
    client.put("/api/apps/soup/state/progress", json={"value": {"portions": 7}}, headers=CSRF)
    assert client.get("/api/apps/soup/state/progress").json()["value"] == {"portions": 7}
    r = client.patch("/api/settings", json={"settings": {"theme": "dark", "accent": "#22d3ee"}}, headers=CSRF)
    assert r.json()["settings"]["theme"] == "dark"
    assert client.patch("/api/settings", json={"settings": {"is_admin": True}}, headers=CSRF).status_code == 400


def test_system_endpoints(client, musya):
    assert client.get("/api/health").json()["ok"]
    st = client.get("/api/system/stats").json()
    assert 0 <= st["cpu_percent"] <= 100 and st["memory"]["total"] > 0
    info = client.get("/api/system/info").json()
    assert info["developer"] == "ARTYONIX STUDIOS" and info["pc"]["available"] is False
    assert client.get("/api/pc").json()["availability"]["available"] is False


def test_rate_limit_on_register(client):
    core = client.app.state.core
    core.limiter.reset()
    codes = [client.post("/api/auth/register", json={"username": f"spam{i}", "password": "murmur-secret-1"},
                         headers=CSRF).status_code for i in range(7)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
    core.limiter.reset()
    client.post("/api/auth/login", json={"username": "musya", "password": "murmur-secret-1"}, headers=CSRF)


def test_data_survives_restart(tmp_path, site):
    """Create data, stop the whole backend, start a new one on the same volume, check everything is there."""
    from app.main import create_app
    with TestClient(create_app(make_settings(tmp_path, site))) as c:
        register(c, "persist")
        pid = c.post("/api/projects", json={"name": "Keep"}, headers=CSRF).json()["id"]
        c.put(f"/api/projects/{pid}/file", json={"path": "main.mysya", "content": 'print "still here"'}, headers=CSRF)
        c.post("/api/drive/upload", files={"file": ("a.bin", io.BytesIO(b"\x00\x01"), "application/octet-stream")},
               data={"path": "/"}, headers=CSRF)
        c.put("/api/apps/soup/state/progress", json={"value": {"portions": 3}}, headers=CSRF)
    with TestClient(create_app(make_settings(tmp_path, site))) as c2:
        r = c2.post("/api/auth/login", json={"username": "persist", "password": "murmur-secret-1"}, headers=CSRF)
        assert r.status_code == 200
        assert c2.get(f"/api/projects/{pid}/file", params={"path": "main.mysya"}).json()["content"] == 'print "still here"'
        assert c2.get("/api/drive/download", params={"path": "/a.bin"}).content == b"\x00\x01"
        assert c2.get("/api/apps/soup/state/progress").json()["value"] == {"portions": 3}
        run = c2.post(f"/api/projects/{pid}/run", json={"path": "main.mysya"}, headers=CSRF).json()
        assert wait_run(c2, pid, run["id"])["output"] == ["still here"]
