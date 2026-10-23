"""Host interface: everything a MUSYA script can reach outside its own memory.

The interpreter never touches the network, the filesystem or the browser
directly. It calls a :class:`RuntimeHost`, and the host decides (and checks
permissions) for each operation. MURCLOUD's backend provides a host wired to
the Service Engine, MUR Drive and MUR AI; :class:`SandboxHost` is a local
host for the CLI and tests.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any

from ..errors import MusyaPermissionError, MusyaRuntimeError

# Permissions understood by the runtime itself. The Service Engine adds the
# browser.* family (see service_engine.permissions).
RUNTIME_PERMISSIONS = {
    "files.read": "Read files inside the project",
    "files.write": "Create, modify and delete files inside the project",
    "ai.use": "Send prompts to MUR AI",
    "events.emit": "Emit events to the MURCLOUD event bus",
}


class RuntimeHost:
    """Base host. Every capability is denied unless a subclass provides it."""

    def __init__(self, permissions: set[str] | None = None):
        # ``None`` means "all permissions" (interactive owner session).
        self.permissions = permissions

    # -- permissions ---------------------------------------------------------
    def has_permission(self, perm: str) -> bool:
        if self.permissions is None:
            return True
        if perm in self.permissions:
            return True
        family = perm.split(".", 1)[0]
        return f"{family}.*" in self.permissions or "*" in self.permissions

    def require(self, perm: str, line: int | None = None) -> None:
        if not self.has_permission(perm):
            raise MusyaPermissionError(f"permission '{perm}' is not granted to this script", line)

    # -- capabilities --------------------------------------------------------
    async def output(self, text: str) -> None:
        print(text)

    async def service(self, verb: str, args: list, line: int | None = None) -> Any:
        raise MusyaRuntimeError(
            "Service Engine is not connected. Run the script inside MURCLOUD Service Studio "
            "or with `musya run --engine`.", line, kind="ServiceError")

    async def emit(self, name: str, data: Any) -> None:
        return None

    async def ai_ask(self, prompt: str, options: dict) -> str:
        raise MusyaRuntimeError("MUR AI is not available in this host", kind="AIError")

    async def load_module(self, path: str, importer: str) -> tuple[str, str]:
        raise MusyaRuntimeError("imports are not available in this host", kind="ImportError")

    # Files ------------------------------------------------------------------
    async def file_read(self, path: str) -> str:
        raise MusyaRuntimeError("file access is not available in this host", kind="FileError")

    async def file_write(self, path: str, content: str, append: bool = False) -> int:
        raise MusyaRuntimeError("file access is not available in this host", kind="FileError")

    async def file_exists(self, path: str) -> bool:
        raise MusyaRuntimeError("file access is not available in this host", kind="FileError")

    async def file_list(self, path: str) -> list:
        raise MusyaRuntimeError("file access is not available in this host", kind="FileError")

    async def file_delete(self, path: str) -> bool:
        raise MusyaRuntimeError("file access is not available in this host", kind="FileError")

    async def file_mkdir(self, path: str) -> bool:
        raise MusyaRuntimeError("file access is not available in this host", kind="FileError")


MAX_FILE_BYTES = 5 * 1024 * 1024


def safe_join(root: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``root``; refuse absolute paths and ``..`` escapes."""
    if not isinstance(rel, str) or not rel.strip():
        raise MusyaRuntimeError("path must be a non-empty string", kind="FileError")
    if "\x00" in rel:
        raise MusyaRuntimeError("invalid path", kind="FileError")
    posix = PurePosixPath(rel.replace("\\", "/"))
    if posix.is_absolute():
        posix = PurePosixPath(*posix.parts[1:])
    root = root.resolve()
    target = (root / Path(*posix.parts)).resolve() if posix.parts else root
    if target != root and root not in target.parents:
        raise MusyaPermissionError(f"path '{rel}' is outside the project")
    return target


class SandboxHost(RuntimeHost):
    """Host confined to a single directory. Used by the CLI and the backend runner."""

    def __init__(self, root: str | os.PathLike, permissions: set[str] | None = None,
                 output_sink=None, max_file_bytes: int = MAX_FILE_BYTES):
        super().__init__(permissions)
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.output_sink = output_sink
        self.max_file_bytes = max_file_bytes
        self.outputs: list[str] = []
        self.events: list[tuple[str, Any]] = []

    async def output(self, text: str) -> None:
        self.outputs.append(text)
        if self.output_sink is not None:
            res = self.output_sink(text)
            if hasattr(res, "__await__"):
                await res
        else:
            print(text)

    async def emit(self, name: str, data: Any) -> None:
        self.events.append((name, data))

    async def load_module(self, path: str, importer: str) -> tuple[str, str]:
        base = PurePosixPath(importer).parent if importer and not importer.startswith("<") else PurePosixPath(".")
        rel = str(base / path)
        if not rel.endswith(".mysya"):
            rel += ".mysya"
        target = safe_join(self.root, rel)
        if not target.is_file():
            raise MusyaRuntimeError(f"module '{path}' not found", kind="ImportError")
        return str(target.relative_to(self.root)), target.read_text(encoding="utf-8")

    async def file_read(self, path: str) -> str:
        self.require("files.read")
        target = safe_join(self.root, path)
        if not target.is_file():
            raise MusyaRuntimeError(f"file '{path}' not found", kind="FileError")
        if target.stat().st_size > self.max_file_bytes:
            raise MusyaRuntimeError(f"file '{path}' is larger than the read limit", kind="FileError")
        return target.read_text(encoding="utf-8", errors="replace")

    async def file_write(self, path: str, content: str, append: bool = False) -> int:
        self.require("files.write")
        target = safe_join(self.root, path)
        data = content.encode("utf-8")
        existing = target.stat().st_size if (append and target.exists()) else 0
        if existing + len(data) > self.max_file_bytes:
            raise MusyaRuntimeError("file would exceed the size limit", kind="FileError")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "ab" if append else "wb") as fh:
            fh.write(data)
        return len(data)

    async def file_exists(self, path: str) -> bool:
        self.require("files.read")
        return safe_join(self.root, path).exists()

    async def file_list(self, path: str) -> list:
        self.require("files.read")
        target = safe_join(self.root, path or ".")
        if not target.is_dir():
            raise MusyaRuntimeError(f"'{path}' is not a folder", kind="FileError")
        return sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())

    async def file_delete(self, path: str) -> bool:
        self.require("files.write")
        target = safe_join(self.root, path)
        if target == self.root:
            raise MusyaPermissionError("refusing to delete the project root")
        if target.is_dir():
            if any(target.iterdir()):
                raise MusyaRuntimeError("folder is not empty", kind="FileError")
            target.rmdir()
            return True
        if target.exists():
            target.unlink()
            return True
        return False

    async def file_mkdir(self, path: str) -> bool:
        self.require("files.write")
        safe_join(self.root, path).mkdir(parents=True, exist_ok=True)
        return True
