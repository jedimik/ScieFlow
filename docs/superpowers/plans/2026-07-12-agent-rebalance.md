# Agent Rebalancing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Demote Gemini (`agy`) to a support tier in both ScieFlow and ResearchX; Claude + codex (ChatGPT 5.6 Sol) become the only agents for comprehensive work.

**Architecture:** A `tier` field on every agent registry entry (`primary` | `support`), a small filter helper in each repo's config library, and routing rules in AGENTS.md + skill files that bind the coordinator. Enforcement is contract-level (skills dispatch by tier), matching how `enabled` already works.

**Tech Stack:** Python 3 + PyYAML + pytest (both repos use `uv run pytest -q`, `pythonpath = ["scripts"]`).

## Global Constraints

- Codex model id: `gpt-5.6-sol` (user-confirmed; config-only, one-line edit if wrong).
- Support tier (agy) may do ONLY: literature-search fan-out, web-search tasks **paired with ≥1 primary agent**, long-document condensation.
- Per-run `config.yml` may narrow the agent set but never promote a support agent into a primary-only role.
- ResearchX is a git submodule at `vendors/ResearchX` — commit its changes inside that repo, then bump the pointer in ScieFlow (Task 5).
- Tests: `uv run pytest -q` must pass in the repo you are editing after every task.

---

### Task 1: ResearchX — tier filter helper in rxlib

**Files:**
- Modify: `vendors/ResearchX/scripts/rxlib/config.py` (append after `load_workspace`, ~line 42)
- Test: `vendors/ResearchX/tests/test_config.py`

**Interfaces:**
- Consumes: `load_workspace(ws, root) -> dict` with keys `agents`, `defaults`, `run_agents` (exists).
- Produces: `tier_agents(merged: dict, tier: str) -> list[str]` — run-set names whose registry entry has `tier: <tier>`. Used by skill prose and later tests.

- [ ] **Step 1: Write the failing test**

Append to `vendors/ResearchX/tests/test_config.py`. Also update the
`make_root` fixture's agents.yml so entries carry tiers:

```python
# In make_root, replace the three agent lines with:
#   claude: {cmd: "claude -p {prompt}", enabled: true, tier: primary}
#   codex: {cmd: "codex exec {prompt}", enabled: true, tier: primary}
#   agy: {cmd: "agy --print {prompt}", enabled: true, tier: support}
#   stub: {cmd: "python stub.py {prompt}", enabled: false, tier: primary}


def test_tier_agents_filters_run_set(tmp_path):
    root, ws = make_root(tmp_path)
    merged = config.load_workspace(ws, root)
    assert config.tier_agents(merged, "primary") == ["claude", "codex"]
    assert config.tier_agents(merged, "support") == ["agy"]


def test_tier_agents_cannot_promote_support(tmp_path):
    # A workspace narrowing the run set to agy still yields no primary agent.
    root, ws = make_root(tmp_path, workspace_cfg="agents: [agy]\n")
    merged = config.load_workspace(ws, root)
    assert config.tier_agents(merged, "primary") == []
    assert config.tier_agents(merged, "support") == ["agy"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd vendors/ResearchX && uv run pytest tests/test_config.py -q`
Expected: FAIL — `AttributeError: module 'rxlib.config' has no attribute 'tier_agents'` (plus any make_root assertions you touched now passing/failing consistently).

- [ ] **Step 3: Implement tier_agents**

Append to `vendors/ResearchX/scripts/rxlib/config.py`:

```python
def tier_agents(merged: dict, tier: str) -> list[str]:
    """Run-set agent names whose registry entry declares this tier.

    Registry tiers are authoritative: a workspace config.yml can narrow the
    run set but can never re-tier an agent (AGENTS.md routing rule).
    """
    return [
        name
        for name in merged["run_agents"]
        if merged["agents"].get(name, {}).get("tier") == tier
    ]
```

- [ ] **Step 4: Run the ResearchX suite**

