"""Claim references resolved against the run's evidence (M-G 5.4): data, run and doi references.

A resolution reads files (confined, see evidence.py) and the run's own data.yml and experiments/ records. It makes
no network call and runs no subprocess. A finding names a reference, a run-relative path or a rule id, never a
value read from an evidence file (M-G 5.16).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scieflow.core.data import levels
from scieflow.core.data.release import ID_PATTERN
from scieflow.core.review.claims import safe_doi
from scieflow.experiments import manifest
from scieflow.research.manuscript.claims import evidence as ev

MANIFEST_FILE = "inputs/manifest.yml"
SELECTED_FILE = "report/selected_dois.txt"
ERROR_RULES = frozenset({"ref-unresolved", "ref-ambiguous", "ref-unsafe", "ref-too-long", "ref-unsafe-doi",
                         "artifact-altered", "data-unreleased"})


def finding(rule: str, message: str) -> dict:
    severity = "error" if rule in ERROR_RULES else "warning"
    return {"rule": rule, "severity": severity, "message": message}


@dataclass
class Outcome:
    """One reference's result: its findings, the evidence files a pin covers, and whether it is pinned."""
    ref: dict
    findings: list = field(default_factory=list)
    files: list = field(default_factory=list)
    pinned: bool = False


@dataclass
class Context:
    run: Path
    level: str
    bib_keys: frozenset
    bib_dois: dict
    selected: frozenset | None
    records: levels.DataFile | None
    _campaigns: dict = field(default_factory=dict)


def open_context(run_folder, bib_keys, bib_dois) -> Context:
    """The resolution context of one build. Raises EvidenceError when report/selected_dois.txt is unsafe."""
    run = Path(run_folder)
    selected = None
    path = ev.regular_file(run, SELECTED_FILE)
    if path is not None:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            raise ev.EvidenceError("ref-unsafe", f"{SELECTED_FILE} cannot be read")
        selected = frozenset(s for s in (line.strip().lower() for line in lines)
                             if s and not s.startswith("#"))
    got = levels.read(run)
    records = got if isinstance(got, levels.DataFile) else None
    return Context(run=run, level=levels.run_level(run), bib_keys=frozenset(bib_keys),
                   bib_dois=dict(bib_dois), selected=selected, records=records)


def resolve_ref(ctx: Context, ref: dict) -> Outcome:
    try:
        if ref["type"] == "data":
            return _data(ctx, ref)
        if ref["type"] == "run":
            return _run(ctx, ref)
        if ref["type"] == "doi":
            return _doi(ctx, ref)
    except ev.EvidenceError as exc:
        return Outcome(ref, [finding(exc.rule, exc.message)])
    except OSError:
        return Outcome(ref, [finding("ref-unresolved", "the evidence of this reference cannot be read")])
    return Outcome(ref, [finding("ref-unresolved", "the reference type is not known")])


def resolve_cite(ctx: Context, key: str) -> list:
    """Findings of one \\cite key: it must have a bibliography entry, and the entry should declare a DOI."""
    if key not in ctx.bib_keys:
        return [finding("ref-unresolved", f"cite key {key} has no bibliography entry")]
    if key not in ctx.bib_dois:
        return [finding("doi-missing", f"bibliography entry {key} declares no DOI")]
    return []


# ---- data -------------------------------------------------------------------------------------------------

def _manifest_hits(ctx: Context, ident: str) -> list:
    path = ev.regular_file(ctx.run, MANIFEST_FILE)
    if path is None:
        return []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError, RecursionError):
        raise ev.EvidenceError("ref-unresolved", f"{MANIFEST_FILE} cannot be read as a data package manifest")
    artifacts = doc.get("artifacts") if isinstance(doc, dict) else None
    if not isinstance(artifacts, list):
        raise ev.EvidenceError("ref-unresolved", f"{MANIFEST_FILE} has no artifacts list")
    hits = []
    for art in artifacts:
        if isinstance(art, dict) and art.get("id") == ident:
            problem = ev.path_problem(art.get("file"))
            if problem:
                raise ev.EvidenceError("ref-unsafe", f"a data package file {problem}")
            hits.append(f"inputs/{art['file']}")
    return hits


