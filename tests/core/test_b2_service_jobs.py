"""T-9 t4: the job side of the service, with the real scripts/dvc_sync.py and the fake dvc (B2:5.8, T9-D2..D5).

Each test runs in a temporary project whose scripts/ is a link to this checkout's scripts/, and whose
config/agents.yml makes the job's own `repo_root()` resolve to that project. The real dvc is never run:
a fake executable placed first on PATH by the fixture records its argv and writes the bytes the test
chooses. Nothing touches the network, a remote, the real home or the RUN layout.
"""

import hashlib
import io
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

from scieflow.core import archives as a
from scieflow.core import service
from tests.fixtures import b2_fake_dvc as fake
from tests.fixtures.b2_archives import (dead_pid, make_project, stat_record, write_job, write_pointer,
                                        write_record, write_zip, zip_path)

REPO_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
MEMBERS = {"status.yml": b"kind: loop\nstate: running\nphase: p1\niteration: 2\n",
           "config.yml": b"agents: {}\n"}


def zip_bytes(members: dict[str, bytes] = MEMBERS) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def pointer_for(project, slug: str, data: bytes) -> str:
    md5 = hashlib.md5(data).hexdigest()
    write_pointer(project, slug, md5=md5, size=len(data))
    return md5


def wait_until_idle(project, slug: str, limit: float = 60.0) -> a.JobView:
    """Bounded wait for the slug's archive job to finish (tests only)."""
    deadline = time.monotonic() + limit
    view = a.effective_job(project, slug)
    while view.running and time.monotonic() < deadline:
        time.sleep(0.2)
        view = a.effective_job(project, slug)
    assert not view.running, "the archive job did not finish in time"
    return view


@pytest.fixture
def dvc_bin(tmp_path, monkeypatch):
    bin_dir = fake.install(tmp_path.parent / f"{tmp_path.name}-dvc-bin")  # outside the project root
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return bin_dir


