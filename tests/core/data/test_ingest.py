"""M-B t5 ingest (Part A 5.9): tabular de-identification with a private pseudonym key."""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.data import ingest, levels
from scieflow.core.data.cli import data
from scieflow.core.project import Project

SENTINEL = "ZZ-sentinel-77be"
ROWS = 25
HEADER = ("record_id,redcap_event_name,rc,name,email,visit_date,datum_vysetreni,anchor_date,"
          "redcap_survey_identifier,score,note")


def source_text(sentinel=False):
    lines = [HEADER]
    for i in range(1, ROWS + 1):
        name = f"Jan {SENTINEL}" if sentinel else "Jan Fiktivni"
        lines.append(f"{i},baseline,999999/9999,{name},fake{i}@example.com,2020-03-05,5. 3. 2020,"
                     f"2020-03-01,SURV{i:03d},{10 + i},Pacient byl vysetren a sdelil nekolik "
                     f"poznamek o spanku a snech dlouhe radek {i}")
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def gate_schemas(project):
    (project / "schemas").mkdir()
    REPO = Path(__file__).resolve().parents[3]
    for name in ("gates", "status"):
        (project / "schemas" / f"{name}.yml").write_text((REPO / "schemas" / f"{name}.yml").read_text())


@pytest.fixture
def proj(project):
    return Project(project)


@pytest.fixture
def salts(tmp_path, monkeypatch):
    directory = tmp_path / "salts"
    monkeypatch.setattr(ingest, "SALTS_OVERRIDE", directory)
    return directory


