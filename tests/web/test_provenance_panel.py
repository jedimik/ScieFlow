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
