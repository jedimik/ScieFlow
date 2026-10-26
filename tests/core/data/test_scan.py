"""M-B t4, Part A 5.6: the scan rules, bounds and the never-a-value rule.

Every value here is synthetic and obviously fake. No test reads real patient data.
"""

import dataclasses

import pytest

from scieflow.core.data import policy, scan

PLANT = "PLANTED-VALUE-9c2e51"


def _write(ws, rel, data):
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def _pol(project, **changes):
    return dataclasses.replace(policy.load(project), **changes)


def _scan(ws, project, **changes):
    return scan.scan(ws, _pol(project, **changes))


def _rules(result):
    return sorted((f.path, f.rule) for f in result.findings)


def test_clean_aggregate_csv_has_no_findings(ws, project):
    _write(ws, "outputs/summary.csv", "site,n,mean_age\nA,12,61.5\nB,9,58.0\n")
    result = _scan(ws, project)
    assert result.findings == ()
    assert result.files_seen == 1
    assert result.truncated is False


def test_redcap_export_needs_record_id_and_a_redcap_marker(ws, project):
    _write(ws, "inputs/a.csv", "record_id,redcap_event_name,score\n1,baseline,4\n")
    _write(ws, "inputs/b.csv", "record_id,pd_complete,score\n1,2,4\n")
    _write(ws, "inputs/c.csv", "record_id,score\n1,4\n")
    result = _scan(ws, project)
    assert _rules(result) == [("inputs/a.csv", "redcap-export"),
                              ("inputs/b.csv", "redcap-export")]


@pytest.mark.parametrize("header", ["jmeno,score", "Příjmení,score", "rodne_cislo,score",
                                    "E-mail,score", "datum narození,score"])
def test_identifier_column_matches_english_and_czech_names(ws, project, header):
    _write(ws, "inputs/people.csv", header + "\nx,4\n")
    result = _scan(ws, project)
    assert ("inputs/people.csv", "identifier-column") in _rules(result)


def test_identifier_column_detail_names_the_column(ws, project):
    _write(ws, "inputs/people.csv", "score,Příjmení\n4,Synthetic\n")
    (finding,) = [f for f in _scan(ws, project).findings if f.rule == "identifier-column"]
    assert finding.detail == "Příjmení"


def test_extra_drop_from_policy_is_an_identifier_column(ws, project):
    _write(ws, "inputs/x.csv", "clinic_code,score\nC1,4\n")
    assert _scan(ws, project).findings == ()
    (project / "config").mkdir(exist_ok=True)
    (project / "config" / "data-policy.yml").write_text(
        "version: 1\ningest:\n  extra_drop: [clinic_code]\n")
    result = scan.scan(ws, policy.load(project))
    assert ("inputs/x.csv", "identifier-column") in _rules(result)


@pytest.mark.parametrize("value", ["900101/1234", "fake.person@example.test",
                                   "+420 601 234 567"])
def test_identifier_values_birth_number_email_and_phone(ws, project, value):
    _write(ws, "inputs/contact.csv", f"contact,score\n{value},4\n")
    result = _scan(ws, project)
    assert ("inputs/contact.csv", "identifier-values") in _rules(result)


def test_identifier_values_detail_is_the_column_not_the_value(ws, project):
    _write(ws, "inputs/contact.csv", "contact,score\n900101/1234,4\n")
    (finding,) = [f for f in _scan(ws, project).findings if f.rule == "identifier-values"]
    assert finding.detail == "contact"


def test_free_text_column_long_values(ws, project):
    body = "Pacient byl vysetren a vysledky jsou v poradku dnes rano."
    rows = "".join(f"{i},{body}\n" for i in range(25))
    _write(ws, "inputs/notes.csv", "id_code,note\n" + rows)
    result = _scan(ws, project)
    assert ("inputs/notes.csv", "free-text-column") in _rules(result)


def test_free_text_column_needs_twenty_values(ws, project):
    body = "Pacient byl vysetren a vysledky jsou v poradku dnes rano."
    rows = "".join(f"{i},{body}\n" for i in range(5))
    _write(ws, "inputs/notes.csv", "id_code,note\n" + rows)
    assert ("inputs/notes.csv", "free-text-column") not in _rules(_scan(ws, project))


def test_dicom_header_by_signature_and_by_extension(ws, project):
    _write(ws, "inputs/scan.bin", b"\x00" * 128 + b"DICM" + b"\x00" * 16)
    _write(ws, "inputs/other.dcm", b"not a real dicom")
    result = _scan(ws, project)
    assert _rules(result) == [("inputs/other.dcm", "dicom-header"),
                              ("inputs/scan.bin", "dicom-header")]


