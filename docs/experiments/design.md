# Study Design and SAP

A run can carry a **study design** with a statistical analysis plan (SAP). Once a
human locks it, ScieFlow reads the design and holds experiment campaigns to it:
a campaign whose metrics are not outcomes of the locked SAP is refused, and every
campaign is refused while the design file differs from its lock. Sample sizes
come from `scieflow experiment power`, never from arithmetic in an agent's
context.

A run with no `design/` directory behaves exactly as before.

Agent rule 19 in `AGENTS.md` states the same rules for agents.

## The design file

`workspace/<slug>/design/design.yml` describes the study: its type, question,
variables and their roles, groups, timeline, sampling, randomisation and
blinding, the sample size, the SAP, the reporting guidelines it follows, and the
data levels it needs. The format is described in `schemas/design.yml`.

Study types: `rct`, `cohort`, `case-control`, `cross-sectional`,
`diagnostic-accuracy`, `prediction-model`, `systematic-review`, `animal`,
`economic-evaluation` and `computational`.

The SAP lists outcomes, each with a priority (`primary`, `secondary`,
`exploratory`), a test, and optionally a `metric`. The `metric` names a campaign
metric; it is how a campaign is tied to an outcome. Every campaign metric must be
named by some SAP outcome to run under a locked non-computational design.

Commands:

```bash
uv run scieflow design init <slug> --type rct --title "Synthetic RCT"
uv run scieflow design check <slug> [--json]
uv run scieflow design render <slug>
uv run scieflow design show <slug> [--json]
uv run scieflow design lock <slug> [--json]
uv run scieflow design amend <slug> --summary "..." --rationale "..." [--json]
uv run scieflow design guidelines [--json] [--check]
```

`init` writes a template whose free-text fields are `TODO`; `check` reports them
until they are filled in. `init` never overwrites a locked design.

## Check rules

`check` reports **errors**, which make a lock impossible, and **warnings**, which
are advice. Every message names a key path and a rule, never a free-text value.

Errors: `keys`, `json-value`, `enum`, `todo`, `id`, `ref`, `primary`, `test`,
`rct`, `sample-size`, `power-mismatch`, `metric`, `alpha`, `exploratory`,
`guideline`, `levels`.

Warnings: `no-sample-size`, `power-unverified`, `guideline-fit`, `no-guideline`,
`guideline-unverified`, `level-undeclared`, `missing-data`, `multiplicity`,
`outcome-role`.

`power-mismatch` recomputes the sample-size file: a hand-edited number in a power
file is an error, not a silent value.

`level-undeclared` warns when the design needs L2 data that the run has not
declared. ScieFlow never declares a level itself; declare it with
`scieflow data declare`. See [Data protection](../data-protection.md).

## Locking, amendments and the ledger

`design lock` checks the design, writes a pending record and opens a
`design-lock` gate. **Only a human can answer that gate.** Agents cannot answer
it, and the answer hook refuses any non-human actor. On approval, ScieFlow
appends a record to the append-only ledger `design/lock.jsonl` and copies the
design and its power file to `design/locked/v<N>/`. Rejecting the gate removes
the pending record and changes nothing else.

The lock covers both `design.yml` and the power file it names, so a locked
power calculation cannot change either.

A change to a locked design goes through `design amend`, which requires a summary
and a rationale, writes a diff under `design/amendments/`, and opens a new
human-only gate. Nothing is ever re-locked or silently edited. The version is
derived from the number of records: `1.0`, `2.0`, and so on.

`design show` reports the status (`none`, `draft`, `pending`, `locked`,
`modified`, `invalid`, `ledger-invalid`), the version, the hashes, the open gate
and the full trail of records.

The content hash is the SHA-256 of canonical JSON (keys sorted, no NaN). Key
order and YAML formatting do not change it; any value change does.

Ledger verification fails closed. A broken chain, a record whose gate is missing
or not approved by a human, or a copy whose content hash does not match all make
the ledger `ledger-invalid`, and that refuses campaigns.

## Campaign rule

`scieflow experiment run` and `scieflow experiment sweep` check the run's design
before anything runs or spends. The check happens after the budget check and
before any container starts.

