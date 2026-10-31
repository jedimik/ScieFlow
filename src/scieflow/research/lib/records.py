"""Identity-source answers as CSLRecords (M-C2 t4, plan 5.4).

Reduced ports of researcher's OpenAlex, Crossref and DataCite `to_record` functions (MIT, credited
in CREDITS.md). Each keeps the fields the identity verdict reads: title, authors as CSLName, the
issued year, the DOI, the arXiv id, the CSL type and `extra.crossref_type` (the preprint test of
verify._is_preprint). The rest of each upstream record (abstract, references, OA locations, update
notices, subjects, URLs) is not read by verify-bib and is not ported. Pure functions: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from scieflow._vendor.researcher_core.model import (
    CSLDate,
    CSLName,
    CSLRecord,
    normalize_authors,
    normalize_doi,
    parse_name,
)

# The work fields researcher asks OpenAlex for (its WORK_FIELDS, joined). The fixture's recorded
# requests carry exactly this `select` string.
OPENALEX_SELECT = (
    "id,doi,ids,title,display_name,publication_year,publication_date,type,language,"
    "authorships,primary_location,best_oa_location,open_access,biblio,cited_by_count,"
    "cited_by_api_url,referenced_works,referenced_works_count,is_retracted,"
    "abstract_inverted_index"
)

_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?/]+?)(?:\.pdf)?$", re.I)
_ARXIV_DOI = "10.48550/arxiv."

_OPENALEX_TYPES = {
    "article": "article-journal",
    "journal-article": "article-journal",
    "book": "book",
    "book-chapter": "chapter",
    "dissertation": "thesis",
    "dataset": "dataset",
    "preprint": "article",
    "posted-content": "article",
    "proceedings-article": "paper-conference",
    "report": "report",
    "review": "review",
    "editorial": "article-journal",
}
_CROSSREF_TYPES = {
    "journal-article": "article-journal",
    "book": "book",
    "book-chapter": "chapter",
    "dissertation": "thesis",
    "dataset": "dataset",
    "posted-content": "article",
    "proceedings-article": "paper-conference",
    "report": "report",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _year_date(raw_date: Any, raw_year: Any) -> CSLDate | None:
    """The full date when it has a year, else the bare year."""
    date = CSLDate.parse(str(raw_date or "")) if raw_date else None
    if date is not None and date.year is not None:
        return date
    return CSLDate.from_year(_as_int(raw_year))


# --- OpenAlex ---------------------------------------------------------------------------------


def _openalex_arxiv_id(raw: Mapping[str, Any], doi: str) -> str:
    if doi.startswith(_ARXIV_DOI):
        return doi[len(_ARXIV_DOI):]
    for key in ("primary_location", "best_oa_location"):
        location = _mapping(raw.get(key))
        for url_key in ("pdf_url", "landing_page_url"):
            match = _ARXIV_URL.search(str(location.get(url_key) or ""))
            if match:
                return match.group(1)
    return ""


def openalex_record(raw: Mapping[str, Any]) -> CSLRecord:
    """One OpenAlex work (a `works` item, or a DOI lookup answer) as a CSLRecord."""
    ids = _mapping(raw.get("ids"))
    doi = normalize_doi(str(raw.get("doi") or ids.get("doi") or ""))
    venue = _mapping(_mapping(raw.get("primary_location")).get("source"))
    names: list[str] = []
    for entry in raw.get("authorships") or []:
        if not isinstance(entry, Mapping):
            continue
        display = str(_mapping(entry.get("author")).get("display_name") or "")
        display = display or str(entry.get("raw_author_name") or "")
        if display.strip():
            names.append(display.strip())
    return CSLRecord(
        type=_OPENALEX_TYPES.get(str(raw.get("type") or "").lower(), "article-journal"),
        title=str(raw.get("title") or raw.get("display_name") or ""),
        author=normalize_authors(names),
        issued=_year_date(raw.get("publication_date"), raw.get("publication_year")),
        container_title=str(venue.get("display_name") or ""),
        DOI=doi,
        source="openalex",
        source_id=str(raw.get("id") or "").rsplit("/", 1)[-1],
        arxiv_id=_openalex_arxiv_id(raw, doi),
    )


# --- Crossref ---------------------------------------------------------------------------------


def _first_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            if item:
                return str(item)
        return ""
    return str(value)


def _crossref_names(value: Any) -> list[CSLName]:
    out: list[CSLName] = []
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, Mapping):
            continue
        family = str(entry.get("family") or "")
        given = str(entry.get("given") or "")
        literal = str(entry.get("name") or "")
        suffix = str(entry.get("suffix") or "")
        if family or given or literal:
            out.append(CSLName(family=family, given=given, suffix=suffix, literal=literal))
    return out


def _crossref_issued(raw: Mapping[str, Any]) -> CSLDate | None:
    """The publication date. `created` is last: it is the DOI deposit date."""
    for key in ("issued", "published", "published-print", "published-online", "created"):
        candidate = CSLDate.from_csl_json(raw.get(key))
        if candidate is not None and candidate.year is not None:
            return candidate
    return None


def crossref_record(raw: Mapping[str, Any]) -> CSLRecord:
    """One Crossref work (a `works` item, or a DOI lookup `message`) as a CSLRecord."""
    doi = str(raw.get("DOI") or "")
    crossref_type = str(raw.get("type") or "")
    extra: dict[str, Any] = {}
    if crossref_type:
        extra["crossref_type"] = crossref_type
    return CSLRecord(
        type=_CROSSREF_TYPES.get(crossref_type, "article-journal"),
        title=_first_string(raw.get("title")),
        author=_crossref_names(raw.get("author")),
        issued=_crossref_issued(raw),
        container_title=_first_string(raw.get("container-title")),
        DOI=doi,
        source="crossref",
        source_id=normalize_doi(doi),
        extra=extra,
    )


# --- DataCite ---------------------------------------------------------------------------------


def _datacite_title(attrs: Mapping[str, Any]) -> str:
    titles = attrs.get("titles")
    if not isinstance(titles, Sequence) or isinstance(titles, (str, bytes)):
        return ""
    rows = [t for t in titles if isinstance(t, Mapping) and t.get("title")]
    for entry in rows:
        if not entry.get("titleType"):
            return str(entry["title"])
    return str(rows[0]["title"]) if rows else ""


def _datacite_names(raw: Any) -> list[CSLName]:
    """DataCite creators. A full display name copied into `familyName` (Zenodo) is split with
    the same parser BibTeX names use, so both sides of a comparison split the same way."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    out: list[CSLName] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "").strip()
        given = str(entry.get("givenName") or "").strip()
        family = str(entry.get("familyName") or "").strip()
        if str(entry.get("nameType") or "").strip().lower() == "organizational":
            literal = name or family
            if literal:
                out.append(CSLName(literal=literal))
        elif given:
            out.append(CSLName(given=given, family=family or name))
        elif family and family != name:
            out.append(CSLName(family=family))
        elif name or family:
            out.append(parse_name(name or family))
    return [n for n in out if not n.is_empty()]


