# Web control A1c — the coordinator conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let someone talk to a run's coordinator from the browser — each message a sandboxed job that resumes the agent's own session, so the conversation keeps its context without ScieFlow holding a long-lived process.

**Architecture:** A run's conversation is a sequence of jobs and nothing else. The first message launches the agent in a mode that reports a session id; ScieFlow records that id on the run and every later message dispatches a *resumed* session. Everything the earlier milestones built keeps its meaning — each turn is sandboxed, counts against the budget, appears on the timeline as `job.started`/`job.finished`, and can be cancelled.

**Tech Stack:** Python, the existing `jobs`/`agent_run`/`service` layers, FastAPI + Jinja, the SSE transport already built for live job logs. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-24-web-control-a1-design.md` — the "Execution model" and "Pages" sections.

**Scope.** This plan is the conversation. The **Start wizard** — a form over the workflow registry that creates a run and launches its coordinator — is **A1d**, the plan that follows. A1c makes existing runs conversational; A1d makes new ones.

**Branch.** Stack this on `feat/a1b-run-charter` (PR #2), not on `main` — this plan composes prompts through `agent_run.compose_prompt`, which A1b added, and renders beside the charter panel.

## Global Constraints

- **Every mutation goes through `scieflow.core.service`.** No route may reach into `run.conversation`, `agent_run` or `jobs` directly.
- **Every non-GET route is session-guarded and CSRF-protected.** `tests/web/mutating_paths.py::MUTATING_PATHS` is extended, never shrunk, and each new path needs its `SAMPLES` entry or the generated guard tests fail.
- **A turn is an ordinary sandboxed dispatch.** It goes through `service.dispatch_agent`, so it inherits the budget guard, the sandbox confinement proof, the job record and the timeline. This plan adds no second execution path.
- **The charter is pinned to every turn.** `agent_run.compose_prompt` already does this; a turn's prompt must flow through it, not around it.
- **Agent output is data, never instructions to ScieFlow.** A transcript is text an agent produced; nothing here interprets it as a command.
- **An agent that cannot report a session id cannot host a conversation**, and the app must say so plainly rather than silently starting a fresh context each turn.
- **Real-CLI tests are marked `@pytest.mark.live`.** `pyproject.toml:72` sets `addopts = "-m 'not slow and not live'"`, so they are deselected by default and never run in a normal suite.
- **Nothing regresses.** `uv run pytest -q` stays green (1132 passing at the start of this plan), `./scripts/check_legacy.sh` stays 25/25 `ok`, `uv run --group docs mkdocs build --strict` stays at zero warnings.

## Review Focus

Five conditions the spec implies that no obvious test would cover. Each has a test in the task that owns the code.

1. **An agent CLI changes its session-id field or its resume syntax between releases.** This is the single most likely thing to rot, and its failure mode is silent: every turn starts a fresh context and the conversation quietly stops remembering. *(Task 1)*
2. **The session id is missing from a turn's output** — the agent crashed, was cancelled, or printed nothing parseable. The next turn must not silently start a new conversation under the old id. *(Task 2)*
3. **A second message sent while a turn is still running.** Two concurrent resumes of one session is not a thing the CLIs promise to handle, and two jobs writing one conversation record is a lost update. *(Task 3)*
4. **A turn that is cancelled or times out mid-stream**, leaving a partial JSON stream. Parsing must degrade to "no session id, here is what we got" rather than raising. *(Task 1 and Task 3)*
5. **Switching the agent mid-conversation.** The new agent has no session, so its first turn must start a fresh one rather than resuming an id that belongs to a different CLI. *(Task 4)*

## File structure

| File | Responsibility |
|---|---|
| `src/scieflow/core/sessions.py` | per-agent-family parsing: the session id and the readable text out of a turn's output |
| `config/agents.yml` | `session_cmd` / `resume_cmd` per agent that can host a conversation |
| `src/scieflow/core/agent_run.py` | `{session}` substitution and a session-mode dispatch |
| `src/scieflow/core/run/conversation.py` | the record: which agent, which session, the turns |
| `src/scieflow/core/service.py` | `say`, `conversation`, `switch_agent` |
| `src/scieflow/web/{api,pages}.py` | the chat routes |
| `src/scieflow/web/templates/run.html` | the chat panel, tailing the live turn |

---

### Task 1: Session-capable commands and the output parser

**Files:**
- Create: `src/scieflow/core/sessions.py`
- Modify: `config/agents.yml`, `src/scieflow/core/agent_run.py`
- Test: `tests/core/test_sessions.py` (create)

**Interfaces:**
- Consumes: `agent_run.build_argv`.
- Produces:
  `sessions.FAMILIES` — mapping an agent name to a parser;
  `sessions.parse(agent_cfg, output) -> Session` where `Session` is a frozen dataclass `(id: str | None, text: str)`;
  `sessions.can_converse(agent_cfg) -> bool` — true when the config declares both `session_cmd` and `resume_cmd`;
  `agent_run.build_argv` additionally substituting `{session}`.

**These are the verified facts. Do not re-derive them, and do not trust the spec over them — the spec is wrong about codex.**

| Agent | Start a session | Session id appears as | Resume |
|---|---|---|---|
| `claude` | `claude -p --output-format=stream-json --verbose …` | `"session_id"` on **every** event, including the first `system` one | `claude -p --resume <id> <prompt>` |
| `codex` | `codex exec --json …` | `{"type":"thread.started","thread_id":"…"}` — the **first** line | `codex exec resume --json <id> <prompt>` |

Three details that will cost an afternoon if missed:
- **`codex exec resume` rejects `--sandbox`.** ScieFlow's current `codex` command template passes `--sandbox workspace-write`, so `resume_cmd` cannot simply reuse `cmd`. Flags must come **before** the session id: `codex exec resume --json {session} {prompt}`.
- **`claude` needs `--verbose` with `--output-format=stream-json`**, and its stream is enormous — session-start hooks echo entire skill documents. Parse line by line and stop at the first id; never read the whole stream into one string first.
- **`codex exec` reads stdin by default.** A dispatch that does not intend to send stdin must close it, or the process waits.

**`agy` is installed but its session support is unverified.** Determine it empirically — if it cannot report a session id, leave it without `session_cmd`/`resume_cmd`; `can_converse` then returns false and the app excludes it, which is the behaviour the spec requires. Say what you found in your report either way.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_sessions.py
"""Reading a session id, and readable text, out of an agent's output.

The offline tests here use captured fixture lines. The `live` tests run the
real CLIs and are deselected by default (`pyproject.toml` sets
`addopts = "-m 'not slow and not live'"`), because they cost money and network
— but they are the only thing that catches a CLI changing its output between
releases, which is this feature's most likely silent failure.
"""

import json
import shutil

import pytest

from scieflow.core import sessions

CLAUDE_STREAM = "\n".join([
    json.dumps({"type": "system", "subtype": "init",
                "session_id": "11ea03ad-e731-4917-8e89-e3f3b4a58776"}),
    json.dumps({"type": "assistant", "session_id": "11ea03ad-e731-4917-8e89-e3f3b4a58776",
                "message": {"content": [{"type": "text", "text": "Hello."}]}}),
    json.dumps({"type": "result", "subtype": "success",
                "session_id": "11ea03ad-e731-4917-8e89-e3f3b4a58776",
                "result": "Hello."}),
])

CODEX_STREAM = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "01a0d570-a280-7f22-b14f-04df145c95fc"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({"type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "Hello."}}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
])


def test_claude_session_id_and_text():
    parsed = sessions.parse({"family": "claude"}, CLAUDE_STREAM)
    assert parsed.id == "11ea03ad-e731-4917-8e89-e3f3b4a58776"
    assert "Hello." in parsed.text


def test_codex_session_id_and_text():
    parsed = sessions.parse({"family": "codex"}, CODEX_STREAM)
    assert parsed.id == "01a0d570-a280-7f22-b14f-04df145c95fc"
    assert "Hello." in parsed.text


def test_the_readable_text_is_not_the_raw_stream():
    """A human opens the job page to read what the agent said. Handing them
    the JSON stream would be worse than the plain output they had before."""
    parsed = sessions.parse({"family": "claude"}, CLAUDE_STREAM)
    assert "session_id" not in parsed.text
    assert '"type"' not in parsed.text


def test_a_truncated_stream_degrades_instead_of_raising():
    """A cancelled or timed-out turn leaves a partial line."""
    truncated = CODEX_STREAM[:len(CODEX_STREAM) // 2]
    parsed = sessions.parse({"family": "codex"}, truncated)
    assert parsed.id == "01a0d570-a280-7f22-b14f-04df145c95fc"


def test_output_with_no_session_id_parses_as_none():
    parsed = sessions.parse({"family": "claude"}, "not json at all\nnor this\n")
    assert parsed.id is None
    assert parsed.text.strip()


def test_noise_before_the_json_is_tolerated():
    """codex writes a models-cache warning to the same stream on this host."""
    noisy = "2026-09-24T22:02:03Z ERROR codex_models_manager: cache miss\n" + CODEX_STREAM
    assert sessions.parse({"family": "codex"}, noisy).id is not None


def test_can_converse_requires_both_commands():
    assert sessions.can_converse({"session_cmd": "x {prompt}", "resume_cmd": "y {session}"})
    assert not sessions.can_converse({"cmd": "x {prompt}"})
    assert not sessions.can_converse({"session_cmd": "x {prompt}"})


@pytest.mark.live
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_really_reports_and_resumes_a_session():
    """Pins the real CLI. A field rename in a future release fails here
    instead of silently restarting the conversation on every turn."""
    import subprocess

    start = subprocess.run(
        ["claude", "-p", "--output-format=stream-json", "--verbose",
         "--model", "claude-haiku-4-5", "Reply with exactly: marker-alpha"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    parsed = sessions.parse({"family": "claude"}, start.stdout)
    assert parsed.id, f"no session id in claude output: {start.stdout[:400]}"

    resumed = subprocess.run(
        ["claude", "-p", "--resume", parsed.id, "--model", "claude-haiku-4-5",
         "What word did I ask you to reply with? Answer with just that word."],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    assert "marker-alpha" in resumed.stdout, "the resumed session lost its context"


@pytest.mark.live
@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed")
def test_codex_really_reports_and_resumes_a_thread():
    import subprocess

    start = subprocess.run(
        ["codex", "exec", "--json", "--sandbox", "read-only",
         "Reply with exactly: marker-beta"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    parsed = sessions.parse({"family": "codex"}, start.stdout)
    assert parsed.id, f"no thread id in codex output: {start.stdout[:400]}"

    # Flags come BEFORE the id, and `resume` rejects --sandbox.
    resumed = subprocess.run(
        ["codex", "exec", "resume", "--json", parsed.id,
         "What word did I ask you to reply with? Answer with just that word."],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    assert "marker-beta" in resumed.stdout, "the resumed thread lost its context"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_sessions.py -v`
