"""M-F t3 (plan 5.2, 5.4): the `scieflow journal research` and `add-source` commands: exit codes,
refusals before any request, and the human-saved page. Mocked HTTP only; synthetic data.
"""

import json
import os
import re
from pathlib import Path

import pytest
import responses
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import network

REPO = Path(__file__).resolve().parents[3]
SOURCES = re.compile(r"https://api\.openalex\.org/sources.*")
WORKS = re.compile(r"https://api\.openalex\.org/works.*")
CROSSREF = "https://api.crossref.org/journals/1234-5679"
HOME = "https://journal.example/"
GUIDE = "https://guides.journal.example/authors"
JOURNAL = "Journal of Synthetic Neurology"
ISSN = "1234-5679"


@pytest.fixture
def run(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "schemas").mkdir()
    (root / "schemas" / "gates.yml").write_text((REPO / "schemas" / "gates.yml").read_text())
    (root / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.chdir(root)
    return root, ws


def invoke(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def research(*extra, name=JOURNAL, issn=ISSN):
    args = ["journal", "research", "demo", "--journal", name, "--as-of", "2026-10-01", *extra]
    if issn:
        args += ["--issn", issn]
    return invoke(*args)


def source_row(name=JOURNAL, issn=ISSN, sid="S123456"):
    return {"id": f"https://openalex.org/{sid}", "display_name": name, "issn_l": issn,
            "issn": [issn] if issn else [], "host_organization_name": "Synthetic Press",
            "homepage_url": HOME, "is_oa": False, "is_in_doaj": False, "apc_usd": None,
            "works_count": 42, "type": "journal"}


def mock_one_source(rows=None):
    responses.add(responses.GET, SOURCES, json={"results": rows or [source_row()]})
    responses.add(responses.GET, WORKS, json={"results": []})
    responses.add(responses.GET, CROSSREF, json={"message": {
        "title": JOURNAL, "publisher": "Synthetic Press", "ISSN": [ISSN]}})


def test_journal_help_lists_research_and_add_source():
    text = invoke("journal", "--help").output
    for command in ("research", "add-source", "show", "list", "validate"):
        assert command in text


@responses.activate
def test_an_ambiguous_name_exits_1_lists_the_candidates_and_writes_no_facts(run):
    root, ws = run
    mock_one_source([source_row(issn="1234-5679", sid="S1"),
                     source_row(issn="9876-5432", sid="S2")])
    result = research(name=JOURNAL, issn=None)
    assert result.exit_code == 1, result.output
    assert "re-run with --issn" in result.output
    assert not (ws / "journal" / "facts.json").exists()


@responses.activate
def test_an_invalid_issn_exits_1_with_no_request(run):
    root, ws = run
    result = research(issn="1234-5678")
    assert result.exit_code == 1, result.output
    assert len(responses.calls) == 0


@responses.activate
def test_a_data_shaped_name_exits_3_with_no_request(run):
    root, ws = run
    result = research(name="patient 4711 age 63", issn=None)
    assert result.exit_code == 3, result.output
    assert len(responses.calls) == 0


@responses.activate
def test_an_http_url_exits_1_with_no_request(run):
    root, ws = run
    result = research("--url", "http://guides.journal.example/authors")
    assert result.exit_code == 1, result.output
    assert len(responses.calls) == 0


@responses.activate
def test_more_than_ten_urls_are_refused(run):
    root, ws = run
    urls = [f"https://guides.journal.example/p{i}" for i in range(11)]
    args = []
    for url in urls:
        args += ["--url", url]
    result = research(*args)
    assert result.exit_code == 1, result.output
    assert len(responses.calls) == 0


def test_a_missing_run_exits_1(run):
    result = CliRunner().invoke(main, ["journal", "research", "no-such-run", "--journal", JOURNAL],
                                catch_exceptions=False)
    assert result.exit_code == 1


def test_a_bad_as_of_date_is_a_usage_error(run):
    result = CliRunner().invoke(main, ["journal", "research", "demo", "--journal", JOURNAL,
                                       "--as-of", "01/10/2026"], catch_exceptions=False)
    assert result.exit_code == 2


@responses.activate
def test_a_replay_with_no_recorded_answer_exits_6_and_sends_nothing(run):
    root, ws = run
    network.write_mode(ws, "replay", actor="human", reason="t3 no-egress test")
    result = research()
    assert result.exit_code == 6, result.output
    assert len(responses.calls) == 0


@responses.activate
def test_json_output_names_the_phase_and_the_counts(run):
    root, ws = run
    (root / "config" / "integrations.yml").write_text(yaml.safe_dump({"providers": {
        "journal-guidelines": {"enabled": True, "rate_per_s": 1000}}}))
    mock_one_source()
    responses.add(responses.GET, HOME, body="<html><body>x</body></html>")
    result = research("--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["phase"] == "gate-open" and data["gate"]
    assert data["counts"] == {"ok": 0, "failed": 0, "too-large": 0}


# --- add-source ----------------------------------------------------------------------------------


def add(ws_name, file, url="https://guides.journal.example/authors", *more):
    return CliRunner().invoke(main, ["journal", "add-source", ws_name, "--file", str(file),
                                     "--url", url, *more], catch_exceptions=False)


def test_add_source_is_refused_inside_a_dispatch(run, monkeypatch):
    root, ws = run
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    page = root / "page.html"
    page.write_text("<html><body>x</body></html>")
    result = add("demo", page)
    assert result.exit_code == 3, result.output
    assert not (ws / "journal").exists()


def test_a_symbolic_link_is_refused(run):
    root, ws = run
    real = root / "real.html"
    real.write_text("<html><body>x</body></html>")
    link = root / "link.html"
    os.symlink(real, link)
    result = add("demo", link)
    assert result.exit_code == 1, result.output


def test_a_pdf_is_refused(run):
    root, ws = run
    pdf = root / "guide.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    assert add("demo", pdf).exit_code == 1


def test_a_file_over_five_megabytes_is_refused(run):
    root, ws = run
    big = root / "big.html"
    big.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    assert add("demo", big).exit_code == 1


def test_an_http_url_is_refused(run):
    root, ws = run
    page = root / "page.html"
    page.write_text("<html><body>x</body></html>")
    assert add("demo", page, "http://guides.journal.example/authors").exit_code == 1


def test_a_valid_saved_page_becomes_a_human_file_row_with_its_text(run):
    root, ws = run
    page = root / "authors.html"
    page.write_text("<html><head><title>Guide</title><script>no()</script></head>"
                    "<body><p>Up to 3000 words.</p></body></html>", encoding="utf-8")
    result = add("demo", page, GUIDE, "--note", "from the journal site")
    assert result.exit_code == 0, result.output
    data = json.loads((ws / "journal" / "sources.json").read_text())
    row = data["rows"][0]
    assert row["retrieved_by"] == "human-file" and row["status"] == "ok"
    assert row["url"] == GUIDE and row["note"] == "from the journal site"
    text = (ws / "journal" / "sources" / f"{row['id']}.txt").read_text()
    assert text == "Guide\nUp to 3000 words."
    assert (ws / "journal" / "sources" / f"{row['id']}.raw").exists()
