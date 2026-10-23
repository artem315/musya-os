"""MUR AI tools - real actions the agent can take, each with schema, permission and risk.

Every tool returns a :class:`ToolOutput` describing what *actually*
happened. Failures are returned as errors to the model (never hidden), so it
cannot claim success that did not occur.
"""

from __future__ import annotations

import base64
import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from musya_runtime import ManifestError, MusyaError, load_manifest_file, parse

from ..safety import Risk, wrap_untrusted
from .schema import SchemaError, validate
from .workspace import MAX_PROJECT_FILES, MAX_READ_BYTES, MAX_WRITE_BYTES, Workspace

TEXT_SUFFIXES = {".mysya", ".yml", ".yaml", ".driver", ".html", ".htm", ".css", ".js", ".mjs", ".ts", ".tsx",
                 ".jsx", ".json", ".md", ".txt", ".py", ".svg", ".xml", ".csv", ".toml", ".ini", ".sh", ""}


@dataclass
class ToolOutput:
    content: str
    is_error: bool = False
    data: dict = field(default_factory=dict)  # structured result for the UI
    images: list[tuple[str, str]] = field(default_factory=list)  # (media_type, base64) for vision models


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    category: str
    risk: Risk | Callable[[dict], Risk]
    permission: str | None | Callable[[dict], str | None]
    handler: Callable[[Workspace, dict], Awaitable[ToolOutput]]

    def risk_for(self, args: dict) -> Risk:
        return self.risk(args) if callable(self.risk) else self.risk

    def permission_for(self, args: dict) -> str | None:
        return self.permission(args) if callable(self.permission) else self.permission

    def validate(self, args: dict) -> None:
        validate(args, self.schema)


def obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


PATH = {"type": "string", "minLength": 1, "maxLength": 300, "description": "Path relative to the project root"}


# ------------------------------------------------------------------ files

async def t_list_files(ws: Workspace, a: dict) -> ToolOutput:
    base = ws.path(a.get("path", "."))
    if not base.is_dir():
        return ToolOutput(f"'{a.get('path', '.')}' is not a folder", True)
    entries = []
    for p in sorted(base.rglob("*") if a.get("recursive", True) else base.iterdir()):
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        entries.append(ws.rel(p) + ("/" if p.is_dir() else f"  ({p.stat().st_size} B)"))
        if len(entries) >= 500:
            entries.append("… (truncated)")
            break
    return ToolOutput("\n".join(entries) or "(empty folder)", data={"entries": len(entries)})


async def t_read_file(ws: Workspace, a: dict) -> ToolOutput:
    p = ws.path(a["path"])
    if not p.is_file():
        return ToolOutput(f"file '{a['path']}' does not exist", True)
    if p.suffix.lower() not in TEXT_SUFFIXES:
        return ToolOutput(f"'{a['path']}' is a binary file ({p.stat().st_size} bytes) - not shown", True)
    raw = p.read_bytes()
    truncated = len(raw) > MAX_READ_BYTES
    text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    numbered = "\n".join(f"{i + 1:4} | {line}" for i, line in enumerate(text.split("\n")))
    return ToolOutput(numbered + ("\n… (truncated)" if truncated else ""), data={"path": a["path"], "bytes": len(raw)})


async def t_write_file(ws: Workspace, a: dict) -> ToolOutput:
    content = a["content"]
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        return ToolOutput("content is larger than the 2 MB write limit", True)
    p = ws.path(a["path"])
    if p == ws.root or p.is_dir():
        return ToolOutput(f"'{a['path']}' is a folder", True)
    if not p.exists() and ws.file_count() >= MAX_PROJECT_FILES:
        return ToolOutput("project file limit reached", True)
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    rel = ws.rel(p)
    await ws.changed(rel, "modified" if existed else "created")
    note = ""
    if p.suffix == ".mysya":
        try:
            parse(content, rel)
            note = " Syntax check: OK."
        except MusyaError as exc:
            note = f" WARNING syntax error: {exc}"
    elif p.suffix in (".yml", ".driver") and p.name != "docker-compose.yml":
        try:
            load_manifest_file(p, project_root=ws.root)
            note = " Manifest check: OK."
        except ManifestError as exc:
            note = f" WARNING manifest problems: {'; '.join(exc.errors)}"
    return ToolOutput(f"{'Updated' if existed else 'Created'} {rel} ({len(data)} bytes).{note}",
                      data={"path": rel, "bytes": len(data), "action": "modified" if existed else "created"})


