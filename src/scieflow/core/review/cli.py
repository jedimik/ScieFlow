"""`scieflow review` commands."""

from __future__ import annotations

import json
import sys

import click
import yaml

_BOOLS = {"true": True, "yes": True, "on": True, "false": False, "no": False}
_STRUCTURED = ("pool", "matrix_override")


@click.group()
def review():
    """Draft review: settings, reviewer assignment, evidence and decisions."""


def _pool(raw: str):
    """`agent[:covers],…` shorthand, or a YAML/JSON list."""
    if raw.lstrip().startswith("["):
        return yaml.safe_load(raw)
    entries = []
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        agent, _, covers = token.partition(":")
        entries.append({"agent": agent.strip(), **({"covers": covers.strip()} if covers else {})})
    return entries


def parse_set(text: str) -> tuple[str, object]:
    """KEY=VALUE from the command line to a typed (key, value); '' clears structured keys."""
    from scieflow.core.review.settings import SettingsError

    key, sep, raw = text.partition("=")
    key = key.strip()
    if not sep or not key:
        raise SettingsError(f"--set expects KEY=VALUE, got {text!r}")
    raw = raw.strip()
    if key in _STRUCTURED:
        if raw == "":
            return key, None
        try:
            value = _pool(raw) if key == "pool" else yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise SettingsError(f"review.{key}: not valid YAML: {exc}") from exc
        return key, value
    if key == "reviews_per_draft":
        try:
            return key, int(raw)
        except ValueError:
            return key, raw
    if key.startswith("allow_"):
        return key, _BOOLS.get(raw.lower(), raw)
    return key, raw


def _show(block: dict) -> None:
    for key, value in block.items():
        shown = "(research.draft-reviewers)" if key == "pool" and value is None else value
        click.echo(f"  {key:<26} {shown}")


@review.command("configure")
@click.argument("slug")
@click.option("--set", "sets", multiple=True, metavar="KEY=VALUE",
              help="scite=off|ask|approved, reviews_per_draft=N, allow_same_family_review=BOOL, "
                   "allow_self_review=BOOL, review_decisions=human|coordinator, "
                   "pool=AGENT[:all|auto],… (empty clears), matrix_override=YAML (repeatable).")
@click.option("--as-agent", is_flag=True,
              help="Write as the coordinator agent: only scite=off and reviews_per_draft.")
@click.option("--yes", is_flag=True, help="Write without asking for confirmation.")
def configure(slug, sets, as_agent, yes):
    """Show or change workspace/<SLUG>'s review settings (the review: block only)."""
    from scieflow.core.project import Project
    from scieflow.core.review import settings

    project = Project.discover()
    try:
        current = settings.load(project, slug)
        if not sets:
            click.echo(f"Review settings of workspace/{slug}:")
            _show(current)
            return
        changes = dict(parse_set(s) for s in sets)
        actor = "agent" if as_agent else "human"
        settings.check_actor(changes, actor)
        settings.validate({k: v for k, v in changes.items() if v is not None})
        for key, value in changes.items():
            click.echo(f"  {key}: {current.get(key)!r} -> {value!r}")
        if not yes:
            if not sys.stdin.isatty():
                raise click.ClickException("not written: pass --yes to confirm non-interactively")
            if not click.confirm("Write these changes?", default=False):
                click.echo("not written")
                return
        settings.write(project, slug, changes, actor)
    except settings.SettingsError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"written: workspace/{slug}/config.yml (review:)")


