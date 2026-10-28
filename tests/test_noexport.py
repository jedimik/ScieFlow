"""M-C1 t5 (plan 5.10; Part B t5 item 4): the no-export invariant (A-15) and the D17 contract.

(1) a run's archive never packs anything under scratch/, .cache/, .uv-cache/;
(2) with skip_rebuildable=False the agent home (scratch/agent-home) is still never packed;
(3) full-text evidence run with HOME = the run's agent home writes its cache only under that home;
(4) the Gateway is the only writer of snapshots/ (AST), and research/lib/fulltext.py imports
    neither the Gateway nor the snapshot store, so full text never becomes a snapshot.
All data is synthetic; no network (the full-text steps are faked).
"""

from __future__ import annotations

import ast
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "scieflow"

from scieflow.integrations.scite import cache as scite_cache  # noqa: E402
from scieflow.research.lib import fulltext  # noqa: E402
from sflib import archive  # noqa: E402


def _tree(run: Path) -> None:
    files = {
        "data/measures.csv": "id,value\n1,0.5\n",
        "logs/prompt.md": "prompt\n",
        "scratch/tests/out.txt": "pytest output\n",
        "scratch/agent-home/claude/.cache/scieflow/scite/cache/ab.json": "{\"full\": \"text\"}\n",
        "scratch/agent-home/claude/.cache/fulltext.json": "{\"passage\": \"x\"}\n",
        ".uv-cache/wheel.bin": "uv\n",
        ".cache/tool.bin": "cache\n",
        "scratch/keep-me.txt": "scratch that is not an agent home\n",
    }
    for rel, text in files.items():
        path = run / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _packed(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return {n.rstrip("/") for n in zf.namelist()}


# --- (1) and (2): what an archive packs ----------------------------------------------------


def test_build_zip_never_packs_scratch_or_caches(tmp_path):
    run = tmp_path / "run"
    _tree(run)
    out = tmp_path / "out.zip"
    archive.build_zip(run, out)
    names = _packed(out)
    assert "data/measures.csv" in names and "logs/prompt.md" in names
    assert not [n for n in names if n.startswith(("scratch/", ".cache/", ".uv-cache/"))]
    assert not [n for n in names if "agent-home" in n or "scite/cache" in n]


def test_skip_rebuildable_false_still_never_packs_the_agent_home(tmp_path):
    run = tmp_path / "run"
    _tree(run)
    out = tmp_path / "out.zip"
    archive.build_zip(run, out, skip_rebuildable=False)
    names = _packed(out)
    assert not [n for n in names if "agent-home" in n or "scite/cache" in n]
    assert "scratch/keep-me.txt" in names          # the switch still applies to other scratch


# --- (3): full text writes its cache under the run's agent home only ----------------------


def test_full_text_cache_is_written_only_under_the_agent_home(tmp_path, monkeypatch):
    run = tmp_path / "run"
    agent_home = run / "scratch" / "agent-home" / "claude"
    agent_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(agent_home))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    cache = scite_cache.Cache(Path(agent_home) / ".cache" / "scieflow" / "scite" / "cache",
                              {"metadata": 30, "search": 7})

    meta = {"doi": "10.5555/fixture.noexport.001", "pmcid": "PMC999", "open_access": True,
            "abstract": "A synthetic abstract."}
    xml = "<article><body><sec><p>Synthetic passage about the target term.</p></sec></body></article>"
    monkeypatch.setattr(fulltext, "resolve", lambda doi, **kw: meta)
    monkeypatch.setattr(fulltext, "fetch_xml", lambda pmcid, **kw: xml)
    fulltext.evidence(meta["doi"], ["target term"], cache=cache, sleep=lambda s: None)

    written = [p for p in run.rglob("*") if p.is_file()]
    assert written, "the full-text step must write its cache"
    assert all(agent_home in p.parents for p in written)
    assert not [p for p in written if "agent-home" not in p.parts]


# --- (4): who writes snapshots, and full text never imports the store ----------------------


def _modules_calling_snapshot_write() -> set[str]:
    hits: set[str] = set()
    for path in SRC.rglob("*.py"):
        if "_vendor" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "write"):
                value = node.func.value
                name = value.id if isinstance(value, ast.Name) else (
                    value.attr if isinstance(value, ast.Attribute) else "")
                if name == "snapshots":
                    hits.add(path.relative_to(SRC).as_posix())
    return hits


def test_only_the_gateway_writes_snapshots():
    assert _modules_calling_snapshot_write() == {"research/lib/providers.py"}


def test_full_text_module_does_not_import_the_gateway_or_the_snapshot_store():
    tree = ast.parse((SRC / "research" / "lib" / "fulltext.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    forbidden = {"scieflow.research.lib.providers", "scieflow.research.lib.snapshots",
                 "providers", "snapshots"}
    assert not (imported & forbidden), imported & forbidden
