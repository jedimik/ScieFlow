"""The per-run git repo that carries a manuscript's history.

Bare, no working tree: every write is plumbing (`hash-object`, `mktree`,
`commit-tree`, `update-ref`). These tests drive real git — it is present on
this host at 2.43.0 — because the whole point of the module is what git
actually does with the trees it is handed.
"""

import re
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


def test_ref_safe_does_not_leak_the_parent_environment(monkeypatch):
    """Fix round 1, item 2: `check-ref-format` is a git subprocess like any
    other in this module, so it must run with `_env()`'s environment, not
    `os.environ` — a hostile parent (a model API key) must never reach it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-travel")
    seen_env = {}
    real_run = provenance.subprocess.run

    def spy(argv, **kwargs):
        if argv[:2] == [provenance.GIT, "check-ref-format"]:
            seen_env.update(kwargs.get("env") or {})
        return real_run(argv, **kwargs)

    monkeypatch.setattr(provenance.subprocess, "run", spy)
    provenance.ref_safe("claude")
    assert seen_env, "check-ref-format was not called via subprocess.run as expected"
    assert "ANTHROPIC_API_KEY" not in seen_env


def test_a_symlink_cycle_in_an_ancestor_of_the_workspace_is_a_provenance_error(tmp_path):
    """Fix round 2: `ws.resolve()` (added in fix round 1, as the
    containment boundary for `put`) runs before `ensure_repo` or any `_git`
    call gets a chance to turn a failure into `ProvenanceError` — so a
    symlink cycle in an *ancestor* of the workspace (not an artifact under
    it) must be caught right there, or it reaches the caller as a bare
    `RuntimeError`. Pre-fix `5b98066` has no such call and instead fails
    inside `ensure_repo`'s `_git(repo, "init", ...)`, which already wraps
    the failure as `ProvenanceError` — so this is a regression window that
    exists only in the fix-round-1 code between the module docstring's
    "every caller has exactly one exception to handle" contract and this
    one new line.

    `loop_a`/`loop_b` are a genuine two-node cycle, not merely a long
    chain: nothing can be created under `ws` (there is no real directory to
    create it in), so this calls `sync` directly on the unusable `Path`
    rather than going through the `ws` fixture."""
    loop_a = tmp_path / "loop_a"
    loop_b = tmp_path / "loop_b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)
    ws = loop_a / "workspace" / "r1"

    with pytest.raises(provenance.ProvenanceError):
        provenance.sync(ws)


def test_an_unsafe_agent_directory_is_skipped_not_fatal(ws):
    """One bad directory costs that directory, never the whole history."""
    _write(ws / "manuscript" / "drafts" / "claude" / "results.tex", "fine\n")
    (ws / "manuscript" / "drafts" / "a..b").mkdir(parents=True)
    _write(ws / "manuscript" / "drafts" / "a..b" / "results.tex", "hostile\n")

    assert provenance.agents(ws) == ["claude"]
    result = provenance.sync(ws)
    assert "a..b" in result["skipped"]["agents"]
    assert "refs/heads/draft/claude" in result["commits"]


def test_a_symlinked_agent_directory_does_not_leak_host_content(ws, tmp_path):
    """CRITICAL (fix round 1): an agent chooses this directory's name, and
    `manuscript/drafts/evil -> /outside` must not surface `evil` as a
    discovered agent, or copy the symlink target's content into the
    committed, exportable repo. `drafts.agents()` on the identical
    workspace already refuses this; discovery has to go through it (or the
    same resolve-and-contain check) rather than a bare `is_dir()`."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.tex").write_text("HOST SECRET CONTENT\n")
    _write(ws / "manuscript" / "drafts" / "claude" / "results.tex", "real draft\n")
    (ws / "manuscript" / "drafts" / "evil").symlink_to(outside)

    assert provenance.agents(ws) == ["claude"]
    result = provenance.sync(ws)
    repo = provenance.repo_path(ws)
    assert provenance._ref_sha(repo, "refs/heads/draft/evil") is None
    for ref in ("refs/heads/main", "refs/heads/draft/claude"):
        listing = provenance._git(repo, "ls-tree", "-r", "--name-only", ref)
        assert "secret.tex" not in listing
    assert result["commits"], "the sync still committed what it safely could"


