"""Service Studio projects: real folders on the persistent data volume."""

from __future__ import annotations

import io
import re
import shutil
import time
import zipfile
from pathlib import Path

from fastapi import HTTPException
from musya_runtime import ManifestError, MusyaError, load_manifest_file, parse, safe_join
from musya_runtime.errors import MusyaPermissionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Project

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 2000
EDITABLE = {".mysya", ".yml", ".yaml", ".driver", ".html", ".htm", ".css", ".js", ".mjs", ".json", ".md", ".txt",
            ".py", ".svg", ".xml", ".csv", ".ts", ".tsx", ".jsx", ".toml", ".ini", ".sh", ""}

TEMPLATES: dict[str, dict[str, str]] = {
    "blank": {
        "main.mysya": 'app "${name}"\n\nprint "Привет из MURCLOUD! 🐈"\nprint "МУРГОТОВО!"\n',
        "app.yml": "name: ${name}\nversion: 1.0.0\nruntime: musya\nentry: main.mysya\npermissions:\n  - files.read\n",
    },
    "browser": {
        "main.mysya": ('app "${name}"\n\nlet url = "https://example.com"\n\nservice load url\n'
                       'service wait "Example Domain"\nlet shot = service screenshot\nprint "Скриншот:", shot.filename\n\n'
                       'if service.exists("More information"):\n    service click "More information"\nend\n\n'
                       'print "Сейчас открыто:", service.url()\nprint "МУРГОТОВО!"\n'),
        "app.yml": ("name: ${name}\nversion: 1.0.0\nruntime: musya\nentry: main.mysya\npermissions:\n"
                    "  - browser.read\n  - browser.navigate\n  - browser.click\n  - browser.screenshot\n"),
        "browser.driver": ("name: ${name} Driver\nversion: 1.0.0\nruntime: musya\nentry: main.mysya\ntype: driver\n"
                           "permissions:\n  - browser.read\n  - browser.navigate\n  - browser.click\n"
                           "  - browser.screenshot\n"),
    },
    "website": {
        "index.html": ('<!doctype html>\n<html lang="ru">\n<head>\n  <meta charset="utf-8">\n'
                       '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
                       '  <title>${name}</title>\n  <link rel="stylesheet" href="style.css">\n</head>\n<body>\n'
                       '  <main class="card">\n    <h1>${name}</h1>\n    <p>Сделано в MURCLOUD Service Studio.</p>\n'
                       '    <button id="btn">Мур!</button>\n  </main>\n  <script src="script.js"></script>\n</body>\n</html>\n'),
        "style.css": ("body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui;"
                      "background:#0b1020;color:#fff}\n.card{padding:40px;border-radius:20px;"
                      "background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.2)}\n"
                      "button{border:0;border-radius:20px;padding:10px 20px;background:#a855f7;color:#fff}\n"),
        "script.js": "document.getElementById('btn').addEventListener('click', () => alert('Мур!'));\n",
        "main.mysya": ('app "${name}"\n# Open this site in the Service Engine browser.\n'
                       '# Start the preview first (Preview tab or MUR AI start_preview).\n'
                       'print "Откройте вкладку Preview, чтобы увидеть сайт."\n'),
        "site_test.mysya": ('test "index.html exists":\n    assert file.exists("index.html"), "index.html is missing"\nend\n'),
        "app.yml": ("name: ${name}\nversion: 1.0.0\nruntime: musya\nentry: main.mysya\npermissions:\n"
                    "  - files.read\n  - browser.read\n  - browser.navigate\n  - browser.screenshot\n"
                    "tests:\n  - site_test.mysya\npreview:\n  root: .\n"),
    },
}


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", name.strip().lower(), flags=re.UNICODE).strip("-")
    return s[:60] or "project"


def project_dict(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "slug": p.slug, "description": p.description, "template": p.template,
            "created_at": p.created_at, "updated_at": p.updated_at, "state": p.state or {}}


