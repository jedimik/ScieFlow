---
name: synthesis
description: Judge results against literature, write the notebook entry, decide the next move
---

# Synthesis

Done by the coordinator directly — no dispatch.

## Procedure

1. Read `hypothesis.md`, `results-summary.md`, `literature.md` for this
   iteration.
2. Verdict: **supported** (results consistent with literature),
   **contradicted** (literature disagrees — say which papers and why), or
   **unexplained** (no precedent found — flag as potentially interesting or
   potentially an artifact).
3. Write `iterations/<n>/synthesis.md`: verdict + reasoning + a concrete
   `Decision:` line — one of `continue` (new/refined hypothesis, state it),
   `stop-converged`, `stop-anomaly`. Mapping to checkpoint reasons (from
   `schemas/status.yml`): `stop-converged` → `checkpoint.py --reason
   converged`; `stop-anomaly` → `--reason anomaly`.
4. Compose the notebook entry per `skills/notebook/SKILL.md`, validate it,
   append it to `notebook.md`.
5. Convergence check: if the last `convergence_window` iterations (from
   config.yml) produced no metric improvement and no new hypothesis
   direction, decide `stop-converged`.

Judgment rules: a citation supports a result, it never replaces the data;
anomalies are reported, not hidden; refining parameters twice in a row with
shrinking gains is a convergence signal, not a reason for a third refine.
