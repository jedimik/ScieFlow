from pathlib import Path

import pytest
import yaml

import budget
import sfx_init
import status

ROOT = Path(__file__).resolve().parents[1]


def make_goal(tmp_path: Path) -> Path:
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\nDenoising parameter study.\n")
    return goal


def test_init_creates_complete_workspace(tmp_path):
    ws = sfx_init.init_workspace(
        "2026-07-denoise", make_goal(tmp_path), tmp_path / "workspace",
        {"approval": "autonomous", "max_iterations": 3}, ROOT)
    assert (ws / "goal.md").read_text().startswith("# Goal")
    assert (ws / "iterations").is_dir() and (ws / "logs").is_dir()
    st = status.read_status(ws)
    assert st["approval"] == "autonomous" and st["run"] == "2026-07-denoise"
    b = budget.read_budget(ws)
    assert b["budgets"]["max_iterations"] == 3
    assert b["budgets"]["max_experiment_runs"] == 40  # default preserved
    cfg = yaml.safe_load((ws / "config.yml").read_text())
    assert cfg["approval"] == "autonomous" and cfg["max_iterations"] == 3
    assert "Research notebook" in (ws / "notebook.md").read_text()


def test_init_refuses_existing_workspace(tmp_path):
    goal = make_goal(tmp_path)
    sfx_init.init_workspace("dup", goal, tmp_path / "workspace", {}, ROOT)
    with pytest.raises(FileExistsError):
        sfx_init.init_workspace("dup", goal, tmp_path / "workspace", {}, ROOT)
