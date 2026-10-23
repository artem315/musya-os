"""SSRF protection for the browser and for downloads.

Every URL the engine navigates to - and every sub-request the page makes,
via Playwright request interception - goes through
:meth:`NetworkPolicy.check_url`. By default only public (globally routable)
addresses are reachable. Loopback/private/link-local/metadata addresses are
refused unless the exact ``host:port`` was allow-listed (used for the
project preview servers MURCLOUD starts for a specific user).

The check resolves DNS itself and rejects a hostname if *any* of its
addresses is non-public. There is an unavoidable gap between this resolution
and Chromium's own (DNS rebinding); interception of every request narrows it
but deployments that need a hard guarantee should also run the browser in a
network namespace without access to internal ranges (see docs/SECURITY.md).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
PASSTHROUGH_SCHEMES = {"data", "blob", "about"}
_BLOCKED_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
                  "metadata.google.internal", "metadata"}


class BlockedURL(Exception):
    def __init__(self, url: str, reason: str):
        super().__init__(f"blocked URL {url!r}: {reason}")
        self.url = url
        self.reason = reason


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise BlockedURL(raw, "empty URL")
    if len(raw) > 8192:
        raise BlockedURL(raw[:64], "URL is too long")
    if "://" not in raw and not raw.startswith(("about:", "data:")):
        raw = "https://" + raw
    return raw


class NetworkPolicy:
    def __init__(self, allow_private: bool = False, allowed_hosts: set[str] | None = None,
                 blocked_hosts: set[str] | None = None, cache_ttl: float = 30.0):
        self.allow_private = allow_private
        self.allowed_hosts = {h.lower() for h in (allowed_hosts or set())}
        self.blocked_hosts = {h.lower() for h in (blocked_hosts or set())}
        self.cache_ttl = cache_ttl
        self._cache: dict[tuple[str, int], tuple[float, str | None]] = {}

    def allow(self, host_port: str) -> None:
        self.allowed_hosts.add(host_port.lower())
        self._cache.clear()

    def revoke(self, host_port: str) -> None:
        self.allowed_hosts.discard(host_port.lower())
        self._cache.clear()

    async def check_url(self, url: str, *, navigation: bool = True) -> str:
        """Return the normalised URL or raise :class:`BlockedURL`."""
        url = normalize_url(url) if navigation else url
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme in PASSTHROUGH_SCHEMES and not navigation:
            return url
        if scheme == "about" and url == "about:blank":
            return url
        if scheme not in ALLOWED_SCHEMES:
            raise BlockedURL(url, f"scheme '{scheme}' is not allowed (only http/https)")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise BlockedURL(url, "URL has no host")
        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError:
            raise BlockedURL(url, "invalid port") from None
        host_port = f"{host}:{port}"
        if host in self.blocked_hosts or host_port in self.blocked_hosts:
            raise BlockedURL(url, "host is blocked by policy")
        if host_port in self.allowed_hosts or host in self.allowed_hosts:
            return url
        if self.allow_private:
            return url
        reason = await self._resolve_reason(host, port)
        if reason:
            raise BlockedURL(url, reason)
        return url

    async def _resolve_reason(self, host: str, port: int) -> str | None:
        key = (host, port)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self.cache_ttl:
            return hit[1]
        reason = await self._compute_reason(host, port)
        if len(self._cache) > 4096:
            self._cache.clear()
        self._cache[key] = (now, reason)
        return reason

    async def _compute_reason(self, host: str, port: int) -> str | None:
        if host in _BLOCKED_NAMES or host.endswith(".localhost") or host.endswith(".internal"):
            return "local hostnames are not reachable from MURCLOUD"
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        if literal is not None:
            return None if _is_public(literal) else f"address {literal} is private/loopback/link-local"
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return "host name could not be resolved"
        if not infos:
            return "host name could not be resolved"
        for info in infos:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
            if not _is_public(ip):
                return f"{host} resolves to non-public address {ip}"
        return None
