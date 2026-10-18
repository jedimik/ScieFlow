"""K Task7 REDs: authenticated composer/history/recovery UI behavior.

Test-first behavioral REDs for the Task7 contract (logs/k-task7-contract.md
T1-T7): form variants carrying mode and canonical decimal revision,
guarded send/hand-over paths, controlled rejection of malformed/stale/
missing fields, R5 matrix adapted from Task4, CSRF/session guards on all
mutating variants, mode badges, immediate repeat suppression, reset
confirmation, admission-hold socket health/SSE/cancel, and bias/stale-state
controls.

These run against the CURRENT (pre-Task7) pages/api: every form-variant,
badge, revision-token, reset-confirmation and hand-over-field behavior
asserted here is absent today, so the suite is RED before implementation,
GREEN after. No synthetic monkeypatch makes a real unsafe route or form
pass; patches here are diagnostic instrumentation only, never bypassing
session/CSRF/admission/launch guards.
"""
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient

from scieflow.core import jobs, service
from scieflow.core.run import conversation
from scieflow.web import auth
from scieflow.web.app import create_app

PROTOCOL = Path(__file__).with_name('web_conversation_protocol.py')
JS = Path('/home/ubuntu/.hermes/hermes-agent/node_modules/jsdom/lib/api.js')
LIVE_TOKEN = 'task7-live-token'


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def api_post(client, path, **form):
    """A script-client positive: session cookie + CSRF header, no form field."""
    return client.post(path, data=form,
                       headers={auth.CSRF_HEADER: client.cookies[auth.CSRF_COOKIE]},
                       follow_redirects=False)


@pytest.fixture
def web_k_project(project, grant_credentials):
    """The run's conversation held by the web-local inert protocol agent.

    Registers the fixture protocol through the same agents.yml mechanism
    the core k fixture uses, web-locally, without importing any core test
    module. The protocol prints one session-id line and one result line —
    the claude stream-json shape `sessions` parses.
    """
    import shlex

    ws = project.run_dir('r1')
    prefix = f'{shlex.quote(sys.executable)} -B {shlex.quote(str(PROTOCOL))}'
    cfg = {'cmd': prefix + ' first {prompt}',
           'session_cmd': prefix + ' first {prompt}',
           'resume_cmd': prefix + ' --resume {session} resume {prompt}',
           'family': 'claude', 'enabled': True, 'timeout_min': 1}
    block = ''.join('    ' + line + '\n' for line in yaml.safe_dump(cfg).splitlines())
    path = project.root / 'config' / 'agents.yml'
    path.write_text(path.read_text() + f'  web-k-fixture:\n{block}')
    conversation.set_agent(ws, 'web-k-fixture')
    grant_credentials(project.root, 'web-k-fixture')     # S4 (A9)
    return project


def register_agent(project, name, prefix):
    cfg = {'cmd': prefix + ' first {prompt}',
           'session_cmd': prefix + ' first {prompt}',
           'resume_cmd': prefix + ' --resume {session} resume {prompt}',
           'family': 'claude', 'enabled': True, 'timeout_min': 1}
    block = ''.join('    ' + line + '\n' for line in yaml.safe_dump(cfg).splitlines())
    path = project.root / 'config' / 'agents.yml'
    path.write_text(path.read_text() + f'  {name}:\n{block}')
    policy = project.root / 'config' / 'sandbox.yml'     # S4 (A9): the registered agent holds a fake login
    data = (yaml.safe_load(policy.read_text()) if policy.exists() else None) or {}
    creds = dict(data.get('credentials') or {})
    creds[name] = {'mode': 'env', 'var': 'SCIEFLOW_FIXTURE_TOKEN', 'file': 'fixture.token',
                   'reason': 'fake fixture token'}
    policy.write_text(yaml.safe_dump({**data, 'credentials': creds}))


def read_doc(project):
    return conversation.read(project.run_dir('r1'))


def revision(project):
    return conversation.selection_revision(read_doc(project))


def snapshot_state(project):
    """Conversation bytes + job records: byte-identical proof for negatives."""
    ws = project.run_dir('r1')
    return {'conversation': (ws / 'conversation.yml').read_bytes(),
            'jobs': sorted((p.name, p.read_bytes())
                           for p in (ws / 'jobs').glob('*.json')) if (ws / 'jobs').exists() else []}


def landing(client, response):
    return client.get(response.headers['location']) if response.status_code == 303 else response


