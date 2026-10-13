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
                               "calls": s, "frozen": _frozen_json(outcome.frozen)},
                              indent=2, ensure_ascii=False))
        return
    _show_frozen(outcome.frozen)
    click.echo(f"written: workspace/{slug}/review/round-{round_}/evidence/bundle.json "
               f"(scite {outcome.scite_status}; {s['mcp']} MCP, {s['rest']} REST, "
               f"{s['cached']} cached, {len(s['skipped'])} skipped)")
    for item in s["skipped"]:
        click.echo(f"  skipped ({item['reason']}): {item['query']}")


_FREEZE_LABELS = (("missing", "no pass A yet"),
                  ("late", "written after the evidence bundle, not frozen"),
                  ("tampered", "CHANGED after it was frozen; pass B refused"))


def _frozen_json(report: dict | None):
    if report is None:
        return None
    return {k: [{"reviewer": rv, "author": au} for rv, au in report[k]]
            for k in ("frozen", "already", "missing", "late", "tampered")} | {
        "invalid": [{"reviewer": rv, "author": au, "reason": why}
                    for (rv, au), why in report["invalid"]]}


def _show_frozen(report: dict | None) -> None:
    if report is None:
        click.echo("  pass A not frozen: the round has no assignment")
        return
    click.echo(f"  pass A frozen: {len(report['frozen']) + len(report['already'])}")
    for key, label in _FREEZE_LABELS:
        for rv, au in report[key]:
            click.echo(f"  {rv} on {au}: {label}")
    for (rv, au), why in report["invalid"]:
        click.echo(f"  {rv} on {au}: invalid, not frozen: {why}")


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


def _run_ws(slug: str):
    from scieflow.core.project import Project

    project = Project.discover()
    try:
        ws = project.run_dir(slug)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if not ws.is_dir():
        raise click.ClickException(f"no workspace {slug!r} under {project.workspace_root}")
    return project, ws


_PASS = click.Choice(["blind", "evidence"])


@review.command("prompt")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
@click.option("--reviewer", required=True, help="The reviewing agent.")
@click.option("--author", required=True, help="The author whose draft is reviewed.")
@click.option("--pass", "pass_", type=_PASS, required=True,
              help="blind (pass A) or evidence (pass B).")
@click.option("--write", "write", is_flag=True,
              help="Write workspace/<SLUG>/prompts/review-<pass>-<reviewer>-on-<author>-round-N.md "
                   "instead of printing the prompt.")
