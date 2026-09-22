"""The push/pull wrapper scripts: selection, flags, and what they invoke."""

from pathlib import Path

import pytest
import yaml

import chats_sync
from scieflow.chats.model import ChatRef


class FakeUI:
    def __init__(self, answers):
        self.answers = list(answers)

    def checkbox(self, message, choices, checked=()):
        return self.answers.pop(0)

    def select(self, message, choices, back=True):
        answer = self.answers.pop(0)
        return next(v for label, v in choices if label == answer or v == answer)

    def confirm(self, message, default=False):
        return self.answers.pop(0)


def ref(tool, project, size=100, chat_id="x"):
    return ChatRef(tool=tool, chat_id=f"{tool}-{chat_id}", project_path=project,
                   title="t", started=None, updated=None, size_bytes=size,
                   message_count=1)


REFS = [ref("claude", "/home/u/A", 300, "1"), ref("claude", "/home/u/B", 200, "2"),
        ref("codex", "/home/u/A", 100, "3"), ref("gemini", "/home/u/B", 50, "4")]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "config" / "chats.yml").write_text(yaml.safe_dump({
        "stores": {"claude": {"root": str(tmp_path / ".claude")}},
        "bundle_dir": str(tmp_path / "bundles"),
        "remote": {"enabled": True, "dir": "workspace/chats"},
    }))
    (tmp_path / "workspace" / "chats").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(argv):
        seen.append(argv)
        return 0

    monkeypatch.setattr(chats_sync, "run_cli", fake)
    return seen


def run(argv, monkeypatch, ui=None, refs=REFS):
    monkeypatch.setattr(chats_sync, "discover", lambda *a, **k: list(refs))
    if ui is not None:
        monkeypatch.setattr(chats_sync, "UI", lambda: ui)
    args = chats_sync.build_parser().parse_args(argv)
    return args.func(args)


def test_agent_choices_carry_counts_and_sizes(repo):
    ui = FakeUI([["claude"]])
    seen = {}

    def checkbox(message, choices, checked=()):
        seen["choices"] = [label for label, _ in choices]
        seen["checked"] = list(checked)
        return ["claude"]

    ui.checkbox = checkbox
    assert chats_sync.choose_tools(ui, REFS, []) == ["claude"]
    assert any("claude" in c and "2 chats" in c for c in seen["choices"])
    assert set(seen["checked"]) == {"claude", "codex", "gemini"}  # all ticked by default


def test_project_choice_all_ticked_means_no_filter():
    ui = FakeUI([["/home/u/A", "/home/u/B"]])
    assert chats_sync.choose_projects(ui, REFS) == []


def test_project_subset_is_returned():
    ui = FakeUI([["/home/u/B"]])
    assert chats_sync.choose_projects(ui, REFS) == ["/home/u/B"]


def test_push_passes_the_picked_agents_and_projects(repo, calls, monkeypatch):
    out = repo / "bundles" / "b.zip"

    def fake(argv):
        calls.append(argv)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"zip")
        return 0

    monkeypatch.setattr(chats_sync, "run_cli", fake)
    ui = FakeUI([["claude", "codex"], ["/home/u/A"]])
    assert run(["push", "--yes", "--no-push", "--out", str(out)], monkeypatch, ui) == 0
    backup = calls[0]
    assert backup[:3] == ["chats", "backup", "--all"]
    assert backup.count("--tool") == 2 and "claude" in backup and "codex" in backup
    assert backup[backup.index("--project") + 1] == "/home/u/A"
    assert "--yes" in backup


def test_push_with_flags_asks_nothing_and_filters_once(repo, calls, monkeypatch):
    """--project is a substring flag; it must not be re-applied as an exact match."""
    out = repo / "b.zip"
    monkeypatch.setattr(chats_sync, "run_cli",
                        lambda argv: (calls.append(argv), out.write_bytes(b"z"), 0)[-1])
    ui = FakeUI([])  # no questions may be asked
    code = run(["push", "--tool", "claude", "--project", "u/A", "--yes", "--no-push",
                "--out", str(out)], monkeypatch, ui)
    assert code == 0
    assert calls[0][calls[0].index("--project") + 1] == "u/A"


