"""Remote campaign bundle, generated scripts and transfer check (M-D3 plan 5.3).

RED stub (M-D3 t2): the signatures and types the tests use. Every behaviour
raises NotImplementedError until the GREEN commit.
"""

from __future__ import annotations

from dataclasses import dataclass

MODES = ("apptainer", "direct")


class BundleError(ValueError):
    """A refusal raised before anything is written (plan 5.3)."""


@dataclass(frozen=True)
class Layout:
    """Remote layout R/bundle/..., R/runs/<id>/ (plan 5.3)."""

    remote_dir: str
    input_name: str


@dataclass(frozen=True)
class Markers:
    """Marker files of one remote run (plan 5.3 read_markers)."""

    exit_code: int | None
    started: str | None
    finished: str | None
    times_from_fetch: bool
    system: str | None
    machine: str | None
    runtime: str | None


def direct_python(scheduler: str) -> str:
    raise NotImplementedError("M-D3 t2 GREEN pending: direct_python")


def task_argv(stage, spec, layout: Layout, mode: str, python: str) -> list[str]:
    raise NotImplementedError("M-D3 t2 GREEN pending: task_argv")


def build_bundle(campaign, stage, tasks, mode: str, dest, *, remote_root: str, python: str) -> dict:
    raise NotImplementedError("M-D3 t2 GREEN pending: build_bundle")


def read_markers(run_dir, fetched_at: str) -> Markers:
    raise NotImplementedError("M-D3 t2 GREEN pending: read_markers")


def verify_transfer(run_dir) -> tuple[bool, str | None, int]:
    raise NotImplementedError("M-D3 t2 GREEN pending: verify_transfer")
