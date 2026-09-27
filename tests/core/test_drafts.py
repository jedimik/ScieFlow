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
    "round:١",  # Arabic-Indic "1" — isdigit() but not isascii()
])
def test_a_source_that_is_not_a_draft_or_a_round_is_refused(ws, bad_source):
    """`source` arrives from a form and selects a directory to compile."""
    with pytest.raises(drafts.DraftError):
        drafts.source_dir(ws, bad_source)


def test_a_symlink_escape_is_excluded_from_sections_listing(ws, tmp_path):
    """The same escape `test_a_symlink_out_of_the_run_is_refused` catches at
    `read_section` must not reach the listing that feeds it either — a
    caller iterating `sections()` and calling `read_section` for each name
    must never be handed a name that call would then refuse."""
    secret = tmp_path / "secret.tex"
    secret.write_text("not yours")
    (ws / "manuscript" / "drafts" / "claude" / "sneaky.tex").symlink_to(secret)
    assert "sneaky" not in drafts.sections(ws, "claude")


def test_source_dir_refuses_a_symlinked_agent_directory_that_escapes(ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws / "manuscript" / "drafts" / "evil").symlink_to(outside)
    with pytest.raises(drafts.DraftError):
        drafts.source_dir(ws, "agent:evil")


def test_source_dir_refuses_a_symlink_that_resolves_to_the_drafts_root(ws):
    """Containment must refuse the root itself, not just anything below it —
    a symlink that resolves back to `manuscript/drafts/` is no agent's
    draft, the same way `web.files.resolve` refuses the run root unless
    told otherwise."""
    (ws / "manuscript" / "drafts" / "self").symlink_to(ws / "manuscript" / "drafts")
    with pytest.raises(drafts.DraftError):
        drafts.source_dir(ws, "agent:self")


def test_a_symlink_cycle_is_a_draft_error_not_a_runtime_error(ws):
    """`Path.resolve()` raises a bare `RuntimeError` on a symlink loop —
    `_inside` must catch that itself rather than let it reach a caller that
    only expects `DraftError`."""
    loop = ws / "manuscript" / "drafts" / "claude" / "loop.tex"
    loop.symlink_to(loop)
    with pytest.raises(drafts.DraftError):
        drafts.read_section(ws, "claude", "loop")


def test_a_name_containing_a_nul_byte_is_refused(ws):
    """`Path.resolve()` raises a bare `ValueError` ("embedded null byte") on
    a NUL in a path component — refused as a name before that can happen."""
    with pytest.raises(drafts.DraftError):
        drafts.read_section(ws, "claude", "a\x00b")


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\tb", "a\x1bb", "a\x7fb",
                                 "a\x01b", "\nresults"])
def test_a_name_containing_a_control_character_is_refused(bad):
    """No legitimate draft name carries one, and a newline in particular is a
    forged-framing channel: `agent`/`section` are rendered into the merge
    prompt's provenance heading on a line of their own, outside every
    passage wrapping, so a newline there splits one heading into several
    lines the merging agent reads top-to-bottom. `service.keep_passage`
    enforces this same shared rule on a kept passage's provenance, which is
    what closes that channel at the writer; the preview directory name and a
    compile job's label are built from the same validated value and are
    sanitised by it for free."""
    with pytest.raises(drafts.DraftError):
        drafts.check_name(bad)


@pytest.mark.parametrize("ok", ["results", "a b", "a:b", "sección", "r-1_2"])
def test_an_ordinary_name_is_still_accepted(ok):
    """The control-character rule must not have swept up the legal names the
    module deliberately allows — a space inside the name, a `:` (see
    `check_name`'s own docstring), non-ASCII letters."""
    drafts.check_name(ok)


def test_a_non_ascii_digit_round_directory_does_not_crash_rounds(ws):
    """`"²".isdigit()` is True but `int("²")` raises — the
    function whose job is to *ignore* a non-round directory must not crash
    on one instead."""
    (ws / "manuscript" / "curation" / "rounds" / "²").mkdir(parents=True)
    assert drafts.rounds(ws) == []


def test_a_non_ascii_digit_round_directory_does_not_duplicate_a_real_round(ws):
    (ws / "manuscript" / "curation" / "rounds" / "1").mkdir(parents=True)
    (ws / "manuscript" / "curation" / "rounds" / "١").mkdir(parents=True)
    assert drafts.rounds(ws) == [1]


def test_a_symlink_cycle_at_the_drafts_directory_itself_is_a_draft_error(tmp_path):
    """A cycle need not be in a caller-supplied name at all — `manuscript/
    drafts` itself can be a symlink into a loop, and `_inside`'s *first*
    `.resolve()` (on `root`, before any `part` is even considered) must
    catch that the same way its second one catches a cycle in a name."""
    ws = tmp_path / "workspace" / "r1"
    manuscript = ws / "manuscript"
    manuscript.mkdir(parents=True)
    (manuscript / "a").symlink_to(manuscript / "b")
    (manuscript / "b").symlink_to(manuscript / "a")
    (manuscript / "drafts").symlink_to(manuscript / "a")
    with pytest.raises(drafts.DraftError):
        drafts.sections(ws, "claude")


def test_a_symlink_cycle_at_the_rounds_directory_itself_is_a_draft_error(tmp_path):
    ws = tmp_path / "workspace" / "r1"
    curation_dir = ws / "manuscript" / "curation"
    curation_dir.mkdir(parents=True)
    (curation_dir / "a").symlink_to(curation_dir / "b")
    (curation_dir / "b").symlink_to(curation_dir / "a")
    (curation_dir / "rounds").symlink_to(curation_dir / "a")
    with pytest.raises(drafts.DraftError):
        drafts.round_sections(ws, 1)


def test_a_symlink_into_another_agents_directory_is_refused_and_sections_agrees(ws):
    """Provenance is the whole point of a kept passage — which agent wrote
    these words. A symlink inside `claude`'s directory that resolves into
    `codex`'s must be refused by `read_section` (never silently serve
    codex's text as claude's), and `sections()` must already have hidden
    it, so the two never disagree about what claude offers."""
    codex_dir = ws / "manuscript" / "drafts" / "codex"
    (codex_dir / "secret.tex").write_text("codex's secret content")
    (ws / "manuscript" / "drafts" / "claude" / "foo.tex").symlink_to(
        codex_dir / "secret.tex")

    assert "foo" not in drafts.sections(ws, "claude")
    with pytest.raises(drafts.DraftError):
        drafts.read_section(ws, "claude", "foo")


def test_a_symlink_into_another_rounds_directory_is_refused_and_listing_agrees(ws):
    round1 = ws / "manuscript" / "curation" / "rounds" / "1"
    round2 = ws / "manuscript" / "curation" / "rounds" / "2"
    round1.mkdir(parents=True)
    round2.mkdir(parents=True)
    (round2 / "secret.tex").write_text("round 2's secret content")
    (round1 / "foo.tex").symlink_to(round2 / "secret.tex")

    assert "foo" not in drafts.round_sections(ws, 1)
    with pytest.raises(drafts.DraftError):
        drafts.read_round_section(ws, 1, "foo")
