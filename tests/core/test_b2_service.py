"""T-9 t4: service functions for archived runs, read side and refusals (B2:5.7, 5.8, 5.11; T9-D2, T9-D10, T9-D11).

Synthetic temporary projects only. No job is started in this module: every refusal test patches
`jobs.start` to fail, so a refusal that starts anything is caught. The read functions are checked for
no subprocess, no socket and no write (`reads_only`).
"""

import hashlib
import io
import socket
import subprocess
import time
import zipfile

import pytest
import yaml

from scieflow.core import archives as a
from scieflow.core import jobs, sandbox, service, store
from scieflow.core.project import Project
from tests.fixtures.b2_archives import (MD5, dead_pid, make_project, state_dir, stat_record,
                                        tree_snapshot, write_job, write_job_pointer,
                                        write_legacy_pointer, write_pointer, write_record,
                                        write_zip, zip_path)

MEMBERS = {"status.yml": b"run:\n  id: r1\niteration: 2\n",
           "config.yml": b"agents: {}\n",
           "budget.yml": b"limits:\n  iterations: 4\nspent:\n  iterations: 1\n",
           "notes/log.md": b"<script>alert(1)</script>\n"}


def zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def ready_archive(project: Project, slug: str = "r1", members: dict | None = None) -> bytes:
    """A zip whose pointer matches it and whose verification record is current: state ready."""
    data = zip_bytes(MEMBERS if members is None else members)
    write_zip(project, slug, data)
    md5 = hashlib.md5(data).hexdigest()
    write_pointer(project, slug, md5=md5, size=len(data))
    write_record(project, slug, stat_record(zip_path(project, slug), pointer_md5=md5))
    return data


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path)


@pytest.fixture
def reads_only(monkeypatch):
    """A read must not start a process, open a socket, or (checked by the caller) write."""
    def boom(*args, **kwargs):
        raise AssertionError("a read started a subprocess or a socket")
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


@pytest.fixture
def no_jobs(monkeypatch):
    """Any refusal that starts a job fails the test."""
    def boom(*args, **kwargs):
        raise AssertionError("a refused action started a job")
    monkeypatch.setattr(jobs, "start", boom)


@pytest.fixture
def roomy_disk(monkeypatch):
    """The disk rule passes (the host's real free space must not decide these tests)."""
    monkeypatch.setattr(a, "disk_check", lambda root, receivers: a.DiskCheck(True, []))


