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


def test_a_hostile_agent_name_is_escaped_in_the_history_panel(client, project):
    """`diff.text` has its own escaping test, but `point.ref`/`point.label`
    reach HTML too, and are not inert: `provenance._draft_points` embeds an
    agent-chosen directory name into both (`f"{short}:sections"` and
    `f"{agent}'s draft"`), and this template's own comment a few lines above
    the drafts grid already says an agent name is "not a value from a safe
    alphabet". Two sinks, not one: `point.ref`/`point.label` reach the page
    as body text (`<li><code>{{ point.ref }}</code> — {{ point.label }}`)
    *and* as an attribute value (`<option value="{{ point.ref }}">`, twice,
    once per `<select>`) — a raw `"` in the latter would break out of
    `value="…"` and hand an attacker attribute-injection, which a body-text
    check alone would never catch.

    `check_name`/`ref_safe` forbid `/` (a path separator) and control
    characters, but not `"` or `<`/`>` — confirmed directly: `git
    check-ref-format` and `drafts.check_name` both accept this payload as a
    branch/directory name, so it is a genuine agent name this feature must
    render safely, not a value the upstream validation already excludes.
    """
    from markupsafe import escape

    name = 'evil"><script>alert(1)'
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / name
    d.mkdir(parents=True)
    (d / "results.tex").write_text("hostile draft\n")

    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200

    ref = f"draft/{name}:sections"
    label = f"{name}'s draft"

    # Nowhere on the page does the raw payload survive unescaped.
    assert '"><script>alert(1)' not in page.text
    assert "<script>alert(1)" not in page.text

    # Body text (the <li> row) carries the escaped ref and label.
    assert str(escape(ref)) in page.text
    assert str(escape(label)) in page.text

    # The attribute sink specifically: a raw '"' here would break out of
    # value="…" — assert the option's whole value attribute round-trips
    # escaped, not just that the escaped text appears somewhere on the page.
    assert f'value="{escape(ref)}"' in page.text


def test_a_diff_is_rendered_when_two_points_are_given(client, merged):
    """`"merged v1"`/`"merged v2"` alone would also pass with the diff panel
    entirely absent — the "Merged rounds" panes above already render each
    round's own text verbatim. Assert on the diff's own shape instead: the
    unified-diff `-`/`+` line prefixes, which only `git diff`'s own output
    (not a round pane) can produce."""
    page = client.get("/runs/r1/drafts?diff_a=main:merge_1&diff_b=main:merge_2")
    assert page.status_code == 200
    assert "-merged v1" in page.text and "+merged v2" in page.text
    assert "diff --git" in page.text


def test_a_hostile_diff_ref_comes_back_as_a_message_not_a_500(client, merged):
    """`"not a point"` alone would also pass on a page with no diff feature
    at all: this app already carries the unrelated phrase "a quotation, not
    a pointer" in `drafts.html`'s own script comment, and `"not a point"` is
    a substring of `"not a pointer"`. Assert the *whole* refusal message
    `service.manuscript_diff` actually raises — `provenance.diff`'s
    `f"not a point: {a!r}"`, HTML-escaped as this page always renders it —
    so this can only match the real error path, never that unrelated prose.
    """
    from markupsafe import escape

    hostile = "--output=/tmp/pwned"
    page = client.get(f"/runs/r1/drafts?diff_a={hostile}&diff_b=main:merge_2")
    assert page.status_code == 200
    assert str(escape(f"not a point: {hostile!r}")) in page.text
    import pathlib
    assert not pathlib.Path("/tmp/pwned").exists()


def test_no_diff_panel_without_diff_params(client, merged):
    """The counterpart to the `{% if False %}` probe used to falsify the
    tests above: without this, disabling the diff-rendering block would
    change nothing observable, and the block-disabled probe on the tests
    above would prove nothing."""
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert '<pre class="diff">' not in page.text


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
    """A diff carries agent-written text straight into HTML.

    `"&lt;script&gt;"` appearing *anywhere* on the page is not enough: the
    poisoned round 2 content is also rendered verbatim (auto-escaped, same
    as everywhere else) by the pre-existing "Merged rounds" panel, so that
    assertion alone would pass even with the diff panel's rendering block
    disabled entirely. Scope the check to the diff panel's own `<pre>` block
    so it can only pass because the diff panel itself escaped the text.
    """
    (merged / "manuscript" / "curation" / "rounds" / "2" / "results.tex").write_text(
        '<script>alert("xss")</script>\n')
    page = client.get("/runs/r1/drafts?diff_a=main:merge_1&diff_b=main:merge_2")
    assert "<script>alert" not in page.text
    start = page.text.index('<pre class="diff">')
    end = page.text.index("</pre>", start)
    diff_block = page.text[start:end]
    assert "<script>alert" not in diff_block
    assert "&lt;script&gt;" in diff_block
