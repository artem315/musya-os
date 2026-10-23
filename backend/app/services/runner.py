"""Running .mysya scripts from Service Studio, and the MUR Terminal REPL."""

from __future__ import annotations

import asyncio
import json
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from musya_runtime import (ALL_PERMISSIONS, SENSITIVE_PERMISSIONS, Interpreter, Limits, ManifestError, MusyaError,
                           load_manifest_file)
from musya_runtime.interpreter.values import to_display
from service_engine import EngineHost, ServiceEngine, describe_commands

MAX_RUNS_PER_USER = 2
RUN_TIMEOUT = 600.0
# Scripts without app.yml get everything except install-time-sensitive permissions.
DEFAULT_SCRIPT_PERMISSIONS = (set(ALL_PERMISSIONS) - SENSITIVE_PERMISSIONS) | {"files.write"}


@dataclass
class Run:
    id: str
    user_id: str
    project_id: str
    file: str
    test_mode: bool
    permissions: list[str]
    status: str = "running"
    output: list[str] = field(default_factory=list)
    error: dict | None = None
    tests: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "file": self.file, "test_mode": self.test_mode,
                "permissions": self.permissions, "status": self.status, "output": self.output[-500:],
                "error": self.error, "tests": self.tests, "started_at": self.started_at,
                "finished_at": self.finished_at}


def manifest_permissions(root: Path) -> tuple[set[str], str]:
    for name in ("app.yml", "app.yaml"):
        p = root / name
        if p.is_file():
            try:
                m = load_manifest_file(p)
            except ManifestError as exc:
                raise ValueError(f"{name} is invalid: {'; '.join(exc.errors)}") from None
            return set(m.permissions), name
    return set(DEFAULT_SCRIPT_PERMISSIONS), "default"


