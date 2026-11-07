"""T-9 t5 synthetic web fixtures: a temporary project, archives, the fake dvc and a signed-in client.

Nothing here touches the real home, the real workspace or a real remote. The `dvc` on PATH is the fake one
(tests/fixtures/b2_fake_dvc.py); its state directory is the isolated SCIEFLOW_STATE_DIR of tests/conftest.py.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scieflow.core import archives as a
from scieflow.web import auth
from scieflow.web.app import create_app
from tests.fixtures import b2_fake_dvc as fake
from tests.fixtures.b2_archives import (MD5, make_project, stat_record, write_pointer, write_record,
                                        write_zip, zip_path)

REPO_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
TOKEN = "test-token"
MEMBERS = {"status.yml": b"run:\n  id: r1\niteration: 2\n",
           "config.yml": b"agents: {}\n",
           "notes/log.md": b"<script>alert(1)</script>\n"}


def zip_bytes(members: dict[str, bytes] = MEMBERS) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def ready_archive(project, slug: str = "r1", data: bytes | None = None) -> bytes:
    """A zip whose pointer matches it and whose verification record is current: state ready."""
    data = zip_bytes() if data is None else data
    write_zip(project, slug, data)
    md5 = hashlib.md5(data).hexdigest()
    write_pointer(project, slug, md5=md5, size=len(data))
    write_record(project, slug, stat_record(zip_path(project, slug), pointer_md5=md5))
    return data


def pointer_only(project, slug: str = "r1", size: int | None = 6) -> None:
    write_pointer(project, slug, md5=MD5, size=size)


def started(project, slug: str = "r1") -> bool:
    """True once an archive job pointer exists for the slug (a job was started)."""
    return a.job_pointer_path(a.state_dir_for(project), slug).exists()


def wait_idle(project, slug: str = "r1", limit: float = 60.0) -> a.JobView:
    deadline = time.monotonic() + limit
    view = a.effective_job(project, slug)
    while view.running and time.monotonic() < deadline:
        time.sleep(0.2)
        view = a.effective_job(project, slug)
    assert not view.running, "the archive job did not finish in time"
    return view


def post(client, path: str, **form):
    """POST the way a browser form does: the CSRF token as a field, no redirect followed."""
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form}, follow_redirects=False)


@pytest.fixture
def dvc_bin(tmp_path, monkeypatch):
    bin_dir = fake.install(tmp_path.parent / f"{tmp_path.name}-dvc-bin")  # outside the project root
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return bin_dir


@pytest.fixture
def arch_project(tmp_path, dvc_bin):
    proj = make_project(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("{}\n")
    (tmp_path / "scripts").symlink_to(REPO_SCRIPTS)
    return proj


@pytest.fixture
def web(arch_project):
    """A signed-in client for the project (the session cookie and the CSRF cookie are set)."""
    with TestClient(create_app(arch_project, TOKEN)) as client:
        client.get(f"/healthz?token={TOKEN}")
        yield client


@pytest.fixture
def roomy_disk(monkeypatch):
    """The disk rule passes, so the host's real free space never decides these tests."""
    monkeypatch.setattr(a, "disk_check", lambda root, receivers: a.DiskCheck(True, []))
