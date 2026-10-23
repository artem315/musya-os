"""Loading and validation of ``.yml`` app configs and ``.driver`` manifests.

Both formats are YAML (parsed with ``yaml.safe_load`` - no object
construction). Example ``browser.driver``::

    name: MUR Browser
    version: 1.0.0
    runtime: musya
    entry: main.mysya
    permissions:
      - browser.read
      - browser.click
      - browser.screenshot

:func:`validate_manifest` returns a :class:`Manifest` or raises
:class:`ManifestError` with *every* problem found, so editors can show them
all at once.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Single source of truth for permission names across MURCLOUD.
ALL_PERMISSIONS: dict[str, str] = {
    "browser.read": "Read page content, search text, list tabs, query browser state",
    "browser.navigate": "Start/stop the browser, open URLs, manage tabs, go back/forward",
    "browser.click": "Click, hover, scroll and select elements on pages",
    "browser.type": "Type text and press keys in pages",
    "browser.screenshot": "Capture screenshots of the browser",
    "browser.download": "Download files from the internet into MUR Drive",
    "files.read": "Read files inside the project",
    "files.write": "Create, modify and delete files inside the project",
    "ai.use": "Send prompts to MUR AI",
    "ai.control": "Start, stop and switch the mode of the MUR AI agent",
    "events.emit": "Emit events to the MURCLOUD event bus",
    "packages.install": "Install MURCLOUD packages and drivers",
}

# Permissions that always require explicit user confirmation at install time.
SENSITIVE_PERMISSIONS = {"browser.download", "files.write", "ai.control", "packages.install"}

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
NAME_RE = re.compile(r"^[\w .\-]{1,64}$", re.UNICODE)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")
KINDS = {"app", "driver", "service"}
RUNTIMES = {"musya"}


class ManifestError(ValueError):
    def __init__(self, errors: list[str], path: str | None = None):
        self.errors = errors
        self.path = path
        super().__init__("; ".join(errors))


@dataclass
class Manifest:
    name: str
    version: str
    runtime: str
    entry: str
    kind: str
    permissions: list[str]
    id: str
    description: str = ""
    author: str = ""
    requires: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    preview: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def sensitive_permissions(self) -> list[str]:
        return [p for p in self.permissions if p in SENSITIVE_PERMISSIONS]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64] or "app"


def parse_yaml(text: str, path: str | None = None) -> dict:
    if len(text) > 256 * 1024:
        raise ManifestError(["manifest is larger than 256 KiB"], path)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise ManifestError([f"invalid YAML{where}: {getattr(exc, 'problem', exc)}"], path) from None
    if data is None:
        raise ManifestError(["manifest is empty"], path)
    if not isinstance(data, dict):
        raise ManifestError(["manifest must be a YAML mapping (key: value pairs)"], path)
    return data


def validate_manifest(data: dict | str, *, kind_hint: str | None = None, path: str | None = None,
                      project_root: str | Path | None = None) -> Manifest:
    """Validate a parsed (or raw YAML) manifest.

    ``kind_hint`` is ``"driver"`` for ``.driver`` files and ``"app"`` for ``.yml``.
    When ``project_root`` is given the entry file must exist inside it.
    """
    if isinstance(data, str):
        data = parse_yaml(data, path)
    errors: list[str] = []
    warnings: list[str] = []

    def get_str(key: str, required: bool = True, default: str = "") -> str:
        v = data.get(key, None)
        if v is None:
            if required:
                errors.append(f"'{key}' is required")
            return default
        if isinstance(v, (int, float)) and not isinstance(v, bool) and key == "version":
            v = str(v)
        if not isinstance(v, str):
            errors.append(f"'{key}' must be a string")
            return default
        return v.strip()

    name = get_str("name")
    if name and not NAME_RE.match(name):
        errors.append("'name' may contain letters, digits, spaces, '.', '_' and '-' (max 64 chars)")
    version = get_str("version")
    if version and not SEMVER_RE.match(version):
        errors.append(f"'version' must be semantic (e.g. 1.0.0), got '{version}'")
    runtime = get_str("runtime", required=False, default="musya") or "musya"
    if runtime not in RUNTIMES:
        errors.append(f"unsupported runtime '{runtime}' (supported: {', '.join(sorted(RUNTIMES))})")
    entry = get_str("entry", required=False, default="main.mysya") or "main.mysya"
    if not entry.endswith(".mysya"):
        errors.append("'entry' must point to a .mysya file")
    if entry.startswith("/") or ".." in Path(entry).parts:
        errors.append("'entry' must be a relative path inside the project")
    elif project_root is not None and not (Path(project_root) / entry).is_file():
        errors.append(f"entry file '{entry}' does not exist")

    kind = get_str("type", required=False, default=kind_hint or "app") or (kind_hint or "app")
    if kind not in KINDS:
        errors.append(f"'type' must be one of {', '.join(sorted(KINDS))}")

    perms_raw = data.get("permissions", [])
    permissions: list[str] = []
    if perms_raw is None:
        perms_raw = []
    if not isinstance(perms_raw, list):
        errors.append("'permissions' must be a list")
    else:
        for p in perms_raw:
            if not isinstance(p, str):
                errors.append("each permission must be a string")
                continue
            p = p.strip()
            if p.endswith(".*"):
                family = p[:-2]
                matches = [x for x in ALL_PERMISSIONS if x.startswith(family + ".")]
                if not matches:
                    errors.append(f"unknown permission family '{p}'")
                    continue
                warnings.append(f"wildcard '{p}' expands to {', '.join(matches)}")
                permissions.extend(m for m in matches if m not in permissions)
            elif p not in ALL_PERMISSIONS:
                errors.append(f"unknown permission '{p}'")
            elif p not in permissions:
                permissions.append(p)

    def str_list(key: str) -> list[str]:
        v = data.get(key, [])
        if v is None:
            return []
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            errors.append(f"'{key}' must be a list of strings")
            return []
        return [x.strip() for x in v]

    requires = str_list("requires")
    commands = str_list("commands")
    tests = str_list("tests")
    for t in tests:
        if not t.endswith(".mysya") or t.startswith("/") or ".." in Path(t).parts:
            errors.append(f"test '{t}' must be a relative .mysya path")

    env_raw = data.get("env", {}) or {}
    env: dict[str, str] = {}
    if not isinstance(env_raw, dict):
        errors.append("'env' must be a mapping")
    else:
        for k, v in env_raw.items():
            if not isinstance(k, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                errors.append(f"invalid env name '{k}'")
            elif isinstance(v, (dict, list)):
                errors.append(f"env '{k}' must be a scalar")
            else:
                if re.search(r"(key|secret|token|password)", k, re.I):
                    warnings.append(f"env '{k}' looks like a secret - store secrets in MUR Settings, not in files")
                env[k] = "" if v is None else str(v)

    preview = data.get("preview", {}) or {}
    if not isinstance(preview, dict):
        errors.append("'preview' must be a mapping")
        preview = {}
    elif "root" in preview and (not isinstance(preview["root"], str) or ".." in Path(preview["root"]).parts):
        errors.append("'preview.root' must be a relative folder")

    known = {"name", "version", "runtime", "entry", "type", "permissions", "description", "author",
             "requires", "env", "commands", "tests", "preview", "id", "icon", "homepage", "license"}
    for k in data:
        if k not in known:
            warnings.append(f"unknown key '{k}' ignored")

    description = get_str("description", required=False)
    author = get_str("author", required=False)
    mid = get_str("id", required=False) or slugify(name or "app")
    if not ID_RE.match(mid):
        errors.append("'id' must be lowercase letters, digits and '-'")

    if errors:
        raise ManifestError(errors, path)
    return Manifest(name=name, version=version, runtime=runtime, entry=entry, kind=kind,
                    permissions=permissions, id=mid, description=description, author=author,
                    requires=requires, env=env, commands=commands, tests=tests, preview=preview,
                    warnings=warnings)


def load_manifest_file(path: str | Path, project_root: str | Path | None = None) -> Manifest:
    p = Path(path)
    kind_hint = "driver" if p.suffix == ".driver" else "app"
    return validate_manifest(parse_yaml(p.read_text(encoding="utf-8"), str(p)), kind_hint=kind_hint,
                             path=str(p), project_root=project_root)
