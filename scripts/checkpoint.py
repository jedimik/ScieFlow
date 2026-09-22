#!/usr/bin/env python3
"""Moved to scieflow.core.run.checkpoint in M1; kept for `uv run scripts/checkpoint.py`
and `import checkpoint`."""

import sys

from scieflow.core.run import checkpoint as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
