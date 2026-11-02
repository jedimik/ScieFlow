"""Shared fixtures for the M-H t2 systematic-review tests: a temporary project root with copies of
the schemas and reporting checklists, one run under workspace/r1 holding the synthetic SR design.
Synthetic data only; no network, no agent."""

import shutil
from pathlib import Path

import pytest

from scieflow.core.project import Project
from scieflow.core.run import status

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "tests" / "fixtures" / "design"


@pytest.fixture
def srroot(tmp_path):
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    shutil.copytree(REPO / "config" / "reporting", tmp_path / "config" / "reporting")
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    (ws / "design").mkdir()
    shutil.copy(FIX / "systematic-review.yml", ws / "design" / "design.yml")
    return tmp_path


@pytest.fixture
def srproj(srroot):
    return Project(srroot)


@pytest.fixture
def srws(srroot):
    return srroot / "workspace" / "r1"
