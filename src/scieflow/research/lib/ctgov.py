"""ClinicalTrials.gov v2: completed trials without a linked publication (free, no key).

A trial that finished years ago and never published is a gap of its own: a
question someone answered (or tried to) without the literature hearing back.

- `search_completed(gw, condition)` — `GET /api/v2/studies` for one condition
  (and optionally an intervention), `filter.overallStatus=COMPLETED`, paged
  with `pageToken` until `totalCount` or `max_pages`. Only the condition text
  leaves the machine (leak-checked).
- `normalise(study)` — NCT id, title, status, completion date, registry
  results flag, study type, phases, linked publications and the number of
  background references.
- `unpublished(studies, as_of=…)` — pure and deterministic: completed trials
  with no linked publication, completed at least `grace_months` before
  `as_of`, oldest first. A *linked publication* is a registry reference of
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


def search_completed(gw, condition: str, *, intervention: str | None = None,
                     page_size: int = 100, max_pages: int = 5) -> list[dict]:
    params = {"query.cond": condition, "filter.overallStatus": "COMPLETED",
              "pageSize": int(page_size), "countTotal": "true", "fields": FIELDS}
    if intervention is not None:
        params["query.intr"] = intervention
    out: list[dict] = []
    total = None
    token = None
    for _ in range(max(1, int(max_pages))):
        page = dict(params, **({"pageToken": token} if token else {}))
        body = gw.get_json(PROVIDER, "studies", API, page, text=("query.cond", "query.intr"),
                           ttl_class="search", count=_count)
        studies = body.get("studies") if isinstance(body, dict) else None
        if not isinstance(studies, list):
            raise http.HTTPFailure("ClinicalTrials.gov response has no studies", "MALFORMED")
        if total is None and isinstance(body.get("totalCount"), int):
            total = body["totalCount"]
        out.extend(normalise(s) for s in studies if isinstance(s, dict))
        token = body.get("nextPageToken")
        if not studies or not isinstance(token, str) or not _TOKEN.match(token) \
                or (total is not None and len(out) >= total):
            break
    seen: set = set()
    return [s for s in out if s["nct_id"] and not (s["nct_id"] in seen or seen.add(s["nct_id"]))]


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


def _ym(value) -> tuple[int, int] | None:
    m = _DATE.match(value) if isinstance(value, str) else None
    if not m:
        return None
    month = int(m.group(2) or 12)        # a bare year counts from its end (conservative)
    return (int(m.group(1)), month) if 1 <= month <= 12 else None


def unpublished(studies, *, as_of: date, grace_months: int = 24) -> list[dict]:
    out = []
    for s in studies:
        if (s.get("status") or "COMPLETED") != "COMPLETED" or s.get("publications"):
            continue
        ym = _ym(s.get("completion_date"))
        if ym is None:
            continue
        months = (as_of.year - ym[0]) * 12 + (as_of.month - ym[1])
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
