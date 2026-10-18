"""Suite-wide isolation: jobs recorded outside any run go to a temp dir,
never into the real repository's .scieflow/."""

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
