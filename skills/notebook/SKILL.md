---
name: notebook
description: Notebook entry format, validation, and the paper-draft handoff
---

# Notebook

`workspace/<slug>/notebook.md` is the run's cumulative research record —
the input to a future paper draft. One entry per iteration.

## Entry format (validated)

    ## Iteration <n> — <short title>

    ### Hypothesis
    <what was tested and why>

    ### Method
    <campaign name, pipeline, grid/scenarios, run ids>

    ### Results
    <key metrics with [run:<id>] provenance>

    ### Literature
    <citations: author, year, title, DOI — with per-paper verdict; a verdict
    checked via skills/claim-check/SKILL.md carries its quote and locator>

    ### Conclusion
    <supported | contradicted | unexplained — one paragraph>

    ### Next step
    <the decision and why>

A DOI alone does not show the source says the thing. To check that, offer
`skills/claim-check/SKILL.md` (opt-in, AGENTS.md rule 12) — never run it
unasked; a checked verdict then carries its quote and locator into the entry.

Validate before appending:
`uv run scripts/validate.py <entry-file> --schema notebook-entry`

## Paper handoff (only on explicit user request — never automatic)

1. Create a research-module data package in its own run workspace,
   `workspace/<paper-slug>/inputs/`:
   - `processing.md` — how the results were produced (pipelines, stages,
     parameters, experiment campaign reports).
   - `results/` — the summary tables/figures selected from the ScieFlow run
     (copy them in; agents quote from delivered files only).
   - `manifest.yml` — per `docs/research/data-packages.md`; one artifact id
     per claimable result. Validate ScieFlow-side first:
     `uv run scripts/validate.py <manifest.yml> --schema manifest`
2. Include `notebook.md` in the package as the narrative source.
3. Dispatch the paper-draft workflow:
   `uv run scieflow agent run <agent> <prompt> <transcript>` — `<agent>` is
   the agent assigned to `loop.paper-draft` (`uv run scieflow agent show --workspace <slug> --json`, AGENTS.md rule 13)
   with a prompt instructing: read only `src/scieflow/research/AGENTS.md`,
   then follow `src/scieflow/research/skills/paper-draft/SKILL.md` for the
   prepared workspace <paper-slug>.
4. Deliverables land in `workspace/<paper-slug>/`; report their paths to the
   user.
