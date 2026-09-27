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


def test_env_never_inherits_the_parent_environment():
    """The whole reason `_git` builds its own environment: a model API key
    (or anything else) sitting in the parent must not reach git. `_env`
    takes `parent` as a seam, exactly like `preview.compile_env`, so this
    can be checked without mutating the real `os.environ`."""
    env = provenance._env({"PATH": "/bin", "LANG": "C",
                           "ANTHROPIC_API_KEY": "sk-should-not-travel"})
    assert set(env) == {
        "PATH", "LANG",
        "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME",
        "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL",
        "GIT_CONFIG_NOSYSTEM",
    }
    assert env["PATH"] == "/bin"
    assert env["LANG"] == "C"
    assert "ANTHROPIC_API_KEY" not in env
    assert "HOME" not in env, "HOME must stay absent so ~/.gitconfig cannot override identity"


def test_ensure_repo_never_recreates_the_root_commit_once_set(ws, monkeypatch):
    """Regression guard for the idempotency test above, which cannot itself
    fail here: two `commit-tree` calls made in the same wall-clock second
    with the same tree/parents/message produce the same SHA even under a
    full reparent, so `root_before == root_after` proves nothing on its
    own. This pins the actual invariant: once `EMPTY_ROOT_REF` exists, a
    second `ensure_repo` must never call `_commit` again at all."""
    provenance.ensure_repo(ws)

    def boom(*args, **kwargs):
        raise AssertionError("_commit must not run once the root ref is set")

    monkeypatch.setattr(provenance, "_commit", boom)
    provenance.ensure_repo(ws)  # must not raise


def test_the_root_commit_is_the_same_sha_in_every_repo(tmp_path):
    """`_ROOT_COMMIT_DATE` pins the root commit's date so it is one
    well-known SHA everywhere, not just stable within a single repo — two
    independent runs' provenance repos must agree on it for a common
    ancestor to mean anything across them."""
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    repo_a = provenance.ensure_repo(ws_a)
    repo_b = provenance.ensure_repo(ws_b)
    sha_a = provenance._ref_sha(repo_a, provenance.EMPTY_ROOT_REF)
    sha_b = provenance._ref_sha(repo_b, provenance.EMPTY_ROOT_REF)
    assert sha_a == sha_b


def test_tree_entry_names_cannot_smuggle_extra_entries(ws):
    """`_tree` is the primitive every later task writes agent-chosen
    filenames through. A name containing what looks like a second
    `<mode> <kind> <sha>\\t<name>` record must land as one literal filename,
    never as a second, unrequested tree entry pointing at content the
    caller never chose."""
    repo = provenance.ensure_repo(ws)
    good_blob = provenance._blob(repo, "legit content\n")
    evil_blob = provenance._blob(repo, "smuggled content\n")
    evil_name = f"results.tex\n100644 blob {evil_blob}\tsmuggled.tex"

    tree = provenance._tree(repo, [("100644", "blob", good_blob, evil_name)])
    listing = provenance._git(repo, "ls-tree", "-z", tree)
    entries = [e for e in listing.split("\x00") if e]

    assert len(entries) == 1, f"a newline in a name must not forge a second entry: {entries!r}"
    assert entries[0] == f"100644 blob {good_blob}\t{evil_name}"


def test_tree_entry_names_with_an_embedded_tab_stay_one_entry(ws):
    repo = provenance.ensure_repo(ws)
    blob = provenance._blob(repo, "content\n")
    name = "weird\tname\twith\ttabs.tex"

    tree = provenance._tree(repo, [("100644", "blob", blob, name)])
    listing = provenance._git(repo, "ls-tree", "-z", tree)
    entries = [e for e in listing.split("\x00") if e]

    assert len(entries) == 1
    assert entries[0] == f"100644 blob {blob}\t{name}"


def test_tree_refuses_a_name_containing_a_nul_byte(ws):
    repo = provenance.ensure_repo(ws)
    blob = provenance._blob(repo, "content\n")
    with pytest.raises(provenance.ProvenanceError):
        provenance._tree(repo, [("100644", "blob", blob, "evil\x00name")])


def test_a_symlinked_repo_path_is_rebuilt_not_raised(ws):
    """`shutil.rmtree` refuses a symlink outright (`OSError`), so a
    symlinked `provenance.git` must be caught before the plain
    file/directory branches, or this breaks the module's "one exception to
    handle" contract."""
    target = ws.parent / "elsewhere"
    target.mkdir()
    (ws / provenance.REPO_DIR).symlink_to(target)

    repo = provenance.ensure_repo(ws)
    assert repo.is_dir()
    assert not repo.is_symlink()
    assert provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF)


def test_a_git_failure_never_leaks_the_full_argument_list(ws):
    """A failing `commit-tree` carries an agent-influenced commit message
    as one of its arguments; the exception must not repeat it."""
    repo = provenance.ensure_repo(ws)
    secret_message = "SENSITIVE-MESSAGE-MUST-NOT-APPEAR-IN-ERROR"
    with pytest.raises(provenance.ProvenanceError) as exc_info:
        provenance._git(repo, "commit-tree", "0" * 40, "-m", secret_message)
    assert secret_message not in str(exc_info.value)
