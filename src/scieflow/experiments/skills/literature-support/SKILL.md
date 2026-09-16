---
name: literature-support
description: Ground parameter choices and anomalous results in scientific literature via the ScieFlow research module
---

# Literature Support

When the experiments module runs inside a ScieFlow research loop, the
coordinator handles literature (root `AGENTS.md` rule 6) and your prompt says
not to use this skill. Use it only for standalone experiment work.

## When to use
- Before a campaign: is there established guidance for this method's
  parameter ranges?
- After a campaign: does the observed behavior (e.g., SSIM optimum at a
  particular smoothing strength) match published results?
- When results look anomalous and you need precedent.

## Workflow

1. Formulate a precise query from the method + observation, e.g.
   "gaussian filter parameter selection image denoising SSIM".
2. Search only with the research module:
   `uv run scieflow research search openalex "<query>"` (also `arxiv`,
   `europepmc`, `crossref`). Prefer peer-reviewed or widely cited work.
3. Extract the *specific* claim that supports or contradicts the result —
   parameter ranges, expected metric behavior, known failure modes.
4. Cite in the campaign report (append to the `## Evaluation` section):
   author, year, title, DOI/arXiv id, and one sentence on relevance.

## Rules
- Never fabricate citations. If nothing relevant is found, say so.
- A citation supports a result; it never replaces validation on the data.
