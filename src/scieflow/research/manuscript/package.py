"""`scieflow manuscript package` (M-F 5.13): the submission folder report/submission/.

The refusals come before any write, in this order: open blockers (the review layer and open review-blocker
gates; a read that fails refuses closed), a structured valid journal profile, a check with no error, a build
whose record matches the current source tree and PDF, and no symbolic link in manuscript/. Only then is the
folder recreated. ScieFlow uploads nothing: the folder is for the user to submit.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from scieflow._vendor.researcher_core import contributors
from scieflow.core import events, gates
from scieflow.core.data import levels as _levels
from scieflow.core.design import reporting as design_reporting
from scieflow.core.review import service as review_service
from scieflow.research.journal import state
from scieflow.research.manuscript import build as build_mod
from scieflow.research.manuscript import check as check_mod
from scieflow.research.manuscript import checklist, letters, metadata, render, tex, ziputil

VERSION = 1
SUBMISSION = Path("report") / "submission"
PACKAGE_NAME = "package.json"
CHECKLIST_NAME = "submission-checklist.md"
BLOCKER_KIND = "review-blocker"
BUILD_HINT = "run scieflow manuscript build first"
L2_LINE = "derived from L2 data: share results only through `scieflow data release`"
DECLARATION_HEADINGS = {
    "coi": ("Competing interests", "coi"),
    "funding": ("Funding", "funding"),
    "ethics": ("Ethics", "ethics"),
    "data-availability": ("Data availability", "data_availability"),
    "code-availability": ("Code availability", "code_availability"),
    "ai-use": ("Use of AI", "ai_use"),
}
CREDIT_HEADING = "Author contributions (CRediT)"
ORCID_HEADING = "ORCID iDs"


class PackageError(Exception):
    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


@dataclass
class PackageResult:
    folder: Path
    files: dict
    package_sha256: str
    journal: str | None
    guidelines: list
    data_level: str

    def to_dict(self) -> dict:
        return {"version": VERSION, "folder": str(self.folder), "files": dict(sorted(self.files.items())),
                "package_sha256": self.package_sha256, "journal": self.journal,
                "guidelines": list(self.guidelines), "data_level": self.data_level}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _refuse_blockers(project, run: Path) -> None:
    try:
        open_review = list(review_service.blockers(project, run.name))
        open_gates = [g for g in gates.list_gates(run, state="open") if g.get("kind") == BLOCKER_KIND]
    except (ImportError, ValueError, OSError, RecursionError) as exc:
        raise PackageError("blockers", f"the review state could not be read ({type(exc).__name__}); "
                                       "refused (fail closed)") from exc
    count = len(open_review) + len(open_gates)
    if count:
        raise PackageError("blockers", f"{count} open blocker(s); see `scieflow review merge-check {run.name}`")


def _metadata(root: Path, folder: Path) -> dict:
    try:
        meta, problems = metadata.load(folder, root)
    except (tex.TexError, ValueError) as exc:
        raise PackageError("metadata", str(exc)) from exc
    if meta is None:
        raise PackageError("metadata-missing", "manuscript/metadata.yml is required")
    errors = [p for p in problems if p.level == "error"]
    if errors:
        raise PackageError("metadata", f"metadata.yml: {errors[0].rule} at {errors[0].path}")
    return meta


def _profile(root: Path, folder: Path, meta: dict) -> dict:
    try:
        prof = render._profile(root, folder, meta)
    except render.RenderError as exc:
        raise PackageError(exc.rule, exc.message) from exc
    if prof is None:
        raise PackageError("profile-missing", "no structured journal profile resolves: name the journal "
                                              "in metadata.yml")
    return prof


def _check(root: Path, run: Path, guidelines) -> check_mod.Report:
    try:
        report = check_mod.run_check(root, run, guidelines=tuple(guidelines))
    except check_mod.CheckError as exc:
        raise PackageError("check", str(exc)) from exc
    if report.errors:
        rules = sorted({f.rule for f in report.errors})
        raise PackageError("check", f"the check has {len(report.errors)} error(s): {', '.join(rules)}; "
                                    f"see `scieflow manuscript check {run.name}`")
    return report


def _built(folder: Path, tree_hash: str) -> bytes:
    """The PDF bytes, when the build record is ok, matches the current source tree and names this PDF."""
    record_path = folder / build_mod.BUILD_DIR / build_mod.BUILD_JSON
    pdf_path = folder / build_mod.BUILD_DIR / "main.pdf"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        pdf = pdf_path.read_bytes()
    except (OSError, ValueError):
        raise PackageError("build-missing", BUILD_HINT) from None
    if not isinstance(record, dict) or record.get("ok") is not True:
        raise PackageError("build-missing", BUILD_HINT)
    if record.get("source_tree_sha256") != tree_hash:
        raise PackageError("build-stale", f"the sources changed after the last build; {BUILD_HINT}")
    if record.get("pdf_sha256") != _sha(pdf):
        raise PackageError("build-pdf", f"the PDF does not match its build record; {BUILD_HINT}")
    return pdf


def _no_links(folder: Path) -> None:
    try:
        build_mod._refuse_links(folder)
    except build_mod.BuildError as exc:
        raise PackageError("symlink", exc.message) from exc


def _source_entries(folder: Path) -> dict:
    """The source tree of 5.12 step 1 plus build/main.bbl when present."""
    entries: dict = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(folder)
        if rel.parts[0] in tex.TREE_EXCLUDED_DIRS:
            continue
        if path.name.endswith(tex.TEX_BYPRODUCTS) or ".bak-" in path.name:
            continue
        entries[rel.as_posix()] = path.read_bytes()
    bbl = folder / build_mod.BUILD_DIR / "main.bbl"
    if bbl.is_file() and not bbl.is_symlink():
        entries["build/main.bbl"] = bbl.read_bytes()
    return entries


def _cover_letter(folder: Path, meta: dict, journal_name) -> bytes:
    own = folder / letters.LETTERS / letters.COVER
    if own.is_file() and not own.is_symlink():
        return own.read_bytes()
    return letters.cover_letter_text(meta, journal_name).encode("utf-8")


def _credit_text(authors: list) -> str:
    lines = []
    for author in authors:
        roles = [contributors._CREDIT_META.get(role, (role,))[0]
                 for role in author.get("credit") or [] if isinstance(role, str)]
        if roles:
            lines.append(f"{author.get('name', '')}: {', '.join(roles)}.")
    return "\n".join(lines) if lines else "No CRediT roles recorded."


def _orcid_text(authors: list) -> str:
    lines = []
    for author in authors:
        orcid = author.get("orcid")
        lines.append(f"{author.get('name', '')}: {orcid}" if isinstance(orcid, str) else
                     f"{author.get('name', '')}: none recorded")
    return "\n".join(lines) if lines else "No authors recorded."


def _declared(texts: dict, key: str) -> bool:
    value = texts.get(DECLARATION_HEADINGS[key][1])
    return isinstance(value, str) and bool(value.strip())


def declarations_text(meta: dict, required) -> str:
    """The declarations in the profile's required order, then the others that have a text."""
    texts = meta.get("declarations") or {}
    authors = meta.get("authors") or []
    order = list(required) + [key for key in DECLARATION_HEADINGS
                              if key not in required and _declared(texts, key)]
    if "credit" not in order:
        order.append("credit")
    blocks = []
    for key in order:
        if key in DECLARATION_HEADINGS:
            heading, field = DECLARATION_HEADINGS[key]
            value = texts.get(field)
            body = value.strip() if isinstance(value, str) and value.strip() else "(missing: no text in metadata.yml)"
        elif key == "credit":
            heading, body = CREDIT_HEADING, _credit_text(authors)
        elif key == "orcid":
            heading, body = ORCID_HEADING, _orcid_text(authors)
        else:
            continue
        blocks.append(f"## {heading}\n\n{body}\n")
    return "\n".join(blocks)


