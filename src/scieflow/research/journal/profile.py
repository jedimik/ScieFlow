"""Journal profile format, loading and validation (M-F plan 5.5, 5.17).

A profile is `config/journals/<slug>.yml` (version 1). This module is the only reader of
`schemas/journal-profile.yml`: the key lists, enums and integer ranges come from there; the types
and the evidence rules are below. Every problem is a Problem (rule, path, message, level) and
fails closed: an unknown key, a missing key, a wrong type, a quote that is not verbatim in a stored
page, a number or guideline name that the quote does not contain, or a conflict that does not pick
the field's value is an error. Warnings (`stale`, `sources-unavailable`) never fail a check.

Run sources (written by `journal research`, M-F 5.4): `workspace/<run>/journal/sources.json` is
`{"version": 1, "sources": [row, ...]}`; each `ok` row has `id`, `url`, `text_sha256`, and its text is
`journal/sources/<id>.txt`. This module only reads them.

Nothing here touches the network or writes a file. Synthetic and human-approved pages only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scieflow.research.journal import quotes

SCHEMA = "schemas/journal-profile.yml"
FIGURE_STYLE = "schemas/figure-style.yml"
MAX_PROFILE_BYTES = 1_000_000
MAX_SOURCES_BYTES = 20_000_000
_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_FACTS = ("identity", "oa", "norms")
BY_STUDY = "reporting_guidelines.by_study_type"

# Type of every leaf, keyed by path with list indexes written as `.item`. `?` allows null; `list:`
# is a list of that scalar; `enum:NAME` is one of schemas/journal-profile.yml `enums.NAME`.
TYPES = {
    "version": "int",
    "identity.name": "str", "identity.slug": "str", "identity.issn_l": "str?",
    "identity.issns": "list:str", "identity.publisher": "str?", "identity.homepage_url": "str?",
    "identity.openalex_id": "str?", "identity.crossref_title": "str?",
    "oa.is_oa": "bool?", "oa.is_in_doaj": "bool?", "oa.apc_usd": "int?",
    "norms.sample.n": "int?", "norms.sample.from_date": "str?", "norms.sample.to_date": "str?",
    "norms.references.median": "num?", "norms.references.p25": "num?", "norms.references.p75": "num?",
    "norms.abstract_words.median": "num?", "norms.abstract_words.p25": "num?",
    "norms.abstract_words.p75": "num?", "norms.abstract_words.share_with_abstract": "num?",
    "norms.title_words.median": "num?",
    "scope.text": "str",
    "article_types": "list",
    "article_types.item.id": "str", "article_types.item.label": "str",
    "article_types.item.abstract.structured": "bool?",
    "article_types.item.abstract.headings": "list:str?",
    "article_types.item.structure.order": "enum:order?",
    "article_types.item.structure.sections": "list",
    "article_types.item.structure.sections.item.title": "str",
    "article_types.item.structure.sections.item.aliases": "list:str",
    "article_types.item.structure.sections.item.required": "bool?",
    "article_types.item.structure.sections.item.words_max": "int?",
    "references.style_name": "str?", "references.bibtex_style": "str?", "references.natbib": "bool?",
    "references.numbering": "enum:numbering?",
    "templates.latex_class": "enum:latex_class?", "templates.latex_class_name": "str?",
    "templates.latex_options": "list:str?", "templates.template_url": "str?",
    "templates.word_first": "bool?",
    "submission.system": "str?", "submission.required_files": "list",
    "submission.required_files.item.id": "str", "submission.required_files.item.label": "str",
    "submission.line_numbers": "bool?", "submission.blinding": "enum:blinding?",
    "declarations.required": "list:enum:declarations",
    "reporting_guidelines.by_study_type": "map",
    "reporting_guidelines.other": "list:str",
    "figures": "map?",
    "figures.single_column_mm": "int", "figures.double_column_mm": "int", "figures.font_pt": "int",
    "figures.dpi": "int", "figures.formats": "list:str",
    "review.type": "enum:review_type?", "review.criteria": "list:str",
    "evidence": "list", "evidence.item.field": "str", "evidence.item.source": "str",
    "evidence.item.quote": "str",
    "conflicts": "list", "conflicts.item.field": "str", "conflicts.item.chosen": "any",
    "conflicts.item.reason": "str", "conflicts.item.statements": "list",
    "conflicts.item.statements.item.source": "str", "conflicts.item.statements.item.quote": "str",
    "provenance.run": "str", "provenance.facts_sha256": "str", "provenance.sources": "list",
    "provenance.sources.item.id": "str", "provenance.sources.item.url": "str",
    "provenance.sources.item.retrieved_at": "str", "provenance.sources.item.retrieved_by": "enum:retrieved_by",
    "provenance.sources.item.text_sha256": "str",
    "provenance.max_age_days": "int", "provenance.adopted_at": "str",
    "provenance.adopted_by": "enum:adopted_by", "provenance.tool": "str", "provenance.format": "int",
}
_BLOCK_LIMITS = "article_types.item.limits"
_ABSENT = object()


class ProfileError(ValueError):
    """A file or schema cannot be read or lacks a required part; nothing is validated."""


@dataclass(frozen=True)
class Problem:
    rule: str
    path: str
    message: str
    level: str = "error"

    def to_dict(self) -> dict:
        return {"rule": self.rule, "path": self.path, "message": self.message, "level": self.level}

    def __str__(self) -> str:
        return f"{self.level} {self.rule} {self.path}: {self.message}"


@dataclass
class SourceSet:
    """The `ok` rows of a run's sources.json, their texts, and the ids whose text hash differs."""
    rows: dict = field(default_factory=dict)
    texts: dict = field(default_factory=dict)
    mismatched: set = field(default_factory=set)


