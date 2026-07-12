# Metacentrum Remote-Execution Module Implementation Plan (ScieFlow)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A policy-enforcing `scripts/remote/` module that lets the coordinator git-pull, qsub, monitor, fix-and-resubmit, and fetch results on metacentrum.cz over Kerberos-authenticated SSH — bounded by a user-editable deny-by-default `config/remotes.yml` — plus the `remote-exec` skill that drives the loop.

**Architecture:** Four focused files under `scripts/remote/`: `policy.py` (load remotes.yml, allowlist/limits enforcement — the only gate), `transport.py` (the only module that shells out to ssh/rsync, with an injectable runner for offline tests), `jobs.py` (jobs.yml ledger: states, attempt counts), `remote.py` (argparse CLI wiring subcommands to the three). Judgment (diagnose, fix, Snakemake gate) lives in `skills/remote-exec/SKILL.md`.

**Tech Stack:** Python 3 stdlib + PyYAML; pytest with `pythonpath = ["scripts"]` (so `scripts/remote/` with `__init__.py` imports as package `remote`); ssh with GSSAPI (Kerberos), rsync; PBS Pro (`qsub`/`qstat`) on metacentrum.

## Global Constraints

- Deny-by-default: any directory, operation, queue, or resource not in `config/remotes.yml` is refused with the violated rule spelled out; the agent must never work around a refusal.
- `config/remotes.yml` is gitignored (contains username/paths); `config/remotes.example.yml` is committed.
- No credential handling ever: Kerberos ticket comes from the host PC (`kinit` by the user). Missing/expired ticket → the CLI exits with instructions; the agent stops and asks the user.
- Fix flow is git-only: edit locally → commit/push → `remote.py pull` → resubmit. Never edit files on the remote.
- Snakemake gate: any Snakemake workload runs `snakemake -n` (no GPU, minimal resources) via qsub first; real run only after a clean dry-run that prints the full DAG.
- Resource requests are clamped to config caps (with a warning), except queue (refused if not listed).
- Retry ceiling: a task may be submitted at most `1 + max_fix_attempts` times; then mark failed + `checkpoint.py --reason anomaly`.
- All run artifacts stay in `workspace/<slug>/` (AGENTS.md rule 1): jobs ledger at `workspace/<slug>/remote/jobs.yml`, fetched data under `workspace/<slug>/remote/data/`.
- Tests are offline: inject a fake runner; never call real ssh in tests. `uv run pytest -q` passes after every task.

---

### Task 1: policy.py — config loading + allowlist enforcement

**Files:**
- Create: `scripts/remote/__init__.py` (empty)
- Create: `scripts/remote/policy.py`
- Create: `config/remotes.example.yml`
- Modify: `.gitignore` (add `config/remotes.yml`)
- Test: `tests/test_remote_policy.py`

**Interfaces (produced, used by Tasks 2-4):**
- `PolicyError(Exception)`
- `Remote` dataclass: `name, host, user, auth, scheduler, allowed_dirs: list[str], allowed_ops: list[str], limits: dict`
- `load_remote(root: Path, name: str) -> Remote`
- `check_op(remote, op: str) -> None` (raises)
- `check_dir(remote, path: str) -> str` (returns normalized path; raises)
- `check_queue(remote, queue: str) -> None` (raises)
- `walltime_seconds(walltime: str) -> int`
- `clamp_resources(remote, walltime, cpus, mem_gb, gpus) -> tuple[dict, list[str]]` — clamped `{"walltime","cpus","mem_gb","gpus"}` + human-readable warnings

- [ ] **Step 1: Write the failing tests**

`tests/test_remote_policy.py`:

```python
from pathlib import Path

import pytest

from remote import policy

CFG = """\
remotes:
  meta:
    host: skirit.metacentrum.cz
    user: testuser
    auth: kerberos
    scheduler: pbs
    allowed_dirs:
      - /storage/brno2/home/testuser/projx
    allowed_ops: [check, git-pull, qsub, qstat, logs, fetch]
    limits:
      max_walltime: "24:00:00"
      max_cpus: 16
      max_mem_gb: 64
      max_gpus: 1
      queues: [default]
      max_concurrent_jobs: 4
      max_fix_attempts: 3
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "remotes.yml").write_text(CFG)
    return tmp_path


def test_load_remote(root):
    r = policy.load_remote(root, "meta")
    assert r.host == "skirit.metacentrum.cz"
    assert r.limits["max_fix_attempts"] == 3


def test_load_missing_file_and_name(tmp_path, root):
    with pytest.raises(policy.PolicyError, match="remotes.example.yml"):
        policy.load_remote(tmp_path, "meta")       # no config/ dir
    with pytest.raises(policy.PolicyError, match="'nope' not defined"):
        policy.load_remote(root, "nope")


def test_check_op(root):
    r = policy.load_remote(root, "meta")
    policy.check_op(r, "qsub")
    with pytest.raises(policy.PolicyError, match="'rm-rf' not in allowed_ops"):
        policy.check_op(r, "rm-rf")


def test_check_dir_normalizes_and_refuses_escape(root):
    r = policy.load_remote(root, "meta")
    base = "/storage/brno2/home/testuser/projx"
    assert policy.check_dir(r, base) == base
    assert policy.check_dir(r, base + "/sub/") == base + "/sub"
    with pytest.raises(policy.PolicyError, match="outside allowed_dirs"):
        policy.check_dir(r, base + "/../other")
    with pytest.raises(policy.PolicyError, match="outside allowed_dirs"):
        policy.check_dir(r, "/storage/brno2/home/testuser/projx-evil")
    with pytest.raises(policy.PolicyError, match="absolute"):
        policy.check_dir(r, "projx")


def test_clamp_resources(root):
    r = policy.load_remote(root, "meta")
    res, warns = policy.clamp_resources(r, "48:00:00", 32, 128, 2)
    assert res == {"walltime": "24:00:00", "cpus": 16, "mem_gb": 64, "gpus": 1}
    assert len(warns) == 4
    res, warns = policy.clamp_resources(r, "01:00:00", 4, 8, 0)
    assert res["gpus"] == 0 and warns == []


def test_check_queue(root):
    r = policy.load_remote(root, "meta")
    policy.check_queue(r, "default")
    with pytest.raises(policy.PolicyError, match="queue 'gpu_long'"):
        policy.check_queue(r, "gpu_long")


def test_walltime_seconds():
    assert policy.walltime_seconds("01:30:10") == 5410
    with pytest.raises(policy.PolicyError, match="HH:MM:SS"):
        policy.walltime_seconds("90m")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_remote_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'remote'`.

