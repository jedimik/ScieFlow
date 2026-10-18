"""Suite-wide isolation: jobs recorded outside any run go to a temp dir,
never into the real repository's .scieflow/."""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(tmp_path_factory.mktemp("state")))


@pytest.fixture(autouse=True)
def _isolated_credentials_dir(tmp_path_factory, monkeypatch):
    """S4 (A9): no test resolves the real ~/.config/scieflow/credentials."""
    from scieflow.core import sandbox

    path = tmp_path_factory.mktemp("credentials")
    monkeypatch.setattr(sandbox, "CREDENTIALS_OVERRIDE", path, raising=False)
    return path


@pytest.fixture
def child_env(tmp_path):
    """S25fix1 (review finding 1, NIT 9): the environment for a child process that may dispatch.

    HOME is a fake home OUTSIDE tmp_path (the project root), so S2 accepts it and the child
    resolves credentials only there. It holds a 0600 fake token at the name `grant_credentials`
    writes (`fixture.token`), so a sandboxed child with a credential entry can read it.
    Returns the env mapping; its HOME is asserted not to be the pytest process's home.
    """
    from pathlib import Path

    home = tmp_path.parent / f"{tmp_path.name}-home"
    creds = home / ".config" / "scieflow" / "credentials"
    creds.mkdir(parents=True, exist_ok=True)
    token = creds / "fixture.token"
    token.write_text("fake-token-fixture\n")
    token.chmod(0o600)
    assert home.resolve() != Path.home().resolve(), "a child must never get the real home"
    assert home.resolve() not in tmp_path.resolve().parents and home.resolve() != tmp_path.resolve()
    return {**os.environ, "HOME": str(home)}


@pytest.fixture
def grant_credentials():
    """S4 (A9): a real env credential per named agent, backed by a fake 0600 token file.

    Merges into the project's config/sandbox.yml, keeping its other keys. Returns grant(root, *agents).
    """
    import yaml

    def grant(root, *agents):
        from pathlib import Path

        from scieflow.core import sandbox

        token = sandbox.credentials_dir() / "fixture.token"
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text("fake-token-fixture\n")
        token.chmod(0o600)
        policy = Path(root) / "config" / "sandbox.yml"
        data = (yaml.safe_load(policy.read_text()) if policy.exists() else None) or {}
        creds = dict(data.get("credentials") or {})
        for agent in agents:
            creds[agent] = {"mode": "env", "var": "SCIEFLOW_FIXTURE_TOKEN",
                            "file": "fixture.token", "reason": "fake fixture token"}
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(yaml.safe_dump({**data, "credentials": creds}))

    return grant
