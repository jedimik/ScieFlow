from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    description: str
    sections: tuple[str, ...]
    section_hints: tuple[str, ...]
    research_focus: str
    suggested_timeout: int


TEMPLATES: dict[str, Template] = {
    "tool": Template(
        key="tool",
        label="Software tool",
        description="Releases, changes, and practical news for a software tool or library.",
        sections=("News", "Updates", "New Use Cases", "Fixes", "Improvements"),
        section_hints=(
            "News: announcements, releases, notable community happenings.",
            "Updates: version bumps and major changes; mark breaking changes **BREAKING**.",
            "New Use Cases: novel applications, integrations, tutorials worth knowing.",
            "Fixes: significant bug fixes. Improvements: performance/UX/API improvements.",
        ),
        research_focus=(
            "Priority sources: the project's release page and changelog, official "
            "docs and blog, GitHub issues/discussions, community forums."
        ),
        suggested_timeout=300,
    ),
    "science": Template(
        key="science",
        label="Scientific topic",
        description="Literature watch: papers, preprints, methods, datasets, and events for a research topic.",
        sections=(
            "New Papers & Preprints",
            "Key Findings",
            "Methods & Datasets",
            "Reviews & Perspectives",
            "Community & Events",
        ),
        section_hints=(
            "New Papers & Preprints: peer-reviewed papers and preprints published in the window; every entry names the venue or server and links a DOI or arXiv id.",
            "Key Findings: what materially changed in the field's understanding, one line each, citing the supporting paper.",
            "Methods & Datasets: new methods, models, datasets, benchmarks, or research code releases.",
            "Reviews & Perspectives: surveys, review articles, perspectives, notable retractions or corrections.",
            "Community & Events: conferences, workshops, calls for papers with deadlines, major grants or consortium news.",
        ),
        research_focus=(
            "Priority sources: arXiv, bioRxiv, medRxiv and other preprint servers, "
            "journal tables of contents, Google Scholar/Semantic Scholar, conference "
            "and lab pages. Cite with DOI or arXiv links whenever one exists."
        ),
        suggested_timeout=900,
    ),
    "coding": Template(
        key="coding",
        label="Developer library/language",
        description="Developer-focused watch: releases, migrations, CVEs, tooling, and patterns.",
        sections=(
            "Releases",
            "Breaking Changes & Migrations",
            "Security (CVEs)",
            "Tooling & Ecosystem",
            "Best Practices & Patterns",
        ),
        section_hints=(
            "Releases: versions shipped in the window with the headline changes.",
            "Breaking Changes & Migrations: API breaks, deprecations, migration guides; mark **BREAKING**.",
            "Security (CVEs): CVEs and security advisories with severity and fixed-in version.",
            "Tooling & Ecosystem: notable plugins, integrations, build/test tooling, adjacent library moves.",
            "Best Practices & Patterns: influential posts, RFCs, or idiom shifts practitioners should know.",
        ),
        research_focus=(
            "Priority sources: GitHub releases and changelogs, security advisory "
            "databases (GHSA, CVE/NVD), package registries, maintainer blogs, RFCs."
        ),
        suggested_timeout=300,
    ),
    "platform": Template(
        key="platform",
        label="Technology platform",
        description="Platform/service watch: product updates, API changes, security, pricing, ecosystem.",
        sections=(
            "Platform Updates",
            "API & Breaking Changes",
            "Security & Advisories",
            "Pricing & Limits",
            "Ecosystem",
        ),
        section_hints=(
            "Platform Updates: new features, GA/beta launches, region or capability expansions.",
            "API & Breaking Changes: API versions, deprecations with sunset dates; mark **BREAKING**.",
            "Security & Advisories: incidents, advisories, compliance changes.",
            "Pricing & Limits: pricing, quota, or rate-limit changes.",
            "Ecosystem: notable third-party integrations, SDK updates, marketplace news.",
        ),
        research_focus=(
            "Priority sources: the vendor's official blog, changelog/release notes, "
            "status page, security advisories, pricing page, developer docs."
        ),
        suggested_timeout=300,
    ),
    "keyword": Template(
        key="keyword",
        label="Topic watch",
        description="Broad keyword/topic monitoring across news, blogs, and community discussion.",
        sections=(
            "Developments",
            "Notable Publications & Posts",
            "Community Pulse",
            "Key Players & Moves",
        ),
        section_hints=(
            "Developments: concrete events and announcements related to the topic.",
            "Notable Publications & Posts: substantial articles, reports, or papers.",
            "Community Pulse: what practitioners are discussing (forums, HN, social); summarize the sentiment.",
            "Key Players & Moves: organizations and people acting in the space — funding, hires, launches.",
        ),
        research_focus=(
            "Priority sources: reputable news outlets, engineering and research blogs, "
            "Hacker News and relevant community forums, industry reports."
        ),
        suggested_timeout=600,
    ),
}

DEFAULT_TEMPLATE = "tool"


def required_sections(template_key: str) -> list[str]:
    return [f"### {s}" for s in TEMPLATES[template_key].sections]