Expected: FAIL — `No module named 'scieflow.core.sessions'`. The two `live` tests are deselected; confirm that by checking the summary says `deselected`.

- [ ] **Step 3: Write the implementation**

Create `src/scieflow/core/sessions.py`:

```python
"""Reading a session id, and readable text, out of an agent's output.

ScieFlow holds no long-lived agent process. A conversation is a sequence of
jobs, and what makes it a conversation rather than a series of strangers is
the agent's own session id: captured from the first turn, handed back on
every later one.

Each CLI reports it differently and neither documents it as a contract, so
the shapes below are pinned by tests that run the real binaries. The failure
this guards against is silent — a renamed field means every turn starts a
fresh context and the conversation stops remembering, with nothing in the
logs to say so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    id: str | None
    text: str


def _events(output: str):
    """Every parseable JSON object in the stream, in order.

    Line by line, tolerating both noise the CLI writes around its JSON (codex
    emits a models-cache warning on some hosts) and a final truncated line
    from a cancelled or timed-out turn.
    """
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _claude(output: str) -> Session:
    session_id, said = None, []
    for event in _events(output):
        if session_id is None and event.get("session_id"):
            session_id = str(event["session_id"])
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    said.append(block.get("text", ""))
        elif event.get("type") == "result" and event.get("result"):
            said.append(str(event["result"]))
    return Session(session_id, _readable(said, output))


def _codex(output: str) -> Session:
    session_id, said = None, []
    for event in _events(output):
        if session_id is None and event.get("type") == "thread.started":
            session_id = str(event.get("thread_id") or "") or None
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                said.append(str(item["text"]))
    return Session(session_id, _readable(said, output))


def _readable(said: list[str], output: str) -> str:
    """What a person sees on the job page.

    Falls back to the raw output when nothing parsed, because an agent that
    failed before emitting any JSON has usually written the useful part —
    an error — in plain text.
    """
    joined = "\n\n".join(part for part in said if part.strip())
    return joined if joined.strip() else output


FAMILIES = {"claude": _claude, "codex": _codex}


def family_of(agent_cfg: dict) -> str:
    """Which output dialect this agent speaks.

    Declared per agent rather than guessed from its name, so a second agent
    wrapping the same CLI parses correctly.
    """
    return str(agent_cfg.get("family", ""))


def parse(agent_cfg: dict, output: str) -> Session:
    parser = FAMILIES.get(family_of(agent_cfg))
    if parser is None:
        return Session(None, output)
    return parser(output)


def can_converse(agent_cfg: dict) -> bool:
    """An agent can host a conversation only if it can both start a session
    and resume one. Anything less would silently restart the context."""
    return bool(agent_cfg.get("session_cmd")) and bool(agent_cfg.get("resume_cmd"))
```

