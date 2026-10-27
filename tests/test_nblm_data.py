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
from scieflow.core.data import egress


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


def test_a_listed_service_reaches_the_downstream_call(root, monkeypatch):
    # M-B t4 review F6(b): "listed -> proceeds" is a real assertion, not only an absent message.
    (root / "config" / "data-policy.yml").write_text(yaml.safe_dump({"version": 1, "l2_providers": [
        {"name": "notebooklm", "reason": "test: reviewed service"}]}))
    reached = []
    monkeypatch.setattr(nblm.policy, "load_profile", lambda *a, **k: "profile")
    monkeypatch.setattr(nblm, "cmd_check", lambda profile, ws: reached.append((profile, ws)) or 0)
    assert nblm.main(["check", "default", "--workspace", str(_ws(root))]) == 0
    assert reached == [("profile", _ws(root))]


def test_a_sub_folder_of_an_l2_run_is_refused_with_exit_3(root, capsys):
    # M-B t4 review F5: nblm takes a raw --workspace path, so a sub-folder must not bypass the rule.
    sub = _ws(root) / "outputs"
    sub.mkdir()
    assert nblm.main(["check", "default", "--workspace", str(sub)]) == 3
    assert "data protection" in capsys.readouterr().err


def test_report_for_an_l2_run_is_not_subject_to_the_session_rule(root, tmp_path, monkeypatch,
                                                                 capsys):
    # M-B t4 review F6(a): report and extract are local and stay outside the session rule.
    calls = []
    monkeypatch.setattr(egress, "service_check", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(nblm.policy, "load_verdicts", lambda root: {})
    monkeypatch.setattr(nblm.verify_mod, "render_report", lambda *a, **k: "report\n")
    out = _ws(root) / "nblm" / "audit.md"
    assert nblm.main(["report", "--workspace", str(_ws(root)), "--out", str(out)]) == 0
    assert out.read_text() == "report\n"
    assert calls == []
    assert "data protection" not in capsys.readouterr().err


def test_extract_is_not_subject_to_the_session_rule(root, monkeypatch):
    calls = []
    monkeypatch.setattr(egress, "service_check", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(nblm, "cmd_extract", lambda *a: 0)
    assert nblm.main(["extract", "--manuscript", "m.md", "--bib", "b.bib", "--out", "o.yml"]) == 0
    assert calls == []


def test_invalid_policy_refuses_a_session_operation(root, capsys):
    (root / "config" / "data-policy.yml").write_text("version: 1\nenforcement: maybe\n")
    assert nblm.main(["upload", "default", "--workspace", str(_ws(root))]) == 3
    assert "data-policy" in capsys.readouterr().err
