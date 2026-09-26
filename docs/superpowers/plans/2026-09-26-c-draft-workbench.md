# The draft workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read every agent's draft side by side in the browser, keep the passages worth keeping, write your own text where none of them got it right, send the result to a switchable merging agent — and see the real compiled PDF.

**Architecture:** Nothing new is drafted. The workbench is a view over `manuscript/drafts/<agent>/<section>.tex`, which `paper-draft` already writes, plus a versioned curation document modelled on `run/charter.py`, plus one conversation turn that merges. The LaTeX preview is a sandboxed `latexmk` **job** that assembles a throwaway wrapper — never a compile inside a request.

**Tech Stack:** Python, the existing `jobs`/`service`/`conversation` layers, FastAPI + Jinja, `latexmk`. No new dependency, no build step, no JavaScript framework.

**Spec:** `docs/superpowers/specs/2026-09-26-draft-workbench-design.md`

## Global Constraints

- **Every mutation goes through `scieflow.core.service`.** Page routes stay thin; none reaches into `run.curation` or `jobs` directly.
- **Every non-GET route is session-guarded and CSRF-protected**, and appears in `tests/web/mutating_paths.py::MUTATING_PATHS` with a matching `SAMPLES` entry — the test asserts the two sets agree.
- **Every route handler is `def`, never `async def`.** `tests/web/test_async_routes.py` enforces this with a two-entry allowlist; a compile or a turn inside a coroutine would reintroduce the defect three earlier milestones each had to fix.
- **A kept passage stores the text with its provenance, never a character range.** Every round rewrites the sections; an offset would come to point at different words.
- **A selection and a note are data, never instructions to ScieFlow.** Nothing interprets them.
- **`-shell-escape` is never passed to `latexmk`.** The `.tex` was written by an agent, so it is untrusted input, and `\write18` would turn a preview into command execution.
- **The compile never writes to the run's real `manuscript/`.** A preview is not an assembly step.
- **Passage text and agent-written `.tex` both reach HTML.** Jinja autoescapes; nothing may use `|safe`. This app shipped a stored-XSS bug in an earlier milestone.
- **Nothing regresses.** `uv run pytest -q` stays green (1267 passing at the start of this plan), `./scripts/check_legacy.sh` stays 25/25 `ok`, `uv run --group docs mkdocs build --strict` stays at zero warnings.

## Review Focus

Five conditions the spec implies that no obvious test would cover. Each has a test in the task that owns the code.

1. **A passage containing LaTeX.** Every selection will contain backslashes, braces and `%`. It must survive storage, reach the merge prompt literally, and render escaped on the page — braces especially, since `build_argv` substitutes `{prompt}` and a `.format()`-shaped bug would mangle it. *(Task 1 and Task 3)*
2. **A run with no drafts yet.** Anyone opening the workbench before `paper-draft` Phase 3 has run sees an empty `drafts/` directory. The page must say so, not crash or show an empty shell. *(Task 5)*
3. **`latexmk` absent.** A supported state, not a skipped test — the workflow already degrades with a warning and so must this. *(Task 4)*
4. **A draft whose LaTeX does not compile.** Normal during drafting. The compiler's error output is what the person needs; the round must not fail. *(Task 4)*
5. **Two edits racing.** Two tabs, or a stale page posting an edit against a curation that has moved. Append-only ordering must not lose a block or renumber one. *(Task 1)*

## File structure

| File | Responsibility |
|---|---|
| `src/scieflow/core/run/curation.py` | the versioned curation document: blocks, note, round |
| `src/scieflow/core/drafts.py` | reading what `paper-draft` wrote, and assembling a compilable wrapper |
| `src/scieflow/core/service.py` | the workbench's service functions |
| `src/scieflow/web/{api,pages}.py` | the workbench routes |
| `src/scieflow/web/templates/drafts.html` | the three panels |
| `tests/core/test_curation.py` | storage, ordering, concurrency, refusals |
| `tests/core/test_drafts.py` | draft discovery, wrapper assembly, the compile |

---

### Task 1: The curation document

**Files:**
- Create: `src/scieflow/core/run/curation.py`
- Modify: `src/scieflow/core/events.py`
- Test: `tests/core/test_curation.py` (create)

**Interfaces:**
- Consumes: `scieflow.core.store` (`read_yaml`, `update_yaml`), `scieflow.core.events` (`emit`, `ACTORS`).
- Produces:
  `curation.CURATION_FILE = "manuscript/curation/document.yml"`;
  `curation.KINDS = frozenset({"kept", "mine"})`;
  `curation.read(ws) -> dict` — `{"round": int, "note": str, "blocks": [...], "version": int}`, empty shape when absent;
  `curation.keep(ws, text, *, agent, section, actor="human") -> dict` — appends a `kept` block;
  `curation.add_own(ws, text, actor="human") -> dict` — appends a `mine` block;
  `curation.edit_block(ws, block_id, text, actor="human") -> dict`;
  `curation.move_block(ws, block_id, position, actor="human") -> dict`;
  `curation.remove_block(ws, block_id, actor="human") -> dict`;
  `curation.set_note(ws, note, actor="human") -> dict`;
  `curation.advance_round(ws, actor="human") -> int`;
  `curation.history(ws) -> list[dict]` — every version, oldest first;
  `curation.revert(ws, version, actor="human") -> dict` — restores that version's blocks and note as a new version;
  `curation.as_text(ws) -> str` — the document rendered for a prompt;
  `curation.CurationError`.

Events `curation.changed` and `curation.round` join the closed vocabulary in `events.py`.

**Follow `run/charter.py`'s shape.** It solves the same problem — versioned run data with events and validate-before-write — and three modules on earlier branches shipped a write-before-validate defect that review had to catch. Read it first; validate every input *before* `store.update_yaml`, and raise `CurationError` rather than letting a bare `ValueError` out.

Each block carries a stable `id` (`store.new_id()`) so a later edit names a block rather than an index — an index would break the moment another tab reorders.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_curation.py
"""The curation document: kept passages, your own text, and their order.

A kept passage stores the TEXT with its provenance, never an offset into a
file the next round rewrites. These tests are written around that.
"""

import pytest

from scieflow.core import events
from scieflow.core.run import curation


@pytest.fixture
def ws(tmp_path):
    from scieflow.core.run import status

    workspace = tmp_path / "workspace" / "r1"
    workspace.mkdir(parents=True)
    status.write_status(workspace, status.new_status("r1", "autonomous"))
    return workspace


def test_a_run_without_curation_reads_as_empty(ws):
    doc = curation.read(ws)
    assert doc == {"round": 1, "note": "", "blocks": [], "version": 0}


def test_keeping_a_passage_records_where_it_came_from(ws):
    block = curation.keep(ws, "The catalyst degrades above 400 K.",
                          agent="claude", section="results")
    assert block["kind"] == "kept"
    assert block["agent"] == "claude" and block["section"] == "results"
    assert block["round"] == 1 and block["id"]
    assert curation.read(ws)["blocks"][0]["text"] == "The catalyst degrades above 400 K."
    assert "curation.changed" in [e["type"] for e in events.read(ws)]


def test_your_own_text_claims_no_provenance(ws):
    block = curation.add_own(ws, "We should say this plainly instead.")
    assert block["kind"] == "mine"
    assert not block.get("agent") and not block.get("section")


def test_blocks_keep_the_order_they_were_added(ws):
    curation.keep(ws, "first", agent="claude", section="intro")
    curation.add_own(ws, "second")
    curation.keep(ws, "third", agent="codex", section="methods")
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["first", "second", "third"]


def test_a_block_is_edited_by_id_not_by_index(ws):
    """Another tab reordering must not make an edit land on the wrong block."""
    first = curation.keep(ws, "first", agent="claude", section="intro")
    curation.add_own(ws, "second")
    curation.move_block(ws, first["id"], 1)           # first is now last
    curation.edit_block(ws, first["id"], "first, edited")
    texts = [b["text"] for b in curation.read(ws)["blocks"]]
    assert texts == ["second", "first, edited"]


def test_moving_a_block_reorders_without_losing_any(ws):
    ids = [curation.add_own(ws, t)["id"] for t in ("a", "b", "c")]
    curation.move_block(ws, ids[2], 0)
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["c", "a", "b"]


def test_removing_a_block_leaves_the_rest(ws):
    ids = [curation.add_own(ws, t)["id"] for t in ("a", "b", "c")]
    curation.remove_block(ws, ids[1])
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["a", "c"]


def test_an_unknown_block_id_is_refused_and_changes_nothing(ws):
    curation.add_own(ws, "only")
    for call in (lambda: curation.edit_block(ws, "nope", "x"),
                 lambda: curation.move_block(ws, "nope", 0),
                 lambda: curation.remove_block(ws, "nope")):
        with pytest.raises(curation.CurationError, match="block"):
            call()
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["only"]


def test_empty_text_is_refused_and_nothing_is_written(ws):
    with pytest.raises(curation.CurationError):
        curation.keep(ws, "   ", agent="claude", section="intro")
    assert curation.read(ws)["blocks"] == []


