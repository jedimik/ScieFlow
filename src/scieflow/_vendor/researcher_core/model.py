"""Canonical record model for researcher_core.

CSL-JSON is the canonical record format (D4). Every connector normalizes whatever an
API hands back into a :class:`CSLRecord`; BibTeX and RIS are emitters, never the model.

This module has no internal dependencies, so anything in the package may import it.
It also owns the deterministic canonicalization helpers (:func:`canonical_json`,
:func:`content_hash`) that the snapshot and cache layers hash with, because
determinism (D15) requires exactly one canonicalization routine in the codebase.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CSLDate",
    "CSLName",
    "CSLRecord",
    "OALocation",
    "canonical_json",
    "content_hash",
    "is_valid_doi",
    "normalize_author",
    "normalize_authors",
    "normalize_doi",
    "normalize_title",
    "parse_name",
    "sha256_hex",
    "title_fingerprint",
]


# ---------------------------------------------------------------------------
# Deterministic canonicalization and hashing
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Serialize ``value`` to the one canonical JSON form used for hashing.

    Deterministic by construction: keys sorted, fixed separators, no NaN or Infinity,
    Unicode kept as-is (not escaped). The same object always produces the same string,
    regardless of the insertion order of any dict inside it.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(data: str | bytes) -> str:
    """SHA-256 of ``data`` as a lowercase hex digest. Strings are hashed as UTF-8."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(value: Any) -> str:
    """SHA-256 hex digest of the canonicalized JSON form of ``value``."""
    return sha256_hex(canonical_json(value))


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi.org/",
    "dx.doi.org/",
    "doi:",
    "doi ",
)

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")

_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Lowercase name particles that belong to the family name, not the given name.
_PARTICLES = frozenset(
    {
        "af",
        "al",
        "bin",
        "da",
        "das",
        "de",
        "del",
        "della",
        "den",
        "der",
        "des",
        "di",
        "do",
        "dos",
        "du",
        "ibn",
        "la",
        "le",
        "les",
        "mac",
        "mc",
        "san",
        "st",
        "ten",
        "ter",
        "van",
        "vander",
        "von",
        "zu",
        "zur",
    }
)

_SUFFIXES = frozenset({"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"})


def normalize_doi(value: str | None) -> str:
    """Normalize a DOI to its bare, lowercased form.

    Strips any resolver prefix (``https://doi.org/``, ``http://dx.doi.org/``, ``doi:``),
    surrounding whitespace, angle brackets, and quotes, then lowercases. DOIs are
    case-insensitive by specification, so lowercasing is the canonical form and is what
    every DOI comparison in the kernel runs against.

    Returns an empty string for empty or None input. Does not validate; use
    :func:`is_valid_doi` for that.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value)).strip()
    text = text.strip("<>\"'").strip()
    changed = True
    while changed and text:
        changed = False
        lowered = text.lower()
        for prefix in _DOI_PREFIXES:
            if lowered.startswith(prefix):
                text = text[len(prefix) :].strip()
                changed = True
                break
    return text.strip().lower()


def is_valid_doi(value: str | None) -> bool:
    """True when ``value`` normalizes to something shaped like a DOI (``10.x/y``)."""
    return bool(_DOI_RE.match(normalize_doi(value)))


def normalize_title(value: str | None) -> str:
    """NFC-normalize a title and collapse every whitespace run to a single space.

    Case and punctuation are preserved: this is the display form. Use
    :func:`title_fingerprint` when comparing titles.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return _WHITESPACE_RE.sub(" ", text).strip()


