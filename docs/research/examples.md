# Examples

Realistic walkthroughs. You drive the ScieFlow research module in natural language through any
agent CLI; the commands shown are what the framework runs under the hood, so
you can also reproduce any step by hand.

---

## Example 1 — A literature review, start to finish

**You (to `claude`, `codex`, or `agy` at the repo root):**

> Do a literature overview on graph neural networks for molecular property
> prediction. Focus on message-passing architectures and benchmark datasets,
> 2021 onwards, max 30 papers. Save citations to my Zotero group 4815162.

**What happens:**

### Phase 1 — Brief

The coordinator creates the workspace and normalizes your request:

```text
workspace/2026-07-gnn-molecular-property/
├── brief.md          # research questions, scope, timeframe, max_papers
├── config.yml        # agents, max_papers: 30, zotero: {library: group:4815162}
└── status.yml        # phase tracking
```

### Phase 2 — Search fan-out

Each agent independently derives queries and runs the shared search scripts —
same sources for everyone:

```bash
uv run scieflow research search openalex "graph neural network molecular property" --limit 25 --from-year 2021
uv run scieflow research search arxiv "message passing neural network molecules" --limit 25
uv run scieflow research search europepmc "GNN molecular property prediction" --limit 25
```

Each writes a schema-validated findings file:

```json title="workspace/.../findings/claude.json"
{
  "agent": "claude",
  "topic": "GNNs for molecular property prediction",
  "papers": [
    {
      "doi": "10.48550/arXiv.2110.01234",
      "title": "Directed Message Passing for Molecular Graphs",
      "year": 2021,
      "venue": "arXiv",
      "cited_by": 412,
      "abstract": "...",
      "url": "http://arxiv.org/pdf/2110.01234",
      "source": "arxiv",
      "relevance": {"score": 5, "why": "Seminal D-MPNN architecture for the exact task."}
    }
  ]
}
```

### Phase 3 — Cross-review

Every agent reviews the others' findings, scoring each paper and flagging
gaps:

```json title="workspace/.../reviews/codex-on-claude.json"
{
  "agent": "codex",
  "reviewed": "claude",
  "per_paper": [
    {"doi": "10.48550/arXiv.2110.01234", "title": "Directed Message Passing...",
     "score": 5, "verdict": "strong", "comment": "Correctly central; well chosen."}
  ],
  "missing": [
    {"doi": "10.1021/acs.jcim.xxxxx", "title": "MoleculeNet benchmark",
     "why": "The standard benchmark suite; any review of this area needs it."}
  ],
  "summary": "Strong architecture coverage but light on benchmark/dataset papers."
}
```

### Phase 4 — Synthesis

The coordinator merges findings and reviews into the deliverables:

```text
workspace/2026-07-gnn-molecular-property/report/
├── overview.md        # exec summary, consensus papers ranked, disputed papers with dissent, gaps
├── matrix.md          # paper × method / data / limitation comparison table
└── selected_dois.txt  # DOIs to export (no-DOI papers listed under a trailing "# no-doi" comment)
```

`overview.md` reads like:

```markdown
## Consensus papers
### 1. Directed Message Passing for Molecular Graphs (2021, arXiv)
- DOI: 10.48550/arXiv.2110.01234 · Citations: 412
- Why it matters: introduces D-MPNN, the reference architecture cited across the field.
- Agent scores: claude 5 · codex 5 · agy 4

## Disputed papers
### "Graph Transformers Are Overkill for Molecules" — agy says 4, codex says 2
- agy: useful negative result on when attention doesn't help.
- codex: methodology underpowered; small datasets only.
```

### Phase 5 — Export

```bash
uv run scieflow research zotero-export --workspace workspace/2026-07-gnn-molecular-property
```

Top papers are pushed into Zotero collection `ScieFlow/2026-07-gnn-molecular-property`
inside **group 4815162** (per your request), and `report/references.bib` is
written into the workspace.

!!! tip "Resume a run"
    If a phase is interrupted, ask the coordinator to continue — it reads
    `status.yml` and picks up at the first unfinished phase. Nothing re-runs
    unnecessarily.

---

## Example 2 — Manuscript review targeting a journal

**You (to any agent):**

> Review my manuscript in `~/papers/gnn-admet/` as a reviewer for the *Journal
> of Chemical Information and Modeling*. Up to 3 revision rounds.

