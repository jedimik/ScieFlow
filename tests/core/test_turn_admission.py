"""Task3 isolated kernel admission and complete protection controls."""
from dataclasses import replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import threading

import pytest

from scieflow.core import agent_run, sandbox, turn_admission as admission
from scieflow.core.project import Project
from scieflow.core.turn_input import accept_human_submission


def _key(project, ws):
    raw = json.dumps(['scieflow.turn-admission.v1', str(project.root.resolve()), str(ws.resolve())],
                     ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return project.root.resolve() / '.scieflow/turn-admission' / ('v1-' + hashlib.sha256(raw).hexdigest() + '.lock')


def _hold(pipe, root, run):
    try:
        with admission.reserve(Project(Path(root)), Path(run)):
            pipe.send('held')
            pipe.recv()
        pipe.send('released')
    except BaseException as exc:
        pipe.send(type(exc).__name__ + ':' + str(exc))
    finally:
        pipe.close()


def _attempt(pipe, root, run):
    try:
        with admission.reserve(Project(Path(root)), Path(run)):
            pipe.send('held')
    except admission.AdmissionError as exc:
        pipe.send(type(exc).__name__)
    finally:
        pipe.close()


def _process(ctx, function, project, ws):
    parent, child = ctx.Pipe()
    process = ctx.Process(target=function, args=(child, str(project.root), str(ws)))
    process.start()
    child.close()
    return process, parent


def _receive(pipe):
    assert pipe.poll(10), 'no bounded pipe acknowledgement'
    return pipe.recv()


def test_t3_separate_processes_aliases_and_other_run(project, tmp_path):
    # The parent NEVER opens this lock before either fork. Both children
    # independently open it; neither can inherit an owner description.
    ctx = multiprocessing.get_context('fork')
    ws = project.run_dir('r1')
    other = project.run_dir('r2')
    other.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(project.root, target_is_directory=True)
    owner, pipe = _process(ctx, _hold, project, ws)
    try:
        assert _receive(pipe) == 'held'
        for candidate_project, candidate_run, expected in (
            (project, ws, 'AdmissionBusy'), (Project(alias), alias / 'workspace/r1', 'AdmissionBusy'),
            (project, other, 'held')):
            child, ack = _process(ctx, _attempt, candidate_project, candidate_run)
            try:
                assert _receive(ack) == expected
                child.join(10)
                assert child.exitcode == 0
            finally:
                if child.is_alive():
                    child.terminate()
                    child.join(10)
                ack.close()
        pipe.send('release')
        assert _receive(pipe) == 'released'
        owner.join(10)
    finally:
        if owner.is_alive():
            owner.terminate()
        owner.join(10)
        pipe.close()
    assert owner.exitcode == 0
    with admission.reserve(project, ws):
        pass


def test_t3_thread_exception_release_stable_inode_and_descriptors(project):
    ws = project.run_dir('r1')
    with admission.reserve(project, ws):
        lock = _key(project, ws)
    inode = lock.stat().st_ino
    before = set(os.listdir('/proc/self/fd'))
    seen = []
    with pytest.raises(RuntimeError, match='exit'):
        with admission.reserve(project, ws) as held:
            assert all(not os.get_inheritable(fd) for fd in held._fds.values())
            def contender():
                try:
                    with admission.reserve(project, ws):
                        seen.append('incorrectly admitted')
                except admission.AdmissionBusy:
                    seen.append('busy')
            thread = threading.Thread(target=contender)
            thread.start()
            thread.join(10)
            assert not thread.is_alive() and seen == ['busy']
            raise RuntimeError('exit')
    assert set(os.listdir('/proc/self/fd')) == before
    assert lock.stat().st_ino == inode and lock.read_bytes() == b''
    with admission.reserve(project, ws):
        pass


def test_t3_key_state_override_and_unicode(project, monkeypatch):
    ws = project.run_dir('r1')
    unicode_run = project.workspace_root / 'é research'
    unicode_run.mkdir()
    monkeypatch.setenv('SCIEFLOW_STATE_DIR', str(ws / 'hostile-state'))
    with admission.reserve(project, unicode_run):
        assert _key(project, unicode_run).is_file()
    assert not (ws / 'hostile-state').exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'directory', 'nonempty', 'mode'])
def test_t3_suspicious_lock_not_repaired(project, kind):
    ws = project.run_dir('r1')
    with admission.reserve(project, ws):
        pass
    lock = _key(project, ws)
    sentinel = project.root / 'sentinel'
    sentinel.write_text('outside')
    lock.unlink()
    if kind == 'symlink':
        lock.symlink_to(sentinel)
    elif kind == 'hardlink':
        os.link(sentinel, lock)
    elif kind == 'fifo':
        os.mkfifo(lock)
    elif kind == 'directory':
        lock.mkdir()
    elif kind == 'nonempty':
        lock.write_bytes(b'corrupt')
        lock.chmod(0o600)
    else:
        lock.touch(mode=0o666)
        lock.chmod(0o666)
    before = lock.lstat()
    with pytest.raises(admission.AdmissionUnsafe):
        with admission.reserve(project, ws):
            pytest.fail('unsafe lock admitted')
    assert lock.lstat().st_ino == before.st_ino and lock.lstat().st_mode == before.st_mode
    assert sentinel.read_text() == 'outside'


@pytest.mark.parametrize('component', ['root', 'workspace', 'run', 'state', 'admission', 'lock'])
def test_t3_held_path_replacement_refuses(project, component):
    ws = project.run_dir('r1')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        paths = {'root': project.root, 'workspace': project.workspace_root, 'run': ws,
                 'state': project.root / '.scieflow', 'admission': _key(project, ws).parent,
                 'lock': _key(project, ws)}
        target = paths[component]
        backup = target.with_name(target.name + '-moved')
        target.rename(backup)
        try:
            if component == 'lock':
                target.touch(mode=0o600)
            else:
                target.mkdir()
            with pytest.raises(admission.AdmissionUnsafe, match='identity|changed'):
                held.validate_protection(protection)
        finally:
            if component == 'lock':
                target.unlink()
            else:
                target.rmdir()
            backup.rename(target)


