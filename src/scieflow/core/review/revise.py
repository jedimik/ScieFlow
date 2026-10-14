"""Filtered revision: authors get only accepted clusters (spec §5; plan T2.4).

`revision_prompt(ws, round, author)` builds the author's revision task from the
round's decisions as `decisions.decide()` recorded them (`decisions.authentic`):
a hand-written or edited decisions.json counts as no decisions at all (Review
Focus #7). A cluster concerns an author when one of its members is a comment on
that author's draft; a cluster made only of bundle flags (no comments) concerns
every author whose draft cites a flagged reference.

`coverage(ws, round, author)` is the mechanical check after the revision: every
accepted cluster id of that author must head a section (`## K2`) with text
under it in `review/round-N/response-<author>.md` (rejected and deferred ids
are not required; an author with nothing accepted needs no letter).
"""

from __future__ import annotations

import re
from pathlib import Path

from scieflow.core.review import claims as claims_mod
from scieflow.core.review import decisions as decisions_mod
from scieflow.core.review import matrix as matrix_mod
from scieflow.core.review import passes

TEMPLATE = "revise-accepted.md"
ROLE = "research.draft-authors"
_CITE = re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\*?\s*(?:\[[^\]]*\]\s*)*\{([^{}]*)\}")


class RevisionError(ValueError):
    """The revision step is refused; nothing was written."""


def role(ws: Path) -> str:
    """The `--role` the revision of this run is dispatched with."""
    return "research.submitter" if claims_mod.reviews_manuscript(ws) else ROLE


def response_name(author: str) -> str:
    return f"response-{author}.md"


def _cited(ws: Path, author: str) -> set[str]:
    """Bib keys the author's draft cites (\\cite, \\citep, \\textcite, ...)."""
    _root, files = passes.draft_files(ws, author)
    keys: set[str] = set()
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _CITE.finditer(text):
            keys.update(k.strip() for k in m.group(1).split(",") if k.strip())
    return keys


def _setup(ws: Path, round_: int, author: str):
    ws = Path(ws)
    try:
        n = passes._round(round_)
        passes._name(author, "author")
    except passes.PassError as exc:
        raise RevisionError(str(exc)) from exc
    recorded = decisions_mod.authentic(ws, n)
    if recorded is None:
        raise RevisionError(
            f"round {n} has no recorded decisions: decisions.json is written only by "
            "`scieflow review decide` or a review-decision gate answer, and a hand-written "
            "or edited file is ignored")
    try:
        data = matrix_mod.check(ws, n)
    except matrix_mod.MatrixError as exc:
        raise RevisionError(f"round {n}: the matrix does not check: "
                            + "; ".join(exc.problems)) from exc
    loaded = matrix_mod._bundle(ws, n)
    bundle = loaded[1] if loaded else {"flags": [], "references": []}
    return ws, n, recorded["decisions"], data, bundle


def _flag_keys(cluster: dict, bundle: dict) -> set[str]:
    by_doi = {str(r.get("doi", "")).lower(): r.get("key") for r in bundle.get("references", [])}
    flags = {f["id"]: f for f in bundle.get("flags", [])}
    keys = set()
    for fid in cluster.get("flags") or []:
        f = flags.get(fid) or {}
        if f.get("key"):
            keys.add(f["key"])
        if f.get("doi") and by_doi.get(str(f["doi"]).lower()):
            keys.add(by_doi[str(f["doi"]).lower()])
    return keys


def _concerns(cluster: dict, author: str, cited: set[str], bundle: dict) -> bool:
    if cluster["members"]:
        return any(m["author"] == author for m in cluster["members"])
    return bool(_flag_keys(cluster, bundle) & cited)


def _comments(ws: Path, n: int, cluster: dict, author: str) -> list[dict]:
    """The cluster's comments on `author`'s draft; a pass-B version replaces its pass A."""
    chosen: dict[tuple[str, str], tuple[str, dict]] = {}
    for m in cluster["members"]:
        if m["author"] != author:
            continue
        key = (m["reviewer"], m["id"])
        if key in chosen and chosen[key][0] == "evidence":
            continue
        review = passes.validate_pass(ws, n, m["reviewer"], author, m["pass"])
        for severity in ("major", "minor"):
            for c in review[severity]:
                if c["id"] == m["id"]:
                    chosen[key] = (m["pass"], {**c, "severity": severity, "pass": m["pass"],
                                               "reviewer": m["reviewer"]})
    return [v[1] for _k, v in sorted(chosen.items())]


def clusters_for(ws: Path, round_: int, author: str) -> dict:
    """The round's clusters that concern `author`, by decision.

    {"round", "author", "accepted": [cluster + "decision" + "comments"],
     "rejected": [ids], "deferred": [ids], "undecided": [ids]}
    """
    ws, n, decided, data, bundle = _setup(ws, round_, author)
    try:
        cited = _cited(ws, author)
    except passes.PassError as exc:
        raise RevisionError(str(exc)) from exc
    out = {"round": n, "author": author, "accepted": [], "rejected": [], "deferred": [],
           "undecided": []}
    for c in data["clusters"]:
        if not _concerns(c, author, cited, bundle):
            continue
        d = decided.get(c["cluster"])
        if d is None:
            out["undecided"].append(c["cluster"])
        elif d["decision"] == "accept":
            try:
                comments = _comments(ws, n, c, author)
            except passes.PassError as exc:
                raise RevisionError(str(exc)) from exc
            out["accepted"].append({**c, "decision": d, "comments": comments})
        else:
            out["rejected" if d["decision"] == "reject" else "deferred"].append(c["cluster"])
    return out


