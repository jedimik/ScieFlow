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
from python_multipart.exceptions import FormParserError
from starlette.formparsers import MultiPartException

#: What a malformed body actually raises out of `request.form()`: Starlette's
#: own wrapper (missing boundary, too many fields, a part over its size cap)
#: or the underlying python-multipart parser's own error (corrupt content
#: that doesn't match the declared boundary at all — `FormParserError` is the
#: base of `MultipartParseError`, `DecodeError`, etc.). Anything else —
#: `starlette.requests.ClientDisconnect`, or a future bug in the body/form
#: caching dance `_supplied_csrf` relies on — is a real failure, not a
#: malformed-input case, and must not be swallowed as one.
BODY_PARSE_ERRORS = (MultiPartException, FormParserError)

SESSION_COOKIE = "scieflow_session"
CSRF_COOKIE = "scieflow_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"
FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})


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


def _issue_session(request: Request, response) -> None:
    session = secrets.token_urlsafe(32)
    request.app.state.sessions.add(session)
    response.set_cookie(SESSION_COOKIE, session, httponly=True,
                        samesite="strict", path="/")
    # Only issue a fresh CSRF cookie when this request carries none. The
    # printed `?token=` URL is an ordinary thing to open twice (a second tab,
    # a bookmark) — rotating the cookie on every exchange would 403 a form
    # rendered before the re-open, purely because its embedded token no
    # longer matches the new cookie.
    if not request.cookies.get(CSRF_COOKIE):
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


async def _supplied_csrf(request: Request) -> str:
    """The token from the header, or from a form field when a browser posted
    a form.

    Reading the body here is the delicate part, because the middleware below
    runs BEFORE routing: if the body were consumed, the route would receive
    an empty form. Starlette's `BaseHTTPMiddleware` wraps the request in a
    `_CachedRequest` whose `wrapped_receive` replays `_body` to the app
    downstream — but only once `body()` has actually been called. So call
    `body()` first, and `form()` afterwards reads from that cache for both
    urlencoded and multipart payloads.

    Only the middleware below calls this. The per-route `csrf_protect`
    dependency trusts the verdict the middleware already recorded instead of
    parsing the body a second time — see its docstring for why.
    """
    header = request.headers.get(CSRF_HEADER)
    if header:
        return header
    if not request.headers.get("content-type", "").startswith(FORM_TYPES):
        return ""
    await request.body()            # cache it, so the route still sees it
    form = await request.form()
    return str(form.get(CSRF_FIELD) or "")


def csrf_ok(cookie: str, supplied: str) -> bool:
    return bool(cookie) and bool(supplied) and secrets.compare_digest(cookie, supplied)


async def csrf_protect(request: Request) -> None:
    """FastAPI dependency for unsafe methods: double-submit cookie check.

    Every unsafe request already passed through `install_session`'s
    middleware before reaching any route — that is where the double-submit
    check actually happens, on `request.state.csrf_checked`, because it is
    the one place that can read the token as a form field without racing
    FastAPI's own `Form(...)` parsing for the route's parameters (which
    consumes the same body first, downstream of this dependency, for any
    route that declares one). So this dependency does not re-derive the
    verdict; it trusts the flag. A missing flag — a router mounted without
    `install_session`, say — fails closed as a 403, not a silent pass.
    """
    if request.method in SAFE_METHODS:
        return
    if not getattr(request.state, "csrf_checked", False):
        raise HTTPException(status_code=403, detail="CSRF token missing or wrong")


def csrf_token(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE, "")


def install_session(app, refuse=None) -> None:
    """Register the token → cookie exchange as middleware.

    Middleware, not a dependency, because the exchange has to happen for
    every path — pages and API alike — before routing decides anything.

    `refuse(request, status, message)` renders a refusal; it exists because
    this middleware answers before any exception handler can, so without it
    a browser form would get a raw JSON body instead of a page.
    """
    if refuse is None:
        def refuse(request, status, message):
            return JSONResponse({"error": message}, status_code=status)

    @app.middleware("http")
    async def _session(request: Request, call_next):
        supplied = request.query_params.get("token")
        if supplied is not None:
            if not secrets.compare_digest(supplied, request.app.state.token):
                return refuse(request, 403, "bad token")
            clean = str(request.url.remove_query_params("token"))
            response = RedirectResponse(clean, status_code=303)
            _issue_session(request, response)
            return response
        if request.method not in SAFE_METHODS:
            # Central enforcement: every unsafe request needs the
            # double-submit token, as a header or a form field, whether or
            # not the route that will handle it also declares
            # `Depends(csrf_protect)`. Record the verdict on the request
            # state so that dependency can trust it instead of re-parsing
            # the body — see `csrf_protect`'s docstring for why.
            try:
                supplied_csrf = await _supplied_csrf(request)
            except BODY_PARSE_ERRORS:
                # `request.form()` parses the body eagerly; a malformed
                # multipart payload (bad boundary, truncated body) raises
                # straight out of this middleware, upstream of every
                # exception handler (those live in ExceptionMiddleware,
                # downstream of call_next). Fail closed with a clean 400
                # instead of leaking a 500 traceback from the one function
                # that runs before routing decides anything. Only the
                # parser's own errors are caught here — anything else (a
                # client disconnect, a future bug) is a real failure and
                # must surface as one, not be reported as bad input.
                return refuse(request, 400, "malformed request body")
            if not csrf_ok(request.cookies.get(CSRF_COOKIE) or "", supplied_csrf):
                return refuse(request, 403, "CSRF token missing or wrong")
            request.state.csrf_checked = True
        return await call_next(request)
