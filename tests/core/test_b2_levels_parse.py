"""T-9 t2: data level from bytes (T9-D6): levels.parse_text and level_of agree with read and level_info."""

from __future__ import annotations

import pytest
import yaml

from scieflow.core.data import levels

TEXTS = [
    "version: 1\n",
    "version: 1\ndefault_level: L1\n",
    "version: 1\ndefault_level: L2\nlevels: []\n",
    "version: 1\nlevels:\n  - {path: inputs/x.csv, level: L2, kind: tabular, reason: t,"
    " declared_by: human, declared_at: '2026-10-26T10:00:00Z'}\n",
    "version: 1\nimports:\n  - {id: i1, file: f, sha256: abc}\n",
    "version: 1\ndefault_level: L9\n",
    "version: 2\n",
    "not: [valid\n",
    "",
    "- a list\n",
    "version: 1\nunknown_key: 1\n",
    "version: 2026-02-30\n",
]


@pytest.mark.parametrize("text", TEXTS)
def test_parse_text_matches_read_on_the_same_bytes(tmp_path, text):
    ws = tmp_path / "r1"
    ws.mkdir()
    (ws / "data.yml").write_text(text)
    assert levels.parse_text(text) == levels.read(ws)


@pytest.mark.parametrize("text", TEXTS)
def test_level_of_matches_level_info(tmp_path, text):
    ws = tmp_path / "r1"
    ws.mkdir()
    (ws / "data.yml").write_text(text)
    assert levels.level_of(levels.parse_text(text)) == levels.level_info(ws)


def test_absent_declaration_is_l0_by_level_of(tmp_path):
    ws = tmp_path / "r1"
    ws.mkdir()
    assert levels.level_of(levels.read(ws)) == levels.LevelInfo("L0")


def test_invalid_declaration_is_l2_with_its_reason():
    got = levels.level_of(levels.parse_text("version: 2\n"))
    assert got == levels.LevelInfo("L2", "data.yml: version must be 1")


def test_import_makes_the_level_at_least_l15():
    got = levels.level_of(levels.parse_text(
        "version: 1\nimports:\n  - {id: i1, file: f, sha256: abc}\n"))
    assert got.level == "L1.5"


def test_parse_text_reads_no_file(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("parse_text must not touch the filesystem")
    monkeypatch.setattr(levels, "read", boom)
    assert levels.parse_text(yaml.safe_dump({"version": 1})).default_level == "L0"
