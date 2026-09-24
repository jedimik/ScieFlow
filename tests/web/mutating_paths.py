"""Single source of truth for every mutating route this app exposes.

`tests/web/test_read_only.py` reads `MUTATING_PATHS` to check the real route
inventory (`app.openapi()["paths"]`) hasn't drifted. `tests/web/test_mutations.py`
reads `SAMPLES` — a concrete path template plus a form payload for each one —
to drive its session-guard and CSRF-guard parametrization from the *same*
list, and asserts `set(SAMPLES) == set(MUTATING_PATHS)`. That assertion is
the point: a route added to one without the other used to be possible (and
happened), so adding a route here now forces a guard case to be added too.

The inventory's paths are templated (`/runs/{slug}/...`); `concrete_path`
fills them in with placeholder ids. The guard tests never need real ids —
a session-less or CSRF-less request is refused before it ever reaches the
service layer to look one up.
"""

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
    "/agents": {"post"},
}

#: Placeholder ids for the templated paths above. Guard tests never resolve
#: these against real data — the request is refused before routing looks
#: anything up — so a fixed placeholder is enough.
PLACEHOLDERS = {"slug": "r1", "gate_id": "no-such-gate", "job_id": "no-such-job"}

#: A sample form body for each mutating path, keyed by the same template.
SAMPLES: dict[str, dict] = {
    "/api/v1/runs/{slug}/phase": {"phase": "hypothesize", "state": "running"},
    "/api/v1/runs/{slug}/advance": {},
    "/api/v1/runs/{slug}/checkpoint": {"reason": "user"},
    "/api/v1/runs/{slug}/resume": {},
    "/api/v1/runs/{slug}/spend": {"experiment_runs": "1"},
    "/api/v1/runs/{slug}/gates/{gate_id}/answer": {"answer": "A"},
    "/api/v1/jobs/{job_id}/cancel": {},
    "/runs/{slug}/gates/{gate_id}": {"answer": "A"},
    "/runs/{slug}/act": {"action": "resume"},
    "/runs/{slug}/jobs/{job_id}/cancel": {},
    "/agents": {"assign": "research.outline=stub2"},
}


def concrete_path(template: str) -> str:
    """The templated path with placeholder ids filled in."""
    return template.format(**PLACEHOLDERS)
