#!/usr/bin/env python3
"""Legacy shim: `uv run scripts/agent_run.py` → `uv run scieflow agent run`.

Kept so agent chats from before the module merge keep working. A
`--cwd vendors/<X>` from the old vendored layout is dropped (the agent runs
from the repo root, where that code now lives). See docs/MIGRATION.md.
"""

import sys

from scieflow.core.legacy import forward, translate_cwd

if __name__ == "__main__":
    forward("uv run scripts/agent_run.py", "scieflow.core.agent_run",
            translate_cwd(sys.argv[1:]))
