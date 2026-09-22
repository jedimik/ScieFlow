"""Normalized paper records shared by every search script.

The eight core fields are strict — every source fills the same shape, and an
unknown core field is a bug. Source-specific enrichment (open-access status,
authors, later citation-context tallies) goes into an optional, namespaced
`signals` block: `{"openalex": {...}, "scite": {...}}`. Records without
signals keep exactly the old shape.
"""

import json
import sys

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


def emit(records: list) -> None:
    json.dump(records, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
