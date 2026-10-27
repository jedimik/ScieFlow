"""Integrity of the vendored researcher core (M-C1-D1, D15).

Offline and read-only. Every vendored file equals its pin in UPSTREAM.json; reversing the recorded
patches gives the upstream hash; query.py minus its ScieFlow header is the pinned excerpt; the
package holds no file that is neither pinned nor ScieFlow-authored; CREDITS.md and VENDORED.md
name the same pins. Test data is the vendored code itself; no network, no patient data.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "src" / "scieflow" / "_vendor" / "researcher_core"
AUTHORED = {"__init__.py", "_fuzz.py"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pin() -> dict:
    return json.loads((PKG / "UPSTREAM.json").read_text(encoding="utf-8"))


def test_pin_records_project_version_snapshot_and_licence() -> None:
    pin = _pin()
    assert pin["project"] == "researcher"
    assert pin["version"] == "1.0.0"
    assert pin["license"] == "MIT"
    assert pin["snapshot"].startswith("e4ddbf4")
    assert pin["url"] == "https://github.com/sokolmarek/researcher"


def test_every_pinned_file_has_the_recorded_sha256() -> None:
    for name, meta in _pin()["files"].items():
        assert _sha((PKG / name).read_bytes()) == meta["sha256"], name


def test_reversing_the_recorded_patches_gives_the_upstream_hash() -> None:
    for name, meta in _pin()["files"].items():
        if "lines" in meta:  # the excerpt is checked by its own test below
            continue
        text = (PKG / name).read_bytes().decode("utf-8")
        for patch in reversed(meta["patches"]):
            assert text.count(patch["new"]) == 1, f"{name}: patch text must occur exactly once"
            text = text.replace(patch["new"], patch["old"], 1)
        assert _sha(text.encode("utf-8")) == meta["upstream_sha256"], name


def test_query_excerpt_is_the_pinned_upstream_lines() -> None:
    meta = _pin()["files"]["query.py"]
    assert meta["lines"] == [86, 203]
    text = (PKG / "query.py").read_bytes().decode("utf-8")
    assert text.startswith(meta["header"])
    body = text[len(meta["header"]) :]
    assert _sha(body.encode("utf-8")) == meta["excerpt_sha256"]


def test_only_pinned_and_authored_files_sit_in_the_package() -> None:
    found = {
        p.relative_to(PKG).as_posix()
        for p in PKG.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    expected = set(_pin()["files"]) | {"UPSTREAM.json"} | AUTHORED
    assert found == expected


def test_authored_package_init_is_a_docstring_with_no_imports() -> None:
    source = (PKG / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    doc = ast.get_docstring(tree) or ""
    assert doc, "the package docstring names the vendoring"
    assert "VENDORED.md" in doc
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))


def test_credits_and_vendored_name_every_pin() -> None:
    credits = (REPO / "CREDITS.md").read_text(encoding="utf-8")
    vendored = (REPO / "VENDORED.md").read_text(encoding="utf-8")
    assert "researcher" in credits and "MIT" in credits and "VENDORED.md" in credits
    assert "1.0.0" in vendored and "e4ddbf4" in vendored
    assert "never edit a vendored file except through a recorded patch" in vendored
    for name, meta in _pin()["files"].items():
        assert meta["sha256"] in vendored, f"{name}: local sha256 missing from VENDORED.md"
        assert meta["upstream_sha256"] in vendored, f"{name}: upstream sha256 missing"
        if "excerpt_sha256" in meta:
            assert meta["excerpt_sha256"] in vendored, f"{name}: excerpt sha256 missing"
        for patch in meta["patches"]:
            assert patch["new"] in vendored, f"{name}: recorded patch missing from VENDORED.md"
