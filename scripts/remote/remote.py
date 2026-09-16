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


def _remote_credentials_failed(stderr: str) -> bool:
    """Recognize observed missing/expired credentials without inspecting secrets."""
    message = stderr.lower()
    return any(marker in message for marker in (
        "remote_no_ticket", "key has expired", "no credentials were supplied",
        "no kerberos credentials available", "credentials cache file not found",
    ))


def _report_remote_credentials() -> int:
    print("NO_TICKET: remote Kerberos/filesystem credentials are missing or expired "
          "even if the local ticket is valid. Stop and ask the user to run `kinit`; "
          "if this persists, inspect credential forwarding. Agent must never authenticate.")
    return 2


def cmd_check(remote, t) -> int:
    policy.check_op(remote, "check")
    if t.local(["klist", "-s"]).returncode != 0:
        print("NO_TICKET: no valid Kerberos ticket on this host — stop and "
              "ask the user to run `kinit` (agent must never authenticate).")
        return 2
    # SSH login can work while PBS and Kerberized filesystems cannot. A plain
    # echo previously masked expired remote credentials (job23754591 follow-up).
    result = t.ssh("if klist -s; then printf 'REMOTE_TICKET_OK\\n'; "
                   "else printf 'REMOTE_NO_TICKET\\n' >&2; exit 2; fi")
    if _remote_credentials_failed(result.stderr):
        return _report_remote_credentials()
    if result.returncode != 0:
        print(f"SSH_FAILED: {result.stderr.strip()}")
        return 1
    if result.stdout.strip() != "REMOTE_TICKET_OK":
        print("CHECK_FAILED: remote credential check did not return its success marker")
        return 1
    print("OK")
    return 0


