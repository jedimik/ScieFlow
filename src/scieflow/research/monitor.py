"""`scieflow research monitor`: a saved literature search whose re-runs report only the new papers.

usage: scieflow research monitor add  SLUG NAME --query Q [--query Q ...] [--source all|SOURCE] [--ts TS]
       scieflow research monitor run  SLUG NAME [--ts TS] [--network MODE]
       scieflow research monitor list SLUG [--json]
       scieflow research monitor show SLUG NAME [--json]

M-C2 t7 (plan 5.7). A monitor holds verbatim strategies (one query, one source or all sources). `run`
executes each strategy through the search fan-out (the same Gateway, leak check, cache, rate limit and
ledger as `search`), takes each paper's identity key and diffs it against the seen list with the
vendored `run_monitor` (researcher's monitor.py, unchanged). The state is saved only when every source
of the run answered (fan-out exit 0); a refusal, a miss or a failure leaves it as it was.

Files: workspace/SLUG/monitors/NAME.json (the vendored MonitorState) and NAME.last.json (the new papers
of the last run). Both are written atomically. `ts` is the caller's RFC 3339 time (--ts, or the UTC clock
of this command); the vendored code never reads a clock. Output and events carry counts, identifiers
(DOIs) and statuses only: never a query text, a reference title, a response body or an address.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

from scieflow._vendor.researcher_core.monitor import (
    MonitorError,
    MonitorState,
    SearchStrategy,
    run_monitor,
)
from scieflow._vendor.researcher_core.dedupe import identity_key
from scieflow.core import events, sanitize
from scieflow.research.lib import kernel
from scieflow.research.search import fanout, registry

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
ALL = "all"
SOURCES = (ALL,) + tuple(fanout.ORDER)
LIMIT = 25
EXIT_FAILED, EXIT_REFUSED = 1, 3


class MonitorRunFailed(Exception):
    """A monitor run that did not answer; `code` is the exit code the fan-out gave it."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code


def _refuse(message: str, code: int = EXIT_FAILED) -> NoReturn:
    print(f"refused: {message}" if code == EXIT_REFUSED else f"error: {message}", file=sys.stderr)
    sys.exit(code)


def _name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        _refuse("a monitor name is lower-case letters, digits and hyphens (1 to 48 characters)")
    return name


def _run_dir(slug: str) -> Path:
    from scieflow.core.project import Project

    ws = Project.discover().run_dir(slug)
    if not ws.is_dir():
        _refuse(f"no run workspace/{slug} in this project")
    return ws


def _paths(ws: Path, name: str) -> tuple[Path, Path]:
    folder = ws / "monitors"
    return folder / f"{name}.json", folder / f"{name}.last.json"


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _dump(data) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _load_state(ws: Path, name: str) -> MonitorState:
    state_file, _ = _paths(ws, name)
    if not state_file.is_file():
        _refuse(f"no monitor {name!r} in this run")
    return _read_state(state_file, name)


def _read_state(state_file: Path, name: str) -> MonitorState:
    try:
        return MonitorState.from_json_dict(json.loads(state_file.read_text(encoding="utf-8")))
    except (MonitorError, ValueError) as exc:
        _refuse(f"monitor {name!r} state is unreadable: {exc}")


def _save(ws: Path, state: MonitorState, new_records: list | None = None) -> None:
    """The new papers are written before the state that marks them seen: a failure between the two
    writes re-reports the papers on the next run (the safe direction), never loses them."""
    state_file, last_file = _paths(ws, state.monitor_id)
    if new_records is not None:
        _atomic(last_file, _dump({"monitor_id": state.monitor_id, "last_run": state.last_run,
                                  "new": new_records}))
    _atomic(state_file, _dump(state.to_json_dict()))


def _record_key(rec: dict) -> str:
    """The identity key of one paper record (researcher's identity_key; DOI first). A record with no
    usable identity gets no key and cannot be diffed, so it is not reported."""
    csl = kernel.to_csl(rec, "monitor", 0)
    key = identity_key(csl)
    return "" if key == csl.id else key


def _default_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _executor(gw, statuses: list, found: dict):
    """One strategy through the fan-out: its paper keys (diffed by run_monitor); the paper records
    are kept aside for the report and the news block."""

    def run(strategy: SearchStrategy) -> list:
        sources = None if strategy.source == ALL else [strategy.source]
        payload = fanout.run_all(gw, strategy.query, sources=sources, limit=LIMIT)
        statuses.extend(payload["sources"])
        keys = []
        for rec in payload["records"]:
            key = _record_key(rec)
            if key:
                found.setdefault(key, rec)
                keys.append(key)
        return keys

    return run


def _checked_source(source: str) -> str:
    if source not in SOURCES:
        _refuse(f"--source must be all or one of {', '.join(fanout.ORDER)}")
    return source


def _live_searches_are_fresh(gw) -> None:
    """A live re-run asks each search source afresh. The Gateway would otherwise answer from its
    search cache (cache_ttl_days.search, 7 days), so a re-run would report nothing new (M-C2 t7 review
    r1, finding 1). Of the network modes only offline reads the cache as its answer, so an offline run
    keeps it; replay and record never read the cache. The policy is copied into the cache, so this
    changes this gateway only."""
    from scieflow.core import network

    modes = {network.resolve(gw.ws, fanout.MODULES[s].PROVIDER, flag=gw.network).mode
             for s in fanout.ORDER}
    if "offline" not in modes:
        gw.cache.ttl_days["search"] = 0


