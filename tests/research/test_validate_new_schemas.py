import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = ["-m", "scieflow.research.validate"]

VALID_MANIFEST_YML = """\
package: 2026-07-demo
delivered: 2026-07-08
processing: processing.md
article: article/draft.md
artifacts:
  - id: tbl-metrics
    file: results/metrics.csv
    kind: table
    description: Per-model accuracy and F1 on the held-out split
    produced_by: scripts/analyze.py
"""

VALID_GAPS = {
    "agent": "claude",
    "topic": "test topic",
    "gaps": [
        {
            "id": "G1",
            "statement": "No published work links X to Y under condition Z",
            "evidence": [
                {"kind": "paper", "ref": "10.1234/abc"},
                {"kind": "data", "ref": "tbl-metrics"},
            ],
            "novelty_rationale": "No prior study combines both.",
        }
    ],
    "hypotheses": [
        {
            "id": "H1",
            "gap": "G1",
            "statement": "X causes Y under Z",
            "testable_prediction": "Y rises when X is present",
            "confidence": 3,
            "experiment": {
                "design": "controlled comparison",
                "data_needed": "20 samples per arm",
                "methods": "mixed-effects model",
                "feasibility_note": "data already collected",
            },
        }
    ],
}


def run(path, *args):
    return subprocess.run(
        [sys.executable, *SCRIPT, str(path), *args], capture_output=True, text=True
    )


def validate(path, schema_name, expect_fail=False):
    """Helper to validate a file against a schema.

    Returns 'OK' on success, or stdout (containing 'INVALID') on failure.
    When expect_fail=False, asserts on success.
    When expect_fail=True, returns stdout for inspection.
    """
    proc = run(path, "--schema", schema_name)
    if expect_fail:
        return proc.stdout
    else:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc.stdout.strip()


def test_valid_manifest_yaml_passes(tmp_path):
    f = tmp_path / "manifest.yml"
    f.write_text(VALID_MANIFEST_YML)
    proc = run(f, "--schema", "manifest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_empty_artifacts_manifest_passes(tmp_path):
    f = tmp_path / "manifest.yml"
    f.write_text(
        "package: p\ndelivered: 2026-07-08\nprocessing: processing.md\nartifacts: []\n"
    )
    proc = run(f, "--schema", "manifest")
    assert proc.returncode == 0


def test_manifest_bad_kind_fails(tmp_path):
    f = tmp_path / "manifest.yml"
    f.write_text(VALID_MANIFEST_YML.replace("kind: table", "kind: spreadsheet"))
    proc = run(f, "--schema", "manifest")
    assert proc.returncode == 1
    assert "INVALID" in proc.stdout


def test_manifest_bad_id_fails(tmp_path):
    f = tmp_path / "manifest.yml"
    f.write_text(VALID_MANIFEST_YML.replace("id: tbl-metrics", "id: Tbl Metrics"))
    proc = run(f, "--schema", "manifest")
    assert proc.returncode == 1


def test_broken_yaml_fails(tmp_path):
    f = tmp_path / "manifest.yml"
    f.write_text("package: [unclosed\n")
    proc = run(f, "--schema", "manifest")
    assert proc.returncode == 1


def test_valid_gaps_passes(tmp_path):
    f = tmp_path / "g.json"
    f.write_text(json.dumps(VALID_GAPS))
    proc = run(f, "--schema", "gaps")
    assert proc.returncode == 0


def test_gaps_empty_hypotheses_ok(tmp_path):
    doc = json.loads(json.dumps(VALID_GAPS))
    doc["hypotheses"] = []
    f = tmp_path / "g.json"
    f.write_text(json.dumps(doc))
    proc = run(f, "--schema", "gaps")
    assert proc.returncode == 0


def test_gaps_evidence_required(tmp_path):
    doc = json.loads(json.dumps(VALID_GAPS))
    doc["gaps"][0]["evidence"] = []
    f = tmp_path / "g.json"
    f.write_text(json.dumps(doc))
    proc = run(f, "--schema", "gaps")
    assert proc.returncode == 1


def test_gaps_bad_confidence_fails(tmp_path):
    doc = json.loads(json.dumps(VALID_GAPS))
    doc["hypotheses"][0]["confidence"] = 7
    f = tmp_path / "g.json"
    f.write_text(json.dumps(doc))
    proc = run(f, "--schema", "gaps")
    assert proc.returncode == 1


VALID_MANUSCRIPT_REVIEW = {
    "agent": "claude",
    "reviewed": "codex",
    "recommendation": "MAJOR REVISION",
    "major": [
        {
            "id": "M1",
            "location": "results.tex, para 2",
            "problem": "Accuracy claim has no source comment.",
            "why_it_matters": "Unverifiable central claim.",
            "resolution": "Cite [data:tbl-metrics] or drop the number.",
        }
    ],
    "minor": [{"id": "m1", "location": "intro.tex", "comment": "Define acronym."}],
    "summary": "Solid structure; the central Results claim is unsupported.",
}


def test_manuscript_review_valid(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(VALID_MANUSCRIPT_REVIEW))
    assert validate(f, "manuscript-review") == "OK"


def test_manuscript_review_rejects_bad_recommendation(tmp_path):
    bad = dict(VALID_MANUSCRIPT_REVIEW, recommendation="LOOKS FINE")
    f = tmp_path / "r.json"
    f.write_text(json.dumps(bad))
    out = validate(f, "manuscript-review", expect_fail=True)
    assert "INVALID" in out


def test_manuscript_review_rejects_bad_major_id(tmp_path):
    bad = dict(VALID_MANUSCRIPT_REVIEW)
    bad["major"] = [dict(bad["major"][0], id="X1")]
    f = tmp_path / "r.json"
    f.write_text(json.dumps(bad))
    out = validate(f, "manuscript-review", expect_fail=True)
    assert "INVALID" in out
