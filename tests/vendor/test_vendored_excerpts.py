"""Excerpt pins are byte-exact (M-C2 t1, plan 5.1 and check 1).

Every pin with a ``lines`` range (query.py, contributors.py, errors.py, provenance_ts.py) must
start with its recorded ScieFlow header, and the rest must be exactly the pinned upstream line
range. The M-C1 integrity test covers only query.py and contributors.py, so this file covers
every excerpt generically. Re-cutting the lines from the read-only researcher snapshot is
checked too, and skipped (with a reason) when that snapshot is absent. Offline.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "src" / "scieflow" / "_vendor" / "researcher_core"
SNAPSHOT = Path("/home/ubuntu/ScieFlow-worktrees/gap-eval/src/researcher")


def _pin() -> dict:
    return json.loads((PKG / "UPSTREAM.json").read_text(encoding="utf-8"))


def _excerpts() -> list[tuple[str, dict]]:
    return [(name, meta) for name, meta in _pin()["files"].items() if "lines" in meta]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_the_four_excerpt_pins_are_exactly_these() -> None:
    assert {name for name, _ in _excerpts()} == {
        "query.py",
        "contributors.py",
        "errors.py",
        "provenance_ts.py",
    }


@pytest.mark.parametrize("name, meta", _excerpts(), ids=lambda v: v if isinstance(v, str) else "")
def test_header_then_pinned_body_hashes_to_the_excerpt_sha256(name: str, meta: dict) -> None:
    data = (PKG / name).read_bytes()
    assert _sha(data) == meta["sha256"], name
    text = data.decode("utf-8")
    assert text.startswith(meta["header"]), name
    assert _sha(text[len(meta["header"]) :].encode("utf-8")) == meta["excerpt_sha256"], name


@pytest.mark.parametrize("name, meta", _excerpts(), ids=lambda v: v if isinstance(v, str) else "")
def test_recut_from_the_snapshot_gives_the_same_bytes(name: str, meta: dict) -> None:
    upstream = SNAPSHOT / meta["upstream"]
    if not upstream.exists():
        pytest.skip(f"read-only researcher snapshot not present at {upstream}")
    assert _sha(upstream.read_bytes()) == meta["upstream_sha256"], meta["upstream"]
    first, last = meta["lines"]
    parts = upstream.read_bytes().decode("utf-8").split("\n")
    cut = "".join(line + "\n" for line in parts[first - 1 : last])
    local = (PKG / name).read_bytes().decode("utf-8")
    assert local[len(meta["header"]) :] == cut, name
