"""M-A T4 (A-5, N4): every documented `scieflow agent run` invocation names its role.

An invocation is matched by MATCHER below. Prose mentions do not match. A role-less
invocation is allowed only when `tests/docs_role_allow.txt` lists it (path, invocation
text, reason; tab-separated).
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALLOW = ROOT / "tests" / "docs_role_allow.txt"

MATCHER = re.compile(
    r"""scieflow\s+agent\s+run(?P<opts>(?:\s+--[a-z][a-z-]*(?:[ =](?!--)[^\s`]+)?)*)\s+
  (?P<agent><[^>\s]+>|[a-z][a-z0-9-]*)\s+
  (?P<prompt><[^>\s]+>|[^\s`]*(?:/|\.md\b)[^\s`]*)""",
    re.X,
)


def _scope_files() -> list[Path]:
    files = [ROOT / "AGENTS.md", ROOT / "README.md"]
    files += sorted((ROOT / "skills").rglob("*.md"))
    files += sorted((ROOT / "src").rglob("*.md"))
    files += sorted((ROOT / "docs").rglob("*.md"))
    out = []
    for path in files:
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("docs/superpowers/") or rel == "docs/MIGRATION.md":
            continue
        out.append(path)
    return sorted(set(out))


def _invocations(text: str):
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in MATCHER.finditer(line):
            yield lineno, m


def _allow() -> set[tuple[str, str]]:
    if not ALLOW.exists():
        return set()
    entries = set()
    for raw in ALLOW.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        path, text, _reason = raw.split("\t", 2)
        entries.add((path, text))
    return entries


# --- matcher fixtures (the planning-time hits; copied as strings, no line numbers) ---

ROLE_LESS = [
    "uv run scieflow agent run <agent> <prompt> <transcript>",
    "uv run scieflow agent run agy workspace/<slug>/prompts/search-agy.md workspace/<slug>/logs/search-agy.log",
]
WITH_ROLE = [
    "uv run scieflow agent run --role research.draft-authors codex-paper <prompt> <transcript>",
    "uv run scieflow agent run --role=loop.experiment codex workspace/r/prompts/x.md out.log",
]
PROSE = [
    "Each invocation of `scieflow agent run` records a job row in the run.",
    "The `scieflow agent run` command refuses a role-less dispatch.",
    "See scieflow agent run --help for options.",
]


def test_matcher_flags_role_less_invocations():
    for line in ROLE_LESS[:2]:
        m = list(MATCHER.finditer(line))
        assert m, line
        assert "--role" not in m[0].group("opts"), line


def test_matcher_passes_invocations_with_role():
    for line in WITH_ROLE:
        m = list(MATCHER.finditer(line))
        assert m, line
        assert "--role" in m[0].group("opts"), line


def test_matcher_ignores_prose_mentions():
    for line in PROSE:
        assert not list(MATCHER.finditer(line)), line


def test_every_documented_invocation_names_its_role():
    allow = _allow()
    problems = []
    for path in _scope_files():
        rel = path.relative_to(ROOT).as_posix()
        for lineno, m in _invocations(path.read_text(encoding="utf-8", errors="replace")):
            if "--role" in m.group("opts"):
                continue
            text = m.group(0).strip()
            if (rel, text) in allow:
                continue
            problems.append(f"{rel}:{lineno}: {text}")
    assert not problems, "role-less agent run in docs (add --role or allow-list it):\n" + "\n".join(problems)

