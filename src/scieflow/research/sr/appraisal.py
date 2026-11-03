"""Risk-of-bias and GRADE worksheets of a systematic review (M-H plan 5.10, M-H-D9).

Tool definitions ship in config/sr/ (rob2, robins-i, quadas-2, grade) and are loaded strictly. Worksheets are
single-assessor: a person or an agent writes the judgements and support text; this module checks them
deterministically and computes GRADE certainty from the recorded levels. Code checks and computes; it never
invents a judgement, and it never stores a certainty as truth.

Writers run verify.preflight first and write nothing on refusal. Record events carry judgements and levels
(closed vocabularies), never the support text or the author's reasons. Error messages name the file, the
domain or outcome and the rule, never support text.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from scieflow.core import events, store
from scieflow.research.sr import ledger, protocol, screening, verify
from scieflow.research.sr.errors import SRError

FORMAT = 1
ROB_TOOLS = ("rob2", "robins-i", "quadas-2")
GRADE_TOOL = "grade"
ROB_DIR = "sr/rob"
GRADE_REL = "sr/grade.yml"
SUMMARY_REL = "sr/grade/summary-of-findings.md"
KIND_ROB, KIND_GRADE = "rob", "grade"
RECORD_RE = re.compile(r"^R\d{5}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
ROB_TOP = frozenset({"version", "id", "name", "source", "verification", "domains", "judgements", "overall",
                     "applicability"})
ROB_TOP_REQUIRED = frozenset({"version", "id", "name", "source", "verification", "domains", "judgements",
                              "overall"})
GRADE_TOP = frozenset({"version", "id", "name", "source", "verification", "starting", "certainty", "downgrade",
                       "upgrade"})
WORKSHEET_KEYS = frozenset({"format", "record_id", "tool", "domains", "overall"})
DOMAIN_KEYS = frozenset({"id", "judgement", "support", "applicability"})
OVERALL_KEYS = frozenset({"judgement", "rationale"})
GRADE_TOP_WORKSHEET = frozenset({"format", "outcomes"})
GRADE_ENTRY_KEYS = frozenset({"outcome", "design", "studies", "downgrade", "upgrade", "effect_text"})
DESIGNS = ("randomised", "non-randomised")


# ------------------------------------------------------------------ tool definitions (strict)


def _read_yaml_file(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise SRError(f"{path.name} cannot be read as YAML") from None


def _mapping(value, where: str, allowed: frozenset, required: frozenset) -> dict:
    if not isinstance(value, dict):
        raise SRError(f"{where} must be a mapping")
    unknown = sorted(str(k) for k in value if k not in allowed)
    if unknown:
        raise SRError(f"{where}: unknown key {unknown[0]!r}")
    missing = sorted(k for k in required if k not in value)
    if missing:
        raise SRError(f"{where}: missing key {missing[0]!r}")
    return value


def _text(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SRError(f"{where} must be non-empty text")
    return value


def _unique_list(value, where: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SRError(f"{where} must be a non-empty list")
    out = [_text(v, f"{where} item") for v in value]
    if len(set(out)) != len(out):
        raise SRError(f"{where} holds a duplicate value")
    return out


def _source_and_verification(raw: dict, path: Path) -> None:
    source = _mapping(raw["source"], f"{path.name}: source", frozenset({"citation", "doi"}),
                      frozenset({"citation", "doi"}))
    _text(source["citation"], f"{path.name}: source.citation")
    if source["doi"] is not None and not isinstance(source["doi"], str):
        raise SRError(f"{path.name}: source.doi must be text or null")
    verification = _mapping(raw["verification"], f"{path.name}: verification",
                            frozenset({"status", "note", "verified_by", "verified_at"}),
                            frozenset({"status", "note", "verified_by", "verified_at"}))
    if verification["status"] not in ("unverified", "verified"):
        raise SRError(f"{path.name}: verification.status must be unverified or verified")
    if verification["status"] == "verified" and not (verification["verified_by"] and verification["verified_at"]):
        raise SRError(f"{path.name}: a verified tool needs verified_by and verified_at")


def _load_checked(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise SRError(f"tool definition {path.name} is missing or not a plain file")
    raw = _read_yaml_file(path)
    if not isinstance(raw, dict):
        raise SRError(f"{path.name} must be a mapping")
    if raw.get("version") != 1:
        raise SRError(f"{path.name}: version must be 1")
    if raw.get("id") != path.stem:
        raise SRError(f"{path.name}: id must match the file name")
    return raw


def load_rob_tool(path: Path) -> dict:
    """A risk-of-bias tool definition, strictly validated (unknown key, duplicate domain, empty list refused)."""
    path = Path(path)
    raw = _load_checked(path)
    _mapping(raw, path.name, ROB_TOP, ROB_TOP_REQUIRED)
    if raw["id"] not in ROB_TOOLS:
        raise SRError(f"{path.name}: id must be one of {', '.join(ROB_TOOLS)}")
    _text(raw["name"], f"{path.name}: name")
    _source_and_verification(raw, path)
    domains = raw["domains"]
    if not isinstance(domains, list) or not domains:
        raise SRError(f"{path.name}: domains must be a non-empty list")
    ids: list[str] = []
    for row in domains:
        _mapping(row, f"{path.name}: domain", frozenset({"id", "label"}), frozenset({"id", "label"}))
        if not ID_RE.match(str(row["id"])):
            raise SRError(f"{path.name}: domain id {row['id']!r} is not a valid id")
        _text(row["label"], f"{path.name}: domain {row['id']} label")
        if row["id"] in ids:
            raise SRError(f"{path.name}: duplicate domain id {row['id']}")
        ids.append(row["id"])
    judgements = _unique_list(raw["judgements"], f"{path.name}: judgements")
    overall = _mapping(raw["overall"], f"{path.name}: overall", frozenset({"rule", "extra", "extra_requires"}),
                       frozenset({"rule", "extra"}))
    if overall["rule"] not in ("worst", "none"):
        raise SRError(f"{path.name}: overall.rule must be worst or none")
    extra = overall["extra"]
    if not isinstance(extra, list) or any(not isinstance(v, str) or not v.strip() for v in extra):
        raise SRError(f"{path.name}: overall.extra must be a list of text")
    if set(extra) & set(judgements) or len(set(extra)) != len(extra):
        raise SRError(f"{path.name}: overall.extra must be unique and outside the judgements")
    if overall["rule"] == "none" and (extra or "extra_requires" in overall):
        raise SRError(f"{path.name}: rule none takes no extra judgements")
    requires = overall.get("extra_requires")
    if requires is not None:
        req = _mapping(requires, f"{path.name}: overall.extra_requires", frozenset({"domain", "none_of"}),
                       frozenset({"domain", "none_of"}))
        if req["domain"] not in extra:
            raise SRError(f"{path.name}: extra_requires.domain must be one of overall.extra")
        if not isinstance(req["none_of"], list) or any(v not in judgements for v in req["none_of"]):
            raise SRError(f"{path.name}: extra_requires.none_of must name judgements")
    app = raw.get("applicability")
    if app is not None:
        app = _mapping(app, f"{path.name}: applicability", frozenset({"domains", "judgements"}),
                       frozenset({"domains", "judgements"}))
        for name in _unique_list(app["domains"], f"{path.name}: applicability.domains"):
            if name not in ids:
                raise SRError(f"{path.name}: applicability domain {name} is not a domain")
        _unique_list(app["judgements"], f"{path.name}: applicability.judgements")
    return raw


def load_grade_tool(path: Path) -> dict:
    """The GRADE tool definition: starting points, certainty names, downgrade and upgrade levels with points."""
    path = Path(path)
    raw = _load_checked(path)
    _mapping(raw, path.name, GRADE_TOP, GRADE_TOP)
    if raw["id"] != GRADE_TOOL:
        raise SRError(f"{path.name}: id must be grade")
    _text(raw["name"], f"{path.name}: name")
    _source_and_verification(raw, path)
    starting = _mapping(raw["starting"], f"{path.name}: starting", frozenset(DESIGNS), frozenset(DESIGNS))
    for design in DESIGNS:
        if not isinstance(starting[design], int) or isinstance(starting[design], bool):
            raise SRError(f"{path.name}: starting.{design} must be a whole number")
    certainty = _unique_list(raw["certainty"], f"{path.name}: certainty")
    if len(certainty) != 4:
        raise SRError(f"{path.name}: certainty must name four levels")
    for block in ("downgrade", "upgrade"):
        rows = raw[block]
        if not isinstance(rows, list) or not rows:
            raise SRError(f"{path.name}: {block} must be a non-empty list")
        ids: list[str] = []
        for row in rows:
            _mapping(row, f"{path.name}: {block} domain", frozenset({"id", "label", "levels"}),
                     frozenset({"id", "label", "levels"}))
            if row["id"] in ids:
                raise SRError(f"{path.name}: duplicate {block} domain {row['id']}")
            ids.append(row["id"])
            _text(row["label"], f"{path.name}: {block} {row['id']} label")
            levels = row["levels"]
            if not isinstance(levels, dict) or not levels or list(levels.values())[0] != 0:
                raise SRError(f"{path.name}: {block} {row['id']} levels must start with none: 0")
            if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in levels.values()):
                raise SRError(f"{path.name}: {block} {row['id']} points must be whole numbers of zero or more")
    return raw


# ------------------------------------------------------------------ pure checks (no I/O)


def check_rob_worksheet(tool: dict, doc, label: str) -> list[str]:
    """Deterministic checks of one risk-of-bias worksheet (plan 5.10). Returns error strings; never support text."""
    errors: list[str] = []

    def err(message: str) -> None:
        errors.append(f"{label}: {message}")

    if not isinstance(doc, dict):
        return [f"{label}: the worksheet must be a mapping"]
    if set(doc) - WORKSHEET_KEYS:
        err("unknown top-level key refused")
    if doc.get("format") != FORMAT:
        err("format must be 1")
    if doc.get("tool") != tool["id"]:
        err(f"tool must be {tool['id']}")
    rows = doc.get("domains")
    if not isinstance(rows, list):
        err("domains must be a list")
        rows = []
    ordered = list(tool["judgements"])
    extra = list(tool["overall"]["extra"])
    allowed = set(ordered) | set(extra)
    tool_ids = [d["id"] for d in tool["domains"]]
    app = tool.get("applicability") or {}
    app_domains = list(app.get("domains", []))
    app_judgements = set(app.get("judgements", []))
    by_id: dict = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - DOMAIN_KEYS:
            err("each domain must be a mapping with id, judgement, support (and applicability where used)")
            continue
        did = row.get("id")
        if did not in tool_ids:
            err("domain id is not one of the tool's domains")
            continue
        if did in by_id:
            err(f"domain {did}: listed more than once")
            continue
        by_id[did] = row
    for did in tool_ids:
        row = by_id.get(did)
        if row is None:
            err(f"domain {did}: missing")
            continue
        judgement = row.get("judgement")
        if not isinstance(judgement, str) or judgement not in allowed:
            err(f"domain {did}: judgement must be one of the tool's judgements")
        if not isinstance(row.get("support"), str) or not row["support"].strip():
            err(f"domain {did}: support is required")
        if did in app_domains:
            block = row.get("applicability")
            if not isinstance(block, dict) or set(block) - {"judgement", "support"}:
                err(f"domain {did}: applicability judgement and support are required")
            else:
                if block.get("judgement") not in app_judgements:
                    err(f"domain {did}: applicability judgement must be one of the tool's judgements")
                if not isinstance(block.get("support"), str) or not block["support"].strip():
                    err(f"domain {did}: applicability support is required")
        elif "applicability" in row:
            err(f"domain {did}: applicability is not used by this tool")
    judged = {did: row.get("judgement") for did, row in by_id.items()
              if isinstance(row.get("judgement"), str) and row.get("judgement") in allowed}
    overall = doc.get("overall")
    if overall is not None and not isinstance(overall, dict):
        err("overall must be a mapping")
        return errors
    if isinstance(overall, dict) and set(overall) - OVERALL_KEYS:
        err("overall has an unknown key")
    oj = overall.get("judgement") if isinstance(overall, dict) else None
    rationale = overall.get("rationale") if isinstance(overall, dict) else None
    has_rationale = isinstance(rationale, str) and bool(rationale.strip())
    if tool["overall"]["rule"] == "none":
        _check_overall_none(tool, ordered, judged, oj, err)
    else:
        _check_overall_worst(tool, ordered, extra, judged, oj, has_rationale, err)
    return errors


def _check_overall_none(tool, ordered, judged, oj, err) -> None:
    if oj is None:
        return  # the overall judgement is optional under rule none (QUADAS-2)
    if oj not in ordered:
        err("overall judgement must be one of the tool's judgements")
    elif oj == ordered[0] and any(v != ordered[0] for v in judged.values()):
        err(f"overall {ordered[0]} requires every domain to be {ordered[0]} (rule none)")


def _check_overall_worst(tool, ordered, extra, judged, oj, has_rationale, err) -> None:
    if oj is None:
        err("overall judgement is required (rule worst)")
        return
    if oj in extra:
        req = tool["overall"].get("extra_requires")
        values = set(judged.values())
        if req is not None and (req["domain"] not in values or values & set(req["none_of"])):
            err(f"overall {oj} needs a domain judged {req['domain']} and no domain judged "
                f"{' or '.join(req['none_of'])} (rule worst)")
        return
    if oj not in ordered:
        err("overall judgement must be one of the tool's judgements")
        return
    indices = [ordered.index(v) for v in judged.values() if v in ordered]
    if not indices:
        err(f"overall {oj} has no domain to rest on (rule worst)")
        return
    worst = max(indices)
    if ordered.index(oj) < worst:
        err(f"overall {oj} is milder than the worst domain {ordered[worst]} (rule worst)")
    elif ordered.index(oj) > worst and not has_rationale:
        err(f"overall {oj} is more severe than the worst domain {ordered[worst]}; a rationale is required")


def grade_result(tool: dict, entry, *, included, label: str = GRADE_REL) -> dict:
    """Deterministic GRADE checks and certainty of one outcome entry (plan 5.10). Certainty is computed here."""
    errors: list[str] = []
    outcome = entry.get("outcome") if isinstance(entry, dict) else None
    where = f"{label} outcome {outcome if isinstance(outcome, str) else '?'}"

    def err(message: str) -> None:
        errors.append(f"{where}: {message}")

    result = {"outcome": outcome, "design": None, "studies": [], "certainty": None, "score": None,
              "levels": {"downgrade": {}, "upgrade": {}}, "errors": errors}
    if not isinstance(entry, dict):
        err("the entry must be a mapping")
        return result
    if set(entry) - GRADE_ENTRY_KEYS:
        bad = sorted(str(k) for k in set(entry) - GRADE_ENTRY_KEYS)[0]
        if bad == "certainty":
            err("certainty is computed by sr grade check and never stored; remove it")
        else:
            err("unknown key refused")
    design = entry.get("design")
    if design not in DESIGNS:
        err("design must be randomised or non-randomised")
    studies = entry.get("studies")
    if not isinstance(studies, list) or not studies:
        err("lists no study")
        studies = []
    for rid in studies:
        if rid not in included:
            err(f"study {rid} is not an included study at full text")
    result["design"] = design if design in DESIGNS else None
    result["studies"] = list(studies)
    downs = _levels_block(tool["downgrade"], entry.get("downgrade"), "downgrade", err)
    ups = _levels_block(tool["upgrade"], entry.get("upgrade"), "upgrade", err)
    result["levels"] = {"downgrade": {k: v[0] for k, v in downs.items()},
                        "upgrade": {k: v[0] for k, v in ups.items()}}
    if design == "randomised" and any(level != "none" for level, _points in ups.values()):
        err("upgrades are not allowed on randomised evidence")
    if design in DESIGNS:
        down_points = sum(points for _level, points in downs.values())
        up_points = sum(points for _level, points in ups.values())
        score = max(1, min(4, tool["starting"][design] - down_points + up_points))
        result["score"] = score
        result["certainty"] = tool["certainty"][score - 1]
    return result


def _levels_block(domains: list, block, name: str, err) -> dict:
    """{domain id: (level, points)} for a downgrade or upgrade block; reasons are required off 'none'."""
    out: dict = {}
    ids = [d["id"] for d in domains]
    if not isinstance(block, dict) or set(block) != set(ids):
        err(f"{name} must list exactly the tool's domains")
        return out
    for domain in domains:
        row = block[domain["id"]]
        if not isinstance(row, dict) or set(row) - {"level", "reason"}:
            err(f"{name} {domain['id']}: level and reason are required")
            continue
        level = row.get("level")
        if level not in domain["levels"]:
            err(f"{name} {domain['id']}: level must be one of the tool's levels")
            continue
        if level != "none" and not (isinstance(row.get("reason"), str) and row["reason"].strip()):
            err(f"{name} {domain['id']}: a reason is required when the level is not none")
        out[domain["id"]] = (level, domain["levels"][level])
    return out


# ------------------------------------------------------------------ I/O helpers (no links)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _no_links(ws: Path, rel: str) -> Path:
    """The path under the run, refused when any component is a link (plan 5.16: symlinks refused)."""
    current = Path(ws)
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            raise SRError(f"{rel} passes through a link; refused")
    return Path(ws) / rel


def _read_worksheet(ws: Path, rel: str):
    path = _no_links(ws, rel)
    if not path.is_file():
        raise SRError(f"{rel} is missing (sr rob init or sr grade init writes it)")
    return _read_yaml_file(path)


def _included(ws: Path) -> list[str]:
    verdicts = screening.resolved(ws, screening.FULL_TEXT)
    return sorted(rid for rid, verdict in verdicts.items() if verdict == screening.INCLUDE)


def _appraisal(locked) -> dict:
    block = locked.review.get("appraisal")
    return block if isinstance(block, dict) else {}


def _tool_file(project, name: str) -> Path:
    return Path(project.root) / "config" / "sr" / f"{name}.yml"


def _rob_tool(project, locked) -> dict:
    name = _appraisal(locked).get("rob_tool")
    if name in (None, "none"):
        raise SRError("the locked protocol names no risk-of-bias tool (appraisal.rob_tool is none)")
    if name not in ROB_TOOLS:
        raise SRError(f"appraisal.rob_tool {name!r} is not a known tool")
    return load_rob_tool(_tool_file(project, name))


def _grade_tool(project) -> dict:
    return load_grade_tool(_tool_file(project, GRADE_TOOL))


def _write(path: Path, text: str) -> None:
    screening._write_text(path, text)


def _dump(doc) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


# ------------------------------------------------------------------ risk of bias


def _template(tool: dict, rid: str) -> dict:
    app = tool.get("applicability") or {}
    domains = []
    for domain in tool["domains"]:
        row = {"id": domain["id"], "judgement": None, "support": ""}
        if domain["id"] in app.get("domains", []):
            row["applicability"] = {"judgement": None, "support": ""}
        domains.append(row)
    return {"format": FORMAT, "record_id": rid, "tool": tool["id"], "domains": domains,
            "overall": {"judgement": None, "rationale": ""}}


def init_rob(project, ws, *, as_agent: bool = False) -> dict:
    """One unjudged worksheet per included study that has none; existing worksheets are never overwritten."""
    ws = Path(ws)
    locked = protocol.require_locked(project, ws)
    verify.preflight(project, ws)
    tool = _rob_tool(project, locked)
    included = _included(ws)
    _no_links(ws, ROB_DIR)
    created: list[str] = []
    existing: list[str] = []
    pending: list[tuple[Path, str]] = []
    for rid in included:
        path = _no_links(ws, f"{ROB_DIR}/{rid}.yml")
        if path.exists():
            existing.append(rid)
            continue
        pending.append((path, _dump(_template(tool, rid))))
        created.append(rid)
    for path, text in pending:
        _write(path, text)
    return {"run": ws.name, "tool": tool["id"], "created": created, "existing": existing,
            "as_agent": as_agent}


def check_rob(project, ws) -> dict:
    """Every included study's worksheet, checked; read-only."""
    ws = Path(ws)
    locked = protocol.require_locked(project, ws)
    tool = _rob_tool(project, locked)
    included = _included(ws)
    rows: list[dict] = []
    errors: list[str] = []
    if not included:
        errors.append("no study is included at full text; there is nothing to appraise")
    for rid in included:
        rel = f"{ROB_DIR}/{rid}.yml"
        try:
            doc = _read_worksheet(ws, rel)
            problems = check_rob_worksheet(tool, doc, rel)
        except SRError as exc:
            problems = [str(exc)]
        rows.append({"record_id": rid, "status": "ok" if not problems else "refused", "errors": problems})
        errors.extend(problems)
    return {"run": ws.name, "tool": tool["id"], "studies": rows, "errors": errors, "ok": not errors}