@pytest.mark.parametrize('component', ['state', 'admission'])
def test_t3_admission_ancestor_symlink_or_unsafe_mode(project, component):
    ws = project.run_dir('r1')
    with admission.reserve(project, ws):
        pass
    target = project.root / '.scieflow'
    if component == 'admission':
        target /= 'turn-admission'
    target.chmod(0o777)
    with pytest.raises(admission.AdmissionUnsafe):
        with admission.reserve(project, ws):
            pass
    target.chmod(0o700)
    backup = target.with_name(target.name + '-moved')
    target.rename(backup)
    target.symlink_to(backup, target_is_directory=True)
    try:
        with pytest.raises(admission.AdmissionUnsafe):
            with admission.reserve(project, ws):
                pass
    finally:
        target.unlink()
        backup.rename(target)


def test_t3_safe_directory_churn_and_read_atime_are_not_replacement(project):
    ws = project.run_dir('r1')
    policy = project.root / sandbox.ALLOWLIST_FILE
    policy.write_text('writable: []\n')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        held.validate_protection(protection)
        for directory in (ws, project.root, project.workspace_root, policy.parent, _key(project, ws).parent):
            (directory / 'ordinary-child').mkdir()
            (directory / 'ordinary-child/file').write_text('ordinary churn')
        policy.read_bytes()
        held.validate_protection(protection)


@pytest.mark.parametrize('change', ['content', 'replace', 'remove', 'create', 'alias'])
def test_t3_policy_and_alias_mutations_invalidate(project, change):
    ws = project.run_dir('r1')
    policy = project.root / sandbox.ALLOWLIST_FILE
    if change != 'create':
        policy.write_text('writable: []\n')
    if change == 'alias':
        extra = project.root / 'extra'
        extra.mkdir()
        alias = project.root / 'grant-alias'
        alias.symlink_to(extra, target_is_directory=True)
        policy.write_text('writable:\n  - path: grant-alias\n')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        held.validate_protection(protection)
        if change == 'content':
            policy.write_text('writable: []\n# modified\n')
        elif change == 'replace':
            replacement = policy.with_suffix('.new')
            replacement.write_bytes(policy.read_bytes())
            replacement.replace(policy)
        elif change == 'remove':
            policy.unlink()
        elif change == 'create':
            policy.write_text('writable: []\n')
        else:
            alias.unlink()
            alias.symlink_to(ws, target_is_directory=True)
        with pytest.raises(admission.AdmissionUnsafe):
            held.validate_protection(protection)


@pytest.mark.parametrize('grant', ['.scieflow', '.scieflow/turn-admission', 'state-alias'])
def test_t3_project_wide_unsafe_grants_and_aliases(project, grant):
    ws = project.run_dir('r1')
    with admission.reserve(project, ws) as held:
        if grant == 'state-alias':
            (project.root / grant).symlink_to(project.root / '.scieflow', target_is_directory=True)
        (project.root / sandbox.ALLOWLIST_FILE).write_text(f'writable:\n  - path: {grant}\n')
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        assert protection.coordinator_writable[0] == project.workspace_root
        with pytest.raises(admission.AdmissionUnsafe, match='grant|writable'):
            held.validate_protection(protection)


@pytest.mark.parametrize('bad', ['object', 'incomplete', 'run', 'project', 'coordinator', 'writable', 'mask', 'unconfined'])
def test_t3_typed_complete_envelope_refuses(project, bad):
    ws = project.run_dir('r1')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        if bad == 'object':
            protection = object()
        elif bad == 'incomplete':
            protection = replace(protection, path_facts=())
        elif bad == 'run':
            protection = replace(protection, run_dir=project.workspace_root / 'other')
        elif bad == 'project':
            protection = replace(protection, project_root=project.root.parent)
        elif bad == 'coordinator':
            protection = replace(protection, coordinator_writable=(project.root,))
        elif bad == 'writable':
            protection = replace(protection, writable=())
        elif bad == 'mask':
            protection = replace(protection, masks=())
        else:
            protection = replace(protection, sandbox_enabled=False, writable=None)
        with pytest.raises(admission.AdmissionUnsafe):
            held.validate_protection(protection)


def test_t3_prepared_protection_exact_shared_interface(k_conversational_project, monkeypatch):
    project, ws, _ = k_conversational_project
    resolves, prepares = [], []
    real_resolve, real_prepare = agent_run.resolve_turn, agent_run.prepare_turn
    with admission.reserve(project, ws) as held:
        resolved = real_resolve(project, 'k-protocol-fixture', run_dir=ws, session=None)
        resolves.append(resolved)
        prepared = real_prepare(resolved, accept_human_submission('/ordinary slash'))
        prepares.append(prepared)
        assert prepared.protection is resolved.protection
        def forbidden(*args, **kwargs):
            raise AssertionError('validation performed semantic reload')
        monkeypatch.setattr(sandbox, '_capture_turn_policy', forbidden)
        monkeypatch.setattr(agent_run, 'resolve_turn', forbidden)
        monkeypatch.setattr(agent_run, 'prepare_turn', forbidden)
        held.validate_protection(prepared.protection)
        held.validate_protection(prepared.protection)
    assert len(resolves) == len(prepares) == 1


def test_t3_unsupported_direct_refusal_has_no_storage(project, monkeypatch):
    monkeypatch.setattr(admission, 'backend_supported', lambda: False)
    with pytest.raises(admission.AdmissionUnsafe, match='unsupported'):
        with admission.reserve(project, project.run_dir('r1')):
            pass
    assert not (project.root / '.scieflow').exists()


