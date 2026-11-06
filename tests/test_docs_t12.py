"""T-12 docs contract (T-12 plan section 5.9, sentences T1-T8).

Self-contained like tests/test_docs_f2.py: every expected sentence is a literal
here and the docs are read only from this repository's docs/ directory, resolved
from this file's own location.
"""

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / 'docs'

T1 = ('Claude effort is `reasoning: default` or `reasoning: extended-thinking`: ScieFlow adds '
      '`MAX_THINKING_TOKENS=32000` itself (the `env` prefix of a one-shot dispatch, or a conversation turn\'s '
      'environment), so choosing it never edits the `cmd`.')
T2 = ('The effort choices for an agent are its own `menu.reasoning.levels` in `config/agents.yml`; list the '
      'effort names your subscription uses there.')
T3 = ('Executable command templates (`cmd`, `stdin_cmd`), project `enabled`, and support-as-primary promotion and '
      'demotion stay CLI-only; a workspace cannot set `enabled` at all.')
T4 = ('Every browser apply carries the revision of its preview; if any file the preview read has changed since, '
      'nothing is written and the page asks you to preview again.')
T5 = ('The workspace index and the health page only read: they never write `INDEX.md`, repair, clean up, upload or '
      'contact a remote or a model.')
T6 = ('A health check is partial when it stops at its budget (10,000 entries or about two seconds); a partial size '
      'is a lower bound, never a total.')
T7 = ('`GET /api/v1/agents` without `scope` still returns the full effective configuration, command templates '
      'included; with `scope=` it returns the browser view, which never includes them.')
T8 = 'The menu\'s Claude effort choice sets `reasoning`; it no longer edits the agent\'s `cmd`.'

# (file, sentence id, sentence): the placements named in plan section 5.9.
PLACEMENTS = [
    ('agents.md', 'T1', T1),
    ('agents.md', 'T2', T2),
    ('web.md', 'T3', T3),
    ('web.md', 'T4', T4),
    ('web.md', 'T5', T5),
    ('web.md', 'T6', T6),
    ('web.md', 'T7', T7),
    ('menu.md', 'T8', T8),
]


def _doc(name: str) -> str:
    return (DOCS / name).read_text(encoding='utf-8')


def _section(text: str, heading: str) -> str:
    """The text of one `## ` section, up to the next `## ` heading."""
    start = text.index(heading)
    rest = text[start + len(heading):]
    match = re.search(r'\n## ', rest)
    return rest[:match.start()] if match else rest


@pytest.mark.parametrize('name,sid,sentence', PLACEMENTS, ids=[f'{sid}-{name}' for name, sid, _ in PLACEMENTS])
def test_t12_sentence_is_present_verbatim(name, sid, sentence):
    assert sentence in _doc(name), f'{sid} missing from docs/{name}'


def test_what_stays_cli_only_no_longer_lists_per_agent_field_edits():
    section = _section(_doc('web.md'), '## What stays CLI-only')
    assert 'Per-agent field edits' not in section
    assert '`--promote`' in section
    assert T3 in section


_CLAIMS = [
    # A positive claim that a page or the API can promote, demote or enable an agent.
    re.compile(r'\b(you can|can now|now lets? you|may now)\b[^.\n]{0,60}\b(promote|demote|enable|disable)\b',
               re.IGNORECASE),
    re.compile(r'\b(promot|demot)\w*\b[^.\n]{0,40}\b(in the browser|on the Agents page|via the API)\b',
               re.IGNORECASE),
    # A positive claim of Host/Origin checks (the residual is documented as not done).
    re.compile(r'\b(enforces|performs|adds|implements)\b[^.\n]{0,40}\b(Host|Origin)\b[^.\n]{0,20}\bcheck',
               re.IGNORECASE),
]


@pytest.mark.parametrize('name', ['web.md', 'agents.md', 'menu.md'])
def test_no_page_claims_browser_promotion_enabled_editing_or_host_origin_checks(name):
    text = _doc(name)
    for pattern in _CLAIMS:
        found = [m.group(0) for m in pattern.finditer(text)]
        assert not found, f'docs/{name} makes a claim the code does not support: {found}'
