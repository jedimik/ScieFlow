from __future__ import annotations

import re
from typing import Sequence

from .templates import required_sections as _template_sections

REQUIRED_SECTIONS = tuple(_template_sections("tool"))


class ExtractionError(Exception):
    """Agent output did not contain a usable report block."""


def extract_block(
    raw: str, name: str, required_sections: Sequence[str] | None = None
) -> str:
    sections = REQUIRED_SECTIONS if required_sections is None else tuple(required_sections)
    text = raw.strip()
    header_re = re.compile(rf"^## {re.escape(name)}\s*$", flags=re.MULTILINE)
    match = header_re.search(text)
    if match is None:
        raise ExtractionError(f"no '## {name}' header in agent output")
    block = text[match.start():].strip()
    fence_lines = list(re.finditer(r"^```[^\n]*$", block, flags=re.MULTILINE))
    if len(fence_lines) % 2 == 1:
        last = fence_lines[-1]
        block = (block[: last.start()] + block[last.end() :]).strip()
    missing = [
        s
        for s in sections
        if not re.search(rf"^{re.escape(s)}\s*$", block, flags=re.MULTILINE)
    ]
    if missing:
        raise ExtractionError(f"missing sections: {', '.join(missing)}")
    return block


def assemble_report(run: dict, results: list[dict]) -> str:
    day = run["timestamp"][:10]
    lines = [f"# ScieFlow news report — {day}", "", f"Agent: `{run['agent']}`", ""]
    for r in results:
        lines.append(f"*Window: {r['window_start']} → {r['window_end']}*")
        lines.append("")
        lines.append((r.get("edited_markdown") or r["markdown"]).rstrip())
        lines.append("")
    if run.get("failures"):
        lines.append("## ⚠ Failed")
        lines.append("")
        for f in run["failures"]:
            lines.append(f"- **{f['name']}** — {f['reason']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
