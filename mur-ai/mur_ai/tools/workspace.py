"""The sandbox MUR AI works in: one project folder + the owner's Service Engine."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from musya_runtime import (Interpreter, Limits, ManifestError, MusyaError, MusyaLimitError, SandboxHost,
                           load_manifest_file, safe_join)

MAX_READ_BYTES = 512 * 1024
MAX_WRITE_BYTES = 2 * 1024 * 1024
MAX_PROJECT_FILES = 2000
SCRIPT_TIMEOUT = 120.0


@dataclass
class Workspace:
    owner: str
    project_id: str
    root: Path
    engine: Any = None  # service_engine.ServiceEngine
    previews: Any = None  # service_engine.preview.PreviewManager
    public_preview_url: Callable[[str], str] | None = None  # project_id -> URL the user can open
    on_file_changed: Callable[[str, str], Awaitable[None] | None] | None = None  # (path, action)
    ai_permissions: set[str] = field(default_factory=set)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, rel: str) -> Path:
        return safe_join(self.root, rel or ".")

    def rel(self, p: Path) -> str:
        r = p.resolve().relative_to(self.root).as_posix()
        return r or "."

    async def changed(self, rel: str, action: str) -> None:
        if self.on_file_changed is not None:
            res = self.on_file_changed(rel, action)
            if asyncio.iscoroutine(res):
                await res

    def file_count(self) -> int:
        return sum(1 for _ in self.root.rglob("*"))

    # -------------------------------------------------------------- scripts
    def manifest_permissions(self) -> tuple[set[str] | None, str | None]:
        """Permissions declared in the project's app.yml (if any)."""
        for name in ("app.yml", "app.yaml"):
            p = self.root / name
            if p.is_file():
                try:
                    m = load_manifest_file(p)
                except ManifestError as exc:
                    raise ValueError(f"{name} is invalid: {exc}") from None
                return set(m.permissions), m.entry
        return None, None

    async def run_script(self, rel: str, *, test_mode: bool = False, timeout: float = SCRIPT_TIMEOUT) -> dict:
        """Run a .mysya file for real and capture its output, errors and test results."""
        from service_engine import EngineHost

        target = self.path(rel)
        if not target.is_file() or target.suffix != ".mysya":
            raise ValueError(f"'{rel}' is not a .mysya file in the project")
        declared, _ = self.manifest_permissions()
        # A script never gets more than the AI session itself was granted.
        perms = (declared if declared is not None else set(self.ai_permissions)) & set(self.ai_permissions)
        output = io.StringIO()
        lines: list[str] = []

        def sink(text: str) -> None:
            lines.append(text)
            output.write(text + "\n")

        if self.engine is not None:
            host = EngineHost(self.root, self.engine, perms, owner=self.owner, source="ai-script", output_sink=sink)
        else:
            host = SandboxHost(self.root, perms, output_sink=sink)
        interp = Interpreter(host, Limits(max_steps=1_000_000))
        if isinstance(host, EngineHost):
            host.attach(interp)
        result: dict[str, Any] = {"file": rel, "permissions": sorted(perms)}
        try:
            run = await asyncio.wait_for(interp.run_source(target.read_text(encoding="utf-8"), rel,
                                                           test_mode=test_mode), timeout)
            result.update(ok=True, app=run.app_name, steps=run.steps)
            if test_mode:
                result["tests"] = [{"name": t.name, "passed": t.passed, "error": t.error} for t in run.tests]
                result["ok"] = all(t.passed for t in run.tests)
        except MusyaLimitError as exc:
            result.update(ok=False, error=exc.to_dict())
        except MusyaError as exc:
            result.update(ok=False, error=exc.to_dict())
        except asyncio.TimeoutError:
            result.update(ok=False, error={"type": "Timeout", "message": f"script exceeded {timeout:g}s"})
        finally:
            if isinstance(host, EngineHost):
                host.detach()
        result["output"] = lines[-200:]
        return result

    def test_files(self) -> list[str]:
        files: list[str] = []
        p = self.root / "app.yml"
        if p.is_file():
            try:
                files = list(load_manifest_file(p).tests)
            except ManifestError:
                files = []
        if not files:
            files = sorted(self.rel(f) for f in self.root.rglob("*.mysya")
                           if f.name.endswith("_test.mysya") or "tests" in f.relative_to(self.root).parts)
        return files
