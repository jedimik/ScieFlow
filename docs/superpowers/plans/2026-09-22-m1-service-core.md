# M1 — Service Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give ScieFlow an observable, controllable, machine-checkable core — explicit project
context, safe state files, an event log, tracked cancellable jobs, approvals as data, budgets
enforced in code, per-role staffing, and one service layer — without breaking a single existing
command, import or old chat.

**Architecture:** Low-level modules take a run directory (`ws: Path`) so they work for any
workspace path, including test ones; the service layer maps `project + slug → ws`. Pure functions
(`status.mark`, `budget.record`) stay pure; `run.actions` wraps them with a locked write plus an
event. Every subprocess becomes a `jobs.Job` streamed to disk. Legacy `scripts/*.py` become
shims that alias the package modules, so `import status` and `uv run scripts/checkpoint.py`
keep working.

**Tech Stack:** Python ≥3.11, click, PyYAML, ruamel.yaml, filelock (moves into core deps),
pytest. No other new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-22-core-roadmap.md` — Milestone 1 (§1.1–§1.9).

## Global Constraints

- Python `>=3.11`; the only dependency change is `filelock>=3.12` moving from the `news` extra
  into core `dependencies`.
- Every existing test stays green after every task: `uv run pytest -q` (baseline 779 passed).
- `scripts/check_legacy.sh` keeps passing; `import status`, `import budget`, `import checkpoint`,
  `import sfx_init`, `import validate` and `uv run scripts/{checkpoint,sfx_init,validate}.py` keep
  working unchanged.
- `scieflow agent run` keeps its exit codes: 0 ok, 124 timeout, the agent's own exit code on
  failure. New: **75** = refused because a budget is exhausted (never collides with agent codes
  like 3).
- ScieFlow never chooses or downgrades a model by itself; model/effort come only from the
  registry, workspace overrides, or explicit role entries the user wrote.
- State files are written only through `scieflow.core.store` (lock + atomic replace).
- Budgets: `iterations` is enforced when advancing an iteration, `experiment_runs` before a
  sweep, `wall_minutes` before any agent dispatch; wall time is **accumulated from job
  durations**, never measured from the run's start.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## File Structure

| Path | Responsibility |
|---|---|
| `src/scieflow/core/project.py` (new) | `Project(root)`: root, workspace/state dirs, run dir lookup, config + schema loaders |
| `src/scieflow/core/store.py` (new) | `new_id()` ULIDs; locked atomic YAML/text writes; locked JSONL append/read |
| `src/scieflow/core/run/__init__.py` (new) | package marker |
| `src/scieflow/core/run/status.py` (moved from `scripts/status.py`) | status.yml transitions, lazy vocabulary, run `id` |
| `src/scieflow/core/run/budget.py` (moved) | budget ledger |
| `src/scieflow/core/run/checkpoint.py` (moved) | graceful stop + resume text |
| `src/scieflow/core/run/init.py` (moved from `scripts/sfx_init.py`) | create a run workspace |
| `src/scieflow/core/run/validate.py` (moved) | schema validation, incl. research status |
| `src/scieflow/core/run/actions.py` (new) | status/budget changes + events; budget guards |
| `src/scieflow/core/run/cli.py` (new) | `scieflow run …` |
| `src/scieflow/core/events.py` (new) | per-run `events.jsonl` |
| `src/scieflow/core/jobs.py` (new) | job records, streamed output, timeout, cancel, reconcile |
| `src/scieflow/core/gates.py` (new) | approvals as data + `scieflow gate …` |
| `src/scieflow/core/service.py` (new) | the one application API every front end calls |
| `schemas/status-research.yml`, `schemas/gates.yml` (new) | vocabularies as data |
| `scripts/{status,budget,checkpoint,sfx_init,validate}.py` | become alias shims |
| `src/scieflow/core/agent_run.py` | `prepare()` + job runner + budget guard + `--role` |
| `src/scieflow/core/agent_config.py`, `agent_configure.py`, `menu.py` | rich role entries |
| `src/scieflow/core/workspace.py` | structured run fields |
| `src/scieflow/experiments/cli.py` | sweep guarded + counted against `experiment_runs` |
| `tests/conftest.py` (new) | isolates `SCIEFLOW_STATE_DIR` for every test |

**Deferred to M2 on purpose** (they only matter once a long-lived server exists, and M2's plan
owns them): exposing the remote PBS ledger (`scripts/remote/jobs.py`) as `kind: remote` jobs in
the unified job list, and a per-project concurrency limit on started jobs. M1 only moves the
PBS ledger onto the safe store (Task 3).

---

### Task 1: Project context

**Files:**
- Create: `src/scieflow/core/project.py`
- Create: `tests/conftest.py`
- Test: `tests/core/test_project.py`

**Interfaces:**
- Produces: `Project(root: Path)` (frozen dataclass) with `Project.discover(start: Path | None = None) -> Project`,
  properties `workspace_root -> Path`, `state_dir -> Path` (honours env `SCIEFLOW_STATE_DIR`),
  methods `run_dir(slug: str) -> Path`, `agents() -> dict`, `defaults() -> dict`,
  `schema(name: str) -> dict`; exception `ProjectError(ValueError)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_project.py
import pytest

from scieflow.core.project import Project, ProjectError


def make_repo(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "status.yml").write_text("phases: [a]\n")
    return tmp_path


def test_discover_from_a_start_path_without_chdir(tmp_path):
    root = make_repo(tmp_path)
    (root / "deep" / "er").mkdir(parents=True)
    project = Project.discover(root / "deep" / "er")
    assert project.root == root.resolve()
    assert project.workspace_root == root.resolve() / "workspace"


def test_loaders_read_the_projects_own_files(tmp_path):
    project = Project(make_repo(tmp_path))
    assert project.agents() == {}
    assert project.defaults()["approval"] == "per-campaign"
    assert project.schema("status") == {"phases": ["a"]}


@pytest.mark.parametrize("slug", ["../escape", "a/b", "", "/abs"])
def test_run_dir_refuses_paths_that_are_not_a_slug(tmp_path, slug):
    with pytest.raises(ProjectError):
        Project(make_repo(tmp_path)).run_dir(slug)


def test_run_dir_accepts_workspace_prefix_and_spaces(tmp_path):
    project = Project(make_repo(tmp_path))
    assert project.run_dir("workspace/2026-01-x/") == project.workspace_root / "2026-01-x"
    assert project.run_dir("2026-08-a copy").name == "2026-08-a copy"


def test_state_dir_honours_the_environment(tmp_path, monkeypatch):
    project = Project(make_repo(tmp_path))
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(tmp_path / "elsewhere"))
    assert project.state_dir == tmp_path / "elsewhere"
    monkeypatch.delenv("SCIEFLOW_STATE_DIR")
    assert project.state_dir == project.root / ".scieflow"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_project.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scieflow.core.project'`

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/core/project.py
"""A ScieFlow project: the repo root and everything derived from it.

`config.repo_root()` reads the current directory, which a server handling
requests — or a test — cannot rely on. New code takes a `Project` instead;
the CLI builds one with `Project.discover()`, tests with `Project(tmp_path)`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from scieflow.core import config

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")


class ProjectError(ValueError):
    """A path or name that does not belong to this project."""


@dataclass(frozen=True)
class Project:
    root: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> "Project":
        return cls(config.repo_root(start).resolve())

    @property
    def workspace_root(self) -> Path:
        return self.root / "workspace"

    @property
    def state_dir(self) -> Path:
        """Project-level runtime state (jobs outside any run). Gitignored."""
        override = os.environ.get("SCIEFLOW_STATE_DIR")
        return Path(override) if override else self.root / ".scieflow"

    def run_dir(self, slug: str) -> Path:
        cleaned = slug.removeprefix("workspace/").rstrip("/")
        if not SLUG_RE.match(cleaned) or ".." in cleaned:
            raise ProjectError(f"not a run slug: {slug!r}")
        return self.workspace_root / cleaned

    def agents(self) -> dict:
        return config.load_agents(self.root)

    def defaults(self) -> dict:
        return config.load_defaults(self.root)

    def schema(self, name: str) -> dict:
        return yaml.safe_load((self.root / "schemas" / f"{name}.yml").read_text())
```

```python
# tests/conftest.py
"""Suite-wide isolation: jobs recorded outside any run go to a temp dir,
never into the real repository's .scieflow/."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(tmp_path_factory.mktemp("state")))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_project.py -v && uv run pytest -q`
Expected: 7 passed; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/project.py tests/core/test_project.py tests/conftest.py
git commit -m "feat(core): explicit Project context instead of the current directory

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Safe state files and ids

**Files:**
- Create: `src/scieflow/core/store.py`
- Modify: `pyproject.toml` (core `dependencies`), `.gitignore`
- Test: `tests/core/test_store.py`

**Interfaces:**
- Produces: `new_id() -> str` (26-char time-sortable ULID); `locked(path) -> contextmanager`;
  `write_text(path, text)`; `write_yaml(path, data)`; `read_yaml(path, default=None)`;
  `update_yaml(path, fn: Callable[[dict], dict]) -> dict`; `append_jsonl(path, record: dict)`;
  `read_jsonl(path) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_store.py
import multiprocessing
import time

import yaml

from scieflow.core import store


def test_ids_are_unique_sortable_and_26_chars():
    ids = [store.new_id() for _ in range(200)]
    assert len(set(ids)) == 200
    assert all(len(i) == 26 for i in ids)
    first = store.new_id()
    time.sleep(0.005)
    assert store.new_id() > first


def test_write_yaml_is_atomic_and_readable(tmp_path):
    path = tmp_path / "s" / "status.yml"
    store.write_yaml(path, {"a": 1, "b": [1, 2]})
    assert store.read_yaml(path) == {"a": 1, "b": [1, 2]}
    assert not list(path.parent.glob(".status.yml.*.tmp"))


def test_read_yaml_default_when_missing(tmp_path):
    assert store.read_yaml(tmp_path / "nope.yml", {"x": 0}) == {"x": 0}


def _bump(path_str, times):
    from pathlib import Path

    from scieflow.core import store as s

    for _ in range(times):
        s.update_yaml(Path(path_str), lambda d: {**d, "n": d.get("n", 0) + 1})


def test_concurrent_updates_never_lose_or_corrupt(tmp_path):
    path = tmp_path / "counter.yml"
    store.write_yaml(path, {"n": 0})
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_bump, args=(str(path), 25)) for _ in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert yaml.safe_load(path.read_text()) == {"n": 150}


def test_jsonl_append_and_read_skip_garbage(tmp_path):
    path = tmp_path / "events.jsonl"
    store.append_jsonl(path, {"a": 1})
    with path.open("a") as fh:
        fh.write("not json\n\n")
    store.append_jsonl(path, {"a": 2})
    assert store.read_jsonl(path) == [{"a": 1}, {"a": 2}]
    assert store.read_jsonl(tmp_path / "missing.jsonl") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scieflow.core.store'`

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/core/store.py
"""Safe state files: locked, atomic writes and appends, plus run ids.

Every writer in core used plain `write_text`, so a browser request and an agent
in a terminal writing the same status.yml could interleave and truncate it.
These helpers take a sibling `<file>.lock` and replace the file atomically.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import yaml
from filelock import FileLock

LOCK_TIMEOUT = 30
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id() -> str:
    """A ULID: 48-bit millisecond timestamp + 80 random bits, Crockford base32.

    Sorts by creation time, so ids double as an ordering for events and jobs.
    """
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


@contextmanager
def locked(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=LOCK_TIMEOUT):
        yield


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    with locked(path):
        _atomic_write(path, text)


def write_yaml(path: Path, data) -> None:
    write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def read_yaml(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return yaml.safe_load(path.read_text())


def update_yaml(path: Path, fn: Callable[[dict], dict]) -> dict:
    """Read, change and write under one lock — no lost updates."""
    path = Path(path)
    with locked(path):
        current = yaml.safe_load(path.read_text()) if path.exists() else {}
        updated = fn(current or {})
        _atomic_write(path, yaml.safe_dump(updated, sort_keys=False, allow_unicode=True))
    return updated


def append_jsonl(path: Path, record: dict) -> None:
    path = Path(path)
    with locked(path):
        with path.open("a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
```

In `pyproject.toml`, change the core dependency line to:

```toml
dependencies = ["pyyaml>=6", "click>=8.1", "ruamel.yaml>=0.18", "questionary>=2.0", "filelock>=3.12"]
```

Append to `.gitignore` (`uv.lock` is tracked, so never a bare `*.lock`):

```gitignore
# Runtime state and lock files written by scieflow.core.store / jobs
.scieflow/
*.yml.lock
*.yaml.lock
*.json.lock
*.jsonl.lock
```

Then `uv lock -q && uv sync --all-extras -q`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_store.py -v && uv run pytest -q`
Expected: 5 passed; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/store.py tests/core/test_store.py pyproject.toml uv.lock .gitignore
git commit -m "feat(core): locked atomic state writes, JSONL log helpers, ULID ids

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Run lifecycle moves into the package

**Files:**
- Create: `src/scieflow/core/run/__init__.py`, `status.py`, `budget.py`, `checkpoint.py`, `init.py`, `validate.py`
- Modify (become shims): `scripts/status.py`, `scripts/budget.py`, `scripts/checkpoint.py`, `scripts/sfx_init.py`, `scripts/validate.py`
- Test: `tests/core/test_run_package.py` (existing `tests/test_status.py`, `test_budget.py`, `test_checkpoint.py`, `test_sfx_init.py`, `test_validate.py`, `test_dry_run.py` must pass unchanged)

**Interfaces:**
- Consumes: `store.write_yaml/read_yaml/update_yaml/new_id`, `Project.discover().schema(name)`.
- Produces: `scieflow.core.run.status` with the old API (`new_status, read_status, write_status, mark, next_pending, advance_iteration, stop, clear_stop`, module attributes `PHASES, STATES, STOP_REASONS, APPROVAL_MODES`) plus `vocab() -> dict` and `ensure_id(ws: Path) -> str`; `new_status()` now includes `"id"`.
  `scieflow.core.run.budget` (old API). `scieflow.core.run.checkpoint` (`checkpoint, resume_info, main`).
  `scieflow.core.run.init` (`init_workspace(slug, goal_file, workspace_root, overrides, root) -> Path`, `main`).
  `scieflow.core.run.validate` (`validate_status, validate_notebook_entry, validate_manifest, validate_claim_audit, main`).

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_run_package.py
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_importing_the_package_does_no_io(tmp_path):
    # A fresh interpreter outside any repo: importing must not look for one.
    done = subprocess.run([sys.executable, "-c", "import scieflow.core.run.status"],
                          cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_new_status_carries_a_stable_id():
    from scieflow.core.run import status

    st = status.new_status("run-a", "autonomous")
    assert len(st["id"]) == 26
    assert status.PHASES == list(st["phases"])


def test_ensure_id_adds_one_to_an_old_run_and_keeps_it(tmp_path):
    from scieflow.core.run import status

    old = status.new_status("old", "per-campaign")
    del old["id"]
    status.write_status(tmp_path, old)
    first = status.ensure_id(tmp_path)
    assert status.ensure_id(tmp_path) == first
    assert status.read_status(tmp_path)["id"] == first


def test_legacy_imports_are_the_package_modules():
    sys.path.insert(0, str(ROOT / "scripts"))
    import budget
    import checkpoint
    import status

    from scieflow.core.run import budget as b2, checkpoint as c2, status as s2

    assert status is s2 and budget is b2 and checkpoint is c2


def test_legacy_scripts_still_run_as_commands(tmp_path):
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate.py"), "--help"],
                          capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0 and "--schema" in done.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_run_package.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scieflow.core.run'`

- [ ] **Step 3: Write the implementation**

`src/scieflow/core/run/__init__.py`:

```python
"""Run lifecycle: status, budget, checkpoint, workspace init, validation, actions."""
```

`src/scieflow/core/run/status.py`:

```python
"""Run state: workspace/<slug>/status.yml transitions and resumability.

