"""ClinicalTrials.gov v2: completed trials without a linked publication (free, no key).

A trial that finished years ago and never published is a gap of its own: a
question someone answered (or tried to) without the literature hearing back.

- `search_completed(gw, condition)` — `GET /api/v2/studies` for one condition
  (and optionally an intervention), `filter.overallStatus=COMPLETED`, paged
  with `pageToken` until `totalCount` or `max_pages`; `complete` says whether
  the list is whole. Only the condition text leaves the machine (leak-checked).
- `normalise(study)` — NCT id, title, status, completion date, registry
  results flag, study type, phases, linked publications and the number of
  background references.
- `unpublished(studies, as_of=…)` — pure and deterministic: completed trials
  with no linked publication, completed at least `grace_months` whole months
  before `as_of` (a bare month or year counts from its last day), oldest first. A *linked publication* is a registry reference of
  type RESULT (entered by the sponsor) or DERIVED (matched by PubMed on the
  NCT id); BACKGROUND references cite earlier work and do not count. Results
  posted on the registry (`has_results`) are reported but are not a paper.

Limitations: the registry's links are incomplete (a paper that never names
the NCT id is missed), so each candidate is a lead to verify, not a finding.
"""

from __future__ import annotations

import re
from datetime import date

from scieflow.research.lib import http

API = "https://clinicaltrials.gov/api/v2/studies"
PROVIDER = "ctgov"
FIELDS = ("NCTId,BriefTitle,OverallStatus,CompletionDate,HasResults,ReferencesModule,"
          "StudyType,Phase")
LINKED = frozenset({"RESULT", "DERIVED"})
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,200}\Z")
_DATE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?\Z")


def _count(value) -> int:
    studies = value.get("studies") if isinstance(value, dict) else None
    return len(studies) if isinstance(studies, list) else 0


def _is_page(value) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("studies"), list):
        raise http.HTTPFailure("ClinicalTrials.gov response has no studies", "MALFORMED")


def search_completed(gw, condition: str, *, intervention: str | None = None,
                     page_size: int = 100, max_pages: int = 5) -> dict:
    """`{"studies": [...], "total": int | None, "complete": bool}` — `complete` is
    False when `max_pages` (or an unusable page token) stopped the paging before
    `totalCount` was reached: the caller then holds a partial list."""
    fixed = {"filter.overallStatus": "COMPLETED", "pageSize": int(page_size),
             "countTotal": "true", "fields": FIELDS}
    text = {"query.cond": condition, "query.intr": intervention}
    out: list[dict] = []
    total = None
    token = None
    pages = 0
    for _ in range(max(1, int(max_pages))):
        body = gw.get_json(PROVIDER, "studies", API, text=text,
                           ids={"pageToken": token} if token else None, fixed=fixed,
                           ttl_class="search", count=_count, validate=_is_page)
        pages += 1
        studies = body["studies"]
        if total is None and isinstance(body.get("totalCount"), int):
            total = body["totalCount"]
        out.extend(normalise(s) for s in studies if isinstance(s, dict))
        token = body.get("nextPageToken")
        if not isinstance(token, str) or not _TOKEN.match(token):
            token = None
        if not studies or token is None or (total is not None and len(out) >= total):
            break
    seen: set = set()
    studies = [s for s in out if s["nct_id"] and not (s["nct_id"] in seen or seen.add(s["nct_id"]))]
    complete = (len(out) >= total) if total is not None else token is None
    return {"studies": studies, "total": total, "complete": bool(complete), "pages": pages}


def normalise(study: dict) -> dict:
    p = study.get("protocolSection") or {}
    ident = p.get("identificationModule") or {}
    status = p.get("statusModule") or {}
    design = p.get("designModule") or {}
    refs = (p.get("referencesModule") or {}).get("references") or []
    pubs, background = [], 0
    for r in refs:
        if not isinstance(r, dict):
            continue
        kind = str(r.get("type") or "").upper()
        if kind in LINKED:
            pub = {"type": kind}
            if isinstance(r.get("pmid"), str):
                pub["pmid"] = r["pmid"]
            pubs.append(pub)
        elif kind == "BACKGROUND":
            background += 1
    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "status": status.get("overallStatus"),
        "completion_date": (status.get("completionDateStruct") or {}).get("date"),
        "has_results": bool(study.get("hasResults")),
        "study_type": design.get("studyType"),
        "phases": list(design.get("phases") or []),
        "publications": pubs,
        "background_references": background,
    }


def _end(value) -> date | None:
    """The completion date; a bare month or year counts from its last day
    (conservative: never flags a trial early)."""
    import calendar

    m = _DATE.match(value) if isinstance(value, str) else None
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2) or 12)
    if not 1 <= mo <= 12:
        return None
    last = calendar.monthrange(y, mo)[1]
    d = int(m.group(3)) if m.group(3) else last
    return date(y, mo, d) if 1 <= d <= last else None


def months_between(start: date, end: date) -> int:
    """Whole calendar months from `start` to `end` (negative if `end` is earlier)."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    return months - 1 if end.day < start.day else months


def unpublished(studies, *, as_of: date, grace_months: int = 24) -> list[dict]:
    out = []
    for s in studies:
        if (s.get("status") or "COMPLETED") != "COMPLETED" or s.get("publications"):
            continue
        done = _end(s.get("completion_date"))
        if done is None:
            continue
        months = months_between(done, as_of)
        if months < grace_months:
            continue
        out.append({"nct_id": s.get("nct_id"), "title": s.get("title"),
                    "completion_date": s.get("completion_date"),
                    "months_since_completion": months,
                    "has_results": bool(s.get("has_results")),
                    "study_type": s.get("study_type"), "phases": list(s.get("phases") or []),
                    "background_references": int(s.get("background_references") or 0)})
    out.sort(key=lambda g: (-g["months_since_completion"], g["completion_date"], g["nct_id"] or ""))
    return out
