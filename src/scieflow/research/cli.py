"""`scieflow research` commands.

Each command forwards its arguments unchanged to the module's own argparse
entry point, so `scieflow research <cmd> --help` shows that command's options.
"""

import importlib
import os

import click

_PASSTHROUGH = {"ignore_unknown_options": True, "allow_extra_args": True,
                "help_option_names": []}


def _forward(name: str, module: str, summary: str, group: click.Group) -> None:
    @group.command(name, context_settings=_PASSTHROUGH, add_help_option=False, help=summary)
    @click.pass_context
    def command(ctx):
        importlib.import_module(module).main(ctx.args)


@click.group()
def research():
    """Literature research: search, validation, citations, Zotero."""


@research.group()
def search():
    """Search a literature index; prints normalized paper JSON."""


for _name, _summary in [
    ("openalex", "Search OpenAlex works."),
    ("arxiv", "Search arXiv preprints."),
    ("europepmc", "Search Europe PMC."),
    ("crossref", "Search Crossref works."),
    ("pubmed", "Search PubMed (NCBI E-utilities)."),
    ("datacite", "Search DataCite (datasets, software, preprints)."),
]:
    _forward(_name, f"scieflow.research.search.{_name}", _summary, search)

_forward("semantic-scholar", "scieflow.research.search.semantic_scholar",
         "Search Semantic Scholar papers.", search)

_forward("all", "scieflow.research.search.fanout",
         "Search every enabled source at once: merged, deduplicated and ranked.", search)

_forward("validate", "scieflow.research.validate",
         "Validate a JSON artifact against a research schema.", research)
_forward("verify-bib", "scieflow.research.verify_bib",
         "Check that each reference in a bibliography is the work it claims to be.", research)
_forward("snowball", "scieflow.research.search.snowball",
         "Follow the citations of seed DOIs: the papers that cite them (or that they cite), one or two hops.",
         research)
_forward("check-citations", "scieflow.research.citations",
         "Check paper-draft citations against references.bib and searched DOIs.", research)
_forward("zotero-export", "scieflow.research.zotero",
         "Export a run's papers to a Zotero collection.", research)


# --- a run's network mode and recorded literature (M-C1 5.4, 5.5) ----------------------------

NETWORK_MODES = ("live", "record", "replay", "offline")


def _run_dir(slug: str):
    from scieflow.core.project import Project

    ws = Project.discover().run_dir(slug)
    if not ws.is_dir():
        raise click.ClickException(f"no run workspace/{slug} in this project")
    return ws


def _no_dispatch(what: str) -> None:
    from scieflow.integrations.scite import mcp as _mcp

    if _mcp.in_dispatch():
        raise click.ClickException(f"refused: {what} is not allowed inside a dispatch")


def _snapshot_folder(ws) -> None:
    """A replaying run needs its snapshots/literature folder before its mode is written, so the
    sandbox can protect it (M-C1 5.8). A linked snapshots/ is left as it is. A file in its place,
    or a folder that cannot be made, refuses here, so nothing is changed."""
    snaps = ws / "snapshots"
    if snaps.is_symlink():
        return
    if os.path.lexists(snaps) and not snaps.is_dir():
        raise click.ClickException("snapshots is not a folder; move it aside first "
                                   "(nothing was changed)")
    try:
        (snaps / "literature").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise click.ClickException(f"cannot create snapshots/literature "
                                   f"({exc.strerror or 'OS error'}); nothing was changed") from exc


@research.command("network")
@click.argument("slug")
@click.argument("mode", required=False, type=click.Choice(NETWORK_MODES))
@click.option("--reason", default="", help="Why the mode changes (needed to loosen or clear).")
@click.option("--as-agent", is_flag=True,
              help="An agent: may only set replay or offline, never loosen.")
