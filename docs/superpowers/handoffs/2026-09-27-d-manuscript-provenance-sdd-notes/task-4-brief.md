### Task 4: The service layer and the guarded sync after a merge round

**Files:**
- Modify: `src/scieflow/core/service.py`
- Test: `tests/core/test_service.py` (append), `tests/core/test_merge_round.py` (append)

**Interfaces:**
- Consumes: `provenance.available`, `ensure_repo`, `sync`, `points`, `diff`, `ProvenanceError` (Tasks 1–3).
- Produces:
  `service.manuscript_history(project, slug) -> dict` — `{"available": bool, "points": [...], "reason": str}`;
  `service.manuscript_diff(project, slug, a, b) -> dict` — `{"text": str, "truncated": bool, "a": str, "b": str}`.

**`manuscript_history` never raises for an ordinary state.** `git` absent, no history yet, a repo that had to be rebuilt — all return the dict with `available`/`points`/`reason` filled in. Only a genuinely malformed `slug` raises `ServiceError`, via `_ws`.

**The sync after a merge round is best-effort and must never fail the round.** The round happened and cost budget; losing a commit is not worth losing that.

- [ ] **Step 1: Write the failing test**

```python
# appended to tests/core/test_service.py

def test_manuscript_history_reports_no_history_for_a_fresh_run(project):
    view = service.manuscript_history(project, "r1")
    assert view["available"] is True
    assert view["points"] == []
    assert "no history" in view["reason"].lower()


def test_manuscript_history_lists_points_after_a_round(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")

    view = service.manuscript_history(project, "r1")
    assert view["available"] is True
    assert "main:merge_1" in [p["ref"] for p in view["points"]]


def test_manuscript_history_degrades_when_git_is_missing(project, monkeypatch):
    from scieflow.core import provenance

    monkeypatch.setattr(provenance.shutil, "which", lambda name: None)
    view = service.manuscript_history(project, "r1")
    assert view["available"] is False
    assert view["points"] == []
    assert "git" in view["reason"].lower(), "the page must be able to say why"


def test_manuscript_diff_refuses_a_ref_that_is_not_a_point(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")
    service.manuscript_history(project, "r1")

    with pytest.raises(service.ServiceError, match="not a point"):
        service.manuscript_diff(project, "r1", "--output=/tmp/x", "main:merge_1")


def test_manuscript_diff_translates_a_provenance_error(project):
    with pytest.raises(service.ServiceError):
        service.manuscript_diff(project, "r1", "main:merge_1", "main:merge_2")


def test_manuscript_history_refuses_an_unknown_run(project):
    with pytest.raises(service.ServiceError):
        service.manuscript_history(project, "nope")
```

```python
# appended to tests/core/test_merge_round.py

def test_a_successful_round_syncs_the_provenance_repo(project, curated, responder):
    from scieflow.core import provenance

    service.merge_round(project, "r1")
    ws = project.run_dir("r1")
    assert provenance.repo_path(ws).is_dir(), "a successful round did not sync"


def test_a_failing_provenance_sync_does_not_fail_the_round(project, curated, responder,
                                                           monkeypatch):
    """The round happened and cost budget. Losing a commit is not worth losing
    that. Falsify by removing the guard around the sync call: this test then
    fails with the raised ProvenanceError instead of returning a round."""
    from scieflow.core import provenance

    def boom(ws):
        raise provenance.ProvenanceError("git exploded")

    monkeypatch.setattr(service.provenance, "sync", boom)
    result = service.merge_round(project, "r1")
    assert result["round"] == 2, "the round must still advance"
    assert result["turn"]["job"]["state"] == service.MERGE_SUCCESS_STATE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_service.py tests/core/test_merge_round.py -k "manuscript or provenance" -v`
Expected: FAIL — `module 'scieflow.core.service' has no attribute 'manuscript_history'`.

- [ ] **Step 3: Write the implementation**

Import `provenance` in `service.py` beside the other core imports — the merge-round test monkeypatches `service.provenance.sync`, so the module must be reachable by that name.

```python
def manuscript_history(project: Project, slug: str) -> dict:
    """The run's provenance points, and why there are none when there are none.

    Never raises for an ordinary state: `git` absent, a run that has produced
    nothing yet, and a repo that had to be rebuilt all come back as data the
    page can render. Syncs first, so opening the page catches up anything the
    workflow wrote since the last merge round.
    """
    ws = _ws(project, slug)
    if not provenance.available():
        return {"available": False, "points": [],
                "reason": "git is not installed, so this run has no manuscript history; "
                          "the drafts and rounds above are unaffected"}
    try:
        provenance.sync(ws)
        points = provenance.points(ws)
    except provenance.ProvenanceError as exc:
        return {"available": True, "points": [], "reason": f"no history yet: {exc}"}
    reason = "" if points else ("no history yet — it appears once an agent has drafted "
                                "or a merge round has completed")
    return {"available": True, "points": points, "reason": reason}


def manuscript_diff(project: Project, slug: str, a: str, b: str) -> dict:
    ws = _ws(project, slug)
    try:
        return provenance.diff(ws, a, b)
    except provenance.ProvenanceError as exc:
        raise ServiceError(str(exc)) from exc
```

In `merge_round`, after the success branch has advanced the round, sync best-effort:

```python
    if turn["job"]["state"] == MERGE_SUCCESS_STATE:
        advanced = curation.advance_round(ws)
        # Best-effort: a sync failure must not fail a round that genuinely
        # happened and cost budget. The repo is derived, so the next page load
        # rebuilds whatever this missed — and because rounds are preserved as
        # paths rather than commit boundaries, a skipped sync costs nothing but
        # a commit.
        try:
            provenance.sync(ws)
        except provenance.ProvenanceError as exc:
            events.emit(ws, "provenance.skipped", "system", why=str(exc))
        return {"round": advanced, "turn": turn}
```

Keep the `except` to `provenance.ProvenanceError` specifically — a bare `except Exception` here would swallow a programming error in the sync for the life of the feature.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core -v && uv run pytest -q`
Expected: all pass; full suite green. Falsify the guard test by removing the `try`/`except` around `provenance.sync` — it must fail — then restore it and record what the failure said.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/service.py tests/core/test_service.py tests/core/test_merge_round.py
git commit -m "feat(core): expose the manuscript history, and sync it after a merge round"
```

---

