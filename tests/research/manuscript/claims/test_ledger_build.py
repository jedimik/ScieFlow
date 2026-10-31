"""The claim ledger (M-G 5.3): build, show, the record shape, limits, determinism and the CLI. Synthetic only."""

import hashlib
import json

import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.manuscript import tex
from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.conftest import RUN

ID_1 = "c-"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ledger_dir(synth):
    return synth.run_dir / "report" / "claims"


def _claims_lines(synth):
    return (_ledger_dir(synth) / "claims.jsonl").read_text(encoding="utf-8").splitlines()


def test_build_writes_the_claim_lines_and_the_ledger_document(claims_ms, synth):
    result = ledger.build(synth.run_dir)
    assert result.count == 10
    lines = _claims_lines(synth)
    assert len(lines) == 10
    first = json.loads(lines[0])
    assert lines[0] == json.dumps(first, sort_keys=True, ensure_ascii=False)
    text = "Synthetic abstract reports 42 invented walkers."
    assert first == {
        "version": 1,
        "id": ID_1 + _sha("main.tex\n" + text)[:12],
        "file": "main.tex",
        "line": 6,
        "end_line": 6,
        "unit": "abstract",
        "section": "",
        "section_kinds": ["abstract"],
        "text": text,
        "text_sha256": _sha(text),
        "numbers": [{"raw": "42", "value": "42", "decimals": 0, "percent": False, "relation": "="}],
        "refs": [{"type": "data", "id": "tbl-metrics", "via": "comment"}],
        "cites": [],
        "pins": {},
    }
    document = json.loads((_ledger_dir(synth) / "ledger.json").read_text(encoding="utf-8"))
    assert document == {
        "version": 1,
        "parser_version": ledger.PARSER_VERSION,
        "main": "main.tex",
        "manuscript_tree_sha256": tex.source_tree_hash(claims_ms),
        "count": 10,
        "claims_sha256": _sha("\n".join(lines) + "\n"),
    }


def test_cite_without_doi_is_a_cite_and_cite_with_doi_is_a_doi_ref(claims_ms, synth):
    ledger.build(synth.run_dir)
    records = [json.loads(line) for line in _claims_lines(synth)]
    intro = next(r for r in records if r["line"] == 10)
    assert intro["cites"] == ["smith2001"] and intro["refs"] == []
    methods = next(r for r in records if r["file"] == "methods.tex")
    assert methods["cites"] == ["lee2010"]
    assert methods["refs"] == [{"type": "doi", "id": "10.5555/syn.2010.1", "key": "lee2010",
                                "via": "cite"}]


def test_rerun_on_an_unchanged_manuscript_is_byte_identical(claims_ms, synth):
    ledger.build(synth.run_dir)
    first = [(p.name, p.read_bytes()) for p in sorted(_ledger_dir(synth).iterdir())]
    ledger.build(synth.run_dir)
    second = [(p.name, p.read_bytes()) for p in sorted(_ledger_dir(synth).iterdir())]
    assert first == second


def test_more_than_5000_claims_are_refused_and_nothing_is_written(synth):
    body = "\n\n".join(f"Item {i} measured 3 times." for i in range(5001))
    synth.write_manuscript({"main.tex": "\\begin{document}\n" + body + "\n\\end{document}\n"})
    try:
        ledger.build(synth.run_dir)
    except ledger.ClaimsError as exc:
        assert exc.rule == "claims-limit"
    else:
        raise AssertionError("5001 claims must be refused")
    assert not (_ledger_dir(synth) / "claims.jsonl").exists()


def test_a_long_claim_text_is_stored_up_to_2000_characters(synth):
    long_sentence = "Long words " * 300 + "with 7 items."
    synth.write_manuscript({"main.tex": "\\begin{document}\n" + long_sentence + "\n\\end{document}\n"})
    ledger.build(synth.run_dir)
    record = json.loads(_claims_lines(synth)[0])
    assert len(record["text"]) == 2000
    assert record["text_truncated"] is True
    assert record["text_sha256"] == _sha(long_sentence.strip())


def test_show_lists_the_ledger_and_filters_by_id(claims_ms, synth):
    ledger.build(synth.run_dir)
    everything = ledger.read(synth.run_dir)
    assert len(everything) == 10
    one = ledger.read(synth.run_dir, claim_id=everything[3]["id"])
    assert [r["id"] for r in one] == [everything[3]["id"]]


def test_show_without_a_build_is_refused(synth):
    try:
        ledger.read(synth.run_dir)
    except ledger.ClaimsError as exc:
        assert exc.rule == "ledger-missing"
    else:
        raise AssertionError("show without a ledger must be refused")


def test_schema_pins_the_keys_the_writer_emits(claims_ms, synth):
    ledger.build(synth.run_dir)
    schema = yaml.safe_load((synth.root / "schemas" / "claims-ledger.yml").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in _claims_lines(synth)]
    optional = set(schema["optional_line_keys"])
    for record in records:
        assert set(schema["line_keys"]) <= set(record) <= set(schema["line_keys"]) | optional
        for number in record["numbers"]:
            assert set(number) == set(schema["number_keys"])
        for ref in record["refs"]:
            assert set(ref) <= set(schema["ref_keys"]) | set(schema["ref_optional_keys"])
            assert set(schema["ref_keys"]) <= set(ref)
    document = json.loads((_ledger_dir(synth) / "ledger.json").read_text(encoding="utf-8"))
    assert set(document) == set(schema["ledger_keys"])


def test_cli_build_and_show_json(claims_ms, synth):
    built = CliRunner().invoke(main, ["manuscript", "claims", "build", RUN], catch_exceptions=False)
    assert built.exit_code == 0, built.output
    assert "10 claims" in built.output
    shown = CliRunner().invoke(main, ["manuscript", "claims", "show", RUN, "--json"],
                               catch_exceptions=False)
    assert shown.exit_code == 0, shown.output
    assert len(json.loads(shown.output)) == 10


def test_cli_show_table_has_one_line_per_claim(claims_ms, synth):
    CliRunner().invoke(main, ["manuscript", "claims", "build", RUN], catch_exceptions=False)
    shown = CliRunner().invoke(main, ["manuscript", "claims", "show", RUN], catch_exceptions=False)
    assert shown.exit_code == 0, shown.output
    assert len(shown.output.strip().splitlines()) == 10


def test_cli_refused_limit_exits_one(synth):
    body = "\n\n".join(f"Item {i} measured 3 times." for i in range(5001))
    synth.write_manuscript({"main.tex": "\\begin{document}\n" + body + "\n\\end{document}\n"})
    result = CliRunner().invoke(main, ["manuscript", "claims", "build", RUN])
    assert result.exit_code == 1
    assert "claims-limit" in result.output
