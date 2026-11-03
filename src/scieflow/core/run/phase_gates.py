"""Phase gates: a paper run's `verify done` and a paper-revision run's `respond done` need a passing check (M-G2 5.3-5.6).

`check` is the first statement of actions.mark_phase. It returns at once unless the phase is being marked done
in a gated workflow; a refusal raises PhaseGateRefused (a ValueError: CLI exit 1, ServiceError in the service
and the web app) before the status is written. Every gate run appends one note.phase_gate event with counts and
ids only; no manuscript excerpt, evidence value, letter quote or blocker detail reaches a message or an event.
The research modules are imported lazily, so a missing module refuses (fail closed) instead of passing.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import NoReturn

from scieflow.core import events
from scieflow.core.project import Project
from scieflow.core.run import status

CLAIMS_MODULE = "scieflow.research.manuscript.claims.compile"
LEDGER_MODULE = "scieflow.research.manuscript.claims.ledger"
LETTER_MODULE = "scieflow.research.revision.letter"
COVERAGE_MODULE = "scieflow.research.revision.coverage"


class PhaseGateRefused(ValueError):
    """A gated phase was marked done while its check does not pass. Nothing was written to status.yml."""


def check(ws, phase: str, state: str, actor: str) -> None:
    """Run the gate of (workflow, phase) when `phase` is being marked done; return or raise PhaseGateRefused."""
    if state != "done":
        return
    ws = Path(ws)
    try:
        st = status.read_status(ws)
    except Exception:
        return  # an unreadable status fails in mark_phase exactly as before, writing nothing
    if not isinstance(st, dict):
        return
    workflow = st.get("workflow", "research-loop")
    if phase == "verify" and workflow in status.PAPER_WORKFLOWS:
        _claims_gate(ws, phase, workflow, actor)
    elif phase == "respond" and workflow == "paper-revision":
        _coverage_gate(ws, phase, workflow, actor)


def _emit(ws: Path, actor: str, **data) -> None:
    events.emit(ws, "note.phase_gate", actor, **data)


def _refuse(ws: Path, actor: str, message: str, *, outcome: str = "refused", **data) -> NoReturn:
    _emit(ws, actor, outcome=outcome, **data)
    raise PhaseGateRefused(message)


def _claims_gate(ws: Path, phase: str, workflow: str, actor: str) -> None:
    """M-G2 5.4: the claim check of the manuscript as it is now, through M-G1's check_run (writes nothing)."""
    slug = ws.name
    base = {"phase": phase, "workflow": workflow, "gate": "claims"}
    man = ws / "manuscript"
    if man.is_symlink() or (man.exists() and not man.is_dir()):
        _refuse(ws, actor, "verify refused: manuscript must be a folder inside the run, not a link or a file",
                **base, errors=0, warnings=0)
    if not man.exists():
        if workflow == "paper-draft":
            _emit(ws, actor, **base, outcome="no-manuscript", errors=0, warnings=0)
            return
        _refuse(ws, actor, "verify refused: a paper-revision run needs its manuscript/ folder",
                **base, errors=0, warnings=0)
    try:
        compile_mod = importlib.import_module(CLAIMS_MODULE)
        ledger_mod = importlib.import_module(LEDGER_MODULE)
    except ImportError:
        _refuse(ws, actor, "verify refused: the claim check could not run (ImportError); fail closed",
                outcome="failed-closed", **base, errors=0, warnings=0)
    try:
        result = compile_mod.check_run(Project.discover(ws), ws)
    except ledger_mod.ClaimsError as exc:
        _refuse(ws, actor, f"verify refused: claims [{exc.rule}]: {exc.message}",
                **base, rule=exc.rule, errors=0, warnings=0)
    except Exception as exc:  # fail closed: whatever else stops the check refuses the phase
        _refuse(ws, actor, f"verify refused: the claim check could not run ({type(exc).__name__}); fail closed",
                outcome="failed-closed", **base, errors=0, warnings=0)
    errors, warnings = len(result.errors), len(result.warnings)
    if result.status_unreadable:
        _refuse(ws, actor, "verify refused: the review status could not be read; fail closed",
                **base, errors=errors, warnings=warnings)
    if errors:
        _refuse(ws, actor,
                f"verify refused: {errors} claim error(s); run `scieflow manuscript claims build {slug}` "
                f"then `scieflow manuscript claims check {slug}`",
                **base, errors=errors, warnings=warnings)
    _emit(ws, actor, **base, outcome="passed", errors=0, warnings=warnings)


def _coverage_gate(ws: Path, phase: str, workflow: str, actor: str) -> None:
    """M-G2 5.5: the coverage of the highest existing round, through M-G1's evaluate (writes nothing)."""
    slug = ws.name
    base = {"phase": phase, "workflow": workflow, "gate": "coverage"}
    try:
        letter_mod = importlib.import_module(LETTER_MODULE)
        coverage_mod = importlib.import_module(COVERAGE_MODULE)
    except ImportError:
        _refuse(ws, actor, "respond refused: the coverage check could not run (ImportError); fail closed",
                outcome="failed-closed", **base, errors=0, warnings=0)
    try:
        rounds = letter_mod.existing_rounds(ws)
    except letter_mod.LetterError as exc:
        _refuse(ws, actor, f"respond refused: [{exc.rule}] {exc.message}", **base, rule=exc.rule,
                errors=0, warnings=0)
    except Exception as exc:  # fail closed
        _refuse(ws, actor, f"respond refused: the coverage check could not run ({type(exc).__name__}); "
                           "fail closed", outcome="failed-closed", **base, errors=0, warnings=0)
    if not rounds:
        _refuse(ws, actor, "respond refused: no decision letter ingested; the user runs "
                           f"`scieflow manuscript revision ingest {slug} --letter FILE`",
                **base, errors=0, warnings=0)
    number = max(rounds)
    try:
        doc = coverage_mod.evaluate(ws, number)
    except letter_mod.LetterError as exc:
        _refuse(ws, actor, f"respond refused: [{exc.rule}] {exc.message}", **base, round=number,
                rule=exc.rule, errors=0, warnings=0)
    except Exception as exc:  # fail closed
        _refuse(ws, actor, f"respond refused: the coverage check could not run ({type(exc).__name__}); "
                           "fail closed", outcome="failed-closed", **base, round=number, errors=0, warnings=0)
    problems = doc["problems"]
    errors = sum(1 for row in problems if row["level"] == "error")
    warnings = len(problems) - errors
    if not doc["ok"]:
        _refuse(ws, actor, f"respond refused: round {number} coverage has {errors} problem(s); run "
                           f"`scieflow manuscript revision coverage {slug} --round {number}`",
                **base, round=number, errors=errors, warnings=warnings)
    _emit(ws, actor, **base, outcome="passed", round=number, errors=0, warnings=warnings)
