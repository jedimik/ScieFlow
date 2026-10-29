"""M-D1 final fix: the `design` group help text is the plan 5.7 text (user-visible in `scieflow --help`)."""

from click.testing import CliRunner

from scieflow.cli import main


def test_design_group_help_is_the_plan_text():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Study design and analysis plan: check, lock, amend." in result.output
    assert "design.yml, power and lock to follow" not in result.output
