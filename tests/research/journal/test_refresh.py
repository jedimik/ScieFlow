"""M-F t4 (plan 5.2): `journal research --refresh-of SLUG` pre-fills the journal name, the ISSN-L and the
gateway URLs of an adopted profile. The research itself (the gate, the fetch) is t3's and is replaced
here by a recorder, so no request is made."""

import json
import re

import responses
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.journal import sources

SOURCES = re.compile(r"https://api\.openalex\.org/sources.*")
WORKS = re.compile(r"https://api\.openalex\.org/works.*")
CROSSREF = "https://api.crossref.org/journals/1234-5679"

RUN = "synth-run"
SLUG = "journal-of-synthetic-neurology"
JOURNAL = "Journal of Synthetic Neurology"
ISSN = "1234-5679"
SRC1_URL = "https://guides.example.test/synthetic-neurology/authors"
SRC2_URL = "https://policies.example.test/synthetic-neurology/word-limits"


def _recorder(monkeypatch):
    calls = []

    def fake(project, run, **kwargs):
        calls.append(kwargs)
        return sources.Result(0, "gate-open", ["Waiting for a human decision."], "g-test", {})

    monkeypatch.setattr(sources, "research", fake)
    return calls


def test_refresh_prefills_name_issn_and_gateway_urls(synth, profile_data, monkeypatch):
    calls = _recorder(monkeypatch)
    synth.profiles_dir.mkdir(exist_ok=True)
    (synth.profiles_dir / f"{SLUG}.yml").write_text(
        yaml.safe_dump(profile_data(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    result = CliRunner().invoke(main, ["journal", "research", RUN, "--refresh-of", SLUG,
                                       "--as-of", "2026-10-01"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    call = calls[0]
    assert call["journal"] == JOURNAL
    assert call["issn"] == ISSN
    assert call["slug"] == SLUG
    assert set(call["extra_urls"]) == {SRC1_URL, SRC2_URL}
    assert "Waiting for a human decision." in result.output


def test_refresh_of_a_missing_profile_is_refused(synth, monkeypatch):
    calls = _recorder(monkeypatch)
    result = CliRunner().invoke(main, ["journal", "research", RUN, "--refresh-of", SLUG,
                                       "--as-of", "2026-10-01"])
    assert result.exit_code == 1
    assert calls == []


def test_research_needs_a_journal_name_or_a_refresh(synth, monkeypatch):
    calls = _recorder(monkeypatch)
    result = CliRunner().invoke(main, ["journal", "research", RUN, "--as-of", "2026-10-01"])
    assert result.exit_code == 2
    assert calls == []


@responses.activate
def test_refresh_runs_the_real_research_and_its_gate_carries_the_gateway_urls(synth, profile_data):
    """Review t4 finding 5: no recorder. HTTP is mocked at `responses`; the guide pages are never requested."""
    (synth.root / "config" / "integrations.yml").write_text(yaml.safe_dump(
        {"providers": {"journal-guidelines": {"enabled": True, "rate_per_s": 1000}}}), encoding="utf-8")
    synth.profiles_dir.mkdir(exist_ok=True)
    (synth.profiles_dir / f"{SLUG}.yml").write_text(
        yaml.safe_dump(profile_data(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    responses.add(responses.GET, SOURCES, json={"results": [{
        "id": "https://openalex.org/S123456", "display_name": JOURNAL, "issn_l": ISSN, "issn": [ISSN],
        "host_organization_name": "Synthetic Press", "homepage_url": None, "is_oa": False,
        "is_in_doaj": False, "apc_usd": None, "works_count": 42, "type": "journal"}]})
    responses.add(responses.GET, WORKS, json={"results": []})
    responses.add(responses.GET, CROSSREF, json={"message": {
        "title": JOURNAL, "publisher": "Synthetic Press", "ISSN": [ISSN]}})
    result = CliRunner().invoke(main, ["journal", "research", RUN, "--refresh-of", SLUG,
                                       "--as-of", "2026-10-01", "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["phase"] == "gate-open" and body["gate"]
    candidates = json.loads((synth.journal_dir / "candidates.json").read_text(encoding="utf-8"))
    assert {SRC1_URL, SRC2_URL} <= set(candidates["urls"])
    guide_calls = [c for c in responses.calls if "example.test" in c.request.url
                   and "api." not in c.request.url]
    assert guide_calls == []