def prompt_cmd(slug, round_, reviewer, author, pass_, write):
    """Build the pass-A or pass-B prompt for one assigned pair.

    Pass B is refused when the pair's pass A was never frozen or changed after it was
    frozen (the coordinator is told through a note.review_pass_a_tampered event).
    """
    from scieflow.core.review import passes

    _project, ws = _run_ws(slug)
    try:
        build = passes.blind_prompt if pass_ == "blind" else passes.evidence_prompt
        text = build(ws, round_, reviewer, author, slug=slug)
    except (passes.PassError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    stem = f"review-{pass_}-{reviewer}-on-{author}-round-{round_}"
    rel_prompt = f"workspace/{slug}/prompts/{stem}.md"
    how = (f"fresh dispatch (no resume, no session id): uv run scieflow agent run "
           f"--role {passes.ROLE} {reviewer} {rel_prompt} workspace/{slug}/logs/{stem}.log")
    if not write:
        click.echo(text, nl=False)
        click.echo(f"# {how}", err=True)
        return
    path = ws / "prompts" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    click.echo(f"written: {rel_prompt}")
    click.echo(how)


@review.command("check")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
@click.option("--reviewer", required=True, help="The reviewing agent.")
@click.option("--author", required=True, help="The author whose draft was reviewed.")
@click.option("--pass", "pass_", type=_PASS, required=True,
              help="blind (pass A) or evidence (pass B).")
def check_cmd(slug, round_, reviewer, author, pass_):
    """Validate one review file and record review.pass_done.

    Schema and identity fields; pass B also needs its frozen pass A unchanged, every
    pass-A comment accounted for and every evidence quote verbatim from the bundle.
    """
    from scieflow.core import events
    from scieflow.core.review import passes

    _project, ws = _run_ws(slug)
    try:
        _data, digest = passes.check_pass(ws, round_, reviewer, author, pass_)
    except passes.TamperError as exc:
        passes.tampered(ws, round_, reviewer, author)
        raise click.ClickException(str(exc)) from exc
    except (passes.PassError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    events.emit(ws, "review.pass_done", **{"round": round_, "reviewer": reviewer,
                                           "author": author, "pass": pass_, "sha256": digest})
    click.echo(f"ok: review/round-{round_}/{passes.file_name(reviewer, author, pass_)} "
               f"(sha256 {digest[:12]}…)")


@review.command("freeze")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
def freeze_cmd(slug, round_):
    """Record the sha256 of every valid pass-A file of round N in assignment.json.

    `scieflow review evidence` does this itself before it builds the bundle; a pass A
    written after the bundle is never frozen. Exit 1 when a frozen pass A changed.
    """
    from scieflow.core.review import passes

    _project, ws = _run_ws(slug)
    try:
        report = passes.freeze(ws, round_)
    except (passes.PassError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"round {round_}: frozen {len(report['frozen'])}, "
               f"already frozen {len(report['already'])}")
    for key, label in _FREEZE_LABELS:
        for rv, au in report[key]:
            click.echo(f"  {rv} on {au}: {label}")
    for (rv, au), why in report["invalid"]:
        click.echo(f"  {rv} on {au}: invalid, not frozen: {why}")
    if report["tampered"]:
        sys.exit(1)


@review.command("matrix")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
@click.option("--check", "check_", is_flag=True,
              help="Check the coordinator's matrix.json, write matrix.md and record "
                   "review.matrix_ready.")
def matrix_cmd(slug, round_, check_):
    """Write the matrix skeleton of round N, or check the clustered matrix.

    Without --check: review/round-N/matrix.skeleton.json (every comment of every
    valid pass plus every evidence-bundle flag) for the coordinator to cluster
    into matrix.json.
    """
    from scieflow.core import events
    from scieflow.core.review import matrix as mx

    _project, ws = _run_ws(slug)
    rel = f"workspace/{slug}/review/round-{round_}"
    if not check_:
        try:
            path = mx.write_skeleton(ws, round_)
        except (mx.MatrixError, ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
        sk = json.loads(path.read_text(encoding="utf-8"))
        click.echo(f"written: {rel}/{mx.SKELETON} ({len(sk['items'])} comments, "
                   f"{len(sk['flags'])} flags, scite {sk['scite_status']})")
        for m in sk["missing"]:
            click.echo(f"  missing: {m['reviewer']} on {m['author']} ({m['pass']})")
        for r in sk["refused"]:
            click.echo(f"  refused: {r['reviewer']} on {r['author']} ({r['pass']}): "
                       f"{r['reason']}")
        click.echo(f"next: cluster it into {rel}/{mx.MATRIX}, then "
                   f"`scieflow review matrix {slug} --round {round_} --check`")
        return
    try:
        data = mx.check(ws, round_)
        path = mx.write_md(ws, round_, data)
    except mx.MatrixError as exc:
        raise click.ClickException("matrix refused:\n  " + "\n  ".join(exc.problems)) from exc
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    blockers = [c["cluster"] for c in data["clusters"] if c["blocker"]]
    events.emit(ws, "review.matrix_ready", round=round_, clusters=len(data["clusters"]),
                blockers=blockers)
    click.echo(f"written: {rel}/{path.name} ({len(data['clusters'])} clusters, "
               f"{len(blockers)} blocker(s))")
    _open_gates(_project, ws, round_)


def _open_gates(project, ws, round_) -> None:
    from scieflow.core.review import decision_gates as dg

    try:
        report = dg.open_gates(project, ws, round_)
    except (dg.DecisionGateError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"review gates of round {round_}: opened {len(report['opened'])}, "
               f"already there {len(report['existing'])}, withdrawn {len(report['withdrawn'])}")
    for g in report["opened"]:
        who = "human only" if g["requires_human"] else (
            "user, or the coordinator with a rationale" if g["in_scope"] else "user")
        click.echo(f"  {g['id']} {g['kind']} ({who}): {dg.parse(g)['cluster']} "
                   f"[{'|'.join(g['options'])}]")
    if report["opened"]:
        click.echo(f"answer with `scieflow gate answer {ws.name} <gate id> <option> "
                   "[--note TEXT]` (an overrule needs the user's reason as --note)")


@review.command("gates")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
def gates_cmd(slug, round_):
    """Open round N's review-decision and review-blocker gates (idempotent).

    One review-decision gate per undecided cluster (accept|reject|defer) and one
    human-only review-blocker gate per open blocker (overrule|fixed). Gates of an
    earlier matrix are withdrawn. `review matrix --check` runs this too.
    """
    project, ws = _run_ws(slug)
    _open_gates(project, ws, round_)


@review.command("revise")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
@click.option("--author", required=True, help="The author whose draft is revised.")
@click.option("--write", "write", is_flag=True,
              help="Write workspace/<SLUG>/prompts/revise-<author>-round-N.md instead of "
                   "printing the prompt.")
def revise_cmd(slug, round_, author, write):
    """Build the author's revision prompt with the ACCEPTED clusters only.

    Needs decisions recorded by `review decide` or a review-decision gate; a
    hand-written decisions.json is ignored. Exit 1 when nothing is accepted.
    """
    from scieflow.core.review import revise

    _project, ws = _run_ws(slug)
    try:
        text = revise.revision_prompt(ws, round_, author, slug=slug)
        got = revise.clusters_for(ws, round_, author)
    except (revise.RevisionError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    stem = f"revise-{author}-round-{round_}"
    rel_prompt = f"workspace/{slug}/prompts/{stem}.md"
    how = (f"dispatch: uv run scieflow agent run --role {revise.ROLE} {author} {rel_prompt} "
           f"workspace/{slug}/logs/{stem}.log")
    summary = (f"accepted {', '.join(c['cluster'] for c in got['accepted'])}; "
               f"undecided {', '.join(got['undecided']) or 'none'}")
    if not write:
        click.echo(text, nl=False)
        click.echo(f"# {summary}\n# {how}", err=True)
        return
    path = ws / "prompts" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    click.echo(f"written: {rel_prompt} ({summary})")
    click.echo(how)


@review.command("coverage")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
@click.option("--author", required=True, help="The author whose response letter is checked.")
def coverage_cmd(slug, round_, author):
    """Check that response-<author>.md addresses every accepted cluster id (exit 1 if not)."""
    from scieflow.core.review import revise

    _project, ws = _run_ws(slug)
    try:
        got = revise.coverage(ws, round_, author)
    except (revise.RevisionError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    name = f"review/round-{round_}/{revise.response_name(author)}"
    if got["ok"]:
        click.echo(f"ok: {name} covers {', '.join(got['required']) or 'no accepted cluster'}")
        return
    if not got["response"]:
        click.echo(f"missing: {name} does not exist (accepted: {', '.join(got['required'])})")
    else:
        click.echo(f"missing in {name}: {', '.join(got['missing'])}")
    sys.exit(1)


@review.command("merge-check")
@click.argument("slug")
def merge_check_cmd(slug):
    """Refuse merge (exit 1) while a retracted / unresolvable reference blocker is open."""
    from scieflow.core.review import service

    project, _ws = _run_ws(slug)
    try:
        open_ = service.blockers(project, slug)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if not open_:
        click.echo(f"ok: workspace/{slug} has no open blockers")
        return
    click.echo(f"merge refused: workspace/{slug} has {len(open_)} open blocker(s)")
    for b in open_:
        refs = ", ".join(" ".join(x for x in (r.get("doi"), f"({r['key']})" if r.get("key")
                                              else None) if x) for r in b["references"])
        click.echo(f"  round {b['round']} {b['cluster'] or '(no cluster)'}: {b['reason']} "
                   f"{refs}" + (f" (decided: {b['decision']})" if b["decision"] else ""))
    click.echo("fix the citation and build the evidence of a new round, or let the user "
               "overrule it:")
    for b in open_:
        if b["cluster"]:
            click.echo(f"  scieflow review decide {slug} --round {b['round']} --decision "
                       f"{b['cluster']}=reject --overrule {b['cluster']}=REASON --yes")
    click.echo(f"  (or answer its open review-blocker gate: scieflow gate answer {slug} <gate id> "
               "overrule --note REASON; `scieflow gate list` shows it)")
    sys.exit(1)
