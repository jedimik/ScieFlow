"""PBS (Metacentrum) adapter. Certified: the only scheduler run against a real cluster.

Every command string here is byte-for-byte what remote.py sent before the
seam existed. tests/test_remote_pbs_golden.py pins them.
"""

import re
import shlex

from remote.schedulers.base import JobRef, JobSpec, JobStatus, Scheduler, SubmitParseError

# PBS job_state letters. An unexpected letter maps to "queued" (M-E-D7), never
# to "done". Changing that would change PBS behaviour.
STATE_MAP = {"Q": "queued", "H": "queued", "R": "running", "E": "running"}

DETAIL_FIELDS = (
    "resources_used.walltime",
    "resources_used.cput",
    "resources_used.cpupercent",
    "resources_used.mem",
    "resources_used.vmem",
    "resources_used.ncpus",
    "stime",
    "start_time",
    "exec_host",
)


def _qstat_value(output: str, field: str) -> str | None:
    """Return one allowlisted single-line field from ``qstat -xf`` output."""
    match = re.search(
        rf"(?m)^\s*{re.escape(field)}\s*=\s*([^\r\n]*)$",
        output,
    )
    return match.group(1).strip() if match else None


class PbsScheduler(Scheduler):
    name = "pbs"
    maturity = "certified"
    transport = "ssh"
    concrete_ops = {"submit": "qsub", "status": "qstat", "cancel": "qdel", "logs": "logs"}
    array_support = True
    labels = {"submit": "QSUB_FAILED", "status": "QSTAT_FAILED"}
    detail_fields = DETAIL_FIELDS
    needs_kerberos = True
    needs_ledger_dir = False

    def resource_args(self, spec: JobSpec) -> str:
        select = f"select=1:ncpus={spec.cpus}:mem={spec.mem_gb}gb"
        if spec.scratch_gb > 0:
            select += f":{spec.scratch_type}={spec.scratch_gb}gb"
        if spec.gpus > 0:
            select += f":ngpus={spec.gpus}"
        return select

    def submit_command(self, spec: JobSpec) -> str:
        env_arg = ""
        if spec.env:
            joined = ",".join(f"{key}={value}" for key, value in spec.env.items())
            env_arg = f"-v {shlex.quote(joined)} "
        return (
            f"cd {shlex.quote(spec.dir)} && qsub -N {shlex.quote(spec.name)} "
            f"-q {shlex.quote(spec.queue)} "
            f"-l walltime={shlex.quote(spec.walltime)} "
            f"-l {shlex.quote(self.resource_args(spec))} {env_arg}{shlex.quote(spec.script)}"
        )

    def parse_submit(self, stdout: str) -> str:
        lines = stdout.strip().splitlines()
        if not lines or not lines[-1].strip():
            raise SubmitParseError("qsub returned success but no job id")
        return lines[-1].strip()

    def status_command(self, job: JobRef) -> str:
        return f"qstat -xf {shlex.quote(job.job_id)}"

    def parse_status(self, stdout: str, job: JobRef) -> JobStatus:
        state_m = re.search(r"job_state\s*=\s*(\w)", stdout)
        exit_m = re.search(r"Exit_status\s*=\s*(-?\d+)", stdout)
        if not state_m:
            return JobStatus(state=None, parse_error=True)
        code = state_m.group(1)
        if code == "F":
            state = "done" if exit_m and exit_m.group(1) == "0" else "failed"
        else:
            state = STATE_MAP.get(code, "queued")
        details = {}
        for field in self.detail_fields:
            value = _qstat_value(stdout, field)
            if value is not None:
                details[field] = value
        return JobStatus(
            state=state,
            exit_code=exit_m.group(1) if exit_m else "-",
            details=details,
        )

    def cancel_command(self, job: JobRef) -> str:
        return f"qdel {shlex.quote(job.job_id)}"

    def log_paths(self, job: JobRef) -> tuple[str, str]:
        seq = job.job_id.split(".")[0]
        return f"{job.job_name}.o{seq}", f"{job.job_name}.e{seq}"

    def logs_command(self, job: JobRef) -> str:
        out_name, err_name = self.log_paths(job)
        return (
            f"cd {shlex.quote(job.dir)} && cat "
            f"{shlex.quote(out_name)} {shlex.quote(err_name)} "
            "2>/dev/null"
        )
