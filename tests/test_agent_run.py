import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_RUN = ROOT / "scripts" / "agent_run.py"


def run_dispatch(agent: str, prompt_file: Path, transcript: Path, cwd: Path | None = None):
    argv = [sys.executable, str(AGENT_RUN), agent, str(prompt_file), str(transcript)]
    if cwd is not None:
        argv += ["--cwd", str(cwd)]
    return subprocess.run(argv, capture_output=True, text=True)


def test_dispatch_stub_writes_output_and_transcript(tmp_path):
    out = tmp_path / "hypothesis.md"
    prompt = tmp_path / "prompt.md"
    prompt.write_text(f"output: {out}\nkind: hypothesis\n")
    transcript = tmp_path / "logs" / "t.md"
    proc = run_dispatch("stub", prompt, transcript)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert "stub: wrote" in transcript.read_text()


def test_dispatch_respects_cwd(tmp_path):
    # {root} substitution must make the stub launchable from any cwd
    out = tmp_path / "lit.md"
    prompt = tmp_path / "prompt.md"
    prompt.write_text(f"output: {out}\nkind: literature\n")
    proc = run_dispatch("stub", prompt, tmp_path / "t.md", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()


def test_unknown_agent_fails(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("x")
    proc = run_dispatch("nope", prompt, tmp_path / "t.md")
    assert proc.returncode != 0
    assert "unknown agent" in proc.stderr


def test_relative_cwd_resolves_against_repo_root(tmp_path):
    # invoked from tmp_path with a repo-relative --cwd; must not depend on invoker cwd
    out = tmp_path / "syn.md"
    prompt = tmp_path / "prompt.md"
    prompt.write_text(f"output: {out}\nkind: synthesis\n")
    argv = [sys.executable, str(AGENT_RUN), "stub", str(prompt), str(tmp_path / "t.md"),
            "--cwd", "vendors"]
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()


def test_launch_failure_writes_transcript(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("output: x\nkind: synthesis\n")
    transcript = tmp_path / "t.md"
    proc = run_dispatch("stub", prompt, transcript, cwd=tmp_path / "does-not-exist")
    assert proc.returncode != 0
    assert "failed to launch" in transcript.read_text()
