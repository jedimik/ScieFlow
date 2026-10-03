# Manuscript provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every run's manuscript a readable git history — what each agent proposed, what each merge round produced — and let the author read and diff it in the browser.

**Architecture:** One bare repo per run at `workspace/<slug>/provenance.git`, populated through git plumbing so there is no working tree for a sandboxed agent to corrupt and no checkout to switch. `main` carries what the run produced collectively; `draft/<agent>` carries what that agent produced alone. One idempotent `sync()` projects the current workspace into trees and commits only what changed.

**Tech Stack:** Python, `git` driven by `subprocess` (no git library), the existing `service`/`events`/`store` layers, FastAPI + Jinja. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-27-manuscript-provenance-design.md`

## Global Constraints

- **The repo is bare and has no working tree.** Every write is `hash-object` → `mktree` → `commit-tree` → `update-ref`. Never `git add`, never `git checkout`, never `--work-tree`.
- **The repo is derived, never authoritative.** The workspace files are the truth. Losing `provenance.git` costs history only, which is what makes best-effort committing correct rather than a compromise.
- **A sync failure never fails a round.** The round happened and cost budget.
- **`git` is an optional runtime dependency.** Absent, `available()` is false, the page says so in plain words, and the workbench is unaffected. This is a supported state and gets a test, not a skip.
- **Refs reaching `git` are whitelisted, never passed through raw.** `a` and `b` arrive from a query string; each must equal a string `points()` returned, compared as whole strings.
- **Agent names are discovered from the run's own artifacts**, never from a fixed list or from `config/agents.yml`.
- **Nothing is uploaded or transmitted.** AGENTS.md rule 15 does not bind this feature, and DVC still carries the bytes.
- **No new mutating route.** Nothing here is a POST, so no `MUTATING_PATHS` entry and no CSRF surface.
- **Every route handler stays `def`**, enforced by `tests/web/test_async_routes.py`.
- **Nothing uses `|safe`.** Diff text reaches HTML.
- **Nothing regresses.** `uv run pytest -q` stays green (1482 passing, 5 skipped at plan start), `./scripts/check_legacy.sh` 25/25 `ok`, `uv run --group docs mkdocs build --strict` clean.

## Review Focus

Five conditions the spec implies that no obvious test would cover. Each has a test in the task that owns the code.

1. **An agent name that is not a legal git ref, or is argument-shaped.** Agent names are directory names *an agent chose*, and they become branch names. Verified against git 2.43.0: `a..b`, `a.lock`, `x y` and `a~1` are all refused by `git check-ref-format`, and `-x` passes the format check but would be parsed as a **flag** if passed as a ref argument. `drafts.check_name` catches none of these. *(Task 2)*
2. **A run with nothing to commit**, and a run that used exactly one agent. Both are ordinary states; the panel must say there is no history rather than render an empty shell or raise. *(Task 3)*
3. **A binary or very large artifact reaching the diff.** `manuscript/` can contain a compiled PDF or a figure. `git diff` reports binaries as "Binary files differ", and a large text diff must be capped with the truncation stated. *(Task 3)*
4. **A workspace shape the projection does not expect** — `findings/claude.json` existing as a *directory*, a section file that is a dangling symlink, an unreadable file. One odd entry must cost that entry, not the whole sync. *(Task 2)*
5. **Two syncs racing** — a page load and a merge round at the same moment. `update-ref` is atomic per ref, so the outcome must be "last writer wins with no corruption", never a broken repo or a partial tree. *(Task 2)*

## File structure

| File | Responsibility |
|---|---|
| `src/scieflow/core/provenance.py` | the whole git layer: repo lifecycle, plumbing, projection, reading |
| `src/scieflow/core/service.py` | `manuscript_history`, `manuscript_diff`, the guarded sync after a merge round |
| `src/scieflow/core/events.py` | two new closed-vocabulary event types |
| `src/scieflow/web/pages.py` | the history and diff panels' context |
| `src/scieflow/web/templates/drafts.html` | the two panels |
| `tests/core/test_provenance.py` | repo lifecycle, plumbing, projection, reading, refusals |

`provenance.py` holds the git layer as one module rather than splitting repo/projection/reading: the three share the `_git` runner and the repo-path convention, and splitting them would mean exporting those. It is expected to land around 300 lines, comparable to `preview.py`.

---

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

### Task 2: The projection — discovering agents and committing what changed

**Files:**
- Modify: `src/scieflow/core/provenance.py`, `src/scieflow/core/events.py`
- Test: `tests/core/test_provenance.py` (append)

**Interfaces:**
- Consumes: everything Task 1 produced.
- Produces:
  `provenance.agents(ws) -> list[str]` — agent names discovered from the run's artifacts, sorted, only ref-safe ones;
  `provenance.ref_safe(name) -> bool`;
  `provenance.sync(ws) -> dict` — `{"commits": {ref: sha}, "agents": [...], "skipped": [...]}`;
  events `provenance.synced` and `provenance.skipped` added to `events.TYPES`.

**Where each artifact goes** — the spec's layout, as a table the implementer can work straight from. Source paths are relative to the run directory; target paths are inside the named branch.

| Source | Branch | Target path |
|---|---|---|
| `outline/outline.md` | `main` | `outline.md` |
| `manuscript/curation/rounds/<n>/<section>.tex` | `main` | `merge_<n>/sections/<section>.tex` |
| `manuscript/curation/document.yml` | `main` | `merge_<n>/curation.yml` for the highest `<n>` present |
| `review/round-<N>/review.md` | `main` | `review_<N>/review.md` |
| `review/round-<N>/response.md` | `main` | `review_<N>/response.md` |
| `findings/<agent>.json` | `draft/<agent>` | `findings.json` |
| `gaps/<agent>.json` | `draft/<agent>` | `gaps.json` |
| `manuscript/drafts/<agent>/<section>.tex` | `draft/<agent>` | `sections/<section>.tex` |
| `reviews/<R>-on-<A>.json` | `draft/<R>` | `reviews/<R>-on-<A>.json` |
| `review/draft-round-<N>/<R>-on-<A>.json` | `draft/<R>` | `review_<N>/<R>-on-<A>.json` |
| `review/draft-round-<N>/response-<A>.md` | `draft/<A>` | `review_<N>/response-<A>.md` |

`curation.yml` attaches to the highest merge round present because that is the round it describes; earlier rounds keep the copy committed when they were current, which is exactly the provenance wanted.

**Agent discovery** unions the names appearing as: subdirectories of `manuscript/drafts/`, stems of `findings/*.json` and `gaps/*.json`, and the `<R>` and `<A>` components of `<R>-on-<A>.json` filenames under `reviews/` and `review/draft-round-*/`. Never read `config/agents.yml`.

**Ref safety is the security requirement of this task.** An agent chooses these directory names, and they become branch names. Verified against git 2.43.0: `a..b`, `a.lock`, `x y` and `a~1` are refused by `git check-ref-format`; `-x` passes the format check but would be read as a **flag** if it reached a git argument position. `drafts.check_name` catches none of them. So `ref_safe` must (a) reject a leading `-`, (b) reject anything `drafts.check_name` rejects, and (c) ask git itself via `check-ref-format` rather than reimplementing its rules. An unsafe name is **skipped and reported**, not fatal — one bad directory must not cost the whole history.

**Idempotency** is per branch: build the branch's full tree, compare it to the tip's tree (`rev-parse <ref>^{tree}`), and commit only when they differ.

- [ ] **Step 1: Write the failing test**

```python
# appended to tests/core/test_provenance.py