- [ ] **Step 3: Implement policy.py + config files**

`scripts/remote/__init__.py`: empty file.

`scripts/remote/policy.py`:

```python
"""Load config/remotes.yml and enforce its deny-by-default allowlists.

The single sanctioned gate for remote operations: every remote.py
subcommand authorizes here before any SSH happens. PolicyError means the
user's config forbids the operation — report it, never work around it.
"""

import posixpath
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_KEYS = ["host", "user", "auth", "scheduler", "allowed_dirs",
                 "allowed_ops", "limits"]
REQUIRED_LIMITS = ["max_walltime", "max_cpus", "max_mem_gb", "max_gpus",
                   "queues", "max_concurrent_jobs", "max_fix_attempts"]


class PolicyError(Exception):
    """Operation refused by config/remotes.yml."""


@dataclass
class Remote:
    name: str
    host: str
    user: str
    auth: str
    scheduler: str
    allowed_dirs: list
    allowed_ops: list
    limits: dict


def load_remote(root: Path, name: str) -> Remote:
    path = root / "config" / "remotes.yml"
    if not path.exists():
        raise PolicyError(
            f"{path} not found — copy config/remotes.example.yml to "
            "config/remotes.yml and fill it in"
        )
    data = yaml.safe_load(path.read_text()) or {}
    entry = (data.get("remotes") or {}).get(name)
    if entry is None:
        raise PolicyError(f"remote '{name}' not defined in {path}")
    missing = [k for k in REQUIRED_KEYS if k not in entry]
    if missing:
        raise PolicyError(f"remote '{name}' missing keys: {', '.join(missing)}")
    missing = [k for k in REQUIRED_LIMITS if k not in entry["limits"]]
    if missing:
        raise PolicyError(f"remote '{name}' limits missing: {', '.join(missing)}")
    return Remote(name=name, **{k: entry[k] for k in REQUIRED_KEYS})


def check_op(remote: Remote, op: str) -> None:
    if op not in remote.allowed_ops:
        raise PolicyError(
            f"operation '{op}' not in allowed_ops for remote "
            f"'{remote.name}' (allowed: {', '.join(remote.allowed_ops)})"
        )


def check_dir(remote: Remote, path: str) -> str:
    norm = posixpath.normpath(path)
    if not norm.startswith("/"):
        raise PolicyError(f"remote path must be absolute: '{path}'")
    for allowed in remote.allowed_dirs:
        base = posixpath.normpath(allowed)
        if norm == base or norm.startswith(base + "/"):
            return norm
    raise PolicyError(
        f"'{norm}' is outside allowed_dirs for remote '{remote.name}'"
    )


def check_queue(remote: Remote, queue: str) -> None:
    queues = remote.limits["queues"]
    if queue not in queues:
        raise PolicyError(
            f"queue '{queue}' not allowed for remote '{remote.name}' "
            f"(allowed: {', '.join(queues)})"
        )


def walltime_seconds(walltime: str) -> int:
    parts = str(walltime).split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise PolicyError(f"bad walltime '{walltime}' (expected HH:MM:SS)")
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def clamp_resources(remote: Remote, walltime: str, cpus: int, mem_gb: int,
                    gpus: int) -> tuple[dict, list[str]]:
    lim = remote.limits
    warnings = []
    if walltime_seconds(walltime) > walltime_seconds(lim["max_walltime"]):
        warnings.append(f"walltime {walltime} clamped to {lim['max_walltime']}")
        walltime = lim["max_walltime"]
    caps = [("cpus", cpus, lim["max_cpus"]), ("mem_gb", mem_gb, lim["max_mem_gb"]),
            ("gpus", gpus, lim["max_gpus"])]
    clamped = {}
    for label, value, cap in caps:
        if value > cap:
            warnings.append(f"{label} {value} clamped to {cap}")
            value = cap
        clamped[label] = value
    return {"walltime": walltime, **clamped}, warnings
```