@dataclass(frozen=True)
class _Leaf:
    path: str
    key: str
    value: object
    parent: str | None

    @property
    def exact_only(self) -> bool:
        return not isinstance(self.value, (str, list))


# --- reading ----------------------------------------------------------------------------------

def _read_regular(path: Path, label: str, limit: int) -> bytes:
    if path.is_symlink():
        raise ProfileError(f"{label}: is a symlink")
    try:
        info = path.stat()
    except OSError:
        raise ProfileError(f"{label}: not found or unreadable") from None
    if not stat.S_ISREG(info.st_mode):
        raise ProfileError(f"{label}: not a regular file")
    if info.st_size > limit:
        raise ProfileError(f"{label}: larger than {limit} bytes")
    try:
        return path.read_bytes()
    except OSError:
        raise ProfileError(f"{label}: unreadable") from None


def _yaml(raw: bytes, label: str) -> object:
    try:
        return yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError, RecursionError, ValueError):
        raise ProfileError(f"{label}: not valid YAML") from None


def load_schema(root) -> dict:
    raw = _read_regular(Path(root) / SCHEMA, SCHEMA, MAX_PROFILE_BYTES)
    data = _yaml(raw, SCHEMA)
    needed = ("format_version", "top_keys", "facts_blocks", "sourced_blocks", "blocks",
              "all_optional", "enums", "ranges", "patterns", "quote")
    if not isinstance(data, dict) or any(key not in data for key in needed):
        raise ProfileError(f"{SCHEMA}: missing one of {', '.join(needed)}")
    return data


def required_top_keys(root) -> tuple:
    return tuple(load_schema(root)["top_keys"]["required"])


def optional_top_keys(root) -> tuple:
    return tuple(load_schema(root)["top_keys"]["optional"])


def figure_ranges(root) -> dict:
    """figure-style.yml as {key: (min, max)} for the numbers and {formats: allowed} (M-D2 5.7)."""
    raw = _read_regular(Path(root) / FIGURE_STYLE, FIGURE_STYLE, MAX_PROFILE_BYTES)
    style = _yaml(raw, FIGURE_STYLE)
    if not isinstance(style, dict) or not isinstance(style.get("figures"), dict):
        raise ProfileError(f"{FIGURE_STYLE}: no figures mapping")
    out = {}
    for key, spec in style["figures"].items():
        if "allowed" in spec:
            out[key] = tuple(spec["allowed"])
        else:
            out[key] = (spec["min"], spec["max"])
    return out