def _record_path_ok(rel: str, prefix: tuple) -> bool:
    parts = rel.split("/")
    return tuple(parts[:len(prefix)]) == prefix and len(parts) == len(prefix) + 1


def _data(ctx: Context, ref: dict) -> Outcome:
    ident = ref["id"]
    if not ID_PATTERN.fullmatch(ident):
        return Outcome(ref, [finding("ref-unresolved", "the data reference is not a valid id")])
    matches = [("manifest", rel) for rel in _manifest_hits(ctx, ident)]
    if ctx.records is not None:
        matches += [("release", rec) for rec in ctx.records.released if rec.get("id") == ident]
        matches += [("import", rec) for rec in ctx.records.imports if rec.get("release") == ident]
    if not matches:
        return Outcome(ref, [finding("ref-unresolved", f"no data artifact has the id {ident}")])
    if len(matches) > 1:
        return Outcome(ref, [finding("ref-ambiguous", f"the data id {ident} names {len(matches)} files")])
    source, item = matches[0]
    if source == "manifest":
        rel = item
        if ev.regular_file(ctx.run, rel) is None:
            return Outcome(ref, [finding("ref-unresolved", f"the data package names {rel}, which is missing")])
        findings = []
        if ctx.level == "L2":
            findings.append(finding("data-unreleased",
                                    f"data {ident} is only in the data package of an L2 run; release it first"))
        return Outcome(ref, findings, [rel], True)
    rec = item
    rel = str(rec.get("file", ""))
    if source == "release":
        if not _record_path_ok(rel, ("released", str(rec.get("id")))):
            raise ev.EvidenceError("ref-unsafe", f"release {ident} names a file outside its folder")
    else:
        prefix = ("inputs", "released", f"{rec.get('from')}-{rec.get('release')}")
        if not _record_path_ok(rel, prefix):
            raise ev.EvidenceError("ref-unsafe", f"import of {ident} names a file outside its folder")
    path = ev.regular_file(ctx.run, rel)
    if path is None:
        return Outcome(ref, [finding("artifact-altered", f"{rel} is missing from the run")])
    if ev.sha256_file(path) != rec.get("sha256"):
        return Outcome(ref, [finding("artifact-altered", f"{rel} differs from its recorded sha256")],
                       [rel], True)
    return Outcome(ref, [], [rel], True)


# ---- run and campaign -------------------------------------------------------------------------------------