# ---------------------------------------------------------------------------
# T2: send form variants — mode + selection_revision; canonical decimal.
# ---------------------------------------------------------------------------

def test_send_absent_mode_is_chat(web_k_project, client):
    """No mode field at all -> ordinary chat turn, unchanged legacy behavior."""
    response = post(client, '/runs/r1/say', message='hello there')
    assert response.status_code == 303
    assert read_doc(web_k_project)['turns'][0]['text'] == 'hello there'


def test_send_explicit_chat_mode(web_k_project, client):
    response = post(client, '/runs/r1/say', message='hi', mode='chat',
                   selection_revision=revision(web_k_project))
    assert response.status_code == 303
    assert read_doc(web_k_project)['turns'][0]['text'] == 'hi'


def test_send_harness_command_mode_accepted_but_not_enabled(web_k_project, client):
    """mode=harness-command with a correct revision is ACCEPTED as a turn,
    but with CERTIFIED_POLICIES empty it must not enable command execution:
    a visible unsupported/incapable message and normal chat behavior (the
    message is still delivered as an ordinary charter-first turn)."""
    response = post(client, '/runs/r1/say', message='/scieflow-menu',
                    mode='harness-command',
                    selection_revision=revision(web_k_project))
    assert response.status_code == 303
    page = landing(client, response).text
    assert ('not certified' in page.lower() or 'unsupported' in page.lower()
            or 'incapable' in page.lower())
    assert read_doc(web_k_project)['turns'][0]['text'] == '/scieflow-menu'


def test_send_invalid_mode_enum_is_controlled_rejection(web_k_project, client):
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', message='hi', mode='shell-command')
    assert response.status_code in (303, 400)
    assert 'mode' in landing(client, response).text.lower()
    assert snapshot_state(web_k_project) == before


@pytest.mark.parametrize('bad', ['01', '-1', '1.0', '١', 'abc', ''])
def test_send_malformed_revision_is_controlled_rejection(web_k_project, client, bad):
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', message='hi', selection_revision=bad)
    assert response.status_code in (303, 400)
    assert snapshot_state(web_k_project) == before


def test_send_negative_revision_is_controlled_rejection(web_k_project, client):
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', message='hi', selection_revision='-2')
    assert response.status_code in (303, 400)
    assert snapshot_state(web_k_project) == before


def test_send_stale_revision_refuses(web_k_project, client):
    conversation.set_agent(web_k_project.run_dir('r1'), 'web-k-fixture')  # bump
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', message='hi', selection_revision='0')
    assert response.status_code in (303, 400)
    assert 'stale' in landing(client, response).text.lower()
    assert snapshot_state(web_k_project) == before


def test_send_duplicate_form_keys_refuse(web_k_project, client):
    """Two selection_revision fields in one body is ambiguity, not a value."""
    token = client.cookies[auth.CSRF_COOKIE]
    body = (f'csrf_token={token}&message=hi'
            '&selection_revision=0&selection_revision=0')
    response = client.post('/runs/r1/say', content=body,
                           headers={'content-type': 'application/x-www-form-urlencoded'},
                           follow_redirects=False)
    assert response.status_code in (303, 400)
    assert read_doc(web_k_project)['turns'] == []


def test_api_send_accepts_mode_and_revision(web_k_project, client):
    response = api_post(client, '/api/v1/runs/r1/conversation', message='api hello',
                        mode='chat', selection_revision=revision(web_k_project))
    assert response.status_code == 200
    doc = read_doc(web_k_project)
    assert doc['turns'][0]['text'] == 'api hello'
    assert doc['turns'][0]['audit']['input_kind'] == 'chat'


def test_api_send_invalid_mode_is_controlled_rejection(web_k_project, client):
    before = snapshot_state(web_k_project)
    response = api_post(client, '/api/v1/runs/r1/conversation', message='hi',
                        mode='shell-command')
    assert response.status_code == 400
    assert 'mode' in response.text.lower()
    assert snapshot_state(web_k_project) == before


def test_api_send_malformed_revision_is_controlled_rejection(web_k_project, client):
    before = snapshot_state(web_k_project)
    response = api_post(client, '/api/v1/runs/r1/conversation', message='hi',
                        selection_revision='01')
    assert response.status_code == 400
    assert snapshot_state(web_k_project) == before


