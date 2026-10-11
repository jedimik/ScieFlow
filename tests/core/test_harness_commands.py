from dataclasses import FrozenInstanceError, dataclass, fields, replace
from pathlib import Path

import pytest

from scieflow.core import harness_commands as hc


@pytest.fixture
def identity():
    # Synthetic scalar identity, never binary/version/discovery evidence.
    return hc.HarnessIdentity(agent='fixture-claude', family='claude', enabled=True,
        executable='/synthetic/claude', executable_sha256='a' * 64, version='fixture-v1',
        session_template='fixture first {prompt}', resume_template='fixture resume {session} {prompt}',
        model='fixture-model', reasoning='fixture-high', cwd='/synthetic/project',
        discovery_sha256='b' * 64, extensions_sha256='c' * 64, settings_sha256='d' * 64,
        effects_pinned=True)


@pytest.fixture
def synthetic_policy(identity, monkeypatch):
    policy = hc._CertifiedPolicy(identity=identity, command='/scieflow-menu',
        policy_revision='fixture-only', verified_on='fixture-date', evidence_ref='synthetic-not-evidence')
    monkeypatch.setattr(hc, 'CERTIFIED_POLICIES', (policy,))
    return policy


def test_production_policy_empty_and_support_disabled(identity):
    assert hc.CERTIFIED_POLICIES == ()
    status = hc.support_for(identity)
    assert status.enabled is False and status.reason and status.policy_revision == ''


@pytest.mark.parametrize('message', ['', '/scieflow-men', '/help', '/scieflow-menu /other', '/scieflow-menu\n', '/scieflow-menu ', '／scieflow-menu', '/scieflow-menu\0', '/scieflow-k-probe', ' /scieflow-menu', '\t/scieflow-menu', b'/scieflow-menu'])
def test_candidate_is_exact(message):
    with pytest.raises(ValueError):
        hc.validate_candidate_raw(message)


def test_candidate_positive():
    assert hc.validate_candidate_raw('/scieflow-menu') is None


@dataclass(frozen=True)
class SyntheticResolved:
    harness_identity: hc.HarnessIdentity


def test_projection_returns_same_immutable_identity_without_io(identity, monkeypatch):
    def no_io(*args, **kwargs):
        raise AssertionError('pure projection/support attempted I/O')
    monkeypatch.setattr(Path, 'resolve', no_io)
    monkeypatch.setattr(Path, 'read_bytes', no_io)
    monkeypatch.setattr('builtins.open', no_io)
    assert hc.effective_identity(SyntheticResolved(identity)) is identity
    assert not hc.support_for(identity).enabled


@pytest.mark.parametrize('resolved', [None, object(), {'harness_identity': 'not-authority'}, SyntheticResolved(None), SyntheticResolved({})])
def test_projection_refuses_absent_or_wrong_type(resolved):
    with pytest.raises(ValueError):
        hc.effective_identity(resolved)


def test_local_synthetic_exact_match(identity, synthetic_policy):
    status = hc.support_for(identity)
    assert status == hc.SupportStatus(True, '', 'fixture-only')


@pytest.mark.parametrize('field,value', [
    ('agent', 'other'), ('family', 'codex'), ('family', 'agy'), ('enabled', False),
    ('executable', '/synthetic/other'), ('executable_sha256', 'e' * 64), ('version', 'fixture-v2'),
    ('session_template', 'different first {prompt}'), ('resume_template', 'different resume {session} {prompt}'),
    ('model', 'other-model'), ('reasoning', 'other-effort'), ('cwd', '/synthetic/other'),
    ('discovery_sha256', 'e' * 64), ('extensions_sha256', 'e' * 64), ('settings_sha256', 'e' * 64),
    ('effects_pinned', False),
])
def test_each_effect_identity_mismatch_disabled(identity, synthetic_policy, field, value):
    assert not hc.support_for(replace(identity, **{field: value})).enabled


@pytest.mark.parametrize('field', ['executable', 'executable_sha256', 'version', 'session_template', 'resume_template', 'model', 'reasoning', 'cwd', 'discovery_sha256', 'extensions_sha256', 'settings_sha256'])
@pytest.mark.parametrize('value', [None, ''])
def test_missing_identity_cannot_certify(identity, monkeypatch, field, value):
    incomplete = replace(identity, **{field: value})
    policy = hc._CertifiedPolicy(incomplete, '/scieflow-menu', 'fixture', 'fixture', 'fixture')
    monkeypatch.setattr(hc, 'CERTIFIED_POLICIES', (policy,))
    assert not hc.support_for(incomplete).enabled


@pytest.mark.parametrize('field', ['executable_sha256', 'discovery_sha256', 'extensions_sha256', 'settings_sha256'])
@pytest.mark.parametrize('value', ['abc', 'a' * 63, 'a' * 65, 'A' * 64, '*' * 64, 'g' * 64])
def test_digest_is_full_lowercase_sha256(identity, monkeypatch, field, value):
    invalid = replace(identity, **{field: value})
    monkeypatch.setattr(hc, 'CERTIFIED_POLICIES', (hc._CertifiedPolicy(invalid, '/scieflow-menu', 'fixture', 'fixture', 'fixture'),))
    assert not hc.support_for(invalid).enabled


@pytest.mark.parametrize('field,value', [('family', 'codex'), ('family', 'agy'), ('family', 'unknown'), ('enabled', False), ('effects_pinned', False)])
def test_non_claude_disabled_unpinned_cannot_match_own_policy(identity, monkeypatch, field, value):
    invalid = replace(identity, **{field: value})
    monkeypatch.setattr(hc, 'CERTIFIED_POLICIES', (hc._CertifiedPolicy(invalid, '/scieflow-menu', 'fixture', 'fixture', 'fixture'),))
    assert not hc.support_for(invalid).enabled


@pytest.mark.parametrize('field,value', [('command', '/help'), ('command', '/scieflow-menu '), ('policy_revision', ''), ('verified_on', ''), ('evidence_ref', ''), ('evidence_ref', ' ')])
def test_invalid_certificate_metadata_disabled(identity, synthetic_policy, monkeypatch, field, value):
    monkeypatch.setattr(hc, 'CERTIFIED_POLICIES', (replace(synthetic_policy, **{field: value}),))
    assert not hc.support_for(identity).enabled


@pytest.mark.parametrize('identity', [None, {}, object()])
def test_unknown_identity_fail_closed(identity):
    assert not hc.support_for(identity).enabled


def test_frozen_records_no_mutable_containers(identity, synthetic_policy):
    assert [f.name for f in fields(identity)] == ['agent', 'family', 'enabled', 'executable', 'executable_sha256', 'version', 'session_template', 'resume_template', 'model', 'reasoning', 'cwd', 'discovery_sha256', 'extensions_sha256', 'settings_sha256', 'effects_pinned']
    for record, field, value in [(identity, 'version', 'changed'), (synthetic_policy, 'command', '/help'), (hc.SupportStatus(False, 'disabled', ''), 'enabled', True)]:
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, value)
        assert not hasattr(record, '__dict__')
    for field in fields(identity):
        assert not isinstance(getattr(identity, field.name), (dict, list, set))


@pytest.mark.parametrize('field,value', [('agent', []), ('family', {}), ('enabled', 1), ('effects_pinned', 'yes'), ('model', []), ('settings_sha256', {})])
def test_identity_rejects_non_scalar_fields(identity, field, value):
    with pytest.raises(TypeError):
        replace(identity, **{field: value})
