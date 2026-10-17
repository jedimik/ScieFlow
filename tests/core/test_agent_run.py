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


def _add_agent(project_root: Path, name: str, block: str) -> None:
    path = project_root / "config" / "agents.yml"
    path.write_text(path.read_text() + f"  {name}:\n{block}")


def test_an_oversized_conversational_prompt_keeps_resume_and_session(
        project_with_run, monkeypatch):
    """Finding 2 (2026-09-25 review): `claude` and `agy` both declare a plain
    `stdin_cmd` alongside `session_cmd`/`resume_cmd`. Before this fix, any
    conversational turn whose composed prompt went over `PROMPT_ARGV_LIMIT`
    matched the (agent-agnostic) `stdin_cmd` branch first and silently ran
    the ordinary one-shot command instead — no `--resume`, no session id on
    the argv, and nothing for `sessions.parse` to recover on the far side."""
    project, ws, prompt_file = project_with_run
    _add_agent(project.root, "claude-like",
              '    cmd: "echo {prompt}"\n'
              '    stdin_cmd: "echo stdin-mode"\n'
              '    session_cmd: "echo start {prompt}"\n'
              '    resume_cmd: "echo resume --resume {session} {prompt}"\n'
              '    family: claude\n    enabled: true\n    timeout_min: 1\n')
    monkeypatch.setattr(agent_run, "PROMPT_ARGV_LIMIT", 10)
    prompt_file.write_text("output: x.md\nkind: hypothesis\n" + ("x" * 200))

    dispatch = agent_run.prepare(project, "claude-like", prompt_file,
                                 session="sess-123", conversational=True)

    assert "stdin-mode" not in dispatch.argv
    assert "sess-123" in dispatch.argv
    assert dispatch.stdin_text is not None and "x" * 200 in dispatch.stdin_text


def test_an_oversized_conversational_prompt_refuses_when_stdin_is_unsafe(
        project_with_run, monkeypatch):
    """`agy`'s session/resume templates take the prompt as `--print`'s value
    (`agy --print {prompt} ...`); dropping only the `{prompt}` token would
    leave `--print` dangling to swallow the next flag. There is no stdin
    form of the conversational command to fall back to, so this must refuse
    loudly (`DispatchError`) rather than send a broken argv."""
    project, ws, prompt_file = project_with_run
    _add_agent(project.root, "agy-like",
              '    cmd: "echo --print {prompt}"\n'
              '    stdin_cmd: "echo stdin-mode"\n'
              '    session_cmd: "echo --print {prompt} --output-format json"\n'
              '    resume_cmd: "echo --print {prompt} --conversation {session}"\n'
              '    family: agy\n    enabled: true\n    timeout_min: 1\n')
    monkeypatch.setattr(agent_run, "PROMPT_ARGV_LIMIT", 10)
    prompt_file.write_text("output: x.md\nkind: hypothesis\n" + ("x" * 200))

    with pytest.raises(agent_run.DispatchError, match="agy-like"):
        agent_run.prepare(project, "agy-like", prompt_file,
                          session="sess-123", conversational=True)


def test_a_charter_that_is_not_a_mapping_fails_the_dispatch_cleanly(project_with_run):
    """`compose_prompt` used to run outside `prepare`'s try/except, so a
    `charter.yml` that parses to a list raised a bare `AttributeError` clean
    through `prepare` — a traceback with no transcript, and nothing for a
    coordinator to retry with. It must come out as `DispatchError` instead."""
    project, ws, prompt_file = project_with_run
    (ws / "charter.yml").write_text("- just a list\n")

    with pytest.raises(agent_run.DispatchError):
        agent_run.prepare(project, "stub", prompt_file)


def test_malformed_charter_yaml_fails_the_dispatch_cleanly(project_with_run):
    """Same failure shape, different cause: a `charter.yml` that fails to
    parse at all used to raise `yaml.parser.ParserError` straight through
    `prepare`."""
    project, ws, prompt_file = project_with_run
    (ws / "charter.yml").write_text("current: 1\nversions: [\n")

    with pytest.raises(agent_run.DispatchError):
        agent_run.prepare(project, "stub", prompt_file)


