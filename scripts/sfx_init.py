#!/usr/bin/env python3
"""Moved to scieflow.core.run.init in M1; kept for `uv run scripts/sfx_init.py`
and `import sfx_init`."""

import sys

from scieflow.core.run import init as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
