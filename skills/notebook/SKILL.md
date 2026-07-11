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
    <citations: author, year, title, DOI — with per-paper verdict>

    ### Conclusion
    <supported | contradicted | unexplained — one paragraph>

    ### Next step
    <the decision and why>

Validate before appending:
`uv run scripts/validate.py <entry-file> --schema notebook-entry`

## Paper handoff (only on explicit user request — never automatic)

1. Create a ResearchX workspace data package under
   `vendors/ResearchX/workspace/<paper-slug>/inputs/`:
   - `processing.md` — how the results were produced (pipelines, stages,
     parameters, expx campaign reports).
   - `results/` — the summary tables/figures selected from the ScieFlow run
     (copy them in; agents quote from delivered files only).
   - `manifest.yml` — per ResearchX `docs/data-packages.md`; one artifact id
     per claimable result. Validate ScieFlow-side first:
     `uv run scripts/validate.py <manifest.yml> --schema manifest`
2. Include `notebook.md` in the package as the narrative source.
3. Dispatch the paper-draft workflow:
   `uv run scripts/agent_run.py claude <prompt> <transcript> --cwd vendors/ResearchX`
   with a prompt instructing: follow `skills/paper-draft/SKILL.md` for the
   prepared workspace <paper-slug>.
4. Deliverables land in the ResearchX workspace; report their paths to the
   user.
