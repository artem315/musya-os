"""MURCLOUD 10 000 ULTRA backend - FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, get_settings
from .core import Core
from .routers import ai, auth, drive, engine, projects, system, ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline' "
       "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; script-src 'self' "
       "'wasm-unsafe-eval'; connect-src 'self' ws: wss:; frame-src 'self'; worker-src 'self' blob:; "
       "object-src 'none'; base-uri 'self'; frame-ancestors 'self'")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    core = Core(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await core.startup()
        yield
        await core.shutdown()

    app = FastAPI(title="MURCLOUD 10 000 ULTRA API", version="10.0.0-beta", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.core = core

    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not path.startswith("/p/"):
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            response.headers.setdefault("Content-Security-Policy", CSP)
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        body = detail if isinstance(detail, dict) else {"message": str(detail)}
        body.setdefault("message", "error")
        return JSONResponse({"error": body}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    for r in (auth.router, projects.router, projects.preview_router, drive.router, engine.router, ai.router,
              system.router, ws.router):
        app.include_router(r)

    dist = settings.frontend_dist
    if dist and dist.is_dir():
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "unknown API route")
            target = (dist / path).resolve()
            if path and target.is_file() and dist in target.parents:
                cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{path}" else "no-cache"
                return FileResponse(target, headers={"Cache-Control": cache})
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


app = None


def get_app() -> FastAPI:  # uvicorn --factory app.main:get_app
    return create_app()
