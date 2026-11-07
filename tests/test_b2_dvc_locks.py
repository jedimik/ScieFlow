"""T-9 t3: the operation lock of dvc_sync (B2:5.8, T9-D5, T8-D14).

A helper subprocess holds the very lock file store.try_locked takes, so the lock is a real cross-process
lock. Synthetic zips, temporary projects, a fake `dvc` on PATH; the real dvc is never run.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

import dvc_sync
from scieflow.core import archives as arch
from tests.fixtures import b2_fake_dvc as fake

HOLD_CODE = (
    "import sys\n"
    "from filelock import FileLock\n"
    "lock = FileLock(sys.argv[1])\n"
    "lock.acquire()\n"
    "print('held', flush=True)\n"
    "sys.stdin.readline()\n"
)


@contextmanager
def held(lock_file: Path):
    """Hold `lock_file` (the exact file store.try_locked locks) in another process."""
    proc = subprocess.Popen([sys.executable, "-c", HOLD_CODE, str(lock_file)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        yield
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "workspace" / "_archives").mkdir(parents=True)
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(tmp_path / "state"))
    return root


@pytest.fixture
def state(proj):
    path = Path(os.environ["SCIEFLOW_STATE_DIR"])
    (path / "archives").mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def dvc_bin(tmp_path, monkeypatch):
    bin_dir = fake.install(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


@pytest.fixture
def calls(monkeypatch):
    """Record dvc argv for push/pull instead of running it; every command succeeds."""
    recorded = []

    def fake_run_cmd(cmd, cwd):
        recorded.append(cmd)
        return 0

    monkeypatch.setattr(dvc_sync, "run_cmd", fake_run_cmd)
    return recorded


def zip_bytes(tmp: Path) -> bytes:
    path = tmp / "synthetic.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("status.yml", "phase: done\n")
    return path.read_bytes()


def write_pointer(root: Path, slug: str, data: bytes) -> None:
    doc = {"outs": [{"md5": hashlib.md5(data).hexdigest(), "size": len(data),
                     "path": f"{slug}.zip", "hash": "md5"}]}
    (root / "workspace" / "_archives" / f"{slug}.zip.dvc").write_text(yaml.safe_dump(doc))


def make_run(workspace: Path, slug: str) -> None:
    run = workspace / slug
    (run / "logs").mkdir(parents=True)
    (run / "status.yml").write_text("phase: done\n")


def test_fetch_on_a_held_slug_refuses_and_writes_nothing(proj, state, dvc_bin, tmp_path, capsys):
    data = zip_bytes(tmp_path)
    write_pointer(proj, "r1", data)
    fake.set_script(dvc_bin, content=data)

    with held(state / "archives" / "r1.lock"):
        rc = dvc_sync.cmd_fetch("r1", proj, proj / "workspace", state)

    assert rc == arch.LOCKED_EXIT
    assert arch.sentence("locked") in capsys.readouterr().out
    assert fake.argv(dvc_bin) == []
    assert not (proj / "workspace" / "_archives" / "r1.zip").exists()
    assert not (state / "archives" / "r1.json").exists()


def test_restore_on_a_held_slug_refuses_and_changes_nothing(proj, state, dvc_bin, tmp_path):
    data = zip_bytes(tmp_path)
    write_pointer(proj, "r1", data)
    fake.set_script(dvc_bin, content=data)
    assert dvc_sync.cmd_fetch("r1", proj, proj / "workspace", state) == 0
    record = (state / "archives" / "r1.json").read_text()

    with held(state / "archives" / "r1.lock"):
        rc = dvc_sync.cmd_restore("r1", proj, proj / "workspace", state)

    assert rc == arch.LOCKED_EXIT
    assert not (proj / "workspace" / "r1").exists()
    assert (state / "archives" / "r1.json").read_text() == record


def test_push_skips_a_held_slug_and_pushes_the_others(proj, state, calls, capsys):
    ws = proj / "workspace"
    make_run(ws, "A")
    make_run(ws, "B")

    with held(state / "archives" / "A.lock"):
        rc = dvc_sync.cmd_push(["A", "B"], proj, ws, archive_flag=True, state_dir=state)

    assert rc == arch.LOCKED_EXIT
    assert "A: " + arch.sentence("locked") in capsys.readouterr().out
    assert not (ws / "_archives" / "A.zip").exists()
    assert not any("A.zip" in part for cmd in calls for part in cmd)
    assert any(cmd[:3] == ["dvc", "add", "--to-remote"] and cmd[-1].endswith("B.zip") for cmd in calls)
    assert not (ws / "_archives" / "B.zip").exists()  # built, uploaded, removed as before


def test_pull_skips_a_held_slug_and_pulls_the_others(proj, state, calls, tmp_path):
    ws = proj / "workspace"
    data = zip_bytes(tmp_path)
    write_pointer(proj, "A", data)
    write_pointer(proj, "B", data)
    (ws / "_archives" / "B.zip").write_bytes(data)

    with held(state / "archives" / "A.lock"):
        rc = dvc_sync.cmd_pull(["A", "B"], proj, ws, state_dir=state)

    assert rc == arch.LOCKED_EXIT
    assert calls == [["dvc", "pull", "workspace/_archives/B.zip.dvc"]]
    assert (ws / "B" / "status.yml").exists()
    assert not (ws / "A").exists()


def test_directory_mode_takes_no_lock(proj, state, calls):
    ws = proj / "workspace"
    (ws / "D").mkdir()
    (ws / "D.dvc").write_text("outs: []\n")

    with held(state / "archives" / "D.lock"):
        rc = dvc_sync.cmd_pull(["D"], proj, ws, state_dir=state)

    assert rc == 0
    assert calls == [["dvc", "pull", "workspace/D.dvc"]]


def test_main_uses_the_state_dir_it_is_given(proj, state, dvc_bin, tmp_path, monkeypatch):
    data = zip_bytes(tmp_path)
    write_pointer(proj, "r1", data)
    fake.set_script(dvc_bin, content=data)
    alt = tmp_path / "alt-state"
    (alt / "archives").mkdir(parents=True)
    monkeypatch.setattr(dvc_sync.config, "repo_root", lambda *a, **k: proj)
    monkeypatch.setattr(sys, "argv", ["dvc_sync.py", "fetch", "r1", "--state-dir", str(alt)])

    with held(alt / "archives" / "r1.lock"):
        with pytest.raises(SystemExit) as exc:
            dvc_sync.main()

    assert exc.value.code == arch.LOCKED_EXIT
    assert fake.argv(dvc_bin) == []


def test_main_defaults_the_state_dir_to_the_project_state(proj, state, dvc_bin, tmp_path, monkeypatch):
    data = zip_bytes(tmp_path)
    write_pointer(proj, "r1", data)
    fake.set_script(dvc_bin, content=data)
    monkeypatch.setattr(dvc_sync.config, "repo_root", lambda *a, **k: proj)
    monkeypatch.setattr(sys, "argv", ["dvc_sync.py", "fetch", "r1"])

    with pytest.raises(SystemExit) as exc:
        dvc_sync.main()

    assert exc.value.code == 0
    assert (state / "archives" / "r1.json").exists()
