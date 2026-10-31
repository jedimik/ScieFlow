"""Claim units (M-G 5.2): environments, abstract, sentences, sections, comments, ids. Synthetic only."""

import hashlib

from scieflow.research.manuscript import checklist, tex
from scieflow.research.manuscript.claims import units as units_mod
from tests.research.manuscript.claims.conftest import FIXTURE_UNITS


def _units(ms):
    return units_mod.extract_units(tex.read_tree(ms, keep_comments=True))


def test_unit_list_of_the_fixture(claims_ms):
    found = _units(claims_ms)
    got = [(u.kind, u.file, u.line, u.end_line, u.section) for u in found]
    assert got == FIXTURE_UNITS


def test_section_kinds_follow_the_checklist_and_front_and_abstract_are_marked(claims_ms):
    found = _units(claims_ms)
    assert found[0].section_kinds == ("abstract",)
    assert found[1].section_kinds == tuple(sorted(checklist.section_kinds("Introduction")))


def test_text_is_normalised_and_comment_free(claims_ms):
    texts = [u.text for u in _units(claims_ms)]
    assert texts[0] == "Synthetic abstract reports 42 invented walkers."
    assert texts[1] == r"Gait timing matters in 1200 invented walkers \cite{smith2001}."
    assert texts[2] == r"Three cameras recorded walkers \cite{lee2010}."
    assert texts[3] == "Mean stride time was 0.91 s in block one."
    assert texts[4] == r"Block two reached 1,234 steps as Figure~\ref{fig:one} shows."
    assert texts[5] == r"Accuracy stayed under 5\% in the 2019 cohort."
    assert texts[6] == "The fit was $p < 0.05$ in block three."
    assert texts[7] == (r"\begin{equation} y = 12.5 x \end{equation} Sigma was 1.5 in the inline run "
                        r"[run:denoise/sigma-1.5].")
    assert texts[8] == (r"\begin{figure} \includegraphics[width=0.5\textwidth]{fig1.pdf} "
                        r"\caption{Synthetic figure one, n = 7 walkers.} \end{figure}")
    assert texts[9] == (r"\begin{table} \caption{Synthetic table of metrics.} \begin{tabular}{lc} "
                        r"Stride & 0.91 \\ \end{tabular} \end{table}")
    assert texts[10] == "Discussion of synthetic results."


def test_comments_attach_to_the_units_whose_span_includes_their_line(claims_ms):
    found = _units(claims_ms)
    assert found[0].comments == ("% source: [data:tbl-metrics]",)
    assert found[3].comments == ("% source: [data:tbl-metrics]",)
    assert found[4].comments == ("% source: [run:denoise/sigma-1.5]",)
    assert found[5].comments == ("% source: [data:tbl-metrics]",)
    assert found[7].comments == ()
    assert found[9].comments == ("% source: [data:tbl-metrics]",)


def test_display_math_stays_in_its_sentence(claims_ms):
    sentence = _units(claims_ms)[7]
    assert r"\begin{equation}" in sentence.text
    assert sentence.line == 17 and sentence.end_line == 20


def test_ids_are_a_hash_prefix_and_a_repeat_gets_a_numbered_suffix(synth):
    body = ("\\begin{document}\n\\section{Results}\n"
            "Same sentence with 3 items here.\n\nSame sentence with 3 items here.\n\\end{document}\n")
    synth.write_manuscript({"main.tex": body})
    found = _units(synth.ms)
    digest = hashlib.sha256("main.tex\nSame sentence with 3 items here.".encode("utf-8")).hexdigest()
    assert [u.id for u in found] == ["c-" + digest[:12], "c-" + digest[:12] + "-2"]


def test_a_unit_keeps_its_full_text_and_the_store_limit_is_applied_by_the_ledger(synth):
    long_sentence = "Long words " * 300 + "with 7 items."
    synth.write_manuscript({"main.tex": "\\begin{document}\n" + long_sentence + "\n\\end{document}\n"})
    found = _units(synth.ms)
    assert len(found) == 1
    assert found[0].text.endswith("with 7 items.")
    assert len(found[0].text) > 2000
