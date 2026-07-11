#!/usr/bin/env python3
"""Fake agent for tests/dry-runs. Reads 'output:' and 'kind:' lines from the
prompt and writes canned markdown there. Zero tokens, zero network.
Pattern borrowed from ResearchX scripts/stub_agent.py."""

import re
import sys
from pathlib import Path

CANNED = {
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


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    out = re.search(r"^output:\s*(\S+)", prompt, re.M)
    kind = re.search(r"^kind:\s*(\S+)", prompt, re.M)
    if not out or not kind or kind.group(1) not in CANNED:
        sys.exit(
            "stub: prompt is missing 'output: <path>' / 'kind: "
            + "|".join(sorted(CANNED)) + "' lines"
        )
    path = Path(out.group(1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CANNED[kind.group(1)])
    print(f"stub: wrote {path}")


if __name__ == "__main__":
    main()
