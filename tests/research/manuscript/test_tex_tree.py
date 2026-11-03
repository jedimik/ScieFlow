"""The manuscript reader (M-F 5.8): tree, includes, comments, limits. Synthetic data only."""

import os

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


def test_include_expansion_is_bounded_in_size(synth, monkeypatch):
    names = [f"l{i:02d}" for i in range(20)]
    for name, child in zip(names, names[1:]):
        (synth.ms / f"{name}.tex").write_text(f"\\input{{{child}}}\n\\input{{{child}}}\n", encoding="utf-8")
    (synth.ms / f"{names[-1]}.tex").write_text("Leaf text.\n", encoding="utf-8")
    synth.edit("main.tex", "\\input{methods}", f"\\input{{{names[0]}}}")
    # Count the work instead of timing it: a wall-clock bound flakes on a loaded host.
    # Expanding this 2-fold chain in full takes about 2**20 calls; the size limit must stop far sooner.
    calls = 0
    expand = tex._expand

    def counting_expand(*args, **kwargs):
        nonlocal calls
        calls += 1
        return expand(*args, **kwargs)

    monkeypatch.setattr(tex, "_expand", counting_expand)
    with pytest.raises(tex.TexError) as info:
        tex.read_tree(synth.ms)
    assert info.value.rule == "tex-limit"
    assert 0 < calls <= 2**20 // 8


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


# --- M-G t1: keep_comments (M-G-D16) -------------------------------------------------------------

def test_keep_comments_keeps_source_comments_with_the_same_line_mapping(synth):
    synth.edit("methods.tex", "Synthetic methods text \\cite{smith2001}.\n",
               "Synthetic methods text \\cite{smith2001}. % source: [data:tbl-one]\n")
    plain = tex.read_tree(synth.ms)
    kept = tex.read_tree(synth.ms, keep_comments=True)
    assert [(line.file, line.lineno) for line in kept.lines] == \
        [(line.file, line.lineno) for line in plain.lines]
    assert any("% source: [data:tbl-one]" in line.text for line in kept.lines)
    assert all("%" not in line.text for line in plain.lines)


def test_default_read_tree_output_is_the_comment_stripped_kept_tree(synth):
    synth.edit("methods.tex", "Synthetic methods text \\cite{smith2001}.\n",
               "Synthetic methods text \\cite{smith2001}. % hidden note\n")
    plain = tex.read_tree(synth.ms)
    kept = tex.read_tree(synth.ms, keep_comments=True)
    assert plain.flat_text() == tex.strip_comments(kept.flat_text())
    assert "hidden note" not in plain.flat_text()


def test_commented_input_is_not_expanded_when_comments_are_kept(synth):
    synth.edit("main.tex", "\\input{methods}", "% \\input{absent}\n\\input{methods}")
    kept = tex.read_tree(synth.ms, keep_comments=True)
    assert "methods.tex" in kept.files
    assert "absent.tex" not in kept.files


def test_default_read_tree_matches_a_hand_written_golden_tree(synth):
    # M-G-D16: the default output is unchanged. The expected lines are written out by hand here, not
    # derived from keep_comments output.
    synth.write_manuscript({
        "main.tex": "\\begin{document}\nStart text % a note\n\\input{methods} tail % cmt\n% \\input{absent}\n"
                    "\\end{document}\n",
        "methods.tex": "\\section{Methods}\nMethod text % note\n",
    })
    tree = tex.read_tree(synth.ms)
    assert list(tree.files) == ["main.tex", "methods.tex"]
    assert tree.files["main.tex"] == ("\\begin{document}\nStart text \n\\input{methods} tail \n\n"
                                      "\\end{document}\n")
    assert [(line.file, line.lineno, line.text) for line in tree.lines] == [
        ("main.tex", 1, "\\begin{document}"),
        ("main.tex", 2, "Start text "),
        ("main.tex", 3, ""),
        ("methods.tex", 1, "\\section{Methods}"),
        ("methods.tex", 2, "Method text "),
        ("methods.tex", 3, ""),
        ("main.tex", 3, " tail "),
        ("main.tex", 4, ""),
        ("main.tex", 5, "\\end{document}"),
        ("main.tex", 6, ""),
    ]