@click.option("--clear", is_flag=True,
              help="Human only: delete network.yml (the run returns to its data-level default).")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def network_cmd(slug, mode, reason, as_agent, clear, as_json):
    """Show or set a run's network mode: live, record, replay or offline.

    Replay and offline send nothing; a search with no recorded answer exits 6. Moving to live or
    record, or clearing, needs --reason. An agent may only tighten to replay or offline."""
    import json

    from scieflow.core import events, network

    ws = _run_dir(slug)
    if mode is None and not clear:
        doc = network.describe(ws)
        snaps = _snapshots()
        try:
            doc["snapshots"] = sum(row["count"] for row in snaps.summary(ws).values())
            problem = None
        except snaps.SnapshotCorrupt as exc:
            doc["snapshots"], problem = None, str(exc)
        if as_json:
            click.echo(json.dumps(doc, indent=2, ensure_ascii=False))
        else:
            click.echo(f"{slug}: {doc['mode']} ({doc['origin']})")
            if doc["origin"] == "file":
                click.echo(f"  set by {doc['set_by']} at {doc['set_at']}: {doc['reason']}")
            shown = doc["snapshots"] if problem is None else "unreadable"
            click.echo(f"  recorded snapshots: {shown}")
        if problem is not None:
            raise click.ClickException(problem)
        return

    _no_dispatch("changing the network mode")
    if clear and mode:
        raise click.ClickException("--clear takes no mode")
    if as_agent and (clear or mode not in ("replay", "offline")):
        raise click.ClickException("an agent may only set replay or offline, never live, record "
                                   "or clear")
    actor = "agent" if as_agent else "human"
    previous, _ = network.base(ws)
    loosening = mode in network.EGRESS and previous not in network.EGRESS
    if (clear or loosening) and not reason.strip():
        what = "clearing the network file" if clear else f"moving to {mode}"
        raise click.ClickException(f"{what} needs --reason")
    if (network.default(ws)[0] if clear else mode) in ("replay", "offline"):
        _snapshot_folder(ws)   # before the file: a refusal here leaves network.yml unchanged
    try:
        if clear:
            network.clear(ws)
        else:
            network.write_mode(ws, mode, actor=actor, reason=reason)
    except network.NetworkWriteError as exc:
        raise click.ClickException(str(exc)) from exc
    new, _ = network.base(ws)
    events.emit(ws, "network.set", actor=actor, mode=new, previous=previous, reason=reason)
    click.echo(f"{slug}: {new} (was {previous})")


def _snapshots():
    from scieflow.research.lib import snapshots

    return snapshots


@research.group("snapshots")
def snapshots_group():
    """A run's recorded literature answers: list, verify, copy (coordinator only)."""


@snapshots_group.command("list")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def snapshots_list_cmd(slug, as_json):
    """Count and size of the recorded answers, per provider."""
    import json

    snaps = _snapshots()
    try:
        rows = snaps.summary(_run_dir(slug))
    except snaps.SnapshotCorrupt as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo(f"{slug}: no recorded answers")
    for provider, row in rows.items():
        click.echo(f"  {provider}: {row['count']} answer(s), {row['bytes']} bytes")


@snapshots_group.command("verify")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def snapshots_verify_cmd(slug, as_json):
    """Check every recorded answer (shape, file name, provider, body hash); exit 1 naming each bad file.

    The stored request is not re-checked here; a replay checks it when it reads the answer."""
    import json

    snaps = _snapshots()
    ws = _run_dir(slug)
    try:
        problems = snaps.verify_tree(ws)
        verified = len(snaps.files(ws)) - len(problems)
    except snaps.SnapshotCorrupt as exc:
        if not as_json:
            raise click.ClickException(str(exc)) from exc
        problems, verified = [str(exc)], 0
    if as_json:
        click.echo(json.dumps({"verified": verified, "problems": problems}, indent=2,
                              ensure_ascii=False))
    elif problems:
        for line in problems:
            click.echo(line, err=True)
    else:
        click.echo(f"verified {verified} recorded answer(s)")
    if problems:
        raise click.ClickException(f"{len(problems)} snapshot problem(s)")


@snapshots_group.command("copy")
@click.argument("source")
@click.argument("target")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def snapshots_copy_cmd(source, target, as_json):
    """Copy the recorded answers of SOURCE into TARGET. Refused from an L2 run (exit 3)."""
    import json
    import sys

    from scieflow.core import events
    from scieflow.core.data import levels

    _no_dispatch("copying recorded literature")
    snaps = _snapshots()
    src, dst = _run_dir(source), _run_dir(target)
    if levels.run_level(src) == "L2":
        click.echo(f"refused: data protection: run {source} is L2; its snapshots hold its queries",
                   err=True)
        sys.exit(3)
    try:
        copied, skipped = snaps.copy_into(src, dst)
    except snaps.SnapshotCopyError as exc:
        raise click.ClickException(str(exc)) from exc
    events.emit(dst, "literature.snapshot", action="copied", count=copied, **{"from": source})
    if as_json:
        click.echo(json.dumps({"copied": copied, "skipped": skipped}, indent=2))
    else:
        click.echo(f"copied {copied} answer(s) from {source} to {target} "
                   f"({skipped} already there)")


