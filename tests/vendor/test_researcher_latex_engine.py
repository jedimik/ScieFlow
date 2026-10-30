"""Tests for the vendored latex_engine: engine detection order and command building.

Ported from researcher v1.0.0 (MIT), scripts/tests/test_latex_engine.py (13 tests, unchanged).
Adapted: the import uses scieflow._vendor.researcher_core.latex_engine instead of conftest's
load_script, and the fake_path fixture is re-created in this file. See VENDORED.md.

Offline: no engine is actually run.
"""

import pytest

from scieflow._vendor.researcher_core import latex_engine
@pytest.fixture
def fake_path(monkeypatch):
    """Control what appears to be installed."""
    installed = {}

    def which(name):
        return installed.get(name)

    monkeypatch.setattr(latex_engine.shutil, "which", which)
    monkeypatch.setattr(latex_engine, "_which", which)
    return installed


def test_prefers_tectonic_when_present(fake_path):
    fake_path.update({"tectonic": "/bin/tectonic", "latexmk": "/bin/latexmk",
                      "pdflatex": "/bin/pdflatex"})
    engine = latex_engine.find_engine(env={})
    assert engine.name == "tectonic"
    assert engine.kind == "tectonic"


def test_falls_back_to_latexmk(fake_path):
    """A TeX Live, MiKTeX, or MacTeX box has no tectonic but has latexmk."""
    fake_path.update({"latexmk": "/bin/latexmk", "pdflatex": "/bin/pdflatex"})
    engine = latex_engine.find_engine(env={})
    assert engine.name == "latexmk"
    assert engine.kind == "latexmk"


def test_falls_back_to_raw_pdflatex(fake_path):
    fake_path.update({"pdflatex": "/bin/pdflatex"})
    engine = latex_engine.find_engine(env={})
    assert engine.name == "pdflatex"
    assert engine.kind == "raw"


def test_no_engine_returns_none(fake_path):
    assert latex_engine.find_engine(env={}) is None
    hint = latex_engine.install_hint()
    for distro in ("tectonic", "TeX Live", "MiKTeX", "MacTeX"):
        assert distro in hint


def test_tectonic_env_var_still_honored(fake_path, tmp_path):
    binary = tmp_path / "tectonic"
    binary.write_text("", encoding="utf-8")
    fake_path.update({"latexmk": "/bin/latexmk"})
    engine = latex_engine.find_engine(env={"TECTONIC": str(binary)})
    assert engine.name == "tectonic"
    assert engine.executable == str(binary)


def test_latex_engine_env_var_selects(fake_path):
    fake_path.update({"tectonic": "/bin/tectonic", "latexmk": "/bin/latexmk"})
    engine = latex_engine.find_engine(env={"LATEX_ENGINE": "latexmk"})
    assert engine.name == "latexmk"


def test_explicit_request_wins_over_tectonic(fake_path):
    fake_path.update({"tectonic": "/bin/tectonic", "xelatex": "/bin/xelatex"})
    engine = latex_engine.find_engine("xelatex", env={})
    assert engine.name == "xelatex"
    assert engine.kind == "raw"


def test_xelatex_request_drives_latexmk_when_only_latexmk_present(fake_path):
    fake_path.update({"latexmk": "/bin/latexmk"})
    engine = latex_engine.find_engine("xelatex", env={})
    assert engine.name == "latexmk"
    assert engine.latexmk_engine == "xelatex"
    command = latex_engine.build_commands(engine, "main.tex")
    assert "-xelatex" in command


def test_unknown_engine_raises(fake_path):
    with pytest.raises(FileNotFoundError):
        latex_engine.find_engine("notarealengine", env={})


def test_explicit_binary_path(fake_path, tmp_path):
    binary = tmp_path / "lualatex"
    binary.write_text("", encoding="utf-8")
    engine = latex_engine.find_engine(str(binary), env={})
    assert engine.name == "lualatex"
    assert engine.executable == str(binary)


def test_command_shapes(fake_path):
    tectonic = latex_engine.Engine("tectonic", "/bin/tectonic")
    assert latex_engine.build_commands(tectonic, "main.tex") == [
        "/bin/tectonic", "main.tex", "--keep-logs", "--keep-intermediates",
    ]

    latexmk = latex_engine.Engine("latexmk", "/bin/latexmk")
    command = latex_engine.build_commands(latexmk, "main.tex")
    assert command[:2] == ["/bin/latexmk", "-pdf"]
    assert "-halt-on-error" in command and command[-1] == "main.tex"

    raw = latex_engine.Engine("pdflatex", "/bin/pdflatex")
    command = latex_engine.build_commands(raw, "main.tex")
    assert command == ["/bin/pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"]


def test_needs_bibliography_detection(tmp_path):
    aux = tmp_path / "main.aux"
    aux.write_text("\\relax\n\\citation{smith2020}\n\\bibdata{library}\n", encoding="utf-8")
    assert latex_engine._needs_bibliography(aux)

    aux.write_text("\\relax\n", encoding="utf-8")
    assert not latex_engine._needs_bibliography(aux)

    assert not latex_engine._needs_bibliography(tmp_path / "missing.aux")


def test_missing_file_reports_hint_not_crash(fake_path, tmp_path):
    ok, output, pdf = latex_engine.compile_document(tmp_path / "nothing.tex")
    assert not ok
    assert pdf is None
    assert "No LaTeX engine found" in output
