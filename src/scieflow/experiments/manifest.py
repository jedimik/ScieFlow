"""Run manifests and campaign verification (M-D2 plan 5.3).

Stdlib only. Every subprocess call has a fixed argv, no shell, a timeout and capped output.
Manifests never record a host name, a user name, an environment variable or an absolute path:
the pipeline is named, the input is hashed, the container is identified by its sha256.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import scieflow

from ..core.data import levels
from ..core.run import actions
from .stage import sif_path

MANIFEST_FILE = "manifest.json"
PACKAGES_DIR = "packages"
GIT_TIMEOUT = 10
PKG_TIMEOUT = 120
PKG_MAX_CHARS = 1 << 20
PKG_MAX_LINES = 20_000
CHUNK = 1 << 20
PKG_LINE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[^\s=]+$")
REQUIRED_KEYS = frozenset({
    "version", "run_id", "campaign", "config_id", "replicate", "seed", "params", "mode",
    "status", "returncode", "started", "finished", "wall_seconds", "code", "stage", "inputs",
    "environment", "packages", "data_level", "artifacts", "metrics_sha256", "metrics_history",
})


class ManifestError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def file_digest(path) -> dict:
    p = Path(path)
    h = hashlib.sha256()
    size = 0
    with p.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
            size += len(chunk)
    return {"kind": "file", "sha256": h.hexdigest(), "size": size}


def _entries(root) -> list[tuple[str, bool, Path]]:
    """Every regular file and symlink below root, sorted; symlinks are never followed."""
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in dirnames:  # a real directory is walked; a symlinked one is recorded only
            p = base / name
            if p.is_symlink():
                out.append((p.relative_to(root).as_posix(), True, p))
        for name in filenames:
            p = base / name
            out.append((p.relative_to(root).as_posix(), p.is_symlink(), p))
    return sorted(out, key=lambda e: e[0])


def tree_digest(path) -> dict:
    lines = []
    files = 0
    for rel, link, p in _entries(path):
        if link:
            lines.append(f"{rel}\0symlink\n")
        else:
            lines.append(f"{rel}\0{file_sha256(p)}\n")
            files += 1
    return {"kind": "dir", "sha256": sha256_bytes("".join(sorted(lines)).encode()), "files": files}


def digest(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"kind": "missing"}
    if p.is_dir():
        return tree_digest(p)
    return file_digest(p)


def artifact_list(root) -> list[dict]:
    out = []
    for rel, link, p in _entries(root):
        if link:
            out.append({"path": rel, "symlink": True})
        else:
            d = file_digest(p)
            out.append({"path": rel, "sha256": d["sha256"], "size": d["size"]})
    return out


def _git_env() -> dict:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _git(args, cwd):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          timeout=GIT_TIMEOUT, env=_git_env())


def git_state(path, probe) -> tuple[dict | None, str | None]:
    """(`{"sha", "dirty"}`, None) for a work tree that tracks `probe`, else (None, reason).

    `dirty` counts tracked changes under `path` only, so untracked files do not make it dirty.
    """
    cwd = Path(path)
    try:
        head = _git(["rev-parse", "HEAD"], cwd)
        if head.returncode != 0:
            return None, "not-a-worktree"
        tracked = _git(["ls-files", "--error-unmatch", "--", str(probe)], cwd)
        if tracked.returncode != 0:
            return None, "not-tracked"
        status = _git(["status", "--porcelain", "--untracked-files=no", "--", str(cwd)], cwd)
    except FileNotFoundError:
        return None, "no-git"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "no-git"
    if status.returncode != 0:
        return None, "status-failed"
    return {"sha": head.stdout.strip(), "dirty": bool(status.stdout.strip())}, None


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _not_captured(reason: str) -> dict:
    return {"status": "not-captured", "reason": reason}


def _captured(source: str, lines: list[str]) -> dict:
    text = "\n".join(lines) + "\n"
    return {"status": "captured", "source": source, "count": len(lines),
            "sha256": sha256_bytes(text.encode()), "list": lines}


def _freeze_lines(stdout: str) -> list[str]:
    lines = []
    for raw in stdout[:PKG_MAX_CHARS].splitlines():
        line = raw.strip()
        if PKG_LINE.match(line):
            lines.append(line)
        if len(lines) >= PKG_MAX_LINES:
            break
    return sorted(set(lines))


def packages(mode: str, sif) -> dict:
    """The installed package list, never an exception. Direct: this interpreter. Apptainer: the .sif."""
    if mode == "direct":
        lines = set()
        for dist in importlib.metadata.distributions():
            name = dist.metadata["Name"] if dist.metadata else None
            if name and dist.version:
                lines.add(f"{_norm(name)}=={dist.version}")
        return _captured("host-interpreter", sorted(lines))
    if mode == "apptainer":
        argv = ["apptainer", "exec", str(sif), "python", "-m", "pip", "list", "--format=freeze"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=PKG_TIMEOUT)
        except subprocess.TimeoutExpired:
            return _not_captured("timeout")
        except FileNotFoundError:
            return _not_captured("missing-binary")
        except OSError:
            return _not_captured("os-error")
        if proc.returncode != 0:
            return _not_captured(f"exit-{proc.returncode}")
        lines = _freeze_lines(proc.stdout or "")
        if not lines:
            return _not_captured("no-package-lines")
        return _captured("apptainer", lines)
    return _not_captured("unknown-mode")


def _package_block(mode: str, sif, sif_sha: str | None, campaign_dir: Path) -> dict:
    if mode == "apptainer" and sif_sha is None:
        return _not_captured("no-sif")
    pk = packages(mode, sif)
    if pk["status"] != "captured" or mode == "direct":
        return pk
    text = "\n".join(pk.pop("list")) + "\n"
    name = f"{sif_sha[:12]}.txt"
    folder = campaign_dir / PACKAGES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write(folder / name, text.encode())
    return {"status": "captured", "source": pk["source"], "count": pk["count"],
            "sha256": pk["sha256"], "file": f"{PACKAGES_DIR}/{name}"}


def _data_level(campaign_dir: Path) -> str | None:
    ws = actions.run_for_path(campaign_dir)
    return levels.run_level(ws) if ws is not None else None


def _environment(mode: str, sif, sif_sha: str | None) -> dict:
    env = {
        "mode": mode,
        "python": platform.python_version(),
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "sif": sif.name if sif is not None else None,
        "sif_sha256": sif_sha,
        "apptainer_version": None,
    }
    if mode == "apptainer":
        try:
            proc = subprocess.run(["apptainer", "--version"], capture_output=True, text=True,
                                  timeout=GIT_TIMEOUT)
            env["apptainer_version"] = proc.stdout.strip() if proc.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            env["apptainer_version"] = None
    return env


def capture_context(campaign, stage, mode: str, campaign_dir) -> dict:
    """Everything a run shares with its sweep: computed once per sweep, or once per run."""
    campaign_dir = Path(campaign_dir)
    pkg_dir = Path(scieflow.__file__).resolve().parent
    sc_state, sc_reason = git_state(pkg_dir, pkg_dir / "__init__.py")
    pipeline_dir = stage.dir.parent.parent
    pl_state, pl_reason = git_state(pipeline_dir, stage.dir / "stage.yaml")
    sif = sif_path(stage) if mode == "apptainer" else None
    sif_sha = file_sha256(sif) if sif is not None and sif.exists() else None
    return {
        "code": {
            "scieflow": {"version": scieflow.__version__, "git": sc_state, "reason": sc_reason},
            "pipeline": {"name": pipeline_dir.name, "git": pl_state, "reason": pl_reason},
        },
        "inputs": {
            "input": digest(campaign.input),
            "reference": digest(campaign.reference) if campaign.reference else None,
        },
        "stage": {
            "name": stage.name,
            "script_sha256": file_sha256(stage.dir / stage.script),
            "stage_yaml_sha256": file_sha256(stage.dir / "stage.yaml"),
        },
        "packages": _package_block(mode, sif, sif_sha, campaign_dir),
        "environment": _environment(mode, sif, sif_sha),
        "data_level": _data_level(campaign_dir),
    }


def _wall_seconds(started: str, finished: str) -> float:
    delta = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
    return round(delta.total_seconds(), 3)


def run_manifest(run_dir, *, campaign_name: str, spec, mode: str, status: str,
                 returncode, started: str, finished: str, context: dict) -> dict:
    run_dir = Path(run_dir)
    metrics_file = run_dir / "metrics.json"
    return {
        "version": 1,
        "run_id": spec.run_id,
        "campaign": campaign_name,
        "config_id": spec.config_id,
        "replicate": spec.replicate,
        "seed": spec.seed,
        "params": spec.params,
        "mode": mode,
        "status": status,
        "returncode": returncode,
        "started": started,
        "finished": finished,
        "wall_seconds": _wall_seconds(started, finished),
        "code": context["code"],
        "stage": context["stage"],
        "inputs": context["inputs"],
        "environment": context["environment"],
        "packages": context["packages"],
        "data_level": context["data_level"],
        "artifacts": artifact_list(run_dir / "artifacts"),
        "metrics_sha256": file_sha256(metrics_file) if metrics_file.is_file() else None,
        "metrics_history": [],
    }


def canonical_bytes(doc: dict) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=1) + "\n").encode()


def atomic_write(path, data: bytes) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_manifest(run_dir, doc: dict) -> str:
    """Write the run's manifest atomically; returns the sha256 of the bytes written."""
    data = canonical_bytes(doc)
    atomic_write(Path(run_dir) / MANIFEST_FILE, data)
    return sha256_bytes(data)


