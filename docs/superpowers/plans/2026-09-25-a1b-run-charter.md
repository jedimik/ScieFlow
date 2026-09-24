# Web control A1b — the run charter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every run a durable, versioned record of what has been agreed — and put it at the top of every agent dispatch's prompt, so a long conversation cannot drift away from the goal it started with.

**Architecture:** A charter is run data like status and budget: `workspace/<slug>/charter.yml`, holding an append-only list of versions and a pointer to the current one. `agent_run.prepare` prepends the current version to the prompt it composes, which is the single point every dispatch already flows through. The browser and the CLI both edit it through the service layer.

**Tech Stack:** Python, ruamel/PyYAML via the existing `store` helpers, FastAPI + Jinja for the panel. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-24-web-control-a1-design.md` — the "The run charter" section.

**Scope.** This plan covers the spec's charter section in full — the user writing one directly, and the coordinator proposing one through a gate. The coordinator *conversation* (job-per-turn with resumed agent sessions) and the Start wizard are **A1c**, the plan that follows. The charter comes first and alone because it is the primitive both depend on — and because the spec says the charter and the draft workbench (programme item C) are the same thing: *a human-curated, versioned document that becomes part of the next dispatch's prompt*. Building it properly here means C is this mechanism with a different editor on top, not a second system.

**Branch.** A1a is on `feat/m2c-web-control` (PR #1) and is not merged to `main`. Branch this work from `feat/m2c-web-control`, not from `main`, or the run page and service layer this plan extends will not be there.

## Global Constraints

- **Every mutation goes through `scieflow.core.service`.** No route may reach into `run.charter` directly.
- **Every non-GET route is session-guarded and CSRF-protected.** CSRF is validated centrally in `auth.install_session`'s middleware, which records its verdict on `request.state.csrf_checked`; route dependencies trust that flag and fail closed when it is absent.
- **`tests/web/mutating_paths.py::MUTATING_PATHS` is extended, never replaced.** `tests/web/test_read_only.py` asserts the app's mutating set equals it exactly, and `tests/web/test_mutations.py` drives its session and CSRF guard tests from the same dict, so a new route without a guard case fails loudly.
- **Run state is never hand-edited.** AGENTS.md rule 4: if the browser can set the charter, the CLI must be able to as well, or someone will edit `charter.yml` by hand.
- **The charter is data, never instructions to ScieFlow.** It is text a human wrote for an agent to read. Nothing in this plan interprets it, templates it, or executes it.
- **Nothing regresses.** `uv run pytest -q` stays green (1069 passing at the start of this plan), `./scripts/check_legacy.sh` stays 25/25 `ok`, `uv run --group docs mkdocs build --strict` stays at zero warnings.

## Review Focus

Five conditions the spec implies that no obvious test would cover. Each has a test in the task that owns the code.

1. **A charter containing `{model}`, `{prompt}` or shell metacharacters.** `agent_run.build_argv` substitutes `{model}`/`{reasoning}`/`{root}`/`{python}` and *then* `{prompt}`, so charter text is inserted last and its braces are never interpolated — the safety is real but rests entirely on that ordering. A test must pin it, or a future reorder silently turns charter text into command templating. *(Task 2)*
2. **A charter large enough to push the prompt past `PROMPT_ARGV_LIMIT` (100 kB) for an agent with no `stdin_cmd`.** `codex` has none. Today that path takes the `else` branch with `include_prompt=False`, dropping the `{prompt}` token and relying on the agent reading stdin. The charter is what makes prompts grow, so this plan is what makes that path reachable. *(Task 2)*
3. **Two tabs editing the charter at once.** Versions are append-only and numbered; a lost update or two versions sharing a number would corrupt the history the feature exists to provide. *(Task 1)*
4. **Reverting to a version that does not exist** — 0, negative, or past the end. Expect a readable refusal with the charter unchanged, not an `IndexError`. *(Task 1)*
5. **Every run that already exists has no `charter.yml`.** Dispatch, the run page and the API must all behave exactly as they do today for those runs. *(Task 2 and Task 3)*

## File structure

| File | Responsibility |
|---|---|
| `src/scieflow/core/run/charter.py` | the versioned document: read, set, revert, history |
| `src/scieflow/core/events.py` | two new event types in the closed vocabulary |
| `src/scieflow/core/agent_run.py` | prepends the current charter to every composed prompt |
| `src/scieflow/core/service.py` | the charter functions the browser and CLI share |
| `src/scieflow/core/run/cli.py` | `scieflow run charter` — show, set, revert |
| `src/scieflow/web/{api,pages}.py` | JSON and form routes |
| `src/scieflow/web/templates/run.html` | the charter panel beside the run |
| `tests/core/test_charter.py` | storage, versioning, concurrency, refusals |
| `tests/core/test_agent_run.py` | the pinning, by falsification |

---

### Task 1: The charter document

**Files:**
- Create: `src/scieflow/core/run/charter.py`
- Modify: `src/scieflow/core/events.py`
- Test: `tests/core/test_charter.py` (create)

**Interfaces:**
- Consumes: `scieflow.core.store` (`read_yaml`, `update_yaml`, `locked`), `scieflow.core.events.emit`.
- Produces:
  `charter.CHARTER_FILE = "charter.yml"`;
  `charter.read(ws) -> dict` — `{"current": int, "versions": [...]}`, or `{"current": 0, "versions": []}` when the file does not exist;
  `charter.current_text(ws) -> str` — `""` when there is no charter;
  `charter.set_text(ws, text, actor="human", note="") -> dict` — appends a version, returns it;
  `charter.revert(ws, version, actor="human") -> dict` — appends a *new* version copying an old one's text;
  `charter.history(ws) -> list[dict]` — newest first;
  `charter.CharterError` for an invalid version or empty text.

**Why reverting appends rather than rewinds:** the history is the point of the feature. A revert that deleted versions would destroy the record of how the goal moved, which is exactly what the spec says the user needs to see.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_charter.py
"""The run charter: a versioned record of what has been agreed."""

import pytest

from scieflow.core import events
from scieflow.core.run import charter


def test_a_run_without_a_charter_reads_as_empty(ws):
    assert charter.read(ws) == {"current": 0, "versions": []}
    assert charter.current_text(ws) == ""
    assert charter.history(ws) == []


def test_setting_the_first_version(ws):
    version = charter.set_text(ws, "Find a better catalyst.", note="initial goal")
    assert version["n"] == 1
    assert charter.current_text(ws) == "Find a better catalyst."
    assert [e["type"] for e in events.read(ws)] == ["charter.set"]


def test_each_set_appends_a_version(ws):
    charter.set_text(ws, "First goal.")
    charter.set_text(ws, "Second goal.")
    doc = charter.read(ws)
    assert doc["current"] == 2
    assert [v["n"] for v in doc["versions"]] == [1, 2]
    assert charter.current_text(ws) == "Second goal."


def test_history_is_newest_first_and_carries_provenance(ws):
    charter.set_text(ws, "First.", actor="agent", note="proposed")
    charter.set_text(ws, "Second.", actor="human")
    first_of_history = charter.history(ws)[0]
    assert first_of_history["n"] == 2 and first_of_history["actor"] == "human"
    assert charter.history(ws)[1]["note"] == "proposed"
    assert first_of_history["ts"]


def test_revert_appends_rather_than_rewinding(ws):
    """The history is the feature. A revert that deleted versions would
    destroy the record of how the goal moved."""
    charter.set_text(ws, "Original goal.")
    charter.set_text(ws, "Drifted goal.")
    restored = charter.revert(ws, 1)
    assert restored["n"] == 3
    assert charter.current_text(ws) == "Original goal."
    assert [v["n"] for v in charter.read(ws)["versions"]] == [1, 2, 3]
    assert "charter.reverted" in [e["type"] for e in events.read(ws)]


@pytest.mark.parametrize("bad", [0, -1, 99])
def test_reverting_to_a_version_that_does_not_exist_is_refused(ws, bad):
    """A stale tab, or a typed version number. Expect a readable refusal and
    an unchanged charter, not an IndexError."""
    charter.set_text(ws, "Only version.")
    with pytest.raises(charter.CharterError, match="version"):
        charter.revert(ws, bad)
    assert charter.current_text(ws) == "Only version."
    assert len(charter.read(ws)["versions"]) == 1


def test_empty_text_is_refused(ws):
    with pytest.raises(charter.CharterError):
        charter.set_text(ws, "   ")


def test_concurrent_writers_do_not_lose_a_version(ws):
    """Two tabs, or a tab and the CLI. Versions are append-only and numbered,
    so a lost update or two versions sharing a number would corrupt the very
    history this feature exists to provide."""
    import threading

    errors = []

    def write(i):
        try:
            charter.set_text(ws, f"goal {i}")
        except Exception as exc:              # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    numbers = [v["n"] for v in charter.read(ws)["versions"]]
    assert numbers == list(range(1, 9)), f"lost or duplicated versions: {numbers}"
    assert charter.read(ws)["current"] == 8
```

