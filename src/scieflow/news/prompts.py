from __future__ import annotations

from datetime import date

from .config import Interest
from .templates import TEMPLATES

GAPS_RULE = (
    "- Gaps & Blind Spots: given the user's context, list adjacent things they may "
    "not have considered — competing tools, features that change their workflow, "
    "upcoming deprecations."
)

SCIENCE_RULE = (
    "- Prioritize scientifically or technically significant items — peer-reviewed "
    "papers, preprints, benchmarks, new methods — over marketing announcements."
)

SHARED_RULES_TAIL = (
    "- 🔥 Highlight: fill it only if something genuinely big happened; otherwise "
    "write _Nothing found._",
    "- Every claim must include a date and a source link.",
    "- If a section has nothing, write _Nothing found._ under it. Never drop a section.",
    "- Do NOT invent news. If nothing happened in the window, say so explicitly.",
    "- Reply with ONLY the markdown block above: no preamble, no commentary after it.",
)


def build_prompt(interest: Interest, start: date, end: date) -> str:
    template = TEMPLATES[interest.template]
    parts: list[str] = [
        f'You are a research assistant. Research what is new about "{interest.name}" '
        f"between {start.isoformat()} and {end.isoformat()} (inclusive). Use web search."
    ]
    parts.append(template.research_focus)
    if interest.repo:
        parts.append(
            f"Primary source to check first: https://github.com/{interest.repo} "
            "(releases, changelog, discussions)."
        )
    for url in interest.urls:
        parts.append(f"Also prioritize this source: {url}")
    if interest.keywords:
        parts.append(
            "Disambiguation keywords (the intended meaning of the topic): "
            + ", ".join(interest.keywords)
        )
    if interest.context:
        parts.append(f"The user's personal context for this interest: {interest.context}")

    skeleton = [f"## {interest.name}"]
    skeleton += [f"### {s}" for s in template.sections]
    skeleton.append("### 🔥 Highlight")
    if interest.context:
        skeleton.append("### Gaps & Blind Spots")
    parts.append(
        "Structure your answer as markdown in exactly this skeleton:\n" + "\n".join(skeleton)
    )

    rules_lines = ["Rules:"]
    rules_lines += [f"- {hint}" for hint in template.section_hints]
    rules_lines += list(SHARED_RULES_TAIL)
    if interest.context:
        rules_lines.insert(-1, GAPS_RULE)
        rules_lines.insert(-1, SCIENCE_RULE)
    parts.append("\n".join(rules_lines))
    return "\n\n".join(parts)
