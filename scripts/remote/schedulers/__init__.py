"""Scheduler adapter registry.

REGISTRY is read at call time, never copied, so policy.load_remote and the
command functions always see the current entries (tests monkeypatch it).
PBS is certified. Slurm, plain SSH and local are experimental and need the
allow_experimental_scheduler opt-in (policy.py).
"""

from remote.errors import PolicyError
from remote.schedulers.local import LocalScheduler
from remote.schedulers.pbs import PbsScheduler
from remote.schedulers.slurm import SlurmScheduler
from remote.schedulers.ssh import SshScheduler

REGISTRY: dict = {}


def _register(adapter) -> None:
    REGISTRY[adapter.name] = adapter


_register(PbsScheduler())
_register(SlurmScheduler())
_register(SshScheduler())
_register(LocalScheduler())


def get(name):
    """Return the adapter for ``name`` or raise PolicyError."""
    if isinstance(name, str) and name in REGISTRY:
        return REGISTRY[name]
    raise PolicyError(
        f"scheduler {name!r} is not supported (supported: {', '.join(names())})"
    )


def names() -> tuple:
    return tuple(sorted(REGISTRY))