def load_profile(path) -> dict:
    """A profile file: regular, not a symlink, at most 1 MB, a YAML mapping."""
    path = Path(path)
    data = _yaml(_read_regular(path, path.name, MAX_PROFILE_BYTES), path.name)
    if not isinstance(data, dict):
        raise ProfileError(f"{path.name}: the profile must be a mapping")
    return data


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_sources(run_dir) -> SourceSet | None:
    """The ok rows and their verified texts of a run, or None when the run has no sources.json."""
    journal = Path(run_dir) / "journal"
    path = journal / "sources.json"
    if path.is_symlink() or not path.is_file():
        return None
    data = _yaml(_read_regular(path, "sources.json", MAX_SOURCES_BYTES), "sources.json")
    rows = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ProfileError("sources.json: no sources list")
    found = SourceSet()
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "ok":
            continue
        sid = row.get("id")
        if not isinstance(sid, str) or not _SOURCE_ID.match(sid):
            continue
        found.rows[sid] = row
        text_path = journal / "sources" / f"{sid}.txt"
        try:
            raw = _read_regular(text_path, f"source {sid}", MAX_SOURCES_BYTES)
        except ProfileError:
            found.mismatched.add(sid)
            continue
        if hashlib.sha256(raw).hexdigest() != row.get("text_sha256"):
            found.mismatched.add(sid)
            continue
        try:
            found.texts[sid] = raw.decode("utf-8")
        except UnicodeDecodeError:
            found.mismatched.add(sid)
    return found


# --- paths --------------------------------------------------------------------------------------

def _join(path: str, key) -> str:
    return f"{path}.{key}" if path else str(key)


def _norm(path: str) -> str:
    return re.sub(r"\[\d+\]", ".item", path)


def get_path(data, path: str):
    """The value at a dotted path with list indexes (`article_types[0].limits`); KeyError if absent."""
    cur = data
    for name, index in _TOKEN.findall(path):
        if name:
            if not isinstance(cur, dict) or name not in cur:
                raise KeyError(path)
            cur = cur[name]
        else:
            if not isinstance(cur, list) or int(index) >= len(cur):
                raise KeyError(path)
            cur = cur[int(index)]
    return cur


# --- validation ---------------------------------------------------------------------------------

