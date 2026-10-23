"""Service Engine tests against a real headless Chromium and a local test site."""

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from musya_runtime import Interpreter, MusyaPermissionError, MusyaRuntimeError
from service_engine import BlockedURL, EngineHost, NetworkPolicy, ServiceEngine, ServiceError

PAGES = {
    "/": """<!doctype html><html><head><title>Musya Test Site</title></head><body>
        <h1>Example Domain</h1>
        <a href="/file.zip">Download</a>
        <a href="/second">More information</a>
        <form onsubmit="event.preventDefault(); document.getElementById('out').textContent = 'Hi ' + document.getElementById('q').value;">
          <input id="q" placeholder="Search here"> <button type="submit">Send</button>
        </form>
        <p id="out"></p>
        <div style="height:3000px">tall</div>
        <img src="http://127.0.0.2:9/tracker.gif">
        </body></html>""",
    "/second": "<html><head><title>Second</title></head><body><h2>Second page</h2></body></html>",
    "/broken": "<html><body><script>console.error('boom from page'); undefinedFunction();</script></body></html>",
    "/delayed": """<html><body><script>setTimeout(() => { document.body.innerHTML += '<b>Ready now</b>' }, 400)</script></body></html>""",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/file.zip":
            body = b"PK\x03\x04" + b"musya" * 100
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="murx-driver.zip"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1/secret")
            self.end_headers()
            return
        if self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Length", str(10 * 1024 * 1024))
            self.end_headers()
            return
        body = PAGES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
async def engine(site, tmp_path_factory):
    eng = ServiceEngine(data_root=tmp_path_factory.mktemp("engine"), allowed_hosts={site},
                        max_download_bytes=1024 * 1024)
    yield eng
    await eng.shutdown()


async def test_load_search_click_type_press_scroll_screenshot(engine, site):
    owner = "u1"
    base = f"http://{site}"
    res = await engine.execute(owner, "load", [base + "/"])
    assert res["title"] == "Musya Test Site" and res["status"] == 200

    found = await engine.execute(owner, "search", ["Download"])
    assert found["count"] >= 1 and found["matches"][0]["tag"] == "a"

    assert await engine.execute(owner, "exists", ["More information"]) is True
    assert await engine.execute(owner, "exists", ["Nonexistent text 123"]) is False

    await engine.execute(owner, "click", ["Search here"])
    await engine.execute(owner, "type", ["Hello from MURCLOUD"])
    await engine.execute(owner, "press", ["Enter"])
    await engine.execute(owner, "wait", ["Hi Hello from MURCLOUD", 3000])
    assert "Hi Hello from MURCLOUD" in await engine.execute(owner, "text", ["#out"])

    scrolled = await engine.execute(owner, "scroll", ["down", 500])
    assert scrolled["position"]["y"] > 0

    shot = await engine.execute(owner, "screenshot", ["home"])
    p = Path(shot["path"])
    assert p.exists() and p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and shot["filename"] == "home.png"

    await engine.execute(owner, "click", ["More information"])
    assert await engine.execute(owner, "title") == "Second"
    back = await engine.execute(owner, "back")
    assert back["title"] == "Musya Test Site"


async def test_tracker_to_private_ip_was_blocked(engine, site):
    errors = await engine.execute("u1", "errors")
    assert any("127.0.0.2" in b["url"] for b in errors["blocked"])


async def test_tabs_and_status(engine, site):
    owner = "u2"
    await engine.execute(owner, "load", [f"http://{site}/"])
    await engine.execute(owner, "tab", ["new", f"http://{site}/second"])
    tabs = await engine.execute(owner, "tab", ["list"])
    assert len(tabs) == 2 and tabs[1]["active"] and tabs[1]["title"] == "Second"
    await engine.execute(owner, "tab", ["switch", 0])
    st = await engine.execute(owner, "status")
    assert st["running"] and st["active_tab"] == 0 and len(st["tabs"]) == 2
    await engine.execute(owner, "tab", ["close", 1])
    assert len(await engine.execute(owner, "tab", ["list"])) == 1


async def test_console_errors_collected(engine, site):
    await engine.execute("u3", "load", [f"http://{site}/broken"])
    await asyncio.sleep(0.2)
    errs = await engine.execute("u3", "errors")
    texts = " ".join(e["text"] for e in errs["console"])
    assert "boom from page" in texts and "undefinedFunction" in texts


async def test_wait_for_late_text_and_timeout(engine, site):
    await engine.execute("u4", "load", [f"http://{site}/delayed"])
    res = await engine.execute("u4", "wait", ["Ready now", 5000])
    assert res["found"]
    with pytest.raises(ServiceError) as exc:
        await engine.execute("u4", "wait", ["Never appears", 300])
    assert exc.value.kind == "Timeout"


async def test_download_safe(engine, site):
    res = await engine.execute("u5", "download", [f"http://{site}/file.zip"])
    assert res["filename"] == "murx-driver.zip" and res["size"] == 504
    assert Path(res["path"]).read_bytes().startswith(b"PK")
    with pytest.raises(ServiceError, match="redirect blocked"):
        await engine.execute("u5", "download", [f"http://{site}/redirect-private"])
    with pytest.raises(ServiceError, match="limit"):
        await engine.execute("u5", "download", [f"http://{site}/big"])
    with pytest.raises(ServiceError) as exc:
        await engine.execute("u5", "download", ["http://169.254.169.254/latest/meta-data"])
    assert exc.value.kind == "BlockedURL"


async def test_ssrf_navigation_blocked(engine):
    for url in ["http://localhost:8000/", "http://169.254.169.254/", "file:///etc/passwd", "http://[::1]/",
                "http://10.1.2.3/", "javascript:alert(1)"]:
        with pytest.raises(ServiceError) as exc:
            await engine.execute("u6", "load", [url])
        assert exc.value.kind == "BlockedURL", url


async def test_permissions_enforced(engine, site):
    with pytest.raises(ServiceError) as exc:
        await engine.execute("u7", "download", [f"http://{site}/file.zip"], permissions={"browser.read"})
    assert exc.value.kind == "PermissionError"
    with pytest.raises(ServiceError, match="unknown command"):
        await engine.execute("u7", "rm", ["-rf"])
    with pytest.raises(ServiceError, match="expects"):
        await engine.execute("u7", "load", [])


async def test_queue_order_and_cancel(engine, site):
    owner = "u8"
    events = engine.bus.subscribe(owner)
    j1 = engine.submit(owner, "wait", [800])
    j2 = engine.submit(owner, "wait", [10])
    j3 = engine.submit(owner, "wait", [10])
    assert engine.cancel(owner, j3.id) == [j3.id]
    await asyncio.sleep(0.1)
    assert j1.status == "running" and j2.status == "queued"
    engine.cancel(owner, j1.id)
    await asyncio.wait_for(j2.future, 2)
    assert j1.status == "cancelled" and j2.status == "done" and j3.status == "cancelled"
    types = []
    while not events.queue.empty():
        types.append(events.queue.get_nowait()["type"])
    events.close()
    assert types[:3] == ["job.queued", "job.queued", "job.queued"]
    assert "job.started" in types and "job.cancelled" in types and "job.finished" in types


async def test_job_timeout(engine):
    job = engine.submit("u9", "wait", [5000], timeout=1)
    with pytest.raises(ServiceError) as exc:
        await job.future
    assert exc.value.kind == "Timeout" and job.status == "timeout"


async def test_mysya_script_drives_real_browser(engine, site, tmp_path):
    out = []
    host = EngineHost(tmp_path, engine, owner="u10", output_sink=out.append)
    interp = Interpreter(host)
    host.attach(interp)
    src = f'''
app "Musya Browser"
let url = "http://{site}/"
let loads = 0
on page_loaded(info):
    loads += 1
end
service load url
service wait "Example Domain"
let shot = service screenshot
if service.exists("More information"):
    service click "More information"
end
print service.title(), shot.filename.ends_with(".png"), loads >= 1
print "МУРГОТОВО!"
'''
    try:
        await interp.run_source(src, "browser.mysya")
    finally:
        host.detach()
    assert out == ["Second true true", "МУРГОТОВО!"]


async def test_script_permissions_from_manifest(engine, site, tmp_path):
    host = EngineHost(tmp_path, engine, permissions={"browser.read", "browser.navigate"}, owner="u11",
                      output_sink=lambda s: None)
    interp = Interpreter(host)
    with pytest.raises(MusyaPermissionError, match="browser.screenshot"):
        await interp.run_source(f'service load "http://{site}/"\nservice screenshot')
    with pytest.raises(MusyaRuntimeError) as exc:
        await Interpreter(host).run_source('service load "http://10.0.0.5/"')
    assert exc.value.kind == "BlockedURL"


async def test_policy_unit():
    pol = NetworkPolicy()
    assert await pol.check_url("1.1.1.1") == "https://1.1.1.1"
    for bad in ["http://127.0.0.1/", "http://0.0.0.0/", "http://[::ffff:127.0.0.1]/", "ftp://example.com",
                "http://100.64.0.1/", "http://192.168.1.1/", "http://foo.localhost/"]:
        with pytest.raises(BlockedURL):
            await pol.check_url(bad)
    pol.allow("127.0.0.1:8080")
    assert await pol.check_url("http://127.0.0.1:8080/x")
