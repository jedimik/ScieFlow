"""The app's mutating routes are an explicit, guarded inventory.

This replaces the read-only milestone's "every route is a GET" test. That
claim stopped being true when run control arrived, but the guarantee behind
it did not: a mutation must never appear without a session guard and CSRF
protection, and never without someone noticing. So the set is listed here by
hand, and the test fails both when a listed path stops mutating and when an
unlisted mutation appears.

`app.openapi()["paths"]` is what FastAPI resolves the nested routers down to;
a shallow walk of `app.routes` sees only the top-level mounts and would pass
while seeing almost nothing. That said, `openapi()` only sees *documented*
routes: a route registered with `include_in_schema=False` (this app has two,
both GET today) or one living under a `Mount` never appears here at all. So
the guarantee this test actually enforces is narrower than "every mutation
is guarded" — it is "no documented mutation lands unnoticed."

`MUTATING_PATHS` here is shared with `tests/web/test_mutations.py`
(`tests/web/mutating_paths.py`), which drives its session/CSRF guard
parametrization from the same list and asserts the two stay in lockstep —
so a route added to the inventory forces a guard case, not just a listing.
"""

from scieflow.web.app import create_app
from tests.web.mutating_paths import MUTATING_PATHS

SAFE_METHODS = {"get", "head", "options"}


def _all_methods(project, token="ro-token") -> dict[str, set[str]]:
    paths = create_app(project, token).openapi()["paths"]
    return {path: {m.lower() for m in methods} for path, methods in paths.items()}


def test_enumeration_actually_sees_the_real_routes(project):
    found = _all_methods(project)
    assert "/api/v1/runs/{slug}" in found
    assert "/runs/{slug}" in found
    assert "/runs/{slug}/jobs/{job_id}" in found
    assert len(found) > 10


def test_the_mutating_routes_are_exactly_the_declared_inventory(project):
    found = _all_methods(project)
    unsafe = {path: methods - SAFE_METHODS
              for path, methods in found.items() if methods - SAFE_METHODS}
    assert unsafe == MUTATING_PATHS, (
        "a mutating route appeared or changed without being declared — add it to "
        "MUTATING_PATHS and make sure it is session-guarded and CSRF-protected")
