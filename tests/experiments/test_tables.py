"""M-D2 t5: tables render only from a fresh stats.json (plan 5.7). Synthetic data only."""

import importlib.util
import json
from pathlib import Path

from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.run import status
from scieflow.experiments import manifest
from scieflow.experiments.cli import experiment

_SUPPORT = Path(__file__).with_name("tables_support.py")
_spec = importlib.util.spec_from_file_location("tables_support", _SUPPORT)
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _tables(cdir):
    return CliRunner().invoke(experiment, ["tables", str(cdir)])


def _handwritten(cdir):
    """Fixed numbers whose rendered text is written out in the expected strings below."""
    doc = ts.fixed_doc()
    doc["configs"][0]["metrics"]["ssim"].update(
        n=6, mean=0.75, sd=0.0125, ci={"low": 0.7375, "high": 0.7625, "level": 0.95,
                                       "method": "BCa", "resamples": 9999, "seed": 0},
        ci_note=None)
    doc["configs"][1]["metrics"]["ssim"].update(
        n=6, mean=0.7, sd=0.02, ci=None, ci_note="degenerate")
    doc["configs"][2]["metrics"]["ssim"] = ts._empty_cell()
    doc["tests"] = [
        {"id": "ssim:000_sigma-0.5", "metric": "ssim", "kind": "pairwise",
         "test": "welch-t-test", "source": "campaign", "config_id": "000_sigma-0.5",
         "baseline": "001_sigma-1.0", "n": 6, "n_baseline": 6, "n_unpaired": 0,
         "computed": True, "reason": None, "statistic": 2.5, "df": 9.876, "p": 0.0312,
         "p_adjusted": 0.0624, "reject": False, "family": "campaign", "confirmatory": False,
         "effect": {"mean_difference": 0.02, "hedges_g": 0.8123, "cliffs_delta": 0.5,
                    "d_z": None, "better": True},
         "warnings": []},
        {"id": "ssim:002_sigma-2.0", "metric": "ssim", "kind": "pairwise",
         "test": "welch-t-test", "source": "campaign", "config_id": "002_sigma-2.0",
         "baseline": "001_sigma-1.0", "n": 0, "n_baseline": 6, "n_unpaired": 0,
         "computed": False, "reason": "n<2", "statistic": None, "df": None, "p": None,
         "p_adjusted": None, "reject": None, "family": "campaign", "confirmatory": False,
         "effect": None, "warnings": []},
    ]
    ts.install_doc(cdir, doc)


EXPECTED_SUMMARY_MD = (
    "| configuration | ssim |\n"
    "|---|---|\n"
    "| 000_sigma-0.5 | 0.7500 ± 0.0125 [0.7375, 0.7625] (6) |\n"
    "| 001_sigma-1.0 | 0.7000 ± 0.0200 [n/c (degenerate)] (6) |\n"
    "| 002_sigma-2.0 | — |\n"
)

EXPECTED_SUMMARY_TEX = (
    "% requires \\usepackage{booktabs}\n"
    "\\begin{tabular}{ll}\n"
    "\\toprule\n"
    "configuration & ssim \\\\\n"
    "\\midrule\n"
    "000\\_sigma-0.5 & 0.7500 $\\pm$ 0.0125 [0.7375, 0.7625] (6) \\\\\n"
    "001\\_sigma-1.0 & 0.7000 $\\pm$ 0.0200 [n/c (degenerate)] (6) \\\\\n"
    "002\\_sigma-2.0 & --- \\\\\n"
    "\\bottomrule\n"
    "\\end{tabular}\n"
)

EXPECTED_TESTS_MD = (
    "| metric | configuration | test | statistic | df | p | p_adjusted | reject | hedges_g "
    "| cliffs_delta | family |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|\n"
    "| ssim | 000_sigma-0.5 | welch-t-test | 2.500 | 9.88 | 0.031 | 0.062 | no | 0.81 | 0.50 "
    "| campaign |\n"
    "| ssim | 002_sigma-2.0 | welch-t-test | n/c (n<2) | n/c (n<2) | n/c (n<2) | n/c (n<2) "
    "| n/c (n<2) | n/c (n<2) | n/c (n<2) | campaign |\n"
)

EXPECTED_TESTS_TEX = (
    "% requires \\usepackage{booktabs}\n"
    "\\begin{tabular}{lllrrrrrrrl}\n"
    "\\toprule\n"
    "metric & configuration & test & statistic & df & p & p\\_adjusted & reject & hedges\\_g "
    "& cliffs\\_delta & family \\\\\n"
    "\\midrule\n"
    "ssim & 000\\_sigma-0.5 & welch-t-test & 2.500 & 9.88 & 0.031 & 0.062 & no & 0.81 & 0.50 "
    "& campaign \\\\\n"
    "ssim & 002\\_sigma-2.0 & welch-t-test & n/c (n\\textless{}2) & n/c (n\\textless{}2) "
    "& n/c (n\\textless{}2) & n/c (n\\textless{}2) & n/c (n\\textless{}2) "
    "& n/c (n\\textless{}2) & n/c (n\\textless{}2) & campaign \\\\\n"
    "\\bottomrule\n"
    "\\end{tabular}\n"
)


