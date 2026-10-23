"""MUR AI agent tests: real tools, real files, real Chromium; only the model is scripted."""

import asyncio
import json
import threading
import http.server

import pytest

from mur_ai import ApprovalBroker, Conversation, Mode, MurAgent, SessionPolicy, Workspace
from mur_ai.providers.anthropic_provider import AnthropicProvider
from mur_ai.providers.demo_script import demo_site_turns
from mur_ai.providers.openai_provider import OpenAIProvider
from mur_ai.providers.scripted import ScriptedProvider
from mur_ai.safety import redact
from service_engine import ServiceEngine
from service_engine.preview import PreviewManager


@pytest.fixture(scope="module")
async def engine(tmp_path_factory):
    eng = ServiceEngine(data_root=tmp_path_factory.mktemp("eng"))
    yield eng
    await eng.shutdown()


def make(tmp_path, engine, provider, mode=Mode.AUTO, owner="musya"):
    previews = PreviewManager(engine)
    ws = Workspace(owner=owner, project_id="demo", root=tmp_path / "demo", engine=engine, previews=previews,
                   public_preview_url=lambda pid: f"/api/projects/{pid}/preview/")
    events, audit = [], []
    broker = ApprovalBroker()
    agent = MurAgent(provider, ws, approvals=broker, emit=lambda t, d: events.append((t, d)), audit=audit.append)
    conv = Conversation(id="c1", owner=owner, project_id="demo", provider_state=provider.new_state(),
                        policy=SessionPolicy(mode=mode))
    return agent, conv, events, audit, broker, previews


async def test_auto_mode_builds_runs_and_checks_site(tmp_path, engine):
    provider = ScriptedProvider(demo_site_turns)
    agent, conv, events, audit, broker, previews = make(tmp_path, engine, provider, Mode.AUTO)
    try:
        final = await agent.run(conv, "Создай красивый сайт для Муси, запусти и открой его")
        root = tmp_path / "demo" / "site"
        assert (root / "index.html").read_text().startswith("<!doctype html>")
        assert (root / "style.css").exists() and (root / "script.js").exists()
        tools = [i for i in conv.transcript if i["type"] == "tool"]
        assert [t["tool"] for t in tools] == ["update_plan", "write_file", "write_file", "write_file", "start_preview",
                                              "browser", "browser", "browser", "browser", "update_plan"]
        assert all(t["status"] == "done" for t in tools), [(t["tool"], t["status"], t.get("result")) for t in tools]
        shots = list(engine.screenshots_dir_for("musya").glob("musya-site*.png"))
        assert shots and shots[0].read_bytes()[:4] == b"\x89PNG"
        assert "МУРГОТОВО" in final
        assert any(t == "ai.turn_finished" for t, _ in events)
        assert any(a["event"] == "tool_call" and a["tool"] == "start_preview" for a in audit)
        # the page really was served and clicked
        assert "Мур-счётчик" in await engine.execute("musya", "text")
    finally:
        previews.stop_all()


async def test_agent_mode_requires_approval(tmp_path, engine):
    provider = ScriptedProvider([
        {"tool_calls": [{"name": "write_file", "input": {"path": "a.txt", "content": "1"}}]},
        {"tool_calls": [{"name": "write_file", "input": {"path": "b.txt", "content": "2"}}]},
        {"text": "done"},
    ])
    agent, conv, events, audit, broker, _ = make(tmp_path, engine, provider, Mode.AGENT)

    async def approver():
        answers = iter(["once", "deny"])
        while True:
            await asyncio.sleep(0.01)
            for info in broker.list("musya"):
                broker.resolve("musya", info["approval_id"], next(answers))

    task = asyncio.ensure_future(approver())
    try:
        await agent.run(conv, "write two files")
    finally:
        task.cancel()
    root = tmp_path / "demo"
    assert (root / "a.txt").read_text() == "1"
    assert not (root / "b.txt").exists()
    statuses = [i["status"] for i in conv.transcript if i["type"] == "tool"]
    assert statuses == ["done", "denied"]
    assert sum(1 for t, _ in events if t == "ai.approval_required") == 2
    # the model was told about the denial
    last_results = provider_state_results(conv)
    assert "denied" in last_results[-1]["content"]


def provider_state_results(conv):
    out = []
    for m in conv.provider_state["messages"]:
        if m.get("role") == "tool_results":
            out.extend(m["results"])
    return out


async def test_high_risk_needs_approval_even_in_auto(tmp_path, engine):
    provider = ScriptedProvider([{"tool_calls": [{"name": "delete_path", "input": {"path": "keep.txt"}}]},
                                 {"text": "ok"}])
    agent, conv, events, audit, broker, _ = make(tmp_path, engine, provider, Mode.AUTO)
    (tmp_path / "demo").mkdir(exist_ok=True)
    (tmp_path / "demo" / "keep.txt").write_text("x")

    async def deny():
        while not broker.list("musya"):
            await asyncio.sleep(0.01)
        broker.resolve("musya", broker.list("musya")[0]["approval_id"], "deny")

    asyncio.ensure_future(deny())
    await agent.run(conv, "delete it")
    assert (tmp_path / "demo" / "keep.txt").exists()


