"""The claim-check module's contract with the rest of the repo.

These guard the seams that unit tests cannot see: the shipped example config,
the run option, the CLI/config vocabulary, and the documentation that tells a
user (and an agent) the module is opt-in.
"""
import re
from pathlib import Path

import pytest
import yaml

from nblm import nblm, policy
from scieflow.core import config as sf_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "notebooklm.example.yml"
SCHEMA = ROOT / "schemas" / "claim-audit.yml"
SKILL = ROOT / "skills" / "claim-check" / "SKILL.md"
AGENTS = ROOT / "AGENTS.md"
README = ROOT / "README.md"
CLAIM_CHECK_VALUES = {"never", "ask", "approved"}


@pytest.fixture
def example_root(tmp_path):
    """A repo root whose notebooklm.yml is the shipped example, verbatim."""
    (tmp_path / "config").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "config" / "notebooklm.yml").write_text(EXAMPLE.read_text())
    (tmp_path / "schemas" / "claim-audit.yml").write_text(SCHEMA.read_text())
    return tmp_path


# --- the shipped example must stay loadable ----------------------------

def test_example_config_passes_its_own_policy(example_root):
    profile = policy.load_profile(example_root, "default")
    assert profile.notebook_prefix
    assert profile.limits["reask_threshold"] in policy.load_verdicts(example_root)


def test_example_config_grants_no_destructive_op():
    profile = yaml.safe_load(EXAMPLE.read_text())["profiles"]["default"]
    assert "delete" not in profile["allowed_ops"]


def test_example_allowed_ops_are_all_real_subcommands():
    profile = yaml.safe_load(EXAMPLE.read_text())["profiles"]["default"]
    subcommands = set()
    for action in nblm.build_parser()._subparsers._group_actions:
        subcommands |= set(action.choices)
    assert set(profile["allowed_ops"]) <= subcommands


def test_example_stays_under_the_free_tier_chat_ceiling():
    limits = yaml.safe_load(EXAMPLE.read_text())["profiles"]["default"]["limits"]
    assert limits["max_questions_per_day"] <= 50
    assert limits["max_questions_per_run"] <= limits["max_questions_per_day"]


def test_example_source_hosts_are_bare_hostnames():
    hosts = yaml.safe_load(EXAMPLE.read_text())["profiles"]["default"]["source_hosts"]
    assert hosts
    for host in hosts:
        assert "/" not in host and ":" not in host, host


# --- claim_check is a real run option ----------------------------------

def test_defaults_declare_claim_check_and_default_to_ask():
    defaults = sf_config.load_defaults(ROOT)
    assert defaults["claim_check"] == "ask"
    assert defaults["claim_check"] in CLAIM_CHECK_VALUES


def test_new_workspace_carries_the_claim_check_setting(tmp_path):
    import sfx_init
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\n")
    ws = sfx_init.init_workspace("run-x", goal, tmp_path / "workspace", {}, ROOT)
    cfg = yaml.safe_load((ws / "config.yml").read_text())
    assert cfg["claim_check"] in CLAIM_CHECK_VALUES


def test_run_config_can_override_claim_check(tmp_path):
    ws = tmp_path / "run-y"
    ws.mkdir()
    (ws / "config.yml").write_text("claim_check: never\n")
    assert sf_config.load_run_config(ws, ROOT)["claim_check"] == "never"


# --- documentation must say it is opt-in -------------------------------

def test_agents_rule_12_exists_and_is_opt_in():
    text = AGENTS.read_text()
    rule = re.search(r"^12\. (.+?)(?=\n\S|\n## )", text, re.M | re.S)
    assert rule, "AGENTS.md has no rule 12"
    body = rule.group(1)
    assert "claim-check" in body
    assert "Never start an audit on your own" in body
    for value in CLAIM_CHECK_VALUES:
        assert f"`{value}`" in body, value


def test_agents_skills_table_lists_the_skill():
    assert "skills/claim-check/SKILL.md" in AGENTS.read_text().split("## Skills")[1]


def test_skill_documents_the_gate_and_every_verdict():
    text = SKILL.read_text()
    assert text.startswith("---\nname: claim-check\n")
    assert "## The gate" in text
    for value in CLAIM_CHECK_VALUES:
        assert f"`{value}`" in text, value
    for verdict in yaml.safe_load(SCHEMA.read_text())["verdicts"]:
        assert f"`{verdict}`" in text, f"skill does not explain verdict {verdict}"


def test_readme_documents_setup_and_consent():
    text = README.read_text()
    assert "## Citation checking (optional, opt-in)" in text
    assert "uv sync --group notebooklm" in text
    assert "config/notebooklm.example.yml config/notebooklm.yml" in text
    for value in CLAIM_CHECK_VALUES:
        assert f"`{value}`" in text, value


def test_the_user_config_is_gitignored():
    assert "config/notebooklm.yml" in (ROOT / ".gitignore").read_text().splitlines()


def test_claim_check_values_survive_yaml_parsing(tmp_path):
    """'off'/'on' would come back as booleans; the vocabulary must not use them."""
    for value in CLAIM_CHECK_VALUES:
        target = tmp_path / f"{value}.yml"
        target.write_text(f"claim_check: {value}\n")
        assert yaml.safe_load(target.read_text())["claim_check"] == value