Add the `ws` fixture at the top of that file — a run workspace with a `status.yml`, because `events.emit` reads the run id from it:

```python
@pytest.fixture
def ws(tmp_path):
    from scieflow.core.run import status

    workspace = tmp_path / "workspace" / "r1"
    workspace.mkdir(parents=True)
    status.write_status(workspace, status.new_status("r1", "autonomous"))
    return workspace
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_charter.py -v`
Expected: FAIL — `No module named 'scieflow.core.run.charter'`.

- [ ] **Step 3: Write the implementation**

Create `src/scieflow/core/run/charter.py`:

```python
"""The run charter: a versioned record of what has been agreed.

Long conversations drift. The charter is the fix: a human-curated document
that ScieFlow puts at the top of every prompt it composes, so the agent is
handed the goal again each time it speaks.

Versions are append-only and a revert appends a copy rather than rewinding,
because seeing how the goal moved is the point — a history that edits itself
cannot show you that.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import events, store

CHARTER_FILE = "charter.yml"


class CharterError(ValueError):
    """A charter change that cannot be made; nothing was written."""


def _path(ws: Path) -> Path:
    return Path(ws) / CHARTER_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(ws: Path) -> dict:
    """The whole document. A run without a charter reads as empty, because
    every run that predates this feature has none."""
    doc = store.read_yaml(_path(ws), default=None)
    if not doc:
        return {"current": 0, "versions": []}
    return {"current": int(doc.get("current", 0)),
            "versions": list(doc.get("versions") or [])}


def current_text(ws: Path) -> str:
    doc = read(ws)
    for version in doc["versions"]:
        if version.get("n") == doc["current"]:
            return str(version.get("text", ""))
    return ""


def history(ws: Path) -> list[dict]:
    """Newest first — the order someone reviewing the drift wants."""
    return sorted(read(ws)["versions"], key=lambda v: v.get("n", 0), reverse=True)


def _append(ws: Path, text: str, actor: str, note: str) -> dict:
    """Append one version under the file lock, so concurrent writers cannot
    both claim the same number."""
    appended: dict = {}

    def bump(doc: dict) -> dict:
        versions = list((doc or {}).get("versions") or [])
        number = max((v.get("n", 0) for v in versions), default=0) + 1
        appended.update({"n": number, "ts": _now(), "actor": actor,
                         "text": text, "note": note})
        versions.append(dict(appended))
        return {"current": number, "versions": versions}

    store.update_yaml(_path(ws), bump)
    return appended


def set_text(ws: Path, text: str, actor: str = "human", note: str = "") -> dict:
    if not text or not text.strip():
        raise CharterError("a charter needs text")
    version = _append(ws, text, actor, note)
    events.emit(ws, "charter.set", actor, version=version["n"], note=note)
    return version


def revert(ws: Path, version: int, actor: str = "human") -> dict:
    """Make an earlier version current again by appending a copy of it."""
    try:
        wanted = int(version)
    except (TypeError, ValueError) as exc:
        raise CharterError(f"not a version number: {version!r}") from exc
    match = next((v for v in read(ws)["versions"] if v.get("n") == wanted), None)
    if match is None:
        raise CharterError(f"no charter version {wanted}")
    restored = _append(ws, str(match.get("text", "")), actor,
                       f"reverted to version {wanted}")
    events.emit(ws, "charter.reverted", actor,
                version=restored["n"], restored_from=wanted)
    return restored
```

