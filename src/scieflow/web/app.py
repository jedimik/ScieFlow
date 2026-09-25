"""The application factory: one FastAPI app over the service layer.

Every route is a thin caller of `scieflow.core.service`, so the browser, the
CLI and the coordinator agent cannot drift apart. Created by
`scieflow serve`; tests build one directly with `create_app(project, token)`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
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
        # FastAPI's automatic schema/docs routes take no dependencies, so
        # registering them at these paths directly would make the route map
        # and interactive UI reachable by anything that can open the port,
        # without ever seeing the printed token. Disable the automatic ones
        # and re-register both below, behind the same session requirement
        # as every other /api/v1 route.
        docs_url=None,
        openapi_url=None,
    )
    app.state.project = project
    app.state.token = token
    app.state.sessions = set()
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    def _refuse(request: Request, status: int, message: str):
        wants_html = ("text/html" in request.headers.get("accept", "")
                      and not request.url.path.startswith("/api/"))
        if wants_html and status in (401, 403):
            return TEMPLATES.TemplateResponse(
                request, "unauthorized.html", status_code=status)
        return JSONResponse({"error": message}, status_code=status)

    auth.install_session(app, refuse=_refuse)

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(FastAPIHTTPException)
    async def _http_error(request: Request, exc: FastAPIHTTPException):
        return _refuse(request, exc.status_code, exc.detail)

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict:
        """Liveness only — deliberately says nothing about the project."""
        return {"ok": True}

    @app.get("/api/v1/openapi.json", include_in_schema=False,
             dependencies=[Depends(auth.require_session)])
    def openapi_schema() -> dict:
        return app.openapi()

    @app.get("/api/v1/docs", include_in_schema=False,
             dependencies=[Depends(auth.require_session)])
    def swagger_docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/api/v1/openapi.json",
                                   title=f"{app.title} — Swagger UI")

    from scieflow.web import api, pages, sse

    app.include_router(api.router)
    app.include_router(sse.router)
    app.include_router(pages.router)

    return app


def project_of(request: Request) -> Project:
    return request.app.state.project
