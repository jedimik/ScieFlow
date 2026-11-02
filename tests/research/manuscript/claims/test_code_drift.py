"""M-G t3: C006 artifact-code drift (M-G 5.6): dirty manifests, shas that are not ancestors of HEAD, unknown
git state. Throwaway git repositories in the synthetic project folder; no network."""

from __future__ import annotations

import pytest

from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t3_support import (
    body, built_check, git, init_repo, make_campaign_with_code, of_code, of_rule, rules,
)
from tests.research.manuscript.t8_support import project_of

RID = "sigma-1.5"
NOT_A_COMMIT = "deadbeef" * 5


def cite_run(synth, code: dict) -> None:
    make_campaign_with_code(synth.run_dir, "denoise", {RID: code})
    from tests.research.manuscript.claims.t2_support import manuscript
    manuscript(synth, body("The inline run gave a stable value [run:denoise/sigma-1.5]."))


def git_block(sha, dirty=False):
    return {"sha": sha, "dirty": dirty}


def repo_head(synth) -> str:
    return init_repo(synth.root)


def test_a_dirty_scieflow_checkout_is_c006_code_dirty(synth):
    head = repo_head(synth)
    cite_run(synth, {"scieflow": {"git": git_block(head, dirty=True)},
                     "pipeline": {"git": git_block(head)}})
    result = built_check(synth)
    found = of_rule(result, "code-dirty")
    assert found and found[0]["code"] == "C006" and found[0]["severity"] == "error", rules(result)


def test_a_commit_that_is_not_an_ancestor_of_head_is_c006_code_not_ancestor(synth):
    head = repo_head(synth)
    cite_run(synth, {"scieflow": {"git": git_block(NOT_A_COMMIT)}, "pipeline": {"git": git_block(head)}})
    result = built_check(synth)
    found = of_rule(result, "code-not-ancestor")
    assert found and found[0]["code"] == "C006" and found[0]["severity"] == "error", rules(result)


def test_an_ancestor_commit_has_no_drift_finding(synth):
    head = repo_head(synth)
    cite_run(synth, {"scieflow": {"git": git_block(head)}, "pipeline": {"git": git_block(head)}})
    result = built_check(synth)
    assert of_code(result, "C006") == [], rules(result)


def test_a_null_git_state_is_code_state_unknown_warning(synth):
    head = repo_head(synth)
    cite_run(synth, {"scieflow": {"git": None}, "pipeline": {"git": git_block(head)}})
    result = built_check(synth)
    found = of_rule(result, "code-state-unknown")
    assert found and found[0]["severity"] == "warning" and found[0]["code"] is None, rules(result)
    assert result.errors == []


def test_git_missing_from_path_is_code_state_unknown_warning(synth, monkeypatch, tmp_path):
    head = repo_head(synth)
    cite_run(synth, {"scieflow": {"git": git_block(head)}, "pipeline": {"git": git_block(head)}})
    empty = tmp_path / "no-git-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    result = built_check(synth)
    found = of_rule(result, "code-state-unknown")
    assert found and found[0]["severity"] == "warning", rules(result)
    assert of_code(result, "C006") == [], "an uncheckable state is a warning with no code"


def test_a_malformed_sha_never_reaches_git(synth):
    head = repo_head(synth)
    cite_run(synth, {"scieflow": {"git": git_block("--output=/tmp/x")}, "pipeline": {"git": git_block(head)}})
    result = built_check(synth)
    assert of_rule(result, "code-state-unknown"), rules(result)
    assert of_rule(result, "code-not-ancestor") == []


def test_c004_is_absent_from_drift_findings(synth):
    head = repo_head(synth)
    cite_run(synth, {"scieflow": {"git": git_block(NOT_A_COMMIT)}, "pipeline": {"git": git_block(head)}})
    result = built_check(synth)
    assert "C004" not in {f["code"] for f in result.findings}
