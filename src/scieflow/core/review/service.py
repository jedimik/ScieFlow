"""The review API the web app and the CLI build on (the contract; spec §6).

Pure functions over a `Project` and a run slug; no web framework imports.
Documented in docs/research/review-contract.md. After M0 the signatures and
returned shapes change additively only.

Readers return None (or []) for an ordinary absent state — a run that does not
exist, has not been reviewed, or a round that lacks the requested file — and
never raise for it. They raise a `ValueError` subclass for a refused input
(path-shaped names, a bad round number, an unknown pass) or a corrupt file:

- `ReviewError`  — reader input refused, a file is unreadable or off-schema;
- `settings.SettingsError` — `review_settings` / `set_review_settings` /
  `preview_assignment` refused a setting or an actor;
- `assign.AssignmentError` — `preview_assignment` cannot staff the round;
- `decisions.DecisionError` — `decide` refused; nothing was written.

File layout of a run (workspace/<slug>/):

    config.yml                                   review: block (settings)
    review/round-N/assignment.json               who reviews which draft
    review/round-N/<reviewer>-on-<author>.blind.json     pass A
    review/round-N/<reviewer>-on-<author>.evidence.json  pass B
    review/round-N/evidence/bundle.json          the shared evidence bundle
    review/round-N/matrix.json (+ matrix.md)     the coordinator's matrix
    review/round-N/decisions.json                written only by decide()
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from scieflow.core import agent_config as ac
from scieflow.core.drafts import DraftError, check_name
from scieflow.core.project import Project, ProjectError
from scieflow.core.review import assign as _assign
from scieflow.core.review import claims as _claims
from scieflow.core.review import decisions as _decisions
from scieflow.core.review import settings as _settings

PASSES = ("blind", "evidence")
PAPER_REVIEW_AUTHOR = "research.submitter"     # the reviewed author in a paper-review run
PAPER_REVIEW_POOL = "research.reviewer"        # its default reviewer pool
_ROUND_DIR = re.compile(r"^round-([1-9][0-9]{0,5})$")
_REFERENCE_STATUSES = ("ok", "corrected", "retracted", "unresolvable")


class ReviewError(ValueError):
    """A refused reader input, or a review file that cannot be read as documented."""


# --- helpers ---------------------------------------------------------------------


def _ws(project: Project, slug: str) -> Path | None:
    """The run directory, or None when the run does not exist."""
    try:
        ws = project.run_dir(slug)
    except ProjectError as exc:
        raise ReviewError(str(exc)) from exc
    return ws if ws.is_dir() else None


def _round(round_) -> int:
    if isinstance(round_, bool) or not isinstance(round_, int) or round_ < 1:
        raise ReviewError(f"round {round_!r} is not an integer >= 1")
    return round_


def _name(value, what: str) -> str:
    if not isinstance(value, str):
        raise ReviewError(f"{what} {value!r} is not an agent name")
    try:
        check_name(value)
    except DraftError as exc:
        raise ReviewError(f"{what} {value!r}: {exc}") from exc
    if "\\" in value or any(c.isspace() for c in value):
        raise ReviewError(f"{what} {value!r}: no backslashes or spaces in agent names")
    return value


def _inside(ws: Path, *parts: str) -> Path:
    """ws/review/<parts…>, refusing anything that resolves outside the run's review/."""
    base = ws / "review"
    candidate = base.joinpath(*parts)
    try:
        real_ws = ws.resolve()
        real = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReviewError(f"cannot resolve review/{'/'.join(parts)}: {exc}") from exc
    if real_ws not in real.parents or real.relative_to(real_ws).parts[:1] != ("review",):
        raise ReviewError(f"review/{'/'.join(parts)} resolves outside the run")
    return candidate


