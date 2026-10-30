"""M-F t4 (plan 5.2): `journal research --refresh-of SLUG` pre-fills the journal name, the ISSN-L and the
gateway URLs of an adopted profile. The research itself (the gate, the fetch) is t3's and is replaced
here by a recorder, so no request is made."""

import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.journal import sources

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