def cmd_pull(
    remote,
    t,
    remote_dir: str,
    *,
    branch: str | None = None,
    reconcile_exact_target: bool = False,
) -> int:
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
    if status.stdout.strip() and not reconcile_exact_target:
        print("DIRTY_TRACKED: refusing to pull or switch a remote repository "
              "with tracked changes", file=sys.stderr)
        print(status.stdout, end="", file=sys.stderr)
        return 1

    if reconcile_exact_target:
        if branch is None:
            raise policy.PolicyError(
                "--reconcile-exact-target requires an explicit --branch"
            )
        policy.check_op(remote, "git-switch")
        branch = policy.check_branch(branch)
        # This repairs a partially synchronized checkout without discarding any
        # content.  The branch pointer advances only when both the index and
        # working tree already equal the fetched target tree, no untracked
        # files exist, and the update is a fast-forward.
        command = (
            f"cd {shlex.quote(d)} && "
            f"test \"$(git symbolic-ref --short HEAD)\" = {shlex.quote(branch)} && "
            "git fetch --prune origin && "
            f"target={shlex.quote('refs/remotes/origin/' + branch)} && "
            "git merge-base --is-ancestor HEAD \"$target\" && "
            "git diff --quiet \"$target\" -- && "
            "git diff --cached --quiet \"$target\" -- && "
            "test -z \"$(git ls-files --others --exclude-standard)\" && "
            "old=$(git rev-parse HEAD) && new=$(git rev-parse \"$target\") && "
            "git update-ref HEAD \"$new\" \"$old\" && "
            "test -z \"$(git status --porcelain=v1 --untracked-files=all)\" && "
            "printf 'RECONCILED_EXACT_TARGET\nBRANCH: ' && "
            "git branch --show-current && printf 'SHA: ' && git rev-parse HEAD"
        )
        result = t.ssh(command)
        print(result.stdout, end="")
        if result.returncode != 0:
            print(
                "RECONCILE_REFUSED: remote content/index must exactly match "
                "a clean fast-forward target",
                file=sys.stderr,
            )
            if result.stderr.strip():
                print(result.stderr.strip(), file=sys.stderr)
            return 1
        if f"BRANCH: {branch}\n" not in result.stdout:
            print(f"BRANCH_MISMATCH: requested '{branch}'", file=sys.stderr)
            return 1
        return 0

    if branch is not None:
        policy.check_op(remote, "git-switch")
        branch = policy.check_branch(branch)
        command = (
            f"cd {shlex.quote(d)} && git fetch --prune origin && "
            f"git switch {shlex.quote(branch)} && "
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


def cmd_repo_status(remote, t, remote_dir: str, *, include_untracked: bool = False) -> int:
    """Report a remote checkout's porcelain status without changing it."""
    policy.check_op(remote, "git-status")
    d = policy.check_dir(remote, remote_dir)
    untracked = "all" if include_untracked else "no"
    result = t.ssh(
        f"cd {shlex.quote(d)} && "
        f"git status --porcelain=v1 --untracked-files={untracked}"
    )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    if result.stdout:
        print(result.stdout, end="")
    else:
        print("CLEAN")
    return 0


def cmd_move(remote, t, source: str, destination: str) -> int:
    """Move one policy-bounded remote path without overwriting its destination."""
    policy.check_op(remote, "mv")
    src = policy.check_dir(remote, source)
    dest = policy.check_dir(remote, destination)
    if src == dest:
        raise policy.PolicyError("move source and destination must differ")
    result = t.ssh(
        f"test -e {shlex.quote(src)} && "
        f"test ! -e {shlex.quote(dest)} && "
        f"mv -- {shlex.quote(src)} {shlex.quote(dest)}"
    )
    if result.returncode != 0:
        print("MOVE_FAILED: source must exist and destination must be absent", file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return 1
    print(f"MOVED: {src} -> {dest}")
    return 0


def cmd_verify_sampling_stage(remote, t, remote_dir: str, subject_root: str, commit: str) -> int:
    """Run the pinned repository's read-only stage verifier on an allowed tree."""
    policy.check_op(remote, "bash")
    policy.check_op(remote, "git-status")
    directory = policy.check_dir(remote, remote_dir)
    subject = policy.check_dir(remote, subject_root)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise policy.PolicyError("stage verification requires a full lowercase Git commit SHA")
    result = t.ssh(
        f"cd {shlex.quote(directory)} && "
        f'test "$(git rev-parse HEAD)" = {commit} && '
        'test -z "$(git status --porcelain=v1 --untracked-files=all)" && '
        "python3 -B workflow/scripts/sampling_resume.py verify "
        f"{shlex.quote(subject)} && "
        f"sha256sum -- {shlex.quote(subject + '/.sampling-stage.json')}"
    )
    print(result.stdout, end="")
    if result.returncode:
        print("STAGE_VERIFY_FAILED: pinned clean source and complete stage integrity required", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    print(f"VERIFIED_STAGE: {subject}")
    return 0


def cmd_storage_status(remote, t, remote_dir: str, *, ceph: bool = False) -> int:
    """Read filesystem capacity for one allowed path; never imply quota headroom.

    The existing bash permission authorizes this fixed diagnostic command, not
    arbitrary shell input. No recursion, writes, user-supplied commands, or
    alternate remote host are accepted.
    """
    policy.check_op(remote, "bash")
    d = policy.check_dir(remote, remote_dir)
    result = t.ssh(f"LC_ALL=C df -Pk -- {shlex.quote(d)}")
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    if ceph:
        for attribute in (
            "ceph.quota.max_bytes", "ceph.quota.max_files",
            "ceph.dir.rbytes", "ceph.dir.rfiles",
        ):
            value = t.ssh(
                f"LC_ALL=C getfattr --absolute-names -n {attribute} -- {shlex.quote(d)}"
            )
            print(value.stdout, end="")
            if value.returncode != 0:
                print(f"ATTRIBUTE_UNAVAILABLE: {attribute}: {value.stderr.strip()}")
        print("QUOTA: directory attributes only; ancestor quotas not inspected")
    print("QUOTA: filesystem availability only; user/project quota not verified")
    return 0


def cmd_path_info(remote, t, remote_dir: str) -> int:
    """Inspect path components only; no arbitrary shell or path outside policy."""
    policy.check_op(remote, "bash")
    directory = policy.check_dir(remote, remote_dir)
    result = t.ssh(f"LC_ALL=C namei -l -- {shlex.quote(directory)}")
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    return 0


def cmd_runtime_info(remote, t, remote_dir: str) -> int:
    """Fixed read-only frontend container-runtime discovery; no arbitrary command."""
    policy.check_op(remote, "bash")
    directory = policy.check_dir(remote, remote_dir)
    probe = (
        "import json,shutil,subprocess; rows=[]\n"
        "for scope,path in [('login',None),('sanitized','/usr/local/bin:/usr/bin:/bin')]:\n"
        " for name in ['apptainer','singularity']:\n"
        "  executable=shutil.which(name,path=path); row={'scope':scope,'name':name,'path':executable}\n"
        "  if executable:\n"
        "   result=subprocess.run([executable,'--version'],capture_output=True,text=True,timeout=15)\n"
        "   row.update(returncode=result.returncode,stdout=result.stdout[:512],stderr=result.stderr[:512])\n"
        "  rows.append(row)\n"
        "print(json.dumps({'frontend_only':True,'runtimes':rows},indent=2))\n"
    )
    result = t.ssh(f"cd {shlex.quote(directory)} && python3 -c {shlex.quote(probe)}")
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


def cmd_mkdir(remote, t, remote_dir: str) -> int:
    """Create one allowed directory tree, without touching code or file data."""
    policy.check_op(remote, "mkdir")
    directory = policy.check_dir(remote, remote_dir)
    if directory != remote_dir:
        raise policy.PolicyError("mkdir requires a normalized absolute path")
    result = t.ssh(f"mkdir -p -- {shlex.quote(directory)}")
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    print(f"DIRECTORY_READY: {directory}")
    return 0


def cmd_verify_posthoc_deployment(remote, t, remote_dir: str, control_dir: str,
                                 commit: str, request_sha256: str) -> int:
    """Read-only fixed Job1 verifier from an authenticated Git deployment."""
    policy.check_op(remote, "bash")
    policy.check_op(remote, "git-status")
    directory = policy.check_dir(remote, remote_dir)
    control = policy.check_dir(remote, control_dir)
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
        raise policy.PolicyError("deployment verification requires full Git and request digests")
    interpreter = control + "/runtime/host/bin/python"
    verifier = directory + "/research/job1-posthoc-meta/verify_deployment.py"
    result = t.ssh(
        f"cd {shlex.quote(directory)} && "
        f'test "$(git rev-parse HEAD)" = {commit} && '
        'test -z "$(git status --porcelain=v1 --untracked-files=all)" && '
        f"{shlex.quote(interpreter)} -B -I {shlex.quote(verifier)} "
        f"--request-sha256 {request_sha256}"
    )
    print(result.stdout, end="")
    if result.returncode:
        print("DEPLOYMENT_VERIFY_FAILED", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    return 0


def cmd_submit(remote, t, remote_dir: str, script: str, *, workspace: Path,
               task: str, walltime: str, cpus: int, mem_gb: int, gpus: int,
               queue: str, name: str | None, scratch_type: str = "none",
               scratch_gb: int = 0,
               environment_assignments: list[str] | None = None,
               submit_dir: str | None = None) -> int:
    policy.check_op(remote, "qsub")
    d = policy.check_dir(remote, remote_dir)
    policy.check_queue(remote, queue)
    policy.check_token(task, "task name")
    policy.check_script(script)
    # Keep PBS logs outside a pinned clean code checkout. The script remains
    # relative to the authorized code dir; only qsub's working dir changes.
    submission_directory = policy.check_dir(remote, submit_dir) if submit_dir is not None else d
    if submit_dir is not None and submission_directory != submit_dir:
        raise policy.PolicyError("submit directory must be a normalized absolute path")
    submitted_script = str(Path(d) / script) if submit_dir is not None else script
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
        f"cd {shlex.quote(submission_directory)} && qsub -N {shlex.quote(jobname)} "
        f"-q {shlex.quote(queue)} "
        f"-l walltime={shlex.quote(res['walltime'])} "
        f"-l {shlex.quote(select)} {env_arg}{shlex.quote(submitted_script)}"
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
        remote_dir=submission_directory, script=script, resources={**res, "queue": queue},
        environment=environment, job_name=jobname,
    )
    if submit_dir is not None:
        entry["code_dir"] = d
    jobs.save_jobs(workspace, ledger)
    print(f"SUBMITTED: {job_id} attempt={entry['attempt']}")
    return 0


def _qstat_value(output: str, field: str) -> str | None:
    """Return one allowlisted single-line field from ``qstat -xf`` output."""

    match = re.search(
        rf"(?m)^\s*{re.escape(field)}\s*=\s*([^\r\n]*)$",
        output,
    )
    return match.group(1).strip() if match else None


def cmd_status(
    remote,
    t,
    job_id: str,
    *,
    workspace: Path,
    details: bool = False,
) -> int:
    policy.check_op(remote, "qstat")
    policy.check_token(job_id, "job id")
    result = t.ssh(f"qstat -xf {shlex.quote(job_id)}")
    if _remote_credentials_failed(result.stderr):
        return _report_remote_credentials()
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
    if details:
        for field in (
            "resources_used.walltime",
            "resources_used.cput",
            "resources_used.cpupercent",
            "resources_used.mem",
            "resources_used.vmem",
            "resources_used.ncpus",
            "stime",
            "start_time",
            "exec_host",
        ):
            value = _qstat_value(result.stdout, field)
            if value is not None:
                print(f"DETAIL: {field}={value}")
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


def cmd_push(remote, t, src: str, dest: str, *, workspace: Path,
             checksum: bool = False) -> int:
    """Push one workspace directory to an approved remote directory."""
    policy.check_op(remote, "rsync")
    remote_dest = policy.check_dir(remote, dest)
    source_path = Path(src).resolve()
    ws = Path(workspace).resolve()
    if not source_path.is_relative_to(ws):
        raise policy.PolicyError(
            f"push source must be inside the workspace: {src}"
        )
    if not source_path.is_dir() and not source_path.is_file():
        raise policy.PolicyError(f"push source must be a file or directory: {src}")
    result = (t.rsync_to(str(source_path), remote_dest, checksum=True)
              if checksum else t.rsync_to(str(source_path), remote_dest))
    if result.returncode != 0:
        print(f"RSYNC_FAILED: {result.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"PUSHED: {source_path} -> {remote_dest}")
    return 0


def cmd_git_push(remote, t, directory: str, branch: str) -> int:
    """Push a clean local-published branch through the governed transport."""
    policy.check_op(remote, "git-push")
    d = policy.check_dir(remote, directory)
    result = t.ssh(
        f"cd {shlex.quote(d)} && git status --porcelain=v1 --untracked-files=all "
        f"&& test -z \"$(git status --porcelain=v1 --untracked-files=all)\" "
        f"&& test \"$(git symbolic-ref --short HEAD)\" = {shlex.quote(branch)} "
        f"&& git push origin {shlex.quote(branch)}"
    )
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, file=sys.stderr, end="")
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="remote.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in (
        "check", "pull", "repo-status", "move", "mkdir", "submit", "status", "logs",
        "fetch", "push", "git-push", "storage-status", "path-info", "verify-sampling-stage",
        "verify-posthoc-deployment", "runtime-info"
    ):
        p = sub.add_parser(name)
        p.add_argument("remote")
        if name == "pull":
            p.add_argument("dir")
            p.add_argument("--branch")
            p.add_argument(
                "--reconcile-exact-target",
                action="store_true",
                help=(
                    "advance a dirty branch only when its index and working "
                    "tree exactly equal the fetched fast-forward target"
                ),
            )
        if name == "repo-status":
            p.add_argument("dir")
            p.add_argument("--include-untracked", action="store_true")
        if name in ("path-info", "runtime-info"):
            p.add_argument("dir")
        if name == "mkdir":
            p.add_argument("dir")
        if name == "verify-posthoc-deployment":
            p.add_argument("dir")
            p.add_argument("control_dir")
            p.add_argument("--commit", required=True)
            p.add_argument("--request-sha256", required=True)
        if name == "storage-status":
            p.add_argument("dir")
            p.add_argument("--ceph", action="store_true",
                           help="read four fixed CephFS quota/usage attributes for this path only")
        if name == "verify-sampling-stage":
            p.add_argument("dir")
            p.add_argument("subject_root")
            p.add_argument("--commit", required=True)
        if name == "move":
            p.add_argument("source")
            p.add_argument("destination")
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
            p.add_argument("--submit-dir", help="allowed PBS working/log directory, separate from code dir")
            p.add_argument(
                "--env", dest="environment_assignments", action="append",
                default=[], metavar="NAME=VALUE",
            )
        if name in ("status", "logs"):
            p.add_argument("job_id")
        if name == "status":
            p.add_argument(
                "--details",
                action="store_true",
                help="print allowlisted read-only PBS usage fields",
            )
        if name == "fetch":
            p.add_argument("src")
            p.add_argument("dest")
        if name == "push":
            p.add_argument("src")
            p.add_argument("dest")
            p.add_argument("--checksum", action="store_true",
                           help="checksum-based sync instead of append-only resume; reconcile content and metadata")
        if name == "git-push":
            p.add_argument("dir")
            p.add_argument("--branch", required=True)
        if name in ("submit", "status", "logs", "fetch", "push"):
            p.add_argument("--workspace", type=Path, required=True)
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    try:
        remote = policy.load_remote(root, args.remote)
        t = transport.Transport(remote)
        if args.cmd == "check":
            return cmd_check(remote, t)
        if args.cmd == "pull":
            return cmd_pull(
                remote,
                t,
                args.dir,
                branch=args.branch,
                reconcile_exact_target=args.reconcile_exact_target,
            )
        if args.cmd == "repo-status":
            return cmd_repo_status(
                remote, t, args.dir, include_untracked=args.include_untracked
            )
        if args.cmd == "move":
            return cmd_move(remote, t, args.source, args.destination)
        if args.cmd == "storage-status":
            return cmd_storage_status(remote, t, args.dir, ceph=args.ceph)
        if args.cmd == "path-info":
            return cmd_path_info(remote, t, args.dir)
        if args.cmd == "runtime-info":
            return cmd_runtime_info(remote, t, args.dir)
        if args.cmd == "mkdir":
            return cmd_mkdir(remote, t, args.dir)
        if args.cmd == "verify-posthoc-deployment":
            return cmd_verify_posthoc_deployment(remote, t, args.dir, args.control_dir,
                                                args.commit, args.request_sha256)
        if args.cmd == "verify-sampling-stage":
            return cmd_verify_sampling_stage(remote, t, args.dir, args.subject_root, args.commit)
        if args.cmd == "submit":
            return cmd_submit(remote, t, args.dir, args.script,
                              workspace=args.workspace, task=args.task,
                              walltime=args.walltime, cpus=args.cpus,
                              mem_gb=args.mem_gb, gpus=args.gpus,
                              queue=args.queue, name=args.name,
                              scratch_type=args.scratch_type,
                              scratch_gb=args.scratch_gb,
                              environment_assignments=args.environment_assignments,
                              submit_dir=args.submit_dir)
        if args.cmd == "status":
            return cmd_status(
                remote,
                t,
                args.job_id,
                workspace=args.workspace,
                details=args.details,
            )
        if args.cmd == "logs":
            return cmd_logs(remote, t, args.job_id, workspace=args.workspace)
        if args.cmd == "fetch":
            return cmd_fetch(remote, t, args.src, args.dest,
                             workspace=args.workspace)
        if args.cmd == "push":
            return cmd_push(remote, t, args.src, args.dest,
                            workspace=args.workspace, checksum=args.checksum)
        if args.cmd == "git-push":
            return cmd_git_push(remote, t, args.dir, args.branch)
    except policy.PolicyError as exc:
        print(f"POLICY: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