In `src/scieflow/core/events.py`, add the two types to the closed vocabulary — `emit` refuses anything not listed:

```python
    "gate.opened", "gate.answered", "gate.withdrawn",
    "charter.set", "charter.reverted",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_charter.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/run/charter.py src/scieflow/core/events.py tests/core/test_charter.py
git commit -m "feat(core): the run charter, a versioned record of what was agreed

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Pin the charter into every composed prompt

**Files:**
- Modify: `src/scieflow/core/agent_run.py`
- Test: `tests/core/test_agent_run.py` (append)

**Interfaces:**
- Consumes: `charter.current_text(ws)`, `agent_run.prepare`, `agent_run.build_argv`, `agent_run.PROMPT_ARGV_LIMIT`.
- Produces: `agent_run.CHARTER_HEADER` and `agent_run.compose_prompt(run_dir, prompt) -> str`, used by `prepare`.

**This is the task the whole feature rests on.** The charter is only useful because it reaches the agent on *every* turn, and `prepare` is the single point every dispatch flows through. The test for it is written by falsification: deleting the pinning must make it fail.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_agent_run.py`)

```python
def test_the_charter_is_pinned_to_the_top_of_the_prompt(project_with_run):
    """Falsification test: this is the requirement most likely to rot
    silently. Without it the only symptom is an agent losing the goal,
    months later. Deleting the pinning from compose_prompt must fail here."""
    from scieflow.core.run import charter

    project, ws, prompt_file = project_with_run
    charter.set_text(ws, "Goal: find a better catalyst. Do not change it.")
    prompt_file.write_text("output: x.md\nkind: hypothesis\nNow do the next step.")

    dispatch = agent_run.prepare(project, "stub", prompt_file)
    sent = dispatch.stdin_text or " ".join(dispatch.argv)

    assert "find a better catalyst" in sent
    assert sent.index("find a better catalyst") < sent.index("Now do the next step")


def test_a_run_without_a_charter_composes_the_prompt_unchanged(project_with_run):
    """Every run that predates this feature has no charter.yml."""
    project, ws, prompt_file = project_with_run
    prompt_file.write_text("output: x.md\nkind: hypothesis\nbody")

    dispatch = agent_run.prepare(project, "stub", prompt_file)
    sent = dispatch.stdin_text or " ".join(dispatch.argv)

    assert agent_run.CHARTER_HEADER not in sent
    assert sent.rstrip().endswith("body")


def test_charter_braces_reach_the_agent_literally(project_with_run):
    """build_argv substitutes {model}/{reasoning}/{root}/{python} and THEN
    {prompt}, so charter text is inserted last and its braces are never
    interpolated. That safety is real but rests entirely on that ordering —
    this test is what stops a future reorder turning charter text into
    command templating."""
    from scieflow.core.run import charter

    project, ws, prompt_file = project_with_run
    charter.set_text(ws, "Use {model} and {root}; mind $PATH and `backticks`.")
    prompt_file.write_text("output: x.md\nkind: hypothesis\nbody")

    dispatch = agent_run.prepare(project, "stub", prompt_file)
    sent = dispatch.stdin_text or " ".join(dispatch.argv)

    assert "{model}" in sent and "{root}" in sent
    assert "`backticks`" in sent


def test_a_charter_that_pushes_the_prompt_over_the_argv_limit_still_sends_it(
        project_with_run, monkeypatch):
    """The charter is what makes prompts grow, so this plan is what makes
    this path reachable. An agent with no `stdin_cmd` (codex has none) must
    not end up invoked with no prompt at all."""
    from scieflow.core.run import charter

    project, ws, prompt_file = project_with_run
    monkeypatch.setattr(agent_run, "PROMPT_ARGV_LIMIT", 200)
    charter.set_text(ws, "G" * 500)
    prompt_file.write_text("output: x.md\nkind: hypothesis\nbody")

    dispatch = agent_run.prepare(project, "stub", prompt_file)
    sent = dispatch.stdin_text or " ".join(dispatch.argv)
    assert "G" * 500 in sent, "the prompt was dropped instead of sent on stdin"
```

