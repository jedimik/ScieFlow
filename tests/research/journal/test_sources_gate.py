"""M-F t3 (plan 5.4): candidates, the journal-sources gate, fetching and saved pages.

Synthetic pages and journals only; every HTTP call is mocked (`responses`). The gate is answered
by a human only: these tests play that human through `gates.answer(..., actor="human")`.
"""

import hashlib
import json
import os
import re
import secrets
from pathlib import Path

import pytest
import responses
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import gates, network
from scieflow.core.project import Project
from scieflow.research.journal import sources, state

REPO = Path(__file__).resolve().parents[3]
SOURCES = r"https://api\.openalex\.org/sources.*"
WORKS = r"https://api\.openalex\.org/works.*"
CROSSREF = "https://api.crossref.org/journals/1234-5679"
HOME = "https://journal.example/"
GUIDE = "https://guides.journal.example/authors"
GUIDE2 = "https://guides.journal.example/word-limits"
JOURNAL = "Journal of Synthetic Neurology"
ISSN = "1234-5679"
PAGE = ("<html><head><title>Authors</title></head><body><h1>Limits</h1>"
        "<p>Research articles may contain up to 3000 words.</p></body></html>")
SAVED_URL = "https://saved.journal.example/authors"


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


def enable(root):
    (root / "config" / "integrations.yml").write_text(yaml.safe_dump({"providers": {
        "journal-guidelines": {"enabled": True, "rate_per_s": 1000}}}))


def mock_facts():
    responses.add(responses.GET, re.compile(SOURCES), json={"results": [{
        "id": "https://openalex.org/S123456", "display_name": JOURNAL,
        "issn_l": ISSN, "issn": [ISSN], "host_organization_name": "Synthetic Press",
        "homepage_url": HOME, "is_oa": False, "is_in_doaj": False,
        "apc_usd": None, "works_count": 42, "type": "journal"}]})
    responses.add(responses.GET, re.compile(WORKS), json={"results": []})
    responses.add(responses.GET, CROSSREF, json={"message": {
        "title": JOURNAL, "publisher": "Synthetic Press", "ISSN": [ISSN]}})
    responses.add(responses.GET, HOME, body=PAGE, status=200)


def page_calls():
    """Every call to a journal's own site (the homepage and the guideline pages)."""
    return [c for c in responses.calls if "journal.example" in c.request.url]


def go(*urls, extra=()):
    args = ["journal", "research", "demo", "--journal", JOURNAL, "--issn", ISSN,
            "--as-of", "2026-10-01"]
    for url in urls:
        args += ["--url", url]
    return CliRunner().invoke(main, [*args, *extra], catch_exceptions=False)


def open_gates(ws):
    return [g for g in gates.list_gates(ws, "open") if g["kind"] == "journal-sources"]


def human_answer(root, ws, answer):
    gate = open_gates(ws)[0]
    return gates.answer(Project(root), ws, gate["id"], answer, actor="human")


def rows(ws):
    return json.loads((ws / "journal" / "sources.json").read_text())["rows"]


def phase(ws):
    return json.loads((ws / "journal" / "state.json").read_text())["phase"]


# --- the provider switched off and on ----------------------------------------------------------


@responses.activate
def test_with_the_provider_off_the_run_is_sources_manual_with_no_gate(run):
    root, ws = run
    mock_facts()
    result = go(GUIDE)
    assert result.exit_code == 0, result.output
    assert phase(ws) == "sources-manual"
    assert gates.list_gates(ws) == []
    assert len(page_calls()) == 0


@responses.activate
def test_enabled_opens_one_human_gate_carrying_the_candidate_hash(run):
    root, ws = run
    enable(root)
    mock_facts()
    result = go(GUIDE)
    assert result.exit_code == 0, result.output
    gate = open_gates(ws)[0]
    assert gate["requires_human"] is True
    sha = json.loads((ws / "journal" / "candidates.json").read_text())["sha256"]
    assert sha[:12] in gate["question"]
    assert f"scieflow gate answer demo {gate['id']} approve" in result.output
    assert phase(ws) == "gate-open"
    assert len(page_calls()) == 0                       # nothing fetched before approval


