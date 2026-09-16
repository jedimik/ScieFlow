"""Normalized paper records shared by every search script."""

import json
import sys

FIELDS = ("doi", "title", "year", "venue", "cited_by", "abstract", "url", "source")


def record(**kw) -> dict:
    unknown = set(kw) - set(FIELDS)
    if unknown:
        raise TypeError(f"unknown paper fields: {unknown}")
    return {f: kw.get(f) for f in FIELDS}


def emit(records: list) -> None:
    json.dump(records, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
