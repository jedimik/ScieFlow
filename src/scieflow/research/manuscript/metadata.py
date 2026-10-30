"""manuscript/metadata.yml (M-F 5.8): schema, identifiers and the corresponding author.

`load` returns (metadata or None when the file is absent, problems). Problems name an author by
position and never carry an e-mail address or a manuscript text. Identifiers are checked with the
vendored validators (scieflow._vendor.researcher_core.contributors), never re-implemented here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from scieflow._vendor.researcher_core.contributors import (
    MetadataError,
    validate_credit_role,
    validate_orcid,
    validate_ror,
)
from scieflow.research.manuscript import tex

SCHEMA = "schemas/manuscript-metadata.yml"
FILE = "metadata.yml"
KEY_SHAPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
TEXT_DECLARATIONS = ("coi", "funding", "ethics", "data_availability", "code_availability", "ai_use")


@dataclass(frozen=True)
class Problem:
    rule: str
    path: str
    message: str
    level: str = "error"


def load_schema(root) -> dict:
    data = yaml.safe_load(tex.read_regular_text(Path(root) / "schemas", "manuscript-metadata.yml"))
    if not isinstance(data, dict) or "top_keys" not in data:
        raise ValueError(f"{SCHEMA}: no top_keys")
    return data


def _key_path(key) -> str:
    return str(key) if isinstance(key, str) and KEY_SHAPE.match(key) else "(key)"


def _text(value) -> bool:
    return value is None or isinstance(value, str)


def load(folder, root) -> tuple:
    """(metadata dict or None, [Problem]) for `folder`/metadata.yml; the file is optional."""
    folder = Path(folder)
    if not (folder / FILE).exists() and not (folder / FILE).is_symlink():
        return None, []
    try:
        raw = tex.read_regular_text(folder, FILE, limit=1_000_000)
        data = yaml.safe_load(raw)
    except tex.TexError as exc:
        return None, [Problem("metadata-file", FILE, exc.message)]
    except yaml.YAMLError:
        return None, [Problem("metadata-yaml", FILE, "not valid YAML")]
    if not isinstance(data, dict):
        return None, [Problem("metadata-schema", FILE, "the metadata must be a mapping")]

    schema = load_schema(root)
    problems = []
    allowed = set(schema["top_keys"]["required"]) | set(schema["top_keys"]["optional"])
    for key in data:
        if key not in allowed:
            problems.append(Problem("metadata-unknown-key", _key_path(key), "unknown key"))
    for key in schema["top_keys"]["required"]:
        if key not in data:
            problems.append(Problem("metadata-missing-key", key, "required key is missing"))
    if data.get("version") != 1 or isinstance(data.get("version"), bool):
        problems.append(Problem("metadata-schema", "version", "version must be 1"))
    for key in ("journal", "article_type", "title"):
        if key in data and not isinstance(data[key], str):
            problems.append(Problem("metadata-schema", key, f"{key} must be a text"))
    for key in ("keywords", "highlights"):
        if key in data and not (isinstance(data[key], list) and all(isinstance(x, str) for x in data[key])):
            problems.append(Problem("metadata-schema", key, f"{key} must be a list of texts"))
    declarations = data.get("declarations")
    if declarations is not None:
        if not isinstance(declarations, dict):
            problems.append(Problem("metadata-schema", "declarations", "declarations must be a mapping"))
        else:
            for key, value in declarations.items():
                if key not in TEXT_DECLARATIONS:
                    problems.append(Problem("metadata-unknown-key", f"declarations.{_key_path(key)}",
                                            "unknown key"))
                elif not _text(value):
                    problems.append(Problem("metadata-schema", f"declarations.{key}", "must be a text"))
    authors = data.get("authors")
    if not isinstance(authors, list):
        problems.append(Problem("metadata-schema", "authors", "authors must be a list"))
        authors = []
    corresponding = 0
    for index, author in enumerate(authors):
        path = f"authors[{index}]"
        if not isinstance(author, dict):
            problems.append(Problem("metadata-schema", path, "an author must be a mapping"))
            continue
        problems.extend(_author(author, path, schema))
        if author.get("corresponding") is True:
            corresponding += 1
    if corresponding > 1:
        problems.append(Problem("corresponding", "authors", "more than one corresponding author"))
    return _canonical(data), problems


def _author(author: dict, path: str, schema: dict) -> list:
    problems = []
    allowed = set(schema["author_keys"]["required"]) | set(schema["author_keys"]["optional"])
    for key in author:
        if key not in allowed:
            problems.append(Problem("metadata-unknown-key", f"{path}.{_key_path(key)}", "unknown key"))
    for key in schema["author_keys"]["required"]:
        if key not in author:
            problems.append(Problem("metadata-missing-key", f"{path}.{key}", "required key is missing"))
    if "corresponding" in author and not isinstance(author["corresponding"], bool):
        problems.append(Problem("metadata-schema", f"{path}.corresponding", "must be true or false"))
    if "email" in author and not isinstance(author["email"], str):
        problems.append(Problem("metadata-schema", f"{path}.email", "must be a text"))
    if isinstance(author.get("orcid"), str):
        try:
            validate_orcid(author["orcid"])
        except MetadataError:
            problems.append(Problem("orcid-invalid", f"{path}.orcid",
                                    "ORCID iD is malformed or fails its checksum"))
    elif "orcid" in author:
        problems.append(Problem("orcid-invalid", f"{path}.orcid", "ORCID iD must be a text"))
    credit = author.get("credit")
    if credit is not None and not isinstance(credit, list):
        problems.append(Problem("metadata-schema", f"{path}.credit", "credit must be a list"))
    for index, role in enumerate(credit if isinstance(credit, list) else []):
        try:
            validate_credit_role(str(role))
        except MetadataError:
            problems.append(Problem("credit-invalid", f"{path}.credit[{index}]",
                                    "CRediT role is not one of the 14 CRediT terms"))
    affiliations = author.get("affiliations")
    if affiliations is not None and not isinstance(affiliations, list):
        problems.append(Problem("metadata-schema", f"{path}.affiliations", "must be a list"))
    for index, item in enumerate(affiliations if isinstance(affiliations, list) else []):
        here = f"{path}.affiliations[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            problems.append(Problem("metadata-schema", here, "an affiliation needs a name"))
            continue
        for key in item:
            if key not in set(schema["affiliation_keys"]["required"]) | set(schema["affiliation_keys"]["optional"]):
                problems.append(Problem("metadata-unknown-key", f"{here}.{_key_path(key)}", "unknown key"))
        if item.get("ror") is not None:
            try:
                validate_ror(str(item["ror"]))
            except MetadataError:
                problems.append(Problem("ror-invalid", f"{here}.ror", "ROR ID is malformed"))
    return problems


def _canonical(data: dict) -> dict:
    """The metadata with valid identifiers in canonical form (invalid ones kept as written)."""
    out = dict(data)
    authors = []
    for author in data.get("authors") or []:
        if not isinstance(author, dict):
            authors.append(author)
            continue
        item = dict(author)
        if isinstance(item.get("orcid"), str):
            item["orcid"] = _try(validate_orcid, item["orcid"])
        if isinstance(item.get("credit"), list):
            item["credit"] = [_try(validate_credit_role, role) for role in item["credit"]]
        if isinstance(item.get("affiliations"), list):
            item["affiliations"] = [
                dict(a, ror=_try(validate_ror, a["ror"])) if isinstance(a, dict) and isinstance(a.get("ror"), str)
                else a for a in item["affiliations"]]
        authors.append(item)
    out["authors"] = authors
    return out


def _try(validator, value):
    try:
        return validator(str(value))
    except MetadataError:
        return value
