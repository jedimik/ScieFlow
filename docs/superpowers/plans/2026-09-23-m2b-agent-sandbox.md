# Agent sandbox (M2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent dispatch runs inside a bubblewrap sandbox that can write only into the run it was given, and a dispatch that cannot prove its sandbox confines is refused rather than run.

**Architecture:** One module, `scieflow.core.sandbox`, is the only code that knows bubblewrap exists; it computes the writable set, builds the wrapped argv, and proves confinement with a live probe before each dispatch. `jobs.start` — the single funnel every dispatch already passes through — applies the wrapper, so nothing can route around it. Refusals reuse the existing refusal machinery (a `job.refused` event and a distinct exit code), and the escape hatch is an ordinary recorded event.

**Tech Stack:** Python 3.11+, bubblewrap 0.9.0 (`bwrap`), PyYAML, pytest. No new Python dependency.

**Spec:** `docs/superpowers/specs/2026-09-23-agent-sandbox-design.md`

## Global Constraints

- **Mechanism is bubblewrap only.** No fallback backend, no vendor sandbox flags. `config/agents.yml` is not modified by this milestone.
- **Filesystem confinement only.** The network is unrestricted and credentials stay readable — documented as the limit, never implied otherwise.
- **Fail closed.** No bubblewrap, or a probe that does not confine, means the dispatch is refused with exit code **77**, a `job.refused` event carrying `reason: sandbox`, and an error naming `sudo apt-get install -y bubblewrap`.
- **The escape hatch is explicit and recorded.** `sandbox: off` in a run's `config.yml`, or `--no-sandbox` on a one-off dispatch; every unsandboxed dispatch emits a `sandbox.disabled` event.
- **Writable set.** Sub-agent: its own `workspace/<slug>/`. Coordinator: the whole `workspace/` tree. Both also get a private `/tmp`, the tool cache (`UV_CACHE_DIR`, else `~/.cache/uv`), and allowlist entries. Never `src/`, `config/`, `$HOME`, or another run.
- **The caller declares which it is.** `writable_for(project, *, run_dir, coordinator)`; the sandbox never guesses.
- **The allowlist cannot grant itself.** `config/` is never writable, so `config/sandbox.yml` can only be edited by a human.
- **Scope.** Agent dispatches and coordinators. Experiment sweeps and sync jobs are *not* wrapped.
- **Nothing regresses.** `uv run pytest -q` stays green (936 passing at the start of this plan), `scripts/check_legacy.sh` stays all `ok`, `uv run --group docs mkdocs build --strict` stays at 0 warnings.

## Review Focus

Five conditions the spec implies that would otherwise reach a user untested. Each has a test in the task that owns the code.

1. **A writable path that does not exist yet** — a fresh machine has no `~/.cache/uv`, and bubblewrap refuses to bind a missing source. Without care the dispatch dies with a confusing bwrap error instead of running. The sandbox must create missing writable directories before binding. *(Task 1)*
2. **A slug containing spaces** — `Project.SLUG_RE` permits them, so `workspace/my run/` is legal. Bind paths and `--chdir` must survive it. *(Task 2)*
3. **bubblewrap installed but non-functional** — inside a container without user namespaces, `available()` is true and only the probe catches it. The dispatch must refuse with exit 77, not crash. *(Task 4)*
4. **A hostile allowlist entry** — one that escapes the repository, names `config/` wholesale, or names `config/sandbox.yml` itself. Must be rejected at load with a clear error, never silently granted. *(Task 2)*
5. **A dispatch whose prompt belongs to no run** — must be refused with a message naming `--no-sandbox`, never promoted to wider permissions. *(Task 4)*

## File structure

| File | Responsibility |
|---|---|
| `src/scieflow/core/sandbox.py` | the only bubblewrap-aware module: availability, writable sets, allowlist, argv wrapping, confinement proof |
| `config/sandbox.yml` | user-owned allowlist, shipped with the one documented exception |
| `src/scieflow/core/jobs.py` | applies the wrapper; records whether a job was sandboxed |
| `src/scieflow/core/agent_run.py` | computes the writable set, enforces fail-closed, owns `--no-sandbox` and exit 77 |
| `src/scieflow/core/events.py` | one new event type, `sandbox.disabled` |
| `src/scieflow/core/service.py` | passes the writable set through; surfaces sandbox state |
| `setup/install.sh`, `setup/doctor.sh` | bubblewrap as a stated requirement, and a functional check |
| `docs/sandbox.md` | what is confined, what is not, refusals, the hatch, the allowlist |
| `tests/core/test_sandbox.py` | the module's own tests, including the defeat test |

---

### Task 1: The sandbox module — availability, wrapping, and the confinement proof

**Files:**
- Create: `src/scieflow/core/sandbox.py`
- Test: `tests/core/test_sandbox.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `BWRAP = "bwrap"`; `VERIFY_TIMEOUT_S = 30`; `SandboxError(RuntimeError)`;
  `SandboxUnavailable(SandboxError)`; `available() -> bool`;
  `wrap(argv: list[str], *, writable: list[Path], cwd: Path) -> list[str]`;
  `verify(writable: list[Path], cwd: Path) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_sandbox.py
