# scite: evidence for draft review

ScieFlow checks a manuscript's citations against the literature before the
reviewers' second pass. Core builds **one evidence bundle per manuscript
version** — free sources first, scite's MCP tools only where nothing else has
the data — and every reviewer reads the same bundle. Reviewers (sub-agents)
never call scite themselves.

Design: `docs/superpowers/specs/2026-10-13-scite-review-and-discovery-design.md`.
File layout and service API: [Review contract](review-contract.md).

## Setup

1. Copy the policy template and edit it (the file is gitignored):

    ```bash
    cp config/scite.example.yml config/scite.yml
    ```

    Without `config/scite.yml`, scite is **off** for every run: nothing is sent,
    no bundle is built, blind reviews still run.

2. Give core the token. Prefer a file only you can read:

    ```yaml
    # config/scite.yml
    token_file: /home/me/.config/scieflow/scite-token   # chmod 600
    ```

    or name an environment variable (`token_env: SCITE_TOKEN`, the default).
    Without a token, scite runs in the **public tier**: the free REST tallies
    work, the MCP calls are skipped (`scite_status: unavailable`).

3. Install the optional MCP SDK:

    ```bash
    uv sync --group scite
    ```

4. Check readiness:

    ```bash
    uv run scieflow research scite status
    uv run scieflow workspace doctor SLUG      # includes the same scite lines
    ```

    The report says whether scite is configured (and the tier), whether the
    SDK group is installed, warns when the token variable is set in the
    ScieFlow process environment (every dispatch inherits it — prefer
    `token_file`), and lists harness configs that register a scite MCP server
    (`~/.claude.json`, `~/.codex/config.toml`, Hermes `config.yaml` and
    profiles, and the project-scoped `.mcp.json` / `.codex/config.toml` in the
    project root) **by name only** — never URLs, headers or tokens.

5. Per run, the user decides whether its query plan may leave the machine
   (`review.scite`):

    | value | meaning |
    |---|---|
    | `off` | nothing is sent, no bundle; blind reviews only |
    | `ask` (default) | build only after the user answers a `scite-consent` gate with yes (the gate file *and* its `gate.answered` event must agree; a hand-written gate file is ignored) |
    | `approved` | the user approved once for all rounds of this run |

    Only the user sets `approved` (`scieflow review configure SLUG --set
    scite=approved`); an agent may only turn scite off. Show the user the plan
    first (below) — it is the exact list of outgoing requests.

## Building the bundle

```bash
uv run scieflow review evidence SLUG --round N --plan   # print the outgoing list; sends nothing
uv run scieflow review evidence SLUG --round N          # build review/round-N/evidence/
```

`scieflow research scite plan|run SLUG --round N` are the same commands;
`scieflow research scite backfill SLUG --round N` fills a round left short by a quota
stop (see below).
They all refuse inside a ScieFlow dispatch (exit 3), as do consent and policy
refusals.

Output in `workspace/<slug>/review/round-N/evidence/`:

| File | Content |
|---|---|
| `bundle.json` | the bundle (`evidence-bundle` schema): references with status and tallies, claims with quoted passages, contested sources with contrasting statements, searches, flags, call counts |
| `calls.json` | this build's rows of the run's scite ledger, the free-source lookups and what was reused |
| `state.json` | successful answers, reused by later builds |

Event: `review.evidence_built` (round, `scite_status`, call counts).

### What is asked, and what it costs

| Need | Source | scite MCP calls |
|---|---|---|
| Reference exists, metadata, retraction / correction | Crossref (`/works/{doi}`); scite `/papers` only when Crossref is down | 0 |
| Supporting / contrasting counts for every cited DOI | scite public bulk `POST /tallies` (one request) | 0 |
| Does the source support the claim? | Europe PMC open-access full text → passages around the claim's key terms, matched locally; abstract otherwise (`abstract_only`) | 0 |
| What others say against the contested sources | `citation_graph`, `direction: in`, with snippets; seeds = ≤10 cited DOIs with the highest contrasting share | 1, only if any DOI has contrasting statements |
| Missing / recent literature on the topic | `search_literature` on the title + keywords | 1 |
| Novelty claims ("first", "novel", "to our knowledge") | one `search_literature`, all novelty sentences OR-combined | 1, only if any |
| Reviewer follow-ups (pass B `followup_requests`) | de-duplicated across reviewers, capped by `max_followups_per_round` | ≤2 |

