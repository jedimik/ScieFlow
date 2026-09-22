"""Moved to scieflow.core.run.status in M1. Kept so `import status` and old
chats keep working: this module *is* the package module."""

import sys

from scieflow.core.run import status as _impl

sys.modules[__name__] = _impl