def _render_cluster(c: dict) -> str:
    one = matrix_mod._cell
    lines = [f"## {c['cluster']} — {one(c.get('title', ''))}"
             + (f" ({c['severity']})" if c.get("severity") else "")]
    d = c["decision"]
    who = "the user" if d["actor"] == "human" else "the coordinator"
    lines.append(f"Decision: accept, by {who}.")
    if d.get("note"):
        lines.append(f"Decision note: {one(d['note'])}")
    if d.get("rationale"):
        lines.append(f"Decision rationale: {one(d['rationale'])}")
    if d.get("overrule_reason"):
        lines.append(f"Blocker ({c.get('blocker_reason')}), user's reason: "
                     f"{one(d['overrule_reason'])}")
    lines.append(f"Coordinator's summary: {one(c['rationale'])}")
    for side, label in (("evidence_for", "Evidence for"), ("evidence_against", "Evidence against")):
        for e in c[side]:
            lines.append(f"{label}: {e['doi']} ({e['source']}"
                         + (f", {e['stance']}" if e.get("stance") else "")
                         + f"): \"{one(e['quote'])}\"")
    if c.get("flags"):
        lines.append(f"Evidence-bundle flags: {', '.join(c['flags'])}")
    if c["comments"]:
        lines.append("Comments on your draft:")
    for cm in c["comments"]:
        status = f", {cm['changed_from_blind']}" if cm.get("changed_from_blind") else ""
        text = cm.get("problem") or cm.get("comment") or ""
        lines.append(f"- reviewer {cm['reviewer']}, {cm['id']} ({cm['severity']}, {cm['pass']} "
                     f"pass{status}) at {one(cm['location'])}: {one(text)}")
        for key, label in (("why_it_matters", "Why it matters"),
                           ("resolution", "Suggested resolution")):
            if cm.get(key):
                lines.append(f"  {label}: {one(cm[key])}")
        for e in cm.get("evidence") or []:
            lines.append(f"  Evidence: {e['doi']} ({e.get('stance', '')}): \"{one(e['quote'])}\"")
    if not c["comments"]:
        lines.append("No reviewer comment: the evidence bundle flagged a reference your "
                     "draft cites.")
    return "\n".join(lines)


def _files(ws: Path, author: str) -> str:
    """Run-relative paths of the files the author edits (main.tex and its inputs)."""
    _root, files = passes.draft_files(ws, author)
    base = Path(ws).resolve()
    return ", ".join(p.resolve().relative_to(base).as_posix() for p in files)


def revision_prompt(ws: Path, round_: int, author: str, *, slug: str | None = None) -> str:
    """The author's revision task with the accepted clusters only."""
    got = clusters_for(ws, round_, author)
    if not got["accepted"]:
        raise RevisionError(f"nothing accepted for {author} in round {got['round']}: no "
                            "revision dispatch is needed")
    body = "\n\n".join(_render_cluster(c) for c in got["accepted"])
    return passes._template(
        TEMPLATE, round=got["round"], slug=slug or Path(ws).name, author=author,
        draft_dir=passes.draft_dir(ws, author), files=_files(ws, author),
        ids=", ".join(c["cluster"] for c in got["accepted"]),
        clusters=passes._fenced(f"ACCEPTED REVIEW CLUSTERS for {author}, round {got['round']}",
                                body))


def coverage(ws: Path, round_: int, author: str) -> dict:
    """Accepted ids of `author` missing from review/round-N/response-<author>.md."""
    got = clusters_for(ws, round_, author)
    n = got["round"]
    required = [c["cluster"] for c in got["accepted"]]
    path = passes.round_dir(ws, n) / response_name(author)
    try:
        raw = passes._read(Path(ws), path)
    except passes.PassError as exc:
        raise RevisionError(str(exc)) from exc
    text = raw.decode("utf-8", errors="replace") if raw is not None else ""
    sections = _sections(text)
    missing = [cid for cid in required if not sections.get(cid)]
    return {"round": n, "author": author, "response": raw is not None, "required": required,
            "missing": missing, "ok": not missing and (raw is not None or not required)}


_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]*([A-Za-z][A-Za-z0-9_-]{0,63})(?![A-Za-z0-9_-])(.*)$")


def _sections(text: str) -> dict[str, bool]:
    """Cluster id -> whether its heading (`## K2 ...`) has any text under it."""
    out: dict[str, bool] = {}
    current = None
    for line in text.splitlines():
        if re.match(r"^ {0,3}#{1,6}(\s|$)", line):
            m = _HEADING.match(line)
            current = m.group(1) if m else None
            if current is not None:
                out.setdefault(current, False)
            continue
        if current is not None and line.strip():
            out[current] = True
    return out
