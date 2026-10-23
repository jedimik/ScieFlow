"""Pre-entry production policy state for the sealed r14 probe binding suite (G5-T6).

The r14 probe binding (DD5, tests/probes/k_task8_binding.py support_bound) refuses to bind
while production CERTIFIED_POLICIES has entries. This autouse fixture applies ONLY to
test_k_task8_binding.py and sets the tuple to its pre-entry value (), so that the sealed
suite verifies generation r14 in the state it was sealed for. The sealed files are not
edited. The G5 policy entry is covered by tests/core/test_harness_commands.py and by the
guard test tests/probes/test_k_g5_binding_guard.py, which does not patch the tuple.

The sealed suite is RUN-layout evidence for generation r14, not a product regression suite:
it derives its paths from the RUN layout and errors at setup anywhere else. It is therefore
excluded from directory and default collection (collect_ignore below, G6D-1). Opt in by
naming the file explicitly:
    pytest tests/probes/test_k_task8_binding.py
"""
import pytest

from scieflow.core import harness_commands

collect_ignore = ['test_k_task8_binding.py']


@pytest.fixture(autouse=True)
def _r14_pre_entry_policy(request, monkeypatch):
    if request.node.path.name != 'test_k_task8_binding.py':
        return
    monkeypatch.setattr(harness_commands, 'CERTIFIED_POLICIES', ())