@pytest.fixture
def dvc_present(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/dvc")


def test_archived_runs_lists_pointers_and_states_without_writing(project, reads_only):
    ready_bytes = ready_archive(project, "r1")
    write_pointer(project, "r2", size=None)
    write_legacy_pointer(project, "r3")
    before = tree_snapshot(project.root)

    doc = service.archived_runs(project)

    rows = {row["slug"]: row for row in doc["rows"]}
    assert sorted(rows) == ["r1", "r2", "r3"]
    assert rows["r1"]["state"] == "ready" and rows["r1"]["relation"] == "archived-only"
    assert rows["r1"]["md5_12"] == hashlib.md5(ready_bytes).hexdigest()[:12]
    assert rows["r2"]["state"] == "not-downloaded"
    assert rows["r3"]["kind"] == "directory" and rows["r3"]["state"] is None
    assert rows["r3"]["command"] == "uv run scripts/dvc_sync.py pull r3"
    assert (doc["total"], doc["shown"], doc["cap"], doc["cap_note"]) == (3, 3, a.LIST_CAP, None)
    assert tree_snapshot(project.root) == before


def test_archive_detail_ready_has_summary_manuscript_and_actions(project, reads_only):
    ready_archive(project, "r1")
    detail = service.archive_detail(project, "r1")
    assert detail["state"] == "ready"
    assert detail["actions"] == ["browse", "restore"]
    assert detail["manuscript"] == (
        "Manuscript history is not kept in archives. It is rebuilt from the run's drafts after you "
        "restore the run: open its drafts page, or complete a merge round.")
    assert detail["summary"]["run"]["kind"] == "loop"
    assert detail["summary"]["run"]["iteration"] == 2
    assert detail["data_level"]["reason"] == "undeclared"
    assert detail["data_level"]["banner"] is None
    assert detail["browse_refusal"] is None
    assert set(detail["environment"]) == {"dvc", "remote_config"}


def test_archive_detail_l2_banner_is_shown(project, reads_only):
    members = dict(MEMBERS, **{"data.yml": yaml.safe_dump({"version": 1, "default_level": "L2"}).encode()})
    ready_archive(project, "r1", members=members)
    detail = service.archive_detail(project, "r1")
    assert detail["data_level"]["level"] == "L2"
    assert detail["data_level"]["banner"].startswith("This archive is declared L2 (patient-level data).")


def test_archive_detail_of_a_download_offers_download_and_disk_figures(project, reads_only, roomy_disk):
    write_pointer(project, "r2", size=12345)
    detail = service.archive_detail(project, "r2")
    assert detail["state"] == "not-downloaded"
    assert detail["actions"] == ["download"]
    assert detail["disk"]["ok"] is True
    assert {g["needed"] for g in detail["disk"]["groups"]} == set()  # the stub check returns no groups


def test_entries_and_members_on_a_ready_archive(project, reads_only):
    ready_archive(project, "r1")
    listing = service.archive_entries(project, "r1")
    assert [e["name"] for e in listing["entries"]] == ["notes", "budget.yml", "config.yml", "status.yml"]
    assert listing["reason"] is None

    view = service.archive_member(project, "r1", "status.yml")
    assert view["text"].startswith("run:") and view["complete"] is True and view["reason"] is None

    html = service.archive_member(project, "r1", "notes/log.md")
    assert html["text"] == "<script>alert(1)</script>\n"  # text is returned; escaping is the page's job

    missing = service.archive_member(project, "r1", "../status.yml")
    assert missing["text"] is None and missing["reason"] == "no-such-member"

    raw = service.archive_member(project, "r1", "status.yml", mode="raw")
    assert raw["reason"] is None
    assert raw["attachment"] is True  # no inline type for a yml member
    assert b"".join(raw["stream"]) == MEMBERS["status.yml"]


def test_entries_and_members_say_not_ready_before_download(project, reads_only):
    write_pointer(project, "r2", size=10)
    assert service.archive_entries(project, "r2")["reason"] == "not-ready"
    assert service.archive_member(project, "r2", "status.yml")["reason"] == "not-ready"


def test_unknown_or_invalid_slug_is_the_only_error_of_a_read(project):
    with pytest.raises(service.ServiceError, match="no archived run nope"):
        service.archive_detail(project, "nope")
    with pytest.raises(service.ServiceError):
        service.archive_detail(project, "../escape")


def test_an_unreadable_pointer_degrades_to_a_reason(project, reads_only):
    write_pointer(project, "r4", text="outs: [\n")
    row = {r["slug"]: r for r in service.archived_runs(project)["rows"]}["r4"]
    assert row["kind"] == "unreadable" and row["reason"] == "yaml"
    assert row["state"] is None


def test_fetch_refused_in_a_dispatch_starts_nothing(project, no_jobs, monkeypatch):
    write_pointer(project, "r1", size=10)
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1", yes=True)
    assert exc.value.reason == "in-dispatch"
    assert not (state_dir(project) / "r1.job").exists()


@pytest.mark.parametrize("call", [
    lambda p: service.restore_archive(p, "r1", yes=True),
    lambda p: service.cancel_archive_job(p, "r1"),
])
def test_restore_and_cancel_refused_in_a_dispatch(project, no_jobs, monkeypatch, call):
    ready_archive(project, "r1")
    monkeypatch.setenv("UV_CACHE_DIR", "/home/x/.cache/" + "uv")  # not the sandbox name: no refusal
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "true")
    with pytest.raises(service.ArchiveRefused) as exc:
        call(project)
    assert exc.value.reason == "in-dispatch"


def test_legacy_pointer_is_not_a_zip_download(project, no_jobs, dvc_present):
    write_legacy_pointer(project, "r3")
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r3")
    assert exc.value.reason == "not-zip-pointer"


def test_dvc_missing_is_refused_before_anything_starts(project, no_jobs, monkeypatch):
    write_pointer(project, "r1", size=10)
    monkeypatch.setenv("PATH", str(project.root / "empty-bin"))
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1", yes=True)
    assert exc.value.reason == "dvc-missing"


