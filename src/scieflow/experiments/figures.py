"""Figures from a fresh stats.json (M-D2 plan 5.7): deterministic PNG, SVG and PDF.

Figures are drawn from the values stored in stats.json only (the per-replicate values of each
configuration and metric, the means and intervals). Nothing is recomputed. Rendering is
deterministic: fixed rc settings, an Agg backend, and save metadata without timestamps, so two
processes with different hash seeds write identical bytes (the test of plan 5.7).
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import manifest, style as style_mod  # noqa: E402
from .tables import (  # noqa: E402
    RenderError,
    campaign_metrics,
    load_fresh_stats,
    output_dir,
    write_outputs,
)

TOOL = "scieflow experiment figures"
VERSION = 1
MAX_PANELS = 9
PANEL_COLUMNS = 3
ROW_RATIO = 0.62  # panel row height = column width × 0.62 (plan 5.7)
JITTER_SPAN = 0.15  # replicate dots sit at linspace(-0.15, 0.15, n) of the x spacing
METADATA = {
    "png": {"Software": None},
    "svg": {"Date": None, "Creator": None},
    "pdf": {"CreationDate": None, "Creator": None, "Producer": None},
}


def _rc(style: dict) -> dict:
    return {
        "svg.hashsalt": "scieflow",
        "svg.fonttype": "path",
        "pdf.fonttype": 42,
        "font.family": "DejaVu Sans",
        "font.size": style["font_pt"],
    }


def select_metrics(campaign: list[str], requested) -> list[str]:
    """The panels in campaign order: the --metric selection, or every campaign metric."""
    if requested:
        unknown = [m for m in requested if m not in campaign]
        if unknown:
            raise RenderError(f"--metric {unknown[0]}: not a campaign metric "
                              f"(metrics: {', '.join(campaign)})")
        chosen = [m for m in campaign if m in set(requested)]
    else:
        chosen = list(campaign)
    if not chosen:
        raise RenderError("no metrics to plot")
    if len(chosen) > MAX_PANELS:
        raise RenderError(f"{len(chosen)} metrics would be {len(chosen)} panels (at most "
                          f"{MAX_PANELS}): select metrics with --metric")
    return chosen


def resolve_width(width, panels: int) -> str:
    if width is None:
        return "single" if panels <= 2 else "double"
    if width not in ("single", "double"):
        raise RenderError(f"--width {width!r}: single or double")
    return width


def _numeric_parameter(configs: list[dict]):
    """The one varying parameter when it is numeric in every configuration, else None."""
    keys = sorted({k for c in configs for k in c["params"]})
    varying = [k for k in keys
               if len({json.dumps(c["params"].get(k), sort_keys=True) for c in configs}) > 1]
    if len(varying) != 1:
        return None
    key = varying[0]
    for c in configs:
        value = c["params"].get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value):
            return None
    return key


def _spacing(xs: list[float]) -> float:
    unique = sorted(set(xs))
    gaps = [b - a for a, b in zip(unique, unique[1:]) if b > a]
    return min(gaps) if gaps else 1.0


def _panel(ax, doc: dict, metric: str) -> None:
    higher = doc["metrics"][metric]["higher_is_better"]
    ax.set_title(f"{metric} ({'higher' if higher else 'lower'} is better)")
    configs = doc["configs"]
    if all(c["metrics"][metric]["n"] == 0 for c in configs):
        ax.text(0.5, 0.5, "no values", ha="center", va="center", transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        return

    numeric = _numeric_parameter(configs)
    if numeric is not None:
        xs = {c["config_id"]: float(c["params"][numeric]) for c in configs}
    else:
        xs = {c["config_id"]: float(i) for i, c in enumerate(configs)}
    spacing = _spacing(list(xs.values()))

    for c in configs:
        cell = c["metrics"][metric]
        vals = [v["value"] for v in cell["values"]]
        if not vals:
            continue
        offsets = np.linspace(-JITTER_SPAN, JITTER_SPAN, len(vals))
        x0 = xs[c["config_id"]]
        ax.scatter([x0 + o * spacing for o in offsets], vals, s=6, color="0.6",
                   linewidths=0, zorder=1)

    measured = [c for c in configs if c["metrics"][metric]["n"] > 0]
    if numeric is not None and len(measured) >= 2:
        measured.sort(key=lambda c: xs[c["config_id"]])
        ax.plot([xs[c["config_id"]] for c in measured],
                [c["metrics"][metric]["mean"] for c in measured],
                color="tab:blue", linewidth=1.0, zorder=2)
    for c in measured:
        cell = c["metrics"][metric]
        x0, mean = xs[c["config_id"]], cell["mean"]
        ci = cell["ci"]
        if ci is not None and ci["low"] <= mean <= ci["high"]:
            # a CI that does not contain the mean (an inconsistent file) draws no bar: no exception
            ax.errorbar([x0], [mean], yerr=[[mean - ci["low"]], [ci["high"] - mean]],
                        fmt="o", color="tab:blue", markersize=4, capsize=2, zorder=3)
        else:
            ax.plot([x0], [mean], marker="o", color="tab:blue", linestyle="none",
                    markersize=4, zorder=3)

    baseline = doc["settings"].get("baseline")
    base_cell = next((c for c in configs if c["config_id"] == baseline), None)
    if base_cell is not None and base_cell["metrics"][metric]["n"] > 0:
        ax.axhline(base_cell["metrics"][metric]["mean"], linestyle="--", color="0.3",
                   linewidth=0.8, zorder=0)

    if numeric is not None:
        ax.set_xlabel(numeric)
    else:
        ax.set_xticks([xs[c["config_id"]] for c in configs])
        ax.set_xticklabels([c["config_id"] for c in configs], rotation=45, ha="right")
    ax.grid(True, alpha=0.3)


def build_figure(doc: dict, metrics: list[str], width_key: str, style: dict):
    """The figure of the selected metrics (one panel each), sized by the style (plan 5.7)."""
    width_mm = style["single_column_mm"] if width_key == "single" else style["double_column_mm"]
    panels = len(metrics)
    columns = min(PANEL_COLUMNS, panels)
    rows = -(-panels // columns)
    height_mm = width_mm * ROW_RATIO * rows
    with plt.rc_context(_rc(style)):
        fig, axes = plt.subplots(rows, columns, figsize=(width_mm / 25.4, height_mm / 25.4),
                                 squeeze=False)
        flat = [ax for row in axes for ax in row]
        for ax in flat[panels:]:
            fig.delaxes(ax)
        for ax, metric in zip(flat, metrics):
            _panel(ax, doc, metric)
        fig.tight_layout()
    return fig


def write_figures(campaign_dir, metrics=None, width=None, style_file=None) -> list[Path]:
    """Write figures/metrics.<ext> per format and figures/metrics.json (plan 5.7)."""
    campaign_dir = Path(campaign_dir)
    style = style_mod.load_style(style_file)
    doc = load_fresh_stats(campaign_dir)
    selected = select_metrics(campaign_metrics(campaign_dir), metrics)
    width_key = resolve_width(width, len(selected))
    stats_sha = manifest.file_sha256(campaign_dir / "stats.json")
    out = output_dir(campaign_dir, "figures")

    fig = build_figure(doc, selected, width_key, style)
    outputs: list[tuple[Path, bytes]] = []
    files: dict[str, str] = {}
    try:
        with plt.rc_context(_rc(style)):
            for ext in style["formats"]:
                buf = io.BytesIO()
                fig.savefig(buf, format=ext, dpi=style["dpi"], metadata=METADATA[ext])
                data = buf.getvalue()
                files[ext] = manifest.sha256_bytes(data)
                outputs.append((out / f"metrics.{ext}", data))
    finally:
        plt.close(fig)

    sidecar = {
        "id": "metrics", "kind": "figure", "campaign": doc["campaign"], "metrics": selected,
        "width": width_key, "files": files, "source": {"stats_sha256": stats_sha},
        "style": style, "tool": TOOL, "version": VERSION,
    }
    outputs.append((out / "metrics.json",
                    (json.dumps(sidecar, sort_keys=True, indent=1) + "\n").encode("utf-8")))
    written = write_outputs(outputs)
    for ext in set(METADATA) - set(style["formats"]):  # formats no longer asked for
        stale = out / f"metrics.{ext}"
        if stale.is_symlink() or stale.exists():
            stale.unlink()
    return written
