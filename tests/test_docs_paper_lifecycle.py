"""PL t2 (plan 5.5 a-e): the paper-lifecycle docs match the code.

Option names are read from the click commands, and the paper phase list from
status.phases_for, so a renamed flag or phase fails here instead of drifting.
"""

import re
from pathlib import Path

import click

from scieflow.cli import main
from scieflow.core.run import status

ROOT = Path(__file__).resolve().parents[1]
CLI_DOC = ROOT / "docs" / "cli.md"
RUNS_DOC = ROOT / "docs" / "runs.md"
WEB_DOC = ROOT / "docs" / "web.md"

ADOPT_LINE = "uv run scieflow run adopt SLUG --workflow paper-draft --approval per-campaign --as-agent"


def _run_command(name):
    ctx = click.Context(main)
    run_group = main.get_command(ctx, "run")
    return run_group.get_command(ctx, name)


def _run_section(text):
    """The text of the `## scieflow run` section of docs/cli.md (up to the next h2)."""
    start = text.index("## `scieflow run`")
    rest = text[start + 1 :]
    end = rest.find("\n## ")
    return text[start : start + 1 + end] if end != -1 else text[start:]


def _runs_paper_section(text):
    start = text.index("### Native paper-draft lifecycle")
    rest = text[start + 1 :]
    match = re.search(r"\n#{1,3} ", rest)
    return text[start : start + 1 + match.start()] if match else text[start:]


def _command_block(section, name):
    """The synopsis block of `run NAME` plus its paragraph (up to the next code block)."""
    marker = f"uv run scieflow run {name} SLUG"
    if marker not in section:
        return None
    after = section.split(marker, 1)[1]
    synopsis, rest = after.split("\n```\n", 1)
    paragraph = rest.split("```", 1)[0]
    return marker + synopsis + paragraph


def test_a_run_init_and_adopt_options_are_documented_per_command():
    cli_run = _run_section(CLI_DOC.read_text(encoding="utf-8"))
    for name in ("init", "adopt"):
        command = _run_command(name)
        assert command is not None, f"run {name} is not a command"
        block = _command_block(cli_run, name)
        assert block is not None, f"docs/cli.md has no `run {name}` block in the run section"
        for param in command.params:
            if not isinstance(param, click.Option) or param.name == "help":
                continue
            for flag in (opt for opt in param.opts if opt.startswith("--")):
                assert flag in block, f"run {name} option {flag} missing from its docs/cli.md block"


def test_b_runs_doc_has_paper_section_with_the_adopt_line():
    text = RUNS_DOC.read_text(encoding="utf-8")
    assert "### Native paper-draft lifecycle" in text
    assert ADOPT_LINE in text


def test_c_runs_doc_paper_phase_list_equals_the_code():
    section = _runs_paper_section(RUNS_DOC.read_text(encoding="utf-8"))
    after = section.split("The paper phases are ", 1)
    assert len(after) == 2, "the paper phase sentence is missing"
    segment = after[1].split("initially", 1)[0]
    documented = re.findall(r"`([^`]+)`", segment)
    assert documented == list(status.phases_for("paper-draft"))


def test_d_web_doc_no_longer_claims_the_one_workflow_sentence():
    assert "the one workflow this form's own" not in WEB_DOC.read_text(encoding="utf-8")


def test_e_cli_doc_says_run_advance_is_research_loop_only():
    section = _run_section(CLI_DOC.read_text(encoding="utf-8"))
    anchor = "uv run scieflow run advance SLUG [--as-agent]"
    assert anchor in section
    after_fence = section.split(anchor, 1)[1].split("\n```\n", 1)[1]
    paragraph = after_fence.split("```bash", 1)[0]
    assert "research-loop" in paragraph
