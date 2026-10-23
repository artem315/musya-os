# MUR PC — remote Linux desktop

A real Debian + XFCE desktop per user session, running in an isolated Docker
container and streamed to the browser with noVNC.

```
browser (noVNC) ──wss──▶ MURCLOUD backend /api/pc/{id}/vnc  (session cookie, Origin check)
                              │  proxy, no ports published
                              ▼
                  container on internal network  websockify :6080 ─▶ x11vnc :5900 (localhost) ─▶ Xvfb :1 + XFCE
```

## Build the image

```bash
docker build -t murcloud/mur-pc:10.0 remote-desktop/
```

## Enable in the backend

| variable | default | meaning |
|---|---|---|
| `MUR_PC_ENABLED` | `0` | turn MUR PC on (needs access to the Docker socket) |
| `MUR_PC_IMAGE` | `murcloud/mur-pc:10.0` | image to start |
| `MUR_PC_MEMORY` / `MUR_PC_CPUS` | `2g` / `1.5` | limits per session |
| `MUR_PC_MAX_SESSIONS` | `5` | total sessions on the host (max 2 per user) |
| `MUR_PC_NETWORK` | `murcloud-pc` | Docker network; created as `internal` (no internet) |
| `MUR_PC_INTERNET` | `0` | `1` creates the network with outbound internet access |

If the backend itself runs in Docker (docker-compose), mount
`/var/run/docker.sock` and attach the backend container to `MUR_PC_NETWORK`
so it can reach the sessions. **Access to the Docker socket is root-equivalent
on the host** — run MUR PC only on a dedicated VPS you control.

## Isolation

* the desktop runs as an unprivileged user (`uid 1000`), every Linux
  capability dropped, `no-new-privileges`, PID/memory/CPU limits, tmpfs `/tmp`;
* no published ports — VNC is bound to localhost inside the container, and
  websockify is only reachable from the backend over the internal network;
* each session gets a random VNC password; the backend only proxies a
  session to its owner;
* launching programs through the API is limited to an allow-list
  (terminal, Firefox, files, editor, task manager, SGT Puzzles).

Render (and similar PaaS) cannot run Docker containers, so MUR PC needs a
separate VPS. The rest of MURCLOUD works without it and the UI says so.
