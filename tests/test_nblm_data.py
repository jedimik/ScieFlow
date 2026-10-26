"""M-B t4, Part A 5.7 and C2: every NotebookLM operation that sends or fetches for an L2 run is
refused (exit 3) before any session or download. Local operations stay allowed.

No session is opened and no network is used: the session and the download are faked to fail.
Synthetic data only.
"""

import pytest
import yaml

from nblm import fetch as fetch_mod
from nblm import nblm
from nblm import session as session_mod


@pytest.fixture
def root(tmp_path, monkeypatch):
    root_dir = tmp_path / "root"
    (root_dir / "config").mkdir(parents=True)
    ws = root_dir / "workspace" / "demo"
    ws.mkdir(parents=True)
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False))
    monkeypatch.setattr(nblm, "_root", lambda: root_dir)

    def no_session(*args, **kwargs):
        raise AssertionError("an L2 run reached a NotebookLM session")

    monkeypatch.setattr(session_mod, "Session", no_session)
    monkeypatch.setattr(fetch_mod, "fetch_pdf", no_session, raising=False)
    return root_dir


def _ws(root):
    return root / "workspace" / "demo"


def test_fetch_for_an_l2_run_is_refused_with_exit_3(root, tmp_path, capsys):
    sources = tmp_path / "sources.yml"
    sources.write_text("- doi: 10.1000/a\n  url: https://example.org/a.pdf\n")
    assert nblm.main(["fetch", "default", "--workspace", str(_ws(root)),
                      "--sources", str(sources)]) == 3
    assert "data protection" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["upload", "default"],
    ["verify", "default", "--claim", "x", "--doi", "10.1000/a"],
    ["ask", "default", "--question-file", "q.md"],
    ["check", "default"],
])
def test_session_operations_for_an_l2_run_are_refused(root, capsys, argv):
    assert nblm.main([*argv, "--workspace", str(_ws(root))]) == 3
    assert "data protection" in capsys.readouterr().err


def test_resolve_for_an_l2_run_is_refused(root, tmp_path, capsys):
    claims = tmp_path / "claims.md"
    claims.write_text("x\n")
    assert nblm.main(["resolve", "default", "--workspace", str(_ws(root)),
                      "--claims", str(claims), "--out", str(tmp_path / "o.yml")]) == 3
    assert "data protection" in capsys.readouterr().err


def test_a_listed_service_is_not_refused_by_the_data_rule(root, capsys):
    (root / "config" / "data-policy.yml").write_text(yaml.safe_dump({"version": 1, "l2_providers": [
        {"name": "notebooklm", "reason": "test: reviewed service"}]}))
    nblm.main(["check", "default", "--workspace", str(_ws(root))])
    assert "data protection" not in capsys.readouterr().err


def test_invalid_policy_refuses_a_session_operation(root, capsys):
    (root / "config" / "data-policy.yml").write_text("version: 1\nenforcement: maybe\n")
    assert nblm.main(["upload", "default", "--workspace", str(_ws(root))]) == 3
    assert "data-policy" in capsys.readouterr().err
