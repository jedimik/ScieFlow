"""M-F t4 (plan 5.6): `scieflow journal adopt` — the adoption order, the refusals and the views.

Synthetic data only. The run comes from the `synth` fixture (two ok sources); facts and the agent
draft are written here. Nothing is sent over the network.
"""

import hashlib
import os

import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events
from scieflow.research.journal import facts, profile, state

RUN = "synth-run"
SLUG = "journal-of-synthetic-neurology"
JOURNAL = "Journal of Synthetic Neurology"
ISSN = "1234-5679"
SRC1_URL = "https://guides.example.test/synthetic-neurology/authors"
SRC2_URL = "https://policies.example.test/synthetic-neurology/word-limits"


def _facts() -> dict:
    return {
        "version": 1, "as_of": "2026-10-01",
        "query": {"name": JOURNAL, "issn": ISSN},
        "openalex": {"id": "https://openalex.org/S123456", "display_name": JOURNAL, "issn_l": ISSN,
                     "issn": [ISSN], "host_organization_name": "Synthetic Press",
                     "homepage_url": "https://journal.example/", "is_oa": False, "is_in_doaj": False,
                     "apc_usd": None, "works_count": 42, "type": "journal"},
        "crossref": {"title": JOURNAL, "publisher": "Synthetic Press", "ISSN": [ISSN]},
        "norms": {"sample": {"n": 0, "from_date": "2024-10-01", "to_date": "2026-10-01"},
                  "references": {"median": None, "p25": None, "p75": None},
                  "abstract_words": {"median": None, "p25": None, "p75": None, "share_with_abstract": None},
                  "title_words": {"median": None}},
        "conflicts": [], "provider_status": {"crossref": "ok", "works": "ok"},
    }