def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def drafted(ws):
    """A run that drafted with two agents and merged twice."""
    _write(ws / "outline" / "outline.md", "# Outline\n")
    _write(ws / "findings" / "claude.json", '{"n": 1}')
    _write(ws / "findings" / "codex.json", '{"n": 2}')
    _write(ws / "gaps" / "claude.json", '{"gap": "a"}')
    _write(ws / "manuscript" / "drafts" / "claude" / "results.tex", "claude's results\n")
    _write(ws / "manuscript" / "drafts" / "codex" / "results.tex", "codex's results\n")
    _write(ws / "reviews" / "claude-on-codex.json", '{"verdict": "ok"}')
    _write(ws / "review" / "draft-round-1" / "claude-on-codex.json", '{"round": 1}')
    _write(ws / "review" / "draft-round-1" / "response-codex.md", "codex responds\n")
    _write(ws / "review" / "round-1" / "review.md", "the review\n")
    _write(ws / "manuscript" / "curation" / "rounds" / "1" / "results.tex", "merged v1\n")
    _write(ws / "manuscript" / "curation" / "rounds" / "2" / "results.tex", "merged v2\n")
    _write(ws / "manuscript" / "curation" / "document.yml", "round: 2\nblocks: []\n")
    return ws


def test_agents_are_discovered_from_artifacts_not_from_config(drafted):
    assert provenance.agents(drafted) == ["claude", "codex"]


def test_a_run_that_used_one_agent_gets_one_agent(ws):
    _write(ws / "manuscript" / "drafts" / "solo" / "intro.tex", "alone\n")
    assert provenance.agents(ws) == ["solo"]


def test_a_run_with_nothing_has_no_agents(ws):
    assert provenance.agents(ws) == []


@pytest.mark.parametrize("name", ["a..b", "a.lock", "x y", "a~1", "-x", "HEAD@{0}", "a^b", "a:b"])
def test_a_name_that_is_not_a_safe_ref_is_refused(name):
    """REVIEW FOCUS 1. These are directory names an *agent* chose, and they
    become branch names. Verified against git 2.43.0: the first four are
    refused by `check-ref-format`, and `-x` passes the format check but would
    be read as a flag in an argument position. `drafts.check_name` catches
    none of them."""
    assert provenance.ref_safe(name) is False


