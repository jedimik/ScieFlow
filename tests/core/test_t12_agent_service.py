"""T-12 t3: the service layer for scoped agent settings (plan 5.4, T12-D6..D11, D14).

`agent_config_view` is the read model for the browser, `plan_agent_changes` /
`apply_agent_changes` the only writers. Rules are enforced here, not in a
template: the allowlist per scope, the value rules, the scope and file rules,
the structured preview and the revision. Every refusal leaves every file
byte-identical and with its mtime unchanged.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_configure as acf
from scieflow.core import store
from scieflow.core.project import Project

NEWS = ("agent: claude\nlookback_days: 30\ninterests:\n"
        "  - name: Snakemake\n    keywords: [workflow]\n")
COMMAND_TEXTS = [
    "claude -p --model {model} {prompt}",
    "codex exec --model {model} -c effort={reasoning} {prompt}",
    "agy --print {prompt} --model {model} --effort {reasoning}",
    "off {prompt}",
]
LOCK = Path("config") / ".agent-configure.lock"


def svc():
    from scieflow.core import service
    return service


def tree(root: Path) -> dict:
    files = sorted(list((root / "config").glob("*.yml")) + list((root / "workspace").rglob("*.yml")))
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in files}


def load(root: Path, rel: str):
    return yaml.safe_load((root / rel).read_text())


@pytest.fixture
def news(repo):
    (repo / "config" / "news.yml").write_text(NEWS)
    return repo


@pytest.fixture
def status_ws(repo):
    (repo / "workspace" / "run-1" / "status.yml").write_text("slug: run-1\n")
    return repo


def plan(repo, scope, changes, slug=None):
    return svc().plan_agent_changes(Project(repo), scope, changes, slug=slug)


def apply(repo, scope, changes, revision, slug=None):
    return svc().apply_agent_changes(Project(repo), scope, changes, revision, slug=slug)


def refused(scope, changes, repo, slug=None, *, match):
    before = tree(repo)
    with pytest.raises(svc().ConfigChangeRefused, match=match):
        plan(repo, scope, changes, slug)
    assert tree(repo) == before


# -- the read model ---------------------------------------------------------------

def test_project_view_reports_stored_inherited_and_effort_per_agent(repo):
    view = svc().agent_config_view(Project(repo), "project")
    claude = view["agents"]["claude"]
    assert claude["model"] == {"stored": "fable", "inherited": None, "effective": "fable",
                               "source": "default", "editable": True, "why_not": ""}
    assert claude["reasoning"]["stored"] is None and claude["reasoning"]["source"] == "not set"
    assert claude["timeout_min"]["effective"] == 30
    assert claude["effort"] == {"mode": "thinking", "levels": ["default", "extended-thinking"],
                                "current": None, "custom_ok": False, "how": claude["effort"]["how"]}
    assert claude["models"] == ["fable", "opus"]
    assert claude["cmd"] == {"configured": True, "cli_only": True}
    assert claude["enabled"] is True
    codex = view["agents"]["codex"]
    assert codex["effort"]["mode"] == "placeholder"
    assert codex["effort"]["levels"] == ["minimal", "low", "medium", "high"]
    assert codex["effort"]["custom_ok"] is True and codex["effort"]["current"] == "medium"
    off = view["agents"]["off"]
    assert off["effort"]["mode"] == "none" and off["effort"]["levels"] == []
    assert off["reasoning"]["editable"] is False and "no effort setting" in off["reasoning"]["why_not"]
    assert view["scope"] == "project" and view["slug"] is None
    assert view["project_root"] == str(repo)


def test_workspace_view_separates_the_run_override_from_the_inherited_value(repo, write_ws):
    write_ws(repo, {"agent_overrides": {"claude": {"timeout_min": 12}}})
    view = svc().agent_config_view(Project(repo), "workspace", "run-1")
    timeout = view["agents"]["claude"]["timeout_min"]
    assert timeout == {"stored": 12, "inherited": 30, "effective": 12, "source": "workspace",
                       "editable": True, "why_not": ""}
    model = view["agents"]["claude"]["model"]
    assert model["stored"] is None and model["inherited"] == "fable"
    assert model["effective"] == "fable" and model["source"] == "default"


def test_roles_show_each_source_label_and_read_only_exceptions(repo, write_ws):
    defaults = yaml.safe_load((repo / "config" / "defaults.yml").read_text())
    defaults["support_as_primary"] = ["research.search"]
    defaults["assignments"]["research.outline"] = {"agent": "codex", "model": "sol", "reasoning": "high"}
    (repo / "config" / "defaults.yml").write_text(yaml.safe_dump(defaults, sort_keys=False))
    write_ws(repo, {"reviewer": "codex", "assignments": {"research.consistency": "claude"}})
    view = svc().agent_config_view(Project(repo), "workspace", "run-1")
    roles = view["roles"]
    assert roles["research.outline"]["overrides"] == {
        "codex": {"model": "sol", "reasoning": "high", "source": "default"}}
    assert roles["research.outline"]["value"] == "codex"
    assert roles["research.consistency"]["source"] == "workspace"
    assert roles["research.reviewer"]["source"] == "workspace (legacy key)"
    assert roles["research.reviewer"]["value"] == "codex"
    assert roles["research.draft-reviewers"]["source"] == "default (= research.draft-authors)"
    assert roles["research.search"]["exception"] == "default"
    assert view["exceptions"] == {"research.search": "default"}
    assert roles["research.search"]["support_ok"] is True
    assert roles["research.outline"]["help"]


def test_view_never_contains_command_template_text(repo, news, write_ws):
    write_ws(repo, {"agent_overrides": {"claude": {"timeout_min": 12}}})
    (repo / "workspace" / "run-1" / "status.yml").write_text("slug: run-1\n")
    views = [
        svc().agent_config_view(Project(repo), "project"),
        svc().agent_config_view(Project(repo), "workspace", "run-1"),
        svc().agent_config_view(Project(repo), "news"),
    ]
    for view in views:
        text = json.dumps(view)
        for template in COMMAND_TEXTS:
            assert template not in text
        assert "{prompt}" not in text


def test_news_view_without_and_with_its_config(repo, news):
    (repo / "config" / "news.yml").unlink()
    absent = svc().agent_config_view(Project(repo), "news")
    assert absent["available"] is False and "scieflow news init" in absent["message"]
    (repo / "config" / "news.yml").write_text(NEWS.replace("agent: claude", "agent: agy"))
    present = svc().agent_config_view(Project(repo), "news")
    assert present["available"] is True
    assert present["settings"]["agent"] == "agy"
    assert present["levels"] == ["low", "medium", "high"]
    assert any("support-tier" in note for note in present["warnings"])
    assert "unset" in present["defaults_meaning"]


def test_view_refuses_a_symlinked_config_and_names_no_content(repo):
    target = repo / "config" / "elsewhere.yml"
    target.write_text((repo / "config" / "agents.yml").read_text())
    (repo / "config" / "agents.yml").unlink()
    (repo / "config" / "agents.yml").symlink_to(target)
    with pytest.raises(svc().ConfigChangeRefused, match="symbolic link"):
        svc().agent_config_view(Project(repo), "project")


def test_malformed_yaml_is_reported_by_line_without_its_content(repo):
    (repo / "config" / "defaults.yml").write_text("assignments: [SECRETTOKEN, b\n")
    with pytest.raises(svc().ConfigChangeRefused) as info:
        svc().agent_config_view(Project(repo), "project")
    assert "`config/defaults.yml`: not valid YAML (line" in str(info.value)
    assert "SECRETTOKEN" not in str(info.value)


def test_oversized_config_is_refused(repo):
    (repo / "config" / "defaults.yml").write_text("# " + "x" * (1024 * 1024) + "\n")
    with pytest.raises(svc().ConfigChangeRefused, match="larger than 1 MiB"):
        svc().agent_config_view(Project(repo), "project")


def test_a_workspace_alias_is_refused_for_view_and_write(repo, write_ws):
    (repo / "workspace" / "alias").symlink_to(repo / "workspace" / "run-1")
    with pytest.raises(svc().ConfigChangeRefused, match="alias"):
        svc().agent_config_view(Project(repo), "workspace", "alias")
    refused("workspace", ["set claude.model=fable-2"], repo, "alias", match="alias")


# -- the write path refuses the same hostile files as the view (T-12 t3 fix, finding 1) ----

def configs(root: Path) -> dict:
    """Bytes of config/*.yml and of each run's config.yml; a directory named status.yml is never read."""
    paths = list((root / "config").glob("*.yml")) + list((root / "workspace").glob("*/config.yml"))
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in paths}


