import sys
from pathlib import Path

import pytest
import yaml

from scieflow.core.project import Project
from scieflow.core.run import status

ROOT = Path(__file__).resolve().parents[2]
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"

REGISTRY = {
    "agents": {
        "claude": {"cmd": "claude -p --model {model} {prompt}", "model": "fable",
                   "tier": "primary", "timeout_min": 30, "enabled": True,
                   "menu": {"models": ["fable", "opus"],
                            "reasoning": {"levels": ["default", "extended-thinking"]}}},
        "codex": {"cmd": "codex exec --model {model} -c effort={reasoning} {prompt}",
                  "model": "sol", "reasoning": "medium", "tier": "primary",
                  "timeout_min": 180, "enabled": True,
                  "menu": {"models": ["sol"],
                           "reasoning": {"levels": ["minimal", "low", "medium", "high"]}}},
        "agy": {"cmd": "agy --print {prompt} --model {model} --effort {reasoning}",
                "model": "gem", "reasoning": "high", "tier": "support",
                "timeout_min": 60, "enabled": True},
        "off": {"cmd": "off {prompt}", "tier": "primary", "enabled": False},
    }
}

ASSIGNMENTS = {
    "loop.experiment": "claude",
    "loop.literature": "claude",
    "loop.paper-draft": "claude",
    "research.search": ["claude", "codex", "agy"],
    "research.cross-review": ["claude", "codex"],
    "research.gap-analysis": ["claude", "codex"],
    "research.debate": ["claude", "codex"],
    "research.journal-profile": ["agy", "claude"],
    "research.reviewer": "codex",
    "research.submitter": "claude",
    "research.outline": "claude",
    "research.draft-authors": ["claude", "codex"],
    "research.consistency": "codex",
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(yaml.safe_dump(REGISTRY, sort_keys=False))
    (tmp_path / "config" / "defaults.yml").write_text(
        yaml.safe_dump({"archive": False, "assignments": ASSIGNMENTS}, sort_keys=False)
    )
    (tmp_path / "workspace" / "run-1").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def write_ws():
    def _write(repo, data: dict, slug: str = "run-1") -> None:
        (repo / "workspace" / slug / "config.yml").write_text(yaml.safe_dump(data))

    return _write


@pytest.fixture
def default_assignments():
    return dict(ASSIGNMENTS)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1, family: claude, '
        f'session_cmd: "{STUB}", resume_cmd: "{STUB} {{session}}"}}\n'
        f'  stub2: {{cmd: "{STUB}", enabled: true, timeout_min: 1, family: claude, '
        f'session_cmd: "{STUB}", resume_cmd: "{STUB} {{session}}"}}\n'
        '  sleepy: {cmd: "sleep 300", enabled: true, timeout_min: 5}\n'
        '  sleepy_turn: {cmd: "sleep 300", enabled: true, timeout_min: 5, '
        'family: claude, session_cmd: "sleep 300", resume_cmd: "sleep 300"}\n'
        f'  stub_disabled: {{cmd: "{STUB}", enabled: false, timeout_min: 1, family: claude, '
        f'session_cmd: "{STUB}", resume_cmd: "{STUB} {{session}}"}}\n')
    (tmp_path / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    # A real run always carries config.yml beside status.yml (run/init.py).
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return Project(tmp_path)
