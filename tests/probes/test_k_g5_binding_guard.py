"""G5 guard: the r14 probe binding stays refused while the production G5 entry exists.

This module does NOT patch CERTIFIED_POLICIES. It proves that the DD5 guard in
k_task8_binding.support_bound is live against the real production tuple, so no probe
can bind an identity while a certified entry is present (a new live probe would be a
new binding generation, H-2).
"""
import dataclasses
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import k_task8_binding as kb  # noqa: E402

from scieflow.core import harness_commands  # noqa: E402


def test_production_policy_is_the_single_g5_entry():
    assert len(harness_commands.CERTIFIED_POLICIES) == 1
    assert harness_commands.CERTIFIED_POLICIES[0].command == '/scieflow-menu'


def test_support_bound_refuses_while_g5_entry_present():
    entry_identity = harness_commands.CERTIFIED_POLICIES[0].identity
    other_identity = dataclasses.replace(entry_identity, model='claude-fable-5')
    before = harness_commands.support_for
    for identity in (entry_identity, other_identity):
        with pytest.raises(kb.ProbeRefused, match='CERTIFIED_POLICIES must be empty before binding'):
            with kb.support_bound(identity):
                pass
    assert harness_commands.support_for is before
    assert harness_commands.support_for(entry_identity).enabled
