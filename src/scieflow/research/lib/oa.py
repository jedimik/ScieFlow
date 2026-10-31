"""`scieflow research oa`: is there a free full text for these DOIs, and where? (M-C2 t7, plan 5.7)

usage: scieflow research oa DOI [DOI ...] [--workspace SLUG] [--network MODE]

Unpaywall `v2/<doi>` answers each DOI with a verdict: full-text (a location is reported), abstract-only
(the DOI is known, no location) or unavailable (Unpaywall answers 404, a clean negative). A source
failure is an error, never a verdict (researcher's rule). The `oa_url` is printed as data; it is never
requested: the only host contacted is api.unpaywall.org. The contact address is sent on the wire only,
never kept in a key, a ledger row, a cache entry, a snapshot, an event or the output.

Exit codes follow the Gateway (registry.call): 0 answered; 3 refused (Unpaywall off, or enabled without
contact_email); 6 a snapshot miss in replay or offline; 1 a usage error (not a DOI, more than 50) or a
failure. Nothing is sent for a usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import quote

from scieflow._vendor.researcher_core.model import is_valid_doi, normalize_doi
from scieflow.core import sanitize
from scieflow.integrations.scite.policy import PolicyError
from scieflow.research.lib import records
from scieflow.research.lib.identity import _doi_key
from scieflow.research.search import registry

PROVIDER = "unpaywall"
OP = "v2-doi"
API = "https://api.unpaywall.org"
ABSENT = (404,)            # "DOI unknown to Unpaywall": a clean negative
MAX_DOIS = 50


def verdict(doi: str, body) -> dict:
    """The verdict row of one DOI from its Unpaywall answer (None: the DOI is unknown)."""
    if body is None or body.get("error"):
        return {"doi": doi, "verdict": "unavailable"}
    location = records.unpaywall_location(body.get("best_oa_location"))
    if location is None:
        return {"doi": doi, "verdict": "abstract-only"}
    return {"doi": doi, "verdict": "full-text", **location}


def run_oa(gw, dois: list) -> dict:
    """The oa payload for normalised DOIs. Raises PolicyError (exit 3) before any request when
    Unpaywall cannot be used now."""
    reason = gw.usable(PROVIDER)
    if reason is not None:
        raise PolicyError(reason)
    contact = gw.policy.contact(PROVIDER)
    rows: list = []
    for doi in dois:
        body = gw.get_json(PROVIDER, OP, f"{API}/v2/{quote(doi, safe='/')}", absent=ABSENT,
                           contact={"email": contact} if contact else None,
                           validate=registry.expect_object)
        rows.append(verdict(doi, body))
    return {
        "kernel": 1,
        "sources": [{"source": PROVIDER, "status": "ok", "count": len(rows)}],
        "results": rows,
    }


def _checked_dois(values) -> list:
    if not values:
        _refuse("give at least one DOI")
    if len(values) > MAX_DOIS:
        _refuse(f"at most {MAX_DOIS} DOIs; got {len(values)}")
    out: list = []
    for value in values:
        doi = _doi_key(value)
        if doi is None or not is_valid_doi(normalize_doi(doi)):
            _refuse("a DOI is not a plain DOI")       # nothing sent
        if doi not in out:
            out.append(doi)
    return out


def _refuse(message: str) -> None:
    print(f"refused: {message}", file=sys.stderr)
    sys.exit(1)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research oa")
    ap.add_argument("dois", nargs="+", metavar="DOI", help="One to fifty DOIs.")
    registry.add_workspace_option(ap)
    registry.add_network_option(ap)
    args = ap.parse_args(argv)
    dois = _checked_dois(args.dois)
    payload = registry.call(args.workspace, lambda gw: run_oa(gw, dois),
                            network_mode=args.network)
    sys.stdout.write(json.dumps(sanitize.sanitize_json_strings(payload), indent=2,
                                ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