def write_refused(scope, changes, repo, slug=None, *, match):
    """Plan and apply both refuse, and a forged revision cannot get past the input checks."""
    before = tree(repo)
    with pytest.raises(svc().ConfigChangeRefused, match=match):
        plan(repo, scope, changes, slug)
    with pytest.raises(svc().ConfigChangeRefused, match=match):
        apply(repo, scope, changes, "forged-revision", slug)
    assert tree(repo) == before


def test_write_refuses_a_symlinked_project_config(repo):
    target = repo / "config" / "elsewhere.yml"
    target.write_text((repo / "config" / "agents.yml").read_text())
    (repo / "config" / "agents.yml").unlink()
    (repo / "config" / "agents.yml").symlink_to(target)
    write_refused("project", ["set claude.timeout_min=45"], repo, match="symbolic link")


def test_write_refuses_an_oversized_project_config(repo):
    (repo / "config" / "agents.yml").write_text("# " + "x" * (1024 * 1024) + "\n")
    write_refused("project", ["set claude.timeout_min=45"], repo, match="larger than 1 MiB")


def test_write_refuses_a_symlinked_run_config(repo):
    target = repo / "config" / "elsewhere.yml"
    target.write_text("agent_overrides: {}\n")
    (repo / "workspace" / "run-1" / "config.yml").symlink_to(target)
    write_refused("workspace", ["set claude.model=fable-2"], repo, "run-1", match="symbolic link")


