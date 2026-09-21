#!/usr/bin/env python3
"""Legacy shim: `uv run scripts/zotero_export.py` → `scieflow research zotero-export`.

Kept so agent chats from before the module merge keep working.
See docs/MIGRATION.md and src/scieflow/core/legacy.py.
"""

from scieflow.core.legacy import forward

if __name__ == "__main__":
    forward("uv run scripts/zotero_export.py", "scieflow.research.zotero")
