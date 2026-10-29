"""Reporting checklists (M-D1 plan section 5.3): the files under config/reporting/ and their validator.

Each checklist is a published reporting guideline transcribed as data. The shipped files are
skeletons written offline, so every one is `verification.status: unverified` until a human checks
it against the official checklist named in its `source`. Loading is fail closed: any problem
makes `registry` raise, and `validate_all` lists every problem with its rule.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from scieflow.core.design import schema as design_schema

INDEX = "config/reporting/index.yml"
DIRECTORY = "config/reporting"
SCHEMA = "schemas/reporting-checklist.yml"

_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")


class ReportingError(ValueError):
    """A checklist file, the index or the schema cannot be loaded (fail closed)."""


@dataclass(frozen=True)
class Problem:
    path: str
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: [{self.rule}] {self.message}"


@dataclass(frozen=True)
class Checklist:
    id: str
    name: str
    version_label: str
    applies_to: tuple
    granularity: str
    source: dict
    verification: dict
    items: tuple


class _Bad(Exception):
    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def _schema(root: Path) -> dict:
    try:
        raw = yaml.safe_load((root / SCHEMA).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ReportingError(f"{SCHEMA}: unreadable ({exc.__class__.__name__})") from exc
    needed = ("format_version", "file_keys", "source_keys", "verification_keys", "item_keys",
              "topic_max_length", "doi_pattern", "enums")
    if not isinstance(raw, dict) or any(key not in raw for key in needed):
        raise ReportingError(f"{SCHEMA}: missing one of {', '.join(needed)}")
    return raw


def _load_mapping(root: Path, rel: str, unreadable: str, missing: str) -> dict:
    path = root / rel
    if path.is_symlink():
        raise _Bad("symlink", f"{rel} is a symlink; checklist files must be regular files")
    if not path.is_file():
        raise _Bad(missing, f"{rel} does not exist")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise _Bad(unreadable, f"{rel} is unreadable ({exc.__class__.__name__})") from None
    if not isinstance(data, dict):
        raise _Bad(unreadable, f"{rel} is not a mapping")
    return data


def _text(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _check_keys(data: dict, required, optional, rel: str, problems: list, where: str = "") -> None:
    for key in sorted(set(required) - set(data), key=str):
        problems.append(Problem(rel, "missing-key", f"{where}{key} is required"))
    for key in sorted(set(data) - set(required) - set(optional), key=str):
        problems.append(Problem(rel, "unknown-key", f"{where}{key} is not a known key"))


def _check_item(rel: str, position: int, item, schema: dict, seen: set, problems: list) -> None:
    where = f"items[{position}]"
    if not isinstance(item, dict):
        problems.append(Problem(rel, "field", f"{where} is not a mapping"))
        return
    keys = schema["item_keys"]
    _check_keys(item, keys["required"], keys["optional"], rel, problems, f"{where}.")
    item_id = item.get("id")
    if not _text(item_id):
        problems.append(Problem(rel, "field", f"{where}.id must be a non-empty string"))
    elif item_id in seen:
        problems.append(Problem(rel, "duplicate-item-id", f"{where}.id {item_id!r} repeats"))
    else:
        seen.add(item_id)
    topic = item.get("topic")
    if not _text(topic) or len(topic) > schema["topic_max_length"]:
        problems.append(Problem(rel, "topic",
                                f"{where}.topic must be 1 to {schema['topic_max_length']} characters"))
    enums = schema["enums"]
    sections = item.get("sections")
    if not (isinstance(sections, list) and sections
            and all(s in enums["sections"] for s in sections)):
        problems.append(Problem(rel, "enum", f"{where}.sections must be a non-empty list of {enums['sections']}"))
    if item.get("evidence") not in enums["evidence"]:
        problems.append(Problem(rel, "enum", f"{where}.evidence must be one of {enums['evidence']}"))
    if "group" in item and item["group"] not in enums["item_groups"]:
        problems.append(Problem(rel, "enum", f"{where}.group must be one of {enums['item_groups']}"))


def _check_file(gid: str, rel: str, data: dict, schema: dict, problems: list, study_types: tuple):
    """Append every problem of one checklist file; return its Checklist when it has none."""
    before = len(problems)
    files, enums = schema["file_keys"], schema["enums"]
    _check_keys(data, files["required"], files["optional"], rel, problems)
    if data.get("id") != gid:
        problems.append(Problem(rel, "id-mismatch", f"id {data.get('id')!r} must equal the file name {gid!r}"))
    if data.get("version") != schema["format_version"]:
        problems.append(Problem(rel, "format-version", f"version must be {schema['format_version']}"))
    for key in ("name", "version_label"):
        if not _text(data.get(key)):
            problems.append(Problem(rel, "field", f"{key} must be a non-empty string"))
    applies = data.get("applies_to")
    if not (isinstance(applies, list) and applies
            and all(a in study_types for a in applies)):
        problems.append(Problem(rel, "applies-to", f"applies_to must be a non-empty list of {study_types}"))
    if data.get("granularity") not in enums["granularity"]:
        problems.append(Problem(rel, "enum", f"granularity must be one of {enums['granularity']}"))
    source = data.get("source")
    if not isinstance(source, dict):
        problems.append(Problem(rel, "field", "source must be a mapping"))
    else:
        _check_keys(source, schema["source_keys"], (), rel, problems, "source.")
        if not _text(source.get("citation")):
            problems.append(Problem(rel, "field", "source.citation must be a non-empty string"))
        doi = source.get("doi")
        if doi is not None and not (isinstance(doi, str) and re.match(schema["doi_pattern"], doi)):
            problems.append(Problem(rel, "doi", "source.doi must be null or a DOI such as 10.1136/bmj.c332"))
    verification = data.get("verification")
    if not isinstance(verification, dict):
        problems.append(Problem(rel, "field", "verification must be a mapping"))
    else:
        _check_keys(verification, schema["verification_keys"], (), rel, problems, "verification.")
        status = verification.get("status")
        if status not in enums["verification_status"]:
            problems.append(Problem(rel, "enum", f"verification.status must be one of {enums['verification_status']}"))
        elif status == "verified":
            when = verification.get("verified_at")
            if not (_text(verification.get("verified_by"))
                    and (_text(when) or isinstance(when, datetime.date))):
                problems.append(Problem(rel, "verification",
                                        "status verified needs verified_by and verified_at"))
        if not isinstance(verification.get("note"), str):
            problems.append(Problem(rel, "field", "verification.note must be a string"))
    items = data.get("items")
    if isinstance(items, list) and not items:
        problems.append(Problem(rel, "empty-items", "items must not be empty"))
    elif not isinstance(items, list):
        problems.append(Problem(rel, "field", "items must be a list"))
    else:
        seen: set = set()
        for position, item in enumerate(items):
            _check_item(rel, position, item, schema, seen, problems)
    if len(problems) != before:
        return None
    return Checklist(
        id=gid, name=data["name"], version_label=data["version_label"],
        applies_to=tuple(data["applies_to"]), granularity=data["granularity"],
        source=dict(source), verification=dict(verification),
        items=tuple(dict(item) for item in items),
    )


def _scan(root: Path) -> tuple[list[Checklist], list[Problem]]:
    schema = _schema(root)
    try:
        study_types = design_schema.study_types(root)
    except design_schema.SchemaError as exc:
        return [], [Problem(design_schema.SCHEMA, "schema-unreadable", str(exc))]
    try:
        index = _load_mapping(root, INDEX, unreadable="index-unreadable", missing="index-unreadable")
    except _Bad as bad:
        return [], [Problem(INDEX, bad.rule, bad.message)]
    problems: list[Problem] = []
    if set(index) != {"version", "guidelines"} or index.get("version") != 1:
        problems.append(Problem(INDEX, "index-unreadable", "index must hold exactly version 1 and guidelines"))
    listed = index.get("guidelines")
    if not (isinstance(listed, list) and listed
            and all(isinstance(g, str) and _ID.match(g) for g in listed)):
        problems.append(Problem(INDEX, "index-unreadable", "guidelines must be a non-empty list of ids"))
        return [], problems
    if len(set(listed)) != len(listed):
        problems.append(Problem(INDEX, "duplicate-id", "guidelines repeats an id"))
    checklists: list[Checklist] = []
    for gid in dict.fromkeys(listed):
        rel = f"{DIRECTORY}/{gid}.yml"
        try:
            data = _load_mapping(root, rel, unreadable="unreadable", missing="missing-file")
        except _Bad as bad:
            problems.append(Problem(rel, bad.rule, bad.message))
            continue
        checklist = _check_file(gid, rel, data, schema, problems, study_types)
        if checklist is not None:
            checklists.append(checklist)
    expected = {"index.yml", *(f"{gid}.yml" for gid in listed)}
    directory = root / DIRECTORY
    if directory.is_dir():
        for entry in sorted(p.name for p in directory.iterdir()):
            if entry not in expected:
                problems.append(Problem(f"{DIRECTORY}/{entry}", "orphan-file", "is not listed in index.yml"))
    return checklists, problems


def validate_all(root: Path) -> list[Problem]:
    """Every problem in the checklist tree under root (empty when the tree is valid)."""
    return _scan(Path(root))[1]


def registry(root: Path) -> list[Checklist]:
    """The checklists in index order; raises ReportingError when any file has a problem."""
    checklists, problems = _scan(Path(root))
    if problems:
        raise ReportingError("; ".join(str(p) for p in problems))
    return checklists


def get(root: Path, checklist_id: str) -> Checklist:
    for checklist in registry(root):
        if checklist.id == checklist_id:
            return checklist
    raise ReportingError(f"no reporting checklist named {checklist_id!r}")