def test_write_refuses_an_oversized_run_config(repo):
    (repo / "workspace" / "run-1" / "config.yml").write_text("# " + "x" * (1024 * 1024) + "\n")
    write_refused("workspace", ["set claude.model=fable-2"], repo, "run-1", match="larger than 1 MiB")


def test_write_refuses_a_run_status_that_is_not_a_regular_file(repo):
    # A directory, not a FIFO: the check must refuse without opening it.
    status = repo / "workspace" / "run-1" / "status.yml"
    status.mkdir()
    before = configs(repo)
    for call in (lambda: plan(repo, "workspace", ["set claude.model=fable-2"], "run-1"),
                 lambda: apply(repo, "workspace", ["set claude.model=fable-2"], "forged-revision", "run-1")):
        with pytest.raises(svc().ConfigChangeRefused, match="not a regular file"):
            call()
    assert configs(repo) == before
    assert not (repo / "workspace" / "run-1" / "config.yml").exists()
    assert status.is_dir()


def test_write_refuses_a_symlinked_news_config(repo):
    target = repo / "config" / "elsewhere-news.yml"
    target.write_text(NEWS)
    (repo / "config" / "news.yml").symlink_to(target)
    write_refused("news", ["set timeout=30"], repo, match="symbolic link")


def test_write_names_a_malformed_file_without_its_content(repo):
    (repo / "config" / "defaults.yml").write_text("assignments: [SECRETTOKEN, b\n")
    before = tree(repo)
    for call in (lambda: plan(repo, "project", ["set claude.timeout_min=45"]),
                 lambda: apply(repo, "project", ["set claude.timeout_min=45"], "forged-revision")):
        with pytest.raises(svc().ConfigChangeRefused) as info:
            call()
        assert "`config/defaults.yml`: not valid YAML (line" in str(info.value)
        assert "SECRETTOKEN" not in str(info.value)
    assert tree(repo) == before


# -- scope rules ------------------------------------------------------------------

@pytest.mark.parametrize("scope, slug, match", [
    ("", None, "unknown scope"),
    ("global", None, "unknown scope"),
    ("workspace", None, "run slug"),
    ("project", "run-1", "takes no run slug"),
    ("news", "run-1", "takes no run slug"),
    ("workspace", "../x", "not a run slug"),
    ("workspace", "missing-run", "no run"),
])
def test_scope_and_slug_rules_refuse_before_any_write(repo, scope, slug, match):
    refused(scope, ["set claude.model=fable-2"], repo, slug, match=match)