def test_unknown_size_needs_yes_and_says_the_command(project, no_jobs, dvc_present):
    write_pointer(project, "r1", size=None)
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1")
    assert exc.value.reason == "size-unknown"
    assert exc.value.details["command"] == "scieflow workspace archive fetch r1 --yes"
    assert "--yes" in exc.value.message


def test_a_changed_local_copy_needs_replace(project, no_jobs, dvc_present):
    write_pointer(project, "r1", size=10)
    write_zip(project, "r1", b"short")          # size differs from the pointer: incomplete
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1", yes=True)
    assert exc.value.reason == "replace-required"


def test_no_space_names_both_figures_and_starts_nothing(project, no_jobs, dvc_present, monkeypatch):
    write_pointer(project, "r1", size=10)
    groups = [{"path": str(project.root), "received": 20, "needed": 999, "free": 5}]
    monkeypatch.setattr(a, "disk_check", lambda root, receivers: a.DiskCheck(False, groups))
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1", yes=True)
    assert exc.value.reason == "no-space"
    assert exc.value.details["needed"] == 999 and exc.value.details["free"] == 5


def test_threshold_asks_for_the_exact_size(project, no_jobs, dvc_present, roomy_disk):
    write_pointer(project, "r1", size=a.CONSENT_BYTES)
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1")
    assert exc.value.reason == "confirm-required"
    assert exc.value.details["size"] == a.CONSENT_BYTES
    assert exc.value.details["command"] == "scieflow workspace archive fetch r1 --yes"


def test_a_wrong_confirmed_size_is_a_mismatch(project, no_jobs, dvc_present, roomy_disk):
    write_pointer(project, "r1", size=a.CONSENT_BYTES)
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1", confirm_size="1")
    assert exc.value.reason == "confirm-mismatch"


def test_restore_needs_a_ready_archive(project, no_jobs):
    write_pointer(project, "r1", size=10)
    with pytest.raises(service.ArchiveRefused) as exc:
        service.restore_archive(project, "r1", yes=True)
    assert exc.value.reason == "not-ready"


def test_restore_refuses_a_non_empty_run_folder(project, no_jobs, roomy_disk):
    ready_archive(project, "r1")
    (project.workspace_root / "r1").mkdir()
    (project.workspace_root / "r1" / "status.yml").write_text("kind: loop\n")
    with pytest.raises(service.ArchiveRefused) as exc:
        service.restore_archive(project, "r1", yes=True)
    assert exc.value.reason == "run-exists"


def test_a_held_start_lock_answers_locked_at_once(project, no_jobs, roomy_disk):
    ready_archive(project, "r1")
    state = a.state_dir_for(project)
    state.mkdir(parents=True, exist_ok=True)
    with store.try_locked(a.start_lock_path(state, "r1")):
        started = time.monotonic()
        with pytest.raises(service.ArchiveRefused) as exc:
            service.restore_archive(project, "r1", yes=True)
        assert time.monotonic() - started < 1.0
    assert exc.value.reason == "locked"


def test_a_held_operation_lock_is_locked_too(project, no_jobs, roomy_disk):
    ready_archive(project, "r1")
    state = a.state_dir_for(project)
    state.mkdir(parents=True, exist_ok=True)
    with store.try_locked(a.lock_path(state, "r1")):
        with pytest.raises(service.ArchiveRefused) as exc:
            service.restore_archive(project, "r1", yes=True)
    assert exc.value.reason == "locked"


def test_a_running_job_makes_a_second_action_locked(project, no_jobs, roomy_disk):
    ready_archive(project, "r1")
    write_job(project, "r1", kind="archive-restore", state="running")  # pid: this test process
    with pytest.raises(service.ArchiveRefused) as exc:
        service.restore_archive(project, "r1", yes=True)
    assert exc.value.reason == "locked"


def test_a_dead_running_job_is_lost_by_an_action_not_by_a_read(project):
    ready_archive(project, "r1")
    job_id = write_job(project, "r1", kind="archive-fetch", state="running", pid=dead_pid())
    record = project.state_dir / "jobs" / f"{job_id}.json"
    assert '"state": "running"' in record.read_text()

    before = tree_snapshot(project.state_dir)
    service.archive_detail(project, "r1")
    service.archived_runs(project)
    assert tree_snapshot(project.state_dir) == before          # reads wrote nothing, not even `lost`
    assert '"state": "running"' in record.read_text()

    result = service.cancel_archive_job(project, "r1")         # an action may write `lost`
    assert result["cancelled"] is False
    assert '"state": "lost"' in record.read_text()


