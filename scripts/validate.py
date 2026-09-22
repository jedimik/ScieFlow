#!/usr/bin/env python3
"""Moved to scieflow.core.run.validate in M1; kept for `uv run scripts/validate.py`
and `import validate`."""

import sys

from scieflow.core.run import validate as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
