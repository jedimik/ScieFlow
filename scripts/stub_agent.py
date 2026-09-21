#!/usr/bin/env python3
"""Legacy shim: `python scripts/stub_agent.py` → `python -m scieflow.core.stub_agent`.

Kept so agent chats from before the module merge keep working.
See docs/MIGRATION.md and src/scieflow/core/legacy.py.
"""

from scieflow.core.legacy import forward

if __name__ == "__main__":
    forward("python scripts/stub_agent.py", "scieflow.core.stub_agent")