@pytest.mark.parametrize("name", ["claude", "codex", "gpt-5", "agent_2", "café"])
def test_an_ordinary_agent_name_is_a_safe_ref(name):
    assert provenance.ref_safe(name) is True


def test_an_unsafe_agent_directory_is_skipped_not_fatal(ws):
    """One bad directory costs that directory, never the whole history."""
    _write(ws / "manuscript" / "drafts" / "claude" / "results.tex", "fine\n")
    (ws / "manuscript" / "drafts" / "a..b").mkdir(parents=True)
    _write(ws / "manuscript" / "drafts" / "a..b" / "results.tex", "hostile\n")

    assert provenance.agents(ws) == ["claude"]
    result = provenance.sync(ws)
    assert "a..b" in result["skipped"]
    assert "refs/heads/draft/claude" in result["commits"]


def test_sync_lays_out_main_exactly_as_the_spec_says(drafted):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    listing = provenance._git(repo, "ls-tree", "-r", "--name-only", "refs/heads/main")
    assert set(listing.splitlines()) == {
        "outline.md",
        "merge_1/sections/results.tex",
        "merge_2/sections/results.tex",
        "merge_2/curation.yml",
        "review_1/review.md",
    }


def test_sync_lays_out_an_agent_branch_exactly_as_the_spec_says(drafted):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    claude = provenance._git(repo, "ls-tree", "-r", "--name-only", "refs/heads/draft/claude")
    assert set(claude.splitlines()) == {
        "findings.json",
        "gaps.json",
        "sections/results.tex",
        "reviews/claude-on-codex.json",
        "review_1/claude-on-codex.json",
    }
    codex = provenance._git(repo, "ls-tree", "-r", "--name-only", "refs/heads/draft/codex")
    assert set(codex.splitlines()) == {
        "findings.json",
        "sections/results.tex",
        "review_1/response-codex.md",
    }, "a response belongs to its author, a review to its reviewer"


def test_every_branch_descends_from_the_empty_root(drafted):
    """Without a common ancestor, `git diff draft/claude main` is not a
    comparison of two histories."""
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    root = provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF)
    for ref in ("refs/heads/main", "refs/heads/draft/claude", "refs/heads/draft/codex"):
        base = provenance._git(repo, "merge-base", ref, root)
        assert base == root, f"{ref} does not descend from the root commit"


def test_syncing_twice_commits_once(drafted):
    first = provenance.sync(drafted)
    second = provenance.sync(drafted)
    assert first["commits"], "the first sync must commit something"
    assert second["commits"] == {}, "an unchanged workspace must not produce a commit"


def test_a_changed_artifact_commits_again_on_that_branch_only(drafted):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    before_main = provenance._ref_sha(repo, "refs/heads/main")
    before_codex = provenance._ref_sha(repo, "refs/heads/draft/codex")

    (drafted / "manuscript" / "drafts" / "claude" / "results.tex").write_text("revised\n")
    result = provenance.sync(drafted)

    assert list(result["commits"]) == ["refs/heads/draft/claude"]
    assert provenance._ref_sha(repo, "refs/heads/main") == before_main
    assert provenance._ref_sha(repo, "refs/heads/draft/codex") == before_codex


def test_a_deleted_repo_is_rebuilt_with_the_history_it_can_still_derive(drafted):
    """The repo is derived, so losing it costs history and nothing else. This
    is what makes best-effort committing correct: the next read reconstructs
    whatever the workspace still supports."""
    import shutil as shutil_mod

    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    before = set(provenance._git(repo, "ls-tree", "-r", "--name-only",
                                 "refs/heads/main").splitlines())
    shutil_mod.rmtree(repo)
    assert not repo.exists()

    provenance.sync(drafted)
    after = set(provenance._git(repo, "ls-tree", "-r", "--name-only",
                                "refs/heads/main").splitlines())
    assert after == before, "the rebuilt history does not match what the workspace holds"
    assert provenance.points(drafted), "points are readable again after a rebuild"


def test_sync_emits_an_event_naming_what_it_did(drafted):
    from scieflow.core import events
    from scieflow.core.run import status

    status.write_status(drafted, status.new_status("r1", "autonomous"))
    provenance.sync(drafted)
    kinds = [e["type"] for e in events.read(drafted)]
    assert "provenance.synced" in kinds


def test_an_unexpected_workspace_shape_costs_that_entry_only(ws):
    """REVIEW FOCUS 4: `findings/claude.json` as a *directory*, and a dangling
    symlink where a section should be. Neither may fail the sync."""
    (ws / "findings" / "claude.json").mkdir(parents=True)
    _write(ws / "manuscript" / "drafts" / "claude" / "results.tex", "real\n")
    (ws / "manuscript" / "drafts" / "claude" / "ghost.tex").symlink_to(ws / "nowhere.tex")

    result = provenance.sync(ws)
    repo = provenance.repo_path(ws)
    listing = provenance._git(repo, "ls-tree", "-r", "--name-only", "refs/heads/draft/claude")
    assert "sections/results.tex" in listing
    assert "ghost.tex" not in listing
    assert "findings.json" not in listing
    assert result["commits"], "the sync still committed what it could read"


