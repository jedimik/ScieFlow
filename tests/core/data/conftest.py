"""Shared fixtures for the data-protection tests (M-B t1, Part A 5.1-5.3, 5.10).

Every value here is synthetic. No test reads or writes real patient data and
no test calls a real service: the registry commands are inert strings.
"""

import pytest
import yaml

from scieflow.core.run import status

REGISTRY = {
    "agents": {
        "cloud-stub": {"cmd": "python3 -m stub {prompt}", "enabled": True, "timeout_min": 1},
        "safe-stub": {"cmd": "{python} -m stub --endpoint {endpoint} {prompt}",
                      "enabled": True, "timeout_min": 1},
        "launch-stub": {"cmd": "{root}/workspace/r1/tools/x.py {endpoint} {prompt}",
                        "enabled": True, "timeout_min": 1},
    }
}
DEFAULTS = {"assignments": {"loop.experiment": "safe-stub"}}


@pytest.fixture(autouse=True)
def clean_dispatch_env(monkeypatch):
    """The tests run as the human's terminal, never inside a sandboxed dispatch."""
    for name in ("SCIEFLOW_DISPATCH", "UV_CACHE_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(yaml.safe_dump(REGISTRY, sort_keys=False))
    (tmp_path / "config" / "defaults.yml").write_text(yaml.safe_dump(DEFAULTS))
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def ws(project):
    return project / "workspace" / "r1"
