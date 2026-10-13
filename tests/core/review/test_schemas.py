"""Review contract schemas (ticket 04): manuscript-review extension and the new
evidence-bundle, review-matrix, review-decisions and scite-prompts schemas.

Every schema has at least one valid and one invalid fixture under
tests/fixtures/review/schemas/ (`<schema>.<case>.valid.json` /
`<schema>.<case>.invalid.json`); each fixture is checked through
`scieflow research validate --schema <name>`, the path agents use.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scieflow.core import stub_agent
from scieflow.research import validate as research_validate

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "src" / "scieflow" / "research" / "schemas"
FIXTURES = ROOT / "tests" / "fixtures" / "review" / "schemas"

NEW = ("evidence-bundle", "review-matrix", "review-decisions", "scite-prompts")
ALL = ("manuscript-review", *NEW)


def schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


def fixtures(name: str, kind: str) -> list[Path]:
    """`kind` is "valid" or "invalid"; `<schema>.<case>.<kind>.json`."""
    return sorted(p for p in FIXTURES.glob(f"{name}.*.json")
                  if p.name.split(".")[0] == name and p.name.split(".")[-2] == kind)


def check(path: Path, name: str, capsys) -> tuple[object, str]:
    code: object = 0
    try:
        research_validate.main([str(path), "--schema", name])
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr().out


@pytest.mark.parametrize("name", ALL)
def test_schema_is_itself_valid_and_versioned(name):
    s = schema(name)
    Draft202012Validator.check_schema(s)
    assert s["$id"] == f"https://scieflow.local/schemas/{name}.schema.json"
    assert s["properties"]["version"] == {"const": 1}


@pytest.mark.parametrize("name", NEW)
def test_new_schemas_require_version(name):
    assert "version" in schema(name)["required"]


@pytest.mark.parametrize("name", ALL)
def test_every_schema_has_valid_and_invalid_fixtures(name):
    assert fixtures(name, "valid"), f"no valid fixture for {name}"
    assert fixtures(name, "invalid"), f"no invalid fixture for {name}"


@pytest.mark.parametrize("path", [p for n in ALL for p in fixtures(n, "valid")],
                         ids=lambda p: p.name)
def test_valid_fixtures_pass_research_validate(path, capsys):
    name = path.name.split(".")[0]
    code, out = check(path, name, capsys)
    assert code == 0 and out.strip() == "OK", out


@pytest.mark.parametrize("path", [p for n in ALL for p in fixtures(n, "invalid")],
                         ids=lambda p: p.name)
def test_invalid_fixtures_fail_research_validate(path, capsys):
    name = path.name.split(".")[0]
    code, out = check(path, name, capsys)
    assert code == 1 and "INVALID" in out, out


def test_research_validate_cli_knows_every_new_name():
    proc = subprocess.run([sys.executable, "-m", "scieflow.research.validate", "--help"],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    for name in (*ALL, "review-assignment", "findings", "review", "gaps", "manifest"):
        assert name in proc.stdout, name


# -- manuscript-review: old files keep validating ------------------------------
def test_stub_agents_canned_manuscript_review_still_validates():
    errors = list(Draft202012Validator(schema("manuscript-review")).iter_errors(
        stub_agent.CANNED_JSON["manuscript-review"]))
    assert errors == []


def test_pass_defaults_to_blind_when_absent():
    s = schema("manuscript-review")
    assert s["properties"]["pass"]["default"] == "blind"
    assert "pass" not in s["required"]


def test_legacy_review_with_blind_only_fields_is_still_blind():
    """A file without `pass` is a blind review: evidence fields are refused."""
    legacy = json.loads((FIXTURES / "manuscript-review.legacy.valid.json").read_text())
    legacy["scite_status"] = "ok"
    assert list(Draft202012Validator(schema("manuscript-review")).iter_errors(legacy))


@pytest.mark.parametrize("field,value", [
    ("followup_requests", [{"query": "x", "reason": "y"}]),
    ("scite_status", "ok"),
])
def test_blind_pass_refuses_evidence_only_top_level_fields(field, value):
    blind = json.loads((FIXTURES / "manuscript-review.blind.valid.json").read_text())
    blind[field] = value
    assert list(Draft202012Validator(schema("manuscript-review")).iter_errors(blind))


def test_evidence_pass_comment_needs_changed_from_blind():
    ev = json.loads((FIXTURES / "manuscript-review.evidence.valid.json").read_text())
    del ev["major"][0]["changed_from_blind"]
    assert list(Draft202012Validator(schema("manuscript-review")).iter_errors(ev))


def test_followup_request_needs_a_query_or_dois():
    ev = json.loads((FIXTURES / "manuscript-review.evidence.valid.json").read_text())
    ev["followup_requests"] = [{"reason": "just because"}]
    assert list(Draft202012Validator(schema("manuscript-review")).iter_errors(ev))
    ev["followup_requests"] = [{"dois": ["10.1000/xyz123"], "reason": "who cites it"}]
    assert not list(Draft202012Validator(schema("manuscript-review")).iter_errors(ev))


@pytest.mark.parametrize("doi", ["../../config/scite.yml", "10.1000/../../etc/passwd",
                                 "10.1/x", "10.1000/has space"])
def test_evidence_doi_must_be_doi_shaped(doi):
    ev = json.loads((FIXTURES / "manuscript-review.evidence.valid.json").read_text())
    ev["major"][0]["evidence"][0]["doi"] = doi
    assert list(Draft202012Validator(schema("manuscript-review")).iter_errors(ev))


# -- evidence bundle -------------------------------------------------------------
@pytest.mark.parametrize("kind", ["unresolvable", "retracted"])
def test_bundle_retracted_and_unresolvable_flags_are_blockers(kind):
    b = json.loads((FIXTURES / "evidence-bundle.valid.json").read_text())
    b["flags"][0]["kind"] = kind
    b["flags"][0]["blocker"] = True
    assert not list(Draft202012Validator(schema("evidence-bundle")).iter_errors(b))
    b["flags"][0]["blocker"] = False
    assert list(Draft202012Validator(schema("evidence-bundle")).iter_errors(b))


def test_bundle_advisory_flags_never_block():
    b = json.loads((FIXTURES / "evidence-bundle.valid.json").read_text())
    b["flags"][1]["blocker"] = True          # `contested` is advisory
    assert list(Draft202012Validator(schema("evidence-bundle")).iter_errors(b))


def test_bundle_skip_reasons_include_budget():
    b = json.loads((FIXTURES / "evidence-bundle.valid.json").read_text())
    for reason in ("budget", "policy", "quota", "plan_only"):
        b["calls"]["skipped"][0]["reason"] = reason
        assert not list(Draft202012Validator(schema("evidence-bundle")).iter_errors(b)), reason


# -- matrix ----------------------------------------------------------------------
def test_matrix_row_has_every_contract_field():
    row = schema("review-matrix")["properties"]["clusters"]["items"]
    assert set(row["required"]) >= {
        "cluster", "members", "agreement", "evidence_for", "evidence_against",
        "blocker", "scite_status", "recommendation", "rationale"}
    assert set(row["properties"]["members"]["items"]["required"]) == {
        "reviewer", "author", "pass", "id"}


def test_matrix_cluster_ids_are_unique_is_left_to_the_builder_but_ids_are_safe():
    m = json.loads((FIXTURES / "review-matrix.valid.json").read_text())
    m["clusters"][0]["cluster"] = "K1/../../x"
    assert list(Draft202012Validator(schema("review-matrix")).iter_errors(m))


# -- decisions -------------------------------------------------------------------
def test_decision_values_are_accept_reject_defer():
    d = schema("review-decisions")
    entry = d["properties"]["decisions"]["additionalProperties"]
    assert entry["properties"]["decision"]["enum"] == ["accept", "reject", "defer"]


# -- scite prompts -------------------------------------------------------------
@pytest.mark.parametrize("template", [
    "{{doi_usage}}", "{{file:notes/x.md}}", "{{section:methods}}",
])
def test_instruction_only_wildcards_never_reach_a_query(template):
    p = json.loads((FIXTURES / "scite-prompts.valid.json").read_text())
    p["prompts"][0]["queries"][1]["template"] = template
    assert list(Draft202012Validator(schema("scite-prompts")).iter_errors(p))


@pytest.mark.parametrize("text", [
    "{{claim}}", "{{claim_dois}}", "{{doi}}", "{{novelty_claim}}", "{{topic}}",
    "{{cited_dois}}", "{{latest_cited_year}}", "{{seed_dois}}", "{{gap}}", "{{hypothesis}}",
    "single {braces} are plain text",
])
def test_query_wildcards_from_the_catalogue_are_accepted(text):
    p = json.loads((FIXTURES / "scite-prompts.valid.json").read_text())
    p["prompts"][0]["queries"][1]["template"] = text
    assert not list(Draft202012Validator(schema("scite-prompts")).iter_errors(p)), text


@pytest.mark.parametrize("text", [
    "{{ claim }}", "{{{claim}}", "{{file:/etc/passwd}}", "{{file:a/../../b}}",
    "{{section:}}", "{{CLAIM}}",
])
def test_malformed_or_escaping_wildcards_are_refused(text):
    p = json.loads((FIXTURES / "scite-prompts.valid.json").read_text())
    p["prompts"][0]["instruction"] = text
    assert list(Draft202012Validator(schema("scite-prompts")).iter_errors(p)), text


def test_tool_only_for_scite_mcp_queries():
    p = json.loads((FIXTURES / "scite-prompts.valid.json").read_text())
    p["prompts"][0]["queries"][1]["tool"] = "search_literature"     # scite_public
    assert list(Draft202012Validator(schema("scite-prompts")).iter_errors(p))


# -- review findings -------------------------------------------------------------
def _set(doc, path, value):
    *head, last = path
    for k in head:
        doc = doc[k]
    doc[last] = value


@pytest.mark.parametrize("name,path,value", [
    ("review-matrix", ("clusters", 0, "members", 0, "reviewer"), "codex-review\n"),
    ("review-matrix", ("clusters", 0, "members", 0, "id"), "M1\n"),
    ("review-matrix", ("clusters", 0, "cluster"), "K1\n"),
    ("review-matrix", ("clusters", 0, "evidence_for", 0, "doi"), "10.1038/s41586-020-2649-2\n"),
    ("evidence-bundle", ("references", 0, "doi"), "10.1038/s41586-020-2649-2\n"),
    ("evidence-bundle", ("claims", 0, "id"), "C1\n"),
    ("evidence-bundle", ("flags", 0, "id"), "F1\n"),
    ("evidence-bundle", ("manuscript_hash",), "a" * 64 + "\n"),
    ("scite-prompts", ("prompts", 0, "id"), "novelty\n"),
    ("manuscript-review", ("major", 0, "evidence", 0, "doi"), "10.1038/s41586-020-2649-2\n"),
    ("manuscript-review", ("major", 0, "id"), "M1\n"),
])
def test_anchored_patterns_refuse_a_trailing_newline(name, path, value):
    """Python's `$` matches before a final newline; ids and names end up in
    gate options, prompts and paths, so a trailing line break must fail."""
    fixture = "manuscript-review.evidence" if name == "manuscript-review" else name
    doc = json.loads((FIXTURES / f"{fixture}.valid.json").read_text())
    _set(doc, path, value)
    assert list(Draft202012Validator(schema(name)).iter_errors(doc)), path


def test_decision_keys_refuse_a_trailing_newline():
    d = json.loads((FIXTURES / "review-decisions.valid.json").read_text())
    d["decisions"]["K1\n"] = d["decisions"].pop("K1")
    assert list(Draft202012Validator(schema("review-decisions")).iter_errors(d))


def test_assignment_names_refuse_a_trailing_newline():
    s = json.loads((SCHEMAS / "review-assignment.schema.json").read_text())
    name = {"$defs": s["$defs"], "$ref": "#/$defs/name"}
    assert not list(Draft202012Validator(name).iter_errors("codex-review"))
    assert list(Draft202012Validator(name).iter_errors("codex-review\n"))


def test_unresolvable_reference_without_a_doi_shaped_value_still_blocks():
    """A bib entry whose DOI is mistyped (not DOI-shaped) is exactly the
    unresolvable case that must block merge: it is recorded by bib key."""
    b = json.loads((FIXTURES / "evidence-bundle.valid.json").read_text())
    b["references"].append({"key": "typo2018", "status": "unresolvable", "sources": ["crossref"]})
    b["flags"].append({"id": "F3", "kind": "unresolvable", "key": "typo2018", "blocker": True,
                       "detail": "bib DOI field is not a DOI"})
    assert not list(Draft202012Validator(schema("evidence-bundle")).iter_errors(b))
    del b["flags"][-1]["key"]                       # a blocker must name its reference
    assert list(Draft202012Validator(schema("evidence-bundle")).iter_errors(b))
    del b["references"][-1]["key"]                  # a reference needs a doi or a key
    assert list(Draft202012Validator(schema("evidence-bundle")).iter_errors(b))
