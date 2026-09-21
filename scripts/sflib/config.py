"""Legacy shim: `sflib.config` moved to `scieflow.core.config`.

About 17 helper scripts inside older workspace runs still import it.
See docs/MIGRATION.md.
"""

from scieflow.core.config import (  # noqa: F401  (re-exported)
    load_agents,
    load_defaults,
    load_run_config,
    repo_root,
    tier_agents,
)
from scieflow.core.legacy import notice

notice("from sflib.config import …", "from scieflow.core.config import …")