def test_two_concurrent_syncs_leave_a_usable_repo(drafted):
    """REVIEW FOCUS 5: `update-ref` is atomic per ref, so the outcome is
    last-writer-wins, never a corrupt repo or a partial tree."""
    import threading

    provenance.ensure_repo(drafted)
    errors = []

    def run():
        try:
            provenance.sync(drafted)
        except Exception as exc:          # noqa: BLE001 — the test is about not corrupting
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    repo = provenance.repo_path(drafted)
    # `fsck` exits non-zero on a corrupt object store and `_git` turns that
    # into ProvenanceError, so this call IS the assertion — there is nothing
    # to compare its output against.
    provenance._git(repo, "fsck", "--no-progress")
    assert provenance._ref_sha(repo, "refs/heads/main"), "main is missing after concurrent syncs"
    listing = provenance._git(repo, "ls-tree", "-r", "--name-only", "refs/heads/main")
    assert "merge_2/sections/results.tex" in listing, f"tree incomplete; errors={errors}"
```

Add to `src/scieflow/core/events.py`'s closed vocabulary:

```python
    "curation.changed", "curation.round",
    "provenance.synced", "provenance.skipped",
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_provenance.py -v`
Expected: FAIL — `AttributeError: module 'scieflow.core.provenance' has no attribute 'agents'`.

- [ ] **Step 3: Write the implementation**

`ref_safe(name)`:

```python
def ref_safe(name: str) -> bool:
    """Whether `name` can be a branch component and a git argument.

    An agent chooses these — they are directory names it writes under
    `manuscript/drafts/` — and they become `refs/heads/draft/<name>`. Three
    separate hazards, so three checks:

    * `drafts.check_name` first, for the path-shaped and control-character
      cases it already owns (a newline in a ref would be its own problem).
    * a leading `-`, because `git diff -x main` reads `-x` as a flag, not a
      ref. `check-ref-format` accepts it.
    * `git check-ref-format` last, for git's own rules — `a..b`, `a.lock`,
      `x y`, `a~1`, `a^b`, `a:b`, `HEAD@{0}`. Asking git is the only way to
      stay right when git's rules change.
    """
```

Implement exactly that order, returning `False` rather than raising for any of the three. `check-ref-format` is run as `[GIT, "check-ref-format", f"refs/heads/draft/{name}"]` through `subprocess.run` with `check=False`, and `returncode == 0` is the answer — do **not** route it through `_git`, which needs a repo and raises.

`agents(ws)` unions the sources listed in the table's right-hand column, filters through `ref_safe`, and returns them sorted. A name that fails `ref_safe` is simply absent.

`sync(ws)`:
- `ensure_repo(ws)`; if `not available()` raise `ProvenanceError` naming git — the caller decides whether that is fatal.
- Build a list of `(target_path, source_path)` pairs per branch from the table. Read each source with `read_bytes()`; skip an entry whose source is not a readable regular file — a directory where a file is expected, a dangling symlink, an unreadable file — and record the skip. `Path.is_file()` follows symlinks and is `False` for a dangling one, which is the check to use.
- Build nested trees from the flat target paths: group by directory, `mktree` the deepest first. Write a small `_tree_from_paths(repo, files: dict[str, bytes]) -> str` helper for this; it is the only non-obvious part of the module and it is used for every branch.
- For each branch, compare the built tree against `_git(repo, "rev-parse", f"{ref}^{{tree}}")` when the ref exists; commit only on a difference, parented on the existing tip or on the root commit when the branch is new.
- Emit `provenance.synced` with the refs committed and the agents found, and `provenance.skipped` when anything was skipped. Guard both `emit` calls: a run directory without `status.yml` is not a reason for a sync to fail, and `events.emit` reads it.
- Return `{"commits": {...}, "agents": [...], "skipped": [...]}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_provenance.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/provenance.py src/scieflow/core/events.py tests/core/test_provenance.py
git commit -m "feat(core): project a run's artifacts onto main and per-agent branches"
```

---

### Task 3: Reading — points and diffs

**Files:**
- Modify: `src/scieflow/core/provenance.py`
- Test: `tests/core/test_provenance.py` (append)

**Interfaces:**
- Consumes: everything Tasks 1–2 produced.
- Produces:
  `provenance.points(ws) -> list[dict]` — each `{"ref": "main:merge_2", "label": "Merge round 2", "kind": "merge"|"review"|"draft"|"outline", "commit": "<sha>", "at": "<iso>"}`;
  `provenance.diff(ws, a, b) -> dict` — `{"text": str, "truncated": bool, "a": str, "b": str}`;
  `provenance.DIFF_LIMIT = 200_000`.

**A point is a `<ref>:<tree path>` pair** — `main:merge_2`, `draft/claude:sections`. It is what the panel lists, what `diff` accepts, and the whitelist the query parameters are compared against as whole strings.

**Ref validation is the security requirement of this task.** `a` and `b` reach `git diff`. Each must equal a string `points()` returned, compared as a whole string — not prefix-matched, not sanitised, not merely checked for `..`. An unvalidated `a` could be argument-shaped (`--output=/tmp/x`), which no containment check would catch.

- [ ] **Step 1: Write the failing test**

```python
# appended to tests/core/test_provenance.py

