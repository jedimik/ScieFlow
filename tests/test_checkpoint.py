from pathlib import Path

import checkpoint
import sfx_init
import status

ROOT = Path(__file__).resolve().parents[1]


def make_ws(tmp_path):
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\n")
    return sfx_init.init_workspace("run-a", goal, tmp_path / "workspace", {}, ROOT)


def test_checkpoint_records_reason_and_resume_point(tmp_path):
    ws = make_ws(tmp_path)
    st = status.read_status(ws)
    status.mark(st, "hypothesize", "done")
    status.write_status(ws, st)
    st = checkpoint.checkpoint(ws, "low-budget", detail="wall clock at 92%")
    assert st["stopped"]["reason"] == "low-budget"
    assert "iteration 1" in st["stopped"]["resume"]
    assert "experiment" in st["stopped"]["resume"]
    # persisted
    assert status.read_status(ws)["stopped"]["detail"] == "wall clock at 92%"


def test_resume_info(tmp_path):
    ws = make_ws(tmp_path)
    assert "hypothesize" in checkpoint.resume_info(ws)
    checkpoint.checkpoint(ws, "user")
    assert "Resume:" in checkpoint.resume_info(ws)
