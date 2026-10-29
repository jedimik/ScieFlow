"""`scieflow journal` (M-F plan 5.2): target journals and their profiles.

This ticket adds the read-only commands `show`, `list` and `validate`. `research` and `adopt`
(which fetch guideline pages and write profiles) come with later tickets.

Exit codes: 0 ok (warnings allowed), 1 refused or a problem found, 2 usage.
"""

from __future__ import annotations
