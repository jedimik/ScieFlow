"""ScieFlow's inbound sanitiser (M-C1 Part A 5.9).

A thin re-export of the vendored researcher sanitiser (src/scieflow/_vendor/researcher_core/sanitize.py,
pinned in UPSTREAM.json), plus `fence_text`: the rendering-attack strip that the evidence bundle's
verbatim fence uses. Nothing here decides a verdict. Sanitisation happens only where a value leaves
the process as output (search printouts, the evidence fence); dedupe, rank, snapshots, caches and the
ledger always see the raw values.
"""

from __future__ import annotations

from scieflow._vendor.researcher_core.sanitize import (
    REDACTION,
    contains_injection,
    fence_untrusted,
    sanitize_json_strings,
    sanitize_string,
    strip_ansi,
    strip_dangerous_controls,
)

__all__ = [
    "REDACTION",
    "contains_injection",
    "fence_text",
    "fence_untrusted",
    "sanitize_json_strings",
    "sanitize_string",
    "strip_ansi",
    "strip_dangerous_controls",
]


def fence_text(content: str) -> str:
    """Verbatim text for an untrusted fence: ANSI escapes, bidi and zero-width characters and
    C0/C1 controls removed; words, line breaks and tabs-as-spaces kept. Instructions are not
    redacted here: the fence itself states that the content is data."""
    return strip_dangerous_controls(strip_ansi(content), keep_newlines=True)