# -- allowlist per scope (the 5.4 table) --------------------------------------------

ALLOWLIST = [
    # project
    ("project", "set claude.model=fable-2", "ok"),
    ("project", "set claude.reasoning=extended-thinking", "ok"),
    ("project", "set claude.timeout_min=45", "ok"),
    ("project", "unset claude.reasoning", "ok"),
    ("project", "assign research.outline=codex", "ok"),
    ("project", "unset research.draft-reviewers", "ok"),
    ("project", "unset research.outline", "required"),
    ("project", "set claude.cmd=echo", "CLI-only"),
    ("project", "set claude.stdin_cmd=echo", "CLI-only"),
    ("project", "unset claude.cmd", "CLI-only"),
    ("project", "set claude.enabled=false", "CLI-only"),
    ("project", "promote research.search", "CLI-only"),
    ("project", "demote research.search", "CLI-only"),
    ("project", "set claude.bogus=1", "unsupported"),
    ("project", "set nope.model=x", "unsupported"),
    ("project", "assign research.nope=claude", "unsupported"),
    ("project", "unset claude", "unsupported"),
    # workspace
    ("workspace", "set claude.model=fable-2", "ok"),
    ("workspace", "set claude.reasoning=extended-thinking", "ok"),
    ("workspace", "unset claude.timeout_min", "ok"),
    ("workspace", "assign research.outline=codex", "ok"),
    ("workspace", "unset research.outline", "ok"),
    ("workspace", "set claude.cmd=echo", "CLI-only"),
    ("workspace", "set claude.stdin_cmd=echo", "CLI-only"),
    ("workspace", "set claude.enabled=false", "unsupported"),
    ("workspace", "promote research.search", "CLI-only"),
    ("workspace", "demote research.search", "CLI-only"),
    ("workspace", "set claude.bogus=1", "unsupported"),
    ("workspace", "set nope.model=x", "unsupported"),
    ("workspace", "assign research.nope=claude", "unsupported"),
    # news
    ("news", "set agent=codex", "ok"),
    ("news", "set model=fable-2", "ok"),
    ("news", "set timeout=30", "ok"),
    ("news", "unset model", "ok"),
    ("news", "assign research.outline=codex", "unsupported"),
    ("news", "unset research.outline", "unsupported"),
    ("news", "set cmd=echo", "unsupported"),
    ("news", "set interests=x", "unsupported"),
    ("news", "promote research.search", "unsupported"),
]


@pytest.mark.parametrize("scope, change, expected", ALLOWLIST)
def test_allowlist_matrix(news, status_ws, scope, change, expected):
    slug = "run-1" if scope == "workspace" else None
    before = tree(news)
    if expected == "ok":
        result = plan(news, scope, [change], slug)
        assert len(result["revision"]) == 64
        return
    with pytest.raises(svc().ConfigChangeRefused) as info:
        plan(news, scope, [change], slug)
    message = str(info.value)
    wording = {"CLI-only": "deliberately CLI-only", "unsupported": "unsupported",
               "required": "required"}[expected]
    assert wording in message, message
    assert tree(news) == before


def test_cli_only_refusals_name_the_terminal_command(repo):
    with pytest.raises(svc().ConfigChangeRefused, match="scieflow agent configure"):
        plan(repo, "project", ["set claude.cmd=echo"])


# -- value rules --------------------------------------------------------------------

@pytest.mark.parametrize("value", ["0", "-5", "abc", "inf", "nan"])
def test_timeout_must_be_a_positive_finite_number(repo, value):
    refused("project", [f"set claude.timeout_min={value}"], repo, match="timeout_min")


@pytest.mark.parametrize("value, match", [
    ("", "empty"),
    ("x" * 201, "200"),
    ("fab\x01le", "control"),
])
def test_model_value_rules(repo, value, match):
    refused("project", [f"set claude.model={value}"], repo, match=match)


def test_thinking_agent_accepts_only_default_or_extended(repo):
    refused("project", ["set claude.reasoning=max"], repo, match="default or extended-thinking")
    result = plan(repo, "project", ["set claude.reasoning=extended-thinking"])
    assert result["empty"] is False


def test_an_agent_without_effort_refuses_any_effort(repo):
    refused("project", ["set off.reasoning=high"], repo, match="no effort setting")
    refused("workspace", ["set off.reasoning=high"], repo, "run-1", match="no effort setting")


