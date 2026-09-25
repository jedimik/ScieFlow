"""Every route handler is `def`, except a declared, justified allowlist.

See `tests/web/async_allowlist.py` for why this exists and why each
allowlisted route is on it.

This walks `create_app(...)`'s route tree directly rather than
`app.openapi()["paths"]` (as `tests/web/test_read_only.py` does) for two
reasons: `openapi()` never exposes the handler function itself, only a JSON
description, so there is nothing to call `inspect.iscoroutinefunction` on;
and two of this app's routes (`/api/v1/openapi.json`, `/api/v1/docs`) are
registered with `include_in_schema=False` and would be invisible to an
`openapi()`-based walk regardless. `app.include_router(...)` (`api.router`,
`sse.router`, `pages.router`, one call each in `scieflow.web.app`) wraps each
router rather than flattening its routes into `app.routes` directly, so
`_routes` below follows that one level of nesting by duck-typing
`original_router` — the attribute every wrapper exposes — rather than
importing FastAPI's private wrapper class by name, which is more likely to
move across a FastAPI upgrade than the attribute itself.
"""

import inspect

from fastapi.routing import APIRoute

from scieflow.web.app import create_app
from tests.web.async_allowlist import ASYNC_ALLOWED

SAFE_METHODS = {"HEAD", "OPTIONS"}


def _routes(routable) -> list[APIRoute]:
    found: list[APIRoute] = []
    for route in routable.routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "original_router"):
            found.extend(_routes(route.original_router))
    return found


def _api_routes(project, token: str = "ro-token") -> list[APIRoute]:
    return _routes(create_app(project, token))


def test_the_walk_actually_sees_the_real_routes(project):
    """A sanity check on `_routes` itself: if a FastAPI upgrade changes how
    `include_router` nests things again, this fails loudly here rather than
    letting the tests below pass by silently finding almost nothing."""
    found = {(route.path, method) for route in _api_routes(project)
             for method in route.methods - SAFE_METHODS}
    assert ("/api/v1/runs/{slug}", "GET") in found
    assert ("/runs/{slug}", "GET") in found
    assert ("/api/v1/runs/{slug}/events/stream", "GET") in found
    assert len(found) > 25


def test_no_handler_is_async_outside_the_declared_allowlist(project):
    offenders = []
    for route in _api_routes(project):
        if not inspect.iscoroutinefunction(route.endpoint):
            continue
        for method in route.methods - SAFE_METHODS:
            if (route.path, method) not in ASYNC_ALLOWED:
                offenders.append((method, route.path))
    assert not offenders, (
        "an async def handler exists outside tests/web/async_allowlist.py: "
        f"{sorted(offenders)} — a blocking call in it would freeze the "
        "whole app (this is one uvicorn process). Make it a plain `def` "
        "(FastAPI runs it in the threadpool), or add it to the allowlist "
        "with a reason it must run on the event loop.")


def test_every_allowlisted_route_still_exists_and_is_still_async(project):
    """The other direction: a stale entry would quietly excuse nothing, and
    a route renamed or made synchronous should shrink the allowlist with
    it, not linger unchecked."""
    by_key: dict[tuple[str, str], APIRoute] = {}
    for route in _api_routes(project):
        for method in route.methods - SAFE_METHODS:
            by_key[(route.path, method)] = route
    for (path, method), reason in ASYNC_ALLOWED.items():
        assert reason, f"{method} {path} is on the allowlist with no reason"
        route = by_key.get((path, method))
        assert route is not None, f"{method} {path} is no longer a route"
        assert inspect.iscoroutinefunction(route.endpoint), (
            f"{method} {path} is on the allowlist but is no longer async def "
            "— remove it from tests/web/async_allowlist.py")
