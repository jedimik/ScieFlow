"""Tables from a fresh stats.json (M-D2 plan 5.7).

Tables are rendered from the values stored in stats.json and from nothing else: no statistic is
computed here. The campaign's metric order is read from campaign.yaml (the source of the metrics),
and stats.json must record the current results.json and campaign.yaml (analysis.stats_fresh).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from . import manifest
from .analysis import stats_fresh

TOOL = "scieflow experiment tables"
VERSION = 1
STALE = "stats.json is missing or stale: run scieflow experiment stats first"
NOT_COMPUTED_NOTE = "Statistics: not computed (run `scieflow experiment stats`)"

_TEX = {
    "\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#", "_": "\\_",
    "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}",
    "<": "\\textless{}", ">": "\\textgreater{}",
}


class RenderError(ValueError):
    """A refusal that the CLI reports as exit 1, naming the rule or the file."""


def tex_escape(text: str) -> str:
    """Escape every LaTeX special character once (no double escaping)."""
    return "".join(_TEX.get(ch, ch) for ch in text)


def tex_cell(text: str) -> str:
    return tex_escape(text).replace("±", "$\\pm$").replace("—", "---")


def load_fresh_stats(campaign_dir) -> dict:
    """The stats.json document, only when it is a regular file that records the current inputs."""
    campaign_dir = Path(campaign_dir)
    path = campaign_dir / "stats.json"
    if path.is_symlink():
        raise RenderError("stats.json: a symlink is refused")
    if not path.is_file() or not stats_fresh(campaign_dir):
        raise RenderError(STALE)
    return json.loads(path.read_text())


def campaign_metrics(campaign_dir) -> list[str]:
    data = yaml.safe_load((Path(campaign_dir) / "campaign.yaml").read_text()) or {}
    return list(data.get("metrics") or [])


def output_dir(campaign_dir, name: str) -> Path:
    """campaign_dir/<name>, created; a symlinked or non-folder entry is refused."""
    path = Path(campaign_dir) / name
    if path.is_symlink():
        raise RenderError(f"{name}/: refusing a symlinked folder")
    if path.exists() and not path.is_dir():
        raise RenderError(f"{name}: not a folder")
    path.mkdir(exist_ok=True)
    return path


def _na(reason) -> str:
    return f"n/c ({reason or 'not computed'})"


def descriptive_cell(cell) -> str:
    """mean ± sd [low, high] (n); an em dash when n is 0 (plan 5.7)."""
    if not cell or not cell.get("n") or cell.get("mean") is None:
        return "—"
    mean = f"{cell['mean']:.4f}"
    sd = f"{cell['sd']:.4f}" if cell.get("sd") is not None else _na("n<2")
    ci = cell.get("ci")
    interval = (f"{ci['low']:.4f}, {ci['high']:.4f}" if ci
                else _na(cell.get("ci_note")))
    return f"{mean} ± {sd} [{interval}] ({cell['n']})"


def _test_row(t: dict) -> list[str]:
    """One row of tests.md/tex. Not computed: every value cell is n/c (reason) (plan 5.7).

    Computed but absent: an effect size that does not apply is an em dash; a p-value that
    was not adjusted (the SAP's method is not applied) or a reject flag is n/c (not adjusted).
    """
    computed = bool(t.get("computed"))
    reason = t.get("reason") or "not computed"
    effect = t.get("effect") or {}

    def num(value, spec: str) -> str:
        if not computed:
            return _na(reason)
        return "—" if value is None else format(value, spec)

    def p_value(value) -> str:
        if not computed:
            return _na(reason)
        if value is None:
            return _na("not adjusted")
        return "< 0.001" if value < 0.001 else f"{value:.3f}"

    if not computed:
        reject = _na(reason)
    elif t.get("reject") is None:
        reject = _na("not adjusted")
    else:
        reject = "yes" if t["reject"] else "no"
    where = str(t.get("config_id") or t["kind"])
    return [
        t["metric"], where, t["test"],
        num(t.get("statistic"), ".3f"), num(t.get("df"), ".2f"),
        p_value(t.get("p")), p_value(t.get("p_adjusted")), reject,
        num(effect.get("hedges_g"), ".2f"), num(effect.get("cliffs_delta"), ".2f"),
        t["family"],
    ]


TESTS_HEADERS = ["metric", "configuration", "test", "statistic", "df", "p", "p_adjusted",
                 "reject", "hedges_g", "cliffs_delta", "family"]


def _md(headers, rows) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _tex(headers, rows, spec: str) -> str:
    lines = ["% requires \\usepackage{booktabs}", f"\\begin{{tabular}}{{{spec}}}", "\\toprule",
             " & ".join(tex_cell(h) for h in headers) + " \\\\", "\\midrule"]
    lines += [" & ".join(tex_cell(c) for c in row) + " \\\\" for row in rows]
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def _summary(doc: dict, metrics: list[str]):
    headers = ["configuration", *metrics]
    rows = []
    for config in doc["configs"]:
        rows.append([config["config_id"],
                     *[descriptive_cell(config["metrics"].get(m)) for m in metrics]])
    return headers, rows


def render_summary_md(doc: dict, metrics: list[str]) -> str:
    return _md(*_summary(doc, metrics))


def render_summary_tex(doc: dict, metrics: list[str]) -> str:
    headers, rows = _summary(doc, metrics)
    return _tex(headers, rows, "l" * len(headers))


def render_tests_md(doc: dict) -> str:
    return _md(TESTS_HEADERS, [_test_row(t) for t in doc["tests"]])


def render_tests_tex(doc: dict) -> str:
    return _tex(TESTS_HEADERS, [_test_row(t) for t in doc["tests"]],
                "lll" + "r" * 7 + "l")


def _sidecar_path(out: Path, name: str) -> Path:
    return out / f"{name}.json"


def _remove_stale_tests(out: Path) -> None:
    for ext in ("md", "tex", "json"):
        path = out / f"tests.{ext}"
        if path.is_symlink() or path.exists():
            path.unlink()


def write_tables(campaign_dir) -> list[Path]:
    """Write tables/summary.{md,tex} (and tests.{md,tex} when the analysis has tests)."""
    campaign_dir = Path(campaign_dir)
    doc = load_fresh_stats(campaign_dir)
    metrics = campaign_metrics(campaign_dir)
    stats_sha = manifest.file_sha256(campaign_dir / "stats.json")
    out = output_dir(campaign_dir, "tables")

    blocks = {"summary": {"md": render_summary_md(doc, metrics),
                          "tex": render_summary_tex(doc, metrics)}}
    if doc.get("tests"):
        blocks["tests"] = {"md": render_tests_md(doc), "tex": render_tests_tex(doc)}
    else:
        _remove_stale_tests(out)

    written: list[Path] = []
    for name, texts in blocks.items():
        for ext, text in texts.items():
            path = out / f"{name}.{ext}"
            manifest.atomic_write(path, text.encode("utf-8"))
            written.append(path)
        sidecar = {
            "id": name, "kind": "table", "campaign": doc["campaign"],
            "files": {ext: manifest.file_sha256(out / f"{name}.{ext}") for ext in texts},
            "source": {"stats_sha256": stats_sha}, "tool": TOOL, "version": VERSION,
        }
        path = _sidecar_path(out, name)
        manifest.atomic_write(path, (json.dumps(sidecar, sort_keys=True, indent=1) + "\n")
                              .encode("utf-8"))
        written.append(path)
    return written
