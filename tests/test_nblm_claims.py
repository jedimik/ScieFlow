from nblm import claims

BIB = """\
@article{smith2021,
  title = {A filtering study},
  author = {Smith, A.},
  doi = {10.1000/xyz123},
}

@inproceedings{jones2020, title={One liner}, author={Jones, B.}, doi={10.2000/abc456}, year={2020}}

@misc{nodoi2019,
  title = {No identifier here},
}
"""

MAIN = r"""
\documentclass{article}
\begin{document}
% a comment citing \cite{ghost} must be ignored
Gaussian filtering improved SSIM by 12\% over baseline \citep{smith2021}.
This sentence cites nobody at all.
\input{sections/results}
\end{document}
"""

RESULTS = r"""
Both methods agree, e.g. in the thalamus, and converge \citep{smith2021,jones2020}.
An unresolved reference appears here \cite{nodoi2019} and here \cite{ghost}.
"""


def write_manuscript(tmp_path):
    (tmp_path / "sections").mkdir()
    (tmp_path / "main.tex").write_text(MAIN)
    (tmp_path / "sections" / "results.tex").write_text(RESULTS)
    bib = tmp_path / "references.bib"
    bib.write_text(BIB)
    return tmp_path / "main.tex", bib


def test_parse_bib_finds_single_line_doi():
    dois = claims.parse_bib(BIB)
    assert dois["smith2021"] == "10.1000/xyz123"
    # The bug this module must not inherit: doi= on a one-line entry.
    assert dois["jones2020"] == "10.2000/abc456"
    assert "nodoi2019" not in dois


def test_parse_bib_strips_doi_url_prefix():
    dois = claims.parse_bib("@article{a, doi = {https://doi.org/10.1/x}}")
    assert dois["a"] == "10.1/x"


def test_strip_comments_preserves_line_count():
    text = "line one % trailing\nline two\n100\\% kept\n"
    stripped = claims.strip_comments(text)
    assert stripped.count("\n") == text.count("\n")
    assert "trailing" not in stripped
    assert "100\\% kept" in stripped


def test_tex_files_follows_input(tmp_path):
    main, _ = write_manuscript(tmp_path)
    found = [p.name for p in claims.tex_files(main)]
    assert found == ["main.tex", "results.tex"]


def test_split_sentences_keeps_abbreviations_together():
    text = "Both agree, e.g. in the thalamus, and converge. Next one starts here."
    parts = [t for _, t in claims.split_sentences(text)]
    assert len(parts) == 2
    assert "e.g. in the thalamus" in parts[0]


def test_extract_pairs_each_sentence_with_each_doi(tmp_path):
    main, bib = write_manuscript(tmp_path)
    out = claims.extract(main, bib)
    texts = [c["text"] for c in out["claims"]]
    dois = [c["doi"] for c in out["claims"]]

    assert len(out["claims"]) == 3
    assert texts[0].startswith("Gaussian filtering improved SSIM by 12%")
    assert "\\citep" not in texts[0]
    # one sentence, two cited sources -> two claims, same text
    assert texts[1] == texts[2]
    assert dois[1:] == ["10.1000/xyz123", "10.2000/abc456"]
    assert out["claims"][1]["cites"] == ["smith2021", "jones2020"]


def test_extract_records_file_and_line(tmp_path):
    main, bib = write_manuscript(tmp_path)
    out = claims.extract(main, bib)
    assert out["claims"][0]["file"] == "main.tex"
    assert out["claims"][0]["line"] == 5
    assert out["claims"][1]["file"] == "sections/results.tex"


def test_extract_reports_unresolved_keys_without_inventing(tmp_path):
    main, bib = write_manuscript(tmp_path)
    out = claims.extract(main, bib)
    reasons = {u["key"]: u["reason"] for u in out["unresolved"]}
    assert reasons == {
        "ghost": "key not in bib",
        "nodoi2019": "bib entry has no doi field",
    }
    assert all(c["doi"] for c in out["claims"])


def test_commented_citation_is_ignored(tmp_path):
    main, bib = write_manuscript(tmp_path)
    out = claims.extract(main, bib)
    assert not any("comment citing" in c["text"] for c in out["claims"])


def test_clean_text_unwraps_latex_formatting_macros():
    """Live finding: raw \\textbf{...} reached NotebookLM as literal noise."""
    raw = (r"\textbf{Interoperable input and output.} It drives "
           r"\texttt{probtrackx2} and \emph{tckgen} \citep{a}.")
    got = claims.clean_text(raw)
    assert got == ("Interoperable input and output. It drives probtrackx2 "
                   "and tckgen.")
    assert "\\" not in got


def test_clean_text_converts_latex_dashes():
    got = claims.clean_text(r"held fixed --- an aside --- and pages 3--4 \cite{a}.")
    assert got == "held fixed — an aside — and pages 3–4."


def test_clean_text_unescapes_latex_escapes():
    got = claims.clean_text(r"an\ accuracy framing at 12\% of R\&D \cite{a}.")
    assert got == "an accuracy framing at 12% of R&D."