async def t_make_dir(ws: Workspace, a: dict) -> ToolOutput:
    p = ws.path(a["path"])
    p.mkdir(parents=True, exist_ok=True)
    await ws.changed(ws.rel(p), "created")
    return ToolOutput(f"Folder {ws.rel(p)} is ready.", data={"path": ws.rel(p)})


async def t_delete_path(ws: Workspace, a: dict) -> ToolOutput:
    p = ws.path(a["path"])
    if p == ws.root:
        return ToolOutput("refusing to delete the project root", True)
    if not p.exists():
        return ToolOutput(f"'{a['path']}' does not exist", True)
    rel = ws.rel(p)
    if p.is_dir():
        files = list(p.rglob("*"))
        if len(files) > 200:
            return ToolOutput("folder has more than 200 entries - delete it from MUR Drive instead", True)
        for f in sorted(files, key=lambda x: len(x.parts), reverse=True):
            f.rmdir() if f.is_dir() else f.unlink()
        p.rmdir()
    else:
        p.unlink()
    await ws.changed(rel, "deleted")
    return ToolOutput(f"Deleted {rel}.", data={"path": rel})


async def t_search_files(ws: Workspace, a: dict) -> ToolOutput:
    q = a["query"]
    pattern = a.get("glob", "*")
    hits = []
    for p in sorted(ws.root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES or p.stat().st_size > MAX_READ_BYTES:
            continue
        if not fnmatch.fnmatch(p.name, pattern):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").split("\n")):
            if q.lower() in line.lower():
                hits.append(f"{ws.rel(p)}:{i + 1}: {line.strip()[:200]}")
                if len(hits) >= 100:
                    break
        if len(hits) >= 100:
            break
    return ToolOutput("\n".join(hits) or f"no matches for '{q}'", data={"matches": len(hits)})


async def t_validate_manifest(ws: Workspace, a: dict) -> ToolOutput:
    p = ws.path(a["path"])
    if not p.is_file():
        return ToolOutput(f"'{a['path']}' does not exist", True)
    try:
        m = load_manifest_file(p, project_root=ws.root)
    except ManifestError as exc:
        return ToolOutput("Manifest is INVALID:\n- " + "\n- ".join(exc.errors), True, data={"errors": exc.errors})
    return ToolOutput(json.dumps(m.to_dict(), ensure_ascii=False, indent=2), data={"manifest": m.to_dict()})


# ------------------------------------------------------------------ plan / project

async def t_update_plan(ws: Workspace, a: dict) -> ToolOutput:
    steps = a["steps"]
    lines = [f"[{'x' if s.get('status') == 'done' else ('>' if s.get('status') == 'in_progress' else ' ')}] "
             f"{s['title']}" for s in steps]
    return ToolOutput("Plan updated:\n" + "\n".join(lines), data={"steps": steps})


async def t_project_info(ws: Workspace, a: dict) -> ToolOutput:
    perms, entry = None, None
    try:
        perms, entry = ws.manifest_permissions()
    except ValueError as exc:
        entry = f"(invalid app.yml: {exc})"
    info = {"project_id": ws.project_id, "files": ws.file_count(), "entry": entry,
            "declared_permissions": sorted(perms) if perms is not None else None,
            "ai_permissions": sorted(ws.ai_permissions),
            "previews": ws.previews.list(ws.owner) if ws.previews else []}
    return ToolOutput(json.dumps(info, ensure_ascii=False, indent=2), data=info)


# ------------------------------------------------------------------ run

async def t_run_mysya(ws: Workspace, a: dict) -> ToolOutput:
    try:
        res = await ws.run_script(a["path"], test_mode=False)
    except ValueError as exc:
        return ToolOutput(str(exc), True)
    text = "\n".join(res["output"]) or "(no output)"
    if res["ok"]:
        return ToolOutput(f"Script finished OK.\nOutput:\n{text}", data=res)
    err = res["error"]
    return ToolOutput(f"Script FAILED: {err['type']} at line {err.get('line')}: {err['message']}\nOutput:\n{text}",
                      True, data=res)


async def t_run_tests(ws: Workspace, a: dict) -> ToolOutput:
    files = [a["path"]] if a.get("path") else ws.test_files()
    if not files:
        return ToolOutput("No tests found. Add `test \"name\": ... end` blocks to a *_test.mysya file or list "
                          "files under `tests:` in app.yml.", True)
    report, all_ok, results = [], True, []
    for f in files:
        try:
            res = await ws.run_script(f, test_mode=True)
        except ValueError as exc:
            report.append(f"{f}: {exc}")
            all_ok = False
            continue
        results.append(res)
        if "error" in res:
            all_ok = False
            report.append(f"{f}: ERROR {res['error']['type']}: {res['error']['message']} (line {res['error'].get('line')})")
        for t in res.get("tests", []):
            all_ok &= t["passed"]
            report.append(f"{'PASS' if t['passed'] else 'FAIL'} {f} :: {t['name']}"
                          + ("" if t["passed"] else f" - {t['error']['message']} (line {t['error'].get('line')})"))
    summary = f"{sum(1 for r in report if r.startswith('PASS'))} passed, " \
              f"{sum(1 for r in report if not r.startswith('PASS'))} failed/errored"
    return ToolOutput(summary + "\n" + "\n".join(report), not all_ok, data={"results": results, "ok": all_ok})


async def t_start_preview(ws: Workspace, a: dict) -> ToolOutput:
    if ws.previews is None:
        return ToolOutput("preview servers are not available in this deployment", True)
    folder = ws.path(a.get("folder", "."))
    if not (folder / "index.html").is_file():
        return ToolOutput(f"'{ws.rel(folder)}' has no index.html", True)
    try:
        preview = ws.previews.start(ws.owner, ws.project_id, folder)
    except ValueError as exc:
        return ToolOutput(str(exc), True)
    user_url = ws.public_preview_url(ws.project_id) if ws.public_preview_url else None
    return ToolOutput(
        f"Local web server is running.\nOpen it in the Service Engine browser with: browser load {preview.url}\n"
        f"The user can view it at: {user_url or preview.url}",
        data={"internal_url": preview.url, "user_url": user_url, "port": preview.port})


async def t_stop_preview(ws: Workspace, a: dict) -> ToolOutput:
    if ws.previews is None or not ws.previews.stop(ws.owner, ws.project_id):
        return ToolOutput("no preview server is running for this project")
    return ToolOutput("Preview server stopped.")


# ------------------------------------------------------------------ browser

BROWSER_VERBS = ["start", "stop", "status", "load", "back", "forward", "reload", "tab", "search", "exists", "text",
                 "url", "title", "errors", "wait", "click", "hover", "type", "fill", "press", "scroll",
                 "screenshot", "download"]
_LOW_VERBS = {"status", "search", "exists", "text", "url", "title", "errors", "wait", "screenshot", "start"}


def _browser_risk(a: dict) -> Risk:
    verb = a.get("command")
    if verb == "download":
        return Risk.HIGH
    return Risk.LOW if verb in _LOW_VERBS else Risk.MEDIUM


def _browser_perm(a: dict) -> str | None:
    from service_engine import VERB_PERMISSIONS
    return VERB_PERMISSIONS.get(a.get("command", ""))


async def t_browser(ws: Workspace, a: dict) -> ToolOutput:
    from service_engine import ServiceError

    if ws.engine is None:
        return ToolOutput("the Service Engine is not available", True)
    verb, args = a["command"], a.get("args", [])
    cmd_line = "service " + verb + "".join(f' "{x}"' if isinstance(x, str) else f" {x}" for x in args)
    try:
        result = await ws.engine.execute(ws.owner, verb, args, source="ai", permissions=set(ws.ai_permissions))
    except ServiceError as exc:
        return ToolOutput(f"`{cmd_line}` failed ({exc.kind}): {exc}", True, data={"command": cmd_line})
    images = []
    data: dict[str, Any] = {"command": cmd_line}
    if verb == "screenshot" and isinstance(result, dict) and result.get("path"):
        raw = Path(result["path"]).read_bytes()
        images.append(("image/png", base64.b64encode(raw).decode()))
        data["screenshot"] = result.get("filename")
        result = {k: v for k, v in result.items() if k != "path"}
    if verb in ("text", "search") or (verb == "errors"):
        body = wrap_untrusted(f"browser {verb}", json.dumps(result, ensure_ascii=False)[:20000]
                              if not isinstance(result, str) else result[:20000])
    else:
        body = json.dumps(result, ensure_ascii=False)[:20000]
    data["result"] = result if not isinstance(result, str) else result[:2000]
    return ToolOutput(f"`{cmd_line}` →\n{body}", data=data, images=images)


TOOLS: list[Tool] = [
    Tool("list_files", "List files and folders of the current MURCLOUD project.",
         obj({"path": {**PATH, "description": "Folder (default '.')"}, "recursive": {"type": "boolean"}}),
         "files", Risk.LOW, "files.read", t_list_files),
    Tool("read_file", "Read a text file of the project (shown with line numbers).",
         obj({"path": PATH}, ["path"]), "files", Risk.LOW, "files.read", t_read_file),
    Tool("write_file", "Create or overwrite a file with the full new content. Use for .mysya, .yml, .driver, "
         "HTML, CSS, JavaScript, Python and other text files.",
         obj({"path": PATH, "content": {"type": "string", "maxLength": MAX_WRITE_BYTES}}, ["path", "content"]),
         "files", Risk.MEDIUM, "files.write", t_write_file),
    Tool("make_dir", "Create a folder (and parents).", obj({"path": PATH}, ["path"]), "files", Risk.MEDIUM,
         "files.write", t_make_dir),
    Tool("delete_path", "Delete a file or a small folder. Always needs the user's confirmation.",
         obj({"path": PATH}, ["path"]), "files", Risk.HIGH, "files.write", t_delete_path),
    Tool("search_files", "Search text in project files (case-insensitive).",
         obj({"query": {"type": "string", "minLength": 1, "maxLength": 200},
              "glob": {"type": "string", "maxLength": 50, "description": "file name pattern, e.g. *.mysya"}},
             ["query"]), "files", Risk.LOW, "files.read", t_search_files),
    Tool("validate_manifest", "Validate a .yml app config or .driver manifest.",
         obj({"path": PATH}, ["path"]), "files", Risk.LOW, "files.read", t_validate_manifest),
    Tool("update_plan", "Show the user your step-by-step plan and progress. Call it first for multi-step tasks "
         "and again when a step changes status.",
         obj({"steps": {"type": "array", "maxItems": 20, "items": obj(
             {"title": {"type": "string", "minLength": 1, "maxLength": 200},
              "status": {"type": "string", "enum": ["pending", "in_progress", "done", "failed"]}},
             ["title", "status"])}}, ["steps"]), "plan", Risk.LOW, None, t_update_plan),
    Tool("project_info", "Show project id, entry file, declared permissions and running previews.",
         obj({}), "project", Risk.LOW, None, t_project_info),
    Tool("run_mysya", "Run a .mysya script for real in the MUSYA Runtime (with the Service Engine) and return "
         "its output or error.", obj({"path": PATH}, ["path"]), "run", Risk.MEDIUM, "scripts.run", t_run_mysya),
    Tool("run_tests", "Run MUSYA tests (`test \"name\": ... end` blocks) of a file or of the whole project.",
         obj({"path": PATH}), "run", Risk.MEDIUM, "scripts.run", t_run_tests),
    Tool("start_preview", "Start a local web server for a folder with index.html (default: project root). "
         "Returns the URL to open with the browser tool.",
         obj({"folder": PATH}), "run", Risk.MEDIUM, "preview.run", t_start_preview),
    Tool("stop_preview", "Stop the project's preview web server.", obj({}), "run", Risk.LOW, "preview.run",
         t_stop_preview),
    Tool("browser", "Run a real Service Engine command in the user's MURCLOUD browser (Playwright/Chromium). "
         "Examples: {command:'load', args:['https://example.com']}, {command:'click', args:['Download']}, "
         "{command:'type', args:['hello']}, {command:'press', args:['Enter']}, {command:'scroll', args:['down', 500]}, "
         "{command:'screenshot'} (returns the image), {command:'errors'} (console errors), "
         "{command:'wait', args:['text', 5000]}. 'download' needs user confirmation.",
         obj({"command": {"type": "string", "enum": BROWSER_VERBS},
              "args": {"type": "array", "maxItems": 3,
                       "items": {"type": ["string", "number", "boolean"]}}}, ["command"]),
         "browser", _browser_risk, _browser_perm, t_browser),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}

__all__ = ["Tool", "ToolOutput", "TOOLS", "TOOLS_BY_NAME", "Workspace", "SchemaError"]