@dataclass
class Report:
    problems: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {"ok": self.ok, "problems": list(self.problems), "notes": list(self.notes)}


def _item(rule: str, run_id, path: str) -> dict:
    return {"rule": rule, "run_id": run_id, "path": path}


def _is_run_id(value) -> bool:
    """A run id is a relative path under runs/ whose components are all plain names.

    Run ids may contain '/' (campaign.py builds them from parameter values and scenario names), so
    containment is enforced per component: no empty, '.' or '..' part (which also rules out an
    absolute path), and no backslash or NUL.
    """
    if not isinstance(value, str) or "\\" in value or "\0" in value:
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _is_artifact_path(value) -> bool:
    """A manifest artifact path is a relative path that stays inside artifacts/."""
    if not isinstance(value, str) or value in ("", ".") or "\0" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def load_results(campaign_dir) -> dict:
    """Parse the campaign's results.json; ValueError when it is missing, unreadable or malformed.

    Fail-closed (plan 5.3): every run entry must be a dict whose run_id is one path component, so
    no later read or write can be steered outside the campaign folder.
    """
    path = Path(campaign_dir) / "results.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"results.json is not readable JSON ({type(e).__name__})") from e
    runs = doc.get("runs") if isinstance(doc, dict) else None
    if not isinstance(runs, list) or not all(
            isinstance(e, dict) and _is_run_id(e.get("run_id")) for e in runs):
        raise ValueError("results.json: runs must be a list of run entries, each with a run_id "
                         "that is one path component")
    return doc


