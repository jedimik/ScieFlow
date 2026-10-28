"""Normalized paper records shared by every search script.

The eight core fields are strict — every source fills the same shape, and an
unknown core field is a bug. Source-specific enrichment (open-access status,
authors, later citation-context tallies) goes into an optional, namespaced
`signals` block: `{"openalex": {...}, "scite": {...}}`. Records without
signals keep exactly the old shape.
"""

import json
import sys

from scieflow.core import sanitize as _sanitize

FIELDS = ("doi", "title", "year", "venue", "cited_by", "abstract", "url", "source")


def record(signals: dict | None = None, **kw) -> dict:
    unknown = set(kw) - set(FIELDS)
    if unknown:
        raise TypeError(f"unknown paper fields: {unknown}")
    out = {f: kw.get(f) for f in FIELDS}
    cleaned = {ns: vals for ns, vals in (signals or {}).items() if vals}
    if cleaned:
        out["signals"] = cleaned
    return out


def emit(records: list, *, sanitize: bool = True) -> None:
    """Print the records as JSON. Their string values are sanitised on the way out (M-C1 5.9):
    the caller's records, and every verdict computed on them, keep the raw values."""
    if sanitize:
        records = _sanitize.sanitize_json_strings(records)
    json.dump(records, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
