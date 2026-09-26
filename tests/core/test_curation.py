"""The curation document: kept passages, your own text, and their order.

A kept passage stores the TEXT with its provenance, never an offset into a
file the next round rewrites. These tests are written around that.
"""

import pytest

from scieflow.core import events
from scieflow.core.run import curation


@pytest.fixture
def ws(tmp_path):
    from scieflow.core.run import status

    workspace = tmp_path / "workspace" / "r1"
    workspace.mkdir(parents=True)
    status.write_status(workspace, status.new_status("r1", "autonomous"))
    return workspace


def test_a_run_without_curation_reads_as_empty(ws):
    doc = curation.read(ws)
    assert doc == {"round": 1, "note": "", "blocks": [], "version": 0}


def test_keeping_a_passage_records_where_it_came_from(ws):
    block = curation.keep(ws, "The catalyst degrades above 400 K.",
                          agent="claude", section="results")
    assert block["kind"] == "kept"
    assert block["agent"] == "claude" and block["section"] == "results"
    assert block["round"] == 1 and block["id"]
    assert curation.read(ws)["blocks"][0]["text"] == "The catalyst degrades above 400 K."
    assert "curation.changed" in [e["type"] for e in events.read(ws)]


def test_your_own_text_claims_no_provenance(ws):
    block = curation.add_own(ws, "We should say this plainly instead.")
    assert block["kind"] == "mine"
    assert not block.get("agent") and not block.get("section")


def test_blocks_keep_the_order_they_were_added(ws):
    curation.keep(ws, "first", agent="claude", section="intro")
    curation.add_own(ws, "second")
    curation.keep(ws, "third", agent="codex", section="methods")
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["first", "second", "third"]


def test_a_block_is_edited_by_id_not_by_index(ws):
    """Another tab reordering must not make an edit land on the wrong block."""
    first = curation.keep(ws, "first", agent="claude", section="intro")
    curation.add_own(ws, "second")
    curation.move_block(ws, first["id"], 1)           # first is now last
    curation.edit_block(ws, first["id"], "first, edited")
    texts = [b["text"] for b in curation.read(ws)["blocks"]]
    assert texts == ["second", "first, edited"]


def test_moving_a_block_reorders_without_losing_any(ws):
    ids = [curation.add_own(ws, t)["id"] for t in ("a", "b", "c")]
    curation.move_block(ws, ids[2], 0)
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["c", "a", "b"]


def test_removing_a_block_leaves_the_rest(ws):
    ids = [curation.add_own(ws, t)["id"] for t in ("a", "b", "c")]
    curation.remove_block(ws, ids[1])
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["a", "c"]


def test_an_unknown_block_id_is_refused_and_changes_nothing(ws):
    curation.add_own(ws, "only")
    for call in (lambda: curation.edit_block(ws, "nope", "x"),
                 lambda: curation.move_block(ws, "nope", 0),
                 lambda: curation.remove_block(ws, "nope")):
        with pytest.raises(curation.CurationError, match="block"):
            call()
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["only"]


def test_empty_text_is_refused_and_nothing_is_written(ws):
    with pytest.raises(curation.CurationError):
        curation.keep(ws, "   ", agent="claude", section="intro")
    assert curation.read(ws)["blocks"] == []


def test_an_invalid_actor_is_refused_before_anything_is_written(ws):
    """The same write-before-validate defect three earlier modules shipped."""
    with pytest.raises(curation.CurationError):
        curation.add_own(ws, "text", actor="wizard")
    assert curation.read(ws)["blocks"] == []
    assert events.read(ws) == []


def test_the_note_is_stored_separately_from_the_blocks(ws):
    curation.add_own(ws, "a passage")
    curation.set_note(ws, "Tighten the results section.")
    doc = curation.read(ws)
    assert doc["note"] == "Tighten the results section."
    assert [b["text"] for b in doc["blocks"]] == ["a passage"]


def test_advancing_a_round_keeps_the_blocks(ws):
    curation.keep(ws, "kept in round one", agent="claude", section="intro")
    assert curation.advance_round(ws) == 2
    doc = curation.read(ws)
    assert doc["round"] == 2
    assert doc["blocks"][0]["round"] == 1, "a block remembers the round it came from"
    assert "curation.round" in [e["type"] for e in events.read(ws)]


def test_every_change_is_a_version_and_any_version_restores(ws):
    """The spec requires the curation be restorable, like the charter: you
    trim it hard to fit a merge prompt, the round goes badly, and you want
    yesterday's selection back."""
    curation.keep(ws, "the good passage", agent="claude", section="results")
    curation.add_own(ws, "a second thought")
    trimmed = curation.read(ws)["version"]
    curation.remove_block(ws, curation.read(ws)["blocks"][0]["id"])
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["a second thought"]

    curation.revert(ws, trimmed)
    assert [b["text"] for b in curation.read(ws)["blocks"]] == [
        "the good passage", "a second thought"]
    assert curation.read(ws)["version"] > trimmed, "a revert is a new version, not a rewind"
    assert len(curation.history(ws)) == curation.read(ws)["version"]


def test_reverting_to_a_version_that_never_existed_is_refused(ws):
    curation.add_own(ws, "only")
    for bad in (0, 99, -1):
        with pytest.raises(curation.CurationError, match="version"):
            curation.revert(ws, bad)
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["only"]


def test_a_revert_restores_the_note_too(ws):
    curation.set_note(ws, "the original note")
    original = curation.read(ws)["version"]
    curation.set_note(ws, "replaced")
    curation.revert(ws, original)
    assert curation.read(ws)["note"] == "the original note"


def test_latex_in_a_passage_is_stored_byte_for_byte(ws):
    r"""Every real selection contains backslashes, braces and %. Braces
    matter most: `build_argv` substitutes `{prompt}`, so a `.format()`-shaped
    bug anywhere downstream would mangle a passage silently."""
    passage = r"\cite{smith2020} showed 95\% at $T={400}$ K % see note"
    curation.keep(ws, passage, agent="claude", section="results")
    assert curation.read(ws)["blocks"][0]["text"] == passage
    assert passage in curation.as_text(ws)


def test_as_text_shows_provenance_and_the_note(ws):
    curation.keep(ws, "from claude", agent="claude", section="results")
    curation.add_own(ws, "mine")
    curation.set_note(ws, "merge these")
    rendered = curation.as_text(ws)
    assert "claude" in rendered and "results" in rendered
    assert "from claude" in rendered and "mine" in rendered
    assert "merge these" in rendered


def test_concurrent_appends_do_not_lose_a_block(ws):
    import threading

    threads = [threading.Thread(target=curation.add_own, args=(ws, f"b{i}"))
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    blocks = curation.read(ws)["blocks"]
    assert len(blocks) == 8
    assert len({b["id"] for b in blocks}) == 8, "ids collided"
