"""M-D2 t5: figures render only from a fresh stats.json, deterministically (plan 5.7). Synthetic only."""

import copy
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.run import status
from scieflow.experiments import manifest
from scieflow.experiments.cli import experiment

_SUPPORT = Path(__file__).with_name("tables_support.py")
_spec = importlib.util.spec_from_file_location("tables_support", _SUPPORT)
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

_CLI = "import sys; from scieflow.experiments.cli import experiment; experiment(sys.argv[1:])"


def _figures(cdir, *args):
    return CliRunner().invoke(experiment, ["figures", *args, str(cdir)])


def _png_size(path):
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def _doc(cdir):
    return json.loads((Path(cdir) / "stats.json").read_text())


def _subprocess_figures(cdir, seed, style_file=None):
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(seed)
    env["MPLCONFIGDIR"] = str(Path(cdir).parent / f"mpl-{seed}")
    args = [sys.executable, "-c", _CLI, "figures", str(cdir)]
    if style_file is not None:
        args[4:4] = ["--style-file", str(style_file)]
    proc = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600)
    return proc


def _style_file(tmp_path, formats=("png", "svg", "pdf")):
    path = tmp_path / "style.yml"
    path.write_text(yaml.safe_dump({"figures": {"formats": list(formats)}}))
    return path


