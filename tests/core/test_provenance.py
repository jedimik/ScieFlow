"""The per-run git repo that carries a manuscript's history.

Bare, no working tree: every write is plumbing (`hash-object`, `mktree`,
`commit-tree`, `update-ref`). These tests drive real git — it is present on
this host at 2.43.0 — because the whole point of the module is what git
actually does with the trees it is handed.
"""

import shutil
import subprocess

import pytest

from scieflow.core import provenance


@pytest.fixture
def ws(tmp_path):
    workspace = tmp_path / "workspace" / "r1"
    workspace.mkdir(parents=True)
    return workspace


def test_git_is_available_on_this_host(ws):
    assert provenance.available() is True


def test_ensure_repo_creates_a_bare_repo_with_no_working_tree(ws):
    repo = provenance.ensure_repo(ws)
    assert repo == ws / provenance.REPO_DIR
    assert repo.is_dir()
    assert (repo / "HEAD").is_file(), "a bare repo keeps HEAD at its root"
    assert not (repo / ".git").exists(), "bare: no nested .git"
    assert not (ws / provenance.REPO_DIR / "sections").exists(), "no checkout"
    out = subprocess.run(["git", "--git-dir", str(repo), "rev-parse", "--is-bare-repository"],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "true"


def test_ensure_repo_is_idempotent(ws):
    first = provenance.ensure_repo(ws)
    root_before = provenance._ref_sha(first, provenance.EMPTY_ROOT_REF)
    second = provenance.ensure_repo(ws)
    assert second == first
    assert provenance._ref_sha(second, provenance.EMPTY_ROOT_REF) == root_before, (
        "the root commit must not be recreated, or every branch reparents")


def test_the_empty_root_commit_exists_and_has_an_empty_tree(ws):
    repo = provenance.ensure_repo(ws)
    sha = provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF)
    assert sha
    listing = provenance._git(repo, "ls-tree", "-r", sha)
    assert listing == "", "the root commit's tree must be empty"


def test_a_corrupt_repo_is_rebuilt_rather_than_raising(ws):
    """The repo is derived, so a broken one is discarded, not repaired."""
    repo = provenance.ensure_repo(ws)
    (repo / "HEAD").write_text("this is not a git HEAD\n")
    rebuilt = provenance.ensure_repo(ws)
    assert provenance._ref_sha(rebuilt, provenance.EMPTY_ROOT_REF), "no usable root after rebuild"


def test_a_repo_directory_that_is_a_file_is_rebuilt(ws):
    (ws / provenance.REPO_DIR).write_text("not a directory")
    repo = provenance.ensure_repo(ws)
    assert repo.is_dir()


def test_blob_tree_and_commit_round_trip(ws):
    repo = provenance.ensure_repo(ws)
    blob = provenance._blob(repo, "\\section{Results}\nYield was 95\\%.\n")
    subtree = provenance._tree(repo, [("100644", "blob", blob, "results.tex")])
    root = provenance._tree(repo, [("040000", "tree", subtree, "sections")])
    commit = provenance._commit(repo, root, [], "probe")
    provenance._update_ref(repo, "refs/heads/probe", commit)

    assert provenance._ref_sha(repo, "refs/heads/probe") == commit
    shown = provenance._git(repo, "show", "probe:sections/results.tex")
    assert "Yield was 95" in shown


def test_a_commit_message_is_never_interpreted_as_an_argument(ws):
    """Messages come from agent-influenced values (round numbers, agent
    names). `-m` takes its value as one argv element, so a message that looks
    like a flag is still a message."""
    repo = provenance.ensure_repo(ws)
    tree = provenance._tree(repo, [])
    commit = provenance._commit(repo, tree, [], "--oneline --all")
    body = provenance._git(repo, "log", "-1", "--format=%s", commit)
    assert body == "--oneline --all"


def test_a_missing_ref_reads_as_none_not_an_error(ws):
    repo = provenance.ensure_repo(ws)
    assert provenance._ref_sha(repo, "refs/heads/nope") is None


def test_a_git_failure_becomes_a_provenance_error(ws):
    repo = provenance.ensure_repo(ws)
    with pytest.raises(provenance.ProvenanceError):
        provenance._git(repo, "cat-file", "-p", "0" * 40)


def test_a_missing_git_reads_as_unavailable(ws, monkeypatch):
    """A supported state, not a skip: the page degrades on this."""
    monkeypatch.setattr(provenance.shutil, "which", lambda name: None)
    assert provenance.available() is False