@research.group()
def scite():
    """scite: readiness and the review evidence bundle (coordinator only)."""


@scite.command("status")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def scite_status_cmd(as_json):
    """Is scite configured, is the SDK installed, is the token exposed to dispatches,
    which harness configs register a scite MCP server (names only)."""
    import json

    from scieflow.core.project import Project
    from scieflow.core.workspace import echo_scite
    from scieflow.integrations.scite import doctor

    report = doctor.report(Project.discover().root)
    if as_json:
        click.echo(json.dumps(report, indent=2))
        return
    echo_scite(report)
    for r in report["harness_registrations"]:
        click.echo(f"  registered: {r['harness']} {r['server']} ({r['file']})")


def _evidence(slug: str, round_: int, plan_only: bool, as_json: bool) -> None:
    from scieflow.core.review.cli import evidence_cmd

    args = [slug, "--round", str(round_)] + (["--plan"] if plan_only else []) \
        + (["--json"] if as_json else [])
    evidence_cmd.main(args, standalone_mode=False)


@scite.command("plan")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number.")
@click.option("--json", "as_json", is_flag=True, help="Print the plan as JSON.")
def scite_plan_cmd(slug, round_, as_json):
    """Same as `scieflow review evidence SLUG --round N --plan`: sends nothing."""
    _evidence(slug, round_, True, as_json)


@scite.command("run")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number.")
@click.option("--json", "as_json", is_flag=True, help="Print the outcome as JSON.")
def scite_run_cmd(slug, round_, as_json):
    """Same as `scieflow review evidence SLUG --round N`."""
    _evidence(slug, round_, False, as_json)


@scite.command("backfill")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number.")
@click.option("--force", is_flag=True,
              help="Backfill a round that already has a matrix (it must then be rebuilt and "
                   "its decisions made again).")
@click.option("--json", "as_json", is_flag=True, help="Print the outcome as JSON.")
def scite_backfill_cmd(slug, round_, force, as_json):
    """Fill what round N's evidence bundle is missing after a quota stop or an outage.

    Earlier answers are reused (no call); only the skipped requests are sent, within
    the budgets of config/scite.yml. Pass-B reviews written against the old bundle
    should be re-checked afterwards. Exit 3 on a policy / consent / dispatch refusal.
    """
    import json
    import sys

    from scieflow.core.project import Project
    from scieflow.core.review import evidence
    from scieflow.integrations.scite import policy as _policy

    project = Project.discover()
    try:
        outcome = evidence.backfill(project, slug, round_, force=force)
    except _policy.PolicyError as exc:
        click.echo(f"refused: {exc}", err=True)
        sys.exit(_policy.exit_code(exc))
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    if outcome.status == "off":
        click.echo(f"scite is off: {outcome.reason} — nothing sent")
        return
    s = outcome.calls["summary"]
    if as_json:
        click.echo(json.dumps({"status": "built", "scite_status": outcome.scite_status,
                               "calls": s}, indent=2, ensure_ascii=False))
        return
    click.echo(f"backfilled: workspace/{slug}/review/round-{round_}/evidence/bundle.json "
               f"(scite {outcome.scite_status}; {s['mcp']} MCP, {s['rest']} REST, "
               f"{s['cached']} cached, {len(s['skipped'])} still skipped)")
    for item in s["skipped"]:
        click.echo(f"  skipped ({item['reason']}): {item['query']}")
    click.echo("next: re-run the matrix check; pass B written against the old bundle may "
               "need a re-run")


# --- gap discovery (src/scieflow/research/skills/gap-discovery/SKILL.md) -----------------------


