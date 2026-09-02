from pathlib import Path
import os
import pytest
import dvc_sync
import dvc_setup_s3


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