import shutil
import subprocess
from pathlib import Path

import pytest

from scieflow.core import sandbox

HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")


def test_available_reflects_the_binary(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.available() is False
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    assert sandbox.available() is True


def test_wrap_without_bubblewrap_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    with pytest.raises(sandbox.SandboxUnavailable, match="apt-get install"):
        sandbox.wrap(["echo", "hi"], writable=[tmp_path], cwd=tmp_path)


def test_wrap_builds_a_read_only_root_with_granted_binds(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    run = tmp_path / "run"
    run.mkdir()
    argv = sandbox.wrap(["agent", "--flag"], writable=[run], cwd=run)
    assert argv[0] == sandbox.BWRAP
    joined = " ".join(argv)
    assert "--ro-bind / /" in joined              # everything read-only first
    assert f"--bind {run} {run}" in joined        # then the grant on top
    assert "--tmpfs /tmp" in joined
    assert "--die-with-parent" in joined          # must not outlive a cancelled job
    assert argv[-2:] == ["agent", "--flag"]       # the real command last, after --
    assert argv[argv.index("--chdir") + 1] == str(run)


def test_wrap_creates_missing_writable_directories(monkeypatch, tmp_path):
    """bubblewrap refuses to bind a source that does not exist — a fresh machine
    has no uv cache, and the failure would otherwise be a cryptic bwrap error."""
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    absent = tmp_path / "not" / "created" / "yet"
    sandbox.wrap(["echo"], writable=[absent], cwd=tmp_path)
    assert absent.is_dir()


@needs_bwrap
def test_verify_passes_with_a_real_sandbox(tmp_path):
    sandbox.verify([tmp_path], cwd=tmp_path)      # does not raise


@needs_bwrap
def test_a_sandboxed_process_cannot_write_outside_its_grant(tmp_path):
    run, outside = tmp_path / "run", tmp_path / "outside"
    run.mkdir()
    outside.mkdir()
    argv = sandbox.wrap(
        ["/bin/sh", "-c", f"touch {run}/inside; touch {outside}/escaped"],
        writable=[run], cwd=run)
    subprocess.run(argv, capture_output=True, timeout=60, check=False)
    assert (run / "inside").exists()              # the grant works
    assert not (outside / "escaped").exists()     # and nothing else does


def test_verify_refuses_a_wrapper_that_does_not_confine(monkeypatch, tmp_path):
    """The defeat test: hand verify() a wrapper that runs the command unchanged.
    If verify() can be fooled, every other guarantee here is decoration."""
    monkeypatch.setattr(sandbox, "wrap",
                        lambda argv, *, writable, cwd: ["/bin/sh", "-c", argv[-1]])
    with pytest.raises(sandbox.SandboxUnavailable, match="did not block"):
        sandbox.verify([tmp_path], cwd=tmp_path)


def test_verify_reports_a_probe_that_cannot_run(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "wrap",
                        lambda argv, *, writable, cwd: ["/nonexistent/binary"])
    with pytest.raises(sandbox.SandboxUnavailable, match="could not run"):
        sandbox.verify([tmp_path], cwd=tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_sandbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scieflow.core.sandbox'`.

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/core/sandbox.py
"""Filesystem confinement for agent dispatches — the only bubblewrap-aware module.

A sandboxed process may write inside the run it was given, plus a private /tmp,
the tool cache and whatever the allowlist grants. It may read the repository,
because agents read their own protocols, skills and schemas.

What this deliberately does NOT do: restrict the network, or hide credentials.
Agents need both to reach their model API, so a hostile agent can still send
what it can read. See docs/sandbox.md; that limit is stated, not implied.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

BWRAP = "bwrap"
VERIFY_TIMEOUT_S = 30
INSTALL_HINT = "install it with: sudo apt-get install -y bubblewrap"


class SandboxError(RuntimeError):
    """The sandbox could not be built, or its configuration is not allowed."""


class SandboxUnavailable(SandboxError):
    """bubblewrap is missing, or it did not actually confine a test write."""


def available() -> bool:
    return shutil.which(BWRAP) is not None


def wrap(argv: list[str], *, writable: list[Path], cwd: Path) -> list[str]:
    """`argv` rewritten to run under bubblewrap, writable only where granted.

    Missing writable directories are created first: bubblewrap refuses to bind a
    source that does not exist, and on a fresh machine the tool cache has not
    been created yet — which would otherwise surface as a cryptic bwrap error.
    """
    if not available():
        raise SandboxUnavailable(f"bubblewrap is not installed; {INSTALL_HINT}")
    out = [BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
           "--tmpfs", "/tmp"]
    for path in writable:
        resolved = Path(path).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        out += ["--bind", str(resolved), str(resolved)]
    out += ["--chdir", str(Path(cwd).resolve()),
            "--unshare-pid", "--die-with-parent", "--"]
    return out + list(argv)


def verify(writable: list[Path], cwd: Path) -> None:
    """Prove the sandbox confines before trusting it; raise if it does not.

    Runs a throwaway process inside the sandbox that tries to write into $HOME,
    which is never in any writable set. If the file appears on the host, the
    sandbox is not in effect and the caller must refuse to dispatch. This is
    what separates "we passed the right flags" from "we watched it block a
    write", and it catches the mechanism changing underneath us.
    """
    probe = Path.home() / f".scieflow-sandbox-probe-{os.getpid()}"
    probe.unlink(missing_ok=True)
    script = f"touch {shlex.quote(str(probe))} 2>/dev/null; true"
    try:
        subprocess.run(wrap(["/bin/sh", "-c", script], writable=writable, cwd=cwd),
                       capture_output=True, timeout=VERIFY_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxUnavailable(f"the sandbox probe could not run: {exc}") from exc
    if probe.exists():
        probe.unlink(missing_ok=True)
        raise SandboxUnavailable(
            "the sandbox did not block a write to $HOME — refusing to dispatch")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_sandbox.py -v && uv run pytest -q`
Expected: 8 passed (2 skipped if bubblewrap is absent); full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/sandbox.py tests/core/test_sandbox.py
git commit -m "feat(core): bubblewrap sandbox module with a confinement proof

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The writable set and the allowlist

**Files:**
- Modify: `src/scieflow/core/sandbox.py` (append)
- Create: `config/sandbox.yml`
- Test: `tests/core/test_sandbox.py` (append)

**Interfaces:**
- Consumes: `SandboxError` from Task 1.
- Produces: `ALLOWLIST_FILE = "config/sandbox.yml"`; `tool_cache() -> Path`;
  `allowlist_paths(project: Project) -> list[Path]`;
  `writable_for(project: Project, *, run_dir: Path | None, coordinator: bool) -> list[Path]`.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_sandbox.py`)

```python
from scieflow.core.project import Project


def make_project(tmp_path) -> Project:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "workspace" / "r1").mkdir(parents=True)
    return Project(tmp_path)


def test_tool_cache_honours_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "custom"))
    assert sandbox.tool_cache() == tmp_path / "custom"
    monkeypatch.delenv("UV_CACHE_DIR")
    assert sandbox.tool_cache() == Path.home() / ".cache" / "uv"


def test_sub_agent_gets_its_own_run_and_nothing_wider(tmp_path):
    project = make_project(tmp_path)
    run = project.run_dir("r1")
    writable = sandbox.writable_for(project, run_dir=run, coordinator=False)
    assert run in writable
    assert sandbox.tool_cache() in writable
    assert project.workspace_root not in writable      # not the whole tree
    assert project.root not in writable                # and certainly not the repo


def test_coordinator_gets_the_workspace_tree(tmp_path):
    project = make_project(tmp_path)
    writable = sandbox.writable_for(project, run_dir=None, coordinator=True)
    assert project.workspace_root in writable
    assert project.root not in writable


def test_a_sub_agent_without_a_run_is_refused_not_promoted(tmp_path):
    project = make_project(tmp_path)
    with pytest.raises(sandbox.SandboxError, match="must name the run"):
        sandbox.writable_for(project, run_dir=None, coordinator=False)


def test_a_slug_with_spaces_still_works(tmp_path):
    """Project.SLUG_RE permits spaces, so `workspace/my run/` is a legal run."""
    project = make_project(tmp_path)
    run = project.run_dir("my run")
    run.mkdir(parents=True)
    writable = sandbox.writable_for(project, run_dir=run, coordinator=False)
    assert run in writable
    argv = sandbox.wrap(["echo"], writable=writable, cwd=run)
    assert str(run) in argv            # one argv element, never word-split
    assert argv[argv.index("--chdir") + 1] == str(run)


def test_allowlist_absent_grants_nothing_extra(tmp_path):
    project = make_project(tmp_path)
    assert sandbox.allowlist_paths(project) == []


def test_allowlist_grants_a_listed_path(tmp_path):
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(
        "writable:\n  - path: config/journals\n    reason: journal cache\n")
    assert sandbox.allowlist_paths(project) == [(tmp_path / "config" / "journals").resolve()]


@pytest.mark.parametrize("entry, match", [
    ("../outside", "escapes the repository"),
    ("/etc", "escapes the repository"),
    (".", "the whole repository"),
    ("config", "the whole repository"),          # would include sandbox.yml itself
    ("config/sandbox.yml", "cannot grant write access to itself"),
])
def test_allowlist_rejects_dangerous_entries(tmp_path, entry, match):
    project = make_project(tmp_path)
    (tmp_path / "config" / "sandbox.yml").write_text(
        f"writable:\n  - path: {entry}\n    reason: nope\n")
    with pytest.raises(sandbox.SandboxError, match=match):
        sandbox.allowlist_paths(project)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_sandbox.py -v`
Expected: the new tests FAIL — `module 'scieflow.core.sandbox' has no attribute 'writable_for'`.

- [ ] **Step 3: Write the implementation** (append to `src/scieflow/core/sandbox.py`)

```python
import yaml

from scieflow.core.project import Project

ALLOWLIST_FILE = "config/sandbox.yml"


def tool_cache() -> Path:
    """Where uv keeps its cache. Writable in every sandbox: `uv run` fails
    outright without it, so every agent that runs a scieflow command needs it."""
    override = os.environ.get("UV_CACHE_DIR")
    return Path(override) if override else Path.home() / ".cache" / "uv"


def allowlist_paths(project: Project) -> list[Path]:
    """Extra writable paths from config/sandbox.yml, deny-by-default.

    The file lives in config/, which is never writable inside a sandbox, so an
    agent cannot extend its own permissions — adding an entry is a human edit.
    """
    path = project.root / ALLOWLIST_FILE
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    root = project.root.resolve()
    forbidden = {root, (root / "config").resolve()}
    out: list[Path] = []
    for entry in data.get("writable") or []:
        raw = entry.get("path") if isinstance(entry, dict) else entry
        if not raw:
            continue
        candidate = (root / str(raw)).resolve()
        if candidate in forbidden:
            raise SandboxError(
                f"allowlist entry {raw!r} would grant the whole repository or all of "
                "config/; grant the specific directory instead")
        if root not in candidate.parents:
            raise SandboxError(f"allowlist entry {raw!r} escapes the repository")
        if candidate == (root / ALLOWLIST_FILE).resolve():
            raise SandboxError("the allowlist cannot grant write access to itself")
        out.append(candidate)
    return out


def writable_for(project: Project, *, run_dir: Path | None,
                 coordinator: bool) -> list[Path]:
    """What this dispatch may write. The caller declares which kind it is —
    the sandbox never guesses, because guessing wrong widens the boundary."""
    if coordinator:
        base = [project.workspace_root]
    elif run_dir is None:
        raise SandboxError(
            "a sandboxed agent dispatch must name the run it belongs to; "
            "use --no-sandbox for a one-off dispatch outside a run")
    else:
        base = [Path(run_dir)]
    return [*base, tool_cache(), *allowlist_paths(project)]
```

Create `config/sandbox.yml`:

```yaml
# Paths a sandboxed agent may write in addition to its own run.
#
# Deny-by-default: delete an entry and that path becomes read-only for agents.
# Agents cannot edit this file — config/ is never writable inside the sandbox —
# so extending this list is always a human decision. Every entry needs a reason.
writable:
  - path: config/journals
    reason: >-
      paper-review and paper-draft cache journal profiles here, the one
      documented exception to AGENTS.md rule 1
      (src/scieflow/research/AGENTS.md).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_sandbox.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/sandbox.py config/sandbox.yml tests/core/test_sandbox.py
git commit -m "feat(core): sandbox writable sets and the deny-by-default allowlist

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The job runner applies the sandbox

**Files:**
- Modify: `src/scieflow/core/jobs.py` (the `Job` dataclass and `start`)
- Test: `tests/core/test_jobs.py` (append)

**Interfaces:**
- Consumes: `sandbox.wrap` from Task 1.
- Produces: `Job.sandboxed: bool = False`; `jobs.start(..., sandbox_writable: list[Path] | None = None)`.
  When `sandbox_writable` is given the argv is wrapped and `Job.sandboxed` is `True`;
  `Job.argv` keeps the **unwrapped** command so the UI shows what was actually asked for.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_jobs.py`)

```python
import shutil

HAVE_BWRAP = shutil.which("bwrap") is not None


def test_a_job_without_a_writable_set_is_not_sandboxed(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = jobs.run_blocking(project, [PY_EXE, "-c", "print(1)"], kind="agent",
                            cwd=tmp_path, run_dir=ws)
    assert job.sandboxed is False
    assert job.state == "done"


@pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
def test_a_sandboxed_job_writes_inside_its_run_and_nowhere_else(tmp_path):
    project, ws = project_and_run(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    code = (f"open({str(ws / 'inside.txt')!r}, 'w').write('ok')\n"
            f"try:\n"
            f"    open({str(outside / 'escaped.txt')!r}, 'w').write('bad')\n"
            f"except OSError:\n"
            f"    pass\n")
    job = jobs.run_blocking(project, [PY_EXE, "-c", code], kind="agent", cwd=ws,
                            run_dir=ws, sandbox_writable=[ws])
    assert job.sandboxed is True
    assert (ws / "inside.txt").exists()
    assert not (outside / "escaped.txt").exists()


@pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
def test_the_recorded_argv_is_the_command_not_the_wrapper(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = jobs.run_blocking(project, [PY_EXE, "-c", "print(1)"], kind="agent",
                            cwd=ws, run_dir=ws, sandbox_writable=[ws])
    assert job.argv[0] == PY_EXE            # what was asked for
    assert "bwrap" not in " ".join(job.argv)  # not the plumbing
```

Add `import pytest` at the top of the file if it is not already imported.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_jobs.py -v`
Expected: FAIL — `Job.__init__() got an unexpected keyword argument` / `start() got an unexpected keyword argument 'sandbox_writable'`.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/jobs.py`, add the field at the **end** of the `Job` dataclass, so existing job records on disk still load:

```python
    err: str = ""
    sandboxed: bool = False
```

Add the import near the other core imports:

```python
from scieflow.core import events, sandbox, store
```

Change `start`'s signature and the launch, keeping the recorded argv unwrapped:

```python
def start(project: Project, argv: list[str], *, kind: str, cwd: Path,
          run_dir: Path | None = None, label: str = "", timeout_s: float | None = None,
          stdin_text: str | None = None, env: dict | None = None,
          sandbox_writable: list[Path] | None = None) -> tuple[Job, subprocess.Popen]:
    job_id = store.new_id()
    directory = jobs_dir(project, run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, kind=kind, argv=list(argv), cwd=str(cwd), label=label,
              run_dir=str(run_dir) if run_dir else None, queued=_now(), timeout_s=timeout_s,
              log=str(directory / f"{job_id}.log"), err=str(directory / f"{job_id}.err"),
              sandboxed=sandbox_writable is not None)
    # The job records the command that was asked for; the wrapper is plumbing.
    launch = (sandbox.wrap(argv, writable=sandbox_writable, cwd=cwd)
              if sandbox_writable is not None else list(argv))
    save(job)
    _emit(job, "job.queued")
    with open(job.log, "w") as out, open(job.err, "w") as err:
        try:
            proc = subprocess.Popen(
                launch, cwd=cwd, stdout=out, stderr=err, text=True, env=env,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                start_new_session=True,
            )
```

The rest of `start` is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_jobs.py tests/core/test_service.py -v && uv run pytest -q`
Expected: all pass. `test_service.py` matters here because `service.job_json` serialises `Job`; the new field must flow through without breaking its cross-endpoint equality test.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/jobs.py tests/core/test_jobs.py
git commit -m "feat(core): the job runner applies the sandbox when given a writable set

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Dispatch enforces the sandbox — fail closed, exit 77, and the escape hatch

**Files:**
- Modify: `src/scieflow/core/agent_run.py`, `src/scieflow/core/events.py`
- Test: `tests/core/test_agent_run.py` (append, plus two helper edits)

**Interfaces:**
- Consumes: `sandbox.writable_for`, `sandbox.verify`, `sandbox.SandboxError/SandboxUnavailable`;
  `jobs.start(..., sandbox_writable=...)`.
- Produces: `SANDBOX_EXIT = 77`; `Dispatch.writable: list[Path] | None`;
  `prepare(project, agent, prompt_file, cwd=None, role=None, *, sandbox: bool = True)`;
  `--no-sandbox` on `scieflow agent run`; the `sandbox.disabled` event type.

- [ ] **Step 1: Write the failing tests** (append to `tests/core/test_agent_run.py`)

```python
def bwrap_free_env(tmp_path):
    """A PATH with no bwrap on it, and a throwaway HOME so a probe cannot
    litter the real one."""
    empty = tmp_path / "emptybin"
    empty.mkdir(exist_ok=True)
    return {**os.environ, "PATH": str(empty), "HOME": str(tmp_path / "home")}


def fake_bwrap_env(tmp_path):
    """A `bwrap` that confines nothing: it drops its own options and runs the
    command. The sandbox looks available but does not work — the case a
    container without user namespaces produces."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "bwrap"
    shim.write_text('#!/bin/sh\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n')
    shim.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(home)}


def test_dispatch_is_refused_when_bubblewrap_is_missing(tmp_path):
    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (ws / "logs" / "p.md").write_text("output: x.md\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=bwrap_free_env(tmp_path))
    assert proc.returncode == 77
    assert "bubblewrap" in (proc.stdout + proc.stderr)
    assert "apt-get install" in (proc.stdout + proc.stderr)
    assert "sandbox" in (ws / "logs" / "t.md").read_text()


def test_dispatch_is_refused_when_the_sandbox_does_not_confine(tmp_path):
    """bwrap is on PATH but confines nothing. Only the probe catches this."""
    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (ws / "logs" / "p.md").write_text("output: x.md\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=fake_bwrap_env(tmp_path))
    assert proc.returncode == 77
    assert "did not block" in (proc.stdout + proc.stderr + (ws / "logs" / "t.md").read_text())


def test_no_sandbox_flag_runs_and_records_the_choice(tmp_path):
    from scieflow.core import events

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    out = ws / "iterations" / "h.md"
    (ws / "logs" / "p.md").write_text(f"output: {out}\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "--no-sandbox", "stub",
         str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=bwrap_free_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert "sandbox.disabled" in [e["type"] for e in events.read(ws)]


def test_run_config_can_disable_the_sandbox(tmp_path):
    from scieflow.core import events

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\nsandbox: off\n")
    out = ws / "iterations" / "h.md"
    (ws / "logs" / "p.md").write_text(f"output: {out}\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=bwrap_free_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert "sandbox.disabled" in [e["type"] for e in events.read(ws)]


def test_a_dispatch_outside_any_run_says_how_to_proceed(tmp_path):
    """Refused rather than promoted to wider permissions — and the message
    names the flag that makes an ad-hoc dispatch possible."""
    root = make_repo(tmp_path)
    (root / "p.md").write_text("output: out.md\nkind: hypothesis\n")
    proc = subprocess.run([*AGENT_RUN, "stub", str(root / "p.md"), str(root / "t.md")],
                          capture_output=True, text=True, cwd=root)
    assert proc.returncode == 77
    assert "--no-sandbox" in (proc.stdout + proc.stderr)


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")
def test_a_sandboxed_agent_cannot_write_to_module_code(tmp_path):
    """The headline guarantee of this milestone. If one assertion survives from
    this plan, it is this one."""
    root = make_repo(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "untouched.py").write_text("original\n")
    ws = make_loop_workspace(root)
    target = root / "src" / "untouched.py"
    (root / "config" / "agents.yml").write_text(
        "agents:\n"
        f'  writer: {{cmd: "sh -c \'echo hacked > {target}\'", enabled: true, timeout_min: 1}}\n')
    (ws / "logs" / "p.md").write_text("go\n")
    subprocess.run([*AGENT_RUN, "writer", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
                   capture_output=True, text=True, cwd=root)
    assert target.read_text() == "original\n"     # the write never landed
```

Add `import shutil` at the top of the file if absent.

Also edit the two existing helpers so run-less dispatches keep working — they exist precisely for prompts that belong to no run, which is now a refusal:

```python
def run_dispatch(agent: str, prompt_file: Path, transcript: Path, cwd: Path | None = None):
    # These prompts belong to no run, which the sandbox refuses by design.
    argv = [*AGENT_RUN, "--no-sandbox", agent, str(prompt_file), str(transcript)]
    if cwd is not None:
        argv += ["--cwd", str(cwd)]
    return subprocess.run(argv, capture_output=True, text=True)


def run_in_repo(root: Path, agent: str, prompt: str, env: dict | None = None):
    (root / "prompt.md").write_text(prompt)
    return subprocess.run(
        [*AGENT_RUN, "--no-sandbox", agent, str(root / "prompt.md"), str(root / "out.log")],
        capture_output=True, text=True, cwd=root, env=env,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_agent_run.py -v`
Expected: the new tests FAIL — `unrecognized arguments: --no-sandbox`, and dispatches succeed where 77 is expected.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/events.py`, add to `TYPES`:

```python
    "job.cancelled", "job.lost", "job.refused", "sandbox.disabled",
```

In `src/scieflow/core/agent_run.py`, add the import and the exit code:

```python
from scieflow.core import sandbox

SANDBOX_EXIT = 77   # EX_NOPERM: refused for want of a sandbox, not an agent failure
```

Add `writable` to the `Dispatch` dataclass:

```python
@dataclass
class Dispatch:
    agent: str
    argv: list[str]
    cwd: Path
    stdin_text: str | None
    timeout_s: float
    run_dir: Path | None
    writable: list[Path] | None = None    # None means this dispatch is unsandboxed
```

Add a reader for the run-level switch, next to `load_prompt_override`:

```python
def sandbox_disabled_in_run(run_dir: Path | None) -> bool:
    """True when a run's config.yml opts out with `sandbox: off`."""
    if run_dir is None:
        return False
    path = Path(run_dir) / "config.yml"
    if not path.is_file():
        return False
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return False
    value = data.get("sandbox")
    return value is False or str(value).lower() in {"off", "false", "no"}
```

Give `prepare` the switch, and compute the writable set at its end (replacing the existing `return Dispatch(...)`):

```python
def prepare(project: Project, agent: str, prompt_file: Path,
            cwd: Path | None = None, role: str | None = None, *,
            sandbox_enabled: bool = True) -> Dispatch:
```

```python
    use_sandbox = sandbox_enabled and not sandbox_disabled_in_run(run_dir)
    writable = (sandbox.writable_for(project, run_dir=run_dir, coordinator=False)
                if use_sandbox else None)
    return Dispatch(agent=agent, argv=argv, cwd=cwd,
                    stdin_text=prompt if use_stdin else None,
                    timeout_s=float(agent_cfg.get("timeout_min", 10)) * 60,
                    run_dir=run_dir, writable=writable)
```

In `main`, add the flag, and enforce before dispatching:

```python
    ap.add_argument("--no-sandbox", action="store_true",
                    help="run without filesystem confinement; recorded on the run's timeline")
    args = ap.parse_args(argv)

    project = Project.discover()
    try:
        d = prepare(project, args.agent, args.prompt_file, args.cwd, args.role,
                    sandbox_enabled=not args.no_sandbox)
    except DispatchError as e:
        sys.exit(str(e))
    except sandbox.SandboxError as e:
        _write(args.transcript_file, f"{args.agent}: refused, {e}\n")
        sys.exit(SANDBOX_EXIT)

    if d.writable is None:
        if d.run_dir is not None:
            events.emit(d.run_dir, "sandbox.disabled", "human", agent=d.agent,
                        why="--no-sandbox" if args.no_sandbox else "config.yml sandbox: off")
    else:
        try:
            sandbox.verify(d.writable, d.cwd)
        except sandbox.SandboxUnavailable as e:
            _write(args.transcript_file, f"{d.agent}: refused, {e}\n")
            if d.run_dir is not None:
                events.emit(d.run_dir, "job.refused", "system", reason="sandbox",
                            detail=str(e))
            sys.exit(SANDBOX_EXIT)
```

Import `events` at the top if it is not already imported, and pass the writable set to the runner:

```python
        job = jobs.run_blocking(project, d.argv, kind="agent", cwd=d.cwd, run_dir=d.run_dir,
                                label=d.agent, timeout_s=d.timeout_s, stdin_text=d.stdin_text,
                                sandbox_writable=d.writable)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_agent_run.py -v && uv run pytest -q && scripts/check_legacy.sh`
Expected: all pass; full suite green; every legacy check `ok`.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/agent_run.py src/scieflow/core/events.py tests/core/test_agent_run.py
git commit -m "feat(core): dispatches fail closed without a proven sandbox (exit 77)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The service layer and the web app carry the sandbox through

**Files:**
- Modify: `src/scieflow/core/service.py` (`dispatch_agent`), `src/scieflow/web/templates/run.html`
- Test: `tests/core/test_service.py`, `tests/web/test_pages.py` (append to each)

**Interfaces:**
- Consumes: `Dispatch.writable`, `jobs.start(..., sandbox_writable=...)`, `Job.sandboxed`.
- Produces: no new names; `service.dispatch_agent` gains the same fail-closed behaviour as the CLI,
  raising `ServiceError` when the sandbox cannot be proven.

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_service.py (append)
def test_dispatch_through_the_service_is_sandboxed(project, monkeypatch):
    """The web app must inherit the boundary without knowing it exists."""
    seen = {}
    real_start = service.jobs.start

    def spy(project_, argv, **kw):
        seen.update(kw)
        return real_start(project_, argv, **kw)

    monkeypatch.setattr(service.jobs, "start", spy)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert seen["sandbox_writable"] is not None
    assert ws in seen["sandbox_writable"]


def test_service_dispatch_refuses_without_a_sandbox(project, monkeypatch):
    from scieflow.core import sandbox

    monkeypatch.setattr(sandbox, "available", lambda: False)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("go\n")
    with pytest.raises(service.ServiceError, match="bubblewrap"):
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
```

```python
# tests/web/test_pages.py (append)
def test_run_page_marks_an_unsandboxed_job(client, project):
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    job.sandboxed = False
    jobs.save(job)
    assert "unsandboxed" in client.get("/runs/r1").text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_service.py tests/web/test_pages.py -v`
Expected: FAIL — `KeyError: 'sandbox_writable'`, no `ServiceError`, and no marker in the page.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/service.py`, inside `dispatch_agent`, after the budget guard and before `jobs.start`:

```python
    from scieflow.core import sandbox

    if d.writable is not None:
        try:
            sandbox.verify(d.writable, d.cwd)
        except sandbox.SandboxError as exc:
            raise ServiceError(str(exc)) from exc
    job, proc = jobs.start(project, d.argv, kind="agent", cwd=d.cwd, run_dir=d.run_dir,
                           label=agent, timeout_s=d.timeout_s, stdin_text=d.stdin_text,
                           sandbox_writable=d.writable)
```

`prepare` already computes `d.writable`, so a `SandboxError` from a run-less dispatch surfaces
through the same `except DispatchError` path the function already has — widen that clause:

```python
    try:
        d = prepare(project, agent, Path(prompt_file), cwd, role)
    except (DispatchError, sandbox.SandboxError) as e:
        raise ServiceError(str(e)) from e
```

In `src/scieflow/web/templates/run.html`, mark it in the jobs table, next to the state cell:

```html
    <td class="state-{{ job.state }}">
      {{ job.state }}{% if not job.sandboxed %} <span class="dim">· unsandboxed</span>{% endif %}
    </td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_service.py tests/web -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/service.py src/scieflow/web/templates/run.html tests/core/test_service.py tests/web/test_pages.py
git commit -m "feat(web): the service layer carries the sandbox; the run page shows unsandboxed jobs

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Setup, documentation, and the protocol rule

**Files:**
- Modify: `setup/install.sh`, `setup/doctor.sh`, `AGENTS.md`, `mkdocs.yml`, `docs/runs.md`, `docs/cli.md`
- Create: `docs/sandbox.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Add bubblewrap to the installer**

In `setup/install.sh`, in the existing `check` block, after the `check agy` line:

```bash
check bwrap     "sudo apt-get install -y bubblewrap (required: agent sandboxing)" ""
```

- [ ] **Step 2: Make doctor prove it, not just find it**

In `setup/doctor.sh`, add a section in the file's existing style. It must check the binary **and**
that confinement actually works, because a present-but-broken bubblewrap is the dangerous case:

```bash
echo "== sandbox =="
if command -v bwrap >/dev/null 2>&1; then
  ok "bwrap $(bwrap --version | awk '{print $2}')"
  probe="$HOME/.scieflow-doctor-probe-$$"
  rm -f "$probe"
  sbtmp="$(mktemp -d)"
  bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
        --bind "$sbtmp" "$sbtmp" --chdir "$sbtmp" --unshare-pid --die-with-parent \
        -- /bin/sh -c "touch '$probe' 2>/dev/null; touch '$sbtmp/inside'" >/dev/null 2>&1 || true
  if [ -e "$probe" ]; then
    rm -f "$probe"; bad "bwrap did not confine a write to \$HOME — dispatches will be refused"
  elif [ -e "$sbtmp/inside" ]; then
    ok "confines writes (granted path writable, \$HOME blocked)"
  else
    bad "bwrap could not run the probe at all"
  fi
  rm -rf "$sbtmp"
else
  bad "bwrap missing — agent dispatches are refused (sudo apt-get install -y bubblewrap)"
fi
```

- [ ] **Step 3: Write `docs/sandbox.md`**

Cover, in this order, and check every claim against the code as you write it:

1. **What it is** — every agent dispatch runs confined to the run it was given; the rule in
   `AGENTS.md` is now enforced rather than requested.
2. **What is confined** — the writable-set table from the spec (sub-agent, coordinator), and that
   the repository stays readable because agents read their own skills and schemas.
3. **What it does not do** — the network is unrestricted and credentials remain readable, so a
   hostile agent can still send what it can read. State it plainly; do not imply otherwise.
4. **Requirements** — `sudo apt-get install -y bubblewrap`, and `setup/doctor.sh` to confirm it
   actually confines.
5. **When a dispatch is refused** — exit 77, what the message means, the `job.refused` event with
   `reason: sandbox`, and the two fixes (install bubblewrap, or opt out deliberately).
6. **The escape hatch** — `--no-sandbox`, `sandbox: off` in a run's `config.yml`, the
   `sandbox.disabled` event, and where it shows up (`scieflow run events`, the run page).
7. **The allowlist** — `config/sandbox.yml`, deny-by-default, each entry with a reason, why
   `config/journals` is there, and that agents cannot edit the file because `config/` is never
   writable.

- [ ] **Step 4: Wire the docs together**

- `mkdocs.yml`: add `- Sandbox: sandbox.md` immediately after `- Web app: web.md`.
- `AGENTS.md` rule 1: append a sentence — "This boundary is enforced, not merely requested: a
  dispatch runs inside a sandbox that can write only into its own run, and one that cannot prove
  its sandbox confines is refused (`docs/sandbox.md`)."
- `docs/runs.md`: one line in the jobs section noting dispatches run sandboxed, linking
  `sandbox.md`.
- `docs/cli.md`: document `--no-sandbox` under `scieflow agent run`, and add **77** to the
  exit-code table — "sandbox could not be established or proven; the dispatch was refused".

- [ ] **Step 5: Full verification**

```bash
uv run pytest -q
scripts/check_legacy.sh
uv run --group docs mkdocs build --strict
setup/doctor.sh 2>&1 | grep -A3 "== sandbox =="
uv run scieflow agent run --help | grep -- --no-sandbox
```
Expected: suite green (936 + this plan's new tests), every legacy check `ok`, docs build with 0
warnings, doctor reports the sandbox confines, and the flag is documented in `--help`.

- [ ] **Step 6: Commit**

```bash
git add setup/install.sh setup/doctor.sh docs/sandbox.md docs/runs.md docs/cli.md mkdocs.yml AGENTS.md
git commit -m "docs(sandbox): requirements, refusals, the escape hatch and the allowlist

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.** Boundary and writable sets: Task 2. Bubblewrap mechanism and the argv shape:
Task 1. The per-dispatch confinement proof: Task 1 (the module) and Task 4 (enforcement). Fail
closed with exit 77 and a `job.refused` event: Task 4. The escape hatch, both forms, each emitting
`sandbox.disabled`: Task 4. The allowlist file, deny-by-default, self-protecting: Task 2. Caller
declares coordinator versus sub-agent: Task 2 (`writable_for`) — the coordinator launcher itself
belongs to the control milestone, which this plan deliberately does not build. Integration at
`jobs.start`: Task 3; at the service layer: Task 5. `install.sh`, `doctor.sh` and documentation:
Task 6. The spec's out-of-scope items (network egress, credential scoping, sandboxing experiment
stages, changing `config/agents.yml`) appear in no task, as intended.

**Placeholders.** None: every step carries the code or the exact file content it needs, and Task 6's
documentation step lists the sections and the claims to verify rather than saying "write the docs".

**Type consistency.** `wrap(argv, *, writable, cwd)`, `verify(writable, cwd)`,
`writable_for(project, *, run_dir, coordinator)`, `Job.sandboxed`,
`jobs.start(..., sandbox_writable=...)` and `Dispatch.writable` are used in later tasks exactly as
defined in the task that introduces them. `verify` takes `cwd` in both its definition (Task 1) and
both call sites (Tasks 4 and 5).

**Review Focus coverage.** (1) Missing writable directory —
`test_wrap_creates_missing_writable_directories`, Task 1. (2) Slug with spaces —
`test_a_slug_with_spaces_still_works`, Task 2. (3) Present but non-functional bubblewrap —
`test_dispatch_is_refused_when_the_sandbox_does_not_confine`, Task 4, using a real shim that
confines nothing. (4) Hostile allowlist entries — `test_allowlist_rejects_dangerous_entries`,
Task 2, parametrised over five shapes. (5) Dispatch with no owning run —
`test_a_dispatch_outside_any_run_says_how_to_proceed`, Task 4.

**One deliberate behaviour change to flag for the executor.** Refusing a dispatch whose prompt
belongs to no run breaks the existing ad-hoc flow that `tests/core/test_agent_run.py` exercises
through `run_dispatch` and `run_in_repo`. Task 4 edits those two helpers to pass `--no-sandbox`,
which is the honest fix — such dispatches genuinely have no run to be confined to. If a reviewer
sees those helper edits, they are intended, not an attempt to dodge a failing test.
