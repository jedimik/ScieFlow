"""Paper-class registry and templates (M-F 5.11).

The registry (`templates/paper/registry.yml`) names the four classes ScieFlow can render and where each
class comes from. The templates hold placeholders only; the class-specific author block is built by
render.py. Class files are never vendored (M-F-D12).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scieflow import research

CLASSES = ("article", "elsarticle", "IEEEtran", "svjour3")

PLACEHOLDERS = frozenset({"CLASSOPTIONS", "TITLE", "AUTHORS", "ABSTRACT", "KEYWORDS", "SECTIONS",
                          "BIBSTYLE", "NATBIB"})

TEMPLATE_DIR = Path(research.__file__).parent / "templates" / "paper"

REGISTRY_FILE = "registry.yml"
REQUIRED_KEYS = frozenset({"source", "bibtex_style", "natbib", "template"})
SOURCES = frozenset({"tex-core", "ctan", "publisher"})
PLACEHOLDER_RE = re.compile(r"%%([A-Z]+)%%")


class TemplateError(ValueError):
    """The registry or a template is malformed."""


def _inside(template_dir: Path, name: str) -> Path:
    path = (template_dir / name).resolve()
    if template_dir.resolve() not in path.parents:
        raise TemplateError(f"template {name!r} lies outside the template folder")
    return path


def read_template(name: str, template_dir=TEMPLATE_DIR) -> str:
    """The text of one template file under `template_dir`."""
    path = _inside(Path(template_dir), name)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"template {name!r} cannot be read: {exc}") from exc


def load_registry(template_dir=TEMPLATE_DIR) -> dict:
    """{class: entry} for the four classes, each entry carrying the keys of REQUIRED_KEYS.

    Refused when a class is missing or extra, a key is missing, the source is unknown, `natbib` is not
    a boolean, or a template file does not exist.
    """
    template_dir = Path(template_dir)
    try:
        data = yaml.safe_load((template_dir / REGISTRY_FILE).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TemplateError(f"registry unreadable: {exc}") from exc
    if not isinstance(data, dict) or set(data) != set(CLASSES):
        raise TemplateError("the registry must list exactly: " + ", ".join(CLASSES))
    out = {}
    for cls in CLASSES:
        entry = data[cls]
        if not isinstance(entry, dict) or not REQUIRED_KEYS <= set(entry):
            raise TemplateError(f"{cls}: the registry entry needs {', '.join(sorted(REQUIRED_KEYS))}")
        if entry["source"] not in SOURCES:
            raise TemplateError(f"{cls}: unknown source {entry['source']!r}")
        if not isinstance(entry["natbib"], bool):
            raise TemplateError(f"{cls}: natbib must be true or false")
        if not isinstance(entry["template"], str):
            raise TemplateError(f"{cls}: template must be a path")
        read_template(entry["template"], template_dir)
        out[cls] = dict(entry)
    return out
