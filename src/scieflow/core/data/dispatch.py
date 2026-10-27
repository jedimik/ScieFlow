"""The dispatch decision (M-B Part A 5.4): one read, no writes, no launch.

The three dispatch paths (`agent_run.prepare` for the CLI, `agent_run.resolve_turn` for a
conversation turn, both reached through `service`) call `decide` after the final agent
configuration and the sandbox on/off state are known, and before argv or the layout is built.
A refusal raises `DataRefused`, which no existing handler catches (it is not a ValueError,
OSError, RuntimeError, SandboxError or DispatchError). The caller then records `job.refused`
(`record_refusal`) and exits or raises. On success the caller records `data.level_resolved`
and one `data.warning` per warning (`record_decision`).

Events and values: messages name paths, columns and rule ids, never a value read from a
data file (M-B-D9, D10).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from scieflow.core import events, network, sandbox
from scieflow.core.data import levels, policy, scan
from scieflow.core.data.scan import Finding

# A human answer that approves external sharing of an L1 run (5.4 rule l1-external-sharing).
EXTERNAL_SHARING_APPROVALS = ("yes", "approve", "approved", "share")
# Warn-mode dedupe of data.warning events, keyed by (path, size, mtime_ns, rule) (5.6).
REPORTED = Path("scratch") / "data-scan-reported.json"
SCAN_LIMIT_DETAIL = "scan stopped at a limit before every file was inspected (config/data-policy.yml)"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule: str | None          # the refusal rule id, or None when allowed
    message: str              # names the rule's subject; never a data value
    level: str                # the run's level (L0 when there is no run)
    boundary: str             # "safe" or "cloud"
    endpoint: str             # the endpoint a safe agent is handed; "" for cloud
    data_hidden: tuple = ()   # absolute paths the sandbox must hide (t3 enforces them)
    protected: tuple = ()     # absolute paths the sandbox must keep read-only (t3)
    warnings: tuple = ()      # Finding tuples; recorded as data.warning
    enforcement: str = "warn"
    agent: str = ""           # the agent the decision is about (set by decide)


class DataRefused(Exception):
    """A dispatch refused by the data-protection rules; carries the decision."""

    def __init__(self, decision: Decision):
        super().__init__(decision.message)
        self.decision = decision


def uses_endpoint(cfg: dict) -> bool:
    """True when any command template of the agent names the {endpoint} placeholder."""
    return any("{endpoint}" in str(cfg[key]) for key in policy.TEMPLATE_KEYS if cfg.get(key))


def refusal_text(agent: str, decision: Decision) -> str:
    return f"{agent}: data protection refused ({decision.rule}): {decision.message}"


def record_refusal(run_dir: Path | None, agent: str, decision: Decision) -> None:
    """The one job.refused event of a data refusal; no job exists for it."""
    if run_dir is None:
        return
    events.emit(run_dir, "job.refused", "system", reason="data-level", rule=decision.rule,
                level=decision.level, agent=agent, boundary=decision.boundary)


def record_decision(run_dir: Path | None, agent: str, decision: Decision | None) -> None:
    """data.level_resolved and one data.warning per new warning, after an allowed dispatch.

    An unlabelled default (an L0 run dispatched to a cloud agent, with nothing hidden from it and
    nothing protected) records no level event, so the event stream of every existing plain
    dispatch is unchanged (M-B t2 deviation). Any hidden or protected path is recorded.
    """
    if run_dir is None or decision is None:
        return
    plain_default = ((decision.level, decision.boundary) == ("L0", "cloud")
                     and not decision.data_hidden and not decision.protected)
    if not plain_default:
        events.emit(run_dir, "data.level_resolved", "system", level=decision.level, agent=agent,
                    boundary=decision.boundary, hidden=len(decision.data_hidden),
                    protected=len(decision.protected))
    report_findings(run_dir, decision.warnings, enforcement=decision.enforcement)


def _report_key(ws: Path, finding: Finding) -> str:
    try:
        st = (ws / finding.path).stat()
        stamp = [st.st_size, st.st_mtime_ns]
    except OSError:
        stamp = [-1, -1]
    return json.dumps([finding.path, *stamp, finding.rule])


def _load_reported(ws: Path) -> dict:
    """The dedupe map. A symbolic link anywhere on its path is ignored (the agent can write scratch)."""
    if (ws / "scratch").is_symlink() or (ws / REPORTED).is_symlink() or not (ws / REPORTED).is_file():
        return {}
    try:
        raw = json.loads((ws / REPORTED).read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_reported(ws: Path, reported: dict) -> None:
    if (ws / "scratch").is_symlink():
        return
    target = ws / REPORTED
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".data-scan-", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(reported, sort_keys=True))
    os.replace(tmp, target)


def report_findings(ws: Path | None, findings, *, enforcement: str = "warn") -> int:
    """One data.warning per finding. Under `warn`, a finding already reported for the same file
    state (path, size, mtime, rule) is not repeated. Under `refuse` the dedupe map is never read
    (M-B t4). Returns the number of events written."""
    if ws is None or not findings:
        return 0
    ws = Path(ws)
    dedupe = enforcement == "warn"
    reported = _load_reported(ws) if dedupe else {}
    written = 0
    for finding in findings:
        key = _report_key(ws, finding)
        if dedupe and key in reported:
            continue
        event = events.emit(ws, "data.warning", "system", rule=finding.rule, path=finding.path,
                            detail=finding.detail)
        if dedupe:
            reported[key] = event["id"]
        written += 1
    if dedupe and written:
        _save_reported(ws, reported)
    return written


def _external_sharing_cleared(ws: Path) -> bool:
    """A human-answered external-sharing gate with an approving answer (fail closed)."""
    from scieflow.core import gates

    try:
        answered = gates.list_gates(ws, state="answered")
    except (OSError, ValueError):
        return False
    return any(gate.get("kind") == "external-sharing" and gate.get("answered_by") == "human"
               and gate.get("answer") in EXTERNAL_SHARING_APPROVALS for gate in answered)


def _outermost(paths) -> tuple:
    """Drop every path that lies inside another listed directory (the outer overlay covers it)."""
    kept: list[Path] = []
    for path in sorted(set(paths), key=lambda p: len(p.parts)):
        if not any(path == other or other in path.parents for other in kept):
            kept.append(path)
    return tuple(kept)


def _scan_message(slug: str, found) -> str:
    """Names paths and rule ids, never a value (M-B-D9)."""
    named = sorted({(f.path, f.rule) for f in found.findings})
    listed = "; ".join(f"{path} ({rule})" for path, rule in named[:5])
    more = f" and {len(named) - 5} more" if len(named) > 5 else ""
    return (f"run {slug}: the scan found data that looks like patient data: {listed}{more}; "
            "declare it with `scieflow data declare` or review config/data-policy.yml")


def decide(project, run_dir: Path | None, agent: str, final_cfg: dict, *, sandboxed: bool,
           templates_use_endpoint: bool) -> Decision:
    """The data-protection decision for one dispatch. Pure apart from reading files."""
    return replace(_decide(project, run_dir, agent, final_cfg, sandboxed=sandboxed,
                           templates_use_endpoint=templates_use_endpoint), agent=agent)


def _decide(project, run_dir: Path | None, agent: str, final_cfg: dict, *, sandboxed: bool,
            templates_use_endpoint: bool) -> Decision:
    root = Path(project.root).resolve()
    ws = Path(run_dir) if run_dir is not None else None
    slug = ws.name if ws is not None else ""
    level = levels.run_level(ws) if ws is not None else "L0"
    try:
        pol = policy.load(root)
    except policy.PolicyInvalid as exc:
        return Decision(False, "policy-invalid", str(exc), level, "cloud", "")

    areas: list = []
    if agent in pol.safe_agents:
        try:
            areas = policy.writable_grants(root)
        except (OSError, ValueError, sandbox.SandboxError) as exc:
            return Decision(False, "policy-invalid",
                            f"config/sandbox.yml cannot be read for the agent boundary "
                            f"({type(exc).__name__})", level, "cloud", "")
    bound = policy.boundary(pol, agent, final_cfg, root=root, writable_areas=areas)

    def refuse(rule: str, message: str) -> Decision:
        return Decision(False, rule, message, level, bound.kind, "", enforcement=pol.enforcement)

    if templates_use_endpoint and bound.kind == "cloud":
        return refuse("endpoint-without-safe-entry",
                      f"{agent} names the endpoint placeholder but is not a pinned safe agent "
                      f"({bound.reason}); the placeholder is never filled for a cloud agent")
    if level == "L2" and bound.kind == "cloud":
        return refuse("l2-cloud",
                      f"run {slug} is L2; {agent} is a cloud agent ({bound.reason}); only "
                      "pinned safe agents may run in an L2 run (config/data-policy.yml)")

    other_l2 = levels.l2_runs(root, slug if ws is not None else None)
    project_l2 = levels.project_l2_paths(root, pol)
    if not sandboxed and bound.kind == "cloud" and (other_l2 or project_l2):
        return refuse("l2-unsandboxed",
                      f"{agent} would run unsandboxed while the project holds L2 data "
                      f"({len(other_l2)} other L2 run(s), {len(project_l2)} L2 folder(s)); "
                      "dispatch it sandboxed")

    warnings: list[Finding] = []
    if (level == "L1" and bound.kind == "cloud" and ws is not None
            and not _external_sharing_cleared(ws)):
        finding = Finding(".", "l1-external-sharing",
                          f"run {slug} is L1; a cloud agent may receive it only after a human "
                          "approves external sharing (gate external-sharing)")
        if pol.enforcement == "refuse":
            return refuse("l1-external-sharing", finding.detail)
        warnings.append(finding)

    if bound.kind == "cloud" and level != "L2" and ws is not None:
        found = scan.scan(ws, pol)
        if found.findings or found.truncated:
            if pol.enforcement == "refuse":
                if found.findings:
                    return refuse("scan-warning", _scan_message(slug, found))
                return refuse("scan-incomplete", SCAN_LIMIT_DETAIL)
            warnings.extend(found.findings)
            if found.truncated:
                warnings.append(Finding(".", "scan-incomplete", SCAN_LIMIT_DETAIL))

    hidden: tuple = ()
    protected: tuple = ()
    if bound.kind == "cloud":
        hidden = _outermost(
            [root / "workspace" / name for name in other_l2] + list(project_l2))
    if ws is not None:
        items = []
        data_file = ws / levels.DATA_FILE
        if data_file.is_file() and not data_file.is_symlink():
            items.append(data_file.resolve())
        released = ws / "released"
        if released.is_dir() and not released.is_symlink():
            items.append(released.resolve())
        net_file = ws / network.FILE
        if net_file.is_file() and not net_file.is_symlink():
            items.append(net_file.resolve())
        snaps = ws / "snapshots"
        if network.replays(ws) and snaps.is_dir() and not snaps.is_symlink():
            items.append(snaps.resolve())
        protected = tuple(items)
    return Decision(True, None, "", level, bound.kind, bound.endpoint, hidden, protected,
                    tuple(warnings), pol.enforcement)