def test_points_lists_what_the_run_has(drafted):
    provenance.sync(drafted)
    refs = [p["ref"] for p in provenance.points(drafted)]
    assert "main:merge_1" in refs
    assert "main:merge_2" in refs
    assert "main:review_1" in refs
    assert "draft/claude:sections" in refs
    assert "draft/codex:sections" in refs
    for point in provenance.points(drafted):
        assert point["commit"] and point["at"] and point["label"]


def test_a_run_with_no_history_has_no_points(ws):
    """REVIEW FOCUS 2: an ordinary state — the panel says so rather than
    raising or rendering an empty shell."""
    provenance.sync(ws)
    assert provenance.points(ws) == []


def test_points_are_stable_across_syncs(drafted):
    provenance.sync(drafted)
    before = [p["ref"] for p in provenance.points(drafted)]
    provenance.sync(drafted)
    assert [p["ref"] for p in provenance.points(drafted)] == before


def test_a_round_to_round_diff_shows_only_what_changed(drafted):
    provenance.sync(drafted)
    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert "-merged v1" in result["text"]
    assert "+merged v2" in result["text"]
    assert result["truncated"] is False


def test_a_draft_against_a_round_diff_works(drafted):
    provenance.sync(drafted)
    result = provenance.diff(drafted, "draft/claude:sections", "main:merge_1/sections")
    assert "claude's results" in result["text"]
    assert "merged v1" in result["text"]


@pytest.mark.parametrize("hostile", [
    "--output=/tmp/pwned",
    "-x",
    "main:merge_1 --output=/tmp/pwned",
    "../../etc/passwd",
    "main:../../../etc",
    "refs/heads/main",          # a real ref, but not a point `points()` returned
    "",
])
def test_a_ref_that_is_not_a_listed_point_is_refused(drafted, hostile):
    """The whitelist is whole-string equality against `points()`. An
    argument-shaped value is the case a containment check would miss."""
    provenance.sync(drafted)
    with pytest.raises(provenance.ProvenanceError, match="not a point"):
        provenance.diff(drafted, hostile, "main:merge_2")
    with pytest.raises(provenance.ProvenanceError, match="not a point"):
        provenance.diff(drafted, "main:merge_2", hostile)


def test_no_file_is_created_by_a_hostile_ref(drafted, tmp_path):
    provenance.sync(drafted)
    target = tmp_path / "pwned"
    with pytest.raises(provenance.ProvenanceError):
        provenance.diff(drafted, f"--output={target}", "main:merge_2")
    assert not target.exists(), "git was handed an option as a ref"


def test_a_large_diff_is_truncated_and_says_so(drafted):
    """REVIEW FOCUS 3: a big diff must not be read whole into a page."""
    big = "x" * (provenance.DIFF_LIMIT + 5000) + "\n"
    (drafted / "manuscript" / "curation" / "rounds" / "2" / "results.tex").write_text(big)
    provenance.sync(drafted)
    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert result["truncated"] is True
    assert len(result["text"]) <= provenance.DIFF_LIMIT + 200
    assert "truncated" in result["text"].lower()


def test_a_binary_artifact_diffs_without_dumping_bytes(drafted):
    """REVIEW FOCUS 3: `manuscript/` can hold a compiled PDF or a figure."""
    pdf = drafted / "manuscript" / "curation" / "rounds" / "2" / "figure.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 20)
    provenance.sync(drafted)
    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert "Binary files" in result["text"] or "figure.pdf" in result["text"]
    assert "\x00" not in result["text"], "raw bytes reached the diff text"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_provenance.py -k "points or diff or hostile or truncated or binary" -v`
Expected: FAIL — `module 'scieflow.core.provenance' has no attribute 'points'`.

- [ ] **Step 3: Write the implementation**

`points(ws)` reads the repo rather than the workspace, so it reflects what is actually committed:
- for `refs/heads/main`, list its tree's top level (`ls-tree --name-only main`) and emit a point per `merge_<n>` and `review_<N>` directory, plus one for `outline.md` when present.
- for each `refs/heads/draft/<agent>`, emit a point for `sections` when that path exists in the tree, and one per `review_<N>` directory.
- each point carries the branch tip's sha and committer date, read once per branch with `log -1 --format=%H%x00%cI`.
- sort newest-round-first within a branch, `main` before the draft branches, so the panel reads top-down as the most recent work.
- a ref that does not exist contributes nothing; a repo with no branches yields `[]`.

`diff(ws, a, b)`:
- build `allowed = {p["ref"] for p in points(ws)}` and raise `ProvenanceError(f"not a point: {a!r}")` unless `a in allowed`, same for `b`. Whole-string equality, and do this **before** any git call.
- run `_git(repo, "diff", "--no-color", a, b)` — note `a` and `b` are already `<ref>:<path>` strings, which `git diff` accepts as tree-ish arguments.
- truncate to `DIFF_LIMIT` characters, appending a line stating the diff was truncated, and set `truncated`.
- the returned `text` must be safe to render: `_git` already decodes as text, and git itself reports binary files as `Binary files … differ` rather than emitting bytes. Pass `errors="replace"` when decoding so a malformed byte cannot raise — the same choice `service._log_tail` made.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_provenance.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/provenance.py tests/core/test_provenance.py
git commit -m "feat(core): list provenance points and diff between them, refs whitelisted"
```

