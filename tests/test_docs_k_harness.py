"""G6-T4 docs contract (G6 plan section 3.4, D-1..D-14).

Self-contained (review R-3): every expected sentence is a literal here and the
docs are read only from this repository's docs/ directory, resolved from this
file's own location. Nothing under workspace/, RUN, reviews/ or scratch/ is read,
so the test also passes in a clean clone (G6 E-4).
"""

import hashlib
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / 'docs'

R53_LIMITATION = (
    'Honest limitation: the decisive interception record (source: session-transcript) is written by the claude CLI into k-probe-a1/scratch/agent-home/claude/.claude/projects/, a directory the sandboxed process can write. It is therefore not proof against a hostile agent. G5 may conclude only that interception is certified for a non-hostile pinned CLI binary driving a model restricted to read-only tools. G5 may not claim proof against a compromised binary or against a model holding a write tool.'
)
R53_SHA256 = 'c6117238d8d92182dcb9269aaf3c174da522a54f7928ff7cbbedb9866e846e15'

EXPECTED = [
    # D-1 ordinary chat vs marked turn
    'Only a marked harness-command turn places the command before the charter; the charter is placed last and appears exactly once in the composed prompt.',
    'Ordinary chat, generated turns and merge turns place the charter first.',
    'The reply you see is the final parsed reply, not the raw job-output JSON.',
    # D-2 finite support list; web/API never executes
    '`CERTIFIED_POLICIES` is a finite code-owned list with exactly one entry: the raw `/scieflow-menu` command with no arguments, for the one evidenced identity.',
    'Unsupported identities run nothing as a command.',
    'The web UI and HTTP API never execute a marked turn as a command today: `mode=harness-command` sent from the browser or the API is always delivered as ordinary chat, whatever `CERTIFIED_POLICIES` holds.',
    # D-3 grammar and limit scope
    'The grammar is exact and raw: nothing is normalised, and a marked input with leading whitespace or a NUL character is rejected.',
    'The 64 KiB limit applies to marked input only.',
    # D-4 display caps
    'Display caps are measured in these units: 200,000 UTF-16 units in the rendered live DOM, 16 KiB of UTF-8 per SSE wire line, 16 KiB per raw record, 64 KiB per read, and 2 MiB for the K-only parse.',
    # D-5 admission
    'Busy or unsafe refusal also applies to ordinary sends and hand-overs.',
    'On a platform without the required Linux primitives the reservation is skipped, ordinary chat runs without the new guarantee, and there is no lock fallback.',
    # D-6 reset / no replay
    'The session is reset explicitly: there is no queue, no replay and no exactly-once delivery, and recovery after a restart or a loss requires a deliberate human reset.',
    # D-7 / D-13 CLI-only and separate gates
    'Chats, DVC uploads, promotions and remote execution stay separately gated, and a human-marked token does not consent to a consequential workflow action.',
    # D-8 permission prompts
    'The current templates skip permission prompts, so do not assume that every tool call asks for permission.',
    # D-9 no enablement
    'There is no enablement anywhere today: the production resolver builds an incomplete identity, so the `/scieflow-menu` entry is inert in every deployment.',
    # D-10 residuals and verbatim limitation
    'The menu shown is a model reconstruction that may drift from the real menu (R5-1 residual).',
    'A D8 residual remains: `~/.claude` is readable by the sandboxed process.',
    R53_LIMITATION,
    # D-11 Host/Origin
    'The Host/Origin, rebinding and same-origin risk is retained as a residual; hardening is separate work and is not done.',
    # D-12 disk growth
    'Raw job logs can grow on disk, and there is no capture or kill policy for that growth.',
    # D-14 sealed suite
    'The sealed r14 probe suite is RUN-layout evidence and runs only when it is named explicitly; a new live probe cannot bind on a tree that carries the `/scieflow-menu` entry.',
]


def _web_docs() -> str:
    return (DOCS / 'web.md').read_text(encoding='utf-8')


def test_expected_sentences_are_present_verbatim():
    text = _web_docs()
    missing = [s for s in EXPECTED if s not in text]
    assert not missing, f'missing from docs/web.md: {missing!r}'


def test_limitation_literal_matches_its_recorded_sha256():
    assert hashlib.sha256(R53_LIMITATION.encode('utf-8')).hexdigest() == R53_SHA256


def test_docs_never_claim_root_enablement():
    text = _web_docs() + (DOCS / 'sandbox.md').read_text(encoding='utf-8')
    assert not re.search(r'enabled (at|in|for) ROOT', text, re.IGNORECASE)
    assert not re.search(r'ROOT[^.\n]{0,40}\benabled\b', text, re.IGNORECASE)


def test_sandbox_doc_points_to_the_harness_section():
    assert 'web.md#harness-commands' in (DOCS / 'sandbox.md').read_text(encoding='utf-8')
