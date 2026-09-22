"""`scieflow agent` commands."""

import click

_PASSTHROUGH = {"ignore_unknown_options": True, "allow_extra_args": True,
                "help_option_names": []}


@click.group()
def agent():
    """Dispatch headless agents."""


@agent.command("run", context_settings=_PASSTHROUGH, add_help_option=False)
@click.pass_context
def run(ctx):
    """Run one agent headless: AGENT PROMPT_FILE TRANSCRIPT_FILE [--cwd DIR]."""
    from scieflow.core import agent_run

    agent_run.main(ctx.args)


def _root():
    from scieflow.core import config

    return config.repo_root()


@agent.command("show")
@click.option("--workspace", "slug", help="Show the effective config of workspace/<SLUG>.")
@click.option("--news", "news", is_flag=True, help="Show the news module's agent settings.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def show(slug, news, as_json):
    """Effective role assignments and agent settings, with where each comes from."""
    import json

    from scieflow.core import agent_config

    root = _root()
    if news and slug:
        raise click.UsageError("--news and --workspace are mutually exclusive")
    if news:
        from scieflow.core import agent_configure

        settings = agent_configure.news_settings(root)
        if as_json:
            click.echo(json.dumps(settings, indent=2))
        else:
            click.echo("News module (config/news.yml; restricted web-only agent adapter)")
            shown = {"model": "(agent CLI default)", "reasoning": "(not set)",
                     "timeout": "(per-template default)"}
            for key, value in settings.items():
                click.echo(f"  {key:<10} {value if value is not None else shown[key]}")
        return
    if slug and not agent_config.workspace_dir(root, slug).is_dir():
        raise click.ClickException(f"no workspace {slug!r} under {root / 'workspace'}")
    eff = agent_config.resolve(root, slug)
    if as_json:
        click.echo(json.dumps(eff.to_json(), indent=2))
    else:
        title = f"Workspace {slug} (unset values inherit the defaults)" if slug else "Defaults"
        click.echo(agent_config.format_table(eff, title))
    if eff.problems:
        raise SystemExit(1)


def _print_plan(plan, root) -> None:
    for change in plan.changes:
        click.echo(change.diff(root), nl=False)
    for warning in plan.warnings:
        click.echo(f"warning: {warning}", err=True)
    for note in plan.notes:
        click.echo(f"note: {note}", err=True)


def _choose(label: str, options: list[str], default: str | None = None) -> str:
    for i, option in enumerate(options, 1):
        click.echo(f"  {i}. {option}")
    while True:
        raw = click.prompt(label, default=default if default else None, show_default=bool(default))
        if raw in options:
            return raw
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        click.echo(f"  choose a number 1-{len(options)} or a name")


def _wizard_ops(root, slug, target) -> list:
    """Interactive questions -> list of Op. Each change is validated as it is added."""
    from scieflow.core import agent_config as ac
    from scieflow.core import agent_configure as acf

    ops: list = []
    registry = ac._load_yaml(root / "config" / "agents.yml").get("agents") or {}

    def plan(candidate) -> None:
        if target == "workspace":
            acf.plan_workspace(root, slug, candidate)
        else:
            acf.plan_defaults(root, candidate)

    def attempt(op) -> None:
        new = [op]
        try:
            plan(ops + new)
        except acf.ConfigureError as exc:
            if not (op.kind == "assign" and "primary-only unless the role is promoted" in str(exc)):
                click.echo(f"  not applied: {exc}")
                return
            click.echo(f"  {op.key} is primary-only and the choice includes a support-tier agent.")
            if not click.confirm(
                f"  Let it act as primary for {op.key} only (explicit exception)?", default=False
            ):
                click.echo("  not applied")
                return
            new = [acf.Op("promote", op.key), op]
            try:
                plan(ops + new)
            except acf.ConfigureError as exc2:
                click.echo(f"  not applied: {exc2}")
                return
        ops.extend(new)
        for queued in new:
            click.echo(f"  queued: {queued.kind} {queued.key}"
                       + ("" if queued.value is None else f" = {queued.value}"))

    while True:
        eff = ac.resolve(root, slug if target == "workspace" else None)
        action = _choose("What to change? (roles / agent / done)", ["roles", "agent", "done"], "done")
        if action == "done":
            return ops
        if action == "roles":
            role = _choose("Role", list(ac.ROLES))
            spec = ac.ROLES[role]
            enabled = [n for n, e in registry.items() if e.get("enabled")]
            current = eff.value(role)
            click.echo(f"  {spec.help}; enabled agents: {', '.join(enabled)}"
                       + ("; support agents allowed alongside a primary" if spec.support_ok
                          else "; primary tier (a support agent needs an explicit exception)"))
            if spec.many:
                shown = ",".join(current) if isinstance(current, list) else (current or "")
                raw = click.prompt("  Agents (comma-separated)", default=shown)
                attempt(acf.parse_assign(f"{role}={raw}"))
            else:
                attempt(acf.Op("assign", role, _choose("  Agent", enabled, current)))
        else:
            agent_name = _choose("Agent", list(registry))
            fields = ac.AGENT_FIELDS if target == "default" else ac.WORKSPACE_AGENT_FIELDS
            field_name = _choose("  Setting", list(fields), "model")
            setting = eff.agents.get(agent_name, {}).get(field_name)
            current = None if setting is None else str(setting.value)
            menu = registry[agent_name].get("menu") or {}
            if field_name == "model" and menu.get("models"):
                click.echo(f"  menu models: {', '.join(menu['models'])}")
            if field_name == "reasoning":
                reasoning = menu.get("reasoning") or {}
                if reasoning.get("levels"):
                    click.echo(f"  menu levels: {', '.join(map(str, reasoning['levels']))}")
                if reasoning.get("how"):
                    click.echo(f"  how: {reasoning['how']}")
            raw = click.prompt(f"  {field_name}", default=current)
            try:
                attempt(acf.Op("set", f"{agent_name}.{field_name}", acf.coerce(field_name, raw)))
            except acf.ConfigureError as exc:
                click.echo(f"  not applied: {exc}")


def _news_wizard_ops(root) -> list:
    from scieflow.core import agent_configure as acf

    ops: list = []
    while True:
        current = acf.news_settings(root)
        for op in ops:
            current[op.key] = op.value if op.kind == "set" else None
        field_name = _choose("News setting (agent / model / reasoning / timeout / done)",
                             list(acf.NEWS_FIELDS) + ["done"], "done")
        if field_name == "done":
            return ops
        if field_name == "agent":
            op = acf.Op("set", "agent", _choose("  Agent", ["claude", "codex", "agy"], current["agent"]))
        elif field_name == "reasoning":
            value = _choose("  Reasoning", ["low", "medium", "high", "unset"], current["reasoning"] or "unset")
            op = acf.Op("unset", "reasoning") if value == "unset" else acf.Op("set", "reasoning", value)
        else:
            if field_name == "model":
                from scieflow.news.agents import curated_models

                models = curated_models(current["agent"])
                if models:
                    click.echo(f"  models for {current['agent']}: {', '.join(models)}")
            raw = click.prompt(f"  {field_name} (empty to unset)", default=str(current[field_name] or ""),
                               show_default=bool(current[field_name]))
            if not raw.strip():
                op = acf.Op("unset", field_name)
            else:
                try:
                    op = acf.Op("set", field_name, acf.coerce(field_name, raw.strip()))
                except acf.ConfigureError as exc:
                    click.echo(f"  not applied: {exc}")
                    continue
        try:
            acf.plan_news(root, ops + [op])
        except acf.ConfigureError as exc:
            click.echo(f"  not applied: {exc}")
            continue
        ops.append(op)
        click.echo(f"  queued: {op.kind} {op.key}" + ("" if op.value is None else f" = {op.value}"))


@agent.command("configure")
@click.option("--workspace", "slug", help="Change workspace/<SLUG> only (stores differences from the defaults).")
@click.option("--news", "news", is_flag=True,
              help="Change the news module's agent settings (--set agent|model|reasoning|timeout=VALUE).")
@click.option("--assign", "assigns", multiple=True, metavar="ROLE=AGENT[@MODEL][/EFFORT][,…]",
              help="ROLE=AGENT[@MODEL][/EFFORT][,…] — e.g. "
                   "research.draft-authors=codex-paper,claude@claude-opus-5/extended-thinking "
                   "(repeatable).")
@click.option("--set", "sets", multiple=True, metavar="AGENT.FIELD=VALUE",
              help="Set model, reasoning, timeout_min, cmd, stdin_cmd or enabled (repeatable).")
@click.option("--promote", "promotes", multiple=True, metavar="ROLE",
              help="Let a support-tier agent act as primary for this role only (explicit exception).")
@click.option("--demote", "demotes", multiple=True, metavar="ROLE",
              help="Remove a role's support-as-primary exception.")
@click.option("--unset", "unsets", multiple=True, metavar="KEY",
              help="Remove a workspace override (ROLE or AGENT.FIELD), or an optional default field.")
@click.option("--yes", is_flag=True, help="Write without asking for confirmation.")
def configure(slug, news, assigns, sets, promotes, demotes, unsets, yes):
    """Change agent configuration: the defaults, one workspace, or the news module.

    Without change options, asks interactive questions. With them (as a
    coordinator agent does after asking the user in chat), applies them
    directly. Every change is validated and shown as a diff before writing.
    """
    import sys

    from scieflow.core import agent_configure as acf

    root = _root()
    if news and slug:
        raise click.UsageError("--news and --workspace are mutually exclusive")
    try:
        ops = ([acf.parse_assign(a) for a in assigns]
               + [acf.parse_set(s, news=news) for s in sets]
               + [acf.parse_role_exception("promote", r) for r in promotes]
               + [acf.parse_role_exception("demote", r) for r in demotes]
               + [acf.parse_unset(u) for u in unsets])
        if ops:
            target = "news" if news else "workspace" if slug else "default"
        else:
            if news:
                target = "news"
            elif slug:
                target = "workspace"
            else:
                target = _choose("Configure (default / workspace / news)",
                                 ["default", "workspace", "news"], "default")
                if target == "workspace":
                    slugs = sorted(p.name for p in (root / "workspace").iterdir() if p.is_dir())
                    slug = _choose("Workspace", slugs)
            ops = _news_wizard_ops(root) if target == "news" else _wizard_ops(root, slug, target)
            if not ops:
                click.echo("nothing to change")
                return
            yes = False
        if target == "news":
            plan = acf.plan_news(root, ops)
        elif target == "workspace":
            plan = acf.plan_workspace(root, slug, ops)
        else:
            plan = acf.plan_defaults(root, ops)
    except acf.ConfigureError as exc:
        raise click.ClickException(str(exc)) from exc

    if not plan.changes:
        click.echo("already configured that way; nothing to write")
        return
    _print_plan(plan, root)
    if not yes:
        interactive = not (assigns or sets or unsets or promotes or demotes)
        if not (interactive or sys.stdin.isatty()):
            raise click.ClickException("not written: pass --yes to confirm non-interactively")
        if not click.confirm("Write these changes?", default=False):
            click.echo("not written")
            return
    acf.write(plan)
    click.echo("written: " + ", ".join(str(c.path.relative_to(root)) for c in plan.changes))
