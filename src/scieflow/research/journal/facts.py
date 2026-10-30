"""Deterministic journal facts (M-F plan 5.3): the journal's identity, its publisher, open-access
flags and the reference / abstract / title norms of a recent works sample.

Requests go through the run's Gateway (`search-openalex`, `search-crossref`): the policy, the
leak check, the cache and the ledger apply. Every fact here is a recorded answer; ScieFlow
never guesses a journal and never resolves a conflict between sources (the conflict is
recorded for a human).
"""

from __future__ import annotations

import json
import re
import statistics
import unicodedata
from datetime import date, timedelta
from pathlib import Path

from scieflow.research.lib import http
from scieflow.research.lib.providers import ProviderRefused
from scieflow.research.search import registry

PROVIDER = "search-openalex"
CROSSREF = "search-crossref"
SOURCES_URL = "https://api.openalex.org/sources"
WORKS_URL = "https://api.openalex.org/works"
CROSSREF_URL = "https://api.crossref.org/journals/{issn}"
WORKS_WINDOW_DAYS = 730
WORKS_SELECT = "id,publication_year,referenced_works_count,abstract_inverted_index,display_name"
SOURCE_ID = re.compile(r"^S\d{1,12}$")
ISSN_SHAPE = re.compile(r"^\d{4}-\d{3}[\dX]$")
FILE = "facts.json"