The `project_with_run` fixture must yield `(project, ws, prompt_file)` where `ws` is the run workspace owning `prompt_file`. `tests/core/test_agent_run.py` already builds a project with a `stub` agent and a run; reuse that setup rather than inventing a second one, and add the fixture if the file does not already have one in this shape.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_agent_run.py -k charter -v`
Expected: FAIL — `module 'scieflow.core.agent_run' has no attribute 'CHARTER_HEADER'`, and the pinning assertions fail because nothing prepends anything.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/agent_run.py`, add the import and the composer:

```python
from scieflow.core.run import charter
```

```python
CHARTER_HEADER = "## The agreed plan for this run"


def compose_prompt(run_dir: Path | None, prompt: str) -> str:
    """The prompt the agent actually receives.

    The charter goes first, every time. That is the whole point of it: a long
    conversation drifts, and because ScieFlow composes each turn's prompt, the
    agent can be handed the goal again each time it speaks rather than being
    trusted to remember it.

    A run with no charter composes exactly as it did before this existed.
    """
    if run_dir is None:
        return prompt
    agreed = charter.current_text(run_dir).strip()
    if not agreed:
        return prompt
    return f"{CHARTER_HEADER}\n\n{agreed}\n\n---\n\n{prompt}"
```

Then, in `prepare`, compose before measuring the length — the charter counts
toward the argv limit, so the measurement has to happen after it is added:

```python
    prompt = compose_prompt(run_dir, prompt_file.read_text())
    use_stdin = len(prompt.encode()) > PROMPT_ARGV_LIMIT
```

Finally, close the drop-the-prompt hole the charter makes reachable. Today
an over-limit prompt for an agent with no `stdin_cmd` falls to the `else`
branch with `include_prompt=False`, so the `{prompt}` token is removed and
the agent is invoked with nothing:

```python
    if use_stdin and "stdin_cmd" in agent_cfg:
        argv = build_argv(agent_cfg, prompt, root, include_prompt=False,
                          template=agent_cfg["stdin_cmd"])
    elif use_stdin:
        # No stdin_cmd: drop the {prompt} token from the normal command and
        # send the text on stdin instead. `stdin_text` below carries it, so
        # the agent still receives the prompt rather than being invoked bare.
        argv = build_argv(agent_cfg, prompt, root, include_prompt=False)
    else:
        argv = build_argv(agent_cfg, prompt, root)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_agent_run.py -v && uv run pytest -q`
Expected: all pass; full suite green.

Then confirm by falsification, which is the point of this task — temporarily
make `compose_prompt` return `prompt` unchanged, re-run, and check that
`test_the_charter_is_pinned_to_the_top_of_the_prompt` **fails**. Restore the
implementation and re-run. Record both outputs in your report; a pinning test
that passes with the pinning removed is worse than no test.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/agent_run.py tests/core/test_agent_run.py
git commit -m "feat(core): pin the run charter to the top of every composed prompt

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The charter on the web — service, API, and the run page panel

**Files:**
- Modify: `src/scieflow/core/service.py`, `src/scieflow/web/api.py`, `src/scieflow/web/pages.py`, `src/scieflow/web/templates/run.html`, `src/scieflow/web/static/app.css`
- Modify: `tests/web/mutating_paths.py`
- Test: `tests/web/test_charter_page.py` (create)

**Interfaces:**
- Consumes: `charter.read`, `charter.current_text`, `charter.history`, `charter.set_text`, `charter.revert`, `charter.CharterError`; `service._ws`, `service.ServiceError`; `auth.csrf_token`, `pages.MUTATE`, `pages._back`.
- Produces:
  `service.run_charter(project, slug) -> dict` — `{"current": int, "text": str, "versions": [...]}`;
  `service.set_charter(project, slug, text, actor="human", note="") -> dict`;
  `service.revert_charter(project, slug, version, actor="human") -> dict`;
  `GET /api/v1/runs/{slug}/charter`, `POST /api/v1/runs/{slug}/charter`, `POST /api/v1/runs/{slug}/charter/revert`;
  `POST /runs/{slug}/charter` (page form, 303 back to the run).

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_charter_page.py
"""The charter panel: read it, edit it, revert it, from the browser."""

