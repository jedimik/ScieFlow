"""Plain SSH adapter. Experimental (D19): never run against a real host in M-E.

A job is one detached wrapper started with setsid/nohup. The wrapper writes
an exit file, so status can tell a finished job from a vanished one. The
launch is grouped in braces so that $! is the wrapper's pid (binding
clarification C2). Cancel signals the whole session (C3), because timeout
moves itself into its own process group.
"""

import re
import shlex

from remote.errors import PolicyError
from remote.schedulers.base import (
    JobRef, JobSpec, JobStatus, Scheduler, SubmitParseError, require_digits,
    walltime_to_seconds,
)

# Constant wrapper W (reviews/m-e-plan.md section 5.5). $0 is the job name,
# $1 the script, $2 the walltime in seconds.
WRAPPER = ('exec >"$0.o$$" 2>"$0.e$$"; timeout "$2" bash "$1"; rc=$?; '
           "printf '%s\\n' \"$rc\" >\"$0.x$$.tmp\" && mv \"$0.x$$.tmp\" \"$0.x$$\"")

NOTICE = ("NOTICE: ssh scheduler does not enforce cpus/mem/gpus/queue; "
          "walltime is enforced with timeout")
JOB_ID_RE = re.compile(r"\d+\.\d+")


def _pid(job_id: str) -> str:
    pid = require_digits(job_id.split(".", 1)[0], "job id")
    if int(pid) < 2:
        # pid 0 and 1 are never a job of this user; refuse before any status or kill (review A1).
        raise PolicyError(f"unsafe job id (pid must be 2 or more): '{job_id}'")
    return pid


class SshScheduler(Scheduler):
    name = "ssh"
    maturity = "experimental"
    transport = "ssh"
    concrete_ops = {}
    array_support = False
    labels = {"submit": "SUBMIT_FAILED", "status": "STATUS_FAILED"}
    detail_fields = ()
    needs_kerberos = True
    needs_ledger_dir = True

    def resource_args(self, spec: JobSpec) -> str:
        if spec.scratch_gb > 0:
            raise PolicyError("ssh scheduler has no scratch")
        return f"timeout={walltime_to_seconds(spec.walltime)}"

    def notices(self, spec: JobSpec) -> list[str]:
        return [NOTICE]

    def submit_command(self, spec: JobSpec) -> str:
        env_prefix = ""
        if spec.env:
            assignments = " ".join(shlex.quote(f"{k}={v}") for k, v in spec.env.items())
            env_prefix = f"env {assignments} "
        seconds = walltime_to_seconds(spec.walltime)
        return (
            f"cd {shlex.quote(spec.dir)} && {{ {env_prefix}setsid nohup bash -c "
            f"{shlex.quote(WRAPPER)} {shlex.quote(spec.name)} {shlex.quote(spec.script)} "
            f"{seconds} </dev/null >/dev/null 2>&1 & echo \"$!.$(date +%s)\"; }}"
        )

    def parse_submit(self, stdout: str) -> str:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines or not JOB_ID_RE.fullmatch(lines[-1]):
            raise SubmitParseError("ssh submit returned no <pid>.<epoch> job id")
        return lines[-1]

    def status_command(self, job: JobRef) -> str:
        pid = _pid(job.job_id)
        exit_file = shlex.quote(f"{job.job_name}.x{pid}")
        cancel_file = shlex.quote(f"{job.job_name}.c{pid}")
        return (
            f"cd {shlex.quote(job.dir)} && if test -f {exit_file}; then "
            f"printf 'EXIT %s\\n' \"$(cat {exit_file})\"; "
            f"elif kill -0 {pid} 2>/dev/null; then echo RUNNING; "
            f"elif test -f {cancel_file}; then echo CANCELLED; "
            "else echo GONE; fi"
        )

    def parse_status(self, stdout: str, job: JobRef) -> JobStatus:
        text = stdout.strip()
        if text == "RUNNING":
            return JobStatus("running")
        if text == "CANCELLED":
            return JobStatus("cancelled")
        if text == "GONE":
            return JobStatus("failed", "-")
        match = re.fullmatch(r"EXIT (\d+)", text)
        if match:
            code = match.group(1)
            return JobStatus("done" if code == "0" else "failed", code)
        return JobStatus(state=None, parse_error=True)

    def cancel_command(self, job: JobRef) -> str:
        pid = _pid(job.job_id)
        cancel_file = shlex.quote(f"{job.job_name}.c{pid}")
        return f"cd {shlex.quote(job.dir)} && : > {cancel_file} && pkill -TERM -s {pid}"

    def log_paths(self, job: JobRef) -> tuple[str, str]:
        pid = _pid(job.job_id)
        return f"{job.job_name}.o{pid}", f"{job.job_name}.e{pid}"

    def logs_command(self, job: JobRef) -> str:
        out_name, err_name = self.log_paths(job)
        return (
            f"cd {shlex.quote(job.dir)} && cat {shlex.quote(out_name)} "
            f"{shlex.quote(err_name)} 2>/dev/null"
        )
