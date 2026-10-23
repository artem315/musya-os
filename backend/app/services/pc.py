"""MUR PC: real remote Linux desktops in isolated Docker containers.

Each session is one container from the ``remote-desktop`` image (Xvfb +
XFCE + x11vnc + websockify). The container is attached to an internal
Docker network and never publishes ports; the backend proxies the
authenticated WebSocket (``/api/pc/{id}/vnc``) to it, so VNC is not
reachable from the internet. Containers run with dropped capabilities,
no-new-privileges, memory/CPU/PID limits and a random per-session VNC
password.

Requires a host with Docker (a VPS). Render web services cannot run it.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time

from fastapi import HTTPException

log = logging.getLogger("murcloud.pc")
LABEL = "murcloud.pc"
ALLOWED_APPS = {"terminal": ["xfce4-terminal"], "browser": ["firefox-esr"], "files": ["thunar"],
                "editor": ["mousepad"], "taskmanager": ["xfce4-taskmanager"], "games": ["sgt-launcher"]}


class PCManager:
    def __init__(self, settings, secretbox):
        self.settings = settings
        self.secretbox = secretbox
        self._client = None
        self._error: str | None = None

    def client(self):
        if not self.settings.pc_enabled:
            raise HTTPException(503, "MUR PC is disabled on this server (set MUR_PC_ENABLED=1 on a Docker host)")
        if self._client is None:
            try:
                import docker
                self._client = docker.from_env()
                self._client.ping()
            except Exception as exc:
                self._client = None
                self._error = str(exc)[:200]
                raise HTTPException(503, f"MUR PC needs Docker on the server: {self._error}") from None
        return self._client

    async def availability(self) -> dict:
        if not self.settings.pc_enabled:
            return {"available": False, "reason": "disabled (MUR_PC_ENABLED=0)"}
        try:
            cli = await asyncio.to_thread(self.client)
            await asyncio.to_thread(cli.images.get, self.settings.pc_image)
        except HTTPException as exc:
            return {"available": False, "reason": exc.detail}
        except Exception:
            return {"available": False, "reason": f"image {self.settings.pc_image} is not built - see remote-desktop/README.md"}
        return {"available": True, "image": self.settings.pc_image}

    def _network(self, cli):
        import docker
        try:
            return cli.networks.get(self.settings.pc_network)
        except docker.errors.NotFound:
            # Internal network: containers get no route to the internet or the host LAN by default.
            internal = not bool(int(__import__("os").environ.get("MUR_PC_INTERNET", "0")))
            return cli.networks.create(self.settings.pc_network, driver="bridge", internal=internal,
                                       labels={LABEL: "1"})

    def _containers(self, user_id: str | None = None):
        cli = self.client()
        filters = {"label": [f"{LABEL}=1"] + ([f"{LABEL}.user={user_id}"] if user_id else [])}
        return cli.containers.list(all=True, filters=filters)

    def _info(self, c) -> dict:
        labels = c.labels
        return {"id": c.id[:12], "name": labels.get(f"{LABEL}.name", c.name), "status": c.status,
                "created_at": float(labels.get(f"{LABEL}.created", "0")), "image": self.settings.pc_image,
                "resolution": labels.get(f"{LABEL}.resolution", "1280x800")}

    async def list(self, user_id: str) -> list[dict]:
        return [self._info(c) for c in await asyncio.to_thread(self._containers, user_id)]

    async def create(self, user_id: str, name: str = "MUR PC", resolution: str = "1280x800") -> dict:
        if resolution not in ("1024x768", "1280x800", "1366x768", "1600x900", "1920x1080"):
            raise HTTPException(400, "unsupported resolution")

        def _create():
            cli = self.client()
            mine = self._containers(user_id)
            if len(mine) >= 2:
                raise HTTPException(400, "you already have 2 MUR PC sessions - delete one first")
            if len(self._containers()) >= self.settings.pc_max_sessions:
                raise HTTPException(503, "all MUR PC slots are busy, try later")
            net = self._network(cli)
            password = secrets.token_urlsafe(12)[:8]  # VNC passwords are limited to 8 chars
            c = cli.containers.run(
                self.settings.pc_image, detach=True, name=f"murpc-{user_id[:8]}-{secrets.token_hex(3)}",
                environment={"VNC_PASSWORD": password, "RESOLUTION": resolution},
                labels={LABEL: "1", f"{LABEL}.user": user_id, f"{LABEL}.name": name[:40],
                        f"{LABEL}.created": str(time.time()), f"{LABEL}.resolution": resolution,
                        f"{LABEL}.secret": self.secretbox.encrypt(password)},
                network=net.name, mem_limit=self.settings.pc_memory, nano_cpus=int(self.settings.pc_cpus * 1e9),
                pids_limit=512, cap_drop=["ALL"], cap_add=["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER"],
                security_opt=["no-new-privileges:true"], shm_size="256m", read_only=False,
                tmpfs={"/tmp": "rw,size=256m"}, restart_policy={"Name": "no"})
            return self._info(c)

        return await asyncio.to_thread(_create)

    def _get(self, user_id: str, pc_id: str):
        for c in self._containers(user_id):
            if c.id.startswith(pc_id):
                return c
        raise HTTPException(404, "MUR PC session not found")

    async def action(self, user_id: str, pc_id: str, action: str) -> dict:
        def _do():
            c = self._get(user_id, pc_id)
            if action == "start":
                c.start()
            elif action == "stop":
                c.stop(timeout=5)
            elif action == "restart":
                c.restart(timeout=5)
            elif action == "delete":
                c.remove(force=True)
                return {"deleted": True}
            else:
                raise HTTPException(400, "action must be start, stop, restart or delete")
            c.reload()
            return self._info(c)
        return await asyncio.to_thread(_do)

    async def launch(self, user_id: str, pc_id: str, app: str) -> dict:
        if app not in ALLOWED_APPS:
            raise HTTPException(400, f"app must be one of {', '.join(ALLOWED_APPS)}")

        def _do():
            c = self._get(user_id, pc_id)
            if c.status != "running":
                raise HTTPException(409, "MUR PC is not running")
            c.exec_run(ALLOWED_APPS[app], user="murcloud", environment={"DISPLAY": ":1"}, detach=True)
            return {"launched": app}
        return await asyncio.to_thread(_do)

    async def stats(self, user_id: str, pc_id: str) -> dict:
        def _do():
            c = self._get(user_id, pc_id)
            if c.status != "running":
                return {"status": c.status}
            s = c.stats(stream=False)
            cpu_delta = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
            sys_delta = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
            ncpu = s["cpu_stats"].get("online_cpus") or 1
            cpu = (cpu_delta / sys_delta) * ncpu * 100 if sys_delta > 0 else 0.0
            mem = s.get("memory_stats", {})
            return {"status": c.status, "cpu_percent": round(cpu, 1), "memory_bytes": mem.get("usage", 0),
                    "memory_limit": mem.get("limit", 0), "pids": s.get("pids_stats", {}).get("current")}
        return await asyncio.to_thread(_do)

    async def target(self, user_id: str, pc_id: str) -> tuple[str, int, str]:
        """(container IP, websockify port, VNC password) for the WebSocket proxy."""
        def _do():
            c = self._get(user_id, pc_id)
            c.reload()
            if c.status != "running":
                raise HTTPException(409, "MUR PC is not running")
            nets = c.attrs["NetworkSettings"]["Networks"]
            ip = (nets.get(self.settings.pc_network) or next(iter(nets.values())))["IPAddress"]
            password = self.secretbox.decrypt(c.labels.get(f"{LABEL}.secret", "")) or ""
            return ip, 6080, password
        return await asyncio.to_thread(_do)
