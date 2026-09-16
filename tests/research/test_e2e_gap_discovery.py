"""Dry-run the gap-discovery mechanical pipeline with 3 stub agents.
No tokens, no network. Judgment phases are represented by their file
contracts, mirroring tests/test_e2e_stub.py."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS = ["stub_a", "stub_b", "stub_c"]

MANIFEST = """\
package: 2026-07-e2e-gaps
delivered: 2026-07-08
processing: processing.md
artifacts:
  - id: tbl-metrics
    file: results/metrics.csv
    kind: table
    description: stub metrics table
"""


def sh(argv, cwd):
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd)
    assert proc.returncode == 0, f"{argv}: {proc.stdout}{proc.stderr}"
    return proc


def make_root(tmp_path: Path):
    stub = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"
    agents_yml = "agents:\n" + "".join(
        f'  {a}: {{cmd: "{stub}", enabled: true, timeout_min: 1}}\n' for a in AGENTS
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(agents_yml)
    ws = tmp_path / "workspace" / "2026-07-e2e-gaps"
    for d in ("inputs/results", "prompts", "logs", "findings", "gaps",
              "debate/round-0", "debate/round-1", "report"):
        (ws / d).mkdir(parents=True)
    return tmp_path, ws


def test_gap_discovery_pipeline(tmp_path):
    root, ws = make_root(tmp_path)
    rel = ws.relative_to(root)

    # Phase 1: intake — manifest + processing description, validated
    (ws / "inputs" / "results" / "metrics.csv").write_text("model,acc\nstub,0.9\n")
    (ws / "inputs" / "processing.md").write_text("Processed by stub pipeline.\n")
    (ws / "inputs" / "manifest.yml").write_text(MANIFEST)
    sh([sys.executable, "-m", "scieflow.research.validate",
        str(ws / "inputs" / "manifest.yml"), "--schema", "manifest"], cwd=root)

    # Phase 2: literature grounding fan-out (findings kind, no cross-review)
    for a in AGENTS:
        prompt = ws / "prompts" / f"search-{a}.md"
        prompt.write_text(f"search task\noutput: {rel}/findings/{a}.json\nkind: findings\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", a, str(prompt),
            str(ws / "logs" / f"search-{a}.log")], cwd=root)
        sh([sys.executable, "-m", "scieflow.research.validate",
            str(ws / "findings" / f"{a}.json")], cwd=root)

    # Phase 3: gap-analysis fan-out
    for a in AGENTS:
        prompt = ws / "prompts" / f"gaps-{a}.md"
        prompt.write_text(f"gap task\noutput: {rel}/gaps/{a}.json\nkind: gaps\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", a, str(prompt),
            str(ws / "logs" / f"gaps-{a}.log")], cwd=root)
        sh([sys.executable, "-m", "scieflow.research.validate",
            str(ws / "gaps" / f"{a}.json"), "--schema", "gaps"], cwd=root)

    # Phase 4: debate — round 0 propose + round 1 discuss, then adjudication
    for a in AGENTS:
        p0 = ws / "prompts" / f"debate-propose-{a}.md"
        p0.write_text(f"propose\noutput: {rel}/debate/round-0/{a}.md\nkind: perspective\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", a, str(p0),
            str(ws / "logs" / f"debate-0-{a}.log")], cwd=root)
        p1 = ws / "prompts" / f"debate-round-1-{a}.md"
        p1.write_text(f"discuss\noutput: {rel}/debate/round-1/{a}.md\nkind: debate-response\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", a, str(p1),
            str(ws / "logs" / f"debate-1-{a}.log")], cwd=root)
    assert len(list((ws / "debate" / "round-0").glob("*.md"))) == 3
    assert len(list((ws / "debate" / "round-1").glob("*.md"))) == 3
    (ws / "debate" / "round-1" / "adjudication.md").write_text(
        "| item | status | note |\n|---|---|---|\n| stub_a/P1 | consensus | |\n"
    )

    # Phase 5: synthesize file contract
    merged = json.loads((ws / "gaps" / "stub_a.json").read_text())
    merged["agent"] = "consensus"
    (ws / "report" / "hypotheses.json").write_text(json.dumps(merged, indent=2))
    sh([sys.executable, "-m", "scieflow.research.validate",
        str(ws / "report" / "hypotheses.json"), "--schema", "gaps"], cwd=root)
    (ws / "report" / "gaps.md").write_text("# Gap Report: stub\n")
    (ws / "report" / "selected_dois.txt").write_text("10.0000/stub.1\n")
