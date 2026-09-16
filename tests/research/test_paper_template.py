import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TPL = REPO / "src" / "scieflow" / "research" / "templates" / "paper"

SECTIONS = ["abstract", "introduction", "methods", "results", "discussion"]


def test_template_files_exist_with_placeholders():
    main = (TPL / "main.tex").read_text()
    for ph in ("%%TITLE%%", "%%AUTHORS%%", "%%DATE%%"):
        assert ph in main
    for s in SECTIONS:
        assert f"\\input{{sections/{s}}}" in main
    assert (TPL / "preamble.tex").exists()


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_template_compiles(tmp_path):
    ms = tmp_path / "manuscript"
    (ms / "sections").mkdir(parents=True)
    main = (TPL / "main.tex").read_text()
    main = (
        main.replace("%%TITLE%%", "Stub Title")
        .replace("%%AUTHORS%%", "Stub Author")
        .replace("%%DATE%%", "2026-07-08")
    )
    (ms / "main.tex").write_text(main)
    (ms / "preamble.tex").write_text((TPL / "preamble.tex").read_text())
    for s in SECTIONS:
        body = "Body text \\cite{stub}." if s == "results" else "Body text."
        (ms / "sections" / f"{s}.tex").write_text(body + "\n")
    (ms / "references.bib").write_text(
        "@article{stub,\n  author = {Stub, A.},\n  title = {Stub Paper},\n"
        "  journal = {Journal of Stubs},\n  year = {2024},\n}\n"
    )
    proc = subprocess.run(
        ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
        cwd=ms, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout[-2000:]
    assert (ms / "main.pdf").exists()
