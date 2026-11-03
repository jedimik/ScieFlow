# MCP server (read-only)

`scieflow mcp serve` runs a read-only Model Context Protocol server for an
interactive coordinator: Hermes, Claude Desktop or Codex. The client can read a
run's state through typed tools instead of shelling out to the CLI and parsing
text. The server is a local subprocess of the client. It has no network
listener.

It shows the same things the web app and the CLI show, and nothing more. Every
action still goes through the CLI or the web app; the server never performs one.

## What it is, and what it is not

- Transport: stdio. The client starts `scieflow mcp serve` as a child process.
  Each message is one line of UTF-8 JSON-RPC 2.0. The protocol layer is
  `scieflow.mcp_server.protocol`; the tools are in
  `scieflow.mcp_server.tools`.
- Read-only: there are no write tools, no MCP resources, no prompts and no
  subprocess calls. A tool reads the same service function the CLI or the web
  app calls, and nothing else.
- Not for sub-agents: the server refuses to start inside a dispatch (`scieflow
  mcp serve` and `scieflow mcp tools` exit with status 1 before they read stdin).
  A sub-agent may not read across runs. No `.mcp.json` is shipped for the
  repository, and the server is not offered to dispatched agents.

## The twelve tools

Every tool is read-only and idempotent, and reports itself as not destructive
and not open-world. A tool that takes a `slug` reads one run, given as the
directory name under `workspace/`.

| Tool | Arguments | What it returns |
|---|---|---|
| `list_runs` | none | Runs with their status and budget. Metadata only; runs at data level L2 are hidden and counted, not named. |
| `run_detail` | `slug` (required) | One run's status, configuration and workspace summary. |
| `run_events` | `slug` (required); `since`, `types` (up to 20 type names), `limit` (1 to 1000, default 200) | The most recent event records of one run, optionally filtered by time and type. |
| `open_gates` | `slug` (optional) | Open approval gates: of one run when a slug is given, otherwise of every run the data rules allow the client to read. |
| `run_charter` | `slug` (required) | The standing goal of one run and its version history. |
| `conversation` | `slug` (required) | The conversation state of one run: its agent, turns in flight and recent messages. |
| `run_overview` | `slug` (required) | The four read-only panels of one run's overview. |
| `workbench` | `slug` (required) | The workbench view of one run: its curation blocks and sources. |
| `curation_history` | `slug` (required) | The version history of one run's curation. |
| `agent_settings` | `slug` (optional) | The agent registry, or the settings resolved for one run when a slug is given. |
| `workflows` | none | The workflows the Start wizard offers. |
| `conversational_agents` | none | Agents that can hold a run's conversation. |

`tools/list` returns these twelve names and nothing else. The test
`tests/mcp_server/test_mcp_parity.py` checks that each tool calls exactly its
service function.

## Access rules, in plain words

- Scope: started with `--workspace SLUG`, the server serves one run. Any other
  slug is refused. Without the option, it serves every run the rules allow.
- Cloud agent rule (the data decision): before a per-run tool reads a run, the
  run's data declaration is checked with the same decision a cloud agent
  dispatch gets. A run that the check refuses is not read by a per-run tool, and
  the aggregated `open_gates` neither reads nor names it.
- L2 runs are hidden: a run declared `L2` is refused, and `list_runs` and
  `open_gates` leave it out and only count it. `list_runs` names the other runs
  by status metadata only, even when the check would refuse one of them.
- `open_gates` without a slug reads the open gates of every run the rules allow,
  and never reads a refused run. The runs it may read are always passed as a
  list, also when that list is empty, so a refused or empty set never means
  "all runs". With a slug, the tool applies the same check to that one run.
- Sanitiser: all text the server returns passes the same sanitiser as literature
  search output before it is serialised. It strips dangerous control characters
  and redacts text that looks like a prompt injection. The structure of the
  result is kept.
- Size cap: a single result is limited to 1,000,000 bytes. A larger result is
  refused with a message that says how to narrow it (for example, `run_events`
  with `since`, `types` or `limit`). A request line is limited to 4 MiB.
- Errors: a tool-level failure is returned as an error result with a short
  message. Tracebacks are written to stderr for the operator and never reach the
  client.
- Undecodable input: a line that is not valid UTF-8 or not valid JSON gets a
  parse error, and the server keeps serving.

## Commands

```bash
uv run scieflow mcp serve                 # all runs the access rules allow
uv run scieflow mcp serve --workspace SLUG
uv run scieflow mcp tools                 # names and titles; --json adds schemas
uv run scieflow mcp tools --json
```

- `serve` starts the stdio server and runs until stdin closes.
- `tools` lists the tools without starting a server.

## Registering the server in a client

Clients differ in their configuration files, so ScieFlow does not write one.
Register it as a generic `mcpServers` entry in your client's own configuration:

```json
{
  "mcpServers": {
    "scieflow": {
      "command": "uv",
      "args": ["--directory", "<path to ScieFlow>", "run", "scieflow", "mcp", "serve"]
    }
  }
}
```

Add `"--workspace", "SLUG"` to `args` to serve one run only. Use the path to
your checkout in place of `<path to ScieFlow>`. Check your client's documentation
for where that entry belongs.

## Honest limits

- The server does not authenticate its client. Anyone who can start the process
  can read every run the rules allow. Keep it on your own machine.
- Reads are not audited.
- There is no real-client interoperability test in the repository. The protocol
  is tested over stdio by starting the server as a subprocess; the first use from
  a real client is a manual check.
- Only the stdio transport is supported. There is no HTTP or SSE transport.
- The server cannot tell whether a client is safe. Anything the access rules
  allow, the client can read.
- The data decision is only as good as the run's declaration. Run
  `scieflow data scan` and declare undeclared data; see
  [Data protection](data-protection.md).
