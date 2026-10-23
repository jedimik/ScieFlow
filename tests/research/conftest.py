"""Research e2e fixtures (K integration, sandbox S4).

Stub agents are dispatched as sandboxed child processes. Each needs a `credentials:` entry in
its project's config/sandbox.yml (grant_credentials, tests/conftest.py), and the child resolves
that entry's token file under ITS OWN HOME. This fixture gives the test process a fake HOME
outside the project root (the same construction as tests/conftest.py child_env) that holds a
fake 0600 token. The real home is never read or written.
"""

from pathlib import Path

import pytest


@pytest.fixture
def stub_child_home(tmp_path, monkeypatch):
    home = tmp_path.parent / f"{tmp_path.name}-home"
    creds = home / ".config" / "scieflow" / "credentials"
    creds.mkdir(parents=True, exist_ok=True)
    token = creds / "fixture.token"
    token.write_text("fake-token-fixture\n")
    token.chmod(0o600)
    assert home.resolve() != Path.home().resolve(), "a child must never get the real home"
    assert home.resolve() not in tmp_path.resolve().parents and home.resolve() != tmp_path.resolve()
    monkeypatch.setenv("HOME", str(home))
    return home