def test_a_bad_job_pointer_is_shown_and_blocks_nothing(project, reads_only):
    ready_archive(project, "r1")
    write_job_pointer(project, "r1", "not a job id\n")
    assert service.archive_detail(project, "r1")["state"] == "ready"


# -- T-9 t4 fix: review r1 findings 1-3 (RED first) ----------------------------------------------

def test_replace_refusal_command_names_the_replace_flag(project, no_jobs, dvc_present, roomy_disk):
    write_pointer(project, "r1", size=None)
    zip_file = write_zip(project, "r1", b"short")         # a local copy that failed verification: "different"
    write_record(project, "r1", stat_record(zip_file, pointer_md5=MD5, outcome="failed", reason="verify-md5"))
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1", replace=True)
    assert exc.value.reason == "size-unknown"
    assert exc.value.details["command"] == "scieflow workspace archive fetch r1 --replace --yes"
    assert "fetch r1 --replace --yes" in exc.value.message


def test_replace_at_a_known_size_asks_with_the_replace_command(project, no_jobs, dvc_present, roomy_disk):
    write_pointer(project, "r1", size=10)
    write_zip(project, "r1", b"short")
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1", replace=True)
    assert exc.value.reason == "confirm-required"
    assert exc.value.details["command"] == "scieflow workspace archive fetch r1 --replace --yes"


def test_confirm_refusal_carries_disk_groups_and_destination(project, no_jobs, dvc_present, monkeypatch):
    groups = [{"path": "/x/cache", "received": 20, "needed": 21, "free": 9 << 30}]
    monkeypatch.setattr(a, "disk_check", lambda root, receivers: a.DiskCheck(True, groups))
    monkeypatch.setattr(a, "CONSENT_BYTES", 10)
    write_pointer(project, "r1", size=20)
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1")
    assert exc.value.reason == "confirm-required"
    assert exc.value.details["groups"] == groups
    assert exc.value.details["destination"] == "workspace/_archives/r1.zip"


def test_restore_confirmation_carries_unpacked_size_groups_and_destination(project, no_jobs, monkeypatch):
    ready_archive(project, "r1")
    groups = [{"path": "/x/ws", "received": 99, "needed": 104, "free": 9 << 30}]
    monkeypatch.setattr(a, "disk_check", lambda root, receivers: a.DiskCheck(True, groups))
    monkeypatch.setattr(a, "CONSENT_BYTES", 10)
    unpacked = sum(len(v) for v in MEMBERS.values())
    with pytest.raises(service.ArchiveRefused) as exc:
        service.restore_archive(project, "r1")
    assert exc.value.reason == "confirm-required"
    assert exc.value.details["size"] == unpacked
    assert exc.value.details["groups"] == groups
    assert exc.value.details["destination"] == "workspace/r1"


def test_archive_detail_restore_block_for_an_archived_only_run(project, reads_only, monkeypatch):
    ready_archive(project, "r1")
    groups = [{"path": "/x/ws", "received": 99, "needed": 104, "free": 9 << 30}]
    monkeypatch.setattr(a, "disk_check", lambda root, receivers: a.DiskCheck(True, groups))
    detail = service.archive_detail(project, "r1")
    assert detail["restore"]["unpacked"] == sum(len(v) for v in MEMBERS.values())
    assert detail["restore"]["destination"] == "workspace/r1"
    assert detail["restore"]["disk"] == {"ok": True, "groups": groups}
    (project.workspace_root / "r1").mkdir()
    (project.workspace_root / "r1" / "status.yml").write_text("kind: loop\n")
    assert service.archive_detail(project, "r1")["restore"] is None


