"""M-G2 t3 (plan 5.7, 5.8, 6 t3): the paper-lifecycle docs and skills match the code.

Phase lists are read from status.phases_for and option choices from the click commands, so a
renamed phase or flag fails here instead of drifting. The paper-draft and journal-revision skills
must never tell an agent to write status.yml by hand or to waive a finding.
"""

import re
from pathlib import Path

import click

from scieflow.cli import main
from scieflow.core.run import status

ROOT = Path(__file__).resolve().parents[1]
RUNS_DOC = ROOT / "docs" / "runs.md"
CLI_DOC = ROOT / "docs" / "cli.md"
CLAIMS_DOC = ROOT / "docs" / "research" / "claims.md"
REVISION_DOC = ROOT / "docs" / "research" / "revision.md"
PAPER_DRAFT_DOC = ROOT / "docs" / "research" / "workflows" / "paper-draft.md"
SKILLS = ROOT / "src" / "scieflow" / "research" / "skills"
PAPER_DRAFT_SKILL = SKILLS / "paper-draft" / "SKILL.md"
JOURNAL_REVISION_SKILL = SKILLS / "journal-revision" / "SKILL.md"
RESEARCH_AGENTS = ROOT / "src" / "scieflow" / "research" / "AGENTS.md"
AGENTS_RULE_5 = (
    "5. Track progress in `workspace/<slug>/status.yml` and append notable events "
    "(dispatches, failures, retries, timings) to `workspace/<slug>/log.md`. "
    "A `paper-draft` or `paper-revision` run has native status: create it with "
    "`uv run scieflow run init` or `run adopt`, change its phases only with `uv run "
    "scieflow run mark <slug> <phase> <state> --as-agent`, and never edit its "
    "`status.yml` by hand."
)

PAPER_REVISION_HEADING = "### Paper-revision runs and phase gates"
PL_D6_SENTENCE = (
    "Native `mark` and `checkpoint` know only research-loop, paper-draft and paper-revision "
    "statuses;"
)


def _read(path):
    return path.read_text(encoding="utf-8")


def _runs_section(text, heading, stop=r"\n#{1,3} "):
    start = text.index(heading)
    rest = text[start + 1 :]
    match = re.search(stop, rest)
    return text[start : start + 1 + match.start()] if match else text[start:]


def _run_section(text):
    start = text.index("## `scieflow run`")
    rest = text[start + 1 :]
    end = rest.find("\n## ")
    return text[start : start + 1 + end] if end != -1 else text[start:]


def _synopsis_block(section, name):
    """The fenced synopsis that starts with `uv run scieflow run NAME `, not any prose mention."""
    blocks = re.findall(r"```bash\n(.*?)```", section, re.S)
    matches = [b for b in blocks if b.startswith(f"uv run scieflow run {name} ")]
    assert len(matches) == 1, f"run {name} has no single synopsis block in docs/cli.md"
    return matches[0]


def _run_command(name):
    ctx = click.Context(main)
    return main.get_command(ctx, "run").get_command(ctx, name)


def _non_negated_status_lines(text):
    """Lines that mention status.yml without a 'never' (the do-not-edit rule is allowed)."""
    return [
        line
        for line in text.splitlines()
        if "status.yml" in line and "never" not in line.lower()
    ]


# --- docs/runs.md: the paper-revision subsection and the PL-D6 sentence ---


def test_runs_doc_has_paper_revision_section_after_the_paper_draft_one():
    text = _read(RUNS_DOC)
    assert PAPER_REVISION_HEADING in text
    assert text.index("### Native paper-draft lifecycle") < text.index(PAPER_REVISION_HEADING)


def test_runs_doc_paper_revision_phase_list_equals_the_code():
    section = _runs_section(_read(RUNS_DOC), PAPER_REVISION_HEADING)
    after = section.split("The paper-revision phases are ", 1)
    assert len(after) == 2, "the paper-revision phase sentence is missing"
    segment = after[1].split("initially", 1)[0]
    documented = re.findall(r"`([^`]+)`", segment)
    assert documented == list(status.phases_for("paper-revision"))