class _Validator:
    def __init__(self, data, root, run_dir, today):
        self.data = data
        self.root = Path(root)
        self.schema = load_schema(root)
        self.blocks = self.schema["blocks"]
        self.enums = self.schema["enums"]
        self.all_optional = set(self.schema["all_optional"])
        self.problems: list[Problem] = []
        # Limit values are integers wherever the schema lists them; the table is otherwise TYPES.
        self.types = dict(TYPES)
        for name in self.blocks[_BLOCK_LIMITS]:
            self.types[f"{_BLOCK_LIMITS}.{name}"] = "int?"
        self.today = today
        self.run_dir = run_dir
        self.sources = load_sources(run_dir) if run_dir is not None else None
        self.min_chars = self.schema["quote"]["min_chars"]
        self.limit_min = self.schema["ranges"]["limits"]["min"]
        self.leaves: list[_Leaf] = []

    def add(self, rule, path, message, level="error"):
        self.problems.append(Problem(rule, path, message, level))

    # type and shape ----------------------------------------------------------------------------

    def check_type(self, spec: str, value, path: str) -> bool:
        if value is None:
            if spec.endswith("?"):
                return True
            self.add("type", path, "a value is required here (null is not allowed)")
            return False
        base = spec.rstrip("?")
        if base == "any":
            return True
        if base == "map":
            if isinstance(value, dict):
                return True
            self.add("type", path, "expected a mapping")
            return False
        if base == "list":
            if isinstance(value, list):
                return True
            self.add("type", path, "expected a list")
            return False
        if base.startswith("list:"):
            if not isinstance(value, list):
                self.add("type", path, "expected a list")
                return False
            ok = True
            for index, item in enumerate(value):
                ok = self.check_scalar(base[5:], item, f"{path}[{index}]") and ok
            return ok
        return self.check_scalar(base, value, path)

    def check_scalar(self, base: str, value, path: str) -> bool:
        if base.startswith("enum:"):
            allowed = self.enums[base[5:]]
            if not isinstance(value, str):
                self.add("type", path, "expected a text value from the list")
                return False
            if value not in allowed:
                self.add("enum", path, f"{value!r} is not one of {', '.join(map(str, allowed))}")
                return False
            return True
        checks = {
            "str": lambda v: isinstance(v, str),
            "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "num": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "bool": lambda v: isinstance(v, bool),
        }
        if checks[base](value):
            return True
        self.add("type", path, f"expected {base}")
        return False

    def mapping_keys(self, value: dict, path: str, block: str):
        allowed = self.blocks[block]
        for key in value:
            if key not in allowed:
                self.add("unknown-key", _join(path, key), f"{key!r} is not a key of this block")
        if block not in self.all_optional:
            for key in allowed:
                if key not in value:
                    self.add("missing-key", _join(path, key), f"{key!r} is required")

    def walk(self, value, path: str, key: str):
        if key.startswith(BY_STUDY + "."):
            spec = "list:str"
        else:
            spec = self.types.get(key)
        if spec is not None and not self.check_type(spec, value, path):
            return
        if isinstance(value, dict):
            if key in self.blocks:
                self.mapping_keys(value, path, key)
            allowed = self.blocks.get(key)
            for child, child_value in value.items():
                if allowed is not None and child not in allowed:
                    continue
                self.walk(child_value, _join(path, child), _join(key, child))
        elif isinstance(value, list):
            item_key = f"{key}.item"
            for index, item in enumerate(value):
                if item_key in self.blocks and not isinstance(item, dict):
                    self.add("type", f"{path}[{index}]", "expected a mapping")
                    continue
                if isinstance(item, (dict, list)) or item_key in self.blocks:
                    self.walk(item, f"{path}[{index}]", item_key)
        elif key in self.blocks:
            self.add("type", path, "expected a mapping")

    def top(self):
        required = self.schema["top_keys"]["required"]
        optional = self.schema["top_keys"]["optional"]
        for key in self.data:
            if key not in required and key not in optional:
                self.add("unknown-key", str(key), f"{key!r} is not a top-level key of a profile")
        for key in required:
            if key not in self.data:
                self.add("missing-key", key, f"{key!r} is required")
        for key in self.data:
            if key in required or key in optional:
                self.walk(self.data[key], key, key)
        version = self.data.get("version")
        if isinstance(version, int) and not isinstance(version, bool) and version != 1:
            self.add("range", "version", "the profile version is 1")

    # article types, limits, figures, provenance ranges ------------------------------------------

    def article_types(self):
        types = self.data.get("article_types")
        if not isinstance(types, list) or not types:
            self.add("article-type", "article_types", "at least one article type is required")
            return
        pattern = re.compile(self.schema["patterns"]["article_type_id"])
        seen = set()
        for index, item in enumerate(types):
            if not isinstance(item, dict):
                continue
            ident = item.get("id")
            path = f"article_types[{index}].id"
            if not isinstance(ident, str) or not pattern.match(ident):
                self.add("article-type", path, "the id must be lower-case kebab-case")
            elif ident in seen:
                self.add("article-type", path, f"the id {ident!r} is used twice")
            else:
                seen.add(ident)
            limits = item.get("limits")
            if isinstance(limits, dict):
                for name, value in limits.items():
                    if isinstance(value, int) and not isinstance(value, bool) and value < self.limit_min:
                        self.add("range", f"article_types[{index}].limits.{name}",
                                 f"a limit is {self.limit_min} or more")

    def ranges(self):
        prov = self.data.get("provenance")
        age = prov.get("max_age_days") if isinstance(prov, dict) else None
        bounds = self.schema["ranges"]["max_age_days"]
        if isinstance(age, int) and not isinstance(age, bool) and not bounds["min"] <= age <= bounds["max"]:
            self.add("range", "provenance.max_age_days", f"must be {bounds['min']} to {bounds['max']}")

    def figures(self, root):
        block = self.data.get("figures")
        if not isinstance(block, dict):
            return
        ranges = figure_ranges(root)
        for key in ("single_column_mm", "double_column_mm", "font_pt", "dpi"):
            value = block.get(key)
            low, high = ranges[key]
            if isinstance(value, int) and not isinstance(value, bool) and not low <= value <= high:
                self.add("range", f"figures.{key}", f"must be {low} to {high}")
        formats = block.get("formats")
        if isinstance(formats, list):
            for value in formats:
                if value not in ranges["formats"]:
                    self.add("enum", "figures.formats", f"{value!r} is not one of {', '.join(ranges['formats'])}")

    # leaves, evidence ----------------------------------------------------------------------------

    def collect(self, node, path: str, key: str, parent: str | None):
        if node is None:
            return
        if isinstance(node, dict):
            free = key == BY_STUDY
            allowed = None if free else self.blocks.get(key)
            if not free and allowed is None:
                return
            for child, value in node.items():
                if allowed is not None and child not in allowed:
                    continue
                self.collect(value, _join(path, child), _join(key, child), path)
        elif isinstance(node, list):
            if node and all(not isinstance(item, (dict, list)) for item in node):
                self.leaves.append(_Leaf(path, key, node, parent))
                return
            for index, item in enumerate(node):
                if isinstance(item, dict):
                    self.collect(item, f"{path}[{index}]", f"{key}.item", path)
        else:
            self.leaves.append(_Leaf(path, key, node, parent))

    def covering(self, leaf: _Leaf, entries: list) -> list:
        out = []
        for entry in entries:
            field_path = entry.get("field")
            if field_path == leaf.path or (not leaf.exact_only and leaf.parent and field_path == leaf.parent):
                out.append(entry)
        return out

    def evidence_entries(self) -> list:
        entries = self.data.get("evidence")
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []

    def check_quote_entry(self, path: str, source, quote):
        if self.sources is None or not isinstance(source, str) or not isinstance(quote, str):
            return
        if source not in self.sources.rows:
            self.add("evidence-source", f"{path}.source", "the source is not an ok row of the run's sources.json")
            return
        if source in self.sources.mismatched:
            self.add("evidence-source", f"{path}.source", "the stored text does not match its recorded sha256")
            return
        problem = quotes.quote_problem(quote, self.sources.texts[source], self.min_chars)
        if problem:
            self.add("evidence-quote", f"{path}.quote", problem)

    def evidence(self):
        entries = self.evidence_entries()
        for index, entry in enumerate(entries):
            self.check_quote_entry(f"evidence[{index}]", entry.get("source"), entry.get("quote"))
        blocks = self.schema["sourced_blocks"]
        for block in blocks:
            if block in self.data:
                self.collect(self.data[block], block, block, None)
        for leaf in self.leaves:
            cover = self.covering(leaf, entries)
            if not cover:
                self.add("evidence-missing", leaf.path, "a value with no evidence entry (quote) for this path")
            self.check_numbers(leaf, cover)
            self.check_names(leaf, cover)

    def check_numbers(self, leaf: _Leaf, cover: list):
        value = leaf.value
        if isinstance(value, int) and not isinstance(value, bool):
            quoted = [e for e in cover if isinstance(e.get("quote"), str)]
            if quoted and not any(quotes.number_in_quote(value, e["quote"]) for e in quoted):
                self.add("evidence-number", leaf.path, f"the number {value} is not in its quote")

    def check_names(self, leaf: _Leaf, cover: list):
        quoted = [e["quote"] for e in cover if isinstance(e.get("quote"), str)]
        if leaf.path == "templates.latex_class" and isinstance(leaf.value, str) and leaf.value != "other":
            if quoted and not any(quotes.class_in_quote(leaf.value, q) for q in quoted):
                self.add("evidence-name", leaf.path, f"the LaTeX class {leaf.value!r} is not named in its quote")
        if leaf.key.startswith(BY_STUDY + "."):
            self.check_study_guidelines(leaf, leaf.key[len(BY_STUDY) + 1:], quoted)

    def check_study_guidelines(self, leaf: _Leaf, study_key: str, quoted: list):
        from scieflow.core.design import schema as design_schema
        if not isinstance(leaf.value, list):
            return
        index = {c.id: c for c in self._checklists()}
        studies = design_schema.study_types(self.root)
        if study_key not in studies:
            self.add("study-type-unknown", leaf.path, f"{study_key!r} is not a study type of schemas/design.yml")
        for position, guideline in enumerate(leaf.value):
            if guideline not in index:
                self.add("guideline-unknown", f"{leaf.path}[{position}]",
                         f"{guideline!r} is not in config/reporting/index.yml")
                continue
            word = quotes.name_first_word(index[guideline].name)
            if quoted and not any(quotes.name_in_quote(word, q) for q in quoted):
                self.add("evidence-name", leaf.path,
                         f"the guideline name {word.upper()!r} of {guideline} is not in its quote")

    def _checklists(self):
        from scieflow.core.design import reporting
        try:
            return reporting.registry(self.root)
        except reporting.ReportingError as exc:
            raise ProfileError(f"config/reporting unreadable: {exc}") from None

    def conflicts(self):
        items = self.data.get("conflicts")
        if not isinstance(items, list):
            return
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            path = f"conflicts[{index}]"
            field_path = item.get("field")
            try:
                current = get_path(self.data, field_path) if isinstance(field_path, str) else _ABSENT
            except KeyError:
                current = _ABSENT
            if current is _ABSENT:
                self.add("conflict", path, "the field is not in the profile")
            elif item.get("chosen") != current:
                self.add("conflict", path, "the chosen value is not the value of the field")
            statements = item.get("statements")
            if not isinstance(statements, list) or len(statements) < 2:
                self.add("conflict", path, "a conflict names at least two statements")
            for pos, statement in enumerate(statements if isinstance(statements, list) else []):
                if isinstance(statement, dict):
                    self.check_quote_entry(f"{path}.statements[{pos}]", statement.get("source"),
                                           statement.get("quote"))

    def sources_state(self):
        if self.sources is None:
            self.add("sources-unavailable", "provenance.run",
                     "the run's sources are not on disk: quotes were not re-checked", "warning")

    def stale(self):
        prov = self.data.get("provenance")
        if not isinstance(prov, dict) or not isinstance(prov.get("sources"), list):
            return
        age_limit = prov.get("max_age_days")
        if not isinstance(age_limit, int) or isinstance(age_limit, bool):
            return
        oldest = None
        for row in prov["sources"]:
            if not isinstance(row, dict) or not isinstance(row.get("retrieved_at"), str):
                continue
            try:
                when = dt.date.fromisoformat(row["retrieved_at"][:10])
            except ValueError:
                self.add("type", "provenance.sources", "retrieved_at is not a date")
                continue
            oldest = when if oldest is None or when < oldest else oldest
        if oldest is not None and (self.today - oldest).days > age_limit:
            self.add("stale", "provenance.sources",
                     f"the oldest source is older than {age_limit} days (retrieved {oldest.isoformat()})",
                     "warning")


def validate(data, *, root, run_dir=None, today: dt.date | None = None) -> list[Problem]:
    """Every problem of a profile against schemas/journal-profile.yml (M-F 5.5).

    `run_dir` is the run whose journal/ folder holds the sources the quotes must match; None means
    the sources are not available (a `sources-unavailable` warning, and no quote is re-checked
    against a page). `today` defaults to the UTC date.
    """
    if not isinstance(data, dict):
        return [Problem("type", "", "the profile must be a mapping")]
    today = today or dt.datetime.now(dt.timezone.utc).date()
    v = _Validator(data, root, run_dir, today)
    v.top()
    v.article_types()
    v.ranges()
    v.figures(root)
    v.sources_state()
    v.evidence()
    v.conflicts()
    v.stale()
    return v.problems