def _load(ws: Path, *parts: str, schema: str | None = None):
    """Parsed JSON of review/<parts…>, or None when the file does not exist."""
    path = _inside(ws, *parts)
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise ReviewError(f"review/{'/'.join(parts)}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ReviewError(f"review/{'/'.join(parts)} is not valid JSON: {exc}") from exc
    if schema:
        problem = _decisions.schema_error(schema, data)
        if problem:
            raise ReviewError(f"review/{'/'.join(parts)} does not match the {schema} "
                              f"schema: {problem}")
    return data


def _round_numbers(ws: Path) -> list[int]:
    base = ws / "review"
    if not base.is_dir():
        return []
    out = []
    for entry in base.iterdir():
        m = _ROUND_DIR.match(entry.name)
        if not m:
            continue
        try:
            _inside(ws, entry.name)
        except ReviewError:
            continue                    # a symlink out of the run is not a round
        if entry.is_dir():
            out.append(int(m.group(1)))
    return sorted(out)


def _rd(n: int) -> str:
    return f"round-{n}"


def _exists(ws: Path, *parts: str) -> bool:
    try:
        return _inside(ws, *parts).is_file()
    except ReviewError:
        return False


# --- settings and assignment -------------------------------------------------------


def review_settings(project: Project, slug: str) -> dict:
    """Effective review settings (defaults < config/defaults.yml < the run's review:)."""
    return _settings.load(project, slug)


def set_review_settings(project: Project, slug: str, changes: dict, actor: str) -> dict:
    """Validate and write `changes` to the run's review: block; returns effective settings.

    `actor` is "human" or "agent"; an agent may only set scite=off and
    reviews_per_draft. A value of None removes the key from the run's block.
    """
    return _settings.write(project, slug, changes, actor)


def assignment_inputs(project: Project, slug: str, settings: dict | None = None):
    """(authors, pool, settings, families) the planner needs, from the run's config."""
    effective = settings if settings is not None else _settings.load(project, slug)
    eff = ac.resolve(project.root, slug)
    # A paper-review run reviews the submitter's manuscript with the
    # research.reviewer pool; a paper-draft run the drafts of research.draft-authors
    # with the research.draft-reviewers pool. Same planner, same switches.
    try:
        manuscript = _claims.reviews_manuscript(project.run_dir(slug))
    except ProjectError:
        manuscript = False
    if manuscript:
        submitter = eff.value(PAPER_REVIEW_AUTHOR)
        if not submitter or not isinstance(submitter, str):
            raise _assign.AssignmentError(
                f"paper-review run: {PAPER_REVIEW_AUTHOR} is not set (assign the submitter "
                f"with `scieflow agent configure --workspace {slug} --assign "
                f"{PAPER_REVIEW_AUTHOR}=AGENT --yes`)")
        authors = [submitter]
    else:
        authors = eff.value(ac.DRAFT_AUTHORS)
        authors = [authors] if isinstance(authors, str) else list(authors or [])
    pool = effective["pool"]
    if pool is None:
        names = eff.value(PAPER_REVIEW_POOL if manuscript else ac.DRAFT_REVIEWERS)
        names = [names] if isinstance(names, str) else list(names or [])
        pool = [{"agent": n} for n in names]
    registry = (ac._load_yaml(project.root / "config" / "agents.yml") or {}).get("agents") or {}
    for name in authors:
        if name not in registry:
            what = "submitter" if manuscript else "draft author"
            raise _assign.AssignmentError(f"{what} {name!r} is not in config/agents.yml")
    reviewers = [e.get("agent") for e in pool] + list(effective["matrix_override"] or {})
    if manuscript and authors[0] in reviewers:
        # paper-review: the submitter never grades its own edits, switch or not.
        raise _assign.AssignmentError(
            f"paper-review run: the submitter {authors[0]!r} may not review its own "
            "manuscript (allow_self_review does not apply to paper-review); remove it from "
            "the reviewer pool / matrix_override")
    for name in reviewers:
        if name not in registry:
            raise _assign.AssignmentError(f"reviewer {name!r} is not in config/agents.yml")
        if not (registry[name] or {}).get("enabled", True):
            raise _assign.AssignmentError(f"reviewer {name!r} is disabled in config/agents.yml")
    families = {name: ac.model_family(cfg or {}) for name, cfg in registry.items()}
    return authors, pool, effective, families


def preview_assignment(project: Project, slug: str, changes: dict | None = None) -> dict:
    """The assignment the planner would write with the run's settings plus unsaved
    `changes` (same keys as set_review_settings). Writes nothing; raises
    SettingsError / AssignmentError with a user-readable message."""
    effective = _settings.load(project, slug)
    if changes:
        if not isinstance(changes, dict):
            raise _settings.SettingsError(f"changes: expected a mapping, got {changes!r}")
        unknown = [k for k in changes if k not in _settings.DEFAULTS]
        if unknown:
            raise _settings.SettingsError(
                f"review: unknown key {unknown[0]!r} (keys: {', '.join(_settings.KEYS)})")
        merged = {**effective}
        for key, value in changes.items():
            merged[key] = _settings.DEFAULTS[key] if value is None else value
        _settings.validate(merged)
        effective = merged
    return _assign.plan(*assignment_inputs(project, slug, effective))


def assignment(project: Project, slug: str, round: int | None = None) -> dict | None:  # noqa: A002
    """review/round-N/assignment.json; `round=None` reads the latest round that has one."""
    ws = _ws(project, slug)
    if ws is None:
        if round is not None:
            _round(round)
        return None
    if round is None:
        for n in reversed(_round_numbers(ws)):
            data = _load(ws, _rd(n), "assignment.json", schema="review-assignment")
            if data is not None:
                return data
        return None
    return _load(ws, _rd(_round(round)), "assignment.json", schema="review-assignment")


# --- rounds and passes -------------------------------------------------------------


def _pass_file(reviewer: str, author: str, pass_: str) -> str:
    return f"{reviewer}-on-{author}.{pass_}.json"


def rounds(project: Project, slug: str) -> list[dict]:
    """Per review round: which files are present and the bundle's scite_status.

    `pairs` come from the round's assignment (empty without one); `decisions`
    is true only for a decisions file decide() wrote (see decisions.authentic).
    """
    ws = _ws(project, slug)
    if ws is None:
        return []
    out = []
    for n in _round_numbers(ws):
        plan = _load(ws, _rd(n), "assignment.json", schema="review-assignment")
        pairs = []
        for p in (plan or {}).get("pairs", []):
            rv, au = p["reviewer"], p["author"]
            pairs.append({"reviewer": rv, "author": au,
                          **{ps: _exists(ws, _rd(n), _pass_file(rv, au, ps)) for ps in PASSES}})
        bundle = _load(ws, _rd(n), "evidence", "bundle.json")
        out.append({
            "round": n,
            "assignment": plan is not None,
            "pairs": pairs,
            "evidence": bundle is not None,
            "scite_status": bundle.get("scite_status") if isinstance(bundle, dict) else None,
            "matrix": _exists(ws, _rd(n), "matrix.json"),
            "decisions": _decisions.authentic(ws, n) is not None,
        })
    return out


def review_pass(project: Project, slug: str, round: int, reviewer: str,  # noqa: A002
                author: str, pass_: str) -> dict | None:
    """One review file; `pass` is filled in as "blind" when the file has none."""
    round_ = _round(round)
    _name(reviewer, "reviewer")
    _name(author, "author")
    if pass_ not in PASSES:
        raise ReviewError(f"pass {pass_!r} is not one of {', '.join(PASSES)}")
    ws = _ws(project, slug)
    if ws is None:
        return None
    data = _load(ws, _rd(round_), _pass_file(reviewer, author, pass_))
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ReviewError(f"review/{_rd(round_)}/{_pass_file(reviewer, author, pass_)} "
                          "is not a JSON object")
    return {**data, "pass": data.get("pass", "blind")}


# --- evidence, matrix, decisions, blockers --------------------------------------------


def _bundle(ws: Path, n: int) -> dict | None:
    return _load(ws, _rd(n), "evidence", "bundle.json", schema="evidence-bundle")


def evidence_summary(project: Project, slug: str, round: int) -> dict | None:  # noqa: A002
    """Counts, flags, calls and quota state of a round's evidence bundle."""
    round_ = _round(round)
    ws = _ws(project, slug)
    if ws is None:
        return None
    bundle = _bundle(ws, round_)
    if bundle is None:
        return None
    by_status = {s: 0 for s in _REFERENCE_STATUSES}
    for ref in bundle["references"]:
        by_status[ref["status"]] += 1
    passages = [p for c in bundle["claims"] for p in c["passages"]]
    return {
        "round": round_,
        "generated": bundle.get("generated"),
        "manuscript_hash": bundle["manuscript_hash"],
        "scite_status": bundle["scite_status"],
        "references": {"total": len(bundle["references"]), "by_status": by_status},
        "claims": len(bundle["claims"]),
        "passages": len(passages),
        "abstract_only": sum(1 for p in passages if p["abstract_only"]),
        "contested": len(bundle.get("contested") or []),
        "searches": [{"id": s["id"], "kind": s["kind"], "source": s["source"],
                      "results": len(s["results"])} for s in bundle.get("searches") or []],
        "flags": bundle["flags"],
        "blockers": [f["id"] for f in bundle["flags"] if f["blocker"]],
        "calls": bundle["calls"],
        "quota": scite_status(project)["quota_remaining"],
    }


def matrix(project: Project, slug: str, round: int) -> dict | None:  # noqa: A002
    """review/round-N/matrix.json (validated against review-matrix)."""
    round_ = _round(round)
    ws = _ws(project, slug)
    if ws is None:
        return None
    return _load(ws, _rd(round_), "matrix.json", schema="review-matrix")


def decisions(project: Project, slug: str, round: int) -> dict | None:  # noqa: A002
    """The round's decisions as decide() wrote them; a hand-written or edited
    decisions.json (no matching review.decided event) reads as None."""
    round_ = _round(round)
    ws = _ws(project, slug)
    if ws is None:
        return None
    _inside(ws, _rd(round_), "decisions.json")
    return _decisions.authentic(ws, round_)


def decide(project: Project, slug: str, round: int, decisions: dict[str, dict],  # noqa: A002
           actor: str) -> dict:
    """Record per-cluster decisions {cluster: {decision, note?, overrule_reason?,
    rationale?}}; writes decisions.json atomically and emits review.decided.
    Returns the whole decisions document of the round."""
    round_ = _round(round)
    ws = _ws(project, slug)
    if ws is None:
        raise _decisions.DecisionError(f"no workspace {slug!r} under {project.workspace_root}")
    try:
        _inside(ws, _rd(round_), "matrix.json")
    except ReviewError as exc:
        raise _decisions.DecisionError(str(exc)) from exc
    settings = _settings.load(project, slug)
    return _decisions.decide(ws, round_, decisions, actor, settings)


def _latest_evidence_round(ws: Path) -> int | None:
    for n in reversed(_round_numbers(ws)):
        if _exists(ws, _rd(n), "matrix.json") or _exists(ws, _rd(n), "evidence", "bundle.json"):
            return n
    return None


def _ref_ids(flag: dict) -> tuple[str, str] | None:
    """Identity of a blocker flag's reference across rounds: (kind, doi or bib key)."""
    ident = flag.get("doi") or flag.get("key")
    return (flag["kind"], ident.lower() if flag.get("doi") else ident) if ident else None


def _overruled(cluster: dict, decided: dict) -> bool:
    d = decided.get(cluster["cluster"])
    return bool(d and d["decision"] == "reject" and d.get("actor") == "human"
                and d.get("overrule_reason"))


def _earlier_overrules(ws: Path, rounds_: list[int]) -> set[tuple[str, str]]:
    """References whose blocker a human overruled in one of `rounds_`."""
    out: set[tuple[str, str]] = set()
    for n in rounds_:
        try:
            mx = _load(ws, _rd(n), "matrix.json", schema="review-matrix")
            bundle = _bundle(ws, n) or {}
        except ReviewError:
            continue                    # an unreadable old round overrules nothing
        if mx is None:
            continue
        decided = (_decisions.authentic(ws, n) or {}).get("decisions", {})
        flags = {f["id"]: f for f in bundle.get("flags", [])}
        for c in mx["clusters"]:
            if c["blocker"] and _overruled(c, decided):
                for fid in c.get("flags", []):
                    if fid in flags and (ident := _ref_ids(flags[fid])):
                        out.add(ident)
    return out


def blockers(project: Project, slug: str) -> list[dict]:
    """Open blockers of the latest round that has a matrix or a bundle.

    A blocker cluster stays open until a human `reject` with an
    overrule_reason (an overrule) is recorded through decide(); a blocker flag
    of the bundle that no blocker cluster covers is open too (`cluster: None`).
    An overrule holds for the same reference (kind + DOI or bib key) in later
    rounds. A later round whose bundle no longer flags the reference has no
    blocker.
    """
    ws = _ws(project, slug)
    if ws is None:
        return []
    n = _latest_evidence_round(ws)
    if n is None:
        return []
    bundle = _bundle(ws, n) or {}
    flags = {f["id"]: f for f in bundle.get("flags", [])}
    mx = _load(ws, _rd(n), "matrix.json", schema="review-matrix")
    decided = (_decisions.authentic(ws, n) or {}).get("decisions", {}) if mx else {}
    earlier = _earlier_overrules(ws, [m for m in _round_numbers(ws) if m < n])

    def refs(ids):
        out = []
        for fid in ids:
            f = flags.get(fid)
            if f is not None:
                out.append({k: f[k] for k in ("doi", "key", "detail") if k in f})
        return out

    def overruled_before(ids) -> bool:
        idents = [_ref_ids(flags[fid]) for fid in ids if fid in flags]
        return bool(idents) and all(i in earlier for i in idents)

    out, covered = [], set()
    for c in (mx or {}).get("clusters", []):
        if not c["blocker"]:
            continue
        covered.update(c.get("flags", []))
        if _overruled(c, decided) or overruled_before(c.get("flags", [])):
            continue
        d = decided.get(c["cluster"])
        out.append({"round": n, "cluster": c["cluster"], "reason": c["blocker_reason"],
                    "title": c.get("title"), "flags": list(c.get("flags", [])),
                    "references": refs(c.get("flags", [])),
                    "decision": d["decision"] if d else None})
    for fid, f in flags.items():
        if f["blocker"] and fid not in covered and not overruled_before([fid]):
            out.append({"round": n, "cluster": None, "reason": f["kind"], "title": None,
                        "flags": [fid], "references": refs([fid]), "decision": None})
    return out


# --- scite ----------------------------------------------------------------------


def scite_status(project: Project) -> dict:
    """Whether scite is usable for this project: `configured` (config/scite.yml
    present and valid), `tier` (`public` = REST only, `mcp`; None when not
    configured), `quota_remaining` (MCP calls left today under
    `max_mcp_calls_per_day`, None without a day budget), `last_error` (the
    latest failed scite call today: `{"ts", "source", "op", "error"}`) and
    `reason` (why scite or its MCP tier is unavailable). Never the token."""
    from scieflow.integrations.scite import ledger as _ledger
    from scieflow.integrations.scite import policy as _policy

    try:
        pol = _policy.load(project.root)
    except _policy.PolicyError as exc:
        return {"configured": False, "tier": None, "quota_remaining": None, "last_error": None,
                "reason": str(exc)}
    if not pol.enabled:
        return {"configured": False, "tier": None, "quota_remaining": None, "last_error": None,
                "reason": pol.reason}
    led = _ledger.Ledger(None, pol)
    last = None
    for row in reversed(led.rows("day")):
        if row.get("status") == "error":
            last = {k: row.get(k) for k in ("ts", "source", "op", "error")}
            break
    return {"configured": True, "tier": pol.tier, "quota_remaining": led.remaining(None)["day"],
            "last_error": last, "reason": pol.reason}