def _discovery_call(fn, *args, **kw):
    """Run one pipeline step: a policy / consent / dispatch refusal exits 3, a step that
    cannot run exits 1 — nothing is sent in either case."""
    import sys

    from scieflow.integrations.scite import policy as _policy
    from scieflow.research.discovery import pipeline

    try:
        return fn(*args, **kw)
    except _policy.PolicyError as exc:
        click.echo(f"refused: {exc}", err=True)
        sys.exit(_policy.exit_code(exc))
    except (pipeline.DiscoveryError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


def _project():
    from scieflow.core.project import Project
    return Project.discover()


def _dump(doc) -> None:
    import json
    click.echo(json.dumps(doc, indent=2, ensure_ascii=False, default=str))


@scite.command("gaps")
@click.argument("slug")
@click.option("--plan", "plan_only", is_flag=True,
              help="Print the exact outgoing requests; send and write nothing.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def scite_gaps_cmd(slug, plan_only, as_json):
    """Gap discovery steps 1-2: the evidence map and the typed detectors.

    Reads workspace/SLUG/discovery/query.yml; at most 2 scite MCP calls (disputes over
    the seed DOIs, stated open questions on the topic) plus the free providers allowed
    by config/integrations.yml. Writes discovery/evidence/."""
    from scieflow.research.discovery import pipeline

    out = _discovery_call(pipeline.gaps_evidence, _project(), slug, plan_only=plan_only)
    if as_json or plan_only:
        _dump(out)
        return
    click.echo(f"evidence map: workspace/{slug}/discovery/evidence/ "
               f"({out['mcp_calls']} of {pipeline.GAP_RUN_MAX_MCP} gap-run MCP calls used)")
    for name, st in out["detectors"].items():
        click.echo(f"  {name}: {st['status']}"
                   + (f", {st['gaps']} candidate gap(s)" if "gaps" in st else ""))


@scite.command("novelty")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Tournament round.")
@click.option("--plan", "plan_only", is_flag=True,
              help="Print the exact outgoing requests; send and write nothing.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def scite_novelty_cmd(slug, round_, plan_only, as_json):
    """Gap discovery step 8: scite novelty + counter-evidence for the round's top-k
    (later rounds: changed items only), ≤2 MCP calls within the gap-run cap."""
    from scieflow.research.discovery import pipeline

    out = _discovery_call(pipeline.scite_novelty, _project(), slug, round_,
                          plan_only=plan_only)
    if as_json or plan_only:
        _dump(out)
        return
    click.echo(f"scite check round {round_}: {out['status']} ({out.get('calls', 0)} MCP "
               f"calls; gap run {out['gap_run']['mcp_calls']}/{out['gap_run']['cap']})")
    for hid, row in out["results"].items():
        click.echo(f"  {hid}: {row['scite_status']}, {len(row['novelty_hits'])} hit(s), "
                   f"{len(row['counter_evidence'])} counter-evidence")


@research.group()
def discover():
    """Gap discovery: the mechanical steps between the dispatches (coordinator only)."""


@discover.command("verify")
@click.argument("slug")
def discover_verify_cmd(slug):
    """Step 3: keep only gaps with a verbatim quote of fetched or delivered evidence."""
    from scieflow.research.discovery import pipeline

    out = _discovery_call(pipeline.verify, _project(), slug)
    click.echo(f"verified: {len(out['kept'])} of {out['candidates']} candidate gap(s) kept "
               f"-> workspace/{slug}/discovery/verified.json")
    for d in out["dropped"]:
        click.echo(f"  dropped {d['source']}: {d['reason']}")
    for agent in out["invalid_files"]:
        click.echo(f"  unreadable: gaps/{agent}.json")


@discover.command("pool")
@click.argument("slug")
def discover_pool_cmd(slug):
    """Step 4: pool the agents' hypotheses on verified gaps as H1..Hn."""
    from scieflow.research.discovery import pipeline

    out = _discovery_call(pipeline.pool, _project(), slug)
    click.echo(f"pooled {len(out['hypotheses'])} hypothesis(es) -> "
               f"workspace/{slug}/discovery/hypotheses.json")
    for d in out["dropped"]:
        click.echo(f"  dropped {d['source']}: {d['reason']}")


@discover.command("debate-status")
@click.argument("slug")
@click.argument("pairs", nargs=-1, required=True)
def discover_debate_status_cmd(slug, pairs):
    """Step 5: record the debate adjudication, e.g. H1=consensus H3=withdrawn."""
    from scieflow.research.discovery import pipeline

    statuses = {}
    for p in pairs:
        hid, sep, st = p.partition("=")
        if not sep:
            raise click.ClickException(f"{p!r}: use ID=STATUS")
        statuses[hid.strip()] = st.strip()
    _discovery_call(pipeline.set_debate_status, _project(), slug, statuses)
    click.echo(f"recorded {len(statuses)} debate status(es)")


@discover.command("precheck")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True)
@click.option("--plan", "plan_only", is_flag=True,
              help="Print the exact queries and enabled providers; send and write nothing.")
def discover_precheck_cmd(slug, round_, plan_only):
    """Step 6: abstract the hypotheses and run the free novelty pre-check."""
    from scieflow.research.discovery import pipeline

    out = _discovery_call(pipeline.precheck, _project(), slug, round_, plan_only=plan_only)
    if plan_only:
        _dump(out)
        return
    refused = [a["id"] for a in out["abstracts"] if a["status"] != "ok"]
    click.echo(f"abstracted {len(out['abstracts']) - len(refused)} of {len(out['abstracts'])}"
               f" -> workspace/{slug}/discovery/round-{round_}/novelty-free.json")
    for hid in refused:
        click.echo(f"  refused (residual leak, never sent): {hid}")


@discover.command("schedule")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True)
def discover_schedule_cmd(slug, round_):
    """Step 7a: the seeded, capped match list and one judge prompt per match."""
    from scieflow.research.discovery import pipeline

    out = _discovery_call(pipeline.schedule, _project(), slug, round_)
    click.echo(f"{len(out['matches'])} match(es), seed {out['seed']} -> "
               f"workspace/{slug}/discovery/round-{round_}/judge/match-<n>.md")
    role = {j["agent"]: j["role"] for j in out["judges"]}
    for m in out["matches"]:
        click.echo(f"  match {m['match']}: {m['a']} vs {m['b']}, judge {m['judge']} "
                   f"({m['judge_tier']}; dispatch with --role {role[m['judge']]})")


@discover.command("rank")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True)
def discover_rank_cmd(slug, round_):
    """Step 7b: Elo over the judges' verdicts."""
    from scieflow.research.discovery import pipeline

    out = _discovery_call(pipeline.rank, _project(), slug, round_)
    click.echo(f"ranked: top {', '.join(out['top'])} ({out['no_verdict']} match(es) without "
               "a usable verdict)")


