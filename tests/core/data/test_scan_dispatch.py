"""M-B t4, Part A 5.4 and 5.6: the pre-dispatch scan (CLI path), warn and refuse modes, the
dedupe of warnings, and `scieflow data scan`.

Synthetic data only. Every agent is an inert `python -c` command; nothing reaches a real service.
"""

import json
import sys

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import agent_run, events, jobs
from scieflow.core.data import policy
from scieflow.core.data.cli import data
from scieflow.core.project import Project

PY = sys.executable
CLOUD_CMD = f'{PY} -c "print(1)" {{prompt}}'
SAFE_CMD = f'{PY} -c "import sys; print(sys.argv[1:])" --endpoint {{endpoint}} {{prompt}}'
EP = "https://llm.example.org/v1"
PLANT = "900101/1234"   # a synthetic birth-number shape; must never reach an output or event
REDCAP_CSV = "record_id,redcap_event_name,score\n1,baseline,4\n"
AGENTS = {
    "cloud-a": {"cmd": CLOUD_CMD, "enabled": True, "timeout_min": 1},
    "safe-a": {"cmd": SAFE_CMD, "enabled": True, "timeout_min": 1},
}


def write_yaml(path, data_):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data_, sort_keys=False))


def write_policy(root, *, enforcement="warn", scan=None):
    text = {"version": 1, "enforcement": enforcement,
            "safe_endpoints": [{"url": EP, "reason": "test"}],
            "safe_agents": {"safe-a": {"endpoint": EP, "reason": "test",
                                       "templates_sha256": policy.pin_sha(AGENTS["safe-a"])}}}
    if scan is not None:
        text["scan"] = scan
    write_yaml(root / "config" / "data-policy.yml", text)


@pytest.fixture
def lab(project, monkeypatch):
    write_yaml(project / "config" / "agents.yml", {"agents": AGENTS})
    ws = project / "workspace" / "r1"
    write_yaml(ws / "config.yml", {"slug": "r1", "approval": "autonomous", "assignments": {
        "loop.experiment": ["cloud-a", "safe-a"]}})
    prompt = ws / "logs" / "prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("synthetic prompt, no real data\n")
    (ws / "inputs").mkdir()
    (ws / "inputs" / "export.csv").write_text(REDCAP_CSV)
    monkeypatch.setattr(Project, "discover", lambda *a, **k: Project(project))
    monkeypatch.chdir(project)
    return project


def dispatch(root, agent, *extra):
    ws = root / "workspace" / "r1"
    argv = [agent, *extra, str(ws / "logs" / "prompt.md"), str(ws / "logs" / "t.md"),
            "--role", "loop.experiment"]
    return agent_run.main(argv)


def warnings(ws):
    return [(e["data"]["rule"], e["data"]["path"])
            for e in events.read(ws, types=("data.warning",))]


def refused(ws):
    return [(e["data"]["reason"], e["data"]["rule"], e["data"]["level"], e["data"]["agent"],
             e["data"]["boundary"]) for e in events.read(ws, types=("job.refused",))]


def ws_of(root):
    return root / "workspace" / "r1"


# --- warn mode ----------------------------------------------------------------------------


def test_warn_mode_dispatches_and_records_one_warning(lab):
    dispatch(lab, "cloud-a", "--no-sandbox")
    assert warnings(ws_of(lab)) == [("redcap-export", "inputs/export.csv")]
    assert jobs.list_jobs(Project(lab), ws_of(lab))


def test_a_second_dispatch_repeats_no_warning(lab):
    dispatch(lab, "cloud-a", "--no-sandbox")
    dispatch(lab, "cloud-a", "--no-sandbox")
    assert warnings(ws_of(lab)) == [("redcap-export", "inputs/export.csv")]


def test_a_changed_file_is_warned_again(lab):
    dispatch(lab, "cloud-a", "--no-sandbox")
    (ws_of(lab) / "inputs" / "export.csv").write_text(REDCAP_CSV + "2,followup,5\n")
    dispatch(lab, "cloud-a", "--no-sandbox")
    assert len(warnings(ws_of(lab))) == 2


def test_warn_mode_truncated_scan_records_scan_incomplete(lab):
    write_policy(lab, scan={"max_files": 1})
    dispatch(lab, "cloud-a", "--no-sandbox")
    assert ("scan-incomplete", ".") in warnings(ws_of(lab))


def test_a_safe_agent_is_not_scanned(lab):
    write_policy(lab)
    dispatch(lab, "safe-a", "--no-sandbox")
    assert warnings(ws_of(lab)) == []


def test_values_never_reach_events_or_the_dedupe_file(lab):
    (ws_of(lab) / "inputs" / "contact.csv").write_text(f"contact,score\n{PLANT},4\n")
    dispatch(lab, "cloud-a", "--no-sandbox")
    assert ("identifier-values", "inputs/contact.csv") in warnings(ws_of(lab))
    ws = ws_of(lab)
    for path in (ws / "events.jsonl", ws / "scratch" / "data-scan-reported.json"):
        assert PLANT not in path.read_text()


