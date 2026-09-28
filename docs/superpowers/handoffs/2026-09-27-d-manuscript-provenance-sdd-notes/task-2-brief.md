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

