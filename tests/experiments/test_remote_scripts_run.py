"""M-D3 t2: the generated scripts run under real bash, no scheduler (plan 5.3; section 6 t2 item 4)."""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from scieflow.experiments import remote_bundle as rb
from scieflow.experiments.campaign import Campaign, RunSpec
from scieflow.experiments.stage import Stage

STAGE_YAML = (
    "name: scale\ndescription: test\nscript: run.py\noutput_file: result.png\n"
    "environment:\n  pip: [numpy]\n"
)

# A pure-Python stage: no third-party imports, writes <output>/result.png.
OK_STAGE = """\
import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--input", required=True)
p.add_argument("--output", required=True)
p.add_argument("--gain", type=float, default=1.0)
a = p.parse_args()
Path(a.output, "result.png").write_bytes(Path(a.input).read_bytes() + f"gain={a.gain}\\n".encode())
"""

FAIL_STAGE = "import sys\nprint('partial')\nsys.exit(3)\n"


def make_stage(root: Path, script_text: str) -> Stage:
    d = root / "pipelines" / "demo" / "stages" / "scale"
    d.mkdir(parents=True)
    (d / "stage.yaml").write_text(STAGE_YAML)
    (d / "run.py").write_text(script_text)
    return Stage.load(d)


def make_campaign(root: Path) -> Campaign:
    inp = root / "data" / "noisy.png"
    inp.parent.mkdir(parents=True)
    inp.write_bytes(b"INPUT\n")
    return Campaign(name="gain-sweep", pipeline="demo", stage="scale", input=inp,
                    reference=None, metrics=["ssim"])


SPECS = [
    RunSpec(run_id="000_gain-0.5", params={"gain": 0.5}),
    RunSpec(run_id="001_gain-1.0", params={"gain": 1.0}),
]


def stage_remote(tmp_path: Path, script_text: str = OK_STAGE, specs=SPECS) -> Path:
    """Render a direct bundle, then copy it to the 'remote' folder R where the scripts run."""
    stage = make_stage(tmp_path / "src", script_text)
    campaign = make_campaign(tmp_path / "src")
    bundle = tmp_path / "B"
    remote = tmp_path / "remote" / "gain-sweep-20261102T000000Z"
    rb.build_bundle(campaign, stage, specs, "direct", bundle,
                    remote_root=str(remote), python=sys.executable)
    shutil.copytree(bundle, remote, copy_function=shutil.copy2)
    return remote


def run_bash(remote: Path, *args, task: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("SCIEFLOW_TASK", None)
    if task is not None:
        env["SCIEFLOW_TASK"] = task
    return subprocess.run(["bash", "job.sh", *args], cwd=remote, env=env,
                          capture_output=True, text=True, timeout=120)


def sha256sum_check(run: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sha256sum", "-c", "remote/SHA256SUMS"], cwd=run,
                          capture_output=True, text=True, timeout=60)


def test_single_task_runs_only_that_run(tmp_path):
    remote = stage_remote(tmp_path)
    proc = run_bash(remote, task="1")
    assert proc.returncode == 0, proc.stderr
    assert not (remote / "runs" / "000_gain-0.5").exists()
    run = remote / "runs" / "001_gain-1.0"
    for name in ("exit", "started", "finished", "environment.txt", "SHA256SUMS"):
        assert (run / "remote" / name).is_file(), name
    assert (run / "remote" / "exit").read_text() == "0\n"
    assert (run / "artifacts" / "result.png").read_bytes() == b"INPUT\ngain=1.0\n"
    assert sha256sum_check(run).returncode == 0


def test_environment_has_three_keys_and_no_host_name(tmp_path):
    remote = stage_remote(tmp_path)
    run_bash(remote, task="0")
    text = (remote / "runs" / "000_gain-0.5" / "remote" / "environment.txt").read_text()
    keys = [line.split("=", 1)[0] for line in text.splitlines()]
    assert keys == ["system", "machine", "runtime"]
    assert socket.gethostname() not in text


def test_array_job_runs_remaining_and_skips_completed(tmp_path):
    remote = stage_remote(tmp_path)
    assert run_bash(remote, task="1").returncode == 0
    done = remote / "runs" / "001_gain-1.0" / "remote" / "finished"
    before = (done.read_bytes(), done.stat().st_mtime_ns)
    proc = run_bash(remote)  # no SCIEFLOW_TASK: the array shape
    assert proc.returncode == 0, proc.stderr
    assert (remote / "runs" / "000_gain-0.5" / "remote" / "exit").read_text() == "0\n"
    assert (done.read_bytes(), done.stat().st_mtime_ns) == before
    assert sha256sum_check(remote / "runs" / "000_gain-0.5").returncode == 0


def test_bad_task_index_exits_98(tmp_path):
    remote = stage_remote(tmp_path)
    proc = run_bash(remote, task="x")
    assert proc.returncode == 98
    assert not (remote / "runs").exists()


def test_failing_stage_keeps_marker_and_complete_checksums(tmp_path):
    remote = stage_remote(tmp_path, script_text=FAIL_STAGE, specs=SPECS[:1])
    proc = run_bash(remote, task="0")
    assert proc.returncode == 3
    run = remote / "runs" / "000_gain-0.5"
    assert (run / "remote" / "exit").read_text() == "3\n"
    assert "partial" in (run / "logs" / "stdout.log").read_text()
    assert sha256sum_check(run).returncode == 0


def test_verify_transfer_accepts_real_output(tmp_path):
    remote = stage_remote(tmp_path)
    run_bash(remote)
    ok, reason, files = rb.verify_transfer(remote / "runs" / "000_gain-0.5")
    assert (ok, reason) == (True, None)
    assert files == 3  # artifacts/result.png, logs/stdout.log, logs/stderr.log
