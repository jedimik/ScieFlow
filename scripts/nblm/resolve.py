"""Resolve DOIs to open-access PDF URLs via Europe PMC — the sources.yml builder.

`cmd_fetch` consumes sources.yml but nothing built it: the research module's four search
scripts all send their argument as a free-text keyword query and cannot look a
known DOI up (search_openalex.py:42, search_crossref.py:45, search_arxiv.py:45).
Crossref returns DOI landing pages, never PDFs; OpenAlex returns arbitrary hosts
the allowlist rejects. Europe PMC is the one route that answers "given this DOI,
where is the open-access PDF".

Never invents a URL: a DOI with no usable open-access PDF is reported with a
reason, not guessed at.
"""

import json
import time
import urllib.parse
import urllib.request

from nblm import policy

SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
TIMEOUT = 30
USER_AGENT = "ScieFlow-claim-check/0.1 (+https://github.com/jedimik/ScieFlow)"
POLITE_DELAY = 0.34          # Europe PMC asks for <= 3 requests/second


class ResolveError(Exception):
    """The Europe PMC lookup itself failed (exit 1)."""


def query_url(doi: str) -> str:
    params = urllib.parse.urlencode({
        "query": f'DOI:"{doi}"',
        "resultType": "core",
        "format": "json",
        "pageSize": 1,
    })
    return f"{SEARCH}?{params}"


def _default_opener(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def _allowed_hosts(profile: policy.Profile) -> set:
    return {h.lower() for h in profile.source_hosts}


def _normalize(url: str, profile: policy.Profile) -> tuple[str, bool]:
    """Upgrade http -> https for an allowlisted host. Never changes the host."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" and host in _allowed_hosts(profile):
        return urllib.parse.urlunsplit(("https", *parts[1:])), True
    return url, False


def pick_pdf_url(result: dict, profile: policy.Profile) -> tuple[str | None, bool, str]:
    """Choose an allowlisted PDF url. Returns (url, upgraded, reason_if_none).

    Prefers an allowlisted host rather than taking the last entry — the flaw in
    src/scieflow/research/search/europepmc.py:11-14, whose loop has no
    break and so silently keeps whichever PDF came last, often the publisher's
    own paywalled domain.
    """
    entries = ((result.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
    pdfs = [e for e in entries if (e.get("documentStyle") or "").lower() == "pdf"]
    if not pdfs:
        return None, False, ("no PDF url in Europe PMC full-text list"
                             if entries else "no open-access full text")

    allowed = _allowed_hosts(profile)
    candidates = []
    for entry in pdfs:
        url = (entry.get("url") or "").strip()
        if not url:
            continue
        normalized, upgraded = _normalize(url, profile)
        host = (urllib.parse.urlsplit(normalized).hostname or "").lower()
        free = (entry.get("availability") or "").lower().startswith(("free", "open"))
        candidates.append((host in allowed, free, normalized, upgraded))

    for in_allowlist, _free, url, upgraded in sorted(
            candidates, key=lambda c: (not c[0], not c[1])):
        if in_allowlist:
            return url, upgraded, ""
    hosts = ", ".join(sorted({urllib.parse.urlsplit(c[2]).hostname or "?"
                              for c in candidates})) or "none"
    return None, False, f"PDF only on non-allowlisted host(s): {hosts}"


def resolve_one(profile: policy.Profile, doi: str, opener=None) -> dict:
    """One DOI -> {doi, url, title, source} or {doi, reason}."""
    url = query_url(doi)
    policy.check_source_url(profile, url)          # the API host is allowlisted too
    open_url = opener or _default_opener
    try:
        with open_url(url) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ResolveError(f"Europe PMC lookup failed for {doi}: {exc}") from exc

    results = ((payload.get("resultList") or {}).get("result") or [])
    if not results:
        return {"doi": doi, "reason": "not found in Europe PMC"}
    result = results[0]
    pdf_url, upgraded, reason = pick_pdf_url(result, profile)
    if not pdf_url:
        return {"doi": doi, "reason": reason}
    row = {
        "doi": doi,
        "url": pdf_url,
        "title": (result.get("title") or "").strip(),
        "source": "europepmc",
    }
    if upgraded:
        row["scheme_upgraded"] = True
    return row


def resolve_all(profile: policy.Profile, dois, opener=None, sleep=time.sleep) -> dict:
    """Resolve many DOIs, preserving order and reporting every failure."""
    sources, unresolved = [], []
    for index, doi in enumerate(dois):
        if index:
            sleep(POLITE_DELAY)
        try:
            policy.check_doi(doi)
        except policy.PolicyError as exc:
            unresolved.append({"doi": doi, "reason": str(exc)})
            continue
        row = resolve_one(profile, doi, opener=opener)
        (unresolved if "reason" in row else sources).append(row)
    return {"sources": sources, "unresolved": unresolved}