# --- K producer baseline, through the existing file-based API ---
@pytest.mark.parametrize('session', [None, 'k-resume-existing'])
@pytest.mark.parametrize('force_stdin', [False, True])
def test_k_ordinary_baseline_protocol(k_conversational_project, monkeypatch, session, force_stdin):
    import hashlib
    import json
    import subprocess
    from scieflow.core import sessions
    from scieflow.core.run import charter

    project, ws, prompt = k_conversational_project
    charter.set_text(ws, 'K charter sentinel')
    prompt.write_text('/scieflow-menu')
    if force_stdin:
        monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 10)
    d = agent_run.prepare(project, 'k-protocol-fixture', prompt,
                          conversational=True, session=session)
    sent = d.stdin_text if d.stdin_text is not None else d.argv[-1]
    assert sent == agent_run.CHARTER_HEADER + '\n\nK charter sentinel\n\n---\n\n/scieflow-menu'
    assert (d.stdin_text is not None) == force_stdin
    proc = subprocess.run(d.argv, input=d.stdin_text, capture_output=True, text=True,
                          cwd=d.cwd, timeout=10, check=False)
    assert proc.returncode == 0, proc.stderr
    parsed = sessions.parse({'family': 'claude'}, proc.stdout)
    assert parsed.id == (session or 'k-session-1') and parsed.text == 'K protocol reply'
    assert json.loads(proc.stdout.splitlines()[-1])['input_sha256'] == hashlib.sha256(sent.encode()).hexdigest()


def _k_sent(prepared):
    return prepared.stdin_text if prepared.stdin_text is not None else prepared.argv[-1]


@pytest.mark.parametrize('session', [None, 'k-resume-existing'])
@pytest.mark.parametrize('force_stdin', [False, True])
def test_k_marked_order_exact_protocol(k_conversational_project, offline_k_support,
                                      monkeypatch, session, force_stdin):
    import hashlib
    import json
    import subprocess
    from scieflow.core import harness_commands, sessions
    from scieflow.core.run import charter
    from scieflow.core.turn_input import accept_human_submission

    project, ws, prompt = k_conversational_project
    charter.set_text(ws, '  K charter sentinel  ')
    if force_stdin:
        monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 10)
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=session)
    assert not prompt.exists()
    offline_k_support(resolved.harness_identity)
    prepared = agent_run.prepare_turn(resolved, accept_human_submission('/scieflow-menu', 'harness-command', '0'))
    expected = '/scieflow-menu\n\n---\n\n' + agent_run.CHARTER_HEADER + '\n\nK charter sentinel'
    assert _k_sent(prepared).encode() == expected.encode()
    assert (prepared.stdin_text is not None) == force_stdin
    assert prepared.composed_bytes == expected.encode() and prepared.accepted_bytes == b'/scieflow-menu'
    assert prepared.composition_order == 'command-first' and prepared.input_kind == 'harness-command'
    assert prepared.selection_revision == '0' and prepared.policy_revision == 'fixture-only'
    assert prepared.charter_version == 1
    assert prepared.charter_sha256 == hashlib.sha256(b'K charter sentinel').hexdigest()
    assert prepared.protection is resolved.protection
    assert prepared.harness_identity is resolved.harness_identity
    assert harness_commands.effective_identity(resolved) is resolved.harness_identity
    assert prepared.env_items is resolved.env_items
    proc = subprocess.run(prepared.argv, input=prepared.stdin_text, capture_output=True,
                          text=True, cwd=prepared.cwd, timeout=10, check=False)
    assert proc.returncode == 0, proc.stderr
    parsed = sessions.parse({'family': 'claude'}, proc.stdout)
    assert parsed.id == (session or 'k-session-1') and parsed.text == 'K protocol reply'
    assert json.loads(proc.stdout.splitlines()[-1])['input_sha256'] == hashlib.sha256(prepared.composed_bytes).hexdigest()
    assert list((ws / 'logs').iterdir()) == []


