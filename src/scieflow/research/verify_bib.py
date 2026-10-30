"""`scieflow research verify-bib` (M-C2 t4, plan 5.4): is every bibliography entry the work it claims?

usage: scieflow research verify-bib SLUG [--bib PATH] [--sources openalex,crossref,datacite]
                                        [--round N] [--network MODE] [--json]

Reads the run's manuscript/references.bib (or --bib, inside the run, no symbolic link on the path),
parses it with the vendored BibTeX reader, and checks each entry against the named identity sources
through the provider Gateway (research/lib/identity.py). Exit codes: 0 nothing refusal-grade; 4 at
least one refusal-grade verdict (mismatch or unresolvable; inconclusive never is); 3 fewer than two
usable sources (nothing sent); 6 a replay/offline miss (nothing sent); 1 failed or bad input.

With --round N (M-C2 t5, coordinator only; refused inside a dispatch) the check is recorded for the
review round: review/round-N/identity.json, one review.identity_checked event and one human-only
review-blocker gate per refusal-grade reference (core/review/identity_gates.py). Without --round
nothing is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

from scieflow._vendor.researcher_core import bib as _bib
from scieflow._vendor.researcher_core import verify
from scieflow.core import sanitize as _sanitize
from scieflow.core.review import claims
from scieflow.core.review import identity_gates
from scieflow.integrations.scite.mcp import DispatchRefused, refuse_in_dispatch
from scieflow.research.lib import identity
from scieflow.research.search import registry

MAX_ENTRIES = 500
DEFAULT_SOURCES = ("openalex", "crossref", "datacite")
EXIT_FAILED = 1
EXIT_SOURCES = 3
EXIT_REFUSAL = 4


def _fail(code: int, message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(code)


def _run_relative(ws: Path, given: str | None) -> Path:
    """The bibliography path, inside the run, with no symbolic link on the way (or exit 1)."""
    ws_abs = Path(os.path.abspath(ws))
    if given is None:
        path = ws_abs / claims.MANUSCRIPT_DIR / claims.BIB_NAME
    else:
        candidate = Path(given)
        path = Path(os.path.abspath(candidate if candidate.is_absolute() else ws_abs / candidate))
    try:
        rel = path.relative_to(ws_abs)
    except ValueError:
        _fail(EXIT_FAILED, "error: the bibliography must be inside the run directory")
    node = ws_abs
    for part in rel.parts:
        node = node / part
        if node.is_symlink():
            _fail(EXIT_FAILED, "error: the bibliography path must not go through a symbolic link")
    if not path.is_file():
        _fail(EXIT_FAILED, f"error: no bibliography at {rel}")
    return path


def _parse_sources(text: str) -> list[str]:
    names = [s.strip() for s in text.split(",") if s.strip()]
    if not names:
        _fail(EXIT_FAILED, "error: --sources names at least one source")
    unknown = [n for n in names if n not in identity.PROVIDER]
    if unknown:
        _fail(EXIT_FAILED, f"error: unknown identity source {unknown[0]!r} "
                           f"(one of {', '.join(identity.SOURCES)})")
    return list(dict.fromkeys(names))


def _claims_of(text: str) -> list[verify.ReferenceClaim]:
    entries = _bib.parse_bib(text).entries
    if len(entries) > MAX_ENTRIES:
        _fail(EXIT_FAILED, f"error: more than {MAX_ENTRIES} entries in the bibliography")
    return [verify.ReferenceClaim.from_record(_bib.entry_to_record(e), key=e.key)
            for e in entries]


def _verify(gw, slug: str, path: Path, rel: str, names: list[str], as_json: bool,
            target=None) -> int:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail(EXIT_FAILED, "error: the bibliography is not UTF-8")
    claim_list = _claims_of(text)

    statuses, usable = [], []
    for name in names:
        provider = identity.PROVIDER[name]
        reason = gw.usable(provider)
        if reason is None:
            statuses.append({"source": name, "status": "ok"})
            usable.append(name)
        else:
            status = "refused" if gw.policy.allows(provider) else "off"
            statuses.append({"source": name, "status": status, "reason": reason})
    if len(usable) < 2:
        _fail(EXIT_SOURCES, "refused: verify-bib needs at least two usable sources "
                            f"(usable: {', '.join(usable) or 'none'})")

    report = identity.check_bib(claim_list, gw, usable, input_path=rel, run_id=slug)
    doc = {"version": 1, "bib": rel, "bib_sha256": hashlib.sha256(raw).hexdigest(),
           "sources": statuses, "report": report}
    if target is not None:
        project, ws, round_ = target
        try:
            identity_gates.record(project, ws, round_, doc)
        except (identity_gates.IdentityError, OSError, ValueError) as exc:
            _fail(EXIT_FAILED, f"error: {exc}")
    clean = _sanitize.sanitize_json_strings(doc)
    if as_json:
        sys.stdout.write(json.dumps(clean, indent=2, ensure_ascii=False) + "\n")
    else:
        for entry in clean["report"]["entries"]:
            print(f"{entry['key']}  {entry['verdict']}  {entry.get('reason', '')}")
    refused = any(verify.is_refusal_grade(e["verdict"]) for e in report["entries"])
    return EXIT_REFUSAL if refused else 0


def _round_target(project, ws: Path, slug: str, value: int | None):
    """(project, run, round) to record the check for, or None without --round.

    Refused (exit 1) inside a dispatch, for a round below 1, and for a round with no review
    directory: the coordinator records a round's check into a round that exists.
    """
    if value is None:
        return None
    try:
        refuse_in_dispatch()
    except DispatchRefused:
        _fail(EXIT_FAILED, "error: --round is coordinator-only: refused inside a dispatch")
    if value < 1:
        _fail(EXIT_FAILED, "error: --round must be 1 or more")
    if not (ws / "review" / f"round-{value}").is_dir():
        _fail(EXIT_FAILED, f"error: no review round {value} in workspace/{slug} "
                           f"(review/round-{value}/ is missing)")
    return project, ws, value


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research verify-bib")
    ap.add_argument("slug")
    ap.add_argument("--bib", metavar="PATH", default=None,
                    help="the bibliography, inside the run (default: manuscript/references.bib)")
    ap.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                    help="identity sources to ask: openalex, crossref, datacite (default: all)")
    ap.add_argument("--round", dest="round_", type=int, default=None, metavar="N",
                    help="record the check for review round N (coordinator only)")
    ap.add_argument("--json", dest="as_json", action="store_true", help="print the JSON report")
    registry.add_network_option(ap)
    args = ap.parse_args(argv)

    names = _parse_sources(args.sources)
    from scieflow.core.project import Project

    project = Project.discover()
    ws = project.run_dir(args.slug)
    if not ws.is_dir():
        _fail(EXIT_FAILED, f"error: no run workspace/{args.slug} in this project")
    target = _round_target(project, ws, args.slug, args.round_)
    path = _run_relative(ws, args.bib)
    rel = path.relative_to(os.path.abspath(ws)).as_posix()
    code = registry.call(args.slug,
                         lambda gw: _verify(gw, args.slug, path, rel, names, args.as_json, target),
                         network_mode=args.network)
    sys.exit(code)


if __name__ == "__main__":
    main()