@responses.activate
def test_an_agent_cannot_answer_the_journal_sources_gate(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    with pytest.raises(gates.GateError):
        gates.answer(Project(root), ws, open_gates(ws)[0]["id"], "approve", actor="agent",
                     rationale="x")


@responses.activate
def test_a_human_approval_fetches_exactly_the_listed_urls(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    responses.add(responses.GET, GUIDE, body=PAGE, status=200)
    human_answer(root, ws, "approve")
    result = go(GUIDE)
    assert result.exit_code == 0, result.output
    assert len(page_calls()) == 2                       # the homepage and the one --url
    ok = [r for r in rows(ws) if r["status"] == "ok"]
    assert len(ok) == 2
    text = (ws / "journal" / "sources" / f"{ok[0]['id']}.txt").read_text()
    assert "Research articles may contain up to 3000 words." in text
    assert (ws / "journal" / "sources" / f"{ok[0]['id']}.raw").exists()
    assert phase(ws) == "fetched"


@responses.activate
def test_a_human_denial_fetches_nothing(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    human_answer(root, ws, "deny")
    result = go(GUIDE)
    assert result.exit_code == 0
    assert phase(ws) == "denied"
    assert len(page_calls()) == 0


@responses.activate
def test_a_gate_whose_hash_differs_from_candidates_is_refused_with_no_call(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    human_answer(root, ws, "approve")
    path = ws / "journal" / "candidates.json"
    data = json.loads(path.read_text())
    data["urls"].append(GUIDE2)                         # urls changed, sha256 left as it was
    path.write_text(json.dumps(data))
    result = go(GUIDE)
    assert result.exit_code == 1, result.output
    assert len(page_calls()) == 0


@responses.activate
def test_a_hand_edited_gate_answered_by_an_agent_is_refused(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    gate = open_gates(ws)[0]
    gate.update(state="answered", answer="approve", answered_by="agent", answered="x")
    (ws / "gates" / f"{gate['id']}.json").write_text(json.dumps(gate))
    result = go(GUIDE)
    assert result.exit_code == 1, result.output
    assert len(page_calls()) == 0


@responses.activate
def test_a_changed_url_list_opens_a_new_gate_and_the_old_one_stays_open(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    first = open_gates(ws)[0]["id"]
    go(GUIDE, GUIDE2)
    ids = [g["id"] for g in open_gates(ws)]
    assert len(ids) == 2 and first in ids


@responses.activate
def test_retry_failed_re_requests_only_the_failed_rows(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE, GUIDE2)
    responses.add(responses.GET, GUIDE, status=404)
    responses.add(responses.GET, GUIDE2, body=PAGE, status=200)
    human_answer(root, ws, "approve")
    go(GUIDE, GUIDE2)
    assert sorted(r["status"] for r in rows(ws) if r["url"] in (GUIDE, GUIDE2)) == \
        ["failed", "ok"]
    before = len(page_calls())
    responses.replace(responses.GET, GUIDE, body=PAGE, status=200)
    result = go(GUIDE, GUIDE2, extra=["--retry-failed"])
    assert result.exit_code == 0, result.output
    assert len(page_calls()) == before + 1
    assert all(r["status"] == "ok" for r in rows(ws) if r["url"] in (GUIDE, GUIDE2))


@responses.activate
def test_a_redirect_is_a_failure_and_its_location_is_never_requested(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    responses.add(responses.GET, GUIDE, status=301,
                  headers={"Location": "https://elsewhere.journal.example/authors"})
    human_answer(root, ws, "approve")
    result = go(GUIDE)
    assert result.exit_code == 0, result.output
    row = [r for r in rows(ws) if r["url"] == GUIDE][0]
    assert row["status"] == "failed" and row["classification"] == "HTTP_301"
    assert "re-run with the final URL" in result.output
    assert not any("elsewhere" in c.request.url for c in responses.calls)


@responses.activate
def test_a_page_over_two_million_characters_is_too_large_and_nothing_is_stored(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    responses.add(responses.GET, GUIDE, body="x" * 2_000_001, status=200)
    human_answer(root, ws, "approve")
    go(GUIDE)
    row = [r for r in rows(ws) if r["url"] == GUIDE][0]
    assert row["status"] == "too-large"
    assert not (ws / "journal" / "sources" / f"{row['id']}.txt").exists()
    assert not (ws / "journal" / "sources" / f"{row['id']}.raw").exists()


@responses.activate
def test_an_l2_run_is_refused_with_exit_3_before_any_request(run):
    root, ws = run
    enable(root)
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False))
    mock_facts()
    result = go(GUIDE)
    assert result.exit_code == 3, result.output
    assert len(responses.calls) == 0


@responses.activate
def test_a_record_run_replays_the_same_pages_with_zero_calls(run):
    root, ws = run
    enable(root)
    network.write_mode(ws, "record", actor="human", reason="t3 record test")
    mock_facts()
    go(GUIDE)
    responses.add(responses.GET, GUIDE, body=PAGE, status=200)
    human_answer(root, ws, "approve")
    go(GUIDE)
    facts_bytes = (ws / "journal" / "facts.json").read_bytes()
    text_name = sorted(p.name for p in (ws / "journal" / "sources").glob("*.txt"))[0]
    text_bytes = (ws / "journal" / "sources" / text_name).read_bytes()

    network.write_mode(ws, "replay", actor="human", reason="t3 replay test")
    responses.calls.reset()
    assert go(GUIDE, extra=["--restart"]).exit_code == 0
    human_answer(root, ws, "approve")                    # a new list-hash gate: approve it again
    responses.calls.reset()
    result = go(GUIDE)
    assert result.exit_code == 0, result.output
    assert len(responses.calls) == 0
    assert (ws / "journal" / "facts.json").read_bytes() == facts_bytes
    assert (ws / "journal" / "sources" / text_name).read_bytes() == text_bytes


def test_the_candidate_hash_is_over_the_url_lines():
    urls = [HOME, GUIDE]
    assert sources.sha_of(urls) == hashlib.sha256(
        ("\n".join(urls) + "\n").encode("utf-8")).hexdigest()


# --- M-F t3 fix (review r1): hosts, saved pages, gates, atomic writes ----------------------------


def test_a_numeric_homepage_is_not_a_candidate_and_a_numeric_url_is_refused():
    assert sources.candidates_for("https://0x7f.0.0.1/", [GUIDE]) == [GUIDE]
    with pytest.raises(sources.SourcesError):
        sources.candidates_for(None, ["https://10.1/guides"])


def test_a_non_string_homepage_is_no_homepage():
    assert sources.candidates_for(123, [GUIDE]) == [GUIDE]
    assert sources.candidates_for({"href": HOME}, []) == []


@responses.activate
def test_a_numeric_host_url_is_refused_when_the_list_is_built_and_no_gate_opens(run):
    root, ws = run
    enable(root)
    result = go("https://127.1/guides/authors")
    assert result.exit_code == 1, result.output
    assert gates.list_gates(ws) == []
    assert len(responses.calls) == 0


def saved_add(tmp_path, url=SAVED_URL, body=PAGE):
    path = tmp_path / "guide.html"
    path.write_text(body, encoding="utf-8")
    return CliRunner().invoke(main, ["journal", "add-source", "demo", "--file", str(path),
                                     "--url", url], catch_exceptions=False)


@responses.activate
def test_a_saved_page_survives_a_run_with_the_provider_off(run, tmp_path):
    root, ws = run
    mock_facts()
    assert go(GUIDE).exit_code == 0                      # sources-manual: the journal is recorded
    added = saved_add(tmp_path)
    assert added.exit_code == 0, added.output
    result = go(GUIDE, extra=["--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["counts"]["ok"] == 1
    human = [r for r in rows(ws) if r["retrieved_by"] == "human-file"]
    assert len(human) == 1 and human[0]["url"] == SAVED_URL and human[0]["status"] == "ok"
    assert json.loads((ws / "journal" / "sources.json").read_text())["journal"] == JOURNAL


@responses.activate
def test_a_saved_page_survives_an_approved_fetch_of_other_pages(run, tmp_path):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)                                            # gate open
    assert saved_add(tmp_path).exit_code == 0
    responses.add(responses.GET, GUIDE, body=PAGE, status=200)
    human_answer(root, ws, "approve")
    result = go(GUIDE)
    assert result.exit_code == 0, result.output
    by_url = {r["url"]: r for r in rows(ws)}
    assert by_url[SAVED_URL]["retrieved_by"] == "human-file" and by_url[SAVED_URL]["status"] == "ok"
    assert by_url[GUIDE]["status"] == "ok"
    assert (ws / "journal" / "sources" / f"{by_url[SAVED_URL]['id']}.txt").exists()


@responses.activate
def test_a_failed_fetch_never_replaces_a_saved_page_for_the_same_url(run, tmp_path):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    assert saved_add(tmp_path, url=GUIDE).exit_code == 0
    responses.add(responses.GET, GUIDE, status=404)
    human_answer(root, ws, "approve")
    result = go(GUIDE)
    assert result.exit_code == 0, result.output
    assert len([c for c in responses.calls if c.request.url == GUIDE]) == 1   # still one request
    row = [r for r in rows(ws) if r["url"] == GUIDE][0]
    assert row["retrieved_by"] == "human-file" and row["status"] == "ok"


@responses.activate
def test_a_new_list_names_the_earlier_gate_that_is_still_open(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    first = open_gates(ws)[0]["id"]
    result = go(GUIDE, GUIDE2)
    assert result.exit_code == 0, result.output
    assert first in result.output and "still open" in result.output


@pytest.mark.parametrize("change", [{"requires_human": False}, {"kind": "review-decision"}])
@responses.activate
def test_a_gate_that_is_not_a_human_journal_sources_gate_authorises_nothing(run, change):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    gate = open_gates(ws)[0]
    gate.update(state="answered", answer="approve", answered_by="human", answered="2026-10-01")
    gate.update(change)
    (ws / "gates" / f"{gate['id']}.json").write_text(json.dumps(gate))
    result = go(GUIDE)
    assert result.exit_code == 1, result.output
    assert len(page_calls()) == 0


@responses.activate
def test_retry_failed_on_a_list_nobody_approved_requests_nothing(run):
    root, ws = run
    enable(root)
    mock_facts()
    go(GUIDE)
    result = go(GUIDE, extra=["--retry-failed"])
    assert result.exit_code == 0, result.output
    assert phase(ws) == "gate-open"
    assert len(page_calls()) == 0


@responses.activate
def test_a_replay_miss_during_the_page_fetch_exits_6_and_sends_nothing(run):
    root, ws = run
    enable(root)
    network.write_mode(ws, "record", actor="human", reason="t3 fix replay-miss test")
    mock_facts()
    go(GUIDE)
    responses.add(responses.GET, GUIDE, body=PAGE, status=200)
    human_answer(root, ws, "approve")
    assert go(GUIDE).exit_code == 0
    network.write_mode(ws, "replay", actor="human", reason="t3 fix replay-miss test")
    responses.calls.reset()
    go(GUIDE, GUIDE2)                                     # a list the record never fetched
    human_answer(root, ws, "approve")
    result = go(GUIDE, GUIDE2)
    assert result.exit_code == 6, result.output
    assert len(responses.calls) == 0


def test_an_atomic_write_never_follows_a_symlink_at_the_old_temp_name(tmp_path):
    folder = tmp_path / "journal"
    folder.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    (folder / f".state.json.{os.getpid()}.tmp").symlink_to(outside)
    state.atomic_write(folder / "state.json", b"{}")
    assert outside.read_text(encoding="utf-8") == "keep"
    assert (folder / "state.json").read_bytes() == b"{}"


def test_an_existing_temp_name_is_refused_and_nothing_outside_is_written(tmp_path, monkeypatch):
    folder = tmp_path / "journal"
    folder.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(secrets, "token_hex", lambda _n: "fixed")
    (folder / ".state.json.fixed.tmp").symlink_to(outside)
    with pytest.raises(state.StateError, match="already exists"):
        state.atomic_write(folder / "state.json", b"{}")
    assert outside.read_text(encoding="utf-8") == "keep"
