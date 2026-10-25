#!/usr/bin/env python3
"""Sanctioned CLI for remote (metacentrum) operations — AGENTS.md rule 11.

Every subcommand authorizes against config/remotes.yml (policy.py) BEFORE
any SSH. Exit codes: 0 ok, 1 remote command failed, 2 no Kerberos ticket,
3 policy refusal, 4 limit ceiling (attempts/concurrency).
"""

import argparse
import hashlib
import json
import os
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

from remote import jobs, policy, schedulers, transport
from remote.schedulers.base import JobRef, JobSpec, SubmitParseError

# A dry run's recorded plan (dag) larger than this is refused (M-E t3).
DAG_MAX_BYTES = 10 * 1024 * 1024


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
    if not schedulers.get(remote.scheduler).needs_kerberos:
        # Local execution has no Kerberos ticket or remote SSH to check.
        print("OK")
        return 0
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


def cmd_verify_thalamus_deployment(remote, t, remote_dir: str, control_dir: str,
                                  commit: str, request_sha256: str, *, progress: bool = False) -> int:
    """Invoke only the frozen thalamus controller's read-only validation path."""
    policy.check_op(remote, "bash")
    policy.check_op(remote, "git-status")
    directory = policy.check_dir(remote, remote_dir)
    control = policy.check_dir(remote, control_dir)
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
        raise policy.PolicyError("deployment verification requires full Git and request digests")
    interpreter = control + "/runtime/host/bin/python"
    module = directory + "/research/job1-thalamus-followup"
    request_path = control + "/deployment/thalamus-01/execution-request.json"
    probe = (
        "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "import controller; "
        "request=controller.read_request(Path(sys.argv[2]),sys.argv[3]); "
        "plan,source=controller.validate(request,sys.argv[3],'dry-run',allocation=False); "
        "print(json.dumps({'schema':'scieflow.thalamus-readonly-deployment.v1',"
        "'status':'success','request_sha256':sys.argv[3],'git':request['git'],"
        "'plan':request['plan'],'inherited_manifest_pins':len(request['inherited']),"
        "'selections':len(plan['selections']),'frontend_only':True,"
        "'participant_execution':False,'remaining_gate':'PBS allocation compatibility and exact DAG'},"
        "sort_keys=True,indent=2))"
    )
    if progress:
        # Inspect only this user's exact pinned verifier invocation. Never
        # print unrelated argv, environment contents, descriptors or filenames.
        probe = (
            "import json,os,sys; from pathlib import Path; rows=[]\n"
            "for p in Path('/proc').iterdir():\n"
            " if not p.name.isdecimal(): continue\n"
            " try:\n"
            "  if p.stat().st_uid != os.getuid(): continue\n"
            "  argv=(p/'cmdline').read_bytes().decode().rstrip('\\0').split('\\0')\n"
            "  if len(argv)!=8 or argv[1:4]!=['-B','-I','-c'] or argv[5:]!=sys.argv[1:]: continue\n"
            "  if not argv[4].startswith('import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import controller; '): continue\n"
            "  status=dict(line.split(':',1) for line in (p/'status').read_text().splitlines() if ':' in line)\n"
            "  io=dict(line.split(':',1) for line in (p/'io').read_text().splitlines())\n"
            "  rows.append({'pid':int(p.name),'state':status['State'].strip(),'rss':status.get('VmRSS','').strip(),"
            "'rchar':int(io['rchar']),'syscr':int(io['syscr']),'read_bytes':int(io['read_bytes'])})\n"
            " except (FileNotFoundError,ProcessLookupError,PermissionError): continue\n"
            "print(json.dumps({'read_only_progress':True,'matching_verifiers':rows},sort_keys=True))\n"
        )
    result = t.ssh(
        f"cd {shlex.quote(directory)} && "
        f'test "$(git rev-parse HEAD)" = {commit} && '
        'test -z "$(git status --porcelain=v1 --untracked-files=all)" && '
        f"{shlex.quote(interpreter)} -B -I -c {shlex.quote(probe)} "
        f"{shlex.quote(module)} {shlex.quote(request_path)} {request_sha256}"
    )
    print(result.stdout, end="")
    if _remote_credentials_failed(result.stderr):
        return _report_remote_credentials()
    if result.returncode:
        print("THALAMUS_DEPLOYMENT_VERIFY_FAILED", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    return 0


def _check_entry_scheduler(remote, entry: dict, job_id: str) -> None:
    """Refuse a ledger entry that was submitted to a different scheduler (M-E-D6).

    The ledger is agent-writable, so the scheduler is re-checked here, before
    any SSH call.
    """
    scheduler = jobs.entry_scheduler(entry)
    if scheduler != remote.scheduler:
        raise policy.PolicyError(
            f"job {job_id} was submitted with scheduler '{scheduler}', but "
            f"remote '{remote.name}' uses '{remote.scheduler}'"
        )


def _ledger_job(remote, entry: dict, job_id: str) -> JobRef:
    """Check a ledger entry against the remote's scheduler and build its JobRef.

    The ledger file is agent-writable. Adapters that build commands from the
    entry's dir and job name get both re-validated here, before any SSH call
    (review t2r-r1 F1), exactly as cmd_logs does.
    """
    _check_entry_scheduler(remote, entry, job_id)
    if schedulers.get(remote.scheduler).needs_ledger_dir:
        if "dir" not in entry or "job_name" not in entry:
            raise policy.PolicyError("ledger entry lacks dir/job_name")
        return JobRef(
            job_id=job_id,
            dir=policy.check_dir(remote, entry["dir"]),
            job_name=policy.check_token(entry["job_name"], "job name"),
        )
    return JobRef(
        job_id=job_id,
        dir=entry.get("dir", ""),
        job_name=entry.get("job_name", ""),
    )


def _dag_path(workspace: Path, task: str) -> Path:
    return Path(workspace) / "remote" / f"dag-{task}.txt"


def _dag_problem(workspace: Path, ledger: list, task: str) -> str | None:
    """Return why --after-dryrun must refuse, or None when the plan is intact.

    Checked from the ledger and the DAG file on disk, before any SSH. Both are
    agent-writable, so this guards against accidents and skipped steps, not
    against an agent that rewrites both (plan C5).
    """
    dry_runs = [j for j in ledger if j.get("task") == task and j.get("kind") == "dryrun"]
    if not dry_runs:
        return f"no dry run recorded for task '{task}'; run dryrun first"
    latest = dry_runs[-1]
    if latest.get("state") != "done":
        return (f"the latest dry run {latest['job_id']} is "
                f"'{latest.get('state')}', not done")
    recorded = latest.get("dag_sha256")
    if not recorded:
        return f"no DAG recorded for dry run {latest['job_id']}; run dag first"
    path = _dag_path(workspace, task)
    if not path.is_file():
        return f"{path} is missing"
    if hashlib.sha256(path.read_bytes()).hexdigest() != recorded:
        return f"{path} changed since it was recorded"
    return None


def _log_ref(remote, entry: dict, job_id: str) -> JobRef:
    """Build the JobRef behind `logs` and `dag`, re-checking the ledger before SSH."""
    _ledger_job(remote, entry, job_id)
    # The ledger is agent-writable: treat dir, sequence and name as untrusted.
    d = policy.check_dir(remote, entry["dir"])
    policy.check_token(job_id.split(".")[0], "job sequence")
    # PBS names default output files from `qsub -N`, which may differ from the
    # submitted script stem. Older ledger entries predate job_name recording.
    stem = policy.check_token(
        entry.get("job_name", Path(entry["script"]).stem), "job name")
    return JobRef(job_id=job_id, dir=d, job_name=stem)


def cmd_submit(remote, t, remote_dir: str, script: str, *, workspace: Path,
               task: str, walltime: str, cpus: int, mem_gb: int, gpus: int,
               queue: str, name: str | None, scratch_type: str = "none",
               scratch_gb: int = 0,
               environment_assignments: list[str] | None = None,
               submit_dir: str | None = None,
               kind: str = "job",
               after_dryrun: bool = False) -> int:
    policy.check_scheduler_op(remote, "submit")
    adapter = schedulers.get(remote.scheduler)
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
    jobname = name or Path(script).stem
    spec = JobSpec(
        name=jobname,
        dir=submission_directory,
        script=submitted_script,
        walltime=res["walltime"],
        cpus=res["cpus"],
        mem_gb=res["mem_gb"],
        gpus=res["gpus"],
        queue=queue,
        scratch_type=res["scratch_type"],
        scratch_gb=res["scratch_gb"],
        env=environment,
    )
    # Resource refusals (for example an unsupported scratch type) happen here,
    # before any SSH call.
    adapter.resource_args(spec)
    for notice in adapter.notices(spec):
        print(notice)

    ledger = jobs.load_jobs(workspace)
    if after_dryrun:
        problem = _dag_problem(workspace, ledger, task)
        if problem is not None:
            print(f"NO_DAG: {problem}", file=sys.stderr)
            return 1
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

    result = t.ssh(adapter.submit_command(spec))
    if result.returncode != 0:
        print(f"{adapter.labels['submit']}: {result.stderr.strip()}", file=sys.stderr)
        return 1
    try:
        job_id = adapter.parse_submit(result.stdout)
    except SubmitParseError as exc:
        print(f"{adapter.labels['submit']}: {exc}", file=sys.stderr)
        return 1
    entry = jobs.record_submit(
        ledger, task=task, job_id=job_id, remote_name=remote.name,
        remote_dir=submission_directory, script=script, resources={**res, "queue": queue},
        environment=environment, job_name=jobname, scheduler=adapter.name, kind=kind,
    )
    if submit_dir is not None:
        entry["code_dir"] = d
    jobs.save_jobs(workspace, ledger)
    print(f"SUBMITTED: {job_id} attempt={entry['attempt']}")
    return 0


def cmd_status(
    remote,
    t,
    job_id: str,
    *,
    workspace: Path,
    details: bool = False,
) -> int:
    policy.check_scheduler_op(remote, "status")
    policy.check_token(job_id, "job id")
    ledger = jobs.load_jobs(workspace)
    entry = jobs.find(ledger, job_id)
    if entry is None:
        # Refused before SSH: a status call cannot pick up an arbitrary id (M-E-D5).
        print(f"UNKNOWN_JOB: {job_id} not in jobs.yml", file=sys.stderr)
        return 1
    job = _ledger_job(remote, entry, job_id)
    adapter = schedulers.get(remote.scheduler)
    result = t.ssh(adapter.status_command(job))
    if _remote_credentials_failed(result.stderr):
        return _report_remote_credentials()
    if result.returncode != 0:
        print(f"{adapter.labels['status']}: {result.stderr.strip()}", file=sys.stderr)
        return 1
    status = adapter.parse_status(result.stdout, job)
    if status.parse_error:
        print(f"UNPARSEABLE: {result.stdout[:200]}", file=sys.stderr)
        return 1
    jobs.set_state(ledger, job_id, status.state)
    jobs.save_jobs(workspace, ledger)
    print(f"STATE: {status.state} exit={status.exit_code}")
    if details:
        for field in adapter.detail_fields:
            if field in status.details:
                print(f"DETAIL: {field}={status.details[field]}")
    return 0


def cmd_logs(remote, t, job_id: str, *, workspace: Path) -> int:
    policy.check_scheduler_op(remote, "logs")
    policy.check_token(job_id, "job id")
    ledger = jobs.load_jobs(workspace)
    entry = jobs.find(ledger, job_id)
    if entry is None:
        print(f"UNKNOWN_JOB: {job_id} not in jobs.yml", file=sys.stderr)
        return 1
    job = _log_ref(remote, entry, job_id)
    adapter = schedulers.get(remote.scheduler)
    result = t.ssh(adapter.logs_command(job))
    print(result.stdout, end="")
    return 0 if result.returncode == 0 else 1


def cmd_schedulers(*, as_json: bool) -> int:
    """List the scheduler adapters. Needs no config/remotes.yml and no SSH."""
    rows = []
    for name in schedulers.names():
        adapter = schedulers.get(name)
        ops = {op: adapter.concrete_ops.get(op, op)
               for op in ("submit", "status", "cancel", "logs")}
        rows.append({"name": adapter.name, "maturity": adapter.maturity,
                     "transport": adapter.transport, "ops": ops,
                     "needs_kerberos": adapter.needs_kerberos})
    if as_json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    for row in rows:
        ops = row["ops"]
        print(f"{row['name']} {row['maturity']} {row['transport']} "
              f"submit={ops['submit']} status={ops['status']} "
              f"cancel={ops['cancel']} logs={ops['logs']}")
    return 0


def cmd_dryrun(remote, t, remote_dir: str, script: str, *, workspace: Path,
               task: str, walltime: str, cpus: int, mem_gb: int, queue: str,
               name: str | None, submit_dir: str | None = None,
               environment_assignments: list[str] | None = None) -> int:
    """Submit a dry run: no GPUs, no scratch, SCIEFLOW_DRYRUN=1 in the job (M-E-D4)."""
    assignments = list(environment_assignments or [])
    if any(item.split("=", 1)[0] == "SCIEFLOW_DRYRUN" for item in assignments):
        raise policy.PolicyError(
            "SCIEFLOW_DRYRUN is set by dryrun and cannot be passed by the caller")
    return cmd_submit(
        remote, t, remote_dir, script, workspace=workspace, task=task,
        walltime=walltime, cpus=cpus, mem_gb=mem_gb, gpus=0, queue=queue,
        name=name, scratch_type="none", scratch_gb=0,
        environment_assignments=[*assignments, "SCIEFLOW_DRYRUN=1"],
        submit_dir=submit_dir, kind="dryrun",
    )


def cmd_dag(remote, t, job_id: str, *, workspace: Path) -> int:
    """Record a finished dry run's logs as the execution plan of record."""
    policy.check_scheduler_op(remote, "logs")
    policy.check_token(job_id, "job id")
    ledger = jobs.load_jobs(workspace)
    entry = jobs.find(ledger, job_id)
    if entry is None:
        print(f"UNKNOWN_JOB: {job_id} not in jobs.yml", file=sys.stderr)
        return 1
    job = _log_ref(remote, entry, job_id)
    if entry.get("kind") != "dryrun" or entry.get("state") != "done":
        print(f"NOT_A_DONE_DRYRUN: {job_id} must be a dry run with state done "
              "(run status first)", file=sys.stderr)
        return 1
    task = policy.check_token(entry["task"], "task name")
    adapter = schedulers.get(remote.scheduler)
    result = t.ssh(adapter.logs_command(job))
    if _remote_credentials_failed(result.stderr):
        return _report_remote_credentials()
    if result.returncode != 0:
        print(f"DAG_LOGS_FAILED: {result.stderr.strip()}", file=sys.stderr)
        return 1
    data = result.stdout.encode("utf-8")
    if len(data) > DAG_MAX_BYTES:
        print(f"DAG_TOO_LARGE: more than {DAG_MAX_BYTES} bytes; nothing recorded",
              file=sys.stderr)
        return 1
    path = _dag_path(workspace, task)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    digest = hashlib.sha256(data).hexdigest()
    entry["dag_sha256"] = digest
    jobs.save_jobs(workspace, ledger)
    print(f"DAG_RECORDED: {path} sha256={digest}")
    return 0


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
        "verify-posthoc-deployment", "verify-thalamus-deployment", "runtime-info"
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
        if name in ("verify-posthoc-deployment", "verify-thalamus-deployment"):
            p.add_argument("dir")
            p.add_argument("control_dir")
            p.add_argument("--commit", required=True)
            p.add_argument("--request-sha256", required=True)
            if name == "verify-thalamus-deployment":
                p.add_argument("--progress", action="store_true",
                               help="read only the exact matching verifier's process/I/O counters")
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
            p.add_argument(
                "--after-dryrun", action="store_true",
                help="refuse unless the task's latest dry run is done and its recorded DAG is unchanged",
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
    sched = sub.add_parser("schedulers", help="list scheduler adapters (needs no config)")
    sched.add_argument("--json", action="store_true")
    dry = sub.add_parser("dryrun", help="submit a dry run: no GPUs, no scratch, SCIEFLOW_DRYRUN=1")
    dry.add_argument("remote")
    dry.add_argument("dir")
    dry.add_argument("script")
    dry.add_argument("--task", required=True)
    dry.add_argument("--walltime", default="01:00:00")
    dry.add_argument("--cpus", type=int, default=1)
    dry.add_argument("--mem-gb", type=int, default=4)
    dry.add_argument("--queue", default="default")
    dry.add_argument("--name")
    dry.add_argument("--submit-dir", help="allowed PBS working/log directory, separate from code dir")
    dry.add_argument(
        "--env", dest="environment_assignments", action="append",
        default=[], metavar="NAME=VALUE",
    )
    dry.add_argument("--workspace", type=Path, required=True)
    dag = sub.add_parser("dag", help="record a finished dry run's logs as the plan of record")
    dag.add_argument("remote")
    dag.add_argument("job_id")
    dag.add_argument("--workspace", type=Path, required=True)
    args = ap.parse_args(argv)

    if args.cmd == "schedulers":
        # Listing needs no remote config and no SSH (M-E t3).
        return cmd_schedulers(as_json=args.json)
    root = Path(__file__).resolve().parents[2]
    try:
        remote = policy.load_remote(root, args.remote)
        t = transport.for_remote(remote)
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
        if args.cmd == "verify-thalamus-deployment":
            return cmd_verify_thalamus_deployment(remote, t, args.dir, args.control_dir,
                                                 args.commit, args.request_sha256,
                                                 progress=args.progress)
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
                              submit_dir=args.submit_dir,
                              after_dryrun=args.after_dryrun)
        if args.cmd == "dryrun":
            return cmd_dryrun(remote, t, args.dir, args.script,
                              workspace=args.workspace, task=args.task,
                              walltime=args.walltime, cpus=args.cpus,
                              mem_gb=args.mem_gb, queue=args.queue, name=args.name,
                              submit_dir=args.submit_dir,
                              environment_assignments=args.environment_assignments)
        if args.cmd == "dag":
            return cmd_dag(remote, t, args.job_id, workspace=args.workspace)
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
