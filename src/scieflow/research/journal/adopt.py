"""`scieflow journal adopt` (M-F plan 5.6): validate the agent's draft against the run's stored sources,
then adopt it into config/journals/<slug>.yml.

Order: refuse inside a dispatch; the run's ok sources; facts.json; the draft (a regular file, at most
1 MB); the facts blocks (identity, oa, norms) replaced from facts.json; provenance written; every rule
of the schema checked (quotes re-checked against the stored source text). Only a draft that passes is
written. A different existing profile needs --replace (the adopted_at stamp is not compared).
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scieflow.core import events
from scieflow.core.project import Project
from scieflow.integrations.scite import mcp as scite_mcp
from scieflow.research.journal import facts, profile, render, state

MARKER = "<!-- scieflow: rendered from "
TOOL = "scieflow journal adopt"
MAX_AGE_DAYS = 90
FORMAT = 1


class AdoptError(state.StateError):
    """A refusal: exit 1, nothing written."""


@dataclass
class Adopted:
    slug: str
    journal: str
    path: str
    sha256: str
    sources: int
    adopted_by: str
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"slug": self.slug, "journal": self.journal, "path": self.path, "sha256": self.sha256,
                "sources": self.sources, "adopted_by": self.adopted_by, "warnings": self.warnings,
                "lines": self.lines}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _null_norms() -> dict:
    return {"sample": {"n": 0, "from_date": None, "to_date": None},
            "references": {"median": None, "p25": None, "p75": None},
            "abstract_words": {"median": None, "p25": None, "p75": None, "share_with_abstract": None},
            "title_words": {"median": None}}


def facts_blocks(data: dict, slug: str) -> dict:
    """identity, oa and norms as facts.json states them (never taken from a draft)."""
    oa = data.get("openalex") or {}
    crossref = data.get("crossref") or {}
    query = data.get("query") or {}
    identity = {
        "name": oa.get("display_name") or query.get("name"),
        "slug": slug,
        "issn_l": oa.get("issn_l"),
        "issns": list(oa.get("issn") or []),
        "publisher": oa.get("host_organization_name"),
        "homepage_url": oa.get("homepage_url"),
        "openalex_id": oa.get("id"),
        "crossref_title": crossref.get("title"),
    }
    open_access = {"is_oa": oa.get("is_oa"), "is_in_doaj": oa.get("is_in_doaj"),
                   "apc_usd": oa.get("apc_usd")}
    norms = data.get("norms") if isinstance(data.get("norms"), dict) else _null_norms()
    return {"identity": identity, "oa": open_access, "norms": norms}


def _ordered(data: dict, root: Path) -> dict:
    """Top-level keys and the identity/oa blocks in the order of schemas/journal-profile.yml."""
    schema = profile.load_schema(root)
    top = [*profile.required_top_keys(root), *profile.optional_top_keys(root)]
    out = {key: data[key] for key in top if key in data}
    out.update({key: value for key, value in data.items() if key not in out})
    for block in ("identity", "oa"):
        keys = schema["blocks"][block]
        if isinstance(out.get(block), dict):
            out[block] = {key: out[block][key] for key in keys if key in out[block]}
    return out


def _comparable(data: dict) -> dict:
    copied = copy.deepcopy(data)
    if isinstance(copied.get("provenance"), dict):
        copied["provenance"].pop("adopted_at", None)
    return copied


def _provenance(run: str, facts_sha: str, rows: list[dict], *, as_agent: bool, now: str) -> dict:
    sources = [{"id": row["id"], "url": row["url"], "retrieved_at": str(row["retrieved_at"])[:10],
                "retrieved_by": row["retrieved_by"], "text_sha256": row["text_sha256"]} for row in rows]
    return {"run": run, "facts_sha256": facts_sha, "sources": sources, "max_age_days": MAX_AGE_DAYS,
            "adopted_at": now, "adopted_by": "agent" if as_agent else "human", "tool": TOOL,
            "format": FORMAT}


def _refuse(problems: list[profile.Problem]) -> None:
    errors = [p for p in problems if p.level == "error"]
    if errors:
        lines = ["refused: the draft does not validate; nothing was written"]
        lines += [f"  {p.rule} {p.path}: {p.message}" for p in errors]
        raise AdoptError("\n".join(lines))


def adopt(project: Project, run: Path, *, draft_path: Path | None = None, replace: bool = False,
          as_agent: bool = False, now: str | None = None) -> Adopted:
    """Adopt the run's draft. Raises AdoptError (or StateError / ProfileError) with nothing written."""
    if scite_mcp.in_dispatch():
        raise AdoptError("refused: the coordinator adopts; sub-agents never do (SCIEFLOW_DISPATCH is set)")
    jdir = state.journal_dir(run)
    st = state.read_state(jdir)
    if not st.get("slug"):
        raise AdoptError("refused: no journal research state for this run; run `scieflow journal research` first")
    slug = state.check_slug(st["slug"])
    stored = profile.load_sources(run)
    if stored is None or not stored.texts:
        raise AdoptError("refused: no ok source in this run's journal/ folder")
    facts_file = facts.facts_path(jdir)
    facts_data = state.read_json(facts_file)
    if not isinstance(facts_data, dict) or facts_file.is_symlink():
        raise AdoptError("refused: no facts.json; run `scieflow journal research` first")
    draft_file = Path(draft_path) if draft_path is not None else jdir / "profile.draft.yml"
    try:
        draft = profile.load_profile(draft_file)
    except profile.ProfileError as exc:
        raise AdoptError(f"refused: {exc}") from None

    candidate = copy.deepcopy(draft)
    warnings = []
    for name, block in facts_blocks(facts_data, slug).items():
        if name in draft and draft[name] != block:
            warnings.append(profile.Problem("facts-overridden", name,
                                            "replaced from facts.json", "warning"))
        candidate[name] = block
    rows = [stored.rows[sid] for sid in stored.texts]
    facts_sha = hashlib.sha256(facts_file.read_bytes()).hexdigest()
    candidate["provenance"] = _provenance(run.name, facts_sha, rows, as_agent=as_agent,
                                          now=now or _now())
    candidate = _ordered(candidate, project.root)

    problems = profile.validate(candidate, root=project.root, run_dir=run)
    _refuse(problems)

    text = yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True, width=100)
    data = text.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    journals = project.root / "config" / "journals"
    target = journals / f"{slug}.yml"
    page = journals / f"{slug}.md"
    # Every refusal is decided before the first write (review t4 finding 1): a symlinked target, page,
    # journal folder or working file stops the adoption with nothing written.
    for link in (journals, target, page, jdir, jdir / "adopted", jdir / "adopted" / f"{sha[:12]}.yml",
                 jdir / "profile.yml", jdir / "profile.md"):
        if link.is_symlink():
            raise AdoptError(f"refused: {link.name} is a symbolic link")
    if target.exists():
        existing = profile.load_profile(target)
        if _comparable(existing) != _comparable(candidate) and not replace:
            raise AdoptError(f"refused: {target.name} holds a different profile; run "
                             f"`scieflow journal diff {slug} {run.name}` to see the change, "
                             "then re-run with --replace")
    md = render.render_md(candidate, slug)
    keep_page = page.exists() and not page.read_text(encoding="utf-8", errors="replace").startswith(
        f"{MARKER}{slug}.yml")
    lines = [f"Adopted {slug}: config/journals/{slug}.yml (sha256 {sha[:12]}, "
             f"{len(rows)} source(s), adopted by {candidate['provenance']['adopted_by']})."]

    state.atomic_write(target, data)
    state.atomic_write(jdir / "adopted" / f"{sha[:12]}.yml", data)
    state.atomic_write(jdir / "profile.yml", data)
    state.write_text(jdir / "profile.md", md)
    if keep_page:
        lines.append(f"note: kept the hand-written {page.name}; it was not overwritten.")
    else:
        state.write_text(page, md)
    for problem in warnings + [p for p in problems if p.level == "warning"]:
        lines.append(f"warning {problem.rule} {problem.path}: {problem.message}")
    adopted_by = candidate["provenance"]["adopted_by"]
    events.emit(run, "note.journal_adopted", actor="agent" if as_agent else "human",
                journal=candidate["identity"]["name"], sha256=sha, sources=len(rows),
                adopted_by=adopted_by)
    return Adopted(slug=slug, journal=candidate["identity"]["name"],
                   path=f"config/journals/{slug}.yml", sha256=sha, sources=len(rows),
                   adopted_by=adopted_by, lines=lines,
                   warnings=[f"{p.rule} {p.path}" for p in warnings])
