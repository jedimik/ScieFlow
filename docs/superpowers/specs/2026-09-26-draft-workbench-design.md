# The draft workbench — design

**Status:** design approved in conversation 2026-09-26; awaiting spec review before planning.
**Position:** A1 (web control: run steering, charter, conversation, Start wizard — all merged) → **this (programme item C)**.

## Why this exists

`paper-draft` already has several agents write a manuscript independently, then merges them. The merge is currently a judgment the *coordinator* makes: Phase 5 picks the stronger version of each section and records why in `report/merge_log.md`.

That is the wrong place for it. Choosing which paragraph of which draft survives is the author's judgment, not the agent's — it is the part of drafting a researcher most wants to control and least wants delegated. Today the only way to exercise it is to read the files in a terminal and hand-edit, which is exactly what AGENTS.md rule 4 tells people not to do.

The workbench moves that decision into the browser: read every draft side by side, keep the passages worth keeping, write your own text where none of them got it right, and send the result back for the next round.

## What already exists, and what that saves

This is not a new pipeline. `paper-draft` Phase 3 writes `manuscript/drafts/<agent>/<section>.tex` — one file per section per agent — and Phase 6 compiles `main.tex` with `latexmk -pdf -interaction=nonstopmode -halt-on-error`, degrading with a warning when `latexmk` is absent.

So the workbench is a **view over files that are already there**, plus a curated document, plus one dispatch. It works on runs that already exist, adds no second drafting path, and inherits the compile step. Three things it does not need to build: the drafting, the file layout, or a way to serve a PDF — `application/pdf` is already on `files.INLINE_SAFE_TYPES`, so the existing artifact route renders one in-page.

## Decisions taken (user, 2026-09-26)

1. **Passage-level curation**, not section-level. The user was offered section-level choice as the cheaper option and chose spans deliberately: "some part of the text from another" was meant literally.
2. **A round returns one merged draft**, not a fresh set of N. The loop converges on one manuscript rather than keeping N alternatives alive.
3. **A real LaTeX preview is in scope.** Offered a source-only view with a link to the already-compiled PDF, the user asked for the rendering pipeline instead.

## Selections are quotations, not pointers

This is the design's load-bearing decision, and it is what makes passage-level curation survivable.

A kept passage stores **the text itself**, together with its provenance: which agent wrote it, which section it came from, which round. It does not store a character range.

Offsets would be the obvious implementation and the wrong one: every round rewrites the sections, so a stored range silently comes to point at different words, or none. A quotation cannot go stale. The cost is that a kept passage no longer tracks later edits to its source — which is correct, because the whole point is that you chose *those words*.

In the browser this needs no editor framework and no build step: `window.getSelection().toString()` posted with the agent and section is the entire capture mechanism.

## The curation document

A1's design already observed that the charter and the workbench are the same primitive — *a human-curated, versioned document that becomes part of the next dispatch's prompt*. That is honoured rather than re-invented: `run/curation.py` follows `run/charter.py`'s shape, and the workbench is that mechanism with a different editor on top.

Stored at `workspace/<slug>/manuscript/curation/document.yml`, versioned append-only, with every change on the run's timeline and any version restorable. It holds an ordered list of blocks:

| Block | Carries |
|---|---|
| `kept` | the passage text, plus `agent`, `section`, `round` |
| `mine` | text the user wrote, with no provenance to claim |

Order is the user's: blocks can be moved and removed. The document also carries the current `round`, which is what lets the page always show the latest
without the user tracking it. A `note` field alongside the blocks is the instruction staged for the
next round — the "intermediate window", kept separate from the passages because it speaks *about* them.

Both block kinds are data. Nothing in ScieFlow interprets a passage or a note; they are text a human assembled for an agent to read.

## The merge round

Sending the curation to the merging agent is **an ordinary conversation turn**. A1c already built exactly this: one sandboxed job that resumes the agent's own session, with a switchable agent.

That gives the feature its whole execution model for free — the merge is confined by the sandbox, counts against the run's budget, appears on the timeline as a job, and can be cancelled. The merging agent is changed the same way any conversation's agent is changed, which already refuses mid-turn and already clears the session when the agent changes.

The turn's prompt pins the curation document, and names where to write: `manuscript/curation/rounds/<n>/<section>.tex` (the document and the
rounds share one `curation/` directory; nothing else writes there). That output becomes the next round's left-hand pane, and the drafts stay where they are — each round is additive, so you can always see what you curated from.

**The user never has to remember the round number.** Rounds advance when a merge turn completes, and the page always shows the latest.

## The LaTeX preview

A compile, not an approximation. A JavaScript maths renderer would show formulae and silently ignore `\section`, `\cite`, floats and the document class — a preview that lies about the artifact is worse than none.