import pytest

from scieflow.core.run import charter
from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def test_the_api_reports_an_empty_charter_for_a_run_without_one(client):
    body = client.get("/api/v1/runs/r1/charter").json()
    assert body == {"current": 0, "text": "", "versions": []}


def test_setting_the_charter_over_the_api(client, project):
    assert post(client, "/api/v1/runs/r1/charter",
                text="Find a better catalyst.").status_code == 200
    assert charter.current_text(project.run_dir("r1")) == "Find a better catalyst."


def test_the_run_page_shows_the_charter_and_an_edit_form(client, project):
    charter.set_text(project.run_dir("r1"), "Find a better catalyst.")
    page = client.get("/runs/r1").text
    assert "Find a better catalyst." in page
    assert 'action="/runs/r1/charter"' in page


def test_the_run_page_of_a_run_without_a_charter_still_renders(client):
    """Every run that predates this feature has no charter.yml."""
    page = client.get("/runs/r1")
    assert page.status_code == 200
    assert "No charter" in page.text


def test_setting_the_charter_from_the_page_redirects_back(client, project):
    response = post(client, "/runs/r1/charter", text="A goal.", note="first")
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/r1"
    assert charter.current_text(project.run_dir("r1")) == "A goal."


def test_reverting_from_the_page(client, project):
    ws = project.run_dir("r1")
    charter.set_text(ws, "Original.")
    charter.set_text(ws, "Drifted.")
    assert post(client, "/runs/r1/charter", action="revert",
                version="1").status_code == 303
    assert charter.current_text(ws) == "Original."


def test_reverting_to_a_missing_version_explains_itself(client, project):
    charter.set_text(project.run_dir("r1"), "Only one.")
    response = post(client, "/runs/r1/charter", action="revert", version="99")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/r1?error=")
    assert "version" in client.get(response.headers["location"]).text
    assert charter.current_text(project.run_dir("r1")) == "Only one."


def test_empty_charter_text_is_refused(client, project):
    response = post(client, "/runs/r1/charter", text="   ")
    assert response.headers["location"].startswith("/runs/r1?error=")
    assert charter.current_text(project.run_dir("r1")) == ""


def test_charter_text_is_escaped_on_the_page(client, project):
    """Charter text can come from an agent's proposal, so it is not trusted
    markup."""
    charter.set_text(project.run_dir("r1"), "<script>alert('x')</script>")
    page = client.get("/runs/r1").text
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_a_charter_route_on_an_unknown_run_is_404(client):
    assert client.get("/api/v1/runs/nope/charter").status_code == 404
```

Extend `tests/web/mutating_paths.py` — `MUTATING_PATHS` with the three new
entries, and `SAMPLES` with a form body for each, so the session and CSRF
guard tests in `tests/web/test_mutations.py` pick them up automatically:

```python
    "/api/v1/runs/{slug}/charter": {"post"},
    "/api/v1/runs/{slug}/charter/revert": {"post"},
    "/runs/{slug}/charter": {"post"},
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_charter_page.py tests/web/test_read_only.py tests/web/test_mutations.py -v`
Expected: FAIL — the charter routes 404, and the inventory test reports the three new paths as declared but absent.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/service.py` (add `charter` to the `from scieflow.core.run import ...` line):

```python
def run_charter(project: Project, slug: str) -> dict:
    """The run's agreed plan: current text plus the whole version history."""
    ws = _ws(project, slug)
    doc = charter.read(ws)
    return {"current": doc["current"], "text": charter.current_text(ws),
            "versions": charter.history(ws)}


def set_charter(project: Project, slug: str, text: str,
                actor: str = "human", note: str = "") -> dict:
    ws = _ws(project, slug)
    try:
        return charter.set_text(ws, text, actor, note)
    except charter.CharterError as exc:
        raise ServiceError(str(exc)) from exc


def revert_charter(project: Project, slug: str, version: int,
                   actor: str = "human") -> dict:
    ws = _ws(project, slug)
    try:
        return charter.revert(ws, version, actor)
    except charter.CharterError as exc:
        raise ServiceError(str(exc)) from exc
```

In `src/scieflow/web/api.py`:

```python
@router.get("/runs/{slug}/charter", tags=["runs"])
async def run_charter(request: Request, slug: str) -> dict:
    """The run's agreed plan, with its version history."""
    return service.run_charter(_project(request), slug)


@router.post("/runs/{slug}/charter", dependencies=MUTATE, tags=["runs"])
async def set_charter(request: Request, slug: str,
                      text: str = Form(...), note: str = Form("")) -> dict:
    """Replace the charter, keeping the previous version in the history."""
    return service.set_charter(_project(request), slug, text, note=note)


@router.post("/runs/{slug}/charter/revert", dependencies=MUTATE, tags=["runs"])
async def revert_charter(request: Request, slug: str,
                         version: int = Form(...)) -> dict:
    """Make an earlier version current again, by appending a copy of it."""
    return service.revert_charter(_project(request), slug, version)
```

