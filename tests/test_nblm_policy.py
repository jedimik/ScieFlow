from pathlib import Path

import pytest

from nblm import policy

CFG = """\
profiles:
  default:
    session_state: /home/tester/.nlm/state.json
    notebook_prefix: scieflow-
    allowed_ops: [check, fetch, upload, verify]
    source_hosts:
      - europepmc.org
      - arxiv.org
    limits:
      max_sources_per_notebook: 50
      max_source_mb: 50
      max_questions_per_run: 40
      max_questions_per_day: 45
      max_claims_per_question: 5
      reask_threshold: partial
"""

SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "claim-audit.yml"


@pytest.fixture
def root(tmp_path):
    root_dir = tmp_path / "root"
    (root_dir / "config").mkdir(parents=True)
    (root_dir / "schemas").mkdir()
    (root_dir / "config" / "notebooklm.yml").write_text(CFG)
    (root_dir / "schemas" / "claim-audit.yml").write_text(SCHEMA.read_text())
    return root_dir


def test_load_profile(root):
    p = policy.load_profile(root, "default")
    assert p.notebook_prefix == "scieflow-"
    assert p.limits["max_claims_per_question"] == 5


def test_load_missing_file_and_name(tmp_path, root):
    with pytest.raises(policy.PolicyError, match="notebooklm.example.yml"):
        policy.load_profile(tmp_path, "default")
    with pytest.raises(policy.PolicyError, match="'nope' not defined"):
        policy.load_profile(root, "nope")


def test_load_rejects_missing_keys_and_limits(root):
    cfg = root / "config" / "notebooklm.yml"
    cfg.write_text(CFG.replace("    notebook_prefix: scieflow-\n", ""))
    with pytest.raises(policy.PolicyError, match="missing keys: notebook_prefix"):
        policy.load_profile(root, "default")
    cfg.write_text(CFG.replace("      max_source_mb: 50\n", ""))
    with pytest.raises(policy.PolicyError, match="limits missing: max_source_mb"):
        policy.load_profile(root, "default")


def test_load_rejects_bad_limit_values(root):
    cfg = root / "config" / "notebooklm.yml"
    cfg.write_text(CFG.replace("max_questions_per_day: 45", "max_questions_per_day: 0"))
    with pytest.raises(policy.PolicyError, match="max_questions_per_day"):
        policy.load_profile(root, "default")
    cfg.write_text(CFG.replace("reask_threshold: partial", "reask_threshold: maybe"))
    with pytest.raises(policy.PolicyError, match="not a verdict"):
        policy.load_profile(root, "default")


def test_check_op(root):
    p = policy.load_profile(root, "default")
    policy.check_op(p, "verify")
    with pytest.raises(policy.PolicyError, match="'delete' not in allowed_ops"):
        policy.check_op(p, "delete")


def test_check_notebook_name(root):
    p = policy.load_profile(root, "default")
    assert policy.check_notebook_name(p, "scieflow-run1") == "scieflow-run1"
    with pytest.raises(policy.PolicyError, match="does not start with"):
        policy.check_notebook_name(p, "my-private-notes")
    with pytest.raises(policy.PolicyError, match="unsafe notebook name"):
        policy.check_notebook_name(p, "scieflow-a b")


def test_check_source_url(root):
    p = policy.load_profile(root, "default")
    url = "https://europepmc.org/api/fulltextRepo?pid=1&type=FILE"
    assert policy.check_source_url(p, url) == url
    with pytest.raises(policy.PolicyError, match="must be https"):
        policy.check_source_url(p, "http://arxiv.org/pdf/1234.pdf")
    with pytest.raises(policy.PolicyError, match="not in source_hosts"):
        policy.check_source_url(p, "https://evil.example/paper.pdf")
    with pytest.raises(policy.PolicyError, match="must not carry credentials"):
        policy.check_source_url(p, "https://u:p@arxiv.org/pdf/1234.pdf")


def test_check_source_url_refuses_subdomain_of_allowed_host(root):
    p = policy.load_profile(root, "default")
    with pytest.raises(policy.PolicyError, match="not in source_hosts"):
        policy.check_source_url(p, "https://arxiv.org.evil.example/pdf/1.pdf")


def test_doi_and_slug(root):
    assert policy.doi_slug("10.1000/xyz.123") == "10.1000-xyz.123"
    with pytest.raises(policy.PolicyError, match="not a DOI"):
        policy.check_doi("nonsense")


def test_check_inside_workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sources").mkdir(parents=True)
    policy.check_inside_workspace(ws, ws / "sources" / "a.pdf", "source")
    with pytest.raises(policy.PolicyError, match="outside the workspace"):
        policy.check_inside_workspace(ws, tmp_path / "elsewhere.pdf", "source")


def test_check_source_size(root):
    p = policy.load_profile(root, "default")
    policy.check_source_size(p, 1024, "source")
    with pytest.raises(policy.PolicyError, match="max_source_mb"):
        policy.check_source_size(p, 60 * 1024 * 1024, "source")
