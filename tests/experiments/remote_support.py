"""M-D3 t3 fixtures: a project with a run, a remote, a tiny stage and a campaign (synthetic data only).

The fake transport records every argv and answers from a small script. No test here reaches a real
host: the remote is either the fake transport (pbs) or the `local` adapter on this machine (smoke).
"""

from __future__ import annotations

import os
import signal
from pathlib import Path
from types import SimpleNamespace

import yaml

from scieflow.core.run import budget, status

STAMP = "20261102T000000Z"
CAMPAIGN_ID = f"gain-sweep-{STAMP}"
REMOTE_DIR = f"/data/x/{CAMPAIGN_ID}"

STAGE_YAML = (
    "name: scale\ndescription: pure-python test stage\nscript: run.py\noutput_file: result.txt\n"
    "environment:\n  pip: []\n"
)
# Writes <output>/result.txt from the input and the gain. No third-party imports.
STAGE_PY = """\
import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--input", required=True)
p.add_argument("--output", required=True)
p.add_argument("--gain", type=float, default=1.0)
a = p.parse_args()
Path(a.output, "result.txt").write_text(Path(a.input).read_text() + f"gain={a.gain}\\n")
"""

PBS_LIMITS = {
    "max_walltime": "24:00:00", "max_cpus": 16, "max_mem_gb": 64, "max_gpus": 1,
    "max_scratch_gb": 100, "scratch_types": ["scratch_ssd"], "queues": ["default"],
    "max_concurrent_jobs": 4, "max_fix_attempts": 3,
}


def pbs_remote(**overrides) -> dict:
    entry = {
        "host": "meta.example.org", "user": "u", "auth": "kerberos", "scheduler": "pbs",
        "allowed_dirs": ["/data/x"],
        "allowed_ops": ["check", "mkdir", "rsync", "fetch", "qsub", "qstat"],
        "limits": dict(PBS_LIMITS),
    }
    entry.update(overrides)
    return entry


def local_remote(allowed_dir: str) -> dict:
    limits = dict(PBS_LIMITS, scratch_types=["tmp"], max_scratch_gb=0, max_gpus=0,
                  max_concurrent_jobs=4, max_fix_attempts=3)
    return {
        "host": "localhost", "user": "local", "auth": "none", "scheduler": "local",
        "allow_experimental_scheduler": True, "allowed_dirs": [allowed_dir],
        "allowed_ops": ["check", "mkdir", "rsync", "fetch", "submit", "status"],
        "limits": limits,
    }


class Project:
    """A test project: root, the run folder, and the campaign file (plain class, file-loaded module)."""

    def __init__(self, root: Path, ws: Path, campaign: Path):
        self.root = root
        self.ws = ws
        self.campaign = campaign

    @property
    def experiments(self) -> Path:
        return self.ws / "experiments"

    @property
    def campaign_dir(self) -> Path:
        return self.experiments / "gain-sweep"