`config/remotes.example.yml`:

```yaml
# Copy to config/remotes.yml (gitignored) and fill in your values.
# Deny-by-default: any directory, operation, queue, or resource not listed
# here is refused by scripts/remote/remote.py. This file is the user's
# control surface over what the agent may touch remotely.
remotes:
  meta:
    host: skirit.metacentrum.cz     # login node
    user: YOUR_METACENTRUM_USERNAME
    auth: kerberos        # uses the host PC's ticket (kinit); no keys stored
    scheduler: pbs
    allowed_dirs:         # the ONLY remote paths the agent may touch
      - /storage/brno2/home/YOUR_USERNAME/projects/example
    allowed_ops: [check, git-pull, qsub, qstat, logs, fetch]
    limits:
      max_walltime: "24:00:00"
      max_cpus: 16
      max_mem_gb: 64
      max_gpus: 1
      queues: [default]
      max_concurrent_jobs: 4
      max_fix_attempts: 3   # automatic fix-and-resubmit ceiling per task
```

`.gitignore` — add after the `workspace/*` block:

```
config/remotes.yml
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_remote_policy.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/remote/__init__.py scripts/remote/policy.py \
        config/remotes.example.yml .gitignore tests/test_remote_policy.py
git commit -m "feat: remote policy — deny-by-default remotes.yml enforcement"
```

---

### Task 2: transport.py — ssh/rsync argv construction

**Files:**
- Create: `scripts/remote/transport.py`
- Test: `tests/test_remote_transport.py`

**Interfaces (produced):**
- `Transport(remote: policy.Remote, runner=None)` — `runner(argv: list[str]) -> CompletedProcess-like` (defaults to `subprocess.run(argv, capture_output=True, text=True)`)
- `.target -> str` (`user@host`)
- `.local(argv) `, `.ssh(command: str)`, `.rsync_from(remote_path: str, dest: str)` — all return the runner's result

- [ ] **Step 1: Write the failing tests**

`tests/test_remote_transport.py`:

```python
from types import SimpleNamespace

from remote import policy, transport

REMOTE = policy.Remote(
    name="meta", host="skirit.metacentrum.cz", user="testuser",
    auth="kerberos", scheduler="pbs",
    allowed_dirs=["/storage/x"], allowed_ops=["check"], limits={},
)


def make_transport(calls):
    def runner(argv):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    return transport.Transport(REMOTE, runner=runner)


def test_ssh_argv():
    calls = []
    t = make_transport(calls)
    result = t.ssh("cd /storage/x && git pull --ff-only")
    assert result.returncode == 0
    assert calls == [[
        "ssh", "-o", "BatchMode=yes", "-o", "GSSAPIAuthentication=yes",
        "testuser@skirit.metacentrum.cz",
        "cd /storage/x && git pull --ff-only",
    ]]


def test_local_and_rsync_argv():
    calls = []
    t = make_transport(calls)
    t.local(["klist", "-s"])
    t.rsync_from("/storage/x/results/", "workspace/run/remote/data/")
    assert calls[0] == ["klist", "-s"]
    assert calls[1] == [
        "rsync", "-az",
        "testuser@skirit.metacentrum.cz:/storage/x/results/",
        "workspace/run/remote/data/",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_remote_transport.py -q`
Expected: FAIL — no module `remote.transport`.

- [ ] **Step 3: Implement transport.py**

```python
"""The only module that shells out to ssh/rsync.

Auth is GSSAPI (Kerberos) with BatchMode — no passwords, no keys, no
prompts: a missing ticket fails fast and remote.py turns that into a
kinit instruction for the user.
"""

import subprocess


def _default_runner(argv):
    return subprocess.run(argv, capture_output=True, text=True)


class Transport:
    def __init__(self, remote, runner=None):
        self.remote = remote
        self.runner = runner or _default_runner

    @property
    def target(self) -> str:
        return f"{self.remote.user}@{self.remote.host}"

    def local(self, argv: list):
        return self.runner(list(argv))

    def ssh(self, command: str):
        return self.runner([
            "ssh", "-o", "BatchMode=yes", "-o", "GSSAPIAuthentication=yes",
            self.target, command,
        ])

    def rsync_from(self, remote_path: str, dest: str):
        return self.runner(["rsync", "-az", f"{self.target}:{remote_path}", dest])
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_remote_transport.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/remote/transport.py tests/test_remote_transport.py
git commit -m "feat: remote transport — injectable ssh/rsync runner"
```

---

### Task 3: jobs.py — the jobs.yml ledger

**Files:**
- Create: `scripts/remote/jobs.py`
- Test: `tests/test_remote_jobs.py`

**Interfaces (produced):**
- `STATES = ("queued", "running", "done", "failed")`
- `load_jobs(workspace: Path) -> list[dict]` / `save_jobs(workspace, jobs)` (file: `workspace/remote/jobs.yml`)
- `attempts(jobs, task: str) -> int`
- `active_count(jobs, remote_name: str) -> int`
- `record_submit(jobs, *, task, job_id, remote_name, remote_dir, script, resources) -> dict` (appends entry, `attempt` = attempts+1)
- `set_state(jobs, job_id, state) -> None` (KeyError if unknown id, ValueError if bad state)

