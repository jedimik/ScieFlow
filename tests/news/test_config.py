from pathlib import Path

import pytest

from scieflow.news.config import (
    Config,
    ConfigError,
    Interest,
    Group,
    group_interest_names,
    resolve_selection,
    load_config,
    save_config,
)

FULL = """\
agent: codex
lookback_days: 14
timeout: 120
interests:
  - name: Snakemake
    context: HPC pipelines with SLURM
    repo: snakemake/snakemake
    urls:
      - https://snakemake.readthedocs.io
    keywords: [workflow, bioinformatics]
  - name: DuckDB
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "news.yml"
    p.write_text(text)
    return p


def test_full_config(tmp_path):
    cfg = load_config(write(tmp_path, FULL))
    assert cfg.agent == "codex"
    assert cfg.lookback_days == 14
    assert cfg.timeout == 120
    assert cfg.interests[0] == Interest(
        name="Snakemake",
        context="HPC pipelines with SLURM",
        repo="snakemake/snakemake",
        urls=["https://snakemake.readthedocs.io"],
        keywords=["workflow", "bioinformatics"],
    )
    assert cfg.interests[1] == Interest(name="DuckDB")


def test_defaults(tmp_path):
    cfg = load_config(write(tmp_path, "interests:\n  - name: X\n"))
    assert (cfg.agent, cfg.lookback_days, cfg.timeout) == ("claude", 30, None)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml(tmp_path):
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(write(tmp_path, "interests: [unclosed"))


def test_bad_agent(tmp_path):
    with pytest.raises(ConfigError, match="agent must be one of"):
        load_config(write(tmp_path, "agent: gemini\ninterests:\n  - name: X\n"))


def test_bad_lookback(tmp_path):
    with pytest.raises(ConfigError, match="lookback_days"):
        load_config(write(tmp_path, "lookback_days: -1\ninterests:\n  - name: X\n"))


def test_timeout_absent_is_none(tmp_path):
    cfg = load_config(write(tmp_path, "interests:\n  - name: X\n"))
    assert cfg.timeout is None


def test_timeout_explicit_value_loads(tmp_path):
    cfg = load_config(write(tmp_path, "timeout: 120\ninterests:\n  - name: X\n"))
    assert cfg.timeout == 120


@pytest.mark.parametrize("bad", [0, "x", True])
def test_timeout_invalid_raises(tmp_path, bad):
    with pytest.raises(ConfigError, match="timeout"):
        load_config(write(tmp_path, f"timeout: {bad!r}\ninterests:\n  - name: X\n"))


def test_save_config_omits_timeout_when_none(tmp_path):
    path = tmp_path / "news.yml"
    save_config(Config(interests=[Interest(name="X")], timeout=None), path)
    text = path.read_text()
    assert "timeout" not in text


def test_save_config_writes_timeout_when_set(tmp_path):
    path = tmp_path / "news.yml"
    save_config(Config(interests=[Interest(name="X")], timeout=120), path)
    text = path.read_text()
    assert "timeout: 120" in text


def test_no_interests(tmp_path):
    with pytest.raises(ConfigError, match="interests"):
        load_config(write(tmp_path, "agent: claude\n"))


def test_interest_without_name(tmp_path):
    with pytest.raises(ConfigError, match=r"interests\[0\]"):
        load_config(write(tmp_path, "interests:\n  - context: foo\n"))


def test_duplicate_names(tmp_path):
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(write(tmp_path, "interests:\n  - name: X\n  - name: X\n"))


def test_urls_not_list(tmp_path):
    with pytest.raises(ConfigError, match="urls"):
        load_config(write(tmp_path, "interests:\n  - name: X\n    urls: notalist\n"))


def test_unknown_top_level_key(tmp_path):
    with pytest.raises(ConfigError, match="unknown config key: 'lookback_day'"):
        load_config(
            write(tmp_path, "lookback_day: 60\ninterests:\n  - name: X\n")
        )


def test_unknown_interest_key(tmp_path):
    with pytest.raises(
        ConfigError, match=r"interest 'Snakemake': unknown key 'url'"
    ):
        load_config(
            write(
                tmp_path,
                "interests:\n  - name: Snakemake\n    url: https://example.com\n",
            )
        )


def test_save_config_roundtrip(tmp_path):
    original = Config(
        agent="agy",
        lookback_days=14,
        timeout=120,
        interests=[
            Interest(
                name="Snakemake",
                context="HPC pipelines",
                repo="snakemake/snakemake",
                urls=["https://snakemake.readthedocs.io"],
                keywords=["workflow"],
            ),
            Interest(name="DuckDB"),
        ],
    )
    path = tmp_path / "news.yml"
    save_config(original, path)
    assert load_config(path) == original


def test_save_config_omits_empty_optionals(tmp_path):
    path = tmp_path / "news.yml"
    save_config(Config(interests=[Interest(name="X")]), path)
    text = path.read_text()
    assert "context" not in text and "repo" not in text
    assert "urls" not in text and "keywords" not in text


GROUPED = """\
model: claude-fable-5
reasoning: high
interests:
  - name: Snakemake
  - name: Nextflow
  - name: DuckDB
