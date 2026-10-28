"""`scieflow research search all`: one query through every enabled literature source (M-C1 5.2).

The query is repaired and checked once (`registry.check_query`) before any source is asked. Sources
run one after another in the canonical order openalex, crossref, europepmc, arxiv, each through the
same Gateway, and each is isolated: a refusal, a switched-off source, a snapshot miss or a failure
ends that source only. The answers are merged by the vendored dedupe, ranked by the vendored rank,
and printed as one JSON object that names every source's status and never repeats the query.

Exit: 0 when a source answered; otherwise 6 when every non-answer is a snapshot miss; otherwise 3
when any source is refused or switched off; otherwise 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from scieflow._vendor.researcher_core.dedupe import dedupe
from scieflow._vendor.researcher_core.rank import rank_records
from scieflow.core import network
from scieflow.integrations.scite.policy import PolicyError
from scieflow.research.lib import http, kernel
from scieflow.research.search import arxiv, crossref, europepmc, openalex, registry

ORDER = ("openalex", "crossref", "europepmc", "arxiv")
MODULES = {"openalex": openalex, "crossref": crossref, "europepmc": europepmc, "arxiv": arxiv}
EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_MISS = 0, 1, 3, 6


def _status(name: str, status: str, count: int = 0, error: str | None = None) -> dict:
    out = {"source": name, "status": status, "count": count}
    if error is not None:
        out["error"] = error
    return out


def run_all(gw, query, *, sources=ORDER, limit: int = 25, from_year: int | None = None,
            top: int | None = None) -> dict:
    """The fan-out payload for one query. `sources` may list any subset in any order; the output
    always follows the canonical order."""
    checked = registry.check_query(query)            # refused here, before any source is asked
    chosen = [name for name in ORDER if name in sources]
    statuses: list[dict] = []
    answers: dict[str, list] = {}
    for name in chosen:
        provider = f"search-{name}"
        if not gw.policy.allows(provider):
            statuses.append(_status(name, "off", error=gw.policy.reason(provider) or "off"))
            continue
        try:
            found = MODULES[name].search(gw, checked, limit=limit, from_year=from_year)
        except network.NetworkMiss as exc:          # before PolicyError: NetworkMiss subclasses it
            statuses.append(_status(name, "offline-miss", error=str(exc)))
        except PolicyError as exc:
            statuses.append(_status(name, "refused", error=str(exc)))
        except http.HTTPFailure as exc:
            statuses.append(_status(name, "failed", error=exc.classification))
        except Exception:                           # one source's fault never sinks the others
            statuses.append(_status(name, "failed", error="ERROR"))
        else:
            answers[name] = found
            statuses.append(_status(name, "ok", count=len(found)))

    csl_records = []
    originals: dict[str, dict] = {}
    for name in chosen:
        for rank, raw in enumerate(answers.get(name, [])):
            csl = kernel.to_csl(raw, name, rank)
            csl_records.append(csl)
            originals[csl.id] = raw

    result = dedupe(csl_records)
    merged_into: dict[str, set] = defaultdict(set)
    for decision in result.decisions:
        merged_into[decision.kept_id].add(decision.duplicate_id)
    ranked = rank_records(result.records, limit=top)
    records = []
    for position, merged in enumerate(ranked):
        members = {merged.id} | merged_into.get(merged.id, set())
        group = [originals[c.id] for c in csl_records if c.id in members]
        records.append(kernel.from_csl(merged, group, rank=position))
    return {
        "kernel": 1,
        "sources": statuses,
        "records": records,
        "dedup": [decision.to_json_dict() for decision in result.decisions],
    }


def exit_code(statuses: list[dict]) -> int:
    """The exit rules of M-C1 5.2 over the per-source statuses."""
    states = [s["status"] for s in statuses]
    if "ok" in states:
        return EXIT_OK
    if states and all(state == "offline-miss" for state in states):
        return EXIT_MISS
    if any(state in ("refused", "off") for state in states):
        return EXIT_REFUSED
    return EXIT_FAILED


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search all")
    ap.add_argument("query")
    ap.add_argument("--sources", default=",".join(ORDER), metavar="LIST",
                    help="Comma-separated sources, any order (openalex, crossref, europepmc, "
                         "arxiv). Default: all four.")
    ap.add_argument("--limit", type=int, default=25, help="Records asked of each source.")
    ap.add_argument("--from-year", type=int)
    ap.add_argument("--top", type=int, default=None,
                    help="Keep only the N best records after ranking.")
    registry.add_workspace_option(ap)
    registry.add_network_option(ap)
    args = ap.parse_args(argv)
    names = {part.strip() for part in args.sources.split(",") if part.strip()}
    unknown = sorted(names - set(ORDER))
    if unknown or not names:
        ap.error(f"unknown source(s): {', '.join(unknown) or '(none given)'} "
                 f"(one of {', '.join(ORDER)})")
    chosen = tuple(name for name in ORDER if name in names)
    payload = registry.call(
        args.workspace,
        lambda gw: run_all(gw, args.query, sources=chosen, limit=args.limit,
                           from_year=args.from_year, top=args.top),
        network_mode=args.network,
    )
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    code = exit_code(payload["sources"])
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
