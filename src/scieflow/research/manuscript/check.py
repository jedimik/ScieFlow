"""`scieflow manuscript check` (M-F 5.9): the manuscript against its journal profile.

The check is local and deterministic: it reads the manuscript folder and the profile, writes
report/manuscript-check.{json,md} under the run, and never sends anything. Messages name rules,
limits, counts, section headings (excerpted to 60 characters), keys and author positions; they
never carry an author e-mail or a longer manuscript text. Reporting-guideline rules (guideline-*)
are M-F t6 and are not evaluated here; the requested ids are recorded in the report.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from scieflow._vendor.researcher_core.contributors import MetadataError, validate_orcid
from scieflow.core import events
from scieflow.research.journal import profile, state
from scieflow.research.manuscript import metadata, tex

VERSION = 1
REPORT_DIR = "report"
JSON_NAME = "manuscript-check.json"
MD_NAME = "manuscript-check.md"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DEFAULT_HEADINGS = ("Background", "Objective(s)", "Methods", "Results", "Conclusion(s)")
DECLARATION_META = {
    "coi": "coi", "funding": "funding", "ethics": "ethics",
    "data-availability": "data_availability", "code-availability": "code_availability",
    "ai-use": "ai_use",
}
DECLARATION_SECTIONS = {
    "coi": {"competing interests", "conflict of interest", "conflicts of interest"},
    "funding": {"funding"},
    "ethics": {"ethics", "ethics statement"},
    "data-availability": {"data availability"},
    "code-availability": {"code availability"},
    "credit": {"author contributions", "credit authorship contribution statement"},
}


class CheckError(Exception):
    """The run cannot be checked at all (the command exits 1)."""


@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    where: str | None = None
    limit: int | None = None
    value: int | None = None

    def to_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "where": self.where,
                "message": self.message, "limit": self.limit, "value": self.value}


@dataclass
class Report:
    journal: str | None
    article_type: str | None
    profile_sha256: str | None
    manuscript_sha256: str
    counts: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    guidelines: list = field(default_factory=list)
    information: dict = field(default_factory=dict)

    @property
    def errors(self) -> list:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def exit_code(self) -> int:
        return 1 if self.errors else 0

    def to_dict(self) -> dict:
        return {
            "version": VERSION, "journal": self.journal, "profile_sha256": self.profile_sha256,
            "article_type": self.article_type, "manuscript_sha256": self.manuscript_sha256,
            "counts": dict(sorted(self.counts.items())),
            "findings": [f.to_dict() for f in self.findings],
            "guidelines": list(self.guidelines), "information": self.information,
        }


_excerpt = tex.excerpt


def _profile_run_dir(root: Path, data: dict) -> Path | None:
    prov = data.get("provenance") if isinstance(data, dict) else None
    run = prov.get("run") if isinstance(prov, dict) else None
    if not isinstance(run, str) or not re.match(r"^[A-Za-z0-9._-]+$", run):
        return None
    path = root / "workspace" / run
    return path if path.is_dir() else None


def _heading_hits(text: str, headings) -> set:
    hits = set()
    for paragraph in re.split(r"\n\s*\n", text):
        opened = re.sub(r"^\\(?:textbf|emph|textit|bf|it)\s*\{\s*", "", paragraph.strip())
        for heading in headings:
            base = re.sub(r"\(s\)", "", str(heading)).strip()
            if re.match(rf"(?i){re.escape(base)}s?\s*(?::|\}})", opened):
                hits.add(base.lower())
    return hits


def _valid_orcid(value) -> bool:
    try:
        validate_orcid(str(value))
    except MetadataError:
        return False
    return True


def _declaration_met(decl: str, meta, section_norms: set) -> bool:
    text = (meta or {}).get("declarations") or {}
    if decl in DECLARATION_META:
        value = text.get(DECLARATION_META[decl]) if isinstance(text, dict) else None
        if isinstance(value, str) and value.strip():
            return True
    if section_norms & DECLARATION_SECTIONS.get(decl, set()):
        return True
    if decl == "ai-use":
        return any("use of ai" in n or "generative ai" in n for n in section_norms)
    authors = (meta or {}).get("authors") or []
    if decl == "credit":
        return bool(authors) and all(isinstance(a, dict) and a.get("credit") for a in authors)
    if decl == "orcid":
        return bool(authors) and all(
            isinstance(a, dict) and a.get("orcid") is not None and _valid_orcid(a["orcid"]) for a in authors)
    return False


def run_check(root, run_dir, *, journal: str | None = None, article_type: str | None = None,
              main: str | None = None, guidelines=(), today: dt.date | None = None) -> Report:
    root = Path(root)
    run = Path(run_dir)
    folder = run / "manuscript"
    if folder.is_symlink() or not folder.is_dir():
        raise CheckError(f"no manuscript folder in {run.name} (looked for {folder})")
    today = today or dt.datetime.now(dt.timezone.utc).date()
    tree_hash = tex.source_tree_hash(folder)
    requested = sorted(str(g) for g in guidelines)
    findings: list = []

    try:
        tree = tex.read_tree(folder, main or "main.tex")
        analysis = tex.analyse(tree)
    except tex.TexError as exc:
        findings.append(Finding(exc.rule, "error", exc.message, where=_excerpt(exc.path)))
        return Report(None, None, None, tree_hash, {}, findings, requested, {})

    try:
        meta, problems = metadata.load(folder, root)
    except (tex.TexError, ValueError) as exc:
        raise CheckError(str(exc)) from exc
    for problem in problems:
        findings.append(Finding(problem.rule, problem.level, problem.message, where=problem.path))

    slug = journal or (meta.get("journal") if meta else None)
    if slug is not None and (not isinstance(slug, str) or not SLUG.match(slug)):
        findings.append(Finding("profile-missing", "error", "not a journal slug", where="journal"))
        slug = None
    if slug is None and not any(f.rule == "profile-missing" for f in findings):
        findings.append(Finding("profile-missing", "error",
                                "no journal: name it with --journal or in metadata.yml", where="journal"))

    prof = None
    prof_sha = None
    profile_ok = False
    if slug:
        yml = root / "config" / "journals" / f"{slug}.yml"
        page = root / "config" / "journals" / f"{slug}.md"
        if yml.is_symlink() or yml.is_file():
            try:
                prof = profile.load_profile(yml)
                prof_sha = profile.sha256_file(yml)
                issues = profile.validate(prof, root=root, run_dir=_profile_run_dir(root, prof), today=today)
            except profile.ProfileError as exc:
                findings.append(Finding("profile-invalid", "error", str(exc), where="profile"))
                prof = None
            else:
                for issue in issues:
                    if issue.level == "error":
                        findings.append(Finding("profile-invalid", "error",
                                                f"{issue.rule}: {issue.message}", where=issue.path))
                    else:
                        rule = "profile-stale" if issue.rule == "stale" else issue.rule
                        findings.append(Finding(rule, "warning", issue.message, where=issue.path))
                profile_ok = not any(issue.level == "error" for issue in issues)
        elif page.is_file():
            findings.append(Finding("profile-legacy", "error",
                                    f"{slug} has only a hand-written page; adopt a structured profile",
                                    where=slug))
        else:
            findings.append(Finding("profile-missing", "error", f"no journal profile for {slug}",
                                    where=slug))

    types = (prof or {}).get("article_types") if profile_ok else None
    ids = [t.get("id") for t in types] if isinstance(types, list) else []
    chosen = article_type or (meta.get("article_type") if meta else None)
    if chosen is None and len(ids) == 1:
        chosen = ids[0]
    if chosen is None:
        findings.append(Finding("article-type", "error",
                                "no article type: name it with --article-type or in metadata.yml"))
    elif profile_ok and chosen not in ids:
        findings.append(Finding("article-type", "error", f"unknown article type {_excerpt(chosen)}",
                                where=_excerpt(chosen)))
    atype = next((t for t in types if t.get("id") == chosen), None) if profile_ok and chosen else None

    bib = analysis.bib or {}
    cites = analysis.cites
    section_norms = {s.norm for s in analysis.sections if s.title}
    authors = (meta or {}).get("authors") or []
    highlights = (meta or {}).get("highlights") or []
    if meta and isinstance(meta.get("keywords"), list):
        keywords = meta["keywords"]
    else:
        keywords = analysis.tex_keywords or []

    counts = {
        "words_main": analysis.main_words,
        "words_abstract": analysis.abstract_words or 0,
        "words_title": analysis.title_words,
        "caption_words": analysis.caption_words,
        "figures": analysis.figures,
        "tables": analysis.tables,
        "display_items": analysis.display_items,
        "references": len(cites),
        "keywords": len(keywords),
        "highlights": len(highlights),
        "sections": len([s for s in analysis.sections if s.title]),
    }
    years = sorted(int(bib[k]) for k in cites if bib.get(k))
    information = {"references_without_year": len([k for k in cites if k in bib and not bib[k]]),
                   "median_year": None, "share_last_five_years": None}
    if years:
        middle = len(years) // 2
        information["median_year"] = years[middle] if len(years) % 2 else (years[middle - 1] + years[middle]) // 2
        recent = len([y for y in years if y >= today.year - 4])
        information["share_last_five_years"] = round(recent / len(years), 3)

    if analysis.abstract is None:
        findings.append(Finding("abstract-missing", "error", "the manuscript has no abstract"))
    if atype is not None:
        _limit_rules(findings, atype, analysis, counts, keywords, highlights, meta)
        _structure_rules(findings, atype, analysis, section_norms)
    _citation_rules(findings, bib, cites)
    if profile_ok:
        for decl in prof["declarations"]["required"]:
            if not _declaration_met(decl, meta, section_norms):
                findings.append(Finding(
                    "declaration-missing", "error",
                    f"required declaration {decl!r} has neither a metadata text nor a matching section",
                    where=decl))
        _template_rules(findings, prof, analysis)

    return Report(journal=slug, article_type=chosen, profile_sha256=prof_sha,
                  manuscript_sha256=tree_hash, counts=counts, findings=findings,
                  guidelines=requested, information=information)


def _limit_rules(findings, atype, analysis, counts, keywords, highlights, meta) -> None:
    limits = atype.get("limits") or {}

    def over(rule, key, value, where=None):
        limit = limits.get(key)
        if limit is not None and value > limit:
            findings.append(Finding(rule, "error", f"{value} exceeds the limit {limit}", where=where,
                                    limit=limit, value=value))

    if analysis.abstract is not None:
        over("abstract-words", "words_abstract", analysis.abstract_words or 0)
        structure = atype.get("abstract") or {}
        if structure.get("structured") is True:
            headings = structure.get("headings") or DEFAULT_HEADINGS
            if len(_heading_hits(analysis.abstract, headings)) < 2:
                findings.append(Finding("abstract-structure", "warning",
                                        "fewer than two of the expected abstract headings start a paragraph"))
    over("title-words", "words_title", analysis.title_words)
    over("main-words", "words_main", analysis.main_words, where="main text")
    over("figures", "figures_max", analysis.figures)
    over("tables", "tables_max", analysis.tables)
    over("display-items", "display_items_max", analysis.display_items)
    low, high = limits.get("keywords_min"), limits.get("keywords_max")
    count = len(keywords)
    if (low is not None and count < low) or (high is not None and count > high):
        findings.append(Finding("keywords", "error", f"{count} keywords, outside {low}..{high}",
                                limit=low if low is not None and count < low else high, value=count))
    limit = limits.get("highlights_max")
    if limit is not None and len(highlights) > limit:
        findings.append(Finding("highlights", "error", f"{len(highlights)} highlights exceed {limit}",
                                limit=limit, value=len(highlights)))
    chars = limits.get("highlight_chars_max")
    if chars is not None:
        for index, text in enumerate(highlights):
            if len(text) > chars:
                findings.append(Finding("highlights", "error",
                                        f"highlight {index + 1} has {len(text)} characters",
                                        where=f"highlights[{index}]", limit=chars, value=len(text)))
    refs = len(analysis.cites)
    low, high = limits.get("references_min"), limits.get("references_max")
    if (low is not None and refs < low) or (high is not None and refs > high):
        findings.append(Finding("references-count", "error",
                                f"{refs} cited keys, outside {low}..{high}",
                                limit=low if low is not None and refs < low else high, value=refs))


def _structure_rules(findings, atype, analysis, section_norms) -> None:
    structure = atype.get("structure") or {}
    items = structure.get("sections") or []
    sections = [s for s in analysis.sections if s.title]
    positions = []
    for item in items:
        names = {tex.normalise_title(item["title"])} | {tex.normalise_title(a) for a in item.get("aliases") or []}
        found = next((i for i, s in enumerate(sections) if s.norm in names), None)
        title = _excerpt(item["title"])
        if found is None:
            if item.get("required"):
                findings.append(Finding("section-missing", "error",
                                        f"required section {title!r} is not in the manuscript", where=title))
            continue
        positions.append(found)
        limit = item.get("words_max")
        if limit is not None and sections[found].words > limit:
            findings.append(Finding("section-words", "error",
                                    f"{sections[found].words} words exceed the limit {limit}",
                                    where=_excerpt(sections[found].title), limit=limit,
                                    value=sections[found].words))
    if structure.get("order") == "strict" and positions != sorted(positions):
        findings.append(Finding("section-order", "warning", "matched sections are out of the profile's order"))


def _citation_rules(findings, bib, cites) -> None:
    for key in cites:
        if key not in bib:
            findings.append(Finding("citation-missing", "error", f"cited key {_excerpt(key)} has no bib entry",
                                    where=_excerpt(key)))
        elif not bib[key]:
            findings.append(Finding("reference-year", "warning", "cited entry has no year",
                                    where=_excerpt(key)))
    for key in bib:
        if key not in cites:
            findings.append(Finding("citation-uncited", "warning", "bib entry is never cited",
                                    where=_excerpt(key)))


def _template_rules(findings, prof, analysis) -> None:
    templates = prof.get("templates") or {}
    latex_class = templates.get("latex_class")
    if latex_class is None:
        findings.append(Finding("template-default", "warning",
                                "the profile names no LaTeX class (the default is article)"))
    elif latex_class != "other" and analysis.documentclass != latex_class:
        findings.append(Finding("template-class", "warning",
                                f"documentclass differs from the profile's {latex_class}"))
    style = (prof.get("references") or {}).get("bibtex_style")
    if style and analysis.bibstyle != style:
        findings.append(Finding("bib-style", "warning", f"bibliography style differs from {style}"))
    if templates.get("word_first") is True:
        findings.append(Finding("word-first", "warning", "the DOCX path is not built (M-F-D1)"))


def render_markdown(report: Report) -> str:
    lines = [f"# Manuscript check: {report.journal or '(no journal)'}", "",
             f"- Article type: {report.article_type or '(none)'}",
             f"- Profile sha256: {report.profile_sha256 or '(none)'}",
             f"- Manuscript sha256: {report.manuscript_sha256}",
             f"- Errors: {len(report.errors)}; warnings: {len(report.warnings)}", "", "## Counts", ""]
    lines += [f"- {key}: {value}" for key, value in sorted(report.counts.items())]
    lines += ["", "## Findings", ""]
    if not report.findings:
        lines.append("None.")
    for finding in report.findings:
        where = f" ({finding.where})" if finding.where else ""
        extra = f" [limit {finding.limit}, value {finding.value}]" if finding.limit is not None else ""
        lines.append(f"- {finding.severity} {finding.rule}{where}: {finding.message}{extra}")
    lines.append("")
    return "\n".join(lines)


def write_report(report: Report, run_dir) -> tuple:
    folder = Path(run_dir) / REPORT_DIR
    document = json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    json_path = folder / JSON_NAME
    md_path = folder / MD_NAME
    state.atomic_write(json_path, document.encode("utf-8"))
    state.atomic_write(md_path, render_markdown(report).encode("utf-8"))
    return json_path, md_path


def note_check(run_dir, report: Report) -> dict:
    return events.emit(Path(run_dir), "note.manuscript_check", actor="agent", journal=report.journal,
                       errors=len(report.errors), warnings=len(report.warnings),
                       manuscript_sha256=report.manuscript_sha256)
