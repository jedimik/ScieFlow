"""Moved to scieflow.core.run.budget in M1. Kept so `import budget` and old
chats keep working: this module *is* the package module."""

import sys

from scieflow.core.run import budget as _impl

sys.modules[__name__] = _impl
