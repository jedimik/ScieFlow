# The `scieflow` menu

Run ScieFlow with no command in a terminal:

```bash
uv run scieflow        # or: uv run scieflow menu
```

Move with the **arrow keys**, tick boxes with **space**, confirm with
**enter**. Every screen has **← Back**; `Ctrl-C` also goes back.

```
› ScieFlow — what now?
 » Research         Literature review, gap discovery, paper review and drafting, or the loop.
   Experiment       Design campaigns with an agent, or run and inspect existing ones.
   What's new       Track what changed in the tools and topics you follow.
   Continue a run   New coordinator session, resume an earlier chat, or check its health.
   Agent settings   Which agent does each role, and each agent's model and effort.
   Workspace        List runs, health report, generated index.
   Chats            Back up and restore agent chats, skills and plugins.
   Quit
```

## What each part does

**Research** and **Experiment → Design a campaign** are agent work. The menu
asks what the run is about, suggests a slug (`YYYY-MM-your-topic`), shows who
will do each relevant role, and then hands the terminal to `claude` or
`codex` with a prepared prompt. You continue in that chat. Choose *Just show
the prompt* to paste it into a chat you already have open.

**Experiment → Run / Compare / Report**, **What's new**, **Workspace** and
**Chats** call the usual `scieflow` commands for you and return to the menu.
A campaign runs only after you confirm it was approved.

**Continue a run** lists every run with its kind and state. For the one you
pick you can:

- start a **new coordinator session** that resumes it from `status.yml`;
- **resume an earlier chat** about it — the menu searches your Claude and
  Codex history for chats that mention `workspace/<slug>` and reopens the one
  you choose (`claude --resume` / `codex resume`) in the directory it started in;
- see its **health report**, or change **agent settings for this run only**.

## Agent settings: where the change applies

The first question is always the scope, because it decides which file changes:

| Scope | Writes | Affects |
|---|---|---|
| All projects | `config/agents.yml`, `config/defaults.yml` | every run without its own override |
| One workspace | `workspace/<slug>/config.yml` (only differences from the defaults) | that run only |
| News | `config/news.yml` | `scieflow news` only |

Use **all projects** when you want a new standing default, e.g. "codex does
all reviews from now on". Use **one workspace** for a single project that
needs something different, e.g. more effort for one paper. When in doubt,
pick one workspace: it cannot surprise another run.

Then change any of:

- **Who does a role** — pick one agent, or tick several for fan-out roles
  such as search or cross-review. Support-tier agents (`agy`) are only
  allowed on primary-only roles as an explicit exception you confirm.
- **An agent's model** — from its menu in `config/agents.yml`, or type
  another your account offers.
- **An agent's effort** — codex: `low … max`; agy: `low/medium/high`;
  claude: `default` or `extended thinking` (adds
  `MAX_THINKING_TOKENS=32000` to its command).

Changes are queued and checked as you go; an invalid one (for example the
same agent as reviewer and submitter) is refused on the spot. **Review and
save** shows the exact diff and writes only after you confirm. Leaving with
changes queued asks before discarding them.

## For agents

Agents cannot press arrow keys. `uv run scieflow menu --json` prints the same
tree with the current settings, and `skills/scieflow-menu/SKILL.md` tells an
agent to offer these choices in its own question UI and apply them with
`scieflow agent configure … --yes`.

Without a terminal, `scieflow` prints its help and `scieflow menu` says to use
`--json`.