def test_an_invalid_actor_is_refused_before_anything_is_written(ws):
    """The same write-before-validate defect three earlier modules shipped."""
    with pytest.raises(curation.CurationError):
        curation.add_own(ws, "text", actor="wizard")
    assert curation.read(ws)["blocks"] == []
    assert events.read(ws) == []


def test_the_note_is_stored_separately_from_the_blocks(ws):
    curation.add_own(ws, "a passage")
    curation.set_note(ws, "Tighten the results section.")
    doc = curation.read(ws)
    assert doc["note"] == "Tighten the results section."
    assert [b["text"] for b in doc["blocks"]] == ["a passage"]


def test_advancing_a_round_keeps_the_blocks(ws):
    curation.keep(ws, "kept in round one", agent="claude", section="intro")
    assert curation.advance_round(ws) == 2
    doc = curation.read(ws)
    assert doc["round"] == 2
    assert doc["blocks"][0]["round"] == 1, "a block remembers the round it came from"
    assert "curation.round" in [e["type"] for e in events.read(ws)]


def test_every_change_is_a_version_and_any_version_restores(ws):
    """The spec requires the curation be restorable, like the charter: you
    trim it hard to fit a merge prompt, the round goes badly, and you want
    yesterday's selection back."""
    curation.keep(ws, "the good passage", agent="claude", section="results")
    curation.add_own(ws, "a second thought")
    trimmed = curation.read(ws)["version"]
    curation.remove_block(ws, curation.read(ws)["blocks"][0]["id"])
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["a second thought"]

    curation.revert(ws, trimmed)
    assert [b["text"] for b in curation.read(ws)["blocks"]] == [
        "the good passage", "a second thought"]
    assert curation.read(ws)["version"] > trimmed, "a revert is a new version, not a rewind"
    assert len(curation.history(ws)) == curation.read(ws)["version"]


def test_reverting_to_a_version_that_never_existed_is_refused(ws):
    curation.add_own(ws, "only")
    for bad in (0, 99, -1):
        with pytest.raises(curation.CurationError, match="version"):
            curation.revert(ws, bad)
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["only"]


def test_a_revert_restores_the_note_too(ws):
    curation.set_note(ws, "the original note")
    original = curation.read(ws)["version"]
    curation.set_note(ws, "replaced")
    curation.revert(ws, original)
    assert curation.read(ws)["note"] == "the original note"


def test_latex_in_a_passage_is_stored_byte_for_byte(ws):
    r"""Every real selection contains backslashes, braces and %. Braces
    matter most: `build_argv` substitutes `{prompt}`, so a `.format()`-shaped
    bug anywhere downstream would mangle a passage silently."""
    passage = r"\cite{smith2020} showed 95\% at $T={400}$ K % see note"
    curation.keep(ws, passage, agent="claude", section="results")
    assert curation.read(ws)["blocks"][0]["text"] == passage
    assert passage in curation.as_text(ws)


def test_as_text_shows_provenance_and_the_note(ws):
    curation.keep(ws, "from claude", agent="claude", section="results")
    curation.add_own(ws, "mine")
    curation.set_note(ws, "merge these")
    rendered = curation.as_text(ws)
    assert "claude" in rendered and "results" in rendered
    assert "from claude" in rendered and "mine" in rendered
    assert "merge these" in rendered


