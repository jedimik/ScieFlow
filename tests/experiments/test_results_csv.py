"""M-D2 t2: results.csv, the tidy table of every run x metric (plan 5.6)."""

import csv
import importlib.util
import json
from pathlib import Path

import yaml

from scieflow.experiments import tidy
from scieflow.experiments.campaign import Campaign
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep

_SUPPORT = Path(__file__).with_name("manifest_support.py")
_spec = importlib.util.spec_from_file_location("manifest_support", _SUPPORT)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

EXPECTED = (
    "campaign,config_id,run_id,replicate,seed,mode,status,metric,value,param.extra,param.flag,param.gain\n"
    "gain-sweep,000_gain-1.0,000_gain-1.0,,,direct,completed,ssim,1.0,,,1.0\n"
    "gain-sweep,000_gain-1.0,000_gain-1.0,,,direct,completed,mse,0.0,,,1.0\n"
    "gain-sweep,001_gain-0.5,001_gain-0.5_r0,0,7,direct,failed,ssim,,\"a,b\",,0.5\n"
    "gain-sweep,001_gain-0.5,001_gain-0.5_r0,0,7,direct,failed,mse,,\"a,b\",,0.5\n"
    "gain-sweep,002_gain-2.0,002_gain-2.0,,,direct,completed,ssim,inf,,true,2.0\n"
    "gain-sweep,002_gain-2.0,002_gain-2.0,,,direct,completed,mse,nan,,true,2.0\n"
)


def write_synthetic(camp: Path) -> None:
    camp.mkdir(parents=True)
    (camp / "campaign.yaml").write_text(yaml.safe_dump({"name": "gain-sweep", "metrics": ["ssim", "mse"]}))
    doc = {
        "campaign": "gain-sweep",
        "rank_by": "ssim",
        "mode": "direct",
        "runs": [
            {"run_id": "000_gain-1.0", "config_id": "000_gain-1.0", "replicate": None, "seed": None,
             "params": {"gain": 1.0}, "mode": "direct", "status": "completed",
             "metrics": {"ssim": 1.0, "mse": 0.0}},
            {"run_id": "001_gain-0.5_r0", "config_id": "001_gain-0.5", "replicate": 0, "seed": 7,
             "params": {"gain": 0.5, "extra": "a,b"}, "mode": "direct", "status": "failed",
             "metrics": {}},
            {"run_id": "002_gain-2.0", "config_id": "002_gain-2.0", "replicate": None, "seed": None,
             "params": {"gain": 2.0, "flag": True}, "mode": "direct", "status": "completed",
             "metrics": {"ssim": float("inf"), "mse": float("nan")}},
        ],
    }
    # json.dumps writes inf/nan as Infinity/NaN, exactly what a recorded campaign holds.
    (camp / "results.json").write_text(json.dumps(doc, indent=2))


def test_exact_text_for_a_synthetic_campaign(tmp_path):
    camp = tmp_path / "gain-sweep"
    write_synthetic(camp)
    path = tidy.write_results_csv(camp)
    assert path == camp / "results.csv"
    assert path.read_text(encoding="utf-8") == EXPECTED


def test_rewrite_is_byte_identical(tmp_path):
    camp = tmp_path / "gain-sweep"
    write_synthetic(camp)
    tidy.write_results_csv(camp)
    first = (camp / "results.csv").read_bytes()
    tidy.write_results_csv(camp)
    assert (camp / "results.csv").read_bytes() == first


def test_failed_run_keeps_its_rows_with_empty_value(tmp_path):
    camp = tmp_path / "gain-sweep"
    write_synthetic(camp)
    tidy.write_results_csv(camp)
    rows = list(csv.DictReader((camp / "results.csv").open(encoding="utf-8")))
    failed = [r for r in rows if r["run_id"] == "001_gain-0.5_r0"]
    assert [r["metric"] for r in failed] == ["ssim", "mse"]
    assert all(r["value"] == "" for r in failed)


def test_sweep_writes_results_csv(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    results = run_sweep(campaign, stage, project / "experiments", mode="direct")
    csv_path = results.parent / "results.csv"
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 2 * 2  # two runs x two metrics
    assert {r["run_id"] for r in rows} == {"000_gain-0.5", "001_gain-1.0"}
    assert {r["metric"] for r in rows} == {"ssim", "mse"}
    assert csv_path.read_text().splitlines()[0].startswith("campaign,config_id,run_id,")
    assert json.loads(results.read_text())["campaign"] == "gain-sweep"


def test_dict_and_list_parameters_are_compact_sorted_json(tmp_path):
    camp = tmp_path / "nested-param"
    camp.mkdir()
    doc = {"campaign": "nested-param", "rank_by": "m", "mode": "direct", "runs": [{
        "run_id": "000_a", "config_id": "000_a", "replicate": None, "seed": None,
        "params": {"opts": {"z": 1, "a": [2, 3]}, "lst": [1, "b"]}, "mode": "direct",
        "status": "completed", "metrics": {"m": 1.0},
    }]}
    (camp / "results.json").write_text(json.dumps(doc))
    tidy.write_results_csv(camp, ["m"])
    row = next(csv.DictReader((camp / "results.csv").open(encoding="utf-8")))
    assert row["param.opts"] == '{"a":[2,3],"z":1}'
    assert row["param.lst"] == '[1,"b"]'
