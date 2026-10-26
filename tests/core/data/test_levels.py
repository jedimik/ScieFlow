"""Run data levels (M-B Part A 5.1): data.yml is read fail-closed (M-B-D5)."""

import pytest
import yaml

from scieflow.core.data import levels


def write_data(ws, data):
    (ws / "data.yml").write_text(yaml.safe_dump(data, sort_keys=False))


def base(**extra):
    return {"version": 1, "default_level": "L0", "levels": [], "pending_releases": [],
            "released": [], "imports": [], **extra}


def entry(path="inputs/x.csv", level="L2", **extra):
    return {"path": path, "level": level, "kind": "tabular", "reason": "test",
            "declared_by": "human", "declared_at": "2026-10-26T10:00:00Z", **extra}


def test_a_run_without_data_yml_is_l0(ws):
    assert levels.run_level(ws) == "L0"
    assert levels.read(ws).present is False


def test_level_is_the_maximum_of_default_and_declarations(ws):
    write_data(ws, base(levels=[entry("a.csv", "L1"), entry("b.csv", "L2")]))
    assert levels.run_level(ws) == "L2"
    write_data(ws, base(default_level="L1", levels=[entry("a.csv", "L0")]))
    assert levels.run_level(ws) == "L1"


def test_a_glob_counts_even_when_it_matches_no_file_today(ws):
    write_data(ws, base(levels=[entry("nothing-yet/*.csv", "L2")]))
    assert levels.run_level(ws) == "L2"


def test_an_import_makes_the_run_at_least_l15(ws):
    write_data(ws, base(imports=[{"id": "i1", "file": "inputs/released/s/a.csv",
                                  "sha256": "0" * 64, "from": "src", "release": "r1",
                                  "imported_at": "2026-10-26T10:00:00Z"}]))
    assert levels.run_level(ws) == "L1.5"


def test_an_import_never_lowers_a_declared_l2(ws):
    write_data(ws, base(levels=[entry("a.csv", "L2")], imports=[{"id": "i1"}]))
    assert levels.run_level(ws) == "L2"


def test_a_valid_file_reads_back_its_declarations(ws):
    write_data(ws, base(levels=[entry("inputs/x.csv", "L2")]))
    got = levels.read(ws)
    assert got.present is True
    assert got.levels[0]["path"] == "inputs/x.csv"


@pytest.mark.parametrize("raw, key", [
    ("levels: [\n", "YAML"),                         # malformed YAML
    ("", "empty"),                                   # empty file is not a declaration
    ("- just\n- a list\n", "mapping"),               # wrong top-level type
    ("version: 2\nlevels: []\n", "version"),         # unknown version
    ("version: true\nlevels: []\n", "version"),      # a bool is not the integer 1
    ("levels: []\n", "version"),                     # version is required
    ("version: 1\nsurprise: 1\n", "surprise"),       # unknown top-level key
    ("version: 1\nlevels: nope\n", "levels"),        # wrong type
    ("version: 1\ndefault_level: L9\n", "default_level"),
])
def test_malformed_files_are_invalid_and_level_l2(ws, raw, key):
    (ws / "data.yml").write_text(raw)
    got = levels.read(ws)
    assert isinstance(got, levels.Invalid)
    assert key in got.reason
    assert levels.run_level(ws) == "L2"


@pytest.mark.parametrize("bad_entry, key", [
    (entry(level="L9"), "level"),
    (entry(path="../escape.csv"), "path"),
    (entry(path="/etc/passwd"), "path"),
    (entry(path=""), "path"),
    (entry(kind="dicom-ish"), "kind"),
    (entry(declared_by="robot"), "declared_by"),
    ({k: v for k, v in entry().items() if k != "reason"}, "reason"),
])
def test_a_bad_declaration_makes_the_whole_run_l2(ws, bad_entry, key):
    write_data(ws, base(levels=[entry("ok.csv", "L0"), bad_entry]))
    got = levels.read(ws)
    assert isinstance(got, levels.Invalid)
    assert key in got.reason
    assert levels.run_level(ws) == "L2"


def test_invalid_reason_names_the_key_never_a_value(ws):
    (ws / "data.yml").write_text(yaml.safe_dump(
        base(levels=[entry(path="../SECRET-PLANT-VALUE.csv")])))
    got = levels.read(ws)
    assert isinstance(got, levels.Invalid)
    assert "path" in got.reason
    assert "SECRET-PLANT-VALUE" not in got.reason