class FactsError(Exception):
    """A journal that cannot be identified or a lookup that failed: exit 1, nothing written."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def issn_ok(value) -> bool:
    """An ISSN of the form 1234-5678 whose ISO 3297 check digit is right."""
    if not isinstance(value, str) or not ISSN_SHAPE.match(value):
        return False
    digits = value.replace("-", "")
    total = sum(int(c) * (8 - i) for i, c in enumerate(digits[:7]))
    check = (11 - total % 11) % 11
    return digits[7] == ("X" if check == 10 else str(check))


def normalise(text) -> str:
    """Case, accents, punctuation and spacing folded away: the comparison used for names and
    publishers."""
    if not isinstance(text, str):
        return ""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[\W_]+", " ", ascii_text.casefold()).split())


def _malformed(provider: str, op: str):
    def check(body) -> None:
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise http.HTTPFailure(f"{provider} {op} response has the wrong shape", "MALFORMED")
    return check


def _results_count(body) -> int:
    return len(body.get("results", [])) if isinstance(body, dict) else 0


def _sources(gw, *, ids=None, checked=None, per_page: int, ttl: str) -> list[dict]:
    body = gw.get_json(PROVIDER, "journal-search" if checked else "journal-source", SOURCES_URL,
                       ids=ids, checked=checked, fixed={"per-page": per_page}, ttl_class=ttl,
                       count=_results_count, validate=_sources_shape)
    rows = [r for r in body["results"] if isinstance(r, dict)]
    gw.remember(PROVIDER, [str(r.get("id", "")).rsplit("/", 1)[-1] for r in rows])
    return rows


def _sources_shape(body) -> None:
    """A sources answer whose every row is an object with a string id and, when present, an ISSN
    list of strings. Anything else is MALFORMED: a clean exit 1, never a traceback (M-F 5.3)."""
    _malformed(PROVIDER, "sources")(body)
    for row in body["results"]:
        ok = isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"] != ""
        issn = row.get("issn") if ok else None
        if ok and issn is not None:
            ok = isinstance(issn, list) and all(isinstance(x, str) for x in issn)
        if not ok:
            raise http.HTTPFailure(f"{PROVIDER} sources response has a row without a string id "
                                   "or a bad ISSN list", "MALFORMED")


def _source_row(row: dict) -> dict:
    return {
        "id": str(row["id"]).rsplit("/", 1)[-1],
        "display_name": row.get("display_name"),
        "issn_l": row.get("issn_l"),
        "issn": sorted(row.get("issn") or []),
        "host_organization_name": row.get("host_organization_name"),
        "homepage_url": row.get("homepage_url"),
        "is_oa": row.get("is_oa"),
        "is_in_doaj": row.get("is_in_doaj"),
        "apc_usd": row.get("apc_usd"),
        "works_count": row.get("works_count"),
        "type": row.get("type"),
    }


def _candidate_line(row: dict) -> str:
    return (f"{row.get('display_name')} | ISSN-L {row.get('issn_l') or '-'} | publisher "
            f"{row.get('host_organization_name') or '-'} | works {row.get('works_count')}")


def select_source(gw, *, name: str | None, issn: str | None) -> dict:
    """The one OpenAlex source for this journal. With an ISSN: the one source listing it. With
    a name: the one source whose normalised name equals the normalised name. ScieFlow never
    guesses: anything else is exit 1 with the candidates listed."""
    if issn is not None:
        if not issn_ok(issn):
            raise FactsError(f"not a valid ISSN (the check digit is wrong or the shape is): {issn}")
        rows = _sources(gw, ids={"filter": f"issn:{issn}"}, per_page=5, ttl="metadata")
        found = [r for r in rows if issn in (r.get("issn") or [])]
        if not found:
            raise FactsError(f"not-found: no OpenAlex source lists ISSN {issn}")
        if len(found) > 1:
            lines = "; ".join(_candidate_line(r) for r in found[:10])
            raise FactsError(f"ambiguous: {len(found)} OpenAlex sources list ISSN {issn}: {lines}")
        return _source_row(found[0])
    if not name:
        raise FactsError("a journal name or an ISSN is required")
    checked = registry.check_query(name)            # LeakError before anything is sent
    rows = _sources(gw, checked={"search": checked}, per_page=10, ttl="search")
    wanted = normalise(name)
    exact = [r for r in rows if normalise(r.get("display_name")) == wanted]
    if len(exact) == 1:
        return _source_row(exact[0])
    shown = (exact or rows)[:10]
    lines = "; ".join(_candidate_line(r) for r in shown) or "no candidates"
    kind = "ambiguous" if exact else "not-found"
    raise FactsError(f"{kind}: {len(exact)} OpenAlex source(s) named {name!r}: {lines}; "
                     "re-run with --issn")


def window(as_of: date) -> tuple[date, date]:
    """The works window of the sample: two years up to `as_of` (M-F 5.3)."""
    return as_of - timedelta(days=WORKS_WINDOW_DAYS), as_of


def works_sample(gw, source_id: str, as_of: date) -> list[dict]:
    """The journal's recent articles (two years to `as_of`, at most 200). The source id must be
    one OpenAlex returned in this run; any other id is refused before the request."""
    if not SOURCE_ID.match(source_id or "") or not gw.was_received(PROVIDER, source_id):
        raise ProviderRefused(f"OpenAlex did not return source {source_id!r}: the works filter "
                              "is refused before any request")
    start, end = window(as_of)
    flt = (f"primary_location.source.id:{source_id},from_publication_date:{start.isoformat()},"
           f"to_publication_date:{end.isoformat()},type:article")
    body = gw.get_json(PROVIDER, "journal-works", WORKS_URL, ids={"filter": flt},
                       fixed={"per-page": 200, "select": WORKS_SELECT,
                              "sort": "publication_date:desc"},
                       ttl_class="metadata", count=_results_count,
                       validate=_malformed(PROVIDER, "works"))
    return [w for w in body["results"] if isinstance(w, dict)]


def _num(value):
    value = round(float(value), 4)
    return int(value) if value.is_integer() else value


def _quartiles(values: list) -> dict:
    if not values:
        return {"median": None, "p25": None, "p75": None}
    if len(values) == 1:
        only = _num(values[0])
        return {"median": only, "p25": only, "p75": only}
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"median": _num(statistics.median(values)), "p25": _num(q1), "p75": _num(q3)}


def norms(works: list[dict], start: date, end: date) -> dict:
    """Median and quartiles over the sample (n <= 200). Quartiles of one value are that value;
    no works give nulls. The window the sample was drawn from is recorded with it."""
    refs = [w["referenced_works_count"] for w in works
            if isinstance(w.get("referenced_works_count"), int)
            and not isinstance(w.get("referenced_works_count"), bool)]
    abstracts = [w["abstract_inverted_index"] for w in works
                 if isinstance(w.get("abstract_inverted_index"), dict)
                 and w["abstract_inverted_index"]]
    abstract_words = [sum(len(pos) for pos in index.values()
                          if isinstance(pos, list)) for index in abstracts]
    title_words = [len(str(w.get("display_name") or "").split()) for w in works]
    share = round(len(abstracts) / len(works), 4) if works else None
    return {
        "sample": {"n": len(works), "from_date": start.isoformat(), "to_date": end.isoformat()},
        "references": _quartiles(refs),
        "abstract_words": {**_quartiles(abstract_words), "share_with_abstract": share},
        "title_words": {"median": _num(statistics.median(title_words)) if title_words else None},
    }


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _crossref(gw, issn: str) -> tuple[dict | None, str]:
    """(the Crossref journal record or None, status). A failure is recorded, not fatal."""
    if not issn_ok(issn):
        return None, "invalid-issn"
    try:
        body = gw.get_json(CROSSREF, "journal", CROSSREF_URL.format(issn=issn),
                           ttl_class="metadata", count=lambda _b: 1,
                           validate=_crossref_shape)
    except http.HTTPFailure as exc:
        return None, exc.classification
    message = body["message"]
    return ({"title": _first(message.get("title")), "publisher": message.get("publisher"),
             "ISSN": sorted(message.get("ISSN") or [])}, "ok")


def _crossref_shape(body) -> None:
    if not isinstance(body, dict) or not isinstance(body.get("message"), dict):
        raise http.HTTPFailure("crossref journal response has the wrong shape", "MALFORMED")


def collect(gw, *, name: str | None, issn: str | None, as_of: date) -> dict:
    """The whole facts block for one journal. Raises FactsError (exit 1) when the journal
    cannot be identified or the source lookup fails; LeakError / ProviderRefused / NetworkMiss
    propagate to the CLI."""
    if name is None and issn is None:
        raise FactsError("a journal name or an ISSN is required")
    try:
        source = select_source(gw, name=name, issn=issn)
    except http.HTTPFailure as exc:
        raise FactsError(f"OpenAlex source lookup failed ({exc.classification})") from None
    status: dict[str, str] = {}
    crossref, status["crossref"] = _crossref(gw, source.get("issn_l") or "")
    conflicts = []
    if crossref is not None:
        for field, ours, theirs in (("publisher", source.get("host_organization_name"),
                                     crossref.get("publisher")),
                                    ("title", source.get("display_name"), crossref.get("title"))):
            if normalise(ours) and normalise(theirs) and normalise(ours) != normalise(theirs):
                conflicts.append({"field": field, "openalex": ours, "crossref": theirs})
    try:
        sample = works_sample(gw, source["id"], as_of)
        block = norms(sample, *window(as_of))
        status["works"] = "ok"
    except http.HTTPFailure as exc:
        block, status["works"] = None, exc.classification
    return {
        "version": 1,
        "as_of": as_of.isoformat(),
        "query": {"name": name, "issn": issn},
        "openalex": source,
        "crossref": crossref,
        "norms": block,
        "conflicts": conflicts,
        "provider_status": status,
    }


def dumps(data: dict) -> str:
    return json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n"


def facts_path(journal_dir: Path) -> Path:
    return Path(journal_dir) / FILE
