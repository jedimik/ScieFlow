from __future__ import annotations

import json
from numbers import Number
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import yaml  # noqa: E402

from .compare import compare_campaign  # noqa: E402
from .tables import (  # noqa: E402
    NOT_COMPUTED_NOTE,
    RenderError,
    load_fresh_stats,
    render_summary_md,
    render_tests_md,
)


def _configuration_lines(comparison: dict, rank_by: str) -> list[str]:
    """Replicate campaigns: one row per configuration, in the compare order (plan 5.7)."""
    lines = [
        "### Configurations",
        "",
        f"| configuration | params | n | failed | mean `{rank_by}` |",
        "|---|---|---|---|---|",
    ]
    for config in comparison["configs"]:
        params = ", ".join(f"{k}={v}" for k, v in sorted(config["params"].items()))
        mean = "n/a" if config["mean"] is None else f"{config['mean']:.4f}"
        lines.append(f"| {config['config_id']} | {params} | {config['n']} | "
                     f"{config['n_failed']} | {mean} |")
    lines.append("")
    return lines


def _statistics_lines(campaign_dir: Path, metric_names: list[str]) -> list[str]:
    """The Statistics section from a fresh stats.json; otherwise one line (plan 5.7)."""
    try:
        doc = load_fresh_stats(campaign_dir)
    except RenderError:
        return ["", NOT_COMPUTED_NOTE]
    lines = ["", "## Statistics", ""]
    lines += render_summary_md(doc, metric_names).splitlines()
    if doc.get("tests"):
        lines += ["", "Tests:", ""] + render_tests_md(doc).splitlines()
    lines.append("")
    for family in doc.get("families", []):
        lines.append(f"- family `{family['name']}`: correction {family['correction']}, "
                     f"alpha {family['alpha']}, size {family['size']}")
    lines.append(f"- design: status `{doc['design']['status']}`")
    for rel in ("tables/summary.md", "tables/tests.md", "figures/metrics.png"):
        if (campaign_dir / rel).is_file():
            lines.append(f"- see `{rel}`")
    return lines


def _varying_params(ranked: list[dict]) -> list[str]:
    if not ranked:
        return []
    keys = ranked[0]["params"].keys()
    return [
        k for k in keys
        if len({json.dumps(r["params"].get(k)) for r in ranked}) > 1
    ]


def _plot(campaign_dir: Path, comparison: dict) -> Path:
    rank_by = comparison["rank_by"]
    ranked = comparison["ranked"]
    plots_dir = campaign_dir / "plots"
    plots_dir.mkdir(exist_ok=True)
    out = plots_dir / f"{rank_by}.png"

    fig, ax = plt.subplots(figsize=(8, 5))
    varying = _varying_params(ranked)
    numeric_param = (
        varying[0]
        if len(varying) == 1
        and all(isinstance(r["params"][varying[0]], Number) for r in ranked)
        else None
    )
    if numeric_param:
        points = sorted(
            (r["params"][numeric_param], r["metrics"][rank_by]) for r in ranked
        )
        ax.plot(*zip(*points), marker="o")
        best = ranked[0]
        ax.plot(
            best["params"][numeric_param], best["metrics"][rank_by],
            marker="*", markersize=16, color="tab:red", linestyle="none",
            label=f"best: {best['run_id']}",
        )
        ax.set_xlabel(numeric_param)
        ax.legend()
    else:
        ids = [r["run_id"] for r in ranked]
        ax.bar(ids, [r["metrics"][rank_by] for r in ranked])
        ax.tick_params(axis="x", rotation=45)
    ax.set_ylabel(rank_by)
    ax.set_title(f"{comparison['campaign']}: {rank_by} per run")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def generate_report(campaign_dir: str | Path, include_direct: bool = False) -> Path:
    campaign_dir = Path(campaign_dir)
    comparison = compare_campaign(campaign_dir, include_direct=include_direct)
    campaign = yaml.safe_load((campaign_dir / "campaign.yaml").read_text())
    rank_by = comparison["rank_by"]
    metric_names = campaign["metrics"]

    lines = [f"# Campaign report: {comparison['campaign']}", ""]
    if campaign.get("notes"):
        lines += [f"> {campaign['notes']}", ""]
    n_direct = len(comparison["direct"])
    n_total = len(comparison["ranked"]) + len(comparison["failed"]) + n_direct
    best = comparison["best"]
    best_display = f"`{best}`" if best is not None else "n/a (no runs ranked)"
    lines += [
        f"- runs: {n_total} ({len(comparison['failed'])} failed)",
        f"- ranked by: `{rank_by}`",
        f"- best run: {best_display}",
    ]
    if n_direct:
        lines.append(f"- direct runs (dev-only, not ranked): {n_direct}")
    if include_direct:
        lines.append("- direct runs are included in the ranking (development only)")
    if comparison["mode_unknown"]:
        unknown = ", ".join(f"`{r}`" for r in comparison["mode_unknown"])
        lines.append(f"- runs with unknown mode (ranked as recorded): {unknown}")
    lines += ["", "## Results", ""]
    if comparison.get("configs") is not None:
        lines += _configuration_lines(comparison, rank_by)
    lines += [
        "| run | params | " + " | ".join(metric_names) + " | validation |",
        "|---|---|" + "---|" * (len(metric_names) + 1),
    ]
    for entry in comparison["ranked"]:
        params = ", ".join(f"{k}={v}" for k, v in sorted(entry["params"].items()))
        values = " | ".join(
            f"{entry['metrics'].get(m, float('nan')):.4f}" for m in metric_names
        )
        flag = "PASS" if entry["validation"]["passed"] else "FAIL"
        tag = " (direct)" if entry["mode"] == "direct" else ""
        lines.append(f"| `{entry['run_id']}`{tag} | {params} | {values} | {flag} |")
    for entry in comparison["direct"]:
        params = ", ".join(f"{k}={v}" for k, v in sorted(entry["params"].items()))
        values = " | ".join(
            f"{entry['metrics'].get(m, float('nan')):.4f}" for m in metric_names
        )
        lines.append(
            f"| `{entry['run_id']}` (direct) | {params} | {values} | DIRECT, NOT RANKED |"
        )
    for run_id in comparison["failed"]:
        lines.append(f"| `{run_id}` | — | " + " | ".join(["—"] * len(metric_names)) + " | NOT RANKED |")

    lines += _statistics_lines(campaign_dir, metric_names)
    if comparison["ranked"]:
        plot_path = _plot(campaign_dir, comparison)
        lines += ["", "## QC plot", "", f"![{rank_by}]({plot_path.relative_to(campaign_dir)})"]

    if campaign.get("validation"):
        lines += ["", "## Validation rules", ""]
        for metric, rule in campaign["validation"].items():
            rule_str = ", ".join(f"{k} {v}" for k, v in rule.items())
            lines.append(f"- `{metric}`: {rule_str}")

    report_path = campaign_dir / "report.md"
    evaluation = ""
    if report_path.exists():
        existing_lines = report_path.read_text().splitlines()
        for i, line in enumerate(existing_lines):
            if line.startswith("## Evaluation"):
                evaluation = "\n".join(existing_lines[i:])
                break

    content = "\n".join(lines) + "\n"
    if evaluation:
        content += "\n" + evaluation + "\n"
    report_path.write_text(content)
    return report_path
