"""Docs, skill and example config match the scheduler seam (M-E t3, plan 5.8)."""

import shutil
from pathlib import Path

from remote import policy, schedulers

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "remote.md"
SKILL = ROOT / "skills" / "remote-exec" / "SKILL.md"
EXAMPLE = ROOT / "config" / "remotes.example.yml"


def test_every_adapter_has_a_docs_row_with_its_maturity():
    text = DOC.read_text()
    for name in schedulers.names():
        rows = [line for line in text.splitlines() if line.startswith(f"| `{name}` |")]
        assert rows, f"docs/remote.md has no adapter table row for {name}"
        assert schedulers.get(name).maturity in rows[0]


def test_docs_state_the_honest_limits():
    text = DOC.read_text()
    for phrase in (
        "agent-writable",          # plan C5: the ledger and the DAG file
        "no cancel command",       # M-E-D2
        "TIMEOUT",                 # A4: Slurm TIMEOUT reads failed, exit from ExitCode
        "OUT_OF_MEMORY",           # A4
        "unknown",                 # failing sacct and unknown ledger state
        "allow_experimental_scheduler",
        "--after-dryrun",
        "dag",
        "dryrun",
    ):
        assert phrase in text, f"docs/remote.md must mention {phrase!r}"


def test_docs_say_dryrun_only_sets_the_variable_and_the_runner_must_honour_it():
    # Review r1 F1: `dryrun` only sets SCIEFLOW_DRYRUN=1 (zero GPUs, no
    # scratch). Whether the job is a dry run depends on the runner script.
    for name, text in (("docs/remote.md", DOC.read_text()),
                       ("skills/remote-exec/SKILL.md", SKILL.read_text())):
        assert "only sets `SCIEFLOW_DRYRUN=1`" in text, f"{name} must say dryrun only sets the variable"
        assert "SCIEFLOW_DRYRUN" in text, f"{name} must name SCIEFLOW_DRYRUN"
        assert "snakemake -n" in text, f"{name} must say the runner runs snakemake -n when it sees the variable"
        assert "submits the pipeline as a dry run" not in text, f"{name} overstates the dry run"
        assert "submits the runner in dry-run" not in text, f"{name} overstates the dry run"


def test_mkdocs_nav_lists_the_remote_page():
    text = (ROOT / "mkdocs.yml").read_text()
    assert "  - Remote execution: remote.md" in text.splitlines()


def test_skill_documents_the_dry_run_gate_and_the_opt_in():
    text = SKILL.read_text()
    for phrase in ("dryrun", "dag", "--after-dryrun", "allow_experimental_scheduler"):
        assert phrase in text, f"skill must mention {phrase!r}"
    assert "only `pbs` is accepted" not in text


def test_example_config_still_loads_when_copied(tmp_path):
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    shutil.copy(EXAMPLE, root / "config" / "remotes.yml")
    remote = policy.load_remote(root, "meta")
    assert remote.scheduler == "pbs"
    assert remote.allow_experimental_scheduler is False
