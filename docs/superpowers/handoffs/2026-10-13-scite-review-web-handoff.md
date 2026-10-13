# Handoff to ScieFlowDev — web UI for reviewer pools, scite and the review matrix

**From:** default-profile Desktop session (scite integration, core side).
**To:** the ScieFlowDev owner (profile `scieflow-dev`).
**Status:** DRAFT — becomes actionable only when PR "scite M0 contract" is merged into `dev/cloud-continuation`. Do not start before that; do not interrupt the current K16 chain for it.
**Spec:** `docs/superpowers/specs/2026-10-13-scite-review-and-discovery-design.md` · **Plan:** `docs/superpowers/plans/2026-10-13-scite-review-and-discovery.md` · **Contract:** `docs/research/review-contract.md` (lands with M0).

## What core provides (frozen at M0, additive afterwards)

- `scieflow.core.review.service`: `review_settings`, `set_review_settings`, `assignment`, `rounds`, `review_pass`, `evidence_summary`, `matrix`, `decide`, `blockers`, `scite_status` — pure functions over `(project, slug, …)`, no FastAPI. Readers return `None`/`[]` for absent state.
- Fixture run `tests/fixtures/review/run/`: 3 authors, 1 extra reviewer, one complete round (assignment, 6 blind + 6 evidence passes, bundle, matrix with one blocker, no decisions yet).
- Events: `review.assigned|pass_done|evidence_built|matrix_ready|decided|blocker`, `scite.quota_low|unavailable` (on the run timeline like every event).
- Gate kinds: `review-decision`, `review-blocker` (human only), `scite-consent` (human only), existing `external-sharing`.
- Actor rules enforced in core: `set_review_settings(..., actor="agent")` refuses `scite=approved`, `allow_*`, `review_decisions`, `pool`; `decide()` refuses blocker clusters without a human `overrule_reason`.

## What the web side builds

1. **Scite toggle** in the Start wizard and the run staffing panel: `off / ask / approved` (render as a checkbox "Use scite" + "don't ask again for this run"); disabled with the reason when `scite_status().configured` is false; shows remaining quota.
2. **Reviewer pool editor** in staffing: pick reviewers (any enabled primary agent; support only if promoted), per reviewer `covers: all | auto`, `reviews_per_draft`, the two switches `allow_same_family_review` and `allow_self_review` (both off by default; explain "a fresh agent, never the authoring thread"), and a live preview of `assignment` (call the planner through the service; show the error text when a draft has no eligible reviewer).
3. **Review round view** per draft: blind vs evidence pass side by side per reviewer, `changed_from_blind` badges, evidence quotes with DOI links, `scite_status` badge (ok / off / quota / unavailable).
4. **Matrix view** with per-cluster accept / reject / defer + note, posting through `decide()`; blocker clusters pinned at the top with the retraction notice and an "overrule (reason required)" action; read-only when the run's `review_decisions` is `coordinator` and the coordinator already decided.
5. **Blocker banner** on the run page while `blockers()` is non-empty.
6. **Workbench link**: the curation page shows the accepted clusters of the latest round next to the drafts (read-only).
7. **(After M3)** hypothesis tournament view: Elo table, pairwise outcomes, novelty verdicts, decision gate.

## Constraints (unchanged web rules)

Every handler stays `def`; mutating routes go through the existing CSRF/session guards and `MUTATING_PATHS`; no `|safe`; path-shaped inputs (`reviewer`, `author`) are validated by core (`drafts.check_name`) but the web must not build paths itself; no edits to `src/scieflow/core/review/**`, `src/scieflow/integrations/**`, `src/scieflow/research/**` — request contract additions in this doc's changelog instead.

## Changelog

- 2026-10-13 — draft written with the spec and plan.