def test_api_send_stale_revision_refuses(web_k_project, client):
    conversation.set_agent(web_k_project.run_dir('r1'), 'web-k-fixture')  # bump
    before = snapshot_state(web_k_project)
    response = api_post(client, '/api/v1/runs/r1/conversation', message='hi',
                        selection_revision='0')
    assert response.status_code in (400, 404, 409)
    assert snapshot_state(web_k_project) == before


def test_send_harness_command_stale_revision_refuses(web_k_project, client):
    """Fix-F1 page node: a marked send carries its revision even when it is
    downgraded to chat. After a revision bump, mode=harness-command with the
    OLD revision must refuse (stale), leaving state byte-identical and
    writing no turn — the same refusal the chat-mode send already gets, not
    a silent drop of the revision on the downgrade path."""
    conversation.set_agent(web_k_project.run_dir('r1'), 'web-k-fixture')  # bump
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', message='/scieflow-menu',
                    mode='harness-command', selection_revision='0')
    assert response.status_code in (303, 400)
    assert 'stale' in landing(client, response).text.lower()
    assert snapshot_state(web_k_project) == before
    assert read_doc(web_k_project)['turns'] == []


def test_api_send_harness_command_stale_revision_refuses(web_k_project, client):
    """Fix-F1 API node: the same stale refusal on the API send surface —
    the same status set the existing chat-mode stale API node accepts, with
    state byte-identical and no turn written."""
    conversation.set_agent(web_k_project.run_dir('r1'), 'web-k-fixture')  # bump
    before = snapshot_state(web_k_project)
    response = api_post(client, '/api/v1/runs/r1/conversation',
                        message='/scieflow-menu',
                        mode='harness-command', selection_revision='0')
    assert response.status_code in (400, 404, 409)
    assert snapshot_state(web_k_project) == before
    assert read_doc(web_k_project)['turns'] == []


def test_send_harness_command_current_revision_still_accepted_as_chat(
        web_k_project, client):
    """Fix-F1 positive guard: the stale refusal must not break the accepted
    path — a harness-command send with the CURRENT revision still lands as
    an ordinary chat turn with the visible not-certified notice."""
    response = post(client, '/runs/r1/say', message='/scieflow-menu',
                    mode='harness-command',
                    selection_revision=revision(web_k_project))
    assert response.status_code == 303
    page = landing(client, response).text
    assert ('not certified' in page.lower() or 'unsupported' in page.lower()
            or 'incapable' in page.lower())
    assert read_doc(web_k_project)['turns'][0]['text'] == '/scieflow-menu'
    assert read_doc(web_k_project)['turns'][0]['audit']['input_kind'] == 'chat'


# ---------------------------------------------------------------------------
# T2/T3: CSRF/session guards on every mutating variant, both modes.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path', ['/runs/r1/say', '/api/v1/runs/r1/conversation'])
def test_k_marked_form_needs_csrf(client, path):
    """Signed-in cookie, correct form fields, but NO CSRF field: must 403.

    Uses the signed-in client so this is a CSRF control, not accidental
    no-session rejection.
    """
    response = client.post(path, data={'message': '/scieflow-menu',
                                       'mode': 'harness-command',
                                       'selection_revision': '0'},
                           follow_redirects=False)
    assert response.status_code == 403


@pytest.mark.parametrize('path', ['/runs/r1/say', '/api/v1/runs/r1/conversation'])
def test_k_marked_form_header_csrf_positive(client, web_k_project, path):
    """Signed-in cookie + CSRF header, no form field: both modes must pass
    the guard layer itself (script-client compatibility). The turn itself
    may be refused by revision semantics; it must NOT be a 401/403."""
    response = client.post(path, data={'message': 'header mode',
                                       'mode': 'chat',
                                       'selection_revision': revision(web_k_project)},
                           headers={auth.CSRF_HEADER: client.cookies[auth.CSRF_COOKIE]},
                           follow_redirects=False)
    assert response.status_code not in (401, 403)


@pytest.mark.parametrize('path', ['/runs/r1/say', '/api/v1/runs/r1/conversation'])
def test_k_marked_form_anonymous_is_401(project, path):
    with TestClient(create_app(project, 'tok')) as anonymous:
        anonymous.cookies.set(auth.CSRF_COOKIE, 'x')
        response = anonymous.post(path, data={auth.CSRF_FIELD: 'x', 'message': 'hi',
                                              'mode': 'chat', 'selection_revision': '0'})
        assert response.status_code == 401


