"""Guideline sources of one journal run (M-F plan 5.4): candidates, the human-only gate, fetching
and saved pages.

Authority: a fetch happens only when the run's `journal-sources` gate was answered `approve` by a
human and its question carries the sha12 of the current candidates.json. Pages are data: they are
stored as `.raw` (as received) and `.txt` (plain text, fenced by `sanitize.fence_text`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from scieflow.core import gates
from scieflow.core.project import Project
from scieflow.research.journal import facts, html, state
from scieflow.research.lib import http, providers

PROVIDER = "journal-guidelines"
GATE_KIND = "journal-sources"
OPTIONS = ("approve", "deny")
MAX_URLS = 10
MAX_CHARS = 2_000_000
MAX_SAVED_BYTES = 5 * 1024 * 1024
SAVED_SUFFIXES = (".html", ".htm", ".txt", ".md")
CANDIDATES = "candidates.json"
SOURCES = "sources.json"


class SourcesError(state.StateError):
    """A refusal: a bad URL, a gate that does not authorise a fetch, a file that fails a check."""


@dataclass
class Result:
    exit_code: int
    phase: str
    lines: list[str] = field(default_factory=list)
    gate_id: str | None = None
    counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"phase": self.phase, "gate": self.gate_id, "counts": self.counts,
                "lines": self.lines}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def https_url(url: str) -> str:
    """An https URL with a lowercase host and no fragment; anything else is refused (M-F 5.4)."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        raise SourcesError(f"not a valid URL: {url!r}") from None
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise SourcesError(f"only https URLs are accepted: {url!r}")
    if parts.username or parts.password or port not in (None, 443):
        raise SourcesError(f"URL with user information or a non-default port refused: {url!r}")
    return urlunsplit(("https", parts.hostname.lower(), parts.path, parts.query, ""))


def candidates_for(homepage: str | None, extra: list[str]) -> list[str]:
    """The OpenAlex homepage (when https) and the human's --url list, deduplicated in order."""
    if len(extra) > MAX_URLS:
        raise SourcesError(f"at most {MAX_URLS} --url values are accepted")
    urls: list[str] = []
    if homepage:
        try:
            urls.append(https_url(homepage))
        except SourcesError:
            pass                                       # an http homepage is not a candidate
    for url in extra:
        urls.append(https_url(url))
    return list(dict.fromkeys(urls))


def sha_of(urls: list[str]) -> str:
    return hashlib.sha256(("\n".join(urls) + "\n").encode("utf-8")).hexdigest()


def source_id(url: str) -> str:
    return "s" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]


def hosts_of(urls: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(urlsplit(u).hostname or "" for u in urls))


def question(journal: str, run: str, urls: list[str], sha: str) -> str:
    return (f"journal-sources {sha[:12]}: download these {len(urls)} author-guideline page(s) "
            f"for {journal} into run {run}? Hosts: {', '.join(hosts_of(urls))}. "
            f"URLs: {' | '.join(urls)}")


def write_candidates(jdir: Path, journal: str, urls: list[str]) -> dict:
    data = {"version": 1, "journal": journal, "urls": urls, "sha256": sha_of(urls)}
    state.write_text(jdir / CANDIDATES, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return data


def load_candidates(jdir: Path) -> dict | None:
    data = state.read_json(jdir / CANDIDATES)
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("urls"), list) \
            or sha_of(data["urls"]) != data.get("sha256"):
        raise SourcesError("candidates.json does not match its own hash; it was changed by hand. "
                           "Restore it, or re-run with --restart to rebuild it")
    return data


def decision(gate: dict, sha: str) -> str:
    """'open', 'approve' or 'deny' for the run's journal-sources gate. Anything else is refused:
    only a human's answer, to the current candidate list, authorises a fetch."""
    if gate.get("kind") != GATE_KIND or gate.get("requires_human") is not True:
        raise SourcesError("the gate is not a human-only journal-sources gate")
    if f"journal-sources {sha[:12]}:" not in str(gate.get("question", "")):
        raise SourcesError("the gate was opened for another candidate list (hash differs from "
                           "candidates.json); no page was fetched")
    if gate.get("state") == "open":
        return "open"
    if gate.get("state") != "answered":
        raise SourcesError(f"the journal-sources gate is {gate.get('state')}; re-run to open a "
                           "new one")
    if gate.get("answered_by") != "human":
        raise SourcesError("the journal-sources gate was not answered by a human; no page was "
                           "fetched")
    if gate.get("answer") not in OPTIONS:
        raise SourcesError(f"the journal-sources gate has an unknown answer {gate.get('answer')!r}")
    return str(gate["answer"])