**What happens:**

1. **Journal profiling.** If not already cached, an agent researches the
   journal's author guidelines and writes:

    ```text
    config/journals/journal-of-chemical-information-and-modeling.md
    ```

    with scope, article types & length limits, formatting rules, review
    criteria, and common rejection reasons. Cached profiles are reused across
    future runs.

2. **Round loop** (reviewer ≠ submitter, so no agent grades its own edits):

    ```text
    workspace/2026-07-gnn-admet-review/
    ├── manuscript/            # working copy of your paper (edited in place)
    ├── journal/profile.md     # the journal persona for this run
    └── review/
        ├── round-1/review.md      # major/minor comments + recommendation line
        ├── round-1/response.md    # point-by-point response; edits applied to manuscript
        ├── round-2/review.md      # reviewer verifies round-1 promises were kept
        └── round-2/response.md
    ```

    Each `review.md` ends with a machine-parsed line:

    ```text
    ## Recommendation
    MAJOR REVISION
    ```

    The loop stops on `ACCEPT` or after 3 rounds. The coordinator verifies the
    manuscript files actually changed each round before trusting the response
    letter.

3. **Final report.** You get the recommendation, rounds used, what changed per
   round, and any remaining open comments.

---

## Example 3 — Just profile a journal

**You:**

> I'm aiming for *Nature Methods* — how does it accept papers?

No review loop runs. You get `config/journals/nature-methods.md`: scope,
article types with length limits, formatting requirements, what its reviewers
weight most, common rejection reasons, and a submission checklist — with the
guideline URLs cited. Reused automatically if you later run a full review
targeting that journal.

---

## Example 4 — Data → gaps → hypotheses

