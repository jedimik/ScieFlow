"""Slurm adapter. Experimental (D19): never run against a real cluster in M-E.

Submit uses sbatch --parsable. Status reads sacct and squeue; the trailing
`|| true` on both legs keeps a finished job that has aged out of the
controller from turning into SACCT_FAILED (binding clarification C1).
"""

import re
import shlex

from remote.errors import PolicyError
from remote.schedulers.base import (
    JobRef, JobSpec, JobStatus, Scheduler, SubmitParseError, require_digits,
)

QUEUED_WORDS = {"PENDING", "CONFIGURING", "REQUEUED", "RESIZING"}
RUNNING_WORDS = {"RUNNING", "COMPLETING", "SUSPENDED", "STOPPED"}
FAILED_WORDS = {"FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL",
                "DEADLINE", "PREEMPTED"}
SQUEUE_MARKER = "#squeue"


def _first_word(value: str) -> str:
    words = value.split()
    return words[0].upper() if words else ""


def _exit_part(exit_code: str) -> str:
    return exit_code.split(":", 1)[0]


class SlurmScheduler(Scheduler):
    name = "slurm"
    maturity = "experimental"
    transport = "ssh"
    concrete_ops = {"submit": "sbatch", "status": "sacct", "cancel": "scancel", "logs": "logs"}
    array_support = True
    labels = {"submit": "SBATCH_FAILED", "status": "SACCT_FAILED"}
    detail_fields = ("Elapsed",)
    needs_kerberos = True
    needs_ledger_dir = True

    def resource_args(self, spec: JobSpec) -> str:
        if spec.scratch_gb > 0 and spec.scratch_type != "tmp":
            raise PolicyError("slurm supports scratch_type 'tmp' only (--tmp)")
        args = f"-N 1 -n 1 -c {spec.cpus} --mem={spec.mem_gb}G"
        if spec.gpus > 0:
            args += f" --gres=gpu:{spec.gpus}"
        if spec.scratch_gb > 0:
            args += f" --tmp={spec.scratch_gb}G"
        return args

    def submit_command(self, spec: JobSpec) -> str:
        export = ""
        if spec.env:
            joined = "ALL," + ",".join(f"{key}={value}" for key, value in spec.env.items())
            export = f" --export={shlex.quote(joined)}"
        return (
            f"cd {shlex.quote(spec.dir)} && sbatch --parsable -J {shlex.quote(spec.name)} "
            f"-p {shlex.quote(spec.queue)} -t {shlex.quote(spec.walltime)} "
            f"{self.resource_args(spec)} -o slurm-%j.out -e slurm-%j.err"
            f"{export} {shlex.quote(spec.script)}"
        )

    def parse_submit(self, stdout: str) -> str:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise SubmitParseError("sbatch returned no job id")
        job_id = lines[-1].split(";", 1)[0]
        if not job_id.isdigit():
            raise SubmitParseError("sbatch returned no numeric job id")
        return job_id

    def status_command(self, job: JobRef) -> str:
        jid = require_digits(job.job_id, "job id")
        return (
            f"sacct -X -n -P -j {jid} --format=JobID,State,ExitCode,Elapsed 2>/dev/null || true; "
            f"printf '%s\\n' '{SQUEUE_MARKER}'; squeue -h -j {jid} -o '%i|%T' 2>/dev/null || true"
        )

    def parse_status(self, stdout: str, job: JobRef) -> JobStatus:
        lines = [line.strip() for line in stdout.splitlines()]
        if SQUEUE_MARKER not in lines:
            return JobStatus(state=None, parse_error=True)
        marker = lines.index(SQUEUE_MARKER)
        sacct_rows = [line.split("|") for line in lines[:marker] if line]
        squeue_rows = [line.split("|") for line in lines[marker + 1:] if line]
        if any(len(row) != 4 for row in sacct_rows) or any(len(row) != 2 for row in squeue_rows):
            return JobStatus(state=None, parse_error=True)

        mine = next((row for row in sacct_rows if row[0] == job.job_id), None)
        if mine is not None:
            _, raw_state, exit_raw, elapsed = mine
            if not re.fullmatch(r"\d+:\d+", exit_raw):
                return JobStatus(state=None, parse_error=True)
            word = _first_word(raw_state)
            details = {"Elapsed": elapsed}
            if word == "COMPLETED":
                state = "done" if exit_raw == "0:0" else "failed"
                return JobStatus(state, _exit_part(exit_raw), details)
            if word in FAILED_WORDS:
                return JobStatus("failed", _exit_part(exit_raw), details)
            if word == "CANCELLED":
                return JobStatus("cancelled", _exit_part(exit_raw), details)
            if word in RUNNING_WORDS:
                return JobStatus("running", "-", details)
            if word in QUEUED_WORDS:
                return JobStatus("queued", "-", details)
            return JobStatus("unknown", "-", details)

        queue_row = next((row for row in squeue_rows if row[0] == job.job_id), None)
        if queue_row is None:
            # Not visible to sacct or squeue yet, or any more. Never "done".
            return JobStatus("unknown", "-", {})
        word = _first_word(queue_row[1])
        if word in QUEUED_WORDS:
            return JobStatus("queued", "-", {})
        if word in RUNNING_WORDS:
            return JobStatus("running", "-", {})
        if word == "CANCELLED":
            return JobStatus("cancelled", "-", {})
        if word in FAILED_WORDS:
            return JobStatus("failed", "-", {})
        # squeue gives no exit code, so COMPLETED alone is not positive success.
        return JobStatus("unknown", "-", {})

    def cancel_command(self, job: JobRef) -> str:
        return f"scancel {require_digits(job.job_id, 'job id')}"

    def log_paths(self, job: JobRef) -> tuple[str, str]:
        jid = require_digits(job.job_id, "job id")
        return f"slurm-{jid}.out", f"slurm-{jid}.err"

    def logs_command(self, job: JobRef) -> str:
        out_name, err_name = self.log_paths(job)
        return (
            f"cd {shlex.quote(job.dir)} && cat {out_name} {err_name} 2>/dev/null"
        )