The vocabulary (phases, states, stop reasons, approval modes) is data in
schemas/status.yml, loaded on first use — importing this module does no I/O.
`PHASES`, `STATES`, `STOP_REASONS` and `APPROVAL_MODES` stay available as
module attributes for existing callers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import store
from scieflow.core.project import Project

_VOCAB: dict | None = None


def vocab() -> dict:
    global _VOCAB
    if _VOCAB is None:
        _VOCAB = Project.discover().schema("status")
    return _VOCAB


def __getattr__(name: str):
    if name == "PHASES":
        return list(vocab()["phases"])
    if name == "STATES":
        return set(vocab()["states"])
    if name == "STOP_REASONS":
        return set(vocab()["stop_reasons"])
    if name == "APPROVAL_MODES":
        return set(vocab()["approval_modes"])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_status(run: str, approval: str) -> dict:
    if approval not in vocab()["approval_modes"]:
        raise ValueError(f"approval must be one of {sorted(vocab()['approval_modes'])}")
    return {
        "run": run,
        "id": store.new_id(),
        "created": _now(),
        "approval": approval,
        "iteration": 1,
        "phases": {p: "pending" for p in vocab()["phases"]},
        "stopped": None,
    }


def read_status(ws: Path) -> dict:
    return store.read_yaml(Path(ws) / "status.yml")


def write_status(ws: Path, st: dict) -> None:
    store.write_yaml(Path(ws) / "status.yml", st)


def ensure_id(ws: Path) -> str:
    """Give an existing run a stable id the first time it is needed."""
    def add(st: dict) -> dict:
        st.setdefault("id", store.new_id())
        return st
    return store.update_yaml(Path(ws) / "status.yml", add)["id"]


def mark(st: dict, phase: str, state: str) -> dict:
    if phase not in vocab()["phases"]:
        raise ValueError(f"unknown phase: {phase}")
    if state not in vocab()["states"]:
        raise ValueError(f"unknown state: {state}")
    st["phases"][phase] = state
    return st


def next_pending(st: dict) -> str | None:
    """First phase of the current iteration not marked done; None if all done."""
    for p in vocab()["phases"]:
        if st["phases"][p] != "done":
            return p
    return None


def advance_iteration(st: dict) -> dict:
    if next_pending(st) is not None:
        raise ValueError("cannot advance: current iteration has unfinished phases")
    st["iteration"] += 1
    st["phases"] = {p: "pending" for p in vocab()["phases"]}
    return st


def stop(st: dict, reason: str, detail: str = "", resume: str = "") -> dict:
    if reason not in vocab()["stop_reasons"]:
        raise ValueError(f"unknown stop reason: {reason}")
    st["stopped"] = {"reason": reason, "at": _now(), "detail": detail, "resume": resume}
    return st


def clear_stop(st: dict) -> dict:
    """Resume: remove the stopped block recorded by stop()/checkpoint."""
    st["stopped"] = None
    return st
```

`src/scieflow/core/run/budget.py` — copy `scripts/budget.py` verbatim, then replace its two I/O
functions and add one query:

```python
from scieflow.core import store


def read_budget(ws: Path) -> dict:
    return store.read_yaml(Path(ws) / "budget.yml")


def write_budget(ws: Path, b: dict) -> None:
    store.write_yaml(Path(ws) / "budget.yml", b)


def is_exhausted(b: dict, dim: str) -> bool:
    cap = b["budgets"][DIMENSIONS[dim]]
    return bool(cap) and b["spent"][dim] >= cap
```

(`import yaml` is no longer needed there; keep `DIMENSIONS`, `new_budget`, `record`,
`set_wall_from_clock`, `remaining_fraction`, `low_dimensions`, `exhausted` unchanged.)

`src/scieflow/core/run/checkpoint.py` — `scripts/checkpoint.py` with the imports and `main`
adapted:

```python
"""Graceful stop: record stop reason + resume instructions in status.yml.

usage: checkpoint.py <workspace> --reason REASON [--detail TEXT]
"""

import argparse
from pathlib import Path

from scieflow.core.run import status as status_mod


def checkpoint(ws: Path, reason: str, detail: str = "") -> dict:
    st = status_mod.read_status(ws)
    pending = status_mod.next_pending(st)
    resume = (
        f"Resume: read status.yml; continue at iteration {st['iteration']}, "
        f"phase '{pending or 'advance-iteration'}'. Phase artifacts are in "
        f"iterations/{st['iteration']}/; budget ledger in budget.yml."
    )
    st = status_mod.stop(st, reason, detail, resume)
    status_mod.write_status(ws, st)
    return st


def resume_info(ws: Path) -> str:
    st = status_mod.read_status(ws)
    if st.get("stopped"):
        return st["stopped"]["resume"]
    pending = status_mod.next_pending(st)
    return (f"Run active: iteration {st['iteration']}, "
            f"next phase '{pending or 'advance-iteration'}'.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="checkpoint.py")
    ap.add_argument("workspace", type=Path)
    ap.add_argument("--reason", required=True,
                    choices=sorted(status_mod.vocab()["stop_reasons"]))
    ap.add_argument("--detail", default="")
    args = ap.parse_args(argv)
    st = checkpoint(args.workspace, args.reason, args.detail)
    print(st["stopped"]["resume"])
```

`src/scieflow/core/run/init.py` — `scripts/sfx_init.py` with these import changes (body of
`init_workspace` unchanged; it already calls `status_mod.write_status` / `budget_mod.write_budget`,
which now go through the store):

```python
from scieflow.core import config
from scieflow.core.run import budget as budget_mod
from scieflow.core.run import status as status_mod
```

and in `main()` replace `choices=sorted(status_mod.APPROVAL_MODES)` with
`choices=sorted(status_mod.vocab()["approval_modes"])`, and give `main` an `argv=None`
parameter passed to `ap.parse_args(argv)`.

`src/scieflow/core/run/validate.py` — `scripts/validate.py` verbatim except:

```python
from scieflow.core.project import Project


def _schema(name: str) -> dict:
    return Project.discover().schema(name)
```

and `main(argv=None)` passing `argv` to `ap.parse_args`.

Shims. `scripts/status.py` and `scripts/budget.py` (library modules):

```python
"""Moved to scieflow.core.run.status in M1. Kept so `import status` and old
chats keep working: this module *is* the package module."""

import sys

from scieflow.core.run import status as _impl

sys.modules[__name__] = _impl
```

(`scripts/budget.py`: same with `budget`.) `scripts/checkpoint.py`, `scripts/sfx_init.py`,
`scripts/validate.py` (also run as commands):

```python
#!/usr/bin/env python3
"""Moved to scieflow.core.run.checkpoint in M1; kept for `uv run scripts/checkpoint.py`
and `import checkpoint`."""

import sys

from scieflow.core.run import checkpoint as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
```

(`sfx_init.py` aliases `scieflow.core.run.init`; `validate.py` aliases `scieflow.core.run.validate`.)

The two other core writers move onto the store in the same task (spec §1.3). In
`scripts/remote/jobs.py`:

```python
from scieflow.core import store


def save_jobs(workspace: Path, jobs: list) -> None:
    store.write_yaml(_path(workspace), jobs)
```

(`_path(workspace)` is the module's existing `workspace / "remote" / "jobs.yml"` helper;
`store.write_yaml` creates the parent directory, so the `mkdir` line goes.)
In `src/scieflow/core/agent_configure.py`:

```python
def write(plan: Plan) -> None:
    for change in plan.changes:
        store.write_text(change.path, change.after)
```

with `from scieflow.core import store` (and `import os` removed if nothing else uses it).
`tests/test_remote_*.py` and `tests/core/test_configure*.py` must still pass unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_run_package.py tests/test_status.py tests/test_budget.py tests/test_checkpoint.py tests/test_sfx_init.py tests/test_validate.py tests/test_dry_run.py -v && uv run pytest -q && scripts/check_legacy.sh`
Expected: all pass; full suite green; every legacy check `ok`.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/run scripts/status.py scripts/budget.py scripts/checkpoint.py scripts/sfx_init.py scripts/validate.py scripts/remote/jobs.py src/scieflow/core/agent_configure.py tests/core/test_run_package.py
git commit -m "refactor(core): run lifecycle moves into scieflow.core.run; scripts become aliases

status/budget/checkpoint/init/validate are importable package modules with no
import-time I/O; new runs get a stable id; state files go through the store.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Event log, run actions, `scieflow run`

**Files:**
- Create: `src/scieflow/core/events.py`, `src/scieflow/core/run/actions.py`, `src/scieflow/core/run/cli.py`
- Modify: `src/scieflow/core/run/init.py` (emit `run.created`), `src/scieflow/cli.py` (`GROUPS["run"]`)
- Test: `tests/core/test_events.py`, `tests/core/test_run_actions.py`

**Interfaces:**
- Consumes: `store.*`, `run.status`, `run.budget`, `run.checkpoint`.
- Produces:
  - `events.emit(ws: Path, type_: str, actor: str = "system", **data) -> dict`,
    `events.read(ws, since: str | None = None, types: tuple[str, ...] = ()) -> list[dict]`,
    `events.follow(ws, since=None, poll=1.0, stop=lambda: False) -> Iterator[dict]`,
    `events.TYPES: frozenset[str]`, `events.ACTORS`. Event = `{id, ts, run, slug, type, actor, data}`.
  - `actions.BudgetExhausted(RuntimeError)` with `.dims: list[str]`;
    `actions.mark_phase(ws, phase, state, actor="agent") -> dict`;
    `actions.advance_iteration(ws, actor="agent") -> dict` (raises `BudgetExhausted`);
    `actions.checkpoint_run(ws, reason, detail="", actor="agent") -> dict`;
    `actions.resume(ws, actor="agent") -> dict`;
    `actions.record_spend(ws, actor="system", **spent) -> dict | None` (no-op without budget.yml);
    `actions.guard_budget(ws, dims: tuple[str, ...]) -> None` (raises `BudgetExhausted`, checkpoints once);
    `actions.run_for_path(path: Path) -> Path | None`.
  - CLI: `scieflow run init|show|mark|advance|checkpoint|resume|events|log`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_events.py
import pytest

from scieflow.core import events
from scieflow.core.run import status


def make_run(tmp_path):
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return ws


def test_emit_appends_structured_events_with_the_run_id(tmp_path):
    ws = make_run(tmp_path)
    e = events.emit(ws, "phase.started", "agent", phase="hypothesize")
    assert e["run"] == status.read_status(ws)["id"] and e["slug"] == "r1"
    assert events.read(ws) == [e]


def test_unknown_types_are_refused_but_notes_are_free(tmp_path):
    ws = make_run(tmp_path)
    with pytest.raises(ValueError):
        events.emit(ws, "phase.strated")
    assert events.emit(ws, "note.idea", "agent", text="x")["type"] == "note.idea"


def test_read_since_returns_only_later_events_in_file_order(tmp_path):
    ws = make_run(tmp_path)
    a = events.emit(ws, "note.a")
    b = events.emit(ws, "note.b")
    c = events.emit(ws, "note.c")
    assert [e["id"] for e in events.read(ws, since=a["id"])] == [b["id"], c["id"]]
    assert [e["type"] for e in events.read(ws, types=("note.b",))] == ["note.b"]
    assert [e["type"] for e in events.read(ws, types=("note.*",))] == ["note.a", "note.b", "note.c"]


def test_follow_yields_new_events_then_stops(tmp_path):
    ws = make_run(tmp_path)
    events.emit(ws, "note.first")
    seen = []
    for e in events.follow(ws, poll=0.01, stop=lambda: len(seen) >= 1):
        seen.append(e)
    assert [e["type"] for e in seen] == ["note.first"]
```

```python
# tests/core/test_run_actions.py
from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.run import actions, budget, status


def make_loop_run(tmp_path, **caps):
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(caps.get("it", 2), caps.get("runs", 5),
                                              caps.get("wall", 60)))
    return ws


def test_mark_phase_writes_status_and_logs_an_event(tmp_path):
    ws = make_loop_run(tmp_path)
    actions.mark_phase(ws, "hypothesize", "running")
    actions.mark_phase(ws, "hypothesize", "done")
    assert status.read_status(ws)["phases"]["hypothesize"] == "done"
    assert [e["type"] for e in events.read(ws)] == ["phase.started", "phase.done"]


def test_advance_refused_when_iterations_are_spent(tmp_path):
    ws = make_loop_run(tmp_path, it=1)
    for phase in status.PHASES:
        actions.mark_phase(ws, phase, "done")
    actions.record_spend(ws, iterations=1)
    with pytest.raises(actions.BudgetExhausted) as exc:
        actions.advance_iteration(ws)
    assert exc.value.dims == ["iterations"]
    assert status.read_status(ws)["stopped"]["reason"] == "low-budget"


def test_advance_allowed_within_budget(tmp_path):
    ws = make_loop_run(tmp_path, it=2)
    for phase in status.PHASES:
        actions.mark_phase(ws, phase, "done")
    actions.record_spend(ws, iterations=1)
    assert actions.advance_iteration(ws)["iteration"] == 2


