"""Tests for the canonical record model and its normalizers.

Non-ASCII text is written with explicit escapes so the assertions stay unambiguous about
which normalization form is under test.
"""

# Ported from researcher v1.0.0 (MIT), core/tests/test_model.py. Imports are
# rewritten to scieflow._vendor.researcher_core; test bodies are unchanged. See VENDORED.md.

from __future__ import annotations

import unicodedata

import pytest

from scieflow._vendor.researcher_core.model import (
    CSLDate,
    CSLName,
    CSLRecord,
    OALocation,
    canonical_json,
    content_hash,
    is_valid_doi,
    normalize_authors,
    normalize_doi,
    normalize_title,
    parse_name,
    title_fingerprint,
)

# "e" + COMBINING ACUTE ACCENT (decomposed) versus the precomposed "e-acute".
E_ACUTE_NFD = "é"
E_ACUTE_NFC = "é"


# ---------------------------------------------------------------------------
# DOI normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.7717/PeerJ.4375", "10.7717/peerj.4375"),
        ("https://doi.org/10.7717/PeerJ.4375", "10.7717/peerj.4375"),
        ("http://doi.org/10.7717/peerj.4375", "10.7717/peerj.4375"),
        ("https://dx.doi.org/10.7717/peerj.4375", "10.7717/peerj.4375"),
        ("http://dx.doi.org/10.7717/peerj.4375", "10.7717/peerj.4375"),
        ("doi:10.7717/peerj.4375", "10.7717/peerj.4375"),
        ("DOI: 10.7717/peerj.4375", "10.7717/peerj.4375"),
        ("  10.7717/peerj.4375  ", "10.7717/peerj.4375"),
        ("<https://doi.org/10.7717/peerj.4375>", "10.7717/peerj.4375"),
        ("doi.org/10.7717/peerj.4375", "10.7717/peerj.4375"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_doi(raw, expected):
    assert normalize_doi(raw) == expected


def test_normalize_doi_is_idempotent():
    once = normalize_doi("https://doi.org/10.1038/S41586-020-2649-2")
    assert normalize_doi(once) == once == "10.1038/s41586-020-2649-2"


def test_is_valid_doi():
    assert is_valid_doi("https://doi.org/10.7717/peerj.4375")
    assert not is_valid_doi("peerj.4375")
    assert not is_valid_doi("10.77/")
    assert not is_valid_doi("")


# ---------------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------------


def test_normalize_title_collapses_whitespace_and_applies_nfc():
    decomposed = f"Caf{E_ACUTE_NFD}  du\tmonde\n\nrevisited"
    assert not unicodedata.is_normalized("NFC", decomposed)

    normalized = normalize_title(decomposed)

    assert normalized == f"Caf{E_ACUTE_NFC} du monde revisited"
    assert unicodedata.is_normalized("NFC", normalized)


def test_normalize_title_of_nfd_and_nfc_agree():
    nfd = f"R{E_ACUTE_NFD}sum{E_ACUTE_NFD} of Attention"
    nfc = f"R{E_ACUTE_NFC}sum{E_ACUTE_NFC} of Attention"
    assert nfd != nfc
    assert normalize_title(nfd) == normalize_title(nfc) == nfc


def test_title_fingerprint_strips_case_and_punctuation():
    assert title_fingerprint("Deep Learning: A Review!") == "deep learning a review"
    assert title_fingerprint("Deep learning - a review") == "deep learning a review"


# ---------------------------------------------------------------------------
# Author names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "family", "given", "suffix"),
    [
        ("Doe, John A.", "Doe", "John A.", ""),
        ("John A. Doe", "Doe", "John A.", ""),
        ("Heather Piwowar", "Piwowar", "Heather", ""),
        ("Piwowar, H.", "Piwowar", "H.", ""),
        ("Jan van der Berg", "van der Berg", "Jan", ""),
        ("van der Berg, Jan", "van der Berg", "Jan", ""),
        ("Doe, Jr., John", "Doe", "John", "Jr."),
        ("Martin Luther King Jr.", "King", "Martin Luther", "Jr."),
        ("Plato", "Plato", "", ""),
        ("   ", "", "", ""),
    ],
)
def test_parse_name(raw, family, given, suffix):
    name = parse_name(raw)
    assert (name.family, name.given, name.suffix) == (family, given, suffix)


def test_normalize_authors_accepts_strings_mappings_and_names():
    authors = normalize_authors(
        [
            "Heather Piwowar",
            {"family": "Priem", "given": "Jason"},
            CSLName(family="Orr", given="Richard"),
            "",
        ]
    )
    assert [a.surname for a in authors] == ["Piwowar", "Priem", "Orr"]


def test_name_display_and_surname():
    assert CSLName(family="Doe", given="John", suffix="Jr.").display() == "John Doe, Jr."
    assert CSLName(literal="World Health Organization").display() == "World Health Organization"
    assert CSLName(literal="World Health Organization").surname == "World Health Organization"


