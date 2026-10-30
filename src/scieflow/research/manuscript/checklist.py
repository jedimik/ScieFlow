"""Reporting checklists against a manuscript (M-F plan 5.10).

Which guidelines a check uses, the status of every checklist item and the filled checklist. The mapping
is deterministic. An item is `missing` when no manuscript part of a mapped kind exists (where: the kind),
or when its evidence (a figure or table environment, a registry id) is not inside a mapped part. A
statement or a number is `needs-judgement` when a mapped part exists. Checklists are read only through
scieflow.core.design.reporting and the design only through scieflow.core.design.model.load.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scieflow.core.design import model as design_model
from scieflow.core.design import reporting
from scieflow.research.manuscript import tex

STATUSES = ("present", "missing", "needs-judgement", "judged-present", "judged-absent", "not-applicable")
EVIDENCE_HITS = {"diagram": "figure", "figure": "figure", "table": "table", "registration": "registration"}
SECTION_RE = re.compile(r"\\section\*?\s*\{([^{}]*)\}")
ENV_RE = re.compile(r"\\begin\{(figure|table)\*?\}")
REGISTRY_RE = re.compile(r"NCT\d{8}|ISRCTN\d{8}|CRD\d{11}|ACTRN\d{14}|DRKS\d{8}|ChiCTR[-\w]{8,}"
                         r"|\d{4}-\d{6}-\d{2}|osf\.io/[a-z0-9]{5}")
KIND_TITLES = {
    "introduction": ("introduction", "background"),
    "methods": ("methods", "method", "materials and methods", "patients and methods",
                "participants and methods", "methodology", "study design", "experimental procedures",
                "statistical analysis"),
    "results": ("results", "findings"),
    "discussion": ("discussion", "conclusion", "conclusions", "limitations", "discussion and conclusions"),
}


class ChecklistError(ValueError):
    """A checklist cannot be used for the requested prompt (the command exits 1)."""


def _title_kinds() -> dict:
    kinds: dict = {}
    for kind, titles in KIND_TITLES.items():
        for title in titles:
            kinds.setdefault(title, set()).add(kind)
    kinds["results and discussion"] = {"results", "discussion"}
    return kinds


TITLE_KINDS = _title_kinds()


def section_kinds(title: str) -> frozenset:
    """The part kinds of a section title (M-F 5.10); a title that maps to none is `other`."""
    kinds = TITLE_KINDS.get(tex.normalise_title(title))
    return frozenset(kinds) if kinds else frozenset({"other"})


@dataclass(frozen=True)
class Hit:
    kinds: frozenset
    where: str


@dataclass(frozen=True)
class Index:
    """What the manuscript shows: its parts, the evidence hits and the comment-free text for quotes."""

    parts: frozenset
    hits: dict
    text: str
    collapsed: str


@dataclass
class ItemResult:
    id: str
    topic: str
    sections: tuple
    status: str
    where: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "topic": self.topic, "sections": list(self.sections),
                "status": self.status, "where": self.where, "reason": self.reason}


@dataclass
class Selection:
    ids: list = field(default_factory=list)
    checklists: list = field(default_factory=list)
    problems: list = field(default_factory=list)


def build_index(tree, analysis) -> Index:
    """The parts and evidence of a manuscript read by tex.read_tree and tex.analyse."""
    parts = {"other"}
    if analysis.title and analysis.title.strip():
        parts.add("title")
    if analysis.abstract is not None:
        parts.add("abstract")
    for section in analysis.sections:
        if section.title:
            parts |= section_kinds(section.title)
    hits: dict = {"figure": [], "table": [], "registration": []}
    started = not any("\\begin{document}" in tex.strip_line(line.text) for line in tree.lines)
    current = None
    for line in tree.lines:
        text = tex.strip_line(line.text)
        if not started and "\\begin{document}" in text:
            started = True
        for match in SECTION_RE.finditer(text):
            current = " ".join(match.group(1).split()) or None
        kinds = section_kinds(current) if current else frozenset()
        where = f"{tex.excerpt(current) if current else 'no section'} ({line.file}:{line.lineno})"
        if started:
            for match in ENV_RE.finditer(text):
                hits[match.group(1)].append(Hit(kinds, where))
        for _ in REGISTRY_RE.finditer(text):
            hits["registration"].append(Hit(kinds, where))
    body = tex.strip_comments(tree.flat_text())
    return Index(parts=frozenset(parts), hits=hits, text=body, collapsed=" ".join(body.split()))


def _status(item: dict, sections: tuple, index: Index) -> tuple:
    if not any(section in index.parts for section in sections):
        return "missing", sections[0]
    key = EVIDENCE_HITS.get(item["evidence"])
    if key is None:
        return "needs-judgement", None
    for hit in index.hits[key]:
        if "other" in sections or hit.kinds & set(sections):
            return "present", hit.where
    return "missing", sections[0]


def evaluate(checklist, index: Index) -> list:
    """One ItemResult per checklist item, in checklist order."""
    rows = []
    for item in checklist.items:
        sections = tuple(item["sections"])
        status, where = _status(item, sections, index)
        rows.append(ItemResult(item["id"], item["topic"], sections, status, where))
    return rows


def is_verified(checklist) -> bool:
    return checklist.verification.get("status") == "verified"


def summary(checklist, rows: list) -> dict:
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row.status] += 1
    return {"id": checklist.id, "name": checklist.name, "verified": is_verified(checklist),
            "counts": counts, "items": [row.to_dict() for row in rows]}


def _cell(value) -> str:
    return " ".join(str(value).split()).replace("|", "/")


def render(checklist, rows: list) -> str:
    """The filled checklist (report/reporting-checklist-<id>.md): a banner when unverified, one row per item."""
    lines = [f"# Reporting checklist: {checklist.name} ({checklist.id})", ""]
    if not is_verified(checklist):
        lines += [f"This checklist is an unverified skeleton (config/reporting/{checklist.id}.yml); "
                  f"check every item against {checklist.source['citation']} before submission.", ""]
    lines += ["| Item | Topic | Section(s) | Status | Where |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        place = row.where or row.reason or ""
        cells = (row.id, row.topic, ", ".join(row.sections), row.status, place)
        lines.append("| " + " | ".join(_cell(value) for value in cells) + " |")
    return "\n".join(lines) + "\n"


def _todo(value: str) -> bool:
    return value == "TODO" or value.startswith("TODO:")


def select(root, run_dir, profile, requested) -> Selection:
    """The guidelines of a check: --guideline ids, the design's list and the profile's list for the design's study type.

    Returns the sorted ids (known or not), the checklists to evaluate and the (rule, severity, message,
    where) problems to report. A profile guideline the design does not name is a warning (M-F-D12).
    """
    out = Selection()
    design_ids: list = []
    design_study = None
    design_read = False
    loaded = design_model.load(run_dir)
    if isinstance(loaded, design_model.Invalid):
        out.problems.append(("design-unreadable", "warning", loaded.reason, "design/design.yml"))
    elif isinstance(loaded, dict):
        design_read = True
        listed = loaded.get("reporting_guidelines")
        if isinstance(listed, list):
            design_ids = [g for g in listed if isinstance(g, str) and not _todo(g)]
        study = loaded.get("study_type")
        design_study = study if isinstance(study, str) else None
    profile_ids: list = []
    if profile and design_read and design_study:
        block = profile.get("reporting_guidelines")
        by_type = block.get("by_study_type") if isinstance(block, dict) else None
        named = by_type.get(design_study) if isinstance(by_type, dict) else None
        if isinstance(named, list):
            profile_ids = [g for g in named if isinstance(g, str)]
    out.ids = sorted(set(requested) | set(design_ids) | set(profile_ids))
    try:
        known = {c.id: c for c in reporting.registry(root)}
    except reporting.ReportingError:
        known = None
        if out.ids:
            out.problems.append(("reporting-unreadable", "error",
                                 "the reporting checklists under config/reporting cannot be loaded",
                                 "config/reporting"))
    for gid in out.ids:
        where = tex.excerpt(gid)
        if gid in profile_ids and gid not in design_ids:
            out.problems.append(("guideline-not-in-design", "warning",
                                 "the journal profile names this guideline for the study type; the design does not",
                                 where))
        if known is None:
            continue
        if gid not in known:
            out.problems.append(("guideline-unknown", "error",
                                 "no reporting checklist has this id (config/reporting/index.yml)", where))
            continue
        checklist = known[gid]
        out.checklists.append(checklist)
        if not is_verified(checklist):
            out.problems.append(("guideline-unverified", "warning",
                                 "the checklist is an unverified skeleton; check every item before submission",
                                 gid))
    return out
