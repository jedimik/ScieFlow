"""M-H t6 (plan 5.9, 5.12, Part B t6 items 1, 3, 4): `sr prisma` numbers come only from the ledger.

The expected values are the hand-computed plan 5.12 numbers in tests/fixtures/sr/prisma-scenario.yml,
never derived from the code under test. Synthetic data only; no network, no agent, no patient data.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml
from click.testing import CliRunner

from scieflow.research.sr import cli as sr_cli
from tests.research.sr import t6_scenario as scn

GOLD = yaml.safe_load((scn.REPO / "tests" / "fixtures" / "sr" / "prisma-scenario.yml").read_text(encoding="utf-8"))
FLOW_KEYS = ("identified", "duplicates_removed", "records_after_dedup", "records_screened", "records_excluded",
             "reports_sought_for_retrieval", "reports_not_retrieved", "reports_assessed", "reports_excluded",
             "studies_included")


@pytest.fixture
def run(tmp_path, monkeypatch):
    """The plan 5.12 round-one scenario, with the CLI pointed at its project root."""
    proj, ws, box = scn.scenario(tmp_path, monkeypatch)
    monkeypatch.setattr(sr_cli, "_root", lambda: tmp_path)
    return tmp_path, ws


def _prisma(*args):
    return CliRunner().invoke(sr_cli.sr, ["prisma", scn.SLUG, *args])


def test_round_one_flow_equals_the_hand_numbers(run):
    root, ws = run
    result = _prisma("--json")
    assert result.exit_code == 0, result.output
    flow = json.loads(result.output)["flow"]
    for key in FLOW_KEYS:
        assert flow[key] == GOLD["round1"][key], key
    assert flow["identified_by_source"] == GOLD["round1"]["identified_by_source"]
    assert flow["reports_excluded_by_reason"] == GOLD["round1"]["reports_excluded_by_reason"]


def test_round_two_identification_is_cumulative(tmp_path, monkeypatch):
    scn.scenario(tmp_path, monkeypatch, round2=True)
    monkeypatch.setattr(sr_cli, "_root", lambda: tmp_path)
    flow = json.loads(_prisma("--json").output)["flow"]
    assert flow["identified"] == GOLD["round2"]["identified"]
    assert flow["duplicates_removed"] == GOLD["round2"]["duplicates_removed"]
    assert flow["records_after_dedup"] == GOLD["round2"]["records_after_dedup"]
    for key in ("records_screened", "records_excluded", "reports_sought_for_retrieval", "studies_included"):
        assert flow[key] == GOLD["round2"][key], key


def test_flow_md_mermaid_numbers_equal_the_json(run):
    root, ws = run
    assert _prisma("--write").exit_code == 0
    text = (ws / "sr" / "prisma" / "flow.md").read_text(encoding="utf-8")
    block = text.split("```mermaid", 1)[1].split("```", 1)[0]
    shown = [int(n) for n in re.findall(r"\(n=(\d+)\)", block)]
    assert shown == GOLD["mermaid_counts_round1"]
    flow = json.loads((ws / "sr" / "prisma" / "flow.json").read_text(encoding="utf-8"))["flow"]
    assert flow["identified"] == shown[0] and flow["duplicates_removed"] == shown[1]
    assert flow["records_after_dedup"] == shown[2] and flow["records_screened"] == shown[3]


def test_checklist_covers_every_item_and_derives_the_evidenced_ones(run):
    root, ws = run
    assert _prisma("--write").exit_code == 0
    checklist = (ws / "sr" / "prisma" / "checklist.md").read_text(encoding="utf-8")
    config = yaml.safe_load((root / "config" / "reporting" / "prisma-2020.yml").read_text(encoding="utf-8"))
    for item in config["items"]:
        assert f"| {item['id']} |" in checklist, item["id"]
    assert re.search(r"\| 7 \|[^\n]*\| derived \|", checklist)
    assert re.search(r"\| 16a \|[^\n]*\| derived \|", checklist)


def test_write_stamps_derived_from_and_nothing_is_read_back(run):
    root, ws = run
    assert _prisma("--write").exit_code == 0
    path = ws / "sr" / "prisma" / "flow.json"
    stamped = json.loads(path.read_text(encoding="utf-8"))
    assert set(stamped["derived_from"]) == {"events", "sha256", "at"}
    assert stamped["derived_from"]["events"] > 0
    # A hand-edited output file is not a source of counts: the next run still derives the hand numbers.
    stamped["flow"]["identified"] = 999
    path.write_text(json.dumps(stamped), encoding="utf-8")
    flow = json.loads(_prisma("--json").output)["flow"]
    assert flow["identified"] == GOLD["round1"]["identified"]


def test_prisma_refuses_an_invalid_ledger_with_the_named_rule(run):
    root, ws = run
    (ws / "sr" / "rounds" / "1" / "records.jsonl").write_text("tampered\n", encoding="utf-8")
    result = _prisma("--json")
    assert result.exit_code == 1
    assert "sr-ledger-invalid:" in result.output
