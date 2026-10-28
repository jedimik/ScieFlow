"""Shared fixtures for the M-D1 t4 tests: lock, amend, the gate hook and ledger protection (plan 5.5-5.9).

Synthetic data only. The run holds the valid RCT fixture design and its power file; the project root
holds copies of the schemas and the reporting checklists the design code reads from the root.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scieflow.core.project import Project
from scieflow.core.run import status

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "tests" / "fixtures" / "design"


@pytest.fixture
def lroot(tmp_path):
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    shutil.copytree(REPO / "config" / "reporting", tmp_path / "config" / "reporting")
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    (ws / "design" / "power").mkdir(parents=True)
    shutil.copy(FIX / "power-rct-ttest.json", ws / "design" / "power" / "rct-ttest.json")
    shutil.copy(FIX / "rct.yml", ws / "design" / "design.yml")
    return tmp_path


@pytest.fixture
def lproj(lroot):
    return Project(lroot)


@pytest.fixture
def lws(lroot):
    return lroot / "workspace" / "r1"