def _verified(root: Path, guideline: str) -> bool:
    try:
        return checklist.is_verified(design_reporting.get(root, guideline))
    except design_reporting.ReportingError:
        return False


def _profile_line(prof: dict, report: check_mod.Report) -> str:
    sources = (prof.get("provenance") or {}).get("sources") or []
    retrieved = sorted(s["retrieved_at"] for s in sources
                       if isinstance(s, dict) and isinstance(s.get("retrieved_at"), str))
    when = (f"the oldest source page was retrieved {retrieved[0]}" if retrieved
            else "no source pages are recorded")
    stale = any(f.rule == "profile-stale" for f in report.findings)
    state_text = "stale (older than its max age): research the journal again" if stale else "not stale"
    return f"- Journal profile: {when}; {state_text}."


def checklist_text(*, prof: dict, report: check_mod.Report, held: set, left: list, unverified: list,
                   level: str, journal_name) -> str:
    templates = prof.get("templates") or {}
    lines = ["# Submission checklist", "",
             f"Journal: {journal_name or report.journal} ({report.journal}). "
             f"Article type: {report.article_type}.", "",
             "## Required by the journal", ""]
    required = (prof.get("submission") or {}).get("required_files") or []
    for item in required:
        if item["id"] in held:
            lines.append(f"- [x] {item['label']}")
        else:
            lines.append(f"- [ ] {item['label']} (prepare this yourself)")
    if not required:
        lines.append("- The profile lists no required files.")
    lines += ["", "## Still yours to do", ""]
    for token in left:
        lines.append(f"- Cover letter placeholder still present: `{token}`")
    if templates.get("word_first") is True:
        lines.append("- The journal is Word-first (templates.word_first): the DOCX path is not built in "
                     "ScieFlow; prepare the DOCX yourself.")
    for guideline in unverified:
        lines.append(f"- Reporting checklist `{guideline}` is an unverified skeleton: check every item "
                     "against its source before submission.")
    lines.append(_profile_line(prof, report))
    lines.append("- ORCID and ROR were checked offline only (format and checksum).")
    lines.append(f"- Data level {level}: {L2_LINE}." if level == "L2" else f"- Data level {level}.")
    lines.append("- ScieFlow uploads nothing; you submit.")
    return "\n".join(lines) + "\n"