def test_member_media_types_follow_the_web_download_rule(project, reads_only):
    from scieflow.web import files as web_files
    members = {"page.html": b"<p>x</p>", "q.svg": b"<svg/>", "s.xyz": b"1 2 3", "r.pdf": b"%PDF-1.4",
               "p.png": b"\x89PNG", "u.unknownext": b"??", "readme.txt": b"hello\n"}
    ready_archive(project, "r1", members=members)
    assert set(service.ARCHIVE_INLINE_TYPES) == set(web_files.INLINE_SAFE_TYPES)
    want = {"page.html": ("application/octet-stream", True), "q.svg": ("application/octet-stream", True),
            "s.xyz": ("application/octet-stream", True), "r.pdf": ("application/pdf", False),
            "p.png": ("image/png", False), "u.unknownext": ("application/octet-stream", True)}
    for name, (media, attachment) in want.items():
        raw = service.archive_member(project, "r1", name, mode="raw")
        assert (raw["media_type"], raw["attachment"]) == (media, attachment), name
    assert service.archive_member(project, "r1", "readme.txt", mode="raw")["text"] is True
    assert service.archive_member(project, "r1", "p.png", mode="raw")["text"] is False


def test_text_flag_follows_the_b2_text_suffixes_not_the_media_type(project, reads_only):
    from scieflow.web import files as web_files
    text_names = ["status.yml", "x.json", "e.jsonl", "run.log", "a.toml", "a.cfg", "a.ini", "a.err", "readme.txt"]
    binary_names = ["data.bin", "p.png", "r.pdf", "s.xyz"]
    ready_archive(project, "r1", members={name: b"k: v\n" for name in text_names + binary_names})
    assert set(service.ARCHIVE_TEXT_SUFFIXES) == set(web_files.TEXT_SUFFIXES)
    for name in text_names:
        assert service.archive_member(project, "r1", name, mode="raw")["text"] is True, name
    for name in binary_names:
        assert service.archive_member(project, "r1", name, mode="raw")["text"] is False, name


# -- T-9 t4 fix: review r1 finding 4 (plan section 6 t4 items that had no test) ------------------

def _dvc_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/dvc")


def _roomy(monkeypatch):
    monkeypatch.setattr(a, "disk_check", lambda root, receivers: a.DiskCheck(True, []))


def _no_dvc(monkeypatch, project):
    monkeypatch.setenv("PATH", str(project.root / "empty-bin"))


def _threshold(monkeypatch, value: int = 10):
    monkeypatch.setattr(a, "CONSENT_BYTES", value)


def _full_disk(monkeypatch):
    groups = [{"path": "/x/cache", "received": 20, "needed": 999, "free": 5}]
    monkeypatch.setattr(a, "disk_check", lambda root, receivers: a.DiskCheck(False, groups))


def _refusal_setups():
    """(name, setup(project, monkeypatch), call(project), reason): every refusal of B2:5.7 rules 1-5."""
    def fetch(**kw):
        return lambda p: service.fetch_archive(p, "r1", **kw)

    def restore(**kw):
        return lambda p: service.restore_archive(p, "r1", **kw)

    def dvc_missing(p, mp):
        write_pointer(p, "r1", size=10)
        _no_dvc(mp, p)

    def size_unknown(p, mp):
        write_pointer(p, "r1", size=None)
        _dvc_on_path(mp)

    def replace_required(p, mp):
        write_pointer(p, "r1", size=10)
        write_zip(p, "r1", b"short")
        _dvc_on_path(mp)

    def no_space(p, mp):
        write_pointer(p, "r1", size=10)
        _dvc_on_path(mp)
        _full_disk(mp)

    def confirm_required(p, mp):
        write_pointer(p, "r1", size=20)
        _dvc_on_path(mp)
        _roomy(mp)
        _threshold(mp)

    def confirm_mismatch(p, mp):
        confirm_required(p, mp)

    def not_ready(p, mp):
        write_pointer(p, "r1", size=10)

    def run_exists(p, mp):
        ready_archive(p, "r1")
        (p.workspace_root / "r1").mkdir()
        (p.workspace_root / "r1" / "status.yml").write_text("kind: loop\n")
        _roomy(mp)

    def in_dispatch(p, mp):
        ready_archive(p, "r1")
        mp.setenv("SCIEFLOW_DISPATCH", "1")

    return [
        ("not-zip-pointer", lambda p, mp: write_legacy_pointer(p, "r1"), fetch(yes=True), "not-zip-pointer"),
        ("dvc-missing", dvc_missing, fetch(yes=True), "dvc-missing"),
        ("size-unknown", size_unknown, fetch(), "size-unknown"),
        ("replace-required", replace_required, fetch(yes=True), "replace-required"),
        ("no-space", no_space, fetch(yes=True), "no-space"),
        ("confirm-required", confirm_required, fetch(), "confirm-required"),
        ("confirm-mismatch", confirm_mismatch, fetch(confirm_size="1"), "confirm-mismatch"),
        ("not-ready", not_ready, restore(yes=True), "not-ready"),
        ("run-exists", run_exists, restore(yes=True), "run-exists"),
        ("in-dispatch", in_dispatch, restore(yes=True), "in-dispatch"),
    ]