def test_the_dedupe_file_lives_in_scratch(lab):
    dispatch(lab, "cloud-a", "--no-sandbox")
    dedupe = ws_of(lab) / "scratch" / "data-scan-reported.json"
    assert dedupe.is_file()
    assert isinstance(json.loads(dedupe.read_text()), dict)


# --- refuse mode --------------------------------------------------------------------------


def test_refuse_mode_refuses_with_scan_warning_and_exit_78(lab):
    write_policy(lab, enforcement="refuse")
    with pytest.raises(SystemExit) as exc:
        dispatch(lab, "cloud-a", "--no-sandbox")
    assert exc.value.code == 78
    assert refused(ws_of(lab)) == [("data-level", "scan-warning", "L0", "cloud-a", "cloud")]
    assert jobs.list_jobs(Project(lab), ws_of(lab)) == []
    assert "scan-warning" in (ws_of(lab) / "logs" / "t.md").read_text()


def test_refuse_mode_does_not_trust_an_earlier_warn_dedupe(lab):
    dispatch(lab, "cloud-a", "--no-sandbox")          # warn mode writes the dedupe file
    write_policy(lab, enforcement="refuse")
    with pytest.raises(SystemExit) as exc:
        dispatch(lab, "cloud-a", "--no-sandbox")
    assert exc.value.code == 78
    assert refused(ws_of(lab))[-1][1] == "scan-warning"


def test_refuse_mode_truncated_scan_refuses_with_scan_incomplete(lab):
    write_policy(lab, enforcement="refuse", scan={"max_files": 1})
    (ws_of(lab) / "inputs" / "export.csv").write_text("record_id,score\n1,4\n")
    with pytest.raises(SystemExit) as exc:
        dispatch(lab, "cloud-a", "--no-sandbox")
    assert exc.value.code == 78
    assert refused(ws_of(lab)) == [("data-level", "scan-incomplete", "L0", "cloud-a", "cloud")]


def test_refuse_mode_a_safe_agent_is_still_not_scanned(lab):
    write_policy(lab, enforcement="refuse")
    dispatch(lab, "safe-a", "--no-sandbox")
    assert refused(ws_of(lab)) == []


# --- scieflow data scan -------------------------------------------------------------------


def test_data_scan_lists_findings_without_values(lab):
    (ws_of(lab) / "inputs" / "contact.csv").write_text(f"contact,score\n{PLANT},4\n")
    result = CliRunner().invoke(data, ["scan", "r1"])
    assert result.exit_code == 0, result.output
    assert "redcap-export" in result.output and "inputs/export.csv" in result.output
    assert "identifier-values" in result.output
    assert PLANT not in result.output


def test_data_scan_json_has_path_rule_and_detail(lab):
    result = CliRunner().invoke(data, ["scan", "r1", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {"path", "rule", "detail"} <= set(payload["findings"][0])
    assert payload["truncated"] is False


def test_data_scan_output_never_holds_a_headerless_value(lab):
    # M-B t4 review F1 and F6(c): the planted value sits in the header row of a headerless file.
    (ws_of(lab) / "inputs" / "contact.csv").write_text(f"{PLANT},4\n910202/5678,5\n")
    as_json = CliRunner().invoke(data, ["scan", "r1", "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert "identifier-values" in as_json.output
    assert PLANT not in as_json.output
    as_text = CliRunner().invoke(data, ["scan", "r1"])
    assert as_text.exit_code == 0, as_text.output
    assert PLANT not in as_text.output


def test_data_scan_in_a_refuse_project_does_not_trust_the_dedupe_file(lab):
    # M-B t4 review N1: under refuse the dedupe file is never trusted, a manual scan included.
    first = CliRunner().invoke(data, ["scan", "r1"])
    assert first.exit_code == 0, first.output
    assert len(warnings(ws_of(lab))) == 1
    write_policy(lab, enforcement="refuse")
    second = CliRunner().invoke(data, ["scan", "r1"])
    assert second.exit_code == 0, second.output
    assert len(warnings(ws_of(lab))) == 2


def test_data_scan_records_warnings_and_is_allowed_inside_a_dispatch(lab, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    result = CliRunner().invoke(data, ["scan", "r1"])
    assert result.exit_code == 0, result.output
    assert warnings(ws_of(lab)) == [("redcap-export", "inputs/export.csv")]


def test_data_scan_writes_no_policy_or_data_file(lab):
    write_policy(lab)
    before = (lab / "config" / "data-policy.yml").read_bytes()
    data_yml = ws_of(lab) / "data.yml"
    CliRunner().invoke(data, ["scan", "r1"])
    assert (lab / "config" / "data-policy.yml").read_bytes() == before
    assert not data_yml.exists()
