"""ORCID, ROR and CRediT validators: a verbatim excerpt of researcher v1.0.0 core/researcher_core/export.py,
lines 809-1000 (MIT; see VENDORED.md). Nothing else from export.py is vendored."""

import re


# ---------------------------------------------------------------------------
# Contributor metadata: ORCID, ROR, CRediT (M5.5)
# ---------------------------------------------------------------------------
#
# Three OPTIONAL identifiers/vocabularies attach to a contributor: an ORCID iD (the person),
# a ROR ID (their institution), and one or more CRediT roles (what they did). All three are
# NEVER fabricated. An unknown or malformed value is REJECTED here with an actionable message
# so the caller can fix or drop it; the kernel never guesses a plausible-looking identifier.
# ORCID is checksum-verified (ISO 7064 mod 11-2), ROR is verified against its published
# character/prefix pattern, and a CRediT role must be one of the fixed 14-term taxonomy.


class MetadataError(ValueError):
    """A supplied contributor identifier or role is malformed or not in the taxonomy.

    A subclass of :class:`ValueError`, so existing ``except ValueError`` handlers still catch
    it, but distinct enough that a caller can single out "bad metadata the user supplied" from
    other value errors when it wants to.
    """


# --- ORCID iD -------------------------------------------------------------

_ORCID_PREFIXES = (
    "https://orcid.org/",
    "http://orcid.org/",
    "orcid.org/",
    "orcid:",
)


def _orcid_check_digit(first_15: str) -> str:
    """The ISO 7064 mod 11-2 check character for the first 15 digits of an ORCID.

    The last character of a valid ORCID is this digit, or ``X`` when it works out to 10.
    """
    total = 0
    for char in first_15:
        total = (total + int(char)) * 2
    remainder = total % 11
    result = (12 - remainder) % 11
    return "X" if result == 10 else str(result)


def validate_orcid(value: str) -> str:
    """Validate an ORCID iD and return it in canonical ``https://orcid.org/XXXX-...`` form.

    Accepts a bare ``0000-0002-1825-0097``, the compact 16-character form, or any
    ``orcid.org`` URL. The 16 characters are 15 digits plus an ISO 7064 mod 11-2 check
    character (a digit or ``X``); a value that fails the checksum is rejected, never
    "corrected" to a nearby valid iD. Raises :class:`MetadataError` on any malformed input.
    """
    raw = str(value).strip()
    lowered = raw.lower()
    for prefix in _ORCID_PREFIXES:
        if lowered.startswith(prefix):
            raw = raw[len(prefix) :].strip()
            break
    compact = raw.replace("-", "").replace(" ", "").upper()
    if len(compact) != 16:
        raise MetadataError(
            f"invalid ORCID {value!r}: an ORCID has 16 characters (got {len(compact)}), "
            "written as four groups of four, for example 0000-0002-1825-0097"
        )
    body, check = compact[:15], compact[15]
    if not body.isdigit():
        raise MetadataError(
            f"invalid ORCID {value!r}: the first 15 characters must all be digits"
        )
    if check not in "0123456789X":
        raise MetadataError(
            f"invalid ORCID {value!r}: the check character must be a digit or X, got {check!r}"
        )
    expected = _orcid_check_digit(body)
    if check != expected:
        raise MetadataError(
            f"invalid ORCID {value!r}: fails the ISO 7064 mod 11-2 checksum "
            f"(check character should be {expected!r}, not {check!r})"
        )
    formatted = "-".join(compact[i : i + 4] for i in range(0, 16, 4))
    return f"https://orcid.org/{formatted}"


# --- ROR ID ---------------------------------------------------------------

_ROR_PREFIXES = (
    "https://ror.org/",
    "http://ror.org/",
    "ror.org/",
)

# A ROR ID is the string that follows ror.org/: a leading 0, then six Crockford base32
# characters (the alphabet 0-9 a-z with i, l, o, and u removed to avoid look-alikes), then a
# two-digit checksum. This is ROR's published identifier pattern.
_ROR_RE = re.compile(r"^0[0-9a-hj-km-np-tv-z]{6}[0-9]{2}$")