In `src/scieflow/core/agent_run.py`, add `{session}` to `build_argv`'s substitutions, beside the existing ones and **before** `{prompt}` is inserted last:

```python
def build_argv(agent_cfg: dict, prompt: str, root: Path,
               include_prompt: bool = True, template: str | None = None,
               session: str = "") -> list[str]:
    ...
        token = (
            token.replace("{model}", model)
            .replace("{reasoning}", reasoning)
            .replace("{root}", str(root))
            .replace("{python}", sys.executable)
            .replace("{session}", session)
        )
        argv.append(token.replace("{prompt}", prompt))
```

In `config/agents.yml`, give `claude` and `codex` the two commands. Note `codex`'s resume does **not** carry `--sandbox`:

```yaml
  claude:
    family: claude
    session_cmd: "claude -p --output-format=stream-json --verbose --dangerously-skip-permissions --model {model} {prompt}"
    resume_cmd: "claude -p --output-format=stream-json --verbose --dangerously-skip-permissions --model {model} --resume {session} {prompt}"
```

```yaml
  codex:
    family: codex
    session_cmd: "codex exec --json --sandbox workspace-write --model {model} -c model_reasoning_effort={reasoning} {prompt}"
    # `codex exec resume` rejects --sandbox, and its flags must precede the id.
    resume_cmd: "codex exec resume --json {session} {prompt}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_sessions.py -v && uv run pytest -q`
Expected: the offline tests pass, the two `live` tests are deselected, full suite green.

Then run the live tests **once**, deliberately, to confirm the real CLIs still behave as pinned:
`uv run pytest tests/core/test_sessions.py -m live -v`
Record the result in your report. If one fails, that is a finding about the CLI, not about your code — report it rather than weakening the test.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/sessions.py src/scieflow/core/agent_run.py config/agents.yml tests/core/test_sessions.py
git commit -m "feat(core): capture and resume an agent's own session

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The conversation record

**Files:**
- Create: `src/scieflow/core/run/conversation.py`
- Modify: `src/scieflow/core/events.py`
- Test: `tests/core/test_conversation.py` (create)

**Interfaces:**
- Consumes: `scieflow.core.store` (`read_yaml`, `update_yaml`), `scieflow.core.events.emit`.
- Produces:
  `conversation.CONVERSATION_FILE = "conversation.yml"`;
  `conversation.read(ws) -> dict` — `{"agent": str, "session": str | None, "turns": [...]}`, empty shape when absent;
  `conversation.set_agent(ws, agent, actor="human") -> dict` — clears the session, because a session belongs to one CLI;
  `conversation.add_turn(ws, *, role, text, job_id="", actor="human") -> dict`;
  `conversation.record_session(ws, session_id) -> dict`;
  `conversation.ConversationError`.

Events `turn.sent` and `turn.received` join the closed vocabulary in `events.py`.

**Why `set_agent` clears the session:** a session id is meaningful only to the CLI that issued it. Handing `claude`'s id to `codex --resume` is not a recoverable error, it is a different conversation — so switching agents starts a fresh one, and the record must not pretend otherwise.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_conversation.py
"""The conversation record: which agent, whose session, what was said."""