def test_t3_invalid_owning_run_refuses(project, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    with pytest.raises(admission.AdmissionUnsafe):
        with admission.reserve(project, outside):
            pass
    assert not (project.root / '.scieflow').exists()


def _live_inert_job(project, ws):
    import select
    import shlex
    from scieflow.core import jobs
    ready, wait = ws / 'ready-fifo', ws / 'wait-fifo'
    os.mkfifo(ready)
    os.mkfifo(wait)
    fd = os.open(ready, os.O_RDONLY | os.O_NONBLOCK)
    proc = None
    try:
        command = f'printf ready > {shlex.quote(str(ready))}; read value < {shlex.quote(str(wait))}'
        job, proc = jobs.start(project, ['/bin/sh', '-c', command], kind='turn', cwd=ws,
                               run_dir=ws, sandbox_writable=[ws], timeout_s=30)
        assert select.select([fd], [], [], 10)[0], 'inert child never acknowledged running'
        assert os.read(fd, 5) == b'ready' and proc.poll() is None
        return job, proc
    except BaseException:
        if proc is not None:
            jobs.cancel(job)
            jobs.wait(job, proc)
        raise
    finally:
        os.close(fd)


def test_t3_cancel_bypasses_held_admission(project):
    from scieflow.core import jobs, service
    ws = project.run_dir('r1')
    job, proc = _live_inert_job(project, ws)
    try:
        with admission.reserve(project, ws):
            assert service.cancel_job(project, job.id)['state'] == 'cancelled'
            assert jobs.wait(job, proc).state == 'cancelled'
            with pytest.raises(admission.AdmissionBusy):
                with admission.reserve(project, ws):
                    pass
    finally:
        jobs.cancel(job)
        jobs.wait(job, proc)


@pytest.mark.parametrize('operation', ['send', 'handover'])
def test_t3_disposable_owner_loss_does_not_bypass_surviving_job(project, operation):
    from scieflow.core import jobs, service
    from scieflow.core.run import conversation
    ws = project.run_dir('r1')
    conversation.set_agent(ws, 'stub')
    before = (ws / 'conversation.yml').read_bytes()
    # This parent has NEVER opened admission; neither it nor the job inherits
    # the disposable owner's independent lock description.
    owner, pipe = _process(multiprocessing.get_context('fork'), _hold, project, ws)
    job = proc = None
    try:
        assert _receive(pipe) == 'held'
        job, proc = _live_inert_job(project, ws)
        owner.terminate()  # ONLY this test-created lock owner
        owner.join(10)
        assert owner.exitcode == -15 and proc.poll() is None
        with admission.reserve(project, ws):
            pass  # kernel ownership was actually released
        with pytest.raises(service.ServiceError, match='still running'):
            if operation == 'send':
                service.say(project, 'r1', 'no duplicate')
            else:
                service.set_conversation_agent(project, 'r1', 'stub2')
        assert (ws / 'conversation.yml').read_bytes() == before
        assert not list((ws / 'logs').glob('turn-*'))
    finally:
        if owner.is_alive():
            owner.terminate()
        owner.join(10)
        pipe.close()
        if job is not None:
            service.cancel_job(project, job.id)
            jobs.wait(job, proc)


def test_t3_writable_canonical_policy_through_readonly_config_alias_refuses(project):
    ws = project.run_dir('r1')
    config = project.root / 'config'
    moved = ws / 'config-target'
    config.rename(moved)
    config.symlink_to(moved, target_is_directory=True)
    (config / 'sandbox.yml').write_text('writable: []\n')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        assert (project.root / sandbox.ALLOWLIST_FILE).resolve().is_relative_to(ws)
        with pytest.raises(admission.AdmissionUnsafe, match='writable grant'):
            held.validate_protection(protection)


def test_t3_project_alias_replaceable_from_effective_run_grant_refuses(project):
    ws = project.run_dir('r1')
    alias = ws / 'project-alias'
    alias.symlink_to(project.root, target_is_directory=True)
    aliased = Project(alias)
    with admission.reserve(aliased, alias / 'workspace/r1') as held:
        protection = sandbox.resolve_turn_protection(aliased, run_dir=alias / 'workspace/r1')
        with pytest.raises(admission.AdmissionUnsafe, match='writable grant'):
            held.validate_protection(protection)


def _creation_race(pipe, root, run, start):
    try:
        assert start.wait(10)
        with admission.reserve(Project(Path(root)), Path(run)):
            pipe.send('held')
            pipe.recv()
    except admission.AdmissionBusy:
        pipe.send('busy')
    except BaseException as exc:
        pipe.send(type(exc).__name__ + ':' + str(exc))
    finally:
        pipe.close()


def test_t3_concurrent_initial_creation_uses_one_stable_inode(project):
    assert not (project.root / '.scieflow').exists()
    ws = project.run_dir('r1')
    ctx = multiprocessing.get_context('fork')
    start = ctx.Event()
    children = []
    try:
        for _ in range(2):
            pipe, child_pipe = ctx.Pipe()
            child = ctx.Process(target=_creation_race, args=(child_pipe, str(project.root), str(ws), start))
            child.start()
            child_pipe.close()
            children.append((child, pipe))
        start.set()
        statuses = [_receive(pipe) for _, pipe in children]
        assert sorted(statuses) == ['busy', 'held']
        inode = _key(project, ws).stat().st_ino
        for (_, pipe), state in zip(children, statuses):
            if state == 'held':
                pipe.send('release')
        for child, _ in children:
            child.join(10)
            assert child.exitcode == 0
        with admission.reserve(project, ws):
            assert _key(project, ws).stat().st_ino == inode
    finally:
        for child, pipe in children:
            if child.is_alive():
                child.terminate()
            child.join(10)
            pipe.close()


@pytest.mark.parametrize('reason', ['ENOLCK', 'EOPNOTSUPP', 'EACCES'])
def test_t3_supported_backend_failure_is_unsafe_not_unsupported(project, monkeypatch, reason):
    import errno
    before = set(os.listdir('/proc/self/fd'))
    def unavailable(*args):
        raise OSError(getattr(errno, reason), reason)
    monkeypatch.setattr(admission.fcntl, 'flock', unavailable)
    assert admission.backend_supported() is True
    with pytest.raises(admission.AdmissionUnsafe, match='supported admission backend'):
        with admission.reserve(project, project.run_dir('r1')):
            pass
    assert admission.backend_supported() is True
    assert set(os.listdir('/proc/self/fd')) == before


def test_t3_unencodable_identity_refuses_before_storage(project):
    ws = project.workspace_root / 'bad-\udcff'
    ws.mkdir()
    with pytest.raises(admission.AdmissionUnsafe):
        with admission.reserve(project, ws):
            pass
    assert not (project.root / '.scieflow').exists()


def test_t3_current_admission_import_is_feature_source():
    assert Path(admission.__file__).resolve() == Path(__file__).resolve().parents[2] / 'src/scieflow/core/turn_admission.py'


def _t3_alias_policy_project(root, location, *, relative=False, hops=1, present=True):
    """Genuine policy capture with canonical bytes outside every grant."""
    project = Project(root)
    run = root / 'workspace/r1'
    run.mkdir(parents=True)
    safe = root / 'safe-store'
    config = safe / 'deep/config'
    config.mkdir(parents=True)
    original = config / 'sandbox.yml'
    if location == 'run':
        parent = run
    elif location == 'coordinator':
        parent = root / 'workspace/r2'
        parent.mkdir()
    else:
        parent = root / ('shared' if location == 'allowlist' else 'readonly-area')
        parent.mkdir()
    bridge = parent / 'bridge'
    def link(path, target):
        spelling = os.path.relpath(target, path.parent) if relative else target
        path.symlink_to(spelling, target_is_directory=True)
    target = safe
    if hops > 1:
        relay = root / 'readonly-relay'
        link(relay, safe)
        target = relay
    link(bridge, target)
    route = bridge
    if hops > 1:
        route = root / 'readonly-front'
        link(route, bridge)
    link(root / 'config', route / 'deep/config')
    if present:
        original.write_text('writable:\n  - path: shared\n' if location == 'allowlist' else 'writable: []\n')
    assert (root / sandbox.ALLOWLIST_FILE).resolve() == original
    assert not original.is_relative_to(project.workspace_root)
    return project, run, bridge, original


@pytest.mark.parametrize('location', ['run', 'coordinator'])
def test_t3_alias_writable_intermediate_policy_ancestor_refuses(tmp_path, location):
    project, run, bridge, original = _t3_alias_policy_project(tmp_path, location)
    with admission.reserve(project, run) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=run)
        assert any(f.path == bridge and f.link_target for f in protection.path_facts)
        assert bridge.resolve() != original.parent
        assert all(not original.is_relative_to(g) for g in protection.coordinator_writable)
        with pytest.raises(admission.AdmissionUnsafe, match='writable|grant|alias|safety'):
            held.validate_protection(protection)