@discover.command("evolve")
@click.argument("slug")
@click.option("--from-round", type=int, default=1, show_default=True)
def discover_evolve_cmd(slug, from_round):
    """Step 9: apply the top-k refinements as the next round."""
    from scieflow.research.discovery import pipeline

    out = _discovery_call(pipeline.evolve, _project(), slug, from_round=from_round)
    click.echo(f"round {out['round']}: {len(out['hypotheses'])} hypothesis(es), changed: "
               f"{', '.join(out['changed']) or 'none'}")


@discover.command("table")
@click.argument("slug")
def discover_table_cmd(slug):
    """Print the final tournament table (paste it under '## Tournament')."""
    from scieflow.research.discovery import pipeline

    click.echo(_discovery_call(pipeline.tournament_table, _project(), slug))


@discover.command("check")
@click.argument("slug")
def discover_check_cmd(slug):
    """Step 10: write report/tournament.{json,md}; fail while the report lacks the
    tournament table or the meta-review."""
    from scieflow.research.discovery import pipeline

    problems = _discovery_call(pipeline.report_check, _project(), slug)
    if problems:
        for p in problems:
            click.echo(f"  {p}", err=True)
        raise click.ClickException(f"{len(problems)} report problem(s)")
    click.echo("report complete")


@discover.command("select")
@click.argument("slug")
@click.option("--read", "read_", is_flag=True,
              help="Read the human answer and write report/selected_hypotheses.json.")
def discover_select_cmd(slug, read_):
    """Step 11: open the hypothesis-selection gate (human only), or read its answer."""
    from scieflow.research.discovery import pipeline

    project = _project()
    if read_:
        out = _discovery_call(pipeline.read_selection, project, slug)
        click.echo(f"selected: {', '.join(out['selected']) or 'none'} -> "
                   f"workspace/{slug}/report/selected_hypotheses.json")
        return
    gate = _discovery_call(pipeline.open_selection, project, slug)
    click.echo(f"opened gate {gate['id']} ({gate['kind']}); wait for the user's answer")