Round 1 is at most 3 (+2) MCP calls: whatever the prompt list (including a
run's `review/prompts.yml`) asks, core makes **one** call per kind —
contested, topic, novelty — and the plan lists any extra query as "not
planned". MCP calls the policy does not allow right now (no token, tool not in
`allowed_tools`) are not in the request list; the plan names them under "not
sent" and the bundle records them as `skipped: policy`. Later rounds re-ask only what changed —
a new DOI, a reworded claim, a new follow-up; an unchanged manuscript costs
**zero** calls of any kind.

### What leaves the machine

Only DOIs and key terms of citing / novelty sentences after sanitising
(numbers, percentages, p-values, CIs, `[data:…]`, `% source:` comments,
`\input`, paths and subject codes removed; anything still looking like data
is refused, not sent), plus the title and keywords. Reviewer follow-up queries
pass the same checks. The `--plan` output is the complete list.

### Budgets

`config/scite.yml` enforces them in code; a refusal is a boundary, never
retried around:

| Key | Default | Counts |
|---|---|---|
| `max_mcp_calls_per_round` | 5 | uncached MCP calls in one review round |
| `max_mcp_calls_per_run` | 20 | uncached MCP calls in the run |
| `max_mcp_calls_per_day` | unset | uncached MCP calls on this machine today (UTC) |
| `max_followups_per_round` | 2 | reviewer follow-up searches per round |
| `contested_seeds` | 10 | seeds of the contested-source graph call (scite caps at 10) |

Cache hits cost nothing. When a budget runs out mid-build, the remaining MCP
calls are recorded as `skipped: budget`, the bundle says `scite_status:
quota`, and the round completes with what it has. A quota error from scite
(`QUOTA`) does the same. Either way the user is told once per run
(`scite.unavailable`), the matrix rows of that round say `scite_status:
quota`, and the review goes on blind-only where evidence is missing. Later,
`uv run scieflow research scite backfill SLUG --round N` fills only what is
missing: every answer the earlier build got is reused without a call, the
skipped requests are sent within the same budgets, and it refuses when the
manuscript changed since (build a new round instead). A round that already
has a matrix is refused unless `--force`: the new bundle invalidates the
matrix, its decisions and the pass-B reviews checked against the old one, so
re-run pass B and the matrix afterwards. At 10 %
of a run or day budget left, `scite.quota_low` is emitted once. Where a call
is not possible at all (no token, SDK missing, auth failure, tool not allowed)
the bundle says `unavailable`.

## Limitations

- **Blindness protects against accidents, not a hostile agent.** Pass A is
  blind because reviewers get no bundle and dispatch harnesses get no scite
  MCP; the scite commands refuse inside a dispatch (detected by the sandbox's
  per-dispatch `UV_CACHE_DIR` until an explicit marker lands). A deliberately
  hostile agent could still read a token from its environment or reach the
  network — the sandbox confines writes, not reads or network. Keep the token
  in `token_file`, not in the shell environment.
- **Paywalled papers are abstract-only.** Passages come from Europe PMC's
  open-access full text; otherwise the abstract is used and the passage is
  flagged `abstract_only`.
- **Snippets are open-access only.** scite returns citing-text snippets for
  open-access citing papers; closed-publisher citations carry only their type.
- **Retraction data is Crossref's** (publisher deposits and Retraction Watch).
  A transient outage is never reported as unresolvable: scite's `/papers`
  record is used instead, and if that fails too the DOI is retried in the next
  build.
- **Results of earlier rounds are reused.** The Crossref status, tallies and
  passages of an unchanged reference are carried over from the latest earlier
  build. To re-check everything (e.g. for a retraction published since), delete
  the latest `review/round-*/evidence/state.json` before building.

## Interactive MCP (coordinator chats only)

For exploring the literature yourself in a chat with the coordinator, register
scite's MCP server in that harness. **Never in a profile or config used for
dispatches** — sub-agents must not reach scite; `scieflow workspace doctor`
lists every registration it finds.

Hermes (`config.yaml` of the profile you chat with):

```yaml
mcp_servers:
  scite:
    url: https://api.scite.ai/mcp
    headers:
      Authorization: "Bearer ***"
```

Claude Code — run it **outside** the ScieFlow project directory: the default
local scope is keyed to the current directory, and a registration in the
project root (or `--scope project`, which writes `.mcp.json`) would reach every
Claude dispatch there:

```bash
cd ~/scite-chat && claude mcp add --transport http scite https://api.scite.ai/mcp --header "Authorization: Bearer ***"
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.scite]
url = "https://api.scite.ai/mcp"
http_headers = { Authorization = "Bearer ***" }
```

Replace `***` with your token; keep these files private.