- [ ] **Step 1: Write the failing tests**

`tests/test_remote_jobs.py`:

```python
import pytest

from remote import jobs


def submit(j, task="taskA", job_id="1.meta"):
    return jobs.record_submit(
        j, task=task, job_id=job_id, remote_name="meta",
        remote_dir="/storage/x", script="run.sh",
        resources={"walltime": "01:00:00", "cpus": 2, "mem_gb": 4, "gpus": 0,
                   "queue": "default"},
    )


def test_roundtrip_and_attempts(tmp_path):
    j = jobs.load_jobs(tmp_path)
    assert j == []
    entry = submit(j)
    assert entry["state"] == "queued" and entry["attempt"] == 1
    submit(j, job_id="2.meta")
    assert jobs.attempts(j, "taskA") == 2
    jobs.save_jobs(tmp_path, j)
    assert jobs.load_jobs(tmp_path)[1]["job_id"] == "2.meta"
    assert (tmp_path / "remote" / "jobs.yml").exists()


def test_active_count_and_set_state(tmp_path):
    j = []
    submit(j, job_id="1.meta")
    submit(j, task="taskB", job_id="2.meta")
    assert jobs.active_count(j, "meta") == 2
    jobs.set_state(j, "1.meta", "done")
    assert jobs.active_count(j, "meta") == 1
    with pytest.raises(KeyError):
        jobs.set_state(j, "9.meta", "done")
    with pytest.raises(ValueError):
        jobs.set_state(j, "2.meta", "vanished")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_remote_jobs.py -q`
Expected: FAIL — no module `remote.jobs`.

- [ ] **Step 3: Implement jobs.py**

```python
"""Job ledger at workspace/<slug>/remote/jobs.yml: states and attempt counts.

`attempt` counts submissions per task name — the fix-and-resubmit ceiling
(limits.max_fix_attempts) is enforced against it by remote.py submit.
"""

from datetime import datetime, timezone
from pathlib import Path

import yaml

STATES = ("queued", "running", "done", "failed")


def _path(workspace: Path) -> Path:
    return workspace / "remote" / "jobs.yml"


def load_jobs(workspace: Path) -> list:
    path = _path(workspace)
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text()) or []


def save_jobs(workspace: Path, jobs: list) -> None:
    path = _path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(jobs, sort_keys=False))


def attempts(jobs: list, task: str) -> int:
    return sum(1 for j in jobs if j["task"] == task)


def active_count(jobs: list, remote_name: str) -> int:
    return sum(
        1 for j in jobs
        if j["remote"] == remote_name and j["state"] in ("queued", "running")
    )


def record_submit(jobs: list, *, task: str, job_id: str, remote_name: str,
                  remote_dir: str, script: str, resources: dict) -> dict:
    entry = {
        "task": task,
        "job_id": job_id,
        "remote": remote_name,
        "dir": remote_dir,
        "script": script,
        "resources": resources,
        "state": "queued",
        "attempt": attempts(jobs, task) + 1,
        "submitted": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    jobs.append(entry)
    return entry


def set_state(jobs: list, job_id: str, state: str) -> None:
    if state not in STATES:
        raise ValueError(f"unknown state '{state}' (one of {', '.join(STATES)})")
    for j in jobs:
        if j["job_id"] == job_id:
            j["state"] = state
            return
    raise KeyError(job_id)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_remote_jobs.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/remote/jobs.py tests/test_remote_jobs.py
git commit -m "feat: remote jobs ledger — states and attempt accounting"
```

---

### Task 4: remote.py — the CLI

**Files:**
- Create: `scripts/remote/remote.py`
- Test: `tests/test_remote_cli.py`

**Interfaces (produced — what the skill invokes):**

```
uv run scripts/remote/remote.py check  <remote>
uv run scripts/remote/remote.py pull   <remote> <dir>
uv run scripts/remote/remote.py submit <remote> <dir> <script> --workspace WS --task NAME
       [--walltime HH:MM:SS] [--cpus N] [--mem-gb N] [--gpus N] [--queue Q] [--name JOBNAME]
uv run scripts/remote/remote.py status <remote> <job_id> --workspace WS
uv run scripts/remote/remote.py logs   <remote> <job_id> --workspace WS
uv run scripts/remote/remote.py fetch  <remote> <src> <dest> --workspace WS
```

Exit codes: 0 ok, 1 remote command failed, 2 no Kerberos ticket (`NO_TICKET`), 3 policy refusal (`POLICY:` prefix on stderr), 4 retry ceiling / concurrency ceiling (`LIMIT:` prefix). Internal functions `cmd_check/cmd_pull/cmd_submit/cmd_status/cmd_logs/cmd_fetch(...) -> int` take (remote, transport, args-ish params) so tests call them directly with a fake runner.

- [ ] **Step 1: Write the failing tests**

`tests/test_remote_cli.py`:

```python
from types import SimpleNamespace

import pytest

from remote import jobs, policy, remote as cli

CFG_REMOTE = policy.Remote(
    name="meta", host="h", user="u", auth="kerberos", scheduler="pbs",
    allowed_dirs=["/storage/x"],
    allowed_ops=["check", "git-pull", "qsub", "qstat", "logs", "fetch"],
    limits={"max_walltime": "24:00:00", "max_cpus": 16, "max_mem_gb": 64,
            "max_gpus": 1, "queues": ["default"], "max_concurrent_jobs": 2,
            "max_fix_attempts": 1},
)


def fake_transport(script):
    """script: list of (returncode, stdout) consumed per call; records argv."""
    calls = []
    replies = list(script)

    def runner(argv):
        calls.append(argv)
        rc, out = replies.pop(0) if replies else (0, "")
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    from remote import transport
    return transport.Transport(CFG_REMOTE, runner=runner), calls


def test_check_ok_and_no_ticket(capsys):
    t, calls = fake_transport([(0, ""), (0, "OK\n")])
    assert cli.cmd_check(CFG_REMOTE, t) == 0
    assert calls[0] == ["klist", "-s"]
    capsys.readouterr()                       # drain the OK output
    t, _ = fake_transport([(1, "")])
    assert cli.cmd_check(CFG_REMOTE, t) == 2
    out = capsys.readouterr().out
    assert "NO_TICKET" in out and "kinit" in out


def test_pull_builds_command_and_respects_policy():
    t, calls = fake_transport([(0, "Already up to date.\n")])
    assert cli.cmd_pull(CFG_REMOTE, t, "/storage/x/repo") == 0
    assert calls[0][-1] == "cd /storage/x/repo && git pull --ff-only"
    with pytest.raises(policy.PolicyError):
        cli.cmd_pull(CFG_REMOTE, t, "/etc")


def test_submit_clamps_records_and_enforces_ceilings(tmp_path):
    t, calls = fake_transport([(0, "101.meta-pbs\n")])
    rc = cli.cmd_submit(CFG_REMOTE, t, "/storage/x/repo", "run.sh",
                        workspace=tmp_path, task="expA",
                        walltime="48:00:00", cpus=99, mem_gb=999, gpus=0,
                        queue="default", name=None)
    assert rc == 0
    qsub = calls[0][-1]
    assert "qsub" in qsub and "walltime=24:00:00" in qsub
    assert "select=1:ncpus=16:mem=64gb" in qsub and "ngpus" not in qsub
    ledger = jobs.load_jobs(tmp_path)
    assert ledger[0]["job_id"] == "101.meta-pbs" and ledger[0]["attempt"] == 1

    # max_fix_attempts=1 → total ceiling 2 submits for the same task
    t2, _ = fake_transport([(0, "102.meta-pbs\n"), (0, "103.meta-pbs\n")])
    assert cli.cmd_submit(CFG_REMOTE, t2, "/storage/x/repo", "run.sh",
                          workspace=tmp_path, task="expA",
                          walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                          queue="default", name=None) == 0
    assert cli.cmd_submit(CFG_REMOTE, t2, "/storage/x/repo", "run.sh",
                          workspace=tmp_path, task="expA",
                          walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                          queue="default", name=None) == 4


def test_submit_concurrency_ceiling(tmp_path):
    script = [(0, f"{n}.meta\n") for n in (1, 2, 3)]
    t, _ = fake_transport(script)
    for n, task in [(1, "a"), (2, "b")]:
        assert cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "s.sh",
                              workspace=tmp_path, task=task,
                              walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                              queue="default", name=None) == 0
    assert cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "s.sh",
                          workspace=tmp_path, task="c",
                          walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                          queue="default", name=None) == 4


def test_submit_gpu_flag(tmp_path):
    t, calls = fake_transport([(0, "7.meta\n")])
    cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "s.sh", workspace=tmp_path,
                   task="g", walltime="01:00:00", cpus=1, mem_gb=1, gpus=1,
                   queue="default", name=None)
    assert "ngpus=1" in calls[0][-1]


def test_status_updates_ledger(tmp_path):
    t, calls = fake_transport([(0, "5.meta\n")])
    cli.cmd_submit(CFG_REMOTE, t, "/storage/x", "s.sh", workspace=tmp_path,
                   task="s", walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                   queue="default", name=None)
    t2, calls2 = fake_transport(
        [(0, "    job_state = F\n    Exit_status = 1\n")])
    assert cli.cmd_status(CFG_REMOTE, t2, "5.meta", workspace=tmp_path) == 0
    assert "qstat -xf 5.meta" in calls2[0][-1]
    assert jobs.load_jobs(tmp_path)[0]["state"] == "failed"


def test_fetch_dest_must_be_inside_workspace(tmp_path):
    t, calls = fake_transport([(0, "")])
    dest = tmp_path / "remote" / "data"
    assert cli.cmd_fetch(CFG_REMOTE, t, "/storage/x/out/", str(dest),
                         workspace=tmp_path) == 0
    assert calls[0][0] == "rsync"
    with pytest.raises(policy.PolicyError, match="inside the workspace"):
        cli.cmd_fetch(CFG_REMOTE, t, "/storage/x/out/", "/tmp/elsewhere",
                      workspace=tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_remote_cli.py -q`
Expected: FAIL — no module `remote.remote`.

- [ ] **Step 3: Implement remote.py**

