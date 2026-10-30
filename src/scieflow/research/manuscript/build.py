"""`scieflow manuscript build` (M-F 5.12): compile manuscript/ in a scratch copy with shell escape off.

The source tree is staged into workspace/<run>/scratch/manuscript-build/ and compiled there with a fixed
argv (no shell), a fixed environment (HOME and TEXMFVAR inside scratch, shell_escape=f, openout_any=p)
and a timeout per call. Engine and class detection come from the vendored latex_engine module. tectonic
is only used when named, and never in a replay, offline or L2 run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from scieflow._vendor.researcher_core import latex_engine as _engine
from scieflow.core import events
from scieflow.core import network as _network
from scieflow.core.data import levels as _levels
from scieflow.research.journal import state
from scieflow.research.manuscript import tex, templates

VERSION = 1
BUILD_DIR = "build"
BUILD_JSON = "build.json"
STAGE = Path("scratch") / "manuscript-build"
ENGINE_CHOICES = ("latexmk", "pdflatex", "xelatex", "lualatex")
KPSE_TIMEOUT = 30
TAIL_LINES = 40
TREE_MAX_BYTES = 200_000_000
ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


class BuildError(Exception):
    """A refusal before any compile: `rule` names it, `message` says what to do."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


@dataclass
class BuildResult:
    ok: bool
    engine: str | None
    argv: list = field(default_factory=list)
    returncodes: list = field(default_factory=list)
    pdf_sha256: str | None = None
    source_tree_sha256: str | None = None
    cls: str | None = None
    bibtex_style: str | None = None
    notes: list = field(default_factory=list)
    log_tail: str = ""

    def to_dict(self) -> dict:
        return {"version": VERSION, "ok": self.ok, "engine": self.engine, "argv": self.argv,
                "returncodes": self.returncodes, "pdf_sha256": self.pdf_sha256,
                "source_tree_sha256": self.source_tree_sha256, "class": self.cls,
                "bibtex_style": self.bibtex_style, "notes": self.notes}


def _which(name: str):
    return _engine._which(name)


def _run(argv, cwd, env, timeout):
    """The one subprocess seam: fixed argv, no shell, no stdin, the given environment and timeout."""
    return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, shell=False,
                          stdin=subprocess.DEVNULL)


def _refuse_links(folder: Path) -> None:
    for current, dirs, files in os.walk(folder):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_symlink():
                rel = path.relative_to(folder).as_posix()
                raise BuildError("symlink", f"{rel} is a symbolic link; the manuscript tree must not have any")


