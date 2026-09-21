# ScieFlow

Agent-driven research framework. A research run iterates
hypothesis → experiment → literature grounding → synthesis and accumulates a
research notebook that can be handed to the paper-draft workflow.

ScieFlow has four modules sharing one agent core:

| Module | What it does | CLI | Agent contract |
|---|---|---|---|
| [Experiments](experiments/index.md) | Containerized parameter campaigns, metrics, validation, reports | `scieflow experiment` | `src/scieflow/experiments/AGENTS.md` |
| [Research](research/index.md) | Literature review, gap discovery, paper review and drafting | `scieflow research` | `src/scieflow/research/AGENTS.md` |
| [News](news/index.md) | Track what changed in the tools and topics you follow | `scieflow news` | `src/scieflow/news/AGENTS.md` |
| [Chats](chats/index.md) | Selective backup and cross-machine restore of agent chats, skills and plugins | `scieflow chats` | `src/scieflow/chats/AGENTS.md` |

Agents are dispatched headless with `scieflow agent run`. Which agent does
each role, and with which model, is layered defaults → per-run overrides; see
[Agent configuration](agents.md) (`scieflow agent show` / `scieflow agent configure`). Run data lives in `workspace/<slug>/` and is synced with
DVC ([Storage](DVC_STORAGE.md)).

Start with the interactive menu — arrow keys, space and enter — to pick a
workflow, continue a run or change agent settings ([guide](menu.md)):

```bash
uv run scieflow
```

Install only what you use:

```bash
uv sync --all-extras                           # everything, incl. the news GUI
uv sync --extra research                       # literature work only
uv sync --extra news                           # news CLI only
uv sync --extra chats                          # chat backup/restore only
```
