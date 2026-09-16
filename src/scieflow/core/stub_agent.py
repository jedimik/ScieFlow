"""Fake agent for tests and dry runs. Zero tokens, zero network.

Reads `output: <path>` and `kind: <kind>` lines from the prompt and writes a
canned artifact there: markdown for research-loop phases, schema-valid JSON
for research-module workflows, and text/TeX for debate and drafting steps.
"""

import json
import re
import sys
from pathlib import Path

# Research loop phases (hypothesize, experiment, literature, synthesize, notebook).
CANNED_MARKDOWN = {
    "hypothesis": (
        "# Hypothesis — stub\n\n"
        "Gaussian sigma near 1.5 maximizes SSIM on the noisy test image.\n\n"
        "Experiment intent: sweep sigma 0.5-3.0 on pipelines/denoise, rank by SSIM.\n"
    ),
    "results-summary": (
        "# Results summary — stub\n\n"
        "Campaign: stub-sweep (pipeline denoise). Runs: 6, failed: 0.\n\n"
        "| sigma | ssim |\n|---|---|\n| 1.5 | 0.91 |\n| 2.0 | 0.88 |\n\n"
        "Best: sigma=1.5 [run:stub_0001]. Anomalies: none.\n"
    ),
    "literature": (
        "# Literature — stub\n\n"
        "- Doe 2021, *Gaussian smoothing for image denoising*, DOI 10.0000/stub.2 — "
        "reports SSIM optimum at moderate sigma. Verdict: supports.\n"
    ),
    "synthesis": (
        "# Synthesis — stub\n\n"
        "Verdict: supported.\n"
        "Next: refine sigma grid 1.2-1.8.\n"
        "Decision: continue\n"
    ),
    "notebook-entry": (
        "## Iteration 1 — Gaussian sigma sweep\n\n"
        "### Hypothesis\nSigma near 1.5 maximizes SSIM.\n\n"
        "### Method\nCampaign stub-sweep, pipeline denoise, runs stub_0001-stub_0006.\n\n"
        "### Results\nBest SSIM 0.91 at sigma=1.5 [run:stub_0001].\n\n"
        "### Literature\nDoe 2021, DOI 10.0000/stub.2 — supports moderate-sigma optimum.\n\n"
        "### Conclusion\nSupported.\n\n"
        "### Next step\nRefine sigma grid 1.2-1.8.\n"
    ),
}

# Research module JSON artifacts (validated by `scieflow research validate`).
CANNED_JSON = {
    "findings": {
        "agent": "stub",
        "topic": "stub topic",
        "papers": [
            {
                "doi": "10.0000/stub.1",
                "title": "Stub Paper One",
                "year": 2024,
                "venue": "Journal of Stubs",
                "cited_by": 42,
                "abstract": "A canned abstract.",
                "url": None,
                "source": "stub",
                "relevance": {"score": 4, "why": "Directly on topic."},
            }
        ],
    },
    "review": {
        "agent": "stub",
        "reviewed": "other",
        "per_paper": [
            {
                "doi": "10.0000/stub.1",
                "title": "Stub Paper One",
                "score": 4,
                "verdict": "strong",
                "comment": "Solid pick.",
            }
        ],
        "missing": [],
        "summary": "Good coverage overall.",
    },
}

CANNED_JSON["gaps"] = {
    "agent": "stub",
    "topic": "stub topic",
    "gaps": [
        {
            "id": "G1",
            "statement": "No study links stub factor X to outcome Y.",
            "evidence": [
                {"kind": "paper", "ref": "10.0000/stub.1"},
                {"kind": "data", "ref": "tbl-metrics"},
            ],
            "novelty_rationale": "Unexplored combination.",
        }
    ],
    "hypotheses": [
        {
            "id": "H1",
            "gap": "G1",
            "statement": "X drives Y in the stub regime.",
            "testable_prediction": "Y increases with X.",
            "confidence": 3,
            "experiment": {
                "design": "two-arm comparison",
                "data_needed": "existing tbl-metrics split",
                "methods": "paired t-test",
                "feasibility_note": "runs on delivered data",
            },
        }
    ],
}

CANNED_JSON["manuscript-review"] = {
    "agent": "stub",
    "reviewed": "stub-other",
    "recommendation": "MINOR REVISION",
    "major": [
        {
            "id": "M1",
            "location": "results.tex, para 1",
            "problem": "Stub finds the accuracy claim under-explained.",
            "why_it_matters": "A demanding reader cannot verify it.",
            "resolution": "Add the derivation and cite [data:tbl-metrics].",
        }
    ],
    "minor": [
        {"id": "m1", "location": "introduction.tex",
         "comment": "Stub wants the acronym defined."}
    ],
    "summary": "Stub cross-review: sound draft, one unsupported claim.",
}

CANNED_TEXT = {
    "perspective": (
        "# Perspectives — stub\n\n"
        "P1. The observed effect may be a preprocessing artifact. "
        "Evidence: [data:tbl-metrics]. To settle: recompute without step 2 "
        "of scripts/analyze.py.\n"
    ),
    "debate-response": (
        "# Debate response — stub\n\n"
        "P1: AGREE — consistent with [data:tbl-metrics].\n"
    ),
    "tex-section": (
        "\\section{Stub Section}\n"
        "Stub prose citing \\cite{stub}. % source: [data:tbl-metrics]\n"
    ),
}


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    out = re.search(r"^output:\s*(\S+)", prompt, re.M)
    kind = re.search(r"^kind:\s*(\S+)", prompt, re.M)
    known = set(CANNED_MARKDOWN) | set(CANNED_JSON) | set(CANNED_TEXT)
    if not out or not kind or kind.group(1) not in known:
        sys.exit(
            "stub: prompt is missing 'output: <path>' / 'kind: "
            + "|".join(sorted(known)) + "' lines"
        )
    path = Path(out.group(1))
    path.parent.mkdir(parents=True, exist_ok=True)
    k = kind.group(1)
    if k in CANNED_JSON:
        path.write_text(json.dumps(CANNED_JSON[k], indent=2))
    elif k in CANNED_TEXT:
        path.write_text(CANNED_TEXT[k])
    else:
        path.write_text(CANNED_MARKDOWN[k])
    print(f"stub: wrote {path}")


if __name__ == "__main__":
    main()