def test_placeholder_agent_takes_custom_effort_within_limits(repo):
    assert plan(repo, "project", ["set codex.reasoning=ultra-max"])["empty"] is False
    refused("project", ["set codex.reasoning=" + "x" * 65], repo, match="64")
    refused("project", ["set codex.reasoning=" + "\x07"], repo, match="control")
    refused("project", ["set codex.reasoning="], repo, match="empty")


def test_assignment_entries_obey_the_same_rules(repo):
    refused("project", ["assign research.outline=claude@" + "m" * 201], repo, match="200")
    refused("project", ["assign research.outline=off@/high"], repo, match="no effort setting")
    refused("project", ["assign research.outline=claude@fable/max"], repo,
            match="default or extended-thinking")


def test_at_most_fifty_changes_per_preview(repo):
    refused("project", ["set claude.timeout_min=45"] * 51, repo, match="at most 50")


@pytest.mark.parametrize("change, match", [
    ("set timeout=1.5", "positive whole number"),
    ("set timeout=0", "positive whole number"),
    ("set timeout=x", "number"),
    ("set reasoning=xhigh", "low, medium, high"),
    ("set agent=gpt", "claude, codex, agy"),
    ("set model=", "empty"),
])
def test_news_value_rules(news, change, match):
    refused("news", [change], news, match=match)


# -- preview: structure, inheritance, notes, warnings, no writes ----------------------

def test_structured_preview_lists_changed_keys_only(repo):
    result = plan(repo, "project", ["set claude.timeout_min=45"])
    assert result["files"] == ["config/agents.yml"]
    assert result["structured"] == [{"file": "config/agents.yml", "key": "agents.claude.timeout_min",
                                     "before": 30, "after": 45}]
    assert result["changes"] == ["set claude.timeout_min=45"]
    assert result["empty"] is False


def test_structured_helper_never_shows_a_command_template():
    before = "agents:\n  c:\n    cmd: old {prompt}\n    model: m\n"
    after = "agents:\n  c:\n    cmd: new {prompt}\n    model: m\n"
    rows = svc()._structured_diff("config/agents.yml", before, after)
    assert rows == [{"file": "config/agents.yml", "key": "agents.c.cmd",
                     "before": "(command template, not shown)",
                     "after": "(command template, not shown)"}]


def test_project_change_states_the_inheritance_sentence(repo):
    result = plan(repo, "project", ["set claude.timeout_min=45"])
    assert result["inheritance"] == [
        "Runs that do not override claude.timeout_min will use the new value; "
        "runs with their own override keep it."]
    assert plan(repo, "workspace", ["set claude.timeout_min=45"], "run-1")["inheritance"] == []


def test_workspace_without_status_keeps_the_note(repo):
    result = plan(repo, "workspace", ["set claude.timeout_min=45"], "run-1")
    assert any("no status.yml" in note for note in result["notes"])


def test_news_agy_keeps_its_warning(news):
    result = plan(news, "news", ["set agent=agy"])
    assert any("support-tier" in warning for warning in result["warnings"])


def test_preview_writes_nothing(repo, status_ws):
    before = tree(repo)
    plan(repo, "project", ["set claude.timeout_min=45"])
    plan(repo, "workspace", ["set claude.timeout_min=45"], "run-1")
    assert tree(repo) == before


# -- apply ----------------------------------------------------------------------------

def test_apply_round_trip_writes_exactly_the_previewed_change(repo):
    previewed = plan(repo, "project", ["set claude.timeout_min=45"])
    result = apply(repo, "project", ["set claude.timeout_min=45"], previewed["revision"])
    assert result["written"] == ["config/agents.yml"]
    assert load(repo, "config/agents.yml")["agents"]["claude"]["timeout_min"] == 45


def test_a_changed_file_after_preview_is_stale_and_nothing_is_written(repo):
    previewed = plan(repo, "project", ["set claude.timeout_min=45"])
    edited = (repo / "config" / "agents.yml").read_text().replace("timeout_min: 30", "timeout_min: 31")
    (repo / "config" / "agents.yml").write_text(edited)
    before = tree(repo)
    with pytest.raises(svc().StaleConfiguration, match="preview again"):
        apply(repo, "project", ["set claude.timeout_min=45"], previewed["revision"])
    assert tree(repo) == before