class Runner:
    def __init__(self, engine: ServiceEngine, ai_ask=None):
        self.engine = engine
        self.ai_ask = ai_ask  # async (user_id, prompt, options) -> str
        self.runs: dict[str, Run] = {}
        self.repls: dict[tuple[str, str], tuple[Interpreter, EngineHost]] = {}

    def publish(self, user_id: str, type_: str, **data) -> None:
        self.engine.bus.publish(user_id, type_, **data)

    def active(self, user_id: str) -> list[Run]:
        return [r for r in self.runs.values() if r.user_id == user_id and r.status == "running"]

    def start(self, user_id: str, project_id: str, root: Path, file: str, *, test_mode: bool = False,
              permissions: set[str] | None = None, source: str = "script") -> Run:
        if len(self.active(user_id)) >= MAX_RUNS_PER_USER:
            raise ValueError(f"at most {MAX_RUNS_PER_USER} scripts can run at once - stop one first")
        target = (root / file).resolve()
        if root.resolve() not in target.parents or not target.is_file() or target.suffix != ".mysya":
            raise ValueError(f"'{file}' is not a .mysya file of this project")
        perms_from = "package"
        if permissions is None:
            permissions, perms_from = manifest_permissions(root)
        run = Run(uuid.uuid4().hex[:12], user_id, project_id, file, test_mode, sorted(permissions))
        self.runs[run.id] = run
        if len(self.runs) > 200:
            for rid in [r.id for r in self.runs.values() if r.status != "running"][:50]:
                self.runs.pop(rid, None)
        self.publish(user_id, "run.started", run=run.to_dict(), permissions_from=perms_from)
        run.task = asyncio.ensure_future(self._execute(run, root, target, permissions, source))
        return run

    async def _execute(self, run: Run, root: Path, target: Path, permissions: set[str], source: str) -> None:
        def sink(text: str) -> None:
            run.output.append(text)
            del run.output[:-2000]
            self.publish(run.user_id, "run.output", run=run.id, text=text)

        async def ask(prompt: str, options: dict) -> str:
            if self.ai_ask is None:
                raise MusyaError("MUR AI is not configured")
            return await self.ai_ask(run.user_id, prompt, options)

        host = EngineHost(root, self.engine, permissions, owner=run.user_id, source=source, output_sink=sink,
                          ai_ask=ask)
        interp = Interpreter(host, Limits())
        host.attach(interp)
        try:
            result = await asyncio.wait_for(
                interp.run_source(target.read_text(encoding="utf-8"), target.relative_to(root).as_posix(),
                                  test_mode=run.test_mode), RUN_TIMEOUT)
            run.status = "done"
            run.tests = [{"name": t.name, "passed": t.passed, "error": t.error} for t in result.tests]
            if run.test_mode and not all(t.passed for t in result.tests):
                run.status = "failed"
        except asyncio.CancelledError:
            run.status = "cancelled"
            self.engine.cancel(run.user_id)
        except asyncio.TimeoutError:
            run.status = "failed"
            run.error = {"type": "Timeout", "message": f"script exceeded {RUN_TIMEOUT:g}s"}
        except MusyaError as exc:
            run.status = "failed"
            run.error = exc.to_dict()
        except Exception as exc:  # never let a crash disappear silently
            run.status = "failed"
            run.error = {"type": type(exc).__name__, "message": str(exc)[:500]}
        finally:
            host.detach()
            run.finished_at = time.time()
            self.publish(run.user_id, "run.finished", run=run.to_dict())

    def stop(self, user_id: str, run_id: str) -> bool:
        run = self.runs.get(run_id)
        if run is None or run.user_id != user_id or run.status != "running" or run.task is None:
            return False
        run.task.cancel()
        return True

    def get(self, user_id: str, run_id: str) -> Run | None:
        run = self.runs.get(run_id)
        return run if run and run.user_id == user_id else None

    # ---------------------------------------------------------------- terminal
    async def terminal(self, user_id: str, project_id: str, root: Path, line: str) -> dict:
        """Execute one terminal line. Returns {"output": [...], "result": ..., "error": ...}."""
        line = line.strip()
        if not line:
            return {"output": []}
        out: list[str] = []
        word = line.split(None, 1)[0]
        if word in ("help", "?"):
            cmds = describe_commands()
            out.append("MUR Terminal — MUSYA REPL + Service Engine")
            out.append("  ls [dir] | cat <file> | run <file.mysya> | test [file] | jobs | cancel [job] | clear")
            out.append("  any MUSYA code, e.g.  let x = 2 * 21   or   print \"мур\"")
            for c in cmds:
                out.append(f"  {c['usage']:<48} {c['summary']}")
            return {"output": out}
        if word == "ls":
            rel = line[2:].strip() or "."
            target = (root / rel).resolve()
            if root.resolve() != target and root.resolve() not in target.parents:
                return {"error": {"type": "PermissionError", "message": "outside the project"}}
            if not target.is_dir():
                return {"error": {"type": "FileError", "message": f"'{rel}' is not a folder"}}
            return {"output": [p.name + ("/" if p.is_dir() else "") for p in sorted(target.iterdir())
                               if not p.name.startswith(".")]}
        if word == "cat":
            rel = line[3:].strip()
            target = (root / rel).resolve()
            if root.resolve() not in target.parents or not target.is_file():
                return {"error": {"type": "FileError", "message": f"'{rel}' not found"}}
            return {"output": target.read_text(encoding="utf-8", errors="replace")[:20000].split("\n")}
        if word in ("run", "test"):
            args = shlex.split(line)[1:]
            if word == "run" and not args:
                return {"error": {"type": "UsageError", "message": "usage: run <file.mysya>"}}
            file = args[0] if args else None
            if file is None:
                files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*_test.mysya"))
                if not files:
                    return {"error": {"type": "UsageError", "message": "no *_test.mysya files - usage: test <file>"}}
                file = files[0]
            try:
                run = self.start(user_id, project_id, root, file, test_mode=word == "test", source="terminal")
            except ValueError as exc:
                return {"error": {"type": "RunError", "message": str(exc)}}
            return {"output": [f"▶ started {file} (run {run.id}) - permissions: {', '.join(run.permissions)}"],
                    "run": run.id}
        if word == "jobs":
            return {"output": [f"{j['id']} {j['status']:<9} {j['source']:<9} {j['command']}"
                               for j in self.engine.jobs(user_id, 20)] or ["(no jobs)"]}
        if word == "cancel":
            parts = line.split()
            ids = self.engine.cancel(user_id, parts[1] if len(parts) > 1 else None)
            return {"output": [f"cancelled {', '.join(ids)}" if ids else "nothing to cancel"]}

        # Anything else is MUSYA code evaluated in a persistent REPL.
        key = (user_id, project_id)
        if key not in self.repls:
            def sink(text: str) -> None:
                self._repl_out.append(text)

            async def ask(prompt: str, options: dict) -> str:
                if self.ai_ask is None:
                    raise MusyaError("MUR AI is not configured")
                return await self.ai_ask(user_id, prompt, options)

            host = EngineHost(root, self.engine, None, owner=user_id, source="terminal", output_sink=sink, ai_ask=ask)
            interp = Interpreter(host, Limits(max_steps=500_000))
            host.attach(interp)
            self.repls[key] = (interp, host)
        interp, host = self.repls[key]
        self._repl_out = out
        host.output_sink = out.append
        try:
            value = await asyncio.wait_for(interp.run_repl(line), 180)
        except MusyaError as exc:
            return {"output": out, "error": exc.to_dict()}
        except asyncio.TimeoutError:
            return {"output": out, "error": {"type": "Timeout", "message": "line exceeded 180s"}}
        result = None
        if value is not None:
            result = value if isinstance(value, (dict, list, str, int, float, bool)) else to_display(value)
            out.append(json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, (dict, list))
                       else to_display(result))
        return {"output": out, "result": _jsonable(result)}

    def close_repls(self, user_id: str) -> None:
        for key in [k for k in self.repls if k[0] == user_id]:
            _, host = self.repls.pop(key)
            host.detach()


def _jsonable(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        return str(v)