@pytest.mark.parametrize('location,present,hops', [
    ('run', True, 2), ('coordinator', True, 2),
    ('run', False, 2), ('coordinator', False, 2),
    ('allowlist', True, 1), ('allowlist', True, 2),
])
def test_t3_alias_relative_transitive_and_allowlist_refuses(tmp_path, location, present, hops):
    project, run, bridge, original = _t3_alias_policy_project(
        tmp_path, location, relative=True, hops=hops, present=present)
    with admission.reserve(project, run) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=run)
        assert (protection.policy_sha256 is not None) is present
        assert all(not original.is_relative_to(g) for g in protection.coordinator_writable)
        if location == 'allowlist':
            assert any(bridge.is_relative_to(g) for g in protection.allowlist)
            assert not bridge.is_relative_to(protection.workspace_root)
        with pytest.raises(admission.AdmissionUnsafe, match='writable|grant|alias|safety'):
            held.validate_protection(protection)


@pytest.mark.parametrize('present', [True, False])
def test_t3_alias_readonly_multi_hop_same_object_and_churn(tmp_path, monkeypatch, present):
    project, run, _, original = _t3_alias_policy_project(
        tmp_path, 'readonly', relative=True, hops=2, present=present)
    with admission.reserve(project, run) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=run)
        def forbidden(*args, **kwargs):
            raise AssertionError('admission recaptured semantic protection')
        monkeypatch.setattr(sandbox, '_capture_turn_policy', forbidden)
        monkeypatch.setattr(sandbox, 'resolve_turn_protection', forbidden)
        held.validate_protection(protection)
        for directory in (run, project.workspace_root, original.parent, original.parent.parent.parent):
            (directory / 'ordinary-file').write_text('directory entry churn')
        if present:
            original.read_bytes()
        held.validate_protection(protection)
        if not present:
            original.write_text('writable: []\n')
            with pytest.raises(admission.AdmissionUnsafe, match='identity changed'):
                held.validate_protection(protection)


def test_t3_alias_unrelated_writable_alias_and_safe_grant_stay_compatible(project, monkeypatch):
    run = project.run_dir('r1')
    shared = project.root / 'ordinary-share'
    shared.mkdir()
    # An ordinary grant spelling can itself be writable; it is NOT a safety
    # alias just because its facts share canonical project ancestors.
    alias = run / 'ordinary-grant-alias'
    alias.symlink_to('../../ordinary-share', target_is_directory=True)
    (project.root / sandbox.ALLOWLIST_FILE).write_text('writable:\n  - path: workspace/r1/ordinary-grant-alias\n')
    with admission.reserve(project, run) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=run)
        assert protection.allowlist == (shared,)
        def forbidden(*args, **kwargs):
            raise AssertionError('semantic policy reload')
        monkeypatch.setattr(sandbox, '_capture_turn_policy', forbidden)
        held.validate_protection(protection)
        (shared / 'safe-positive').write_text('ordinary grant')
        held.validate_protection(protection)


def test_t3_alias_missing_intermediate_fact_refuses(tmp_path):
    project, run, bridge, _ = _t3_alias_policy_project(tmp_path, 'readonly', relative=True, hops=2)
    with admission.reserve(project, run) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=run)
        incomplete = replace(protection, path_facts=tuple(f for f in protection.path_facts if f.path != bridge))
        with pytest.raises(admission.AdmissionUnsafe, match='incomplete'):
            held.validate_protection(incomplete)


