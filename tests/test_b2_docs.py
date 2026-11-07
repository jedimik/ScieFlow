"""T-9 docs contract (T-9 plan section 5.12: sentences W1-W5, D1-D2, C1, S1, A1).

Self-contained like tests/test_docs_t12.py: every expected sentence is a literal
here, and the files are read only from this repository's own tree, resolved from
this file's location. Whitespace is normalised before comparison, so a sentence
may wrap across lines in the Markdown source.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'

W1 = ('Archived runs are listed from the pointer files git already carries; opening a page never contacts the DVC '
      'remote or starts `dvc`.')
W2 = ('Download fetches the one zip and checks its size and checksum; it never unpacks it. Restore is a separate '
      'button and unpacks only into an absent or empty `workspace/<slug>`.')
W3 = ('At 1 GiB (1,073,741,824 bytes) or more the page asks you to confirm the exact size first; from the terminal '
      'the same needs `--yes`.')
W4 = ("Manuscript history is not kept in archives. It is rebuilt from the run's drafts after you restore the run: "
      "open its drafts page, or complete a merge round.")
W5_BULLET = ('**Pulls of old per-file DVC pointers** (`workspace/<slug>.dvc`) — one such pull can be tens of thousands '
             'of objects; use `uv run scripts/dvc_sync.py pull <slug>`.')
W5_API_ROWS = [
    '`GET /api/v1/archives`',
    '`GET /api/v1/archives/<slug>`',
    '`GET /api/v1/archives/<slug>/entries`',
    '`POST /api/v1/archives/<slug>/fetch`',
    '`POST /api/v1/archives/<slug>/restore`',
    '`POST /api/v1/archives/<slug>/cancel`',
]
D1 = ("Downloads from the web app or `scieflow workspace archive fetch` use this checkout's own DVC configuration "
      "and credentials, as `dvc_sync.py pull` does; ScieFlow never reads the credentials itself.")
D2 = ('Each filesystem that receives a download must keep 1 GiB free afterwards, with 5% headroom; otherwise the '
      'download is refused.')
C1 = 'Read-only views over `workspace/`, plus downloading and restoring archived runs (`archive fetch|restore`).'
C1_COMMANDS = [
    'scieflow workspace archives',
    'scieflow workspace archive show',
    'scieflow workspace archive ls',
    'scieflow workspace archive cat',
    'scieflow workspace archive fetch',
    'scieflow workspace archive restore',
    'scieflow workspace archive cancel',
]
S1 = ("A download of 1 GiB or more (`scieflow workspace archive fetch|restore … --yes`) is the user's decision: "
      "ask before passing `--yes`; inside a dispatch the commands refuse.")
A1 = ("A download of 1 GiB or more (`scieflow workspace archive fetch|restore … --yes`) is the user's decision too: "
      "ask before passing `--yes`.")

# (file, sentence id, sentence): the verbatim placements named in plan section 5.12.
PLACEMENTS = [
    ('docs/web.md', 'W1', W1),
    ('docs/web.md', 'W2', W2),
    ('docs/web.md', 'W3', W3),
    ('docs/web.md', 'W4', W4),
    ('docs/web.md', 'W5-bullet', W5_BULLET),
    ('docs/DVC_STORAGE.md', 'D1', D1),
    ('docs/DVC_STORAGE.md', 'D2', D2),
    ('docs/cli.md', 'C1', C1),
    ('skills/workspace-sync/SKILL.md', 'S1', S1),
    ('AGENTS.md', 'A1', A1),
]

# Phrases no page may claim (plan 5.12: no browser upload, no browser delete, no remote listing).
FORBIDDEN_CLAIMS = [
    'upload from the browser',
    'upload in the browser',
    'browser upload',
    'delete from the browser',
    'delete in the browser',
    'browser delete',
    'remote listing is',
    'list the remote in the browser',
    'lists the remote',
]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding='utf-8')


def _norm(text: str) -> str:
    return ' '.join(text.split())


def _section(text: str, heading: str) -> str:
    """The text from `heading` up to the next level-2 heading."""
    start = text.index(heading)
    rest = text[start + len(heading):]
    match = re.search(r'^## ', rest, flags=re.M)
    return text[start: start + len(heading) + (match.start() if match else len(rest))]


@pytest.mark.parametrize('path,sid,sentence', PLACEMENTS, ids=[f'{sid}' for _, sid, _ in PLACEMENTS])
def test_b2_sentence_is_present_verbatim(path, sid, sentence):
    assert _norm(sentence) in _norm(_read(path)), f'{sid} missing from {path}'


def test_w5_api_table_gains_the_six_archive_rows():
    api = _section(_read('docs/web.md'), '## The API')
    for row in W5_API_ROWS:
        assert row in api, f'{row} missing from the API table'


def test_w5_dvc_uploads_bullet_stays_in_what_stays_cli_only():
    section = _section(_read('docs/web.md'), '## What stays CLI-only')
    assert '**DVC uploads**' in section
    assert _norm(W5_BULLET) in _norm(section)


def test_archived_runs_section_is_a_pages_subsection():
    web = _read('docs/web.md')
    pages = _section(web, '## Pages')
    assert '### Archived runs' in pages
    assert W1 in _norm(pages) or _norm(W1) in _norm(pages)


def test_c1_replaces_the_old_read_only_line_and_documents_the_seven_commands():
    section = _section(_read('docs/cli.md'), '## `scieflow workspace`')
    assert _norm(C1) in _norm(section)
    assert 'Read-only views over `workspace/`.' not in section
    for command in C1_COMMANDS:
        assert command in section, f'{command} is not documented in the workspace section'


def test_no_docs_page_claims_a_browser_upload_delete_or_remote_listing():
    text = ''.join(_read(p) for p in (
        'docs/web.md', 'docs/DVC_STORAGE.md', 'docs/cli.md', 'skills/workspace-sync/SKILL.md'))
    normalised = _norm(text).lower()
    found = [phrase for phrase in FORBIDDEN_CLAIMS if phrase in normalised]
    assert not found, f'docs make a claim the code does not support: {found}'


def test_agents_md_rule_15_is_the_only_protected_sentence_added():
    agents = _norm(_read('AGENTS.md'))
    assert _norm(A1) in agents
    assert agents.count(_norm(A1)) == 1
