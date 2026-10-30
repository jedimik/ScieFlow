"""Helpers for the M-F t6 checklist tests (plan 5.10). Synthetic articles only; no patient data."""

from __future__ import annotations

import json

import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.manuscript import tex
from tests.research.manuscript.conftest import RUN, _evidence_for, make_profile

SECTION_INTRO = "Synthetic introduction."
QUOTE_INTRO = SECTION_INTRO


def article(figure_in_results: bool = False, registry_in_methods: bool = False,
            methods: bool = True) -> str:
    """A small invented RCT article: abstract, Introduction, optional Methods, Results, Discussion."""
    parts = [
        "\\documentclass[11pt]{article}\n\\begin{document}\n",
        "\\title{Synthetic trial of timing}\n",
        "\\begin{abstract}\nSynthetic abstract text.\n\\end{abstract}\n\\maketitle\n",
        "\\section{Introduction}\n" + SECTION_INTRO + "\n",
    ]
    if methods:
        suffix = " registered as NCT01234567" if registry_in_methods else ""
        parts.append("\\section{Methods}\nSynthetic methods text" + suffix + ".\n")
    parts.append("\\section{Results}\nSynthetic results text.\n")
    if figure_in_results:
        parts.append("\\begin{figure}\n\\includegraphics{flow.pdf}\n"
                     "\\caption{Synthetic flow.}\n\\end{figure}\n")
    parts.append("\\section{Discussion}\nSynthetic discussion text.\n\\end{document}\n")
    return "".join(parts)


def write_main(synth, text: str) -> None:
    (synth.ms / "main.tex").write_text(text, encoding="utf-8")


def write_design(synth, study_type: str = "rct", guidelines=("consort-2010",)) -> None:
    path = synth.run_dir / "design" / "design.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"study_type": study_type,
                                    "reporting_guidelines": list(guidelines)}), encoding="utf-8")


def profile_with_guidelines(synth, by_study_type: dict) -> None:
    data = make_profile()
    data["reporting_guidelines"] = {"by_study_type": by_study_type, "other": []}
    data["evidence"] = _evidence_for(data)
    synth.write_profile(data)


def verify_checklist(synth, gid: str = "consort-2010") -> None:
    path = synth.root / "config" / "reporting" / f"{gid}.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["verification"] = {"status": "verified", "note": "Checked by the synthetic test.",
                            "verified_by": "Test Reviewer", "verified_at": "2026-10-01"}
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def check_cli(*extra):
    return CliRunner().invoke(main, ["manuscript", "check", RUN, *extra], catch_exceptions=False)


def report_doc(synth) -> dict:
    path = synth.run_dir / "report" / "manuscript-check.json"
    return json.loads(path.read_text(encoding="utf-8"))


def checklist_file(synth, gid: str = "consort-2010"):
    return synth.run_dir / "report" / f"reporting-checklist-{gid}.md"


def guideline_ids(doc) -> list:
    return [record["id"] for record in doc["reporting"]]


def guideline_record(doc, gid: str = "consort-2010") -> dict:
    return next(record for record in doc["reporting"] if record["id"] == gid)


def item(doc, item_id: str, gid: str = "consort-2010") -> dict:
    return next(row for row in guideline_record(doc, gid)["items"] if row["id"] == item_id)


def finding_keys(doc) -> set:
    return {(f["rule"], f["severity"], f["where"]) for f in doc["findings"]}


def findings_of(doc, rule: str) -> list:
    return [f for f in doc["findings"] if f["rule"] == rule]


def judgement_path(synth, gid: str = "consort-2010"):
    return synth.run_dir / "report" / "checklist" / f"{gid}.judgement.json"


def write_judgement(synth, items: list, gid: str = "consort-2010", sha: str | None = None) -> None:
    path = judgement_path(synth, gid)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "guideline": gid,
                "manuscript_sha256": sha or tex.source_tree_hash(synth.ms), "items": items}
    path.write_text(json.dumps(document), encoding="utf-8")
