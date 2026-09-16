import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run_stub(tmp_path, kind, out_name):
    out = tmp_path / out_name
    prompt = f"task\noutput: {out}\nkind: {kind}\n"
    proc = subprocess.run(
        [sys.executable, "-m", "scieflow.core.stub_agent", prompt], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return out


def test_gaps_kind_is_schema_valid(tmp_path):
    out = run_stub(tmp_path, "gaps", "g.json")
    proc = subprocess.run(
        [sys.executable, "-m", "scieflow.research.validate", str(out), "--schema", "gaps"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout


def test_perspective_kind_writes_numbered_items(tmp_path):
    out = run_stub(tmp_path, "perspective", "p.md")
    assert "P1." in out.read_text()


def test_debate_response_kind_has_verdict(tmp_path):
    out = run_stub(tmp_path, "debate-response", "r.md")
    assert "AGREE" in out.read_text()


def test_tex_section_kind_cites_and_sources(tmp_path):
    out = run_stub(tmp_path, "tex-section", "s.tex")
    text = out.read_text()
    assert "\\cite{stub}" in text
    assert "% source: [data:tbl-metrics]" in text


def test_findings_kind_still_works(tmp_path):
    out = run_stub(tmp_path, "findings", "f.json")
    assert json.loads(out.read_text())["agent"] == "stub"


def test_manuscript_review_kind_is_schema_valid(tmp_path):
    out = run_stub(tmp_path, "manuscript-review", "mr.json")
    proc = subprocess.run(
        [sys.executable, "-m", "scieflow.research.validate", str(out), "--schema", "manuscript-review"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout
