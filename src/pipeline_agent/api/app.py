"""FastAPI app for the Ops Console: /api/v1 + the built React app (frontend/dist)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..config import Settings
from .actions import router as actions_router
from .auth import ROLES
from .context import ConsoleContext
from .views import router as views_router

log = logging.getLogger(__name__)


def find_dist(configured: Path | None = None) -> Path:
    """Built UI location: AGENT_CONSOLE_DIST, else ./frontend/dist (container), else the source checkout."""
    for candidate in (configured, Path.cwd() / "frontend" / "dist", Path(__file__).resolve().parents[3] / "frontend" / "dist"):
        if candidate and (candidate / "index.html").exists():
            return candidate
    return Path.cwd() / "frontend" / "dist"


DIST = find_dist()

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # Fluent UI injects styles at runtime (CSS-in-JS), hence 'unsafe-inline' for styles only.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


class DemoLogin(BaseModel):
    name: str = Field(min_length=2, max_length=60, pattern=r"^[\w .@-]+$")
    role: str


def create_app(settings: Settings | None = None, context: ConsoleContext | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Pipeline Ops Console API", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.ctx = context or ConsoleContext(settings)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/v1/auth/config")
    def auth_config() -> dict:
        return {"mode": app.state.ctx.settings.console_auth, "roles": list(ROLES)}

    @app.post("/api/v1/auth/demo-login")
    def demo_login(body: DemoLogin) -> dict:
        if app.state.ctx.settings.console_auth != "demo":
            raise HTTPException(404, "demo login is disabled")
        if body.role not in ROLES:
            raise HTTPException(400, f"role must be one of {ROLES}")
        token = app.state.ctx.signer.issue(body.name.strip(), body.role)
        app.state.ctx.store.append_audit(f"user:{body.name.strip()} ({body.role})", "console_login", "console", {"mode": "demo"})
        return {"token": token, "name": body.name.strip(), "role": body.role}

    app.include_router(views_router)
    app.include_router(actions_router)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # never leak stack traces to the browser
        log.exception("console API error on %s", request.url.path)
        return JSONResponse({"detail": "internal error (see console server log)"}, status_code=500)

    dist = find_dist(settings.console_dist)
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            file = (dist / path).resolve()
            if path and file.is_file() and dist.resolve() in file.parents:  # no path traversal
                return FileResponse(file)
            return FileResponse(dist / "index.html")
    return app
