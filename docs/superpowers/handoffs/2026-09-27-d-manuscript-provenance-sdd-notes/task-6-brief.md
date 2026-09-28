### Task 6: Documentation

**Files:**
- Modify: `docs/web.md`, `docs/runs.md`
- Not modified: `docs/cli.md` — this plan adds no CLI command. Say so in your report rather than leaving it ambiguous.
- Test: `uv run --group docs mkdocs build --strict`

**Interfaces:** consumes the behaviour of Tasks 1–5. Produces no code.

Document it where the workbench is already documented, matching that section's voice and depth — read `docs/web.md`'s draft-workbench subsection and `docs/runs.md`'s charter section first; this repo's docs explain *why*, not just *what*.

- [ ] **Step 1: Write the documentation**

Cover, verifying each claim against the code rather than against this plan:

- Where the history lives (`workspace/<slug>/provenance.git`), that it is a real git repo you can point any git tool at, and that it is **bare** so there is nothing checked out.
- The layout: `main` for what the run produced collectively, `draft/<agent>` for what each agent produced alone, `merge_N/` for the workbench's merge rounds and `review_N/` for `paper-review`'s draft rounds — and that these are two different counters, which is why they are named apart.
- That branch names come from the agents the run actually used, discovered from its own artifacts, so adding an agent needs no configuration.
- The three diffs a person will actually want, as commands they can run themselves:
  `git diff main:merge_1 main:merge_2`, `git diff draft/claude:sections main:merge_1/sections`,
  `git diff draft/claude:review_1 draft/claude:review_2`.
- That the repo is **derived**: the workspace files are the truth, deleting it costs history only, and the next page load rebuilds what it can. State that this is why a failed sync never fails a round.
- That it is local only — nothing is pushed anywhere, and DVC still carries the bytes.
- That `git` is optional: without it the panel says so and the workbench is unaffected.
- The on-disk layout as a block, as `docs/web.md` already does for the workbench.

In `docs/runs.md`, add a cross-reference from wherever run artifacts are described, pointing at the new section. Check whether `AGENTS.md` keeps an inventory this belongs in; **it does not** — it has only scattered feature-specific mentions, so add nothing there, and say in your report that you checked.

- [ ] **Step 2: Verify**

Run: `uv run --group docs mkdocs build --strict && ./scripts/check_legacy.sh && uv run pytest -q`
Expected: zero mkdocs warnings; 25/25 `ok`; suite green.

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs: the manuscript's git history, and how to read it"
```
