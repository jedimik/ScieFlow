"""Egress rules of the data-protection model (M-B Part A 5.7).

Every egress point asks here before anything leaves the machine or enters a run:

- a literature or scite request for an L2 run needs its service in `l2_providers`;
- a remote push from an L2 run, or from a folder that holds one, is refused unless the remote
  is in `l2_remotes` or the source is a `released/` copy of that run (C1: decided from paths);
- a fetch from a remote in `l2_remotes` into a run below L2 records a data.warning
  (`l2-remote-fetch`), or is refused under `enforcement: refuse`.

An invalid config/data-policy.yml refuses every such request (M-B-D5). Messages name runs,
services and remotes, never a value read from a data file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scieflow.core.data import levels, policy
from scieflow.core.data.dispatch import report_findings
from scieflow.core.data.scan import Finding

L2_REMOTE_FETCH = "l2-remote-fetch"


class EgressRefused(Exception):
    """A boundary: the request is not sent. The message names the run, service or remote."""


def root_of_run(ws) -> Path:
    """The project root of a run directory (workspace/<slug>)."""
    return Path(ws).resolve().parent.parent


def _load(root):
    try:
        return policy.load(root)
    except policy.PolicyInvalid as exc:
        raise EgressRefused(f"{exc}; every literature, scite, NotebookLM and remote request "
                            "refuses while the policy is invalid (M-B-D5)") from None


def runs_touched(root, path) -> list[Path]:
    """Run directories that `path` lies inside, or contains (a folder that holds runs)."""
    target = Path(path).resolve()
    hits = []
    for run in levels.run_dirs(root):
        real = run.resolve()
        if target == real or real in target.parents or target in real.parents:
            hits.append(run)
    return hits


def service_check(root, ws, service: str) -> None:
    """Refuse a request to `service` made for run `ws` when the run is L2 and the service is
    not in l2_providers. A run below L2 is never refused here (except by an invalid policy)."""
    pol = _load(root)
    if levels.run_level(ws) != "L2":
        return
    if service in pol.l2_providers:
        return
    raise EgressRefused(f"data protection: run {Path(ws).name} is L2; {service} is not in "
                        "l2_providers (config/data-policy.yml)")


def push_check(root, source, remote_name: str) -> None:
    """Refuse a push of `source` to `remote_name` when an L2 run lies inside or in `source`,
    unless the remote is in l2_remotes or the source lies under that run's released/ folder."""
    pol = _load(root)
    if remote_name in pol.l2_remotes:
        return
    src = Path(source).resolve()
    for run in runs_touched(root, src):
        if levels.run_level(run) != "L2":
            continue
        if src.is_relative_to((run / "released").resolve()):
            continue
        raise EgressRefused(
            f"data protection: an L2 run ({run.name}) would leave the workspace by push to "
            f"{remote_name}; only its released/ copies may leave, or a remote listed in "
            "l2_remotes (config/data-policy.yml)")


def fetch_check(root, dest, remote_name: str) -> None:
    """A fetch from a remote in l2_remotes into a run below L2: a data.warning in that run, or a
    refusal under enforcement: refuse. Nothing is recorded for any other fetch."""
    pol = _load(root)
    if remote_name not in pol.l2_remotes:
        return
    dest_path = Path(dest).resolve()
    for run in runs_touched(root, dest_path):
        if levels.run_level(run) == "L2":
            continue
        message = (f"{L2_REMOTE_FETCH}: fetched from {remote_name}, which is in l2_remotes; "
                   f"declare the fetched data in run {run.name} (scieflow data declare)")
        if pol.enforcement == "refuse":
            raise EgressRefused(message)
        rel = dest_path.relative_to(run.resolve()).as_posix()
        report_findings(run, [Finding(rel, L2_REMOTE_FETCH, "fetched from a remote in l2_remotes")],
                        enforcement="warn")
        return