@pytest.mark.parametrize('force_stdin', [False, True])
def test_k_accepted_input_wins_over_artifact(k_conversational_project, offline_k_support,
                                           monkeypatch, force_stdin):
    from scieflow.core.turn_input import accept_human_submission

    project, ws, prompt = k_conversational_project
    if force_stdin:
        monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 10)
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)
    offline_k_support(resolved.harness_identity)
    accepted = accept_human_submission('/scieflow-menu', 'harness-command', '0')
    prompt.write_text('/hostile-replacement')
    prepared = agent_run.prepare_turn(resolved, accepted)
    assert _k_sent(prepared).encode() == b'/scieflow-menu'
    prompt.unlink()
    prompt.symlink_to(project.root / 'config/agents.yml')
    assert agent_run.prepare_turn(resolved, accepted) == prepared


def test_k_single_resolution_and_pure_snapshot(k_conversational_project, monkeypatch):
    import hashlib
    from dataclasses import FrozenInstanceError, replace
    from scieflow.core import sandbox
    from scieflow.core.run import charter
    from scieflow.core.turn_input import accept_human_submission

    project, ws, prompt = k_conversational_project
    charter.set_text(ws, '  Snapshot {model} `literal` "quoted" é  ')
    registry = agent_run.config.load_agents(project.root)
    counts = {'registry': 0, 'config': 0, 'policy': 0, 'charter': 0, 'protection': 0}
    real_config = Path.read_text
    real_policy, real_charter = sandbox._capture_turn_policy, charter.snapshot
    real_protection = sandbox.resolve_turn_protection
    def load(root):
        counts['registry'] += 1
        return registry
    def read(path, *args, **kwargs):
        if path == ws / 'config.yml':
            counts['config'] += 1
        return real_config(path, *args, **kwargs)
    def policy(path):
        counts['policy'] += 1
        return real_policy(path)
    def snapshot(path):
        counts['charter'] += 1
        return real_charter(path)
    def protect(project, **kwargs):
        counts['protection'] += 1
        return real_protection(project, **kwargs)
    monkeypatch.setattr(agent_run.config, 'load_agents', load)
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(sandbox, '_capture_turn_policy', policy)
    monkeypatch.setattr(sandbox, 'resolve_turn_protection', protect)
    monkeypatch.setattr(charter, 'snapshot', snapshot)
    monkeypatch.setattr(sandbox, 'environment', lambda base: {'SYNTHETIC_ENV': 'original'})
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws,
                                     session='k-existing', cwd=Path('workspace/r1'))
    assert counts == dict.fromkeys(counts, 1)
    assert resolved.cwd == ws
    # The turn env is the captured snapshot plus its own HOME and sandbox PATH (S1).
    env = dict(resolved.env_items)
    assert set(env) == {'SYNTHETIC_ENV', 'HOME', 'PATH'} and env['SYNTHETIC_ENV'] == 'original'
    assert env['HOME'] == str(ws.resolve() / 'scratch' / 'agent-home' / 'k-protocol-fixture')
    assert 'SYNTHETIC_ENV' not in repr(resolved)
    registry['k-protocol-fixture']['session_cmd'] = 'mutated {prompt}'
    registry['k-protocol-fixture']['model'] = 'mutated'
    (ws / 'config.yml').write_text('agent_overrides: [bad]\n')
    (ws / 'charter.yml').write_text('- malformed\n')
    (project.root / 'config/sandbox.yml').write_text('writable:\n  - path: src\n')
    monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 0)
    monkeypatch.setattr(agent_run.sys, 'executable', '/mutated-python')
    def forbidden(*args, **kwargs):
        raise AssertionError('pure preparation performed external lookup')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(agent_run.config, 'load_agents', forbidden)
    monkeypatch.setattr(charter, 'snapshot', forbidden)
    monkeypatch.setattr(sandbox, 'environment', forbidden)
    prepared = agent_run.prepare_turn(resolved, accept_human_submission('literal {root} `code` é'))
    assert counts == dict.fromkeys(counts, 1)
    assert prepared.stdin_text is None and prepared.argv[0] != '/mutated-python'
    assert 'mutated' not in prepared.argv
    assert prepared.composed_bytes == (agent_run.CHARTER_HEADER + '\n\nSnapshot {model} `literal` "quoted" é\n\n---\n\nliteral {root} `code` é').encode()
    assert prepared.charter_sha256 == hashlib.sha256(resolved.charter_text.encode()).hexdigest()
    assert prepared.protection is resolved.protection and prepared.env_items is resolved.env_items
    assert 'SYNTHETIC_ENV' not in repr(prepared)
    with pytest.raises(FrozenInstanceError):
        resolved.model = 'bad'
    for obj, values in ((resolved, {'env_items': {'BAD': 'mutable'}}),
                        (resolved, {'protection': {}}), (prepared, {'argv': list(prepared.argv)}),
                        (prepared, {'accepted_bytes': bytearray(b'mutable')})):
        with pytest.raises(TypeError):
            replace(obj, **values)


