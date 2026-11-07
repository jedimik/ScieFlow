"""T-9 t5: the archive page (B2:5.5 actions per state, 5.7 facts, the manuscript sentence, the L2 banner)."""

import html

import yaml

from scieflow.core import archives as a
from tests.fixtures.b2_archives import write_pointer
from tests.fixtures.b2_web import (MEMBERS, arch_project, dvc_bin, pointer_only, ready_archive,  # noqa: F401
                                   web, zip_bytes)


def test_not_downloaded_page_shows_state_environment_and_download(arch_project, web, monkeypatch, tmp_path):
    empty = tmp_path / "no-programs"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))          # no dvc on this machine
    pointer_only(arch_project, "r1", size=6)
    r = web.get("/archives/r1")
    assert r.status_code == 200
    assert "not-downloaded" in r.text
    assert "not installed" in r.text                 # dvc
    assert "not found" in r.text                     # DVC remote configuration (advisory)
    assert 'action="/archives/r1/fetch"' in r.text


def test_manuscript_sentence_and_no_history_panel(arch_project, web):
    pointer_only(arch_project, "r1")
    body = html.unescape(web.get("/archives/r1").text)
    assert body.count("Manuscript history is not kept in archives.") == 1
    assert "<h2>History" not in body


def test_ready_archive_summary_and_browse_link(arch_project, web):
    ready_archive(arch_project, "r1")
    body = web.get("/archives/r1").text
    assert "ready" in body
    assert "loop" in body                             # the run summary, from status.yml
    assert 'href="/archives/r1/files"' in body        # Browse files
    assert "Browse files" in body


def test_l2_archive_shows_the_banner(arch_project, web):
    data_yml = yaml.safe_dump({"version": 1, "default_level": "L2", "levels": [],
                               "pending_releases": [], "released": [], "imports": []}).encode()
    ready_archive(arch_project, "r1", zip_bytes({**MEMBERS, "data.yml": data_yml}))
    body = html.unescape(web.get("/archives/r1").text)
    assert "This archive is declared L2 (patient-level data)." in body
    assert "ScieFlow sends nothing from an archive to a model or a remote." in body


def test_archived_only_ready_archive_shows_the_restore_panel(arch_project, web):
    ready_archive(arch_project, "r1")               # no local workspace/r1: archived-only
    body = web.get("/archives/r1").text
    assert 'action="/archives/r1/restore"' in body
    assert "workspace/r1" in body


def test_unknown_slug_is_404(web):
    assert web.get("/archives/no-such-run").status_code == 404


def _summary_block(body: str) -> str:
    return body.split("<h2>Run summary</h2>")[1].split("<h2>Environment</h2>")[0]


def test_header_shows_pointer_kind_exact_bytes_and_md5_prefix(arch_project, web):
    """T-9 t5 r1 finding 3 (B2:5.7.1)."""
    import hashlib
    data = ready_archive(arch_project, "r1")
    body = html.unescape(web.get("/archives/r1").text)
    assert '<span class="dim">pointer</span> zip' in body
    assert f"{len(data)} bytes" in body
    assert f'<span class="dim">md5</span> {hashlib.md5(data).hexdigest()[:12]}' in body


def test_summary_names_its_fields_and_renders_no_raw_dict(arch_project, web):
    """T-9 t5 r1 finding 3 (B2:5.7.3): named fields in words; no Python None, [] or dict dump."""
    ready_archive(arch_project, "r1")
    block = _summary_block(html.unescape(web.get("/archives/r1").text))
    for label in ("kind", "state", "phase", "iteration", "stopped reason", "lineage", "budget remaining",
                  "packed", "members", "unpacked"):
        assert label in block, label
    assert "None" not in block and "[]" not in block and "{" not in block
    assert "not recorded" in block                             # a missing value reads as words
    assert "not in this archive" in block                      # budget.yml is absent from MEMBERS
    assert f'<span class="dim">members</span> {len(MEMBERS)}' in block


def test_unverified_state_offers_verify(arch_project, web):
    """T-9 t5 r1 coverage: a zip present without its verification record is `unverified`."""
    import hashlib
    from tests.fixtures.b2_archives import write_zip
    data = zip_bytes()
    write_zip(arch_project, "r1", data)
    write_pointer(arch_project, "r1", md5=hashlib.md5(data).hexdigest(), size=len(data))
    body = web.get("/archives/r1").text
    assert "unverified" in body
    assert "<button>Verify</button>" in body