def test_summary_md_text_is_exact(tmp_path):
    cdir = ts.campaign(tmp_path)
    _handwritten(cdir)
    result = _tables(cdir)
    assert result.exit_code == 0, result.output
    assert (cdir / "tables" / "summary.md").read_text() == EXPECTED_SUMMARY_MD


def test_summary_tex_text_is_exact(tmp_path):
    cdir = ts.campaign(tmp_path)
    _handwritten(cdir)
    assert _tables(cdir).exit_code == 0
    assert (cdir / "tables" / "summary.tex").read_text() == EXPECTED_SUMMARY_TEX


def test_tests_md_and_tex_text_are_exact(tmp_path):
    cdir = ts.campaign(tmp_path)
    _handwritten(cdir)
    assert _tables(cdir).exit_code == 0
    assert (cdir / "tables" / "tests.md").read_text() == EXPECTED_TESTS_MD
    assert (cdir / "tables" / "tests.tex").read_text() == EXPECTED_TESTS_TEX


def test_p_values_below_0_001_are_written_as_a_bound(tmp_path):
    cdir = ts.campaign(tmp_path, analysis=None)
    ts.install(cdir)  # the fixed doc: 002 has p 0.0004 and p_adjusted 0.0008
    assert _tables(cdir).exit_code == 0
    text = (cdir / "tables" / "tests.md").read_text()
    assert "| < 0.001 | < 0.001 | yes |" in text


def test_tex_escape_covers_every_special_character():
    from scieflow.experiments import tables

    got = tables.tex_escape("a_b%&#$^~{}\\<>")
    assert got == (
        "a\\_b\\%\\&\\#\\$\\textasciicircum{}\\textasciitilde{}\\{\\}"
        "\\textbackslash{}\\textless{}\\textgreater{}")


def test_sidecars_hash_the_files_and_the_source_stats(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    assert _tables(cdir).exit_code == 0
    stats_sha = manifest.file_sha256(cdir / "stats.json")
    for name in ("summary", "tests"):
        sidecar = json.loads((cdir / "tables" / f"{name}.json").read_text())
        assert sidecar == {
            "id": name, "kind": "table", "campaign": "stat-camp",
            "files": {ext: manifest.file_sha256(cdir / "tables" / f"{name}.{ext}")
                      for ext in ("md", "tex")},
            "source": {"stats_sha256": stats_sha},
            "tool": "scieflow experiment tables", "version": 1,
        }


def test_rerun_is_byte_identical(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    _tables(cdir)
    first = {p.name: p.read_bytes() for p in (cdir / "tables").iterdir()}
    _tables(cdir)
    second = {p.name: p.read_bytes() for p in (cdir / "tables").iterdir()}
    assert first == second and first


def test_without_tests_only_the_summary_is_written(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir, lambda d: d.update(tests=[], families=[]))
    assert _tables(cdir).exit_code == 0
    assert sorted(p.name for p in (cdir / "tables").iterdir()) == [
        "summary.json", "summary.md", "summary.tex"]


def test_tables_render_the_stats_json_values_not_a_recomputation(tmp_path):
    cdir = ts.campaign(tmp_path)

    def tamper(doc):
        doc["configs"][0]["metrics"]["ssim"]["mean"] = 9.5

    ts.install(cdir, tamper)
    assert _tables(cdir).exit_code == 0
    assert "| 9.5000 ±" in (cdir / "tables" / "summary.md").read_text()


def test_stale_stats_json_is_refused_and_nothing_is_written(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    ts.edit_text(cdir / "results.json")
    result = _tables(cdir)
    assert result.exit_code == 1
    assert "run scieflow experiment stats first" in result.output
    assert not (cdir / "tables").exists()


def test_missing_stats_json_is_refused(tmp_path):
    cdir = ts.campaign(tmp_path)
    result = _tables(cdir)
    assert result.exit_code == 1
    assert "run scieflow experiment stats first" in result.output


def test_symlinked_tables_folder_is_refused(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (cdir / "tables").symlink_to(elsewhere, target_is_directory=True)
    result = _tables(cdir)
    assert result.exit_code == 1
    assert "symlink" in result.output
    assert list(elsewhere.iterdir()) == []


def test_tables_inside_a_run_emit_a_value_free_note(tmp_path):
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    cdir = ts.campaign(ws / "experiments")
    ts.install(cdir)
    assert _tables(cdir).exit_code == 0
    notes = [e for e in events.read(ws) if e["type"] == "note.experiment_analysis"]
    assert len(notes) == 1
    data = notes[0]["data"]
    assert set(data) == {"kind", "campaign", "sha256"}
    assert data["kind"] == "tables" and data["campaign"] == "stat-camp"
    assert data["sha256"] == manifest.file_sha256(cdir / "tables" / "summary.md")