@pytest.fixture
def project(tmp_path, dvc_bin):
    proj = make_project(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("{}\n")
    (tmp_path / "scripts").symlink_to(REPO_SCRIPTS)
    return proj


def test_fetch_downloads_and_verifies_without_unpacking(project, dvc_bin):
    data = zip_bytes()
    pointer_for(project, "r1", data)
    fake.set_script(dvc_bin, content=data)

    doc = service.fetch_archive(project, "r1", wait=True)

    assert doc["started"] is True and doc["action"] == "fetch"
    assert doc["outcome"] == "ready" and doc["reason"] is None and doc["state"] == "ready"
    assert doc["message"] == "Downloaded and verified."
    assert zip_path(project, "r1").read_bytes() == data
    assert not (project.workspace_root / "r1").exists()          # never unpacked
    assert fake.argv(dvc_bin) == [["pull", "workspace/_archives/r1.zip.dvc"]]


def test_a_failed_download_shows_the_sentence_never_dvc_output(project, dvc_bin):
    data = zip_bytes()
    pointer_for(project, "r1", data)
    fake.set_script(dvc_bin, exit_code=1, stderr=["ERROR: NoCredentialsError: Unable to locate credentials"])

    doc = service.fetch_archive(project, "r1", wait=True)

    assert doc["outcome"] == "failed:no-credentials" and doc["reason"] == "no-credentials"
    assert doc["message"] == a.sentence("no-credentials")
    assert "NoCredentialsError" not in doc["message"]
    assert doc["log"]                                              # the job log is named, not shown


def test_an_unverified_copy_is_verified_without_dvc(project, dvc_bin):
    data = zip_bytes()
    pointer_for(project, "r1", data)
    write_zip(project, "r1", data)                                 # no verification record yet

    doc = service.fetch_archive(project, "r1", wait=True)

    assert doc["action"] == "verify" and doc["outcome"] == "ready"
    assert doc["message"] == "Verified."
    assert fake.argv(dvc_bin) == []                                # no network, dvc never runs


def test_without_wait_the_call_returns_at_once_and_the_job_finishes(project, dvc_bin):
    data = zip_bytes()
    pointer_for(project, "r1", data)
    fake.set_script(dvc_bin, content=data, sleep=0.5)

    doc = service.fetch_archive(project, "r1")

    assert doc["started"] is True and doc["job_id"] and doc["outcome"] is None
    assert wait_until_idle(project, "r1").outcome == "ready"


def test_two_concurrent_requests_start_one_job(project, dvc_bin):
    data = zip_bytes()
    pointer_for(project, "r1", data)
    fake.set_script(dvc_bin, content=data, sleep=1.5)
    started, refused = [], []

    def call():
        try:
            started.append(service.fetch_archive(project, "r1"))
        except service.ArchiveRefused as exc:
            refused.append(exc.reason)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(started) == 1 and refused == ["locked"]
    assert len(list((project.state_dir / "jobs").glob("*.json"))) == 1
    wait_until_idle(project, "r1")


def test_cancel_stops_a_running_download(project, dvc_bin):
    data = zip_bytes()
    pointer_for(project, "r1", data)
    fake.set_script(dvc_bin, content=data, sleep=30)
    service.fetch_archive(project, "r1")
    assert a.effective_job(project, "r1").running

    result = service.cancel_archive_job(project, "r1")

    assert result["cancelled"] is True
    view = wait_until_idle(project, "r1")
    assert view.outcome == "interrupted"


def test_restore_makes_the_run_local(project, dvc_bin):
    data = zip_bytes()
    md5 = pointer_for(project, "r1", data)
    write_zip(project, "r1", data)
    write_record(project, "r1", stat_record(zip_path(project, "r1"), pointer_md5=md5))

    doc = service.restore_archive(project, "r1", wait=True)

    assert doc["outcome"] == "restored" and doc["state"] == "ready"
    assert doc["message"] == "Restored into workspace; the run is now local."
    assert (project.workspace_root / "r1" / "status.yml").read_bytes() == MEMBERS["status.yml"]
    rows = {row["slug"]: row for row in service.archived_runs(project)["rows"]}
    assert rows["r1"]["relation"] == "also-local"
    assert "r1" in [run["slug"] for run in service.list_runs_with_budget(project)]


def test_restore_accepts_an_empty_run_folder(project, dvc_bin):
    """B2:5.8 and W2: restore unpacks into an absent or EMPTY workspace/<slug>."""
    data = zip_bytes()
    md5 = pointer_for(project, "r1", data)
    write_zip(project, "r1", data)
    write_record(project, "r1", stat_record(zip_path(project, "r1"), pointer_md5=md5))
    (project.workspace_root / "r1").mkdir()

    doc = service.restore_archive(project, "r1", wait=True)

    assert doc["outcome"] == "restored"
    assert (project.workspace_root / "r1" / "status.yml").read_bytes() == MEMBERS["status.yml"]


def test_a_dead_running_job_is_lost_by_the_next_fetch(project, dvc_bin):
    data = zip_bytes()
    pointer_for(project, "r1", data)
    job_id = write_job(project, "r1", kind="archive-fetch", state="running", pid=dead_pid())
    fake.set_script(dvc_bin, content=data)

    doc = service.fetch_archive(project, "r1", wait=True)

    assert doc["outcome"] == "ready"
    assert '"state": "lost"' in (project.state_dir / "jobs" / f"{job_id}.json").read_text()


def test_a_dead_running_job_is_lost_by_the_next_restore(project, dvc_bin):
    data = zip_bytes()
    md5 = pointer_for(project, "r1", data)
    write_zip(project, "r1", data)
    write_record(project, "r1", stat_record(zip_path(project, "r1"), pointer_md5=md5))
    job_id = write_job(project, "r1", kind="archive-restore", state="running", pid=dead_pid())

    doc = service.restore_archive(project, "r1", wait=True)

    assert doc["outcome"] == "restored"
    assert '"state": "lost"' in (project.state_dir / "jobs" / f"{job_id}.json").read_text()