def record_rob(project, ws, *, as_agent: bool = False) -> dict:
    """One sr.appraisal per included study, then one sr.committed with every worksheet's sha256 (plan 5.10)."""
    ws = Path(ws)
    locked = protocol.require_locked(project, ws)
    verify.preflight(project, ws)
    tool = _rob_tool(project, locked)
    doc = check_rob(project, ws)
    if not doc["ok"]:
        raise SRError(f"rob check refuses the record: {doc['errors'][0]}")
    actor = "agent" if as_agent else "human"
    op = store.new_id()
    files: dict[str, str] = {}
    appraisals: list[tuple[str, dict, str | None]] = []
    for row in doc["studies"]:
        rid = row["record_id"]
        rel = f"{ROB_DIR}/{rid}.yml"
        sheet = _read_worksheet(ws, rel)
        files[rel] = _sha256(_no_links(ws, rel))
        judgements = {d["id"]: d["judgement"] for d in sheet["domains"]}
        overall = sheet["overall"].get("judgement") if isinstance(sheet.get("overall"), dict) else None
        appraisals.append((rid, judgements, overall))
    led = ledger.EventsLedger(ws, actor=actor, op=op, extra={"protocol_version": locked.version})
    for rid, judgements, overall in appraisals:
        events.emit(ws, "sr.appraisal", actor, op=op, kind=KIND_ROB, record_id=rid, tool=tool["id"],
                    judgements=judgements, overall=overall, protocol_version=locked.version,
                    protocol_hash=locked.content_hash)
    led.commit(KIND_ROB, files=files, appraisals=len(appraisals), tool=tool["id"],
               protocol_hash=locked.content_hash)
    return {"run": ws.name, "tool": tool["id"], "appraisals": len(appraisals), "op": op,
            "protocol_version": locked.version}