| Design state | Rule id | Non-computational | Computational |
|---|---|---|---|
| No `design/` directory | none | allowed | allowed |
| Ledger does not verify | `design-ledger-invalid` | refused | refused |
| `design.yml` differs from its lock | `design-modified` | refused | refused |
| `design.yml` cannot be read | `design-invalid` | refused | refused |
| Not locked, or has errors | `design-unlocked` / `design-invalid` | refused | warning |
| Locked, a campaign metric is not in the SAP | `out-of-sap` | refused | warning |
| Locked, every metric is in the SAP | none | allowed | allowed |

A refusal records a `job.refused` event with `reason: design` and the rule id,
and exits with status 1. A computational warning prints to stderr and records a
`note.design_warning` event. The integrity rules, `design-ledger-invalid` and
`design-modified`, refuse for every study type (M-D1-D6). Only the methodological
rules, `design-unlocked` and `out-of-sap`, are warnings for computational designs.

Bypassing the guard by pointing `--experiments-dir` outside the run is the same
residual the budget guard has (see residuals below).

## Sample size: `scieflow experiment power`

```bash
uv run scieflow experiment power t-ind --effect-size 0.5 --power 0.8 --out design/power/rct.json
```

The command solves for one unknown: the sample size (`--n`), the power
(`--power`) or, for some families, the effect size. Output includes the method
and the assumptions, and `--out` writes a JSON file the design refers to.

| Family | Effect input | Power calculation |
|---|---|---|
| `t-ind` | `--effect-size` d | noncentral t (statsmodels `TTestIndPower` semantics) |
| `t-paired`, `t-one` | `--effect-size` d | noncentral t (`TTestPower` semantics) |
| `anova` | `--effect-size` f, `--groups` k | noncentral F (`FTestAnovaPower` semantics) |
| `proportions` | `--p1 --p2` or `--effect-size` h | normal approximation on Cohen's arcsine h (`NormalIndPower` semantics) |
| `correlation` | `--r` | Fisher z normal approximation |
| `survival` | `--hazard-ratio`, `--allocation`, `--event-probability` | Schoenfeld (1983) events approximation |

The formulas are re-implemented over scipy, which the `experiments` extra
already installs; statsmodels is not a dependency. Reference values were
computed on this host and cross-checked against published integers (Cohen 1988,
G*Power, Schoenfeld 1983). A test compares against statsmodels whenever it is
installed.

Survival power is an approximation that assumes proportional hazards and has no
accrual or dropout model. The output's assumptions say so.

## Reporting checklists

`config/reporting/` holds eight checklists as data: CONSORT 2010, STROBE,
STARD 2015, TRIPOD+AI, PRISMA 2020, SPIRIT 2013, ARRIVE 2.0 and CHEERS 2022. Each
item has an id, a short label written in our own words, a manuscript section and
the kind of evidence it needs. The official item wording is not reproduced.

**Every checklist is `unverified`.** The item ids and labels were written offline
from knowledge, without network access, and must be checked against the official
checklists before anyone relies on them for a submission. `design check` and
`design guidelines` say so. CONSORT 2025 and SPIRIT 2025 are not included.

`scieflow design guidelines --check` validates the checklist files.

## Data levels

A design can state the data levels it needs (`L0`, `L1`, `L2`). See
[Data protection](../data-protection.md) for what each level means. The design
only warns when it needs L2 and the run has not declared it. It never declares
or lowers a level itself.

## Credits

The lock and amendment rules follow researcher's `protocol.py` semantics
(researcher, MIT licence). They are re-implemented over a ScieFlow ledger file,
not vendored. The power formulas follow the statsmodels power module semantics
(statsmodels, BSD-3-Clause licence) and are re-implemented over scipy.

## Residual risks

- **R1 No statsmodels comparison yet.** The reference values are this
  implementation's formulas on scipy, cross-checked against published integers.
  The statsmodels comparison test is skipped until statsmodels is installed.
- **R2 File-based state is writable before protection exists.** The gate files
  and `events.jsonl` are not protected, and `lock.jsonl` and `locked/` are
  protected only once they exist. `verify_ledger` raises the bar but cannot prove
  a human acted. A host-side signed ledger is a follow-up.
- **R3 Guard reach.** The campaign check applies only when `--experiments-dir`
  lies inside a run. A campaign recorded elsewhere is not checked.
- **R4 Checklists unverified.** See the reporting checklists section. A human
  must verify them before any submission use.
- **R5 Test per outcome not enforced.** Campaigns have no statistical test. The
  check arrives with the `stats.json` output of the analysis module.
- **R6 Survival power is an approximation.** See the sample size section.
