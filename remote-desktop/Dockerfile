# MUR PC - an isolated Linux desktop for MURCLOUD (XFCE + Xvfb + x11vnc + websockify).
# Runs entirely as an unprivileged user; publishes nothing - the MURCLOUD backend
# proxies the authenticated WebSocket to port 6080 on an internal Docker network.
FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc websockify xfce4 xfce4-terminal xfce4-taskmanager thunar mousepad \
        firefox-esr sgt-puzzles dbus-x11 x11-xserver-utils fonts-dejavu fonts-noto-color-emoji \
        ca-certificates procps curl locales \
    && sed -i 's/# ru_RU.UTF-8/ru_RU.UTF-8/; s/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen \
    && (apt-get purge -y xfce4-power-manager xscreensaver || true) \
    && rm -rf /var/lib/apt/lists/* \
    && command -v Xvfb x11vnc websockify startxfce4 firefox-esr /usr/games/mines

RUN useradd -m -s /bin/bash -u 1000 murcloud
COPY --chown=murcloud:murcloud entrypoint.sh /usr/local/bin/mur-pc
COPY --chown=murcloud:murcloud welcome.txt /home/murcloud/Desktop/Добро_пожаловать.txt
RUN chmod 755 /usr/local/bin/mur-pc

USER murcloud
WORKDIR /home/murcloud
ENV DISPLAY=:1 RESOLUTION=1280x800 LANG=ru_RU.UTF-8 HOME=/home/murcloud
EXPOSE 6080
HEALTHCHECK --interval=15s --timeout=3s CMD curl -fs http://127.0.0.1:6080/ >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/mur-pc"]