@pytest.mark.parametrize('path', ['/runs/r1/say', '/api/v1/runs/r1/conversation'])
def test_k_marked_form_wrong_session_is_401(project, path):
    """A session cookie issued by a DIFFERENT app instance is not a session
    here, even with a matching CSRF pair (the CSRF cookie is not secret)."""
    with TestClient(create_app(project, 'tok-a')) as owner, \
            TestClient(create_app(project, 'tok-b')) as stranger:
        owner.get('/healthz?token=tok-a')
        stranger.cookies.set(auth.SESSION_COOKIE, owner.cookies[auth.SESSION_COOKIE])
        stranger.cookies.set(auth.CSRF_COOKIE, owner.cookies[auth.CSRF_COOKIE])
        response = stranger.post(path, data={auth.CSRF_FIELD: owner.cookies[auth.CSRF_COOKIE],
                                             'message': 'hi', 'mode': 'chat',
                                             'selection_revision': '0'})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# T2: hand-over form variants — expected_revision / reset_session /
# confirm_new_session; controlled rejection.
# ---------------------------------------------------------------------------

def test_handover_same_manager_no_reset_bumps_but_preserves(web_k_project, client):
    conversation.record_session(web_k_project.run_dir('r1'), 'keep-me')
    current = revision(web_k_project)
    response = post(client, '/runs/r1/say', action='agent', agent='web-k-fixture',
                    expected_revision=current)
    assert response.status_code == 303
    doc = read_doc(web_k_project)
    assert doc['session'] == 'keep-me'
    assert int(doc['selection_generation']) == int(current) + 1


def test_handover_broken_to_eligible_replacement(web_k_project, client):
    """A broken current selection must not block selection of an eligible
    target; the switch lands and history is preserved."""
    ws = web_k_project.run_dir('r1')
    conversation.add_turn(ws, role='human', text='retained history')
    path = web_k_project.root / 'config' / 'agents.yml'
    data = yaml.safe_load(path.read_text())
    data['agents']['web-k-fixture']['session_cmd'] = '/absent/executable {prompt}'
    path.write_text(yaml.safe_dump(data))
    response = post(client, '/runs/r1/say', action='agent', agent='stub',
                    expected_revision=revision(web_k_project))
    assert response.status_code == 303
    doc = read_doc(web_k_project)
    assert doc['agent'] == 'stub'
    assert doc['turns'][0]['text'] == 'retained history'


def test_handover_malformed_revision_refuses(web_k_project, client):
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', action='agent', agent='stub2',
                   expected_revision='01')
    assert response.status_code in (303, 400)
    assert snapshot_state(web_k_project) == before


def test_handover_stale_revision_refuses(web_k_project, client):
    conversation.set_agent(web_k_project.run_dir('r1'), 'web-k-fixture')  # bump
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', action='agent', agent='stub',
                   expected_revision='0')
    assert response.status_code in (303, 400)
    assert 'stale' in landing(client, response).text.lower()
    assert snapshot_state(web_k_project) == before


def test_handover_unknown_target_refuses_without_state_change(web_k_project, client):
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', action='agent', agent='no-such-agent',
                   expected_revision=revision(web_k_project))
    assert response.status_code in (303, 400, 404)
    assert snapshot_state(web_k_project) == before


def test_reset_requires_confirmation_and_revision(web_k_project, client):
    """reset_session=true without confirm_new_session=true refuses, state
    byte-identical; an explicit reset without any revision also refuses."""
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', action='agent', agent='web-k-fixture',
                   reset_session='true', expected_revision=revision(web_k_project))
    assert response.status_code in (303, 400)
    assert 'confirm' in landing(client, response).text.lower()
    assert snapshot_state(web_k_project) == before
    response = post(client, '/runs/r1/say', action='agent', agent='web-k-fixture',
                   reset_session='true', confirm_new_session='true')
    assert response.status_code in (303, 400)
    assert snapshot_state(web_k_project) == before


def test_reset_stale_revision_refuses(web_k_project, client):
    conversation.set_agent(web_k_project.run_dir('r1'), 'web-k-fixture')  # bump
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', action='agent', agent='web-k-fixture',
                   reset_session='true', confirm_new_session='true',
                   expected_revision='0')
    assert response.status_code in (303, 400)
    assert 'stale' in landing(client, response).text.lower()
    assert snapshot_state(web_k_project) == before


