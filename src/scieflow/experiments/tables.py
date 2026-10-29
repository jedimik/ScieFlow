"""Tables from a fresh stats.json (M-D2 plan 5.7).

Tables are rendered from the values stored in stats.json and from nothing else: no statistic is
computed here. The campaign's metric order is read from campaign.yaml (the source of the metrics),
and stats.json must record the current results.json and campaign.yaml (analysis.stats_fresh).
"""

from __future__ import annotations

import json
import math
import os
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


_REQUIRED_KEYS = ("version", "campaign", "source", "settings", "design", "metrics", "configs",
                  "tests", "families")
_TEST_KEYS = ("id", "metric", "kind", "test", "family", "computed")
_FAMILY_KEYS = ("name", "correction", "alpha", "size")


def _reject_constant(name: str):
    """json.loads hook: NaN, Infinity and -Infinity are refused, never rendered as numbers."""
    raise RenderError(f"stats.json: non-finite number {name} is refused")


def _finite(value, where: str) -> None:
    """Refuse a non-finite float anywhere in the document (overflowing literals included)."""
    if isinstance(value, float) and not math.isfinite(value):
        raise RenderError(f"stats.json: {where}: non-finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, f"{where}[{index}]")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_cell(cell, where: str) -> None:
    if not isinstance(cell, dict):
        raise RenderError(f"stats.json: {where}: missing cell")
    n = cell.get("n")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise RenderError(f"stats.json: {where}.n: a count of 0 or more")
    for key in ("mean", "sd"):
        if cell.get(key) is not None and not _number(cell[key]):
            raise RenderError(f"stats.json: {where}.{key}: a number or null")
    if n > 0 and not _number(cell.get("mean")):
        raise RenderError(f"stats.json: {where}.mean: a number when n > 0")
    values = cell.get("values")
    if not isinstance(values, list) or any(
            not isinstance(v, dict) or not _number(v.get("value")) for v in values):
        raise RenderError(f"stats.json: {where}.values: a list of {{value}} numbers")
    ci = cell.get("ci")
    if ci is not None:
        if not isinstance(ci, dict) or not _number(ci.get("low")) or not _number(ci.get("high")):
            raise RenderError(f"stats.json: {where}.ci: low and high must be numbers")
        if ci["low"] > ci["high"]:
            raise RenderError(f"stats.json: {where}.ci: low <= high is required")


def _validate_stats(doc, metrics: list[str]) -> None:
    """Fail closed on a malformed document: the rule is named, nothing is rendered (plan 5.12)."""
    if not isinstance(doc, dict):
        raise RenderError("stats.json: the document must be a mapping")
    for key in _REQUIRED_KEYS:
        if key not in doc:
            raise RenderError(f"stats.json: missing key {key!r}")
    _finite(doc, "document")
    if doc["version"] != 1:
        raise RenderError("stats.json: version must be 1")
    if not (isinstance(doc["metrics"], dict) and isinstance(doc["settings"], dict)
            and isinstance(doc["design"], dict) and isinstance(doc["configs"], list)
            and isinstance(doc["tests"], list) and isinstance(doc["families"], list)):
        raise RenderError("stats.json: metrics, settings and design are mappings; "
                          "configs, tests and families are lists")
    if not isinstance(doc["design"].get("status"), str):
        raise RenderError("stats.json: design.status: a string is required")
    for metric in metrics:
        spec = doc["metrics"].get(metric)
        if not isinstance(spec, dict) or not isinstance(spec.get("higher_is_better"), bool):
            raise RenderError(f"stats.json: metrics.{metric}.higher_is_better: a boolean; "
                              f"the campaign metric must be listed")
    for i, config in enumerate(doc["configs"]):
        where = f"configs[{i}]"
        if (not isinstance(config, dict) or not isinstance(config.get("config_id"), str)
                or not isinstance(config.get("params"), dict)
                or not isinstance(config.get("metrics"), dict)):
            raise RenderError(f"stats.json: {where}: config_id, params and metrics are required")
        for metric in metrics:
            _check_cell(config["metrics"].get(metric), f"{where}.metrics.{metric}")
    for i, test in enumerate(doc["tests"]):
        if not isinstance(test, dict) or any(k not in test for k in _TEST_KEYS):
            raise RenderError(f"stats.json: tests[{i}]: {', '.join(_TEST_KEYS)} are required")
    for i, family in enumerate(doc["families"]):
        if not isinstance(family, dict) or any(k not in family for k in _FAMILY_KEYS):
            raise RenderError(f"stats.json: families[{i}]: {', '.join(_FAMILY_KEYS)} are required")


def load_fresh_stats(campaign_dir) -> dict:
    """The stats.json document, only when it is a regular file that records the current inputs.

    A hash-fresh document is still validated before anything is rendered from it: malformed
    structure, NaN/Infinity and inconsistent intervals are refused, naming the rule.
    """
    campaign_dir = Path(campaign_dir)
    path = campaign_dir / "stats.json"
    if path.is_symlink():
        raise RenderError("stats.json: a symlink is refused")
    if not path.is_file() or not stats_fresh(campaign_dir):
        raise RenderError(STALE)
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        raise RenderError("stats.json: unreadable") from e
    try:
        doc = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise RenderError("stats.json: not valid JSON") from e
    _validate_stats(doc, campaign_metrics(campaign_dir))
    return doc


def campaign_metrics(campaign_dir) -> list[str]:
    try:
        data = yaml.safe_load((Path(campaign_dir) / "campaign.yaml").read_text()) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise RenderError("campaign.yaml: unreadable YAML") from e
    if not isinstance(data, dict):
        raise RenderError("campaign.yaml: must be a mapping")
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


def _tmp_of(path: Path) -> Path:
    return path.with_name(path.name + ".tmp")


def _refuse_link(path: Path) -> None:
    if path.is_symlink():
        raise RenderError(f"{path.name}: refusing a symlinked output")
    if path.exists() and not path.is_file():
        raise RenderError(f"{path.name}: not a regular file")


def write_outputs(outputs) -> list[Path]:
    """Write every (path, bytes) pair, or none (plan 5.12 "symlinked outputs refused").

    A symlink at an output or at its .tmp sibling is refused before anything is written, and the
    .tmp file is created with O_EXCL, so a pre-planted link cannot redirect a write outside the
    campaign folder.
    """
    pairs = [(Path(path), data) for path, data in outputs]
    for path, _ in pairs:
        _refuse_link(path)
        _refuse_link(_tmp_of(path))
    for path, data in pairs:
        _refuse_link(path)
        tmp = _tmp_of(path)
        _refuse_link(tmp)
        if tmp.exists():
            tmp.unlink()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    return [path for path, _ in pairs]


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

    outputs: list[tuple[Path, bytes]] = []
    for name, texts in blocks.items():
        encoded = {ext: text.encode("utf-8") for ext, text in texts.items()}
        for ext, data in encoded.items():
            outputs.append((out / f"{name}.{ext}", data))
        sidecar = {
            "id": name, "kind": "table", "campaign": doc["campaign"],
            "files": {ext: manifest.sha256_bytes(data) for ext, data in encoded.items()},
            "source": {"stats_sha256": stats_sha}, "tool": TOOL, "version": VERSION,
        }
        outputs.append((_sidecar_path(out, name),
                        (json.dumps(sidecar, sort_keys=True, indent=1) + "\n").encode("utf-8")))
    written = write_outputs(outputs)
    if not doc.get("tests"):
        _remove_stale_tests(out)
    return written