import pytest

from scieflow.core import events
from scieflow.core.run import conversation


@pytest.fixture
def ws(tmp_path):
    from scieflow.core.run import status

    workspace = tmp_path / "workspace" / "r1"
    workspace.mkdir(parents=True)
    status.write_status(workspace, status.new_status("r1", "autonomous"))
    return workspace


def test_a_run_without_a_conversation_reads_as_empty(ws):
    assert conversation.read(ws) == {"agent": "", "session": None, "turns": []}


def test_choosing_an_agent_then_recording_a_session(ws):
    conversation.set_agent(ws, "claude")
    conversation.record_session(ws, "abc-123")
    doc = conversation.read(ws)
    assert doc["agent"] == "claude" and doc["session"] == "abc-123"


def test_turns_accumulate_in_order(ws):
    conversation.set_agent(ws, "claude")
    conversation.add_turn(ws, role="human", text="What next?")
    conversation.add_turn(ws, role="agent", text="Run the sweep.", job_id="J1")
    turns = conversation.read(ws)["turns"]
    assert [t["role"] for t in turns] == ["human", "agent"]
    assert turns[1]["job_id"] == "J1"
    assert turns[0]["ts"]
    kinds = [e["type"] for e in events.read(ws)]
    assert kinds.count("turn.sent") == 1 and kinds.count("turn.received") == 1


def test_switching_the_agent_clears_the_session(ws):
    """A session id means nothing to a different CLI. Switching starts a new
    conversation, and the record must not pretend otherwise."""
    conversation.set_agent(ws, "claude")
    conversation.record_session(ws, "claude-session")
    conversation.set_agent(ws, "codex")
    doc = conversation.read(ws)
    assert doc["agent"] == "codex"
    assert doc["session"] is None
    assert len(doc["turns"]) == 0 or doc["turns"], "turns are history, not session state"


def test_switching_to_the_same_agent_keeps_the_session(ws):
    conversation.set_agent(ws, "claude")
    conversation.record_session(ws, "keep-me")
    conversation.set_agent(ws, "claude")
    assert conversation.read(ws)["session"] == "keep-me"


def test_recording_no_session_id_leaves_the_old_one_alone(ws):
    """A turn whose output had no id — the agent crashed, or was cancelled.
    Silently clearing would restart the conversation; silently keeping a
    stale id is what we want, because the session on the CLI's side is
    still there."""
    conversation.set_agent(ws, "claude")
    conversation.record_session(ws, "first")
    conversation.record_session(ws, None)
    assert conversation.read(ws)["session"] == "first"


def test_a_turn_needs_a_role_we_recognise(ws):
    with pytest.raises(conversation.ConversationError):
        conversation.add_turn(ws, role="wizard", text="hi")


