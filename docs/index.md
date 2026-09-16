# ScieFlow

Agent-driven research framework. A research run iterates
hypothesis → experiment → literature grounding → synthesis and accumulates a
research notebook that can be handed to the paper-draft workflow.

ScieFlow has two modules sharing one agent core:

| Module | What it does | CLI | Agent contract |
|---|---|---|---|
| [Experiments](experiments/index.md) | Containerized parameter campaigns, metrics, validation, reports | `scieflow experiment` | `src/scieflow/experiments/AGENTS.md` |
| [Research](research/index.md) | Literature review, gap discovery, paper review and drafting | `scieflow research` | `src/scieflow/research/AGENTS.md` |

Agents are dispatched headless with `scieflow agent run`, configured in
`config/agents.yml`. Run data lives in `workspace/<slug>/` and is synced with
DVC ([Storage](DVC_STORAGE.md)).

Install only what you use:

```bash
uv sync --extra experiments --extra research   # both modules
uv sync --extra research                       # literature work only
```
