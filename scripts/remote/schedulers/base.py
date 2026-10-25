"""Scheduler adapter protocol: the dataclasses and the base class.

An adapter turns values that policy.py has already validated into fixed
command strings, and reads the scheduler's answer back into ScieFlow's six
job states. It never accepts free-form scheduler flags and never decides
whether a request is allowed; that stays in policy.py.
"""

from dataclasses import dataclass, field

from remote.errors import PolicyError  # noqa: F401  (re-exported for adapters)

# Ledger states. "unknown" counts as active for the concurrency ceiling
# (M-E-D6), so it never frees a slot.
STATES = ("queued", "running", "done", "failed", "cancelled", "unknown")

# The abstract operations remote.py dispatches. policy.check_scheduler_op
# accepts either the abstract name or the adapter's concrete alias.
ABSTRACT_OPS = ("submit", "status", "cancel", "logs")


class SubmitParseError(Exception):
    """The submit command returned no usable job id."""


def walltime_to_seconds(walltime: str) -> int:
    """Convert a validated HH:MM:SS walltime to seconds.

    Adapters cannot import policy.py (it imports this registry), so the
    conversion lives here. policy.walltime_seconds does the same job.
    """
    parts = str(walltime).split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise PolicyError(f"bad walltime '{walltime}' (expected HH:MM:SS)")
    hours, minutes, seconds = (int(p) for p in parts)
    return hours * 3600 + minutes * 60 + seconds


def require_digits(value: str, what: str) -> str:
    """Refuse a job id component that is not plain digits (invariant d)."""
    if not isinstance(value, str) or not value.isdigit():
        raise PolicyError(f"unsafe {what} (expected digits): '{value}'")
    return value


@dataclass(frozen=True)
class JobSpec:
    """A submission whose fields policy.py has already validated and clamped."""

    name: str
    dir: str
    script: str
    walltime: str
    cpus: int
    mem_gb: int
    gpus: int
    queue: str
    scratch_type: str
    scratch_gb: int
    env: dict = field(default_factory=dict)


@dataclass(frozen=True)
class JobRef:
    """A job as recorded in the ledger."""

    job_id: str
    dir: str = ""
    job_name: str = ""


@dataclass
class JobStatus:
    state: str | None
    exit_code: str = "-"
    details: dict = field(default_factory=dict)
    parse_error: bool = False


class Scheduler:
    """Base class. Concrete adapters override the class attributes and methods."""

    name = ""
    maturity = "experimental"          # "certified" or "experimental"
    transport = "ssh"                  # "ssh" or "local"
    concrete_ops: dict = {}            # abstract op -> scheduler command name
    array_support = False
    labels: dict = {}                  # {"submit": "...", "status": "..."}
    detail_fields: tuple = ()
    needs_kerberos = True
    needs_ledger_dir = False           # ledger entry must carry dir and job_name

    def validate_remote(self, entry: dict) -> None:
        """Extra per-adapter checks on a remote's config entry."""

    def resource_args(self, spec: JobSpec) -> str:
        raise NotImplementedError

    def notices(self, spec: JobSpec) -> list[str]:
        return []

    def submit_command(self, spec: JobSpec) -> str:
        raise NotImplementedError

    def parse_submit(self, stdout: str) -> str:
        raise NotImplementedError

    def status_command(self, job: JobRef) -> str:
        raise NotImplementedError

    def parse_status(self, stdout: str, job: JobRef) -> JobStatus:
        raise NotImplementedError

    def cancel_command(self, job: JobRef) -> str:
        raise NotImplementedError

    def log_paths(self, job: JobRef) -> tuple[str, str]:
        raise NotImplementedError

    def logs_command(self, job: JobRef) -> str:
        raise NotImplementedError