async def test_chat_mode_offers_no_tools(tmp_path, engine):
    provider = ScriptedProvider([{"text": "hi", "tool_calls": [{"name": "write_file",
                                                                "input": {"path": "x", "content": "y"}}]},
                                 {"text": "ok"}])
    agent, conv, *_ = make(tmp_path, engine, provider, Mode.CHAT)
    await agent.run(conv, "hello")
    assert provider.seen_tools[0] == []
    assert not (tmp_path / "demo" / "x").exists()
    assert [i["status"] for i in conv.transcript if i["type"] == "tool"] == ["denied"]


async def test_code_mode_has_only_file_tools(tmp_path, engine):
    provider = ScriptedProvider([{"text": "ok"}])
    agent, conv, *_ = make(tmp_path, engine, provider, Mode.CODE)
    await agent.run(conv, "hello")
    assert "browser" not in provider.seen_tools[0] and "write_file" in provider.seen_tools[0]


async def test_permission_validation_and_sandbox_errors(tmp_path, engine):
    provider = ScriptedProvider([
        {"tool_calls": [
            {"name": "browser", "input": {"command": "download", "args": ["https://example.com/a.zip"]}},
            {"name": "write_file", "input": {"path": "a.txt"}},
            {"name": "read_file", "input": {"path": "../../etc/passwd"}},
            {"name": "nope", "input": {}},
            {"name": "browser", "input": {"command": "rm"}},
        ]},
        {"text": "reported"},
    ])
    agent, conv, *_ = make(tmp_path, engine, provider, Mode.AUTO)
    await agent.run(conv, "try bad things")
    items = [i for i in conv.transcript if i["type"] == "tool"]
    assert items[0]["status"] == "denied" and "browser.download" in items[0]["result"]
    assert items[1]["status"] == "failed" and "content is required" in items[1]["result"]
    assert items[2]["status"] == "failed" and "outside the project" in items[2]["result"]
    assert items[3]["status"] == "denied"
    assert items[4]["status"] == "failed" and "must be one of" in items[4]["result"]


async def test_run_tests_tool_runs_real_musya(tmp_path, engine):
    provider = ScriptedProvider([
        {"tool_calls": [{"name": "write_file", "input": {"path": "math_test.mysya", "content":
            'func add(a, b):\n return a + b\nend\ntest "add":\n assert add(2, 2) == 4\nend\n'
            'test "broken":\n assert add(2, 2) == 5, "bad math"\nend\n'}}]},
        {"tool_calls": [{"name": "run_tests", "input": {}}]},
        {"text": "one test fails"},
    ])
    agent, conv, *_ = make(tmp_path, engine, provider, Mode.AUTO)
    await agent.run(conv, "write and run tests")
    run = [i for i in conv.transcript if i["type"] == "tool"][-1]
    assert run["status"] == "failed"
    assert "PASS math_test.mysya :: add" in run["result"] and "FAIL math_test.mysya :: broken - bad math" in run["result"]


def test_redaction():
    s = redact("key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTU and api_key=supersecret123 and ghp_abcdefghijklmnopqrstuvwxyz12")
    assert "ABCDEFGH" not in s and "supersecret" not in s and "ghp_abc" not in s


# ------------------------------------------------- provider wire format (fake HTTP servers)

class FakeAPI(http.server.BaseHTTPRequestHandler):
    requests: list = []
    response: dict = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAPI.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        data = json.dumps(FakeAPI.response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_api():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeAPI.requests = []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_anthropic_provider_wire_format(fake_api):
    from mur_ai.providers import ToolDef, ToolResult
    FakeAPI.response = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                    {"type": "text", "text": "Opening it."},
                    {"type": "tool_use", "id": "toolu_1", "name": "browser", "input": {"command": "load", "args": ["x"]}}],
        "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5}}
    p = AnthropicProvider("sk-ant-test", base_url=fake_api)
    state = p.new_state()
    p.add_user(state, "hi")
    turn = await p.complete(state, "sys", [ToolDef("browser", "d", {"type": "object", "properties": {}})])
    assert turn.text == "Opening it." and turn.tool_calls[0].name == "browser"
    assert turn.tool_calls[0].input == {"command": "load", "args": ["x"]}
    req = FakeAPI.requests[0]
    assert req["path"].startswith("/v1/messages")
    assert req["body"]["model"] == "claude-opus-5-5"
    assert req["body"]["thinking"] == {"type": "adaptive"}
    assert req["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in req["headers"].get("anthropic-beta", "")
    assert "sk-ant-test" in (req["headers"].get("x-api-key", "") + req["headers"].get("authorization", ""))
    # thinking block is preserved verbatim for the next request
    assert state["messages"][-1]["content"][0]["type"] == "thinking"
    p.add_tool_results(state, [ToolResult("toolu_1", "ok", False, [("image/png", "AAAA")])])
    tr = state["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result" and tr["content"][1]["type"] == "image"


async def test_openai_provider_wire_format(fake_api):
    from mur_ai.providers import ToolDef
    FakeAPI.response = {
        "id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-5",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a\"}"}},
                {"id": "call_2", "type": "function", "function": {"name": "read_file", "arguments": "{bad"}}]}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
    p = OpenAIProvider("sk-test", base_url=fake_api + "/v1")
    state = p.new_state()
    p.add_user(state, "hi")
    turn = await p.complete(state, "sys", [ToolDef("read_file", "d", {"type": "object", "properties": {}})])
    assert turn.tool_calls[0].input == {"path": "a"}
    assert turn.tool_calls[1].parse_error
    body = FakeAPI.requests[0]["body"]
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["function"]["name"] == "read_file"
