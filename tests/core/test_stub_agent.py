import subprocess
import sys



def run_stub(prompt: str):
    return subprocess.run([sys.executable, "-m", "scieflow.core.stub_agent", prompt], capture_output=True, text=True)


def test_stub_writes_each_kind(tmp_path):
    for kind in ["hypothesis", "results-summary", "literature", "synthesis", "notebook-entry"]:
        out = tmp_path / f"{kind}.md"
        proc = run_stub(f"output: {out}\nkind: {kind}\n")
        assert proc.returncode == 0, proc.stderr
        assert out.read_text().strip()


def test_stub_rejects_missing_directives(tmp_path):
    proc = run_stub("no directives here")
    assert proc.returncode != 0


def test_stub_notebook_entry_has_required_sections(tmp_path):
    out = tmp_path / "entry.md"
    run_stub(f"output: {out}\nkind: notebook-entry\n")
    text = out.read_text()
    for section in ["Hypothesis", "Method", "Results", "Literature", "Conclusion", "Next step"]:
        assert f"### {section}" in text


def test_stub_writes_research_json_and_text_kinds(tmp_path):
    import json

    for kind in ["findings", "review", "gaps", "manuscript-review"]:
        out = tmp_path / f"{kind}.json"
        assert run_stub(f"output: {out}\nkind: {kind}\n").returncode == 0
        assert json.loads(out.read_text())["agent"] == "stub"
    for kind, marker in [("perspective", "P1."), ("debate-response", "AGREE"),
                         ("tex-section", "\\cite{stub}")]:
        out = tmp_path / f"{kind}.txt"
        assert run_stub(f"output: {out}\nkind: {kind}\n").returncode == 0
        assert marker in out.read_text()
