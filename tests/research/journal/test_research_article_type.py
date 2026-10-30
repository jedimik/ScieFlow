"""M-F final fix (review r1 finding 2, Rule D decision): `journal research --article-type ID`.

Plan 5.2 names the option and plan 5.6 the article-type hint in the agent prompt. The option is
validated before any request, stored in journal/state.json, and printed as a hint by `journal prompt`.
Synthetic journal and pages only; every HTTP call is mocked.
"""

import json
from pathlib import Path

import pytest
import responses
from click.testing import CliRunner

from scieflow.cli import main
from tests.research.journal.test_sources_gate import GUIDE, go, mock_facts, page_calls

PAGE = ("<html><head><title>Authors</title></head><body>"
        "<p>Research articles may contain up to 3000 words.</p></body></html>")


@pytest.fixture
def run(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    repo = Path(__file__).resolve().parents[3]
    import shutil
    shutil.copytree(repo / "schemas", root / "schemas")
    shutil.copytree(repo / "config" / "reporting", root / "config" / "reporting")
    (root / "config" / "journals").mkdir()
    (root / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.chdir(root)
    return root, ws


def _state(ws):
    return json.loads((ws / "journal" / "state.json").read_text(encoding="utf-8"))


@responses.activate
def test_research_records_the_article_type_in_the_state(run):
    root, ws = run
    mock_facts()
    result = go(GUIDE, extra=("--article-type", "research-article"))
    assert result.exit_code == 0, result.output
    assert _state(ws)["article_type"] == "research-article"


@responses.activate
def test_a_bad_article_type_is_a_usage_error_with_no_request(run):
    root, ws = run
    result = CliRunner().invoke(main, ["journal", "research", "demo", "--journal", "Journal X",
                                       "--as-of", "2026-10-01", "--article-type", "Not Kebab!"])
    assert result.exit_code == 2, result.output
    assert page_calls() == [] and len(responses.calls) == 0
    assert not (ws / "journal").exists()


@responses.activate
def test_the_prompt_carries_the_article_type_as_a_hint(run):
    root, ws = run
    mock_facts()
    assert go(GUIDE, extra=("--article-type", "research-article")).exit_code == 0
    page = root / "authors.html"
    page.write_text(PAGE, encoding="utf-8")
    added = CliRunner().invoke(main, ["journal", "add-source", "demo", "--file", str(page),
                                      "--url", "https://saved.journal.example/authors"],
                               catch_exceptions=False)
    assert added.exit_code == 0, added.output
    prompted = CliRunner().invoke(main, ["journal", "prompt", "demo", "--write"],
                                  catch_exceptions=False)
    assert prompted.exit_code == 0, prompted.output
    text = (ws / "prompts" / "journal-profile.md").read_text(encoding="utf-8")
    assert "## Article type requested" in text
    assert "`research-article`" in text
