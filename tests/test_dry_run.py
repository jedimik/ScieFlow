"""End-to-end dry run: one full iteration driven through the deterministic core
with the stub agent standing in for both vendor cycles, ending in a
low-budget checkpoint. No LLM calls, no network."""

import subprocess
import sys
from pathlib import Path

import budget as budget_mod
import checkpoint as checkpoint_mod
import sfx_init
import status as status_mod
import validate as validate_mod

ROOT = Path(__file__).resolve().parents[1]

KIND_FOR_PHASE = {
    "hypothesize": "hypothesis",
    "experiment": "results-summary",
    "literature": "literature",
    "synthesize": "synthesis",
}
FILE_FOR_PHASE = {
    "hypothesize": "hypothesis.md",
    "experiment": "results-summary.md",
    "literature": "literature.md",
    "synthesize": "synthesis.md",
}


def dispatch_stub(ws: Path, kind: str, out_path: Path) -> None:
    prompt = ws / "logs" / f"{kind}.prompt.md"
    prompt.write_text(f"output: {out_path}\nkind: {kind}\n")
    transcript = ws / "logs" / f"{kind}.transcript.md"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "agent_run.py"),
         "stub", str(prompt), str(transcript)],
        check=True,
    )


def test_full_iteration_then_low_budget_checkpoint(tmp_path):
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\nDenoising parameter study (dry run).\n")
    ws = sfx_init.init_workspace(
        "dry-run", goal, tmp_path / "workspace",
        {"approval": "autonomous", "max_iterations": 1,
         "max_experiment_runs": 6, "max_wall_minutes": 60},
        ROOT,
    )
    it_dir = ws / "iterations" / "1"
    it_dir.mkdir()

    # Walk all four phases: pending -> running -> (stub dispatch) -> done
    st = status_mod.read_status(ws)
    while (phase := status_mod.next_pending(st)) is not None:
        status_mod.write_status(ws, status_mod.mark(st, phase, "running"))
        dispatch_stub(ws, KIND_FOR_PHASE[phase], it_dir / FILE_FOR_PHASE[phase])
        assert (it_dir / FILE_FOR_PHASE[phase]).exists()
        st = status_mod.mark(st, phase, "done")
        status_mod.write_status(ws, st)

    # Record spend for the iteration (stub campaign ran 6 experiment runs)
    b = budget_mod.read_budget(ws)
    budget_mod.write_budget(ws, budget_mod.record(b, iterations=1, experiment_runs=6))

    # Notebook entry: produced, validated, appended
    entry_path = it_dir / "notebook-entry.md"
    dispatch_stub(ws, "notebook-entry", entry_path)
    entry = entry_path.read_text()
    assert validate_mod.validate_notebook_entry(entry) == []
    with (ws / "notebook.md").open("a") as fh:
        fh.write("\n" + entry)

    # Budget check: iterations 1/1 spent -> low budget -> graceful checkpoint
    b = budget_mod.read_budget(ws)
    assert "iterations" in budget_mod.low_dimensions(b)
    st = checkpoint_mod.checkpoint(ws, "low-budget", "iterations exhausted")
    assert st["stopped"]["reason"] == "low-budget"
    assert "iteration 1" in st["stopped"]["resume"]

    # Whole-status still schema-valid after everything
    assert validate_mod.validate_status(status_mod.read_status(ws)) == []
