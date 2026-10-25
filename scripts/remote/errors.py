"""The policy refusal exception, kept apart from policy.py.

Scheduler adapters raise this without importing policy.py, so the registry
can be imported by policy.py without an import cycle. policy.py re-exports it,
so ``policy.PolicyError`` is the same class everywhere.
"""


class PolicyError(Exception):
    """Operation refused by config/remotes.yml."""
