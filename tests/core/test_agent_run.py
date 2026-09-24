import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_run

ROOT = Path(__file__).resolve().parents[2]
AGENT_RUN = [sys.executable, "-m", "scieflow.core.agent_run"]


def test_agy_defaults_use_explicit_effort_and_nested_timeouts():
    # agy's independent five-minute default can expire even when our wrapper
    # allows much longer. Cover both argv and large-prompt/stdin dispatch.
    from scieflow.core.agent_run import build_argv

    cfg = yaml.safe_load((ROOT / "config" / "agents.yml").read_text())["agents"]["agy"]
    assert cfg["model"] == "gemini-3.1-pro-high"
    for use_stdin in (False, True):
        argv = build_argv(
            cfg, "bounded audit", ROOT, include_prompt=not use_stdin,
            template=cfg["stdin_cmd"] if use_stdin else cfg["cmd"],
        )
        assert argv[argv.index("--model") + 1] == cfg["model"]
        assert argv[argv.index("--effort") + 1] == "high"
        inner_timeout = argv[argv.index("--print-timeout") + 1]
        assert inner_timeout.endswith("m")
        assert 5 < float(inner_timeout[:-1]) < cfg["timeout_min"]
        assert ("--print" in argv) is not use_stdin
        assert ("bounded audit" in argv) is not use_stdin


def run_dispatch(agent: str, prompt_file: Path, transcript: Path, cwd: Path | None = None):
    # These prompts belong to no run, which the sandbox refuses by design.
    argv = [*AGENT_RUN, "--no-sandbox", agent, str(prompt_file), str(transcript)]
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
    # This prompt belongs to no run, which the sandbox refuses by design.
    argv = [*AGENT_RUN, "--no-sandbox", "stub", str(prompt), str(tmp_path / "t.md"),
            "--cwd", "src"]
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


# --- behaviour inside a standalone repo (config found from the working directory) ---

STUB_CMD = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"


def make_repo(tmp_path: Path, stub_cmd: str = STUB_CMD) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f"""
agents:
  stub: {{cmd: "{stub_cmd}", enabled: true, timeout_min: 1}}
  sleepy: {{cmd: "sleep 300", enabled: true, timeout_min: 0.02}}
  failing: {{cmd: "sh -c 'echo boom; exit 3'", enabled: true, timeout_min: 1}}
"""
    )
    return tmp_path


def run_in_repo(root: Path, agent: str, prompt: str, env: dict | None = None):
    (root / "prompt.md").write_text(prompt)
    return subprocess.run(
        [*AGENT_RUN, "--no-sandbox", agent, str(root / "prompt.md"), str(root / "out.log")],
        capture_output=True, text=True, cwd=root, env=env,
    )


def test_repo_config_is_found_from_working_directory(tmp_path):
    root = make_repo(tmp_path)
    proc = run_in_repo(root, "stub", "task\noutput: findings/stub.json\nkind: findings\n")
    assert proc.returncode == 0, proc.stderr
    data = json.loads((root / "findings" / "stub.json").read_text())
    assert data["papers"][0]["relevance"]["score"] in range(1, 6)
    assert (root / "out.log").exists()


def test_timeout_exits_124(tmp_path):
    root = make_repo(tmp_path)
    proc = run_in_repo(root, "sleepy", "hi")
    assert proc.returncode == 124
    assert "timed out" in (root / "out.log").read_text()


def test_failing_agent_exit_code_and_transcript(tmp_path):
    root = make_repo(tmp_path)
    proc = run_in_repo(root, "failing", "hi")
    assert proc.returncode == 3
    assert "boom" in (root / "out.log").read_text()


def test_stdin_fallback_for_oversized_prompt(tmp_path):
    root = make_repo(tmp_path)
    prompt = "task\noutput: findings/stub.json\nkind: findings\n" + ("x" * 200)
    env = {**os.environ, "SCIEFLOW_PROMPT_ARGV_LIMIT": "10"}
    proc = run_in_repo(root, "stub", prompt, env=env)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((root / "findings" / "stub.json").read_text())["agent"] == "stub"


