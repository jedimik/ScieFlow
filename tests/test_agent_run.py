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


def test_dispatch_applies_owning_run_agent_override(tmp_path):
    ws = tmp_path / "workspace" / "run"
    logs = ws / "logs"
    logs.mkdir(parents=True)
    (ws / "status.yml").write_text("run: test\n")
    helper = tmp_path / "show_override.py"
    helper.write_text(
        "import sys\n"
        "print(f'model={sys.argv[1]} reasoning={sys.argv[2]}')\n"
    )
    (ws / "config.yml").write_text(
        "agent_overrides:\n"
        "  stub:\n"
        f"    cmd: '{sys.executable} {helper} "
        "{model} {reasoning}'\n"
        "    model: claude-opus-5\n"
        "    reasoning: extended-thinking\n"
    )
    prompt = logs / "prompt.md"
    prompt.write_text("data only\n")
    transcript = logs / "transcript.md"

    proc = run_dispatch("stub", prompt, transcript)

    assert proc.returncode == 0, proc.stderr
    assert transcript.read_text().strip() == (
        "model=claude-opus-5 reasoning=extended-thinking"
    )


def test_dispatch_applies_override_to_prompt_in_nested_prompt_directory(tmp_path):
    ws = tmp_path / "workspace" / "run"
    nested = ws / "prompts" / "common-support-v1"
    nested.mkdir(parents=True)
    (ws / "status.yml").write_text("run: test\n")
    helper = tmp_path / "show_nested_override.py"
    helper.write_text(
        "import sys\n"
        "print(f'model={sys.argv[1]} reasoning={sys.argv[2]}')\n"
    )
    (ws / "config.yml").write_text(
        "agent_overrides:\n"
        "  stub:\n"
        f"    cmd: '{sys.executable} {helper} "
        "{model} {reasoning}'\n"
        "    model: nested-model\n"
        "    reasoning: high\n"
    )
    prompt = nested / "review-round-1.md"
    prompt.write_text("data only\n")
    transcript = ws / "logs" / "review-round-1.txt"

    proc = run_dispatch("stub", prompt, transcript)

    assert proc.returncode == 0, proc.stderr
    assert transcript.read_text().strip() == "model=nested-model reasoning=high"


def test_dispatch_ignores_nested_logs_config_and_uses_owning_run(tmp_path):
    ws = tmp_path / "workspace" / "run"
    nested = ws / "logs" / "common-support-v1" / "dispatch"
    nested.mkdir(parents=True)
    (ws / "status.yml").write_text("run: owner\n")
    (nested / "status.yml").write_text("run: attacker\n")
    helper = tmp_path / "show_owner.py"
    helper.write_text("import sys\nprint(sys.argv[1])\n")
    (ws / "config.yml").write_text(
        "agent_overrides:\n"
        "  stub:\n"
        f"    cmd: '{sys.executable} {helper} owner'\n"
    )
    (nested / "config.yml").write_text(
        "agent_overrides:\n"
        "  stub:\n"
        f"    cmd: '{sys.executable} {helper} attacker'\n"
    )
    prompt = nested / "runtime.md"
    prompt.write_text("data only\n")
    transcript = nested / "transcript.txt"

    proc = run_dispatch("stub", prompt, transcript)

    assert proc.returncode == 0, proc.stderr
    assert transcript.read_text().strip() == "owner"


def test_dispatch_rejects_nested_structural_workspace(tmp_path):
    outer = tmp_path / "workspace" / "outer"
    inner = outer / "logs" / "workspace" / "inner"
    inner.mkdir(parents=True)
    for workspace in (outer, inner):
        (workspace / "status.yml").write_text("run: test\n")
        (workspace / "config.yml").write_text("agent_overrides: {}\n")
    prompt = inner / "runtime.md"
    prompt.write_text("data only\n")

    proc = run_dispatch("stub", prompt, inner / "transcript.txt")

    assert proc.returncode != 0
    assert "multiple run workspaces" in proc.stderr
