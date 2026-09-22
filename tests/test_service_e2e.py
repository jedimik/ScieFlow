"""One research-loop iteration through the service core: run created, phases
marked, agents dispatched as jobs, an approval gate, spend recorded, and a
budget-driven checkpoint — all visible in the event log. Stub agent, no network."""

import sys
from pathlib import Path

from scieflow.core import events, gates, jobs, service
from scieflow.core.project import Project
from scieflow.core.run import actions, init, status

ROOT = Path(__file__).resolve().parents[1]
KIND = {"hypothesize": "hypothesis", "experiment": "results-summary",
        "literature": "literature", "synthesize": "synthesis"}


def test_one_iteration_end_to_end(tmp_path):
    project = Project(ROOT)
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\nDry run through the service core.\n")
    ws = init.init_workspace("e2e", goal, tmp_path / "workspace",
                             {"approval": "autonomous", "max_iterations": 1,
                              "max_experiment_runs": 6, "max_wall_minutes": 60}, ROOT)
    it = ws / "iterations" / "1"
    it.mkdir()

    for phase, kind in KIND.items():
        actions.mark_phase(ws, phase, "running")
        if phase == "experiment":
            g = gates.open_gate(project, ws, "campaign-approval", "Run 6-run sweep?",
                                options=["approve", "reject"], in_scope=True)
            gates.answer(project, ws, g["id"], "approve", actor="agent",
                         rationale="inside the approved budget and scope")
        prompt = ws / "logs" / f"{kind}.md"
        prompt.write_text(f"output: {it / (kind + '.md')}\nkind: {kind}\n")
        job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / f"{kind}.t.md")
        assert job["state"] == "done"
        actions.mark_phase(ws, phase, "done")

    actions.record_spend(ws, iterations=1, experiment_runs=6)
    try:
        actions.advance_iteration(ws)
        raise AssertionError("advance must be refused: iterations spent")
    except actions.BudgetExhausted:
        pass

    st = status.read_status(ws)
    assert st["stopped"]["reason"] == "low-budget" and len(st["id"]) == 26
    types = [e["type"] for e in events.read(ws)]
    for expected in ("run.created", "phase.started", "gate.opened", "gate.answered",
                     "job.finished", "budget.recorded", "checkpoint", "job.refused"):
        assert expected in types, expected
    answered = [e for e in events.read(ws) if e["type"] == "gate.answered"][0]
    assert answered["actor"] == "agent"
    assert len(jobs.list_jobs(project, ws)) == 4
