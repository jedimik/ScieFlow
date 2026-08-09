#!/usr/bin/env python3
"""Sanctioned CLI for remote (metacentrum) operations — AGENTS.md rule 11.

Every subcommand authorizes against config/remotes.yml (policy.py) BEFORE
any SSH. Exit codes: 0 ok, 1 remote command failed, 2 no Kerberos ticket,
3 policy refusal, 4 limit ceiling (attempts/concurrency).
"""

import argparse
import re
import shlex
import sys
from pathlib import Path

# When run directly (`uv run scripts/remote/remote.py ...`), the
# interpreter puts this file's own directory (scripts/remote/) at the
# front of sys.path, so a bare `import remote` resolves to this very
# file instead of the `remote` package — a self-shadowing collision.
# Put scripts/ ahead of it so `remote` resolves to the package.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from remote import jobs, policy, transport

STATE_MAP = {"Q": "queued", "H": "queued", "R": "running", "E": "running"}


def cmd_check(remote, t) -> int:
    policy.check_op(remote, "check")
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


def cmd_pull(remote, t, remote_dir: str, *, branch: str | None = None) -> int:
    policy.check_op(remote, "git-pull")
    policy.check_op(remote, "git-status")
    d = policy.check_dir(remote, remote_dir)
    status = t.ssh(
        f"cd {shlex.quote(d)} && "
        "git status --porcelain --untracked-files=no"
    )
    if status.returncode != 0:
        print(status.stderr.strip(), file=sys.stderr)
        return 1
    if status.stdout.strip():
        print("DIRTY_TRACKED: refusing to pull or switch a remote repository "
              "with tracked changes", file=sys.stderr)
        print(status.stdout, end="", file=sys.stderr)
        return 1

    if branch is not None:
        policy.check_op(remote, "git-switch")
        branch = policy.check_token(branch, "branch")
        command = (
            f"cd {shlex.quote(d)} && git switch {shlex.quote(branch)} && "
            f"git pull --ff-only origin {shlex.quote(branch)} && "
            "printf 'BRANCH: ' && git branch --show-current && "
            "printf 'SHA: ' && git rev-parse HEAD"
        )
    else:
        command = (
            f"cd {shlex.quote(d)} && git pull --ff-only && "
            "printf 'BRANCH: ' && git branch --show-current && "
            "printf 'SHA: ' && git rev-parse HEAD"
        )
    result = t.ssh(command)
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
    elif branch is not None and f"BRANCH: {branch}\n" not in result.stdout:
        print(f"BRANCH_MISMATCH: requested '{branch}'", file=sys.stderr)
        return 1
    return 0 if result.returncode == 0 else 1


def cmd_submit(remote, t, remote_dir: str, script: str, *, workspace: Path,
               task: str, walltime: str, cpus: int, mem_gb: int, gpus: int,
               queue: str, name: str | None, scratch_type: str = "none",
               scratch_gb: int = 0,
               environment_assignments: list[str] | None = None) -> int:
    policy.check_op(remote, "qsub")
    d = policy.check_dir(remote, remote_dir)
    policy.check_queue(remote, queue)
    policy.check_token(task, "task name")
    policy.check_script(script)
    if name is not None:
        policy.check_token(name, "job name")
    environment = policy.check_environment_assignments(
        environment_assignments or []
    )
    res, warnings = policy.clamp_resources(
        remote,
        walltime,
        cpus,
        mem_gb,
        gpus,
        scratch_type,
        scratch_gb,
    )
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
    if res["scratch_gb"] > 0:
        select += f":{res['scratch_type']}={res['scratch_gb']}gb"
    if res["gpus"] > 0:
        select += f":ngpus={res['gpus']}"
    jobname = name or Path(script).stem
    env_arg = ""
    if environment:
        joined = ",".join(f"{key}={value}" for key, value in environment.items())
        env_arg = f"-v {shlex.quote(joined)} "
    result = t.ssh(
        f"cd {shlex.quote(d)} && qsub -N {shlex.quote(jobname)} "
        f"-q {shlex.quote(queue)} "
        f"-l walltime={shlex.quote(res['walltime'])} "
        f"-l {shlex.quote(select)} {env_arg}{shlex.quote(script)}"
    )
    if result.returncode != 0:
        print(f"QSUB_FAILED: {result.stderr.strip()}", file=sys.stderr)
        return 1
    lines = result.stdout.strip().splitlines()
    if not lines or not lines[-1].strip():
        print("QSUB_FAILED: qsub returned success but no job id", file=sys.stderr)
        return 1
    job_id = lines[-1].strip()
    entry = jobs.record_submit(
        ledger, task=task, job_id=job_id, remote_name=remote.name,
        remote_dir=d, script=script, resources={**res, "queue": queue},
        environment=environment, job_name=jobname,
    )
    jobs.save_jobs(workspace, ledger)
    print(f"SUBMITTED: {job_id} attempt={entry['attempt']}")
    return 0


def cmd_status(remote, t, job_id: str, *, workspace: Path) -> int:
    policy.check_op(remote, "qstat")
    policy.check_token(job_id, "job id")
    result = t.ssh(f"qstat -xf {shlex.quote(job_id)}")
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
    policy.check_token(job_id, "job id")
    ledger = jobs.load_jobs(workspace)
    entry = next((j for j in ledger if j["job_id"] == job_id), None)
    if entry is None:
        print(f"UNKNOWN_JOB: {job_id} not in jobs.yml", file=sys.stderr)
        return 1
    # Ledger fields were validated on submit, but re-check: the ledger file
    # is agent-writable, so treat it as untrusted before shelling out.
    d = policy.check_dir(remote, entry["dir"])
    seq = policy.check_token(job_id.split(".")[0], "job sequence")
    # PBS names default output files from `qsub -N`, which may differ from the
    # submitted script stem. Older ledger entries predate job_name recording.
    stem = policy.check_token(
        entry.get("job_name", Path(entry["script"]).stem), "job name"
    )
    result = t.ssh(
        f"cd {shlex.quote(d)} && cat "
        f"{shlex.quote(f'{stem}.o{seq}')} {shlex.quote(f'{stem}.e{seq}')} "
        "2>/dev/null"
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
            p.add_argument("--branch")
        if name == "submit":
            p.add_argument("dir")
            p.add_argument("script")
            p.add_argument("--task", required=True)
            p.add_argument("--walltime", default="01:00:00")
            p.add_argument("--cpus", type=int, default=1)
            p.add_argument("--mem-gb", type=int, default=4)
            p.add_argument("--gpus", type=int, default=0)
            p.add_argument("--scratch-type", default="none")
            p.add_argument("--scratch-gb", type=int, default=0)
            p.add_argument("--queue", default="default")
            p.add_argument("--name")
            p.add_argument(
                "--env", dest="environment_assignments", action="append",
                default=[], metavar="NAME=VALUE",
            )
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
            return cmd_check(remote, t)
        if args.cmd == "pull":
            return cmd_pull(remote, t, args.dir, branch=args.branch)
        if args.cmd == "submit":
            return cmd_submit(remote, t, args.dir, args.script,
                              workspace=args.workspace, task=args.task,
                              walltime=args.walltime, cpus=args.cpus,
                              mem_gb=args.mem_gb, gpus=args.gpus,
                              queue=args.queue, name=args.name,
                              scratch_type=args.scratch_type,
                              scratch_gb=args.scratch_gb,
                              environment_assignments=args.environment_assignments)
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
