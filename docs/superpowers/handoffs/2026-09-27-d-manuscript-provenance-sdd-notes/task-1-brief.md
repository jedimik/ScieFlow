### Task 1: The repo and the plumbing primitives

**Files:**
- Create: `src/scieflow/core/provenance.py`
- Test: `tests/core/test_provenance.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  `provenance.REPO_DIR = "provenance.git"`;
  `provenance.ProvenanceError` (subclasses `ValueError`);
  `provenance.available() -> bool`;
  `provenance.repo_path(ws) -> Path`;
  `provenance.ensure_repo(ws) -> Path` — `init --bare`, idempotent, rebuilds a corrupt repo, creates the empty root commit;
  `provenance.EMPTY_ROOT_REF = "refs/heads/_root"`;
  private `_git(repo, *args, stdin=None) -> str`, `_blob(repo, data) -> str`, `_tree(repo, entries) -> str`, `_commit(repo, tree, parents, message) -> str`, `_update_ref(repo, ref, sha) -> None`, `_ref_sha(repo, ref) -> str | None`.

**Why an empty root commit.** Every branch — `main` and each `draft/<agent>` — needs a common ancestor for `git diff` between them to behave. `_root` is one commit with an empty tree, created once, never moved.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_provenance.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_provenance.py -v`
Expected: FAIL — `No module named 'scieflow.core.provenance'`.

- [ ] **Step 3: Write the implementation**

Create `src/scieflow/core/provenance.py`. Import `shutil` and `subprocess` at module level under those names — the tests monkeypatch `provenance.shutil.which`.

```python
REPO_DIR = "provenance.git"
EMPTY_ROOT_REF = "refs/heads/_root"
GIT = "git"
_TIMEOUT_S = 30


class ProvenanceError(ValueError):
    """A git operation failed, or the repo could not be made usable."""


def available() -> bool:
    return shutil.which(GIT) is not None


def repo_path(ws: Path) -> Path:
    return Path(ws) / REPO_DIR
```

`_git(repo, *args, stdin=None)` runs `[GIT, "--git-dir", str(repo), *args]` through `subprocess.run` with `capture_output=True`, `text=True`, `timeout=_TIMEOUT_S`, and `input=stdin`. On a non-zero return code, or `FileNotFoundError`, or `subprocess.TimeoutExpired`, raise `ProvenanceError` carrying the command's tail of stderr. Return `stdout.strip()`.

Pass every argument as its own list element and never build a shell string — that is what makes the commit-message test above true, and it is the same rule the rest of this repo follows for `bwrap` and `latexmk`.

`_blob(repo, data)` is `_git(repo, "hash-object", "-w", "--stdin", stdin=data)`.

`_tree(repo, entries)` formats each entry as `f"{mode} {kind} {sha}\t{name}"`, joins with `"\n"`, and pipes it to `_git(repo, "mktree", stdin=...)`. An empty `entries` list must still produce the empty tree — pass an empty string as stdin rather than skipping the call.

`_commit(repo, tree, parents, message)` is `_git(repo, "commit-tree", tree, *chain(("-p", p) for p in parents), "-m", message)`.

**`_git` owns the environment, not `_commit`.** Build it once inside `_git` for every call rather than only for commits: `PATH` and `LANG` inherited, plus `GIT_AUTHOR_NAME`/`GIT_COMMITTER_NAME` of `"ScieFlow"` and both email variables set to `"provenance@scieflow.local"`, with the dates left to git. Two reasons this belongs in the runner: a missing global `user.email` makes `commit-tree` fail on a fresh machine, and constructing the env in one place means no later call site can accidentally inherit the serve process's environment — the same discipline `preview.compile_env` applies for `latexmk`. Do not copy `os.environ`.

`_update_ref(repo, ref, sha)` is `_git(repo, "update-ref", ref, sha)`.

`_ref_sha(repo, ref)` runs `rev-parse --verify --quiet <ref>` and returns `None` on the non-zero exit rather than raising — a missing branch is an ordinary state here.

`ensure_repo(ws)`:
- if `repo_path(ws)` exists but `_git(repo, "rev-parse", "--is-bare-repository")` fails or does not return `"true"`, `shutil.rmtree` it (or `unlink` when it is a file) and fall through to init. Wrap that probe so a corrupt repo is detected rather than propagated — the repo is derived, so discarding is correct.
- `subprocess.run([GIT, "init", "--bare", "--quiet", str(repo)])` when absent.
- create `EMPTY_ROOT_REF` when `_ref_sha` returns `None`: `_commit(repo, _tree(repo, []), [], "empty root")` then `_update_ref`. Never recreate it when present, or every branch reparents.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_provenance.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/provenance.py tests/core/test_provenance.py
git commit -m "feat(core): a bare per-run provenance repo, written with git plumbing"
```

---