def test_guard_budget_checkpoints_once_and_refuses(tmp_path):
    ws = make_loop_run(tmp_path, wall=1)
    actions.record_spend(ws, wall_minutes=1.5)
    for _ in range(2):
        with pytest.raises(actions.BudgetExhausted):
            actions.guard_budget(ws, ("wall_minutes",))
    types = [e["type"] for e in events.read(ws)]
    assert types.count("checkpoint") == 1 and types.count("job.refused") == 2


def test_guard_budget_ignores_other_dimensions_and_research_runs(tmp_path):
    ws = make_loop_run(tmp_path, it=1)
    actions.record_spend(ws, iterations=1)
    actions.guard_budget(ws, ("wall_minutes",))  # iterations spent: not this guard's job
    research = tmp_path / "workspace" / "lit"
    research.mkdir()
    actions.guard_budget(research, ("wall_minutes",))  # no budget.yml: nothing to guard


def test_run_for_path_finds_the_enclosing_run(tmp_path):
    ws = make_loop_run(tmp_path)
    assert actions.run_for_path(ws / "logs" / "p.md") == ws
    assert actions.run_for_path(tmp_path) is None


def test_run_cli_mark_events_and_log(tmp_path, monkeypatch):
    from scieflow.core.run.cli import run as run_group

    ws = make_loop_run(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "schemas").mkdir()
    real = Path(__file__).resolve().parents[2] / "schemas" / "status.yml"
    (tmp_path / "schemas" / "status.yml").write_text(real.read_text())
    monkeypatch.chdir(tmp_path)
    cli = CliRunner()
    assert cli.invoke(run_group, ["mark", "r1", "hypothesize", "running"]).exit_code == 0
    assert cli.invoke(run_group, ["log", "r1", "note.idea", "--message", "try sigma 2"]).exit_code == 0
    out = cli.invoke(run_group, ["events", "r1", "--json"])
    assert out.exit_code == 0, out.output
    assert '"note.idea"' in out.output and '"phase.started"' in out.output
    assert cli.invoke(run_group, ["log", "r1", "phase.done"]).exit_code != 0  # agents log notes only
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_events.py tests/core/test_run_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scieflow.core.events'`

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/core/events.py
"""Per-run event log — workspace/<slug>/events.jsonl, one JSON object per line.

status.yml is the snapshot; events are the history: what happened, when, and
who did it. Append-only under a lock, so the web app, the CLI and agents can
all write at once. A dashboard replays it; `scieflow run events --follow`
tails it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import store

EVENTS_FILE = "events.jsonl"
ACTORS = frozenset({"human", "agent", "system"})
TYPES = frozenset({
    "run.created", "run.resumed",
    "phase.pending", "phase.started", "phase.done", "phase.failed",
    "iteration.advanced", "checkpoint", "budget.recorded",
    "job.queued", "job.started", "job.finished", "job.failed", "job.timeout",
    "job.cancelled", "job.lost", "job.refused",
    "gate.opened", "gate.answered", "gate.withdrawn",
    "integration.call", "sync.pushed", "sync.pulled",
})


def _run_id(ws: Path) -> str:
    st = store.read_yaml(Path(ws) / "status.yml") or {}
    return str(st.get("id") or Path(ws).name)


def emit(ws: Path, type_: str, actor: str = "system", **data) -> dict:
    if type_ not in TYPES and not type_.startswith("note."):
        raise ValueError(f"unknown event type {type_!r} (free-form events use 'note.<name>')")
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r} (one of {', '.join(sorted(ACTORS))})")
    event = {
        "id": store.new_id(),
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "run": _run_id(ws),
        "slug": Path(ws).name,
        "type": type_,
        "actor": actor,
        "data": data,
    }
    store.append_jsonl(Path(ws) / EVENTS_FILE, event)
    return event


def _matches(event_type: str, types: tuple[str, ...]) -> bool:
    return any(event_type == t or (t.endswith("*") and event_type.startswith(t[:-1]))
               for t in types)


def read(ws: Path, since: str | None = None, types: tuple[str, ...] = ()) -> list[dict]:
    """Events in file order; `since` = an event id, returns only what came after it."""
    evs = store.read_jsonl(Path(ws) / EVENTS_FILE)
    if since is not None:
        ids = [e.get("id") for e in evs]
        if since in ids:
            evs = evs[ids.index(since) + 1:]
    if types:
        evs = [e for e in evs if _matches(e.get("type", ""), types)]
    return evs


def follow(ws: Path, since: str | None = None, poll: float = 1.0,
           stop: Callable[[], bool] = lambda: False) -> Iterator[dict]:
    last = since
    while not stop():
        for event in read(ws, since=last):
            last = event["id"]
            yield event
            if stop():
                return
        time.sleep(poll)
```

```python
# src/scieflow/core/run/actions.py
"""Run actions: status and budget changes that are also recorded as events.

The functions in status.py and budget.py stay pure; these read, change, write
(under one lock) and log. The CLI, the web app and agents all change runs
through here — and budgets are enforced here, in code, rather than by an
agent remembering to check.
"""

from __future__ import annotations

from pathlib import Path

from scieflow.core import events, store
from scieflow.core.run import budget, status


class BudgetExhausted(RuntimeError):
    def __init__(self, dims: list[str]):
        super().__init__(f"budget exhausted: {', '.join(dims)}")
        self.dims = dims


def _update_status(ws: Path, fn) -> dict:
    return store.update_yaml(Path(ws) / "status.yml", fn)


def mark_phase(ws: Path, phase: str, state: str, actor: str = "agent") -> dict:
    st = _update_status(ws, lambda s: status.mark(s, phase, state))
    kind = "phase.started" if state == "running" else f"phase.{state}"
    events.emit(ws, kind, actor, phase=phase, iteration=st["iteration"])
    return st


def checkpoint_run(ws: Path, reason: str, detail: str = "", actor: str = "agent") -> dict:
    from scieflow.core.run import checkpoint

    st = checkpoint.checkpoint(Path(ws), reason, detail)
    events.emit(ws, "checkpoint", actor, reason=reason, detail=detail)
    return st


def resume(ws: Path, actor: str = "agent") -> dict:
    st = _update_status(ws, status.clear_stop)
    events.emit(ws, "run.resumed", actor)
    return st


def record_spend(ws: Path, actor: str = "system", **spent) -> dict | None:
    path = Path(ws) / "budget.yml"
    if not path.exists():
        return None
    b = store.update_yaml(path, lambda cur: budget.record(cur, **spent))
    events.emit(ws, "budget.recorded", actor, **spent)
    return b


def _refuse(ws: Path, dims: list[str], detail: str) -> None:
    st = status.read_status(ws) or {}
    if not st.get("stopped"):
        checkpoint_run(ws, "low-budget", detail, actor="system")
    events.emit(ws, "job.refused", "system", reason="budget", dims=dims)
    raise BudgetExhausted(dims)


def guard_budget(ws: Path, dims: tuple[str, ...]) -> None:
    """Refuse work that would spend an exhausted dimension; checkpoint once."""
    path = Path(ws) / "budget.yml"
    if not path.exists():
        return
    b = store.read_yaml(path)
    out = [d for d in dims if budget.is_exhausted(b, d)]
    if out:
        _refuse(ws, out, f"budget exhausted: {', '.join(out)}")


def advance_iteration(ws: Path, actor: str = "agent") -> dict:
    path = Path(ws) / "budget.yml"
    if path.exists() and budget.is_exhausted(store.read_yaml(path), "iterations"):
        _refuse(ws, ["iterations"], "iterations exhausted")
    st = _update_status(ws, status.advance_iteration)
    events.emit(ws, "iteration.advanced", actor, iteration=st["iteration"])
    return st


def run_for_path(path: Path) -> Path | None:
    """The run workspace (workspace/<slug>/ with a status.yml) containing `path`."""
    p = Path(path).resolve()
    for candidate in [p, *p.parents]:
        if candidate.parent.name == "workspace" and (candidate / "status.yml").exists():
            return candidate
    return None
```

In `src/scieflow/core/run/init.py`, after `(ws / "notebook.md").write_text(...)` inside the
`try:` add:

```python
        from scieflow.core import events

        events.emit(ws, "run.created", "system", approval=cfg["approval"])
```

```python
# src/scieflow/core/run/cli.py
"""`scieflow run` — a run's state, history and lifecycle from the command line.

Agents use these instead of editing status.yml or budget.yml by hand, so every
change is locked, validated and recorded as an event.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from scieflow.core import events
from scieflow.core.project import Project, ProjectError
from scieflow.core.run import actions, budget, status


def _ws(slug: str) -> Path:
    try:
        ws = Project.discover().run_dir(slug)
    except ProjectError as e:
        raise click.ClickException(str(e)) from e
    if not (ws / "status.yml").exists():
        raise click.ClickException(f"no run workspace/{slug} (status.yml missing)")
    return ws


def _actor(as_agent: bool) -> str:
    return "agent" if as_agent else "human"


AGENT_FLAG = click.option("--as-agent", is_flag=True,
                          help="Record the change as made by an agent (agents must pass this).")


@click.group()
def run():
    """A run's state, history and lifecycle."""


@run.command()
@click.argument("slug")
@click.option("--goal", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--approval", type=click.Choice(["per-campaign", "autonomous"]))
@click.option("--max-iterations", type=int)
@click.option("--max-experiment-runs", type=int)
@click.option("--max-wall-minutes", type=int)
def init(slug, goal, approval, max_iterations, max_experiment_runs, max_wall_minutes):
    """Create a research-loop run workspace."""
    from scieflow.core.run.init import init_workspace

    project = Project.discover()
    try:
        ws = init_workspace(slug, goal, project.workspace_root,
                            {"approval": approval, "max_iterations": max_iterations,
                             "max_experiment_runs": max_experiment_runs,
                             "max_wall_minutes": max_wall_minutes}, project.root)
    except FileExistsError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"initialized {ws}")


@run.command()
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True)
def show(slug, as_json):
    """Status, budget and what to do next."""
    ws = _ws(slug)
    st = status.read_status(ws)
    b = budget.read_budget(ws) if (ws / "budget.yml").exists() else None
    data = {"status": st, "budget": b,
            "remaining": budget.remaining_fraction(b) if b else None}
    if as_json:
        click.echo(json.dumps(data, indent=2, default=str))
        return
    click.echo(f"{slug}  id={st.get('id', '-')}  iteration {st.get('iteration', '-')}")
    for phase, state in (st.get("phases") or {}).items():
        click.echo(f"  {phase:<14} {state}")
    if st.get("stopped"):
        click.echo(f"  stopped: {st['stopped']['reason']} — {st['stopped'].get('resume', '')}")
    if b:
        for dim, frac in budget.remaining_fraction(b).items():
            click.echo(f"  budget {dim:<16} {frac:6.0%} left")


@run.command()
@click.argument("slug")
@click.argument("phase")
@click.argument("state")
@AGENT_FLAG
def mark(slug, phase, state, as_agent):
    """Set a phase's state (pending/running/done/failed)."""
    try:
        actions.mark_phase(_ws(slug), phase, state, _actor(as_agent))
    except ValueError as e:
        raise click.ClickException(str(e)) from e


@run.command()
@click.argument("slug")
@AGENT_FLAG
def advance(slug, as_agent):
    """Start the next iteration (refused when the iteration budget is spent)."""
    try:
        st = actions.advance_iteration(_ws(slug), _actor(as_agent))
    except actions.BudgetExhausted as e:
        raise click.ClickException(f"{e} — run checkpointed") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"iteration {st['iteration']}")


@run.command()
@click.argument("slug")
@click.option("--reason", required=True,
              type=click.Choice(["low-budget", "max-iterations", "converged", "anomaly", "user"]))
@click.option("--detail", default="")
@AGENT_FLAG
def checkpoint(slug, reason, detail, as_agent):
    """Stop the run gracefully with resume instructions."""
    st = actions.checkpoint_run(_ws(slug), reason, detail, _actor(as_agent))
    click.echo(st["stopped"]["resume"])


@run.command()
@click.argument("slug")
@AGENT_FLAG
def resume(slug, as_agent):
    """Clear a stop so the run can continue."""
    actions.resume(_ws(slug), _actor(as_agent))
    click.echo("resumed")


@run.command("events")
@click.argument("slug")
@click.option("--since", help="Only events after this event id.")
@click.option("--type", "types", multiple=True, help="Filter by type; 'job.*' matches a prefix.")
@click.option("--follow", is_flag=True, help="Keep printing new events (Ctrl-C to stop).")
@click.option("--json", "as_json", is_flag=True, help="One JSON object per line.")
def events_cmd(slug, since, types, follow, as_json):
    """The run's history."""
    ws = _ws(slug)

    def show_one(e):
        if as_json:
            click.echo(json.dumps(e, default=str))
        else:
            detail = " ".join(f"{k}={v}" for k, v in e["data"].items())
            click.echo(f"{e['ts'][:19]}  {e['actor']:<6} {e['type']:<20} {detail}")

    stream = events.follow(ws, since=since) if follow else events.read(ws, since=since)
    try:
        for e in stream:
            if not types or events._matches(e["type"], types):
                show_one(e)
    except KeyboardInterrupt:
        pass


@run.command("log")
@click.argument("slug")
@click.argument("type_")
@click.option("--message", default="")
@click.option("--data", "pairs", multiple=True, metavar="KEY=VALUE")
def log_cmd(slug, type_, message, pairs):
    """Record a free-form 'note.<name>' event (structured alternative to log.md)."""
    if not type_.startswith("note."):
        raise click.ClickException("agents log free-form events as 'note.<name>'")
    data = dict(p.split("=", 1) for p in pairs if "=" in p)
    if message:
        data["message"] = message
    events.emit(_ws(slug), type_, "agent", **data)
```

In `src/scieflow/cli.py` `GROUPS`, add after `"agent"`:

```python
    "run": ("scieflow.core.run.cli", "run", None,
            "A run's state, history and lifecycle."),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_events.py tests/core/test_run_actions.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/events.py src/scieflow/core/run/actions.py src/scieflow/core/run/cli.py src/scieflow/core/run/init.py src/scieflow/cli.py tests/core/test_events.py tests/core/test_run_actions.py
git commit -m "feat(core): per-run event log, run actions with budget guards, scieflow run

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Structured run state

**Files:**
- Create: `schemas/status-research.yml`
- Modify: `src/scieflow/core/workspace.py` (`Run`, `describe`), `src/scieflow/core/run/validate.py` (`status-research`)
- Test: `tests/core/test_workspace.py` (append)