---

### Task 4: The service layer and the guarded sync after a merge round

**Files:**
- Modify: `src/scieflow/core/service.py`
- Test: `tests/core/test_service.py` (append), `tests/core/test_merge_round.py` (append)

**Interfaces:**
- Consumes: `provenance.available`, `ensure_repo`, `sync`, `points`, `diff`, `ProvenanceError` (Tasks 1–3).
- Produces:
  `service.manuscript_history(project, slug) -> dict` — `{"available": bool, "points": [...], "reason": str}`;
  `service.manuscript_diff(project, slug, a, b) -> dict` — `{"text": str, "truncated": bool, "a": str, "b": str}`.

**`manuscript_history` never raises for an ordinary state.** `git` absent, no history yet, a repo that had to be rebuilt — all return the dict with `available`/`points`/`reason` filled in. Only a genuinely malformed `slug` raises `ServiceError`, via `_ws`.

**The sync after a merge round is best-effort and must never fail the round.** The round happened and cost budget; losing a commit is not worth losing that.

- [ ] **Step 1: Write the failing test**

```python
# appended to tests/core/test_service.py

def test_manuscript_history_reports_no_history_for_a_fresh_run(project):
    view = service.manuscript_history(project, "r1")
    assert view["available"] is True
    assert view["points"] == []
    assert "no history" in view["reason"].lower()


def test_manuscript_history_lists_points_after_a_round(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")

    view = service.manuscript_history(project, "r1")
    assert view["available"] is True
    assert "main:merge_1" in [p["ref"] for p in view["points"]]


def test_manuscript_history_degrades_when_git_is_missing(project, monkeypatch):
    from scieflow.core import provenance

    monkeypatch.setattr(provenance.shutil, "which", lambda name: None)
    view = service.manuscript_history(project, "r1")
    assert view["available"] is False
    assert view["points"] == []
    assert "git" in view["reason"].lower(), "the page must be able to say why"


def test_manuscript_diff_refuses_a_ref_that_is_not_a_point(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")
    service.manuscript_history(project, "r1")

    with pytest.raises(service.ServiceError, match="not a point"):
        service.manuscript_diff(project, "r1", "--output=/tmp/x", "main:merge_1")


def test_manuscript_diff_translates_a_provenance_error(project):
    with pytest.raises(service.ServiceError):
        service.manuscript_diff(project, "r1", "main:merge_1", "main:merge_2")


def test_manuscript_history_refuses_an_unknown_run(project):
    with pytest.raises(service.ServiceError):
        service.manuscript_history(project, "nope")
```

```python
# appended to tests/core/test_merge_round.py

def test_a_successful_round_syncs_the_provenance_repo(project, curated, responder):
    from scieflow.core import provenance

    service.merge_round(project, "r1")
    ws = project.run_dir("r1")
    assert provenance.repo_path(ws).is_dir(), "a successful round did not sync"


def test_a_failing_provenance_sync_does_not_fail_the_round(project, curated, responder,
                                                           monkeypatch):
    """The round happened and cost budget. Losing a commit is not worth losing
    that. Falsify by removing the guard around the sync call: this test then
    fails with the raised ProvenanceError instead of returning a round."""
    from scieflow.core import provenance

    def boom(ws):
        raise provenance.ProvenanceError("git exploded")

    monkeypatch.setattr(service.provenance, "sync", boom)
    result = service.merge_round(project, "r1")
    assert result["round"] == 2, "the round must still advance"
    assert result["turn"]["job"]["state"] == service.MERGE_SUCCESS_STATE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_service.py tests/core/test_merge_round.py -k "manuscript or provenance" -v`
Expected: FAIL — `module 'scieflow.core.service' has no attribute 'manuscript_history'`.

- [ ] **Step 3: Write the implementation**

Import `provenance` in `service.py` beside the other core imports — the merge-round test monkeypatches `service.provenance.sync`, so the module must be reachable by that name.