@pytest.mark.parametrize('broken', ['config-absent', 'status-absent', 'config-link',
    'status-link', 'config-directory', 'bad-config', 'bad-overrides', 'bad-agent-override',
    'stale-sandbox', 'malformed-charter', 'malformed-history'])
def test_k_full_resolution_refuses_before_artifacts(k_conversational_project, broken):
    from scieflow.core import sandbox

    project, ws, prompt = k_conversational_project
    if broken in ('config-absent', 'status-absent'):
        (ws / (broken.split('-')[0] + '.yml')).unlink()
    elif broken in ('config-link', 'status-link'):
        control = ws / (broken.split('-')[0] + '.yml')
        contents = control.read_bytes()
        control.unlink()
        other = ws / 'other.yml'
        other.write_bytes(contents)
        control.symlink_to(other)
    elif broken == 'config-directory':
        (ws / 'config.yml').unlink()
        (ws / 'config.yml').mkdir()
    else:
        payloads = {'bad-config': '[]\n', 'bad-overrides': 'agent_overrides: [bad]\n',
                    'bad-agent-override': 'agent_overrides:\n  k-protocol-fixture: [bad]\n',
                    'stale-sandbox': 'sandbox: off\n',
                    'malformed-charter': '- bad\n',
                    'malformed-history': 'current: 1\nversions: [bad]\n'}
        target = 'charter.yml' if broken.startswith('malformed-') else 'config.yml'
        (ws / target).write_text(payloads[broken])
    with pytest.raises((agent_run.DispatchError, sandbox.SandboxError)):
        agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)
    assert not prompt.exists() and list((ws / 'logs').iterdir()) == []


def test_k_run_authority_is_explicit_unique_and_project_owned(k_conversational_project):
    project, ws, _ = k_conversational_project
    decoy = ws / 'logs/decoy'
    decoy.mkdir()
    for run in (decoy, project.root.parent):
        with pytest.raises(agent_run.DispatchError):
            agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=run, session=None)
    nested = ws / 'workspace/child'
    nested.mkdir(parents=True)
    (nested / 'config.yml').write_text('{}\n')
    (nested / 'status.yml').write_text('{}\n')
    with pytest.raises(agent_run.DispatchError):
        agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=nested, session=None)


def test_k_unsupported_identity_and_unconfined_marked_refuse(k_conversational_project, offline_k_support):
    from dataclasses import replace
    from scieflow.core import harness_commands
    from scieflow.core.turn_input import accept_human_submission

    project, ws, prompt = k_conversational_project
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)
    assert not resolved.harness_identity.effects_pinned and resolved.harness_identity.version is None
    assert harness_commands.CERTIFIED_POLICIES == ()
    marked = accept_human_submission('/scieflow-menu', 'harness-command', '0')
    with pytest.raises(agent_run.DispatchError):
        agent_run.prepare_turn(resolved, marked)
    offline_k_support(resolved.harness_identity)
    changed = replace(resolved, harness_identity=replace(resolved.harness_identity, model='other'))
    with pytest.raises(agent_run.DispatchError):
        agent_run.prepare_turn(changed, marked)
    (project.root / 'config/sandbox.yml').write_text('unsandboxed_runs:\n  - slug: r1\n    reason: human\n')
    unconfined = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)
    offline_k_support(unconfined.harness_identity)
    with pytest.raises(agent_run.DispatchError):
        agent_run.prepare_turn(unconfined, marked)
    assert not prompt.exists()


