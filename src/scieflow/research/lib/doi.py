"""DOI normalisation and validation shared by the evidence sources.

A DOI is only ever sent to an external service (scite, Crossref, doi.org,
Europe PMC) after `is_valid()`: the same shape the review schemas accept
(`10.<4-9 digits>/<no whitespace>`, at most 300 characters, no `..` path
segment, no control characters), and it goes into URL paths through `quote()`.
"""

from __future__ import annotations

import re
from urllib.parse import quote as _quote

# doi:… / DOI … / urn:doi:… / [http(s)://][www.][dx.]doi.org/…
_PREFIX = re.compile(r"^(?:urn:)?(?:doi[:\s]\s*|(?:https?://)?(?:www\.)?(?:dx\.)?doi\.org/)",
                     re.IGNORECASE)
_SHAPE = re.compile(r"^10\.[0-9]{4,9}/\S+\Z")
_DOTDOT = re.compile(r"(^|/)\.\.(/|$)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_LEN = 300


def normalise(doi) -> str:
    """Lower-cased, without `doi:` / `DOI ` / `https://(www.|dx.)doi.org/` prefixes or
    outer whitespace."""
    return _PREFIX.sub("", str(doi).strip()).strip().lower()


def is_valid(doi) -> bool:
    if not isinstance(doi, str) or len(doi) > MAX_LEN:
        return False
    return bool(_SHAPE.match(doi)) and not _DOTDOT.search(doi) and not _CONTROL.search(doi)


def quote(doi: str) -> str:
    """Percent-encode a DOI for a URL path, keeping `/` and parentheses."""
    return _quote(doi, safe="/()")
