"""manuscript/metadata.yml: schema, identifiers, corresponding author (M-F 5.8). Synthetic data only."""

import yaml

from scieflow.research.manuscript import metadata

ORCID = "0000-0002-1825-0097"
ROR = "https://ror.org/00f54p054"


def _write(synth, data):
    (synth.ms / "metadata.yml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _load(synth):
    return metadata.load(synth.ms, synth.root)


def _base(**author_overrides):
    author = {
        "name": "Ada Synthetic", "email": "ada.synthetic@example.test", "orcid": ORCID,
        "affiliations": [{"name": "Synthetic Institute", "ror": ROR}],
        "credit": ["conceptualization"], "corresponding": True,
    }
    author.update(author_overrides)
    return {
        "version": 1, "journal": "journal-of-synthetic-neurology", "article_type": "research-article",
        "title": "Gait timing in synthetic cohorts", "keywords": ["gait"], "highlights": [],
        "authors": [author],
        "declarations": {"coi": "None declared.", "funding": None, "ethics": None},
    }


def test_valid_identifiers_are_canonicalised(synth):
    _write(synth, _base())
    meta, problems = _load(synth)
    assert problems == []
    assert meta["authors"][0]["orcid"] == f"https://orcid.org/{ORCID}"
    assert meta["authors"][0]["affiliations"][0]["ror"] == ROR
    assert meta["authors"][0]["credit"] == ["conceptualization"]


def test_a_bad_orcid_checksum_is_flagged_by_author_position(synth):
    _write(synth, _base(orcid="0000-0002-1825-0098"))
    _, problems = _load(synth)
    assert [(p.rule, p.path) for p in problems] == [("orcid-invalid", "authors[0].orcid")]


def test_a_bad_ror_is_flagged(synth):
    _write(synth, _base(affiliations=[{"name": "Synthetic Institute", "ror": "https://ror.org/zzzz"}]))
    _, problems = _load(synth)
    assert [p.rule for p in problems] == ["ror-invalid"]


def test_an_unknown_credit_role_is_flagged(synth):
    _write(synth, _base(credit=["wizardry"]))
    _, problems = _load(synth)
    assert [p.rule for p in problems] == ["credit-invalid"]


def test_two_corresponding_authors_are_flagged(synth):
    data = _base()
    second = dict(data["authors"][0], name="Bo Synthetic", corresponding=True)
    data["authors"].append(second)
    _write(synth, data)
    _, problems = _load(synth)
    assert [p.rule for p in problems] == ["corresponding"]


def test_an_unknown_key_is_refused(synth):
    data = _base()
    data["funder_secret"] = "x"
    _write(synth, data)
    _, problems = _load(synth)
    assert [p.rule for p in problems] == ["metadata-unknown-key"]
    assert problems[0].path == "funder_secret"


def test_no_author_email_appears_in_any_message(synth):
    _write(synth, _base(orcid="0000-0002-1825-0098", credit=["wizardry"]))
    _, problems = _load(synth)
    text = " ".join(f"{p.rule} {p.path} {p.message}" for p in problems)
    assert problems and "ada.synthetic" not in text and "@" not in text


def test_absent_metadata_is_not_an_error(synth):
    (synth.ms / "metadata.yml").unlink()
    meta, problems = _load(synth)
    assert meta is None and problems == []
