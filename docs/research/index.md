# ScieFlow research module

**Multi-agent scientific research framework.** Three AI agent CLIs — `claude`
(Claude Code), `codex` (OpenAI Codex CLI), and `agy` (Antigravity / Gemini) —
collaborate on research workflows through file-based coordination. Whichever
agent you talk to becomes the coordinator; it dispatches the others headless
and merges their work.

## What it does

<div class="grid cards" markdown>

- **Literature review**

    Ask any agent for a literature overview. All three agents search the same
    scholarly APIs (OpenAlex, arXiv, Europe PMC, Crossref), select papers with
    their own judgment, **peer-review each other's selections**, and the
    coordinator merges everything into a ranked report — consensus papers
    first, disputes documented. Top papers land in a Zotero collection with an
    exported `references.bib`.

- **Paper review**

    An iterative reviewer/submitter loop over your manuscript: one agent
    reviews like a journal referee, another applies the edits and writes a
    point-by-point response letter, repeat until *accept* or max rounds.
    Optionally targets a **specific journal** — the reviewer adopts that
    journal's scope, format limits, and review standards.

- **Gap discovery**

    Hand over your processed results as a [data package](data-packages.md)
    (plus, optionally, your draft article) and ask what's novel. Agents
    ground themselves in a literature search, independently propose research
    gaps and testable hypotheses with experiment designs, then **debate each
    other's viewpoints** — alternative framings included — before the
    coordinator ships a ranked report with recorded dissent.

- **Paper draft**

    A compilable **LaTeX article draft written from your data**: your
    processing description becomes the Methods "Data processing" subsection,
    every Results number carries a `% source: [data:<id>]` provenance
    comment, and the draft passes compile, citation-integrity, and
    provenance checks before handing off to the paper-review loop. You
    approve the outline before the expensive drafting starts.

</div>

## Why multiple agents?

A single model has blind spots — in what it retrieves, what it considers
important, and what it misses. The research module turns that into a feature:

- **Same sources, different judgment.** Every agent queries identical APIs, so
  differences in their paper selections reflect judgment, not access.
- **Cross-examination.** Each agent scores the others' picks, flags weak or
  off-topic papers, and names important papers the others missed.
- **Documented dissent.** The final report ranks consensus papers first and
  lists disputed papers with each side's argument — you see *where the models
  disagree*, which is often the most useful signal.

## Design principles

| Principle | Meaning |
| --- | --- |
| No orchestrator service | The intelligence lives in instruction files (`AGENTS.md`, `skills/*/SKILL.md`) any agent can read. Nothing to run, nothing to keep alive. |
| File-based coordination | Agents exchange work exclusively through files in `workspace/<slug>/`. Every intermediate artifact is inspectable and the run is resumable at any phase. |
| Schema-validated outputs | Agent findings and reviews are JSON validated against schemas; invalid output gets one retry with the errors fed back, then the run degrades gracefully. |
| Deterministic tooling | Search, validation, and Zotero export are plain Python scripts — testable, replicable, no LLM in the loop where none is needed. |
| Zero-token testing | A stub agent fakes every pipeline offline; 65 tests run without spending a single token or network call. |
| Evidence provenance | With a [data package](data-packages.md), every quantitative claim must trace to a searched DOI or a delivered artifact id — agents never invent numbers. |

## Quick start

```bash
git clone https://github.com/jedimik/ScieFlow
cd ScieFlow
setup/install.sh     # uv sync + checks for agent CLIs, zot, LaTeX
setup/doctor.sh      # verifies everything actually works
```

Then open any agent CLI in the repo and ask:

> "Do a literature review on protein structure prediction with diffusion models, 2022 onwards, max 30 papers."

See [Getting Started](getting-started.md) for setup details and
[Examples](examples.md) for full walkthroughs.
