# Skills Reference

The agent layer is six markdown skills under `src/scieflow/experiments/skills/`. They are
plain markdown with YAML frontmatter — readable by any agent CLI.

| Skill (path in repo) | Purpose | Key rule |
|---|---|---|
| `src/scieflow/experiments/skills/experiment-designer/SKILL.md` | Turn a goal into a campaign YAML | No execution before user approval |
| `src/scieflow/experiments/skills/experiment-runner/SKILL.md` | Build envs, execute campaigns | Containers only for recorded runs |
| `src/scieflow/experiments/skills/evaluator/SKILL.md` | Metrics, QC, validation, reporting | Inspect artifacts, not just numbers |
| `src/scieflow/experiments/skills/literature-support/SKILL.md` | Ground findings in publications | Never fabricate citations |
| `src/scieflow/experiments/skills/framework-extender/SKILL.md` | New stages/metrics/domains | Don't modify the experiments core |
| `src/scieflow/experiments/skills/model-routing/SKILL.md` | Route each phase to the right model tier | Judgment stays on high-tier models |

Each skill file states *when to use it*, the *workflow*, and the *rules*.
Agents are instructed by `AGENTS.md`/`CLAUDE.md` to read the relevant skill
before acting.

Skill files live in the repo (not in an agent-specific plugin format) so the
same instructions work across Claude Code, Codex, Gemini CLI, agy, and
future agent CLIs.
