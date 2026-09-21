"""Old entry points must keep working for chats from before the merge."""

import subprocess
import sys
from pathlib import Path

import pytest

from scieflow.core import legacy

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["a", "p", "t", "--cwd", "vendors/ResearchX"], ["a", "p", "t"]),
        (["a", "p", "t", "--cwd=./vendors/ExperimentX/sub"], ["a", "p", "t"]),
        (["a", "p", "t", "--cwd", "workspace/x"], ["a", "p", "t", "--cwd", "workspace/x"]),
        (["a", "p", "t"], ["a", "p", "t"]),
    ],
)
def test_translate_cwd_drops_only_vendor_paths(argv, expected):
    assert legacy.translate_cwd(argv) == expected


def test_env_prefers_current_then_legacy(monkeypatch):
    monkeypatch.delenv("SCIEFLOW_NEWS_DB", raising=False)
    monkeypatch.setenv("WHATSNEW_DB", "/old.json")
    assert legacy.env("SCIEFLOW_NEWS_DB") == "/old.json"
    monkeypatch.setenv("SCIEFLOW_NEWS_DB", "/new.json")
    assert legacy.env("SCIEFLOW_NEWS_DB") == "/new.json"
    assert legacy.env("UNSET_ANYWHERE", "d") == "d"


def test_notice_can_be_silenced(monkeypatch, capsys):
    legacy.notice("old", "new")
    assert "legacy: old → new" in capsys.readouterr().err
    monkeypatch.setenv("SCIEFLOW_LEGACY_QUIET", "1")
    legacy.notice("old", "new")
    assert capsys.readouterr().err == ""


def test_expx_run_without_experiments_dir_names_the_fix(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["expx", "sweep", "-c", "c.yaml"])
    with pytest.raises(SystemExit) as exc:
        legacy.expx_main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--experiments-dir workspace/<slug>/experiments" in err
    assert "scieflow experiment sweep -c c.yaml" in err


def test_every_mapping_target_is_a_real_command():
    for new in legacy.COMMANDS.values():
        assert new.startswith(("uv run scieflow", "python -m scieflow", "from scieflow"))


def test_agent_run_shim_runs_the_stub_from_a_vendor_cwd(tmp_path):
    prompt = tmp_path / "prompt.md"
    output = tmp_path / "hypothesis.md"
    prompt.write_text(f"kind: hypothesis\noutput: {output}\n")
    transcript = tmp_path / "out.md"
    done = subprocess.run(
        [sys.executable, "scripts/agent_run.py", "stub", str(prompt), str(transcript),
         "--cwd", "vendors/ResearchX"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert "legacy:" in done.stderr
    assert "vendors/ResearchX" not in done.stderr.split("→", 1)[-1]
    assert done.returncode == 0, done.stderr
    assert output.exists()
