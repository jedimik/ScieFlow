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


def test_push_archive_uploads_removes_zip_and_old_pointer(repo, calls, capsys):
    root, ws = repo
    make_ws(ws, "run-01")
    (ws / "run-01.dvc").write_text("outs: []\n")

    rc = dvc_sync.cmd_push(["run-01"], root, ws, archive_flag=True)

    assert rc == 0
    assert calls == [
        ["dvc", "add", "--to-remote", "workspace/_archives/run-01.zip"],
        ["dvc", "remove", "workspace/run-01.dvc"],
    ]
    assert not archive.archive_path(ws, "run-01").exists()
    out = capsys.readouterr().out
    assert "git add -A workspace/_archives workspace/run-01.dvc\n" in out


def test_push_archive_keep_zip_and_remote(repo, calls):
    root, ws = repo
    make_ws(ws, "run-01")

    rc = dvc_sync.cmd_push(["run-01"], root, ws, archive_flag=True, keep_zip=True, remote="alt")

    assert rc == 0
    assert calls == [["dvc", "add", "--to-remote", "-r", "alt", "workspace/_archives/run-01.zip"]]
    assert archive.verify_zip(archive.archive_path(ws, "run-01")) > 0


def test_push_archive_upload_failure_keeps_zip(repo, monkeypatch):
    root, ws = repo
    make_ws(ws, "run-01")
    monkeypatch.setattr(dvc_sync, "run_cmd", lambda cmd, cwd: 3)

    assert dvc_sync.cmd_push(["run-01"], root, ws, archive_flag=True) == 3
    assert archive.archive_path(ws, "run-01").exists()


def test_push_archive_disk_preflight_refuses(repo, calls, monkeypatch, capsys):
    root, ws = repo
    make_ws(ws, "run-01")
    monkeypatch.setattr(archive, "workspace_size", lambda _src: 254 * 1024**3)
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda _p: SimpleNamespace(free=100 * 1024**3))

    assert dvc_sync.cmd_push(["run-01"], root, ws, archive_flag=True) == 1
    assert calls == []
    assert "needs 266.7G, 100.0G free" in capsys.readouterr().out


def test_push_archive_skips_pointer_only_slug(repo, calls):
    root, ws = repo
    add_pointer(ws, "run-00-archived")
    assert dvc_sync.cmd_push(["run-00-archived"], root, ws) == 0
    assert calls == []


def test_push_refuses_implicit_directory_mode(repo, calls, capsys):
    """Directory mode uploads file-by-file; it must never happen by default."""
    root, ws = repo
    make_ws(ws, "run-01", {"archive": True})
    make_ws(ws, "run-02", {"archive": False})
    (ws / "run-02.dvc").write_text("outs: []\n")

    assert dvc_sync.cmd_push(["run-01", "run-02"], root, ws) == 1
    out = capsys.readouterr().out
    assert "directory mode is not implicit" in out
    assert "run-02" in out
    assert calls == []


def test_push_directory_mode_when_asked_for(repo, calls):
    root, ws = repo
    make_ws(ws, "run-02", {"archive": False})
    (ws / "run-02.dvc").write_text("outs: []\n")

    assert dvc_sync.cmd_push(["run-02"], root, ws, archive_flag=False) == 0
    assert calls == [["dvc", "push", "workspace/run-02.dvc"]]


def test_pull_archive_extracts_and_keeps_zip(repo, monkeypatch, capsys):
    root, ws = repo
    source = make_ws(root / "elsewhere", "run-01")
    zip_path = archive.archive_path(ws, "run-01")
    add_pointer(ws, "run-01")
    recorded = []

    def fake_pull(cmd, cwd):  # dvc pull materializes the zip
        recorded.append(cmd)
        archive.build_zip(source, zip_path)
        return 0

    monkeypatch.setattr(dvc_sync, "run_cmd", fake_pull)

    assert dvc_sync.cmd_pull(["run-01"], root, ws) == 0
    assert recorded == [["dvc", "pull", "workspace/_archives/run-01.zip.dvc"]]
    assert (ws / "run-01" / "logs" / "agent.log").read_text() == "transcript\n"
    assert zip_path.exists()
    assert "could not hardlink" in capsys.readouterr().out  # no cache object in tmp repo


def test_pull_archive_refuses_non_empty_dir_without_force(repo, monkeypatch):
    root, ws = repo
    source = make_ws(root / "elsewhere", "run-01")
    make_ws(ws, "run-01")
    (ws / "run-01" / "local-only.txt").write_text("keep me")
    add_pointer(ws, "run-01")
    monkeypatch.setattr(
        dvc_sync, "run_cmd",
        lambda cmd, cwd: archive.build_zip(source, archive.archive_path(ws, "run-01")) or 0,
    )

    assert dvc_sync.cmd_pull(["run-01"], root, ws) == 1
    assert (ws / "run-01" / "local-only.txt").exists()
    assert dvc_sync.cmd_pull(["run-01"], root, ws, force=True) == 0
    assert not (ws / "run-01" / "local-only.txt").exists()


def test_pull_directory_argv_unchanged(repo, calls):
    root, ws = repo
    (ws / "run-02.dvc").write_text("outs: []\n")
    assert dvc_sync.cmd_pull(["run-02"], root, ws) == 0
    assert calls == [["dvc", "pull", "workspace/run-02.dvc"]]


def test_parser_archive_flags():
    parser = dvc_sync.build_parser(Path(".env"))
    assert parser.parse_args(["push", "run-01"]).archive is None
    assert parser.parse_args(["push", "run-01", "--archive"]).archive is True
    assert parser.parse_args(["push", "run-01", "--no-archive"]).archive is False
    with pytest.raises(SystemExit):
        parser.parse_args(["push", "run-01", "--archive", "--no-archive"])
    pulled = parser.parse_args(["pull", "run-01", "--force", "--remote", "alt"])
    assert (pulled.force, pulled.remote) == (True, "alt")


def test_push_and_pull_require_explicit_slugs():
    """`--all` is for `track` only: moving data is never one flag away."""
    parser = dvc_sync.build_parser(Path(".env"))
    for command in ("push", "pull"):
        with pytest.raises(SystemExit):
            parser.parse_args([command])
        with pytest.raises(SystemExit):
            parser.parse_args([command, "--all"])
    assert parser.parse_args(["track", "--all"]).all is True
