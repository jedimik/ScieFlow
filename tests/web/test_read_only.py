"""Pins this milestone's central claim: every route in the app is a GET.

A naive walk of `app.routes` in this FastAPI version sees only the handful
of top-level entries (the mounted static files, the three routers) because
each `include_router()` call nests its routes inside a `Mount`-like object
rather than flattening them — so a shallow walk would silently pass while
seeing almost none of the real route map. `app.openapi()["paths"]` is what
FastAPI itself resolves the nested routers down to (it is also what the
Swagger UI and any external client sees), so that is what this test
enumerates. It first proves the enumeration is not vacuous by checking a
known, deeply-nested path shows up before trusting the "every path is safe"
assertion that follows.
"""

from scieflow.web.app import create_app

SAFE_METHODS = {"get", "head", "options"}


def _all_methods(project, token="ro-token") -> dict[str, set[str]]:
    app = create_app(project, token)
    schema = app.openapi()
    return {path: set(methods) for path, methods in schema["paths"].items()}


def test_enumeration_actually_sees_the_real_routes(project):
    """Falsification guard for the enumeration itself: a path that only
    exists because api.py's router was included must appear, or the
    assertion below would pass vacuously over an empty/near-empty map."""
    methods = _all_methods(project)
    assert "/api/v1/runs/{slug}" in methods
    assert "/runs/{slug}" in methods
    assert "/runs/{slug}/jobs/{job_id}" in methods
    # Sanity: this must be more than the handful of top-level entries a
    # shallow, non-recursive walk of `app.routes` would see.
    assert len(methods) > 10


def test_every_route_is_a_safe_method(project):
    methods = _all_methods(project)
    offenders = {path: verbs - SAFE_METHODS for path, verbs in methods.items()
                if verbs - SAFE_METHODS}
    assert not offenders, f"non-GET routes found: {offenders}"
