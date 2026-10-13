"""Crossref metadata and retraction / correction status for cited DOIs (free, no key).

`check(doi)` reads `GET api.crossref.org/works/{doi}` once. Crossref lists the
notices that update a work in `updated-by` — the inverse of each notice's
`update-to` relation, merged from publisher deposits and Retraction Watch — so
one request covers both. A work that is itself a notice carries `update-to`
(reported as `is_notice` / `notice_of`).

Status (matching the evidence bundle's reference status):

- `retracted`   — an update of type retraction / withdrawal / removal, unless a
                  dated reinstatement follows it (then `corrected`);
- `corrected`   — correction, erratum, corrigendum, partial retraction,
                  expression of concern, addendum, clarification (advisory);
- `ok`          — no such update; also a DOI that resolves at doi.org but is
                  not registered with Crossref (`registered: "other"`, e.g.
                  DataCite) — Crossref simply has no update data for it;
- `unresolvable`— not DOI-shaped even after normalisation (`reason: "malformed
                  DOI"`, never sent anywhere) or Crossref 404 *and* doi.org
                  answers "handle not found" (responseCode 100).

Only an explicit "not found" makes a DOI unresolvable: network errors, 5xx,
rate limits and unexpected bodies raise `HTTPFailure` so a transient outage
never becomes a merge blocker. `check_many` maps those to `unavailable`.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from scieflow.research.lib import doi as _doi
from scieflow.research.lib import http

API = "https://api.crossref.org/works/"
HANDLE_API = "https://doi.org/api/handles/"

RETRACTING = frozenset({"retraction", "withdrawal", "removal"})
CORRECTING = frozenset({"correction", "erratum", "corrigendum", "partial_retraction",
                        "expression_of_concern", "addendum", "clarification"})

RATE_PER_S = 5          # polite default; Crossref's public pool allows more


def _type(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _date(update: dict) -> str | None:
    """`updated.date-parts` → YYYY[-MM[-DD]]."""
    try:
        parts = update["updated"]["date-parts"][0]
    except (KeyError, IndexError, TypeError):
        return None
    out = []
    for x in parts[:3] if isinstance(parts, list) else []:
        if not isinstance(x, int) or isinstance(x, bool):
            break
        out.append(f"{x:04d}" if not out else f"{x:02d}")
    return "-".join(out) or None


def _first(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


def _year(msg: dict) -> int | None:
    for k in ("published", "published-print", "published-online", "issued"):
        try:
            y = msg[k]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(y, int) and not isinstance(y, bool):
            return y
    return None


def _reinstated(updates: list[dict]) -> bool:
    """A dated reinstatement on or after every dated retraction. An undated
    reinstatement proves nothing (conservative: the retraction stands)."""
    retracted = [u["date"] for u in updates if u["type"] in RETRACTING]
    return any(u["type"] == "reinstatement" and u["date"] is not None
               and all(d is None or u["date"] >= d for d in retracted)
               for u in updates)


def classify(msg: dict) -> dict:
    """Status and updates from one Crossref work message (pure)."""
    updates = []
    for u in msg.get("updated-by") or []:
        if not isinstance(u, dict):
            continue
        t = _type(u.get("type"))
        if t is None:
            continue
        nd = u.get("DOI")
        updates.append({"type": t,
                        "notice_doi": _doi.normalise(nd) if isinstance(nd, str) else None,
                        "date": _date(u),
                        "source": u.get("source") if isinstance(u.get("source"), str) else None})
    kinds = {u["type"] for u in updates}
    status = ("retracted" if kinds & RETRACTING else
              "corrected" if kinds & CORRECTING else "ok")
    if status == "retracted" and _reinstated(updates):
        status = "corrected"     # retracted, then reinstated: advisory, not a blocker
    notice_of = []
    for u in msg.get("update-to") or []:
        if isinstance(u, dict) and _type(u.get("type")) and isinstance(u.get("DOI"), str):
            notice_of.append({"type": _type(u["type"]), "doi": _doi.normalise(u["DOI"])})
    return {"status": status, "updates": updates, "is_notice": bool(notice_of),
            "notice_of": notice_of}


def _json(resp, what: str):
    try:
        return resp.json()
    except ValueError:
        raise http.HTTPFailure(f"{what} response is not JSON", "MALFORMED",
                               resp.status_code) from None


def _registered_elsewhere(doi: str, send) -> bool:
    """True if doi.org resolves the DOI, False on an explicit 'handle not found'."""
    resp = send("GET", HANDLE_API + _doi.quote(doi), passthrough=(404,))
    body = _json(resp, "doi.org handle")
    code = body.get("responseCode") if isinstance(body, dict) else None
    if resp.status_code == 200 and code == 1:
        return True
    if code == 100:
        return False
    raise http.HTTPFailure("unexpected doi.org handle response", "MALFORMED", resp.status_code)


def _lookup(doi: str, send) -> dict:
    resp = send("GET", API + _doi.quote(doi), passthrough=(404,))
    if resp.status_code == 404:
        if _registered_elsewhere(doi, send):
            return {"doi": doi, "status": "ok", "registered": "other", "updates": [],
                    "is_notice": False, "notice_of": [], "title": None, "year": None,
                    "venue": None}
        return {"doi": doi, "status": "unresolvable", "registered": None,
                "reason": "not registered (Crossref 404, doi.org: handle not found)"}
    body = _json(resp, "Crossref")
    msg = body.get("message") if isinstance(body, dict) else None
    if not isinstance(msg, dict):
        raise http.HTTPFailure("Crossref response has no message", "MALFORMED", resp.status_code)
    return {"doi": doi, "registered": "crossref", **classify(msg),
            "title": _first(msg.get("title")), "year": _year(msg),
            "venue": _first(msg.get("container-title"))}


def check(doi, *, cache=None, limiter: http.RateLimiter | None = None, sleep=time.sleep,
          retries: int = 3, session=None) -> dict:
    n = _doi.normalise(doi) if isinstance(doi, str) else None
    if not n or not _doi.is_valid(n):
        return {"doi": n or None, "status": "unresolvable", "registered": None,
                "reason": "malformed DOI"}

    def send(method, url, **kw):
        return http.send(method, url, retries=retries, limiter=lim, sleep=sleep,
                         session=session, **kw)

    lim = limiter or http.RateLimiter(RATE_PER_S, sleep=sleep)

    return http.cached(cache, "metadata", {"source": "crossref", "op": "work", "doi": n},
                       lambda: _lookup(n, send))


def check_many(dois: Iterable, **kw) -> dict:
    """`check` for each distinct DOI, keyed by normalised DOI (input order). A
    failure is recorded as `unavailable` with its classification."""
    kw.setdefault("limiter", http.RateLimiter(RATE_PER_S, sleep=kw.get("sleep", time.sleep)))
    out: dict = {}
    for d in dois:
        key = _doi.normalise(d) if isinstance(d, str) else d
        if key in out:
            continue
        try:
            out[key] = check(d, **kw)
        except http.HTTPFailure as exc:
            out[key] = {"doi": key, "status": "unavailable", "error": exc.classification}
    return out
