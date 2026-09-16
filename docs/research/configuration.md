# Configuration

Two layers: a global registry (`config/agents.yml`) and optional per-run
overrides (`workspace/<slug>/config.yml`). Scripts and skills read these —
nothing else hardcodes models, flags, or Zotero targets.

## Global: `config/agents.yml`

```yaml
# Edit models/flags here; nothing else hardcodes them.
defaults:
  max_papers: 40
  max_review_rounds: 3
  max_debate_rounds: 2      # perspective debate: discussion rounds after propose
  max_gaps: 10              # gap-discovery: per-agent cap
  max_hypotheses: 5         # gap-discovery: per-agent cap
  auto_approve_outline: false   # paper-draft: pause for user outline approval
  zotero:
    library: user            # or "group:<id>"; workspace config.yml overrides

agents:
  claude:
    cmd: "claude -p --dangerously-skip-permissions --model {model} {prompt}"
    model: claude-fable-5
    tier: primary
    timeout_min: 15
    enabled: true
  codex:
    cmd: "codex exec --sandbox workspace-write --model {model} -c model_reasoning_effort={reasoning} {prompt}"
    model: gpt-5.6-sol
    reasoning: medium
    tier: primary
    timeout_min: 15
    enabled: true
  agy:
    cmd: "agy --print {prompt} --model {model} --dangerously-skip-permissions"
    # agy --print consumes --model as its value when {prompt} is absent
    stdin_cmd: "agy --model {model} --dangerously-skip-permissions"
    model: "Gemini 3.1 Pro (High)"
    tier: support
    capabilities: [web-search, large-context]
    timeout_min: 15
    enabled: true
  stub:
    cmd: "python -m scieflow.core.stub_agent {prompt}"
    tier: primary          # tests dispatch it into primary-only roles
    timeout_min: 1
    enabled: false            # test/dry-run only
```

### Agent fields

| Field | Meaning |
| --- | --- |
| `cmd` | Command template. `{model}` and `{prompt}` are substituted as single argv tokens (safe for multi-line prompts and model names with spaces). |
| `stdin_cmd` | *Optional.* Alternate template used when a prompt is too large for argv (delivered via stdin instead). Needed only for CLIs like `agy` whose flag parsing breaks when the prompt token is dropped. |
| `model` | Model passed as `{model}`. Omit if the CLI should use its account default. |
| `tier` | `primary` or `support` — routing rule 9 in AGENTS.md: comprehensive tasks dispatch primary agents only. |
| `capabilities` | *Optional.* Free-form hints (e.g. `web-search`) coordinators use when routing support-tier tasks. |
| `reasoning` | *Optional.* Substituted as `{reasoning}` in the cmd template (e.g. codex `model_reasoning_effort`). |
| `timeout_min` | Per-dispatch timeout in minutes. A timed-out agent is logged and the phase continues without it. |
| `enabled` | `false` excludes the agent from all runs by default. |
| `menu` | *Optional, read by coordinators, not code.* The provider menu shown to the user at the run configuration gate (AGENTS.md): available models, reasoning levels, and how each choice maps to `agent_overrides`. |

### How the runner picks argv vs. stdin

`scieflow agent run` embeds the prompt in argv until it exceeds
`SCIEFLOW_PROMPT_ARGV_LIMIT` bytes (default 100000; set the env var to
override). Above that it switches to stdin delivery — using `stdin_cmd` if the
agent defines one, otherwise dropping the `{prompt}` token from `cmd`. This
prevents the `Argument list too long` (E2BIG) failure on large manuscript
prompts.

## Per-run: `workspace/<slug>/config.yml`

Any key here overrides the global default **for that research only**:

The `agents:` run set, role keys, and `agent_overrides:` are normally written
by the coordinator after the **run configuration gate** (AGENTS.md), where it
recommends an agent/model/reasoning assignment and asks you to confirm or
adjust it.

```yaml
agents: [claude, agy]         # subset of agents for this run
agent_overrides:              # per-agent model/reasoning for this run only;
  claude:                     # applied by scieflow agent run to prompts under
    model: claude-opus-4-8    # workspace/<slug>/prompts/
  codex:
    reasoning: high           # fills {reasoning} in the cmd template
outline_agent: claude         # paper-draft: role assignments from the gate
consistency_agent: agy
max_papers: 30
journal: "Nature Methods"     # paper-review + paper-draft: target journal
reviewer: codex               # paper-review: role assignments
submitter: claude
scope: full                   # or: sections: [Introduction, Methods]
max_debate_rounds: 1          # gap-discovery: shorter debate for this run
max_gaps: 6                   # gap-discovery: per-agent caps
max_hypotheses: 3
literature_from: workspace/2026-07-other-topic   # gap-discovery: reuse a lit-review's findings
gaps_from: workspace/2026-07-other-topic         # paper-draft: import a gap report + DOI list
title: "My Article Title"     # paper-draft: filled into the LaTeX skeleton
authors: "A. Author, B. Author"
auto_approve_outline: false   # paper-draft: keep the outline approval gate
zotero:
  library: "group:1234567"    # personal ("user") or a group ID
  collection: "ScieFlow/2026-07-topic"
```

## Zotero target resolution

Each research — and each paper — can save citations to a **different** Zotero
library or group. The target is resolved in order:

1. Workspace `config.yml` → `zotero:` block
2. Global `config/agents.yml` → `defaults.zotero`
3. Fallback: your personal library (`user`)

The collection defaults to `ScieFlow/<slug>` when not specified. Export uses
the `zot` CLI (`--library user` or `--library group:<id>`), so anything `zot`
can reach, ScieFlow can target.

## Environment variables

| Variable | Effect |
| --- | --- |
| `SCIEFLOW_MAILTO` | Appended to the HTTP User-Agent for polite scholarly-API access (recommended). |
| `SCIEFLOW_PROMPT_ARGV_LIMIT` | Byte threshold above which prompts go via stdin (default 100000). |
