"""Loopback session auth: a one-time token in the URL becomes a cookie.

`scieflow serve` prints http://127.0.0.1:<port>/?token=<token>, Jupyter
style. Opening it checks the token in constant time, issues an httponly
session cookie and redirects to the same path without the token, so the
token never lingers in history, a bookmark or a referrer. Unsafe methods
additionally need the double-submit CSRF header.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

SESSION_COOKIE = "scieflow_session"
CSRF_COOKIE = "scieflow_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})
OPEN_PATHS = frozenset({"/healthz"})


def loopback_only(host: str) -> str:
    """The only bind this app accepts. Remote access is a tunnel's job."""
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"refusing to bind {host!r}: the ScieFlow web app serves loopback "
            "only. For another machine, forward the port over an SSH tunnel "
            "or Tailscale instead of exposing it."
        )
    return host


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _issue_session(app, response) -> None:
    session = secrets.token_urlsafe(32)
    app.state.sessions.add(session)
    response.set_cookie(SESSION_COOKIE, session, httponly=True,
                        samesite="strict", path="/")
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), httponly=False,
                        samesite="strict", path="/")


def has_session(request: Request) -> bool:
    session = request.cookies.get(SESSION_COOKIE)
    return bool(session) and session in request.app.state.sessions


def require_session(request: Request) -> None:
    """FastAPI dependency: 401 unless this request carries a live session."""
    if not has_session(request):
        raise HTTPException(status_code=401, detail="no session; open the URL "
                                                    "printed by `scieflow serve`")


def csrf_protect(request: Request) -> None:
    """FastAPI dependency for unsafe methods: double-submit cookie check."""
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    header = request.headers.get(CSRF_HEADER) or ""
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="CSRF token missing or wrong")


def install_session(app) -> None:
    """Register the token → cookie exchange as middleware.

    Middleware, not a dependency, because the exchange has to happen for
    every path — pages and API alike — before routing decides anything.
    """

    @app.middleware("http")
    async def _session(request: Request, call_next):
        supplied = request.query_params.get("token")
        if supplied is not None:
            if not secrets.compare_digest(supplied, request.app.state.token):
                return JSONResponse({"error": "bad token"}, status_code=403)
            clean = str(request.url.remove_query_params("token"))
            response = RedirectResponse(clean, status_code=303)
            _issue_session(request.app, response)
            return response
        if request.method not in SAFE_METHODS:
            # Central enforcement: every unsafe request needs the
            # double-submit CSRF header, whether or not the route that
            # will handle it also declares `Depends(csrf_protect)`. Every
            # route is a GET today, so this only guards against the next
            # milestone's first POST forgetting the per-route dependency.
            cookie = request.cookies.get(CSRF_COOKIE) or ""
            header = request.headers.get(CSRF_HEADER) or ""
            if not cookie or not header or not secrets.compare_digest(cookie, header):
                return JSONResponse({"error": "CSRF token missing or wrong"},
                                    status_code=403)
        return await call_next(request)