class Projects:
    def __init__(self, settings):
        self.settings = settings

    def root(self, user_id: str, project_id: str) -> Path:
        d = self.settings.user_dir(user_id) / "projects" / project_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def get(self, db: AsyncSession, user_id: str, project_id: str) -> Project:
        p = await db.get(Project, project_id)
        if p is None or p.user_id != user_id:
            raise HTTPException(404, "project not found")
        return p

    async def list(self, db: AsyncSession, user_id: str) -> list[dict]:
        rows = (await db.execute(select(Project).where(Project.user_id == user_id)
                                 .order_by(Project.updated_at.desc()))).scalars().all()
        return [project_dict(r) for r in rows]

    async def create(self, db: AsyncSession, user_id: str, name: str, template: str = "blank",
                     description: str = "") -> Project:
        name = name.strip()
        if not name or len(name) > 80:
            raise HTTPException(400, "project name must be 1-80 characters")
        if template not in TEMPLATES:
            raise HTTPException(400, f"unknown template '{template}'")
        count = len((await db.execute(select(Project.id).where(Project.user_id == user_id))).all())
        if count >= 100:
            raise HTTPException(400, "project limit reached")
        slug = base = slugify(name)
        i = 1
        while (await db.execute(select(Project.id).where(Project.user_id == user_id, Project.slug == slug))).first():
            i += 1
            slug = f"{base}-{i}"
        project = Project(user_id=user_id, name=name, slug=slug, template=template, description=description[:2000])
        db.add(project)
        await db.flush()
        root = self.root(user_id, project.id)
        safe = re.sub(r"[\"\\$`]", "", name)
        for rel, content in TEMPLATES[template].items():
            (root / rel).write_text(content.replace("${name}", safe), encoding="utf-8")
        return project

    async def delete(self, db: AsyncSession, user_id: str, project_id: str) -> None:
        p = await self.get(db, user_id, project_id)
        await db.delete(p)
        shutil.rmtree(self.root(user_id, project_id), ignore_errors=True)

    def path(self, user_id: str, project_id: str, rel: str) -> Path:
        try:
            return safe_join(self.root(user_id, project_id), rel or ".")
        except (MusyaPermissionError, MusyaError) as exc:
            raise HTTPException(400, exc.message) from None

    def tree(self, user_id: str, project_id: str) -> list[dict]:
        root = self.root(user_id, project_id)
        out = []
        for p in sorted(root.rglob("*"), key=lambda x: (x.relative_to(root).parts[:-1], not x.is_dir(), x.name)):
            rel = p.relative_to(root)
            if any(part.startswith(".") for part in rel.parts) or "Downloads" in rel.parts[:1]:
                continue
            out.append({"path": rel.as_posix(), "name": p.name, "is_dir": p.is_dir(),
                        "size": p.stat().st_size if p.is_file() else 0, "mtime": p.stat().st_mtime})
            if len(out) >= MAX_FILES:
                break
        return out

    def read(self, user_id: str, project_id: str, rel: str) -> dict:
        p = self.path(user_id, project_id, rel)
        if not p.is_file():
            raise HTTPException(404, f"'{rel}' not found")
        if p.suffix.lower() not in EDITABLE:
            return {"path": rel, "binary": True, "size": p.stat().st_size}
        if p.stat().st_size > MAX_FILE_BYTES:
            raise HTTPException(413, "file is too large for the editor")
        return {"path": rel, "binary": False, "content": p.read_text(encoding="utf-8", errors="replace"),
                "size": p.stat().st_size, "mtime": p.stat().st_mtime}

    def write(self, user_id: str, project_id: str, rel: str, content: str) -> dict:
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, "file is larger than 2 MB")
        p = self.path(user_id, project_id, rel)
        if p.is_dir():
            raise HTTPException(400, "path is a folder")
        root = self.root(user_id, project_id)
        if not p.exists() and sum(1 for _ in root.rglob("*")) >= MAX_FILES:
            raise HTTPException(400, "project file limit reached")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"path": rel, "size": len(data), "mtime": p.stat().st_mtime, "diagnostics": self.diagnose(p, content, root)}

    @staticmethod
    def diagnose(p: Path, content: str, root: Path) -> list[dict]:
        """Syntax/manifest problems for the editor gutter."""
        if p.suffix == ".mysya":
            try:
                parse(content, p.name)
            except MusyaError as exc:
                return [{"severity": "error", "line": exc.line or 1, "column": exc.column or 1, "message": exc.message}]
        elif p.suffix in (".driver",) or p.name in ("app.yml", "app.yaml"):
            try:
                m = load_manifest_file(p, project_root=root)
                return [{"severity": "warning", "line": 1, "column": 1, "message": w} for w in m.warnings]
            except ManifestError as exc:
                return [{"severity": "error", "line": 1, "column": 1, "message": e} for e in exc.errors]
        return []

    def mkdir(self, user_id: str, project_id: str, rel: str) -> None:
        self.path(user_id, project_id, rel).mkdir(parents=True, exist_ok=True)

    def remove(self, user_id: str, project_id: str, rel: str) -> None:
        p = self.path(user_id, project_id, rel)
        if p == self.root(user_id, project_id):
            raise HTTPException(400, "cannot delete the project root")
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        else:
            raise HTTPException(404, f"'{rel}' not found")

    def rename(self, user_id: str, project_id: str, rel: str, new_rel: str) -> None:
        src, dst = self.path(user_id, project_id, rel), self.path(user_id, project_id, new_rel)
        if not src.exists():
            raise HTTPException(404, f"'{rel}' not found")
        if dst.exists():
            raise HTTPException(409, f"'{new_rel}' already exists")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)

    def build(self, user_id: str, project_id: str) -> dict:
        """Static checks: every .mysya parses, every manifest validates."""
        root = self.root(user_id, project_id)
        report = {"ok": True, "files": 0, "problems": [], "manifest": None, "started_at": time.time()}
        for p in sorted(root.rglob("*")):
            if not p.is_file() or any(x.startswith(".") for x in p.relative_to(root).parts):
                continue
            report["files"] += 1
            if p.suffix in (".mysya", ".driver") or p.name in ("app.yml", "app.yaml"):
                for d in self.diagnose(p, p.read_text(encoding="utf-8", errors="replace"), root):
                    d["file"] = p.relative_to(root).as_posix()
                    report["problems"].append(d)
                    if d["severity"] == "error":
                        report["ok"] = False
        app = root / "app.yml"
        if app.is_file():
            try:
                report["manifest"] = load_manifest_file(app, project_root=root).to_dict()
            except ManifestError:
                pass
        return report

    def zip_bytes(self, user_id: str, project_id: str) -> bytes:
        root = self.root(user_id, project_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(root.rglob("*")):
                rel = p.relative_to(root)
                if p.is_file() and not any(x.startswith(".") for x in rel.parts):
                    zf.write(p, rel.as_posix())
        return buf.getvalue()
