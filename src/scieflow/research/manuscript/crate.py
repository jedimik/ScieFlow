"""The RO-Crate export of a manuscript (M-G 5.14, M-G-D13): one `ro-crate-metadata.json` (RO-Crate 1.1).

The crate describes the manuscript source files, the authors (name and ORCID, never an e-mail), every claim of the
ledger, the DOIs the claims cite, the runs and data files they rest on, with sha256 identifiers. It is metadata
only: no file is copied into it and no PROV is written (M-G-D13). An unreleased L2 data file appears only as an
id-only entity. The crate is built from the ledger and the run's own files; it makes no network call and writes
deterministic bytes (sorted keys, no timestamps).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from scieflow._vendor.researcher_core.contributors import MetadataError, validate_orcid
from scieflow.core import events
from scieflow.research.journal import state
from scieflow.research.manuscript import points
from scieflow.research.manuscript.claims import evidence as ev
from scieflow.research.manuscript.claims import ledger as ledger_mod
from scieflow.research.manuscript.claims import resolve as resolve_mod
from scieflow.research.manuscript import metadata as metadata_mod

CONTEXT = "https://w3id.org/ro/crate/1.1/context"
CONFORMS = "https://w3id.org/ro/crate/1.1"
DESCRIPTOR = "ro-crate-metadata.json"
OUT = Path("report") / "ro-crate" / DESCRIPTOR
SHA_RE = re.compile(r"[0-9a-f]{64}")
RUN_MANIFEST_RE = re.compile(r"experiments/([^/]+)/runs/(.+)/manifest\.json")
UNRELEASED = "data-unreleased"
ENCODING = {
    ".tex": "application/x-tex", ".bib": "application/x-bibtex", ".yml": "application/yaml",
    ".yaml": "application/yaml", ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".json": "application/json", ".txt": "text/plain", ".md": "text/markdown",
    ".pdf": "application/pdf", ".png": "image/png",
}


class CrateError(ValueError):
    """The crate is refused: `rule` names the refusal, `message` says why (no values)."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def _refuse_links(run: Path) -> None:
    """Refuse a symbolic link at report/, report/ro-crate or the crate file (M-G 5.1, 5.16)."""
    for part in (run / "report", run / "report" / "ro-crate", run / OUT):
        if part.is_symlink():
            raise CrateError("crate-path", f"{part.relative_to(run).as_posix()} is a symbolic link")


def _file_entity(run: Path, rel: str) -> dict | None:
    """A File entity for a run-relative regular file, or None when the file is absent."""
    try:
        path = ev.regular_file(run, rel)
    except ev.EvidenceError as exc:
        raise CrateError(exc.rule, exc.message) from exc
    if path is None:
        return None
    return {"@id": rel, "@type": "File", "name": Path(rel).name,
            "identifier": f"sha256:{ev.sha256_file(path)}",
            "encodingFormat": ENCODING.get(Path(rel).suffix.lower(), "application/octet-stream")}


def _stage_id(graph: dict, name: str, digest: str) -> str:
    """The stage entity id of a run's script. `#stage-<name>` unless another run of that stage name already
    recorded a different script digest; then `#stage-<name>-<sha12>`, so neither run's script is overwritten.
    """
    base = f"#stage-{name}"
    identifier = f"sha256:{digest}"
    existing = graph.get(base)
    if existing is None or existing["identifier"] == identifier:
        return base
    candidate = f"{base}-{digest[:12]}"
    other = graph.get(candidate)
    if other is not None and other["identifier"] != identifier:
        raise CrateError("run-manifest", f"stage {name} has script hashes that collide in the crate")
    return candidate


def _run_action(run: Path, outcome, graph: dict, files: dict) -> str | None:
    """The CreateAction of a run reference (its manifest names the run); None for a campaign reference."""
    for rel in outcome.files:
        match = RUN_MANIFEST_RE.fullmatch(rel)
        if match:
            break
    else:
        return None
    manifest_rel = rel
    campaign, rid = match.groups()
    try:
        path = ev.regular_file(run, manifest_rel)
        doc = json.loads(path.read_text(encoding="utf-8")) if path is not None else None
    except (OSError, ValueError, ev.EvidenceError) as exc:
        raise CrateError("run-manifest", f"the manifest of run {rid} cannot be read") from exc
    if not isinstance(doc, dict):
        raise CrateError("run-manifest", f"the manifest of run {rid} cannot be read")
    action_id = f"#run-{campaign}-{rid.replace('/', '-')}"
    action: dict = {"@id": action_id, "@type": "CreateAction", "name": f"run {campaign}/{rid}"}
    if isinstance(doc.get("finished"), str):
        action["endTime"] = doc["finished"]
    stage = doc.get("stage") if isinstance(doc.get("stage"), dict) else {}
    name, digest = stage.get("name"), stage.get("script_sha256")
    if isinstance(name, str) and name and isinstance(digest, str) and SHA_RE.fullmatch(digest):
        stage_id = _stage_id(graph, name, digest)
        graph[stage_id] = {"@id": stage_id, "@type": "SoftwareApplication", "name": name,
                           "identifier": f"sha256:{digest}"}
        action["instrument"] = {"@id": stage_id}
    results = []
    for rel in sorted(outcome.files):
        if rel == manifest_rel:
            continue
        entity = files.get(rel) or _file_entity(run, rel)
        if entity is not None:
            files[rel] = entity
            results.append({"@id": rel})
    action["result"] = results
    graph[action_id] = action
    return action_id


