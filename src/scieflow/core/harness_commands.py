"""Pure exact-identity capability policy. Shipped support is disabled.

Identity capture belongs to the future resolver, never to these projections.
No path resolution, binary/version execution, discovery or certificate loader.
"""
from dataclasses import dataclass, fields
import re


@dataclass(frozen=True, slots=True)
class HarnessIdentity:
    agent: str
    family: str
    enabled: bool
    executable: str | None
    executable_sha256: str | None
    version: str | None
    session_template: str | None
    resume_template: str | None
    model: str | None
    reasoning: str | None
    cwd: str | None
    discovery_sha256: str | None
    extensions_sha256: str | None
    settings_sha256: str | None
    effects_pinned: bool

    def __post_init__(self):
        # Scalar-only records cannot hide mutable config/environment references.
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in ('enabled', 'effects_pinned'):
                valid = type(value) is bool
            elif field.name in ('agent', 'family'):
                valid = type(value) is str
            else:
                valid = value is None or type(value) is str
            if not valid:
                raise TypeError(f'{field.name} must be an immutable scalar of its declared type')


@dataclass(frozen=True, slots=True)
class SupportStatus:
    enabled: bool
    reason: str
    policy_revision: str

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.reason) is not str or type(self.policy_revision) is not str:
            raise TypeError('support status requires bool/text scalars')


@dataclass(frozen=True, slots=True)
class _CertifiedPolicy:
    identity: HarnessIdentity
    command: str
    policy_revision: str
    verified_on: str
    evidence_ref: str

    def __post_init__(self):
        if type(self.identity) is not HarnessIdentity:
            raise TypeError('policy requires exact immutable HarnessIdentity')
        for name in ('command', 'policy_revision', 'verified_on', 'evidence_ref'):
            if type(getattr(self, name)) is not str:
                raise TypeError('policy metadata must be text scalars')


CERTIFIED_POLICIES: tuple[_CertifiedPolicy, ...] = (
    _CertifiedPolicy(
        identity=HarnessIdentity(
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
            effects_pinned=True),
        command='/scieflow-menu',
        policy_revision='k-g5-r14-1',
        verified_on='2026-10-22',
        evidence_ref='reviews/k-g5-review.md sha256-of-final-report=5c4a7f84add07f6cdbee1b302c584abc44460b2890f8aa101fde3f2f0860bf28',
    ),
)


def validate_candidate_raw(message: str) -> None:
    """Only the exact settled candidate, without arguments or normalization."""
    if type(message) is not str or message != '/scieflow-menu':
        raise ValueError('marked candidate must be exactly /scieflow-menu; ordinary chat is separate')


def effective_identity(resolved) -> HarnessIdentity:
    """Project the future ResolvedTurn's already captured immutable identity."""
    identity = getattr(resolved, 'harness_identity', None)
    if type(identity) is not HarnessIdentity:
        raise ValueError('resolved turn lacks an immutable HarnessIdentity')
    return identity


def _nonempty_text(value) -> bool:
    return type(value) is str and bool(value.strip()) and '\0' not in value


def _complete(identity) -> bool:
    if type(identity) is not HarnessIdentity:
        return False
    if identity.family != 'claude' or not identity.enabled or not identity.effects_pinned:
        return False
    for field in fields(identity):
        if field.name in ('enabled', 'effects_pinned'):
            continue
        value = getattr(identity, field.name)
        if field.name.endswith('_sha256'):
            if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
                return False
        elif not _nonempty_text(value):
            return False
    return True


def support_for(identity: HarnessIdentity) -> SupportStatus:
    """Fail closed on missing effects or any exact certificate mismatch.

    Synthetic records may be monkeypatched only within offline tests. A file,
    report or audit record cannot populate the shipped empty code-owned tuple.
    """
    if not _complete(identity):
        return SupportStatus(False, 'Incomplete, disabled, unsupported or unpinned harness identity', '')
    for policy in CERTIFIED_POLICIES:
        if (type(policy) is _CertifiedPolicy and policy.command == '/scieflow-menu'
                and all(_nonempty_text(getattr(policy, name)) for name in
                        ('policy_revision', 'verified_on', 'evidence_ref'))
                and policy.identity == identity):
            return SupportStatus(True, '', policy.policy_revision)
    return SupportStatus(False, 'No exact certified /scieflow-menu policy for this harness identity', '')