def test_stdin_cmd_template_used_for_oversized_prompt(tmp_path):
    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "agents.yml").write_text(
        """
agents:
  marker:
    cmd: "sh -c 'echo argv-mode'"
    stdin_cmd: "sh -c 'echo stdin-mode'"
    enabled: true
    timeout_min: 1
"""
    )
    assert run_in_repo(root, "marker", "small").returncode == 0
    assert "argv-mode" in (root / "out.log").read_text()
    env = {**os.environ, "SCIEFLOW_PROMPT_ARGV_LIMIT": "10"}
    assert run_in_repo(root, "marker", "x" * 200, env=env).returncode == 0
    assert "stdin-mode" in (root / "out.log").read_text()


def test_run_overrides_apply_only_inside_the_owning_run(tmp_path):
    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "agents.yml").write_text(
        'agents:\n  echoer: {cmd: "echo m={model} r={reasoning}", model: base,'
        " reasoning: low, enabled: true, timeout_min: 1}\n"
    )
    ws = root / "workspace" / "2026-07-test"
    (ws / "prompts").mkdir(parents=True)
    (ws / "status.yml").write_text("workflow: lit-review\n")
    (ws / "config.yml").write_text("agent_overrides:\n  echoer:\n    model: big\n    reasoning: high\n")
    prompt = ws / "prompts" / "task.md"
    prompt.write_text("hi")
    proc = subprocess.run([*AGENT_RUN, "echoer", str(prompt), str(root / "out.log")],
                          capture_output=True, text=True, cwd=root)
    assert proc.returncode == 0, proc.stderr
    assert "m=big r=high" in (root / "out.log").read_text()

    assert run_in_repo(root, "echoer", "hi").returncode == 0
    assert "m=base r=low" in (root / "out.log").read_text()


def test_python_placeholder_is_the_running_interpreter():
    from scieflow.core.agent_run import build_argv

    argv = build_argv({"cmd": "{python} -m x {prompt}"}, "p", ROOT)
    assert argv == [sys.executable, "-m", "x", "p"]


