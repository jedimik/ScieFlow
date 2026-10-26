"""M-B t5 release (Part A 5.8): the checks, the data-release gate and its human answer hook.

All data is synthetic. SENTINEL is a planted marker that must never reach any output.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events, gates
from scieflow.core.data import levels, policy, release
from scieflow.core.data.cli import data
from scieflow.core.project import Project
from scieflow.core.run import status

REPO = Path(__file__).resolve().parents[3]
CLEAN = "group,n\nA,12\nB,7\n"
SENTINEL = "ZZ-sentinel-41c9"


@pytest.fixture(autouse=True)
def gate_schemas(project):
    (project / "schemas").mkdir()
    for name in ("gates", "status"):
        (project / "schemas" / f"{name}.yml").write_text((REPO / "schemas" / f"{name}.yml").read_text())


@pytest.fixture
def proj(project):
    return Project(project)


def write(ws, rel, data_):
    path = Path(ws) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data_, bytes):
        path.write_bytes(data_)
    else:
        path.write_text(data_)
    return path


def sha(path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(project, path, counts=(), no_counts=False):
    return release.check_file(Path(path), policy.load(project), counts=counts, no_counts=no_counts)


def checks(report) -> set:
    return {p.check for p in report.problems}


def pairs(report) -> set:
    return {(p.check, p.detail) for p in report.problems}


# -- gate kind -------------------------------------------------------------------
def test_the_data_release_kind_needs_a_human():
    table = yaml.safe_load((REPO / "schemas" / "gates.yml").read_text())["kinds"]
    assert table["data-release"]["requires_human"] is True
    assert table["data-release"]["help"].strip()


# -- checks (5.8 table) ------------------------------------------------------------
def test_a_clean_aggregate_passes(project, ws):
    report = check(project, write(ws, "outputs/agg.csv", CLEAN))
    assert report.problems == ()


def test_a_small_cell_is_refused(project, ws):
    assert checks(check(project, write(ws, "outputs/agg.csv", "group,n\nA,12\nB,3\n"))) == {"small-cell"}


def test_zero_and_large_cells_pass(project, ws):
    assert check(project, write(ws, "outputs/agg.csv", "n\n0\n12\n")).problems == ()


def test_a_count_column_must_be_recognised_or_named(project, ws):
    report = check(project, write(ws, "outputs/agg.csv", "group,total_score\nA,12\n"))
    assert checks(report) == {"counts-unknown"}


def test_no_counts_passes_without_a_count_column(project, ws):
    path = write(ws, "outputs/agg.csv", "group,total_score\nA,12\n")
    assert check(project, path, no_counts=True).problems == ()


def test_an_explicit_counts_column_is_checked(project, ws):
    path = write(ws, "outputs/agg.csv", "group,tally\nA,2\n")
    assert checks(check(project, path, counts=("tally",))) == {"small-cell"}


def test_a_named_counts_column_missing_from_the_header_is_refused_by_name(project, ws):
    report = check(project, write(ws, "outputs/agg.csv", CLEAN), counts=("nope",))
    assert checks(report) == {"counts-unknown"}
    assert "nope" in report.problems[0].detail


def test_a_record_level_column_is_refused(project, ws):
    report = check(project, write(ws, "outputs/agg.csv", "record_id,n\n1,12\n"), no_counts=True)
    assert "record-level" in checks(report)


def test_an_identifier_column_is_refused_by_its_normalised_term(project, ws):
    report = check(project, write(ws, "outputs/agg.csv", "Příjmení,n\nX,12\n"), no_counts=True)
    assert ("identifier-column", "prijmeni") in pairs(report)


def test_an_identifier_value_is_refused_by_column_index(project, ws):
    report = check(project, write(ws, "outputs/agg.csv", "group,n\n900101/1234,12\n"), no_counts=True)
    assert ("identifier-values", "column 1") in pairs(report)


def test_an_e_mail_value_is_refused(project, ws):
    report = check(project, write(ws, "outputs/agg.csv", "contact,n\nfake.person@example.com,12\n"),
                   no_counts=True)
    assert ("identifier-values", "column 1") in pairs(report)


def test_a_value_in_the_header_row_is_checked_too(project, ws):
    # A headerless file: its first record is read as the header, so its cells are still checked.
    report = check(project, write(ws, "outputs/agg.csv", "900101/1234,12\nA,13\n"), no_counts=True)
    assert ("identifier-values", "column 1") in pairs(report)


def test_free_text_is_refused(project, ws):
    rows = "note,n\n" + "".join(f'"{"a long sentence " * 3}",{i + 1}\n' for i in range(25))
    report = check(project, write(ws, "outputs/agg.csv", rows), no_counts=True)
    assert ("free-text-column", "column 1") in pairs(report)


def test_a_path_value_is_refused_and_the_report_holds_no_value(project, ws):
    path = write(ws, "outputs/agg.csv", f"source,n\n/srv/{SENTINEL}/x.csv,12\n")
    report = check(project, path, no_counts=True)
    assert checks(report) == {"path-values"}
    assert SENTINEL not in repr(report)


def test_a_path_value_in_json_is_refused(project, ws):
    path = write(ws, "outputs/agg.json", f'{{"where": "/srv/{SENTINEL}/x.json"}}')
    report = check(project, path)
    assert checks(report) == {"path-values"}
    assert SENTINEL not in repr(report)


def test_a_dicom_header_is_refused(project, ws):
    assert "dicom-header" in checks(check(project, write(ws, "scans/a.dcm", b"\x00" * 200)))


def test_an_eeg_file_is_refused(project, ws):
    assert "eeg-header" in checks(check(project, write(ws, "rec/r.edf", b"0" + b" " * 7 + b"x" * 200)))


def test_a_binary_tabular_file_is_refused(project, ws):
    assert "binary-tabular" in checks(check(project, write(ws, "outputs/t.xlsx", b"PK\x03\x04")))


def test_an_imaging_file_is_refused(project, ws):
    assert "imaging-file" in checks(check(project, write(ws, "scans/s.nii", b"\x00" * 64)))


def test_a_picture_passes_with_a_human_review_note(project, ws):
    report = check(project, write(ws, "figs/f.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64))
    assert report.problems == ()
    assert "not-inspectable" in {n.check for n in report.notes}


# -- the flow (5.8 items 3-4) --------------------------------------------------------
def test_a_release_opens_one_gate_and_one_pending_entry(proj, ws):
    write(ws, "outputs/agg.csv", CLEAN)
    g = release.request(proj, ws, "outputs/agg.csv", rid="rel-1", reason="counts")
    assert g["kind"] == "data-release" and g["requires_human"] is True
    assert g["options"] == ["release", "reject"]
    [pending] = levels.read(ws).pending_releases
    assert pending["id"] == "rel-1" and pending["gate"] == g["id"]
    assert pending["source"] == "outputs/agg.csv"
    assert pending["sha256"] == sha(ws / "outputs" / "agg.csv")


def test_a_refused_release_opens_no_gate_and_writes_nothing(proj, ws):
    write(ws, "outputs/agg.csv", "group,n\nA,3\n")
    with pytest.raises(release.ReleaseError) as exc:
        release.request(proj, ws, "outputs/agg.csv", rid="rel-1")
    assert exc.value.report.problems[0].check == "small-cell"
    assert gates.list_gates(ws) == []
    assert not (ws / "data.yml").exists()


def test_an_agent_cannot_answer_the_release_gate(proj, ws):
    write(ws, "outputs/agg.csv", CLEAN)
    g = release.request(proj, ws, "outputs/agg.csv", rid="rel-1")
    with pytest.raises(gates.GateError):
        gates.answer(proj, ws, g["id"], "release", actor="agent", rationale="x")
    assert gates.get(ws, g["id"])["state"] == "open"
    assert not (ws / "released").exists()


def test_the_cli_agent_answer_is_refused(proj, ws, project):
    write(ws, "outputs/agg.csv", CLEAN)
    g = release.request(proj, ws, "outputs/agg.csv", rid="rel-1")
    r = CliRunner().invoke(gates.gate, ["answer", "r1", g["id"], "release", "--as-agent",
                                        "--rationale", "x"])
    assert r.exit_code != 0
    assert gates.get(ws, g["id"])["state"] == "open"
    assert not (ws / "released").exists()


def test_a_human_release_copies_the_file_and_records_it(proj, ws):
    write(ws, "outputs/agg.csv", CLEAN)
    g = release.request(proj, ws, "outputs/agg.csv", rid="rel-1")
    gates.answer(proj, ws, g["id"], "release", actor="human")
    copied = ws / "released" / "rel-1" / "agg.csv"
    assert copied.read_bytes() == (ws / "outputs" / "agg.csv").read_bytes()
    df = levels.read(ws)
    assert df.pending_releases == []
    [rec] = df.released
    assert rec["id"] == "rel-1" and rec["file"] == "released/rel-1/agg.csv"
    assert rec["sha256"] == sha(copied) and rec["approved_by"] == "human" and rec["gate"] == g["id"]
    last = events.read(ws, types=("data.released",))[-1]
    assert last["actor"] == "human" and last["data"]["outcome"] == "released"


def test_a_source_changed_after_opening_refuses_the_answer_and_keeps_the_gate_open(proj, ws):
    write(ws, "outputs/agg.csv", CLEAN)
    g = release.request(proj, ws, "outputs/agg.csv", rid="rel-1")
    write(ws, "outputs/agg.csv", "group,n\nA,1\n")
    with pytest.raises(gates.GateError):
        gates.answer(proj, ws, g["id"], "release", actor="human")
    assert gates.get(ws, g["id"])["state"] == "open"
    assert not (ws / "released").exists()
    assert levels.read(ws).pending_releases


def test_reject_drops_the_pending_entry_and_records_the_rejection(proj, ws):
    write(ws, "outputs/agg.csv", CLEAN)
    g = release.request(proj, ws, "outputs/agg.csv", rid="rel-1")
    gates.answer(proj, ws, g["id"], "reject", actor="human")
    df = levels.read(ws)
    assert df.pending_releases == [] and df.released == []
    assert not (ws / "released").exists()
    assert events.read(ws, types=("data.released",))[-1]["data"]["outcome"] == "rejected"


def test_a_release_id_must_be_a_plain_name(proj, ws):
    write(ws, "outputs/agg.csv", CLEAN)
    with pytest.raises(release.ReleaseError):
        release.request(proj, ws, "outputs/agg.csv", rid="../escape")


def test_the_artifact_must_be_a_regular_file_inside_the_run(proj, ws, tmp_path):
    write(ws, "outputs/agg.csv", CLEAN)
    (ws / "outputs" / "link.csv").symlink_to(ws / "outputs" / "agg.csv")
    outside = tmp_path / "outside.csv"
    outside.write_text(CLEAN)
    for bad in ("outputs/link.csv", "../escape.csv", str(outside)):
        with pytest.raises(release.ReleaseError):
            release.request(proj, ws, bad, rid="rel-1")
    assert gates.list_gates(ws) == []


def test_a_file_under_released_is_not_a_source(proj, ws):
    write(ws, "released/rel-1/agg.csv", CLEAN)
    with pytest.raises(release.ReleaseError):
        release.request(proj, ws, "released/rel-1/agg.csv", rid="rel-2")


def test_the_release_command_exits_1_on_refusal_and_0_when_the_gate_opens(project, ws):
    write(ws, "outputs/agg.csv", "group,n\nA,3\n")
    refused = CliRunner().invoke(data, ["release", "r1", "outputs/agg.csv", "--reason", "x"])
    assert refused.exit_code == 1
    write(ws, "outputs/agg.csv", CLEAN)
    ok = CliRunner().invoke(data, ["release", "r1", "outputs/agg.csv", "--reason", "x"])
    assert ok.exit_code == 0, ok.output
    assert gates.list_gates(ws, "open")[0]["id"] in ok.output
