"""The run page shows the data level in the overview band (M-B Part A 5.12). Synthetic data only."""

import yaml

from scieflow.core import service


def _band(page):
    start = page.text.index('id="ov-data"')
    return page.text[start:start + 1200]


def test_the_run_page_shows_the_level_of_a_declared_run(client, project):
    ws = project.run_dir("r1")
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L0", "pending_releases": [], "released": [],
        "imports": [], "levels": [{"path": "inputs/x.csv", "level": "L2", "kind": "tabular",
                                   "reason": "synthetic", "declared_by": "human",
                                   "declared_at": "2026-10-26T10:00:00Z"}]}))
    page = client.get("/runs/r1")
    assert page.status_code == 200
    band = _band(page)
    assert "L2" in band
    assert "safe agents only" in band


def test_an_undeclared_run_says_l0_and_not_safe_agents_only(client, project):
    page = client.get("/runs/r1")
    band = _band(page)
    assert "L0" in band
    assert "safe agents only" not in band


def test_a_failed_data_panel_says_level_unknown_not_a_blank(client, project, monkeypatch):
    def broken(ws):
        raise RuntimeError("synthetic panel failure")

    monkeypatch.setitem(service._PANELS, "data", broken)
    page = client.get("/runs/r1")
    assert page.status_code == 200
    band = _band(page)
    assert "Level unknown" in band
    assert "Warnings: unknown" in band
    assert "Level ." not in band