In `src/scieflow/web/pages.py`, one form route handling both actions:

```python
@router.post("/runs/{slug}/charter", dependencies=MUTATE)
async def edit_charter(request: Request, slug: str, action: str = Form("set"),
                       text: str = Form(""), note: str = Form(""),
                       version: int = Form(0)):
    project = _project(request)
    try:
        if action == "revert":
            service.revert_charter(project, slug, version)
        else:
            service.set_charter(project, slug, text, note=note)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)
```

and pass the charter into the run page from `run_page`:

```python
        "charter": service.run_charter(project, slug),
```

In `run.html`, add the panel directly under the run's summary line, before
`<h2>Phases</h2>` — the charter is the run's standing goal, so it belongs
above the machinery:

```html
<h2>The agreed plan</h2>
{% if not charter.text %}
  <p class="dim">No charter yet. Whatever you write here is put at the top of
     every prompt this run sends an agent, so a long conversation cannot drift
     away from it.</p>
{% else %}
  <pre class="charter">{{ charter.text }}</pre>
{% endif %}
<form method="post" action="/runs/{{ slug }}/charter">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <textarea name="text" rows="6" placeholder="What this run is for">{{ charter.text }}</textarea>
  <input name="note" placeholder="why you are changing it (optional)">
  <button name="action" value="set">Save the plan</button>
</form>
{% if charter.versions|length > 1 %}
<details>
  <summary class="dim">{{ charter.versions|length }} versions</summary>
  <table>
    {% for version in charter.versions %}
    <tr>
      <td class="dim">v{{ version.n }}</td>
      <td class="dim">{{ version.ts[11:19] }}</td>
      <td class="dim">{{ version.actor }}</td>
      <td>{{ version.note }}</td>
      <td>
        {% if version.n != charter.current %}
        <form method="post" action="/runs/{{ slug }}/charter" class="inline">
          <input type="hidden" name="csrf_token" value="{{ csrf }}">
          <input type="hidden" name="version" value="{{ version.n }}">
          <button name="action" value="revert">Restore</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</details>
{% endif %}
```

Add to `static/app.css`:

```css
.charter { white-space: pre-wrap; padding: .75rem; border-left: 3px solid var(--warn); }
textarea { width: 100%; font: inherit; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass. The inventory test matches `MUTATING_PATHS` exactly, and the guard tests now cover the three new paths automatically.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow tests/web
git commit -m "feat(web): read, edit and revert the run charter from the browser

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: CLI parity and documentation

**Files:**
- Modify: `src/scieflow/core/run/cli.py`, `docs/runs.md`, `docs/web.md`, `docs/agents.md`
- Test: `tests/core/test_run_actions.py` (append)

**Interfaces:**
- Consumes: the Task 3 service functions; the existing `_actor(as_agent)` helper in `run/cli.py`.
- Produces: `scieflow run charter <slug>` (show), `--set TEXT`, `--note TEXT`, `--revert N`.

**Why the CLI needs this at all:** AGENTS.md rule 4 — run state is never hand-edited. If the browser can set the charter and the terminal cannot, the next person in a terminal will open `charter.yml` in an editor, and the version history the feature exists to keep will be silently wrong. This is the same reason `run spend` was added in M1.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_run_actions.py`)

```python
def test_run_charter_shows_the_current_text(project_cli):
    from scieflow.core.run import charter

    runner, project = project_cli
    charter.set_text(project.run_dir("r1"), "Find a better catalyst.")
    result = runner.invoke(cli, ["run", "charter", "r1"])
    assert result.exit_code == 0
    assert "Find a better catalyst." in result.output


def test_run_charter_set_and_revert(project_cli):
    from scieflow.core.run import charter

    runner, project = project_cli
    ws = project.run_dir("r1")
    assert runner.invoke(cli, ["run", "charter", "r1", "--set", "First."]).exit_code == 0
    assert runner.invoke(cli, ["run", "charter", "r1", "--set", "Second."]).exit_code == 0
    assert charter.current_text(ws) == "Second."
    assert runner.invoke(cli, ["run", "charter", "r1", "--revert", "1"]).exit_code == 0
    assert charter.current_text(ws) == "First."


def test_run_charter_reverting_to_a_missing_version_fails_cleanly(project_cli):
    runner, project = project_cli
    result = runner.invoke(cli, ["run", "charter", "r1", "--revert", "99"])
    assert result.exit_code != 0
    assert "version" in result.output
    assert "Traceback" not in result.output
```