def test_push_uploads_and_can_commit_the_pointer(repo, calls, monkeypatch):
    out = repo / "bundles" / "b.zip.gpg"
    monkeypatch.setattr(chats_sync, "run_cli",
                        lambda argv: (calls.append(argv),
                                      out.parent.mkdir(parents=True, exist_ok=True),
                                      out.write_bytes(b"z"), 0)[-1])
    committed = []
    monkeypatch.setattr(chats_sync, "commit_pointer",
                        lambda pointer, name: committed.append(name) or 0)
    (repo / "workspace" / "chats" / "b.zip.gpg.dvc").write_text("outs: []\n")
    ui = FakeUI([["claude"], []])
    assert run(["push", "--yes", "--commit", "--out", str(out)], monkeypatch, ui) == 0
    assert calls[1] == ["chats", "push", str(out)]
    assert committed == ["b.zip.gpg"]


def test_push_skips_upload_when_remote_is_off(repo, calls, monkeypatch, capsys):
    cfg = repo / "config" / "chats.yml"
    doc = yaml.safe_load(cfg.read_text())
    doc["remote"]["enabled"] = False
    cfg.write_text(yaml.safe_dump(doc))
    out = repo / "b.zip"
    monkeypatch.setattr(chats_sync, "run_cli",
                        lambda argv: (calls.append(argv), out.write_bytes(b"z"), 0)[-1])
    ui = FakeUI([["claude"], []])
    assert run(["push", "--yes", "--out", str(out)], monkeypatch, ui) == 0
    assert len(calls) == 1  # backup only
    assert "remote sync is off" in capsys.readouterr().out


def test_pull_latest_inspects_then_dry_runs_then_applies(repo, calls, monkeypatch):
    pointer = repo / "workspace" / "chats" / "b.zip.gpg.dvc"
    pointer.write_text("outs: []\n")
    bundle = repo / "bundles" / "b.zip.gpg"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_bytes(b"z")
    ui = FakeUI([True])  # yes, apply
    assert run(["pull", "--latest"], monkeypatch, ui) == 0
    assert calls[0][:2] == ["chats", "inspect"]
    assert calls[1][:2] == ["chats", "restore"] and "--apply" not in calls[1]
    assert "--apply" in calls[2] and "--yes" in calls[2]


def test_pull_no_restore_stops_after_inspect(repo, calls, monkeypatch):
    (repo / "workspace" / "chats" / "b.zip.gpg.dvc").write_text("outs: []\n")
    bundle = repo / "bundles" / "b.zip.gpg"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_bytes(b"z")
    assert run(["pull", "--latest", "--no-restore"], monkeypatch, FakeUI([])) == 0
    assert [c[1] for c in calls] == ["inspect"]


def test_pull_refuses_when_remote_is_off(repo, monkeypatch):
    cfg = repo / "config" / "chats.yml"
    doc = yaml.safe_load(cfg.read_text())
    doc["remote"]["enabled"] = False
    cfg.write_text(yaml.safe_dump(doc))
    with pytest.raises(SystemExit, match="remote sync is off"):
        run(["pull", "--latest"], monkeypatch, FakeUI([]))


def test_pull_names_a_missing_bundle(repo, monkeypatch):
    (repo / "workspace" / "chats" / "b.zip.gpg.dvc").write_text("outs: []\n")
    with pytest.raises(SystemExit, match="no tracked bundle matches"):
        run(["pull", "nothing-like-this"], monkeypatch, FakeUI([]))


def test_pull_downloads_when_the_bundle_is_absent(repo, calls, monkeypatch):
    (repo / "workspace" / "chats" / "b.zip.gpg.dvc").write_text("outs: []\n")
    assert run(["pull", "--latest", "--no-restore"], monkeypatch, FakeUI([])) == 0
    assert calls[0] == ["chats", "pull", "b.zip.gpg"]


def test_shell_wrappers_delegate(tmp_path):
    for name, command in (("chats-push.sh", "push"), ("chats-pull.sh", "pull")):
        text = Path("scripts", name).read_text()
        assert f"chats_sync.py {command}" in text
        assert Path("scripts", name).stat().st_mode & 0o111
