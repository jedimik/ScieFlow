"""Read-only MCP server for an interactive coordinator (M-I, plan 5.4-5.6).

stdio transport, JSON-RPC 2.0 (`protocol.py`), a registry of the twelve read-only tools
(`tools.py`), the per-run access rules (`access.py`) and the `scieflow mcp` command (`cli.py`).
The package has no network listener, no write path and no subprocess, and it never imports
an MCP SDK; a static test enforces this.
"""
