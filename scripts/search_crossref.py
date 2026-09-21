#!/usr/bin/env python3
"""Legacy shim: `uv run scripts/search_crossref.py` → `scieflow research search crossref`.

Kept so agent chats from before the module merge keep working.
See docs/MIGRATION.md and src/scieflow/core/legacy.py.
"""

from scieflow.core.legacy import forward

if __name__ == "__main__":
    forward("uv run scripts/search_crossref.py", "scieflow.research.search.crossref")