def title_fingerprint(value: str | None) -> str:
    """Comparison form of a title: normalized, lowercased, punctuation removed.

    Used by dedupe and verify for similarity scoring so that "Deep Learning: A Review"
    and "Deep learning - a review" compare as the same string content.
    """
    text = normalize_title(value).casefold()
    text = _PUNCTUATION_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def parse_name(raw: str) -> CSLName:
    """Split a free-form personal name into CSL family / given / suffix parts.

    Handles the three shapes that bibliographic sources actually emit:

    * ``"Doe, John A."``            -> family="Doe", given="John A."
    * ``"Doe, Jr., John"``          -> family="Doe", given="John", suffix="Jr."
    * ``"John A. Doe"``             -> family="Doe", given="John A."
    * ``"Jan van der Berg"``        -> family="van der Berg", given="Jan"
    * ``"Plato"``                   -> family="Plato"

    Never raises: an unparseable string lands in ``family``.
    """
    text = normalize_title(raw)
    if not text:
        return CSLName()

    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        family = parts[0]
        given = ""
        suffix = ""
        rest = [p for p in parts[1:] if p]
        if len(rest) == 1:
            given = rest[0]
        elif len(rest) >= 2:
            # BibTeX "von Last, Jr, First" ordering.
            if rest[0].lower().rstrip(".") in {s.rstrip(".") for s in _SUFFIXES}:
                suffix = rest[0]
                given = " ".join(rest[1:])
            else:
                given = " ".join(rest)
        return CSLName(family=family, given=given, suffix=suffix)

    tokens = text.split(" ")
    if len(tokens) == 1:
        return CSLName(family=tokens[0])

    suffix = ""
    if tokens[-1].lower() in _SUFFIXES and len(tokens) > 2:
        suffix = tokens[-1]
        tokens = tokens[:-1]

    # Find the first particle token that is followed by at least one more token; from
    # there to the end is the family name ("van der Berg").
    family_start = len(tokens) - 1
    for index, token in enumerate(tokens[:-1]):
        if index > 0 and token.lower().strip(".") in _PARTICLES:
            family_start = index
            break

    given = " ".join(tokens[:family_start]).strip()
    family = " ".join(tokens[family_start:]).strip()
    return CSLName(family=family, given=given, suffix=suffix)


def normalize_author(value: Any) -> CSLName:
    """Coerce a string, mapping, or :class:`CSLName` into a :class:`CSLName`."""
    if isinstance(value, CSLName):
        return value
    if isinstance(value, Mapping):
        return CSLName.from_csl_json(value)
    return parse_name(str(value))


def normalize_authors(values: Iterable[Any] | None) -> list[CSLName]:
    """Coerce an iterable of names into a list of :class:`CSLName`, dropping empties."""
    if not values:
        return []
    out: list[CSLName] = []
    for value in values:
        name = normalize_author(value)
        if not name.is_empty():
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


