"""F-2 docs contract (F-2 plan section 5.9, sentences S1-S8).

Self-contained like tests/test_docs_k_harness.py: every expected sentence is a
literal here and the docs are read only from this repository's docs/ directory,
resolved from this file's own location.
"""

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / 'docs'

S1 = ('The production resolver now captures the full 15-field identity for a marked turn, but the `/scieflow-menu` '
      'entry still matches no deployment: its working directory is the probe tree and its menu-skill bytes predate '
      'the F-1 change, so no other checkout produces the same identity.')
S2 = ("Without a shell, the menu skill reads the code-owned menu snapshot `skills/scieflow-menu/menu.json`, which a "
      "test keeps equal to the code; current settings and runs come from the run's own files.")
S3 = ("A conversation turn gets Claude's extended thinking from `reasoning: extended-thinking` (or the "
      "`env MAX_THINKING_TOKENS=32000 ` prefix on the agent's `cmd`): ScieFlow adds `MAX_THINKING_TOKENS=32000` to the "
      "turn's own environment, because an `env` prefix on `session_cmd` or `resume_cmd` is refused.")
S4 = ('When the HTTP API receives `mode=harness-command`, its response carries '
      '`"harness_command": {"requested": true, "delivered_as": "chat"}` with the same notice the page shows.')
S5 = ('Below the web UI and API, a marked turn on a platform without the required Linux admission primitives is '
      'refused; it is never run as chat there.')
S6 = ('On Linux, a run listed in `unsandboxed_runs` (or with an unsafe writable grant) cannot hold a protected '
      'conversation: ordinary sends and hand-overs to it are refused as well.')
S7 = ('The charter appears exactly once only when the run has a non-empty charter; a run without one sends the '
      'message alone.')
S8 = ('Agents without a shell read `skills/scieflow-menu/menu.json`, the code-owned part of the same tree (sections, '
      'workflows, scopes, roles and the apply commands); `tests/core/test_menu_snapshot.py` keeps it equal to the code.')

# (file, sentence id, sentence): the placements named in plan section 5.9.
PLACEMENTS = [
    ('web.md', 'S1', S1),
    ('web.md', 'S2', S2),
    ('web.md', 'S4', S4),
    ('web.md', 'S5', S5),
    ('web.md', 'S6', S6),
    ('web.md', 'S7', S7),
    ('agents.md', 'S3', S3),
    ('sandbox.md', 'S3', S3),
    ('menu.md', 'S8', S8),
]

R53_LIMITATION_START = 'Honest limitation: the decisive interception record'
NEVER_EXECUTE = 'The web UI and HTTP API never execute a marked turn as a command today'


def _doc(name: str) -> str:
    return (DOCS / name).read_text(encoding='utf-8')


@pytest.mark.parametrize('name,sid,sentence', PLACEMENTS, ids=[f'{sid}-{name}' for name, sid, _ in PLACEMENTS])
def test_f2_sentence_is_present_verbatim(name, sid, sentence):
    assert sentence in _doc(name), f'{sid} missing from docs/{name}'


def test_web_docs_keep_the_r53_limitation_and_never_execute_sentence():
    text = _doc('web.md')
    assert R53_LIMITATION_START in text
    assert NEVER_EXECUTE in text


def test_f2_docs_never_claim_root_enablement():
    text = ''.join(_doc(name) for name in ('web.md', 'agents.md', 'sandbox.md', 'menu.md'))
    assert not re.search(r'enabled (at|in|for) ROOT', text, re.IGNORECASE)
    assert not re.search(r'ROOT[^.\n]{0,40}\benabled\b', text, re.IGNORECASE)