**You (

Drop a `config.yml` into a workspace to override the global defaults for that
research only:

```yaml title="workspace/2026-07-my-topic/config.yml"
agents: [claude, agy]         # skip codex for this run
max_papers: 20
zotero:
  library: "group:4815162"    # this topic saves to a specific group...
  collection: "ScieFlow/GNN survey"
```

A different research can target a **different** Zotero group — the resolution
order is workspace `config.yml` → global `config/agents.yml` → your personal
library. See [Configuration](configuration.md).

---

## Example 5 — Discover gaps and hypotheses from your data

**You (to any agent):**

> Find research gaps for my ADMET prediction study. Results are in
> `~/study/out/` — `metrics.csv` is per-model AUROC (5-fold CV, produced by
> `analyze.py`, which I'm including). Reuse my earlier lit-review in
> `workspace/2026-07-gnn-admet`. Use all three agents.

**What happens:**

1. **Intake.** The coordinator builds a [data package](data-packages.md),
   confirming the manifest with you:

    ```yaml title="workspace/2026-07-admet-gaps/inputs/manifest.yml"
    package: 2026-07-admet-gaps
    delivered: 2026-07-08
    processing: processing.md
    artifacts:
      - id: tbl-auroc
        file: results/metrics.csv
        kind: table
        description: per-model AUROC, 5-fold CV
        produced_by: scripts/analyze.py
    ```

2. **Literature grounding.** `literature_from:` reuses the earlier
   lit-review's findings instead of searching again.

3. **Gap analysis fan-out.** Each agent writes `gaps/<agent>.json` — gaps and
   hypotheses, every one carrying an evidence trail:

    ```json title="workspace/.../gaps/claude.json"
    {
      "agent": "claude", "topic": "ADMET prediction gaps",
      "gaps": [{"id": "G1",
        "statement": "No model reports calibration on the low-solubility tail.",
        "evidence": [{"kind": "data", "ref": "tbl-auroc"},
                     {"kind": "paper", "ref": "10.1021/acs.jcim.xxxxx"}],
        "novelty_rationale": "AUROC hides tail miscalibration."}],
      "hypotheses": [{"id": "H1", "gap": "G1",
        "statement": "Temperature scaling improves tail calibration without AUROC loss.",
        "testable_prediction": "ECE drops >30% on the low-solubility decile.",
        "confidence": 4,
        "experiment": {"design": "post-hoc recalibration on the held-out split",
          "data_needed": "per-sample logits (you have these)",
          "methods": "temperature scaling; ECE by decile",
          "feasibility_note": "runs on the delivered predictions"}}]
    }
    ```

4. **Perspective debate.** Agents challenge framings and each other's gaps
   (see [Perspective Debate](debate.md)); unresolved disagreement is recorded.

5. **Synthesize.** You get:

    ```text
    workspace/2026-07-admet-gaps/report/
    ├── gaps.md            # framings, gaps, ranked hypotheses w/ experiment designs, recorded dissent
    ├── hypotheses.json    # merged, feeds paper-draft
    └── selected_dois.txt
    ```

Read `## Recorded dissent` first — the disputed items are usually the
interesting ones. Then: *"draft a paper from hypothesis H1."*

---

## Example 6 — Draft a LaTeX article from your data

**You (to any agent):**

> Draft the article for hypothesis H1 from that gap-discovery run. Target
> *PLOS Computational Biology*. Same data package.

```yaml title="workspace/2026-07-admet-draft/config.yml"
gaps_from: workspace/2026-07-admet-gaps
journal: PLOS Computational Biology
title: "Tail calibration of ADMET models via temperature scaling"
authors: "T. Krajca"
```

**What happens:**

1. **Intake.** `gaps_from:` imports the hypotheses, gap report, and DOI list;
   the DOIs are exported to `manuscript/references.bib`. `journal:` reuses the
   PLOS CompBio profile.

2. **Outline + approval gate.** One agent outlines every section as
   claims-with-evidence; two others critique it in a one-round perspective
   pass. The coordinator **shows you the outline and waits** — drafting is the
   expensive step.

3. **Section drafting fan-out.** Agents draft `manuscript/sections/*.tex`.
   Every Results number carries a provenance comment; Methods contains a
   "Data processing" subsection built from your `processing.md`:

    ```latex title="workspace/.../manuscript/sections/results.tex"
    Temperature scaling reduced decile ECE by 34\% with no AUROC change.
    % source: [data:tbl-auroc]
    ```

4. **Assemble + verify.** The coordinator fills the LaTeX skeleton and runs
   three gates:

    ```bash
    latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex   # compiles
    uv run scieflow research check-citations --workspace workspace/2026-07-admet-draft
    # + a provenance spot-check: sampled Results numbers must exist in their cited artifact
    ```

5. **Handoff.**

    ```text
    workspace/2026-07-admet-draft/manuscript/main.pdf   ← compiled draft
    workspace/2026-07-admet-draft/manuscript/main.tex
    workspace/2026-07-admet-draft/manuscript/sections/*.tex
    ```

The draft sits exactly where the reviewer loop expects it — follow with
*"run a paper review on this draft targeting PLOS CompBio"* (Example 2).

---

## Example 7 — Dry-run the whole pipeline for free

To exercise the mechanical flow without spending tokens, enable the `stub`
agent — it emits canned, schema-valid output:

```bash
uv run pytest tests/test_e2e_stub.py -q            # lit-review flow, 3 stub agents
uv run pytest tests/test_e2e_gap_discovery.py -q   # gap-discovery flow
uv run pytest tests/test_e2e_paper_draft.py -q     # paper-draft flow (compiles if latexmk present)
```

This is exactly how the framework is tested: fan-out → validation → debate →
synthesis → export/compile, all offline. Useful for verifying a fresh
install's plumbing before committing real agent time.

---

## Running any step by hand

Because coordination is just files and scripts, you can reproduce or debug any
piece directly:

```bash
# search one source
uv run scieflow research search openalex "your query" --limit 10 --from-year 2020

# validate an agent's output (JSON findings/review/gaps, or a YAML manifest)
uv run scieflow research validate workspace/<slug>/findings/claude.json
uv run scieflow research validate workspace/<slug>/reviews/codex-on-claude.json --schema review
uv run scieflow research validate workspace/<slug>/gaps/claude.json --schema gaps
uv run scieflow research validate workspace/<slug>/inputs/manifest.yml --schema manifest

# check a draft's citation + provenance integrity
uv run scieflow research check-citations --workspace workspace/<slug>

# dispatch one agent headless with a prompt file
uv run scieflow agent run agy workspace/<slug>/prompts/search-agy.md workspace/<slug>/logs/search-agy.log

# export selected papers to Zotero + bib
uv run scieflow research zotero-export --workspace workspace/<slug>
```