```python
def manuscript_history(project: Project, slug: str) -> dict:
    """The run's provenance points, and why there are none when there are none.

    Never raises for an ordinary state: `git` absent, a run that has produced
    nothing yet, and a repo that had to be rebuilt all come back as data the
    page can render. Syncs first, so opening the page catches up anything the
    workflow wrote since the last merge round.
    """
    ws = _ws(project, slug)
    if not provenance.available():
        return {"available": False, "points": [],
                "reason": "git is not installed, so this run has no manuscript history; "
                          "the drafts and rounds above are unaffected"}
    try:
        provenance.sync(ws)
        points = provenance.points(ws)
    except provenance.ProvenanceError as exc:
        return {"available": True, "points": [], "reason": f"no history yet: {exc}"}
    reason = "" if points else ("no history yet — it appears once an agent has drafted "
                                "or a merge round has completed")
    return {"available": True, "points": points, "reason": reason}


def manuscript_diff(project: Project, slug: str, a: str, b: str) -> dict:
    ws = _ws(project, slug)
    try:
        return provenance.diff(ws, a, b)
    except provenance.ProvenanceError as exc:
        raise ServiceError(str(exc)) from exc
```

In `merge_round`, after the success branch has advanced the round, sync best-effort:

```python
    if turn["job"]["state"] == MERGE_SUCCESS_STATE:
        advanced = curation.advance_round(ws)
        # Best-effort: a sync failure must not fail a round that genuinely
        # happened and cost budget. The repo is derived, so the next page load
        # rebuilds whatever this missed — and because rounds are preserved as
        # paths rather than commit boundaries, a skipped sync costs nothing but
        # a commit.
        try:
            provenance.sync(ws)
        except provenance.ProvenanceError as exc:
            events.emit(ws, "provenance.skipped", "system", why=str(exc))
        return {"round": advanced, "turn": turn}
```

Keep the `except` to `provenance.ProvenanceError` specifically — a bare `except Exception` here would swallow a programming error in the sync for the life of the feature.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core -v && uv run pytest -q`
Expected: all pass; full suite green. Falsify the guard test by removing the `try`/`except` around `provenance.sync` — it must fail — then restore it and record what the failure said.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/service.py tests/core/test_service.py tests/core/test_merge_round.py
git commit -m "feat(core): expose the manuscript history, and sync it after a merge round"
```

---

### Task 5: The history and diff panels

**Files:**
- Modify: `src/scieflow/web/pages.py`, `src/scieflow/web/templates/drafts.html`
- Test: `tests/web/test_provenance_panel.py` (create)

**Interfaces:**
- Consumes: `service.manuscript_history`, `service.manuscript_diff` (Task 4).
- Produces: no new route. `GET /runs/{slug}/drafts` gains optional `diff_a` and `diff_b` query parameters.

**The context key must not be `history`.** `drafts_page` already passes `history` — that is `service.curation_history`, the curation's own version list, which the existing version panel renders. Use `provenance` for the new one. A shared key would let one silently shadow the other depending on dict order, which is exactly the defect a Task-5 fix round in the C plan was spent on.

**No new mutating route.** Both panels are read-only, driven by query parameters on the existing GET. So there is nothing to add to `MUTATING_PATHS` or `SAMPLES`, and no CSRF field on either panel.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_provenance_panel.py
"""The manuscript-history panel and its diff view, on the workbench page."""

import pytest


@pytest.fixture
def merged(project):
    """A run with two merge rounds, so there is something to diff."""
    ws = project.run_dir("r1")
    for n, text in ((1, "merged v1\n"), (2, "merged v2\n")):
        d = ws / "manuscript" / "curation" / "rounds" / str(n)
        d.mkdir(parents=True)
        (d / "results.tex").write_text(text)
    return ws


def test_the_panel_lists_the_rounds(client, merged):
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "main:merge_1" in page.text
    assert "main:merge_2" in page.text


def test_a_run_with_no_history_says_so(client, project):
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "no history yet" in page.text.lower()
    assert "main:merge" not in page.text


def test_the_panel_says_so_when_git_is_missing(client, merged, monkeypatch):
    from scieflow.core import provenance

    monkeypatch.setattr(provenance.shutil, "which", lambda name: None)
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "git is not installed" in page.text
    assert "merged v2" in page.text or "Compile" in page.text, (
        "the rest of the workbench must still render")


def test_a_diff_is_rendered_when_two_points_are_given(client, merged):
    page = client.get("/runs/r1/drafts?diff_a=main:merge_1&diff_b=main:merge_2")
    assert page.status_code == 200
    assert "merged v1" in page.text and "merged v2" in page.text


def test_a_hostile_diff_ref_comes_back_as_a_message_not_a_500(client, merged):
    page = client.get("/runs/r1/drafts?diff_a=--output=/tmp/pwned&diff_b=main:merge_2")
    assert page.status_code == 200
    assert "not a point" in page.text
    import pathlib
    assert not pathlib.Path("/tmp/pwned").exists()