def make_project(tmp_path: Path, *, remote: dict | None = None, remotes_file: bool = True,
                 n_gains: int = 2, cap: int | None = 100, spent: int = 0, jobs: str | None = None,
                 resources: dict | None = None, in_run: bool = True, input_name: str = "in.txt",
                 remote_name: str = "meta", campaign_remote: dict | None = None) -> Project:
    """A project root with config/remotes.yml, one run `r1`, a stage and a remote campaign file."""
    root = tmp_path / "project"
    root.mkdir()
    if remotes_file:
        (root / "config").mkdir()
        entry = remote if remote is not None else pbs_remote()
        (root / "config" / "remotes.yml").write_text(
            yaml.safe_dump({"remotes": {"meta": entry}}))
    stage = root / "pipelines" / "demo" / "stages" / "scale"
    stage.mkdir(parents=True)
    (stage / "stage.yaml").write_text(STAGE_YAML)
    (stage / "run.py").write_text(STAGE_PY)
    data = root / "pipelines" / "demo" / "data"
    data.mkdir(parents=True)
    (data / input_name).write_text("synthetic input\n")
    campaigns = root / "pipelines" / "demo" / "campaigns"
    campaigns.mkdir(parents=True)
    gains = [round(0.5 + 0.5 * i, 2) for i in range(n_gains)]
    block = {"name": remote_name, "dir": "/data/x"}
    if jobs is not None:
        block["jobs"] = jobs
    if campaign_remote is not None:
        block = campaign_remote
    data_doc = {
        "name": "gain-sweep", "pipeline": "demo", "stage": "scale",
        "input": f"../data/{input_name}", "backend": "remote", "remote": block,
        "parameters": {"gain": {"grid": gains}}, "metrics": ["score"],
    }
    if resources is not None:
        data_doc["resources"] = resources
    campaign = campaigns / "gain-sweep.yaml"
    campaign.write_text(yaml.safe_dump(data_doc))
    ws = root / "workspace" / "r1"
    if in_run:
        ws.mkdir(parents=True)
        status.write_status(ws, status.new_status("r1", "autonomous"))
        budget_doc = budget.new_budget(10, cap or 0, 600)
        if spent:
            budget_doc = budget.record(budget_doc, experiment_runs=spent)
        budget.write_budget(ws, budget_doc)
    else:
        ws = root / "elsewhere"
        ws.mkdir()
    return Project(root, ws, campaign)


class FakeRemote:
    """A recording runner for transport.Transport. Answers by argv shape; never runs anything."""

    def __init__(self, *, klist_rc: int = 0, ticket_rc: int = 0, mkdir_rc: int = 0,
                 rsync_rc: int = 0, qsub_rc: int | list[int] = 0):
        self.calls: list[list[str]] = []
        self.klist_rc = klist_rc
        self.ticket_rc = ticket_rc
        self.mkdir_rc = mkdir_rc
        self.rsync_rc = rsync_rc
        self._qsub_rc = list(qsub_rc) if isinstance(qsub_rc, list) else None
        self.qsub_rc = 0 if isinstance(qsub_rc, list) else qsub_rc
        self.qsub_count = 0

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        if argv == ["klist", "-s"]:
            return _result(self.klist_rc)
        if argv[0] == "rsync":
            return _result(self.rsync_rc)
        if argv[0] == "ssh":
            cmd = argv[-1]
            if "klist -s; then" in cmd:
                if self.ticket_rc:
                    return _result(self.ticket_rc, "", "REMOTE_NO_TICKET")
                return _result(0, "REMOTE_TICKET_OK\n")
            if cmd.startswith("mkdir -p"):
                return _result(self.mkdir_rc)
            if " qsub " in cmd:
                rc = self._next_qsub_rc()
                self.qsub_count += 1 if rc == 0 else 0
                if rc:
                    return _result(rc, "", "qsub: refused")
                return _result(0, f"{100 + self.qsub_count}.meta.example.org\n")
        return _result(0)

    def _next_qsub_rc(self) -> int:
        if self._qsub_rc is None:
            return self.qsub_rc
        return self._qsub_rc.pop(0) if self._qsub_rc else 0

    def ssh_commands(self) -> list[str]:
        return [c[-1] for c in self.calls if c and c[0] == "ssh"]

    def qsubs(self) -> list[str]:
        return [c for c in self.ssh_commands() if " qsub " in c]


def _result(rc: int, out: str = "", err: str = ""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def read_remote_json(campaign_dir: Path) -> dict:
    import json
    return json.loads((campaign_dir / "remote.json").read_text())


def read_events(ws: Path) -> list[dict]:
    from scieflow.core import events
    return events.read(ws)


def kill_local_sessions(job_ids: list[str]) -> None:
    """Teardown for the local adapter: kill each job's session (ids are <pid>.<epoch>)."""
    for job_id in job_ids:
        try:
            pid = int(job_id.split(".")[0])
            os.killpg(pid, signal.SIGKILL)
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass


__all__ = ["CAMPAIGN_ID", "REMOTE_DIR", "STAMP", "FakeRemote", "Project", "make_project",
           "pbs_remote", "local_remote", "read_remote_json", "read_events", "kill_local_sessions"]