# ------------------------------------------------------------------ GRADE


def _grade_template(outcomes: list[str], tool: dict) -> dict:
    def blank(rows):
        return {row["id"]: {"level": "none", "reason": ""} for row in rows}

    return {"format": FORMAT, "outcomes": [
        {"outcome": oid, "design": None, "studies": [], "downgrade": blank(tool["downgrade"]),
         "upgrade": blank(tool["upgrade"]), "effect_text": ""} for oid in outcomes]}


def init_grade(project, ws, *, as_agent: bool = False) -> dict:
    """The GRADE worksheet: one entry per SAP outcome of the locked design; never overwritten."""
    ws = Path(ws)
    locked = protocol.require_locked(project, ws)
    verify.preflight(project, ws)
    if _appraisal(locked).get("grade") is not True:
        raise SRError("the locked protocol does not plan GRADE (appraisal.grade is not true)")
    tool = _grade_tool(project)
    path = _no_links(ws, GRADE_REL)
    if path.exists():
        raise SRError(f"{GRADE_REL} exists and is never overwritten")
    outcomes = [o["id"] for o in (locked.design.get("sap") or {}).get("outcomes") or []]
    if not outcomes:
        raise SRError("the locked design has no SAP outcome to grade")
    _write(path, _dump(_grade_template(outcomes, tool)))
    return {"run": ws.name, "file": GRADE_REL, "outcomes": outcomes, "as_agent": as_agent}


