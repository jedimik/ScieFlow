"""M-C1 t5 (plan 5.10; Part B t5 item 4): the no-export invariant (A-15) and the D17 contract.

(1) a run's archive never packs anything under scratch/, .cache/, .uv-cache/;
(2) with skip_rebuildable=False the agent home (scratch/agent-home) is still never packed;
(3) full-text evidence run with HOME = the run's agent home writes its cache only under that home,
    using the production default cache location (scite ledger default_dir), not a path the test builds;
(4) the Gateway is the only automatic writer of snapshots/ (AST, alias-aware: module aliases,
    from-imports, star imports, factory functions returning the store), the one human-only
    `research snapshots copy` is named explicitly, and research/lib/fulltext.py imports neither the
    Gateway nor the snapshot store (absolute and relative forms), so full text never becomes a snapshot.
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
from scieflow.integrations.scite import ledger as scite_ledger  # noqa: E402
from scieflow.research.lib import fulltext  # noqa: E402
from sflib import archive  # noqa: E402

STORE = "snapshots"
STORE_FILE = "research/lib/snapshots.py"
MUTATORS = frozenset({"write", "copy_into", "_atomic_bytes"})
# Named so distinctively that any receiver of them is the store (no other module defines them).
DISTINCT_MUTATORS = frozenset({"copy_into", "_atomic_bytes"})
# The only writers allowed: the Gateway (automatic, mode-aware) and the human-only
# `research snapshots copy` (plan 5.5: refused inside a dispatch and from L2 runs).
ALLOWED_WRITERS = frozenset({
    ("research/lib/providers.py", "write"),
    ("research/cli.py", "copy_into"),
})
FORBIDDEN_FOR_FULLTEXT = frozenset({"providers", "snapshots"})


# --- (1) and (2): what an archive packs ----------------------------------------------------


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
    # The production default location, resolved the way core/review/evidence.py resolves it.
    default = scite_ledger.default_dir()
    assert agent_home in default.parents, "precondition: the default cache must live under HOME"
    cache = scite_cache.Cache(default / "cache", {"metadata": 30, "search": 7})

    meta = {"doi": "10.5555/fixture.noexport.001", "pmcid": "PMC999", "open_access": True,
            "abstract": "A synthetic abstract."}
    xml = "<article><body><sec><p>Synthetic passage about the target term.</p></sec></body></article>"
    monkeypatch.setattr(fulltext, "resolve", lambda doi, **kw: meta)
    monkeypatch.setattr(fulltext, "fetch_xml", lambda pmcid, **kw: xml)
    fulltext.evidence(meta["doi"], ["target term"], cache=cache, sleep=lambda s: None)

    # Everything the test wrote anywhere under tmp_path, not only under the run folder.
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written, "the full-text step must write its cache"
    assert all(agent_home in p.parents for p in written), [str(p) for p in written]


# --- (4): who writes snapshots, and full text never imports the store ----------------------


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _store_writers(source: str) -> set[str]:
    """Names of the store's mutating functions that `source` calls, through any alias we can resolve.

    Resolved: `from ...lib import snapshots [as x]`, `import ...snapshots [as x]`, `from ...snapshots
    import write [as w]`, star imports from the store, names assigned from a store expression, and
    functions that return the store module (`def _snapshots(): return snapshots`).
    Distinctive mutators (`copy_into`, `_atomic_bytes`) are flagged on any receiver; `write` only
    when its receiver is the store.
    """
    tree = ast.parse(source)
    store_names: set[str] = set()     # local names bound to the store module
    store_funcs: dict[str, str] = {}  # local name -> store mutator it is bound to
    store_factories: set[str] = set() # functions returning the store module
    star = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            segs = (node.module or "").split(".") if node.module else []
            for a in node.names:
                if a.name == "*":
                    star = star or STORE in segs
                elif a.name == STORE:
                    store_names.add(a.asname or a.name)
                elif STORE in segs and a.name in MUTATORS:
                    store_funcs[a.asname or a.name] = a.name
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] == STORE:
                    store_names.add(a.asname or a.name)

    def is_store(expr: ast.AST) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in store_names
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            return expr.func.id in store_factories
        return _dotted(expr).split(".")[-1] == STORE

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name not in store_factories:
                if any(isinstance(r, ast.Return) and r.value is not None and is_store(r.value)
                       for r in ast.walk(node)):
                    store_factories.add(node.name)
                    changed = True
            if isinstance(node, ast.Assign) and is_store(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in store_names:
                        store_names.add(target.id)
                        changed = True

    writers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in MUTATORS:
            if fn.attr in DISTINCT_MUTATORS or is_store(fn.value):
                writers.add(fn.attr)
        elif isinstance(fn, ast.Name):
            if fn.id in store_funcs:
                writers.add(store_funcs[fn.id])
            elif star and fn.id in MUTATORS:
                writers.add(fn.id)
    return writers


def _writers_by_module() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if "_vendor" in path.parts or rel == STORE_FILE:
            continue
        for name in _store_writers(path.read_text(encoding="utf-8")):
            found.add((rel, name))
    return found


def test_only_the_gateway_and_the_human_copy_write_snapshots():
    assert _writers_by_module() == ALLOWED_WRITERS


@pytest.mark.parametrize("source, expected", [
    ("from scieflow.research.lib import snapshots as s\ns.write(ws)\n", {"write"}),
    ("from scieflow.research.lib import snapshots\nsnapshots.write(ws)\n", {"write"}),
    ("from . import snapshots\nsnapshots.write(ws)\n", {"write"}),
    ("import scieflow.research.lib.snapshots as s\ns.copy_into(a, b)\n", {"copy_into"}),
    ("import scieflow.research.lib.snapshots\nscieflow.research.lib.snapshots.write(ws)\n", {"write"}),
    ("from scieflow.research.lib.snapshots import write as w\nw(ws)\n", {"write"}),
    ("from scieflow.research.lib.snapshots import *\nwrite(ws)\n", {"write"}),
    ("from scieflow.research.lib import snapshots\ndef f():\n    return snapshots\n"
     "def g(ws):\n    f().write(ws)\n", {"write"}),
    ("from scieflow.research.lib import snapshots\ndef f():\n    return snapshots\n"
     "snaps = f()\nsnaps.write(ws)\n", {"write"}),
    ("def h(src, dst):\n    return anything.copy_into(src, dst)\n", {"copy_into"}),
    # benign: reads of the store and writes to unrelated objects are not writers
    ("from scieflow.research.lib import snapshots\nsnapshots.write_failed(ws)\nhandle.write(data)\n",
     set()),
])
def test_the_writer_scan_sees_every_alias_form(source, expected):
    assert _store_writers(source) == expected


FULLTEXT = SRC / "research" / "lib" / "fulltext.py"


def _import_paths(source: str) -> set[str]:
    """Dotted paths a module imports, absolute or relative, including imported names."""
    paths: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            base = node.module.split(".") if node.module else []
            if base:
                paths.add(".".join(base))
            paths.update(".".join(base + [a.name]) for a in node.names)
        elif isinstance(node, ast.Import):
            paths.update(a.name for a in node.names)
    return paths


def _forbidden_imports(source: str) -> set[str]:
    return {p for p in _import_paths(source) if set(p.split(".")) & FORBIDDEN_FOR_FULLTEXT}


def test_full_text_module_does_not_import_the_gateway_or_the_snapshot_store():
    assert _forbidden_imports(FULLTEXT.read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize("snippet", [
    "from . import snapshots\n",
    "from .providers import Gateway\n",
    "from ..lib import providers\n",
    "from scieflow.research.lib import providers as p\n",
    "import scieflow.research.lib.snapshots\n",
])
def test_the_import_pin_sees_relative_and_absolute_forms(snippet):
    assert _forbidden_imports(snippet), snippet