@pytest.fixture
def src(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(source_text(), encoding="utf-8")
    return path


def read_out(ws, rel="inputs/clean.csv"):
    return list(csv.DictReader(io.StringIO((ws / rel).read_text(encoding="utf-8"))))


def header_of(ws, rel="inputs/clean.csv"):
    return (ws / rel).read_text(encoding="utf-8").splitlines()[0].split(",")


def manifest(ws, rel="inputs/clean.csv"):
    return yaml.safe_load((ws / (rel + ".ingest.yml")).read_text())


def test_a_failed_declaration_leaves_no_output(proj, ws, src, salts, monkeypatch):  # F2
    def refuse(ws_, change):
        raise levels.DataFileError("data.yml: forced by the test")

    monkeypatch.setattr(levels, "modify", refuse)
    with pytest.raises(ingest.IngestError):
        ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date")
    assert not (ws / "inputs" / "clean.csv").exists()
    assert not (ws / "inputs" / "clean.csv.ingest.yml").exists()


def test_a_failure_after_the_output_is_written_removes_it(proj, ws, src, salts, monkeypatch):  # F2
    def boom(path):
        raise OSError("forced by the test")

    monkeypatch.setattr(ingest, "_sha", boom)
    with pytest.raises(OSError):
        ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date")
    assert not (ws / "inputs" / "clean.csv").exists()
    assert not (ws / "inputs" / ".clean.csv.part").exists()
    # The declaration is kept on purpose: a run with a stale L2 declaration is stricter, never looser.
    assert levels.run_level(ws) == "L2"


@pytest.mark.parametrize("out", ["data.yml", "events.jsonl", "budget.yml", "charter.yml"])
def test_out_cannot_name_run_bookkeeping(proj, ws, src, salts, out):  # F2
    with pytest.raises(ingest.IngestError):
        ingest.ingest(proj, ws, src, out, date_anchor="anchor_date")
    assert not (ws / out).exists()


def test_the_out_data_yml_command_exits_1_and_writes_nothing(proj, ws, src, salts):  # F2
    r = CliRunner().invoke(data, ["ingest", "r1", str(src), "--out", "data.yml",
                                  "--date-anchor", "anchor_date"])
    assert r.exit_code == 1
    assert not (ws / "data.yml").exists()


def test_identifiers_and_rc_are_dropped_and_named_in_the_manifest(proj, ws, src, salts):
    ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date")
    assert not {"name", "email", "rc"} & set(header_of(ws))
    dropped = {d["name"]: d["rule"] for d in manifest(ws)["columns_dropped"]}
    assert dropped["name"] == "identifier-column" and dropped["rc"] == "identifier-column"


def test_free_text_is_dropped_unless_kept(proj, ws, src, salts):
    ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date")
    assert "note" not in header_of(ws)
    ingest.ingest(proj, ws, src, "inputs/kept.csv", date_anchor="anchor_date", keep_free_text=True)
    assert "note" in header_of(ws, "inputs/kept.csv")
    assert read_out(ws, "inputs/kept.csv")[0]["note"].endswith("radek 1")


def test_dates_become_days_since_the_anchor_in_iso_and_czech_form(proj, ws, src, salts):
    ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date")
    row = read_out(ws)[0]
    assert row["visit_date"] == "4" and row["datum_vysetreni"] == "4"
    assert "anchor_date" not in header_of(ws)
    assert set(manifest(ws)["dates_converted"]) >= {"visit_date", "datum_vysetreni"}


def test_a_date_column_without_an_anchor_is_refused_naming_the_columns(proj, ws, src, salts):
    with pytest.raises(ingest.IngestError) as exc:
        ingest.ingest(proj, ws, src, "inputs/clean.csv")
    assert "visit_date" in str(exc.value) and "datum_vysetreni" in str(exc.value)
    assert not (ws / "inputs" / "clean.csv").exists()


def test_pseudonyms_are_stable_for_one_salt_and_differ_across_salts(proj, ws, src, salts):
    ingest.ingest(proj, ws, src, "inputs/a.csv", date_anchor="anchor_date")
    ingest.ingest(proj, ws, src, "inputs/b.csv", date_anchor="anchor_date")
    a, b = read_out(ws, "inputs/a.csv")[0], read_out(ws, "inputs/b.csv")[0]
    assert a["record_id"] == b["record_id"]
    assert len(a["record_id"]) == 16 and int(a["record_id"], 16) >= 0
    for path in salts.iterdir():
        path.unlink()
    ingest.ingest(proj, ws, src, "inputs/c.csv", date_anchor="anchor_date")
    assert read_out(ws, "inputs/c.csv")[0]["record_id"] != a["record_id"]


def test_the_salt_is_private_outside_the_run_and_never_printed(proj, ws, src, salts, capsys):
    ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date")
    [salt_file] = list(salts.iterdir())
    assert salt_file.stat().st_mode & 0o777 == 0o600
    salt = salt_file.read_bytes()
    assert len(salt) == 32
    for path in ws.rglob("*"):
        if path.is_file():
            assert salt not in path.read_bytes()
    printed = capsys.readouterr()
    assert salt.hex() not in printed.out + printed.err


def test_a_salt_that_is_not_private_is_refused(proj, ws, src, salts):
    ingest.ingest(proj, ws, src, "inputs/a.csv", date_anchor="anchor_date")
    [salt_file] = list(salts.iterdir())
    os.chmod(salt_file, 0o644)
    with pytest.raises(ingest.IngestError):
        ingest.ingest(proj, ws, src, "inputs/b.csv", date_anchor="anchor_date")


def test_the_manifest_holds_the_plan_fields_and_no_salt(proj, ws, src, salts):
    ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date")
    m = manifest(ws)
    for key in ("source_name", "source_sha256", "source_mtime", "scieflow_version", "rows_in",
                "rows_out", "columns_kept", "columns_dropped", "dates_converted", "id_column",
                "salt_id"):
        assert key in m, key
    assert m["rows_in"] == ROWS and m["rows_out"] == ROWS
    assert m["id_column"] == "record_id" and len(m["salt_id"]) == 8
    assert m["source_name"] == "export.csv"


def test_the_output_is_declared_l2_as_an_ingest_output(proj, ws, src, salts):
    ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date")
    [entry] = levels.read(ws).levels
    assert entry["path"] == "inputs/clean.csv" and entry["level"] == "L2"
    assert entry["reason"] == "ingest output" and entry["declared_by"] == "human"
    assert levels.run_level(ws) == "L2"


def test_ingest_emits_counts_only_and_no_value_reaches_any_output(proj, ws, tmp_path, salts, capsys):
    path = tmp_path / "export.csv"
    path.write_text(source_text(sentinel=True), encoding="utf-8")
    ingest.ingest(proj, ws, path, "inputs/clean.csv", date_anchor="anchor_date")
    event = events.read(ws, types=("data.ingested",))[-1]
    assert event["data"]["rows_in"] == ROWS
    blob = " ".join(p.read_bytes().decode("utf-8", "replace") for p in ws.rglob("*") if p.is_file())
    assert SENTINEL not in blob
    printed = capsys.readouterr()
    assert SENTINEL not in printed.out + printed.err


def test_the_redcap_profile_drops_the_survey_identifier(proj, ws, src, salts):
    ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date", profile="redcap")
    assert "redcap_survey_identifier" not in header_of(ws)
    assert "score" in header_of(ws)


def test_a_missing_id_column_is_refused_by_name(proj, ws, src, salts):
    with pytest.raises(ingest.IngestError) as exc:
        ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date",
                      id_col="patient_code")
    assert "patient_code" in str(exc.value)


def test_the_output_must_be_a_new_file_inside_the_run(proj, ws, src, salts):
    for bad in ("../escape.csv", "released/x.csv"):
        with pytest.raises(ingest.IngestError):
            ingest.ingest(proj, ws, src, bad, date_anchor="anchor_date")
    (ws / "inputs").mkdir()
    (ws / "inputs" / "clean.csv").write_text("x")
    with pytest.raises(ingest.IngestError):
        ingest.ingest(proj, ws, src, "inputs/clean.csv", date_anchor="anchor_date")


def test_the_imaging_and_eeg_profiles_are_usage_errors_pointing_to_the_docs(project, ws, src, salts):
    for profile in ("imaging", "eeg"):
        r = CliRunner().invoke(data, ["ingest", "r1", str(src), "--out", "inputs/x.csv",
                                      "--profile", profile])
        assert r.exit_code == 2, r.output
        assert "docs/data-protection.md" in r.output


def test_ingest_inside_a_dispatch_is_refused(project, ws, src, salts, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    r = CliRunner().invoke(data, ["ingest", "r1", str(src), "--out", "inputs/x.csv",
                                  "--date-anchor", "anchor_date"])
    assert r.exit_code == 1
    assert not (ws / "inputs" / "x.csv").exists()