def _tree_bytes(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def _stage(folder: Path, run: Path) -> Path:
    """Copy the source tree (regular files, no by-products, no build/drafts/letters) into scratch."""
    scratch = run / "scratch"
    if scratch.is_symlink():
        raise BuildError("symlink", "scratch/ is a symbolic link; refused")
    stage = run / STAGE
    if stage.is_symlink() or (stage.exists() and not stage.is_dir()):
        raise BuildError("symlink", "the build folder is a symbolic link or not a folder; refused")
    scratch.mkdir(exist_ok=True)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if not path.is_file() or rel.parts[0] in tex.TREE_EXCLUDED_DIRS:
            continue
        if path.name.endswith(tex.TEX_BYPRODUCTS) or ".bak-" in path.name:
            continue
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    return stage


def _kpse_has(name: str, cwd: Path, env: dict, notes: list) -> bool:
    kpsewhich = _which("kpsewhich")
    if kpsewhich is None:
        notes.append(f"{name}: lookup skipped (kpsewhich is not installed)")
        return True
    try:
        result = _run([kpsewhich, name], cwd, env, KPSE_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _check_asset(folder: Path, name: str, stage: Path, env: dict, notes: list, source: str) -> None:
    if not ASSET_RE.match(name):
        raise BuildError("asset-name", f"{name!r} is not a plain TeX file name; refused")
    if (folder / name).is_file():
        return
    if not _kpse_has(name, stage, env, notes):
        raise BuildError("asset-missing", f"{name} is neither in manuscript/ nor installed ({source})")


def _class_source(cls: str) -> str:
    entry = templates.load_registry().get(cls)
    if entry is None:
        return "not a registry class: put it in manuscript/ or install it"
    if entry["source"] == "publisher":
        return "publisher-supplied: put it in manuscript/"
    if entry["source"] == "ctan":
        return f"CTAN package {entry.get('package', cls)}: install it in your TeX distribution"
    return "part of TeX Live"


def _resolve_engine(engine, env):
    if engine:
        try:
            found = _engine.find_engine(engine, env={})
        except FileNotFoundError as exc:
            raise BuildError("engine-missing", str(exc)) from exc
        if found is None:
            raise BuildError("engine-missing", _engine.install_hint())
        return found
    for name in ENGINE_CHOICES:
        try:
            found = _engine.find_engine(name, env={})
        except FileNotFoundError:
            continue
        if found is not None:
            return found
    raise BuildError("no-engine", _engine.install_hint())


def _compile(found, stage: Path, env: dict, timeout: int, main: str):
    """(ok, argv, returncodes, outputs): the engine's passes, ScieFlow's loop for a raw engine."""
    argv = _engine.build_commands(found, main)
    argv.insert(1, "--untrusted" if found.kind == "tectonic" else "-no-shell-escape")
    returncodes: list = []
    outputs: list = []

    def go(command) -> bool:
        try:
            result = _run(command, stage, env, timeout)
        except subprocess.TimeoutExpired:
            returncodes.append(None)
            outputs.append(f"timed out after {timeout}s")
            return False
        except OSError as exc:
            returncodes.append(None)
            outputs.append(str(exc))
            return False
        returncodes.append(result.returncode)
        outputs.append((result.stdout or "") + (result.stderr or ""))
        return result.returncode == 0

    ok = go(argv)
    if ok and found.kind == "raw":
        biber = _which("biber")
        bibtex = _which("bibtex")
        if (stage / f"{Path(main).stem}.bcf").exists() and biber:
            ok = go([biber, Path(main).stem])
        elif _engine._needs_bibliography(stage / f"{Path(main).stem}.aux") and bibtex:
            ok = go([bibtex, Path(main).stem])
        for _ in range(2):
            if not ok:
                break
            ok = go(argv)
    return ok, [Path(argv[0]).name] + argv[1:], returncodes, outputs


def build_run(root, run_dir, *, engine: str | None = None, timeout: int = 600,
              main: str = "main.tex") -> BuildResult:
    run = Path(run_dir)
    folder = run / "manuscript"
    if folder.is_symlink() or not folder.is_dir():
        raise BuildError("manuscript-missing", f"no manuscript folder in {run.name}")
    _refuse_links(folder)
    if _tree_bytes(folder) > TREE_MAX_BYTES:
        raise BuildError("tree-size", "the manuscript tree is larger than 200 MB")
    try:
        tree = tex.read_tree(folder, main)
        analysis = tex.analyse(tree)
    except tex.TexError as exc:
        raise BuildError(exc.rule, exc.message) from exc
    if analysis.documentclass is None:
        raise BuildError("documentclass-missing", f"{main} has no \\documentclass")
    source_hash = tex.source_tree_hash(folder)

    stage = _stage(folder, run)
    env_root = run / "scratch" / "manuscript-env"
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8",
           "HOME": str(env_root / "home"), "TEXMFVAR": str(env_root / "texmf-var"),
           "shell_escape": "f", "openout_any": "p"}
    for key in ("HOME", "TEXMFVAR"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)

    notes: list = []
    cls = analysis.documentclass
    _check_asset(folder, f"{cls}.cls", stage, env, notes, _class_source(cls))
    if analysis.bibstyle:
        _check_asset(folder, f"{analysis.bibstyle}.bst", stage, env, notes,
                     "a bibliography style: put it in manuscript/ or install it")

    if engine == "tectonic" and (_network.replays(run) or _levels.run_level(run) == "L2"):
        raise BuildError("tectonic-refused", "tectonic downloads packages: refused in a replay, offline or L2 run")
    found = _resolve_engine(engine, env)

    ok, argv, returncodes, outputs = _compile(found, stage, env, timeout, main)
    pdf = stage / f"{Path(main).stem}.pdf"
    ok = ok and pdf.is_file()

    build_dir = folder / BUILD_DIR
    if build_dir.is_symlink() or (build_dir.exists() and not build_dir.is_dir()):
        raise BuildError("symlink", "manuscript/build is a symbolic link or not a folder; refused")
    build_dir.mkdir(exist_ok=True)
    log = stage / f"{Path(main).stem}.log"
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    tail_source = log_text or (outputs[-1] if outputs else "")
    log_tail = "\n".join(tail_source.splitlines()[-TAIL_LINES:])

    pdf_sha = None
    if ok:
        pdf_bytes = pdf.read_bytes()
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
        state.atomic_write(build_dir / f"{Path(main).stem}.pdf", pdf_bytes)
    else:
        stale = build_dir / f"{Path(main).stem}.pdf"
        if stale.is_file():
            stale.unlink()
    if log.is_file():
        state.atomic_write(build_dir / f"{Path(main).stem}.log", log.read_bytes())
    bbl = stage / f"{Path(main).stem}.bbl"
    if bbl.is_file():
        state.atomic_write(build_dir / f"{Path(main).stem}.bbl", bbl.read_bytes())

    result = BuildResult(ok=ok, engine=found.name, argv=argv, returncodes=returncodes,
                         pdf_sha256=pdf_sha, source_tree_sha256=source_hash, cls=cls,
                         bibtex_style=analysis.bibstyle, notes=notes, log_tail=log_tail)
    state.atomic_write(build_dir / BUILD_JSON,
                       (json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n").encode("utf-8"))
    events.emit(run, "note.manuscript_build", actor="agent", ok=ok, engine=found.name, pdf_sha256=pdf_sha)
    return result
