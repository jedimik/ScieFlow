"""Citation integrity for paper-draft manuscripts.

Checks that (a) every \\cite key resolves to a references.bib entry,
(b) every bib entry is actually cited, and (c) every bib DOI appears in
report/selected_dois.txt (the searched/imported DOI list — provenance).

usage: scieflow research check-citations --workspace workspace/<slug>
Prints OK (exit 0) or INVALID lines (exit 1), like scieflow research validate.
Missing DOI list: WARN + skip check (c), still OK.
"""

import argparse
import re
import sys
from pathlib import Path

# The whole \*cite* family — \cite, \citep, \citet, \citealp, \parencite,
# \autocite, \textcite … — with any optional [..] arguments. Same pattern as
# scripts/nblm/claims.py so the two checkers see the same citations.
CITE_RE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]*)\}")
BIBKEY_RE = re.compile(r"^@\w+\{([^,\s]+)\s*,", re.M)
# Unanchored on purpose: an anchored '^\\s*doi' misses the doi field of a
# single-line BibTeX entry, silently skipping check (c) for that source. The
# lookbehind keeps it from matching composite field names like 'eprintdoi'.
DOI_RE = re.compile(r"(?<![A-Za-z])doi\s*=\s*[{\"]?\s*([^,}\"\n]+)", re.I)
DOI_URL_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.I)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research check-citations")
    ap.add_argument("--workspace", type=Path, required=True)
    args = ap.parse_args(argv)

    manuscript = args.workspace / "manuscript"
    bib_path = manuscript / "references.bib"
    problems: list[str] = []

    tex_files = sorted(manuscript.rglob("*.tex"))
    if not tex_files:
        problems.append(f"INVALID: no .tex files under {manuscript}")
    if not bib_path.exists():
        problems.append(f"INVALID: missing {bib_path}")
    if problems:
        print("\n".join(problems))
        sys.exit(1)

    tex = "\n".join(p.read_text() for p in tex_files)
    cited = {
        key.strip()
        for m in CITE_RE.finditer(tex)
        for key in m.group(1).split(",")
        if key.strip()
    }
    bib = bib_path.read_text()
    bib_keys = set(BIBKEY_RE.findall(bib))

    for key in sorted(cited - bib_keys):
        problems.append(f"INVALID: \\cite{{{key}}} has no entry in references.bib")
    for key in sorted(bib_keys - cited):
        problems.append(f"INVALID: bib entry '{key}' is never cited in the manuscript")

    dois_file = args.workspace / "report" / "selected_dois.txt"
    if dois_file.exists():
        allowed = {
            line.strip().lower()
            for line in dois_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
        for raw in DOI_RE.findall(bib):
            doi = DOI_URL_RE.sub("", raw.strip().rstrip("},").strip())
            if doi.lower() not in allowed:
                problems.append(
                    f"INVALID: bib DOI {doi} not in {dois_file.name} (unverified source)"
                )
    else:
        print(f"WARN: {dois_file} missing — skipping DOI provenance check")

    if problems:
        print("\n".join(problems))
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
