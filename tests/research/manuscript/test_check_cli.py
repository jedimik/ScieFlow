"""`scieflow manuscript check`: exit codes, the report files, determinism, the run note. Synthetic only."""

import json

from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events
from tests.research.manuscript.conftest import RUN, SLUG


def _run(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def _report_paths(synth):
    folder = synth.run_dir / "report"
    return folder / "manuscript-check.json", folder / "manuscript-check.md"


def test_manuscript_help_lists_check():
    result = _run("manuscript", "--help")
    assert result.exit_code == 0
    assert "check" in result.output


def test_clean_check_exits_zero_and_writes_both_reports(synth):
    result = _run("manuscript", "check", RUN)
    assert result.exit_code == 0, result.output
    json_path, md_path = _report_paths(synth)
    assert json_path.is_file() and md_path.is_file()
    document = json.loads(json_path.read_text(encoding="utf-8"))
    assert set(document) >= {"version", "journal", "profile_sha256", "article_type",
                             "manuscript_sha256", "counts", "findings", "guidelines"}
    assert document["journal"] == SLUG and document["article_type"] == "research-article"
    assert document["counts"]["words_main"] == 15


def test_json_flag_prints_the_document(synth):
    result = _run("manuscript", "check", RUN, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["journal"] == SLUG


def test_reports_are_byte_identical_on_rerun(synth):
    _run("manuscript", "check", RUN)
    json_path, md_path = _report_paths(synth)
    first = (json_path.read_bytes(), md_path.read_bytes())
    _run("manuscript", "check", RUN)
    assert (json_path.read_bytes(), md_path.read_bytes()) == first


def test_no_write_writes_nothing(synth):
    result = _run("manuscript", "check", RUN, "--no-write")
    assert result.exit_code == 0, result.output
    assert not (synth.run_dir / "report").exists()
    assert events.read(synth.run_dir, types=("note.manuscript_check",)) == []


def test_an_error_finding_exits_one_and_still_writes_the_report(synth):
    synth.set_abstract(" ".join(["word"] * 251))
    result = _run("manuscript", "check", RUN)
    assert result.exit_code == 1
    json_path, _ = _report_paths(synth)
    rules = {f["rule"] for f in json.loads(json_path.read_text(encoding="utf-8"))["findings"]}
    assert "abstract-words" in rules


def test_a_run_note_is_recorded(synth):
    _run("manuscript", "check", RUN)
    notes = events.read(synth.run_dir, types=("note.manuscript_check",))
    assert len(notes) == 1
    data = notes[0]["data"]
    assert data["journal"] == SLUG and data["errors"] == 0
    assert len(data["manuscript_sha256"]) == 64


def test_no_author_email_reaches_the_output(synth):
    import yaml
    data = yaml.safe_load((synth.ms / "metadata.yml").read_text(encoding="utf-8"))
    data["authors"][0]["orcid"] = "0000-0002-1825-0098"
    (synth.ms / "metadata.yml").write_text(yaml.safe_dump(data), encoding="utf-8")
    result = _run("manuscript", "check", RUN, "--json")
    assert result.exit_code == 1
    assert "ada.synthetic@example.test" not in result.output
    json_path, md_path = _report_paths(synth)
    assert "ada.synthetic" not in json_path.read_text(encoding="utf-8")
    assert "ada.synthetic" not in md_path.read_text(encoding="utf-8")


def test_unknown_run_exits_one(synth):
    assert _run("manuscript", "check", "no-such-run").exit_code == 1


def test_missing_run_argument_is_a_usage_error(synth):
    assert CliRunner().invoke(main, ["manuscript", "check"]).exit_code == 2