Run: `cd vendors/ResearchX && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit (inside the submodule)**

```bash
cd vendors/ResearchX
git add scripts/rxlib/config.py tests/test_config.py
git commit -m "feat: tier_agents helper — registry tiers filter the run set"
```

---

### Task 2: ResearchX — registry tiers + codex model pin

**Files:**
- Modify: `vendors/ResearchX/config/agents.yml`
- Test: `vendors/ResearchX/tests/test_config.py`

**Interfaces:**
- Produces: real `config/agents.yml` where every enabled agent has `tier`; `codex` pinned to `gpt-5.6-sol`; `agy` has `tier: support` and `capabilities: [web-search, large-context]`.

- [ ] **Step 1: Write the failing test**

Append to `vendors/ResearchX/tests/test_config.py`:

```python
def test_real_registry_declares_tiers():
    agents = config.load_agents(REPO)
    assert agents["claude"]["tier"] == "primary"
    assert agents["codex"]["tier"] == "primary"
    assert agents["codex"]["model"] == "gpt-5.6-sol"
    assert "--model {model}" in agents["codex"]["cmd"]
    assert agents["agy"]["tier"] == "support"
    assert agents["agy"]["capabilities"] == ["web-search", "large-context"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vendors/ResearchX && uv run pytest tests/test_config.py::test_real_registry_declares_tiers -q`
Expected: FAIL with `KeyError: 'tier'`.

- [ ] **Step 3: Edit the registry**

In `vendors/ResearchX/config/agents.yml` replace the `agents:` block with
(defaults block unchanged):

```yaml
agents:
  claude:
    cmd: "claude -p --dangerously-skip-permissions --model {model} {prompt}"
    model: claude-fable-5
    tier: primary
    timeout_min: 15
    enabled: true
  codex:
    cmd: "codex exec --sandbox workspace-write --model {model} {prompt}"
    model: gpt-5.6-sol
    tier: primary
    timeout_min: 15
    enabled: true
  agy:
    cmd: "agy --print {prompt} --model {model} --dangerously-skip-permissions"
    # stdin_cmd: agy --print consumes --model as its value when {prompt} is absent
    stdin_cmd: "agy --model {model} --dangerously-skip-permissions"
    model: "Gemini 3.1 Pro (High)"
    tier: support
    capabilities: [web-search, large-context]
    timeout_min: 15
    enabled: true
  stub:
    cmd: "python scripts/stub_agent.py {prompt}"
    tier: primary          # tests dispatch it into primary-only roles
    timeout_min: 1
    enabled: false            # test/dry-run only
```

Note: `agent_run.py` substitutes `{model}` from the entry's `model:` key —
verify with `grep -n "{model}" vendors/ResearchX/scripts/agent_run.py`; if
substitution works via `.format(model=...)`, the codex `cmd` above is
enough. If `agent_run.py` errors on entries whose cmd lacks `{model}` or
vice versa, follow the claude entry's pattern exactly (it already uses
`{model}` + `model:` successfully).

- [ ] **Step 4: Run tests**

Run: `cd vendors/ResearchX && uv run pytest -q`
Expected: all PASS (including `test_real_registry_declares_tiers`).

- [ ] **Step 5: Commit**

```bash
cd vendors/ResearchX
git add config/agents.yml tests/test_config.py
git commit -m "feat: tiered agent registry — codex pinned to gpt-5.6-sol, agy demoted to support"
```

---

### Task 3: ResearchX — routing rule in AGENTS.md + skill wording

**Files:**
- Modify: `vendors/ResearchX/AGENTS.md` (Hard rules list, after rule 8)
- Modify: `vendors/ResearchX/skills/lit-review/SKILL.md` (Phase 3 intro)
- Modify: `vendors/ResearchX/skills/gap-discovery/SKILL.md` (Phase 3 + Phase 4 intros)
- Modify: `vendors/ResearchX/skills/paper-review/SKILL.md` (Setup item 2; journal-profiling item 2)
- Modify: `vendors/ResearchX/skills/paper-draft/SKILL.md` (Phase 2 item 1; Phase 3 intro)

**Interfaces:**
- Consumes: `tier: primary|support` registry semantics from Task 2; `tier_agents` vocabulary from Task 1.
- Produces: the routing contract every later plan (dual-author drafting) builds on.

- [ ] **Step 1: Add hard rule 9 to AGENTS.md (renumber the old 9+ if present — current list ends at 8)**

Append to the Hard rules list in `vendors/ResearchX/AGENTS.md`:

```markdown
9. **Tier routing.** Agents carry `tier: primary` (claude, codex) or
   `tier: support` (agy) in `config/agents.yml`. Comprehensive tasks —
   cross-review, synthesis, gap analysis, perspective debate, outlining,
   drafting, manuscript reviewer/submitter roles — dispatch **primary
   agents only**. Support agents are allowed only for: the
   literature-search fan-out; web-search tasks (e.g. journal profiling),
   and never alone there — always paired with at least one primary agent
   whose output cross-checks it; and long-document condensation. A
   workspace `config.yml` may narrow the run set but never promotes a
   support agent into a primary-only role.
```

- [ ] **Step 2: lit-review Phase 3 — restrict reviewers**

In `vendors/ResearchX/skills/lit-review/SKILL.md`, Phase 3 (cross-review),
after the quorum-skip sentence, change the pairing sentence to:

```markdown
For each ordered pair (reviewer R, author A), R ≠ A, where **R is a
primary-tier agent** (support agents' findings are still reviewed; they
never review — AGENTS.md rule 9) and A produced valid findings: write
`prompts/review-<R>-on-<A>.md`:
```

- [ ] **Step 3: gap-discovery — primary-only gap analysis and debate**

In `vendors/ResearchX/skills/gap-discovery/SKILL.md`:
- Phase 3 (gap analysis) line "For each agent in the run set (including
  yourself — write your own file last)" becomes:

```markdown
For each **primary-tier** agent in the run set (including yourself — write
your own file last; support agents do not do gap analysis, AGENTS.md
rule 9), write `prompts/gaps-<agent>.md`:
```

- Phase 4 (debate) participants line becomes:

```markdown
- participants = primary-tier agents with valid gaps files, plus you;
```

- [ ] **Step 4: paper-review — primary roles + paired profiling**

In `vendors/ResearchX/skills/paper-review/SKILL.md`:
- Setup item 2, reviewer/submitter defaults sentence becomes:

```markdown
   - `reviewer:` / `submitter:` — agent names, both **primary tier**
     (AGENTS.md rule 9). Default: reviewer is an enabled primary agent
     that is NOT you; submitter is you. Reviewer and submitter MUST
     differ (never grade your own edits).
```

- Journal profiling item 2 first sentence becomes:

```markdown
2. Otherwise dispatch one enabled agent with web access (a support-tier
   agent like agy is a good fit here — but never alone: also dispatch or
   perform a primary-agent pass that cross-checks its profile against the
   guideline URLs it cites, per AGENTS.md rule 9) via
   `prompts/journal-profile.md`:
```

- [ ] **Step 5: paper-draft — primary-only outline and drafting**

In `vendors/ResearchX/skills/paper-draft/SKILL.md`:
- Phase 2 item 1: "Dispatch ONE agent (your pick from the run set)" →
  "Dispatch ONE **primary-tier** agent (your pick from the run set)".
- Phase 2 item 2: "participants = two other agents" →
  "participants = two other primary-tier agents (fall back to one if only
  two primary agents exist)".
- Phase 3 heading line "Assign sections across the run set" →
  "Assign sections across the **primary-tier** run set".

- [ ] **Step 6: Verify and commit**

Run: `cd vendors/ResearchX && uv run pytest -q` (docs-only change — suite must still pass).

```bash
cd vendors/ResearchX
git add AGENTS.md skills/lit-review/SKILL.md skills/gap-discovery/SKILL.md \
        skills/paper-review/SKILL.md skills/paper-draft/SKILL.md
git commit -m "docs: tier routing rule — primary-only comprehensive tasks, paired support usage"
```

---

### Task 4: ScieFlow — registry entries, tier helper, AGENTS.md rule 10

**Files:**
- Modify: `config/agents.yml`
- Modify: `scripts/sflib/config.py`
- Modify: `AGENTS.md:49-51` (rule 10)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `load_agents(root) -> dict` (exists in sflib/config.py).
- Produces: `tier_agents(agents: dict, tier: str) -> list[str]` in sflib (flat signature — ScieFlow has no `run_agents` merge); registry entries `claude`/`codex` (primary), `agy` (support).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` (check its existing imports/fixtures first
and follow them; it imports `from sflib import config` or similar — match):

```python
def test_real_registry_tiers():
    root = config.repo_root()
    agents = config.load_agents(root)
    assert agents["claude"]["tier"] == "primary"
    assert agents["codex"]["tier"] == "primary"
    assert agents["codex"]["model"] == "gpt-5.6-sol"
    assert agents["agy"]["tier"] == "support"


def test_tier_agents_filters_enabled_only():
    agents = {
        "claude": {"tier": "primary", "enabled": True},
        "codex": {"tier": "primary", "enabled": False},
        "agy": {"tier": "support", "enabled": True},
    }
    assert config.tier_agents(agents, "primary") == ["claude"]
    assert config.tier_agents(agents, "support") == ["agy"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q` (from ScieFlow root)
Expected: FAIL — `KeyError: 'codex'` and `AttributeError: ... tier_agents`.

- [ ] **Step 3: Edit registry and sflib**

`config/agents.yml` — replace the header comment and `agents:` block:

```yaml
# ScieFlow agent registry. Tier routing (AGENTS.md rule 10): primary agents
# (claude, codex) take comprehensive work; support agents (agy) only easy /
# large-context / paired web-search tasks.
# {model}/{prompt}/{root} are substituted by scripts/agent_run.py.
# 'enabled' and 'tier' are advisory and bind the coordinator: skills dispatch
# only enabled agents, respecting tiers. agent_run.py does not enforce them;
# the stub is dispatched directly by the test suite.
agents:
  claude:
    cmd: "claude -p --dangerously-skip-permissions --model {model} {prompt}"
    stdin_cmd: "claude -p --dangerously-skip-permissions --model {model}"
    model: claude-fable-5
    tier: primary
    timeout_min: 30
    enabled: true
  codex:
    cmd: "codex exec --sandbox workspace-write --model {model} {prompt}"
    model: gpt-5.6-sol
    tier: primary
    timeout_min: 30
    enabled: true
  agy:
    cmd: "agy --print {prompt} --model {model} --dangerously-skip-permissions"
    stdin_cmd: "agy --model {model} --dangerously-skip-permissions"
    model: "Gemini 3.1 Pro (High)"
    tier: support
    capabilities: [web-search, large-context]
    timeout_min: 15
    enabled: true
  stub:
    cmd: "python3 {root}/scripts/stub_agent.py {prompt}"
    tier: primary
    timeout_min: 1
    enabled: false            # tests/dry-run only
```

Append to `scripts/sflib/config.py`:

```python
def tier_agents(agents: dict, tier: str) -> list[str]:
    """Enabled agent names declaring this tier (AGENTS.md rule 10)."""
    return [
        name
        for name, entry in agents.items()
        if entry.get("enabled") and entry.get("tier") == tier
    ]
```

- [ ] **Step 4: Rewrite AGENTS.md rule 10**

Replace rule 10 in `AGENTS.md` (currently "Model policy: every dispatch
uses the agent named by `agent` in the run's config (default `claude` ...)
No model switching."):

```markdown
10. Tier routing: agents carry `tier: primary` (claude — the default —
    and codex) or `tier: support` (agy) in `config/agents.yml`.
    Comprehensive work (experiment campaigns, synthesis, drafting,
    review) dispatches primary agents only, named by the run's config and
    marked `enabled: true`. Support agents are allowed only for easy,
    well-scoped tasks: literature-search fan-out, long-document
    condensation, and web-search auxiliaries — never alone for web
    search; always paired with a primary agent that cross-checks the
    output. A run config may narrow the agent set but never promotes a
    support agent into a primary-only role.
```

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -q`
Expected: all PASS. If `tests/test_dry_run.py` or `test_agent_run.py`
asserts on registry contents (e.g. exact agent list), update those
assertions to the new registry in the same commit.

- [ ] **Step 6: Commit**

```bash
git add config/agents.yml scripts/sflib/config.py AGENTS.md tests/test_config.py
git commit -m "feat: tiered ScieFlow registry — add codex (gpt-5.6-sol) and agy, rewrite rule 10"
```

---

### Task 5: Bump the ResearchX submodule pointer

**Files:**
- Modify: `vendors/ResearchX` (gitlink)

- [ ] **Step 1: Verify both suites pass**

Run: `uv run pytest -q && (cd vendors/ResearchX && uv run pytest -q)`
Expected: PASS, PASS.

- [ ] **Step 2: Commit the pointer**

```bash
git add vendors/ResearchX
git commit -m "chore: bump ResearchX — tiered registry and routing rules"
```