**Interfaces:**
- Produces: `Run` gains `id: str | None`, `phase: str | None`, `phase_state: str | None`,
  `iteration: int | None`, `stopped_reason: str | None`, `updated_at: str | None` (full ISO
  timestamp, UTC). Existing fields (`slug, kind, state, updated, aliases, lineage`) unchanged.
  `validate.validate_status_research(st: dict) -> list[str]`; `--schema status-research`.

- [ ] **Step 1: Write the failing tests** (append to `tests/core/test_workspace.py`)

```python
def test_describe_gives_structured_fields_for_a_loop_run(ws):
    run = {r.slug: r for r in wsmod.list_runs()}["2026-01-loop"]
    assert run.phase == "experiment" and run.phase_state == "running"
    assert run.iteration == 2 and run.stopped_reason is None
    assert run.updated_at and "T" in run.updated_at


def test_describe_gives_structured_fields_for_a_research_run(ws):
    run = {r.slug: r for r in wsmod.list_runs()}["2026-01-lit"]
    assert run.kind == "lit-review" and run.phase == "report"


def test_research_status_validation(tmp_path):
    from scieflow.core.run import validate

    good = {"workflow": "lit-review", "slug": "x", "phase": "search",
            "phases": {"brief": "done", "search": "running"}}
    assert validate.validate_status_research(good) == []
    bad = {"workflow": "lit-review", "phases": {"nonsense": "done", "search": "exploded"}}
    errors = validate.validate_status_research(bad)
    assert any("nonsense" in e for e in errors) and any("exploded" in e for e in errors)
    assert validate.validate_status_research({"workflow": "unknown-flow"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_workspace.py -v`
Expected: FAIL with `AttributeError: 'Run' object has no attribute 'phase'`

- [ ] **Step 3: Write the implementation**

```yaml
# schemas/status-research.yml
# Research-module runs (status.yml has `workflow:`). Lenient on purpose: phases
# may hold a state string or a per-agent mapping, and extra keys are allowed.
workflows:
  lit-review: [brief, search, cross-review, synthesize, export]
  gap-discovery: [intake, literature, gap-analysis, debate, synthesize]
  paper-draft: [intake, outline, draft, cross-review, merge, verify, handoff]
  paper-review: []          # tracked by `rounds:`, no fixed phases
states: [pending, running, done, failed, skipped, skipped-quorum, incomplete, not_requested]
```

In `src/scieflow/core/run/validate.py` add:

```python
def validate_status_research(st: dict) -> list[str]:
    s = _schema("status-research")
    workflow = st.get("workflow")
    if workflow not in s["workflows"]:
        return [f"unknown workflow: {workflow!r} (known: {', '.join(s['workflows'])})"]
    errors = []
    declared = s["workflows"][workflow]
    for phase, state in (st.get("phases") or {}).items():
        if declared and phase not in declared:
            errors.append(f"unknown phase for {workflow}: {phase}")
        if isinstance(state, str) and not any(
                state == known or state.startswith(f"{known}-") for known in s["states"]):
            errors.append(f"bad state for {phase}: {state}")
    return errors
```

and in `main`: add `"status-research"` to the `--schema` choices, and
`elif args.schema == "status-research": errors = validate_status_research(yaml.safe_load(text))`.

In `src/scieflow/core/workspace.py`, extend `Run` and `describe`:

```python
@dataclass
class Run:
    slug: str
    kind: str                     # loop | lit-review | gap-discovery | … | none
    state: str
    updated: str | None
    aliases: list[str] = field(default_factory=list)
    lineage: str | None = None
    id: str | None = None
    phase: str | None = None
    phase_state: str | None = None
    iteration: int | None = None
    stopped_reason: str | None = None
    updated_at: str | None = None


def _updated_at(path: Path) -> str | None:
    stamps = [(path / n).stat().st_mtime for n in
              ("status.yml", "log.md", "notebook.md", "events.jsonl") if (path / n).exists()]
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=timezone.utc).isoformat(timespec="seconds")


def _current_phase(kind: str, status: dict) -> tuple[str | None, str | None]:
    phases = status.get("phases") or {}
    if kind == "loop":
        phase = next((p for p in LOOP_PHASES if (phases.get(p) or "pending") != "done"), None)
    else:
        phase = status.get("phase") or next(
            (p for p, s in phases.items() if isinstance(s, str) and s != "done"), None)
    state = phases.get(phase) if phase else None
    return phase, state if isinstance(state, str) else None
```

and inside `describe`, build the extra fields:

```python
    phase, phase_state = _current_phase(kind, status) if kind != "none" else (None, None)
    stopped = status.get("stopped")
    return Run(
        slug=path.name,
        kind=kind,
        state=_state(kind, status),
        updated=_updated(path),
        lineage=config.get("lineage") if isinstance(config.get("lineage"), str) else None,
        id=status.get("id"),
        phase=phase,
        phase_state=phase_state,
        iteration=status.get("iteration") if kind == "loop" else None,
        stopped_reason=(stopped.get("reason") if isinstance(stopped, dict) else stopped) or None,
        updated_at=_updated_at(path),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_workspace.py -v && uv run pytest -q`
Expected: all pass (existing `list --json` tests still see `slug`, `kind`, `aliases`); full suite green.

- [ ] **Step 5: Commit**

```bash
git add schemas/status-research.yml src/scieflow/core/workspace.py src/scieflow/core/run/validate.py tests/core/test_workspace.py
git commit -m "feat(core): structured run state and a research status schema

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Job runner

**Files:**
- Create: `src/scieflow/core/jobs.py`
- Test: `tests/core/test_jobs.py`

**Interfaces:**
- Consumes: `Project`, `store`, `events.emit`.
- Produces: `Job` dataclass (`id, kind, argv, cwd, label, run_dir, state, pid, queued, started,
  finished, exit_code, timeout_s, log, err`, property `duration_s -> float | None`);
  `STATES`, `FINAL`, `TIMEOUT_EXIT = 124`;
  `start(project, argv, *, kind, cwd, run_dir=None, label="", timeout_s=None, stdin_text=None, env=None) -> tuple[Job, subprocess.Popen]`;
  `wait(job, proc) -> Job`; `run_blocking(project, argv, **kw) -> Job`; `cancel(job) -> Job`;
  `load(path) -> Job`; `list_jobs(project, run_dir=None) -> list[Job]`; `find(project, job_id) -> Job | None`;
  `reconcile(project, run_dir=None) -> list[Job]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_jobs.py
import sys
import threading
import time

from scieflow.core import events, jobs
from scieflow.core.project import Project
from scieflow.core.run import status

PY = sys.executable