groups:
  - name: Bio
    interests: [Snakemake]
    groups:
      - name: Workflows
        interests: [Nextflow, Snakemake]
  - name: Data
    interests: [DuckDB]
"""


def test_groups_model_reasoning_parse(tmp_path):
    cfg = load_config(write(tmp_path, GROUPED))
    assert cfg.model == "claude-fable-5"
    assert cfg.reasoning == "high"
    assert [g.name for g in cfg.groups] == ["Bio", "Data"]
    assert cfg.groups[0].groups[0] == Group(name="Workflows", interests=["Nextflow", "Snakemake"])


def test_group_interest_names_dedup():
    g = Group(name="Bio", interests=["A"], groups=[Group(name="W", interests=["B", "A"])])
    assert group_interest_names(g) == ["A", "B"]


def test_groups_depth_limit(tmp_path):
    deep = (
        "interests:\n  - name: X\n"
        "groups:\n  - name: a\n    groups:\n      - name: b\n        groups:\n"
        "          - name: c\n            groups:\n              - name: d\n"
    )
    with pytest.raises(ConfigError, match="depth"):
        load_config(write(tmp_path, deep))


def test_group_unknown_interest(tmp_path):
    bad = "interests:\n  - name: X\ngroups:\n  - name: g\n    interests: [Nope]\n"
    with pytest.raises(ConfigError, match="Nope"):
        load_config(write(tmp_path, bad))


def test_group_duplicate_names(tmp_path):
    bad = "interests:\n  - name: X\ngroups:\n  - name: g\n  - name: g\n"
    with pytest.raises(ConfigError, match="duplicate group"):
        load_config(write(tmp_path, bad))


def test_bad_reasoning(tmp_path):
    with pytest.raises(ConfigError, match="reasoning"):
        load_config(write(tmp_path, "reasoning: extreme\ninterests:\n  - name: X\n"))


def test_save_config_roundtrips_groups(tmp_path):
    path = write(tmp_path, GROUPED)
    cfg = load_config(path)
    out = tmp_path / "out.yaml"
    save_config(cfg, out)
    assert load_config(out) == cfg
    text = out.read_text()
    assert text.index("model") < text.index("interests:")


def _grouped_config(tmp_path):
    return load_config(write(tmp_path, GROUPED))


def test_resolve_selection_none_when_empty(tmp_path):
    cfg = _grouped_config(tmp_path)
    assert resolve_selection(cfg, None, None) is None
    assert resolve_selection(cfg, [], []) is None


def test_resolve_selection_groups_and_interests_config_order(tmp_path):
    cfg = _grouped_config(tmp_path)
    # Bio -> Snakemake, Workflows -> Nextflow, Snakemake ; plus explicit DuckDB
    assert resolve_selection(cfg, ["DuckDB"], ["Bio"]) == ["Snakemake", "Nextflow", "DuckDB"]


def test_resolve_selection_unknowns(tmp_path):
    cfg = _grouped_config(tmp_path)
    with pytest.raises(ValueError, match="unknown groups: Ghost"):
        resolve_selection(cfg, None, ["Ghost"])
    with pytest.raises(ValueError, match="unknown interests: Nope"):
        resolve_selection(cfg, ["Nope"], None)


def test_explicit_null_groups_key_loads_as_empty_list(tmp_path):
    cfg = load_config(write(tmp_path, "interests:\n  - name: X\ngroups:\n"))
    assert cfg.groups == []


def test_interest_template_and_lookback(tmp_path):
    text = (
        "interests:\n"
        "  - name: PLM\n    template: science\n    lookback_days: 60\n"
        "  - name: X\n"
    )
    cfg = load_config(write(tmp_path, text))
    assert cfg.interests[0].template == "science"
    assert cfg.interests[0].lookback_days == 60
    assert cfg.interests[1].template == "tool"
    assert cfg.interests[1].lookback_days is None


def test_interest_bad_template(tmp_path):
    with pytest.raises(ConfigError, match="template"):
        load_config(write(tmp_path, "interests:\n  - name: X\n    template: nope\n"))


def test_interest_bad_lookback(tmp_path):
    with pytest.raises(ConfigError, match="lookback_days"):
        load_config(write(tmp_path, "interests:\n  - name: X\n    lookback_days: 0\n"))


def test_save_config_roundtrips_template_and_lookback(tmp_path):
    cfg = Config(interests=[
        Interest(name="PLM", template="science", lookback_days=60),
        Interest(name="X"),
    ])
    path = tmp_path / "news.yml"
    save_config(cfg, path)
    assert load_config(path) == cfg
    text = path.read_text()
    assert "template" in text and text.count("template") == 1  # omitted at default
    assert "lookback_days" in text
