"""Synthetic fixtures for M-G t4 (provenance points and diffs, M-G 5.9). Every value here is invented.

Points live under revision/points/<label>/ of the conftest `synth` run. Packages come from M-F's t8 helpers
(make_build + package_run); no TeX runs here. Zip archives are built by hand so that unsafe names, symbolic
links and oversize entries can be written on purpose.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.manuscript import package as package_mod
from tests.research.manuscript.t8_support import make_build, project_of, submission

ZIP_DATE = (1980, 1, 1, 0, 0, 0)
MAIN_MARKER = "Gait timing in synthetic cohorts"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cli(*args):
    """Invoke `scieflow manuscript <args>` in-process; returns the CliRunner result."""
    return CliRunner().invoke(main, ["manuscript", *args], catch_exceptions=False)


def point_folder(synth, label: str) -> Path:
    return synth.run_dir / "revision" / "points" / label


def read_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, record: dict) -> None:
    Path(path).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_package(synth) -> Path:
    """Build the synthetic manuscript record and package it; returns report/submission/."""
    make_build(synth)
    package_mod.package_run(project_of(synth), synth.run_dir)
    return submission(synth)


def zip_bytes(entries) -> bytes:
    """A zip of (name, data[, unix_mode]) entries, written in the given order."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            name, data = entry[0], entry[1]
            mode = entry[2] if len(entry) > 2 else 0o644
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = mode << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def replace_source_zip(synth, data: bytes) -> None:
    """Swap in `data` as source.zip and pin its digest in package.json, so only the archive's contents
    (names, links, sizes) decide whether the point is accepted."""
    sub = submission(synth)
    (sub / "source.zip").write_bytes(data)
    record = read_json(sub / "package.json")
    record["files"]["source.zip"] = sha(data)
    write_json(sub / "package.json", record)