# M1-M4: only an explicitly owned host mask transition may replace absence.
def test_t3_mask_owned_absence_same_object_churn_and_cleanup(project, monkeypatch):
    from scieflow.core import provenance
    ws = project.run_dir('r1')
    leaf = ws / provenance.REPO_DIR
    before = set(os.listdir('/proc/self/fd'))
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        absence = next(f for f in protection.path_facts if f.path == leaf)
        assert not absence.exists and absence.resolved == leaf and absence.link_target is None
        original_facts = protection.path_facts
        def forbidden(*args, **kwargs):
            raise AssertionError('mask preparation performed a semantic recapture')
        monkeypatch.setattr(sandbox, '_capture_turn_policy', forbidden)
        inside = set(os.listdir('/proc/self/fd'))
        held.prepare_launch_masks(protection)
        assert leaf.is_dir(), 'authorized preparation must materialize its captured-absent mask'
        owned = held._launch_mask
        assert owned.protection is protection and owned.absence is absence
        assert leaf not in held._fds, 'writable mask must not enter immutable storage safety closure'
        assert len(set(os.listdir('/proc/self/fd')) - inside) == 1
        assert not os.get_inheritable(owned.fd)
        assert os.fstat(owned.fd).st_ino == leaf.lstat().st_ino
        assert leaf.stat().st_mode & 0o777 == 0o700
        assert leaf.stat().st_uid == os.geteuid()
        inode = leaf.stat().st_ino
        (leaf / 'ordinary-child').write_text('entry churn, not replacement')
        held.validate_protection(protection)
        held.prepare_launch_masks(protection)
        assert held._launch_mask is owned and leaf.stat().st_ino == inode
        assert protection.path_facts is original_facts and not absence.exists
    assert set(os.listdir('/proc/self/fd')) == before
    with pytest.raises(OSError):
        os.fstat(owned.fd)
    assert leaf.is_dir() and (leaf / 'ordinary-child').read_text() == 'entry churn, not replacement'
    with pytest.raises(admission.AdmissionUnsafe, match='not held'):
        held.prepare_launch_masks(protection)


def test_t3_mask_original_existing_content_and_identity_not_reinitialized(project, monkeypatch):
    from scieflow.core import provenance
    leaf = project.run_dir('r1') / provenance.REPO_DIR
    leaf.mkdir(mode=0o750)
    sentinel = leaf / 'existing-provenance'
    sentinel.write_bytes(b'host provenance stays intact')
    original = leaf.stat()
    with admission.reserve(project, leaf.parent) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=leaf.parent)
        def forbidden(*args, **kwargs):
            raise AssertionError('existing provenance mask was recreated')
        monkeypatch.setattr(os, 'mkdir', forbidden)
        held.prepare_launch_masks(protection)
        held.validate_protection(protection)
        assert held._launch_mask is None
    assert leaf.stat().st_ino == original.st_ino and leaf.stat().st_mode == original.st_mode
    assert sentinel.read_bytes() == b'host provenance stays intact'


@pytest.mark.parametrize('kind', ['directory', 'symlink', 'file'])
def test_t3_mask_unowned_creation_refuses_readonly_and_preparation(project, kind):
    from scieflow.core import provenance
    ws = project.run_dir('r1')
    leaf = ws / provenance.REPO_DIR
    outside = project.root / 'outside-mask'
    outside.mkdir()
    (outside / 'sentinel').write_bytes(b'outside')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        if kind == 'directory':
            leaf.mkdir(mode=0o700)
        elif kind == 'symlink':
            leaf.symlink_to(outside, target_is_directory=True)
        else:
            leaf.write_bytes(b'unowned')
        original = leaf.lstat()
        for check in (held.validate_protection, held.prepare_launch_masks):
            with pytest.raises(admission.AdmissionUnsafe):
                check(protection)
        assert leaf.lstat().st_ino == original.st_ino and leaf.lstat().st_mode == original.st_mode
        assert held._launch_mask is None
    assert (outside / 'sentinel').read_bytes() == b'outside'


@pytest.mark.parametrize('kind', ['directory', 'symlink'])
def test_t3_mask_exclusive_mkdir_race_never_adopts(project, monkeypatch, kind):
    from scieflow.core import provenance
    ws = project.run_dir('r1')
    leaf = ws / provenance.REPO_DIR
    outside = project.root / 'outside-mask'
    outside.mkdir()
    real_mkdir = os.mkdir
    real_publish = admission._publish_mask_noreplace
    races = []
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        inside = set(os.listdir('/proc/self/fd'))
        def race(src_fd, src_name, dst_fd, dst_name):
            if dst_name == leaf.name and dst_fd == held._fds[ws]:
                if kind == 'directory':
                    real_mkdir(dst_name, 0o700, dir_fd=dst_fd)
                else:
                    os.symlink(str(outside), dst_name, dir_fd=dst_fd)
                races.append(leaf.lstat().st_ino)
            return real_publish(src_fd, src_name, dst_fd, dst_name)
        monkeypatch.setattr(admission, '_publish_mask_noreplace', race)
        with pytest.raises(admission.AdmissionUnsafe):
            held.prepare_launch_masks(protection)
        assert races == [leaf.lstat().st_ino], 'exclusive creation race must actually be reached'
        assert held._launch_mask is None
        assert set(os.listdir('/proc/self/fd')) == inside
        with pytest.raises(admission.AdmissionUnsafe):
            held.validate_protection(protection)
    assert not list(outside.iterdir())


@pytest.mark.parametrize('change', ['replace', 'remove', 'symlink', 'file', 'mode'])
def test_t3_mask_owned_persistent_changes_refuse_without_repair(project, change):
    from scieflow.core import provenance
    ws = project.run_dir('r1')
    leaf = ws / provenance.REPO_DIR
    outside = project.root / 'outside-mask'
    outside.mkdir()
    (outside / 'sentinel').write_bytes(b'outside')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        held.prepare_launch_masks(protection)
        fd = held._launch_mask.fd
        if change == 'mode':
            leaf.chmod(0o755)
        else:
            leaf.rename(ws / 'original-mask-held-inode')
            if change == 'replace':
                leaf.mkdir(mode=0o700)
            elif change == 'symlink':
                leaf.symlink_to(outside, target_is_directory=True)
            elif change == 'file':
                leaf.write_bytes(b'not a directory')
        for check in (held.validate_protection, held.prepare_launch_masks):
            with pytest.raises(admission.AdmissionUnsafe):
                check(protection)
        assert leaf.exists() is (change != 'remove')
        assert os.fstat(fd).st_ino == held._launch_mask.identity[1]
    with pytest.raises(OSError):
        os.fstat(fd)
    assert (outside / 'sentinel').read_bytes() == b'outside'


