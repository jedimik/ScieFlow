"""M-D3 t5 (plan 5.11, Part B t5): the remote-campaign docs, the CLI reference, the skills and the
example config match the code.

The rule 11 text is the operator-applied, human-approved protected edit (plan section 3); it is
pinned byte for byte here. The names checked are the literals the code emits or accepts.
"""

import shutil
from pathlib import Path

from click.testing import CliRunner

from remote import policy
from scieflow.experiments.cli import experiment

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "src" / "scieflow" / "experiments"
DOC = ROOT / "docs" / "experiments" / "remote-campaigns.md"
CLI_DOC = ROOT / "docs" / "experiments" / "cli.md"
CONCEPTS = ROOT / "docs" / "experiments" / "concepts.md"
REMOTE_DOC = ROOT / "docs" / "remote.md"
EXP_CYCLE = ROOT / "skills" / "experiment-cycle" / "SKILL.md"
REMOTE_SKILL = ROOT / "skills" / "remote-exec" / "SKILL.md"
RUNNER = EXP / "skills" / "experiment-runner" / "SKILL.md"
DESIGNER = EXP / "skills" / "experiment-designer" / "SKILL.md"
EXAMPLE = ROOT / "config" / "remotes.example.yml"
AGENTS = ROOT / "AGENTS.md"

RULE11 = """\
11. Remote execution (metacentrum) goes ONLY through
    `uv run scripts/remote/remote.py` per `skills/remote-exec/SKILL.md`
    or, for a `backend: remote` campaign, `scieflow experiment sweep` and
    `scieflow experiment watch`, which call the same policy gate
    (`docs/experiments/remote-campaigns.md`) — never raw `ssh`/`scp`.
    `config/remotes.yml` (user-owned,
"""
RULE11_OLD_TAIL = "per `skills/remote-exec/SKILL.md` —\n    never raw `ssh`/`scp`. `config/remotes.yml` (user-owned,"

CAMPAIGN_KEYS = ("backend", "remote", "name", "dir", "jobs", "per-run", "array", "resources",
                 "walltime", "cpus", "mem_gb", "gpus", "queue", "scratch_type", "scratch_gb",
                 "01:00:00")
FAILURE_REASONS = ("transfer-missing", "transfer-hash", "transfer-extra", "fetched-symlink",
                   "no-exit-marker", "exit-nonzero", "no-output", "job-cancelled", "submit-failed")
PREFLIGHT_RULES = ("no-run", "campaign-exists", "resources-over-limit", "remote-state-invalid")
REQUIRED_OPS = ("check", "mkdir", "rsync", "fetch", "submit", "status", "qsub", "qstat")
DOC_PHRASES = ("`watch`", "`--once`", "`--interval`", "`--max-minutes`", "`--json`",
               "exit `0`", "exit `4`", "exit `1`", "A remote array job of N tasks counts as N runs",
               "remote.json", "SHA256SUMS", "cannot be cancelled", "l2_remotes", "runtime-info",
               "NO_TICKET", "kinit", "per-run", "array", "M-D3-D")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body of a `## heading` section up to the next `## ` heading."""
    head = f"\n## {heading}\n"
    assert head in text, heading
    return text.split(head, 1)[1].split("\n## ", 1)[0]


def test_rule_11_is_the_approved_text_byte_for_byte_and_the_old_lines_are_gone():
    text = _text(AGENTS)
    assert text.count(RULE11) == 1
    assert RULE11_OLD_TAIL not in text


def test_the_remote_campaigns_page_exists_and_is_in_the_nav_after_the_analysis_page():
    assert DOC.is_file()
    lines = _text(ROOT / "mkdocs.yml").splitlines()
    analysis = lines.index("      - Analysis and Statistics: experiments/analysis.md")
    assert lines[analysis + 1] == "      - Remote campaigns: experiments/remote-campaigns.md"


def test_the_remote_campaigns_page_names_every_campaign_key_and_default():
    text = _text(DOC)
    for key in CAMPAIGN_KEYS:
        assert key in text, key


def test_the_remote_campaigns_page_states_the_budget_rule_and_the_commands_and_flags():
    text = _text(DOC)
    for phrase in DOC_PHRASES:
        assert phrase in text, phrase


def test_the_remote_campaigns_page_names_every_failure_reason_and_preflight_rule():
    text = _text(DOC)
    for reason in FAILURE_REASONS + PREFLIGHT_RULES:
        assert reason in text, reason


def test_the_remote_campaigns_page_names_the_required_ops_and_states_residuals_r1_to_r8():
    text = _text(DOC)
    for op in REQUIRED_OPS:
        assert op in text, op
    assert "## Residuals and limits" in text
    for n in range(1, 9):
        assert f"R{n} " in text, n


def test_the_cli_reference_has_a_watch_section_and_the_sweep_names_backend_remote():
    text = _text(CLI_DOC)
    watch = _section(text, "scieflow experiment watch")
    for flag in ("--once", "--interval", "--max-minutes", "--json"):
        assert flag in watch, flag
    assert "backend: remote" in _section(text, "scieflow experiment sweep")


def test_every_flag_documented_for_watch_exists_in_its_help():
    text = _text(CLI_DOC)
    flags = ("--once", "--interval", "--max-minutes", "--json")
    result = CliRunner().invoke(experiment, ["watch", "--help"])
    assert result.exit_code == 0, result.output
    for flag in flags:
        assert flag in result.output, flag
        assert flag in _section(text, "scieflow experiment watch"), flag


def test_the_concepts_page_shows_remote_json_and_the_run_remote_folder():
    text = _text(CONCEPTS)
    assert "remote.json" in text
    assert "remote/" in text


def test_the_remote_page_names_the_sweep_and_links_the_campaign_page():
    text = _text(REMOTE_DOC)
    assert "scieflow experiment sweep" in text
    assert "experiments/remote-campaigns.md" in text
    assert "## Remote campaigns" in text


def test_experiment_cycle_drops_the_refusal_and_the_hand_driven_text_and_says_sweep_then_watch():
    text = _text(EXP_CYCLE)
    assert "is refused by `scieflow experiment`" not in text
    assert "drive the campaign's tasks yourself" not in text
    assert "scieflow experiment sweep" in text
    assert "scieflow experiment watch" in text
    assert any("records its own runs" in line for line in text.splitlines())


def test_remote_exec_has_a_campaigns_paragraph_for_backend_remote():
    text = _text(REMOTE_SKILL)
    assert "## Campaigns" in text
    campaigns = _section(text, "Campaigns")
    assert "`backend: remote`" in campaigns
    assert "scieflow experiment sweep" in campaigns
    assert "scieflow experiment watch" in campaigns
    assert "never by submitting its runs with `remote.py`" in campaigns


def test_experiment_runner_names_watch_and_no_ticket():
    text = _text(RUNNER)
    assert "scieflow experiment watch" in text
    assert "NO_TICKET" in text


def test_the_designer_schema_block_shows_backend_remote_and_resources():
    text = _text(DESIGNER)
    assert "backend:" in text
    assert "remote:" in text
    assert "resources:" in text


def test_the_example_config_names_mkdir_and_rsync_for_remote_campaigns_and_still_loads(tmp_path):
    comments = [line for line in _text(EXAMPLE).splitlines() if line.lstrip().startswith("#")]
    assert any("remote campaign" in line and "mkdir" in line and "rsync" in line
               for line in comments)
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    shutil.copy(EXAMPLE, root / "config" / "remotes.yml")
    remote = policy.load_remote(root, "meta")
    assert remote.scheduler == "pbs"
