"""`scieflow` menu — pick what to do with arrow keys, space and enter.

Everything here is a front end: settings go through `agent_configure`
(diff shown, confirmation required), CLI actions call the existing click
commands, and agent-driven workflows hand over to a coordinator session
(`claude` or `codex`) with a prepared prompt. `scieflow menu --json` prints
the same option tree for agents (skills/scieflow-menu/SKILL.md), so the TUI
and the skill never drift apart.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import click

from scieflow.core import config as config_mod

BACK = object()
COORDINATORS = ("claude", "codex")  # primary tier; agy is support-only (rule 10)
EXTENDED_THINKING = "env MAX_THINKING_TOKENS=32000 "


# -- option tree (single source for the TUI and `--json`) --------------------
@dataclass
class Item:
    key: str
    title: str
    description: str
    how: str  # "agent" = coordinator session, "cli" = scieflow command, "settings"


@dataclass
class Section:
    key: str
    title: str
    description: str
    items: list[Item] = field(default_factory=list)


WORKFLOWS: dict[str, dict] = {
    "lit-review": {
        "skill": "src/scieflow/research/skills/lit-review/SKILL.md",
        "ask": "Topic or question to review",
        "roles": ["research.search", "research.cross-review"],
    },
    "gap-discovery": {
        "skill": "src/scieflow/research/skills/gap-discovery/SKILL.md",
        "ask": "Field or question to find gaps in",
        "roles": ["research.search", "research.gap-analysis", "research.debate"],
    },
    "paper-review": {
        "skill": "src/scieflow/research/skills/paper-review/SKILL.md",
        "ask": "Manuscript to review (path) and target journal",
        "roles": ["research.journal-profile", "research.reviewer", "research.submitter"],
    },
    "paper-draft": {
        "skill": "src/scieflow/research/skills/paper-draft/SKILL.md",
        "ask": "What to draft from (a finished run slug, or a topic)",
        "roles": ["research.outline", "research.draft-authors",
                  "research.cross-review", "research.consistency"],
    },
    "research-loop": {
        "skill": "skills/research-loop/SKILL.md",
        "ask": "Research question / goal for the loop",
        "roles": ["loop.experiment", "loop.literature", "loop.paper-draft"],
    },
    "experiment-design": {
        "skill": "src/scieflow/experiments/skills/experiment-designer/SKILL.md",
        "ask": "What the experiment campaign should test",
        "roles": ["loop.experiment"],
    },
}

TREE: list[Section] = [
    Section("research", "Research", "Literature review, gap discovery, paper review and "
            "drafting, or the full experiment ↔ literature loop.", [
        Item("lit-review", "Literature review", "Search, validate and synthesise papers.", "agent"),
        Item("gap-discovery", "Gap discovery", "Find open questions and debate them.", "agent"),
        Item("paper-review", "Paper review", "Journal-style review of a manuscript.", "agent"),
        Item("paper-draft", "Paper draft", "Two independent drafts, cross-review, merge.", "agent"),
        Item("research-loop", "Research loop", "Hypothesis → experiment → literature → "
             "synthesis, iterated.", "agent"),
    ]),
    Section("experiment", "Experiment", "Design campaigns with an agent, or run and "
            "inspect existing ones.", [
        Item("experiment-design", "Design a campaign", "Agent proposes a campaign for approval.",
             "agent"),
        Item("sweep", "Run a campaign", "`scieflow experiment sweep` on an approved campaign.",
             "cli"),
        Item("compare", "Compare runs", "`scieflow experiment compare` for a campaign.", "cli"),
        Item("report", "Campaign report", "`scieflow experiment report` for a campaign.", "cli"),
    ]),
    Section("news", "What's new", "Track what changed in the tools and topics you follow.", [
        Item("news-run", "Run", "All interests, chosen interests, or a group.", "cli"),
        Item("news-status", "Status", "Interests and when each was last checked.", "cli"),
        Item("news-export", "Export latest report", "Markdown into workspace/news/reports/.",
             "cli"),
        Item("news-gui", "Open the web GUI", "Local browser UI (extra: news-gui).", "cli"),
    ]),
    Section("continue", "Continue a run", "Pick a run: new coordinator session on it, "
            "resume an earlier chat about it, or check its health.", []),
    Section("settings", "Agent settings", "Which agent does each role, and each agent's "
            "model and effort — for all projects, one workspace, or news.", []),
    Section("workspace", "Workspace", "List runs, health report, generated index.", [
        Item("ws-list", "List runs", "`scieflow workspace list`.", "cli"),
        Item("ws-doctor", "Health report for a run", "`scieflow workspace doctor`.", "cli"),
        Item("ws-index", "Write INDEX.md", "`scieflow workspace index`.", "cli"),
        Item("ws-sync-status", "What would a sync upload?",
             "New files since the last sync, and the big ones.", "cli"),
    ]),
    Section("chats", "Chats", "Back up and restore agent chats, skills and plugins.", [
        Item("chats-scan", "Scan", "Read-only inventory.", "cli"),
        Item("chats-backup", "Back up", "Pick chats → encrypted bundle.", "cli"),
        Item("chats-restore", "Restore (dry run first)", "Restore a bundle on this machine.",
             "cli"),
        Item("chats-push", "Push to DVC storage", "Pick agents and projects, bundle, upload.",
             "cli"),
        Item("chats-pull", "Pull from DVC storage", "Fetch a bundle and restore it.", "cli"),
    ]),
]

SCOPES = {
    "default": ("All projects (ScieFlow defaults)",
                "Writes config/agents.yml and config/defaults.yml. Every run without its "
                "own override follows it."),
    "workspace": ("One workspace only",
                  "Writes workspace/<slug>/config.yml. Only that run changes; it stores "
                  "just the differences from the defaults."),
    "news": ("News only", "Writes config/news.yml. Affects `scieflow news` and nothing else."),
}


# -- UI: questionary with a numbered fallback -------------------------------
class UI:
    def __init__(self) -> None:
        self.q = None
        if sys.stdin.isatty() and sys.stdout.isatty():
            try:
                import questionary

                self.q = questionary
            except ImportError:
                self.q = None

    def select(self, message: str, choices: list[tuple[str, object]], back: bool = True):
        if back:
            choices = [*choices, ("← Back", BACK)]
        if self.q is not None:
            answer = self.q.select(
                message,
                choices=[self.q.Choice(title=label, value=value) for label, value in choices],
                use_shortcuts=False,
                qmark="›",
            ).ask()
            return BACK if answer is None else answer
        click.echo(message)
        for i, (label, _) in enumerate(choices, 1):
            click.echo(f"  {i}. {label}")
        while True:
            raw = click.prompt("Choose", default=str(len(choices)) if back else None)
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return choices[int(raw) - 1][1]
            click.echo(f"  choose 1-{len(choices)}")

    def checkbox(self, message: str, choices: list[tuple[str, object]], checked=()):
        if self.q is not None:
            answer = self.q.checkbox(
                message,
                choices=[self.q.Choice(title=l, value=v, checked=v in checked)
                         for l, v in choices],
                qmark="›",
            ).ask()
            return None if answer is None else list(answer)
        click.echo(message)
        for i, (label, value) in enumerate(choices, 1):
            click.echo(f"  {i}. [{'x' if value in checked else ' '}] {label}")
        default = ",".join(str(i) for i, (_, v) in enumerate(choices, 1) if v in checked)
        raw = click.prompt("Numbers (comma-separated)", default=default or "")
        picked = {int(p) for p in raw.replace(" ", "").split(",") if p.isdigit()}
        return [v for i, (_, v) in enumerate(choices, 1) if i in picked]

    def text(self, message: str, default: str = "") -> str | None:
        if self.q is not None:
            answer = self.q.text(message, default=default, qmark="›").ask()
            return None if answer is None else answer.strip()
        return click.prompt(message, default=default or None, show_default=bool(default)).strip()

    def confirm(self, message: str, default: bool = False) -> bool:
        if self.q is not None:
            answer = self.q.confirm(message, default=default, qmark="›").ask()
            return bool(answer)
        return click.confirm(message, default=default)


# -- helpers ----------------------------------------------------------------
def _root() -> Path:
    return config_mod.repo_root()


def run_cli(args: list[str]) -> None:
    """Call an existing `scieflow` command in-process, then come back."""
    from scieflow.cli import main

    click.echo(click.style(f"$ scieflow {' '.join(args)}", dim=True))
    try:
        main.main(args=args, prog_name="scieflow", standalone_mode=False)
    except click.exceptions.Abort:
        click.echo("cancelled")
    except click.ClickException as exc:
        exc.show()
    except SystemExit:
        pass


def suggest_slug(topic: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return f"{date.today():%Y-%m}-{words[:48].rstrip('-')}" if words else f"{date.today():%Y-%m}-run"


def prompt_for(workflow: str, subject: str, slug: str) -> str:
    spec = WORKFLOWS[workflow]
    if workflow == "research-loop":
        return (f"Read AGENTS.md, then follow {spec['skill']} to start a new research run. "
                f"Goal: {subject}. Use workspace slug `{slug}`. Draft goal.md with me and "
                "ask me for the approval mode before initialising the run.")
    if workflow == "experiment-design":
        return (f"Read AGENTS.md and src/scieflow/experiments/AGENTS.md. Using {spec['skill']}, "
                f"propose an experiment campaign for: {subject}. Record runs under "
                f"workspace/{slug}/experiments. Do not run anything before I approve it.")
    return (f"Read AGENTS.md and src/scieflow/research/AGENTS.md, then run the {workflow} "
            f"workflow ({spec['skill']}) on: {subject}. Use workspace slug `{slug}`. Start "
            "with the run configuration gate and confirm staffing with me.")


def resume_prompt(slug: str, kind: str) -> str:
    if kind == "loop":
        return (f"Read AGENTS.md. Resume the research run workspace/{slug}: read its "
                "status.yml and continue from the first phase not done, per "
                "skills/research-loop/SKILL.md.")
    skill = WORKFLOWS.get(kind, {}).get("skill", "src/scieflow/research/AGENTS.md")
    return (f"Read AGENTS.md and src/scieflow/research/AGENTS.md. Resume the {kind} run "
            f"workspace/{slug}: read its status.yml and log.md and continue per {skill}.")


def launch(ui: UI, prompt: str, cwd: Path | None = None) -> None:
    """Hand the terminal to a coordinator agent, or just print the prompt."""
    available = [c for c in COORDINATORS if shutil.which(c)]
    choices = [(f"Start a {c} session with this prompt", c) for c in available]
    choices.append(("Just show the prompt (paste it into a chat you already have)", "print"))
    click.echo("\n" + click.style("Prompt:", bold=True) + f"\n  {prompt}\n")
    pick = ui.select("How do you want to continue?", choices)
    if pick is BACK:
        return
    if pick == "print":
        click.echo(prompt)
        raise SystemExit(0)
    os.chdir(cwd or _root())
    os.execvp(pick, [pick, prompt])


# -- continue a run ---------------------------------------------------------
@dataclass
class FoundChat:
    tool: str
    session_id: str
    cwd: str
    when: float
    title: str


_UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")


def find_chats(slug: str, limit: int = 12) -> list[FoundChat]:
    """Claude and Codex sessions that mention workspace/<slug>, newest first."""
    from scieflow.chats.stores.base import home

    needle = f"workspace/{slug}"
    found: list[FoundChat] = []
    for tool, base in (("claude", home() / ".claude" / "projects"),
                       ("codex", home() / ".codex" / "sessions")):
        if not base.is_dir() or shutil.which("grep") is None:
            continue
        try:
            done = subprocess.run(["grep", "-rlF", "--include=*.jsonl", needle, str(base)],
                                  capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        for line in done.stdout.splitlines():
            path = Path(line)
            if "/subagents/" in line:
                continue
            match = _UUID.search(path.name)
            if not match:
                continue
            cwd, title = _chat_header(path, tool)
            found.append(FoundChat(tool, match.group(1), cwd or str(_root()),
                                   path.stat().st_mtime, title))
    return sorted(found, key=lambda c: -c.when)[:limit]


def _chat_header(path: Path, tool: str) -> tuple[str | None, str]:
    cwd, title = None, ""
    try:
        with path.open("rb") as fh:
            for index, raw in enumerate(fh):
                if index > 60:
                    break
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if tool == "codex":
                    payload = row.get("payload") or {}
                    cwd = cwd or payload.get("cwd")
                    continue
                cwd = cwd or row.get("cwd")
                message = row.get("message") or {}
                if not title and row.get("type") == "user" and isinstance(message.get("content"), str):
                    title = " ".join(message["content"].split())[:70]
    except OSError:
        pass
    return cwd, title


def continue_run(ui: UI) -> None:
    from scieflow.core import workspace as ws

    runs = ws.list_runs()
    if not runs:
        click.echo("no runs in workspace/")
        return
    run = ui.select("Which run?", [
        (f"{r.updated or '    ?     '}  {r.kind:<13} {r.slug}  — {r.state}", r) for r in runs
    ])
    if run is BACK:
        return
    action = ui.select(f"{run.slug}", [
        ("Start a new coordinator session on it", "new"),
        ("Resume an earlier chat about it (searches your Claude/Codex history)", "resume"),
        ("Health report", "doctor"),
        ("Agent settings for this run only", "settings"),
    ])
    if action is BACK:
        return
    if action == "new":
        launch(ui, resume_prompt(run.slug, run.kind))
    elif action == "doctor":
        run_cli(["workspace", "doctor", run.slug])
    elif action == "settings":
        agent_settings(ui, scope="workspace", slug=run.slug)
    else:
        click.echo("searching chat history…")
        chats = find_chats(run.slug)
        if not chats:
            click.echo(f"no Claude or Codex chat mentions workspace/{run.slug}")
            return
        from datetime import datetime

        pick = ui.select("Which chat?", [
            (f"{datetime.fromtimestamp(c.when):%Y-%m-%d %H:%M}  {c.tool:<6} "
             f"{c.session_id[:8]}  {c.title or Path(c.cwd).name}", c) for c in chats
        ])
        if pick is BACK:
            return
        if not shutil.which(pick.tool):
            click.echo(f"{pick.tool} is not on PATH")
            return
        cmd = ["claude", "--resume", pick.session_id] if pick.tool == "claude" \
            else ["codex", "resume", pick.session_id]
        click.echo(f"$ cd {pick.cwd} && {' '.join(cmd)}")
        os.chdir(pick.cwd if Path(pick.cwd).is_dir() else _root())
        os.execvp(cmd[0], cmd)


# -- agent settings ---------------------------------------------------------
DEFAULT_LEVELS = ["low", "medium", "high"]


def effort_levels(eff, name: str) -> list[str]:
    """Effort choices for an agent — the same list for the TUI and `--json`."""
    menu = eff.menus.get(name) or {}
    cmd = eff.agents.get(name, {}).get("cmd")
    if cmd and "{reasoning}" in str(cmd.value):
        levels = (menu.get("reasoning") or {}).get("levels")
        return [str(level) for level in levels] if levels else list(DEFAULT_LEVELS)
    return ["default", "extended-thinking"]


def _agent_label(name: str, eff) -> str:
    tier = eff.tiers.get(name) or "?"
    model = eff.agents.get(name, {}).get("model")
    return f"{name}  ({tier}{', ' + str(model.value) if model else ''})"


def agent_settings(ui: UI, scope: str | None = None, slug: str | None = None) -> None:
    from scieflow.core import agent_config as ac
    from scieflow.core import agent_configure as acf
    from scieflow.core.cli import _print_plan

    root = _root()
    if scope is None:
        scope = ui.select("Apply changes to…", [
            (f"{title} — {why}", key) for key, (title, why) in SCOPES.items()
        ])
        if scope is BACK:
            return
    if scope == "workspace" and slug is None:
        from scieflow.core import workspace as ws

        runs = ws.list_runs()
        slug = ui.select("Which run?", [(f"{r.kind:<13} {r.slug}", r.slug) for r in runs])
        if slug is BACK:
            return
    if scope == "news":
        return _news_settings(ui, root)

    ops: list = []

    def plan(candidate):
        return (acf.plan_workspace(root, slug, candidate) if scope == "workspace"
                else acf.plan_defaults(root, candidate))

    def attempt(new: list) -> None:
        try:
            plan(ops + new)
        except acf.ConfigureError as exc:
            if not (new[-1].kind == "assign" and "primary-only unless" in str(exc)):
                click.echo(click.style(f"  not applied: {exc}", fg="red"))
                return
            click.echo(f"  {new[-1].key} is primary-only and your choice includes a support "
                       "agent (AGENTS.md rule 10).")
            if not ui.confirm(f"  Allow it as primary for {new[-1].key} only "
                              "(explicit exception)?", default=False):
                return
            new = [acf.Op("promote", new[-1].key), *new]
            try:
                plan(ops + new)
            except acf.ConfigureError as exc2:
                click.echo(click.style(f"  not applied: {exc2}", fg="red"))
                return
        ops.extend(new)
        for op in new:
            click.echo(click.style(f"  queued: {op.kind} {op.key}"
                                   + ("" if op.value is None else f" = {op.value}"), fg="green"))

    title = f"workspace/{slug}" if scope == "workspace" else "all projects"
    while True:
        eff = ac.resolve(root, slug if scope == "workspace" else None)
        action = ui.select(f"Agent settings — {title}  ({len(ops)} change(s) queued)", [
            ("Who does a role (e.g. who reviews, who drafts)", "role"),
            ("An agent's model", "model"),
            ("An agent's effort / reasoning", "effort"),
            ("Show current settings", "show"),
            ("Review and save queued changes", "save"),
        ], back=True)
        if action is BACK:
            if ops and not ui.confirm("Discard queued changes?", default=False):
                continue
            return
        if action == "show":
            click.echo(ac.format_table(eff, title))
        elif action == "role":
            _pick_role(ui, eff, attempt)
        elif action in ("model", "effort"):
            agents = [n for n in eff.agents if (eff.agents[n].get("enabled")
                      and eff.agents[n]["enabled"].value) and n != "stub"]
            name = ui.select("Which agent?", [(_agent_label(n, eff), n) for n in agents])
            if name is BACK:
                continue
            if action == "model":
                _pick_model(ui, eff, name, attempt)
            else:
                _pick_effort(ui, eff, name, attempt)
        elif action == "save":
            if not ops:
                click.echo("nothing queued")
                continue
            result = plan(ops)
            _print_plan(result, root)
            if ui.confirm("Write these changes?", default=False):
                acf.write(result)
                click.echo(click.style("saved", fg="green"))
                return


def _pick_role(ui: UI, eff, attempt) -> None:
    from scieflow.core import agent_config as ac
    from scieflow.core import agent_configure as acf

    role = ui.select("Which role?", [
        (f"{r:<26} now: {eff.value(r)}  — {spec.help}", r) for r, spec in ac.ROLES.items()
    ])
    if role is BACK:
        return
    spec = ac.ROLES[role]
    enabled = [n for n, fields in eff.agents.items()
               if n != "stub" and fields.get("enabled") and fields["enabled"].value]
    current = eff.value(role)
    if spec.many:
        picked = ui.checkbox(f"{role}: agents (space toggles)",
                             [(_agent_label(n, eff), n) for n in enabled],
                             checked=current if isinstance(current, list) else [current])
        if not picked:
            return
        attempt([acf.Op("assign", role, picked)])
    else:
        picked = ui.select(f"{role}: agent", [(_agent_label(n, eff), n) for n in enabled])
        if picked is BACK:
            return
        attempt([acf.Op("assign", role, picked)])


def _pick_model(ui: UI, eff, name: str, attempt) -> None:
    from scieflow.core import agent_configure as acf

    menu = eff.menus.get(name) or {}
    models = [str(m) for m in menu.get("models") or []]
    current = eff.agents[name].get("model")
    choices = [(m + ("  (current)" if current and current.value == m else ""), m) for m in models]
    choices.append(("Other… (type a model name)", "__other__"))
    if menu.get("models_note"):
        click.echo(f"  note: {menu['models_note']}")
    model = ui.select(f"{name}: model", choices)
    if model is BACK:
        return
    if model == "__other__":
        model = ui.text("Model name", default=str(current.value) if current else "")
        if not model:
            return
    attempt([acf.Op("set", f"{name}.model", model)])


def _pick_effort(ui: UI, eff, name: str, attempt) -> None:
    from scieflow.core import agent_configure as acf

    menu = eff.menus.get(name) or {}
    reasoning = menu.get("reasoning") or {}
    cmd = eff.agents[name].get("cmd")
    cmd_value = str(cmd.value) if cmd else ""
    if "{reasoning}" in cmd_value:
        levels = effort_levels(eff, name)
        current = eff.agents[name].get("reasoning")
        level = ui.select(f"{name}: effort", [
            (lv + ("  (current)" if current and current.value == lv else ""), lv) for lv in levels
        ])
        if level is not BACK:
            attempt([acf.Op("set", f"{name}.reasoning", level)])
        return
    # Claude has no reasoning flag: extended thinking is an env prefix on cmd.
    on = cmd_value.startswith(EXTENDED_THINKING)
    if reasoning.get("how"):
        click.echo(f"  how: {reasoning['how']}")
    level = ui.select(f"{name}: effort", [
        ("default" + ("" if on else "  (current)"), "default"),
        ("extended thinking (32k thinking tokens)" + ("  (current)" if on else ""), "extended"),
    ])
    if level is BACK:
        return
    ops = []
    for field_name in ("cmd", "stdin_cmd"):
        setting = eff.agents[name].get(field_name)
        if not setting:
            continue
        plain = str(setting.value).removeprefix(EXTENDED_THINKING)
        value = EXTENDED_THINKING + plain if level == "extended" else plain
        if value != str(setting.value):
            ops.append(acf.Op("set", f"{name}.{field_name}", value))
    if ops:
        attempt(ops)
    else:
        click.echo("  already set")


def _news_settings(ui: UI, root: Path) -> None:
    from scieflow.core import agent_configure as acf
    from scieflow.core.cli import _print_plan
    from scieflow.news.agents import curated_models
    from scieflow.news.config import VALID_AGENTS, VALID_REASONING

    try:
        current = acf.news_settings(root)
    except (acf.ConfigureError, OSError) as exc:
        click.echo(f"news settings unavailable: {exc}")
        return
    ops: list = []
    while True:
        field_name = ui.select(
            f"News settings — agent={current.get('agent')} model={current.get('model')} "
            f"reasoning={current.get('reasoning')}  ({len(ops)} queued)",
            [("Agent", "agent"), ("Model", "model"), ("Reasoning", "reasoning"),
             ("Review and save", "save")])
        if field_name is BACK:
            return
        if field_name == "save":
            if not ops:
                click.echo("nothing queued")
                continue
            try:
                result = acf.plan_news(root, ops)
            except acf.ConfigureError as exc:
                click.echo(click.style(f"not applied: {exc}", fg="red"))
                ops.clear()
                continue
            _print_plan(result, root)
            if ui.confirm("Write these changes?", default=False):
                acf.write(result)
                click.echo(click.style("saved", fg="green"))
            return
        if field_name == "agent":
            value = ui.select("Agent", [(a, a) for a in VALID_AGENTS])
            if value == "agy":
                click.echo("  note: agy is support tier; news only uses it if you choose it.")
        elif field_name == "model":
            agent = next((op.value for op in reversed(ops) if op.key == "agent"),
                         current.get("agent") or "claude")
            value = ui.select("Model", [(m, m) for m in curated_models(agent)])
        else:
            value = ui.select("Reasoning", [(r, r) for r in VALID_REASONING])
        if value is BACK:
            continue
        candidate = ops + [acf.Op("set", field_name, value)]
        try:
            acf.plan_news(root, candidate)
        except acf.ConfigureError as exc:
            click.echo(click.style(f"  not applied: {exc}", fg="red"))
            continue
        ops = candidate
        current[field_name] = value


# -- section handlers --------------------------------------------------------
def start_workflow(ui: UI, workflow: str) -> None:
    from scieflow.core import agent_config as ac

    spec = WORKFLOWS[workflow]
    subject = ui.text(spec["ask"])
    if not subject:
        return
    slug = ui.text("Workspace slug", default=suggest_slug(subject))
    if not slug:
        return
    if (_root() / "workspace" / slug).exists():
        click.echo(f"  workspace/{slug} already exists — use 'Continue a run' to resume it.")
        if not ui.confirm("Use it anyway?", default=False):
            return
    eff = ac.resolve(_root())
    click.echo("\nStaffing (defaults; the agent confirms it with you for this run):")
    for role in spec["roles"]:
        click.echo(f"  {role:<26} {eff.value(role)}")
    if ui.confirm("Change the defaults for all projects first?", default=False):
        agent_settings(ui, scope="default")
    launch(ui, prompt_for(workflow, subject, slug))


def _pick_campaign_dir(ui: UI):
    from scieflow.core import workspace as ws

    root = _root()
    dirs = []
    for run in ws.list_runs():
        base = root / "workspace" / run.slug / "experiments"
        if base.is_dir():
            dirs += [p for p in sorted(base.iterdir()) if (p / "campaign.yaml").exists()]
    if not dirs:
        click.echo("no recorded campaigns under workspace/*/experiments/")
        return BACK
    return ui.select("Which campaign?", [(str(p.relative_to(root)), p) for p in dirs])


def experiment_item(ui: UI, key: str) -> None:
    from scieflow.core import workspace as ws

    if key in ("compare", "report"):
        picked = _pick_campaign_dir(ui)
        if picked is not BACK:
            run_cli(["experiment", key, str(picked)])
        return
    runs = [r for r in ws.list_runs() if r.kind == "loop"]
    slug = ui.select("Record runs in which workspace?", [(r.slug, r.slug) for r in runs])
    if slug is BACK:
        return
    base = _root() / "workspace" / slug
    found = sorted({*base.glob("iterations/*/campaign*.y*ml"), *base.glob("campaign*.y*ml"),
                    *base.glob("experiments/*/campaign.yaml")})
    choices = [(str(p.relative_to(_root())), str(p)) for p in found]
    choices.append(("Other… (type a path)", "__other__"))
    campaign = ui.select("Which approved campaign?", choices)
    if campaign is BACK:
        return
    if campaign == "__other__":
        campaign = ui.text("Campaign YAML path")
        if not campaign:
            return
    click.echo("Experiments run only after you approved the campaign (experiments/AGENTS.md).")
    if ui.confirm(f"Run {campaign} now?", default=False):
        run_cli(["experiment", "sweep", "-c", campaign,
                 "--experiments-dir", f"workspace/{slug}/experiments"])


def news_item(ui: UI, key: str) -> None:
    if key == "news-status":
        return run_cli(["news", "status"])
    if key == "news-export":
        return run_cli(["news", "export", "--latest"])
    if key == "news-gui":
        return run_cli(["news", "gui"])
    mode = ui.select("Run what?", [("All interests", "all"), ("Choose interests", "pick"),
                                   ("A group", "group")])
    if mode is BACK:
        return
    if mode == "all":
        return run_cli(["news", "run"])
    try:
        from scieflow.news.config import load_config

        cfg = load_config(_root() / "config" / "news.yml")
    except Exception as exc:  # noqa: BLE001 — surfaced to the user as-is
        click.echo(f"news config unavailable: {exc}")
        return
    if mode == "pick":
        names = ui.checkbox("Interests (space toggles)", [(i.name, i.name) for i in cfg.interests])
        if names:
            run_cli(["news", "run", *[a for n in names for a in ("--interest", n)]])
    else:
        groups = [g.name for g in cfg.groups]
        if not groups:
            click.echo("no groups in config/news.yml")
            return
        group = ui.select("Group", [(g, g) for g in groups])
        if group is not BACK:
            run_cli(["news", "run", "--group", group])


def workspace_item(ui: UI, key: str) -> None:
    from scieflow.core import workspace as ws

    if key == "ws-list":
        return run_cli(["workspace", "list"])
    if key == "ws-index":
        return run_cli(["workspace", "index"])
    if key == "ws-sync-status":
        return run_cli(["workspace", "sync-status"])
    slug = ui.select("Which run?", [(f"{r.kind:<13} {r.slug}", r.slug) for r in ws.list_runs()])
    if slug is not BACK:
        run_cli(["workspace", "doctor", slug])


def chats_item(ui: UI, key: str) -> None:
    if key in ("chats-push", "chats-pull"):
        # The wrapper scripts own the agent/project pickers; keep one copy.
        script = _root() / "scripts" / f"{key.replace('chats-', 'chats-')}.sh"
        click.echo(click.style(f"$ {script.relative_to(_root())}", dim=True))
        subprocess.run([str(script)], cwd=_root())
        return None
    if key == "chats-scan":
        return run_cli(["chats", "scan"])
    if key == "chats-backup":
        return run_cli(["chats", "backup"])
    bundle = ui.text("Bundle path")
    if bundle:
        run_cli(["chats", "restore", bundle])


def run_menu() -> None:
    ui = UI()
    handlers = {
        "research": start_workflow,
        "experiment": lambda u, k: start_workflow(u, k) if k == "experiment-design"
        else experiment_item(u, k),
        "news": news_item,
        "workspace": workspace_item,
        "chats": chats_item,
    }
    while True:
        section = ui.select("ScieFlow — what now?", [
            (f"{s.title:<16} {s.description}", s) for s in TREE
        ] + [("Quit", None)], back=False)
        if section is None or section is BACK:
            return
        try:
            if section.key == "continue":
                continue_run(ui)
            elif section.key == "settings":
                agent_settings(ui)
            else:
                item = ui.select(section.title, [
                    (f"{i.title:<26} {i.description}", i) for i in section.items
                ])
                if item is not BACK:
                    handlers[section.key](ui, item.key)
        except KeyboardInterrupt:
            click.echo("")
        click.echo("")


# -- JSON for agents ---------------------------------------------------------
def menu_json() -> dict:
    from dataclasses import asdict

    from scieflow.core import agent_config as ac
    from scieflow.core import service
    from scieflow.core import workspace as ws
    from scieflow.core.project import Project

    root = _root()
    eff = ac.resolve(root)
    agents = {}
    for name in eff.agents:
        if name == "stub":
            continue
        menu = eff.menus.get(name) or {}
        reasoning = menu.get("reasoning") or {}
        agents[name] = {
            "tier": eff.tiers.get(name),
            "enabled": bool(eff.agents[name].get("enabled") and eff.agents[name]["enabled"].value),
            "models": menu.get("models") or [],
            "models_note": menu.get("models_note"),
            "effort_levels": effort_levels(eff, name),
            "effort_how": reasoning.get("how"),
        }
    return {
        "sections": [asdict(s) for s in TREE],
        "workflows": {k: {**v, "prompt_template": prompt_for(k, "<subject>", "<slug>")}
                      for k, v in WORKFLOWS.items()},
        "agent_settings": {
            "scopes": {k: {"title": t, "effect": e} for k, (t, e) in SCOPES.items()},
            "roles": {r: {"help": s.help, "many": s.many, "support_ok": s.support_ok}
                      for r, s in ac.ROLES.items()},
            "agents": agents,
            "effective_defaults": eff.to_json(),
            "apply_with": {
                "default": "uv run scieflow agent configure --assign ROLE=AGENT "
                           "--set AGENT.FIELD=VALUE --yes",
                "workspace": "uv run scieflow agent configure --workspace <slug> "
                             "--assign ROLE=AGENT --set AGENT.FIELD=VALUE --yes",
                "news": "uv run scieflow agent configure --news --set FIELD=VALUE --yes",
            },
        },
        "runs": service.list_runs(Project.discover()),
        "resume_prompt_template": resume_prompt("<slug>", "loop"),
    }


@click.command("menu")
@click.option("--json", "as_json", is_flag=True,
              help="Print the option tree and current settings (for agents).")
def menu(as_json):
    """Interactive menu: research, experiments, news, settings, runs, chats."""
    if as_json:
        click.echo(json.dumps(menu_json(), indent=2, default=str))
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise click.ClickException("the menu needs a terminal; agents use `scieflow menu --json`")
    run_menu()