@dataclass
class CSLName:
    """A CSL-JSON name variable (a person or, via ``literal``, an organization)."""

    family: str = ""
    given: str = ""
    suffix: str = ""
    literal: str = ""
    non_dropping_particle: str = ""
    dropping_particle: str = ""

    def __post_init__(self) -> None:
        self.family = normalize_title(self.family)
        self.given = normalize_title(self.given)
        self.suffix = normalize_title(self.suffix)
        self.literal = normalize_title(self.literal)
        self.non_dropping_particle = normalize_title(self.non_dropping_particle)
        self.dropping_particle = normalize_title(self.dropping_particle)

    def is_empty(self) -> bool:
        return not (self.family or self.given or self.literal)

    @property
    def surname(self) -> str:
        """The family name, or the literal when the name is not a person."""
        return self.family or self.literal

    def display(self) -> str:
        """``"Given Family, Suffix"`` for a person, or the literal for an organization."""
        if self.literal and not self.family:
            return self.literal
        parts = [p for p in (self.given, self.non_dropping_particle, self.family) if p]
        joined = " ".join(parts)
        if self.suffix:
            joined = f"{joined}, {self.suffix}" if joined else self.suffix
        return joined

    def to_csl_json(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.family:
            out["family"] = self.family
        if self.given:
            out["given"] = self.given
        if self.suffix:
            out["suffix"] = self.suffix
        if self.literal:
            out["literal"] = self.literal
        if self.non_dropping_particle:
            out["non-dropping-particle"] = self.non_dropping_particle
        if self.dropping_particle:
            out["dropping-particle"] = self.dropping_particle
        return out

    @classmethod
    def from_csl_json(cls, data: Mapping[str, Any]) -> CSLName:
        return cls(
            family=str(data.get("family") or ""),
            given=str(data.get("given") or ""),
            suffix=str(data.get("suffix") or ""),
            literal=str(data.get("literal") or ""),
            non_dropping_particle=str(data.get("non-dropping-particle") or ""),
            dropping_particle=str(data.get("dropping-particle") or ""),
        )

    @classmethod
    def parse(cls, raw: str) -> CSLName:
        return parse_name(raw)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?")


@dataclass
class CSLDate:
    """A CSL-JSON date variable, reduced to the parts bibliographic sources supply."""

    year: int | None = None
    month: int | None = None
    day: int | None = None
    raw: str = ""

    def is_empty(self) -> bool:
        return self.year is None and not self.raw

    def to_csl_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.year is not None:
            parts: list[int] = [self.year]
            if self.month is not None:
                parts.append(self.month)
                if self.day is not None:
                    parts.append(self.day)
            out["date-parts"] = [parts]
        if self.raw:
            out["raw"] = self.raw
        return out

    @classmethod
    def from_csl_json(cls, data: Any) -> CSLDate | None:
        """Accept ``{"date-parts": [[y, m, d]]}``, ``{"raw": "..."}``, an int, or a string."""
        if data is None or data == "":
            return None
        if isinstance(data, CSLDate):
            return data
        if isinstance(data, int):
            return cls(year=data)
        if isinstance(data, str):
            return cls.parse(data)
        if isinstance(data, Mapping):
            parts = data.get("date-parts")
            year = month = day = None
            if isinstance(parts, Sequence) and parts and isinstance(parts[0], Sequence):
                first = list(parts[0])
                values = [_as_int(v) for v in first[:3]]
                if values:
                    year = values[0]
                if len(values) > 1:
                    month = values[1]
                if len(values) > 2:
                    day = values[2]
            raw = str(data.get("raw") or data.get("literal") or "")
            if year is None and raw:
                parsed = cls.parse(raw)
                if parsed is not None:
                    return cls(year=parsed.year, month=parsed.month, day=parsed.day, raw=raw)
            if year is None and not raw:
                return None
            return cls(year=year, month=month, day=day, raw=raw)
        return None

    @classmethod
    def parse(cls, text: str) -> CSLDate | None:
        """Parse ``2020``, ``2020-05``, ``2020-05-17``, or ``2020/5/17``."""
        if not text:
            return None
        match = _DATE_RE.search(str(text))
        if not match:
            return cls(raw=str(text).strip())
        year = _as_int(match.group(1))
        month = _as_int(match.group(2))
        day = _as_int(match.group(3))
        return cls(year=year, month=month, day=day)

    @classmethod
    def from_year(cls, year: int | None) -> CSLDate | None:
        return None if year is None else cls(year=int(year))


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Open-access locations
# ---------------------------------------------------------------------------


@dataclass
class OALocation:
    """An open-access location for a work: what to fetch and where it came from.

    Returned by :meth:`BaseConnector.get_oa_pdf` and consumed by the OA cascade and
    ``fulltext.py``. ``content_type`` drives which extractor runs.
    """

    url: str = ""
    content_type: str = "pdf"  # "pdf" | "html" | "xml"
    source: str = ""  # connector that resolved it: unpaywall, arxiv, pubmed, ...
    version: str = ""  # submittedVersion | acceptedVersion | publishedVersion
    license: str = ""
    host_type: str = ""  # publisher | repository
    is_oa: bool = True

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "content_type": self.content_type,
            "source": self.source,
            "version": self.version,
            "license": self.license,
            "host_type": self.host_type,
            "is_oa": self.is_oa,
        }

    @classmethod
    def from_json_dict(cls, data: Mapping[str, Any]) -> OALocation:
        return cls(
            url=str(data.get("url") or ""),
            content_type=str(data.get("content_type") or "pdf"),
            source=str(data.get("source") or ""),
            version=str(data.get("version") or ""),
            license=str(data.get("license") or ""),
            host_type=str(data.get("host_type") or ""),
            is_oa=bool(data.get("is_oa", True)),
        )


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