def test_runs_doc_paper_revision_init_and_adopt_lines_are_documented():
    text = _read(RUNS_DOC)
    assert (
        "uv run scieflow run init SLUG --workflow paper-revision --goal FILE --approval per-campaign"
        in text
    )
    assert (
        "uv run scieflow run adopt SLUG --workflow paper-revision --approval per-campaign --as-agent"
        in text
    )


def test_runs_doc_names_both_gates_their_refusal_stems_and_the_event():
    section = _runs_section(_read(RUNS_DOC), PAPER_REVISION_HEADING)
    for phrase in (
        "verify refused:",
        "respond refused:",
        "fail closed",
        "no-manuscript",
        "note.phase_gate",
        "run mark",
        "verify done",
        "respond done",
    ):
        assert phrase in section, phrase


def test_runs_doc_says_the_browser_cannot_create_a_paper_revision_run():
    section = _runs_section(_read(RUNS_DOC), PAPER_REVISION_HEADING)
    assert "browser" in section
    assert "cannot create" in section or "does not create" in section


def test_runs_doc_pl_d6_sentence_names_paper_revision():
    assert PL_D6_SENTENCE in _read(RUNS_DOC)


def test_runs_doc_gate_refusals_do_not_overclaim_the_review_status():
    section = " ".join(_runs_section(_read(RUNS_DOC), PAPER_REVISION_HEADING).split())
    assert "a missing or unreadable review status" not in section
    assert "an unreadable review status" in section
    assert "cannot discover" in section


# --- research/AGENTS.md: the protected rule-5 edit of plan 3.3, pinned here ---


def test_research_agents_rule_5_pins_the_native_paper_status_sentence():
    text = " ".join(_read(RESEARCH_AGENTS).split())
    assert AGENTS_RULE_5 in text


# --- docs/cli.md: the run section lists every --workflow choice; mark names the gates ---


def test_cli_run_init_and_adopt_list_every_workflow_choice():
    section = _run_section(_read(CLI_DOC))
    for name in ("init", "adopt"):
        command = _run_command(name)
        assert command is not None, f"run {name} is not a command"
        workflow = next(p for p in command.params if p.name == "workflow")
        block = _synopsis_block(section, name)
        for choice in workflow.type.choices:
            assert choice in block, f"run {name} --workflow {choice} missing from its synopsis"


def test_cli_run_init_and_adopt_prose_names_paper_revision():
    paragraphs = _run_section(_read(CLI_DOC)).split("\n\n")
    init_prose = [p for p in paragraphs if "loads no defaults" in p]
    adopt_prose = [p for p in paragraphs if "Give a prepared paper folder" in p]
    assert len(init_prose) == 1 and len(adopt_prose) == 1
    assert "paper-revision" in init_prose[0], "run init prose must name paper-revision"
    assert "paper-revision" in adopt_prose[0], "run adopt prose must name paper-revision"


def test_cli_run_mark_entry_names_the_gates_and_links_the_runs_section():
    section = _run_section(_read(CLI_DOC))
    marker = "uv run scieflow run mark SLUG PHASE STATE [--as-agent]"
    assert marker in section
    entry = section.split(marker, 1)[1].split("```bash", 1)[0]
    assert "verify done" in entry
    assert "respond done" in entry
    assert "claim check" in entry or "claims check" in entry
    assert "runs.md#paper-revision-runs-and-phase-gates" in entry


# --- docs/research/claims.md and revision.md: no 'not wired' statement remains ---


def test_claims_doc_no_longer_says_the_gate_is_not_wired():
    text = _read(CLAIMS_DOC)
    assert "not wired" not in text
    assert "run mark" in text and "verify done" in text
    assert "fail closed" in text
    assert "paper-revision" in text


def test_claims_doc_residuals_name_the_gate_limits():
    text = _read(CLAIMS_DOC)
    assert "no-manuscript" in text, "R1 paper drafts without a manuscript folder"
    assert "check-then-write" in text or "window" in text, "R2 check-then-write window"
    assert "hand-edited" in text or "by hand" in text, "R3 hand-edited status"


