"""Dry-run the paper-draft mechanical pipeline with stub agents: two
independent full drafts, adversarial cross-review, coordinator merge,
citation verification, compile if latexmk exists."""

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AUTHORS = ["stub_a", "stub_b"]
SECTIONS = ["abstract", "introduction", "methods", "results", "discussion"]

MANIFEST = """\
package: 2026-07-e2e-draft
delivered: 2026-07-08
processing: processing.md
artifacts:
  - id: tbl-metrics
    file: results/metrics.csv
    kind: table
    description: stub metrics table
"""

BIB = """\
@article{stub,
  author = {Stub, A.},
  title = {Stub Paper One},
  journal = {Journal of Stubs},
  year = {2024},
  doi = {10.0000/stub.1},
}
"""


def sh(argv, cwd):
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd)
    assert proc.returncode == 0, f"{argv}: {proc.stdout}{proc.stderr}"
    return proc


def test_paper_draft_pipeline(tmp_path):
    stub = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"
    agents_yml = "agents:\n" + "".join(
        f'  {a}: {{cmd: "{stub}", enabled: true, timeout_min: 1, tier: primary}}\n'
        for a in AUTHORS
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(agents_yml)
    ws = tmp_path / "workspace" / "2026-07-e2e-draft"
    for d in ("inputs/results", "prompts", "logs", "outline",
              "manuscript/sections", "report"):
        (ws / d).mkdir(parents=True)
    rel = ws.relative_to(tmp_path)

    # Phase 1: intake
    (ws / "inputs" / "results" / "metrics.csv").write_text("model,acc\nstub,0.9\n")
    (ws / "inputs" / "processing.md").write_text("Processed by stub pipeline.\n")
    (ws / "inputs" / "manifest.yml").write_text(MANIFEST)
    sh([sys.executable, "-m", "scieflow.research.validate",
        str(ws / "inputs" / "manifest.yml"), "--schema", "manifest"], cwd=tmp_path)
    (ws / "manuscript" / "references.bib").write_text(BIB)
    (ws / "report" / "selected_dois.txt").write_text("10.0000/stub.1\n")

    # Phase 2 contract: outline exists (judgment content stubbed)
    (ws / "outline" / "outline.md").write_text("- Results: acc 0.9 [data:tbl-metrics]\n")

    # Phase 3: two full independent drafts (stub: one dispatch per section)
    for agent in AUTHORS:
        for section in SECTIONS:
            prompt = ws / "prompts" / f"draft-{agent}-{section}.md"
            prompt.write_text(
                f"draft\noutput: {rel}/manuscript/drafts/{agent}/{section}.tex\n"
                "kind: tex-section\n"
            )
            sh([sys.executable, "-m", "scieflow.core.agent_run", agent,
                str(prompt), str(ws / "logs" / f"draft-{agent}-{section}.log")],
               cwd=tmp_path)
        for section in SECTIONS:
            assert (ws / "manuscript" / "drafts" / agent / f"{section}.tex"
                    ).stat().st_size > 0

    # Phase 4: adversarial cross-review (each author reviews the other)
    (ws / "review" / "draft-round-1").mkdir(parents=True)
    for reviewer, author in [("stub_a", "stub_b"), ("stub_b", "stub_a")]:
        prompt = ws / "prompts" / f"xreview-{reviewer}-round-1.md"
        out = f"{rel}/review/draft-round-1/{reviewer}-on-{author}.json"
        prompt.write_text(f"review\noutput: {out}\nkind: manuscript-review\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", reviewer,
            str(prompt), str(ws / "logs" / f"xreview-{reviewer}.log")],
           cwd=tmp_path)
        sh([sys.executable, "-m", "scieflow.research.validate",
            str(ws / "review" / "draft-round-1" / f"{reviewer}-on-{author}.json"),
            "--schema", "manuscript-review"], cwd=tmp_path)

    # Phase 5: merge (coordinator judgment stubbed: take stub_a wholesale)
    for section in SECTIONS:
        src = ws / "manuscript" / "drafts" / "stub_a" / f"{section}.tex"
        (ws / "manuscript" / "sections" / f"{section}.tex").write_text(
            src.read_text())
    (ws / "report" / "merge_log.md").write_text(
        "| section | chosen | rationale |\n|---|---|---|\n"
        + "".join(f"| {s} | stub_a | stub pick |\n" for s in SECTIONS))

    # Phase 5 (assemble): populate main.tex/preamble.tex from the real template
    tpl = REPO / "src" / "scieflow" / "research" / "templates" / "paper"
    main = (tpl / "main.tex").read_text()
    main = (main.replace("%%TITLE%%", "Stub Title")
                .replace("%%AUTHORS%%", "Stub Author")
                .replace("%%DATE%%", "2026-07-08"))
    (ws / "manuscript" / "main.tex").write_text(main)
    (ws / "manuscript" / "preamble.tex").write_text((tpl / "preamble.tex").read_text())

    # Phase 6a: citation integrity
    sh([sys.executable, "-m", "scieflow.research.citations",
        "--workspace", str(ws)], cwd=tmp_path)

    # Phase 6b: compile when the toolchain exists
    if shutil.which("latexmk"):
        sh(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error",
            "main.tex"], cwd=ws / "manuscript")
        assert (ws / "manuscript" / "main.pdf").exists()