def test_agent_run_reachable_from_root_cli(tmp_path):
    out = tmp_path / "hyp.md"
    prompt = tmp_path / "prompt.md"
    prompt.write_text(f"output: {out}\nkind: hypothesis\n")
    # This prompt belongs to no run, which the sandbox refuses by design.
    proc = subprocess.run(
        [sys.executable, "-m", "scieflow.cli", "agent", "run", "--no-sandbox", "stub",
         str(prompt), str(tmp_path / "t.md")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()


def make_loop_workspace(root: Path, wall_cap: int = 60) -> Path:
    from scieflow.core.run import budget, status

    # The dispatch subprocess runs in this repo; a refusal checkpoints the run,
    # which needs the status vocabulary.
    (root / "schemas").mkdir(exist_ok=True)
    (root / "schemas" / "status.yml").write_text((ROOT / "schemas" / "status.yml").read_text())
    ws = root / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    # A real run always carries config.yml beside status.yml (run/init.py).
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\n")
    status.write_status(ws, {**status.new_status("r1", "autonomous")})
    budget.write_budget(ws, budget.new_budget(3, 10, wall_cap))
    return ws


def dispatch_in_ws(root: Path, ws: Path, agent: str, prompt: str):
    (ws / "logs" / "p.md").write_text(prompt)
    return subprocess.run(
        [*AGENT_RUN, agent, str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root,
    )


def test_timeout_transcript_keeps_partial_output(tmp_path):
    root = make_repo(tmp_path, stub_cmd=STUB_CMD)
    (root / "config" / "agents.yml").write_text(
        "agents:\n"
        "  talky: {cmd: \"sh -c 'echo partial-output; sleep 300'\", enabled: true, timeout_min: 0.02}\n")
    proc = run_in_repo(root, "talky", "hi")
    assert proc.returncode == 124
    transcript = (root / "out.log").read_text()
    assert "partial-output" in transcript and "timed out" in transcript


def test_dispatch_inside_a_run_is_a_recorded_job_with_events(tmp_path):
    from scieflow.core import events

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    out = ws / "iterations" / "h.md"
    proc = dispatch_in_ws(root, ws, "stub", f"output: {out}\nkind: hypothesis\n")
    assert proc.returncode == 0, proc.stderr
    assert list((ws / "jobs").glob("*.json"))
    types = [e["type"] for e in events.read(ws)]
    assert types[:3] == ["job.queued", "job.started", "job.finished"]
    assert "budget.recorded" in types        # wall time accumulated by the runner


def test_dispatch_refused_when_wall_time_is_spent(tmp_path):
    from scieflow.core.run import actions, status

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root, wall_cap=1)
    actions.record_spend(ws, wall_minutes=2)
    proc = dispatch_in_ws(root, ws, "stub", "output: x.md\nkind: hypothesis\n")
    assert proc.returncode == 75
    assert "budget exhausted" in (ws / "logs" / "t.md").read_text()
    assert status.read_status(ws)["stopped"]["reason"] == "low-budget"


def test_prepare_is_callable_without_exiting(tmp_path):
    from scieflow.core.agent_run import DispatchError, prepare
    from scieflow.core.project import Project

    root = make_repo(tmp_path)
    (root / "p.md").write_text("hello")
    # This prompt belongs to no run, which the sandbox refuses by design.
    d = prepare(Project(root), "stub", root / "p.md", sandbox_enabled=False)
    assert d.agent == "stub" and d.run_dir is None and d.timeout_s == 60
    with pytest.raises(DispatchError):
        prepare(Project(root), "nope", root / "p.md", sandbox_enabled=False)


def bwrap_free_env(tmp_path):
    """A PATH with no bwrap on it, and a throwaway HOME so a probe cannot
    litter the real one."""
    empty = tmp_path / "emptybin"
    empty.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {**os.environ, "PATH": str(empty), "HOME": str(home)}


def fake_bwrap_env(tmp_path):
    """A `bwrap` that confines nothing: it drops its own options and runs the
    command. The sandbox looks available but does not work — the case a
    container without user namespaces produces."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "bwrap"
    shim.write_text('#!/bin/sh\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n')
    shim.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(home)}


def test_dispatch_is_refused_when_bubblewrap_is_missing(tmp_path):
    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (ws / "logs" / "p.md").write_text("output: x.md\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=bwrap_free_env(tmp_path))
    assert proc.returncode == 77
    assert "bubblewrap" in (proc.stdout + proc.stderr)
    assert "apt-get install" in (proc.stdout + proc.stderr)
    assert "sandbox" in (ws / "logs" / "t.md").read_text()


def test_dispatch_is_refused_when_the_sandbox_does_not_confine(tmp_path):
    """bwrap is on PATH but confines nothing. Only the probe catches this."""
    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (ws / "logs" / "p.md").write_text("output: x.md\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=fake_bwrap_env(tmp_path))
    assert proc.returncode == 77
    assert "did not block" in (proc.stdout + proc.stderr + (ws / "logs" / "t.md").read_text())


def test_no_sandbox_flag_runs_and_records_the_choice(tmp_path):
    from scieflow.core import events

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    out = ws / "iterations" / "h.md"
    (ws / "logs" / "p.md").write_text(f"output: {out}\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "--no-sandbox", "stub",
         str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=bwrap_free_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert "sandbox.disabled" in [e["type"] for e in events.read(ws)]


def test_an_agent_cannot_disable_its_own_sandbox(tmp_path):
    """The run's own config.yml sits inside the dispatch's writable bind. If
    `sandbox: off` there still worked, a confined agent could append it and the
    next dispatch would run unconfined — choosing its own `cmd` through
    `agent_overrides:` in the same file. The key is never honoured."""
    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (ws / "config.yml").write_text(
        "slug: r1\napproval: autonomous\nsandbox: off\n")
    out = ws / "iterations" / "h.md"
    (ws / "logs" / "p.md").write_text(f"output: {out}\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=bwrap_free_env(tmp_path))
    assert proc.returncode == 77, proc.stdout + proc.stderr
    assert not out.exists()          # nothing ran unconfined


def test_a_stale_run_sandbox_key_says_where_the_hatch_moved(tmp_path):
    """Ignoring the key silently would leave a run looking opted out when it is
    not, so the dispatch is refused with the new location named."""
    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\nsandbox: off\n")
    (ws / "logs" / "p.md").write_text("output: x.md\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root)
    assert proc.returncode == 77
    said = proc.stdout + proc.stderr + (ws / "logs" / "t.md").read_text()
    assert "config/sandbox.yml" in said
    assert "unsandboxed_runs" in said


def test_the_allowlist_can_let_a_run_dispatch_unsandboxed(tmp_path):
    """The escape hatch survives the move — in config/, which no agent can write."""
    from scieflow.core import events

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (root / "config" / "sandbox.yml").write_text(
        "unsandboxed_runs:\n  - slug: r1\n    reason: a human decided\n")
    out = ws / "iterations" / "h.md"
    (ws / "logs" / "p.md").write_text(f"output: {out}\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=bwrap_free_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert "sandbox.disabled" in [e["type"] for e in events.read(ws)]


def test_the_allowlist_opt_out_does_not_leak_to_other_runs(tmp_path):
    """Listing one slug must not unconfine the run next door."""
    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (root / "config" / "sandbox.yml").write_text(
        "unsandboxed_runs:\n  - slug: someone-else\n    reason: not this run\n")
    (ws / "logs" / "p.md").write_text("output: x.md\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root, env=bwrap_free_env(tmp_path))
    assert proc.returncode == 77


def test_a_dispatch_outside_any_run_says_how_to_proceed(tmp_path):
    """Refused rather than promoted to wider permissions — and the message
    names the flag that makes an ad-hoc dispatch possible."""
    root = make_repo(tmp_path)
    (root / "p.md").write_text("output: out.md\nkind: hypothesis\n")
    proc = subprocess.run([*AGENT_RUN, "stub", str(root / "p.md"), str(root / "t.md")],
                          capture_output=True, text=True, cwd=root)
    assert proc.returncode == 77
    assert "--no-sandbox" in (proc.stdout + proc.stderr)


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")
def test_a_sandboxed_agent_cannot_write_to_module_code(tmp_path):
    """The headline guarantee of this milestone. If one assertion survives from
    this plan, it is this one."""
    root = make_repo(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "untouched.py").write_text("original\n")
    ws = make_loop_workspace(root)
    target = root / "src" / "untouched.py"
    (root / "config" / "agents.yml").write_text(
        "agents:\n"
        f'  writer: {{cmd: "sh -c \'echo hacked > {target}\'", enabled: true, timeout_min: 1}}\n')
    (ws / "logs" / "p.md").write_text("go\n")
    subprocess.run([*AGENT_RUN, "writer", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
                   capture_output=True, text=True, cwd=root)
    assert target.read_text() == "original\n"     # the write never landed


def test_a_sandboxed_dispatch_gets_its_own_uv_cache(tmp_path):
    """uv hardlinks cache files into .venv, so a writable shared cache is a
    writable host virtualenv. Each dispatch caches inside its own run instead."""
    from scieflow.core.agent_run import prepare
    from scieflow.core.project import Project

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (ws / "logs" / "p.md").write_text("go\n")
    d = prepare(Project(root), "stub", ws / "logs" / "p.md")
    assert d.env["UV_CACHE_DIR"] == str((ws / ".uv-cache").resolve())
    assert (Path.home() / ".cache" / "uv") not in d.writable


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")
def test_a_sandboxed_agent_cannot_write_the_shared_uv_cache(tmp_path):
    """The shared cache is hardlinked into the host venv: a write there is a
    write to code the host will execute outside any sandbox."""
    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    home = tmp_path / "home"
    cache = home / ".cache" / "uv"
    cache.mkdir(parents=True)
    victim = cache / "victim"
    victim.write_text("original\n")
    (root / "config" / "agents.yml").write_text(
        "agents:\n"
        f'  writer: {{cmd: "sh -c \'echo hacked > {victim}\'", enabled: true, timeout_min: 1}}\n')
    (ws / "logs" / "p.md").write_text("go\n")
    subprocess.run([*AGENT_RUN, "writer", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
                   capture_output=True, text=True, cwd=root,
                   env={**os.environ, "HOME": str(home)})
    assert victim.read_text() == "original\n"


def test_a_writable_grant_that_is_a_file_is_refused_not_a_traceback(tmp_path):
    """A plain SandboxError (not SandboxUnavailable) must still exit 77 on the
    CLI path; it used to escape as a traceback with exit 1."""
    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (root / "config" / "journals").write_text("not a directory\n")
    (root / "config" / "sandbox.yml").write_text(
        "writable:\n  - path: config/journals\n    reason: journal cache\n")
    (ws / "logs" / "p.md").write_text("output: x.md\nkind: hypothesis\n")
    proc = subprocess.run(
        [*AGENT_RUN, "stub", str(ws / "logs" / "p.md"), str(ws / "logs" / "t.md")],
        capture_output=True, text=True, cwd=root)
    assert proc.returncode == 77, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "must be a directory" in (proc.stdout + proc.stderr)


def test_an_unreadable_run_config_does_not_escape_as_a_traceback(tmp_path):
    """A config.yml that cannot be read is a typed answer (sandbox stays on),
    not an OSError escaping through the dispatch path."""
    from scieflow.core.agent_run import sandbox_disabled_in_run
    from scieflow.core.project import Project

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    (ws / "config.yml").chmod(0o000)
    try:
        assert sandbox_disabled_in_run(Project(root), ws) is False
    finally:
        (ws / "config.yml").chmod(0o644)


# --- charter pinning ---


@pytest.fixture
def project_with_run(tmp_path):
    """A project with a run workspace, and a prompt file that
    ``agent_run.owning_run_workspace`` resolves to that workspace — so a
    charter written to it is the one ``prepare`` finds."""
    from scieflow.core.project import Project

    root = make_repo(tmp_path)
    ws = make_loop_workspace(root)
    prompt_file = ws / "logs" / "prompt.md"
    prompt_file.write_text("output: x.md\nkind: hypothesis\nbody")
    return Project(root), ws, prompt_file


def test_the_charter_is_pinned_to_the_top_of_the_prompt(project_with_run):
    """Falsification test: this is the requirement most likely to rot
    silently. Without it the only symptom is an agent losing the goal,
    months later. Deleting the pinning from compose_prompt must fail here."""
    from scieflow.core.run import charter

    project, ws, prompt_file = project_with_run
    charter.set_text(ws, "Goal: find a better catalyst. Do not change it.")
    prompt_file.write_text("output: x.md\nkind: hypothesis\nNow do the next step.")

    dispatch = agent_run.prepare(project, "stub", prompt_file)
    sent = dispatch.stdin_text or " ".join(dispatch.argv)

    assert "find a better catalyst" in sent
    assert sent.index("find a better catalyst") < sent.index("Now do the next step")


def test_a_run_without_a_charter_composes_the_prompt_unchanged(project_with_run):
    """Every run that predates this feature has no charter.yml."""
    project, ws, prompt_file = project_with_run
    prompt_file.write_text("output: x.md\nkind: hypothesis\nbody")

    dispatch = agent_run.prepare(project, "stub", prompt_file)
    sent = dispatch.stdin_text or " ".join(dispatch.argv)

    assert agent_run.CHARTER_HEADER not in sent
    assert sent.rstrip().endswith("body")


def test_charter_braces_reach_the_agent_literally(project_with_run):
    """build_argv substitutes {model}/{reasoning}/{root}/{python} and THEN
    {prompt}, so charter text is inserted last and its braces are never
    interpolated. That safety is real but rests entirely on that ordering —
    this test is what stops a future reorder turning charter text into
    command templating."""
    from scieflow.core.run import charter

    project, ws, prompt_file = project_with_run
    charter.set_text(ws, "Use {model} and {root}; mind $PATH and `backticks`.")
    prompt_file.write_text("output: x.md\nkind: hypothesis\nbody")

    dispatch = agent_run.prepare(project, "stub", prompt_file)
    sent = dispatch.stdin_text or " ".join(dispatch.argv)

    assert "{model}" in sent and "{root}" in sent
    assert "`backticks`" in sent


def test_a_charter_that_pushes_the_prompt_over_the_argv_limit_still_sends_it(
        project_with_run, monkeypatch):
    """The charter is what makes prompts grow, so this plan is what makes
    this path reachable. An agent with no `stdin_cmd` (codex has none) must
    not end up invoked with no prompt at all."""
    from scieflow.core.run import charter

    project, ws, prompt_file = project_with_run
    monkeypatch.setattr(agent_run, "PROMPT_ARGV_LIMIT", 200)
    charter.set_text(ws, "G" * 500)
    prompt_file.write_text("output: x.md\nkind: hypothesis\nbody")

    dispatch = agent_run.prepare(project, "stub", prompt_file)
    sent = dispatch.stdin_text or " ".join(dispatch.argv)
    assert "G" * 500 in sent, "the prompt was dropped instead of sent on stdin"
