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


def test_the_root_commit_is_a_known_sha(ws):
    """A literal SHA, not a comparison between two freshly built repos.

    This task's own history has tripped the same trap three times: two
    `commit-tree` calls made in the same wall-clock second, with the same
    tree/parents/message/identity, produce the *same* SHA even when one of
    the inputs that is supposed to matter (the date) is wrong — so a test
    comparing "repo A's root" to "repo B's root" built moments apart proves
    nothing about whether the date is actually pinned. It already fooled
    `test_ensure_repo_is_idempotent` (a full unconditional reparent still
    passed it), the embedded-tab tree test (no bearing on the mktree/-z
    fix either way, noted at the time), and a since-deleted version of
    this very test that built two repos back to back and compared their
    root SHAs — which stayed green even with `when=_ROOT_COMMIT_DATE`
    removed from `ensure_repo` entirely, because both commits still landed
    in the same second.

    A hardcoded constant closes the class rather than one instance of it:
    `_root`'s SHA is fully determined by five inputs this module fixes —
    the empty tree (`4b825dc642cb6eb9a060e54bf8d69288fbee4904`), no
    parents, the message `"empty root"`, the `ScieFlow
    <provenance@scieflow.local>` author/committer identity, and
    `_ROOT_COMMIT_DATE` (`"@0 +0000"`) for both dates — so computing it
    once and asserting equality against that literal means changing *any*
    of those five silently breaks this test, with no wall-clock coincidence
    able to rescue it. The constant below was produced by this exact
    module (`provenance.ensure_repo` against a scratch workspace) and
    independently cross-checked with a bare `git commit-tree` call built
    from the same five inputs by hand; do not regenerate it from a fresh
    call in this test, since that reintroduces exactly the same-second
    blind spot this test exists to close.
    """
    repo = provenance.ensure_repo(ws)
    sha = provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF)
    assert sha == "90f8e5daab6c2ed97a857cd6f9a6fe65c1158122"


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