def test_confirmed_reset_produces_new_session(web_k_project, client):
    ws = web_k_project.run_dir('r1')
    conversation.record_session(ws, 'old-session')
    response = post(client, '/runs/r1/say', action='agent', agent='web-k-fixture',
                   reset_session='true', confirm_new_session='true',
                   expected_revision=revision(web_k_project))
    assert response.status_code == 303
    doc = read_doc(web_k_project)
    assert doc['session'] is None
    assert doc['recovery_required'] is False and doc['pending_turn_id'] is None


def test_api_handover_variant(web_k_project, client):
    response = api_post(client, '/api/v1/runs/r1/conversation/agent', agent='stub',
                        expected_revision=revision(web_k_project))
    assert response.status_code == 200
    assert read_doc(web_k_project)['agent'] == 'stub'


def test_api_reset_without_confirm_refuses(web_k_project, client):
    before = snapshot_state(web_k_project)
    response = api_post(client, '/api/v1/runs/r1/conversation/agent',
                       agent='web-k-fixture', reset_session='true',
                       expected_revision=revision(web_k_project))
    assert response.status_code in (400, 404, 409)
    assert snapshot_state(web_k_project) == before


def test_api_confirmed_reset_variant(web_k_project, client):
    response = api_post(client, '/api/v1/runs/r1/conversation/agent',
                        agent='web-k-fixture', reset_session='true',
                        confirm_new_session='true',
                        expected_revision=revision(web_k_project))
    assert response.status_code == 200
    assert read_doc(web_k_project)['session'] is None


# ---------------------------------------------------------------------------
# T3: R5 matrix — recovery/uncertainty, busy admission, malformed
# generation, selection-probe instrumentation.
# ---------------------------------------------------------------------------

def test_uncertain_state_send_refuses_and_recovery_reset_clears(web_k_project, client):
    ws = web_k_project.run_dir('r1')
    conversation.record_session(ws, 'held-session')
    doc = yaml.safe_load((ws / 'conversation.yml').read_text())
    doc['recovery_required'] = True
    (ws / 'conversation.yml').write_text(yaml.safe_dump(doc))
    before = snapshot_state(web_k_project)
    response = post(client, '/runs/r1/say', message='while uncertain',
                    selection_revision=revision(web_k_project))
    assert response.status_code in (303, 400)
    assert 'recovery' in landing(client, response).text.lower()
    assert snapshot_state(web_k_project) == before
    # A confirmed revision-checked reset is the deliberate recovery path.
    response = post(client, '/runs/r1/say', action='agent', agent='web-k-fixture',
                   reset_session='true', confirm_new_session='true',
                   expected_revision=revision(web_k_project))
    assert response.status_code == 303
    assert read_doc(web_k_project)['recovery_required'] is False


def test_same_manager_non_reset_preserves_uncertainty(web_k_project, client):
    """A same-manager hand-over without reset bumps the generation but must
    NOT silently clear K uncertainty or the session."""
    ws = web_k_project.run_dir('r1')
    conversation.record_session(ws, 'held-session')
    doc = yaml.safe_load((ws / 'conversation.yml').read_text())
    doc['recovery_required'] = True
    (ws / 'conversation.yml').write_text(yaml.safe_dump(doc))
    response = post(client, '/runs/r1/say', action='agent', agent='web-k-fixture',
                    expected_revision=revision(web_k_project))
    assert response.status_code == 303
    doc = read_doc(web_k_project)
    assert doc['session'] == 'held-session'
    assert doc['recovery_required'] is True


def test_busy_admission_refuses_without_state_change(web_k_project, client):
    """A running TURN job holds admission: a new send refuses, byte-identical."""
    project = web_k_project
    job, proc = jobs.start(project, [sys.executable, '-c', 'import time; time.sleep(30)'],
                           kind='turn', cwd=project.root, run_dir=project.run_dir('r1'))
    try:
        before = snapshot_state(project)
        response = post(client, '/runs/r1/say', message='while busy',
                        selection_revision=revision(project))
        assert response.status_code in (303, 400)
        text = landing(client, response).text.lower()
        assert 'running' in text or 'busy' in text
        assert snapshot_state(project) == before
    finally:
        jobs.cancel(job)
        jobs.wait(job, proc)