def test_concurrent_appends_do_not_lose_a_block(ws):
    import threading

    threads = [threading.Thread(target=curation.add_own, args=(ws, f"b{i}"))
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    blocks = curation.read(ws)["blocks"]
    assert len(blocks) == 8
    assert len({b["id"] for b in blocks}) == 8, "ids collided"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_curation.py -v`
Expected: FAIL — `No module named 'scieflow.core.run.curation'`.

- [ ] **Step 3: Write the implementation**

Create `src/scieflow/core/run/curation.py`, following `charter.py`'s structure — module docstring explaining *why*, a `CurationError(ValueError)`, `_path`/`_now` helpers, validation before every `store.update_yaml`, and `events.emit` after a successful write.

The parts worth stating exactly:

```python
CURATION_FILE = "manuscript/curation/document.yml"
KINDS = frozenset({"kept", "mine"})
```

`read` returns `{"round": int, "note": str, "blocks": list, "versions": int}` and treats a missing file as `{"round": 1, "note": "", "blocks": [], "version": 0}`. Guard a non-mapping document with `CurationError` the way `conversation.py` does, and catch `yaml.YAMLError` as well as `TypeError`/`ValueError` — a hand-edited file must not surface a raw stdlib error.

Every mutator validates first — actor against `events.ACTORS`, text non-blank, `kind` in `KINDS`, the block id present — then writes inside one `store.update_yaml` closure, then emits. `version` increments on every write, which is what makes a change visible without diffing.

**History and revert follow `charter.py` exactly.** Read its `_append`, `history` and `revert` and use the same storage shape: the document stores `current:` (the current version number) beside a `versions:` list of snapshots, each carrying `n`, `at`, `actor`, and here the `blocks` and `note` as they stood — the same two keys and the same `n` field `charter.py` uses.

**`read(ws)` returns `version` (singular), not `versions`.** On disk `versions` is the snapshot list; a return value using the same word for an integer count would give one key two meanings, and `history(ws)` is how a caller gets the list. `read` is therefore closer to `charter.snapshot` than to `charter.read`. `revert(version, …)` validates that the version exists, then appends the restored state as a **new** version rather than truncating; `test_every_change_is_a_version_and_any_version_restores` pins that, because a rewind would destroy the record of the trim you are undoing.

Like `charter.snapshot`, any function returning both the current state and its history must read the file **once** — a second read could pair one write's count with another write's blocks.

`as_text(ws)` renders the document for a prompt. Show each block's provenance so the merging agent knows what is quoted and what the human wrote — something like a `## Kept from <agent> (<section>, round N)` heading before a `kept` block and `## Written by the author` before a `mine` one, then the note under its own heading. The text itself is inserted verbatim; nothing is escaped, templated or `.format()`-ed.

In `src/scieflow/core/events.py`, extend the closed vocabulary:

```python
    "turn.sent", "turn.received", "turn.session_lost",
    "curation.changed", "curation.round",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_curation.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/run/curation.py src/scieflow/core/events.py tests/core/test_curation.py
git commit -m "feat(core): the curation document — kept passages, your own text, their order"
```

---

### Task 2: Reading the drafts and the rounds, and the workbench's service functions

**Files:**
- Create: `src/scieflow/core/drafts.py`
- Modify: `src/scieflow/core/service.py`, `tests/core/conftest.py`
- Test: `tests/core/test_drafts.py` (create), `tests/core/test_service.py` (append)

**Move** the `project` fixture from `tests/core/test_service.py` into `tests/core/conftest.py` unchanged, deleting the original. Tasks 3 and 4 add test modules that need the same fixture, and three copies of a 25-line fixture is the verbatim duplication the review rubric exists to catch. Nothing else about it changes, and `test_service.py` picks it up from the conftest.

**Interfaces:**
- Consumes: `curation.*` from Task 1, `service._ws`, `service.ServiceError`, `web.files.resolve` as the containment model to mirror.
- Produces:
  `drafts.DRAFTS_DIR = "manuscript/drafts"`;
  `drafts.agents(ws) -> list[str]` — subdirectories of `drafts/`, sorted;
  `drafts.sections(ws, agent) -> list[str]` — that agent's section names, sorted;
  `drafts.read_section(ws, agent, section) -> str`;
  `drafts.ROUNDS_DIR = "manuscript/curation/rounds"`;
  `drafts.rounds(ws) -> list[int]` — completed merge rounds, ascending;
  `drafts.round_sections(ws, n) -> list[str]`;
  `drafts.read_round_section(ws, n, section) -> str`;
  `drafts.source_dir(ws, source) -> Path` — resolves `"agent:<name>"` or `"round:<n>"` to the directory holding that draft's sections; raises `DraftError` for anything else;
  `drafts.DraftError`;
  `service.workbench(project, slug) -> dict` — `{"agents": [...], "sections": [...], "drafts": {agent: {section: text}}, "rounds": {n: {section: text}}, "curation": {...}}`;
  `service.keep_passage(project, slug, text, agent, section) -> dict`;
  `service.add_own_text(project, slug, text) -> dict`;
  `service.edit_curation_block(project, slug, block_id, text) -> dict`;
  `service.move_curation_block(project, slug, block_id, position) -> dict`;
  `service.remove_curation_block(project, slug, block_id) -> dict`;
  `service.set_curation_note(project, slug, note) -> dict`;
  `service.revert_curation(project, slug, version) -> dict`;
  `service.curation_history(project, slug) -> list[dict]`.

**`agent` and `section` arrive from a form, so they name files.** `drafts.read_section` must refuse anything that escapes `manuscript/drafts/`. `src/scieflow/web/files.py::resolve` is the codebase's pattern for this — resolve first, then check containment on the *resolved* path against the *resolved* root, never on the raw string. Mirror that logic here rather than importing a web module into core, and say so in a comment.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_drafts.py
"""Reading what `paper-draft` wrote.

`agent` and `section` arrive from a web form and are used to build a path,
so the refusals below are the load-bearing part of this module.
"""

import pytest

from scieflow.core import drafts


@pytest.fixture
def ws(tmp_path):
    workspace = tmp_path / "workspace" / "r1"
    for agent in ("claude", "codex"):
        d = workspace / "manuscript" / "drafts" / agent
        d.mkdir(parents=True)
        (d / "introduction.tex").write_text(f"\\section{{Intro}} by {agent}\n")
        (d / "results.tex").write_text(f"\\section{{Results}} by {agent}\n")
    return workspace


def test_agents_are_the_draft_subdirectories(ws):
    assert drafts.agents(ws) == ["claude", "codex"]


def test_sections_are_that_agents_tex_files(ws):
    assert drafts.sections(ws, "claude") == ["introduction", "results"]


def test_reading_a_section_returns_its_source(ws):
    assert "by claude" in drafts.read_section(ws, "claude", "introduction")


def test_a_run_with_no_drafts_reads_as_empty(tmp_path):
    empty = tmp_path / "workspace" / "r1"
    empty.mkdir(parents=True)
    assert drafts.agents(empty) == []


@pytest.mark.parametrize("bad_agent", ["../../..", "a/b", "", "   ", ".."])
def test_an_agent_name_that_escapes_the_drafts_directory_is_refused(ws, bad_agent):
    with pytest.raises(drafts.DraftError):
        drafts.sections(ws, bad_agent)


@pytest.mark.parametrize("bad_section", ["../../../etc/passwd", "a/b", "..", ""])
def test_a_section_name_that_escapes_is_refused(ws, bad_section):
    with pytest.raises(drafts.DraftError):
        drafts.read_section(ws, "claude", bad_section)


def test_a_symlink_out_of_the_run_is_refused(ws, tmp_path):
    """Containment is checked on the resolved path, so a symlink cannot be
    trusted just because its name looks local."""
    secret = tmp_path / "secret.tex"
    secret.write_text("not yours")
    (ws / "manuscript" / "drafts" / "claude" / "sneaky.tex").symlink_to(secret)
    with pytest.raises(drafts.DraftError):
        drafts.read_section(ws, "claude", "sneaky")


def test_a_missing_section_is_refused_readably(ws):
    with pytest.raises(drafts.DraftError, match="nosuch"):
        drafts.read_section(ws, "claude", "nosuch")


def test_completed_rounds_are_listed_in_order(ws):
    """A merge round's output is the next round's left-hand pane, so it is
    read exactly like an agent's draft."""
    for n in (1, 2, 10):
        d = ws / "manuscript" / "curation" / "rounds" / str(n)
        d.mkdir(parents=True)
        (d / "results.tex").write_text(f"round {n}")
    assert drafts.rounds(ws) == [1, 2, 10], "sorted numerically, not as strings"
    assert drafts.round_sections(ws, 2) == ["results"]
    assert drafts.read_round_section(ws, 2, "results") == "round 2"


def test_a_run_with_no_rounds_reads_as_empty(ws):
    assert drafts.rounds(ws) == []


def test_a_non_numeric_round_directory_is_ignored(ws):
    """`curation/` also holds `document.yml`; only numbered round dirs count."""
    (ws / "manuscript" / "curation" / "rounds" / "draft-notes").mkdir(parents=True)
    assert drafts.rounds(ws) == []


def test_source_dir_resolves_both_kinds(ws):
    (ws / "manuscript" / "curation" / "rounds" / "3").mkdir(parents=True)
    assert drafts.source_dir(ws, "agent:claude").name == "claude"
    assert drafts.source_dir(ws, "round:3").name == "3"


@pytest.mark.parametrize("bad_source", [
    "agent:../../etc", "round:../../etc", "round:abc", "round:-1", "round:",
    "agent:", "claude", "", "agent:claude:extra", "file:/etc/passwd",
])
def test_a_source_that_is_not_a_draft_or_a_round_is_refused(ws, bad_source):
    """`source` arrives from a form and selects a directory to compile."""
    with pytest.raises(drafts.DraftError):
        drafts.source_dir(ws, bad_source)
```

Append to `tests/core/test_service.py`:

```python
def test_workbench_gathers_drafts_and_curation(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / "claude"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("\\section{Results} text")

    view = service.workbench(project, "r1")
    assert view["agents"] == ["claude"]
    assert view["sections"] == ["results"]
    assert "text" in view["drafts"]["claude"]["results"]
    assert view["curation"]["blocks"] == []


def test_keep_passage_records_provenance_through_the_service(project):
    from scieflow.core.run import curation

    ws = project.run_dir("r1")
    (ws / "manuscript" / "drafts" / "claude").mkdir(parents=True)
    service.keep_passage(project, "r1", "a passage", "claude", "results")
    block = curation.read(ws)["blocks"][0]
    assert block["agent"] == "claude" and block["section"] == "results"


def test_keep_passage_refuses_an_agent_that_escapes_the_run(project):
    with pytest.raises(service.ServiceError):
        service.keep_passage(project, "r1", "a passage", "../../etc", "passwd")


def test_the_curation_is_restorable_through_the_service(project):
    from scieflow.core.run import curation

    ws = project.run_dir("r1")
    service.add_own_text(project, "r1", "keep me")
    version = curation.read(ws)["version"]
    service.remove_curation_block(project, "r1", curation.read(ws)["blocks"][0]["id"])

    service.revert_curation(project, "r1", version)
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["keep me"]
    assert len(service.curation_history(project, "r1")) == curation.read(ws)["version"]


def test_reverting_to_an_unknown_version_is_a_service_error(project):
    with pytest.raises(service.ServiceError, match="version"):
        service.revert_curation(project, "r1", 99)


def test_curation_refusals_reach_the_caller_as_service_errors(project):
    with pytest.raises(service.ServiceError):
        service.add_own_text(project, "r1", "   ")
    with pytest.raises(service.ServiceError, match="block"):
        service.edit_curation_block(project, "r1", "nope", "text")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_drafts.py tests/core/test_service.py -k "drafts or workbench or curation or keep_passage" -v`
Expected: FAIL — `No module named 'scieflow.core.drafts'`.

- [ ] **Step 3: Write the implementation**

Create `src/scieflow/core/drafts.py` with `DRAFTS_DIR`, `DraftError(ValueError)`, and a private `_inside(root, *parts) -> Path` that resolves and then checks containment on the resolved path:

```python
def _inside(root: Path, *parts: str) -> Path:
    """`root/parts…`, refusing anything that resolves outside `root`.

    Mirrors the containment check `scieflow.web.files.resolve` applies to a
    browser-requested artifact path — resolve first (normalising `..` and
    following symlinks), then compare the *resolved* candidate against the
    *resolved* root, never the raw string. Written out here rather than
    imported, because core must not depend on the web layer.
    """
    base = Path(root).resolve()
    for part in parts:
        if not part or not part.strip() or "/" in part or part in {".", ".."}:
            raise DraftError(f"not a draft name: {part!r}")
    candidate = (base / Path(*parts)).resolve()
    if candidate != base and base not in candidate.parents:
        raise DraftError(f"path escapes the run: {'/'.join(parts)!r}")
    return candidate
```

`agents(ws)` returns sorted subdirectory names of `ws / DRAFTS_DIR`, or `[]` when the directory is absent — a run that has not drafted yet is an ordinary state, not an error. `sections(ws, agent)` returns sorted `.tex` stems. `read_section(ws, agent, section)` resolves through `_inside`, raises `DraftError` naming the section when it is missing, and reads with `encoding="utf-8"`.

Validate the **raw** section name before appending `.tex` — pass `agent` and `section` through `_inside`'s name check and only then build `f"{section}.tex"`. Appending first would turn `""` into the plausible filename `".tex"` and `".."` into `"...tex"`, so a bad name would be caught incidentally as a missing file rather than refused as a name.

The round readers are the same three functions over `ROUNDS_DIR`. `rounds(ws)` keeps only subdirectories whose name `.isdigit()` and sorts them as `int`, so `10` follows `2` and `document.yml`'s neighbours are ignored.

`source_dir` is the one function that turns a form value into a directory, so it validates by construction rather than by rejection:

```python
def source_dir(ws: Path, source: str) -> Path:
    """The directory holding one previewable draft's sections.

    `source` is `"agent:<name>"` or `"round:<n>"` and arrives from a form.
    Split once, match the kind exactly, and hand the remainder to `_inside`
    — never build a path from an unmatched string.
    """
    kind, _, rest = str(source).partition(":")
    if kind == "agent" and rest:
        return _inside(ws / DRAFTS_DIR, rest)
    if kind == "round" and rest.isdigit():
        return _inside(ws / ROUNDS_DIR, rest)
    raise DraftError(f"not a previewable draft: {source!r}")
```

`rest.isdigit()` is deliberate: it is false for `"-1"`, `"1.0"`, `""` and `"abc"`, so only a round that could exist gets as far as a path.

In `service.py`, add the thin wrappers — including `revert_curation` over `curation.revert` and `curation_history` over `curation.history`, so the versioning the document keeps is reachable from the page. `workbench` gathers everything the page needs in one call so the template does no I/O; each mutator translates `curation.CurationError` and `drafts.DraftError` into `ServiceError`:

```python
def workbench(project: Project, slug: str) -> dict:
    """Everything the workbench page shows, in one read."""
    ws = _ws(project, slug)
    agents = drafts.agents(ws)
    sections = sorted({s for a in agents for s in drafts.sections(ws, a)})
    return {
        "agents": agents,
        "sections": sections,
        "drafts": {a: {s: drafts.read_section(ws, a, s)
                       for s in drafts.sections(ws, a)} for a in agents},
        "rounds": {n: {s: drafts.read_round_section(ws, n, s)
                       for s in drafts.round_sections(ws, n)}
                   for n in drafts.rounds(ws)},
        "curation": curation.read(ws),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core tests/core
git commit -m "feat(core): read the drafts, and curate them through the service layer"
```

---

### Task 3: The merge round

**Files:**
- Modify: `src/scieflow/core/service.py`
- Test: `tests/core/test_merge_round.py` (create)

**Interfaces:**
- Consumes: `curation.read`, `curation.as_text`, `curation.advance_round` (Task 1); `service.say`, `service.dispatch_agent`, `service.TURN_PROMPT_DIR`.
- Produces:
  `service.ROUND_TARGET = "manuscript/curation/rounds"`;
  `service.merge_round(project, slug) -> dict` — returns `{"round": int, "turn": {...}}`.

**The merge is an ordinary conversation turn.** `service.say` already guards the budget, refuses a second turn in flight, proves the sandbox, resumes the agent's own session, records spend and writes the transcript. `merge_round` composes the prompt and calls `say`; it must not reimplement any of that, and it must not call `dispatch_agent` directly.

`say` writes the message it is given as the human turn, so the composed merge prompt appears in the conversation — which is correct: the curation *is* what was said this round.

The round advances **after** `say` returns, not before. A turn that fails raises out of `say`, and a round that never produced output must not consume a number — the next attempt is still round *n*.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_merge_round.py
"""Sending the curation to the merging agent.

The pinning test below is written by falsification: it fails if the curation
stops reaching the dispatched prompt. That is the requirement most likely to
rot silently, because everything else about a turn keeps working without it.
"""

import pytest

from scieflow.core import service
from scieflow.core.run import conversation, curation


@pytest.fixture
def curated(project):
    """A run with an agent that can hold a conversation and a curation to send.

    `stub` is the fixture's conversable agent — it carries `family: claude`,
    `session_cmd` and `resume_cmd`, which `sessions.can_converse` requires.
    """
    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    curation.keep(ws, "The catalyst degrades above 400 K.",
                  agent="claude", section="results")
    curation.add_own(ws, "State the limitation in the abstract too.")
    curation.set_note(ws, "Keep the methods section as claude wrote it.")
    return ws


def test_the_curation_reaches_the_dispatched_prompt(project, curated):
    """FALSIFICATION: delete the `curation.as_text(ws)` line from the prompt
    composition and this test fails. A test that only asserted the turn
    succeeded would pass with the pinning gone, and the merging agent would
    silently receive an empty instruction every round."""
    service.merge_round(project, "r1")

    prompts = sorted((curated / service.TURN_PROMPT_DIR).glob("turn-*.md"))
    assert prompts, "no prompt file was written"
    sent = prompts[-1].read_text()
    assert "The catalyst degrades above 400 K." in sent
    assert "State the limitation in the abstract too." in sent
    assert "Keep the methods section as claude wrote it." in sent
    assert "claude" in sent and "results" in sent, "provenance was dropped"


def test_the_prompt_names_where_to_write_the_merged_sections(project, curated):
    service.merge_round(project, "r1")
    sent = sorted((curated / service.TURN_PROMPT_DIR).glob("turn-*.md"))[-1].read_text()
    assert "manuscript/curation/rounds/1" in sent, (
        "the agent must be told the round directory, or its output lands nowhere "
        "the next round's left-hand pane will look")


def test_latex_in_a_passage_survives_into_the_prompt(project, curated):
    r"""Braces especially: `build_argv` substitutes `{prompt}` by `.replace()`
    precisely so a passage full of `{}` cannot be reinterpreted."""
    passage = r"\cite{smith2020} reached 95\% at $T={400}$ K"
    curation.keep(curated, passage, agent="codex", section="results")
    service.merge_round(project, "r1")
    sent = sorted((curated / service.TURN_PROMPT_DIR).glob("turn-*.md"))[-1].read_text()
    assert passage in sent


def test_the_round_advances_once_the_turn_completes(project, curated):
    assert curation.read(curated)["round"] == 1
    result = service.merge_round(project, "r1")
    assert result["round"] == 2
    assert curation.read(curated)["round"] == 2


def test_the_merge_appears_in_the_conversation(project, curated):
    service.merge_round(project, "r1")
    turns = conversation.read(curated)["turns"]
    assert [t["role"] for t in turns] == ["human", "agent"]
    assert "The catalyst degrades above 400 K." in turns[0]["text"]


def test_an_empty_curation_is_refused_and_does_not_advance_the_round(project):
    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    with pytest.raises(service.ServiceError, match="nothing"):
        service.merge_round(project, "r1")
    assert curation.read(ws)["round"] == 1
    assert conversation.read(ws)["turns"] == []


def test_a_note_alone_is_enough_to_send_a_round(project):
    """You may have nothing worth keeping and still want to say so."""
    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    curation.set_note(ws, "Start the results section again from the data.")
    assert service.merge_round(project, "r1")["round"] == 2


def test_a_failed_turn_leaves_the_round_where_it_was(project, curated, monkeypatch):
    """A round that produced no output must not consume a number."""
    def boom(*args, **kwargs):
        raise service.ServiceError("the agent died")

    monkeypatch.setattr(service, "say", boom)
    with pytest.raises(service.ServiceError):
        service.merge_round(project, "r1")
    assert curation.read(curated)["round"] == 1


def test_a_run_without_a_conversation_agent_is_refused(project):
    ws = project.run_dir("r1")
    curation.add_own(ws, "something")
    with pytest.raises(service.ServiceError, match="agent"):
        service.merge_round(project, "r1")
    assert curation.read(ws)["round"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_merge_round.py -v`
Expected: FAIL — `AttributeError: module 'scieflow.core.service' has no attribute 'merge_round'`.

- [ ] **Step 3: Write the implementation**

```python
ROUND_TARGET = "manuscript/curation/rounds"


def _merge_prompt(ws: Path, round_n: int) -> str:
    """What the merging agent is asked, with the curation pinned into it.

    The curation is the whole instruction: quoted passages with their
    provenance, the author's own text, and the note staged for this round.
    Dropping `curation.as_text` here would leave a turn that still succeeds
    and still costs budget while asking for nothing — which is why
    `test_the_curation_reaches_the_dispatched_prompt` is written to fail
    the moment this line goes.
    """
    target = f"{ROUND_TARGET}/{round_n}"
    return (
        f"Merge round {round_n}.\n\n"
        "The author has read every draft and curated the passages below. "
        "Produce one merged manuscript from them: keep the kept passages' "
        "substance, fold in the author's own text, and follow the note.\n\n"
        f"Write one file per section to `{target}/<section>.tex`, using the "
        "same section names as the drafts. Write nothing else.\n\n"
        "--- curation ---\n"
        f"{curation.as_text(ws)}\n"
        "--- end curation ---\n"
    )


def merge_round(project: Project, slug: str) -> dict:
    """Send this round's curation to the merging agent.

    An ordinary conversation turn — `say` guards the budget, refuses a second
    turn in flight, proves the sandbox, resumes the agent's session and
    records the spend. Nothing here duplicates that.
    """
    ws = _ws(project, slug)
    try:
        doc = curation.read(ws)
    except curation.CurationError as exc:
        raise ServiceError(str(exc)) from exc
    if not doc["blocks"] and not doc["note"].strip():
        raise ServiceError(
            "nothing to merge: keep a passage, write your own text, or leave a note")

    turn = say(project, slug, _merge_prompt(ws, doc["round"]))
    return {"round": curation.advance_round(ws), "turn": turn}
```

Note the order: `_merge_prompt` is built from the round *before* it advances, so the directory the agent is told to write matches the round the output belongs to.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_merge_round.py -v && uv run pytest -q`
Expected: all pass; full suite green.

Then prove the falsification test bites: comment out the `{curation.as_text(ws)}` line, run `test_the_curation_reaches_the_dispatched_prompt`, watch it fail, restore the line. Record in your report that you did this and what the failure said — a falsification test nobody falsified is an assumption.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/service.py tests/core/test_merge_round.py
git commit -m "feat(core): send a merge round as a conversation turn with the curation pinned"
```

---

### Task 4: The compile job

**Files:**
- Create: `src/scieflow/core/preview.py`
- Modify: `src/scieflow/core/service.py`
- Test: `tests/core/test_preview.py` (create)

**Interfaces:**
- Consumes: `drafts.source_dir`, `drafts.sections`/`round_sections` (Task 2); `jobs.start`, `jobs.wait`, `jobs.run_blocking`; `sandbox.writable_for`, `sandbox.available`; `config.load_config`.
- Produces:
  `preview.LATEXMK = "latexmk"`;
  `preview.PREVIEW_DIR = "manuscript/curation/preview"`;
  `preview.TEMPLATE_DIR` — `Path(scieflow.research.__file__).parent / "templates" / "paper"`;
  `preview.available() -> bool`;
  `preview.compile_argv() -> list[str]`;
  `preview.assemble(ws, source, dest) -> Path` — writes the throwaway document, returns its `main.tex`;
  `preview.run_compile(project, ws, main, writable) -> jobs.Job` — starts and waits on the compile job;
  `preview.PreviewError`;
  `service.compile_preview(project, slug, source) -> dict` — the job;
  `service.preview_of(project, slug, source) -> dict` — `{"available": bool, "pdf": str|None, "log": str, "state": str|None}`.

**Three properties this task exists to hold.**

1. **`-shell-escape` is never in the argv.** The `.tex` was written by an agent. `\write18` would make a preview a command-execution primitive. `compile_argv()` exists as its own function precisely so a test can assert the flag's absence directly; a test that only checked "the compile succeeded" would not catch someone adding it to make a package work.
2. **The run's real `manuscript/` is never written.** The wrapper goes in `manuscript/curation/preview/<source>/`, a scratch directory this module owns. A preview is not an assembly step, and `paper-draft` Phase 5's output must not be shadowed by one.
3. **A missing `latexmk` is a supported state.** `available()` is false, `compile_preview` refuses with a readable reason, and the page keeps the source view. This is not a skipped test.

**The wrapper, and the bibliography trap.** `main.tex` in the shipped template ends `\bibliographystyle{plainnat}` / `\bibliography{references}`. With `-halt-on-error`, a missing `references.bib` halts the compile — so a draft with no bibliography yet would never preview. `assemble` therefore emits those two lines **only** when it copied a `references.bib`, and `\cite` keys simply render unresolved otherwise, which is the right trade for a preview.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_preview.py
"""Compiling one whole draft into a PDF.

A section `.tex` has no `\\documentclass`, and the template's `main.tex`
`\\input`s `sections/` — the *merged* output — so neither can preview a
draft. `assemble` builds a throwaway document around the directory being
previewed instead, in a scratch dir, never touching the run's manuscript.
"""

import shutil

import pytest

from scieflow.core import preview, service


@pytest.fixture
def drafted(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / "claude"
    d.mkdir(parents=True)
    (d / "introduction.tex").write_text("Catalysis matters.\n")
    (d / "results.tex").write_text("Yield was 95\\%.\n")
    return ws


def test_shell_escape_is_never_passed(drafted):
    """THE security property of this feature. The `.tex` was written by an
    agent, so `\\write18` would turn a preview into command execution. This
    asserts the flag's absence directly, because a test that only checked
    the compile succeeded would pass after someone added it."""
    argv = preview.compile_argv()
    joined = " ".join(argv)
    assert "-shell-escape" not in joined
    assert "--shell-escape" not in joined
    assert "-enable-write18" not in joined
    assert "shell-escape" not in joined, "not in any spelling, not in any argument"
    assert argv[0] == preview.LATEXMK
    assert "-halt-on-error" in argv and "-interaction=nonstopmode" in argv


def test_assemble_builds_a_document_around_the_draft(drafted):
    dest = drafted / "scratch"
    main = preview.assemble(drafted, "agent:claude", dest)
    text = main.read_text()
    assert "\\documentclass" in text
    assert "\\input{preamble}" in text
    assert (dest / "preamble.tex").exists(), "the preamble must travel with it"
    for section in ("introduction", "results"):
        assert f"\\input{{{section}}}" in text, "inputs point at the draft, not sections/"
        assert (dest / f"{section}.tex").exists()


def test_assemble_never_writes_into_the_runs_manuscript(drafted):
    before = sorted(p.relative_to(drafted) for p in (drafted / "manuscript").rglob("*"))
    preview.assemble(drafted, "agent:claude", drafted / "manuscript" / "curation" / "preview" / "x")
    after = sorted(p.relative_to(drafted) for p in (drafted / "manuscript").rglob("*"))
    added = set(after) - set(before)
    assert added, "nothing was written at all"
    assert all(str(p).startswith("manuscript/curation") for p in added), (
        f"a preview wrote outside its scratch directory: {added}")


def test_the_bibliography_is_emitted_only_when_there_is_one(drafted):
    """`-halt-on-error` plus a missing references.bib halts the compile, so a
    draft with no bibliography yet must still be previewable."""
    plain = preview.assemble(drafted, "agent:claude", drafted / "s1").read_text()
    assert "\\bibliography{" not in plain

    (drafted / "manuscript").mkdir(exist_ok=True)
    (drafted / "manuscript" / "references.bib").write_text("@article{a,title={t}}\n")
    withbib = preview.assemble(drafted, "agent:claude", drafted / "s2")
    assert "\\bibliography{references}" in withbib.read_text()
    assert (withbib.parent / "references.bib").exists()


def test_the_runs_own_preamble_wins_over_the_template(drafted):
    (drafted / "manuscript").mkdir(exist_ok=True)
    (drafted / "manuscript" / "preamble.tex").write_text("% the run's own preamble\n")
    main = preview.assemble(drafted, "agent:claude", drafted / "s")
    assert "the run's own preamble" in (main.parent / "preamble.tex").read_text()


def test_a_draft_before_assembly_uses_the_shipped_template(drafted):
    """Pre-Phase-5 there is no manuscript/preamble.tex; a preview must not
    demand the user assemble first."""
    main = preview.assemble(drafted, "agent:claude", drafted / "s")
    assert "geometry" in (main.parent / "preamble.tex").read_text()


def test_placeholders_get_plain_fallbacks(drafted):
    """A preview must not require the author list to be settled."""
    text = preview.assemble(drafted, "agent:claude", drafted / "s").read_text()
    for placeholder in ("%%TITLE%%", "%%AUTHORS%%", "%%DATE%%"):
        assert placeholder not in text


def test_a_source_with_no_sections_is_refused(project):
    ws = project.run_dir("r1")
    (ws / "manuscript" / "drafts" / "empty").mkdir(parents=True)
    with pytest.raises(preview.PreviewError, match="no sections"):
        preview.assemble(ws, "agent:empty", ws / "s")


def test_a_round_is_previewable_like_a_draft(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged results\n")
    main = preview.assemble(ws, "round:1", ws / "s")
    assert "\\input{results}" in main.read_text()


def test_a_missing_latexmk_is_a_supported_state(project, drafted, monkeypatch):
    """Not a skip. Phase 6 already degrades with a warning; so does this."""
    monkeypatch.setattr(preview.shutil, "which", lambda name: None)
    assert preview.available() is False
    with pytest.raises(service.ServiceError, match="latexmk"):
        service.compile_preview(project, "r1", "agent:claude")
    view = service.preview_of(project, "r1", "agent:claude")
    assert view["available"] is False
    assert "latexmk" in view["log"], "the page must be able to say why"


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_a_real_compile_produces_a_pdf(project, drafted):
    job = service.compile_preview(project, "r1", "agent:claude")
    assert job["state"] == "done", job
    view = service.preview_of(project, "r1", "agent:claude")
    assert view["pdf"] and (drafted / view["pdf"]).exists()
    assert (drafted / view["pdf"]).read_bytes()[:4] == b"%PDF"


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_broken_latex_reports_the_compiler_error_without_failing_the_round(project, drafted):
    """Normal during drafting. The error output is what a person needs."""
    (drafted / "manuscript" / "drafts" / "claude" / "results.tex").write_text(
        "\\begin{itemize}\nno end\n")
    job = service.compile_preview(project, "r1", "agent:claude")
    assert job["state"] == "failed"

    view = service.preview_of(project, "r1", "agent:claude")
    assert view["log"].strip(), "a failed compile with no error output is useless"
    from scieflow.core.run import curation
    assert curation.read(drafted)["round"] == 1, "a failed preview is not a failed round"


def test_the_compile_is_sandboxed_and_confined_to_its_run(project, drafted, monkeypatch):
    seen = {}

    def spy(prj, argv, **kwargs):
        seen["argv"] = argv
        seen["writable"] = kwargs.get("sandbox_writable")
        seen["kind"] = kwargs.get("kind")
        raise RuntimeError("stop here")

    monkeypatch.setattr(preview.jobs, "run_blocking", spy)
    monkeypatch.setattr(preview.shutil, "which", lambda name: "/usr/bin/latexmk")
    with pytest.raises(RuntimeError):
        service.compile_preview(project, "r1", "agent:claude")

    assert "shell-escape" not in " ".join(seen["argv"])
    assert seen["kind"] == "preview"
    assert seen["writable"] is not None, "a preview must not run unconfined"
    assert seen["writable"][0] == drafted, (
        "a preview is confined to the run it belongs to, like any dispatch")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_preview.py -v`
Expected: FAIL — `No module named 'scieflow.core.preview'`.

- [ ] **Step 3: Write the implementation**

Create `src/scieflow/core/preview.py`. Import `shutil` and `jobs` at module level under those exact names — the tests monkeypatch `preview.shutil.which` and `preview.jobs.run_blocking`.

```python
LATEXMK = "latexmk"
PREVIEW_DIR = "manuscript/curation/preview"
TEMPLATE_DIR = Path(research.__file__).parent / "templates" / "paper"


def available() -> bool:
    return shutil.which(LATEXMK) is not None


def compile_argv() -> list[str]:
    """`latexmk` as Phase 6 already invokes it — and no more.

    `-shell-escape` is deliberately absent and must stay absent: the `.tex`
    being compiled was written by an agent, so `\\write18` would turn a
    preview into arbitrary command execution. `test_shell_escape_is_never_passed`
    asserts this directly rather than inferring it from a successful compile.
    """
    return [LATEXMK, "-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"]
```

`assemble(ws, source, dest)`:
- `src = drafts.source_dir(ws, source)`; collect `sorted(src.glob("*.tex"))`; raise `PreviewError("no sections to preview: …")` when empty.
- `dest.mkdir(parents=True, exist_ok=True)`; copy each section `.tex` into `dest` with its own name — flat, so `\input{<section>}` resolves without a subdirectory.
- Copy the preamble: `ws / "manuscript" / "preamble.tex"` when it exists, else `TEMPLATE_DIR / "preamble.tex"`.
- Copy `ws / "manuscript" / "references.bib"` when it exists, and remember whether you did.
- Read `TEMPLATE_DIR / "main.tex"` and rewrite it: substitute the three placeholders, replace the template's `\input{sections/...}` lines with one `\input{<section>}` per section actually present (in the template's section order where they match, then any extras, so a draft reads in the conventional order), and keep the `\bibliographystyle`/`\bibliography` pair **only** when a `references.bib` was copied.
- Placeholders come from `config.load_config(ws)` where available, with fallbacks `"Draft preview"`, the run slug, and today's date — never a demand that the author list be settled first.
- Return `dest / "main.tex"`.

Do the `main.tex` rewrite by line, not by regex over the whole file: drop every line containing `\input{sections/`, insert the section inputs at the first such position, and drop the two bibliography lines when there is no `.bib`. A comment should say why the template cannot be used as-is (it `\input`s the merged output).

In `service.py`:

```python
def compile_preview(project: Project, slug: str, source: str) -> dict:
    """Compile one whole draft, as a job.

    A job and not an inline call: `latexmk` takes seconds to minutes, and
    this app runs one uvicorn process. It also gets the sandbox, the
    timeline and a Cancel button for free this way.
    """
    ws = _ws(project, slug)
    if not preview.available():
        raise ServiceError(
            f"{preview.LATEXMK} is not installed, so a draft cannot be compiled here; "
            "the source view still works (install texlive + latexmk for previews)")
    try:
        main = preview.assemble(ws, source, _preview_dest(ws, source))
    except (preview.PreviewError, drafts.DraftError) as exc:
        raise ServiceError(str(exc)) from exc

    writable = sandbox.writable_for(project, run_dir=ws, coordinator=False)
    return job_json(preview.run_compile(project, ws, main, writable))
```

`job_json` is `service.py`'s existing `Job`-to-dict helper (`asdict` plus `duration_s`); use it rather than inventing a shape.

The job itself is started inside `preview.py`, so the whole compile — the argv, the confinement, the job kind — lives in one module and one test can patch it:

```python
def run_compile(project, ws: Path, main: Path, writable: list[Path]) -> jobs.Job:
    """The compile, as a job confined to its own run.

    Blocking here is correct: the caller is a `def` route handler running in
    Starlette's threadpool, so the wait costs a thread, not the event loop.
    """
    return jobs.run_blocking(project, compile_argv(), kind="preview",
                             cwd=main.parent, run_dir=ws,
                             label=f"preview {main.parent.name}",
                             sandbox_writable=writable)
```

`jobs.run_blocking(project, argv, **kw)` forwards everything to `jobs.start`, so `kind`, `cwd`, `run_dir`, `label` and `sandbox_writable` are all accepted.

`_preview_dest(ws, source)` is `ws / PREVIEW_DIR / source.replace(":", "-")`, so `agent:claude` and `round:1` get their own directories and a rebuild reuses `latexmk`'s aux files.

`preview_of(project, slug, source)` reports without compiling: `available()`, the relative path of `main.pdf` under the run when it exists (so the existing artifact route can serve it inline — `application/pdf` is already in `files.INLINE_SAFE_TYPES`), the last job's state, and the log. The log is `main.log` when present, else the reason `latexmk` is unavailable. Never raise from this function for an absent preview — "not compiled yet" is the normal state of every draft.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_preview.py -v && uv run pytest -q`
Expected: all pass. Report which of the two `latexmk` tests ran and which skipped — `tests/research/test_paper_template.py` skips the same way, so a host without `latexmk` is expected; `test_a_missing_latexmk_is_a_supported_state` must run either way.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/preview.py src/scieflow/core/service.py tests/core/test_preview.py
git commit -m "feat(core): compile one draft as a sandboxed preview job, shell-escape off"
```

---

### Task 5: The workbench page

**Files:**
- Create: `src/scieflow/web/templates/drafts.html`
- Modify: `src/scieflow/web/pages.py`, `src/scieflow/web/templates/run.html`, `tests/web/mutating_paths.py`
- Test: `tests/web/test_drafts_page.py` (create)

**Interfaces:**
- Consumes: `service.workbench`, `service.keep_passage`, `service.add_own_text`, `service.edit_curation_block`, `service.move_curation_block`, `service.remove_curation_block`, `service.set_curation_note`, `service.revert_curation`, `service.curation_history`, `service.merge_round` (Tasks 2–3).
- Produces: `GET /runs/{slug}/drafts`, `POST /runs/{slug}/drafts`.

**One POST route with an `action` field**, exactly as `/runs/{slug}/charter` and `/runs/{slug}/say` already do. That keeps the mutating inventory one entry wide and the guard test one case wide, and it is the pattern a reader of this app already knows.

**Both handlers are `def`.** `merge_round` runs a whole agent turn; an `async def` here would freeze the single-process server for its full `timeout_min`, which is the defect three earlier milestones each had to fix.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_drafts_page.py
"""The workbench page: three panels over one run's drafts."""

import pytest

from scieflow.core.run import curation


@pytest.fixture
def drafted(project):
    ws = project.run_dir("r1")
    for agent in ("claude", "codex"):
        d = ws / "manuscript" / "drafts" / agent
        d.mkdir(parents=True)
        (d / "results.tex").write_text(f"Yield was 95\\% per {agent}.\n")
    return ws


def test_the_page_shows_every_agents_draft(client, drafted):
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "claude" in page.text and "codex" in page.text
    assert "per claude" in page.text and "per codex" in page.text


def test_a_run_with_no_drafts_says_so(client, project):
    """REVIEW FOCUS 2: anyone opening the workbench before `paper-draft`
    Phase 3 has run has an empty `drafts/` directory. An empty shell with
    three blank panels tells them nothing about why."""
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "no drafts" in page.text.lower()
    assert "paper-draft" in page.text, "say which workflow produces them"


def test_an_unknown_run_is_404(client):
    assert client.get("/runs/nope/drafts").status_code == 404


def test_keeping_a_passage_stores_it_with_its_provenance(client, drafted):
    response = client.post("/runs/r1/drafts", data={
        "action": "keep", "text": "Yield was 95\\%.",
        "agent": "claude", "section": "results"})
    assert response.status_code in (200, 303)
    block = curation.read(drafted)["blocks"][0]
    assert block["agent"] == "claude" and block["section"] == "results"


def test_your_own_text_is_added_from_the_same_form(client, drafted):
    client.post("/runs/r1/drafts", data={"action": "mine", "text": "My own sentence."})
    blocks = curation.read(drafted)["blocks"]
    assert blocks[0]["kind"] == "mine" and blocks[0]["text"] == "My own sentence."


def test_a_block_is_edited_moved_and_removed(client, drafted):
    client.post("/runs/r1/drafts", data={"action": "mine", "text": "first"})
    client.post("/runs/r1/drafts", data={"action": "mine", "text": "second"})
    first, second = [b["id"] for b in curation.read(drafted)["blocks"]]

    client.post("/runs/r1/drafts", data={"action": "edit", "block": first, "text": "edited"})
    client.post("/runs/r1/drafts", data={"action": "move", "block": first, "position": "1"})
    assert [b["text"] for b in curation.read(drafted)["blocks"]] == ["second", "edited"]

    client.post("/runs/r1/drafts", data={"action": "remove", "block": second})
    assert [b["text"] for b in curation.read(drafted)["blocks"]] == ["edited"]


def test_a_trimmed_curation_is_restorable_from_the_page(client, drafted):
    """You trim hard to fit a merge prompt, the round goes badly, and you
    want the earlier selection back — so the versioning must be reachable
    from the only UI this feature has."""
    client.post("/runs/r1/drafts", data={"action": "mine", "text": "keep me"})
    version = curation.read(drafted)["version"]
    block = curation.read(drafted)["blocks"][0]["id"]
    client.post("/runs/r1/drafts", data={"action": "remove", "block": block})
    assert curation.read(drafted)["blocks"] == []

    client.post("/runs/r1/drafts", data={"action": "revert", "version": str(version)})
    assert [b["text"] for b in curation.read(drafted)["blocks"]] == ["keep me"]


def test_the_staging_note_is_saved(client, drafted):
    client.post("/runs/r1/drafts", data={"action": "note", "note": "Tighten the results."})
    assert curation.read(drafted)["note"] == "Tighten the results."


def test_the_curation_is_shown_with_where_each_passage_came_from(client, drafted):
    curation.keep(drafted, "kept text", agent="codex", section="results")
    page = client.get("/runs/r1/drafts")
    assert "kept text" in page.text
    assert "codex" in page.text and "results" in page.text


def test_a_refusal_comes_back_as_a_message_not_a_500(client, drafted):
    response = client.post("/runs/r1/drafts",
                           data={"action": "edit", "block": "nope", "text": "x"},
                           follow_redirects=True)
    assert response.status_code == 200
    assert "block" in response.text.lower()


def test_an_unknown_action_is_refused(client, drafted):
    response = client.post("/runs/r1/drafts", data={"action": "destroy"},
                           follow_redirects=True)
    assert response.status_code in (200, 400)
    assert curation.read(drafted)["blocks"] == []


def test_a_passage_cannot_inject_script_into_the_page(client, drafted):
    """REVIEW FOCUS 1, and this app's own history: a stored-XSS bug shipped
    in an earlier milestone. A kept passage is arbitrary text from a file an
    agent wrote, rendered back into HTML."""
    payload = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
    curation.keep(drafted, payload, agent="claude", section="results")
    page = client.get("/runs/r1/drafts")
    assert "<script>alert" not in page.text
    assert "onerror=alert" not in page.text
    assert "&lt;script&gt;" in page.text, "shown as text, so you can see what it says"


def test_agent_written_latex_is_escaped_too(client, drafted):
    """The draft source itself reaches HTML, and an agent wrote it."""
    (drafted / "manuscript" / "drafts" / "claude" / "results.tex").write_text(
        '<script>alert("from the tex")</script>')
    page = client.get("/runs/r1/drafts")
    assert "<script>alert" not in page.text


def test_the_run_page_links_to_the_workbench(client, drafted):
    page = client.get("/runs/r1")
    assert "/runs/r1/drafts" in page.text


def test_the_page_offers_the_merging_agent_and_the_send_button(client, drafted):
    page = client.get("/runs/r1/drafts")
    assert 'value="merge"' in page.text, "the button that sends the round"
    assert "/runs/r1/say" in page.text or 'name="agent"' in page.text, (
        "the merging agent is switchable from here")


def test_selection_capture_posts_the_agent_and_section(client, drafted):
    """The whole capture mechanism: `window.getSelection()` plus the two
    provenance fields. No editor framework, no build step."""
    page = client.get("/runs/r1/drafts")
    assert "getSelection" in page.text
    assert 'name="agent"' in page.text and 'name="section"' in page.text
```

Add to `tests/web/mutating_paths.py` — **both** dicts, or the equality assertion fails:

```python
    "/runs/{slug}/drafts": {"post"},
```

```python
    "/runs/{slug}/drafts": {"action": "mine", "text": "a passage"},
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_drafts_page.py -v`
Expected: FAIL — 404 on `/runs/r1/drafts`. `tests/web/test_read_only.py` also fails, because `mutating_paths.py` now names a route that does not exist: that is the inventory test working.

- [ ] **Step 3: Write the implementation**

In `pages.py`, following the shape of `run_page` and `edit_charter`:

```python
@router.get("/runs/{slug}/drafts", response_class=HTMLResponse)
def drafts_page(request: Request, slug: str, error: str = "") -> HTMLResponse:
    project = _project(request)
    try:
        view = service.workbench(project, slug)
    except service.ServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(request, "drafts.html", {
        "slug": slug, "error": error, "conversation": service.conversation_state(project, slug),
        "agents": service.conversational_agents(project),
        "history": service.curation_history(project, slug), **view})


@router.post("/runs/{slug}/drafts", dependencies=MUTATE)
def curate(request: Request, slug: str, action: str = Form(...),
           text: str = Form(""), agent: str = Form(""), section: str = Form(""),
           block: str = Form(""), position: str = Form("0"), note: str = Form(""),
           version: str = Form("0")):
    """Every workbench mutation, dispatched on `action` — the same shape
    `edit_charter` and `say` already use, which keeps the mutating-route
    inventory and its guard cases one entry wide.

    `def`, not `async def`: `merge_round` dispatches a whole agent turn and
    would otherwise block this app's single event loop for its entire
    `timeout_min`. `tests/web/test_async_routes.py` enforces it.
    """
    project = _project(request)
    try:
        if action == "keep":
            service.keep_passage(project, slug, text, agent, section)
        elif action == "mine":
            service.add_own_text(project, slug, text)
        elif action == "edit":
            service.edit_curation_block(project, slug, block, text)
        elif action == "move":
            service.move_curation_block(project, slug, block, _as_int(position))
        elif action == "remove":
            service.remove_curation_block(project, slug, block)
        elif action == "note":
            service.set_curation_note(project, slug, note)
        elif action == "revert":
            service.revert_curation(project, slug, _as_int(version))
        elif action == "merge":
            service.merge_round(project, slug)
        else:
            return _drafts_back(slug, f"unknown action: {action}")
    except service.ServiceError as exc:
        return _drafts_back(slug, str(exc))
    return _drafts_back(slug)
```

`_as_int(value)` turns a form string into an `int`, raising `ServiceError` rather than letting a `ValueError` reach the user as a 500 — both `position` and `version` arrive as text and both can be anything. `_drafts_back(slug, error="")` mirrors `_back`, redirecting to `/runs/{slug}/drafts` with the error as a query parameter.

`drafts.html` extends the app's base template and holds three regions. The drafts panel is one column per agent, each section in a `<pre class="draft" data-agent="…" data-section="…">` so the JS can read the provenance off the element the selection landed in. The curation panel lists each block with its provenance, an edit field, up/down and remove buttons. The third region is the note textarea, the merging-agent selector, and the send button.

The selection capture, inline, no framework:

```html
<form method="post" action="/runs/{{ slug }}/drafts" id="keep-form">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <input type="hidden" name="action" value="keep">
  <input type="hidden" name="agent" value="">
  <input type="hidden" name="section" value="">
  <textarea name="text" rows="4" placeholder="Select text in a draft, or type here"></textarea>
  <button type="submit">Keep this passage</button>
</form>
<script>
// A kept passage is a quotation, not a pointer: we post the TEXT plus which
// agent and section it came from. Every round rewrites the sections, so an
// offset would come to point at different words.
document.addEventListener('selectionchange', function () {
  const sel = document.getSelection();
  const chosen = sel ? sel.toString() : '';
  if (!chosen.trim()) { return; }
  let node = sel.anchorNode;
  while (node && !(node.dataset && node.dataset.agent)) { node = node.parentElement; }
  if (!node) { return; }
  const form = document.getElementById('keep-form');
  form.text.value = chosen;
  form.agent.value = node.dataset.agent;
  form.section.value = node.dataset.section;
});
</script>
```

Use the base template's existing CSRF token variable name — read another form in `run.html` rather than assuming `csrf_token`. Every passage and every draft body is rendered with plain `{{ … }}`; **no `|safe` anywhere**, which is what the two escaping tests above pin.

Add the link in `run.html` beside the existing charter and files links.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass, including `test_read_only.py`, `test_mutations.py` and `test_async_routes.py` — the three inventory tests that now cover the new route.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web
git commit -m "feat(web): the draft workbench — read every draft, keep passages, send a round"
```

---

### Task 6: The preview on the page

**Files:**
- Modify: `src/scieflow/web/pages.py`, `src/scieflow/web/templates/drafts.html`, `tests/web/mutating_paths.py`
- Test: `tests/web/test_drafts_preview.py` (create), `tests/web/test_sse.py` (append)

**Interfaces:**
- Consumes: `service.compile_preview`, `service.preview_of` (Task 4); the existing `/runs/{slug}/file?path=…` artifact route, which already serves `application/pdf` inline.
- Produces: `POST /runs/{slug}/preview`.

The preview is offered **per draft**, not per section — a section has no `\documentclass`. Each draft column and each completed round gets a Compile button; the result is shown in an `<iframe>` pointed at the existing artifact route, with the compiler's log beside it when the compile failed.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_drafts_preview.py
"""Compiling a draft from the workbench."""

import shutil

import pytest


@pytest.fixture
def drafted(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / "claude"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("Yield was 95\\%.\n")
    return ws


def test_each_draft_offers_a_compile_button(client, drafted):
    page = client.get("/runs/r1/drafts")
    assert "/runs/r1/preview" in page.text
    assert 'value="agent:claude"' in page.text


def test_a_completed_round_is_previewable_too(client, drafted):
    d = drafted / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")
    page = client.get("/runs/r1/drafts")
    assert 'value="round:1"' in page.text


def test_a_preview_source_that_is_not_a_draft_is_refused(client, drafted):
    response = client.post("/runs/r1/preview",
                           data={"source": "agent:../../etc"}, follow_redirects=True)
    assert response.status_code == 200
    assert "500" not in response.text
    assert not (drafted.parent / "etc").exists()


def test_the_page_says_so_when_latexmk_is_missing(client, drafted, monkeypatch):
    """REVIEW FOCUS 3: a supported state. The source view must keep working,
    and the page must explain why there is no PDF rather than showing a
    broken frame or a bare error."""
    from scieflow.core import preview

    monkeypatch.setattr(preview.shutil, "which", lambda name: None)
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "latexmk" in page.text
    assert "Yield was 95" in page.text, "the source view still works"

    response = client.post("/runs/r1/preview", data={"source": "agent:claude"},
                           follow_redirects=True)
    assert response.status_code == 200
    assert "latexmk" in response.text


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_a_compiled_preview_is_shown_in_the_page(client, drafted):
    client.post("/runs/r1/preview", data={"source": "agent:claude"}, follow_redirects=True)
    page = client.get("/runs/r1/drafts")
    assert "<iframe" in page.text
    assert "main.pdf" in page.text

    pdf_path = "manuscript/curation/preview/agent-claude/main.pdf"
    served = client.get(f"/runs/r1/file?path={pdf_path}")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("application/pdf")
    assert "attachment" not in served.headers.get("content-disposition", "")


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_a_failed_compile_shows_the_compiler_error_beside_the_source(client, drafted):
    """REVIEW FOCUS 4: an agent's LaTeX will not always build. That is normal
    during drafting, and the error output is the thing a person needs."""
    (drafted / "manuscript" / "drafts" / "claude" / "results.tex").write_text(
        "\\begin{itemize}\nno end\n")
    client.post("/runs/r1/preview", data={"source": "agent:claude"}, follow_redirects=True)
    page = client.get("/runs/r1/drafts")
    assert "Yield" not in page.text or "itemize" in page.text
    assert "itemize" in page.text or "error" in page.text.lower(), (
        "the compiler's own output must reach the page")
```

Add to **both** dicts in `tests/web/mutating_paths.py`:

```python
    "/runs/{slug}/preview": {"post"},
```
```python
    "/runs/{slug}/preview": {"source": "agent:claude"},
```

Append to `tests/web/test_sse.py`:

```python
def test_the_server_keeps_answering_while_a_preview_compiles(live, project):
    """A `latexmk` run takes seconds to minutes. The compile is a job for
    exactly that reason, and the handler is `def` — so it runs in Starlette's
    threadpool and the one event loop stays free. Same proof as the turn and
    cancel tests above: a real socket, a compile genuinely in flight, and
    `/healthz` still fast. A `TestClient` cannot exercise this; see this
    module's docstring.

    The stand-in `latexmk` sleeps, so this test does not need a TeX
    installation to prove the property that matters here."""
    import shutil as shutil_mod

    from scieflow.core import preview
    from scieflow.web.auth import CSRF_COOKIE

    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / "claude"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("text\n")

    fake = project.root / "slow-latexmk"
    fake.write_text("#!/bin/sh\nsleep 3\n")
    fake.chmod(0o755)
    original_which = shutil_mod.which
    preview.shutil.which = lambda name: str(fake) if name == preview.LATEXMK else original_which(name)
    preview.LATEXMK = str(fake)
    try:
        done = threading.Event()

        def compile_it():
            live.post("/runs/r1/preview", data={"source": "agent:claude"},
                      headers={"x-csrf-token": live.cookies[CSRF_COOKIE]}, timeout=30.0)
            done.set()

        worker = threading.Thread(target=compile_it, daemon=True)
        worker.start()
        time.sleep(0.5)
        assert not done.is_set(), "the compile finished too fast to prove anything"

        start = time.monotonic()
        health = live.get("/healthz")
        elapsed = time.monotonic() - start
        assert health.status_code == 200
        assert elapsed < 2.0, f"/healthz took {elapsed:.1f}s -- the event loop was blocked"

        worker.join(timeout=20.0)
        assert done.is_set(), "the compile never completed"
    finally:
        preview.shutil.which = original_which
        preview.LATEXMK = "latexmk"
```

If reassigning `preview.LATEXMK` turns out not to reach `compile_argv()` (it will, since `compile_argv` reads the module global), use `monkeypatch`-free explicit save/restore as written — this test runs against a live server in a thread, so a fixture-scoped patch is the wrong tool.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_drafts_preview.py -v`
Expected: FAIL — 404 on `/runs/r1/preview`; `test_read_only.py` fails on the new inventory entry.

- [ ] **Step 3: Write the implementation**

```python
@router.post("/runs/{slug}/preview", dependencies=MUTATE)
def compile_preview(request: Request, slug: str, source: str = Form(...)):
    """Compile one whole draft. `def`, so the compile runs in the threadpool."""
    project = _project(request)
    try:
        service.compile_preview(project, slug, source)
    except service.ServiceError as exc:
        return _drafts_back(slug, str(exc))
    return _drafts_back(slug)
```

`drafts_page` gains the preview state for every source it lists — one `service.preview_of` call per draft and round, folded into the context as `previews[source]`. Keep it to that one call per source; the dashboard's N+1 over `service.run_budget` is already a carried follow-up and this page should not add a second.

In `drafts.html`, each draft column's header gets a Compile form posting `source`, and below it:
- the PDF in `<iframe src="/runs/{{ slug }}/file?path={{ p.pdf }}" title="…">` when `p.pdf` is set,
- the log in a `<pre>` when the last compile failed — escaped like everything else,
- the reason in plain words when `p.available` is false, with the source view left exactly as it was.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass. Report which `latexmk`-gated tests skipped on this host; `test_the_page_says_so_when_latexmk_is_missing` and the live no-block test must both run regardless.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web
git commit -m "feat(web): compile and show a draft's PDF from the workbench"
```

---

### Task 7: Documentation

**Files:**
- Modify: `docs/web.md`, `AGENTS.md`
- Not modified: `docs/reference/cli.md` — this plan adds no CLI command, only web routes and service functions.
- Test: `uv run --group docs mkdocs build --strict`

**Interfaces:** consumes the routes and service functions from Tasks 2–6. Produces no code.

Document the workbench where the web app is already documented, matching that page's voice and depth — read it before writing.

- [ ] **Step 1: Write the documentation**

In `docs/web.md`, a section covering: what the workbench is for and where it lives (`/runs/<slug>/drafts`); that a kept passage is a quotation with provenance and *why* (the next round rewrites the sections, so a pointer would go stale — this is the one design fact a user needs in order to trust the feature); that the note is what gets said *about* the passages; that a round is an ordinary conversation turn, so it costs budget, appears on the timeline and can be cancelled; that the merging agent is switched the same way any conversation's agent is; and that the preview compiles one whole draft with `latexmk`, degrading to the source view when `latexmk` is absent.

State the file layout once, since a user will look for these on disk:

```
workspace/<slug>/manuscript/
  drafts/<agent>/<section>.tex        what paper-draft wrote
  curation/document.yml               your curation, versioned
  curation/rounds/<n>/<section>.tex   each merge round's output
  curation/preview/<source>/          throwaway preview builds
```

Say plainly that `-shell-escape` is off when compiling agent-authored LaTeX, and why.

In `AGENTS.md`, add the workbench to whatever inventory of web surfaces it already keeps — check first; if it keeps none, add nothing.

- [ ] **Step 2: Verify**

Run: `uv run --group docs mkdocs build --strict && ./scripts/check_legacy.sh && uv run pytest -q`
Expected: zero mkdocs warnings; 25/25 `ok`; suite green.

- [ ] **Step 3: Commit**

```bash
git add docs AGENTS.md
git commit -m "docs: the draft workbench and its LaTeX preview"
```
