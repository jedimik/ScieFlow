"""Identity connectors for `verify-bib` (M-C2 t4, plan 5.4).

Three connectors (OpenAlex, Crossref, DataCite), each a subclass of the vendored verify.py's
BaseConnector, bound to its search catalogue entry (`search-openalex`, `search-crossref`,
`search-datacite`; M-C2-D3). The requests are the ones researcher's connectors send (the endpoints
and parameters are the ones in the gold fixture), but every request goes through the provider
Gateway: policy, data check, leak check, cache, rate limit, transport (no redirects), ledger.

The async methods only wrap the synchronous Gateway (no awaits inside), so asyncio.gather runs the
sources one after another in source order: deterministic.

Failures: a clean "no such record" (404/410) is None; a failed request (rate limit, server error,
malformed body) is a SourceError whose message is the classification only; a title the search leak
check refuses is not sent and is a SourceError of kind config, and so is an identifier that is not a
plain DOI or arXiv id (path-shaped, spaced, a query or fragment): it is not sent, and no source has
answered, so it is never a negative. NetworkMiss, ProviderRefused and
SnapshotCorrupt are not caught here: the command maps them to its exit codes.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar
from urllib.parse import quote

from scieflow._vendor.researcher_core import query as _vquery
from scieflow._vendor.researcher_core import verify
from scieflow._vendor.researcher_core._deps import BaseConnector, SourceError, SourceErrorKind
from scieflow.core.review.claims import LeakError, safe_doi
from scieflow.research.lib import http, records
from scieflow.research.search import registry

OPENALEX_API = "https://api.openalex.org"
CROSSREF_API = "https://api.crossref.org"
DATACITE_API = "https://api.datacite.org"
ABSENT = (404, 410)               # "no such record": a clean negative, cached and replayable
_ARXIV_ID = re.compile(r"^(?:arxiv[:/])?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)$",
                       re.I)

#: source name -> provider (catalogue) name, M-C2-D3
PROVIDER = {"openalex": "search-openalex", "crossref": "search-crossref",
            "datacite": "search-datacite"}
SOURCES = tuple(PROVIDER)

_KIND = {
    "RATE": SourceErrorKind.RATE_LIMIT,
    "TRANSIENT": SourceErrorKind.SERVER_ERROR,
    "MALFORMED": SourceErrorKind.BAD_RESPONSE,
}
_URL_MARKERS = re.compile(r"[?#]")


def _doi_key(value) -> str | None:
    """The DOI that may go into a request URL, or None: nothing is sent (M-C2 5.4, 5.9).

    claims.safe_doi refuses path-shaped, spaced and traversal text. It does not refuse a query or
    fragment marker, which would start a part of the URL the Gateway never checked, so those are
    refused here too."""
    key = safe_doi(value)
    if key is None or _URL_MARKERS.search(key):
        return None
    return key


def _expect_object(body: Any) -> None:
    if not isinstance(body, Mapping):
        raise http.HTTPFailure("response has an unexpected shape", "MALFORMED")


def _count(body: Any) -> int:
    return len(body.get("results") or []) if isinstance(body, Mapping) else 0


class _Identity(BaseConnector):
    """Shared plumbing: the Gateway calls, the failure mapping and the search leak check."""

    name: ClassVar[str] = ""
    provider: ClassVar[str] = ""
    capabilities: ClassVar[frozenset[str]] = frozenset({"search", "get_by_id", "resolve_doi"})

    def __init__(self, gateway) -> None:
        self.gateway = gateway

    def _get(self, op: str, url: str, *, absent: tuple[int, ...] = (), **kwargs: Any) -> Any:
        """`absent` lists the statuses that mean "no such record". Only a DOI lookup passes it: a
        404 from a search endpoint is an anomaly and raises (a SourceError), never a negative."""
        try:
            return self.gateway.get_json(self.provider, op, url, absent=absent,
                                         validate=_expect_object, **kwargs)
        except http.HTTPFailure as exc:
            raise self._failure(exc) from None

    def _failure(self, exc: http.HTTPFailure) -> SourceError:
        kind = _KIND.get(exc.classification, SourceErrorKind.BAD_RESPONSE)
        return SourceError(self.name, f"request failed: {exc.classification}", kind=kind,
                           status_code=exc.status)

    def _not_sent(self) -> SourceError:
        """An identifier that is not sent is not a clean negative: no source answered (like a
        refused title, M-C2 t4 review r1 finding 1)."""
        return SourceError(self.name, "identifier not sent: it is not a plain DOI or arXiv id",
                           kind=SourceErrorKind.CONFIG)

    def _checked_title(self, query: str):
        text = _vquery.sanitize_query(query)
        if not text:
            return None
        try:
            return registry.check_query(text)
        except LeakError:
            raise SourceError(self.name, "title not sent: it failed the leak check",
                              kind=SourceErrorKind.CONFIG) from None

    async def aclose(self) -> None:
        return None


class OpenAlexIdentity(_Identity):
    name: ClassVar[str] = "openalex"
    provider: ClassVar[str] = PROVIDER["openalex"]

    async def resolve_doi(self, doi: str):
        key = _doi_key(doi)
        if key is None:
            raise self._not_sent()
        body = self._get("works-doi", f"{OPENALEX_API}/works/doi:{key}", absent=ABSENT,
                         fixed={"select": records.OPENALEX_SELECT})
        return None if body is None else records.openalex_record(body)

    async def get_by_id(self, identifier: str):
        text = str(identifier or "").strip()
        if text.startswith("10."):
            return await self.resolve_doi(text)
        match = _ARXIV_ID.match(text)
        if match is None:
            raise self._not_sent()
        return await self.resolve_doi(f"10.48550/arxiv.{match.group(1).lower()}")

    async def search(self, query: str, *, limit: int = 5, since: str | None = None):
        # `since` is part of the connector interface; verify-bib never passes it.
        checked = self._checked_title(query)
        if checked is None:
            return []
        size = max(1, min(int(limit), 200))
        body = self._get("search", f"{OPENALEX_API}/works", checked={"search": checked},
                         fixed={"per-page": size, "select": records.OPENALEX_SELECT},
                         count=_count)
        if not isinstance(body, Mapping) or not isinstance(body.get("results"), list):
            return []
        return [records.openalex_record(r) for r in body["results"][:size]
                if isinstance(r, Mapping)]


class CrossrefIdentity(_Identity):
    name: ClassVar[str] = "crossref"
    provider: ClassVar[str] = PROVIDER["crossref"]

    async def resolve_doi(self, doi: str):
        key = _doi_key(doi)
        if key is None:
            raise self._not_sent()
        return await self._work(key)

    async def get_by_id(self, identifier: str):
        # Researcher sends an arXiv id as a DOI path (M-C2 5.4). Only a strictly arXiv-shaped id is
        # sent, so the path cannot carry anything else; any other text is not sent.
        text = str(identifier or "").strip()
        if text.startswith("10."):
            return await self.resolve_doi(text)
        match = _ARXIV_ID.match(text)
        if match is None:
            raise self._not_sent()
        return await self._work(match.group(1).lower())

    async def _work(self, key: str):
        body = self._get("works-doi", f"{CROSSREF_API}/works/{quote(key, safe='/')}", absent=ABSENT)
        message = body.get("message") if isinstance(body, Mapping) else None
        return records.crossref_record(message) if isinstance(message, Mapping) else None

    async def search(self, query: str, *, limit: int = 5, since: str | None = None):
        # `since` is part of the connector interface; verify-bib never passes it.
        checked = self._checked_title(query)
        if checked is None:
            return []
        rows = max(1, min(int(limit), 100))
        body = self._get("search", f"{CROSSREF_API}/works",
                         checked={"query.bibliographic": checked}, fixed={"rows": rows})
        message = body.get("message") if isinstance(body, Mapping) else None
        items = message.get("items") if isinstance(message, Mapping) else None
        if not isinstance(items, list):
            return []
        return [records.crossref_record(i) for i in items[:rows] if isinstance(i, Mapping)]


class DataCiteIdentity(_Identity):
    name: ClassVar[str] = "datacite"
    provider: ClassVar[str] = PROVIDER["datacite"]

    async def resolve_doi(self, doi: str):
        key = _doi_key(doi)
        if key is None:
            raise self._not_sent()
        return await self._work(key)

    async def get_by_id(self, identifier: str):
        # As for Crossref: only a strictly arXiv-shaped id is sent (M-C2 5.4); other text is not sent.
        text = str(identifier or "").strip()
        if text.startswith("10."):
            return await self.resolve_doi(text)
        match = _ARXIV_ID.match(text)
        if match is None:
            raise self._not_sent()
        return await self._work(match.group(1).lower())

    async def _work(self, key: str):
        body = self._get("dois-doi", f"{DATACITE_API}/dois/{key}", absent=ABSENT)
        data = body.get("data") if isinstance(body, Mapping) else None
        return records.datacite_record(data) if isinstance(data, Mapping) else None

    async def search(self, query: str, *, limit: int = 5, since: str | None = None):
        # `since` is part of the connector interface; verify-bib never passes it.
        checked = self._checked_title(query)
        if checked is None:
            return []
        size = max(1, min(int(limit), 100))
        body = self._get("search", f"{DATACITE_API}/dois",
                         composed={"query": registry.datacite_query(checked)},
                         fixed={"page[size]": size})
        data = body.get("data") if isinstance(body, Mapping) else None
        if not isinstance(data, list):
            return []
        return [records.datacite_record(i) for i in data[:size] if isinstance(i, Mapping)]


CONNECTORS = {"openalex": OpenAlexIdentity, "crossref": CrossrefIdentity,
              "datacite": DataCiteIdentity}


def build_connectors(gateway, names: Sequence[str]) -> list[_Identity]:
    """The connectors for these source names, in the order given."""
    unknown = [n for n in names if n not in CONNECTORS]
    if unknown:
        raise ValueError(f"unknown identity source: {unknown[0]}")
    return [CONNECTORS[n](gateway) for n in names]


def check_bib(claims: Sequence[verify.ReferenceClaim], gateway, names: Sequence[str], *,
              input_path: str, run_id: str) -> dict:
    """Verify every claim against the named sources; the vendored axis (a) report.

    No `generated_at` is written, so a replay of the same snapshots is byte-identical (D15).
    Axis (b) (status) and axis (d) (accessibility) are not asked (M-C2 5.4)."""
    connectors = build_connectors(gateway, names)
    return asyncio.run(verify.verify_claims_async(
        claims, connectors, check_status=False, check_accessibility=False,
        input_kind="bib", input_path=input_path, run_id=run_id))