def test_concurrent_turns_do_not_lose_one(ws):
    import threading

    conversation.set_agent(ws, "claude")
    threads = [threading.Thread(target=conversation.add_turn, kwargs={
        "role": "human", "text": f"m{i}"}) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(conversation.read(ws)["turns"]) == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_conversation.py -v`
Expected: FAIL — `No module named 'scieflow.core.run.conversation'`.

- [ ] **Step 3: Write the implementation**

Create `src/scieflow/core/run/conversation.py`:

```python
"""A run's conversation: which agent is talking, whose session, what was said.

There is no long-lived process here. Each turn is a job, and this file is
what makes a series of jobs into a conversation — it holds the agent's own
session id, which the next turn hands back to the CLI.

Turns are history and are never rewritten. The session is state and can be
cleared, which is exactly what switching agents does: an id issued by one
CLI means nothing to another.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import events, store

CONVERSATION_FILE = "conversation.yml"
ROLES = frozenset({"human", "agent"})


class ConversationError(ValueError):
    """A conversation change that cannot be made; nothing was written."""


def _path(ws: Path) -> Path:
    return Path(ws) / CONVERSATION_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(ws: Path) -> dict:
    doc = store.read_yaml(_path(ws), default=None) or {}
    if not isinstance(doc, dict):
        raise ConversationError(f"malformed {CONVERSATION_FILE}: not a mapping")
    return {"agent": str(doc.get("agent") or ""),
            "session": doc.get("session") or None,
            "turns": list(doc.get("turns") or [])}


def set_agent(ws: Path, agent: str, actor: str = "human") -> dict:
    """Choose who is talking. Switching clears the session, because a session
    id is meaningful only to the CLI that issued it."""
    if not agent or not agent.strip():
        raise ConversationError("name an agent")

    def change(doc: dict) -> dict:
        current = dict(doc or {})
        if str(current.get("agent") or "") != agent:
            current["session"] = None
        current["agent"] = agent
        current.setdefault("turns", [])
        return current

    store.update_yaml(_path(ws), change)
    return read(ws)


def record_session(ws: Path, session_id: str | None) -> dict:
    """Remember the id this turn reported.

    `None` leaves the previous id alone: a turn can fail or be cancelled
    before printing one, and the session on the CLI's side is still there.
    """
    if not session_id:
        return read(ws)
    store.update_yaml(_path(ws), lambda doc: {**(doc or {}), "session": str(session_id)})
    return read(ws)


def add_turn(ws: Path, *, role: str, text: str, job_id: str = "",
             actor: str = "human") -> dict:
    if role not in ROLES:
        raise ConversationError(f"unknown turn role {role!r} (one of {', '.join(sorted(ROLES))})")
    turn = {"role": role, "text": text, "job_id": job_id, "ts": _now()}

    def append(doc: dict) -> dict:
        current = dict(doc or {})
        current["turns"] = [*(current.get("turns") or []), dict(turn)]
        return current

    store.update_yaml(_path(ws), append)
    events.emit(ws, "turn.sent" if role == "human" else "turn.received",
                actor, role=role, job=job_id)
    return turn
```

In `src/scieflow/core/events.py`, extend the closed vocabulary:

```python
    "charter.set", "charter.reverted",
    "turn.sent", "turn.received",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_conversation.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/run/conversation.py src/scieflow/core/events.py tests/core/test_conversation.py
git commit -m "feat(core): the conversation record — agent, session and turns

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Dispatching a turn

**Files:**
- Modify: `src/scieflow/core/agent_run.py`, `src/scieflow/core/service.py`
- Test: `tests/core/test_service.py` (append)

**Interfaces:**
- Consumes: `sessions.parse`, `sessions.can_converse`, `conversation.*`, `agent_run.prepare`, `agent_run.compose_prompt`, `service.dispatch_agent`, `service._ws`, `service.ServiceError`.
- Produces:
  `agent_run.prepare(..., session: str | None = None, conversational: bool = False)` — selects `session_cmd` when `conversational` and no session, `resume_cmd` when a session exists;
  `service.say(project, slug, message, actor="human") -> dict` — appends the human turn, dispatches, records the session and the agent turn, returns `{"job": ..., "turn": ...}`;
  `service.conversation_state(project, slug) -> dict` — the record plus `busy: bool` and
  `can_converse: bool`. Named `conversation_state`, not `conversation`, because `service.py`
  imports the `conversation` module and a same-named function would shadow it.

**The turn is an ordinary dispatch.** `service.dispatch_agent` already guards the budget, proves the sandbox, starts the job, records spend and writes the transcript. `say` composes the prompt and calls it — it does not reimplement any of that, and it must not bypass `compose_prompt`, which is what pins the charter to every turn.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_service.py`)

```python
def test_say_dispatches_a_turn_and_records_both_sides(project):
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    result = service.say(project, "r1", "What should we try next?")

    turns = conversation.read(ws)["turns"]
    assert [t["role"] for t in turns] == ["human", "agent"]
    assert turns[0]["text"] == "What should we try next?"
    assert turns[1]["job_id"] == result["job"]["id"]


def test_say_pins_the_charter_into_the_turn(project):
    """The whole point of the charter is that every turn carries it. A turn
    that composed its own prompt would bypass that silently."""
    from scieflow.core.run import charter, conversation

    ws = project.run_dir("r1")
    charter.set_text(ws, "Goal: characterise the catalyst.")
    conversation.set_agent(ws, "stub")
    service.say(project, "r1", "next step?")

    sent = (ws / "logs").glob("turn-*.md")
    composed = "\n".join(p.read_text() for p in sent)
    assert "characterise the catalyst" in composed
    assert composed.index("characterise the catalyst") < composed.index("next step?")


def test_say_refuses_when_no_agent_is_chosen(project):
    with pytest.raises(service.ServiceError, match="agent"):
        service.say(project, "r1", "hello")


def test_say_refuses_an_agent_that_cannot_hold_a_session(project):
    """The spec is explicit: an agent that cannot report a session id must be
    refused plainly, not silently restarted on every turn."""
    from scieflow.core.run import conversation

    conversation.set_agent(project.run_dir("r1"), "sleepy")   # no session_cmd
    with pytest.raises(service.ServiceError, match="conversation"):
        service.say(project, "r1", "hello")


def test_say_refuses_while_a_turn_is_still_running(project, running_turn):
    """Two concurrent resumes of one session is not something either CLI
    promises to handle, and two jobs appending one record is a lost update."""
    with pytest.raises(service.ServiceError, match="still"):
        service.say(project, "r1", "and another thing")


def test_say_refuses_an_empty_message(project):
    from scieflow.core.run import conversation

    conversation.set_agent(project.run_dir("r1"), "stub")
    with pytest.raises(service.ServiceError):
        service.say(project, "r1", "   ")


def test_conversation_reports_whether_it_can_converse(project):
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "sleepy")
    assert service.conversation_state(project, "r1")["can_converse"] is False
    conversation.set_agent(ws, "stub")
    assert service.conversation_state(project, "r1")["can_converse"] is True
```

The `project` fixture in `tests/core/test_service.py` registers `stub` and `sleepy`. Give `stub` a `family`, a `session_cmd` and a `resume_cmd` in that fixture so it can host a conversation — pointing at `scieflow.core.stub_agent` as its `cmd` already does — and leave `sleepy` without them so the refusal test has a subject. `running_turn` is a new fixture: start a turn whose job is still running (the `sleepy` agent's `sleep 300` command is the existing pattern) and cancel it on teardown.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_service.py -k say -v`
Expected: FAIL — `module 'scieflow.core.service' has no attribute 'say'`.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/agent_run.py`, teach `prepare` about session mode. Add the two parameters and select the template:

```python
def prepare(project: Project, agent: str, prompt_file: Path,
            cwd: Path | None = None, role: str | None = None, *,
            sandbox_enabled: bool = True,
            session: str | None = None, conversational: bool = False) -> Dispatch:
```

and where the argv is built, before the existing stdin branch:

```python
    template = None
    if conversational:
        key = "resume_cmd" if session else "session_cmd"
        template = agent_cfg.get(key)
        if not template:
            raise DispatchError(
                f"{agent} cannot host a conversation: no {key} in its configuration")
```

then pass `template` and `session` through to `build_argv` in each branch, leaving the `stdin_cmd` fallback untouched for the non-conversational path.

In `src/scieflow/core/service.py` (import `sessions` and `conversation`):

```python
TURN_PROMPT_DIR = "logs"


def conversation_state(project: Project, slug: str) -> dict:
    """The record, plus whether a turn is in flight and whether this agent
    can hold a session at all."""
    ws = _ws(project, slug)
    doc = conversation.read(ws)
    cfg = config.load_agents(project.root).get(doc["agent"], {})
    return {**doc,
            "busy": _turn_in_flight(project, ws),
            "can_converse": bool(doc["agent"]) and sessions.can_converse(cfg)}


def _turn_in_flight(project: Project, ws: Path) -> bool:
    return any(job.state == "running" and job.kind == "agent"
               for job in jobs.list_jobs(project, ws))


def say(project: Project, slug: str, message: str, actor: str = "human") -> dict:
    """One conversation turn: a sandboxed job that resumes the agent's session."""
    if not message or not message.strip():
        raise ServiceError("say something")
    ws = _ws(project, slug)
    doc = conversation.read(ws)
    if not doc["agent"]:
        raise ServiceError("choose an agent for this run's conversation first")
    if _turn_in_flight(project, ws):
        raise ServiceError("a turn is still running; wait for it or cancel it")

    prompt_file = Path(ws) / TURN_PROMPT_DIR / f"turn-{store.new_id()}.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(agent_run.compose_prompt(ws, message))

    conversation.add_turn(ws, role="human", text=message, actor=actor)
    transcript = prompt_file.with_suffix(".out.md")
    try:
        job = dispatch_agent(project, doc["agent"], prompt_file, transcript,
                             session=doc["session"], conversational=True)
    except ServiceError:
        raise

    parsed = sessions.parse(config.load_agents(project.root)[doc["agent"]],
                            Path(transcript).read_text())
    conversation.record_session(ws, parsed.id)
    turn = conversation.add_turn(ws, role="agent", text=parsed.text,
                                 job_id=job["id"], actor="agent")
    Path(transcript).write_text(parsed.text)      # the job page shows prose, not JSON
    return {"job": job, "turn": turn}
```

`dispatch_agent` gains `session` and `conversational` as pass-through parameters and hands them to
`prepare`. Note the human turn is recorded **before** the dispatch, so a turn that crashes still shows
what was asked.

**Imports this task needs in `service.py`.** It currently imports `agent_config, events, gates, jobs,
sandbox, workspace` from `scieflow.core` and pulls `agent_run` in lazily inside `dispatch_agent`. You
will additionally need `config` (for `load_agents`), `store` (for `new_id`), `sessions`, and
`conversation` from `scieflow.core.run` — and `compose_prompt`, which is in `agent_run`. Check for an
import cycle before adding `agent_run` at module level; if there is one, follow the lazy-import
pattern `dispatch_agent` already uses rather than restructuring.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_service.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core tests/core/test_service.py
git commit -m "feat(core): one conversation turn is one sandboxed, resumed job

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Switching the agent mid-conversation

**Files:**
- Modify: `src/scieflow/core/service.py`
- Test: `tests/core/test_service.py` (append)

**Interfaces:**
- Consumes: `conversation.set_agent`, `sessions.can_converse`, `service._turn_in_flight`.
- Produces: `service.set_conversation_agent(project, slug, agent, actor="human") -> dict`.

The spec calls for switching the agent at any time after a job finishes. The mechanism is already there — the next turn simply dispatches to a different agent — so this task is the guard rails around it: refuse an unknown agent, refuse one that cannot hold a session, refuse mid-turn, and make the session reset visible rather than surprising.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_service.py`)