def _add(slug: str, name: str, queries: list, source: str, ts: str) -> dict:
    _name(name)
    _checked_source(source)
    if not queries:
        _refuse("give at least one --query")
    ws = _run_dir(slug)
    state_file, _ = _paths(ws, name)
    if state_file.exists():
        _refuse(f"monitor {name!r} already exists in this run")

    def build(gw):
        for query in queries:
            registry.check_query(query)      # refused here, before anything is saved (exit 3)
        return tuple(SearchStrategy(source=source, query=q) for q in queries)

    strategies = registry.call(slug, build)
    state = MonitorState(monitor_id=name, strategies=strategies)
    _save(ws, state)
    events.emit(ws, "literature.monitor", action="added", name=name, new=0, ts=ts,
                strategies=len(strategies))
    return {"kernel": 1, "action": "added", "name": name, "strategies": len(strategies), "ts": ts}


def execute(slug: str, name: str, *, ts: str, network: str | None = None) -> dict:
    """Run a saved monitor. Returns the report; raises MonitorRunFailed (state unchanged) when the
    fan-out did not answer. Records the run's events and saves the advanced state on success."""
    _name(name)
    ws = _run_dir(slug)
    state = _load_state(ws, name)
    statuses: list = []
    found: dict = {}

    def work(gw):
        _live_searches_are_fresh(gw)
        return run_monitor(state, _executor(gw, statuses, found), ts=ts)

    rerun = registry.call(slug, work, network_mode=network)
    code = fanout.exit_code(statuses)
    if code:
        raise MonitorRunFailed(code, "no source of the monitor answered")
    new_records = []
    for key in rerun.new_ids:
        rec = found[key]
        new_records.append({"id": key, "title": rec.get("title") or "", "year": rec.get("year"),
                            "doi": rec.get("doi") or "", "url": rec.get("url") or ""})
    _save(ws, rerun.state, new_records)
    events.emit(ws, "literature.monitor", action="ran", name=name, new=len(new_records), ts=ts,
                run_count=rerun.state.run_count)
    return {
        "kernel": 1,
        "action": "ran",
        "name": name,
        "ts": ts,
        "new": len(new_records),
        "new_dois": [r["doi"] for r in new_records if r["doi"]],
        "run_count": rerun.state.run_count,
        "sources": [{"source": s["source"], "status": s["status"], "count": s["count"]}
                    for s in statuses],
        "_new_records": new_records,
    }


def run_for_news(workspace: str, name: str, *, ts: str) -> list:
    """The new paper records of one monitor run, for a news `literature:` interest (plan 5.7). No
    printing and no exit: a failure raises MonitorRunFailed and the state is unchanged."""
    try:
        report = execute(workspace, name, ts=ts)
    except SystemExit as exc:                  # a refusal or failure ends the CLI path with a code
        code = exc.code if isinstance(exc.code, int) else EXIT_FAILED
        raise MonitorRunFailed(code, f"the monitor did not answer (exit {code})") from exc
    return report["_new_records"]


def _listing(ws: Path) -> list:
    folder = ws / "monitors"
    rows = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name.endswith(".last.json"):
            continue
        state = _read_state(path, path.stem)
        rows.append({"name": state.monitor_id, "strategies": len(state.strategies),
                     "seen": len(state.seen_ids), "last_run": state.last_run,
                     "run_count": state.run_count})
    return rows


def _show(ws: Path, name: str) -> dict:
    state = _load_state(ws, name)
    return {
        "name": state.monitor_id,
        "strategies": [{"source": s.source, "label": s.label} for s in state.strategies],
        "seen": len(state.seen_ids),
        "last_run": state.last_run,
        "run_count": state.run_count,
    }


def _print(payload, as_json: bool, text: str) -> None:
    if as_json:
        sys.stdout.write(json.dumps(sanitize.sanitize_json_strings(payload), indent=2,
                                    ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(text + "\n")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research monitor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="Save a search to re-run.")
    add.add_argument("slug")
    add.add_argument("name")
    add.add_argument("--query", action="append", default=[], metavar="Q", help="A query (repeat).")
    add.add_argument("--source", default=ALL, metavar="SOURCE",
                     help="all (default) or one of " + ", ".join(fanout.ORDER) + ".")
    add.add_argument("--ts", default=None, metavar="TS", help="RFC 3339 time of this step.")
    run = sub.add_parser("run", help="Re-run a saved search and report only the new papers.")
    run.add_argument("slug")
    run.add_argument("name")
    run.add_argument("--ts", default=None, metavar="TS", help="RFC 3339 time of this run.")
    registry.add_network_option(run)
    listing = sub.add_parser("list", help="The saved searches of a run.")
    listing.add_argument("slug")
    listing.add_argument("--json", action="store_true")
    show = sub.add_parser("show", help="One saved search: strategies, counts, last run.")
    show.add_argument("slug")
    show.add_argument("name")
    show.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "add":
        payload = _add(args.slug, args.name, args.query, args.source, args.ts or _default_ts())
        _print(payload, True, "")
        return
    if args.cmd == "run":
        try:
            report = execute(args.slug, args.name, ts=args.ts or _default_ts(),
                             network=args.network)
        except MonitorRunFailed as exc:
            _refuse(str(exc), exc.code)
        report.pop("_new_records", None)
        _print(report, True, "")
        return
    ws = _run_dir(args.slug)
    if args.cmd == "list":
        rows = _listing(ws)
        text = "\n".join(f"{r['name']}  runs={r['run_count']} seen={r['seen']} last={r['last_run']}"
                         for r in rows) or "no monitors"
        _print({"kernel": 1, "monitors": rows}, args.json, text)
        return
    payload = _show(ws, args.name)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    _print(payload, args.json, text)


if __name__ == "__main__":
    main()