Reuse whatever runner/project fixture `tests/core/test_run_actions.py` already
uses for the other `run` subcommands rather than adding a second one; the
names above assume a `project_cli` yielding `(CliRunner(), Project)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_run_actions.py -k charter -v`
Expected: FAIL — `Error: No such command 'charter'`.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/run/cli.py`, following the shape of the neighbouring
subcommands:

```python
@run.command("charter")
@click.argument("slug")
@click.option("--set", "text", default="", help="Replace the charter with TEXT.")
@click.option("--note", default="", help="Why you are changing it.")
@click.option("--revert", "version", type=int, default=0,
              help="Make version N current again, by appending a copy of it.")
@click.option("--as-agent", is_flag=True, help="Record the change as the agent.")
def charter_cmd(slug: str, text: str, note: str, version: int, as_agent: bool) -> None:
    """Show or change what this run has agreed to do.

    The charter is put at the top of every prompt this run sends an agent, so
    a long conversation cannot drift away from the goal it started with.
    """
    project = Project.discover()
    actor = _actor(as_agent)
    try:
        if version:
            result = service.revert_charter(project, slug, version, actor)
            click.echo(f"restored version {version} as v{result['n']}")
            return
        if text:
            result = service.set_charter(project, slug, text, actor, note)
            click.echo(f"charter v{result['n']} written")
            return
        doc = service.run_charter(project, slug)
        if not doc["text"]:
            click.echo("no charter yet; set one with --set")
            return
        click.echo(doc["text"])
        click.echo(f"\n(version {doc['current']} of {len(doc['versions'])})")
    except service.ServiceError as exc:
        raise click.ClickException(str(exc)) from exc
```

Import `service` at the top of that module if it is not already imported, and
check how the neighbouring commands obtain their `Project` — match them
rather than the sketch above if they differ.

- [ ] **Step 4: Write the documentation**

In `docs/runs.md`, add a section on the charter covering: what it is (the
run's standing goal, versioned); that it is placed at the top of every prompt
the run sends an agent, which is the reason it exists; that a revert appends
rather than rewinds, so the history of how the goal moved is never lost; and
the CLI ↔ browser pair (`scieflow run charter <slug> --set …` ↔ the panel on
the run page), matching the format the other lifecycle commands use there.

In `docs/web.md`, add the charter panel to the run page's description and the
three routes to the API table.

In `docs/agents.md`, add one short paragraph: an agent writing prompts for a
run should expect the charter to already be at the top of what it receives,
and should not restate it.

Check every claim against the code as it shipped — in particular, say that
the charter is prepended by `agent_run.compose_prompt`, and do **not** repeat
the older, false claim that the CLI and the browser go through the same
`service` function: for run actions the CLI calls `actions.*` directly. Both
reach the same primitive, which is the true and useful statement.

- [ ] **Step 5: Verify everything**

Run each and check the exit code explicitly — a pipe would hide a failure:

```bash
uv run pytest -q; echo "pytest: $?"
./scripts/check_legacy.sh; echo "legacy: $?"
uv run --group docs mkdocs build --strict; echo "mkdocs: $?"
```

Expected: `pytest: 0` with no failures, `legacy: 0` with every check `ok`,
`mkdocs: 0` with no warnings.

- [ ] **Step 6: Commit**

```bash
git add src/scieflow/core/run/cli.py docs tests/core/test_run_actions.py
git commit -m "feat(cli): scieflow run charter; document the run charter

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: A coordinator may propose a charter, and adopting it is a gate

**Files:**
- Modify: `schemas/gates.yml`, `src/scieflow/core/service.py`, `docs/runs.md`, `docs/agents.md`
- Test: `tests/core/test_charter.py` (append), `tests/web/test_charter_page.py` (append)

**Interfaces:**
- Consumes: `gates.open_gate(project, ws, kind, question, options=(), files=(), in_scope=False, actor="agent")`, `service.answer_gate`, `charter.set_text`.
- Produces: gate kind `charter-adoption` in `schemas/gates.yml`; `service.answer_gate` adopts the proposal when such a gate is answered affirmatively.

**The design, and why.** `open_gate` has a fixed field set with nowhere to put a payload, so a proposed charter travels the way every other document a gate refers to already travels: the agent writes it to a file inside its own run and names that file in the gate's `files`. On adoption, ScieFlow reads it and writes it as a new charter version, with the answering human as the actor.

`requires_human: true`, because adopting a charter redefines what the run is *for*. The neighbouring `scope-change` gate is marked the same way for the same reason, and an autonomous coordinator that could rewrite its own goal is not autonomous within a scope — it is unbounded.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_charter.py`)

```python
def test_a_proposal_becomes_the_charter_when_the_gate_is_answered(project_ws):
    """The coordinator proposes; a human adopts. The decision is recorded
    with an actor and a timestamp, like every other approval."""
    from scieflow.core import gates, service
    from scieflow.core.run import charter

    project, ws = project_ws
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Goal: characterise the catalyst before scaling.")

    gate = gates.open_gate(project, ws, "charter-adoption",
                           "Adopt this plan as the run's charter?",
                           options=["adopt", "decline"], files=[proposal])
    assert gate["requires_human"] is True

    service.answer_gate(project, "r1", gate["id"], "adopt")

    assert charter.current_text(ws) == "Goal: characterise the catalyst before scaling."
    assert charter.history(ws)[0]["actor"] == "human"
    assert "adopted from a proposal" in charter.history(ws)[0]["note"]


