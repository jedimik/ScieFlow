"""F-2 t3: the code-owned menu snapshot and the no-shell menu skill (plan 5.6, t3 RED -> GREEN 1-5).

Hermetic: no read of the host home, no run, no subprocess. The repo's own skill files are read as
bytes only; the click tree is inspected without executing any command.
"""
import dataclasses
import json
import re
import shutil
from pathlib import Path

import click
import pytest

from scieflow.core import agent_config as ac
from scieflow.core import harness_commands as hc
from scieflow.core import harness_identity as hi
from scieflow.core import menu
from scieflow.core import service
from scieflow.core.project import Project

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / 'skills' / 'scieflow-menu'
SNAPSHOT = SKILL_DIR / 'menu.json'
SKILL = SKILL_DIR / 'SKILL.md'
FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'harness_identity_r14.json').read_text())
REGEN = 'uv run python -c "from scieflow.core import menu; menu.write_static_snapshot()"'
STATIC_KEYS = ['sections', 'workflows', 'agent_settings', 'resume_prompt_template', 'omitted']
MENU_KEYS = ['sections', 'workflows', 'agent_settings', 'runs', 'resume_prompt_template']
AGENT_SETTINGS_KEYS = ['scopes', 'roles', 'agents', 'effective_defaults', 'apply_with']
STATIC_AGENT_SETTINGS_KEYS = ['scopes', 'roles', 'apply_with']
OMITTED = ['agent_settings.agents', 'agent_settings.effective_defaults', 'runs']


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=True) + '\n'


def _check_snapshot(path: Path) -> None:
    want = _dump(menu.static_menu_json())
    got = path.read_text(encoding='utf-8')
    if got != want:
        raise AssertionError(f'{path} differs from menu.static_menu_json(); regenerate it with: {REGEN}')


# ---- static part: keys, order, no config or run read (t3 RED 1) ----------------------------

def test_static_menu_json_has_the_specified_keys_and_order():
    static = menu.static_menu_json()
    assert list(static.keys()) == STATIC_KEYS
    assert list(static['agent_settings'].keys()) == STATIC_AGENT_SETTINGS_KEYS
    assert static['omitted'] == OMITTED


def test_static_menu_json_reads_no_config_no_run_and_no_path(monkeypatch):
    expected = menu.static_menu_json()

    def refuse(*args, **kwargs):
        raise AssertionError('static_menu_json must not read config, runs or paths')

    monkeypatch.setattr(menu, '_root', refuse)
    monkeypatch.setattr(ac, 'resolve', refuse)
    monkeypatch.setattr(service, 'list_runs', refuse)
    assert menu.static_menu_json() == expected


def test_static_menu_json_is_unchanged_by_a_changed_repo_config(tmp_path, monkeypatch):
    # F-2 t3 review r1 finding 2: plan 6 t3 RED 1 asks for a changed config/agents.yml in a temp repo.
    from scieflow.core import config as config_mod

    expected = menu.static_menu_json()
    repo = tmp_path / 'repo'
    (repo / 'config').mkdir(parents=True)
    (repo / 'config' / 'agents.yml').write_text(
        'agents:\n  claude:\n    menu:\n      models: [changed-model-9]\n', encoding='utf-8')
    (repo / 'config' / 'defaults.yml').write_text('defaults: {}\n', encoding='utf-8')
    (repo / 'workspace' / 'demo').mkdir(parents=True)
    (repo / 'workspace' / 'demo' / 'config.yml').write_text('agent_overrides: {}\n', encoding='utf-8')
    (repo / 'workspace' / 'demo' / 'status.yml').write_text('phase: changed\n', encoding='utf-8')
    monkeypatch.chdir(repo)
    monkeypatch.setattr(menu, '_root', lambda: repo)
    assert config_mod.repo_root() == repo
    assert menu.static_menu_json() == expected


# ---- dynamic composition and byte identity (t3 RED 2) --------------------------------------

def test_menu_json_is_the_static_part_plus_the_dynamic_part(monkeypatch):
    monkeypatch.setattr(menu, '_root', lambda: REPO)
    monkeypatch.setattr(Project, 'discover', lambda *a, **k: None)
    monkeypatch.setattr(service, 'list_runs', lambda project: [{'slug': 'demo'}])
    full = menu.menu_json()
    static = menu.static_menu_json()
    assert list(full.keys()) == MENU_KEYS
    assert list(full['agent_settings'].keys()) == AGENT_SETTINGS_KEYS
    for key in ('sections', 'workflows', 'resume_prompt_template'):
        assert full[key] == static[key]
    for key in STATIC_AGENT_SETTINGS_KEYS:
        assert full['agent_settings'][key] == static['agent_settings'][key]
    assert full['runs'] == [{'slug': 'demo'}]


# ---- checked-in snapshot (t3 RED 3) ---------------------------------------------------------

def test_checked_in_snapshot_equals_the_static_menu():
    _check_snapshot(SNAPSHOT)


