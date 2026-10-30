"""M-F final fix (review r1 finding 3): `manuscript build` prints the path where the PDF really is.

The build writes workspace/<run>/manuscript/build/<stem>.pdf. Synthetic run; the engine is replaced
by a stub result, so no TeX runs here (the real latexmk path is covered by test_build_latexmk.py).
"""

from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.manuscript import build as build_mod
from tests.research.manuscript.conftest import RUN


def test_build_prints_the_pdf_where_the_build_writes_it(synth, monkeypatch):
    def fake_build(root, run_dir, **kwargs):
        return build_mod.BuildResult(ok=True, engine="latexmk", pdf_sha256="0" * 64)

    monkeypatch.setattr(build_mod, "build_run", fake_build)
    result = CliRunner().invoke(main, ["manuscript", "build", RUN], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    expected = synth.run_dir / "manuscript" / "build" / "main.pdf"
    assert f"build: {expected}" in result.output
    assert f"build: {synth.run_dir / 'build' / 'main.pdf'}" not in result.output
