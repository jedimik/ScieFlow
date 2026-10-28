"""M-D1 t2: reporting checklists under config/reporting/ and their validator (plan section 5.3).

The shipped checklists must equal the approved skeleton item for item. The corruption tests
build their own small tree from the skeleton, so each rule is checked against a known-good base.
Synthetic data only; these files describe published reporting guidelines, no patient data.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core.design import cli as design_cli
from scieflow.core.design import reporting

REPO = Path(__file__).resolve().parents[3]
SKELETON = REPO / "tests" / "fixtures" / "reporting" / "skeletons.yml"
SKELETON_SHA256 = "756f1a957bb8b1cc5c43b559d5cf541579d1014fba3c935b84e1cf87f45f52eb"
NOTE = ("Item ids and labels were written offline from knowledge (M-D1 planning). "
        "Check them against the official checklist (source.doi) before relying on them.")
IDS = ["consort-2010", "strobe", "stard-2015", "tripod-ai", "prisma-2020",
       "spirit-2013", "arrive-2.0", "cheers-2022"]
COUNTS = {"consort-2010": 37, "strobe": 34, "stard-2015": 34, "tripod-ai": 27,
          "prisma-2020": 42, "spirit-2013": 33, "arrive-2.0": 21, "cheers-2022": 28}


def _skeleton_guidelines():
    return yaml.safe_load(SKELETON.read_text())["guidelines"]


def _item_text(item):
    q = json.dumps
    parts = [f"id: {q(item['id'])}", f"topic: {q(item['topic'])}",
             f"sections: {q(item['sections'])}", f"evidence: {q(item['evidence'])}"]
    if "group" in item:
        parts.append(f"group: {q(item['group'])}")
    return "  - {" + ", ".join(parts) + "}"


def _checklist_text(g):
    """Plan section 5.3 layout. JSON-quoted scalars are valid YAML, so values are copied exactly."""
    q = json.dumps
    lines = [
        "version: 1",
        f"id: {q(g['id'])}",
        f"name: {q(g['name'])}",
        f"version_label: {q(g['version'])}",
        f"applies_to: {q(g['applies_to'])}",
        f"granularity: {q(g['granularity'])}",
        f"source: {{citation: {q(g['source']['citation'])}, doi: {q(g['source']['doi'])}}}",
        f"verification: {{status: \"unverified\", note: {q(NOTE)}, verified_by: null, verified_at: null}}",
        "items:",
    ]
    lines += [_item_text(item) for item in g["items"]]
    return "\n".join(lines) + "\n"


def _write_tree(root: Path) -> None:
    """A known-good checklist tree (index, one file per guideline, the schema) under root."""
    (root / "config" / "reporting").mkdir(parents=True)
    (root / "schemas").mkdir()
    shutil.copy(REPO / "schemas" / "reporting-checklist.yml", root / "schemas")
    shutil.copy(REPO / "schemas" / "design.yml", root / "schemas")  # t3: study types come from here
    guidelines = _skeleton_guidelines()
    index = f"version: 1\nguidelines: {json.dumps([g['id'] for g in guidelines])}\n"
    (root / "config" / "reporting" / "index.yml").write_text(index)
    for g in guidelines:
        (root / "config" / "reporting" / f"{g['id']}.yml").write_text(_checklist_text(g))


def _edit(root: Path, gid: str, mutate) -> None:
    path = root / "config" / "reporting" / f"{gid}.yml"
    data = yaml.safe_load(path.read_text())
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _rules(root: Path) -> set[str]:
    return {problem.rule for problem in reporting.validate_all(root)}


# --- the shipped files -------------------------------------------------------------------

def test_skeleton_fixture_is_the_approved_input():
    assert hashlib.sha256(SKELETON.read_bytes()).hexdigest() == SKELETON_SHA256


def test_shipped_checklists_validate():
    assert reporting.validate_all(REPO) == []


def test_index_lists_the_eight_in_skeleton_order():
    index = yaml.safe_load((REPO / "config" / "reporting" / "index.yml").read_text())
    assert index == {"version": 1, "guidelines": IDS}


def test_registry_lists_the_eight_in_skeleton_order():
    assert [c.id for c in reporting.registry(REPO)] == IDS


@pytest.mark.parametrize("gid", IDS)
def test_item_count_matches_skeleton(gid):
    assert len(reporting.get(REPO, gid).items) == COUNTS[gid]


def test_every_shipped_file_equals_its_skeleton_entry():
    guidelines = _skeleton_guidelines()
    assert [g["id"] for g in guidelines] == IDS
    for g in guidelines:
        data = yaml.safe_load((REPO / "config" / "reporting" / f"{g['id']}.yml").read_text())
        assert set(data) == {"version", "id", "name", "version_label", "applies_to",
                             "granularity", "source", "verification", "items"}, g["id"]
        assert data["version"] == 1
        assert data["version_label"] == g["version"], g["id"]
        for key in ("id", "name", "applies_to", "granularity", "source"):
            assert data[key] == g[key], (g["id"], key)
        assert data["items"] == g["items"], g["id"]


def test_every_shipped_file_is_unverified_with_the_plan_note():
    for checklist in reporting.registry(REPO):
        assert checklist.verification == {"status": "unverified", "note": NOTE,
                                          "verified_by": None, "verified_at": None}, checklist.id


# --- the validator on a known-good base ---------------------------------------------------

def test_a_known_good_tree_validates(tmp_path):
    _write_tree(tmp_path)
    assert reporting.validate_all(tmp_path) == []


def test_verified_with_reviewer_and_date_is_accepted(tmp_path):
    _write_tree(tmp_path)
    _edit(tmp_path, "strobe", lambda d: d["verification"].update(
        status="verified", verified_by="reviewer", verified_at="2026-11-01"))
    assert reporting.validate_all(tmp_path) == []


def _orphan(root):
    shutil.copy(root / "config/reporting/strobe.yml", root / "config/reporting/extra.yml")


def _listed_without_file(root):
    (root / "config/reporting/strobe.yml").unlink()


def _symlinked(root):
    path = root / "config/reporting/strobe.yml"
    path.unlink()
    path.symlink_to(root / "config/reporting/tripod-ai.yml")


def _unreadable(root):
    (root / "config/reporting/strobe.yml").write_text("items: [\n")


def _index_unreadable(root):
    (root / "config/reporting/index.yml").write_text("guidelines: [\n")


CORRUPTIONS = [
    ("orphan-file", _orphan),
    ("missing-file", _listed_without_file),
    ("symlink", _symlinked),
    ("unreadable", _unreadable),
    ("index-unreadable", _index_unreadable),
    ("id-mismatch", lambda r: _edit(r, "strobe", lambda d: d.__setitem__("id", "stard-2015"))),
    ("duplicate-item-id", lambda r: _edit(r, "consort-2010",
                                          lambda d: d["items"].append(dict(d["items"][0])))),
    ("enum", lambda r: _edit(r, "strobe", lambda d: d["items"][0].__setitem__("sections", ["nowhere"]))),
    ("enum", lambda r: _edit(r, "strobe", lambda d: d["items"][0].__setitem__("evidence", "bogus"))),
    ("enum", lambda r: _edit(r, "strobe", lambda d: d["items"][0].__setitem__("group", "optional"))),
    ("topic", lambda r: _edit(r, "strobe", lambda d: d["items"][0].__setitem__("topic", ""))),
    ("topic", lambda r: _edit(r, "strobe", lambda d: d["items"][0].__setitem__("topic", "x" * 61))),
    ("verification", lambda r: _edit(r, "strobe", lambda d: d["verification"].update(status="verified"))),
    ("doi", lambda r: _edit(r, "strobe", lambda d: d["source"].__setitem__("doi", "not-a-doi"))),
    ("unknown-key", lambda r: _edit(r, "strobe", lambda d: d.__setitem__("extra", 1))),
    ("missing-key", lambda r: _edit(r, "strobe", lambda d: d.pop("name"))),
    ("applies-to", lambda r: _edit(r, "strobe", lambda d: d.__setitem__("applies_to", ["martian"]))),
    ("empty-items", lambda r: _edit(r, "strobe", lambda d: d.__setitem__("items", []))),
]


@pytest.mark.parametrize("rule, corrupt", CORRUPTIONS, ids=[f"{i}-{r}" for i, (r, _) in enumerate(CORRUPTIONS)])
def test_each_corruption_is_reported_with_its_rule(tmp_path, rule, corrupt):
    _write_tree(tmp_path)
    corrupt(tmp_path)
    assert rule in _rules(tmp_path)


@pytest.mark.parametrize("rule, corrupt", CORRUPTIONS, ids=[f"{i}-{r}" for i, (r, _) in enumerate(CORRUPTIONS)])
def test_loading_fails_closed_on_each_corruption(tmp_path, rule, corrupt):
    _write_tree(tmp_path)
    corrupt(tmp_path)
    with pytest.raises(reporting.ReportingError):
        reporting.registry(tmp_path)


# --- the `design guidelines` command ------------------------------------------------------

def test_design_group_is_listed_in_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "design" in result.output


def test_guidelines_json_lists_the_eight_unverified():
    result = CliRunner().invoke(main, ["design", "guidelines", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [row["id"] for row in rows] == IDS
    assert {row["id"]: row["items"] for row in rows} == COUNTS
    assert {row["verification"] for row in rows} == {"unverified"}


def test_guidelines_check_passes_on_the_shipped_files():
    result = CliRunner().invoke(main, ["design", "guidelines", "--check"])
    assert result.exit_code == 0, result.output


def test_guidelines_check_reports_a_corrupted_copy(tmp_path, monkeypatch):
    _write_tree(tmp_path)
    _edit(tmp_path, "strobe", lambda d: d["items"][0].__setitem__("sections", ["nowhere"]))
    monkeypatch.setattr(design_cli, "_root", lambda: tmp_path)
    result = CliRunner().invoke(main, ["design", "guidelines", "--check"])
    assert result.exit_code == 1, result.output
    assert "[enum]" in result.output


def test_design_group_loads_without_optional_extras():
    code = ("import sys, scieflow.core.design.cli; "
            "heavy = [m for m in ('scipy', 'jsonschema', 'statsmodels') if m in sys.modules]; "
            "assert not heavy, heavy")
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                          text=True, env=dict(os.environ))
    assert done.returncode == 0, done.stderr