```python
#!/usr/bin/env python3
"""Sanctioned CLI for remote (metacentrum) operations — AGENTS.md rule 11.

Every subcommand authorizes against config/remotes.yml (policy.py) BEFORE
any SSH. Exit codes: 0 ok, 1 remote command failed, 2 no Kerberos ticket,
3 policy refusal, 4 limit ceiling (attempts/concurrency).
"""

import argparse
import re
import sys
from pathlib import Path

from remote import jobs, policy, transport

STATE_MAP = {"Q": "queued", "H": "queued", "R": "running", "E": "running"}


def cmd_check(remote, t) -> int:
    if t.local(["klist", "-s"]).returncode != 0:
        print("NO_TICKET: no valid Kerberos ticket on this host — stop and "
              "ask the user to run `kinit` (agent must never authenticate).")
        return 2
    result = t.ssh("echo OK")
    if result.returncode != 0:
        print(f"SSH_FAILED: {result.stderr.strip()}")
        return 1
    print("OK")
    return 0


def cmd_pull(remote, t, remote_dir: str) -> int:
    policy.check_op(remote, "git-pull")
    d = policy.check_dir(remote, remote_dir)
    result = t.ssh(f"cd {d} && git pull --ff-only")
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
    return 0 if result.returncode == 0 else 1


def cmd_submit(remote, t, remote_dir: str, script: str, *, workspace: Path,
               task: str, walltime: str, cpus: int, mem_gb: int, gpus: int,
               queue: str, name: str | None) -> int:
    policy.check_op(remote, "qsub")
    d = policy.check_dir(remote, remote_dir)
    policy.check_queue(remote, queue)
    res, warnings = policy.clamp_resources(remote, walltime, cpus, mem_gb, gpus)
    for w in warnings:
        print(f"CLAMPED: {w}")

    ledger = jobs.load_jobs(workspace)
    lim = remote.limits
    if jobs.attempts(ledger, task) >= 1 + lim["max_fix_attempts"]:
        print(f"LIMIT: task '{task}' already submitted "
              f"{jobs.attempts(ledger, task)} times "
              f"(1 + max_fix_attempts={lim['max_fix_attempts']}) — mark it "
              "failed and checkpoint --reason anomaly.")
        return 4
    if jobs.active_count(ledger, remote.name) >= lim["max_concurrent_jobs"]:
        print(f"LIMIT: {lim['max_concurrent_jobs']} jobs already "
              f"queued/running on '{remote.name}' — wait before submitting.")
        return 4

    select = f"select=1:ncpus={res['cpus']}:mem={res['mem_gb']}gb"
    if res["gpus"] > 0:
        select += f":ngpus={res['gpus']}"
    jobname = name or Path(script).stem
    result = t.ssh(
        f"cd {d} && qsub -N {jobname} -q {queue} "
        f"-l walltime={res['walltime']} -l {select} {script}"
    )
    if result.returncode != 0:
        print(f"QSUB_FAILED: {result.stderr.strip()}", file=sys.stderr)
        return 1
    job_id = result.stdout.strip().splitlines()[-1]
    entry = jobs.record_submit(
        ledger, task=task, job_id=job_id, remote_name=remote.name,
        remote_dir=d, script=script, resources={**res, "queue": queue},
    )
    jobs.save_jobs(workspace, ledger)
    print(f"SUBMITTED: {job_id} attempt={entry['attempt']}")
    return 0


def cmd_status(remote, t, job_id: str, *, workspace: Path) -> int:
    policy.check_op(remote, "qstat")
    result = t.ssh(f"qstat -xf {job_id}")
    if result.returncode != 0:
        print(f"QSTAT_FAILED: {result.stderr.strip()}", file=sys.stderr)
        return 1
    state_m = re.search(r"job_state\s*=\s*(\w)", result.stdout)
    exit_m = re.search(r"Exit_status\s*=\s*(-?\d+)", result.stdout)
    if not state_m:
        print(f"UNPARSEABLE: {result.stdout[:200]}", file=sys.stderr)
        return 1
    code = state_m.group(1)
    if code == "F":
        state = "done" if exit_m and exit_m.group(1) == "0" else "failed"
    else:
        state = STATE_MAP.get(code, "queued")
    ledger = jobs.load_jobs(workspace)
    jobs.set_state(ledger, job_id, state)
    jobs.save_jobs(workspace, ledger)
    exit_status = exit_m.group(1) if exit_m else "-"
    print(f"STATE: {state} exit={exit_status}")
    return 0


def cmd_logs(remote, t, job_id: str, *, workspace: Path) -> int:
    policy.check_op(remote, "logs")
    ledger = jobs.load_jobs(workspace)
    entry = next((j for j in ledger if j["job_id"] == job_id), None)
    if entry is None:
        print(f"UNKNOWN_JOB: {job_id} not in jobs.yml", file=sys.stderr)
        return 1
    seq = job_id.split(".")[0]
    stem = Path(entry["script"]).stem
    result = t.ssh(
        f"cd {entry['dir']} && cat {stem}.o{seq} {stem}.e{seq} 2>/dev/null"
    )
    print(result.stdout, end="")
    return 0 if result.returncode == 0 else 1


def cmd_fetch(remote, t, src: str, dest: str, *, workspace: Path) -> int:
    policy.check_op(remote, "fetch")
    s = policy.check_dir(remote, src)
    if src.endswith("/"):
        s += "/"                      # preserve rsync dir-contents semantics
    dest_path = Path(dest).resolve()
    ws = Path(workspace).resolve()
    if not dest_path.is_relative_to(ws):
        raise policy.PolicyError(
            f"fetch destination must be inside the workspace: {dest}")
    dest_path.mkdir(parents=True, exist_ok=True)
    result = t.rsync_from(s, str(dest_path))
    if result.returncode != 0:
        print(f"RSYNC_FAILED: {result.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"FETCHED: {s} -> {dest_path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="remote.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "pull", "submit", "status", "logs", "fetch"):
        p = sub.add_parser(name)
        p.add_argument("remote")
        if name == "pull":
            p.add_argument("dir")
        if name == "submit":
            p.add_argument("dir")
            p.add_argument("script")
            p.add_argument("--task", required=True)
            p.add_argument("--walltime", default="01:00:00")
            p.add_argument("--cpus", type=int, default=1)
            p.add_argument("--mem-gb", type=int, default=4)
            p.add_argument("--gpus", type=int, default=0)
            p.add_argument("--queue", default="default")
            p.add_argument("--name")
        if name in ("status", "logs"):
            p.add_argument("job_id")
        if name == "fetch":
            p.add_argument("src")
            p.add_argument("dest")
        if name in ("submit", "status", "logs", "fetch"):
            p.add_argument("--workspace", type=Path, required=True)
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    try:
        remote = policy.load_remote(root, args.remote)
        t = transport.Transport(remote)
        if args.cmd == "check":
            policy.check_op(remote, "check")
            return cmd_check(remote, t)
        if args.cmd == "pull":
            return cmd_pull(remote, t, args.dir)
        if args.cmd == "submit":
            return cmd_submit(remote, t, args.dir, args.script,
                              workspace=args.workspace, task=args.task,
                              walltime=args.walltime, cpus=args.cpus,
                              mem_gb=args.mem_gb, gpus=args.gpus,
                              queue=args.queue, name=args.name)
        if args.cmd == "status":
            return cmd_status(remote, t, args.job_id, workspace=args.workspace)
        if args.cmd == "logs":
            return cmd_logs(remote, t, args.job_id, workspace=args.workspace)
        if args.cmd == "fetch":
            return cmd_fetch(remote, t, args.src, args.dest,
                             workspace=args.workspace)
    except policy.PolicyError as exc:
        print(f"POLICY: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests, then the whole suite**

Run: `uv run pytest tests/test_remote_cli.py -q` → PASS.
Run: `uv run pytest -q` → all PASS.
Also smoke the argparse path without a config:
`uv run scripts/remote/remote.py check meta` → prints `POLICY: ... remotes.example.yml ...`, exit 3.

- [ ] **Step 5: Commit**

```bash
git add scripts/remote/remote.py tests/test_remote_cli.py
git commit -m "feat: remote CLI — check/pull/submit/status/logs/fetch with enforced limits"
```

---

### Task 5: remote-exec skill, AGENTS.md rule, experiment-cycle backend

**Files:**
- Create: `skills/remote-exec/SKILL.md`
- Modify: `AGENTS.md` (add hard rule 11; add skill table row)
- Modify: `skills/experiment-cycle/SKILL.md` (backend routing section at the top)

**Interfaces:**
- Consumes: the CLI contract from Task 4 (exact invocations and exit codes).

- [ ] **Step 1: Write skills/remote-exec/SKILL.md**

````markdown
---
name: remote-exec
description: Submit, monitor, fix, and fetch PBS jobs on a configured remote (metacentrum) through scripts/remote/remote.py — Kerberos preflight, snakemake dry-run gate, git-only fix loop, bounded retries.
---

# Remote Execution Protocol (metacentrum)

You are the **coordinator**. ALL remote access goes through
`uv run scripts/remote/remote.py ...` (AGENTS.md rule 11) — never raw
`ssh`/`scp`. The user's `config/remotes.yml` is the authority on what you
may touch; a `POLICY:` refusal (exit 3) is a hard boundary: report it,
never work around it. `<remote>` below is the remote's name in that file
(e.g. `meta`); `WS` = `workspace/<slug>`.

## Setup (once per host, user-facing)

- The user copies `config/remotes.example.yml` → `config/remotes.yml` and
  fills in host, user, allowed_dirs, allowed_ops, limits. Offer to walk
  them through it; never fill in paths you were not given.
- Optional hardening: suggest the user add Claude Code permission
  deny-rules for bare `ssh`/`scp`/`rsync` so the wrapper is the only path.

## Preflight (every session, before anything else)

1. `uv run scripts/remote/remote.py check <remote>`
2. Exit 2 (`NO_TICKET`) → STOP. Tell the user to run `kinit` and wait.
   Never attempt any authentication yourself.
3. Exit 1 → retry once; still failing → report to the user and stop.

## Task loop (per campaign task; autonomous within an approved campaign —
AGENTS.md rule 3 delegation applies, bounded by config/remotes.yml limits)

1. **Sync**: `remote.py pull <remote> <dir>` for each remote repo dir the
   task needs. Pull failure (dirty tree, diverged) → report; do not force.
2. **Snakemake gate** (MANDATORY when the task runs a Snakemake pipeline —
   most of the user's workloads):
   - Submit a dry-run first: the pipeline's runner script with
     `snakemake -n` semantics, `--gpus 0`, minimal resources
     (e.g. `--cpus 1 --mem-gb 4 --walltime 00:30:00`), task name
     `<task>-dryrun`.
   - Wait for it; `remote.py logs` must show a clean exit AND the full
     job DAG. Save that output to `WS/remote/dag-<task>.txt` — it is the
     execution plan of record.
   - Dry-run failed → enter the fix loop below (dry-run attempts count
     against the same task ceiling).
   - Only after a clean DAG: submit the real run with real resources.
3. **Submit**:
   `remote.py submit <remote> <dir> <script> --workspace WS --task <task>
   --walltime ... --cpus ... --mem-gb ... [--gpus N] [--queue Q]`
   Heed `CLAMPED:` warnings — if a clamp likely breaks the job (e.g.
   walltime halved), tell the user instead of submitting blind.
   Exit 4 (`LIMIT:`) → concurrency: wait and poll; attempts: go to
   "Exhaustion" below.
4. **Monitor**: poll `remote.py status <remote> <job_id> --workspace WS`
   with backoff (start ~2 min, double to ~15 min cap; long jobs need no
   tight polling). `STATE: done` → step 6. `STATE: failed` → step 5.
5. **Fix loop** (git-only — never edit files on the remote):
   a. `remote.py logs <remote> <job_id> --workspace WS`; read the error.
   b. Diagnose honestly. Fix the script/pipeline in the LOCAL clone of
      that repo, commit with a message naming the job id, push.
   c. `remote.py pull`, then resubmit (same `--task` name — the ledger
      counts attempts). Snakemake tasks re-enter the dry-run gate.
   d. Never "fix" by deleting checks, silencing errors, or shrinking the
      experiment to make it pass — that is rerun-until-green (AGENTS.md
      rule 5 anomaly policy applies).
6. **Fetch**: `remote.py fetch <remote> <dir>/results/ WS/remote/data/<task>/
   --workspace WS`. Analyze locally; quantitative claims cite fetched
   artifacts by path (provenance, AGENTS.md rule 9). If analysis warrants
   more experiments, new campaigns go through the normal approval
   contract, then re-enter this loop.

## Exhaustion and anomalies

- Attempt ceiling hit (exit 4 on submit, or `max_fix_attempts` fixes
  spent): mark the task `failed` in `status.yml`, run
  `uv run scripts/checkpoint.py --reason anomaly`, report what you tried
  and the last error. Never shop for workarounds past the ceiling.
- Job stuck queued far beyond expectation: report to the user; never
  qdel (not an allowed op) or resubmit a duplicate.
- Every submit/status/fix/fetch gets a line in `WS/log.md`; budget: record
  each real (non-dry-run) job as an experiment run (`budget.py`).
````

- [ ] **Step 2: Add AGENTS.md rule 11 and skill row**

Append to the Hard rules list in `AGENTS.md`:

```markdown
11. Remote execution (metacentrum) goes ONLY through
    `uv run scripts/remote/remote.py` per `skills/remote-exec/SKILL.md` —
    never raw `ssh`/`scp`. `config/remotes.yml` (user-owned,
    deny-by-default) bounds every directory, operation, and resource; a
    `POLICY:` refusal is a hard boundary. Kerberos is the user's: on
    `NO_TICKET`, stop and ask them to `kinit`. Fixes reach the remote via
    git (local edit → push → pull), never direct remote edits.
