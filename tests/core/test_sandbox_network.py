"""M-C1 t3 (Part A 5.8): the run's network.yml and replaying snapshots/ are protected in a dispatch.

Synthetic values only. The real-bwrap tests run fixture commands inside the sandbox; no network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scieflow.core import sandbox
from scieflow.core.data.dispatch import decide
from scieflow.core.project import Project

HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
REAL_WRAP = sandbox.wrap
CLOUD = {"cmd": "python3 -m stub {prompt}", "enabled": True, "timeout_min": 1}


def _tree(tmp_path: Path) -> dict:
    root = (tmp_path / "project").resolve()
    grant = root / "workspace" / "B"
    (grant / "logs").mkdir(parents=True)
    (grant / "snapshots" / "literature").mkdir(parents=True)
    (grant / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L0", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")
    (root / "config").mkdir()
    return {"root": root, "grant": grant}


def _net(grant: Path, mode: str | None) -> None:
    if mode is None:
        return
    (grant / "network.yml").write_text(yaml.safe_dump({
        "version": 1, "mode": mode, "set_by": "human", "set_at": "2026-10-27T15:00:00Z",
        "reason": "test"}), encoding="utf-8")


def _decide(t):
    return decide(Project(t["root"]), t["grant"], "cloud-a", CLOUD, sandboxed=True,
                  templates_use_endpoint=False)


def _names(paths) -> set[str]:
    return {Path(p).name for p in paths}


# --- decide(): the protected set --------------------------------------------------------------

@pytest.mark.parametrize("mode, network_file, snapshots_folder", [
    ("live", True, False),       # live: the file is protected, snapshots stay writable
    ("record", True, False),     # record: snapshots writable so a dispatch can record
    ("replay", True, True),
    ("offline", True, True),
])
def test_protected_set_follows_the_mode(tmp_path, mode, network_file, snapshots_folder):
    t = _tree(tmp_path)
    _net(t["grant"], mode)
    got = _decide(t)
    assert got.allowed
    assert ((t["grant"] / "network.yml").resolve() in got.protected) == network_file
    assert ((t["grant"] / "snapshots").resolve() in got.protected) == snapshots_folder


def test_an_invalid_file_is_offline_so_snapshots_are_protected(tmp_path):
    t = _tree(tmp_path)
    (t["grant"] / "network.yml").write_text("mode: [broken\n", encoding="utf-8")
    got = _decide(t)
    assert (t["grant"] / "network.yml").resolve() in got.protected
    assert (t["grant"] / "snapshots").resolve() in got.protected


def test_no_network_file_protects_nothing_new(tmp_path):
    t = _tree(tmp_path)
    got = _decide(t)
    assert not (_names(got.protected) & {"network.yml", "snapshots"})


def test_a_linked_network_file_is_not_offered_to_the_layout(tmp_path):
    t = _tree(tmp_path)
    target = tmp_path / "elsewhere.yml"
    target.write_text("keep\n", encoding="utf-8")
    os.symlink(target, t["grant"] / "network.yml")
    got = _decide(t)
    assert all(Path(p).name != "network.yml" for p in got.protected)


def test_a_replaying_run_without_a_snapshot_folder_offers_none(tmp_path):
    t = _tree(tmp_path)
    _net(t["grant"], "replay")
    (t["grant"] / "snapshots" / "literature").rmdir()
    (t["grant"] / "snapshots").rmdir()
    got = _decide(t)
    assert all(Path(p).name != "snapshots" for p in got.protected)


# --- real bwrap: the protected files cannot be changed from inside ----------------------------

def _sh(script: str, layout, grant: Path, *, writable=None) -> subprocess.CompletedProcess:
    argv = REAL_WRAP(["/bin/sh", "-c", script], writable=writable or [grant], cwd=grant,
                     layout=layout)
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)


@needs_bwrap
def test_a_replaying_runs_network_file_and_snapshots_cannot_change_inside(tmp_path):
    t = _tree(tmp_path)
    grant = t["grant"]
    _net(grant, "replay")
    rec = grant / "snapshots" / "literature" / "search-openalex"
    rec.mkdir(parents=True)
    (rec / ("c" * 64 + ".json")).write_text("{}\n", encoding="utf-8")
    net = grant / "network.yml"
    snaps = grant / "snapshots"
    layout = sandbox.Layout(readable=(t["root"],), protected=(net.resolve(), snaps.resolve()))
    before = {p: p.read_bytes() for p in (net, rec / ("c" * 64 + ".json"))}
    script = (
        f"if echo x >> {net} 2>/dev/null; then echo leak-append; fi; "
        f"if mv {net} {grant / 'moved.yml'} 2>/dev/null; then echo leak-rename; fi; "
        f"if rm -f {net} 2>/dev/null; then echo leak-unlink; fi; "
        f"if echo x > {rec / ('d' * 64 + '.json')} 2>/dev/null; then echo leak-create; fi; "
        f"if rm -f {rec / ('c' * 64 + '.json')} 2>/dev/null; then echo leak-remove; fi; "
        "echo done")
    proc = _sh(script, layout, grant)
    assert proc.stdout.splitlines() == ["done"], proc.stdout + proc.stderr
    for path, content in before.items():
        assert path.read_bytes() == content
    sandbox.verify([grant], grant, layout=layout)


@needs_bwrap
def test_the_same_files_are_writable_when_protection_is_dropped(tmp_path):
    """A negative control: without the protected entries the same writes succeed (not vacuous)."""
    t = _tree(tmp_path)
    grant = t["grant"]
    _net(grant, "replay")
    layout = sandbox.Layout(readable=(t["root"],))
    proc = _sh(f"echo x >> {grant / 'network.yml'} 2>/dev/null && echo leak-append; echo done",
               layout, grant)
    assert "leak-append" in proc.stdout.splitlines()


@needs_bwrap
def test_in_record_mode_a_dispatch_can_write_snapshots(tmp_path):
    t = _tree(tmp_path)
    grant = t["grant"]
    _net(grant, "record")
    layout = sandbox.Layout(readable=(t["root"],))   # record: nothing under snapshots/ is protected
    target = grant / "snapshots" / "literature" / ("e" * 64 + ".json")
    proc = _sh(f"echo '{{}}' > {target}; echo done", layout, grant)
    assert proc.stdout.splitlines() == ["done"], proc.stdout + proc.stderr
    assert target.is_file()
