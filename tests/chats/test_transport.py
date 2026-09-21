"""The optional DVC transport: opt-in, encrypted-only, bundles only."""

import pytest

from scieflow.chats.config import load_config
from scieflow.chats.transport import TransportError, available, staging_dir
from scieflow.chats.transport import pull as pull_bundle
from scieflow.chats.transport import push as push_bundle
from tests.chats.conftest import write_config


@pytest.fixture
def remote_config(tmp_path, source_home):
    home, _ = source_home
    return lambda **kw: load_config(
        write_config(
            tmp_path / f"remote-{len(kw)}-{sorted(kw)}.yml",
            home,
            tmp_path / "bundles",
            remote={"enabled": True, "dvc_remote": None, "dir": "workspace/chats", **kw},
        )
    )


def test_remote_is_off_by_default(source_config):
    config = load_config(source_config)
    assert config.remote.enabled is False


def test_push_refuses_while_remote_is_off(tmp_path, source_config):
    bundle = tmp_path / "b.zip.age"
    bundle.write_bytes(b"x")
    with pytest.raises(TransportError, match="remote sync is off"):
        push_bundle(load_config(source_config), bundle)


def test_push_refuses_a_plaintext_bundle(tmp_path, remote_config):
    bundle = tmp_path / "plain.zip"
    bundle.write_bytes(b"x")
    with pytest.raises(TransportError, match="not encrypted"):
        push_bundle(remote_config(), bundle)


def test_push_refuses_a_missing_bundle(tmp_path, remote_config):
    with pytest.raises(TransportError, match="no such bundle"):
        push_bundle(remote_config(), tmp_path / "absent.zip.age")


def test_pull_needs_a_pointer(remote_config):
    with pytest.raises(TransportError, match="no pointer"):
        pull_bundle(remote_config(), "nothing.zip.age")


def test_available_lists_pointers_newest_first(remote_config):
    config = remote_config()
    stage = staging_dir(config)
    stage.mkdir(parents=True, exist_ok=True)
    older = stage / "old.zip.age.dvc"
    newer = stage / "new.zip.age.dvc"
    older.write_text("outs: []\n")
    newer.write_text("outs: []\n")
    import os

    os.utime(older, (1, 1))
    try:
        assert [p.name for p in available(config)][0] == newer.name
        assert older.name in [p.name for p in available(config)]
    finally:
        older.unlink()
        newer.unlink()


def test_remote_dir_must_be_relative(tmp_path, source_home):
    from scieflow.chats.config import ConfigError

    home, _ = source_home
    cfg = write_config(
        tmp_path / "abs.yml", home, tmp_path, remote={"dir": "/etc", "enabled": True}
    )
    with pytest.raises(ConfigError, match="relative to the repo root"):
        load_config(cfg)


def test_unknown_remote_key_is_rejected(tmp_path, source_home):
    from scieflow.chats.config import ConfigError

    home, _ = source_home
    cfg = write_config(tmp_path / "bad.yml", home, tmp_path, remote={"bucket": "s3://x"})
    with pytest.raises(ConfigError, match="remote: unknown key 'bucket'"):
        load_config(cfg)