def _write(run: Path, files: dict, package_bytes: bytes) -> Path:
    for part in (run / "report", run / SUBMISSION):
        if part.is_symlink() or (part.exists() and not part.is_dir()):
            raise PackageError("symlink", f"{part.relative_to(run).as_posix()} is a symbolic link or not a "
                                          "folder; refused")
    sub = run / SUBMISSION
    if sub.exists():
        shutil.rmtree(sub)
    sub.mkdir(parents=True)
    for name, data in sorted(files.items()):
        state.atomic_write(sub / name, data)
    state.atomic_write(sub / PACKAGE_NAME, package_bytes)
    return sub


def package_run(project, run_dir, *, guidelines=()) -> PackageResult:
    root = Path(project.root)
    run = Path(run_dir)
    folder = run / "manuscript"
    if folder.is_symlink() or not folder.is_dir():
        raise PackageError("manuscript-missing", f"no manuscript folder in {run.name}")

    _refuse_blockers(project, run)
    meta = _metadata(root, folder)
    prof = _profile(root, folder, meta)
    report = _check(root, run, guidelines)
    pdf = _built(folder, report.manuscript_sha256)
    _no_links(folder)

    journal_name = (prof.get("identity") or {}).get("name")
    level = _levels.run_level(run)
    cover = _cover_letter(folder, meta, journal_name)
    left = letters.placeholders(cover.decode("utf-8"))
    unverified = [gid for gid in sorted(report.filled) if not _verified(root, gid)]

    files: dict = {
        "main.pdf": pdf,
        "source.zip": ziputil.zip_bytes(_source_entries(folder)),
        "cover-letter.tex": cover,
        "declarations.md": declarations_text(meta, (prof.get("declarations") or {}).get("required") or []).encode(
            "utf-8"),
        "manuscript-check.md": check_mod.render_markdown(report).encode("utf-8"),
    }
    highlights = meta.get("highlights") or []
    if highlights:
        files["highlights.txt"] = ("\n".join(highlights) + "\n").encode("utf-8")
    for guideline, text in sorted(report.filled.items()):
        files[f"reporting-checklist-{guideline}.md"] = text.encode("utf-8")
    held = {"manuscript-pdf", "source-files", "cover-letter", "declarations"}
    if "highlights.txt" in files:
        held.add("highlights")
    if report.filled:
        held.add("reporting-checklist")
    files[CHECKLIST_NAME] = checklist_text(prof=prof, report=report, held=held, left=left,
                                           unverified=unverified, level=level,
                                           journal_name=journal_name).encode("utf-8")

    manifest = {name: _sha(data) for name, data in files.items()}
    record = {
        "version": VERSION, "run": run.name,
        "journal": {"slug": report.journal, "profile_sha256": report.profile_sha256},
        "article_type": report.article_type,
        "check_sha256": _sha(json.dumps(report.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")),
        "build": {"pdf_sha256": _sha(pdf), "source_tree_sha256": report.manuscript_sha256},
        "files": manifest,
        "guidelines": [{"id": gid, "verified": gid not in unverified} for gid in sorted(report.filled)],
        "blockers": 0,
        "data_level": level,
    }
    package_bytes = (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    package_sha = _sha(package_bytes)
    sub = _write(run, files, package_bytes)
    events.emit(run, "note.manuscript_package", actor="agent", journal=report.journal,
                files=len(files) + 1, package_sha256=package_sha)
    return PackageResult(folder=sub, files={**manifest, PACKAGE_NAME: package_sha}, package_sha256=package_sha,
                         journal=report.journal, guidelines=record["guidelines"], data_level=level)
