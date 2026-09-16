import pytest

from scieflow.news.config import Config, Interest, load_config
from scieflow.news.gui.interests import (
    create_example_config,
    parse_interest_form,
    remove_interest,
    upsert_interest,
)


def test_create_example_config_is_valid(tmp_path):
    path = tmp_path / "news.yml"
    create_example_config(path)
    config = load_config(path)
    assert len(config.interests) >= 1


def test_parse_interest_form_full():
    interest = parse_interest_form(
        name="  Snakemake ",
        context=" HPC pipelines ",
        repo=" snakemake/snakemake ",
        urls_text="https://a.example\n\n https://b.example \n",
        keywords_text=" workflow, bioinformatics ,",
    )
    assert interest == Interest(
        name="Snakemake",
        context="HPC pipelines",
        repo="snakemake/snakemake",
        urls=["https://a.example", "https://b.example"],
        keywords=["workflow", "bioinformatics"],
    )


def test_parse_interest_form_minimal():
    interest = parse_interest_form("DuckDB", "", "", "", "")
    assert interest == Interest(name="DuckDB")


def test_parse_interest_form_requires_name():
    with pytest.raises(ValueError, match="Name is required"):
        parse_interest_form("   ", "", "", "", "")


def test_parse_interest_form_template_and_lookback():
    interest = parse_interest_form("X", "", "", "", "", template="science", lookback_text="60")
    assert interest.template == "science" and interest.lookback_days == 60
    with pytest.raises(ValueError, match="unknown template"):
        parse_interest_form("X", "", "", "", "", template="nope")
    with pytest.raises(ValueError, match="positive integer"):
        parse_interest_form("X", "", "", "", "", lookback_text="zero")
    with pytest.raises(ValueError, match="positive integer"):
        parse_interest_form("X", "", "", "", "", lookback_text="0")


def test_upsert_add_and_duplicate():
    config = Config(interests=[Interest(name="A")])
    upsert_interest(config, Interest(name="B"), replacing=None)
    assert [i.name for i in config.interests] == ["A", "B"]
    with pytest.raises(ValueError, match="already exists"):
        upsert_interest(config, Interest(name="A"), replacing=None)


def test_upsert_edit_and_rename():
    config = Config(interests=[Interest(name="A"), Interest(name="B")])
    upsert_interest(config, Interest(name="A", context="ctx"), replacing="A")
    assert config.interests[0].context == "ctx"
    with pytest.raises(ValueError, match="already exists"):
        upsert_interest(config, Interest(name="B"), replacing="A")


def test_upsert_rejects_stale_rename():
    config = Config(interests=[Interest(name="A")])
    with pytest.raises(ValueError, match="no longer exists"):
        upsert_interest(config, Interest(name="Z"), replacing="Ghost")


def test_remove_interest_refuses_last():
    config = Config(interests=[Interest(name="A"), Interest(name="B")])
    remove_interest(config, "A")
    assert [i.name for i in config.interests] == ["B"]
    with pytest.raises(ValueError, match="last interest"):
        remove_interest(config, "B")
