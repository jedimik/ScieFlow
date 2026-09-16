import importlib.util

import pytest

# The news module needs the `news` extra; its GUI needs `news-gui`.
_HAS_CORE = all(importlib.util.find_spec(m) for m in ("tinydb", "filelock"))
_HAS_GUI = _HAS_CORE and importlib.util.find_spec("nicegui") is not None

if not _HAS_CORE:
    collect_ignore_glob = ["test_*.py"]
elif not _HAS_GUI:
    collect_ignore_glob = ["test_gui_*.py"]


@pytest.fixture
def make_block():
    def _make(name: str = "Snakemake") -> str:
        return (
            f"## {name}\n"
            "### News\n"
            "- Released 9.9 (2026-07-01) [notes](https://example.com/9.9)\n"
            "### Updates\n"
            "_Nothing found._\n"
            "### New Use Cases\n"
            "_Nothing found._\n"
            "### Fixes\n"
            "_Nothing found._\n"
            "### Improvements\n"
            "_Nothing found._\n"
            "### 🔥 Highlight\n"
            "_Nothing found._\n"
        )

    return _make