@pytest.mark.parametrize('body', ['agent_overrides: []\n',
    'agent_overrides:\n  k-protocol-fixture: []\n'])
def test_k_falsy_invalid_override_is_not_absent(k_conversational_project, body):
    project, ws, _ = k_conversational_project
    (ws / 'config.yml').write_text(body)
    with pytest.raises(agent_run.DispatchError):
        agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)


@pytest.mark.parametrize('session', [None, 'resume "quoted" {root}'])
def test_k_captured_python_and_oversized_safety(k_conversational_project, monkeypatch, session):
    import shlex
    import sys
    import yaml
    from scieflow.core.turn_input import accept_human_submission

    project, ws, _ = k_conversational_project
    cfg_path = project.root / 'config/agents.yml'
    registry = yaml.safe_load(cfg_path.read_text())
    cfg = registry['agents']['k-protocol-fixture']
    cfg['session_cmd'] = '{python} direct first {prompt}'
    cfg['resume_cmd'] = '{python} direct --resume {session} resume {prompt}'
    cfg['stdin_cmd'] = 'wrong-one-shot {prompt}'
    cfg_path.write_text(yaml.safe_dump(registry))
    monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 10)
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=session)
    python = sys.executable
    monkeypatch.setattr(agent_run.sys, 'executable', '/changed-after-resolution')
    prepared = agent_run.prepare_turn(resolved, accept_human_submission('é' * 100))
    assert prepared.argv[0] == python and prepared.stdin_text == 'é' * 100
    assert 'wrong-one-shot' not in prepared.argv
    if session:
        assert prepared.argv[prepared.argv.index('--resume') + 1] == session
    from dataclasses import replace
    unsafe = replace(resolved, session_template='agent --print {prompt} --other flag',
                     resume_template='agent --print {prompt} --resume {session}')
    with pytest.raises(agent_run.DispatchError):
        agent_run.prepare_turn(unsafe, accept_human_submission('é' * 100))


@pytest.mark.parametrize('change', [{'enabled': False}, {'family': 'missing'},
    {'session_cmd': ''}, {'resume_cmd': ''}, {'session_cmd': '"unterminated'},
    {'model': []}, {'timeout_min': float('nan')}, {'timeout_min': True}])
def test_k_effective_full_send_validation(k_conversational_project, change):
    import yaml
    project, ws, prompt = k_conversational_project
    (ws / 'config.yml').write_text(yaml.safe_dump({'agent_overrides': {'k-protocol-fixture': change}}))
    with pytest.raises(agent_run.DispatchError):
        agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)
    assert not prompt.exists()


def test_k_exact_fixture_identity_mutations_all_deny(k_conversational_project, offline_k_support):
    from dataclasses import fields, replace
    from scieflow.core import harness_commands
    project, ws, _ = k_conversational_project
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)
    identity = resolved.harness_identity
    original = harness_commands.support_for
    offline_k_support(identity)
    assert harness_commands.support_for(identity).enabled
    for f in fields(identity):
        value = getattr(identity, f.name)
        changed = not value if type(value) is bool else 'changed'
        assert not harness_commands.support_for(replace(identity, **{f.name: changed})).enabled
    assert harness_commands.CERTIFIED_POLICIES == ()
    assert not original(identity).enabled


def test_k_binary_capture_does_not_read_unreviewed_command_contents(tmp_path, monkeypatch):
    script = tmp_path / 'unreviewed-executable'
    script.write_text('#!/bin/sh\nprivate-content\n')
    script.chmod(0o700)
    def forbidden(*args, **kwargs):
        raise AssertionError('unreviewed command file contents read')
    monkeypatch.setattr(agent_run.os, 'open', forbidden)
    executable, digest = agent_run._capture_executable(str(script), tmp_path)
    assert executable == str(script) and digest is None


