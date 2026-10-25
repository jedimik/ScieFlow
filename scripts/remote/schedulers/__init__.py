"""Scheduler adapter registry.

REGISTRY is read at call time, never copied, so policy.load_remote and the
command functions always see the current entries (tests monkeypatch it).
Only PBS is registered in M-E t1; Slurm, SSH and local follow in t2.
"""

from remote.errors import PolicyError
from remote.schedulers.pbs import PbsScheduler

REGISTRY: dict = {}


def _register(adapter) -> None:
    REGISTRY[adapter.name] = adapter


_register(PbsScheduler())


def get(name):
    """Return the adapter for ``name`` or raise PolicyError."""
    if isinstance(name, str) and name in REGISTRY:
        return REGISTRY[name]
    raise PolicyError(
        f"scheduler {name!r} is not supported (supported: {', '.join(names())})"
    )


def names() -> tuple:
    return tuple(sorted(REGISTRY))
