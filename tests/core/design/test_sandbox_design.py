"""M-D1 t4 (plan 5.5, 5.9): the ledger and locked copies are protected inside a cloud dispatch.

The decision lists the protected paths (dispatch.py), and the real bwrap sandbox proves they cannot
be written from inside while design.yml stays writable. Synthetic data only.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scieflow.core import sandbox
from scieflow.core.data.dispatch import decide
from scieflow.core.project import Project

HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
CLOUD = {"cmd": "python3 -m stub {prompt}", "enabled": True, "timeout_min": 1}


def _decide(root: Path, ws: Path):
    return decide(Project(root), ws, "cloud-a", CLOUD, sandboxed=True, templates_use_endpoint=False)


def _ws(root: Path) -> Path:
    """The run of the lroot fixture (its design/ holds the valid RCT fixture already)."""
    ws = root / "workspace" / "r1"
    (ws / "logs").mkdir(exist_ok=True)
    return ws


def test_nothing_is_protected_before_a_lock_exists(lroot):
    ws = _ws(lroot)
    assert _decide(lroot, ws).protected == ()


def test_the_ledger_is_protected_once_it_exists(lroot):
    ws = _ws(lroot)
    (ws / "design" / "lock.jsonl").write_text("")
    assert set(_decide(lroot, ws).protected) == {(ws / "design" / "lock.jsonl").resolve()}


def test_the_locked_folder_is_protected_once_it_exists(lroot):
    ws = _ws(lroot)
    (ws / "design" / "locked" / "v1").mkdir(parents=True)
    assert set(_decide(lroot, ws).protected) == {(ws / "design" / "locked").resolve()}


def test_both_are_protected_beside_the_design_file_which_stays_writable(lroot):
    ws = _ws(lroot)
    (ws / "design" / "lock.jsonl").write_text("")
    (ws / "design" / "locked" / "v1").mkdir(parents=True)
    protected = set(_decide(lroot, ws).protected)
    assert protected == {(ws / "design" / "lock.jsonl").resolve(), (ws / "design" / "locked").resolve()}
    assert (ws / "design" / "design.yml").resolve() not in protected


def test_a_symlinked_ledger_or_locked_folder_is_not_listed(lroot, tmp_path):
    ws = _ws(lroot)
    real = tmp_path / "elsewhere.jsonl"
    real.write_text("")
    (ws / "design" / "lock.jsonl").symlink_to(real)
    outside = tmp_path / "elsewhere-locked"
    outside.mkdir()
    (ws / "design" / "locked").symlink_to(outside)
    assert _decide(lroot, ws).protected == ()


def _sh(script: str, layout, grant: Path) -> subprocess.CompletedProcess:
    argv = sandbox.wrap(["/bin/sh", "-c", script], writable=[grant], cwd=grant, layout=layout)
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)


@needs_bwrap
def test_a_sandboxed_cloud_dispatch_cannot_write_the_ledger_or_the_locked_copies(lroot):
    ws = _ws(lroot)
    ledger = ws / "design" / "lock.jsonl"
    ledger.write_text('{"seq": 1}\n')
    locked = ws / "design" / "locked"
    (locked / "v1").mkdir(parents=True)
    (locked / "v1" / "design.yml").write_text("study_type: rct\n")
    decision = _decide(lroot, ws)
    layout = sandbox.Layout(readable=(lroot.resolve(),), protected=decision.protected)
    script = (
        f"if echo x >> {ledger} 2>/dev/null; then echo leak-append; fi; "
        f"if mv {ledger} {ws / 'moved.jsonl'} 2>/dev/null; then echo leak-rename; fi; "
        f"if rm -f {ledger} 2>/dev/null; then echo leak-unlink; fi; "
        f"if echo x > {locked / 'v1' / 'new.yml'} 2>/dev/null; then echo leak-create; fi; "
        f"if echo x > {locked / 'v1' / 'design.yml'} 2>/dev/null; then echo leak-copy; fi; "
        f"if echo edited > {ws / 'design' / 'design.yml'} 2>/dev/null; then echo design-writable; fi; "
        "echo done")
    proc = _sh(script, layout, ws)
    assert proc.stdout.splitlines() == ["design-writable", "done"], proc.stdout + proc.stderr
    assert ledger.read_text() == '{"seq": 1}\n'
    assert (locked / "v1" / "design.yml").read_text() == "study_type: rct\n"
    assert (ws / "design" / "design.yml").read_text() == "edited\n"
    sandbox.verify([ws], ws, layout=layout)
