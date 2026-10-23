"""MUR Package Manager: catalog of drivers/apps, permission-checked install, run."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import HTTPException
from musya_runtime import ManifestError, load_manifest_file
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AuditLog, InstalledPackage


def _find_manifest(folder: Path) -> Path | None:
    drivers = sorted(folder.glob("*.driver"))
    if drivers:
        return drivers[0]
    for name in ("app.yml", "app.yaml"):
        if (folder / name).is_file():
            return folder / name
    return None


class Packages:
    def __init__(self, core):
        self.core = core

    @property
    def catalog_dir(self) -> Path:
        return self.core.settings.packages_dir

    def catalog(self) -> list[dict]:
        out = []
        if not self.catalog_dir.is_dir():
            return out
        for folder in sorted(p for p in self.catalog_dir.iterdir() if p.is_dir()):
            mf = _find_manifest(folder)
            if mf is None:
                continue
            try:
                m = load_manifest_file(mf, project_root=folder)
            except ManifestError as exc:
                out.append({"id": folder.name, "valid": False, "errors": exc.errors})
                continue
            out.append({**m.to_dict(), "id": m.id, "valid": True, "folder": folder.name,
                        "sensitive_permissions": m.sensitive_permissions,
                        "readme": (folder / "README.md").read_text(encoding="utf-8")[:4000]
                        if (folder / "README.md").is_file() else ""})
        return out

    def _catalog_entry(self, package_id: str) -> tuple[Path, dict]:
        for entry in self.catalog():
            if entry.get("valid") and (entry["id"] == package_id or entry.get("folder") == package_id):
                return self.catalog_dir / entry["folder"], entry
        raise HTTPException(404, f"package '{package_id}' is not in the catalog")

    def install_dir(self, user_id: str, package_id: str) -> Path:
        return self.core.settings.user_dir(user_id) / "packages" / package_id

    async def installed(self, db: AsyncSession, user_id: str) -> list[dict]:
        rows = (await db.execute(select(InstalledPackage).where(InstalledPackage.user_id == user_id)
                                 .order_by(InstalledPackage.installed_at))).scalars().all()
        return [{"id": r.package_id, "name": r.name, "version": r.version, "kind": r.kind,
                 "permissions": r.permissions, "source": r.source, "installed_at": r.installed_at,
                 "entry": r.manifest.get("entry")} for r in rows]

    async def install(self, db: AsyncSession, user_id: str, package_id: str, *, approved_permissions: list[str] | None,
                      source_dir: Path | None = None, source: str = "catalog") -> dict:
        """Install a package. ``approved_permissions`` must cover every sensitive permission it requests."""
        if source_dir is None:
            folder, entry = self._catalog_entry(package_id)
        else:
            folder = source_dir
            mf = _find_manifest(folder)
            if mf is None:
                raise HTTPException(400, "the project has no .driver or app.yml manifest")
            try:
                m = load_manifest_file(mf, project_root=folder)
            except ManifestError as exc:
                raise HTTPException(400, "invalid manifest: " + "; ".join(exc.errors)) from None
            entry = {**m.to_dict(), "sensitive_permissions": m.sensitive_permissions}
            package_id = m.id
        missing = [p for p in entry["sensitive_permissions"] if p not in (approved_permissions or [])]
        if missing:
            raise HTTPException(403, {"message": "confirmation required for sensitive permissions",
                                      "permissions": entry["permissions"], "needs_approval": missing})
        target = self.install_dir(user_id, package_id)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(folder, target, ignore=shutil.ignore_patterns(".*", "__pycache__", "node_modules"))
        row = (await db.execute(select(InstalledPackage).where(InstalledPackage.user_id == user_id,
                                                               InstalledPackage.package_id == package_id))
               ).scalar_one_or_none()
        if row is None:
            row = InstalledPackage(user_id=user_id, package_id=package_id, name="", version="", kind="")
            db.add(row)
        row.name, row.version, row.kind = entry["name"], entry["version"], entry["kind"]
        row.permissions, row.source = entry["permissions"], source
        row.manifest = {k: entry[k] for k in ("name", "version", "kind", "entry", "permissions", "description",
                                              "author", "commands") if k in entry}
        db.add(AuditLog(user_id=user_id, actor="user", event="package_installed", tool=package_id,
                        detail={"version": entry["version"], "permissions": entry["permissions"], "source": source}))
        await db.flush()
        self.core.engine.bus.publish(user_id, "package.installed", package=package_id, version=entry["version"])
        return {"id": package_id, "name": entry["name"], "version": entry["version"], "permissions": entry["permissions"]}

    async def uninstall(self, db: AsyncSession, user_id: str, package_id: str) -> None:
        row = (await db.execute(select(InstalledPackage).where(InstalledPackage.user_id == user_id,
                                                               InstalledPackage.package_id == package_id))
               ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "package is not installed")
        await db.delete(row)
        shutil.rmtree(self.install_dir(user_id, package_id), ignore_errors=True)
        await self.core.mirror.remove(user_id, f"package:{package_id}")
        db.add(AuditLog(user_id=user_id, actor="user", event="package_removed", tool=package_id))

    async def get_installed(self, db: AsyncSession, user_id: str, package_id: str) -> InstalledPackage:
        row = (await db.execute(select(InstalledPackage).where(InstalledPackage.user_id == user_id,
                                                               InstalledPackage.package_id == package_id))
               ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "package is not installed")
        return row

    async def service_install(self, user_id: str, name: str) -> dict:
        """`service install <pkg>` from scripts/terminal: only packages without sensitive permissions."""
        async with self.core.db.sessionmaker() as db:
            try:
                res = await self.install(db, user_id, name, approved_permissions=[])
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else exc.detail.get("message")
                if exc.status_code == 403:
                    detail += " - install it from MUR Package Manager to approve: " + \
                        ", ".join(exc.detail.get("needs_approval", []))
                from service_engine.commands import CommandError
                raise CommandError(detail) from None
            await db.commit()
            return res
