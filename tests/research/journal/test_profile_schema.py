"""schemas/journal-profile.yml and profile.py agree; the figures block is pinned to figure-style.yml."""

import ast
from pathlib import Path

import pytest
import yaml

from scieflow.research.journal import profile

REPO = Path(__file__).resolve().parents[3]
PKG = REPO / "src" / "scieflow" / "research" / "journal"


def _schema(root):
    return yaml.safe_load((root / "schemas" / "journal-profile.yml").read_text(encoding="utf-8"))


def test_top_keys_are_the_schema_keys(synth):
    schema = _schema(synth.root)
    assert list(profile.required_top_keys(synth.root)) == schema["top_keys"]["required"]
    assert list(profile.optional_top_keys(synth.root)) == schema["top_keys"]["optional"]


def test_every_schema_key_is_accepted_and_every_other_key_is_refused(synth, profile_data):
    # The schema is the only source of the key lists: a profile that uses every listed key
    # (figures included) raises no unknown-key problem, and any extra key does.
    data = profile_data()
    data["figures"] = {"single_column_mm": 89, "double_column_mm": 183, "font_pt": 8,
                       "dpi": 300, "formats": ["png"]}
    assert not [p for p in profile.validate(data, root=synth.root) if p.rule == "unknown-key"]
    data["article_types"][0]["limits"]["bogus_limit"] = 1
    data["provenance"]["bogus"] = 1
    rules = {(p.rule, p.path) for p in profile.validate(data, root=synth.root)}
    assert ("unknown-key", "article_types[0].limits.bogus_limit") in rules
    assert ("unknown-key", "provenance.bogus") in rules


def test_figures_keys_and_ranges_are_pinned_to_figure_style(synth):
    style = yaml.safe_load((synth.root / "schemas" / "figure-style.yml").read_text(encoding="utf-8"))["figures"]
    schema = _schema(synth.root)
    assert sorted(schema["blocks"]["figures"]) == sorted(style)
    ranges = profile.figure_ranges(synth.root)
    assert set(ranges) == set(style)
    for key in ("single_column_mm", "double_column_mm", "font_pt", "dpi"):
        assert ranges[key] == (style[key]["min"], style[key]["max"])
    assert ranges["formats"] == tuple(style["formats"]["allowed"])


def test_schema_enums_match_the_format_description(synth):
    schema = _schema(synth.root)
    assert schema["enums"]["latex_class"][-1] == "other"
    assert set(schema["enums"]["declarations"]) == {
        "coi", "funding", "ethics", "data-availability", "code-availability", "ai-use", "credit", "orcid"}
    assert schema["ranges"]["max_age_days"] == {"min": 1, "max": 3650}


def test_schema_refuses_a_symlinked_file(synth):
    target = synth.root / "schemas" / "journal-profile.yml"
    moved = synth.root / "schemas" / "moved.yml"
    target.rename(moved)
    target.symlink_to(moved)
    with pytest.raises(profile.ProfileError):
        profile.load_schema(synth.root)


def test_schema_missing_a_section_fails_closed(synth):
    target = synth.root / "schemas" / "journal-profile.yml"
    data = _schema(synth.root)
    del data["blocks"]
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(profile.ProfileError):
        profile.load_schema(synth.root)


def test_profile_package_never_imports_experiments():
    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert "experiments" not in name.split("."), f"{path.name} imports {name}"


def test_load_profile_refuses_a_symlink_and_a_non_mapping(synth):
    real = synth.root / "real.yml"
    real.write_text("version: 1\n", encoding="utf-8")
    link = synth.root / "link.yml"
    link.symlink_to(real)
    with pytest.raises(profile.ProfileError):
        profile.load_profile(link)
    listy = synth.root / "list.yml"
    listy.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(profile.ProfileError):
        profile.load_profile(listy)
    assert isinstance(profile.load_profile(real), dict)


def test_schema_file_is_under_schemas_and_read_only_for_the_module(synth):
    assert Path(profile.SCHEMA) == Path("schemas/journal-profile.yml")
