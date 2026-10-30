"""M-F t3 (plan 5.4, Text): saved author-guideline pages become deterministic plain text.

Synthetic pages only.
"""

from scieflow.research.journal import html

FIXTURE_PAGE = """<!DOCTYPE html>
<html><head><title>Guide for Authors &amp; Policies</title><style>p {color: red}</style>
<script>var x = "ignore me";</script><meta charset="utf-8"></head>
<body>
<h1>Submission</h1>
<p>Manuscripts must not exceed <b>3000</b> words.</p>
<p>Abstracts:&nbsp;max&nbsp;250 words.</p>
<ul><li>Title page</li><li>Abstract</li></ul>
<table><tr><th>Item</th><th>Limit</th></tr><tr><td>References</td><td>40</td></tr></table>
<noscript>Enable JavaScript</noscript>
<p>
</p>


<p>Last line.</p>
</body></html>
"""

EXPECTED = "\n".join([
    "Guide for Authors & Policies",
    "Submission",
    "Manuscripts must not exceed 3000 words.",
    "Abstracts: max 250 words.",
    "- Title page",
    "- Abstract",
    "Item | Limit",
    "References | 40",
    "Last line.",
])


def test_the_fixture_page_has_exactly_the_expected_text():
    assert html.to_text(FIXTURE_PAGE) == EXPECTED


def test_scripts_styles_and_hidden_blocks_never_reach_the_text():
    text = html.to_text(FIXTURE_PAGE)
    for hidden in ("ignore me", "color", "Enable JavaScript", "charset"):
        assert hidden not in text


def test_the_conversion_is_deterministic():
    assert html.to_text(FIXTURE_PAGE) == html.to_text(FIXTURE_PAGE)


def test_a_page_without_an_html_or_body_root_is_kept_as_plain_text():
    body = "Line one\n\n\n\nLine two <not a tag> & more\n"
    assert html.to_text(body) == "Line one\n\nLine two <not a tag> & more"


def test_repeated_blank_lines_are_dropped_in_plain_text():
    assert html.to_text("\n\nA\n\n\nB\n\n") == "A\n\nB"


def test_ansi_and_zero_width_characters_are_removed_from_the_text():
    text = html.to_text("<html><body><p>\x1b[31mred\x1b[0m\u200b word</p></body></html>")
    assert text == "red word"


def test_an_empty_body_gives_empty_text():
    assert html.to_text("<html><body></body></html>") == ""
