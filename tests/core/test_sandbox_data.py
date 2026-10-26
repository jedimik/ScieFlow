"""M-B t3 (plan 5.4 sandbox inputs, 5.5): hiding L2 data, read-only protection, verify proofs.

Every path, file and value here is synthetic and obviously fake. The real-bwrap tests run the
fixture commands inside the sandbox; no network, no real credential, no real home file.
"""

import inspect
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_run, sandbox, service
from scieflow.core.project import Project
from scieflow.core.run import budget, status

ROLE = "loop.experiment"
HAVE_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
REAL_WRAP = sandbox.wrap


def _tree(tmp_path: Path) -> dict:
    """A project-like tree: root/workspace/{A,B}, grant B, and a fake L2 file in A."""
    root = (tmp_path / "project").resolve()
    grant = root / "workspace" / "B"
    other = root / "workspace" / "A"
    (grant / "logs").mkdir(parents=True)
    (other / "inputs").mkdir(parents=True)
    (other / "inputs" / "x.csv").write_text("synthetic,L2,fake\n")
    return {"root": root, "grant": grant, "other": other, "secret": other / "inputs" / "x.csv"}


def _sh(argv_script: str, layout, grant: Path, *, writable=None) -> subprocess.CompletedProcess:
    argv = REAL_WRAP(["/bin/sh", "-c", argv_script], writable=writable or [grant], cwd=grant,
                     layout=layout)
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)


def _leaky_without(field: str):
    """A wrap() that drops one data field from the layout it is given (a leaky layout)."""
    def wrapped(argv, **kw):
        if kw.get("layout") is not None:
            kw["layout"] = replace(kw["layout"], **{field: ()})
        return REAL_WRAP(argv, **kw)
    return wrapped


# --- A. the new Layout fields (pure) ------------------------------------------

def test_layout_data_fields_default_empty_and_are_validated(tmp_path):
    assert sandbox.Layout().data_hidden == ()
    assert sandbox.Layout().protected == ()
    with pytest.raises(TypeError):
        sandbox.Layout(data_hidden=[tmp_path])
    with pytest.raises(TypeError):
        sandbox.Layout(data_hidden=(Path("relative"),))
    with pytest.raises(TypeError):
        sandbox.Layout(protected=[tmp_path])
    with pytest.raises(TypeError):
        sandbox.Layout(protected=(Path("relative"),))


def test_dispatch_layout_takes_the_data_sets_with_empty_defaults():
    params = inspect.signature(sandbox.dispatch_layout).parameters
    assert params["data_hidden"].default == ()
    assert params["protected"].default == ()


# --- B. wrap() argv golden checks (real bwrap binary, no run) -----------------

@needs_bwrap
def test_wrap_overlays_data_hidden_like_hidden_once_before_the_writable_binds(tmp_path):
    t = _tree(tmp_path)
    layout = sandbox.Layout(readable=(t["root"],), hidden=(t["other"],),
                            data_hidden=(t["other"], t["secret"]))
    argv = REAL_WRAP(["/bin/true"], writable=[t["grant"]], cwd=t["grant"], layout=layout)
    assert argv.count(str(t["other"])) == 1          # one overlay for a path in both lists
    assert argv[argv.index(str(t["other"])) - 1] == "--tmpfs"
    assert ["--ro-bind", "/dev/null", str(t["secret"])] == argv[
        argv.index("/dev/null") - 1:argv.index("/dev/null") + 2]
    first_writable = argv.index("--bind")
    assert argv.index(str(t["other"])) < first_writable
    assert argv.index(str(t["secret"])) < first_writable


@needs_bwrap
def test_wrap_binds_protected_read_only_after_masks_and_before_the_home(tmp_path):
    t = _tree(tmp_path)
    protected = t["grant"] / "data.yml"
    protected.write_text("levels: []\n")
    released = t["grant"] / "released"
    released.mkdir()
    mask = [t["grant"] / ".repo"]
    home = t["grant"] / "scratch" / "agent-home" / "tracer"
    layout = sandbox.Layout(readable=(t["root"],), home=home,
                            protected=(protected, released))
    argv = REAL_WRAP(["/bin/true"], writable=[t["grant"]], cwd=t["grant"], layout=layout,
                     mask=mask)
    ro = [i for i, tok in enumerate(argv)
          if tok == "--ro-bind" and i + 2 < len(argv) and argv[i + 1] == argv[i + 2]
          and argv[i + 1] in (str(protected), str(released))]
    assert len(ro) == 2
    grant_bind = argv.index("--bind", 0)
    mask_tmpfs = argv.index("--tmpfs", 0)
    home_bind = [i for i, tok in enumerate(argv) if tok == "--bind" and argv[i + 1] == str(home)]
    assert grant_bind < max(ro) and mask_tmpfs < max(ro)
    assert max(ro) < home_bind[0]


@needs_bwrap
@pytest.mark.parametrize("case", ["missing", "symlink", "outside", "grant-itself"])
def test_wrap_refuses_a_protected_path_that_is_missing_linked_outside_or_the_grant(tmp_path, case):
    t = _tree(tmp_path)
    target = t["grant"] / "data.yml"
    target.write_text("x")
    if case == "missing":
        protected = t["grant"] / "absent.yml"
    elif case == "symlink":
        protected = t["grant"] / "link.yml"
        protected.symlink_to(target)
    elif case == "outside":
        protected = t["other"] / "inputs" / "x.csv"
    else:
        protected = t["grant"]
    layout = sandbox.Layout(readable=(t["root"],), protected=(protected,))
    with pytest.raises(sandbox.SandboxError):
        REAL_WRAP(["/bin/true"], writable=[t["grant"]], cwd=t["grant"], layout=layout)


# --- C. real bwrap: hiding and protection, then the verify proofs --------------

