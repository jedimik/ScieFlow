"""T-9 t5: the archive page (B2:5.5 actions per state, 5.7 facts, the manuscript sentence, the L2 banner)."""

import hashlib
import html
import os
import sys

import yaml

from scieflow.core import archives as a
from tests.fixtures.b2_archives import stat_record, write_job, write_pointer, write_record, zip_path
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


def _actions_of(body: str) -> str:
    """The Actions section only (B2:5.5 actions per state)."""
    return body.split("<h2>Actions</h2>")[1].split("<h2>")[0]


def test_different_state_offers_only_download_again(arch_project, web):
    """B2:5.5: a failed MD5 verification (`different`) offers Download again (replace) and nothing else."""
    data = ready_archive(arch_project, "r1")
    md5 = hashlib.md5(data).hexdigest()
    write_record(arch_project, "r1", stat_record(zip_path(arch_project, "r1"), pointer_md5=md5,
                                                 outcome="failed", reason="verify-md5"))
    body = web.get("/archives/r1").text
    actions = _actions_of(body)
    assert "different" in body
    assert "Download again (replace)" in actions
    assert '<input type="hidden" name="replace" value="1">' in actions
    assert "Verify" not in actions and "Cancel" not in actions


def test_downloading_state_offers_only_cancel(arch_project, web):
    pointer_only(arch_project, "r1")
    write_job(arch_project, "r1", kind="archive-fetch", state="running", pid=os.getpid())
    body = web.get("/archives/r1").text
    actions = _actions_of(body)
    assert "downloading" in body
    assert 'action="/archives/r1/cancel"' in actions
    assert "Download" not in actions and "Verify" not in actions


def test_verifying_state_offers_only_cancel(arch_project, web):
    pointer_only(arch_project, "r1")
    write_job(arch_project, "r1", kind="archive-fetch", state="running", pid=os.getpid(),
              argv=[sys.executable, "-B", "scripts/dvc_sync.py", "fetch", "r1", "--verify-only"])
    body = web.get("/archives/r1").text
    actions = _actions_of(body)
    assert "verifying" in body
    assert 'action="/archives/r1/cancel"' in actions
    assert "Verify" not in actions and "Download" not in actions


def test_restoring_state_offers_only_cancel_and_no_restore_form(arch_project, web):
    ready_archive(arch_project, "r1")                  # archived-only, so Restore would otherwise be offered
    write_job(arch_project, "r1", kind="archive-restore", state="running", pid=os.getpid())
    body = web.get("/archives/r1").text
    actions = _actions_of(body)
    assert "restoring" in body
    assert 'action="/archives/r1/cancel"' in actions
    assert 'action="/archives/r1/restore"' not in body


def test_also_local_ready_archive_offers_browse_but_no_restore(arch_project, web):
    ready_archive(arch_project, "r1")
    (arch_project.workspace_root / "r1").mkdir()
    body = web.get("/archives/r1").text
    assert '<span class="dim">relation</span> also-local' in body
    assert "Browse files" in body
    assert 'action="/archives/r1/restore"' not in body


def test_alias_ready_archive_offers_browse_but_no_restore(arch_project, web, tmp_path):
    ready_archive(arch_project, "r1")
    target = tmp_path / "elsewhere-run"
    target.mkdir()
    (arch_project.workspace_root / "r1").symlink_to(target)
    body = web.get("/archives/r1").text
    assert '<span class="dim">relation</span> alias' in body
    assert "Browse files" in body
    assert 'action="/archives/r1/restore"' not in body


def test_zip_symlink_outside_the_dvc_cache_is_incomplete_with_its_note(arch_project, web, tmp_path):
    """B2:5.5 symlinked zip: a link outside .dvc/cache is never opened; the page says so and offers replace."""
    data = zip_bytes()
    outside = tmp_path / "outside.zip"
    outside.write_bytes(data)
    zip_path(arch_project, "r1").symlink_to(outside)
    pointer_only(arch_project, "r1", size=len(data))
    body = web.get("/archives/r1").text
    assert "incomplete" in body
    assert "the local zip is a link outside the DVC cache" in body
    assert "Download again (replace)" in _actions_of(body)


def test_budget_remaining_and_member_totals_render(arch_project, web):
    """T-9 t5 r2 finding 1: a budget.yml member pins the 'budget remaining' percentages and the member count."""
    budget = yaml.safe_dump({"budgets": {"max_iterations": 10, "max_experiment_runs": 5, "max_wall_minutes": 100},
                             "spent": {"iterations": 5, "experiment_runs": 0, "wall_minutes": 25}}).encode()
    ready_archive(arch_project, "r1", zip_bytes({"status.yml": b"run:\n  id: r1\niteration: 2\n",
                                                 "budget.yml": budget, "notes/log.md": b"hello\n"}))
    body = html.unescape(web.get("/archives/r1").text)
    block = body.split("<h2>Run summary</h2>")[1].split("<h2>Environment</h2>")[0]
    assert "iterations 50%" in block
    assert "wall minutes 75%" in block
    assert '<span class="dim">members</span> 3' in block
    assert "Traceback" not in body