def test_a_symlinked_artifact_file_is_skipped_not_copied(ws, tmp_path):
    """CRITICAL (fix round 1): a symlink at any single artifact path (not
    just a whole agent directory) that resolves outside `ws` must be
    skipped, reported by its run-relative path, and never read — this is
    `put`'s containment check, the second half of the same hole."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----\n")
    _write(ws / "manuscript" / "drafts" / "claude" / "results.tex", "real\n")
    (ws / "findings").mkdir(parents=True)
    (ws / "findings" / "claude.json").symlink_to(outside / "id_rsa")

    result = provenance.sync(ws)
    repo = provenance.repo_path(ws)
    listing = provenance._git(repo, "ls-tree", "-r", "--name-only", "refs/heads/draft/claude")
    assert "findings.json" not in listing
    assert "findings/claude.json" in result["skipped"]["artifacts"]
    assert result["commits"], "the sync still committed what it safely could"


def test_a_non_canonical_round_directory_is_refused(ws):
    """Ruling 3: `round-01` and `round-1` (or `1` and `01` under
    `manuscript/curation/rounds/`) must not collapse onto the same target
    with the winner decided by directory-scan order — that would make a
    committed tree nondeterministic across syncs."""
    _write(ws / "manuscript" / "drafts" / "claude" / "results.tex", "fine\n")
    _write(ws / "manuscript" / "curation" / "rounds" / "01" / "results.tex", "v1\n")
    _write(ws / "review" / "round-01" / "review.md", "the review\n")

    result = provenance.sync(ws)
    repo = provenance.repo_path(ws)
    listing = provenance._git(repo, "ls-tree", "-r", "--name-only", "refs/heads/main")
    assert "merge_1/sections/results.tex" not in listing
    assert "review_1/review.md" not in listing
    assert "manuscript/curation/rounds/01" in result["skipped"]["artifacts"]
    assert "review/round-01" in result["skipped"]["artifacts"]


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


def test_sync_emits_a_skipped_event_when_something_is_skipped(ws):
    """Fix round 1, item 4: only `provenance.synced` had a test; nothing
    asserted `provenance.skipped` is ever actually emitted."""
    from scieflow.core import events
    from scieflow.core.run import status

    status.write_status(ws, status.new_status("r1", "autonomous"))
    _write(ws / "manuscript" / "drafts" / "claude" / "results.tex", "fine\n")
    (ws / "manuscript" / "drafts" / "a..b").mkdir(parents=True)
    _write(ws / "manuscript" / "drafts" / "a..b" / "results.tex", "hostile\n")

    provenance.sync(ws)
    kinds = [e["type"] for e in events.read(ws)]
    assert "provenance.skipped" in kinds


def test_a_failed_synced_emission_does_not_suppress_the_skipped_emission(ws, monkeypatch):
    """Fix round 1, item 3: the two `emit` calls must be independently
    guarded. A single `try` around both means a failure emitting
    `provenance.synced` also silently swallows `provenance.skipped`,
    burying the one record that the event log itself failed."""
    from scieflow.core import events

    _write(ws / "manuscript" / "drafts" / "claude" / "results.tex", "fine\n")
    (ws / "manuscript" / "drafts" / "a..b").mkdir(parents=True)
    _write(ws / "manuscript" / "drafts" / "a..b" / "results.tex", "hostile\n")

    calls = []
    real_emit = events.emit

    def flaky_emit(ws_, type_, **kwargs):
        calls.append(type_)
        if type_ == "provenance.synced":
            raise RuntimeError("boom")
        return real_emit(ws_, type_, **kwargs)

    monkeypatch.setattr(provenance.events, "emit", flaky_emit)
    provenance.sync(ws)  # must not raise
    assert "provenance.skipped" in calls, "the second emit must still be attempted"


def test_a_non_utf8_source_is_committed_byte_exact(ws):
    """Ruling 1: hashing by path (`--no-filters`) makes an artifact's
    encoding irrelevant. A latin-1 `.tex` file is ordinary in LaTeX work; it
    must land in history exactly as written, not get silently dropped for
    failing a UTF-8 decode."""
    raw = "R\xe9sultats en fran\xe7ais\n".encode("latin-1")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    path = ws / "manuscript" / "drafts" / "claude" / "results.tex"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)

    provenance.sync(ws)
    repo = provenance.repo_path(ws)
    blob_sha = provenance._git(
        repo, "rev-parse", "refs/heads/draft/claude:sections/results.tex")
    out = subprocess.run(
        ["git", "--git-dir", str(repo), "cat-file", "-p", blob_sha],
        capture_output=True, check=True,
    )
    assert out.stdout == raw


def test_a_name_used_as_both_a_file_and_a_directory_is_refused(ws):
    """Fix round 1, item 1: `mktree` would otherwise happily produce two
    entries both named `"a"` — `fsck --strict`'s `duplicateEntries` — which
    nothing in today's path table can trigger, but a future table row
    silently could. Uses a real, readable, non-directory stand-in file for
    both entries: this must fail on the collision itself, not incidentally
    on `hash-object` refusing a directory."""
    repo = provenance.ensure_repo(ws)
    probe = ws / "probe.txt"
    probe.write_text("x\n")
    with pytest.raises(provenance.ProvenanceError, match="both a file and a directory"):
        provenance._tree_from_paths(repo, {
            "a": probe,
            "a/b": probe,
        })


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


# ---------------------------------------------------------------------------
# Task 3: points() and diff()
# ---------------------------------------------------------------------------


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


def test_points_are_a_closed_set_in_a_stated_order(drafted):
    """Fix round 1, Important 2: the brief states an ordering and
    `points`'s own docstring claims it, but nothing before this asserted
    it, and `test_points_lists_what_the_run_has`'s `in`-only checks hold
    under any deterministic order — reordering the `_main_points` loops,
    or running the draft-branch loop before `main`, would silently reverse
    a panel Task 5 renders, with every existing test still green. This
    pins the full ordered list in one assertion: `main` before the draft
    branches (agents sorted by name), newest round first within each
    branch, each `merge_<n>` immediately followed by its `merge_<n>/
    sections` sub-point when the round has one, and `outline.md` last on
    `main` since it predates every round."""
    provenance.sync(drafted)
    refs = [p["ref"] for p in provenance.points(drafted)]
    assert refs == [
        "main:merge_2",
        "main:merge_2/sections",
        "main:merge_1",
        "main:merge_1/sections",
        "main:review_1",
        "main:outline.md",
        "draft/claude:sections",
        "draft/claude:review_1",
        "draft/codex:sections",
        "draft/codex:review_1",
    ]


def test_a_sub_point_names_its_parent_point(drafted):
    """Fix round 1, Important 3: 20 rounds means 40 points, 20 of them
    sub-points indistinguishable in shape from the round-level point they
    refine (same `kind`, `commit`, `at` — only a `/sections` suffix on
    `ref` and a trailing " sections" on `label` set them apart). A `parent`
    field lets a consumer filter to top-level points without suffix-
    parsing `ref` itself. `diff`'s whitelist is untouched — every point,
    sub-point included, is still just one entry in a flat set of valid
    `ref` values."""
    provenance.sync(drafted)
    by_ref = {p["ref"]: p for p in provenance.points(drafted)}
    assert by_ref["main:merge_1/sections"]["parent"] == "main:merge_1"
    assert by_ref["main:merge_2/sections"]["parent"] == "main:merge_2"
    for ref in ("main:merge_1", "main:merge_2", "main:review_1", "main:outline.md",
                "draft/claude:sections", "draft/claude:review_1",
                "draft/codex:sections", "draft/codex:review_1"):
        assert by_ref[ref].get("parent") is None, f"{ref} is not a sub-point"
    # the sub-point must still be a plain member of diff's whitelist
    result = provenance.diff(drafted, "main:merge_1/sections", "main:merge_2/sections")
    assert result["text"] is not None


def test_a_run_with_no_history_has_no_points(ws):
    """REVIEW FOCUS 2: an ordinary state — the panel says so rather than
    raising or rendering an empty shell."""
    provenance.sync(ws)
    assert provenance.points(ws) == []


def test_a_run_whose_repo_was_never_synced_has_no_points(ws):
    """`points` is a pure reader: it must not create a repo (that's
    `sync`'s/`ensure_repo`'s job) just to answer "what's here" about one
    that was never built."""
    assert provenance.points(ws) == []
    assert not provenance.repo_path(ws).exists()


def test_points_are_stable_across_syncs(drafted):
    provenance.sync(drafted)
    before = [p["ref"] for p in provenance.points(drafted)]
    provenance.sync(drafted)
    assert [p["ref"] for p in provenance.points(drafted)] == before


def test_points_are_stable_after_the_repo_is_rebuilt(drafted):
    """Task 2's deleted-repo test ended with a forward reference to this
    task's own deliverable (`points()`) and had to drop it. This closes
    that gap: the repo is derived, so after losing and rebuilding it,
    `points()` must report the same refs it did before — the reader-side
    half of "the repo is derived and rebuildable" that Task 2 could not
    cover itself."""
    provenance.sync(drafted)
    before = {p["ref"] for p in provenance.points(drafted)}
    assert before, "the fixture must produce at least one point to make this test meaningful"

    shutil.rmtree(provenance.repo_path(drafted))
    assert not provenance.repo_path(drafted).exists()

    provenance.sync(drafted)
    after = {p["ref"] for p in provenance.points(drafted)}
    assert after == before


def test_a_round_to_round_diff_shows_only_what_changed(drafted):
    provenance.sync(drafted)
    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert "-merged v1" in result["text"]
    assert "+merged v2" in result["text"]
    assert result["truncated"] is False


def test_a_draft_against_a_round_diff_works(drafted):
    """Fix round 1, Important 1: the two substring checks below both also
    pass against a *coarse*, path-disjoint comparison
    (`draft/claude:sections` vs `main:merge_1`, no `/sections` on the
    second side) — that diff reads as "delete every line of the draft,
    add every line of the round" and still contains both substrings, since
    they appear as a deletion and an addition respectively either way.
    That coarse comparison is exactly the wrong answer this sub-point
    exists to avoid (see the module's points()/`_main_points` docstrings),
    so this test needs to assert the *hunk shape* — a single real,
    line-level diff — not just substring presence, or it cannot tell a
    meaningful comparison from a meaningless one."""
    provenance.sync(drafted)
    result = provenance.diff(drafted, "draft/claude:sections", "main:merge_1/sections")
    assert "-claude's results" in result["text"]
    assert "+merged v1" in result["text"]
    assert "deleted file" not in result["text"], (
        "the two sides were compared at different depths")
    assert "new file" not in result["text"], (
        "the two sides were compared at different depths")
    assert result["text"].count("diff --git") == 1


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


def test_a_large_diff_truncates_on_a_line_boundary(drafted):
    """Fix round 1, Minor 2: cutting at character `DIFF_LIMIT` lands
    wherever that offset happens to fall inside a line, leaving a
    fragment no diff parser or renderer can make sense of. Many short,
    distinctly-numbered lines (21 bytes each, which does not evenly
    divide `DIFF_LIMIT`) make a mid-line cut near-certain unless the
    truncation itself seeks back to the last newline; the last surviving
    content line must then be one of the exact lines written, never a
    partial one."""
    lines = "".join(f"line-{i:07d}-fill\n" for i in range(20000))  # 21 bytes/line
    (drafted / "manuscript" / "curation" / "rounds" / "2" / "results.tex").write_text(lines)
    provenance.sync(drafted)
    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert result["truncated"] is True

    body, sep, _notice = result["text"].rpartition("\n… ")
    assert sep, "the truncation notice must be present and findable"
    last_line = body.rsplit("\n", 1)[-1]
    assert re.fullmatch(r"[-+]line-\d{7}-fill", last_line), (
        f"truncation landed mid-line: {last_line!r}")


def test_git_always_pins_utf8_decoding(ws, monkeypatch):
    """Fix round 1, Minor 1: `errors=` alone only decides what happens to
    a byte that fails to decode — it says nothing about which codec is
    tried first. Left to `subprocess.run`'s default, that codec follows
    `locale.getpreferredencoding()`, so the exact same git output would
    decode differently (or wrongly, without raising) on a host whose
    locale isn't UTF-8. `_git` must pin `encoding="utf-8"` explicitly on
    every call, not just the diff-specific `errors=`."""
    seen_encodings = []
    real_run = provenance.subprocess.run

    def spy(argv, **kwargs):
        if argv and argv[0] == provenance.GIT and "--git-dir" in argv:
            seen_encodings.append(kwargs.get("encoding"))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(provenance.subprocess, "run", spy)
    provenance.ensure_repo(ws)
    assert seen_encodings, "no _git-routed subprocess call was observed"
    assert all(enc == "utf-8" for enc in seen_encodings)


def test_a_binary_file_in_a_round_is_never_committed_or_reachable_by_diff(drafted):
    """REVIEW FOCUS 3, corrected: the brief's original version of this test
    (`test_a_binary_artifact_diffs_without_dumping_bytes`) wrote a
    `figure.pdf` into a round directory and asserted the diff mentions it.
    But `sync`'s projection globs only text patterns for a round
    (`glob("*.tex")` for sections, plus the named `curation.yml`) — a
    `figure.pdf` under `rounds/2/` is never picked up by any path-table
    entry, so neither substring in that assertion could ever appear, and
    that test would fail against a correct implementation. The real,
    stronger guarantee is that such a file never reaches the committed
    tree at all, and therefore can never reach a diff."""
    round_dir = drafted / "manuscript" / "curation" / "rounds" / "2"
    (round_dir / "figure.pdf").write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 20)
    (round_dir / "extra.tex").write_text("extra section\n")

    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    listing = provenance._git(repo, "ls-tree", "-r", "--name-only", "refs/heads/main")
    assert "merge_2/sections/extra.tex" in listing
    assert "figure.pdf" not in listing
    assert not any("figure.pdf" in line for line in listing.splitlines())
    # The exact set, not just an absence-of-substring check: a regression
    # that iterated every file in the round (not just `*.tex`) would still
    # commit the PDF's bytes, just under a renamed `figure.tex` entry —
    # invisible to a check that only greps for the literal name "figure.pdf".
    sections = provenance._git(
        repo, "ls-tree", "--name-only", "refs/heads/main:merge_2/sections")
    assert set(sections.splitlines()) == {"results.tex", "extra.tex"}

    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert "\x00" not in result["text"], "raw bytes reached the diff text"
    assert "figure.pdf" not in result["text"]


def test_draft_branch_discovery_is_not_confused_by_a_same_named_tag(drafted):
    """Fix round 1, Minor 3: `%(refname:short)` shortens ambiguously — a
    tag whose name collides with a branch's suffix (`refs/tags/draft/
    claude` alongside `refs/heads/draft/claude`) makes git report the
    branch's "short" name as `heads/draft/claude` instead of
    `draft/claude` (verified directly against this host's git), and a
    bare `removeprefix("draft/")` against that would silently fail to
    strip it — dropping the agent's points entirely rather than reporting
    them under the wrong name. `%(refname:lstrip=3)` strips exactly
    `refs/heads/draft/` from the ref's real name and is unaffected by
    anything else in the repo."""
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    claude_sha = provenance._ref_sha(repo, "refs/heads/draft/claude")
    provenance._git(repo, "tag", "draft/claude", claude_sha)

    refs = [p["ref"] for p in provenance.points(drafted)]
    assert "draft/claude:sections" in refs


def test_points_reflects_the_repo_even_if_the_workspace_drifts(drafted):
    """`points` reads the repo, not the workspace, so it reports what is
    actually committed — not what the run's files currently look like.
    Every workspace trace of "claude" (not just its draft directory —
    `agents(ws)` would otherwise still re-derive "claude" from its
    `findings`/`gaps`/`reviews` entries) is removed after the sync, so
    `agents(ws)` itself would no longer name "claude" at all; the
    already-committed `draft/claude` branch must still show up, which
    means discovering draft branches has to come from the repo's own
    `refs/heads/draft/*`, not from re-scanning the workspace."""
    provenance.sync(drafted)
    before = {p["ref"] for p in provenance.points(drafted)}
    assert "draft/claude:sections" in before

    shutil.rmtree(drafted / "manuscript" / "drafts" / "claude")
    (drafted / "findings" / "claude.json").unlink()
    (drafted / "gaps" / "claude.json").unlink()
    (drafted / "reviews" / "claude-on-codex.json").unlink()
    (drafted / "review" / "draft-round-1" / "claude-on-codex.json").unlink()
    assert provenance.agents(drafted) == ["codex"], (
        "the fixture must no longer imply \"claude\" from the workspace "
        "for this test to isolate repo-vs-workspace behaviour")

    after = {p["ref"] for p in provenance.points(drafted)}
    assert after == before, "points must reflect the repo, not today's workspace"


def test_a_non_utf8_change_does_not_crash_the_diff(ws):
    """`_git`'s stdout decode must not raise on a byte a legitimately
    non-UTF-8 `.tex` file (see `test_a_non_utf8_source_is_committed_byte_
    exact`) can put straight into a diff hunk. `diff` passes
    `errors="replace"` to `_git` for exactly this — a malformed byte is
    substituted, not a crash."""
    _write(ws / "manuscript" / "curation" / "rounds" / "1" / "results.tex", "v1\n")
    round2 = ws / "manuscript" / "curation" / "rounds" / "2"
    round2.mkdir(parents=True)
    (round2 / "results.tex").write_bytes(
        "R\xe9sultats en fran\xe7ais\n".encode("latin-1"))

    provenance.sync(ws)
    result = provenance.diff(ws, "main:merge_1", "main:merge_2")
    assert result["truncated"] is False
    assert "results.tex" in result["text"]
    assert "�" in result["text"], "a malformed byte should be replaced, not raise"


def _four_agent_history(ws, rounds):
    """Equivalent histories; all fixture Git work precedes measurement."""
    _write(ws / "outline" / "outline.md", "# Outline\n")
    for n in range(1, rounds + 1):
        _write(ws / "manuscript" / "curation" / "rounds" / str(n) / "results.tex",
               f"merged {n}\n")
        _write(ws / "review" / f"round-{n}" / "review.md", f"review {n}\n")
        for agent in ("agy", "claude", "codex", "gemini"):
            _write(ws / "manuscript" / "drafts" / agent / "results.tex", f"{agent}\n")
            _write(ws / "review" / f"draft-round-{n}" / f"response-{agent}.md",
                   f"response {n}\n")
    commits = provenance.sync(ws)["commits"]
    repo = provenance.repo_path(ws)
    return {ref: (sha, provenance._git(repo, "log", "-1", "--format=%cI", sha))
            for ref, sha in commits.items()}


def _history_points(tips, rounds):
    """Hand-derived public vocabulary, including absence of top-level parent."""
    expected = []
    sha, at = tips["refs/heads/main"]
    for n in range(rounds, 0, -1):
        expected.extend([
            {"ref": f"main:merge_{n}", "label": f"Merge round {n}",
             "kind": "merge", "commit": sha, "at": at},
            {"ref": f"main:merge_{n}/sections", "label": f"Merge round {n} sections",
             "kind": "merge", "parent": f"main:merge_{n}", "commit": sha, "at": at},
        ])
    for n in range(rounds, 0, -1):
        expected.append({"ref": f"main:review_{n}", "label": f"Review round {n}",
                         "kind": "review", "commit": sha, "at": at})
    expected.append({"ref": "main:outline.md", "label": "Outline", "kind": "outline",
                     "commit": sha, "at": at})
    for agent in ("agy", "claude", "codex", "gemini"):
        sha, at = tips[f"refs/heads/draft/{agent}"]
        expected.append({"ref": f"draft/{agent}:sections", "label": f"{agent}'s draft",
                         "kind": "draft", "commit": sha, "at": at})
        for n in range(rounds, 0, -1):
            expected.append({"ref": f"draft/{agent}:review_{n}",
                             "label": f"Review round {n} ({agent})", "kind": "review",
                             "commit": sha, "at": at})
    return expected


@pytest.mark.parametrize("rounds", [2, 20])
def test_cold_points_git_cost_is_bounded_by_branches(ws, monkeypatch, rounds):
    """A per-round traversal breaks both the tree and total-command budgets."""
    tips = _four_agent_history(ws, rounds)
    calls = []
    real_run = provenance.subprocess.run

    def measured(argv, **kwargs):
        if argv[:3] == [provenance.GIT, "--git-dir", str(provenance.repo_path(ws))]:
            calls.append(argv[3:])
        return real_run(argv, **kwargs)

    monkeypatch.setattr(provenance.subprocess, "run", measured)
    actual = provenance.points(ws)
    tree_count = sum(args[0] == "ls-tree" for args in calls)
    print(f"COLD_POINTS rounds={rounds} branches=5 ls-tree={tree_count} "
          f"total-git={len(calls)} points={len(actual)}")
    assert actual == _history_points(tips, rounds)
    assert tree_count == 5, "one recursive listing per relevant branch, independent of rounds"
    assert len(calls) == 6, "one ref observation plus five listings, no per-point metadata work"


def _point_commit(repo, ref, entries):
    tree = provenance._tree(repo, entries)
    commit = provenance._commit(repo, tree, [], "point fixture", when="@946684800 +0000")
    provenance._update_ref(repo, ref, commit)
    return commit


def test_points_round_components_are_exact_ascii_canonical(ws):
    """Noncanonical spellings must not create aliases of real or missing rounds."""
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    sections = provenance._tree(repo, [("040000", "tree", empty, "sections")])
    suffixes = ["0", "2", "00", "01", "١", "１", "+1", "-1", "1\n", "1\r",
                "1\t", "1x", " 1", "1 "]
    main_entries = [("040000", "tree", sections if n == "2" else empty, f"merge_{n}")
                    for n in suffixes]
    main_entries += [("040000", "tree", empty, f"review_{n}") for n in suffixes]
    _point_commit(repo, "refs/heads/main", main_entries)
    _point_commit(repo, "refs/heads/draft/claude",
                  [("040000", "tree", empty, "sections")]
                  + [("040000", "tree", empty, f"review_{n}") for n in suffixes])
    assert [p["ref"] for p in provenance.points(ws)] == [
        "main:merge_2", "main:merge_2/sections", "main:merge_0",
        "main:review_2", "main:review_0", "draft/claude:sections",
        "draft/claude:review_2", "draft/claude:review_0",
    ]


@pytest.mark.parametrize("mode,kind", [("100644", "blob"), ("120000", "blob"),
                                      ("160000", "commit")])
def test_points_directory_names_require_actual_trees(ws, mode, kind):
    """Blobs, symlinks and gitlinks cannot masquerade as point directories."""
    repo = provenance.ensure_repo(ws)
    oid = (provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF) if kind == "commit"
           else provenance._blob(repo, "not a directory"))
    assert oid is not None
    merge = provenance._tree(repo, [(mode, kind, oid, "sections")])
    _point_commit(repo, "refs/heads/main", [
        ("040000", "tree", merge, "merge_1"),
        (mode, kind, oid, "merge_8"), (mode, kind, oid, "review_8"),
    ])
    _point_commit(repo, "refs/heads/draft/claude", [
        (mode, kind, oid, "sections"), (mode, kind, oid, "review_8"),
    ])
    assert [p["ref"] for p in provenance.points(ws)] == ["main:merge_1"]


@pytest.mark.parametrize("kind", ["tree", "commit"])
def test_points_outline_requires_a_blob(ws, kind):
    repo = provenance.ensure_repo(ws)
    oid = (provenance._tree(repo, []) if kind == "tree"
           else provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF))
    assert oid is not None
    _point_commit(repo, "refs/heads/main", [
        ("040000" if kind == "tree" else "160000", kind, oid, "outline.md"),
    ])
    assert provenance.points(ws) == []


@pytest.mark.parametrize("agent", ["café", 'quote"agent', "line\u2028agent", "line\u0085agent"])
def test_points_ref_metadata_preserves_unicode_without_line_splitting(ws, agent):
    """Only Git's record newline separates refs, not Unicode line separators."""
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    commit = _point_commit(repo, f"refs/heads/draft/{agent}",
                           [("040000", "tree", empty, "sections")])
    assert provenance.points(ws) == [{
        "ref": f"draft/{agent}:sections", "label": f"{agent}'s draft", "kind": "draft",
        "commit": commit, "at": "2000-01-01T00:00:00+00:00",
    }]


def test_points_unrepresentable_ref_metadata_is_a_typed_failure(ws):
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    tree = provenance._tree(repo, [("040000", "tree", empty, "sections")])
    commit = provenance._commit(repo, tree, [], "raw ref", when="@946684800 +0000")
    subprocess.run(["git", "--git-dir", str(repo), "update-ref", b"refs/heads/draft/\xff",
                    commit], capture_output=True, check=True, env=provenance._env())
    with pytest.raises(provenance.ProvenanceError):
        provenance.points(ws)


def _raw_tree(repo, entries):
    records = [f"{mode} {kind} {oid}\t".encode("ascii") + name + b"\x00"
               for mode, kind, oid, name in entries]
    result = subprocess.run(["git", "--git-dir", str(repo), "mktree", "-z"],
                            input=b"".join(records), capture_output=True, check=True,
                            env=provenance._env())
    return result.stdout.decode("ascii").strip()


def test_recursive_points_are_lossless_for_irrelevant_path_bytes(ws, monkeypatch):
    """Descendant bytes and misleading nested names cannot forge point vocabulary."""
    repo = provenance.ensure_repo(ws)
    blob = provenance._blob(repo, "x\n")
    names = [b"tab\tname.tex", b"line\nname.tex", b"cr\rname.tex", b'quote"name.tex',
             "café.tex".encode("utf-8"), b"raw-\xff.tex", b"trailing\r\n",
             f"leaf\n040000 tree {'0' * 40}\treview_99".encode("ascii")]
    leaves = _raw_tree(repo, [("100644", "blob", blob, name) for name in names])
    sections = provenance._tree(repo, [("040000", "tree", leaves, "sections")])
    nested = provenance._tree(repo, [("040000", "tree", sections, "merge_99"),
                                    ("040000", "tree", leaves, "review_99")])
    no_direct_sections = provenance._tree(repo, [("040000", "tree", sections, "nested"),
                                                ("040000", "tree", leaves, "sections_extra")])
    main = _point_commit(repo, "refs/heads/main", [
        ("040000", "tree", sections, "merge_0"),
        ("040000", "tree", no_direct_sections, "merge_3"),
        ("040000", "tree", leaves, "review_0"),
        ("040000", "tree", nested, "nested"),
        ("040000", "tree", leaves, "x\nmerge_99"),
        ("100644", "blob", blob, "outline.md"),
    ])
    draft = _point_commit(repo, "refs/heads/draft/claude", [
        ("040000", "tree", leaves, "sections"),
        ("040000", "tree", leaves, "review_0"),
        ("040000", "tree", nested, "nested"),
    ])
    real_run = provenance.subprocess.run
    commands = []

    def read_only(argv, **kwargs):
        if argv[:3] == ["git", "--git-dir", str(repo)]:
            commands.append(argv[3:])
            assert argv[3] in {"for-each-ref", "ls-tree"}
            assert kwargs.get("env") == {**provenance._env(), "GIT_NO_REPLACE_OBJECTS": "1"}
            assert kwargs.get("timeout") == provenance._TIMEOUT_S
            assert not kwargs.get("text", False) and "encoding" not in kwargs
        return real_run(argv, **kwargs)

    def no_write(*args, **kwargs):
        raise AssertionError("point reads must not repair or synchronize")

    monkeypatch.setattr(provenance.subprocess, "run", read_only)
    monkeypatch.setattr(provenance, "ensure_repo", no_write)
    monkeypatch.setattr(provenance, "sync", no_write)
    points = provenance.points(ws)
    assert [p["ref"] for p in points] == [
        "main:merge_3", "main:merge_0", "main:merge_0/sections", "main:review_0",
        "main:outline.md", "draft/claude:sections", "draft/claude:review_0",
    ]
    assert [p["commit"] for p in points] == [main] * 5 + [draft] * 2
    assert all(p["at"] == "2000-01-01T00:00:00+00:00" for p in points)
    assert commands[1:] == [["ls-tree", "-r", "-t", "-z", main],
                             ["ls-tree", "-r", "-t", "-z", draft]]


def test_point_snapshot_preserves_the_observed_generation_when_refs_move(ws, monkeypatch):
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    old = _point_commit(repo, "refs/heads/main", [("040000", "tree", empty, "merge_0")])
    new_tree = provenance._tree(repo, [("040000", "tree", empty, "merge_99")])
    new = provenance._commit(repo, new_tree, [], "later", when="@978307200 +0000")
    real_run = provenance.subprocess.run
    reads = []

    def move_after_observation(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if argv[:3] == ["git", "--git-dir", str(repo)]:
            reads.append(argv[3:])
            if argv[3] == "for-each-ref":
                real_run(["git", "--git-dir", str(repo), "update-ref", "refs/heads/main", new],
                         capture_output=True, check=True, env=provenance._env())
        return result

    monkeypatch.setattr(provenance.subprocess, "run", move_after_observation)
    snapshot = provenance._read_point_snapshot(ws)
    assert snapshot is not None
    assert [(t.ref, t.commit, t.at) for t in snapshot.branches] == [
        ("refs/heads/main", old, "2000-01-01T00:00:00+00:00"),
    ]
    assert snapshot.points == ({"ref": "main:merge_0", "label": "Merge round 0",
                                "kind": "merge", "commit": old,
                                "at": "2000-01-01T00:00:00+00:00"},)
    assert len(reads) == 2 and reads[1] == ["ls-tree", "-r", "-t", "-z", old]
    assert provenance._ref_sha(repo, "refs/heads/main") == new


def test_point_snapshot_distinguishes_empty_refs_from_failed_ref_observation(ws):
    repo = provenance.ensure_repo(ws)
    snapshot = provenance._read_point_snapshot(ws)
    assert snapshot is not None and snapshot.branches == () and snapshot.points == ()
    (repo / "HEAD").write_text("broken HEAD\n")
    assert provenance._read_point_snapshot(ws) is None
    assert provenance.points(ws) == []
    assert (repo / "HEAD").read_text() == "broken HEAD\n", "reader must not repair"


def test_point_snapshot_recursive_failure_discards_all_partial_points(ws):
    """Real missing subtree after readable refs must not look like empty history."""
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    _point_commit(repo, "refs/heads/main", [("040000", "tree", empty, "merge_0")])
    blob = provenance._blob(repo, "unique content for removed subtree\n")
    subtree = provenance._tree(repo, [("100644", "blob", blob, "results.tex")])
    _point_commit(repo, "refs/heads/draft/claude", [("040000", "tree", subtree, "sections")])
    (repo / "objects" / subtree[:2] / subtree[2:]).unlink()
    assert provenance._git(repo, "for-each-ref", "--format=%(refname)")
    with pytest.raises(provenance.ProvenanceError, match="ls-tree failed"):
        provenance.points(ws)


@pytest.mark.parametrize("failure", ["timeout", "missing", "oserror"])
def test_binary_git_transport_uses_typed_failures(ws, monkeypatch, failure):
    repo = provenance.ensure_repo(ws)

    def fail(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, provenance._TIMEOUT_S)
        if failure == "missing":
            raise FileNotFoundError("git missing")
        raise OSError("cannot spawn Git")

    monkeypatch.setattr(provenance.subprocess, "run", fail)
    with pytest.raises(provenance.ProvenanceError):
        provenance._git_bytes(repo, "ls-tree", "SENSITIVE-ARGUMENT")
    assert provenance.points(ws) == [], "initial ref-read failures retain empty-history behavior"


@pytest.mark.parametrize("hostile", ["main:merge_999", "HEAD:merge_1", "main~0:merge_1",
                                     "main^:merge_1", "main:merge_1/", "main:merge_1/../merge_2",
                                     "draft/claude:sections^{tree}", "refs/heads/draft/claude:sections"])
def test_point_whitelist_refuses_revision_expressions_unknown_refs_and_aliases(drafted, hostile):
    provenance.sync(drafted)
    with pytest.raises(provenance.ProvenanceError, match="not a point"):
        provenance.diff(drafted, hostile, "main:merge_2")
    with pytest.raises(provenance.ProvenanceError, match="not a point"):
        provenance.diff(drafted, "main:merge_2", hostile)


def test_point_whitelist_and_comparisons_ignore_branch_tag_ambiguity(drafted):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    root = provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF)
    assert root is not None
    before = provenance.points(drafted)
    provenance._update_ref(repo, "refs/tags/main", root)
    provenance._update_ref(repo, "refs/tags/draft/claude", root)
    assert provenance.points(drafted) == before
    result = provenance.diff(drafted, "draft/claude:sections", "main:merge_1/sections")
    assert "-claude's results" in result["text"] and "+merged v1" in result["text"]
    assert result["text"].count("diff --git") == 1


def test_points_canonical_round_numbers_do_not_depend_on_python_int_limits(ws):
    """Valid canonical path components stay sortable without bounded int parsing."""
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    largest = "9" * 5000
    smaller = "9" * 4999
    _point_commit(repo, "refs/heads/main", [
        ("040000", "tree", empty, f"merge_{n}")
        for n in ("0", "2", "10", smaller, largest)
    ])
    assert [p["ref"] for p in provenance.points(ws)] == [
        f"main:merge_{largest}", f"main:merge_{smaller}",
        "main:merge_10", "main:merge_2", "main:merge_0",
    ]


def test_point_snapshot_noncommit_head_is_a_typed_failure_not_a_point(ws):
    """A corrupt branch pointing to a tree must not gain commit/date authority."""
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    tree = provenance._tree(repo, [("040000", "tree", empty, "merge_0")])
    # update-ref refuses a non-commit head; model actual on-disk corruption.
    (repo / "refs" / "heads" / "main").write_text(tree + "\n")
    with pytest.raises(provenance.ProvenanceError):
        provenance.points(ws)


def test_task2_snapshot_records_do_not_retain_mutable_authority(drafted):
    provenance.sync(drafted)
    snapshot = provenance._read_point_snapshot(drafted)
    assert snapshot is not None
    with pytest.raises(TypeError):
        snapshot.points[0]["ref"] = "--output=forged"


def test_task2_diff_pins_validated_object_ids_when_ref_moves(drafted, monkeypatch):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    old = provenance._ref_sha(repo, "refs/heads/main")
    root = provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF)
    assert old is not None and root is not None
    real_run = provenance.subprocess.run
    calls = []

    def move_before_diff(argv, **kwargs):
        if argv[:4] == ["git", "--git-dir", str(repo), "diff"]:
            calls.append(argv[4:])
            real_run(["git", "--git-dir", str(repo), "update-ref", "refs/heads/main", root],
                     check=True, capture_output=True, env=provenance._env())
        return real_run(argv, **kwargs)

    monkeypatch.setattr(provenance.subprocess, "run", move_before_diff)
    result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
    assert "-merged v1" in result["text"] and "+merged v2" in result["text"]
    assert calls == [["--no-color", f"{old}:merge_1", f"{old}:merge_2"]]
    assert result["a"] == "main:merge_1" and result["b"] == "main:merge_2"


def _task2_reads(monkeypatch, *repos):
    from pathlib import Path

    real_run = provenance.subprocess.run
    calls = []
    normalized = {repo.resolve() for repo in repos}

    def measured(argv, **kwargs):
        if argv[:2] == ["git", "--git-dir"] and Path(argv[2]).resolve() in normalized:
            calls.append((argv[2], argv[3:]))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(provenance.subprocess, "run", measured)
    return calls


def _task2_trees(calls):
    return sum(args[0] == "ls-tree" for _repo, args in calls)


@pytest.mark.parametrize("hostile", ["--output=forged", "-x", "main:merge_999", "HEAD:merge_1",
                                     "main~0:merge_1", "main^:merge_1", "main:merge_1/",
                                     "main:merge_1/../merge_2", "draft/claude:sections^{tree}",
                                     "refs/heads/draft/claude:sections", "main:merge_1/sections/results.tex"])
def test_task2_mutated_public_lists_cannot_authorize_either_diff_operand(drafted, monkeypatch, hostile):
    provenance.sync(drafted)
    calls = _task2_reads(monkeypatch, provenance.repo_path(drafted))
    with provenance.read_scope():
        before = provenance.points(drafted)
        forged = provenance.points(drafted)
        assert forged == before and forged is not before and forged[0] is not before[0]
        forged[0]["ref"] = hostile
        forged.clear()
        forged.append({"ref": hostile, "commit": before[0]["commit"]})
        for a, b in [(hostile, "main:merge_2"), ("main:merge_2", hostile)]:
            with pytest.raises(provenance.ProvenanceError, match="not a point"):
                provenance.diff(drafted, a, b)
        assert not any(args[0] == "diff" for _repo, args in calls)
        assert provenance.points(drafted) == before
        valid = provenance.diff(drafted, "main:merge_1", "main:merge_2")
        assert "+merged v2" in valid["text"] and valid["truncated"] is False
        assert _task2_trees(calls) == 3


def test_task2_external_whitelist_is_not_an_api(drafted, monkeypatch):
    import inspect

    provenance.sync(drafted)
    calls = _task2_reads(monkeypatch, provenance.repo_path(drafted))
    assert list(inspect.signature(provenance.diff).parameters) == ["ws", "a", "b"]
    with pytest.raises(TypeError):
        provenance.diff(drafted, "--output=forged", "main:merge_2", **{"allowed": {"--output=forged"}})
    assert calls == []


def test_task2_scopes_are_lazy_sequential_and_unscoped_reads_retain_nothing(drafted, monkeypatch):
    provenance.sync(drafted)
    calls = _task2_reads(monkeypatch, provenance.repo_path(drafted))
    assert provenance._read_memo.get() is None
    with provenance.read_scope():
        assert calls == []
    for _ in range(2):
        with provenance.read_scope():
            first = provenance.points(drafted)
            assert provenance.points(drafted) == first
    assert _task2_trees(calls) == 2 * 3
    assert len(calls) == 2 * (3 + 2)
    assert provenance._read_memo.get() is None
    provenance.points(drafted)
    provenance.points(drafted)
    assert _task2_trees(calls) == 4 * 3 and len(calls) == 2 * (3 + 2) + 2 * (3 + 1)


def test_task2_scoped_refs_add_delete_and_a_b_a_are_revalidated(ws, monkeypatch):
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    old = _point_commit(repo, "refs/heads/main", [("040000", "tree", empty, "merge_1")])
    new_tree = provenance._tree(repo, [("040000", "tree", empty, "merge_2")])
    new = provenance._commit(repo, new_tree, [], "B", when="@978307200 +0000")
    calls = _task2_reads(monkeypatch, repo)
    with provenance.read_scope():
        first = provenance.points(ws)
        assert first[0]["commit"] == old
        provenance._update_ref(repo, "refs/heads/main", new)
        assert [p["ref"] for p in provenance.points(ws)] == ["main:merge_2"]
        with pytest.raises(provenance.ProvenanceError):
            provenance.diff(ws, "main:merge_1", "main:merge_2")
        provenance._update_ref(repo, "refs/heads/main", old)
        assert provenance.points(ws) == first
        assert _task2_trees(calls) == 3, "A to B to A must not revive stale enumeration"
        provenance._update_ref(repo, "refs/heads/draft/new", new)
        assert [p["ref"] for p in provenance.points(ws)] == ["main:merge_1"]
        assert _task2_trees(calls) == 5, "even a branch with no points changes the fingerprint"
        provenance._git(repo, "update-ref", "-d", "refs/heads/main")
        assert provenance.points(ws) == []
        with pytest.raises(provenance.ProvenanceError):
            provenance.diff(ws, "main:merge_1", "main:merge_1")
        assert _task2_trees(calls) == 6
        assert not any(args[0] == "diff" for _repo, args in calls)


def test_task2_move_during_fill_uses_old_oids_then_rechecks_once(ws, monkeypatch):
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    old = _point_commit(repo, "refs/heads/main", [("040000", "tree", empty, "merge_1")])
    tree = provenance._tree(repo, [("040000", "tree", empty, "merge_2")])
    new = provenance._commit(repo, tree, [], "B", when="@978307200 +0000")
    real_run = provenance.subprocess.run
    reads = []
    observed = 0

    def churn(argv, **kwargs):
        nonlocal observed
        result = real_run(argv, **kwargs)
        if argv[:3] == ["git", "--git-dir", str(repo)]:
            reads.append(argv[3:])
            if argv[3] == "for-each-ref":
                observed += 1
                move = new if observed % 2 else old
                real_run(["git", "--git-dir", str(repo), "update-ref", "refs/heads/main", move],
                         check=True, capture_output=True, env=provenance._env())
        return result

    monkeypatch.setattr(provenance.subprocess, "run", churn)
    with provenance.read_scope():
        first = provenance.points(ws)
        assert first[0]["ref"] == "main:merge_1" and first[0]["commit"] == old
        second = provenance.points(ws)
        assert second[0]["ref"] == "main:merge_2" and second[0]["commit"] == new
    assert observed == 2 and len(reads) == 4, "no retry until stable"
    assert [args[-1] for args in reads if args[0] == "ls-tree"] == [old, new]


@pytest.mark.parametrize("failure", ["refs", "metadata", "listing"])
def test_task2_failed_revalidation_or_listing_invalidates_prior_authority(drafted, monkeypatch, failure):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    main = provenance._ref_sha(repo, "refs/heads/main")
    assert main is not None
    calls = _task2_reads(monkeypatch, repo)
    real_bytes = provenance._git_bytes
    fail = False

    def transport(repo, *args):
        if fail and args[0] == ("ls-tree" if failure == "listing" else "for-each-ref"):
            if failure == "metadata":
                return b"refs/heads/main\x00bad\x00commit\x00\xff\n"
            raise provenance.ProvenanceError("injected transport failure")
        return real_bytes(repo, *args)

    monkeypatch.setattr(provenance, "_git_bytes", transport)
    with provenance.read_scope():
        before = provenance.points(drafted)
        fail = True
        if failure == "listing":
            provenance._update_ref(repo, "refs/heads/draft/new", main)
        if failure == "refs":
            assert provenance.points(drafted) == []
        else:
            with pytest.raises(provenance.ProvenanceError):
                provenance.points(drafted)
        assert provenance._read_memo.get() == (), "failed reads cannot retain old authority"
        with pytest.raises(provenance.ProvenanceError):
            provenance.diff(drafted, "main:merge_1", "main:merge_2")
        fail = False
        after = provenance.points(drafted)
        assert after[:len(before)] == before
        assert _task2_trees(calls) == (2 * 3 + 1 if failure == "listing" else 2 * 3)
        assert not any(args[0] == "diff" for _repo, args in calls)


def test_task2_empty_refs_are_cached_but_broken_initial_refs_are_not(ws, monkeypatch):
    repo = provenance.ensure_repo(ws)
    calls = _task2_reads(monkeypatch, repo)
    with provenance.read_scope():
        assert provenance.points(ws) == []
        assert len(provenance._read_memo.get() or ()) == 1
        assert provenance.points(ws) == [] and len(calls) == 2
        head = (repo / "HEAD").read_bytes()
        (repo / "HEAD").write_text("broken HEAD\n")
        assert provenance.points(ws) == [] and provenance._read_memo.get() == ()
        assert (repo / "HEAD").read_text() == "broken HEAD\n"
        (repo / "HEAD").write_bytes(head)
        assert provenance.points(ws) == [] and len(provenance._read_memo.get() or ()) == 1


def test_task2_repo_replacement_disappearance_and_identical_tips_do_not_share_cache(drafted, monkeypatch, tmp_path):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    other_ws = tmp_path / "other"
    other_ws.mkdir()
    other = provenance.repo_path(other_ws)
    shutil.copytree(repo, other)
    calls = _task2_reads(monkeypatch, repo, other)
    with provenance.read_scope():
        first = provenance.points(drafted)
        assert provenance.points(other_ws) == first
        assert _task2_trees(calls) == 2 * 3, "identical tips in a different repo need their own enumeration"
        saved = drafted / "saved.git"
        repo.rename(saved)
        shutil.copytree(saved, repo)
        assert provenance.points(drafted) == first
        assert _task2_trees(calls) == 3 * 3, "replacement without a missing read changes inode"
        repo.rename(drafted / "replacement.git")
        assert provenance.points(drafted) == []
        assert provenance._read_memo.get() == ()
        shutil.copytree(saved, repo)
        assert provenance.points(drafted) == first
        assert _task2_trees(calls) == 4 * 3, "disappearance also discards cached authority"


def test_task2_nested_scope_exception_unwinds_and_outer_revalidates(drafted, monkeypatch):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    root = provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF)
    assert root is not None
    calls = _task2_reads(monkeypatch, repo)
    with provenance.read_scope():
        before = provenance.points(drafted)
        outer = provenance._read_memo.get()
        with pytest.raises(RuntimeError, match="unwind"):
            with provenance.read_scope():
                assert provenance._read_memo.get() == ()
                assert provenance.points(drafted) == before
                provenance._update_ref(repo, "refs/heads/main", root)
                raise RuntimeError("unwind")
        assert provenance._read_memo.get() is outer
        current = provenance.points(drafted)
        assert all(not p["ref"].startswith("main:") for p in current)
        assert _task2_trees(calls) == 3 * 3
    assert provenance._read_memo.get() is None


def test_task2_simultaneous_async_scopes_and_copied_contexts_are_isolated(drafted, monkeypatch):
    import asyncio

    provenance.sync(drafted)
    calls = _task2_reads(monkeypatch, provenance.repo_path(drafted))

    async def request():
        with provenance.read_scope():
            first = provenance.points(drafted)
            await asyncio.sleep(0)
            assert provenance.points(drafted) == first
            return provenance._read_memo.get()

    async def parallel():
        return await asyncio.gather(request(), request())

    states = asyncio.run(parallel())
    assert states[0] is not None and states[1] is not None
    assert states[0] is not states[1] and states[0][0] is not states[1][0]
    assert _task2_trees(calls) == 2 * 3 and len(calls) == 2 * (3 + 2)
    assert provenance._read_memo.get() is None

    async def copied():
        # Child contexts inherit immutable state but must replace, not mutate it.
        with provenance.read_scope():
            provenance.points(drafted)
            outer = provenance._read_memo.get()

            async def child():
                assert provenance._read_memo.get() is outer
                provenance.points(drafted)
                assert provenance._read_memo.get() is not outer

            await asyncio.gather(child(), child())
            assert provenance._read_memo.get() is outer

    asyncio.run(copied())
    assert _task2_trees(calls) == 3 * 3 and len(calls) == 2 * (3 + 2) + 3 + 1 + 2
    assert provenance._read_memo.get() is None


def test_task2_real_threads_do_not_share_request_authority(drafted, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    provenance.sync(drafted)
    calls = _task2_reads(monkeypatch, provenance.repo_path(drafted))
    barrier = Barrier(2)

    def request():
        assert provenance._read_memo.get() is None
        with provenance.read_scope():
            barrier.wait(timeout=10)
            before = provenance.points(drafted)
            state = provenance._read_memo.get()
            barrier.wait(timeout=10)
            assert provenance.points(drafted) == before
        assert provenance._read_memo.get() is None
        return state

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda _n: request(), range(2)))
    assert states[0] is not None and states[1] is not None
    assert states[0] is not states[1] and states[0][0] is not states[1][0]
    assert _task2_trees(calls) == 2 * 3 and len(calls) == 2 * (3 + 2)


def test_task2_tag_collisions_are_distinct_content_and_not_cached_authority(drafted, monkeypatch):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    root = provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF)
    main = provenance._ref_sha(repo, "refs/heads/main")
    draft = provenance._ref_sha(repo, "refs/heads/draft/claude")
    assert root is not None and root != main and root != draft
    calls = _task2_reads(monkeypatch, repo)
    with provenance.read_scope():
        before = provenance.points(drafted)
        provenance._update_ref(repo, "refs/tags/main", root)
        provenance._update_ref(repo, "refs/tags/draft/claude", root)
        assert provenance.points(drafted) == before
        result = provenance.diff(drafted, "draft/claude:sections", "main:merge_1/sections")
        assert "-claude's results" in result["text"] and "+merged v1" in result["text"]
        assert _task2_trees(calls) == 3
        assert [args for _repo, args in calls if args[0] == "diff"] == [
            ["diff", "--no-color", f"{draft}:sections", f"{main}:merge_1/sections"]]


def test_task2_identity_is_normalized_absolute_and_restat_before_hit(drafted, monkeypatch):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    identity = provenance._repo_identity(repo)
    assert identity is not None
    assert identity.location == str(repo.resolve())
    assert (identity.device, identity.inode) == (repo.stat().st_dev, repo.stat().st_ino)
    calls = _task2_reads(monkeypatch, repo)
    real_identity = provenance._repo_identity
    checks = []

    def observed(path):
        checks.append(path)
        return real_identity(path)

    monkeypatch.setattr(provenance, "_repo_identity", observed)
    with provenance.read_scope():
        before = provenance.points(drafted)
        assert provenance.points(drafted / ".." / drafted.name) == before
        assert len(checks) == 4, "stat before refs and before reuse for both reads"
        assert _task2_trees(calls) == 3
        assert len(calls) == 3 + 2


def test_task2_replacement_during_fingerprint_is_typed_and_discards_memo(drafted, monkeypatch):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    replacement = drafted / "ready.git"
    shutil.copytree(repo, replacement)
    real_tips = provenance._read_branch_tips
    move = False

    def observe(path):
        nonlocal move
        result = real_tips(path)
        if move:
            move = False
            repo.rename(drafted / "saved-before-recheck.git")
            replacement.rename(repo)
        return result

    monkeypatch.setattr(provenance, "_read_branch_tips", observe)
    with provenance.read_scope():
        first = provenance.points(drafted)
        move = True
        with pytest.raises(provenance.ProvenanceError, match="repository changed"):
            provenance.diff(drafted, "main:merge_1", "main:merge_2")
        assert provenance._read_memo.get() == ()
        assert provenance.points(drafted) == first


def test_task2_scoped_diff_uses_the_rechecked_generation_even_if_refs_move_before_git(drafted, monkeypatch):
    provenance.sync(drafted)
    repo = provenance.repo_path(drafted)
    main = provenance._ref_sha(repo, "refs/heads/main")
    root = provenance._ref_sha(repo, provenance.EMPTY_ROOT_REF)
    assert main is not None and root is not None
    real_run = provenance.subprocess.run
    calls = []

    def change(argv, **kwargs):
        if argv[:3] == ["git", "--git-dir", str(repo)]:
            calls.append(argv[3:])
            if argv[3] == "diff":
                real_run(["git", "--git-dir", str(repo), "update-ref", "refs/heads/main", root],
                         check=True, capture_output=True, env=provenance._env())
        return real_run(argv, **kwargs)

    monkeypatch.setattr(provenance.subprocess, "run", change)
    with provenance.read_scope():
        before = provenance.points(drafted)
        assert before[0]["commit"] == main
        result = provenance.diff(drafted, "main:merge_1", "main:merge_2")
        assert "+merged v2" in result["text"] and "-merged v1" in result["text"]
        assert calls[-2][0] == "for-each-ref"
        assert calls[-1] == ["diff", "--no-color", f"{main}:merge_1", f"{main}:merge_2"]
        assert sum(c[0] == "ls-tree" for c in calls) == 3 and len(calls) == 3 + 3
        with pytest.raises(provenance.ProvenanceError, match="not a point"):
            provenance.diff(drafted, "main:merge_1", "main:merge_2")


def _i1_history(repo, prefix, rounds=(1, 2), when="@946684800 +0000"
                ) -> tuple[str, str, dict[int, str], dict[int, str]]:
    blobs, trees, entries = {}, {}, []
    for n in rounds:
        blobs[n] = provenance._blob(repo, f"{prefix} round {n}\n")
        trees[n] = provenance._tree(repo, [("100644", "blob", blobs[n], "results.tex")])
        entries.append(("040000", "tree", trees[n], f"merge_{n}"))
    root = provenance._tree(repo, entries)
    commit = provenance._commit(repo, root, [], "I1 fixture", when=when)
    return commit, root, trees, blobs


def _i1_fixture(ws):
    repo = provenance.ensure_repo(ws)
    original = _i1_history(repo, "original")
    first = _i1_history(repo, "replacement", when="@978307200 +0000")
    second = _i1_history(repo, "changed", when="@1009843200 +0000")
    vocabulary = [_i1_history(repo, "foreign", (n,), when="@978307200 +0000")
                  for n in (9, 10)]
    provenance._update_ref(repo, "refs/heads/main", original[0])
    return repo, original, first, second, vocabulary


def _i1_repo_bytes(repo):
    return {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


@pytest.mark.parametrize("kind", ["commit", "tree", "blob"])
def test_i1_replacement_add_change_remove_preserves_original_authority_and_content(ws, kind):
    repo, original, first, second, vocabulary = _i1_fixture(ws)
    target = {"commit": original[0], "tree": original[1], "blob": original[3][1]}[kind]
    replacements = ([v[0] if kind == "commit" else v[1] for v in vocabulary]
                    if kind != "blob" else [first[3][1], second[3][1]])
    identity = provenance._repo_identity(repo)
    tips = provenance._read_branch_tips(repo)
    expected = provenance.points(ws)
    assert expected == [
        {"ref": f"main:merge_{n}", "label": f"Merge round {n}", "kind": "merge",
         "commit": original[0], "at": "2000-01-01T00:00:00+00:00"} for n in (2, 1)
    ]
    baseline = provenance.diff(ws, "main:merge_1", "main:merge_2")
    assert "-original round 1" in baseline["text"] and "+original round 2" in baseline["text"]
    with provenance.read_scope():
        assert provenance.points(ws) == expected
        for replacement in replacements:
            provenance._git(repo, "replace", "-f", target, replacement)
            # Positive controls prove these real replacements affect ordinary Git.
            if kind == "blob":
                redirected = provenance._git(repo, "diff", "--no-color",
                                             f"{original[0]}:merge_1", f"{original[0]}:merge_2")
                assert "-original round 1" not in redirected
                assert "-replacement round 1" in redirected or "-changed round 1" in redirected
            else:
                redirected = provenance._git(repo, "ls-tree", "-r", "-t", original[0])
                assert "merge_9" in redirected or "merge_10" in redirected
                assert "merge_1/" not in redirected
            before_read = _i1_repo_bytes(repo)
            assert provenance._repo_identity(repo) == identity
            assert provenance._read_branch_tips(repo) == tips
            cached = provenance.points(ws)
            with provenance.read_scope():
                cold = provenance.points(ws)
                cold_diff = provenance.diff(ws, "main:merge_1", "main:merge_2")
            assert cached == cold == expected, "replacement refs must not reinterpret checked OIDs"
            assert cold_diff == provenance.diff(ws, "main:merge_1", "main:merge_2") == baseline
            assert _i1_repo_bytes(repo) == before_read, "reads must not repair refs, objects or config"
            assert provenance._ref_sha(repo, f"refs/replace/{target}") == replacement
        provenance._git(repo, "replace", "-d", target)
        assert provenance.points(ws) == expected
        with provenance.read_scope():
            assert provenance.points(ws) == expected
        assert provenance.diff(ws, "main:merge_1", "main:merge_2") == baseline
    assert provenance.points(ws) == expected
    assert provenance.diff(ws, "main:merge_1", "main:merge_2") == baseline


@pytest.mark.parametrize("kind", ["commit", "tree", "blob"])
@pytest.mark.parametrize("change_existing", [False, True])
def test_i1_replacement_after_validation_cannot_redirect_real_oid_diff(ws, monkeypatch, kind,
                                                                    change_existing):
    repo, original, first, second, _vocabulary = _i1_fixture(ws)
    target = {"commit": original[0], "tree": original[2][1], "blob": original[3][1]}[kind]
    select = lambda history: {"commit": history[0], "tree": history[2][1],
                              "blob": history[3][1]}[kind]
    expected = provenance.diff(ws, "main:merge_1", "main:merge_2")
    assert "-original round 1" in expected["text"] and "+original round 2" in expected["text"]
    real_run = provenance.subprocess.run
    diff_argv, executed = [], []

    def replace_before_diff(argv, **kwargs):
        if argv[:4] == ["git", "--git-dir", str(repo), "diff"]:
            diff_argv.append(argv[4:])
            real_run(["git", "--git-dir", str(repo), "replace", "-f", target, select(second)],
                     check=True, capture_output=True, env=provenance._env())
            before_read = _i1_repo_bytes(repo)
            control = real_run(argv, capture_output=True, text=True, check=True,
                               env=provenance._env()).stdout.strip()
            assert "-changed round 1" in control and control != expected["text"]
            result = real_run(argv, **kwargs)
            assert _i1_repo_bytes(repo) == before_read
            executed.append(result.returncode)
            return result
        return real_run(argv, **kwargs)

    with provenance.read_scope():
        points = provenance.points(ws)
        assert all(p["commit"] == original[0] and p["at"] == "2000-01-01T00:00:00+00:00"
                   for p in points)
        if change_existing:
            provenance._git(repo, "replace", target, select(first))
        monkeypatch.setattr(provenance.subprocess, "run", replace_before_diff)
        actual = provenance.diff(ws, "main:merge_1", "main:merge_2")
    assert diff_argv == [["--no-color", f"{original[0]}:merge_1", f"{original[0]}:merge_2"]]
    assert executed == [0], "a real final subprocess must execute successfully"
    assert actual == expected, "public fields and original text must survive post-validation replacement"
    assert actual["a"] == "main:merge_1" and actual["b"] == "main:merge_2"
    assert actual["truncated"] is False
    assert provenance._ref_sha(repo, f"refs/replace/{target}") == select(second)


def test_i1_reads_use_exact_sealed_suppression_but_writers_keep_base_environment(ws, monkeypatch):
    repo, original, _first, _second, _vocabulary = _i1_fixture(ws)
    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "hostile-not-the-fixed-value")
    monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/hostile/")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "99")
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-secret-never-inherited")
    base = provenance._env()
    assert not {"GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_CONFIG_COUNT",
                "MODEL_API_KEY"} & base.keys()
    real_run = provenance.subprocess.run
    reads, writes = [], []

    def observe(argv, **kwargs):
        if argv[:3] == ["git", "--git-dir", str(repo)]:
            command = argv[3]
            if command in {"for-each-ref", "ls-tree", "diff"}:
                reads.append(argv[3:])
                assert kwargs["env"] == {**base, "GIT_NO_REPLACE_OBJECTS": "1"}
                assert kwargs["timeout"] == provenance._TIMEOUT_S
                if command == "diff":
                    assert kwargs["text"] and kwargs["encoding"] == "utf-8"
                    assert kwargs["errors"] == "replace"
                else:
                    assert not kwargs.get("text", False) and "encoding" not in kwargs
            else:
                writes.append(command)
                expected = dict(base)
                if command == "commit-tree":
                    expected.update(GIT_AUTHOR_DATE="@946684800 +0000",
                                    GIT_COMMITTER_DATE="@946684800 +0000")
                assert kwargs["env"] == expected
        return real_run(argv, **kwargs)

    monkeypatch.setattr(provenance.subprocess, "run", observe)
    before_read = _i1_repo_bytes(repo)
    with provenance.read_scope():
        assert [p["commit"] for p in provenance.points(ws)] == [original[0]] * 2
        result = provenance.diff(ws, "main:merge_1", "main:merge_2")
    assert "-original round 1" in result["text"] and "+original round 2" in result["text"]
    assert _i1_repo_bytes(repo) == before_read and writes == []
    assert [args[0] for args in reads] == ["for-each-ref", "ls-tree", "for-each-ref", "diff"]
    blob = provenance._blob(repo, "writer control\n")
    tree = provenance._tree(repo, [("100644", "blob", blob, "control")])
    commit = provenance._commit(repo, tree, [], "writer", when="@946684800 +0000")
    provenance._update_ref(repo, "refs/heads/control", commit)
    assert writes == ["hash-object", "mktree", "commit-tree", "update-ref"]
    assert provenance._env() == base


@pytest.mark.parametrize("kind", ["commit", "tree", "blob"])
def test_i1_unscoped_reads_with_live_replacement_transitions_keep_original_bytes(ws, kind):
    """Post-fix coverage: unscoped calls also ignore added/changed/removed refs."""
    repo, original, first, second, _vocabulary = _i1_fixture(ws)
    target = {"commit": original[0], "tree": original[2][1], "blob": original[3][1]}[kind]
    expected_points = provenance.points(ws)
    expected_diff = provenance.diff(ws, "main:merge_1", "main:merge_2")
    assert provenance._read_memo.get() is None
    identity, tips = provenance._repo_identity(repo), provenance._read_branch_tips(repo)
    for history in (first, second, None):
        if history is None:
            provenance._git(repo, "replace", "-d", target)
        else:
            replacement = {"commit": history[0], "tree": history[2][1],
                           "blob": history[3][1]}[kind]
            provenance._git(repo, "replace", "-f", target, replacement)
            raw_diff = provenance._git(repo, "diff", "--no-color",
                                       f"{original[0]}:merge_1", f"{original[0]}:merge_2")
            assert raw_diff != expected_diff["text"]
        before_read = _i1_repo_bytes(repo)
        assert provenance._repo_identity(repo) == identity
        assert provenance._read_branch_tips(repo) == tips
        assert provenance.points(ws) == expected_points
        assert provenance.diff(ws, "main:merge_1", "main:merge_2") == expected_diff
        assert provenance._read_memo.get() is None
        assert _i1_repo_bytes(repo) == before_read
