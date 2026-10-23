"""Default collection must not pick up the sealed r14 probe suite (G6-T3, plan G6D-1).

test_k_task8_binding.py is DD1-sealed and only runs in the RUN layout, so it is excluded
from directory collection by conftest.collect_ignore. Explicit path collection still
selects it (opt-in). Collection runs in a subprocess so the check reflects real pytest
collection, not this process's already-imported state.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEALED = 'test_k_task8_binding.py'
GUARD = 'test_k_g5_binding_guard.py'


def _collect(*targets):
    proc = subprocess.run(
        [sys.executable, '-B', '-m', 'pytest', '--collect-only', '-q',
         '-p', 'no:cacheprovider', *targets],
        cwd=REPO, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return [line for line in proc.stdout.splitlines() if '::' in line]


def test_directory_collection_excludes_sealed_suite():
    ids = _collect('tests/probes')
    assert not [i for i in ids if SEALED + '::' in i]
    assert [i for i in ids if GUARD + '::' in i]


def test_explicit_path_still_collects_sealed_suite():
    ids = _collect('tests/probes/' + SEALED)
    assert [i for i in ids if SEALED + '::' in i]
