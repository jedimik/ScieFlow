"""ORCID, ROR and CRediT validator tests.

Ported from researcher v1.0.0 (MIT), core/tests/test_export.py lines 446-566 (the ORCID, ROR and
CRediT sections only). Imports are rewritten to scieflow._vendor.researcher_core.contributors;
test bodies are unchanged. See VENDORED.md.
"""

from __future__ import annotations

import pytest

from scieflow._vendor.researcher_core.contributors import (
    CREDIT_ROLES,
    MetadataError,
    validate_credit_role,
    validate_orcid,
    validate_ror,
)


# ---------------------------------------------------------------------------
# ORCID iD validation (ISO 7064 mod 11-2 checksum)  (M5.5)
# ---------------------------------------------------------------------------

# Real, checksum-valid ORCID iDs (the X check character is exercised too).
VALID_ORCIDS = [
    "0000-0002-1825-0097",  # the canonical ORCID example
    "0000-0001-5109-3700",
    "0000-0002-1694-233X",  # check character X (== 10)
]


@pytest.mark.parametrize("orcid", VALID_ORCIDS)
def test_validate_orcid_accepts_and_canonicalizes(orcid: str) -> None:
    assert validate_orcid(orcid) == f"https://orcid.org/{orcid}"


def test_validate_orcid_accepts_url_and_compact_forms() -> None:
    assert validate_orcid("https://orcid.org/0000-0002-1825-0097") == (
        "https://orcid.org/0000-0002-1825-0097"
    )
    assert validate_orcid("orcid.org/0000-0002-1825-0097") == (
        "https://orcid.org/0000-0002-1825-0097"
    )
    # Compact 16-character form, no hyphens.
    assert validate_orcid("0000000218250097") == "https://orcid.org/0000-0002-1825-0097"


def test_validate_orcid_rejects_a_bad_checksum() -> None:
    # Last digit flipped from 7 to 8: valid shape, wrong ISO 7064 mod 11-2 check character.
    with pytest.raises(MetadataError, match="checksum"):
        validate_orcid("0000-0002-1825-0098")


def test_validate_orcid_rejects_wrong_length_and_non_digits() -> None:
    with pytest.raises(MetadataError, match="16 characters"):
        validate_orcid("0000-0002-1825")
    with pytest.raises(MetadataError, match="digits"):
        validate_orcid("0000-0002-1825-00A7")


def test_validate_orcid_is_a_value_error() -> None:
    # MetadataError subclasses ValueError, so existing handlers still catch it.
    assert issubclass(MetadataError, ValueError)
    with pytest.raises(ValueError):
        validate_orcid("not-an-orcid")


# ---------------------------------------------------------------------------
# ROR ID validation (published prefix/character pattern)  (M5.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ror",
    [
        "042nb2s44",  # MIT
        "03vek6s52",  # Harvard
        "https://ror.org/00f54p054",  # Stanford, full URL
        "ror.org/013meh722",  # bare host prefix
    ],
)
def test_validate_ror_accepts_and_canonicalizes(ror: str) -> None:
    result = validate_ror(ror)
    assert result.startswith("https://ror.org/0")
    # Idempotent: validating the canonical form returns it unchanged.
    assert validate_ror(result) == result


def test_validate_ror_rejects_bad_patterns() -> None:
    # Missing leading 0.
    with pytest.raises(MetadataError, match="ROR"):
        validate_ror("142nb2s44")
    # Contains an excluded look-alike character (i, l, o, u are not in the alphabet).
    with pytest.raises(MetadataError, match="ROR"):
        validate_ror("0i2nb2s44")
    # Wrong length.
    with pytest.raises(MetadataError, match="ROR"):
        validate_ror("042nb2s4")
    # A raw grant/other string is not a ROR.
    with pytest.raises(MetadataError, match="ROR"):
        validate_ror("University of Nowhere")


# ---------------------------------------------------------------------------
# CRediT role validation (fixed 14-term taxonomy)  (M5.5)
# ---------------------------------------------------------------------------


def test_credit_taxonomy_has_the_14_terms() -> None:
    assert len(CREDIT_ROLES) == 14
    assert "conceptualization" in CREDIT_ROLES
    assert "writing - original draft" in CREDIT_ROLES
    assert "writing - review and editing" in CREDIT_ROLES


@pytest.mark.parametrize("role", CREDIT_ROLES)
def test_every_canonical_role_validates_to_itself(role: str) -> None:
    assert validate_credit_role(role) == role


def test_validate_credit_role_is_case_and_hyphen_tolerant() -> None:
    assert validate_credit_role("Data Curation") == "data curation"
    assert validate_credit_role("data-curation") == "data curation"
    assert validate_credit_role("FORMAL ANALYSIS") == "formal analysis"
    assert validate_credit_role("writing-original-draft") == "writing - original draft"


def test_validate_credit_role_rejects_unknown_roles() -> None:
    with pytest.raises(MetadataError, match="CRediT"):
        validate_credit_role("proofreading")
    with pytest.raises(MetadataError, match="CRediT"):
        validate_credit_role("writing")


def test_no_credit_role_uses_an_en_or_em_dash() -> None:
    # The CI dash guard bans U+2013/U+2014; the taxonomy uses ordinary hyphen-minus only.
    # The dash characters are written as escapes here so this file itself stays dash-free.
    joined = " ".join(CREDIT_ROLES)
    assert chr(0x2013) not in joined  # en dash
    assert chr(0x2014) not in joined  # em dash