def _load_rows(jdir: Path, sha: str | None) -> dict[str, dict]:
    data = state.read_json(jdir / SOURCES)
    if not isinstance(data, dict) or data.get("candidates_sha256") != sha:
        return {}
    return {r["id"]: r for r in data.get("rows", []) if isinstance(r, dict) and "id" in r}


def _write_rows(jdir: Path, journal: str, sha: str | None, rows: list[dict]) -> None:
    data = {"version": 1, "journal": journal, "candidates_sha256": sha, "rows": rows}
    state.write_text(jdir / SOURCES, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _store(jdir: Path, sid: str, raw: bytes, text: str) -> None:
    folder = jdir / "sources"
    state.atomic_write(folder / f"{sid}.raw", raw)
    state.atomic_write(folder / f"{sid}.txt", text.encode("utf-8"))


def fetch_one(gw, jdir: Path, url: str) -> dict:
    """One approved page: exactly one GET through the Gateway; a failure is a row, not a stop."""
    sid = source_id(url)
    row = {"id": sid, "url": url, "host": urlsplit(url).hostname, "status": "failed",
           "classification": None, "retrieved_at": _now(), "retrieved_by": "gateway",
           "raw_sha256": None, "text_sha256": None, "chars": 0}
    try:
        body = gw.get_text(PROVIDER, "page", url, count=lambda _b: 1, ttl_class="metadata")
    except http.HTTPFailure as exc:
        row["classification"] = exc.classification
        return row
    if len(body) > MAX_CHARS:
        row["status"] = "too-large"
        return row
    raw = body.encode("utf-8")
    text = html.to_text(body)
    _store(jdir, sid, raw, text)
    row.update(status="ok", raw_sha256=hashlib.sha256(raw).hexdigest(),
               text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), chars=len(text))
    return row


def _counts(rows: list[dict]) -> dict:
    out = {"ok": 0, "failed": 0, "too-large": 0}
    for row in rows:
        out[row["status"]] = out.get(row["status"], 0) + 1
    return out


def _restart(jdir: Path) -> None:
    state.remove_working(jdir)


def research(project: Project, run: Path, *, journal: str, slug: str, issn: str | None,
             extra_urls: list[str], as_of: dt.date, restart: bool, retry_failed: bool,
             policy: providers.ProviderPolicy, make_gateway) -> Result:
    """Facts, then the candidate list, then the gate, then (after a human approval) the fetch.
    Re-entrant: every call picks up from the phase the folder records. Refusals raise."""
    jdir = state.journal_dir(run)
    if restart:
        _restart(jdir)
    candidates_for(None, extra_urls)                          # validates every --url first
    gw = make_gateway()

    query = {"name": journal, "issn": issn}
    facts_file = facts.facts_path(jdir)
    existing = state.read_json(facts_file)
    if isinstance(existing, dict) and existing.get("query") == query \
            and existing.get("as_of") == as_of.isoformat():
        data = existing
    else:
        data = facts.collect(gw, name=journal, issn=issn, as_of=as_of)
        state.write_text(facts_file, facts.dumps(data))

    homepage = (data.get("openalex") or {}).get("homepage_url")
    derived = candidates_for(homepage, extra_urls)
    sha = sha_of(derived)
    st = state.read_state(jdir)
    on_disk = load_candidates(jdir)
    gate_id = st.get("gate_id")
    if gate_id:
        try:
            old = gates.get(run, gate_id)
        except gates.GateError:
            raise SourcesError(f"the journal-sources gate {gate_id} is missing") from None
        if on_disk is None or f"journal-sources {on_disk['sha256'][:12]}:" \
                not in str(old.get("question", "")):
            raise SourcesError("candidates.json no longer matches the journal-sources gate "
                               f"{gate_id}; no page was fetched. Re-run with --restart to start over")
    if on_disk is None or on_disk["sha256"] != sha:
        on_disk = write_candidates(jdir, journal, derived)
        gate_id = None                                        # a changed list: a new gate
        st = {**st, "gate_id": None}

    counts_rows = list(_load_rows(jdir, sha).values())
    if not policy.allows(PROVIDER):
        return _phase(run, jdir, journal, slug, "sources-manual", st, gate_id=None, lines=[
            f"Guideline pages are not fetched: {PROVIDER} is off in config/integrations.yml.",
            "Switch it on with `providers: journal-guidelines: {enabled: true}`, or save each page "
            "as a file and run `scieflow journal add-source`."], counts=_counts(counts_rows))

    if gate_id is None:
        gate = gates.open_gate(project, run, GATE_KIND,
                               question(journal, run.name, derived, sha),
                               options=OPTIONS, files=[f"journal/{CANDIDATES}"], actor="agent")
        return _phase(run, jdir, journal, slug, "gate-open", st, gate_id=gate["id"], lines=[
            f"Waiting for a human decision on {len(derived)} page(s) for {journal}.",
            f"scieflow gate answer {run.name} {gate['id']} approve   (or: ... deny)"],
            counts=_counts(counts_rows))

    gate = gates.get(run, gate_id)
    answer = decision(gate, sha)
    if answer == "open":
        return _phase(run, jdir, journal, slug, "gate-open", st, gate_id=gate_id, lines=[
            f"Still waiting: scieflow gate answer {run.name} {gate_id} approve   (or: ... deny)"],
            counts=_counts(counts_rows))
    if answer == "deny":
        return _phase(run, jdir, journal, slug, "denied", st, gate_id=gate_id,
                      lines=["The human denied the guideline fetch: nothing was requested."],
                      counts=_counts(counts_rows))
    if st.get("phase") == "fetched" and not retry_failed:
        return _phase(run, jdir, journal, slug, "fetched", st, gate_id=gate_id, lines=[
            "Already fetched for this list; use --retry-failed to re-request failed pages."],
            counts=_counts(counts_rows))

    prev = _load_rows(jdir, sha)
    targets = [u for u in derived
               if not retry_failed or prev.get(source_id(u), {}).get("status") == "failed"]
    fetch_gw = make_gateway(run_hosts={PROVIDER: hosts_of(derived)})
    fresh = {source_id(u): fetch_one(fetch_gw, jdir, u) for u in targets}
    rows = [fresh.get(source_id(u)) or prev.get(source_id(u))
            for u in derived]
    rows = [r for r in rows if r is not None]
    _write_rows(jdir, journal, sha, rows)
    lines = [f"Fetched {len(targets)} page(s) for {journal}."]
    for row in rows:
        if row["status"] != "ok":
            lines.append(f"{row['status']}: {row['url']} ({row.get('classification') or '-'})")
            if (row.get("classification") or "").startswith("HTTP_3"):
                lines.append("  A redirect is not followed: re-run with the final URL as --url.")
    return _phase(run, jdir, journal, slug, "fetched", st, gate_id=gate_id, lines=lines,
                  counts=_counts(rows))


