"""T-9 t5 (5.11 item 7; the human UI rule of 2026-11-03): no browser dialogs, no scripts, no raw HTML filters.

Visual sibling matching is checked by reviewers from the template diff; these tests pin the mechanical part.
"""

from pathlib import Path

from tests.fixtures.b2_web import arch_project, dvc_bin, pointer_only, web  # noqa: F401

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "scieflow" / "web" / "templates"
NEW_AND_CHANGED = ("archive.html", "archive_files.html", "archive_file.html", "dashboard.html")
FORBIDDEN = ("alert(", "confirm(", "prompt(", "<dialog", "<script", "|safe")


def test_no_dialog_script_or_raw_html_in_the_archive_templates():
    for name in NEW_AND_CHANGED:
        text = (TEMPLATES / name).read_text()
        for token in FORBIDDEN:
            assert token not in text, f"{name} contains {token!r}"


def test_archive_actions_reuse_the_run_page_form_wrapper(arch_project, web):
    pointer_only(arch_project, "r1")
    text = (TEMPLATES / "archive.html").read_text()
    assert 'class="actions"' in text
    assert "<button" in text
    body = web.get("/archives/r1").text
    assert '<form method="post" action="/archives/r1/fetch" class="actions">' in body


def test_member_view_uses_the_log_panel_of_the_run_file_page(arch_project, web):
    """T-9 t5 r1 finding 5: archive_file.html reuses the sibling file.html's `pre.log` panel."""
    from tests.fixtures.b2_web import ready_archive
    ready_archive(arch_project, "r1")
    body = web.get("/archives/r1/file", params={"path": "notes/log.md"}).text
    assert '<pre class="log">' in body