def test_name_csl_json_round_trip():
    name = CSLName(family="Berg", given="Jan", non_dropping_particle="van der")
    payload = name.to_csl_json()
    assert payload["non-dropping-particle"] == "van der"
    assert CSLName.from_csl_json(payload) == name


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_csl_date_from_date_parts():
    date = CSLDate.from_csl_json({"date-parts": [[2018, 2, 13]]})
    assert (date.year, date.month, date.day) == (2018, 2, 13)
    assert date.to_csl_json() == {"date-parts": [[2018, 2, 13]]}


def test_csl_date_year_only_round_trip():
    date = CSLDate.from_csl_json({"date-parts": [[2018]]})
    assert date.to_csl_json() == {"date-parts": [[2018]]}


def test_csl_date_parses_strings_and_ints():
    assert CSLDate.from_csl_json(2020).year == 2020
    assert CSLDate.from_csl_json("2020-05").month == 5
    assert CSLDate.from_csl_json("2020-05-17").day == 17
    assert CSLDate.from_csl_json(None) is None


def test_csl_date_falls_back_to_raw():
    date = CSLDate.from_csl_json({"raw": "in press"})
    assert date is not None
    assert date.year is None
    assert date.raw == "in press"


# ---------------------------------------------------------------------------
# CSLRecord
# ---------------------------------------------------------------------------


def test_record_normalizes_on_construction():
    record = CSLRecord(
        title="  The   state\tof OA  ",
        DOI="https://doi.org/10.7717/PeerJ.4375",
        author=["Heather Piwowar", "Priem, Jason"],
        issued=2018,
    )
    assert record.DOI == "10.7717/peerj.4375"
    assert record.title == "The state of OA"
    assert record.first_author_surname == "Piwowar"
    assert record.year == 2018
    # The default id falls back to the normalized DOI.
    assert record.id == "10.7717/peerj.4375"


def test_record_csl_json_round_trip():
    record = CSLRecord(
        type="article-journal",
        title="The state of OA",
        author=[CSLName(family="Piwowar", given="Heather")],
        issued=CSLDate(year=2018, month=2, day=13),
        container_title="PeerJ",
        volume="6",
        page="e4375",
        DOI="10.7717/peerj.4375",
        ISSN="2167-8359",
        source="openalex",
        openalex_id="https://openalex.org/W2741809807",
        citation_count=1250,
        is_retracted=False,
        extra={"host_venue_type": "journal"},
    )
    payload = record.to_csl_json()

    # Standard CSL keys stay at the top level; extensions live under "custom".
    assert payload["DOI"] == "10.7717/peerj.4375"
    assert payload["container-title"] == "PeerJ"
    assert payload["issued"] == {"date-parts": [[2018, 2, 13]]}
    assert payload["custom"]["source"] == "openalex"
    assert payload["custom"]["citation_count"] == 1250
    assert payload["custom"]["is_retracted"] is False
    assert payload["custom"]["host_venue_type"] == "journal"

    restored = CSLRecord.from_csl_json(payload)
    assert restored.to_csl_json() == payload
    assert restored.content_hash() == record.content_hash()
    assert restored.extra == {"host_venue_type": "journal"}


def test_record_from_csl_json_accepts_lowercase_doi_key():
    record = CSLRecord.from_csl_json({"doi": "https://doi.org/10.1000/XYZ", "title": "T"})
    assert record.DOI == "10.1000/xyz"


def test_record_content_hash_is_insertion_order_independent():
    left = CSLRecord(title="A", DOI="10.1/a", author=["Ann Lee"])
    right = CSLRecord(DOI="10.1/A", author=["Lee, Ann"], title="A")
    assert left.content_hash() == right.content_hash()


def test_record_content_hash_changes_with_content():
    left = CSLRecord(title="A", DOI="10.1/a")
    right = CSLRecord(title="B", DOI="10.1/a")
    assert left.content_hash() != right.content_hash()


def test_record_default_id_prefers_doi_then_arxiv():
    assert CSLRecord(arxiv_id="2103.00020").id == "arxiv:2103.00020"
    assert CSLRecord(DOI="10.1/a", arxiv_id="2103.00020").id == "10.1/a"
    anonymous = CSLRecord(title="No identifiers here")
    assert len(anonymous.id) == 16


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def test_canonical_json_sorts_keys_and_fixes_separators():
    assert canonical_json({"b": 1, "a": [2, {"d": 4, "c": 3}]}) == '{"a":[2,{"c":3,"d":4}],"b":1}'


def test_canonical_json_keeps_unicode_unescaped():
    text = f"R{E_ACUTE_NFC}sum{E_ACUTE_NFC}"
    assert canonical_json({"t": text}) == '{"t":"' + text + '"}'


def test_canonical_json_rejects_nan():
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_content_hash_is_stable_and_order_independent():
    left = content_hash({"a": 1, "b": [1, 2]})
    right = content_hash({"b": [1, 2], "a": 1})
    assert left == right
    assert len(left) == 64
    # List order is content, not noise: reordering changes the hash.
    assert content_hash({"a": 1, "b": [2, 1]}) != left


# ---------------------------------------------------------------------------
# OA locations
# ---------------------------------------------------------------------------


def test_oa_location_round_trip():
    location = OALocation(
        url="https://arxiv.org/pdf/2103.00020",
        content_type="pdf",
        source="arxiv",
        version="submittedVersion",
    )
    assert OALocation.from_json_dict(location.to_json_dict()) == location