def _campaign_names(ctx: Context) -> list:
    root = ctx.run / "experiments"
    if not os.path.lexists(root):
        return []
    if root.is_symlink() or not root.is_dir():
        raise ev.EvidenceError("ref-unsafe", "experiments/ is not a folder")
    names = []
    for entry in sorted(root.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        if ev.regular_file(ctx.run, f"experiments/{entry.name}/results.json") is not None:
            names.append(entry.name)
    return names


def _campaign(ctx: Context, campaign: str):
    """(results document, verify report) of a campaign, read once per build."""
    if campaign not in ctx._campaigns:
        folder = ctx.run / "experiments" / campaign
        try:
            doc = manifest.load_results(folder)
        except ValueError:
            raise ev.EvidenceError("ref-unresolved", f"experiments/{campaign}/results.json cannot be read")
        ctx._campaigns[campaign] = (doc, manifest.verify_campaign(folder))
    return ctx._campaigns[campaign]


def _run_ids(ctx: Context, campaign: str) -> set:
    doc, _report = _campaign(ctx, campaign)
    return {entry["run_id"] for entry in doc["runs"]}


def _candidates(ctx: Context, ident: str) -> list:
    """(campaign, run id or None) the reference may name: a campaign first, then campaign/run, then a bare run id."""
    if ev.regular_file(ctx.run, f"experiments/{ident}/results.json") is not None:
        return [(ident, None)]
    parts = ident.split("/")
    qualified = []
    for cut in range(1, len(parts)):
        campaign, rid = "/".join(parts[:cut]), "/".join(parts[cut:])
        if ev.regular_file(ctx.run, f"experiments/{campaign}/results.json") is not None \
                and rid in _run_ids(ctx, campaign):
            qualified.append((campaign, rid))
    if qualified:
        return qualified
    return [(campaign, ident) for campaign in _campaign_names(ctx) if ident in _run_ids(ctx, campaign)]


def _run(ctx: Context, ref: dict) -> Outcome:
    ident = ref["id"]
    problem = ev.path_problem(ident)
    if problem:
        raise ev.EvidenceError("ref-unsafe", f"the run reference {problem}")
    matches = _candidates(ctx, ident)
    if not matches:
        return Outcome(ref, [finding("ref-unresolved", f"no campaign or run has the id {ident}")])
    if len(matches) > 1:
        return Outcome(ref, [finding("ref-ambiguous", f"the run id {ident} names {len(matches)} runs")])
    campaign, rid = matches[0]
    if rid is None:
        return _campaign_outcome(ctx, ref, campaign)
    return _run_outcome(ctx, ref, campaign, rid)


def _campaign_outcome(ctx: Context, ref: dict, campaign: str) -> Outcome:
    _doc, report = _campaign(ctx, campaign)
    findings = [finding("artifact-altered", f"run {p.get('run_id')} fails a recorded hash ({p['rule']})")
                for p in report.problems]
    findings += [finding("run-unverifiable", f"run {n.get('run_id')} has no recorded manifest")
                 for n in report.notes if n.get("rule") == "legacy-run"]
    base = f"experiments/{campaign}"
    files = []
    for name in ("results.json", "results.csv", "stats.json"):
        if ev.regular_file(ctx.run, f"{base}/{name}") is not None:
            files.append(f"{base}/{name}")
    files += ev.folder_files(ctx.run, f"{base}/tables") or []
    return Outcome(ref, findings, sorted(files), True)


def _run_outcome(ctx: Context, ref: dict, campaign: str, rid: str) -> Outcome:
    _doc, report = _campaign(ctx, campaign)
    findings = [finding("artifact-altered", f"run {rid} fails a recorded hash ({p['rule']})")
                for p in report.problems if p.get("run_id") == rid]
    findings += [finding("run-unverifiable", f"run {rid} has no recorded manifest")
                 for n in report.notes if n.get("run_id") == rid and n.get("rule") == "legacy-run"]
    base = f"experiments/{campaign}/runs/{rid}"
    files = [f"{base}/{name}" for name in ("metrics.json", "manifest.json")
             if ev.regular_file(ctx.run, f"{base}/{name}") is not None]
    files += ev.folder_files(ctx.run, f"{base}/artifacts") or []
    return Outcome(ref, findings, sorted(files), True)


# ---- doi --------------------------------------------------------------------------------------------------

def _doi(ctx: Context, ref: dict) -> Outcome:
    doi = ref["id"]
    key = ref.get("key")
    if key is not None:
        declared = safe_doi(ctx.bib_dois.get(key)) if key in ctx.bib_keys else None
        if key not in ctx.bib_keys or declared != doi:
            return Outcome(ref, [finding("ref-unresolved", f"cite key {key} does not declare this DOI")])
    elif doi not in {safe_doi(value) for value in ctx.bib_dois.values()}:
        return Outcome(ref, [finding("ref-unresolved", "the DOI is not declared in the bibliography")])
    findings = []
    if ctx.selected is not None and doi.lower() not in ctx.selected:
        findings.append(finding("doi-unselected", "the DOI is not in report/selected_dois.txt"))
    return Outcome(ref, findings, [], False)