def project_and_run(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return Project(tmp_path), ws


def test_output_streams_to_disk_while_running(tmp_path):
    project, ws = project_and_run(tmp_path)
    code = "import time,sys\nprint('first', flush=True)\ntime.sleep(2)\nprint('second')"
    job, proc = jobs.start(project, [PY, "-c", code], kind="agent", cwd=tmp_path, run_dir=ws)
    deadline = time.time() + 5
    while "first" not in open(job.log).read() and time.time() < deadline:
        time.sleep(0.05)
    assert "first" in open(job.log).read()          # visible before exit
    done = jobs.wait(job, proc)
    assert done.state == "done" and done.exit_code == 0
    assert "second" in open(job.log).read()
    types = [e["type"] for e in events.read(ws)]
    assert types == ["job.queued", "job.started", "job.finished"]


def test_failure_keeps_stderr_and_exit_code(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = jobs.run_blocking(project, [PY, "-c", "import sys; sys.stderr.write('bad'); sys.exit(3)"],
                            kind="agent", cwd=tmp_path, run_dir=ws)
    assert job.state == "failed" and job.exit_code == 3
    assert open(job.err).read() == "bad"


def test_timeout_keeps_partial_output(tmp_path):
    project, ws = project_and_run(tmp_path)
    code = "import time\nprint('partial', flush=True)\ntime.sleep(60)"
    job = jobs.run_blocking(project, [PY, "-c", code], kind="agent", cwd=tmp_path,
                            run_dir=ws, timeout_s=1)
    assert job.state == "timeout" and job.exit_code == jobs.TIMEOUT_EXIT
    assert "partial" in open(job.log).read()


def test_cancel_kills_the_whole_process_group(tmp_path):
    project, ws = project_and_run(tmp_path)
    marker = tmp_path / "grandchild.pid"
    code = ("import subprocess,sys,time\n"
            f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(60)")
    job, proc = jobs.start(project, [PY, "-c", code], kind="agent", cwd=tmp_path, run_dir=ws)
    waiter = threading.Thread(target=jobs.wait, args=(job, proc))
    waiter.start()
    deadline = time.time() + 5
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    jobs.cancel(job)
    waiter.join(20)
    grandchild = int(marker.read_text())
    time.sleep(0.3)
    assert not jobs._alive(grandchild)
    assert jobs.find(project, job.id).state == "cancelled"


def test_stdin_is_fed_without_blocking(tmp_path):
    project, ws = project_and_run(tmp_path)
    big = "x" * 300_000   # larger than a pipe buffer
    job = jobs.run_blocking(project, [PY, "-c", "import sys; print(len(sys.stdin.read()))"],
                            kind="agent", cwd=tmp_path, run_dir=ws, stdin_text=big, timeout_s=30)
    assert open(job.log).read().strip() == "300000"


def test_jobs_outside_a_run_go_to_the_state_dir(tmp_path, monkeypatch):
    project, _ = project_and_run(tmp_path)
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(tmp_path / "state"))
    job = jobs.run_blocking(project, [PY, "-c", "print(1)"], kind="agent", cwd=tmp_path)
    assert job.log.startswith(str(tmp_path / "state" / "jobs"))
    assert [j.id for j in jobs.list_jobs(project)] == [job.id]


def test_reconcile_marks_dead_running_jobs_lost(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = jobs.run_blocking(project, [PY, "-c", "print(1)"], kind="agent", cwd=tmp_path, run_dir=ws)
    job.state, job.pid = "running", 999_999_999    # a pid that does not exist
    jobs.save(job)
    lost = jobs.reconcile(project, ws)
    assert [j.id for j in lost] == [job.id]
    assert jobs.find(project, job.id).state == "lost"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scieflow.core.jobs'`

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/core/jobs.py
"""Job runner: every long-running thing is a tracked, cancellable job.

A job is a subprocess in its own process group, with stdout and stderr streamed
to files while it runs and a JSON record of what it is and what state it is in.
The CLI waits for it; the web app starts it and watches it. Output is never
lost: a timeout keeps everything the process already wrote.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import events, store
from scieflow.core.project import Project

STATES = ("queued", "running", "done", "failed", "timeout", "cancelled", "lost")
FINAL = frozenset({"done", "failed", "timeout", "cancelled", "lost"})
TIMEOUT_EXIT = 124
KILL_GRACE = 10.0


@dataclass
class Job:
    id: str
    kind: str
    argv: list[str]
    cwd: str
    label: str = ""
    run_dir: str | None = None
    state: str = "queued"
    pid: int | None = None
    queued: str = ""
    started: str | None = None
    finished: str | None = None
    exit_code: int | None = None
    timeout_s: float | None = None
    log: str = ""
    err: str = ""

    @property
    def duration_s(self) -> float | None:
        if not (self.started and self.finished):
            return None
        a = datetime.fromisoformat(self.started)
        b = datetime.fromisoformat(self.finished)
        return max(0.0, (b - a).total_seconds())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def jobs_dir(project: Project, run_dir: Path | None) -> Path:
    return Path(run_dir) / "jobs" if run_dir else project.state_dir / "jobs"


def _record_path(job: Job) -> Path:
    return Path(job.log).with_suffix(".json")


def save(job: Job) -> None:
    store.write_text(_record_path(job), json.dumps(asdict(job), indent=2))


def load(path: Path) -> Job:
    return Job(**json.loads(Path(path).read_text()))


def _reload(job: Job) -> Job:
    try:
        return load(_record_path(job))
    except (OSError, ValueError, TypeError):
        return job


def list_jobs(project: Project, run_dir: Path | None = None) -> list[Job]:
    dirs = ([jobs_dir(project, run_dir)] if run_dir else
            [project.state_dir / "jobs", *sorted(project.workspace_root.glob("*/jobs"))])
    out = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                out.append(load(path))
            except (OSError, ValueError, TypeError):
                continue
    return sorted(out, key=lambda j: j.id)


def find(project: Project, job_id: str) -> Job | None:
    return next((j for j in list_jobs(project) if j.id == job_id), None)


def _emit(job: Job, type_: str, **data) -> None:
    if job.run_dir:
        events.emit(Path(job.run_dir), type_, "system", job=job.id, kind=job.kind,
                    label=job.label, **data)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start(project: Project, argv: list[str], *, kind: str, cwd: Path,
          run_dir: Path | None = None, label: str = "", timeout_s: float | None = None,
          stdin_text: str | None = None, env: dict | None = None) -> tuple[Job, subprocess.Popen]:
    job_id = store.new_id()
    directory = jobs_dir(project, run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, kind=kind, argv=list(argv), cwd=str(cwd), label=label,
              run_dir=str(run_dir) if run_dir else None, queued=_now(), timeout_s=timeout_s,
              log=str(directory / f"{job_id}.log"), err=str(directory / f"{job_id}.err"))
    save(job)
    _emit(job, "job.queued")
    with open(job.log, "w") as out, open(job.err, "w") as err:
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, stdout=out, stderr=err, text=True, env=env,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            err.write(f"failed to launch: {exc}\n")
            job.state, job.finished = "failed", _now()
            save(job)
            _emit(job, "job.failed", error=str(exc))
            raise
    job.state, job.pid, job.started = "running", proc.pid, _now()
    save(job)
    _emit(job, "job.started", pid=proc.pid)
    if stdin_text is not None:
        def feed() -> None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        threading.Thread(target=feed, daemon=True).start()
    return job, proc


def _kill_group(pid: int, proc: subprocess.Popen | None = None, grace: float = KILL_GRACE) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        done = proc.poll() is not None if proc is not None else not _alive(pid)
        if done:
            break
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)   # also takes down children left behind
    except (ProcessLookupError, PermissionError):
        pass


def _finish(job: Job, state: str, code: int | None) -> Job:
    job.state, job.exit_code, job.finished = state, code, _now()
    save(job)
    kind = {"done": "job.finished", "failed": "job.failed", "timeout": "job.timeout"}[state]
    _emit(job, kind, exit_code=code, duration_s=job.duration_s)
    return job


def wait(job: Job, proc: subprocess.Popen) -> Job:
    try:
        code = proc.wait(timeout=job.timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid, proc)
        proc.wait()
        return _finish(job, "timeout", TIMEOUT_EXIT)
    current = _reload(job)
    if current.state == "cancelled":       # cancelled by another caller meanwhile
        return current
    return _finish(job, "done" if code == 0 else "failed", code)


def run_blocking(project: Project, argv: list[str], **kw) -> Job:
    job, proc = start(project, argv, **kw)
    return wait(job, proc)


def cancel(job: Job) -> Job:
    current = _reload(job)
    if current.state in FINAL:
        return current
    current.state, current.finished = "cancelled", _now()
    save(current)
    _emit(current, "job.cancelled")
    if current.pid:
        _kill_group(current.pid)
    return current


def reconcile(project: Project, run_dir: Path | None = None) -> list[Job]:
    """Jobs recorded as running whose process is gone (host restart) become lost."""
    lost = []
    for job in list_jobs(project, run_dir):
        if job.state == "running" and job.pid and not _alive(job.pid):
            job.state, job.finished = "lost", _now()
            save(job)
            _emit(job, "job.lost")
            lost.append(job)
    return lost
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_jobs.py -v && uv run pytest -q`
Expected: 7 passed; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/jobs.py tests/core/test_jobs.py
git commit -m "feat(core): job runner with streamed output, timeout, cancel and reconcile

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Agent dispatch on the job runner, with the wall-time guard

**Files:**
- Modify: `src/scieflow/core/agent_run.py`
- Test: `tests/core/test_agent_run.py` (append; existing tests unchanged)

**Interfaces:**
- Consumes: `Project`, `jobs.run_blocking`, `actions.guard_budget/record_spend/run_for_path`.
- Produces: `DispatchError(Exception)`; `Dispatch` dataclass
  (`agent: str, argv: list[str], cwd: Path, stdin_text: str | None, timeout_s: float, run_dir: Path | None`);
  `prepare(project: Project, agent: str, prompt_file: Path, cwd: Path | None = None, role: str | None = None) -> Dispatch`
  (`role` is accepted and ignored until Task 11); `BUDGET_EXIT = 75`; `main()` keeps its
  contract.

- [ ] **Step 1: Write the failing tests** (append to `tests/core/test_agent_run.py`)

```python
def make_loop_workspace(root: Path, wall_cap: int = 60) -> Path:
    from scieflow.core.run import budget, status

    # The dispatch subprocess runs in this repo; a refusal checkpoints the run,
    # which needs the status vocabulary.
    (root / "schemas").mkdir(exist_ok=True)
    (root / "schemas" / "status.yml").write_text((ROOT / "schemas" / "status.yml").read_text())
    ws = root / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    status.write_status(ws, {**status.new_status("r1", "autonomous")})
    budget.write_budget(ws, budget.new_budget(3, 10, wall_cap))
    return ws


def dispatch_in_ws(root: Path, ws: Path, agent: str, prompt: str):
    (ws / "logs" / "p.md").write_text(prompt)
    return subprocess.run(
        [*AGENT_RUN, agent, str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root,
    )


def test_timeout_transcript_keeps_partial_output(tmp_path):
    root = make_repo(tmp_path, stub_cmd=STUB_CMD)
    (root / "config" / "agents.yml").write_text(
        "agents:\n"
        "  talky: {cmd: \"sh -c 'echo partial-output; sleep 300'\", enabled: true, timeout_min: 0.02}\n")
    proc = run_in_repo(root, "talky", "hi")
    assert proc.returncode == 124
    transcript = (root / "out.log").read_text()
    assert "partial-output" in transcript and "timed out" in transcript


def test_dispatch_inside_a_run_is_a_recorded_job_with_events(tmp_path):
    from scieflow.core import events

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    out = ws / "iterations" / "h.md"
    proc = dispatch_in_ws(root, ws, "stub", f"output: {out}\nkind: hypothesis\n")
    assert proc.returncode == 0, proc.stderr
    assert list((ws / "jobs").glob("*.json"))
    types = [e["type"] for e in events.read(ws)]
    assert types[:3] == ["job.queued", "job.started", "job.finished"]
    assert "budget.recorded" in types        # wall time accumulated by the runner


def test_dispatch_refused_when_wall_time_is_spent(tmp_path):
    from scieflow.core.run import actions, status

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root, wall_cap=1)
    actions.record_spend(ws, wall_minutes=2)
    proc = dispatch_in_ws(root, ws, "stub", "output: x.md\nkind: hypothesis\n")
    assert proc.returncode == 75
    assert "budget exhausted" in (ws / "logs" / "t.md").read_text()
    assert status.read_status(ws)["stopped"]["reason"] == "low-budget"


def test_prepare_is_callable_without_exiting(tmp_path):
    from scieflow.core.agent_run import DispatchError, prepare
    from scieflow.core.project import Project

    root = make_repo(tmp_path)
    (root / "p.md").write_text("hello")
    d = prepare(Project(root), "stub", root / "p.md")
    assert d.agent == "stub" and d.run_dir is None and d.timeout_s == 60
    with pytest.raises(DispatchError):
        prepare(Project(root), "nope", root / "p.md")
```

(Add `import pytest` at the top of the file if absent.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_agent_run.py -v`
Expected: the four new tests FAIL (`ImportError: cannot import name 'prepare'`, missing job
records, exit code 0 instead of 75, transcript without `partial-output`).

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/agent_run.py` add imports and replace `main()`:

```python
from dataclasses import dataclass

from scieflow.core import jobs
from scieflow.core.project import Project
from scieflow.core.run import actions

BUDGET_EXIT = 75   # EX_TEMPFAIL: refused, not failed — never an agent's own code


class DispatchError(Exception):
    """A dispatch that cannot be prepared (unknown agent, bad run config)."""


@dataclass
class Dispatch:
    agent: str
    argv: list[str]
    cwd: Path
    stdin_text: str | None
    timeout_s: float
    run_dir: Path | None


def prepare(project: Project, agent: str, prompt_file: Path,
            cwd: Path | None = None, role: str | None = None) -> Dispatch:
    root = project.root
    agents = config.load_agents(root)
    if agent not in agents:
        raise DispatchError(f"unknown agent: {agent} (known: {', '.join(agents)})")
    agent_cfg = agents[agent]
    try:
        override = load_prompt_override(prompt_file, agent)
        run_dir = owning_run_workspace(prompt_file)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise DispatchError(f"invalid owning run configuration: {exc}") from exc
    if override:
        agent_cfg = {**agent_cfg, **override}
    cwd = cwd or root
    if not cwd.is_absolute():
        cwd = root / cwd
    prompt = prompt_file.read_text()
    use_stdin = len(prompt.encode()) > PROMPT_ARGV_LIMIT
    if use_stdin and "stdin_cmd" in agent_cfg:
        argv = build_argv(agent_cfg, prompt, root, include_prompt=False,
                          template=agent_cfg["stdin_cmd"])
    else:
        argv = build_argv(agent_cfg, prompt, root, include_prompt=not use_stdin)
    return Dispatch(agent=agent, argv=argv, cwd=cwd,
                    stdin_text=prompt if use_stdin else None,
                    timeout_s=float(agent_cfg.get("timeout_min", 10)) * 60,
                    run_dir=run_dir)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow agent run")
    ap.add_argument("agent")
    ap.add_argument("prompt_file", type=Path)
    ap.add_argument("transcript_file", type=Path)
    ap.add_argument("--cwd", type=Path, default=None,
                    help="working directory for the agent (default: repo root)")
    args = ap.parse_args(argv)

    project = Project.discover()
    try:
        d = prepare(project, args.agent, args.prompt_file, args.cwd)
    except DispatchError as e:
        sys.exit(str(e))

    if d.run_dir is not None:
        try:
            actions.guard_budget(d.run_dir, ("wall_minutes",))
        except actions.BudgetExhausted as e:
            _write(args.transcript_file,
                   f"{d.agent}: refused, {e} (run checkpointed; resume after raising the budget)\n")
            sys.exit(BUDGET_EXIT)

    try:
        job = jobs.run_blocking(project, d.argv, kind="agent", cwd=d.cwd, run_dir=d.run_dir,
                                label=d.agent, timeout_s=d.timeout_s, stdin_text=d.stdin_text)
    except OSError as e:
        _write(args.transcript_file, f"{d.agent}: failed to launch subprocess: {e}\n")
        sys.exit(f"{d.agent}: failed to launch subprocess: {e}")

    if d.run_dir is not None and job.duration_s is not None:
        actions.record_spend(d.run_dir, wall_minutes=round(job.duration_s / 60, 3))

    output = Path(job.log).read_text()
    if job.state == "timeout":
        _write(args.transcript_file,
               output + f"\n{d.agent}: timed out after {d.timeout_s:.0f}s\n")
        sys.exit(124)
    _write(args.transcript_file, output)
    if job.exit_code != 0:
        sys.stderr.write(Path(job.err).read_text())
        sys.exit(job.exit_code)
    print(f"{d.agent}: done, transcript at {args.transcript_file}")
```

(`subprocess` stays imported only if still used elsewhere in the module; remove it otherwise.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_agent_run.py tests/test_dry_run.py tests/core/test_legacy.py -v && uv run pytest -q && scripts/check_legacy.sh`
Expected: all pass — including the untouched `test_timeout_exits_124`,
`test_failing_agent_exit_code_and_transcript` (exit 3) and the dry run; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/agent_run.py tests/core/test_agent_run.py
git commit -m "feat(core): agent dispatch runs as a job; wall-time budget enforced in code

Output streams to disk and survives timeouts; dispatch inside a run records
its duration against the budget and is refused (exit 75, checkpointed) once
wall time is spent.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Experiment runs counted and guarded

**Files:**
- Modify: `src/scieflow/experiments/cli.py` (`run`, `sweep`)
- Test: `tests/experiments/test_budget_counting.py`

**Interfaces:**
- Consumes: `actions.run_for_path`, `actions.guard_budget`, `actions.record_spend`, `Campaign.expand()`.
- Produces: `_budget_run(experiments_dir: Path) -> Path | None` helper in `experiments/cli.py`.

- [ ] **Step 1: Write the failing test**

The `project` fixture in `tests/experiments/conftest.py` returns the repo root (`tmp_path`)
holding `pipelines/demo/` with the campaign `pipelines/demo/campaigns/gain-sweep.yaml`
(grid `gain: [0.5, 1.0]` → 2 runs).

```python
# tests/experiments/test_budget_counting.py
from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.run import actions, budget, status
from scieflow.experiments.campaign import Campaign
from scieflow.experiments.cli import experiment


def make_run(root, runs_cap):
    ws = root / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, runs_cap, 600))
    return ws


def sweep(root, experiments_dir):
    campaign = root / "pipelines" / "demo" / "campaigns" / "gain-sweep.yaml"
    return CliRunner().invoke(experiment, [
        "sweep", "-c", str(campaign), "--direct",
        "--experiments-dir", str(experiments_dir),
        "--pipelines-dir", str(root / "pipelines")])


def test_sweep_counts_runs_against_the_budget(project):
    ws = make_run(project, runs_cap=10)
    result = sweep(project, ws / "experiments")
    assert result.exit_code == 0, result.output
    n_runs = len(Campaign.load(project / "pipelines" / "demo" / "campaigns"
                               / "gain-sweep.yaml").expand())
    assert budget.read_budget(ws)["spent"]["experiment_runs"] == n_runs == 2
    assert "budget.recorded" in [e["type"] for e in events.read(ws)]


def test_sweep_refused_when_runs_are_spent(project):
    ws = make_run(project, runs_cap=2)
    actions.record_spend(ws, experiment_runs=2)
    result = sweep(project, ws / "experiments")
    assert result.exit_code != 0
    assert "budget exhausted" in result.output
    assert not (ws / "experiments").exists()


def test_sweep_outside_a_run_is_unaffected(project):
    result = sweep(project, project / "exp")
    assert result.exit_code == 0, result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/experiments/test_budget_counting.py -v`
Expected: FAIL — `experiment_runs` stays 0 and the exhausted sweep still runs.

- [ ] **Step 3: Write the implementation**

In `src/scieflow/experiments/cli.py`:

```python
from scieflow.core.run import actions


def _budget_run(experiments_dir: Path) -> Path | None:
    """The run whose budget these runs spend, if experiments_dir lives in one."""
    return actions.run_for_path(Path(experiments_dir))


def _guard(run_dir: Path | None) -> None:
    if run_dir is None:
        return
    try:
        actions.guard_budget(run_dir, ("experiment_runs",))
    except actions.BudgetExhausted as e:
        raise click.ClickException(f"{e} — run checkpointed; raise the budget to continue") from e
```

`run_for_path` needs the run dir to exist; `workspace/<slug>/experiments` may not exist yet, so
resolve against the nearest existing parent: in `_budget_run` use
`actions.run_for_path(next(p for p in [Path(experiments_dir), *Path(experiments_dir).parents] if p.exists()))`.

In `sweep` (before `run_sweep`) and `run` (before `execute_run`) add `run_dir = _budget_run(experiments_dir)`
and `_guard(run_dir)`; after the work, record what was spent:

```python
    # sweep:
    n = len(campaign.expand())
    if run_dir is not None:
        actions.record_spend(run_dir, experiment_runs=n)

    # run:
    if run_dir is not None:
        actions.record_spend(run_dir, experiment_runs=1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/experiments -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/experiments/cli.py tests/experiments/test_budget_counting.py
git commit -m "feat(experiments): runs inside a research run are counted and guarded by its budget

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Gates — approvals as data, with autonomy rules

**Files:**
- Create: `schemas/gates.yml`, `src/scieflow/core/gates.py`
- Modify: `src/scieflow/cli.py` (`GROUPS["gate"]`)
- Test: `tests/core/test_gates.py`

**Interfaces:**
- Consumes: `Project`, `store`, `events.emit`, `run.status.read_status`.
- Produces: `GateError(ValueError)`; `kinds(project) -> dict`;
  `open_gate(project, ws, kind, question, options=(), files=(), in_scope=False, actor="agent") -> dict`;
  `get(ws, gate_id) -> dict`; `list_gates(ws, state=None) -> list[dict]`;
  `answer(project, ws, gate_id, answer, actor="human", rationale="", note="") -> dict`;
  `wait(ws, gate_id, timeout=None, poll=2.0) -> dict` (raises `TimeoutError`).
  Gate record: `{id, kind, question, options, files, in_scope, requires_human, state, opened, opened_by, answer, answered, answered_by, rationale, note}`.
  CLI: `scieflow gate open|list|show|answer|wait`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_gates.py
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow.core import events, gates
from scieflow.core.project import Project
from scieflow.core.run import status

ROOT = Path(__file__).resolve().parents[2]


def setup(tmp_path, approval="per-campaign"):
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", approval))
    return Project(ROOT), ws        # schemas come from the real repo


def test_open_and_human_answer(tmp_path):
    project, ws = setup(tmp_path)
    g = gates.open_gate(project, ws, "campaign-approval", "Run sigma-sweep (7 runs)?",
                        options=["approve", "reject"])
    assert g["state"] == "open" and g["requires_human"] is False
    done = gates.answer(project, ws, g["id"], "approve")
    assert done["state"] == "answered" and done["answered_by"] == "human"
    assert [e["type"] for e in events.read(ws)] == ["gate.opened", "gate.answered"]


def test_answer_must_be_an_offered_option(tmp_path):
    project, ws = setup(tmp_path)
    g = gates.open_gate(project, ws, "campaign-approval", "?", options=["approve", "reject"])
    with pytest.raises(gates.GateError):
        gates.answer(project, ws, g["id"], "maybe")


def test_agent_cannot_answer_in_a_gated_run(tmp_path):
    project, ws = setup(tmp_path, approval="per-campaign")
    g = gates.open_gate(project, ws, "campaign-approval", "?", in_scope=True)
    with pytest.raises(gates.GateError, match="autonomous"):
        gates.answer(project, ws, g["id"], "approve", actor="agent", rationale="within scope")


def test_agent_answers_in_scope_gate_in_autonomous_run(tmp_path):
    project, ws = setup(tmp_path, approval="autonomous")
    g = gates.open_gate(project, ws, "campaign-approval", "?", in_scope=True)
    done = gates.answer(project, ws, g["id"], "approve", actor="agent",
                        rationale="grid stays inside the approved sigma range")
    assert done["answered_by"] == "agent" and done["rationale"]


@pytest.mark.parametrize("kind", ["upload", "external-sharing", "tier-promotion",
                                  "scope-change", "budget-extension", "claim-check-consent"])
def test_requires_human_gates_block_agents_even_when_autonomous(tmp_path, kind):
    project, ws = setup(tmp_path, approval="autonomous")
    g = gates.open_gate(project, ws, kind, "?", in_scope=True)
    assert g["requires_human"] is True
    with pytest.raises(gates.GateError, match="human"):
        gates.answer(project, ws, g["id"], "yes", actor="agent", rationale="r")


def test_agent_needs_scope_and_rationale(tmp_path):
    project, ws = setup(tmp_path, approval="autonomous")
    out_of_scope = gates.open_gate(project, ws, "outline-approval", "?")
    with pytest.raises(gates.GateError, match="scope"):
        gates.answer(project, ws, out_of_scope["id"], "ok", actor="agent", rationale="r")
    in_scope = gates.open_gate(project, ws, "outline-approval", "?", in_scope=True)
    with pytest.raises(gates.GateError, match="rationale"):
        gates.answer(project, ws, in_scope["id"], "ok", actor="agent")


def test_unknown_kind_and_double_answer_are_refused(tmp_path):
    project, ws = setup(tmp_path)
    with pytest.raises(gates.GateError):
        gates.open_gate(project, ws, "coffee", "?")
    g = gates.open_gate(project, ws, "question", "?")
    gates.answer(project, ws, g["id"], "a")
    with pytest.raises(gates.GateError, match="not open"):
        gates.answer(project, ws, g["id"], "b")


def test_wait_returns_when_answered_elsewhere(tmp_path):
    project, ws = setup(tmp_path)
    g = gates.open_gate(project, ws, "question", "?")
    threading.Timer(0.3, lambda: gates.answer(project, ws, g["id"], "later")).start()
    assert gates.wait(ws, g["id"], timeout=5, poll=0.05)["answer"] == "later"
    other = gates.open_gate(project, ws, "question", "?")
    with pytest.raises(TimeoutError):
        gates.wait(ws, other["id"], timeout=0.2, poll=0.05)


def test_cli_open_list_answer(tmp_path, monkeypatch):
    from scieflow.core.gates import gate

    project, ws = setup(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "schemas").mkdir()
    for name in ("gates", "status"):
        (tmp_path / "schemas" / f"{name}.yml").write_text((ROOT / "schemas" / f"{name}.yml").read_text())
    monkeypatch.chdir(tmp_path)
    cli = CliRunner()
    opened = cli.invoke(gate, ["open", "r1", "--kind", "question", "--question", "Which dataset?",
                               "--option", "A", "--option", "B", "--json"])
    assert opened.exit_code == 0, opened.output
    gate_id = __import__("json").loads(opened.output)["id"]
    assert gate_id in cli.invoke(gate, ["list", "r1", "--open"]).output
    assert cli.invoke(gate, ["answer", "r1", gate_id, "B"]).exit_code == 0
    assert "B" in cli.invoke(gate, ["show", "r1", gate_id]).output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_gates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scieflow.core.gates'`

- [ ] **Step 3: Write the implementation**

```yaml
# schemas/gates.yml
# Decisions the protocols require. `requires_human: true` blocks in every mode —
# in an autonomous run the coordinator may answer only the others, only when it
# opened them as in-scope, and only with a recorded rationale.
kinds:
  campaign-approval:   {requires_human: false, help: "Run a proposed experiment campaign"}
  outline-approval:    {requires_human: false, help: "Draft from this paper outline"}
  staffing:            {requires_human: false, help: "Which agents do which roles for this run"}
  question:            {requires_human: false, help: "A clarifying question with no side effects"}
  claim-check-consent: {requires_human: true,  help: "Spend the user's NotebookLM quota on a claim audit"}
  upload:              {requires_human: true,  help: "Push data or chats to remote storage"}
  external-sharing:    {requires_human: true,  help: "Send content to an external service"}
  tier-promotion:      {requires_human: true,  help: "Let a support agent act as primary for a role"}
  scope-change:        {requires_human: true,  help: "Leave the approved question, scope or bounds"}
  budget-extension:    {requires_human: true,  help: "Raise a budget limit"}
states: [open, answered, withdrawn]
```

```python
# src/scieflow/core/gates.py
"""Gates: every approval a protocol requires, stored as data.

A gate is `workspace/<slug>/gates/<id>.json`. Agents open it and wait; the human
answers from the terminal or the browser. In an autonomous run the coordinator
may answer a gate itself — only a kind that does not require a human, only one
it opened as in-scope, and only with a rationale — and that answer is logged
as the agent's, never passed off as the user's.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from scieflow.core import events, store
from scieflow.core.project import Project, ProjectError
from scieflow.core.run import status as status_mod

GATES_DIR = "gates"


class GateError(ValueError):
    """A gate operation the rules do not allow."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kinds(project: Project) -> dict:
    return project.schema("gates")["kinds"]


def _path(ws: Path, gate_id: str) -> Path:
    return Path(ws) / GATES_DIR / f"{gate_id}.json"


def _save(ws: Path, gate: dict) -> None:
    store.write_text(_path(ws, gate["id"]), json.dumps(gate, indent=2))


def get(ws: Path, gate_id: str) -> dict:
    path = _path(ws, gate_id)
    if not path.exists():
        raise GateError(f"no gate {gate_id} in {Path(ws).name}")
    return json.loads(path.read_text())


def list_gates(ws: Path, state: str | None = None) -> list[dict]:
    directory = Path(ws) / GATES_DIR
    found = [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))] \
        if directory.is_dir() else []
    return [g for g in found if state is None or g["state"] == state]


def open_gate(project: Project, ws: Path, kind: str, question: str, options=(),
              files=(), in_scope: bool = False, actor: str = "agent") -> dict:
    table = kinds(project)
    if kind not in table:
        raise GateError(f"unknown gate kind {kind!r} (known: {', '.join(table)})")
    gate = {
        "id": store.new_id(), "kind": kind, "question": question,
        "options": list(options), "files": [str(f) for f in files],
        "in_scope": bool(in_scope), "requires_human": bool(table[kind]["requires_human"]),
        "state": "open", "opened": _now(), "opened_by": actor,
        "answer": None, "answered": None, "answered_by": None, "rationale": "", "note": "",
    }
    _save(ws, gate)
    events.emit(ws, "gate.opened", actor, gate=gate["id"], kind=kind, question=question)
    return gate


def _check_agent_may_answer(ws: Path, gate: dict, rationale: str) -> None:
    if gate["requires_human"]:
        raise GateError(f"{gate['kind']} gates need a human answer, in every mode")
    approval = (status_mod.read_status(ws) or {}).get("approval")
    if approval != "autonomous":
        raise GateError("agents answer gates only in autonomous runs")
    if not gate["in_scope"]:
        raise GateError("only gates opened as in-scope can be answered by the agent")
    if not rationale.strip():
        raise GateError("an agent's answer needs a rationale")


def answer(project: Project, ws: Path, gate_id: str, answer: str, actor: str = "human",
           rationale: str = "", note: str = "") -> dict:
    with store.locked(_path(ws, gate_id)):
        gate = get(ws, gate_id)
        if gate["state"] != "open":
            raise GateError(f"gate {gate_id} is not open ({gate['state']})")
        if gate["options"] and answer not in gate["options"]:
            raise GateError(f"answer must be one of: {', '.join(gate['options'])}")
        if actor == "agent":
            _check_agent_may_answer(ws, gate, rationale)
        gate.update(state="answered", answer=answer, answered=_now(), answered_by=actor,
                    rationale=rationale, note=note)
        path = _path(ws, gate_id)
        tmp = path.with_suffix(".json.new")
        tmp.write_text(json.dumps(gate, indent=2))
        tmp.replace(path)
    events.emit(ws, "gate.answered", actor, gate=gate_id, kind=gate["kind"], answer=answer,
                rationale=rationale)
    return gate


def wait(ws: Path, gate_id: str, timeout: float | None = None, poll: float = 2.0) -> dict:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        gate = get(ws, gate_id)
        if gate["state"] != "open":
            return gate
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"gate {gate_id} still open")
        time.sleep(poll)


# -- CLI ------------------------------------------------------------------
def _ws(slug: str) -> tuple[Project, Path]:
    project = Project.discover()
    try:
        ws = project.run_dir(slug)
    except ProjectError as e:
        raise click.ClickException(str(e)) from e
    if not ws.is_dir():
        raise click.ClickException(f"no run workspace/{slug}")
    return project, ws


@click.group()
def gate():
    """Approvals the protocols require, answered from terminal or browser."""


@gate.command("open")
@click.argument("slug")
@click.option("--kind", required=True)
@click.option("--question", required=True)
@click.option("--option", "options", multiple=True)
@click.option("--file", "files", multiple=True, type=click.Path(path_type=Path))
@click.option("--in-scope", is_flag=True, help="Inside the approved goal, scope and budget.")
@click.option("--json", "as_json", is_flag=True)
def open_cmd(slug, kind, question, options, files, in_scope, as_json):
    """Open a gate (agents call this, then `gate wait`)."""
    project, ws = _ws(slug)
    try:
        g = open_gate(project, ws, kind, question, options, files, in_scope, actor="agent")
    except GateError as e:
        raise click.ClickException(str(e)) from e
    click.echo(json.dumps(g, indent=2) if as_json else g["id"])


@gate.command("list")
@click.argument("slug")
@click.option("--open", "only_open", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
def list_cmd(slug, only_open, as_json):
    """Gates of a run."""
    _, ws = _ws(slug)
    found = list_gates(ws, "open" if only_open else None)
    if as_json:
        click.echo(json.dumps(found, indent=2))
        return
    for g in found:
        flag = " [human]" if g["requires_human"] else ""
        click.echo(f"{g['id']}  {g['state']:<9} {g['kind']:<20}{flag}  {g['question']}")


@gate.command("show")
@click.argument("slug")
@click.argument("gate_id")
def show_cmd(slug, gate_id):
    """One gate, in full."""
    _, ws = _ws(slug)
    try:
        click.echo(json.dumps(get(ws, gate_id), indent=2))
    except GateError as e:
        raise click.ClickException(str(e)) from e


@gate.command("answer")
@click.argument("slug")
@click.argument("gate_id")
@click.argument("answer_text")
@click.option("--note", default="")
@click.option("--as-agent", is_flag=True, help="Answer as the coordinator (autonomous runs only).")
@click.option("--rationale", default="")
def answer_cmd(slug, gate_id, answer_text, note, as_agent, rationale):
    """Answer an open gate."""
    project, ws = _ws(slug)
    try:
        g = answer(project, ws, gate_id, answer_text, "agent" if as_agent else "human",
                   rationale, note)
    except GateError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"{g['id']}: {g['answer']}")


@gate.command("wait")
@click.argument("slug")
@click.argument("gate_id")
@click.option("--timeout", type=float, default=None, help="Seconds; default waits forever.")
def wait_cmd(slug, gate_id, timeout):
    """Block until the gate is answered; print it as JSON (exit 2 on timeout)."""
    _, ws = _ws(slug)
    try:
        click.echo(json.dumps(wait(ws, gate_id, timeout), indent=2))
    except TimeoutError as e:
        click.echo(str(e), err=True)
        raise SystemExit(2) from e
```

In `src/scieflow/cli.py` `GROUPS` add:

```python
    "gate": ("scieflow.core.gates", "gate", None,
             "Approvals the protocols require, answered from terminal or browser."),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_gates.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add schemas/gates.yml src/scieflow/core/gates.py src/scieflow/cli.py tests/core/test_gates.py
git commit -m "feat(core): gates — approvals as data, with autonomous-run rules

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Service layer

**Files:**
- Create: `src/scieflow/core/service.py`
- Modify: `src/scieflow/core/run/cli.py` (add `run list`), `src/scieflow/core/menu.py` (`menu_json` runs via the service)
- Test: `tests/core/test_service.py`

**Interfaces:**
- Consumes: everything above.
- Produces (all JSON-ready dicts/lists):
  `list_runs(project) -> list[dict]`;
  `run_detail(project, slug) -> dict` with keys `run, status, budget, remaining, gates, jobs, events`;
  `run_events(project, slug, since=None, types=()) -> list[dict]`;
  `dispatch_agent(project, agent, prompt_file, transcript, *, cwd=None, role=None, detach=False) -> dict` (job as dict);
  `cancel_job(project, job_id) -> dict`;
  `open_gates(project, slug=None) -> list[dict]` (each with `slug`);
  `answer_gate(project, slug, gate_id, answer, actor="human", rationale="", note="") -> dict`;
  `agent_settings(project, slug=None) -> dict`;
  `ServiceError(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_service.py
import sys
import time
from pathlib import Path

import pytest

from scieflow.core import service
from scieflow.core.project import Project
from scieflow.core.run import status

ROOT = Path(__file__).resolve().parents[2]
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1}}\n'
        '  sleepy: {cmd: "sleep 300", enabled: true, timeout_min: 5}\n')
    (tmp_path / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return Project(tmp_path)


def test_list_and_detail(project):
    assert [r["slug"] for r in service.list_runs(project)] == ["r1"]
    detail = service.run_detail(project, "r1")
    assert detail["status"]["run"] == "r1"
    assert detail["gates"] == [] and detail["jobs"] == [] and detail["events"] == []


def test_dispatch_blocking_records_a_job(project):
    ws = project.run_dir("r1")
    out = ws / "iterations" / "h.md"
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {out}\nkind: hypothesis\n")
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert job["state"] == "done" and out.exists()
    assert (ws / "logs" / "t.md").exists()
    assert service.run_detail(project, "r1")["jobs"][0]["id"] == job["id"]


def test_dispatch_detached_then_cancel(project):
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("hi")
    job = service.dispatch_agent(project, "sleepy", prompt, ws / "logs" / "t.md", detach=True)
    assert job["state"] == "running"
    cancelled = service.cancel_job(project, job["id"])
    assert cancelled["state"] == "cancelled"


def test_gates_across_runs(project):
    from scieflow.core import gates

    ws = project.run_dir("r1")
    g = gates.open_gate(project, ws, "question", "Which dataset?", options=["A", "B"])
    assert [x["id"] for x in service.open_gates(project)] == [g["id"]]
    assert service.open_gates(project)[0]["slug"] == "r1"
    service.answer_gate(project, "r1", g["id"], "A")
    assert service.open_gates(project) == []


def test_unknown_run_is_a_service_error(project):
    with pytest.raises(service.ServiceError):
        service.run_detail(project, "nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scieflow.core.service'`

- [ ] **Step 3: Write the implementation**

```python
# src/scieflow/core/service.py
"""The service layer: every ScieFlow action exists once, here.

The CLI, the TUI menu, the agent skill JSON and (M2) the HTTP API are thin
callers. Everything returns plain JSON-ready data and raises `ServiceError`
for anything a caller should show the user.
"""

from __future__ import annotations

import threading
from dataclasses import asdict
from pathlib import Path

from scieflow.core import agent_config, events, gates, jobs, workspace
from scieflow.core.project import Project, ProjectError
from scieflow.core.run import budget, status

RECENT_JOBS = 20
RECENT_EVENTS = 50


class ServiceError(Exception):
    """A request the service cannot fulfil, with a message for the user."""


def _ws(project: Project, slug: str) -> Path:
    try:
        ws = project.run_dir(slug)
    except ProjectError as e:
        raise ServiceError(str(e)) from e
    if not ws.is_dir():
        raise ServiceError(f"no run workspace/{slug}")
    return ws


def list_runs(project: Project) -> list[dict]:
    return [asdict(r) for r in workspace.list_runs(project.root)]


def run_detail(project: Project, slug: str) -> dict:
    ws = _ws(project, slug)
    st = status.read_status(ws) if (ws / "status.yml").exists() else None
    b = budget.read_budget(ws) if (ws / "budget.yml").exists() else None
    return {
        "run": asdict(workspace.describe(ws)),
        "status": st,
        "budget": b,
        "remaining": budget.remaining_fraction(b) if b else None,
        "gates": gates.list_gates(ws, "open"),
        "jobs": [asdict(j) for j in jobs.list_jobs(project, ws)][-RECENT_JOBS:][::-1],
        "events": events.read(ws)[-RECENT_EVENTS:],
    }


def run_events(project: Project, slug: str, since: str | None = None,
               types: tuple[str, ...] = ()) -> list[dict]:
    return events.read(_ws(project, slug), since=since, types=types)


def dispatch_agent(project: Project, agent: str, prompt_file: Path, transcript: Path, *,
                   cwd: Path | None = None, role: str | None = None,
                   detach: bool = False) -> dict:
    from scieflow.core.agent_run import DispatchError, prepare
    from scieflow.core.run import actions

    try:
        d = prepare(project, agent, Path(prompt_file), cwd, role)
    except DispatchError as e:
        raise ServiceError(str(e)) from e
    if d.run_dir is not None:
        try:
            actions.guard_budget(d.run_dir, ("wall_minutes",))
        except actions.BudgetExhausted as e:
            raise ServiceError(f"{e} — run checkpointed") from e
    job, proc = jobs.start(project, d.argv, kind="agent", cwd=d.cwd, run_dir=d.run_dir,
                           label=agent, timeout_s=d.timeout_s, stdin_text=d.stdin_text)

    def finish() -> jobs.Job:
        done = jobs.wait(job, proc)
        if d.run_dir is not None and done.duration_s is not None:
            actions.record_spend(d.run_dir, wall_minutes=round(done.duration_s / 60, 3))
        out = Path(done.log).read_text()
        note = f"\n{agent}: timed out after {d.timeout_s:.0f}s\n" if done.state == "timeout" else ""
        Path(transcript).parent.mkdir(parents=True, exist_ok=True)
        Path(transcript).write_text(out + note)
        return done

    if detach:
        threading.Thread(target=finish, daemon=True).start()
        return asdict(job)
    return asdict(finish())


def cancel_job(project: Project, job_id: str) -> dict:
    job = jobs.find(project, job_id)
    if job is None:
        raise ServiceError(f"no job {job_id}")
    return asdict(jobs.cancel(job))


def open_gates(project: Project, slug: str | None = None) -> list[dict]:
    slugs = [slug] if slug else [r["slug"] for r in list_runs(project)]
    out = []
    for name in slugs:
        for g in gates.list_gates(_ws(project, name), "open"):
            out.append({**g, "slug": name})
    return out


def answer_gate(project: Project, slug: str, gate_id: str, answer: str,
                actor: str = "human", rationale: str = "", note: str = "") -> dict:
    try:
        return gates.answer(project, _ws(project, slug), gate_id, answer, actor, rationale, note)
    except gates.GateError as e:
        raise ServiceError(str(e)) from e


def agent_settings(project: Project, slug: str | None = None) -> dict:
    return agent_config.resolve(project.root, slug).to_json()
```

In `src/scieflow/core/run/cli.py` add:

```python
@run.command("list")
@click.option("--json", "as_json", is_flag=True)
def list_cmd(as_json):
    """Every run with kind, phase and state."""
    from scieflow.core import service

    runs = service.list_runs(Project.discover())
    if as_json:
        click.echo(json.dumps(runs, indent=2, default=str))
        return
    for r in runs:
        phase = f"{r['phase']} ({r['phase_state']})" if r.get("phase") else ""
        click.echo(f"{(r.get('updated_at') or '')[:16]:<16}  {r['kind']:<13} {r['slug']:<48} {phase}")
```

In `src/scieflow/core/menu.py` `menu_json`, replace `"runs": [asdict(r) for r in ws.list_runs()]` with:

```python
        "runs": service.list_runs(Project.discover()),
```

(importing `from scieflow.core import service` and `from scieflow.core.project import Project`
inside `menu_json`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_service.py tests/core/test_menu.py -v && uv run pytest -q`
Expected: all pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/service.py src/scieflow/core/run/cli.py src/scieflow/core/menu.py tests/core/test_service.py
git commit -m "feat(core): service layer — one API for CLI, menu, skills and the coming web app

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Staffing per role — provider, model, effort

**Files:**
- Modify: `src/scieflow/core/agent_config.py`, `src/scieflow/core/agent_configure.py`,
  `src/scieflow/core/agent_run.py`, `src/scieflow/core/menu.py`, `src/scieflow/core/cli.py`
- Test: `tests/core/test_staffing.py`; update scripts in `tests/core/test_menu.py`

**Interfaces:**
- Produces:
  - `agent_config.EXTENDED_THINKING = "env MAX_THINKING_TOKENS=32000 "` (menu re-exports it);
  - `Effective.role_overrides: dict[str, dict[str, Setting]]` (role → agent → `Setting({model?, reasoning?}, source)`), included in `to_json()` as `"role_overrides"`;
  - `agent_config.resolve_dir(root: Path, run_dir: Path | None) -> Effective`;
  - `agent_config.apply_role_override(agent_cfg: dict, override: dict) -> dict`;
  - `agent_configure.parse_assign` accepts `AGENT@MODEL/EFFORT` tokens (either part optional);
  - `scieflow agent run --role ROLE …` applies the role's override for that agent and refuses an agent not assigned to that role.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_staffing.py
import pytest

from scieflow.core import agent_config as ac
from scieflow.core import agent_configure as acf

REGISTRY = {"agents": {
    "codex": {"cmd": "codex exec --model {model} -c e={reasoning} {prompt}", "model": "astra",
              "reasoning": "high", "tier": "primary", "enabled": True,
              "menu": {"models": ["astra", "sol"],
                       "reasoning": {"levels": ["low", "high", "xhigh"]}}},
    "claude": {"cmd": "claude -p --model {model} {prompt}", "model": "fable",
               "tier": "primary", "enabled": True, "menu": {"models": ["fable", "opus"]}},
    "agy": {"cmd": "agy --model {model} {prompt}", "model": "gem", "tier": "support",
            "enabled": True},
}}


def defaults(draft):
    roles = {r: ("claude" if not spec.many else ["claude"]) for r, spec in ac.ROLES.items()}
    roles["research.submitter"] = "codex"
    roles["research.draft-authors"] = draft
    return {"assignments": roles}


def test_rich_entries_resolve_to_names_plus_role_overrides():
    eff = ac.resolve_data(REGISTRY, defaults([
        {"agent": "codex", "model": "sol", "reasoning": "xhigh"},
        {"agent": "claude", "reasoning": "extended-thinking"},
    ]))
    assert eff.value("research.draft-authors") == ["codex", "claude"]
    assert eff.role_overrides["research.draft-authors"]["codex"].value == {
        "model": "sol", "reasoning": "xhigh"}
    assert eff.problems == []
    assert "role_overrides" in eff.to_json()


def test_plain_strings_still_work():
    eff = ac.resolve_data(REGISTRY, defaults(["codex", "claude"]))
    assert eff.value("research.draft-authors") == ["codex", "claude"]
    assert eff.role_overrides == {}


def test_unknown_fields_and_off_menu_models_are_reported():
    eff = ac.resolve_data(REGISTRY, defaults([{"agent": "codex", "temperature": 2},
                                              {"agent": "claude", "model": "gpt-9"}]))
    assert any("temperature" in p for p in eff.problems)
    assert any("gpt-9" in w for w in eff.warnings)


def test_support_agent_drafting_needs_the_explicit_promotion():
    base = defaults([{"agent": "agy", "model": "gem"}, "codex"])
    assert any("primary-only" in p for p in ac.resolve_data(REGISTRY, base).problems)
    promoted = {**base, "support_as_primary": ["research.draft-authors"]}
    assert ac.resolve_data(REGISTRY, promoted).problems == []


def test_apply_role_override_handles_reasoning_both_ways():
    codex = REGISTRY["agents"]["codex"]
    assert ac.apply_role_override(codex, {"model": "sol", "reasoning": "xhigh"})["reasoning"] == "xhigh"
    claude = ac.apply_role_override(REGISTRY["agents"]["claude"], {"reasoning": "extended-thinking"})
    assert claude["cmd"].startswith(ac.EXTENDED_THINKING)
    back = ac.apply_role_override(claude, {"reasoning": "default"})
    assert not back["cmd"].startswith(ac.EXTENDED_THINKING)


@pytest.mark.parametrize("token, expected", [
    ("codex", "codex"),
    ("codex@sol", {"agent": "codex", "model": "sol"}),
    ("codex@sol/xhigh", {"agent": "codex", "model": "sol", "reasoning": "xhigh"}),
    ("claude@/extended-thinking", {"agent": "claude", "reasoning": "extended-thinking"}),
])
def test_parse_assign_rich_tokens(token, expected):
    op = acf.parse_assign(f"research.draft-authors={token},codex")
    assert op.value[0] == expected


def test_agent_run_role_applies_override_and_refuses_unassigned(tmp_path):
    import subprocess
    import sys
    import yaml

    root = tmp_path
    (root / "config").mkdir()
    cmd = f"{sys.executable} -c \"import sys; print(sys.argv[1:])\" --model {{model}} --effort {{reasoning}}"
    reg = {"agents": {"codex": {"cmd": cmd, "model": "astra", "reasoning": "high",
                                "tier": "primary", "enabled": True, "timeout_min": 1},
                      "claude": {"cmd": cmd.replace(" --effort {reasoning}", ""), "model": "fable",
                                 "tier": "primary", "enabled": True, "timeout_min": 1}}}
    (root / "config" / "agents.yml").write_text(yaml.safe_dump(reg))
    d = defaults([{"agent": "codex", "model": "sol", "reasoning": "xhigh"}])
    d["assignments"]["research.submitter"] = "codex"
    (root / "config" / "defaults.yml").write_text(yaml.safe_dump(d))
    (root / "p.md").write_text("hi")
    run = [sys.executable, "-m", "scieflow.core.agent_run"]
    ok = subprocess.run([*run, "--role", "research.draft-authors", "codex", "p.md", "t.md"],
                        cwd=root, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert "'sol'" in (root / "t.md").read_text() and "'xhigh'" in (root / "t.md").read_text()
    refused = subprocess.run([*run, "--role", "research.draft-authors", "claude", "p.md", "t.md"],
                             cwd=root, capture_output=True, text=True)
    assert refused.returncode != 0 and "not assigned" in refused.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_staffing.py -v`
Expected: FAIL (`AttributeError: 'Effective' object has no attribute 'role_overrides'` / `module has no attribute 'EXTENDED_THINKING'`).

- [ ] **Step 3: Write the implementation**

In `src/scieflow/core/agent_config.py`:

```python
EXTENDED_THINKING = "env MAX_THINKING_TOKENS=32000 "
ROLE_OVERRIDE_FIELDS = ("model", "reasoning")
```

Add to `Effective`: `role_overrides: dict[str, dict[str, Setting]] = field(default_factory=dict)`,
and in `to_json` add
`"role_overrides": {r: {a: {"value": s.value, "source": s.source} for a, s in per.items()} for r, per in self.role_overrides.items()},`.

Add the splitter and use it at the end of `resolve_data`, right before `eff = Effective(...)`:

```python
def _split_entries(value):
    """Role entries may be names or {agent, model?, reasoning?}; split them."""
    def one(entry):
        if isinstance(entry, dict):
            return entry.get("agent"), {k: v for k, v in entry.items() if k != "agent"}
        return entry, {}

    if isinstance(value, list):
        names, extra = [], {}
        for entry in value:
            name, fields = one(entry)
            names.append(name)
            if fields and isinstance(name, str):
                extra[name] = fields
        return names, extra
    name, fields = one(value)
    return name, ({name: fields} if fields and isinstance(name, str) else {})
```

```python
    role_overrides: dict[str, dict[str, Setting]] = {}
    for role, setting in list(assignments.items()):
        names, extra = _split_entries(setting.value)
        assignments[role] = Setting(names, setting.source)
        if extra:
            role_overrides[role] = {a: Setting(v, setting.source) for a, v in extra.items()}
```

and pass `role_overrides=role_overrides` to `Effective(...)`. In `validate`, after the tier
checks, add:

```python
    for role, per_agent in eff.role_overrides.items():
        for name, setting in per_agent.items():
            bad = sorted(set(setting.value) - set(ROLE_OVERRIDE_FIELDS))
            if bad:
                problems.append(f"role {role}: {name}: unknown field(s) {', '.join(bad)} "
                                f"(allowed: {', '.join(ROLE_OVERRIDE_FIELDS)})")
            model = setting.value.get("model")
            models = (eff.menus.get(name) or {}).get("models") or []
            if model and models and model not in models:
                warnings.append(f"role {role}: {name}: model {model!r} is not on its menu "
                                f"({', '.join(map(str, models))})")
```

Add the helpers:

```python
def resolve_dir(root: Path, run_dir: Path | None) -> Effective:
    """Resolve with the workspace config read from `run_dir` (any path, not only root/workspace)."""
    return resolve_data(_load_yaml(root / "config" / "agents.yml"),
                        _load_yaml(root / "config" / "defaults.yml"),
                        _load_yaml(run_dir / "config.yml") if run_dir else None)


def apply_role_override(agent_cfg: dict, override: dict) -> dict:
    """Agent config as this role runs it. Claude has no effort flag: extended
    thinking is the MAX_THINKING_TOKENS prefix on its commands."""
    cfg = dict(agent_cfg)
    if override.get("model"):
        cfg["model"] = override["model"]
    reasoning = override.get("reasoning")
    if not reasoning:
        return cfg
    if "{reasoning}" in cfg.get("cmd", ""):
        cfg["reasoning"] = reasoning
        return cfg
    for key in ("cmd", "stdin_cmd"):
        if key not in cfg:
            continue
        plain = cfg[key].removeprefix(EXTENDED_THINKING)
        cfg[key] = EXTENDED_THINKING + plain if reasoning == "extended-thinking" else plain
    return cfg
```

In `src/scieflow/core/menu.py` replace `EXTENDED_THINKING = "env MAX_THINKING_TOKENS=32000 "`
with `from scieflow.core.agent_config import EXTENDED_THINKING  # noqa: F401 (re-exported)`.

In `src/scieflow/core/agent_configure.py` `parse_assign`, build entries from tokens:

```python
def _entry(token: str):
    agent, sep, rest = token.partition("@")
    if not sep:
        return agent
    model, _, reasoning = rest.partition("/")
    entry = {"agent": agent}
    if model:
        entry["model"] = model
    if reasoning:
        entry["reasoning"] = reasoning
    return entry if len(entry) > 1 else agent
```

and use `names = [_entry(n.strip()) for n in raw.split(",") if n.strip()]`; the help text of
`--assign` in `src/scieflow/core/cli.py` becomes
`"ROLE=AGENT[@MODEL][/EFFORT][,…] — e.g. research.draft-authors=codex-paper,claude@claude-opus-5/extended-thinking"`.

In `src/scieflow/core/agent_run.py`: add `ap.add_argument("--role", default=None, help="the role this dispatch performs; applies that role's model/effort for the agent")`,
pass `args.role` to `prepare`, and in `prepare` after the workspace override:

```python
    if role:
        eff = agent_config.resolve_dir(root, run_dir)
        assigned = eff.value(role)
        names = assigned if isinstance(assigned, list) else [assigned]
        if agent not in names:
            raise DispatchError(f"{agent} is not assigned to {role} "
                                f"(assigned: {', '.join(map(str, names))})")
        override_setting = eff.role_overrides.get(role, {}).get(agent)
        if override_setting:
            agent_cfg = agent_config.apply_role_override(agent_cfg, override_setting.value)
```

(`from scieflow.core import agent_config`.)

In `src/scieflow/core/menu.py` `_pick_role`, after the agents are chosen, offer per-role tuning
and assign the resulting entries:

```python
def _role_entries(ui: UI, eff, role: str, names: list[str]):
    entries = []
    for name in names:
        if not ui.confirm(f"  Set model/effort for {name} in {role} only?", default=False):
            entries.append(name)
            continue
        models = [str(m) for m in (eff.menus.get(name) or {}).get("models") or []]
        model = ui.select(f"  {name} model for {role}",
                          [(m, m) for m in models] + [("keep the agent's default", "")])
        if model is BACK:
            return None
        level = ui.select(f"  {name} effort for {role}",
                          [(lv, lv) for lv in effort_levels(eff, name)]
                          + [("keep the agent's default", "")])
        if level is BACK:
            return None
        entry = {"agent": name, **({"model": model} if model else {}),
                 **({"reasoning": level} if level else {})}
        entries.append(entry if len(entry) > 1 else name)
    return entries
```

In `_pick_role`, for a fan-out role replace `attempt([acf.Op("assign", role, picked)])` with:

```python
        entries = _role_entries(ui, eff, role, picked)
        if entries is None:
            return
        attempt([acf.Op("assign", role, entries)])
```

and for a single role with `picked` a name:

```python
        entries = _role_entries(ui, eff, role, [picked])
        if entries is None:
            return
        attempt([acf.Op("assign", role, entries[0])])
```

Update `tests/core/test_menu.py` scripts — each role pick now asks one confirm per agent
(answer `False` to keep today's behaviour):

```python
# test_cancel_writes_nothing
ui = ScriptedUI(["default", "role", "research.outline", "codex", False, "save", False,
                 "BACK", True])
# test_role_change_for_all_projects
ui = ScriptedUI(["default", "role", "research.outline", "codex", False, "save", True])
# test_invalid_combination_is_refused_before_queueing
ui = ScriptedUI(["default", "role", "research.reviewer", "claude", False, "save", "BACK"])
# test_support_agent_on_primary_role_needs_explicit_exception
ui = ScriptedUI(["default", "role", "research.reviewer", "agy", False, False, "save", "BACK"])
```

and add a test for the new path:

```python
def test_role_level_model_and_effort(repo):
    ui = ScriptedUI(["default", "role", "research.outline", "codex", True, "sol", "high",
                     "save", True])
    menu.agent_settings(ui)
    doc = yaml.safe_load((repo / "config" / "defaults.yml").read_text())
    assert doc["assignments"]["research.outline"] == {
        "agent": "codex", "model": "sol", "reasoning": "high"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_staffing.py tests/core/test_menu.py tests/core/test_agent_config.py tests/core/test_legacy_keys.py -v && uv run pytest -q`
Expected: all pass; full suite green; `uv run scieflow agent show` reports no problems on the real config.

- [ ] **Step 5: Commit**

```bash
git add src/scieflow/core/agent_config.py src/scieflow/core/agent_configure.py src/scieflow/core/agent_run.py src/scieflow/core/menu.py src/scieflow/core/cli.py tests/core/test_staffing.py tests/core/test_menu.py
git commit -m "feat(core): staffing per role — provider, model and effort for each task

Assignments may name {agent, model, reasoning}; `agent run --role` applies the
role's choice. The user chooses; ScieFlow never picks or downgrades a model.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Protocols, docs, and the end-to-end check

**Files:**
- Modify: `AGENTS.md` (rules 4, 5, 13 + a new gates rule), `skills/research-loop/SKILL.md`,
  `skills/experiment-cycle/SKILL.md`, `src/scieflow/research/AGENTS.md`, `README.md`, `mkdocs.yml`
- Create: `docs/runs.md`, `tests/test_service_e2e.py`

**Interfaces:**
- Consumes: the whole milestone.

- [ ] **Step 1: Write the end-to-end test**

```python
# tests/test_service_e2e.py
"""One research-loop iteration through the service core: run created, phases
marked, agents dispatched as jobs, an approval gate, spend recorded, and a
budget-driven checkpoint — all visible in the event log. Stub agent, no network."""

import sys
from pathlib import Path

from scieflow.core import events, gates, jobs, service
from scieflow.core.project import Project
from scieflow.core.run import actions, init, status

ROOT = Path(__file__).resolve().parents[1]
KIND = {"hypothesize": "hypothesis", "experiment": "results-summary",
        "literature": "literature", "synthesize": "synthesis"}


def test_one_iteration_end_to_end(tmp_path):
    project = Project(ROOT)
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\nDry run through the service core.\n")
    ws = init.init_workspace("e2e", goal, tmp_path / "workspace",
                             {"approval": "autonomous", "max_iterations": 1,
                              "max_experiment_runs": 6, "max_wall_minutes": 60}, ROOT)
    it = ws / "iterations" / "1"
    it.mkdir()

    for phase, kind in KIND.items():
        actions.mark_phase(ws, phase, "running")
        if phase == "experiment":
            g = gates.open_gate(project, ws, "campaign-approval", "Run 6-run sweep?",
                                options=["approve", "reject"], in_scope=True)
            gates.answer(project, ws, g["id"], "approve", actor="agent",
                         rationale="inside the approved budget and scope")
        prompt = ws / "logs" / f"{kind}.md"
        prompt.write_text(f"output: {it / (kind + '.md')}\nkind: {kind}\n")
        job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / f"{kind}.t.md")
        assert job["state"] == "done"
        actions.mark_phase(ws, phase, "done")

    actions.record_spend(ws, iterations=1, experiment_runs=6)
    try:
        actions.advance_iteration(ws)
        raise AssertionError("advance must be refused: iterations spent")
    except actions.BudgetExhausted:
        pass

    st = status.read_status(ws)
    assert st["stopped"]["reason"] == "low-budget" and len(st["id"]) == 26
    types = [e["type"] for e in events.read(ws)]
    for expected in ("run.created", "phase.started", "gate.opened", "gate.answered",
                     "job.finished", "budget.recorded", "checkpoint", "job.refused"):
        assert expected in types, expected
    answered = [e for e in events.read(ws) if e["type"] == "gate.answered"][0]
    assert answered["actor"] == "agent"
    assert len(jobs.list_jobs(project, ws)) == 4
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_service_e2e.py -v`
Expected: PASS (all earlier tasks are in place). If it fails, the failure names the missing
event or state — fix the task that owns it, not the test.

- [ ] **Step 3: Update the protocols and docs**

`AGENTS.md`:

- Rule 4 becomes: "Track state only through `uv run scieflow run mark|advance|checkpoint|resume <slug> … --as-agent`
  (the older `scripts/status.py`/`budget.py`/`checkpoint.py` still work but record no events).
  On entry to a run, `uv run scieflow run show <slug>` and continue from the first phase not
  `done`. Log notable moments with `uv run scieflow run log <slug> note.<name> --message …`
  alongside `log.md`."
- Rule 5, last bullet: "budgets are enforced in code: an exhausted `wall_minutes` refuses the
  next dispatch (exit 75), exhausted `experiment_runs` refuses the next sweep, and
  `run advance` refuses a new iteration once `iterations` is spent — each refusal checkpoints
  the run. Never edit `budget.yml` to get past one; ask the user."
- Rule 13, append: "Dispatch with `--role <role>` (`uv run scieflow agent run --role
  research.draft-authors codex-paper …`) so a role's model/effort choice applies."
- New rule after 13: "**Approvals are gates.** Every approval a protocol requires (campaign,
  outline, staffing, claim-check consent, uploads, external sharing, promotions, scope or
  budget changes) is opened with `uv run scieflow gate open <slug> --kind <kind> --question …`
  and waited on with `scieflow gate wait <slug> <id>`. The user answers in the terminal or
  browser. Only in an `autonomous` run may you answer — with `--as-agent --rationale …`, only a
  kind that does not require a human (`schemas/gates.yml`), and only a gate you opened with
  `--in-scope`. Never answer as the user."

`skills/research-loop/SKILL.md`: replace every instruction to edit `status.yml` or `budget.yml`
by hand (including the "edits `budget.yml` via python … or rewrites the file" passage and any
`set_wall_from_clock` call — wall time is now recorded by the runner) with the matching
`scieflow run …` command, and the campaign-approval step with `gate open` / `gate wait`.
`skills/experiment-cycle/SKILL.md`: the approval step opens a `campaign-approval` gate.
`src/scieflow/research/AGENTS.md`: the run configuration gate opens a `staffing` gate; the
outline approval in paper-draft opens an `outline-approval` gate; every
`scieflow agent run <agent>` line in `src/scieflow/research/skills/*/SKILL.md` gains
`--role <the role named in that step>` (find them with `grep -rn "agent run" src/scieflow/research/skills`).

`docs/runs.md` (new, added to `mkdocs.yml` nav after "Menu"): what a run is; `scieflow run
list|show|events --follow|log`; jobs (streamed output under `workspace/<slug>/jobs/`, timeouts
keep output, `service.cancel_job`); gates (kinds table from `schemas/gates.yml`, gated vs
autonomous answering); budgets enforced in code and exit 75; per-role staffing with the
`AGENT@MODEL/EFFORT` syntax. `README.md`: one paragraph pointing at `docs/runs.md`.

- [ ] **Step 4: Full verification**

Run:
```bash
uv run pytest -q
scripts/check_legacy.sh
uv run --group docs mkdocs build --strict
uv run scieflow agent show | tail -3
uv run scieflow run list
```
Expected: suite green (≥ 779 + the new tests); every legacy check `ok`; docs build with 0
warnings; `agent show` reports no problems; `run list` prints the local runs.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md skills src/scieflow/research README.md mkdocs.yml docs/runs.md tests/test_service_e2e.py
git commit -m "docs(core): protocols use runs, gates and roles; end-to-end service check

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