@pytest.mark.parametrize("name, setup, call, reason", _refusal_setups(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_refusal_starts_nothing_and_writes_no_job(project, no_jobs, monkeypatch, name, setup, call, reason):
    setup(project, monkeypatch)
    with pytest.raises(service.ArchiveRefused) as exc:
        call(project)
    assert exc.value.reason == reason
    assert not (state_dir(project) / "r1.job").exists()
    jobs_dir = project.state_dir / "jobs"
    assert not jobs_dir.exists() or not list(jobs_dir.glob("*.json"))
    assert not zip_path(project, "r1").with_suffix(".zip.partial").exists()


def test_one_byte_below_the_threshold_is_not_asked(project, dvc_present, roomy_disk, monkeypatch):
    class Reached(Exception):
        """The call got past every refusal and would start the job."""
    write_pointer(project, "r1", size=a.CONSENT_BYTES - 1)

    def reached(*args, **kwargs):
        raise Reached()
    monkeypatch.setattr(jobs, "start", reached)
    with pytest.raises(Reached):
        service.fetch_archive(project, "r1")


def test_replace_needs_consent_even_for_a_small_copy(project, dvc_present, roomy_disk, monkeypatch):
    class Reached(Exception):
        """The call got past every refusal and would start the job."""
    write_pointer(project, "r1", size=10)
    write_zip(project, "r1", b"short")
    with pytest.raises(service.ArchiveRefused) as exc:
        service.fetch_archive(project, "r1", replace=True)
    assert exc.value.reason == "confirm-required"

    monkeypatch.setattr(jobs, "start", lambda *args, **kwargs: (_ for _ in ()).throw(Reached()))
    with pytest.raises(Reached):
        service.fetch_archive(project, "r1", replace=True, confirm_size="10")


@pytest.mark.parametrize("call", [
    lambda p: service.fetch_archive(p, "r1", yes=True),
    lambda p: service.restore_archive(p, "r1", yes=True),
    lambda p: service.cancel_archive_job(p, "r1"),
])
def test_actions_refused_by_the_sandbox_cache_dir_heuristic(project, no_jobs, roomy_disk, dvc_present,
                                                           monkeypatch, call):
    ready_archive(project, "r1")
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.setenv("UV_CACHE_DIR", "/home/ubuntu/cache/" + sandbox.CACHE_DIRNAME)
    with pytest.raises(service.ArchiveRefused) as exc:
        call(project)
    assert exc.value.reason == "in-dispatch"
    assert not (state_dir(project) / "r1.job").exists()


def test_gets_never_reconcile_find_list_or_lock_and_change_nothing(project, monkeypatch):
    ready_archive(project, "r1")
    from tests.fixtures.b2_archives import write_job
    job_id = write_job(project, "r1", kind="archive-fetch", state="running", pid=dead_pid())

    def boom(*args, **kwargs):
        raise AssertionError("a read started a process, opened a socket, reconciled, searched, listed or locked")
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    for owner, name in ((jobs, "reconcile"), (jobs, "find"), (jobs, "list_jobs"), (store, "try_locked")):
        monkeypatch.setattr(owner, name, boom)

    before = (tree_snapshot(project.root), tree_snapshot(project.state_dir))
    service.archived_runs(project)
    service.archive_detail(project, "r1")
    service.archive_entries(project, "r1")
    service.archive_entries(project, "r1", "notes")
    service.archive_member(project, "r1", "status.yml")
    service.archive_member(project, "r1", "status.yml", mode="raw")
    assert (tree_snapshot(project.root), tree_snapshot(project.state_dir)) == before
    assert '"state": "running"' in (project.state_dir / "jobs" / f"{job_id}.json").read_text()