def check_grade(project, ws) -> dict:
    """Every outcome entry, computed; read-only. Certainty is derived here, never read from the file."""
    ws = Path(ws)
    locked = protocol.require_locked(project, ws)
    tool = _grade_tool(project)
    included = set(_included(ws))
    errors: list[str] = []
    rows: list[dict] = []
    try:
        doc = _read_worksheet(ws, GRADE_REL)
    except SRError as exc:
        return {"run": ws.name, "entries": [], "errors": [str(exc)], "ok": False}
    if not isinstance(doc, dict) or set(doc) != GRADE_TOP_WORKSHEET or doc.get("format") != FORMAT:
        errors.append(f"{GRADE_REL}: format 1 and the outcomes key are required")
        return {"run": ws.name, "entries": [], "errors": errors, "ok": False}
    sap = [o["id"] for o in (locked.design.get("sap") or {}).get("outcomes") or []]
    seen: list[str] = []
    for entry in doc["outcomes"] if isinstance(doc.get("outcomes"), list) else []:
        oid = entry.get("outcome") if isinstance(entry, dict) else None
        if oid not in sap:
            errors.append(f"{GRADE_REL}: an entry names an outcome that is not a SAP outcome of the locked design")
            continue
        if oid in seen:
            errors.append(f"{GRADE_REL}: outcome {oid} has more than one entry")
            continue
        seen.append(oid)
        result = grade_result(tool, entry, included=included, label=GRADE_REL)
        errors.extend(result["errors"])
        rows.append({k: result[k] for k in ("outcome", "design", "studies", "certainty", "score", "levels")})
    for oid in sap:
        if oid not in seen:
            errors.append(f"{GRADE_REL}: SAP outcome {oid} has no entry")
    if not isinstance(doc.get("outcomes"), list):
        errors.append(f"{GRADE_REL}: outcomes must be a list")
    return {"run": ws.name, "entries": rows, "errors": errors, "ok": not errors}


