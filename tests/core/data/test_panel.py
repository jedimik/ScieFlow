"""The data panel of the run overview (M-B Part A 5.12). Synthetic data only.

The panel reads the run's declarations and the policy, never raises for an ordinary
state, and names paths and rule ids, never a value read from a data file.
"""

import yaml

from scieflow.core import service
from scieflow.core.data import cli as data_cli
from scieflow.core.project import Project

PANEL_KEYS = {"reason", "level", "enforcement", "declarations", "warnings", "released",
              "imports", "suggestion"}


def _declare_l2(ws):
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L0", "pending_releases": [], "released": [],
        "imports": [], "levels": [{"path": "inputs/x.csv", "level": "L2", "kind": "tabular",
                                   "reason": "synthetic", "declared_by": "human",
                                   "declared_at": "2026-10-26T10:00:00Z"}]}))


def _panel(project_root, slug="r1"):
    return service.run_overview(Project(project_root), slug)["data"]


def test_the_panel_has_the_5_12_keys(project):
    assert PANEL_KEYS <= set(_panel(project))


def test_a_run_without_declarations_is_l0_and_names_no_warning(project):
    panel = _panel(project)
    assert panel["level"] == "L0"
    assert panel["declarations"] == []
    assert panel["warnings"] == 0
    assert panel["suggestion"] is None


def test_an_l2_declaration_shows_as_l2(project, ws):
    _declare_l2(ws)
    panel = _panel(project)
    assert panel["level"] == "L2"
    assert [d["path"] for d in panel["declarations"]] == ["inputs/x.csv"]


def test_a_broken_data_file_is_l2_with_a_reason_and_no_exception(project, ws):
    (ws / "data.yml").write_text("levels: [\n")
    panel = _panel(project)
    assert panel["level"] == "L2"
    assert panel["reason"]


def test_a_non_mapping_release_item_is_l2_and_never_raises_in_the_panel(project, ws):
    # M-B review F1: released: [1, 2] crashed the panel with AttributeError.
    (ws / "data.yml").write_text(yaml.safe_dump({"version": 1, "released": [1, 2]}))
    panel = _panel(project)
    assert panel["level"] == "L2"
    assert panel["reason"]


def test_a_declaration_without_a_kind_defaults_to_other_in_the_panel(project, ws):
    # M-B final fix F1 (same class): kind is optional in the loader, so the panel must not index it.
    (ws / "data.yml").write_text(yaml.safe_dump({"version": 1, "levels": [{
        "path": "inputs/x.csv", "level": "L2", "reason": "synthetic", "declared_by": "human",
        "declared_at": "2026-10-26T10:00:00Z"}]}))
    panel = _panel(project)
    assert panel["level"] == "L2"
    assert panel["declarations"] == [{"path": "inputs/x.csv", "level": "L2", "kind": "other"}]


def test_an_l2_run_under_warn_enforcement_gets_the_refuse_suggestion(project, ws):
    _declare_l2(ws)
    panel = _panel(project)
    assert panel["enforcement"] == "warn"
    assert panel["suggestion"] and "enforcement: refuse" in panel["suggestion"]


def test_the_panel_suggestion_is_the_cli_suggestion():
    assert service._DATA_SUGGESTION == data_cli.SUGGESTION
