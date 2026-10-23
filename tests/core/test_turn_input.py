from dataclasses import FrozenInstanceError, fields

import pytest

from scieflow.core.turn_input import (
    HumanSubmission, accept_human_submission, validate_marked_input,
)


@pytest.mark.parametrize('message', ['', ' /scieflow-menu', '\t/scieflow-menu', '\n/scieflow-menu', '\u00a0/scieflow-menu', '/scieflow-menu\0', '\ud800', 'a' * 65537])
def test_marked_input_refuses_without_normalizing(message):
    with pytest.raises(ValueError):
        validate_marked_input(message)


@pytest.mark.parametrize('message', ['a' * 65536, 'é' * 32768, 'x\n', 'x\t', 'x\u00a0'])
def test_utility_preserves_exact_utf8_and_is_not_command_grammar(message):
    assert validate_marked_input(message) == message.encode('utf-8')


def test_multibyte_over_boundary():
    with pytest.raises(ValueError):
        validate_marked_input('é' * 32769)


@pytest.mark.parametrize('message', [' /scieflow-menu\0\n', 'é' * 32769, '', '\ud800'])
def test_default_chat_preserves_text_without_new_marked_limit(message):
    submission = accept_human_submission(message)
    assert submission == HumanSubmission(message, 'chat', None)
    assert submission.message == message


@pytest.mark.parametrize('revision', ['0', '1', '123', '9' * 10000])
@pytest.mark.parametrize('mode', ['chat', 'harness-command'])
def test_canonical_revision_preserved_without_int_conversion(revision, mode):
    submission = accept_human_submission('/scieflow-menu', mode, revision)
    assert submission.selection_revision == revision
    assert submission.message == '/scieflow-menu' and submission.mode == mode


@pytest.mark.parametrize('revision', ['', '00', '01', '-1', '+1', ' 1', '1 ', '1\n', '1.0', '١', '１', '1\0', 0, True, []])
@pytest.mark.parametrize('mode', ['chat', 'harness-command'])
def test_supplied_invalid_revision_refused(revision, mode):
    with pytest.raises(ValueError):
        accept_human_submission('/scieflow-menu', mode, revision)


def test_marked_requires_revision():
    with pytest.raises(ValueError):
        accept_human_submission('/scieflow-menu', 'harness-command')


@pytest.mark.parametrize('mode', ['', 'command', 'CHAT', True, []])
def test_invalid_mode_not_inferred(mode):
    with pytest.raises(ValueError):
        accept_human_submission('/scieflow-menu', mode, '0')


@pytest.mark.parametrize('message', ['/scieflow-men', '/help', '/scieflow-menu /other', '/scieflow-menu\n', '/scieflow-menu ', '／scieflow-menu', '/scieflow-k-probe'])
def test_marked_acceptance_requires_candidate_grammar(message):
    with pytest.raises(ValueError):
        accept_human_submission(message, 'harness-command', '0')


def test_submission_is_frozen_and_only_scalar_fields():
    submission = accept_human_submission('/scieflow-menu', 'harness-command', '0')
    assert [f.name for f in fields(submission)] == ['message', 'mode', 'selection_revision']
    with pytest.raises(FrozenInstanceError):
        submission.message = 'changed'
    assert not hasattr(submission, '__dict__')


@pytest.mark.parametrize('message', [b'/scieflow-menu', None, []])
def test_non_text_refused(message):
    with pytest.raises(ValueError):
        validate_marked_input(message)
    with pytest.raises(ValueError):
        accept_human_submission(message)
