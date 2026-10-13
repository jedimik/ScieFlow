"""scite public REST tier (no key, not counted against the MCP token).

- `tallies(dois)` — supporting / contrasting / mentioning counts for a whole
  reference list with bulk `POST /tallies` (body: a JSON array of DOIs), chunked
  at the policy's `rest_max_dois_per_request` (scite's limit is 500).
- `paper(doi)` — `GET /papers/{doi}`: title, year, journal, `retracted` and
  editorial notices.

Every request goes through `gateway.request` (policy → cache → ledger →
transport) with `source="rest"`, so it is refused when scite is off, ledgered
and announced with `integration.call`, and a second identical request is a free
cache hit. The public tier never sends the scite token — not even when one is
configured. Only DOI-shaped strings (`research.lib.doi.is_valid`) ever leave
the machine; anything else is reported `invalid` without a request.

Outcomes per DOI: `ok`, `unknown` (scite has no record: 404 / absent from the
bulk answer — cached like any answer), `invalid`, or `unavailable` with the
failure classification (`RATE`, `TRANSIENT`, `MALFORMED`, …; never cached).
`RATE` stops the remaining chunks: a refusal is a boundary, not a reason to
keep knocking. `paper()` raises `HTTPFailure` instead; the caller classifies.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from scieflow.integrations.scite import gateway
from scieflow.integrations.scite.cache import Cache
from scieflow.integrations.scite.ledger import Ledger
from scieflow.integrations.scite.policy import Policy
from scieflow.research.lib import doi as _doi
from scieflow.research.lib import http

API = "https://api.scite.ai"
_TALLY_FIELDS = (("supporting", "supporting"), ("contradicting", "contrasting"),
                 ("mentioning", "mentioning"), ("unclassified", "unclassified"),
                 ("total", "total"))


def _count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _tally(raw) -> dict | None:
    """scite TallyResponse → bundle-shaped counts (`contradicting` → `contrasting`)."""
    if not isinstance(raw, dict) or not all(_count(raw.get(k)) for k, _ in _TALLY_FIELDS):
        return None
    out = {new: raw[old] for old, new in _TALLY_FIELDS}
    cp = raw.get("citingPublications")
    out["citing_publications"] = cp if _count(cp) else None
    return out


def _str(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _paper(raw: dict, doi: str) -> dict:
    notices = []
    for n in raw.get("editorialNotices") or []:
        if isinstance(n, dict):
            nd = _str(n.get("noticeDoi"))
            notices.append({"status": _str(n.get("status")), "date": _str(n.get("date")),
                            "notice_doi": _doi.normalise(nd) if nd else None})
    year = raw.get("year")
    retracted = raw.get("retracted")
    return {
        "doi": doi,
        "title": _str(raw.get("title")),
        "year": year if isinstance(year, int) and not isinstance(year, bool) else None,
        "journal": _str(raw.get("journal")) or _str(raw.get("shortJournal")),
        "type": _str(raw.get("type")),
        "retracted": retracted if isinstance(retracted, bool) else None,
        "notices": notices,
        "abstract": _str(raw.get("abstract")),
    }


class PublicClient:
    def __init__(self, policy: Policy, cache: Cache, ledger: Ledger, *, base: str = API,
                 sleep=time.sleep, clock=time.monotonic, retries: int = 3,
                 max_retry_after: float = 60.0, session=None):
        self.policy = policy
        self.cache = cache
        self.ledger = ledger
        self.base = base.rstrip("/")
        self.sleep = sleep
        self.retries = retries
        self.max_retry_after = max_retry_after
        self.session = session
        self.limiter = http.RateLimiter(policy.rest_rate_per_s, clock=clock, sleep=sleep)

    # --- transport (only reached through the gateway) ---------------------------------

    def _send(self, method: str, url: str, **kw):
        # no Authorization header: the public tier never carries the token
        return http.send(method, url, retries=self.retries, max_retry_after=self.max_retry_after,
                         limiter=self.limiter, sleep=self.sleep, session=self.session, **kw)

    @staticmethod
    def _json(resp):
        try:
            return resp.json()
        except ValueError:
            raise http.HTTPFailure("response is not JSON", "MALFORMED", resp.status_code) from None

    def _transport(self, op: str, params: dict):
        if op == "tallies":
            resp = self._send("POST", f"{self.base}/tallies", json=list(params["dois"]),
                              passthrough=())
            body = self._json(resp)
            if not isinstance(body, dict) or not isinstance(body.get("tallies"), dict):
                raise http.HTTPFailure("tallies response has no 'tallies' mapping", "MALFORMED",
                                       resp.status_code)
            value = {}
            for k, raw in body["tallies"].items():
                t = _tally(raw)
                if isinstance(k, str) and t is not None:
                    value[_doi.normalise(k)] = t
            return value, len(value)
        if op == "paper":
            resp = self._send("GET", f"{self.base}/papers/{_doi.quote(params['doi'])}",
                              passthrough=(404,))
            if resp.status_code == 404:
                return None, 0
            body = self._json(resp)
            if not isinstance(body, dict):
                raise http.HTTPFailure("paper response is not an object", "MALFORMED",
                                       resp.status_code)
            return _paper(body, params["doi"]), 1
        raise ValueError(f"unknown public scite op {op!r}")

    # --- API ---------------------------------------------------------------------------

    def tallies(self, dois: Iterable[str], *, round: int | None = None) -> dict:  # noqa: A002
        out: dict[str, dict] = {}
        valid: set[str] = set()
        for d in dois:
            n = _doi.normalise(d) if isinstance(d, str) else d
            if isinstance(n, str) and _doi.is_valid(n):
                valid.add(n)
            else:
                out[d] = {"status": "invalid"}
        ordered = sorted(valid)
        size = self.policy.rest_max_dois_per_request
        chunks = [ordered[i:i + size] for i in range(0, len(ordered), size)]
        stopped: str | None = None
        for chunk in chunks:
            if stopped is not None:
                for d in chunk:
                    out[d] = {"status": "unavailable", "error": stopped}
                continue
            try:
                res = gateway.request(self.policy, self.cache, self.ledger, source="rest",
                                      op="tallies", params={"dois": chunk}, round=round,
                                      ttl_class="metadata", transport=self._transport)
            except http.HTTPFailure as exc:
                for d in chunk:
                    out[d] = {"status": "unavailable", "error": exc.classification}
                if exc.classification == "RATE":
                    stopped = "RATE"
                continue
            value = res.value if isinstance(res.value, dict) else {}
            for d in chunk:
                t = value.get(d)
                out[d] = {"status": "ok", **t} if isinstance(t, dict) else {"status": "unknown"}
        return out

    def paper(self, doi: str, *, round: int | None = None) -> dict:  # noqa: A002
        n = _doi.normalise(doi) if isinstance(doi, str) else None
        if n is None or not _doi.is_valid(n):
            return {"status": "invalid"}
        res = gateway.request(self.policy, self.cache, self.ledger, source="rest", op="paper",
                              params={"doi": n}, round=round, ttl_class="metadata",
                              transport=self._transport)
        if not isinstance(res.value, dict):
            return {"status": "unknown", "doi": n}
        return {"status": "ok", **res.value}