def test_malformed_generation_is_display_integrity_error(web_k_project, client):
    """A malformed stored selection_generation must render a visible
    integrity error, not a silent zero-rewrite or a 500."""
    ws = web_k_project.run_dir('r1')
    doc = yaml.safe_load((ws / 'conversation.yml').read_text())
    doc['selection_generation'] = 'broken'
    (ws / 'conversation.yml').write_text(yaml.safe_dump(doc))
    response = client.get('/runs/r1')
    assert response.status_code == 200
    assert 'integrity' in response.text.lower() or 'malformed' in response.text.lower()


def test_stale_revision_conflict_message_on_page(web_k_project, client):
    """Stale revision on a send shows a conflict message naming revision."""
    conversation.set_agent(web_k_project.run_dir('r1'), 'web-k-fixture')  # bump
    response = post(client, '/runs/r1/say', message='stale try',
                    selection_revision='0')
    page = landing(client, response).text.lower()
    assert 'stale' in page and 'revision' in page


def test_handover_is_target_only_no_launch_probes(web_k_project, client, monkeypatch):
    """R5 web adaptation: a guarded selection must not launch, charter,
    certify or spend — instrument the forbidden calls and one policy load."""
    from scieflow.core import agent_run, harness_commands, sandbox
    from scieflow.core.run import charter

    project = web_k_project
    ws = project.run_dir('r1')
    conversation.add_turn(ws, role='human', text='retained history')
    path = project.root / 'config/agents.yml'
    data = yaml.safe_load(path.read_text())
    data['agents']['web-k-fixture']['session_cmd'] = '/absent/executable {prompt}'
    path.write_text(yaml.safe_dump(data))

    def forbidden(*args, **kwargs):
        pytest.fail('selection touched launch/charter/certification/spend')

    for obj, name in [(agent_run, 'resolve_turn'), (agent_run, 'prepare_turn'),
                      (charter, 'snapshot'), (harness_commands, 'support_for'),
                      (service.actions, 'record_spend'), (jobs, 'start')]:
        monkeypatch.setattr(obj, name, forbidden)
    loads = []
    original = sandbox.resolve_turn_protection
    monkeypatch.setattr(sandbox, 'resolve_turn_protection',
                        lambda *a, **kw: loads.append(1) or original(*a, **kw))
    response = post(client, '/runs/r1/say', action='agent', agent='stub',
                    expected_revision=revision(project))
    assert response.status_code == 303
    assert read_doc(project)['agent'] == 'stub'
    assert len(loads) == 1, 'exactly one policy load during selection'


# ---------------------------------------------------------------------------
# T4: composer/history/recovery UI display.
# ---------------------------------------------------------------------------

def test_run_page_has_mode_composer(web_k_project, client):
    page = client.get('/runs/r1').text
    assert 'name="mode"' in page


def test_run_page_renders_revision_token_for_both_forms(web_k_project, client):
    current = revision(web_k_project)
    page = client.get('/runs/r1').text
    assert f'name="selection_revision" value="{current}"' in page
    assert f'name="expected_revision" value="{current}"' in page


def test_per_turn_mode_badge_in_history(web_k_project, client):
    """Each audited turn shows its composition mode as inert text."""
    ws = web_k_project.run_dir('r1')
    conversation.add_turn(ws, role='human', text='plain hello')
    conversation.add_turn(ws, role='agent', text='reply', job_id='')
    page = client.get('/runs/r1').text
    assert 'mode-badge' in page and re.search(r'mode-badge[^>]*>\s*chat\s*<', page)


def test_unsupported_mode_badge_for_uncertified_marked_turn(web_k_project, client):
    """A stored marked turn with an empty policy_revision badges as
    'unsupported', never as a claim of enablement."""
    ws = web_k_project.run_dir('r1')
    doc = yaml.safe_load((ws / 'conversation.yml').read_text())
    doc['turns'] = [{'role': 'human', 'text': '/scieflow-menu',
                     'audit': {'input_kind': 'harness-command', 'policy_revision': ''}}]
    (ws / 'conversation.yml').write_text(yaml.safe_dump(doc))
    page = client.get('/runs/r1').text
    assert re.search(r'mode-badge[^>]*>\s*unsupported\s*<', page)


def test_mode_badge_invariant_under_xss(web_k_project, client):
    """A stored audit mode value containing markup renders escaped and the
    badge text is inert."""
    ws = web_k_project.run_dir('r1')
    doc = yaml.safe_load((ws / 'conversation.yml').read_text())
    doc['turns'] = [{'role': 'human', 'text': 'hi',
                     'audit': {'input_kind': '<script>alert(1)</script>'}}]
    (ws / 'conversation.yml').write_text(yaml.safe_dump(doc))
    page = client.get('/runs/r1')
    assert page.status_code == 200
    assert '<script>alert(1)</script>' not in page.text
    assert '&lt;script&gt;' in page.text
    assert 'mode-badge' in page.text