def _verify_artifacts(run_dir: Path, rel: str, run_id: str, m: dict, report: Report) -> bool:
    art_root = run_dir / "artifacts"
    entries = m.get("artifacts") or []
    if not isinstance(entries, list) or not all(
            isinstance(a, dict) and _is_artifact_path(a.get("path")) for a in entries):
        report.problems.append(_item("manifest-invalid", run_id, f"{rel}/manifest.json"))
        return False
    declared = {a["path"]: a for a in entries}
    for name, a in declared.items():
        p = art_root / name
        where = f"{rel}/artifacts/{name}"
        if a.get("symlink"):
            if not p.is_symlink():
                rule = "artifact-hash" if os.path.lexists(p) else "artifact-missing"
                report.problems.append(_item(rule, run_id, where))
            continue
        if not os.path.lexists(p):
            report.problems.append(_item("artifact-missing", run_id, where))
        elif p.is_symlink() or not p.is_file():
            report.problems.append(_item("artifact-hash", run_id, where))
        elif file_sha256(p) != a.get("sha256"):
            report.problems.append(_item("artifact-hash", run_id, where))
    present = {rel_p for rel_p, _, _ in _entries(art_root)} if art_root.is_dir() else set()
    for name in sorted(present - set(declared)):
        report.problems.append(_item("artifact-extra", run_id, f"{rel}/artifacts/{name}"))
    return True


def verify_campaign(campaign_dir) -> Report:
    """Re-check every recorded hash of a campaign (plan 5.3). Never reads file contents into messages."""
    root = Path(campaign_dir)
    report = Report()
    try:
        doc = load_results(root)
    except ValueError:
        report.problems.append(_item("results-invalid", None, "results.json"))
        return report
    for entry in doc["runs"]:
        run_id = entry["run_id"]
        rel = f"runs/{run_id}"
        run_dir = root / "runs" / run_id
        recorded = entry.get("manifest_sha256")
        if recorded is None:
            if (run_dir / MANIFEST_FILE).exists():
                report.problems.append(_item("manifest-hash", run_id, f"{rel}/manifest.json"))
            else:
                report.notes.append(_item("legacy-run", run_id, rel))
            continue
        before = len(report.problems)
        mpath = run_dir / MANIFEST_FILE
        if not mpath.is_file():
            report.problems.append(_item("manifest-missing", run_id, f"{rel}/manifest.json"))
            continue
        raw = mpath.read_bytes()
        if sha256_bytes(raw) != recorded:
            report.problems.append(_item("manifest-hash", run_id, f"{rel}/manifest.json"))
        try:
            m = json.loads(raw)
        except ValueError:
            report.problems.append(_item("manifest-invalid", run_id, f"{rel}/manifest.json"))
            continue
        if not isinstance(m, dict) or not REQUIRED_KEYS <= set(m):
            report.problems.append(_item("manifest-invalid", run_id, f"{rel}/manifest.json"))
            continue
        _verify_artifacts(run_dir, rel, run_id, m, report)
        mfile = run_dir / "metrics.json"
        actual = file_sha256(mfile) if mfile.is_file() else None
        if actual != m.get("metrics_sha256"):
            report.problems.append(_item("metrics-hash", run_id, f"{rel}/metrics.json"))
        if len(report.problems) == before and m.get("metrics_history"):
            report.notes.append(_item("metrics-recomputed", run_id, f"{rel}/metrics.json"))
    return report
