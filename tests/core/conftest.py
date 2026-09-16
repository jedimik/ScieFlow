import pytest
import yaml

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
