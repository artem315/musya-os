"""Permission model of the Service Engine.

Every ``service`` verb maps to exactly one permission from
:data:`musya_runtime.ALL_PERMISSIONS`. Scripts run with the permissions in
their ``.yml``/``.driver`` manifest; the interactive owner (Service Studio
terminal) runs with every permission; MUR AI runs with the set the user
granted to the agent session plus per-call approvals.
"""

from __future__ import annotations

from musya_runtime import ALL_PERMISSIONS

from .network import BlockedURL, NetworkPolicy

VERB_PERMISSIONS: dict[str, str] = {
    "start": "browser.navigate",
    "stop": "browser.navigate",
    "status": "browser.read",
    "load": "browser.navigate",
    "open": "browser.navigate",
    "back": "browser.navigate",
    "forward": "browser.navigate",
    "reload": "browser.navigate",
    "tab": "browser.navigate",
    "search": "browser.read",
    "exists": "browser.read",
    "text": "browser.read",
    "url": "browser.read",
    "title": "browser.read",
    "html": "browser.read",
    "errors": "browser.read",
    "wait": "browser.read",
    "click": "browser.click",
    "dblclick": "browser.click",
    "hover": "browser.click",
    "click_at": "browser.click",
    "scroll": "browser.click",
    "select": "browser.click",
    "type": "browser.type",
    "fill": "browser.type",
    "press": "browser.type",
    "screenshot": "browser.screenshot",
    "download": "browser.download",
    "event": "events.emit",
    "ai": "ai.control",
    "install": "packages.install",
}

assert set(VERB_PERMISSIONS.values()) <= set(ALL_PERMISSIONS), "unknown permission in VERB_PERMISSIONS"


class PermissionDenied(Exception):
    def __init__(self, permission: str, verb: str):
        super().__init__(f"permission '{permission}' is required for 'service {verb}'")
        self.permission = permission
        self.verb = verb


def permission_for(verb: str) -> str | None:
    return VERB_PERMISSIONS.get(verb)


def check(verb: str, granted: set[str] | None) -> None:
    """Raise :class:`PermissionDenied` unless ``granted`` covers ``verb``. ``None`` = all."""
    perm = permission_for(verb)
    if perm is None or granted is None:
        return
    family = perm.split(".", 1)[0]
    if perm in granted or f"{family}.*" in granted or "*" in granted:
        return
    raise PermissionDenied(perm, verb)


__all__ = ["VERB_PERMISSIONS", "PermissionDenied", "permission_for", "check", "NetworkPolicy",
           "BlockedURL"]