@needs_bwrap
def test_a_cloud_dispatch_cannot_list_or_read_another_runs_l2_file(tmp_path):
    t = _tree(tmp_path)
    layout = sandbox.Layout(readable=(t["root"],), hidden=(t["other"],),
                            data_hidden=(t["other"],))
    proc = _sh(f"ls {t['other']}; cat {t['secret']}", layout, t["grant"])
    assert proc.stdout == "" and proc.returncode != 0
    sandbox.verify([t["grant"]], t["grant"], layout=layout)


@needs_bwrap
def test_verify_refuses_when_a_data_hidden_directory_is_visible(monkeypatch, tmp_path):
    t = _tree(tmp_path)
    layout = sandbox.Layout(readable=(t["root"],), data_hidden=(t["other"],))
    monkeypatch.setattr(sandbox, "wrap", _leaky_without("data_hidden"))
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.verify([t["grant"]], t["grant"], layout=layout)
    assert not [p for p in t["other"].iterdir() if p.name.startswith(sandbox.MARKER_PREFIX)]


@needs_bwrap
def test_verify_refuses_when_a_data_hidden_file_is_readable(monkeypatch, tmp_path):
    t = _tree(tmp_path)
    layout = sandbox.Layout(readable=(t["root"],), data_hidden=(t["secret"],))
    monkeypatch.setattr(sandbox, "wrap", _leaky_without("data_hidden"))
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.verify([t["grant"]], t["grant"], layout=layout)


@needs_bwrap
def test_verify_removes_its_data_markers_on_success(tmp_path):
    t = _tree(tmp_path)
    layout = sandbox.Layout(readable=(t["root"],), data_hidden=(t["other"],))
    sandbox.verify([t["grant"]], t["grant"], layout=layout)
    assert not [p for p in t["other"].iterdir() if p.name.startswith(sandbox.MARKER_PREFIX)]


@needs_bwrap
def test_protected_run_files_cannot_be_written_renamed_or_removed_from_inside(tmp_path):
    t = _tree(tmp_path)
    grant = t["grant"]
    data = grant / "data.yml"
    data.write_text("levels: []\n")
    released = grant / "released"
    released.mkdir()
    (released / "a.txt").write_text("synthetic release\n")
    layout = sandbox.Layout(readable=(t["root"],), protected=(data, released))
    script = (
        f"if echo x >> {data} 2>/dev/null; then echo leak-append; fi; "
        f"if mv {data} {grant / 'moved.yml'} 2>/dev/null; then echo leak-rename; fi; "
        f"if rm -f {data} 2>/dev/null; then echo leak-unlink; fi; "
        f"if echo x > {released / 'new.txt'} 2>/dev/null; then echo leak-create; fi; "
        f"if mv {released / 'a.txt'} {released / 'b.txt'} 2>/dev/null; then echo leak-dir; fi; "
        "echo done")
    proc = _sh(script, layout, grant)
    assert proc.stdout.splitlines() == ["done"], proc.stdout + proc.stderr
    assert data.read_text() == "levels: []\n"
    assert (released / "a.txt").read_text() == "synthetic release\n"
    assert not (released / "new.txt").exists()
    sandbox.verify([grant], grant, layout=layout)


@needs_bwrap
def test_verify_refuses_when_a_protected_path_is_writable(monkeypatch, tmp_path):
    t = _tree(tmp_path)
    data = t["grant"] / "data.yml"
    data.write_text("levels: []\n")
    layout = sandbox.Layout(readable=(t["root"],), protected=(data,))
    monkeypatch.setattr(sandbox, "wrap", _leaky_without("protected"))
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.verify([t["grant"]], t["grant"], layout=layout)


# --- D. the CLI and conversation paths hand the decision sets to the layout ----

def _l2_run(root: Path, slug: str) -> Path:
    ws = root / "workspace" / slug
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    (ws / "config.yml").write_text(f"slug: {slug}\napproval: autonomous\n")
    status.write_status(ws, status.new_status(slug, "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 60))
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L0",
        "levels": [{"path": "inputs/x.csv", "level": "L2", "kind": "tabular",
                    "reason": "synthetic", "declared_by": "human",
                    "declared_at": "2026-10-26T10:00:00Z"}],
        "pending_releases": [], "released": [], "imports": []}, sort_keys=False))
    return ws


def test_cli_prepare_hands_the_hidden_and_protected_sets_to_the_layout(project, monkeypatch):
    other = _l2_run(project.root, "r2").resolve()
    seen: dict = {}
    real = sandbox.dispatch_layout

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(sandbox, "dispatch_layout", spy)
    prompt = project.root / "workspace" / "r1" / "logs" / "p.md"
    prompt.write_text("synthetic prompt\n")
    agent_run.prepare(Project(project.root), "stub", prompt, role=ROLE)
    assert tuple(seen["data_hidden"]) == (other,)
    assert tuple(seen["protected"]) == ()


def test_turn_layout_and_verify_carry_the_hidden_sets(k_conversational_project, monkeypatch,
                                                     tmp_path_factory):
    project, ws, _ = k_conversational_project
    other = _l2_run(project.root, "r2").resolve()
    service.set_conversation_agent(project, "r1", "k-protocol-fixture")
    resolved = agent_run.resolve_turn(project, "k-protocol-fixture", run_dir=ws, session=None)
    assert other in tuple(resolved.layout.data_hidden)
    seen = []
    real_verify = sandbox.verify

    def spy(writable, cwd, *, mask=None, layout=None):
        seen.append(layout)
        return real_verify(writable, cwd, mask=mask, layout=layout)
    monkeypatch.setattr(sandbox, "verify", spy)
    service.say(project, "r1", "hello")
    assert seen and other in tuple(seen[0].data_hidden)
