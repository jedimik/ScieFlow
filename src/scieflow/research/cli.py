"""`scieflow research` commands.

Each command forwards its arguments unchanged to the module's own argparse
entry point, so `scieflow research <cmd> --help` shows that command's options.
"""

import importlib

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
]:
    _forward(_name, f"scieflow.research.search.{_name}", _summary, search)

_forward("validate", "scieflow.research.validate",
         "Validate a JSON artifact against a research schema.", research)
_forward("check-citations", "scieflow.research.citations",
         "Check paper-draft citations against references.bib and searched DOIs.", research)
_forward("zotero-export", "scieflow.research.zotero",
         "Export a run's papers to a Zotero collection.", research)


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