def test_job_page_renders_turn_mode_badge(web_k_project, client):
    """job.html shows the conversation job's mode badge as inert text."""
    response = api_post(client, '/api/v1/runs/r1/conversation', message='badge turn',
                        mode='chat', selection_revision=revision(web_k_project))
    assert response.status_code == 200
    job_id = response.json()['job']['id']
    page = client.get(f'/runs/r1/jobs/{job_id}').text
    assert 'mode-badge' in page


def test_composer_disabled_when_busy(web_k_project, client):
    project = web_k_project
    job, proc = jobs.start(project, [sys.executable, '-c', 'import time; time.sleep(30)'],
                           kind='turn', cwd=project.root, run_dir=project.run_dir('r1'))
    try:
        page = client.get('/runs/r1').text
        send_form = page.split('name="message"', 1)[1].split('</form>', 1)[0]
        assert 'disabled' in send_form
    finally:
        jobs.cancel(job)
        jobs.wait(job, proc)


def test_failed_turn_shows_recovery_notice_not_auto_retry(web_k_project, client):
    """A stored recovery_required turn renders a visible recovery notice
    with an affordance, not an automatic resend."""
    ws = web_k_project.run_dir('r1')
    doc = yaml.safe_load((ws / 'conversation.yml').read_text())
    doc['recovery_required'] = True
    doc['turns'] = doc.get('turns', []) + [
        {'role': 'human', 'text': '/scieflow-menu', 'ts': '2026-10-12T00:00:00'}]
    (ws / 'conversation.yml').write_text(yaml.safe_dump(doc))
    page = client.get('/runs/r1').text.lower()
    assert 'recovery' in page
    assert 'reset' in page or 'retry' in page


# ---------------------------------------------------------------------------
# T5: DOM behavior — repeat suppression, reset confirmation, EventSource
# termination on selection/reset, badge inertness. One real DOM gate.
# ---------------------------------------------------------------------------

def test_dom_composer_repeat_suppression_and_reset_controls(web_k_project, client, tmp_path):
    """REAL DOM (installed jsdom): rapid duplicate submissions are blocked;
    reset without confirm is blocked with a visible message; a confirmed
    selection/reset terminates the timeline EventSource (no replay/refresh);
    mode badges render as inert text."""
    ws = web_k_project.run_dir('r1')
    conversation.add_turn(ws, role='human', text='hello')
    conversation.add_turn(ws, role='agent', text='reply', job_id='')
    page = client.get('/runs/r1').text
    source = tmp_path / 'rendered-run.html'
    source.write_text(page)
    harness = Path(__file__).with_name('k_task7_dom.cjs')
    result = subprocess.run(['node', str(harness), str(source), str(JS)],
                            capture_output=True, text=True, timeout=15)
    print('TASK7_REAL_DOM', result.stdout, result.stderr)
    assert result.returncode == 0
    assert 'TASK7_REAL_DOM_PASS' in result.stdout


# ---------------------------------------------------------------------------
# T5: real-socket gate — health/SSE/cancel during an admission-holding turn.
# ---------------------------------------------------------------------------