def test_one_changed_byte_fails_with_the_regeneration_command(tmp_path):
    copy = tmp_path / 'menu.json'
    data = SNAPSHOT.read_bytes()
    copy.write_bytes(data.replace(b'"', b"'", 1))
    with pytest.raises(AssertionError, match=re.escape(REGEN)):
        _check_snapshot(copy)


def test_write_static_snapshot_writes_exactly_the_static_text(tmp_path):
    out = menu.write_static_snapshot(tmp_path)
    assert out == tmp_path / 'skills' / 'scieflow-menu' / 'menu.json'
    assert out.read_text(encoding='utf-8') == _dump(menu.static_menu_json())


# ---- the no-shell skill (t3 RED 4) -----------------------------------------------------------

def _skill_text() -> str:
    return SKILL.read_text(encoding='utf-8')


def test_skill_no_shell_path_names_the_snapshot_and_its_sources():
    text = _skill_text()
    assert 'uv run scieflow menu --json' in text
    assert 'skills/scieflow-menu/menu.json' in text
    for source in ('config/agents.yml', 'config/defaults.yml', 'workspace/<slug>/config.yml',
                   'workspace/*/status.yml'):
        assert source in text
    assert re.search(r'never reconstruct the menu from `src/`', text, re.IGNORECASE)


def test_skill_effort_sentence_states_the_conversation_rule():
    text = _skill_text()
    assert 'reasoning: extended-thinking' in text
    assert 'MAX_THINKING_TOKENS=32000' in text
    assert 'session_cmd' in text and 'resume_cmd' in text


def test_skill_effort_sentence_is_written_once_and_well_formed():
    # F-2 t3 review r1 finding 1: the effort clause must not be duplicated or broken across lines.
    text = _skill_text()
    assert text.count("Claude's effort is") == 1
    assert '`default` or\nClaude' not in text


def test_every_scieflow_command_the_skill_names_exists_in_the_click_tree():
    from scieflow.cli import main

    words_seen = [tuple(m.split()) for m in re.findall(r'scieflow((?: [a-z][a-z-]*)+)', _skill_text())]
    assert {('menu',), ('agent', 'configure'), ('experiment', 'sweep'), ('news', 'run'),
            ('workspace', 'list'), ('chats', 'scan')} <= set(words_seen)
    ctx = click.Context(main)
    missing = []
    for words in words_seen:
        cmd = main
        for word in words:
            if not isinstance(cmd, click.Group):
                missing.append(words)
                break
            nxt = cmd.get_command(ctx, word)
            if nxt is None:
                missing.append(words)
                break
            cmd = nxt
    assert not missing, f'skill names commands missing from the CLI: {sorted(set(missing))}'


# ---- identity: a changed menu skill cannot match the certified entry (t3 RED 5) -------------

def _extensions_project(root: Path) -> Path:
    """A synthetic project whose .claude/skills links and skill folders mirror the repo's layout."""
    for name in ('scieflow-menu', 'workspace-sync'):
        shutil.copytree(REPO / 'skills' / name, root / 'skills' / name)
        (root / '.claude' / 'skills').mkdir(parents=True, exist_ok=True)
        link = root / '.claude' / 'skills' / name
        if not link.exists():
            link.symlink_to(f'../../skills/{name}')
    return root


def _derived_from_fixture(extensions):
    inputs = FIXTURE['inputs']
    records = FIXTURE['recipe_records']
    version = hi.derive_version(inputs['executable'], inputs['executable_sha256'],
                                versions_dir=Path(FIXTURE['versions_dir']))
    return hi.derive(agent=inputs['agent'], family=inputs['family'], executable=inputs['executable'],
                     executable_sha256=inputs['executable_sha256'], version=version,
                     session_template=inputs['session_template'], resume_template=inputs['resume_template'],
                     model=inputs['model'], reasoning=inputs['reasoning'], cwd=inputs['cwd'],
                     discovery=records['discovery'], extensions=extensions,
                     settings=records['settings'], sandbox_enabled=inputs['sandbox_enabled'],
                     policy_sha256=inputs['policy_sha256'])


def test_changed_menu_skill_breaks_only_the_extensions_digest(tmp_path):
    root = _extensions_project(tmp_path / 'project')
    extensions = hi.read_extensions(root)
    assert extensions is not None
    digest = hi.canonical_digest({'schema': hi.SCHEMA_EXTENSIONS, **extensions})
    assert digest != FIXTURE['digests']['extensions_sha256']

    derived = _derived_from_fixture(extensions)
    entry = hc.HarnessIdentity(**FIXTURE['identity_t4'])
    differing = {f.name for f in dataclasses.fields(entry)
                 if getattr(derived, f.name) != getattr(entry, f.name)}
    assert differing == {'extensions_sha256'}
    assert derived.extensions_sha256 == digest
    assert hc.support_for(derived).enabled is False
