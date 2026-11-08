"""T-9 t1 synthetic archive fixtures: temporary projects, pointers, zips, records and job records.

Nothing here touches the real home, the real workspace or a real remote. Zip bytes are synthetic.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import subprocess
from pathlib import Path

import yaml

from scieflow.core import archives as a
from scieflow.core import jobs
from scieflow.core.project import Project

MD5 = "0123456789abcdef0123456789abcdef"
OTHER_MD5 = "fedcba9876543210fedcba9876543210"


def make_project(tmp_path: Path) -> Project:
    (tmp_path / "workspace" / "_archives").mkdir(parents=True)
    return Project(tmp_path)


def pointer_doc(slug: str, *, md5: str = MD5, size: int | None = 6, path: str | None = None,
                hash_key: str | None = "md5") -> dict:
    out: dict = {"md5": md5, "path": path if path is not None else f"{slug}.zip"}
    if size is not None:
        out["size"] = size
    if hash_key is not None:
        out["hash"] = hash_key
    return {"outs": [out]}


def write_pointer(project: Project, slug: str, *, text: str | None = None, **kw) -> Path:
    path = project.workspace_root / "_archives" / f"{slug}.zip.dvc"
    path.write_text(text if text is not None else yaml.safe_dump(pointer_doc(slug, **kw)))
    return path


def write_legacy_pointer(project: Project, slug: str, *, md5: str = MD5 + ".dir", size: int = 6,
                         nfiles: int = 2) -> Path:
    path = project.workspace_root / f"{slug}.dvc"
    path.write_text(yaml.safe_dump({"outs": [{"md5": md5, "path": slug, "size": size,
                                              "nfiles": nfiles}]}))
    return path


def zip_path(project: Project, slug: str) -> Path:
    return project.workspace_root / "_archives" / f"{slug}.zip"


def write_zip(project: Project, slug: str, data: bytes = b"abcdef") -> Path:
    path = zip_path(project, slug)
    path.write_bytes(data)
    return path


def state_dir(project: Project) -> Path:
    return a.state_dir_for(project)


def jobs_dir(project: Project) -> Path:
    """Job records live beside the archive state: <project state_dir>/jobs (jobs.jobs_dir, no run)."""
    return Path(project.state_dir) / "jobs"


def record_path(project: Project, slug: str) -> Path:
    return state_dir(project) / f"{slug}.json"


def write_record(project: Project, slug: str, doc: dict | str) -> Path:
    path = record_path(project, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return path


def stat_record(zip_file: Path, *, pointer_md5: str = MD5, outcome: str = "ready",
                reason: str | None = None) -> dict:
    st = os.stat(zip_file)
    return {"pointer_md5": pointer_md5, "size": st.st_size, "st_dev": st.st_dev,
            "st_ino": st.st_ino, "st_mtime_ns": st.st_mtime_ns, "verified_at": "2026-11-07T00:00:00Z",
            "linked": False, "outcome": outcome, "reason": reason, "exit_code": 0, "pid": 1}


def dead_pid() -> int:
    """The pid of a child that has been reaped: no process has it now."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def new_job_id() -> str:
    from scieflow.core import store
    return store.new_id()


def write_job(project: Project, slug: str, *, kind: str = "archive-fetch", state: str = "running",
              pid: int | None = None, argv: list[str] | None = None, job_id: str | None = None,
              exit_code: int | None = None, pointer: bool = True) -> str:
    """A job record in state_dir/jobs and (by default) the slug's <slug>.job pointer."""
    job_id = job_id or new_job_id()
    directory = jobs_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    job = jobs.Job(id=job_id, kind=kind,
                   argv=argv if argv is not None else [sys.executable, "-B", "scripts/dvc_sync.py",
                                                       kind.split("-", 1)[1], slug],
                   cwd=str(project.root), label=slug, state=state,
                   pid=pid if pid is not None else os.getpid(), queued="2026-11-07T00:00:00+00:00",
                   started="2026-11-07T00:00:00+00:00",
                   finished="2026-11-07T00:00:01+00:00" if state in jobs.FINAL else None,
                   exit_code=exit_code, log=str(directory / f"{job_id}.log"),
                   err=str(directory / f"{job_id}.err"))
    (directory / f"{job_id}.json").write_text(json.dumps(dataclasses.asdict(job), indent=2))
    if pointer:
        write_job_pointer(project, slug, job_id)
    return job_id


def write_job_pointer(project: Project, slug: str, text: str) -> Path:
    path = state_dir(project) / f"{slug}.job"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def tree_snapshot(root: Path) -> dict:
    """Every path under root with its size and mtime: a GET must leave this unchanged."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames) + sorted(filenames):
            full = Path(dirpath) / name
            st = os.lstat(full)
            out[str(full)] = (st.st_size, st.st_mtime_ns)
    return out