@review.command("assign")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
@click.option("--json", "as_json", is_flag=True, help="Print the assignment as JSON.")
def assign_cmd(slug, round_, as_json):
    """Plan and record who reviews which draft in round N (review/round-N/assignment.json)."""
    from scieflow.core.project import Project
    from scieflow.core.review import assign
    from scieflow.core.review.settings import SettingsError

    project = Project.discover()
    try:
        ws = project.run_dir(slug)
        if not ws.is_dir():
            raise click.ClickException(f"no workspace {slug!r} under {project.workspace_root}")
        from scieflow.core.review import service

        authors, pool, effective, families = service.assignment_inputs(project, slug)
        plan = assign.plan(authors, pool, effective, families)
        path = assign.write(ws, round_, plan)
    except (assign.AssignmentError, SettingsError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(path.read_text(), nl=False)
        return
    click.echo(f"Round {round_} of workspace/{slug} ({plan['source']}; "
               f"reviews_per_draft {plan['settings']['reviews_per_draft']}):")
    for author in plan["authors"]:
        name = author["agent"]
        got = [p for p in plan["pairs"] if p["author"] == name]
        marks = [p["reviewer"] + (" (self)" if p["self"] else "")
                 + (" (same family)" if p["same_family"] and not p["self"] else "")
                 for p in got]
        click.echo(f"  {name:<20} <- {', '.join(marks)}")
    for warning in plan["warnings"]:
        click.echo(f"  warning: {warning}")
    click.echo("every review is a fresh dispatch")
    click.echo(f"written: {path.relative_to(project.root)}")


def _show_plan(p) -> None:
    data = p.to_json()
    c = data["counts"]
    click.echo(f"Evidence plan for workspace/{p.slug} round {p.round}: {c['requests']} outgoing "
               f"request(s), {c['mcp']} scite MCP call(s); nothing has been sent.")
    for r in data["requests"]:
        cond = f"  [{r['conditional']}]" if r.get("conditional") else ""
        kind = f" ({r['kind']})" if r.get("kind") else ""
        click.echo(f"  {r['id']:<4} {r['source']:<12} {r['op']}{kind}: "
                   f"{json.dumps(r['sends'], ensure_ascii=False)}{cond}")
    reused = {k: v for k, v in data["reused"].items() if v}
    if reused:
        click.echo("  reused from an earlier build (no call): "
                   + ", ".join(f"{k} {v}" for k, v in reused.items()))
    for r in data["refused"]:
        where = ", ".join(f"{k} {v}" for k, v in r.items() if k != "reason")
        click.echo(f"  refused ({where}): {r['reason']}")
    for d in data["dropped"]:
        click.echo(f"  not planned: {d['reason']}")
    for u in data["unsent"]:
        click.echo(f"  not sent ({u['kind']}, {u['tool']}): {u['detail']}")
    for note in data["notes"]:
        click.echo(f"  note: {note}")


@review.command("evidence")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
@click.option("--plan", "plan_only", is_flag=True,
              help="Print the exact outgoing request list and send nothing.")
@click.option("--json", "as_json", is_flag=True, help="Print the plan or the outcome as JSON.")
def evidence_cmd(slug, round_, plan_only, as_json):
    """Build round N's shared evidence bundle (review/round-N/evidence/bundle.json).

    Free sources first (Crossref, scite public tallies, Europe PMC); scite MCP only for
    contested sources, the topic search, novelty claims and reviewer follow-ups, within
    the budgets of config/scite.yml. Needs review.scite=approved, or 'ask' plus a
    human-answered scite-consent gate. Exit 3 on a policy / consent / dispatch refusal.
    """
    from scieflow.core.project import Project
    from scieflow.core.review import evidence
    from scieflow.integrations.scite import policy as _policy

    project = Project.discover()
    try:
        outcome = evidence.build(project, slug, round_, plan_only=plan_only)
    except _policy.PolicyError as exc:
        click.echo(f"refused: {exc}", err=True)
        sys.exit(_policy.exit_code(exc))
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    if outcome.status == "off":
        if as_json:
            click.echo(json.dumps({"status": "off", "reason": outcome.reason}))
        else:
            click.echo(f"scite is off: {outcome.reason} — nothing sent, no bundle written "
                       "(blind reviews still run)")
        return
    if outcome.status == "planned":
        if as_json:
            click.echo(json.dumps(outcome.plan.to_json(), indent=2, ensure_ascii=False))
        else:
            _show_plan(outcome.plan)
        return
    s = outcome.calls["summary"]
    if as_json:
        click.echo(json.dumps({"status": "built", "scite_status": outcome.scite_status,
                               "calls": s}, indent=2, ensure_ascii=False))
        return
    click.echo(f"written: workspace/{slug}/review/round-{round_}/evidence/bundle.json "
               f"(scite {outcome.scite_status}; {s['mcp']} MCP, {s['rest']} REST, "
               f"{s['cached']} cached, {len(s['skipped'])} skipped)")
    for item in s["skipped"]:
        click.echo(f"  skipped ({item['reason']}): {item['query']}")


def _pairs(values: tuple[str, ...], what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for text in values:
        key, sep, value = text.partition("=")
        if not sep or not key.strip():
            raise click.ClickException(f"{what} expects CLUSTER=VALUE, got {text!r}")
        out[key.strip()] = value
    return out


@review.command("decide")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number.")
@click.option("--decision", "decisions", multiple=True, required=True,
              metavar="CLUSTER=accept|reject|defer", help="A decision (repeatable).")
@click.option("--note", "notes", multiple=True, metavar="CLUSTER=TEXT",
              help="Optional note on a decided cluster (repeatable).")
@click.option("--overrule", "overrules", multiple=True, metavar="CLUSTER=REASON",
              help="The user's reason for deciding a blocker cluster (required for blockers; "
                   "a reject with a reason overrules the blocker).")
@click.option("--rationale", default=None,
              help="Rationale for every decision in this call (required with --as-agent).")
@click.option("--as-agent", is_flag=True,
              help="Decide as the coordinator agent (only with review_decisions: coordinator; "
                   "never blockers).")
@click.option("--yes", is_flag=True, help="Write without asking for confirmation.")
def decide_cmd(slug, round_, decisions, notes, overrules, rationale, as_agent, yes):
    """Record per-cluster decisions for round N (review/round-N/decisions.json)."""
    from scieflow.core.project import Project
    from scieflow.core.review import service
    from scieflow.core.review.decisions import DecisionError

    chosen = _pairs(decisions, "--decision")
    extra = {"note": _pairs(notes, "--note"), "overrule_reason": _pairs(overrules, "--overrule")}
    for field, values in extra.items():
        stray = sorted(set(values) - set(chosen))
        if stray:
            raise click.ClickException(f"--{'overrule' if field != 'note' else 'note'} for "
                                       f"{stray[0]!r}, which has no --decision")
    batch = {}
    for cluster, value in chosen.items():
        entry = {"decision": value.strip()}
        for field, values in extra.items():
            if cluster in values:
                entry[field] = values[cluster]
        if rationale is not None:
            entry["rationale"] = rationale
        batch[cluster] = entry
    actor = "agent" if as_agent else "human"
    for cluster, entry in batch.items():
        click.echo(f"  {cluster}: {entry['decision']}"
                   + (" (overrule)" if "overrule_reason" in entry else ""))
    if not yes:
        if not sys.stdin.isatty():
            raise click.ClickException("not written: pass --yes to confirm non-interactively")
        if not click.confirm("Record these decisions?", default=False):
            click.echo("not written")
            return
    project = Project.discover()
    try:
        service.decide(project, slug, round_, batch, actor)
    except (DecisionError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"written: workspace/{slug}/review/round-{round_}/decisions.json")


@review.command("status")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print rounds, blockers and scite as JSON.")
def status_cmd(slug, as_json):
    """Review rounds of workspace/<SLUG>: files present, open blockers, scite."""
    import json

    from scieflow.core.project import Project
    from scieflow.core.review import service

    project = Project.discover()
    try:
        rounds = service.rounds(project, slug)
        blockers = service.blockers(project, slug)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    scite = service.scite_status(project)
    if as_json:
        click.echo(json.dumps({"rounds": rounds, "blockers": blockers, "scite": scite},
                              indent=2, ensure_ascii=False))
        return
    if not rounds:
        click.echo(f"workspace/{slug}: no review rounds yet")
    for r in rounds:
        done = {ps: sum(1 for p in r["pairs"] if p[ps]) for ps in ("blind", "evidence")}
        click.echo(f"round {r['round']}: {len(r['pairs'])} pairs, blind {done['blind']}, "
                   f"evidence {done['evidence']}, bundle {'yes' if r['evidence'] else 'no'} "
                   f"(scite {r['scite_status'] or '-'}), matrix "
                   f"{'yes' if r['matrix'] else 'no'}, decisions "
                   f"{'yes' if r['decisions'] else 'no'}")
    for b in blockers:
        refs = ", ".join(x.get("doi") or x.get("key", "?") for x in b["references"])
        click.echo(f"  blocker {b['cluster'] or '(no cluster)'}: {b['reason']} {refs}"
                   + (f" (decided: {b['decision']})" if b["decision"] else ""))
    click.echo(f"scite: {'configured' if scite['configured'] else 'not configured'}")