def _phase(run: Path, jdir: Path, journal: str, slug: str, phase: str, st: dict, *,
           gate_id: str | None, lines: list[str], counts: dict) -> Result:
    st = {**st, "version": 1, "journal": journal, "slug": slug, "phase": phase,
          "gate_id": gate_id, "updated_at": _now()}
    state.write_state(jdir, st)
    state.note(run, journal=journal, phase=phase, sources_ok=counts.get("ok", 0),
               sources_failed=counts.get("failed", 0), gate=gate_id)
    exit_code = 0
    return Result(exit_code, phase, lines, gate_id, counts)


def add_source(jdir: Path, file_path: Path, url: str, note: str = "") -> dict:
    """A page the human saved (M-F 5.4): a regular file of at most 5 MB with a page suffix and an
    https URL saying where it came from. The same text pipeline as a fetched page."""
    src = Path(file_path)
    if src.is_symlink():
        raise SourcesError("refused: the file is a symbolic link")
    if not src.is_file():
        raise SourcesError("refused: not a regular file")
    if src.suffix.lower() not in SAVED_SUFFIXES:
        raise SourcesError(f"refused: the suffix must be one of {', '.join(SAVED_SUFFIXES)}")
    if src.stat().st_size > MAX_SAVED_BYTES:
        raise SourcesError("refused: the file is larger than 5 MB")
    where = https_url(url)
    try:
        body = src.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise SourcesError("refused: the file is not UTF-8 text") from None
    if len(body) > MAX_CHARS:
        raise SourcesError("refused: the page is too large")
    text = html.to_text(body)
    sid = source_id(where)
    _store(jdir, sid, body.encode("utf-8"), text)
    row = {"id": sid, "url": where, "host": urlsplit(where).hostname, "status": "ok",
           "classification": None, "retrieved_at": _now(), "retrieved_by": "human-file",
           "raw_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
           "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
           "chars": len(text), "note": (note or "")[:200]}
    current = state.read_json(jdir / SOURCES) or {}
    rows = [r for r in current.get("rows", []) if isinstance(r, dict) and r.get("id") != sid]
    _write_rows(jdir, current.get("journal", ""), current.get("candidates_sha256"),
                [*rows, row])
    return row