def datacite_record(raw: Mapping[str, Any]) -> CSLRecord:
    """One DataCite JSON:API item (`data` item or DOI lookup `data`) as a CSLRecord."""
    attrs = _mapping(raw.get("attributes"))
    doi = normalize_doi(str(attrs.get("doi") or raw.get("id") or ""))
    types = _mapping(attrs.get("types"))
    general = str(types.get("resourceTypeGeneral") or "").strip().lower()
    return CSLRecord(
        type="dataset" if general == "dataset" else "article-journal",
        title=_datacite_title(attrs),
        author=_datacite_names(attrs.get("creators")),
        issued=CSLDate.from_year(_as_int(attrs.get("publicationYear"))),
        container_title=str(_mapping(attrs.get("container")).get("title") or ""),
        DOI=doi,
        source="datacite",
        source_id=doi,
    )


# --- Semantic Scholar and OpenCitations (snowball, M-C2 t6) -------------------------------------


def _s2_int(value: Any) -> int | None:
    return _as_int(value)


def semantic_scholar_record(raw: Mapping[str, Any]) -> CSLRecord:
    """One Semantic Scholar paper object (a citing or cited paper of an edge) as a CSLRecord.

    Keeps the fields the snowball graph reads: DOI (from `externalIds`), the Semantic Scholar
    paper id (the node key when there is no DOI), title, year, venue and citation count."""
    external = _mapping(raw.get("externalIds"))
    journal = _mapping(raw.get("journal"))
    venue_obj = _mapping(raw.get("publicationVenue"))
    venue = journal.get("name") or venue_obj.get("name") or raw.get("venue") or ""
    paper_id = str(raw.get("paperId") or "")
    return CSLRecord(
        type="article-journal",
        title=str(raw.get("title") or ""),
        issued=CSLDate.from_year(_s2_int(raw.get("year"))),
        container_title=str(venue),
        DOI=str(external.get("DOI") or ""),
        source="semantic-scholar",
        source_id=paper_id,
        s2_id=paper_id,
        citation_count=_s2_int(raw.get("citationCount")),
    )


def opencitations_record(doi: str) -> CSLRecord:
    """A DOI-only record for one OpenCitations edge end: the index holds no metadata (its answer
    is a list of DOI pairs), so only the DOI is set. Hydration is the identity sources' job."""
    return CSLRecord(type="article-journal", DOI=str(doi or ""), source="opencitations",
                     source_id=str(doi or ""))


def unpaywall_location(raw: Any) -> dict | None:
    """The open-access location of one Unpaywall `best_oa_location` object (M-C2 t7, plan 5.7).

    A reduced port of researcher's Unpaywall `to_oa_location` rule (MIT, CREDITS.md): the PDF link
    when there is one, else the landing page, else nothing. The URL is returned as data and is never
    requested. None when Unpaywall reports no location.
    """
    if not isinstance(raw, Mapping):
        return None
    url = str(raw.get("url_for_pdf") or raw.get("url") or raw.get("url_for_landing_page") or "")
    if not url:
        return None
    out: dict = {"oa_url": url}
    for key in ("host_type", "version", "license"):
        value = str(raw.get(key) or "")
        if value:
            out[key] = value
    return out
