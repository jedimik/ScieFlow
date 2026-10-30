---
name: journal-research
description: Research a target journal's author guidelines into an adopted, quote-checked profile, then check, render, build and package a manuscript against it. Coordinator-side protocol; read AGENTS.md first.
---

# Journal Research and Manuscript Check

You are the **coordinator**. The journal side and the manuscript side are both
ScieFlow commands; your job is to run them in order, dispatch the drafting and
reading agents with the right role, and report what they return. Full reference:
`docs/research/journals.md` and `docs/research/manuscript.md`.

## Hard rules

- **Never answer a `journal-sources` gate.** It is human-only. Show the user the
  answer line that `journal research` prints and wait for them.
- **Never add a source page yourself.** `scieflow journal add-source` is the user's
  act and is refused inside a dispatch. If a publisher refuses downloads, ask the
  user to save the page and run it.
- **Never write or edit a profile file** (`config/journals/<slug>.yml` or `.md`), and
  never hand-write a draft as if it were checked. Profiles come only from
  `scieflow journal adopt`, from a draft that carries a quote for every value.
- **Never adopt or add a source from a sub-agent.** `adopt` and `add-source` refuse
  inside a dispatch; do not try another route to the same result.
- **Never invent a limit.** A value without a verbatim quote from a stored source is
  not evidence. Leave it null and say so.
- **Support agents never work alone.** A support drafter is always followed by a
  primary cross-check (tier rule).
- **Turning on `journal-guidelines` is the user's choice** (it allows downloads from
  the publisher's hosts). Ask before editing `config/integrations.yml`.
- **Data level.** Journal research only in a run at data level L0 (the profile is
  project-wide; the journal name and ISSN are the only things sent). If the run is
  L2, stop and ask the user for an L0 run.
- **Every dispatch carries `--role`.** A role-less `scieflow agent run` is refused.

## Phase A — facts and the guideline pages

1. Research the journal in a run the user names (a paper-draft or paper-review
   workspace, or another run they choose):

   ```bash
   uv run scieflow journal research <run> --journal "<journal name>" [--issn <ISSN>] [--url <https guideline page>]...
   ```

   - Exit 1 listing candidates: ask the user for the ISSN and re-run with `--issn`.
     Do not pick a candidate yourself.
   - Off by default: if the output says `journal-guidelines` is off, either the user
     saves the pages (step 3) or they switch the provider on (see the hard rule).
   - Gate open: the command prints `uv run scieflow gate answer <run> <id> approve`.
     Give the user that line and stop until they answer. Never answer it yourself.
2. After the human approves, run `journal research <run>` again to fetch. Failed rows:
   `--retry-failed`. A redirect is a failure: the user re-runs with the final URL.
3. Saved pages (the user's act): `uv run scieflow journal add-source <run> --file <page> --url <https URL>`.
   A PDF guide must be saved as text first.

## Phase B — draft and adopt

4. Drafts from each support agent assigned to `research.journal-profile`:

   ```bash
   uv run scieflow journal prompt <run> --for <support-agent> --write
   uv run scieflow agent run --role research.journal-profile <support-agent> workspace/<run>/prompts/journal-profile-<support-agent>.md workspace/<run>/logs/journal-profile-<support-agent>.log
   ```

5. The primary cross-check of every draft (verifies each value and quote against the
   sources; writes `journal/profile.draft.yml`):

   ```bash
   uv run scieflow journal prompt <run> --cross-check --write
   uv run scieflow agent run --role research.journal-profile <primary-agent> workspace/<run>/prompts/journal-profile.md workspace/<run>/logs/journal-profile-check.log
   ```

6. Adopt the checked draft:

   ```bash
   uv run scieflow journal adopt <run> --as-agent
   ```

   - Errors list `rule path` lines and nothing is written. Re-dispatch the cross-check
     with the errors appended (once), then stop and report if it still fails.
   - Keep `--as-agent` when you, the coordinator agent, run the adoption: without it
     the record names the human as adopter (`adopted_by: human`). Leave it out only
     when the user runs `journal adopt` themselves, and say which you did.
   - An existing profile of the same slug with other content needs `--replace`. Show
     `uv run scieflow journal diff <slug> <run>` first and get the user's go-ahead.
7. Show the result: `uv run scieflow journal show <slug>` (or `--json`). Staleness is
   in `journal list`; a stale profile is a warning, not a blocker.

## Phase C — check and package a manuscript

8. Check the manuscript against the profile:

   ```bash
   uv run scieflow manuscript check <run> [--guideline <id>]
   ```

   For a reporting guideline that needs a judgement, write and dispatch the reader,
   then check again:

   ```bash
   uv run scieflow manuscript checklist-prompt <run> --guideline <id> --write
   uv run scieflow agent run --role research.reviewer <primary-agent> workspace/<run>/prompts/checklist-<id>.md workspace/<run>/logs/checklist-<id>.log
   uv run scieflow manuscript check <run> --guideline <id>
   ```

   Judgements are advisory and checklists are unverified until a human verifies them.
9. Fix what the check finds in the authors' text (sections, `metadata.yml`), not by
   editing the report. Word counts follow ScieFlow's documented rule; flag borderline
   counts to the user.
10. Render, build, check, package:

    ```bash
    uv run scieflow manuscript render <run> --class <article|elsarticle|IEEEtran|svjour3>
    uv run scieflow manuscript build <run> --engine <engine>
    uv run scieflow manuscript check <run>
    uv run scieflow manuscript package <run>
    ```

    `build` uses the engine the user names; `tectonic` is used only when named. Do not
    hand-edit `main.tex`; `render --force` keeps a hand-edited file as a backup, and only
    with the user's go-ahead.
11. `package` refuses while a review blocker is open (see `uv run scieflow review
    merge-check <run>`), when the check has an error, and when the build is stale. A
    refusal is a boundary: fix the cause or report it. Never package past a blocker.
12. Starter letters: `uv run scieflow manuscript letter <run> --kind cover` (or
    `--kind response`). The letter's content is the authors'; list the placeholders
    the package reports.

## Report to the user

Give the profile slug and its source list, the check's error and warning rule ids
with counts, the build result, the package path (`report/submission/`), the
placeholders still open, and any unverified checklists. ScieFlow uploads nothing:
the user submits.
