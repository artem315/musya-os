# MURCLOUD 10 000 ULTRA - backend + built desktop UI + Playwright Chromium in one image.

# ---- 1. build the React desktop ------------------------------------------------
FROM node:22-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. runtime ------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

COPY musya-runtime/ /app/musya-runtime/
COPY service-engine/ /app/service-engine/
COPY mur-ai/ /app/mur-ai/
COPY backend/ /app/backend/
RUN pip install /app/musya-runtime /app/service-engine /app/mur-ai "/app/backend[s3,pc]" \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY packages/ /app/packages/
COPY examples/ /app/examples/
COPY --from=web /src/frontend/dist /app/frontend/dist

RUN useradd -m -u 10001 murcloud && mkdir -p /data && chown murcloud:murcloud /data
USER murcloud
WORKDIR /app/backend

ENV MUR_DATA_DIR=/data MUR_FRONTEND_DIST=/app/frontend/dist MUR_PACKAGES_DIR=/app/packages PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health')"
# --proxy-headers: trust X-Forwarded-* from the TLS-terminating reverse proxy in front (Caddy / Render).
CMD ["sh", "-c", "exec uvicorn --factory app.main:get_app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1} --ws-max-size 16777216"]
