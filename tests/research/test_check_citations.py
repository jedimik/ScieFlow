import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = ["-m", "scieflow.research.citations"]

BIB = """\
@article{smith2024,
  title = {A Paper},
  doi = {10.1234/abc},
}
@article{jones2023,
  title = {Another Paper},
  doi = {10.5678/def},
}
"""


def make_ws(tmp_path, tex, bib=BIB, dois="10.1234/abc\n10.5678/def\n"):
    ws = tmp_path / "ws"
    (ws / "manuscript" / "sections").mkdir(parents=True)
    (ws / "report").mkdir()
    (ws / "manuscript" / "sections" / "body.tex").write_text(tex)
    (ws / "manuscript" / "references.bib").write_text(bib)
    if dois is not None:
        (ws / "report" / "selected_dois.txt").write_text(dois)
    return ws


def run(ws):
    return subprocess.run(
        [sys.executable, *SCRIPT, "--workspace", str(ws)],
        capture_output=True, text=True,
    )


def test_clean_manuscript_passes(tmp_path):
    ws = make_ws(tmp_path, r"Cites \cite{smith2024} and \citep{jones2023}.")
    proc = run(ws)
    assert proc.returncode == 0, proc.stdout
    assert "OK" in proc.stdout


def test_missing_bibkey_fails(tmp_path):
    ws = make_ws(tmp_path, r"\cite{smith2024,ghost2020} \cite{jones2023}")
    proc = run(ws)
    assert proc.returncode == 1
    assert "ghost2020" in proc.stdout


def test_orphan_bib_entry_fails(tmp_path):
    ws = make_ws(tmp_path, r"\cite{smith2024}")
    proc = run(ws)
    assert proc.returncode == 1
    assert "jones2023" in proc.stdout


def test_unlisted_doi_fails(tmp_path):
    ws = make_ws(
        tmp_path, r"\cite{smith2024} \cite{jones2023}", dois="10.1234/abc\n"
    )
    proc = run(ws)
    assert proc.returncode == 1
    assert "10.5678/def" in proc.stdout


def test_missing_doi_list_warns_but_passes(tmp_path):
    ws = make_ws(tmp_path, r"\cite{smith2024} \cite{jones2023}", dois=None)
    proc = run(ws)
    assert proc.returncode == 0
    assert "WARN" in proc.stdout


def test_no_doi_comment_lines_ignored(tmp_path):
    ws = make_ws(
        tmp_path,
        r"\cite{smith2024} \cite{jones2023}",
        dois="10.1234/abc\n# no-doi\n10.5678/def\n",
    )
    proc = run(ws)
    assert proc.returncode == 0


INLINE_BIB = (
    "@article{smith2024, title={A Paper}, doi={10.1234/abc}, year={2024}}\n"
    "@article{jones2023, title={Another}, doi = {https://doi.org/10.5678/def}}\n"
)


def test_single_line_entry_doi_is_checked(tmp_path):
    """Regression: the old anchored '^\\s*doi' regex skipped these entries."""
    ws = make_ws(
        tmp_path,
        r"\cite{smith2024} \cite{jones2023}",
        bib=INLINE_BIB,
        dois="10.1234/abc\n",          # jones2023's DOI is deliberately absent
    )
    proc = run(ws)
    assert proc.returncode == 1, proc.stdout
    assert "10.5678/def" in proc.stdout


def test_doi_url_prefix_is_normalized_before_comparison(tmp_path):
    ws = make_ws(
        tmp_path,
        r"\cite{smith2024} \cite{jones2023}",
        bib=INLINE_BIB,
        dois="10.1234/abc\n10.5678/def\n",
    )
    proc = run(ws)
    assert proc.returncode == 0, proc.stdout
