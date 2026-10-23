"""Exact accepted human text and explicit intent, not authentication.

Only guarded controllers may construct marked submissions in production.
Ordinary slash text, stored records and actor labels confer no marked intent.
"""
from dataclasses import dataclass
import re
from typing import Literal, cast

from scieflow.core.harness_commands import validate_candidate_raw

MARKED_INPUT_BYTES = 65536


@dataclass(frozen=True, slots=True)
class HumanSubmission:
    message: str
    mode: Literal['chat', 'harness-command']
    selection_revision: str | None

    def __post_init__(self):
        if type(self.message) is not str:
            raise ValueError('message must be text')
        if type(self.mode) is not str or self.mode not in ('chat', 'harness-command'):
            raise ValueError('mode must be chat or harness-command')
        revision = self.selection_revision
        if revision is not None and (type(revision) is not str or
                re.fullmatch(r'(?:0|[1-9][0-9]*)', revision) is None):
            raise ValueError('selection revision must be canonical ASCII decimal text')
        if self.mode == 'harness-command':
            if revision is None:
                raise ValueError('marked submission requires selection revision')
            validate_marked_input(self.message)
            validate_candidate_raw(self.message)


def validate_marked_input(message: str) -> bytes:
    """Return unchanged UTF-8 bytes; length utility is not command grammar."""
    if type(message) is not str or not message:
        raise ValueError('marked input must be nonempty text')
    if message[0].isspace() or '\0' in message:
        raise ValueError('marked input cannot start with whitespace or contain NUL')
    try:
        raw = message.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError('marked input must be valid UTF-8') from exc
    if len(raw) > MARKED_INPUT_BYTES:
        raise ValueError('marked input exceeds 64 KiB UTF-8')
    return raw


def accept_human_submission(message: str, mode: str | None = None,
                            selection_revision: str | None = None) -> HumanSubmission:
    """Validate explicit fields; absent mode/revision retain legacy chat."""
    accepted_mode = cast(Literal['chat', 'harness-command'], 'chat' if mode is None else mode)
    return HumanSubmission(message, accepted_mode, selection_revision)
