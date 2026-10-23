#!/bin/bash
# Start the MUR PC desktop: virtual X server, XFCE session, VNC server, WebSocket bridge.
set -euo pipefail

: "${VNC_PASSWORD:?VNC_PASSWORD must be set}"
RES="${RESOLUTION:-1280x800}"
mkdir -p "$HOME/.vnc" "$HOME/Desktop"
x11vnc -storepasswd "$VNC_PASSWORD" "$HOME/.vnc/passwd" >/dev/null 2>&1
unset VNC_PASSWORD

Xvfb :1 -screen 0 "${RES}x24" -nolisten tcp -ac &
for _ in $(seq 1 100); do xset -display :1 q >/dev/null 2>&1 && break; sleep 0.1; done

dbus-launch --exit-with-session startxfce4 >/tmp/xfce.log 2>&1 &

# VNC only on localhost inside the container; websockify exposes it as WebSocket on 6080.
x11vnc -display :1 -rfbauth "$HOME/.vnc/passwd" -localhost -forever -shared -noxdamage -quiet \
       -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
exec websockify --heartbeat 30 0.0.0.0:6080 127.0.0.1:5900
