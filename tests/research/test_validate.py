import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = ["-m", "scieflow.research.validate"]

VALID_FINDINGS = {
    "agent": "claude",
    "topic": "test topic",
    "papers": [
        {
            "doi": "10.1/x",
            "title": "A Paper",
            "year": 2024,
            "relevance": {"score": 4, "why": "on topic"},
        }
    ],
}

VALID_REVIEW = {
    "agent": "codex",
    "reviewed": "claude",
    "per_paper": [{"title": "A Paper", "score": 4, "verdict": "strong", "comment": "good"}],
    "missing": [],
    "summary": "solid",
}


def run(path, *args):
    return subprocess.run(
        [sys.executable, *SCRIPT, str(path), *args], capture_output=True, text=True
    )


def test_valid_findings_pass(tmp_path):
    f = tmp_path / "f.json"
    f.write_text(json.dumps(VALID_FINDINGS))
    proc = run(f)
    assert proc.returncode == 0
    assert "OK" in proc.stdout


def test_bad_score_fails(tmp_path):
    bad = json.loads(json.dumps(VALID_FINDINGS))
    bad["papers"][0]["relevance"]["score"] = 9
    f = tmp_path / "f.json"
    f.write_text(json.dumps(bad))
    proc = run(f)
    assert proc.returncode == 1
    assert "INVALID" in proc.stdout


def test_broken_json_fails(tmp_path):
    f = tmp_path / "f.json"
    f.write_text("{not json")
    proc = run(f)
    assert proc.returncode == 1


def test_valid_review_passes(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(VALID_REVIEW))
    proc = run(f, "--schema", "review")
    assert proc.returncode == 0


def test_review_bad_verdict_fails(tmp_path):
    bad = json.loads(json.dumps(VALID_REVIEW))
    bad["per_paper"][0]["verdict"] = "meh"
    f = tmp_path / "r.json"
    f.write_text(json.dumps(bad))
    proc = run(f, "--schema", "review")
    assert proc.returncode == 1
