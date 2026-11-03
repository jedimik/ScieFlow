"""Claim references (M-G 5.4): `[data:<id>]`, `[run:<ref>]` and `[doi:<doi>]` markers, and `\\cite` keys.

A marker in a `% source:` comment of a claim is `via: comment`; one in its text is `via: inline`; a cite key
whose bibliography entry declares a DOI is `via: cite` (its key is kept). Only the form of a reference is read
here; whether it resolves to evidence is decided by the resolver (M-G t2). A marker whose id is longer than
200 characters is refused and reported, never recorded; a malformed marker is ignored.
"""

from __future__ import annotations

import re

from scieflow.core.review.claims import safe_doi
from scieflow.research.citations import CITE_RE

MARKER_RE = re.compile(r"\[(data|run|doi):([^\]\s]+)\]")
MAX_ID = 200


def cite_keys(text: str) -> list[str]:
    """The `\\cite`-family keys of `text`, in order, each once."""
    keys: list[str] = []
    for match in CITE_RE.finditer(text):
        for key in match.group(1).split(","):
            key = key.strip()
            if key and key not in keys:
                keys.append(key)
    return keys


def parse_refs(comment: str, text: str, bib_dois: dict) -> tuple[list[dict], list[dict]]:
    """(references, problems) of one unit: markers in its comment, markers in its text, then cites.

    `bib_dois` maps a citation key to the DOI its bibliography entry declares (as written there).
    """
    found: list[dict] = []
    problems: list[dict] = []

    def add(ref: dict) -> None:
        if ref not in found:
            found.append(ref)

    for via, source in (("comment", comment), ("inline", text)):
        for match in MARKER_RE.finditer(source):
            kind, ident = match.group(1), match.group(2)
            if len(ident) > MAX_ID:
                problems.append({"rule": "ref-too-long",
                                 "message": f"a {kind} reference id is longer than {MAX_ID} characters"})
                continue
            if kind == "doi":
                doi = safe_doi(ident)
                if doi is None:
                    problems.append({"rule": "ref-unsafe-doi", "message": "a doi reference is not a safe DOI"})
                    continue
                add({"type": "doi", "id": doi, "via": via})
            else:
                add({"type": kind, "id": ident, "via": via})

    for key in cite_keys(text):
        declared = bib_dois.get(key)
        if not declared:
            continue
        doi = safe_doi(declared)
        if doi is None:
            problems.append({"rule": "ref-unsafe-doi", "message": "a cited entry declares an unsafe DOI"})
            continue
        add({"type": "doi", "id": doi, "key": key, "via": "cite"})
    return found, problems
