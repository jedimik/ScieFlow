"""T-9 final fix (final review r1, finding 1 and 3): the archive pages say what B2 says for a legacy
directory pointer, the environment lines, the leftovers and the browse refusals (B2:5.2, 5.7.5, 5.7.6, 5.10).
"""

import html

from scieflow.core import archives as a
from tests.fixtures.b2_archives import write_legacy_pointer
from tests.fixtures.b2_web import arch_project, dvc_bin, pointer_only, ready_archive, web  # noqa: F401

PARTIAL = ("an interrupted archive build left this file; it is not an archive and nothing reads it; "
           "delete it when no push is running")


def page(web, path: str) -> str:
    return html.unescape(web.get(path).text)


def test_legacy_pointer_page_shows_its_file_count_and_pull_command(arch_project, web):
    write_legacy_pointer(arch_project, "old", nfiles=4321)
    body = page(web, "/archives/old")
    assert "4321 files" in body
    assert "uv run scripts/dvc_sync.py pull old" in body


def test_dashboard_row_of_a_legacy_pointer_shows_its_count_and_command(arch_project, web):
    write_legacy_pointer(arch_project, "old", nfiles=4321)
    body = page(web, "/")
    assert "4321 files" in body
    assert "uv run scripts/dvc_sync.py pull old" in body


def test_partial_leftover_states_its_b2_sentence(arch_project, web):
    pointer_only(arch_project, "r1")
    (arch_project.workspace_root / "_archives" / "r1.zip.partial").write_bytes(b"x")
    body = page(web, "/archives/r1")
    assert "r1.zip.partial" in body and PARTIAL in body


def test_link_extract_and_replaced_leftovers_state_their_sentences(arch_project, web):
    pointer_only(arch_project, "r1")
    (arch_project.workspace_root / "_archives" / "r1.zip.link").write_text("x")
    (arch_project.workspace_root / ".r1.extract-abc").mkdir()
    (arch_project.workspace_root / ".r1.replaced-abc").mkdir()
    body = page(web, "/archives/r1")
    assert "an interrupted cache link" in body
    assert body.count("an interrupted restore or forced pull") == 2
    assert body.count("not the run; delete it") == 2


def test_environment_lines_use_the_b2_wording(arch_project, web):
    pointer_only(arch_project, "r1")
    body = page(web, "/archives/r1")
    assert "(docs/DVC_STORAGE.md §1)" in body
    assert "DVC remote configuration in this checkout:" in body
    assert "(.dvc/config is machine-local; docs/DVC_STORAGE.md §2)" in body


def test_too_many_entries_browse_refusal_is_shown_with_its_hint(arch_project, web, monkeypatch):
    monkeypatch.setattr(a, "MAX_ARCHIVE_ENTRIES", 2)
    ready_archive(arch_project, "r1")                     # three members, the bound is two
    hint = "The archive has more entries than ScieFlow reads: restore it from the terminal to look inside."
    assert hint in page(web, "/archives/r1")
    assert hint in page(web, "/archives/r1/files")


def test_unreadable_zip_browse_refusal_has_its_own_sentence(arch_project, web):
    # A run directory exists, so the restore panel is absent and only the browse refusal can say it.
    (arch_project.root / "workspace" / "r1").mkdir()
    ready_archive(arch_project, "r1", data=b"this is not a zip file at all")
    body = page(web, "/archives/r1")
    assert "The archive is not a readable zip file" in body
    assert "The pointer file cannot be read" not in body
    files = page(web, "/archives/r1/files")
    assert "not a readable zip file" in files
    assert "The pointer file cannot be read" not in files