def test_the_curation_version_panel_still_works(client, merged):
    """`history` (the curation's versions) and `provenance` (the manuscript's)
    are different lists. A shared context key would let one shadow the other
    — the defect a whole fix round went to in the C plan."""
    from scieflow.core.run import curation

    curation.add_own(merged, "a passage of my own")
    page = client.get("/runs/r1/drafts")
    assert "a passage of my own" in page.text
    assert "main:merge_1" in page.text, "the provenance panel is also present"


def test_diff_text_is_escaped_not_raw_html(client, merged):
    """A diff carries agent-written text straight into HTML."""
    (merged / "manuscript" / "curation" / "rounds" / "2" / "results.tex").write_text(
        '<script>alert("xss")</script>\n')
    page = client.get("/runs/r1/drafts?diff_a=main:merge_1&diff_b=main:merge_2")
    assert "<script>alert" not in page.text
    assert "&lt;script&gt;" in page.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_provenance_panel.py -v`
Expected: FAIL — the page renders without any of these strings.

- [ ] **Step 3: Write the implementation**

In `pages.py`, extend the existing handler's signature and context. It stays `def`:

```python
@router.get("/runs/{slug}/drafts", response_class=HTMLResponse)
def drafts_page(request: Request, slug: str, error: str = "",
                diff_a: str = "", diff_b: str = "") -> HTMLResponse:
```

Add to the context, keeping `**view` last as it already is:

```python
        "provenance": service.manuscript_history(project, slug),
        "diff": _manuscript_diff(project, slug, diff_a, diff_b),
```

`_manuscript_diff(project, slug, a, b)` returns `None` when either is empty, and otherwise calls `service.manuscript_diff`, catching `ServiceError` and returning `{"error": str(exc)}` so a hostile ref renders as a message rather than a 500. Note the key is `provenance`, **not** `history` — `history` is already the curation's version list.

In `drafts.html`, two regions:
- the history panel: when `provenance.available` is false or `provenance.points` is empty, render `provenance.reason` as plain text and nothing else. Otherwise a list of points, each labelled, each a link back to this page with `diff_a`/`diff_b` set — a pair of selects submitting a GET form is the simplest shape and needs no CSRF field.
- the diff panel: when `diff` is set, render `diff.error` when present, else `diff.text` in a `<pre>`, with the truncation stated when `diff.truncated`.

Render everything with plain `{{ … }}`. **No `|safe`** — a diff carries agent-written text.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass, including `test_read_only.py`, `test_mutations.py` and `test_async_routes.py` — the route inventory is unchanged, which is itself the check that no mutating route was added.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web/test_provenance_panel.py
git commit -m "feat(web): read the manuscript's history and diff it on the workbench"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/web.md`, `docs/runs.md`
- Not modified: `docs/cli.md` — this plan adds no CLI command. Say so in your report rather than leaving it ambiguous.
- Test: `uv run --group docs mkdocs build --strict`

**Interfaces:** consumes the behaviour of Tasks 1–5. Produces no code.

Document it where the workbench is already documented, matching that section's voice and depth — read `docs/web.md`'s draft-workbench subsection and `docs/runs.md`'s charter section first; this repo's docs explain *why*, not just *what*.

- [ ] **Step 1: Write the documentation**

Cover, verifying each claim against the code rather than against this plan:

- Where the history lives (`workspace/<slug>/provenance.git`), that it is a real git repo you can point any git tool at, and that it is **bare** so there is nothing checked out.
- The layout: `main` for what the run produced collectively, `draft/<agent>` for what each agent produced alone, `merge_N/` for the workbench's merge rounds and `review_N/` for `paper-review`'s draft rounds — and that these are two different counters, which is why they are named apart.
- That branch names come from the agents the run actually used, discovered from its own artifacts, so adding an agent needs no configuration.
- The three diffs a person will actually want, as commands they can run themselves:
  `git diff main:merge_1 main:merge_2`, `git diff draft/claude:sections main:merge_1/sections`,
  `git diff draft/claude:review_1 draft/claude:review_2`.
- That the repo is **derived**: the workspace files are the truth, deleting it costs history only, and the next page load rebuilds what it can. State that this is why a failed sync never fails a round.
- That it is local only — nothing is pushed anywhere, and DVC still carries the bytes.
- That `git` is optional: without it the panel says so and the workbench is unaffected.
- The on-disk layout as a block, as `docs/web.md` already does for the workbench.

In `docs/runs.md`, add a cross-reference from wherever run artifacts are described, pointing at the new section. Check whether `AGENTS.md` keeps an inventory this belongs in; **it does not** — it has only scattered feature-specific mentions, so add nothing there, and say in your report that you checked.

- [ ] **Step 2: Verify**

Run: `uv run --group docs mkdocs build --strict && ./scripts/check_legacy.sh && uv run pytest -q`
Expected: zero mkdocs warnings; 25/25 `ok`; suite green.

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs: the manuscript's git history, and how to read it"
```