def validate_ror(value: str) -> str:
    """Validate a ROR ID and return it in canonical ``https://ror.org/0...`` form.

    Accepts a bare ``042nb2s44`` or any ``ror.org`` URL. The identifier must match ROR's
    published pattern (a leading ``0``, six base32 characters, two check digits); anything else
    is rejected, never guessed. Raises :class:`MetadataError` on malformed input.
    """
    raw = str(value).strip()
    lowered = raw.lower()
    for prefix in _ROR_PREFIXES:
        if lowered.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    ident = raw.strip("/").lower()
    if not _ROR_RE.match(ident):
        raise MetadataError(
            f"invalid ROR ID {value!r}: expected https://ror.org/ followed by a 0, six base32 "
            "characters, and two check digits, for example https://ror.org/042nb2s44"
        )
    return f"https://ror.org/{ident}"


# --- CRediT roles ---------------------------------------------------------

#: The CRediT (Contributor Roles Taxonomy) 14 terms, in the canonical spelling. Hyphens here
#: are ordinary hyphen-minus characters, never en/em dashes.
CREDIT_ROLES: tuple[str, ...] = (
    "conceptualization",
    "data curation",
    "formal analysis",
    "funding acquisition",
    "investigation",
    "methodology",
    "project administration",
    "resources",
    "software",
    "supervision",
    "validation",
    "visualization",
    "writing - original draft",
    "writing - review and editing",
)

#: canonical role -> (display term, NISO contributor-role slug). The slug builds the
#: ``vocab-term-identifier`` URI (https://credit.niso.org/contributor-roles/<slug>/).
_CREDIT_META: dict[str, tuple[str, str]] = {
    "conceptualization": ("Conceptualization", "conceptualization"),
    "data curation": ("Data curation", "data-curation"),
    "formal analysis": ("Formal analysis", "formal-analysis"),
    "funding acquisition": ("Funding acquisition", "funding-acquisition"),
    "investigation": ("Investigation", "investigation"),
    "methodology": ("Methodology", "methodology"),
    "project administration": ("Project administration", "project-administration"),
    "resources": ("Resources", "resources"),
    "software": ("Software", "software"),
    "supervision": ("Supervision", "supervision"),
    "validation": ("Validation", "validation"),
    "visualization": ("Visualization", "visualization"),
    "writing - original draft": ("Writing - original draft", "writing-original-draft"),
    "writing - review and editing": ("Writing - review and editing", "writing-review-editing"),
}


def _credit_key(value: str) -> str:
    """Normalize a role string for matching: lowercase, hyphens to spaces, whitespace collapsed.

    This makes ``"Data Curation"``, ``"data-curation"``, and ``"data curation"`` all match, so a
    caller is not forced to reproduce the exact canonical spacing.
    """
    text = str(value).strip().lower().replace("-", " ")
    return " ".join(text.split())


#: normalized form -> canonical role. Includes the slug spellings so both
#: ``"writing - original draft"`` and ``"writing-original-draft"`` resolve.
_CREDIT_LOOKUP: dict[str, str] = {}
for _canonical, (_display, _slug) in _CREDIT_META.items():
    _CREDIT_LOOKUP[_credit_key(_canonical)] = _canonical
    _CREDIT_LOOKUP[_credit_key(_slug)] = _canonical


def validate_credit_role(value: str) -> str:
    """Validate a CRediT role and return its canonical spelling (one of :data:`CREDIT_ROLES`).

    Matching is case-insensitive and tolerant of hyphen-vs-space, so ``"Formal Analysis"`` and
    ``"formal-analysis"`` both resolve to ``"formal analysis"``. A role outside the 14-term
    taxonomy is rejected, never approximated. Raises :class:`MetadataError` on an unknown role.
    """
    canonical = _CREDIT_LOOKUP.get(_credit_key(value))
    if canonical is None:
        raise MetadataError(
            f"unknown CRediT role {value!r}: not one of the 14 CRediT terms "
            f"({', '.join(CREDIT_ROLES)})"
        )
    return canonical