```python
def test_switching_the_agent_starts_a_fresh_session(project):
    """The new agent has no session of its own, so its first turn must start
    one rather than resuming an id that belongs to a different CLI."""
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.record_session(ws, "stub-session")
    service.set_conversation_agent(project, "r1", "stub2")
    doc = conversation.read(ws)
    assert doc["agent"] == "stub2" and doc["session"] is None


def test_switching_keeps_the_turns_already_said(project):
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.add_turn(ws, role="human", text="earlier question")
    service.set_conversation_agent(project, "r1", "stub2")
    assert conversation.read(ws)["turns"][0]["text"] == "earlier question"


def test_switching_to_an_unknown_agent_is_refused(project):
    with pytest.raises(service.ServiceError, match="unknown agent"):
        service.set_conversation_agent(project, "r1", "nonesuch")


def test_switching_to_an_agent_that_cannot_converse_is_refused(project):
    with pytest.raises(service.ServiceError, match="conversation"):
        service.set_conversation_agent(project, "r1", "sleepy")


def test_switching_mid_turn_is_refused(project, running_turn):
    with pytest.raises(service.ServiceError, match="still"):
        service.set_conversation_agent(project, "r1", "stub2")
```

Add `stub2` to that file's `project` fixture with the same session commands as `stub`, so there is a second conversational agent to switch to.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_service.py -k switching -v`
Expected: FAIL — `has no attribute 'set_conversation_agent'`.

- [ ] **Step 3: Write the implementation**