def record_grade(project, ws, *, as_agent: bool = False) -> dict:
    """One sr.appraisal per outcome (levels and certainty; no reasons), then one sr.committed (plan 5.10)."""
    ws = Path(ws)
    locked = protocol.require_locked(project, ws)
    verify.preflight(project, ws)
    doc = check_grade(project, ws)
    if not doc["ok"]:
        raise SRError(f"grade check refuses the record: {doc['errors'][0]}")
    actor = "agent" if as_agent else "human"
    op = store.new_id()
    digest = _sha256(_no_links(ws, GRADE_REL))
    led = ledger.EventsLedger(ws, actor=actor, op=op, extra={"protocol_version": locked.version})
    for row in doc["entries"]:
        events.emit(ws, "sr.appraisal", actor, op=op, kind=KIND_GRADE, outcome=row["outcome"],
                    design=row["design"], studies=row["studies"], certainty=row["certainty"], score=row["score"],
                    levels=row["levels"], protocol_version=locked.version, protocol_hash=locked.content_hash)
    led.commit(KIND_GRADE, files={GRADE_REL: digest}, outcomes=len(doc["entries"]),
               protocol_hash=locked.content_hash)
    return {"run": ws.name, "outcomes": len(doc["entries"]), "op": op, "protocol_version": locked.version}


