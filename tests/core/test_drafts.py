"""Reading what `paper-draft` wrote.

`agent` and `section` arrive from a web form and are used to build a path,
so the refusals below are the load-bearing part of this module.
"""

import pytest

from scieflow.core import drafts


@pytest.fixture
def ws(tmp_path):
    workspace = tmp_path / "workspace" / "r1"
    for agent in ("claude", "codex"):
        d = workspace / "manuscript" / "drafts" / agent
        d.mkdir(parents=True)
        (d / "introduction.tex").write_text(f"\\section{{Intro}} by {agent}\n")
        (d / "results.tex").write_text(f"\\section{{Results}} by {agent}\n")
    return workspace


def test_agents_are_the_draft_subdirectories(ws):
    assert drafts.agents(ws) == ["claude", "codex"]


def test_sections_are_that_agents_tex_files(ws):
    assert drafts.sections(ws, "claude") == ["introduction", "results"]


def test_reading_a_section_returns_its_source(ws):
    assert "by claude" in drafts.read_section(ws, "claude", "introduction")


def test_a_run_with_no_drafts_reads_as_empty(tmp_path):
    empty = tmp_path / "workspace" / "r1"
    empty.mkdir(parents=True)
    assert drafts.agents(empty) == []


@pytest.mark.parametrize("bad_agent", ["../../..", "a/b", "", "   ", ".."])
def test_an_agent_name_that_escapes_the_drafts_directory_is_refused(ws, bad_agent):
    with pytest.raises(drafts.DraftError):
        drafts.sections(ws, bad_agent)


@pytest.mark.parametrize("bad_section", ["../../../etc/passwd", "a/b", "..", ""])
def test_a_section_name_that_escapes_is_refused(ws, bad_section):
    with pytest.raises(drafts.DraftError):
        drafts.read_section(ws, "claude", bad_section)


def test_a_symlink_out_of_the_run_is_refused(ws, tmp_path):
    """Containment is checked on the resolved path, so a symlink cannot be
    trusted just because its name looks local."""
    secret = tmp_path / "secret.tex"
    secret.write_text("not yours")
    (ws / "manuscript" / "drafts" / "claude" / "sneaky.tex").symlink_to(secret)
    with pytest.raises(drafts.DraftError):
        drafts.read_section(ws, "claude", "sneaky")


def test_a_missing_section_is_refused_readably(ws):
    with pytest.raises(drafts.DraftError, match="nosuch"):
        drafts.read_section(ws, "claude", "nosuch")


def test_completed_rounds_are_listed_in_order(ws):
    """A merge round's output is the next round's left-hand pane, so it is
    read exactly like an agent's draft."""
    for n in (1, 2, 10):
        d = ws / "manuscript" / "curation" / "rounds" / str(n)
        d.mkdir(parents=True)
        (d / "results.tex").write_text(f"round {n}")
    assert drafts.rounds(ws) == [1, 2, 10], "sorted numerically, not as strings"
    assert drafts.round_sections(ws, 2) == ["results"]
    assert drafts.read_round_section(ws, 2, "results") == "round 2"


def test_a_run_with_no_rounds_reads_as_empty(ws):
    assert drafts.rounds(ws) == []


def test_a_non_numeric_round_directory_is_ignored(ws):
    """`curation/` also holds `document.yml`; only numbered round dirs count."""
    (ws / "manuscript" / "curation" / "rounds" / "draft-notes").mkdir(parents=True)
    assert drafts.rounds(ws) == []


def test_source_dir_resolves_both_kinds(ws):
    (ws / "manuscript" / "curation" / "rounds" / "3").mkdir(parents=True)
    assert drafts.source_dir(ws, "agent:claude").name == "claude"
    assert drafts.source_dir(ws, "round:3").name == "3"


@pytest.mark.parametrize("bad_source", [
    "agent:../../etc", "round:../../etc", "round:abc", "round:-1", "round:",
    "agent:", "claude", "", "agent:claude:extra", "file:/etc/passwd",
])
def test_a_source_that_is_not_a_draft_or_a_round_is_refused(ws, bad_source):
    """`source` arrives from a form and selects a directory to compile."""
    with pytest.raises(drafts.DraftError):
        drafts.source_dir(ws, bad_source)