# --- K Task2 correction: original-token expansion and full-send bindings ---
def _k_operands_execute(prepared):
    import hashlib
    from scieflow.core import sessions

    protocol = ROOT / 'tests/fixtures/k_conversation_protocol.py'
    assert prepared.argv[:3] == (sys.executable, '-B', str(protocol))
    assert Path(agent_run.__file__).resolve() == ROOT / 'src/scieflow/core/agent_run.py'
    proc = subprocess.run(prepared.argv, cwd=prepared.cwd, capture_output=True, text=True,
                          timeout=10, check=False, env={'SYNTHETIC_OPERANDS_TEST': 'only'},
                          input=prepared.stdin_text,
                          stdin=subprocess.DEVNULL if prepared.stdin_text is None else None)
    assert proc.returncode == 0, proc.stderr
    parsed = sessions.parse({'family': 'claude'}, proc.stdout)
    assert parsed.text == 'K protocol reply' and parsed.id
    result = json.loads(proc.stdout.splitlines()[-1])
    print('PERMANENT_OPERAND_OBSERVATION ' + json.dumps({
        'exit_code': proc.returncode, 'session': parsed.id,
        'received_sha256': result['input_sha256'],
        'expected_sha256': hashlib.sha256(prepared.composed_bytes).hexdigest(),
        'stdin_selected': prepared.stdin_text is not None}, sort_keys=True))
    return parsed, result


@pytest.mark.parametrize('session', [None, 'k-resume-control', 'k-resume-{prompt}'])
@pytest.mark.parametrize('force_stdin', [False, True])
def test_k_operands_literal_protocol(k_conversational_project, monkeypatch, session, force_stdin):
    import hashlib
    from scieflow.core.run import charter
    from scieflow.core.turn_input import accept_human_submission

    project, ws, prompt = k_conversational_project
    charter.set_text(ws, 'Literal charter {model} é')
    if force_stdin:
        monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 1)
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=session)
    accepted = accept_human_submission('Accepted {root} "quoted" `literal` é')
    prepared = agent_run.prepare_turn(resolved, accepted)
    expected = (agent_run.CHARTER_HEADER + '\n\nLiteral charter {model} é\n\n---\n\n' + accepted.message).encode()
    assert prepared.accepted_bytes == accepted.message.encode() and prepared.composed_bytes == expected
    assert prepared.protection is resolved.protection and prepared.session == session
    assert (prepared.stdin_text is not None) == force_stdin
    parsed, result = _k_operands_execute(prepared)
    assert result['input_sha256'] == hashlib.sha256(expected).hexdigest()
    assert not prompt.exists() and list((ws / 'logs').iterdir()) == []
    assert parsed.id == (session or 'k-session-1'), 'captured session was reinterpreted as a template'
    if session:
        assert prepared.argv[prepared.argv.index('--resume') + 1] == session


@pytest.mark.parametrize('shape', ['missing-first-prompt', 'missing-resume-session'])
@pytest.mark.parametrize('force_stdin', [False, True])
def test_k_operands_missing_binding_protocol(k_conversational_project, monkeypatch, shape, force_stdin):
    import hashlib
    from scieflow.core.turn_input import accept_human_submission

    project, ws, prompt = k_conversational_project
    path = project.root / 'config/agents.yml'
    registry = yaml.safe_load(path.read_text())
    cfg = registry['agents']['k-protocol-fixture']
    session = None
    if shape == 'missing-first-prompt':
        cfg['session_cmd'] = cfg['session_cmd'].removesuffix(' {prompt}')
    else:
        cfg['resume_cmd'] = cfg['resume_cmd'].replace(' --resume {session}', '')
        session = 'k-existing-session'
    path.write_text(yaml.safe_dump(registry))
    if force_stdin:
        monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 1)
    try:
        resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=session)
        prepared = agent_run.prepare_turn(resolved, accept_human_submission('Accepted short input é'))
    except agent_run.DispatchError:
        assert not prompt.exists() and list((ws / 'logs').iterdir()) == []
        return
    # A zero process exit/usable session alone is not a valid full-send contract.
    parsed, result = _k_operands_execute(prepared)
    assert not prompt.exists() and list((ws / 'logs').iterdir()) == []
    assert result['input_sha256'] == hashlib.sha256(prepared.composed_bytes).hexdigest(), 'prepared bytes silently dropped'
    assert parsed.id == (session or 'k-session-1'), 'prepared resume silently changed session'
    pytest.fail('unsupported required binding accepted, including forced-stdin path')