def table(project, ws, *, write: bool = False) -> dict:
    """The summary-of-findings table, derived from the check; --write renders sr/grade/summary-of-findings.md."""
    ws = Path(ws)
    if write:
        verify.preflight(project, ws)
    doc = check_grade(project, ws)
    if not doc["ok"]:
        raise SRError(f"grade check refuses the table: {doc['errors'][0]}")
    lines = [f"# Summary of findings: run {ws.name}", "",
             "Certainty is computed by `sr grade check` from the recorded levels, never read from the file.", "",
             "| Outcome | Design | Studies | Downgraded for | Upgraded for | Certainty |",
             "|---|---|---|---|---|---|"]
    for row in doc["entries"]:
        down = ", ".join(f"{k} ({v})" for k, v in row["levels"]["downgrade"].items() if v != "none") or "-"
        up = ", ".join(f"{k} ({v})" for k, v in row["levels"]["upgrade"].items() if v != "none") or "-"
        lines.append(f"| {row['outcome']} | {row['design']} | {len(row['studies'])} | {down} | {up} | "
                     f"{row['certainty']} |")
    markdown = "\n".join(lines) + "\n"
    written = None
    if write:
        path = _no_links(ws, SUMMARY_REL)
        _write(path, markdown)
        written = SUMMARY_REL
    return {"run": ws.name, "entries": doc["entries"], "markdown": markdown, "file": written}
