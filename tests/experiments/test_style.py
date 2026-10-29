"""M-D2 t5: the figure sizing contract (plan 5.7, schemas/figure-style.yml). Synthetic only."""

import importlib.util
from pathlib import Path

import pytest
import yaml

_HERE = Path(__file__)
REPO = _HERE.parents[2]


def _style():
    from scieflow.experiments import style

    return style


def test_defaults_are_the_contract_defaults(tmp_path):
    style = _style()
    assert style.load_style(None) == {
        "single_column_mm": 89, "double_column_mm": 183, "font_pt": 8, "dpi": 300,
        "formats": ["png", "svg"],
    }


def test_style_file_overrides_only_the_keys_it_states(tmp_path):
    style = _style()
    path = tmp_path / "style.yml"
    path.write_text(yaml.safe_dump({"figures": {"dpi": 150, "formats": ["pdf"]}}))
    got = style.load_style(path)
    assert got["dpi"] == 150 and got["formats"] == ["pdf"]
    assert got["single_column_mm"] == 89 and got["font_pt"] == 8


@pytest.mark.parametrize("key, value", [
    ("single_column_mm", 29), ("single_column_mm", 201), ("single_column_mm", True),
    ("double_column_mm", 59), ("double_column_mm", 301),
    ("font_pt", 4), ("font_pt", 15),
    ("dpi", 71), ("dpi", 1201), ("dpi", 300.5),
    ("formats", []), ("formats", ["tiff"]), ("formats", "png"), ("formats", ["png", "png", 1]),
])
def test_out_of_range_or_wrong_type_names_the_key(tmp_path, key, value):
    style = _style()
    path = tmp_path / "style.yml"
    path.write_text(yaml.safe_dump({"figures": {key: value}}))
    with pytest.raises(style.StyleError, match=key):
        style.load_style(path)


def test_unknown_key_names_the_key(tmp_path):
    style = _style()
    path = tmp_path / "style.yml"
    path.write_text(yaml.safe_dump({"figures": {"colour": "red"}}))
    with pytest.raises(style.StyleError, match="colour"):
        style.load_style(path)


def test_unknown_top_level_key_is_refused(tmp_path):
    style = _style()
    path = tmp_path / "style.yml"
    path.write_text(yaml.safe_dump({"figures": {"dpi": 150}, "journal": "x"}))
    with pytest.raises(style.StyleError, match="journal"):
        style.load_style(path)


def test_figures_block_must_be_a_mapping(tmp_path):
    style = _style()
    path = tmp_path / "style.yml"
    path.write_text(yaml.safe_dump({"figures": ["dpi", 150]}))
    with pytest.raises(style.StyleError, match="figures"):
        style.load_style(path)


def test_symlinked_style_file_is_refused(tmp_path):
    style = _style()
    target = tmp_path / "real.yml"
    target.write_text(yaml.safe_dump({"figures": {"dpi": 150}}))
    link = tmp_path / "link.yml"
    link.symlink_to(target)
    with pytest.raises(style.StyleError, match="symlink"):
        style.load_style(link)


def test_missing_style_file_is_refused(tmp_path):
    style = _style()
    with pytest.raises(style.StyleError, match="missing.yml"):
        style.load_style(tmp_path / "missing.yml")


def test_constants_are_pinned_to_the_schema():
    style = _style()
    schema_path = REPO / "schemas" / "figure-style.yml"
    assert schema_path.exists(), "schemas/figure-style.yml is the documented contract"
    schema = yaml.safe_load(schema_path.read_text())["figures"]
    for key in ("single_column_mm", "double_column_mm", "font_pt", "dpi"):
        spec = schema[key]
        assert style.DEFAULTS[key] == spec["default"], key
        assert style.RANGES[key] == (spec["min"], spec["max"]), key
    assert style.DEFAULTS["formats"] == schema["formats"]["default"]
    assert list(style.ALLOWED_FORMATS) == schema["formats"]["allowed"]
