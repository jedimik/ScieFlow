"""The app's mutating routes are an explicit, guarded inventory.

This replaces the read-only milestone's "every route is a GET" test. That
claim stopped being true when run control arrived, but the guarantee behind
it did not: a mutation must never appear without a session guard and CSRF
protection, and never without someone noticing. So the set is listed here by
hand, and the test fails both when a listed path stops mutating and when an
unlisted mutation appears.

`app.openapi()["paths"]` is what FastAPI resolves the nested routers down to;
a shallow walk of `app.routes` sees only the top-level mounts and would pass
while seeing almost nothing.
"""

from scieflow.web.app import create_app

SAFE_METHODS = {"get", "head", "options"}

#: Every path that may be mutated, and the methods allowed on it.
MUTATING_PATHS = {
    "/api/v1/runs/{slug}/phase": {"post"},
    "/api/v1/runs/{slug}/advance": {"post"},
    "/api/v1/runs/{slug}/checkpoint": {"post"},
    "/api/v1/runs/{slug}/resume": {"post"},
    "/api/v1/runs/{slug}/spend": {"post"},
    "/api/v1/runs/{slug}/gates/{gate_id}/answer": {"post"},
    "/api/v1/jobs/{job_id}/cancel": {"post"},
    "/runs/{slug}/gates/{gate_id}": {"post"},
    "/runs/{slug}/act": {"post"},
    "/runs/{slug}/jobs/{job_id}/cancel": {"post"},
}


def _all_methods(project, token="ro-token") -> dict[str, set[str]]:
    paths = create_app(project, token).openapi()["paths"]
    return {path: {m.lower() for m in methods} for path, methods in paths.items()}


def test_enumeration_actually_sees_the_real_routes(project):
    found = _all_methods(project)
    assert "/api/v1/runs/{slug}" in found
    assert "/runs/{slug}" in found
    assert len(found) > 10


def test_the_mutating_routes_are_exactly_the_declared_inventory(project):
    found = _all_methods(project)
    unsafe = {path: methods - SAFE_METHODS
              for path, methods in found.items() if methods - SAFE_METHODS}
    assert unsafe == MUTATING_PATHS, (
        "a mutating route appeared or changed without being declared — add it to "
        "MUTATING_PATHS and make sure it is session-guarded and CSRF-protected")
