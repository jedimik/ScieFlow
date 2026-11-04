"""F-2 t4: the HTTP API downgrade marker and the composition order on the
run page and the job page (plan section 5.7, ticket t4).

Test-first. Synthetic, hermetic fixtures only (the `project` and `client`
fixtures of tests/web/conftest.py). No marked turn is ever executed here:
the API send is observed through a spy on `service.say`.
"""
import re
import sys
from pathlib import Path

import pytest
import yaml

from scieflow.core import jobs, service
from scieflow.core.run import conversation
from scieflow.web import auth, presentation

NOTICE = ('harness-command capability is not certified on this deployment; '
          'your message was sent as ordinary chat')


def api_post(client, path, **form):
    """A script-client positive: session cookie + CSRF header, no form field."""
    return client.post(path, data=form,
                       headers={auth.CSRF_HEADER: client.cookies[auth.CSRF_COOKIE]},
                       follow_redirects=False)


def revision(project):
    return conversation.selection_revision(conversation.read(project.run_dir('r1')))


def audit(turn_id, *, kind):
    return dict(schema_version=1, turn_id=turn_id, input_kind=kind,
                composition_order='command-first' if kind == 'harness-command' else 'charter-first',
                selected_manager='fixture', family='claude', policy_revision='fixture-only',
                message_sha256='a' * 64, charter_version=0, charter_sha256='b' * 64,
                composed_artifact=f'logs/turn-{turn_id}.composed.md', job_id='',
                parse_complete=None, reason='', accepted_revision='0', final_revision=None)


def write_turns(project, turns):
    ws = project.run_dir('r1')
    (ws / 'conversation.yml').write_text(yaml.safe_dump({'agent': '', 'session': '', 'turns': turns}))


# ---------------------------------------------------------------------------
# 1. API: a marked send is delivered as chat, and the response says so.
# ---------------------------------------------------------------------------

def test_api_marked_send_is_delivered_as_chat_and_reports_it(project, client, monkeypatch):
    calls = []

    def spy(project_, slug, message, actor='human', *, human_submission=None):
        calls.append(human_submission)
        return {'turn': 'stub'}

    monkeypatch.setattr(service, 'say', spy)
    response = api_post(client, '/api/v1/runs/r1/conversation', message='/scieflow-menu',
                        mode='harness-command', selection_revision=revision(project))
    assert response.status_code == 200, response.text
    assert len(calls) == 1 and calls[0].mode == 'chat'
    body = response.json()
    assert body['turn'] == 'stub'
    assert body['harness_command'] == {'requested': True, 'delivered_as': 'chat', 'notice': NOTICE}


def test_api_ordinary_send_response_has_no_harness_key(project, client, monkeypatch):
    monkeypatch.setattr(service, 'say', lambda *args, **kwargs: {'turn': 'stub'})
    response = api_post(client, '/api/v1/runs/r1/conversation', message='hello',
                        mode='chat', selection_revision=revision(project))
    assert response.status_code == 200, response.text
    assert response.json() == {'turn': 'stub'}


# ---------------------------------------------------------------------------
# 2. Presentation mapping (pure).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('audit_value, expected', [
    ({'composition_order': 'command-first'}, 'command-first'),
    ({'composition_order': 'charter-first'}, 'charter-first'),
    ({'composition_order': 'shell'}, 'unknown'),
    ({'composition_order': ''}, 'unknown'),
    ({'composition_order': ['command-first']}, 'unknown'),
    ({'input_kind': 'chat'}, None),
    (None, None),
    ('command-first', None),
])
def test_composition_label_mapping(audit_value, expected):
    assert presentation.composition_label(audit_value) == expected


def test_web_package_never_imports_harness_commands():
    root = Path(presentation.__file__).resolve().parent
    offenders = sorted(str(p) for p in root.rglob('*.py')
                       if 'harness_commands' in p.read_text(encoding='utf-8'))
    assert offenders == []


# ---------------------------------------------------------------------------
# 3. Run page: composition order after the mode badge.
# ---------------------------------------------------------------------------

def test_run_page_shows_composition_after_the_mode_badge(project, client):
    write_turns(project, [
        {'role': 'human', 'text': '/scieflow-menu', 'audit': audit('01ARZ3NDEKTSV4RRFFQ69G5FAV', kind='harness-command')},
        {'role': 'human', 'text': 'plain', 'audit': audit('01ARZ3NDEKTSV4RRFFQ69G5FAW', kind='chat')},
    ])
    page = client.get('/runs/r1').text
    assert re.search(r'mode-badge[^>]*>\s*harness-command\s*</span>\s*'
                     r'<span class="dim">composition:</span>\s*<span class="mode-badge">command-first</span>',
                     page)
    assert re.search(r'mode-badge[^>]*>\s*chat\s*</span>\s*'
                     r'<span class="dim">composition:</span>\s*<span class="mode-badge">charter-first</span>',
                     page)


def test_run_page_unknown_composition_is_escaped_and_inert(project, client):
    write_turns(project, [{'role': 'human', 'text': 'x',
                           'audit': {'input_kind': 'chat',
                                     'composition_order': '<script>alert(1)</script>'}}])
    response = client.get('/runs/r1')
    assert response.status_code == 200
    assert '<script>alert(1)</script>' not in response.text
    assert '&lt;script&gt;' in response.text
    assert re.search(r'composition:</span>\s*<span class="mode-badge">unknown</span>', response.text)


def test_run_page_has_no_composition_for_a_turn_without_audit(project, client):
    write_turns(project, [{'role': 'human', 'text': 'plain turn without audit'}])
    page = client.get('/runs/r1').text
    assert 'plain turn without audit' in page
    assert 'composition:' not in page


# ---------------------------------------------------------------------------
# 4. Job page: composition beside the turn mode.
# ---------------------------------------------------------------------------

def test_job_page_shows_composition_for_a_turn_job(project, client):
    job, proc = jobs.start(project, [sys.executable, '-c', 'pass'], kind='turn',
                           cwd=project.root, run_dir=project.run_dir('r1'),
                           turn_audit=audit('01ARZ3NDEKTSV4RRFFQ69G5FAX', kind='chat'))
    proc.wait(timeout=30)
    page = client.get(f'/runs/r1/jobs/{job.id}').text
    assert re.search(r'turn mode: <span class="mode-badge">chat</span>', page)
    assert re.search(r'composition:</span>\s*<span class="mode-badge">charter-first</span>', page)


def test_job_page_has_no_composition_for_a_plain_job(project, client):
    job, proc = jobs.start(project, [sys.executable, '-c', 'pass'], kind='job',
                           cwd=project.root, run_dir=project.run_dir('r1'))
    proc.wait(timeout=30)
    page = client.get(f'/runs/r1/jobs/{job.id}').text
    assert 'composition:' not in page