```python
def set_conversation_agent(project: Project, slug: str, agent: str,
                           actor: str = "human") -> dict:
    """Choose who holds this run's conversation.

    Switching discards the session id, because one CLI's session means
    nothing to another — the next turn starts a new one. The turns already
    said are history and are kept.
    """
    ws = _ws(project, slug)
    agents = config.load_agents(project.root)
    if agent not in agents:
        raise ServiceError(f"unknown agent: {agent} (known: {', '.join(agents)})")
    if not sessions.can_converse(agents[agent]):
        raise ServiceError(
            f"{agent} cannot host a conversation: its configuration has no "
            "session_cmd and resume_cmd, so every turn would start over")
    if _turn_in_flight(project, ws):
        raise ServiceError("a turn is still running; wait for it or cancel it")
    try:
        return conversation.set_agent(ws, agent, actor)
    except conversation.ConversationError as exc:
        raise ServiceError(str(exc)) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_service.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/service.py tests/core/test_service.py
git commit -m "feat(core): switch the conversation's agent, starting a fresh session

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The chat panel

**Files:**
- Modify: `src/scieflow/web/api.py`, `src/scieflow/web/pages.py`, `src/scieflow/web/templates/run.html`, `src/scieflow/web/static/app.css`, `tests/web/mutating_paths.py`
- Test: `tests/web/test_conversation_page.py` (create)

**Interfaces:**
- Consumes: `service.conversation_state`, `service.say`, `service.set_conversation_agent`, `auth.csrf_token`, `pages.MUTATE`, `pages._back`.
- Produces: `GET /api/v1/runs/{slug}/conversation`, `POST /api/v1/runs/{slug}/conversation` (say), `POST /api/v1/runs/{slug}/conversation/agent`; page route `POST /runs/{slug}/say`.

**Live updates need no new transport.** A turn is a job, so the panel shows the turns it has, marks the run busy while one is running, and lets the existing timeline `EventSource` tell it when `turn.received` lands — at which point it re-fetches the conversation. Do not stream the job log into the chat: in session mode that log is the raw JSON stream, and the readable text only exists once the turn completes and `sessions.parse` has run.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_conversation_page.py
"""Talking to a run's coordinator from the browser."""

from scieflow.core.run import conversation
from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def test_the_api_reports_an_empty_conversation(client):
    body = client.get("/api/v1/runs/r1/conversation").json()
    assert body["agent"] == "" and body["turns"] == []
    assert body["can_converse"] is False


def test_the_run_page_offers_a_chat_panel(client, project):
    conversation.set_agent(project.run_dir("r1"), "stub")
    page = client.get("/runs/r1").text
    assert 'action="/runs/r1/say"' in page
    assert 'name="csrf_token"' in page


def test_the_panel_explains_itself_when_no_agent_is_chosen(client):
    page = client.get("/runs/r1").text
    assert "choose an agent" in page.lower()


def test_saying_something_from_the_page(client, project):
    conversation.set_agent(project.run_dir("r1"), "stub")
    response = post(client, "/runs/r1/say", message="What next?")
    assert response.status_code == 303
    assert conversation.read(project.run_dir("r1"))["turns"][0]["text"] == "What next?"


def test_an_empty_message_is_refused_with_an_explanation(client, project):
    conversation.set_agent(project.run_dir("r1"), "stub")
    response = post(client, "/runs/r1/say", message="   ")
    assert response.headers["location"].startswith("/runs/r1?error=")
    assert conversation.read(project.run_dir("r1"))["turns"] == []


def test_turn_text_is_escaped_on_the_page(client, project):
    """Turn text is whatever an agent wrote — never trusted markup."""
    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.add_turn(ws, role="agent", text="<script>alert('x')</script>")
    page = client.get("/runs/r1").text
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_switching_the_agent_from_the_page(client, project):
    conversation.set_agent(project.run_dir("r1"), "stub")
    assert post(client, "/runs/r1/say", action="agent",
                agent="stub2").status_code == 303
    assert conversation.read(project.run_dir("r1"))["agent"] == "stub2"


def test_the_conversation_route_on_an_unknown_run_is_404(client):
    assert client.get("/api/v1/runs/nope/conversation").status_code == 404
```

Extend `tests/web/mutating_paths.py` — `MUTATING_PATHS` and `SAMPLES` together, or the generated guard tests fail:

```python
    "/api/v1/runs/{slug}/conversation": {"post"},
    "/api/v1/runs/{slug}/conversation/agent": {"post"},
    "/runs/{slug}/say": {"post"},
```

The web `project` fixture needs `stub` and `stub2` configured as conversational agents, the same way Task 3 configured them in the core fixture.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_conversation_page.py tests/web/test_read_only.py -v`
Expected: FAIL — the routes 404 and the inventory test reports the three new paths declared but absent.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/web/api.py`:

```python
@router.get("/runs/{slug}/conversation", tags=["runs"])
async def conversation(request: Request, slug: str) -> dict:
    """The run's conversation: agent, session state and every turn."""
    return service.conversation_state(_project(request), slug)


@router.post("/runs/{slug}/conversation", dependencies=MUTATE, tags=["runs"])
async def say(request: Request, slug: str, message: str = Form(...)) -> dict:
    """Send one message; the reply is a sandboxed job that resumes the session."""
    return service.say(_project(request), slug, message)


@router.post("/runs/{slug}/conversation/agent", dependencies=MUTATE, tags=["runs"])
async def set_conversation_agent(request: Request, slug: str,
                                 agent: str = Form(...)) -> dict:
    """Hand the conversation to a different agent; the next turn starts fresh."""
    return service.set_conversation_agent(_project(request), slug, agent)
```

In `src/scieflow/web/pages.py`, one form route for both actions, and the conversation passed into the run page:

```python
@router.post("/runs/{slug}/say", dependencies=MUTATE)
async def say(request: Request, slug: str, action: str = Form("say"),
              message: str = Form(""), agent: str = Form("")):
    project = _project(request)
    try:
        if action == "agent":
            service.set_conversation_agent(project, slug, agent)
        else:
            service.say(project, slug, message)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)
```

