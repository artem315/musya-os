#!/bin/sh
# Container entrypoint. Hosted platforms (Render, Fly, ...) mount persistent disks owned by
# root; when started as root we hand /data to the unprivileged user, then drop privileges.
set -e
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${MUR_DATA_DIR:-/data}"
    if [ "$(stat -c %u "${MUR_DATA_DIR:-/data}")" != "10001" ]; then
        chown -R murcloud:murcloud "${MUR_DATA_DIR:-/data}"
    fi
    export HOME=/home/murcloud  # libraries (asyncpg, Playwright) read ~/ of the runtime user
    exec setpriv --reuid=murcloud --regid=murcloud --init-groups "$@"
fi
exec "$@"
