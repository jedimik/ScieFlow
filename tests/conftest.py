"""Suite-wide isolation: jobs recorded outside any run go to a temp dir,
never into the real repository's .scieflow/."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(tmp_path_factory.mktemp("state")))
