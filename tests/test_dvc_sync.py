from pathlib import Path
import os
from types import SimpleNamespace

import pytest
import yaml

import dvc_sync
import dvc_setup_s3
from sflib import archive


def test_find_workspaces(tmp_path):
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    (ws_dir / "run-01").mkdir()
    (ws_dir / "run-02").mkdir()
    (ws_dir / ".hidden").mkdir()
    (ws_dir / "README.md").write_text("info")

    slugs = dvc_sync.find_workspaces(ws_dir)
    assert slugs == ["run-01", "run-02"]


def test_resolve_slugs_all(tmp_path):
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    (ws_dir / "run-01").mkdir()
    (ws_dir / "run-02").mkdir()

    assert dvc_sync.resolve_slugs([], True, ws_dir) == ["run-01", "run-02"]


def test_resolve_slugs_specific(tmp_path):
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    (ws_dir / "run-01").mkdir()

    assert dvc_sync.resolve_slugs(["run-01"], False, ws_dir) == ["run-01"]
    assert dvc_sync.resolve_slugs(["workspace/run-01"], False, ws_dir) == ["run-01"]
    with pytest.raises(FileNotFoundError):
        dvc_sync.resolve_slugs(["non-existent"], False, ws_dir)


def test_normalize_s3_url():
    url, ep = dvc_setup_s3.normalize_s3_url("s3://bucket/path")
    assert url == "s3://bucket/path" and ep is None

    url, ep = dvc_setup_s3.normalize_s3_url("s3.cl4.du.cesnet.cz://dvc-projects/scieflow")
    assert url == "s3://dvc-projects/scieflow"
    assert ep == "https://s3.cl4.du.cesnet.cz"

    url, ep = dvc_setup_s3.normalize_s3_url("https://s3.cl4.du.cesnet.cz/dvc-projects/scieflow")
    assert url == "s3://dvc-projects/scieflow"
    assert ep == "https://s3.cl4.du.cesnet.cz"


def test_parse_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("""
# Comment
DVC_S3_URL="s3.cl4.du.cesnet.cz://dvc-projects/scieflow"
AWS_ACCESS_KEY_ID='mykey'
AWS_SECRET_ACCESS_KEY=mysecret
EMPTY=
""")
    parsed = dvc_setup_s3.parse_env_file(env_file)
    assert parsed["DVC_S3_URL"] == "s3.cl4.du.cesnet.cz://dvc-projects/scieflow"
    assert parsed["AWS_ACCESS_KEY_ID"] == "mykey"
    assert parsed["AWS_SECRET_ACCESS_KEY"] == "mysecret"
    assert "EMPTY" not in parsed


def test_load_env_into_environ(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_KEY_DVC=hello_world\n")
    monkeypatch.delenv("TEST_KEY_DVC", raising=False)
    dvc_sync.load_env_into_environ(env_file)
    assert os.environ.get("TEST_KEY_DVC") == "hello_world"


@pytest.fixture
def repo(tmp_path):
    """Minimal repo: config/defaults.yml (archive: false) + workspace/."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "defaults.yml").write_text("archive: false\n")
    ws = tmp_path / "workspace"
    ws.mkdir()
    return tmp_path, ws


@pytest.fixture
def calls(monkeypatch):
    """Record dvc argv instead of running it; every command succeeds."""
    recorded = []

    def fake_run_cmd(cmd, cwd):
        recorded.append(cmd)
        return 0

    monkeypatch.setattr(dvc_sync, "run_cmd", fake_run_cmd)
    return recorded


def make_ws(ws: Path, slug: str, run_config: dict | None = None) -> Path:
    run = ws / slug
    (run / "logs").mkdir(parents=True)
    (run / "status.yml").write_text("phase: done\n")
    (run / "logs" / "agent.log").write_text("transcript\n")
    if run_config is not None:
        (run / "config.yml").write_text(yaml.safe_dump(run_config))
    return run


def add_pointer(ws: Path, slug: str) -> Path:
    pointer = archive.pointer_path(ws, slug)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(f"outs:\n- md5: 00000000000000000000000000000000\n  path: {slug}.zip\n")
    return pointer


def test_find_workspaces_skips_archive_dir(repo):
    _, ws = repo
    make_ws(ws, "run-01")
    (ws / "_archives").mkdir()
    assert dvc_sync.find_workspaces(ws) == ["run-01"]


def test_resolve_slugs_all_includes_pointer_only_slugs(repo):
    _, ws = repo
    make_ws(ws, "run-01")
    add_pointer(ws, "run-00-archived")
    assert dvc_sync.resolve_slugs([], True, ws) == ["run-00-archived", "run-01"]


def test_resolve_slugs_accepts_pointer_only_and_rejects_archive_dir(repo):
    _, ws = repo
    add_pointer(ws, "run-00-archived")
    assert dvc_sync.resolve_slugs(["workspace/run-00-archived"], False, ws) == ["run-00-archived"]
    with pytest.raises(ValueError, match="archive directory"):
        dvc_sync.resolve_slugs(["_archives"], False, ws)


@pytest.mark.parametrize(
    "has_pointer, flag, run_config, expected",
    [
        (False, None, None, False),              # default: directory mode
        (False, None, {"archive": True}, True),   # opted in via config.yml
        (False, None, {"archive": False}, False),
        (False, True, None, True),               # --archive
        (True, None, None, True),                # pointer already exists
        (True, False, None, False),              # --no-archive beats pointer
        (False, False, {"archive": True}, False),  # --no-archive beats config
    ],
)
def test_use_archive_push(repo, has_pointer, flag, run_config, expected):
    root, ws = repo
    make_ws(ws, "run-01", run_config)
    if has_pointer:
        add_pointer(ws, "run-01")
    assert dvc_sync.use_archive("run-01", root, ws, command="push", archive_flag=flag) is expected


def test_use_archive_pull_depends_on_pointer_only(repo):
    root, ws = repo
    make_ws(ws, "run-01", {"archive": True})
    assert dvc_sync.use_archive("run-01", root, ws, command="pull") is False
    add_pointer(ws, "run-01")
    assert dvc_sync.use_archive("run-01", root, ws, command="pull") is True


def test_track_skips_archived_workspace(repo, calls):
    root, ws = repo
    make_ws(ws, "run-01")
    add_pointer(ws, "run-01")
    assert dvc_sync.cmd_track(["run-01"], root, ws) == 0
    assert calls == []


def test_workspace_summary_three_states(repo):
    _, ws = repo
    make_ws(ws, "run-a")
    add_pointer(ws, "run-a")
    make_ws(ws, "run-b")
    (ws / "run-b.dvc").write_text("outs: []\n")
    make_ws(ws, "run-c")
    add_pointer(ws, "run-d")
    archive.archive_path(ws, "run-d").write_bytes(b"zip")

    lines = dvc_sync.workspace_summary(ws)
    assert "[ARCHIVE] zip: not downloaded" in lines[0]
    assert "[TRACKED in DVC]" in lines[1]
    assert "[LOCAL ONLY - NOT TRACKED]" in lines[2]
    assert "[ARCHIVE] zip: present" in lines[3]