**The compile is a job, never inline in a request.** A `latexmk` run takes seconds to minutes. The previous milestone spent a full fix wave on handlers that blocked the single-process server, and `tests/web/test_async_routes.py` now enforces that no handler is a coroutine; a compile inside a request would reintroduce the same defect with a worse constant. As a job it also gets the sandbox, the timeline and a Cancel button, and the existing `application/pdf` inline route displays the result in an `<iframe>`.

**What gets compiled, and how.** A section `.tex` has no `\documentclass`, so it cannot be compiled
alone — and the template's `main.tex` cannot compile a draft either, because it `\input`s fixed paths
under `sections/`, which holds the *merged* output rather than any agent's draft. So the preview unit
is **one whole draft** (an agent's, or a completed round's), and the compile job assembles a
throwaway document in a scratch directory: the `preamble.tex` the run is actually using when it has
one, else `src/scieflow/research/templates/paper/preamble.tex`, plus a `main.tex` generated from the
same template with its `\input` paths pointed at the directory being previewed. Placeholders come
from the run's config, with plain fallbacks — a preview must not demand the user settle the author
list first. The run's real `manuscript/` is never written to; a preview is not an assembly step.

Three further properties the implementation must hold:

- **`-shell-escape` stays off.** The `.tex` being compiled was written by an agent, so it is untrusted input, and `\write18` would turn a preview into arbitrary command execution. The existing invocation does not enable it; this must be pinned by a test, because it is precisely the flag someone adds later to make a package work.
- **The compile runs sandboxed**, like every other dispatch — confined to the run it belongs to.
- **It degrades when `latexmk` is absent**, the way Phase 6 already does: say so on the page and keep the source view, rather than failing the round. The repo's own tests already skip on a missing `latexmk`, and this host has a known flake in that area.

A failed compile is normal during drafting — an agent's LaTeX will not always build. The page shows the compiler's error output beside the source, because that is the thing a person needs in order to fix it.

## Pages

One new page, `/runs/<slug>/drafts`. The run page is already dense — charter, conversation, phases, budget, gates, jobs, timeline — and a three-panel workbench does not belong inside it. The run page links out.

Three regions, matching what the user described:

- **The drafts**, one column per agent, section by section, with the source shown and selectable.
  The compiled preview is offered per *draft*, not per section — see below.
- **The curation**, ordered, showing each passage with where it came from, editable, reorderable, with your own text addable anywhere.
- **The staging note plus the merging agent**, and the button that sends the round.

## Write-path safety

Nothing new in kind; the existing rules cover it.

- Every mutation goes through `scieflow.core.service`. The page routes stay thin.
- Every non-GET route is session-guarded and CSRF-protected, and must appear in `tests/web/mutating_paths.py::MUTATING_PATHS` with a `SAMPLES` entry — the test asserts the two agree, which is what stops a route landing unguarded.
- Every handler stays `def`. `tests/web/test_async_routes.py` enforces it.
- Passage text and agent-written `.tex` both reach HTML. Jinja autoescapes; nothing may defeat it. This app shipped a stored-XSS bug in a prior milestone, so the workbench's rendering gets its own test with a real payload rather than an assumption.
- Selections arrive from a form and are stored verbatim. There is no path by which a passage becomes an instruction to ScieFlow.

## Testing

- The curation document gets the treatment `charter.py` got: validation before any write, concurrent appends under the store lock, and a refusal leaving the file untouched.
- **The pinning is tested by falsification** — that the curation text reaches the merge turn's dispatched prompt, written so that removing the pinning makes it fail. The charter's equivalent test is the model; this is the requirement most likely to rot silently.
- **`-shell-escape` absent from the compile argv** is asserted directly. A test that only checks "the compile succeeded" would not catch someone adding it.
- The compile-as-a-job property is tested the way the cancel and turn routes were: a real server, a compile in flight, and a probe proving the app still answers.
- A missing `latexmk` is tested as a supported state, not skipped.

## Risks

| Risk | Handling |
|---|---|
| A kept passage's source is rewritten next round | By design — a selection is a quotation, not a pointer, and keeps the words that were chosen |
| Agent LaTeX that will not compile | Expected during drafting; the error output is shown beside the source rather than failing the round |
| `latexmk` absent, or flaky on this host | Degrades to the source view with the reason stated; already the workflow's convention |
| A compile blocking the server | It is a job, and the async-route inventory test makes a regression fail loudly |
| Malicious LaTeX | `-shell-escape` off and pinned by a test; the compile is sandboxed like any dispatch |
| A run whose `manuscript/` has no preamble yet (pre-Phase-5) | The preview falls back to the shipped template, so a draft is previewable before assembly |
| Curation growing until it crowds the merge prompt | Versioned and editable; the user trims, and the pinned portion is visible beside the panel |

## Out of scope

Re-drafting with N agents each round (the user chose convergence). Manuscript git sync — that is programme item D. The run explorer (B). A container sandbox backend for macOS and native Windows (E), which the preview inherits rather than needs. Any change to how `paper-draft` produces its drafts: the workbench reads what that workflow already writes, and changing the producer is a separate question.