@pytest.mark.parametrize("name", ["brain.nii", "brain.nii.gz", "vol.mgz", "vol.img", "vol.hdr"])
def test_imaging_file_by_extension(ws, project, name):
    _write(ws, f"inputs/{name}", b"0" * 8)
    assert ("inputs/" + name, "imaging-file") in _rules(_scan(ws, project))


def test_eeg_header_by_extension(ws, project):
    _write(ws, "inputs/rec.edf", b"0" * 8)
    assert ("inputs/rec.edf", "eeg-header") in _rules(_scan(ws, project))


def test_eeg_header_patient_field_not_blank_is_a_finding(ws, project):
    header = b"0".ljust(8) + b"FAKE-PATIENT".ljust(80) + b"\x00" * 168
    _write(ws, "inputs/unnamed.bin", header)
    assert ("inputs/unnamed.bin", "eeg-header") in _rules(_scan(ws, project))


@pytest.mark.parametrize("field", [b" " * 80, b"X X X X".ljust(80)])
def test_eeg_header_blank_or_unknown_patient_field_is_not_a_finding(ws, project, field):
    _write(ws, "inputs/unnamed.bin", b"0".ljust(8) + field + b"\x00" * 168)
    assert _scan(ws, project).findings == ()


def test_eeg_header_biosemi_signature(ws, project):
    _write(ws, "inputs/bio.bin", b"\xffBIOSEMI" + b"\x00" * 16)
    assert ("inputs/bio.bin", "eeg-header") in _rules(_scan(ws, project))


@pytest.mark.parametrize("name", ["table.xlsx", "table.xls", "model.sav", "x.parquet"])
def test_binary_tabular_cannot_be_inspected(ws, project, name):
    _write(ws, "inputs/" + name, b"PK\x03\x04")
    (finding,) = _scan(ws, project).findings
    assert (finding.path, finding.rule) == ("inputs/" + name, "binary-tabular")
    assert "cannot be inspected" in finding.detail


def test_unreadable_file_is_a_finding(ws, project, monkeypatch):
    _write(ws, "inputs/broken.csv", "a,b\n1,2\n")
    real = scan._read_bytes

    def flaky(path, limit):
        if path.name == "broken.csv":
            raise OSError("simulated read failure")
        return real(path, limit)

    monkeypatch.setattr(scan, "_read_bytes", flaky)
    assert ("inputs/broken.csv", "unreadable") in _rules(_scan(ws, project))


def test_bounded_by_max_files(ws, project):
    for i in range(3):
        _write(ws, f"outputs/f{i}.txt", "plain text\n")
    result = _scan(ws, project, scan_max_files=2)
    assert result.files_seen == 2
    assert result.truncated is True


def test_exactly_max_files_is_not_truncated(ws, project):
    for i in range(2):
        _write(ws, f"outputs/f{i}.txt", "plain text\n")
    result = _scan(ws, project, scan_max_files=2)
    assert result.files_seen == 2
    assert result.truncated is False


def test_bounded_by_max_seconds_with_an_injected_clock(ws, project):
    for i in range(3):
        _write(ws, f"outputs/f{i}.txt", "plain text\n")
    ticks = iter(range(0, 1000, 10))
    pol = _pol(project, scan_max_seconds=5)
    result = scan.scan(ws, pol, clock=lambda: next(ticks))
    assert result.truncated is True
    assert result.files_seen < 3


def test_skips_agent_home_cache_logs_gates_scite_and_events(ws, project):
    for rel in ("scratch/agent-home/a/notes.csv", "scratch/x/.uv-cache/c.csv",
                "logs/p.csv", "gates/g.csv", "scite/s.csv", "events.jsonl",
                "provenance.git/p.csv"):
        _write(ws, rel, "jmeno,score\nx,4\n")
    result = _scan(ws, project)
    assert result.files_seen == 0
    assert result.findings == ()


def test_findings_are_in_sorted_path_order(ws, project):
    _write(ws, "outputs/b.csv", "jmeno,score\nx,4\n")
    _write(ws, "outputs/a.csv", "jmeno,score\nx,4\n")
    paths = [f.path for f in _scan(ws, project).findings]
    assert paths == sorted(paths)


def test_no_value_from_a_data_file_appears_in_the_result(ws, project):
    _write(ws, "inputs/contact.csv", f"contact,jmeno\n{PLANT}@example.test,{PLANT}\n")
    result = _scan(ws, project)
    text = repr(result)
    assert PLANT not in text
    assert result.findings