def test_declining_a_proposal_changes_nothing(project_ws):
    from scieflow.core import gates, service
    from scieflow.core.run import charter

    project, ws = project_ws
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("A plan nobody wants.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[proposal])

    service.answer_gate(project, "r1", gate["id"], "decline")

    assert charter.current_text(ws) == ""


def test_an_agent_cannot_adopt_its_own_proposal(project_ws):
    """requires_human is the point: a coordinator that could rewrite its own
    goal is not autonomous within a scope, it is unbounded."""
    from scieflow.core import gates, service
    from scieflow.core.run import charter

    project, ws = project_ws
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Let me do whatever I like.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[proposal])

    with pytest.raises(service.ServiceError):
        service.answer_gate(project, "r1", gate["id"], "adopt",
                            actor="agent", rationale="I wrote it myself")
    assert charter.current_text(ws) == ""


def test_adopting_a_proposal_whose_file_is_gone_is_refused(project_ws):
    """The agent's run is writable by the agent, so the file it named can be
    deleted between proposing and adopting."""
    from scieflow.core import gates, service
    from scieflow.core.run import charter

    project, ws = project_ws
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Here now, gone later.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[proposal])
    proposal.unlink()

    with pytest.raises(service.ServiceError, match="proposal"):
        service.answer_gate(project, "r1", gate["id"], "adopt")
    assert charter.current_text(ws) == ""
```

Add a `project_ws` fixture to that file yielding `(Project, ws)` for run
`r1` — the existing `ws` fixture builds the workspace; this one additionally
needs the `schemas/` directory copied in, because `gates.kinds(project)`
reads `schemas/gates.yml`. `tests/web/conftest.py` shows the pattern for
copying those schema files into a `tmp_path` project.

Append to `tests/web/test_charter_page.py` a test that the browser path works
end to end, since answering gates from the page already exists:

```python
def test_adopting_a_proposal_from_the_gate_form(client, project):
    from scieflow.core import gates

    ws = project.run_dir("r1")
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Adopted from the browser.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[proposal])

    assert post(client, f"/runs/r1/gates/{gate['id']}",
                answer="adopt").status_code == 303
    assert charter.current_text(ws) == "Adopted from the browser."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_charter.py -k proposal -v`
Expected: FAIL — `GateError: unknown gate kind 'charter-adoption'`.

- [ ] **Step 3: Write the implementation**

In `schemas/gates.yml`, add the kind beside the other `requires_human: true`
entries:

```yaml
  charter-adoption:    {requires_human: true,  help: "Adopt a proposed plan as this run's charter"}
```

In `src/scieflow/core/service.py`, extend `answer_gate` so an adopted
proposal becomes a charter version. The charter write happens *after* the
gate is answered, so the recorded decision and the charter cannot disagree:

```python
ADOPTED = frozenset({"adopt", "yes", "approve", "approved"})


def _adopt_charter(ws: Path, gate: dict, actor: str) -> None:
    """A `charter-adoption` gate carries its proposal as the file it names.

    `open_gate` has no payload field, so the proposal travels the way every
    other document a gate refers to travels — as a file inside the run.
    """
    files = gate.get("files") or []
    if not files:
        raise ServiceError("that proposal names no file to adopt")
    path = Path(files[0])
    try:
        text = path.read_text()
    except OSError as exc:
        raise ServiceError(f"cannot read the proposal at {path}: {exc}") from exc
    try:
        charter.set_text(ws, text, actor,
                         f"adopted from a proposal (gate {gate['id']})")
    except charter.CharterError as exc:
        raise ServiceError(str(exc)) from exc
```

and in `answer_gate`, after the existing `gates.answer(...)` call:

```python
def answer_gate(project: Project, slug: str, gate_id: str, answer: str,
                actor: str = "human", rationale: str = "", note: str = "") -> dict:
    ws = _ws(project, slug)
    try:
        gate = gates.answer(project, ws, gate_id, answer, actor, rationale, note)
    except gates.GateError as e:
        raise ServiceError(str(e)) from e
    if gate["kind"] == "charter-adoption" and answer.strip().lower() in ADOPTED:
        _adopt_charter(ws, gate, actor)
    return gate
```

The `requires_human: true` flag already stops an agent answering its own
proposal — `gates._check_agent_may_answer` raises for any gate marked that
way, and `answer_gate` turns that into a `ServiceError`. Do not add a second
check; verify the existing one covers it and say so in your report.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_charter.py tests/web/test_charter_page.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Document it**

In `docs/runs.md`'s charter section, add the proposal flow: a coordinator
writes a proposed plan to a file in its run and opens a `charter-adoption`
gate naming it; the gate needs a human; adopting writes it as a new charter
version attributed to whoever answered. In `docs/agents.md`, tell an agent
how to propose one — write the file, open the gate, and do not expect to
answer it.

- [ ] **Step 6: Commit**

```bash
git add schemas/gates.yml src/scieflow/core/service.py docs tests
git commit -m "feat(core): propose a charter through a gate, adopt it with a human

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