```python
        "conversation": service.conversation_state(project, slug),
        "agents": service.agent_settings(project, slug)["agents"],
```

In `run.html`, a panel below the charter — the charter is what the run agreed, the conversation is how it is being steered, so they belong together:

```html
<h2>Conversation</h2>
{% if not conversation.agent %}
  <p class="dim">No agent is holding this conversation yet — choose an agent
     below to start one. Each message is a sandboxed job that resumes the
     agent's own session, so it keeps its context between turns.</p>
{% elif not conversation.can_converse %}
  <p class="state-failed">{{ conversation.agent }} cannot host a conversation:
     its configuration has no session commands, so every turn would start over.
     Choose another agent.</p>
{% endif %}

<div class="chat">
  {% for turn in conversation.turns %}
  <div class="turn {{ turn.role }}">
    <span class="dim">{{ turn.role }} · {{ turn.ts[11:19] }}</span>
    <pre>{{ turn.text }}</pre>
    {% if turn.job_id %}
    <a class="dim" href="/runs/{{ slug }}/jobs/{{ turn.job_id }}">this turn's job →</a>
    {% endif %}
  </div>
  {% endfor %}
  {% if conversation.busy %}<p class="dim">A turn is running…</p>{% endif %}
</div>

<form method="post" action="/runs/{{ slug }}/say">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <textarea name="message" rows="3" placeholder="Say something to the coordinator"
            {% if conversation.busy or not conversation.can_converse %}disabled{% endif %}></textarea>
  <button name="action" value="say"
          {% if conversation.busy or not conversation.can_converse %}disabled{% endif %}>Send</button>
</form>

<form method="post" action="/runs/{{ slug }}/say" class="actions">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <select name="agent">
    {% for name in agents %}
    <option {% if name == conversation.agent %}selected{% endif %}>{{ name }}</option>
    {% endfor %}
  </select>
  <button name="action" value="agent"
          {% if conversation.busy %}disabled{% endif %}>Hand the conversation over</button>
</form>
```

Extend the existing timeline `EventSource` block so a finished turn refreshes the page rather than leaving stale text on screen — the transport is already there:

```javascript
  stream.addEventListener("run", (message) => {
    const event = JSON.parse(message.data);
    if (event.type === "turn.received") { location.reload(); }
    ...
  });
```

Add to `static/app.css`:

```css
.chat { display: flex; flex-direction: column; gap: .75rem; margin: 1rem 0; }
.turn pre { white-space: pre-wrap; margin: .25rem 0; }
.turn.human pre { border-left: 3px solid var(--accent); padding-left: .75rem; }
.turn.agent pre { border-left: 3px solid var(--warn); padding-left: .75rem; }
```

Use whatever accent variables `app.css` already defines; if `--accent` is not one of them, pick from what is there rather than inventing a new token.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -v && uv run pytest -q`
Expected: all pass; the inventory matches `MUTATING_PATHS` exactly and the generated guard tests cover the three new paths.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/web tests/web
git commit -m "feat(web): talk to a run's coordinator from its page

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/runs.md`, `docs/web.md`, `docs/agents.md`, `docs/architecture.md`, `AGENTS.md`
- Test: none — this task's verification is the three commands in Step 2.

**Interfaces:**
- Consumes: everything Tasks 1–5 produced.
- Produces: no code.

- [ ] **Step 1: Write the documentation**

In `docs/runs.md`, a section on the conversation covering: that a run can be steered by talking to its coordinator; that **each message is one sandboxed job**, so a turn is confined, counts against the budget, appears on the timeline and can be cancelled exactly like any other dispatch; that continuity comes from the agent's own session id, which ScieFlow records on the run and hands back on the next turn; that the charter is pinned to the top of every turn; and that switching agents keeps the turns but starts a new session, because a session id means nothing to a different CLI.

In `docs/web.md`, add the chat panel to the run page's description and the three routes to the API table. State plainly that the panel refreshes when a turn lands rather than streaming the agent's output live, and why: in session mode the job's log is a JSON stream, and the readable text only exists once the turn completes.

In `docs/agents.md`, document what a conversational agent needs — `family`, `session_cmd` and `resume_cmd` — with the verified `claude` and `codex` values and the two traps: `codex exec resume` rejects `--sandbox` and takes its flags before the session id, and `claude` needs `--verbose` alongside `--output-format=stream-json`. Say what `agy` turned out to support. State that an agent without both commands cannot hold a conversation and the app will say so.

In `docs/architecture.md`, one paragraph: ScieFlow holds no long-lived agent process, and a conversation is a sequence of jobs — which is what keeps the sandbox, the budget and the timeline meaningful for chat turns.

In `AGENTS.md`, a line in rule 4's neighbourhood: a coordinator may be spoken to through a run's conversation, its turns are recorded in `conversation.yml`, and that file is never hand-edited.

Check every claim against the code as it shipped. Do not repeat the older, false statement that the CLI and the browser go through the same `service` function for run actions — for these conversation routes the browser does, and the statement is true of them, but it is not true generally.

- [ ] **Step 2: Verify everything**

Run each and check the exit code explicitly — a pipe would hide a failure:

```bash
uv run pytest -q; echo "pytest: $?"
./scripts/check_legacy.sh; echo "legacy: $?"
uv run --group docs mkdocs build --strict; echo "mkdocs: $?"
```

Expected: `pytest: 0` with no failures, `legacy: 0` with every check `ok`, `mkdocs: 0` with no warnings.

- [ ] **Step 3: Commit**

```bash
git add docs AGENTS.md
git commit -m "docs: the coordinator conversation — one job per turn

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
