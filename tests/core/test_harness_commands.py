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


def test_synthetic_identity_stays_disabled_under_production_policy(identity):
    status = hc.support_for(identity)
    assert status.enabled is False and status.reason and status.policy_revision == ''


# G5 single policy entry (reviews/k-g5-review.md section 5; identity from reviews/k-g5-identity-argument.md section 1).
PRODUCTION_IDENTITY = hc.HarnessIdentity(
    agent='claude', family='claude', enabled=True,
    executable='/home/ubuntu/.local/share/claude/versions/2.1.293',
    executable_sha256='8968405e26db478af44eabc4635ab5ca557057b702a54460a59c13e1b253e978',
    version='2.1.293',
    session_template='claude -p --output-format=stream-json --verbose --dangerously-skip-permissions --tools=Read,Glob,Grep,Skill --model {model} {prompt}',
    resume_template='claude -p --output-format=stream-json --verbose --dangerously-skip-permissions --tools=Read,Glob,Grep,Skill --model {model} --resume {session} {prompt}',
    model='claude-opus-5-5',
    reasoning='env:MAX_THINKING_TOKENS=32000',
    cwd='/home/ubuntu/ScieFlow-probe/k-task8-k-r11',
    discovery_sha256='f027fccae046faf5cfeb471be16a73e17295c0d65e4e88335477b15ffedea02a',
    extensions_sha256='352742d657d7658e3f2fe4706057456b2d4099c923b92ba903b845e093e6bb57',
    settings_sha256='255c46d5669e86c54b0587feb75c771d369d9f1cd5255df58494fc5a5d04a98e',
    effects_pinned=True)

FINAL_REPORT_SHA = '5c4a7f84add07f6cdbee1b302c584abc44460b2890f8aa101fde3f2f0860bf28'


def test_production_policy_is_exactly_the_g5_entry():
    assert len(hc.CERTIFIED_POLICIES) == 1
    policy = hc.CERTIFIED_POLICIES[0]
    assert type(policy) is hc._CertifiedPolicy
    assert policy.command == '/scieflow-menu'
    assert policy.identity == PRODUCTION_IDENTITY
    assert policy.policy_revision == 'k-g5-r14-1'
    assert policy.verified_on == '2026-10-22'
    assert 'reviews/k-g5-review.md' in policy.evidence_ref
    assert FINAL_REPORT_SHA in policy.evidence_ref


def test_production_entry_enables_exact_identity_only():
    assert hc.support_for(PRODUCTION_IDENTITY) == hc.SupportStatus(True, '', 'k-g5-r14-1')


@pytest.mark.parametrize('field,value', [
    ('agent', 'codex'), ('family', 'agy'), ('enabled', False), ('effects_pinned', False),
    ('executable', '/home/ubuntu/.local/share/claude/versions/2.1.306'),
    ('executable_sha256', 'e' * 64), ('version', '2.1.306'),
    ('session_template', PRODUCTION_IDENTITY.session_template + ' --x'),
    ('resume_template', PRODUCTION_IDENTITY.resume_template + ' --x'),
    ('model', 'claude-fable-5'), ('reasoning', 'env:MAX_THINKING_TOKENS=1'),
    ('cwd', '/home/ubuntu/ScieFlow-claude-f2976f52'),
    ('discovery_sha256', 'e' * 64), ('extensions_sha256', 'e' * 64), ('settings_sha256', 'e' * 64),
])
def test_production_entry_single_field_mutation_disabled(field, value):
    assert not hc.support_for(replace(PRODUCTION_IDENTITY, **{field: value})).enabled


def test_production_shaped_identity_stays_disabled():
    # G5P-1: production capture leaves the digests, version and effects unset.
    production_shaped = replace(PRODUCTION_IDENTITY, version=None, executable_sha256=None,
        discovery_sha256=None, extensions_sha256=None, settings_sha256=None, reasoning='',
        effects_pinned=False)
    assert not hc.support_for(production_shaped).enabled


def test_root_cwd_identity_stays_disabled():
    assert not hc.support_for(replace(PRODUCTION_IDENTITY, cwd='/home/ubuntu/ScieFlow-claude-f2976f52')).enabled


def test_production_tuple_refuses_any_other_command():
    assert all(policy.command == '/scieflow-menu' for policy in hc.CERTIFIED_POLICIES)
    with pytest.raises(ValueError):
        hc.validate_candidate_raw('/help')


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