```

Add to the Skills table:

```markdown
| Run jobs on metacentrum | `skills/remote-exec/SKILL.md` |
```

- [ ] **Step 3: Add backend routing to experiment-cycle**

In `skills/experiment-cycle/SKILL.md`, insert after the first paragraph:

```markdown
## Backend selection

The campaign YAML may set `backend: local` (default) or `backend: remote`
(+ `remote: meta`, per-task resource requests). `local` → dispatch into
ExperimentX as below. `remote` → drive the campaign's tasks yourself via
`skills/remote-exec/SKILL.md` (pull, snakemake dry-run gate, submit,
monitor, fix, fetch), then write the same results-summary contract to
`workspace/<slug>/iterations/<n>/results-summary.md` (campaign name, runs
count, failed count, metrics table, best configuration, anomalies) so the
rest of the loop is backend-agnostic. Budget: each real remote job counts
as one experiment run.
```

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest -q` → all PASS (docs only).

```bash
git add skills/remote-exec/SKILL.md AGENTS.md skills/experiment-cycle/SKILL.md
git commit -m "feat: remote-exec skill, hard rule 11, experiment backend routing"
```

---

### Task 6: live smoke test (manual, with the user)

Not automatable offline — do this interactively with the user present.

- [ ] **Step 1:** User creates `config/remotes.yml` from the example with one real allowed dir.
- [ ] **Step 2:** `uv run scripts/remote/remote.py check meta` → OK (user has a ticket).
- [ ] **Step 3:** `pull` the allowed dir; `submit` a trivial `echo` script with 5-minute walltime into a scratch workspace; `status` until done; `logs` shows the echo; `fetch` its output dir.
- [ ] **Step 4:** Negative check: try `pull` on a path outside `allowed_dirs` → `POLICY:` exit 3.
- [ ] **Step 5:** Record any metacentrum-specific quirks (qsub output format, log file naming) as fixes with tests, and commit.