def test_a_different_change_text_with_the_same_revision_is_stale(repo):
    previewed = plan(repo, "project", ["set claude.timeout_min=45"])
    before = tree(repo)
    with pytest.raises(svc().StaleConfiguration):
        apply(repo, "project", ["set claude.timeout_min=46"], previewed["revision"])
    assert tree(repo) == before


def test_apply_without_a_revision_writes_nothing(repo):
    before = tree(repo)
    for revision in (None, ""):
        with pytest.raises(svc().ConfigChangeRefused, match="preview"):
            apply(repo, "project", ["set claude.timeout_min=45"], revision)
    assert tree(repo) == before


def test_an_empty_change_is_refused_on_apply(repo):
    previewed = plan(repo, "project", ["set claude.model=fable"])
    assert previewed["empty"] is True
    before = tree(repo)
    with pytest.raises(svc().ConfigChangeRefused, match="nothing"):
        apply(repo, "project", ["set claude.model=fable"], previewed["revision"])
    assert tree(repo) == before


def test_a_held_project_lock_is_stale_for_preview_and_apply(repo, monkeypatch):
    previewed = plan(repo, "project", ["set claude.timeout_min=45"])
    monkeypatch.setattr(acf, "CONFIGURE_LOCK_TIMEOUT", 0.3)
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time, filelock\n"
        "lock = filelock.FileLock(sys.argv[1]); lock.acquire()\n"
        "print('held', flush=True); time.sleep(60)\n"), str(repo / LOCK)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with pytest.raises(svc().StaleConfiguration):
            plan(repo, "project", ["set claude.timeout_min=45"])
        with pytest.raises(svc().StaleConfiguration):
            apply(repo, "project", ["set claude.timeout_min=45"], previewed["revision"])
        assert time.monotonic() - started < 5
    finally:
        holder.kill()
        holder.wait()


def test_a_partial_write_is_reported_with_its_written_and_uncertain_lists(repo, monkeypatch):
    previewed = plan(repo, "project", ["set claude.timeout_min=45"])
    partial = acf.PartialWriteError(written=["config/a.yml"], uncertain=["config/b.yml"], unchanged=[])
    monkeypatch.setattr(acf, "apply", lambda plan_: (_ for _ in ()).throw(partial))
    with pytest.raises(svc().StaleConfiguration) as info:
        apply(repo, "project", ["set claude.timeout_min=45"], previewed["revision"])
    assert info.value.written == ["config/a.yml"]
    assert info.value.uncertain == ["config/b.yml"]