def test_t3_mask_transition_does_not_transfer_to_equal_protection(project):
    ws = project.run_dir('r1')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        clone = replace(protection)
        assert clone == protection and clone is not protection
        held.prepare_launch_masks(protection)
        for check in (held.validate_protection, held.prepare_launch_masks):
            with pytest.raises(admission.AdmissionUnsafe, match='object'):
                check(clone)
        held.validate_protection(protection)


@pytest.mark.parametrize('change', ['create', 'content', 'replace', 'remove'])
def test_t3_mask_owned_transition_keeps_policy_changes_strict(project, change):
    ws = project.run_dir('r1')
    policy = project.root / sandbox.ALLOWLIST_FILE
    if change != 'create':
        policy.write_text('writable: []\n')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        held.prepare_launch_masks(protection)
        if change == 'create':
            policy.write_text('writable: []\n')
        elif change == 'content':
            policy.write_text('writable: []\n# changed policy\n')
        elif change == 'replace':
            other = policy.with_suffix('.new')
            other.write_bytes(policy.read_bytes())
            other.replace(policy)
        else:
            policy.unlink()
        for check in (held.validate_protection, held.prepare_launch_masks):
            with pytest.raises(admission.AdmissionUnsafe):
                check(protection)


@pytest.mark.parametrize('change', ['request', 'grant-alias', 'parent', 'lock'])
def test_t3_mask_owned_transition_keeps_alias_and_ancestor_checks(project, change):
    ws = project.run_dir('r1')
    other = project.run_dir('r2')
    other.mkdir()
    request = project.workspace_root / 'view'
    request.symlink_to('r1', target_is_directory=True)
    grant = project.root / 'extra'
    grant.mkdir()
    alias = project.root / 'grant-alias'
    alias.symlink_to(grant, target_is_directory=True)
    (project.root / sandbox.ALLOWLIST_FILE).write_text('writable:\n  - path: grant-alias\n')
    with admission.reserve(project, request) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        held.prepare_launch_masks(protection)
        backup = None
        if change == 'request':
            request.unlink()
            request.symlink_to('r2', target_is_directory=True)
        elif change == 'grant-alias':
            alias.unlink()
            alias.symlink_to(other, target_is_directory=True)
        else:
            target = _key(project, ws) if change == 'lock' else project.root / '.scieflow'
            backup = target.with_name(target.name + '-mask-backup')
            target.rename(backup)
            if change == 'lock':
                target.touch(mode=0o600)
            else:
                target.mkdir()
        try:
            for check in (held.validate_protection, held.prepare_launch_masks):
                with pytest.raises(admission.AdmissionUnsafe):
                    check(protection)
        finally:
            if backup is not None:
                if change == 'lock':
                    target.unlink()
                else:
                    target.rmdir()
                backup.rename(target)
    assert not list(other.iterdir())


@pytest.mark.parametrize('grant', ['.scieflow', '.scieflow/turn-admission', 'state-alias'])
def test_t3_mask_preparation_unsafe_grants_refuse_before_creation(project, grant):
    from scieflow.core import provenance
    ws = project.run_dir('r1')
    with admission.reserve(project, ws) as held:
        if grant == 'state-alias':
            (project.root / grant).symlink_to(project.root / '.scieflow', target_is_directory=True)
        (project.root / sandbox.ALLOWLIST_FILE).write_text(f'writable:\n  - path: {grant}\n')
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        with pytest.raises(admission.AdmissionUnsafe, match='grant|writable'):
            held.prepare_launch_masks(protection)
        assert not (ws / provenance.REPO_DIR).exists()
        assert held._launch_mask is None


@pytest.mark.parametrize('fault', ['noninheritance', 'fstat', 'replacement', 'request', 'lock'])
def test_t3_mask_injected_failure_closes_new_fd_and_never_unlinks(project, monkeypatch, fault):
    from scieflow.core import provenance
    ws = project.run_dir('r1')
    leaf = ws / provenance.REPO_DIR
    other = project.run_dir('r2')
    other.mkdir()
    request = project.workspace_root / 'view'
    request.symlink_to('r1', target_is_directory=True)
    real_open, real_set, real_fstat = os.open, os.set_inheritable, os.fstat
    opened = []
    with admission.reserve(project, request) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        before = set(os.listdir('/proc/self/fd'))
        def opening(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == leaf.name and dir_fd == held._fds[ws]:
                opened.append(fd)
                if fault == 'replacement':
                    leaf.rename(ws / 'created-mask-backup')
                    leaf.mkdir(mode=0o700)
                elif fault == 'request':
                    request.unlink()
                    request.symlink_to('r2', target_is_directory=True)
                elif fault == 'lock':
                    lock = _key(project, ws)
                    lock.rename(lock.with_suffix('.held'))
                    lock.touch(mode=0o600)
            return fd
        def setting(fd, inheritable):
            if fd in opened and fault == 'noninheritance':
                raise OSError('injected mask noninheritance failure')
            return real_set(fd, inheritable)
        def stating(fd):
            if fd in opened and fault == 'fstat':
                raise OSError('injected mask fstat failure')
            return real_fstat(fd)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'open', opening)
            patch.setattr(os, 'set_inheritable', setting)
            patch.setattr(os, 'fstat', stating)
            with pytest.raises(admission.AdmissionUnsafe):
                held.prepare_launch_masks(protection)
        assert len(opened) == 1 and held._launch_mask is None
        with pytest.raises(OSError):
            real_fstat(opened[0])
        assert set(os.listdir('/proc/self/fd')) == before
        assert leaf.is_dir(), 'failure cleanup must never unlink a potentially replaced leaf'
        assert not list(other.iterdir())


