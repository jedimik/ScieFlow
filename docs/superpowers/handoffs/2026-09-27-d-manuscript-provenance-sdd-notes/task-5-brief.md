### Task 5: The history and diff panels

**Files:**
- Modify: `src/scieflow/web/pages.py`, `src/scieflow/web/templates/drafts.html`
- Test: `tests/web/test_provenance_panel.py` (create)

**Interfaces:**
- Consumes: `service.manuscript_history`, `service.manuscript_diff` (Task 4).
- Produces: no new route. `GET /runs/{slug}/drafts` gains optional `diff_a` and `diff_b` query parameters.

**The context key must not be `history`.** `drafts_page` already passes `history` — that is `service.curation_history`, the curation's own version list, which the existing version panel renders. Use `provenance` for the new one. A shared key would let one silently shadow the other depending on dict order, which is exactly the defect a Task-5 fix round in the C plan was spent on.

**No new mutating route.** Both panels are read-only, driven by query parameters on the existing GET. So there is nothing to add to `MUTATING_PATHS` or `SAMPLES`, and no CSRF field on either panel.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_provenance_panel.py
"""The manuscript-history panel and its diff view, on the workbench page."""

import pytest


@pytest.fixture
def merged(project):
    """A run with two merge rounds, so there is something to diff."""
    ws = project.run_dir("r1")
    for n, text in ((1, "merged v1\n"), (2, "merged v2\n")):
        d = ws / "manuscript" / "curation" / "rounds" / str(n)
        d.mkdir(parents=True)
        (d / "results.tex").write_text(text)
    return ws


def test_the_panel_lists_the_rounds(client, merged):
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "main:merge_1" in page.text
    assert "main:merge_2" in page.text


def test_a_run_with_no_history_says_so(client, project):
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "no history yet" in page.text.lower()
    assert "main:merge" not in page.text


def test_the_panel_says_so_when_git_is_missing(client, merged, monkeypatch):
    from scieflow.core import provenance

    monkeypatch.setattr(provenance.shutil, "which", lambda name: None)
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "git is not installed" in page.text
    assert "merged v2" in page.text or "Compile" in page.text, (
        "the rest of the workbench must still render")


def test_a_diff_is_rendered_when_two_points_are_given(client, merged):
    page = client.get("/runs/r1/drafts?diff_a=main:merge_1&diff_b=main:merge_2")
    assert page.status_code == 200
    assert "merged v1" in page.text and "merged v2" in page.text


def test_a_hostile_diff_ref_comes_back_as_a_message_not_a_500(client, merged):
    page = client.get("/runs/r1/drafts?diff_a=--output=/tmp/pwned&diff_b=main:merge_2")
    assert page.status_code == 200
    assert "not a point" in page.text
    import pathlib
    assert not pathlib.Path("/tmp/pwned").exists()


def test_the_curation_version_panel_still_works(client, merged):
    """`history` (the curation's versions) and `provenance` (the manuscript's)
    are different lists. A shared context key would let one shadow the other
    — the defect a whole fix round went to in the C plan."""
    from scieflow.core.run import curation

    curation.add_own(merged, "a passage of my own")
    page = client.get("/runs/r1/drafts")
    assert "a passage of my own" in page.text
    assert "main:merge_1" in page.text, "the provenance panel is also present"


def test_diff_text_is_escaped_not_raw_html(client, merged):
    """A diff carries agent-written text straight into HTML."""
    (merged / "manuscript" / "curation" / "rounds" / "2" / "results.tex").write_text(
        '<script>alert("xss")</script>\n')
    page = client.get("/runs/r1/drafts?diff_a=main:merge_1&diff_b=main:merge_2")
    assert "<script>alert" not in page.text
    assert "&lt;script&gt;" in page.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_provenance_panel.py -v`
Expected: FAIL — the page renders without any of these strings.

- [ ] **Step 3: Write the implementation**

In `pages.py`, extend the existing handler's signature and context. It stays `def`:

```python
@router.get("/runs/{slug}/drafts", response_class=HTMLResponse)
def drafts_page(request: Request, slug: str, error: str = "",
                diff_a: str = "", diff_b: str = "") -> HTMLResponse:
```

Add to the context, keeping `**view` last as it already is:

```python
        "provenance": service.manuscript_history(project, slug),
        "diff": _manuscript_diff(project, slug, diff_a, diff_b),
```

`_manuscript_diff(project, slug, a, b)` returns `None` when either is empty, and otherwise calls `service.manuscript_diff`, catching `ServiceError` and returning `{"error": str(exc)}` so a hostile ref renders as a message rather than a 500. Note the key is `provenance`, **not** `history` — `history` is already the curation's version list.

In `drafts.html`, two regions:
- the history panel: when `provenance.available` is false or `provenance.points` is empty, render `provenance.reason` as plain text and nothing else. Otherwise a list of points, each labelled, each a link back to this page with `diff_a`/`diff_b` set — a pair of selects submitting a GET form is the simplest shape and needs no CSRF field.
- the diff panel: when `diff` is set, render `diff.error` when present, else `diff.text` in a `<pre>`, with the truncation stated when `diff.truncated`.

Render everything with plain `{{ … }}`. **No `|safe`** — a diff carries agent-written text.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass, including `test_read_only.py`, `test_mutations.py` and `test_async_routes.py` — the route inventory is unchanged, which is itself the check that no mutating route was added.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web/test_provenance_panel.py
git commit -m "feat(web): read the manuscript's history and diff it on the workbench"
```

---

