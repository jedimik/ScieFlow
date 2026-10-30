"""`scieflow manuscript build` (M-F 5.12, t7) with the TeX subprocesses mocked. Synthetic data only.

Every subprocess goes through build._run and every tool lookup through latex_engine._which, so the
fakes below replace the whole TeX installation. The one real compile is in test_build_latexmk.py.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events
from scieflow.research.manuscript import build, tex
from tests.research.manuscript.conftest import RUN

MAIN = r"""\documentclass{article}
\begin{document}
Synthetic \cite{smith2001}.
\bibliographystyle{plainnat}
\bibliography{references}
\end{document}
"""

KPSE_HITS = {
    "article.cls": "/usr/share/texlive/texmf-dist/tex/latex/base/article.cls",
    "plainnat.bst": "/usr/share/texlive/texmf-dist/bibtex/bst/natbib/plainnat.bst",
}

TEX_NAMES = {"latexmk", "pdflatex", "xelatex", "lualatex", "tectonic"}


def _run(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


class FakeTeX:
    """Stands in for the installed TeX tools. Records every call; writes outputs like a compile would."""

    def __init__(self, present, *, hits=None, fail=False, log_lines=0, aux_bib=False, bcf=False):
        self.present = set(present)
        self.hits = dict(KPSE_HITS if hits is None else hits)
        self.fail = fail
        self.log_lines = log_lines
        self.aux_bib = aux_bib
        self.bcf = bcf
        self.calls = []

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.present else None

    def run(self, argv, cwd, env, timeout):
        name = Path(argv[0]).name
        self.calls.append(SimpleNamespace(name=name, argv=list(argv), cwd=Path(cwd), env=dict(env),
                                          timeout=timeout))
        if name == "kpsewhich":
            hit = self.hits.get(argv[1], "")
            return SimpleNamespace(returncode=0, stdout=hit + ("\n" if hit else ""), stderr="")
        if name in ("bibtex", "biber"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        cwd = Path(cwd)
        if self.log_lines:
            body = "\n".join(f"log line {i:02d}" for i in range(self.log_lines)) + "\n"
            (cwd / "main.log").write_text(body, encoding="utf-8")
        if self.fail:
            return SimpleNamespace(returncode=1, stdout="", stderr="! Emergency stop.")
        (cwd / "main.pdf").write_bytes(b"%PDF-1.4 synthetic\n")
        (cwd / "main.log").write_text("synthetic log\n", encoding="utf-8")
        if self.aux_bib:
            (cwd / "main.aux").write_text("\\bibdata{references}\n\\citation{smith2001}\n", encoding="utf-8")
        if self.bcf:
            (cwd / "main.bcf").write_text("<bcf/>\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def tex_calls(self):
        return [c for c in self.calls if c.name in TEX_NAMES | {"bibtex", "biber"}]


@pytest.fixture
def tex_env(synth, monkeypatch):
    """A manuscript with main.tex and the references, plus a factory that installs a FakeTeX."""
    ms = synth.ms
    ms.mkdir(parents=True, exist_ok=True)
    (ms / "main.tex").write_text(MAIN, encoding="utf-8")
    (ms / "references.bib").write_text("@article{smith2001, title={t}, year={2001}}\n", encoding="utf-8")

    def install(**kwargs):
        fake = FakeTeX(**kwargs)
        monkeypatch.setattr(build._engine, "_which", fake.which)
        monkeypatch.setattr(build, "_run", fake.run)
        return fake

    return SimpleNamespace(synth=synth, ms=ms, install=install)


STANDARD = ("latexmk", "kpsewhich")


def test_latexmk_is_the_first_engine_found(tex_env):
    fake = tex_env.install(present=("latexmk", "pdflatex", "kpsewhich"))
    assert _run("manuscript", "build", RUN).exit_code == 0
    engine_calls = [c for c in fake.tex_calls() if c.name in TEX_NAMES]
    assert engine_calls and all(c.name == "latexmk" for c in engine_calls)


def test_tectonic_is_never_chosen_without_the_flag(tex_env):
    fake = tex_env.install(present=("tectonic", "kpsewhich"))
    result = _run("manuscript", "build", RUN)
    assert result.exit_code == 1, result.output
    assert "No LaTeX engine found" in result.output
    assert not [c for c in fake.calls if c.name == "tectonic"]


def test_explicit_tectonic_runs_untrusted(tex_env):
    fake = tex_env.install(present=("tectonic", "kpsewhich"))
    assert _run("manuscript", "build", RUN, "--engine", "tectonic").exit_code == 0
    call = next(c for c in fake.calls if c.name == "tectonic")
    assert call.argv[1] == "--untrusted"
    assert "-no-shell-escape" not in call.argv


@pytest.mark.parametrize("present", [("latexmk", "kpsewhich"), ("pdflatex", "kpsewhich")])
def test_latex_runs_with_shell_escape_off(tex_env, present):
    fake = tex_env.install(present=present)
    assert _run("manuscript", "build", RUN).exit_code == 0
    call = next(c for c in fake.tex_calls() if c.name in TEX_NAMES)
    assert call.argv[1] == "-no-shell-escape"


def test_latexmk_runs_with_norc_so_a_manuscript_rc_file_is_never_read(tex_env):
    fake = tex_env.install(present=STANDARD)
    assert _run("manuscript", "build", RUN).exit_code == 0
    call = next(c for c in fake.tex_calls() if c.name == "latexmk")
    assert call.argv[1] == "-no-shell-escape"
    assert "-norc" in call.argv


def test_raw_engines_never_get_norc(tex_env):
    fake = tex_env.install(present=("pdflatex", "kpsewhich"))
    assert _run("manuscript", "build", RUN).exit_code == 0
    call = next(c for c in fake.tex_calls() if c.name == "pdflatex")
    assert "-norc" not in call.argv


def test_the_engine_environment_variable_does_not_change_the_engine(tex_env, monkeypatch):
    monkeypatch.setenv("LATEX_ENGINE", "pdflatex")
    fake = tex_env.install(present=("latexmk", "pdflatex", "kpsewhich"))
    assert _run("manuscript", "build", RUN).exit_code == 0
    engine_calls = [c for c in fake.tex_calls() if c.name in TEX_NAMES]
    assert engine_calls and all(c.name == "latexmk" for c in engine_calls)


def test_the_environment_is_fixed_and_homes_stay_in_scratch(tex_env):
    fake = tex_env.install(present=STANDARD)
    assert _run("manuscript", "build", RUN).exit_code == 0
    call = next(c for c in fake.tex_calls() if c.name in TEX_NAMES)
    assert set(call.env) == {"PATH", "LANG", "HOME", "TEXMFVAR", "shell_escape", "openout_any", "openin_any"}
    assert call.env["shell_escape"] == "f"
    assert call.env["openout_any"] == "p"
    assert call.env["openin_any"] == "p"
    assert call.env["LANG"] == "C.UTF-8"
    scratch = tex_env.synth.run_dir / "scratch"
    assert Path(call.env["HOME"]).is_relative_to(scratch)
    assert Path(call.env["TEXMFVAR"]).is_relative_to(scratch)


def test_a_compile_never_runs_in_the_manuscript_folder(tex_env):
    fake = tex_env.install(present=STANDARD)
    assert _run("manuscript", "build", RUN).exit_code == 0
    call = next(c for c in fake.tex_calls() if c.name in TEX_NAMES)
    assert call.cwd != tex_env.ms
    assert call.cwd.is_relative_to(tex_env.synth.run_dir / "scratch")
    assert not (tex_env.ms / "main.aux").exists()
    assert not (tex_env.ms / "main.log").exists()


def test_the_timeout_reaches_every_call(tex_env):
    fake = tex_env.install(present=STANDARD)
    assert _run("manuscript", "build", RUN, "--timeout", "45").exit_code == 0
    assert {c.timeout for c in fake.tex_calls()} == {45}


def test_raw_pdflatex_runs_bibtex_then_two_passes(tex_env):
    fake = tex_env.install(present=("pdflatex", "bibtex", "kpsewhich"), aux_bib=True)
    assert _run("manuscript", "build", RUN).exit_code == 0
    assert [c.name for c in fake.tex_calls()] == ["pdflatex", "bibtex", "pdflatex", "pdflatex"]


def test_raw_pdflatex_runs_biber_when_a_bcf_exists(tex_env):
    fake = tex_env.install(present=("pdflatex", "biber", "bibtex", "kpsewhich"), bcf=True)
    assert _run("manuscript", "build", RUN).exit_code == 0
    names = [c.name for c in fake.tex_calls()]
    assert "biber" in names and "bibtex" not in names


def test_no_engine_is_a_refusal_with_the_install_hint(tex_env):
    fake = tex_env.install(present=("kpsewhich",))
    result = _run("manuscript", "build", RUN)
    assert result.exit_code == 1, result.output
    assert "No LaTeX engine found" in result.output
    assert not fake.tex_calls()


def test_tectonic_is_refused_in_a_replay_run(tex_env, monkeypatch):
    fake = tex_env.install(present=("tectonic", "kpsewhich"))
    monkeypatch.setattr(build._network, "replays", lambda ws: True)
    result = _run("manuscript", "build", RUN, "--engine", "tectonic")
    assert result.exit_code == 1, result.output
    assert not [c for c in fake.calls if c.name == "tectonic"]


def test_tectonic_is_refused_in_an_l2_run(tex_env, monkeypatch):
    fake = tex_env.install(present=("tectonic", "kpsewhich"))
    monkeypatch.setattr(build._levels, "run_level", lambda ws: "L2")
    result = _run("manuscript", "build", RUN, "--engine", "tectonic")
    assert result.exit_code == 1, result.output
    assert not [c for c in fake.calls if c.name == "tectonic"]


def test_a_missing_class_is_named(tex_env):
    tex_env.install(present=STANDARD, hits={"plainnat.bst": KPSE_HITS["plainnat.bst"]})
    result = _run("manuscript", "build", RUN)
    assert result.exit_code == 1, result.output
    assert "article.cls" in result.output


def test_a_missing_bibliography_style_is_named(tex_env):
    tex_env.install(present=STANDARD, hits={"article.cls": KPSE_HITS["article.cls"]})
    result = _run("manuscript", "build", RUN)
    assert result.exit_code == 1, result.output
    assert "plainnat.bst" in result.output


def test_a_class_inside_the_folder_needs_no_kpsewhich(tex_env):
    tex_env.install(present=STANDARD, hits={})
    (tex_env.ms / "article.cls").write_text("% a class of the author's own\n", encoding="utf-8")
    (tex_env.ms / "plainnat.bst").write_text("% style\n", encoding="utf-8")
    assert _run("manuscript", "build", RUN).exit_code == 0


def test_a_symlink_in_the_tree_is_refused(tex_env):
    fake = tex_env.install(present=STANDARD)
    (tex_env.ms / "link.tex").symlink_to(tex_env.ms / "main.tex")
    result = _run("manuscript", "build", RUN)
    assert result.exit_code == 1, result.output
    assert "symbolic link" in result.output
    assert not fake.tex_calls()


@pytest.mark.parametrize("link", ["scratch/manuscript-env", "scratch/manuscript-env/home"])
def test_a_scratch_env_folder_that_is_a_symlink_is_refused_and_writes_nothing(tex_env, tmp_path, link):
    fake = tex_env.install(present=STANDARD)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    run = tex_env.synth.run_dir
    if link.endswith("/home"):
        (run / "scratch" / "manuscript-env").mkdir(parents=True, exist_ok=True)
    else:
        (run / "scratch").mkdir(exist_ok=True)
    (run / link).symlink_to(outside, target_is_directory=True)
    result = _run("manuscript", "build", RUN)
    assert result.exit_code == 1, result.output
    assert "symbolic link" in result.output
    assert list(outside.iterdir()) == []
    assert not fake.tex_calls()


def test_a_refused_tectonic_build_writes_nothing_into_scratch(tex_env, monkeypatch):
    tex_env.install(present=("tectonic", "kpsewhich"))
    monkeypatch.setattr(build._network, "replays", lambda ws: True)
    result = _run("manuscript", "build", RUN, "--engine", "tectonic")
    assert result.exit_code == 1, result.output
    run = tex_env.synth.run_dir
    assert not (run / "scratch" / "manuscript-build").exists()
    assert not (run / "scratch" / "manuscript-env").exists()


def test_a_failed_compile_writes_ok_false_and_prints_the_log_tail(tex_env):
    tex_env.install(present=STANDARD, fail=True, log_lines=60)
    result = _run("manuscript", "build", RUN)
    assert result.exit_code == 1, result.output
    assert "log line 59" in result.output
    assert "log line 20" in result.output
    assert "log line 19" not in result.output
    document = json.loads((tex_env.ms / "build" / "build.json").read_text(encoding="utf-8"))
    assert document["ok"] is False
    assert document["pdf_sha256"] is None
    assert not (tex_env.ms / "build" / "main.pdf").exists()


def test_a_success_writes_build_json_and_the_outputs(tex_env):
    tex_env.install(present=STANDARD)
    result = _run("manuscript", "build", RUN, "--json")
    assert result.exit_code == 0, result.output
    pdf = tex_env.ms / "build" / "main.pdf"
    assert pdf.is_file() and (tex_env.ms / "build" / "main.log").is_file()
    document = json.loads((tex_env.ms / "build" / "build.json").read_text(encoding="utf-8"))
    assert set(document) >= {"version", "ok", "engine", "argv", "returncodes", "pdf_sha256",
                             "source_tree_sha256", "class", "bibtex_style"}
    assert document["ok"] is True
    assert document["engine"] == "latexmk"
    assert document["pdf_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert document["source_tree_sha256"] == tex.source_tree_hash(tex_env.ms)
    assert document["class"] == "article" and document["bibtex_style"] == "plainnat"
    assert json.loads(result.output)["ok"] is True


def test_by_products_do_not_change_the_source_tree_hash_but_a_section_does(tex_env):
    tex_env.install(present=STANDARD)
    (tex_env.ms / "sections").mkdir()
    (tex_env.ms / "sections" / "methods.tex").write_text("Synthetic methods.\n", encoding="utf-8")
    assert _run("manuscript", "build", RUN).exit_code == 0
    first = json.loads((tex_env.ms / "build" / "build.json").read_text(encoding="utf-8"))
    (tex_env.ms / "main.aux").write_text("by-product\n", encoding="utf-8")
    (tex_env.ms / "main.log").write_text("by-product\n", encoding="utf-8")
    assert _run("manuscript", "build", RUN).exit_code == 0
    second = json.loads((tex_env.ms / "build" / "build.json").read_text(encoding="utf-8"))
    assert second["source_tree_sha256"] == first["source_tree_sha256"]
    (tex_env.ms / "sections" / "methods.tex").write_text("Changed methods.\n", encoding="utf-8")
    assert _run("manuscript", "build", RUN).exit_code == 0
    third = json.loads((tex_env.ms / "build" / "build.json").read_text(encoding="utf-8"))
    assert third["source_tree_sha256"] != first["source_tree_sha256"]


def test_a_build_emits_one_note_without_the_text(tex_env):
    tex_env.install(present=STANDARD)
    assert _run("manuscript", "build", RUN).exit_code == 0
    notes = events.read(tex_env.synth.run_dir, types=("note.manuscript_build",))
    assert len(notes) == 1
    assert notes[0]["type"] == "note.manuscript_build"
    assert "pdf_sha256" in json.dumps(notes[0])
    assert "Synthetic" not in json.dumps(notes[0])


def test_a_class_name_that_leaves_the_folder_is_refused(tex_env):
    fake = tex_env.install(present=STANDARD)
    (tex_env.ms / "main.tex").write_text("\\documentclass{../evil}\n\\begin{document}x\\end{document}\n",
                                         encoding="utf-8")
    result = _run("manuscript", "build", RUN)
    assert result.exit_code == 1, result.output
    assert "asset-name" in result.output
    assert not [c for c in fake.calls if c.name == "kpsewhich"]


def test_an_engine_that_is_not_a_name_is_a_usage_error(tex_env):
    fake = tex_env.install(present=STANDARD)
    assert _run("manuscript", "build", RUN, "--engine", "/bin/sh").exit_code == 2
    assert not fake.calls


def test_a_timeout_outside_the_range_is_a_usage_error(tex_env):
    tex_env.install(present=STANDARD)
    assert _run("manuscript", "build", RUN, "--timeout", "10").exit_code == 2