# Standard CSL-JSON keys emitted at the top level of to_csl_json().
_STANDARD_KEYS = (
    "id",
    "type",
    "title",
    "author",
    "editor",
    "issued",
    "container-title",
    "publisher",
    "volume",
    "issue",
    "page",
    "number",
    "version",
    "abstract",
    "DOI",
    "URL",
    "ISSN",
    "ISBN",
    "language",
    "note",
    "keyword",
)

# Non-CSL extension fields. They are emitted under the "custom" object, which is the
# sanctioned CSL-JSON extension slot, so top-level output stays valid CSL-JSON.
_CUSTOM_KEYS = (
    "source",
    "source_id",
    "openalex_id",
    "arxiv_id",
    "pmid",
    "pmcid",
    "s2_id",
    "citation_count",
    "reference_count",
    "is_retracted",
    "is_oa",
    "oa_url",
)


@dataclass
class CSLRecord:
    """The canonical bibliographic record (CSL-JSON per D4).

    Every field is normalized on construction, so a record built by any connector has a
    lowercased bare DOI, an NFC-normalized whitespace-collapsed title, and structured
    author names. Connectors never need to remember to normalize.

    Non-CSL data (which index produced the record, citation counts, retraction flags,
    OA URL) lives in the extension fields listed in ``_CUSTOM_KEYS`` plus the free-form
    ``extra`` dict, and serializes under the CSL-JSON ``custom`` object.
    """

    id: str = ""
    type: str = "article-journal"
    title: str = ""
    author: list[CSLName] = field(default_factory=list)
    editor: list[CSLName] = field(default_factory=list)
    issued: CSLDate | None = None
    container_title: str = ""
    publisher: str = ""
    volume: str = ""
    issue: str = ""
    page: str = ""
    number: str = ""
    version: str = ""
    abstract: str = ""
    DOI: str = ""
    URL: str = ""
    ISSN: list[str] = field(default_factory=list)
    ISBN: str = ""
    language: str = ""
    note: str = ""
    keyword: list[str] = field(default_factory=list)

    # Extension fields (serialized under "custom").
    source: str = ""
    source_id: str = ""
    openalex_id: str = ""
    arxiv_id: str = ""
    pmid: str = ""
    pmcid: str = ""
    s2_id: str = ""
    citation_count: int | None = None
    reference_count: int | None = None
    is_retracted: bool | None = None
    is_oa: bool | None = None
    oa_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.DOI = normalize_doi(self.DOI)
        self.title = normalize_title(self.title)
        self.container_title = normalize_title(self.container_title)
        self.publisher = normalize_title(self.publisher)
        self.abstract = normalize_title(self.abstract)
        self.author = normalize_authors(self.author)
        self.editor = normalize_authors(self.editor)
        if self.issued is not None and not isinstance(self.issued, CSLDate):
            self.issued = CSLDate.from_csl_json(self.issued)
        if isinstance(self.ISSN, str):
            self.ISSN = [self.ISSN] if self.ISSN else []
        if isinstance(self.keyword, str):
            self.keyword = [self.keyword] if self.keyword else []
        if not self.id:
            self.id = self.default_id()

    # -- convenience -------------------------------------------------------

    def default_id(self) -> str:
        """A stable citation key derived from the record itself."""
        if self.DOI:
            return self.DOI
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        if self.openalex_id:
            return self.openalex_id
        if self.source_id:
            return f"{self.source}:{self.source_id}" if self.source else self.source_id
        return self.content_hash()[:16]

    @property
    def year(self) -> int | None:
        return self.issued.year if self.issued else None

    @property
    def first_author_surname(self) -> str:
        return self.author[0].surname if self.author else ""

    @property
    def title_key(self) -> str:
        """Comparison form of the title (see :func:`title_fingerprint`)."""
        return title_fingerprint(self.title)

    # -- serialization -----------------------------------------------------

    def to_csl_json(self) -> dict[str, Any]:
        """CSL-JSON dict. Empty fields are omitted, so output stays compact and stable."""
        out: dict[str, Any] = {"id": self.id, "type": self.type}
        if self.title:
            out["title"] = self.title
        if self.author:
            out["author"] = [a.to_csl_json() for a in self.author]
        if self.editor:
            out["editor"] = [e.to_csl_json() for e in self.editor]
        if self.issued is not None and not self.issued.is_empty():
            out["issued"] = self.issued.to_csl_json()
        if self.container_title:
            out["container-title"] = self.container_title
        for key in ("publisher", "volume", "issue", "page", "number", "version"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.abstract:
            out["abstract"] = self.abstract
        if self.DOI:
            out["DOI"] = self.DOI
        if self.URL:
            out["URL"] = self.URL
        if self.ISSN:
            out["ISSN"] = list(self.ISSN)
        if self.ISBN:
            out["ISBN"] = self.ISBN
        if self.language:
            out["language"] = self.language
        if self.note:
            out["note"] = self.note
        if self.keyword:
            out["keyword"] = list(self.keyword)

        custom: dict[str, Any] = {}
        for key in _CUSTOM_KEYS:
            value = getattr(self, key)
            if value not in ("", None):
                custom[key] = value
        for key, value in self.extra.items():
            if key not in custom:
                custom[key] = value
        if custom:
            out["custom"] = custom
        return out

    @classmethod
    def from_csl_json(cls, data: Mapping[str, Any]) -> CSLRecord:
        """Inverse of :meth:`to_csl_json`. Unknown custom keys land in ``extra``."""
        custom_raw = data.get("custom")
        custom: dict[str, Any] = dict(custom_raw) if isinstance(custom_raw, Mapping) else {}
        extra = {k: v for k, v in custom.items() if k not in _CUSTOM_KEYS}
        return cls(
            id=str(data.get("id") or ""),
            type=str(data.get("type") or "article-journal"),
            title=str(data.get("title") or ""),
            author=[normalize_author(a) for a in data.get("author") or []],
            editor=[normalize_author(e) for e in data.get("editor") or []],
            issued=CSLDate.from_csl_json(data.get("issued")),
            container_title=str(data.get("container-title") or ""),
            publisher=str(data.get("publisher") or ""),
            volume=str(data.get("volume") or ""),
            issue=str(data.get("issue") or ""),
            page=str(data.get("page") or ""),
            number=str(data.get("number") or ""),
            version=str(data.get("version") or ""),
            abstract=str(data.get("abstract") or ""),
            DOI=str(data.get("DOI") or data.get("doi") or ""),
            URL=str(data.get("URL") or data.get("url") or ""),
            ISSN=_as_str_list(data.get("ISSN")),
            ISBN=str(data.get("ISBN") or ""),
            language=str(data.get("language") or ""),
            note=str(data.get("note") or ""),
            keyword=_as_str_list(data.get("keyword")),
            source=str(custom.get("source") or ""),
            source_id=str(custom.get("source_id") or ""),
            openalex_id=str(custom.get("openalex_id") or ""),
            arxiv_id=str(custom.get("arxiv_id") or ""),
            pmid=str(custom.get("pmid") or ""),
            pmcid=str(custom.get("pmcid") or ""),
            s2_id=str(custom.get("s2_id") or ""),
            citation_count=_as_int(custom.get("citation_count")),
            reference_count=_as_int(custom.get("reference_count")),
            is_retracted=_as_bool(custom.get("is_retracted")),
            is_oa=_as_bool(custom.get("is_oa")),
            oa_url=str(custom.get("oa_url") or ""),
            extra=extra,
        )

    def canonical_json(self) -> str:
        """The canonical serialization of this record, and the input to :meth:`content_hash`."""
        return canonical_json(self.to_csl_json())

    def content_hash(self) -> str:
        """Stable SHA-256 of the record content. Independent of field insertion order."""
        return sha256_hex(canonical_json(self.to_csl_json()))


def _as_str_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)]


def _as_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return None
    return bool(value)