@pytest.mark.parametrize('field', ['model', 'reasoning', 'root', 'python', 'session', 'prompt'])
def test_k_operands_all_inserted_fields_are_literal(field):
    import shlex

    names = ('model', 'reasoning', 'root', 'python', 'session', 'prompt')
    literal = '{model} {reasoning} {root} {python} {session} {prompt} "quoted" \'single\' é'
    values = dict.fromkeys(names, 'control')
    values[field] = literal
    template = 'tool ' + ' '.join('{' + name + '}' for name in names)
    template += ' ' + shlex.quote('embedded:{' + field + '}:end') + ' {unknown} '
    template += shlex.quote('{unknown:{model}}')
    argv = agent_run._template_argv(template, values['prompt'], Path(values['root']),
                                   model=values['model'], reasoning=values['reasoning'],
                                   python=values['python'], session=values['session'], include_prompt=True)
    assert argv == ['tool', *(values[name] for name in names), 'embedded:' + values[field] + ':end',
                    '{unknown}', '{unknown:' + values['model'] + '}']
    # Legacy omission filters ORIGINAL prompt-containing tokens, not inserted values.
    omitted = agent_run._template_argv(template, values['prompt'], Path(values['root']),
                                      model=values['model'], reasoning=values['reasoning'],
                                      python=values['python'], session=values['session'], include_prompt=False)
    assert omitted == [value for token, value in zip(shlex.split(template), argv) if '{prompt}' not in token]


def test_k_operands_executable_identity_matches_literal_launch(tmp_path, monkeypatch):
    import shlex
    from scieflow.core import sandbox
    from scieflow.core.project import Project
    from scieflow.core.turn_input import accept_human_submission

    root = tmp_path / 'identity-{python}-{model}-{session}-{prompt} "é"'
    (root / 'config').mkdir(parents=True)
    ws = root / 'workspace/r1'
    (ws / 'logs').mkdir(parents=True)
    (ws / 'config.yml').write_text('slug: r1\n')
    (ws / 'status.yml').write_text('{}\n')
    literal = '{model}-{reasoning}-{root}-{python}-{session}-{prompt} "é"'
    python = 'python-' + literal
    template = shlex.quote('{root}/{python}/{model}/{reasoning}') + ' first {prompt}'
    cfg = {'enabled': True, 'family': 'claude', 'model': 'model-' + literal,
           'reasoning': 'reasoning-' + literal, 'session_cmd': template,
           'resume_cmd': 'tool --resume {session} resume {prompt}'}
    (root / 'config/agents.yml').write_text(yaml.safe_dump({'agents': {'fixture': cfg}}))
    monkeypatch.setattr(agent_run.sys, 'executable', python)
    monkeypatch.setattr(sandbox, 'environment', lambda base: {'SYNTHETIC_OPERANDS_TEST': 'only'})
    captured = []
    def capture(token, cwd, env):
        captured.append(token)
        return Path(token), (), token
    monkeypatch.setattr(agent_run, '_capture_execution', capture)
    resolved = agent_run.resolve_turn(Project(root), 'fixture', run_dir=ws, session=None)
    prepared = agent_run.prepare_turn(resolved, accept_human_submission('ordinary text'))
    expected = str(root) + '/' + python + '/' + cfg['model'] + '/' + cfg['reasoning']
    assert captured == [expected]
    assert resolved.harness_identity.executable == prepared.argv[0] == expected


def _k_operands_bad_template(template, placeholder, shape):
    import shlex
    tokens = shlex.split(template)
    token = '{' + placeholder + '}'
    index = tokens.index(token)
    if shape == 'missing':
        tokens.pop(index)
    elif shape == 'duplicate':
        tokens.append(token)
    elif shape == 'embedded':
        tokens[index] = 'prefix-' + token
    elif shape == 'embedded-duplicate':
        tokens.append('suffix-' + token)
    elif shape == 'executable':
        tokens.pop(index)
        tokens[0] = token
    elif shape == 'executable-duplicate':
        tokens[0] = token
    return shlex.join(tokens)


