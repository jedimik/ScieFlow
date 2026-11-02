# Claim ledger and check

`scieflow manuscript claims` ties every checkable sentence, figure and table of a
run's manuscript to its evidence, then checks the ledger mechanically. A number
that is missing from its evidence, a sentence with no source, evidence that changed
after it was pinned, a retracted reference or a run from changed code is an error.
`scieflow manuscript crate` exports the same run as an RO-Crate 1.1 metadata file.

Everything here is local. The check makes no network call, nothing leaves the run,
and no evidence value is printed in a message or an event. The journal decision
letter, the response to reviewers and the provenance points and diffs are in
[Journal revision](revision.md).

Status: the check is a command, not a lifecycle gate. The verify phase does not run
it yet, and the `paper-revision` workflow is not wired (M-G2). `scieflow manuscript
package` already refuses when the check has an error (see [Package refusal](#package-refusal)).

## Commands

```bash
uv run scieflow manuscript claims build <run> [--main main.tex] [--repin] [--repin-claim ID] [--json]
uv run scieflow manuscript claims show <run> [--id ID] [--json]
uv run scieflow manuscript claims check <run> [--main main.tex] [--json] [--no-write]
uv run scieflow manuscript claims waive <run> <claim-id> --code C001 --reason "..."
uv run scieflow manuscript crate <run> [--json] [--no-write]
```

Exit codes: `0` ok, `1` refused or a check with errors, `2` usage. A refusal inside a
sub-agent dispatch names the command and says it is the user's or the coordinator's.

## Files in a run

```text
workspace/<run>/manuscript/                 the source tree (read, never written here)
workspace/<run>/report/claims/claims.jsonl  one ledger line per claim, in document order
workspace/<run>/report/claims/ledger.json   version, parser, tree hash, count, claims hash
workspace/<run>/report/claims/check.json    the last check (counts, findings, waived)
workspace/<run>/report/claims/check.md      the same findings as a table
workspace/<run>/report/ro-crate/ro-crate-metadata.json   the crate (see RO-Crate)
```

All writes are atomic and refuse a symbolic link on the path. Every read is confined
to the run. Outputs are deterministic: sorted keys, no timestamps, UTF-8, `\n` line ends.

## Claims

- `build` reads the document body (from `\begin{document}` to `\end{document}`, or to the
  first bibliography command). Each `figure`, `figure*`, `table` and `table*` environment
  is one unit. Abstract sentences are units. The rest is split into paragraphs at blank
  lines and sectioning commands, then into sentences with the sentence splitter of the
  claim-check module.
- A unit is a claim when it has a checkable number, a reference or a `\cite`. Other units
  are not ledgered.
- Each claim has an id `c-` plus 12 hex characters of a hash of its file and normalised text.
  Repeats get `-2`, `-3` in document order.
- Limits: at most 5,000 claims; a larger ledger is refused (`claims-limit`) and nothing is
  written. Stored text is capped at 2,000 characters.
- `build` pins a claim's evidence from the current files. Pins carry over from the previous
  ledger while the claim's id and references are unchanged. `--repin` takes every pin from
  the current evidence; `--repin-claim ID` takes one claim's pins (repeatable). An unresolved
  reference has no pin.
- `check` refuses a ledger whose `manuscript_tree_sha256` differs from the current tree
  (`ledger-stale`): run `scieflow manuscript claims build` first.
- `show` prints the ledger. Table excerpts are at most 60 characters.

## References

A reference is a marker `[data:<id>]`, `[run:<ref>]` or `[doi:<doi>]`, written in a comment
of the unit (the paper-draft form is `% source: [data:<id>]`) or in its text. Every `\cite`
key adds a DOI reference when its bibliography entry has a DOI.

- `data:<id>` resolves to an artifact of `inputs/manifest.yml`, a released artifact of
  `data.yml`, or an import with that release id. Two matches are `ref-ambiguous`, none is
  `ref-unresolved`, an unsafe path is `ref-unsafe`. A released artifact whose hash no longer
  matches is `artifact-altered` (C002).
- `run:<ref>` looks only inside this run's `experiments/`: a campaign folder with
  `results.json`, a `<campaign>/<run_id>` pair, or a bare run id that exactly one campaign
  lists. A run id in two campaigns is `ref-ambiguous`.
- `doi:<doi>` must be declared by a bibliography entry (`ref-unresolved` otherwise). A cite key
  must declare the DOI it cites. An entry with
  no DOI is the warning `doi-missing`. When `report/selected_dois.txt` exists and lacks the DOI,
  the warning is `doi-unselected`. A reference id longer than 200 characters is `ref-too-long`,
  and an unsafe DOI is `ref-unsafe-doi` (both C001).
- Text-like evidence (`.csv`, `.tsv`, `.json`, `.txt`, `.md`, `.tex`, `.yml`, `.yaml`, up to
  20 MB each, at most 200 files per reference) gives numbers. Other files are listed and
  hashed only; a claim whose numbers can only be checked against them is `number-uncheckable`.
- Pins record the hash of each evidence file. A DOI has no pin.
- L2 runs: a `data:` reference that resolves through the manifest, not through a release, is
  `data-unreleased`. It is an error and cannot be waived (see [L2 runs](#l2-runs)).

## Numbers

- A number is a token with an optional sign (`-`, `−`, `+`), thousands separators and decimals,
  optionally followed by `%`. Scientific notation is read in the evidence; a `\times 10^{…}`
  form in the manuscript is not checked (`number-uncheckable`).
- A relation word or symbol directly before a number (`<`, `\le`, `≥`, `\geq` and similar) sets
  the relation. Otherwise the relation is `=`.
- Not checked: numbers inside citation, reference, label, URL, hyperlink, image and option
  arguments, display math, numbers after words such as `Figure`, `Table`, `Section` or `Step`,
  integers from 1900 to 2099 without `%` (years), ordinals, and tokens glued to letters
  (`H2O`, `SF-36`).
- A manuscript value with `d` decimals matches an evidence value when `round_half_up` or
  `round_half_even` at `d` decimals gives it. Arithmetic is exact (`decimal.Decimal`). A
  percentage also matches `a × 100`. A relation must hold.
- A claim with data or run references and a DOI cite gets `number-unmatched-cited` (a warning):
  the number may come from the cited paper.
- A claim with only DOI references is not number-checked (`numbers_checked: false` in the report).
- Messages name the manuscript number, the file and line, and the evidence path. They never
  print a value read from an evidence file.

## The check

`check` runs the ledger against the evidence and the review blockers. Findings have a code,
a rule id and a severity.

| Code | Rule ids | Severity |
| --- | --- | --- |
| C001 orphan or unresolved claim | `orphan` (an error in an abstract or results section, or in a table or figure unit; a warning elsewhere), `ref-unresolved`, `ref-ambiguous`, `ref-unsafe`, `ref-too-long`, `ref-unsafe-doi` | error (`ref-*` always; `orphan` as stated) |
| C002 altered number | `number-missing`, `artifact-altered` | error |
| C003 stale evidence | `evidence-changed` (error), `source-corrected` (warning) | error or warning |
| C004 | reserved; no rule emits it (M-G-D3) | — |
| C005 retraction | `blocked-reference` | error, not waivable |
| C006 code drift | `code-dirty`, `code-not-ancestor` | error |
| warnings | `data-unreleased` (error, not waivable, see L2), `code-state-unknown`, `run-unverifiable`, `doi-missing`, `doi-unselected`, `doi-status-unknown`, `number-uncheckable`, `number-unmatched-cited`, `status-unreadable` | warning |

- `C006`: a cited run whose manifest records a dirty code state, or whose commit is not an
  ancestor of the repository HEAD, is drift. `code-state-unknown` is a warning when git is
  missing or the manifest has no git record.
- `C005`: the message names the claim, the reference (a DOI, or the cite key's safe token)
  and the blocker reason (`retracted`, `unresolvable`, `identity-mismatch` and similar). It
  says how it clears: fix the reference, or the user overrules the `review-blocker` gate. It
  never prints the blocker's detail text. The key token is the rule of the identity gates,
  reimplemented in the check and pinned equal by a test.
- `doi-status-unknown` is reported once per check when the run has no evidence round and no
  identity check.
- A status lookup that fails is the warning `status-unreadable`, never C005. The package
  treats the same failure as a refusal (fail closed).
- `check.json` is sorted by file order, line, code and rule. `check.md` has the same findings
  as a table. `--json` prints the document. `--no-write` writes nothing and records no event.
  With a write, one `note.claims_checked` event records the counts.
- The check exits 0 without errors, 1 with errors or bad input.

## Waivers

`scieflow manuscript claims waive <run> <claim-id> --code C001 --reason "..."`:

- Refused when `SCIEFLOW_DISPATCH` is set. The user waives; sub-agents never do.
- The claim must exist in the current ledger and have a finding with that code.
- Waivable codes: C001, C002, C003 and C006. C005 and `data-unreleased` are not waivable.
- `--reason` is 10 to 500 characters.
- The event `note.claims_waived` records the claim, code, the claim's text hash and the reason,
  with actor `human`. The check applies a waiver only when a human-actor event carries the claim's
  current text hash. Editing the sentence voids the waiver. Waived findings move to `waived` and
  do not count as errors.

## Package refusal

`scieflow manuscript package` runs the same ledger build and check in memory after its
existing refusals (`_refuse_claims`). It refuses with `claims`, naming the count and the
command to run, when there is any claim error. If the ledger cannot be built or checked, it
refuses with `the claim ledger could not be built`: the package fails closed. The refusal
writes nothing, and `package.json` is unchanged.

## RO-Crate

`scieflow manuscript crate <run>` writes `report/ro-crate/ro-crate-metadata.json`
(RO-Crate 1.1 metadata only). It lists the manuscript source files and every evidence
file as `File` entities with a `sha256:` identifier. Authors from `metadata.yml` are
`Person` entities with an ORCID only when it is valid, and no e-mail or affiliation
address. Claims are `CreativeWork` entities that link to their file, their DOIs and their
evidence. Runs are `CreateAction` entities with their stage scripts. Paths are relative to
the run folder. The file is byte-identical on re-run. `--json` prints it, and `--no-write`
writes nothing.

## L2 runs

A run declared L2 (see [Data protection](../data-protection.md)) keeps its record-level
data inside the run. The check never lets data leave it:

- A `data:` reference resolved through the manifest rather than a release is `data-unreleased`
  (error, not waivable). Release the artifact first.
- The crate lists unreleased L2 data without a path or hash, as a `CreativeWork` with the
  condition `L2 data, not released`.
- Nothing is sent anywhere. The check and the crate make no network call.

## Limits and residuals

- Numbers written as words ("forty-three") are not recognised.
- A number that also appears somewhere else in the evidence is accepted: "appears in" is
  weaker than "is the value of row X".
- Scientific notation written in the manuscript (`\times 10^{…}`) is not parsed.
- A cross-run `run:` reference is not supported.
- Retraction status is only as fresh as the last evidence round or identity check.
- C004 is reserved and never emitted.
- The verify-phase gate and the `paper-revision` workflow are not wired yet (M-G2). Until then
  the check runs only when you run it, and `package` is the only place that refuses on it.
- No PROV-JSON-LD, no PDF of a diff, no web page or menu item: the CLI is complete.
- File-based integrity (R2). The ledger, `data.yml`, the manifests and the events file are plain
  files that a run's own agents can write. A coherent forgery (a rewritten ledger with matching
  hashes, or a forged human-actor event) is not caught. Waivers count only from human-actor
  events, and `waive`, `ingest` and `adopt` refuse inside a dispatch, but an events line can be
  forged. Host-side signing is a follow-up.
- The RO-Crate is checked structurally and offline against the RO-Crate 1.1 required properties.
  No external validator runs (R5).
- A shallow clone can make `code-not-ancestor` (C006) fire for a run made from a commit the clone
  lacks. The user may waive it with a reason (R9).
