"""Local preview web servers for projects ("run the site I just built").

Each preview is a real static HTTP server bound to ``127.0.0.1`` on a random
port, serving one project folder. The port is allow-listed only in the
owning user's :class:`NetworkPolicy`, so only that user's Service Engine
browser can open it; it is never exposed outside the host. End users see
the same files through the authenticated backend route instead.
"""

from __future__ import annotations

import functools
import http.server
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_PREVIEWS_PER_OWNER = 3


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - signature from stdlib
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404, "directory listing disabled")
        return None


@dataclass
class Preview:
    owner: str
    key: str
    root: Path
    server: http.server.ThreadingHTTPServer
    thread: threading.Thread
    started_at: float = field(default_factory=time.time)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def to_dict(self) -> dict:
        return {"key": self.key, "url": self.url, "port": self.port, "root": str(self.root),
                "started_at": self.started_at}


class PreviewManager:
    def __init__(self, engine=None):
        self.engine = engine
        self.previews: dict[tuple[str, str], Preview] = {}

    def start(self, owner: str, key: str, root: str | Path) -> Preview:
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError(f"preview folder '{root.name}' does not exist")
        existing = self.previews.get((owner, key))
        if existing is not None:
            if existing.root == root:
                return existing
            self.stop(owner, key)
        if sum(1 for (o, _) in self.previews if o == owner) >= MAX_PREVIEWS_PER_OWNER:
            raise ValueError(f"at most {MAX_PREVIEWS_PER_OWNER} previews can run at once - stop one first")
        handler = functools.partial(_QuietHandler, directory=str(root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name=f"preview-{owner}-{key}", daemon=True)
        thread.start()
        preview = Preview(owner, key, root, server, thread)
        self.previews[(owner, key)] = preview
        if self.engine is not None:
            self.engine.policy_for(owner).allow(f"127.0.0.1:{preview.port}")
            self.engine.bus.publish(owner, "preview.started", **preview.to_dict())
        return preview

    def stop(self, owner: str, key: str) -> bool:
        preview = self.previews.pop((owner, key), None)
        if preview is None:
            return False
        preview.server.shutdown()
        preview.server.server_close()
        if self.engine is not None:
            self.engine.policy_for(owner).revoke(f"127.0.0.1:{preview.port}")
            self.engine.bus.publish(owner, "preview.stopped", key=key)
        return True

    def get(self, owner: str, key: str) -> Preview | None:
        return self.previews.get((owner, key))

    def list(self, owner: str) -> list[dict]:
        return [p.to_dict() for (o, _), p in self.previews.items() if o == owner]

    def stop_all(self) -> None:
        for owner, key in list(self.previews):
            self.stop(owner, key)