def test_revision_doc_has_paper_revision_section_with_the_phase_table():
    text = _read(REVISION_DOC)
    assert "## The paper-revision workflow" in text
    section = _runs_section(text, "## The paper-revision workflow", stop=r"\n## ")
    for phase in status.phases_for("paper-revision"):
        assert f"`{phase}`" in section, phase
    assert "run mark" in section
    assert "in place" in section, "revising in place without phase tracking stays available"


def test_revision_doc_no_longer_says_the_gate_is_not_wired():
    assert "not wired" not in _read(REVISION_DOC)


# --- docs/research/workflows/paper-draft.md: phase 1 and phase 6 ---


def test_paper_draft_workflow_doc_creates_the_run_natively():
    text = _read(PAPER_DRAFT_DOC)
    assert "run init --workflow paper-draft" in text
    assert "run adopt" in text


def test_paper_draft_workflow_doc_verify_is_refused_while_the_claim_check_has_errors():
    text = _read(PAPER_DRAFT_DOC)
    assert "run mark <slug> verify done" in text
    assert "refused" in text


# --- skills: native lifecycle, no hand-edited status, no waiver, the paper-revision section ---


def test_paper_draft_skill_uses_the_native_lifecycle():
    text = _read(PAPER_DRAFT_SKILL)
    for phrase in (
        "uv run scieflow run init <slug> --workflow paper-draft",
        "uv run scieflow run adopt <slug> --workflow paper-draft",
        "uv run scieflow run mark <slug> verify done --as-agent",
        "uv run scieflow run log <slug> note.draft_author_failed",
        "uv run scieflow run show <slug>",
        "never edit `status.yml`",
    ):
        assert phrase in text, phrase


def test_paper_draft_skill_never_asks_for_a_hand_written_status():
    lines = _non_negated_status_lines(_read(PAPER_DRAFT_SKILL))
    bad = [line for line in lines if "in `status.yml`" in line or "Update `status.yml`" in line]
    assert bad == [], bad
    assert "`status.yml`: workflow" not in _read(PAPER_DRAFT_SKILL)


def test_paper_draft_skill_verify_names_the_refusal_and_no_waiver():
    text = _read(PAPER_DRAFT_SKILL)
    assert "refuses while the claim check has errors" in text
    assert "never waive" in text


def test_journal_revision_skill_has_the_paper_revision_section_before_the_report():
    text = _read(JOURNAL_REVISION_SKILL)
    assert "## Paper-revision workflow" in text
    assert text.index("## Paper-revision workflow") < text.index("## Report to the user")
    section = _runs_section(text, "## Paper-revision workflow", stop=r"\n## ")
    for phase in status.phases_for("paper-revision"):
        assert f"`{phase}`" in section, phase
    for phrase in (
        "uv run scieflow run init <slug> --workflow paper-revision",
        "uv run scieflow run adopt <slug> --workflow paper-revision",
        "uv run scieflow run mark <slug> respond done --as-agent",
        "uv run scieflow run mark <slug> verify done --as-agent",
        "The user stores the letter with `scieflow manuscript revision ingest`",
        "never its `experiments/` or `inputs/`",
        "scieflow manuscript revision ingest",
        "scieflow manuscript revision adopt",
        "scieflow manuscript revision coverage",
        "scieflow manuscript revision letter",
        "scieflow manuscript package",
        "resubmit",
        "never edit `status.yml`",
    ):
        assert phrase in section, phrase


def test_journal_revision_skill_never_asks_for_a_hand_written_status():
    lines = _non_negated_status_lines(_read(JOURNAL_REVISION_SKILL))
    bad = [line for line in lines if "in `status.yml`" in line or "Update `status.yml`" in line]
    assert bad == [], bad


def test_skills_never_tell_an_agent_to_edit_claims_or_waive():
    for path in (PAPER_DRAFT_SKILL, JOURNAL_REVISION_SKILL):
        text = _read(path)
        for line in text.splitlines():
            lowered = line.lower()
            if "waive" in lowered and "never" not in lowered and "user" not in lowered:
                raise AssertionError(f"{path.name} tells the agent to waive: {line!r}")
            if "report/claims/" in line and "never" not in lowered:
                raise AssertionError(f"{path.name} asks the agent to touch report/claims: {line!r}")
