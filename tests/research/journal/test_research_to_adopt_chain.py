"""M-F final fix (review r1 finding 1): the REAL producers feed the REAL consumers on one run.

`journal research` (approved fetch) and `journal add-source` write journal/sources.json; `journal
prompt` and `journal adopt` read it back. The unit tests used differently shaped fixtures, so this
chain was never exercised. Synthetic journal and pages only; every HTTP call is mocked.
"""

import json
import shutil
from pathlib import Path

import pytest
import responses
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from tests.research.journal.conftest import SRC1, SRC1_TEXT, valid_profile_data
from tests.research.journal.test_sources_gate import (
    GUIDE,
    enable,
    go,
    human_answer,
    mock_facts,
    rows,
)

PAGE = ("<html><head><title>Authors</title></head><body>"
        + "".join(f"<p>{line}</p>" for line in SRC1_TEXT.splitlines())
        + "</body></html>")
SAVED_URL = "https://saved.journal.example/authors"


@pytest.fixture
def run(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    repo = Path(__file__).resolve().parents[3]
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


def _cli(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def _draft(ws, sid):
    """A valid synthetic draft whose quotes come from the fetched text, keyed by the real source id."""
    data = json.loads(json.dumps(valid_profile_data()).replace(SRC1, sid))
    (ws / "journal" / "profile.draft.yml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _slug(ws):
    return json.loads((ws / "journal" / "state.json").read_text())["slug"]


@responses.activate
def test_fetched_research_output_feeds_prompt_adopt_and_validate(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    responses.add(responses.GET, GUIDE, body=PAGE, status=200)
    human_answer(root, ws, "approve")
    assert go(GUIDE).exit_code == 0
    ok = [r for r in rows(ws) if r["status"] == "ok"]
    guide = next(r for r in ok if r["url"] == GUIDE)

    prompted = _cli("journal", "prompt", "demo", "--write")
    assert prompted.exit_code == 0, prompted.output

    _draft(ws, guide["id"])
    adopted = _cli("journal", "adopt", "demo")
    assert adopted.exit_code == 0, adopted.output

    validated = _cli("journal", "validate", _slug(ws))
    assert validated.exit_code == 0, validated.output


@responses.activate
def test_add_source_row_feeds_prompt_adopt_and_validate(run):
    root, ws = run
    mock_facts()
    assert go(GUIDE).exit_code == 0          # provider off: sources-manual, no gate, no fetch
    page = root / "authors.html"
    page.write_text(PAGE, encoding="utf-8")
    added = _cli("journal", "add-source", "demo", "--file", str(page), "--url", SAVED_URL)
    assert added.exit_code == 0, added.output
    ok = [r for r in rows(ws) if r["status"] == "ok"]
    assert len(ok) == 1 and ok[0]["retrieved_by"] == "human-file"

    prompted = _cli("journal", "prompt", "demo", "--write")
    assert prompted.exit_code == 0, prompted.output

    _draft(ws, ok[0]["id"])
    adopted = _cli("journal", "adopt", "demo")
    assert adopted.exit_code == 0, adopted.output

    validated = _cli("journal", "validate", _slug(ws))
    assert validated.exit_code == 0, validated.output
