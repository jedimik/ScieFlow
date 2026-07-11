import status
import validate


def test_valid_status_passes():
    st = status.new_status("run-a", "autonomous")
    assert validate.validate_status(st) == []


def test_bad_status_reports_errors():
    st = status.new_status("run-a", "autonomous")
    st["phases"]["experiment"] = "sideways"
    del st["iteration"]
    errors = validate.validate_status(st)
    assert any("iteration" in e for e in errors)


def test_notebook_entry_requires_all_sections():
    good = (
        "## Iteration 3 — refine sigma\n\n### Hypothesis\nx\n\n### Method\nx\n\n"
        "### Results\nx\n\n### Literature\nx\n\n### Conclusion\nx\n\n### Next step\nx\n"
    )
    assert validate.validate_notebook_entry(good) == []
    errors = validate.validate_notebook_entry("## Iteration 3\n\n### Hypothesis\nx\n")
    assert any("Results" in e for e in errors)
    assert any("Next step" in e for e in errors)


def test_manifest_validation():
    good = {
        "package": "2026-07-denoise", "delivered": "2026-07-11",
        "processing": "processing.md",
        "artifacts": [{"id": "tbl-1", "file": "results/m.csv",
                       "kind": "table", "description": "metrics"}],
    }
    assert validate.validate_manifest(good) == []
    bad = {"package": "x", "artifacts": [{"id": "a", "kind": "wat"}]}
    errors = validate.validate_manifest(bad)
    assert any("delivered" in e for e in errors)
    assert any("file" in e for e in errors)
    assert any("kind" in e for e in errors)