def _outcome(ctx, ref: dict):
    """The resolution outcome of one reference, or None when it cannot be resolved safely (the check reports it)."""
    try:
        return resolve_mod.resolve_ref(ctx, ref)
    except ev.EvidenceError:
        return None


def _claim_entity(run, record, ctx, graph: dict, files: dict) -> dict:
    based: list[str] = []
    citations: list[str] = []
    for ref in record.get("refs", []):
        kind, ident = ref["type"], ref["id"]
        if kind == "doi":
            doi_id = f"https://doi.org/{ident}"
            graph[doi_id] = {"@id": doi_id, "@type": "ScholarlyArticle", "identifier": ident}
            citations.append(doi_id)
            continue
        outcome = _outcome(ctx, ref)
        if outcome is None:
            continue
        unsafe = [finding for finding in outcome.findings if finding["rule"] == "ref-unsafe"]
        if unsafe:
            raise CrateError("ref-unsafe", f"claim {record['id']}: {unsafe[0]['message']}")
        if any(finding["rule"] == UNRELEASED for finding in outcome.findings):
            entity_id = f"#data-{ident}"
            graph[entity_id] = {"@id": entity_id, "@type": "CreativeWork", "name": ident,
                                "conditionsOfAccess": "L2 data, not released"}
            based.append(entity_id)
            continue
        action = _run_action(run, outcome, graph, files) if kind == "run" else None
        if action is not None:
            based.append(action)
            continue
        for rel in sorted(outcome.files):
            entity = files.get(rel) or _file_entity(run, rel)
            if entity is not None:
                files[rel] = entity
                based.append(rel)
    claim: dict = {"@id": f"#claim-{record['id']}", "@type": "CreativeWork", "name": f"claim {record['id']}",
                   "text": record["text"], "additionalType": record["unit"]}
    source = f"manuscript/{record['file']}"
    entity = files.get(source) or _file_entity(run, source)
    if entity is None:
        raise CrateError("crate-file", f"{source} is missing from the run")
    files[source] = entity
    claim["isPartOf"] = {"@id": source}
    if citations:
        claim["citation"] = [{"@id": doi} for doi in sorted(set(citations))]
    if based:
        claim["isBasedOn"] = [{"@id": ident} for ident in sorted(set(based))]
    return claim


def build(project, run_folder) -> dict:
    """The RO-Crate document of the run (not written). Raises CrateError or ClaimsError (no ledger)."""
    run = Path(run_folder)
    _refuse_links(run)
    records = ledger_mod.read(run)
    manuscript = run / "manuscript"
    try:
        sources = points.source_files(manuscript)
    except points.PointError as exc:
        raise CrateError(exc.rule, exc.message) from exc
    meta, _problems = metadata_mod.load(manuscript, project.root)
    meta = meta if isinstance(meta, dict) else {}
    try:
        ctx = resolve_mod.open_context(run, (), {})
    except ev.EvidenceError as exc:
        raise CrateError(exc.rule, exc.message) from exc

    graph: dict[str, dict] = {}
    files: dict[str, dict] = {}
    for rel in sources:
        entity = _file_entity(run, f"manuscript/{rel}")
        if entity is not None:
            files[entity["@id"]] = entity
    claims = [_claim_entity(run, record, ctx, graph, files) for record in records]

    persons = []
    for index, author in enumerate(meta.get("authors") or []):
        if not isinstance(author, dict):
            continue
        person_id = f"#person-{index}"
        person = {"@id": person_id, "@type": "Person", "name": str(author.get("name", ""))}
        if author.get("orcid"):
            try:
                person["identifier"] = validate_orcid(str(author["orcid"]))
            except MetadataError:
                pass  # a malformed or bad-checksum ORCID is not written; the metadata check reports it
        graph[person_id] = person
        persons.append({"@id": person_id})

    for claim in claims:
        graph[claim["@id"]] = claim
    title = meta.get("title") if isinstance(meta.get("title"), str) else run.name
    root = {"@id": "./", "@type": "Dataset", "name": title,
            "hasPart": [{"@id": rel} for rel in sorted(files)], "author": persons}
    descriptor = {"@id": DESCRIPTOR, "@type": "CreativeWork", "conformsTo": {"@id": CONFORMS},
                  "about": {"@id": "./"}}
    entities = sorted(list(graph.values()) + list(files.values()), key=lambda entity: entity["@id"])
    return {"@context": CONTEXT, "@graph": [descriptor, root, *entities]}


def serialise(document: dict) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write(run_folder, document: dict) -> Path:
    """Write report/ro-crate/ro-crate-metadata.json (atomic; a symbolic link on the path is refused)."""
    run = Path(run_folder)
    _refuse_links(run)
    target = run / OUT
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        state.atomic_write(target, serialise(document))
    except (state.StateError, OSError) as exc:
        raise CrateError("crate-path", f"the crate cannot be written: {exc}") from exc
    return target


def note_written(run_folder, document: dict) -> dict:
    """One note.manuscript_crate event with counts only (no values, no e-mail, no paths)."""
    graph = document["@graph"][2:]
    counts = {
        "entities": len(graph),
        "files": sum(1 for entity in graph if entity.get("@type") == "File"),
        "claims": sum(1 for entity in graph if entity["@id"].startswith("#claim-")),
        "unreleased": sum(1 for entity in graph if entity["@id"].startswith("#data-")),
    }
    return events.emit(Path(run_folder), "note.manuscript_crate", "system", **counts)