# P1-P4 additions keep the entire old prefix (apart from the named hook).
import errno


@pytest.mark.parametrize('conflict', ['absent', 'empty-directory', 'directory', 'symlink', 'file'])
def test_t3_publication_real_noreplace_relative_fds(tmp_path, monkeypatch, conflict):
    import ctypes
    source, target, outside = (tmp_path / name for name in ('source', 'target', 'outside'))
    for parent in (source, target, outside):
        parent.mkdir()
    (outside / 'sentinel').write_bytes(b'outside')
    (source / 'stage').mkdir(mode=0o700)
    original = (source / 'stage').lstat().st_ino
    leaf = target / 'leaf'
    if conflict in ('empty-directory', 'directory'):
        leaf.mkdir()
        if conflict == 'directory':
            (leaf / 'sentinel').write_bytes(b'existing')
    elif conflict == 'symlink':
        leaf.symlink_to(outside, target_is_directory=True)
    elif conflict == 'file':
        leaf.write_bytes(b'existing')
    old = leaf.lstat() if conflict != 'absent' else None
    real_cdll, calls = ctypes.CDLL, []

    class Function:
        def __init__(self, real):
            self.real = real

        def __call__(self, *args):
            assert self.argtypes == (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            assert self.restype is ctypes.c_int
            self.real.argtypes, self.real.restype = self.argtypes, self.restype
            calls.append(args)
            return self.real(*args)

    class Library:
        def __init__(self, name, *, use_errno):
            assert name is None and use_errno is True
            self.renameat2 = Function(real_cdll(name, use_errno=use_errno).renameat2)

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    src_fd, dst_fd = os.open(source, flags), os.open(target, flags)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(ctypes, 'CDLL', Library)
            if conflict == 'absent':
                assert admission._publish_mask_noreplace(src_fd, 'stage', dst_fd, 'leaf') is None
                assert leaf.lstat().st_ino == original and not (source / 'stage').exists()
            else:
                with pytest.raises(OSError) as error:
                    admission._publish_mask_noreplace(src_fd, 'stage', dst_fd, 'leaf')
                assert error.value.errno == errno.EEXIST
                assert leaf.lstat().st_ino == old.st_ino and leaf.lstat().st_mode == old.st_mode
                assert (source / 'stage').lstat().st_ino == original
        assert calls == [(src_fd, b'stage', dst_fd, b'leaf', 1)]
    finally:
        os.close(src_fd)
        os.close(dst_fd)
    assert (outside / 'sentinel').read_bytes() == b'outside'
    if conflict == 'file':
        assert leaf.read_bytes() == b'existing'
    elif conflict == 'directory':
        assert (leaf / 'sentinel').read_bytes() == b'existing'
    elif conflict == 'empty-directory':
        assert not list(leaf.iterdir())


@pytest.mark.parametrize('name', ['', '.', '..', 'a/b', '/leaf', 'a\x00b'])
def test_t3_publication_rejects_nonbasenames_before_libc(monkeypatch, name):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid basename reached libc')
    monkeypatch.setattr(admission.ctypes, 'CDLL', forbidden)
    for src, dst in ((name, 'leaf'), ('stage', name)):
        with pytest.raises(ValueError, match='basenames'):
            admission._publish_mask_noreplace(1, src, 2, dst)


@pytest.mark.parametrize('failure', ['missing-symbol', errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.EXDEV, errno.EACCES])
def test_t3_publication_libc_errors_refuse_and_clean_proven_stage(project, monkeypatch, failure):
    import ctypes
    ws = project.run_dir('r1')
    leaf = ws / 'provenance.git'
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        neutral = held._lock_path.parent
        before = set(os.listdir('/proc/self/fd'))
        calls = []

        class Function:
            def __call__(self, *args):
                calls.append(args)
                ctypes.set_errno(failure)
                return -1

        class Library:
            def __init__(self, *args, **kwargs):
                if failure != 'missing-symbol':
                    self.renameat2 = Function()

        monkeypatch.setattr(ctypes, 'CDLL', Library)
        with pytest.raises(admission.AdmissionUnsafe) as error:
            held.prepare_launch_masks(protection)
        assert isinstance(error.value.__cause__, OSError)
        assert error.value.__cause__.errno == (errno.ENOSYS if failure == 'missing-symbol' else failure)
        assert len(calls) == (0 if failure == 'missing-symbol' else 1)
        assert not leaf.exists() and not list(neutral.glob('mask-v1-*.stage'))
        assert held._launch_mask is None and set(os.listdir('/proc/self/fd')) == before
        held.validate_protection(protection)


@pytest.mark.parametrize('fault', ['collision', 'open', 'noninheritance', 'fstat', 'source-replacement',
                                 'parent-drift', 'cleanup-failure', 'nonempty-source', 'ambiguous-published'])
def test_t3_publication_stage_faults_preserve_uncertain_data_and_close_fds(project, monkeypatch, fault):
    ws = project.run_dir('r1')
    leaf = ws / 'provenance.git'
    token = 'a' * 32
    before_all = set(os.listdir('/proc/self/fd'))
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        neutral = held._lock_path.parent
        stage = neutral / ('mask-v1-' + token + '.stage')
        backup = stage.with_suffix('.backup')
        parent_backup = neutral.with_name('turn-admission-held')
        neutral_fd = held._fds[neutral]
        before = set(os.listdir('/proc/self/fd'))
        real_open, real_set, real_fstat, real_stat = os.open, os.set_inheritable, os.fstat, os.stat
        real_publish, real_rmdir = admission._publish_mask_noreplace, os.rmdir
        opened, reached = [], []
        if fault == 'collision':
            stage.mkdir(mode=0o700)
            (stage / 'unrelated').write_bytes(b'keep collision')
            original = stage.lstat().st_ino

        def opening(name, flags, mode=0o777, *, dir_fd=None):
            if dir_fd == neutral_fd and name == stage.name:
                reached.append('open')
                if fault == 'open':
                    raise OSError(errno.EACCES, 'injected stage open')
            fd = real_open(name, flags, mode, dir_fd=dir_fd)
            if dir_fd == neutral_fd and name == stage.name:
                opened.append(fd)
            return fd

        def setting(fd, value):
            if fd in opened and fault == 'noninheritance':
                reached.append('set')
                raise OSError(errno.EPERM, 'injected stage noninheritance')
            return real_set(fd, value)

        def fstat(fd):
            if fd in opened and fault == 'fstat':
                reached.append('fstat')
                raise OSError(errno.EIO, 'injected stage fstat')
            return real_fstat(fd)

        def stating(name, *args, **kwargs):
            if (kwargs.get('dir_fd') == neutral_fd and name == stage.name and
                    fault == 'source-replacement' and 'replacement' not in reached):
                reached.append('replacement')
                stage.rename(backup)
                stage.mkdir(mode=0o700)
                (stage / 'stranger').write_bytes(b'keep replacement')
            return real_stat(name, *args, **kwargs)

        def publish(src_fd, src_name, dst_fd, dst_name):
            reached.append('publish')
            if fault == 'parent-drift':
                neutral.rename(parent_backup)
                neutral.mkdir(mode=0o700)
            elif fault == 'nonempty-source':
                (stage / 'unrelated').write_bytes(b'keep nonempty')
            elif fault == 'ambiguous-published':
                real_publish(src_fd, src_name, dst_fd, dst_name)
            raise OSError(errno.EIO, 'injected publication uncertainty')

        def removal(name, *, dir_fd=None):
            if name == stage.name and dir_fd == neutral_fd:
                reached.append('cleanup')
                if fault == 'cleanup-failure':
                    raise OSError(errno.EACCES, 'injected cleanup failure')
            return real_rmdir(name, dir_fd=dir_fd)

        with monkeypatch.context() as patch:
            patch.setattr(admission.secrets, 'token_hex', lambda size: token if size == 16 else pytest.fail('wrong random size'))
            patch.setattr(os, 'open', opening)
            patch.setattr(os, 'set_inheritable', setting)
            patch.setattr(os, 'fstat', fstat)
            patch.setattr(os, 'stat', stating)
            patch.setattr(os, 'rmdir', removal)
            patch.setattr(admission, '_publish_mask_noreplace', publish)
            with pytest.raises(admission.AdmissionUnsafe):
                held.prepare_launch_masks(protection)
        assert held._launch_mask is None and set(os.listdir('/proc/self/fd')) == before
        for fd in opened:
            with pytest.raises(OSError):
                real_fstat(fd)
        if fault == 'collision':
            assert not reached and stage.lstat().st_ino == original
            assert (stage / 'unrelated').read_bytes() == b'keep collision'
        elif fault == 'source-replacement':
            assert 'replacement' in reached and 'publish' not in reached and 'cleanup' not in reached
            assert backup.is_dir() and (stage / 'stranger').read_bytes() == b'keep replacement'
        elif fault == 'parent-drift':
            assert 'publish' in reached and 'cleanup' not in reached
            assert (parent_backup / stage.name).is_dir() and not stage.exists()
            neutral.rmdir()
            parent_backup.rename(neutral)  # Fixture restoration, not production repair.
        elif fault == 'ambiguous-published':
            assert leaf.is_dir() and not stage.exists() and 'cleanup' not in reached
        else:
            assert stage.is_dir(), 'uncertain or unremovable source must not be erased'
            if fault == 'nonempty-source':
                assert (stage / 'unrelated').read_bytes() == b'keep nonempty' and 'cleanup' in reached
            if fault == 'cleanup-failure':
                assert 'cleanup' in reached
        assert leaf.exists() is (fault == 'ambiguous-published')
    assert set(os.listdir('/proc/self/fd')) == before_all


def test_t3_publication_different_device_refuses_before_stage(project, monkeypatch):
    from types import SimpleNamespace
    ws = project.run_dir('r1')
    with admission.reserve(project, ws) as held:
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        original = held._validate_held
        real_fstat = os.fstat
        calls = []
        # The first full validation invokes held checking twice. Install this
        # seam only after that full check, in the subsequent operation check.
        real_validate = held.validate_protection
        def checked(value):
            real_validate(value)
            def operation_validate():
                original()
                def stating(fd):
                    value = real_fstat(fd)
                    return SimpleNamespace(st_dev=value.st_dev + 1) if fd == held._fds[ws] else value
                monkeypatch.setattr(os, 'fstat', stating)
                calls.append(True)
            monkeypatch.setattr(held, '_validate_held', operation_validate)
        monkeypatch.setattr(held, 'validate_protection', checked)
        with pytest.raises(admission.AdmissionUnsafe, match='same mounted filesystem'):
            held.prepare_launch_masks(protection)
        assert calls == [True] and not (ws / 'provenance.git').exists()
        assert not list(held._lock_path.parent.glob('mask-v1-*.stage'))


@pytest.mark.parametrize('grant', ['stage', 'descendant'])
def test_t3_publication_candidate_grants_refuse_before_mkdir(project, monkeypatch, grant):
    token = 'b' * 32
    ws = project.run_dir('r1')
    with admission.reserve(project, ws) as held:
        relative = '.scieflow/turn-admission/mask-v1-' + token + '.stage'
        if grant == 'descendant':
            relative += '/child'
        (project.root / sandbox.ALLOWLIST_FILE).write_text(f'writable:\n  - path: {relative}\n')
        protection = sandbox.resolve_turn_protection(project, run_dir=ws)
        monkeypatch.setattr(admission.secrets, 'token_hex', lambda size: token)
        with pytest.raises(admission.AdmissionUnsafe, match='grant|protection'):
            held.prepare_launch_masks(protection)
        assert not (ws / 'provenance.git').exists()
        assert not list(held._lock_path.parent.glob('mask-v1-*.stage'))
