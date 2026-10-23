"""Validation of user-supplied names and virtual paths."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from fastapi import HTTPException

_BAD_CHARS = re.compile(r"[\x00-\x1f/\\<>:\"|?*]")
MAX_DEPTH = 32


def clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name in (".", "..") or len(name) > 255 or _BAD_CHARS.search(name):
        raise HTTPException(400, f"invalid name '{name[:60]}'")
    return name


def clean_path(path: str) -> str:
    """Normalise a Drive path to ``/a/b`` form; rejects ``..`` and control characters."""
    path = (path or "/").replace("\\", "/")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if len(parts) > MAX_DEPTH:
        raise HTTPException(400, "path is too deep")
    for p in parts:
        if p == "..":
            raise HTTPException(400, "'..' is not allowed in paths")
        clean_name(p)
    return "/" + "/".join(parts)


def parent_of(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "/" if parent in ("", ".") else parent


def join(parent: str, name: str) -> str:
    return clean_path(f"{parent.rstrip('/')}/{clean_name(name)}")


def clean_rel(path: str) -> str:
    """Project-relative path (``src/main.mysya``)."""
    p = clean_path(path)
    return p.lstrip("/") or "."
