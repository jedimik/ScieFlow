"""Errors of the systematic-review package (M-H plan 5.13).

A refused or invalid operation raises SRError (exit 1). The message names the rule and a path or
an id. It never carries record text, a search query or a screener's free text.
"""


class SRError(ValueError):
    """A refused or invalid systematic-review operation."""
