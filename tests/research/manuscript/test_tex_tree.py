"""The manuscript reader (M-F 5.8): tree, includes, comments, limits. Synthetic data only."""

import os
import time

import pytest

from scieflow.research.manuscript import tex


def test_inputs_resolve_with_suffix_added_and_are_flattened(synth):
    tree = tex.read_tree(synth.ms)
    assert "methods.tex" in tree.files
    assert "Synthetic methods text" in tree.flat_text()
    assert tree.flat_text().count("\\section{Methods}") == 1


def test_line_provenance_names_the_included_file(synth):
    tree = tex.read_tree(synth.ms)
    located = [line for line in tree.lines if "Synthetic methods text" in line.text]
    assert located and located[0].file == "methods.tex" and located[0].lineno == 2


def test_comments_are_removed_but_escaped_percent_is_kept():
    text = "Kept \\% visible % hidden words here\nNext line.\n"
    assert tex.strip_comments(text) == "Kept \\% visible \nNext line.\n"


def test_cycle_is_refused(synth):
    synth.edit("main.tex", "\\input{methods}", "\\input{methods}\n\\input{loop}")
    (synth.ms / "loop.tex").write_text("\\input{main}\n", encoding="utf-8")
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms)
    assert info.value.rule == "tex-cycle"


def test_missing_input_is_refused(synth):
    synth.edit("main.tex", "\\input{methods}", "\\input{absent}")
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms)
    assert info.value.rule == "tex-missing-input"


def test_path_escaping_the_folder_is_refused(synth, tmp_path):
    outside = tmp_path / "outside.tex"
    outside.write_text("Outside text.\n", encoding="utf-8")
    synth.edit("main.tex", "\\input{methods}", f"\\input{{{outside}}}")
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms)
    assert info.value.rule == "tex-path"


def test_symbolic_link_is_refused(synth, tmp_path):
    target = tmp_path / "linked.tex"
    target.write_text("Linked text.\n", encoding="utf-8")
    os.symlink(target, synth.ms / "linked.tex")
    synth.edit("main.tex", "\\input{methods}", "\\input{linked}")
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms)
    assert info.value.rule == "tex-path"


def test_more_than_200_files_is_refused(synth):
    names = [f"part{i:03d}" for i in range(200)]
    for name in names:
        (synth.ms / f"{name}.tex").write_text("Part text.\n", encoding="utf-8")
    synth.edit("main.tex", "\\input{methods}", "\n".join(f"\\input{{{n}}}" for n in names))
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms)
    assert info.value.rule == "tex-limit"


def test_source_tree_hash_is_stable_and_ignores_build_output(synth):
    first = tex.source_tree_hash(synth.ms)
    (synth.ms / "main.aux").write_text("by-product\n", encoding="utf-8")
    (synth.ms / "build").mkdir()
    (synth.ms / "build" / "main.pdf").write_bytes(b"%PDF")
    assert tex.source_tree_hash(synth.ms) == first
    (synth.ms / "methods.tex").write_text("Changed.\n", encoding="utf-8")
    assert tex.source_tree_hash(synth.ms) != first


# --- review r1 fixes (M-F t5 fix) ---------------------------------------------------------------

def test_title_before_begin_document_is_read(synth):
    baseline = tex.analyse(tex.read_tree(synth.ms))
    synth.edit("main.tex", "\\title{Gait timing in synthetic cohorts}\n", "")
    synth.edit("main.tex", "\\documentclass[11pt]{article}\n",
               "\\documentclass[11pt]{article}\n\\title{Gait timing in synthetic cohorts}\n")
    analysis = tex.analyse(tex.read_tree(synth.ms))
    assert analysis.title == "Gait timing in synthetic cohorts"
    assert analysis.title_words == 5
    assert analysis.main_words == baseline.main_words


def test_include_expansion_is_bounded_in_size(synth):
    names = [f"l{i:02d}" for i in range(20)]
    for name, child in zip(names, names[1:]):
        (synth.ms / f"{name}.tex").write_text(f"\\input{{{child}}}\n\\input{{{child}}}\n", encoding="utf-8")
    (synth.ms / f"{names[-1]}.tex").write_text("Leaf text.\n", encoding="utf-8")
    synth.edit("main.tex", "\\input{methods}", f"\\input{{{names[0]}}}")
    started = time.monotonic()
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms)
    assert info.value.rule == "tex-limit"
    assert time.monotonic() - started < 1.0


def test_file_inputted_twice_is_flattened_twice(synth):
    synth.edit("main.tex", "\\input{methods}", "\\input{methods}\n\\input{methods}")
    tree = tex.read_tree(synth.ms)
    assert tree.flat_text().count("Synthetic methods text") == 2


def test_over_long_input_name_fails_closed_with_a_short_message(synth):
    long_name = "a" * 300
    synth.edit("main.tex", "\\input{methods}", f"\\input{{{long_name}}}")
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms)
    assert info.value.rule == "tex-path"
    assert long_name not in str(info.value)
    assert len(str(info.value)) < 120


def test_missing_input_message_excerpts_the_name(synth):
    name = "b" * 200
    synth.edit("main.tex", "\\input{methods}", f"\\input{{{name}}}")
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms)
    assert info.value.rule == "tex-missing-input"
    assert "b" * 61 not in str(info.value)


def test_over_long_main_name_fails_closed(synth):
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms, main="c" * 300)
    assert info.value.rule == "tex-path"