def test_a_real_partial_write_names_only_project_relative_files(repo, monkeypatch):
    previewed = plan(repo, "project", ["set claude.timeout_min=45"])

    def fail(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_atomic_write", fail)
    with pytest.raises(svc().StaleConfiguration) as info:
        apply(repo, "project", ["set claude.timeout_min=45"], previewed["revision"])
    message = str(info.value)
    assert "uncertain: config/agents.yml" in message
    assert "unchanged: none" in message
    assert str(repo) not in message
    assert info.value.uncertain == ["config/agents.yml"]


# -- isolation and file families -------------------------------------------------------

def test_a_workspace_change_touches_only_that_runs_config(repo, status_ws, write_ws):
    write_ws(repo, {"agent_overrides": {"claude": {"timeout_min": 12}}})
    other = repo / "workspace" / "run-2"
    other.mkdir()
    (other / "config.yml").write_text("slug: run-2\n")
    before = tree(repo)
    change = ["set claude.model=fable-2"]
    previewed = plan(repo, "workspace", change, "run-1")
    apply(repo, "workspace", change, previewed["revision"], "run-1")
    after = tree(repo)
    assert after["workspace/run-1/config.yml"][0] != before["workspace/run-1/config.yml"][0]
    for name in before:
        if name != "workspace/run-1/config.yml":
            assert after[name] == before[name], name


def test_a_project_timeout_change_touches_only_the_registry(repo):
    before = tree(repo)
    previewed = plan(repo, "project", ["set claude.timeout_min=45"])
    apply(repo, "project", ["set claude.timeout_min=45"], previewed["revision"])
    after = tree(repo)
    assert after["config/defaults.yml"] == before["config/defaults.yml"]
    assert after["config/agents.yml"] != before["config/agents.yml"]


def test_a_project_assignment_touches_only_the_defaults(repo):
    before = tree(repo)
    previewed = plan(repo, "project", ["assign research.outline=codex"])
    apply(repo, "project", ["assign research.outline=codex"], previewed["revision"])
    after = tree(repo)
    assert after["config/agents.yml"] == before["config/agents.yml"]
    assert after["config/defaults.yml"] != before["config/defaults.yml"]


def test_a_mixed_registry_and_assignment_change_is_refused_in_the_browser(repo):
    refused("project", ["set claude.timeout_min=45", "assign research.outline=codex"], repo,
            match="not both")


def test_a_news_change_touches_only_news_yml(news):
    before = tree(news)
    previewed = plan(news, "news", ["set timeout=30"])
    apply(news, "news", ["set timeout=30"], previewed["revision"])
    after = tree(news)
    assert after["config/news.yml"] != before["config/news.yml"]
    assert {k: v for k, v in after.items() if k != "config/news.yml"} == \
        {k: v for k, v in before.items() if k != "config/news.yml"}


# -- reset semantics --------------------------------------------------------------------

def test_a_workspace_unset_removes_only_that_override(repo, write_ws):
    write_ws(repo, {"agent_overrides": {"claude": {"timeout_min": 12, "model": "opus"}}})
    previewed = plan(repo, "workspace", ["unset claude.timeout_min"], "run-1")
    apply(repo, "workspace", ["unset claude.timeout_min"], previewed["revision"], "run-1")
    assert load(repo, "workspace/run-1/config.yml") == {"agent_overrides": {"claude": {"model": "opus"}}}


def test_a_workspace_value_equal_to_the_registry_removes_the_delta(repo, write_ws):
    write_ws(repo, {"agent_overrides": {"claude": {"timeout_min": 12}}})
    previewed = plan(repo, "workspace", ["set claude.timeout_min=30"], "run-1")
    apply(repo, "workspace", ["set claude.timeout_min=30"], previewed["revision"], "run-1")
    assert "agent_overrides" not in (load(repo, "workspace/run-1/config.yml") or {})


def test_a_role_reset_exposes_the_legacy_key_with_its_source(repo, write_ws):
    write_ws(repo, {"reviewer": "codex", "assignments": {"research.reviewer": "claude"}})
    previewed = plan(repo, "workspace", ["unset research.reviewer"], "run-1")
    apply(repo, "workspace", ["unset research.reviewer"], previewed["revision"], "run-1")
    role = svc().agent_config_view(Project(repo), "workspace", "run-1")["roles"]["research.reviewer"]
    assert role["source"] == "workspace (legacy key)" and role["value"] == "codex"


def test_a_reset_preview_shows_the_effective_value_and_its_source(repo, write_ws):
    write_ws(repo, {"reviewer": "codex", "assignments": {"research.reviewer": "claude"}})
    result = plan(repo, "workspace", ["unset research.reviewer"], "run-1")
    assert result["effective"] == [{"key": "research.reviewer", "value": "codex",
                                    "source": "workspace (legacy key)"}]


def test_a_workspace_override_preview_shows_its_source(repo, write_ws):
    write_ws(repo, {})
    timeout = plan(repo, "workspace", ["set claude.timeout_min=45"], "run-1")
    assert timeout["effective"] == [{"key": "claude.timeout_min", "value": 45, "source": "workspace"}]


def test_a_project_role_reset_is_refused_except_the_reviewer_pool(repo):
    refused("project", ["unset research.outline"], repo, match="required")
    previewed = plan(repo, "project", ["unset research.draft-reviewers"])
    assert previewed["empty"] is True


# -- parity -----------------------------------------------------------------------------

def test_the_three_entries_are_in_the_parity_table():
    table = yaml.safe_load((Path(__file__).parent / "cli_parity.yml").read_text())["functions"]
    assert table["agent_config_view"]["cli"] == "agent show"
    assert table["plan_agent_changes"]["cli"] == "agent configure"
    assert table["apply_agent_changes"]["cli"] == "agent configure"
