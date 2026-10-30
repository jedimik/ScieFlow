"""Word count and structure (M-F 5.8). Every expected count is hand-counted on synthetic text."""

from scieflow.research.manuscript import tex


def test_citations_are_not_words():
    assert tex.count_words("Gait timing matters \\cite{smith2001} here.") == 4


def test_math_is_not_counted():
    assert tex.count_words("The value $x = y + 1$ is small, and $$a^2$$ too. \\[ b \\] end.") == 7


def test_hyphenated_words_apostrophes_and_numbers():
    assert tex.count_words("State-of-the-art gait's data-set 2026.") == 4


def test_href_keeps_the_link_text_only():
    assert tex.count_words("See \\href{https://example.test/a}{the guide} now.") == 4


def test_unknown_commands_keep_their_argument():
    assert tex.count_words("A \\emph{very \\textbf{important}} idea.") == 4


def test_escaped_percent_is_a_symbol_not_a_word_break_error():
    assert tex.count_words("Rate was 5\\% in total.") == 5


def test_figure_environment_is_excluded_and_its_caption_counted_apart():
    text = "Before.\n\\begin{figure}\n\\caption{Hidden words}\n\\end{figure}\nAfter."
    assert tex.count_words(text) == 2
    assert tex.caption_words(text) == 2


def test_section_word_counts_on_the_synthetic_article(synth):
    analysis = tex.analyse(tex.read_tree(synth.ms))
    by_title = {s.title: s for s in analysis.sections}
    assert by_title["Introduction"].words == 3
    assert by_title["Methods"].words == 3
    assert by_title["Results"].words == 5
    assert by_title["Discussion"].words == 4
    assert by_title["Results"].file == "main.tex"
    assert by_title["Methods"].file == "methods.tex"


def test_excluded_sections_are_not_main_text(synth):
    analysis = tex.analyse(tex.read_tree(synth.ms))
    assert by_title(analysis, "References").excluded is True
    assert by_title(analysis, "Funding").excluded is True
    assert analysis.main_words == 3 + 3 + 5 + 4


def test_abstract_title_and_captions_are_counted_separately(synth):
    analysis = tex.analyse(tex.read_tree(synth.ms))
    assert analysis.abstract_words == 20
    assert analysis.title_words == 5
    assert analysis.caption_words == 3 + 3 + 2
    assert analysis.figures == 2 and analysis.tables == 1
    assert analysis.display_items == 0


def test_citations_and_bibliography_years(synth):
    analysis = tex.analyse(tex.read_tree(synth.ms))
    assert analysis.cites == ["smith2001", "lee2010"]
    assert analysis.bib == {"smith2001": "2001", "lee2010": "2010"}
    assert analysis.bibstyle == "plainnat"
    assert analysis.documentclass == "article"


def test_identical_input_gives_identical_analysis(synth):
    first = tex.analyse(tex.read_tree(synth.ms))
    second = tex.analyse(tex.read_tree(synth.ms))
    assert first.to_dict() == second.to_dict()


def test_title_normalisation_for_section_matching():
    assert tex.normalise_title("Materials \\& Methods:") == "materials and methods"
    assert tex.normalise_title("\\textbf{Ethics}  statement") == "ethics statement"


def by_title(analysis, title):
    return next(s for s in analysis.sections if s.title == title)
