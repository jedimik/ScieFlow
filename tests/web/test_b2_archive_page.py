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