def test_two_processes_with_different_hash_seeds_write_identical_bytes(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    style = _style_file(tmp_path)
    digests = []
    for seed in (1, 2):
        shutil.rmtree(cdir / "figures", ignore_errors=True)
        proc = _subprocess_figures(cdir, seed, style)
        assert proc.returncode == 0, proc.stderr
        names = sorted(p.name for p in (cdir / "figures").iterdir())
        assert names == ["metrics.json", "metrics.pdf", "metrics.png", "metrics.svg"]
        digests.append({n: manifest.file_sha256(cdir / "figures" / n) for n in names})
    assert digests[0] == digests[1]


def test_sidecar_hashes_equal_the_files_and_carry_no_timestamps(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    proc = _subprocess_figures(cdir, 3, _style_file(tmp_path))
    assert proc.returncode == 0, proc.stderr
    sidecar = json.loads((cdir / "figures" / "metrics.json").read_text())
    assert sidecar["id"] == "metrics" and sidecar["kind"] == "figure"
    assert sidecar["campaign"] == "stat-camp" and sidecar["metrics"] == ["ssim"]
    assert sidecar["source"] == {"stats_sha256": manifest.file_sha256(cdir / "stats.json")}
    for ext in ("png", "svg", "pdf"):
        assert sidecar["files"][ext] == manifest.file_sha256(cdir / "figures" / f"metrics.{ext}")
    assert sidecar["tool"] == "scieflow experiment figures" and sidecar["version"] == 1


def test_metadata_has_no_creation_date_software_or_date(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    assert _subprocess_figures(cdir, 4, _style_file(tmp_path)).returncode == 0
    png = (cdir / "figures" / "metrics.png").read_bytes()
    svg = (cdir / "figures" / "metrics.svg").read_bytes()
    pdf = (cdir / "figures" / "metrics.pdf").read_bytes()
    assert b"Software" not in png
    assert b"dc:date" not in svg
    assert b"/CreationDate" not in pdf and b"/Producer" not in pdf


def test_png_pixel_size_follows_the_column_width_and_dpi(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    assert _figures(cdir).exit_code == 0
    width, height = _png_size(cdir / "figures" / "metrics.png")
    # one metric: single column (89 mm) at 300 dpi, one row of height 0.62 × width
    assert abs(width - round(89 / 25.4 * 300)) <= 1
    assert abs(height - round(89 * 0.62 / 25.4 * 300)) <= 1


def test_default_width_is_single_for_two_panels_and_double_on_request(tmp_path):
    cdir = ts.campaign(tmp_path, metrics=("ssim", "psnr"))
    ts.install(cdir, metrics=("ssim", "psnr"))
    assert _figures(cdir).exit_code == 0
    width, _ = _png_size(cdir / "figures" / "metrics.png")
    assert abs(width - round(89 / 25.4 * 300)) <= 1  # default: single for at most two panels
    cdir2 = ts.campaign(tmp_path / "b", metrics=("ssim", "psnr"))
    ts.install(cdir2, metrics=("ssim", "psnr"))
    assert _figures(cdir2, "--width", "double").exit_code == 0
    width2, _ = _png_size(cdir2 / "figures" / "metrics.png")
    assert abs(width2 - round(183 / 25.4 * 300)) <= 1


def test_panels_follow_the_campaign_metric_order(tmp_path):
    from scieflow.experiments import figures, style

    cdir = ts.campaign(tmp_path, metrics=("ssim", "psnr"))
    doc = ts.install(cdir, metrics=("ssim", "psnr"))
    fig = figures.build_figure(doc, ["ssim", "psnr"], "single", style.load_style(None))
    titles = [ax.get_title() for ax in fig.axes]
    assert titles == ["ssim (higher is better)", "psnr (higher is better)"]


def test_metric_selection_draws_only_the_selected_panel(tmp_path):
    cdir = ts.campaign(tmp_path, metrics=("ssim", "psnr"))
    ts.install(cdir, metrics=("ssim", "psnr"))
    assert _figures(cdir, "--metric", "psnr").exit_code == 0
    sidecar = json.loads((cdir / "figures" / "metrics.json").read_text())
    assert sidecar["metrics"] == ["psnr"]


def test_ten_metrics_without_metric_option_exits_1(tmp_path):
    cdir = ts.campaign(tmp_path)
    names = [f"m{i}" for i in range(10)]
    campaign_yaml = yaml.safe_load((cdir / "campaign.yaml").read_text())
    campaign_yaml["metrics"] = names
    (cdir / "campaign.yaml").write_text(yaml.safe_dump(campaign_yaml, sort_keys=False))
    doc = ts.fixed_doc()
    doc["metrics"] = {n: {"higher_is_better": True} for n in names}
    for config in doc["configs"]:  # a valid document: every campaign metric has a cell
        for name in names:
            config["metrics"][name] = copy.deepcopy(config["metrics"]["ssim"])
    ts.install_doc(cdir, doc)
    result = _figures(cdir)
    assert result.exit_code == 1
    assert "select metrics with --metric" in result.output
    assert not (cdir / "figures").exists()


def test_unknown_metric_option_exits_1(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    result = _figures(cdir, "--metric", "nope")
    assert result.exit_code == 1
    assert "nope" in result.output


def test_numeric_single_parameter_is_drawn_as_a_line(tmp_path):
    from scieflow.experiments import figures, style

    cdir = ts.campaign(tmp_path)
    doc = ts.install(cdir)
    fig = figures.build_figure(doc, ["ssim"], "single", style.load_style(None))
    lines = [ln for ln in fig.axes[0].get_lines() if len(ln.get_xdata()) == 3]
    assert lines, "a line through the means over the numeric parameter sigma"
    assert list(lines[0].get_xdata()) == [0.5, 1.0, 2.0]
    means = [doc["configs"][i]["metrics"]["ssim"]["mean"] for i in range(3)]
    assert list(lines[0].get_ydata()) == means


def test_configuration_without_ci_gets_no_error_bar_and_no_exception(tmp_path):
    from scieflow.experiments import figures, style

    cdir = ts.campaign(tmp_path)
    doc = ts.install(cdir, lambda d: d["configs"][1]["metrics"]["ssim"].update(ci=None,
                                                                              ci_note="degenerate"))
    fig = figures.build_figure(doc, ["ssim"], "single", style.load_style(None))
    assert fig.axes[0].get_title() == "ssim (higher is better)"
    assert _figures(cdir).exit_code == 0


def test_metric_without_values_in_any_configuration_is_a_no_values_panel(tmp_path):
    from scieflow.experiments import figures, style

    cdir = ts.campaign(tmp_path, reference=False)
    doc = ts.install(cdir, empty=True)
    fig = figures.build_figure(doc, ["ssim"], "single", style.load_style(None))
    assert "no values" in [t.get_text() for t in fig.axes[0].texts]
    assert _figures(cdir).exit_code == 0


def test_stale_stats_json_is_refused_and_nothing_is_written(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    ts.edit_text(cdir / "results.json")
    result = _figures(cdir)
    assert result.exit_code == 1
    assert "run scieflow experiment stats first" in result.output
    assert not (cdir / "figures").exists()


def test_symlinked_figures_folder_is_refused(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (cdir / "figures").symlink_to(elsewhere, target_is_directory=True)
    result = _figures(cdir)
    assert result.exit_code == 1
    assert "symlink" in result.output
    assert list(elsewhere.iterdir()) == []


def test_style_file_sets_the_size_and_the_formats(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    style_path = tmp_path / "style.yml"
    style_path.write_text(yaml.safe_dump({"figures": {"single_column_mm": 60,
                                                      "formats": ["svg"]}}))
    result = _figures(cdir, "--style-file", str(style_path))
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in (cdir / "figures").iterdir()) == [
        "metrics.json", "metrics.svg"]
    sidecar = json.loads((cdir / "figures" / "metrics.json").read_text())
    assert sidecar["style"]["single_column_mm"] == 60 and sidecar["style"]["formats"] == ["svg"]


def test_bad_style_file_exits_1_naming_the_key(tmp_path):
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    style_path = tmp_path / "style.yml"
    style_path.write_text(yaml.safe_dump({"figures": {"dpi": 5000}}))
    result = _figures(cdir, "--style-file", str(style_path))
    assert result.exit_code == 1
    assert "dpi" in result.output


def test_figures_inside_a_run_emit_a_value_free_note(tmp_path):
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    cdir = ts.campaign(ws / "experiments")
    ts.install(cdir)
    assert _figures(cdir).exit_code == 0
    notes = [e for e in events.read(ws) if e["type"] == "note.experiment_analysis"]
    assert len(notes) == 1
    data = notes[0]["data"]
    assert set(data) == {"kind", "campaign", "sha256"}
    assert data["kind"] == "figures"
    assert data["sha256"] == manifest.file_sha256(cdir / "figures" / "metrics.json")


def _victim(tmp_path) -> Path:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n")
    return victim


@pytest.mark.parametrize("name", ["metrics.png.tmp", "metrics.png", "metrics.svg.tmp",
                                  "metrics.json"])
def test_symlinked_figure_output_is_refused_and_nothing_is_written(tmp_path, name):
    """Finding 1: a pre-planted .tmp or final symlink in figures/ is never followed."""
    cdir = ts.campaign(tmp_path)
    ts.install(cdir)
    victim = _victim(tmp_path)
    out = cdir / "figures"
    out.mkdir()
    (out / name).symlink_to(victim)
    result = _figures(cdir)
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert victim.read_text() == "keep me\n"
    assert [p.name for p in out.iterdir()] == [name]


def test_inverted_ci_is_refused_with_the_rule(tmp_path):
    """Finding 2/3: low <= high is a rule of the stats.json loader; figures refuse, naming it."""
    cdir = ts.campaign(tmp_path)

    def invert(doc):
        ci = doc["configs"][0]["metrics"]["ssim"]["ci"]
        ci["low"], ci["high"] = ci["high"], ci["low"]

    ts.write_raw(cdir, _edited(ts.fixed_doc(), invert))
    result = _figures(cdir)
    assert result.exit_code == 1, result.output
    assert "ci: low <= high" in result.output
    assert isinstance(result.exception, SystemExit)
    assert not (cdir / "figures").exists()


def test_nan_in_stats_json_is_refused_and_no_figure_is_written(tmp_path):
    cdir = ts.campaign(tmp_path)
    doc = _edited(ts.fixed_doc(), lambda d: d["configs"][0]["metrics"]["ssim"].update(
        mean=float("nan")))
    ts.write_raw(cdir, doc)
    result = _figures(cdir)
    assert result.exit_code == 1, result.output
    assert "non-finite" in result.output
    assert not (cdir / "figures").exists()


def test_ci_that_excludes_the_mean_draws_no_error_bar_and_no_exception(tmp_path):
    """Finding 3: a CI not containing the mean must not make errorbar raise (no bar is drawn)."""
    cdir = ts.campaign(tmp_path)
    ts.install(cdir, lambda d: d["configs"][1]["metrics"]["ssim"].update(
        ci={"low": 0.1, "high": 0.2, "level": 0.95, "method": "BCa", "resamples": 9999,
            "seed": 0}))
    result = _figures(cdir)
    assert result.exit_code == 0, result.output
    assert (cdir / "figures" / "metrics.png").is_file()


def test_all_no_values_panel_is_byte_deterministic_across_processes(tmp_path):
    """Finding 4(a): plan t5 case 3 (review F3) — the n = 0 panel is deterministic too."""
    cdir = ts.campaign(tmp_path, reference=False)
    ts.install(cdir, empty=True)
    style = _style_file(tmp_path)
    digests = []
    for seed in (5, 6):
        shutil.rmtree(cdir / "figures", ignore_errors=True)
        proc = _subprocess_figures(cdir, seed, style)
        assert proc.returncode == 0, proc.stderr
        names = sorted(p.name for p in (cdir / "figures").iterdir())
        digests.append({n: manifest.file_sha256(cdir / "figures" / n) for n in names})
    assert digests[0] == digests[1]
    assert len(digests[0]) == 4


def _extended(count: int):
    """A campaign and a stats.json with count metrics (ssim, psnr and copies of ssim)."""
    names = ["ssim", "psnr"] + [f"extra{i}" for i in range(count - 2)]
    return names


@pytest.mark.parametrize("count", [2, 4, 5])
def test_svg_has_one_axes_group_per_selected_panel(tmp_path, count):
    """Finding 4(b): panel count read back from the SVG axes_ groups, incl. more than 3 metrics."""
    names = _extended(count)
    cdir = ts.campaign(tmp_path, metrics=("ssim", "psnr"))
    campaign_yaml = yaml.safe_load((cdir / "campaign.yaml").read_text())
    campaign_yaml["metrics"] = names
    (cdir / "campaign.yaml").write_text(yaml.safe_dump(campaign_yaml, sort_keys=False))
    doc = ts.fixed_doc(metrics=("ssim", "psnr"))
    for name in names[2:]:
        doc["metrics"][name] = {"higher_is_better": True}
        for config in doc["configs"]:
            config["metrics"][name] = copy.deepcopy(config["metrics"]["ssim"])
    ts.install_doc(cdir, doc)
    style = _style_file(tmp_path, formats=("svg",))
    result = _figures(cdir, "--style-file", str(style))
    assert result.exit_code == 0, result.output
    svg = (cdir / "figures" / "metrics.svg").read_text()
    assert len(re.findall(r'id="axes_\d+"', svg)) == count
    sidecar = json.loads((cdir / "figures" / "metrics.json").read_text())
    assert sidecar["metrics"] == names


def test_metric_selection_is_written_in_campaign_order(tmp_path):
    """Finding 4(b): selection order does not reorder the panels; the campaign order wins (5.7)."""
    cdir = ts.campaign(tmp_path, metrics=("ssim", "psnr"))
    ts.install(cdir, metrics=("ssim", "psnr"))
    result = _figures(cdir, "--metric", "psnr", "--metric", "ssim")
    assert result.exit_code == 0, result.output
    sidecar = json.loads((cdir / "figures" / "metrics.json").read_text())
    assert sidecar["metrics"] == ["ssim", "psnr"]


def _edited(doc, edit):
    edit(doc)
    return doc
