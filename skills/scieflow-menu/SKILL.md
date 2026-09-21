---
name: scieflow-menu
description: Show the user what ScieFlow can do and let them pick — research workflows, experiments, what's new, continuing a run, agent settings (agent per role, model, effort; all projects vs one workspace vs news), workspace health, chat backups. Use when the user asks "what can ScieFlow do", "show options", "open the menu", "what should we do next", or wants to change which agent/model/effort is used.
---

# ScieFlow menu (for agents)

The user has an arrow-key menu (`uv run scieflow` in a terminal). You cannot
press keys in it, so you offer the **same options** with your own choice UI and
apply the answers with non-interactive commands. The menu and this skill share
one source of truth — never invent options that are not in it.

## 1. Load the tree

```bash
uv run scieflow menu --json
```

It returns `sections` (top-level choices and their items), `workflows`
(skill path, what to ask, relevant roles, prompt template), `agent_settings`
(scopes, roles, per-agent models and effort levels, current effective
defaults, the exact commands to apply) and `runs` (every run with kind and
state).

## 2. Ask, one level at a time

Use your structured-question tool (AskUserQuestion in Claude Code; a short
numbered list elsewhere). Top level = the section titles with their
one-line description. Drill down only into what they picked. Keep each
question to at most four options; if a list is longer (runs, roles), show the
most relevant four and let them type another.

## 3. Act

| Picked | Do |
|---|---|
| A research workflow or the research loop | Collect `workflows.<key>.ask` and a slug (`YYYY-MM-kebab`), then follow that workflow's `skill` yourself — you are the coordinator. Its run configuration gate still applies. |
| Design a campaign | Follow `src/scieflow/experiments/skills/experiment-designer/SKILL.md`; nothing runs before approval. |
| Run / compare / report a campaign | `uv run scieflow experiment sweep -c <yaml> --experiments-dir workspace/<slug>/experiments` (only an approved campaign), `experiment compare|report <campaign-dir>`. |
| What's new | `uv run scieflow news run [--interest X]… [--group G]`, `news status`, `news export --latest`. User-invoked only (news AGENTS.md). |
| Continue a run | Read `workspace/<slug>/status.yml` and resume per the kind's skill (`runs[].kind`: `loop` → `skills/research-loop/SKILL.md`). To reopen an old chat instead, tell the user to pick *Continue a run → Resume an earlier chat* in `uv run scieflow`. |
| Workspace | `uv run scieflow workspace list | doctor <slug> | index`. |
| Chats | `uv run scieflow chats scan`. Backups, restores and push/pull are the user's to run (AGENTS.md rule 14) — give them the command. |

## 4. Agent settings — scope first, always

Ask **where the change applies** before anything else, and say what it writes:

- **All projects** → `config/agents.yml` / `config/defaults.yml`; every run
  without its own override follows it.
- **One workspace** → `workspace/<slug>/config.yml`; only that run.
- **News** → `config/news.yml`; only `scieflow news`.

Then role → agent, or agent → model / effort, using only the values listed in
`agent_settings.agents.<name>.models` / `effort_levels` (a model outside the
list only if the user names it). Claude's effort is `default` or
`extended-thinking`; the latter means prefixing its `cmd`/`stdin_cmd` with
`env MAX_THINKING_TOKENS=32000 `.

Apply with the matching `agent_settings.apply_with` command plus `--yes`, e.g.

```bash
uv run scieflow agent configure --workspace 2026-09-job1-posthoc-wta \
  --set codex.reasoning=xhigh --yes
```

Show the user the diff that command prints. A refusal (tier rule 10, invalid
combination such as reviewer = submitter) is a boundary: report it. Never add
`--promote` unless the user asked for that exception in so many words.

## Rules that still bind

AGENTS.md rules 10 (tiers), 13 (agent selection only through `agent
configure`) and 14 (chats are the user's) apply unchanged. This skill only
presents choices; it never widens what you may do.