def test_socket_health_sse_cancel_during_admission_hold(web_k_project, client):
    """REAL socket: while a synthetic K turn holds admission, /healthz
    answers <2s, the SSE stream still delivers, and a guarded cancel
    executes against the admission-holding job. Afterwards the page shows
    the turn's mode badge."""
    import socket as socket_mod

    project = web_k_project
    ws = project.run_dir('r1')
    import shlex
    prefix = f'{shlex.quote(sys.executable)} -B {shlex.quote(str(PROTOCOL))} --hold 8'
    register_agent(project, 'web-k-hold', prefix)
    conversation.set_agent(ws, 'web-k-hold')
    current = revision(project)

    with socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(create_app(project, LIVE_TOKEN), host='127.0.0.1',
                            port=port, log_level='warning', lifespan='off')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5.0
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    assert server.started, 'uvicorn did not start in time'
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=30.0) as live:
            live.get(f'/healthz?token={LIVE_TOKEN}')   # exchange token for cookies
            csrf = live.cookies[auth.CSRF_COOKIE]
            result = {}

            def send():
                response = live.post('/runs/r1/say',
                                     data={'message': 'hold this', 'mode': 'chat',
                                           'selection_revision': current},
                                     headers={auth.CSRF_HEADER: csrf})
                result['status'] = response.status_code

            sender = threading.Thread(target=send, daemon=True)
            sender.start()
            job = None
            deadline = time.time() + 10.0
            while time.time() < deadline:
                running = [j for j in jobs.list_jobs(project, ws)
                           if j.state == 'running' and j.kind == 'turn']
                if running:
                    job = running[0]
                    break
                time.sleep(0.02)
            assert job is not None, 'the admission-holding turn never started'

            start = time.monotonic()
            health = live.get('/healthz')
            elapsed = time.monotonic() - start
            assert health.status_code == 200
            assert elapsed < 2.0, f'/healthz took {elapsed:.1f}s during admission hold'

            with live.stream('GET', '/api/v1/runs/r1/events/stream') as response:
                assert response.status_code == 200
                assert response.headers['content-type'].startswith('text/event-stream')
                lines = response.iter_lines()
                assert next(line for line in lines if line.startswith('data:'))

            cancel = live.post(f'/api/v1/jobs/{job.id}/cancel',
                               headers={auth.CSRF_HEADER: csrf})
            assert cancel.status_code == 200

            sender.join(timeout=20.0)
            assert sender.is_alive() is False
            assert result.get('status') == 303, result
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)

    page = client.get('/runs/r1').text
    assert 'mode-badge' in page


# ---------------------------------------------------------------------------
# Bias/rebinding and baseline controls.
# ---------------------------------------------------------------------------

def test_valid_no_origin_request_remains_accepted(web_k_project, client):
    """A valid send with no Origin header stays accepted: Task7 adds no
    universal Origin requirement."""
    response = post(client, '/runs/r1/say', message='no origin here',
                    selection_revision=revision(web_k_project))
    assert response.status_code == 303


def test_origin_header_present_valid_post_accepted(web_k_project, client):
    """An Origin header on an otherwise-valid request does not refuse it;
    trust is not derived from Origin."""
    response = client.post('/runs/r1/say',
                           data={auth.CSRF_FIELD: client.cookies[auth.CSRF_COOKIE],
                                 'message': 'with origin', 'mode': 'chat',
                                 'selection_revision': revision(web_k_project)},
                           headers={auth.CSRF_HEADER: client.cookies[auth.CSRF_COOKIE],
                                    'Origin': 'http://evil.example'},
                           follow_redirects=False)
    assert response.status_code == 303


def test_host_mismatch_does_not_bypass_guards(client):
    """A foreign Host header gets no trust: the CSRF guard still refuses an
    unauthenticated mutation."""
    response = client.post('/runs/r1/say', data={'message': 'x'},
                           headers={'Host': 'evil.example'})
    assert response.status_code == 403


def test_malformed_multipart_is_controlled_400(web_k_project, client):
    """A malformed multipart body to a conversation variant is a controlled
    400 from the middleware, matching the retained auth behavior."""
    response = client.post('/runs/r1/say', content=b'not actually multipart',
                           headers={'content-type': 'multipart/form-data; boundary=zzz'},
                           follow_redirects=False)
    assert response.status_code == 400
    assert response.json()['error']


# ---------------------------------------------------------------------------
# Guard inventory extension (T1/T3): mode variants in SAMPLES coverage.
# ---------------------------------------------------------------------------

def test_mutating_samples_carry_mode_variants():
    """The SAMPLES-driven guard coverage must include mode-bearing send and
    hand-over/reset field variants, without duplicating or removing route
    inventory keys."""
    from tests.web import mutating_paths

    assert set(mutating_paths.SAMPLES) == set(mutating_paths.MUTATING_PATHS)
    assert 'mode' in mutating_paths.SAMPLES['/runs/{slug}/say']
    assert 'selection_revision' in mutating_paths.SAMPLES['/runs/{slug}/say']
    assert 'mode' in mutating_paths.SAMPLES['/api/v1/runs/{slug}/conversation']
    assert 'expected_revision' in mutating_paths.SAMPLES['/api/v1/runs/{slug}/conversation/agent']
    assert 'reset_session' in mutating_paths.SAMPLES['/api/v1/runs/{slug}/conversation/agent']