@pytest.mark.parametrize('key,placeholder', [('session_cmd', 'prompt'),
    ('resume_cmd', 'prompt'), ('resume_cmd', 'session')])
@pytest.mark.parametrize('shape', ['missing', 'duplicate', 'embedded', 'embedded-duplicate',
    'executable', 'executable-duplicate'])
def test_k_operands_resolver_checks_both_templates(k_conversational_project, monkeypatch, key, placeholder, shape):
    project, ws, prompt = k_conversational_project
    path = project.root / 'config/agents.yml'
    registry = yaml.safe_load(path.read_text())
    cfg = registry['agents']['k-protocol-fixture']
    cfg[key] = _k_operands_bad_template(cfg[key], placeholder, shape)
    # Effective override of the UNSELECTED template must also fail full resolution.
    (ws / 'config.yml').write_text(yaml.safe_dump({'agent_overrides': {'k-protocol-fixture': {key: cfg[key]}}}))
    captured = []
    monkeypatch.setattr(agent_run, '_capture_execution', lambda token, cwd, env: (captured.append(token), (), None))
    with pytest.raises(agent_run.DispatchError, match=key):
        agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws,
                               session='existing' if key == 'session_cmd' else None)
    assert not captured and not prompt.exists() and list((ws / 'logs').iterdir()) == []


@pytest.mark.parametrize('key,placeholder', [('session_template', 'prompt'),
    ('resume_template', 'prompt'), ('resume_template', 'session')])
@pytest.mark.parametrize('shape', ['missing', 'duplicate', 'embedded', 'embedded-duplicate',
    'executable', 'executable-duplicate', 'empty', 'malformed'])
@pytest.mark.parametrize('force_stdin', [False, True])
def test_k_operands_prepare_revalidates_snapshot(k_conversational_project, monkeypatch, key, placeholder, shape, force_stdin):
    from dataclasses import replace
    from scieflow.core.turn_input import accept_human_submission

    project, ws, prompt = k_conversational_project
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws,
                                     session='existing' if key == 'resume_template' else None)
    template = getattr(resolved, key)
    bad = '' if shape == 'empty' else '"unterminated' if shape == 'malformed' else _k_operands_bad_template(template, placeholder, shape)
    changed = replace(resolved, **{key: bad}, prompt_argv_limit=1 if force_stdin else 100000)
    def forbidden(*args, **kwargs):
        raise AssertionError('pure preparation performed external lookup')
    for name in ('read_text', 'read_bytes', 'resolve'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(agent_run.config, 'load_agents', forbidden)
    monkeypatch.setattr(agent_run, '_capture_execution', forbidden)
    with pytest.raises(agent_run.DispatchError):
        agent_run.prepare_turn(changed, accept_human_submission('Accepted short input é'))
    assert not prompt.exists() and list((ws / 'logs').iterdir()) == []


def test_k_operands_legacy_and_target_only_do_not_gain_full_send_rules(k_conversational_project):
    from scieflow.core import service

    project, ws, prompt = k_conversational_project
    registry = agent_run.config.load_agents(project.root)
    cfg = registry['k-protocol-fixture']
    cfg['session_cmd'] = cfg['session_cmd'].removesuffix(' {prompt}')
    cfg['resume_cmd'] = cfg['resume_cmd'].replace(' --resume {session}', '')
    assert service._validate_conversation_target('k-protocol-fixture', registry) is None
    (project.root / 'config/agents.yml').write_text(yaml.safe_dump({'agents': registry}))
    prompt.write_text('legacy input')
    dispatch = agent_run.prepare(project, 'k-protocol-fixture', prompt, conversational=True)
    assert dispatch.argv[-1] == 'first' and dispatch.stdin_text is None


def test_k_operands_public_build_argv_literal_values():
    literal = '{reasoning} {root} {python} {session} {prompt} "é"'
    cfg = {'cmd': 'tool {model} {reasoning} {session} {prompt}', 'model': literal, 'reasoning': literal}
    assert agent_run.build_argv(cfg, literal, ROOT, session=literal) == ['tool', literal, literal, literal, literal]