def _ready(synth, profile_data, draft=None):
    """facts.json, the research state and the agent draft, as `journal research` leaves them."""
    (synth.journal_dir / "facts.json").write_text(facts.dumps(_facts()), encoding="utf-8")
    state.write_state(synth.journal_dir, {"version": 1, "journal": JOURNAL, "slug": SLUG,
                                          "phase": "fetched", "gate_id": None})
    data = profile_data() if draft is None else draft
    (synth.journal_dir / "profile.draft.yml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _adopt(*args):
    return CliRunner().invoke(main, ["journal", "adopt", RUN, *args])


def _yml(synth):
    return synth.profiles_dir / f"{SLUG}.yml"


def _nothing_adopted(synth):
    assert not _yml(synth).exists()


def test_adopt_writes_the_profile_and_its_views(synth, profile_data):
    _ready(synth, profile_data)
    result = _adopt()
    assert result.exit_code == 0, result.output
    text = _yml(synth).read_bytes()
    sha12 = hashlib.sha256(text).hexdigest()[:12]
    assert (synth.journal_dir / "adopted" / f"{sha12}.yml").read_bytes() == text
    assert (synth.journal_dir / "profile.yml").read_bytes() == text
    assert (synth.journal_dir / "profile.md").is_file()
    assert (synth.profiles_dir / f"{SLUG}.md").read_text(encoding="utf-8").startswith(
        f"<!-- scieflow: rendered from {SLUG}.yml by scieflow journal adopt; do not edit -->")


def test_the_adopted_profile_validates_against_its_run(synth, profile_data):
    _ready(synth, profile_data)
    assert _adopt().exit_code == 0
    data = profile.load_profile(_yml(synth))
    errors = [p for p in profile.validate(data, root=synth.root, run_dir=synth.run_dir) if p.level == "error"]
    assert errors == []
    check = CliRunner().invoke(main, ["journal", "validate", SLUG])
    assert check.exit_code == 0, check.output


def test_provenance_lists_both_urls_with_their_text_hashes(synth, profile_data):
    _ready(synth, profile_data)
    assert _adopt().exit_code == 0
    prov = profile.load_profile(_yml(synth))["provenance"]
    assert prov["run"] == RUN
    assert prov["facts_sha256"] == hashlib.sha256((synth.journal_dir / "facts.json").read_bytes()).hexdigest()
    by_url = {row["url"]: row for row in prov["sources"]}
    assert set(by_url) == {SRC1_URL, SRC2_URL}
    for url, sid in ((SRC1_URL, "s4f1c2a9e07"), (SRC2_URL, "s9b03d55e2a")):
        text = (synth.journal_dir / "sources" / f"{sid}.txt").read_bytes()
        assert by_url[url]["text_sha256"] == hashlib.sha256(text).hexdigest()
    assert prov["adopted_by"] == "human"
    assert prov["format"] == 1


def test_one_adoption_event_is_recorded(synth, profile_data):
    _ready(synth, profile_data)
    assert _adopt().exit_code == 0
    adopted = [e for e in events.read(synth.run_dir) if e.get("type") == "note.journal_adopted"]
    assert len(adopted) == 1
    assert adopted[0]["data"]["journal"] == JOURNAL
    assert adopted[0]["data"]["sources"] == 2
    assert adopted[0]["actor"] == "human"


def test_as_agent_records_the_agent_as_adopter(synth, profile_data):
    _ready(synth, profile_data)
    assert _adopt("--as-agent").exit_code == 0
    assert profile.load_profile(_yml(synth))["provenance"]["adopted_by"] == "agent"


def test_refused_inside_a_dispatch(synth, profile_data, monkeypatch):
    _ready(synth, profile_data)
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    result = _adopt()
    assert result.exit_code == 1
    assert "sub-agents never" in result.output or "dispatch" in result.output
    _nothing_adopted(synth)


def test_refused_without_an_ok_source(synth, profile_data):
    _ready(synth, profile_data)
    path = synth.journal_dir / "sources.json"
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    for row in data["sources"]:
        row["status"] = "failed"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _adopt().exit_code == 1
    _nothing_adopted(synth)


def test_fabricated_quote_is_refused_and_nothing_is_written(synth, profile_data):
    draft = profile_data()
    for item in draft["evidence"]:
        if item["field"] == "scope.text":
            item["quote"] = "An invented sentence that is not in the stored source text at all."
    _ready(synth, profile_data, draft=draft)
    result = _adopt()
    assert result.exit_code == 1
    assert "evidence-quote" in result.output
    _nothing_adopted(synth)


def test_number_missing_from_its_quote_is_refused(synth, profile_data):
    draft = profile_data()
    draft["article_types"][0]["limits"]["words_main"] = 3001
    _ready(synth, profile_data, draft=draft)
    result = _adopt()
    assert result.exit_code == 1
    assert "evidence-number" in result.output
    _nothing_adopted(synth)


def test_guideline_name_must_appear_in_its_quote(synth, profile_data):
    draft = profile_data()
    for item in draft["evidence"]:
        if item["field"] == "reporting_guidelines.by_study_type":
            item["quote"] = "Manuscripts must follow the randomised trial reporting guideline for authors."
    _ready(synth, profile_data, draft=draft)
    result = _adopt()
    assert result.exit_code == 1
    assert "evidence-name" in result.output
    _nothing_adopted(synth)


def test_a_symlinked_draft_is_refused(synth, profile_data):
    _ready(synth, profile_data)
    real = synth.run_dir / "elsewhere.yml"
    real.write_text((synth.journal_dir / "profile.draft.yml").read_text(encoding="utf-8"), encoding="utf-8")
    draft = synth.journal_dir / "profile.draft.yml"
    draft.unlink()
    os.symlink(real, draft)
    assert _adopt().exit_code == 1
    _nothing_adopted(synth)


def test_a_draft_over_one_megabyte_is_refused(synth, profile_data):
    _ready(synth, profile_data)
    (synth.journal_dir / "profile.draft.yml").write_text("x" * 1_000_001, encoding="utf-8")
    assert _adopt().exit_code == 1
    _nothing_adopted(synth)


def test_facts_are_never_taken_from_the_draft(synth, profile_data):
    draft = profile_data()
    draft["identity"]["publisher"] = "Wrong Press"
    _ready(synth, profile_data, draft=draft)
    result = _adopt()
    assert result.exit_code == 0, result.output
    assert "facts-overridden" in result.output
    assert profile.load_profile(_yml(synth))["identity"]["publisher"] == "Synthetic Press"


def test_a_different_existing_profile_needs_replace(synth, profile_data):
    _ready(synth, profile_data)
    assert _adopt().exit_code == 0
    changed = profile_data()
    changed["review"]["criteria"] = ["Clarity of aims"]
    _ready(synth, profile_data, draft=changed)
    result = _adopt()
    assert result.exit_code == 1
    assert "scieflow journal diff" in result.output
    assert _adopt("--replace").exit_code == 0
    assert profile.load_profile(_yml(synth))["review"]["criteria"] == ["Clarity of aims"]


def test_the_same_reading_again_is_not_a_change(synth, profile_data):
    _ready(synth, profile_data)
    assert _adopt().exit_code == 0
    assert _adopt().exit_code == 0


def test_a_hand_written_md_is_kept(synth, profile_data):
    _ready(synth, profile_data)
    page = synth.profiles_dir / f"{SLUG}.md"
    page.write_text("# Hand-written notes\n", encoding="utf-8")
    result = _adopt()
    assert result.exit_code == 0, result.output
    assert page.read_text(encoding="utf-8") == "# Hand-written notes\n"
    assert "kept" in result.output


def test_a_marker_md_is_rendered_again(synth, profile_data):
    _ready(synth, profile_data)
    page = synth.profiles_dir / f"{SLUG}.md"
    page.write_text(f"<!-- scieflow: rendered from {SLUG}.yml by scieflow journal adopt; do not edit -->\nold\n",
                    encoding="utf-8")
    assert _adopt().exit_code == 0
    text = page.read_text(encoding="utf-8")
    assert text.startswith(f"<!-- scieflow: rendered from {SLUG}.yml")
    assert "\nold\n" not in text
    assert "# Journal of Synthetic Neurology" in text


def test_json_output_names_the_hash(synth, profile_data):
    import json

    _ready(synth, profile_data)
    result = _adopt("--json")
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["sha256"] == hashlib.sha256(_yml(synth).read_bytes()).hexdigest()


def _nothing_written_at_all(synth):
    """Review t4 finding 1: a refusal leaves no profile, no working copy and no event behind."""
    assert not _yml(synth).exists()
    assert not (synth.journal_dir / "adopted").exists()
    assert not (synth.journal_dir / "profile.yml").exists()
    assert not (synth.journal_dir / "profile.md").exists()
    assert not [e for e in events.read(synth.run_dir) if e.get("type") == "note.journal_adopted"]


def test_a_symlinked_profile_page_is_refused_before_any_write(synth, profile_data, tmp_path):
    _ready(synth, profile_data)
    real = tmp_path / "elsewhere.md"
    real.write_text("# elsewhere\n", encoding="utf-8")
    os.symlink(real, synth.profiles_dir / f"{SLUG}.md")
    result = _adopt()
    assert result.exit_code == 1, result.output
    assert "symbolic link" in result.output
    _nothing_written_at_all(synth)


def test_a_draft_without_facts_blocks_adopts_with_no_facts_warning(synth, profile_data):
    """Review t4 finding 4: a warning only when the draft supplied a facts block that differs."""
    draft = profile_data()
    for name in ("identity", "oa", "norms", "provenance"):
        del draft[name]
    _ready(synth, profile_data, draft=draft)
    result = _adopt()
    assert result.exit_code == 0, result.output
    assert "facts-overridden" not in result.output
    assert profile.load_profile(_yml(synth))["identity"]["publisher"] == "Synthetic Press"
