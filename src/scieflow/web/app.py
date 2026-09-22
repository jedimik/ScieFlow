"""The application factory: one FastAPI app over the service layer.

Every route is a thin caller of `scieflow.core.service`, so the browser, the
CLI and the coordinator agent cannot drift apart. Created by
`scieflow serve`; tests build one directly with `create_app(project, token)`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from scieflow.core.project import Project
from scieflow.core.service import ServiceError
from scieflow.web import auth

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def create_app(project: Project, token: str) -> FastAPI:
    app = FastAPI(
        title="ScieFlow",
        summary="Local control surface for agent-driven research runs.",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    app.state.project = project
    app.state.token = token
    app.state.sessions = set()
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    auth.install_session(app)

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(FastAPIHTTPException)
    async def _http_error(request: Request, exc: FastAPIHTTPException):
        wants_html = ("text/html" in request.headers.get("accept", "")
                      and not request.url.path.startswith("/api/"))
        if exc.status_code == 401 and wants_html:
            return TEMPLATES.TemplateResponse(
                request, "unauthorized.html", status_code=401)
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        """Liveness only — deliberately says nothing about the project."""
        return {"ok": True}

    return app


def project_of(request: Request) -> Project:
    return request.app.state.project
