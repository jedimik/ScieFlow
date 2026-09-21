import pytest

from scieflow.chats.config import ConfigError, load_config
from scieflow.chats.select import parse_ranges


def test_roots_are_expanded(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("stores:\n  claude:\n    root: ~/.claude\n")
    config = load_config(cfg)
    assert config.stores["claude"].root.is_absolute()
    assert "~" not in str(config.stores["claude"].root)


@pytest.mark.parametrize(
    "body, message",
    [
        ("stores: {}\n", "non-empty 'stores'"),
        ("stores:\n  nope:\n    root: /x\n", "unknown store"),
        ("stores:\n  claude:\n    bad: 1\n", "unknown key"),
        ("stores:\n  claude: {root: /x}\nencryption: {method: rot13}\n", "encryption.method"),
        ("stores:\n  claude: {root: /x}\nmax_bundle_gb: 0\n", "max_bundle_gb"),
    ],
)
def test_bad_config_is_rejected(tmp_path, body, message):
    cfg = tmp_path / "c.yml"
    cfg.write_text(body)
    with pytest.raises(ConfigError, match=message):
        load_config(cfg)


def test_disabled_and_missing_stores_are_skipped(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        f"stores:\n"
        f"  claude: {{root: {tmp_path}, enabled: false}}\n"
        f"  codex: {{root: {tmp_path / 'absent'}}}\n"
    )
    assert load_config(cfg).enabled_stores() == []


@pytest.mark.parametrize(
    "answer, expected",
    [("all", [1, 2, 3, 4, 5]), ("none", []), ("", []), ("1-3,5", [1, 2, 3, 5]),
     ("2", [2]), ("4-99", [4, 5])],
)
def test_parse_ranges(answer, expected):
    assert parse_ranges(answer, 5) == expected
